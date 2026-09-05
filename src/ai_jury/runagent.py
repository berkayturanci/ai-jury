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
    "xai-api",
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
    {
        "anthropic-api",
        "openai-api",
        "google-api",
        "xai-api",
        "openai-compatible",
        "ollama",
        "local",
    }
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
    """Strip a model id to a coarse **family + major** label (pure).

    ``qwen2.5:7b`` → ``qwen``, ``gemma2`` → ``gemma``, ``gpt-5.5`` → ``gpt-5``,
    ``anthropic-api:claude-opus-4-5`` → ``claude-opus-4-5``. The label has to
    stay stable across a vendor's point releases, or every model bump would
    fork the attribution history of otherwise-identical work.

    **The grouping is deliberately coarse and deliberately uneven**, and it is
    not a bug to be smoothed out here. The rule is byte-for-byte keel's
    ``agents.model_base`` (ship #2036) because the two projects write the same
    ``model:<base>`` label onto the same issues — a "better" rule on one side
    only would silently split one project's history in half. Consequences worth
    knowing before you rely on the label:

    * A tier or effort suffix **collapses**: ``gemini-3.8-flash``,
      ``gemini-3.8-flash-high`` and ``gemini-3.8-pro`` all become ``gemini-3``,
      because everything after the first hyphen is cut at the first ``.``.
    * A vendor that spells its version with hyphens instead **keeps** it:
      ``claude-opus-4-5`` and ``claude-opus-4-6`` stay distinct.

    So the label answers "roughly which family ran this", not "exactly which
    model". When the exact id matters, read the ``model`` field of the result
    document, which carries it verbatim.
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
#: A run still marked running whose child process is gone (see run_summary).
STATUS_LOST = "lost"


def new_run_id(token_fn=None) -> str:
    """A fresh run id (12 hex characters)."""
    return (token_fn or secrets.token_hex)(6)


class RunIdError(ValueError):
    """An identifier that must never be turned into a path (issue #661).

    Its own type so a caller can tell "you named a run badly" apart from the
    ``ValueError`` a corrupt JSON body raises.
    """


def check_run_id(run_id) -> str | None:
    """None when ``run_id`` is usable as a filename, else why not (pure)."""
    if not isinstance(run_id, str) or not _RUN_ID_RE.match(run_id):
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


def run_path(run_id, suffix: str, cache_dir=None) -> Path:
    """The path of one run's file, or raise :class:`RunIdError` (issue #661).

    **The only place a run id becomes a path**, so every caller — the detach
    parent, the ``--_child`` writer, ``--wait``, ``--status`` — inherits the
    same check rather than each remembering to make it. Validating in the
    parent alone was not enough: the child re-parses its own ``--run-id`` and
    wrote wherever it pointed, so ``--run-id ../../PWNED`` (or an absolute
    path) put a file holding the agent's full output outside the cache
    directory entirely.

    Two barriers, because one of them can be loosened by a future edit to a
    regex: the id must match :data:`_RUN_ID_RE` (no separator, no leading dot,
    so neither ``..`` nor an absolute path can be spelled), and the resolved
    file must still sit directly inside the runs directory.
    """
    problem = check_run_id(run_id)
    if problem:
        raise RunIdError(problem)
    directory = runs_dir(cache_dir)
    path = directory / f"{run_id}{suffix}"
    # Belt and braces: `..` and `/` are already unspellable above, but a path
    # that escaped anyway must not be written to. Compared without touching the
    # filesystem, so a missing directory is not an error here.
    if os.path.normpath(path.parent) != os.path.normpath(directory):
        raise RunIdError(f"invalid run id '{run_id}': resolves outside {directory}")
    return path


def state_path(run_id, cache_dir=None) -> Path:
    return run_path(run_id, ".json", cache_dir)


def output_path(run_id, cache_dir=None) -> Path:
    return run_path(run_id, ".out", cache_dir)


def write_state(run_id, state: dict, cache_dir=None) -> Path:
    """Write a run's state file atomically, 0600, in a 0700 directory.

    A state file holds the agent's full output, which is derived from the
    prompt — the same trust level as the diff a review sees — so it is written
    with the same restrictive permissions the result cache uses. The rename is
    atomic so a ``--wait`` poller never reads a half-written document.

    Raises :class:`RunIdError` before creating anything when the id is not a
    safe filename; a write to an unvalidated location must fail loudly.
    """
    final = state_path(run_id, cache_dir)
    directory = final.parent
    directory.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        directory.chmod(0o700)
    tmp = directory / f"{final.name}.{secrets.token_hex(4)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")
    tmp.replace(final)
    return final


#: Upper bound on a state file read. A result document is small; this only
#: bounds memory against a corrupt or hostile file in a shared cache dir.
_MAX_STATE_BYTES = 16 * 1024 * 1024


def read_state(run_id, cache_dir=None) -> dict | None:
    """Read one run's state, or None when it is missing/unreadable/corrupt.

    An unsafe id is a miss, not a raise: a read is a lookup, and "there is no
    such run" is the honest answer for a name that could never have named one.
    """
    try:
        path = state_path(run_id, cache_dir)
        with path.open("r", encoding="utf-8") as fh:
            raw = fh.read(_MAX_STATE_BYTES + 1)
        if len(raw) > _MAX_STATE_BYTES:
            return None
        data = json.loads(raw)
    except (OSError, ValueError):  # RunIdError is a ValueError
        return None
    return data if isinstance(data, dict) else None


class _DefaultProbe:
    """Sentinel: resolve :func:`pid_alive` when the call happens.

    Distinct from ``None`` on purpose. ``alive_fn=None`` used to mean "use the
    default" in :func:`wait_for_run`/:func:`list_runs` but "do not probe at all"
    in :func:`run_summary` — the same value with opposite meanings, one function
    apart. Now ``None`` means "do not probe" everywhere and this means "use the
    default", which cannot be confused for a caller-supplied probe.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<default probe>"


#: See :class:`_DefaultProbe`.
DEFAULT_PROBE = _DefaultProbe()


def _resolve_probe(alive_fn):
    """The probe to call: the module-level default, a caller's, or none."""
    return pid_alive if alive_fn is DEFAULT_PROBE else alive_fn


def pid_alive(pid) -> bool | None:
    """Is that process still running? ``None`` when we cannot safely tell.

    POSIX only. On Windows ``os.kill(pid, 0)`` does **not** mean "probe": for
    any signal other than the two console events CPython opens the process and
    calls ``TerminateProcess``, so a liveness check there would kill the very
    run it was asked about. Returning ``None`` (unknown) leaves the run
    reported as ``running``, which is the safe reading of "we don't know".

    **A live pid is not proof of identity.** Pids are recycled, so a state file
    that outlives its process — across a reboot, or in a ``--cache-dir`` shared
    between machines — can name a pid some unrelated process now holds, and this
    reports ``True`` for it. That is why the answer is only ever used to demote
    ``running`` to ``lost`` on a definite ``False``: a wrong ``True`` leaves a
    finished-looking run reported as still running (recoverable, and the state
    file carries ``started_at`` so a reader can see how old the claim is), while
    a wrong ``False`` would declare a live run dead. Ruling out recycling needs
    the process start time, which the standard library does not expose
    portably — and a runtime dependency is not on the table for this.

    ``bool`` is excluded explicitly because it subclasses ``int``: without that
    guard ``pid_alive(True)`` probes pid 1, which on POSIX raises
    ``PermissionError`` and so reports "alive" — while
    :func:`liveness_unknown_reason` calls the very same state document
    unrecorded. Two classifications of one state that disagree is a drift the
    guard costs one term to prevent.
    """
    if os.name == "nt" or not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive, and owned by somebody else.
        return True
    except OSError:
        return None
    return True


def list_runs(cache_dir=None, alive_fn=DEFAULT_PROBE) -> list[dict]:
    """Every recorded run, newest first, as summary dicts.

    Summaries only: the full agent text stays in the state file, so ``--status``
    on a busy machine prints a listing rather than every transcript it ever ran.

    ``alive_fn`` defaults to :data:`DEFAULT_PROBE`, which resolves to
    :func:`pid_alive` at call time (see :func:`wait_for_run` for why that is not
    a default argument). Passing ``None`` means "do not probe", the same as it
    does everywhere else.
    """
    probe = _resolve_probe(alive_fn)
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
        runs.append(run_summary(state, alive_fn=probe))
    runs.sort(key=lambda r: (r.get("started_at") or 0, r.get("run_id") or ""), reverse=True)
    return runs


def run_summary(state: dict, alive_fn=None) -> dict:
    """The listing projection of one state document (pure given *alive_fn*).

    A run still marked ``running`` whose child is definitively gone is reported
    as ``lost``: the child is killed, or crashed hard enough to skip even its
    own ``finally``, and reporting it as running forever is the one answer that
    is certainly wrong. Only a definitive ``False`` from *alive_fn* demotes it —
    unknown (no pid recorded, or a platform we cannot probe) stays ``running``.
    """
    status = state.get("status")
    if status == STATUS_RUNNING and alive_fn is not None and alive_fn(state.get("pid")) is False:
        status = STATUS_LOST
    return {
        "run_id": state.get("run_id"),
        "status": status,
        "agent": state.get("agent"),
        "role": state.get("role"),
        "ok": state.get("ok"),
        "error_code": state.get("error_code"),
        "started_at": state.get("started_at"),
        "duration_s": state.get("duration_s"),
        "pid": state.get("pid"),
    }


def initial_state(
    run_id: str, spec: AgentSpec, role: str, now: float, pid: int | None = None
) -> dict:
    """The state file written before a detached child is spawned (pure).

    ``pid`` and ``timeout_s`` are recorded so a later ``--status`` can tell a
    live run from an abandoned one, and so ``--wait`` can derive a deadline
    from what the run was actually given rather than a guess.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "status": STATUS_RUNNING,
        "agent": spec.name,
        "vendor": spec.vendor,
        "model": spec.model or None,
        "role": role,
        "started_at": now,
        "pid": pid,
        "timeout_s": spec.timeout,
    }


def liveness_unknown_reason(state, alive_fn=DEFAULT_PROBE) -> str | None:
    """Why a running run's liveness cannot be determined, or None when it can.

    Two genuinely different situations reach the same ``pid_alive`` answer of
    ``None``, and telling a POSIX operator that "this platform cannot probe"
    when the truth is "the child has not claimed the run yet" is simply false:

    * **No process id recorded.** The ordinary window between ``--detach``
      writing the state file and the child claiming it — and also where a child
      died before it could claim. Happens on every platform.
    * **The platform cannot probe one.** Windows, where ``os.kill(pid, 0)``
      terminates rather than probes.

    A finished run has nothing to determine, so it returns None.
    """
    state = state or {}
    if state.get("status") != STATUS_RUNNING:
        return None
    pid = state.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return "it has not recorded a process id yet"
    probe = _resolve_probe(alive_fn)
    if probe is not None and probe(pid) is None:
        return "this platform cannot check a process id without terminating it"
    return None


#: Head-room added to a run's own timeout to get a default ``--wait`` deadline:
#: the agent has until its timeout, and the child needs a moment after that to
#: write its state file.
WAIT_GRACE_S = 60

#: Deadline used when a run has recorded no timeout of its own (no state file
#: yet, or one written by an older version). Bounded rather than infinite, so a
#: scripted `--wait` cannot hang a pipeline forever.
DEFAULT_WAIT_TIMEOUT_S = 3600


def default_wait_timeout(state) -> float:
    """The ``--wait`` deadline implied by a run's own timeout (pure)."""
    recorded = (state or {}).get("timeout_s")
    if isinstance(recorded, int) and not isinstance(recorded, bool) and recorded > 0:
        return float(recorded + WAIT_GRACE_S)
    return float(DEFAULT_WAIT_TIMEOUT_S)


def wait_for_run(
    run_id: str,
    cache_dir=None,
    timeout: float | None = None,
    poll_s: float = 0.25,
    sleep=time.sleep,
    clock=time.monotonic,
    alive_fn=DEFAULT_PROBE,
) -> tuple[dict | None, bool]:
    """Block until a detached run finishes. Returns ``(state, timed_out)``.

    ``sleep``, ``clock`` and ``alive_fn`` are injected so the lifecycle is
    testable against a fake clock instead of real seconds; ``alive_fn`` defaults
    to :data:`DEFAULT_PROBE`, i.e. :func:`pid_alive` looked up when this is
    *called*, and ``None`` disables probing. A missing state file
    is treated as "not finished yet", not as an error: a caller may legitimately
    start waiting before the child has written anything.

    A run whose recorded process is **definitively gone** returns immediately
    with its status mapped to :data:`STATUS_LOST`, rather than blocking to the
    deadline for a result that is never coming — the same judgement ``--status``
    makes, applied here so the two cannot disagree. Before reporting that, the
    state is re-read once: the child may have written its terminal document in
    the window between our read and the probe, and a real answer always wins
    over an inference about a pid.
    """
    # Resolved at CALL time, not bound as a default: a default argument would
    # capture this module's `pid_alive` when the def executes, and a test that
    # patches `runagent.pid_alive` would then patch nothing while appearing to
    # work — which is exactly how a Windows-only infinite wait reached CI green
    # on every other platform.
    probe = _resolve_probe(alive_fn)
    deadline = None if timeout is None else clock() + float(timeout)
    while True:
        state = read_state(run_id, cache_dir)
        if state is not None and state.get("status") != STATUS_RUNNING:
            return state, False
        if state is not None and probe is not None and probe(state.get("pid")) is False:
            confirmed = read_state(run_id, cache_dir)
            if confirmed is not None and confirmed.get("status") != STATUS_RUNNING:
                return confirmed, False
            lost = dict(confirmed or state)
            lost["status"] = STATUS_LOST
            return lost, False
        if deadline is not None and clock() >= deadline:
            return state, True
        sleep(poll_s)
