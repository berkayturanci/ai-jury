# Theater mode — courtroom view design (`--theater`)

`--theater` is an **opt-in, presentation-only** animated view of a *live* jury
run: each model sits in a seat, stands to speak as the run moves through its
phases, and the verdict is delivered by the chair (gavel) or the panel (vote
ballots). It consumes the orchestrator's `on_event` stream — the same per-phase
results that drive the report — so it always reflects the **real** run
(`--mock` drives the deterministic mock panel for a demo). It is a pure-stdlib
ANSI side channel: it never touches the structured outcome, the report, or the
CI gate, and on a non-interactive terminal it falls back to the plain `--live`
step stream.

Implementation: `src/ai_jury/theater.py` (`Screen` grid buffer + `Courtroom`
scene). Tests: `tests/test_theater.py`. Flag: `cli.py` (`--theater`).

## Screen bands (≈90×30, scales 70–98 wide)

```
row 0   TITLE     ⚖  ai-jury   case: PR #142  ·  3 jurors · chair
row 2   PHASE     > REVIEW -- · DEBATE·r2 -- · VERIFY -- · VERDICT   00:14
row 5–8 BENCH     THE HON. CHAIR / [ codex ]   (gavel /\ → [##] on verdict)
row 10–14 SEATS   N avatars in seats, vendor-coloured nameplates
row 16–21 STAGE   NOW SPEAKING bubble · or VERIFY checklist · or VERDICT banner
row 23–28 TRANSCRIPT  last ~4 event log lines
row 29  STATUS    ⏵ <phase> … / court adjourned
```

## Seat sprite (state-driven)

```
( -- )      idle / seated          ( o o )   speaking (review)
 |name |                           /|name|\   stands, arms up, ▲ caret
_|######|_  desk                   ( ^ ^ )   arguing (debate)
[VENDOR]    accent tag             ( - - )✓  done      ( x x )!  errored
```

Vendor accent colours: anthropic = yellow, openai = green, google = blue,
local = magenta.

## Phase → animation beat (driven by `on_event`)

| event (kind) | scene change |
|---|---|
| `review`   | speaker stands, speech bubble shows their top finding (severity-coloured); sits back. |
| `debate`   | speaker "argues"; phase strip shows `DEBATE·rN`. If review→verify with no debate, debate is marked *skipped — converged*. |
| `verify`   | bench brightens ("chair takes over"); a checklist ticks each verdict ✓ verified / ✗ unsupported / ⚖ disputed. |
| `synthesis`| **chair mode:** gavel strike + `ORDER IN THE COURT` + verdict banner. |
| (after run)| **vote mode:** each seat shows its ballot chip; bench reads `THE PANEL`; banner shows the verdict + tally. |

## Full-package coverage

- **PR vs issue:** title case label (`PR #N` / `issue #N`); `mode` switches the
  rubric context; the verdict banner is vocabulary-agnostic (colours map
  REQUEST CHANGES / NEEDS-INFO → red, APPROVE / READY → green, COMMENT / UNCLEAR
  → yellow).
- **chair vs vote:** different finale (gavel vs ballots+tally), different bench.
- **flow:** debate round number; early-stop (no debate → "converged"); disputed
  findings counted in the verify note.

## Degradation

- Not a TTY / width < 60 → fall back to the plain `--live` step stream.
- `unicode=False` → ASCII glyph set (`[J]`, `^`, `v`, `x`, `-`, `.`), no
  box/▲/✓ chars. (Agent *content* may still carry Unicode; only the chrome is
  forced ASCII.)
- Seats distribute evenly for 2–5 agents (computed gutter).

A richer **isometric pixel-art** style (`docs/theater-pixel-design.md`) is a
possible future `--theater` look; the flat scene above is the shipping default.
