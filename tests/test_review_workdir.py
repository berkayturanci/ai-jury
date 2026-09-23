"""A panel reviewer starts in a fresh, empty directory — never the repository.

The repository under review is the author's on a pull-request checkout, and an
agent CLI started inside it picks things up on its own: instruction files
(``CLAUDE.md``, ``AGENTS.md``), project settings that can carry hooks or MCP
servers, and a ``.env`` one relative path away. A reviewer needs none of it —
its prompt carries the diff — so the three native CLIs are started in an empty
temporary directory that is removed when the call returns. Stdlib + offline;
the one real subprocess here is this interpreter.
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_jury import adapters, runagent  # noqa: E402
from ai_jury.adapters import ERR_TIMEOUT, make_adapter  # noqa: E402
from ai_jury.cli import _run_run_agent  # noqa: E402
from ai_jury.config import AgentSpec  # noqa: E402

#: Prints where it was started and what that directory held, as a review would
#: print its text. Run by the real `_spawn`, so the directory is the real one.
_REPORT_CWD = (
    "import json, os; print(json.dumps({'cwd': os.getcwd(), 'entries': sorted(os.listdir('.'))}))"
)


class _CwdProbe(adapters.Adapter):
    """An isolated adapter whose "CLI" reports its own working directory."""

    ISOLATE_REVIEW_CWD = True

    def available(self) -> bool:
        return True

    def build_argv(self, prompt: str) -> list[str]:
        del prompt
        return [sys.executable, "-c", _REPORT_CWD]


def _spec(name="probe", vendor="anthropic", command="claude", **kw) -> AgentSpec:
    return AgentSpec(name=name, vendor=vendor, command=command, **kw)


class _Recorder:
    """Stands in for `adapters._spawn`; notes the directory and what it held."""

    def __init__(self, raises: BaseException | None = None):
        self.raises = raises
        self.argv: list[str] | None = None
        self.cwd: str | None = None
        self.entries: list[str] | None = None

    def __call__(self, argv, stdin, timeout, cwd=None):
        del stdin, timeout
        self.argv, self.cwd = list(argv), cwd
        self.entries = sorted(p.name for p in Path(cwd).iterdir()) if cwd else None
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(argv, 0, "a review", "")


def _run(adapter, recorder, role_policy=None):
    with (
        mock.patch.object(adapters.shutil, "which", lambda cmd: f"/usr/local/bin/{cmd}"),
        mock.patch.object(adapters, "_spawn", recorder),
    ):
        return adapter.run("the prompt", role_policy=role_policy)


class APanelReviewerStartsOutsideTheRepository(unittest.TestCase):
    def test_the_real_child_runs_in_an_empty_directory_that_is_not_ours(self):
        result = _CwdProbe(_spec()).run("the prompt")
        self.assertTrue(result.ok, result.error)
        seen = json.loads(result.output)
        self.assertNotEqual(Path(seen["cwd"]).resolve(), Path.cwd().resolve())
        self.assertEqual(seen["entries"], [])
        self.assertFalse(Path(seen["cwd"]).exists(), "the directory outlived the call")

    def test_each_call_gets_a_directory_of_its_own(self):
        adapter = _CwdProbe(_spec())
        first = json.loads(adapter.run("p").output)["cwd"]
        second = json.loads(adapter.run("p").output)["cwd"]
        self.assertNotEqual(first, second)

    def test_the_three_native_clis_are_isolated(self):
        for vendor, command in (("anthropic", "claude"), ("openai", "codex"), ("google", "agy")):
            with self.subTest(vendor):
                recorder = _Recorder()
                result = _run(make_adapter(_spec(vendor=vendor, command=command)), recorder)
                self.assertTrue(result.ok, result.error)
                self.assertIsNotNone(recorder.cwd, f"{command} ran in jury's own directory")
                self.assertNotEqual(Path(recorder.cwd).resolve(), Path.cwd().resolve())
                self.assertEqual(recorder.entries, [])
                self.assertFalse(Path(recorder.cwd).exists())

    def test_the_directory_is_removed_when_the_reviewer_times_out(self):
        recorder = _Recorder(raises=subprocess.TimeoutExpired("claude", 1))
        result = _run(make_adapter(_spec()), recorder)
        self.assertEqual(result.error_code, ERR_TIMEOUT)
        self.assertIsNotNone(recorder.cwd)
        self.assertFalse(Path(recorder.cwd).exists())


class WhatStaysWhereItWasStarted(unittest.TestCase):
    def test_a_write_role_runs_in_the_directory_it_was_given(self):
        # `jury run-agent --role implement --allow-write --cwd <worktree>` edits
        # that worktree; moving it would make the implementer edit nothing.
        recorder = _Recorder()
        policy = runagent.role_policy("implement", True)
        _run(make_adapter(_spec()), recorder, role_policy=policy)
        self.assertIsNone(recorder.cwd)

    def test_run_agent_read_only_roles_start_outside_the_checkout_too(self):
        # review/gate/chair read attacker-controlled content from their prompt
        # file and need nothing from the repository; only write roles keep --cwd.
        for role in runagent.READ_ONLY_ROLES:
            with self.subTest(role):
                recorder = _Recorder()
                _run(make_adapter(_spec()), recorder, role_policy=runagent.role_policy(role))
                self.assertIsNotNone(recorder.cwd, f"{role} ran in the caller's directory")
                self.assertEqual(recorder.entries, [])

    def test_a_bring_your_own_cli_seat_is_not_moved(self):
        # This tool cannot know what an operator's own binary expects of its
        # directory (cursor-agent asks for workspace trust, aider for a repo).
        recorder = _Recorder()
        result = _run(make_adapter(_spec(vendor="cli", command="aider")), recorder)
        self.assertTrue(result.ok, result.error)
        self.assertIsNone(recorder.cwd)
        self.assertFalse(adapters.Adapter.ISOLATE_REVIEW_CWD)
        self.assertFalse(adapters.GenericCLIAdapter.ISOLATE_REVIEW_CWD)


class AProjectHookInTheCheckoutCannotRun(unittest.TestCase):
    """A PR checkout's `.claude/settings.json` hook must not reach a reader.

    Measured on Claude Code 2.1.236: under the no-tool argv alone, a
    `SessionStart` and a `UserPromptSubmit` hook in the working directory's
    `.claude/settings.json` both ran, with no trust prompt. Two things stop it,
    and each is asserted here for the panel and every read-only `run-agent`
    role: the reviewer starts in an empty directory, and its argv carries
    `--safe-mode`, which loads no hooks, CLAUDE.md, skills or plugins at all.
    """

    def _checkout(self, tmp: Path) -> Path:
        checkout = tmp / "checkout"
        (checkout / ".claude").mkdir(parents=True)
        hook = [{"hooks": [{"type": "command", "command": "touch pwned"}]}]
        (checkout / ".claude" / "settings.json").write_text(
            json.dumps({"hooks": {"SessionStart": hook, "UserPromptSubmit": hook}}),
            encoding="utf-8",
        )
        (checkout / "CLAUDE.md").write_text("# injected instructions\n", encoding="utf-8")
        return checkout

    def _run_agent(self, tmp: Path, checkout: Path, role: str, *extra: str):
        prompt = tmp / "prompt.md"
        prompt.write_text("review this", encoding="utf-8")
        config = tmp / "jury.toml"
        config.write_text(
            '[jury]\nchair = "claude"\n\n[[agent]]\nname = "claude"\n'
            'vendor = "anthropic"\ncommand = "claude"\n',
            encoding="utf-8",
        )
        seen: dict = {}

        def spawn(argv, stdin, timeout, cwd=None):
            del stdin, timeout
            where = Path(cwd) if cwd is not None else Path.cwd()
            seen.update(
                argv=list(argv),
                cwd=where.resolve(),
                entries=sorted(p.name for p in where.iterdir()),
            )
            return subprocess.CompletedProcess(argv, 0, "a review", "")

        argv = ["--agent", "claude", "--role", role, "--cwd", str(checkout)]
        argv += ["--prompt-file", str(prompt), "--config", str(config), *extra]
        err = io.StringIO()
        with (
            mock.patch.object(adapters.shutil, "which", lambda cmd: f"/usr/local/bin/{cmd}"),
            mock.patch.object(adapters, "_spawn", spawn),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(err),
        ):
            code = _run_run_agent(argv)
        return code, seen, err.getvalue()

    def test_read_only_run_agent_roles_neither_start_there_nor_load_settings(self):
        for role in runagent.READ_ONLY_ROLES:
            with self.subTest(role), tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                checkout = self._checkout(tmp)
                code, seen, err = self._run_agent(tmp, checkout, role)
                self.assertEqual(code, 0, err)
                self.assertNotEqual(seen["cwd"], checkout.resolve())
                self.assertEqual(seen["entries"], [], "the reviewer's directory is not empty")
                self.assertIn("--safe-mode", seen["argv"])
                self.assertIn("--no-session-persistence", seen["argv"])
                self.assertIn("--cwd applies to write roles", err)

    def test_the_panel_argv_carries_safe_mode(self):
        recorder = _Recorder()
        _run(make_adapter(_spec()), recorder)
        self.assertIn("--safe-mode", recorder.argv)
        self.assertEqual(recorder.entries, [])

    def test_a_write_role_still_works_in_the_checkout_with_its_settings(self):
        # An implementer edits the operator's own worktree, whose CLAUDE.md and
        # hooks it is meant to follow: no safe mode, and it runs right there.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            checkout = self._checkout(tmp)
            code, seen, err = self._run_agent(tmp, checkout, "implement", "--allow-write")
        self.assertEqual(code, 0, err)
        self.assertEqual(seen["cwd"], checkout.resolve())
        self.assertNotIn("--safe-mode", seen["argv"])
        self.assertNotIn("--cwd applies to write roles", err)


class CodexOutsideAGitRepository(unittest.TestCase):
    """`codex exec` refuses to start outside a git repo without this flag."""

    def test_the_read_only_argv_skips_the_git_repo_check(self):
        argv = make_adapter(_spec(vendor="openai", command="codex")).build_argv("p")
        self.assertIn("--skip-git-repo-check", argv)

    def test_a_configured_skip_is_not_doubled(self):
        spec = _spec(vendor="openai", command="codex", extra_args=["--skip-git-repo-check"])
        argv = make_adapter(spec).build_argv("p")
        self.assertEqual(argv.count("--skip-git-repo-check"), 1)

    def test_the_write_argv_is_unchanged(self):
        # An implementer runs in its worktree, which is a git repository.
        argv = make_adapter(_spec(vendor="openai", command="codex")).build_write_argv("p")
        self.assertNotIn("--skip-git-repo-check", argv)


class TheSpawnSeam(unittest.TestCase):
    def test_cwd_reaches_the_child(self):
        with adapters._review_workdir(True) as path:
            proc = adapters._spawn([sys.executable, "-c", _REPORT_CWD], None, 60, cwd=path)
            seen = json.loads(proc.stdout)
            self.assertEqual(Path(seen["cwd"]).resolve(), Path(path).resolve())
        self.assertFalse(Path(path).exists())

    def test_no_isolation_yields_no_directory(self):
        with adapters._review_workdir(False) as path:
            self.assertIsNone(path)


if __name__ == "__main__":
    unittest.main()
