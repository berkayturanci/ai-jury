"""Render the jury run into a single markdown report."""

from __future__ import annotations

from . import classification as _classification
from .adapters import AgentResult
from .findings import SEVERITY_ORDER, Finding


def _block(title: str, body: str) -> str:
    return f"### {title}\n\n{body.strip() or '_(no output)_'}\n"


def _fail_status(r: AgentResult) -> str:
    """Failed-agent status line with a concise typed-error-code prefix."""
    prefix = f"[{r.error_code}] " if getattr(r, "error_code", None) else ""
    return f"⚠️ {prefix}{r.error}"


def _finding_line(f: Finding) -> str:
    loc = f.file or "?"
    if f.line is not None:
        loc = f"{loc}:{f.line}"
    return f"- [{f.severity}] {loc} — {f.claim} ({f.confidence}, by {f.reviewer})"


_BUCKET_LABELS = {
    "consensus": "Consensus (all reviewers)",
    "majority": "Majority",
    "single_reviewer": "Single reviewer",
    "disputed": "Disputed (needs human decision)",
    "rejected": "Rejected (unsupported by verifier)",
}
_BUCKET_ORDER = ["consensus", "majority", "single_reviewer", "disputed", "rejected"]

_STATUS_LABELS = {
    "verified": "verified",
    "unsupported": "unsupported",
    "needs_human_decision": "needs human decision",
}


def _group_line(g) -> str:
    f = g.representative
    loc = f.file or "?"
    if f.line is not None:
        loc = f"{loc}:{f.line}"
    reviewers = ", ".join(g.reviewers) if g.reviewers else "(unknown)"

    parts = [f"- [{g.severity}] {loc} — {f.claim} (reviewers: {reviewers})"]

    # Surface the reviewer's supporting evidence — the "why" behind the claim —
    # so the verdict is auditable, not just asserted (issue: evidence surfacing).
    if getattr(f, "evidence", ""):
        parts.append(f"\n  - _evidence:_ {f.evidence}")

    status = getattr(g, "status", "")
    if status:
        reasoning = getattr(g, "status_reasoning", "")
        if reasoning:
            parts.append(
                f"\n  - _verification:_ {_STATUS_LABELS.get(status, status)} — {reasoning}"
            )
        else:
            parts.append(f"\n  - _verification:_ {_STATUS_LABELS.get(status, status)}")

    if f.suggested_fix:
        parts.append(f"\n  - _fix:_ {f.suggested_fix}")

    return "".join(parts) if len(parts) > 1 else parts[0]


def _metadata_block(metadata: dict) -> list[str]:
    """Render the deterministic run-metadata section.

    Intentionally omits non-deterministic fields (e.g. ``generated_at``) so the
    Markdown report stays stable for snapshot tests. Per-agent durations are
    deterministic under mock (0s) and scrubbed by the golden test's duration
    normalizer otherwise. Wall-clock is labelled a cost proxy, not a dollar cost.
    """
    lines = ["## Run metadata\n"]
    lines.append(f"- rounds executed: {metadata['rounds_executed']}")
    # Adaptive-rounds explanation (issue #40): only shown when the orchestrator
    # recorded a reason, so a plain fixed-N run stays unchanged.
    if metadata.get("from_cache"):
        lines.append("- ♻️ served from local cache (not re-computed)")
    stop_reason = metadata.get("stop_reason")
    if stop_reason:
        lines.append(f"- rounds decision: {stop_reason}")
    lines.append(f"- verify: {'on' if metadata['verify_enabled'] else 'off'}")
    lines.append(f"- context mode: {metadata['context_mode']}")
    # Partial-result signals (issue #30): only rendered when relevant so a
    # complete, unbudgeted run is unaffected.
    if metadata.get("budget_exhausted"):
        lines.append("- ⚠️ run budget exhausted: some phases were skipped")
    skipped = metadata.get("skipped") or []
    if skipped:
        # bolt: CPython optimization — list comprehension inside join avoids generator overhead
        names = ", ".join([f"{s['name']} ({s['reason']})" for s in skipped])
        lines.append(f"- skipped agents (never ran): {names}")
    retried = metadata.get("retried") or []
    if retried:
        lines.append(f"- retried agents: {', '.join(retried)}")
    total = metadata["total_wall_clock_s"]
    lines.append(f"- total wall-clock (cost proxy, not $): {total:.0f}s")
    lines.append("")
    lines.append("| agent | vendor | status | duration |")
    lines.append("| --- | --- | --- | --- |")
    for a in metadata["agents"]:
        code = a.get("error_code")
        status = a["status"] if not code else f"{a['status']} ({code})"
        # Note a retried agent inline; attempts == 1 leaves the row unchanged.
        attempts = a.get("attempts", 1)
        if attempts and attempts > 1:
            status += f", {attempts} attempts"
        lines.append(f"| {a['name']} | {a['vendor']} | {status} | {a['duration_s']:.0f}s |")
    lines.append("")
    lines.append(
        "_Wall-clock seconds are an approximate cost proxy (no token counts are "
        "available from the CLIs), not a dollar cost._\n"
    )
    return lines


def _classification_block(classification: dict) -> list[str]:
    """Render the compact PR-level classification summary.

    Deterministic: ``classification`` is produced by the pure
    :mod:`ai_jury.classification` module, so the rendered section is
    stable for a deterministic run (and golden-tested under mock).
    """
    return [
        "## Classification\n",
        _classification.summary_line(classification),
        "",
    ]


def _consensus_block(groups) -> list[str]:
    lines = ["## Consensus\n"]
    by_bucket: dict[str, list] = {b: [] for b in _BUCKET_ORDER}
    for g in groups:
        by_bucket.setdefault(g.bucket, []).append(g)
    for bucket in _BUCKET_ORDER:
        bg = by_bucket.get(bucket) or []
        if not bg:
            continue
        lines.append(f"### {_BUCKET_LABELS.get(bucket, bucket)}\n")
        for g in bg:
            lines.append(_group_line(g))
        lines.append("")
    return lines


def _vote_block(vote) -> list[str]:
    """Render the panel-vote verdict + tally + per-reviewer ballots (issue #220).

    Vocabulary-agnostic: the tally renders whatever stances the vote carries
    (code: REQUEST CHANGES/COMMENT/APPROVE; issue: NEEDS-INFO/UNCLEAR/READY).
    """
    lines = ["## Verdict — panel vote\n"]
    # bolt: CPython optimization — list comprehension avoids generator expression overhead
    tally = " · ".join([f"{n} {label.lower()}" for label, n in vote.tally.items()])
    lines.append(f"**{vote.verdict}** — {tally}\n")
    for b in vote.ballots:
        lines.append(f"- `{b.reviewer}`: **{b.vote}** ({b.reason})")
    lines.append("")
    return lines


def _verdict_headline(synthesis, vote) -> str | None:
    """One-line verdict for the report's TL;DR callout (pure, deterministic).

    Prefers the panel vote's verdict when voting; otherwise lifts the opening
    ``## Verdict`` line out of the chair's synthesis prose — both the code and
    issue synthesis prompts mandate a ``## Verdict\\n<LABEL> — <one sentence>``
    first section, so the lift is reliable. The verdict sentence may wrap across
    lines; they are joined into one. Returns ``None`` when neither source is
    available (failed/absent synthesis, deviating output) so the caller simply
    omits the callout — it is purely additive, never replacing a section.
    """
    if vote is not None and getattr(vote, "verdict", None):
        return vote.verdict
    if synthesis is None or not getattr(synthesis, "ok", False):
        return None
    rows = (synthesis.output or "").splitlines()
    for i, row in enumerate(rows):
        if row.strip().lower().lstrip("#").strip() == "verdict":
            collected: list[str] = []
            for nxt in rows[i + 1 :]:
                if nxt.strip().startswith("#"):
                    break
                if not nxt.strip():
                    if collected:
                        break
                    continue
                collected.append(nxt.strip())
            return " ".join(collected) or None
    return None


def render(
    reviews: list[AgentResult],
    debate: list[AgentResult],
    synthesis: AgentResult | None,
    *,
    chair: str,
    findings: list[Finding] | None = None,
    warnings: list[str] | None = None,
    groups: list | None = None,
    verify: AgentResult | None = None,
    context_mode: str | None = None,
    redact_secrets: bool | None = None,
    redaction_count: int = 0,
    metadata: dict | None = None,
    classification: dict | None = None,
    review_scope: str | None = None,
    vote=None,
) -> str:
    findings = findings or []
    warnings = warnings or []
    groups = groups or []
    lines: list[str] = []
    lines.append("# 🏛️ AI Jury\n")

    # TL;DR callout (issue: scannable headline): hoist the verdict to the very
    # top so the outcome is the first thing a reader sees, before the panel and
    # the full report. Purely additive — omitted when no verdict is available.
    headline = _verdict_headline(synthesis, vote)
    if headline:
        lines.append(f"> ⚡ **TL;DR · {headline}**\n")

    # bolt: Explicit list materialization lets join evaluate iteratively in C
    panel = ", ".join([f"`{r.agent}` ({r.vendor})" for r in reviews])
    lines.append(f"**Panel:** {panel}\n")

    # Review-scope note (issue #9): only rendered when the caller supplies it
    # (incremental mode), so the default report is unchanged.
    if review_scope:
        lines.append(f"{review_scope}\n")

    # Compact, deterministic PR-level classification (issue #7). Derived from the
    # structured findings/groups when not supplied explicitly so the section
    # always renders for a normal run.
    if classification is None:
        classification = _classification.classify(findings=findings, groups=groups)
    lines.extend(_classification_block(classification))

    if context_mode is not None or redact_secrets is not None:
        lines.append("## Context policy\n")
        if context_mode is not None:
            lines.append(f"- context mode: {context_mode}")
        if redact_secrets is not None:
            state = "on" if redact_secrets else "off"
            extra = f" ({redaction_count} redacted)" if redact_secrets else ""
            lines.append(f"- secret redaction: {state}{extra}")
        lines.append("")

    if groups:
        lines.extend(_consensus_block(groups))
        lines.append("---\n")

    # Panel-vote verdict (issue #220): when voting, the tally is the headline
    # verdict and the chair's synthesis becomes supporting reasoning.
    if vote is not None:
        lines.extend(_vote_block(vote))
        lines.append("---\n")

    if verify is not None:
        lines.append("## Verification\n")
        lines.append(f"> Verified by `{chair}`\n")
        if verify.ok:
            lines.append(verify.output.strip() + "\n")
        else:
            lines.append(f"_Verification failed: {verify.error}_\n")
        lines.append("---\n")

    chair_heading = "Chair's reasoning" if vote is not None else "Chair verdict"
    if synthesis and synthesis.ok:
        lines.append(f"## {chair_heading}\n")
        lines.append(f"> Synthesized by `{chair}`\n")
        lines.append(synthesis.output.strip() + "\n")
    elif synthesis and not synthesis.ok:
        lines.append(f"## {chair_heading}\n")
        lines.append(f"_Synthesis failed: {synthesis.error}_\n")

    lines.append("---\n")
    lines.append("## Structured findings\n")
    if findings:
        # ``f.file``/``f.line`` may be None (a finding need not be located), so
        # coerce in the sort key — comparing None against str/int raises TypeError.
        ranked = sorted(
            findings,
            key=lambda f: (SEVERITY_ORDER.get(f.severity, 99), f.file or "", f.line or 0),
        )
        for f in ranked:
            lines.append(_finding_line(f))
        lines.append("")
    else:
        lines.append("_(no structured findings parsed)_\n")

    if warnings:
        lines.append("> ⚠️ agent output warnings\n")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    lines.append("## Round 1 — independent reviews\n")
    for r in reviews:
        status = f"{r.duration_s:.0f}s" if r.ok else _fail_status(r)
        lines.append(_block(f"`{r.agent}` ({r.vendor}) — {status}", r.output if r.ok else ""))

    if debate:
        lines.append("## Round 2 — cross-examination\n")
        for r in debate:
            status = f"{r.duration_s:.0f}s" if r.ok else _fail_status(r)
            lines.append(_block(f"`{r.agent}` — {status}", r.output if r.ok else ""))

    if metadata is not None:
        lines.append("---\n")
        lines.extend(_metadata_block(metadata))

    lines.append("---")
    lines.append(
        "\n<sub>Generated by "
        "[ai-jury](https://github.com/berkayturanci/ai-jury)"
        " — a cross-vendor multi-agent PR review jury.</sub>"
    )
    return "\n".join(lines)


_LIVE_LABELS = {
    "review": "Round 1 review",
    "debate": "Cross-examination",
    "verify": "Verification",
    "synthesis": "Decision — verdict & reasoning",
}


def render_live_step(
    kind: str, result: AgentResult, round_no: int | None = None
) -> tuple[str, str]:
    """Format one streamed step as ``(title, body)`` for live output (issue #210).

    Pure — no I/O. The CLI ``--live`` handler prints this to stdout and (with
    ``--pr``) posts it as its own comment, as each step completes. ``kind`` is one
    of review / debate / verify / synthesis."""
    label = _LIVE_LABELS.get(kind, kind)
    if kind == "debate" and round_no:
        label = f"Cross-examination · round {round_no}"
    if kind in ("verify", "synthesis"):
        who = f"chair `{result.agent}`"
    else:
        who = f"`{result.agent}` ({result.vendor})"
    status = f"{result.duration_s:.0f}s" if result.ok else _fail_status(result)
    title = f"🏛️ AI Jury — {label}: {who} — {status}"
    body = result.output.strip() if result.ok else ""
    return title, (body or "_(no output)_")


def _conversation_blocks(
    reviews: list[AgentResult],
    debate: list[AgentResult],
    synthesis: AgentResult | None,
    verify: AgentResult | None,
    *,
    chair: str,
) -> list[str]:
    """The chronological deliberation, foregrounded: each reviewer's raw output,
    then the debate exchanges in order, then verification, then the chair's
    decision *and its reasoning* — so a reader can follow who said what and why
    the chair ruled as it did (issue: full transcript)."""
    lines: list[str] = ["## Round 1 — independent reviews\n"]
    for r in reviews:
        status = f"{r.duration_s:.0f}s" if r.ok else _fail_status(r)
        lines.append(_block(f"`{r.agent}` ({r.vendor}) — {status}", r.output if r.ok else ""))
    if debate:
        lines.append("## Round 2 — cross-examination (debate)\n")
        for r in debate:
            status = f"{r.duration_s:.0f}s" if r.ok else _fail_status(r)
            lines.append(_block(f"`{r.agent}` — {status}", r.output if r.ok else ""))
    if verify is not None:
        lines.append("## Verification\n")
        lines.append(f"> Verified by `{chair}`\n")
        lines.append(
            verify.output.strip() + "\n"
            if verify.ok
            else f"_Verification failed: {verify.error}_\n"
        )
    lines.append("## Decision — verdict & reasoning\n")
    if synthesis and synthesis.ok:
        lines.append(f"> Decided by `{chair}`\n")
        lines.append(synthesis.output.strip() + "\n")
    elif synthesis and not synthesis.ok:
        lines.append(f"_Synthesis failed: {synthesis.error}_\n")
    else:
        lines.append("_(no synthesis produced)_\n")
    return lines


def _summary_blocks(
    findings: list[Finding],
    warnings: list[str],
    groups: list,
    classification: dict,
    vote=None,
) -> list[str]:
    """Consensus + structured-findings recap (the auditable at-a-glance summary)."""
    lines = list(_classification_block(classification))
    if vote is not None:
        lines.extend(_vote_block(vote))
    if groups:
        lines.extend(_consensus_block(groups))
    lines.append("## Structured findings\n")
    if findings:
        ranked = sorted(
            findings,
            key=lambda f: (SEVERITY_ORDER.get(f.severity, 99), f.file or "", f.line or 0),
        )
        # bolt: CPython optimization — list comprehension instead of generator expressions in extend()
        lines.extend([_finding_line(f) for f in ranked])
        lines.append("")
    else:
        lines.append("_(no structured findings parsed)_\n")
    if warnings:
        lines.append("> ⚠️ agent output warnings\n")
        lines.extend([f"- {w}" for w in warnings])
        lines.append("")
    return lines


def render_transcript(
    reviews: list[AgentResult],
    debate: list[AgentResult],
    synthesis: AgentResult | None,
    *,
    chair: str,
    findings: list[Finding] | None = None,
    warnings: list[str] | None = None,
    groups: list | None = None,
    verify: AgentResult | None = None,
    context_mode: str | None = None,
    redact_secrets: bool | None = None,
    redaction_count: int = 0,
    metadata: dict | None = None,
    classification: dict | None = None,
    review_scope: str | None = None,
    lead_with_summary: bool = False,
    vote=None,
) -> str:
    """Render the full play-by-play transcript (issue: full transcript / --verbose).

    Two layouts from one function:

    * ``lead_with_summary=False`` (``--transcript``) — a dedicated, conversation-first
      document: Round 1 → debate → verification → the chair's decision & reasoning,
      then a compact consensus/findings recap for auditability.
    * ``lead_with_summary=True`` (``--verbose``) — the consensus/verdict summary first,
      then the same full transcript below it, in one document.

    The default :func:`render` (consensus-first summary with a raw appendix) is
    unchanged, so existing reports/goldens are unaffected.
    """
    findings = findings or []
    warnings = warnings or []
    groups = groups or []
    if classification is None:
        classification = _classification.classify(findings=findings, groups=groups)

    lines: list[str] = []
    lines.append(
        "# 🏛️ AI Jury — verbose report\n" if lead_with_summary else "# 🏛️ AI Jury — full transcript\n"
    )
    # TL;DR callout (parity with render()): the verdict headline leads the
    # verbose/transcript report too, so every renderer surfaces the outcome first.
    headline = _verdict_headline(synthesis, vote)
    if headline:
        lines.append(f"> ⚡ **TL;DR · {headline}**\n")
    # bolt: explicit list enables optimized string join bypassing generator loop overhead
    panel = ", ".join([f"`{r.agent}` ({r.vendor})" for r in reviews])
    lines.append(f"**Panel:** {panel}\n")
    if review_scope:
        lines.append(f"{review_scope}\n")

    # Disclose the context/redaction policy (parity with render()): whoever reads
    # the shared transcript should see whether secrets were redacted before the
    # diff reached the agents.
    if context_mode is not None or redact_secrets is not None:
        lines.append("## Context policy\n")
        if context_mode is not None:
            lines.append(f"- context mode: {context_mode}")
        if redact_secrets is not None:
            state = "on" if redact_secrets else "off"
            extra = f" ({redaction_count} redacted)" if redact_secrets else ""
            lines.append(f"- secret redaction: {state}{extra}")
        lines.append("")

    if lead_with_summary:
        lines.extend(_summary_blocks(findings, warnings, groups, classification, vote=vote))
        lines.append("---\n")
        lines.append("# Full transcript\n")
        lines.extend(_conversation_blocks(reviews, debate, synthesis, verify, chair=chair))
    else:
        lines.extend(_conversation_blocks(reviews, debate, synthesis, verify, chair=chair))
        lines.append("---\n")
        lines.extend(_summary_blocks(findings, warnings, groups, classification, vote=vote))

    if metadata is not None:
        lines.append("---\n")
        lines.extend(_metadata_block(metadata))

    lines.append("---")
    lines.append(
        "\n<sub>Generated by "
        "[ai-jury](https://github.com/berkayturanci/ai-jury)"
        " — a cross-vendor multi-agent PR review jury.</sub>"
    )
    return "\n".join(lines)


def render_sections(
    reviews: list[AgentResult],
    debate: list[AgentResult],
    synthesis: AgentResult | None,
    *,
    chair: str,
    findings: list[Finding] | None = None,
    warnings: list[str] | None = None,
    groups: list | None = None,
    verify: AgentResult | None = None,
    classification: dict | None = None,
    vote=None,
) -> list[tuple[str, str]]:
    """Split the report into ordered ``(title, body)`` sections for phased posting.

    Returns up to three sections — **Round 1** (independent reviews), **Round 2**
    (debate, omitted when there was none), and **Decision** (verification + chair
    verdict + consensus + structured findings) — so a PR can show the flow as
    separate, readable comments (issue #127). ``render()`` (the single-blob
    report) is unchanged. Empty sections are skipped.
    """
    findings = findings or []
    warnings = warnings or []
    groups = groups or []
    sections: list[tuple[str, str]] = []

    # Round 1 — independent reviews.
    # bolt: Explicitly evaluating as a list allows C-level optimizations in join
    r1 = [f"**Panel:** {', '.join([f'`{r.agent}` ({r.vendor})' for r in reviews])}\n"]
    for r in reviews:
        status = f"{r.duration_s:.0f}s" if r.ok else _fail_status(r)
        r1.append(_block(f"`{r.agent}` ({r.vendor}) — {status}", r.output if r.ok else ""))
    sections.append(("🏛️ AI Jury — Round 1: independent reviews", "\n".join(r1).strip()))

    # Round 2 — cross-examination (only if a debate ran).
    if debate:
        r2 = []
        for r in debate:
            status = f"{r.duration_s:.0f}s" if r.ok else _fail_status(r)
            r2.append(_block(f"`{r.agent}` — {status}", r.output if r.ok else ""))
        sections.append(("🏛️ AI Jury — Round 2: cross-examination (debate)", "\n".join(r2).strip()))

    # Decision — verification + chair verdict + consensus + findings.
    dec: list[str] = []
    if classification is None:
        classification = _classification.classify(findings=findings, groups=groups)
    dec.extend(_classification_block(classification))
    if vote is not None:
        dec.extend(_vote_block(vote))
    if groups:
        dec.extend(_consensus_block(groups))
    if verify is not None:
        dec.append("## Verification\n")
        dec.append(f"> Verified by `{chair}`\n")
        dec.append(
            verify.output.strip() + "\n"
            if verify.ok
            else f"_Verification failed: {verify.error}_\n"
        )
    chair_heading = "Chair's reasoning" if vote is not None else "Chair verdict"
    if synthesis and synthesis.ok:
        dec.append(f"## {chair_heading}\n")
        dec.append(f"> Synthesized by `{chair}`\n")
        dec.append(synthesis.output.strip() + "\n")
    elif synthesis and not synthesis.ok:
        dec.append(f"## {chair_heading}\n\n_Synthesis failed: {synthesis.error}_\n")
    if findings:
        dec.append("## Structured findings\n")
        ranked = sorted(
            findings,
            key=lambda f: (SEVERITY_ORDER.get(f.severity, 99), f.file or "", f.line or 0),
        )
        # bolt: Optimizes speed by allowing Python C implementations of extend()
        dec.extend([_finding_line(f) for f in ranked])
    if warnings:
        dec.append("\n> ⚠️ agent output warnings\n")
        dec.extend([f"- {w}" for w in warnings])
    sections.append(("🏛️ AI Jury — Decision: verdict & consensus", "\n".join(dec).strip()))

    return sections
