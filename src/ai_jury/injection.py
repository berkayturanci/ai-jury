"""Prompt-injection heuristics for untrusted review input (OWASP LLM01).

The jury feeds attacker-controlled content (the PR diff, and via ``--pr`` the
PR title/body) into reviewer prompts. This module scans that content for common
prompt-injection patterns and surfaces hits as *synthetic findings/warnings* —
it never alters agent behaviour or the CI gate. Surfacing-not-obeying is the
whole point: a human (and the structured consensus pipeline) stays in control.

The detector is intentionally conservative and dependency-free (stdlib only).
False positives are acceptable here because a hit only adds an advisory finding;
it cannot flip a verdict.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Zero-width / bidi / invisible control characters often used to smuggle hidden
# text. Built from explicit code points (NOT invisible string literals) so the
# set is readable, verifiable, and can't be silently stripped by an editor or a
# whitespace normalizer (review of #303/L-2). Extended to cover direction marks,
# invisible math operators, soft hyphen, CGJ, Mongolian vowel separator, and
# Hangul fillers.
_ZERO_WIDTH_CODEPOINTS = (
    0x200B,  # zero-width space
    0x200C,  # zero-width non-joiner
    0x200D,  # zero-width joiner
    0x2060,  # word joiner
    0xFEFF,  # zero-width no-break space / BOM
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,  # bidi embedding/override controls
    0x200E, 0x200F,  # LRM / RLM (left/right-to-left marks)
    0x061C,  # Arabic letter mark
    0x2061, 0x2062, 0x2063, 0x2064,  # invisible math operators
    0x00AD,  # soft hyphen
    0x034F,  # combining grapheme joiner
    0x180E,  # Mongolian vowel separator
    0x115F, 0x1160, 0x3164, 0xFFA0,  # Hangul fillers (render as invisible)
)
_ZERO_WIDTH = "".join(chr(c) for c in _ZERO_WIDTH_CODEPOINTS)
_ZERO_WIDTH_RE = re.compile("[" + re.escape(_ZERO_WIDTH) + "]")

# Imperative phrases that try to override the system/developer instructions.
_PHRASE_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("override-instructions", re.compile(
        r"(?i)\b(ignore|disregard|forget|override)\b[^.\n]{0,40}"
        r"\b(previous|prior|above|earlier|all|any|the)\b[^.\n]{0,20}"
        r"\b(instruction|prompt|message|context|rule|direction)s?\b"
    )),
    ("role-reassignment", re.compile(
        r"(?i)\byou\s+are\s+now\b|\bnew\s+(instructions?|persona|role|system\s+prompt)\b"
    )),
    ("fake-system-turn", re.compile(
        r"(?im)^\s*(system|assistant|developer)\s*:",
    )),
    ("verdict-coercion", re.compile(
        r"(?i)\b(approve|lgtm|pass|merge)\b[^.\n]{0,40}"
        r"\b(no\s+findings?|no\s+issues?|without\s+(any\s+)?(review|findings?|comment))\b"
    )),
    ("instruction-tag", re.compile(
        r"(?i)<\s*/?\s*(system|instructions?|prompt)\s*>"
    )),
)

# A long run of base64-ish characters can hide an encoded payload. The class
# includes URL-safe base64 (`-_`) as well as standard `+/` (issue #303/L-2).
_BASE64_RE = re.compile(r"[A-Za-z0-9+/_-]{120,}={0,2}")


@dataclass
class InjectionHit:
    """One suspicious pattern detected in untrusted content."""

    kind: str
    source: str  # "diff" or "context"
    line: int | None
    snippet: str

    def location(self) -> str:
        loc = self.source
        if self.line is not None:
            loc = f"{self.source}:{self.line}"
        return loc


def _snippet(text: str, start: int, end: int, width: int = 60) -> str:
    frag = text[start:end]
    frag = frag.replace("\n", "\\n").replace("\r", "")
    frag = "".join(ch for ch in frag if ch.isprintable())
    if len(frag) > width:
        frag = frag[:width] + "..."
    return frag.strip()


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def scan(text: str, source: str = "diff") -> list[InjectionHit]:
    """Scan *text* for prompt-injection patterns.

    Returns a (possibly empty) list of :class:`InjectionHit`. Never raises.
    *source* labels where the text came from ("diff" or "context").
    """
    if not text:
        return []

    hits: list[InjectionHit] = []

    for kind, pat in _PHRASE_PATTERNS:
        for m in pat.finditer(text):
            hits.append(
                InjectionHit(
                    kind=kind,
                    source=source,
                    line=_line_of(text, m.start()),
                    snippet=_snippet(text, m.start(), m.end()),
                )
            )

    for m in _BASE64_RE.finditer(text):
        hits.append(
            InjectionHit(
                kind="base64-blob",
                source=source,
                line=_line_of(text, m.start()),
                snippet=f"{len(m.group(0))}-char base64-like blob",
            )
        )

    for m in _ZERO_WIDTH_RE.finditer(text):
        hits.append(
            InjectionHit(
                kind="zero-width-char",
                source=source,
                line=_line_of(text, m.start()),
                snippet=f"hidden control char U+{ord(m.group(0)):04X}",
            )
        )

    return hits


def scan_inputs(diff: str, context: str = "") -> list[InjectionHit]:
    """Scan both the diff and PR context, labelling each hit's source."""
    hits = scan(diff, source="diff")
    if context:
        hits.extend(scan(context, source="context"))
    return hits


def hits_to_warnings(hits: list[InjectionHit]) -> list[str]:
    """Render hits as human-readable warning strings for ``outcome.warnings``."""
    out: list[str] = []
    for h in hits:
        out.append(
            f"possible prompt-injection ({h.kind}) in {h.location()}: {h.snippet}"
        )
    return out


def hits_to_finding(hits: list[InjectionHit]):
    """Build a single synthetic ``[major]`` Finding summarizing all hits.

    Returns ``None`` when there are no hits. The finding is advisory: it informs
    the human and report, but the CI gate is derived from structured *consensus*
    (see ``ci.evaluate_ci``), so an injected "APPROVE" cannot flip the gate.
    """
    if not hits:
        return None
    # Imported lazily to avoid a circular import (findings has no dep on us).
    from .findings import Finding

    first = hits[0]
    kinds = sorted({h.kind for h in hits})
    locs = ", ".join(dict.fromkeys(h.location() for h in hits[:5]))
    claim = (
        f"possible prompt-injection in untrusted input "
        f"({len(hits)} hit(s): {', '.join(kinds)})"
    )
    return Finding(
        severity="major",
        file=first.source,
        line=first.line,
        claim=claim,
        evidence=(
            "Untrusted diff/PR content contains text resembling instructions to "
            f"the model at {locs}. Treated as data, not obeyed."
        ),
        suggested_fix=(
            "Review the flagged locations manually; do not act on any instructions "
            "embedded in the diff or PR description."
        ),
        confidence="medium",
        reviewer="injection-scanner",
    )
