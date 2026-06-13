"""Jury orchestration: review -> debate -> synthesis.

The orchestrator owns the round structure and prompt assembly; adapters only run
their CLI. Rounds run agents concurrently (thread pool) because each call is an
independent, IO-bound subprocess.
"""

from __future__ import annotations

import random
import string
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace

from . import convergence, injection, largediff, prompts
from .adapters import RETRYABLE_ERROR_CODES, Adapter, AgentResult, make_adapter
from .config import JuryConfig
from .consensus import FindingGroup, group_findings
from .findings import Finding, Verdict, parse_findings, parse_verdicts
from .policy import ReviewPolicy, render_policy_section
from .privilege import audit_privilege
from .redaction import redact


class RunBudget:
    """Wall-clock budget for one jury run (issue #30).

    Tracks the elapsed time since construction and derives the timeout to pass a
    single agent call from the optional total-run and per-phase budgets. ``None``
    for either budget means uncapped; when both are unset ``call_timeout``
    returns ``None`` so adapters fall back to their own per-agent timeout and
    behaviour is identical to having no budget at all.
    """

    def __init__(self, total_timeout: int | None, phase_timeout: int | None):
        self.total = total_timeout
        self.phase = phase_timeout
        self._start = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self._start

    def remaining(self) -> float | None:
        if self.total is None:
            return None
        return max(0.0, self.total - self.elapsed())

    def expired(self) -> bool:
        return self.total is not None and self.elapsed() >= self.total

    def call_timeout(self) -> int | None:
        """Per-call timeout: the min of the phase budget and remaining total.

        The agent's own per-agent timeout is applied by the adapter (it takes the
        min with this value), so it is not needed here. Returns ``None`` when
        neither budget caps the call, leaving the adapter to use its configured
        per-agent timeout.
        """
        caps: list[float] = []
        if self.phase is not None:
            caps.append(float(self.phase))
        remaining = self.remaining()
        if remaining is not None:
            caps.append(remaining)
        if not caps:
            return None
        return max(1, int(min(caps)))


def _run_with_retry(
    adapter: Adapter,
    prompt: str,
    phase: str,
    budget: RunBudget,
    retries: int,
    log,
) -> AgentResult:
    """Run one agent for one phase, retrying transient failures (issue #30).

    Retries only failures whose typed error code is in
    ``RETRYABLE_ERROR_CODES`` (timeout/rate-limit/spawn), up to ``retries`` extra
    attempts. A deterministic failure (auth, missing CLI, empty output, generic
    nonzero exit) is returned immediately. The returned result's ``attempts``
    records how many tries were made. Retrying stops early when the run budget is
    exhausted so a retry never overruns the total timeout.
    """
    max_attempts = max(1, retries + 1)
    result = adapter.run(prompt, phase=phase, timeout=budget.call_timeout())
    attempts = 1
    while (
        not result.ok
        and result.error_code in RETRYABLE_ERROR_CODES
        and attempts < max_attempts
        and not budget.expired()
    ):
        log(f"{adapter.name}: {phase} attempt {attempts} failed ({result.error_code}); retrying")
        result = adapter.run(prompt, phase=phase, timeout=budget.call_timeout())
        attempts += 1
    result.attempts = attempts
    return result


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
class JuryOutcome:
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
    # Execution/partial-result signals (issue #30): agents skipped because their
    # CLI was unavailable (name, reason), and whether the run budget was
    # exhausted before all phases completed.
    skipped: list = field(default_factory=list)
    budget_exhausted: bool = False
    # Adaptive-rounds signals (issue #40): rounds actually executed and a short
    # human-readable reason for why the debate ran / stopped.
    rounds_executed: int = 1
    stop_reason: str = ""
    # Set when this outcome was served from the local result cache (issue #33),
    # so the report/metadata can mark it as cached rather than freshly computed.
    from_cache: bool = False


def _run_phase(
    adapters: list[Adapter],
    prompt_for: dict[str, str],
    phase: str,
    parallel: bool,
    *,
    budget: RunBudget,
    retries: int,
    log,
) -> list[AgentResult]:
    def task(a: Adapter) -> AgentResult:
        return _run_with_retry(a, prompt_for[a.name], phase, budget, retries, log)

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


def _debate_round(
    debaters: list[Adapter],
    reviews: list[AgentResult],
    diff: str,
    config: JuryConfig,
    run_rng: random.Random,
    agent_order: list[str],
    prior: list[AgentResult],
    budget: RunBudget,
    retries: int,
    log,
    round_no: int,
    template: str = prompts.DEBATE,
) -> list[AgentResult]:
    """Run one debate round and return its results in stable agent order.

    ``prior`` holds the previous round's debate outputs (empty for the first
    debate round); when present they are appended to each debater's prompt as a
    "prior debate" addendum so later rounds in an adaptive run (issue #40) build
    on, rather than repeat, earlier cross-examination. The peer-review anonymizing
    path (#37) is preserved unchanged.
    """
    log(f"round {round_no}: {len(debaters)} agents cross-examining")
    own = {r.agent: r.output for r in reviews if r.ok}
    # Prior-round debate output quotes attacker-controlled diff text, so it is
    # untrusted: neutralize sentinels (issue #316/L-1) before it is fenced and
    # appended below, matching every other peer-output slot.
    prior_txt = prompts.neutralize_sentinels(
        "\n\n".join(f"### {r.agent}\n{r.output}" for r in prior if r.ok and r.output)
    )
    debate_prompt: dict[str, str] = {}
    for a in debaters:
        if config.anonymize_debate:
            # Per-debater deterministic shuffle: derive a child RNG from the
            # shared run RNG so each debater gets an independent but reproducible
            # peer ordering (same seed -> same order).
            peer_rng = random.Random(run_rng.random())
            other_reviews, _label_map = _anonymize_peers(reviews, a.name, peer_rng)
        else:
            other_reviews = _others(reviews, a.name)
        text = template.format(
            name=a.name,
            diff=prompts.neutralize_sentinels(diff),
            own_review=prompts.neutralize_sentinels(
                own.get(a.name, "_(your review was unavailable)_")
            ),
            other_reviews=prompts.neutralize_sentinels(other_reviews),
            notice=prompts._UNTRUSTED_NOTICE,
        )
        if prior_txt:
            text += (
                "\n\n=== PRIOR DEBATE (earlier round) ===\n"
                "Build on this; do not just repeat it. Only keep a DISPUTE or "
                "MISSED item if it is still unresolved.\n\n"
                "<<<UNTRUSTED_REVIEW\n" + prior_txt + "\nUNTRUSTED_REVIEW>>>\n"
            )
        debate_prompt[a.name] = text
    results = _run_phase(
        debaters,
        debate_prompt,
        "debate",
        config.parallel,
        budget=budget,
        retries=retries,
        log=log,
    )
    # Same stable-ordering guarantee as round 1: independent of thread-pool
    # completion order.
    return _order_by_agents(results, agent_order)


def run_jury(
    config: JuryConfig,
    diff: str,
    *,
    context: str = "",
    mock: bool = False,
    strict: bool = False,
    seed: int | None = None,
    policy: ReviewPolicy | None = None,
    log=lambda _msg: None,
    budget: RunBudget | None = None,
    on_event=None,
    mode: str = "code",
) -> JuryOutcome:
    # Jury mode (issue #221): "code" (default) reviews a diff with the code-review
    # rubric; "issue" reviews a GitHub issue's prose for completeness/clarity.
    # Only the prompt TEMPLATES differ — the round structure, consensus, voting,
    # verification, ordering, and determinism are identical. ``tmpl`` selects the
    # four phase templates; each is threaded into the phase that uses it so the
    # call sites are otherwise unchanged.
    tmpl = prompts.for_mode(mode)
    # Live play-by-play hook (issue #210): an optional callback fired after each
    # phase result is produced — ``on_event(kind, result, round_no=None)`` with
    # kind in {"review", "debate", "verify", "synthesis"}. It lets a caller stream
    # the deliberation as it happens (CLI ``--live``) without the orchestrator
    # doing any I/O itself. Fired in stable per-phase order (not thread-completion
    # order) so the event sequence is deterministic. Defaults to a no-op.
    emit = on_event or (lambda *_a, **_k: None)
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
    # module so seeding a jury run never perturbs unrelated global state.
    # When the seed is None the RNG is unseeded (still deterministic
    # orchestration; randomness, if any, is just not reproducible run-to-run).
    # LLM output itself is never made deterministic by this — only the
    # orchestration around it. ``run_rng`` is the shared run RNG: pass it to
    # any feature that needs reproducible randomness instead of using ``random``.
    run_seed = seed if seed is not None else config.seed
    run_rng = random.Random(run_seed)  # shared run RNG (see docstring)

    # Run budget (issue #30): a single wall-clock budget threaded through every
    # phase. Defaults (both None) leave behaviour identical to no budget, with
    # each agent bounded only by its own per-agent timeout. ``retries`` is the
    # number of extra attempts for transient (retryable) failures. A caller may
    # pass a SHARED budget so ``total_timeout`` spans a whole chunked review
    # rather than resetting per chunk (issue #31 / review finding).
    if budget is None:
        budget = RunBudget(config.total_timeout, config.phase_timeout)
    retries = config.retries

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
            "least-privilege check failed (--strict): " + "; ".join(privilege_warnings)
        )

    specs = config.enabled_agents
    adapters = [make_adapter(s, mock=mock) for s in specs]

    # Filter to available agents (unless strict, where a missing CLI is fatal).
    # Skipped agents are recorded (name, reason) so the report can state exactly
    # which agents never ran — part of the partial-result policy (issue #30).
    usable: list[Adapter] = []
    skipped: list[tuple[str, str]] = []
    for a in adapters:
        if a.available():
            usable.append(a)
        elif strict:
            raise RuntimeError(f"agent '{a.name}' CLI not available: {a.spec.command}")
        else:
            reason = f"CLI not found ({a.spec.command})"
            log(f"skipping '{a.name}': {reason}")
            skipped.append((a.name, reason))
    if not usable:
        raise RuntimeError("no usable agents — install at least one agent CLI or use --mock")

    usable_names = [a.name for a in usable]

    # Round 1: independent reviews.
    log(f"round 1: {len(usable)} agents reviewing")
    review_prompt = {
        a.name: tmpl["review"].format(
            name=a.name,
            context=prompts.neutralize_sentinels(context or "_(none)_"),
            diff=prompts.neutralize_sentinels(diff),
            policy=policy_section,
            notice=prompts._UNTRUSTED_NOTICE,
        )
        for a in usable
    }
    reviews = _run_phase(
        usable,
        review_prompt,
        "review",
        config.parallel,
        budget=budget,
        retries=retries,
        log=log,
    )
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

    # Stream round-1 reviews as they're now finalized (stable order).
    for r in reviews:
        emit("review", r)

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

    # Round 2+: debate. Only agents whose round-1 review succeeded participate.
    # Two modes (issue #40):
    #   - fixed (early_stop = false): honour ``rounds`` exactly — run one debate
    #     round iff rounds >= 2. Reproducible fixed-N behaviour for benchmarking.
    #   - adaptive (early_stop = true): skip the debate when round-1 reviewers
    #     already agree, otherwise run debate up to ``max_rounds`` rounds and stop
    #     as soon as a round resolves all disputes.
    debate: list[AgentResult] = []
    rounds_executed = 1
    stop_reason = ""
    budget_exhausted = False
    debaters = [a for a in usable if any(r.agent == a.name and r.ok for r in reviews)]
    can_debate = len(debaters) >= 2

    if config.early_stop:
        max_rounds = config.effective_max_rounds
        if not can_debate:
            stop_reason = "stopped after round 1: need >=2 successful reviews to debate"
            log(stop_reason)
        elif max_rounds < 2:
            stop_reason = "stopped after round 1: max_rounds < 2"
            log(stop_reason)
        else:
            converged, why = convergence.review_convergence(groups, len(reviews))
            if converged:
                stop_reason = f"early stop after round 1: {why}"
                log(stop_reason)
            else:
                log(f"early stop active: {why}; running debate up to {max_rounds} round(s)")
                prior: list[AgentResult] = []
                round_no = 1
                while round_no < max_rounds:
                    if budget.expired():
                        budget_exhausted = True
                        stop_reason = f"stopped at round {rounds_executed}: run budget exhausted"
                        log(stop_reason)
                        break
                    round_no += 1
                    debate = _debate_round(
                        debaters,
                        reviews,
                        diff,
                        config,
                        run_rng,
                        agent_order,
                        prior,
                        budget,
                        retries,
                        log,
                        round_no,
                        template=tmpl["debate"],
                    )
                    rounds_executed = round_no
                    for r in debate:
                        emit("debate", r, round_no)
                    dconv, dwhy = convergence.debate_convergence(debate)
                    if dconv:
                        stop_reason = f"converged after round {round_no}: {dwhy}"
                        log(stop_reason)
                        break
                    prior = debate
                    stop_reason = f"ran {round_no} rounds: {dwhy}"
                else:
                    stop_reason = stop_reason or (
                        f"reached max_rounds ({max_rounds}) with disagreement remaining"
                    )
    else:
        # Fixed-N: exactly the historical behaviour.
        if config.rounds >= 2 and can_debate:
            if budget.expired():
                budget_exhausted = True
                stop_reason = "round 2 skipped: run budget exhausted"
                log(stop_reason)
            else:
                debate = _debate_round(
                    debaters,
                    reviews,
                    diff,
                    config,
                    run_rng,
                    agent_order,
                    [],
                    budget,
                    retries,
                    log,
                    2,
                    template=tmpl["debate"],
                )
                rounds_executed = 2
                for r in debate:
                    emit("debate", r, 2)
        elif config.rounds >= 2:
            stop_reason = "round 2 skipped: need >=2 successful reviews to debate"
            log(stop_reason)
        else:
            stop_reason = "single round (rounds = 1)"

    # Verification: the chair judges candidate findings to reduce false
    # positives. Skipped when the run budget is exhausted (issue #30) so a
    # partial run still returns what completed instead of overrunning.
    verify_result: AgentResult | None = None
    verdicts: list[Verdict] = []
    if config.verify:
        if budget.expired():
            budget_exhausted = True
            msg = "verification skipped: run budget exhausted"
            log(msg)
            all_warnings.append(msg)
        else:
            verify_result, verdicts, verify_warnings = _verify(
                chair_name,
                usable,
                all_findings,
                diff,
                context,
                budget,
                retries,
                log,
                template=tmpl["verify"],
            )
            all_warnings.extend(verify_warnings)
            _apply_verdicts(groups, verdicts)
            if verify_result is not None:
                emit("verify", verify_result)

    # Synthesis: the chair consolidates. When the resolved chair is ALSO a
    # round-1 reviewer, feed it an anonymized view of the reviews (#38 guardrail)
    # so it cannot preferentially weight its own findings; the report still
    # attributes by real name because it renders the real outcome data, not this
    # synthesis prompt.
    synthesis: AgentResult | None = None
    if budget.expired():
        budget_exhausted = True
        msg = "synthesis skipped: run budget exhausted"
        log(msg)
        if msg not in all_warnings:
            all_warnings.append(msg)
    else:
        chair_is_reviewer = chair_name in reviewer_names
        anonymize_synthesis = config.anonymize_debate and chair_is_reviewer
        synthesis = _synthesize(
            chair_name,
            usable,
            reviews,
            debate,
            diff,
            budget,
            retries,
            log,
            verdicts=verdicts,
            anonymize_reviews=anonymize_synthesis,
            rng=run_rng,
            template=tmpl["synthesis"],
        )
        if synthesis is not None:
            emit("synthesis", synthesis)

    return JuryOutcome(
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
        skipped=skipped,
        budget_exhausted=budget_exhausted,
        rounds_executed=rounds_executed,
        stop_reason=stop_reason,
    )


def resolve_chair(
    config: JuryConfig,
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
    """Render candidate findings for the chair's verification prompt.

    Reviewer identity is omitted (#250) so the chair can't favour its own
    findings while judging them — parity with the #37/#38 anonymization.
    Verdicts match back by file/line/claim, so dropping it is safe.
    """
    if not findings:
        return "_(no candidate findings)_"
    lines = []
    for f in findings:
        loc = f.file or "?"
        if f.line is not None:
            loc = f"{loc}:{f.line}"
        lines.append(f"- [{f.severity}] {loc} — {f.claim}")
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
    chair_name,
    usable,
    findings,
    diff,
    context,
    budget,
    retries,
    log,
    template=prompts.VERIFY,
) -> tuple[AgentResult | None, list[Verdict], list[str]]:
    chair = next((a for a in usable if a.name == chair_name), None)
    if chair is None:
        return None, [], []
    log(f"verification: chair '{chair_name}' judging {len(findings)} candidate findings")
    prompt = template.format(
        diff=prompts.neutralize_sentinels(diff),
        findings=prompts.neutralize_sentinels(_format_findings_for_verify(findings)),
        context=prompts.neutralize_sentinels(context or "_(none)_"),
        notice=prompts._UNTRUSTED_NOTICE,
    )
    result = _run_with_retry(chair, prompt, "verify", budget, retries, log)
    if not result.ok:
        return result, [], [f"verification failed: {result.error}"]
    verdicts, warnings = parse_verdicts(result.output, chair_name)
    return result, verdicts, warnings


def _verdict_matches_group(verdict: Verdict, group: FindingGroup) -> bool:
    from .consensus import _normalize_claim, _normalize_path

    rep = group.representative
    # Case-EXACT path match (fold_case=False): on a case-sensitive filesystem
    # ``Config.py`` != ``config.py``, so a verdict must not reject a finding it
    # only case-collapses onto (audit 2026-06-13 r6/M).
    if _normalize_path(verdict.file, fold_case=False) != _normalize_path(
        rep.file, fold_case=False
    ):
        return False
    if verdict.line is not None and rep.line is not None and abs(verdict.line - rep.line) > 3:
        return False
    v_claim = _normalize_claim(verdict.claim)
    r_claim = _normalize_claim(rep.claim)
    if not v_claim:
        # An empty verdict claim is allowed to match the finding *at this
        # location* (the verifier may omit the claim and refer to it by
        # position). But a verdict with NEITHER a claim NOR a line has no
        # location precision at all: it would otherwise match — and, when
        # ``unsupported``, REJECT — every finding group in the file, including
        # unrelated criticals, flipping the CI gate from FAIL to PASS. Such a
        # claim-less, line-less verdict is a file-wide wildcard and must not
        # match (security audit 2026-06-13 r6/M). Require a concrete line that
        # actually pins the finding before honoring an empty-claim match.
        return verdict.line is not None and rep.line is not None
    if v_claim == r_claim:
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
    budget,
    retries,
    log,
    verdicts=None,
    anonymize_reviews=False,
    rng=None,
    template=prompts.SYNTHESIS,
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
        reviews_txt = (
            "\n\n".join(
                f"### {r.agent} ({r.vendor})\n{r.output}" for r in reviews if r.ok and r.output
            )
            or "_(no reviews)_"
        )
    debate_txt = (
        "\n\n".join(f"### {r.agent}\n{r.output}" for r in debate if r.ok and r.output)
        or "_(no debate round)_"
    )
    prompt = template.format(
        diff=prompts.neutralize_sentinels(diff),
        reviews=prompts.neutralize_sentinels(reviews_txt),
        debate=prompts.neutralize_sentinels(debate_txt),
        notice=prompts._UNTRUSTED_NOTICE,
    )
    if verdicts:
        # The verdicts quote candidate findings, which transitively quote
        # untrusted diff text (issue v1.5.0/M-1: this addendum was the one slot
        # the #316/L-1 fix missed). Fence + neutralize it like every other
        # peer-output slot so an embedded closing token can't break out.
        prompt += (
            "\n\n=== VERIFICATION VERDICTS (may quote UNTRUSTED text) ===\n"
            "<<<UNTRUSTED_FINDINGS\n"
            + prompts.neutralize_sentinels(_format_verdicts(verdicts))
            + "\nUNTRUSTED_FINDINGS>>>\n"
        )
    return _run_with_retry(chair, prompt, "synthesis", budget, retries, log)


def _merge_results_by_agent(phase_lists: list[list[AgentResult]]) -> list[AgentResult]:
    """Merge per-chunk results for the same agent into one result (issue #31).

    Outputs are concatenated under per-chunk headers, durations summed, ``ok`` is
    true if the agent succeeded on any chunk, and ``attempts`` keeps the max so a
    retried chunk is still visible. Agent order follows first appearance.
    """
    order: list[str] = []
    by_agent: dict[str, list[AgentResult]] = {}
    for lst in phase_lists:
        for r in lst:
            if r.agent not in by_agent:
                by_agent[r.agent] = []
                order.append(r.agent)
            by_agent[r.agent].append(r)

    merged: list[AgentResult] = []
    for name in order:
        parts = by_agent[name]
        ok = any(p.ok for p in parts)
        body = "\n\n".join(
            f"#### chunk {i}\n{p.output}" for i, p in enumerate(parts, 1) if p.ok and p.output
        )
        first_err = next((p for p in parts if not p.ok), None)
        merged.append(
            AgentResult(
                name,
                parts[0].vendor,
                ok,
                body,
                round(sum(p.duration_s for p in parts), 3),
                error=None if ok else (first_err.error if first_err else None),
                error_code=None if ok else (first_err.error_code if first_err else None),
                attempts=max(p.attempts for p in parts),
            )
        )
    return merged


def _combine_chair_results(results: list[AgentResult], chair: str) -> AgentResult | None:
    """Combine per-chunk chair results (verify/synthesis) into one labelled result."""
    ok_parts = [r for r in results if r.ok and r.output]
    if not ok_parts:
        return results[0] if results else None
    vendor = ok_parts[0].vendor
    body = "\n\n".join(f"### chunk {i}\n{r.output}" for i, r in enumerate(ok_parts, 1))
    return AgentResult(chair, vendor, True, body, round(sum(r.duration_s for r in ok_parts), 3))


def _merge_chunk_outcomes(outcomes: list[JuryOutcome]) -> JuryOutcome:
    """Fold per-chunk outcomes (issue #31) into one renderable JuryOutcome.

    Findings are unioned and re-grouped across all chunks so the consensus view
    is global; verdicts are re-applied to the merged groups. Review/debate/chair
    outputs are merged per agent with chunk labels so the report stays coherent.
    """
    if len(outcomes) == 1:
        return outcomes[0]
    base = outcomes[0]

    reviews = _merge_results_by_agent([o.reviews for o in outcomes])
    debate = (
        _merge_results_by_agent([o.debate for o in outcomes])
        if any(o.debate for o in outcomes)
        else []
    )
    findings = [f for o in outcomes for f in o.findings]
    groups = group_findings(findings, len(reviews))
    verdicts = [v for o in outcomes for v in o.verdicts]
    _apply_verdicts(groups, verdicts)
    warnings = [w for o in outcomes for w in o.warnings]

    synthesis = _combine_chair_results([o.synthesis for o in outcomes if o.synthesis], base.chair)
    verify = _combine_chair_results([o.verify for o in outcomes if o.verify], base.chair)

    return JuryOutcome(
        reviews=reviews,
        debate=debate,
        synthesis=synthesis,
        chair=base.chair,
        findings=findings,
        warnings=warnings,
        groups=groups,
        verify=verify,
        verdicts=verdicts,
        context_mode=base.context_mode,
        redact_secrets=base.redact_secrets,
        redaction_count=sum(o.redaction_count for o in outcomes),
        injection_hits=[h for o in outcomes for h in o.injection_hits],
        skipped=base.skipped,
        budget_exhausted=any(o.budget_exhausted for o in outcomes),
        rounds_executed=max(o.rounds_executed for o in outcomes),
        stop_reason=f"chunked review across {len(outcomes)} part(s)",
    )


def review_diff(
    config: JuryConfig,
    diff: str,
    *,
    context: str = "",
    mock: bool = False,
    strict: bool = False,
    seed: int | None = None,
    policy: ReviewPolicy | None = None,
    log=lambda _msg: None,
    on_event=None,
) -> tuple[JuryOutcome, largediff.DiffPlan]:
    """Plan a diff (filter + size + mode) then run the jury (issue #31).

    The single entry point the CLI uses: it measures and filters the diff,
    reports the size and the selected handling mode, and dispatches:

    - ``full``      — review the filtered diff in one ``run_jury`` pass;
    - ``chunked``   — review each chunk and merge the outcomes;
    - ``too_large`` — raise ``RuntimeError`` with an actionable message.

    Returns ``(outcome, plan)`` so the caller can surface the plan. Existing
    callers of :func:`run_jury` are unaffected.
    """
    dc = config.diff
    plan = largediff.plan_diff(
        diff,
        max_bytes=dc.max_bytes,
        chunk=dc.chunk,
        chunk_max_bytes=dc.chunk_max_bytes,
        exclude_generated=dc.exclude_generated,
        exclude=dc.exclude,
        include=dc.include,
    )
    log(
        f"diff size: {plan.total_bytes} B total, {plan.kept_bytes} B after filters "
        f"({len(plan.kept)} file(s) kept, {len(plan.excluded)} excluded); "
        f"mode: {plan.mode}"
    )
    if plan.excluded:
        log("excluded: " + ", ".join(f"{p} [{why}]" for p, why in plan.excluded))
    log(plan.reason)

    if plan.mode == largediff.MODE_TOO_LARGE:
        raise RuntimeError(f"diff too large to review: {plan.reason}")
    if not plan.chunks:
        raise RuntimeError(
            "nothing to review after filters — all files were excluded "
            "(check [jury.diff] include/exclude patterns)"
        )

    # One shared budget across all chunks so ``total_timeout`` bounds the WHOLE
    # review, not each chunk independently (review finding). ``phase_timeout`` and
    # per-agent timeouts still apply per call via the same budget.
    shared_budget = RunBudget(config.total_timeout, config.phase_timeout)

    # Redact the shared context ONCE here, before fan-out (#249). The same context
    # is reviewed against every chunk; letting each per-chunk ``run_jury`` redact
    # it would count its secrets once per chunk and ``_merge_chunk_outcomes`` would
    # sum them, inflating ``redaction_count`` (e.g. a 1-secret context over 8
    # chunks reported 8). Pre-redacting makes each chunk's re-redaction a no-op —
    # the ``[REDACTED:…]`` placeholders no longer match — so we add the one-time
    # context count back at the end. Diff/chunk redactions are still counted
    # per chunk and summed, which is correct (each chunk's diff is distinct).
    # `config.context` is the right path: `_from_dict` flattens the `[jury]`
    # table onto JuryConfig, so the `[jury.context]` sub-table is `config.context`
    # (a ContextConfig), NOT `config.jury.context` — there is no `config.jury`.
    # This mirrors how run_jury() reads it.
    ctx_cfg = getattr(config, "context", None)
    ctx_mode = getattr(ctx_cfg, "mode", "diff-only") if ctx_cfg else "diff-only"
    redact_on = getattr(ctx_cfg, "redact_secrets", True) if ctx_cfg else True
    context_redactions = 0
    if redact_on and ctx_mode != "diff-only" and context:
        context, context_redactions = redact(context)

    def _run(chunk: str) -> JuryOutcome:
        return run_jury(
            config,
            chunk,
            context=context,
            mock=mock,
            strict=strict,
            seed=seed,
            policy=policy,
            log=log,
            budget=shared_budget,
            on_event=on_event,
        )

    def _finalize(outcome: JuryOutcome) -> JuryOutcome:
        # Add the one-time context redaction count (per-chunk runs saw an already-
        # redacted context and counted 0 for it).
        if not context_redactions:
            return outcome
        return replace(outcome, redaction_count=outcome.redaction_count + context_redactions)

    if plan.mode == largediff.MODE_FULL:
        return _finalize(_run(plan.chunks[0])), plan

    outcomes = []
    for i, chunk in enumerate(plan.chunks, 1):
        log(f"reviewing chunk {i}/{len(plan.chunks)}")
        outcomes.append(_run(chunk))
    return _finalize(_merge_chunk_outcomes(outcomes)), plan
