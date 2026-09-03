"""Single-agent role dispatch for orchestrators: ``jury run-agent`` (issue #661).

ai-jury already owns the transport-agnostic provider runtime an orchestrator
needs — an adapter per vendor, per-vendor availability, the read-only flags,
timeouts, the typed error taxonomy — but until now it was reachable only through
a full panel run. So an orchestrator that wanted to dispatch *one* implementer,
gate reviewer or chair had to hand-write the argv itself, and hand-written argv
is exactly where a read-only guarantee gets lost.

This module is the pure core of that command: role policy, agent resolution,
attribution, and the on-disk shape of a detached run. Everything that touches a
subprocess, the clock or the filesystem lives in ``cli._run_run_agent`` or is
passed in as a seam, so all of the below is unit-testable offline.

The security-load-bearing piece is :func:`role_policy`. ``review``/``gate``/
``chair`` are read-only *always* — the same invocation a panel run uses, with no
way to widen it — because those roles read attacker-controlled content.
``implement``/``fix`` are the only roles that can write, and only when the
operator passes ``--allow-write``.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, replace
from pathlib import Path

from .config import DEFAULT_CONFIG, AgentSpec

#: Stable schema identifier for the ``jury run-agent`` JSON result. Bump this
#: when a key changes meaning or disappears; ``tests/test_cli_run_agent.py``
#: pins the whole shape key-by-key so that has to be deliberate.
SCHEMA_VERSION = "ai-jury.run-agent.v1"

#: Every role an orchestrator can dispatch.
ROLES: tuple[str, ...] = ("implement", "review", "gate", "chair", "fix")

#: The roles that may modify a working tree — and only with ``--allow-write``.
WRITE_ROLES = frozenset({"implement", "fix"})

#: The roles that are read-only unconditionally. These read attacker-controlled
#: content (a diff, a PR body, another agent's output), so no flag widens them.
READ_ONLY_ROLES: tuple[str, ...] = tuple(r for r in ROLES if r not in WRITE_ROLES)


# --- role policy -------------------------------------------------------------


@dataclass(frozen=True)
class RolePolicy:
    """What one role is allowed to do (pure value).

    ``refusal`` set means the run must not start at all (exit 2). ``warning`` is
    advisory — the run proceeds, but the operator asked for something that was
    not honoured.
    """

    role: str
    write: bool = False
    refusal: str | None = None
    warning: str | None = None


def role_policy(role: str, allow_write: bool = False) -> RolePolicy:
    """Resolve ``(role, allow_write)`` to a role policy (pure).

    The one place the read-only/write decision is made, so no adapter has to
    re-derive it:

    * ``review``/``gate``/``chair`` → read-only, always. ``--allow-write`` is
      **ignored** rather than obeyed (with a warning), because a reviewer of an
      attacker-controlled diff must not become write-capable by way of a flag —
      the whole point of the least-privilege posture ``privilege.py`` audits.
    * ``implement``/``fix`` → write-capable, but only with ``--allow-write``.
      Without it the run is refused: silently downgrading an implementer to a
      read-only agent produces a confident report of work that never happened.
    """
    name = (role or "").strip().lower()
    if name not in ROLES:
        return RolePolicy(
            role=name,
            refusal=f"unknown role '{role}'; expected one of {', '.join(ROLES)}",
        )
    if name in WRITE_ROLES:
        if not allow_write:
            return RolePolicy(
                role=name,
                refusal=(
                    f"role '{name}' needs write access to be useful; re-run with "
                    f"--allow-write to grant it. Read-only roles "
                    f"({', '.join(READ_ONLY_ROLES)}) never need it."
                ),
            )
        return RolePolicy(role=name, write=True)
    warning = None
    if allow_write:
        warning = (
            f"--allow-write ignored for read-only role '{name}': "
            f"{'/'.join(READ_ONLY_ROLES)} always run under the vendor's read-only "
            f"invocation, the same one a panel review uses."
        )
    return RolePolicy(role=name, write=False, warning=warning)


# --- agent resolution --------------------------------------------------------

#: Bare vendor tokens ``--agent`` accepts with no ``[[agent]]`` entry at all, so
#: ``jury run-agent --agent claude --role review`` works in a repo with no
#: ``jury.toml``. CLI vendors reuse the shipped default entry (including its
#: read-only ``extra_args``); the hosted-API vendors need only their env key.
BUILTIN_AGENTS: tuple[str, ...] = (
    "claude",
    "codex",
    "agy",
    "anthropic-api",
    "openai-api",
    "google-api",
)

#: A model id as the vendors actually spell them: ``gemini-3.8-flash-high``,
#: ``qwen2.5-coder:7b``, ``anthropic/claude-x``. Anchored, and required to start
#: with an alphanumeric so a token can never be read as a flag by the CLI it is
#: forwarded to.
_MODEL_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")


def parse_agent_token(token: str) -> tuple[str, str | None, str | None]:
    """Split ``name`` / ``name:model`` (pure).

    Returns ``(name, model_or_None, error_or_None)``. Only the FIRST colon
    separates, because a model id may legitimately contain one (an Ollama
    ``:tag``, a Bedrock ``…-v1:0``).
    """
    raw = (token or "").strip()
    if not raw:
        return "", None, "--agent must name an agent (e.g. claude, or codex:gpt-5.2)"
    name, sep, model = raw.partition(":")
    name = name.strip()
    if not name:
        return "", None, f"invalid --agent '{token}': the agent name is empty"
    if not sep:
        return name, None, None
    model = model.strip()
    if not _MODEL_TOKEN_RE.match(model):
        return (
            name,
            None,
            f"invalid model '{model}' in --agent '{token}': expected letters, "
            f"digits, '.', '_', ':', '/' or '-', starting with a letter or digit",
        )
    return name, model, None


def builtin_spec(name: str) -> AgentSpec | None:
    """The default :class:`AgentSpec` for a bare built-in vendor token (pure).

    A CLI vendor reuses the entry from :data:`config.DEFAULT_CONFIG` — the same
    command and the same read-only ``extra_args`` a default panel would use — so
    a bare ``--agent claude`` and a configured ``[[agent]] name = "claude"``
    cannot drift apart.
    """
    key = (name or "").strip().lower()
    if key not in BUILTIN_AGENTS:
        return None
    for raw in DEFAULT_CONFIG["agent"]:
        if raw["name"] == key:
            return AgentSpec(
                name=raw["name"],
                vendor=raw["vendor"],
                command=raw.get("command", ""),
                extra_args=list(raw.get("extra_args", [])),
            )
    # A hosted-API vendor: no command, no extra_args, keyed by its env var. The
    # model comes from `vendor:model` (or a [[agent]] entry).
    return AgentSpec(name=key, vendor=key)


def resolve_agent(config, token: str) -> tuple[AgentSpec | None, str | None]:
    """Resolve ``--agent`` to a concrete :class:`AgentSpec` (pure).

    Precedence: a ``[[agent]]`` entry whose ``name`` matches wins over a
    built-in vendor of the same name, so an operator who configured
    ``name = "claude"`` with their own model/flags gets exactly that. A
    ``name:model`` suffix overrides the resolved spec's model either way.

    A configured agent is matched whether or not it is ``enabled``: that flag
    selects the *panel*, and ``run-agent`` is an explicit single dispatch — the
    caller named this agent on purpose.
    """
    name, model, error = parse_agent_token(token)
    if error is not None:
        return None, error

    spec = None
    for candidate in getattr(config, "agents", None) or []:
        if candidate.name == name:
            spec = candidate
            break
    if spec is None:
        spec = builtin_spec(name)
    if spec is None:
        return None, (
            f"unknown agent '{name}': add a [[agent]] entry named '{name}' to "
            f"jury.toml, or use a built-in vendor ({', '.join(BUILTIN_AGENTS)})"
        )
    if model:
        spec = replace(spec, model=model)
    return spec, None


def transport_for(spec: AgentSpec) -> str:
    """How this agent is reached: ``cli``, ``api`` or ``local`` (pure).

    Deliberately the same vocabulary — and the same rule — as
    ``jury --doctor --json``, so an orchestrator reading both sees one answer.
    """
    from .doctor import _transport

    return _transport(spec.vendor, spec.command, spec.endpoint)


# --- attribution -------------------------------------------------------------

#: Prefixes that name a TRANSPORT rather than a model, so ``anthropic-api:``
#: in ``anthropic-api:claude-opus-4-5`` is stripped while the ``:7b`` in
#: ``qwen2.5-coder:7b`` is not. Mirrors keel's ``agents._TRANSPORT_PREFIXES``,
#: which is what consumes these labels.
_TRANSPORT_PREFIXES = frozenset(
    {"anthropic-api", "openai-api", "google-api", "openai-compatible", "ollama", "local"}
)


def strip_transport(model: str) -> str:
    """Drop a ``<transport>:`` prefix, leaving the vendor's own model id (pure).

    Both ``ollama:qwen2.5:7b`` and ``anthropic-api:claude-opus-4-5`` carry a
    colon and only the second has the model on the right, so the colon is read
    by what sits on either side of it, never by position. A colon NOT preceded
    by a transport belongs to the model.
    """
    text = (model or "").strip().lower()
    head, sep, tail = text.partition(":")
    if sep and tail and head in _TRANSPORT_PREFIXES:
        return tail
    return text


def model_base(model: str) -> str:
    """Strip a model id to a coarse, versionless base label (pure).

    ``qwen2.5:7b`` → ``qwen``, ``gemma2`` → ``gemma``, ``gpt-5.5`` → ``gpt-5``,
    ``anthropic-api:claude-opus-4-5`` → ``claude-opus-4-5`` (a hyphenated
    family keeps its major, only the ``.minor`` goes). The label has to stay
    stable across a vendor's point releases, or every model bump would fork the
    attribution history of otherwise-identical work.
    """
    text = strip_transport(model)
    if not text:
        return ""
    text = text.split(":", 1)[0]  # an Ollama :tag
    if "-" in text:
        # A hyphenated family: keep <word>-<major>, drop the .minor.
        head, _, tail = text.partition("-")
        return f"{head}-{tail.split('.', 1)[0]}"
    # Otherwise drop the trailing numeric run (digits and dots).
    i = len(text)
    while i > 0 and (text[i - 1].isdigit() or text[i - 1] == "."):
        i -= 1
    return text[:i]


def attribution(vendor: str, model: str | None = None) -> dict:
    """Who did the work: ``{vendor, model, label}`` (pure).

    ``label`` is the space-joined pair an orchestrator applies verbatim —
    ``agent:<vendor>`` plus a versionless ``model:<base>`` when a model is known.
    Split it on whitespace to get the individual labels back.
    """
    parts = [f"agent:{vendor}"]
    base = model_base(model or "")
    if base:
        parts.append(f"model:{base}")
    return {"vendor": vendor, "model": model or None, "label": " ".join(parts)}


# --- result shape ------------------------------------------------------------


def result_dict(spec: AgentSpec, role: str, result) -> dict:
    """Project an :class:`~ai_jury.adapters.AgentResult` onto the v1 export (pure).

    Every key is always present, with a stable type, so a consumer can read the
    document without probing for optional fields.
    """
    from .adapters import ERR_TIMEOUT

    return {
        "schema_version": SCHEMA_VERSION,
        "ok": bool(result.ok),
        "agent": spec.name,
        "vendor": spec.vendor,
        "model": spec.model or None,
        "role": role,
        "transport": transport_for(spec),
        "text": result.output or "",
        "exit_code": result.exit_code,
        "duration_s": round(float(result.duration_s), 3),
        "timed_out": result.error_code == ERR_TIMEOUT,
        "error_code": result.error_code,
        "error": result.error,
        "attribution": attribution(spec.vendor, spec.model),
    }


# --- detached runs -----------------------------------------------------------

#: Subdirectory of the cache dir that holds detached-run state. The cache dir is
#: already the project's "derived local state" location, already documented as
#: sensitive, and already overridable with ``--cache-dir``/``$JURY_CACHE_DIR``.
RUNS_DIR_NAME = "run-agent"

#: A run id has to be safe as a filename: no separators, no traversal, bounded.
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: Status values a state file can carry.
STATUS_RUNNING = "running"
STATUS_DONE = "done"


def new_run_id(token_fn=None) -> str:
    """A fresh run id (12 hex characters)."""
    return (token_fn or secrets.token_hex)(6)


def check_run_id(run_id: str) -> str | None:
    """None when ``run_id`` is usable as a filename, else why not (pure)."""
    if not _RUN_ID_RE.match(run_id or ""):
        return (
            f"invalid run id '{run_id}': use letters, digits, '.', '_' or '-' "
            f"(max 64 characters, starting with a letter or digit)"
        )
    return None


def runs_dir(cache_dir=None) -> Path:
    """The directory holding detached-run state files."""
    from .cache import default_cache_dir

    base = Path(cache_dir) if cache_dir else default_cache_dir()
    return base / RUNS_DIR_NAME


def state_path(run_id: str, cache_dir=None) -> Path:
    return runs_dir(cache_dir) / f"{run_id}.json"


def output_path(run_id: str, cache_dir=None) -> Path:
    return runs_dir(cache_dir) / f"{run_id}.out"


def write_state(run_id: str, state: dict, cache_dir=None) -> Path:
    """Write a run's state file atomically, 0600, in a 0700 directory.

    A state file holds the agent's full output, which is derived from the
    prompt — the same trust level as the diff a review sees — so it is written
    with the same restrictive permissions the result cache uses. The rename is
    atomic so a ``--wait`` poller never reads a half-written document.
    """
    directory = runs_dir(cache_dir)
    directory.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        directory.chmod(0o700)
    final = directory / f"{run_id}.json"
    tmp = directory / f"{run_id}.json.{secrets.token_hex(4)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")
    tmp.replace(final)
    return final


#: Upper bound on a state file read. A result document is small; this only
#: bounds memory against a corrupt or hostile file in a shared cache dir.
_MAX_STATE_BYTES = 16 * 1024 * 1024


def read_state(run_id: str, cache_dir=None) -> dict | None:
    """Read one run's state, or None when it is missing/unreadable/corrupt."""
    path = state_path(run_id, cache_dir)
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = fh.read(_MAX_STATE_BYTES + 1)
        if len(raw) > _MAX_STATE_BYTES:
            return None
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def list_runs(cache_dir=None) -> list[dict]:
    """Every recorded run, newest first, as summary dicts.

    Summaries only: the full agent text stays in the state file, so ``--status``
    on a busy machine prints a listing rather than every transcript it ever ran.
    """
    directory = runs_dir(cache_dir)
    try:
        names = sorted(p.stem for p in directory.glob("*.json"))
    except OSError:
        return []
    runs = []
    for run_id in names:
        state = read_state(run_id, cache_dir)
        if state is None:
            continue
        runs.append(run_summary(state))
    runs.sort(key=lambda r: (r.get("started_at") or 0, r.get("run_id") or ""), reverse=True)
    return runs


def run_summary(state: dict) -> dict:
    """The listing projection of one state document (pure)."""
    return {
        "run_id": state.get("run_id"),
        "status": state.get("status"),
        "agent": state.get("agent"),
        "role": state.get("role"),
        "ok": state.get("ok"),
        "error_code": state.get("error_code"),
        "started_at": state.get("started_at"),
        "duration_s": state.get("duration_s"),
    }


def initial_state(run_id: str, spec: AgentSpec, role: str, now: float) -> dict:
    """The state file written before a detached child is spawned (pure)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "status": STATUS_RUNNING,
        "agent": spec.name,
        "vendor": spec.vendor,
        "model": spec.model or None,
        "role": role,
        "started_at": now,
    }


def wait_for_run(
    run_id: str,
    cache_dir=None,
    timeout: float | None = None,
    poll_s: float = 0.25,
    sleep=time.sleep,
    clock=time.monotonic,
) -> tuple[dict | None, bool]:
    """Block until a detached run finishes. Returns ``(state, timed_out)``.

    ``sleep`` and ``clock`` are injected so the lifecycle is testable against a
    fake clock instead of real seconds. A missing state file is treated as "not
    finished yet", not as an error: ``--wait`` may legitimately start before the
    child has written anything.
    """
    deadline = None if timeout is None else clock() + float(timeout)
    while True:
        state = read_state(run_id, cache_dir)
        if state is not None and state.get("status") != STATUS_RUNNING:
            return state, False
        if deadline is not None and clock() >= deadline:
            return state, True
        sleep(poll_s)
