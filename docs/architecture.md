# Architecture

`agent-review-council` orchestrates **native coding-agent CLIs from different vendors**
into an adversarial review panel over a single diff. The design goal is a clean
separation between *what to ask* (orchestrator + prompts) and *how to invoke a given
agent* (adapters), so new vendors are a ~20-line addition.

## Components

```
                       ┌──────────────────────────────────────────┐
   diff / PR  ───────▶ │              orchestrator                 │
   (gh / file / stdin) │  round 1 review → round 2 debate → synth  │
                       └───────┬───────────────┬──────────────┬────┘
                               │ prompts.py     │              │
                       ┌───────▼──────┐  ┌──────▼─────┐  ┌─────▼──────┐
                       │ ClaudeAdapter│  │CodexAdapter│  │ AgyAdapter │   (adapters.py)
                       │  claude -p   │  │ codex exec │  │ agy --print│
                       └──────────────┘  └────────────┘  └────────────┘
                               │                │              │
                               └───── subprocess (headless, parallel) ─────┘
                                                │
                                         report.py → markdown → stdout / -o / gh comment
```

## Round structure

1. **Round 1 — independent review.** Every available agent reviews the diff in
   isolation with the same rubric (correctness → security → regressions → tests).
   Agents run concurrently (thread pool; each call is an IO-bound subprocess).
2. **Round 2 — cross-examination (optional, `rounds = 2`).** Each agent receives the
   diff, its own review, and every *other* agent's review, then emits `AGREE` /
   `DISPUTE` / `MISSED`. This is where cross-model disagreement filters false
   positives and surfaces gaps. Only agents whose round-1 call succeeded participate;
   debate needs ≥2 of them.
3. **Synthesis.** The configured `chair` agent consolidates all rounds into a single
   verdict (`APPROVE` / `COMMENT` / `REQUEST CHANGES`) plus consensus, disputed, and
   notable single-reviewer findings. If the chair is unavailable, the first available
   agent chairs.

## Adapters

Each adapter knows only how to invoke one CLI headlessly; the orchestrator owns prompt
content. Verified headless invocations (early 2026):

| Vendor | Adapter | Invocation |
|:--|:--|:--|
| Anthropic | `ClaudeAdapter` | `claude -p "<prompt>" --output-format text` |
| OpenAI | `CodexAdapter` | `codex exec "<prompt>"` |
| Google | `AgyAdapter` | `agy --print "<prompt>"` |
| — (tests) | `MockAdapter` | deterministic, phase-aware output; no subprocess |

Adapters fail soft: a missing CLI, non-zero exit, or timeout becomes a non-fatal
`AgentResult(ok=False, error=…)`. The run continues with whoever is available unless
`--strict` is passed. Read-only safety is enforced per vendor via `extra_args`
(e.g. Claude's `--disallowed-tools Edit,Write,...`).

## Design decisions

- **Native CLI over API.** The differentiator is that each reviewer runs in its own
  vendor agent with its own tooling and context handling, not a raw model API call.
- **Stdlib only.** No third-party deps — `subprocess`, `tomllib`, `concurrent.futures`,
  `argparse`. Easy to vendor into any repo or CI.
- **Mock path is first-class.** `--mock` runs the entire pipeline deterministically so
  tests and CI never need credentials or token spend.
- **Orchestrator owns prompts.** Keeps the round structure auditable in one file and
  makes adapters trivially swappable.

## Repository review policy

The runtime configuration in `council.toml` describes *how the agent runs* (which
agents/vendors make up the council, rounds, chair). A repository under review may
additionally ship an **optional, separate review policy** that describes *what
reviewers should care about* for that project. The two are kept deliberately
distinct: a different file and a different loader (`policy.py`).

A policy file is plain TOML, discovered (when `--policy` is not given) from the
current working directory in this order:

1. `.council/policy.toml`
2. `council-policy.toml`

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

## Roadmap

Prioritized from the prior-art research (see [`feasibility.md`](feasibility.md)):

1. **Verify/audit pass** — after findings are collected, a tool-equipped agent re-reads
   the actual code per finding to drop false positives / by-design issues and recalibrate
   severity. Identified as the single biggest quality multiplier (Magpie).
2. **JSON structurizer + tiered consensus** — capture machine output
   (`claude --output-format json`, `codex --output-schema`) via a dedicated prose→JSON
   step, then aggregate by `{severity, file, line, category}` into
   consensus / majority / individual tiers instead of free-text.
3. **Anonymized rebuttal** — strip agent identity/order in round 2 to curb position bias
   (which debate can amplify).
4. **Convergence-based early stop** — halt extra rounds once agents agree (cost control;
   multi-agent ≈ 15× single-chat tokens).
5. **Severity-gated CI exit codes** (`REQUEST CHANGES` → non-zero).
6. **A2A transport** (later) — wrap each agent as an A2A server only to admit remote
   third-party agents; unnecessary for local subprocess orchestration.
