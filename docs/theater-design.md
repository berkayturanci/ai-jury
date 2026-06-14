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
`cli.py` (`--theater`).

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
