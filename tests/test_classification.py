"""Tests for the deterministic PR-level classification (issue #7).

These tests are fully offline: they craft ``Finding`` / ``FindingGroup`` objects
directly and assert the pure classification output, the label-string derivation,
and that labeling is OFF by default (the *decision*, not any network call). One
test drives the full mock pipeline and asserts the rendered ``## Classification``
section is deterministic. No GitHub network access is required anywhere.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_review_council import classification as C  # noqa: E402
from agent_review_council.config import (  # noqa: E402
    DEFAULT_CONFIG,
    CouncilConfig,
    _from_dict,
)
from agent_review_council.consensus import FindingGroup, group_findings  # noqa: E402
from agent_review_council.findings import Finding  # noqa: E402
from agent_review_council.orchestrator import run_council  # noqa: E402
from agent_review_council.report import render  # noqa: E402


def _f(severity, claim="a problem", file="src/app.py", line=10, reviewer="claude"):
    return Finding(
        severity=severity,
        file=file,
        claim=claim,
        line=line,
        reviewer=reviewer,
    )


def _config() -> CouncilConfig:
    return _from_dict(DEFAULT_CONFIG)


SAMPLE_DIFF = """diff --git a/src/example.py b/src/example.py
@@ -1,3 +1,6 @@
+def parse(x):
+    return int(x)
"""


class RiskLevelTests(unittest.TestCase):
    def test_empty_findings_is_low_risk(self):
        cls = C.classify(findings=[], groups=[])
        self.assertEqual(cls["risk_level"], "low")

    def test_critical_is_high_risk(self):
        cls = C.classify(findings=[_f("critical")], groups=[])
        self.assertEqual(cls["risk_level"], "high")

    def test_only_minor_is_medium_risk(self):
        cls = C.classify(findings=[_f("minor")], groups=[])
        self.assertEqual(cls["risk_level"], "medium")

    def test_only_nit_info_is_low_risk(self):
        cls = C.classify(findings=[_f("nit"), _f("info")], groups=[])
        self.assertEqual(cls["risk_level"], "low")

    def test_single_reviewer_major_is_medium_risk(self):
        findings = [_f("major", reviewer="claude")]
        groups = group_findings(findings, reviewer_count=3)
        cls = C.classify(findings=findings, groups=groups)
        # Isolated, unconfirmed major -> medium (not high).
        self.assertEqual(cls["risk_level"], "medium")

    def test_consensus_major_is_high_risk(self):
        # Same claim/location raised by all 2 reviewers -> consensus bucket.
        findings = [
            _f("major", claim="off by one bug", reviewer="claude"),
            _f("major", claim="off by one bug", reviewer="codex"),
        ]
        groups = group_findings(findings, reviewer_count=2)
        self.assertTrue(any(g.bucket == "consensus" for g in groups))
        cls = C.classify(findings=findings, groups=groups)
        self.assertEqual(cls["risk_level"], "high")


class ReviewEffortTests(unittest.TestCase):
    def test_empty_is_effort_one(self):
        cls = C.classify(findings=[], groups=[])
        self.assertEqual(cls["review_effort"], 1)

    def test_effort_in_range(self):
        for findings in ([], [_f("nit")], [_f("major")], [_f("critical")] * 12):
            cls = C.classify(findings=findings, groups=[], diff=SAMPLE_DIFF)
            self.assertGreaterEqual(cls["review_effort"], 1)
            self.assertLessEqual(cls["review_effort"], 5)

    def test_single_nit_low_effort(self):
        # 1 finding, only nit, tiny diff -> base 1 (nit is below the minor
        # severity term, so it adds nothing); documented minimum effort.
        cls = C.classify(findings=[_f("nit")], groups=[])
        self.assertEqual(cls["review_effort"], 1)

    def test_single_major_higher_than_single_nit(self):
        nit = C.classify(findings=[_f("nit")], groups=[])["review_effort"]
        major = C.classify(findings=[_f("major")], groups=[])["review_effort"]
        self.assertGreater(major, nit)

    def test_many_severe_findings_max_effort(self):
        # >=8 findings (+2) + critical (+2) + base 1 = 5, clamped to 5.
        cls = C.classify(findings=[_f("critical")] * 10, groups=[])
        self.assertEqual(cls["review_effort"], 5)

    def test_diff_size_raises_effort(self):
        big = "\n".join("+x" for _ in range(500))
        diff = "diff --git a/x b/x\n@@ -0,0 +1,500 @@\n" + big
        small = C.classify(findings=[_f("nit")], groups=[])["review_effort"]
        large = C.classify(findings=[_f("nit")], groups=[], diff=diff)["review_effort"]
        self.assertGreater(large, small)


class SecuritySensitiveTests(unittest.TestCase):
    def test_sql_injection_claim_is_security(self):
        cls = C.classify(findings=[_f("minor", claim="possible SQL injection")], groups=[])
        self.assertTrue(cls["security_sensitive"])

    def test_benign_claim_is_not_security(self):
        cls = C.classify(findings=[_f("minor", claim="rename this variable")], groups=[])
        self.assertFalse(cls["security_sensitive"])

    def test_critical_is_always_security(self):
        cls = C.classify(findings=[_f("critical", claim="rename this variable")], groups=[])
        self.assertTrue(cls["security_sensitive"])

    def test_auth_keyword_word_boundary(self):
        # "author" must NOT match the "auth" keyword.
        self.assertFalse(C.is_security_finding(_f("nit", claim="update the author field")))
        self.assertTrue(C.is_security_finding(_f("nit", claim="broken auth check")))

    def test_various_keywords(self):
        for kw in ("XSS", "CSRF", "path traversal", "hardcoded secret", "RCE"):
            self.assertTrue(
                C.is_security_finding(_f("nit", claim=f"found a {kw} issue")),
                kw,
            )


class NeedsHumanAttentionTests(unittest.TestCase):
    def test_empty_no_human_attention(self):
        cls = C.classify(findings=[], groups=[])
        self.assertFalse(cls["needs_human_attention"])

    def test_high_risk_needs_human(self):
        cls = C.classify(findings=[_f("critical")], groups=[])
        self.assertTrue(cls["needs_human_attention"])

    def test_security_needs_human(self):
        cls = C.classify(findings=[_f("minor", claim="SQL injection")], groups=[])
        self.assertTrue(cls["needs_human_attention"])

    def test_disputed_group_needs_human(self):
        g = FindingGroup(representative=_f("minor"), severity="minor", bucket="disputed")
        cls = C.classify(findings=[_f("minor")], groups=[g])
        self.assertTrue(cls["needs_human_attention"])

    def test_benign_minor_no_human(self):
        findings = [_f("minor", claim="rename a variable")]
        groups = group_findings(findings, reviewer_count=3)
        cls = C.classify(findings=findings, groups=groups)
        self.assertFalse(cls["needs_human_attention"])


class DeterminismTests(unittest.TestCase):
    def test_classify_is_deterministic(self):
        findings = [_f("major", claim="injection bug"), _f("nit")]
        groups = group_findings(findings, reviewer_count=2)
        a = C.classify(findings=findings, groups=groups, diff=SAMPLE_DIFF)
        b = C.classify(findings=findings, groups=groups, diff=SAMPLE_DIFF)
        self.assertEqual(a, b)

    def test_outcome_positional_matches_kwargs(self):
        outcome = run_council(_config(), SAMPLE_DIFF, mock=True)
        via_outcome = C.classify(outcome)
        via_kwargs = C.classify(findings=outcome.findings, groups=outcome.groups)
        self.assertEqual(via_outcome, via_kwargs)


class LabelStringTests(unittest.TestCase):
    def test_basic_labels(self):
        cls = {
            "review_effort": 3,
            "risk_level": "high",
            "security_sensitive": True,
            "needs_human_attention": True,
        }
        labels = C.label_strings(cls)
        self.assertIn("review effort: 3/5", labels)
        self.assertIn("risk: high", labels)
        self.assertIn("possible security issue", labels)
        self.assertIn("needs human attention", labels)

    def test_no_optional_labels_when_false(self):
        cls = {
            "review_effort": 1,
            "risk_level": "low",
            "security_sensitive": False,
            "needs_human_attention": False,
        }
        labels = C.label_strings(cls)
        self.assertEqual(labels, ["review effort: 1/5", "risk: low"])


class LabelingDefaultOffTests(unittest.TestCase):
    """Labeling is OFF by default: assert the DECISION, never the network."""

    def test_label_flag_defaults_false(self):
        from agent_review_council.cli import build_parser

        args = build_parser().parse_args(["--mock", "--diff-file", "-"])
        self.assertFalse(args.label)

    def test_label_flag_can_be_enabled(self):
        from agent_review_council.cli import build_parser

        args = build_parser().parse_args(["--pr", "1", "--label"])
        self.assertTrue(args.label)

    def test_build_label_args_empty_when_no_labels(self):
        from agent_review_council.github import build_label_args

        self.assertEqual(build_label_args("1", []), [])

    def test_build_label_args_constructs_gh_args(self):
        from agent_review_council.github import build_label_args

        args = build_label_args("42", ["risk: high", "review effort: 3/5"], repo="o/r")
        self.assertEqual(
            args,
            [
                "pr", "edit", "42",
                "--add-label", "risk: high",
                "--add-label", "review effort: 3/5",
                "--repo", "o/r",
            ],
        )


class RenderClassificationSectionTests(unittest.TestCase):
    def test_full_mock_renders_classification_deterministically(self):
        cfg = _config()
        outcome = run_council(cfg, SAMPLE_DIFF, mock=True)

        def _render():
            return render(
                outcome.reviews,
                outcome.debate,
                outcome.synthesis,
                chair=outcome.chair,
                findings=outcome.findings,
                warnings=outcome.warnings,
                groups=outcome.groups,
                context_mode=outcome.context_mode,
                redact_secrets=outcome.redact_secrets,
                redaction_count=outcome.redaction_count,
            )

        report = _render()
        self.assertIn("## Classification", report)
        self.assertIn("review effort:", report)
        self.assertIn("risk:", report)
        # Determinism: rendering twice is byte-identical.
        self.assertEqual(report, _render())


if __name__ == "__main__":
    unittest.main()
