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
# Issue-review verdict vocabulary (issue #230): the panel votes over an issue's
# completeness instead of a diff's correctness.
READY = "READY"
NEEDS_INFO = "NEEDS-INFO"
UNCLEAR = "UNCLEAR"

# Per-mode vocabulary: the worst gap/finding a reviewer raised maps to a stance
# (blocking severity → strict, middling → soft, none → clear), and ties resolve
# to the strictest stance via the ``order`` (higher = stricter).
_MODES = {
    "code": {
        "blocking": REQUEST_CHANGES,
        "middling": COMMENT,
        "clear": APPROVE,
        # Keys are listed strictest-first so the rendered tally reads
        # "request changes · comment · approve"; the integer values (not key
        # order) drive the tie-break, so display order is free to be intuitive.
        "order": {REQUEST_CHANGES: 2, COMMENT: 1, APPROVE: 0},
    },
    "issue": {
        "blocking": NEEDS_INFO,
        "middling": UNCLEAR,
        "clear": READY,
        "order": {NEEDS_INFO: 2, UNCLEAR: 1, READY: 0},
    },
}

# A reviewer that did not actually review (an empty reply, or a short refusal /
# safety-decline like "I can't assist with that request") must NOT be counted as
# a "clear" vote — a non-answer is not an approval (issue #251). Such reviewers
# abstain and are dropped from the tally entirely. Kept conservative: a genuine
# clean review has substantive prose and won't match. Markers are written in the
# *normalized* (expanded-contraction) form below.
_ABSTENTION_MARKERS = (
    "i cannot assist",
    "i cannot help",
    "cannot help with that",
    "cannot comply",
    "cannot do that",
    "i am unable to",
    "i will not be able to",
    "unable to assist",
    "unable to help",
)


def _normalize_refusal(text: str) -> str:
    """Fold contraction/apostrophe variants so "can't X" and "cannot X" match the
    same marker (issue #251 follow-up: the jury caught "can't comply" slipping
    past a "cannot comply"-only list)."""
    t = text.replace("’", "'")  # smart apostrophe → ascii
    return t.replace("can't", "cannot").replace("won't", "will not").replace("i'm ", "i am ")


def is_abstention(output) -> bool:
    """True when a review is empty or a recognizable non-review (refusal).

    Empty/whitespace output always abstains. A short reply (no real review body)
    that contains a refusal marker also abstains; longer substantive reviews do
    not, even if they happen to quote one of these phrases. Contraction variants
    (``can't``/``cannot``, ``won't``/``will not``, ``i'm``/``i am``) are folded so
    a marker need only be listed once.
    """
    text = (output or "").strip().lower()
    if not text:
        return True
    return len(text) < 400 and any(m in _normalize_refusal(text) for m in _ABSTENTION_MARKERS)


# Worst-severity thresholds. critical/major are blocking; minor/nit are middling.
_MAJOR_RANK = SEVERITY_ORDER["major"]
_NIT_RANK = SEVERITY_ORDER["nit"]


def _severity_to_vote(rank: int, vocab: dict) -> str:
    if rank <= _MAJOR_RANK:
        return vocab["blocking"]
    if rank <= _NIT_RANK:
        return vocab["middling"]
    return vocab["clear"]


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


def tally_votes(groups, reviewers, *, mode: str = "code") -> VoteResult:
    """Tally a panel verdict from the consensus ``groups`` and ``reviewers``.

    Each reviewer votes from the worst-severity group they contributed to that was
    not marked ``unsupported`` by the verifier. The verdict is whichever stance has
    the most ballots; a tie resolves toward the strictest stance. The vocabulary is
    mode-aware (issue #230): ``code`` → REQUEST CHANGES > COMMENT > APPROVE;
    ``issue`` → NEEDS-INFO > UNCLEAR > READY (the panel judges completeness, not
    correctness). With no reviewers the verdict is ``NO QUORUM``.
    """
    vocab = _MODES.get(mode, _MODES["code"])
    order = vocab["order"]
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
            ballots.append(Ballot(rv, vocab["clear"], "no supported findings raised"))
        else:
            sev = SEVERITIES[worst_rank]
            ballots.append(
                Ballot(rv, _severity_to_vote(worst_rank, vocab), f"worst finding: {sev}")
            )

    tally = dict.fromkeys(order, 0)
    for b in ballots:
        tally[b.vote] += 1

    if not ballots:
        return VoteResult(NO_QUORUM, tally, ballots)

    # Most votes wins; tie-break toward the strictest stance.
    verdict = max(tally, key=lambda v: (tally[v], order[v]))
    return VoteResult(verdict, tally, ballots)
