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

import argparse
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_jury import __version__  # noqa: E402
from ai_jury.cli import build_parser, main  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
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

#: The two documents that promise to list every public flag. The README's
#: "Stable flags" list and the "CLI flags" tables of the parameter reference are
#: what a user reads; the test below reads the flags out of *them* and compares
#: each with the parser. It used to compare the parser with a list kept in this
#: file, which stayed complete while the README lacked six flags and the
#: parameter reference three (#868).
README = REPO_ROOT / "README.md"
PARAMETERS = REPO_ROOT / "docs" / "parameters.md"

#: A long option inside an inline code span: `--effort {low,medium,high}` names
#: `--effort`, `-o` / `--output` names `--output`.
_LONG_FLAG = re.compile(r"`(--[a-z][a-z0-9-]*)")


def _section(text: str, start: str, end: str) -> str:
    """The text between the first `start` and the first `end` after it."""
    head = text.index(start)
    return text[head : text.index(end, head)]


def parser_flags() -> set[str]:
    """Every long option the real `jury` parser accepts, `--help` included."""
    return {
        opt
        for action in build_parser()._actions
        for opt in action.option_strings
        if opt.startswith("--")
    }


def readme_flags() -> set[str]:
    """The long options the README's "Stable flags" list names."""
    text = README.read_text(encoding="utf-8")
    block = _section(text, "**Stable flags**", "**Stable error messages")
    bullets = "\n".join(line for line in block.splitlines() if line.startswith(("- ", "  ")))
    return set(_LONG_FLAG.findall(bullets))


def parameters_flags() -> set[str]:
    """The long options the first column of the parameter reference's flag tables names."""
    text = PARAMETERS.read_text(encoding="utf-8")
    block = _section(text, "\n## CLI flags\n", "\n## Subcommands\n")
    first_cells = (line.split("|")[1] for line in block.splitlines() if line.startswith("| `"))
    return set(_LONG_FLAG.findall("\n".join(first_cells)))


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

    def test_help_lists_every_parser_flag(self):
        # Version-independent: argparse formatting varies across Python versions, but
        # every public flag must always appear somewhere in the help output.
        help_text = _render_help()
        for flag in sorted(parser_flags()):
            self.assertIn(flag, help_text, f"{flag} missing from --help")
        self.assertIn("jury", help_text)

    def _assert_documents_the_parser(self, documented: set[str], where: str):
        # Both directions: a flag the parser accepts that the page leaves out, and a
        # flag the page names that the parser no longer defines. ``--help`` is
        # auto-added by argparse and is part of the public surface, so nothing is
        # excluded and the comparison can be exact.
        actual = parser_flags()
        self.assertIn("--help", actual)
        self.assertEqual(
            sorted(actual - documented), [], f"the parser accepts flags {where} does not list"
        )
        self.assertEqual(
            sorted(documented - actual), [], f"{where} lists flags the parser does not define"
        )

    def test_readme_stable_flags_match_the_parser(self):
        # Version-INDEPENDENT guarantee that the public flag surface is locked. The
        # exact ``--help`` snapshot below only runs on Python 3.13; this reads the
        # README's own "Stable flags" list, which is the promise a user reads.
        self._assert_documents_the_parser(readme_flags(), "README.md's Stable flags")

    def test_parameter_reference_matches_the_parser(self):
        # docs/parameters.md says it covers every CLI flag; its "CLI flags" tables
        # are held to that (#868).
        self._assert_documents_the_parser(parameters_flags(), "docs/parameters.md's CLI flags")

    def test_help_matches_golden(self):
        # NOTE: this exact snapshot is pinned to Python 3.13 argparse
        # formatting and self-skips elsewhere. The version-independent
        # guarantee that the public flag surface stays complete and in sync
        # lives in ``test_readme_stable_flags_match_the_parser`` above.
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

    def test_no_input_source_under_mock_runs_the_bundled_demo(self):
        # Contract change (#21): `jury --mock` with no diff source is the offline
        # demo — it reviews a diff bundled with the package rather than erroring.
        # The no-source error is now reserved for real (non-mock) runs, and is
        # locked by test_offline_demo / test_commit_sources at the unit level.
        code, out, err = _run_cli(["--mock"], stdin="")
        self.assertEqual(code, 0)
        self.assertIn("AI Jury", out)
        self.assertIn("bundled offline-demo diff", err)

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

    def test_missing_diff_file_error_redacts_the_exception_text(self):
        # The OSError repeats the path; a token-shaped component in it must
        # come back as the placeholder (#658). The prefix quotes the argument
        # verbatim, so the raw token appears exactly once — in the user's own
        # input — and never in the interpolated exception.
        token_dir = "ghp_" + "B" * 36
        path = f"/nonexistent/{token_dir}/diff.patch"
        code, _, err = _run_cli(["--mock", "--diff-file", path])
        text = str(code) + err
        self.assertIn("error reading diff file", text)
        self.assertIn("[REDACTED:github_token]", text)
        self.assertEqual(text.count(token_dir), 1)


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


class FailOnVocabularyCliTests(unittest.TestCase):
    """A misspelled `--fail-on` severity exits 2 instead of gating nothing (#718)."""

    def test_typo_exits_2_naming_the_value(self):
        code, out, err = _run_cli(
            ["--mock", "--ci", "--fail-on", "crticial", "--diff-file", "-"], stdin=SAMPLE_DIFF
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("--fail-on", err)
        self.assertIn("crticial", err)
        self.assertIn("critical, major, minor, nit, info, blocker", err)

    def test_a_typo_beside_a_valid_severity_is_refused(self):
        code, _, err = _run_cli(
            ["--mock", "--ci", "--fail-on", "critical,majr", "--diff-file", "-"], stdin=SAMPLE_DIFF
        )
        self.assertEqual(code, 2)
        self.assertIn("majr", err)

    def test_the_guard_runs_without_ci_too(self):
        # `--fail-on` is inert without `--ci`, so a typo would otherwise surface
        # only on the run it was supposed to gate.
        code, _, err = _run_cli(
            ["--mock", "--fail-on", "majr", "--diff-file", "-"], stdin=SAMPLE_DIFF
        )
        self.assertEqual(code, 2)
        self.assertIn("majr", err)

    def test_the_documented_alias_and_mixed_case_are_accepted(self):
        code, out, _ = _run_cli(
            ["--mock", "--ci", "--fail-on", " Blocker , MINOR ", "--diff-file", "-"],
            stdin=SAMPLE_DIFF,
        )
        self.assertNotEqual(code, 2)
        self.assertIn("## CI gate", out)


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


# --------------------------------------------------------------------------- #
# The rest of the reference: subcommands, `jury.toml` keys, environment,      #
# and the JSON report's top level (docs audit 2026-09-29)                     #
# --------------------------------------------------------------------------- #

SRC = REPO_ROOT / "src" / "ai_jury"
REPORT_FORMAT = REPO_ROOT / "docs" / "report-format.md"

#: A backticked table key in a first cell: `fail_on`, `min_vendors`.
_TABLE_KEY = re.compile(r"^\| `([a-z_]+)` \|", re.MULTILINE)

#: An environment variable named as a string literal in the package source.
_ENV_LITERAL = re.compile(r"[\"']((?:JURY|XDG)_[A-Z_]+)[\"']")


def _subsections(block: str) -> dict[str, str]:
    """`### heading` → its body, for every level-3 heading in *block*."""
    parts = re.split(r"^### ", block, flags=re.MULTILINE)[1:]
    return {part.split("\n", 1)[0]: part.split("\n", 1)[1] for part in parts}


def _first_cells(body: str) -> str:
    return "\n".join(line.split("|")[1] for line in body.splitlines() if line.startswith("| `"))


class _ParserCapturedError(Exception):
    """Raised instead of parsing, carrying the parser a subcommand built."""


def subcommand_parser(argv: list[str]):
    """The argparse parser `jury <argv>` builds, captured before it parses anything.

    The subcommands are argv-intercepts in `cli.main` that build their parser
    inline, so the parser is read off the first `parse_args` call instead of
    being rebuilt here — a copy would agree with the docs and not with `jury`.
    """

    def capture(parser, *_args, **_kwargs):
        raise _ParserCapturedError(parser)

    with mock.patch.object(
        argparse.ArgumentParser, "parse_args", autospec=True, side_effect=capture
    ):
        try:
            main(argv)
        except _ParserCapturedError as got:
            return got.args[0]
    raise AssertionError(f"jury {' '.join(argv)} never parsed its arguments")


def _public_long_options(parser) -> set[str]:
    return {
        opt
        for action in parser._actions
        if action.help != argparse.SUPPRESS
        for opt in action.option_strings
        if opt.startswith("--") and opt != "--help"
    }


class SubcommandFlagTablesMatchTheirParsers(unittest.TestCase):
    """Each `### `jury <name>`` table in the parameter reference lists that parser's flags.

    The page promises "every parameter"; `jury apply` and `jury replay` had no
    table at all and `jury comment` was one row naming two of its five flags.
    """

    SUBCOMMANDS = ("init", "run-agent", "apply", "replay", "comment")

    def _documented(self) -> dict[str, set[str]]:
        text = PARAMETERS.read_text(encoding="utf-8")
        block = _section(text, "\n## Subcommands\n", "\n## `jury.toml` reference\n")
        tables = {}
        for heading, body in _subsections(block).items():
            match = re.match(r"`jury ([a-z-]+)`", heading)
            if match:
                tables[match.group(1)] = set(_LONG_FLAG.findall(_first_cells(body)))
        return tables

    def test_every_subcommand_has_a_table(self):
        self.assertEqual(sorted(set(self.SUBCOMMANDS) - set(self._documented())), [])

    def test_each_table_names_exactly_its_parsers_flags(self):
        documented = self._documented()
        for name in self.SUBCOMMANDS:
            with self.subTest(subcommand=name):
                actual = _public_long_options(subcommand_parser([name]))
                self.assertTrue(actual, name)
                self.assertEqual(sorted(actual - documented.get(name, set())), [])
                self.assertEqual(sorted(documented.get(name, set()) - actual), [])


class JuryTomlTablesMatchTheSchema(unittest.TestCase):
    """The `jury.toml` reference lists exactly the keys the validator knows.

    `[jury.ci]` listed two of its four keys: `min_vendors` and `min_reviews`, the
    two guards that fail a run, had no row. The expected sets are the ones
    `validate_config` itself checks unknown keys against.
    """

    def _tables(self) -> dict[str, set[str]]:
        text = PARAMETERS.read_text(encoding="utf-8")
        block = _section(text, "\n## `jury.toml` reference\n", "\n## Enumerations\n")
        tables = {}
        for heading, body in _subsections(block).items():
            name = re.match(r"`(\[\[?[a-z.]+\]\]?)`", heading).group(1)
            tables[name] = set(_TABLE_KEY.findall(body))
        return tables

    def test_every_table_lists_exactly_the_known_keys(self):
        from ai_jury import config

        expected = {
            "[jury]": set(config.KNOWN_JURY_KEYS) - set(config.KNOWN_NESTED_JURY_KEYS),
            "[jury.ci]": set(config.KNOWN_CI_KEYS),
            "[jury.context]": set(config.KNOWN_CONTEXT_KEYS),
            "[jury.diff]": set(config.KNOWN_DIFF_KEYS),
            "[jury.output]": set(config.KNOWN_OUTPUT_KEYS),
            "[[agent]]": set(config.KNOWN_AGENT_KEYS),
        }
        tables = self._tables()
        self.assertEqual(sorted(tables), sorted(expected))
        for name, keys in expected.items():
            with self.subTest(table=name):
                self.assertEqual(sorted(keys - tables[name]), [], "keys missing from the table")
                self.assertEqual(sorted(tables[name] - keys), [], "rows the schema has no key for")


class EnvironmentTableListsWhatTheCodeReads(unittest.TestCase):
    """Every environment variable the package reads has a row in the reference.

    The table lacked `JURY_TRUST_PROJECT_CONFIG` — the one a non-interactive run
    needs to get past the config-trust gate — `JURY_REQUIRE_ABSOLUTE_COMMAND` and
    both `XDG_*` bases.
    """

    def _documented(self) -> set[str]:
        text = PARAMETERS.read_text(encoding="utf-8")
        block = text[text.index("\n## Environment variables\n") :]
        return set(re.findall(r"^\| `([A-Z_]+)` \|", block, flags=re.MULTILINE))

    def test_every_jury_and_xdg_variable_in_the_source_is_documented(self):
        read = set()
        for path in SRC.rglob("*.py"):
            read |= set(_ENV_LITERAL.findall(path.read_text(encoding="utf-8")))
        self.assertIn("JURY_TRUST_PROJECT_CONFIG", read)
        self.assertEqual(sorted(read - self._documented()), [])

    def test_the_api_key_env_row_names_every_adapters_default(self):
        # `api_key_env` unset falls back to the ADAPTER's own variable; the row
        # said `OPENAI_API_KEY` for every seat, and only for `openai-compatible`.
        from ai_jury import adapters

        defaults = {
            cls._ENV_VAR_NAME
            for cls in vars(adapters).values()
            if isinstance(cls, type) and hasattr(cls, "_ENV_VAR_NAME")
        }
        self.assertIn("GEMINI_API_KEY", defaults)
        text = PARAMETERS.read_text(encoding="utf-8")
        row = re.search(r"^\| `api_key_env` \|.*$", text, flags=re.MULTILINE).group(0)
        self.assertEqual(sorted(name for name in defaults if f"`{name}`" not in row), [])
        self.assertEqual(sorted(defaults - self._documented()), [])


class JsonReportTopLevelIsDocumented(unittest.TestCase):
    """`report-format.md` lists the JSON report's top-level keys, in order."""

    def test_the_documented_keys_are_the_documents(self):
        code, out, _err = _run_cli(["--mock", "--format", "json", "-q"])
        self.assertEqual(code, 0)
        text = REPORT_FORMAT.read_text(encoding="utf-8")
        self.assertIn("\n## The JSON report and SARIF\n", text)
        start = text.index("\n## The JSON report and SARIF\n") + 1
        block = text[start : text.index("\n## ", start)]
        self.assertEqual(_TABLE_KEY.findall(block), list(json.loads(out)))


if __name__ == "__main__":
    unittest.main()
