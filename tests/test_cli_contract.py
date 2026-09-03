"""Public CLI compatibility contract tests.

The ``jury`` CLI is this project's public API. These tests lock the stable
surfaces (flags, help text, error messages, exit codes, and report headings) so
accidental changes are caught in review. See the "CLI compatibility contract"
section of the README for the documented policy.

The ``--help`` snapshot is stored under ``tests/golden/help.txt``. argparse
wraps help to the terminal width and (on Python 3.13+) colorizes it, so the
render helper pins ``COLUMNS=80`` and ``NO_COLOR=1`` to make the output
deterministic. The exact-match snapshot assertion only runs on Python 3.13,
the version the golden was generated under, because argparse's option
formatting changed in 3.13 (e.g. ``-o, --output OUTPUT``); structural checks
that every documented flag appears run on all supported versions.

Regenerate the golden after an intentional change::

    UPDATE_GOLDEN=1 PYTHONPATH=src python3 -m unittest tests.test_cli_contract
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_jury import __version__  # noqa: E402
from ai_jury.cli import build_parser, main  # noqa: E402

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
HELP_GOLDEN = GOLDEN_DIR / "help.txt"

# A minimal but well-formed unified diff. The mock pipeline keys structured
# findings off "src/example.py", but any non-empty diff exercises the path.
SAMPLE_DIFF = (
    "diff --git a/src/example.py b/src/example.py\n"
    "@@ -1,3 +1,4 @@\n"
    "+def parse(x):\n"
    "+    return int(x)\n"
)

# Every flag the CLI documents as part of its public surface. Used for a
# version-independent structural check on the help text.
DOCUMENTED_FLAGS = [
    "--pr",
    "--issue",
    "--repo",
    "--diff-file",
    "--commit",
    "--commits",
    "--config",
    "--policy",
    "--context-mode",
    "--redact",
    "--no-redact",
    "--rounds",
    "--max-rounds",
    "--early-stop",
    "--no-early-stop",
    "--auto",
    "--no-auto",
    "--total-timeout",
    "--phase-timeout",
    "--retries",
    "--max-diff-bytes",
    "--chunk",
    "--no-chunk",
    "--exclude",
    "--include",
    "--cache",
    "--clear-cache",
    "--cache-dir",
    "--suggest-patches",
    "--patches-out",
    "--incremental",
    "--seed",
    "--chair",
    "--mock",
    "--strict",
    "--min-vendors",
    "--verify",
    "--no-verify",
    "--doctor",
    "--json",
    "--write",
    "--effort",
    "--output",
    "--metadata-json",
    "--format",
    "--decision",
    "--transcript",
    "--no-transcript",
    "--verbose",
    "--live",
    "--theater",
    "--no-theater",
    "--theater-style",
    "--post-summary",
    "--post",
    "--post-inline",
    "--post-progress",
    "--post-mode",
    "--dry-run",
    "--label",
    "--ci",
    "--fail-on",
    "--quiet",
    "--config-validate",
    "--strict-config",
    "--tiered",
    "--hints",
    "--version",
    "--help",
]


def _render_help() -> str:
    """Render ``jury --help`` deterministically (width + color pinned)."""
    saved = {k: os.environ.get(k) for k in ("COLUMNS", "NO_COLOR")}
    os.environ["COLUMNS"] = "80"
    os.environ["NO_COLOR"] = "1"
    try:
        return build_parser().format_help()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _run_cli(argv, stdin=""):
    """Invoke ``main(argv)`` capturing stdout/stderr and the exit code.

    Returns ``(exit_code, stdout, stderr)``. A raised ``SystemExit`` is caught
    and its ``.code`` (an int, or the error-message string for the CLI's
    ``raise SystemExit("error: ...")`` paths) is returned, mirroring how the
    process would actually terminate.
    """
    out, err = io.StringIO(), io.StringIO()
    prev_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
    except SystemExit as exc:
        code = exc.code
    finally:
        sys.stdin = prev_stdin
    return code, out.getvalue(), err.getvalue()


class HelpSnapshotTests(unittest.TestCase):
    """Lock the ``--help`` text against a width/color-pinned golden file."""

    def test_help_render_is_deterministic(self):
        self.assertEqual(_render_help(), _render_help())

    def test_help_lists_every_documented_flag(self):
        # Version-independent: argparse formatting varies across 3.11-3.13, but
        # every public flag must always appear somewhere in the help output.
        help_text = _render_help()
        for flag in DOCUMENTED_FLAGS:
            self.assertIn(flag, help_text, f"{flag} missing from --help")
        self.assertIn("jury", help_text)

    def test_documented_flags_match_parser_exactly(self):
        # Version-INDEPENDENT guarantee that the public flag surface is locked.
        # The exact ``--help`` snapshot below only runs on Python 3.13, so this
        # bidirectional check is what keeps the contract honest on every
        # supported version: it proves no documented flag silently disappeared
        # from the parser AND that DOCUMENTED_FLAGS never drifts out of sync
        # with the actual parser definition (a new flag added without being
        # documented here fails the test, forcing a deliberate update).
        parser = build_parser()
        parser_flags = {
            opt
            for action in parser._actions
            for opt in action.option_strings
            if opt.startswith("--")
        }
        documented = set(DOCUMENTED_FLAGS)
        # ``--help`` is auto-added by argparse rather than declared in
        # build_parser; it is part of the public surface and is asserted
        # explicitly here so the comparison can be exact (nothing excluded).
        self.assertIn("--help", parser_flags)
        self.assertIn("--help", documented)

        missing_from_docs = parser_flags - documented
        self.assertFalse(
            missing_from_docs,
            f"parser exposes flags not in DOCUMENTED_FLAGS: {sorted(missing_from_docs)}",
        )
        stale_in_docs = documented - parser_flags
        self.assertFalse(
            stale_in_docs,
            f"DOCUMENTED_FLAGS lists flags the parser no longer defines: {sorted(stale_in_docs)}",
        )
        self.assertEqual(documented, parser_flags)

    def test_help_matches_golden(self):
        # NOTE: this exact snapshot is pinned to Python 3.13 argparse
        # formatting and self-skips elsewhere. The version-independent
        # guarantee that the public flag surface stays complete and in sync
        # lives in ``test_documented_flags_match_parser_exactly`` above.
        rendered = _render_help()
        if os.environ.get("UPDATE_GOLDEN") == "1":
            GOLDEN_DIR.mkdir(exist_ok=True)
            HELP_GOLDEN.write_text(rendered, encoding="utf-8")
        if sys.version_info[:2] != (3, 13):
            self.skipTest("help golden is pinned to Python 3.13 argparse formatting")
        self.assertTrue(
            HELP_GOLDEN.exists(),
            "help golden missing; regenerate with UPDATE_GOLDEN=1",
        )
        expected = HELP_GOLDEN.read_text(encoding="utf-8")
        self.assertEqual(
            rendered,
            expected,
            "jury --help changed. If intentional, regenerate the golden "
            "with UPDATE_GOLDEN=1 and add a CHANGELOG entry (see the CLI "
            "compatibility contract in the README).",
        )


class VersionTests(unittest.TestCase):
    def test_version_prints_jury_version_and_exits_zero(self):
        code, out, err = _run_cli(["--version"])
        self.assertEqual(code, 0)
        # argparse may print to either stream depending on version; the exact
        # text "jury <version>" is the locked contract.
        self.assertEqual((out + err).strip(), f"jury {__version__}")


class ErrorContractTests(unittest.TestCase):
    """Lock the documented error messages and non-zero exits.

    These invocations are deterministic and offline: none use --pr, so no
    network or real `gh` is touched.
    """

    def test_two_input_sources_are_refused_and_both_are_named(self):
        code, _, _ = _run_cli(["--mock", "--commit", "HEAD", "--pr", "5"], stdin="")
        self.assertIn("choose one input source", code)
        self.assertIn("--pr", code)
        self.assertIn("--commit", code)

    def test_no_input_source(self):
        code, _, _ = _run_cli(["--mock"], stdin="")
        self.assertEqual(
            code,
            "error: provide one of --pr, --issue, --diff-file, --commit, --commits "
            "(or --diff-file - for stdin)",
        )

    def test_empty_diff(self):
        code, _, _ = _run_cli(["--mock", "--diff-file", "-"], stdin="")
        self.assertEqual(code, "error: empty diff — nothing to review")

    def test_post_summary_without_pr(self):
        code, _, _ = _run_cli(["--mock", "--diff-file", "-", "--post-summary"], stdin=SAMPLE_DIFF)
        self.assertEqual(code, "error: --post-summary requires --pr")

    def test_post_inline_without_pr(self):
        code, _, _ = _run_cli(["--mock", "--diff-file", "-", "--post-inline"], stdin=SAMPLE_DIFF)
        self.assertEqual(code, "error: --post-inline requires --pr")

    def test_unknown_flag_exits_2(self):
        # argparse rejects unknown options with exit code 2.
        code, _, _ = _run_cli(["--definitely-not-a-flag"])
        self.assertEqual(code, 2)

    def test_missing_diff_file_exits_with_clean_error(self):
        code, _, err = _run_cli(["--mock", "--diff-file", "/nonexistent/diff.patch"])
        self.assertNotEqual(code, 0)
        self.assertIn("error reading diff file", str(code) + err)


class MockPipelineTests(unittest.TestCase):
    """Lock the deterministic offline pipeline behavior and report headings."""

    def test_mock_run_succeeds_with_stable_headings(self):
        code, out, _ = _run_cli(["--mock", "--diff-file", "-"], stdin=SAMPLE_DIFF)
        self.assertEqual(code, 0)
        for heading in ("AI Jury", "Chair verdict", "Round 1"):
            self.assertIn(heading, out)

    def test_mock_run_is_deterministic(self):
        _, out_a, _ = _run_cli(["--mock", "--diff-file", "-"], stdin=SAMPLE_DIFF)
        _, out_b, _ = _run_cli(["--mock", "--diff-file", "-"], stdin=SAMPLE_DIFF)
        self.assertEqual(out_a, out_b)

    def test_quiet_suppresses_progress_logs(self):
        # --quiet suppresses the "[jury] ..." progress lines on stderr; the
        # report itself still goes to stdout.
        code, out, err = _run_cli(["--mock", "--diff-file", "-", "--quiet"], stdin=SAMPLE_DIFF)
        self.assertEqual(code, 0)
        self.assertNotIn("[jury]", err)
        self.assertIn("AI Jury", out)

    def test_output_writes_report_to_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.md"
            code, out, _ = _run_cli(
                ["--mock", "--diff-file", "-", "--output", str(path)],
                stdin=SAMPLE_DIFF,
            )
            self.assertEqual(code, 0)
            self.assertEqual(out, "")  # report went to the file, not stdout
            self.assertIn("AI Jury", path.read_text(encoding="utf-8"))


class TheaterCliTests(unittest.TestCase):
    """The --theater wiring on an interactive terminal.

    Regression guard: the scene branch only runs when ``supports_scene`` is true
    (a real TTY), which never happens under test capture — so a crash in that
    path (e.g. a wrong ``resolve_chair`` call) slips past every other test. Here
    we force it on (sleeps patched out) and assert the run still completes.
    """

    def _run_theater(self, *extra):
        import unittest.mock as mock

        from ai_jury import theater

        with (
            mock.patch.object(theater, "supports_scene", return_value=True),
            mock.patch("ai_jury.theater.time.sleep"),
        ):
            return _run_cli(["--mock", "--diff-file", "-", "--theater", *extra], stdin=SAMPLE_DIFF)

    def test_theater_flat_completes(self):
        code, _, err = self._run_theater()
        self.assertEqual(code, 0, err)

    def test_theater_pixel_completes(self):
        code, _, err = self._run_theater("--theater-style", "pixel")
        self.assertEqual(code, 0, err)

    def test_theater_issue_vote_completes(self):
        # the path that crashed in the field: --issue + --theater
        code, _, err = self._run_theater("--decision", "vote")
        self.assertEqual(code, 0, err)

    def _run_with_config(self, toml, *extra):
        import unittest.mock as mock

        from ai_jury import theater

        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "jury.toml"
            cfg.write_text(toml, encoding="utf-8")
            with (
                mock.patch.object(theater, "supports_scene", return_value=True),
                mock.patch("ai_jury.theater.time.sleep"),
            ):
                return _run_cli(
                    ["--mock", "--diff-file", "-", "--config", str(cfg), *extra], stdin=SAMPLE_DIFF
                )

    def test_theater_defaulted_on_from_jury_toml(self):
        # [jury] theater = true should render the scene without the CLI flag.
        code, out, err = self._run_with_config(
            "[jury]\ntheater = true\n[[agent]]\nname='m'\nvendor='anthropic'\ncommand='claude'\n"
        )
        self.assertEqual(code, 0, err)
        self.assertIn("\033[?25l", out)  # scene hid the cursor → it ran

    def test_no_theater_overrides_config_on(self):
        code, out, err = self._run_with_config(
            "[jury]\ntheater = true\n[[agent]]\nname='m'\nvendor='anthropic'\ncommand='claude'\n",
            "--no-theater",
        )
        self.assertEqual(code, 0, err)
        self.assertNotIn("\033[?25l", out)  # flag won → no scene


class CiGateTests(unittest.TestCase):
    """Lock the --ci exit-code contract for the mock pipeline."""

    def test_ci_mock_fails_on_blocking_finding(self):
        # The mock pipeline produces a confirmed blocking major finding, so the
        # CI gate must return a non-zero exit code (1).
        code, _, _ = _run_cli(["--ci", "--mock", "--diff-file", "-"], stdin=SAMPLE_DIFF)
        self.assertEqual(code, 1)


class DoctorJsonCliTests(unittest.TestCase):
    """`jury --doctor --json`: exactly one JSON document on stdout (issue #662)."""

    CONFIG = (
        '[jury]\nrounds = 1\nchair = "a"\n\n'
        '[[agent]]\nname = "a"\nvendor = "openai-api"\nmodel = "gpt-x"\neffort = "high"\n'
    )

    def _config_path(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".toml", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(self.CONFIG)
        self.addCleanup(os.unlink, tmp.name)
        return tmp.name

    def test_stdout_is_one_json_document_and_nothing_else(self):
        code, out, _err = _run_cli(["--doctor", "--json", "--config", self._config_path()])
        self.assertEqual(code, 0)
        payload = json.loads(out)  # raises if stdout carries anything but JSON
        self.assertEqual(payload["schema_version"], "ai-jury.doctor.v1")
        self.assertEqual(payload["tool_version"], __version__)
        self.assertNotIn("jury doctor", out)  # the human report stayed out of it

    def test_human_report_is_still_the_default(self):
        code, out, _err = _run_cli(["--doctor", "--config", self._config_path()])
        self.assertEqual(code, 0)
        self.assertIn("jury doctor", out)
        with self.assertRaises(ValueError):
            json.loads(out)

    def test_write_confirmation_moves_to_stderr_under_json(self):
        out_dir = Path(tempfile.mkdtemp())
        target = out_dir / "diagnostics.json"
        code, out, err = _run_cli(
            ["--doctor", "--json", "--write", str(target), "--config", self._config_path()]
        )
        self.assertEqual(code, 0)
        json.loads(out)  # stdout is STILL just the one export
        self.assertIn("Wrote diagnostics", err)
        self.assertTrue(target.exists())

    def test_json_without_doctor_is_rejected(self):
        code, out, err = _run_cli(["--json", "--mock", "--diff-file", "-"], stdin=SAMPLE_DIFF)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("--json applies to --doctor", err)

    def test_the_guard_runs_before_every_short_circuiting_branch(self):
        # It used to sit behind --clear-cache, which returns first, so a
        # misplaced --json was silently accepted on that path.
        out_dir = Path(tempfile.mkdtemp())
        code, out, err = _run_cli(["--json", "--clear-cache", "--cache-dir", str(out_dir)])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("--json applies to --doctor", err)

    def test_effort_choices_come_from_the_adapter_mapping(self):
        from ai_jury.adapters import EFFORT_LEVELS

        action = next(a for a in build_parser()._actions if "--effort" in a.option_strings)
        self.assertEqual(list(action.choices), list(EFFORT_LEVELS))


class EffortCliTests(unittest.TestCase):
    """`--effort` overrides every agent for the run and warns where unsupported."""

    @staticmethod
    def _write(text):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".toml", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(text)
        return tmp.name

    def _captured_config(self, argv, config_text):
        """Run the CLI with the orchestrator stubbed; return the config it got."""
        path = self._write(config_text)
        self.addCleanup(os.unlink, path)
        captured = {}

        def _fake_review_diff(config, *_a, **_kw):
            captured["config"] = config
            raise SystemExit(0)

        with mock.patch("ai_jury.cli.review_diff", side_effect=_fake_review_diff):
            code, out, err = _run_cli(
                [*argv, "--config", path, "--mock", "--diff-file", "-"], stdin=SAMPLE_DIFF
            )
        return captured.get("config"), code, out, err

    CONFIG = (
        '[jury]\nrounds = 1\nchair = "gpt"\n\n'
        '[[agent]]\nname = "gpt"\nvendor = "openai-api"\nmodel = "gpt-x"\neffort = "low"\n\n'
        '[[agent]]\nname = "claude"\nvendor = "anthropic"\ncommand = "claude"\n'
    )

    def test_flag_overrides_every_configured_agent(self):
        config, _code, _out, _err = self._captured_config(["--effort", "high"], self.CONFIG)
        self.assertEqual([a.effort for a in config.agents], ["high", "high"])

    def test_config_effort_is_kept_without_the_flag(self):
        config, _code, _out, _err = self._captured_config([], self.CONFIG)
        self.assertEqual([a.effort for a in config.agents], ["low", None])

    def test_unsupported_vendor_warns_once_per_run(self):
        _config, _code, _out, err = self._captured_config(["--effort", "high"], self.CONFIG)
        warning = "effort unsupported for anthropic, ignored"
        self.assertEqual(err.count(warning), 1)

    def test_no_warning_when_every_vendor_supports_effort(self):
        supported = (
            '[jury]\nrounds = 1\nchair = "gpt"\n\n'
            '[[agent]]\nname = "gpt"\nvendor = "openai-api"\nmodel = "gpt-x"\n'
        )
        _config, _code, _out, err = self._captured_config(["--effort", "high"], supported)
        self.assertNotIn("effort unsupported", err)

    def test_model_listings_are_probed_outside_mock_only(self):
        # --mock must never spawn a vendor CLI to check a model listing.
        for argv, expects_factory in ((["--mock"], False), ([], True)):
            with (
                mock.patch("ai_jury.cli.effort_warnings", return_value=[]) as warned,
                mock.patch("ai_jury.cli.review_diff", side_effect=SystemExit(0)),
            ):
                path = self._write(self.CONFIG)
                self.addCleanup(os.unlink, path)
                _run_cli(
                    ["--effort", "high", "--config", path, "--diff-file", "-", *argv],
                    stdin=SAMPLE_DIFF,
                )
            factory = warned.call_args.kwargs["adapter_factory"]
            self.assertEqual(factory is not None, expects_factory, argv)

    def test_invalid_level_is_rejected_by_the_parser(self):
        code, _out, err = _run_cli(
            ["--effort", "maximum", "--mock", "--diff-file", "-"], stdin=SAMPLE_DIFF
        )
        self.assertNotEqual(code, 0)
        self.assertIn("--effort", err)


if __name__ == "__main__":
    unittest.main()
