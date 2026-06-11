"""Offline tests for adapter argv/stdin construction.

Run with: python -m unittest discover -s tests
No third-party dependencies, no live CLIs, no network — the real ``codex``
binary is never invoked; we only inspect what ``build_argv`` / ``_stdin_for``
would produce.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury.adapters import CodexAdapter  # noqa: E402
from ai_jury.config import AgentSpec  # noqa: E402

PROMPT = "Review this diff and report findings."


def _codex_spec(model: str | None = None, extra_args: list[str] | None = None) -> AgentSpec:
    return AgentSpec(
        name="codex",
        vendor="openai",
        command="codex",
        model=model,
        extra_args=list(extra_args if extra_args is not None else ["-s", "danger-full-access"]),
    )


class CodexAdapterTest(unittest.TestCase):
    def test_build_argv_does_not_contain_prompt(self):
        argv = CodexAdapter(_codex_spec()).build_argv(PROMPT)
        self.assertNotIn(PROMPT, argv)

    def test_stdin_for_returns_prompt(self):
        adapter = CodexAdapter(_codex_spec())
        self.assertEqual(adapter._stdin_for(PROMPT), PROMPT)

    def test_argv_starts_with_command_and_exec(self):
        argv = CodexAdapter(_codex_spec()).build_argv(PROMPT)
        self.assertEqual(argv[0], "codex")
        self.assertIn("exec", argv)

    def test_argv_includes_configured_extra_args(self):
        argv = CodexAdapter(_codex_spec()).build_argv(PROMPT)
        self.assertIn("-s", argv)
        self.assertIn("danger-full-access", argv)

    def test_custom_extra_args_are_passed_through(self):
        # Custom args pass through; the secure-default sandbox (-s read-only) is
        # injected because none was configured (issue #288 enforcement).
        argv = CodexAdapter(_codex_spec(extra_args=["--foo", "bar"])).build_argv(PROMPT)
        self.assertEqual(argv, ["codex", "exec", "-s", "read-only", "--foo", "bar"])

    def test_model_flag_present_when_model_set(self):
        argv = CodexAdapter(_codex_spec(model="gpt-5-codex")).build_argv(PROMPT)
        self.assertIn("-m", argv)
        self.assertEqual(argv[argv.index("-m") + 1], "gpt-5-codex")

    def test_model_flag_absent_when_model_unset(self):
        argv = CodexAdapter(_codex_spec()).build_argv(PROMPT)
        self.assertNotIn("-m", argv)


if __name__ == "__main__":
    unittest.main()
