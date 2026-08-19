"""Suggested-patch output for verified findings (issue #10).

The jury identifies issues; this renders a *separate*, opt-in "suggested
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
from pathlib import Path

from .consensus import BUCKET_REJECTED, FindingGroup
from .findings import fence_safe, flatten_inline
from .redaction import redact


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
        # Flatten the heading text and break any fence-closer inside the
        # suggestion body so attacker-influenced finding text can't inject a
        # forged verdict/heading into the posted comment (audit 2026-06-13 r3).
        lines.append(
            f"### {flatten_inline(s.location())} — [{s.severity}] {flatten_inline(s.claim)}"
        )
        lines.append("")
        lines.append("> Verified by the jury.")
        lines.append("")
        lines.append("```suggestion")
        lines.append(fence_safe(s.suggested_fix))
        lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_patch_suggestions(text: str) -> list[PatchSuggestion]:
    """Parse PatchSuggestion objects from a markdown report or suggested-patches block."""
    import re

    out: list[PatchSuggestion] = []
    # Pattern matches: ### file.py:123 — [severity] claim
    heading_re = re.compile(r"^###\s+([^—\n]+?)(?::(\d+))?\s+—\s+\[([^\]]+)\]\s+(.+)$", re.MULTILINE)
    suggestion_block_re = re.compile(r"```suggestion\n(.*?)\n```", re.DOTALL)

    matches = list(heading_re.finditer(text))
    for i, m in enumerate(matches):
        file_path = m.group(1).strip()
        line_num = int(m.group(2)) if m.group(2) else None
        severity = m.group(3).strip()
        claim = m.group(4).strip()

        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sub_content = text[start:end]

        fix_match = suggestion_block_re.search(sub_content)
        if fix_match:
            fix = fix_match.group(1).strip()
            out.append(
                PatchSuggestion(
                    file=file_path,
                    line=line_num,
                    severity=severity,
                    claim=claim,
                    suggested_fix=fix,
                )
            )
    return out


def apply_patch_suggestion(
    suggestion: PatchSuggestion, root_dir: Path | None = None
) -> tuple[bool, str]:
    """Safely apply a patch suggestion to the targeted file."""
    root = (root_dir or Path.cwd()).resolve()
    try:
        target = (root / suggestion.file).resolve()
        target.relative_to(root)
    except (ValueError, RuntimeError):
        return False, f"Path traversal rejected: {suggestion.file}"

    if not target.exists() or not target.is_file():
        return False, f"File not found: {suggestion.file}"

    fix = suggestion.suggested_fix
    if fix.startswith("---") or "@@" in fix:
        import subprocess

        # Ensure unified diff headers do not attempt path traversal or target outside suggestion.file
        for line in fix.splitlines():
            if line.startswith("--- ") or line.startswith("+++ "):
                path_part = line[4:].strip().split("\t")[0]
                if path_part and path_part != "/dev/null":
                    clean_path = path_part.removeprefix("a/").removeprefix("b/").strip()
                    try:
                        diff_target = (root / clean_path).resolve()
                        diff_target.relative_to(root)
                    except (ValueError, RuntimeError):
                        return False, f"Path traversal rejected in diff header: {clean_path}"
                    if diff_target != target:
                        return False, f"Diff header target mismatch: {clean_path} != {suggestion.file}"

        proc = subprocess.run(
            ["git", "apply", "-"], input=fix, text=True, cwd=str(root), capture_output=True
        )
        if proc.returncode == 0:
            return True, f"Applied git patch to {suggestion.file}"
        return False, f"Git apply failed: {redact(proc.stderr.strip())[0] or 'patch does not apply cleanly'}"

    try:
        lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError) as exc:
        return False, f"Cannot read {suggestion.file}: {exc}"

    if suggestion.line is not None and 1 <= suggestion.line <= len(lines):
        idx = suggestion.line - 1
        lines[idx] = fix + ("\n" if not fix.endswith("\n") else "")
        try:
            target.write_text("".join(lines), encoding="utf-8")
        except OSError as exc:
            return False, f"Cannot write {suggestion.file}: {exc}"
        return True, f"Applied line replacement at {suggestion.file}:{suggestion.line}"

    return False, f"Cannot apply non-diff suggestion without line match in {suggestion.file}"

