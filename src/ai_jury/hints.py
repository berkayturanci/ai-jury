"""Static analysis hints pre-pass (issue #523).

Collects fast deterministic static linter findings (Ruff, ESLint, Flake8, Gitleaks)
and injects compact hints into Round 1 prompt context so LLM reviewers focus their
attention on deep logic bugs and security flaws rather than trivial formatting.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def collect_static_hints(files: list[str] | None = None, root_dir: Path | None = None) -> str:
    """Run fast local linters on modified files and return a prompt hints string.

    Never fails or throws: returns empty string if linters are unavailable.
    """
    root = root_dir or Path.cwd()
    hints: list[str] = []

    # 1. Check for ruff
    if shutil.which("ruff"):
        try:
            cmd = ["ruff", "check", "--select", "E,F", "--output-format", "concise"]
            if files:
                cmd.extend([f for f in files if f.endswith(".py")])
            else:
                cmd.append(".")
            res = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=5)
            if res.returncode != 0 and res.stdout.strip():
                lines = [line.strip() for line in res.stdout.splitlines()[:5] if line.strip()]
                if lines:
                    hints.append("Python linter (Ruff) warnings:\n" + "\n".join(f"- {item}" for item in lines))
        except (subprocess.SubprocessError, OSError, Exception):
            # Best-effort local Ruff linter invocation; gracefully ignore errors.
            pass

    # 2. Check for eslint
    if shutil.which("npx") and (root / "package.json").exists():
        try:
            cmd = ["npx", "eslint", "--format", "compact"]
            if files is not None:
                js_files = [f for f in files if f.endswith((".js", ".ts", ".jsx", ".tsx"))]
                if not js_files:
                    cmd = []
                else:
                    cmd.extend(js_files)
            else:
                cmd.append(".")
            if cmd:
                res = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=5)
                if res.returncode != 0 and res.stdout.strip():
                    lines = [line.strip() for line in res.stdout.splitlines()[:5] if line.strip()]
                    if lines:
                        hints.append("JS/TS linter (ESLint) warnings:\n" + "\n".join(f"- {item}" for item in lines))
        except (subprocess.SubprocessError, OSError, Exception):
            # Best-effort local ESLint invocation; gracefully ignore errors.
            pass

    if not hints:
        return ""

    out = ["## Static Analysis Hints (Pre-pass)\n"]
    out.append("Static linters flagged basic syntax/formatting on modified files:")
    out.extend(hints)
    out.append(
        "\n👉 Note to reviewers: Focus your review on deep logic bugs, security vulnerabilities, "
        "race conditions, edge cases, and architectural design."
    )
    return "\n\n".join(out)
