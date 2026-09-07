"""The install retry, run against a stub installer (#770).

`publish.yml`'s verify job waits for PyPI and then installs. The two reads are
of different surfaces: `wait-for-pypi-dists.sh` polls the **JSON API**, and pip
resolves through the **simple index**, which is served from its own cache and
lags behind it. The step already knew that — it carried the comment "The JSON
API can know a version before the installer index serves it" above a six-attempt
loop — and the loop could not act on it:

    for attempt in 1 2 3 4 5 6; do
      if timeout 90 /tmp/verify-venv/bin/python -m pip install --timeout 30 …

PyPI serves the simple index `cache-control: max-age=600, public`, and pip's HTTP
cache honours a fresh response without revalidating. Attempt 1, made during the
lag, records the version list that is missing the release; attempts 2 to 6 read
it back off local disk without opening a connection. Fifty seconds of retrying
against an answer that stays fresh for six hundred.

That is worse than no retry, because it made the job look protected. The sibling
repository hit the same lag on a real release, failed its verify job and filed a
`release-broken` issue automatically against a release that was fine.

So `.github/scripts/pip-install-with-retry.sh` passes `--no-cache-dir`, and takes
its budget from `PYPI_ATTEMPTS`/`PYPI_INTERVAL_SECONDS` — the same two variables
`wait-for-pypi-dists.sh` reads, so the two waits cannot be given different
patience by an edit that remembers only one of them.

A release can be cut once, so the workflow cannot be tested; the shell in it can.
Every test below runs the **real script** under real `bash` against a stub
installer that fails N times and then succeeds, and a stub `sleep` that records
the wait rather than taking it.

Hermetic and instant: no network, no PyPI, no pip, no wall-clock sleeping — the
recorded sleeps are what prove the interval was honoured, which a clock could
only have proved by paying for it.

`publish.yml` runs on `ubuntu-latest` and nowhere else, so a Windows runner has
nothing to say about the shell in it. `TheScriptIsReadableWithoutAShell` reads
the file as text and runs everywhere.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".github" / "scripts" / "pip-install-with-retry.sh"

#: A version that does not exist, so a test that somehow escaped the stub would
#: fail rather than quietly reach the real index.
REQUIREMENT = "ai-jury==9.9.9"

POSIX_SHELL = sys.platform != "win32" and shutil.which("bash") is not None

#: pip's own words when an index has not caught up. Kept verbatim so the
#: assertion that the installer's output survives into the log is about the text
#: a maintainer reading a failed release would actually be looking for.
PIP_MISS = (
    f"ERROR: Could not find a version that satisfies the requirement {REQUIREMENT} "
    "(from versions: 1.16.0, 1.17.0)"
)

STUB_INSTALLER = f"""#!/usr/bin/env bash
# Fails its first $FAILURES calls the way pip fails against a lagging index, then
# succeeds. Records every call so the harness counts attempts rather than
# inferring them from timing.
set -u
calls="$(cat "$COUNTER" 2>/dev/null || echo 0)"
calls=$((calls + 1))
printf '%s\\n' "$calls" > "$COUNTER"
printf '%s\\n' "$*" >> "$ARGV"
if [ "$calls" -le "$FAILURES" ]; then
  echo "{PIP_MISS}"
  echo "ERROR: No matching distribution found for {REQUIREMENT}"
  exit 1
fi
echo "Successfully installed {REQUIREMENT.replace("==", "-")}"
exit 0
"""

STUB_SLEEP = """#!/usr/bin/env bash
# Records the wait instead of taking it.
set -u
printf '%s\\n' "$1" >> "$SLEEPS"
exit 0
"""

#: `timeout(1)` is GNU coreutils and macOS ships neither it nor `gtimeout`, so
#: the per-attempt bound cannot be exercised with the real tool on every runner.
#: This records the duration it was given and then runs the command, which is
#: what the assertions are actually about: that the attempt goes *through* a
#: bound, and with which number.
STUB_TIMEOUT = """#!/usr/bin/env bash
set -u
printf '%s\\n' "$1" >> "$TIMEOUTS"
shift
exec "$@"
"""


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


class Run:
    """One invocation of the script, with what the stubs saw."""

    def __init__(
        self,
        completed,
        calls: int,
        argv: list[str],
        sleeps: list[str],
        timeouts: list[str] | None = None,
    ):
        self.completed = completed
        self.calls = calls
        self.argv = argv
        self.sleeps = sleeps
        self.timeouts = timeouts or []

    @property
    def code(self) -> int:
        return self.completed.returncode

    @property
    def output(self) -> str:
        return self.completed.stdout + self.completed.stderr


@unittest.skipUnless(POSIX_SHELL, "needs bash on a POSIX platform")
class AgainstAStubInstaller(unittest.TestCase):
    """Runs the real script against scripted stubs. No tests in here."""

    def run_install(
        self,
        *,
        failures: int = 0,
        attempts: str | None = None,
        seconds: str = "10",
        installer: str | None = None,
        requirement: str | None = REQUIREMENT,
        attempt_timeout: str = "0",
        stub_timeout: bool = False,
    ) -> Run:
        workdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, workdir, True)
        binaries = workdir / "bin"
        binaries.mkdir()

        counter, argv, sleeps = workdir / "calls", workdir / "argv", workdir / "sleeps"
        timeouts = workdir / "timeouts"
        stub = binaries / "stub-pip"
        stub.write_text(STUB_INSTALLER, encoding="utf-8")
        stub.chmod(0o755)
        # Prepended, not replacing: `date` and `seq` still resolve from the real
        # PATH, so only the wait is stubbed out.
        nap = binaries / "sleep"
        nap.write_text(STUB_SLEEP, encoding="utf-8")
        nap.chmod(0o755)
        if stub_timeout:
            bound = binaries / "timeout"
            bound.write_text(STUB_TIMEOUT, encoding="utf-8")
            bound.chmod(0o755)

        env = {
            **os.environ,
            "PATH": f"{binaries}{os.pathsep}{os.environ.get('PATH', '')}",
            "COUNTER": str(counter),
            "ARGV": str(argv),
            "SLEEPS": str(sleeps),
            "TIMEOUTS": str(timeouts),
            "FAILURES": str(failures),
            # `0` by default: these run on macOS too, which ships no `timeout`,
            # and the script refuses a bound it cannot apply rather than
            # skipping it. The class below asks for one explicitly.
            "PYPI_ATTEMPT_TIMEOUT": attempt_timeout,
            "PYPI_INTERVAL_SECONDS": seconds,
            "INSTALLER": str(stub) if installer is None else installer,
        }
        env.pop("PYPI_ATTEMPTS", None)
        if requirement is not None:
            env["REQUIREMENT"] = requirement
        else:
            env.pop("REQUIREMENT", None)
        if attempts is not None:
            env["PYPI_ATTEMPTS"] = attempts

        completed = subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return Run(
            completed,
            int(counter.read_text(encoding="utf-8").strip()) if counter.exists() else 0,
            _lines(argv),
            _lines(sleeps),
            _lines(timeouts),
        )


class EveryAttemptBypassesPipsHttpCache(AgainstAStubInstaller):
    """The line that makes every other line here mean something.

    Without `--no-cache-dir` the loop reads attempt 1's answer back off disk for
    the next ten minutes, so it retries its own answer. This is the assertion
    the old inline loop would have failed.
    """

    def test_every_attempt_carries_no_cache_dir(self):
        run = self.run_install(failures=3, attempts="5")

        self.assertEqual(run.code, 0)
        self.assertEqual(len(run.argv), 4, run.argv)
        for call in run.argv:
            self.assertIn("--no-cache-dir", call)

    def test_the_requirement_is_passed_through_unchanged(self):
        run = self.run_install()

        self.assertTrue(run.argv[0].endswith(REQUIREMENT), run.argv)


class EachAttemptIsBoundedAndNotOnlyTheLoop(AgainstAStubInstaller):
    """The loop this replaced wrapped every attempt in `timeout 90`.

    Keeping only an outer bound on the whole loop would have been a regression
    dressed as a simplification: one pip that connects and then stops writing
    spends the entire budget, and the other twenty-nine attempts never happen.
    pip's own `--timeout` cannot stand in for it — that bounds one quiet read,
    not the call.
    """

    def test_every_attempt_goes_through_the_bound(self):
        run = self.run_install(failures=2, attempts="4", attempt_timeout="90", stub_timeout=True)

        self.assertEqual(run.code, 0)
        self.assertEqual(run.timeouts, ["90", "90", "90"])

    def test_zero_asks_for_no_bound_and_gets_none(self):
        run = self.run_install(attempt_timeout="0", stub_timeout=True)

        self.assertEqual(run.code, 0)
        self.assertEqual(run.timeouts, [], "0 must not reach `timeout` at all")

    def test_a_bound_that_cannot_be_applied_is_refused_not_skipped(self):
        """A bound quietly absent is worse than one nobody asked for."""
        run = self.run_install(attempt_timeout="90", stub_timeout=False)

        self.assertEqual(run.code, 2)
        self.assertEqual(run.calls, 0)
        self.assertIn("PYPI_ATTEMPT_TIMEOUT", run.output)
        self.assertIn("timeout(1)", run.output)

    def test_a_bound_that_is_not_a_number_names_the_knob(self):
        run = self.run_install(attempt_timeout="ninety")

        self.assertEqual(run.code, 2)
        self.assertIn("PYPI_ATTEMPT_TIMEOUT", run.output)


class AnIndexThatIsReadyIsInstalledFromOnce(AgainstAStubInstaller):
    def test_one_call_and_no_waiting(self):
        run = self.run_install()

        self.assertEqual(run.code, 0)
        self.assertEqual(run.calls, 1)
        self.assertEqual(run.sleeps, [], "a ready index must not be waited for")
        self.assertIn("on attempt 1/", run.output)


class AnIndexThatLagsIsWaitedOutRatherThanFailed(AgainstAStubInstaller):
    def test_it_retries_until_the_index_answers(self):
        run = self.run_install(failures=2, attempts="6")

        self.assertEqual(run.code, 0)
        self.assertEqual(run.calls, 3)
        self.assertIn("on attempt 3/6", run.output)

    def test_the_interval_is_honoured_between_attempts_and_not_after_the_last(self):
        """Two waits for three attempts. A trailing sleep only delays a result."""
        run = self.run_install(failures=2, attempts="6", seconds="10")

        self.assertEqual(run.sleeps, ["10", "10"])

    def test_pips_own_words_survive_into_the_log(self):
        """The `release-broken` report quotes this log; a captured attempt is mute."""
        run = self.run_install(failures=1, attempts="3")

        self.assertIn(PIP_MISS, run.output)


class ASpentBudgetStillFailsTheJob(AgainstAStubInstaller):
    """It is a wait, not a softener."""

    def test_a_budget_that_never_succeeds_exits_one(self):
        run = self.run_install(failures=99, attempts="4")

        self.assertEqual(run.code, 1)
        self.assertEqual(run.calls, 4)
        self.assertEqual(run.sleeps, ["10", "10", "10"], "no sleep after the last attempt")

    def test_the_error_names_the_requirement_and_the_budget(self):
        run = self.run_install(failures=99, attempts="4")

        self.assertIn("::error::", run.output)
        self.assertIn(REQUIREMENT, run.output)
        self.assertIn("4 attempts", run.output)


class AMistakeInTheCallIsDiagnosedAndNotPolled(AgainstAStubInstaller):
    """Exit 2, distinct from the exhausted-budget 1.

    A budget spent waiting for something nobody asked for, ending in an error
    that blames PyPI, is the second failure mode this script exists to avoid —
    the rule `wait-for-pypi-dists.sh` already follows for its own knobs.
    """

    def test_a_missing_requirement_is_refused_before_any_waiting(self):
        run = self.run_install(requirement=None)

        self.assertEqual(run.code, 2)
        self.assertEqual(run.calls, 0)
        self.assertEqual(run.sleeps, [])
        self.assertIn("REQUIREMENT is empty", run.output)

    def test_an_installer_that_cannot_run_is_not_a_slow_index(self):
        run = self.run_install(installer="/nonexistent/pip")

        self.assertEqual(run.code, 2)
        self.assertIn("not an executable", run.output)

    def test_a_budget_that_is_not_a_number_names_the_knob(self):
        run = self.run_install(attempts="lots")

        self.assertEqual(run.code, 2)
        self.assertIn("PYPI_ATTEMPTS", run.output)
        self.assertEqual(run.calls, 0)

    def test_zero_attempts_names_the_knob_rather_than_blaming_pypi(self):
        run = self.run_install(attempts="0")

        self.assertEqual(run.code, 2)
        self.assertIn("at least 1", run.output)

    def test_a_zero_padded_count_is_read_as_base_ten(self):
        """`08` is a valid attempt count and an invalid octal literal."""
        run = self.run_install(failures=99, attempts="08")

        self.assertEqual(run.code, 1)
        self.assertEqual(run.calls, 8)


class TheScriptIsReadableWithoutAShell(unittest.TestCase):
    """Text assertions, so they hold on every runner including Windows."""

    def setUp(self):
        self.source = SCRIPT.read_text(encoding="utf-8")
        # The two assertions below are about what the script *does*. Its own
        # prose explains why it avoids `${{ }}` and the `PIP_` namespace, and a
        # plain search matches that explanation — failing on a comment that says
        # the right thing. Comment lines go first, the way `_code` does it in
        # `test_publish_release_chain.py`.
        self.code = "\n".join(
            line for line in self.source.splitlines() if not line.lstrip().startswith("#")
        )

    def test_it_is_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK), "the workflow runs it directly")

    def test_it_reads_the_same_budget_variables_as_the_index_wait(self):
        """Named once, or the two waits drift — which is how #770 was written."""
        wait = (REPO_ROOT / ".github" / "scripts" / "wait-for-pypi-dists.sh").read_text(
            encoding="utf-8"
        )
        for knob in ("PYPI_ATTEMPTS", "PYPI_INTERVAL_SECONDS"):
            with self.subTest(knob=knob):
                self.assertIn(f'"${{{knob}:-', self.source)
                self.assertIn(f'"${{{knob}:-', wait)

    def test_the_defaults_match_the_index_waits(self):
        self.assertIn('attempts="${PYPI_ATTEMPTS:-30}"', self.source)
        self.assertIn('interval="${PYPI_INTERVAL_SECONDS:-10}"', self.source)

    def test_the_workflow_declares_the_shared_budget_once(self):
        """Sharing the *names* was not enough, which is what the gate found.

        With neither variable set anywhere, each script expanded its own default
        and "the two waits cannot drift" described two copies that happened to
        agree. The verify job declares them, so both scripts read one value.
        """
        workflow = (REPO_ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
        self.assertIn('PYPI_ATTEMPTS: "30"', workflow)
        self.assertIn('PYPI_INTERVAL_SECONDS: "10"', workflow)

    def test_no_input_arrives_through_an_actions_expression(self):
        """`${{ }}` is substituted into the source before bash parses it."""
        self.assertNotIn("${{", self.code)

    def test_it_uses_no_pip_namespaced_variable(self):
        """pip reads every `PIP_<OPTION>` in the environment as one of its flags."""
        self.assertNotIn("PIP_", self.code)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
