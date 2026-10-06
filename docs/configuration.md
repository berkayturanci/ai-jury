# `jury.toml` — behaviour & validation

The jury reads `./jury.toml` by default (override with `--config PATH`); built-in
defaults apply if the file is absent. Don't hand-write it the first time — run
**`jury init`** to scaffold a valid config from your installed agents (and
discovered local models).

**An auto-discovered `./jury.toml` that runs commands must be trusted first**
(#831). When the file was picked up from the working directory — not named with
`--config` — and any enabled or disabled `[[agent]]` in it has a `command` (every
CLI seat does: `claude`, `codex`, a bring-your-own CLI), a review or `jury
run-agent` runs its commands only once the file is trusted. Trust is one of:
`--config jury.toml`, the environment variable `JURY_TRUST_PROJECT_CONFIG` set to
`1`, `true`, `yes` or `on`, or a `y` answered at the terminal prompt, which is
recorded in `$XDG_CONFIG_HOME/ai-jury/trusted-configs` (default
`~/.config/ai-jury/`) against the file's real path **and** a hash of its bytes,
so an edited file asks again. `jury init` records trust for the file it writes.
Off a terminal there is no prompt, so a piped `git diff | jury --diff-file -`, a
CI job or the `/jury` comment workflow is refused with exit `2` and a message
naming these three ways in. `--mock` runs no command and is not gated, and
`--config-validate` reads the file without running anything.

> **Every field — type, default, allowed values** — for `[jury]`, `[jury.ci]`,
> `[jury.context]`, `[jury.diff]`, `[jury.output]` and `[[agent]]`, plus all CLI flags, lives in the
> single [**parameter reference**](parameters.md). This page explains the
> *behaviour* behind those keys: validation, execution budgets, adaptive rounds,
> large-diff handling, and the result cache.

## Validation

Check a config without running a review:

```
jury --config-validate --config jury.toml
```

Exit codes: `0` valid (warnings printed if any), `2` invalid — or unreadable: a
`--config` that names a directory, or a file without read permission, prints
`error: cannot read config <path>: <reason>`. During a normal run
the config is validated too; pass `--strict-config` to turn warnings into hard
errors (exit `2`).

The lists below name the common cases; they are not exhaustive, and the message
always names the key and the rule it breaks.

- **Hard errors** (always fail) include: `rounds < 1`, non-positive `timeout` (jury or
  per-agent), duplicate agent names, missing/empty agent `name` or `command`, no
  `[[agent]]` entries at all, `decision` other than `"chair"` / `"vote"`,
  an `adapter` this build does not have, an agent `headers` that is not a table
  (or whose key is not a string), an `endpoint` on a seat whose adapter runs a
  CLI (`anthropic`, `openai`, `google`, `cli`, `xai` — they spawn `command` and
  never read `endpoint`; before #901's review it silently skipped the `command`
  checks and the least-privilege audit), a `[jury.output] attribution` that is not
  a bool, malformed tables.
  - Also hard: a bound broken on `retries` (`>= 0`), `total_timeout`,
    `phase_timeout`, `max_rounds` or `[jury.diff] max_bytes` / `chunk_max_bytes`
    (`>= 1` when set); `theater` that is not a bool; `theater_style` other than
    `"flat"` / `"pixel"`; an unknown `routing`, `[[agent]] effort` or
    `[[agent]] tier`; a `temperature` that is not a number from `0` to `2`; a
    `[jury.ci] fail_on` entry that is not a [severity](parameters.md#severities);
    a `command` that is a relative path (`./bin/x` — use a bare name or an
    absolute path), or any non-absolute `command` while
    `JURY_REQUIRE_ABSOLUTE_COMMAND` is set; and an `endpoint` that is not a
    string, not a valid URL, not `http`/`https`, or on a non-loopback host
    without `JURY_ALLOW_REMOTE_ENDPOINT` in the environment.
- **Warnings** (fail only under `--strict-config`) include: unknown vendor, `chair` not
  matching an enabled agent, unknown top-level/section/agent keys, a non-string
  `headers` value (it is coerced to a string and sent).
  - Also warnings: a `local`, hosted-API or other HTTP seat with no `model`; an `api_key_env` that
    is not a valid environment-variable name (the vendor default is used); a
    `temperature` on a seat whose adapter is not `local` (ignored); and, once
    `JURY_ALLOW_REMOTE_ENDPOINT` allows it, a non-loopback `endpoint` (plus one
    more for plaintext `http`) — so `--strict-config` still refuses a remote
    endpoint.
  - Unknown keys are reported at *every* level, including inside the nested
    `[jury.ci]`, `[jury.context]`, `[jury.diff]` and `[jury.output]` tables, with the dotted path
    that locates them: `unknown key 'jury.ci.min_vendor'`. A key nobody reads is
    a setting that is silently not applied, so `--strict-config` refuses it.

### The vendor vocabulary

`vendor` must be one of:

`anthropic` · `openai` · `google` · `xai` · `local` · `anthropic-api` ·
`openai-api` · `google-api` · `xai-api` · `openai-compatible` · `cli` · any
vendor registered at runtime with `register_adapter()`.

Anything else is a **warning, not an error** — the seat still runs, on the
generic `cli` fallback — but it is counted as the vendor `cli` by the
cross-vendor guard, and the warning says so:

```
agent 'cursor' has unknown vendor 'xa1' (expected one of anthropic, openai,
google, xai, ...); using the generic 'cli' fallback, which counts as vendor
'cli' for min_vendors — two such seats are one vendor, not two.
```

Two seats that both land on the fallback therefore cannot satisfy
`min_vendors = 2` between them. A misspelled or unrecognised vendor is a
configuration mistake, and the failure mode it used to produce was a bench that
looked diverse and was not. The seat's own `vendor` string is still what the
report, the ballots and `--metadata-json` carry — provenance is never rewritten,
only the *gate* collapses.

**An unknown vendor's argv is not passed through as written.** Such a seat runs
the generic CLI adapter, but the read-only enforcement treats a vendor it does
not know as agy (#910): it removes every `--sandbox=<value>` from `extra_args` and
adds a bare `--sandbox` when the argv has none, whatever those flags mean to your
CLI, and the least-privilege audit warns that it did. `vendor = "acme"`,
`command = "mycli"`, `extra_args = ["--sandbox=false", "-x"]` runs
`mycli --sandbox -x`. Name the protocol — `adapter = "cli"` — to have the argv
passed through untouched.

`register_adapter("my-vendor", MyAdapter)` teaches the vocabulary as well as the
adapter table, so a genuinely custom vendor keeps its own identity at the gate
and stops warning.

**Spelling is normalised before any rule reads it.** `vendor` is stripped and
lower-cased once, when the seat is built, so `"XAI-API"`, `" xai-api "` and
`"xai-api"` are the same vendor to validation, to the adapter lookup, to
`--doctor`, to the read-only guard and to the cross-vendor gate alike. A vendor
this tool recognises is recognised by *every* rule: `vendor = "XAI-API"` with no
`command` is a valid hosted-API seat, not a seat missing a command, and
`vendor = " XAI "` gets the same invocation `vendor = "xai"` gets — no sandbox
flag from another vendor's CLI. Because normalising is only strip-and-lowercase,
the vendor *name* you wrote is what every surface below shows; its whitespace and
capitalisation are not. Validation messages still quote the file verbatim.

### Identity vs. protocol: the `adapter` key

`vendor` answers *what is this seat?* — the identity `min_vendors` counts, and
the string the ballots carry. `adapter` answers *how is its command line built?*
It is optional and defaults to the vendor's own adapter, so a config that names
only `vendor` builds exactly the argv it always did.

They were one key until #705, and one key could not describe a bench built from a
CLI that fronts several vendors' models — Cursor's `cursor-agent`, a corporate
gateway, a local proxy, an `aider`-style front:

```toml
# unsandboxed — runs with your permissions
[[agent]]
name = "gpt"
vendor = "openai"        # identity: what the cross-vendor gate counts
adapter = "cli"          # protocol: pass extra_args through untouched
command = "cursor-agent"
extra_args = ["-p", "--trust", "--mode", "ask", "--sandbox", "enabled", "--model", "gpt-5.3-codex-high", "--output-format", "text"]
```

A `cli`-adapter seat is unsandboxed — runs with your permissions: jury passes
the argv through as written and adds or checks no sandbox of its own, so what
confines the seat is what `cursor-agent`'s own flags ask for. The sample asks for
Cursor's read-only ask mode (`--mode ask`) and its own sandbox (`--sandbox
enabled`), and leaves out `--force`/`--yolo`, which approve every command. It
still runs in the checkout jury was started from, whose `.cursor/` project
config a trusted workspace loads — see
[Cursor CLI / Arbitrary CLI Agent](#cursor-cli--arbitrary-cli-agent-vendor--cli).

Without `adapter`, `vendor = "openai"` also selected the Codex adapter, which
runs `<command> exec …` — and `cursor-agent exec` is not a command, so the seat
died in half a second with `nonzero_exit`. The only pass-through adapter was
reachable as `vendor = "cli"`, which made every such seat the *same* vendor at
the gate. A bench of Cursor-fronted GPT, Gemini and Grok seats could run, or be
counted as three vendors — not both. With `adapter` it does both.

The adapter vocabulary **is** the vendor vocabulary, because the adapter registry
is keyed by vendor name: the claude protocol is `adapter = "anthropic"` (not
`"claude"`), the codex protocol is `"openai"`, agy's is `"google"`,
bring-your-own-CLI pass-through is `"cli"`, and each hosted API is its own
`…-api` name. `register_adapter("my-protocol", MyAdapter)` adds a name usable as
either key.

Everything about *how a seat is invoked* follows the adapter: the argv, the
read-only sandbox flag the tool guarantees (`--sandbox` is never spliced into a
CLI that has no such flag — except on a seat with an unknown vendor and no
`adapter`, which gets agy's handling, [above](#the-vendor-vocabulary)), whether a
`command` is required at all, how `effort`
is expressed, and the transport `--doctor` reports. Everything about *who the
seat is* follows the vendor: `min_vendors`, `panel.vendors`, the report's vendor
column, the ballots and `--format keel-reviews`.

**A mismatched pair is unusual, not invalid.** `vendor = "openai", adapter =
"anthropic"` validates silently and runs: it invokes `claude -p` and counts as
the vendor `openai`. That is deliberate. The supported configuration this key
exists for — `vendor = "openai", adapter = "cli"` — *is* a mismatch, so any rule
that flagged "the adapter is not the vendor's own" would fire on the main use
case; and the tool cannot tell a sensible pair from a silly one without knowing
which CLI fronts which vendor, which is precisely the knowledge it does not have.
What it can check, it checks strictly: an `adapter` naming a protocol this build
does not have is a **hard error**, not a warning, because the only alternative is
to guess — and the guess is the half-second `nonzero_exit` above, discovered
mid-run on a review you have already paid for. (An unknown *vendor* stays a
warning: that seat still runs, it just answers to `cli` at the gate.)

That error is raised **everywhere the name is read**, not only by
`--config-validate`. `jury --doctor` loads the config with the same validation a
run does, so it reports the config error as its verdict — `ready to run: no`,
naming the seat and the adapter, describing no seat — instead of a bench the run
will refuse. And the adapter lookup itself refuses a name it does not have rather
than falling through to the generic CLI adapter, which covers a caller that builds
a seat without validating the file (Python calling `make_adapter` directly).
`jury run-agent` validates the file as a review does (#903) and exits `2` with the
same message. Falling through was the last place
the silent guess survived: `--doctor` used to print three `[available]` rows and
`cross-vendor ready: yes` for the very file `jury` rejected before its first
round. (An unknown *vendor* still falls through, because that seat named no
protocol — inheriting the generic one is its documented behaviour, with agy's
`--sandbox` handling applied to its argv, as [above](#the-vendor-vocabulary).)

`--doctor` prints both fields on every seat row, so a Codex seat and a
GPT-through-Cursor seat are distinguishable at a glance:

```
  [available] codex (vendor=openai, adapter=openai, command=codex)
  [available] gpt   (vendor=openai, adapter=cli, command=cursor-agent)
```

### Which vendor string is which

The configured string and the identity a seat carries at the gate are two
different facts, and every place that shows one says which it is:

| Where | Shows | Why |
| --- | --- | --- |
| `--doctor` `panel.vendors_configured` / `vendors_available` | **Identity** | These are the gate's arithmetic. They equal what a run counts for the same config, so doctor cannot call a bench cross-vendor ready that the run then refuses — which is also why the doctor validates the config the way a run does: on a file the run rejects outright, it reports that error and counts nothing. |
| `--doctor` agent rows (`vendor`, and the text report's `vendor=…`) | **Configured vendor** (normalised spelling), with `vendor_identity` beside it | Provenance, plus the one number that matters next to it. The text report renders `vendor=xa1 -> counts as cli` only when the two differ. |
| `--doctor` agent rows (`adapter`, and the text report's `adapter=…`) | **Protocol** | Neither of the other two: it is how the seat's command line is built. `vendor=openai, adapter=cli` is a GPT model reached through someone else's CLI. One exception: a seat whose vendor this build does not know and that names no `adapter` shows that vendor string here (`adapter=acme`), while it runs on the generic CLI adapter with agy's `--sandbox` handling ([above](#the-vendor-vocabulary)). |
| `--metadata-json` `panel.vendors` | **Identity** | It is the number `min_vendors` is compared against. |
| `--metadata-json` `agents[].vendor`, the markdown report's `vendor` column, the ballots, `--format keel-reviews` | **Configured vendor** (normalised spelling) | These attribute output to the seat that produced it. Collapsing the gate is not a licence to rewrite provenance — and normalising the spelling does not rewrite it either: the vendor named is the vendor you configured. |

## Execution budget (`total_timeout` / `phase_timeout` / `retries`)

`total_timeout` and `phase_timeout` bound how long a run may take. The effective
per-agent-call timeout is the **minimum** of the agent's own `timeout`, the phase
budget, and the time remaining in the total budget — whichever is smallest. When
the total budget is exhausted, the remaining phases (debate/verify/synthesis) are
skipped and the report states this; the run still returns what completed
(partial-result policy). `retries` is opt-in and bounded: only failures classed as
transient (timeout / rate-limit / spawn / connection error) are retried, and a retry never overruns
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

[[agent]]
name = "claude"
vendor = "anthropic"
command = "claude"            # tier unset → "frontier": may anchor a routed panel

[[agent]]
name = "gpt"
vendor = "openai"
command = "codex"             # frontier too: benched on a routine diff

[[agent]]
name = "flash"
vendor = "google-api"         # hosted Gemini API, keyed by GEMINI_API_KEY
model = "gemini-3.8-flash"
tier = "economical"           # sits on routine diffs in place of the benched seats
```

- **`routing = "tiered"`** (`--tiered`): decides the **round-1 panel** from the
  diff's risk band and each seat's `[[agent]] tier` (#714). The band is the one
  `--auto` uses (`diffprofile`: size, file count, docs-only, security-sensitive
  paths); the tier is what the operator wrote — there are no model-name
  heuristics.
  - `high` risk (security-sensitive paths, large or many-file diffs) → the **full**
    enabled panel. Nothing is saved on a change that can hurt.
  - `low` or `medium` risk → every **economical** seat plus **one frontier anchor**:
    the configured chair when it is frontier and usable, otherwise the first usable
    frontier seat in config order. The remaining frontier seats are **benched**.
  - Two floors always hold: the panel keeps at least `[jury.ci] min_vendors`
    distinct vendors (counted as the gate counts them) and at least
    `[jury.ci] min_reviews` seats — benched seats are added back, a new vendor
    first, then config order, until both hold. `--min-vendors` reaches the plan.
  - A bench with no economical seat, or no frontier seat, runs the full panel
    (there is nothing to save, or nothing to anchor with) and the report says so.
  - **Escalation**: if round 1 leaves a `critical` or `major` finding, the benched
    frontier seats join the debate as cross-examiners (they receive every round-1
    review) and the chair for verification and synthesis is drawn from the
    frontier seats — the configured chair when it is one, else the first. Without
    escalation the benched seats never run. Some runs have **no debate to join**:
    one round (which is what `--auto` sets on the `low` band that benched them),
    or an adaptive run that converges after round 1 (`--auto` sets that on
    `medium`). Escalation then moves the chair and nothing else.
    `escalation_reason` is written **after** the debate section from what
    actually happened — `gpt joined the debate`, `the debate ran without the
    bench, so only the chair was escalated` (every benched call failed), or
    `no debate round ran, so only the chair was escalated` — so the record can
    never claim a round the run did not have. A chunked review escalates per
    chunk: the record says on how many, recomputes the effect from the merged
    debate, and publishes the chair of the first chunk that escalated. What an escalated seat contributes is
    a **debate** voice — `AGREE` / `DISPUTE` / `MISSED` on the reviews it is
    shown — which reaches the chair's verdict but is not parsed into structured
    findings; that is how the debate round has always worked, for every seat.
    A benched seat therefore never adds a finding the CI gate can fire on. If
    you need every seat's findings on every diff, that is what `standard`
    routing is.
  - Every decision is recorded: `metadata.routing` in the JSON report
    (`mode`, `risk`, `panel`, `benched`, `anchor`, `reason`, `escalated`,
    `escalation_reason`), one `routing:` line in the Markdown run metadata, and
    a `tiered routing:` log line. `routing` and every seat's `tier` are part of
    the config hash, so a `--cache` entry is never shared across two plans.
- **`hints = true`** (`--hints` / `--no-hints`): Runs fast local static linters (Ruff for Python, ESLint for JS/TS) on the files changed by the diff under review and injects compact hints into Round 1 prompt context so reviewers focus strictly on deep logic bugs and security flaws.

The linters are pointed at the **changed files and nothing else**: exactly the
paths the panel is shown, after the `[jury.diff]` `include`/`exclude` filters
have dropped the rest. A change that touches no file a linter handles — no
`.py` for Ruff, no `.js`/`.ts`/`.jsx`/`.tsx` for ESLint — produces **no block at
all**; the pre-pass never falls back to linting the whole working tree, so
pre-existing lint elsewhere in the repository cannot crowd out the diff (#737).
`--issue` reviews prose and has no changed files, so it never gets a block
either. Each linter reads those paths from your working tree, so a path that is
not checked out simply yields nothing, and a missing linter is not an error.

The hints are produced locally by this run, not taken from your context, so they
are added to the Round 1 prompt as their own block **after** the
`[jury.context] mode` filter — `hints = true` reaches the panel under
`diff-only` (the default) as well as `expanded`. Both keys are
orchestration-affecting: they are part of the config hash and the cache key, so
a `--cache` entry is never shared across two different settings. `routing` is a
closed vocabulary for the reason `[[agent]] tier` is — nothing but `"tiered"`
selects the routed panel, so `routing = "teired"` would quietly buy the standard
one — and an unknown value is a hard config error naming it.

The cache key covers more than the `hints` **flag**: it covers the **block** the
linters produced, whenever there is one (#745). The block is a function of your
working tree rather than of the diff, so it is the one prompt input that can
change while the diff and the config stand still — fix a lint error anywhere the
linters can see and the panel would have been shown something different. A run
whose linters said nothing, and every run under the default `hints = false`, keys
exactly as it did before, so no existing entry is invalidated.

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
| `xai-api` | `reasoning_effort` | `low` | `medium` | `high` |
| `openai-compatible` | `reasoning_effort` | `low` | `medium` | `high` |
| `google-api` | `generationConfig.thinkingConfig.thinkingBudget` | `1024` | `8192` | `32768` |
| `anthropic` (`claude` CLI) | — no headless control | ignored | ignored | ignored |
| `openai` (`codex` CLI) | — no headless control | ignored | ignored | ignored |
| `local`, `cli`, `xai` (Grok through a CLI), custom | — not sent | ignored | ignored | ignored |

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
  review; effort is not sent on speculation. For a local reasoning model, set the
  level in the model itself. For example, an Ollama Modelfile can rewrite
  gpt-oss's template default `Reasoning: medium` to `low`.
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

## Sampling temperature (`[[agent]] temperature`, local seats)

A local seat sends `temperature: 0` (greedy decoding) unless it is told
otherwise. Greedy is the right default for a reviewer, and most models are fine
with it. Some are not. Replaying a real jury prompt (a 36 KB diff) against
gpt-oss 20B on Ollama:

| `temperature` | Result |
|---|---|
| `0` | reasoning looped ("Ok." ×164, "Stop." ×48) to the 8,192-token output cap; **no answer** (`empty_output`, or a timeout without a cap) |
| `1` | a review in 164 s, with its `Checked:` and `Tested:` lines |

A model's own `PARAMETER temperature` cannot help, because the request value
overrides it. So the seat takes the setting:

```toml
[[agent]]
name = "gpt-oss"
vendor = "local"
model = "gpt-oss:20b"
temperature = 1.0      # OpenAI's recommended setting for gpt-oss; 0 loops
```

- **Local seats only.** It is sent by the `local` adapter, which is decided by the
  seat's [adapter](#identity-vs-protocol-the-adapter-key), not its vendor. On any
  other seat it is a warning (`temperature applies only to local seats`) and is
  ignored, because hosted and CLI seats own their own sampling.
- **Unset changes nothing.** The request carries the same literal `0` as before
  this key existed, and the config hash gains nothing, so existing cache entries
  stay valid. Set it only for a model that needs it.
- **Validated like `effort`.** A number from `0` to `2`, the OpenAI range, which
  every OpenAI-compatible server accepts. A string, a boolean, `nan`, or a value
  out of range is a **hard config error**: a silent fallback to `0` would bring
  back exactly the empty review the operator set it to fix.
- Part of the config hash when set, so runs at different temperatures do not
  share a cache entry.

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
  "tool_version": "1.25.0",
  "python": "3.12.14",
  "config_path": "/path/to/jury.toml",
  "ready": true,
  "panel": {
    "vendors_configured": 3,
    "vendors_available": 1,
    "min_vendors": 2,
    "contributing_vendors": null,
    "multi_vendor_ready": false,
    "panelists_available": 1,
    "reviews_supplied_max": 1,
    "min_reviews": 0
  },
  "agents": [
    {
      "name": "agy",
      "vendor": "google",
      "vendor_identity": "google",
      "adapter": "google",
      "transport": "cli",
      "available": true,
      "reason": null,
      "command": "agy",
      "resolved": "/usr/local/bin/agy",
      "version": "1.1.25",
      "capabilities": ["headless", "model-selection"],
      "models": ["gemini-3.8-flash-high", "gemini-3.8-flash-low"],
      "effort_supported": true,
      "effort": "high",
      "tier": "frontier"
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
| `vendor` | The vendor as configured, in its normalised spelling — stripped and lowercased once when the seat is built, so ` XAI-API ` reads back as `xai-api`. Provenance, as distinct from `vendor_identity`. |
| `vendor_identity` | The vendor this seat counts as at the cross-vendor gate: its own name when recognised, `cli` when it fell back to the generic adapter. This is what `panel` counts. |
| `adapter` | The protocol that builds this seat's command line — its `[[agent]] adapter`, or its vendor's own adapter when the key is unset. Neither provenance nor identity: `vendor: "openai", adapter: "cli"` is a GPT model reached through a CLI this tool does not otherwise know. For a seat whose vendor this build does not know and that names no `adapter`, this is that vendor string (`"acme"`), although the seat runs on the generic CLI adapter with agy's `--sandbox` handling (see [the vendor vocabulary](#the-vendor-vocabulary)). |
| `transport` | `cli` (a command on PATH), `api` (a hosted vendor API), or `local` (an OpenAI-compatible server you run). |
| `command` / `endpoint` | Exactly one, named for the transport: a `cli` agent carries `command`, everything else carries the `endpoint` its adapter would actually call. |
| `reason` | Why an agent is unusable, or `null` when it is available. |
| `capabilities` | Labels from the version probe: `headless`, `model-selection`. |
| `models` | Model ids discovered for that agent (`agy models`, or a local server's `/v1/models`), or `null` when nothing could be listed. Discovered **only** for `--json`: it costs a probe per agent, and the text report does not show it. |
| `effort_supported` / `effort` | Whether the vendor has an effort control, and the level configured for this agent. |
| `tier` | The seat's cost tier from `[[agent]] tier`: `frontier` (the default) or `economical`. What [tiered routing](#tiered-routing-routing--tiered--static-hints-hints--true) reads to decide which seats sit on a routine diff and which anchor it, so a bench can be checked before it is routed. |
| `warnings` | The same config warnings the human report lists. On a config with a **hard** error — an `adapter` this build does not have, no `[[agent]]` at all — this carries that error, `agents` is empty and `ready` is `false`: the doctor validates what a run validates, so it never describes a bench the run refuses. |

### `panel`: readiness, not contribution

| Field | Meaning |
| --- | --- |
| `vendors_configured` | Distinct vendor **identities** among the **enabled** agents — the same count `min_vendors` is compared against, so this number and a run's agree. Slots are not vendors: three agents on one vendor count as 1, and two seats on the generic `cli` fallback count as 1. With no config file, no reachable agent and a local server that lists a model, a run reviews with that model alone (the zero-config fallback), so every count here is that one-seat panel's, and the text report names the model on a `panel of a run:` line. |
| `vendors_available` | How many of those identities are reachable right now. |
| `min_vendors` | The effective threshold, resolved as a run resolves it: `--min-vendors N` / `--no-min-vendors` given with `--doctor`, else `[jury.ci] min_vendors` (`0` when opted out). |
| `contributing_vendors` | **Always `null` here.** Doctor runs no review, so it cannot know how many vendors would actually contribute. The real number is `panel.vendors` in a run's `--metadata-json`. |
| `panelists_available` | Enabled agents reachable right now — each of them a ballot in the bundle, the chairing agent included (it reviews too). |
| `reviews_supplied_max` | **Reviews** a downstream consumer would receive: at most one per reachable agent, so the same number as `panelists_available`. The chair's synthesis record is carried beside them and is **not** added — a gate like `keel review --from-jury` splits the `reviewers` array on `role`, counts only the ballots, and then refuses any whose scope names nothing. A strict **upper bound**, which is why the text report prints it as `at most N`: an agent that runs and returns nothing, or answers without naming a file, line or symbol, casts a ballot that is recorded and not counted. |
| `min_reviews` | The effective `[jury.ci] min_reviews` threshold (`0` when off). |
| `multi_vendor_ready` | `false` exactly when a run on this machine would fail the gate — the guard is on, this config names at least `min_vendors` distinct vendors (or `--min-vendors N` names the threshold, which a run enforces unscoped), and fewer than that are reachable. `true` when the guard is off, when the config never claimed that many vendors, or when enough are reachable. It is the run's own predicate, with reachable vendors in place of contributing ones. (Also `false` when no config could be loaded: there is nothing to be ready for.) |

`contributing_vendors` is in the export precisely *because* it is null: a
consumer must be able to see that availability was checked and contribution was
not. A CLI can be installed, pass its version probe, be reported `[available]`,
and still return nothing — that is [#635], and it is why a green doctor is not
evidence of a cross-vendor panel. Only a run proves that.

When two or more vendors are enabled and fewer than `min_vendors` are reachable,
or `--min-vendors N` asks for more vendors than are reachable, the same fact also
appears in `warnings` (and in the human report's **Cross-vendor readiness**
block), because the run that follows will exit 3. The zero-config fallback's
one-seat panel is not warned about under the default threshold, because its run
exits 0.

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
min_reviews = 0                   # REVIEWS a consumer must receive (ballots)
```

`fail_on` / `ignore_unverified` apply only under `--ci`. `min_vendors` applies to
**every** run, because it is not a judgement about findings — it is the question
of whether a jury happened at all.

Every `fail_on` entry must be a known [severity](parameters.md#severities) —
`critical`, `major`, `minor`, `nit`, `info`, or the `blocker` alias for
`critical` — matched case-insensitively. A value outside that vocabulary is a
**hard config error** naming it, not a warning: a misspelling (`"majr"`) matches
no finding, so the setting that decides whether CI fails would report a green
`PASS` quoting the typo on every run. `--fail-on` is held to the same
vocabulary and exits **2** with the same message.

| Key | Default | Effect |
| --- | --- | --- |
| `min_vendors` | `2` | Exit **3** unless at least this many *distinct vendors contributed a review*. `0` disables. |
| `min_reviews` | `0` | Refuse the run unless it can supply this many *reviews* to a downstream consumer — a panel ballot that named what it read and voted. `0` disables. |

### The panel-size guard (`min_reviews`)

A different question from `min_vendors`: not *how many perspectives did the panel
have* but *how many reviews does the thing downstream get handed*. That number is
one review per agent that answered **and named what it read**, and **not** one
more for the chair: a consumer splits the report's `reviewers` array on `role`,
reads the `chair` entry as the panel's consensus record rather than a review, and
then refuses any remaining verdict whose scope names no file, line or symbol. The
chairing agent still reviews — it is drawn from the usable agents and runs in
round 1 — and its ballot is an ordinary `panelist` entry that counts like any
other when it reviewed. The report says which agent chaired and what became of
its ballot, so neither half is a guess.

It is checked twice, because neither check alone is enough:

- **before the panel runs**, from the agents that are actually available. That
  can only be a *ceiling* — every seat is a seat that might review — so the line
  reads `at most N`, and a bench that cannot reach even the ceiling costs nothing
  and exits **2** with a message naming the shortfall;
- **on the result**, exit **3**, because no pre-flight can predict an agent that
  is installed, runs, and returns nothing, nor one that answers with prose naming
  nothing checkable, nor one that names a file and then declines — all are
  recorded as abstentions, none is a review, and the number supplied falls under
  the minimum ([#635] again, restated for ballots).

The failure message names the seats that ran without reviewing **by cause**, in
the five buckets `--metadata-json` publishes under `panel` — `silent`,
`insubstantial` (answered, named nothing checkable), `not_in_change` (named a
path or symbol this change does not contain), `refused` and `adapter_failed` —
because the remedies differ: a silent agent is a CLI that broke or a budget that
ran out, a seat that named nothing is a reviewer that did not review, a seat that
named only absent things reviewed something that is not this diff, a refusal is a
model declining the task, and a failed adapter is an invocation to fix. Each seat
is counted under the cause its own ballot records
(`reviewers[].abstention_cause`), so the five and `reviews_supplied` add up to
`ballots` and no sentence describes a seat as something its ballot denies.

`--min-reviews N` overrides the key. It is `0` by default: most consumers have no
minimum, and a gate that failed closed here would break every single-agent
install. Set it to what your consumer enforces — for a `keel review` at risk tier
3, `min_reviews = 3`.

Unlike `min_vendors` it is **not** part of the config hash, so turning it on does
not invalidate the review cache: it changes neither the orchestration nor the
findings, and it is re-evaluated on every run, cache hits included.

It **fails closed**, and it is scoped on the vendors your config **names**:

- a config naming two or more distinct vendors exits **3**
  unless at least that many actually contributed a review — **including when a
  configured CLI is not installed on this machine.** The shipped `jury.toml`
  enables two vendors (`claude` and `codex`; its `agy` seat is there with
  `enabled = false`, because agy is opt-in), and on a machine with only one of
  those CLIs installed it fails, by design: a
  configuration that promises two vendors and delivers one is the collapse
  this guard exists to catch, and a missing CLI is not an exemption. Install it,
  drop the agent from the config, or opt out explicitly;
- it applies only when the config claimed that consensus: with fewer distinct
  vendors enabled than the threshold — a deliberate single-agent setup — it
  never fires;
- with no config at all, the built-in seats are not that claim either. When none
  of their CLIs is installed and a local model server lists a model, the
  zero-config fallback seats that model, and the panel is that one seat: a
  single-vendor run the default never fails. The built-in seats are still listed
  in the report as never run;
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

## The attribution footer (`[jury.output]`)

A markdown report ends with one footer line naming the tool and the seats that
sat:

```markdown
<sub>🏛️ Synthesized by [ai-jury](https://github.com/berkayturanci/ai-jury) · claude, codex, agy — Cross-vendor multi-agent code review · [⭐ Star on GitHub](https://github.com/berkayturanci/ai-jury) · [Add to your repo](https://ai-jury.dev/)</sub>
```

(`--transcript` and `--verbose` use the same footer in their own wording,
"Generated by ai-jury · <seats>".) The seats are the ones that **returned** a
review, by their `[[agent]] name`, deduplicated and in seat order, so a seat that
failed or abstained (answered with nothing, or refused) is not claimed; when no seat returned a review the footer is kept without a
seat list. It is the last thing in the report, after the `## CI gate` and suggested
patch sections.

Every comment `jury` posts ends with exactly one footer: the single summary comment,
the issue comment, the last comment of `--post-mode phased`, and the final body of
the `--post-progress` live comment. The live comment's intermediate states, the
`--live` per-step comments and inline comments do not carry it. On the summary
and last phased comment it sits before the hidden incremental SHA marker, so
`--incremental` still reads the marker from the last comment. The issue comment
carries no SHA marker (`--incremental` is PR-only), so it ends with the footer.
The `--post-progress`
final body carries no SHA marker (it never has), so it ends with the footer. JSON, SARIF and `keel-reviews` output never get it. It is static text
in a report you already chose to print or post, so it makes no request of its own.

```toml
[jury.output]
attribution = false   # default true
```

`attribution = false`, or `--no-attribution` for one run, removes the footer (and
its `---` rule) from stdout, `-o` and every posted comment. The value must be a
bool: `"false"` is refused by validation, because a non-empty string would read as
true. The key is rendering-only and is not in the cache key.

## Local result cache (`--cache`)

Re-running the jury on an unchanged diff with an unchanged config wastes time and
tokens. The cache is **off by default**; enable it with `--cache`:

```
jury --pr 123 --cache      # reuse a cached outcome, or run + store on a miss
jury cache clear           # delete all cache entries (alias: --clear-cache)
```

The cache key covers the diff, the effective config hash, the prompt-template
version, the package version, the context mode and the redaction setting, whether
the verification round runs, the seed, whether the run is `--mock`, the review
mode (a diff/PR or an `--issue`), a fingerprint of the repository review policy
(`--policy`), and the version of the cache-entry format (`cache_schema`) — so any
change to those is a miss (automatic invalidation). A cache hit is marked in the progress
log and in the report's "Run metadata" section (`served from local cache`).

It also covers the **context text**, but only where the panel is shown it: under
`mode = "expanded"` the PR title and body go into every Round 1 prompt, so
editing a PR description is a miss (#738). Under the default `diff-only` the
context never reaches the panel and is not part of the key, so entries written
by default-mode runs keep their keys.

And it covers the **static-analysis block** on the same terms, wherever there is
one (#745). Unlike the context that is *every* context mode, because the block is
joined into Round 1 after the mode filter: with `hints = true` / `--hints`, a
working tree whose linters now say something different is a miss even though the
diff and the config are unchanged. A run whose linters produced nothing — every
run under the default `hints = false` included — keeps its key.

**Privacy.** A cache entry stores the full structured outcome, including agent
review/debate/synthesis text derived from the diff. Treat the cache directory as
sensitive — the same trust level as the diff. It defaults to `$JURY_CACHE_DIR`,
else `$XDG_CACHE_HOME/ai-jury`, else `~/.cache/ai-jury` (override with
`--cache-dir`).

## Universal Agent Provider Support

`ai-jury` supports **any AI agent provider**:

1. **Vendor Native CLIs**: `claude` (Anthropic Claude Code), `codex` (OpenAI Codex CLI), `agy` (Google Antigravity CLI — opt-in only: its `--sandbox` does not confine it, so it is never in the default panel and is seated only by name).
2. **Hosted Vendor APIs**: `vendor = "anthropic-api"` / `"openai-api"` / `"google-api"` / `"xai-api"` — no CLI install, keyed by an env-var API key.
3. **Hosted OpenAI-Compatible APIs**: `vendor = "openai-compatible"` works with OpenRouter, DeepSeek, Groq, Mistral, Anyscale, LiteLLM, or Azure OpenAI proxies. Configurable via `endpoint`, `api_key_env`, and custom `headers`.
4. **Local / Open-Weight Models**: `vendor = "local"` over Ollama, `llama.cpp`, vLLM, or LM Studio.
5. **Arbitrary Coding CLI Agents**: `vendor = "cli"` (such as Aider, Goose, OpenHands) — or `vendor = "xai"` for a Grok seat driven through Cursor's `cursor-agent` — with `prompt_mode = "stdin"` or `"arg"`.
6. **Pluggable Python Adapters**: Register custom adapters in Python via `ai_jury.adapters.register_adapter("my-vendor", MyAdapter)`.

### Configuration Examples (`jury.toml`)

The OpenRouter, DeepSeek and Groq seats below, and Grok written as
`openai-compatible`, point `endpoint` at a host that is not this machine. Such an
endpoint is refused — a hard config error, exit `2`, from `--config-validate` as
well as from a run — unless `JURY_ALLOW_REMOTE_ENDPOINT` is set in the
environment (see the [security note](#local-models-ollama-lm-studio-vllm-llamacpp-vendor--local)
below). Allowed, it is still a warning, so `--strict-config` still refuses it. The
hosted-API vendors (`anthropic-api`, `openai-api`, `google-api`, `xai-api`) have a
fixed endpoint and need no opt-in.

#### OpenRouter API (`vendor = "openai-compatible"`)

```toml
# needs JURY_ALLOW_REMOTE_ENDPOINT=1 in the environment: without it this
# non-loopback endpoint is refused and the run (or --config-validate) exits 2
[[agent]]
name = "openrouter"
vendor = "openai-compatible"
model = "anthropic/claude-opus-5.5"
endpoint = "https://openrouter.ai/api/v1/chat/completions"
api_key_env = "OPENROUTER_API_KEY"

[agent.headers]
HTTP-Referer = "https://github.com/berkayturanci/ai-jury"
X-Title = "ai-jury"
```

#### DeepSeek API (`vendor = "openai-compatible"`)

```toml
# needs JURY_ALLOW_REMOTE_ENDPOINT=1 in the environment: without it this
# non-loopback endpoint is refused and the run (or --config-validate) exits 2
[[agent]]
name = "deepseek"
vendor = "openai-compatible"
model = "deepseek-v4-pro"
endpoint = "https://api.deepseek.com/v1/chat/completions"
api_key_env = "DEEPSEEK_API_KEY"
```

#### Groq API (`vendor = "openai-compatible"`)

```toml
# needs JURY_ALLOW_REMOTE_ENDPOINT=1 in the environment: without it this
# non-loopback endpoint is refused and the run (or --config-validate) exits 2
[[agent]]
name = "groq"
vendor = "openai-compatible"
model = "llama-3.3-70b-versatile"
endpoint = "https://api.groq.com/openai/v1/chat/completions"
api_key_env = "GROQ_API_KEY"
```

#### Grok / xAI API (`vendor = "xai-api"`)

```toml
# Direct xAI API — fixed endpoint, keyed by XAI_API_KEY
[[agent]]
name = "grok"
vendor = "xai-api"
model = "grok-4.7"
```

The `openai-compatible` spelling still works and is still supported — xAI serves
the OpenAI chat-completions shape — but it makes the seat's vendor identity
`openai-compatible`, which is not what a Grok seat is:

```toml
# needs JURY_ALLOW_REMOTE_ENDPOINT=1 in the environment: without it this
# non-loopback endpoint is refused and the run (or --config-validate) exits 2
[[agent]]
name = "grok"
vendor = "openai-compatible"
model = "grok-4.7"
endpoint = "https://api.x.ai/v1/chat/completions"
api_key_env = "XAI_API_KEY"
```

#### Unified LLM Gateways & Proxies (OmniRoute, LiteLLM, One API) (`vendor = "openai-compatible"`)

```toml
# OmniRoute / Unified LLM Gateway
[[agent]]
name = "omni-claude"
vendor = "openai-compatible"
model = "anthropic/claude-opus-5.5"
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

# A reasoning model that loops under greedy decoding (see "Sampling temperature")
[[agent]]
name = "local-gpt-oss"
vendor = "local"
model = "gpt-oss:20b"
temperature = 1.0
```

> **Remote Endpoint Security Note:** By default, endpoints for `vendor = "local"` and `openai-compatible` are restricted to loopback interfaces (`localhost`, `127.0.0.1`, `::1`) to guard against unintended SSRF. If pointing to a trusted remote host, set `JURY_ALLOW_REMOTE_ENDPOINT=1` in your environment (any non-empty value is read as the opt-in, `0` included). The opt-in lives in the environment rather than in `jury.toml` on purpose: a config from a checkout you did not write must not be able to grant it.

#### Cursor CLI / Arbitrary CLI Agent (`vendor = "cli"`)

```toml
# Cursor CLI (using standalone cursor-agent binary & model selection)
# unsandboxed — runs with your permissions
[[agent]]
name = "cursor"
vendor = "cli"
command = "cursor-agent"
extra_args = ["--print", "--trust", "--mode", "ask", "--sandbox", "enabled", "--model", "claude-4.6-sonnet-medium", "--output-format", "text"]

# The same CLI pointed at a Grok model: `vendor = "xai"` so the seat is counted
# as xAI by the cross-vendor guard rather than as one more generic `cli`.
# unsandboxed — runs with your permissions
[[agent]]
name = "grok-cursor"
vendor = "xai"
command = "cursor-agent"
extra_args = ["-p", "--trust", "--mode", "ask", "--sandbox", "enabled", "--model", "cursor-grok-4.6-high-fast", "--output-format", "text"]

# Aider CLI
# unsandboxed — runs with your permissions
[[agent]]
name = "aider"
vendor = "cli"
command = "aider"
extra_args = ["--chat-mode", "ask", "--dry-run", "--no-auto-commits", "--no-dirty-commits", "--no-suggest-shell-commands", "--no-auto-lint", "--no-detect-urls", "--no-gitignore", "--message"]
prompt_mode = "arg" # "arg" (appends prompt as last argument) or "stdin" (pipes prompt to stdin)
```

Every seat in this block is **unsandboxed — runs with your permissions**. jury
passes a bring-your-own CLI's argv through as written, adds or checks no sandbox
of its own, and the least-privilege audit warns about each such seat (`--strict`
fails the run) — see [docs/security.md](security.md#bring-your-own-cli-seats).
Such a seat also runs in the directory `jury` was started from, which on a pull
request is the author's checkout, and both CLIs below read configuration from
that directory. The flags narrow what each CLI does with its prompt; they do not
stop it from obeying the checkout's own config. **Seat either only on checkouts
you trust.**

- **`cursor-agent`**: `--mode ask` is the mode its `--help` calls "Q&A style …
  (read-only)", and `--sandbox enabled` asks for its own sandbox ("overrides
  config" — measured, it did not change the user's `~/.cursor/cli-config.json`).
  `--print` alone is not read-only: its `--help` says it "has access to all
  tools, including write and shell". Measured on `cursor-agent` 2026.09.26 in an
  empty directory, asked to create a file and run `touch`: `--print --force` did
  both; `--print --trust` wrote the file (the shell command was refused);
  `--print --trust --mode ask` and `--print --trust --mode ask --sandbox enabled`
  did neither. `--trust` is what a headless run needs in a directory it has not
  seen (without it, or `--force`/`--yolo`, it stops at a workspace-trust prompt),
  and it trusts that directory's `.cursor/` project config too: Cursor's hooks
  documentation says project hooks (`.cursor/hooks.json`) "run in any trusted
  workspace", and lists a `workspaceOpen` hook that "Runs in the Cursor desktop
  app and CLI"; its CLI MCP page says the CLI "uses the same configuration as the
  editor", project or global. So an author's checkout can carry commands that run
  when the seat starts. The docs do not say which other hooks, or which MCP
  servers without `--approve-mcps`, run in `--print` mode. Never add
  `--force`/`-f`/`--yolo`: each approves every command.
- **`aider`**: `--message` alone is aider's normal editing mode, and aider
  commits its edits by default. What the flags control: `--chat-mode ask` makes
  the chat answer without editing ("never make changes"); `--dry-run` stops aider
  applying edits and committing; `--no-auto-commits`/`--no-dirty-commits` turn off
  its commits; `--no-suggest-shell-commands`, `--no-auto-lint`, `--no-detect-urls`
  and `--no-gitignore` turn off shell-command suggestions, the lint run after an
  edit, fetching URLs named in the prompt, and adding `.aider*` to `.gitignore`.
  What they do not control: aider reads `.aider.conf.yml` from the directory it
  runs in and from the git root, and `.env` from both. Those files can set
  `test: true` with a `test-cmd`, `lint: true` with a `lint-cmd`, or `load:`, and
  aider runs those commands before it reads `--message`, whatever the argv says —
  `--test` and `--lint` are switches with no `--no-` form, and no flag stops
  aider reading those files (`--config` and `--env-file` add a file, they do not
  replace the ones it finds). It also writes `.aider.chat.history.md` and
  `.aider.input.history` in the checkout, dry run or not. Checked against aider's
  source at `main` 5dc9490 (0.86.3.dev) and ConfigArgParse `master` d8ac796.
  Never add `--yes-always`. `--message` stays last, because `prompt_mode = "arg"`
  appends the prompt after it.

Both modes are the CLI's own promise, not something jury enforces.

**How the prompt reaches each seat.** Every Cursor seat above takes the prompt on
stdin: none sets `prompt_mode`, so the default `stdin` applies. With no prompt on
its command line, `cursor-agent -p` reads one from stdin. Measured on
`cursor-agent` 2026.09.26: with an empty stdin it stops with `No prompt provided
for print mode`, and with text on stdin it goes on to the model call, as it does
with the prompt as an argument. The account's usage limit stopped both runs before
a reply, so a reply to a stdin prompt was not observed. The aider seat has to use
`prompt_mode = "arg"`, because `--message` takes the prompt from its command line,
and `arg` puts the whole prompt into one argument. Linux caps one argument at
128 KiB: measured in a Linux container, a 131071-byte argument ran and a
131072-byte one failed with `Argument list too long`. That is below the 200000-byte
default of `[jury.diff] max_bytes`, and the prompt is the diff plus jury's own
instructions, so on Linux a large diff fails that seat. macOS caps the whole
command line at 1 MiB (`getconf ARG_MAX`). Windows caps it at 32767 characters,
according to Microsoft's `CreateProcess` documentation; this was not measured.
When a spawn fails this way, the seat's error names the prompt's size and
`prompt_mode`. On Linux, give an `arg` seat a lower `max_bytes` and
`chunk_max_bytes`, which apply to the whole panel, or use a CLI that reads stdin.

Cursor's sandbox (`--sandbox enabled`) was measured on macOS only. On Linux,
Cursor's run-modes documentation says the sandbox needs kernel 6.2 or later with
Landlock v3 and unprivileged user namespaces, and that without them Cursor "falls
back to asking for approval before running commands". jury has not measured
whether a headless `--print` run on such a host then refuses the command or
stops. On macOS, `--mode ask` alone already kept the seat from writing the file
or running the command (the measurement above).

`prompt_mode` is part of the config hash when it is written (#746), like
`adapter`: the two modes are two invocation protocols, so a `--cache` entry made
under one is never served for a run configured with the other. A seat that never
names the key hashes exactly as it did before, so no existing entry is
invalidated by this.

#### Custom Pluggable Python Adapter

For a backend that is neither a coding-agent CLI (`vendor = "cli"`) nor an
OpenAI-/Anthropic-compatible HTTP endpoint, subclass `Adapter`, override `run()`
to call your backend, and return an `AgentResult`:

```python
# docs-exec: custom-adapter — this block is executed verbatim by
# tests/test_docs_python_snippets.py, so it must import and run as written.
from ai_jury.adapters import Adapter, AgentResult, register_adapter


class CustomCompanyAdapter(Adapter):
    SPAWNS_PROCESS = False  # it calls a backend over the network, no subprocess

    def available(self) -> bool:
        return True  # whether your backend is reachable (shown by --doctor)

    def run(self, prompt, phase="review", timeout=None, role_policy=None):
        # Call your HTTP/gRPC/SDK backend with `prompt` and return its text.
        # `phase` is "review", "debate", or "verify"; a reviewer emits a fenced
        # JSON findings block the panel parses out of `output`. This stub returns
        # an empty review so the example runs offline.
        review_text = "Checked: src/example.py\nTested: nothing\n[]"
        return AgentResult(
            agent=self.name,
            vendor=self.spec.vendor,
            ok=True,
            output=review_text,
            duration_s=0.0,
        )


# Teach this build the vendor name so a seat can select it.
register_adapter("company-llm", CustomCompanyAdapter)
```

`SPAWNS_PROCESS = False` tells the least-privilege audit the adapter runs no subprocess, so its seats are not warned about (or failed by `--strict`) as unsandboxed CLIs; leave it out (it defaults to `True`) for an adapter that spawns one.

Reference it from a seat like any built-in vendor (no `command` when it sets
`SPAWNS_PROCESS = False` — your `run()` is the invocation):

```toml
[[agent]]
name = "internal-llm"
vendor = "company-llm"
model = "company-v1"
```

The `jury` CLI reviews with the adapters compiled into the build — it does not
import external Python, so there is no `--load` flag. Register your adapter in
the **same process** that runs the panel, i.e. drive the jury from Python:

```python
from ai_jury.config import load_config
from ai_jury.orchestrator import review_diff

# ...after the register_adapter(...) call above has run in this process...
config = load_config("jury.toml")  # the seat above selects vendor = company-llm
with open("changes.diff", encoding="utf-8") as fh:
    diff = fh.read()
outcome, _plan = review_diff(config, diff)  # runs the panel with your adapter
print(len(outcome.findings), "finding(s)")
```
