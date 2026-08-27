"""A failing git command must not carry its stderr into an exception unredacted.

`_git_diff` had two adjacent error paths and only one of them redacted: the
spawn failure did, the non-zero exit did not (#631). `redact()` does catch what
this is aimed at, so the inconsistency was worth closing.

Severity is [LOW] and the test says why: only `git show` and `git diff` reach
this path, both purely local, so neither can print a remote URL carrying a
credential. This is defence in depth against a future caller that runs
something which can — not a fix for an observed leak. Filed as CRITICAL and
corrected on review; #600/#608 is the precedent for why that distinction is
worth keeping in the record.
"""

from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from ai_jury import cli

TOKEN = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"


def failing_git(stderr: str):
    """A `subprocess.run` that fails the way git does, with `stderr` on the pipe."""

    def run(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(args=["git"], returncode=128, stdout="", stderr=stderr)

    return run


class TheGitErrorPathRedacts(unittest.TestCase):
    def _message(self, stderr: str) -> str:
        with (
            mock.patch.object(subprocess, "run", failing_git(stderr)),
            self.assertRaises(SystemExit) as caught,
        ):
            cli._git_diff(["git", "diff", "HEAD"], "range HEAD")
        return str(caught.exception)

    def test_a_token_in_git_stderr_does_not_reach_the_exception(self):
        message = self._message(
            f"fatal: unable to access 'https://x-token:{TOKEN}@github.com/o/r.git/': 403\n"
        )
        self.assertNotIn(TOKEN, message)
        self.assertIn("REDACTED", message)

    def test_the_ordinary_error_still_reaches_the_operator(self):
        """Redaction must not turn a readable git error into noise.

        This is the half a redaction change usually breaks: the common case is
        `fatal: ambiguous argument`, which carries no secret and is the only
        thing telling the operator what they typed wrong.
        """
        message = self._message("fatal: ambiguous argument 'nope': unknown revision\n")
        self.assertIn("ambiguous argument", message)
        self.assertIn("nope", message)

    def test_only_the_first_stderr_line_is_quoted(self):
        """Unchanged behaviour, pinned so the redaction fix cannot widen it."""
        message = self._message("first line\nsecond line\nthird line\n")
        self.assertIn("first line", message)
        self.assertNotIn("second line", message)

    def test_an_empty_stderr_leaves_a_bare_message(self):
        message = self._message("")
        self.assertIn("git could not resolve", message)
        self.assertTrue(message.rstrip().endswith("range HEAD"), message)
