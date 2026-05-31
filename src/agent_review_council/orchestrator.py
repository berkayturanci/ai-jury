"""Council orchestration: review -> debate -> synthesis.

The orchestrator owns the round structure and prompt assembly; adapters only run
their CLI. Rounds run agents concurrently (thread pool) because each call is an
independent, IO-bound subprocess.
"""
from __future__ import annotations

import random
import string
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import injection, prompts
from .adapters import Adapter, AgentResult, make_adapter
from .config import CouncilConfig
from .policy import ReviewPolicy, render_policy_section
from .consensus import FindingGroup, group_findings
from .findings import Finding, Verdict, parse_findings, parse_verdicts
from .privilege import audit_privilege
from .redaction import redact


def _order_by_agents(results: list[AgentResult], order: list[str]) -> list[AgentResult]:
    """Reorder phase results into the configured/enabled agent order.

    Round phases run agents concurrently (ThreadPoolExecutor.map), so the order
    in which results arrive is not guaranteed across runs. The report and all
    downstream consumers must NOT depend on thread-completion order, so we sort
    every phase's results by each agent's index in ``order`` (the stable
    enabled-agent list). Agents not present in ``order`` (should not happen)
    sort to the end, preserving their relative arrival order as a stable
    tiebreak so the sort is total and deterministic.
    """
    index = {name: i for i, name in enumerate(order)}
    fallback = len(order)
    return sorted(results, key=lambda r: index.get(r.agent, fallback))


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
    injection_hits: list = field(default_factory=list)


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
    """Identity-labeled peer reviews (legacy path; ``anonymize_debate = false``).

    Renders each *other* reviewer's round-1 output with its real agent/vendor
    identity in the stable enabled-agent order. This is the pre-#37 behaviour and
    leaks both identity and position; the anonymizing path below is the default.
    """
    chunks = [
        f"### {r.agent} ({r.vendor})\n{r.output}"
        for r in reviews
        if r.agent != me and r.ok and r.output
    ]
    return "\n\n".join(chunks) if chunks else "_(no other reviews available)_"


def _anon_label(i: int) -> str:
    """Stable anonymous reviewer label: 0->'A', 1->'B', ... 26->'AA'."""
    letters = string.ascii_uppercase
    label = ""
    i += 1
    while i > 0:
        i, rem = divmod(i - 1, 26)
        label = letters[rem] + label
    return label


def _anonymize_peers(
    reviews: list[AgentResult], me: str, rng: random.Random
) -> tuple[str, dict[str, str]]:
    """Chatham House peer view for a debater (#37).

    Returns ``(prompt_text, label_to_agent)`` where the prompt text renders each
    *other* successful reviewer's round-1 output under an anonymous
    ``### Reviewer A`` / ``### Reviewer B`` heading — NO vendor or agent name.
    The debater's OWN review is excluded (it is passed separately as
    ``own_review``). Presentation order is shuffled DETERMINISTICALLY using the
    shared run RNG so neither identity nor position is a stable signal; the same
    seed yields the same order, different seeds may differ.

    ``label_to_agent`` keeps the anonymous-label -> real-agent mapping internal so
    callers can still recover authorship (the report attributes by real name).
    """
    peers = [r for r in reviews if r.agent != me and r.ok and r.output]
    if not peers:
        return "_(no other reviews available)_", {}
    # Deterministic per-debater shuffle from the shared run RNG. We shuffle a
    # copy so the caller's review list (used elsewhere) is untouched.
    order = list(peers)
    rng.shuffle(order)
    chunks: list[str] = []
    label_to_agent: dict[str, str] = {}
    for i, r in enumerate(order):
        label = f"Reviewer {_anon_label(i)}"
        label_to_agent[label] = r.agent
        chunks.append(f"### {label}\n{r.output}")
    return "\n\n".join(chunks), label_to_agent


def run_council(
    config: CouncilConfig,
    diff: str,
    *,
    context: str = "",
    mock: bool = False,
    strict: bool = False,
    seed: int | None = None,
    policy: ReviewPolicy | None = None,
    log=lambda _msg: None,
) -> CouncilOutcome:
    # Repository review policy (optional, #8): maintainer-authored, TRUSTED
    # content rendered into each REVIEW prompt in a clearly separated section.
    # When ``policy`` is None a sentinel placeholder is used, so the prompt is
    # unchanged except for that section. The policy is distinct from the
    # agent-runtime ``config`` and never enters the untrusted diff/context fences.
    policy_section = render_policy_section(policy)
    # Run reproducibility: a single shared RNG seeds every randomized
    # orchestration decision (future: anonymized-rebuttal order, rotating
    # chair, tie-breaks). The seed comes from the explicit ``seed`` argument if
    # given, else from ``config.seed``. We construct a dedicated
    # ``random.Random`` instance rather than touching the global ``random``
    # module so seeding a council run never perturbs unrelated global state.
    # When the seed is None the RNG is unseeded (still deterministic
    # orchestration; randomness, if any, is just not reproducible run-to-run).
    # LLM output itself is never made deterministic by this — only the
    # orchestration around it. ``run_rng`` is the shared run RNG: pass it to
    # any feature that needs reproducible randomness instead of using ``random``.
    run_seed = seed if seed is not None else config.seed
    run_rng = random.Random(run_seed)  # shared run RNG (see docstring)

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

    # Prompt-injection heuristic (OWASP LLM01): scan untrusted diff/context for
    # patterns that try to override instructions, then SURFACE them as a synthetic
    # finding/warning. We never act on them; the CI gate is derived from
    # structured consensus (see ci.evaluate_ci), so an injected "APPROVE"
    # cannot flip the verdict.
    injection_hits = injection.scan_inputs(diff, context)
    injection_findings: list[Finding] = []
    if injection_hits:
        log(f"prompt-injection heuristic: {len(injection_hits)} suspicious pattern(s) flagged")
        syn = injection.hits_to_finding(injection_hits)
        if syn is not None:
            injection_findings.append(syn)

    # Least-privilege audit: warn when a configured agent could perform
    # write/tool actions while reviewing attacker-controlled content.
    privilege_warnings = audit_privilege(config.enabled_agents)
    for w in privilege_warnings:
        log(f"least-privilege warning: {w}")
    if strict and privilege_warnings:
        raise RuntimeError(
            "least-privilege check failed (--strict): "
            + "; ".join(privilege_warnings)
        )

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

    usable_names = [a.name for a in usable]

    # Round 1: independent reviews.
    log(f"round 1: {len(usable)} agents reviewing")
    review_prompt = {
        a.name: prompts.REVIEW.format(
            name=a.name,
            context=context or "_(none)_",
            diff=diff,
            policy=policy_section,
            notice=prompts._UNTRUSTED_NOTICE,
        )
        for a in usable
    }
    reviews = _run_phase(usable, review_prompt, "review", config.parallel)
    # Stable ordering: the thread pool can return results in any completion
    # order. Reorder to the enabled-agent order so the report (and every
    # downstream consumer) is independent of which thread finished first.
    agent_order = [a.name for a in usable]
    reviews = _order_by_agents(reviews, agent_order)

    # Parse structured findings from each successful review and aggregate them.
    # Seed with the synthetic injection finding/warnings so they surface in the
    # report and outcome.warnings without ever influencing agent behaviour.
    all_findings: list[Finding] = list(injection_findings)
    all_warnings: list[str] = injection.hits_to_warnings(injection_hits)
    all_warnings.extend(privilege_warnings)
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

    # Names of agents whose round-1 review succeeded — the chair resolver uses
    # this to (optionally) prefer a non-reviewer chair (#38).
    reviewer_names = [r.agent for r in reviews if r.ok]

    # Resolve the chair ONCE for the whole run so verify and synthesis use the
    # SAME chair. ``chair = "rotate"`` and prefer-non-reviewer both consume the
    # shared run RNG / reviewer info, so resolving once (rather than recomputing
    # per phase) is what keeps a rotating chair stable within a run (#38).
    chair_name = resolve_chair(config, usable_names, reviewer_names, run_rng)

    # Round 2: debate (only agents whose round-1 succeeded participate).
    debate: list[AgentResult] = []
    if config.rounds >= 2:
        debaters = [a for a in usable if any(r.agent == a.name and r.ok for r in reviews)]
        if len(debaters) >= 2:
            log(f"round 2: {len(debaters)} agents cross-examining")
            own = {r.agent: r.output for r in reviews if r.ok}
            debate_prompt = {}
            for a in debaters:
                if config.anonymize_debate:
                    # Per-debater deterministic shuffle: derive a child RNG from
                    # the shared run RNG so each debater gets an independent but
                    # reproducible peer ordering (same seed -> same order).
                    peer_rng = random.Random(run_rng.random())
                    other_reviews, _label_map = _anonymize_peers(reviews, a.name, peer_rng)
                else:
                    other_reviews = _others(reviews, a.name)
                debate_prompt[a.name] = prompts.DEBATE.format(
                    name=a.name,
                    diff=diff,
                    own_review=own.get(a.name, "_(your review was unavailable)_"),
                    other_reviews=other_reviews,
                    notice=prompts._UNTRUSTED_NOTICE,
                )
            debate = _run_phase(debaters, debate_prompt, "debate", config.parallel)
            # Same stable-ordering guarantee as round 1: independent of
            # thread-pool completion order.
            debate = _order_by_agents(debate, agent_order)
        else:
            log("round 2 skipped: need >=2 successful reviews to debate")

    # Verification: the chair judges candidate findings to reduce false positives.
    verify_result: AgentResult | None = None
    verdicts: list[Verdict] = []
    if config.verify:
        verify_result, verdicts, verify_warnings = _verify(
            chair_name, usable, all_findings, diff, context, log
        )
        all_warnings.extend(verify_warnings)
        _apply_verdicts(groups, verdicts)

    # Synthesis: the chair consolidates. When the resolved chair is ALSO a
    # round-1 reviewer, feed it an anonymized view of the reviews (#38 guardrail)
    # so it cannot preferentially weight its own findings; the report still
    # attributes by real name because it renders the real outcome data, not this
    # synthesis prompt.
    chair_is_reviewer = chair_name in reviewer_names
    anonymize_synthesis = config.anonymize_debate and chair_is_reviewer
    synthesis = _synthesize(
        chair_name,
        usable,
        reviews,
        debate,
        diff,
        log,
        verdicts=verdicts,
        anonymize_reviews=anonymize_synthesis,
        rng=run_rng,
    )

    return CouncilOutcome(
        reviews=reviews,
        debate=debate,
        synthesis=synthesis,
        chair=chair_name,
        findings=all_findings,
        warnings=all_warnings,
        groups=groups,
        verify=verify_result,
        verdicts=verdicts,
        context_mode=context_mode,
        redact_secrets=redact_on,
        redaction_count=redaction_count,
        injection_hits=injection_hits,
    )


def resolve_chair(
    config: CouncilConfig,
    usable: list[str],
    reviewers: list[str],
    rng: random.Random,
) -> str:
    """Resolve the chair for a run as a PURE function of its inputs (#38).

    Precedence:
      1. ``chair = "rotate"`` — pick deterministically from the usable agents
         using the shared run ``rng``. Same seed -> same chair; different seeds
         may differ. Falls back to the first usable agent when none are usable.
      2. An explicit ``config.chair`` that names a usable agent — honoured as-is
         (an operator-chosen chair always wins).
      3. ``prefer_non_reviewer_chair`` — when set and a usable agent that was NOT
         a successful round-1 reviewer exists, prefer the first such agent
         (neutral chair). This only applies when the configured chair is not
         itself a usable agent.
      4. Fallback to the first usable agent (legacy behaviour).

    Keeping this pure (no Adapter objects, no I/O) makes it directly
    unit-testable and guarantees ``_verify`` and ``_synthesize`` agree because
    the caller resolves it ONCE and threads the result through both.
    """
    if not usable:
        return config.chair
    names = set(usable)

    if config.chair == "rotate":
        # Deterministic rotation: sort for a stable candidate order independent
        # of dict/thread ordering, then index with the shared run RNG. Sorting
        # the candidate list (not iterating the set) makes the pick a pure
        # function of (seed, usable-name set): same seed + same agents -> same
        # chair, regardless of RNG-consumption order elsewhere.
        candidates = sorted(names)
        return candidates[rng.randrange(len(candidates))]

    if config.chair in names:
        return config.chair

    if config.prefer_non_reviewer_chair:
        reviewer_set = set(reviewers)
        non_reviewers = [n for n in usable if n not in reviewer_set]
        if non_reviewers:
            return non_reviewers[0]

    return usable[0]


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
    chair_name, usable, findings, diff, context, log
) -> tuple[AgentResult | None, list[Verdict], list[str]]:
    chair = next((a for a in usable if a.name == chair_name), None)
    if chair is None:
        return None, [], []
    log(f"verification: chair '{chair_name}' judging {len(findings)} candidate findings")
    prompt = prompts.VERIFY.format(
        diff=diff,
        findings=_format_findings_for_verify(findings),
        context=context or "_(none)_",
        notice=prompts._UNTRUSTED_NOTICE,
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


def _synthesize(
    chair_name,
    usable,
    reviews,
    debate,
    diff,
    log,
    verdicts=None,
    anonymize_reviews=False,
    rng=None,
) -> AgentResult | None:
    chair = next((a for a in usable if a.name == chair_name), None)
    if chair is None:
        return None
    log(f"synthesis: chair '{chair_name}' consolidating verdict")
    if anonymize_reviews:
        # Chair self-preference guardrail (#38): present round-1 reviews to the
        # chair under anonymous labels (no agent/vendor identity, no stable
        # order) so it cannot tell which review is "its own". Uses the shared run
        # RNG for deterministic-but-unstable ordering. ``me=None`` keeps ALL
        # reviews (we are not excluding a debater here, only stripping identity).
        peer_rng = random.Random(rng.random()) if rng is not None else random.Random()
        reviews_txt, _label_map = _anonymize_peers(reviews, None, peer_rng)
    else:
        reviews_txt = "\n\n".join(
            f"### {r.agent} ({r.vendor})\n{r.output}" for r in reviews if r.ok and r.output
        ) or "_(no reviews)_"
    debate_txt = "\n\n".join(
        f"### {r.agent}\n{r.output}" for r in debate if r.ok and r.output
    ) or "_(no debate round)_"
    prompt = prompts.SYNTHESIS.format(
        diff=diff,
        reviews=reviews_txt,
        debate=debate_txt,
        notice=prompts._UNTRUSTED_NOTICE,
    )
    if verdicts:
        prompt += f"\n\n=== VERIFICATION VERDICTS ===\n{_format_verdicts(verdicts)}\n"
    return chair.run(prompt, phase="synthesis")
