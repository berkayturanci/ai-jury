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
