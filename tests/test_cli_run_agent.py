"""Offline tests for ``jury run-agent`` (issue #661).

Every adapter is mocked or driven through a patched ``_spawn``: no live CLI, no
network, no real clock. The load-bearing assertions are the role-policy ones — a
``review``/``gate``/``chair`` invocation must never carry a write-enabling flag,
and ``implement``/``fix`` must be refused without ``--allow-write``.

Run with: python -m unittest discover -s tests
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import privilege, runagent  # noqa: E402
from ai_jury.adapters import Adapter, AgentResult, make_adapter  # noqa: E402
from ai_jury.cli import (  # noqa: E402
    _child_argv,
    _run_agent_parser,
    _run_run_agent,
    _spawn_detached,
    main,
)
from ai_jury.config import DEFAULT_CONFIG, AgentSpec, _from_dict  # noqa: E402

# Flags that would let an agent edit files or run commands. NONE of these may
# appear in a read-only role's argv, whatever the operator passed.
WRITE_ENABLING = ("workspace-write", "danger-full-access", "--full-auto", "--yolo")

SAMPLE_CONFIG = """\
[jury]
chair = "claude"

[[agent]]
name = "claude"
vendor = "anthropic"
command = "claude"
model = "claude-opus-4-5"
extra_args = ["--disallowed-tools", "Edit,Write,NotebookEdit,Bash"]

[[agent]]
name = "house"
vendor = "openai"
command = "codex"
extra_args = ["-s", "read-only"]

[[agent]]
name = "retired"
vendor = "google"
command = "agy"
enabled = false
extra_args = ["--sandbox"]
"""


@contextlib.contextmanager
def _workspace(config_text: str = SAMPLE_CONFIG, prompt: str = "do the thing"):
    """A temp dir holding a jury.toml, a prompt file and a cache dir."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "jury.toml").write_text(config_text, encoding="utf-8")
        (root / "prompt.md").write_text(prompt, encoding="utf-8")
        yield root


def _run(argv: list[str]) -> tuple[int, str, str]:
    """Run ``jury run-agent`` capturing stdout/stderr."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = _run_run_agent(argv)
    return code, out.getvalue(), err.getvalue()


def _run_run_agent_capture(argv, **kwargs):
    """`_run` with the injected sleep/clock seams (for the wait lifecycle)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = _run_run_agent(argv, **kwargs)
    return code, out.getvalue(), err.getvalue()


class RolePolicyTests(unittest.TestCase):
    """The pure role policy — the security decision, isolated."""

    def test_read_only_roles_are_never_write_capable(self):
        for role in ("review", "gate", "chair"):
            for allow_write in (False, True):
                policy = runagent.role_policy(role, allow_write)
                self.assertFalse(policy.write, f"{role} write={allow_write}")
                self.assertIsNone(policy.refusal)

    def test_allow_write_on_a_read_only_role_warns_but_proceeds(self):
        policy = runagent.role_policy("gate", allow_write=True)
        self.assertFalse(policy.write)
        self.assertIn("ignored", policy.warning)

    def test_write_roles_are_refused_without_allow_write(self):
        for role in ("implement", "fix"):
            policy = runagent.role_policy(role, allow_write=False)
            self.assertFalse(policy.write)
            self.assertIn("--allow-write", policy.refusal)

    def test_write_roles_are_granted_with_allow_write(self):
        for role in ("implement", "fix"):
            policy = runagent.role_policy(role, allow_write=True)
            self.assertTrue(policy.write)
            self.assertIsNone(policy.refusal)

    def test_unknown_role_is_refused(self):
        policy = runagent.role_policy("deploy", allow_write=True)
        self.assertFalse(policy.write)
        self.assertIn("unknown role", policy.refusal)

    def test_role_is_normalized(self):
        self.assertEqual(runagent.role_policy("  REVIEW ").role, "review")

    def test_role_sets_partition_cleanly(self):
        self.assertEqual(set(runagent.ROLES), set(runagent.READ_ONLY_ROLES) | runagent.WRITE_ROLES)
        self.assertFalse(set(runagent.READ_ONLY_ROLES) & runagent.WRITE_ROLES)


class RoleArgvTests(unittest.TestCase):
    """The role policy as it actually reaches each vendor's argv."""

    def setUp(self):
        self.agents = _from_dict(DEFAULT_CONFIG).agents

    def test_panel_invocation_is_byte_identical(self):
        # No policy = every existing call site. The seam must not move a byte.
        for spec in self.agents:
            adapter = make_adapter(spec)
            self.assertEqual(
                adapter.build_argv_for_role("prompt", None), adapter.build_argv("prompt")
            )

    def test_read_only_roles_get_the_panel_argv(self):
        for spec in self.agents:
            adapter = make_adapter(spec)
            for role in runagent.READ_ONLY_ROLES:
                for allow_write in (False, True):
                    policy = runagent.role_policy(role, allow_write)
                    argv = adapter.build_argv_for_role("prompt", policy)
                    self.assertEqual(argv, adapter.build_argv("prompt"))
                    for flag in WRITE_ENABLING:
                        self.assertNotIn(flag, argv, f"{spec.name}/{role} got {flag}")

    def test_claude_write_role_drops_the_deny_list(self):
        spec = next(a for a in self.agents if a.name == "claude")
        argv = make_adapter(spec).build_argv_for_role("p", runagent.role_policy("implement", True))
        self.assertNotIn("--disallowed-tools", argv)
        self.assertNotIn("Edit,Write,NotebookEdit,Bash", argv)
        self.assertEqual(argv[:2], ["claude", "-p"])

    def test_codex_write_role_moves_to_workspace_write(self):
        spec = next(a for a in self.agents if a.name == "codex")
        argv = make_adapter(spec).build_argv_for_role("p", runagent.role_policy("fix", True))
        self.assertIn("workspace-write", argv)
        self.assertNotIn("read-only", argv)

    def test_agy_write_role_drops_the_sandbox(self):
        spec = next(a for a in self.agents if a.name == "agy")
        argv = make_adapter(spec).build_argv_for_role("p", runagent.role_policy("implement", True))
        self.assertNotIn("--sandbox", argv)
        self.assertIn("--dangerously-skip-permissions", argv)

    def test_model_still_selected_in_a_write_role(self):
        spec = AgentSpec(name="claude", vendor="anthropic", command="claude", model="opus")
        argv = make_adapter(spec).build_argv_for_role("p", runagent.role_policy("implement", True))
        self.assertIn("--model", argv)
        self.assertIn("opus", argv)

    def test_generic_cli_profile_uses_its_configured_args_either_way(self):
        spec = AgentSpec(name="aider", vendor="cli", command="aider", extra_args=["--yes"])
        adapter = make_adapter(spec)
        proc = subprocess.CompletedProcess([], 0, "done", "")
        with (
            mock.patch("ai_jury.adapters.shutil.which", return_value="/usr/bin/aider"),
            mock.patch("ai_jury.adapters._spawn", return_value=proc) as spawned,
        ):
            adapter.run("p", role_policy=runagent.role_policy("review"))
            adapter.run("p", role_policy=runagent.role_policy("implement", True))
        read_only, write = (call.args[0] for call in spawned.call_args_list)
        self.assertEqual(read_only, write)
        self.assertEqual(read_only, ["aider", "--yes"])

    def test_network_adapter_ignores_the_policy(self):
        spec = AgentSpec(name="claude-api", vendor="anthropic-api", model="claude-opus-4-5")
        adapter = make_adapter(spec)
        with (
            mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}, clear=False),
            mock.patch(
                "ai_jury.adapters._post_json",
                return_value=({"content": [{"type": "text", "text": "hi"}]}, None, None),
            ),
        ):
            result = adapter.run("p", role_policy=runagent.role_policy("implement", True))
        self.assertTrue(result.ok)
        self.assertIsNone(result.exit_code)


class BaseAdapterSeamTests(unittest.TestCase):
    """The base class fails closed: no write mode unless a vendor defines one."""

    def test_build_write_argv_defaults_to_the_read_only_argv(self):
        class Toy(Adapter):
            def build_argv(self, prompt):
                del prompt
                return ["toy", "--safe"]

        adapter = Toy(AgentSpec(name="toy", vendor="martian", command="toy"))
        self.assertEqual(adapter.build_write_argv("p"), ["toy", "--safe"])
        self.assertEqual(
            adapter.build_argv_for_role("p", runagent.role_policy("implement", True)),
            ["toy", "--safe"],
        )


class SpawnDetachedTests(unittest.TestCase):
    """The one place `--detach` really forks, exercised on a harmless command."""

    @staticmethod
    def _spawn_and_reap(out_path: Path) -> int:
        """Run a trivial detached child to completion, returning its pid."""
        pid = _spawn_detached([sys.executable, "-c", "print('detached')"], out_path)
        for _ in range(200):
            if out_path.exists() and out_path.read_text(encoding="utf-8").strip():
                break
            time.sleep(0.05)
        # Reap it, so the suite does not warn about a still-running subprocess.
        if os.name != "nt":
            with contextlib.suppress(ChildProcessError, OSError):
                os.waitpid(pid, 0)
        return pid

    @unittest.skipIf(os.name == "nt", "POSIX permission semantics")
    def test_the_child_output_goes_to_an_owner_only_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "run.out"
            self._spawn_and_reap(out_path)
            self.assertIn("detached", out_path.read_text(encoding="utf-8"))
            self.assertEqual(out_path.stat().st_mode & 0o777, 0o600)

    def test_the_child_log_is_written_wherever_we_run(self):
        # Platform-neutral companion to the permission assertion above, which is
        # POSIX-only: Windows chmod is a no-op, but the log must still appear.
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "run.out"
            pid = self._spawn_and_reap(out_path)
            self.assertIsInstance(pid, int)
            self.assertIn("detached", out_path.read_text(encoding="utf-8"))


class EnableWriteTests(unittest.TestCase):
    """``privilege.enable_write`` — the inverse of the read-only enforcement."""

    def test_claude_equals_form_is_dropped(self):
        self.assertEqual(
            privilege.enable_write("anthropic", "claude", ["--disallowed-tools=Bash", "-p"]),
            ["-p"],
        )

    def test_claude_trailing_flag_without_a_value_is_dropped(self):
        self.assertEqual(privilege.enable_write("anthropic", "claude", ["--disallowed-tools"]), [])

    def test_codex_replaces_both_sandbox_spellings(self):
        self.assertEqual(
            privilege.enable_write("openai", "codex", ["--sandbox=read-only"]),
            ["-s", "workspace-write"],
        )
        self.assertEqual(
            privilege.enable_write("openai", "codex", ["-s", "read-only", "--json"]),
            ["-s", "workspace-write", "--json"],
        )

    def test_codex_sandbox_without_a_value_is_still_replaced(self):
        self.assertEqual(
            privilege.enable_write("openai", "codex", ["-s", "--json"]),
            ["-s", "workspace-write", "--json"],
        )

    def test_agy_and_unknown_vendors_drop_the_boolean_sandbox(self):
        self.assertEqual(
            privilege.enable_write("google", "agy", ["--sandbox", "--yolo"]), ["--yolo"]
        )
        self.assertEqual(privilege.enable_write("martian", "x", ["--sandbox=", "-v"]), ["-v"])

    def test_network_vendors_are_untouched(self):
        for vendor in ("local", "anthropic-api", "openai-compatible", "cli", "custom-api"):
            self.assertEqual(privilege.enable_write(vendor, "claude-ish", ["--x"]), ["--x"])

    def test_read_only_enforcement_is_unchanged_by_the_refactor(self):
        self.assertEqual(privilege.enforce_read_only("openai", "codex", []), ["-s", "read-only"])
        self.assertEqual(privilege.enforce_read_only("local", "qwen", ["--x"]), ["--x"])


class AgentResolutionTests(unittest.TestCase):
    def setUp(self):
        self.config = _from_dict(
            {
                "agent": [
                    {"name": "claude", "vendor": "anthropic", "command": "my-claude"},
                    {"name": "retired", "vendor": "google", "command": "agy", "enabled": False},
                ]
            }
        )

    def test_config_entry_wins_over_the_builtin_of_the_same_name(self):
        spec, error = runagent.resolve_agent(self.config, "claude")
        self.assertIsNone(error)
        self.assertEqual(spec.command, "my-claude")

    def test_disabled_agents_are_still_dispatchable(self):
        spec, error = runagent.resolve_agent(self.config, "retired")
        self.assertIsNone(error)
        self.assertEqual(spec.vendor, "google")

    def test_bare_builtin_vendor_needs_no_config_entry(self):
        spec, error = runagent.resolve_agent(self.config, "codex")
        self.assertIsNone(error)
        self.assertEqual((spec.vendor, spec.command), ("openai", "codex"))
        self.assertIn("read-only", spec.extra_args)

    def test_hosted_api_builtin_has_no_command(self):
        spec, error = runagent.resolve_agent(self.config, "openai-api")
        self.assertIsNone(error)
        self.assertEqual(spec.vendor, "openai-api")
        self.assertEqual(spec.command, "")

    def test_model_suffix_overrides_the_resolved_model(self):
        spec, error = runagent.resolve_agent(self.config, "claude:claude-opus-4-5")
        self.assertIsNone(error)
        self.assertEqual(spec.model, "claude-opus-4-5")
        self.assertEqual(spec.command, "my-claude")

    def test_only_the_first_colon_separates(self):
        spec, _ = runagent.resolve_agent(self.config, "codex:qwen2.5-coder:7b")
        self.assertEqual(spec.model, "qwen2.5-coder:7b")

    def test_unknown_agent_names_the_builtins(self):
        spec, error = runagent.resolve_agent(self.config, "nope")
        self.assertIsNone(spec)
        self.assertIn("unknown agent 'nope'", error)
        for builtin in runagent.BUILTIN_AGENTS:
            self.assertIn(builtin, error)

    def test_an_invalid_model_token_is_refused_at_resolution(self):
        spec, error = runagent.resolve_agent(self.config, "claude:--yolo")
        self.assertIsNone(spec)
        self.assertIn("invalid model", error)

    def test_builtin_spec_returns_none_for_a_non_builtin(self):
        self.assertIsNone(runagent.builtin_spec("nope"))

    def test_transport_matches_the_doctor_vocabulary(self):
        self.assertEqual(
            runagent.transport_for(AgentSpec(name="c", vendor="anthropic", command="claude")),
            "cli",
        )
        self.assertEqual(runagent.transport_for(AgentSpec(name="a", vendor="openai-api")), "api")
        self.assertEqual(runagent.transport_for(AgentSpec(name="q", vendor="local")), "local")


class ModelTokenTests(unittest.TestCase):
    def test_valid_model_tokens_are_accepted(self):
        for token in ("gpt-5.2", "qwen2.5-coder:7b", "anthropic/claude-x", "gemini_3"):
            _, model, error = runagent.parse_agent_token(f"agent:{token}")
            self.assertIsNone(error, token)
            self.assertEqual(model, token)

    def test_a_leading_dash_is_rejected(self):
        # Otherwise the token would be forwarded to the CLI as another flag.
        _, model, error = runagent.parse_agent_token("claude:--dangerously-skip-permissions")
        self.assertIsNone(model)
        self.assertIn("invalid model", error)

    def test_shell_metacharacters_are_rejected(self):
        for bad in ("a b", "a;rm -rf /", "a|b", "a$(x)", "a`x`", "a&b", ""):
            _, _, error = runagent.parse_agent_token(f"claude:{bad}")
            self.assertIsNotNone(error, bad)

    def test_empty_and_nameless_tokens_are_rejected(self):
        self.assertIsNotNone(runagent.parse_agent_token("")[2])
        self.assertIsNotNone(runagent.parse_agent_token("  ")[2])
        self.assertIsNotNone(runagent.parse_agent_token(":model")[2])


class AttributionTests(unittest.TestCase):
    def test_model_base_drops_versions_and_transports(self):
        cases = {
            "qwen2.5:7b": "qwen",
            "gemma2": "gemma",
            "llama3.1": "llama",
            "gpt-5.5": "gpt-5",
            "gpt-4o": "gpt-4o",
            "anthropic-api:claude-opus-4-5": "claude-opus-4-5",
            "ollama:qwen2.5:7b": "qwen",
            "": "",
        }
        for raw, expected in cases.items():
            self.assertEqual(runagent.model_base(raw), expected, raw)

    def test_tier_suffixes_collapse_by_design(self):
        # Documented consequence of the family+major rule, kept identical to
        # keel's agents.model_base (ship #2036) so the two projects cannot write
        # different `model:` labels onto the same issue. Pinned so a change has
        # to be deliberate — and coordinated with keel.
        for raw in ("gemini-3.8-flash", "gemini-3.8-flash-high", "gemini-3.8-pro"):
            self.assertEqual(runagent.model_base(raw), "gemini-3", raw)

    def test_hyphen_versioned_families_stay_distinct(self):
        # The other half of the same rule: a vendor that spells its version with
        # hyphens keeps it, so these do NOT collapse together.
        self.assertEqual(runagent.model_base("claude-opus-4-5"), "claude-opus-4-5")
        self.assertEqual(runagent.model_base("claude-opus-4-6"), "claude-opus-4-6")
        self.assertNotEqual(
            runagent.model_base("claude-opus-4-5"), runagent.model_base("claude-opus-4-6")
        )

    def test_the_exact_model_id_survives_in_the_document(self):
        # The coarse label is a grouping, not an identity; `model` carries the
        # verbatim id for anyone who needs to know exactly what ran.
        spec = AgentSpec(name="a", vendor="google", model="gemini-3.8-flash-high")
        doc = runagent.result_dict(spec, "review", AgentResult("a", "google", True, "x", 0.0))
        self.assertEqual(doc["model"], "gemini-3.8-flash-high")
        self.assertEqual(doc["attribution"]["label"], "agent:google model:gemini-3")

    def test_a_non_transport_prefix_is_kept(self):
        self.assertEqual(runagent.strip_transport("qwen2.5:7b"), "qwen2.5:7b")

    def test_label_pairs_agent_and_versionless_model(self):
        got = runagent.attribution("anthropic", "claude-opus-4-5")
        self.assertEqual(got["label"], "agent:anthropic model:claude-opus-4-5")
        self.assertEqual(got["model"], "claude-opus-4-5")

    def test_label_omits_the_model_when_none_is_known(self):
        self.assertEqual(runagent.attribution("openai")["label"], "agent:openai")


class ResultSchemaTests(unittest.TestCase):
    """The v1 export is a contract: pin every key and its type."""

    EXPECTED = {
        "schema_version": str,
        "ok": bool,
        "agent": str,
        "vendor": str,
        "model": (str, type(None)),
        "role": str,
        "transport": str,
        "text": str,
        "exit_code": (int, type(None)),
        "duration_s": float,
        "timed_out": bool,
        "error_code": (str, type(None)),
        "error": (str, type(None)),
        "attribution": dict,
    }

    def _document(self, result):
        spec = AgentSpec(name="claude", vendor="anthropic", command="claude", model="opus-4-5")
        return runagent.result_dict(spec, "review", result)

    def test_schema_keys_are_exactly_pinned(self):
        doc = self._document(AgentResult("claude", "anthropic", True, "hi", 1.5, exit_code=0))
        self.assertEqual(set(doc), set(self.EXPECTED))
        for key, kind in self.EXPECTED.items():
            self.assertIsInstance(doc[key], kind, key)
        self.assertEqual(doc["schema_version"], "ai-jury.run-agent.v1")
        self.assertEqual(set(doc["attribution"]), {"vendor", "model", "label"})

    def test_a_timeout_is_flagged(self):
        result = AgentResult(
            "claude", "anthropic", False, "", 9.0, "timed out after 9s", error_code="timeout"
        )
        doc = self._document(result)
        self.assertTrue(doc["timed_out"])
        self.assertFalse(doc["ok"])
        self.assertEqual(doc["error_code"], "timeout")

    def test_a_failure_keeps_the_same_keys(self):
        result = AgentResult(
            "claude", "anthropic", False, "", 0.0, "exit 3: boom", error_code="nonzero_exit"
        )
        doc = self._document(result)
        self.assertEqual(set(doc), set(self.EXPECTED))
        self.assertFalse(doc["timed_out"])


class ExitCodeTests(unittest.TestCase):
    """The adapter now records the child's real exit status."""

    def _run_with(self, returncode, stdout="ok", stderr=""):
        spec = AgentSpec(name="claude", vendor="anthropic", command="claude")
        adapter = make_adapter(spec)
        with (
            mock.patch("ai_jury.adapters.shutil.which", return_value="/usr/bin/claude"),
            mock.patch(
                "ai_jury.adapters._spawn",
                return_value=subprocess.CompletedProcess([], returncode, stdout, stderr),
            ),
        ):
            return adapter.run("p")

    def test_success_records_zero(self):
        self.assertEqual(self._run_with(0).exit_code, 0)

    def test_failure_records_the_code(self):
        self.assertEqual(self._run_with(3, stdout="", stderr="boom").exit_code, 3)

    def test_empty_output_records_the_code(self):
        result = self._run_with(0, stdout="")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.error_code, "empty_output")

    def test_missing_cli_has_no_exit_code(self):
        spec = AgentSpec(name="claude", vendor="anthropic", command="nope")
        with mock.patch("ai_jury.adapters.shutil.which", return_value=None):
            result = make_adapter(spec).run("p")
        self.assertIsNone(result.exit_code)


class CliTests(unittest.TestCase):
    """End-to-end through ``jury run-agent``, always on the mock adapter."""

    def test_mock_review_prints_the_json_document(self):
        with _workspace() as root:
            code, out, err = _run(
                [
                    "--agent",
                    "claude",
                    "--role",
                    "review",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "jury.toml"),
                    "--mock",
                ]
            )
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual(doc["schema_version"], "ai-jury.run-agent.v1")
        self.assertEqual(doc["role"], "review")
        self.assertEqual(doc["model"], "claude-opus-4-5")
        self.assertEqual(doc["attribution"]["label"], "agent:anthropic model:claude-opus-4-5")
        self.assertIn("run-agent:", err)

    def test_format_text_prints_only_the_agent_text(self):
        with _workspace() as root:
            code, out, _ = _run(
                [
                    "--agent",
                    "claude",
                    "--role",
                    "chair",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "jury.toml"),
                    "--mock",
                    "--format",
                    "text",
                ]
            )
        self.assertEqual(code, 0)
        self.assertNotIn("schema_version", out)
        self.assertIn("Verdict", out)

    def test_implement_without_allow_write_exits_2(self):
        with _workspace() as root:
            code, out, err = _run(
                [
                    "--agent",
                    "claude",
                    "--role",
                    "implement",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "jury.toml"),
                    "--mock",
                ]
            )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("--allow-write", err)

    def test_implement_with_allow_write_runs(self):
        with _workspace() as root:
            code, out, _ = _run(
                [
                    "--agent",
                    "claude",
                    "--role",
                    "implement",
                    "--allow-write",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "jury.toml"),
                    "--mock",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["role"], "implement")

    def test_allow_write_on_a_review_warns_on_stderr(self):
        with _workspace() as root:
            code, _, err = _run(
                [
                    "--agent",
                    "claude",
                    "--role",
                    "review",
                    "--allow-write",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "jury.toml"),
                    "--mock",
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn("warning:", err)

    def test_unknown_role_exits_2(self):
        with _workspace() as root:
            code, _, err = _run(
                ["--agent", "claude", "--role", "deploy", "--prompt-file", str(root / "prompt.md")]
            )
        self.assertEqual(code, 2)
        self.assertIn("unknown role", err)

    def test_missing_required_flags_exit_2(self):
        code, _, err = _run(["--agent", "claude"])
        self.assertEqual(code, 2)
        self.assertIn("--role", err)
        self.assertIn("--prompt-file", err)

    def test_missing_prompt_file_exits_2(self):
        with _workspace() as root:
            code, _, err = _run(
                [
                    "--agent",
                    "claude",
                    "--role",
                    "review",
                    "--prompt-file",
                    str(root / "nope.md"),
                    "--config",
                    str(root / "jury.toml"),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("prompt file not found", err)

    def test_empty_prompt_exits_2(self):
        with _workspace(prompt="   \n") as root:
            code, _, err = _run(
                [
                    "--agent",
                    "claude",
                    "--role",
                    "review",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "jury.toml"),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("empty prompt", err)

    def test_oversized_prompt_exits_2(self):
        with _workspace() as root, mock.patch("ai_jury.cli._MAX_PROMPT_BYTES", 2):
            code, _, err = _run(
                [
                    "--agent",
                    "claude",
                    "--role",
                    "review",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "jury.toml"),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("limit", err)

    def test_prompt_from_stdin(self):
        with _workspace() as root:
            stdin = mock.MagicMock()
            stdin.buffer.read.return_value = b"from stdin"
            with mock.patch("ai_jury.cli.sys.stdin", stdin):
                code, out, _ = _run(
                    [
                        "--agent",
                        "claude",
                        "--role",
                        "review",
                        "--prompt-file",
                        "-",
                        "--config",
                        str(root / "jury.toml"),
                        "--mock",
                    ]
                )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["ok"])

    def test_unavailable_stdin_exits_2(self):
        with _workspace() as root, mock.patch("ai_jury.cli.sys.stdin", None):
            code, _, err = _run(
                [
                    "--agent",
                    "claude",
                    "--role",
                    "review",
                    "--prompt-file",
                    "-",
                    "--config",
                    str(root / "jury.toml"),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("stdin", err)

    def test_unknown_agent_exits_2(self):
        with _workspace() as root:
            code, _, err = _run(
                [
                    "--agent",
                    "ghost",
                    "--role",
                    "review",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "jury.toml"),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("unknown agent", err)

    def test_bad_config_exits_2(self):
        with _workspace() as root:
            code, _, err = _run(
                [
                    "--agent",
                    "claude",
                    "--role",
                    "review",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "missing.toml"),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("error:", err)

    def test_bad_cwd_exits_2(self):
        with _workspace() as root:
            code, _, err = _run(
                [
                    "--agent",
                    "claude",
                    "--role",
                    "review",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "jury.toml"),
                    "--cwd",
                    str(root / "nowhere"),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("--cwd", err)

    def test_cwd_is_honoured(self):
        seen = {}

        def fake_run(*_args, **_kwargs):
            seen["cwd"] = Path.cwd().resolve()
            return AgentResult("claude", "anthropic", True, "ok", 0.1, exit_code=0)

        with _workspace() as root:
            (root / "sub").mkdir()
            with mock.patch("ai_jury.adapters.MockAdapter.run", fake_run):
                code, _, _ = _run(
                    [
                        "--agent",
                        "claude",
                        "--role",
                        "review",
                        "--prompt-file",
                        str(root / "prompt.md"),
                        "--config",
                        str(root / "jury.toml"),
                        "--cwd",
                        str(root / "sub"),
                        "--mock",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(seen["cwd"], (root / "sub").resolve())

    def test_a_positive_timeout_overrides_the_agent_bound(self):
        seen = {}

        def fake_run(self, prompt, phase="review", timeout=None, role_policy=None):  # noqa: ARG001
            seen["timeout"] = self.spec.timeout
            return AgentResult("claude", "anthropic", True, "ok", 0.1, exit_code=0)

        with _workspace() as root, mock.patch("ai_jury.adapters.MockAdapter.run", fake_run):
            code, _, _ = _run(
                [
                    "--agent",
                    "claude",
                    "--role",
                    "review",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "jury.toml"),
                    "--timeout",
                    "42",
                    "--mock",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(seen["timeout"], 42)

    def test_non_positive_timeout_exits_2(self):
        with _workspace() as root:
            code, _, err = _run(
                [
                    "--agent",
                    "claude",
                    "--role",
                    "review",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "jury.toml"),
                    "--timeout",
                    "0",
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("--timeout", err)

    def test_effort_on_an_unsupported_vendor_warns_once(self):
        with _workspace() as root:
            code, _, err = _run(
                [
                    "--agent",
                    "claude",
                    "--role",
                    "review",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "jury.toml"),
                    "--effort",
                    "high",
                    "--mock",
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn("effort unsupported", err)

    def test_effort_reaches_a_supported_vendor(self):
        with _workspace() as root:
            code, out, _ = _run(
                [
                    "--agent",
                    "google-api:gemini-3.8-flash",
                    "--role",
                    "review",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "jury.toml"),
                    "--effort",
                    "high",
                    "--mock",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["vendor"], "google-api")

    def test_a_failing_agent_exits_1_with_a_readable_document(self):
        def fake_run(*_args, **_kwargs):
            return AgentResult(
                "claude", "anthropic", False, "", 0.2, "exit 3: boom", error_code="nonzero_exit"
            )

        with _workspace() as root, mock.patch("ai_jury.adapters.MockAdapter.run", fake_run):
            code, out, _ = _run(
                [
                    "--agent",
                    "claude",
                    "--role",
                    "review",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "jury.toml"),
                    "--mock",
                ]
            )
        self.assertEqual(code, 1)
        doc = json.loads(out)
        self.assertFalse(doc["ok"])
        self.assertEqual(doc["error_code"], "nonzero_exit")

    def test_main_dispatches_the_subcommand(self):
        with _workspace() as root:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(
                    [
                        "run-agent",
                        "--agent",
                        "claude",
                        "--role",
                        "gate",
                        "--prompt-file",
                        str(root / "prompt.md"),
                        "--config",
                        str(root / "jury.toml"),
                        "--mock",
                    ]
                )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["role"], "gate")


class RolePolicyReachesTheCliTests(unittest.TestCase):
    """The argv a real (patched-spawn) adapter receives from the CLI."""

    def _argv_for(self, extra):
        proc = subprocess.CompletedProcess([], 0, "reviewed", "")
        with (
            _workspace() as root,
            mock.patch("ai_jury.adapters.shutil.which", return_value="/usr/bin/claude"),
            mock.patch("ai_jury.adapters._spawn", return_value=proc) as spawned,
        ):
            code, _, _ = _run(
                [
                    "--agent",
                    "claude",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "jury.toml"),
                    *extra,
                ]
            )
        return code, list(spawned.call_args.args[0])

    def test_a_review_run_keeps_the_deny_list(self):
        code, argv = self._argv_for(["--role", "review"])
        self.assertEqual(code, 0)
        self.assertIn("--disallowed-tools", argv)
        for flag in WRITE_ENABLING:
            self.assertNotIn(flag, argv)

    def test_a_review_run_with_allow_write_still_keeps_it(self):
        _, argv = self._argv_for(["--role", "review", "--allow-write"])
        self.assertIn("--disallowed-tools", argv)

    def test_an_implement_run_drops_the_deny_list(self):
        _, argv = self._argv_for(["--role", "implement", "--allow-write"])
        self.assertNotIn("--disallowed-tools", argv)


class DetachTests(unittest.TestCase):
    def _detach(self, root, extra=(), spawn=None):
        calls = []

        def recorder(argv, out_path):
            calls.append((list(argv), Path(out_path)))

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = _run_run_agent(
                [
                    "--agent",
                    "claude",
                    "--role",
                    "review",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "jury.toml"),
                    "--cache-dir",
                    str(root / "cache"),
                    "--detach",
                    *extra,
                ],
                spawn=spawn or recorder,
            )
        return code, out.getvalue(), err.getvalue(), calls

    def test_detach_writes_a_running_state_and_spawns_a_child(self):
        with _workspace() as root:
            code, out, _, calls = self._detach(root, ["--run-id", "abc123"])
            self.assertEqual(code, 0)
            doc = json.loads(out)
            self.assertEqual(doc["status"], "running")
            self.assertEqual(doc["run_id"], "abc123")
            state = runagent.read_state("abc123", root / "cache")
            self.assertEqual(state["status"], "running")
            argv, out_path = calls[0]
            self.assertIn("run-agent", argv)
            self.assertIn("--_child", argv)
            self.assertEqual(out_path.name, "abc123.out")

    def test_detach_generates_a_run_id_when_none_is_given(self):
        with _workspace() as root:
            code, out, _, _ = self._detach(root)
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(out)["run_id"])

    def test_detach_refuses_a_duplicate_run_id(self):
        with _workspace() as root:
            self._detach(root, ["--run-id", "dup"])
            code, _, err, _ = self._detach(root, ["--run-id", "dup"])
            self.assertEqual(code, 2)
            self.assertIn("already exists", err)

    def test_detach_refuses_an_unsafe_run_id(self):
        with _workspace() as root:
            code, _, err, _ = self._detach(root, ["--run-id", "../escape"])
            self.assertEqual(code, 2)
            self.assertIn("invalid run id", err)

    def test_detach_refuses_a_stdin_prompt(self):
        with _workspace() as root:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = _run_run_agent(
                    [
                        "--agent",
                        "claude",
                        "--role",
                        "review",
                        "--prompt-file",
                        "-",
                        "--config",
                        str(root / "jury.toml"),
                        "--detach",
                    ],
                    spawn=lambda *_args: None,
                )
            self.assertEqual(code, 2)
            self.assertIn("--detach needs a prompt FILE", err.getvalue())

    def test_detach_refuses_a_missing_prompt_file(self):
        with _workspace() as root:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = _run_run_agent(
                    [
                        "--agent",
                        "claude",
                        "--role",
                        "review",
                        "--prompt-file",
                        str(root / "nope.md"),
                        "--config",
                        str(root / "jury.toml"),
                        "--detach",
                    ],
                    spawn=lambda *_args: None,
                )
            self.assertEqual(code, 2)
            self.assertIn("prompt file not found", err.getvalue())

    def test_a_failed_spawn_exits_2(self):
        def boom(*_args):
            raise OSError("no fork for you")

        with _workspace() as root:
            code, _, err, _ = self._detach(root, ["--run-id", "boom"], spawn=boom)
            self.assertEqual(code, 2)
            self.assertIn("could not start", err)

    def test_child_argv_carries_every_forwarded_flag(self):
        namespace = mock.Mock(
            agent="claude:opus",
            role="implement",
            prompt_file="prompt.md",
            cwd="/tmp",
            timeout=30,
            effort="high",
            allow_write=True,
            config="jury.toml",
            cache_dir="cache",
            mock=True,
        )
        argv = _child_argv(namespace, "rid", "/usr/bin/python3")
        self.assertEqual(argv[:5], ["/usr/bin/python3", "-m", "ai_jury", "run-agent", "--agent"])
        for flag in ("--cwd", "--timeout", "--effort", "--allow-write", "--config", "--mock"):
            self.assertIn(flag, argv)
        self.assertIn("--_child", argv)
        self.assertIn("rid", argv)

    def test_child_argv_omits_unset_flags(self):
        namespace = mock.Mock(
            agent="claude",
            role="review",
            prompt_file="prompt.md",
            cwd=None,
            timeout=None,
            effort=None,
            allow_write=False,
            config=None,
            cache_dir=None,
            mock=False,
        )
        argv = _child_argv(namespace, "rid", sys.executable)
        for flag in ("--cwd", "--timeout", "--effort", "--allow-write", "--config", "--mock"):
            self.assertNotIn(flag, argv)

    def test_child_records_its_result_in_the_state_file(self):
        with _workspace() as root:
            code, _, _ = _run(
                [
                    "--agent",
                    "claude",
                    "--role",
                    "review",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "jury.toml"),
                    "--cache-dir",
                    str(root / "cache"),
                    "--run-id",
                    "child1",
                    "--_child",
                    "--mock",
                ]
            )
            self.assertEqual(code, 0)
            state = runagent.read_state("child1", root / "cache")
            self.assertEqual(state["status"], "done")
            self.assertTrue(state["ok"])
            self.assertEqual(state["run_id"], "child1")

    @unittest.skipIf(os.name == "nt", "POSIX permission semantics")
    def test_state_files_are_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = runagent.write_state("perm", {"run_id": "perm"}, Path(tmp))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_state_is_readable_back_on_every_platform(self):
        # Platform-neutral companion: the write path itself must work on Windows,
        # where the 0600/0700 request above is a no-op the OS does not honour.
        with tempfile.TemporaryDirectory() as tmp:
            runagent.write_state("rt", {"run_id": "rt", "status": "done"}, Path(tmp))
            self.assertEqual(runagent.read_state("rt", Path(tmp))["status"], "done")


class WaitAndStatusTests(unittest.TestCase):
    def test_wait_polls_until_the_child_finishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            runagent.write_state(
                "r1", {"run_id": "r1", "status": "running", "agent": "claude"}, cache
            )
            ticks = {"n": 0}

            def fake_sleep(_seconds):
                ticks["n"] += 1
                if ticks["n"] == 3:
                    runagent.write_state(
                        "r1", {"run_id": "r1", "status": "done", "ok": True}, cache
                    )

            state, timed_out = runagent.wait_for_run(
                "r1", cache_dir=cache, sleep=fake_sleep, clock=lambda: 0.0
            )
            self.assertFalse(timed_out)
            self.assertEqual(state["status"], "done")
            self.assertEqual(ticks["n"], 3)

    def test_wait_gives_up_on_a_fake_clock_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            runagent.write_state("r2", {"run_id": "r2", "status": "running"}, cache)
            now = {"t": 0.0}

            def clock():
                return now["t"]

            def fake_sleep(_seconds):
                now["t"] += 1.0

            state, timed_out = runagent.wait_for_run(
                "r2", cache_dir=cache, timeout=2, sleep=fake_sleep, clock=clock
            )
            self.assertTrue(timed_out)
            self.assertEqual(state["status"], "running")

    def test_cli_wait_prints_the_finished_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            runagent.write_state(
                "done1", {"run_id": "done1", "status": "done", "ok": True, "text": "hi"}, cache
            )
            code, out, _ = _run(["--wait", "done1", "--cache-dir", str(cache)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["text"], "hi")

    def test_cli_wait_exits_1_for_a_failed_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            runagent.write_state("bad", {"run_id": "bad", "status": "done", "ok": False}, cache)
            code, _, _ = _run(["--wait", "bad", "--cache-dir", str(cache)])
            self.assertEqual(code, 1)

    def test_cli_wait_on_an_unknown_run_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = _run(["--wait", "ghost", "--cache-dir", str(tmp)])
            self.assertEqual(code, 2)
            # Answered immediately: --detach reserves the id before returning, so
            # a run with no state file was never started.
            self.assertIn("no such run", err)

    def test_cli_wait_rejects_an_unsafe_run_id(self):
        code, _, err = _run(["--wait", "../etc/passwd"])
        self.assertEqual(code, 2)
        self.assertIn("invalid run id", err)

    def test_cli_wait_reports_a_run_that_vanished_mid_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            runagent.write_state("vanish", {"run_id": "vanish", "status": "running"}, cache)
            with mock.patch.object(runagent, "wait_for_run", return_value=(None, False)):
                code, _, err = _run(["--wait", "vanish", "--cache-dir", str(cache)])
        self.assertEqual(code, 2)
        self.assertIn("disappeared while waiting", err)

    def test_status_lists_runs_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            runagent.write_state(
                "old", {"run_id": "old", "status": "done", "ok": True, "started_at": 1}, cache
            )
            runagent.write_state(
                "new", {"run_id": "new", "status": "running", "started_at": 2}, cache
            )
            code, out, _ = _run(["--status", "--cache-dir", str(cache)])
            self.assertEqual(code, 0)
            runs = json.loads(out)["runs"]
            self.assertEqual([r["run_id"] for r in runs], ["new", "old"])

    def test_an_unreadable_runs_directory_lists_nothing(self):
        with mock.patch.object(Path, "glob", side_effect=OSError("denied")):
            self.assertEqual(runagent.list_runs("/tmp/whatever"), [])

    def test_status_on_an_empty_or_missing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = _run(["--status", "--cache-dir", str(Path(tmp) / "nope")])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["runs"], [])

    def test_corrupt_state_files_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            runagent.runs_dir(cache).mkdir(parents=True)
            (runagent.runs_dir(cache) / "junk.json").write_text("not json", encoding="utf-8")
            (runagent.runs_dir(cache) / "list.json").write_text("[1, 2]", encoding="utf-8")
            self.assertEqual(runagent.list_runs(cache), [])
            self.assertIsNone(runagent.read_state("junk", cache))
            self.assertIsNone(runagent.read_state("list", cache))

    def test_oversized_state_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            runagent.write_state("big", {"run_id": "big", "pad": "x" * 100}, cache)
            with mock.patch.object(runagent, "_MAX_STATE_BYTES", 8):
                self.assertIsNone(runagent.read_state("big", cache))

    def test_run_ids_are_random_and_valid(self):
        run_id = runagent.new_run_id()
        self.assertIsNone(runagent.check_run_id(run_id))
        self.assertNotEqual(run_id, runagent.new_run_id())

    def test_paths_live_under_the_cache_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(runagent.state_path("x", tmp).parent.name, runagent.RUNS_DIR_NAME)
            self.assertEqual(runagent.output_path("x", tmp).name, "x.out")

    def test_default_cache_dir_is_used_when_none_is_given(self):
        with mock.patch("ai_jury.cache.default_cache_dir", return_value=Path("/tmp/jc")):
            self.assertEqual(runagent.runs_dir(), Path("/tmp/jc") / runagent.RUNS_DIR_NAME)


class RunIdTraversalTests(unittest.TestCase):
    """A run id must never become a path outside the runs directory.

    Round-1 finding: `--run-id` was validated in the detach *parent* only, and
    the `--_child` branch re-parses its own argv — so a traversal or absolute id
    handed to the child wrote a 0600 file holding the agent's full output
    anywhere the process could reach. The check now lives in `run_path`, which
    is the only place an id becomes a path, so every caller inherits it.
    """

    HOSTILE = (
        "../PWNED",
        "../../PWNED",
        "../../../../private/tmp/PWNED",
        "/private/tmp/PWNED",
        "/etc/passwd",
        "sub/PWNED",
        "..",
        ".hidden",
        "with space",
        "a" * 65,
        "",
    )

    def test_check_run_id_rejects_every_hostile_form(self):
        for run_id in self.HOSTILE:
            self.assertIsNotNone(runagent.check_run_id(run_id), run_id)

    def test_path_construction_refuses_them(self):
        for run_id in self.HOSTILE:
            with self.assertRaises(runagent.RunIdError, msg=run_id):
                runagent.state_path(run_id, "/tmp/whatever")
            with self.assertRaises(runagent.RunIdError, msg=run_id):
                runagent.output_path(run_id, "/tmp/whatever")

    def test_write_state_refuses_them_without_creating_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cache").mkdir()
            for run_id in self.HOSTILE:
                with self.assertRaises(runagent.RunIdError, msg=run_id):
                    runagent.write_state(run_id, {"leak": "x"}, root / "cache")
            # Nothing anywhere under the temp root, inside the cache dir or out.
            self.assertEqual([p.name for p in root.rglob("*") if p.is_file()], [])

    def test_read_state_treats_them_as_a_miss(self):
        for run_id in self.HOSTILE:
            self.assertIsNone(runagent.read_state(run_id, "/tmp/whatever"), run_id)

    def test_the_child_path_refuses_a_traversal_id(self):
        # The proven exploit: the child, not the parent, did the writing.
        with _workspace() as root:
            outside = root / "outside"
            outside.mkdir()
            code, _, err = _run(
                [
                    "--agent",
                    "claude",
                    "--role",
                    "review",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "jury.toml"),
                    "--cache-dir",
                    str(root / "cache"),
                    "--run-id",
                    "../../outside/PWNED",
                    "--_child",
                    "--mock",
                ]
            )
            self.assertEqual(code, 2)
            self.assertIn("invalid run id", err)
            self.assertEqual(list(outside.iterdir()), [])

    def test_the_child_path_refuses_an_absolute_id(self):
        with _workspace() as root:
            outside = root / "outside"
            outside.mkdir()
            code, _, err = _run(
                [
                    "--agent",
                    "claude",
                    "--role",
                    "review",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "jury.toml"),
                    "--cache-dir",
                    str(root / "cache"),
                    "--run-id",
                    str(outside / "ABSPWNED"),
                    "--_child",
                    "--mock",
                ]
            )
            self.assertEqual(code, 2)
            self.assertIn("invalid run id", err)
            self.assertEqual(list(outside.iterdir()), [])

    def test_wait_refuses_a_traversal_id(self):
        code, _, err = _run(["--wait", "../../etc/passwd"])
        self.assertEqual(code, 2)
        self.assertIn("invalid run id", err)

    def test_a_containment_breach_is_caught_even_if_the_pattern_loosens(self):
        # Second barrier: if _RUN_ID_RE were ever widened, run_path still refuses
        # anything that does not land directly in the runs directory.
        with (
            mock.patch.object(runagent, "check_run_id", return_value=None),
            self.assertRaises(runagent.RunIdError),
        ):
            runagent.state_path("../escape", "/tmp/whatever")

    def test_a_valid_id_still_resolves_inside_the_runs_directory(self):
        path = runagent.state_path("run-1.a_B", "/tmp/whatever")
        self.assertEqual(path.parent, runagent.runs_dir("/tmp/whatever"))
        self.assertEqual(path.name, "run-1.a_B.json")


class RunIdUsageTests(unittest.TestCase):
    def test_run_id_without_detach_is_refused(self):
        with _workspace() as root:
            code, _, err = _run(
                [
                    "--agent",
                    "claude",
                    "--role",
                    "review",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "jury.toml"),
                    "--run-id",
                    "orphan",
                    "--mock",
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("--run-id only applies to a detached run", err)

    def test_an_unsafe_run_id_is_refused_at_parse_time(self):
        code, _, err = _run(["--run-id", "../x", "--status"])
        self.assertEqual(code, 2)
        self.assertIn("--run-id", err)


class LivenessTests(unittest.TestCase):
    """A detached run must not sit at `running` forever."""

    def test_a_dead_child_is_reported_lost(self):
        state = {"run_id": "r", "status": "running", "pid": 4242}
        summary = runagent.run_summary(state, alive_fn=lambda _pid: False)
        self.assertEqual(summary["status"], runagent.STATUS_LOST)

    def test_a_live_child_stays_running(self):
        state = {"run_id": "r", "status": "running", "pid": 4242}
        self.assertEqual(
            runagent.run_summary(state, alive_fn=lambda _pid: True)["status"], "running"
        )

    def test_unknown_liveness_stays_running(self):
        # No pid recorded, or a platform we refuse to probe: "we don't know" must
        # not be reported as "dead".
        state = {"run_id": "r", "status": "running", "pid": None}
        self.assertEqual(
            runagent.run_summary(state, alive_fn=lambda _pid: None)["status"], "running"
        )

    def test_a_finished_run_is_never_relabelled(self):
        state = {"run_id": "r", "status": "done", "ok": True, "pid": 4242}
        self.assertEqual(runagent.run_summary(state, alive_fn=lambda _pid: False)["status"], "done")

    def test_status_marks_a_dead_run_lost(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            runagent.write_state(
                "zombie", {"run_id": "zombie", "status": "running", "pid": 999999}, cache
            )
            with mock.patch.object(runagent, "pid_alive", return_value=False):
                code, out, _ = _run(["--status", "--cache-dir", str(cache)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["runs"][0]["status"], "lost")

    def test_pid_alive_knows_this_process_is_running(self):
        self.assertTrue(runagent.pid_alive(os.getpid()))

    def test_pid_alive_is_unknown_for_a_bad_pid(self):
        for pid in (None, 0, -1, "x"):
            self.assertIsNone(runagent.pid_alive(pid))

    @unittest.skipIf(os.name == "nt", "POSIX signal semantics")
    def test_pid_alive_probes_a_real_reaped_child(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        # A pid can in principle be recycled between the wait and the probe, so
        # this asserts the probe RUNS and answers definitively rather than
        # pinning which answer; `--status` only demotes on a definite False.
        self.assertIn(runagent.pid_alive(proc.pid), (False, True))

    def test_windows_never_probes_a_pid(self):
        # os.kill(pid, 0) TERMINATES the process on Windows, so the probe must
        # not run there at all.
        with (
            mock.patch.object(runagent.os, "name", "nt"),
            mock.patch.object(runagent.os, "kill") as killed,
        ):
            self.assertIsNone(runagent.pid_alive(4242))
            killed.assert_not_called()

    def test_a_permission_error_means_alive(self):
        with mock.patch.object(runagent.os, "kill", side_effect=PermissionError):
            self.assertTrue(runagent.pid_alive(4242))

    def test_an_unexpected_oserror_is_unknown(self):
        with mock.patch.object(runagent.os, "kill", side_effect=OSError):
            self.assertIsNone(runagent.pid_alive(4242))

    def test_the_detach_parent_records_the_child_pid(self):
        with _workspace() as root:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = _run_run_agent(
                    [
                        "--agent",
                        "claude",
                        "--role",
                        "review",
                        "--prompt-file",
                        str(root / "prompt.md"),
                        "--config",
                        str(root / "jury.toml"),
                        "--cache-dir",
                        str(root / "cache"),
                        "--detach",
                        "--run-id",
                        "withpid",
                    ],
                    spawn=lambda *_args: 31337,
                )
            self.assertEqual(code, 0)
            self.assertEqual(runagent.read_state("withpid", root / "cache")["pid"], 31337)

    def test_a_child_that_crashes_still_writes_a_terminal_state(self):
        def boom(*_args, **_kwargs):
            raise RuntimeError("adapter exploded")

        with _workspace() as root, mock.patch("ai_jury.adapters.MockAdapter.run", boom):
            with self.assertRaises(RuntimeError):
                _run(
                    [
                        "--agent",
                        "claude",
                        "--role",
                        "review",
                        "--prompt-file",
                        str(root / "prompt.md"),
                        "--config",
                        str(root / "jury.toml"),
                        "--cache-dir",
                        str(root / "cache"),
                        "--run-id",
                        "crashed",
                        "--_child",
                        "--mock",
                    ]
                )
            state = runagent.read_state("crashed", root / "cache")
        self.assertIsNotNone(state)
        self.assertEqual(state["status"], "done")
        self.assertFalse(state["ok"])
        self.assertIn("exited before the agent finished", state["error"])

    def test_a_child_that_cannot_record_still_prints_its_document(self):
        with (
            _workspace() as root,
            mock.patch.object(runagent, "write_state", side_effect=OSError("disk full")),
        ):
            code, out, err = _run(
                [
                    "--agent",
                    "claude",
                    "--role",
                    "review",
                    "--prompt-file",
                    str(root / "prompt.md"),
                    "--config",
                    str(root / "jury.toml"),
                    "--cache-dir",
                    str(root / "cache"),
                    "--run-id",
                    "nodisk",
                    "--_child",
                    "--mock",
                ]
            )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["ok"])
        self.assertIn("could not record run state", err)


class WaitTimeoutTests(unittest.TestCase):
    def test_the_default_deadline_comes_from_the_run_s_own_timeout(self):
        self.assertEqual(
            runagent.default_wait_timeout({"timeout_s": 600}), 600 + runagent.WAIT_GRACE_S
        )

    def test_a_run_without_a_recorded_timeout_falls_back(self):
        for state in (None, {}, {"timeout_s": 0}, {"timeout_s": "x"}, {"timeout_s": True}):
            self.assertEqual(runagent.default_wait_timeout(state), runagent.DEFAULT_WAIT_TIMEOUT_S)

    def test_wait_is_bounded_even_with_no_flag(self):
        # Previously `--wait` with no --timeout blocked forever on a dead run.
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            runagent.write_state(
                "hung", {"run_id": "hung", "status": "running", "timeout_s": 5}, cache
            )
            now = {"t": 0.0}
            code, _, err = _run_run_agent_capture(
                ["--wait", "hung", "--cache-dir", str(cache)],
                sleep=lambda _s: now.update(t=now["t"] + 10),
                clock=lambda: now["t"],
            )
        self.assertEqual(code, 2)
        self.assertIn("did not finish within 65s", err)

    def test_an_explicit_wait_timeout_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            runagent.write_state(
                "hung2", {"run_id": "hung2", "status": "running", "timeout_s": 5}, cache
            )
            now = {"t": 0.0}
            code, _, err = _run_run_agent_capture(
                ["--wait", "hung2", "--cache-dir", str(cache), "--wait-timeout", "3"],
                sleep=lambda _s: now.update(t=now["t"] + 1),
                clock=lambda: now["t"],
            )
        self.assertEqual(code, 2)
        self.assertIn("did not finish within 3s", err)

    def test_a_dead_run_says_so_when_the_wait_gives_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            runagent.write_state(
                "gone", {"run_id": "gone", "status": "running", "pid": 999999}, cache
            )
            now = {"t": 0.0}
            with mock.patch.object(runagent, "pid_alive", return_value=False):
                code, _, err = _run_run_agent_capture(
                    ["--wait", "gone", "--cache-dir", str(cache), "--wait-timeout", "1"],
                    sleep=lambda _s: now.update(t=now["t"] + 1),
                    clock=lambda: now["t"],
                )
        self.assertEqual(code, 2)
        self.assertIn("its process is gone", err)

    def test_a_non_positive_wait_timeout_is_refused(self):
        code, _, err = _run(["--wait", "x", "--wait-timeout", "0"])
        self.assertEqual(code, 2)
        self.assertIn("--wait-timeout", err)

    def test_timeout_still_bounds_the_agent_not_the_wait(self):
        # The two are now separate flags; --timeout never shortens a wait.
        help_text = " ".join(_run_agent_parser().format_help().split())
        self.assertIn("--wait-timeout", help_text)
        self.assertIn("This bounds the agent, never the wait", help_text)
        self.assertIn("seconds --wait will block before giving up", help_text)


class CredentialLeakTests(unittest.TestCase):
    """No credential-shaped config value reaches the run-agent output.

    `jury run-agent` prints one JSON document to stdout, mirrors it into a
    detached run's state file, and streams progress to stderr — three new sinks
    for whatever an adapter puts in `AgentResult.error`. For a hosted-API agent
    that text names the environment variable to set, and `[[agent]] api_key_env`
    is an arbitrary operator string, so it must arrive rebuilt through
    `redaction.safe_env_var_name` rather than passed through (the class CodeQL
    flags as `py/clear-text-logging-sensitive-data`). These pin that the barrier
    holds all the way to the export, not just at the adapter.
    """

    CONFIG = """\
[[agent]]
name = "hosted"
vendor = "openai-api"
model = "gpt-5.2"
api_key_env = "%s"
"""

    def _document(self, api_key_env: str) -> dict:
        with _workspace(config_text=self.CONFIG % api_key_env) as root:
            env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
            with mock.patch.dict(os.environ, env, clear=True):
                code, out, err = _run(
                    [
                        "--agent",
                        "hosted",
                        "--role",
                        "review",
                        "--prompt-file",
                        str(root / "prompt.md"),
                        "--config",
                        str(root / "jury.toml"),
                    ]
                )
        self.assertEqual(code, 1)  # fail-soft: the agent ran and could not
        return json.loads(out), err

    def test_a_well_formed_env_var_name_is_reported_to_the_operator(self):
        # The point of the message is actionable: name the variable to set.
        doc, _ = self._document("MY_HOSTED_KEY")
        self.assertEqual(doc["error_code"], "missing_api_key")
        self.assertIn("MY_HOSTED_KEY", doc["error"])

    def test_a_malformed_env_var_name_is_never_echoed(self):
        # A name carrying characters an env var cannot hold falls back to the
        # vendor constant instead of being spliced into the document.
        hostile = "BAD-NAME;injected"
        doc, err = self._document(hostile)
        self.assertNotIn("injected", json.dumps(doc))
        self.assertNotIn(hostile, json.dumps(doc))
        self.assertNotIn(hostile, err)
        self.assertIn("OPENAI_API_KEY", doc["error"])

    def test_the_result_document_never_exports_the_credential_fields(self):
        doc, _ = self._document("MY_HOSTED_KEY")
        self.assertNotIn("api_key_env", doc)
        self.assertNotIn("api_key_env", doc["attribution"])

    def test_the_detached_state_file_carries_the_same_sanitized_text(self):
        # The state file is a second sink for the same string; it must not be a
        # way around the barrier the stdout document goes through.
        hostile = "NOPE\nX-Injected: 1"
        with _workspace(config_text=self.CONFIG % hostile.replace("\n", "\\n")) as root:
            env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
            with mock.patch.dict(os.environ, env, clear=True):
                _run(
                    [
                        "--agent",
                        "hosted",
                        "--role",
                        "review",
                        "--prompt-file",
                        str(root / "prompt.md"),
                        "--config",
                        str(root / "jury.toml"),
                        "--cache-dir",
                        str(root / "cache"),
                        "--run-id",
                        "leak",
                        "--_child",
                    ]
                )
            state = runagent.read_state("leak", root / "cache")
        self.assertIsNotNone(state)
        self.assertNotIn("X-Injected", json.dumps(state))
        self.assertIn("OPENAI_API_KEY", state["error"])


class HelpSurfaceTests(unittest.TestCase):
    """`jury run-agent --help` names every documented flag."""

    def test_help_lists_the_documented_flags(self):
        from ai_jury.cli import _run_agent_parser

        help_text = _run_agent_parser().format_help()
        for flag in (
            "--agent",
            "--role",
            "--prompt-file",
            "--cwd",
            "--timeout",
            "--effort",
            "--allow-write",
            "--format",
            "--detach",
            "--run-id",
            "--wait",
            "--status",
        ):
            self.assertIn(flag, help_text, f"{flag} missing from run-agent --help")

    def test_top_level_help_names_the_subcommand(self):
        # Version-independent companion to the 3.13-pinned help golden: the
        # argv-intercept subcommands are invisible to argparse, so the epilog is
        # the only place `--help` can mention them.
        from ai_jury.cli import build_parser

        help_text = build_parser().format_help()
        for name in ("run-agent", "init", "apply", "replay"):
            self.assertIn(name, help_text)

    def test_the_child_flag_is_hidden(self):
        from ai_jury.cli import _run_agent_parser

        self.assertNotIn("--_child", _run_agent_parser().format_help())


if __name__ == "__main__":
    unittest.main()
