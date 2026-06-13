"""Animated "courtroom" scene for a live jury run (opt-in ``--theater`` mode).

A presentation-only consumer of the orchestrator's ``on_event`` stream: it draws
a fixed-grid courtroom where each model sits in a seat, stands to speak as the
real run moves through its phases (review -> debate -> verify -> verdict), and the
chair gavels a verdict at the end. Pure stdlib (ANSI escapes; no curses, no
deps).

It reflects the REAL run — seats come from the configured panel, and every
speech bubble / finding / verdict is the actual structured output of that phase
(``--mock`` drives the deterministic mock panel for a demo). It is a side
channel only: it never touches the structured outcome, the report, or the CI
gate, and it degrades to the plain ``--live`` transcript on a non-TTY.

The design (region bands, sprite states, colour map) follows
``docs/theater-design.md``.
"""

from __future__ import annotations

import shutil
import sys
import time

from .adapters import AgentResult
from .findings import SEVERITY_ORDER, flatten_inline, parse_findings, parse_verdicts

# ---- styling ---------------------------------------------------------------
_RESET = "\033[0m"
_VENDOR_SGR = {"anthropic": "33", "openai": "32", "google": "34", "local": "35"}
_PHASES = (("review", "REVIEW"), ("debate", "DEBATE"), ("verify", "VERIFY"),
           ("synthesis", "VERDICT"))
_W = 11  # seat sprite width

_GLYPHS = {"scale": "⚖", "caret": "▲", "ok": "✓", "no": "✗", "dispute": "⚖",
           "gavel": "/\\", "strike": "[##]", "play": "⏵", "star": "*"}
_ASCII_GLYPHS = {"scale": "[J]", "caret": "^", "ok": "v", "no": "x", "dispute": "?",
                 "gavel": "/\\", "strike": "[##]", "play": ">", "star": "*"}
# Positive / negative / neutral verdict vocab for code AND issue modes.
_VERDICT_POS = ("APPROVE", "READY")
_VERDICT_NEG = ("REQUEST", "BLOCK", "NEEDS-INFO", "NEEDS INFO", "CHANGES")


def _banner_sgr(verdict: str) -> str:
    up = verdict.upper()
    if any(k in up for k in _VERDICT_NEG):
        return "97;41;1"   # white on red
    if any(k in up for k in _VERDICT_POS):
        return "30;42;1"   # black on green
    return "30;43;1"       # black on yellow (COMMENT / UNCLEAR / neutral)


def supports_scene(stream) -> bool:
    """True when ``stream`` is a TTY wide enough for the seated scene."""
    try:
        if not stream.isatty():
            return False
    except Exception:  # noqa: BLE001
        return False
    return shutil.get_terminal_size((80, 24)).columns >= 60


class Screen:
    """A fixed grid of (char, sgr) cells rendered to ANSI or plain text."""

    def __init__(self, cols: int, rows: int):
        self.cols, self.rows = cols, rows
        self.clear()

    def clear(self) -> None:
        self._g = [[(" ", "")] * self.cols for _ in range(self.rows)]

    def put(self, r: int, c: int, text: str, sgr: str = "") -> None:
        if not (0 <= r < self.rows):
            return
        row = self._g[r]
        for i, ch in enumerate(text):
            x = c + i
            if 0 <= x < self.cols:
                row[x] = (ch, sgr)

    def center(self, r: int, text: str, sgr: str = "") -> None:
        self.put(r, max(0, (self.cols - len(text)) // 2), text, sgr)

    def _row_ansi(self, row) -> str:
        out, cur = [], None
        for ch, sgr in row:
            if sgr != cur:
                out.append(_RESET if not sgr else f"\033[{sgr}m")
                cur = sgr
            out.append(ch)
        if cur:
            out.append(_RESET)
        return "".join(out).rstrip()

    def to_ansi(self) -> str:
        return "\n".join(self._row_ansi(r) for r in self._g)

    def to_plain(self) -> str:
        return "\n".join("".join(ch for ch, _ in r).rstrip() for r in self._g)


class Courtroom:
    """Draws and animates the courtroom from the on_event stream."""

    def __init__(self, agents, chair: str, *, case: str = "", stream=None,
                 animate: bool = True, cols: int | None = None, rows: int = 30,
                 capture=None, mode: str = "code", decision: str = "chair",
                 unicode: bool = True):
        # agents: list of (name, vendor); chair: name of the synthesizing agent.
        # mode: "code" (PR/diff) or "issue" (issue triage) — changes labels +
        # verdict vocabulary. decision: "chair" (single synthesizer gavels) or
        # "vote" (the panel casts ballots and a tally decides) — different finale.
        self.agents = list(agents)
        self.chair = chair
        self.case = case
        self.mode = mode
        self.decision = decision
        self.out = stream if stream is not None else sys.stdout
        self.animate = animate
        self.cols = cols or min(98, max(70, shutil.get_terminal_size((90, 30)).columns))
        self.rows = rows
        self._capture = capture  # optional list to append frame snapshots (preview)
        self.g = _GLYPHS if unicode else _ASCII_GLYPHS
        self.hr = "─" if unicode else "-"
        self.dot = "·" if unicode else "."
        self.screen = Screen(self.cols, self.rows)
        self.phase = None
        self.done_phases: set[str] = set()
        self.state: dict[str, str] = {a[0]: "idle" for a in self.agents}
        self.log: list[str] = []
        self.bubble: tuple[str, str] = ("", "")  # (speaker, text)
        self.verifies: list = []
        self.verdict: str | None = None
        self.vote = None            # set_vote(): panel ballots/tally for vote mode
        self.ballots: dict[str, str] = {}
        self.debate_seen = False
        self.max_round = 0
        self.disputes = 0
        self.start = time.monotonic()

    # -- geometry --------------------------------------------------------
    def _seat_x(self, i: int) -> int:
        n = len(self.agents)
        interior = self.cols - 2
        gutter = max(1, (interior - n * _W) // (n + 1))
        return 1 + gutter + i * (_W + gutter)

    # -- painting --------------------------------------------------------
    def _paint(self) -> None:
        s = self.screen
        s.clear()
        self._title()
        self._strip()
        self._bench()
        self._seats()
        self._speaking_area()
        self._transcript()
        self._status()

    def _title(self) -> None:
        s = self.screen
        s.put(0, 1, f"{self.g['scale']}  ai-jury", "93;1")
        decided = "panel vote" if self.decision == "vote" else "chair"
        meta = f"{len(self.agents)} jurors {self.dot} {decided}"
        if self.case:
            meta = f"case: {self.case}  {self.dot}  " + meta
        s.put(0, 16, meta, "97")
        s.put(1, 0, self.hr, "2;37")

    def _strip(self) -> None:
        s = self.screen
        x = 2
        for kind, label in _PHASES:
            text = label
            if kind == "debate" and self.max_round > 1 and kind in (self.phase, *self.done_phases):
                text = f"{label}{self.dot}r{self.max_round}"
            if kind in self.done_phases:
                mark, sgr = self.g["ok"], "32"
            elif kind == self.phase:
                mark, sgr = ">", "96;1"
            else:
                mark, sgr = self.dot, "2;37"
            cell = f"{mark} {text}"
            s.put(2, x, cell, sgr)
            x += len(cell) + 1
            if kind != _PHASES[-1][0]:
                s.put(2, x, "--", "2;37")
                x += 3
        elapsed = int(time.monotonic() - self.start)
        s.put(2, self.cols - 8, f"{elapsed // 60:02d}:{elapsed % 60:02d}", "96")
        s.put(3, 0, self.hr, "2;37")

    def _bench(self) -> None:
        s = self.screen
        active = self.phase in ("verify", "synthesis")
        sgr = "97;1" if active else "2;37"
        cx = self.cols // 2
        bench_title = "THE PANEL" if self.decision == "vote" else "THE HON. CHAIR"
        s.put(5, cx - 13, ".----------------------.", sgr)
        s.put(6, cx - 13, f"|  {bench_title:^18}  |", sgr)
        gavel = self.g["strike"] if self.verdict else self.g["gavel"]
        seat = "vote" if self.decision == "vote" else self.chair[:8]
        s.put(7, cx - 13, f"|    [ {seat:^8} ]   |", sgr)
        s.put(8, cx - 13, "'----------------------'", sgr)
        s.put(7, cx + 12, gavel, "93;1" if self.verdict else sgr)
        if self.verdict:
            star = self.g["star"]
            s.center(4, f"{star}  ORDER IN THE COURT  {star}", "93;1")

    def _sprite_state(self, name: str) -> str:
        return self.state.get(name, "idle")

    def _seats(self) -> None:
        s = self.screen
        top = 10
        faces = {"idle": "( -- )", "speaking": "( o o )", "arguing": "( ^ ^ )",
                 "done": "( - - )", "error": "( x x )"}
        for i, (name, vendor) in enumerate(self.agents):
            x = self._seat_x(i)
            st = self._sprite_state(name)
            vsgr = _VENDOR_SGR.get(vendor, "37")
            hi = st in ("speaking", "arguing")
            face = faces.get(st, faces["idle"])
            nm = name[:6]
            body = f"/|{nm:^6}|\\" if hi else f" |{nm:^6}| "
            s.put(top, x, face, "96;1" if hi else "2;37")
            s.put(top + 1, x, body, "97" if hi else "37")
            s.put(top + 2, x, "_|######|_", "2;37")
            tag = {"anthropic": "[ANTHRO]", "openai": "[OPENAI]",
                   "google": "[GOOGLE]", "local": "[LOCAL ]"}.get(vendor, "[AGENT ]")
            mark = f" {self.g['ok']}" if st == "done" else (" !" if st == "error" else "")
            s.put(top + 3, x, tag + mark, vsgr + (";1" if hi else ""))
            # Ballot chip above a seat in vote-mode finale.
            ballot = self.ballots.get(name)
            if ballot:
                s.put(top - 1, x, f"[{ballot[:7]}]", _banner_sgr(ballot))
            if hi:
                s.put(top + 4, x + 2, self.g["caret"], "96")

    def _speaking_area(self) -> None:
        s = self.screen
        r0 = 16
        s.put(r0, 0, self.hr, "2;37")
        if self.phase == "verify" and self.verifies:
            s.put(r0, 2, " VERIFY ", "97;1")
            for j, v in enumerate(self.verifies[:4]):
                mk, msg, sgr = self._verify_row(v)
                s.put(r0 + 1 + j, 4, f"{mk} {msg}", sgr)
            return
        if self.verdict:
            head = " THE VERDICT - by panel vote " if self.decision == "vote" else " VERDICT "
            s.put(r0, 2, head, "97;1")
            extra = ""
            if self.decision == "vote" and self.vote is not None:
                extra = "   " + f" {self.dot} ".join(
                    f"{n} {lbl.lower()}" for lbl, n in self.vote.tally.items()
                )
            banner = f"  {self.g['scale']}  {self.verdict}{extra}  "
            vsgr = _banner_sgr(self.verdict)
            s.center(r0 + 2, "#" * (len(banner) + 4), vsgr)
            s.center(r0 + 3, "# " + banner + " #", vsgr)
            s.center(r0 + 4, "#" * (len(banner) + 4), vsgr)
            return
        speaker, text = self.bubble
        if speaker:
            label = f" NOW SPEAKING: {speaker} "
            s.put(r0, 2, label, "96;1")
            wrapped = _wrap(text, self.cols - 12)[:3]
            width = max((len(w) for w in wrapped), default=0)
            s.put(r0 + 1, 6, "." + "-" * (width + 2) + ".", "97")
            for j, w in enumerate(wrapped):
                s.put(r0 + 2 + j, 6, f"( {w:<{width}} )", "39")
            s.put(r0 + 2 + len(wrapped), 6, "'" + "-" * (width + 2) + "'", "97")

    def _verify_row(self, v):
        msg = f"{v.status:<18} {flatten_inline(v.claim)[:44]}"
        if v.status == "verified":
            return self.g["ok"], msg, "32;1"
        if v.status == "unsupported":
            return self.g["no"], msg, "2;31"
        return self.g["dispute"], msg, "33"

    def _transcript(self) -> None:
        s = self.screen
        r0 = 23
        s.put(r0, 0, self.hr, "2;37")
        s.put(r0, 2, " TRANSCRIPT ", "2;37")
        for j, line in enumerate(self.log[-4:]):
            s.put(r0 + 1 + j, 2, flatten_inline(line)[: self.cols - 4], "37")

    def _status(self) -> None:
        s = self.screen
        msg = "court adjourned" if self.verdict else (f"{self.phase or 'opening'}...")
        s.put(self.rows - 1, 2, f"{self.g['play']} {msg}", "96")

    # -- emit / animate --------------------------------------------------
    def _flush(self) -> None:
        frame = self.screen.to_ansi()
        if self._capture is not None:
            self._capture.append(frame)
        if self.animate:
            # Move home and repaint (clears via per-row trailing spaces is skipped;
            # full clear keeps it simple and flicker-tolerant for event cadence).
            self.out.write("\033[H\033[J" + frame)
            self.out.flush()

    def _beat(self, secs: float) -> None:
        if self.animate:
            time.sleep(secs)

    def _frame(self, beat: float = 0.0) -> None:
        self._paint()
        self._flush()
        self._beat(beat)

    # -- public API ------------------------------------------------------
    def open(self) -> None:
        if self.animate:
            self.out.write("\033[2J\033[?25l")  # clear + hide cursor
        self.phase = "review"
        self.log.append("court is now in session")
        self._paint()
        self._flush()
        self._beat(0.4)

    def step(self, kind: str, result: AgentResult, round_no: int | None = None) -> None:
        # Flow awareness: if we reach verify/synthesis having never debated, the
        # panel converged on round 1 — note it (before the generic done-phase
        # fill below would silently mark debate done).
        skipped_debate = (
            kind in ("verify", "synthesis")
            and not self.debate_seen
            and "debate" not in self.done_phases
        )
        if kind != self.phase and self.phase is not None:
            self.done_phases.update(
                k for k, _ in _PHASES if k != kind and self._phase_before(k, kind)
            )
        if skipped_debate:
            self.done_phases.add("debate")
            self.log.append("no cross-examination - reviewers converged")
        self.phase = kind
        if kind == "debate":
            self.debate_seen = True
            self.max_round = max(self.max_round, round_no or 1)
        if kind in ("review", "debate"):
            self._speak(kind, result, round_no)
        elif kind == "verify":
            self._verify(result)
        elif kind == "synthesis":
            self._synthesize(result)

    @staticmethod
    def _phase_before(a: str, b: str) -> bool:
        order = [k for k, _ in _PHASES]
        return order.index(a) < order.index(b)

    def _speak(self, kind, result, _round_no=None):
        name = result.agent
        # everyone idle, speaker stands
        for k in self.state:
            if self.state[k] != "done":
                self.state[k] = "idle"
        self.state[name] = "arguing" if kind == "debate" else "speaking"
        if not result.ok:
            self.state[name] = "error"
            self.bubble = (name, flatten_inline(result.error or "no output"))
            self.log.append(f"{name}: failed")
            self._frame(0.5)
            return
        findings, _ = parse_findings(result.output or "", name)
        if findings:
            top = sorted(findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 9))[0]
            text = f"[{top.severity}] {flatten_inline(top.claim)}"
            verb = "raises" if kind == "review" else "argues"
            self.log.append(f"{name} {verb} {top.severity}: {flatten_inline(top.claim)[:48]}")
        else:
            text = _gist(result.output or "")
            self.log.append(f"{name}: {text[:54]}")
        self.bubble = (name, text)
        self._frame(0.6)
        self.state[name] = "idle"

    def _verify(self, result):
        for k in self.state:
            self.state[k] = "done" if self.state[k] != "error" else "error"
        verdicts, _ = parse_verdicts(result.output or "", result.agent)
        self.verifies = verdicts
        ok = sum(1 for v in verdicts if v.status == "verified")
        self.disputes = sum(1 for v in verdicts if v.status == "needs_human_decision")
        note = f"chair verifies: {ok}/{len(verdicts) or 0} upheld"
        if self.disputes:
            note += f" {self.dot} {self.disputes} disputed"
        self.log.append(note)
        self.bubble = ("", "")
        self._frame(0.6)

    def _synthesize(self, result):
        self.done_phases.update({"review", "debate", "verify"})
        # In vote mode the verdict is decided by the panel tally (set_vote), not
        # the chair's synthesis — so don't overwrite it here.
        if self.decision != "vote":
            self.verdict = _verdict_headline(result.output or "") if result.ok else "NO VERDICT"
            self.log.append(f"VERDICT -> {self.verdict}")
            self._frame(0.4)

    def set_vote(self, vote) -> None:
        """Provide the panel-vote result (decided after the run) for the finale."""
        self.vote = vote
        self.verdict = getattr(vote, "verdict", None)
        for b in getattr(vote, "ballots", []):
            self.ballots[b.reviewer] = b.vote

    def close(self) -> None:
        self.done_phases.update(k for k, _ in _PHASES)
        if self.decision == "vote" and self.vote is not None:
            # Vote finale: jurors cast ballots (chips above seats), then the
            # tally banner — a different beat from the chair's gavel.
            self.log.append(f"panel votes -> {self.verdict}")
            self._frame(0.6)
        self._paint()
        self._flush()
        if self.animate:
            self.out.write("\033[?25h\n")  # show cursor
            self.out.flush()


# ---- helpers ---------------------------------------------------------------
def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def _gist(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return flatten_inline(line)[:100]
    return "(no output)"


def _verdict_headline(text: str) -> str:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("## verdict"):
            for nxt in lines[i + 1:]:
                if nxt.strip():
                    return flatten_inline(nxt)
    return _gist(text)
