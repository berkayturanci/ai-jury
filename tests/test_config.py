# pyright: reportMissingImports=false
"""Tests for config schema validation."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_jury.config import (  # noqa: E402
    DEFAULT_CONFIG,
    ConfigError,
    _from_dict,
    config_hash,
    load_config,
    validate_config,
)


def _valid() -> dict:
    return copy.deepcopy(DEFAULT_CONFIG)


class TestValidateConfig(unittest.TestCase):
    def test_default_config_passes(self):
        self.assertEqual(validate_config(_valid()), [])

    def test_rounds_zero_raises(self):
        data = _valid()
        data["jury"]["rounds"] = 0
        with self.assertRaises(ConfigError):
            validate_config(data)

    def test_timeout_negative_raises(self):
        data = _valid()
        data["jury"]["timeout"] = -1
        with self.assertRaises(ConfigError):
            validate_config(data)

    def test_duplicate_agent_names_raise(self):
        data = _valid()
        data["agent"][1]["name"] = data["agent"][0]["name"]
        with self.assertRaises(ConfigError):
            validate_config(data)

    def test_empty_command_raises(self):
        data = _valid()
        data["agent"][0]["command"] = ""
        with self.assertRaises(ConfigError):
            validate_config(data)

    def test_empty_agent_list_raises(self):
        data = _valid()
        data["agent"] = []
        with self.assertRaises(ConfigError):
            validate_config(data)

    def test_unknown_vendor_warns(self):
        data = _valid()
        data["agent"][0]["vendor"] = "acme"
        warnings = validate_config(data)
        self.assertTrue(any("vendor" in w for w in warnings))

    def test_unknown_vendor_strict_raises(self):
        data = _valid()
        data["agent"][0]["vendor"] = "acme"
        with self.assertRaises(ConfigError):
            validate_config(data, strict=True)

    def test_chair_mismatch_warns(self):
        data = _valid()
        data["jury"]["chair"] = "nobody"
        warnings = validate_config(data)
        self.assertTrue(any("chair" in w for w in warnings))

    def test_chair_mismatch_strict_raises(self):
        data = _valid()
        data["jury"]["chair"] = "nobody"
        with self.assertRaises(ConfigError):
            validate_config(data, strict=True)

    def test_unknown_top_level_key_warns(self):
        data = _valid()
        data["bogus"] = {"x": 1}
        warnings = validate_config(data)
        self.assertTrue(any("bogus" in w for w in warnings))


class TestLoadConfig(unittest.TestCase):
    def test_load_default_still_works(self):
        cfg = load_config()
        self.assertEqual(cfg.rounds, 2)
        self.assertTrue(cfg.agents)

    def test_load_default_with_validation(self):
        cfg = load_config(validate=True)
        self.assertEqual(cfg.rounds, 2)


class TheaterConfigTests(unittest.TestCase):
    """jury.toml theater defaults (issue #364)."""

    def test_defaults_off_flat(self):
        cfg = _from_dict({})
        self.assertFalse(cfg.theater)
        self.assertEqual(cfg.theater_style, "flat")

    def test_parsed_from_jury_table(self):
        cfg = _from_dict({"jury": {"theater": True, "theater_style": "PIXEL"}})
        self.assertTrue(cfg.theater)
        self.assertEqual(cfg.theater_style, "pixel")   # normalised lower

    def test_rendering_only_excluded_from_config_hash(self):
        base = _from_dict({})
        themed = _from_dict({"jury": {"theater": True, "theater_style": "pixel"}})
        self.assertEqual(config_hash(base), config_hash(themed))

    def test_validate_rejects_bad_values(self):
        bad = _valid()
        bad["jury"]["theater"] = "yes"
        with self.assertRaises(ConfigError):
            validate_config(bad)
        bad2 = _valid()
        bad2["jury"]["theater_style"] = "ascii"
        with self.assertRaises(ConfigError):
            validate_config(bad2)
        ok = _valid()
        ok["jury"]["theater"] = True
        ok["jury"]["theater_style"] = "pixel"
        self.assertEqual(validate_config(ok), [])


if __name__ == "__main__":
    unittest.main()
