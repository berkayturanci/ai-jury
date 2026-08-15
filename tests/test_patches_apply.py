"""Unit tests for suggested patch parsing and jury apply (issue #521)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

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
                code = main(["apply", "--report", report_path, "1"])
                self.assertEqual(code, 0)
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
