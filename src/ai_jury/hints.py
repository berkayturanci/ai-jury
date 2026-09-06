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


def _inside(root: Path, files: Sequence[str] | None) -> list[str]:
    """The subset of *files* that are files inside *root* (pure apart from stat).

    Two kinds of name never reach a linter whatever they resolve to, because the
    linters read them as something other than a path:

    * a leading ``-`` is a flag;
    * a leading ``@`` is a **response file**. Ruff's argument parser expands
      ``@name`` into the paths that file lists, and it does so after ``--`` as
      well — so a checkout containing a file literally called ``@pwn.py``, whose
      body is the absolute path of something outside the tree, would have every
      containment check below pass and the linter read the outside file anyway
      (review round 2). The names it lists never pass through here at all.

    The rest are resolved against ``root`` and kept only if they stay under it
    and are files. ``resolve()`` follows symlinks, so a link inside the
    repository that points outside it is dropped too, which is the point: what
    reaches the linter has to be a file this checkout actually contains.
    """
    kept: list[str] = []
    try:
        base = root.resolve()
    except OSError:  # pragma: no cover - an unresolvable cwd is not a review
        return kept
    for name in files or ():
        if not name or name[0] in "-@":
            continue
        try:
            target = (base / name).resolve()
            if not target.is_file():
                continue
            target.relative_to(base)
        except (OSError, ValueError):
            continue
        kept.append(name)
    return kept


def collect_static_hints(files: Sequence[str] | None, root_dir: Path | None = None) -> str:
    """Run fast local linters on the **changed files** and return a hints string.

    ``files`` names the paths in the change under review and is required: this
    function lints those paths and nothing else. There is no whole-tree form, so
    ``files=[]`` (or ``None``) means "no files to lint" and the answer is the
    empty string — never the first diagnostics found elsewhere in the repository
    (#737). A change that touches no file a linter handles — no ``.py`` for
    Ruff, no ``.js``/``.ts``/``.jsx``/``.tsx`` for ESLint — therefore produces no
    block at all.

    Only paths that are **files inside** ``root`` are linted. Two reasons, both
    found in review of this change:

    * A diff names paths, and a diff is attacker-controlled. Forwarding them
      unchecked pointed the linter at anything the diff cared to name —
      ``../../etc/x.py``, ``/tmp/abs.py`` — and the linter's reading of that
      file went into the reviewer prompt. The old whole-tree form could not do
      that, because it was scoped to the working directory; scoping by path
      has to re-establish what it gave away.
    * A path in the diff need not exist here: every deletion names one, and a
      ``--pr`` review of a branch nobody checked out names only such paths.
      Ruff answers a missing file with ``E902 No such file or directory`` on
      stdout and a nonzero exit, which this module would have read as a
      diagnostic and put in front of the panel.

    Never fails or throws: returns an empty string when the linters are
    unavailable, clean, or error.
    """
    root = root_dir or Path.cwd()
    paths = _inside(root, files)
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
