"""Offline coverage tail: close the small residual gaps across ci, injection,
patches, diffprofile, metadata, github, and report.

Every test is deterministic and network/subprocess-free. The `gh` CLI is never
invoked: github helpers are exercised either through mocked ``_gh`` /
``_gh_with_input`` or as pure arg-builders. Each test notes the exact
line/branch it targets.
"""
from __future__ import annotations

import sys
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import github, report  # noqa: E402
from ai_jury.adapters import AgentResult  # noqa: E402
from ai_jury.ci import evaluate_ci  # noqa: E402
from ai_jury.consensus import FindingGroup  # noqa: E402
from ai_jury.diffprofile import DiffProfile, describe  # noqa: E402
from ai_jury.findings import Finding  # noqa: E402
from ai_jury.injection import InjectionHit, _snippet  # noqa: E402
from ai_jury.metadata import build_run_metadata  # noqa: E402
from ai_jury.orchestrator import JuryOutcome  # noqa: E402
from ai_jury.patches import PatchSuggestion  # noqa: E402


def _ar(agent: str = "claude", *, ok: bool = True, output: str = "out") -> AgentResult:
    return AgentResult(
        agent=agent, vendor="anthropic", ok=ok, output=output,
        duration_s=0.0, error=None if ok else "boom",
    )


# --------------------------------------------------------------------------- ci


class CiGateReasonTests(unittest.TestCase):
    def _group(self, rep, severity="major"):
        return FindingGroup(
            representative=rep, reviewers=["claude"], severity=severity,
            members=[rep] if rep is not None else [], bucket="consensus",
            status="verified",
        )

    def test_blocking_group_with_no_representative(self):
        # ci.py 38->42: rep is None -> loc stays "" -> "(no location)".
        g = self._group(rep=None)
        code, reason = evaluate_ci([g], ["major"], ignore_unverified=True)
        self.assertEqual(code, 1)
        self.assertIn("(no location)", reason)

    def test_blocking_group_representative_without_file(self):
        # ci.py 38->42: rep present but file is empty -> "(no location)".
        rep = Finding(severity="major", file="", line=10, claim="c")
        g = self._group(rep=rep)
        code, reason = evaluate_ci([g], ["major"], ignore_unverified=True)
        self.assertEqual(code, 1)
        self.assertIn("(no location)", reason)

    def test_blocking_group_file_but_no_line(self):
        # ci.py 40->42: file present, line is None -> loc has no ":line" suffix.
        rep = Finding(severity="major", file="src/a.py", line=None, claim="c")
        g = self._group(rep=rep)
        code, reason = evaluate_ci([g], ["major"], ignore_unverified=True)
        self.assertEqual(code, 1)
        self.assertIn("src/a.py", reason)
        self.assertNotIn("src/a.py:", reason)


# -------------------------------------------------------------------- injection


class InjectionTailTests(unittest.TestCase):
    def test_hit_location_without_line(self):
        # injection.py 66->68: line is None -> location() is just the source.
        hit = InjectionHit(kind="override-instructions", source="diff", line=None, snippet="x")
        self.assertEqual(hit.location(), "diff")

    def test_hit_location_with_line(self):
        hit = InjectionHit(kind="override-instructions", source="context", line=7, snippet="x")
        self.assertEqual(hit.location(), "context:7")

    def test_snippet_truncates_long_fragment(self):
        # injection.py line 76: len(frag) > width (default 60) -> truncate + "...".
        text = "a" * 100
        out = _snippet(text, 0, 100)
        self.assertTrue(out.endswith("..."))
        self.assertEqual(len(out), 63)  # 60 chars + "..."

    def test_snippet_short_fragment_not_truncated(self):
        out = _snippet("short", 0, 5)
        self.assertEqual(out, "short")


# ---------------------------------------------------------------------- patches


class PatchLocationTests(unittest.TestCase):
    def test_location_without_line(self):
        # patches.py 32->34: line is None -> location() has no ":line".
        p = PatchSuggestion(file="src/x.py", line=None, severity="major", claim="c", suggested_fix="f")
        self.assertEqual(p.location(), "src/x.py")

    def test_location_with_line(self):
        p = PatchSuggestion(file="src/x.py", line=3, severity="major", claim="c", suggested_fix="f")
        self.assertEqual(p.location(), "src/x.py:3")

    def test_location_no_file_falls_back(self):
        p = PatchSuggestion(file="", line=None, severity="major", claim="c", suggested_fix="f")
        self.assertEqual(p.location(), "?")


# ------------------------------------------------------------------ diffprofile


class DiffProfileDescribeTests(unittest.TestCase):
    def test_describe_docs_or_generated_only(self):
        # diffprofile.py line 101: docs_or_generated_only True -> "docs/generated-only".
        profile = DiffProfile(
            changed_lines=3, file_count=1, paths=["README.md"],
            docs_or_generated_only=True, security_sensitive=False, risk="low",
        )
        out = describe(profile)
        self.assertIn("docs/generated-only", out)
        self.assertIn("risk=low", out)

    def test_describe_security_sensitive(self):
        profile = DiffProfile(
            changed_lines=500, file_count=1, paths=["auth.py"],
            docs_or_generated_only=False, security_sensitive=True, risk="high",
        )
        out = describe(profile)
        self.assertIn("security-sensitive paths", out)
        self.assertNotIn("docs/generated-only", out)


# -------------------------------------------------------------------- metadata


class MetadataSynthesisNoneTests(unittest.TestCase):
    def test_build_metadata_with_synthesis_none(self):
        # metadata.py 74->76: outcome.synthesis is None -> not appended to
        # all_results (the FALSE branch). verify is also None here.
        from ai_jury.config import DEFAULT_CONFIG, _from_dict

        outcome = JuryOutcome(
            reviews=[_ar("claude"), _ar("codex")],
            debate=[],
            synthesis=None,
            chair="claude",
        )
        cfg = _from_dict(DEFAULT_CONFIG)
        meta = build_run_metadata(outcome, cfg)
        # Two review agents, no synthesis/verify contribution.
        self.assertEqual(len(meta["agents"]), 2)
        self.assertEqual(meta["total_wall_clock_s"], 0.0)


# ----------------------------------------------------------------------- github


class GithubArgBranchTests(unittest.TestCase):
    @mock.patch("ai_jury.github._gh", return_value="title\n\nbody")
    def test_pr_context_with_repo(self, mock_gh):
        # github.py line 37: repo truthy -> "--repo" appended.
        out = github.pr_context("123", repo="org/repo")
        self.assertEqual(out, "title\n\nbody")
        args = mock_gh.call_args.args
        self.assertIn("--repo", args)
        self.assertIn("org/repo", args)

    @mock.patch("ai_jury.github._gh", return_value="")
    def test_post_pr_comment_without_repo(self, mock_gh):
        # github.py 47->49: repo falsy -> "--repo" NOT appended; _gh still called.
        github.post_pr_comment("123", "hello", repo=None)
        args = mock_gh.call_args.args
        self.assertNotIn("--repo", args)
        self.assertIn("pr", args)
        self.assertIn("comment", args)

    @mock.patch("ai_jury.github._gh", return_value="a\nb")
    def test_pr_comment_bodies_with_repo(self, mock_gh):
        # github.py line 74: repo truthy -> "--repo" appended.
        out = github.pr_comment_bodies("123", repo="org/repo")
        self.assertEqual(out, ["a", "b"])
        args = mock_gh.call_args.args
        self.assertIn("--repo", args)
        self.assertIn("org/repo", args)

    def test_build_label_args_without_repo(self):
        # github.py 115->117: repo falsy -> arg vector omits "--repo".
        args = github.build_label_args("42", ["bug"], repo=None)
        self.assertNotIn("--repo", args)
        self.assertEqual(args, ["pr", "edit", "--add-label", "bug", "--", "42"])

    def test_render_progress_body_done_with_final_none(self):
        # github.py 317->319: done=True (so "if not done" is False) with
        # final=None -> the live-update footer is skipped, header is "complete".
        body = github.render_progress_body(["stage one"], done=True, final=None)
        self.assertIn(github.PROGRESS_MARKER, body)
        self.assertIn("review complete", body)
        self.assertNotIn("Updating live", body)
        self.assertIn("- stage one", body)


# ----------------------------------------------------------------------- report


class ReportTailTests(unittest.TestCase):
    def test_group_line_no_location_and_status_without_reasoning(self):
        # report.py 45->47: rep.line is None -> loc has no ":line".
        # report.py line 62: status present but no status_reasoning -> the
        # "_verification:" line is rendered WITHOUT a reason.
        rep = Finding(severity="major", file="src/a.py", line=None, claim="boom")
        g = FindingGroup(
            representative=rep, reviewers=["claude"], severity="major",
            members=[rep], bucket="consensus", status="verified", status_reasoning="",
        )
        line = report._group_line(g)
        self.assertIn("src/a.py —", line)
        self.assertNotIn("src/a.py:", line)
        self.assertIn("_verification:_ verified", line)
        self.assertNotIn("verified —", line)

    def test_render_with_explicit_classification(self):
        # report.py 187->189: classification is NOT None -> the in-line
        # classify() call is skipped (the explicit dict is used instead).
        explicit = {"summary": "x", "label": "approve"}
        with mock.patch("ai_jury.classification.classify") as classify, \
             mock.patch("ai_jury.classification.summary_line", return_value="SUMMARY"):
            out = report.render(
                [_ar("claude")], [], _ar("claude"), chair="claude",
                classification=explicit,
            )
        classify.assert_not_called()
        self.assertIn("SUMMARY", out)

    def test_render_context_policy_only_redact(self):
        # report.py 193->195: context_mode is None (skip that line) but
        # redact_secrets set -> the secret-redaction line still renders.
        out = report.render(
            [_ar("claude")], [], _ar("claude"), chair="claude",
            context_mode=None, redact_secrets=True, redaction_count=2,
        )
        self.assertIn("## Context policy", out)
        self.assertIn("secret redaction: on (2 redacted)", out)
        self.assertNotIn("context mode:", out)

    def test_render_context_policy_only_context_mode(self):
        # report.py 195->199: context_mode set but redact_secrets is None ->
        # only the context-mode line renders, redaction line skipped.
        out = report.render(
            [_ar("claude")], [], _ar("claude"), chair="claude",
            context_mode="full-repo", redact_secrets=None,
        )
        self.assertIn("## Context policy", out)
        self.assertIn("context mode: full-repo", out)
        self.assertNotIn("secret redaction:", out)

    def test_render_sections_synthesis_failed(self):
        # report.py line 323: render_sections with synthesis present but not ok
        # -> "Synthesis failed" decision text.
        synthesis = _ar("claude", ok=False)
        sections = report.render_sections(
            [_ar("claude")], [], synthesis, chair="claude",
        )
        decision = sections[-1][1]
        self.assertIn("Synthesis failed", decision)
        self.assertIn("boom", decision)

    def test_render_sections_with_explicit_classification(self):
        # report.py 309->311: classification is NOT None -> in-line classify()
        # skipped in render_sections too.
        explicit = {"summary": "x"}
        with mock.patch("ai_jury.classification.classify") as classify, \
             mock.patch("ai_jury.classification.summary_line", return_value="SUMMARY"):
            sections = report.render_sections(
                [_ar("claude")], [], _ar("claude"), chair="claude",
                classification=explicit,
            )
        classify.assert_not_called()
        self.assertIn("SUMMARY", sections[-1][1])


class CliDetectedAgentsContinue(unittest.TestCase):
    """cli 464->471: non-interactive ``init`` with no --agents/--preset but at
    least one detected agent skips the error and proceeds to build the config."""

    def test_detected_agents_proceeds(self):
        import contextlib
        import io
        import tempfile

        from ai_jury import cli
        from ai_jury.scaffold import KNOWN_AGENTS

        detected = {n: (n == "claude") for n in KNOWN_AGENTS}  # claude reachable
        out_path = Path(tempfile.mkdtemp()) / "jury.toml"
        buf, err = io.StringIO(), io.StringIO()
        with mock.patch("ai_jury.cli._init_available", return_value=detected), \
             mock.patch("ai_jury.cli.sys.stdin") as stdin:
            stdin.isatty.return_value = False
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                code = cli.main(["init", "-o", str(out_path), "--force"])
        self.assertEqual(code, 0)
        self.assertTrue(out_path.exists())


if __name__ == "__main__":
    unittest.main()
