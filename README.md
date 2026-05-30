# 🏛️ agent-review-council

> Convene a **cross-vendor multi-agent review council**: native coding-agent CLIs from
> different vendors review the *same* pull request, cross-examine each other, and a
> chair synthesizes one verdict.

[![CI](https://github.com/berkayturanci/agent-review-council/actions/workflows/ci.yml/badge.svg)](https://github.com/berkayturanci/agent-review-council/actions/workflows/ci.yml)
[![GitHub release](https://img.shields.io/github/v/release/berkayturanci/agent-review-council)](https://github.com/berkayturanci/agent-review-council/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Most "multi-model review" tools call models at the **API level**. This one drives each
vendor's **native CLI agent** — `claude` (Claude Code), `codex` (OpenAI Codex CLI), and
`agy` (Google Antigravity) — so every reviewer runs in its own native environment with
its own tooling. Each agent runs headless; the orchestrator owns the round structure.

```
        ┌──────── round 1 ────────┐   ┌──── round 2 ────┐   ┌── synthesis ──┐
diff ──▶ claude  codex  agy  (review) ▶ each rebuts the   ▶ chair agent ▶ verdict
         (parallel, independent)         others' findings    consolidates    + report
```

## Why

Different models miss different things. Running them as an adversarial panel — each
seeing the others' findings and arguing — surfaces more real issues and filters more
false positives than any single reviewer. The research-backed lever is **vendor
heterogeneity**, not more rounds. See [`docs/architecture.md`](docs/architecture.md)
and [`docs/feasibility.md`](docs/feasibility.md).

## Install

```bash
pipx install agent-review-council         # or: pip install -e .
```

Requires Python 3.11+. Install at least one agent CLI (`claude`, `codex`, `agy`);
missing ones are skipped automatically. `gh` is needed for `--pr` / `--post`.

## Usage

```bash
council --pr 123                          # review a GitHub PR
council --pr 123 --post                   # ...and post the verdict as a comment
git diff origin/HEAD... | council --diff-file -   # review the current branch
council --diff-file examples/sample.diff  # review a diff file
council --rounds 1                        # independent review only (no debate)
council --mock --diff-file examples/sample.diff   # offline demo, no live CLIs
```

A sample report is in [`docs/example-run.md`](docs/example-run.md).

## Configuration — `council.toml`

```toml
[council]
rounds = 2          # 1 = review only, 2 = review + debate
chair  = "claude"   # which agent synthesizes the verdict
timeout = 300       # per-agent wall-clock seconds (a hung CLI is killed at this bound)
parallel = true

[[agent]]
name = "claude"
vendor = "anthropic"   # anthropic | openai | google
command = "claude"
# model = "claude-opus-4-8"
extra_args = ["--output-format", "text", "--disallowed-tools", "Edit,Write,NotebookEdit,Bash", "--dangerously-skip-permissions"]
```

Override per run with `--rounds`, `--chair`, `--config`.

## Use it from another project (skill)

A Claude Code skill ships in [`skill/review-council/`](skill/review-council/SKILL.md).
Drop it into a project's `.claude/skills/` and the agent can convene the council on
demand. It composes with existing review workflows: run the council for a cross-vendor
pass, then act on the consensus findings.

## How it works

| Module | Responsibility |
|:--|:--|
| `config.py` | Load `council.toml` (or built-in default) |
| `adapters.py` | One adapter per vendor CLI; turns a prompt into a headless subprocess |
| `orchestrator.py` | Round structure: review → debate → synthesis (agents run in parallel) |
| `prompts.py` | The three prompt templates |
| `report.py` | Render the run as one markdown report |
| `github.py` | `gh`-based PR diff in / comment out |

## Prior art & how this differs

This is a known pattern, not a new invention. The closest project is
**[Magpie](https://github.com/liliu-z/magpie)** (multi-vendor CLI review + debate, with a
[benchmark](https://milvus.io/blog/ai-code-review-gets-better-when-models-debate-claude-vs-gemini-vs-codex-vs-qwen-vs-minimax.md)
showing debate lifts bug detection to ~80%); see also
[agent-council](https://github.com/yogirk/agent-council),
[the-council](https://github.com/DantesPeak85/the-council), and Mozilla.ai's
[Star Chamber](https://blog.mozilla.ai/the-star-chamber-multi-llm-consensus-for-code-quality/).
`agent-review-council` aims to be the **smallest drop-in** version: stdlib-only Python, a
single `council.toml`, and a Claude Code skill that snaps into an existing repo's review
workflow. Full comparison and supporting research in [`docs/feasibility.md`](docs/feasibility.md).

## Status

MVP (v0.1). Cross-vendor round 1 (review) + round 2 (debate) run end-to-end with the real
CLIs; the offline `--mock` path is covered by tests. Roadmap (from the research): a
verify/audit pass that re-reads code to kill false positives, JSON structurizer +
tiered consensus (consensus / majority / individual), anonymized rebuttal to curb
position bias, and severity-gated CI exit codes. See [`docs/architecture.md`](docs/architecture.md).

The phased plan and how to pick up a session's worth of work is in [`ROADMAP.md`](ROADMAP.md);
issues are tracked under [milestones](https://github.com/berkayturanci/agent-review-council/milestones).

## License

MIT — see [LICENSE](LICENSE).
