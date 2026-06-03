"""Full-transcript / verbose output mode (issue: full transcript).

Covers report.render_transcript (both layouts), the CLI --transcript / --verbose /
--no-transcript wiring, the [jury] transcript config key, and the invariant that
the transcript toggle is rendering-only (does not change config_hash). Offline and
deterministic — agents run via --mock with a fixed --seed.
"""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import cli, report  # noqa: E402
from ai_jury.adapters import AgentResult  # noqa: E402
from ai_jury.config import JuryConfig, config_hash, load_config  # noqa: E402

DIFF = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"


def _ar(agent, vendor="anthropic", *, ok=True, output="some output", error=None):
    return AgentResult(agent=agent, vendor=vendor, ok=ok, output=output,
                       duration_s=0.0, error=error)


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


class RenderTranscriptTests(unittest.TestCase):
    def setUp(self):
        self.reviews = [_ar("claude", "anthropic", output="claude says X"),
                        _ar("codex", "openai", output="codex says Y")]
        self.debate = [_ar("claude", "anthropic", output="claude rebuts Y")]
        self.verify = _ar("claude", "anthropic", output="verification notes")
        self.synthesis = _ar("claude", "anthropic", output="REQUEST CHANGES because Z")

    def test_transcript_is_conversation_first(self):
        md = report.render_transcript(
            self.reviews, self.debate, self.synthesis,
            chair="claude", verify=self.verify, lead_with_summary=False,
        )
        self.assertIn("# 🏛️ AI Jury — full transcript", md)
        # Each reviewer's raw output and the chair's reasoning are present.
        self.assertIn("claude says X", md)
        self.assertIn("codex says Y", md)
        self.assertIn("claude rebuts Y", md)
        self.assertIn("verification notes", md)
        self.assertIn("REQUEST CHANGES because Z", md)
        self.assertIn("Decision — verdict & reasoning", md)
        # Conversation comes before the summary recap.
        self.assertLess(md.index("Round 1"), md.index("Structured findings"))

    def test_verbose_is_summary_first(self):
        md = report.render_transcript(
            self.reviews, self.debate, self.synthesis,
            chair="claude", verify=self.verify, lead_with_summary=True,
        )
        self.assertIn("# 🏛️ AI Jury — verbose report", md)
        self.assertIn("# Full transcript", md)
        # Summary (structured findings) precedes the full transcript.
        self.assertLess(md.index("Structured findings"), md.index("Full transcript"))
        self.assertIn("claude says X", md)

    def test_failed_agent_and_missing_synthesis(self):
        md = report.render_transcript(
            [_ar("codex", "openai", ok=False, output="", error="timeout")],
            [], None, chair="claude",
        )
        self.assertIn("timeout", md)
        self.assertIn("_(no synthesis produced)_", md)

    def test_explicit_classification_and_review_scope(self):
        # classification supplied (skips re-classify) + review_scope rendered.
        from ai_jury.classification import classify
        cls = classify(findings=[], groups=[])
        md = report.render_transcript(
            self.reviews, [], self.synthesis, chair="claude",
            classification=cls, review_scope="_Reviewing 1 new file since last run._",
        )
        self.assertIn("Reviewing 1 new file since last run", md)

    def test_failed_synthesis_and_verify(self):
        md = report.render_transcript(
            self.reviews, [], _ar("claude", ok=False, output="", error="boom"),
            chair="claude", verify=_ar("claude", ok=False, output="", error="vfail"),
        )
        self.assertIn("Synthesis failed: boom", md)
        self.assertIn("Verification failed: vfail", md)

    def test_no_debate_omits_round_2(self):
        md = report.render_transcript(self.reviews, [], self.synthesis, chair="claude")
        self.assertNotIn("Round 2", md)
        self.assertIn("Round 1", md)

    def test_redaction_policy_disclosed(self):
        # Parity with render(): the transcript must disclose the redaction policy
        # (a security-relevant signal) to whoever reads the shared artifact.
        md = report.render_transcript(
            self.reviews, [], self.synthesis, chair="claude",
            context_mode="diff-only", redact_secrets=True, redaction_count=3,
        )
        self.assertIn("secret redaction: on (3 redacted)", md)
        self.assertIn("context mode: diff-only", md)

    def test_context_policy_one_sided(self):
        # Only context_mode set (no redaction info), and vice-versa.
        md1 = report.render_transcript(self.reviews, [], self.synthesis,
                                       chair="claude", context_mode="expanded")
        self.assertIn("context mode: expanded", md1)
        self.assertNotIn("secret redaction", md1)
        md2 = report.render_transcript(self.reviews, [], self.synthesis,
                                       chair="claude", redact_secrets=False)
        self.assertIn("secret redaction: off", md2)
        self.assertNotIn("context mode:", md2)


class CliTranscriptWiringTests(unittest.TestCase):
    def test_default_is_summary(self):
        code, out, _ = _run(["--mock", "--diff-file", "-", "-q", "--seed", "1"])
        self.assertEqual(code, 0)
        self.assertIn("# 🏛️ AI Jury", out)
        self.assertNotIn("full transcript", out)
        self.assertNotIn("verbose report", out)

    def test_transcript_flag(self):
        code, out, _ = _run(["--mock", "--diff-file", "-", "-q", "--seed", "1", "--transcript"])
        self.assertEqual(code, 0)
        self.assertIn("# 🏛️ AI Jury — full transcript", out)

    def test_verbose_flag(self):
        code, out, _ = _run(["--mock", "--diff-file", "-", "-q", "--seed", "1", "--verbose"])
        self.assertEqual(code, 0)
        self.assertIn("# 🏛️ AI Jury — verbose report", out)
        self.assertIn("# Full transcript", out)

    def test_config_transcript_true_defaults_on(self):
        d = Path(tempfile.mkdtemp())
        cfg = d / "jury.toml"
        cfg.write_text('[jury]\nrounds = 1\nchair = "claude"\ntranscript = true\n'
                       '\n[[agent]]\nname = "claude"\nvendor = "anthropic"\ncommand = "x"\n')
        code, out, _ = _run(["--mock", "--diff-file", "-", "-q", "--seed", "1", "--config", str(cfg)])
        self.assertEqual(code, 0)
        self.assertIn("full transcript", out)

    def test_verbose_implies_transcript_even_with_no_transcript(self):
        # --verbose is its own opt-in: it includes the transcript regardless of
        # --no-transcript (documented precedence).
        code, out, _ = _run(["--mock", "--diff-file", "-", "-q", "--seed", "1",
                             "--no-transcript", "--verbose"])
        self.assertEqual(code, 0)
        self.assertIn("# 🏛️ AI Jury — verbose report", out)
        self.assertIn("# Full transcript", out)

    def test_no_transcript_overrides_config(self):
        d = Path(tempfile.mkdtemp())
        cfg = d / "jury.toml"
        cfg.write_text('[jury]\nrounds = 1\nchair = "claude"\ntranscript = true\n'
                       '\n[[agent]]\nname = "claude"\nvendor = "anthropic"\ncommand = "x"\n')
        code, out, _ = _run(["--mock", "--diff-file", "-", "-q", "--seed", "1",
                             "--config", str(cfg), "--no-transcript"])
        self.assertEqual(code, 0)
        self.assertNotIn("full transcript", out)


class TranscriptConfigInvariantTests(unittest.TestCase):
    def test_transcript_key_parsed(self):
        d = Path(tempfile.mkdtemp())
        cfg = d / "jury.toml"
        cfg.write_text('[jury]\nrounds = 1\nchair = "a"\ntranscript = true\n'
                       '\n[[agent]]\nname = "a"\nvendor = "anthropic"\ncommand = "x"\n')
        c = load_config(str(cfg))
        self.assertTrue(c.transcript)

    def test_rendering_only_not_in_config_hash(self):
        # The transcript toggle must NOT change config_hash (it is rendering-only).
        base = JuryConfig(transcript=False)
        on = JuryConfig(transcript=True)
        self.assertEqual(config_hash(base), config_hash(on))


if __name__ == "__main__":
    unittest.main()
