# Parameter reference

A complete reference of every `jury` parameter — CLI flags, subcommand flags, and
`jury.toml` keys — with allowed values and defaults.

**Precedence (highest wins):** CLI flag → `jury.toml` → built-in default. A flag
left unset falls through to the config; a config key left unset falls through to
the default shown below.

---

## Common recipes

Copy-pasteable commands for the everyday jobs. Each line says what it does.

```bash
# Scaffold a jury.toml with a guided, skippable wizard
jury init --wizard
#   → numbered questions for the most-used settings (reviewers, depth, chair vs.
#     vote, verify, context, CI gate); Enter keeps each default. Plain `jury init`
#     (or `--preset`) still works for a one-shot scaffold.

# Review a GitHub PR (uses `gh` to fetch the diff)
jury --pr 123
#   → run the panel on PR #123 and print the verdict to stdout

# Review a PR and post the verdict back as one summary comment
jury --pr 123 --post
#   → same review, plus a single summary comment on the PR (`--post` ⇔ `--post-summary`)

# Review a GitHub issue for completeness/clarity (not code — the issue's prose)
jury --issue 42
#   → run the full jury with an issue-quality rubric (repro, expected/actual,
#     scope, missing context); verdict is READY / NEEDS-INFO / UNCLEAR

# Review an issue and post the verdict back as a comment on that issue
jury --issue 42 --post
#   → posts via `gh issue comment` (PR-only flags like --post-inline are rejected)

# Decide an issue by panel vote instead of a single chair
jury --issue 42 --decision vote
#   → each reviewer votes; tally is NEEDS-INFO > UNCLEAR > READY, ties go stricter

# Stream an issue review live AND mirror each step to the issue thread
jury --issue 42 --live --post
#   → terminal play-by-play, with each step posted to the issue as it lands

# Review the current branch against the base, from a piped diff
git diff origin/HEAD... | jury --diff-file -
#   → review your local commits without touching GitHub (`-` reads stdin)

# Single round — review only, no debate (fast, cheap)
jury --pr 123 --rounds 1
#   → each agent reviews once; skips the debate round (a fixed value disables early-stop)

# Offline demo with deterministic mock agents (no CLIs, no network)
jury --mock --diff-file examples/sample.diff
#   → exercise the full pipeline locally; byte-identical output every run

# CI gate — fail the build on blocking findings
jury --pr 123 --ci --fail-on critical,major
#   → exit non-zero if any confirmed critical/major finding remains

# Full play-by-play transcript instead of the consensus-first summary
jury --pr 123 --transcript
#   → render every agent's review, the debate, and the chair's reasoning

# Summary report followed by the full transcript, in one document
jury --pr 123 --verbose
#   → consensus summary first, then the chronological transcript below it

# Live, streamed play-by-play to the terminal as each step lands
jury --pr 123 --live
#   → print each review/debate turn/verdict the moment it completes

# Live AND post each step as its own PR comment (posting is opt-in)
jury --pr 123 --live --post
#   → stream locally and mirror each step to the PR as a separate comment

# Incremental — review only what changed since the last jury run on the PR
jury --pr 123 --incremental --post
#   → narrow to the new range when a prior marker exists, else full review

# Suggested patches for verified findings (read-only; written to a file)
jury --pr 123 --suggest-patches --patches-out fixes.patch
#   → emit an opt-in patch section for VERIFIED findings; never auto-applied

# Machine-readable output for tooling
jury --pr 123 --format json -o review.json     # JSON document to a file
jury --pr 123 --format sarif -o review.sarif   # SARIF for code-scanning upload

# Phased posting — Round 1 / debate / decision as separate comments
jury --pr 123 --post --post-mode phased
#   → post the flow as readable, round-by-round comments (requires `--post`)
```

---

## CLI flags

### Input (choose one)

Exactly one source. Giving two exits non-zero and names both — a silent precedence
order would make it impossible to tell which one was actually reviewed.

`--commit`/`--commits` resolve to a unified diff locally and then flow through the
same pipeline as any other diff: large-diff filtering, redaction, rounds, verify and
the report all apply unchanged. A revision may not begin with `-` (git would read it
as an option) and an empty resolved diff is an error rather than a review of nothing.

| Flag | Value | Description |
| --- | --- | --- |
| `--pr` | PR number or URL | Review a GitHub PR (uses `gh`). |
| `--issue` | issue number or URL | Review a GitHub **issue** for completeness/clarity (uses `gh`). Runs the full jury with an issue-quality rubric (repro, expected/actual, scope, missing context); the verdict vocabulary is READY / NEEDS-INFO / UNCLEAR. |
| `--repo` | `owner/name` | Repository for `--pr`/`--issue` (defaults to the current repo). |
| `--diff-file` | path, or `-` for stdin | Review a diff file (or piped stdin). |
| `--commit` | revision | Review the diff one commit introduces. Needs a git repo; no `gh`. |
| `--commits` | range | Review a commit range, e.g. `origin/main..HEAD` or `HEAD~5..HEAD`. |

Exactly one input source is required. `--repo` modifies `--pr`/`--issue`; all
posting flags (`--post-summary`/`--post`, `--post-inline`, `--post-progress`,
`--label`) and `--incremental` require `--pr`. With `--issue`, `--post`/`--post-summary`
posts the verdict back via `gh issue comment`; the PR/diff-only flags
(`--post-inline`, `--post-progress`, `--label`, `--incremental`) are rejected.

**Example:** `jury --pr 123 --repo octocat/hello` reviews PR #123 in
`octocat/hello`; `git diff origin/HEAD... | jury --diff-file -` reviews the
current branch from stdin.

### Rounds & depth

| Flag | Value | Default | Description |
| --- | --- | --- | --- |
| `--rounds` | integer ≥ 1 | `2` | Fixed rounds: `1` = review only, `2` = + debate. A fixed value disables early-stop (reproducible). |
| `--max-rounds` | integer ≥ 1 | = `rounds` | Ceiling on adaptive rounds when early-stop is on. |
| `--early-stop` / `--no-early-stop` | flag | from config (`false`) | Adaptive: stop after round 1 when reviewers agree; debate only on disagreement. |
| `--auto` / `--no-auto` | flag | from config (`false`) | Risk-aware auto-depth: scale rounds/verify to the diff profile. |
| `--tiered` | flag | from config (`routing = "standard"`) | Risk-aware tiered model routing: routes routine diffs to economical models while keeping frontier models as anchors for critical files. |
| `--hints` / `--no-hints` | flag | from config (`hints = false`) | Static analysis pre-pass: runs fast local linters (Ruff, ESLint) and injects hints into Round 1 prompt context. |
| `--verify` / `--no-verify` | flag | from config (`true`) | Run (or skip) the verification round. |
| `--chair` | agent name, or `rotate` | from config (`claude`) | Which agent synthesizes the verdict (and runs verification). Must be an enabled agent. |
| `--seed` | integer | from config (unset) | Reproducible orchestration; identical mock runs + seed ⇒ byte-identical reports. |
| `--effort` | `low` \| `medium` \| `high` | from config (unset) | Reasoning effort for every agent this run, mapped per vendor. |

A fixed `--rounds N` is a hard override: it also disables adaptive early-stop
(for reproducible fixed-N runs) unless you pass `--early-stop` explicitly.
`--chair` accepts an **enabled agent name** or the literal `rotate`.

**Example:** `jury --pr 123 --rounds 1` runs review only (no debate);
`jury --pr 123 --early-stop --max-rounds 3 --chair rotate` debates only on
disagreement, up to 3 rounds, rotating the synthesizing chair per run.

**`--effort {low,medium,high}`** sets the reasoning effort for **every** agent
this run, overriding any `[[agent]] effort`. It is mapped to each vendor's own
control (agy model suffix, Anthropic thinking budget, OpenAI `reasoning_effort`,
Gemini `thinkingConfig`); a vendor with no effort control warns once on stderr
(`effort unsupported for <vendor>, ignored`) and runs unchanged. See the
[per-vendor mapping table](configuration.md#reasoning-effort-agent-effort----effort).

### Execution budget & reliability

| Flag | Value | Default | Description |
| --- | --- | --- | --- |
| `--total-timeout` | seconds | unset | Overall wall-clock budget for the whole run. |
| `--phase-timeout` | seconds | unset | Per-phase wall-clock budget. |
| `--retries` | integer ≥ 0 | `0` | Extra attempts for *transient* failures (timeout / rate-limit / spawn). |
| `--strict` | flag | off | Fail the run if any configured agent CLI is missing. |

**Example:** `jury --pr 123 --total-timeout 900 --phase-timeout 240 --retries 1`
caps the whole run at 15 min, each phase at 4 min, and retries transient
failures once.

### Large-diff handling

| Flag | Value | Default | Description |
| --- | --- | --- | --- |
| `--max-diff-bytes` | integer | `200000` | Size budget for the filtered diff before chunk/too-large. |
| `--chunk` / `--no-chunk` | flag | from config (`false`) | Chunk an over-budget diff by file instead of failing. |
| `--exclude` | path glob (repeatable) | from config (`[]`) | Exclude files matching this glob. |
| `--include` | path glob (repeatable) | from config (`[]`) | Only review files matching this glob. |

`--exclude` / `--include` are repeatable and are **added on top of** the config
lists. When any `--include` is set, only matching files are reviewed.

**Example:** `jury --pr 123 --chunk --max-diff-bytes 400000 --exclude 'docs/**'
--exclude '*.lock'` chunks an over-budget diff by file and skips docs/lockfiles.

### Context & privacy

| Flag | Value | Default | Description |
| --- | --- | --- | --- |
| `--context-mode` | `diff-only` \| `expanded` | from config (`diff-only`) | `diff-only` sends only the diff; `expanded` adds PR context. |
| `--redact` / `--no-redact` | flag | from config (`true`) | Redact recognized secrets from prompt text before sending. |
| `--policy` | path | auto-discover `.jury/policy.toml` / `jury-policy.toml` | Optional repository review policy; missing files are allowed. |

`--context-mode` accepts `diff-only` (just the diff) or `expanded` (adds PR
context). Redaction is on by default; `--no-redact` disables it.

**Example:** `jury --pr 123 --context-mode expanded --policy .jury/policy.toml`
sends the PR description/context alongside the diff and applies a repo policy.

### Output & format

| Flag | Value | Default | Description |
| --- | --- | --- | --- |
| `--format` | `markdown` \| `json` \| `sarif` | `markdown` | Output format for stdout/`--output`. |
| `--decision` | `chair` \| `vote` | from config (`chair`) | Final verdict source. `chair` = the chair's synthesis is the verdict (default). `vote` = a **panel vote**: each reviewer votes from the worst finding they raised, majority wins, ties resolve to the stricter stance; the chair's synthesis is then shown as supporting reasoning. The vocabulary is mode-aware — a diff/PR votes **REQUEST CHANGES > COMMENT > APPROVE**; an `--issue` votes **NEEDS-INFO > UNCLEAR > READY**. Rendering-only — does not change the run, the cache key, or the severity-based `--ci` gate. Example: `jury --pr 123 --decision vote`. |
| `--transcript` / `--no-transcript` | flag | from config | Render the full play-by-play transcript (each agent's review, the debate, and the chair's reasoning) instead of the consensus-first summary. `--no-transcript` forces the summary even if `[jury] transcript` is set. Markdown only; rendering-only (does not change the run or its cache key). |
| `--verbose` | flag | off | Summary report **and** the full transcript, in one document. Implies a transcript even with `--no-transcript`. |
| `--live` | flag | off | Stream each step (each review, each debate turn, verification, the decision) to stdout **as it happens**. Add **`--post`** (with `--pr` or `--issue`) to also post each step as its own comment on the PR/issue (posting is opt-in — a bare target only selects the source). The live stream replaces the consolidated **markdown** stdout dump (`-o` still writes the full report to a file; `--format json`/`sarif` still print the document to stdout). In chunked large-diff mode the stream repeats once per chunk. |
| `-o`, `--output` | path | stdout | Write the report to a file. |
| `--metadata-json` | path | — | Write machine-readable run metadata (durations, status, rounds) as JSON. |
| `-q`, `--quiet` | flag | off | Suppress progress logs on stderr. |

`--transcript` / `--verbose` shape the **stdout / `--output` / single-comment**
report. Phased posting (`--post-mode phased`) always posts the per-round
sections regardless, since it is already a round-by-round layout.

`--format` accepts `markdown` | `json` | `sarif`. `--transcript` and `--verbose`
apply to **markdown only**; `--live` streams markdown steps to stdout (and, with
`--pr --post`, posts each step). `--no-transcript` forces the summary even when
`[jury] transcript = true`.

**Example:** `jury --pr 123 --format sarif -o review.sarif` writes a SARIF
document for code-scanning; `jury --pr 123 --transcript -o review.md` writes the
full transcript to a file; `jury --pr 123 -q` silences the stderr progress logs.

### GitHub posting

| Flag | Value | Default | Description |
| --- | --- | --- | --- |
| `--post-summary`, `--post` | flag | off | Post the report as a single summary comment. Works with **`--pr`** (via `gh pr comment`) **and `--issue`** (via `gh issue comment`). |
| `--post-inline` | flag | off | Post inline review comments on located findings. **`--pr` only.** |
| `--post-progress` | flag | off | Keep a live, sticky status comment updated per round/chunk. **`--pr` only.** |
| `--post-mode` | `single` \| `phased` | `single` | With `--post-summary`: one comment, or separate Round 1 / debate / decision comments. **`--pr` only.** |
| `--dry-run` | flag | off | With `--post-inline`, print the payload without calling GitHub. |
| `--label` | flag | off | Apply classification labels (review-effort / risk / security) to the PR. **`--pr` only.** |

**Depends on / conflicts:** `--post`/`--post-summary` work with `--pr` **or**
`--issue` (bare `--pr`/`--issue` only selects the source — it never posts). The
other flags here — `--post-inline`, `--post-progress`, `--post-mode`, `--label` —
require `--pr` and are rejected with `--issue`. `--post-mode` requires
`--post-summary`/`--post`; `--post-mode` accepts `single` | `phased`.
`--dry-run` only affects `--post-inline`.

**Example:** `jury --pr 123 --post --post-inline --label` posts the summary,
adds inline comments on located findings, and applies classification labels;
`jury --pr 123 --post-inline --dry-run` previews the inline payload without
calling GitHub.

### CI gating

| Flag | Value | Default | Description |
| --- | --- | --- | --- |
| `--ci` | flag | off | CI mode: exit non-zero when blocking findings remain. |
| `--fail-on` | comma-separated severities | from config (`critical,major`) | Severities that fail CI. See [severities](#severities). |

`--fail-on` takes a comma-separated list drawn from the [severities](#severities)
(`critical`, `major`, `minor`, `nit`, `info`; `blocker` aliases `critical`).
Without `--ci`, `--fail-on` has no effect on the exit code.

**Example:** `jury --pr 123 --ci --fail-on critical,major` exits non-zero when a
confirmed critical or major finding remains — the canonical CI gate.

### Result cache

| Flag | Value | Default | Description |
| --- | --- | --- | --- |
| `--cache` | flag | off | Reuse a cached outcome for an unchanged diff+config, else run and store. |
| `--cache-dir` | path | `$JURY_CACHE_DIR` or `~/.cache/ai-jury` | Override the cache directory. |
| `--clear-cache` | flag | — | Delete all cache entries and exit (alias: `jury cache clear`). |

The cache key covers the diff, effective config, prompt version, package
version, context policy, and seed — change any and the next run is a miss.

**Example:** `jury --pr 123 --cache` reuses a stored verdict for an unchanged
diff+config; `jury --clear-cache` (or `jury cache clear`) wipes all entries.

### Suggested patches & incremental

| Flag | Value | Default | Description |
| --- | --- | --- | --- |
| `--suggest-patches` | flag | off | Emit an opt-in suggested-patches section for **verified** findings (read-only; never applied). |
| `--patches-out` | path | — | With `--suggest-patches`, write patches to this file instead of appending. |
| `--incremental` | flag | off | Review only the diff since the last jury run on `--pr` (falls back to full review). |

**Depends on / conflicts:** `--patches-out` requires `--suggest-patches`;
`--incremental` requires `--pr`. Suggested patches cover **verified** findings
only and are never applied automatically.

**Example:** `jury --pr 123 --suggest-patches --patches-out fixes.patch` writes
patches for verified findings to a file; `jury --pr 123 --incremental --post`
reviews only the new range since the last posted run, then posts.

### Utility

| Flag | Value | Description |
| --- | --- | --- |
| `--config` | path | Path to `jury.toml` (default: `./jury.toml` or built-in). |
| `--config-validate` | flag | Validate the resolved config and exit (`0` valid, `2` invalid). |
| `--strict-config` | flag | Treat configuration warnings as errors. |
| `--mock` | flag | Offline demo using deterministic mock agents. |
| `--doctor` | flag | Print a local readiness diagnostics report and exit (no telemetry). |
| `--json` | flag | With `--doctor`, print the machine-readable provider export (schema `ai-jury.doctor.v1`) as the **only** thing on stdout. |
| `--write` | path | With `--doctor`, also write the full internal diagnostics as JSON (secrets redacted). |
| `--version` | flag | Print the version and exit. |
| `-h`, `--help` | flag | Show help and exit. |

**Depends on / conflicts:** `--write` and `--json` only apply with `--doctor`;
`--json` without `--doctor` exits `2` (use `--format json` for the review report).
`--config-validate` and `--doctor` short-circuit the run (they print and exit).
Under `--json` the `--write` confirmation line goes to stderr, so stdout stays a
single JSON document.

**Example:** `jury --config-validate --config jury.toml` validates and exits
(`0` valid, `2` invalid); `jury --doctor --write doctor.json` prints readiness
diagnostics and also writes them as redacted JSON;
`jury --doctor --json | jq '.agents[] | select(.available) | .name'` lists the
reviewers this machine can actually run. The exported schema is documented in
[configuration.md](configuration.md#machine-readable-diagnostics-jury---doctor---json).

---

## Subcommands

### `jury init` — scaffold a `jury.toml`

| Flag | Value | Description |
| --- | --- | --- |
| `--preset` | `offline` \| `fast` \| `balanced` \| `thorough` | One-command setup (see [presets](#presets)). |
| `--agents` | `claude,codex,agy,qwen,claude-api,codex-api,gemini-api` | Comma-separated panel to scaffold. `claude-api`/`codex-api`/`gemini-api` scaffold a hosted-API agent (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GEMINI_API_KEY`) instead of a CLI. |
| `--rounds` | integer | Rounds for the scaffolded config. |
| `--chair` | agent name | Chair for the scaffolded config. |
| `--verify` / `--no-verify` | flag | Verification round on/off. |
| `--local-model` | model id | Model id for a local agent (e.g. `qwen2.5-coder:7b`). |
| `--local-endpoint` | URL | OpenAI-compatible base URL for a local agent. |
| `-o`, `--output` | path | Output path (default `jury.toml`). |
| `--force` | flag | Overwrite an existing file. |
| `--interactive` | flag | Force interactive prompts. The interactive flow also asks for a reasoning [effort](configuration.md#reasoning-effort-agent-effort----effort) (skippable); a chosen level is written onto each agent whose vendor supports it, and every other effort-capable agent gets a commented `# effort = "medium"` hint. |
| `--wizard` | flag | Guided, numbered-option setup for the most-used settings (reviewers, depth, decision, verification, context, CI gate). Every question is skippable (Enter keeps the built-in default); only the keys you choose are written, so the file stays minimal. |
| `--list-agents` | flag | List known agents + availability and exit. |
| `--list-models` | flag | List local models on the server and exit. |

**Example:** `jury init --preset balanced -o jury.toml` scaffolds a debate +
early-stop config; `jury init --agents claude,codex,qwen --chair rotate
--local-model qwen2.5-coder:7b` scaffolds a specific panel with a local model.

### Other subcommands

| Command | Description |
| --- | --- |
| `jury config show` | Print the effective resolved config and its source. |
| `jury config path` | Print the resolved config path. |
| `jury cache clear` | Delete all local cache entries (alias of `--clear-cache`). |
| `jury comment --text "/jury review" --pr N` | Run from an allowlisted PR comment. Actions: `review`, `summary` (see [comment actions](#comment-actions)). Flags: `--print-args`, `--no-post`. |
| `jury examples` | Print a plain-language list of common example commands. |
| `jury guide` | Print a short end-to-end walkthrough (install → init → review → post → CI). |

Running bare `jury` with no arguments **in a terminal** prints a compact overview
(what it does + the handful of commands most people use) and exits 0. In a
non-interactive context (piped/CI) it still errors with `provide one of
--pr, --issue, --diff-file, --commit, --commits` and a non-zero exit, so a script that forgot an input
fails loudly.

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
| `decision` | `"chair"` \| `"vote"` | `"chair"` | Final-verdict source: chair synthesis or a panel vote (CLI `--decision`). Rendering-only — not part of the config hash or cache key. |
| `theater` | bool | `false` | Enable live interactive terminal animation. |
| `theater_style` | `"flat"` \| `"pixel"` | `"flat"` | Visual aesthetic for terminal animation. |
| `routing` | `"standard"` \| `"tiered"` | `"standard"` | Risk-aware tiered model routing with frontier anchor (CLI `--tiered`). |
| `hints` | bool | `false` | Run fast static linter pre-pass to inject hints into Round 1 (CLI `--hints`). |
| `demote_local_only` | bool | `false` | Demote uncorroborated single-local-model findings to minor advisory status. |

**Example:**

```toml
[jury]
rounds = 2
chair = "rotate"
early_stop = true
max_rounds = 3
transcript = true   # default the markdown report to the full play-by-play
```

(With `transcript = true`, every run renders the transcript unless you pass
`--no-transcript`.)

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
| `vendor` | string | — | `anthropic` \| `openai` \| `google` \| `local` \| `anthropic-api` \| `openai-api` \| `google-api` \| `openai-compatible` \| `cli` \| custom registered vendor. |
| `command` | string | — | CLI command (not required for HTTP/API vendors or when `endpoint` is set). |
| `model` | string | unset | Model identifier. Required for API providers and local models. |
| `endpoint` | string | `http://localhost:11434/v1` (local) | Base URL for OpenAI-compatible HTTP providers (Ollama, OpenRouter, DeepSeek, Groq, Mistral, LiteLLM). |
| `api_key_env` | string | unset (`OPENAI_API_KEY` default) | Environment variable **name** holding the API key for `openai-compatible` vendors (e.g. `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`). Must match `[A-Za-z_][A-Za-z0-9_]*` (max 128 chars); anything else warns and falls back to the vendor default, because this name is echoed into `jury --doctor` and its JSON export. The warning names the agent and the rule but never quotes the rejected value back. The key's **value** is read from the environment and never displayed. |
| `prompt_mode` | string | `stdin` | Prompt delivery for `vendor = "cli"` (`stdin` \| `arg`). |
| `headers` | table | `{}` | Custom HTTP headers map for `openai-compatible` API calls. |
| `effort` | string | unset | `low` \| `medium` \| `high`. Reasoning effort, mapped per vendor (see [effort](configuration.md#reasoning-effort-agent-effort----effort)). An unknown value is a hard config error; a vendor with no effort control warns once and ignores it. Overridden by `--effort`. |
| `timeout` | int | `600` | Positive seconds (inherits `jury.timeout`). |
| `enabled` | bool | `true` | Disabled agents are skipped. |
| `extra_args` | list[str] | `[]` | Extra CLI args (e.g. the secure-default sandbox flags). |

---

## Enumerations

### Severities
Ordered most → least severe: **`critical`**, **`major`**, **`minor`**, **`nit`**, **`info`**.
Alias: `blocker` → `critical`. Used by `--fail-on` / `[jury.ci] fail_on`.

### Verdicts
The final verdict vocabulary is **mode-aware** (the rubric differs for code vs. an issue):

| Mode | Strictest → most permissive |
| --- | --- |
| **PR / diff** (`--pr`, `--diff-file`) | **`REQUEST CHANGES`** > **`COMMENT`** > **`APPROVE`** |
| **Issue** (`--issue`) | **`NEEDS-INFO`** > **`UNCLEAR`** > **`READY`** |

A panel vote (`--decision vote`) tallies along the same order — majority wins,
ties resolve to the **stricter** stance.

### Decision modes (`--decision` / `[jury] decision`)
`chair` (default — the chair synthesizes the verdict) · `vote` (panel vote; the chair's synthesis becomes supporting reasoning). Rendering-only — not part of the config hash, cache key, or the `--ci` gate.

### Vendors
`anthropic` · `openai` · `google` · `local` (OpenAI-compatible: Ollama, llama.cpp, vLLM,
LM Studio) · `anthropic-api` · `openai-api` · `google-api` · `openai-compatible` (OpenRouter, DeepSeek, Groq, Mistral, LiteLLM) · `cli` (arbitrary CLI agents: Aider, Goose, OpenHands) · custom registered vendors via `register_adapter()`.

### Presets
Set with `jury init --preset`.

| Preset | Panel / depth |
| --- | --- |
| `offline` | Local-only ($0), no cloud CLIs. |
| `fast` | 1 round (review only). |
| `balanced` | Debate + early-stop. |
| `thorough` | All available agents + debate + verify. |

### Reasoning effort (`--effort` / `[[agent]] effort`)
`low` · `medium` · `high`. Supported by `google` (agy), `anthropic-api`,
`openai-api`, `openai-compatible` and `google-api`; ignored with a one-line
warning for `anthropic`/`openai` (the `claude`/`codex` CLIs), `local`, `cli` and
custom vendors.

### Output formats
`markdown` (default) · `json` · `sarif`.

### Context modes
`diff-only` (default) · `expanded`.

### Post modes (`--post-mode`)
`single` (default, one comment) · `phased` (Round 1 / debate / decision as separate comments).

### Comment actions
Used by `jury comment`: `review` (full review) · `summary` (fast single-round pass).

---

## Environment variables

| Variable | Purpose |
| --- | --- |
| `JURY_CONFIG` | Custom path to `jury.toml` config file (overridden by `--config`). |
| `JURY_CACHE_DIR` | Cache directory (default `~/.cache/ai-jury`); overridden by `--cache-dir`. |
| `JURY_ALLOW_REMOTE_ENDPOINT` | Set to `1` to allow non-loopback HTTP/HTTPS endpoints for `vendor = "local"` and `openai-compatible`. |
| `ANTHROPIC_API_KEY` | API key used by `vendor = "anthropic-api"`. |
| `OPENAI_API_KEY` | API key used by `vendor = "openai-api"` and default for `openai-compatible`. |
| `GEMINI_API_KEY` | API key used by `vendor = "google-api"`. |
| `OPENROUTER_API_KEY` | Default API key for `openrouter` template. |
| `DEEPSEEK_API_KEY` | Default API key for `deepseek` template. |
| `GROQ_API_KEY` | Default API key for `groq` template. |
| `JURY_LIVE` | `1` enables opt-in live native-CLI tests. |
| `JURY_LOCAL_LIVE` | `1` enables opt-in live local-model tests. |
| `JURY_BENCH_LIVE` | `1` runs the benchmark against live agents instead of recorded fixtures. |

---

_See also: [configuration.md](configuration.md) for prose explanations and the
[cookbook](cookbook.md) for task-oriented recipes._
