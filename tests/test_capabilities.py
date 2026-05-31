"""Offline tests for adapter capability/version detection.

Run with: python -m unittest discover -s tests
No third-party deps, no real agent CLIs, no network. Subprocess calls and
``shutil.which`` are monkeypatched so detection runs against FAKE CLIs only.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_review_council import adapters  # noqa: E402
from agent_review_council.config import AgentSpec  # noqa: E402


class _FakeCompleted:
    """Stand-in for subprocess.CompletedProcess."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _spec(**kw):
    base = dict(name="claude", vendor="anthropic", command="claude")
    base.update(kw)
    return AgentSpec(**base)


class CapabilityDetectionTests(unittest.TestCase):
    def setUp(self):
        # By default pretend the CLI is on PATH; individual tests override.
        self._orig_which = adapters.shutil.which
        self._orig_run = adapters.subprocess.run
        adapters.shutil.which = lambda cmd: "/usr/bin/" + cmd
        self.addCleanup(self._restore)

    def _restore(self):
        adapters.shutil.which = self._orig_which
        adapters.subprocess.run = self._orig_run

    def _patch_run(self, fn):
        adapters.subprocess.run = fn

    def test_parses_version_from_stdout(self):
        self._patch_run(
            lambda *a, **k: _FakeCompleted(stdout="claude 1.2.3 (build abc)\n")
        )
        caps = adapters.ClaudeAdapter(_spec()).detect_capabilities()
        self.assertEqual(caps["version"], "1.2.3")
        self.assertEqual(caps["status"], adapters.CAP_OK)
        self.assertTrue(caps["supports_headless"])
        self.assertTrue(caps["supports_model_selection"])
        self.assertEqual(caps["warnings"], [])

    def test_parses_two_component_version(self):
        self._patch_run(lambda *a, **k: _FakeCompleted(stdout="v0.45\n"))
        caps = adapters.ClaudeAdapter(_spec()).detect_capabilities()
        self.assertEqual(caps["version"], "0.45")
        self.assertEqual(caps["status"], adapters.CAP_OK)

    def test_parses_version_from_stderr(self):
        # Some CLIs print --version to stderr.
        self._patch_run(
            lambda *a, **k: _FakeCompleted(returncode=0, stdout="", stderr="codex 2.0.1\n")
        )
        caps = adapters.CodexAdapter(
            _spec(name="codex", vendor="openai", command="codex")
        ).detect_capabilities()
        self.assertEqual(caps["version"], "2.0.1")
        self.assertEqual(caps["status"], adapters.CAP_OK)

    def test_unavailable_skips_subprocess(self):
        adapters.shutil.which = lambda cmd: None

        def _boom(*a, **k):  # pragma: no cover - must never be called
            raise AssertionError("subprocess.run called for unavailable CLI")

        self._patch_run(_boom)
        caps = adapters.ClaudeAdapter(_spec()).detect_capabilities()
        self.assertEqual(caps["status"], adapters.CAP_UNAVAILABLE)
        self.assertIsNone(caps["version"])

    def test_timeout_yields_unknown_version_no_raise(self):
        def _timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd="claude", timeout=10)

        self._patch_run(_timeout)
        caps = adapters.ClaudeAdapter(_spec()).detect_capabilities()
        self.assertEqual(caps["status"], adapters.CAP_UNKNOWN_VERSION)
        self.assertIsNone(caps["version"])
        self.assertTrue(caps["warnings"])

    def test_spawn_failure_yields_unknown_version_no_raise(self):
        def _fnf(*a, **k):
            raise FileNotFoundError("no such file")

        self._patch_run(_fnf)
        caps = adapters.ClaudeAdapter(_spec()).detect_capabilities()
        self.assertEqual(caps["status"], adapters.CAP_UNKNOWN_VERSION)
        self.assertTrue(caps["warnings"])

    def test_nonzero_exit_yields_unknown_version(self):
        self._patch_run(
            lambda *a, **k: _FakeCompleted(returncode=1, stderr="usage: ...")
        )
        caps = adapters.ClaudeAdapter(_spec()).detect_capabilities()
        self.assertEqual(caps["status"], adapters.CAP_UNKNOWN_VERSION)
        self.assertIsNone(caps["version"])
        self.assertTrue(caps["warnings"])

    def test_garbage_output_yields_unknown_version(self):
        # Exit 0 but no version-looking token.
        self._patch_run(
            lambda *a, **k: _FakeCompleted(returncode=0, stdout="hello world\n")
        )
        caps = adapters.ClaudeAdapter(_spec()).detect_capabilities()
        self.assertEqual(caps["status"], adapters.CAP_UNKNOWN_VERSION)
        self.assertIsNone(caps["version"])

    def test_raw_output_is_truncated(self):
        self._patch_run(
            lambda *a, **k: _FakeCompleted(stdout="1.0 " + "x" * 5000)
        )
        caps = adapters.ClaudeAdapter(_spec()).detect_capabilities()
        self.assertLessEqual(len(caps["raw_version_output"]), 200)

    def test_version_argv_uses_command_and_flag(self):
        adapter = adapters.ClaudeAdapter(_spec(command="claude"))
        self.assertEqual(adapter._version_argv(), ["claude", "--version"])


class MockAdapterCapabilityTests(unittest.TestCase):
    def test_mock_returns_stable_fake_caps(self):
        caps = adapters.MockAdapter(
            _spec(name="mock", vendor="anthropic", command="mock")
        ).detect_capabilities()
        self.assertEqual(caps["version"], "mock-1.0")
        self.assertEqual(caps["status"], adapters.CAP_OK)
        self.assertTrue(caps["supports_headless"])
        self.assertFalse(caps["supports_model_selection"])
        self.assertEqual(caps["warnings"], [])

    def test_mock_does_not_spawn(self):
        # Even with subprocess.run rigged to explode, the mock never calls it.
        orig = adapters.subprocess.run

        def _boom(*a, **k):  # pragma: no cover
            raise AssertionError("mock must not spawn a subprocess")

        adapters.subprocess.run = _boom
        try:
            caps = adapters.MockAdapter(
                _spec(name="mock", vendor="anthropic", command="mock")
            ).detect_capabilities()
        finally:
            adapters.subprocess.run = orig
        self.assertEqual(caps["status"], adapters.CAP_OK)


if __name__ == "__main__":
    unittest.main()
