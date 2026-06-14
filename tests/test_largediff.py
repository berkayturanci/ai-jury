"""Tests for large-diff filtering and chunking (issue #31).

Offline: pure ``plan_diff`` fixtures plus a mock chunked pipeline run.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury.config import _from_dict  # noqa: E402
from ai_jury.largediff import (  # noqa: E402
    MODE_CHUNKED,
    MODE_FULL,
    MODE_TOO_LARGE,
    plan_diff,
    split_diff,
)
from ai_jury.orchestrator import review_diff  # noqa: E402


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

    def test_space_in_path_recovered_from_marker(self):
        # The `diff --git` header is ambiguous for space-containing names; the
        # `+++ b/<p>` marker carries the full path (audit 2026-06-13/L-4).
        seg = _file_segment("src/evil with space.py")
        files = split_diff(seg)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].path, "src/evil with space.py")

    def test_space_path_not_hidden_from_include_filter(self):
        # A space-named file must not slip past an include allow-list by being
        # truncated to its first token (audit 2026-06-13/N-3).
        diff = _file_segment("src/evil with space.py")
        plan = plan_diff(diff, max_bytes=1_000_000, chunk=False, include=["src/*"])
        self.assertEqual(plan.kept_paths, ["src/evil with space.py"])

    def test_crlf_marker_path_has_no_trailing_cr(self):
        # On Windows a diff read in binary keeps CRLF; the +++/--- path must not
        # retain a trailing '\r' or it fails glob/include matching (regression).
        seg = _file_segment("src/a.py").replace("\n", "\r\n")
        files = split_diff(seg)
        self.assertEqual(files[0].path, "src/a.py")

    def test_crlf_path_matches_include_filter(self):
        diff = _file_segment("src/a.py").replace("\n", "\r\n")
        plan = plan_diff(diff, max_bytes=1_000_000, chunk=False, include=["*.py"])
        self.assertEqual(plan.kept_paths, ["src/a.py"])

    def test_markerless_rename_path_from_extended_header(self):
        # A pure rename (no +++/--- lines) with a space in the name must recover
        # the full path from the `rename to` header, not truncate at the space
        # (audit 2026-06-13 r3; #343 fix was incomplete for marker-less segments).
        seg = (
            "diff --git a/old.txt b/src/auth handler.py\n"
            "similarity index 100%\n"
            "rename from old.txt\n"
            "rename to src/auth handler.py\n"
        )
        files = split_diff(seg)
        self.assertEqual(files[0].path, "src/auth handler.py")

    def test_markerless_rename_not_hidden_from_include(self):
        seg = (
            "diff --git a/old.py b/src/evil thing.py\n"
            "rename from old.py\n"
            "rename to src/evil thing.py\n"
        )
        plan = plan_diff(seg, max_bytes=1_000_000, chunk=False, include=["src/*"])
        self.assertEqual(plan.kept_paths, ["src/evil thing.py"])

    def test_space_in_header_recovered_without_markers(self):
        # Header path with a space, no marker lines: split on " b/" not " ".
        seg = "diff --git a/a b.py b/a b.py\nold mode 100644\nnew mode 100755\n"
        files = split_diff(seg)
        self.assertEqual(files[0].path, "a b.py")

    def test_modechange_quoted_spaced_path_recovered(self):
        # Quoted symmetric header (git C-quotes spaced/special paths) with no
        # +++/--- markers must still recover the full path (audit r5/L).
        seg = (
            'diff --git "a/evil file.py" "b/evil file.py"\n'
            "old mode 100644\nnew mode 100755\n"
        )
        files = split_diff(seg)
        self.assertEqual(files[0].path, "evil file.py")

    def test_modechange_path_with_b_slash_in_name_recovered(self):
        # A mode-change-only segment (no +++/--- or rename marker) whose path
        # contains the literal " b/" must not be truncated/hidden (audit r4/L).
        seg = (
            "diff --git a/weird b/secret.py b/weird b/secret.py\n"
            "old mode 100644\nnew mode 100755\n"
        )
        files = split_diff(seg)
        self.assertEqual(files[0].path, "weird b/secret.py")

    def test_quoted_unicode_path_unquoted(self):
        seg = _file_segment("src/a.py").replace(
            "+++ b/src/a.py", '+++ "b/src/\\303\\251.py"'
        )
        files = split_diff(seg)
        self.assertEqual(files[0].path, "src/é.py")


class FilterTest(unittest.TestCase):
    def test_binary_file_excluded(self):
        diff = (
            "diff --git a/img.png b/img.png\nBinary files a/img.png and b/img.png differ\n"
        ) + _file_segment("src/a.py")
        plan = plan_diff(diff, max_bytes=1_000_000, chunk=False)
        self.assertEqual(plan.kept_paths, ["src/a.py"])
        self.assertIn(("img.png", "binary"), plan.excluded)

    def test_source_mentioning_binary_markers_is_not_binary(self):
        # Regression: a source file whose *content* mentions "Binary files" or
        # "GIT binary patch" (e.g. a binary detector) must NOT be misdetected as
        # binary — those strings appear as added (+) content lines, not as the
        # diff's unprefixed binary-marker header.
        diff = (
            "diff --git a/src/detect.py b/src/detect.py\n"
            "--- a/src/detect.py\n+++ b/src/detect.py\n"
            "@@ -0,0 +1,2 @@\n"
            '+    return "Binary files " in text or "GIT binary patch" in text\n'
            '+# handles the "Binary files a/x and b/x differ" marker\n'
        )
        plan = plan_diff(diff, max_bytes=1_000_000, chunk=False)
        self.assertEqual(plan.kept_paths, ["src/detect.py"])
        self.assertEqual(plan.excluded, [])

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
                "jury": {
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

    def test_context_redactions_counted_once_across_chunks(self):
        # #249: the SAME expanded context is reviewed against every chunk, so its
        # secrets must be counted ONCE — not once per chunk (which summed in
        # _merge_chunk_outcomes and inflated redaction_count).
        cfg = _from_dict(
            {
                "jury": {
                    "rounds": 1,
                    "verify": False,
                    "context": {"mode": "expanded", "redact_secrets": True},
                    "diff": {"max_bytes": 10, "chunk": True, "chunk_max_bytes": 200},
                },
                "agent": [{"name": "claude", "vendor": "anthropic", "command": "claude"}],
            }
        )
        # The diff carries no secrets, so every redaction comes from the context.
        diff = _file_segment("src/a.py", 20) + _file_segment("src/b.py", 20)
        context = "deploy key AKIAABCDEFGHIJKLMNOP used here"
        outcome, plan = review_diff(cfg, diff, context=context, mock=True)
        self.assertEqual(plan.mode, MODE_CHUNKED)
        self.assertGreaterEqual(len(plan.chunks), 2)
        # One secret in the context → counted once, independent of chunk count.
        self.assertEqual(outcome.redaction_count, 1)

    def test_context_redaction_count_preserved_in_full_mode(self):
        # The pre-redact-once path must not LOSE the context count for non-chunked
        # reviews (full mode pre-redacts too, then adds the one-time count back).
        cfg = _from_dict(
            {
                "jury": {
                    "rounds": 1,
                    "verify": False,
                    "context": {"mode": "expanded", "redact_secrets": True},
                },
                "agent": [{"name": "claude", "vendor": "anthropic", "command": "claude"}],
            }
        )
        outcome, plan = review_diff(
            cfg,
            _file_segment("src/a.py", 2),
            context="token AKIAABCDEFGHIJKLMNOP here",
            mock=True,
        )
        self.assertEqual(plan.mode, MODE_FULL)
        self.assertEqual(outcome.redaction_count, 1)

    def test_total_timeout_budget_shared_across_chunks(self):
        # Regression: total_timeout must bound the WHOLE chunked review, not reset
        # per chunk. Verify review_diff passes the SAME budget object to every
        # chunk's run_jury call.
        import ai_jury.orchestrator as orch

        cfg = _from_dict(
            {
                "jury": {
                    "rounds": 1,
                    "verify": False,
                    "total_timeout": 300,
                    "diff": {"max_bytes": 10, "chunk": True, "chunk_max_bytes": 200},
                },
                "agent": [{"name": "claude", "vendor": "anthropic", "command": "claude"}],
            }
        )
        diff = _file_segment("src/a.py", 20) + _file_segment("src/b.py", 20)
        seen_budgets = []
        real = orch.run_jury

        def capture(config, chunk, **kw):
            seen_budgets.append(kw.get("budget"))
            return real(config, chunk, **kw)

        orch.run_jury = capture
        try:
            outcome, plan = orch.review_diff(cfg, diff, mock=True)
        finally:
            orch.run_jury = real
        self.assertGreaterEqual(len(plan.chunks), 2)
        self.assertEqual(len(seen_budgets), len(plan.chunks))
        self.assertIsNotNone(seen_budgets[0])
        # All chunks share one budget object -> total_timeout spans the run.
        self.assertTrue(all(b is seen_budgets[0] for b in seen_budgets))

    def test_too_large_raises(self):
        cfg = _from_dict(
            {
                "jury": {"diff": {"max_bytes": 10, "chunk": False}},
                "agent": [{"name": "claude", "vendor": "anthropic", "command": "claude"}],
            }
        )
        diff = _file_segment("src/a.py", 200)
        with self.assertRaises(RuntimeError):
            review_diff(cfg, diff, mock=True)


if __name__ == "__main__":
    unittest.main()
