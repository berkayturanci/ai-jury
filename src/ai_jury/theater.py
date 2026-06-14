"""Animated "deliberation" scene for a live jury run (opt-in ``--theater`` mode).

A presentation-only consumer of the orchestrator's ``on_event`` stream: it draws
a top-down **deliberation room** where the models sit around a table, take turns
speaking as the run moves through its phases (review -> debate -> verify ->
decision), and reach a decision together — by panel vote, or recorded by the
chair (the synthesizer). There is no judge; the jurors deliberate with each
other. Pure stdlib (ANSI escapes; no curses, no deps).

It reflects the REAL run — seats come from the configured panel, and every
speech bubble / finding / decision is the actual structured output of that phase
(``--mock`` drives the deterministic mock panel for a demo). It is a side
channel only: it never touches the structured outcome, the report, or the CI
gate, and it degrades to the plain ``--live`` transcript on a non-TTY.

The design follows ``docs/theater-design.md``.
"""

from __future__ import annotations

import shutil
import sys
import threading
import time

from .adapters import AgentResult
from .findings import SEVERITY_ORDER, flatten_inline, parse_findings, parse_verdicts

# ---- styling ---------------------------------------------------------------
_RESET = "\033[0m"
# Each vendor's own product brand colour (24-bit truecolor SGR). Matches the
# website's vendor tokens: Anthropic coral, OpenAI teal, Google blue, local
# violet. Terminals without truecolor degrade to the nearest available colour.
_VENDOR_SGR = {
    "anthropic": "38;2;217;119;87",   # Claude  #d97757
    "openai": "38;2;16;163;127",      # Codex   #10a37f
    "google": "38;2;66;133;244",      # Antigravity #4285f4
    "local": "38;2;168;85;247",       # local / open-weight #a855f7
}
# Same brand palette as RGB tuples, for the pixel-art style (half-block render).
_VENDOR_RGB = {
    "anthropic": (217, 119, 87),
    "openai": (16, 163, 127),
    "google": (66, 133, 244),
    "local": (168, 85, 247),
}
# Pixel-art scene palette (RGB). The room is drawn into a pixel buffer and folded
# to the terminal two rows at a time via the upper-half-block ▀ (fg = top pixel,
# bg = bottom pixel), so it needs a truecolor + unicode terminal.
_HALF = "▀"  # ▀
_PIX = {
    "bg": (16, 16, 20), "floor_a": (208, 170, 120), "floor_b": (196, 156, 108),
    "rug": (34, 36, 54), "table": (176, 110, 38), "table_edge": (150, 92, 28),
    "glow": (210, 210, 210), "skin": (226, 208, 182), "spk": (255, 255, 255),
}
_PHASES = (("review", "REVIEW"), ("debate", "DEBATE"), ("verify", "VERIFY"),
           ("synthesis", "DECISION"))

_GLYPHS = {"caret": "▲", "ok": "✓", "no": "✗", "dispute": "⚖", "play": "⏵",
           "idle": "•", "speak": "●", "table": "═"}
_ASCII_GLYPHS = {"caret": "^", "ok": "v", "no": "x", "dispute": "?", "play": ">",
                 "idle": ".", "speak": "*", "table": "="}
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
    """True when ``stream`` is a TTY wide enough for the deliberation scene."""
    try:
        if not stream.isatty():
            return False
    except Exception:  # noqa: BLE001
        return False
    return shutil.get_terminal_size((80, 24)).columns >= 60


def _unsafe_cell_char(ch: str) -> bool:
    """True for characters that must never appear in agent-influenced terminal
    cell content: C0 controls (incl. ESC), DEL, C1 controls, and the Unicode
    bidi/zero-width format characters used for text-spoofing (Trojan Source,
    CVE-2021-42574). Styling escapes arrive separately via the trusted ``sgr``
    argument, so any of these in the *content* is an injection/spoof attempt."""
    o = ord(ch)
    return (
        o < 0x20 or o == 0x7F or 0x80 <= o <= 0x9F          # C0 / DEL / C1
        or 0x200B <= o <= 0x200F or 0x202A <= o <= 0x202E   # zero-width / bidi
        or 0x2066 <= o <= 0x2069 or o == 0xFEFF             # bidi isolates / BOM
    )


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
            # Scrub control / bidi / zero-width characters from agent-influenced
            # cell content (finding claims, the verdict line). Styling arrives
            # separately via ``sgr`` (trusted), so any of these in the content is
            # a terminal-injection (cursor/clear/title) or text-spoof (bidi
            # override) attempt. Replace with a space to keep the layout width.
            if _unsafe_cell_char(ch):
                ch = " "
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


# Table geometry (rows on the fixed grid).
_TABLE_TOP = 8
_TABLE_BOT = 14
_SEAT_SLOT = 14   # min horizontal room per seat before we fall back to a roster

# Pixel-art band geometry (terminal rows occupied by the half-block scene).
_PIX_TOP = 5
_PIX_BOT = 17


class Courtroom:
    """Draws and animates the deliberation from the on_event stream.

    (Class name kept for back-compat; the scene is a round-table deliberation.)
    """

    def __init__(self, agents, chair: str, *, case: str = "", stream=None,
                 animate: bool = True, cols: int | None = None, rows: int = 30,
                 capture=None, mode: str = "code", decision: str = "chair",
                 unicode: bool = True, style: str = "flat"):
        # agents: list of (name, vendor); chair: the synthesizer (records the
        # decision in chair mode). mode: "code"/"issue". decision: "chair" or
        # "vote" (the panel tallies ballots) — different decision beat. style:
        # "flat" (ANSI line scene) or "pixel" (half-block pixel-art room).
        self.agents = list(agents)
        self.chair = chair
        self.case = case
        self.mode = mode
        self.decision = decision
        self.out = stream if stream is not None else sys.stdout
        self.animate = animate
        self.cols = cols or min(98, max(70, shutil.get_terminal_size((90, 30)).columns))
        self.rows = rows
        self._capture = capture
        self.unicode = unicode
        # Pixel-art needs the half-block glyph + truecolor; with unicode off it
        # transparently falls back to the flat line scene.
        self.pixel = (style == "pixel" and unicode)
        self.g = _GLYPHS if unicode else _ASCII_GLYPHS
        self.hr = "─" if unicode else "-"
        self.dot = "·" if unicode else "."
        self.screen = Screen(self.cols, self.rows)
        self.phase = None
        self.done_phases: set[str] = set()
        self.state: dict[str, str] = {a[0]: "idle" for a in self.agents}
        self.log: list[str] = []
        self.bubble: tuple[str, str] = ("", "")
        self.verifies: list = []
        self.verdict: str | None = None
        self.vote = None
        self.ballots: dict[str, str] = {}
        self.debate_seen = False
        self.max_round = 0
        self.disputes = 0
        self.start = time.monotonic()
        # Background ticker: repaint on an interval so the clock stays live and
        # the scene doesn't freeze between on_event callbacks (agents can run for
        # tens of seconds). Guarded by a lock shared with event-driven repaints.
        self.tick_interval = 1.0
        self._lock = threading.RLock()
        self._tick_stop: threading.Event | None = None
        self._tick_thread: threading.Thread | None = None

    # -- geometry --------------------------------------------------------
    def _split_seats(self):
        """Split jurors into the top edge and bottom edge of the table."""
        n = len(self.agents)
        top = self.agents[: (n + 1) // 2]
        bottom = self.agents[(n + 1) // 2:]
        return top, bottom

    def _slots(self, count: int):
        """Evenly spaced seat centre-x across the table's width."""
        if count <= 0:
            return []
        x0, x1 = 4, self.cols - 4
        span = x1 - x0
        return [x0 + span * (2 * i + 1) // (2 * count) for i in range(count)]

    def _seats_fit(self) -> bool:
        top, bottom = self._split_seats()
        widest = max(len(top), len(bottom), 1)
        return (self.cols - 8) // widest >= _SEAT_SLOT

    # -- painting --------------------------------------------------------
    def _paint(self) -> None:
        self.screen.clear()
        self._title()
        self._strip()
        if self.pixel and self._seats_fit():
            self._pixel_room()
        elif self._seats_fit():
            self._table_and_seats()
        else:
            self._roster()
        self._speaking_area()
        self._transcript()
        self._status()

    def _title(self) -> None:
        s = self.screen
        s.put(0, 1, f"{self.g['speak']} ai-jury - deliberation", "93;1")
        decided = "panel vote" if self.decision == "vote" else f"chair: {self.chair[:10]}"
        meta = f"{len(self.agents)} jurors {self.dot} {decided}"
        if self.case:
            meta = f"case: {self.case}  {self.dot}  " + meta
        s.put(0, 34, meta, "97")
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

    def _seat(self, x: int, name: str, vendor: str, *, facing: str) -> None:
        """Draw one juror seated at the table (facing 'down' = top edge, 'up' =
        bottom edge), with vendor-coloured nameplate + a chair/figure glyph."""
        s = self.screen
        st = self.state.get(name, "idle")
        hi = st in ("speaking", "arguing")
        vsgr = _VENDOR_SGR.get(vendor, "37") + (";1" if hi else "")
        figure = self.g["speak"] if hi else self.g["idle"]
        if st == "done":
            figure = self.g["ok"]
        elif st == "error":
            figure = "!"
        plate = f"{name[:10]}"
        if self.chair == name and self.decision != "vote":
            plate += "*"           # chair/moderator marker
        ballot = self.ballots.get(name)
        plate_x = x - len(plate) // 2
        fig = f"({figure})"
        fx = x - 1
        if facing == "down":      # top edge: nameplate above, figure toward table
            s.put(_TABLE_TOP - 3, plate_x, plate, vsgr)
            s.put(_TABLE_TOP - 2, fx, fig, "96;1" if hi else "2;37")
            if ballot:
                s.put(_TABLE_TOP - 4, x - len(ballot) // 2 - 1, f"[{ballot[:8]}]",
                      _banner_sgr(ballot))
            if hi:
                s.put(_TABLE_TOP - 1, x, self.g["caret"], "96")
        else:                      # bottom edge: figure toward table, nameplate below
            if hi:
                s.put(_TABLE_BOT, x, self.g["caret"], "96")
            s.put(_TABLE_BOT + 1, fx, fig, "96;1" if hi else "2;37")
            s.put(_TABLE_BOT + 2, plate_x, plate, vsgr)
            if ballot:
                s.put(_TABLE_BOT + 3, x - len(ballot) // 2 - 1, f"[{ballot[:8]}]",
                      _banner_sgr(ballot))

    def _table_and_seats(self) -> None:
        s = self.screen
        tx0, tx1 = 6, self.cols - 7
        # table border
        s.put(_TABLE_TOP, tx0, "." + self.hr * (tx1 - tx0 - 1) + ".", "33")
        for r in range(_TABLE_TOP + 1, _TABLE_BOT):
            s.put(r, tx0, "|", "33")
            s.put(r, tx1, "|", "33")
        s.put(_TABLE_BOT, tx0, "'" + self.hr * (tx1 - tx0 - 1) + "'", "33")
        self._table_interior()
        top, bottom = self._split_seats()
        for x, (name, vendor) in zip(self._slots(len(top)), top, strict=True):
            self._seat(x, name, vendor, facing="down")
        for x, (name, vendor) in zip(self._slots(len(bottom)), bottom, strict=True):
            self._seat(x, name, vendor, facing="up")

    def _table_interior(self) -> None:
        """What's 'on the table': the decision, the verify checklist, or a hint."""
        s = self.screen
        mid = (_TABLE_TOP + _TABLE_BOT) // 2
        if self.verdict:
            extra = ""
            if self.decision == "vote" and self.vote is not None:
                extra = "  (" + " · ".join(
                    f"{n} {lbl.lower()}" for lbl, n in self.vote.tally.items()
                ) + ")"
            label = ("DECISION by panel vote" if self.decision == "vote"
                     else "DECISION (chair)")
            s.center(mid - 1, label, "2;37")
            banner = f"  {self._fit(self.verdict + extra, self.cols - 18)}  "
            s.center(mid, banner, _banner_sgr(self.verdict))
            return
        if self.phase == "verify" and self.verifies:
            s.center(_TABLE_TOP + 1, "- verifying findings -", "97;1")
            for j, v in enumerate(self.verifies[:4]):
                mk, msg, sgr = self._verify_row(v)
                s.put(_TABLE_TOP + 2 + j, 9, f"{mk} {msg}", sgr)
            return
        s.center(mid, f"case: {self.case}" if self.case else "deliberating…", "2;37")

    def _roster(self) -> None:
        # Compact fallback (many jurors / narrow terminal): a wrapped row of
        # juror chips with state marks, no clipping.
        s = self.screen
        marks = {"speaking": self.g["caret"], "arguing": self.g["caret"],
                 "done": self.g["ok"], "error": "!"}
        s.put(_TABLE_TOP, 2, "JURY:", "2;37")
        row, x = _TABLE_TOP + 1, 4
        for name, vendor in self.agents:
            st = self.state.get(name, "idle")
            label = f"{name[:10]}{marks.get(st, self.dot)}"
            if x + len(label) + 2 > self.cols - 2:
                row += 1
                x = 4
            if row > _TABLE_BOT:
                break
            s.put(row, x, label, _VENDOR_SGR.get(vendor, "37")
                  + (";1" if st in ("speaking", "arguing") else ""))
            x += len(label) + 2
        self._table_interior()

    def _fit(self, text: str, width: int) -> str:
        """Truncate ``text`` to ``width`` columns with an ellipsis so a long
        verdict line never overflows the table / screen edge."""
        if width <= 0:
            return ""
        if len(text) <= width:
            return text
        ell = "…" if self.unicode else "..."
        return text[: max(0, width - len(ell))].rstrip() + ell

    def _verify_row(self, v):
        msg = f"{v.status:<18} {flatten_inline(v.claim)[:40]}"
        if v.status == "verified":
            return self.g["ok"], msg, "32;1"
        if v.status == "unsupported":
            return self.g["no"], msg, "2;31"
        return self.g["dispute"], msg, "33"

    # -- pixel-art scene (--theater-style pixel) -------------------------
    def _pix_slots(self, count: int, width: int):
        """Evenly spaced seat centre-x in pixel columns across the room width."""
        if count <= 0:
            return []
        x0, x1 = 9, width - 9
        span = x1 - x0
        return [x0 + span * (2 * i + 1) // (2 * count) for i in range(count)]

    def _pixel_room(self) -> None:
        """Draw the top-down room as pixel-art (half-block) and overlay labels."""
        pw, ph = self.cols, (_PIX_BOT - _PIX_TOP + 1) * 2
        px = [[_PIX["bg"]] * pw for _ in range(ph)]

        def rect(x0, y0, x1, y1, c):
            for y in range(max(0, y0), min(ph, y1 + 1)):
                rowp = px[y]
                for x in range(max(0, x0), min(pw, x1 + 1)):
                    rowp[x] = c

        for y in range(ph):                       # warm checkerboard floor
            for x in range(pw):
                px[y][x] = _PIX["floor_a"] if (x // 2 + y // 2) % 2 else _PIX["floor_b"]
        mx = 4
        rect(mx, 2, pw - 1 - mx, ph - 3, _PIX["rug"])
        tx0, ty0, tx1, ty1 = mx + 6, 8, pw - 1 - mx - 6, 17
        rect(tx0, ty0, tx1, ty1, _PIX["table"])
        rect(tx0, ty0, tx1, ty0 + 1, _PIX["table_edge"])
        cx, cy = (tx0 + tx1) // 2, (ty0 + ty1) // 2
        rect(cx - 6, cy - 1, cx + 6, cy + 1, _PIX["glow"])

        eye = (40, 40, 54)

        def figure(axc, heady, vendor, name):
            body = _VENDOR_RGB.get(vendor, (180, 180, 190))
            hair = tuple(int(c * 0.55) for c in body)        # darker vendor tint
            hi = self.state.get(name) in ("speaking", "arguing")
            rect(axc - 2, heady, axc + 2, heady + 2, _PIX["skin"])   # head (5×3)
            rect(axc - 2, heady, axc + 2, heady, hair)              # hair on top
            rect(axc - 1, heady + 1, axc - 1, heady + 1, eye)        # left eye
            rect(axc + 1, heady + 1, axc + 1, heady + 1, eye)        # right eye
            rect(axc - 2, heady + 3, axc + 2, heady + 5, body)      # torso (5×3)
            rect(axc - 3, heady + 3, axc - 3, heady + 4, body)      # left arm
            rect(axc + 3, heady + 3, axc + 3, heady + 4, body)      # right arm
            if hi:                                                  # speaking halo
                rect(axc - 3, heady - 1, axc + 3, heady - 1, _PIX["spk"])

        top, bottom = self._split_seats()
        txs, bxs = self._pix_slots(len(top), pw), self._pix_slots(len(bottom), pw)
        for axc, (name, vendor) in zip(txs, top, strict=True):
            figure(axc, 2, vendor, name)
        for axc, (name, vendor) in zip(bxs, bottom, strict=True):
            figure(axc, 18, vendor, name)

        self._blit_band(px)
        self._pixel_overlays(txs, bxs, top, bottom)

    def _blit_band(self, px) -> None:
        """Fold the pixel buffer into the screen, two rows per cell via ▀."""
        s = self.screen
        for i in range(_PIX_BOT - _PIX_TOP + 1):
            top_row, bot_row = px[2 * i], px[2 * i + 1]
            for c in range(self.cols):
                tr, tg, tb = top_row[c]
                br, bg, bb = bot_row[c]
                s.put(_PIX_TOP + i, c, _HALF, f"38;2;{tr};{tg};{tb};48;2;{br};{bg};{bb}")

    def _pixel_overlays(self, txs, bxs, top, bottom) -> None:
        """Names, ballots and the decision/verify text laid over the pixel band."""
        s = self.screen
        # name labels: top edge along the top of the band, bottom edge below it.
        # Top ballots sit in the gap above the band; the bottom edge has no spare
        # row (the speech band follows), so the panel tally on the table banner is
        # the per-juror vote record there.
        for x, (name, vendor) in zip(txs, top, strict=True):
            self._pix_label(x, name, vendor, _PIX_TOP, ballot_row=_PIX_TOP - 1)
        for x, (name, vendor) in zip(bxs, bottom, strict=True):
            self._pix_label(x, name, vendor, _PIX_BOT, ballot_row=None)

        mid = (_PIX_TOP + _PIX_BOT) // 2
        if self.verdict:
            extra = ""
            if self.decision == "vote" and self.vote is not None:
                extra = "  (" + " · ".join(
                    f"{n} {lbl.lower()}" for lbl, n in self.vote.tally.items()) + ")"
            label = ("DECISION by panel vote" if self.decision == "vote"
                     else "DECISION (chair)")
            s.center(mid - 1, f" {label} ", "97;1")
            s.center(mid, f"  {self._fit(self.verdict + extra, self.cols - 8)}  ",
                     _banner_sgr(self.verdict))
        elif self.phase == "verify" and self.verifies:
            s.center(mid - 1, " verifying findings ", "97;1")
            for j, v in enumerate(self.verifies[:3]):
                mk, msg, sgr = self._verify_row(v)
                s.center(mid + j, f"{mk} {msg}", sgr)
        elif self.case:
            s.center(mid, f" case: {self.case} ", "97;1")

    def _pix_label(self, x, name, vendor, row, *, ballot_row) -> None:
        st = self.state.get(name, "idle")
        plate = name[:10]
        if self.chair == name and self.decision != "vote":
            plate += "*"
        plate = f" {plate} "      # padding for the dark pill
        # Vendor-coloured, bold, on a dark pill so the name reads over the floor.
        speaking = st in ("speaking", "arguing")
        sgr = _VENDOR_SGR.get(vendor, "37") + ";48;2;22;22;32;1"
        if speaking:
            sgr = "30;47;1"       # invert (black on white) while speaking
        self.screen.put(row, max(0, x - len(plate) // 2), plate, sgr)
        ballot = self.ballots.get(name)
        if ballot and ballot_row is not None:
            chip = f"[{ballot[:8]}]"
            self.screen.put(ballot_row, max(0, x - len(chip) // 2), chip,
                            _banner_sgr(ballot))

    def _speaking_area(self) -> None:
        s = self.screen
        r0 = 18
        s.put(r0, 0, self.hr, "2;37")
        speaker, text = self.bubble
        if speaker and not self.verdict:
            s.put(r0, 2, f" {speaker} is speaking ", "96;1")
            wrapped = _wrap(text, self.cols - 12)[:3]
            width = max((len(w) for w in wrapped), default=0)
            s.put(r0 + 1, 6, "." + "-" * (width + 2) + ".", "97")
            for j, w in enumerate(wrapped):
                s.put(r0 + 2 + j, 6, f"( {w:<{width}} )", "39")
            s.put(r0 + 2 + len(wrapped), 6, "'" + "-" * (width + 2) + "'", "97")
        elif self.verdict:
            s.put(r0, 2, " the panel has decided ", "97;1")

    def _transcript(self) -> None:
        s = self.screen
        r0 = 23
        s.put(r0, 0, self.hr, "2;37")
        s.put(r0, 2, " TRANSCRIPT ", "2;37")
        for j, line in enumerate(self.log[-4:]):
            s.put(r0 + 1 + j, 2, flatten_inline(line)[: self.cols - 4], "37")

    def _status(self) -> None:
        s = self.screen
        msg = "deliberation closed" if self.verdict else (f"{self.phase or 'opening'}...")
        s.put(self.rows - 1, 2, f"{self.g['play']} {msg}", "96")

    # -- emit / animate --------------------------------------------------
    def _flush(self) -> None:
        frame = self.screen.to_ansi()
        if self._capture is not None:
            self._capture.append(frame)
        if self.animate:
            self.out.write("\033[H\033[J" + frame)
            self.out.flush()

    def _render(self) -> None:
        # Paint + flush as one critical section so an event-driven repaint and a
        # background tick never interleave writes (torn frames) on the terminal.
        with self._lock:
            self._paint()
            self._flush()

    def _beat(self, secs: float) -> None:
        if self.animate:
            time.sleep(secs)

    def _frame(self, beat: float = 0.0) -> None:
        self._render()
        self._beat(beat)

    def _tick_loop(self) -> None:
        # Repaint every ``tick_interval`` until stopped — keeps the clock live and
        # the scene from freezing while the orchestrator waits on the agents.
        assert self._tick_stop is not None
        while not self._tick_stop.wait(self.tick_interval):
            self._render()

    def _start_ticker(self) -> None:
        if not self.animate or self._tick_thread is not None:
            return
        self._tick_stop = threading.Event()
        self._tick_thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._tick_thread.start()

    def _stop_ticker(self) -> None:
        if self._tick_stop is not None:
            self._tick_stop.set()
        if self._tick_thread is not None:
            self._tick_thread.join(timeout=1.0)
            self._tick_thread = None

    # -- public API ------------------------------------------------------
    def open(self) -> None:
        if self.animate:
            self.out.write("\033[2J\033[?25l")
        self.phase = "review"
        self.log.append("the jury convenes")
        self._frame(0.4)
        self._start_ticker()

    def step(self, kind: str, result: AgentResult, round_no: int | None = None) -> None:
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
            self.log.append("no debate - the jurors agreed")
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
        note = f"the jury verifies: {ok}/{len(verdicts) or 0} upheld"
        if self.disputes:
            note += f" {self.dot} {self.disputes} disputed"
        self.log.append(note)
        self.bubble = ("", "")
        self._frame(0.6)

    def _synthesize(self, result):
        self.done_phases.update({"review", "debate", "verify"})
        if self.decision != "vote":
            self.verdict = _verdict_headline(result.output or "") if result.ok else "NO DECISION"
            self.log.append(f"DECISION -> {self.verdict}")
            self._frame(0.4)

    def set_vote(self, vote) -> None:
        """Provide the panel-vote result (decided after the run) for the finale."""
        self.vote = vote
        self.verdict = getattr(vote, "verdict", None)
        for b in getattr(vote, "ballots", []):
            self.ballots[b.reviewer] = b.vote

    def close(self) -> None:
        self._stop_ticker()
        self.done_phases.update(k for k, _ in _PHASES)
        if self.decision == "vote" and self.vote is not None:
            self.log.append(f"the panel votes -> {self.verdict}")
            self._frame(0.6)
        self._frame()
        if self.animate:
            self.out.write("\033[?25h\n")
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
