"""Run metadata and cost-awareness (wall-clock proxy) reporting.

Builds a machine-readable metadata dict describing a jury run: which
agents participated, their per-agent status and wall-clock duration, how many
rounds ran, whether verification was enabled, and timestamps.

IMPORTANT: This metadata deliberately contains NO diff text, NO prompt text,
NO agent output, and NO secrets -- only structural/operational signals.

There are no token counts available from the underlying CLIs, so wall-clock
seconds are used as an approximate cost *proxy*, not a dollar cost.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .config import vendor_identity

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .config import JuryConfig
    from .orchestrator import JuryOutcome

# v2 (issue #30/#40) added: stop_reason, skipped, retried, budget_exhausted,
# execution{...}, and per-agent ``attempts``.
# v4 (issue #501) added: ``panel`` (configured vs effective size, abstentions) and
# per-agent ``review_status``. A slot that returns no review is an abstention, not an
# approval, and until now nothing in the output said so.
SCHEMA_VERSION = 4


#: What a reviewer slot actually contributed (issue #501). ``clean`` and
#: ``abstained`` both carry zero findings and used to be reported identically,
#: which is how a run with two non-reviewing slots still described itself as a
#: three-agent panel.
REVIEW_STATUSES = ("findings", "clean", "abstained", "failed")


def review_status(result) -> str:
    """Classify one reviewer slot's contribution. Pure, and never judges content.

    * ``failed``    — the adapter did not return a result at all.
    * ``findings``  — produced at least one structured finding.
    * ``clean``     — emitted a findings block that was empty: examined, found nothing.
    * ``abstained`` — returned successfully with no findings block. Not an approval:
      nothing reviewable came back, so this slot contributed no evidence either way.
    """
    if not getattr(result, "ok", False):
        return "failed"
    if getattr(result, "findings", None):
        return "findings"
    return "clean" if getattr(result, "structured", False) else "abstained"


def panel_accounting(reviews) -> dict:
    """Configured versus *effective* panel size, and the per-status breakdown.

    A consumer gating on the panel needs the effective number — keel downgrades a
    jury to advisory below two participating vendors, and can only do that if the
    report says the panel was short. ``vendors`` counts distinct vendors that
    actually contributed a review, which is the number that matters for
    cross-vendor consensus: three slots from one vendor are not three perspectives.

    Vendors are counted by :func:`config.vendor_identity`, not by the raw
    string: a seat whose vendor the tool does not recognise ran on the generic
    ``cli`` fallback and is counted as ``cli``, so two unidentifiable seats are
    one vendor here even though the report still names each one honestly
    (issue #701).
    """
    reviews = list(reviews or [])

    # bolt: Consolidate multiple metrics into a single-pass O(N) explicit loop
    effective_count = 0
    abstained_count = 0
    failed_count = 0
    contributing_vendors = set()

    for r in reviews:
        st = review_status(r)
        if st in ("findings", "clean"):
            effective_count += 1
            vendor = vendor_identity(getattr(r, "vendor", ""))
            if vendor:
                contributing_vendors.add(vendor)
        elif st == "abstained":
            abstained_count += 1
        elif st == "failed":
            failed_count += 1

    return {
        "configured": len(reviews),
        "effective": effective_count,
        "vendors": len(contributing_vendors),
        "abstained": abstained_count,
        "failed": failed_count,
        "short": effective_count < len(reviews),
    }


def distinct_vendors(specs) -> int:
    """How many distinct vendors a set of agent specs represents (pure).

    Slots, not vendors, is the mistake this exists to prevent: three
    ``[[agent]]`` entries all pointing at one vendor are one perspective, so a
    run configured that way never claimed cross-vendor consensus and must not be
    failed for not delivering it.

    Counted by :func:`config.vendor_identity`, so a pair of seats naming
    vendors this build does not know collapses to the single ``cli`` identity
    they actually share (issue #701) rather than reading as two.
    """
    return len({vendor_identity(getattr(s, "vendor", "")) for s in specs or []} - {""})


def collapse_reason(reviews, required: int, configured_vendors: int | None = None) -> str | None:
    """Why this run may not stand as cross-vendor consensus, or ``None``.

    PURE. ``required`` is the number of distinct vendors that must have
    *contributed* a review (:func:`panel_accounting`'s ``vendors``), not the
    number configured — an agent that was installed, probed clean and then
    returned nothing is exactly the failure this guards (#635/#682).

    ``configured_vendors`` scopes the DEFAULT: when fewer distinct vendors are
    enabled than ``required``, the run never claimed cross-vendor consensus and
    is left alone, so turning the guard on by default cannot fail a
    single-vendor install that was always honest about being one. Pass ``None``
    for an explicitly requested threshold, which is enforced as asked.

    The message NAMES the opt-out. Whoever reads it is looking at a red CI step
    on a gate that ships on by default, quite possibly for the first time, and a
    failure that does not say how to accept it sends them to the issue tracker
    for a flag the tool already has.
    """
    if required <= 0:
        return None
    if configured_vendors is not None and configured_vendors < required:
        return None
    contributed = panel_accounting(reviews).get("vendors", 0)
    if contributed >= required:
        return None
    return (
        f"panel collapsed: {contributed} vendor(s) contributed a review, "
        f"{required} required. An abstention is not an approval; "
        f"cross-vendor consensus was not formed. To accept a collapsed panel, "
        f"pass --no-min-vendors (or set [jury.ci] min_vendors = 0); to catch a "
        f"missing CLI at startup instead, run with --strict."
    )


def _agent_entry(result) -> dict:
    """Build a single agent metadata entry.

    Only operational fields are copied -- never ``output`` or ``error`` text,
    which could contain raw prompt/diff content or secrets.
    """
    return {
        "name": result.agent,
        "vendor": result.vendor,
        "status": "ok" if result.ok else "failed",
        "duration_s": round(float(result.duration_s), 3),
        "error_code": result.error_code,
        # Number of attempts made (issue #30): >1 means a transient failure was
        # retried before this outcome.
        "attempts": int(getattr(result, "attempts", 1) or 1),
        # What this slot contributed, not merely whether the CLI exited 0 (#501).
        "review_status": review_status(result),
    }


def _rounds_executed(outcome: JuryOutcome) -> int:
    # Prefer the orchestrator's authoritative count (adaptive rounds, issue #40);
    # fall back to inferring it from the phases that produced output.
    recorded = getattr(outcome, "rounds_executed", None)
    if isinstance(recorded, int) and recorded >= 1:
        return recorded
    rounds = 1 if outcome.reviews else 0
    if outcome.debate:
        rounds += 1
    return rounds


def estimate_economics(results: list) -> dict:
    """Estimate token counts and USD dollar cost across all executed agent slots (issue #528).

    Uses conservative token heuristics (~4 chars/token from output + base context)
    and published per-vendor pricing tiers. Local models (Ollama, local) are computed
    at $0.00 (free offline).
    """
    vendor_rates_per_1m = {
        "local": 0.0,
        "ollama": 0.0,
        "deepseek": 0.27,
        "groq": 0.30,
        "moonshot": 0.50,
        "gemini": 1.25,
        "google": 1.25,
        "openai": 2.50,
        "codex": 2.50,
        "anthropic": 3.00,
        "claude": 3.00,
    }
    breakdown = []
    total_tokens = 0
    total_cost_usd = 0.0
    # bolt: Consolidate multiple metrics into a single-pass O(N) explicit loop
    local_free_slots = 0

    for r in results:
        agent = getattr(r, "agent", "unknown")
        vendor = (getattr(r, "vendor", "") or "").lower()
        output_len = len(getattr(r, "output", "") or "")
        # Heuristic: base prompt ~800 tokens + output tokens
        tokens_est = max(100, 800 + (output_len // 4)) if getattr(r, "ok", False) else 200

        # Match rate
        rate_per_1m = 2.0  # default generic rate
        for k, v in vendor_rates_per_1m.items():
            if k in vendor or k in agent.lower():
                rate_per_1m = v
                break

        cost_usd = (tokens_est / 1_000_000) * rate_per_1m
        is_local = rate_per_1m == 0.0

        total_tokens += tokens_est
        total_cost_usd += cost_usd
        if is_local:
            local_free_slots += 1

        breakdown.append(
            {
                "agent": agent,
                "vendor": getattr(r, "vendor", ""),
                "tokens_est": tokens_est,
                "cost_usd_est": round(cost_usd, 6),
                "is_local_free": is_local,
            }
        )

    return {
        "total_tokens_est": total_tokens,
        "total_cost_usd_est": round(total_cost_usd, 4),
        "local_free_slots": local_free_slots,
        "breakdown": breakdown,
    }


def build_run_metadata(
    outcome: JuryOutcome, config: JuryConfig, *, decision=None, vote=None
) -> dict:
    """Return a machine-readable metadata dict for a jury run.

    The dict is safe to serialize as JSON and contains no diff text, prompt
    text, agent output, or secrets.

    Per-agent entries reflect the review panel (round 1). Total wall-clock is
    summed across every phase (review, debate, verify, synthesis) so it captures
    the full run cost proxy even though debate/verify/synthesis are re-runs of
    panel agents rather than distinct participants.
    """
    # The panel is the set of round-1 participants; this is the canonical
    # per-agent view and avoids duplicating the chair across later phases.
    agents = [_agent_entry(r) for r in outcome.reviews]

    all_results = list(outcome.reviews) + list(outcome.debate)
    if outcome.synthesis is not None:
        all_results.append(outcome.synthesis)
    if outcome.verify is not None:
        all_results.append(outcome.verify)
    total_wall_clock_s = round(sum(float(r.duration_s) for r in all_results), 3)

    # Reproducibility signals (issue #41): the run seed and a stable hash of the
    # effective config let a run be reproduced/explained. The seed is whatever
    # the run was configured with (may be None when unseeded). The config hash
    # is a pure function of config, so it is stable across runs and over time.
    from .classification import classify
    from .config import config_hash

    # Execution / partial-result signals (issue #30) and adaptive-round signals
    # (issue #40). ``skipped`` lists agents whose CLI was unavailable so they
    # never ran; ``budget_exhausted`` flags a run that stopped early on the total
    # timeout; ``stop_reason`` explains why debate ran or stopped.
    skipped = [
        {"name": name, "reason": reason} for name, reason in getattr(outcome, "skipped", []) or []
    ]
    retried = [a["name"] for a in agents if a["attempts"] > 1]

    # Final-verdict mode (issue #220). ``decision`` is the effective mode (CLI
    # override else config); ``vote`` is the tally dict when voting, else None.
    decision = decision or config.decision
    vote_meta = None
    if vote is not None:
        vote_meta = {
            "verdict": vote.verdict,
            "tally": vote.tally,
            "ballots": [
                {"reviewer": b.reviewer, "vote": b.vote, "reason": b.reason} for b in vote.ballots
            ],
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "decision": decision,
        "vote": vote_meta,
        "agents": agents,
        # Configured vs effective panel size (issue #501): a slot that returned no
        # review is an abstention, not an approval, and must not inflate the panel.
        "panel": panel_accounting(outcome.reviews),
        "economics": estimate_economics(all_results),
        "rounds_executed": _rounds_executed(outcome),
        "from_cache": bool(getattr(outcome, "from_cache", False)),
        "stop_reason": getattr(outcome, "stop_reason", "") or "",
        "skipped": skipped,
        "retried": retried,
        "budget_exhausted": bool(getattr(outcome, "budget_exhausted", False)),
        "execution": {
            "total_timeout": config.total_timeout,
            "phase_timeout": config.phase_timeout,
            "retries": config.retries,
            "early_stop": config.early_stop,
            "max_rounds": config.effective_max_rounds,
        },
        "verify_enabled": bool(config.verify),
        "context_mode": outcome.context_mode,
        "redact_secrets": bool(outcome.redact_secrets),
        "redaction_count": outcome.redaction_count,
        "seed": config.seed,
        "config_hash": config_hash(config),
        # PR-level classification (issue #7): deterministic summary derived from
        # the structured findings + consensus groups. No diff text is included.
        "classification": classify(outcome),
        # Wall-clock is an approximate COST PROXY, not a dollar cost. No token
        # counts are available from the underlying CLIs.
        "total_wall_clock_s": total_wall_clock_s,
        "cost_signal": "wall-clock-proxy",
        "generated_at": datetime.now(UTC).isoformat(),
    }


# end
