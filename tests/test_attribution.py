"""The attribution footer on posted verdicts (issue #911).

Every comment `jury` posts ends with one `<sub>` line naming the tool and the seats
that returned a review; `[jury.output] attribution = false` and `--no-attribution`
remove it. All offline: `gh` is mocked and the panel runs via `--mock`.
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

from ai_jury import cli  # noqa: E402
from ai_jury.adapters import AgentResult  # noqa: E402
from ai_jury.config import ConfigError, _from_dict, validate_config  # noqa: E402
from ai_jury.incremental import parse_reviewed_sha  # noqa: E402
from ai_jury.report import ATTRIBUTION_URL, render_attribution  # noqa: E402

DIFF = """diff --git a/app.py b/app.py
index 0000000..1111111 100644
--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
 def f(x):
-    return x
+    return x + 1
"""

SHA = "c" * 40
LEAD = f'<sub>Reviewed by <a href="{ATTRIBUTION_URL}">ai-jury</a> · '

CONFIG = """
[jury]
rounds = 1
chair = "claude"

[jury.output]
attribution = {value}

[[agent]]
name = "claude"
vendor = "anthropic"
command = "claude"

[[agent]]
name = "codex"
vendor = "openai"
command = "codex"
"""


def _result(agent, ok=True, vendor="anthropic"):
    return AgentResult(agent=agent, vendor=vendor, ok=ok, output="x", duration_s=1.0)


def _run(args):
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(args)
    except SystemExit as exc:
        code = exc.code
    return code, err.getvalue()


def _config_file(value: str) -> str:
    path = Path(tempfile.mkdtemp()) / "jury.toml"
    path.write_text(CONFIG.format(value=value), encoding="utf-8")
    return str(path)


@contextlib.contextmanager
def _gh(posted):
    record = lambda *a, **_k: posted.append(a[1])  # noqa: E731
    with (
        mock.patch("ai_jury.cli.pr_diff", return_value=DIFF),
        mock.patch("ai_jury.cli.pr_context", return_value="title"),
        mock.patch("ai_jury.cli.post_pr_comment", side_effect=record),
        mock.patch("ai_jury.cli.post_issue_comment", side_effect=record),
        mock.patch("ai_jury.cli.issue_body", return_value="# Bug\n\nIt breaks."),
        mock.patch("ai_jury.github.pr_head_sha", return_value=SHA),
    ):
        yield


class RenderAttribution(unittest.TestCase):
    def test_names_the_seats_that_returned_a_review_in_seat_order(self):
        line = render_attribution([_result("claude"), _result("codex"), _result("agy")])
        self.assertEqual(f"{LEAD}claude, codex, agy</sub>", line)

    def test_a_failed_seat_is_not_claimed(self):
        line = render_attribution([_result("claude"), _result("codex", ok=False)])
        self.assertEqual(f"{LEAD}claude</sub>", line)

    def test_a_seat_that_reviewed_twice_is_named_once(self):
        # Chunked large-diff reviews carry one result per chunk per seat.
        line = render_attribution([_result("claude"), _result("codex"), _result("claude")])
        self.assertEqual(f"{LEAD}claude, codex</sub>", line)

    def test_no_review_no_footer(self):
        self.assertEqual("", render_attribution([_result("claude", ok=False)]))
        self.assertEqual("", render_attribution([]))
        self.assertEqual("", render_attribution([_result("")]))

    def test_a_name_cannot_break_out_of_the_line(self):
        line = render_attribution([_result("<b>x</b>\n## heading")])
        self.assertEqual(f"{LEAD}&lt;b&gt;x&lt;/b&gt; ## heading</sub>", line)
        self.assertNotIn("\n", line)


class PostedCommentsCarryTheFooter(unittest.TestCase):
    def test_a_posted_summary_ends_with_the_line_then_the_marker(self):
        posted = []
        with _gh(posted):
            code, _ = _run(["--mock", "--pr", "7", "--post", "-q"])
        self.assertEqual(0, code)
        body = posted[0]
        self.assertIn(f"\n\n{LEAD}", body)
        self.assertLess(body.index(LEAD), body.index("arc-reviewed-sha"))
        self.assertTrue(body.rstrip().endswith("-->"), "the SHA marker must stay last")
        # `--incremental` still reads the marker out of a comment carrying the line.
        self.assertEqual(SHA, parse_reviewed_sha([body]))

    def test_no_attribution_flag_removes_the_line(self):
        posted = []
        with _gh(posted):
            code, _ = _run(["--mock", "--pr", "7", "--post", "--no-attribution", "-q"])
        self.assertEqual(0, code)
        self.assertNotIn("Reviewed by", posted[0])
        self.assertEqual(SHA, parse_reviewed_sha(posted))

    def test_config_attribution_false_removes_the_line(self):
        posted = []
        with _gh(posted):
            code, _ = _run(
                ["--mock", "--pr", "7", "--post", "--config", _config_file("false"), "-q"]
            )
        self.assertEqual(0, code)
        self.assertNotIn("Reviewed by", posted[0])

    def test_config_attribution_true_keeps_the_line(self):
        # Counterweight to the `false` case: the config file is read at all.
        posted = []
        with _gh(posted):
            code, _ = _run(
                ["--mock", "--pr", "7", "--post", "--config", _config_file("true"), "-q"]
            )
        self.assertEqual(0, code)
        self.assertIn(LEAD, posted[0])

    def test_only_the_last_phased_comment_carries_it(self):
        posted = []
        with _gh(posted):
            code, _ = _run(["--mock", "--pr", "7", "--post", "--post-mode", "phased", "-q"])
        self.assertEqual(0, code)
        self.assertGreater(len(posted), 1)
        self.assertEqual([False] * (len(posted) - 1) + [True], [LEAD in b for b in posted])
        self.assertLess(posted[-1].index(LEAD), posted[-1].index("arc-reviewed-sha"))
        self.assertEqual(SHA, parse_reviewed_sha(posted))

    def test_an_issue_comment_carries_it(self):
        posted = []
        with _gh(posted):
            code, _ = _run(["--mock", "--issue", "5", "--post", "-q"])
        self.assertEqual(0, code)
        self.assertEqual(1, len(posted))
        self.assertIn(f"\n\n{LEAD}", posted[0])

    def test_the_live_comment_carries_it_on_its_final_body_only(self):
        with (
            _gh([]),
            mock.patch("ai_jury.github.ProgressReporter") as reporter,
        ):
            code, _ = _run(["--mock", "--pr", "7", "--post-progress", "-q"])
        self.assertEqual(0, code)
        final = reporter.return_value.finish.call_args.args[0]
        self.assertIn(f"\n\n{LEAD}", final)
        for call in reporter.return_value.update.call_args_list:
            self.assertNotIn("Reviewed by", call.args[0])


class OutputConfigValidation(unittest.TestCase):
    @staticmethod
    def _with_output(body):
        return {
            "jury": {"rounds": 1, "chair": "a", "output": body},
            "agent": [{"name": "a", "vendor": "anthropic", "command": "claude"}],
        }

    def test_defaults_on(self):
        self.assertTrue(_from_dict(self._with_output({})).output.attribution)

    def test_false_turns_it_off(self):
        self.assertFalse(_from_dict(self._with_output({"attribution": False})).output.attribution)

    def test_a_non_bool_is_a_hard_error(self):
        # The string "false" is truthy; accepting it would post the line turned off.
        with self.assertRaises(ConfigError) as ctx:
            validate_config(self._with_output({"attribution": "false"}))
        self.assertIn("jury.output.attribution must be true or false", str(ctx.exception))

    def test_a_typo_warns_with_the_dotted_path(self):
        w = validate_config(self._with_output({"atribution": False}))
        self.assertEqual(1, len(w), w)
        self.assertIn("unknown key 'jury.output.atribution'", w[0])


if __name__ == "__main__":
    unittest.main()
