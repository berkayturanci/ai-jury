"""Machine-readable progress events for a live jury run (``--events-file``).

A side channel for observers that cannot sit in the jury process — a Claude Code mod,
keel's progress view, a dashboard — so they can follow a run as it happens: the
``on_event`` stream the orchestrator already fires for ``--live`` and ``--theater``,
written as one JSON object per line (NDJSON).

The records carry **metadata only**: who spoke, in which phase, whether it worked,
how long it took and how many findings it raised. No reviewer output, diff text or
finding body is written, so the file can be watched by another process without
widening what the run exposes. Like ``--live``, it never touches the structured
outcome, the report or the CI gate.

Where the file goes: ``--events-file PATH`` names it for one run. Without the flag,
``$JURY_EVENTS_DIR`` (when set, and not ``off``) gives every run its own file in that
directory, ``<UTC stamp>-<pid>.ndjson``, so a watcher that knows the directory finds
runs nobody passed a flag to — one an agent or keel started. The directory keeps the
newest :data:`KEEP` run files. Unset, nothing is written: a run writes no file the
operator did not ask for.

The record builders and the naming are pure; :class:`EventsWriter` and
:func:`open_dir_writer` are the thin I/O around them and the only places that read the
wall clock, the process id or the directory.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TextIO

from .adapters import AgentResult

#: Bumped when a record's shape changes in a way a reader has to know about.
SCHEMA = "ai-jury.events.v1"

#: Phases in the order a run moves through them; ``step`` records name one of these.
PHASES = ("review", "debate", "verify", "synthesis")

#: The environment variable naming the directory every run writes its events to.
ENV_DIR = "JURY_EVENTS_DIR"

#: Values of :data:`ENV_DIR` that mean "write nothing", as unset does.
_OFF = frozenset({"", "off", "0", "false", "no"})

#: How many run files the directory keeps; older ones go when a new run starts.
KEEP = 20

#: How many names a run tries before it gives up on writing events.
_ATTEMPTS = 5

#: The run files this module names, and the only files it ever deletes: a directory
#: shared with anything else loses nothing else.
_RUN_NAME = re.compile(r"^\d{8}T\d{6}\.\d{3}Z-\d+(?:-\d+)?\.ndjson$")


def events_dir(value: str | None) -> Path | None:
    """The directory ``$JURY_EVENTS_DIR`` names, or ``None`` when it asks for none."""
    if value is None or value.strip().lower() in _OFF:
        return None
    return Path(value.strip()).expanduser()


#: The file a watcher (the jury-progress mod) keeps in ``<cache dir>/events`` to ask for
#: events without an environment variable, which a Claude Code mod cannot hand to the
#: commands its session runs. It reads ``on`` while someone watches, ``off`` after.
WATCH_MARKER = ".watched"


def marker_says_on(text: str) -> bool:
    """Whether a watch marker's content asks for events: its first word is ``on``."""
    words = text.split()
    return bool(words) and words[0].lower() == "on"


def watched_dir(directory: Path) -> Path | None:
    """``directory`` when its :data:`WATCH_MARKER` reads ``on``, else ``None``.

    A marker that is missing, unreadable, too large to be one, or says anything else
    asks for nothing: no events are written unless a watcher said so.
    """
    marker = directory / WATCH_MARKER
    try:
        if not marker.is_file() or marker.stat().st_size > 64:
            return None
        return directory if marker_says_on(marker.read_text(encoding="utf-8")) else None
    except (OSError, UnicodeDecodeError):
        return None


def run_file_name(now: float, pid: int, attempt: int = 0) -> str:
    """``<UTC stamp to the ms>-<pid>.ndjson``: sorts by start time.

    ``attempt`` > 0 adds ``-<attempt>``, for a name another run of the same process
    already took in the same millisecond (the file is created exclusively).
    """
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(now))
    millis = int(now * 1000) % 1000
    suffix = f"-{attempt}" if attempt else ""
    return f"{stamp}.{millis:03d}Z-{pid}{suffix}.ndjson"


def stale_run_files(names: Iterable[str], keep: int = KEEP) -> list[str]:
    """The run files past the newest ``keep``, oldest first; other names are never listed."""
    runs = sorted(n for n in names if _RUN_NAME.match(n))
    return runs[: max(0, len(runs) - keep)]


def start_record(
    agents: Iterable[tuple[str, ...]],
    *,
    chair: str | None,
    target: str,
    mode: str,
    decision: str,
    cached: bool,
    pid: int | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    """The first record: the seated panel, so an observer can draw pending seats.

    Each seat is ``(name, vendor)`` or ``(name, vendor, model)``; ``model`` is the
    id the seat will be asked for, or ``None`` when its CLI chooses. ``pid`` lets a
    watcher tell a run still going from one that died without an ``end`` record;
    ``cwd`` tells it which checkout the run reviews from.
    """
    return {
        "event": "start",
        "panel": [
            {
                "agent": seat[0],
                "vendor": seat[1],
                "model": (seat[2] or None) if len(seat) > 2 else None,
            }
            for seat in agents
        ],
        "chair": chair,
        "target": target,
        "mode": mode,
        "decision": decision,
        "cached": cached,
        "phases": list(PHASES),
        "pid": pid,
        "cwd": cwd,
    }


def step_record(kind: str, result: AgentResult, round_no: int | None = None) -> dict[str, Any]:
    """One phase result, as the orchestrator's ``on_event`` reports it."""
    return {
        "event": "step",
        "phase": kind,
        "round": round_no,
        "agent": result.agent,
        "vendor": result.vendor,
        "ok": result.ok,
        "duration_s": round(result.duration_s, 1),
        "findings": len(result.findings),
        "error_code": result.error_code,
        # The id this invocation sent (None when the CLI chose), and what it raised by
        # severity: counts only, never a finding's text.
        "model": getattr(result, "model", "") or None,
        "severity": severity_counts(result.findings),
    }


def severity_counts(findings: Iterable[Any]) -> dict[str, int]:
    """``{severity: count}`` over findings, in no particular order; empty for none."""
    counts: dict[str, int] = {}
    for f in findings:
        sev = getattr(f, "severity", None)
        if isinstance(sev, str) and sev:
            counts[sev] = counts.get(sev, 0) + 1
    return counts


def ballot_entries(reviewers: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The panelists of a report's ``reviewers`` array, cut to what a watcher shows.

    ``agent``, ``model``, ``verdict``, ``findings`` (how many) and ``review``
    (whether the ballot counts as a review): no scope or testing prose, so the
    record stays metadata. The chair's consensus entry is left out; the run's
    verdict is the ``end`` record's own.
    """
    out: list[dict[str, Any]] = []
    for r in reviewers:
        if r.get("role") == "chair":
            continue
        found = r.get("findings")
        out.append(
            {
                "agent": r.get("name"),
                "model": r.get("model") or None,
                "verdict": r.get("verdict"),
                "findings": len(found) if isinstance(found, list) else 0,
                "review": bool(r.get("counts_as_review")),
            }
        )
    return out


def end_record(
    status: str,
    *,
    findings: int | None = None,
    verdict: str | None = None,
    ballots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The last record. ``status`` is ``done``, ``cancelled`` or ``error``.

    ``ballots`` (a done run's, from :func:`ballot_entries`) says how each seat voted.
    """
    rec: dict[str, Any] = {
        "event": "end",
        "status": status,
        "findings": findings,
        "verdict": verdict,
    }
    if ballots is not None:
        rec["ballots"] = ballots
    return rec


class EventsWriter:
    """Writes records to ``path`` as NDJSON, one flushed line each.

    The file is truncated when the writer opens, so it always describes one run.
    Each record gets the schema, a sequence number and a timestamp in seconds.

    Opening is the caller's to fail on (an unwritable path is a user error, reported
    before the run). A write that fails mid-run must not take the review down with it:
    the file is a side channel someone is watching, not the outcome. The first failure
    is reported once through ``on_error`` and the writer goes quiet; the run, its report
    and its CI gate carry on.
    """

    def __init__(
        self,
        path: str,
        *,
        clock: Callable[[], float] = time.time,
        on_error: Callable[[str], None] | None = None,
        exclusive: bool = False,
        source: str = "--events-file",
    ) -> None:
        self._path = path
        self._source = source
        self._fh: TextIO | None
        if exclusive:
            # A file of the run's own in a directory others may share: created, never
            # reused, and never through a symlink someone placed under its name.
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            self._fh = os.fdopen(os.open(path, flags, 0o600), "w", encoding="utf-8")
        else:
            self._fh = Path(path).open("w", encoding="utf-8")  # noqa: SIM115 - closed in close()
        self._clock = clock
        self._seq = 0
        self._on_error = on_error

    @property
    def failed(self) -> bool:
        """Whether a write failed and the writer stopped."""
        return self._fh is None

    def write(self, record: dict[str, Any]) -> None:
        if self._fh is None:
            return
        self._seq += 1
        line = {"schema": SCHEMA, "seq": self._seq, "ts": round(self._clock(), 3), **record}
        try:
            self._fh.write(json.dumps(line, sort_keys=True) + "\n")
            self._fh.flush()
        except OSError as exc:
            self._stop(exc)

    def close(self) -> None:
        if self._fh is None:
            return
        try:
            self._fh.close()
        except OSError as exc:
            self._fh = None
            self._report(exc)
        else:
            self._fh = None

    def _stop(self, exc: OSError) -> None:
        fh, self._fh = self._fh, None
        with contextlib.suppress(OSError):
            if fh is not None:
                fh.close()
        self._report(exc)

    def _report(self, exc: OSError) -> None:
        if self._on_error is not None:
            self._on_error(
                f"{self._source} {self._path}: {exc}; progress events stopped, the review continues"
            )


def open_dir_writer(
    directory: Path,
    *,
    clock: Callable[[], float] = time.time,
    pid: int | None = None,
    on_error: Callable[[str], None] | None = None,
) -> EventsWriter | None:
    """A writer for this run's own file in ``directory``, after pruning old runs.

    Nobody passed a flag for this file, so nothing about it may fail the run: a
    directory that cannot be made or written is reported once through ``on_error``
    and the run goes on without events. A run file that cannot be pruned is left.
    """
    pid = os.getpid() if pid is None else pid
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Room for this run's own file: the directory holds KEEP runs with it. Pruning
        # goes by name, so with KEEP runs going at once the oldest live one's file can
        # go too; that run keeps writing to its open handle, only a watcher loses it.
        for name in stale_run_files((p.name for p in directory.iterdir()), KEEP - 1):
            with contextlib.suppress(OSError):
                (directory / name).unlink()
        now = clock()
        for attempt in range(_ATTEMPTS):
            try:
                return EventsWriter(
                    str(directory / run_file_name(now, pid, attempt)),
                    clock=clock,
                    on_error=on_error,
                    exclusive=True,
                    source=f"${ENV_DIR}",
                )
            except FileExistsError:
                continue
        raise FileExistsError(f"{_ATTEMPTS} run file names for pid {pid} are taken")
    except OSError as exc:
        if on_error is not None:
            on_error(f"${ENV_DIR} {directory}: {exc}; this run writes no progress events")
        return None
