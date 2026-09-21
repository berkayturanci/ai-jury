"""The zero-setup offline demo: `jury --mock` with no diff source (issue #21).

The docs (and the CLI's own `--help`) present `jury --mock` / `jury --mock
--theater` as an offline demo, but with no `--diff-file`/`--pr` it used to exit 1
with "provide one of --pr, --issue, --diff-file, ...". A fresh `pipx install
ai-jury` has no `examples/sample.diff` to point at either. So `--mock` with no
source now reviews a diff bundled inside the package, and the sample is shipped as
package data (`ai_jury/data/sample.diff`) rather than only living in `examples/`.
"""

import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace

from ai_jury import cli

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _args(**kw):
    base = {
        "pr": None,
        "issue": None,
        "diff_file": None,
        "repo": None,
        "commit": None,
        "commits": None,
        "mock": False,
    }
    base.update(kw)
    return SimpleNamespace(**base)


class BundledSampleDiff(unittest.TestCase):
    def test_the_helper_reads_the_packaged_diff(self):
        # Proves the importlib.resources path resolves — i.e. the file is where
        # the package-data rule ships it, not just in a source checkout.
        diff = cli._bundled_sample_diff()
        self.assertTrue(diff.startswith("diff --git "))
        self.assertIn("src/payments.py", diff)

    def test_it_stays_byte_identical_to_the_examples_copy(self):
        # `examples/sample.diff` is what checkout-based docs and the release
        # checklist point at; the packaged copy is what `jury --mock` reads. They
        # must not drift, or the demo a reader sees depends on how they installed.
        packaged = (_REPO_ROOT / "src" / "ai_jury" / "data" / "sample.diff").read_bytes()
        examples = (_REPO_ROOT / "examples" / "sample.diff").read_bytes()
        self.assertEqual(packaged, examples)


class MockWithNoSource(unittest.TestCase):
    def test_it_falls_back_to_the_bundled_sample(self):
        with redirect_stderr(io.StringIO()) as err:
            diff, context = cli._read_diff(_args(mock=True))
        self.assertEqual(diff, cli._bundled_sample_diff())
        self.assertEqual(context, "")
        # The note tells the reader this is the demo sample, not their own change.
        self.assertIn("bundled offline-demo diff", err.getvalue())

    def test_an_explicit_source_still_wins_under_mock(self):
        # `--mock` only supplies a *fallback*; a real source is still honoured, so
        # `jury --mock --diff-file mine.diff` reviews mine.diff, not the sample.
        with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False, encoding="utf-8") as fh:
            fh.write("diff --git a/mine.py b/mine.py\n+MINE\n")
            path = fh.name
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))
        diff, _ = cli._read_diff(_args(mock=True, diff_file=path))
        self.assertIn("MINE", diff)
        self.assertNotIn("payments.py", diff)

    def test_without_mock_no_source_still_errors(self):
        # The fallback is gated on --mock: a real run with no source must not
        # silently review the sample — it must still tell the user what to pass.
        with self.assertRaises(SystemExit) as ctx:
            cli._read_diff(_args(mock=False))
        message = str(ctx.exception)
        for flag in ("--pr", "--issue", "--diff-file", "--commit", "--commits"):
            self.assertIn(flag, message)


if __name__ == "__main__":
    unittest.main()
