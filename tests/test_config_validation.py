"""Coverage for config.validate_config branches (hard errors vs warnings)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury.config import ConfigError, validate_config  # noqa: E402


def _cfg(**jury_over):
    jury = {"rounds": 2, "chair": "a"}
    jury.update(jury_over)
    return {"jury": jury, "agent": [{"name": "a", "vendor": "anthropic", "command": "x"}]}


class HardErrors(unittest.TestCase):
    def test_root_not_dict(self):
        with self.assertRaises(ConfigError):
            validate_config("not a dict")  # type: ignore[arg-type]

    def test_jury_not_table(self):
        with self.assertRaises(ConfigError):
            validate_config({"jury": "x", "agent": [{"name": "a", "command": "x"}]})

    def test_rounds_below_one(self):
        with self.assertRaises(ConfigError):
            validate_config(_cfg(rounds=0))

    def test_timeout_non_positive(self):
        with self.assertRaises(ConfigError):
            validate_config(_cfg(timeout=0))

    def test_retries_negative(self):
        with self.assertRaises(ConfigError):
            validate_config(_cfg(retries=-1))

    def test_max_rounds_bad(self):
        with self.assertRaises(ConfigError):
            validate_config(_cfg(max_rounds=0))

    def test_total_timeout_bad(self):
        with self.assertRaises(ConfigError):
            validate_config(_cfg(total_timeout=0))

    def test_diff_not_table(self):
        with self.assertRaises(ConfigError):
            validate_config(_cfg(diff="x"))

    def test_agent_not_array(self):
        with self.assertRaises(ConfigError):
            validate_config({"jury": {"rounds": 1}, "agent": "x"})

    def test_agent_not_table(self):
        with self.assertRaises(ConfigError):
            validate_config({"jury": {"rounds": 1}, "agent": ["nope"]})

    def test_agent_missing_name(self):
        with self.assertRaises(ConfigError):
            validate_config({"jury": {"rounds": 1, "chair": "a"},
                             "agent": [{"vendor": "anthropic", "command": "x"}]})

    def test_agent_missing_command(self):
        with self.assertRaises(ConfigError):
            validate_config({"jury": {"rounds": 1, "chair": "a"},
                             "agent": [{"name": "a", "vendor": "anthropic"}]})

    def test_duplicate_agent_names(self):
        with self.assertRaises(ConfigError):
            validate_config({"jury": {"rounds": 1, "chair": "a"}, "agent": [
                {"name": "a", "vendor": "anthropic", "command": "x"},
                {"name": "a", "vendor": "openai", "command": "y"},
            ]})

    def test_no_agents(self):
        with self.assertRaises(ConfigError):
            validate_config({"jury": {"rounds": 1}, "agent": []})


class SoftWarnings(unittest.TestCase):
    def test_valid_returns_no_warnings(self):
        self.assertEqual(validate_config(_cfg()), [])

    def test_unknown_jury_key_warns(self):
        w = validate_config(_cfg(bogus=1))
        self.assertTrue(any("bogus" in x for x in w), w)

    def test_unknown_agent_key_warns(self):
        w = validate_config({"jury": {"rounds": 1, "chair": "a"},
                             "agent": [{"name": "a", "vendor": "anthropic", "command": "x", "weird": 1}]})
        self.assertTrue(any("weird" in x for x in w), w)

    def test_unknown_vendor_warns(self):
        w = validate_config({"jury": {"rounds": 1, "chair": "a"},
                             "agent": [{"name": "a", "vendor": "acme", "command": "x"}]})
        self.assertTrue(any("acme" in x or "vendor" in x for x in w), w)

    def test_chair_not_enabled_warns(self):
        w = validate_config({"jury": {"rounds": 1, "chair": "ghost"},
                             "agent": [{"name": "a", "vendor": "anthropic", "command": "x"}]})
        self.assertTrue(any("ghost" in x or "chair" in x for x in w), w)

    def test_local_agent_needs_no_command(self):
        # vendor=local doesn't require a command — should not raise.
        validate_config({"jury": {"rounds": 1, "chair": "q"}, "agent": [
            {"name": "q", "vendor": "local", "model": "m", "endpoint": "http://localhost:11434/v1"},
        ]})

    def test_strict_promotes_warnings(self):
        with self.assertRaises(ConfigError):
            validate_config(_cfg(bogus=1), strict=True)


if __name__ == "__main__":
    unittest.main()
