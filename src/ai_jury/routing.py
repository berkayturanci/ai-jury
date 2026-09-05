"""Risk-aware tiered routing: which seats review, which one anchors, who escalates.

Issue #714 (the ask was #524). ``routing = "tiered"`` / ``--tiered`` is a cost
lever, and a cost lever that could silently weaken a review would be worse than
none, so every decision here is a pure function of things the operator wrote
and the diff itself, and every decision is reported:

* The **risk band** is :func:`ai_jury.diffprofile.profile_diff` — the same
  classifier ``--auto`` uses — so a routed run and an auto-depth run agree on
  what "routine" means.
* The **tier** of a seat is ``[[agent]] tier`` (default ``frontier``). There are
  no model-name heuristics: a seat is economical because the operator said so.
* A ``high``-risk diff (security-sensitive paths, large, many files) gets the
  **full** enabled panel; nothing is saved on a change that can hurt.
* A ``low`` or ``medium`` diff gets every **economical** seat plus **one
  frontier anchor** — the configured chair when it is frontier and usable, else
  the first usable frontier seat in config order. The other frontier seats are
  *benched*: they run only if round 1 escalates.
* Two floors always hold, in this order: the panel keeps at least
  ``[jury.ci] min_vendors`` distinct vendors (counted by
  :func:`ai_jury.config.vendor_identity`, as the gate counts them) and at least
  ``[jury.ci] min_reviews`` seats; benched seats are added back in config order
  until both do. A bench with no economical seat, or no frontier seat, runs
  unchanged — there is nothing to save, or nothing to anchor with — and the
  plan says which.
* **Escalation** after round 1: when the consensus groups carry a ``critical``
  or ``major`` finding, the benched frontier seats join the debate as
  cross-examiners and the chair for verification/synthesis is drawn from the
  frontier seats. Without escalation the benched seats stay benched. What they
  contribute is a debate voice the chair reads, not structured findings — the
  debate round has never been parsed for findings, for any seat — so a routed
  run can miss a finding a full panel would have surfaced. That is the trade
  the flag is, and the report names every seat it did not seat.

Pure and deterministic; the orchestrator owns applying it and logging it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import DEFAULT_TIER, AgentSpec, vendor_identity
from .diffprofile import RISK_HIGH, RISK_LOW, RISK_MEDIUM

MODE_STANDARD = "standard"
MODE_TIERED = "tiered"
TIER_ECONOMICAL = "economical"

#: Severities that escalate a routed run after round 1.
ESCALATING_SEVERITIES: tuple[str, ...] = ("critical", "major")


@dataclass
class RoutingPlan:
    """What tiered routing decided, and why — the record the report carries."""

    mode: str
    risk: str
    panel: list[str] = field(default_factory=list)
    benched: list[str] = field(default_factory=list)
    anchor: str | None = None
    reason: str = ""
    escalated: bool = False
    escalation_reason: str = ""

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "risk": self.risk,
            "panel": list(self.panel),
            "benched": list(self.benched),
            "anchor": self.anchor,
            "reason": self.reason,
            "escalated": self.escalated,
            "escalation_reason": self.escalation_reason,
        }


def standard_plan(usable: list[str]) -> RoutingPlan:
    """The plan a ``standard`` run records: everybody sits, nothing is benched."""
    return RoutingPlan(mode=MODE_STANDARD, risk="", panel=list(usable), reason="standard routing")


def _tier_of(spec: AgentSpec) -> str:
    return getattr(spec, "tier", DEFAULT_TIER) or DEFAULT_TIER


def plan_panel(
    specs: list[AgentSpec],
    usable: list[str],
    risk: str,
    *,
    chair: str,
    min_vendors: int = 0,
    min_reviews: int = 0,
) -> RoutingPlan:
    """Decide the round-1 panel for a ``tiered`` run (pure).

    ``specs`` is the enabled bench in config order; ``usable`` the names whose
    CLI answered. ``risk`` is a :mod:`ai_jury.diffprofile` band. The result
    lists the panel and the bench in config order.
    """
    usable_set = set(usable)
    ordered = [s for s in specs if s.name in usable_set]
    names = [s.name for s in ordered]
    economical = [s.name for s in ordered if _tier_of(s) == TIER_ECONOMICAL]
    frontier = [s.name for s in ordered if _tier_of(s) != TIER_ECONOMICAL]

    def full(reason: str) -> RoutingPlan:
        return RoutingPlan(mode=MODE_TIERED, risk=risk, panel=names, reason=reason)

    if risk not in (RISK_LOW, RISK_MEDIUM):
        band = risk if risk == RISK_HIGH else f"{risk!r} (unknown band, treated as high)"
        return full(f"risk={band}: full panel, nothing benched")
    if not economical:
        return full(f"risk={risk}: no economical seat configured, full panel")
    if not frontier:
        return full(f"risk={risk}: no frontier seat to anchor with, full panel")

    anchor = chair if chair in frontier else frontier[0]
    keep = set(economical) | {anchor}
    benched = [n for n in frontier if n != anchor]
    vendor_of = {s.name: vendor_identity(s.vendor) for s in ordered}
    notes: list[str] = []

    def distinct(panel: set[str]) -> int:
        return len({vendor_of[n] for n in panel})

    # Floor 1: the cross-vendor gate must still be reachable. Prefer a benched
    # seat that brings a vendor the panel lacks; fall back to config order.
    while benched and distinct(keep) < min_vendors:
        adding = next(
            (n for n in benched if vendor_of[n] not in {vendor_of[k] for k in keep}), None
        )
        if adding is None:
            adding = benched[0]
        benched.remove(adding)
        keep.add(adding)
        notes.append(f"{adding} unbenched for min_vendors={min_vendors}")
    # Floor 2: the review count a consumer requires.
    while benched and len(keep) < min_reviews:
        adding = benched.pop(0)
        keep.add(adding)
        notes.append(f"{adding} unbenched for min_reviews={min_reviews}")

    panel = [n for n in names if n in keep]
    reason = (
        f"risk={risk}: {len(economical)} economical seat(s) + anchor {anchor}; "
        f"benched {', '.join(benched) if benched else 'nobody'}"
    )
    if notes:
        reason += "; " + "; ".join(notes)
    return RoutingPlan(
        mode=MODE_TIERED, risk=risk, panel=panel, benched=benched, anchor=anchor, reason=reason
    )


def escalation_effect(joined: list[str], debate_ran: bool = True) -> str:
    """What escalation actually did, read off the run rather than predicted.

    Called **after** the debate section, with the benched seats that produced a
    debate result. Predicting this is what two review rounds caught: a
    single-round run has no debate to join (``--auto`` sets exactly one round on
    the ``low`` band that benched the seats), and an adaptive run can converge
    after round 1 and skip the debate it was going to have (``--auto`` sets
    ``early_stop`` on ``medium``). Escalation still moves the chair in both
    cases — verification and synthesis are a frontier seat reading the diff and
    the findings — but the bench did not review, and a record that says it did
    is a record that lies about the run (#714, review rounds 1 and 2).
    """
    if joined:
        return f"{', '.join(joined)} joined the debate"
    if debate_ran:
        # The debate happened; the bench is simply not in it, because every
        # benched call failed. Saying "no debate round ran" would be false
        # about the seated voices that did debate (review round 4).
        return "the debate ran without the bench, so only the chair was escalated"
    return "no debate round ran, so only the chair was escalated"


def should_escalate(groups) -> tuple[bool, str]:
    """Whether round 1 warrants the benched frontier seats (pure).

    ``groups`` are the consensus groups after round 1. Any ``critical`` or
    ``major`` group escalates: those are the findings a cheaper panel is most
    likely to have got wrong in either direction, and the ones the frontier
    seats were kept back for.
    """
    hot = [g for g in groups if getattr(g, "severity", "") in ESCALATING_SEVERITIES]
    if not hot:
        return False, "no critical or major finding after round 1"
    worst = min(hot, key=lambda g: ESCALATING_SEVERITIES.index(g.severity))
    return True, f"{len(hot)} {worst.severity}-or-worse finding group(s) after round 1"


def frontier_names(specs: list[AgentSpec], usable: list[str]) -> list[str]:
    """Usable frontier seats in config order — the chair pool once escalated."""
    usable_set = set(usable)
    return [s.name for s in specs if s.name in usable_set and _tier_of(s) != TIER_ECONOMICAL]


def describe(plan: RoutingPlan) -> str:
    """One log line naming the decision."""
    if plan.mode != MODE_TIERED:
        return "routing: standard (full panel)"
    return f"tiered routing: {plan.reason} → panel {', '.join(plan.panel)}"
