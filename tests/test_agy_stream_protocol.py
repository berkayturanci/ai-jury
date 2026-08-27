"""`agy` speaks NDJSON now, and the prompt still must not reach argv.

#635: `AgyAdapter` built `agy --print` and wrote the prompt to stdin. That was
verified against agy 1.0.6. On 1.1.x `--print` takes a value, so the same
invocation dies before the model is reached — `flag needs an argument: -print`
with an empty `extra_args`, and *"took --dangerously-skip-permissions as its
prompt"* with a non-empty one. The agent passed every availability check and
contributed nothing to the panel.

The obvious repair is to put the prompt in argv, and it silently undoes #287 —
the redacted diff would become readable in `ps` to any local user. So the
prompt moved to agy's own stdin channel instead, which keeps that property.

These tests pin the three things that can rot independently: the argv shape,
the frame the prompt travels in, and the parse of what comes back. All three
were measured against agy 1.1.22 before being written down.
"""

from __future__ import annotations

import json
import unittest

from ai_jury.adapters import AgyAdapter
from ai_jury.config import AgentSpec


def adapter(**kwargs) -> AgyAdapter:
    return AgyAdapter(AgentSpec(name="agy", vendor="google", command="agy", **kwargs))


class TheInvocationMatchesWhatAgyAccepts(unittest.TestCase):
    def test_print_is_not_passed_at_all(self):
        """`--print` is what broke. It is not needed: the input format implies it.

        Passing it would reintroduce the arity problem — on 1.1.x it consumes
        the next token as its value, whatever that token happens to be.
        """
        self.assertNotIn("--print", adapter().build_argv("P"))

    def test_the_stream_formats_are_both_present(self):
        """agy refuses `--input-format stream-json` without the matching output."""
        argv = adapter().build_argv("P")
        self.assertIn("--input-format", argv)
        self.assertIn("--output-format", argv)
        self.assertEqual(argv[argv.index("--input-format") + 1], "stream-json")
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")

    def test_a_model_still_reaches_the_command_line(self):
        argv = adapter(model="gemini-x").build_argv("P")
        self.assertEqual(argv[argv.index("--model") + 1], "gemini-x")


class ThePromptNeverReachesArgv(unittest.TestCase):
    """#287, which the obvious fix for #635 would have quietly reverted."""

    def test_the_prompt_is_absent_from_every_argument(self):
        secret = "SENSITIVE-DIFF-CONTENT"
        argv = adapter().build_argv(secret)
        self.assertFalse(
            [arg for arg in argv if secret in arg],
            "the prompt is visible in the process list to any local user",
        )

    def test_the_prompt_travels_in_the_stdin_frame(self):
        secret = "SENSITIVE-DIFF-CONTENT"
        frame = json.loads(adapter()._stdin_for(secret))
        self.assertEqual(frame["message"]["content"], secret)

    def test_the_frame_is_the_shape_agy_accepts(self):
        """Measured: a frame without `event` is rejected with
        `stream input message is missing the "event" field`."""
        frame = json.loads(adapter()._stdin_for("P"))
        self.assertEqual(frame["event"], "user")
        self.assertEqual(frame["message"]["role"], "user")

    def test_the_frame_is_one_ndjson_line(self):
        """One message per line is the protocol; an embedded newline splits it."""
        raw = adapter()._stdin_for("first line\nsecond line")
        self.assertTrue(raw.endswith("\n"))
        self.assertEqual(len(raw.rstrip("\n").splitlines()), 1)


class TheResponseIsReadOutOfTheStream(unittest.TestCase):
    def _stream(self, *events: dict) -> str:
        return "\n".join(json.dumps(e) for e in events)

    def test_the_result_events_response_is_returned(self):
        raw = self._stream(
            {"event": "init", "conversation_id": "x"},
            {"event": "step_update"},
            {"event": "result", "result": {"status": "SUCCESS", "response": "the review"}},
        )
        self.assertEqual(adapter()._text_from_stdout(raw), "the review")

    def test_the_last_result_wins(self):
        raw = self._stream(
            {"event": "result", "result": {"response": "first"}},
            {"event": "result", "result": {"response": "second"}},
        )
        self.assertEqual(adapter()._text_from_stdout(raw), "second")

    def test_a_stream_with_no_result_falls_back_to_the_raw_text(self):
        """A truncated stream must surface as an unreadable review, not silence.

        Returning "" would be counted as an abstention, and #625 exists because
        an abstention read as an approval is the expensive failure. The operator
        needs to see what actually came back.
        """
        raw = self._stream({"event": "init"}, {"event": "step_update"})
        self.assertEqual(adapter()._text_from_stdout(raw), raw)

    def test_unparseable_lines_are_skipped_not_fatal(self):
        raw = "not json at all\n" + json.dumps(
            {"event": "result", "result": {"response": "survived"}}
        )
        self.assertEqual(adapter()._text_from_stdout(raw), "survived")

    def test_a_result_without_a_response_field_falls_back(self):
        """An error result carries no `response`; the raw stream is the evidence."""
        raw = self._stream({"event": "result", "result": {"status": "ERROR", "error": "boom"}})
        self.assertEqual(adapter()._text_from_stdout(raw), raw)

    def test_a_non_object_event_does_not_crash(self):
        raw = "[1, 2, 3]\n" + json.dumps({"event": "result", "result": {"response": "ok"}})
        self.assertEqual(adapter()._text_from_stdout(raw), "ok")

    def test_plain_text_output_is_returned_unchanged(self):
        """Every other adapter's stdout goes through the same seam untouched."""
        self.assertEqual(adapter()._text_from_stdout("just prose"), "just prose")
