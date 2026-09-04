"""The shared PyPI index wait, run against a stub index (#694).

Cutting `v1.16.0`, `publish.yml` uploaded both distributions and then failed in
*Render the Homebrew formula from the published sdist*::

    sdist = next(f for f in urls if f['packagetype'] == 'sdist')
    StopIteration
    curl: (3) URL rejected: Malformed input to a URL function

PyPI had accepted the upload and its version endpoint answered, but the `urls`
array in the answer did not yet list the files. The step's wait polled until the
endpoint answered *at all* — a question that was already true — so it fell
straight through to a read of a file list that was not there. That failure lands
between the upload and the GitHub Release, which is the worst place it can land:
1.16.0 went live on PyPI with no release, hence no
`releases/latest/download/ai-jury.rb`, and the tap correctly kept serving
1.15.1.

`.github/scripts/wait-for-pypi-dists.sh` waits on the file list instead, and is
the single implementation `publish.yml` calls from three steps. A workflow that
only truly runs on a tag cannot be exercised here, but the shell it runs can be:
every test below starts a stub index on loopback whose answers are scripted, so
the convergence the incident hit is reproduced deterministically rather than
waited for.

The last class goes one step further and runs the **render step's own `run:`
body**, lifted out of `publish.yml` rather than copied here, in a sandbox holding
the shared wait and the real formula template — against a stub that answers first
with the empty file list and then with a complete one whose sdist it serves
itself. So the download, the digest check, the `sed` render and the placeholder
check all run, and the failure path is asserted to end in a named `::error::`
rather than a traceback.

Two of the stub's answers are not documents. One is a 404, and one is no answer
at all: the request is accepted and then left open, which is the only way to show
that a bound in *attempts* is not a bound in *time* — and, once the requests
carry `--connect-timeout` and `--max-time`, that the poll gives up anyway.

Hermetic: no network, no PyPI, `PYPI_INTERVAL_SECONDS=0` so the poll costs
nothing, and the interpreter the script shells out to is this one. Everything
except `TheScriptIsShapedToBeShared` runs real `bash` and real `curl` against the
loopback stub; that one class reads the script as text and runs everywhere.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".github" / "scripts" / "wait-for-pypi-dists.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish.yml"
TEMPLATE = REPO_ROOT / "packaging" / "homebrew" / "ai-jury.rb.template"
RENDER_STEP = "Render the Homebrew formula from the published sdist"

#: These tests execute POSIX shell. `publish.yml` runs on `ubuntu-latest` and
#: nowhere else, so a Windows runner has nothing to say about it: Git Bash there
#: is handed Windows paths for both the script and the interpreter, which is a
#: failure of the harness rather than of the thing under test. The static
#: assertions at the bottom of the module still run everywhere.
POSIX_SHELL = sys.platform != "win32" and None not in (
    shutil.which("bash"),
    shutil.which("curl"),
)

#: Stand-in bytes for the published sdist, served by the stub at whatever url
#: it advertises, so the render step's re-download and digest check run for real.
TARBALL = b"not really a tarball, but it has a digest\n"
TARBALL_SHA = hashlib.sha256(TARBALL).hexdigest()

SDIST_URL = "https://files.pythonhosted.org/packages/ab/cd/ai_jury-9.9.9.tar.gz"
SDIST_SHA = "a" * 64
WHEEL_URL = "https://files.pythonhosted.org/packages/ef/01/ai_jury-9.9.9-py3-none-any.whl"


def _file(packagetype: str, url: str, sha: str = "b" * 64) -> dict:
    return {"packagetype": packagetype, "url": url, "digests": {"sha256": sha}}


#: An answer the stub never sends: the request is accepted and then left open,
#: which is the shape a bound in attempts alone does not bound at all.
STALL = object()

BOTH = {"urls": [_file("bdist_wheel", WHEEL_URL), _file("sdist", SDIST_URL, SDIST_SHA)]}
#: What PyPI actually served on 1.16.0: the version exists, the files do not yet.
EMPTY = {"info": {"version": "9.9.9"}, "urls": []}
WHEEL_ONLY = {"urls": [_file("bdist_wheel", WHEEL_URL)]}
#: Complete, and pointing at the stub itself, so a render can download the sdist
#: it names and re-hash it exactly as the workflow step does.
SERVED = {
    "urls": [
        _file("bdist_wheel", WHEEL_URL),
        _file("sdist", "{base}/packages/ai_jury-9.9.9.tar.gz", TARBALL_SHA),
    ]
}


class _Handler(BaseHTTPRequestHandler):
    """Serves the next scripted answer; the last one repeats forever."""

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's spelling
        self.server.paths.append(self.path)
        if self.path.endswith(".tar.gz"):
            # The sdist itself, so the render step's re-download and digest
            # check are exercised against bytes rather than mocked away.
            self._send(200, TARBALL, "application/octet-stream")
            return
        answers = self.server.answers
        answer = answers[0] if len(answers) == 1 else answers.pop(0)
        if answer is None:
            self.send_error(404)
            return
        if answer is STALL:
            # Accepted, and then nothing — no status line, no body. Only a
            # client-side timeout ends this; the connect timeout never fires,
            # because connecting is the one thing that did work. The wait is
            # released by `StubIndex.close`, and the thread is a daemon, so a
            # failing assertion cannot leave the suite hanging on it.
            self.server.release.wait(timeout=120)
            return
        raw = answer if isinstance(answer, bytes) else json.dumps(answer).encode()
        # `{base}` lets an answer point at this very server: the sdist url PyPI
        # reports is not knowable until the stub has bound a port.
        body = raw.replace(b"{base}", self.server.base_url.encode()).strip()
        self._send(200, body, "application/json")

    def _send(self, code: int, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # pragma: no cover - silence the stub
        pass


class StubIndex:
    """A loopback index whose answers are a script, not a fixture."""

    def __init__(self, answers):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.answers = list(answers)
        self.server.paths = []
        self.server.release = threading.Event()
        host, port = self.server.server_address[:2]
        self.server.base_url = f"http://{host}:{port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return self.server.base_url

    @property
    def paths(self) -> list[str]:
        return self.server.paths

    def close(self):
        self.server.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@unittest.skipUnless(POSIX_SHELL, "needs bash and curl on a POSIX platform")
class WaitAgainstStub(unittest.TestCase):
    """Runs the real script against a scripted loopback index. No tests here."""

    def run_wait(
        self,
        answers,
        *,
        attempts=4,
        version: str = "9.9.9",
        max_time: str = "10",
        connect_timeout: str = "5",
        budget: str | None = None,
        python: str | None = None,
    ):
        index = StubIndex(answers)
        self.addCleanup(index.close)
        workdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, workdir, True)
        env = {
            **os.environ,
            "PYPI_PROJECT": "ai-jury",
            "PYPI_VERSION": version,
            "PYPI_BASE_URL": index.base_url,
            "PYPI_ATTEMPTS": str(attempts),
            "PYPI_INTERVAL_SECONDS": "0",
            "PYPI_MAX_TIME": max_time,
            "PYPI_CONNECT_TIMEOUT": connect_timeout,
            "PYPI_PYTHON": python if python is not None else sys.executable,
        }
        if budget is not None:
            env["PYPI_BUDGET_SECONDS"] = budget
        env.pop("GITHUB_REF_NAME", None)
        completed = subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return completed, Path(workdir), index


class TheWaitRunsAgainstAStubIndex(WaitAgainstStub):
    """The extracted shell, executed. Nothing here reaches the real PyPI."""

    def test_it_returns_the_sdist_pair_once_both_distributions_are_listed(self):
        completed, workdir, _ = self.run_wait([BOTH])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        pair = (workdir / "published-sdist.txt").read_text(encoding="utf-8").split()
        self.assertEqual(pair, [SDIST_URL, SDIST_SHA])

    def test_the_pair_is_readable_the_way_the_workflow_reads_it(self):
        """`read -r sdist_url sdist_sha < published-sdist.txt` is the contract."""
        completed, workdir, _ = self.run_wait([BOTH])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        read_back = subprocess.run(
            ["bash", "-c", 'read -r u s < published-sdist.txt; printf "%s|%s" "$u" "$s"'],
            cwd=workdir,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(read_back.stdout, f"{SDIST_URL}|{SDIST_SHA}")

    def test_it_waits_through_the_answer_that_broke_the_release(self):
        """A 200 with an empty `urls` is not convergence; it is what 1.16.0 got."""
        completed, workdir, index = self.run_wait([EMPTY, EMPTY, BOTH])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(len(index.paths), 3)
        self.assertIn("missing sdist bdist_wheel", completed.stdout)
        self.assertTrue((workdir / "published-sdist.txt").exists())

    def test_it_waits_through_a_404_before_the_version_exists(self):
        completed, _, index = self.run_wait([None, BOTH])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(len(index.paths), 2)
        self.assertIn("missing metadata", completed.stdout)

    def test_it_waits_for_the_sdist_when_only_the_wheel_is_indexed(self):
        completed, _, _ = self.run_wait([WHEEL_ONLY, WHEEL_ONLY, BOTH])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("missing sdist", completed.stdout)

    def test_it_asks_for_the_version_endpoint_of_the_project(self):
        _, _, index = self.run_wait([BOTH])
        self.assertEqual(index.paths, ["/pypi/ai-jury/9.9.9/json"])

    def test_a_leading_v_is_stripped_from_the_version(self):
        _, _, index = self.run_wait([BOTH], version="v9.9.9")
        self.assertEqual(index.paths, ["/pypi/ai-jury/9.9.9/json"])


class AnIndexThatNeverConvergesFailsLegibly(WaitAgainstStub):
    """The acceptance criterion: a named missing artifact, not a traceback."""

    def test_it_gives_up_after_the_configured_number_of_attempts(self):
        completed, _, index = self.run_wait([EMPTY], attempts=3)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(len(index.paths), 3)

    def test_the_failure_names_the_version_and_the_missing_distribution(self):
        completed, _, _ = self.run_wait([WHEEL_ONLY], attempts=2)
        self.assertEqual(completed.returncode, 1)
        last = completed.stdout.strip().splitlines()[-1]
        self.assertTrue(last.startswith("::error::"), last)
        self.assertIn("ai-jury 9.9.9", last)
        self.assertIn("missing sdist", last)

    def test_the_failure_is_a_message_and_not_a_traceback(self):
        """`StopIteration` followed by `curl: (3)` is what this replaces."""
        completed, workdir, _ = self.run_wait([EMPTY], attempts=2)
        self.assertEqual(completed.returncode, 1)
        self.assertNotIn("Traceback", completed.stdout + completed.stderr)
        self.assertNotIn("StopIteration", completed.stdout + completed.stderr)
        self.assertFalse((workdir / "published-sdist.txt").exists())

    def test_a_document_that_is_not_the_expected_shape_is_diagnosed(self):
        """Not a crash: an unreadable answer is one more not-yet-served answer."""
        completed, _, _ = self.run_wait([b"<html>503</html>", b"<html>503</html>"], attempts=2)
        self.assertEqual(completed.returncode, 1)
        self.assertNotIn("Traceback", completed.stdout + completed.stderr)
        self.assertIn("missing a readable file list", completed.stdout)

    def test_an_sdist_listed_without_a_digest_is_not_treated_as_served(self):
        """The empty string is exactly what `curl` rejected as a malformed url."""
        broken = {"urls": [_file("bdist_wheel", WHEEL_URL), {"packagetype": "sdist", "url": ""}]}
        completed, workdir, _ = self.run_wait([broken], attempts=2)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("missing sdist", completed.stdout)
        self.assertFalse((workdir / "published-sdist.txt").exists())

    def test_no_version_at_all_is_refused_rather_than_polled(self):
        workdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, workdir, True)
        env = {**os.environ, "PYPI_PYTHON": sys.executable}
        env.pop("GITHUB_REF_NAME", None)
        env.pop("PYPI_VERSION", None)
        completed = subprocess.run(
            ["bash", str(SCRIPT)], cwd=workdir, env=env, capture_output=True, text=True, timeout=60
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("::error::", completed.stdout)
        self.assertIn("no version to wait for", completed.stdout)


class AStalledResponseIsNotAllowedToHoldTheStepOpen(WaitAgainstStub):
    """Attempts bound how many requests are made, not how long one of them takes.

    A peer that accepts the connection and then says nothing defeats an
    attempt-only bound completely: the first request never returns, so the
    second is never made. `publish.yml` sets no `timeout-minutes` anywhere, so
    the ceiling on that would be GitHub's six-hour default — in the step between
    the upload and the GitHub Release, which is the half-made release this whole
    change exists to remove, only arrived at slowly.
    """

    def test_a_response_that_never_arrives_is_abandoned_and_the_poll_ends(self):
        """The reviewer's repro: a stalling stub, two attempts, no interval."""
        started = time.monotonic()
        completed, _, index = self.run_wait([STALL], attempts=2, max_time="1")
        elapsed = time.monotonic() - started
        self.assertEqual(completed.returncode, 1)
        # Two requests actually issued: the first was abandoned, not waited on.
        self.assertEqual(len(index.paths), 2)
        self.assertLess(elapsed, 30, "the stalled response was not abandoned promptly")
        self.assertIn("timed out", completed.stdout)
        self.assertIn("::error::PyPI never served ai-jury 9.9.9", completed.stdout)

    def test_the_wall_clock_budget_ends_the_poll_before_the_attempts_do(self):
        """What makes the header's five minutes a ceiling rather than an estimate.

        Thirty attempts are allowed and the budget is two seconds, so the budget
        is what stops it — and `--max-time` is clamped to the budget left, so the
        one request that stalls cannot outlive the poll it belongs to.
        """
        started = time.monotonic()
        completed, _, index = self.run_wait([STALL], attempts=30, budget="2", max_time="60")
        elapsed = time.monotonic() - started
        self.assertEqual(completed.returncode, 1)
        self.assertLess(elapsed, 30, "the budget did not bound the poll")
        self.assertLess(len(index.paths), 30, "the poll ran to its attempt count, not its budget")
        self.assertIn("::error::PyPI never served ai-jury 9.9.9", completed.stdout)


class MisconfigurationIsDiagnosedRatherThanPolled(WaitAgainstStub):
    """Neither is reachable from `publish.yml`; both were reported as PyPI's fault.

    The script's stated purpose is that a maintainer reads a diagnosis instead of
    a shell error, and that is not a property of the happy path only.
    """

    def test_a_non_numeric_attempt_count_is_named_and_not_left_to_bash(self):
        """`set -u` turns `$((attempts * interval))` into `lots: unbound variable`.

        A shell error about a variable nobody set, printed before any message of
        the script's own, is exactly what this file is against.
        """
        completed, _, index = self.run_wait([BOTH], attempts="lots")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("::error::", completed.stdout)
        self.assertIn("PYPI_ATTEMPTS", completed.stdout)
        self.assertIn("'lots'", completed.stdout)
        self.assertNotIn("unbound variable", completed.stdout + completed.stderr)
        self.assertEqual(index.paths, [], "a misconfigured poll asked the index anyway")

    def test_an_attempt_count_of_zero_is_refused(self):
        """Zero attempts is a wait that never waits, which is not a wait."""
        completed, _, index = self.run_wait([BOTH], attempts=0)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("PYPI_ATTEMPTS must be at least 1", completed.stdout)
        self.assertEqual(index.paths, [])

    def test_an_unusable_interpreter_is_named_rather_than_blamed_on_the_index(self):
        """It exits 127 from every read, which reads as an unreadable file list.

        So a missing interpreter used to spend the whole budget and then accuse
        PyPI of never serving the version. It is a configuration mistake and it
        is now diagnosed as one, before the first request.
        """
        completed, _, index = self.run_wait([BOTH], python="/nonexistent/python3")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("PYPI_PYTHON='/nonexistent/python3'", completed.stdout)
        self.assertIn("not a usable interpreter", completed.stdout)
        self.assertNotIn("never served", completed.stdout)
        self.assertEqual(index.paths, [], "the budget was spent before the diagnosis")

    @unittest.skipIf(shutil.which("echo") is None, "needs an executable that is not python")
    def test_an_interpreter_that_runs_but_is_not_python_is_diagnosed(self):
        """`-c ""` only proves something answered; writing the pair proves more."""
        completed, workdir, _ = self.run_wait([BOTH], python=shutil.which("echo"))
        self.assertEqual(completed.returncode, 2)
        self.assertIn("does not behave like a Python interpreter", completed.stdout)
        self.assertFalse((workdir / "published-sdist.txt").exists())


class TheScriptIsShapedToBeShared(unittest.TestCase):
    """It is a committed, runnable file, and both jobs can actually run it."""

    def test_it_exists_and_is_executable(self):
        self.assertTrue(SCRIPT.exists(), f"{SCRIPT} is missing")
        self.assertTrue(os.access(SCRIPT, os.X_OK), "the shared wait is not executable")

    def test_it_is_valid_shell(self):
        if not POSIX_SHELL:  # pragma: no cover - depends on the runner
            self.skipTest("needs bash on a POSIX platform")
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True, capture_output=True)

    def test_it_carries_the_five_minute_budget(self):
        """The budget moved into the script; it must not have been lost on the way."""
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('attempts="${PYPI_ATTEMPTS:-30}"', text)
        self.assertIn('interval="${PYPI_INTERVAL_SECONDS:-10}"', text)
        self.assertIn('budget="${PYPI_BUDGET_SECONDS:-$((attempts * interval))}"', text)

    def test_every_request_is_bounded_in_time_and_not_only_in_attempts(self):
        """Thirty attempts is not five minutes if one of them can never return."""
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('--connect-timeout "$connect_timeout"', text)
        self.assertIn('--max-time "$request_max"', text)

    def test_every_input_arrives_through_the_environment(self):
        """No Actions expression may reach a shell, and this is still a shell.

        Comment lines are stripped first, as `test_publish_release_chain` does:
        the header discusses the substitution rule it obeys, and a grep would
        match that discussion happily.
        """
        code = [
            line
            for line in SCRIPT.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        ]
        self.assertNotIn("${{", "\n".join(code))


def render_step_shell() -> str:
    """The `run:` body of the render step, lifted out of `publish.yml`.

    Read from the workflow rather than copied into this file: a copy would go on
    passing after the step it stands for had changed, which is the one thing a
    test of a tag-only workflow must not do.
    """
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    header = f"- name: {RENDER_STEP}"
    start = next(i for i, line in enumerate(lines) if line.strip() == header)
    run_at = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run_at]) - len(lines[run_at].lstrip())
    body = []
    for line in lines[run_at + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line[indent + 2 :] if len(line) > indent + 2 else "")
    return "\n".join(body)


@unittest.skipUnless(POSIX_SHELL, "needs bash and curl on a POSIX platform")
@unittest.skipIf(shutil.which("sha256sum") is None, "the step calls sha256sum")
class TheRenderStepRunsAgainstAStubIndex(unittest.TestCase):
    """The step that failed on v1.16.0, run end to end against a stub index.

    `publish.yml` only truly runs on a tag, so the honest thing a suite can do is
    execute its shell somewhere else. The step's own `run:` body is extracted
    from the workflow and run in a sandbox holding the shared wait and the real
    formula template, against a loopback index that answers the way PyPI did:
    first a 200 with an empty file list, then the complete one.
    """

    def render(self, answers, *, attempts: int = 4):
        index = StubIndex(answers)
        self.addCleanup(index.close)
        workdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, workdir, True)
        (workdir / ".github" / "scripts").mkdir(parents=True)
        shutil.copy2(SCRIPT, workdir / ".github" / "scripts" / SCRIPT.name)
        (workdir / "packaging" / "homebrew").mkdir(parents=True)
        shutil.copy2(TEMPLATE, workdir / "packaging" / "homebrew" / TEMPLATE.name)
        env = {
            **os.environ,
            "GITHUB_REF_NAME": "v9.9.9",
            "PYPI_BASE_URL": index.base_url,
            "PYPI_ATTEMPTS": str(attempts),
            "PYPI_INTERVAL_SECONDS": "0",
            "PYPI_PYTHON": sys.executable,
        }
        completed = subprocess.run(
            ["bash", "-c", render_step_shell()],
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return completed, workdir

    def test_the_step_body_was_actually_extracted(self):
        """Vacuity: an empty script would `bash -c` cleanly and prove nothing."""
        body = render_step_shell()
        self.assertGreater(len(body.splitlines()), 15)
        self.assertIn("set -euo pipefail", body)
        self.assertIn(".github/scripts/wait-for-pypi-dists.sh", body)
        self.assertIn("packaging/homebrew/ai-jury.rb.template", body)

    def test_it_renders_a_formula_after_the_index_converges(self):
        completed, workdir = self.render([EMPTY, SERVED])
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        formula = (workdir / "release" / "ai-jury.rb").read_text(encoding="utf-8")
        self.assertIn(f'sha256 "{TARBALL_SHA}"', formula)
        self.assertIn("ai_jury-9.9.9.tar.gz", formula)
        self.assertIn('assert_match "jury 9.9.9"', formula)
        self.assertEqual(re.findall(r"@[A-Z0-9_]+@", formula), [])
        self.assertIn("ai-jury.rb", (workdir / "release" / "SHA256SUMS").read_text())

    def test_an_index_that_never_converges_fails_before_anything_is_rendered(self):
        completed, workdir = self.render([EMPTY], attempts=2)
        self.assertEqual(completed.returncode, 1)
        self.assertNotIn("Traceback", completed.stdout + completed.stderr)
        self.assertNotIn("Malformed", completed.stdout + completed.stderr)
        last = completed.stdout.strip().splitlines()[-1]
        self.assertIn("::error::PyPI never served ai-jury 9.9.9", last)
        self.assertIn("missing sdist", last)
        self.assertFalse((workdir / "release" / "ai-jury.rb").exists())


# Last, deliberately: as the first thing after the shared-shape class this
# collected seventeen of the module's tests and silently dropped the three that
# run the render step — the ones carrying this change's headline claim. The
# discovery-based suite was never affected, but `python tests/…` is how a
# maintainer checks one file.
if __name__ == "__main__":  # pragma: no cover
    unittest.main()
