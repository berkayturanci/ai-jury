"""End-to-end CLI coverage via the offline ``--mock`` path. Invokes
``cli.main([...])`` and asserts stdout/exit for the major flag surfaces.
Network-free and deterministic (mock agents)."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import cli  # noqa: E402


def _tmpdir(case: unittest.TestCase) -> Path:
    """A temp directory that is removed when the test case finishes."""
    d = tempfile.mkdtemp()
    case.addCleanup(shutil.rmtree, d, ignore_errors=True)
    return Path(d)

DIFF = """diff --git a/app.py b/app.py
index 0000000..1111111 100644
--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
 def f(x):
-    return x
+    return x + 1  # changed
+    # trailing
"""


def run(args, stdin=None):
    out, err = io.StringIO(), io.StringIO()
    prev = sys.stdin
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    code = None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(args)
    except SystemExit as exc:
        code = exc.code
    finally:
        sys.stdin = prev
    return code, out.getvalue(), err.getvalue()


class CliMockTests(unittest.TestCase):
    def setUp(self):
        self.d = _tmpdir(self)
        self.diff = self.d / "x.diff"
        self.diff.write_text(DIFF)

    def test_markdown_default(self):
        code, out, _ = run(["--mock", "--diff-file", str(self.diff), "-q"])
        self.assertEqual(code, 0)
        self.assertIn("AI Jury", out)

    def test_stdin_diff(self):
        code, out, _ = run(["--mock", "--diff-file", "-", "-q"], stdin=DIFF)
        self.assertEqual(code, 0)
        self.assertIn("AI Jury", out)

    def test_json_format_to_file(self):
        outp = self.d / "r.json"
        code, _, _ = run(
            ["--mock", "--diff-file", str(self.diff), "-q", "--format", "json", "-o", str(outp)]
        )
        self.assertEqual(code, 0)
        data = json.loads(outp.read_text())
        self.assertIn("schema_version", data)
        self.assertIn("findings", data)

    def test_sarif_format_stdout(self):
        code, out, _ = run(["--mock", "--diff-file", str(self.diff), "-q", "--format", "sarif"])
        self.assertEqual(code, 0)
        sarif = json.loads(out)
        self.assertIn("runs", sarif)

    def test_metadata_json(self):
        mp = self.d / "m.json"
        code, _, _ = run(
            ["--mock", "--diff-file", str(self.diff), "-q", "--metadata-json", str(mp)]
        )
        self.assertEqual(code, 0)
        meta = json.loads(mp.read_text())
        self.assertIn("rounds_executed", meta)

    def test_ci_mode_returns_gate_code(self):
        # --seed makes the mock run reproducible; assert a valid CLI exit (the
        # point is to exercise the --ci path, not pin a specific verdict).
        code, _, _ = run(
            [
                "--mock",
                "--diff-file",
                str(self.diff),
                "-q",
                "--seed",
                "1",
                "--ci",
                "--fail-on",
                "critical,major",
            ]
        )
        self.assertIsInstance(code, int)
        self.assertIn(code, (0, 1, 2))

    def test_no_input_errors(self):
        code, _, _ = run([])
        self.assertNotEqual(code, 0)

    def test_doctor_runs(self):
        code, out, _ = run(["--doctor"])
        self.assertIn("ready to run", out)

    def test_cache_clear_temp_dir(self):
        code, out, _ = run(["cache", "clear", "--cache-dir", str(self.d)])
        self.assertEqual(code, 0)
        self.assertIn("Cleared", out)

    def test_config_validate_valid(self):
        cfg = self.d / "jury.toml"
        cfg.write_text(
            '[jury]\nrounds = 1\nchair = "a"\n\n[[agent]]\nname = "a"\nvendor = "anthropic"\ncommand = "x"\n'
        )
        code, _, _ = run(["--config-validate", "--config", str(cfg)])
        self.assertEqual(code, 0)

    def test_config_validate_invalid(self):
        cfg = self.d / "bad.toml"
        cfg.write_text(
            '[jury]\nrounds = 0\n\n[[agent]]\nname = "a"\nvendor = "anthropic"\ncommand = "x"\n'
        )
        code, _, _ = run(["--config-validate", "--config", str(cfg)])
        self.assertEqual(code, 2)

    def test_version(self):
        code, out, _ = run(["--version"])
        self.assertEqual(code, 0)
        self.assertIn("jury", out)

    def test_suggest_patches_section(self):
        code, out, _ = run(["--mock", "--diff-file", str(self.diff), "-q", "--suggest-patches"])
        self.assertEqual(code, 0)
        self.assertIn("AI Jury", out)


class CliOverviewTests(unittest.TestCase):
    """#265: friendly first-impression surface."""

    def test_examples_subcommand(self):
        code, out, _ = run(["examples"])
        self.assertEqual(code, 0)
        self.assertIn("example commands", out)
        self.assertIn("jury --pr 123", out)

    def test_guide_subcommand(self):
        code, out, _ = run(["guide"])
        self.assertEqual(code, 0)
        self.assertIn("walkthrough", out)
        self.assertIn("jury init --wizard", out)

    def test_bare_jury_in_tty_prints_overview_and_exits_zero(self):
        import unittest.mock as m

        fake = m.MagicMock()
        fake.isatty.return_value = True
        with m.patch.object(sys, "stdin", fake):
            code, out, _ = run([])
        self.assertEqual(code, 0)
        self.assertIn("ai-jury", out)
        self.assertIn("Common commands", out)

    def test_examples_with_trailing_args_is_not_silently_ignored(self):
        # `jury examples foo` must NOT print the overview-and-exit-0; it falls
        # through to argparse and errors (no silent arg-swallowing).
        code, out, _ = run(["examples", "foo"])
        self.assertNotEqual(code, 0)
        self.assertNotIn("example commands", out)

    def test_bare_jury_with_none_stdin_does_not_crash(self):
        # sys.stdin can be None under detached I/O; the isatty() guard must not
        # raise AttributeError — it falls through to the normal missing-input path.
        import unittest.mock as m

        with m.patch.object(sys, "stdin", None):
            code, _, _ = run([])
        self.assertNotEqual(code, 0)  # missing-input error, not a crash

    def test_bare_jury_non_interactive_still_errors(self):
        # Piped/CI (no TTY) must keep the strict error + non-zero exit so a script
        # that forgot an input fails loudly instead of printing a welcome.
        import unittest.mock as m

        fake = m.MagicMock()
        fake.isatty.return_value = False
        fake.read.return_value = ""
        with m.patch.object(sys, "stdin", fake):
            code, out, _ = run([])
        self.assertNotEqual(code, 0)
        self.assertNotIn("Common commands", out)


if __name__ == "__main__":
    unittest.main()
