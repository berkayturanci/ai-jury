"""Offline coverage tests for four pure-ish modules.

Targets specific uncovered lines/branches in consensus, largediff,
classification, and policy. Deterministic, no network, no mocking needed.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import classification, consensus, largediff, policy  # noqa: E402
from ai_jury.consensus import FindingGroup, group_findings  # noqa: E402
from ai_jury.findings import Finding  # noqa: E402
from ai_jury.policy import PolicyError, ReviewPolicy, SeverityOverride  # noqa: E402


def _f(reviewer="claude", severity="major", file="src/a.py", line=10, claim="Null deref"):
    return Finding(severity=severity, file=file, line=line, claim=claim, reviewer=reviewer)


class ConsensusTests(unittest.TestCase):
    def test_normalize_path_empty_returns_empty(self):
        # Line 44: falsy path short-circuits to "".
        self.assertEqual(consensus._normalize_path(""), "")
        self.assertEqual(consensus._normalize_path(None), "")

    def test_jaccard_both_empty_is_one(self):
        # Line 60: two empty token sets are identical.
        self.assertEqual(consensus._jaccard(set(), set()), 1.0)

    def test_jaccard_one_empty_is_zero(self):
        # Line 62: exactly one side empty -> 0.0.
        self.assertEqual(consensus._jaccard({"a"}, set()), 0.0)
        self.assertEqual(consensus._jaccard(set(), {"b"}), 0.0)

    def test_jaccard_partial_overlap(self):
        self.assertAlmostEqual(consensus._jaccard({"a", "b"}, {"b", "c"}), 1 / 3)

    def test_same_location_one_line_none_not_grouped(self):
        # Line 74: one finding has a line, the other does not -> not the same
        # location, so two distinct groups even though path + claim match.
        findings = [
            _f("claude", line=10),
            _f("codex", line=None),
        ]
        groups = group_findings(findings, reviewer_count=2)
        self.assertEqual(len(groups), 2)

    def test_same_location_both_line_none_grouped(self):
        # Sanity: both None -> same location path (line 72) groups them.
        findings = [
            _f("claude", line=None),
            _f("codex", line=None),
        ]
        groups = group_findings(findings, reviewer_count=2)
        self.assertEqual(len(groups), 1)

    def test_empty_input_returns_empty(self):
        self.assertEqual(group_findings([], reviewer_count=0), [])


class LargeDiffTests(unittest.TestCase):
    def test_strip_ab_no_prefix(self):
        # Line 93: path without an a/ or b/ prefix is returned unchanged.
        self.assertEqual(largediff._strip_ab("plain/path.py"), "plain/path.py")

    def test_strip_ab_with_prefix(self):
        self.assertEqual(largediff._strip_ab("b/src/x.py"), "src/x.py")

    def test_split_diff_preamble_sets_empty_path(self):
        # Line 125: leading content before any "diff --git" header keeps
        # cur_path None until set to "" by the preamble branch.
        diff = "some preamble line\nanother preamble line\n"
        files = largediff.split_diff(diff)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].path, "")
        self.assertEqual(files[0].text, diff)

    def test_split_diff_empty_returns_empty(self):
        self.assertEqual(largediff.split_diff(""), [])

    def test_is_binary_git_binary_patch_marker(self):
        # Line 142: standalone "GIT binary patch" line.
        text = "diff --git a/x.bin b/x.bin\nGIT binary patch\nliteral 0\n"
        self.assertTrue(largediff._is_binary(text))

    def test_is_binary_files_differ_marker(self):
        text = "diff --git a/x.png b/x.png\nBinary files a/x.png and b/x.png differ\n"
        self.assertTrue(largediff._is_binary(text))

    def test_is_binary_false_on_source(self):
        text = "diff --git a/x.py b/x.py\n+print('GIT binary patch mentioned')\n"
        self.assertFalse(largediff._is_binary(text))

    def test_chunk_files_empty_input(self):
        # Branch 182->184: loop body never runs, `current` stays empty, so the
        # trailing `if current` is False -> no chunks.
        self.assertEqual(largediff._chunk_files([], chunk_max_bytes=100), [])

    def test_chunk_files_single_file_emits_trailing_chunk(self):
        # The 182->183 side: one leftover file flushed after the loop.
        f = largediff.DiffFile(path="a.py", text="hello")
        self.assertEqual(largediff._chunk_files([f], chunk_max_bytes=100), ["hello"])

    def test_plan_diff_exclude_filter_reason(self):
        # Lines 218-219: a file that passes include + isn't generated but
        # matches an explicit `exclude` glob -> EXCLUDE_FILTER.
        diff = (
            "diff --git a/src/keep.py b/src/keep.py\n+keep\n"
            "diff --git a/src/skip_me.py b/src/skip_me.py\n+skip\n"
        )
        plan = largediff.plan_diff(
            diff,
            max_bytes=10_000,
            chunk=False,
            exclude_generated=False,
            exclude=("*skip_me.py",),
        )
        self.assertIn(("src/skip_me.py", largediff.EXCLUDE_FILTER), plan.excluded)
        self.assertEqual(plan.kept_paths, ["src/keep.py"])

    def test_plan_diff_chunked_mode(self):
        f1 = "diff --git a/a.py b/a.py\n" + "+" + ("x" * 50) + "\n"
        f2 = "diff --git a/b.py b/b.py\n" + "+" + ("y" * 50) + "\n"
        plan = largediff.plan_diff(f1 + f2, max_bytes=40, chunk=True, chunk_max_bytes=70)
        self.assertEqual(plan.mode, largediff.MODE_CHUNKED)
        self.assertGreaterEqual(len(plan.chunks), 2)


class ClassificationTests(unittest.TestCase):
    def test_resolved_findings_empty_when_all_none(self):
        # Line 97: no findings arg, outcome None -> [].
        self.assertEqual(classification._resolved_findings(None, None), [])

    def test_resolved_findings_outcome_none_findings(self):
        class Outcome:
            findings = None

        self.assertEqual(classification._resolved_findings(Outcome(), None), [])

    def test_resolved_groups_empty_when_all_none(self):
        # Line 105: no groups arg, outcome None -> [].
        self.assertEqual(classification._resolved_groups(None, None), [])

    def test_resolved_groups_outcome_none_groups(self):
        class Outcome:
            groups = None

        self.assertEqual(classification._resolved_groups(Outcome(), None), [])

    def test_review_effort_medium_diff_size_bump(self):
        # Line 219: 80 < lines_changed <= 400 adds +1.
        diff = "diff --git a/x.py b/x.py\n" + "".join(f"+line{i}\n" for i in range(100))
        result = classification.classify(findings=[], diff=diff)
        # base 1 + diff bump 1 (no findings, no severity term) = 2.
        self.assertEqual(result["review_effort"], 2)

    def test_review_effort_large_diff_bump(self):
        diff = "diff --git a/x.py b/x.py\n" + "".join(f"+line{i}\n" for i in range(500))
        result = classification.classify(findings=[], diff=diff)
        self.assertEqual(result["review_effort"], 3)  # base 1 + large bump 2

    def test_has_unresolved_groups_via_status(self):
        # Line 230: a group whose status is needs_human_decision (bucket not
        # in the unresolved set) still flags unresolved.
        g = FindingGroup(
            representative=_f(),
            bucket="single_reviewer",
            status="needs_human_decision",
        )
        self.assertTrue(classification._has_unresolved_groups([g]))

    def test_has_unresolved_groups_via_bucket(self):
        g = FindingGroup(representative=_f(), bucket="disputed")
        self.assertTrue(classification._has_unresolved_groups([g]))

    def test_has_unresolved_groups_false(self):
        g = FindingGroup(representative=_f(), bucket="consensus", status="confirmed")
        self.assertFalse(classification._has_unresolved_groups([g]))

    def test_classify_uses_resolved_when_outcome_only(self):
        groups = [
            FindingGroup(
                representative=_f(), bucket="single_reviewer", status="needs_human_decision"
            )
        ]
        result = classification.classify(findings=[_f(severity="info")], groups=groups)
        self.assertTrue(result["needs_human_attention"])


class PolicyTests(unittest.TestCase):
    def _write(self, text):
        import shutil
        import tempfile

        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        p = Path(d) / "policy.toml"
        p.write_text(text, encoding="utf-8")
        return p

    def test_load_policy_read_oserror_is_policyerror(self):
        # Line 91: open() raises OSError (directory used as a file).
        import shutil
        import tempfile

        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        with self.assertRaises(PolicyError) as ctx:
            policy.load_policy(d)
        self.assertIn("could not read policy file", str(ctx.exception))

    def test_checklist_not_string_raises(self):
        # Line 124: checklist must be a string.
        p = self._write("checklist = 123\n")
        with self.assertRaises(PolicyError) as ctx:
            policy.load_policy(p)
        self.assertIn("'checklist' must be a string", str(ctx.exception))

    def test_severity_overrides_not_list_raises(self):
        # Line 128: severity_overrides must be a list of tables.
        p = self._write('severity_overrides = "nope"\n')
        with self.assertRaises(PolicyError) as ctx:
            policy.load_policy(p)
        self.assertIn("'severity_overrides' must be a list of tables", str(ctx.exception))

    def test_severity_override_entry_not_table_raises(self):
        # Line 132: each override must be a table (here it's a bare string).
        p = self._write('severity_overrides = ["nope"]\n')
        with self.assertRaises(PolicyError) as ctx:
            policy.load_policy(p)
        self.assertIn("each severity override must be a table", str(ctx.exception))

    def test_valid_policy_round_trip(self):
        p = self._write(
            'high_risk_paths = ["src/auth/**"]\n'
            'checklist = "do the thing"\n'
            "[[severity_overrides]]\n"
            'glob = "src/x.py"\n'
            'severity = "critical"\n'
        )
        loaded = policy.load_policy(p)
        self.assertIsInstance(loaded, ReviewPolicy)
        self.assertEqual(loaded.high_risk_paths, ["src/auth/**"])
        self.assertEqual(loaded.severity_overrides, [SeverityOverride("src/x.py", "critical")])
        self.assertFalse(loaded.is_empty())


if __name__ == "__main__":
    unittest.main()
