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
import json
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
    if hasattr(os, "killpg"):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
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
# Hosted-API adapter (issue #430): the vendor's API key env var is unset. Distinct
# from ERR_AUTH_REQUIRED (a key was sent but the server rejected it) so a report
# can tell "never configured" apart from "misconfigured/expired/revoked".
ERR_MISSING_API_KEY = "missing_api_key"
# Hosted-API adapter (issue #430): the configured key contains a control
# character and was rejected BEFORE being sent as a header, rather than
# letting http.client raise (and risk echoing a transformed/escaped copy of
# the secret in its exception text — see _HostedApiAdapter._invalid_key_reason).
ERR_INVALID_API_KEY = "invalid_api_key"
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
        ERR_MISSING_API_KEY,
        ERR_INVALID_API_KEY,
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
    # Did this agent emit a structured findings block at all (issue #501)? A
    # reviewer that examined the diff and found nothing still emits `[]`; one that
    # produced no review emits prose and no block. Without this, both arrive as zero
    # findings and the panel reports the same size either way.
    structured: bool = False


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

    def _text_from_stdout(self, raw: str) -> str:
        """The reviewer's prose, given the CLI's raw stdout.

        Plain text for every adapter but one. `agy` speaks NDJSON events, so it
        overrides this rather than teaching `run` about event streams.
        """
        return raw

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
            caps["warnings"].append(
                f"version probe for '{self.spec.command}' failed: {redaction.redact(str(exc))[0]}"
            )
            return caps

        raw = ((proc.stdout or "") + (proc.stderr or "")).strip()
        caps["raw_version_output"] = redaction.redact(raw[:200])[0]
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
                f"spawn failed: {redaction.redact(str(exc))[0]}",
                error_code=ERR_SPAWN_FAILED,
            )
        dur = time.monotonic() - start
        out = self._text_from_stdout((proc.stdout or "").strip()).strip()
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
    # Prompt on STDIN, not argv (issue #287): the redacted diff must not appear in
    # the process list, where any local user can read it.
    #
    # `agy --print` used to read the prompt from stdin (verified against 1.0.6).
    # On 1.1.x `--print` takes a value, so that invocation dies before the model
    # is reached — `flag needs an argument: -print` with an empty `extra_args`,
    # and "took --dangerously-skip-permissions as its prompt" with a non-empty
    # one (#635). The agent passed every availability check and contributed
    # nothing to the panel.
    #
    # The obvious repair — put the prompt in argv — silently undoes #287. So the
    # prompt moves to agy's own stdin channel instead: `--input-format
    # stream-json` reads one NDJSON message per line and requires
    # `--output-format stream-json`. `--print` is not passed at all; the input
    # format implies print mode, and passing it would reintroduce the arity
    # problem. Verified end to end against agy 1.1.22.
    _STREAM_ARGS = ("--input-format", "stream-json", "--output-format", "stream-json")

    def build_argv(self, prompt: str) -> list[str]:
        del prompt
        argv = [self.spec.command, *self._STREAM_ARGS]
        if self.spec.model:
            argv += ["--model", self.spec.model]
        return argv + _read_only_extra_args(self.spec)

    def _stdin_for(self, prompt: str) -> str | None:
        """One NDJSON frame carrying the prompt. Shape verified against 1.1.22."""
        return json.dumps({"event": "user", "message": {"role": "user", "content": prompt}}) + "\n"

    def _text_from_stdout(self, raw: str) -> str:
        """The `result` event's response, out of the NDJSON stream.

        Falls back to the raw stream when no `result` frame is present rather
        than returning empty: a truncated stream should surface as an
        unparseable review the operator can read, not as a silent abstention —
        an empty review is counted as one, and #625 exists because an
        abstention read as an approval is the expensive failure.
        """
        response, saw_result = None, False
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("event") == "result":
                saw_result = True
                result = event.get("result")
                if isinstance(result, dict):
                    response = result.get("response")
        if saw_result and isinstance(response, str):
            return response
        return raw


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
                f"could not reach local server at {self.endpoint}: {redaction.redact(str(exc.reason))[0]}",
                error_code=ERR_CONNECTION,
            )
        except Exception as exc:  # noqa: BLE001 - surface any other failure
            return AgentResult(
                self.name,
                self.spec.vendor,
                False,
                "",
                time.monotonic() - start,
                f"local request failed: {redaction.redact(str(exc))[0]}",
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


# Hosted vendor API endpoints (issue #430). Fixed, not configurable: unlike
# `local`'s user-supplied `endpoint` (which needs the SSRF validation in
# config._endpoint_issues), a hosted vendor's URL is a known constant, not an
# attacker- or operator-influenceable value, so there is nothing to validate.
_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_API_VERSION = "2023-06-01"
_OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
# The Anthropic Messages API requires max_tokens on every request; there is no
# server-side default. Generous enough for a review response, small enough to
# bound cost/latency if a run is ever misconfigured to loop.
_HOSTED_API_MAX_TOKENS = 4096


def _hosted_api_status_code(status: int) -> str:
    """Map a hosted-API HTTP status to a typed error code (issue #430).

    Shared by every hosted-API adapter — identical mapping to
    ``LocalAdapter.classify_http_status`` (401/403 → auth, 429 → rate limit),
    kept as a free function since it has no per-adapter state.
    """
    if status in (401, 403):
        return ERR_AUTH_REQUIRED
    if status == 429:
        return ERR_RATE_LIMITED
    return ERR_NONZERO_EXIT


def _post_json(
    url: str, payload: dict, headers: dict[str, str], timeout: int
) -> tuple[dict | None, str | None, str | None]:
    """POST a JSON body and parse a JSON response (issue #430).

    Shared HTTP mechanics for the hosted-API adapters: build the request,
    route it through the SSRF-safe opener (``_open``, no file/ftp handlers, no
    redirect following — the same seam ``LocalAdapter`` uses), cap the response
    read at ``_MAX_RESPONSE_BYTES``, and classify any failure into a typed
    error code. Returns ``(response_dict, None, None)`` on success or
    ``(None, error_message, error_code)`` on failure — exactly one shape.
    Response bodies are redacted before being returned in an error message
    since they originate from the network and are surfaced in the report.
    """
    import json as _json
    import urllib.error
    import urllib.request

    body = _json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with _open(req, timeout) as resp:  # noqa: S310
            raw = resp.read(_MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
        return _json.loads(raw), None, None
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(_MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")[:300]
        except Exception:  # noqa: BLE001 - reading the error body is best-effort
            detail = exc.reason or ""
        detail = redaction.redact(detail)[0]
        return None, f"HTTP {exc.code}: {detail}", _hosted_api_status_code(exc.code)
    except TimeoutError:
        return None, f"timed out after {timeout}s", ERR_TIMEOUT
    except urllib.error.URLError as exc:
        return (
            None,
            f"could not reach {url}: {redaction.redact(str(exc.reason))[0]}",
            ERR_CONNECTION,
        )
    except Exception as exc:  # noqa: BLE001 - surface any other failure
        return None, f"request failed: {redaction.redact(str(exc))[0]}", ERR_UNKNOWN


class _HostedApiAdapter(Adapter):
    """Shared base for hosted-vendor-API reviewers keyed by an env-var API key.

    No CLI install, no interactive login, no subprocess: just an HTTP call
    over stdlib ``urllib`` to the vendor's real hosted API (issue #430), the
    same no-subprocess/no-new-dependency design as ``LocalAdapter`` but
    pointed at a hosted endpoint instead of a local server. The API key is
    read from the environment ONLY, never from ``jury.toml``, so it cannot
    leak into a checked-in config; the endpoint is a fixed per-vendor
    constant, not a config value, so there is no SSRF surface to guard the
    way `local`'s `endpoint` needs.
    """

    SUPPORTS_HEADLESS = True
    SUPPORTS_MODEL_SELECTION = True

    # Subclasses override.
    _API_KEY_ENV: str = ""

    def _api_key_env(self) -> str:
        return (getattr(self.spec, "api_key_env", None) or self._API_KEY_ENV) or "OPENAI_API_KEY"

    def _api_key(self) -> str:
        return os.environ.get(self._api_key_env(), "")

    def _api_url(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def _invalid_key_reason(self) -> str | None:
        """None if the key is safe to use as an HTTP header value; else why not.

        A key containing a control character (most plausibly a stray
        trailing ``\\n`` from a file/k8s-secret/`.env` mount) trips CPython's
        ``http.client`` header-injection guard. That guard reports the
        rejected value via ``repr()`` (e.g. an embedded newline becomes the
        two literal characters ``\\`` ``n``), which does **not** byte-for-byte
        match the raw key — so a literal substring scrub of the exception
        text (see :meth:`_scrub_secret`) cannot reliably catch it; the
        transformed text is no longer equal to the original secret. Validate
        and reject *before* the key ever reaches a header instead of trying
        to scrub it back out afterward.
        """
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in self._api_key()):
            return (
                f"{self._api_key_env()} contains a control character (e.g. a stray "
                f"trailing newline from how the secret was loaded) and cannot be "
                f"used as an HTTP header value"
            )
        return None

    def _scrub_secret(self, text: str) -> str:
        """Strip the literal API key value from an error message (issue #430).

        Defense-in-depth alongside ``redaction.redact()`` (which only
        recognizes known vendor-token *shapes* via regex) for any leak path
        NOT already ruled out by :meth:`_invalid_key_reason` — e.g. a
        well-formed key that still ends up quoted in some other library's
        error text. Not a substitute for that check: once a value contains
        control characters, downstream formatting (``repr()``, percent-
        encoding, ...) can transform it before it reaches an error message,
        and a literal match against the *original* key would then silently
        miss it — which is exactly why control characters are rejected
        upfront in :meth:`run` instead of relying on this alone.
        """
        key = self._api_key()
        if key and key in text:
            return text.replace(key, "[REDACTED]")
        return text

    def available(self) -> bool:
        """Available when a *usable* API key is set — a fast, network-free check.

        Unlike ``LocalAdapter.available()`` (which probes the server, since a
        local endpoint's reachability is genuinely uncertain), a hosted
        vendor's API is assumed reachable; the two real unknowns locally are
        whether the operator configured a key at all, and whether it's
        actually usable as a header value (see :meth:`_invalid_key_reason`) —
        a key that will be rejected by :meth:`run` should not report as
        available here either, or a capability check (``jury --doctor``)
        would give a falsely reassuring answer.
        """
        return bool(self._api_key()) and self._invalid_key_reason() is None

    def detect_capabilities(self) -> dict:
        key_set = bool(self._api_key())
        invalid_reason = self._invalid_key_reason() if key_set else None
        has_key = key_set and invalid_reason is None
        if not key_set:
            warnings = [f"{self._api_key_env()} is not set in the environment"]
        elif invalid_reason:
            warnings = [invalid_reason]
        else:
            warnings = []
        return {
            "version": None,
            "supports_headless": self.SUPPORTS_HEADLESS,
            "supports_model_selection": self.SUPPORTS_MODEL_SELECTION,
            "raw_version_output": f"hosted API {self._api_url()}",
            "status": CAP_OK if has_key else CAP_UNAVAILABLE,
            "warnings": warnings,
        }

    def build_payload(self, prompt: str) -> dict:  # pragma: no cover - overridden
        raise NotImplementedError

    def _headers(self) -> dict[str, str]:  # pragma: no cover - overridden
        raise NotImplementedError

    @staticmethod
    def parse_content(data: dict) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def run(self, prompt: str, phase: str = "review", timeout: int | None = None) -> AgentResult:
        del phase
        # Checked independently of available() (not just "not available()"):
        # available() now also returns False for a key that IS set but
        # invalid, and that case needs its own distinct error_code/message
        # below rather than the misleading "is not set" one.
        if not self._api_key():
            return AgentResult(
                self.name,
                self.spec.vendor,
                False,
                "",
                0.0,
                f"{self._api_key_env()} is not set in the environment",
                error_code=ERR_MISSING_API_KEY,
            )
        invalid_reason = self._invalid_key_reason()
        if invalid_reason is not None:
            # Reject before the key ever reaches a header — see
            # _invalid_key_reason for why post-hoc scrubbing can't be trusted
            # here. This message never echoes the key itself.
            return AgentResult(
                self.name,
                self.spec.vendor,
                False,
                "",
                0.0,
                invalid_reason,
                error_code=ERR_INVALID_API_KEY,
            )
        effective_timeout = self.spec.timeout
        if timeout is not None:
            effective_timeout = max(1, min(self.spec.timeout, int(timeout)))
        start = time.monotonic()
        data, err_msg, err_code = _post_json(
            self._api_url(), self.build_payload(prompt), self._headers(), effective_timeout
        )
        dur = time.monotonic() - start
        if err_msg is not None:
            return AgentResult(
                self.name,
                self.spec.vendor,
                False,
                "",
                dur,
                self._scrub_secret(err_msg),
                error_code=err_code,
            )
        content = self.parse_content(data or {})
        if not content:
            return AgentResult(
                self.name,
                self.spec.vendor,
                False,
                "",
                dur,
                "hosted API returned empty content",
                error_code=ERR_EMPTY_OUTPUT,
            )
        return AgentResult(self.name, self.spec.vendor, True, content, dur)


class AnthropicApiAdapter(_HostedApiAdapter):
    """Hosted Anthropic Messages API reviewer, keyed by ``ANTHROPIC_API_KEY`` (issue #430).

    Configure as a normal ``[[agent]]`` with ``vendor = "anthropic-api"`` and a
    ``model`` (e.g. a current Claude model id) — no ``command``, no ``claude``
    CLI install or interactive login needed.
    """

    _API_KEY_ENV = "ANTHROPIC_API_KEY"

    def _api_url(self) -> str:
        return _ANTHROPIC_API_URL

    def build_payload(self, prompt: str) -> dict:
        """Build the Anthropic Messages API request body (pure)."""
        return {
            "model": self.spec.model or "",
            "max_tokens": _HOSTED_API_MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self._api_key(),
            "anthropic-version": _ANTHROPIC_API_VERSION,
        }

    @staticmethod
    def parse_content(data: dict) -> str:
        """Extract the assistant text from a Messages API response."""
        if not isinstance(data, dict):
            return ""
        blocks = data.get("content") or []
        texts = [
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(texts).strip()


class OpenAiApiAdapter(_HostedApiAdapter):
    """Hosted OpenAI Chat Completions API reviewer, keyed by ``OPENAI_API_KEY`` (issue #430).

    Configure as a normal ``[[agent]]`` with ``vendor = "openai-api"`` and a
    ``model`` (e.g. a current GPT model id) — no ``command``, no ``codex`` CLI
    install or interactive login needed. Same request/response shape as
    ``LocalAdapter`` (both are OpenAI-compatible chat completions), just
    against the real hosted API with an ``Authorization`` header.
    """

    _API_KEY_ENV = "OPENAI_API_KEY"

    def _api_url(self) -> str:
        return _OPENAI_API_URL

    def build_payload(self, prompt: str) -> dict:
        """Build the OpenAI chat-completions request body (pure)."""
        return {
            "model": self.spec.model or "",
            "messages": [{"role": "user", "content": prompt}],
        }

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key()}",
        }

    @staticmethod
    def parse_content(data: dict) -> str:
        """Extract the assistant message text from a chat-completions response."""
        if not isinstance(data, dict):
            return ""
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return (message.get("content") or "").strip()


_GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GoogleApiAdapter(_HostedApiAdapter):
    """Hosted Google Gemini API reviewer, keyed by ``GEMINI_API_KEY`` (issue #432).

    Configure as a normal ``[[agent]]`` with ``vendor = "google-api"`` and a
    ``model`` (e.g. a current Gemini model id) — no ``command``, no ``agy``
    CLI install or interactive login needed.

    Two differences from the other two hosted adapters:

    - The Gemini API embeds the model id in the URL **path**
      (``.../models/{model}:generateContent``), not the request body, so
      ``_api_url()`` is built from ``self.spec.model`` on every call rather
      than returning a fixed constant like the other two adapters.
    - The key is sent via the ``x-goog-api-key`` header. Gemini also accepts
      the key as a ``?key=...`` query parameter, but a query-string key is a
      much easier accidental-leak vector (proxy/access logs, anything that
      prints the request URL) than a header — deliberately not supported.

    A prompt blocked by Gemini's safety filters comes back with an empty
    ``candidates`` list (and a ``promptFeedback.blockReason``); this is not
    distinguished from a genuinely empty response and both currently surface
    as the same generic ``ERR_EMPTY_OUTPUT`` — a possible future refinement,
    not required for parity with the other two adapters.
    """

    _API_KEY_ENV = "GEMINI_API_KEY"

    def _api_url(self) -> str:
        # Escape the model id as a single path segment (issue #432 review): an
        # operator-configured model containing reserved URL characters
        # (`/`, `?`, `#`, ...) would otherwise change the request's path/query
        # semantics instead of staying a single `{model}` segment.
        import urllib.parse

        model = urllib.parse.quote(self.spec.model or "", safe="")
        return f"{_GEMINI_API_BASE}/{model}:generateContent"

    def build_payload(self, prompt: str) -> dict:
        """Build the Gemini ``generateContent`` request body (pure)."""
        return {"contents": [{"parts": [{"text": prompt}]}]}

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-goog-api-key": self._api_key(),
        }

    @staticmethod
    def parse_content(data: dict) -> str:
        """Extract the assistant text from a ``generateContent`` response."""
        if not isinstance(data, dict):
            return ""
        candidates = data.get("candidates") or []
        if not candidates or not isinstance(candidates[0], dict):
            return ""
        content = candidates[0].get("content")
        if not isinstance(content, dict):
            return ""
        parts = content.get("parts") or []
        texts = [
            part.get("text", "")
            for part in parts
            if isinstance(part, dict) and isinstance(part.get("text", ""), str)
        ]
        return "".join(texts).strip()


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


class GenericOpenAICompatibleAdapter(_HostedApiAdapter):
    """Hosted OpenAI-compatible API reviewer (OpenRouter, DeepSeek, Groq, Mistral API, LiteLLM, etc.).

    Supports custom ``endpoint``, custom ``api_key_env``, and extra HTTP ``headers``.
    """

    _API_KEY_ENV = "OPENAI_API_KEY"

    def _api_url(self) -> str:
        endpoint = (self.spec.endpoint or _OPENAI_API_URL).rstrip("/")
        if endpoint.endswith("/chat/completions"):
            return endpoint
        return f"{endpoint}/chat/completions"

    def build_payload(self, prompt: str) -> dict:
        return {
            "model": self.spec.model or "",
            "messages": [{"role": "user", "content": prompt}],
        }

    def _headers(self) -> dict[str, str]:
        hdrs = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key()}",
        }
        if self.spec.headers:
            hdrs.update(self.spec.headers)
        return hdrs

    @staticmethod
    def parse_content(data: dict) -> str:
        if not isinstance(data, dict):
            return ""
        choices = data.get("choices") or []
        if not choices or not isinstance(choices, list):
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        msg = first.get("message") or {}
        if not isinstance(msg, dict):
            return ""
        raw_content = msg.get("content")
        if isinstance(raw_content, str):
            return raw_content.strip()
        if isinstance(raw_content, list):
            parts = []
            for item in raw_content:
                if isinstance(item, dict):
                    if (item.get("type") == "text" or "text" in item) and isinstance(
                        item.get("text"), str
                    ):
                        parts.append(item["text"])
                elif isinstance(item, str):
                    parts.append(item)
            return "".join(parts).strip()
        return ""


class GenericCLIAdapter(Adapter):
    """Generic CLI adapter for arbitrary coding-agent CLIs (Aider, Goose, OpenHands, Copilot CLI, etc.).

    Supports configurable prompt delivery modes:
    - ``prompt_mode = "stdin"`` (default): prompt passed via STDIN
    - ``prompt_mode = "arg"``: prompt passed as positional argument on argv
    """

    def available(self) -> bool:
        command = self.spec.command or ""
        if not command:
            return False
        return shutil.which(command) is not None

    def detect_capabilities(self) -> dict:
        if not self.available():
            return {
                "version": None,
                "supports_headless": None,
                "supports_model_selection": None,
                "raw_version_output": "",
                "status": CAP_UNAVAILABLE,
                "warnings": [f"command '{self.spec.command}' not found on PATH"],
            }
        return {
            "version": "generic-cli",
            "supports_headless": True,
            "supports_model_selection": bool(self.spec.model),
            "raw_version_output": "generic-cli",
            "status": CAP_OK,
            "warnings": [],
        }

    def run(self, prompt: str, phase: str = "review", timeout: int | None = None) -> AgentResult:
        del phase
        if not self.available():
            return AgentResult(
                agent=self.spec.name,
                vendor=self.spec.vendor,
                ok=False,
                output="",
                duration_s=0.0,
                error=f"command '{self.spec.command}' not found on PATH.",
                error_code=ERR_MISSING_CLI,
            )
        effective_timeout = timeout if timeout is not None else self.spec.timeout
        extra_args = _read_only_extra_args(self.spec)
        argv = [self.spec.command, *extra_args]

        mode = (self.spec.prompt_mode or "stdin").lower()
        stdin_content = None

        if mode == "arg":
            argv.append(prompt)
        else:
            stdin_content = prompt

        start = time.monotonic()
        try:
            res = _spawn(argv, stdin_content, timeout=effective_timeout)
        except subprocess.TimeoutExpired:
            return AgentResult(
                self.spec.name,
                self.spec.vendor,
                False,
                "",
                effective_timeout,
                f"execution timed out after {effective_timeout}s.",
                error_code=ERR_TIMEOUT,
            )
        except Exception as exc:
            duration = time.monotonic() - start
            return AgentResult(
                self.spec.name,
                self.spec.vendor,
                False,
                "",
                duration,
                f"failed to spawn '{self.spec.command}': {redaction.redact(str(exc))[0]}",
                error_code=ERR_SPAWN_FAILED,
            )

        duration = time.monotonic() - start
        out = (res.stdout or "").strip()
        err = (res.stderr or "").strip()

        if res.returncode != 0:
            detail = err or out or f"exited with code {res.returncode}"
            safe_detail = redaction.redact(detail)[0]
            err_code = classify_stderr(res.returncode, err or out)
            return AgentResult(
                self.spec.name,
                self.spec.vendor,
                False,
                "",
                duration,
                f"exit {res.returncode}: {safe_detail[:500]}",
                error_code=err_code,
            )

        if not out:
            return AgentResult(
                self.spec.name,
                self.spec.vendor,
                False,
                "",
                duration,
                "agent produced empty output",
                error_code=ERR_EMPTY_OUTPUT,
            )

        return AgentResult(self.spec.name, self.spec.vendor, True, out, duration)


_VENDOR_ADAPTERS: dict[str, type[Adapter]] = {
    "anthropic": ClaudeAdapter,
    "openai": CodexAdapter,
    "google": AgyAdapter,
    "local": LocalAdapter,
    "anthropic-api": AnthropicApiAdapter,
    "openai-api": OpenAiApiAdapter,
    "google-api": GoogleApiAdapter,
    "openai-compatible": GenericOpenAICompatibleAdapter,
    "cli": GenericCLIAdapter,
}


def register_adapter(vendor: str, adapter_cls: type[Adapter]) -> None:
    """Register a custom adapter class for a vendor string."""
    _VENDOR_ADAPTERS[vendor.lower()] = adapter_cls


def make_adapter(spec: AgentSpec, mock: bool = False) -> Adapter:
    if mock:
        return MockAdapter(spec)
    cls = _VENDOR_ADAPTERS.get((spec.vendor or "").lower())
    if cls is not None:
        return cls(spec)
    if spec.endpoint or (spec.api_key_env and not spec.command):
        return GenericOpenAICompatibleAdapter(spec)
    if spec.command:
        return GenericCLIAdapter(spec)
    return AgyAdapter(spec)
