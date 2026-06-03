"""Coverage for small branch gaps: metadata rounds fallback, cache dir
resolution, and comment-parse error paths. Network-free."""
from __future__ import annotations

import os
import sys
import unittest
import unittest.mock as mock
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import cache, commands, metadata  # noqa: E402


class RoundsExecutedFallback(unittest.TestCase):
    def test_infers_two_from_phases(self):
        o = SimpleNamespace(rounds_executed=None, reviews=["r"], debate=["d"])
        self.assertEqual(metadata._rounds_executed(o), 2)

    def test_reviews_only_is_one(self):
        o = SimpleNamespace(rounds_executed=None, reviews=["r"], debate=[])
        self.assertEqual(metadata._rounds_executed(o), 1)

    def test_nothing_is_zero(self):
        o = SimpleNamespace(rounds_executed=None, reviews=[], debate=[])
        self.assertEqual(metadata._rounds_executed(o), 0)

    def test_recorded_count_wins(self):
        o = SimpleNamespace(rounds_executed=3, reviews=["r"], debate=[])
        self.assertEqual(metadata._rounds_executed(o), 3)


class DefaultCacheDir(unittest.TestCase):
    def test_env_override(self):
        with mock.patch.dict(os.environ, {"JURY_CACHE_DIR": "/tmp/jc"}, clear=False):
            self.assertEqual(cache.default_cache_dir(), Path("/tmp/jc"))

    def test_xdg_cache_home(self):
        env = dict(os.environ)
        env.pop("JURY_CACHE_DIR", None)
        env["XDG_CACHE_HOME"] = "/tmp/xdg"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(cache.default_cache_dir(), Path("/tmp/xdg/ai-jury"))

    def test_home_fallback(self):
        env = dict(os.environ)
        env.pop("JURY_CACHE_DIR", None)
        env.pop("XDG_CACHE_HOME", None)
        with mock.patch.dict(os.environ, env, clear=True):
            # OS-portable: compare Path objects, not a POSIX-slash string.
            self.assertEqual(cache.default_cache_dir(), Path.home() / ".cache" / "ai-jury")


class ParseCommentErrors(unittest.TestCase):
    def test_empty(self):
        with self.assertRaises(commands.CommandError):
            commands.parse_comment("")

    def test_no_jury_command(self):
        with self.assertRaises(commands.CommandError):
            commands.parse_comment("just a normal PR comment")

    def test_unsupported_command(self):
        with self.assertRaises(commands.CommandError):
            commands.parse_comment("/jury bogus")

    def test_rounds_requires_a_value(self):
        with self.assertRaises(commands.CommandError):
            commands.parse_comment("/jury review --rounds")

    def test_rounds_non_integer(self):
        with self.assertRaises(commands.CommandError):
            commands.parse_comment("/jury review --rounds notanint")

    def test_valid_review(self):
        self.assertEqual(commands.parse_comment("/jury review").command, "review")

    def test_valid_summary(self):
        self.assertEqual(commands.parse_comment("/jury summary").command, "summary")


if __name__ == "__main__":
    unittest.main()
