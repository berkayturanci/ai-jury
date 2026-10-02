"""``--events-file``: the run's progress as metadata-only NDJSON for an observer in
another process. The record builders are pure; the CLI tests run the offline
``--mock`` panel and read the file back."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
            },
        )

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


class TheCli(unittest.TestCase):
    def setUp(self):
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
        self.assertTrue({r["phase"] for r in steps} <= set(events.PHASES))
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

    def test_an_interrupted_run_ends_cancelled(self):
        with mock.patch("ai_jury.cli.review_diff", side_effect=KeyboardInterrupt()):
            code = _run(["--mock", "--diff-file", str(self.diff), "--events-file", str(self.ev)])
        self.assertEqual(code, 130)
        recs = _read(self.ev)
        self.assertEqual([r["event"] for r in recs], ["start", "end"])
        self.assertEqual(recs[-1]["status"], "cancelled")

    def test_a_failed_run_ends_error(self):
        with mock.patch("ai_jury.cli.review_diff", side_effect=RuntimeError("no usable agents")):
            code = _run(["--mock", "--diff-file", str(self.diff), "--events-file", str(self.ev)])
        self.assertEqual(code, 2)
        self.assertEqual(_read(self.ev)[-1]["status"], "error")


class RunTarget(unittest.TestCase):
    def test_each_source_is_named(self):
        def ns(**kw):
            base = {"pr": None, "issue": None, "commit": None, "commits": None}
            base.update(kw)
            return mock.Mock(**base)

        self.assertEqual(cli._run_target(ns(pr=5)), "PR #5")
        self.assertEqual(cli._run_target(ns(issue=9)), "issue #9")
        self.assertEqual(cli._run_target(ns(commit="abc")), "commit abc")
        self.assertEqual(cli._run_target(ns(commits="a..b")), "range a..b")
        self.assertEqual(cli._run_target(ns()), "local diff")


if __name__ == "__main__":
    unittest.main()
