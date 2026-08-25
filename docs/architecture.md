# Architecture

`ai-jury` orchestrates **native coding-agent CLIs from different vendors**
into an adversarial review panel over a single diff. The design goal is a clean
separation between *what to ask* (orchestrator + prompts) and *how to invoke a given
agent* (adapters), so new vendors are a ~20-line addition.

## Components

```
   diff / PR            ┌──────────────────────────────────────────────────────┐
   (gh / file / stdin)─▶│ prep: redact secrets · scan injection · filter+chunk  │ (redaction/injection/largediff)
                        └───────────────────────────┬──────────────────────────┘
                        ┌───────────────────────────▼──────────────────────────┐
                        │                     orchestrator                      │
                        │  round 1 review → round 2 debate (adaptive) →         │
                        │  verify → synthesis      (budget · retries · cache)   │
                        └──┬───────────────┬───────────────┬───────────────┬────┘
                           │ prompts.py     │               │               │
                    ┌──────▼─────┐  ┌───────▼────┐  ┌────────▼───┐  ┌────────▼─────┐
                    │ClaudeAdapter│ │CodexAdapter│  │ AgyAdapter │  │ LocalAdapter │  (adapters.py)
                    │  claude -p  │ │ codex exec │  │ agy --print│  │ HTTP /v1 chat│
                    └─────────────┘ └────────────┘  └────────────┘  └──────────────┘
                       cloud CLIs (subprocess, headless, parallel)   local / open-weight
                                                │
                                  consensus.py → report.py → markdown / json / sarif
                                                          → stdout / -o / gh comment / CI gate
```

## Round structure

1. **Round 1 — independent review.** Every available agent reviews the diff in
   isolation with the same rubric (correctness → security → regressions → tests).
   Agents run concurrently (thread pool; each call is an IO-bound subprocess).
2. **Round 2 — cross-examination (optional, `rounds = 2`).** Each agent receives the
   diff, its own review, and every *other* agent's review, then emits `AGREE` /
   `DISPUTE` / `MISSED`. This is where cross-model disagreement filters false
   positives and surfaces gaps. Only agents whose round-1 call succeeded participate;
   debate needs ≥2 of them. With `early_stop = true` the debate is **adaptive**: a
   unanimous round-1 panel skips it, and disagreement runs debate up to `max_rounds`
   (see `convergence.py`).
3. **Verify (optional, `verify = true`).** The chair re-judges each candidate finding
   (`verified` / `unsupported` / `needs_human_decision`) to drop false positives
   before synthesis; verdicts attach to the consensus groups.
4. **Synthesis.** The configured `chair` agent consolidates all rounds into a single
   verdict (`APPROVE` / `COMMENT` / `REQUEST CHANGES` for a PR or diff; `READY` /
   `NEEDS-INFO` / `UNCLEAR` for an `--issue`) plus consensus, disputed, and
   notable single-reviewer findings. If the chair is unavailable, the first available
   agent chairs. With `decision = "vote"` the verdict is a panel tally instead.

The whole run is bounded by an optional **budget** (`total_timeout` / `phase_timeout`)
with opt-in **retries** for transient failures, and can be served from / stored in an
optional **result cache** (`--cache`). Large diffs are measured, filtered (binary /
generated / path globs), and **chunked** before review.

**Auto-depth (`--auto` / `auto_depth`).** Before round 1 the diff is profiled
(`diffprofile.py`) and the round count / verify flag are set from its risk level, so a
trivial change runs shallow and a risky one runs the full panel without manual flags.

**Live output.** Because a full run can take minutes, the orchestrator emits milestone
events the CLI can surface on the PR: `--post-progress` maintains one **sticky** status
comment updated each round/chunk, and `--post-mode phased` posts Round 1 / debate /
decision as **separate** comments as each phase completes (vs the default single
end-of-run comment).

## Adapters

Each CLI-backed adapter knows only how to invoke one CLI headlessly; the HTTP-backed
adapters (`LocalAdapter` and the hosted-API adapters) speak plain HTTP instead. Either
way, the orchestrator owns prompt content. Verified headless invocations (early 2026):

| Vendor | Adapter | Invocation |
|:--|:--|:--|
| Anthropic | `ClaudeAdapter` | `claude -p "<prompt>" --output-format text` |
| OpenAI | `CodexAdapter` | `codex exec` (prompt piped on stdin, not argv) |
| Google | `AgyAdapter` | `agy --print "<prompt>"` |
| local / open-weight | `LocalAdapter` | HTTP `POST {endpoint}/v1/chat/completions` (Ollama, llama.cpp, vLLM, LM Studio) — stdlib `urllib`, no subprocess |
| Anthropic (hosted API) | `AnthropicApiAdapter` | HTTP `POST api.anthropic.com/v1/messages`, keyed by `ANTHROPIC_API_KEY` — stdlib `urllib`, no subprocess, no CLI needed |
| OpenAI (hosted API) | `OpenAiApiAdapter` | HTTP `POST api.openai.com/v1/chat/completions`, keyed by `OPENAI_API_KEY` — stdlib `urllib`, no subprocess, no CLI needed |
| Google (hosted API) | `GoogleApiAdapter` | HTTP `POST generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`, keyed by `GEMINI_API_KEY` (header, not the `?key=...` query form) — stdlib `urllib`, no subprocess, no CLI needed |
| OpenAI-Compatible (hosted API) | `GenericOpenAICompatibleAdapter` | HTTP `POST {endpoint}/chat/completions` (OpenRouter, DeepSeek, Groq, Mistral, LiteLLM) — stdlib `urllib`, custom `api_key_env` & `headers` |
| Arbitrary CLI Agents | `GenericCLIAdapter` | Subprocess CLI adapter (Aider, Goose, OpenHands) with `prompt_mode = "stdin"` or `"arg"`, secret redaction & stderr error classification |
| Custom Registered Vendors | `register_adapter()` | Dynamically registered Python adapter classes via `ai_jury.adapters.register_adapter("my-vendor", MyAdapter)` |
| — (tests) | `MockAdapter` | deterministic, phase-aware output; no subprocess |

Adapters fail soft: a missing CLI, **non-zero exit** (even with stdout), timeout, an
unreachable local endpoint (`connection_error`), or an unset hosted-API key
(`missing_api_key`) becomes a non-fatal `AgentResult(ok=False, error=…)`. The run
continues with whoever is available unless `--strict` is passed; the report lists
skipped / failed / retried / timed-out agents.

`--strict` checks **availability at startup**. An agent that is installed, probes
clean, and then returns nothing still leaves the panel short — and cross-vendor
consensus is the premise, so a run that collapses to one vendor is a different
thing wearing the same output. `--min-vendors N` fails such a run (exit 3,
distinct from a findings failure), counting **distinct vendors that contributed a
review**: three agents from one vendor are one perspective, and an abstention is
not one at all. It is off by default — failing closed on a flaky vendor CLI turns
a degraded second opinion into no second opinion, which is a trade for whoever
runs the panel to make.

**Read-only by default (secure):** reviewers read attacker-controlled diffs, so the
shipped defaults run them sandboxed — Claude with `--disallowed-tools
Edit,Write,NotebookEdit,Bash`, Codex with `-s read-only`, Antigravity with
`--sandbox`. `privilege.py` audits this and warns (or fails under `--strict`) when an
agent is given broad powers without a sandbox. `local` and hosted-API agents are out of
scope for that audit entirely — they run no subprocess and have no filesystem/tool
access to disallow in the first place, so there is nothing to sandbox.

A `local` agent is a normal `[[agent]]` with `vendor = "local"`, an `endpoint`
(default `http://localhost:11434/v1`), and a `model`; it adds vendor diversity at
zero marginal cost and enables fully offline reviews.

A **hosted-API agent** is a normal `[[agent]]` with `vendor = "anthropic-api"`,
`vendor = "openai-api"`, or `vendor = "google-api"` and a `model` — no `command`, no
CLI install, no interactive login. The API key comes from the environment only
(`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`), never from `jury.toml`,
so it can't leak into a checked-in config; the endpoint is a fixed, non-configurable
constant per vendor (unlike `local`'s user-supplied `endpoint`, so there is no SSRF
surface to validate — the Gemini adapter's URL depends on `model`, but only to select
*which* fixed path segment, never an operator- or attacker-supplied host). This is the
lowest-friction reviewer seat for CI/containers where installing and interactively
authenticating an agent CLI is impractical but an API key as a secret is trivial —
see `jury init --agents claude-api,codex-api,gemini-api`.

## Design decisions

- **Native CLI over API, with a hosted-API escape hatch.** The differentiator for the
  primary review adapters is that each reviewer runs in its own vendor agent with its
  own tooling and context handling, not a raw model API call — but a review prompt
  needs no filesystem/tool access at all (the jury fetches the diff itself), so the
  hosted-API adapters (issue #430) trade that native tooling for zero-install,
  API-key-only reviewers where a CLI genuinely can't be installed or authenticated
  (CI, containers).
- **Stdlib only.** No third-party deps — `subprocess`, `tomllib`, `concurrent.futures`,
  `argparse`. Easy to vendor into any repo or CI.
- **Mock path is first-class.** `--mock` runs the entire pipeline deterministically so
  tests and CI never need credentials or token spend.
- **Orchestrator owns prompts.** Keeps the round structure auditable in one file and
  makes adapters trivially swappable.

## Repository review policy

The runtime configuration in `jury.toml` describes *how the agent runs* (which
agents/vendors make up the jury, rounds, chair). A repository under review may
additionally ship an **optional, separate review policy** that describes *what
reviewers should care about* for that project. The two are kept deliberately
distinct: a different file and a different loader (`policy.py`).

A policy file is plain TOML, discovered (when `--policy` is not given) from the
current working directory in this order:

1. `.jury/policy.toml`
2. `jury-policy.toml`

A missing policy is allowed and is a no-op (the loader returns `None`). A policy
file that exists but is malformed raises a clear `PolicyError`. An explicit
`--policy PATH` that does not exist is treated as a user error and also raises.

### Schema

All fields are optional:

| Field | Type | Meaning |
| --- | --- | --- |
| `high_risk_paths` | list of strings | Paths to review with extra care. |
| `focus_areas` | list of strings | Required review focus areas. |
| `forbidden_output` | list of strings | Output behaviours reviewers must avoid. |
| `checklist` | string | Free-form project review checklist text. |
| `doc_links` | list of strings | Docs reviewers should consider. |
| `severity_overrides` | list of `{ glob, severity }` tables | Severity overrides for matching path patterns. |

### Trusted injection

Unlike the diff and free-form context (which are untrusted and wrapped in the
`<<<UNTRUSTED_DIFF` / `<<<UNTRUSTED_CONTEXT` sentinel fences), the policy is
authored by the repository maintainers and is therefore treated as **trusted**.
It is injected into the REVIEW prompt in a clearly labelled section
("`=== REPOSITORY REVIEW POLICY (maintainer-provided, TRUSTED) ===`") that sits
*outside* the untrusted fences, so it can refine review priorities without being
subject to the prompt-injection defences applied to the change under review. The
policy support is fully project-agnostic and hardcodes no project names; see
`examples/policy.toml` for a generic example.

## Supported platforms

CI proves the package on a deliberately small matrix (see
[`.github/workflows/ci.yml`](../.github/workflows/ci.yml)):

| OS | Python | Notes |
|:--|:--|:--|
| Ubuntu (latest) | 3.11, 3.12, 3.13 | Full version coverage; primary CI target. |
| macOS (latest) | 3.13 | Latest Python only, to keep the matrix cheap. |
| Windows (latest) | 3.13 | Unit tests + mock smoke test run here. The mock path is fully cross-platform. |

OS-specific notes:

- The **mock path** (`--mock`) is pure-Python and behaves identically on all three OSes.
- **Live agent CLIs** (`claude`, `codex`, `agy`) and `gh` are spawned as subprocesses.
  Their availability and behavior on Windows depend on each vendor's own Windows
  support; the orchestrator itself is OS-agnostic and fails soft when a CLI is missing.
- Only the latest supported Python is exercised on macOS/Windows; the full
  3.11–3.13 sweep runs on Linux. The supported range is declared once in
  `pyproject.toml` (`requires-python`) and mirrored by this matrix and the README.

### CI & runners

The repository is **public**, so GitHub-hosted runner minutes are free and
unlimited. The **authoritative** per-push / per-PR signal is the hosted
[`ci.yml`](../.github/workflows/ci.yml) matrix: the stdlib-only unit tests and
the mock smoke test across ubuntu/macOS/windows × Python 3.11–3.13, plus a
dedicated coverage gate (`fail_under` in `pyproject.toml`).

There is **no self-hosted runner**. An earlier self-hosted macOS runner was
removed when the repo went public: running untrusted forked-PR code on a
maintainer's machine is an arbitrary-code-execution risk, and with free hosted
minutes there is no reason to keep it. Security scanning runs per-commit too:

| Workflow | Runner | Trigger |
|:--|:--|:--|
| CI (cross-OS matrix + coverage) | GitHub-hosted | push + PR — **authoritative** |
| CodeQL | GitHub-hosted | push + PR + weekly `schedule` |
| OpenSSF Scorecard | GitHub-hosted | push to `main` + weekly `schedule` |
| Deploy website (Pages) | GitHub-hosted | push to `main` (+ manual dispatch) |
| Publish to PyPI + Release | GitHub-hosted | `v*` tag (OIDC trusted publishing) |

## Implemented capabilities

The prior-art research priorities (see [`feasibility.md`](feasibility.md)) have
landed; the current pipeline includes:

1. **Verify/audit pass** — the chair re-judges each finding to drop false positives and
   recalibrate before synthesis (`verify = true`). *(biggest quality multiplier)*
2. **Structured findings + tiered consensus** — reviewer output is parsed into a
   `Finding` schema and grouped by `{severity, file, line, claim}` into
   consensus / majority / single-reviewer tiers (`findings.py`, `consensus.py`).
3. **Anonymized rebuttal** — round 2 strips agent identity/order to curb position bias;
   the chair's own review is anonymized in synthesis too.
4. **Convergence-based early stop** — adaptive rounds halt once agents agree
   (`early_stop`, `max_rounds`; `convergence.py`).
5. **Severity-gated CI exit codes** — `jury --ci` exits non-zero on blocking findings
   (`ci.py`).

Also shipped since: a **local / open-weight adapter** (free, offline reviews); a run
**budget + retries + partial-result** policy; **large-diff** filtering and chunking; an
optional **result cache**; **incremental** review of updated PRs; opt-in **suggested
patches**; allowlisted **comment-command** triggering; and **`jury init`** config
scaffolding with local-model discovery. See [`configuration.md`](configuration.md) and
[`cookbook.md`](cookbook.md) for usage.

Still future:

- **A2A transport** — wrap each agent as an A2A server only to admit remote third-party
  agents; unnecessary for local subprocess / HTTP orchestration.


## PR-level classification

After consensus, the jury derives a compact, **deterministic** PR-level
classification from the structured findings, the consensus groups, and
(optionally) the unified diff. It lives in
`ai_jury/classification.py` and never calls an LLM or the network,
so identical inputs always yield identical output (it is golden-tested under the
mock pipeline).

### Fields

| field | type | meaning |
| --- | --- | --- |
| `review_effort` | int 1-5 | how much reviewer effort the PR likely needs |
| `risk_level` | `low` / `medium` / `high` | severity of the issues found |
| `security_sensitive` | bool | the change looks security-relevant |
| `needs_human_attention` | bool | a human should look before merging |

### Formulas

**`risk_level`** — from the most severe finding:

* `high` — any `critical` finding, OR any `major` finding that is part of a
  confirmed consensus/majority group (not rejected/unsupported).
* `medium` — any other `major` finding (single-reviewer/unverified), or any
  `minor` finding.
* `low` — only `nit`/`info` findings, or no findings at all.

**`review_effort`** (clamped to 1-5):

    base = 1
    + finding count:   >= 8 -> +2,  >= 3 -> +1
    + severity spread: any critical/major -> +2, else any minor -> +1
    + diff size:       > 400 changed lines -> +2, > 80 -> +1

Each term only ever raises the score, so it is monotonic-ish in count,
severity, and diff size. Changed lines are counted from the unified diff
(added/removed lines, excluding `+++`/`---` headers).

**`security_sensitive`** — true if any finding has severity `critical`, OR any
of the `SECURITY_KEYWORDS` tokens (e.g. `injection`, `xss`, `csrf`, `ssrf`,
`rce`, `traversal`, `secret`, `credential`, `token`, `auth`, `deserialization`,
`sanitize`, `vulnerab`, `exploit`, `privilege`) appears in a finding's claim,
evidence, suggested fix, or file path. Matching is case-insensitive and
word-boundary anchored, so `auth` does **not** fire inside `author`. The
synthetic injection-scanner finding is caught via the keyword path.

**`needs_human_attention`** — true if `risk_level` is `high`, OR
`security_sensitive` is true, OR any consensus group is disputed / needs a human
decision.

### Where it surfaces

* The markdown report includes a compact `## Classification` summary line, e.g.
  `review effort: 4/5 · risk: high · security-sensitive: no · needs human
  attention: yes`.
* `build_run_metadata` embeds the classification dict, so it appears in
  `--metadata-json` output and in the JSON report (`to_json`), both at the top
  level (`classification`) and inside `metadata`.

### Optional GitHub labels (opt-in)

Labeling is **off by default** and never applied automatically. Pass `--label`
together with `--pr` to apply classification labels to the pull request via
`gh pr edit --add-label`. The labels are derived from the classification:

* `review effort: N/5`
* `risk: low|medium|high`
* `possible security issue` (only when `security_sensitive`)
* `needs human attention` (only when `needs_human_attention`)
