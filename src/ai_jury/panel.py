"""Panel arithmetic: how many reviews a consumer actually receives (#699).

A downstream gate does not ask "how many agents ran"; it asks "how many reviews
did I get". Those two numbers were never the same and nothing in the tool ever
said so, which is how a healthy three-CLI bench was reported as ``cross-vendor
ready: yes``, exited 0, and then failed at the consumer with *supplied 2
review(s) but tier requires at least 3*.

Three facts settle the arithmetic, and all three live here so that every
renderer, ``--doctor`` and the run itself quote the same one:

* **A review is a ballot.** The consumer this feature exists for splits the
  report's ``reviewers`` array on ``role``: the entry carrying ``role: "chair"``
  becomes the panel's consensus record, every other entry is a ballot, and only
  the ballots are counted against the tier's minimum. So the number to announce
  is the ballot count. The chair's synthesis is carried *alongside* the reviews,
  never as one of them — counting it is the original defect wearing a different
  hat, because it makes ai-jury claim one more review than the consumer finds.
* **The chair sits on the panel.** :func:`ai_jury.orchestrator.resolve_chair`
  only ever picks a chair from the *usable* agents, and round 1 runs every usable
  agent, so the chairing agent reviews on every code path. That ballot is an
  ordinary panelist record carrying no chair role, and the consumer counts it
  like any other — an *n*-agent bench yields *n* ballots, not *n-1*. What was
  missing was any statement of that: a reader of the bundle could not tell
  whether the chairing agent had also voted, and one who guesses drops a review
  the panel actually cast.
* **A silent slot is not a ballot.** An agent that returns nothing is dropped
  from the bundle rather than counted as a stance (see
  :func:`ai_jury.ballots.participating`), so the bundle can be smaller than the
  bench. That shrinkage used to be invisible until the consumer refused it.

Everything here is pure: no I/O, no clock, no randomness.
"""

from __future__ import annotations

#: Records the bundle carries that are **not** reviews: the chair's synthesis.
#: Named rather than inlined as ``+ 1`` so that prose about the bundle's *shape*
#: and prose about its *review count* cannot quietly become the same number.
CHAIR_SYNTHESIS_RECORDS = 1


def is_ballot(result) -> bool:
    """Does this round-1 slot produce a ballot in the bundle?

    "Returned output", not "exited 0": adapters fail soft, so a nonzero exit can
    still carry a complete review on stdout. A slot with nothing at all has no
    stance to record and is not a ballot — counting it would invent one.

    This is the single predicate :func:`ai_jury.ballots.participating` and
    :func:`ai_jury.metadata.panel_accounting` both apply, so the number the
    report announces and the number the bundle contains cannot drift apart.
    """
    return bool((getattr(result, "output", "") or "").strip())


def ballot_slots(reviews) -> list:
    """The round-1 slots that produce a ballot, in the stable panel order."""
    return [r for r in (reviews or []) if is_ballot(r)]


def review_count(reviews) -> int:
    """Reviews a consumer receives from these round-1 slots.

    The one count. Every announcement, both halves of the ``min_reviews`` gate,
    the markdown report and ``--doctor`` resolve to this — or, where they already
    hold a ballot count, to that same integer — so the number ai-jury states and
    the number the consumer finds in the bundle are equal by construction.
    """
    return len(ballot_slots(reviews))


def bundle_records(ballots: int) -> int:
    """Total records in the bundle: the ballots, plus the chair's synthesis.

    Deliberately distinct from the review count. It exists for prose describing
    the bundle's *shape* ("this bundle carries N records") and must never answer
    "how many reviews", which is what :func:`review_count` is for.
    """
    return int(ballots) + CHAIR_SYNTHESIS_RECORDS


def describe(ballots: int, *, available: int | None = None) -> str:
    """One line stating the number a consumer will actually receive.

    Rendered before the panel runs (from the available agents) and again in the
    report (from the ballots that materialised), so a shortfall is visible at the
    point it can still be fixed rather than at the point it is refused. The chair
    record is named in the same breath, and named as *not* a review, because this
    line exists to forestall exactly the arithmetic that would add it.
    """
    prefix = f"{available} available agent(s) → " if available is not None else ""
    return (
        f"panel: {prefix}{ballots} ballot(s) = {ballots} review(s) for a downstream "
        f"consumer, plus {CHAIR_SYNTHESIS_RECORDS} chair synthesis record "
        f"(not counted as a review)"
    )


def shortfall(ballots: int, required: int, *, stage: str, silent: int = 0) -> str | None:
    """Why this run cannot supply ``required`` reviews, or ``None`` if it can.

    ``stage`` is the phrase naming when the check fired, so the three call sites
    ("before the panel runs" / "after the panel ran" / "on this machine") read as
    one message with the timing filled in rather than as unrelated errors.
    """
    required = int(required or 0)
    if required <= 0:
        return None
    supplied = int(ballots)
    if supplied >= required:
        return None
    silent_clause = (
        f" {silent} agent(s) ran but returned no review, so they cast no ballot." if silent else ""
    )
    return (
        f"panel too small {stage}: {supplied} review(s) available "
        f"({supplied} ballot(s); the chair's synthesis record is not one of them) "
        f"but min_reviews requires {required}.{silent_clause} Enable another agent, "
        f"or lower `[jury.ci] min_reviews` / `--min-reviews`."
    )
