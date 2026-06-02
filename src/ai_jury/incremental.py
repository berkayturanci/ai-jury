"""Incremental review mode for updated PRs (issue #9).

Re-reviewing a large PR's full diff on every push is slow and costly. Incremental
mode reviews only what changed since the jury last ran: it records the
reviewed head SHA in a hidden marker on its summary comment, and on the next run
compares that SHA to the current head to fetch just the new range.

This module holds the PURE, network-free core — embedding/parsing the marker and
deciding the review scope — so it is fully unit-testable. The thin GitHub calls
(fetch head SHA, fetch comment bodies, fetch the range diff) live in
``github.py`` and the CLI; this module never touches the network.
"""
from __future__ import annotations

import re

# Hidden marker embedded in the jury summary comment recording the SHA that
# was reviewed, so a later run can compute the incremental range.
_MARKER_RE = re.compile(r"<!--\s*arc-reviewed-sha:([0-9a-fA-F]{7,40})\s*-->")

MODE_FULL = "full"
MODE_INCREMENTAL = "incremental"


def reviewed_sha_marker(sha: str) -> str:
    """Return the hidden HTML-comment marker recording the reviewed head SHA."""
    return f"<!-- arc-reviewed-sha:{sha} -->"


def parse_reviewed_sha(comment_bodies) -> str | None:
    """Return the most recent reviewed SHA across jury comment bodies, or None.

    Scans every body for the marker and returns the LAST match found (later
    comments override earlier ones), so a fresh re-review marker wins.
    """
    last: str | None = None
    for body in comment_bodies or []:
        for m in _MARKER_RE.finditer(body or ""):
            last = m.group(1)
    return last


def decide_review(prev_sha: str | None, head_sha: str | None) -> tuple[str, str]:
    """Decide review scope from the previous reviewed SHA and the current head.

    Returns ``(mode, reason)`` where ``mode`` is ``"full"`` or ``"incremental"``.
    Falls back to a full review whenever incremental is not safely possible: no
    prior marker, unknown head, or an unchanged head (nothing new to review).
    """
    if not prev_sha:
        return MODE_FULL, "no prior jury marker found — full review"
    if not head_sha:
        return MODE_FULL, "current head SHA unavailable — full review"
    if prev_sha == head_sha:
        return MODE_FULL, "head unchanged since last review — full review"
    return (
        MODE_INCREMENTAL,
        f"incremental: reviewing {prev_sha[:7]}..{head_sha[:7]}",
    )


def compare_range(prev_sha: str, head_sha: str) -> str:
    """Return the ``base...head`` range spec for the GitHub compare API."""
    return f"{prev_sha}...{head_sha}"


def scope_note(mode: str, reason: str) -> str:
    """Render the user-facing review-scope line for the report (issue #9)."""
    label = "Incremental" if mode == MODE_INCREMENTAL else "Full"
    return f"**Review scope:** {label} — {reason}"
