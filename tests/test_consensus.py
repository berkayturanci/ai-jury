"""Tests for deterministic consensus grouping."""

import unittest

from ai_jury.consensus import (
    BUCKET_CONSENSUS,
    BUCKET_MAJORITY,
    BUCKET_SINGLE,
    group_findings,
)
from ai_jury.findings import Finding


def _f(reviewer, severity="major", file="src/a.py", line=10, claim="Null deref"):
    return Finding(severity=severity, file=file, line=line, claim=claim, reviewer=reviewer)


class NormalizePathTests(unittest.TestCase):
    def test_dotfile_path_not_collided_with_sibling(self):
        # Security audit r5/M: lstrip("./") collapsed ".github/x.yml" and
        # "github/x.yml" to the same key, letting a benign sibling's verdict
        # swallow a real finding. Distinct paths must group separately.
        a = _f("claude", severity="critical", file=".github/workflows/deploy.yml")
        b = _f("codex", severity="critical", file="github/workflows/deploy.yml")
        groups = group_findings([a, b], reviewer_count=2)
        self.assertEqual(len(groups), 2)

    def test_parent_and_current_dir_not_collided(self):
        a = _f("claude", file="../auth.py")
        b = _f("codex", file="./auth.py")
        groups = group_findings([a, b], reviewer_count=2)
        self.assertEqual(len(groups), 2)

    def test_leading_dotslash_still_normalized(self):
        # A real "./" prefix is still stripped so genuine duplicates group.
        a = _f("claude", file="./src/a.py")
        b = _f("codex", file="src/a.py")
        groups = group_findings([a, b], reviewer_count=2)
        self.assertEqual(len(groups), 1)


class GroupFindingsTests(unittest.TestCase):
    def test_exact_duplicate_grouped_consensus(self):
        findings = [
            _f("claude"),
            _f("codex"),
        ]
        groups = group_findings(findings, reviewer_count=2)
        self.assertEqual(len(groups), 1)
        g = groups[0]
        self.assertEqual(g.reviewers, ["claude", "codex"])
        self.assertEqual(g.bucket, BUCKET_CONSENSUS)

    def test_nearby_line_grouped(self):
        findings = [
            _f("claude", line=42),
            _f("codex", line=44),
        ]
        groups = group_findings(findings, reviewer_count=2)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].bucket, BUCKET_CONSENSUS)

    def test_far_line_not_grouped(self):
        findings = [
            _f("claude", line=10),
            _f("codex", line=40),
        ]
        groups = group_findings(findings, reviewer_count=2)
        self.assertEqual(len(groups), 2)

    def test_single_reviewer_bucket(self):
        findings = [_f("claude")]
        groups = group_findings(findings, reviewer_count=3)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].bucket, BUCKET_SINGLE)

    def test_majority_bucket(self):
        findings = [_f("claude"), _f("codex")]
        groups = group_findings(findings, reviewer_count=3)
        self.assertEqual(groups[0].bucket, BUCKET_MAJORITY)

    def test_jaccard_similar_claims_grouped(self):
        findings = [
            _f("claude", claim="Possible null dereference on x"),
            _f("codex", claim="Null dereference possible on x here"),
        ]
        groups = group_findings(findings, reviewer_count=2)
        self.assertEqual(len(groups), 1)

    def test_both_none_line_grouped(self):
        findings = [
            _f("claude", line=None),
            _f("codex", line=None),
        ]
        groups = group_findings(findings, reviewer_count=2)
        self.assertEqual(len(groups), 1)

    def test_max_severity_used(self):
        findings = [
            _f("claude", severity="minor"),
            _f("codex", severity="critical"),
        ]
        groups = group_findings(findings, reviewer_count=2)
        self.assertEqual(groups[0].severity, "critical")

    def test_determinism(self):
        a = [
            _f("codex", line=44, claim="Null deref here"),
            _f("claude", line=42, claim="Null deref"),
            _f("agy", severity="minor", file="src/b.py", line=1, claim="Style"),
        ]
        b = list(reversed(a))
        ga = group_findings(a, reviewer_count=3)
        gb = group_findings(b, reviewer_count=3)
        self.assertEqual(
            [
                (g.severity, g.representative.file, g.representative.line, g.reviewers, g.bucket)
                for g in ga
            ],
            [
                (g.severity, g.representative.file, g.representative.line, g.reviewers, g.bucket)
                for g in gb
            ],
        )


if __name__ == "__main__":
    unittest.main()
