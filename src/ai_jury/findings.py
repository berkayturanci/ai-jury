"""Machine-readable finding schema and parser.

Reviewer/chair output is human-readable markdown, which is hard to dedupe, score,
gate in CI, or turn into inline comments. This module defines a structured
``Finding`` schema and a tolerant parser that extracts findings from an agent's
raw output (a fenced ``json`` code block).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .redaction import redact

SEVERITIES: tuple[str, ...] = ("critical", "major", "minor", "nit", "info")
CONFIDENCES: tuple[str, ...] = ("high", "medium", "low")

# Output-injection guards for attacker-influenced finding text rendered into the
# human-facing markdown report that is posted verbatim to the PR/issue (security
# audit 2026-06-13 round 3). The machine CI gate is a pure function of the
# structured fields and is unaffected by this text; these helpers only stop a
# forged ``## Verdict APPROVE`` heading or a broken code fence from corrupting
# the comment a human (or a downstream grep) reads.
_FENCE_RUN_RE = re.compile(r"`{3,}|~{3,}")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def flatten_inline(text: str) -> str:
    """Collapse text to a single line for safe inline rendering.

    Markdown headings, list items, and code fences must begin a line, so
    flattening newlines (and runs of whitespace) neutralizes forged structure
    when the value is rendered inside a one-line list item.
    """
    if not text:
        return text
    return " ".join(str(text).split())


def fence_safe(text: str) -> str:
    """Break 3+ backtick/tilde runs so text rendered *inside* a code fence
    (e.g. a ``suggestion`` block) cannot close the fence and inject markdown."""
    if not text:
        return text
    return _FENCE_RUN_RE.sub(lambda m: m.group()[0], str(text))


def strip_html_comments(text: str) -> str:
    """Remove HTML comments so attacker text can't forge the jury's hidden
    inline-comment markers (``<!-- arc-inline -->`` / ``<!-- arc-sig:… -->``)."""
    if not text:
        return text
    return _HTML_COMMENT_RE.sub("", str(text))


# Verification verdict statuses (issue #3).
VERDICT_STATUSES: tuple[str, ...] = ("verified", "unsupported", "needs_human_decision")

# Lower number = more severe; useful for ranking/sorting.
SEVERITY_ORDER: dict[str, int] = {sev: i for i, sev in enumerate(SEVERITIES)}

# Legacy severity names mapped onto the canonical schema.
_SEVERITY_ALIASES: dict[str, str] = {"blocker": "critical"}

_DEFAULT_SEVERITY = "info"
_DEFAULT_CONFIDENCE = "medium"

# Matches a fenced ```json ... ``` block (case-insensitive on the language tag).
_JSON_BLOCK_RE = re.compile(r"```[ \t]*json[ \t]*\r?\n(.*?)```", re.DOTALL | re.IGNORECASE)


def _normalize_severity(value: object) -> str:
    if isinstance(value, str):
        v = value.strip().lower()
        v = _SEVERITY_ALIASES.get(v, v)
        if v in SEVERITY_ORDER:
            return v
    return _DEFAULT_SEVERITY


def _normalize_confidence(value: object) -> str:
    if isinstance(value, str):
        v = value.strip().lower()
        if v in CONFIDENCES:
            return v
    return _DEFAULT_CONFIDENCE


@dataclass
class Finding:
    """A single structured review finding."""

    severity: str
    file: str
    claim: str
    line: int | None = None
    evidence: str = ""
    suggested_fix: str = ""
    confidence: str = _DEFAULT_CONFIDENCE
    reviewer: str = ""

    def __post_init__(self) -> None:
        self.severity = _normalize_severity(self.severity)
        self.confidence = _normalize_confidence(self.confidence)
        if self.line is not None and not isinstance(self.line, bool):
            try:
                self.line = int(self.line)
            except (TypeError, ValueError):
                self.line = None
        else:
            self.line = None

    @classmethod
    def from_obj(cls, obj: dict, reviewer: str) -> Finding:
        """Build a Finding from a decoded JSON object, forcing ``reviewer``."""
        return cls(
            severity=str(obj.get("severity", _DEFAULT_SEVERITY)),
            file=str(obj.get("file", "")),
            claim=str(obj.get("claim", "")),
            line=obj.get("line"),
            evidence=str(obj.get("evidence", "")),
            suggested_fix=str(obj.get("suggested_fix", "")),
            confidence=str(obj.get("confidence", _DEFAULT_CONFIDENCE)),
            reviewer=reviewer,
        )


def parse_findings(text: str, reviewer: str) -> tuple[list[Finding], list[str]]:
    """Extract structured findings from an agent's raw output.

    The agent is asked to emit a fenced ```json block holding a JSON array of
    finding objects. We locate the *last* such block, decode it, and build
    Finding objects (forcing ``reviewer`` to preserve identity).

    Never raises. On a malformed/wrong-typed ``json`` block, returns
    ``([], [warning])``. A legitimately missing block yields ``([], [])``.
    """
    if not text:
        return [], []

    blocks = _JSON_BLOCK_RE.findall(text)
    if not blocks:
        return [], []

    raw = blocks[-1].strip()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError, RecursionError) as exc:
        # RecursionError (deeply nested JSON, e.g. "[[[[…") is not a ValueError;
        # catching it keeps the documented "never raises" contract so one
        # steerable reviewer can't abort the whole run (audit 2026-06-13/N-2).
        return [], [f"{reviewer}: malformed or missing structured findings ({redact(str(exc))[0]})"]

    if not isinstance(data, list):
        return [], [
            f"{reviewer}: malformed or missing structured findings "
            f"(expected a JSON array, got {type(data).__name__})"
        ]

    findings: list[Finding] = []
    warnings: list[str] = []
    for i, obj in enumerate(data):
        if not isinstance(obj, dict):
            warnings.append(
                f"{reviewer}: malformed or missing structured findings "
                f"(item {i} is {type(obj).__name__}, expected object)"
            )
            continue
        findings.append(Finding.from_obj(obj, reviewer))
    return findings, warnings


def _coerce_line(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_status(value: object) -> str:
    if isinstance(value, str):
        v = value.strip().lower().replace("-", "_").replace(" ", "_")
        if v in VERDICT_STATUSES:
            return v
    return "needs_human_decision"


@dataclass
class Verdict:
    """A verifier's judgement on a candidate finding."""

    file: str | None = None
    line: int | None = None
    claim: str = ""
    status: str = "needs_human_decision"
    reasoning: str = ""


def parse_verdicts(text: str, verifier: str = "") -> tuple[list[Verdict], list[str]]:
    """Extract verification verdicts from a verifier's raw output.

    The verifier is asked to emit a fenced ```json block holding a JSON array of
    verdict objects. We locate the *last* such block and decode it. Never raises;
    on malformed input returns ``([], [warning])``.
    """
    label = verifier or "verifier"
    if not text:
        return [], [f"{label}: no verdicts (empty output)"]

    blocks = _JSON_BLOCK_RE.findall(text)
    if not blocks:
        return [], [f"{label}: no JSON verdicts block found"]

    raw = blocks[-1].strip()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError, RecursionError) as exc:
        # See parse_findings: RecursionError on deeply nested JSON must not
        # escape (audit 2026-06-13/N-2).
        return [], [f"{label}: malformed verdicts JSON ({redact(str(exc))[0]})"]

    if isinstance(data, dict):
        data = data.get("verdicts", data.get("findings", []))
    if not isinstance(data, list):
        return [], [f"{label}: verdicts block is not a JSON array"]

    verdicts: list[Verdict] = []
    warnings: list[str] = []
    for i, obj in enumerate(data):
        if not isinstance(obj, dict):
            warnings.append(f"{label}: verdict item {i} is {type(obj).__name__}, expected object")
            continue
        verdicts.append(
            Verdict(
                file=(obj.get("file") or None),
                line=_coerce_line(obj.get("line")),
                claim=str(obj.get("claim", "")).strip(),
                status=_normalize_status(obj.get("status")),
                reasoning=str(obj.get("reasoning", "")).strip(),
            )
        )
    return verdicts, warnings
