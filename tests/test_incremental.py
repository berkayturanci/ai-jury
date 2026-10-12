"""Tests for incremental review mode (issue #9). Network-free."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury.incremental import (  # noqa: E402
    MODE_FULL,
    MODE_INCREMENTAL,
    compare_range,
    decide_review,
    parse_reviewed_sha,
    range_full_reason,
    reviewed_sha_marker,
    scope_note,
)

SHA_A = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
SHA_B = "0b9a8f7e6d5c4b3a2f1e0d9c8b7a6f5e4d3c2b1a"


class MarkerTest(unittest.TestCase):
    def test_roundtrip(self):
        body = "report text\n\n" + reviewed_sha_marker(SHA_A)
        self.assertEqual(parse_reviewed_sha([body]), SHA_A)

    def test_returns_latest_marker(self):
        bodies = [
            "old\n" + reviewed_sha_marker(SHA_A),
            "new\n" + reviewed_sha_marker(SHA_B),
        ]
        self.assertEqual(parse_reviewed_sha(bodies), SHA_B)

    def test_none_when_absent(self):
        self.assertIsNone(parse_reviewed_sha(["no marker here", ""]))

    def test_short_sha_marker_parsed(self):
        self.assertEqual(parse_reviewed_sha(["x " + reviewed_sha_marker("abc1234")]), "abc1234")


class DecideTest(unittest.TestCase):
    def test_no_prev_is_full(self):
        mode, reason = decide_review(None, SHA_A)
        self.assertEqual(mode, MODE_FULL)
        self.assertIn("no prior", reason)

    def test_no_head_is_full(self):
        self.assertEqual(decide_review(SHA_A, "")[0], MODE_FULL)

    def test_unchanged_head_is_full(self):
        mode, reason = decide_review(SHA_A, SHA_A)
        self.assertEqual(mode, MODE_FULL)
        self.assertIn("unchanged", reason)

    def test_changed_head_is_incremental(self):
        mode, reason = decide_review(SHA_A, SHA_B)
        self.assertEqual(mode, MODE_INCREMENTAL)
        self.assertIn(SHA_A[:7], reason)
        self.assertIn(SHA_B[:7], reason)

    def test_compare_range_format(self):
        self.assertEqual(compare_range(SHA_A, SHA_B), f"{SHA_A}...{SHA_B}")

    def test_scope_note_labels(self):
        self.assertIn("Incremental", scope_note(MODE_INCREMENTAL, "x"))
        self.assertIn("Full", scope_note(MODE_FULL, "y"))


class RangeFullReasonTest(unittest.TestCase):
    """Issue #974: only a plain fast-forward range is the PR's own delta."""

    def test_plain_fast_forward_is_incremental(self):
        meta = {"status": "ahead", "total_commits": 2, "parents": [1, 1]}
        self.assertIsNone(range_full_reason(meta))

    def test_merge_commit_forces_full(self):
        meta = {"status": "ahead", "total_commits": 3, "parents": [1, 1, 2]}
        self.assertIn("merged another branch", range_full_reason(meta))

    def test_diverged_forces_full(self):
        meta = {"status": "diverged", "total_commits": 1, "parents": [1]}
        self.assertIn("not a fast-forward", range_full_reason(meta))

    def test_truncated_commit_list_forces_full(self):
        meta = {"status": "ahead", "total_commits": 300, "parents": [1] * 100}
        self.assertIn("too long", range_full_reason(meta))

    def test_list_at_page_size_forces_full_even_if_total_matches(self):
        meta = {"status": "ahead", "total_commits": 100, "parents": [1] * 100}
        self.assertIn("too long", range_full_reason(meta))

    def test_list_at_api_cap_forces_full_even_if_total_matches(self):
        meta = {"status": "ahead", "total_commits": 250, "parents": [1] * 250}
        self.assertIn("too long", range_full_reason(meta))

    def test_just_under_page_size_is_incremental(self):
        meta = {"status": "ahead", "total_commits": 99, "parents": [1] * 99}
        self.assertIsNone(range_full_reason(meta))

    def test_missing_meta_forces_full(self):
        self.assertIn("unavailable", range_full_reason(None))
        self.assertIn("unavailable", range_full_reason({}))


if __name__ == "__main__":
    unittest.main()
