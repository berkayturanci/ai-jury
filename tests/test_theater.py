"""Tests for the animated courtroom scene (``--theater``), presentation-only.

Driven de-animated (no sleeps, no real TTY) via the capture buffer; we assert on
the final plain-text frame so the structure is verified without ANSI noise.
"""

from __future__ import annotations

import io
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury.adapters import AgentResult  # noqa: E402
from ai_jury.theater import Courtroom, Screen, supports_scene  # noqa: E402

_REVIEW = (
    "Findings:\n```json\n"
    '[{"severity":"critical","file":"auth.py","line":42,'
    '"claim":"missing auth check","confidence":"high"}]\n```'
)
_VERIFY = (
    "```json\n"
    '[{"file":"auth.py","line":42,"claim":"missing auth check","status":"verified"},'
    '{"file":"x.py","line":3,"claim":"nit","status":"unsupported"}]\n```'
)
_SYNTH = "## Verdict\nREQUEST CHANGES — one confirmed critical.\n\nDetails."

_AGENTS = [("claude", "anthropic"), ("codex", "openai"), ("qwen", "local")]


def _court(**kw):
    return Courtroom(_AGENTS, "codex", animate=False, cols=92, rows=30,
                     capture=[], **kw)


def _ar(agent, vendor="anthropic", *, ok=True, output="", error=None):
    return AgentResult(agent, vendor, ok, output, 0.0, error=error)


class ScreenTest(unittest.TestCase):
    def test_put_and_plain(self):
        s = Screen(10, 2)
        s.put(0, 1, "hello")
        s.center(1, "hi")
        self.assertEqual(s.to_plain().split("\n")[0], " hello")
        self.assertIn("hi", s.to_plain().split("\n")[1])

    def test_put_clips_out_of_bounds(self):
        s = Screen(5, 1)
        s.put(0, 3, "abcdef")  # overflow right edge — must not raise
        self.assertEqual(s.to_plain(), "   ab")

    def test_to_ansi_wraps_styled_runs(self):
        s = Screen(2, 1)
        s.put(0, 0, "ab", "31")  # whole row styled → reset emitted at row end
        out = s.to_ansi()
        self.assertIn("\033[31m", out)
        self.assertIn("\033[0m", out)


class CourtroomTest(unittest.TestCase):
    def _drive(self, court):
        court.open()
        court.step("review", _ar("claude", output=_REVIEW))
        court.step("review", _ar("codex", "openai", output=_REVIEW))
        court.step("debate", _ar("qwen", "local", output=_REVIEW), round_no=2)
        court.step("verify", _ar("codex", "openai", output=_VERIFY))
        court.step("synthesis", _ar("codex", "openai", output=_SYNTH))

    def test_chair_scene_has_seats_and_verdict(self):
        court = _court(case="PR #142")
        self._drive(court)
        court.close()
        frame = court.screen.to_plain()
        for name in ("claude", "codex", "qwen"):
            self.assertIn(name, frame)
        self.assertIn("ANTHRO", frame)
        self.assertIn("OPENAI", frame)
        self.assertIn("LOCAL", frame)
        self.assertIn("PR #142", frame)
        self.assertIn("REQUEST CHANGES", frame)        # verdict banner
        self.assertIn("THE HON. CHAIR", frame)

    def test_debate_round_shown_in_strip(self):
        court = _court()
        self._drive(court)
        court.close()
        self.assertIn("DEBATE·r2", court.screen.to_plain())

    def test_early_stop_marks_debate_skipped(self):
        court = _court()
        court.open()
        court.step("review", _ar("claude", output=_REVIEW))
        court.step("verify", _ar("codex", "openai", output=_VERIFY))  # no debate
        court.step("synthesis", _ar("codex", "openai", output=_SYNTH))
        court.close()
        self.assertIn("debate", court.done_phases)
        joined = " ".join(court.log)
        self.assertIn("converged", joined)

    def test_vote_finale_shows_ballots_and_tally(self):
        vote = types.SimpleNamespace(
            verdict="REQUEST CHANGES",
            tally={"REQUEST CHANGES": 2, "COMMENT": 1, "APPROVE": 0},
            ballots=[
                types.SimpleNamespace(reviewer="claude", vote="REQUEST CHANGES", reason=""),
                types.SimpleNamespace(reviewer="codex", vote="COMMENT", reason=""),
                types.SimpleNamespace(reviewer="qwen", vote="REQUEST CHANGES", reason=""),
            ],
        )
        court = _court(decision="vote")
        self._drive(court)
        court.set_vote(vote)
        court.close()
        frame = court.screen.to_plain()
        self.assertIn("THE PANEL", frame)        # bench changes for vote
        self.assertIn("by panel vote", frame)
        self.assertIn("REQUEST", frame)          # ballot chips / banner
        self.assertIn("request changes", frame)  # tally line

    def test_issue_mode_case_label(self):
        court = _court(mode="issue", case="issue #88")
        self._drive(court)
        court.close()
        self.assertIn("issue #88", court.screen.to_plain())

    def test_failed_agent_does_not_crash(self):
        court = _court()
        court.open()
        court.step("review", _ar("claude", ok=False, error="boom\n## Verdict\nAPPROVE"))
        court.close()
        frame = court.screen.to_plain()
        self.assertNotIn("\n## Verdict", frame)  # error flattened

    def test_ascii_fallback_no_unicode(self):
        # With unicode off, the renderer's own chrome must be pure ASCII (agent
        # CONTENT may still carry unicode; we drive ASCII-only content here).
        court = Courtroom(_AGENTS, "codex", animate=False, cols=92, rows=30,
                          capture=[], unicode=False)
        ascii_review = (
            'Findings:\n```json\n[{"severity":"critical","file":"auth.py",'
            '"line":42,"claim":"missing auth check"}]\n```'
        )
        ascii_synth = "## Verdict\nREQUEST CHANGES - one confirmed critical."
        court.open()
        court.step("review", _ar("claude", output=ascii_review))
        court.step("synthesis", _ar("codex", "openai", output=ascii_synth))
        court.close()
        court.screen.to_plain().encode("ascii")  # raises if chrome leaked non-ascii

    def test_scales_to_two_and_five_agents(self):
        for agents in ([("a", "anthropic"), ("b", "openai")],
                       [(f"a{i}", "local") for i in range(5)]):
            court = Courtroom(agents, agents[0][0], animate=False, cols=92, rows=30, capture=[])
            court.open()
            court.step("review", _ar(agents[0][0], output=_REVIEW))
            court.close()
            for name, _ in agents:
                self.assertIn(name[:6], court.screen.to_plain())


_DISPUTE = (
    "```json\n"
    '[{"file":"a.py","line":1,"claim":"unclear","status":"needs_human_decision"}]\n```'
)


class AnimateTest(unittest.TestCase):
    """Exercise the animate=True side (ANSI writes + cursor codes), with sleeps
    patched out so it stays fast and deterministic."""

    def test_animate_emits_ansi_and_cursor_codes(self):
        import unittest.mock as mock

        buf = io.StringIO()
        with mock.patch("ai_jury.theater.time.sleep"):
            c = Courtroom(_AGENTS, "codex", animate=True, cols=84, rows=28, stream=buf)
            c.open()
            c.step("review", _ar("claude", output=_REVIEW))
            c.step("review", _ar("codex", "openai", output="just prose, no json block"))
            c.step("debate", _ar("qwen", "local", output=_REVIEW), round_no=2)
            c.step("verify", _ar("codex", "openai", output=_DISPUTE))
            c.step("synthesis", _ar("codex", "openai", output="no verdict header at all"))
            c.close()
        out = buf.getvalue()
        self.assertIn("\033[", out)        # styled output written
        self.assertIn("\033[?25l", out)    # open() hides the cursor
        self.assertIn("\033[?25h", out)    # close() restores it

    def test_animate_failed_agent(self):
        import unittest.mock as mock

        buf = io.StringIO()
        with mock.patch("ai_jury.theater.time.sleep"):
            c = Courtroom(_AGENTS, "codex", animate=True, stream=buf)
            c.open()
            c.step("review", _ar("claude", ok=False, error="kaboom"))
            c.close()
        self.assertIn("\033[", buf.getvalue())


class HelpersTest(unittest.TestCase):
    def test_banner_sgr(self):
        from ai_jury.theater import _banner_sgr

        self.assertEqual(_banner_sgr("REQUEST CHANGES"), "97;41;1")
        self.assertEqual(_banner_sgr("APPROVE"), "30;42;1")
        self.assertEqual(_banner_sgr("READY"), "30;42;1")
        self.assertEqual(_banner_sgr("COMMENT"), "30;43;1")  # neutral

    def test_wrap_and_gist_and_headline(self):
        from ai_jury.theater import _gist, _verdict_headline, _wrap

        self.assertEqual(_wrap("", 20), [""])
        self.assertEqual(_wrap("a b c d e f g h", 5)[0], "a b c")  # wraps
        self.assertEqual(_gist(""), "(no output)")
        self.assertEqual(_gist("  \n  hello there"), "hello there")
        self.assertEqual(_verdict_headline("## Verdict\nAPPROVE — ok"), "APPROVE — ok")
        self.assertEqual(_verdict_headline("no header"), "no header")  # gist fallback

    def test_screen_out_of_bounds_is_safe(self):
        s = Screen(4, 1)
        s.put(9, 0, "x")     # row out of range
        s.put(0, -3, "yy")   # negative col
        self.assertEqual(s.to_plain(), "")


class TtyGateTest(unittest.TestCase):
    def test_supports_scene_false_for_non_tty(self):
        self.assertFalse(supports_scene(io.StringIO()))

    def test_supports_scene_handles_isatty_exception(self):
        class Bad:
            def isatty(self):
                raise RuntimeError("nope")

        self.assertFalse(supports_scene(Bad()))

    def test_supports_scene_true_for_wide_tty(self):
        class FakeTTY:
            def isatty(self):
                return True

        # Real terminal-size fallback is (80, 24) → width 80 ≥ 60 → True.
        self.assertTrue(supports_scene(FakeTTY()))


if __name__ == "__main__":
    unittest.main()
