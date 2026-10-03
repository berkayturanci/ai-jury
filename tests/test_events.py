"""``--events-file``: the run's progress as metadata-only NDJSON for an observer in
another process. The record builders are pure; the CLI tests run the offline
``--mock`` panel and read the file back."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import cli, events  # noqa: E402
from ai_jury.adapters import AgentResult  # noqa: E402

DIFF = """diff --git a/app.py b/app.py
index 0000000..1111111 100644
--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
 def f(x):
-    return x
+    return x + 1  # changed
+    # trailing
"""


def _run(args: list[str]) -> int:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        return cli.main(args)


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class TheRecordBuilders(unittest.TestCase):
    def test_start_record_seats_the_panel_and_names_the_phases(self):
        rec = events.start_record(
            [("claude", "anthropic"), ("codex", "openai")],
            chair="claude",
            target="PR #7",
            mode="code",
            decision="vote",
            cached=False,
        )
        self.assertEqual(
            rec,
            {
                "event": "start",
                "panel": [
                    {"agent": "claude", "vendor": "anthropic"},
                    {"agent": "codex", "vendor": "openai"},
                ],
                "chair": "claude",
                "target": "PR #7",
                "mode": "code",
                "decision": "vote",
                "cached": False,
                "phases": ["review", "debate", "verify", "synthesis"],
                "pid": None,
                "cwd": None,
            },
        )

    def test_start_record_carries_the_pid_and_the_checkout(self):
        rec = events.start_record(
            [],
            chair=None,
            target="PR #7",
            mode="code",
            decision="vote",
            cached=False,
            pid=4242,
            cwd="/repo",
        )
        self.assertEqual((rec["pid"], rec["cwd"]), (4242, "/repo"))

    def test_step_record_carries_metadata_and_never_the_output(self):
        result = AgentResult(
            agent="codex",
            vendor="openai",
            ok=False,
            output="SECRET reviewer text",
            duration_s=12.345,
            error="boom",
            findings=[object(), object()],
            error_code="timeout",
        )
        rec = events.step_record("debate", result, 2)
        self.assertEqual(
            rec,
            {
                "event": "step",
                "phase": "debate",
                "round": 2,
                "agent": "codex",
                "vendor": "openai",
                "ok": False,
                "duration_s": 12.3,
                "findings": 2,
                "error_code": "timeout",
            },
        )
        self.assertNotIn("SECRET", json.dumps(rec))
        self.assertNotIn("boom", json.dumps(rec))

    def test_end_record(self):
        self.assertEqual(
            events.end_record("done", findings=3, verdict="LGTM"),
            {"event": "end", "status": "done", "findings": 3, "verdict": "LGTM"},
        )
        self.assertEqual(
            events.end_record("cancelled"),
            {"event": "end", "status": "cancelled", "findings": None, "verdict": None},
        )


class TheWriter(unittest.TestCase):
    def test_lines_are_numbered_stamped_and_truncate_the_previous_run(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "ev.ndjson"
            path.write_text("left over from the last run\n", encoding="utf-8")
            ticks = iter([100.0, 101.25])
            w = events.EventsWriter(str(path), clock=lambda: next(ticks))
            w.write({"event": "start"})
            w.write({"event": "end"})
            w.close()
            self.assertEqual(
                _read(path),
                [
                    {"schema": events.SCHEMA, "seq": 1, "ts": 100.0, "event": "start"},
                    {"schema": events.SCHEMA, "seq": 2, "ts": 101.25, "event": "end"},
                ],
            )


class TheWriterNeverTakesTheRunDown(unittest.TestCase):
    """A write that fails mid-run stops the side channel, not the review."""

    class _Breaks:
        """A file object whose writes fail from the ``after``-th on."""

        def __init__(self, after: int, *, close_fails: bool = False):
            self.after, self.calls, self.close_fails, self.closed = after, 0, close_fails, False

        def write(self, text):
            self.calls += 1
            if self.calls >= self.after:
                raise OSError(28, "No space left on device")
            return len(text)

        def flush(self):
            return None

        def close(self):
            self.closed = True
            if self.close_fails:
                raise OSError(5, "I/O error")

    def _writer(self, fh):
        errors: list[str] = []
        with unittest.mock.patch.object(Path, "open", return_value=fh):
            w = events.EventsWriter("/watched/ev.ndjson", clock=lambda: 1.0, on_error=errors.append)
        return w, errors

    def test_a_failed_write_reports_once_and_goes_quiet(self):
        fh = self._Breaks(after=2)
        w, errors = self._writer(fh)
        w.write({"event": "start"})
        self.assertFalse(w.failed)
        w.write({"event": "step"})
        w.write({"event": "step"})
        w.close()
        self.assertTrue(w.failed)
        self.assertEqual(fh.calls, 2)
        self.assertTrue(fh.closed)
        self.assertEqual(len(errors), 1)
        self.assertIn("No space left on device", errors[0])
        self.assertIn("the review continues", errors[0])

    def test_a_failed_close_is_reported_not_raised(self):
        fh = self._Breaks(after=99, close_fails=True)
        w, errors = self._writer(fh)
        w.write({"event": "end"})
        w.close()
        w.close()
        self.assertEqual(len(errors), 1)
        self.assertIn("I/O error", errors[0])

    def test_without_a_reporter_a_failure_is_still_swallowed(self):
        fh = self._Breaks(after=1)
        with unittest.mock.patch.object(Path, "open", return_value=fh):
            w = events.EventsWriter("/watched/ev.ndjson")
        w.write({"event": "start"})
        self.assertTrue(w.failed)


class TheCli(unittest.TestCase):
    def setUp(self):
        # An operator's own $JURY_EVENTS_DIR must not leak into these runs.
        env = unittest.mock.patch.dict("os.environ")
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(events.ENV_DIR, None)
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)
        self.diff = self.d / "change.diff"
        self.diff.write_text(DIFF, encoding="utf-8")
        self.ev = self.d / "ev.ndjson"

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_run_writes_start_each_step_and_an_end_matching_the_report(self):
        report = self.d / "report.json"
        code = _run(
            [
                "--mock",
                "--seed",
                "1",
                "--diff-file",
                str(self.diff),
                "-q",
                "--format",
                "json",
                "-o",
                str(report),
                "--events-file",
                str(self.ev),
            ]
        )
        self.assertEqual(code, 0)
        recs = _read(self.ev)
        self.assertEqual([r["seq"] for r in recs], list(range(1, len(recs) + 1)))
        self.assertEqual(recs[0]["event"], "start")
        self.assertEqual(recs[0]["target"], "local diff")
        self.assertFalse(recs[0]["cached"])
        self.assertTrue(recs[0]["panel"])
        steps = [r for r in recs if r["event"] == "step"]
        self.assertTrue(steps)
        self.assertEqual(steps[0]["phase"], "review")
        self.assertLessEqual({r["phase"] for r in steps}, set(events.PHASES))
        end = recs[-1]
        self.assertEqual(end["event"], "end")
        self.assertEqual(end["status"], "done")
        # The events file and the report never disagree on the outcome.
        out = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(end["findings"], len(out["findings"]))
        chair = [r for r in out["reviewers"] if r.get("role") == "chair"]
        self.assertEqual(end["verdict"], chair[0]["verdict"])

    def test_without_the_flag_no_file_is_written_and_nothing_is_streamed(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["--mock", "--diff-file", str(self.diff), "-q", "--format", "json"])
        self.assertEqual(code, 0)
        self.assertFalse(self.ev.exists())
        # stdout is the report alone: no --live step blocks leaked in.
        json.loads(out.getvalue())

    def test_the_flag_alone_does_not_stream_steps_to_stdout(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            cli.main(
                [
                    "--mock",
                    "--diff-file",
                    str(self.diff),
                    "-q",
                    "--format",
                    "json",
                    "--events-file",
                    str(self.ev),
                ]
            )
        json.loads(out.getvalue())
        self.assertTrue(self.ev.exists())

    def test_an_unwritable_path_is_a_clean_user_error(self):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = cli.main(
                [
                    "--mock",
                    "--diff-file",
                    str(self.diff),
                    "--events-file",
                    str(self.d / "no" / "ev"),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("error: cannot write --events-file", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

    def test_a_write_that_fails_mid_run_leaves_the_review_and_its_report_intact(self):
        report = self.d / "report.json"
        real_write = events.EventsWriter.write
        calls = {"n": 0}

        def flaky(writer, record):
            calls["n"] += 1
            if calls["n"] == 2:
                writer._stop(OSError(28, "No space left on device"))
                return
            real_write(writer, record)

        err = io.StringIO()
        with (
            unittest.mock.patch.object(events.EventsWriter, "write", flaky),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(err),
        ):
            code = cli.main(
                [
                    "--mock",
                    "--diff-file",
                    str(self.diff),
                    "-q",
                    "--format",
                    "json",
                    "-o",
                    str(report),
                    "--events-file",
                    str(self.ev),
                ]
            )
        self.assertEqual(code, 0)
        self.assertIsNotNone(json.loads(report.read_text(encoding="utf-8"))["findings"])
        self.assertEqual(err.getvalue().count("progress events stopped"), 1)
        self.assertEqual([r["event"] for r in _read(self.ev)], ["start"])

    def test_a_secret_in_an_unwritable_path_is_redacted(self):
        token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = cli.main(
                [
                    "--mock",
                    "--diff-file",
                    str(self.diff),
                    "--events-file",
                    str(self.d / token / "ev"),
                ]
            )
        self.assertEqual(code, 2)
        self.assertNotIn(token, err.getvalue())
        self.assertIn("[REDACTED:github_token]", err.getvalue())

    def test_a_secret_in_the_path_is_redacted_in_the_mid_run_warning(self):
        token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
        ev = self.d / f"{token}.ndjson"
        real_write = events.EventsWriter.write
        calls = {"n": 0}

        def flaky(writer, record):
            calls["n"] += 1
            if calls["n"] == 2:
                writer._stop(OSError(28, f"No space left on device: '{ev}'"))
                return
            real_write(writer, record)

        err = io.StringIO()
        with (
            unittest.mock.patch.object(events.EventsWriter, "write", flaky),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(err),
        ):
            code = cli.main(["--mock", "--diff-file", str(self.diff), "--events-file", str(ev)])
        self.assertEqual(code, 0)
        self.assertIn("progress events stopped", err.getvalue())
        self.assertNotIn(token, err.getvalue())
        self.assertIn("[REDACTED:github_token]", err.getvalue())

    def test_the_start_record_seats_only_enabled_agents(self):
        cfg = self.d / "jury.toml"
        cfg.write_text(
            '[[agent]]\nname = "claude"\nvendor = "anthropic"\ncommand = "claude"\n'
            '[[agent]]\nname = "codex"\nvendor = "openai"\ncommand = "codex"\nenabled = false\n',
            encoding="utf-8",
        )
        code = _run(
            [
                "--mock",
                "--config",
                str(cfg),
                "--diff-file",
                str(self.diff),
                "-q",
                "--events-file",
                str(self.ev),
            ]
        )
        self.assertEqual(code, 0)
        seated = [p["agent"] for p in _read(self.ev)[0]["panel"]]
        self.assertIn("claude", seated)
        self.assertNotIn("codex", seated)

    def test_an_interrupted_run_ends_cancelled(self):
        with unittest.mock.patch("ai_jury.cli.review_diff", side_effect=KeyboardInterrupt()):
            code = _run(["--mock", "--diff-file", str(self.diff), "--events-file", str(self.ev)])
        self.assertEqual(code, 130)
        recs = _read(self.ev)
        self.assertEqual([r["event"] for r in recs], ["start", "end"])
        self.assertEqual(recs[-1]["status"], "cancelled")

    def test_a_failed_run_ends_error(self):
        with unittest.mock.patch(
            "ai_jury.cli.review_diff", side_effect=RuntimeError("no usable agents")
        ):
            code = _run(["--mock", "--diff-file", str(self.diff), "--events-file", str(self.ev)])
        self.assertEqual(code, 2)
        self.assertEqual(_read(self.ev)[-1]["status"], "error")


class TheEventsDirectory(unittest.TestCase):
    def test_unset_empty_and_off_mean_no_directory(self):
        for value in (None, "", "  ", "off", "OFF", "0", "false", "no"):
            self.assertIsNone(events.events_dir(value), value)

    def test_a_path_is_taken_with_the_home_expanded(self):
        self.assertEqual(events.events_dir(" /tmp/ev "), Path("/tmp/ev"))
        self.assertEqual(events.events_dir("~/ev"), Path.home() / "ev")

    def test_run_files_are_named_by_utc_start_and_pid(self):
        self.assertEqual(events.run_file_name(0, 7), "19700101T000000Z-7.ndjson")

    def test_only_run_files_past_the_newest_keep_are_stale(self):
        runs = [f"2026100{d}T000000Z-1.ndjson" for d in range(1, 6)]
        names = [*reversed(runs), "notes.txt", "ev.ndjson", "20261001T000000Z-x.ndjson"]
        self.assertEqual(events.stale_run_files(names, keep=2), runs[:3])
        self.assertEqual(events.stale_run_files(names, keep=10), [])
        self.assertEqual(events.stale_run_files(names, keep=0), runs)

    def test_the_writer_makes_the_directory_prunes_and_writes_its_own_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "a" / "b"
            root.mkdir(parents=True)
            old = [root / f"2026100{i}T000000Z-1.ndjson" for i in range(1, 4)]
            for f in old:
                f.write_text("{}\n", encoding="utf-8")
            (root / "keep-me.txt").write_text("x", encoding="utf-8")
            with unittest.mock.patch.object(events, "KEEP", 2):
                w = events.open_dir_writer(root, clock=lambda: 0.0, pid=9)
            self.assertIsNotNone(w)
            w.write({"event": "start"})
            w.close()
            left = sorted(p.name for p in root.iterdir())
            # KEEP runs with this one: the newest old run stays, the foreign file too.
            self.assertEqual(left, ["19700101T000000Z-9.ndjson", old[2].name, "keep-me.txt"])

    def test_a_directory_that_cannot_be_made_warns_once_and_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            blocker = Path(d) / "file"
            blocker.write_text("x", encoding="utf-8")
            msgs: list[str] = []
            self.assertIsNone(events.open_dir_writer(blocker / "ev", on_error=msgs.append))
            self.assertEqual(len(msgs), 1)
            self.assertIn("$JURY_EVENTS_DIR", msgs[0])
            self.assertIn("no progress events", msgs[0])


class TheCliWithAnEventsDirectory(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = Path(self._tmp.name)
        self.diff = self.d / "change.diff"
        self.diff.write_text(DIFF, encoding="utf-8")
        self.dir = self.d / "events"

    def _run_with(self, value: str, *extra: str) -> tuple[int, str]:
        err = io.StringIO()
        with (
            unittest.mock.patch.dict("os.environ", {events.ENV_DIR: value}),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(err),
        ):
            code = cli.main(["--mock", "--diff-file", str(self.diff), "-q", *extra])
        return code, err.getvalue()

    def test_a_run_writes_its_own_file_with_its_pid_and_checkout(self):
        code, _ = self._run_with(str(self.dir))
        self.assertEqual(code, 0)
        files = list(self.dir.iterdir())
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].name.endswith(f"-{os.getpid()}.ndjson"))
        recs = _read(files[0])
        self.assertEqual(recs[0]["event"], "start")
        self.assertEqual(recs[0]["pid"], os.getpid())
        self.assertEqual(recs[0]["cwd"], str(Path.cwd()))
        self.assertEqual((recs[-1]["event"], recs[-1]["status"]), ("end", "done"))

    def test_the_flag_wins_over_the_directory(self):
        ev = self.d / "ev.ndjson"
        code, _ = self._run_with(str(self.dir), "--events-file", str(ev))
        self.assertEqual(code, 0)
        self.assertTrue(ev.exists())
        self.assertFalse(self.dir.exists())

    def test_off_writes_nothing(self):
        code, _ = self._run_with("off")
        self.assertEqual(code, 0)
        self.assertFalse(self.dir.exists())

    def test_an_unwritable_directory_warns_and_the_review_still_runs(self):
        blocker = self.d / "file"
        blocker.write_text("x", encoding="utf-8")
        code, err = self._run_with(str(blocker / "ev"))
        self.assertEqual(code, 0)
        self.assertIn("warning: $JURY_EVENTS_DIR", err)
        self.assertNotIn("Traceback", err)


class RunTarget(unittest.TestCase):
    def test_each_source_is_named(self):
        def ns(**kw):
            base = {"pr": None, "issue": None, "commit": None, "commits": None}
            base.update(kw)
            return unittest.mock.Mock(**base)

        self.assertEqual(cli._run_target(ns(pr=5)), "PR #5")
        self.assertEqual(cli._run_target(ns(issue=9)), "issue #9")
        self.assertEqual(cli._run_target(ns(commit="abc")), "commit abc")
        self.assertEqual(cli._run_target(ns(commits="a..b")), "range a..b")
        self.assertEqual(cli._run_target(ns()), "local diff")


if __name__ == "__main__":
    unittest.main()
