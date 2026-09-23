"""agy is opt-in: never seated implicitly, always flagged when seated.

Measured on agy 1.2.9, the shipped argv (`--sandbox --dangerously-skip-permissions`)
read and wrote files outside its working directory and reached the network, and
agy has no flag that removes its tools. So it is out of the built-in default
panel and every implicit choice `jury init` makes, a seat the operator configures
draws a least-privilege warning (a failure under `--strict`), and a machine whose
only CLI is agy is told why it was not used instead of being told to install one.
Stdlib + offline.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import tomllib
import types
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_jury import cli, doctor, privilege, runagent, scaffold  # noqa: E402
from ai_jury.config import (  # noqa: E402
    AGY_AGENT,
    AGY_OPT_IN_NOTE,
    DEFAULT_CONFIG,
    DEFAULT_MIN_VENDORS,
    _from_dict,
    agy_opt_in_hint,
    load_config,
    vendor_identity,
)
from ai_jury.orchestrator import run_jury  # noqa: E402

DIFF = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"


def _only_agy_on_path(command):
    """`shutil.which` on a machine whose one agent CLI is agy."""
    return "/opt/agy/bin/agy" if command == "agy" else None


def _nothing_on_path(_command):
    return None


@contextlib.contextmanager
def _in_empty_dir():
    """A working directory with no jury.toml, restored afterwards."""
    with tempfile.TemporaryDirectory() as tmp:
        prev = Path.cwd()
        os.chdir(tmp)
        try:
            yield Path(tmp)
        finally:
            os.chdir(prev)


class TheDefaultPanelHasNoAgy(unittest.TestCase):
    def test_the_built_in_seats_do_not_include_agy(self):
        seats = _from_dict(DEFAULT_CONFIG).agents
        self.assertEqual([s.name for s in seats], ["claude", "codex"])
        self.assertNotIn("google", {s.adapter_key for s in seats})

    def test_a_run_with_no_jury_toml_never_seats_agy(self):
        with _in_empty_dir():
            config = load_config(None)
        self.assertNotIn("google", {s.adapter_key for s in config.enabled_agents})

    def test_the_default_panel_still_meets_the_vendor_guard(self):
        vendors = {vendor_identity(s.vendor) for s in _from_dict(DEFAULT_CONFIG).enabled_agents}
        self.assertGreaterEqual(len(vendors), DEFAULT_MIN_VENDORS)

    def test_agy_is_still_dispatchable_by_name_with_its_write_role_unchanged(self):
        spec = runagent.builtin_spec("agy")
        self.assertEqual(spec.command, "agy")
        self.assertEqual(spec.extra_args, AGY_AGENT["extra_args"])
        self.assertEqual(
            privilege.enable_write("google", list(spec.extra_args)),
            ["--dangerously-skip-permissions"],
        )


class AnExplicitAgySeatIsFlagged(unittest.TestCase):
    def _config(self):
        return _from_dict({**DEFAULT_CONFIG, "agent": [*DEFAULT_CONFIG["agent"], AGY_AGENT]})

    def test_the_audit_warns_about_it_and_only_it(self):
        warnings = privilege.audit_privilege(self._config().enabled_agents)
        self.assertEqual(len(warnings), 1)
        self.assertIn("agent 'agy' (agy) cannot be confined", warnings[0])
        self.assertIn("do not use it on untrusted diffs", warnings[0])

    def test_strict_refuses_the_run(self):
        with self.assertRaises(RuntimeError) as ctx:
            run_jury(self._config(), DIFF, strict=True, seed=1)
        self.assertIn("least-privilege check failed (--strict)", str(ctx.exception))
        self.assertIn("cannot be confined", str(ctx.exception))

    def test_a_disabled_agy_seat_is_not_flagged(self):
        seat = {**AGY_AGENT, "enabled": False}
        config = _from_dict({**DEFAULT_CONFIG, "agent": [*DEFAULT_CONFIG["agent"], seat]})
        self.assertEqual(privilege.audit_privilege(config.enabled_agents), [])


class AnAgyOnlyMachineIsToldWhy(unittest.TestCase):
    def test_the_hint_names_the_reason_and_the_fix(self):
        self.assertIn("cannot be confined for untrusted diffs", AGY_OPT_IN_NOTE)
        self.assertIn("add it explicitly in jury.toml", AGY_OPT_IN_NOTE)
        self.assertEqual(agy_opt_in_hint(["anthropic"], _only_agy_on_path), AGY_OPT_IN_NOTE)
        self.assertIsNone(agy_opt_in_hint(["anthropic"], _nothing_on_path))
        self.assertIsNone(agy_opt_in_hint(["google"], _only_agy_on_path))

    def test_no_usable_agents_says_why_agy_sat_out(self):
        with (
            mock.patch("shutil.which", _only_agy_on_path),
            self.assertRaises(RuntimeError) as ctx,
        ):
            run_jury(_from_dict(DEFAULT_CONFIG), DIFF, seed=1)
        message = str(ctx.exception)
        self.assertIn("no usable agents", message)
        self.assertIn(AGY_OPT_IN_NOTE, message)

    def test_no_usable_agents_without_agy_says_nothing_about_it(self):
        with (
            mock.patch("shutil.which", _nothing_on_path),
            self.assertRaises(RuntimeError) as ctx,
        ):
            run_jury(_from_dict(DEFAULT_CONFIG), DIFF, seed=1)
        self.assertNotIn("agy", str(ctx.exception))

    def test_the_doctor_says_why(self):
        agents = [
            {"name": "claude", "adapter": "anthropic", "available": False},
            {"name": "codex", "adapter": "openai", "available": False},
        ]
        with (
            mock.patch("shutil.which", _only_agy_on_path),
            mock.patch("ai_jury.adapters.list_local_models", return_value=[]),
        ):
            steps = doctor._recommendations("jury.toml", None, agents)["steps"]
        self.assertIn(f"Note: {AGY_OPT_IN_NOTE}.", steps)

    def test_the_local_fallback_says_why(self):
        config = _from_dict(DEFAULT_CONFIG)
        logged: list[str] = []
        args = types.SimpleNamespace(config=None, mock=False)
        with (
            _in_empty_dir(),
            mock.patch("shutil.which", _only_agy_on_path),
            mock.patch("ai_jury.adapters.list_local_models", return_value=["qwen2.5-coder:7b"]),
        ):
            cli._maybe_add_local_fallback(config, args, logged.append)
        self.assertEqual(config.chair, "local")
        self.assertIn(f"note: {AGY_OPT_IN_NOTE}", logged)


class ACollapsedPanelOnAnAgyMachineSaysWhy(unittest.TestCase):
    """claude + agy installed, no codex, no config: the vendor guard exits 3.

    Before agy left the default panel that runner had two vendors. Now it has
    one, and the exit must say which second vendor it already has and why it
    was not seated.
    """

    TOML = (
        '[jury]\nrounds = 1\nchair = "claude"\n\n'
        '[[agent]]\nname = "claude"\nvendor = "anthropic"\ncommand = "claude"\n\n'
        '[[agent]]\nname = "codex"\nvendor = "openai"\ncommand = "codex"\n'
    )

    def _jury(self, which):
        from ai_jury import adapters as adapters_module
        from ai_jury.adapters import AgentResult, MockAdapter

        real = MockAdapter.run

        def run(self, prompt, phase="review", timeout=None, role_policy=None):
            if phase == "review" and self.name == "codex":
                return AgentResult(
                    "codex",
                    "openai",
                    False,
                    "",
                    0.0,
                    "missing",
                    error_code=adapters_module.ERR_MISSING_CLI,
                )
            return real(self, prompt, phase=phase, timeout=timeout, role_policy=role_policy)

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "jury.toml"
            config.write_text(self.TOML, encoding="utf-8")
            diff = Path(tmp) / "changes.diff"
            diff.write_text(DIFF, encoding="utf-8")
            err = io.StringIO()
            with (
                mock.patch.object(MockAdapter, "run", run),
                mock.patch("shutil.which", which),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(err),
            ):
                code = cli.main(["--mock", "--diff-file", str(diff), "--config", str(config)])
        return code, err.getvalue()

    def test_the_guard_failure_names_agy_when_it_is_installed(self):
        code, err = self._jury(_only_agy_on_path)
        self.assertEqual(code, 3)
        self.assertIn("panel collapsed", err)
        self.assertIn(f"note: {AGY_OPT_IN_NOTE}", err)
        self.assertIn("lower --min-vendors", err)

    def test_the_guard_failure_is_unchanged_without_agy(self):
        code, err = self._jury(_nothing_on_path)
        self.assertEqual(code, 3)
        self.assertNotIn("agy", err)


class JuryInitSeatsAgyOnlyByName(unittest.TestCase):
    AGY_ONLY = {name: name == "agy" for name in scaffold.KNOWN_AGENTS}

    def _init(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(cli, "_init_available", return_value=dict(self.AGY_ONLY)),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
            mock.patch.object(sys, "stdin", io.StringIO("")),
        ):
            code = cli.main(["init", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_detection_alone_does_not_seat_agy_and_says_why(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _out, err = self._init("-o", str(Path(tmp) / "jury.toml"))
        self.assertEqual(code, 2)
        self.assertIn("no agents detected", err)
        self.assertIn(f"note: {AGY_OPT_IN_NOTE}.", err)

    def test_presets_leave_agy_out(self):
        for preset in ("fast", "balanced", "thorough"):
            with self.subTest(preset), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "jury.toml"
                code, _out, _err = self._init("--preset", preset, "-o", str(path))
                self.assertEqual(code, 0)
                names = [
                    a["name"] for a in tomllib.loads(path.read_text(encoding="utf-8"))["agent"]
                ]
                self.assertNotIn("agy", names)

    def test_naming_agy_writes_it_with_a_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jury.toml"
            code, _out, err = self._init("--agents", "agy", "-o", str(path))
            names = [a["name"] for a in tomllib.loads(path.read_text(encoding="utf-8"))["agent"]]
        self.assertEqual(code, 0)
        self.assertEqual(names, ["agy"])
        self.assertIn("warning: agy cannot be confined", err)

    def test_an_interactive_default_never_includes_agy(self):
        available = dict.fromkeys(scaffold.KNOWN_AGENTS, True)
        self.assertNotIn("agy", cli._default_init_agents(available))
        self.assertNotIn("agy", cli._default_init_agents({}))


if __name__ == "__main__":
    unittest.main()
