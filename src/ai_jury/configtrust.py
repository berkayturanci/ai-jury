"""Trust gate for an auto-discovered ``jury.toml`` that runs local commands (#831).

The threat is a person running ``jury`` inside a repository they did not write — a clone
from a link, a fork's pull-request branch checked out to review. ``jury`` auto-discovers
``./jury.toml``, and a ``[[agent]]`` there can carry a ``command`` (the generic-CLI adapter):
``command = "sh"`` with ``extra_args = ["-c", "…"]`` is arbitrary code execution the moment
the review runs. The rest of the config surface is already default-secure — a relative-path
command is refused (``config._is_relative_path_command``) and a non-loopback endpoint needs an
env opt-in — but a bare command name resolved from ``PATH`` is not, and that is the gap here.

So a ``command``-bearing config that keel *discovered* (rather than one the operator named with
``--config``) must be trusted before its commands run. Trust is one of, in order: the
``JURY_TRUST_PROJECT_CONFIG`` env opt-in (the same outside-the-config pattern the endpoint
opt-in uses); a recorded entry keyed by the file's real path **and** a hash of its bytes (so an
edit re-asks); or, at a terminal, an explicit confirmation that records that entry. Off a
terminal with none of those, the run is refused with an actionable message. ``jury init``
records trust for the config it writes, so the ordinary ``init`` → ``jury`` path never prompts.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

TRUST_ENV = "JURY_TRUST_PROJECT_CONFIG"
_DISCOVERED_NAME = "jury.toml"


class ConfigTrustError(Exception):
    """Raised when an auto-discovered command-bearing config is not trusted."""


def _truthy_env(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ai-jury"


def trust_store_path() -> Path:
    return _config_dir() / "trusted-configs"


def content_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _entry(path: Path, digest: str) -> str:
    return f"{digest}  {os.path.realpath(path)}"


def is_trusted(path: Path, digest: str) -> bool:
    try:
        lines = trust_store_path().read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return False
    return _entry(path, digest) in lines


def record_trust(path: Path, digest: str) -> None:
    entry = _entry(path, digest)
    store = trust_store_path()
    try:
        store.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        existing = store.read_text(encoding="utf-8").splitlines() if store.exists() else []
        if entry not in existing:
            with store.open("a", encoding="utf-8") as handle:
                handle.write(entry + "\n")
    except (OSError, ValueError):
        # ValueError covers a store whose bytes are not UTF-8 — fail soft like is_trusted.
        # Trust that cannot be persisted is not fatal: the run still proceeds this
        # time (the caller only records after a positive trust decision), it will
        # just ask again next time.
        return


def command_seats(config) -> list[str]:
    """Names of agents whose seat runs a local ``command`` (empty when none do)."""
    seats = []
    for spec in getattr(config, "agents", ()):
        if str(getattr(spec, "command", "") or "").strip():
            seats.append(getattr(spec, "name", "?"))
    return seats


def _discovered_path(config_arg: str | None) -> Path | None:
    """The file keel auto-discovered, or None when the config was not discovered.

    ``--config`` (a named path) and the built-in default (no file on disk) are both
    deliberate, so neither is gated; only ``./jury.toml`` picked up from the working
    directory is.
    """
    if config_arg is not None:
        return None
    candidate = Path(_DISCOVERED_NAME)
    return candidate if candidate.is_file() else None


def enforce(config_arg, config, *, mock: bool, stdin=None, stdout=None) -> None:
    """Refuse to run a discovered, command-bearing ``jury.toml`` unless it is trusted.

    Raises :class:`ConfigTrustError` when the run must not proceed. A no-op for an
    explicit ``--config``, the built-in default, a mock run, or a config with no
    ``command`` seat.
    """
    if mock:
        return
    path = _discovered_path(config_arg)
    if path is None:
        return
    seats = command_seats(config)
    if not seats:
        return
    if _truthy_env(os.environ.get(TRUST_ENV)):
        return
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigTrustError(f"cannot read {path} to check whether it is trusted: {exc}") from exc
    digest = content_digest(raw)
    if is_trusted(path, digest):
        return

    listed = ", ".join(seats)
    stream_in = stdin if stdin is not None else sys.stdin
    stream_out = stdout if stdout is not None else sys.stdout
    if stream_in is not None and hasattr(stream_in, "isatty") and stream_in.isatty():
        # Read the answer from the passed-in streams, not the builtin ``input()`` — that
        # would always read the real ``sys.stdin`` and ignore an injected stream.
        print(
            f"\n{path} was found in this directory and runs local command(s) as part of the "
            f"review:\n  agents with a command: {listed}\n"
            "A cloned or fork repository can ship a jury.toml that runs arbitrary commands.\n"
            "Trust this config and run its commands? [y/N] ",
            end="",
            file=stream_out,
            flush=True,
        )
        answer = (stream_in.readline() or "").strip().lower()
        if answer in {"y", "yes"}:
            record_trust(path, digest)
            return
        raise ConfigTrustError("not trusted by the operator; nothing was run")

    raise ConfigTrustError(
        f"refusing to run commands from an auto-discovered {path} without confirmation "
        f"(agents with a command: {listed}). A cloned or fork repository can ship a jury.toml "
        f"that runs arbitrary commands. To proceed: pass --config {path} if you trust it, set "
        f"{TRUST_ENV}=1, or run once in a terminal to confirm."
    )
