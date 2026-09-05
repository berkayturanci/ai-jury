"""The vendor vocabulary, and what an unrecognised vendor is worth (issue #701).

Two claims are pinned here.

**`xai` is a vendor this tool knows.** Grok is reachable today from Cursor's
CLI, and that CLI is already installed on machines running this panel, so the
`[[agent]]` from #701 must validate silently and the seat's ballots must say
`xai` — not `cli`, and not `openai-compatible` borrowed from a neighbour.

**The generic fallback is not a vendor identity.** `cli` stays a real,
documented vendor and the fallback stays soft — a run does not abort because a
vendor name is ahead of the build. What changes is what the fallback is *worth*
at the cross-vendor gate: a seat whose vendor the tool cannot identify answers
to `cli`, so two of them are one vendor, not two. That is the #682 failure
reached through configuration — a bench that looks diverse satisfying
`min_vendors` without being diverse — and the ballots still carry each seat's
own configured string, because collapsing the gate is not a licence to rewrite
provenance.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import adapters, cli, doctor  # noqa: E402
from ai_jury import config as config_module
from ai_jury.adapters import (  # noqa: E402
    GenericCLIAdapter,
    XaiApiAdapter,
    effort_args,
    effort_supported,
    make_adapter,
)
from ai_jury.config import (  # noqa: E402
    GENERIC_VENDOR,
    KNOWN_VENDORS,
    AgentSpec,
    ConfigError,
    _from_dict,
    is_commandless_vendor,
    is_recognised_vendor,
    load_config,
    normalise_vendor,
    recognised_vendors,
    register_vendor,
    validate_config,
    vendor_identity,
)
from ai_jury.doctor import _transport, doctor_report_dict  # noqa: E402
from ai_jury.metadata import collapse_reason, distinct_vendors, panel_accounting  # noqa: E402
from ai_jury.privilege import enable_write, enforce_read_only  # noqa: E402
from ai_jury.runagent import builtin_spec, strip_transport  # noqa: E402

#: The `[[agent]]` from #701, verbatim.
_GROK_SEAT = {
    "name": "cursor",
    "vendor": "xai",
    "command": "cursor-agent",
    "extra_args": ["-p", "--model", "cursor-grok-4.6-high-fast", "--force", "--output-format"],
}


class _Review:
    """The shape `review_status` reads. Mirrors `tests/test_min_vendors._Review`."""

    def __init__(self, vendor: str):
        self.vendor = vendor
        self.ok = True
        self.findings = [object()]
        self.structured = True


class TheGrokSeatFromTheIssue(unittest.TestCase):
    def test_it_validates_with_no_warning(self):
        warnings = validate_config(
            {"jury": {"chair": "cursor"}, "agent": [dict(_GROK_SEAT)]}, strict=False
        )
        self.assertEqual(warnings, [])

    def test_it_survives_strict_config(self):
        """`--strict-config` turns warnings into exit 2; there is nothing to turn."""
        validate_config({"jury": {"chair": "cursor"}, "agent": [dict(_GROK_SEAT)]}, strict=True)

    def test_the_spec_keeps_the_vendor_it_was_given(self):
        config = _from_dict({"agent": [dict(_GROK_SEAT)]})
        self.assertEqual(config.agents[0].vendor, "xai")

    def test_it_runs_on_the_bring_your_own_cli_adapter(self):
        """`cursor-agent` is the operator's binary, invoked as configured."""
        spec = _from_dict({"agent": [dict(_GROK_SEAT)]}).agents[0]
        self.assertIsInstance(make_adapter(spec), GenericCLIAdapter)

    def test_it_counts_as_its_own_vendor_beside_the_shipped_panel(self):
        config = _from_dict(
            {
                "agent": [
                    {"name": "claude", "vendor": "anthropic", "command": "claude"},
                    dict(_GROK_SEAT),
                ]
            }
        )
        self.assertEqual(distinct_vendors(config.enabled_agents), 2)

    def test_no_sandbox_flag_is_injected_into_someone_elses_cli(self):
        """agy's `--sandbox` in `cursor-agent`'s argv breaks the seat, not the diff.

        `xai` is the generic CLI profile, exactly like `cli`: this tool knows no
        vendor-specific sandbox flag for it, so it forwards `extra_args`
        untouched in both directions rather than inventing one. `privilege`
        still audits the seat and warns when nothing sandboxes it.
        """
        args = list(_GROK_SEAT["extra_args"])
        self.assertEqual(enforce_read_only("xai", "cursor", args), args)
        self.assertEqual(enable_write("xai", "cursor", args), args)


class TheVocabulary(unittest.TestCase):
    def test_xai_and_its_api_flavour_are_both_known(self):
        self.assertIn("xai", KNOWN_VENDORS)
        self.assertIn("xai-api", KNOWN_VENDORS)

    def test_every_known_vendor_recognises_itself(self):
        for vendor in KNOWN_VENDORS:
            with self.subTest(vendor):
                self.assertTrue(is_recognised_vendor(vendor))
                self.assertEqual(vendor_identity(vendor), vendor)

    def test_recognition_ignores_case_and_padding(self):
        self.assertTrue(is_recognised_vendor("  XAI  "))
        self.assertEqual(vendor_identity("  XAI  "), "xai")

    def test_an_unknown_name_is_not_recognised(self):
        self.assertFalse(is_recognised_vendor("xa1"))

    def test_a_non_string_is_not_a_vendor_name(self):
        """`vendor = 3` is a config mistake to warn about, not a crash."""
        self.assertFalse(is_recognised_vendor(3))
        self.assertEqual(vendor_identity(3), "")

    def test_an_absent_vendor_is_no_vendor_at_all(self):
        self.assertEqual(vendor_identity(""), "")
        self.assertEqual(vendor_identity(None), "")
        self.assertFalse(is_recognised_vendor(""))

    def test_every_shipped_vendor_has_an_adapter_behind_it(self):
        """A name in the vocabulary that routes nowhere is a name that lies."""
        self.assertEqual(set(KNOWN_VENDORS) - set(adapters._VENDOR_ADAPTERS), set())


class AnUnknownVendorIsWorthTheGenericBucket(unittest.TestCase):
    def test_it_answers_to_cli(self):
        self.assertEqual(vendor_identity("xa1"), GENERIC_VENDOR)

    def test_two_unknown_seats_are_one_vendor(self):
        """The acceptance criterion, at the level of the configured panel."""
        config = _from_dict(
            {
                "agent": [
                    {"name": "a", "vendor": "xa1", "command": "cursor-agent"},
                    {"name": "b", "vendor": "grok-cli", "command": "cursor-agent"},
                ]
            }
        )
        self.assertEqual(distinct_vendors(config.enabled_agents), 1)

    def test_an_unknown_seat_and_a_cli_seat_are_one_vendor(self):
        """They are the same bucket, so they are the same vendor."""
        config = _from_dict(
            {
                "agent": [
                    {"name": "a", "vendor": "xa1", "command": "aider"},
                    {"name": "b", "vendor": "cli", "command": "goose"},
                ]
            }
        )
        self.assertEqual(distinct_vendors(config.enabled_agents), 1)

    def test_two_fallback_reviews_contribute_one_vendor(self):
        panel = panel_accounting([_Review("xa1"), _Review("grok-cli")])
        self.assertEqual(panel["effective"], 2)
        self.assertEqual(panel["vendors"], 1)

    def test_they_cannot_satisfy_a_threshold_of_two(self):
        reason = collapse_reason([_Review("xa1"), _Review("grok-cli")], 2)
        self.assertIsNotNone(reason)
        self.assertIn("panel collapsed", reason)

    def test_a_real_second_vendor_still_satisfies_it(self):
        """The gate is not simply harder: a genuine pair still passes."""
        self.assertIsNone(collapse_reason([_Review("anthropic"), _Review("xai")], 2))

    def test_the_warning_names_the_consequence(self):
        warnings = validate_config(
            {
                "jury": {"chair": "cursor"},
                "agent": [{"name": "cursor", "vendor": "xa1", "command": "cursor-agent"}],
            },
            strict=False,
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("unknown vendor 'xa1'", warnings[0])
        self.assertIn("min_vendors", warnings[0])
        self.assertIn("one vendor, not two", warnings[0])

    def test_the_warning_lists_xai_among_the_expected_names(self):
        warnings = validate_config(
            {
                "jury": {"chair": "cursor"},
                "agent": [{"name": "cursor", "vendor": "xa1", "command": "cursor-agent"}],
            },
            strict=False,
        )
        self.assertIn("xai", warnings[0])

    def test_it_is_still_only_a_warning(self):
        """The fallback is kept: an unknown name must not abort a review run."""
        with self.assertRaises(ConfigError):
            validate_config(
                {
                    "jury": {"chair": "cursor"},
                    "agent": [{"name": "cursor", "vendor": "xa1", "command": "cursor-agent"}],
                },
                strict=True,
            )


class RegisteringAnAdapterTeachesTheVocabulary(unittest.TestCase):
    """`register_adapter()` is the documented extension point, and it still works.

    Folding unrecognised names into `cli` would otherwise demote a legitimate
    custom vendor to a generic bucket. Registering an adapter is what makes a
    name real, so it updates both registries at once.
    """

    def _forget(self, vendor: str) -> None:
        config_module._REGISTERED_VENDORS.discard(vendor)
        adapters._VENDOR_ADAPTERS.pop(vendor, None)

    def test_a_registered_vendor_keeps_its_own_identity(self):
        self.addCleanup(self._forget, "company-llm")
        adapters.register_adapter("company-llm", GenericCLIAdapter)
        self.assertTrue(is_recognised_vendor("company-llm"))
        self.assertEqual(vendor_identity("company-llm"), "company-llm")
        self.assertIn("company-llm", recognised_vendors())

    def test_a_registered_vendor_stops_warning(self):
        self.addCleanup(self._forget, "company-llm")
        adapters.register_adapter("company-llm", GenericCLIAdapter)
        warnings = validate_config(
            {
                "jury": {"chair": "internal"},
                "agent": [{"name": "internal", "vendor": "company-llm", "command": "x"}],
            },
            strict=False,
        )
        self.assertEqual(warnings, [])

    def test_two_registered_vendors_are_two_vendors(self):
        self.addCleanup(self._forget, "company-llm")
        self.addCleanup(self._forget, "other-llm")
        adapters.register_adapter("company-llm", GenericCLIAdapter)
        adapters.register_adapter("other-llm", GenericCLIAdapter)
        self.assertEqual(len({vendor_identity("company-llm"), vendor_identity("other-llm")}), 2)

    def test_an_empty_name_registers_nothing(self):
        before = set(recognised_vendors())
        register_vendor("   ")
        self.assertEqual(set(recognised_vendors()), before)

    def test_an_empty_name_is_refused_before_either_table_is_touched(self):
        """`''` used to land in the adapter table under the key `''` while the
        vocabulary learned nothing — two registries disagreeing on the one input
        they were joined to agree on."""
        for name in ("", "   "):
            with self.subTest(vendor=repr(name)):
                registry = dict(adapters._VENDOR_ADAPTERS)
                before = set(recognised_vendors())
                with self.assertRaises(ValueError):
                    adapters.register_adapter(name, GenericCLIAdapter)
                self.assertEqual(adapters._VENDOR_ADAPTERS, registry)
                self.assertNotIn("", adapters._VENDOR_ADAPTERS)
                self.assertEqual(set(recognised_vendors()), before)

    def test_a_name_that_normalises_onto_a_shipped_vendor_lands_on_that_key(self):
        registry = dict(adapters._VENDOR_ADAPTERS)
        self.addCleanup(adapters._VENDOR_ADAPTERS.update, registry)
        self.addCleanup(config_module._REGISTERED_VENDORS.discard, "openai")
        adapters.register_adapter("  OpenAI  ", GenericCLIAdapter)
        self.assertIs(adapters._VENDOR_ADAPTERS["openai"], GenericCLIAdapter)
        self.assertNotIn("  OpenAI  ", adapters._VENDOR_ADAPTERS)
        self.assertEqual(list(recognised_vendors()).count("openai"), 1)

    def test_the_shipped_names_are_never_duplicated(self):
        self.addCleanup(config_module._REGISTERED_VENDORS.discard, "cli")
        register_vendor("cli")
        self.assertEqual(list(recognised_vendors()).count("cli"), 1)


class TheXaiHostedApiAdapter(unittest.TestCase):
    """`xai-api` is the API-flavoured spelling the other vendors already have."""

    def _spec(self, **kw):
        base = {"name": "grok", "vendor": "xai-api", "model": "grok-probe-1"}
        base.update(kw)
        return AgentSpec(**base)

    def test_the_vendor_routes_to_it(self):
        self.assertIsInstance(make_adapter(self._spec()), XaiApiAdapter)

    def test_it_posts_to_xais_own_host(self):
        self.assertEqual(
            XaiApiAdapter(self._spec())._api_url(), "https://api.x.ai/v1/chat/completions"
        )

    def test_it_reads_its_own_credential_env_var(self):
        self.assertEqual(XaiApiAdapter(self._spec())._env_var_name(), "XAI_API_KEY")

    def test_it_speaks_chat_completions(self):
        payload = XaiApiAdapter(self._spec()).build_payload("review this")
        self.assertEqual(payload["model"], "grok-probe-1")
        self.assertEqual(payload["messages"], [{"role": "user", "content": "review this"}])

    def test_it_parses_a_chat_completions_response(self):
        data = {"choices": [{"message": {"content": " findings \n"}}]}
        self.assertEqual(XaiApiAdapter.parse_content(data), "findings")

    def test_it_sends_effort_as_reasoning_effort(self):
        self.assertTrue(effort_supported("xai-api"))
        self.assertEqual(effort_args("xai-api", "high").payload, {"reasoning_effort": "high"})

    def test_the_cli_spelling_has_no_effort_knob(self):
        """`xai` is someone else's CLI; there is no request body to put it in."""
        self.assertFalse(effort_supported("xai"))

    def test_it_needs_no_command(self):
        warnings = validate_config(
            {
                "jury": {"chair": "grok"},
                "agent": [{"name": "grok", "vendor": "xai-api", "model": "grok-2-latest"}],
            },
            strict=False,
        )
        self.assertEqual(warnings, [])

    def test_it_has_no_sandbox_surface(self):
        self.assertEqual(enforce_read_only("xai-api", "grok", []), [])

    def test_doctor_calls_it_an_api_and_the_cli_spelling_a_cli(self):
        self.assertEqual(_transport("xai-api", "", None), "api")
        self.assertEqual(_transport("xai", "cursor-agent", None), "cli")

    def test_it_is_a_bare_agent_token(self):
        spec = builtin_spec("xai-api")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.vendor, "xai-api")

    def test_its_transport_prefix_is_stripped_from_a_model_id(self):
        self.assertEqual(strip_transport("xai-api:grok-2-latest"), "grok-2-latest")


# --- end to end --------------------------------------------------------------
#
# The classes above pin the pieces. This one runs `jury` on a two-seat panel of
# unrecognised vendors and reads the exit code and the metadata, because "two
# fallback seats cannot satisfy min_vendors = 2" is a claim about the command.
# The panel is mocked: no CLI, no network, no spend.

_DIFF = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"

_TWO_FALLBACK_SEATS = """
[jury]
rounds = 1
chair = "one"
verify = false

[[agent]]
name = "one"
vendor = "xa1"
command = "cursor-agent"

[[agent]]
name = "two"
vendor = "grok-cli"
command = "cursor-agent"
"""

_A_GROK_SEAT_AND_A_CLAUDE_SEAT = """
[jury]
rounds = 1
chair = "claude"
verify = false

[[agent]]
name = "claude"
vendor = "anthropic"
command = "claude"

[[agent]]
name = "cursor"
vendor = "xai"
command = "cursor-agent"
"""


def _config_validate(config_toml: str) -> str:
    """`jury --config-validate`, the command #701 quotes. Returns stdout."""
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "jury.toml"
        config.write_text(config_toml, encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--config", str(config), "--config-validate"])
    assert code == 0, err.getvalue()
    return out.getvalue()


def _jury(config_toml: str, argv: list[str]):
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "jury.toml"
        config.write_text(config_toml, encoding="utf-8")
        diff = Path(tmp) / "changes.diff"
        diff.write_text(_DIFF, encoding="utf-8")
        meta = Path(tmp) / "meta.json"
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(
                [
                    "--mock",
                    "--diff-file",
                    str(diff),
                    "--config",
                    str(config),
                    "--metadata-json",
                    str(meta),
                    *argv,
                ]
            )
        metadata = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
    return code, err.getvalue(), metadata


class TwoFallbackSeatsAreOneVendorToTheRun(unittest.TestCase):
    def test_an_explicit_threshold_of_two_fails_them(self):
        """The acceptance criterion of #701, as an exit code."""
        code, err, _meta = _jury(_TWO_FALLBACK_SEATS, ["--min-vendors", "2"])
        self.assertEqual(code, 3)
        self.assertIn("panel collapsed", err)

    def test_the_metadata_reports_one_vendor_either_way(self):
        """What a consumer reads. keel downgrades a jury below two vendors."""
        _code, _err, meta = _jury(_TWO_FALLBACK_SEATS, [])
        self.assertEqual(meta["panel"]["effective"], 2)
        self.assertEqual(meta["panel"]["vendors"], 1)

    def test_the_default_gate_treats_them_as_the_single_vendor_run_they_are(self):
        """Not a new failure: the shipped default is scoped to runs that claimed
        cross-vendor consensus, and this configuration no longer claims one."""
        code, err, _meta = _jury(_TWO_FALLBACK_SEATS, [])
        self.assertEqual(code, 0)
        self.assertNotIn("panel collapsed", err)

    def test_config_validate_still_warns_about_each_seat(self):
        report = _config_validate(_TWO_FALLBACK_SEATS)
        self.assertIn("unknown vendor 'xa1'", report)
        self.assertIn("unknown vendor 'grok-cli'", report)


class AGrokSeatIsARealSecondVendor(unittest.TestCase):
    def test_the_panel_satisfies_the_default_gate(self):
        code, err, meta = _jury(_A_GROK_SEAT_AND_A_CLAUDE_SEAT, [])
        self.assertEqual(code, 0)
        self.assertNotIn("panel collapsed", err)
        self.assertEqual(meta["panel"]["vendors"], 2)

    def test_config_validate_says_nothing_about_an_unknown_vendor(self):
        """The line #701 opens with, gone: `Config valid`, no warning."""
        report = _config_validate(_A_GROK_SEAT_AND_A_CLAUDE_SEAT)
        self.assertNotIn("unknown vendor", report)
        self.assertIn("Config valid", report)

    def test_the_seats_ballots_carry_vendor_xai(self):
        """Vendor identity is what this project's output is for."""
        _code, _err, meta = _jury(_A_GROK_SEAT_AND_A_CLAUDE_SEAT, [])
        vendors = {a["name"]: a["vendor"] for a in meta["agents"]}
        self.assertEqual(vendors["cursor"], "xai")


class TheKeelReviewsBundleAttributesTheGrokSeat(unittest.TestCase):
    """`--format keel-reviews` is the ballot surface #663 built; #701 is about
    what it says the vendor was."""

    def test_the_ballot_names_xai(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "jury.toml"
            config.write_text(_A_GROK_SEAT_AND_A_CLAUDE_SEAT, encoding="utf-8")
            diff = Path(tmp) / "changes.diff"
            diff.write_text(_DIFF, encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                cli.main(
                    [
                        "--mock",
                        "--diff-file",
                        str(diff),
                        "--config",
                        str(config),
                        "--format",
                        "keel-reviews",
                    ]
                )
        reviews = json.loads(out.getvalue())
        vendors = {r["reviewer"]: r["vendor"] for r in reviews}
        self.assertEqual(vendors["cursor"], "xai")


class TheContractGoldenLocksTheGrokInvocation(unittest.TestCase):
    def test_the_locked_contract_is_the_pass_through(self):
        """`tests/golden/adapter_contracts.json` carries the `xai` entry; this
        asserts the tool adds nothing to what the operator configured."""
        spec = AgentSpec(
            name="cursor",
            vendor="xai",
            command="cursor-agent",
            extra_args=["-p", "--model", "cursor-grok-4.6-high-fast"],
        )
        with mock.patch.object(adapters.shutil, "which", lambda cmd: f"/bin/{cmd}"):
            argv = make_adapter(spec).build_argv("PROMPT")
        self.assertEqual(argv, ["cursor-agent", "-p", "--model", "cursor-grok-4.6-high-fast"])


# --- round 2: one vocabulary, one arithmetic, and each place says which ------


_TWO_FALLBACK_SEATS = """
[jury]
[jury.ci]
min_vendors = 2

[[agent]]
name = "grok"
vendor = "xa1"
command = "sh"

[[agent]]
name = "grok2"
vendor = "grok-cli"
command = "sh"
"""


class TheDoctorCountsWhatTheGateCounts(unittest.TestCase):
    """The two numbers an operator compares must agree (review round 2).

    `--doctor` counted raw vendor strings while the gate counted collapsed
    identity, so a bench of two unidentifiable seats was reported as
    cross-vendor ready and then counted as a single vendor by the run. Same
    vocabulary, same arithmetic, or the vocabulary is back in two places.
    """

    @contextlib.contextmanager
    def _config(self, text=_TWO_FALLBACK_SEATS):
        """A config file whose every seat is forced reachable.

        Availability is not what these tests are about: they are about the
        arithmetic doctor does over whatever it found.
        """
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(doctor, "_is_available", lambda _spec: True),
        ):
            path = Path(tmp) / "jury.toml"
            path.write_text(text)
            yield path

    def _report(self, path):
        return doctor_report_dict(doctor.build_diagnostics(str(path)))

    def test_configured_count_equals_the_gate_count(self):
        with self._config() as path:
            gate = distinct_vendors(load_config(str(path)).enabled_agents)
            panel = self._report(path)["panel"]
        self.assertEqual(gate, 1)
        self.assertEqual(panel["vendors_configured"], gate)

    def test_available_count_is_collapsed_too(self):
        """Both reachable seats are the same vendor, so they are one available vendor."""
        with self._config() as path:
            panel = self._report(path)["panel"]
        self.assertEqual(panel["vendors_available"], 1)

    def test_two_real_vendors_still_count_as_two(self):
        """The collapse is not a cap: recognised names keep their own identity."""
        text = _TWO_FALLBACK_SEATS.replace('"xa1"', '"xai"').replace('"grok-cli"', '"cli"')
        with self._config(text) as path:
            gate = distinct_vendors(load_config(str(path)).enabled_agents)
            panel = self._report(path)["panel"]
        self.assertEqual(gate, 2)
        self.assertEqual(panel["vendors_configured"], 2)
        self.assertEqual(panel["vendors_available"], 2)

    def test_the_agent_row_still_carries_the_configured_string(self):
        """Collapsing the gate is not a licence to rewrite provenance."""
        with self._config() as path:
            agents = self._report(path)["agents"]
        self.assertEqual([a["vendor"] for a in agents], ["xa1", "grok-cli"])

    def test_the_agent_row_also_states_the_identity_it_carries(self):
        with self._config() as path:
            agents = self._report(path)["agents"]
        self.assertEqual([a["vendor_identity"] for a in agents], [GENERIC_VENDOR, GENERIC_VENDOR])

    def test_a_recognised_seat_reports_itself_as_its_own_identity(self):
        text = _TWO_FALLBACK_SEATS.replace('"xa1"', '"xai"')
        with self._config(text) as path:
            agents = self._report(path)["agents"]
        self.assertEqual(agents[0]["vendor"], "xai")
        self.assertEqual(agents[0]["vendor_identity"], "xai")

    def test_the_human_report_says_which_number_is_which(self):
        with self._config() as path:
            text = doctor.render_report(doctor.build_diagnostics(str(path)))
        self.assertIn("vendors enabled:   1 (by vendor identity)", text)
        self.assertIn("vendors reachable: 1 (by vendor identity)", text)
        self.assertIn("counted by vendor identity", text)

    def test_a_collapsed_row_shows_both_strings(self):
        with self._config() as path:
            text = doctor.render_report(doctor.build_diagnostics(str(path)))
        self.assertIn("vendor=xa1 -> counts as cli", text)

    def test_a_recognised_row_is_left_alone(self):
        """No arrow where there is nothing to explain."""
        text_cfg = _TWO_FALLBACK_SEATS.replace('"xa1"', '"xai"')
        with self._config(text_cfg) as path:
            text = doctor.render_report(doctor.build_diagnostics(str(path)))
        self.assertIn("vendor=xai,", text)
        self.assertNotIn("vendor=xai ->", text)


class ARecognisedVendorIsRecognisedByEveryRule(unittest.TestCase):
    """Normalise once, then every rule reads the normalised value (round 2).

    `is_recognised_vendor("XAI-API")` was True while `validate_config` failed
    the same seat for having no `command`, because the commandless-vendor test
    ran on the un-normalised string. A vendor the tool recognises must be
    recognised by validation, by the adapter lookup and by the gate alike.
    """

    def _validate(self, vendor, **extra):
        agent = {"name": "grok", "vendor": vendor, "model": "grok-4"}
        agent.update(extra)
        return validate_config({"jury": {"chair": "grok"}, "agent": [agent]})

    def test_a_padded_api_vendor_needs_no_command(self):
        for spelling in ("XAI-API", " xai-api ", "Xai-Api", "\tXAI-API\n"):
            with self.subTest(spelling=spelling):
                self.assertEqual(self._validate(spelling), [])

    def test_the_other_api_vendors_normalise_too(self):
        for spelling in ("OpenAI-API", " ANTHROPIC-API", "Google-API ", "LOCAL"):
            with self.subTest(spelling=spelling):
                self.assertEqual(self._validate(spelling), [])

    def test_a_padded_cli_vendor_still_needs_a_command(self):
        """Normalising is not a licence to skip the command check."""
        with self.assertRaises(ConfigError) as caught:
            self._validate(" XAI ")
        self.assertIn("is missing a non-empty 'command'", str(caught.exception))

    def test_a_padded_recognised_vendor_does_not_warn_as_unknown(self):
        self.assertEqual(self._validate(" XAI ", command="cursor-agent"), [])

    def test_the_unknown_warning_quotes_what_the_operator_wrote(self):
        """The message is provenance: it must echo the spelling in the file."""
        warnings = self._validate(" Xa1 ", command="cursor-agent")
        self.assertEqual(len(warnings), 1)
        self.assertIn("unknown vendor ' Xa1 '", warnings[0])

    def test_the_missing_model_warning_quotes_it_too(self):
        warnings = validate_config(
            {"jury": {"chair": "grok"}, "agent": [{"name": "grok", "vendor": "XAI-API"}]}
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("(vendor 'XAI-API') has no 'model'", warnings[0])

    def test_the_adapter_lookup_sees_the_same_normalised_string(self):
        spec = AgentSpec(name="grok", vendor=" XAI-API ", model="grok-4")
        self.assertIsInstance(make_adapter(spec), XaiApiAdapter)

    def test_the_gate_sees_the_same_normalised_string(self):
        self.assertEqual(vendor_identity(" XAI-API "), "xai-api")
        self.assertEqual(distinct_vendors([AgentSpec(name="a", vendor=" XAI-API ")]), 1)

    def test_one_seat_cannot_be_two_vendors_by_spelling(self):
        """`xai` and ` XAI ` are one vendor, not a free second perspective."""
        config = _from_dict(
            {
                "agent": [
                    {"name": "a", "vendor": "xai", "command": "cursor-agent"},
                    {"name": "b", "vendor": " XAI ", "command": "cursor-agent"},
                ]
            }
        )
        self.assertEqual(distinct_vendors(config.enabled_agents), 1)

    def test_registering_a_padded_name_registers_the_normalised_one(self):
        registry = dict(adapters._VENDOR_ADAPTERS)
        registered = set(config_module._REGISTERED_VENDORS)
        try:
            adapters.register_adapter("  My-Vendor  ", GenericCLIAdapter)
            self.assertEqual(vendor_identity("my-vendor"), "my-vendor")
            spec = AgentSpec(name="x", vendor="MY-VENDOR", command="mine")
            self.assertIsInstance(make_adapter(spec), GenericCLIAdapter)
            self.assertIn("my-vendor", recognised_vendors())
        finally:
            adapters._VENDOR_ADAPTERS.clear()
            adapters._VENDOR_ADAPTERS.update(registry)
            config_module._REGISTERED_VENDORS.clear()
            config_module._REGISTERED_VENDORS.update(registered)

    def test_a_non_string_vendor_normalises_to_nothing(self):
        """A config mistake to warn about, not a crash."""
        self.assertEqual(normalise_vendor(3), "")
        self.assertFalse(is_commandless_vendor(3))
        self.assertFalse(is_commandless_vendor("xai"))
        self.assertTrue(is_commandless_vendor(" LOCAL "))


class OneSpellingIsOneAnswerEverywhere(unittest.TestCase):
    """One padded, cased spelling, walked past every reader (round 3).

    Round 2 normalised inside each rule, which is a convention, not an
    invariant: two readers of the same seat were still free to disagree, and
    two of them did. ``doctor._detect_warnings`` compared the raw string while
    ``doctor._unavailable_reason`` compared the normalised one, so a single
    ``vendor = "XAI-API"`` seat came out of one ``--doctor`` run as both "the
    hosted API is not reachable" and "command '' is not on PATH". And
    ``privilege.enforce_read_only`` lowercased without stripping, so ``" XAI "``
    — a spelling validation accepts — missed ``GENERIC_CLI_VENDORS`` and had
    ``--sandbox`` injected into ``cursor-agent``, the one flag the xai profile
    exists to keep off that CLI.

    So the claim under test is not "each rule normalises" but "there is one
    answer": the spec normalises on construction, and every reader below is
    asked about the same padded seat and must return the same thing the
    canonical spelling returns.
    """

    #: Padded, cased spellings of both xai flavours, each with the single answer
    #: every reader has to give. ``transport`` is doctor's classification;
    #: ``sandboxed`` is what the privilege guard may add to ``extra_args``.
    _SEATS = (
        {
            "spelling": " XAI-API ",
            "canonical": "xai-api",
            "seat": {"name": "grok", "vendor": " XAI-API ", "model": "grok-4"},
            "adapter": XaiApiAdapter,
            "transport": "hosted-api",
            "doctor_transport": "api",
            "extra_args": ["-p"],
            "guarded_args": ["-p"],
        },
        {
            "spelling": " XAI ",
            "canonical": "xai",
            "seat": {
                "name": "grok",
                "vendor": " XAI ",
                "command": "cursor-agent",
                "extra_args": ["-p"],
            },
            "adapter": GenericCLIAdapter,
            "transport": "cli",
            "doctor_transport": "cli",
            "extra_args": ["-p"],
            "guarded_args": ["-p"],
        },
    )

    def _config(self, case):
        return _from_dict({"jury": {"chair": "grok"}, "agent": [dict(case["seat"])]})

    def test_every_reader_gives_the_same_seat_the_same_answer(self):
        """The whole walk, in one test: nobody in this chain may disagree."""
        for case in self._SEATS:
            with self.subTest(spelling=case["spelling"]):
                canonical = case["canonical"]

                # 1. Validation accepts the spelling, with nothing to report.
                self.assertEqual(
                    validate_config({"jury": {"chair": "grok"}, "agent": [dict(case["seat"])]}),
                    [],
                )

                # 2. The adapter lookup routes it to the vendor's own adapter.
                spec = self._config(case).agents[0]
                self.assertIsInstance(make_adapter(spec), case["adapter"])

                # 3. The gate counts it as that vendor, not as the fallback.
                self.assertEqual(vendor_identity(case["spelling"]), canonical)
                self.assertNotEqual(vendor_identity(case["spelling"]), GENERIC_VENDOR)

                # 4. Doctor's two unavailability readers describe the SAME
                #    failure. Asserted through what each one actually prints, so
                #    this stays a test of the diagnosis rather than of whichever
                #    helper happens to produce it.
                config = self._config(case)
                with mock.patch.object(doctor, "_is_available", return_value=False):
                    warnings = doctor._detect_warnings(config)
                    reason = doctor._unavailable_reason(spec, [])
                self.assertEqual(len(warnings), 1)
                if case["transport"] == "hosted-api":
                    self.assertIn("(hosted API)", warnings[0])
                    self.assertNotIn("is not on PATH", warnings[0])
                    self.assertEqual(reason, "the hosted API is not reachable")
                else:
                    self.assertIn("'cursor-agent' is not on PATH", warnings[0])
                    self.assertEqual(reason, "command 'cursor-agent' is not on PATH")

                # 5. The privilege guard treats it exactly as the canonical
                #    spelling: no flag belonging to another vendor's CLI.
                self.assertEqual(
                    enforce_read_only(case["spelling"], "cursor", list(case["extra_args"])),
                    enforce_read_only(canonical, "cursor", list(case["extra_args"])),
                )
                self.assertEqual(
                    enforce_read_only(case["spelling"], "cursor", list(case["extra_args"])),
                    case["guarded_args"],
                )

                # 6. And the invariant all five rest on: the spec normalised the
                #    spelling once, on construction, so none of the readers above
                #    was ever offered the raw form to disagree over.
                self.assertEqual(spec.vendor, canonical)

    def test_the_padded_seat_and_the_canonical_seat_are_the_same_doctor_row(self):
        """Same seat, two spellings, one export row (bar the name)."""
        for case in self._SEATS:
            with self.subTest(spelling=case["spelling"]):
                canonical_seat = dict(case["seat"], vendor=case["canonical"])
                with mock.patch.object(doctor, "_is_available", return_value=False):
                    padded = doctor._agent_entry(self._config(case).agents[0])
                    plain = doctor._agent_entry(
                        _from_dict({"agent": [canonical_seat]}).agents[0],
                    )
                self.assertEqual(padded["vendor"], plain["vendor"])
                self.assertEqual(padded["vendor_identity"], plain["vendor_identity"])
                self.assertEqual(padded["reason"], plain["reason"])
                self.assertEqual(
                    _transport(case["spelling"], case["seat"].get("command", ""), None),
                    case["doctor_transport"],
                )

    def test_lifting_the_guarantee_reads_the_same_spelling(self):
        """`enable_write` is the mirror image and must mirror this too."""
        for case in self._SEATS:
            with self.subTest(spelling=case["spelling"]):
                self.assertEqual(
                    enable_write(case["spelling"], "cursor", ["-p"]),
                    enable_write(case["canonical"], "cursor", ["-p"]),
                )

    def test_no_reader_can_be_handed_the_raw_spelling(self):
        """The invariant behind all of the above: the spec normalises itself.

        Not `_from_dict` — every construction site, including the ad-hoc specs
        `cli` and `runagent` build, so a reader added tomorrow cannot see a raw
        vendor even if it forgets the normaliser exists.
        """
        self.assertEqual(AgentSpec(name="a", vendor=" XAI-API ").vendor, "xai-api")
        self.assertEqual(AgentSpec(name="a", vendor="\tGoogle\n").vendor, "google")
        self.assertEqual(AgentSpec(name="a", vendor=3).vendor, "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
