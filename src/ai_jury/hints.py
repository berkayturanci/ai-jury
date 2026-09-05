"""Static analysis hints pre-pass (issue #523).

Collects fast deterministic static linter findings (Ruff, ESLint, Flake8, Gitleaks)
and injects compact hints into Round 1 prompt context so LLM reviewers focus their
attention on deep logic bugs and security flaws rather than trivial formatting.

The pre-pass is scoped to the change under review and to nothing else (#737):
:func:`collect_static_hints` takes the changed paths as a **required** argument
and has no whole-tree form. There is no argument that lints the working
directory, so the "first five diagnostics from anywhere in the repository"
failure cannot be reached by a caller that simply forgets to pass the paths —
that call is now a ``TypeError``, not a silently wrong prompt.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

#: Extensions each linter is asked about. A change that touches none of them
#: produces no block at all.
_PY_SUFFIXES = (".py",)
_JS_SUFFIXES = (".js", ".ts", ".jsx", ".tsx")

#: How many diagnostics from one linter reach the prompt.
_MAX_LINES = 5


def _run_linter(cmd: list[str], root: Path, title: str) -> str | None:
    """Run one linter and format up to :data:`_MAX_LINES` of its output (thin I/O).

    Returns ``None`` when the linter is clean, unavailable, or fails in any way:
    the pre-pass is best-effort and never breaks a review.
    """
    try:
        res = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=5)
    except (subprocess.SubprocessError, OSError, Exception):
        # Best-effort local linter invocation; gracefully ignore errors.
        return None
    if res.returncode == 0:
        return None
    lines = [line.strip() for line in res.stdout.splitlines()[:_MAX_LINES] if line.strip()]
    if not lines:
        return None
    return f"{title}\n" + "\n".join(f"- {item}" for item in lines)


def collect_static_hints(files: Sequence[str] | None, root_dir: Path | None = None) -> str:
    """Run fast local linters on the **changed files** and return a hints string.

    ``files`` names the paths in the change under review and is required: this
    function lints those paths and nothing else. There is no whole-tree form, so
    ``files=[]`` (or ``None``) means "no files to lint" and the answer is the
    empty string — never the first diagnostics found elsewhere in the repository
    (#737). A change that touches no file a linter handles — no ``.py`` for
    Ruff, no ``.js``/``.ts``/``.jsx``/``.tsx`` for ESLint — therefore produces no
    block at all.

    Each linter reads those paths from the working tree, so a path that is not
    checked out simply yields nothing. Never fails or throws: returns an empty
    string when the linters are unavailable, clean, or error.
    """
    root = root_dir or Path.cwd()
    # A leading "-" would be read as a flag by the linter, not a path.
    paths = [f for f in (files or ()) if f and not f.startswith("-")]
    if not paths:
        return ""

    hints: list[str] = []

    # 1. Ruff, on the changed Python files only.
    py_files = [f for f in paths if f.endswith(_PY_SUFFIXES)]
    if py_files and shutil.which("ruff"):
        block = _run_linter(
            ["ruff", "check", "--select", "E,F", "--output-format", "concise", "--", *py_files],
            root,
            "Python linter (Ruff) warnings:",
        )
        if block:
            hints.append(block)

    # 2. ESLint, on the changed JS/TS files only.
    js_files = [f for f in paths if f.endswith(_JS_SUFFIXES)]
    if js_files and shutil.which("npx") and (root / "package.json").exists():
        block = _run_linter(
            ["npx", "eslint", "--format", "compact", "--", *js_files],
            root,
            "JS/TS linter (ESLint) warnings:",
        )
        if block:
            hints.append(block)

    if not hints:
        return ""

    out = ["## Static Analysis Hints (Pre-pass)\n"]
    out.append("Static linters flagged basic syntax/formatting in files changed by this diff:")
    out.extend(hints)
    out.append(
        "\n👉 Note to reviewers: Focus your review on deep logic bugs, security vulnerabilities, "
        "race conditions, edge cases, and architectural design."
    )
    return "\n\n".join(out)
