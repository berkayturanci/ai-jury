"""Thin GitHub helpers built on the `gh` CLI.

Used to pull a PR diff in and to post the council verdict back as a comment.
Kept dependency-free; if `gh` is unavailable these raise a clear error.
"""
from __future__ import annotations

import shutil
import subprocess


def _gh(*args: str) -> str:
    if shutil.which("gh") is None:
        raise RuntimeError("the GitHub CLI `gh` is not installed or not on PATH")
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def pr_diff(pr: str, repo: str | None = None) -> str:
    args = ["pr", "diff", str(pr)]
    if repo:
        args += ["--repo", repo]
    return _gh(*args)


def pr_context(pr: str, repo: str | None = None) -> str:
    """Return 'title\\n\\nbody' for a PR, best-effort."""
    args = ["pr", "view", str(pr), "--json", "title,body",
            "--jq", '.title + "\\n\\n" + (.body // "")']
    if repo:
        args += ["--repo", repo]
    try:
        return _gh(*args).strip()
    except RuntimeError:
        return ""


def post_pr_comment(pr: str, body: str, repo: str | None = None) -> None:
    args = ["pr", "comment", str(pr), "--body", body]
    if repo:
        args += ["--repo", repo]
    _gh(*args)
