"""Replay a saved jury outcome in the deliberation theater (issue #449).

``jury replay <outcome.json>`` re-drives the presentation layer — the theater
scene or the plain ``--live`` transcript stream — from a serialized
:class:`~ai_jury.orchestrator.JuryOutcome`. No orchestration, no network, no
agents: this module only re-issues the same ``on_event`` sequence the
orchestrator emits during a live run (reviews in panel order → the recorded
debate round → verify → synthesis), plus the ``set_vote``/``close`` finale the
CLI performs on the theater.

Accepted input shapes (sniffed by top-level keys):

* a bare outcome dict — the exact shape :func:`ai_jury.cache.outcome_to_dict`
  produces (top-level ``reviews`` / ``debate`` / ``chair`` ...);
* a result-cache entry — the on-disk ``*.json`` the cache writes, which wraps
  that same dict under an ``"outcome"`` key.

A ``--format json`` report (``schema_version`` + ``metadata`` at top level) is
recognised and rejected with a clear message: it carries findings and the
verdict but NOT the per-agent deliberation stream, so there is nothing to
replay from it.

Security posture: the file is untrusted input. It is read with a hard byte cap
(mirroring ``config._read_toml_bounded`` / ``cache._MAX_CACHE_BYTES``), parsed
with plain :func:`json.loads` (never eval), and every parse failure surfaces as
a :class:`ReplayError` rather than a traceback. Output hardening (control-byte
scrubbing, bidi/zero-width stripping) is the theater's and report renderer's
existing responsibility — replay adds no new output channel.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from .adapters import AgentResult
from .cache import outcome_from_dict
from .orchestrator import JuryOutcome
from .redaction import redact

# Upper bound on a replay-file read (issue #449). Matches the result cache's
# ceiling (``cache._MAX_CACHE_BYTES``): a serialized outcome is a few KB, so a
# multi-MB file is either corrupt or hostile — reject it without pulling it
# fully into memory.
_MAX_REPLAY_BYTES = 8 * 1024 * 1024


class ReplayError(ValueError):
    """A replay input problem the user can act on (bad path/shape/JSON)."""


def load_outcome(path: Path | str) -> JuryOutcome:
    """Load and validate a serialized outcome from ``path``.

    Accepts a bare ``outcome_to_dict`` dict or a cache entry wrapping one under
    ``"outcome"``. Raises :class:`ReplayError` — never a raw traceback — for a
    missing/unreadable file, an oversized file, invalid JSON, an unrecognized
    shape, or a malformed outcome.
    """
    path = Path(path)
    try:
        # Cap the READ itself (not stat-then-read, which is a TOCTOU): read at
        # most the ceiling + 1 so an oversized file is detected without ever
        # being held fully in memory.
        with path.open("r", encoding="utf-8") as fh:
            raw = fh.read(_MAX_REPLAY_BYTES + 1)
    except OSError as exc:
        raise ReplayError(f"cannot read '{path}': {redact(str(exc))[0]}") from exc
    except UnicodeDecodeError as exc:
        raise ReplayError(f"'{path}' is not UTF-8 text: {redact(str(exc))[0]}") from exc
    if len(raw) > _MAX_REPLAY_BYTES:
        raise ReplayError(f"'{path}' exceeds the {_MAX_REPLAY_BYTES}-byte replay limit")
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        # RecursionError on deeply nested JSON is not a ValueError; catch it so
        # a hostile file cannot crash the loader (mirrors cache.py).
        raise ReplayError(f"'{path}' is not valid JSON: {redact(str(exc))[0]}") from exc

    if not isinstance(data, dict):
        raise ReplayError(f"'{path}' is not a JSON object")

    if isinstance(data.get("outcome"), dict):
        # Result-cache entry ({"cache_schema": ..., "outcome": {...}, "mac": ...}).
        # The MAC is deliberately NOT verified here: it authenticates entries
        # for the cache-hit fast path; replay is presentation-only and the user
        # chose this file explicitly.
        inner = data["outcome"]
    elif "reviews" in data:
        # Bare outcome_to_dict shape.
        inner = data
    elif "schema_version" in data and "metadata" in data:
        raise ReplayError(
            f"'{path}' looks like a `jury --format json` report, which does not "
            "contain the per-agent deliberation stream (reviews/debate), so it "
            "cannot be replayed. Pass a serialized outcome instead: a result-"
            "cache entry (see `jury --cache`) or an `outcome_to_dict` dump."
        )
    else:
        raise ReplayError(
            f"'{path}' is not a recognized outcome shape (expected a serialized "
            "outcome with a top-level 'reviews' key, or a cache entry with a "
            "top-level 'outcome' key)"
        )

    _coerce_agent_results(inner)
    try:
        outcome = outcome_from_dict(inner)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ReplayError(f"'{path}' holds a malformed outcome: {redact(str(exc))[0]}") from exc
    if not outcome.reviews:
        raise ReplayError(f"'{path}' contains no reviews — nothing to replay")
    return outcome


def _coerce_agent_results(inner: dict) -> None:
    """Coerce type-invalid AgentResult fields in place (untrusted file).

    ``outcome_from_dict``/``cache._agent_result`` copy values without type
    validation, so a hand-edited file with ``"output": null`` or a string
    ``duration_s`` would pass loading and crash far later inside the render
    loop with a raw traceback (review finding). Coerce the fields the render
    path consumes: ``output`` to str, ``duration_s`` to float, ``ok`` to bool,
    ``agent``/``vendor`` to str.
    """
    for key in ("reviews", "debate"):
        items = inner.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                _coerce_one(item)
    for key in ("synthesis", "verify"):
        item = inner.get(key)
        if isinstance(item, dict):
            _coerce_one(item)


def _coerce_one(item: dict) -> None:
    item["agent"] = str(item.get("agent") or "")
    item["vendor"] = str(item.get("vendor") or "")
    item["ok"] = bool(item.get("ok"))
    out = item.get("output")
    item["output"] = out if isinstance(out, str) else ("" if out is None else str(out))
    try:
        item["duration_s"] = float(item.get("duration_s") or 0.0)
    except (TypeError, ValueError):
        item["duration_s"] = 0.0


def replay_events(
    outcome: JuryOutcome,
) -> Iterator[tuple[str, AgentResult, int | None]]:
    """Yield the ``(kind, result, round_no)`` sequence a live run would emit.

    Mirrors the orchestrator's ``on_event`` stream: each review in panel order,
    then the recorded debate round, then verify, then synthesis — phases absent
    from the outcome are simply skipped, exactly as a live run that skipped
    them. The outcome stores only the FINAL debate round (earlier rounds are
    superseded, not serialized), numbered from ``rounds_executed`` — debate
    rounds start at 2, review being round 1.
    """
    for r in outcome.reviews:
        yield ("review", r, None)
    if outcome.debate:
        round_no = outcome.rounds_executed if outcome.rounds_executed >= 2 else 2
        for r in outcome.debate:
            yield ("debate", r, round_no)
    if outcome.verify is not None:
        yield ("verify", outcome.verify, None)
    if outcome.synthesis is not None:
        yield ("synthesis", outcome.synthesis, None)


def replay_into(court, outcome: JuryOutcome, vote=None) -> None:
    """Drive ``court`` (a theater ``Courtroom``-like object) from ``outcome``.

    Uses exactly the API the live CLI path uses: ``open()``, ``step(kind,
    result, round_no)`` per event, ``set_vote(vote)`` when a panel vote is
    supplied, and ``close()`` (always — the terminal must be restored even if a
    step raises).
    """
    court.open()
    try:
        for kind, result, round_no in replay_events(outcome):
            court.step(kind, result, round_no)
        if vote is not None:
            court.set_vote(vote)
    finally:
        court.close()
