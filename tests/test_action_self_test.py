"""CI runs the GitHub Action itself on all three runner OSes (#900).

Before this job no workflow ran the composite Action. The rest of this suite
reads `action.yml` in pieces, and #898's install step failed on every
windows-latest run (a `\\r` from `print()` reached pip) with all of it green.
The `action-self-test` job in `ci.yml` runs the Action from the pull request's
own tree, offline and with no secrets, and asserts that the step exits 0, that
the package it installs is the version the Action's own `pyproject.toml`
declares, and that a hostile `version:` is refused by the Action's validator
before pip runs.

The Action is checked out into a subdirectory and run from there, beside a
workspace whose `pyproject.toml` declares another version (#909). Run as
`uses: ./`, the Action's path and the workspace were one directory, so an Action
that read the caller's `pyproject.toml` instead of its own passed the job.

GitHub Actions cannot run here, so these tests pin the job's shape: each
property the job depends on is asserted against the file's text, and each
fails as an assertion — not an exception — when its piece is taken out. Read
without a YAML parser for the reason `test_github_action.py` gives: the package
declares no dependencies, and a test is not a reason to add one.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
ACTION = REPO_ROOT / "action.yml"
SPEC_SCRIPT = REPO_ROOT / "scripts" / "action_install_spec.py"
JOB = "action-self-test"

#: A step that runs an action from the workspace, `uses: ./<dir>` or `uses: ./`.
_LOCAL_ACTION = re.compile(r"^\s*(?:-\s+)?uses:\s*\./\S*\s*$", re.M)
#: The line of a `run:` script that writes the workspace's own pyproject.toml.
_DECOY = re.compile(r"^\s*printf '(.*)' > pyproject\.toml\s*$", re.M)
#: The file a hostile run's `BASH_ENV` script sends that run's stderr to.
_STDERR_TO = re.compile(r"^\s*printf 'exec 2>>(\S+)\\n' > (\S+)\s*$", re.M)
#: A third-party action pinned to a full commit SHA.
_SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w.-]+)*@[0-9a-f]{40}$")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def job_lines(text: str, name: str = JOB) -> list[str]:
    """The lines of one top-level job, comment lines removed; empty if absent.

    Comments are dropped so a sentence that mentions `continue-on-error` or a
    secret is never read as the key itself.
    """
    lines = text.splitlines()
    if f"  {name}:" not in lines:
        return []
    body: list[str] = []
    for line in lines[lines.index(f"  {name}:") + 1 :]:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped and _indent(line) <= 2:
            break
        body.append(line)
    return body


def steps_of(body: list[str]) -> list[str]:
    """Each step of the job as one block of text, in file order."""
    if "    steps:" not in body:
        return []
    chunks: list[list[str]] = []
    for line in body[body.index("    steps:") + 1 :]:
        if line.startswith("      - "):
            chunks.append([line])
        elif chunks:
            chunks[-1].append(line)
    return ["\n".join(chunk) for chunk in chunks]


def value(step: str, key: str) -> str | None:
    """The scalar a step gives `key`, unquoted; ``None`` when it gives none."""
    match = re.search(rf"^\s*(?:-\s+)?{re.escape(key)}:[ \t]*(.*?)[ \t]*$", step, re.M)
    if not match:
        return None
    raw = match.group(1)
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1].replace("''", "'")
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return raw[1:-1]
    return re.sub(r"\s+#.*$", "", raw)


def _load_spec_module():
    spec = importlib.util.spec_from_file_location("action_install_spec", SPEC_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Job(unittest.TestCase):
    def setUp(self):
        self.text = CI.read_text(encoding="utf-8")
        self.body = job_lines(self.text)
        self.job = "\n".join(self.body)
        self.steps = steps_of(self.body)
        self.local = [step for step in self.steps if _LOCAL_ACTION.search(step)]

    def _happy(self) -> str:
        """The Action run that must succeed: a local `uses:` without continue-on-error."""
        found = [step for step in self.local if value(step, "continue-on-error") is None]
        self.assertEqual(len(found), 1, f"expected one plain local `uses:` step, found {found}")
        return found[0]

    def _hostile(self) -> str:
        """The Action run that must fail: a local `uses:` carrying `continue-on-error`."""
        found = [step for step in self.local if value(step, "continue-on-error") is not None]
        self.assertEqual(
            len(found), 1, f"expected one local `uses:` step expected to fail, found {found}"
        )
        return found[0]

    def _action_dir(self) -> str:
        """Where the job checks the Action out, relative to the workspace."""
        checkout = [s for s in self.steps if "actions/checkout@" in s]
        self.assertEqual(len(checkout), 1, "the job must check the Action out once")
        path = (value(checkout[0], "path") or "").strip("/")
        self.assertNotIn(
            path,
            ("", "."),
            "the Action is checked out at the workspace root, so its own path and the "
            "workspace are one directory and reading the wrong pyproject.toml passes",
        )
        return path

    def _version_check(self) -> str:
        checks = [s for s in self._after(self._happy()) if "jury --version" in s]
        self.assertEqual(len(checks), 1, "no step after the Action reads `jury --version`")
        return checks[0]

    def _after(self, step: str) -> list[str]:
        return self.steps[self.steps.index(step) + 1 :]


class TheJobRunsTheActionOnEveryRunnerOS(_Job):
    def test_the_job_exists(self):
        self.assertTrue(self.body, f"ci.yml has no `{JOB}` job: nothing runs the Action itself")
        self.assertTrue(self.steps, f"the `{JOB}` job has no steps")

    def test_it_covers_ubuntu_macos_and_windows(self):
        self.assertIn("    runs-on: ${{ matrix.os }}", self.body)
        matrix = [line.strip() for line in self.body if line.strip().startswith("os:")]
        self.assertEqual(len(matrix), 1, f"expected one `os:` matrix axis, found {matrix}")
        oses = {os.strip() for os in matrix[0].split(":", 1)[1].strip(" []").split(",")}
        self.assertEqual(oses, {"ubuntu-latest", "macos-latest", "windows-latest"})

    def test_one_leg_failing_does_not_cancel_the_others(self):
        """A Windows-only defect is the case this job exists for; it must be reported
        alongside the other two legs, not cancel them."""
        self.assertIn("      fail-fast: false", self.body)

    def test_it_runs_the_action_from_this_tree(self):
        self.assertGreaterEqual(len(self.local), 2, "the job must run `uses: ./` (twice)")
        self._happy()
        self._hostile()

    def test_it_sets_a_ceiling(self):
        self.assertTrue(
            any(re.fullmatch(r"    timeout-minutes: \d+", line) for line in self.body),
            "the job sets no timeout-minutes",
        )


class TheJobIsOfflineAndHoldsNoSecrets(_Job):
    def test_it_reads_no_secret(self):
        self.assertTrue(self.body, "no job to check")
        self.assertNotIn("secrets.", self.job)
        self.assertNotIn("api-key:", self.job, "the self-test passes no model key")

    def test_every_action_run_passes_an_empty_token(self):
        """Without it `github-token` defaults to `github.token`."""
        self.assertTrue(self.local, "no `uses: ./` step to check")
        for step in self.local:
            with self.subTest(step=step.splitlines()[0]):
                self.assertEqual(value(step, "github-token"), "")

    def test_every_action_run_is_a_mock_review_of_a_committed_diff(self):
        """On `pull_request` the Action would otherwise run `jury --pr`, which needs
        `gh`, a token and the network."""
        self.assertTrue(self.local, "no `uses: ./` step to check")
        for step in self.local:
            with self.subTest(step=step.splitlines()[0]):
                self.assertIn("--mock", (value(step, "args") or "").split())
                diff = value(step, "diff-file")
                self.assertTrue(diff, "no diff-file: the Action would review the pull request")
                # Relative to the workspace, where the tree sits under the checkout path.
                prefix = f"{self._action_dir()}/"
                self.assertTrue(diff.startswith(prefix), f"{diff} is not under {prefix}")
                in_tree = diff[len(prefix) :]
                self.assertTrue((REPO_ROOT / in_tree).is_file(), f"{in_tree} is not in the tree")

    def test_permissions_are_read_only_contents(self):
        self.assertIn("    permissions:", self.body)
        at = self.body.index("    permissions:")
        granted = []
        for line in self.body[at + 1 :]:
            if line.strip() and _indent(line) <= 4:
                break
            if line.strip():
                granted.append(line.strip())
        self.assertEqual(granted, ["contents: read"])

    def test_the_checkout_keeps_no_credentials(self):
        self.assertTrue(self.steps, "no steps to check")
        checkout = [s for s in self.steps if "actions/checkout@" in s]
        self.assertEqual(len(checkout), 1)
        self.assertEqual(value(checkout[0], "persist-credentials"), "false")

    def test_it_runs_on_pull_requests(self):
        on = self.text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("\n  pull_request:", on)
        self.assertTrue(self.body, "no job to check")
        self.assertFalse(
            [line for line in self.body if line.startswith("    if:")],
            "a job-level `if:` could skip the self-test on pull requests",
        )

    def test_every_third_party_action_is_pinned_by_sha(self):
        uses = [value(step, "uses") for step in self.steps if value(step, "uses")]
        third_party = [u for u in uses if not u.startswith("./")]
        self.assertTrue(third_party, "no third-party action found in the job")
        for ref in third_party:
            with self.subTest(ref=ref):
                self.assertRegex(ref, _SHA_PIN)


class TheInstalledVersionIsThePin(_Job):
    def test_the_happy_path_takes_the_production_install(self):
        """No `version:`: the default pin from `$GITHUB_ACTION_PATH/pyproject.toml`
        is the path under test, not an override."""
        self.assertIsNone(value(self._happy(), "version"))

    def test_pip_cannot_reach_an_index_during_the_action(self):
        """So the pin can only be met by this tree, installed beforehand; a pin
        naming any other version fails the step."""
        self.assertEqual(value(self._happy(), "PIP_NO_INDEX"), "1")

    def test_the_actions_tree_is_installed_before_the_action_runs(self):
        """The Action's checkout, not the workspace root: the pin it names must be the
        one pip can satisfy, and the decoy's must not be."""
        happy = self._happy()
        before = self.steps[: self.steps.index(happy)]
        installs = re.compile(rf"pip install \./{re.escape(self._action_dir())}/?\s*$", re.M)
        self.assertTrue(
            any(installs.search(step) for step in before),
            "nothing installs the Action's tree before the Action pins it",
        )
        self.assertFalse(
            [s for s in self.steps if re.search(r"pip install \.\s*$", s, re.M)],
            "the workspace root is installed: its decoy version could satisfy a wrong pin",
        )

    def test_the_action_and_the_job_resolve_the_same_interpreter(self):
        """The pre-install only counts if the Action's `setup-python` lands on it."""
        action = ACTION.read_text(encoding="utf-8")
        action_ref = re.search(r"uses: (actions/setup-python@[0-9a-f]{40})", action)
        action_python = re.search(r'python-version: "([^"]+)"', action)
        job_setup = [s for s in self.steps if "actions/setup-python@" in s]
        self.assertEqual(len(job_setup), 1, "the job must set up Python once")
        self.assertEqual(value(job_setup[0], "uses"), action_ref.group(1))
        self.assertEqual(value(job_setup[0], "python-version"), action_python.group(1))

    def test_the_installed_version_is_compared_with_the_actions_pyproject(self):
        check = self._version_check()
        self.assertIn('["project"]["version"]', check, "the expectation is not pyproject's")
        self.assertIn(
            f'expected="$(version_of {self._action_dir()}/pyproject.toml)"',
            check,
            "the expected version is not read from the Action's own pyproject.toml",
        )
        self.assertIn('if [ "$installed" != "jury $expected" ]; then', check)
        self.assertIn("exit 1", check)

    def test_the_action_is_shown_to_have_pinned_it(self):
        """`jury --version` alone passes an unpinned `pip install ai-jury` too, since
        the tree is already installed; the pip log records the requirement."""
        log = value(self._happy(), "PIP_LOG")
        self.assertTrue(log, "the Action's pip call writes no log to check")
        self.assertIn(f'grep -qF "ai-jury==$expected in " {log}', self._version_check())

    def test_the_happy_path_may_not_fail_quietly(self):
        """Only the hostile run may carry `continue-on-error`."""
        self.assertTrue(self.steps, "no steps to check")
        tolerant = [s for s in self.steps if value(s, "continue-on-error") is not None]
        self.assertEqual(tolerant, [self._hostile()])
        self.assertNotIn("    continue-on-error:", self.body)


class TheActionRunsBesideAWorkspaceThatDeclaresAnotherVersion(_Job):
    """`uses: ./` made `$GITHUB_ACTION_PATH` the workspace, so a read of the wrong
    pyproject.toml pinned the right version (#909). Each piece that keeps the two
    apart is pinned here."""

    def test_the_action_is_checked_out_into_a_subdirectory(self):
        self._action_dir()

    def test_every_action_run_uses_that_subdirectory(self):
        """A run left on `uses: ./` would run a workspace with no action.yml at all,
        or, with a root checkout, the one layout that cannot tell the paths apart."""
        self.assertTrue(self.local, "no local `uses:` step to check")
        where = f"./{self._action_dir()}"
        for step in self.local:
            with self.subTest(step=step.splitlines()[0]):
                self.assertEqual(value(step, "uses"), where)

    def _decoy(self) -> tuple[str, dict]:
        happy = self._happy()
        written = [
            (step, _DECOY.search(step))
            for step in self.steps[: self.steps.index(happy)]
            if _DECOY.search(step)
        ]
        self.assertEqual(
            len(written), 1, "no step before the Action writes a pyproject.toml of the workspace's"
        )
        step, match = written[0]
        text = match.group(1).replace("\\n", "\n")
        return step, tomllib.loads(text)

    def test_the_workspace_declares_another_version(self):
        _, decoy = self._decoy()
        own = tomllib.loads(REPO_ROOT.joinpath("pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("version", decoy.get("project", {}), "the decoy declares no version")
        self.assertNotEqual(
            decoy["project"]["version"],
            own["project"]["version"],
            "the workspace declares the Action's own version, so the paths cannot be told apart",
        )

    def test_the_decoy_would_be_pinned_by_a_wrong_read(self):
        """A well-formed pin, so a regression fails at pip, as it would for a caller,
        rather than at the validator for some unrelated reason."""
        _, decoy = self._decoy()
        module = _load_spec_module()
        text = f'[project]\nversion = "{decoy["project"]["version"]}"\n'
        self.assertEqual(module.requirement("", text), f"ai-jury=={decoy['project']['version']}")

    def test_the_check_refuses_a_workspace_on_the_actions_version(self):
        """The job itself fails if the two versions ever meet, not only this suite."""
        check = self._version_check()
        self.assertIn('workspace="$(version_of pyproject.toml)"', check)
        guard = check.split('if [ "$workspace" = "$expected" ]; then', 1)
        self.assertEqual(len(guard), 2, "the check does not compare the two versions")
        self.assertIn("exit 1", guard[1].split("fi", 1)[0])

    def test_the_pip_log_names_no_pin_of_the_workspace_version(self):
        log = value(self._happy(), "PIP_LOG")
        check = self._version_check()
        guard = check.split(f'if grep -qF "ai-jury==$workspace" {log}; then', 1)
        self.assertEqual(len(guard), 2, "the pip log is not searched for the workspace's pin")
        self.assertIn("exit 1", guard[1].split("fi", 1)[0])


class AHostileVersionFailsBeforePip(_Job):
    def test_the_hostile_value_would_be_shell_code(self):
        version = value(self._hostile(), "version")
        self.assertTrue(version, "the hostile run passes no version")
        for piece in ('"', ";", " "):
            self.assertIn(piece, version)

    def test_the_install_spec_refuses_the_hostile_value(self):
        """The value really is one the Action rejects, so `failure` is the right outcome."""
        module = _load_spec_module()
        with self.assertRaises(module.SpecError):
            module.requirement(value(self._hostile(), "version") or "", None)

    def test_it_is_expected_to_fail(self):
        self.assertEqual(value(self._hostile(), "continue-on-error"), "true")

    def _assertion(self) -> tuple[str, str]:
        hostile = self._hostile()
        ident = value(hostile, "id")
        self.assertTrue(ident, "the hostile run has no id, so its outcome cannot be read")
        expression = f"${{{{ steps.{ident}.outcome }}}}"
        found = [s for s in self._after(hostile) if expression in s]
        self.assertEqual(len(found), 1, f"no later step reads {expression}")
        return hostile, found[0]

    def test_a_later_step_fails_unless_the_outcome_is_failure(self):
        _, check = self._assertion()
        variable = re.search(r"^\s+(\w+): \$\{\{ steps\.\w+\.outcome \}\}", check, re.M)
        self.assertTrue(variable, "the outcome is not passed through env:")
        self.assertIn(f'if [ "${variable.group(1)}" != "failure" ]; then', check)
        self.assertIn("exit 1", check)

    def test_pip_is_shown_not_to_have_run(self):
        hostile, check = self._assertion()
        log = value(hostile, "PIP_LOG")
        self.assertTrue(log, "the hostile run gives pip no log, so 'pip never ran' is unproven")
        self.assertIn(f"if [ -e {log} ]; then", check)

    def _stderr_log(self) -> str:
        """The log the hostile run's bash steps write their stderr to (#909)."""
        hostile = self._hostile()
        bash_env = value(hostile, "BASH_ENV")
        self.assertTrue(bash_env, "the hostile run keeps no stderr to tell its failure apart")
        before = self.steps[: self.steps.index(hostile)]
        writers = [m for s in before for m in _STDERR_TO.finditer(s) if m.group(2) == bash_env]
        self.assertEqual(len(writers), 1, f"no earlier step writes {bash_env}")
        return writers[0].group(1)

    def test_only_the_hostile_run_redirects_its_stderr(self):
        self._stderr_log()
        self.assertIsNone(
            value(self._happy(), "BASH_ENV"), "the happy run's errors would be hidden"
        )

    def test_the_failure_is_shown_to_be_the_validators_refusal(self):
        """`failure` is any failure, a broken setup-python too. The validator's own
        message in the run's stderr is what names the install step's refusal."""
        log = self._stderr_log()
        _, check = self._assertion()
        grep = re.search(rf'if ! grep -qF "([^"]+)" {re.escape(log)}; then', check)
        self.assertTrue(grep, f"the check does not look for the validator's message in {log}")
        self.assertIn("exit 1", check.split(grep.group(0), 1)[1].split("fi", 1)[0])
        module = _load_spec_module()
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = module.main({"INPUT_VERSION": value(self._hostile(), "version") or ""})
        self.assertEqual(code, 2)
        self.assertIn(grep.group(1), err.getvalue(), "the grep is not the validator's message")

    def test_the_payload_is_shown_not_to_have_run(self):
        hostile, check = self._assertion()
        marker = re.search(r"> (\S+);", value(hostile, "version") or "")
        self.assertTrue(marker, "the hostile value writes no marker to look for")
        self.assertIn(f"if [ -e {marker.group(1)} ]; then", check)


if __name__ == "__main__":
    unittest.main()
