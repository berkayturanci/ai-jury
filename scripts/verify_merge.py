#!/usr/bin/env python3
"""Merge verification & silent-revert drift detector.

Prevents stale branch squash-merges from silently reverting releases or
overwriting files changed by intervening PRs (addressing #547).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


def _load_release_surfaces():
    """The shared surface table, loaded by path rather than by package import.

    This script is run as `python scripts/verify_merge.py` from CI and imported
    by path from `tests/`, and neither puts the repository on `sys.path`. It is
    registered in `sys.modules` under a stable name so every guard — this one and
    the two test modules — holds the *same* module object: a test that patches
    the table has to patch the table this script reads (#665).
    """
    module = sys.modules.get("release_surfaces")
    if module is None:
        path = Path(__file__).resolve().parent / "release_surfaces.py"
        spec = importlib.util.spec_from_file_location("release_surfaces", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["release_surfaces"] = module
        spec.loader.exec_module(module)
    return module


#: Every file that carries the project's version lives in one table, shared with
#: `tests/test_release_metadata.py` and `tests/test_homebrew_formula.py`. A marker
#: missing from it is a marker nothing watches — `uv.lock` was outside the set and
#: sat a release behind on main while the check reported agreement (#556), and the
#: website sat two releases behind because registering a surface meant editing
#: three disjoint lists (#646, #665).
release_surfaces = _load_release_surfaces()


def parse_semver(v: str) -> tuple[int, int, int]:
    """Parse a 'X.Y.Z' or 'vX.Y.Z' string into integer tuple (major, minor, patch)."""
    clean = v.lstrip("v").strip()
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", clean)
    if not match:
        raise ValueError(f"Invalid semver version: {v!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def check_version_integrity(root: Path) -> list[str]:
    """Verify that every release surface names one version, and that the version
    has not gone backwards from the highest released git tag.

    Surfaces come from `scripts/release_surfaces.py`; there is no second list
    here to fall out of step with it.
    """
    errors: list[str] = release_surfaces.problems(root)

    # Agreement. `problems` has already reported surfaces that could not be read
    # at all; what is left is files that each name a version, which must be the
    # same version. An absent optional surface (a checkout with no `uv.lock`, no
    # website) is not drift — there is simply nothing to compare.
    versions = release_surfaces.find_versions(root)
    distinct_versions = {v for found in versions.values() for v in found}
    if len(distinct_versions) > 1:
        rendered = {path: sorted(found) for path, found in sorted(versions.items())}
        errors.append(f"Version mismatch among markers: {rendered}")

    # Monotonicity check against git tags. This is the assertion that catches the
    # #547 shape - a stale branch writing an older version over a released one -
    # so "I could not check" must not be reported the same way as "I checked".
    current_ver = release_surfaces.declared_version(root)
    if current_ver is None and distinct_versions:
        current_ver = sorted(distinct_versions)[0]
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


def check_release_surfaces(root: Path) -> list[str]:
    """Every release surface must name the version `pyproject.toml` declares.

    Stricter than `check_version_integrity`, which only asks whether the surfaces
    present agree: here a listed file that is missing is itself the defect. This
    is the question a release asks, and `make release-check` is how a maintainer
    asks it before opening the release pull request instead of after the tag.
    """
    expected = release_surfaces.declared_version(root)
    if expected is None:
        return ["cannot read a version from pyproject.toml, so no surface can be checked"]
    return release_surfaces.mismatches(root, expected)


def check_pr_merge_drift(root: Path, pr_number: int) -> list[str]:
    """Check if any files modified in PR were modified on base branch after PR branched."""
    errors: list[str] = []
    try:
        res = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--json",
                "baseRefName,headRefOid,files,mergedAt,createdAt",
            ],
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
            [
                "git",
                "log",
                f"origin/{base_branch}",
                "--since",
                data.get("createdAt", ""),
                "--name-only",
                "--pretty=format:",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if log_res.returncode != 0:
            return [
                f"cannot read {base_branch} history: {log_res.stderr.strip() or 'git log failed'}"
            ]

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

    parser = argparse.ArgumentParser(
        description="Verify merge and version integrity against silent reverts."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository root directory")
    parser.add_argument(
        "--check-version",
        action="store_true",
        help="Assert version marker agreement & monotonicity",
    )
    parser.add_argument(
        "--check-surfaces",
        action="store_true",
        help="Assert every file in scripts/release_surfaces.py names the declared version",
    )
    parser.add_argument("--pr", type=int, help="PR number to verify for merge drift")
    parser.add_argument("--all", action="store_true", help="Run all verification checks")

    args = parser.parse_args(argv)
    root = args.root.resolve()

    run_version = args.check_version or args.all or not (args.pr or args.check_surfaces)
    run_surfaces = args.check_surfaces or args.all
    run_pr = args.pr is not None or args.all

    all_errors: list[str] = []

    if run_surfaces:
        s_errors = check_release_surfaces(root)
        if s_errors:
            all_errors.extend(s_errors)
        else:
            print(
                f"[OK] Release surfaces: {len(release_surfaces.SURFACE_PATHS)} files "
                f"all name {release_surfaces.declared_version(root)}."
            )

    if run_version:
        v_errors = check_version_integrity(root)
        if v_errors:
            all_errors.extend(v_errors)
        else:
            # Name what was compared, not a fixed list. The old message hard-coded
            # three files and claimed ">= latest tag" even in the shallow-checkout
            # case where no tag was ever read (#556) — a success line asserting a
            # check that did not run is worse than no line at all.
            print(
                f"[OK] Version integrity: {release_surfaces.SURFACE_PATHS} agree "
                "and do not regress on the last tag."
            )

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
