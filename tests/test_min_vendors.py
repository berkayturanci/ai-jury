"""A panel that collapsed to one vendor can be made to fail the run.

#625: `--strict` fails when a configured agent CLI is **missing**. It does not
fail when an agent is present, probes clean, and then returns nothing — which
is how a three-vendor panel silently becomes one. Observed on a real run:

    effective panel: 1 of 3 reviewer(s) (0 returned no review, 2 failed)
    claude — failed (OAuth session expired)
    codex  — ok
    agy    — failed (launcher ate the prompt)

`jury --doctor` had reported all three `[available]` with `probe: ok`
beforehand, and was right: they were installed. They still did not review, and
the run exited 0 with a verdict.

So the tests below use agents that are **configured and available but return
nothing**, never a missing CLI — the missing-CLI case is what `--strict`
already covers and would pass whatever this change does.

`--min-vendors` shipped opt-in (default 0) while the policy question was open:
on a flaky vendor CLI, failing closed turns a degraded second opinion into no
second opinion. #682 decided it the other way. The product claim is a
cross-vendor jury, so a run that silently delivers one vendor and exits 0 is the
more expensive failure — a degraded run that says so is recoverable, a false
consensus is not. The default is now 2 (`[jury.ci] min_vendors`), with
`--no-min-vendors` / `min_vendors = 0` as the explicit opt-out.

Turning it on by default is only safe because it is SCOPED: the default gate
applies to runs that claimed cross-vendor consensus in the first place — two or
more distinct vendors enabled — so a deliberate single-agent install keeps
exiting 0. A threshold typed on the command line is honoured as typed.
"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

from ai_jury import adapters as adapters_module
from ai_jury import cli
from ai_jury.adapters import AgentResult, MockAdapter
from ai_jury.cli import resolve_min_vendors
from ai_jury.config import DEFAULT_CONFIG, DEFAULT_MIN_VENDORS, JuryConfig, _from_dict
from ai_jury.metadata import collapse_reason, distinct_vendors, panel_accounting

#: A three-vendor panel, the shape the shipped `jury.toml` configures.
_AGENTS = [
    {"name": "claude", "vendor": "anthropic", "command": "claude"},
    {"name": "codex", "vendor": "openai", "command": "codex"},
    {"name": "agy", "vendor": "google", "command": "agy"},
]


class _Review:
    """The shape `review_status` actually reads: ``ok``, ``findings``, ``structured``.

    Built from that function rather than guessed. A stub that does not match it
    would make every assertion below vacuous — the first cut of this file used
    a `status` attribute nothing reads, and `panel_accounting` scored every
    review as `failed`.
    """

    def __init__(self, status: str, vendor: str):
        self.vendor = vendor
        self.ok = status != "failed"
        self.findings = [object()] if status == "findings" else []
        # `clean` means "emitted an empty findings block": examined, found
        # nothing. `abstained` means nothing reviewable came back at all.
        self.structured = status == "clean"


def _panel(*pairs):
    return panel_accounting([_Review(status, vendor) for status, vendor in pairs])


class ThePanelAccountingCountsVendorsNotSlots(unittest.TestCase):
    def test_a_healthy_three_vendor_panel(self):
        panel = _panel(("findings", "anthropic"), ("clean", "openai"), ("clean", "google"))
        self.assertEqual(panel["vendors"], 3)
        self.assertEqual(panel["effective"], 3)

    def test_the_observed_collapse(self):
        """The run that motivated this: two agents failed, one reviewed."""
        panel = _panel(("failed", "anthropic"), ("findings", "openai"), ("failed", "google"))
        self.assertEqual(panel["vendors"], 1)
        self.assertEqual(panel["effective"], 1)
        self.assertEqual(panel["configured"], 3)

    def test_three_slots_from_one_vendor_are_one_perspective(self):
        """The distinction the whole flag rests on.

        A run can be at full effective strength and still have formed no
        cross-vendor consensus. Counting slots would report 3 and be wrong.
        """
        panel = _panel(("findings", "openai"), ("clean", "openai"), ("clean", "openai"))
        self.assertEqual(panel["effective"], 3)
        self.assertEqual(panel["vendors"], 1)

    def test_an_abstention_does_not_count_as_a_vendor(self):
        """ "An abstention is not an approval" — and it is not a perspective either."""
        panel = _panel(("findings", "anthropic"), ("abstained", "openai"))
        self.assertEqual(panel["vendors"], 1)


class TheDefaultFailsClosedAndIsDistinguishable(unittest.TestCase):
    def setUp(self):
        self.cli = (Path(__file__).parent.parent / "src" / "ai_jury" / "cli.py").read_text(
            encoding="utf-8"
        )

    def test_the_flag_and_its_opt_out_both_exist(self):
        self.assertIn('"--min-vendors",', self.cli)
        self.assertIn('"--no-min-vendors",', self.cli)

    def test_the_flag_defaults_to_deferring_to_config(self):
        """`None` is "nobody said", which is what lets config own the default.

        A literal default here would silently outrank `[jury.ci] min_vendors`.
        """
        self.assertIn('resolve_min_vendors(getattr(args, "min_vendors", None), config)', self.cli)

    def test_the_exit_code_is_not_the_findings_one(self):
        """A collapsed panel and a blocking finding are different outcomes.

        `evaluate_ci` returns 0 or 1, so reusing 1 would make a caller unable to
        tell "the reviewers disagreed with you" from "the reviewers never ran".
        """
        self.assertIn("ci_exit = 3", self.cli)
        self.assertNotIn("ci_exit = 1", self.cli)

    def test_the_ci_gate_cannot_overwrite_a_collapse(self):
        """`--ci` used to reassign `ci_exit` and erase the exit-3 (#682)."""
        self.assertIn("ci_exit = ci_exit or gate_exit", self.cli)


class TheShippedDefaultIsTwo(unittest.TestCase):
    def test_config_ships_the_guard_on(self):
        self.assertEqual(DEFAULT_MIN_VENDORS, 2)
        self.assertEqual(JuryConfig().ci.min_vendors, 2)

    def test_the_default_config_document_says_so(self):
        self.assertEqual(DEFAULT_CONFIG["jury"]["ci"]["min_vendors"], 2)

    def test_a_malformed_value_falls_back_to_the_safe_default(self):
        """A typo in a CI knob must not quietly disable the guard."""
        config = _from_dict({"jury": {"ci": {"min_vendors": "two"}}, "agent": _AGENTS})
        self.assertEqual(config.ci.min_vendors, 2)

    def test_zero_is_the_documented_opt_out(self):
        config = _from_dict({"jury": {"ci": {"min_vendors": 0}}, "agent": _AGENTS})
        self.assertEqual(config.ci.min_vendors, 0)


class ResolvingTheThreshold(unittest.TestCase):
    """Who wins between the flag, the config key, and the shipped default."""

    def test_no_flag_takes_the_config_value_and_is_not_explicit(self):
        config = _from_dict({"jury": {"ci": {"min_vendors": 3}}, "agent": _AGENTS})
        self.assertEqual(resolve_min_vendors(None, config), (3, False))

    def test_a_flag_wins_and_is_explicit(self):
        config = _from_dict({"jury": {"ci": {"min_vendors": 3}}, "agent": _AGENTS})
        self.assertEqual(resolve_min_vendors(2, config), (2, True))

    def test_the_opt_out_is_explicit_zero(self):
        """`--no-min-vendors` sets 0, which must beat a config that says 2."""
        config = _from_dict({"jury": {"ci": {"min_vendors": 2}}, "agent": _AGENTS})
        self.assertEqual(resolve_min_vendors(0, config), (0, True))


class TheGateOnlyFiresOnARunThatClaimedConsensus(unittest.TestCase):
    def _reviews(self, *pairs):
        return [_Review(status, vendor) for status, vendor in pairs]

    def test_a_collapsed_three_vendor_panel_fails(self):
        reason = collapse_reason(
            self._reviews(("failed", "anthropic"), ("findings", "openai"), ("failed", "google")),
            2,
            configured_vendors=3,
        )
        self.assertIn("panel collapsed", reason)
        self.assertIn("1 vendor(s) contributed", reason)

    def test_a_healthy_panel_passes(self):
        self.assertIsNone(
            collapse_reason(
                self._reviews(("findings", "anthropic"), ("clean", "openai")),
                2,
                configured_vendors=2,
            )
        )

    def test_a_deliberate_single_vendor_install_is_left_alone(self):
        """The whole reason the default can be on: it is scoped, not blanket."""
        self.assertIsNone(
            collapse_reason(self._reviews(("findings", "anthropic")), 2, configured_vendors=1)
        )

    def test_an_explicit_threshold_is_enforced_as_asked(self):
        """`--min-vendors 3` on a two-vendor panel is a request for the failure."""
        self.assertIsNotNone(
            collapse_reason(
                self._reviews(("findings", "anthropic"), ("clean", "openai")),
                3,
                configured_vendors=None,
            )
        )

    def test_zero_disables_everything(self):
        self.assertIsNone(collapse_reason(self._reviews(("failed", "anthropic")), 0))

    def test_three_slots_from_one_vendor_do_not_satisfy_it(self):
        self.assertIsNotNone(
            collapse_reason(
                self._reviews(("findings", "openai"), ("clean", "openai"), ("clean", "openai")),
                2,
                configured_vendors=2,
            )
        )


class TheFailureSaysHowToGetPastIt(unittest.TestCase):
    """A default-on gate that fails without naming its opt-out is a support ticket.

    Whoever reads this line is looking at a red CI step on a guard that shipped
    on by default, quite possibly for the first time — and the two things they
    need (accept the collapse, or catch the missing CLI earlier) are both flags
    the tool already has.
    """

    def _reason(self) -> str:
        reason = collapse_reason(
            [_Review("findings", "anthropic"), _Review("failed", "openai")],
            2,
            configured_vendors=2,
        )
        self.assertIsNotNone(reason)
        return reason

    def test_it_names_the_opt_out_flag(self):
        self.assertIn("--no-min-vendors", self._reason())

    def test_it_names_the_config_key_too(self):
        """A CI caller edits jury.toml as often as it edits the command line."""
        self.assertIn("min_vendors = 0", self._reason())

    def test_it_names_the_startup_check_for_a_missing_cli(self):
        """The other escape, and the one a missing CLI actually wants."""
        self.assertIn("--strict", self._reason())

    def test_the_flags_it_names_are_real(self):
        """Guards against advertising a flag that argparse would reject."""
        parser = cli.build_parser()
        for flag in ("--no-min-vendors", "--strict"):
            with self.subTest(flag):
                self.assertIn(flag, self._reason())
                args = parser.parse_args(["--diff-file", "-", flag])
                self.assertIsNotNone(args)


class CountingConfiguredVendors(unittest.TestCase):
    def test_distinct_vendors_ignores_slot_count(self):
        config = _from_dict({"agent": _AGENTS})
        self.assertEqual(distinct_vendors(config.enabled_agents), 3)

    def test_two_slots_on_one_vendor_are_one_vendor(self):
        config = _from_dict(
            {
                "agent": [
                    {"name": "a", "vendor": "openai", "command": "codex"},
                    {"name": "b", "vendor": "OpenAI", "command": "codex"},
                ]
            }
        )
        self.assertEqual(distinct_vendors(config.enabled_agents), 1)

    def test_nothing_configured_is_zero(self):
        self.assertEqual(distinct_vendors([]), 0)


# --- end to end -------------------------------------------------------------
#
# The classes above pin the pieces; this one runs `jury` and reads its exit
# code, because "the default fails closed" is a claim about the command, not
# about a helper. The panel is mocked, so no CLI, network or spend is involved.

_DIFF = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"

_THREE_VENDOR_TOML = """
[jury]
rounds = 1
chair = "claude"
verify = false

[[agent]]
name = "claude"
vendor = "anthropic"
command = "claude"

[[agent]]
name = "codex"
vendor = "openai"
command = "codex"

[[agent]]
name = "agy"
vendor = "google"
command = "agy"
"""

_ONE_VENDOR_TOML = """
[jury]
rounds = 1
chair = "claude"
verify = false

[[agent]]
name = "claude"
vendor = "anthropic"
command = "claude"
"""


def _collapsing_run(dead: set[str]):
    """A MockAdapter.run that gives `dead` agents the #635 treatment.

    They are available, they are invoked, and they come back with nothing —
    which is precisely the case `--strict` cannot see.
    """
    real = MockAdapter.run

    def _run(self, prompt, phase="review", timeout=None, role_policy=None):
        if phase == "review" and self.name in dead:
            return AgentResult(
                self.name,
                self.spec.vendor,
                False,
                "",
                0.0,
                "no review returned: the agent declined to review",
                error_code=adapters_module.ERR_NO_REVIEW,
            )
        return real(self, prompt, phase=phase, timeout=timeout, role_policy=role_policy)

    return _run


def _jury(config_toml: str, argv: list[str], dead: set[str] = frozenset()):
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "jury.toml"
        config.write_text(config_toml, encoding="utf-8")
        diff = Path(tmp) / "changes.diff"
        diff.write_text(_DIFF, encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(MockAdapter, "run", _collapsing_run(dead)),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                code = cli.main(
                    ["--mock", "--diff-file", str(diff), "--config", str(config), *argv]
                )
            except SystemExit as exc:  # pragma: no cover - argparse only
                code = exc.code
    return code, out.getvalue(), err.getvalue()


class TheCommandFailsClosedByDefault(unittest.TestCase):
    def test_a_collapsed_three_vendor_panel_exits_three(self):
        code, _out, err = _jury(_THREE_VENDOR_TOML, [], dead={"codex", "agy"})
        self.assertEqual(code, 3)
        self.assertIn("panel collapsed", err)

    def test_a_healthy_three_vendor_panel_exits_zero(self):
        code, _out, err = _jury(_THREE_VENDOR_TOML, [])
        self.assertEqual(code, 0)
        self.assertNotIn("panel collapsed", err)

    def test_the_opt_out_flag_restores_the_old_behaviour(self):
        code, _out, _err = _jury(_THREE_VENDOR_TOML, ["--no-min-vendors"], dead={"codex", "agy"})
        self.assertEqual(code, 0)

    def test_the_config_key_is_an_opt_out_too(self):
        code, _out, _err = _jury(
            _THREE_VENDOR_TOML.replace("rounds = 1", "rounds = 1\n\n[jury.ci]\nmin_vendors = 0"),
            [],
            dead={"codex", "agy"},
        )
        self.assertEqual(code, 0)

    def test_a_single_vendor_install_is_not_failed_by_the_default(self):
        """The scoping that makes a fail-closed default safe to ship."""
        code, _out, err = _jury(_ONE_VENDOR_TOML, [])
        self.assertEqual(code, 0)
        self.assertNotIn("panel collapsed", err)

    def test_an_explicit_threshold_beats_the_scoping(self):
        code, _out, _err = _jury(_ONE_VENDOR_TOML, ["--min-vendors", "2"])
        self.assertEqual(code, 3)

    def test_the_ci_gate_cannot_launder_a_collapsed_panel(self):
        """`--ci` re-assigned the exit code and erased the 3 (#682)."""
        code, _out, _err = _jury(_THREE_VENDOR_TOML, ["--ci"], dead={"codex", "agy"})
        self.assertEqual(code, 3)


class TheReadmeContractListsExitThree(unittest.TestCase):
    """The README's exit-code table is the project's own stable contract (#692).

    It shipped 1.16.0 listing 0, 1 and 2 only, so a CI integrator reading the
    section that the README itself says may not change without a changelog entry
    concluded those were the whole set — and then met exit 3 in a red pipeline
    with nothing to look it up in. Anchored on the table row rather than on the
    file, so moving the section does not fail this and deleting the row does.
    """

    @classmethod
    def setUpClass(cls):
        text = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
        cls.table = text.split("**Stable error messages and exit codes:**", 1)[1].split(
            "**Stable report headings**", 1
        )[0]

    def _row(self) -> str:
        rows = [r for r in self.table.splitlines() if "min_vendors" in r]
        self.assertEqual(len(rows), 1, "expected exactly one min_vendors row")
        return rows[0]

    def test_the_table_declares_exit_three(self):
        self.assertIn("`3`", self._row())

    def test_the_row_separates_it_from_the_findings_failure(self):
        """0/1/2 were "the whole set" precisely because 3 looked like the `--ci` 1."""
        row = self._row()
        self.assertIn("*not* a findings failure", row)
        self.assertIn("--no-min-vendors", row)

    def test_the_findings_row_names_its_own_code(self):
        rows = [r for r in self.table.splitlines() if "blocking findings remaining" in r]
        self.assertEqual(len(rows), 1)
        self.assertIn("`1`", rows[0])  # `evaluate_ci` returns 0 or 1


if __name__ == "__main__":
    unittest.main()
