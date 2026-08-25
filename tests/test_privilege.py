"""Unit tests for the least-privilege agent auditor (OWASP LLM01 defense).

Locks the behaviour that dangerous agent invocations are surfaced as warnings
while a read-only / locked-down config is not. Stdlib + offline.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_jury import privilege
from ai_jury.config import AgentSpec


class AuditAgentTest(unittest.TestCase):
    def test_claude_without_disallowed_tools_warns(self):
        spec = AgentSpec(name="claude", vendor="anthropic", command="claude", extra_args=[])
        warnings = privilege.audit_agent(spec)
        self.assertTrue(warnings)
        self.assertIn("read-only", warnings[0])

    def test_claude_locked_down_has_no_warning(self):
        spec = AgentSpec(
            name="claude",
            vendor="anthropic",
            command="claude",
            extra_args=["--disallowed-tools", "Edit,Write,NotebookEdit,Bash"],
        )
        self.assertEqual(privilege.audit_agent(spec), [])

    def test_claude_partial_disallowed_still_warns(self):
        spec = AgentSpec(
            name="claude",
            vendor="anthropic",
            command="claude",
            extra_args=["--disallowed-tools", "Edit,Write"],
        )
        # Bash/NotebookEdit still permitted → not fully locked down.
        self.assertTrue(privilege.audit_agent(spec))

    def test_codex_danger_full_access_warns(self):
        spec = AgentSpec(
            name="codex",
            vendor="openai",
            command="codex",
            extra_args=["-s", "danger-full-access"],
        )
        warnings = privilege.audit_agent(spec)
        self.assertTrue(warnings)
        self.assertIn("danger-full-access", warnings[0])

    def test_agy_dangerously_skip_permissions_warns(self):
        spec = AgentSpec(
            name="agy",
            vendor="google",
            command="agy",
            extra_args=["--dangerously-skip-permissions"],
        )
        warnings = privilege.audit_agent(spec)
        self.assertTrue(warnings)
        self.assertIn("--dangerously-skip-permissions", warnings[0])

    def test_yolo_flag_warns(self):
        spec = AgentSpec(name="gemini", vendor="google", command="gemini", extra_args=["--yolo"])
        self.assertTrue(privilege.audit_agent(spec))

    def test_full_auto_flag_warns(self):
        spec = AgentSpec(name="codex", vendor="openai", command="codex", extra_args=["--full-auto"])
        self.assertTrue(privilege.audit_agent(spec))

    def test_read_only_codex_has_no_warning(self):
        spec = AgentSpec(
            name="codex", vendor="openai", command="codex", extra_args=["-s", "read-only"]
        )
        self.assertEqual(privilege.audit_agent(spec), [])

    def test_agy_skip_permissions_with_sandbox_has_no_warning(self):
        # Issue #100: --sandbox neutralizes --dangerously-skip-permissions, so the
        # shipped agy default is not flagged.
        spec = AgentSpec(
            name="agy",
            vendor="google",
            command="agy",
            extra_args=["--dangerously-skip-permissions", "--sandbox"],
        )
        self.assertEqual(privilege.audit_agent(spec), [])

    # Issue #300: an unsandboxed non-claude agent must warn even with no
    # dangerous flag (closes the audit blind spot).
    def test_unknown_vendor_unsandboxed_warns(self):
        spec = AgentSpec(name="x", vendor="acme", command="claude", extra_args=[])
        warnings = privilege.audit_agent(spec)
        self.assertTrue(warnings)
        self.assertIn("sandbox", warnings[0].lower())

    def test_codex_no_sandbox_no_dangerous_flag_warns(self):
        # Not being sandboxed is itself the warning condition. `_DANGEROUS_FLAGS`
        # only selects which message is emitted — it is not a second gate an
        # agent can slip past by using a flag that is not on the list.
        #
        # This test existed and was green throughout the #600 review, in the
        # file under review, while four passes asserted the opposite (see #608).
        # `assertTrue` was too quiet to be read as the refutation it was, so the
        # catch-all is now named.
        spec = AgentSpec(name="codex", vendor="openai", command="codex", extra_args=[])
        warnings = privilege.audit_agent(spec)
        self.assertEqual(len(warnings), 1)
        self.assertIn("not running under a recognized read-only sandbox", warnings[0])

    def test_an_unlisted_wide_sandbox_still_warns(self):
        """The generalisation: no `_DANGEROUS_FLAGS` entry is needed to warn.

        A value sandbox that restricts nothing and appears on no list is the
        exact shape #600 believed was a bypass. One warning, via the catch-all.
        """
        spec = AgentSpec(
            name="codex",
            vendor="openai",
            command="codex",
            extra_args=["-s", "some-future-mode-nobody-listed"],
        )
        warnings = privilege.audit_agent(spec)
        self.assertEqual(len(warnings), 1)
        self.assertIn("not running under a recognized read-only sandbox", warnings[0])

    def test_local_vendor_is_not_audited(self):
        # A local/HTTP agent runs no subprocess to sandbox — out of scope.
        spec = AgentSpec(
            name="qwen",
            vendor="local",
            command="",
            model="m",
            endpoint="http://localhost:11434/v1",
        )
        self.assertEqual(privilege.audit_agent(spec), [])


class AuditPrivilegeTest(unittest.TestCase):
    def test_dangerous_config_produces_warnings(self):
        specs = [
            AgentSpec(name="claude", vendor="anthropic", command="claude", extra_args=[]),
            AgentSpec(
                name="codex",
                vendor="openai",
                command="codex",
                extra_args=["-s", "danger-full-access"],
            ),
            AgentSpec(
                name="agy",
                vendor="google",
                command="agy",
                extra_args=["--dangerously-skip-permissions"],
            ),
        ]
        warnings = privilege.audit_privilege(specs)
        # One warning per dangerous agent.
        self.assertEqual(len(warnings), 3)

    def test_codex_workspace_write_produces_dangerous_flag_warning(self):
        spec = AgentSpec(
            name="codex",
            vendor="openai",
            command="codex",
            extra_args=["-s", "workspace-write"],
        )
        warnings = privilege.audit_agent(spec)
        self.assertEqual(len(warnings), 1)
        self.assertIn("workspace-write", warnings[0])
        self.assertIn("granting write/tool/network powers", warnings[0])

    def test_locked_down_config_has_no_warnings(self):
        specs = [
            AgentSpec(
                name="claude",
                vendor="anthropic",
                command="claude",
                extra_args=["--disallowed-tools", "Edit,Write,NotebookEdit,Bash"],
            ),
            AgentSpec(
                name="codex",
                vendor="openai",
                command="codex",
                extra_args=["-s", "read-only"],
            ),
        ]
        self.assertEqual(privilege.audit_privilege(specs), [])

    def test_empty_specs_has_no_warnings(self):
        self.assertEqual(privilege.audit_privilege([]), [])

    def test_shipped_default_config_has_no_warnings(self):
        # Issue #100: the out-of-the-box defaults must be secure (read-only codex,
        # sandboxed agy, locked-down claude) — no least-privilege warnings.
        from ai_jury.config import DEFAULT_CONFIG, _from_dict

        cfg = _from_dict(DEFAULT_CONFIG)
        self.assertEqual(privilege.audit_privilege(cfg.enabled_agents), [])


class IsSandboxedVendorAwareTest(unittest.TestCase):
    """Issue #292: a bare --sandbox token must not give false assurance."""

    def test_bare_sandbox_with_dangerous_flags_not_trusted_for_non_agy(self):
        # The F-5 example: --sandbox followed by broad-powers flags on a vendor
        # whose --sandbox is NOT a boolean restricting sandbox is no longer
        # accepted, so the dangerous flags are surfaced.
        spec = AgentSpec(
            name="custom",
            vendor="openai",
            command="x",
            extra_args=["--sandbox", "--dangerously-skip-permissions", "--yolo"],
        )
        warnings = privilege.audit_agent(spec)
        self.assertTrue(warnings)

    def test_codex_wide_value_sandbox_is_not_sandboxed(self):
        # --sandbox workspace-write takes a non-restricting value -> not a sandbox.
        self.assertFalse(privilege._is_sandboxed(["--sandbox", "workspace-write"], vendor="openai"))

    def test_agy_bare_sandbox_still_trusted(self):
        # The shipped agy default must keep passing (issue #100 not regressed).
        self.assertTrue(
            privilege._is_sandboxed(
                ["--dangerously-skip-permissions", "--sandbox"], vendor="google"
            )
        )

    def test_codex_read_only_value_is_sandboxed(self):
        self.assertTrue(privilege._is_sandboxed(["-s", "read-only"], vendor="openai"))

    def test_equals_form_sandbox_recognized(self):
        # Issue #316/L-6: the audit must recognize the =-form the enforcement
        # already accepts, or it false-positives a safe config under --strict.
        self.assertTrue(privilege._is_sandboxed(["--sandbox=read-only"], vendor="openai"))
        self.assertTrue(privilege._is_sandboxed(["-s=read-only"], vendor="openai"))
        self.assertFalse(privilege._is_sandboxed(["--sandbox=workspace-write"], vendor="openai"))

    def test_codex_equals_read_only_has_no_audit_warning(self):
        spec = AgentSpec(
            name="codex",
            vendor="openai",
            command="codex",
            extra_args=["--sandbox=read-only"],
        )
        self.assertEqual(privilege.audit_agent(spec), [])


class EnforceReadOnlyTest(unittest.TestCase):
    """Issue #288: the sandbox is guaranteed at the adapter layer, not config."""

    def test_claude_injects_disallowed_tools_when_absent(self):
        out = privilege.enforce_read_only("anthropic", "claude", [])
        self.assertEqual(out, ["--disallowed-tools", "Edit,Write,NotebookEdit,Bash"])

    def test_claude_merges_missing_write_tools_into_existing(self):
        out = privilege.enforce_read_only(
            "anthropic", "claude", ["--disallowed-tools", "Edit,Write"]
        )
        self.assertEqual(out, ["--disallowed-tools", "Edit,Write,NotebookEdit,Bash"])

    def test_claude_shipped_default_is_unchanged(self):
        shipped = [
            "--output-format",
            "text",
            "--disallowed-tools",
            "Edit,Write,NotebookEdit,Bash",
            "--dangerously-skip-permissions",
        ]
        self.assertEqual(privilege.enforce_read_only("anthropic", "claude", shipped), shipped)

    def test_claude_equals_form_disallowed_is_merged(self):
        # Review of #288: the =-form must be merged too, not left to sit after the
        # injected safe set where a last-wins CLI could narrow the deny set.
        out = privilege.enforce_read_only("anthropic", "claude", ["--disallowed-tools=Edit"])
        self.assertEqual(out, ["--disallowed-tools=Edit,Write,NotebookEdit,Bash"])

    def test_codex_injects_read_only_when_no_sandbox(self):
        out = privilege.enforce_read_only("openai", "codex", [])
        self.assertEqual(out, ["-s", "read-only"])

    def test_codex_equals_form_sandbox_is_respected_not_doubled(self):
        out = privilege.enforce_read_only("openai", "codex", ["--sandbox=read-only"])
        self.assertEqual(out, ["--sandbox=read-only"])

    def test_codex_respects_operator_widened_sandbox(self):
        # An explicit (audited) opt-in is preserved, never overridden.
        out = privilege.enforce_read_only("openai", "codex", ["-s", "workspace-write"])
        self.assertEqual(out, ["-s", "workspace-write"])

    def test_agy_injects_sandbox_when_absent(self):
        out = privilege.enforce_read_only("google", "agy", ["--dangerously-skip-permissions"])
        self.assertEqual(out, ["--sandbox", "--dangerously-skip-permissions"])

    def test_unknown_vendor_gets_sandbox(self):
        # Issue #310 (completes #300): an unknown vendor routes to the generic
        # AgyAdapter, so --sandbox is injected — fail-closed, never fail-open.
        out = privilege.enforce_read_only("weirdvendor", "x", ["--foo"])
        self.assertEqual(out, ["--sandbox", "--foo"])

    def test_unknown_vendor_existing_sandbox_not_doubled(self):
        out = privilege.enforce_read_only("weirdvendor", "x", ["--sandbox"])
        self.assertEqual(out, ["--sandbox"])

    def test_local_vendor_name_substring_not_mishandled(self):
        # Review of #310: a local agent whose NAME contains "claude"/"codex" must
        # still be left unchanged (the vendor=="local" fast-path wins).
        self.assertEqual(privilege.enforce_read_only("local", "local-claude", []), [])
        self.assertEqual(privilege.enforce_read_only("local", "my-codex", []), [])

    def test_local_vendor_is_left_untouched(self):
        self.assertEqual(privilege.enforce_read_only("local", "qwen", []), [])

    def test_cli_vendor_is_left_untouched(self):
        self.assertEqual(privilege.enforce_read_only("cli", "cursor", ["--print"]), ["--print"])


if __name__ == "__main__":
    unittest.main()
