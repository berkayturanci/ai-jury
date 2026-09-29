"""Regression tests for the CLI/config bugs the docs audit of 2026-09-29 found.

Each class pins one finding: the documented contract, measured on the code path
the operator reaches. Offline, deterministic, no credentials; any cache or
config directory the tests touch is a temp directory.
"""

from __future__ import annotations

import contextlib
import io
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import adapters, cli  # noqa: E402
from ai_jury.adapters import Adapter, AgentResult, register_adapter  # noqa: E402
from ai_jury.config import (  # noqa: E402
    ConfigError,
    _from_dict,
    _non_negative_int,
    load_config,
    validate_config,
)
from ai_jury.report import render_footer  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

DIFF = """diff --git a/app.py b/app.py
index 0000000..1111111 100644
--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
 def f(x):
-    return x
+    return x + 1
+    # trailing
"""

_TWO_SEATS = (
    '[[agent]]\nname = "claude"\nvendor = "anthropic"\ncommand = "claude"\n\n'
    '[[agent]]\nname = "codex"\nvendor = "openai"\ncommand = "codex"\n'
)


def run(args):
    out, err = io.StringIO(), io.StringIO()
    code = None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(args)
    except SystemExit as exc:
        code = exc.code
    return code, out.getvalue(), err.getvalue()


def _agents(*names):
    vendors = ("anthropic", "openai", "google")
    return [
        {"name": n, "vendor": vendors[i % len(vendors)], "command": n} for i, n in enumerate(names)
    ]


def strict_warnings(test, data):
    """``validate_config(data, strict=True)``, a refusal reported as a test FAILURE."""
    try:
        return validate_config(data, strict=True)
    except ConfigError as exc:
        raise test.failureException(f"config refused: {exc}") from exc


class _TempDirCase(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.diff = self.d / "x.diff"
        self.diff.write_text(DIFF, encoding="utf-8")
        self.config = self.d / "jury.toml"
        self.config.write_text("[jury]\nrounds = 1\n\n" + _TWO_SEATS, encoding="utf-8")

    def mock_run(self, *extra):
        return run(
            ["--mock", "-q", "--config", str(self.config), "--diff-file", str(self.diff), *extra]
        )


class CacheClearHelpDoesNotClear(_TempDirCase):
    """X1: `jury cache clear --help` deleted the cache it was asked to describe."""

    def setUp(self):
        super().setUp()
        self.cache = self.d / "cache"
        self.cache.mkdir()
        self.entry = self.cache / f"{'a' * 64}.json"
        self.entry.write_text("{}", encoding="utf-8")

    def test_help_prints_its_own_usage_and_leaves_the_cache(self):
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                code, out, _ = run(["cache", "clear", flag, "--cache-dir", str(self.cache)])
                self.assertEqual(code, 0)
                self.assertIn("usage: jury cache clear", out)
                self.assertNotIn("Cleared", out)
                self.assertTrue(self.entry.exists(), "cache entry was deleted by --help")

    def test_an_unexpected_argument_is_refused_before_clearing(self):
        code, out, err = run(["cache", "clear", "--cache-dir", str(self.cache), "--bogus"])
        self.assertEqual(code, 2)
        self.assertIn("unrecognized arguments: --bogus", err)
        self.assertNotIn("Cleared", out)
        self.assertTrue(self.entry.exists())

    def test_clear_still_clears(self):
        code, out, _ = run(["cache", "clear", "--cache-dir", str(self.cache)])
        self.assertEqual(code, 0)
        self.assertIn("Cleared 1 cache entry", out)
        self.assertFalse(self.entry.exists())


class ExamplesAndGuideHaveTheirOwnHelp(unittest.TestCase):
    """X2: the epilogue promises every subcommand its own --help."""

    def test_help_is_the_subcommands_not_the_main_parsers(self):
        for sub in ("examples", "guide"):
            with self.subTest(sub=sub):
                code, out, _ = run([sub, "--help"])
                self.assertEqual(code, 0)
                self.assertIn(f"usage: jury {sub}", out)
                self.assertNotIn("--pr PR", out)

    def test_trailing_junk_is_still_an_error(self):
        code, out, err = run(["guide", "foo"])
        self.assertEqual(code, 2)
        self.assertIn("usage: jury guide", err)
        self.assertNotIn("walkthrough", out)


class TheFailClosedGuardsRefuseBadBounds(_TempDirCase):
    """X3/P2: a negative or non-integer guard silently turned the guard off."""

    def test_negative_flags_are_refused_before_the_run(self):
        for flag in ("--min-vendors", "--min-reviews"):
            with self.subTest(flag=flag):
                code, out, err = self.mock_run(f"{flag}=-5")
                self.assertEqual(code, 2)
                self.assertIn(f"error: {flag} must be an integer >= 0", err)
                self.assertNotIn("AI Jury", out)

    def test_doctor_refuses_a_negative_min_vendors(self):
        code, _, err = run(["--doctor", "--config", str(self.config), "--min-vendors", "-1"])
        self.assertEqual(code, 2)
        self.assertIn("error: --min-vendors must be an integer >= 0", err)

    def test_doctor_refuses_a_negative_min_reviews_too(self):
        # codex seat finding on #926: the doctor path checked --min-vendors only.
        code, _, err = run(["--doctor", "--config", str(self.config), "--min-reviews", "-1"])
        self.assertEqual(code, 2)
        self.assertIn("error: --min-reviews must be an integer >= 0", err)

    def test_zero_and_the_opt_out_flag_are_still_accepted(self):
        for extra in (["--min-vendors", "0"], ["--no-min-vendors"], ["--min-reviews", "0"]):
            with self.subTest(extra=extra):
                code, _, _ = self.mock_run(*extra)
                self.assertEqual(code, 0)

    def test_bad_config_values_are_hard_errors_strict_or_not(self):
        for key, value in (
            ("min_vendors", -1),
            ("min_vendors", "two"),
            ("min_reviews", "lots"),
            ("min_reviews", -3),
            ("min_reviews", True),
        ):
            data = {"jury": {"ci": {key: value}}, "agent": _agents("claude", "codex")}
            for strict in (False, True):
                with self.subTest(key=key, value=value, strict=strict):
                    with self.assertRaises(ConfigError) as ctx:
                        validate_config(data, strict=strict)
                    self.assertIn(f"jury.ci.{key} must be an integer >= 0", str(ctx.exception))

    def test_plain_load_and_config_validate_refuse_them(self):
        self.config.write_text(
            '[jury.ci]\nmin_vendors = -1\nmin_reviews = "lots"\n\n' + _TWO_SEATS,
            encoding="utf-8",
        )
        with self.assertRaises(ConfigError):
            load_config(self.config, validate=True)
        for extra in ([], ["--strict-config"]):
            with self.subTest(extra=extra):
                code, _, err = run(["--config-validate", "--config", str(self.config), *extra])
                self.assertEqual(code, 2)
                self.assertIn("jury.ci.min_vendors must be an integer >= 0", err)
                self.assertIn("jury.ci.min_reviews must be an integer >= 0", err)
        code, out, err = self.mock_run()
        self.assertEqual(code, 2)
        self.assertIn("jury.ci.min_vendors", err)

    def test_valid_values_pass(self):
        data = {
            "jury": {"ci": {"min_vendors": 0, "min_reviews": 2}},
            "agent": _agents("claude", "codex"),
        }
        self.assertEqual(validate_config(data, strict=True), [])

    def test_unvalidated_materialisation_never_reads_a_negative_as_off(self):
        self.assertEqual(_non_negative_int(-1, 2), 2)
        config = _from_dict({"jury": {"ci": {"min_vendors": -1}}, "agent": _agents("a", "b")})
        self.assertEqual(config.ci.min_vendors, 2)


class TheChairIsValidated(_TempDirCase):
    """P4/P5: an unknown --chair was accepted; the validator's default was not the run's."""

    def test_an_unknown_chair_flag_is_refused(self):
        code, out, err = self.mock_run("--chair", "bogus")
        self.assertEqual(code, 2)
        self.assertIn("error: --chair 'bogus' is not an enabled agent", err)
        self.assertIn("enabled: claude, codex", err)
        self.assertNotIn("AI Jury", out)

    def test_a_disabled_seat_cannot_chair(self):
        self.config.write_text(
            _TWO_SEATS + '\n[[agent]]\nname = "gem"\nvendor = "google"\ncommand = "gemini"\n'
            "enabled = false\n",
            encoding="utf-8",
        )
        code, _, err = self.mock_run("--chair", "gem")
        self.assertEqual(code, 2)
        self.assertIn("--chair 'gem' is not an enabled agent", err)

    def test_an_enabled_seat_and_rotate_are_accepted(self):
        for chair in ("codex", "rotate"):
            with self.subTest(chair=chair):
                code, _, _ = self.mock_run("--chair", chair)
                self.assertEqual(code, 0)

    def test_the_validator_and_the_run_agree_on_the_default(self):
        data = {"jury": {"rounds": 1}, "agent": _agents("codex", "gem")}
        # No `claude` seat and no `chair`: the run chairs codex, so strict passes.
        self.assertEqual(strict_warnings(self, data), [])
        self.assertEqual(_from_dict(data).chair, "codex")

    def test_the_default_skips_a_disabled_first_seat(self):
        agents = _agents("codex", "gem")
        agents[0]["enabled"] = False
        data = {"agent": agents}
        self.assertEqual(validate_config(data, strict=True), [])
        self.assertEqual(_from_dict(data).chair, "gem")

    def test_a_written_chair_that_is_not_enabled_still_warns(self):
        data = {"jury": {"chair": "claude"}, "agent": _agents("codex", "gem")}
        warnings = validate_config(data)
        self.assertTrue(any("jury.chair 'claude' is not an enabled agent" in w for w in warnings))


class PostModeNeedsASummary(_TempDirCase):
    """P3: `--post-mode` without `--post-summary`, or with `--issue`, was accepted."""

    def test_post_mode_without_a_summary_is_refused(self):
        for mode in ("phased", "single"):
            with self.subTest(mode=mode):
                # Raised like the other posting-flag usage errors beside it.
                code, out, _ = self.mock_run("--post-mode", mode)
                self.assertEqual(code, "error: --post-mode requires --post-summary (or --post)")
                self.assertNotIn("AI Jury", out)

    def test_post_mode_with_issue_is_refused(self):
        code, _, _ = run(
            ["--mock", "-q", "--config", str(self.config), "--issue", "1", "--post"]
            + ["--post-mode", "phased"]
        )
        self.assertEqual(
            code, "error: --post-mode is not supported with --issue (it is a PR/diff concept)"
        )

    def test_no_post_mode_is_still_fine(self):
        code, _, _ = self.mock_run()
        self.assertEqual(code, 0)


@unittest.skipIf(os.name == "nt", "the hook's entry is a bash one-liner")
@unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git")
class PreCommitHookForwardsArgs(unittest.TestCase):
    """CB3: the hook's `bash -c` entry dropped every `args:` the user configured."""

    def _entry(self):
        text = (REPO_ROOT / ".pre-commit-hooks.yaml").read_text(encoding="utf-8")
        entries = [
            line.split("entry:", 1)[1].strip()
            for line in text.splitlines()
            if line.strip().startswith("entry:")
        ]
        self.assertEqual(len(entries), 1, "expected one entry: line")
        return entries[0]

    def test_args_reach_jury_after_the_hooks_own_flags(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "bin").mkdir()
        stub = d / "bin" / "jury"
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n', encoding="utf-8")
        stub.chmod(0o755)
        (d / "repo").mkdir()
        subprocess.run(["git", "init", "-q", str(d / "repo")], check=True)
        env = {**os.environ, "PATH": f"{d / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"}
        # pre-commit runs `shlex.split(entry) + args` (pass_filenames is false).
        argv = [*shlex.split(self._entry()), "--rounds", "2", "--min-vendors", "0"]
        result = subprocess.run(
            argv, cwd=d / "repo", env=env, capture_output=True, text=True, check=True
        )
        self.assertEqual(
            result.stdout.split("\n")[:-1],
            ["--diff-file", "-", "--rounds", "1", "--rounds", "2", "--min-vendors", "0"],
        )


class _ProcessFreeAdapter(Adapter):
    SPAWNS_PROCESS = False

    def available(self) -> bool:
        return True

    def run(self, *_args, **_kwargs):  # pragma: no cover - validation never runs a seat
        raise AssertionError("not called")


class _SpawningAdapter(_ProcessFreeAdapter):
    SPAWNS_PROCESS = True


class RegisteredAdapterNeedsNoCommand(unittest.TestCase):
    """C3: a registered process-free adapter was refused for having no `command`."""

    def setUp(self):
        self.addCleanup(adapters._restore_registry_state, adapters._registry_state())

    def test_a_process_free_adapter_needs_no_command(self):
        register_adapter("audit-http-llm", _ProcessFreeAdapter)
        data = {"agent": [{"name": "internal", "vendor": "audit-http-llm", "model": "m"}]}
        self.assertEqual(strict_warnings(self, data), [])

    def test_a_spawning_adapter_still_needs_one(self):
        register_adapter("audit-cli-llm", _SpawningAdapter)
        data = {"agent": [{"name": "internal", "vendor": "audit-cli-llm"}]}
        with self.assertRaises(ConfigError) as ctx:
            validate_config(data)
        self.assertIn("missing a non-empty 'command'", str(ctx.exception))


class BooleansAndEnumsAreTyped(unittest.TestCase):
    """C8: `verify = "false"` and friends passed --strict-config and were coerced."""

    def _data(self, jury=None, **agent_extra):
        agents = _agents("claude", "codex")
        agents[0].update(agent_extra)
        return {"jury": jury or {}, "agent": agents}

    def assert_refused(self, data, needle):
        for strict in (False, True):
            with self.subTest(needle=needle, strict=strict):
                with self.assertRaises(ConfigError) as ctx:
                    validate_config(data, strict=strict)
                self.assertIn(needle, str(ctx.exception))

    def test_a_quoted_boolean_is_refused(self):
        for key in (
            "verify",
            "parallel",
            "anonymize_debate",
            "prefer_non_reviewer_chair",
            "demote_local_only",
            "early_stop",
            "auto_depth",
            "transcript",
            "hints",
        ):
            self.assert_refused(
                self._data({key: "false"}), f"jury.{key} must be true or false (got 'false')"
            )
        for table, key in (
            ("ci", "ignore_unverified"),
            ("context", "redact_secrets"),
            ("diff", "chunk"),
            ("diff", "exclude_generated"),
        ):
            self.assert_refused(
                self._data({table: {key: "false"}}),
                f"jury.{table}.{key} must be true or false (got 'false')",
            )
        self.assert_refused(
            self._data(enabled="false"), "agent 'claude' enabled must be true or false"
        )

    def test_an_unknown_mode_is_refused(self):
        self.assert_refused(
            self._data({"context": {"mode": "expand"}}),
            "jury.context.mode must be one of diff-only, expanded (got 'expand')",
        )
        self.assert_refused(
            self._data(prompt_mode="args"),
            "agent 'claude' prompt_mode must be one of stdin, arg (got 'args')",
        )

    def test_real_values_pass(self):
        data = self._data(
            {"verify": False, "context": {"mode": "Expanded", "redact_secrets": True}},
            enabled=True,
            prompt_mode="ARG",
        )
        self.assertEqual(validate_config(data, strict=True), [])


class FooterNamesOnlySeatsThatReviewed(unittest.TestCase):
    """Theme 5: an abstaining seat was named as having returned a review."""

    @staticmethod
    def _result(agent, output, ok=True):
        return AgentResult(agent=agent, vendor="v", ok=ok, output=output, duration_s=0.0)

    def test_abstentions_are_not_named(self):
        footer = render_footer(
            [
                self._result("claude", "Checked: app.py\nLooks fine.\n[]"),
                self._result("codex", "   "),
                self._result("gem", "I can't help with that."),
                self._result("local", "boom", ok=False),
            ]
        )
        self.assertIn("· claude —", footer)
        for seat in ("codex", "gem", "local"):
            self.assertNotIn(seat, footer)

    def test_a_panel_that_only_abstained_gets_no_seat_list(self):
        footer = render_footer([self._result("codex", "")])
        self.assertIn("[ai-jury](https://github.com/berkayturanci/ai-jury) —", footer)


if __name__ == "__main__":
    unittest.main()
