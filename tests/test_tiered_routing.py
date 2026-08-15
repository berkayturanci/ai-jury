"""Unit tests for tiered model routing (issue #524)."""

from __future__ import annotations

import sys
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

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


if __name__ == "__main__":
    unittest.main()
