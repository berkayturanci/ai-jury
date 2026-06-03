"""Deterministic PR-level classification derived from structured findings.

The jury report already lists individual findings and consensus groups, but
maintainers also want a compact, at-a-glance signal: how much review effort a PR
needs, how risky it is, whether it touches security-sensitive code, and whether
it warrants human attention. This module derives those four classifications as a
PURE, fully deterministic function of the structured findings, the consensus
groups, and (optionally) the unified diff.

Nothing here calls an LLM or the network: identical inputs always produce
identical output, which is what makes the classification safe to snapshot-test
and to render in the deterministic mock report.

Classifications
---------------
``review_effort`` : int, 1-5
``risk_level``    : str, one of ``low`` / ``medium`` / ``high``
``security_sensitive`` : bool
``needs_human_attention`` : bool

See :func:`classify` for the exact, documented formulas.
"""
from __future__ import annotations

import re
from typing import Any

from .findings import SEVERITY_ORDER

# Risk levels, ordered least to most severe.
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

# Consensus buckets that mean "a human still needs to look at this": the verifier
# could not confirm the finding, or flagged it as needing a human decision.
_UNRESOLVED_BUCKETS = {"disputed"}
_UNRESOLVED_STATUSES = {"needs_human_decision"}

# Security keyword set. A finding is treated as security-sensitive if any of
# these whole-word tokens (or multi-word phrases) appears in its claim, evidence,
# suggested fix, or file path. Kept deliberately small and high-signal so benign
# findings do not over-match. Matching is case-insensitive and word-boundary
# anchored for single tokens (so "auth" does not fire inside "author").
SECURITY_KEYWORDS: tuple[str, ...] = (
    "injection",
    "sql injection",
    "xss",
    "csrf",
    "ssrf",
    "rce",
    "remote code execution",
    "traversal",
    "path traversal",
    "directory traversal",
    "secret",
    "credential",
    "password",
    "token",
    "api key",
    "private key",
    "auth",
    "authentication",
    "authorization",
    "deserialization",
    "sanitize",
    "sanitization",
    "escape",
    "vulnerab",
    "exploit",
    "privilege",
    "sandbox escape",
)

# Pre-compiled, word-boundary anchored matchers for each keyword. Multi-word
# phrases match on a relaxed boundary (spaces inside the phrase are literal).
_KEYWORD_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE) for kw in SECURITY_KEYWORDS
)

# Hunk header of a unified diff, e.g. ``@@ -1,3 +1,6 @@``.
_HUNK_RE = re.compile(r"^@@ ")


def _severity_rank(severity: str) -> int:
    """Lower number = more severe (mirrors findings.SEVERITY_ORDER)."""
    return SEVERITY_ORDER.get(severity, len(SEVERITY_ORDER))


def _resolved_findings(outcome: Any, findings: Any) -> list:
    """Pick the finding list to classify on.

    Prefers an explicit ``findings`` argument, then ``outcome.findings``. The
    list is returned as-is (callers pass already-aggregated findings).
    """
    if findings is not None:
        return list(findings)
    if outcome is not None and getattr(outcome, "findings", None) is not None:
        return list(outcome.findings)
    return []


def _resolved_groups(outcome: Any, groups: Any) -> list:
    if groups is not None:
        return list(groups)
    if outcome is not None and getattr(outcome, "groups", None) is not None:
        return list(outcome.groups)
    return []


def diff_lines_changed(diff: str | None) -> int:
    """Count added/removed lines in a unified diff (deterministic).

    Counts lines beginning with a single ``+`` or ``-`` that are NOT part of the
    file header (``+++`` / ``---``). Returns 0 for an empty or missing diff.
    """
    if not diff:
        return 0
    changed = 0
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") or line.startswith("-"):
            changed += 1
    return changed


def _text_blob(finding: Any) -> str:
    """Concatenate the human-text fields of a finding for keyword scanning."""
    parts = [
        getattr(finding, "claim", "") or "",
        getattr(finding, "evidence", "") or "",
        getattr(finding, "suggested_fix", "") or "",
        getattr(finding, "file", "") or "",
        getattr(finding, "reviewer", "") or "",
    ]
    return " ".join(parts)


def is_security_finding(finding: Any) -> bool:
    """True if a single finding looks security-related.

    A finding is security-sensitive when EITHER its severity is ``critical`` OR
    any :data:`SECURITY_KEYWORDS` token appears in its text fields. The
    injection-scanner's synthetic finding (reviewer ``injection-scanner``,
    claim mentioning "injection") is therefore caught by the keyword path.
    """
    if getattr(finding, "severity", "") == "critical":
        return True
    blob = _text_blob(finding)
    return any(rx.search(blob) for rx in _KEYWORD_RES)


def _risk_level(findings: list, groups: list) -> str:
    """Derive the risk level from the most severe finding.

    Thresholds (deterministic):
      * ``high``   — any ``critical`` finding, OR any ``major`` finding that is
        part of a confirmed consensus group (consensus/majority bucket and not
        rejected/unsupported).
      * ``medium`` — any ``major`` finding (single-reviewer / unverified), OR any
        ``minor`` finding.
      * ``low``    — only ``nit`` / ``info`` findings, or no findings at all.
    """
    if not findings:
        return RISK_LOW

    has_critical = any(f.severity == "critical" for f in findings)
    if has_critical:
        return RISK_HIGH

    has_major = any(f.severity == "major" for f in findings)
    if has_major:
        # A confirmed (consensus/majority, not rejected) major finding is high
        # risk; an isolated or rejected one is medium.
        for g in groups:
            if g.severity == "major" and g.bucket in ("consensus", "majority"):
                return RISK_HIGH
        return RISK_MEDIUM

    has_minor = any(f.severity == "minor" for f in findings)
    if has_minor:
        return RISK_MEDIUM

    return RISK_LOW


def _review_effort(findings: list, lines_changed: int) -> int:
    """Map findings + diff size onto a 1-5 review-effort score (deterministic).

    Formula (clamped to 1..5):

        base = 1
        + finding-count contribution:
            >= 8 findings -> +2, >= 3 findings -> +1, >= 1 finding -> +0 extra
            (1 is added below via the severity/any-finding term)
        + severity-spread contribution:
            any critical/major -> +2, else any minor -> +1
        + diff-size contribution:
            > 400 changed lines -> +2, > 80 changed lines -> +1

    The score is monotonic-ish: more findings, more severe findings, and a
    larger diff each only ever raise (never lower) the effort, and the result is
    clamped to the documented 1..5 range.
    """
    score = 1

    n = len(findings)
    if n >= 8:
        score += 2
    elif n >= 3:
        score += 1
    elif n >= 1:
        score += 0  # presence is captured by the severity term below

    most_severe = min((_severity_rank(f.severity) for f in findings), default=99)
    if most_severe <= _severity_rank("major"):
        score += 2
    elif most_severe <= _severity_rank("minor"):
        score += 1

    if lines_changed > 400:
        score += 2
    elif lines_changed > 80:
        score += 1

    return max(1, min(5, score))


def _has_unresolved_groups(groups: list) -> bool:
    """True if any consensus group is disputed or needs a human decision."""
    for g in groups:
        if getattr(g, "bucket", "") in _UNRESOLVED_BUCKETS:
            return True
        if getattr(g, "status", "") in _UNRESOLVED_STATUSES:
            return True
    return False


def classify(
    outcome: Any = None,
    *,
    findings: Any = None,
    groups: Any = None,
    diff: str | None = None,
) -> dict:
    """Return the deterministic PR-level classification dict.

    Accepts either a ``JuryOutcome`` (positional) or explicit ``findings`` /
    ``groups`` keyword arguments (which take precedence). ``diff`` is optional;
    when given, its changed-line count feeds the review-effort score.

    Returns a dict with exactly these keys:

      ``review_effort``         int 1-5
      ``risk_level``            "low" | "medium" | "high"
      ``security_sensitive``    bool
      ``needs_human_attention`` bool

    Determinism: the result is a pure function of the inputs — same findings,
    groups, and diff always yield the same dict.
    """
    fs = _resolved_findings(outcome, findings)
    gs = _resolved_groups(outcome, groups)
    lines_changed = diff_lines_changed(diff)

    risk = _risk_level(fs, gs)
    security = any(is_security_finding(f) for f in fs)
    effort = _review_effort(fs, lines_changed)
    needs_human = (
        risk == RISK_HIGH or security or _has_unresolved_groups(gs)
    )

    return {
        "review_effort": effort,
        "risk_level": risk,
        "security_sensitive": bool(security),
        "needs_human_attention": bool(needs_human),
    }


def label_strings(classification: dict) -> list[str]:
    """Derive GitHub label strings from a classification dict (deterministic).

    Mirrors the labels suggested in issue #7, e.g.::

        ["review effort: 3/5", "risk: high", "possible security issue",
         "needs human attention"]

    The security and human-attention labels are only emitted when their flag is
    true. Order is stable.
    """
    labels = [
        f"review effort: {classification['review_effort']}/5",
        f"risk: {classification['risk_level']}",
    ]
    if classification.get("security_sensitive"):
        labels.append("possible security issue")
    if classification.get("needs_human_attention"):
        labels.append("needs human attention")
    return labels


def summary_line(classification: dict) -> str:
    """Render a compact one-line human summary of the classification."""
    return (
        f"review effort: {classification['review_effort']}/5"
        f" · risk: {classification['risk_level']}"
        f" · security-sensitive: {'yes' if classification['security_sensitive'] else 'no'}"
        f" · needs human attention: "
        f"{'yes' if classification['needs_human_attention'] else 'no'}"
    )
