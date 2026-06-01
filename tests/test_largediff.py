"""Tests for large-diff filtering and chunking (issue #31).

Offline: pure ``plan_diff`` fixtures plus a mock chunked pipeline run.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_review_council.config import _from_dict  # noqa: E402
from agent_review_council.largediff import (  # noqa: E402
    MODE_CHUNKED,
    MODE_FULL,
    MODE_TOO_LARGE,
    plan_diff,
    split_diff,
)
from agent_review_council.orchestrator import review_diff  # noqa: E402


def _file_segment(path: str, added_lines: int = 1) -> str:
    body = "".join(f"+line {i}\n" for i in range(added_lines))
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n+++ b/{path}\n@@ -0,0 +1,{added_lines} @@\n{body}"
    )


class SplitDiffTest(unittest.TestCase):
    def test_splits_per_file(self):
        diff = _file_segment("src/a.py") + _file_segment("src/b.py")
        files = split_diff(diff)
        self.assertEqual([f.path for f in files], ["src/a.py", "src/b.py"])

    def test_empty_diff(self):
        self.assertEqual(split_diff(""), [])


class FilterTest(unittest.TestCase):
    def test_binary_file_excluded(self):
        diff = (
            "diff --git a/img.png b/img.png\n"
            "Binary files a/img.png and b/img.png differ\n"
        ) + _file_segment("src/a.py")
        plan = plan_diff(diff, max_bytes=1_000_000, chunk=False)
        self.assertEqual(plan.kept_paths, ["src/a.py"])
        self.assertIn(("img.png", "binary"), plan.excluded)

    def test_generated_lockfile_excluded(self):
        diff = _file_segment("package-lock.json", 50) + _file_segment("src/a.py")
        plan = plan_diff(diff, max_bytes=1_000_000, chunk=False)
        self.assertEqual(plan.kept_paths, ["src/a.py"])
        self.assertIn(("package-lock.json", "generated"), plan.excluded)

    def test_vendored_dir_excluded(self):
        diff = _file_segment("vendor/lib/x.go") + _file_segment("src/a.py")
        plan = plan_diff(diff, max_bytes=1_000_000, chunk=False)
        self.assertEqual(plan.kept_paths, ["src/a.py"])

    def test_exclude_generated_off_keeps_them(self):
        diff = _file_segment("yarn.lock", 5)
        plan = plan_diff(diff, max_bytes=1_000_000, chunk=False, exclude_generated=False)
        self.assertEqual(plan.kept_paths, ["yarn.lock"])

    def test_user_exclude_glob(self):
        diff = _file_segment("docs/x.md") + _file_segment("src/a.py")
        plan = plan_diff(diff, max_bytes=1_000_000, chunk=False, exclude=["docs/*"])
        self.assertEqual(plan.kept_paths, ["src/a.py"])

    def test_include_allowlist(self):
        diff = _file_segment("src/a.py") + _file_segment("test/b.py")
        plan = plan_diff(diff, max_bytes=1_000_000, chunk=False, include=["src/*"])
        self.assertEqual(plan.kept_paths, ["src/a.py"])
        self.assertIn(("test/b.py", "not-in-include-filter"), plan.excluded)


class ModeTest(unittest.TestCase):
    def test_small_diff_is_full(self):
        plan = plan_diff(_file_segment("src/a.py"), max_bytes=1_000_000, chunk=False)
        self.assertEqual(plan.mode, MODE_FULL)
        self.assertEqual(len(plan.chunks), 1)

    def test_over_budget_no_chunk_is_too_large(self):
        diff = _file_segment("src/a.py", 200)
        plan = plan_diff(diff, max_bytes=50, chunk=False)
        self.assertEqual(plan.mode, MODE_TOO_LARGE)
        self.assertEqual(plan.chunks, [])

    def test_over_budget_chunks_by_file(self):
        # Three files, each ~> budget/3, force multiple chunks.
        diff = (
            _file_segment("src/a.py", 30)
            + _file_segment("src/b.py", 30)
            + _file_segment("src/c.py", 30)
        )
        plan = plan_diff(diff, max_bytes=10, chunk=True, chunk_max_bytes=400)
        self.assertEqual(plan.mode, MODE_CHUNKED)
        self.assertGreaterEqual(len(plan.chunks), 2)
        # No chunk exceeds the per-chunk budget unless a single file does.
        # Every kept file appears in exactly one chunk.
        joined = "".join(plan.chunks)
        for path in ("src/a.py", "src/b.py", "src/c.py"):
            self.assertEqual(joined.count(f"diff --git a/{path} "), 1)

    def test_oversized_single_file_is_its_own_chunk(self):
        diff = _file_segment("src/big.py", 500)
        plan = plan_diff(diff, max_bytes=10, chunk=True, chunk_max_bytes=10)
        self.assertEqual(plan.mode, MODE_CHUNKED)
        self.assertEqual(len(plan.chunks), 1)


class ChunkedPipelineTest(unittest.TestCase):
    def test_chunked_mock_run_reviews_all_chunks(self):
        cfg = _from_dict(
            {
                "council": {
                    "rounds": 1,
                    "verify": False,
                    "diff": {"max_bytes": 10, "chunk": True, "chunk_max_bytes": 200},
                },
                "agent": [{"name": "claude", "vendor": "anthropic", "command": "claude"}],
            }
        )
        diff = _file_segment("src/a.py", 20) + _file_segment("src/b.py", 20)
        outcome, plan = review_diff(cfg, diff, mock=True)
        self.assertEqual(plan.mode, MODE_CHUNKED)
        self.assertGreaterEqual(len(plan.chunks), 2)
        # The merged outcome is renderable and reflects multiple chunks.
        self.assertTrue(outcome.reviews)
        self.assertIn("chunk", outcome.reviews[0].output)
        self.assertIn("part(s)", outcome.stop_reason)

    def test_too_large_raises(self):
        cfg = _from_dict(
            {
                "council": {"diff": {"max_bytes": 10, "chunk": False}},
                "agent": [{"name": "claude", "vendor": "anthropic", "command": "claude"}],
            }
        )
        diff = _file_segment("src/a.py", 200)
        with self.assertRaises(RuntimeError):
            review_diff(cfg, diff, mock=True)


if __name__ == "__main__":
    unittest.main()
