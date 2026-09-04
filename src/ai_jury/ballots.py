"""Per-reviewer ballots: each panelist's own stance, with vendor provenance (issue #663).

The consolidated ``findings``/``consensus`` views answer *what the panel found*.
They do not answer *who said what*, which is the question a downstream gate asks
when it wants the panel to **be** its review: one head-pinned verdict per
panelist, attributed to the vendor and model that produced it. Until now that
existed only as prose in the markdown report's vote block.

Everything here is pure and deterministic — a function of the outcome, the config
and the (optional) vote. No I/O, no wall-clock, no randomness. Two renderings are
built on it:

* :func:`reviewer_ballots` — the ``reviewers`` array of the JSON report.
* :func:`keel_reviews` — the ``--format keel-reviews`` bundle, shaped for a
  consumer that accepts a JSON array of ``{reviewer, verdict, scope, findings,
  testing, vendor, model}`` records (keel's ``keel review --reviews``).

Only legitimate, already-reported fields are emitted: severities, locations,
claims, vendor/model provenance and durations. Never raw diff text, prompt text
or secrets. Free-text lifted out of an agent's reply (``scope``, ``testing``) is
attacker-influenced, so it is flattened to a single line and length-capped before
it leaves this module.
"""

from __future__ import annotations

import re
from typing import Any

from .findings import flatten_inline
from .panel import ballot_slots, bundle_size
from .voting import is_abstention, tally_votes

#: Stance recorded for a panelist that did not actually review — an empty reply,
#: a refusal, or an adapter that failed. Counting such a slot as the "clear"
#: stance (APPROVE/READY) is precisely the bug :mod:`ai_jury.voting` refuses to
#: have (issue #251): a non-answer is not an approval. The vote tally drops those
#: reviewers from the tally entirely; a ballot list has to name every panelist
#: that returned something, so it names the abstention instead of inventing a
#: stance for it.
ABSTAIN = "ABSTAIN"

#: ``reviewer`` / ``name`` used for the chair's entry.
CHAIR_NAME = "chair"

#: Placeholder for a panelist that stated no verification.
NOT_STATED = "not stated"

#: Caps on free text lifted out of agent output. A reviewer's reply is
#: attacker-influenced, so a forged 100 kB "scope" must not become the bulk of a
#: bundle posted downstream.
_CLAUSE_MAX = 240
_SCOPE_CLAUSES = 3
_FILES_LISTED = 8

# Sentence boundary: end punctuation followed by whitespace. Lines are split
# first, so a bullet list yields one clause per bullet.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

#: Clauses that describe what a reviewer looked at (folded into ``scope``).
#: Anchored on word boundaries, which is load-bearing rather than tidy: the most
#: common finding wording in this tool's own fixtures is "**un**checked return
#: value", and a bare substring test folds that claim into the coverage summary
#: as though the reviewer had said it checked something.
_COVERAGE_RE = re.compile(r"\b(checked|examined|inspected|reviewed|covered)\b", re.IGNORECASE)

#: Clauses that describe what a reviewer did to verify a claim (``testing``).
_TESTING_RE = re.compile(
    r"\b(ran\s+(?:the\s+)?tests?|test\s+suite|unit\s+tests?|i\s+ran|reproduced|"
    r"verified|verification|tested)\b",
    re.IGNORECASE,
)


def normalize_verdict(verdict: str) -> str:
    """Fold a display verdict into a single machine token.

    ``REQUEST CHANGES`` → ``REQUEST_CHANGES``, ``NEEDS-INFO`` → ``NEEDS_INFO``,
    ``NO QUORUM`` → ``NO_QUORUM``. The markdown report keeps the spaced form for
    humans; a machine consumer keys on one word, and a verdict that changes shape
    between the two renderings is a verdict that gets matched wrong.
    """
    token = flatten_inline(verdict or "").strip().upper()
    return re.sub(r"[\s\-]+", "_", token)


def _prose_lines(text: str) -> list[str]:
    """The agent's prose with fenced blocks removed.

    A review's fenced ``json`` block is the *structured* findings — it is already
    parsed into :class:`~ai_jury.findings.Finding` objects and rendered as the
    ballot's ``findings``. Left in, its serialized claim/evidence text is by far
    the longest thing in the reply and swamps every prose clause after it.
    Unterminated fences swallow the remainder, which is the fail-safe direction:
    less lifted text, never more.
    """
    lines: list[str] = []
    in_fence = False
    for raw in (text or "").splitlines():
        if raw.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(raw)
    return lines


def _clauses(text: str) -> list[str]:
    """Split agent output into flattened, capped candidate clauses (pure)."""
    out: list[str] = []
    for raw in _prose_lines(text):
        line = flatten_inline(raw).strip().lstrip("-*#> ").strip()
        if not line:
            continue
        # The split pattern consumes the whitespace after the end punctuation and
        # ``line`` is already stripped, so no part can be blank — hence no guard.
        for part in _SENTENCE_SPLIT.split(line):
            out.append(part[:_CLAUSE_MAX])
    return out


def _first_matching(clauses: list[str], pattern: re.Pattern[str]) -> str:
    for clause in clauses:
        if pattern.search(clause):
            return clause
    return ""


def _matching(clauses: list[str], pattern: re.Pattern[str], limit: int) -> list[str]:
    hits: list[str] = []
    for clause in clauses:
        if pattern.search(clause) and clause not in hits:
            hits.append(clause)
            if len(hits) >= limit:
                break
    return hits


def _files_named(findings: list) -> list[str]:
    """Distinct file paths a reviewer named, in first-reported order."""
    seen: list[str] = []
    for f in findings:
        path = flatten_inline(getattr(f, "file", "") or "").strip()
        if path and path not in seen:
            seen.append(path)
    return seen


def describe_scope(result: Any, findings: list) -> str:
    """One-paragraph summary of what a panelist covered (pure, deterministic).

    Built from the files the panelist actually named in its structured findings
    plus any "checked / examined / reviewed" clauses in its prose. Both halves
    are optional: a clean review names no file and may still say what it looked
    at, and a terse reviewer may name files and say nothing. With neither, the
    scope states the reviewer's actual position (the whole diff, nothing named)
    rather than fabricating coverage it never claimed.
    """
    files = _files_named(findings)
    parts: list[str] = []
    if files:
        listed = ", ".join(files[:_FILES_LISTED])
        more = len(files) - _FILES_LISTED
        suffix = f" (+{more} more)" if more > 0 else ""
        parts.append(f"Named {len(files)} file(s): {listed}{suffix}.")
    else:
        parts.append("Reviewed the supplied diff; named no specific file.")
    parts.extend(_matching(_clauses(getattr(result, "output", "")), _COVERAGE_RE, _SCOPE_CLAUSES))
    return " ".join(parts)


def describe_testing(result: Any) -> str:
    """The panelist's stated verification, or :data:`NOT_STATED`.

    Lifted verbatim (flattened and capped) from the reviewer's own prose rather
    than summarized: a testing claim carried downstream as evidence must be the
    reviewer's words, not this module's paraphrase of them.
    """
    clause = _first_matching(_clauses(getattr(result, "output", "")), _TESTING_RE)
    return clause or NOT_STATED


def _model_for(config: Any, agent_name: str) -> str:
    """Effective model for an agent slot ("" when the CLI's default is used)."""
    for spec in getattr(config, "agents", []) or []:
        if spec.name == agent_name:
            return spec.model or ""
    return ""


def _vendor_for(config: Any, agent_name: str) -> str:
    for spec in getattr(config, "agents", []) or []:
        if spec.name == agent_name:
            return spec.vendor or ""
    return ""


def participating(outcome: Any) -> list:
    """Round-1 slots that returned output at all, in the stable panel order.

    "Returned output", not "exited 0": adapters fail soft, so a nonzero exit can
    still carry a complete review on stdout (see :class:`ai_jury.adapters.Adapter`).
    Such a slot has a stance worth recording; ``round1_ok`` says the adapter
    reported failure. A slot with nothing at all is not a ballot.

    The predicate itself lives in :mod:`ai_jury.panel` because the number this
    returns *is* the number a consumer receives, minus the chair record — and
    the report, ``--doctor`` and the pre-run gate all have to quote it (#699).
    """
    return ballot_slots(getattr(outcome, "reviews", []) or [])


def _stance_by_reviewer(outcome: Any, names: list[str], mode: str) -> dict[str, str]:
    """Per-panelist stance, derived exactly as the vote tally derives ballots.

    :func:`ai_jury.voting.tally_votes` is the single source of truth for turning
    "the worst supported finding this reviewer raised" into a stance, so it is
    called rather than reimplemented — a second copy of that mapping is a second
    place for the severity thresholds to drift.
    """
    result = tally_votes(getattr(outcome, "groups", []) or [], names, mode=mode)
    return {b.reviewer: normalize_verdict(b.vote) for b in result.ballots}


def _verdict_for(result: Any, stances: dict[str, str]) -> str:
    if not getattr(result, "ok", False):
        return ABSTAIN
    if is_abstention(getattr(result, "output", "")):
        return ABSTAIN
    return stances.get(getattr(result, "agent", ""), ABSTAIN)


def chair_verdict(outcome: Any, vote: Any = None) -> str:
    """The run's final verdict as one machine token.

    The panel vote when voting; otherwise the label the chair opened its
    synthesis with. The headline lift is
    :func:`ai_jury.report._verdict_headline` — reused rather than duplicated, so
    the JSON verdict and the markdown TL;DR can never disagree. The headline is a
    label plus a sentence (``REQUEST CHANGES — one confirmed major issue.``); only
    the label is a verdict, so the sentence is dropped.
    """
    from .report import _verdict_headline

    headline = _verdict_headline(getattr(outcome, "synthesis", None), vote)
    if not headline:
        return ABSTAIN
    label = re.split(r"[—–:.]| - ", headline, maxsplit=1)[0]
    return normalize_verdict(label) or ABSTAIN


def _verified_count(outcome: Any, name: str) -> int:
    """Consensus groups this reviewer contributed to that the verifier upheld."""
    return sum(
        1
        for g in getattr(outcome, "groups", []) or []
        if name in (getattr(g, "reviewers", []) or [])
        and (getattr(g, "status", "") or "") == "verified"
    )


def reviewer_ballots(
    outcome: Any, config: Any, *, vote: Any = None, mode: str = "code"
) -> list[dict]:
    """The JSON report's ``reviewers`` array: one ballot per panelist, then the chair.

    Panelist entries carry ``name``, ``role: "panelist"``, ``chaired``,
    ``vendor``, ``model``, ``verdict``, ``findings`` (indexes into the report's
    top-level ``findings`` array), ``round1_ok``, ``verified_count`` and
    ``duration_s``. The chair's entry is the one carrying ``role: "chair"`` and is
    always last.

    ``chaired`` and the chair entry's ``agent``/``ballot_counted`` exist because
    the chair reviews too (#699): without them a reader cannot tell that the
    ``claude`` ballot and the ``chair`` record are the same agent, nor whether the
    chair contributed a ballot at all — and a reader who guesses drops a review
    the panel actually cast.
    """
    slots = participating(outcome)
    names = [getattr(r, "agent", "") for r in slots]
    stances = _stance_by_reviewer(outcome, names, mode)
    all_findings = list(getattr(outcome, "findings", []) or [])
    chair_name = getattr(outcome, "chair", "") or ""

    entries: list[dict] = []
    for r in slots:
        name = getattr(r, "agent", "")
        entries.append(
            {
                "name": name,
                "role": "panelist",
                "chaired": bool(chair_name) and name == chair_name,
                "vendor": getattr(r, "vendor", "") or "",
                "model": _model_for(config, name),
                "verdict": _verdict_for(r, stances),
                "findings": [i for i, f in enumerate(all_findings) if f.reviewer == name],
                "round1_ok": bool(getattr(r, "ok", False)),
                "verified_count": _verified_count(outcome, name),
                "duration_s": round(float(getattr(r, "duration_s", 0.0) or 0.0), 3),
            }
        )

    entries.append(
        {
            "name": CHAIR_NAME,
            "role": "chair",
            "agent": chair_name,
            # The chair record is itself one of the reviews a consumer counts,
            # which is the fact the bundle never stated (#699).
            "ballot_counted": any(e["chaired"] for e in entries),
            "reviews_supplied": bundle_size(len(entries)),
            "vendor": _vendor_for(config, chair_name),
            "model": _model_for(config, chair_name),
            "verdict": chair_verdict(outcome, vote),
        }
    )
    return entries


def _keel_finding(f: Any) -> dict:
    """One finding in the consumer's shape: ``file`` → ``path``, ``claim`` → ``message``."""
    line = getattr(f, "line", None)
    return {
        "severity": getattr(f, "severity", "") or "",
        "path": getattr(f, "file", "") or "",
        "line": line if isinstance(line, int) else None,
        "message": flatten_inline(getattr(f, "claim", "") or ""),
    }


def _chair_findings(outcome: Any) -> list:
    """The chair's surviving evidence: group representatives the verifier did not reject."""
    return [
        g.representative
        for g in getattr(outcome, "groups", []) or []
        if (getattr(g, "status", "") or "") != "unsupported"
    ]


def _chair_role_sentence(chair_agent: str, chaired_ballot: dict | None) -> str:
    """State, in the record itself, whether this chair's ballot is counted (#699).

    A chair that reviewed and a chair that only synthesised produce records that
    are otherwise identical, and a reader who cannot tell them apart guesses —
    which is how a bundle of four reviews is handed on as two. So the record says
    which agent chaired and where (or whether) its own ballot is in the bundle.
    """
    who = f"`{chair_agent}`" if chair_agent else "This run's chair"
    if chaired_ballot is not None:
        return (
            f"The chair is {who}, which also sat on the panel: its ballot is the "
            f"'{chaired_ballot['name']}' review in this bundle and is counted "
            f"alongside this synthesis."
        )
    return (
        f"The chair is {who}, which returned no panel review of its own, so this "
        f"bundle carries no ballot from it — only this synthesis."
    )


def _chair_scope(outcome: Any, panelists: list[dict]) -> str:
    groups = getattr(outcome, "groups", []) or []
    files = _files_named([g.representative for g in groups])
    listed = ", ".join(files[:_FILES_LISTED]) if files else "no specific file"
    more = len(files) - _FILES_LISTED
    suffix = f" (+{more} more)" if more > 0 else ""
    chair_agent = getattr(outcome, "chair", "") or ""
    chaired = next((p for p in panelists if p.get("chaired")), None)
    return (
        f"Chair synthesis over {len(panelists)} panel review(s) and "
        f"{len(groups)} consensus group(s), across {listed}{suffix}. "
        f"{_chair_role_sentence(chair_agent, chaired)} This bundle carries "
        f"{bundle_size(len(panelists))} review(s) in total."
    )


def keel_reviews(outcome: Any, config: Any, *, vote: Any = None, mode: str = "code") -> list[dict]:
    """The ``--format keel-reviews`` bundle: one review record per panelist, plus the chair.

    Each record is ``{reviewer, verdict, scope, findings, testing, vendor, model}``
    where ``findings`` are ``{severity, path, line, message}`` objects — the shape
    a consumer of head-pinned per-reviewer verdicts accepts. Pure: the caller
    serializes it.
    """
    ballots = reviewer_ballots(outcome, config, vote=vote, mode=mode)
    by_name = {getattr(r, "agent", ""): r for r in participating(outcome)}
    all_findings = list(getattr(outcome, "findings", []) or [])

    records: list[dict] = []
    panelists = [b for b in ballots if b.get("role") != "chair"]
    for b in panelists:
        result = by_name.get(b["name"])
        own = [all_findings[i] for i in b["findings"]]
        scope = describe_scope(result, own)
        if b.get("chaired"):
            # Said on the ballot as well as on the chair record, because the two
            # are read in different places: a consumer that posts one verdict per
            # review shows this text on its own, with no chair record beside it.
            scope += (
                " This reviewer also chaired the run (verification and synthesis);"
                " this ballot is counted alongside the chair record below."
            )
        records.append(
            {
                "reviewer": b["name"],
                "verdict": b["verdict"],
                "scope": scope,
                "findings": [_keel_finding(f) for f in own],
                "testing": describe_testing(result),
                "vendor": b["vendor"],
                "model": b["model"],
            }
        )

    chair = ballots[-1]
    verify = getattr(outcome, "verify", None)
    chair_testing = describe_testing(verify) if verify is not None else NOT_STATED
    records.append(
        {
            "reviewer": chair["name"],
            "verdict": chair["verdict"],
            "scope": _chair_scope(outcome, panelists),
            "findings": [_keel_finding(f) for f in _chair_findings(outcome)],
            "testing": chair_testing,
            "vendor": chair["vendor"],
            "model": chair["model"],
        }
    )
    return records
