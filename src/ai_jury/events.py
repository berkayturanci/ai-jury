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

The record builders are pure; :class:`EventsWriter` is the thin I/O around them and
the only place that reads the wall clock.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TextIO

from .adapters import AgentResult

#: Bumped when a record's shape changes in a way a reader has to know about.
SCHEMA = "ai-jury.events.v1"

#: Phases in the order a run moves through them; ``step`` records name one of these.
PHASES = ("review", "debate", "verify", "synthesis")


def start_record(
    agents: Iterable[tuple[str, str]],
    *,
    chair: str | None,
    target: str,
    mode: str,
    decision: str,
    cached: bool,
) -> dict[str, Any]:
    """The first record: the seated panel, so an observer can draw pending seats."""
    return {
        "event": "start",
        "panel": [{"agent": name, "vendor": vendor} for name, vendor in agents],
        "chair": chair,
        "target": target,
        "mode": mode,
        "decision": decision,
        "cached": cached,
        "phases": list(PHASES),
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
    }


def end_record(
    status: str,
    *,
    findings: int | None = None,
    verdict: str | None = None,
) -> dict[str, Any]:
    """The last record. ``status`` is ``done``, ``cancelled`` or ``error``."""
    return {"event": "end", "status": status, "findings": findings, "verdict": verdict}


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
    ) -> None:
        self._path = path
        self._fh: TextIO | None = Path(path).open("w", encoding="utf-8")  # noqa: SIM115 - closed in close()
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
                f"--events-file {self._path}: {exc}; progress events stopped, the review continues"
            )
