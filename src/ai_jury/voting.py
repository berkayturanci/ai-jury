"""Panel voting: derive a verdict by tallying the reviewers instead of letting a
single chair decide (issue #220).

Pure and deterministic — a function of the consensus groups and the reviewer
names only (no I/O, no wall-clock, no randomness). Each reviewer's vote is
derived from the worst-severity finding they raised that the verifier did not
reject ("unsupported"); the panel verdict is the majority, ties broken toward the
more conservative stance. This is a rendering/aggregation layer: it never changes
how agents run, and the severity-based CI gate (:func:`ai_jury.ci.evaluate_ci`)
remains the independent hard safety check.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .findings import SEVERITIES, SEVERITY_ORDER

APPROVE = "APPROVE"
COMMENT = "COMMENT"
REQUEST_CHANGES = "REQUEST CHANGES"
NO_QUORUM = "NO QUORUM"

# Conservatism order for tie-breaking: a tie resolves to the stricter stance.
_ORDER = {APPROVE: 0, COMMENT: 1, REQUEST_CHANGES: 2}

# Worst-severity → vote. critical/major are blocking; minor/nit are comments.
_MAJOR_RANK = SEVERITY_ORDER["major"]
_NIT_RANK = SEVERITY_ORDER["nit"]


def _severity_to_vote(rank: int) -> str:
    if rank <= _MAJOR_RANK:
        return REQUEST_CHANGES
    if rank <= _NIT_RANK:
        return COMMENT
    return APPROVE


@dataclass
class Ballot:
    reviewer: str
    vote: str
    reason: str


@dataclass
class VoteResult:
    verdict: str
    tally: dict = field(default_factory=dict)
    ballots: list[Ballot] = field(default_factory=list)


def tally_votes(groups, reviewers) -> VoteResult:
    """Tally a panel verdict from the consensus ``groups`` and ``reviewers``.

    Each reviewer votes from the worst-severity group they contributed to that was
    not marked ``unsupported`` by the verifier. The panel verdict is whichever vote
    has the most ballots; a tie resolves toward the more conservative stance
    (REQUEST CHANGES > COMMENT > APPROVE). With no reviewers the verdict is
    ``NO QUORUM``.
    """
    ballots: list[Ballot] = []
    for rv in reviewers:
        worst_rank: int | None = None
        for g in groups:
            if rv not in getattr(g, "reviewers", []):
                continue
            if (getattr(g, "status", "") or "") == "unsupported":
                continue  # verifier rejected it — doesn't count toward this vote
            rank = SEVERITY_ORDER.get(getattr(g, "severity", ""), len(SEVERITIES) - 1)
            if worst_rank is None or rank < worst_rank:
                worst_rank = rank
        if worst_rank is None:
            ballots.append(Ballot(rv, APPROVE, "no supported findings raised"))
        else:
            sev = SEVERITIES[worst_rank]
            ballots.append(Ballot(rv, _severity_to_vote(worst_rank), f"worst finding: {sev}"))

    tally = {APPROVE: 0, COMMENT: 0, REQUEST_CHANGES: 0}
    for b in ballots:
        tally[b.vote] += 1

    if not ballots:
        return VoteResult(NO_QUORUM, tally, ballots)

    # Most votes wins; tie-break toward the more conservative stance.
    verdict = max(tally, key=lambda v: (tally[v], _ORDER[v]))
    return VoteResult(verdict, tally, ballots)
