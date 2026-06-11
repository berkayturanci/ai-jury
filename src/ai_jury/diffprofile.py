"""Cheap, pre-review diff profiling for risk-aware auto-depth (issue #120).

A multi-vendor fan-out is expensive, so it should scale to the change: a
docs-only or few-line diff does not need debate + verification, while a large or
security-touching diff warrants the full panel. This module derives a fast,
PURE risk profile from the raw diff (size, files, whether it only touches
docs/generated files, whether it touches security-sensitive paths) and maps it
to a review depth (rounds / verify / early-stop).

It never trims the *panel* (vendor diversity is the load-bearing advantage) —
only how many rounds run and whether the verification pass runs. Pure and
deterministic; the CLI owns applying it (opt-in) and logging it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .classification import _COMBINED_RX, diff_lines_changed
from .largediff import DEFAULT_GENERATED_GLOBS, _matches_any, split_diff

# Paths that are low-risk to review at full depth (docs/text/config notes).
_DOC_GLOBS: tuple[str, ...] = ("*.md", "*.rst", "*.txt", "docs/**", "*.adoc")

RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

# Thresholds (changed lines / file count) for the high and low bands.
_HIGH_LINES, _HIGH_FILES = 400, 20
_LOW_LINES, _LOW_FILES = 15, 2


@dataclass
class DiffProfile:
    changed_lines: int
    file_count: int
    paths: list[str] = field(default_factory=list)
    docs_or_generated_only: bool = False
    security_sensitive: bool = False
    risk: str = RISK_MEDIUM


def _is_doc_or_generated(path: str) -> bool:
    return _matches_any(path, _DOC_GLOBS) or _matches_any(path, DEFAULT_GENERATED_GLOBS)


def _path_is_security_sensitive(path: str) -> bool:
    # bolt: Evaluate multiple regexes at once via the C engine instead of sequential looping.
    return bool(_COMBINED_RX.search(path))


def profile_diff(diff: str) -> DiffProfile:
    """Profile a unified diff into a deterministic risk band (issue #120)."""
    files = split_diff(diff)
    paths = [f.path for f in files if f.path]
    changed = diff_lines_changed(diff)
    file_count = len(files)

    docs_only = bool(paths) and all(_is_doc_or_generated(p) for p in paths)
    security = any(_path_is_security_sensitive(p) for p in paths)

    if security or changed > _HIGH_LINES or file_count > _HIGH_FILES:
        risk = RISK_HIGH
    elif docs_only or (changed <= _LOW_LINES and file_count <= _LOW_FILES):
        risk = RISK_LOW
    else:
        risk = RISK_MEDIUM

    return DiffProfile(
        changed_lines=changed,
        file_count=file_count,
        paths=paths,
        docs_or_generated_only=docs_only,
        security_sensitive=security,
        risk=risk,
    )


def depth_for(risk: str) -> tuple[int, bool, bool]:
    """Map a risk band to ``(rounds, verify, early_stop)``.

    - low  → 1 round, no verification (trivial change).
    - medium → 2 rounds, no verification, early-stop on (skip debate if agreed).
    - high → 2 rounds + verification, full (no early-stop).
    """
    if risk == RISK_LOW:
        return 1, False, False
    if risk == RISK_HIGH:
        return 2, True, False
    return 2, False, True


def describe(profile: DiffProfile) -> str:
    """One-line, human-readable summary of the profile + chosen depth."""
    rounds, verify, _early = depth_for(profile.risk)
    bits = [
        f"risk={profile.risk}",
        f"{profile.changed_lines} changed lines",
        f"{profile.file_count} file(s)",
    ]
    if profile.docs_or_generated_only:
        bits.append("docs/generated-only")
    if profile.security_sensitive:
        bits.append("security-sensitive paths")
    return f"auto-depth: {', '.join(bits)} → rounds={rounds}, verify={'on' if verify else 'off'}"
