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

    def test_put_scrubs_control_chars(self):
        # Content is agent-influenced; a raw escape would be a terminal-injection
        # attempt. The cell content must never carry control bytes (only the
        # trusted sgr param emits escapes).
        s = Screen(28, 1)
        # C0/ESC/DEL/C1 + bidi override (U+202E) + zero-width (U+200B) + BOM
        s.put(0, 0, "\x1b[2Jx\x07y\x7fz\x9b‮EVIL​﻿", "")
        plain = s.to_plain()
        for bad in ("\x1b", "\x07", "\x7f", "\x9b", "‮", "​", "﻿"):
            self.assertNotIn(bad, plain)
        self.assertIn("x", plain)
        self.assertIn("z", plain)
        self.assertNotIn("\x1b[2J", s.to_ansi())   # injected control seq gone from terminal output


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
        self.assertIn("PR #142", frame)
        self.assertIn("REQUEST CHANGES", frame)        # decision banner
        self.assertIn("DECISION", frame)
        self.assertIn("chair: codex", frame)           # chair recorded, no judge

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
        self.assertIn("agreed", joined)

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
        self.assertIn("panel vote", frame)              # title + interior label
        self.assertIn("DECISION by panel vote", frame)
        self.assertIn("REQUEST", frame)                 # ballot chips / banner
        self.assertIn("request changes", frame)         # tally line

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

    def test_many_jurors_fall_back_to_roster(self):
        # Too many jurors to seat around the table → compact roster, no clipping.
        agents = [(f"juror{i}", "local") for i in range(16)]
        court = Courtroom(agents, agents[0][0], animate=False, cols=92, rows=30, capture=[])
        self.assertFalse(court._seats_fit())
        court.open()
        court.step("review", _ar("juror0", output=_REVIEW))
        court.close()
        frame = court.screen.to_plain()
        self.assertIn("JURY:", frame)
        self.assertIn("juror0", frame)
        self.assertIn("juror1", frame)

    def test_narrow_terminal_uses_roster(self):
        agents = [(f"a{i}", "openai") for i in range(6)]
        court = Courtroom(agents, agents[0][0], animate=False, cols=44, rows=30, capture=[])
        self.assertFalse(court._seats_fit())
        court.open()
        court.close()
        self.assertIn("JURY:", court.screen.to_plain())

    def test_roster_wraps_and_caps_many_jurors(self):
        # Lots of long-named jurors on a narrow width → roster wraps across rows
        # and stops (caps) without clipping/erroring.
        agents = [(f"longjuror{i:02d}", "local") for i in range(40)]
        court = Courtroom(agents, agents[0][0], animate=False, cols=44, rows=30, capture=[])
        court.open()
        court.close()
        self.assertIn("JURY:", court.screen.to_plain())

    def test_slots_empty(self):
        court = _court()
        self.assertEqual(court._slots(0), [])


_PIX_AGENTS = [("claude", "anthropic"), ("codex", "openai"),
               ("agy", "google"), ("qwen", "local")]


def _pix_court(**kw):
    return Courtroom(_PIX_AGENTS, "claude", animate=False, cols=92, rows=30,
                     capture=[], style="pixel", **kw)


class PixelSceneTest(unittest.TestCase):
    """The pixel-art style (--theater-style pixel): half-block room render."""

    def _drive(self, court):
        court.open()
        court.step("review", _ar("claude", output=_REVIEW))
        court.step("review", _ar("codex", "openai", output=_REVIEW))
        court.step("debate", _ar("agy", "google", output=_REVIEW), round_no=2)
        court.step("verify", _ar("codex", "openai", output=_VERIFY))
        court.step("synthesis", _ar("codex", "openai", output=_SYNTH))

    def test_pixel_chair_scene_renders_band_and_names(self):
        court = _pix_court(case="PR #142")
        self._drive(court)
        court.close()
        plain = court.screen.to_plain()
        self.assertIn("▀", plain)                       # half-block band drawn
        for name in ("claude", "codex", "agy", "qwen"):
            self.assertIn(name, plain)
        self.assertIn("REQUEST CHANGES", plain)         # chair decision banner
        self.assertIn("DECISION (chair)", plain)
        # truecolor fg+bg per half-block cell
        self.assertIn("48;2;", court.screen.to_ansi())

    def test_pixel_vote_finale_banner_tally_and_top_ballots(self):
        vote = types.SimpleNamespace(
            verdict="REQUEST CHANGES",
            tally={"REQUEST CHANGES": 3, "COMMENT": 1, "APPROVE": 0},
            ballots=[
                types.SimpleNamespace(reviewer="claude", vote="REQUEST CHANGES", reason=""),
                types.SimpleNamespace(reviewer="codex", vote="REQUEST CHANGES", reason=""),
                types.SimpleNamespace(reviewer="agy", vote="COMMENT", reason=""),
                types.SimpleNamespace(reviewer="qwen", vote="REQUEST CHANGES", reason=""),
            ],
        )
        court = _pix_court(decision="vote", case="PR #142")
        self._drive(court)
        court.set_vote(vote)
        court.close()
        plain = court.screen.to_plain()
        self.assertIn("DECISION by panel vote", plain)
        self.assertIn("request changes", plain)         # tally on the banner
        self.assertIn("[REQUEST", plain)                # a top-edge ballot chip

    def test_pixel_verify_overlay(self):
        court = _pix_court()
        court.open()
        court.step("review", _ar("claude", output=_REVIEW))
        court.step("verify", _ar("codex", "openai", output=_VERIFY))
        frame = court.screen.to_plain()
        self.assertIn("verifying findings", frame)
        self.assertIn("▀", frame)

    def test_pixel_ascii_falls_back_to_flat(self):
        # unicode off → no half-block; pixel transparently uses the flat scene.
        court = Courtroom(_PIX_AGENTS, "claude", animate=False, cols=92, rows=30,
                          capture=[], style="pixel", unicode=False)
        court.open()
        court.step("review", _ar("claude", output=_REVIEW))
        court.close()
        plain = court.screen.to_plain()
        self.assertNotIn("▀", plain)
        self.assertIn("claude", plain)

    def test_pixel_many_jurors_falls_back_to_roster(self):
        agents = [(f"j{i}", "local") for i in range(16)]
        court = Courtroom(agents, agents[0][0], animate=False, cols=92, rows=30,
                          capture=[], style="pixel")
        court.open()
        court.close()
        plain = court.screen.to_plain()
        self.assertIn("JURY:", plain)
        self.assertNotIn("▀", plain)        # no pixel band when seats don't fit

    def test_pix_slots_empty(self):
        self.assertEqual(_pix_court()._pix_slots(0, 92), [])


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


class LiveAndFitTest(unittest.TestCase):
    """Background ticker (live clock) + verdict-banner truncation."""

    def test_ticker_repaints_while_open_then_stops(self):
        import threading
        import unittest.mock as mock

        buf = io.StringIO()
        with mock.patch("ai_jury.theater.time.sleep"):   # no real beat sleeps
            c = Courtroom(_AGENTS, "codex", animate=True, stream=buf)
            c.tick_interval = 0.01                        # Event.wait stays real
            c.open()
            before = buf.getvalue()
            # Real delay that the time.sleep patch does NOT affect, so the
            # background ticker gets wall-clock time to repaint.
            threading.Event().wait(0.08)
            self.assertGreater(len(buf.getvalue()), len(before))
            c.close()
        self.assertIsNone(c._tick_thread)                # joined/cleared on close

    def test_ticker_noop_when_not_animating(self):
        c = _court()           # animate=False
        c._start_ticker()
        self.assertIsNone(c._tick_thread)

    def test_fit_truncates_with_ellipsis(self):
        c = _court()
        self.assertEqual(c._fit("short", 20), "short")
        out = c._fit("a very long verdict line that overflows", 12)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(len(out), 12)
        self.assertEqual(c._fit("x", 0), "")
        ascii_court = Courtroom(_AGENTS, "codex", animate=False, cols=92, rows=30,
                                capture=[], unicode=False)
        self.assertTrue(ascii_court._fit("y" * 40, 8).endswith("..."))

    def test_long_verdict_banner_is_truncated(self):
        court = _court(case="issue #264", mode="issue")
        court.open()
        court.step("synthesis", _ar("codex", "openai",
                   output="## Verdict\nNEEDS-INFO — " + "blah " * 80))
        court.close()
        plain = court.screen.to_plain()
        self.assertIn("…", plain)                        # banner ellipsised
        for line in plain.split("\n"):
            self.assertLessEqual(len(line), court.cols)   # nothing overflows


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
