"""Panel arithmetic: how many review records a consumer actually receives (#699).

A downstream gate does not ask "how many agents ran"; it asks "how many reviews
did I get". Those two numbers were never the same and nothing in the tool ever
said so, which is how a healthy three-CLI bench was reported as ``cross-vendor
ready: yes``, exited 0, and then failed at the consumer with *supplied 2
review(s) but tier requires at least 3*.

Two facts settle the arithmetic, and both live here so that every renderer,
``--doctor`` and the run itself quote the same one:

* **The chair sits on the panel.** :func:`ai_jury.orchestrator.resolve_chair`
  only ever picks a chair from the *usable* agents, and round 1 runs every usable
  agent, so the chair reviews on every code path. Its ballot is already counted
  alongside its synthesis — an *n*-agent bench yields *n* ballots, not *n-1*.
  What was missing was any statement of that: a reader of the bundle could not
  tell the trailing ``chair`` record from a synthesis-only entry, and dropping it
  is exactly how a four-record bundle gets reported as two reviews.
* **A silent slot is not a ballot.** An agent that returns nothing is dropped
  from the bundle rather than counted as a stance (see
  :func:`ai_jury.ballots.participating`), so the bundle can be smaller than the
  bench. That shrinkage used to be invisible until the consumer refused it.

Everything here is pure: no I/O, no clock, no randomness.
"""

from __future__ import annotations

#: Review records the bundle carries beyond the panel ballots: the chair's.
#: Named rather than inlined as ``+ 1`` so the one place this arithmetic can
#: change is the one place it is written down.
CHAIR_RECORDS = 1


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


def bundle_size(ballots: int) -> int:
    """Review records a consumer receives for ``ballots`` panel ballots."""
    return int(ballots) + CHAIR_RECORDS


def describe(ballots: int, *, available: int | None = None) -> str:
    """One line stating the number a consumer will actually receive.

    Rendered before the panel runs (from the available agents) and again in the
    report (from the ballots that materialised), so a shortfall is visible at the
    point it can still be fixed rather than at the point it is refused.
    """
    prefix = f"{available} available agent(s) → " if available is not None else ""
    return (
        f"panel: {prefix}{ballots} ballot(s) + {CHAIR_RECORDS} chair record = "
        f"{bundle_size(ballots)} review(s) for a downstream consumer"
    )


def shortfall(ballots: int, required: int, *, stage: str, silent: int = 0) -> str | None:
    """Why this run cannot supply ``required`` reviews, or ``None`` if it can.

    ``stage`` is the phrase naming when the check fired, so the two call sites
    ("before the panel runs" / "after the panel ran") read as one message with
    the timing filled in rather than as two unrelated errors.
    """
    required = int(required or 0)
    if required <= 0:
        return None
    supplied = bundle_size(ballots)
    if supplied >= required:
        return None
    silent_clause = (
        f" {silent} agent(s) ran but returned no review, so they cast no ballot." if silent else ""
    )
    return (
        f"panel too small {stage}: {supplied} review(s) available "
        f"({ballots} ballot(s) + {CHAIR_RECORDS} chair record) but min_reviews "
        f"requires {required}.{silent_clause} Enable another agent, or lower "
        f"`[jury.ci] min_reviews` / `--min-reviews`."
    )
