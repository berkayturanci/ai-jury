# `jury.toml` — behaviour & validation

The jury reads `./jury.toml` by default (override with `--config PATH`); built-in
defaults apply if the file is absent. Don't hand-write it the first time — run
**`jury init`** to scaffold a valid config from your installed agents (and
discovered local models).

> **Every field — type, default, allowed values** — for `[jury]`, `[jury.ci]`,
> `[jury.context]`, `[jury.diff]` and `[[agent]]`, plus all CLI flags, lives in the
> single [**parameter reference**](parameters.md). This page explains the
> *behaviour* behind those keys: validation, execution budgets, adaptive rounds,
> large-diff handling, and the result cache.

## Validation

Check a config without running a review:

```
jury --config-validate --config jury.toml
```

Exit codes: `0` valid (warnings printed if any), `2` invalid. During a normal run
the config is validated too; pass `--strict-config` to turn warnings into hard
errors (exit `2`).

- **Hard errors** (always fail): `rounds < 1`, non-positive `timeout` (jury or
  per-agent), duplicate agent names, missing/empty agent `name` or `command`, no
  `[[agent]]` entries at all, `decision` other than `"chair"` / `"vote"`,
  malformed tables.
- **Warnings** (fail only under `--strict-config`): unknown vendor, `chair` not
  matching an enabled agent, unknown top-level/section/agent keys.

## Execution budget (`total_timeout` / `phase_timeout` / `retries`)

`total_timeout` and `phase_timeout` bound how long a run may take. The effective
per-agent-call timeout is the **minimum** of the agent's own `timeout`, the phase
budget, and the time remaining in the total budget — whichever is smallest. When
the total budget is exhausted, the remaining phases (debate/verify/synthesis) are
skipped and the report states this; the run still returns what completed
(partial-result policy). `retries` is opt-in and bounded: only failures classed as
transient (timeout / rate-limit / spawn) are retried, and a retry never overruns
the total budget. `Ctrl-C` cancels cleanly (exit code `130`) instead of dumping a
traceback. CLI overrides: `--total-timeout`, `--phase-timeout`, `--retries`.

## Adaptive rounds (`early_stop` / `max_rounds`)

With `early_stop = true`, the orchestrator decides whether to spend a debate round
from the round-1 convergence signal instead of always running a fixed number of
rounds: a **unanimous** panel stops after round 1, while **disagreement** runs
debate up to `max_rounds`, stopping as soon as a round resolves all disputes. The
chosen rounds and the reason are recorded in the run metadata. A fixed `--rounds`
(or `rounds` with `early_stop = false`) keeps reproducible fixed-N behaviour for
benchmarking. Risk-aware `auto_depth` (`--auto`) instead derives depth from a
pre-review diff profile. CLI overrides: `--early-stop` / `--no-early-stop`,
`--max-rounds`, `--auto`.

## Verdict source (`decision` = `chair` | `vote`)

By default (`decision = "chair"`) the chair synthesizes the final verdict. With
`decision = "vote"` (CLI `--decision vote`) the verdict is a **panel tally**: each
reviewer votes from the worst finding they raised, majority wins, and ties resolve
to the **stricter** stance; the chair's synthesis is kept as supporting reasoning.
The tally is **mode-aware** — a PR/diff votes `REQUEST CHANGES > COMMENT > APPROVE`,
an `--issue` votes `NEEDS-INFO > UNCLEAR > READY`. Voting is **rendering-only**: it
does not change orchestration, the config hash / cache key, or the severity-based
`--ci` gate, which stays the independent hard safety check.

## Large-diff handling (`[jury.diff]`)

Before running, the diff is measured and filtered. The CLI logs the total and
post-filter size, the kept/excluded file counts, and the selected handling mode
(`full`, `chunked`, or `too_large`). A `too_large` diff (over budget with
`chunk = false`) fails with an actionable message (exit `2`); enable chunking or
narrow the diff with `include`/`exclude`. In `chunked` mode each chunk is reviewed
and the findings are merged into one report. CLI overrides: `--max-diff-bytes`,
`--chunk` / `--no-chunk`, `--exclude GLOB` (repeatable), `--include GLOB`
(repeatable).

## Local result cache (`--cache`)

Re-running the jury on an unchanged diff with an unchanged config wastes time and
tokens. The cache is **off by default**; enable it with `--cache`:

```
jury --pr 123 --cache      # reuse a cached outcome, or run + store on a miss
jury cache clear           # delete all cache entries (alias: --clear-cache)
```

The cache key covers the diff, the effective config hash, the prompt-template
version, the package version, the context policy, and the seed — so any change to
those is a miss (automatic invalidation). A cache hit is marked in the progress
log and in the report's "Run metadata" section (`served from local cache`).

**Privacy.** A cache entry stores the full structured outcome, including agent
review/debate/synthesis text derived from the diff. Treat the cache directory as
sensitive — the same trust level as the diff. It defaults to `$JURY_CACHE_DIR` or
`~/.cache/ai-jury` (override with `--cache-dir`).
