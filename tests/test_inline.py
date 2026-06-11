"""Tests for inline GitHub review comments (issue #5)."""
import io
import json
import unittest
from contextlib import redirect_stdout

from ai_jury import github
from ai_jury.findings import Finding
from ai_jury.github import build_inline_payload, post_inline_comments


class BuildInlinePayloadTests(unittest.TestCase):
    def test_only_file_and_line_findings(self):
        findings = [
            Finding(severity="major", file="a.py", line=10, claim="c1", suggested_fix="fix1"),
            Finding(severity="minor", file="", line=5, claim="no file"),
            Finding(severity="minor", file="b.py", line=None, claim="no line"),
            Finding(severity="info", file="c.py", line=3, claim="c4"),
        ]
        payload = build_inline_payload(findings)
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["path"], "a.py")
        self.assertEqual(payload[0]["line"], 10)
        self.assertEqual(payload[0]["side"], "RIGHT")
        self.assertIn("[major]", payload[0]["body"])
        self.assertIn("c1", payload[0]["body"])
        self.assertIn("fix1", payload[0]["body"])

    def test_empty(self):
        self.assertEqual(build_inline_payload([]), [])

    def test_marker_present(self):
        payload = build_inline_payload([Finding(severity="nit", file="x.py", line=1, claim="c")])
        self.assertIn(github.INLINE_MARKER, payload[0]["body"])


class PostInlineDryRunTests(unittest.TestCase):
    def test_dry_run_no_network(self):
        findings = [Finding(severity="major", file="a.py", line=10, claim="c1")]
        buf = io.StringIO()
        with redirect_stdout(buf):
            payload = post_inline_comments("1", findings, dry_run=True)
        self.assertEqual(payload["event"], "COMMENT")
        self.assertEqual(len(payload["comments"]), 1)
        printed = json.loads(buf.getvalue())
        self.assertEqual(printed["comments"][0]["path"], "a.py")
        self.assertEqual(printed["comments"][0]["side"], "RIGHT")

    def test_review_payload_has_nonempty_body(self):
        # Issue #122: GitHub's create-review API requires a top-level `body` when
        # event is COMMENT, else it can 422.
        findings = [Finding(severity="major", file="a.py", line=10, claim="c1")]
        with redirect_stdout(io.StringIO()):
            payload = post_inline_comments("1", findings, dry_run=True)
        self.assertTrue(payload.get("body"))


class FindingSignatureTests(unittest.TestCase):
    def test_signature_stable_across_calls(self):
        f = Finding(severity="major", file="a.py", line=10, claim="c1")
        self.assertEqual(github._finding_signature(f), github._finding_signature(f))

    def test_signature_normalizes_case_and_whitespace(self):
        a = Finding(severity="MAJOR", claim="  SQL Injection ", file="a.py", line=1)
        b = Finding(severity="major", claim="sql injection", file="a.py", line=1)
        self.assertEqual(github._finding_signature(a), github._finding_signature(b))

    def test_signature_differs_by_claim(self):
        a = Finding(severity="major", claim="claim one", file="a.py", line=1)
        b = Finding(severity="major", claim="claim two", file="a.py", line=1)
        self.assertNotEqual(github._finding_signature(a), github._finding_signature(b))

    def test_signature_differs_by_severity(self):
        a = Finding(severity="major", claim="same claim", file="a.py", line=1)
        b = Finding(severity="minor", claim="same claim", file="a.py", line=1)
        self.assertNotEqual(github._finding_signature(a), github._finding_signature(b))

    def test_signature_embedded_in_body_and_roundtrips(self):
        f = Finding(severity="major", file="a.py", line=10, claim="c1")
        body = github._comment_body(f)
        self.assertIn(github.INLINE_MARKER, body)
        self.assertEqual(github._sig_from_body(body), github._finding_signature(f))

    def test_sig_from_body_absent(self):
        self.assertEqual(github._sig_from_body(""), "")
        self.assertEqual(github._sig_from_body(None), "")
        self.assertEqual(github._sig_from_body("plain text, no marker"), "")


class SameLineDistinctFindingsTests(unittest.TestCase):
    def test_two_distinct_findings_same_line_not_collapsed(self):
        findings = [
            Finding(severity="major", file="a.py", line=10, claim="sql injection"),
            Finding(severity="major", file="a.py", line=10, claim="missing auth"),
        ]
        payload = build_inline_payload(findings)
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["path"], payload[1]["path"])
        self.assertEqual(payload[0]["line"], payload[1]["line"])
        sigs = {github._sig_from_body(c["body"]) for c in payload}
        self.assertEqual(len(sigs), 2)


class InlineDedupTests(unittest.TestCase):
    def _patch_no_network(self, posted):
        # Avoid any real `gh` call: capture the JSON body that would be sent.
        self._orig_resolve = github._resolve_repo
        self._orig_with_input = github._gh_with_input
        github._resolve_repo = lambda _repo: "o/r"

        def fake_with_input(_args, stdin_data):
            posted.append(json.loads(stdin_data))
            return ""

        github._gh_with_input = fake_with_input

    def tearDown(self):
        if hasattr(self, "_orig_resolve"):
            github._resolve_repo = self._orig_resolve
        if hasattr(self, "_orig_with_input"):
            github._gh_with_input = self._orig_with_input
        if hasattr(self, "_orig_existing"):
            github._existing_inline_keys = self._orig_existing

    def test_existing_signature_skips_only_that_finding(self):
        f1 = Finding(severity="major", file="a.py", line=10, claim="sql injection")
        f2 = Finding(severity="major", file="a.py", line=10, claim="missing auth")
        sig1 = github._finding_signature(f1)

        self._orig_existing = github._existing_inline_keys
        github._existing_inline_keys = lambda _pr, _repo: {("a.py", 10, sig1)}

        posted = []
        self._patch_no_network(posted)
        payload = github.post_inline_comments("1", [f1, f2], repo="o/r")

        # f1 already present -> skipped; the distinct same-line f2 -> kept.
        self.assertEqual(len(payload["comments"]), 1)
        kept = payload["comments"][0]
        self.assertEqual(github._sig_from_body(kept["body"]), github._finding_signature(f2))
        self.assertIn("missing auth", kept["body"])
        self.assertNotIn("sql injection", kept["body"])
        self.assertEqual(len(posted), 1)

    def test_null_line_falls_back_to_original_line(self):
        f = Finding(severity="major", file="a.py", line=10, claim="c1")
        body = github._comment_body(f)
        sig = github._finding_signature(f)

        # Existing comment with line=null but original_line set.
        comments_json = json.dumps(
            [{"path": "a.py", "line": None, "original_line": 10, "body": body}]
        )
        self._orig_gh = github._gh
        github._gh = lambda *_args: comments_json
        try:
            keys = github._existing_inline_keys("1", "o/r")
        finally:
            github._gh = self._orig_gh

        self.assertIn(("a.py", 10, sig), keys)


if __name__ == "__main__":
    unittest.main()
