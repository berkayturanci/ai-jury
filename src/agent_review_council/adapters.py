"""Agent adapters — each wraps one native coding-agent CLI in headless mode.

Every adapter turns a prompt into a subprocess invocation and captures stdout as
the agent's response. Adapters are intentionally thin: the orchestrator owns the
prompt content and the round structure; an adapter only knows how to *invoke its
CLI*.

Headless invocations (verified against installed CLIs, early 2026):
  - Claude Code : ``claude -p "<prompt>" --output-format text``
  - Codex CLI   : ``codex exec "<prompt>"``  (also: ``codex exec review``)
  - Antigravity : ``agy --print "<prompt>"``
"""
from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass

from .config import AgentSpec


@dataclass
class AgentResult:
    agent: str
    vendor: str
    ok: bool
    output: str
    duration_s: float
    error: str | None = None


class Adapter:
    """Base adapter. Subclasses build the argv for their CLI."""

    def __init__(self, spec: AgentSpec):
        self.spec = spec

    @property
    def name(self) -> str:
        return self.spec.name

    def available(self) -> bool:
        return shutil.which(self.spec.command) is not None

    def build_argv(self, prompt: str) -> list[str]:  # pragma: no cover - overridden
        raise NotImplementedError

    def run(self, prompt: str, phase: str = "review") -> AgentResult:
        if not self.available():
            return AgentResult(
                self.name, self.spec.vendor, False, "",
                0.0, f"command not found on PATH: {self.spec.command}",
            )
        argv = self.build_argv(prompt)
        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.spec.timeout,
            )
        except subprocess.TimeoutExpired:
            return AgentResult(
                self.name, self.spec.vendor, False, "",
                time.monotonic() - start, f"timed out after {self.spec.timeout}s",
            )
        except Exception as exc:  # noqa: BLE001 - surface any spawn failure
            return AgentResult(
                self.name, self.spec.vendor, False, "",
                time.monotonic() - start, f"spawn failed: {exc}",
            )
        dur = time.monotonic() - start
        out = (proc.stdout or "").strip()
        if proc.returncode != 0 and not out:
            return AgentResult(
                self.name, self.spec.vendor, False, "",
                dur, f"exit {proc.returncode}: {(proc.stderr or '').strip()[:500]}",
            )
        return AgentResult(self.name, self.spec.vendor, True, out, dur)


class ClaudeAdapter(Adapter):
    def build_argv(self, prompt: str) -> list[str]:
        argv = [self.spec.command, "-p", prompt]
        if self.spec.model:
            argv += ["--model", self.spec.model]
        return argv + self.spec.extra_args


class CodexAdapter(Adapter):
    def build_argv(self, prompt: str) -> list[str]:
        argv = [self.spec.command, "exec"]
        if self.spec.model:
            argv += ["-m", self.spec.model]
        return argv + self.spec.extra_args + [prompt]


class AgyAdapter(Adapter):
    def build_argv(self, prompt: str) -> list[str]:
        argv = [self.spec.command, "--print", prompt]
        if self.spec.model:
            argv += ["--model", self.spec.model]
        return argv + self.spec.extra_args


class MockAdapter(Adapter):
    """Offline adapter for tests and ``--mock`` runs.

    Produces deterministic, phase-aware text so the full orchestration pipeline
    can run end-to-end without live CLIs, auth, or token spend.
    """

    def available(self) -> bool:
        return True

    def run(self, prompt: str, phase: str = "review") -> AgentResult:
        n = self.name
        if phase == "review":
            body = (
                f"- **[major]** `src/example.py:42` — {n}: unchecked return value "
                f"may swallow an error.\n"
                f"- **[minor]** `src/example.py:7` — {n}: missing docstring."
            )
        elif phase == "debate":
            body = (
                f"## AGREE\n- {n}: confirm the unchecked-return finding at "
                f"`src/example.py:42`.\n"
                f"## DISPUTE\n- {n}: the missing-docstring finding is a nit, not blocking.\n"
                f"## MISSED\n- {n}: no test covers the error branch."
            )
        else:  # synthesis
            body = (
                "## Verdict\nREQUEST CHANGES — one confirmed major issue.\n\n"
                "## Consensus findings\n- **[major]** `src/example.py:42` — unchecked "
                "return value (raised by all reviewers).\n\n"
                "## Disputed findings\n- Missing docstring: ruled non-blocking.\n\n"
                "## Notable single-reviewer findings\n- Missing test for the error branch."
            )
        return AgentResult(n, self.spec.vendor, True, body, 0.0)


_VENDOR_ADAPTERS: dict[str, type[Adapter]] = {
    "anthropic": ClaudeAdapter,
    "openai": CodexAdapter,
    "google": AgyAdapter,
}


def make_adapter(spec: AgentSpec, mock: bool = False) -> Adapter:
    if mock:
        return MockAdapter(spec)
    cls = _VENDOR_ADAPTERS.get(spec.vendor)
    if cls is None:
        # Unknown vendor: treat command as a print-style CLI (prompt as last arg).
        return AgyAdapter(spec)
    return cls(spec)
