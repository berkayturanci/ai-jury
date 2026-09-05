"""Unit tests for tiered model routing and hints (issue #524, #523, #715)."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_jury import orchestrator  # noqa: E402
from ai_jury.cli import build_parser, main  # noqa: E402
from ai_jury.config import (  # noqa: E402
    KNOWN_JURY_KEYS,
    JuryConfig,
    _from_dict,
    config_hash,
    validate_config,
)
from ai_jury.diffprofile import profile_diff  # noqa: E402

SAMPLE_DIFF = "diff --git a/a.py b/a.py\n@@ -0,0 +1 @@\n+x = 1\n"
HINT_MARKER = "SENTINEL-HINT-715"
HINT_BLOCK = f"## Static Analysis Hints (Pre-pass)\n\n- {HINT_MARKER}"

#: A config that turns the pre-pass on from the file, so the CLI flags are
#: exercised as overrides in both directions (#715).
HINTS_ON_TOML = """
[jury]
rounds = 1
verify = false
chair = "a"
hints = true
routing = "tiered"

[[agent]]
name = "a"
vendor = "anthropic"
command = "claude"
"""


def _run_cli(argv):
    """Run ``main(argv)`` on ``SAMPLE_DIFF`` from stdin, muting the report."""
    prev_stdin = sys.stdin
    sys.stdin = io.StringIO(SAMPLE_DIFF)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return main(argv)
    finally:
        sys.stdin = prev_stdin


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


class StaticHintsContractTests(unittest.TestCase):
    """The documented `hints` contract, end to end (issue #715).

    The pre-pass used to be appended to the user context in the CLI, which
    ``run_jury`` clears under the default "diff-only" context mode — so the
    linters ran and no reviewer ever saw a hint. These tests assert on the
    Round 1 PROMPT, not on the exit code, which stayed 0 throughout.
    """

    def _round1_prompts(self, argv, hints=HINT_BLOCK):
        """Return the Round 1 prompts of a mock run of *argv*, as one string."""
        captured: list[str] = []
        real = orchestrator._run_phase

        def spy(adapters, prompt_for, phase, parallel, **kwargs):
            if phase == "review":
                captured.extend(prompt_for.values())
            return real(adapters, prompt_for, phase, parallel, **kwargs)

        with (
            patch("ai_jury.hints.collect_static_hints", return_value=hints) as collect,
            patch("ai_jury.orchestrator._run_phase", side_effect=spy),
        ):
            code = _run_cli(argv)
        self.assertEqual(code, 0)
        self.assertTrue(captured, "round 1 ran no reviewer")
        return "\n".join(captured), collect

    def test_hints_reach_round_one_under_the_default_context_mode(self):
        # No [jury.context] section => context_mode "diff-only" => the old code
        # dropped the hints here.
        prompts, _ = self._round1_prompts(["--mock", "--diff-file", "-", "--hints"])
        self.assertIn(HINT_MARKER, prompts)

    def test_without_hints_the_block_is_absent(self):
        prompts, collect = self._round1_prompts(["--mock", "--diff-file", "-"])
        self.assertNotIn(HINT_MARKER, prompts)
        collect.assert_not_called()

    def test_empty_hint_output_leaves_the_prompt_unchanged(self):
        prompts, _ = self._round1_prompts(["--mock", "--diff-file", "-", "--hints"], hints="")
        self.assertNotIn(HINT_MARKER, prompts)
        self.assertIn("_(none)_", prompts)

    def test_no_hints_turns_off_a_config_that_enables_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = str(Path(tmp) / "jury.toml")
            Path(cfg_path).write_text(HINTS_ON_TOML, encoding="utf-8")
            on, _ = self._round1_prompts(["--mock", "--diff-file", "-", "--config", cfg_path])
            self.assertIn(HINT_MARKER, on)
            off, collect = self._round1_prompts(
                ["--mock", "--diff-file", "-", "--config", cfg_path, "--no-hints"]
            )
        self.assertNotIn(HINT_MARKER, off)
        collect.assert_not_called()

    def test_flag_default_is_a_sentinel_so_the_config_decides(self):
        parse = build_parser().parse_args
        self.assertIsNone(parse(["--diff-file", "-"]).hints)
        self.assertTrue(parse(["--diff-file", "-", "--hints"]).hints)
        self.assertFalse(parse(["--diff-file", "-", "--no-hints"]).hints)

    def test_hints_survive_the_chunked_path(self):
        # A diff over `max_bytes` with `chunk = true` is split per file and each
        # chunk reviewed on its own (`mode: chunked`); the hints must reach the
        # Round 1 prompt of every chunk, not just the unchunked path.
        config = _from_dict(
            tomllib.loads(HINTS_ON_TOML + "\n[jury.diff]\nchunk = true\nmax_bytes = 60\n")
        )
        two_files = SAMPLE_DIFF + SAMPLE_DIFF.replace("a.py", "b.py")
        captured: list[str] = []
        real = orchestrator._run_phase

        def spy(adapters, prompt_for, phase, parallel, **kwargs):
            if phase == "review":
                captured.extend(prompt_for.values())
            return real(adapters, prompt_for, phase, parallel, **kwargs)

        with patch("ai_jury.orchestrator._run_phase", side_effect=spy):
            outcome, plan = orchestrator.review_diff(config, two_files, hints=HINT_BLOCK, mock=True)
        self.assertEqual(plan.mode, "chunked")
        self.assertGreaterEqual(len(captured), 2)
        self.assertTrue(all(HINT_MARKER in prompt for prompt in captured))
        self.assertTrue(outcome.reviews)


class StaticHintsConfigContractTests(unittest.TestCase):
    """`hints` / `routing` as first-class `[jury]` keys (issue #715)."""

    @staticmethod
    def _config(**jury):
        return {
            "jury": {"rounds": 1, "chair": "a", **jury},
            "agent": [{"name": "a", "vendor": "anthropic", "command": "claude"}],
        }

    def test_hints_and_routing_are_known_jury_keys(self):
        # Not merely parsed: the documented example must not warn, so that
        # `--strict-config` (which promotes warnings to exit 2) accepts it.
        self.assertIn("hints", KNOWN_JURY_KEYS)
        self.assertIn("routing", KNOWN_JURY_KEYS)
        self.assertEqual(validate_config(self._config(hints=True, routing="tiered")), [])

    def test_strict_config_accepts_the_documented_example(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = str(Path(tmp) / "jury.toml")
            Path(cfg_path).write_text(HINTS_ON_TOML, encoding="utf-8")
            with patch("ai_jury.hints.collect_static_hints", return_value=""):
                code = _run_cli(
                    ["--mock", "--diff-file", "-", "--config", cfg_path, "--strict-config"]
                )
        self.assertEqual(code, 0)

    def test_hints_change_the_config_hash(self):
        # A cached outcome from a run that never saw the hints must not be
        # served to a run that asked for them.
        base = _from_dict(self._config())
        self.assertNotEqual(config_hash(base), config_hash(_from_dict(self._config(hints=True))))

    def test_routing_changes_the_config_hash(self):
        base = _from_dict(self._config())
        other = _from_dict(self._config(routing="tiered"))
        self.assertNotEqual(config_hash(base), config_hash(other))


if __name__ == "__main__":
    unittest.main()


TIERED_TOML = """
[jury]
rounds = 2
verify = true
chair = "claude"
routing = "tiered"

[jury.ci]
min_vendors = 0

[[agent]]
name = "claude"
vendor = "anthropic"
command = "claude"

[[agent]]
name = "gpt"
vendor = "openai"
command = "codex"

[[agent]]
name = "cheap"
vendor = "google"
command = "agy"
tier = "economical"
"""

#: A one-file, one-line change: risk "low" under diffprofile.
ROUTINE_DIFF = SAMPLE_DIFF
#: A change under a security-sensitive path: risk "high".
SENSITIVE_DIFF = "diff --git a/src/auth/login.py b/src/auth/login.py\n@@ -0,0 +1 @@\n+x = 1\n"


def _tiered_config(**jury):
    data = tomllib.loads(TIERED_TOML)
    data["jury"].update(jury)
    return _from_dict(data)


class TieredRoutingRunsThePanelItPlans(unittest.TestCase):
    """The plan is what round 1 runs, and the report says so (#714)."""

    def test_a_routine_diff_benches_the_second_frontier_seat(self):
        outcome = orchestrator.run_jury(_tiered_config(), ROUTINE_DIFF, mock=True)
        self.assertEqual([r.agent for r in outcome.reviews], ["claude", "cheap"])
        self.assertEqual(outcome.routing["mode"], "tiered")
        self.assertEqual(outcome.routing["risk"], "low")
        self.assertEqual(outcome.routing["benched"], ["gpt"])
        self.assertEqual(outcome.routing["anchor"], "claude")

    def test_a_sensitive_diff_seats_the_full_panel(self):
        outcome = orchestrator.run_jury(_tiered_config(), SENSITIVE_DIFF, mock=True)
        self.assertEqual([r.agent for r in outcome.reviews], ["claude", "gpt", "cheap"])
        self.assertEqual(outcome.routing["risk"], "high")
        self.assertEqual(outcome.routing["benched"], [])
        self.assertFalse(outcome.routing["escalated"])

    def test_standard_routing_is_unchanged_and_recorded(self):
        outcome = orchestrator.run_jury(_tiered_config(routing="standard"), ROUTINE_DIFF, mock=True)
        self.assertEqual([r.agent for r in outcome.reviews], ["claude", "gpt", "cheap"])
        self.assertEqual(outcome.routing["mode"], "standard")
        self.assertEqual(outcome.routing["panel"], ["claude", "gpt", "cheap"])

    def test_a_major_finding_escalates_the_benched_seat_into_the_debate(self):
        # The mock reviewer reports a major finding, so round 1 escalates: the
        # benched frontier seat cross-examines and the chair stays frontier.
        outcome = orchestrator.run_jury(_tiered_config(), ROUTINE_DIFF, mock=True)
        self.assertTrue(outcome.routing["escalated"])
        self.assertIn("major", outcome.routing["escalation_reason"])
        self.assertEqual([r.agent for r in outcome.debate], ["claude", "cheap", "gpt"])
        self.assertEqual(outcome.chair, "claude")

    def test_without_escalation_the_benched_seat_never_runs(self):
        with patch("ai_jury.routing.should_escalate", return_value=(False, "quiet round")):
            outcome = orchestrator.run_jury(_tiered_config(), ROUTINE_DIFF, mock=True)
        self.assertFalse(outcome.routing["escalated"])
        self.assertEqual(outcome.routing["escalation_reason"], "quiet round")
        self.assertEqual([r.agent for r in outcome.debate], ["claude", "cheap"])
        every_phase = list(outcome.reviews) + list(outcome.debate)
        for result in (outcome.verify, outcome.synthesis):
            if result is not None:
                every_phase.append(result)
        self.assertNotIn("gpt", {r.agent for r in every_phase})

    def test_a_single_round_run_escalates_the_chair_and_says_the_bench_did_not_run(self):
        # Round-1 finding: `--auto` sets rounds = 1 on the routine band that
        # benched the seats, and a single-round run has no debate to join. The
        # chair still moves to a frontier seat; the record says so instead of
        # promising a debate that never happens.
        outcome = orchestrator.run_jury(_tiered_config(rounds=1), ROUTINE_DIFF, mock=True)
        self.assertTrue(outcome.routing["escalated"])
        self.assertIn("only the chair was escalated", outcome.routing["escalation_reason"])
        self.assertEqual(outcome.debate, [])
        self.assertEqual([r.agent for r in outcome.reviews], ["claude", "cheap"])
        self.assertEqual(outcome.chair, "claude")

    def test_an_auto_depth_single_round_run_never_claims_a_debate(self):
        # The combination the finding named: --auto's own depth for a low-risk
        # diff is (1 round, no verify), and --tiered reads the same band.
        from ai_jury.diffprofile import depth_for

        rounds, verify, early_stop = depth_for("low")
        self.assertEqual(rounds, 1)
        config = _tiered_config(rounds=rounds, verify=verify, chair="cheap")
        config.early_stop = early_stop
        lines: list[str] = []
        outcome = orchestrator.run_jury(config, ROUTINE_DIFF, mock=True, log=lines.append)
        self.assertEqual(outcome.debate, [])
        self.assertNotIn("gpt", {r.agent for r in outcome.reviews})
        self.assertIn("only the chair was escalated", outcome.routing["escalation_reason"])
        self.assertFalse(any("joined the debate" in line for line in lines), lines)
        # The chair is still a frontier seat, so a frontier model reads the
        # findings in synthesis even though no debate ran.
        self.assertEqual(outcome.chair, "claude")

    def test_a_two_round_run_does_claim_and_run_the_debate(self):
        outcome = orchestrator.run_jury(_tiered_config(rounds=2), ROUTINE_DIFF, mock=True)
        self.assertIn("gpt joined the debate", outcome.routing["escalation_reason"])
        self.assertEqual([r.agent for r in outcome.debate], ["claude", "cheap", "gpt"])

    def test_one_seated_review_still_gets_a_debate_because_the_bench_joins_it(self):
        # A seated seat failing leaves one successful review, which alone
        # cannot debate. The escalated frontier seat is the second voice: it
        # cross-examines what the one review said, and the record says so.
        real = orchestrator._run_phase

        def one_seat_fails(adapters, prompt_for, phase, parallel, **kwargs):
            results = real(adapters, prompt_for, phase, parallel, **kwargs)
            if phase == "review":
                for r in results:
                    if r.agent == "cheap":
                        r.ok = False
                        r.output = ""
            return results

        with patch("ai_jury.orchestrator._run_phase", side_effect=one_seat_fails):
            outcome = orchestrator.run_jury(_tiered_config(rounds=2), ROUTINE_DIFF, mock=True)
        self.assertEqual(outcome.routing["benched"], ["gpt"])
        self.assertTrue(outcome.routing["escalated"])
        self.assertIn("gpt joined the debate", outcome.routing["escalation_reason"])
        self.assertEqual([r.agent for r in outcome.debate], ["claude", "gpt"])

    def test_an_adaptive_run_that_converges_says_the_bench_did_not_run(self):
        # The other prediction that was wrong (review round 2): `--auto` sets
        # early_stop on the `medium` band, and a unanimous panel converges after
        # round 1 — so the debate the record was going to claim never runs.
        config = _tiered_config(rounds=2)
        config.early_stop = True
        config.max_rounds = 3
        with patch("ai_jury.convergence.review_convergence", return_value=(True, "unanimous")):
            outcome = orchestrator.run_jury(config, ROUTINE_DIFF, mock=True)
        self.assertEqual(outcome.debate, [])
        self.assertTrue(outcome.routing["escalated"])
        self.assertIn("only the chair was escalated", outcome.routing["escalation_reason"])
        self.assertNotIn("gpt", {r.agent for r in outcome.reviews})

    def test_an_economical_chair_is_replaced_by_a_frontier_one_on_escalation(self):
        outcome = orchestrator.run_jury(_tiered_config(chair="cheap"), ROUTINE_DIFF, mock=True)
        self.assertEqual(outcome.routing["anchor"], "claude")
        self.assertTrue(outcome.routing["escalated"])
        self.assertEqual(outcome.chair, "claude")

    def test_the_shipped_vendor_floor_still_benches(self):
        # The realistic case: `[jury.ci] min_vendors` ships as 2, and the routed
        # panel (claude + cheap) already carries two identities, so the feature
        # is not defeated by the default the cross-vendor gate is set to.
        config = _tiered_config()
        config.ci.min_vendors = 2
        outcome = orchestrator.run_jury(config, ROUTINE_DIFF, mock=True)
        self.assertEqual([r.agent for r in outcome.reviews], ["claude", "cheap"])
        self.assertEqual(outcome.routing["benched"], ["gpt"])
        # And the gate that runs after the panel is paid for is satisfied.
        from ai_jury.metadata import collapse_reason

        self.assertIsNone(collapse_reason(outcome.reviews, 2))

    def test_the_vendor_floor_reaches_the_plan(self):
        config = _tiered_config()
        config.ci.min_vendors = 3
        outcome = orchestrator.run_jury(config, ROUTINE_DIFF, mock=True)
        self.assertEqual([r.agent for r in outcome.reviews], ["claude", "gpt", "cheap"])
        self.assertIn("min_vendors=3", outcome.routing["reason"])

    def test_the_log_names_the_decision(self):
        lines: list[str] = []
        orchestrator.run_jury(_tiered_config(), ROUTINE_DIFF, mock=True, log=lines.append)
        self.assertTrue(any(line.startswith("tiered routing: risk=low") for line in lines), lines)
        self.assertTrue(any("escalating" in line for line in lines), lines)


class ChunkingDoesNotDowngradeTheBand(unittest.TestCase):
    """A chunk of a big change is not a routine change (#714).

    `review_diff` reviews an over-size diff chunk by chunk, and each chunk on
    its own can look small enough to bench the frontier seats — on exactly the
    change they were kept for. The band is computed once, on the whole filtered
    diff, and threaded through every chunk.
    """

    @staticmethod
    def _big_diff(files=6, lines=90):
        parts = []
        for i in range(files):
            body = "".join(f"+line {n}\n" for n in range(lines))
            parts.append(f"diff --git a/f{i}.py b/f{i}.py\n@@ -0,0 +1,{lines} @@\n{body}")
        return "".join(parts)

    def _config(self):
        config = _tiered_config()
        config.diff.chunk = True
        config.diff.max_bytes = 400
        config.diff.chunk_max_bytes = 400
        return config

    def test_a_chunked_high_risk_diff_still_seats_the_full_panel(self):
        diff = self._big_diff()
        self.assertEqual(profile_diff(diff).risk, "high")
        # Any single chunk, on its own, is not high risk.
        one_file = diff.split("diff --git")[1]
        self.assertNotEqual(profile_diff("diff --git" + one_file).risk, "high")
        outcome, plan = orchestrator.review_diff(self._config(), diff, mock=True)
        self.assertEqual(plan.mode, "chunked")
        self.assertEqual(outcome.routing["risk"], "high")
        self.assertEqual(outcome.routing["benched"], [])
        self.assertEqual(sorted(r.agent for r in outcome.reviews), ["cheap", "claude", "gpt"])

    def test_the_merged_record_reports_escalation_from_any_chunk(self):
        # The mock reviewer reports a major finding on every chunk, so the
        # merged record says the run escalated and why.
        config = self._config()
        config.diff.max_bytes = 100_000  # one chunk, routine band
        outcome, _plan = orchestrator.review_diff(config, ROUTINE_DIFF, mock=True)
        self.assertEqual(outcome.routing["risk"], "low")
        self.assertTrue(outcome.routing["escalated"])


class TheRoutingRecordSurvivesTheCache(unittest.TestCase):
    """A cached tiered run reports the plan it ran, not a standard one (#714).

    `outcome_to_dict` serialises the whole dataclass; the reader rebuilds it
    field by field, and a field it forgets is a field a `--cache` hit reports
    wrongly — the defect class of #722.
    """

    def test_the_plan_round_trips(self):
        from ai_jury.cache import outcome_from_dict, outcome_to_dict

        outcome = orchestrator.run_jury(_tiered_config(), ROUTINE_DIFF, mock=True)
        restored = outcome_from_dict(outcome_to_dict(outcome))
        self.assertEqual(restored.routing, outcome.routing)
        self.assertEqual(restored.routing["benched"], ["gpt"])
        self.assertTrue(restored.routing["escalated"])

    def test_an_entry_written_before_the_key_reads_as_the_standard_run_it_was(self):
        from ai_jury.cache import outcome_from_dict, outcome_to_dict

        outcome = orchestrator.run_jury(_tiered_config(routing="standard"), ROUTINE_DIFF, mock=True)
        stored = outcome_to_dict(outcome)
        del stored["routing"]
        restored = outcome_from_dict(stored)
        self.assertEqual(restored.routing["mode"], "standard")
        self.assertEqual(restored.routing["panel"], ["claude", "gpt", "cheap"])
        self.assertEqual(restored.routing["benched"], [])


class TieredRoutingReachesTheReports(unittest.TestCase):
    def _json_run(self, extra):
        import json as _json

        from ai_jury.formats import to_json

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = str(Path(tmp) / "jury.toml")
            Path(cfg_path).write_text(TIERED_TOML, encoding="utf-8")
            captured = io.StringIO()
            prev_stdin = sys.stdin
            sys.stdin = io.StringIO(ROUTINE_DIFF)
            try:
                with contextlib.redirect_stdout(captured):
                    code = main(
                        ["--mock", "--diff-file", "-", "--config", cfg_path, "--format", "json"]
                        + extra
                    )
            finally:
                sys.stdin = prev_stdin
        self.assertEqual(code, 0, captured.getvalue()[-500:])
        del to_json
        return _json.loads(captured.getvalue())

    def test_the_json_report_carries_the_routing_block(self):
        doc = self._json_run([])
        self.assertEqual(doc["schema_version"], "1.4")
        self.assertEqual(doc["metadata"]["schema_version"], 7)
        routing_meta = doc["metadata"]["routing"]
        self.assertEqual(routing_meta["mode"], "tiered")
        self.assertEqual(routing_meta["panel"], ["claude", "cheap"])
        self.assertEqual(routing_meta["benched"], ["gpt"])
        self.assertEqual(
            [r["name"] for r in doc["reviewers"] if r["role"] == "panelist"],
            ["claude", "cheap"],
        )

    def test_the_min_vendors_flag_reaches_the_plan(self):
        doc = self._json_run(["--min-vendors", "3"])
        self.assertEqual(doc["metadata"]["routing"]["panel"], ["claude", "gpt", "cheap"])

    def test_the_markdown_report_names_the_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = str(Path(tmp) / "jury.toml")
            Path(cfg_path).write_text(TIERED_TOML, encoding="utf-8")
            captured = io.StringIO()
            prev_stdin = sys.stdin
            sys.stdin = io.StringIO(ROUTINE_DIFF)
            try:
                with contextlib.redirect_stdout(captured):
                    code = main(["--mock", "--diff-file", "-", "--config", cfg_path])
            finally:
                sys.stdin = prev_stdin
        self.assertEqual(code, 0)
        text = captured.getvalue()
        self.assertIn("routing: tiered — risk=low", text)
        self.assertIn("escalated:", text)
