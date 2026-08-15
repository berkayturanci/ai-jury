"""Unit tests for tiered model routing and hints (issue #524, #523)."""

from __future__ import annotations

import io
import sys
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_jury.cli import main  # noqa: E402
from ai_jury.config import JuryConfig, _from_dict  # noqa: E402


class TieredRoutingTests(unittest.TestCase):
    def test_tiered_routing_config_field(self):
        cfg = JuryConfig(routing="tiered")
        self.assertEqual(cfg.routing, "tiered")

    def test_tiered_routing_toml_parsing(self):
        toml_content = """
        [jury]
        routing = "tiered"
        hints = true
        """
        data = tomllib.loads(toml_content)
        cfg = _from_dict(data)
        self.assertEqual(cfg.routing, "tiered")
        self.assertTrue(cfg.hints)

    def test_cli_tiered_and_hints_flags(self):
        with patch("ai_jury.hints.collect_static_hints", return_value="## Hints"):
            prev_stdin = sys.stdin
            sys.stdin = io.StringIO("diff --git a/a.py b/a.py\n+x = 1\n")
            try:
                code = main(["--mock", "--diff-file", "-", "--tiered", "--hints"])
                self.assertEqual(code, 0)
            finally:
                sys.stdin = prev_stdin


if __name__ == "__main__":
    unittest.main()
