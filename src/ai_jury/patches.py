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
    heading_re = re.compile(
        r"^###\s+([^—\n]+?)(?::(\d+))?\s+—\s+\[([^\]]+)\]\s+(.+)$", re.MULTILINE
    )
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


def _patch_body(fix: str) -> str:
    """The patch text as a patch *file* would hold it — newline-terminated.

    ``parse_patch_suggestions`` strips the fenced block, which takes the trailing
    newline with it. Git then rejects bodies whose last line is a header rather
    than content ("git diff header lacks filename information"), so a suggestion
    carrying a rename or mode section could never be read at all — including by
    the preview, which would report "nothing git could read" for a patch that is
    merely missing its terminator. Content semantics are unaffected: a file whose
    last line has no newline is expressed by git's own ``\\ No newline at end of
    file`` marker, not by the patch text's terminator.
    """
    return fix if fix.endswith("\n") else fix + "\n"


def _probe_patch(fix: str, root: Path):
    """Ask git what ``fix`` would do, writing nothing (``--check``)."""
    import subprocess

    return subprocess.run(
        ["git", "apply", "--numstat", "-z", "--summary", "--check", "-"],
        input=_patch_body(fix),
        text=True,
        cwd=str(root),
        capture_output=True,
    )


def preview_patch_suggestion(
    suggestion: PatchSuggestion, root_dir: Path | None = None
) -> tuple[list[str], str | None]:
    """Paths ``suggestion`` would touch, and why it would be refused (if it would).

    The operator-facing half of the containment check, sharing its probe so the
    preview cannot disagree with what an apply would do (#605). Any hand-rolled
    containment check is a bet that every way a patch can name a file was
    enumerated; showing the operator git's own answer before writing is what makes
    losing that bet survivable rather than silent.

    Returns ``(paths, refusal)``. ``refusal`` is ``None`` when the suggestion would
    be applied; ``paths`` is what git says it would touch, which for a refused
    patch is exactly the evidence the operator needs to see.
    """
    root = (root_dir or Path.cwd()).resolve()
    try:
        target = (root / suggestion.file).resolve()
        target.relative_to(root)
    except (ValueError, RuntimeError):
        return [], f"Path traversal rejected: {suggestion.file}"
    if not target.exists() or not target.is_file():
        return [], f"File not found: {suggestion.file}"

    fix = suggestion.suggested_fix
    if not _looks_like_patch(fix):
        # A literal line replacement touches exactly the file it names.
        return [suggestion.file], None

    probe = _probe_patch(fix, root)
    if probe.returncode != 0:
        detail = redact(probe.stderr.strip())[0] or "patch does not apply cleanly"
        return [], f"Git apply failed: {detail}"
    paths = [
        record.split("\t", 2)[2]
        for record in probe.stdout.split("\0")[:-1]
        if len(record.split("\t", 2)) == 3
    ]
    return paths, _containment_refusal(fix, root=root, target=target, file=suggestion.file)


def _containment_refusal(fix: str, *, root: Path, target: Path, file: str) -> str | None:
    """Why ``fix`` may not be applied, or ``None`` when it touches only ``target``.

    Asks git, rather than reading the patch by hand. The previous check inspected
    only ``---``/``+++`` header lines, but git carries filenames in several other
    constructs and honours all of them: ``rename from``/``rename to``,
    ``copy from``/``copy to``, ``old mode``/``new mode``, and a ``GIT binary
    patch`` section which has no ``---``/``+++`` lines at all. A patch whose
    headers named the suggested file could rename an unrelated path and still be
    reported as "Applied git patch to <file>" (#603, reproduced).

    That check was a **blocklist** — enumerate the dangerous header forms — and it
    missed because git has more of them than the enumeration covered. Adding
    ``rename from`` to the same loop repeats the design and misses the next one.
    So the question goes to the parser that will actually apply the patch:
    ``--check`` writes nothing, ``--numstat -z`` lists every path the patch would
    touch, and ``--summary`` names the operations. Validation and application now
    share one parser, which is what closes the gap rather than narrowing it.
    """
    probe = _probe_patch(fix, root)
    if probe.returncode != 0:
        detail = redact(probe.stderr.strip())[0] or "patch does not apply cleanly"
        return f"Git apply failed: {detail}"

    # NUL-terminated numstat records, then the summary block as trailing text.
    chunks = probe.stdout.split("\0")
    summary = chunks.pop() if chunks else ""
    if not chunks:
        # An allowlist answers "which paths does this touch?" — and "none that I
        # could see" is not the same answer as "only the target". A patch git reads
        # as touching nothing cannot be the fix this suggestion claims to be.
        return f"Patch touches no files; nothing to apply to {file}"
    for record in chunks:
        fields = record.split("\t", 2)
        if len(fields) != 3:
            # Not a shape this parser understands. Refusing is the only safe
            # reading: an unparsed record is a path that went unchecked.
            return f"Unrecognized patch summary from git, refusing to apply to {file}"
        try:
            touched = (root / fields[2]).resolve()
            touched.relative_to(root)
        except (ValueError, RuntimeError):
            return f"Path traversal rejected in patch: {fields[2]}"
        if touched != target:
            return f"Patch touches {fields[2]}, not {file}"

    # `--numstat` reports a rename's *destination* but never its source, so a patch
    # can delete a path that no numstat record mentions. A single-file suggestion
    # has no business renaming or copying anything, so the operation itself is
    # refused rather than its paths re-derived from prose.
    for line in summary.splitlines():
        operation = line.strip().split(" ", 1)[0]
        if operation in ("rename", "copy"):
            return f"Patch {operation}s a file; a suggestion for {file} may only edit it"
    return None


#: Line prefixes that mean "git will read this body as a patch".
#:
#: The old test — ``startswith("---") or "@@" in fix`` — recognised only a plain
#: unified diff. A rename-only or binary-only body has neither, so it missed the
#: git branch entirely and fell through to the line-replacement path, which wrote
#: the diff *text* into the file and reported success (found while fixing #603).
#: That is the same blocklist mistake as the containment check, one step earlier:
#: a form this list does not name is not merely unvalidated, it is written
#: literally. Everything git can read as a patch must reach the git branch, where
#: :func:`_containment_refusal` decides whether it may be applied.
_PATCH_MARKERS = ("diff --git ", "--- ", "+++ ", "@@", "GIT binary patch")


def _looks_like_patch(fix: str) -> bool:
    """Whether ``fix`` should be handled as a git patch rather than as literal text."""
    if fix.startswith("---") or "@@" in fix:
        return True
    return any(line.startswith(marker) for line in fix.splitlines() for marker in _PATCH_MARKERS)


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
    if _looks_like_patch(fix):
        import subprocess

        refusal = _containment_refusal(fix, root=root, target=target, file=suggestion.file)
        if refusal is not None:
            return False, refusal

        # Same body the probe validated — a different one here would mean the
        # containment check answered a question about a patch that is not applied.
        proc = subprocess.run(
            ["git", "apply", "-"],
            input=_patch_body(fix),
            text=True,
            cwd=str(root),
            capture_output=True,
        )
        if proc.returncode == 0:
            return True, f"Applied git patch to {suggestion.file}"
        return (
            False,
            f"Git apply failed: {redact(proc.stderr.strip())[0] or 'patch does not apply cleanly'}",
        )

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
