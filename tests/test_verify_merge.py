"""Tests for scripts/verify_merge.py.

The fixtures are real git repositories with real tags, not bare directories.
That matters: the assertion this script exists for — "the version has not gone
backwards from the last release" — can only be exercised against a repo that has
a release tag, and it was the untested half. #556 found it silently skipping on
every CI run because the checkout was shallow, which no test would have noticed.
"""

import importlib.util
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

# Dynamically import verify_merge script
script_path = Path(__file__).resolve().parent.parent / "scripts" / "verify_merge.py"
spec = importlib.util.spec_from_file_location("verify_merge", script_path)
verify_merge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_merge)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _repo(root: Path, version: str = "1.14.0", *, lock: str | None = "1.14.0") -> None:
    """A minimal repo carrying every version marker, committed."""
    (root / "pyproject.toml").write_text(
        f'[project]\nversion = "{version}"\n', encoding="utf-8"
    )
    (root / "src" / "ai_jury").mkdir(parents=True, exist_ok=True)
    (root / "src" / "ai_jury" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    (root / "CHANGELOG.md").write_text(f"## [{version}] - 2026-08-16\n", encoding="utf-8")
    if lock is not None:
        (root / "uv.lock").write_text(
            f'[[package]]\nname = "ai-jury"\nversion = "{lock}"\n', encoding="utf-8"
        )
    _git(root, "init", "-q", ".")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "fixture")


class ParseSemverTests(unittest.TestCase):
    def test_parse_semver(self):
        self.assertEqual(verify_merge.parse_semver("1.14.0"), (1, 14, 0))
        self.assertEqual(verify_merge.parse_semver("v1.14.0"), (1, 14, 0))
        self.assertEqual(verify_merge.parse_semver("v2.0.1"), (2, 0, 1))
        with self.assertRaises(ValueError):
            verify_merge.parse_semver("invalid-version")


class VersionIntegrityTests(unittest.TestCase):
    def test_markers_agreeing_at_the_tagged_version_is_clean(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _repo(root)
            _git(root, "tag", "v1.14.0")
            self.assertEqual([], verify_merge.check_version_integrity(root))

    def test_being_ahead_of_the_last_tag_is_not_an_error(self):
        # The normal state between releases. Comparing with != instead of <
        # would make every post-release commit red.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _repo(root, "1.15.0", lock="1.15.0")
            _git(root, "tag", "v1.14.0")
            self.assertEqual([], verify_merge.check_version_integrity(root))

    def test_a_version_behind_the_last_tag_is_the_silent_revert(self):
        # Exactly the incident #547 was filed about: markers internally
        # consistent, all of them one release behind what was published.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _repo(root, "1.13.0", lock="1.13.0")
            _git(root, "tag", "v1.14.0")
            errors = verify_merge.check_version_integrity(root)
            self.assertTrue(any("Silent revert detected" in e for e in errors), errors)

    def test_a_checkout_without_tags_fails_rather_than_passing(self):
        # actions/checkout defaults to fetch-depth: 1 / fetch-tags: false, so the
        # comparison above had nothing to compare against and reported success on
        # every run (#556). Not being able to check is not a pass.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _repo(root, "1.13.0", lock="1.13.0")  # would be a revert, if we could tell
            errors = verify_merge.check_version_integrity(root)
            self.assertTrue(any("no v* tags found" in e for e in errors), errors)

    def test_uv_lock_left_behind_is_a_mismatch(self):
        # uv.lock carries the version too and was outside the checked set, so it
        # sat a release behind on main while the script reported agreement.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _repo(root, "1.14.0", lock="1.13.0")
            _git(root, "tag", "v1.14.0")
            errors = verify_merge.check_version_integrity(root)
            self.assertTrue(any("mismatch" in e.lower() for e in errors), errors)
            self.assertTrue(any("uv.lock" in e for e in errors), errors)

    def test_a_repo_without_uv_lock_is_not_drift(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _repo(root, lock=None)
            _git(root, "tag", "v1.14.0")
            self.assertEqual([], verify_merge.check_version_integrity(root))

    def test_disagreeing_markers_are_reported(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _repo(root)
            (root / "src" / "ai_jury" / "__init__.py").write_text(
                '__version__ = "1.13.0"\n', encoding="utf-8"
            )
            _git(root, "tag", "v1.14.0")
            errors = verify_merge.check_version_integrity(root)
            self.assertTrue(any("mismatch" in e.lower() for e in errors), errors)

    def test_missing_files_are_reported(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _git(root, "init", "-q", ".")
            errors = verify_merge.check_version_integrity(root)
            self.assertGreaterEqual(len(errors), 3)


class MergeDriftTests(unittest.TestCase):
    """The half of #547 that is actually about merge drift.

    It used to compute the overlap and discard it with `pass`, so it could only
    ever report clean — the shape of check this repo keeps finding.
    """

    def _run(self, pr_files, intervening):
        listed = "\n".join(intervening)
        payload = (
            '{"baseRefName":"main","headRefOid":"abc","createdAt":"2026-08-16T00:00:00Z",'
            '"files":[' + ",".join(f'{{"path":"{p}"}}' for p in pr_files) + "]}"
        )

        def fake_run(cmd, *_args, **_kwargs):
            if cmd[0] == "gh":
                return subprocess.CompletedProcess(cmd, 0, payload, "")
            return subprocess.CompletedProcess(cmd, 0, listed, "")

        with unittest.mock.patch.object(verify_merge.subprocess, "run", side_effect=fake_run):
            return verify_merge.check_pr_merge_drift(Path.cwd(), 1)

    def test_overlap_is_reported_not_swallowed(self):
        errors = self._run(["website/index.html"], ["website/index.html", "README.md"])
        self.assertTrue(any("Merge drift" in e for e in errors), errors)
        self.assertTrue(any("website/index.html" in e for e in errors), errors)

    def test_no_overlap_is_clean(self):
        self.assertEqual([], self._run(["a.py"], ["b.py", "c.py"]))

    def test_a_pr_touching_nothing_is_clean(self):
        self.assertEqual([], self._run([], ["a.py"]))


class CliTests(unittest.TestCase):
    def test_cli_returns_zero_on_a_clean_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _repo(root)
            _git(root, "tag", "v1.14.0")
            self.assertEqual(0, verify_merge.main(["--root", str(root), "--check-version"]))

    def test_cli_returns_one_on_a_revert(self):
        # Built against a fixture rather than the real repo: the unit suite runs
        # in the shallow matrix job, where the real checkout has no tags.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _repo(root, "1.13.0", lock="1.13.0")
            _git(root, "tag", "v1.14.0")
            self.assertEqual(1, verify_merge.main(["--root", str(root), "--check-version"]))


class GuardIsWiredTests(unittest.TestCase):
    """The guard must stay wired, because it cannot report its own removal.

    On 2026-08-17 all of #557 was reverted by an unrelated PR: an agent pushed a
    stale working tree onto its branch *after* review, and the squash carried the
    revert under a one-line commit message about something else. Every check
    passed — the thing that would have objected was what got deleted.

    These read the files from disk rather than importing, so they fail on a
    checkout where the guard is gone instead of erroring on an import.
    """

    def _read(self, rel):
        return (Path(__file__).resolve().parent.parent / rel).read_text(encoding="utf-8")

    def test_every_version_marker_is_watched(self):
        # uv.lock was outside the set and sat a release behind on main (#556).
        source = self._read("scripts/verify_merge.py")
        for marker in ("pyproject.toml", "__init__.py", "CHANGELOG.md", "uv.lock"):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)

    def test_the_drift_check_reports_instead_of_discarding(self):
        source = self._read("scripts/verify_merge.py")
        self.assertNotIn("is informational", source)
        self.assertIn("Merge drift: PR #", source)

    def test_ci_runs_the_guard_with_tags_available(self):
        # actions/checkout defaults to fetch-depth: 1 and fetch-tags: false, so
        # without a full checkout the monotonicity comparison silently does
        # nothing while still printing a success line.
        ci = self._read(".github/workflows/ci.yml")
        self.assertIn("version-integrity:", ci)
        self.assertIn("fetch-depth: 0", ci)
        self.assertIn("verify_merge.py --check-version", ci)


if __name__ == "__main__":
    unittest.main()
