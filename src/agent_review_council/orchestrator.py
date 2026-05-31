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
from .consensus import FindingGroup, group_findings
from .findings import Finding, Verdict, parse_findings, parse_verdicts
from .redaction import redact


@dataclass
class CouncilOutcome:
    reviews: list[AgentResult]
    debate: list[AgentResult]
    synthesis: AgentResult | None
    chair: str
    findings: list[Finding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    groups: list[FindingGroup] = field(default_factory=list)
    verify: AgentResult | None = None
    verdicts: list[Verdict] = field(default_factory=list)
    context_mode: str = "diff-only"
    redact_secrets: bool = True
    redaction_count: int = 0


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
    # Context policy: diff-only sends only the diff; expanded includes context.
    ctx_cfg = getattr(config, "context", None)
    context_mode = getattr(ctx_cfg, "mode", "diff-only") if ctx_cfg else "diff-only"
    redact_on = getattr(ctx_cfg, "redact_secrets", True) if ctx_cfg else True
    if context_mode == "diff-only":
        context = ""
    redaction_count = 0
    if redact_on:
        diff, _n1 = redact(diff)
        context, _n2 = redact(context)
        redaction_count = _n1 + _n2
        if redaction_count:
            log(f"redacted {redaction_count} secret(s) before sending to agents")

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

    # Deterministic consensus grouping across reviewers.
    groups = group_findings(all_findings, len(reviews))

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

    # Verification: the chair judges candidate findings to reduce false positives.
    verify_result: AgentResult | None = None
    verdicts: list[Verdict] = []
    if config.verify:
        verify_result, verdicts, verify_warnings = _verify(
            config, usable, all_findings, diff, context, log
        )
        all_warnings.extend(verify_warnings)
        _apply_verdicts(groups, verdicts)

    # Synthesis: the chair consolidates.
    synthesis = _synthesize(config, usable, reviews, debate, diff, log, verdicts=verdicts)

    return CouncilOutcome(
        reviews=reviews,
        debate=debate,
        synthesis=synthesis,
        chair=_chair_name(config, usable),
        findings=all_findings,
        warnings=all_warnings,
        groups=groups,
        verify=verify_result,
        verdicts=verdicts,
        context_mode=context_mode,
        redact_secrets=redact_on,
        redaction_count=redaction_count,
    )


def _chair_name(config: CouncilConfig, usable: list[Adapter]) -> str:
    names = {a.name for a in usable}
    if config.chair in names:
        return config.chair
    return usable[0].name if usable else config.chair


def _format_findings_for_verify(findings: list[Finding]) -> str:
    if not findings:
        return "_(no candidate findings)_"
    lines = []
    for f in findings:
        loc = f.file or "?"
        if f.line is not None:
            loc = f"{loc}:{f.line}"
        lines.append(f"- [{f.severity}] {loc} — {f.claim} (by {f.reviewer})")
    return "\n".join(lines)


def _format_verdicts(verdicts: list[Verdict]) -> str:
    if not verdicts:
        return "_(no verification verdicts)_"
    lines = []
    for v in verdicts:
        loc = v.file or "?"
        if v.line is not None:
            loc = f"{loc}:{v.line}"
        lines.append(f"- [{v.status}] {loc} — {v.claim}: {v.reasoning}")
    return "\n".join(lines)


def _verify(
    config, usable, findings, diff, context, log
) -> tuple[AgentResult | None, list[Verdict], list[str]]:
    chair_name = _chair_name(config, usable)
    chair = next((a for a in usable if a.name == chair_name), None)
    if chair is None:
        return None, [], []
    log(f"verification: chair '{chair_name}' judging {len(findings)} candidate findings")
    prompt = prompts.VERIFY.format(
        diff=diff,
        findings=_format_findings_for_verify(findings),
        context=context or "_(none)_",
    )
    result = chair.run(prompt, phase="verify")
    if not result.ok:
        return result, [], [f"verification failed: {result.error}"]
    verdicts, warnings = parse_verdicts(result.output, chair_name)
    return result, verdicts, warnings


def _verdict_matches_group(verdict: Verdict, group: FindingGroup) -> bool:
    from .consensus import _normalize_claim, _normalize_path

    rep = group.representative
    if _normalize_path(verdict.file) != _normalize_path(rep.file):
        return False
    if verdict.line is not None and rep.line is not None and abs(verdict.line - rep.line) > 3:
        return False
    v_claim = _normalize_claim(verdict.claim)
    r_claim = _normalize_claim(rep.claim)
    if not v_claim or v_claim == r_claim:
        return True
    v_tokens, r_tokens = set(v_claim.split()), set(r_claim.split())
    if not v_tokens or not r_tokens:
        return False
    inter = len(v_tokens & r_tokens)
    union = len(v_tokens | r_tokens)
    return (inter / union if union else 0.0) >= 0.5


def _apply_verdicts(groups: list[FindingGroup], verdicts: list[Verdict]) -> None:
    """Attach verification statuses to consensus groups.

    unsupported -> bucket 'rejected'; needs_human_decision -> bucket 'disputed';
    verified -> status recorded, bucket unchanged.
    """
    for verdict in verdicts:
        # Apply to every matching group: when reviewers phrase the same issue
        # slightly differently it can land in more than one group, and all of
        # them should carry the verifier's judgement.
        for group in groups:
            if group.status:
                continue
            if _verdict_matches_group(verdict, group):
                group.status = verdict.status
                group.status_reasoning = verdict.reasoning
                if verdict.status == "unsupported":
                    group.bucket = "rejected"
                elif verdict.status == "needs_human_decision":
                    group.bucket = "disputed"


def _synthesize(config, usable, reviews, debate, diff, log, verdicts=None) -> AgentResult | None:
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
    if verdicts:
        prompt += f"\n\n=== VERIFICATION VERDICTS ===\n{_format_verdicts(verdicts)}\n"
    return chair.run(prompt, phase="synthesis")
