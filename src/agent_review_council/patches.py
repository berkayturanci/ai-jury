"""Suggested-patch output for verified findings (issue #10).

The council identifies issues; this renders a *separate*, opt-in "suggested
patches" section that turns verified findings into concrete, inspectable fix
suggestions. It is deliberately conservative:

- only VERIFIED findings (a consensus group the verifier confirmed) produce a
  suggestion — unverified or rejected findings never do;
- suggestions are rendered as clearly-labelled blocks tied to one finding;
- nothing is ever applied automatically (read-only by design); the output is for
  a human to inspect, copy, or adapt.

Pure and deterministic: given the same groups it renders the same markdown.
"""
from __future__ import annotations

from dataclasses import dataclass

from .consensus import BUCKET_REJECTED, FindingGroup


@dataclass
class PatchSuggestion:
    file: str
    line: int | None
    severity: str
    claim: str
    suggested_fix: str

    def location(self) -> str:
        loc = self.file or "?"
        if self.line is not None:
            loc = f"{loc}:{self.line}"
        return loc


def patch_suggestions(groups: list[FindingGroup]) -> list[PatchSuggestion]:
    """Return one suggestion per VERIFIED group that carries a suggested fix.

    A group qualifies only when the verifier marked it ``verified`` (not
    unsupported/disputed and not merely unverified) AND its representative
    finding has a non-empty ``suggested_fix``. Order follows the input group
    order (already severity-sorted by the consensus pass).
    """
    out: list[PatchSuggestion] = []
    for g in groups:
        if getattr(g, "status", "") != "verified" or g.bucket == BUCKET_REJECTED:
            continue
        rep = g.representative
        fix = (getattr(rep, "suggested_fix", "") or "").strip()
        if not rep or not fix:
            continue
        out.append(
            PatchSuggestion(
                file=rep.file or "",
                line=rep.line,
                severity=g.severity,
                claim=(rep.claim or "").strip(),
                suggested_fix=fix,
            )
        )
    return out


def render_patch_suggestions(groups: list[FindingGroup]) -> str:
    """Render the "Suggested patches" markdown section, or "" when there are none.

    Kept separate from the default report so the standard review flow stays
    read-only; the CLI emits this only under ``--suggest-patches``.
    """
    suggestions = patch_suggestions(groups)
    if not suggestions:
        return ""
    lines = [
        "## Suggested patches",
        "",
        "_Opt-in, read-only suggestions for **verified** findings only. Inspect "
        "before applying — nothing here is applied automatically._",
        "",
    ]
    for s in suggestions:
        lines.append(f"### {s.location()} — [{s.severity}] {s.claim}")
        lines.append("")
        lines.append("> Verified by the council.")
        lines.append("")
        lines.append("```suggestion")
        lines.append(s.suggested_fix)
        lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
