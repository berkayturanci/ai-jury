"""Unit tests for suggested patch parsing and jury apply (issue #521)."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_jury.cli import main  # noqa: E402
from ai_jury.patches import (  # noqa: E402
    PatchSuggestion,
    apply_patch_suggestion,
    parse_patch_suggestions,
)

SAMPLE_PATCH_REPORT = """# 🏛️ AI Jury

## Suggested patches

### src/auth.py:10 — [critical] SQL injection vulnerability

> Verified by the jury.

```suggestion
cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
```

### src/utils.py:25 — [minor] missing type annotation

> Verified by the jury.

```suggestion
def add(a: int, b: int) -> int:
```
"""


class PatchesApplyTests(unittest.TestCase):
    def test_parse_patch_suggestions(self):
        suggestions = parse_patch_suggestions(SAMPLE_PATCH_REPORT)
        self.assertEqual(len(suggestions), 2)
        self.assertEqual(suggestions[0].file, "src/auth.py")
        self.assertEqual(suggestions[0].line, 10)
        self.assertEqual(suggestions[0].severity, "critical")
        self.assertIn("SQL injection", suggestions[0].claim)
        self.assertIn("cursor.execute", suggestions[0].suggested_fix)

        self.assertEqual(suggestions[1].file, "src/utils.py")
        self.assertEqual(suggestions[1].line, 25)

    def test_apply_patch_suggestion_line_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            auth_file = tmp_path / "src" / "auth.py"
            auth_file.parent.mkdir(parents=True)
            auth_file.write_text(
                "\n".join(
                    [f"# line {i}" for i in range(1, 10)]
                    + ['cursor.execute(f"SELECT * FROM users WHERE username = {username}")']
                )
                + "\n",
                encoding="utf-8",
            )

            s = PatchSuggestion(
                file="src/auth.py",
                line=10,
                severity="critical",
                claim="SQL injection",
                suggested_fix='cursor.execute("SELECT * FROM users WHERE username = %s", (username,))',
            )

            ok, msg = apply_patch_suggestion(s, root_dir=tmp_path)
            self.assertTrue(ok, f"Failed to apply: {msg}")
            content = auth_file.read_text(encoding="utf-8")
            self.assertIn("(username,)", content)

    def test_apply_patch_suggestion_path_traversal_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            s = PatchSuggestion(
                file="../../etc/passwd",
                line=1,
                severity="critical",
                claim="path traversal",
                suggested_fix="root:x:0:0:::",
            )
            ok, msg = apply_patch_suggestion(s, root_dir=tmp_path)
            self.assertFalse(ok)
            self.assertIn("Path traversal rejected", msg)

    def test_apply_patch_suggestion_file_not_found(self):
        s = PatchSuggestion(
            file="nonexistent.py",
            line=1,
            severity="minor",
            claim="test",
            suggested_fix="pass",
        )
        ok, msg = apply_patch_suggestion(s)
        self.assertFalse(ok)
        self.assertIn("File not found", msg)

    def test_apply_patch_suggestion_line_out_of_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            f = tmp_path / "test.py"
            f.write_text("x = 1\n", encoding="utf-8")
            s = PatchSuggestion(
                file="test.py",
                line=999,
                severity="minor",
                claim="test",
                suggested_fix="pass",
            )
            ok, msg = apply_patch_suggestion(s, root_dir=tmp_path)
            self.assertFalse(ok)
            self.assertIn("Cannot apply non-diff suggestion", msg)

    def test_apply_patch_suggestion_git_apply_success_and_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            f = tmp_path / "test.py"
            f.write_text("x = 1\n", encoding="utf-8")
            s = PatchSuggestion(
                file="test.py",
                line=None,
                severity="minor",
                claim="test",
                suggested_fix="--- a/test.py\n+++ b/test.py\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n",
            )
            # Two subprocess calls now: the --check probe, then the real apply.
            probe_ok = MagicMock(returncode=0, stdout="1\t1\ttest.py\0", stderr="")
            apply_ok = MagicMock(returncode=0, stdout="", stderr="")
            with patch("subprocess.run", side_effect=[probe_ok, apply_ok]):
                ok, msg = apply_patch_suggestion(s, root_dir=tmp_path)
                self.assertTrue(ok)
                self.assertIn("Applied git patch", msg)

            mock_fail = MagicMock(returncode=1, stdout="", stderr="error: corrupt patch")
            with patch("subprocess.run", return_value=mock_fail):
                ok, msg = apply_patch_suggestion(s, root_dir=tmp_path)
                self.assertFalse(ok)
                self.assertIn("Git apply failed", msg)

            # A patch git reads as touching nothing is not the fix it claims to be.
            probe_empty = MagicMock(returncode=0, stdout="", stderr="")
            with patch("subprocess.run", return_value=probe_empty):
                ok, msg = apply_patch_suggestion(s, root_dir=tmp_path)
                self.assertFalse(ok)
                self.assertIn("touches no files", msg)

            # A record git emits in a shape this parser does not understand is a
            # path that went unchecked, so it is refused rather than skipped.
            probe_odd = MagicMock(returncode=0, stdout="garbage\0", stderr="")
            with patch("subprocess.run", return_value=probe_odd):
                ok, msg = apply_patch_suggestion(s, root_dir=tmp_path)
                self.assertFalse(ok)
                self.assertIn("Unrecognized patch summary", msg)

            probe_escape = MagicMock(returncode=0, stdout="1\t1\t../../etc/passwd\0", stderr="")
            with patch("subprocess.run", return_value=probe_escape):
                ok, msg = apply_patch_suggestion(s, root_dir=tmp_path)
                self.assertFalse(ok)
                self.assertIn("Path traversal rejected", msg)

    def test_cli_apply_subcommand_dispatch(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(SAMPLE_PATCH_REPORT)
            report_path = f.name

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "src").mkdir()
            (tmp_path / "src" / "auth.py").write_text("\n" * 15, encoding="utf-8")
            (tmp_path / "src" / "utils.py").write_text("\n" * 30, encoding="utf-8")

            old_cwd = Path.cwd()
            try:
                os.chdir(tmp_path)
                # Apply specific index
                code = main(["apply", "--report", report_path, "1", "--yes"])
                self.assertEqual(code, 0)

                # Apply all
                code_all = main(["apply", "--report", report_path, "all", "--yes"])
                self.assertEqual(code_all, 0)

                # Invalid index
                code_bad_idx = main(["apply", "--report", report_path, "99"])
                self.assertEqual(code_bad_idx, 2)

                # Non-numeric invalid index
                code_invalid = main(["apply", "--report", report_path, "abc"])
                self.assertEqual(code_invalid, 2)
            finally:
                os.chdir(old_cwd)

    def test_cli_apply_failures_on_nonexistent_files(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(SAMPLE_PATCH_REPORT)
            report_path = f.name

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Do NOT create src/auth.py or src/utils.py -> applying will fail
            old_cwd = Path.cwd()
            try:
                os.chdir(tmp_path)
                code_fail_all = main(["apply", "--report", report_path, "all", "--yes"])
                self.assertEqual(code_fail_all, 1)

                code_fail_single = main(["apply", "--report", report_path, "1", "--yes"])
                self.assertEqual(code_fail_single, 1)
            finally:
                os.chdir(old_cwd)

    def test_cli_apply_errors(self):
        # Missing report
        code = main(["apply", "--report", "/nonexistent/report.md"])
        self.assertEqual(code, 2)

        # Directory given as report
        with tempfile.TemporaryDirectory() as tmp_dir:
            code_dir = main(["apply", "--report", tmp_dir])
            self.assertEqual(code_dir, 2)

        # Empty report stdin
        with (
            patch("sys.stdin", io.StringIO("No patches here")),
            patch("sys.stdin.isatty", return_value=False),
        ):
            code = main(["apply"])
            self.assertEqual(code, 1)

        # No report and isatty
        with patch("sys.stdin.isatty", return_value=True):
            code = main(["apply"])
            self.assertEqual(code, 2)


class ApplyPreviewsAndConfirmsBeforeWriting(unittest.TestCase):
    """#605: `jury apply` wrote with no preview, no confirmation, and defaulted to
    applying every suggestion.

    Independent of #603: any hand-rolled containment check is a bet that every way
    a patch can name a file was enumerated. A preview-and-confirm step is what
    makes losing that bet survivable rather than silent.

    Every assertion here checks the **working tree**, not just the exit code — the
    defect being fixed is precisely a command that reported one thing and wrote
    another.
    """

    REPORT = (
        "# 🏛️ AI Jury\n\n## Suggested patches\n\n"
        "### target.py:1 — [major] tighten this\n\n> Verified by the jury.\n\n"
        "```suggestion\nx = 2\n```\n"
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        for argv in (
            ["git", "init", "-q", "."],
            ["git", "config", "user.email", "t@example.com"],
            ["git", "config", "user.name", "t"],
        ):
            subprocess.run(argv, cwd=self.root, check=True, capture_output=True)
        (self.root / "target.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "victim.py").write_text("secret = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-qm", "init"], cwd=self.root, check=True, capture_output=True
        )
        # Outside the repo: a report inside it shows as untracked and would
        # make every `git status --porcelain` assertion pass or fail for the
        # wrong reason.
        self._reports = tempfile.TemporaryDirectory()
        self.addCleanup(self._reports.cleanup)
        self.report = Path(self._reports.name) / "report.md"
        self.report.write_text(self.REPORT, encoding="utf-8")

        old = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, old)

    def _porcelain(self):
        return subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def _run(self, argv):
        err = io.StringIO()
        with patch.object(sys, "stderr", err):
            code = main(argv)
        return code, err.getvalue()

    def test_dry_run_writes_nothing(self):
        code, err = self._run(["apply", "--report", str(self.report), "all", "--dry-run"])

        self.assertEqual(0, code)
        self.assertIn("would touch", err)
        self.assertIn("target.py", err)
        # The tree is the assertion, not the exit code.
        self.assertEqual("", self._porcelain())

    def test_a_non_tty_without_yes_refuses_and_writes_nothing(self):
        # Piping a report in is exactly the unattended case, and stdin may already
        # be consumed by the report — silence must not read as consent.
        with patch.object(sys.stdin, "isatty", return_value=False):
            code, err = self._run(["apply", "--report", str(self.report), "all"])

        self.assertEqual(2, code)
        self.assertIn("refusing to write without confirmation", err)
        self.assertEqual("", self._porcelain())

    def test_declining_the_prompt_writes_nothing(self):
        with (
            patch.object(sys.stdin, "isatty", return_value=True),
            patch("builtins.input", return_value="n"),
        ):
            code, err = self._run(["apply", "--report", str(self.report), "all"])

        self.assertEqual(1, code)
        self.assertIn("Aborted", err)
        self.assertEqual("", self._porcelain())

    def test_accepting_the_prompt_writes(self):
        with (
            patch.object(sys.stdin, "isatty", return_value=True),
            patch("builtins.input", return_value="y"),
        ):
            code, _ = self._run(["apply", "--report", str(self.report), "all"])

        self.assertEqual(0, code)
        self.assertNotEqual("", self._porcelain(), "nothing was written after a yes")

    def test_a_bare_apply_no_longer_applies_everything(self):
        # The old default was `all`, so `jury apply --report r.md` rewrote the tree.
        code, err = self._run(["apply", "--report", str(self.report)])

        self.assertEqual(2, code)
        self.assertIn("choose what to apply", err)
        self.assertEqual("", self._porcelain())

    def test_the_preview_names_a_path_the_suggestion_does_not_claim(self):
        """Proves the preview reflects git's answer, not the suggestion's own claim.

        The suggestion names `target.py`; the patch body renames `victim.py`. A
        preview built from `suggestion.file` would show only `target.py` and hide
        exactly the thing the operator needs to see.
        """
        report = (
            "# 🏛️ AI Jury\n\n## Suggested patches\n\n"
            "### target.py:1 — [major] tighten this\n\n> Verified by the jury.\n\n"
            "```suggestion\n"
            "diff --git a/target.py b/target.py\n"
            "--- a/target.py\n+++ b/target.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
            "diff --git a/victim.py b/pwned.py\n"
            "similarity index 100%\nrename from victim.py\nrename to pwned.py\n"
            "```\n"
        )
        self.report.write_text(report, encoding="utf-8")

        code, err = self._run(["apply", "--report", str(self.report), "all", "--dry-run"])

        self.assertEqual(0, code)
        self.assertIn("pwned.py", err, "the preview hid a path the patch would create")
        self.assertIn("would be refused", err)
        self.assertEqual("", self._porcelain())


class ContainmentAgainstARealGitRepository(unittest.TestCase):
    """#603: the check read only ---/+++ headers, so git's other filename-bearing
    constructs reached `git apply` unvalidated.

    Every fixture here **creates the secondary target**. The old test passed for
    the wrong reason: the second path did not exist, so `git apply` failed on its
    own and the assertion landed on the resulting error string — remove the guard
    entirely and it still passed. Here the patch would apply cleanly, so the only
    thing keeping these green is the guard.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        for argv in (
            ["git", "init", "-q", "."],
            ["git", "config", "user.email", "t@example.com"],
            ["git", "config", "user.name", "t"],
            ["git", "config", "core.autocrlf", "false"],
        ):
            subprocess.run(argv, cwd=self.root, check=True, capture_output=True)
        (self.root / "target.py").write_text("x = 1\n", encoding="utf-8")
        # The secondary target exists, so a patch reaching it would succeed.
        (self.root / "victim.py").write_text("secret = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-qm", "init"], cwd=self.root, check=True, capture_output=True
        )

    def _apply(self, fix):
        return apply_patch_suggestion(
            PatchSuggestion(
                file="target.py", line=1, severity="major", claim="c", suggested_fix=fix
            ),
            root_dir=self.root,
        )

    EDIT = "--- a/target.py\n+++ b/target.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"

    def test_a_legitimate_single_file_patch_still_applies(self):
        ok, msg = self._apply(self.EDIT)

        self.assertTrue(ok, msg)
        self.assertEqual("x = 2\n", (self.root / "target.py").read_text(encoding="utf-8"))

    def test_a_rename_section_cannot_ride_along(self):
        ok, msg = self._apply(
            self.EDIT + "diff --git a/victim.py b/pwned.py\n"
            "similarity index 100%\n"
            "rename from victim.py\n"
            "rename to pwned.py\n"
        )

        self.assertFalse(ok)
        self.assertIn("pwned.py", msg)
        # Nothing was written — not the rename, and not the edit that carried it.
        self.assertTrue((self.root / "victim.py").exists())
        self.assertFalse((self.root / "pwned.py").exists())
        self.assertEqual("x = 1\n", (self.root / "target.py").read_text(encoding="utf-8"))

    def test_a_mode_change_on_another_file_cannot_ride_along(self):
        # The mode change is paired with a content hunk so the section parses
        # identically everywhere: Windows git ignores file modes, and a
        # mode-only section is rejected there as lacking filename information —
        # which would make this pass for git's reason rather than the guard's.
        ok, msg = self._apply(
            self.EDIT + "diff --git a/victim.py b/victim.py\n"
            "old mode 100644\n"
            "new mode 100755\n"
            "--- a/victim.py\n"
            "+++ b/victim.py\n"
            "@@ -1 +1 @@\n"
            "-secret = 1\n"
            "+secret = 2\n"
        )

        self.assertFalse(ok)
        self.assertIn("victim.py", msg)
        self.assertEqual("x = 1\n", (self.root / "target.py").read_text(encoding="utf-8"))
        self.assertEqual("secret = 1\n", (self.root / "victim.py").read_text(encoding="utf-8"))

    def test_a_new_file_cannot_ride_along(self):
        ok, msg = self._apply(
            self.EDIT + "diff --git a/planted.py b/planted.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/planted.py\n"
            "@@ -0,0 +1 @@\n"
            "+import os\n"
        )

        self.assertFalse(ok)
        self.assertFalse((self.root / "planted.py").exists())

    def test_renaming_the_target_itself_is_refused(self):
        # numstat reports a rename's destination but never its source, so a rename
        # whose destination is the suggested file would look contained while
        # destroying the source. The operation is refused, not its paths re-derived.
        ok, msg = self._apply(
            "diff --git a/target.py b/renamed.py\n"
            "similarity index 100%\n"
            "rename from target.py\n"
            "rename to renamed.py\n"
        )

        self.assertFalse(ok)
        self.assertTrue((self.root / "target.py").exists())
        self.assertFalse((self.root / "renamed.py").exists())

    def test_a_rename_only_body_is_not_written_into_the_file_as_text(self):
        """Found while fixing #603, one step earlier than the reported hole.

        The diff detection was `startswith("---") or "@@" in fix`. A rename-only
        body has neither, so it never reached the git branch at all — it fell
        through to the line-replacement path, which wrote the diff *text* into
        target.py and returned "Applied line replacement". A body git can read as
        a patch must reach the branch where containment is decided.
        """
        original = (self.root / "target.py").read_text(encoding="utf-8")

        ok, _ = self._apply(
            "diff --git a/target.py b/renamed.py\n"
            "similarity index 100%\n"
            "rename from target.py\n"
            "rename to renamed.py\n"
        )

        self.assertFalse(ok)
        self.assertEqual(original, (self.root / "target.py").read_text(encoding="utf-8"))
        self.assertNotIn("rename from", (self.root / "target.py").read_text(encoding="utf-8"))

    def test_a_binary_section_reaches_the_containment_check(self):
        # A binary patch has no ---/+++ lines at all, so under the old detection it
        # was literal text too.
        ok, _ = self._apply(
            "diff --git a/blob.bin b/blob.bin\n"
            "new file mode 100644\n"
            "GIT binary patch\n"
            "literal 4\n"
            "Lc$@aOOaK4?\n\n"
        )

        self.assertFalse(ok)
        self.assertFalse((self.root / "blob.bin").exists())

    def test_a_rename_hidden_behind_a_matching_path_set_is_refused(self):
        """The case that makes `--numstat` alone insufficient.

        Delete target.py, then rename victim.py onto target.py. Git accepts the
        patch, and numstat reports *only* `target.py` — twice — because it names a
        rename's destination and never its source. The path set therefore equals
        the suggested file exactly, and the containment check passes it. The only
        evidence that victim.py is destroyed is the `--summary` rename line, which
        is why `rename`/`copy` are refused as operations rather than having their
        paths re-derived.
        """
        ok, msg = self._apply(
            "diff --git a/target.py b/target.py\n"
            "deleted file mode 100644\n"
            "--- a/target.py\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-x = 1\n"
            "diff --git a/victim.py b/target.py\n"
            "similarity index 100%\n"
            "rename from victim.py\n"
            "rename to target.py\n"
        )

        self.assertFalse(ok)
        self.assertIn("rename", msg)
        self.assertTrue((self.root / "victim.py").exists(), "victim.py was destroyed")
        self.assertEqual("x = 1\n", (self.root / "target.py").read_text(encoding="utf-8"))

    def test_a_copy_cannot_ride_along(self):
        ok, _ = self._apply(
            "diff --git a/target.py b/copy.py\n"
            "similarity index 100%\n"
            "copy from target.py\n"
            "copy to copy.py\n"
        )

        self.assertFalse(ok)
        self.assertFalse((self.root / "copy.py").exists())


class ErrorBranchesThatNothingExecuted(unittest.TestCase):
    """The three `try`/`except` arms #588 added and #587 asked to be tested.

    Its body said "Added regression tests in test_cli_contract.py and
    test_patches_apply.py" — literally true, one test in each file, and between
    them they covered two of five new branches. The test placed in *this* file
    exercised `cli.py`'s `_run_apply`, not `patches.py` at all, so the filename
    made it look as though the `patches.py` bullet above it was guarded.

    Nothing in the repository referenced the strings `Cannot read` or
    `Cannot write`, and the 98% coverage floor had room for three uncovered
    branches (#607).
    """

    def _suggestion(self, path: Path, fix: str = "x = 2\n"):
        return PatchSuggestion(
            file=path.name, line=1, claim="c", suggested_fix=fix, severity="minor"
        )

    def test_a_file_that_cannot_be_decoded_reports_cannot_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "t.py"
            # Valid UTF-8 has no lone continuation byte, so read_text raises
            # UnicodeDecodeError — a real shape for a file the agent picked up
            # from a binary or differently-encoded tree.
            target.write_bytes(b"\xff\xfe not utf-8 \x80")

            ok, message = apply_patch_suggestion(self._suggestion(target), root_dir=root)

            self.assertFalse(ok)
            self.assertIn("Cannot read", message)
            self.assertIn("t.py", message)

    # `hasattr` first: os.geteuid is POSIX-only, and a decorator argument is
    # evaluated when the class body runs — so calling it unguarded raised
    # AttributeError at *import* time on Windows, failing the whole module
    # rather than this one test. Windows has no root to skip for, and chmod
    # there sets the read-only attribute, so the test itself still applies.
    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores the write bit")
    def test_a_file_that_cannot_be_written_reports_cannot_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "t.py"
            target.write_text("x = 1\n", encoding="utf-8")
            target.chmod(0o444)
            try:
                ok, message = apply_patch_suggestion(self._suggestion(target), root_dir=root)
            finally:
                target.chmod(0o644)

            self.assertFalse(ok)
            self.assertIn("Cannot write", message)
            self.assertIn("t.py", message)

    def test_the_read_failure_leaves_the_file_alone(self):
        """A refused apply must not be a partial apply."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "t.py"
            original = b"\xff\xfe not utf-8 \x80"
            target.write_bytes(original)

            apply_patch_suggestion(self._suggestion(target), root_dir=root)

            self.assertEqual(target.read_bytes(), original)

    def test_an_undecodable_report_reports_the_read_error(self):
        """`cli.py`'s own read arm — the third branch, in the other file."""
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.md"
            report.write_bytes(b"\xff\xfe \x80")

            err = io.StringIO()
            with patch.object(sys, "stderr", err):
                code = main(["apply", "--report", str(report)])

            self.assertEqual(code, 2)
            self.assertIn("Error reading report file", err.getvalue())


# A directory component shaped like a GitHub token. The outer message names the
# suggestion's *relative* file, so the only way the token reaches the output is
# through the absolute path inside the OSError text — the arm #658 wraps.
_TOKEN_DIR = "ghp_" + "A" * 36


class IoErrorRedactionTests(unittest.TestCase):
    """The four `{exc}` arms #658 wrapped must redact what they interpolate.

    `OSError.__str__` repeats the resolved absolute path, which the outer
    message does not otherwise show — so a secret-shaped path component was
    the concrete leak. Each test plants one and asserts the placeholder comes
    back instead. Root is skipped where the test relies on a permission bit.
    """

    def _suggestion(self, path: Path, fix: str = "x = 2\n"):
        return PatchSuggestion(
            file=path.name, line=1, claim="c", suggested_fix=fix, severity="minor"
        )

    @unittest.skipIf(sys.platform == "win32", "chmod 0o000 does not block reads on Windows")
    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores the read bit")
    def test_cannot_read_redacts_the_path_in_the_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / _TOKEN_DIR
            root.mkdir()
            target = root / "t.py"
            target.write_text("x = 1\n", encoding="utf-8")
            target.chmod(0o000)
            try:
                ok, message = apply_patch_suggestion(self._suggestion(target), root_dir=root)
            finally:
                target.chmod(0o644)

            self.assertFalse(ok)
            self.assertIn("Cannot read t.py", message)
            self.assertIn("[REDACTED:github_token]", message)
            self.assertNotIn(_TOKEN_DIR, message)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores the write bit")
    def test_cannot_write_redacts_the_path_in_the_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / _TOKEN_DIR
            root.mkdir()
            target = root / "t.py"
            target.write_text("x = 1\n", encoding="utf-8")
            target.chmod(0o444)
            try:
                ok, message = apply_patch_suggestion(self._suggestion(target), root_dir=root)
            finally:
                target.chmod(0o644)

            self.assertFalse(ok)
            self.assertIn("Cannot write t.py", message)
            self.assertIn("[REDACTED:github_token]", message)
            self.assertNotIn(_TOKEN_DIR, message)

    @unittest.skipIf(sys.platform == "win32", "chmod 0o000 does not block reads on Windows")
    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores the read bit")
    def test_an_unreadable_report_redacts_the_path_in_the_exception(self):
        """`cli.py`'s `apply --report` read arm."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / _TOKEN_DIR
            folder.mkdir()
            report = folder / "report.md"
            report.write_text("# report\n", encoding="utf-8")
            report.chmod(0o000)

            err = io.StringIO()
            try:
                with patch.object(sys, "stderr", err):
                    code = main(["apply", "--report", str(report)])
            finally:
                report.chmod(0o644)

            self.assertEqual(code, 2)
            # The prefix quotes the argument as given — the user's own input —
            # so the token appears exactly once there and nowhere else.
            text = err.getvalue()
            self.assertIn("Error reading report file", text)
            self.assertIn("[REDACTED:github_token]", text)
            self.assertEqual(text.count(_TOKEN_DIR), 1)


if __name__ == "__main__":
    unittest.main()
