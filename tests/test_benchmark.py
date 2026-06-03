"""Tests for the offline jury-review-quality benchmark (issue #12).

Run with: python -m unittest discover -s tests
No third-party dependencies, no live CLIs, no network.

These tests exercise the scorer math deterministically, validate every shipped
fixture's schema, and confirm the offline runner is deterministic. They never
require live agents (live mode is gated behind JURY_BENCH_LIVE=1).
"""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import benchmark  # noqa: E402
from ai_jury.benchmark import (  # noqa: E402
    BLOCKING_SEVERITIES,
    aggregate,
    discover_fixture_ids,
    finding_matches_expected,
    format_table,
    load_fixtures,
    run_offline,
    score_fixture,
)

EXPECTED_CATEGORIES = {
    "obvious-logic-bug",
    "subtle-boolean-guard",
    "missing-error-handling",
    "false-positive-trap",
    "docs-only",
}


class MatchRuleTest(unittest.TestCase):
    """The documented file/line/severity/keyword match rule."""

    def _finding(self, **kw):
        base = {
            "severity": "major",
            "file": "src/foo.py",
            "line": 10,
            "claim": "off-by-one in loop",
            "evidence": "",
        }
        base.update(kw)
        return base

    def test_exact_match(self):
        entry = {"file": "src/foo.py", "line": 10, "severity": "major", "keywords": ["off-by-one"]}
        self.assertTrue(finding_matches_expected(self._finding(), entry))

    def test_line_within_tolerance(self):
        entry = {"file": "src/foo.py", "line": 12, "keywords": ["off-by-one"]}
        self.assertTrue(finding_matches_expected(self._finding(line=10), entry))

    def test_line_outside_tolerance(self):
        entry = {"file": "src/foo.py", "line": 20, "keywords": ["off-by-one"]}
        self.assertFalse(finding_matches_expected(self._finding(line=10), entry))

    def test_wrong_file(self):
        entry = {"file": "src/bar.py", "line": 10, "keywords": ["off-by-one"]}
        self.assertFalse(finding_matches_expected(self._finding(), entry))

    def test_severity_more_severe_matches(self):
        # Expected major is satisfied by a critical finding.
        entry = {"file": "src/foo.py", "line": 10, "severity": "major", "keywords": ["off-by-one"]}
        self.assertTrue(finding_matches_expected(self._finding(severity="critical"), entry))

    def test_severity_less_severe_fails(self):
        entry = {"file": "src/foo.py", "line": 10, "severity": "major", "keywords": ["off-by-one"]}
        self.assertFalse(finding_matches_expected(self._finding(severity="minor"), entry))

    def test_keyword_case_insensitive(self):
        entry = {"file": "src/foo.py", "line": 10, "keywords": ["OFF-BY-ONE"]}
        self.assertTrue(finding_matches_expected(self._finding(), entry))

    def test_keyword_in_evidence(self):
        entry = {"file": "src/foo.py", "line": 10, "keywords": ["index"]}
        f = self._finding(claim="loop issue", evidence="reads past the index bound")
        self.assertTrue(finding_matches_expected(f, entry))

    def test_no_keyword_no_constraint(self):
        entry = {"file": "src/foo.py", "line": 10}
        self.assertTrue(finding_matches_expected(self._finding(claim="anything"), entry))

    def test_entry_without_file_or_line_keyword_only(self):
        entry = {"keywords": ["off-by-one"]}
        self.assertTrue(finding_matches_expected(self._finding(file="src/other.py", line=999), entry))


class ScoreMathTest(unittest.TestCase):
    """Exact matched/missed/false-positive numbers and pass/fail."""

    def test_all_matched_pass(self):
        findings = [
            {"severity": "major", "file": "a.py", "line": 5, "claim": "off-by-one here", "evidence": ""},
            {"severity": "critical", "file": "b.py", "line": 8, "claim": "null deref", "evidence": ""},
        ]
        expected = {
            "must_match": [
                {"file": "a.py", "line": 5, "severity": "major", "keywords": ["off-by-one"]},
                {"file": "b.py", "line": 8, "severity": "major", "keywords": ["null"]},
            ],
            "must_not_flag": [],
        }
        score = score_fixture(findings, expected)
        self.assertTrue(score.passed)
        self.assertEqual(score.matched, 2)
        self.assertEqual(score.missed, 0)
        self.assertEqual(score.false_positives, 0)
        self.assertEqual(score.expected_count, 2)
        self.assertEqual(score.precision, 1.0)
        self.assertEqual(score.recall, 1.0)

    def test_one_missed_fail(self):
        findings = [
            {"severity": "major", "file": "a.py", "line": 5, "claim": "off-by-one here", "evidence": ""},
        ]
        expected = {
            "must_match": [
                {"file": "a.py", "line": 5, "severity": "major", "keywords": ["off-by-one"]},
                {"file": "b.py", "line": 8, "severity": "major", "keywords": ["null"]},
            ],
            "must_not_flag": [],
        }
        score = score_fixture(findings, expected)
        self.assertFalse(score.passed)
        self.assertEqual(score.matched, 1)
        self.assertEqual(score.missed, 1)
        self.assertEqual(score.false_positives, 0)
        self.assertEqual(score.recall, 0.5)
        self.assertEqual(score.precision, 1.0)

    def test_false_positive_counted(self):
        findings = [
            {"severity": "major", "file": "c.py", "line": 3, "claim": "timing attack vulnerable ==", "evidence": ""},
        ]
        expected = {
            "must_match": [],
            "must_not_flag": [
                {"file": "c.py", "severity": "major", "keywords": ["timing attack"]},
            ],
        }
        score = score_fixture(findings, expected)
        self.assertFalse(score.passed)
        self.assertEqual(score.matched, 0)
        self.assertEqual(score.false_positives, 1)
        self.assertEqual(score.precision, 0.0)
        self.assertEqual(score.recall, 1.0)

    def test_min_findings_not_met(self):
        expected = {"min_findings": 1, "must_match": [], "must_not_flag": []}
        score = score_fixture([], expected)
        self.assertFalse(score.passed)

    def test_max_blocking_exceeded(self):
        findings = [
            {"severity": "major", "file": "x.py", "line": 1, "claim": "boom", "evidence": ""},
        ]
        expected = {"max_blocking": 0, "must_match": [], "must_not_flag": []}
        score = score_fixture(findings, expected)
        self.assertFalse(score.passed)
        self.assertEqual(score.false_positives, 1)

    def test_max_blocking_allows_nonblocking(self):
        findings = [
            {"severity": "info", "file": "x.py", "line": 1, "claim": "looks fine", "evidence": ""},
            {"severity": "nit", "file": "x.py", "line": 2, "claim": "style", "evidence": ""},
        ]
        expected = {"max_blocking": 0, "must_match": [], "must_not_flag": []}
        score = score_fixture(findings, expected)
        self.assertTrue(score.passed)
        self.assertEqual(score.false_positives, 0)

    def test_blocking_severities_are_critical_and_major(self):
        self.assertEqual(BLOCKING_SEVERITIES, frozenset({"critical", "major"}))

    def test_aggregate(self):
        s1 = score_fixture(
            [{"severity": "major", "file": "a.py", "line": 5, "claim": "off-by-one", "evidence": ""}],
            {"must_match": [{"file": "a.py", "line": 5, "severity": "major", "keywords": ["off-by-one"]}], "must_not_flag": []},
        )
        s2 = score_fixture([], {"max_blocking": 0, "must_match": [], "must_not_flag": []})
        summary = aggregate([s1, s2])
        self.assertEqual(summary["fixtures"], 2)
        self.assertEqual(summary["passed"], 2)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["matched"], 1)
        self.assertEqual(summary["expected_count"], 1)
        self.assertEqual(summary["recall"], 1.0)
        self.assertEqual(summary["precision"], 1.0)


class FixtureSchemaTest(unittest.TestCase):
    """Every shipped fixture parses and has a valid schema."""

    def test_all_categories_present(self):
        ids = set(discover_fixture_ids())
        self.assertEqual(ids, EXPECTED_CATEGORIES)

    def test_each_fixture_parses_and_valid(self):
        fixtures = load_fixtures()
        self.assertEqual(len(fixtures), len(EXPECTED_CATEGORIES))
        for fx in fixtures:
            with self.subTest(fixture=fx.id):
                self.assertTrue(fx.diff.strip(), f"{fx.id}: empty diff")
                self.assertTrue(fx.description, f"{fx.id}: missing description")
                self.assertIsInstance(fx.expected, dict)
                self.assertIsInstance(fx.recorded, list)
                # must_match / must_not_flag, when present, are lists of dicts.
                for key in ("must_match", "must_not_flag"):
                    for entry in fx.expected.get(key, []):
                        self.assertIsInstance(entry, dict)
                        sev = entry.get("severity")
                        if sev is not None:
                            self.assertIn(sev, benchmark.SEVERITIES)
                # Recorded findings are dicts with at least a file.
                for rec in fx.recorded:
                    self.assertIsInstance(rec, dict)
                    self.assertIn("file", rec)

    def test_false_positive_trap_encodes_no_blocking(self):
        fixtures = {fx.id: fx for fx in load_fixtures()}
        fp = fixtures["false-positive-trap"]
        self.assertEqual(fp.expected.get("max_blocking"), 0)
        self.assertEqual(fp.expected.get("must_match", []), [])

    def test_docs_only_encodes_no_blocking(self):
        fixtures = {fx.id: fx for fx in load_fixtures()}
        docs = fixtures["docs-only"]
        self.assertEqual(docs.expected.get("max_blocking"), 0)
        self.assertEqual(docs.expected.get("must_match", []), [])


class OfflineRunnerTest(unittest.TestCase):
    """run_offline() returns a result per fixture and is deterministic."""

    def test_run_offline_covers_every_fixture(self):
        scores, summary = run_offline()
        ids = {s.id for s in scores}
        self.assertEqual(ids, EXPECTED_CATEGORIES)
        self.assertEqual(summary["fixtures"], len(EXPECTED_CATEGORIES))

    def test_run_offline_deterministic(self):
        scores1, summary1 = run_offline()
        scores2, summary2 = run_offline()
        self.assertEqual(summary1, summary2)
        self.assertEqual(
            [(s.id, s.passed, s.matched, s.missed, s.false_positives) for s in scores1],
            [(s.id, s.passed, s.matched, s.missed, s.false_positives) for s in scores2],
        )

    def test_recorded_baselines_all_pass(self):
        # The shipped recorded findings are an intentionally-aligned baseline:
        # they should all pass their own expected spec.
        scores, summary = run_offline()
        for s in scores:
            with self.subTest(fixture=s.id):
                self.assertTrue(s.passed, f"{s.id} failed: {s.reasons}")
        self.assertEqual(summary["failed"], 0)

    def test_format_table_renders(self):
        scores, summary = run_offline()
        table = format_table(scores, summary)
        self.assertIn("fixture", table)
        self.assertIn("TOTAL", table)
        for fid in EXPECTED_CATEGORIES:
            self.assertIn(fid, table)


class LiveModeGuardTest(unittest.TestCase):
    """Live mode is off by default and guarded; no live CLIs are invoked."""

    def test_run_live_raises_when_disabled(self):
        self.assertFalse(benchmark.live_enabled())
        with self.assertRaises(RuntimeError):
            benchmark.run_live()

    def test_finding_to_dict_maps_fields(self):
        from ai_jury.findings import Finding

        f = Finding(
            severity="major",
            file="src/x.py",
            claim="boom",
            line=12,
            evidence="here",
        )
        d = benchmark._finding_to_dict(f)
        self.assertEqual(d["severity"], "major")
        self.assertEqual(d["file"], "src/x.py")
        self.assertEqual(d["line"], 12)
        self.assertEqual(d["claim"], "boom")
        self.assertEqual(d["evidence"], "here")



class FormatTableTest(unittest.TestCase):
    """format_table produces the expected fixed-width layout."""

    def test_format_table_exact_output(self):
        from ai_jury.benchmark import FixtureScore, format_table
        scores = [
            FixtureScore(
                id="test-fixture-1",
                passed=True,
                matched=2,
                missed=0,
                false_positives=0,
                expected_count=2,
                precision=1.0,
                recall=1.0,
            ),
            FixtureScore(
                id="test-fixture-2",
                passed=False,
                matched=1,
                missed=1,
                false_positives=2,
                expected_count=2,
                precision=0.3333,
                recall=0.5,
            )
        ]
        summary = {
            "fixtures": 2,
            "passed": 1,
            "matched": 3,
            "missed": 1,
            "false_positives": 2,
            "precision": 0.6,
            "recall": 0.75,
        }
        actual = format_table(scores, summary)
        expected = (
            "fixture                  pass  match  miss  fp   prec  recall\n"
            "-------------------------------------------------------------\n"
            "test-fixture-1           yes   2      0     0    1.00  1.00  \n"
            "test-fixture-2           NO    1      1     2    0.33  0.50  \n"
            "-------------------------------------------------------------\n"
            "TOTAL                    1/2   3      1     2    0.60  0.75  "
        )
        self.assertEqual(actual, expected)


class MainEntryTest(unittest.TestCase):
    """The module entry point runs offline without live CLIs."""

    def test_main_offline_returns_zero(self):
        # JURY_BENCH_LIVE is not set in the unit-test environment.
        self.assertFalse(benchmark.live_enabled())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = benchmark.main()
        self.assertEqual(rc, 0)
        self.assertIn("TOTAL", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
