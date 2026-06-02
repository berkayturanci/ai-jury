# `jury.toml` configuration reference

The jury reads `./jury.toml` by default (override with `--config PATH`).
If the file is absent, built-in defaults are used.

> **Tip:** don't hand-write it the first time — run **`jury init`** to scaffold a
> valid config from your installed agents (and discovered local models). See
> [the README](../README.md#configuration--jurytoml) for the `init` flags.

## Validation

Check a config without running a review:

```
jury --config-validate --config jury.toml
```

Exit codes: `0` valid (warnings printed if any), `2` invalid. During a normal
run the config is validated too; pass `--strict-config` to turn warnings into
hard errors (exit `2`).

- **Hard errors** (always fail): `rounds < 1`, non-positive `timeout` (jury
  or per-agent), duplicate agent names, missing/empty agent `name` or
  `command`, no `[[agent]]` entries at all, malformed tables.
- **Warnings** (fail only under `--strict-config`): unknown vendor, `chair`
  not matching an enabled agent, unknown top-level/section/agent keys.

## `[jury]`

| Field      | Type   | Default    | Allowed / notes                                   |
| ---------- | ------ | ---------- | ------------------------------------------------- |
| `rounds`   | int    | `2`        | Integer `>= 1` (1 = review only, 2 = + debate).   |
| `chair`    | string | `"claude"` | Name of an enabled agent (else fallback warning). |
| `timeout`  | int    | `600`      | Positive seconds; per-agent wall-clock bound.     |
| `parallel` | bool   | `true`     | Run agents concurrently.                          |
| `verify`   | bool   | `true`     | Run the verification round.                       |
| `total_timeout` | int | unset   | Optional overall wall-clock budget (seconds) for the whole run. |
| `phase_timeout` | int | unset   | Optional per-phase wall-clock budget (seconds).   |
| `retries`  | int    | `0`        | Extra attempts for *transient* failures (timeout / rate-limit / spawn). |
| `max_rounds` | int  | = `rounds` | Round ceiling when `early_stop` is on (issue #40). |
| `early_stop` | bool | `false`    | Adaptive rounds: skip debate when reviewers agree, else debate up to `max_rounds`. |

### Execution controls (issue #30)

`total_timeout` and `phase_timeout` bound how long a run may take. The effective
per-agent-call timeout is the **minimum** of the agent's own `timeout`, the
phase budget, and the time remaining in the total budget — whichever is
smallest. When the total budget is exhausted, the remaining phases
(debate/verify/synthesis) are skipped and the report states this; the run still
returns what completed (partial-result policy). `retries` is opt-in and bounded:
only failures classed as transient are retried, and a retry never overruns the
total budget. `Ctrl-C` cancels cleanly (exit code `130`) instead of dumping a
traceback.

The same controls have CLI overrides: `--total-timeout`, `--phase-timeout`,
`--retries`.

### Adaptive rounds (issue #40)

With `early_stop = true`, the orchestrator decides whether to spend a debate
round from the round-1 convergence signal instead of always running a fixed
number of rounds: a **unanimous** panel stops after round 1, while
**disagreement** runs debate up to `max_rounds`, stopping as soon as a round
resolves all disputes. The chosen rounds and the reason are recorded in the run
metadata and the report's "Run metadata" section. A fixed `--rounds` (or
`rounds` with `early_stop = false`) keeps reproducible fixed-N behaviour for
benchmarking. CLI overrides: `--early-stop` / `--no-early-stop`, `--max-rounds`.

### `[jury.ci]`

| Field               | Type      | Default                  | Notes                                  |
| ------------------- | --------- | ------------------------ | -------------------------------------- |
| `fail_on`           | list[str] | `["critical", "major"]`  | Severities that fail CI mode.          |
| `ignore_unverified` | bool      | `true`                   | Skip findings not confirmed by verify. |

### `[jury.context]`

| Field            | Type   | Default       | Notes                                          |
| ---------------- | ------ | ------------- | ---------------------------------------------- |
| `mode`           | string | `"diff-only"` | `"diff-only"` or `"expanded"`.                 |
| `redact_secrets` | bool   | `true`        | Scrub recognized secrets before sending.       |

### `[jury.diff]` (large-diff handling, issue #31)

| Field              | Type      | Default    | Notes                                                            |
| ------------------ | --------- | ---------- | ---------------------------------------------------------------- |
| `max_bytes`        | int       | `200000`   | Budget (UTF-8 bytes, after filtering) before chunk/too-large.    |
| `chunk`            | bool      | `false`    | Chunk an over-budget diff by file instead of failing.            |
| `chunk_max_bytes`  | int       | = `max_bytes` | Per-chunk byte budget when chunking.                          |
| `exclude_generated`| bool      | `true`     | Drop binary and common generated/vendored files.                 |
| `exclude`          | list[str] | `[]`       | Extra path-glob deny list (e.g. `["docs/*", "*.lock"]`).         |
| `include`          | list[str] | `[]`       | Path-glob allow list; when set, only matching files are reviewed.|

Before running, the diff is measured and filtered. The CLI logs the total and
post-filter size, the kept/excluded file counts, and the selected handling mode
(`full`, `chunked`, or `too_large`). A `too_large` diff (over budget with
`chunk = false`) fails with an actionable message (exit `2`); enable chunking or
narrow the diff with `include`/`exclude`. In `chunked` mode each chunk is
reviewed and the findings are merged into one report. CLI overrides:
`--max-diff-bytes`, `--chunk` / `--no-chunk`, `--exclude GLOB` (repeatable),
`--include GLOB` (repeatable).

## Local result cache (issue #33)

Re-running the jury on an unchanged diff with an unchanged config wastes time
and tokens. The cache is **off by default**; enable it with `--cache`:

```
jury --pr 123 --cache      # reuse a cached outcome, or run + store on a miss
jury cache clear           # delete all cache entries (alias: --clear-cache)
```

The cache key covers the diff, the effective config hash, the prompt-template
version, the package version, the context policy, and the seed — so any change
to those is a miss (automatic invalidation). A cache hit is marked in the
progress log and in the report's "Run metadata" section (`served from local
cache`).

**Privacy.** A cache entry stores the full structured outcome, including agent
review/debate/synthesis text derived from the diff. Treat the cache directory as
sensitive — same trust level as the diff. It defaults to
`$JURY_CACHE_DIR` or `~/.cache/ai-jury` (override with
`--cache-dir`).

## `[[agent]]`

At least one entry is required.

| Field        | Type      | Default | Allowed / notes                                           |
| ------------ | --------- | ------- | -------------------------------------------------------- |
| `name`       | string    | —       | Required, non-empty, unique.                             |
| `vendor`     | string    | —       | `anthropic`, `openai`, `google`; unknown → fallback warn. |
| `command`    | string    | —       | Required, non-empty CLI command.                         |
| `model`      | string    | unset   | Optional model identifier.                               |
| `timeout`    | int       | `600`   | Positive seconds (inherits `jury.timeout`).           |
| `enabled`    | bool      | `true`  | Disabled agents are skipped.                             |
| `extra_args` | list[str] | `[]`    | Extra CLI args passed to the agent command.              |
