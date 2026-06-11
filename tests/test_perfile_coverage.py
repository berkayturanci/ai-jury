"""Push benchmark/doctor/cli over 90%: interactive init, benchmark live/edges,
and doctor diagnostics branches. Network/subprocess-free (mocked)."""

from __future__ import annotations

import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import benchmark, cli, doctor  # noqa: E402


class InitInteractiveTests(unittest.TestCase):
    def test_defaults_accepted(self):
        avail = {"claude": True, "codex": True}
        kw = cli._init_interactive(avail, input_fn=lambda _p: "", models_fn=lambda _e: [])
        self.assertEqual(kw["agents"], ["claude", "codex"])
        self.assertEqual(kw["rounds"], 2)
        self.assertEqual(kw["chair"], "claude")
        self.assertTrue(kw["verify"])
        self.assertIsNone(kw["local_model"])

    def test_explicit_answers(self):
        answers = iter(["claude,codex", "1", "codex", "n"])
        kw = cli._init_interactive(
            {"claude": True}, input_fn=lambda _p: next(answers), models_fn=lambda _e: []
        )
        self.assertEqual(kw["agents"], ["claude", "codex"])
        self.assertEqual(kw["rounds"], 1)
        self.assertEqual(kw["chair"], "codex")
        self.assertFalse(kw["verify"])

    def test_local_model_pick_by_number(self):
        answers = iter(["claude,qwen", "", "", "", "2"])  # agents, rounds, chair, verify, model#2
        kw = cli._init_interactive(
            {"claude": True, "qwen": True},
            input_fn=lambda _p: next(answers),
            models_fn=lambda _e: ["qwen2.5-coder:7b", "gemma:2b"],
        )
        self.assertEqual(kw["local_model"], "gemma:2b")

    def test_local_model_pick_by_name(self):
        answers = iter(["qwen", "", "", "", "deepseek-coder:6.7b"])
        kw = cli._init_interactive(
            {"qwen": True},
            input_fn=lambda _p: next(answers),
            models_fn=lambda _e: ["qwen2.5-coder:7b", "deepseek-coder:6.7b"],
        )
        self.assertEqual(kw["local_model"], "deepseek-coder:6.7b")

    def test_local_no_models_reachable(self):
        answers = iter(["qwen", "", "", "", "my-model"])  # last = typed model name
        kw = cli._init_interactive(
            {"qwen": True},
            input_fn=lambda _p: next(answers),
            models_fn=lambda _e: [],
        )
        self.assertEqual(kw["local_model"], "my-model")


class BenchmarkEdgeTests(unittest.TestCase):
    def test_severity_rank_unknown(self):
        self.assertEqual(benchmark._severity_rank("bogus"), len(benchmark.SEVERITIES) - 1)

    def test_keywords_match_false(self):
        f = {"claim": "something unrelated", "evidence": ""}
        self.assertFalse(benchmark._keywords_match(f, ["sqlinjection"]))

    def test_keywords_match_empty_is_true(self):
        self.assertTrue(benchmark._keywords_match({"claim": "x"}, []))

    def test_discover_fixture_ids_missing_dir(self):
        self.assertEqual(benchmark.discover_fixture_ids(Path("/no/such/dir")), [])

    def test_run_live_disabled_raises(self):
        with (
            mock.patch("ai_jury.benchmark.live_enabled", return_value=False),
            self.assertRaises(RuntimeError),
        ):
            benchmark.run_live()

    def test_run_live_mocked(self):
        fx = SimpleNamespace(diff="diff --git a/a b/a\n", expected={})
        outcome = SimpleNamespace(findings=[])
        with (
            mock.patch("ai_jury.benchmark.live_enabled", return_value=True),
            mock.patch("ai_jury.benchmark.load_fixtures", return_value=[fx]),
            mock.patch("ai_jury.orchestrator.run_jury", return_value=outcome),
        ):
            scores, agg = benchmark.run_live()
        self.assertEqual(len(scores), 1)
        self.assertIn("fixtures", agg)


class DoctorDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_all_agents_disabled(self):
        cfg = self.d / "jury.toml"
        cfg.write_text(
            '[jury]\nrounds = 1\nchair = "a"\n\n[[agent]]\nname = "a"\nvendor = "anthropic"\ncommand = "x"\nenabled = false\n'
        )
        diag = doctor.build_diagnostics(str(cfg))
        report = doctor.render_report(diag)
        self.assertIn("ready to run", report)

    def test_unloadable_config_renders(self):
        bad = self.d / "broken.toml"
        bad.write_text("this is not = valid = toml [[[")
        diag = doctor.build_diagnostics(str(bad))
        report = doctor.render_report(diag)
        self.assertIsInstance(report, str)
        self.assertIn("ready to run", report)

    def test_valid_config_report(self):
        cfg = self.d / "ok.toml"
        cfg.write_text(
            '[jury]\nrounds = 1\nchair = "a"\n\n[[agent]]\nname = "a"\nvendor = "anthropic"\ncommand = "definitely-not-on-path-xyz"\n'
        )
        diag = doctor.build_diagnostics(str(cfg))
        report = doctor.render_report(diag)
        self.assertIn("ready to run", report)


class CliExtraPathsTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.diff = self.d / "x.diff"
        self.diff.write_text(
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n"
        )

    def _run(self, args):
        import contextlib
        import io

        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(args)
        except SystemExit as exc:
            code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_init_preset_thorough_all_agents(self):
        out = self.d / "t.toml"
        code, _, _ = self._run(["init", "--preset", "thorough", "-o", str(out), "--force"])
        self.assertEqual(code, 0)

    def test_init_preset_fast_detected(self):
        out = self.d / "f.toml"
        with mock.patch("ai_jury.adapters.list_local_models", return_value=[]):
            code, _, _ = self._run(["init", "--preset", "fast", "-o", str(out), "--force"])
        self.assertEqual(code, 0)

    def test_policy_load_error_exits_2(self):
        bad_policy = self.d / "policy.toml"
        bad_policy.write_text("this is not = valid [[[ toml")
        code, _, err = self._run(
            ["--mock", "--diff-file", str(self.diff), "-q", "--policy", str(bad_policy)]
        )
        self.assertEqual(code, 2)
        self.assertIn("error", err.lower())

    def test_suggest_patches_to_file(self):
        patches = self.d / "patches.md"
        code, _, _ = self._run(
            [
                "--mock",
                "--diff-file",
                str(self.diff),
                "-q",
                "--suggest-patches",
                "--patches-out",
                str(patches),
                "--seed",
                "1",
            ]
        )
        self.assertEqual(code, 0)

    def test_suggest_patches_json_skipped(self):
        code, _, _ = self._run(
            [
                "--mock",
                "--diff-file",
                str(self.diff),
                "-q",
                "--format",
                "json",
                "--suggest-patches",
                "--seed",
                "1",
            ]
        )
        self.assertEqual(code, 0)


class FindingsParseTests(unittest.TestCase):
    def test_parse_findings_no_json(self):
        from ai_jury.findings import parse_findings

        findings, _ = parse_findings("just prose, no fenced json block", "rev")
        self.assertEqual(findings, [])

    def test_from_obj_bad_line_coerces_none(self):
        from ai_jury.findings import Finding

        f = Finding.from_obj(
            {"severity": "major", "file": "a.py", "claim": "c", "line": "not-an-int"}, "rev"
        )
        self.assertIsNone(f.line)

    def test_parse_verdicts_not_array(self):
        from ai_jury.findings import parse_verdicts

        # A "verdicts" value that isn't a list → "not a JSON array" warning.
        verdicts, warnings = parse_verdicts('```json\n{"verdicts": 123}\n```', "v")
        self.assertEqual(verdicts, [])
        self.assertTrue(warnings)

    def test_parse_verdicts_non_object_items(self):
        from ai_jury.findings import parse_verdicts

        verdicts, warnings = parse_verdicts('```json\n[1, "two"]\n```', "v")
        self.assertTrue(any("expected object" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
