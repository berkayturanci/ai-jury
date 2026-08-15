"""Unit tests for suggested patch parsing and jury apply (issue #521)."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_jury.cli import main  # noqa: E402
from ai_jury.patches import (  # noqa: E402
    PatchSuggestion,
    apply_patch_suggestion,
    parse_patch_suggestions,
)

SAMPLE_PATCH_REPORT = """# 🏛️ AI Jury

## Suggested patches

### src/auth.py:10 — [critical] SQL injection vulnerability

> Verified by the jury.

```suggestion
cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
```

### src/utils.py:25 — [minor] missing type annotation

> Verified by the jury.

```suggestion
def add(a: int, b: int) -> int:
```
"""


class PatchesApplyTests(unittest.TestCase):
    def test_parse_patch_suggestions(self):
        suggestions = parse_patch_suggestions(SAMPLE_PATCH_REPORT)
        self.assertEqual(len(suggestions), 2)
        self.assertEqual(suggestions[0].file, "src/auth.py")
        self.assertEqual(suggestions[0].line, 10)
        self.assertEqual(suggestions[0].severity, "critical")
        self.assertIn("SQL injection", suggestions[0].claim)
        self.assertIn("cursor.execute", suggestions[0].suggested_fix)

        self.assertEqual(suggestions[1].file, "src/utils.py")
        self.assertEqual(suggestions[1].line, 25)

    def test_apply_patch_suggestion_line_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            auth_file = tmp_path / "src" / "auth.py"
            auth_file.parent.mkdir(parents=True)
            auth_file.write_text(
                "\n".join([f"# line {i}" for i in range(1, 10)] + ['cursor.execute(f"SELECT * FROM users WHERE username = {username}")'])
                + "\n",
                encoding="utf-8",
            )

            s = PatchSuggestion(
                file="src/auth.py",
                line=10,
                severity="critical",
                claim="SQL injection",
                suggested_fix='cursor.execute("SELECT * FROM users WHERE username = %s", (username,))',
            )

            ok, msg = apply_patch_suggestion(s, root_dir=tmp_path)
            self.assertTrue(ok, f"Failed to apply: {msg}")
            content = auth_file.read_text(encoding="utf-8")
            self.assertIn("(username,)", content)

    def test_apply_patch_suggestion_path_traversal_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            s = PatchSuggestion(
                file="../../etc/passwd",
                line=1,
                severity="critical",
                claim="path traversal",
                suggested_fix="root:x:0:0:::",
            )
            ok, msg = apply_patch_suggestion(s, root_dir=tmp_path)
            self.assertFalse(ok)
            self.assertIn("Path traversal rejected", msg)

    def test_apply_patch_suggestion_file_not_found(self):
        s = PatchSuggestion(
            file="nonexistent.py",
            line=1,
            severity="minor",
            claim="test",
            suggested_fix="pass",
        )
        ok, msg = apply_patch_suggestion(s)
        self.assertFalse(ok)
        self.assertIn("File not found", msg)

    def test_apply_patch_suggestion_line_out_of_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            f = tmp_path / "test.py"
            f.write_text("x = 1\n", encoding="utf-8")
            s = PatchSuggestion(
                file="test.py",
                line=999,
                severity="minor",
                claim="test",
                suggested_fix="pass",
            )
            ok, msg = apply_patch_suggestion(s, root_dir=tmp_path)
            self.assertFalse(ok)
            self.assertIn("Cannot apply non-diff suggestion", msg)

    def test_apply_patch_suggestion_git_apply_success_and_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            f = tmp_path / "test.py"
            f.write_text("x = 1\n", encoding="utf-8")
            s = PatchSuggestion(
                file="test.py",
                line=None,
                severity="minor",
                claim="test",
                suggested_fix="--- a/test.py\n+++ b/test.py\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n",
            )
            mock_ok = MagicMock(returncode=0)
            with patch("subprocess.run", return_value=mock_ok):
                ok, msg = apply_patch_suggestion(s, root_dir=tmp_path)
                self.assertTrue(ok)
                self.assertIn("Applied git patch", msg)

            mock_fail = MagicMock(returncode=1, stderr="error: corrupt patch")
            with patch("subprocess.run", return_value=mock_fail):
                ok, msg = apply_patch_suggestion(s, root_dir=tmp_path)
                self.assertFalse(ok)
                self.assertIn("Git apply failed", msg)

    def test_cli_apply_subcommand_dispatch(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(SAMPLE_PATCH_REPORT)
            report_path = f.name

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "src").mkdir()
            (tmp_path / "src" / "auth.py").write_text("\n" * 15, encoding="utf-8")
            (tmp_path / "src" / "utils.py").write_text("\n" * 30, encoding="utf-8")

            old_cwd = Path.cwd()
            try:
                os.chdir(tmp_path)
                # Apply specific index
                code = main(["apply", "--report", report_path, "1"])
                self.assertEqual(code, 0)

                # Apply all
                code_all = main(["apply", "--report", report_path, "all"])
                self.assertEqual(code_all, 0)

                # Invalid index
                code_bad_idx = main(["apply", "--report", report_path, "99"])
                self.assertEqual(code_bad_idx, 2)

                # Non-numeric invalid index
                code_invalid = main(["apply", "--report", report_path, "abc"])
                self.assertEqual(code_invalid, 2)
            finally:
                os.chdir(old_cwd)

    def test_cli_apply_failures_on_nonexistent_files(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(SAMPLE_PATCH_REPORT)
            report_path = f.name

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Do NOT create src/auth.py or src/utils.py -> applying will fail
            old_cwd = Path.cwd()
            try:
                os.chdir(tmp_path)
                code_fail_all = main(["apply", "--report", report_path, "all"])
                self.assertEqual(code_fail_all, 1)

                code_fail_single = main(["apply", "--report", report_path, "1"])
                self.assertEqual(code_fail_single, 1)
            finally:
                os.chdir(old_cwd)

    def test_cli_apply_errors(self):
        # Missing report
        code = main(["apply", "--report", "/nonexistent/report.md"])
        self.assertEqual(code, 2)

        # Empty report stdin
        with (
            patch("sys.stdin", io.StringIO("No patches here")),
            patch("sys.stdin.isatty", return_value=False),
        ):
            code = main(["apply"])
            self.assertEqual(code, 1)

        # No report and isatty
        with patch("sys.stdin.isatty", return_value=True):
            code = main(["apply"])
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
