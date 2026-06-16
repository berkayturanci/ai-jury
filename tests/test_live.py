"""Live play-by-play mode (issue #210).

Covers the orchestrator on_event hook (deterministic per-phase ordering),
report.render_live_step formatting, and the CLI --live wiring (stdout stream,
per-step PR posting, and the cache-hit-does-not-stream case). Offline and
deterministic — agents run via --mock with a fixed --seed.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import cli, orchestrator, report  # noqa: E402
from ai_jury.adapters import AgentResult  # noqa: E402
from ai_jury.config import load_config  # noqa: E402

DIFF = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"


def _run(args, stdin=DIFF):
    out, err = io.StringIO(), io.StringIO()
    prev = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(args)
    except SystemExit as exc:
        code = exc.code
    finally:
        sys.stdin = prev
    return code, out.getvalue(), err.getvalue()


class RenderLiveStepTests(unittest.TestCase):
    def _ar(self, agent, vendor="anthropic", *, ok=True, output="hi", error=None):
        return AgentResult(
            agent=agent, vendor=vendor, ok=ok, output=output, duration_s=0.0, error=error
        )

    def test_review_step(self):
        title, body = report.render_live_step("review", self._ar("claude"))
        self.assertIn("Round 1 review", title)
        self.assertIn("`claude` (anthropic)", title)
        self.assertEqual(body, "hi")

    def test_debate_step_has_round(self):
        title, _ = report.render_live_step("debate", self._ar("codex", "openai"), round_no=2)
        self.assertIn("round 2", title)

    def test_chair_steps_labelled(self):
        vt, _ = report.render_live_step("verify", self._ar("claude"))
        st, _ = report.render_live_step("synthesis", self._ar("claude"))
        self.assertIn("chair `claude`", vt)
        self.assertIn("Decision", st)

    def test_failed_step_shows_error(self):
        title, body = report.render_live_step(
            "review", self._ar("codex", "openai", ok=False, output="", error="timeout")
        )
        self.assertIn("timeout", title)
        self.assertEqual(body, "_(no output)_")


class OrchestratorEventOrderTests(unittest.TestCase):
    def _config(self):
        d = Path(tempfile.mkdtemp())
        cfg = d / "jury.toml"
        cfg.write_text(
            '[jury]\nrounds = 2\nchair = "claude"\nverify = true\n'
            '\n[[agent]]\nname = "claude"\nvendor = "anthropic"\ncommand = "x"\n'
            '\n[[agent]]\nname = "codex"\nvendor = "openai"\ncommand = "y"\n'
        )
        return load_config(str(cfg))

    def test_events_fire_in_phase_order(self):
        events = []
        orchestrator.review_diff(
            self._config(),
            DIFF,
            mock=True,
            seed=1,
            on_event=lambda kind, result, round_no=None: events.append(
                (kind, result.agent, round_no)
            ),
        )
        kinds = [e[0] for e in events]
        # ALL reviews precede ALL debates precede verify precedes synthesis
        # (deterministic per-phase order, not interleaved completion order).
        order = {"review": 0, "debate": 1, "verify": 2, "synthesis": 3}
        ranks = [order[k] for k in kinds]
        self.assertEqual(ranks, sorted(ranks), f"events out of phase order: {kinds}")
        self.assertEqual(kinds[0], "review")
        self.assertEqual(kinds[-1], "synthesis")
        # Within round 1, agents stream in the configured panel order.
        review_agents = [a for k, a, _ in events if k == "review"]
        self.assertEqual(review_agents, ["claude", "codex"])

    def test_default_no_event_is_noop(self):
        # No on_event => run completes normally (the no-op path).
        outcome, _ = orchestrator.review_diff(self._config(), DIFF, mock=True, seed=1)
        self.assertTrue(outcome.reviews)

    def test_unresolvable_chair_skips_verify_and_synthesis_events(self):
        # When the chair resolves to a non-usable name, _verify/_synthesize return
        # None, so no verify/synthesis events are emitted (the guard's None path).
        events = []
        with mock.patch("ai_jury.orchestrator.resolve_chair", return_value="ghost"):
            outcome, _ = orchestrator.review_diff(
                self._config(),
                DIFF,
                mock=True,
                seed=1,
                on_event=lambda kind, _result, _round_no=None: events.append(kind),
            )
        self.assertNotIn("verify", events)
        self.assertNotIn("synthesis", events)
        self.assertIsNone(outcome.verify)
        self.assertIsNone(outcome.synthesis)


class CliLiveTests(unittest.TestCase):
    def test_live_streams_steps_and_suppresses_report(self):
        code, out, _ = _run(["--mock", "--diff-file", "-", "-q", "--seed", "1", "--live"])
        self.assertEqual(code, 0)
        self.assertIn("Round 1 review", out)
        self.assertIn("Decision — verdict & reasoning", out)
        # The consolidated report (its Run metadata table) is NOT also dumped.
        self.assertNotIn("Run metadata", out)

    def test_live_with_output_file_still_writes_report(self):
        d = Path(tempfile.mkdtemp())
        outp = d / "r.md"
        code, out, _ = _run(
            ["--mock", "--diff-file", "-", "-q", "--seed", "1", "--live", "-o", str(outp)]
        )
        self.assertEqual(code, 0)
        self.assertIn("Round 1 review", out)  # streamed to stdout
        # Read as UTF-8 explicitly: the report contains 🏛️ and Windows' default
        # cp1252 codec can't decode it.
        self.assertIn("Run metadata", outp.read_text(encoding="utf-8"))  # report on disk

    def test_live_format_json_still_emits_json(self):
        # --live streams markdown, but a json/sarif document must still reach stdout.
        import json

        code, out, _ = _run(
            ["--mock", "--diff-file", "-", "-q", "--seed", "1", "--live", "--format", "json"]
        )
        self.assertEqual(code, 0)
        # The stream is markdown blocks; the JSON document is the trailing chunk.
        # Find the first column-0 '{' from which the rest parses as JSON.
        lines = out.splitlines()
        data = None
        for i, line in enumerate(lines):
            if line.startswith("{"):
                try:
                    data = json.loads("\n".join(lines[i:]))
                    break
                except json.JSONDecodeError:
                    continue
        self.assertIsNotNone(data, "no JSON document found on stdout")
        self.assertIn("schema_version", data)

    def test_live_pr_without_post_does_not_post(self):
        # Posting is opt-in: bare --pr --live streams locally, never spams the PR.
        with (
            mock.patch("ai_jury.cli.pr_diff", return_value=DIFF),
            mock.patch("ai_jury.cli.pr_context", return_value="t\n\nb"),
            mock.patch("ai_jury.github.pr_head_sha", return_value="abc123"),
            mock.patch("ai_jury.cli.post_pr_comment") as ppc,
        ):
            code, out, _ = _run(["--mock", "--pr", "5", "-q", "--seed", "1", "--live"])
        self.assertEqual(code, 0)
        self.assertIn("Round 1 review", out)
        ppc.assert_not_called()

    def test_live_posts_each_step_with_pr_and_post(self):
        with (
            mock.patch("ai_jury.cli.pr_diff", return_value=DIFF),
            mock.patch("ai_jury.cli.pr_context", return_value="t\n\nb"),
            mock.patch("ai_jury.github.pr_head_sha", return_value="abc123"),
            mock.patch("ai_jury.cli.post_pr_comment") as ppc,
        ):
            code, _, _ = _run(["--mock", "--pr", "5", "-q", "--seed", "1", "--live", "--post"])
        self.assertEqual(code, 0)
        # A comment per streamed step (>= 2 reviewers + decision), plus the final summary.
        self.assertGreaterEqual(ppc.call_count, 4)

    def test_live_post_failure_does_not_crash(self):
        # A failing live STEP post is logged and never aborts the run; the final
        # summary post (separate, existing behavior) still succeeds here.
        def fail_only_steps(_pr, body, _repo=None):
            if "AI Jury — " in body:  # an em-dash live step title
                raise RuntimeError("boom")

        with (
            mock.patch("ai_jury.cli.pr_diff", return_value=DIFF),
            mock.patch("ai_jury.cli.pr_context", return_value="t\n\nb"),
            mock.patch("ai_jury.github.pr_head_sha", return_value="abc123"),
            mock.patch("ai_jury.cli.post_pr_comment", side_effect=fail_only_steps),
        ):
            code, out, err = _run(["--mock", "--pr", "5", "--seed", "1", "--live", "--post"])
        self.assertEqual(code, 0)
        self.assertIn("failed to post step", err)
        self.assertIn("Round 1 review", out)  # streaming completed despite post failures


if __name__ == "__main__":
    unittest.main()
