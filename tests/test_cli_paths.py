"""Deep CLI coverage: --pr / post / inline / label / cache / incremental /
auto-depth / progress / error paths and the full flag-override block. All
offline — `gh` and network functions are mocked; agents run via --mock."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import cli, config  # noqa: E402

DIFF = """diff --git a/app.py b/app.py
index 0000000..1111111 100644
--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
 def f(x):
-    return x
+    return x + 1
+    # added
"""


def run(args, stdin=None):
    out, err = io.StringIO(), io.StringIO()
    prev = sys.stdin
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    code = None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(args)
    except SystemExit as exc:
        code = exc.code
    finally:
        sys.stdin = prev
    return code, out.getvalue(), err.getvalue()


@contextlib.contextmanager
def gh_mocked(diff=DIFF, head="abc123def456789", comments=None):
    comments = comments or []
    with (
        mock.patch("ai_jury.cli.pr_diff", return_value=diff),
        mock.patch("ai_jury.cli.pr_context", return_value="title\n\nbody"),
        mock.patch("ai_jury.cli.post_pr_comment") as ppc,
        mock.patch("ai_jury.cli.post_inline_comments") as pic,
        mock.patch("ai_jury.cli.apply_labels") as al,
        mock.patch("ai_jury.github.pr_head_sha", return_value=head),
        mock.patch("ai_jury.github.pr_comment_bodies", return_value=comments),
        mock.patch("ai_jury.github.compare_diff", return_value=diff),
    ):
        yield {"post": ppc, "inline": pic, "labels": al}


class FlagOverrideBlock(unittest.TestCase):
    def test_all_overrides_apply(self):
        d = Path(tempfile.mkdtemp()) / "x.diff"
        d.write_text(DIFF)
        code, out, _ = run(
            [
                "--mock",
                "--diff-file",
                str(d),
                "-q",
                "--rounds",
                "2",
                "--max-rounds",
                "3",
                "--early-stop",
                "--total-timeout",
                "120",
                "--phase-timeout",
                "60",
                "--retries",
                "1",
                "--seed",
                "7",
                "--chair",
                "codex",
                "--verify",
                "--context-mode",
                "expanded",
                "--redact",
                "--max-diff-bytes",
                "500000",
                "--chunk",
                "--exclude",
                "*.lock",
                "--include",
                "*.py",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("AI Jury", out)

    def test_auto_depth(self):
        d = Path(tempfile.mkdtemp()) / "x.diff"
        d.write_text(DIFF)
        code, out, _ = run(["--mock", "--diff-file", str(d), "--auto", "-q"])
        self.assertEqual(code, 0)


class PrPostPaths(unittest.TestCase):
    def test_post_single(self):
        with gh_mocked() as m:
            code, _, _ = run(["--mock", "--pr", "123", "--post", "-q"])
        self.assertEqual(code, 0)
        m["post"].assert_called()

    def test_post_phased(self):
        with gh_mocked() as m:
            code, _, _ = run(["--mock", "--pr", "123", "--post", "--post-mode", "phased", "-q"])
        self.assertEqual(code, 0)
        self.assertGreaterEqual(m["post"].call_count, 1)

    def test_post_inline_dry_run(self):
        with gh_mocked() as m:
            code, _, _ = run(["--mock", "--pr", "123", "--post-inline", "--dry-run", "-q"])
        self.assertEqual(code, 0)
        m["inline"].assert_called()

    def test_label(self):
        with gh_mocked() as m:
            code, _, _ = run(["--mock", "--pr", "123", "--label", "-q"])
        self.assertEqual(code, 0)
        m["labels"].assert_called()

    def test_post_progress(self):
        with gh_mocked(), mock.patch("ai_jury.github.ProgressReporter") as progress_reporter:
            code, _, _ = run(["--mock", "--pr", "123", "--post-progress", "-q"])
        self.assertEqual(code, 0)
        progress_reporter.assert_called()

    def test_incremental(self):
        marker = "<!-- arc-reviewed-sha: deadbeefcafe -->"
        with gh_mocked(comments=[marker]):
            code, _, _ = run(["--mock", "--pr", "123", "--incremental", "-q"])
        self.assertEqual(code, 0)


class CachePath(unittest.TestCase):
    def test_cache_miss_then_hit(self):
        cdir = tempfile.mkdtemp()
        d = Path(tempfile.mkdtemp()) / "x.diff"
        d.write_text(DIFF)
        # No -q: the "cache miss/hit" lines are emitted by log() to stderr.
        a = ["--mock", "--diff-file", str(d), "--cache", "--cache-dir", cdir]
        code1, _, err1 = run(a)
        code2, _, err2 = run(a)
        self.assertEqual(code1, 0)
        self.assertEqual(code2, 0)
        self.assertIn("cache miss", err1)
        self.assertIn("cache hit", err2)


class ErrorPaths(unittest.TestCase):
    def test_review_runtime_error(self):
        d = Path(tempfile.mkdtemp()) / "x.diff"
        d.write_text(DIFF)
        with mock.patch("ai_jury.cli.review_diff", side_effect=RuntimeError("no usable agents")):
            code, _, err = run(["--mock", "--diff-file", str(d), "-q"])
        self.assertEqual(code, 2)
        self.assertIn("no usable agents", err)

    def test_review_keyboard_interrupt(self):
        d = Path(tempfile.mkdtemp()) / "x.diff"
        d.write_text(DIFF)
        with mock.patch("ai_jury.cli.review_diff", side_effect=KeyboardInterrupt()):
            code, _, err = run(["--mock", "--diff-file", str(d), "-q"])
        self.assertEqual(code, 130)

    def test_empty_diff_errors(self):
        d = Path(tempfile.mkdtemp()) / "empty.diff"
        d.write_text("   \n")
        code, _, _ = run(["--mock", "--diff-file", str(d), "-q"])
        self.assertNotEqual(code, 0)

    def test_config_load_invalid(self):
        cfg = Path(tempfile.mkdtemp()) / "bad.toml"
        cfg.write_text(
            '[jury]\nrounds = 0\n\n[[agent]]\nname = "a"\nvendor = "anthropic"\ncommand = "x"\n'
        )
        d = Path(tempfile.mkdtemp()) / "x.diff"
        d.write_text(DIFF)
        code, _, err = run(["--mock", "--diff-file", str(d), "--config", str(cfg)])
        self.assertEqual(code, 2)
        self.assertIn("Config invalid", err)


class CliOverrideBounds(unittest.TestCase):
    """Issue #748: a bound holds on the flag as well as on the config key.

    The overrides are assigned onto the config *after* `validate_config` has
    run, so nothing range-checked them: `--rounds 0` was accepted, ran a full
    Round 1 and exited 0 while `rounds = 0` in `jury.toml` was a hard error.
    """

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.diff = self.d / "x.diff"
        self.diff.write_text(DIFF)

    def _run(self, *flags):
        return run(["--mock", "--diff-file", str(self.diff), "-q", *flags])

    def _namespace(self, dest=None, value=None):
        """An args namespace with every bounded flag unset but one."""
        ns = cli.build_parser().parse_args(["--mock", "--diff-file", str(self.diff)])
        if dest is not None:
            setattr(ns, dest, value)
        return ns

    def test_rounds_zero_is_refused_instead_of_running_one_round(self):
        code, out, err = self._run("--rounds", "0")
        self.assertEqual(code, 2)
        self.assertIn("--rounds must be an integer >= 1 (got 0).", err)
        self.assertNotIn("AI Jury", out)

    def test_the_smallest_valid_rounds_still_runs(self):
        code, out, _ = self._run("--rounds", "1")
        self.assertEqual(code, 0)
        self.assertIn("AI Jury", out)

    def test_the_flag_and_the_config_key_give_one_answer(self):
        # The whole point of #748: one value, one field, one rule — the two
        # surfaces differ only in the name they blame.
        cfg = self.d / "zero.toml"
        cfg.write_text(
            '[jury]\nrounds = 0\n\n[[agent]]\nname = "a"\nvendor = "anthropic"\ncommand = "x"\n'
        )
        toml_code, _, toml_err = run(["--config-validate", "--config", str(cfg)])
        flag_code, _, flag_err = self._run("--rounds", "0")
        rule = "must be an integer >= 1 (got 0)."
        self.assertEqual((toml_code, flag_code), (2, 2))
        self.assertIn(f"jury.rounds {rule}", toml_err)
        self.assertIn(f"--rounds {rule}", flag_err)

    def test_every_bounded_flag_is_refused_below_its_minimum(self):
        for flag, _dest, setting in cli._BOUNDED_FLAGS:
            minimum = config._NUMERIC_BOUNDS[setting][0]
            with self.subTest(flag=flag):
                code, _, err = self._run(flag, str(minimum - 1))
                self.assertEqual(code, 2)
                self.assertIn(f"{flag} must be ", err)
                self.assertIn(f"(got {minimum - 1}).", err)

    def test_every_bounded_flag_accepts_its_minimum(self):
        # Asserted on the check rather than on a whole run: `--max-diff-bytes 1`
        # is a legal budget that no real diff fits in, and a `--total-timeout 1`
        # run would be measuring the machine.
        for flag, dest, setting in cli._BOUNDED_FLAGS:
            minimum = config._NUMERIC_BOUNDS[setting][0]
            with self.subTest(flag=flag):
                self.assertIsNone(cli._override_bound_error(self._namespace(dest, minimum)))

    def test_an_unset_flag_is_not_read_as_a_zero(self):
        self.assertIsNone(cli._override_bound_error(self._namespace()))

    def test_every_entry_names_a_real_flag_and_a_real_bound(self):
        defaults = self._namespace()
        for flag, dest, setting in cli._BOUNDED_FLAGS:
            with self.subTest(flag=flag):
                self.assertTrue(hasattr(defaults, dest))
                self.assertIn(setting, config._NUMERIC_BOUNDS)

    def test_a_negative_retries_is_refused_rather_than_silently_clamped(self):
        # It used to be `max(0, args.retries)` — a second rule for one value,
        # answering "0" where the config path answers "invalid".
        code, _, err = self._run("--retries", "-1")
        self.assertEqual(code, 2)
        self.assertIn("--retries must be an integer >= 0 (got -1).", err)

    def test_an_unbounded_flag_is_left_alone(self):
        # `--seed` is in no bounds table on either surface: a malformed
        # `[jury] seed` is read as "no seed" on purpose, so there is nothing for
        # the flag to disagree with.
        code, _, _ = self._run("--seed", "-5")
        self.assertEqual(code, 0)


class UnwritableOutputPath(unittest.TestCase):
    """Issue #749: a run already paid for is not lost to an unwritable `-o`."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.diff = self.d / "x.diff"
        self.diff.write_text(DIFF)

    def _run(self, target):
        return run(["--mock", "--diff-file", str(self.diff), "-q", "-o", str(target)])

    def test_a_missing_parent_directory_is_a_cli_error_not_a_traceback(self):
        target = self.d / "nope" / "report.md"
        code, _, err = self._run(target)
        self.assertEqual(code, 2)
        self.assertIn("could not write the report to", err)
        self.assertIn(str(target), err)
        self.assertNotIn("Traceback", err)

    def test_the_report_falls_back_to_stdout_so_the_run_is_not_lost(self):
        target = self.d / "nope" / "report.md"
        code, out, err = self._run(target)
        self.assertEqual(code, 2)
        self.assertIn("AI Jury", out)
        self.assertIn("printed on stdout", err)

    def test_a_target_that_is_a_directory_is_reported_the_same_way(self):
        # A second OSError shape, and one every platform raises: opening a
        # directory for writing fails on POSIX and on Windows alike.
        target = self.d / "adir"
        target.mkdir()
        code, out, err = self._run(target)
        self.assertEqual(code, 2)
        self.assertIn("could not write the report to", err)
        self.assertIn("AI Jury", out)

    def test_a_secret_in_the_path_is_redacted_on_both_halves_of_the_message(self):
        """The path is printed twice — interpolated, and inside the OSError text.

        `redact` was applied to the exception only, so one line carried the same
        path masked and in the clear. A path is operator-supplied text like any
        other, and CI logs keep it.
        """
        token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
        target = self.d / token / "report.md"

        code, _, err = self._run(target)

        self.assertEqual(code, 2)
        self.assertNotIn(token, err)
        self.assertEqual(err.count("[REDACTED:github_token]"), 2)

    def test_a_secret_in_a_written_path_is_redacted_too(self):
        """The success lines print the same operator text and got the same fix."""
        token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
        target = self.d / f"{token}.md"

        code, _, err = self._run(target)

        self.assertEqual(code, 0)
        self.assertNotIn(token, err)
        self.assertTrue(target.exists())

    def test_a_writable_path_still_gets_the_report(self):
        target = self.d / "report.md"
        code, out, _ = self._run(target)
        self.assertEqual(code, 0)
        self.assertIn("AI Jury", target.read_text(encoding="utf-8"))
        self.assertNotIn("AI Jury", out)


class DoctorAndCommentPaths(unittest.TestCase):
    def test_doctor_write(self):
        outp = Path(tempfile.mkdtemp()) / "diag.json"
        code, out, _ = run(["--doctor", "--write", str(outp)])
        self.assertIn("ready to run", out)
        self.assertTrue(outp.exists())

    def test_config_validate_warnings(self):
        cfg = Path(tempfile.mkdtemp()) / "warn.toml"
        # unknown vendor → a soft warning (valid, but warned).
        cfg.write_text(
            '[jury]\nrounds = 1\nchair = "a"\n\n[[agent]]\nname = "a"\nvendor = "acme"\ncommand = "x"\n'
        )
        code, out, _ = run(["--config-validate", "--config", str(cfg)])
        self.assertEqual(code, 0)
        self.assertIn("warning", out.lower())

    def test_comment_print_args(self):
        with gh_mocked():
            code, out, _ = run(["comment", "--text", "/jury review", "--pr", "1", "--print-args"])
        self.assertEqual(code, 0)


class DiffIngestCapTests(unittest.TestCase):
    """Security audit 2026-06-13: the raw diff read must be bounded so a hostile
    huge input cannot OOM the process before diff.max_bytes engages."""

    def test_within_cap_returned(self):
        payload = "x" * 1000
        self.assertEqual(cli._read_capped(io.BytesIO(payload.encode()), "stdin"), payload)

    def test_over_cap_rejected_bytes(self):
        big = io.BytesIO(b"y" * (cli._MAX_DIFF_INGEST_BYTES + 10))
        with self.assertRaises(SystemExit) as ctx:
            cli._read_capped(big, "stdin")
        self.assertIn("ingest limit", str(ctx.exception))

    def test_cap_is_on_bytes_not_chars(self):
        # A multi-byte char payload just over the byte cap must be rejected even
        # though its character count is well under it (red-team 2026-06-13).
        n_chars = cli._MAX_DIFF_INGEST_BYTES // 3 + 10  # "界" is 3 bytes in UTF-8
        big = io.BytesIO(("界" * n_chars).encode("utf-8"))
        with self.assertRaises(SystemExit):
            cli._read_capped(big, "stdin")


if __name__ == "__main__":
    unittest.main()
