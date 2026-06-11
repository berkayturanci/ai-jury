"""Regression tests for the v1.1.1 credibility-cluster fixes (issues #245, #247,
#248, #251) — the bugs the jury found reviewing its own repo. Offline/deterministic.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_jury import classification as clf  # noqa: E402
from ai_jury import voting  # noqa: E402
from ai_jury.ci import evaluate_ci  # noqa: E402
from ai_jury.config import DEFAULT_CONFIG, _from_dict  # noqa: E402
from ai_jury.consensus import FindingGroup  # noqa: E402
from ai_jury.findings import Finding  # noqa: E402
from ai_jury.formats import to_json  # noqa: E402
from ai_jury.orchestrator import run_jury  # noqa: E402

DIFF = (
    "diff --git a/src/example.py b/src/example.py\n"
    "--- a/src/example.py\n+++ b/src/example.py\n"
    "@@ -1,2 +1,2 @@\n-x = 1\n+x = 2\n y = 3\n"
)


def _grp(severity, *, bucket="consensus", status="", reviewers=("claude",)):
    f = Finding(severity=severity, file="src/a.py", line=1, claim="c", reviewer=reviewers[0])
    return FindingGroup(
        representative=f,
        reviewers=list(reviewers),
        severity=severity,
        members=[f],
        bucket=bucket,
        status=status,
    )


class FailOnBlockerAlias(unittest.TestCase):  # issue #245
    def test_blocker_alias_fails_a_critical_group(self):
        groups = [_grp("critical", status="verified")]
        code, reason = evaluate_ci(groups, ["blocker"], ignore_unverified=True)
        self.assertEqual(code, 1)
        self.assertIn("FAIL", reason)

    def test_plain_blocker_still_passes_clean(self):
        groups = [_grp("minor", status="verified")]
        code, _ = evaluate_ci(groups, ["blocker"], ignore_unverified=True)
        self.assertEqual(code, 0)


class RiskLevelHonoursVerifyStatus(unittest.TestCase):  # issue #247
    def test_rejected_major_consensus_is_not_high(self):
        findings = [Finding(severity="major", file="src/a.py", line=1, claim="c")]
        rejected = [_grp("major", bucket="consensus", status="unsupported")]
        self.assertEqual(clf._risk_level(findings, rejected), clf.RISK_MEDIUM)

    def test_verified_major_consensus_is_high(self):
        findings = [Finding(severity="major", file="src/a.py", line=1, claim="c")]
        verified = [_grp("major", bucket="consensus", status="verified")]
        self.assertEqual(clf._risk_level(findings, verified), clf.RISK_HIGH)


class JsonMetadataReflectsDecision(unittest.TestCase):  # issue #248
    def _built(self):
        cfg = _from_dict(DEFAULT_CONFIG)
        return run_jury(cfg, DIFF, mock=True), cfg

    def test_vote_threaded_into_json_metadata(self):
        outcome, cfg = self._built()
        vr = voting.tally_votes(outcome.groups, ["claude"], mode="code")
        doc = json.loads(to_json(outcome, cfg, decision="vote", vote=vr))
        self.assertEqual(doc["metadata"]["decision"], "vote")
        self.assertIsNotNone(doc["metadata"].get("vote"))

    def test_defaults_to_config_decision(self):
        outcome, cfg = self._built()
        doc = json.loads(to_json(outcome, cfg))
        self.assertEqual(doc["metadata"]["decision"], cfg.decision)


class AbstentionNotCounted(unittest.TestCase):  # issue #251
    def test_empty_output_abstains(self):
        self.assertTrue(voting.is_abstention(""))
        self.assertTrue(voting.is_abstention("   \n  "))
        self.assertTrue(voting.is_abstention(None))

    def test_short_refusal_abstains(self):
        self.assertTrue(voting.is_abstention("I can't assist with that request."))
        self.assertTrue(voting.is_abstention("Sorry, I'm unable to help here."))

    def test_contraction_variants_abstain(self):
        # The 3-vendor jury (codex/agy) caught "can't comply" slipping past a
        # "cannot comply"-only list — contraction folding must cover it.
        self.assertTrue(voting.is_abstention("I can't comply with that request."))
        self.assertTrue(voting.is_abstention("I won't be able to help with this."))
        self.assertTrue(voting.is_abstention("I can’t assist with that."))  # smart apostrophe

    def test_substantive_review_does_not_abstain(self):
        review = (
            "After reviewing the diff I found no blocking issues; the change "
            "looks correct and the error handling is adequate. " * 6
        )
        self.assertFalse(voting.is_abstention(review))

    def test_long_text_quoting_refusal_does_not_abstain(self):
        # A real review long enough to be substantive isn't an abstention even if
        # it happens to contain a refusal phrase verbatim.
        review = ("x" * 500) + " the model said i can't assist but that's quoted"
        self.assertFalse(voting.is_abstention(review))


if __name__ == "__main__":
    unittest.main()
