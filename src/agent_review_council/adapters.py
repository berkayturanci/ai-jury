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

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from .config import AgentSpec

# Short timeout for capability/version probes. Detection is best-effort and must
# never slow down or block a normal run, so probes are deliberately snappy.
_VERSION_PROBE_TIMEOUT = 10

# Matches a version-looking token, e.g. "1.2", "1.2.3", "v0.45.1".
_VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+)?")

# Capability/version probe statuses.
CAP_OK = "ok"
CAP_UNKNOWN_VERSION = "unknown_version"
CAP_UNAVAILABLE = "unavailable"

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

# Failures that are worth retrying because they are typically transient (issue
# #30): a timeout, a rate-limit, or a process that failed to spawn. Auth,
# missing-CLI, permission-prompt, empty-output, and generic nonzero-exit are
# treated as deterministic — retrying them just burns time and tokens.
RETRYABLE_ERROR_CODES = frozenset({
    ERR_TIMEOUT,
    ERR_RATE_LIMITED,
    ERR_SPAWN_FAILED,
})


# Ordered keyword groups for classify_stderr. Each keyword is matched on word
# boundaries (\b...\b) so incidental substrings do NOT trigger a false
# classification: bare "auth" matches "auth error" but not "author identity",
# and "login" matches "login required" but not "login_attempts" ("_" is a word
# char, so there is no boundary inside "login_attempts"). Multi-word phrases
# tolerate a space OR "_" between tokens (e.g. "rate limit"/"rate_limit").
def _keyword_pattern(*keywords: str) -> "re.Pattern[str]":
    parts = [
        r"[ _]+".join(re.escape(tok) for tok in kw.split())
        for kw in keywords
    ]
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b")


# Order matters: auth and rate-limit signals are checked before the generic
# permission and nonzero-exit fallbacks.
_AUTH_RE = _keyword_pattern(
    "not authenticated", "unauthenticated", "authentication", "unauthorized",
    "api key", "auth", "log in", "login", "credential", "credentials",
)
_RATE_LIMIT_RE = _keyword_pattern("rate limit", "429", "quota", "too many requests")
_PERMISSION_RE = _keyword_pattern(
    "permission", "permissions", "approve", "approval", "confirm", "confirmation",
)


def classify_stderr(returncode: int, stderr: str) -> str:
    """Classify a nonzero-exit failure into a typed error code from its stderr.

    Token-aware matching against the lowercased stderr: each keyword group is a
    word-boundary regex, so incidental substrings (e.g. "author" containing
    "auth") never cause a misclassification. Ordering matters (auth and
    rate-limit signals are checked before the generic permission and
    nonzero-exit fallbacks). Returns one of the ``ERR_*`` codes.
    """
    text = (stderr or "").lower()
    if _AUTH_RE.search(text):
        return ERR_AUTH_REQUIRED
    if _RATE_LIMIT_RE.search(text):
        return ERR_RATE_LIMITED
    if _PERMISSION_RE.search(text):
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
    # Number of attempts made for this result (issue #30): 1 means no retry.
    # >1 records that a transient failure was retried before this outcome.
    attempts: int = 1


class Adapter:
    """Base adapter. Subclasses build the argv for their CLI."""

    # Declarative capability metadata. Real coding-agent CLIs support a headless
    # (non-interactive) invocation and model selection; subclasses override where
    # this differs. ``MockAdapter`` reports synthetic capabilities.
    SUPPORTS_HEADLESS = True
    SUPPORTS_MODEL_SELECTION = True

    # Args passed to the CLI to print its version. Subclasses override if the CLI
    # uses a different verb/flag (e.g. ``codex --version``).
    _VERSION_ARGS = ("--version",)

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

    def _version_argv(self) -> list[str]:
        """Argv used to probe the CLI's version."""
        return [self.spec.command, *self._VERSION_ARGS]

    def detect_capabilities(self) -> dict:
        """Best-effort probe of this agent's version and capabilities.

        Returns a dict shaped like::

            {
                "version": "<str|None>",
                "supports_headless": bool,
                "supports_model_selection": bool,
                "raw_version_output": "<short str>",
                "status": "ok|unknown_version|unavailable",
                "warnings": [...],
            }

        This is intentionally fast and forgiving: it runs ``<command> --version``
        with a SHORT timeout and swallows ALL errors (missing CLI, timeout,
        nonzero exit, garbage output). It NEVER raises, so it is safe to call
        from diagnostics without blocking or crashing a run.
        """
        caps = {
            "version": None,
            "supports_headless": self.SUPPORTS_HEADLESS,
            "supports_model_selection": self.SUPPORTS_MODEL_SELECTION,
            "raw_version_output": "",
            "status": CAP_UNAVAILABLE,
            "warnings": [],
        }

        # Not on PATH: report unavailable without spawning a subprocess.
        if not self.available():
            return caps

        try:
            proc = subprocess.run(
                self._version_argv(),
                capture_output=True,
                text=True,
                timeout=_VERSION_PROBE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            caps["status"] = CAP_UNKNOWN_VERSION
            caps["warnings"].append(
                f"version probe for '{self.spec.command}' timed out after "
                f"{_VERSION_PROBE_TIMEOUT}s"
            )
            return caps
        except Exception as exc:  # noqa: BLE001 - swallow any spawn failure
            caps["status"] = CAP_UNKNOWN_VERSION
            caps["warnings"].append(
                f"version probe for '{self.spec.command}' failed: {exc}"
            )
            return caps

        raw = ((proc.stdout or "") + (proc.stderr or "")).strip()
        caps["raw_version_output"] = raw[:200]
        match = _VERSION_RE.search(raw)
        if proc.returncode == 0 and match:
            caps["version"] = match.group(0)
            caps["status"] = CAP_OK
        else:
            caps["status"] = CAP_UNKNOWN_VERSION
            caps["warnings"].append(
                f"could not determine version of '{self.spec.command}' "
                f"(exit {proc.returncode}); capabilities assumed from vendor defaults"
            )
        return caps

    def run(self, prompt: str, phase: str = "review", timeout: int | None = None) -> AgentResult:
        del phase
        if not self.available():
            return AgentResult(
                self.name, self.spec.vendor, False, "",
                0.0, f"command not found on PATH: {self.spec.command}",
                error_code=ERR_MISSING_CLI,
            )
        # The effective timeout is the caller's override (the run budget, issue
        # #30) when smaller than the agent's own bound, else the agent timeout.
        effective_timeout = self.spec.timeout
        if timeout is not None:
            effective_timeout = max(1, min(self.spec.timeout, int(timeout)))
        argv = self.build_argv(prompt)
        stdin = self._stdin_for(prompt)
        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired:
            return AgentResult(
                self.name, self.spec.vendor, False, "",
                time.monotonic() - start, f"timed out after {effective_timeout}s",
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

    # Synthetic capabilities: the mock is offline and runs no real CLI.
    SUPPORTS_HEADLESS = True
    SUPPORTS_MODEL_SELECTION = False

    def available(self) -> bool:
        return True

    def detect_capabilities(self) -> dict:
        """Deterministic fake capabilities so doctor/tests stay stable offline."""
        return {
            "version": "mock-1.0",
            "supports_headless": self.SUPPORTS_HEADLESS,
            "supports_model_selection": self.SUPPORTS_MODEL_SELECTION,
            "raw_version_output": "mock-1.0",
            "status": CAP_OK,
            "warnings": [],
        }

    def run(self, prompt: str, phase: str = "review", timeout: int | None = None) -> AgentResult:
        del prompt, timeout
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
