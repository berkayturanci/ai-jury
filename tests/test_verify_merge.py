"""Tests for scripts/verify_merge.py."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

# Dynamically import verify_merge script
script_path = Path(__file__).resolve().parent.parent / "scripts" / "verify_merge.py"
spec = importlib.util.spec_from_file_location("verify_merge", script_path)
verify_merge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_merge)


class VerifyMergeTests(unittest.TestCase):
    def test_parse_semver(self):
        self.assertEqual(verify_merge.parse_semver("1.14.0"), (1, 14, 0))
        self.assertEqual(verify_merge.parse_semver("v1.14.0"), (1, 14, 0))
        self.assertEqual(verify_merge.parse_semver("v2.0.1"), (2, 0, 1))
        with self.assertRaises(ValueError):
            verify_merge.parse_semver("invalid-version")

    def test_version_integrity_clean(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pyproject.toml").write_text('[project]\nversion = "1.14.0"\n', encoding="utf-8")
            (root / "src" / "ai_jury").mkdir(parents=True)
            (root / "src" / "ai_jury" / "__init__.py").write_text('__version__ = "1.14.0"\n', encoding="utf-8")
            (root / "CHANGELOG.md").write_text("## [1.14.0] - 2026-08-16\n", encoding="utf-8")

            errors = verify_merge.check_version_integrity(root)
            self.assertEqual(errors, [])

    def test_version_integrity_mismatch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pyproject.toml").write_text('[project]\nversion = "1.14.0"\n', encoding="utf-8")
            (root / "src" / "ai_jury").mkdir(parents=True)
            (root / "src" / "ai_jury" / "__init__.py").write_text('__version__ = "1.13.0"\n', encoding="utf-8")
            (root / "CHANGELOG.md").write_text("## [1.14.0] - 2026-08-16\n", encoding="utf-8")

            errors = verify_merge.check_version_integrity(root)
            self.assertTrue(any("mismatch" in e.lower() for e in errors))

    def test_version_integrity_missing_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            errors = verify_merge.check_version_integrity(root)
            self.assertTrue(len(errors) >= 3)

    def test_cli_execution(self):
        repo_root = Path(__file__).resolve().parent.parent
        exit_code = verify_merge.main(["--root", str(repo_root), "--check-version"])
        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
