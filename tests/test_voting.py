"""Panel voting verdict mode (issue #220).

Covers the pure voting.tally_votes logic, the CLI --decision / [jury] decision
wiring, and the invariant that the decision mode is rendering-only (does not
change config_hash). Offline and deterministic.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import cli, voting  # noqa: E402
from ai_jury.config import JuryConfig, config_hash, load_config  # noqa: E402
from ai_jury.consensus import FindingGroup  # noqa: E402
from ai_jury.findings import Finding  # noqa: E402

DIFF = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"


def _grp(severity, reviewers, status=""):
    f = Finding(severity=severity, file="a.py", claim="c", reviewer=reviewers[0])
    return FindingGroup(
        representative=f, reviewers=list(reviewers), severity=severity, status=status
    )


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


class TallyVotesTests(unittest.TestCase):
    def test_all_major_request_changes(self):
        groups = [_grp("major", ["claude", "codex", "agy"])]
        r = voting.tally_votes(groups, ["claude", "codex", "agy"])
        self.assertEqual(r.verdict, voting.REQUEST_CHANGES)
        self.assertEqual(r.tally[voting.REQUEST_CHANGES], 3)

    def test_minor_only_comment(self):
        groups = [_grp("minor", ["claude"]), _grp("nit", ["codex"])]
        r = voting.tally_votes(groups, ["claude", "codex"])
        self.assertEqual(r.verdict, voting.COMMENT)

    def test_no_findings_approve(self):
        r = voting.tally_votes([], ["claude", "codex"])
        self.assertEqual(r.verdict, voting.APPROVE)
        self.assertEqual(r.tally[voting.APPROVE], 2)

    def test_majority_rules(self):
        # claude raises major (REQUEST CHANGES); codex+agy clean (APPROVE) -> majority APPROVE.
        groups = [_grp("major", ["claude"])]
        r = voting.tally_votes(groups, ["claude", "codex", "agy"])
        self.assertEqual(r.verdict, voting.APPROVE)
        self.assertEqual(r.tally[voting.REQUEST_CHANGES], 1)
        self.assertEqual(r.tally[voting.APPROVE], 2)

    def test_tie_breaks_conservative(self):
        # 1 REQUEST CHANGES vs 1 APPROVE -> tie resolves to the stricter stance.
        groups = [_grp("critical", ["claude"])]
        r = voting.tally_votes(groups, ["claude", "codex"])
        self.assertEqual(r.verdict, voting.REQUEST_CHANGES)

    def test_unsupported_findings_excluded(self):
        # A finding the verifier marked unsupported does not count toward the vote.
        groups = [_grp("critical", ["claude"], status="unsupported")]
        r = voting.tally_votes(groups, ["claude"])
        self.assertEqual(r.verdict, voting.APPROVE)
        self.assertIn("no supported findings", r.ballots[0].reason)

    def test_worst_severity_kept_across_groups(self):
        # A reviewer in two groups keeps the worst (major), not the later milder one.
        groups = [_grp("major", ["claude"]), _grp("minor", ["claude"])]
        r = voting.tally_votes(groups, ["claude"])
        self.assertEqual(r.verdict, voting.REQUEST_CHANGES)
        self.assertIn("major", r.ballots[0].reason)

    def test_info_severity_votes_approve(self):
        # An info-severity finding maps to APPROVE via the severity→vote path.
        r = voting.tally_votes([_grp("info", ["claude"])], ["claude"])
        self.assertEqual(r.verdict, voting.APPROVE)
        self.assertIn("info", r.ballots[0].reason)

    def test_no_quorum_when_no_reviewers(self):
        r = voting.tally_votes([_grp("major", ["x"])], [])
        self.assertEqual(r.verdict, voting.NO_QUORUM)
        self.assertEqual(r.ballots, [])


class VoteRenderOrderTests(unittest.TestCase):
    def test_tally_renders_strictest_first(self):
        # The tally headline lists the strictest stance first (caught as a
        # regression by the dogfood jury on #230).
        from ai_jury import report

        vote = voting.tally_votes([_grp("major", ["a"]), _grp("minor", ["b"])], ["a", "b"])
        line = next(ln for ln in report._vote_block(vote) if ln.startswith("**"))
        self.assertLess(line.index("request changes"), line.index("approve"))

    def test_issue_tally_renders_strictest_first(self):
        from ai_jury import report

        vote = voting.tally_votes([_grp("major", ["a"])], ["a"], mode="issue")
        line = next(ln for ln in report._vote_block(vote) if ln.startswith("**"))
        self.assertLess(line.index("needs-info"), line.index("ready"))


class CliDecisionTests(unittest.TestCase):
    def test_default_is_chair(self):
        code, out, _ = _run(["--mock", "--diff-file", "-", "-q", "--seed", "1"])
        self.assertEqual(code, 0)
        self.assertNotIn("panel vote", out)
        self.assertIn("Chair verdict", out)

    def test_decision_vote_renders_tally_and_relabels_chair(self):
        code, out, _ = _run(
            ["--mock", "--diff-file", "-", "-q", "--seed", "1", "--decision", "vote"]
        )
        self.assertEqual(code, 0)
        self.assertIn("## Verdict — panel vote", out)
        self.assertIn("Chair's reasoning", out)  # synthesis relabelled
        self.assertNotIn("## Chair verdict", out)

    def test_decision_vote_in_metadata_json(self):
        d = Path(tempfile.mkdtemp())
        mp = d / "m.json"
        code, _, _ = _run(
            [
                "--mock",
                "--diff-file",
                "-",
                "-q",
                "--seed",
                "1",
                "--decision",
                "vote",
                "--metadata-json",
                str(mp),
            ]
        )
        self.assertEqual(code, 0)
        meta = json.loads(mp.read_text(encoding="utf-8"))
        self.assertEqual(meta["decision"], "vote")
        self.assertIn("verdict", meta["vote"])
        self.assertIn("ballots", meta["vote"])

    def test_config_decision_vote(self):
        d = Path(tempfile.mkdtemp())
        cfg = d / "jury.toml"
        cfg.write_text(
            '[jury]\nrounds = 1\nchair = "claude"\ndecision = "vote"\n'
            '\n[[agent]]\nname = "claude"\nvendor = "anthropic"\ncommand = "x"\n'
        )
        code, out, _ = _run(
            ["--mock", "--diff-file", "-", "-q", "--seed", "1", "--config", str(cfg)]
        )
        self.assertEqual(code, 0)
        self.assertIn("panel vote", out)

    def test_vote_in_verbose_transcript(self):
        code, out, _ = _run(
            ["--mock", "--diff-file", "-", "-q", "--seed", "1", "--decision", "vote", "--verbose"]
        )
        self.assertEqual(code, 0)
        self.assertIn("## Verdict — panel vote", out)

    def test_flag_overrides_config(self):
        d = Path(tempfile.mkdtemp())
        cfg = d / "jury.toml"
        cfg.write_text(
            '[jury]\nrounds = 1\nchair = "claude"\ndecision = "vote"\n'
            '\n[[agent]]\nname = "claude"\nvendor = "anthropic"\ncommand = "x"\n'
        )
        code, out, _ = _run(
            [
                "--mock",
                "--diff-file",
                "-",
                "-q",
                "--seed",
                "1",
                "--config",
                str(cfg),
                "--decision",
                "chair",
            ]
        )
        self.assertEqual(code, 0)
        self.assertNotIn("panel vote", out)


class PhasedPostingVoteTests(unittest.TestCase):
    def test_render_sections_includes_vote(self):
        from ai_jury import report
        from ai_jury.adapters import AgentResult

        rv = [AgentResult(agent="claude", vendor="anthropic", ok=True, output="x", duration_s=0.0)]
        vote = voting.tally_votes([_grp("major", ["claude"])], ["claude"])
        sections = report.render_sections(
            rv, [], None, chair="claude", groups=[_grp("major", ["claude"])], vote=vote
        )
        decision = sections[-1][1]  # the Decision section body
        self.assertIn("Verdict — panel vote", decision)
        self.assertIn("REQUEST CHANGES", decision)

    def test_render_sections_without_vote_unchanged(self):
        from ai_jury import report
        from ai_jury.adapters import AgentResult

        rv = [AgentResult(agent="claude", vendor="anthropic", ok=True, output="x", duration_s=0.0)]
        sections = report.render_sections(rv, [], None, chair="claude")
        self.assertNotIn("panel vote", sections[-1][1])


class DecisionConfigInvariantTests(unittest.TestCase):
    def test_decision_parsed(self):
        d = Path(tempfile.mkdtemp())
        cfg = d / "jury.toml"
        cfg.write_text(
            '[jury]\nrounds = 1\nchair = "a"\ndecision = "vote"\n'
            '\n[[agent]]\nname = "a"\nvendor = "anthropic"\ncommand = "x"\n'
        )
        self.assertEqual(load_config(str(cfg)).decision, "vote")

    def test_rendering_only_not_in_config_hash(self):
        self.assertEqual(
            config_hash(JuryConfig(decision="chair")),
            config_hash(JuryConfig(decision="vote")),
        )

    def test_validate_rejects_bad_decision(self):
        from ai_jury.config import ConfigError, validate_config

        base = {
            "jury": {"rounds": 1, "chair": "a"},
            "agent": [{"name": "a", "vendor": "anthropic", "command": "x"}],
        }
        bad = {**base, "jury": {**base["jury"], "decision": "bogus"}}
        with self.assertRaises(ConfigError) as ctx:
            validate_config(bad)
        self.assertIn("decision", str(ctx.exception))
        # A valid decision does not raise.
        ok = {**base, "jury": {**base["jury"], "decision": "vote"}}
        validate_config(ok)


if __name__ == "__main__":
    unittest.main()
