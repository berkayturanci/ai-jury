"""Large-diff handling: filtering and chunking (issue #31).

The jury sends the diff to every agent, so a large or generated diff inflates
cost, runtime, and prompt size. This module measures a diff, drops files that
should not be reviewed (binary blobs, generated/vendored files, and anything the
configured path filters exclude), and decides a handling mode:

- ``full``       — kept diff fits the budget; review it in one pass.
- ``chunked``    — kept diff is over budget and chunking is enabled; split it
                   into per-file chunks each within the chunk budget.
- ``too_large``  — over budget and chunking is disabled; the caller should fail
                   with a clear message.

Everything here is PURE and deterministic: parsing, classification, and chunk
boundaries are a function of the diff text and config only, so the plan is
reproducible and unit-testable.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

# Default "generated / not worth reviewing" path globs. Conservative and
# language-agnostic; users extend or replace via ``[jury.diff] exclude``.
DEFAULT_GENERATED_GLOBS: tuple[str, ...] = (
    # Dependency lockfiles.
    "*.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Cargo.lock",
    "composer.lock",
    "Gemfile.lock",
    "go.sum",
    # Minified / map artifacts.
    "*.min.js",
    "*.min.css",
    "*.map",
    # Snapshots and common generated code.
    "*.snap",
    "*.pb.go",
    "*_pb2.py",
    "*_pb2_grpc.py",
    # Vendored / build output directories.
    "vendor/**",
    "node_modules/**",
    "dist/**",
    "build/**",
)

EXCLUDE_BINARY = "binary"
EXCLUDE_GENERATED = "generated"
EXCLUDE_FILTER = "excluded-by-filter"
EXCLUDE_NOT_INCLUDED = "not-in-include-filter"

MODE_FULL = "full"
MODE_CHUNKED = "chunked"
MODE_TOO_LARGE = "too_large"


@dataclass
class DiffFile:
    """One file's segment of a unified diff."""

    path: str
    text: str

    @property
    def size_bytes(self) -> int:
        return len(self.text.encode("utf-8"))


@dataclass
class DiffPlan:
    mode: str
    chunks: list[str] = field(default_factory=list)
    kept: list[DiffFile] = field(default_factory=list)
    excluded: list[tuple[str, str]] = field(default_factory=list)
    total_bytes: int = 0
    kept_bytes: int = 0
    reason: str = ""

    @property
    def kept_paths(self) -> list[str]:
        return [f.path for f in self.kept]


def _strip_ab(path: str) -> str:
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path


def _unquote_git_path(path: str) -> str:
    """Undo git's C-style quoting of paths with special chars (best-effort).

    git wraps a path in double quotes and octal-escapes special/non-ASCII bytes
    when ``core.quotepath`` is on. We decode it back so the full path is
    recovered for glob filtering and classification.
    """
    if len(path) >= 2 and path.startswith('"') and path.endswith('"'):
        inner = path[1:-1]
        try:
            return (
                inner.encode("latin-1", "backslashreplace")
                .decode("unicode_escape")
                .encode("latin-1")
                .decode("utf-8", "replace")
            )
        except (UnicodeDecodeError, UnicodeEncodeError):
            return inner.replace('\\"', '"').replace("\\\\", "\\")
    return path


def _path_from_marker(line: str) -> str | None:
    """Path from a ``+++ b/<p>`` or ``--- a/<p>`` line, or None for /dev/null.

    These marker lines carry a single, unambiguous path even when it contains
    spaces or quoted special chars — unlike the ``diff --git a/<p> b/<p>``
    header, which a ``str.split()`` truncates at the first space, hiding or
    mislabeling the file (security audit 2026-06-13/L-4,N-3).
    """
    rest = line[4:].rstrip("\r\n")
    # Some diff formats append a tab + timestamp; the path ends at the tab.
    if "\t" in rest:
        rest = rest.split("\t", 1)[0]
    if rest == "/dev/null":
        return None
    return _strip_ab(_unquote_git_path(rest))


def _path_from_git_header(line: str) -> str:
    """Best-effort new-side path from a ``diff --git a/<p> b/<p>`` header.

    ``str.split()[3]`` truncates a space-containing name; split on the last
    `` b/`` separator instead so the full b-side path is recovered, then unquote
    git's C-quoting (audit 2026-06-13/L-4, r3 marker-less case).
    """
    rest = line[len("diff --git ") :].rstrip("\r\n")
    # Non-rename headers are symmetric: ``a/<p> b/<p>`` with the SAME <p> on both
    # sides. Recover <p> by halving, which is robust even when <p> itself
    # contains `` b/`` (a mode-change-only segment has no +++/--- or rename
    # marker to fall back on — audit 2026-06-13 r4/L). len(body) = 2*len(p)+3.
    if rest.startswith("a/"):
        body = rest[2:]
        half = (len(body) - 3) // 2
        if (
            len(body) >= 3
            and body[half : half + 3] == " b/"
            and body[:half] == body[half + 3 :]
        ):
            return _unquote_git_path(body[:half])
    idx = rest.rfind(" b/")
    if idx != -1:
        return _strip_ab(_unquote_git_path(rest[idx + 1 :]))
    # Quoted b-side: git C-quotes special/spaced paths as `"a/<p>" "b/<p>"`, so
    # the separator is `` "b/`` not `` b/`` (audit 2026-06-13 r5/L).
    qidx = rest.rfind(' "b/')
    if qidx != -1:
        return _strip_ab(_unquote_git_path(rest[qidx + 1 :]))
    parts = line.split()
    return _strip_ab(parts[3]) if len(parts) >= 4 else _strip_ab(parts[-1])


def split_diff(diff: str) -> list[DiffFile]:
    """Split a unified diff into per-file segments.

    Segments start at ``diff --git a/<p> b/<p>`` headers (the git format the
    adapters emit). Any preamble before the first header is attached to the first
    file so no bytes are silently dropped. A diff with no ``diff --git`` header is
    returned as a single unnamed segment (it cannot be chunked by file).
    """
    if not diff:
        return []

    files: list[DiffFile] = []

    parts = []
    # bolt: avoid allocating a huge list of strings from splitlines(keepends=True)
    # by splitting chunks directly and only iterating their header lines.
    idx = diff.find("diff --git ")
    if idx == -1:
        parts = [diff]
    else:
        if idx > 0:
            parts.append(diff[:idx])

        while idx != -1:
            next_idx = diff.find("\ndiff --git ", idx)
            if next_idx == -1:
                parts.append(diff[idx:])
                break
            parts.append(diff[idx:next_idx+1])
            idx = next_idx + 1

    for part in parts:
        cur_path = None

        p_idx = 0
        while p_idx < len(part):
            next_nl = part.find("\n", p_idx)
            line = part[p_idx:] if next_nl == -1 else part[p_idx : next_nl + 1]

            if line.startswith("diff --git "):
                cur_path = _path_from_git_header(line)
            elif line.startswith("+++ ") or (line.startswith("--- ") and not cur_path):
                p = _path_from_marker(line)
                if p is not None:
                    cur_path = p
            elif line.startswith(("rename to ", "copy to ")):
                p = _strip_ab(_unquote_git_path(line.split(" to ", 1)[1].rstrip("\r\n")))
                if p:
                    cur_path = p

            if cur_path is None:
                cur_path = ""

            if line.startswith("@@ "):
                break

            if next_nl == -1:
                break
            p_idx = next_nl + 1

        files.append(DiffFile(path=cur_path or "", text=part))

    return files


_BINARY_RE = re.compile(r"(?m)^\s*(?:GIT binary patch|Binary files .* differ)\s*$")


def _is_binary(text: str) -> bool:
    """True when a file segment is a git *binary* diff.

    Matches the binary marker on its own header line — ``Binary files … differ``
    or a standalone ``GIT binary patch`` — rather than the substring anywhere in
    the text. A diff's content lines are prefixed with ``+``/``-``/`` ``, so this
    never misfires on source code that merely *mentions* those strings (e.g. this
    module's own detector).
    """
    # bolt: avoid allocating a huge list of strings from splitlines()
    # and generator overhead by using C-optimized regex finding.
    return bool(_BINARY_RE.search(text))


def _matches_any(path: str, patterns) -> bool:
    """True when ``path`` matches any glob in ``patterns``.

    Supports a trailing ``/**`` to mean "anything under this directory" and the
    basename for simple ``*.ext`` patterns, in addition to a full-path match.
    """
    name = path.rsplit("/", 1)[-1]
    for pat in patterns:
        if pat.endswith("/**"):
            prefix = pat[:-2]  # keep trailing slash
            if path.startswith(prefix):
                return True
        if fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(name, pat):
            return True
    return False


def _split_file_at_hunk_boundaries(f: DiffFile, chunk_max_bytes: int) -> list[str]:
    """Split a single large diff file across semantic hunk headers (issue #522).

    Preserves the file header preamble on subsequent chunks so context is not lost.
    """
    text = f.text
    hunk_pattern = re.compile(r"(?m)^(@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@.*)$")
    parts = hunk_pattern.split(text)
    if len(parts) <= 1:
        return [text]

    header = parts[0]
    chunks: list[str] = []
    current: list[str] = [header]
    current_bytes = len(header.encode("utf-8"))

    for i in range(1, len(parts), 2):
        hunk = parts[i] + (parts[i + 1] if i + 1 < len(parts) else "")
        hb = len(hunk.encode("utf-8"))
        if current_bytes + hb > chunk_max_bytes and len(current) > 1:
            chunks.append("".join(current))
            current = [header, hunk]
            current_bytes = len(header.encode("utf-8")) + hb
        else:
            current.append(hunk)
            current_bytes += hb

    if current:
        chunks.append("".join(current))
    return chunks


def _chunk_files(kept: list[DiffFile], chunk_max_bytes: int) -> list[str]:
    """Greedily pack kept files into chunks no larger than the budget.

    Files keep their order. Large files exceeding budget are semantically split
    at hunk boundaries with file header preservation (issue #522).
    """
    chunks: list[str] = []
    current: list[str] = []
    current_bytes = 0
    for f in kept:
        fb = f.size_bytes
        if fb > chunk_max_bytes:
            if current:
                chunks.append("".join(current))
                current, current_bytes = [], 0
            chunks.extend(_split_file_at_hunk_boundaries(f, chunk_max_bytes))
            continue
        if current and current_bytes + fb > chunk_max_bytes:
            chunks.append("".join(current))
            current, current_bytes = [], 0
        current.append(f.text)
        current_bytes += fb
    if current:
        chunks.append("".join(current))
    return chunks


def plan_diff(
    diff: str,
    *,
    max_bytes: int,
    chunk: bool,
    chunk_max_bytes: int | None = None,
    exclude_generated: bool = True,
    exclude: tuple[str, ...] | list[str] = (),
    include: tuple[str, ...] | list[str] = (),
) -> DiffPlan:
    """Measure, filter, and decide a handling mode for ``diff`` (issue #31)."""
    files = split_diff(diff)
    total_bytes = len(diff.encode("utf-8"))
    chunk_max_bytes = chunk_max_bytes or max_bytes

    kept: list[DiffFile] = []
    excluded: list[tuple[str, str]] = []
    generated_globs = tuple(DEFAULT_GENERATED_GLOBS) + tuple(exclude)

    for f in files:
        # An include allow-list, when present, drops anything not matching.
        if include and not _matches_any(f.path, include):
            excluded.append((f.path, EXCLUDE_NOT_INCLUDED))
            continue
        if _is_binary(f.text):
            excluded.append((f.path, EXCLUDE_BINARY))
            continue
        if exclude_generated and _matches_any(f.path, generated_globs):
            excluded.append((f.path, EXCLUDE_GENERATED))
            continue
        if exclude and _matches_any(f.path, exclude):
            excluded.append((f.path, EXCLUDE_FILTER))
            continue
        kept.append(f)

    filtered_diff = "".join(f.text for f in kept)
    kept_bytes = len(filtered_diff.encode("utf-8"))

    if kept_bytes <= max_bytes:
        mode = MODE_FULL
        chunks = [filtered_diff] if kept_bytes else []
        reason = (
            f"{kept_bytes} B within budget ({max_bytes} B); reviewing in one pass"
            if kept_bytes
            else "nothing left to review after filters"
        )
    elif chunk:
        mode = MODE_CHUNKED
        chunks = _chunk_files(kept, chunk_max_bytes)
        reason = (
            f"{kept_bytes} B over budget ({max_bytes} B); chunked into "
            f"{len(chunks)} part(s) of <= {chunk_max_bytes} B"
        )
    else:
        mode = MODE_TOO_LARGE
        chunks = []
        reason = (
            f"{kept_bytes} B over budget ({max_bytes} B) and chunking is disabled; "
            f"enable [jury.diff] chunk = true or narrow the diff with "
            f"include/exclude filters"
        )

    return DiffPlan(
        mode=mode,
        chunks=chunks,
        kept=kept,
        excluded=excluded,
        total_bytes=total_bytes,
        kept_bytes=kept_bytes,
        reason=reason,
    )
