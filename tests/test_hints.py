"""Unit tests for static analysis hints pre-pass (issue #523, #737)."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_jury.cli import main  # noqa: E402
from ai_jury.hints import collect_static_hints  # noqa: E402


def _recorder(calls, returncode=0, stdout=""):
    """A ``subprocess.run`` stand-in that records each argv it is handed."""

    def run(cmd, **_kwargs):
        calls.append(list(cmd))
        return MagicMock(returncode=returncode, stdout=stdout)

    return run


def _which_everything(name, *_args, **_kwargs):
    return "/usr/bin/" + name


class HintsTests(unittest.TestCase):
    def test_collect_static_hints_empty_when_no_linters(self):
        # Both languages are in the change; neither linter is installed.
        with patch("shutil.which", return_value=None):
            hints = collect_static_hints(
                ["src/ai_jury/config.py", "web/app.ts"], root_dir=REPO_ROOT
            )
            self.assertEqual(hints, "")

    def test_collect_static_hints_with_ruff_output(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = "src/main.py:10:1: F401 `os` imported but unused\nsrc/main.py:12:1: E501 line too long\n"

        with (
            patch(
                "shutil.which",
                side_effect=lambda name: "/usr/bin/" + name if name == "ruff" else None,
            ),
            patch("subprocess.run", return_value=mock_proc),
        ):
            hints = collect_static_hints(files=["src/main.py"], root_dir=REPO_ROOT)
            self.assertIn("Python linter (Ruff) warnings:", hints)
            self.assertIn("F401", hints)
            self.assertIn("Static Analysis Hints", hints)

    def test_collect_static_hints_with_ruff_clean(self):
        mock_clean = MagicMock(returncode=0, stdout="")
        with (
            patch(
                "shutil.which",
                side_effect=lambda name: "/usr/bin/" + name if name == "ruff" else None,
            ),
            patch("subprocess.run", return_value=mock_clean),
        ):
            hints = collect_static_hints(files=["src/main.py"], root_dir=REPO_ROOT)
            self.assertEqual(hints, "")

    def test_collect_static_hints_with_eslint_output(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = "src/app.ts: line 5, col 10, Error - Unexpected any (no-explicit-any)\n"

        with (
            patch(
                "shutil.which",
                side_effect=lambda name: "/usr/bin/" + name if name == "npx" else None,
            ),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", return_value=mock_proc),
        ):
            hints = collect_static_hints(files=["src/app.ts"], root_dir=REPO_ROOT)
            self.assertIn("JS/TS linter (ESLint) warnings:", hints)
            self.assertIn("no-explicit-any", hints)

    def test_collect_static_hints_with_eslint_clean_or_no_js(self):
        mock_clean = MagicMock(returncode=0, stdout="")
        with (
            patch(
                "shutil.which",
                side_effect=lambda name: "/usr/bin/" + name if name == "npx" else None,
            ),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", return_value=mock_clean),
        ):
            hints = collect_static_hints(files=["src/main.py"], root_dir=REPO_ROOT)
            self.assertEqual(hints, "")

    def test_collect_static_hints_without_a_package_json(self):
        # ESLint is only meaningful in a JS project, even when npx is installed.
        calls: list[list[str]] = []
        with (
            patch("shutil.which", side_effect=_which_everything),
            patch("pathlib.Path.exists", return_value=False),
            patch("subprocess.run", side_effect=_recorder(calls, returncode=1, stdout="boom\n")),
        ):
            hints = collect_static_hints(files=["src/app.ts"], root_dir=REPO_ROOT)
        self.assertEqual(hints, "")
        self.assertEqual(calls, [])

    def test_a_failing_linter_with_no_output_produces_no_block(self):
        # A linter that exits nonzero but says nothing on stdout (it crashed, or
        # only wrote to stderr) contributes nothing rather than an empty heading.
        with (
            patch("shutil.which", side_effect=_which_everything),
            patch("subprocess.run", return_value=MagicMock(returncode=2, stdout="  \n")),
        ):
            hints = collect_static_hints(files=["src/main.py"], root_dir=REPO_ROOT)
        self.assertEqual(hints, "")

    def test_collect_static_hints_exception_tolerance(self):
        with (
            patch("shutil.which", return_value="/usr/bin/ruff"),
            patch("subprocess.run", side_effect=RuntimeError("boom")),
        ):
            hints = collect_static_hints(files=["src/main.py"], root_dir=REPO_ROOT)
            self.assertEqual(hints, "")

        with (
            patch(
                "shutil.which",
                side_effect=lambda name: "/usr/bin/" + name if name == "npx" else None,
            ),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", side_effect=RuntimeError("boom")),
        ):
            hints = collect_static_hints(files=["src/app.js"], root_dir=REPO_ROOT)
            self.assertEqual(hints, "")


class ChangedFilesOnlyTests(unittest.TestCase):
    """The pre-pass lints the change under review and nothing else (issue #737).

    ``collect_static_hints()`` used to be callable with no arguments, and both
    linter branches then fell through to their whole-tree form (``ruff check
    … -- .``) rooted at the process working directory. The panel was shown the
    first five diagnostics from anywhere in the repository under a heading that
    said "modified files". There is no whole-tree form any more: the paths are a
    required argument, and no argument reinstates it.
    """

    def test_the_changed_files_are_required(self):
        with self.assertRaises(TypeError):
            collect_static_hints()

    def test_no_changed_files_runs_no_linter(self):
        # `None` used to mean "the whole working tree". It now means "nothing".
        calls: list[list[str]] = []
        with (
            patch("shutil.which", side_effect=_which_everything),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", side_effect=_recorder(calls, returncode=1, stdout="boom\n")),
        ):
            self.assertEqual(collect_static_hints(None, root_dir=REPO_ROOT), "")
            self.assertEqual(collect_static_hints([], root_dir=REPO_ROOT), "")
        self.assertEqual(calls, [])

    def test_a_change_touching_no_linted_language_emits_no_block(self):
        calls: list[list[str]] = []
        with (
            patch("shutil.which", side_effect=_which_everything),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", side_effect=_recorder(calls, returncode=1, stdout="boom\n")),
        ):
            hints = collect_static_hints(
                ["README.md", "docs/configuration.md", "Makefile"], root_dir=REPO_ROOT
            )
        self.assertEqual(hints, "")
        self.assertEqual(calls, [])

    def test_linters_are_given_only_the_changed_files(self):
        calls: list[list[str]] = []
        changed = ["src/changed.py", "web/changed.ts", "docs/untouched-by-linters.md"]
        with (
            patch("shutil.which", side_effect=_which_everything),
            patch("pathlib.Path.exists", return_value=True),
            patch(
                "subprocess.run",
                side_effect=_recorder(calls, returncode=1, stdout="src/changed.py:1:1: F401 x\n"),
            ),
        ):
            hints = collect_static_hints(changed, root_dir=REPO_ROOT)

        ruff = next(argv for argv in calls if argv[0] == "ruff")
        eslint = next(argv for argv in calls if argv[0] == "npx")
        self.assertEqual(ruff[ruff.index("--") + 1 :], ["src/changed.py"])
        self.assertEqual(eslint[eslint.index("--") + 1 :], ["web/changed.ts"])
        # The whole-tree argument, in either spelling, is what #737 removed.
        for argv in calls:
            self.assertNotIn(".", argv)
            self.assertNotIn("./", argv)
        self.assertIn("files changed by this diff", hints)

    def test_flag_shaped_and_empty_paths_are_dropped(self):
        # A path starting with "-" would be read as a linter flag, not a file.
        calls: list[list[str]] = []
        with (
            patch("shutil.which", side_effect=_which_everything),
            patch(
                "subprocess.run", side_effect=_recorder(calls, returncode=1, stdout="x.py:1:1\n")
            ),
        ):
            collect_static_hints(["", "--fix", "src/main.py"], root_dir=REPO_ROOT)
        ruff = next(argv for argv in calls if argv[0] == "ruff")
        self.assertEqual(ruff[ruff.index("--") + 1 :], ["src/main.py"])
        self.assertNotIn("--fix", ruff)


HINTS_ON_TOML = """
[jury]
rounds = 1
verify = false
chair = "a"
hints = true

[[agent]]
name = "a"
vendor = "anthropic"
command = "claude"
"""


class HintsScopeEndToEndTests(unittest.TestCase):
    """The injected block names only files in the diff (issue #737).

    The CLI used to call ``collect_static_hints()`` with no arguments, so the
    panel was shown lint from files that are not in the change at all. These
    tests record the argv each linter is handed during a real ``--mock`` run.
    """

    DIFF = (
        "diff --git a/pkg/changed.py b/pkg/changed.py\n@@ -0,0 +1 @@\n+x = 1\n"
        "diff --git a/vendored/also_changed.py b/vendored/also_changed.py\n@@ -0,0 +1 @@\n+y = 2\n"
        "diff --git a/docs/readme.md b/docs/readme.md\n@@ -0,0 +1 @@\n+hello\n"
    )
    MD_ONLY_DIFF = "diff --git a/docs/readme.md b/docs/readme.md\n@@ -0,0 +1 @@\n+hello\n"

    def _linter_calls(self, diff, argv, **recorder):
        """Run the CLI on *diff* and return the argv of each linter invocation."""
        calls: list[list[str]] = []
        prev_stdin = sys.stdin
        sys.stdin = io.StringIO(diff)
        try:
            with (
                patch("shutil.which", side_effect=_which_everything),
                patch("subprocess.run", side_effect=_recorder(calls, **recorder)),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                code = main(argv)
        finally:
            sys.stdin = prev_stdin
        self.assertEqual(code, 0)
        return [call for call in calls if call[0] in ("ruff", "npx")]

    def test_the_cli_lints_only_the_files_in_the_diff(self):
        calls = self._linter_calls(
            self.DIFF,
            ["--mock", "--diff-file", "-", "--hints"],
            returncode=1,
            stdout="pkg/changed.py:1:1: F401 `os` imported but unused\n",
        )
        self.assertEqual([call[0] for call in calls], ["ruff"])
        ruff = calls[0]
        self.assertEqual(
            ruff[ruff.index("--") + 1 :], ["pkg/changed.py", "vendored/also_changed.py"]
        )
        # The whole-tree argument is what #737 removed.
        self.assertNotIn(".", ruff)

    def test_the_cli_honours_the_diff_filters(self):
        # A file the panel never sees ([jury.diff] exclude) must not be linted
        # either: the hints describe the diff under review, not the checkout.
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "jury.toml"
            cfg.write_text(
                HINTS_ON_TOML + '\n[jury.diff]\nexclude = ["vendored/**"]\n', encoding="utf-8"
            )
            calls = self._linter_calls(
                self.DIFF,
                ["--mock", "--diff-file", "-", "--config", str(cfg)],
                returncode=1,
                stdout="pkg/changed.py:1:1: F401 x\n",
            )
        self.assertEqual([call[0] for call in calls], ["ruff"])
        ruff = calls[0]
        self.assertEqual(ruff[ruff.index("--") + 1 :], ["pkg/changed.py"])

    def test_a_diff_with_no_linted_language_injects_nothing(self):
        calls = self._linter_calls(
            self.MD_ONLY_DIFF,
            ["--mock", "--diff-file", "-", "--hints"],
            returncode=1,
            stdout="anything at all\n",
        )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
