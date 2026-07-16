"""Tests for deterministic consensus grouping."""

import unittest

from ai_jury.consensus import (
    BUCKET_CONSENSUS,
    BUCKET_MAJORITY,
    BUCKET_SINGLE,
    demote_local_only_groups,
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

    def test_case_fold_for_grouping_but_not_for_gate_match(self):
        # fold_case=True (grouping/dedup) folds case; fold_case=False (the
        # gate-critical verdict match) is case-exact so Config.py != config.py
        # on a case-sensitive filesystem (audit r6/M).
        from ai_jury.consensus import _normalize_path

        self.assertEqual(_normalize_path("Config.py"), _normalize_path("config.py"))
        self.assertNotEqual(
            _normalize_path("Config.py", fold_case=False),
            _normalize_path("config.py", fold_case=False),
        )

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


class DemoteLocalOnlyGroupsTests(unittest.TestCase):
    """demote_local_only_groups (issue #442)."""

    VENDORS = {"local-model": "local", "claude": "anthropic", "codex": "openai"}

    def test_local_only_critical_demoted_to_minor(self):
        groups = group_findings([_f("local-model", severity="critical")], reviewer_count=1)
        demote_local_only_groups(groups, self.VENDORS)
        self.assertEqual(groups[0].severity, "minor")

    def test_cloud_corroboration_leaves_severity_untouched(self):
        findings = [
            _f("local-model", severity="critical"),
            _f("claude", severity="critical"),
        ]
        groups = group_findings(findings, reviewer_count=2)
        demote_local_only_groups(groups, self.VENDORS)
        self.assertEqual(groups[0].severity, "critical")

    def test_already_low_severity_not_raised(self):
        # A local-only "nit" must not be *escalated* to "minor" — demotion is a
        # ceiling, never a floor.
        groups = group_findings([_f("local-model", severity="nit")], reviewer_count=1)
        demote_local_only_groups(groups, self.VENDORS)
        self.assertEqual(groups[0].severity, "nit")

    def test_unknown_reviewer_not_treated_as_local(self):
        # A reviewer absent from the vendor map (e.g. a stale/renamed agent) must
        # not be silently treated as "local" and demoted.
        groups = group_findings([_f("ghost", severity="major")], reviewer_count=1)
        demote_local_only_groups(groups, self.VENDORS)
        self.assertEqual(groups[0].severity, "major")

    def test_no_reviewers_left_untouched(self):
        # A synthetic/injected finding can carry no reviewer at all.
        f = Finding(severity="critical", file="a.py", claim="x", reviewer="")
        groups = group_findings([f], reviewer_count=1)
        self.assertEqual(groups[0].reviewers, [])
        demote_local_only_groups(groups, self.VENDORS)
        self.assertEqual(groups[0].severity, "critical")

    def test_idempotent(self):
        groups = group_findings([_f("local-model", severity="major")], reviewer_count=1)
        demote_local_only_groups(groups, self.VENDORS)
        demote_local_only_groups(groups, self.VENDORS)
        self.assertEqual(groups[0].severity, "minor")

    def test_demotion_flips_default_ci_gate_from_fail_to_pass(self):
        # Proves the feature's actual purpose, not just that a field mutates:
        # an uncorroborated local-only critical must stop blocking the default
        # CI gate once demoted.
        from ai_jury.ci import evaluate_ci

        groups = group_findings([_f("local-model", severity="critical")], reviewer_count=1)
        self.assertEqual(evaluate_ci(groups, ["critical", "major"], ignore_unverified=False)[0], 1)
        demote_local_only_groups(groups, self.VENDORS)
        self.assertEqual(evaluate_ci(groups, ["critical", "major"], ignore_unverified=False)[0], 0)

    def test_demotion_softens_local_reviewers_own_tallied_vote(self):
        # Proves the vote tally (not just the CI gate) reflects the demotion:
        # the local reviewer's own ballot softens from blocking to non-blocking
        # once its uncorroborated critical is capped at "minor" ("middling").
        from ai_jury.voting import COMMENT, REQUEST_CHANGES, tally_votes

        groups = group_findings([_f("local-model", severity="critical")], reviewer_count=1)
        before = tally_votes(groups, ["local-model"])
        self.assertEqual(before.verdict, REQUEST_CHANGES)
        demote_local_only_groups(groups, self.VENDORS)
        after = tally_votes(groups, ["local-model"])
        self.assertEqual(after.verdict, COMMENT)


if __name__ == "__main__":
    unittest.main()
