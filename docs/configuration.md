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

## Theater view (`theater` / `theater_style`)

The opt-in animated **deliberation** view (`--theater`) can be defaulted on from
the config so you don't pass the flag every run:

```toml
[jury]
theater = true            # default the animated scene on (default: false)
theater_style = "pixel"   # "flat" (ANSI line scene, default) | "pixel" (pixel-art room)
```

The CLI overrides the file per run: `--theater` / `--no-theater` and
`--theater-style {flat,pixel}`. Like `decision`, this is **rendering-only** — it
never touches orchestration, the config hash / cache key, or the `--ci` gate.
Theater is TTY-only: even with `theater = true` it falls back to the plain
`--live` step stream off an interactive terminal, and `pixel` falls back to
`flat` without a truecolor + unicode terminal.

## Tiered Routing (`routing = "tiered"`) & Static Hints (`hints = true`)

Control cost optimization and deterministic pre-pass linter checks:

```toml
[jury]
routing = "tiered"   # "standard" (default) | "tiered" (risk-aware cost tiering)
hints = true         # run local Ruff/ESLint pre-pass before Round 1 (default: false)
```

- **`routing = "tiered"`** (`--tiered`): Uses the diff risk classifier to route non-critical files to economical models while keeping frontier models as anchor reviewers for security-critical paths (`auth/`, `crypto/`).
- **`hints = true`** (`--hints`): Runs fast local static linters (Ruff for Python, ESLint for JS/TS) on modified files and injects compact hints into Round 1 prompt context so reviewers focus strictly on deep logic bugs and security flaws.

## Reasoning effort (`[[agent]] effort` / `--effort`)

Ask a reviewer to think harder (or cheaper) without learning each vendor's own
knob. Set it per agent, or for the whole run:

```toml
[[agent]]
name = "gemini-api"
vendor = "google-api"
model = "gemini-3.8-flash"
effort = "high"        # "low" | "medium" | "high"
```

```
jury --pr 123 --effort high     # overrides every [[agent]] effort for this run
```

The level is a **hint expressed in each vendor's own vocabulary** — the mapping
lives in exactly one place (`adapters.effort_args`):

| Vendor | How the level is sent | `low` | `medium` | `high` |
| --- | --- | --- | --- | --- |
| `google` (`agy` CLI) | model-id suffix | `<model>-low` | `<model>-medium` | `<model>-high` |
| `anthropic-api` | `thinking.budget_tokens` (extended thinking) | `2048` | `8192` | `27904` † |
| `openai-api` | `reasoning_effort` | `low` | `medium` | `high` |
| `openai-compatible` | `reasoning_effort` | `low` | `medium` | `high` |
| `google-api` | `generationConfig.thinkingConfig.thinkingBudget` | `1024` | `8192` | `32768` |
| `anthropic` (`claude` CLI) | — no headless control | ignored | ignored | ignored |
| `openai` (`codex` CLI) | — no headless control | ignored | ignored | ignored |
| `local`, `cli`, custom | — not sent | ignored | ignored | ignored |

† **`high` is clamped.** Thinking tokens come out of the same allowance as the
response, so `max_tokens` is lifted above the budget — but per-model `max_tokens`
caps vary, and the nominal `32768` plus the 4096-token response allowance would
build a request some models reject. The budget is therefore capped at
**27904**, holding `max_tokens` at or below the documented ceiling of **32000**.
`low` and `medium` are already under it and are sent as-is.

Notes:

- **`agy` encodes effort in the model id.** `gemini-3.8-flash` + `high` becomes
  `gemini-3.8-flash-high`; a model that *already* carries a `-low`/`-medium`/`-high`
  suffix is left exactly as configured, so an explicit model id always wins.
  `jury --doctor --json` lists the ids the installed CLI actually offers.
- **A suffixed id is checked against what `agy` offers.** When the listing can be
  discovered, an id the CLI does not have (say the model has no `-high` variant)
  falls back to the configured model and warns, rather than sending an unknown id
  and losing that reviewer's whole review. If the listing cannot be discovered,
  the mapping is used as-is — "unknown" is not treated as "absent". The probe runs
  only when an effort level is set, and at most once per run per agent.
- **`local` is deliberately excluded.** Many local OpenAI-compatible servers
  reject an unknown request field outright, which would turn a hint into a failed
  review; effort is not sent on speculation.
- **`openai-compatible` is only as good as the provider behind it.** The vendor
  covers OpenRouter, DeepSeek, Groq, Mistral, LiteLLM and any other
  OpenAI-shaped endpoint, and `reasoning_effort` is sent to all of them. A
  provider that validates unknown fields strictly will reject the request rather
  than ignore it, so set `effort` on such a profile only when that provider
  documents `reasoning_effort`; check with a one-agent run before relying on it.
- **Unsupported vendors warn once per run** (`effort unsupported for <vendor>,
  ignored`) on stderr and run unchanged — a panel is never blocked by one
  panelist that cannot take the hint.
- An unknown level (`effort = "maximum"`) is a **hard config error**, not a
  silent fallback: paying for a shallower run than you asked for is worse than
  a clear message.
- Effort is part of the config hash, so two runs that differ only by effort do
  not share a cache entry.

## Machine-readable diagnostics (`jury --doctor --json`)

`jury --doctor` prints a human report. `--json` prints the same facts as a single
JSON document on stdout and nothing else, for wizards and orchestrators that need
to know what this machine can actually run:

```
jury --doctor --json | jq '.agents[] | select(.available) | .name'
```

The document is `schema_version: "ai-jury.doctor.v1"`:

```json
{
  "schema_version": "ai-jury.doctor.v1",
  "tool_version": "1.16.0",
  "python": "3.12.14",
  "config_path": "/path/to/jury.toml",
  "ready": true,
  "panel": {
    "vendors_configured": 3,
    "vendors_available": 1,
    "min_vendors": 2,
    "contributing_vendors": null,
    "multi_vendor_ready": false
  },
  "agents": [
    {
      "name": "agy",
      "vendor": "google",
      "transport": "cli",
      "available": true,
      "reason": null,
      "command": "agy",
      "resolved": "/usr/local/bin/agy",
      "version": "1.1.25",
      "capabilities": ["headless", "model-selection"],
      "models": ["gemini-3.8-flash-high", "gemini-3.8-flash-low"],
      "effort_supported": true,
      "effort": "high"
    }
  ],
  "warnings": []
}
```

| Field | Meaning |
| --- | --- |
| `schema_version` | `ai-jury.doctor.v1`. Bumped on any breaking shape change. |
| `ready` | At least one configured agent is reachable. |
| `panel` | Cross-vendor readiness (see below). |
| `transport` | `cli` (a command on PATH), `api` (a hosted vendor API), or `local` (an OpenAI-compatible server you run). |
| `command` / `endpoint` | Exactly one, named for the transport: a `cli` agent carries `command`, everything else carries the `endpoint` its adapter would actually call. |
| `reason` | Why an agent is unusable, or `null` when it is available. |
| `capabilities` | Labels from the version probe: `headless`, `model-selection`. |
| `models` | Model ids discovered for that agent (`agy models`, or a local server's `/v1/models`), or `null` when nothing could be listed. Discovered **only** for `--json`: it costs a probe per agent, and the text report does not show it. |
| `effort_supported` / `effort` | Whether the vendor has an effort control, and the level configured for this agent. |
| `warnings` | The same config warnings the human report lists. |

### `panel`: readiness, not contribution

| Field | Meaning |
| --- | --- |
| `vendors_configured` | Distinct vendors among the **enabled** agents. Slots are not vendors: three agents on one vendor count as 1. |
| `vendors_available` | How many of those are reachable right now. |
| `min_vendors` | The effective `[jury.ci] min_vendors` threshold (`0` when opted out). |
| `contributing_vendors` | **Always `null` here.** Doctor runs no review, so it cannot know how many vendors would actually contribute. The real number is `panel.vendors` in a run's `--metadata-json`. |
| `multi_vendor_ready` | `false` exactly when a run on this machine would fail the gate — the guard is on, this config names at least `min_vendors` distinct vendors, and fewer than that are reachable. `true` when the guard is off, when the config never claimed that many vendors, or when enough are reachable. (Also `false` when no config could be loaded: there is nothing to be ready for.) |

`contributing_vendors` is in the export precisely *because* it is null: a
consumer must be able to see that availability was checked and contribution was
not. A CLI can be installed, pass its version probe, be reported `[available]`,
and still return nothing — that is [#635], and it is why a green doctor is not
evidence of a cross-vendor panel. Only a run proves that.

When two or more vendors are enabled and fewer than `min_vendors` are reachable,
the same fact also appears in `warnings` (and in the human report's
**Cross-vendor readiness** block), because the run that follows will exit 3.

[#635]: https://github.com/berkayturanci/ai-jury/issues/635

**Secrets are never included** — only environment *variable names* (e.g.
`OPENAI_API_KEY is not set in the environment`), never their values, and
userinfo credentials are stripped from any endpoint. The text report and this
export are two renderings of one projection (`doctor.doctor_report_dict`), so
they cannot disagree about the panel.

`--write PATH` is unchanged and still writes the *full* internal diagnostics dict
(a superset, no schema promise); under `--json` its confirmation line moves to
stderr so stdout stays a single JSON document.

## CI gate & the cross-vendor guard (`[jury.ci]`)

```toml
[jury.ci]
fail_on = ["critical", "major"]   # severities that fail `jury --ci` (exit 1)
ignore_unverified = true          # only verified findings can fail the build
min_vendors = 2                   # distinct vendors that must have REVIEWED
```

`fail_on` / `ignore_unverified` apply only under `--ci`. `min_vendors` applies to
**every** run, because it is not a judgement about findings — it is the question
of whether a jury happened at all.

| Key | Default | Effect |
| --- | --- | --- |
| `min_vendors` | `2` | Exit **3** unless at least this many *distinct vendors contributed a review*. `0` disables. |

It **fails closed**, and it is scoped on the vendors your config **names**:

- from this release, a config naming two or more distinct vendors exits **3**
  unless at least that many actually contributed a review — **including when a
  configured CLI is not installed on this machine.** The shipped three-vendor
  `jury.toml` on a machine with one installed CLI fails, by design: a
  configuration that promises three vendors and delivers one is the collapse
  this guard exists to catch, and a missing CLI is not an exemption. Install it,
  drop the agent from the config, or opt out explicitly;
- it applies only when the config claimed that consensus: with fewer distinct
  vendors enabled than the threshold — a deliberate single-agent setup — it
  never fires;
- an agent that abstained (replied with no findings block) or came back
  `ok=False` — including the typed `no_review` failure for a refusal or a CLI
  usage banner — does not count toward the total;
- `--min-vendors N` overrides the key, and a threshold given on the command line
  is enforced as given, scoping or not;
- `--no-min-vendors` (or `min_vendors = 0`) is the explicit opt-out, and
  `--strict` is the other escape for the missing-CLI case: it does not lower the
  bar, it fails at **startup** on a configured CLI that is not installed, so you
  learn before the run rather than from a collapsed panel after it.

Exit 3 is distinct from the `--ci` findings failure (exit 1), and a collapsed
panel outranks it: `evaluate_ci` reports on findings the panel did or did not
raise, and a panel that never formed is not evidence either way.

The official GitHub Action exposes the same knob as a `min-vendors` input,
defaulting to `2` — see [cookbook §15](cookbook.md#15-zero-friction-ci-with-the-official-github-action).

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

## Universal Agent Provider Support

`ai-jury` supports **any AI agent provider**:

1. **Vendor Native CLIs**: `claude` (Anthropic Claude Code), `codex` (OpenAI Codex CLI), `agy` (Google Antigravity CLI).
2. **Hosted OpenAI-Compatible APIs**: `vendor = "openai-compatible"` works with OpenRouter, DeepSeek, Groq, Mistral, Anyscale, LiteLLM, or Azure OpenAI proxies. Configurable via `endpoint`, `api_key_env`, and custom `headers`.
3. **Local / Open-Weight Models**: `vendor = "local"` over Ollama, `llama.cpp`, vLLM, or LM Studio.
4. **Arbitrary Coding CLI Agents**: `vendor = "cli"` (such as Aider, Goose, OpenHands) with `prompt_mode = "stdin"` or `"arg"`.
5. **Pluggable Python Adapters**: Register custom adapters in Python via `ai_jury.adapters.register_adapter("my-vendor", MyAdapter)`.

### Configuration Examples (`jury.toml`)

#### OpenRouter API (`vendor = "openai-compatible"`)

```toml
[[agent]]
name = "openrouter"
vendor = "openai-compatible"
model = "deepseek/deepseek-r1"
endpoint = "https://openrouter.ai/api/v1/chat/completions"
api_key_env = "OPENROUTER_API_KEY"

[agent.headers]
HTTP-Referer = "https://github.com/berkayturanci/ai-jury"
X-Title = "ai-jury"
```

#### DeepSeek API (`vendor = "openai-compatible"`)

```toml
[[agent]]
name = "deepseek"
vendor = "openai-compatible"
model = "deepseek-reasoner"
endpoint = "https://api.deepseek.com/v1/chat/completions"
api_key_env = "DEEPSEEK_API_KEY"
```

#### Groq API (`vendor = "openai-compatible"`)

```toml
[[agent]]
name = "groq"
vendor = "openai-compatible"
model = "llama-3.3-70b-versatile"
endpoint = "https://api.groq.com/openai/v1/chat/completions"
api_key_env = "GROQ_API_KEY"
```

#### Grok / xAI API (`vendor = "openai-compatible"`)

```toml
# Direct xAI API (https://api.x.ai/v1)
[[agent]]
name = "grok"
vendor = "openai-compatible"
model = "grok-2-latest"
endpoint = "https://api.x.ai/v1/chat/completions"
api_key_env = "XAI_API_KEY"
```

#### Unified LLM Gateways & Proxies (OmniRoute, LiteLLM, One API) (`vendor = "openai-compatible"`)

```toml
# OmniRoute / Unified LLM Gateway
[[agent]]
name = "omni-claude"
vendor = "openai-compatible"
model = "anthropic/claude-3-5-sonnet"
endpoint = "http://localhost:8000/v1/chat/completions"
api_key_env = "OMNIROUTE_API_KEY"
```

#### Local Models (Ollama, LM Studio, vLLM, llama.cpp) (`vendor = "local"`)

```toml
# Ollama default (http://localhost:11434/v1)
[[agent]]
name = "local-qwen"
vendor = "local"
model = "qwen2.5-coder:14b"

# LM Studio or vLLM custom port
[[agent]]
name = "local-lmstudio"
vendor = "local"
model = "local-model"
endpoint = "http://localhost:1234/v1"
```

> **Remote Endpoint Security Note:** By default, endpoints for `vendor = "local"` and `openai-compatible` are restricted to loopback interfaces (`localhost`, `127.0.0.1`, `::1`) to guard against unintended SSRF. If pointing to a trusted remote host, set `JURY_ALLOW_REMOTE_ENDPOINT=1` in your environment.

#### Cursor CLI / Arbitrary CLI Agent (`vendor = "cli"`)

```toml
# Cursor CLI (using standalone cursor-agent binary & model selection)
[[agent]]
name = "cursor"
vendor = "cli"
command = "cursor-agent"
extra_args = ["--print", "--trust", "--model", "claude-4.6-sonnet-medium"]
prompt_mode = "arg"

# Aider CLI
[[agent]]
name = "aider"
vendor = "cli"
command = "aider --message"
prompt_mode = "arg" # "arg" (appends prompt as last argument) or "stdin" (pipes prompt to stdin)
```

#### Custom Pluggable Python Adapter

```python
from ai_jury.adapters import BaseAdapter, register_adapter, AgentResult


class CustomCompanyAdapter(BaseAdapter):
    def invoke(self, prompt: str, timeout: float) -> AgentResult:
        # Custom HTTP, gRPC, or CLI logic here
        return AgentResult(ok=True, text="Response from custom adapter")


# Register custom vendor
register_adapter("company-llm", CustomCompanyAdapter)
```
```toml
[[agent]]
name = "internal-llm"
vendor = "company-llm"
model = "company-v1"
```
