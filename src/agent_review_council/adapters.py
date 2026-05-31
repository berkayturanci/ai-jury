"""Agent adapters — each wraps one native coding-agent CLI in headless mode.

Every adapter turns a prompt into a subprocess invocation and captures stdout as
the agent's response. Adapters are intentionally thin: the orchestrator owns the
prompt content and the round structure; an adapter only knows how to *invoke its
CLI*.

Headless invocations (verified against installed CLIs, early 2026):
  - Claude Code : ``claude -p "<prompt>" --output-format text``
  - Codex CLI   : ``codex exec <args> < <prompt>``  (prompt piped via stdin)
  - Antigravity : ``agy --print "<prompt>"``
"""
from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field

from .config import AgentSpec

# Stable, typed error taxonomy for failed agent executions. These codes let
# reports and CI/policy distinguish retryable from non-retryable failures
# instead of pattern-matching free-text error strings.
ERR_MISSING_CLI = "missing_cli"
ERR_AUTH_REQUIRED = "auth_required"
ERR_PERMISSION_PROMPT = "permission_prompt"
ERR_TIMEOUT = "timeout"
ERR_NONZERO_EXIT = "nonzero_exit"
ERR_EMPTY_OUTPUT = "empty_output"
ERR_SPAWN_FAILED = "spawn_failed"
ERR_RATE_LIMITED = "rate_limited"
ERR_UNKNOWN = "unknown"

ERROR_CODES = frozenset({
    ERR_MISSING_CLI,
    ERR_AUTH_REQUIRED,
    ERR_PERMISSION_PROMPT,
    ERR_TIMEOUT,
    ERR_NONZERO_EXIT,
    ERR_EMPTY_OUTPUT,
    ERR_SPAWN_FAILED,
    ERR_RATE_LIMITED,
    ERR_UNKNOWN,
})


def classify_stderr(returncode: int, stderr: str) -> str:
    """Classify a nonzero-exit failure into a typed error code from its stderr.

    Heuristic substring matching against the lowercased stderr; ordering matters
    (auth and rate-limit signals are checked before the generic permission and
    nonzero-exit fallbacks). Returns one of the ``ERR_*`` codes.
    """
    text = (stderr or "").lower()
    if any(s in text for s in ("not authenticated", "unauthorized", "api key", "auth", "login")):
        return ERR_AUTH_REQUIRED
    if any(s in text for s in ("rate limit", "429", "quota")):
        return ERR_RATE_LIMITED
    if any(s in text for s in ("permission", "approve", "confirm")):
        return ERR_PERMISSION_PROMPT
    del returncode
    return ERR_NONZERO_EXIT


@dataclass
class AgentResult:
    agent: str
    vendor: str
    ok: bool
    output: str
    duration_s: float
    error: str | None = None
    findings: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    error_code: str | None = None


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

    def _stdin_for(self, prompt: str) -> str | None:
        """Prompt to feed on stdin, or None to pass it in argv (the default)."""
        del prompt
        return None

    def run(self, prompt: str, phase: str = "review") -> AgentResult:
        del phase
        if not self.available():
            return AgentResult(
                self.name, self.spec.vendor, False, "",
                0.0, f"command not found on PATH: {self.spec.command}",
                error_code=ERR_MISSING_CLI,
            )
        argv = self.build_argv(prompt)
        stdin = self._stdin_for(prompt)
        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=self.spec.timeout,
            )
        except subprocess.TimeoutExpired:
            return AgentResult(
                self.name, self.spec.vendor, False, "",
                time.monotonic() - start, f"timed out after {self.spec.timeout}s",
                error_code=ERR_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 - surface any spawn failure
            return AgentResult(
                self.name, self.spec.vendor, False, "",
                time.monotonic() - start, f"spawn failed: {exc}",
                error_code=ERR_SPAWN_FAILED,
            )
        dur = time.monotonic() - start
        out = (proc.stdout or "").strip()
        if proc.returncode != 0 and not out:
            stderr = (proc.stderr or "").strip()
            return AgentResult(
                self.name, self.spec.vendor, False, "",
                dur, f"exit {proc.returncode}: {stderr[:500]}",
                error_code=classify_stderr(proc.returncode, stderr),
            )
        if not out:
            # Exit 0 but nothing on stdout: the agent produced no usable review.
            return AgentResult(
                self.name, self.spec.vendor, False, "",
                dur, f"exit {proc.returncode}: empty output",
                error_code=ERR_EMPTY_OUTPUT,
            )
        return AgentResult(self.name, self.spec.vendor, True, out, dur)


class ClaudeAdapter(Adapter):
    def build_argv(self, prompt: str) -> list[str]:
        argv = [self.spec.command, "-p", prompt]
        if self.spec.model:
            argv += ["--model", self.spec.model]
        return argv + self.spec.extra_args


class CodexAdapter(Adapter):
    # Pipe the prompt on stdin (not positionally) so ``codex exec`` never blocks
    # waiting for input in non-interactive runs. Sandbox flags live in extra_args
    # (default ``-s danger-full-access`` so the sandbox doesn't block ``gh``).
    def build_argv(self, prompt: str) -> list[str]:
        del prompt
        argv = [self.spec.command, "exec"]
        if self.spec.model:
            argv += ["-m", self.spec.model]
        return argv + self.spec.extra_args

    def _stdin_for(self, prompt: str) -> str | None:
        return prompt


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
        del prompt
        n = self.name
        if phase == "review":
            body = (
                f"- **[major]** `src/example.py:42` — {n}: unchecked return value "
                f"may swallow an error.\n"
                f"- **[minor]** `src/example.py:7` — {n}: missing docstring.\n\n"
                "```json\n"
                "[\n"
                '  {"severity": "major", "file": "src/example.py", "line": 42, '
                f'"claim": "{n}: unchecked return value may swallow an error", '
                '"evidence": "the added code ignores the return value of int(x)", '
                '"suggested_fix": "check the result and raise on failure", '
                f'"confidence": "high", "reviewer": "{n}"}},\n'
                '  {"severity": "minor", "file": "src/example.py", "line": 7, '
                f'"claim": "{n}: missing docstring", '
                '"evidence": "the new function parse() has no docstring", '
                '"suggested_fix": "add a one-line docstring", '
                f'"confidence": "medium", "reviewer": "{n}"}}\n'
                "]\n"
                "```"
            )
        elif phase == "debate":
            body = (
                f"## AGREE\n- {n}: confirm the unchecked-return finding at "
                f"`src/example.py:42`.\n"
                f"## DISPUTE\n- {n}: the missing-docstring finding is a nit, not blocking.\n"
                f"## MISSED\n- {n}: no test covers the error branch."
            )
        elif phase == "verify":
            body = (
                "Verification: confirming the unchecked-return finding at "
                "`src/example.py:42`; the missing-docstring claim at `:7` is a nit "
                "not supported as blocking.\n\n"
                "```json\n"
                "[\n"
                '  {"file": "src/example.py", "line": 42, '
                '"claim": "unchecked return value may swallow an error", '
                '"status": "verified", '
                '"reasoning": "the added code ignores the return value of int(x)"},\n'
                '  {"file": "src/example.py", "line": 7, '
                '"claim": "missing docstring", '
                '"status": "unsupported", '
                '"reasoning": "a missing docstring is not a defect the diff introduces"}\n'
                "]\n"
                "```"
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
