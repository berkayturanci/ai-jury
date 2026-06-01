"""Large-diff handling: filtering and chunking (issue #31).

The council sends the diff to every agent, so a large or generated diff inflates
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
from dataclasses import dataclass, field

# Default "generated / not worth reviewing" path globs. Conservative and
# language-agnostic; users extend or replace via ``[council.diff] exclude``.
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
            return path[len(prefix):]
    return path


def split_diff(diff: str) -> list[DiffFile]:
    """Split a unified diff into per-file segments.

    Segments start at ``diff --git a/<p> b/<p>`` headers (the git format the
    adapters emit). Any preamble before the first header is attached to the first
    file so no bytes are silently dropped. A diff with no ``diff --git`` header is
    returned as a single unnamed segment (it cannot be chunked by file).
    """
    if not diff:
        return []
    lines = diff.splitlines(keepends=True)
    files: list[DiffFile] = []
    cur_path: str | None = None
    cur: list[str] = []

    def flush() -> None:
        if cur:
            files.append(DiffFile(path=cur_path or "", text="".join(cur)))

    for line in lines:
        if line.startswith("diff --git "):
            flush()
            cur = [line]
            parts = line.split()
            # "diff --git a/x b/x" -> prefer the new-side (b/) path.
            cur_path = _strip_ab(parts[3]) if len(parts) >= 4 else _strip_ab(parts[-1])
        else:
            cur.append(line)
            if cur_path is None:
                cur_path = ""
    flush()
    return files


def _is_binary(text: str) -> bool:
    return "Binary files " in text or "GIT binary patch" in text


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


def _chunk_files(kept: list[DiffFile], chunk_max_bytes: int) -> list[str]:
    """Greedily pack kept files into chunks no larger than the budget.

    Files keep their order. A file that alone exceeds the budget becomes its own
    chunk (it cannot be split without breaking the diff), so chunking degrades
    gracefully rather than failing.
    """
    chunks: list[str] = []
    current: list[str] = []
    current_bytes = 0
    for f in kept:
        fb = f.size_bytes
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
            f"enable [council.diff] chunk = true or narrow the diff with "
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
