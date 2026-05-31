"""Offline tests for ``council --doctor`` diagnostics.

Run with: python -m unittest discover -s tests
No third-party dependencies, no live agent CLIs, no network.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_review_council import doctor  # noqa: E402


VALID_CONFIG = """\
[council]
rounds = 2
chair = "claude"

[council.context]
mode = "diff-only"

[[agent]]
name = "claude"
vendor = "anthropic"
command = "definitely-not-a-real-cli-xyz"
enabled = true

[[agent]]
name = "codex"
vendor = "openai"
command = "definitely-not-a-real-cli-abc"
enabled = false
"""

# A config carrying a fake secret in string fields. The doctor summary must
# redact it so it never appears in any rendered output or JSON.
SECRET = "sk-ABCDEF0123456789ABCDEF0123456789secretvalue"
SECRET_CONFIG = """\
[council]
rounds = 1
chair = "token={secret}"

[council.context]
mode = "diff-only"

[[agent]]
name = "claude"
vendor = "anthropic"
command = "ls"
enabled = true
""".format(secret=SECRET)


def _write_config(text):
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".toml", delete=False, encoding="utf-8"
    )
    tmp.write(text)
    tmp.close()
    return tmp.name


class BuildDiagnosticsTests(unittest.TestCase):
    def test_has_expected_keys(self):
        path = _write_config(VALID_CONFIG)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)

        for key in (
            "tool_version",
            "python_version",
            "os",
            "agents",
            "config",
            "config_warnings",
        ):
            self.assertIn(key, diag)

        self.assertIsInstance(diag["agents"], list)
        self.assertIsInstance(diag["config_warnings"], list)
        self.assertEqual(diag["config"]["rounds"], 2)
        self.assertEqual(diag["config"]["chair"], "claude")
        self.assertEqual(diag["config"]["context_mode"], "diff-only")
        # Only enabled agents are summarised.
        self.assertEqual(diag["config"]["enabled_agents"], ["claude"])

    def test_agent_availability_reflects_path(self):
        path = _write_config(VALID_CONFIG)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)

        by_name = {a["name"]: a for a in diag["agents"]}
        # Bogus commands are never on PATH.
        self.assertFalse(by_name["claude"]["available"])
        self.assertFalse(by_name["codex"]["available"])

    def test_real_command_is_available(self):
        config = """\
[council]
rounds = 1
chair = "claude"

[[agent]]
name = "claude"
vendor = "anthropic"
command = "ls"
enabled = true
"""
        path = _write_config(config)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)
        self.assertTrue(diag["agents"][0]["available"])

    def test_invalid_config_is_best_effort(self):
        path = _write_config("this is not = valid = toml [[[")
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)
        # Does not raise; captures the problem and leaves config None.
        self.assertIsNone(diag["config"])
        self.assertTrue(diag["config_warnings"])

    def test_missing_config_is_best_effort(self):
        diag = doctor.build_diagnostics("/nonexistent/path/council.toml")
        self.assertIsNone(diag["config"])
        self.assertTrue(diag["config_warnings"])

    def test_chair_mismatch_warning(self):
        config = """\
[council]
rounds = 1
chair = "nonexistent-chair"

[[agent]]
name = "claude"
vendor = "anthropic"
command = "ls"
enabled = true
"""
        path = _write_config(config)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)
        self.assertTrue(
            any("chair" in w for w in diag["config_warnings"]),
            diag["config_warnings"],
        )


class RedactionTests(unittest.TestCase):
    def test_secret_not_in_diagnostics(self):
        path = _write_config(SECRET_CONFIG)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)
        self.assertNotIn(SECRET, json.dumps(diag))

    def test_secret_not_in_text_report(self):
        path = _write_config(SECRET_CONFIG)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)
        report = doctor.render_report(diag)
        self.assertNotIn(SECRET, report)


class SafetyTests(unittest.TestCase):
    def test_no_raw_diff_or_agent_output_keys(self):
        path = _write_config(VALID_CONFIG)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)

        forbidden = {"diff", "raw_diff", "agent_output", "output", "responses"}
        self.assertEqual(forbidden & set(diag.keys()), set())

    def test_diff_content_never_leaks(self):
        path = _write_config(VALID_CONFIG)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)
        # A marker that would only exist in a diff is absent.
        self.assertNotIn("diff --git", json.dumps(diag))


class RenderReportTests(unittest.TestCase):
    def test_renders_readable_text(self):
        path = _write_config(VALID_CONFIG)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)
        report = doctor.render_report(diag)

        self.assertIn("council doctor", report)
        self.assertIn("Agents", report)
        self.assertIn("Config summary", report)
        self.assertIn("no telemetry", report.lower())


if __name__ == "__main__":
    unittest.main()
