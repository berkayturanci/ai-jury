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

#: Every file that carries the project's version. A marker missing from here is a
#: marker nothing watches — `uv.lock` was outside it and sat a release behind on
#: main while the check reported agreement (#556).
VERSION_MARKERS = ("pyproject.toml", "src/ai_jury/__init__.py", "CHANGELOG.md", "uv.lock")

#: Rendered form, so a test can assert the watched set by *reading the file*.
#: Importing would not help on a checkout where the guard has been reverted
#: away, which is the state these assertions exist to catch (#560).
VERSION_MARKERS_TEXT = ", ".join(VERSION_MARKERS)


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

    # 4. uv.lock — it carries the project's own version under `name = "ai-jury"`,
    # and the v1.14.0 release did not update it. A marker left out of this set is
    # a marker nothing watches, which is how that drift sat on main while this
    # script reported every marker in agreement (#556).
    lock_path = root / "uv.lock"
    lock_version = None
    if lock_path.is_file():
        m = re.search(
            r'name\s*=\s*"ai-jury"\s*\nversion\s*=\s*"([^"]+)"',
            lock_path.read_text(encoding="utf-8"),
        )
        if m:
            lock_version = m.group(1)
        else:
            errors.append("uv.lock: no ai-jury version entry found")
    # uv.lock itself is optional — a checkout without one is not drift.

    # Agreement check
    versions = {
        "pyproject.toml": pyproject_version,
        "src/ai_jury/__init__.py": init_version,
        "CHANGELOG.md": changelog_version,
        "uv.lock": lock_version,
    }
    distinct_versions = {v for v in versions.values() if v is not None}
    if len(distinct_versions) > 1:
        errors.append(f"Version mismatch among markers: {versions}")

    # Monotonicity check against git tags. This is the assertion that catches the
    # #547 shape - a stale branch writing an older version over a released one -
    # so "I could not check" must not be reported the same way as "I checked".
    current_ver = pyproject_version or init_version or changelog_version
    if current_ver:
        res = subprocess.run(
            ["git", "tag", "-l", "v*"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            errors.append(
                "cannot list git tags, so the released version is unknown: "
                f"{res.stderr.strip() or 'git failed'}"
            )
        else:
            parsed_tags = []
            for tag in (t.strip() for t in res.stdout.splitlines() if t.strip()):
                try:
                    parsed_tags.append((parse_semver(tag), tag))
                except ValueError:
                    continue
            if not parsed_tags:
                # A shallow checkout has no tags, which is what actions/checkout
                # gives you by default (fetch-depth: 1, fetch-tags: false). Left
                # as a pass, this check reported success on a reverted version.
                errors.append(
                    "no v* tags found, so the version cannot be compared against the "
                    "last release - the checkout is probably shallow (fetch-depth: 0?)"
                )
            else:
                highest_semver, highest_tag = max(parsed_tags, key=lambda x: x[0])
                if parse_semver(current_ver) < highest_semver:
                    errors.append(
                        f"Silent revert detected: current version {current_ver} "
                        f"is lower than existing tag {highest_tag} ({highest_semver})"
                    )

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
        if log_res.returncode != 0:
            return [f"cannot read {base_branch} history: {log_res.stderr.strip() or 'git log failed'}"]

        intervening_files = {line.strip() for line in log_res.stdout.splitlines() if line.strip()}
        overlap = pr_files & intervening_files
        if overlap:
            # This is the whole point of the check, and it used to be computed and
            # then discarded with `pass` (#556), so the function could only ever
            # report "clean". A PR that edits a file the base branch changed after
            # the PR branched is the exact shape that reverted a release: the
            # squash writes the branch's whole version of the file back over it.
            errors.append(
                f"Merge drift: PR #{pr_number} edits {len(overlap)} file(s) that "
                f"{base_branch} changed after it branched - a squash merge would "
                f"overwrite that work: {', '.join(sorted(overlap))}"
            )
    except Exception as exc:
        errors.append(f"Merge drift check error: {exc}")

    return errors


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            # Reconfigure stdout/stderr encoding is best-effort
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
            # Name what was compared, not a fixed list. The old message hard-coded
            # three files and claimed ">= latest tag" even in the shallow-checkout
            # case where no tag was ever read (#556) — a success line asserting a
            # check that did not run is worse than no line at all.
            print(f"[OK] Version integrity: {VERSION_MARKERS} agree and do not regress on the last tag.")

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
