"""Unit tests for semantic hunk chunking in largediff (issue #522)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_jury.largediff import DiffFile, _split_file_at_hunk_boundaries, plan_diff  # noqa: E402

LARGE_DIFF_MULTI_HUNK = """diff --git a/src/service.py b/src/service.py
index 1111111..2222222 100644
--- a/src/service.py
+++ b/src/service.py
@@ -10,6 +10,12 @@ def authenticate(user, token):
+    # Hunk 1 content
+    validate_token(token)
+    check_permissions(user)
@@ -100,6 +106,12 @@ def process_payment(account, amount):
+    # Hunk 2 content
+    verify_balance(account)
+    charge_card(account, amount)
@@ -200,6 +212,12 @@ def send_receipt(user, transaction_id):
+    # Hunk 3 content
+    generate_pdf(transaction_id)
+    send_email(user)
"""


class SemanticChunkingTests(unittest.TestCase):
    def test_semantic_chunking_splits_across_hunk_boundaries(self):
        plan = plan_diff(LARGE_DIFF_MULTI_HUNK, max_bytes=200, chunk=True, chunk_max_bytes=250)
        self.assertEqual(plan.mode, "chunked")
        self.assertGreater(len(plan.chunks), 1)
        for chunk in plan.chunks:
            # Each chunk must preserve the file header preamble
            self.assertIn("diff --git a/src/service.py b/src/service.py", chunk)

    def test_split_file_at_hunk_boundaries_single_or_no_hunk(self):
        f = DiffFile(path="a.txt", text="diff --git a/a.txt b/a.txt\nplain text without hunks")
        chunks = _split_file_at_hunk_boundaries(f, 100)
        self.assertEqual(chunks, [f.text])

    def test_chunking_with_preceding_small_files(self):
        small_diff = "diff --git a/small.py b/small.py\n@@ -1,1 +1,1 @@\n-a\n+b\n"
        combined = small_diff + LARGE_DIFF_MULTI_HUNK
        plan = plan_diff(combined, max_bytes=200, chunk=True, chunk_max_bytes=250)
        self.assertEqual(plan.mode, "chunked")


if __name__ == "__main__":
    unittest.main()
