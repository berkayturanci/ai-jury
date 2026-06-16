"""Offline coverage top-ups for small modules: findings, commands, scaffold,
formats, benchmark. Targets the residual uncovered lines/branches not already
exercised by the dedicated per-module test files (e.g. tests/test_perfile_coverage
FindingsParseTests). Deterministic and network/subprocess-free."""

from __future__ import annotations

import sys
import unittest
import unittest.mock as mock
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import benchmark, commands, findings, formats, scaffold  # noqa: E402


class FindingsCovTests(unittest.TestCase):
    def test_normalize_severity_unknown_string_falls_back(self):
        # 34->39: a string that is not in SEVERITY_ORDER returns the default.
        self.assertEqual(findings._normalize_severity("totally-bogus"), "info")

    def test_normalize_severity_non_string_falls_back(self):
        # isinstance check is False -> default.
        self.assertEqual(findings._normalize_severity(123), "info")

    def test_normalize_severity_alias(self):
        self.assertEqual(findings._normalize_severity("blocker"), "critical")

    def test_normalize_confidence_unknown_string_falls_back(self):
        # 43->47: string but not in CONFIDENCES -> default.
        self.assertEqual(findings._normalize_confidence("ultra"), "medium")

    def test_normalize_confidence_non_string_falls_back(self):
        self.assertEqual(findings._normalize_confidence(None), "medium")

    def test_normalize_confidence_valid(self):
        self.assertEqual(findings._normalize_confidence(" HIGH "), "high")

    def test_parse_findings_empty_text(self):
        # line 100: empty text short-circuits to ([], []).
        self.assertEqual(findings.parse_findings("", "rev"), ([], []))

    def test_coerce_line_none(self):
        # line 133: None -> None.
        self.assertIsNone(findings._coerce_line(None))

    def test_coerce_line_bool(self):
        # line 133: bool is excluded from int coercion -> None.
        self.assertIsNone(findings._coerce_line(True))

    def test_coerce_line_valid_int(self):
        self.assertEqual(findings._coerce_line("42"), 42)

    def test_coerce_line_invalid_raises_to_none(self):
        # 136-137: non-coercible value -> ValueError caught -> None.
        self.assertIsNone(findings._coerce_line("not-a-number"))
        # TypeError path (object that int() rejects).
        self.assertIsNone(findings._coerce_line(object()))

    def test_normalize_status_valid_with_dashes_and_spaces(self):
        self.assertEqual(findings._normalize_status("needs-human decision"), "needs_human_decision")
        self.assertEqual(findings._normalize_status("VERIFIED"), "verified")

    def test_normalize_status_non_string_falls_back(self):
        # line 145: not a str -> default fallback.
        self.assertEqual(findings._normalize_status(99), "needs_human_decision")

    def test_normalize_status_unknown_string_falls_back(self):
        # line 145: str but not a recognized status -> default fallback.
        self.assertEqual(findings._normalize_status("maybe"), "needs_human_decision")

    def test_parse_verdicts_empty_text(self):
        # line 168: empty output -> warning, no verdicts.
        verdicts, warnings = findings.parse_verdicts("", "v")
        self.assertEqual(verdicts, [])
        self.assertTrue(any("empty output" in w for w in warnings))

    def test_parse_verdicts_no_block(self):
        # line 172: no fenced json block -> warning.
        verdicts, warnings = findings.parse_verdicts("prose only", "v")
        self.assertEqual(verdicts, [])
        self.assertTrue(any("no JSON verdicts block" in w for w in warnings))

    def test_parse_verdicts_malformed_json(self):
        # 177-178: invalid JSON in the block -> malformed warning.
        verdicts, warnings = findings.parse_verdicts("```json\n{not valid}\n```", "v")
        self.assertEqual(verdicts, [])
        self.assertTrue(any("malformed verdicts JSON" in w for w in warnings))

    def test_parse_verdicts_default_label(self):
        # No verifier name -> default "verifier" label used in the warning.
        _, warnings = findings.parse_verdicts("")
        self.assertTrue(any(w.startswith("verifier:") for w in warnings))

    def test_parse_verdicts_dict_findings_key(self):
        # dict with a "findings" key (no "verdicts") is unwrapped to a list.
        verdicts, warnings = findings.parse_verdicts(
            '```json\n{"findings": [{"status": "verified", "claim": "ok"}]}\n```', "v"
        )
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0].status, "verified")
        self.assertEqual(warnings, [])


class CommandsCovTests(unittest.TestCase):
    def test_unbalanced_quotes_raises(self):
        # 69-70: shlex.split raises ValueError -> wrapped in CommandError.
        with self.assertRaises(commands.CommandError) as ctx:
            commands.parse_comment('/jury review "unterminated')
        self.assertIn("could not parse command", str(ctx.exception))

    def test_trigger_without_subcommand_raises(self):
        # line 73: "/jury" with no tokens after it -> missing subcommand.
        with self.assertRaises(commands.CommandError) as ctx:
            commands.parse_comment("/jury")
        self.assertIn("missing subcommand", str(ctx.exception))

    def test_trigger_with_only_whitespace_raises(self):
        # line 73: trailing whitespace tokenizes to no tokens.
        with self.assertRaises(commands.CommandError) as ctx:
            commands.parse_comment("/jury    ")
        self.assertIn("missing subcommand", str(ctx.exception))

    def test_empty_comment_raises(self):
        # line 61: empty/falsy text short-circuits before regex search.
        with self.assertRaises(commands.CommandError) as ctx:
            commands.parse_comment("")
        self.assertIn("empty comment", str(ctx.exception))

    def test_rounds_flag_without_value_raises(self):
        # line 89: "--rounds" as the last token has no following value.
        with self.assertRaises(commands.CommandError) as ctx:
            commands.parse_comment("/jury review --rounds")
        self.assertIn("--rounds requires a value", str(ctx.exception))


class ScaffoldCovTests(unittest.TestCase):
    def test_from_default_unknown_returns_none(self):
        # line 29: a name not present in DEFAULT_CONFIG agents -> None.
        self.assertIsNone(scaffold._from_default("does-not-exist"))

    def test_agent_templates_skips_missing(self):
        # 37->35: a template name absent from DEFAULT_CONFIG is skipped (not
        # added). We force one of the known names to be missing.
        real_from_default = scaffold._from_default

        def fake(name):
            if name == "codex":
                return None
            return real_from_default(name)

        with mock.patch.object(scaffold, "_from_default", side_effect=fake):
            templates = scaffold.agent_templates()
        self.assertNotIn("codex", templates)
        # qwen (the local template) is always present.
        self.assertIn("qwen", templates)

    def test_scalar_unsupported_type_raises(self):
        # line 132: a float is none of bool/int/str -> TypeError.
        with self.assertRaises(TypeError):
            scaffold._scalar(3.14)

    def test_render_toml_omits_empty_agent_values(self):
        # line 170: agent keys whose value is None/""/[] are skipped on render.
        config = {
            "jury": {"rounds": 1, "chair": "qwen", "verify": False},
            "agent": [
                {
                    "name": "qwen",
                    "vendor": "local",
                    "command": "",  # skipped
                    "endpoint": "http://localhost:11434/v1",
                    "model": "qwen2.5-coder:7b",
                    "extra_args": [],  # skipped
                }
            ],
        }
        toml = scaffold.render_toml(config)
        self.assertNotIn("command =", toml)
        self.assertNotIn("extra_args =", toml)
        self.assertIn('model = "qwen2.5-coder:7b"', toml)
        self.assertIn('endpoint = "http://localhost:11434/v1"', toml)


class FormatsCovTests(unittest.TestCase):
    def _make_outcome(self, *, synthesis=None, findings_list=None, groups=None, verdicts=None):
        return SimpleNamespace(
            synthesis=synthesis,
            findings=findings_list or [],
            groups=groups or [],
            verdicts=verdicts or [],
        )

    def test_to_json_synthesis_not_ok_yields_empty_verdict(self):
        # 87->92: synthesis present but ok=False -> verdict_text stays "".
        outcome = self._make_outcome(synthesis=SimpleNamespace(ok=False, output="ignored"))
        with (
            mock.patch("ai_jury.classification.classify", return_value="trivial"),
            mock.patch("ai_jury.formats.build_run_metadata", return_value={}),
        ):
            import json

            doc = json.loads(formats.to_json(outcome, SimpleNamespace()))
        self.assertEqual(doc["verdict"], "")

    def test_to_sarif_no_groups_uses_raw_findings(self):
        # line 143: outcome.groups is empty -> fall back to raw findings.
        f = findings.Finding(severity="major", file="a.py", claim="boom", line=5)
        outcome = self._make_outcome(findings_list=[f], groups=[])
        import json

        doc = json.loads(formats.to_sarif(outcome, SimpleNamespace()))
        results = doc["runs"][0]["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["message"]["text"], "boom")
        # A rule for the 'major' severity is emitted.
        rule_ids = {r["id"] for r in doc["runs"][0]["tool"]["driver"]["rules"]}
        self.assertIn("jury/major", rule_ids)


class BenchmarkCovTests(unittest.TestCase):
    def test_severity_rank_non_string(self):
        # line 95: a non-string severity -> least-severe rank.
        self.assertEqual(benchmark._severity_rank(None), len(benchmark.SEVERITIES) - 1)
        self.assertEqual(benchmark._severity_rank(42), len(benchmark.SEVERITIES) - 1)

    def test_line_matches_finding_line_none(self):
        # line 113: expected line set but finding line None -> no positional match.
        self.assertFalse(benchmark._line_matches(None, 10, benchmark.LINE_TOLERANCE))

    def test_line_matches_non_numeric_raises_to_false(self):
        # 116-117: int() on a non-numeric value -> ValueError/TypeError -> False.
        self.assertFalse(benchmark._line_matches("x", 10, benchmark.LINE_TOLERANCE))
        self.assertFalse(benchmark._line_matches(object(), 10, benchmark.LINE_TOLERANCE))

    def test_line_matches_within_tolerance(self):
        self.assertTrue(benchmark._line_matches(12, 10, benchmark.LINE_TOLERANCE))

    def test_line_matches_expected_none(self):
        self.assertTrue(benchmark._line_matches(None, None, benchmark.LINE_TOLERANCE))

    def test_load_fixture_without_recorded_findings(self):
        # 284->286: a fixture with expected.json + diff but NO findings.json
        # leaves recorded == [].
        import shutil
        import tempfile

        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "x.diff").write_text("diff --git a/a b/a\n", encoding="utf-8")
        (d / "x.expected.json").write_text(
            '{"description": "d", "expect": {"min_findings": 0}}', encoding="utf-8"
        )
        fx = benchmark.load_fixture("x", base_dir=d)
        self.assertEqual(fx.recorded, [])
        self.assertEqual(fx.id, "x")
        self.assertEqual(fx.description, "d")


if __name__ == "__main__":
    unittest.main()
