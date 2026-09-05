"""Panel arithmetic: how many reviews a consumer actually receives (#699, #700).

A downstream gate does not ask "how many agents ran"; it asks "how many reviews
did I get". Those two numbers were never the same and nothing in the tool ever
said so, which is how a healthy three-CLI bench was reported as ``cross-vendor
ready: yes``, exited 0, and then failed at the consumer with *supplied 2
review(s) but tier requires at least 3*.

Four facts settle the arithmetic, and all four live here so that every renderer,
``--doctor`` and the run itself quote the same one:

* **A review is a ballot that reviewed.** :func:`is_review` is the definition,
  and it is the only one: a ``panelist`` record whose scope names something a
  reader could go and check, and whose verdict is a vote rather than
  :data:`ABSTAIN`. Every seat that ran gets a ballot record — that is how the
  report can say *which* seat returned nothing — but a ballot that named nothing
  is the record of a seat that did **not** review, and counting it supplies the
  consumer a review it will refuse. The consumer's own
  ``review-verdict-insubstantial`` rule rejects exactly that shape, so a count
  including it would be #699's mismatch wearing better prose: a number ai-jury
  states and the consumer does not find.
* **The chair's synthesis is not a review.** The consumer splits the report's
  ``reviewers`` array on ``role``: the entry carrying ``role: "chair"`` becomes
  the panel's consensus record, and only a panelist entry can be a review. The
  chair's synthesis is carried *alongside* the reviews, never as one of them.
* **The chair sits on the panel.** :func:`ai_jury.orchestrator.resolve_chair`
  only ever picks a chair from the *usable* agents, and round 1 runs every usable
  agent, so the chairing agent reviews on every code path. That ballot is an
  ordinary panelist record carrying no chair role, and the consumer counts it
  like any other. What was missing was any statement of that: a reader of the
  bundle could not tell whether the chairing agent had also voted, and one who
  guesses drops a review the panel actually cast.
* **A seat can ballot without reviewing.** An agent that returns nothing, refuses,
  fails, or answers with prose naming no file, line or symbol is recorded as an
  abstention naming *which* seat and *why* — visible in the bundle, and excluded
  from the count. So the number of reviews is knowable only as an upper bound
  before the run, which is why the gate is checked on both sides of it.
* **Every seat is in exactly one bucket, and the bucket is the cause.**
  :func:`abstention_buckets` splits the non-reviewing seats five ways — silent,
  named nothing, named only what is not in the change, refused, adapter failed —
  by reading each ballot's own recorded cause. It replaces a subtraction (``ballots - silent - supplied``) that was
  arithmetically right and descriptively false: once a ballot could carry a
  substantive scope and still abstain, the remainder held seats that had named a
  file and then refused, and every renderer billed them as having named nothing
  checkable — a cause the ballot beside the number contradicted (#700, round 5).

Everything here is pure: no I/O, no clock, no randomness.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Stance recorded for a seat that did not review. Defined here rather than in
#: :mod:`ai_jury.ballots` because :func:`is_review` — the one definition of what
#: gets counted — has to test for it, and a second copy of the token is a second
#: place for the count and the record to disagree. ``ballots`` re-exports it.
ABSTAIN = "ABSTAIN"

#: The two values of a ``reviewers`` entry's ``role``. A consumer splits on this.
PANELIST_ROLE = "panelist"
CHAIR_ROLE = "chair"

#: Records the bundle carries that are **not** reviews: the chair's synthesis.
#: Named rather than inlined as ``+ 1`` so that prose about the bundle's *shape*
#: and prose about its *review count* cannot quietly become the same number.
CHAIR_SYNTHESIS_RECORDS = 1

#: Why a seat that balloted did not review. Five causes, because five different
#: things happen to a seat and they ask for five different fixes.
SILENT = "silent"
NAMED_NOTHING = "named_nothing"
#: The seat named something and none of it is in the change (#710). Split out of
#: ``named_nothing`` because the two send their reader to opposite places: a
#: reviewer that named nothing did not say what it read, while one that named
#: `src/made/up.py` said it read a file that is not in the diff — a claim about
#: coverage that the change itself contradicts, and a much louder signal.
NOT_IN_CHANGE = "not_in_change"
REFUSED = "refused"
ADAPTER_FAILED = "adapter_failed"

#: The causes in the order every renderer lists them, and the key set of
#: :func:`abstention_buckets` — so a renderer cannot iterate a cause the buckets
#: do not carry, nor miss one they do.
ABSTENTION_CAUSES = (SILENT, NAMED_NOTHING, NOT_IN_CHANGE, REFUSED, ADAPTER_FAILED)

#: The clause each cause contributes to a count sentence, subject ``"N "``. One
#: table, because the markdown report and :func:`shortfall` describe the same
#: seats and used to phrase them apart — and the phrasing was where they lied.
#: Every clause is true of *every* ballot in its bucket; in particular none of
#: them says the seat named nothing, except the one bucket where that is what
#: happened. "N named nothing checkable" asserted over a seat that named a file
#: and *then* refused is the defect this table exists to make impossible
#: (#700, round 5).
CAUSE_PHRASES = {
    SILENT: "returned nothing at all",
    NAMED_NOTHING: "answered but named nothing checkable",
    NOT_IN_CHANGE: "named only things that are not in this change",
    REFUSED: "returned a refusal rather than a review",
    ADAPTER_FAILED: "reported an adapter failure",
}

#: The run-metadata key each cause is published under. Kept here because three
#: modules read those keys and one of them is not spelled like its cause:
#: ``named_nothing`` ships as ``insubstantial``, the name it has had since #700's
#: second round, and renaming a published key to tidy a table would break every
#: consumer reading it. A renderer iterating this mapping cannot leave a cause
#: out, which is what a hand-written pair of ``if`` statements did.
PANEL_METADATA_KEYS = {
    SILENT: "silent",
    NAMED_NOTHING: "insubstantial",
    NOT_IN_CHANGE: "not_in_change",
    REFUSED: "refused",
    ADAPTER_FAILED: "adapter_failed",
}

#: The key a ballot record carries its cause under. Read from the record, never
#: recomputed from it: whether a seat fell silent or refused is a fact about the
#: result that produced the ballot, and no predicate over the record alone can
#: tell those two apart — both are ``scope_substantive: false`` when the refusal
#: named nothing, which is why subtraction was reached for in the first place.
CAUSE_FIELD = "abstention_cause"


def responded(result) -> bool:
    """Did this round-1 seat return any output at all?

    "Returned output", not "exited 0": adapters fail soft, so a nonzero exit can
    still carry a complete review on stdout. A seat with nothing at all still
    gets a ballot — an abstention naming it, so the report can say which seat
    fell silent (#700, round 2) — but this predicate is what lets that record
    state *which* kind of nothing came back.
    """
    return bool((getattr(result, "output", "") or "").strip())


def ballot_seats(reviews) -> list:
    """The round-1 seats that produce a ballot, in the stable panel order.

    **Every** seat that ran. A seat that returned nothing used to be dropped
    here, which left the bundle unable to say which agent had gone silent: the
    report simply had one fewer entry than the bench, and a reader comparing the
    two had to guess which. It is recorded as an abstention instead, and
    :func:`is_review` is what keeps it out of the count.
    """
    return list(reviews or [])


def is_review(ballot: Mapping[str, Any]) -> bool:
    """**The** definition: does this ballot record count as a review? (pure)

    Three conditions, and each one is a defect this project has already shipped:

    * ``role == "panelist"`` — the chair's synthesis record is the panel's
      consensus, not an *n+1*-th review (#699).
    * ``scope_substantive`` — the ballot names something a reader could go and
      check. A prose-only reply (*"Looks good to me, no concerns."*) names
      nothing, and the consumer refuses a verdict shaped like that (#700).
    * ``verdict != ABSTAIN`` — the seat actually cast a vote. An abstention is
      not an approval (#251), and it is not a review either.

    Read from the record rather than recomputed from the agent result, because
    the record is what the consumer is handed: if the two could disagree, the
    number ai-jury announces would once again describe something other than the
    document it shipped.
    """
    if not isinstance(ballot, Mapping):  # pragma: no cover - defensive
        return False
    return (
        (ballot.get("role") or "") == PANELIST_ROLE
        and bool(ballot.get("scope_substantive"))
        and (ballot.get("verdict") or "") != ABSTAIN
    )


def review_count(ballots) -> int:
    """Reviews a consumer receives from these ballot records.

    The one count. Every announcement, both halves of the ``min_reviews`` gate,
    the markdown report, the chair record's ``reviews_supplied`` and ``--doctor``
    resolve to this, so the number ai-jury states and the number the consumer
    finds in the bundle are equal by construction.

    Takes the **ballots**, not the raw round-1 results: whether a seat reviewed
    is a fact about the record it produced — its scope and its verdict — and no
    predicate over the raw result can see it.
    """
    return sum(1 for b in ballots or [] if is_review(b))


def panelist_ballots(ballots) -> list:
    """The ballot records that are *seats* — everything but the chair's synthesis.

    The unit the buckets and the count are both taken over, so "how many seats
    balloted" and "how many of them reviewed" can never be measured against two
    different populations.
    """
    return [
        b
        for b in ballots or []
        if isinstance(b, Mapping) and (b.get("role") or "") == PANELIST_ROLE
    ]


def abstention_cause(ballot: Mapping[str, Any]) -> str:
    """Why this ballot is not a review — one of :data:`ABSTENTION_CAUSES` (pure).

    **Read** from :data:`CAUSE_FIELD` rather than recomputed. Which of the four
    happened is a fact about the result that produced the ballot: a seat that
    fell silent and one that answered with nothing checkable leave the same two
    structural fields behind, so a record that does not carry its cause has lost
    it. The ballot is what the consumer is handed, so the cause travels with it.

    A record written by an older build — or by hand, as tests do — carries no
    cause, and is read from the fields every ballot has rather than refused: a
    failed adapter says so in ``round1_ok``, and an empty ``scope_substantive``
    means nothing checkable was named. That reading cannot see silence, which is
    exactly why the field exists; it errs toward ``named_nothing``, which is true
    of a silent seat as well.
    """
    if not isinstance(ballot, Mapping):  # pragma: no cover - defensive
        return NAMED_NOTHING
    recorded = (ballot.get(CAUSE_FIELD) or "").strip()
    if recorded in ABSTENTION_CAUSES:
        return recorded
    if not ballot.get("round1_ok", True):
        return ADAPTER_FAILED
    if not ballot.get("scope_substantive"):
        return NAMED_NOTHING
    return REFUSED


def abstention_buckets(ballots) -> dict[str, int]:
    """The seats that balloted without reviewing, counted by cause (pure).

    The five counts and :func:`review_count` partition the panelist ballots:
    every seat lands in exactly one, because each is classified by what happened
    to it. Subtraction is what this replaces, and what subtraction shipped: with
    ``insubstantial`` derived as ``ballots - silent - supplied``, every seat that
    was neither silent nor a counted review was billed as "named nothing
    checkable" — including, since a ballot could carry a substantive scope and
    still abstain, the seat that named a file and *then* refused, and the one
    whose adapter died holding a file name. The number was right and the cause
    printed beside it was contradicted by the ballot it described (#700, round 5).
    """
    counts = dict.fromkeys(ABSTENTION_CAUSES, 0)
    for ballot in panelist_ballots(ballots):
        if not is_review(ballot):
            counts[abstention_cause(ballot)] += 1
    return counts


def bundle_records(ballots: int) -> int:
    """Total records in the bundle: the ballots, plus the chair's synthesis.

    Deliberately distinct from the review count, and now distinct from it in two
    directions: the chair record is one more record than there are ballots, and
    a ballot is not necessarily a review. It exists for prose describing the
    bundle's *shape* ("this bundle carries N records") and must never answer
    "how many reviews", which is what :func:`review_count` is for.
    """
    return int(ballots) + CHAIR_SYNTHESIS_RECORDS


def describe(reviews: int, *, available: int | None = None) -> str:
    """One line stating the number a consumer will actually receive.

    Rendered before the panel runs, from the agents that are reachable — which
    can only be an **upper bound**, and now says so: every seat is a seat that
    *might* review, and one that returns nothing or names nothing checkable is
    recorded as an abstention rather than a review. Passing ``available``
    selects that pre-run wording; without it the number is the count that
    actually materialised.

    The chair record is named in the same breath, and named as *not* a review,
    because this line exists to forestall exactly the arithmetic that would add
    it.
    """
    tail = f"plus {CHAIR_SYNTHESIS_RECORDS} chair synthesis record (not counted as a review)"
    if available is not None:
        return (
            f"panel: {available} available agent(s) → at most {available} review(s) for a "
            f"downstream consumer (one per seat that names what it read and votes; a seat "
            f"that returns nothing, or names nothing checkable, abstains and is not one), "
            f"{tail}"
        )
    return f"panel: {reviews} review(s) for a downstream consumer, {tail}"


def shortfall(
    supplied: int,
    required: int,
    *,
    stage: str,
    silent: int = 0,
    insubstantial: int = 0,
    not_in_change: int = 0,
    refused: int = 0,
    adapter_failed: int = 0,
) -> str | None:
    """Why this run cannot supply ``required`` reviews, or ``None`` if it can.

    ``stage`` is the phrase naming when the check fired, so the three call sites
    ("before the panel runs" / "after the panel ran" / "on this machine") read as
    one message with the timing filled in rather than as unrelated errors.

    The five counts are :func:`abstention_buckets` — the ways a seat that ran
    produces no review — and they are named separately because the remedies
    differ: a silent agent is usually a CLI that broke or a budget that ran out,
    a seat that answered and named nothing is a reviewer that did not review, a
    seat that named only things absent from the change reviewed something that
    is not this diff, a refusal is a model declining the task, and a failed
    adapter is an invocation to fix. ``insubstantial`` keeps its name for
    ``named_nothing`` and means exactly that. Pass them as the buckets report
    them: a caller that folds causes into one prints a sentence the ballots
    contradict.
    """
    required = int(required or 0)
    if required <= 0:
        return None
    supplied = int(supplied)
    if supplied >= required:
        return None
    counted = {
        SILENT: int(silent),
        NAMED_NOTHING: int(insubstantial),
        NOT_IN_CHANGE: int(not_in_change),
        REFUSED: int(refused),
        ADAPTER_FAILED: int(adapter_failed),
    }
    reasons = [
        f"{counted[cause]} {CAUSE_PHRASES[cause]}" for cause in ABSTENTION_CAUSES if counted[cause]
    ]
    missing = sum(counted.values())
    clause = (
        f" {missing} seat(s) ran without producing a review "
        f"({'; '.join(reasons)}), so they are recorded as abstentions "
        f"and are not counted."
        if missing
        else ""
    )
    return (
        f"panel too small {stage}: {supplied} review(s) available "
        f"(the chair's synthesis record is not one of them, and neither is an "
        f"abstaining ballot) but min_reviews requires {required}.{clause} "
        f"Enable another agent, or lower `[jury.ci] min_reviews` / `--min-reviews`."
    )
