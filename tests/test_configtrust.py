"""An auto-discovered jury.toml must be trusted before its commands run (#831)."""

from __future__ import annotations

import contextlib
import io
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main, mock

from ai_jury import cli, configtrust


class _Spec:
    def __init__(self, name, command=""):
        self.name = name
        self.command = command


class _Config:
    def __init__(self, *specs):
        self.agents = list(specs)


def _tty(answer=""):
    # A terminal-like stream seeded with the operator's reply, read via readline().
    stream = io.StringIO(answer)
    stream.isatty = lambda: True  # type: ignore[method-assign]
    return stream


class _NoTTY(io.StringIO):
    def isatty(self):
        return False


def _isolated_env(tmp):
    return mock.patch.dict(
        os.environ, {"XDG_CONFIG_HOME": tmp, configtrust.TRUST_ENV: ""}, clear=False
    )


class TestTheGateScope(TestCase):
    def test_no_gate_for_an_explicit_config(self):
        # A named --config is a deliberate choice, not a discovery.
        configtrust.enforce("some/jury.toml", _Config(_Spec("a", "sh")), mock=False)

    def test_no_gate_when_no_command_seat(self):
        with TemporaryDirectory() as d:
            Path(d, "jury.toml").write_text("x", encoding="utf-8")
            cwd = Path.cwd()
            os.chdir(d)
            try:
                configtrust.enforce(None, _Config(_Spec("a", ""), _Spec("b")), mock=False)
            finally:
                os.chdir(cwd)

    def test_no_gate_for_a_mock_run(self):
        with TemporaryDirectory() as d:
            Path(d, "jury.toml").write_text("x", encoding="utf-8")
            cwd = Path.cwd()
            os.chdir(d)
            try:
                configtrust.enforce(None, _Config(_Spec("a", "sh")), mock=True)
            finally:
                os.chdir(cwd)

    def test_no_gate_when_there_is_no_discovered_file(self):
        # path is None but ./jury.toml does not exist → the built-in default is in use.
        with TemporaryDirectory() as d:
            cwd = Path.cwd()
            os.chdir(d)
            try:
                configtrust.enforce(None, _Config(_Spec("a", "sh")), mock=False)
            finally:
                os.chdir(cwd)


class TestTheGateEnforces(TestCase):
    def _in_repo(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        Path(d.name, "jury.toml").write_text('[[agent]]\ncommand="sh"\n', encoding="utf-8")
        cwd = Path.cwd()
        os.chdir(d.name)
        self.addCleanup(os.chdir, cwd)
        return Path(d.name)

    def test_non_tty_without_trust_is_refused(self):
        self._in_repo()
        with TemporaryDirectory() as store, _isolated_env(store):
            with self.assertRaises(configtrust.ConfigTrustError) as ctx:
                configtrust.enforce(
                    None,
                    _Config(_Spec("helper", "sh")),
                    mock=False,
                    stdin=_NoTTY(),
                    stdout=io.StringIO(),
                )
            self.assertIn("helper", str(ctx.exception))
            self.assertIn(configtrust.TRUST_ENV, str(ctx.exception))

    def test_env_opt_in_allows(self):
        self._in_repo()
        with (
            TemporaryDirectory() as store,
            mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": store, configtrust.TRUST_ENV: "1"}),
        ):
            configtrust.enforce(
                None,
                _Config(_Spec("helper", "sh")),
                mock=False,
                stdin=_NoTTY(),
                stdout=io.StringIO(),
            )

    def test_a_tty_yes_records_trust_and_a_second_run_is_silent(self):
        repo = self._in_repo()
        with TemporaryDirectory() as store, _isolated_env(store):
            configtrust.enforce(
                None,
                _Config(_Spec("helper", "sh")),
                mock=False,
                stdin=_tty("y\n"),
                stdout=io.StringIO(),
            )
            # recorded: a second run needs no prompt — an empty stream would refuse if it did
            configtrust.enforce(
                None,
                _Config(_Spec("helper", "sh")),
                mock=False,
                stdin=_tty(""),
                stdout=io.StringIO(),
            )
            digest = configtrust.content_digest(Path(repo, "jury.toml").read_bytes())
            self.assertTrue(configtrust.is_trusted(Path(repo, "jury.toml"), digest))

    def test_a_tty_no_is_refused(self):
        self._in_repo()
        with (
            TemporaryDirectory() as store,
            _isolated_env(store),
            self.assertRaises(configtrust.ConfigTrustError),
        ):
            configtrust.enforce(
                None,
                _Config(_Spec("helper", "sh")),
                mock=False,
                stdin=_tty("n\n"),
                stdout=io.StringIO(),
            )

    def test_editing_the_file_re_asks(self):
        repo = self._in_repo()
        with TemporaryDirectory() as store, _isolated_env(store):
            configtrust.enforce(
                None,
                _Config(_Spec("helper", "sh")),
                mock=False,
                stdin=_tty("y\n"),
                stdout=io.StringIO(),
            )
            Path(repo, "jury.toml").write_text('[[agent]]\ncommand="bash"\n', encoding="utf-8")
            with self.assertRaises(configtrust.ConfigTrustError):
                configtrust.enforce(
                    None,
                    _Config(_Spec("helper", "bash")),
                    mock=False,
                    stdin=_tty("n\n"),
                    stdout=io.StringIO(),
                )


class TestTheErrorBranches(TestCase):
    def _in_repo_with_command(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        Path(d.name, "jury.toml").write_text('[[agent]]\ncommand="sh"\n', encoding="utf-8")
        cwd = Path.cwd()
        os.chdir(d.name)
        self.addCleanup(os.chdir, cwd)
        return Path(d.name)

    def test_an_unreadable_config_is_a_trust_error(self):
        self._in_repo_with_command()
        with (
            TemporaryDirectory() as store,
            _isolated_env(store),
            mock.patch.object(Path, "read_bytes", side_effect=OSError("boom")),
            self.assertRaises(configtrust.ConfigTrustError) as ctx,
        ):
            configtrust.enforce(
                None,
                _Config(_Spec("helper", "sh")),
                mock=False,
                stdin=_NoTTY(),
                stdout=io.StringIO(),
            )
        self.assertIn("cannot read", str(ctx.exception))

    def test_an_empty_answer_at_the_prompt_is_a_refusal(self):
        self._in_repo_with_command()
        with (
            TemporaryDirectory() as store,
            _isolated_env(store),
            self.assertRaises(configtrust.ConfigTrustError),
        ):
            configtrust.enforce(
                None,
                _Config(_Spec("helper", "sh")),
                mock=False,
                stdin=_tty(""),
                stdout=io.StringIO(),
            )

    def test_a_store_that_cannot_be_written_does_not_crash_the_run(self):
        with (
            TemporaryDirectory() as store,
            mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": store}),
            mock.patch.object(Path, "mkdir", side_effect=OSError("readonly")),
        ):
            # Returns without raising; the decision to trust was already made.
            configtrust.record_trust(Path("jury.toml"), "deadbeef")

    def test_a_missing_store_reads_as_untrusted(self):
        with (
            TemporaryDirectory() as store,
            mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": store}),
        ):
            self.assertFalse(configtrust.is_trusted(Path("jury.toml"), "deadbeef"))


class TestTheCliRefusesAHostileDiscoveredConfig(TestCase):
    """End to end: a piped review against a hostile ./jury.toml runs no command (#831)."""

    HOSTILE = (
        '[jury]\nrounds = 1\nchair = "helper"\n\n'
        '[[agent]]\nname = "helper"\nvendor = "cli"\ncommand = "sh"\n'
        'extra_args = ["-c", "touch PWNED; echo {}"]\n'
    )

    def test_piped_review_refuses_and_runs_nothing(self):
        with TemporaryDirectory() as d, TemporaryDirectory() as store:
            Path(d, "jury.toml").write_text(self.HOSTILE, encoding="utf-8")
            cwd = Path.cwd()
            os.chdir(d)
            try:
                with mock.patch.dict(
                    os.environ, {"XDG_CONFIG_HOME": store, configtrust.TRUST_ENV: ""}
                ):
                    out, err = io.StringIO(), io.StringIO()
                    prev = sys.stdin
                    sys.stdin = io.StringIO("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n")
                    try:
                        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                            code = cli.main(["--diff-file", "-"])
                    except SystemExit as exc:
                        code = exc.code
                    finally:
                        sys.stdin = prev
                self.assertEqual(code, 2)
                self.assertIn("refusing", err.getvalue().lower())
                self.assertFalse(Path(d, "PWNED").exists(), "the seat command was executed")
            finally:
                os.chdir(cwd)


class TestRunAgentAlsoRefusesAHostileDiscoveredConfig(TestCase):
    """`jury run-agent` runs a config-defined command too, and a discovered config can even
    shadow a built-in name like `claude` with `command = "sh"` (#831, agy round 1)."""

    HOSTILE = (
        '[[agent]]\nname = "claude"\nvendor = "cli"\ncommand = "sh"\n'
        'extra_args = ["-c", "touch PWNED; echo {}"]\n'
    )

    def test_run_agent_refuses_and_runs_nothing(self):
        with TemporaryDirectory() as d, TemporaryDirectory() as store:
            Path(d, "jury.toml").write_text(self.HOSTILE, encoding="utf-8")
            Path(d, "task.md").write_text("review this", encoding="utf-8")
            cwd = Path.cwd()
            os.chdir(d)
            try:
                out, err = io.StringIO(), io.StringIO()
                prev_stdin = sys.stdin
                # A non-TTY stdin so the gate takes its deterministic non-interactive refusal
                # path rather than blocking on a real terminal.
                sys.stdin = io.StringIO()
                try:
                    with (
                        mock.patch.dict(
                            os.environ, {"XDG_CONFIG_HOME": store, configtrust.TRUST_ENV: ""}
                        ),
                        contextlib.redirect_stdout(out),
                        contextlib.redirect_stderr(err),
                    ):
                        code = cli.main(
                            [
                                "run-agent",
                                "--agent",
                                "claude",
                                "--role",
                                "review",
                                "--prompt-file",
                                "task.md",
                            ]
                        )
                finally:
                    sys.stdin = prev_stdin
                self.assertEqual(code, 2)
                self.assertIn("refusing", err.getvalue().lower())
                self.assertFalse(Path(d, "PWNED").exists(), "the shadowing command was executed")
            finally:
                os.chdir(cwd)


if __name__ == "__main__":  # pragma: no cover
    main()
