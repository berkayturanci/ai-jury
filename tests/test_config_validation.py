"""Coverage for config.validate_config branches (hard errors vs warnings)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import tempfile  # noqa: E402

from ai_jury.config import (  # noqa: E402
    ConfigError,
    load_raw_config,
    validate_config,
)


class ConfigSizeLimit(unittest.TestCase):
    """Issue #316/L-5: a config file is size-capped before tomllib parses it."""

    def test_oversized_config_is_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as fh:
            fh.write("x = 1\n# " + "a" * (5 * 1024 * 1024))  # > 4 MiB
            name = fh.name
        try:
            with self.assertRaises(ConfigError):
                load_raw_config(name)
        finally:
            Path(name).unlink()

    def test_invalid_utf8_config_is_a_clean_error(self):
        # Review of #316: a non-UTF-8 config is a ConfigError, not a raw
        # UnicodeDecodeError stack trace.
        with tempfile.NamedTemporaryFile("wb", suffix=".toml", delete=False) as fh:
            fh.write(b"\xff\xfe not valid utf-8")
            name = fh.name
        try:
            with self.assertRaises(ConfigError):
                load_raw_config(name)
        finally:
            Path(name).unlink()


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
            validate_config(
                {
                    "jury": {"rounds": 1, "chair": "a"},
                    "agent": [{"vendor": "anthropic", "command": "x"}],
                }
            )

    def test_agent_missing_command(self):
        with self.assertRaises(ConfigError):
            validate_config(
                {
                    "jury": {"rounds": 1, "chair": "a"},
                    "agent": [{"name": "a", "vendor": "anthropic"}],
                }
            )

    def test_duplicate_agent_names(self):
        with self.assertRaises(ConfigError):
            validate_config(
                {
                    "jury": {"rounds": 1, "chair": "a"},
                    "agent": [
                        {"name": "a", "vendor": "anthropic", "command": "x"},
                        {"name": "a", "vendor": "openai", "command": "y"},
                    ],
                }
            )

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
        w = validate_config(
            {
                "jury": {"rounds": 1, "chair": "a"},
                "agent": [{"name": "a", "vendor": "anthropic", "command": "x", "weird": 1}],
            }
        )
        self.assertTrue(any("weird" in x for x in w), w)

    def test_unknown_vendor_warns(self):
        w = validate_config(
            {
                "jury": {"rounds": 1, "chair": "a"},
                "agent": [{"name": "a", "vendor": "acme", "command": "x"}],
            }
        )
        self.assertTrue(any("acme" in x or "vendor" in x for x in w), w)

    def test_chair_not_enabled_warns(self):
        w = validate_config(
            {
                "jury": {"rounds": 1, "chair": "ghost"},
                "agent": [{"name": "a", "vendor": "anthropic", "command": "x"}],
            }
        )
        self.assertTrue(any("ghost" in x or "chair" in x for x in w), w)

    def test_local_agent_needs_no_command(self):
        # vendor=local doesn't require a command — should not raise.
        validate_config(
            {
                "jury": {"rounds": 1, "chair": "q"},
                "agent": [
                    {
                        "name": "q",
                        "vendor": "local",
                        "model": "m",
                        "endpoint": "http://localhost:11434/v1",
                    },
                ],
            }
        )

    def test_strict_promotes_warnings(self):
        with self.assertRaises(ConfigError):
            validate_config(_cfg(bogus=1), strict=True)


class CommandPathValidation(unittest.TestCase):
    """Issue #293/F-6: a relative path command is rejected."""

    def _agent(self, command):
        return {
            "jury": {"rounds": 1, "chair": "a"},
            "agent": [{"name": "a", "vendor": "anthropic", "command": command}],
        }

    def test_relative_path_command_is_hard_error(self):
        with self.assertRaises(ConfigError):
            validate_config(self._agent("./tools/claude"))

    def test_relative_subdir_command_is_hard_error(self):
        with self.assertRaises(ConfigError):
            validate_config(self._agent("bin/agy"))

    def test_bare_name_is_allowed(self):
        validate_config(self._agent("claude"))  # resolved on PATH

    def test_absolute_path_is_allowed(self):
        # A POSIX path like /usr/... is NOT absolute on Windows (no drive), so use
        # a platform-appropriate absolute path. The production check correctly
        # treats a drive-less path as relative on Windows.
        abs_path = "C:\\bin\\claude" if os.name == "nt" else "/usr/local/bin/claude"
        validate_config(self._agent(abs_path))

    # Issue #296: opt-in strict absolute-path mode.
    def test_strict_mode_rejects_bare_name(self):
        with (
            mock.patch.dict(os.environ, {"JURY_REQUIRE_ABSOLUTE_COMMAND": "1"}, clear=True),
            self.assertRaises(ConfigError),
        ):
            validate_config(self._agent("claude"))

    def test_strict_mode_allows_absolute(self):
        abs_path = "C:\\bin\\claude" if os.name == "nt" else "/usr/local/bin/claude"
        with mock.patch.dict(os.environ, {"JURY_REQUIRE_ABSOLUTE_COMMAND": "1"}, clear=True):
            validate_config(self._agent(abs_path))

    def test_bare_name_allowed_without_strict_mode(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            validate_config(self._agent("claude"))


class EndpointValidation(unittest.TestCase):
    """Issue #291: local-agent endpoint scheme/host validation (SSRF defense)."""

    def _local(self, endpoint):
        return {
            "jury": {"rounds": 1, "chair": "q"},
            "agent": [{"name": "q", "vendor": "local", "model": "m", "endpoint": endpoint}],
        }

    def test_file_scheme_is_hard_error(self):
        with self.assertRaises(ConfigError):
            validate_config(self._local("file:///etc/passwd"))

    def test_ftp_scheme_is_hard_error(self):
        with self.assertRaises(ConfigError):
            validate_config(self._local("ftp://internal/host"))

    def test_loopback_http_has_no_warning(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            w = validate_config(self._local("http://localhost:11434/v1"))
        self.assertFalse(any("endpoint" in x for x in w), w)

    def test_non_loopback_host_is_hard_error_by_default(self):
        # Review of #291: an attacker-controlled config must not be able to reach
        # a non-loopback host (incl. IMDS) without an out-of-band opt-in.
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(ConfigError):
            validate_config(self._local("http://169.254.169.254/latest/meta-data"))

    def test_malformed_endpoint_is_a_clean_hard_error(self):
        # Issue #315: a URL that makes urlsplit raise (e.g. `http://[::1`) must be
        # a ConfigError, not an uncaught ValueError stack trace.
        with self.assertRaises(ConfigError):
            validate_config(self._local("http://[::1"))

    def test_non_loopback_allowed_with_env_opt_in_warns(self):
        with mock.patch.dict(os.environ, {"JURY_ALLOW_REMOTE_ENDPOINT": "1"}, clear=True):
            w = validate_config(self._local("http://gpu-box.internal:8000/v1"))
        self.assertTrue(any("not loopback" in x for x in w), w)
        self.assertTrue(any("cleartext" in x or "plaintext" in x for x in w), w)


if __name__ == "__main__":
    unittest.main()
