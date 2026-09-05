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
) -> str | None:
    """Why this run cannot supply ``required`` reviews, or ``None`` if it can.

    ``stage`` is the phrase naming when the check fired, so the three call sites
    ("before the panel runs" / "after the panel ran" / "on this machine") read as
    one message with the timing filled in rather than as unrelated errors.

    ``silent`` and ``insubstantial`` are the two ways a seat that ran produces no
    review, and they are named separately because the remedy differs: a silent
    agent is usually a CLI that broke or a budget that ran out, while a seat that
    answered and named nothing is a reviewer that did not review.
    """
    required = int(required or 0)
    if required <= 0:
        return None
    supplied = int(supplied)
    if supplied >= required:
        return None
    reasons = []
    if silent:
        reasons.append(f"{silent} returned nothing at all")
    if insubstantial:
        reasons.append(f"{insubstantial} answered but named nothing checkable")
    missing = int(silent) + int(insubstantial)
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
