"""Optional local result cache for repeated jury runs (issue #33).

Re-running the jury against an unchanged diff with an unchanged config
re-spends time and tokens for an identical result. This module adds an opt-in,
on-disk cache keyed by everything that can change the outcome: the diff, the
effective config hash, the prompt-template version, the package version, the
context policy *and the context text it admits*, and the run seed.

Privacy note: a cache entry stores the full structured outcome — including agent
review/debate/synthesis text, which is derived from the diff. Treat the cache
directory as sensitive (same trust level as the diff itself). The cache is OFF
by default and only writes when explicitly enabled with ``--cache``; clear it
with ``--clear-cache`` (or ``jury cache clear``).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import tempfile
from dataclasses import asdict
from pathlib import Path

from . import __version__, prompts
from .adapters import AgentResult
from .config import JuryConfig, config_hash
from .consensus import FindingGroup
from .findings import Finding, Verdict
from .injection import InjectionHit
from .largediff import ChangeIndex
from .orchestrator import JuryOutcome

#: The record *format* version, bumped when a stored outcome gains a field a
#: renderer reads back. 2 (issue #709, round 2): ``AgentResult`` gained
#: ``model``, the id the invocation sent, and the ballot reads it to justify
#: ``model_source: requested``. An entry written without the field cannot
#: support that label — the run that wrote it did not record what it sent — and
#: recomputing an id to fill the gap puts a derived value under a token whose
#: whole claim is that it came off the wire, which is #709 itself. So such an
#: entry is **not read**: a cache exists to be an exact stand-in for a fresh
#: run, and where it cannot be, one re-run is the honest price.
#:
#: The cache *key* does not settle this on its own. It happens to invalidate
#: every pre-#709 entry in this release, because ``prompts.PROMPT_VERSION`` went
#: 7 → 8 for #710 in the same change — but that is a coincidence of two fixes
#: shipping together. Had #709 landed alone the key would have been unchanged
#: and every existing entry would have come back a field short. A change to the
#: record's format belongs in the field that versions the format.
CACHE_SCHEMA = 2
_ENV_DIR = "JURY_CACHE_DIR"

# Cache files are named `<64-hex sha256>.json` (entries) or
# `<64-hex>.json.<rand>.tmp` (in-flight atomic writes). `clear()` only touches
# files matching this shape (issue #316/L-3) so it never deletes unrelated files
# when JURY_CACHE_DIR points at a shared/populated directory.
_CACHE_NAME_RE = re.compile(r"^[0-9a-f]{64}\.json")


def default_cache_dir() -> Path:
    """Cache directory: ``$JURY_CACHE_DIR`` or ``~/.cache/ai-jury``."""
    override = os.environ.get(_ENV_DIR)
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "ai-jury"


def _policy_fingerprint(policy) -> str:
    """Stable fingerprint of a review policy for the cache key (issue #122).

    Returns "none" for no/empty policy. The policy is maintainer-authored review
    guidance injected into the prompts, so it changes the outcome and must be
    part of the key.
    """
    if policy is None or (hasattr(policy, "is_empty") and policy.is_empty()):
        return "none"
    from dataclasses import asdict, is_dataclass

    data = asdict(policy) if is_dataclass(policy) else policy
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def cache_key(
    config: JuryConfig,
    diff: str,
    *,
    context: str = "",
    hints: str = "",
    seed: int | None = None,
    mock: bool = False,
    policy=None,
    mode: str = "code",
) -> str:
    """Stable cache key for a run.

    A pure function of the inputs that determine the outcome. The seed is part of
    the key (it changes randomized orchestration), unlike in ``config_hash``
    which describes configuration independent of seed. ``mock`` is included so a
    ``--mock`` run (deterministic canned findings) can NEVER be served as a real
    review for the same diff+config, and vice versa. ``policy`` (the repository
    review policy) is fingerprinted in too, since it is injected into the prompts
    and changes the result (issue #122).

    ``context`` is the PR context block (issue #738). The *policy* — the mode and
    ``redact_secrets`` — was already in the payload, but the text it admits was
    not, so under ``--context-mode expanded`` the PR title and body were rendered
    into every Round 1 prompt (and read by the injection scanner) while the key
    stayed put: editing a PR description, or a third party editing it, changed
    what the panel was shown and ``--cache`` replayed the previous outcome.

    It is hashed **pre-redaction**, exactly as ``diff`` is: ``run_jury`` redacts
    both itself, after this key is computed, so the pre-redaction string is what
    every caller has. Two contexts differing only in a secret therefore key
    differently even though the panel would see the same redacted text — a
    conservative split (one extra run, never a stale hit) and not a leak: what is
    stored is a digest, not the text, it never leaves the machine, and a secret
    appearing in a PR body is a change to the input this key exists to notice.
    ``redact_secrets`` is separately in the payload, so the redaction *policy*
    cannot change under a fixed key either.

    The digest is present only when the panel will actually be shown context —
    ``run_jury`` clears ``context`` under ``diff-only``, the default mode, before
    anything reads it. Omitting the field there (rather than hashing ``""``)
    keeps the payload byte-identical for every run that sends no context, so no
    existing entry is invalidated for a string the panel never saw. Same
    precedent as ``[[agent]] adapter`` in ``config_hash`` (#705).

    ``hints`` is the static-analysis pre-pass block (issue #745), and it is here
    for the reason ``context`` is: it is joined into every Round 1 prompt. The
    ``hints`` *flag* was already in ``config_hash`` (#715), but the flag says
    only that the linters ran — the block itself is a function of the **working
    tree**, not of the diff or the config, so fixing a lint error anywhere in the
    tree changed what the panel was shown while every other input to this key
    stood still.

    Unlike ``context`` it takes no mode filter: ``run_jury`` joins the block into
    the Round 1 prompt *after* the ``diff-only`` filter, precisely so the default
    context mode cannot discard it (#715), and it is not redacted either. So the
    string the caller passes is the string the panel is shown, and hashing it
    when it is non-empty — absent otherwise, by the rule above — leaves a run
    with ``hints = false``, the default, keyed exactly as it was.
    """
    # What ``run_jury`` will hand the panel: nothing under "diff-only".
    panel_context = "" if config.context.mode == "diff-only" else context
    payload = {
        "cache_schema": CACHE_SCHEMA,
        "package_version": __version__,
        "prompt_version": prompts.PROMPT_VERSION,
        "config_hash": config_hash(config),
        "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        "context_mode": config.context.mode,
        "redact_secrets": config.context.redact_secrets,
        "verify": config.verify,
        "seed": seed if seed is not None else config.seed,
        "mock": bool(mock),
        "policy": _policy_fingerprint(policy),
        # Review mode (issue #221): "code" vs "issue" select different prompt
        # rubrics, so the same text must never be served across modes.
        "mode": mode,
    }
    if panel_context:
        payload["context_sha256"] = hashlib.sha256(panel_context.encode("utf-8")).hexdigest()
    if hints:
        payload["hints_sha256"] = hashlib.sha256(hints.encode("utf-8")).hexdigest()
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _finding(d: dict) -> Finding:
    return Finding(
        severity=d.get("severity", "info"),
        file=d.get("file", ""),
        claim=d.get("claim", ""),
        line=d.get("line"),
        evidence=d.get("evidence", ""),
        suggested_fix=d.get("suggested_fix", ""),
        confidence=d.get("confidence", "medium"),
        reviewer=d.get("reviewer", ""),
    )


def _verdict(d: dict) -> Verdict:
    return Verdict(
        file=d.get("file"),
        line=d.get("line"),
        claim=d.get("claim", ""),
        status=d.get("status", "needs_human_decision"),
        reasoning=d.get("reasoning", ""),
    )


def _agent_result(d: dict | None) -> AgentResult | None:
    if d is None:
        return None
    return AgentResult(
        agent=d["agent"],
        vendor=d["vendor"],
        ok=d["ok"],
        output=d["output"],
        duration_s=d["duration_s"],
        error=d.get("error"),
        findings=[_finding(f) for f in d.get("findings", [])],
        warnings=list(d.get("warnings", [])),
        error_code=d.get("error_code"),
        attempts=d.get("attempts", 1),
        # The id the invocation sent (#709). Restored so a cached ballot quotes
        # the same string a fresh one does — the cache key already pins the
        # config, so the id cannot have been anything else. The default is for a
        # dict that never came from a cache entry (`jury replay` takes any
        # `outcome_to_dict` dump): a stored entry missing the field is refused
        # by `CACHE_SCHEMA` before it reaches here, rather than balloting a
        # recomputed id under a label that claims it was sent.
        model=d.get("model", ""),
    )


def _group(d: dict) -> FindingGroup:
    return FindingGroup(
        representative=_finding(d["representative"]),
        reviewers=list(d.get("reviewers", [])),
        severity=d.get("severity", "info"),
        members=[_finding(m) for m in d.get("members", [])],
        bucket=d.get("bucket", "single_reviewer"),
        status=d.get("status", ""),
        status_reasoning=d.get("status_reasoning", ""),
    )


def _hit(d: dict) -> InjectionHit:
    return InjectionHit(
        kind=d.get("kind", ""),
        source=d.get("source", ""),
        line=d.get("line"),
        snippet=d.get("snippet", ""),
    )


def _change_index(d: dict | None) -> ChangeIndex | None:
    """Rebuild a :class:`ai_jury.largediff.ChangeIndex`, or None for a legacy entry."""
    if not isinstance(d, dict):
        return None
    return ChangeIndex(
        paths=tuple(d.get("paths") or ()),
        symbols=tuple(d.get("symbols") or ()),
    )


def outcome_to_dict(outcome: JuryOutcome) -> dict:
    """Serialize a JuryOutcome to a JSON-safe dict (dataclasses all the way down)."""
    return asdict(outcome)


def outcome_from_dict(data: dict) -> JuryOutcome:
    """Rebuild a JuryOutcome from :func:`outcome_to_dict` output."""
    return JuryOutcome(
        reviews=[_agent_result(r) for r in data.get("reviews", [])],
        debate=[_agent_result(r) for r in data.get("debate", [])],
        synthesis=_agent_result(data.get("synthesis")),
        chair=data.get("chair", ""),
        findings=[_finding(f) for f in data.get("findings", [])],
        warnings=list(data.get("warnings", [])),
        groups=[_group(g) for g in data.get("groups", [])],
        verify=_agent_result(data.get("verify")),
        verdicts=[_verdict(v) for v in data.get("verdicts", [])],
        context_mode=data.get("context_mode", "diff-only"),
        redact_secrets=data.get("redact_secrets", True),
        redaction_count=data.get("redaction_count", 0),
        injection_hits=[_hit(h) for h in data.get("injection_hits", [])],
        skipped=[tuple(s) for s in data.get("skipped", [])],
        budget_exhausted=data.get("budget_exhausted", False),
        rounds_executed=data.get("rounds_executed", 1),
        stop_reason=data.get("stop_reason", ""),
        from_cache=data.get("from_cache", False),
        # What the change contained (#710). The cache key already pins the diff
        # by digest, so a restored index describes the same bytes as the run
        # that wrote it; a legacy entry has none and the scope rule falls back.
        changed=_change_index(data.get("changed")),
        # The routing record rides along like every other field (#714). An
        # entry written before this key existed is restored as the standard
        # record naming the seats it holds, which is what those runs did: the
        # panel was never trimmed. That is truthful without invalidating a
        # single cache entry — `routing` and every seat's `tier` are already in
        # `config_hash`, so a plan that would route differently has a different
        # key and cannot hit an old entry at all.
        routing=dict(data["routing"])
        if data.get("routing")
        else {
            "mode": "standard",
            "risk": "",
            "panel": [r.get("agent", "") for r in data.get("reviews", [])],
            "benched": [],
            "anchor": None,
            "reason": "standard routing",
            "escalated": False,
            "escalation_reason": "",
        },
    )


_HMAC_KEY_FILE = ".hmac_key"

# Upper bound on a cache entry read before MAC verification (issue #303/L-5). A
# jury outcome is small (a few KB); reject a multi-MB file so an attacker-planted
# giant entry can't be fully parsed into memory before the MAC rejects it.
_MAX_CACHE_BYTES = 8 * 1024 * 1024


def _dir_is_untrusted(directory: Path) -> bool:
    """True when ``directory`` is group/other-writable (issue #295).

    A world-/group-writable cache dir lets another local user plant entries (and
    even swap the HMAC key file), so we fail closed rather than trust it. POSIX
    only — Windows ACLs are not represented in ``st_mode``, so the check is
    skipped there.
    """
    if os.name == "nt":
        return False
    try:
        mode = directory.stat().st_mode
    except OSError:
        return False
    return bool(mode & (stat.S_IWGRP | stat.S_IWOTH))


def _hmac_key(directory: Path) -> bytes | None:
    """Return the per-user cache MAC secret, creating it 0o600 on first use.

    The key lives in ``<cache_dir>/.hmac_key`` readable only by the owner, so a
    forged entry can't carry a valid MAC unless the attacker can read the secret
    (blocked by 0o600) or replace it (blocked by ``_dir_is_untrusted``). Returns
    None if the secret can't be read/created, in which case callers skip MACing.
    """
    key_path = directory / _HMAC_KEY_FILE
    with contextlib.suppress(OSError):
        return key_path.read_bytes()
    # Not present (or unreadable) — try to create it atomically, owner-only.
    try:
        fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Lost a race with a concurrent writer; read what they wrote.
        try:
            return key_path.read_bytes()
        except OSError:
            return None
    except OSError:
        return None
    try:
        key = secrets.token_bytes(32)
        with os.fdopen(fd, "wb") as handle:
            handle.write(key)
        return key
    except OSError:
        return None


def _canonical(entry: dict) -> str:
    """Deterministic serialization of an entry (minus its ``mac``) for MACing."""
    return json.dumps(
        {k: v for k, v in entry.items() if k != "mac"},
        separators=(",", ":"),
        sort_keys=True,
    )


def _compute_mac(key: bytes, entry: dict) -> str:
    return hmac.new(key, _canonical(entry).encode("utf-8"), hashlib.sha256).hexdigest()


class Cache:
    """A simple on-disk JSON cache of jury outcomes."""

    def __init__(self, directory: Path | str | None = None):
        self.dir = Path(directory) if directory else default_cache_dir()

    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def load(self, key: str) -> JuryOutcome | None:
        """Return the cached outcome for ``key`` (marked ``from_cache``), or None.

        A corrupt or unreadable entry is treated as a miss rather than an error,
        so a bad cache file never breaks a run.
        """
        path = self._path(key)
        if not path.exists():
            return None
        # Fail closed: never trust an entry read from a world/group-writable dir
        # (issue #295) — an attacker who can write the dir can forge entries.
        if _dir_is_untrusted(self.dir):
            return None
        try:
            # Size-cap the READ itself (issue #303/L-5, review): read at most
            # _MAX_CACHE_BYTES+1 chars rather than stat-then-read (which is a
            # TOCTOU and still reads the whole file). A giant attacker-planted
            # entry is rejected without being pulled into memory.
            with path.open("r", encoding="utf-8") as fh:
                raw = fh.read(_MAX_CACHE_BYTES + 1)
            if len(raw) > _MAX_CACHE_BYTES:
                return None
            data = json.loads(raw)
        except (OSError, ValueError, RecursionError):
            # RecursionError on deeply nested JSON is not a ValueError; catch it
            # so a planted entry can't crash the fail-closed read (audit
            # 2026-06-13 r3, mirrors findings.py).
            return None
        if data.get("cache_schema") != CACHE_SCHEMA:
            return None
        # Integrity: the entry must name the key it was written for (issue
        # #293/F-10). A file dropped at <digest>.json with mismatched content
        # (e.g. a forged verdict copied from another key) is treated as a miss.
        if data.get("cache_key") != key:
            return None
        # Integrity: verify the per-user HMAC (issue #295). Fail closed — if the
        # key can't be read/created we cannot authenticate the entry, so treat it
        # as a miss rather than trusting an unsigned blob. A missing or wrong MAC
        # (a forgery, or a legacy pre-MAC entry) is likewise a miss.
        mac_key = _hmac_key(self.dir)
        if mac_key is None:
            return None
        stored_mac = data.get("mac")
        if not isinstance(stored_mac, str) or not hmac.compare_digest(
            stored_mac, _compute_mac(mac_key, data)
        ):
            return None
        outcome = outcome_from_dict(data.get("outcome", {}))
        outcome.from_cache = True
        return outcome

    def store(self, key: str, outcome: JuryOutcome) -> None:
        """Persist ``outcome`` under ``key`` (best-effort; ignores write errors)."""
        with contextlib.suppress(OSError):
            # Owner-only cache dir so another local user cannot plant entries
            # (issue #293/F-10); best-effort tighten if it already exists.
            self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            with contextlib.suppress(OSError):
                self.dir.chmod(0o700)
            # Fail closed (#295): we just tried to tighten the dir to 0700. If it
            # is STILL group/other-writable, the chmod failed (we don't own it) —
            # an attacker could swap entries or the MAC key, so refuse to write
            # rather than trust it (the audit's "don't suppress and continue").
            if _dir_is_untrusted(self.dir):
                return
            payload = {
                "cache_schema": CACHE_SCHEMA,
                "cache_key": key,
                "outcome": outcome_to_dict(outcome),
            }
            # Fail closed (#295): if we can't obtain the MAC key, do NOT write an
            # unsigned entry — an unsigned blob would be accepted as trusted only
            # if MACing were optional, which it is not. Skip caching instead.
            mac_key = _hmac_key(self.dir)
            if mac_key is None:
                return
            payload["mac"] = _compute_mac(mac_key, payload)
            # Atomic write (issue #303/L-4, hardened in #316/L-4): mkstemp gives a
            # UNIQUE name created with O_EXCL and no symlink-follow — safe against
            # same-PID/thread concurrency and a pre-planted temp symlink — then an
            # atomic replace. A crash mid-write can't leave a truncated entry and
            # a reader never sees a partial file.
            path = self._path(key)
            blob = json.dumps(payload, separators=(",", ":")) + "\n"
            fd, tmp_name = tempfile.mkstemp(dir=self.dir, prefix=f"{path.name}.", suffix=".tmp")
            tmp = Path(tmp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(blob)
                tmp.replace(path)
            except OSError:
                with contextlib.suppress(OSError):
                    tmp.unlink()
                raise  # caught by the outer contextlib.suppress(OSError)

    def clear(self) -> int:
        """Remove all cache entries; return the number deleted.

        Also rotates (deletes) the per-user MAC key (issue #303/L-3) so a clear
        after a suspected compromise starts fresh; the count covers only the
        ``*.json`` entries.
        """
        if not self.dir.exists():
            return 0
        removed = 0
        # Only touch files matching the cache-name shape (#316/L-3) so a clear on
        # a shared JURY_CACHE_DIR never deletes unrelated files. Entries
        # (`<hex>.json`) count; leftover atomic-write temps (`<hex>.json.*.tmp`)
        # are reaped but not counted.
        for path in self.dir.glob("*.json"):
            if _CACHE_NAME_RE.match(path.name):
                with contextlib.suppress(OSError):
                    path.unlink()
                    removed += 1
        for tmp in self.dir.glob("*.tmp"):
            if _CACHE_NAME_RE.match(tmp.name):
                with contextlib.suppress(OSError):
                    tmp.unlink()
        with contextlib.suppress(OSError):
            (self.dir / _HMAC_KEY_FILE).unlink()
        return removed
