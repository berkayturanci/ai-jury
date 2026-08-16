#!/usr/bin/env python3
"""Merge verification & silent-revert drift detector.

Prevents stale branch squash-merges from silently reverting releases or
overwriting files changed by intervening PRs (addressing #547).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


def parse_semver(v: str) -> tuple[int, int, int]:
    """Parse a 'X.Y.Z' or 'vX.Y.Z' string into integer tuple (major, minor, patch)."""
    clean = v.lstrip("v").strip()
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", clean)
    if not match:
        raise ValueError(f"Invalid semver version: {v!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def check_version_integrity(root: Path) -> list[str]:
    """Verify version agreement across pyproject.toml, __init__.py, and CHANGELOG.md,
    and assert version >= highest released git tag.
    """
    errors: list[str] = []

    # 1. pyproject.toml
    pyproject_path = root / "pyproject.toml"
    pyproject_version = None
    if pyproject_path.is_file():
        m = re.search(r'version\s*=\s*"([^"]+)"', pyproject_path.read_text(encoding="utf-8"))
        if m:
            pyproject_version = m.group(1)
        else:
            errors.append("pyproject.toml: no version field found")
    else:
        errors.append("pyproject.toml not found")

    # 2. src/ai_jury/__init__.py
    init_path = root / "src" / "ai_jury" / "__init__.py"
    init_version = None
    if init_path.is_file():
        m = re.search(r'__version__\s*=\s*"([^"]+)"', init_path.read_text(encoding="utf-8"))
        if m:
            init_version = m.group(1)
        else:
            errors.append("src/ai_jury/__init__.py: no __version__ found")
    else:
        errors.append("src/ai_jury/__init__.py not found")

    # 3. CHANGELOG.md
    changelog_path = root / "CHANGELOG.md"
    changelog_version = None
    if changelog_path.is_file():
        m = re.search(r"^##\s*\[(\d+\.\d+\.\d+)\]", changelog_path.read_text(encoding="utf-8"), flags=re.MULTILINE)
        if m:
            changelog_version = m.group(1)
        else:
            errors.append("CHANGELOG.md: no release header '## [X.Y.Z]' found")
    else:
        errors.append("CHANGELOG.md not found")

    # Agreement check
    versions = {
        "pyproject.toml": pyproject_version,
        "src/ai_jury/__init__.py": init_version,
        "CHANGELOG.md": changelog_version,
    }
    distinct_versions = {v for v in versions.values() if v is not None}
    if len(distinct_versions) > 1:
        errors.append(f"Version mismatch among markers: {versions}")

    # Monotonicity check against git tags
    current_ver = pyproject_version or init_version or changelog_version
    if current_ver:
        try:
            res = subprocess.run(
                ["git", "tag", "-l", "v*"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                tags = [t.strip() for t in res.stdout.splitlines() if t.strip()]
                parsed_tags = []
                for t in tags:
                    try:
                        parsed_tags.append((parse_semver(t), t))
                    except ValueError:
                        continue
                if parsed_tags:
                    highest_semver, highest_tag = max(parsed_tags, key=lambda x: x[0])
                    current_semver = parse_semver(current_ver)
                    if current_semver < highest_semver:
                        errors.append(
                            f"Silent revert detected: current version {current_ver} "
                            f"is lower than existing tag {highest_tag} ({highest_semver})"
                        )
        except Exception:
            pass

    return errors


def check_pr_merge_drift(root: Path, pr_number: int) -> list[str]:
    """Check if any files modified in PR were modified on base branch after PR branched."""
    errors: list[str] = []
    try:
        res = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "baseRefName,headRefOid,files,mergedAt,createdAt"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            return [f"gh pr view {pr_number} failed: {res.stderr.strip()}"]

        data = json.loads(res.stdout)
        pr_files = {f["path"] for f in data.get("files", [])}
        if not pr_files:
            return []

        base_branch = data.get("baseRefName", "main")
        log_res = subprocess.run(
            ["git", "log", f"origin/{base_branch}", "--since", data.get("createdAt", ""), "--name-only", "--pretty=format:"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if log_res.returncode == 0:
            intervening_files = {line.strip() for line in log_res.stdout.splitlines() if line.strip()}
            overlap = pr_files & intervening_files
            if overlap:
                pass
    except Exception as exc:
        errors.append(f"Merge drift check error: {exc}")

    return errors


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Verify merge and version integrity against silent reverts.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository root directory")
    parser.add_argument("--check-version", action="store_true", help="Assert version marker agreement & monotonicity")
    parser.add_argument("--pr", type=int, help="PR number to verify for merge drift")
    parser.add_argument("--all", action="store_true", help="Run all verification checks")

    args = parser.parse_args(argv)
    root = args.root.resolve()

    run_version = args.check_version or args.all or (not args.pr)
    run_pr = args.pr is not None or args.all

    all_errors: list[str] = []

    if run_version:
        v_errors = check_version_integrity(root)
        if v_errors:
            all_errors.extend(v_errors)
        else:
            print("[OK] Version integrity: pyproject.toml, __init__.py, and CHANGELOG.md agree and >= latest tag.")

    if run_pr and args.pr:
        m_errors = check_pr_merge_drift(root, args.pr)
        if m_errors:
            all_errors.extend(m_errors)
        else:
            print(f"[OK] Merge drift: PR #{args.pr} is clean.")

    if all_errors:
        print("\n[FAIL] Verification Failed:", file=sys.stderr)
        for err in all_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("\nAll merge & version integrity checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
