"""Unit tests for the least-privilege agent auditor (OWASP LLM01 defense).

Locks the behaviour that dangerous agent invocations are surfaced as warnings
while a read-only / locked-down config is not. Stdlib + offline.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_review_council import privilege
from agent_review_council.config import AgentSpec


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


class AuditPrivilegeTest(unittest.TestCase):
    def test_dangerous_config_produces_warnings(self):
        specs = [
            AgentSpec(name="claude", vendor="anthropic", command="claude", extra_args=[]),
            AgentSpec(
                name="codex", vendor="openai", command="codex",
                extra_args=["-s", "danger-full-access"],
            ),
            AgentSpec(
                name="agy", vendor="google", command="agy",
                extra_args=["--dangerously-skip-permissions"],
            ),
        ]
        warnings = privilege.audit_privilege(specs)
        # One warning per dangerous agent.
        self.assertEqual(len(warnings), 3)

    def test_locked_down_config_has_no_warnings(self):
        specs = [
            AgentSpec(
                name="claude", vendor="anthropic", command="claude",
                extra_args=["--disallowed-tools", "Edit,Write,NotebookEdit,Bash"],
            ),
            AgentSpec(
                name="codex", vendor="openai", command="codex",
                extra_args=["-s", "read-only"],
            ),
        ]
        self.assertEqual(privilege.audit_privilege(specs), [])

    def test_empty_specs_has_no_warnings(self):
        self.assertEqual(privilege.audit_privilege([]), [])


if __name__ == "__main__":
    unittest.main()
