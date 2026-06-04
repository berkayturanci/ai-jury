# Feasibility & Prior Art

Research grounding for `ai-jury`, gathered before/while building the MVP
(2026). Two questions: **does cross-vendor multi-agent code review actually work**, and
**has someone already built this?**

## TL;DR

- **The thesis holds.** Heterogeneous (different-vendor) models reviewing the same code
  and debating measurably improves bug detection. This is the one research-backed
  advantage to lean on.
- **It is not greenfield.** A close production-grade project already exists —
  **[Magpie](https://github.com/liliu-z/magpie)** — plus several adjacent tools. This
  project differentiates on simplicity (stdlib-only, drop-in skill) and on integrating
  with an existing repo's review workflow, not on inventing the concept.
- **Use plain subprocess orchestration; skip A2A for the MVP.** Every shipping tool in
  this space shells out to vendor CLIs directly.

## All three vendor CLIs run headless (verified locally, early 2026)

| Vendor | Command | Notes |
|:--|:--|:--|
| Claude Code | `claude -p "<prompt>" --output-format text\|json` | `--output-format json` returns a structured object (`result`, `usage`, `total_cost_usd`). In `-p` mode add `--dangerously-skip-permissions` (or restrict tools) so it never blocks on a permission prompt. |
| OpenAI Codex | `codex exec "<prompt>"` (`--json`, `--output-schema`) | Requires a trusted git dir or `--skip-git-repo-check`. Known bug: `--json`/`--output-schema` are ignored when MCP servers are active (openai/codex#15451) — disable MCP for review runs. |
| Antigravity | `agy --print "<prompt>"` (`--print-timeout`, default 5m) | Auto-approves with `--dangerously-skip-permissions`. Note: Antigravity CLI is replacing Gemini CLI for unpaid/Google One tiers around 2026-06-18 — pin the invocation path. |

## Prior art (closest first)

| Project | What it is | Closeness |
|:--|:--|:--|
| **[Magpie](https://github.com/liliu-z/magpie)** | TS CLI; multi-vendor adversarial PR review **with debate rounds**, shelling out to claude-code/codex/gemini/qwen. Author also published [a benchmark](https://milvus.io/blog/ai-code-review-gets-better-when-models-debate-claude-vs-gemini-vs-codex-vs-qwen-vs-minimax.md) on 15 real bug-shipping PRs: **80% bug detection after debate; 100% on the hardest system-level bugs**, well above single-model. | Essentially this concept. Study before extending. |
| **[agent-jury](https://github.com/yogirk/agent-jury)** | Bun/TS skill; convenes claude/codex/gemini, 4 stages: independent → anonymized peer review → chairman synthesis → nudge. `--with-review` for audits. | Very close (general deliberation + review). |
| **[the-jury](https://github.com/DantesPeak85/the-jury)** | Claude Code skill; Claude chairs, invokes Codex + Gemini in read-only sandboxes, synthesizes. | Close; no rebuttal round. |
| **[Star Chamber](https://blog.mozilla.ai/the-star-chamber-multi-llm-consensus-for-code-quality/)** (Mozilla.ai) | Multi-LLM consensus skill / `uvx star-chamber`, optional `--debate --rounds N`. API-level (`any-llm`), not vendor CLIs. | Same pattern, different transport. |
| **[reviewd](https://github.com/simion/reviewd)** | Python; single-reviewer PR review via one CLI. | Adjacent (no debate). |

Originating pattern: Karpathy's [llm-jury](https://github.com/karpathy/llm-jury)
(respond → peer-review anonymized → chairman synthesizes).

## What the research says about debate/consensus

- **Heterogeneity is the real lever.** "Should we be going MAD?" (Smit et al., ICML 2024,
  [arxiv 2311.17371](https://arxiv.org/abs/2311.17371)) finds multi-agent debate does *not*
  reliably beat ensembling at equal cost — but **model diversity helps**. Same-model /
  different-prompt is the documented failure mode; different vendors is the win.
- **Debate improves factuality/reasoning** — Du et al., ICML 2024,
  [arxiv 2305.14325](https://arxiv.org/abs/2305.14325).
- **Diverse jury > single judge, ~7× cheaper** — Cohere PoLL,
  [arxiv 2404.18796](https://arxiv.org/abs/2404.18796).
- **Position bias is real and debate can amplify it** —
  [arxiv 2505.19477](https://arxiv.org/abs/2505.19477). Mitigation: anonymize peer
  feedback (Chatham House), strip ordering.
- **Rounds plateau at 2–3; cost ~15× a single chat.** Stop early on convergence.

**Design implications baked into this MVP:** heterogeneous vendors by default; debate is
one extra round (opt-out via `--rounds 1`); chair synthesis rather than naive voting.
Convergence-based early stop, a verify pass, anonymized peer feedback, and an optional
panel vote (`--decision vote`) all shipped since; a rotating anonymized-rebuttal turn
order remains a roadmap item.

## A2A protocol — not for the MVP

A2A is mature (Linux Foundation, `a2a-python` SDK, samples) but **no reference impl wraps
a CLI agent as an A2A server** — you'd hand-write the executor, agent card, and an ASGI
server per agent to get the exact `subprocess → capture → parse` we already have, plus
network surface. Every shipping tool uses direct subprocess. Revisit A2A only to admit
remote third-party agents.

## Parsing: don't parse the prose

Prompt-only "respond in JSON" fails 5–20% of the time (fences, preamble, wrappers). The
robust pattern (Magpie's, and the one we implement):

1. Reviewer produces prose (ideally with tool access to read real files, not diff-only).
2. A separate **structurizer** step converts prose → JSON `{severity, file, line, category}`
   using the CLI's native machine output (`claude --output-format json`,
   `codex --output-schema`).
3. A **verify/audit pass** re-reads the actual code per finding to drop false positives /
   by-design issues and recalibrate severity. *This is the single biggest quality
   multiplier.*
4. Aggregate by structured fields (severity+location) into consensus / majority /
   individual tiers — not fuzzy text matching.

## MVP run observations (this environment)

Building and running the MVP locally with the three real CLIs:

- ✅ Round 1 (independent review) and Round 2 (cross-examination) completed across all
  three vendors in parallel — the **core cross-vendor orchestration works**.
- ✅ Offline `--mock` pipeline + unit tests pass deterministically (no creds / tokens).
- ⚠️ `claude -p` synthesis on a *large* combined prompt was slow / occasionally stalled
  in this sandbox; mitigations: a smaller/faster chair, shorter synthesis context, or
  `--output-format json`. Per-agent `timeout` bounds any stall and the run still renders.
- ⚠️ `codex exec` needs a trusted git dir (`--skip-git-repo-check`).
- ⚠️ `agy` headless needs `--dangerously-skip-permissions`; some sandboxes block that
  flag — run agy where autonomous approval is permitted.

These are environmental/operational quirks, not design problems — captured here so the
next person wiring real CLIs knows the sharp edges up front.
