# Security audit — theater rendering (2026-06-14)

Scope: the new attack surface added since the last clean audit (v1.6.2) — the
`--theater` animated scene (`theater.py`, v1.7.0–v1.8.1), its pixel-art style and
`jury.toml` defaults (`config.py`, #364), and the website demo animation (#365).
Threat model (unchanged): reviewers process **attacker-controlled diffs**, so any
agent output is treated as attacker-influenced.

## Round 1 — finding (Medium→High): terminal escape-sequence injection

**`theater.py` wrote agent-influenced text to the terminal without scrubbing
control characters.** A finding `claim` / verdict line / transcript entry flows
from the diff → an agent → `parse_findings` → the scene. `flatten_inline`
collapses whitespace but does **not** remove `ESC` (`\x1b`) or other C0/C1
controls (`" ".join(text.split())` leaves `\x1b` intact). Those bytes were placed
into `Screen` cells and emitted verbatim by `to_ansi()`.

Confirmed: a claim of `"\x1b[2J\x1b[H FAKE APPROVE \x1b[5m"` rendered the raw
`\x1b[2J` (clear screen) and `\x1b[5m` (blink) into the frame. Impact on the
reviewer's terminal: clear/scroll, cursor moves to **overwrite the verdict
banner** (e.g. spoof REQUEST CHANGES → APPROVE), set the window title, and other
escape-driven mischief. Opt-in + TTY-only, but squarely in the threat model.

## Round 2 — hardening: bidi / zero-width text spoofing

The same sink is exposed to **Unicode bidi-override / zero-width** spoofing
(Trojan Source, CVE-2021-42574): an attacker claim can reorder or hide displayed
text (e.g. flip the meaning of a finding) using `U+202E` etc. Neither
`flatten_inline` nor the Round-1 control scrub removed these.

### Fix (one chokepoint)

`Screen.put` — the single rendering sink — now scrubs every unsafe character from
cell **content**, replacing it with a space (preserving layout width):

- C0 controls incl. `ESC`, `DEL` (`0x7F`), C1 controls (`0x80–0x9F`);
- zero-width + bidi format chars: `U+200B–200F`, `U+202A–202E`, `U+2066–2069`,
  `U+FEFF`.

Styling escapes are unaffected — they arrive separately via the trusted `sgr`
argument (vendor colours, banner SGR, truecolor half-block), never from content.
All stdout writes go through `to_ansi()` (scrubbed) or fixed, trusted control
strings (`\x1b[2J`, cursor hide/show), so no agent content bypasses the scrub.

Regression test: `tests/test_theater.py::ScreenTest::test_put_scrubs_control_chars`.

## Round 3 — verification (clean)

Re-audited the full new surface; no further findings:

- **theater.py** — every agent-derived string (bubble, transcript, verify rows,
  verdict banner, gist/headline, names, case) routes through `Screen.put`, now
  scrubbed. `sgr` values are all renderer-computed literals/ints; `_banner_sgr`
  returns fixed SGR by keyword match without embedding the verdict. The pixel
  band's SGR is `38;2;r;g;b;48;2;…` from the trusted `_VENDOR_RGB`/`_PIX` palette.
- **config.py (#364)** — `theater` is bool-validated; `theater_style` is
  enum-validated to `{flat,pixel}` and lower-cased in `_from_dict`; any other
  value falls back to `flat`. Rendering-only — excluded from `config_hash`/cache.
- **cli.py** — `theater_on`/`theater_style` resolution and the best-effort chair
  display name carry no attacker input into a sink.
- **website demo (#365)** — dynamic text uses `textContent` (no HTML injection);
  `innerHTML` fragments use `esc()` + the trusted `LABEL`/phase constants;
  `verdictClass` returns fixed class names. No XSS. Scripted data only.

Result: **no Critical/High/Medium remaining.** Shipped as the v1.9.1 security
patch.
