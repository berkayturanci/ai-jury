"""Council orchestration: review -> debate -> synthesis.

The orchestrator owns the round structure and prompt assembly; adapters only run
their CLI. Rounds run agents concurrently (thread pool) because each call is an
independent, IO-bound subprocess.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import prompts
from .adapters import Adapter, AgentResult, make_adapter
from .config import CouncilConfig
from .findings import Finding, parse_findings


@dataclass
class CouncilOutcome:
    reviews: list[AgentResult]
    debate: list[AgentResult]
    synthesis: AgentResult | None
    chair: str
    findings: list[Finding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _run_phase(
    adapters: list[Adapter],
    prompt_for: dict[str, str],
    phase: str,
    parallel: bool,
) -> list[AgentResult]:
    def task(a: Adapter) -> AgentResult:
        return a.run(prompt_for[a.name], phase=phase)

    if parallel and len(adapters) > 1:
        with ThreadPoolExecutor(max_workers=len(adapters)) as pool:
            return list(pool.map(task, adapters))
    return [task(a) for a in adapters]


def _others(reviews: list[AgentResult], me: str) -> str:
    chunks = [
        f"### {r.agent} ({r.vendor})\n{r.output}"
        for r in reviews
        if r.agent != me and r.ok and r.output
    ]
    return "\n\n".join(chunks) if chunks else "_(no other reviews available)_"


def run_council(
    config: CouncilConfig,
    diff: str,
    *,
    context: str = "",
    mock: bool = False,
    strict: bool = False,
    log=lambda _msg: None,
) -> CouncilOutcome:
    specs = config.enabled_agents
    adapters = [make_adapter(s, mock=mock) for s in specs]

    # Filter to available agents (unless strict, where a missing CLI is fatal).
    usable: list[Adapter] = []
    for a in adapters:
        if a.available():
            usable.append(a)
        elif strict:
            raise RuntimeError(f"agent '{a.name}' CLI not available: {a.spec.command}")
        else:
            log(f"skipping '{a.name}': CLI not found ({a.spec.command})")
    if not usable:
        raise RuntimeError("no usable agents — install at least one agent CLI or use --mock")

    # Round 1: independent reviews.
    log(f"round 1: {len(usable)} agents reviewing")
    review_prompt = {
        a.name: prompts.REVIEW.format(name=a.name, context=context or "_(none)_", diff=diff)
        for a in usable
    }
    reviews = _run_phase(usable, review_prompt, "review", config.parallel)

    # Parse structured findings from each successful review and aggregate them.
    all_findings: list[Finding] = []
    all_warnings: list[str] = []
    for r in reviews:
        if not r.ok:
            continue
        found, warns = parse_findings(r.output, r.agent)
        r.findings = found
        r.warnings = warns
        all_findings.extend(found)
        all_warnings.extend(warns)

    # Round 2: debate (only agents whose round-1 succeeded participate).
    debate: list[AgentResult] = []
    if config.rounds >= 2:
        debaters = [a for a in usable if any(r.agent == a.name and r.ok for r in reviews)]
        if len(debaters) >= 2:
            log(f"round 2: {len(debaters)} agents cross-examining")
            own = {r.agent: r.output for r in reviews if r.ok}
            debate_prompt = {
                a.name: prompts.DEBATE.format(
                    name=a.name,
                    diff=diff,
                    own_review=own.get(a.name, "_(your review was unavailable)_"),
                    other_reviews=_others(reviews, a.name),
                )
                for a in debaters
            }
            debate = _run_phase(debaters, debate_prompt, "debate", config.parallel)
        else:
            log("round 2 skipped: need >=2 successful reviews to debate")

    # Synthesis: the chair consolidates.
    synthesis = _synthesize(config, usable, reviews, debate, diff, log)

    return CouncilOutcome(
        reviews=reviews,
        debate=debate,
        synthesis=synthesis,
        chair=_chair_name(config, usable),
        findings=all_findings,
        warnings=all_warnings,
    )


def _chair_name(config: CouncilConfig, usable: list[Adapter]) -> str:
    names = {a.name for a in usable}
    if config.chair in names:
        return config.chair
    return usable[0].name if usable else config.chair


def _synthesize(config, usable, reviews, debate, diff, log) -> AgentResult | None:
    chair_name = _chair_name(config, usable)
    chair = next((a for a in usable if a.name == chair_name), None)
    if chair is None:
        return None
    log(f"synthesis: chair '{chair_name}' consolidating verdict")
    reviews_txt = "\n\n".join(
        f"### {r.agent} ({r.vendor})\n{r.output}" for r in reviews if r.ok and r.output
    ) or "_(no reviews)_"
    debate_txt = "\n\n".join(
        f"### {r.agent}\n{r.output}" for r in debate if r.ok and r.output
    ) or "_(no debate round)_"
    prompt = prompts.SYNTHESIS.format(diff=diff, reviews=reviews_txt, debate=debate_txt)
    return chair.run(prompt, phase="synthesis")
