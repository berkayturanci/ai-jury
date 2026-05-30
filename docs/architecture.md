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
