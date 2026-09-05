"""Coverage for config.validate_config branches (hard errors vs warnings)."""

from __future__ import annotations

import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import tempfile  # noqa: E402

from ai_jury.config import (  # noqa: E402
    DEFAULT_CONFIG,
    KNOWN_AGENT_KEYS,
    KNOWN_EFFORTS,
    ConfigError,
    _from_dict,
    config_hash,
    load_raw_config,
    validate_config,
)
from ai_jury.findings import SEVERITY_INPUTS  # noqa: E402


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


class ApiKeyEnvNameValidation(unittest.TestCase):
    """`api_key_env` names an env var and is displayed, so it is bounded (#669)."""

    @staticmethod
    def _with(value):
        return {
            "jury": {"rounds": 1, "chair": "a"},
            "agent": [
                {
                    "name": "a",
                    "vendor": "openai-compatible",
                    "model": "m",
                    "endpoint": "http://localhost:9/v1",
                    "api_key_env": value,
                }
            ],
        }

    def test_a_valid_name_is_accepted_silently(self):
        self.assertEqual(validate_config(self._with("MY_TOKEN_VAR")), [])

    def test_a_malformed_name_warns_rather_than_failing(self):
        # Soft, not hard: the vendor default still works, but a silent fallback
        # would leave the operator wondering why their variable is ignored.
        warnings = validate_config(self._with('EVIL", "injected": "yes'))
        self.assertEqual(len(warnings), 1)
        self.assertIn("is not a valid environment variable name", warnings[0])

    def test_the_rejected_value_is_never_quoted_back(self):
        # Reaching this branch means the value holds characters outside the safe
        # set — the very class that must not be written to a terminal. The
        # message locates the problem (agent name + rule) without reproducing it.
        for hostile, marker in (
            ('EVIL", "injected": "yes', "injected"),
            ("TWO\nLINES", "TWO"),
            ("ansi\x1b[31mred", "\x1b"),
        ):
            warning = validate_config(self._with(hostile))[0]
            self.assertNotIn(marker, warning, hostile)
            self.assertIn("api_key_env", warning)
            self.assertIn("agent 'a'", warning)

    def test_the_message_states_the_rule_it_enforces(self):
        from ai_jury.redaction import ENV_VAR_NAME_RULE

        self.assertIn(ENV_VAR_NAME_RULE, validate_config(self._with("bad name"))[0])

    def test_a_newline_in_the_name_warns(self):
        self.assertTrue(validate_config(self._with("TWO\nLINES")))

    def test_absent_key_produces_no_warning(self):
        data = self._with("X")
        del data["agent"][0]["api_key_env"]
        self.assertEqual(validate_config(data), [])


class EffortValidation(unittest.TestCase):
    """`[[agent]] effort` is a closed enum — a typo is a hard error (issue #662)."""

    @staticmethod
    def _with_effort(value):
        return {
            "jury": {"rounds": 1, "chair": "a"},
            "agent": [{"name": "a", "vendor": "openai-api", "model": "m", "effort": value}],
        }

    def test_every_known_level_is_accepted(self):
        for level in KNOWN_EFFORTS:
            self.assertEqual(validate_config(self._with_effort(level)), [])

    def test_case_and_padding_are_tolerated(self):
        self.assertEqual(validate_config(self._with_effort("  HIGH ")), [])

    def test_unknown_level_is_a_hard_error(self):
        with self.assertRaises(ConfigError) as ctx:
            validate_config(self._with_effort("maximum"))
        self.assertIn("effort must be one of", str(ctx.exception))

    def test_non_string_effort_is_a_hard_error(self):
        for value in (3, True, ["high"]):
            with self.assertRaises(ConfigError):
                validate_config(self._with_effort(value))

    def test_effort_is_a_known_agent_key(self):
        # Not merely accepted: it must not produce an "unknown key" warning.
        self.assertIn("effort", KNOWN_AGENT_KEYS)
        self.assertEqual(validate_config(self._with_effort("low")), [])

    def test_effort_is_normalized_and_hashed(self):
        cfg = _from_dict(self._with_effort(" High "))
        self.assertEqual(cfg.agents[0].effort, "high")
        other = _from_dict(self._with_effort("low"))
        # Effort changes the request an agent sends, so it must split the cache key.
        self.assertNotEqual(config_hash(cfg), config_hash(other))

    def test_absent_effort_stays_none(self):
        cfg = _from_dict(
            {"jury": {"rounds": 1, "chair": "a"}, "agent": [{"name": "a", "command": "x"}]}
        )
        self.assertIsNone(cfg.agents[0].effort)


class FailOnVocabulary(unittest.TestCase):
    """`[jury.ci] fail_on` is a closed vocabulary — a typo is a hard error (#718).

    A misspelled severity matches no finding group, so the one setting that
    decides whether CI fails would pass green forever, quoting the typo back.
    """

    @staticmethod
    def _with_fail_on(value):
        return {
            "jury": {"rounds": 1, "chair": "a", "ci": {"fail_on": value}},
            "agent": [{"name": "a", "vendor": "openai", "command": "codex"}],
        }

    def test_every_known_severity_is_accepted(self):
        self.assertEqual(validate_config(self._with_fail_on(list(SEVERITY_INPUTS))), [])

    def test_case_and_padding_are_tolerated(self):
        self.assertEqual(validate_config(self._with_fail_on([" CRITICAL ", "Blocker"])), [])

    def test_a_scalar_is_accepted_because_the_loader_wraps_one(self):
        self.assertEqual(validate_config(self._with_fail_on("critical")), [])
        self.assertEqual(_from_dict(self._with_fail_on("critical")).ci.fail_on, ["critical"])

    def test_empty_list_is_accepted(self):
        # An explicitly empty gate blocks nothing, which is a choice, not a typo.
        self.assertEqual(validate_config(self._with_fail_on([])), [])

    def test_typo_is_a_hard_error_naming_the_value(self):
        with self.assertRaises(ConfigError) as ctx:
            validate_config(self._with_fail_on(["crticial", "majr"]))
        message = str(ctx.exception)
        self.assertIn("jury.ci.fail_on", message)
        self.assertIn("crticial", message)
        self.assertIn("majr", message)
        self.assertIn("critical, major, minor, nit, info, blocker", message)

    def test_a_typo_beside_a_valid_severity_is_still_refused(self):
        # The gate would half-work, which is the shape that hides for months.
        with self.assertRaises(ConfigError):
            validate_config(self._with_fail_on(["critical", "hihg"]))

    def test_scalar_typo_is_a_hard_error(self):
        with self.assertRaises(ConfigError):
            validate_config(self._with_fail_on("majr"))

    def test_absent_ci_table_is_accepted(self):
        data = {
            "jury": {"rounds": 1, "chair": "a"},
            "agent": [{"name": "a", "vendor": "openai", "command": "codex"}],
        }
        self.assertEqual(validate_config(data), [])

    def test_non_table_ci_is_a_hard_error(self):
        data = {
            "jury": {"rounds": 1, "chair": "a", "ci": "critical"},
            "agent": [{"name": "a", "vendor": "openai", "command": "codex"}],
        }
        with self.assertRaises(ConfigError) as ctx:
            validate_config(data)
        self.assertIn("[jury.ci] must be a table", str(ctx.exception))

    def test_the_shipped_default_config_passes_its_own_gate(self):
        self.assertEqual(validate_config(DEFAULT_CONFIG), [])


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
            unittest.mock.patch.dict(
                os.environ, {"JURY_REQUIRE_ABSOLUTE_COMMAND": "1"}, clear=True
            ),
            self.assertRaises(ConfigError),
        ):
            validate_config(self._agent("claude"))

    def test_strict_mode_allows_absolute(self):
        abs_path = "C:\\bin\\claude" if os.name == "nt" else "/usr/local/bin/claude"
        with unittest.mock.patch.dict(
            os.environ, {"JURY_REQUIRE_ABSOLUTE_COMMAND": "1"}, clear=True
        ):
            validate_config(self._agent(abs_path))

    def test_bare_name_allowed_without_strict_mode(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
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
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            w = validate_config(self._local("http://localhost:11434/v1"))
        self.assertFalse(any("endpoint" in x for x in w), w)

    def test_non_loopback_host_is_hard_error_by_default(self):
        # Review of #291: an attacker-controlled config must not be able to reach
        # a non-loopback host (incl. IMDS) without an out-of-band opt-in.
        with unittest.mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(ConfigError):
            validate_config(self._local("http://169.254.169.254/latest/meta-data"))

    def test_malformed_endpoint_is_a_clean_hard_error(self):
        # Issue #315: a URL that makes urlsplit raise (e.g. `http://[::1`) must be
        # a ConfigError, not an uncaught ValueError stack trace.
        with self.assertRaises(ConfigError):
            validate_config(self._local("http://[::1"))

    def test_non_loopback_allowed_with_env_opt_in_warns(self):
        with unittest.mock.patch.dict(os.environ, {"JURY_ALLOW_REMOTE_ENDPOINT": "1"}, clear=True):
            w = validate_config(self._local("http://gpu-box.internal:8000/v1"))
        self.assertTrue(any("not loopback" in x for x in w), w)
        self.assertTrue(any("cleartext" in x or "plaintext" in x for x in w), w)


if __name__ == "__main__":
    unittest.main()
