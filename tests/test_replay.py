"""Tests for ``jury replay`` (issue #449) — offline, presentation-only.

Covers the loader (both accepted shapes, every rejection path), the synthesized
event sequence, the theater driver (via a fake court recording ordered calls),
and the CLI wiring including the non-TTY transcript fallback.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import cli, theater  # noqa: E402
from ai_jury.adapters import AgentResult  # noqa: E402
from ai_jury.cache import outcome_to_dict  # noqa: E402
from ai_jury.orchestrator import JuryOutcome  # noqa: E402
from ai_jury.replay import (  # noqa: E402
    _MAX_REPLAY_BYTES,
    ReplayError,
    load_outcome,
    replay_events,
    replay_into,
)


def _ar(agent, vendor="anthropic", *, ok=True, output="looks fine", error=None):
    return AgentResult(agent, vendor, ok, output, 1.0, error=error)


def _outcome(**kw):
    base = {
        "reviews": [_ar("claude"), _ar("codex", "openai")],
        "debate": [
            _ar("claude", output="I maintain my finding"),
            _ar("codex", "openai", output="I concede"),
        ],
        "synthesis": _ar("codex", "openai", output="## Verdict\nAPPROVE — fine."),
        "chair": "codex",
        "verify": _ar("codex", "openai", output="[]"),
        "rounds_executed": 2,
    }
    base.update(kw)
    return JuryOutcome(**base)


class _FakeCourt:
    """Records the exact ordered theater-API calls replay makes."""

    instances: list[_FakeCourt] = []

    def __init__(self, agents, chair, **kw):
        self.agents = list(agents)
        self.chair = chair
        self.kw = kw
        self.calls: list[tuple] = []
        _FakeCourt.instances.append(self)

    def open(self):
        self.calls.append(("open",))

    def step(self, kind, result, round_no=None):
        self.calls.append(("step", kind, result.agent, round_no))

    def set_vote(self, vote):
        self.calls.append(("set_vote", getattr(vote, "verdict", None)))

    def close(self):
        self.calls.append(("close",))


class LoadOutcomeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _write(self, payload, name="outcome.json"):
        path = self.dir / name
        text = payload if isinstance(payload, str) else json.dumps(payload)
        path.write_text(text, encoding="utf-8")
        return path

    def test_loads_bare_outcome_shape(self):
        path = self._write(outcome_to_dict(_outcome()))
        got = load_outcome(path)
        self.assertEqual([r.agent for r in got.reviews], ["claude", "codex"])
        self.assertEqual(got.chair, "codex")
        self.assertTrue(got.synthesis.output.startswith("## Verdict"))
        self.assertEqual(got.rounds_executed, 2)

    def test_loads_cache_entry_shape(self):
        # The on-disk result-cache entry wraps the outcome dict; the MAC is a
        # cache-integrity concern, not a replay one, so it is not verified.
        entry = {
            "cache_schema": 1,
            "cache_key": "deadbeef",
            "outcome": outcome_to_dict(_outcome()),
            "mac": "not-checked-by-replay",
        }
        got = load_outcome(self._write(entry))
        self.assertEqual(len(got.reviews), 2)
        self.assertEqual(got.verify.agent, "codex")

    def test_rejects_format_json_report_shape(self):
        report = {"schema_version": 1, "metadata": {}, "findings": [], "verdict": ""}
        with self.assertRaisesRegex(ReplayError, "cannot be replayed"):
            load_outcome(self._write(report))

    def test_rejects_unrecognized_shape(self):
        with self.assertRaisesRegex(ReplayError, "not a recognized outcome shape"):
            load_outcome(self._write({"hello": "world"}))

    def test_rejects_invalid_json(self):
        with self.assertRaisesRegex(ReplayError, "not valid JSON"):
            load_outcome(self._write("{not json"))

    def test_rejects_deeply_nested_json_without_crashing(self):
        # A RecursionError from a hostile deeply-nested file must surface as a
        # ReplayError, not a traceback (mirrors cache.py's fail-closed read).
        path = self._write("[" * 100_000 + "]" * 100_000)
        with self.assertRaises(ReplayError):
            load_outcome(path)

    def test_rejects_non_object_json(self):
        with self.assertRaisesRegex(ReplayError, "not a JSON object"):
            load_outcome(self._write("[1, 2, 3]"))

    def test_rejects_missing_file(self):
        with self.assertRaisesRegex(ReplayError, "cannot read"):
            load_outcome(self.dir / "nope.json")

    def test_rejects_non_utf8_file(self):
        path = self.dir / "binary.json"
        path.write_bytes(b"\xff\xfe{}")
        with self.assertRaisesRegex(ReplayError, "not UTF-8"):
            load_outcome(path)

    def test_rejects_oversized_file(self):
        path = self.dir / "huge.json"
        path.write_text("x" * (_MAX_REPLAY_BYTES + 16), encoding="utf-8")
        with self.assertRaisesRegex(ReplayError, "replay limit"):
            load_outcome(path)

    def test_rejects_malformed_outcome(self):
        # A review record missing required AgentResult keys must not traceback.
        with self.assertRaisesRegex(ReplayError, "malformed outcome"):
            load_outcome(self._write({"reviews": [{"agent": "claude"}]}))

    def test_rejects_empty_reviews(self):
        with self.assertRaisesRegex(ReplayError, "no reviews"):
            load_outcome(self._write({"reviews": []}))


class ReplayEventsTest(unittest.TestCase):
    def test_full_sequence_matches_live_order(self):
        events = list(replay_events(_outcome()))
        self.assertEqual(
            [(k, r.agent, n) for k, r, n in events],
            [
                ("review", "claude", None),
                ("review", "codex", None),
                ("debate", "claude", 2),
                ("debate", "codex", 2),
                ("verify", "codex", None),
                ("synthesis", "codex", None),
            ],
        )

    def test_debate_round_number_follows_rounds_executed(self):
        events = list(replay_events(_outcome(rounds_executed=3)))
        self.assertEqual({n for k, _, n in events if k == "debate"}, {3})

    def test_missing_phases_are_skipped(self):
        outcome = _outcome(debate=[], verify=None, synthesis=None, rounds_executed=1)
        events = list(replay_events(outcome))
        self.assertEqual([k for k, _, _ in events], ["review", "review"])


class ReplayIntoTest(unittest.TestCase):
    def test_drives_theater_api_in_order(self):
        court = _FakeCourt([("claude", "anthropic")], "codex")
        replay_into(court, _outcome())
        self.assertEqual(court.calls[0], ("open",))
        self.assertEqual(court.calls[-1], ("close",))
        self.assertEqual(
            [c[1] for c in court.calls if c[0] == "step"],
            ["review", "review", "debate", "debate", "verify", "synthesis"],
        )

    def test_set_vote_before_close_when_vote_given(self):
        court = _FakeCourt([("claude", "anthropic")], "codex")
        vote = mock.Mock(verdict="APPROVE")
        replay_into(court, _outcome(), vote=vote)
        self.assertEqual(court.calls[-2:], [("set_vote", "APPROVE"), ("close",)])

    def test_close_still_called_when_step_raises(self):
        court = _FakeCourt([("claude", "anthropic")], "codex")
        court.step = mock.Mock(side_effect=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            replay_into(court, _outcome())
        self.assertEqual(court.calls[-1], ("close",))


class CliReplayTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "outcome.json"
        self.path.write_text(json.dumps(outcome_to_dict(_outcome())), encoding="utf-8")

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_transcript_fallback_streams_steps_in_order(self):
        rc, out, _ = self._run(["replay", str(self.path)])
        self.assertEqual(rc, 0)
        self.assertEqual(out.count("## 🏛️ AI Jury"), 6)
        pos = [out.index(s) for s in
               ("Round 1 review", "Cross-examination · round 2",
                "Verification", "Decision")]
        self.assertEqual(pos, sorted(pos))

    def test_theater_degrades_to_transcript_off_tty(self):
        # StringIO is not a TTY, so --theater must fall back to the same plain
        # step stream the live path uses.
        rc, out, _ = self._run(["replay", str(self.path), "--theater"])
        self.assertEqual(rc, 0)
        self.assertIn("Round 1 review", out)
        self.assertNotIn("\033[2J", out)  # no scene control sequences

    def test_decision_vote_parses_and_replays(self):
        rc, out, _ = self._run(["replay", str(self.path), "--decision", "vote"])
        self.assertEqual(rc, 0)
        self.assertIn("Decision", out)

    def test_theater_path_drives_courtroom(self):
        _FakeCourt.instances.clear()
        with mock.patch.object(theater, "supports_scene", return_value=True), \
                mock.patch.object(theater, "Courtroom", _FakeCourt):
            rc, _, _ = self._run(
                ["replay", str(self.path), "--theater", "--theater-style",
                 "pixel", "--decision", "vote"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(_FakeCourt.instances), 1)
        court = _FakeCourt.instances[0]
        # Seats come from the reviews (ordered, deduped); chair from the outcome.
        self.assertEqual(court.agents, [("claude", "anthropic"), ("codex", "openai")])
        self.assertEqual(court.chair, "codex")
        self.assertEqual(court.kw.get("style"), "pixel")
        self.assertEqual(court.calls[0], ("open",))
        self.assertEqual(court.calls[-1], ("close",))
        self.assertEqual(court.calls[-2][0], "set_vote")

    def test_missing_file_exits_nonzero_with_message(self):
        rc, _, err = self._run(["replay", str(self.path.parent / "gone.json")])
        self.assertEqual(rc, 2)
        self.assertIn("error:", err)
        self.assertIn("cannot read", err)

    def test_bad_json_exits_nonzero_with_message(self):
        bad = self.path.parent / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        rc, _, err = self._run(["replay", str(bad)])
        self.assertEqual(rc, 2)
        self.assertIn("not valid JSON", err)

    def test_report_shape_exits_nonzero_with_pointer(self):
        rep = self.path.parent / "report.json"
        rep.write_text(
            json.dumps({"schema_version": 1, "metadata": {}, "findings": []}),
            encoding="utf-8",
        )
        rc, _, err = self._run(["replay", str(rep)])
        self.assertEqual(rc, 2)
        self.assertIn("cannot be replayed", err)

    def test_unknown_flag_is_an_argparse_error(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(["replay", str(self.path), "--bogus"])


if __name__ == "__main__":
    unittest.main()
