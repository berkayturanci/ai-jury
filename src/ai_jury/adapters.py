"""Agent adapters — each wraps one native coding-agent CLI in headless mode.

Every adapter turns a prompt into a subprocess invocation and captures stdout as
the agent's response. Adapters are intentionally thin: the orchestrator owns the
prompt content and the round structure; an adapter only knows how to *invoke its
CLI*.

Headless invocations (verified against installed CLIs, early 2026). The prompt
embeds the redacted diff, so it is delivered on STDIN (never argv) for every
real adapter so it is not exposed in the process list (issue #287):
  - Claude Code : ``claude -p --output-format text``  (prompt piped via stdin)
  - Codex CLI   : ``codex exec <args>``               (prompt piped via stdin)
  - Antigravity : ``agy --print``                     (prompt piped via stdin)
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field

from . import privilege, redaction
from .config import AgentSpec

# Cap on a single local-model HTTP response body (issue #293/F-9). A chat
# completion is small; an unbounded read from a malicious/buggy endpoint would
# let it OOM the process.
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024


def _kill_process_group(proc: subprocess.Popen) -> None:
    """Best-effort kill of the child's whole process group (issue #293/F-7)."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
    except (ProcessLookupError, PermissionError, OSError):
        pass
    with contextlib.suppress(OSError):
        proc.kill()


def _spawn(argv: list[str], stdin: str | None, timeout: int) -> subprocess.CompletedProcess:
    """Run a CLI with stdout/stderr captured, killing the whole group on timeout.

    ``subprocess.run(timeout=…)`` SIGKILLs only the direct child, so an agent CLI
    that wraps node/python can leak orphaned grandchildren (issue #293/F-7). The
    child is started in its own session (process-group leader); on timeout the
    entire group is killed before re-raising ``TimeoutExpired`` so the caller's
    handling is unchanged. Returns a ``CompletedProcess``.
    """
    popen_kwargs: dict = {
        "stdin": subprocess.PIPE if stdin is not None else None,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if hasattr(os, "setsid"):
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(argv, **popen_kwargs)
    try:
        out, err = proc.communicate(input=stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        with contextlib.suppress(Exception):
            proc.communicate()  # reap the killed child
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def _read_only_extra_args(spec: AgentSpec) -> list[str]:
    """The agent's ``extra_args`` with its mandatory read-only sandbox guaranteed.

    Enforced at the adapter layer (issue #288) so a missing/misconfigured
    ``extra_args`` cannot strip the sandbox: a reviewer of an attacker-controlled
    diff is never write/tool-capable. Config may widen a codex sandbox knowingly,
    but never remove the restriction.
    """
    return privilege.enforce_read_only(spec.vendor, spec.name, spec.extra_args)


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
# Local/HTTP adapter could not reach its server (issue #43): connection refused,
# DNS failure, or the local model server is not running.
ERR_CONNECTION = "connection_error"
ERR_UNKNOWN = "unknown"

ERROR_CODES = frozenset(
    {
        ERR_MISSING_CLI,
        ERR_AUTH_REQUIRED,
        ERR_PERMISSION_PROMPT,
        ERR_TIMEOUT,
        ERR_NONZERO_EXIT,
        ERR_EMPTY_OUTPUT,
        ERR_SPAWN_FAILED,
        ERR_RATE_LIMITED,
        ERR_CONNECTION,
        ERR_UNKNOWN,
    }
)

# Failures that are worth retrying because they are typically transient (issue
# #30): a timeout, a rate-limit, a process that failed to spawn, or a local
# server that was briefly unreachable (#43). Auth, missing-CLI,
# permission-prompt, empty-output, and generic nonzero-exit are treated as
# deterministic — retrying them just burns time and tokens.
RETRYABLE_ERROR_CODES = frozenset(
    {
        ERR_TIMEOUT,
        ERR_RATE_LIMITED,
        ERR_SPAWN_FAILED,
        ERR_CONNECTION,
    }
)


# Ordered keyword groups for classify_stderr. Each keyword is matched on word
# boundaries (\b...\b) so incidental substrings do NOT trigger a false
# classification: bare "auth" matches "auth error" but not "author identity",
# and "login" matches "login required" but not "login_attempts" ("_" is a word
# char, so there is no boundary inside "login_attempts"). Multi-word phrases
# tolerate a space OR "_" between tokens (e.g. "rate limit"/"rate_limit").
def _keyword_pattern(*keywords: str) -> re.Pattern[str]:
    parts = [r"[ _]+".join(re.escape(tok) for tok in kw.split()) for kw in keywords]
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b")


# Order matters: auth and rate-limit signals are checked before the generic
# permission and nonzero-exit fallbacks.
_AUTH_RE = _keyword_pattern(
    "not authenticated",
    "unauthenticated",
    "authentication",
    "unauthorized",
    "api key",
    "auth",
    "log in",
    "login",
    "credential",
    "credentials",
)
_RATE_LIMIT_RE = _keyword_pattern("rate limit", "429", "quota", "too many requests")
_PERMISSION_RE = _keyword_pattern(
    "permission",
    "permissions",
    "approve",
    "approval",
    "confirm",
    "confirmation",
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
            # Via _spawn so the probe also runs in its own process group and the
            # whole group is killed on timeout (issue #303/L-1) — matching the
            # main run path; a bare subprocess.run would orphan grandchildren.
            proc = _spawn(self._version_argv(), None, _VERSION_PROBE_TIMEOUT)
        except subprocess.TimeoutExpired:
            caps["status"] = CAP_UNKNOWN_VERSION
            caps["warnings"].append(
                f"version probe for '{self.spec.command}' timed out after {_VERSION_PROBE_TIMEOUT}s"
            )
            return caps
        except Exception as exc:  # noqa: BLE001 - swallow any spawn failure
            caps["status"] = CAP_UNKNOWN_VERSION
            caps["warnings"].append(f"version probe for '{self.spec.command}' failed: {exc}")
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
                self.name,
                self.spec.vendor,
                False,
                "",
                0.0,
                f"command not found on PATH: {self.spec.command}",
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
            proc = _spawn(argv, stdin, effective_timeout)
        except subprocess.TimeoutExpired:
            return AgentResult(
                self.name,
                self.spec.vendor,
                False,
                "",
                time.monotonic() - start,
                f"timed out after {effective_timeout}s",
                error_code=ERR_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 - surface any spawn failure
            return AgentResult(
                self.name,
                self.spec.vendor,
                False,
                "",
                time.monotonic() - start,
                f"spawn failed: {exc}",
                error_code=ERR_SPAWN_FAILED,
            )
        dur = time.monotonic() - start
        out = (proc.stdout or "").strip()
        # A nonzero exit is ALWAYS a failure, even with stdout (issue #101): a
        # crashing CLI can still print partial or error output, and counting that
        # as a clean review would silently feed it into consensus, synthesis, and
        # the CI gate. We classify from stderr (falling back to any stdout) and
        # keep a short snippet in the error for debugging — but ok=False, so the
        # orchestrator excludes it.
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            detail = stderr or out
            # Redact before embedding in the error: a crashing CLI can dump an
            # env var / token into its stderr, and this string is rendered into
            # the report and posted to the PR. Mirrors the LocalAdapter path
            # (#293/F-8); the asymmetry was a secret-leak vector (audit
            # 2026-06-13/N-1). Classify on the raw text (no secrets in codes).
            safe_detail = redaction.redact(detail)[0]
            return AgentResult(
                self.name,
                self.spec.vendor,
                False,
                "",
                dur,
                f"exit {proc.returncode}: {safe_detail[:500]}",
                error_code=classify_stderr(proc.returncode, stderr or out),
            )
        if not out:
            # Exit 0 but nothing on stdout: the agent produced no usable review.
            return AgentResult(
                self.name,
                self.spec.vendor,
                False,
                "",
                dur,
                f"exit {proc.returncode}: empty output",
                error_code=ERR_EMPTY_OUTPUT,
            )
        return AgentResult(self.name, self.spec.vendor, True, out, dur)


class ClaudeAdapter(Adapter):
    # The prompt embeds the (redacted) diff and PR/issue context; deliver it on
    # STDIN rather than as a process argument so it is not exposed in `ps` /
    # /proc/<pid>/cmdline to other local users (issue #287). `claude -p` reads
    # the prompt from stdin when no positional prompt is given.
    def build_argv(self, prompt: str) -> list[str]:
        del prompt
        argv = [self.spec.command, "-p"]
        if self.spec.model:
            argv += ["--model", self.spec.model]
        return argv + _read_only_extra_args(self.spec)

    def _stdin_for(self, prompt: str) -> str | None:
        return prompt


class CodexAdapter(Adapter):
    # Pipe the prompt on stdin (not positionally) so ``codex exec`` never blocks
    # waiting for input in non-interactive runs. Sandbox flags live in extra_args;
    # the shipped default is ``-s read-only`` (secure by default, #100) — the
    # reviewer only reads its prompt, since the jury fetches the diff via ``gh``.
    def build_argv(self, prompt: str) -> list[str]:
        del prompt
        argv = [self.spec.command, "exec"]
        if self.spec.model:
            argv += ["-m", self.spec.model]
        return argv + _read_only_extra_args(self.spec)

    def _stdin_for(self, prompt: str) -> str | None:
        return prompt


class AgyAdapter(Adapter):
    # Prompt on STDIN, not argv (issue #287): `agy --print` reads the prompt from
    # stdin when no positional prompt is given (verified against agy 1.0.6), so the
    # redacted diff is not exposed in the process list.
    def build_argv(self, prompt: str) -> list[str]:
        del prompt
        argv = [self.spec.command, "--print"]
        if self.spec.model:
            argv += ["--model", self.spec.model]
        return argv + _read_only_extra_args(self.spec)

    def _stdin_for(self, prompt: str) -> str | None:
        return prompt


_DEFAULT_LOCAL_ENDPOINT = "http://localhost:11434/v1"


def _http_only_opener():
    """An opener that handles ONLY http/https (issue #291, SSRF defense).

    The default ``urllib`` opener honors ``file://`` and ``ftp://``, so an
    attacker-influenced ``endpoint`` could read local files or reach other
    schemes. This OpenerDirector registers no ``FileHandler``/``FTPHandler``, so
    any non-http(s) URL raises ``URLError("unknown url type")`` regardless of
    config validation — defense in depth alongside ``config._endpoint_issues``.

    It also registers NO ``HTTPRedirectHandler`` (review of #291): otherwise a
    malicious/compromised endpoint could 302-redirect to an internal/metadata
    host (e.g. ``169.254.169.254``) and the opener would follow it, bypassing the
    configured-URL validation. Without the handler a 3xx surfaces as an
    ``HTTPError`` (a failed review) and is never followed.
    """
    import urllib.request

    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.HTTPHandler,
        urllib.request.HTTPSHandler,
        urllib.request.HTTPDefaultErrorHandler,
        urllib.request.HTTPErrorProcessor,
        # UnknownHandler raises URLError("unknown url type: …") for any scheme
        # without a registered handler — so file://, ftp://, etc. fail loudly
        # instead of silently resolving to None.
        urllib.request.UnknownHandler,
    ):
        opener.add_handler(handler())
    return opener


def _open(target, timeout):
    """Open an http/https URL or Request via the restricted opener (issue #291).

    Single seam for every local-adapter HTTP call so the SSRF-safe opener (no
    file/ftp handlers) is always used.
    """
    return _http_only_opener().open(target, timeout=timeout)


def list_local_models(endpoint: str = _DEFAULT_LOCAL_ENDPOINT) -> list[str]:
    """List model ids from a local OpenAI-compatible server (issue #109).

    GETs ``{endpoint}/models`` (the OpenAI-compatible listing that Ollama,
    vLLM, LM Studio, etc. expose) and returns the model ids in their reported
    order. Best-effort and stdlib-only: any failure (server down, bad JSON)
    returns ``[]`` so callers can fall back gracefully.

    The endpoint is validated here at the seam (issue #309) so EVERY caller —
    including the un-gated ``jury init --local-endpoint`` discovery path — gets
    the same SSRF gate that ``config._endpoint_issues`` enforces for config-file
    endpoints: a non-``http(s)`` scheme or a non-loopback host (without the
    ``JURY_ALLOW_REMOTE_ENDPOINT`` opt-in) yields ``[]`` without any network call.
    """
    import json as _json

    from .config import _endpoint_issues

    base = (endpoint or _DEFAULT_LOCAL_ENDPOINT).rstrip("/")
    try:
        # SSRF gate INSIDE the try (review of #309): `_endpoint_issues` calls
        # urlsplit, which raises ValueError on a malformed URL (e.g. `http://[::1`);
        # keep the best-effort "any failure -> []" contract rather than crashing.
        if _endpoint_issues(base, "local-endpoint")[0]:  # hard-error issues -> refuse
            return []
        url = base if base.endswith("/models") else f"{base}/models"
        with _open(url, _VERSION_PROBE_TIMEOUT) as resp:  # noqa: S310
            data = _json.loads(resp.read(_MAX_RESPONSE_BYTES).decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - discovery is best-effort
        return []
    models = data.get("data") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return []
    ids = [m.get("id") for m in models if isinstance(m, dict) and m.get("id")]
    return [str(i) for i in ids]


class LocalAdapter(Adapter):
    """Open-weight / local-model reviewer over an OpenAI-compatible API (issue #43).

    Targets the ``/v1/chat/completions`` endpoint exposed by common local servers
    (Ollama, llama.cpp ``llama-server``, vLLM, LM Studio). It talks plain HTTP via
    the stdlib (``urllib``) — no new dependencies and no subprocess — so one panel
    seat can run free and fully offline, adding model diversity (the load-bearing
    advantage) at zero marginal cost.

    Configure as a normal ``[[agent]]`` with ``vendor = "local"``, an
    ``endpoint`` (base URL, default ``http://localhost:11434/v1``), and a
    ``model``. ``extra_args`` is unused. An unreachable server fails with the
    typed ``connection_error`` code (issue #29) rather than a crash.
    """

    SUPPORTS_HEADLESS = True
    SUPPORTS_MODEL_SELECTION = True

    @property
    def endpoint(self) -> str:
        return (self.spec.endpoint or _DEFAULT_LOCAL_ENDPOINT).rstrip("/")

    def completions_url(self) -> str:
        """Resolve the chat-completions URL from the configured base endpoint.

        Accepts either a base URL (``…/v1``) or a full completions URL; pure so it
        can be unit-tested without network.
        """
        base = self.endpoint
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def build_payload(self, prompt: str) -> dict:
        """Build the OpenAI-compatible chat-completions request body (pure)."""
        return {
            "model": self.spec.model or "",
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "temperature": 0,
        }

    @staticmethod
    def parse_content(data: dict) -> str:
        """Extract the assistant message text from a chat-completions response."""
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return (message.get("content") or "").strip()

    @staticmethod
    def classify_http_status(status: int) -> str:
        """Map an HTTP error status to a typed error code (issue #29)."""
        if status in (401, 403):
            return ERR_AUTH_REQUIRED
        if status == 429:
            return ERR_RATE_LIMITED
        return ERR_NONZERO_EXIT

    def available(self) -> bool:
        """A local agent is 'available' when its server answers a quick probe.

        Probes the OpenAI-compatible ``/v1/models`` (or the endpoint root) with a
        short timeout. Network-only; never raises.
        """
        import urllib.error
        import urllib.request

        url = f"{self.endpoint}/models"
        try:
            with _open(url, _VERSION_PROBE_TIMEOUT) as resp:  # noqa: S310
                return 200 <= resp.status < 500
        except urllib.error.HTTPError as exc:
            # A 4xx (e.g. 404 on /models) still means the server is up.
            return exc.code < 500
        except Exception:  # noqa: BLE001 - unreachable server -> not available
            return False

    def detect_capabilities(self) -> dict:
        reachable = self.available()
        return {
            "version": None,
            "supports_headless": self.SUPPORTS_HEADLESS,
            "supports_model_selection": self.SUPPORTS_MODEL_SELECTION,
            "raw_version_output": f"local endpoint {self.endpoint}",
            "status": CAP_OK if reachable else CAP_UNAVAILABLE,
            "warnings": ([] if reachable else [f"local server unreachable at {self.endpoint}"]),
        }

    def run(self, prompt: str, phase: str = "review", timeout: int | None = None) -> AgentResult:
        import json as _json
        import urllib.error
        import urllib.request

        del phase
        effective_timeout = self.spec.timeout
        if timeout is not None:
            effective_timeout = max(1, min(self.spec.timeout, int(timeout)))
        body = _json.dumps(self.build_payload(prompt)).encode("utf-8")
        req = urllib.request.Request(
            self.completions_url(),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        start = time.monotonic()
        try:
            with _open(req, effective_timeout) as resp:  # noqa: S310
                raw = resp.read(_MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
            data = _json.loads(raw)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read(_MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")[:300]
            except Exception:  # noqa: BLE001
                detail = exc.reason or ""
            # The body is from a possibly-untrusted endpoint and is surfaced in
            # the report; redact recognized secrets before embedding (#293/F-8).
            detail = redaction.redact(detail)[0]
            return AgentResult(
                self.name,
                self.spec.vendor,
                False,
                "",
                time.monotonic() - start,
                f"HTTP {exc.code}: {detail}",
                error_code=self.classify_http_status(exc.code),
            )
        except TimeoutError:
            return AgentResult(
                self.name,
                self.spec.vendor,
                False,
                "",
                time.monotonic() - start,
                f"timed out after {effective_timeout}s",
                error_code=ERR_TIMEOUT,
            )
        except urllib.error.URLError as exc:
            return AgentResult(
                self.name,
                self.spec.vendor,
                False,
                "",
                time.monotonic() - start,
                f"could not reach local server at {self.endpoint}: {exc.reason}",
                error_code=ERR_CONNECTION,
            )
        except Exception as exc:  # noqa: BLE001 - surface any other failure
            return AgentResult(
                self.name,
                self.spec.vendor,
                False,
                "",
                time.monotonic() - start,
                f"local request failed: {exc}",
                error_code=ERR_UNKNOWN,
            )
        dur = time.monotonic() - start
        content = self.parse_content(data)
        if not content:
            return AgentResult(
                self.name,
                self.spec.vendor,
                False,
                "",
                dur,
                "local model returned empty content",
                error_code=ERR_EMPTY_OUTPUT,
            )
        return AgentResult(self.name, self.spec.vendor, True, content, dur)


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
    "local": LocalAdapter,
}


def make_adapter(spec: AgentSpec, mock: bool = False) -> Adapter:
    if mock:
        return MockAdapter(spec)
    cls = _VENDOR_ADAPTERS.get(spec.vendor)
    if cls is None:
        # Unknown vendor: treat command as a print-style CLI (prompt as last arg).
        return AgyAdapter(spec)
    return cls(spec)
