# Theater mode — deliberation view design (`--theater`)

`--theater` is an **opt-in, presentation-only** animated view of a *live* jury
run: the models sit around a table and take turns speaking as the run moves
through its phases, then reach a decision **together** — by panel vote, or
recorded by the chair (the synthesizer). There is **no judge**; the jurors
deliberate with each other. It consumes the orchestrator's `on_event` stream —
the same per-phase results that drive the report — so it always reflects the
**real** run (`--mock` drives the deterministic mock panel for a demo). It is a
pure-stdlib ANSI side channel: it never touches the structured outcome, the
report, or the CI gate, and on a non-interactive terminal it falls back to the
plain `--live` step stream.

Implementation: `src/ai_jury/theater.py` (`Screen` grid buffer + `Courtroom`
scene — class name kept for back-compat). Tests: `tests/test_theater.py`. Flag:
`cli.py` (`--theater`, `--theater-style`).

## Scene styles (`--theater-style {flat,pixel}`)

The same deliberation, the same `on_event` flow, two render styles:

- **`flat`** (default) — the ANSI line scene: a drawn table with vendor-coloured
  nameplates and figure glyphs, a speech bubble, the verify checklist, and the
  decision banner. The chrome uses the terminal's **default foreground**
  (bold/faint), so it stays readable on both **light and dark** backgrounds.
- **`pixel`** — a top-down **pixel-art room** drawn into an RGB pixel buffer and
  folded to the terminal two rows per cell via the upper-half-block `▀`
  (foreground = top pixel, background = bottom pixel). Little chibi jurors
  (hair + eyes + vendor-coloured torso) sit around a wooden table on a warm
  checkerboard floor; the speaker gets a bright halo and an inverted nameplate;
  the table shows the case / verify checklist / decision banner. The per-juror
  vote chips show on the top edge; the full tally is on the banner. It needs a
  **truecolor + unicode** terminal; without either it transparently falls back
  to the `flat` scene (and, like `flat`, to the plain `--live` stream off a TTY
  or when too many jurors won't fit, where it shows the compact roster).

## Screen bands (≈90×30, scales 70–98 wide)

```
row 0   TITLE      ● ai-jury - deliberation   case: PR #142 · 4 jurors · panel vote
row 2   PHASE      > REVIEW -- · DEBATE·r2 -- · VERIFY -- · DECISION        00:14
row 5–6 TOP SEATS  jurors seated along the table's top edge (facing in)
row 8–14 TABLE     the round table — shows "what's on the table":
                     · the shared case, or
                     · the verify checklist (✓ verified / ✗ unsupported / ⚖ disputed), or
                     · the DECISION banner (vote tally or chair's verdict)
row 15–16 BOTTOM SEATS  jurors along the bottom edge (facing in)
row 18–21 SPEECH   the current speaker's bubble (their top finding / argument)
row 23–27 TRANSCRIPT   last ~4 event log lines
row 29  STATUS     ⏵ <phase> … / deliberation closed
```

Jurors are split across the top and bottom edges of the table and spaced evenly
(`_slots`). Each seat is a vendor-coloured nameplate + a chair/figure glyph; the
chair's plate is marked `*` (chair mode). The active speaker's figure
brightens with a `▲` caret and their bubble opens in the speech band.

## Phase → animation beat (driven by `on_event`)

| event (kind) | scene change |
|---|---|
| `review`   | the speaker's figure lights up; their top finding (severity-coloured) types into the bubble; then they settle. |
| `debate`   | speaker "argues"; the phase strip shows `DEBATE·rN`. If review→verify with no debate, debate is marked *skipped — the jurors agreed*. |
| `verify`   | the table shows a checklist ticking each verdict ✓ verified / ✗ unsupported / ⚖ disputed. |
| `synthesis`| **chair mode:** the decision lands on the table — `DECISION (chair)` + the verdict banner. |
| (after run)| **vote mode:** each seat shows its ballot chip; the table reads `DECISION by panel vote` + the verdict and tally. |

## Full-package coverage

- **PR vs issue:** title case label (`PR #N` / `issue #N`); the verdict banner is
  vocabulary-agnostic (REQUEST CHANGES / NEEDS-INFO → red, APPROVE / READY →
  green, COMMENT / UNCLEAR → yellow).
- **chair vs vote:** chair mode marks the chair seat and records the decision;
  vote mode shows ballots on each seat + the tally.
- **flow:** debate round number; early-stop ("the jurors agreed"); disputed
  findings counted in the verify note.

## Many jurors / degradation

- Jurors seat around the table while there's room (computed from terminal width).
- When they would not fit (many jurors and/or a narrow terminal), the scene
  falls back to a compact **`JURY:` roster** of vendor-coloured chips with state
  marks (`▲` speaking / `✓` done / `!` error) — no clipping.
- Not a TTY / width < 60 → fall back to the plain `--live` step stream.
- `unicode=False` → ASCII glyph set (the chrome is forced ASCII; agent *content*
  may still carry Unicode).

## Liveness, banner & safety

- **Live clock.** Events can be tens of seconds apart (agents running), so a
  background ticker repaints on an interval (default 1s) under a shared lock —
  the clock ticks smoothly and the scene never freezes between phases.
- **Readable verdict.** The decision banner **wraps the full verdict** over up to
  three lines on the table (an ellipsis only if it still overflows), instead of
  truncating it to one line. The rolling transcript logs the short verdict
  keyword (`DECISION -> NEEDS-INFO`); long transcript lines are ellipsised, not
  hard-cut.
- **Light & dark.** Chrome uses the terminal default foreground (see above); the
  pixel scene paints its own background, and vendor/verdict colours have contrast
  on both themes.
- **Safety.** All agent-influenced text (claims, the verdict line) is rendered
  through `Screen.put`, which scrubs control / DEL / C1 and bidi / zero-width
  characters, so a crafted finding can't inject ANSI escapes (banner/cursor
  spoofing) or Trojan-Source text into the terminal. See
  `docs/security-audit-2026-06-14-theater.md`.
