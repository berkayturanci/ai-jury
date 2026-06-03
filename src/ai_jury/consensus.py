"""Deterministic consensus grouping of findings across reviewers.

Groups findings that refer to the same underlying issue so the report can
distinguish issues raised by every reviewer (consensus) from those raised by a
single reviewer (single_reviewer). Grouping is fully deterministic: identical
input always produces identical output.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .findings import SEVERITY_ORDER, Finding

# Buckets describing how broadly a finding was raised.
BUCKET_CONSENSUS = "consensus"
BUCKET_MAJORITY = "majority"
BUCKET_SINGLE = "single_reviewer"
# Verification-derived buckets (issue #3).
BUCKET_REJECTED = "rejected"
BUCKET_DISPUTED = "disputed"

# Line proximity threshold: findings within this many lines are treated as the
# same location (when both have a line).
LINE_PROXIMITY = 3
# Token-set Jaccard threshold for treating two claims as the same.
JACCARD_THRESHOLD = 0.6


@dataclass
class FindingGroup:
    representative: Finding
    reviewers: list[str] = field(default_factory=list)
    severity: str = "info"
    members: list[Finding] = field(default_factory=list)
    bucket: str = BUCKET_SINGLE
    # Verification status (issue #3); empty until a verdict is attached.
    status: str = ""
    status_reasoning: str = ""


def _normalize_path(path) -> str:
    if not path:
        return ""
    return str(path).strip().replace("\\", "/").lstrip("./").lower()


def _normalize_claim(claim) -> str:
    text = (claim or "").lower()
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(claim_norm: str) -> set[str]:
    return set(claim_norm.split())


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _same_location(a: Finding, b: Finding) -> bool:
    if _normalize_path(a.file) != _normalize_path(b.file):
        return False
    if a.line is None and b.line is None:
        return True
    if a.line is None or b.line is None:
        return False
    return abs(a.line - b.line) <= LINE_PROXIMITY


def _same_claim(a_norm: str, b_norm: str) -> bool:
    if a_norm == b_norm:
        return True
    return _jaccard(_tokens(a_norm), _tokens(b_norm)) >= JACCARD_THRESHOLD


def _sort_key(f: Finding):
    return (_normalize_path(f.file), f.line if f.line is not None else -1, _normalize_claim(f.claim))


def _max_severity(findings) -> str:
    # Lower SEVERITY_ORDER index == more severe.
    best = None
    best_rank = None
    for f in findings:
        rank = SEVERITY_ORDER.get(f.severity, len(SEVERITY_ORDER))
        if best_rank is None or rank < best_rank:
            best_rank = rank
            best = f.severity
    return best or "info"


def _classify(reviewer_count: int, distinct_reviewers: int) -> str:
    if reviewer_count > 0 and distinct_reviewers >= reviewer_count:
        return BUCKET_CONSENSUS
    if distinct_reviewers > 1:
        return BUCKET_MAJORITY
    return BUCKET_SINGLE


def group_findings(findings, reviewer_count: int) -> list[FindingGroup]:
    """Group findings that describe the same issue.

    Findings match when they share a normalized file path, are within
    ``LINE_PROXIMITY`` lines (or both have no line), and have an equal or
    sufficiently similar (token-set Jaccard) normalized claim.

    Deterministic: findings are sorted before greedy grouping, and the resulting
    groups are returned in a stable order.
    """
    ordered = sorted(findings, key=_sort_key)
    groups: list[FindingGroup] = []
    group_claim_norms: list[str] = []

    for finding in ordered:
        f_claim = _normalize_claim(finding.claim)
        placed = False
        for idx, group in enumerate(groups):
            rep = group.representative
            if _same_location(finding, rep) and _same_claim(f_claim, group_claim_norms[idx]):
                group.members.append(finding)
                placed = True
                break
        if not placed:
            groups.append(
                FindingGroup(
                    representative=finding,
                    members=[finding],
                )
            )
            group_claim_norms.append(f_claim)

    for group in groups:
        reviewers = sorted({m.reviewer for m in group.members if m.reviewer})
        group.reviewers = reviewers
        group.severity = _max_severity(group.members)
        # Pick the most severe member as representative for display stability.
        group.representative = min(
            group.members,
            key=lambda m: (
                SEVERITY_ORDER.get(m.severity, len(SEVERITY_ORDER)),
                _sort_key(m),
            ),
        )
        group.bucket = _classify(reviewer_count, len(reviewers))

    # Stable, deterministic output order: severity, then location, then claim.
    groups.sort(
        key=lambda g: (
            SEVERITY_ORDER.get(g.severity, len(SEVERITY_ORDER)),
            _normalize_path(g.representative.file),
            g.representative.line if g.representative.line is not None else -1,
            _normalize_claim(g.representative.claim),
        )
    )
    return groups
