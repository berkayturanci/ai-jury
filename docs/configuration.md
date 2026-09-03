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
| `anthropic-api` | `thinking.budget_tokens` (extended thinking) | `2048` | `8192` | `32768` |
| `openai-api` | `reasoning_effort` | `low` | `medium` | `high` |
| `openai-compatible` | `reasoning_effort` | `low` | `medium` | `high` |
| `google-api` | `generationConfig.thinkingConfig.thinkingBudget` | `1024` | `8192` | `32768` |
| `anthropic` (`claude` CLI) | — no headless control | ignored | ignored | ignored |
| `openai` (`codex` CLI) | — no headless control | ignored | ignored | ignored |
| `local`, `cli`, custom | — not sent | ignored | ignored | ignored |

Notes:

- **`agy` encodes effort in the model id.** `gemini-3.8-flash` + `high` becomes
  `gemini-3.8-flash-high`; a model that *already* carries a `-low`/`-medium`/`-high`
  suffix is left exactly as configured, so an explicit model id always wins.
  `jury --doctor --json` lists the ids the installed CLI actually offers.
- **Anthropic raises `max_tokens`.** Thinking tokens come out of the same
  allowance, so the request's `max_tokens` is lifted above the budget rather than
  leaving the response no room.
- **`local` is deliberately excluded.** Many local OpenAI-compatible servers
  reject an unknown request field outright, which would turn a hint into a failed
  review; effort is not sent on speculation.
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
  "tool_version": "1.15.1",
  "python": "3.12.14",
  "config_path": "/path/to/jury.toml",
  "ready": true,
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
| `transport` | `cli` (a command on PATH), `api` (a hosted vendor API), or `local` (an OpenAI-compatible server you run). |
| `command` / `endpoint` | Exactly one, named for the transport: a `cli` agent carries `command`, everything else carries the `endpoint` its adapter would actually call. |
| `reason` | Why an agent is unusable, or `null` when it is available. |
| `capabilities` | Labels from the version probe: `headless`, `model-selection`. |
| `models` | Model ids discovered for that agent (`agy models`, or a local server's `/v1/models`), or `null` when nothing could be listed. |
| `effort_supported` / `effort` | Whether the vendor has an effort control, and the level configured for this agent. |
| `warnings` | The same config warnings the human report lists. |

**Secrets are never included** — only environment *variable names* (e.g.
`OPENAI_API_KEY is not set in the environment`), never their values, and
userinfo credentials are stripped from any endpoint. The text report and this
export are two renderings of one projection (`doctor.doctor_report_dict`), so
they cannot disagree about the panel.

`--write PATH` is unchanged and still writes the *full* internal diagnostics dict
(a superset, no schema promise); under `--json` its confirmation line moves to
stderr so stdout stays a single JSON document.

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
