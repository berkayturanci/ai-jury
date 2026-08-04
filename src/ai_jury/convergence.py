"""Convergence signals for adaptive debate rounds (issue #40).

Multi-agent debate has diminishing returns: accuracy plateaus at ~2–3 rounds
while each round costs roughly a full extra panel of agent calls. These helpers
turn the structured consensus state into a deterministic *stop* decision so the
orchestrator can skip a debate round when reviewers already agree and only spend
extra rounds when genuine disagreement remains.

Both functions are PURE (no I/O, no randomness): identical inputs always yield
the same ``(converged, reason)`` pair, so the early-stop decision is reproducible
and unit-testable with mock fixtures.
"""

from __future__ import annotations

import re

from .consensus import BUCKET_CONSENSUS, BUCKET_REJECTED, FindingGroup

# Debate sections that signal *unresolved* disagreement. AGREE does not.
_DISAGREEMENT_SECTIONS = ("dispute", "missed")

# Markdown header line, e.g. "## DISPUTE" or "### Missed findings".
_HEADER_RE = re.compile(r"^#{1,6}\s*(.+?)\s*$")

# Bullet/section content that means "nothing here" and should not count as a
# real disagreement (e.g. "- none", "N/A").
_EMPTY_TOKENS = frozenset({"none", "na", "nothing", "nonenoted", "nonefound"})


def review_convergence(groups: list[FindingGroup], reviewer_count: int) -> tuple[bool, str]:
    """Decide whether round-1 reviews have already converged.

    Converged means there is nothing for a debate round to resolve:

    - no findings were raised at all, or
    - every finding is unanimous consensus (raised by all reviewers), or
    - there are fewer than two reviewers, so there is no one to debate with.

    Any non-unanimous finding (a single-reviewer or majority group) is treated as
    disagreement worth a debate round. Verifier-rejected groups are ignored
    because verification has not run yet at this point.
    """
    if reviewer_count < 2:
        return True, "single reviewer: nothing to debate"

    meaningful = [g for g in groups if g.bucket != BUCKET_REJECTED]
    if not meaningful:
        return True, "no findings raised in round 1"

    non_unanimous = [g for g in meaningful if g.bucket != BUCKET_CONSENSUS]
    if not non_unanimous:
        return True, f"reviewers unanimous on all {len(meaningful)} finding(s) after round 1"

    return False, f"{len(non_unanimous)} non-unanimous finding(s) after round 1"


def _section_has_content(output: str, section_prefixes: tuple[str, ...]) -> bool:
    """True when any named markdown section holds non-empty, non-"none" content."""
    in_section = False
    for raw in (output or "").splitlines():
        line = raw.strip()
        header = _HEADER_RE.match(line)
        if header:
            name = re.sub(r"[^a-z]", "", header.group(1).lower())
            # bolt: avoid generator overhead by passing tuple directly to C-optimized startswith
            in_section = name.startswith(section_prefixes)
            continue
        if in_section and line:
            token = re.sub(r"[^a-z0-9]", "", line.lower())
            if token and token not in _EMPTY_TOKENS:
                return True
    return False


def debate_convergence(debate_results) -> tuple[bool, str]:
    """Decide whether a debate round resolved all disagreement.

    A round has converged when no successful debater still lists a DISPUTE or a
    MISSED finding (an empty or "none" section does not count). Otherwise the
    panel still disagrees and another round may help, up to ``max_rounds``.
    """
    disputing = [
        r.agent
        for r in debate_results
        if getattr(r, "ok", False)
        and _section_has_content(getattr(r, "output", ""), _DISAGREEMENT_SECTIONS)
    ]
    if not disputing:
        return True, "no unresolved disputes or missed findings in debate"
    return False, f"{len(disputing)} debater(s) still raising disputes/missed findings"
