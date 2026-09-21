"""`jury apply` must not write into git internals or CI config (#831).

The containment check refused only a path that escaped the working tree (``../``). But a
suggestion whose ``file`` is ``.git/config`` resolves *inside* the tree, so it passed — and
the line-replacement branch then overwrote a line of ``.git/config``. Writing there
(``core.fsmonitor``, a hook path) runs a command on the next git operation; writing a
``.github/workflows/*.yml`` runs one in CI. The suggestion text is attacker-influenced (it
rides the report the tool posts on a pull request), and ``apply`` parses that report's prose,
so this is reachable without the operator noticing which path a preview names.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_jury.patches import (  # noqa: E402
    PatchSuggestion,
    apply_patch_suggestion,
    preview_patch_suggestion,
)


def _suggest(file, fix, line=1):
    return PatchSuggestion(file=file, line=line, severity="major", claim="x", suggested_fix=fix)


class TestApplyRefusesSensitiveTargets(unittest.TestCase):
    def _repo(self, tmp):
        root = Path(tmp)
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text("[core]\n\tbare = false\n", encoding="utf-8")
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / ".github" / "workflows" / "ci.yml").write_text("on: push\n", encoding="utf-8")
        return root

    def test_a_write_into_dot_git_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            before = (root / ".git" / "config").read_text(encoding="utf-8")
            ok, msg = apply_patch_suggestion(
                _suggest(".git/config", '\tfsmonitor = "touch PWNED; false"'), root_dir=root
            )
            self.assertFalse(ok)
            self.assertIn(".git", msg)
            self.assertEqual((root / ".git" / "config").read_text(encoding="utf-8"), before)

    def test_a_write_into_dot_github_workflows_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            before = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
            ok, msg = apply_patch_suggestion(
                _suggest(".github/workflows/ci.yml", "on: [pull_request_target]"), root_dir=root
            )
            self.assertFalse(ok)
            self.assertEqual(
                (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"), before
            )

    @unittest.skipIf(os.name == "nt", "POSIX symlink semantics")
    def test_a_symlink_that_redirects_into_dot_git_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            (root / "link").symlink_to(root / ".git" / "config")
            before = (root / ".git" / "config").read_text(encoding="utf-8")
            ok, msg = apply_patch_suggestion(
                _suggest("link", '\tfsmonitor = "touch PWNED; false"'), root_dir=root
            )
            self.assertFalse(ok)
            self.assertEqual((root / ".git" / "config").read_text(encoding="utf-8"), before)

    def test_a_case_variant_of_dot_git_is_refused(self):
        # On a case-insensitive filesystem `.Git/config` names the real `.git/config`; the
        # check case-folds so the variant cannot slip past (agy round 1).
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            for name in (".Git/config", ".GITHUB/workflows/ci.yml"):
                with self.subTest(name=name):
                    ok, msg = apply_patch_suggestion(_suggest(name, "evil = 1"), root_dir=root)
                    self.assertFalse(ok, msg)

    def test_the_dry_run_preview_refuses_a_sensitive_target(self):
        # Preview and apply must agree (#605): a dry run may not report `.git/config` as
        # something it "would touch" with no refusal.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            paths, refusal = preview_patch_suggestion(
                _suggest(".git/config", "x = 1"), root_dir=root
            )
            self.assertIsNotNone(refusal)
            self.assertEqual(paths, [])

    def test_an_ordinary_source_file_still_applies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            (root / "src").mkdir()
            (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
            ok, msg = apply_patch_suggestion(_suggest("src/a.py", "x = 2", line=1), root_dir=root)
            self.assertTrue(ok, msg)
            self.assertEqual((root / "src" / "a.py").read_text(encoding="utf-8"), "x = 2\n")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
