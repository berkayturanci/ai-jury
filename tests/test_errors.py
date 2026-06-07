"""Offline tests for the typed error taxonomy on agent execution.

Run with: python -m unittest discover -s tests
No third-party dependencies, no live CLIs, no network. Subprocess is faked or
monkeypatched so no real agent CLI is ever spawned.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import adapters  # noqa: E402
from ai_jury.adapters import (  # noqa: E402
    ERR_AUTH_REQUIRED,
    ERR_EMPTY_OUTPUT,
    ERR_MISSING_CLI,
    ERR_NONZERO_EXIT,
    ERR_PERMISSION_PROMPT,
    ERR_RATE_LIMITED,
    ERR_SPAWN_FAILED,
    ERR_TIMEOUT,
    ERROR_CODES,
    Adapter,
    classify_stderr,
)
from ai_jury.config import AgentSpec  # noqa: E402


def _spec(command: str = "definitely-not-a-real-cli-xyz") -> AgentSpec:
    return AgentSpec(name="t", vendor="anthropic", command=command, timeout=5)


class _FakeAdapter(Adapter):
    """Adapter that always reports available so run() reaches subprocess."""

    def available(self) -> bool:
        return True

    def build_argv(self, prompt: str) -> list[str]:
        del prompt
        return [self.spec.command]


class _Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class ClassifyStderrTest(unittest.TestCase):
    def test_auth_signals(self):
        for s in (
            "Error: not authenticated",
            "401 Unauthorized",
            "Please provide an API key",
            "run `claude login` first",
            "auth token expired",
        ):
            self.assertEqual(classify_stderr(1, s), ERR_AUTH_REQUIRED, s)

    def test_rate_limit_signals(self):
        for s in ("rate limit exceeded", "HTTP 429 Too Many Requests", "quota exceeded"):
            self.assertEqual(classify_stderr(1, s), ERR_RATE_LIMITED, s)

    def test_permission_signals(self):
        for s in (
            "permission required to edit files",
            "please approve this action",
            "confirm to continue",
        ):
            self.assertEqual(classify_stderr(1, s), ERR_PERMISSION_PROMPT, s)

    def test_generic_nonzero(self):
        for s in ("Traceback: KeyError", "segmentation fault", ""):
            self.assertEqual(classify_stderr(2, s), ERR_NONZERO_EXIT, s)

    def test_incidental_substrings_not_auth(self):
        # Words that merely CONTAIN "auth"/"login" must not be read as auth
        # signals; they fall through to the generic nonzero-exit code.
        for s in (
            "fatal: author identity unknown",
            "the authoritative source is unreachable",
            "login_attempts table is locked",
        ):
            self.assertEqual(classify_stderr(2, s), ERR_NONZERO_EXIT, s)

    def test_incidental_substrings_other_groups(self):
        # "quotation" contains "quota"; "iterate" contains "rate";
        # "approved by"/"disconfirming" contain "approve"/"confirm".
        for s in (
            "missing quotation mark in template",
            "failed to iterate over results",
            "change approved by reviewer bot",
            "disconfirming heuristic disabled",
        ):
            self.assertEqual(classify_stderr(2, s), ERR_NONZERO_EXIT, s)

    def test_all_codes_registered(self):
        for code in (
            ERR_AUTH_REQUIRED,
            ERR_RATE_LIMITED,
            ERR_PERMISSION_PROMPT,
            ERR_NONZERO_EXIT,
        ):
            self.assertIn(code, ERROR_CODES)


class RunClassificationTest(unittest.TestCase):
    def test_missing_cli(self):
        # Command not on PATH -> early missing_cli without spawning anything.
        res = _MissingAdapter(_spec()).run("prompt")
        self.assertFalse(res.ok)
        self.assertEqual(res.error_code, ERR_MISSING_CLI)

    def test_timeout(self):
        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="x", timeout=5)

        with _patched(fake_run):
            res = _FakeAdapter(_spec()).run("prompt")
        self.assertFalse(res.ok)
        self.assertEqual(res.error_code, ERR_TIMEOUT)

    def test_spawn_failed(self):
        def fake_run(*args, **kwargs):
            raise OSError("boom")

        with _patched(fake_run):
            res = _FakeAdapter(_spec()).run("prompt")
        self.assertFalse(res.ok)
        self.assertEqual(res.error_code, ERR_SPAWN_FAILED)

    def test_nonzero_exit_auth(self):
        def fake_run(*args, **kwargs):
            return _Completed(1, stdout="", stderr="not authenticated")

        with _patched(fake_run):
            res = _FakeAdapter(_spec()).run("prompt")
        self.assertFalse(res.ok)
        self.assertEqual(res.error_code, ERR_AUTH_REQUIRED)

    def test_empty_output_exit_zero(self):
        def fake_run(*args, **kwargs):
            return _Completed(0, stdout="   \n", stderr="")

        with _patched(fake_run):
            res = _FakeAdapter(_spec()).run("prompt")
        self.assertFalse(res.ok)
        self.assertEqual(res.error_code, ERR_EMPTY_OUTPUT)

    def test_success_has_no_error_code(self):
        def fake_run(*args, **kwargs):
            return _Completed(0, stdout="real review content", stderr="")

        with _patched(fake_run):
            res = _FakeAdapter(_spec()).run("prompt")
        self.assertTrue(res.ok)
        self.assertIsNone(res.error_code)

    def test_nonzero_exit_with_stdout_is_failure(self):
        # Issue #101: a nonzero exit must be a failure even when the CLI printed
        # something — partial/error output must not count as a clean review.
        def fake_run(*args, **kwargs):
            return _Completed(1, stdout="partial review then crash", stderr="")

        with _patched(fake_run):
            res = _FakeAdapter(_spec()).run("prompt")
        self.assertFalse(res.ok)
        self.assertEqual(res.output, "")
        self.assertIn(res.error_code, ERROR_CODES)
        self.assertEqual(res.error_code, ERR_NONZERO_EXIT)

    def test_nonzero_exit_with_stdout_classifies_from_stderr(self):
        # stderr still drives classification when present, even with stdout.
        def fake_run(*args, **kwargs):
            return _Completed(1, stdout="some output", stderr="rate limit exceeded")

        with _patched(fake_run):
            res = _FakeAdapter(_spec()).run("prompt")
        self.assertFalse(res.ok)
        self.assertEqual(res.error_code, ERR_RATE_LIMITED)


class _MissingAdapter(_FakeAdapter):
    def available(self) -> bool:
        return False


class _patched:
    """Context manager that monkeypatches adapters._spawn (the run() seam)."""

    def __init__(self, fn):
        self.fn = fn
        self._orig = None

    def __enter__(self):
        self._orig = adapters._spawn
        adapters._spawn = self.fn
        return self

    def __exit__(self, *exc):
        adapters._spawn = self._orig
        return False


if __name__ == "__main__":
    unittest.main()
