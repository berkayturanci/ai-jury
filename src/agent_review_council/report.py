"""Render the council run into a single markdown report."""
from __future__ import annotations

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
    out = f"- [{g.severity}] {loc} — {f.claim} (reviewers: {reviewers})"
    status = getattr(g, "status", "")
    if status:
        out += f"\n  - _verification:_ {_STATUS_LABELS.get(status, status)}"
        if getattr(g, "status_reasoning", ""):
            out += f" — {g.status_reasoning}"
    if f.suggested_fix:
        out += f"\n  - _fix:_ {f.suggested_fix}"
    return out


def _metadata_block(metadata: dict) -> list[str]:
    """Render the deterministic run-metadata section.

    Intentionally omits non-deterministic fields (e.g. ``generated_at``) so the
    Markdown report stays stable for snapshot tests. Per-agent durations are
    deterministic under mock (0s) and scrubbed by the golden test's duration
    normalizer otherwise. Wall-clock is labelled a cost proxy, not a dollar cost.
    """
    lines = ["## Run metadata\n"]
    lines.append(f"- rounds executed: {metadata['rounds_executed']}")
    lines.append(f"- verify: {'on' if metadata['verify_enabled'] else 'off'}")
    lines.append(f"- context mode: {metadata['context_mode']}")
    total = metadata["total_wall_clock_s"]
    lines.append(f"- total wall-clock (cost proxy, not $): {total:.0f}s")
    lines.append("")
    lines.append("| agent | vendor | status | duration |")
    lines.append("| --- | --- | --- | --- |")
    for a in metadata["agents"]:
        code = a.get("error_code")
        status = a["status"] if not code else f"{a['status']} ({code})"
        lines.append(
            f"| {a['name']} | {a['vendor']} | {status} | {a['duration_s']:.0f}s |"
        )
    lines.append("")
    lines.append(
        "_Wall-clock seconds are an approximate cost proxy (no token counts are "
        "available from the CLIs), not a dollar cost._\n"
    )
    return lines


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
) -> str:
    findings = findings or []
    warnings = warnings or []
    groups = groups or []
    lines: list[str] = []
    lines.append("# 🏛️ Agent Review Council\n")

    panel = ", ".join(f"`{r.agent}` ({r.vendor})" for r in reviews)
    lines.append(f"**Panel:** {panel}\n")

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

    if verify is not None:
        lines.append("## Verification\n")
        lines.append(f"> Verified by `{chair}`\n")
        if verify.ok:
            lines.append(verify.output.strip() + "\n")
        else:
            lines.append(f"_Verification failed: {verify.error}_\n")
        lines.append("---\n")

    if synthesis and synthesis.ok:
        lines.append("## Chair verdict\n")
        lines.append(f"> Synthesized by `{chair}`\n")
        lines.append(synthesis.output.strip() + "\n")
    elif synthesis and not synthesis.ok:
        lines.append("## Chair verdict\n")
        lines.append(f"_Synthesis failed: {synthesis.error}_\n")

    lines.append("---\n")
    lines.append("## Structured findings\n")
    if findings:
        ranked = sorted(
            findings, key=lambda f: (SEVERITY_ORDER.get(f.severity, 99), f.file, f.line or 0)
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
        "[agent-review-council](https://github.com/berkayturanci/agent-review-council)"
        " — a cross-vendor multi-agent PR review council.</sub>"
    )
    return "\n".join(lines)
