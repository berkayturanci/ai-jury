# Parameter reference

A complete reference of every `jury` parameter — CLI flags, subcommand flags, and
`jury.toml` keys — with allowed values and defaults.

**Precedence (highest wins):** CLI flag → `jury.toml` → built-in default. A flag
left unset falls through to the config; a config key left unset falls through to
the default shown below.

---

## CLI flags

### Input (choose one)

| Flag | Value | Description |
| --- | --- | --- |
| `--pr` | PR number or URL | Review a GitHub PR (uses `gh`). |
| `--repo` | `owner/name` | Repository for `--pr` (defaults to the current repo). |
| `--diff-file` | path, or `-` for stdin | Review a diff file (or piped stdin). |

### Rounds & depth

| Flag | Value | Default | Description |
| --- | --- | --- | --- |
| `--rounds` | integer ≥ 1 | `2` | Fixed rounds: `1` = review only, `2` = + debate. A fixed value disables early-stop (reproducible). |
| `--max-rounds` | integer ≥ 1 | = `rounds` | Ceiling on adaptive rounds when early-stop is on. |
| `--early-stop` / `--no-early-stop` | flag | from config (`false`) | Adaptive: stop after round 1 when reviewers agree; debate only on disagreement. |
| `--auto` / `--no-auto` | flag | from config (`false`) | Risk-aware auto-depth: scale rounds/verify to the diff profile. |
| `--verify` / `--no-verify` | flag | from config (`true`) | Run (or skip) the verification round. |
| `--chair` | agent name, or `rotate` | from config (`claude`) | Which agent synthesizes the verdict (and runs verification). Must be an enabled agent. |
| `--seed` | integer | from config (unset) | Reproducible orchestration; identical mock runs + seed ⇒ byte-identical reports. |

### Execution budget & reliability

| Flag | Value | Default | Description |
| --- | --- | --- | --- |
| `--total-timeout` | seconds | unset | Overall wall-clock budget for the whole run. |
| `--phase-timeout` | seconds | unset | Per-phase wall-clock budget. |
| `--retries` | integer ≥ 0 | `0` | Extra attempts for *transient* failures (timeout / rate-limit / spawn). |
| `--strict` | flag | off | Fail the run if any configured agent CLI is missing. |

### Large-diff handling

| Flag | Value | Default | Description |
| --- | --- | --- | --- |
| `--max-diff-bytes` | integer | `200000` | Size budget for the filtered diff before chunk/too-large. |
| `--chunk` / `--no-chunk` | flag | from config (`false`) | Chunk an over-budget diff by file instead of failing. |
| `--exclude` | path glob (repeatable) | from config (`[]`) | Exclude files matching this glob. |
| `--include` | path glob (repeatable) | from config (`[]`) | Only review files matching this glob. |

### Context & privacy

| Flag | Value | Default | Description |
| --- | --- | --- | --- |
| `--context-mode` | `diff-only` \| `expanded` | from config (`diff-only`) | `diff-only` sends only the diff; `expanded` adds PR context. |
| `--redact` / `--no-redact` | flag | from config (`true`) | Redact recognized secrets from prompt text before sending. |
| `--policy` | path | auto-discover `.jury/policy.toml` / `jury-policy.toml` | Optional repository review policy; missing files are allowed. |

### Output & format

| Flag | Value | Default | Description |
| --- | --- | --- | --- |
| `--format` | `markdown` \| `json` \| `sarif` | `markdown` | Output format for stdout/`--output`. |
| `--transcript` / `--no-transcript` | flag | from config | Render the full play-by-play transcript (each agent's review, the debate, and the chair's reasoning) instead of the consensus-first summary. `--no-transcript` forces the summary even if `[jury] transcript` is set. Markdown only; rendering-only (does not change the run or its cache key). |
| `--verbose` | flag | off | Summary report **and** the full transcript, in one document. Implies a transcript even with `--no-transcript`. |
| `--live` | flag | off | Stream each step (each review, each debate turn, verification, the decision) to stdout **as it happens**. Add **`--pr --post`** to also post each step as its own PR comment (posting is opt-in — bare `--pr` only selects the source). The live stream replaces the consolidated **markdown** stdout dump (`-o` still writes the full report to a file; `--format json`/`sarif` still print the document to stdout). In chunked large-diff mode the stream repeats once per chunk. |
| `-o`, `--output` | path | stdout | Write the report to a file. |
| `--metadata-json` | path | — | Write machine-readable run metadata (durations, status, rounds) as JSON. |
| `-q`, `--quiet` | flag | off | Suppress progress logs on stderr. |

`--transcript` / `--verbose` shape the **stdout / `--output` / single-comment**
report. Phased posting (`--post-mode phased`) always posts the per-round
sections regardless, since it is already a round-by-round layout.

### GitHub posting (require `--pr`)

| Flag | Value | Default | Description |
| --- | --- | --- | --- |
| `--post-summary`, `--post` | flag | off | Post the report as a single summary comment. |
| `--post-inline` | flag | off | Post inline review comments on located findings. |
| `--post-progress` | flag | off | Keep a live, sticky status comment updated per round/chunk. |
| `--post-mode` | `single` \| `phased` | `single` | With `--post-summary`: one comment, or separate Round 1 / debate / decision comments. |
| `--dry-run` | flag | off | With `--post-inline`, print the payload without calling GitHub. |
| `--label` | flag | off | Apply classification labels (review-effort / risk / security) to the PR. |

### CI gating

| Flag | Value | Default | Description |
| --- | --- | --- | --- |
| `--ci` | flag | off | CI mode: exit non-zero when blocking findings remain. |
| `--fail-on` | comma-separated severities | from config (`critical,major`) | Severities that fail CI. See [severities](#severities). |

### Result cache

| Flag | Value | Default | Description |
| --- | --- | --- | --- |
| `--cache` | flag | off | Reuse a cached outcome for an unchanged diff+config, else run and store. |
| `--cache-dir` | path | `$JURY_CACHE_DIR` or `~/.cache/ai-jury` | Override the cache directory. |
| `--clear-cache` | flag | — | Delete all cache entries and exit (alias: `jury cache clear`). |

### Suggested patches & incremental

| Flag | Value | Default | Description |
| --- | --- | --- | --- |
| `--suggest-patches` | flag | off | Emit an opt-in suggested-patches section for **verified** findings (read-only; never applied). |
| `--patches-out` | path | — | With `--suggest-patches`, write patches to this file instead of appending. |
| `--incremental` | flag | off | Review only the diff since the last jury run on `--pr` (falls back to full review). |

### Utility

| Flag | Value | Description |
| --- | --- | --- |
| `--config` | path | Path to `jury.toml` (default: `./jury.toml` or built-in). |
| `--config-validate` | flag | Validate the resolved config and exit (`0` valid, `2` invalid). |
| `--strict-config` | flag | Treat configuration warnings as errors. |
| `--mock` | flag | Offline demo using deterministic mock agents. |
| `--doctor` | flag | Print a local readiness diagnostics report and exit (no telemetry). |
| `--write` | path | With `--doctor`, also write diagnostics as JSON (secrets redacted). |
| `--version` | flag | Print the version and exit. |
| `-h`, `--help` | flag | Show help and exit. |

---

## Subcommands

### `jury init` — scaffold a `jury.toml`

| Flag | Value | Description |
| --- | --- | --- |
| `--preset` | `offline` \| `fast` \| `balanced` \| `thorough` | One-command setup (see [presets](#presets)). |
| `--agents` | `claude,codex,agy,qwen` | Comma-separated panel to scaffold. |
| `--rounds` | integer | Rounds for the scaffolded config. |
| `--chair` | agent name | Chair for the scaffolded config. |
| `--verify` / `--no-verify` | flag | Verification round on/off. |
| `--local-model` | model id | Model id for a local agent (e.g. `qwen2.5-coder:7b`). |
| `--local-endpoint` | URL | OpenAI-compatible base URL for a local agent. |
| `-o`, `--output` | path | Output path (default `jury.toml`). |
| `--force` | flag | Overwrite an existing file. |
| `--interactive` | flag | Force interactive prompts. |
| `--list-agents` | flag | List known agents + availability and exit. |
| `--list-models` | flag | List local models on the server and exit. |

### Other subcommands

| Command | Description |
| --- | --- |
| `jury config show` | Print the effective resolved config and its source. |
| `jury config path` | Print the resolved config path. |
| `jury cache clear` | Delete all local cache entries (alias of `--clear-cache`). |
| `jury comment --text "/jury review" --pr N` | Run from an allowlisted PR comment. Actions: `review`, `summary` (see [comment actions](#comment-actions)). Flags: `--print-args`, `--no-post`. |

---

## `jury.toml` reference

### `[jury]`

| Key | Type | Default | Allowed / notes |
| --- | --- | --- | --- |
| `rounds` | int | `2` | ≥ 1 (1 = review, 2 = + debate). |
| `chair` | string | `"claude"` | An enabled agent name, or `"rotate"`. |
| `timeout` | int | `600` | Positive seconds; per-agent wall-clock bound. |
| `parallel` | bool | `true` | Run agents concurrently. |
| `verify` | bool | `true` | Run the verification round. |
| `seed` | int | unset | Reproducible orchestration. |
| `anonymize_debate` | bool | `true` | Strip agent identity in round 2 (relabel Reviewer A/B/…) to curb bias. |
| `prefer_non_reviewer_chair` | bool | `false` | Prefer a chair that wasn't a round-1 reviewer (no effect when `chair = "rotate"`). |
| `total_timeout` | int | unset | Overall wall-clock budget (seconds). |
| `phase_timeout` | int | unset | Per-phase wall-clock budget (seconds). |
| `retries` | int | `0` | Extra attempts for transient failures. |
| `max_rounds` | int | = `rounds` | Round ceiling when `early_stop` is on. |
| `early_stop` | bool | `false` | Adaptive rounds. |
| `auto_depth` | bool | `false` | Risk-aware auto-depth (CLI `--auto`). |
| `transcript` | bool | `false` | Default the markdown report to the full play-by-play transcript (CLI `--transcript` / `--no-transcript`). Rendering-only — not part of the config hash or cache key. |

### `[jury.ci]`

| Key | Type | Default | Allowed / notes |
| --- | --- | --- | --- |
| `fail_on` | list[str] | `["critical", "major"]` | Severities that fail `--ci`. See [severities](#severities). |
| `ignore_unverified` | bool | `true` | Skip findings not confirmed by verification. |

### `[jury.context]`

| Key | Type | Default | Allowed / notes |
| --- | --- | --- | --- |
| `mode` | string | `"diff-only"` | `"diff-only"` or `"expanded"`. |
| `redact_secrets` | bool | `true` | Scrub recognized secrets before sending. |

### `[jury.diff]` (large-diff handling)

| Key | Type | Default | Allowed / notes |
| --- | --- | --- | --- |
| `max_bytes` | int | `200000` | Byte budget (after filtering) before chunk/too-large. |
| `chunk` | bool | `false` | Chunk an over-budget diff by file instead of failing. |
| `chunk_max_bytes` | int | = `max_bytes` | Per-chunk byte budget when chunking. |
| `exclude_generated` | bool | `true` | Drop binary + common generated/vendored files. |
| `exclude` | list[str] | `[]` | Path-glob deny list (e.g. `["docs/**", "*.lock"]`). |
| `include` | list[str] | `[]` | Path-glob allow list; when set, only matching files are reviewed. |

### `[[agent]]` (one table per reviewer; at least one required)

| Key | Type | Default | Allowed / notes |
| --- | --- | --- | --- |
| `name` | string | — | **Required**, unique, non-empty. |
| `vendor` | string | — | `anthropic` \| `openai` \| `google` \| `local` (unknown → generic fallback + warning). |
| `command` | string | — | Required CLI command (not required for `vendor = "local"`). |
| `model` | string | unset | Model identifier. |
| `endpoint` | string | `http://localhost:11434/v1` (local) | OpenAI-compatible base URL for `vendor = "local"`. |
| `timeout` | int | `600` | Positive seconds (inherits `jury.timeout`). |
| `enabled` | bool | `true` | Disabled agents are skipped. |
| `extra_args` | list[str] | `[]` | Extra CLI args (e.g. the secure-default sandbox flags). |

---

## Enumerations

### Severities
Ordered most → least severe: **`critical`**, **`major`**, **`minor`**, **`nit`**, **`info`**.
Alias: `blocker` → `critical`. Used by `--fail-on` / `[jury.ci] fail_on`.

### Vendors
`anthropic` · `openai` · `google` · `local` (OpenAI-compatible: Ollama, llama.cpp, vLLM, LM Studio).

### Presets (`jury init --preset`)
| Preset | Panel / depth |
| --- | --- |
| `offline` | Local-only ($0), no cloud CLIs. |
| `fast` | 1 round (review only). |
| `balanced` | Debate + early-stop. |
| `thorough` | All available agents + debate + verify. |

### Output formats
`markdown` (default) · `json` · `sarif`.

### Context modes
`diff-only` (default) · `expanded`.

### Post modes (`--post-mode`)
`single` (default, one comment) · `phased` (Round 1 / debate / decision as separate comments).

### Comment actions (`jury comment`)
`review` (full review) · `summary` (fast single-round pass).

---

## Environment variables

| Variable | Purpose |
| --- | --- |
| `JURY_CACHE_DIR` | Cache directory (default `~/.cache/ai-jury`); overridden by `--cache-dir`. |
| `JURY_LIVE` | `1` enables opt-in live native-CLI tests. |
| `JURY_LOCAL_LIVE` | `1` enables opt-in live local-model tests. |
| `JURY_BENCH_LIVE` | `1` runs the benchmark against live agents instead of recorded fixtures. |

---

_See also: [configuration.md](configuration.md) for prose explanations and the
[cookbook](cookbook.md) for task-oriented recipes._
