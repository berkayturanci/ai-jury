"""Coverage for config.validate_config branches (hard errors vs warnings)."""

from __future__ import annotations

import os
import re
import sys
import tomllib
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import tempfile  # noqa: E402

from ai_jury.config import (
    DEFAULT_CONFIG,
    KNOWN_AGENT_KEYS,
    KNOWN_CI_KEYS,
    KNOWN_CONTEXT_KEYS,
    KNOWN_DIFF_KEYS,
    KNOWN_EFFORTS,
    KNOWN_JURY_KEYS,
    KNOWN_NESTED_JURY_KEYS,
    AgentSpec,
    ConfigError,
    _ci_from_dict,
    _context_from_dict,
    _diff_from_dict,
    _from_dict,
    config_hash,
    load_config,
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


class TierValidation(unittest.TestCase):
    """`[[agent]] tier` is `frontier` or `economical`, and nothing else (#714).

    Hard, like `effort`, and for the same reason: an unknown spelling read as
    the default would treat the seat as frontier — the opposite of what an
    operator who wrote `tier = "cheap"` meant.
    """

    @staticmethod
    def _with_tier(value):
        agent = {"name": "a", "vendor": "anthropic", "command": "claude"}
        if value is not None:
            agent["tier"] = value
        return {"jury": {"rounds": 1, "chair": "a"}, "agent": [agent]}

    def test_the_two_kinds_are_accepted_case_insensitively(self):
        for value, expected in (("frontier", "frontier"), ("Economical", "economical")):
            with self.subTest(value=value):
                data = self._with_tier(value)
                self.assertEqual(validate_config(data), [])
                self.assertEqual(_from_dict(data).agents[0].tier, expected)

    def test_unset_means_frontier(self):
        data = self._with_tier(None)
        self.assertEqual(validate_config(data), [])
        self.assertEqual(_from_dict(data).agents[0].tier, "frontier")

    def test_an_unknown_kind_is_a_hard_error_naming_the_agent(self):
        for value in ("cheap", 3, ""):
            with self.subTest(value=value):
                with self.assertRaises(ConfigError) as ctx:
                    validate_config(self._with_tier(value))
                self.assertIn(
                    "agent 'a' tier must be one of frontier, economical", str(ctx.exception)
                )

    def test_the_spec_never_carries_an_unknown_spelling(self):
        # The reader normalises on construction, so no rule downstream can be
        # handed a tier the vocabulary does not have.
        self.assertEqual(AgentSpec("a", "anthropic", tier=" ECONOMICAL ").tier, "economical")
        self.assertEqual(AgentSpec("a", "anthropic", tier="cheap").tier, "frontier")

    def test_economical_splits_the_cache_key_and_the_default_does_not(self):
        base = _from_dict(self._with_tier(None))
        explicit_default = _from_dict(self._with_tier("frontier"))
        economical = _from_dict(self._with_tier("economical"))
        self.assertEqual(config_hash(base), config_hash(explicit_default))
        self.assertNotEqual(config_hash(base), config_hash(economical))


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


class HeadersValidation(unittest.TestCase):
    """`[[agent]] headers` is checked, and the severity follows usability (#716).

    The old behaviour is what makes the shape errors hard rather than soft:
    `_from_dict` coerced every non-table to `{}`, so a misspelled or mis-typed
    `headers` passed `--config-validate` AND `--strict-config` and the seat ran
    with no extra headers at all. For a routing header the provider honours by
    default, that is a run against a backend nobody chose.

    The split is this module's usual one, applied here (review r1): a shape that
    cannot become headers is a hard error (not a table; a non-string key), and a
    shape that becomes working headers by a documented coercion is a warning (a
    non-string VALUE — `X-Retries = 3` is sent as `X-Retries: 3`), exactly as a
    malformed `api_key_env` name warns and falls back.
    """

    @staticmethod
    def _with_headers(value):
        return {
            "jury": {"rounds": 1, "chair": "a"},
            "agent": [
                {
                    "name": "a",
                    "vendor": "openai-compatible",
                    "model": "m",
                    "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
                    "headers": value,
                }
            ],
        }

    def test_a_table_of_strings_is_accepted_and_materialised(self):
        data = self._with_headers({"X-Route": "premium", "HTTP-Referer": "https://ai-jury.org"})
        self.assertEqual(validate_config(data), [])
        self.assertEqual(
            _from_dict(data).agents[0].headers,
            {"X-Route": "premium", "HTTP-Referer": "https://ai-jury.org"},
        )

    def test_a_string_is_a_hard_error_naming_the_agent(self):
        # The issue's own reproduction: TOML written as `headers = "A = B"`.
        with self.assertRaises(ConfigError) as ctx:
            validate_config(self._with_headers("Authorization = Bearer y"))
        message = str(ctx.exception)
        self.assertIn("agent 'a' headers must be a table", message)
        self.assertIn("got str", message)

    def test_a_list_is_a_hard_error(self):
        with self.assertRaises(ConfigError) as ctx:
            validate_config(self._with_headers([["X-Route", "premium"]]))
        self.assertIn("agent 'a' headers must be a table", str(ctx.exception))

    def test_a_non_string_value_warns_and_is_coerced(self):
        """`X-Retries = 3` is a working header, so it warns rather than fails."""
        data = self._with_headers({"X-Retries": 3})
        warnings = validate_config(data)
        self.assertEqual(len(warnings), 1)
        self.assertIn("agent 'a' headers value for 'X-Retries'", warnings[0])
        self.assertIn("int", warnings[0])
        self.assertIn("coerced", warnings[0])
        # And the coercion the warning describes is the one that happens.
        self.assertEqual(_from_dict(data).agents[0].headers, {"X-Retries": "3"})

    def test_a_non_string_value_is_fatal_only_under_strict(self):
        """Where an operator asks for a warning to be fatal — `--strict-config`."""
        with self.assertRaises(ConfigError) as ctx:
            validate_config(self._with_headers({"X-Retries": 3}), strict=True)
        self.assertIn("agent 'a' headers value for 'X-Retries'", str(ctx.exception))

    def test_a_non_string_header_name_is_a_hard_error(self):
        """A non-string key cannot be a header name, so it cannot be coerced.

        tomllib never produces one — every TOML key, bare or quoted, parses to
        `str`, so `3 = "premium"` is a *bare* key spelled `"3"` — which is why
        this rule guards a config dict built in Python (an embedder calling
        `validate_config` / `_from_dict` directly, or a test like this one)
        rather than anything a written `jury.toml` can express.
        """
        self.assertEqual(list(tomllib.loads('[a]\n3 = "premium"\n')["a"]), ["3"])
        with self.assertRaises(ConfigError) as ctx:
            validate_config(self._with_headers({3: "premium"}))
        self.assertIn("agent 'a' headers has a non-string header name", str(ctx.exception))

    def test_the_offending_value_is_never_echoed_back(self):
        """A header is where a credential lives; no message may carry one."""
        secret = "Bearer sk-secret-42"
        warnings = validate_config(self._with_headers({"Authorization": ["Bearer sk-secret-42"]}))
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("sk-secret-42", warnings[0])
        with self.assertRaises(ConfigError) as ctx:
            validate_config(self._with_headers(f"Authorization = {secret}"))
        self.assertNotIn("sk-secret-42", str(ctx.exception))
        with self.assertRaises(ConfigError) as ctx:
            _from_dict(self._with_headers(f"Authorization = {secret}"))
        self.assertNotIn("sk-secret-42", str(ctx.exception))

    def test_materialising_a_non_table_raises_the_config_error_not_an_attribute_error(self):
        """The unvalidated path must fail as a config error, not a traceback.

        `load_config` defaults to `validate=False` and `jury run-agent` keeps it
        that way on purpose, so `_from_dict` is reached with a raw dict nothing
        checked. Dropping the old `isinstance` guard would otherwise turn a
        string `headers` into `'str' object has no attribute 'items'` there.
        """
        for value in ("Authorization = Bearer y", [["X-Route", "premium"]], 3):
            with self.subTest(value=value), self.assertRaises(ConfigError) as ctx:
                _from_dict(self._with_headers(value))
            self.assertIn("agent 'a' headers must be a table", str(ctx.exception))

    def test_materialising_a_non_string_key_raises_the_config_error(self):
        """`_from_dict` classifies exactly as `validate_config` does."""
        with self.assertRaises(ConfigError) as ctx:
            _from_dict(self._with_headers({3: "premium"}))
        self.assertIn("agent 'a' headers has a non-string header name", str(ctx.exception))

    def test_an_unnamed_agent_is_still_located_in_the_message(self):
        """`_from_dict` runs before `name` is read, so it falls back to the index."""
        data = self._with_headers("nope")
        del data["agent"][0]["name"]
        with self.assertRaises(ConfigError) as ctx:
            _from_dict(data)
        self.assertIn("agent 'agent[0]' headers must be a table", str(ctx.exception))

    def test_loading_an_unvalidated_file_reports_the_config_error(self):
        """End-to-end on the default `load_config(validate=False)` path."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jury.toml"
            path.write_text(
                '[jury]\nchair = "a"\n\n[[agent]]\nname = "a"\nvendor = "openai-compatible"\n'
                'model = "m"\nheaders = "Authorization = Bearer y"\n',
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
        self.assertIn("agent 'a' headers must be a table", str(ctx.exception))
        self.assertIn("got str", str(ctx.exception))

    def test_absent_headers_stay_an_empty_table(self):
        data = self._with_headers({})
        del data["agent"][0]["headers"]
        self.assertEqual(validate_config(data), [])
        self.assertEqual(_from_dict(data).agents[0].headers, {})

    def test_headers_split_the_cache_key(self):
        """Two seats differing only in a routing header are not the same run."""
        premium = _from_dict(self._with_headers({"X-Route": "premium"}))
        cheap = _from_dict(self._with_headers({"X-Route": "cheap"}))
        none = _from_dict(self._with_headers({}))
        self.assertNotEqual(config_hash(premium), config_hash(cheap))
        self.assertNotEqual(config_hash(premium), config_hash(none))

    def test_header_order_does_not_split_the_cache_key(self):
        """The digest is a function of the mapping, not of how it was written."""
        one = _from_dict(self._with_headers({"A": "1", "B": "2"}))
        other = _from_dict(self._with_headers({"B": "2", "A": "1"}))
        self.assertEqual(config_hash(one), config_hash(other))

    def test_api_key_env_splits_the_cache_key(self):
        """A different key can mean a different account, hence a different run."""
        base = self._with_headers({})
        first = dict(base, agent=[dict(base["agent"][0], api_key_env="ROUTER_A_KEY")])
        second = dict(base, agent=[dict(base["agent"][0], api_key_env="ROUTER_B_KEY")])
        self.assertNotEqual(config_hash(_from_dict(first)), config_hash(_from_dict(second)))
        self.assertNotEqual(config_hash(_from_dict(first)), config_hash(_from_dict(base)))


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


class _RecordingTable(dict):
    """A config table that records every key a ``*_from_dict`` reader asks for.

    This is how the ``KNOWN_*_KEYS`` tuples are pinned to reality: they are
    derived from the dataclass fields, but what an operator may legitimately
    write is whatever the READER reads. Should a reader ever grow an alias (an
    old key name kept working, say) the tuple would still list only the fields
    and the alias would start warning as if it were a typo. Recording the reads
    catches that divergence at the one place it can be seen.
    """

    def __init__(self):
        super().__init__()
        self.read: set = set()

    def get(self, key, default=None):
        self.read.add(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.read.add(key)
        return super().__getitem__(key)


class NestedJuryTableUnknownKeys(unittest.TestCase):
    """Issue #719: a typo inside `[jury.ci]`/`[jury.context]`/`[jury.diff]` warns.

    The unknown-key check used to stop at the top level: `[jury] roundz` warned,
    but `[jury.ci] min_vendor = 3` — the cross-vendor gate, one `s` short — was
    read by nobody, dropped by `_ci_from_dict`, and passed `--config-validate
    --strict-config` clean while the panel ran on the default of 2.
    """

    @staticmethod
    def _with_nested(table, body):
        return {
            "jury": {"rounds": 1, "chair": "a", table: body},
            "agent": [{"name": "a", "vendor": "anthropic", "command": "claude"}],
        }

    def test_ci_typo_warns_with_the_dotted_path(self):
        # The reported reproduction: `min_vendors` minus its `s`.
        w = validate_config(self._with_nested("ci", {"min_vendor": 3}))
        self.assertEqual(len(w), 1, w)
        self.assertIn("unknown key 'jury.ci.min_vendor'", w[0])
        self.assertIn("min_vendors", w[0])

    def test_context_typo_warns_with_the_dotted_path(self):
        w = validate_config(self._with_nested("context", {"redact_secretz": False}))
        self.assertEqual(len(w), 1, w)
        self.assertIn("unknown key 'jury.context.redact_secretz'", w[0])
        self.assertIn("redact_secrets", w[0])

    def test_diff_typo_warns_with_the_dotted_path(self):
        w = validate_config(self._with_nested("diff", {"max_bytez": 10}))
        self.assertEqual(len(w), 1, w)
        self.assertIn("unknown key 'jury.diff.max_bytez'", w[0])
        self.assertIn("max_bytes", w[0])

    def test_the_issue_reproduction_now_warns_once_per_typo(self):
        data = {
            "jury": {
                "rounds": 1,
                "chair": "a",
                "ci": {"min_vendor": 3, "fail_onn": ["critical"]},
                "context": {"redact_secretz": False},
                "diff": {"max_bytez": 10},
            },
            "agent": [{"name": "a", "vendor": "anthropic", "command": "claude"}],
        }
        self.assertEqual(len(validate_config(data)), 4)

    def test_strict_promotes_a_nested_typo_to_an_error(self):
        for table, body in (
            ("ci", {"min_vendor": 3}),
            ("context", {"redact_secretz": False}),
            ("diff", {"max_bytez": 10}),
        ):
            with self.subTest(table=table):
                with self.assertRaises(ConfigError) as ctx:
                    validate_config(self._with_nested(table, body), strict=True)
                self.assertIn(f"jury.{table}.", str(ctx.exception))

    def test_every_known_nested_key_is_accepted(self):
        for table, keys in KNOWN_NESTED_JURY_KEYS.items():
            with self.subTest(table=table):
                # Values that pass the per-key shape rules; only the NAMES matter.
                body = {key: [] if key in ("fail_on", "exclude", "include") else 1 for key in keys}
                self.assertEqual(validate_config(self._with_nested(table, body)), [])

    def test_an_empty_nested_table_is_accepted(self):
        for table in KNOWN_NESTED_JURY_KEYS:
            with self.subTest(table=table):
                self.assertEqual(validate_config(self._with_nested(table, {})), [])

    def test_a_non_table_is_a_hard_error_and_reports_no_unknown_keys(self):
        # Every nested table is checked with the same wording (issue #729), and
        # none of them is iterated for unknown keys: a string would otherwise
        # report one unknown key per character.
        for table, scalar in (
            ("ci", "critical"),
            ("context", "diff-only"),
            ("diff", 4096),
        ):
            with self.subTest(table=table):
                with self.assertRaises(ConfigError) as ctx:
                    validate_config(self._with_nested(table, scalar))
                message = str(ctx.exception)
                self.assertIn(f"[jury.{table}] must be a table", message)
                self.assertNotIn("unknown key", message)

    def test_the_known_key_tuples_match_what_the_readers_read(self):
        for reader, known in (
            (_ci_from_dict, KNOWN_CI_KEYS),
            (_context_from_dict, KNOWN_CONTEXT_KEYS),
            (_diff_from_dict, KNOWN_DIFF_KEYS),
        ):
            with self.subTest(reader=reader.__name__):
                table = _RecordingTable()
                reader(table)
                self.assertEqual(table.read, set(known))

    def test_the_tables_are_exactly_the_nested_ones_jury_reads(self):
        # Every other `KNOWN_JURY_KEYS` member is a scalar, so this dict is the
        # complete set of sub-tables a typo could disappear into.
        defaults = _from_dict({"jury": {}, "agent": []})
        nested = {
            name
            for name in KNOWN_JURY_KEYS
            if hasattr(getattr(defaults, name, None), "__dataclass_fields__")
        }
        self.assertEqual(nested, set(KNOWN_NESTED_JURY_KEYS))


class NestedTableShapeOnTheUnvalidatedPath(unittest.TestCase):
    """A scalar `[jury.*]` is a `ConfigError` in the readers too (issue #729).

    `validate_config` is not on every path to a `JuryConfig`: `load_config`
    defaults to `validate=False`, and `jury run-agent` keeps it that way on
    purpose so one seat's (or one table's) mistake cannot stop a single-seat
    run. Before this, `context = "diff-only"` reached `_context_from_dict` and
    raised `'str' object has no attribute 'get'` — a traceback rather than the
    message the caller already knows how to print. `ci` and `diff` had the same
    hole; the fix is the same in all three, with the wording `validate_config`
    uses.
    """

    _SCALARS = (("ci", "critical"), ("context", "diff-only"), ("diff", 4096))

    _READERS = {"ci": _ci_from_dict, "context": _context_from_dict, "diff": _diff_from_dict}

    def test_each_reader_rejects_a_scalar_with_the_shared_message(self):
        for table, scalar in self._SCALARS:
            reader = self._READERS[table]
            with self.subTest(table=table):
                with self.assertRaises(ConfigError) as ctx:
                    reader(scalar)
                self.assertIn(f"[jury.{table}] must be a table", str(ctx.exception))

    def test_from_dict_raises_config_error_not_attribute_error(self):
        for table, scalar in self._SCALARS:
            with self.subTest(table=table):
                data = {
                    "jury": {"chair": "a", table: scalar},
                    "agent": [{"name": "a", "vendor": "anthropic", "command": "claude"}],
                }
                with self.assertRaises(ConfigError) as ctx:
                    _from_dict(data)
                self.assertIn(f"[jury.{table}] must be a table", str(ctx.exception))

    def test_a_well_formed_nested_table_still_materialises(self):
        cfg = _from_dict(
            {
                "jury": {
                    "chair": "a",
                    "ci": {"min_vendors": 1},
                    "context": {"mode": "expanded"},
                    "diff": {"max_bytes": 4096},
                },
                "agent": [{"name": "a", "vendor": "anthropic", "command": "claude"}],
            }
        )
        self.assertEqual(cfg.ci.min_vendors, 1)
        self.assertEqual(cfg.context.mode, "expanded")
        self.assertEqual(cfg.diff.max_bytes, 4096)


#: `jury.toml` examples live in these two docs; both are copy-pasted by readers.
_DOCS_WITH_JURY_TOML = ("configuration.md", "parameters.md")
_TOML_FENCE_RE = re.compile(r"```toml\n(.*?)```", re.DOTALL)
#: A documented `[jury…]` fragment rarely declares its own panel; supply one so
#: the block is judged on its own keys, not on "no agents configured".
_STAND_IN_AGENT = {"name": "claude", "vendor": "anthropic", "command": "claude"}


def _documented_jury_blocks() -> list:
    """Every fenced ```toml block in the docs that configures a `[jury…]` table."""
    docs = Path(__file__).resolve().parent.parent / "docs"
    blocks = []
    for name in _DOCS_WITH_JURY_TOML:
        text = (docs / name).read_text(encoding="utf-8")
        for index, match in enumerate(_TOML_FENCE_RE.finditer(text)):
            body = match.group(1)
            if "[jury" in body:
                blocks.append((f"{name} toml block #{index}", body))
    return blocks


class TopLevelTableShapeOnTheUnvalidatedPath(unittest.TestCase):
    """A scalar `jury` or a malformed `agent` is a `ConfigError` in `_from_dict` too (#732).

    #731 closed the nested tables; the top level had the same hole one level up.
    `validate_config` already refused these shapes — the reader did not, and the
    non-validating callers only handle `ConfigError`.
    """

    _AGENT = {"name": "a", "vendor": "anthropic", "command": "claude"}

    def test_a_scalar_jury_is_a_config_error_not_an_attribute_error(self):
        with self.assertRaises(ConfigError) as ctx:
            _from_dict({"jury": "x", "agent": [self._AGENT]})
        self.assertEqual(str(ctx.exception), "[jury] must be a table.")

    def test_a_scalar_agent_is_a_config_error(self):
        with self.assertRaises(ConfigError) as ctx:
            _from_dict({"jury": {}, "agent": "claude"})
        self.assertEqual(str(ctx.exception), "[[agent]] must be an array of tables.")

    def test_an_agent_entry_that_is_not_a_table_is_a_config_error_naming_its_index(self):
        with self.assertRaises(ConfigError) as ctx:
            _from_dict({"jury": {}, "agent": [self._AGENT, "codex"]})
        self.assertEqual(str(ctx.exception), "agent[1] must be a table.")

    def test_the_reader_and_the_validator_say_the_same_thing(self):
        for data in (
            {"jury": "x", "agent": [self._AGENT]},
            {"jury": {}, "agent": "claude"},
        ):
            with self.subTest(data=data):
                with self.assertRaises(ConfigError) as validated:
                    validate_config(data)
                with self.assertRaises(ConfigError) as materialised:
                    _from_dict(data)
                self.assertEqual(str(validated.exception), str(materialised.exception))


class DocumentedExamplesAreStrictClean(unittest.TestCase):
    """Every documented `[jury…]` example survives `--strict-config` (#719).

    Nested-table validation is only safe to add if the documentation is not
    itself teaching keys the validator will now reject, and the check is worth
    keeping afterwards: a doc that drifts from the schema hands the reader a
    config that fails the very gate the doc told them to run.
    """

    def test_the_docs_contain_jury_examples_to_check(self):
        # Guard the regex itself: silently matching nothing would make the
        # assertion below vacuous.
        blocks = _documented_jury_blocks()
        self.assertTrue(blocks)
        for name in _DOCS_WITH_JURY_TOML:
            self.assertTrue(any(label.startswith(name) for label, _ in blocks), name)

    def test_every_documented_jury_block_validates_under_strict(self):
        for label, body in _documented_jury_blocks():
            with self.subTest(block=label):
                data = tomllib.loads(body)
                data.setdefault("agent", [dict(_STAND_IN_AGENT)])
                self.assertEqual(validate_config(data, strict=True), [])


if __name__ == "__main__":
    unittest.main()
