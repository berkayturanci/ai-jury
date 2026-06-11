"""Tests for risk-aware auto-depth diff profiling (issue #120). Network-free."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury.diffprofile import (  # noqa: E402
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    depth_for,
    profile_diff,
)


def _seg(path: str, added: int = 1) -> str:
    body = "".join(f"+line {i}\n" for i in range(added))
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1,{added} @@\n{body}"
    )


class ProfileTest(unittest.TestCase):
    def test_tiny_code_diff_is_low(self):
        p = profile_diff(_seg("src/a.py", 3))
        self.assertEqual(p.risk, RISK_LOW)
        self.assertFalse(p.security_sensitive)

    def test_docs_only_is_low(self):
        p = profile_diff(_seg("docs/guide.md", 200) + _seg("README.md", 50))
        self.assertTrue(p.docs_or_generated_only)
        self.assertEqual(p.risk, RISK_LOW)

    def test_security_path_forces_high_even_if_tiny(self):
        p = profile_diff(_seg("src/auth/login.py", 2))
        self.assertTrue(p.security_sensitive)
        self.assertEqual(p.risk, RISK_HIGH)

    def test_large_diff_is_high(self):
        p = profile_diff(_seg("src/big.py", 500))
        self.assertEqual(p.risk, RISK_HIGH)

    def test_many_files_is_high(self):
        diff = "".join(_seg(f"src/m{i}.py", 2) for i in range(25))
        self.assertEqual(profile_diff(diff).risk, RISK_HIGH)

    def test_moderate_diff_is_medium(self):
        p = profile_diff(_seg("src/a.py", 60) + _seg("src/b.py", 60))
        self.assertEqual(p.risk, RISK_MEDIUM)

    def test_author_path_does_not_trip_auth_keyword(self):
        # word-boundary: "author" must not match the "auth" security keyword.
        p = profile_diff(_seg("src/author.py", 3))
        self.assertFalse(p.security_sensitive)


class DepthTest(unittest.TestCase):
    def test_low_is_shallow(self):
        self.assertEqual(depth_for(RISK_LOW), (1, False, False))

    def test_medium_debate_no_verify_early_stop(self):
        self.assertEqual(depth_for(RISK_MEDIUM), (2, False, True))

    def test_high_is_full(self):
        self.assertEqual(depth_for(RISK_HIGH), (2, True, False))


class AutoDepthCliTest(unittest.TestCase):
    """The CLI applies auto-depth and explicit flags override it."""

    def _run_capture_config(self, argv, stdin):
        # Patch review_diff to capture the resolved config instead of running.
        import contextlib
        import io
        from unittest import mock

        import ai_jury.cli as cli

        captured = {}

        def fake_review(config, diff, **kw):
            del diff, kw
            captured["config"] = config
            raise SystemExit(0)  # stop before rendering

        with mock.patch.object(cli, "review_diff", side_effect=fake_review):
            old = sys.stdin
            sys.stdin = io.StringIO(stdin)
            try:
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                    contextlib.suppress(SystemExit),
                ):
                    cli.main(argv)
            finally:
                sys.stdin = old
        return captured.get("config")

    def test_auto_low_diff_goes_shallow(self):
        diff = _seg("src/a.py", 3)
        cfg = self._run_capture_config(["--mock", "--diff-file", "-", "--auto"], stdin=diff)
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.rounds, 1)
        self.assertFalse(cfg.verify)

    def test_auto_high_diff_goes_full(self):
        diff = _seg("src/auth/login.py", 5)
        cfg = self._run_capture_config(["--mock", "--diff-file", "-", "--auto"], stdin=diff)
        self.assertEqual(cfg.rounds, 2)
        self.assertTrue(cfg.verify)

    def test_explicit_rounds_overrides_auto(self):
        diff = _seg("src/a.py", 3)  # would be low -> rounds 1
        cfg = self._run_capture_config(
            ["--mock", "--diff-file", "-", "--auto", "--rounds", "2"], stdin=diff
        )
        self.assertEqual(cfg.rounds, 2)  # explicit wins


if __name__ == "__main__":
    unittest.main()
