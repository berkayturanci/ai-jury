"""Run metadata and cost-awareness (wall-clock proxy) reporting.

Builds a machine-readable metadata dict describing a council run: which
agents participated, their per-agent status and wall-clock duration, how many
rounds ran, whether verification was enabled, and timestamps.

IMPORTANT: This metadata deliberately contains NO diff text, NO prompt text,
NO agent output, and NO secrets -- only structural/operational signals.

There are no token counts available from the underlying CLIs, so wall-clock
seconds are used as an approximate cost *proxy*, not a dollar cost.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .config import CouncilConfig
    from .orchestrator import CouncilOutcome

# v2 (issue #30/#40) added: stop_reason, skipped, retried, budget_exhausted,
# execution{...}, and per-agent ``attempts``.
SCHEMA_VERSION = 2


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
    }


def _rounds_executed(outcome: "CouncilOutcome") -> int:
    # Prefer the orchestrator's authoritative count (adaptive rounds, issue #40);
    # fall back to inferring it from the phases that produced output.
    recorded = getattr(outcome, "rounds_executed", None)
    if isinstance(recorded, int) and recorded >= 1:
        return recorded
    rounds = 1 if outcome.reviews else 0
    if outcome.debate:
        rounds += 1
    return rounds


def build_run_metadata(outcome: "CouncilOutcome", config: "CouncilConfig") -> dict:
    """Return a machine-readable metadata dict for a council run.

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
        {"name": name, "reason": reason}
        for name, reason in getattr(outcome, "skipped", []) or []
    ]
    retried = [a["name"] for a in agents if a["attempts"] > 1]

    return {
        "schema_version": SCHEMA_VERSION,
        "agents": agents,
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
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# end
