# 🏛️ agent-review-council

> Convene a **cross-vendor multi-agent review council**: native coding-agent CLIs from
> different vendors review the *same* pull request, cross-examine each other, and a
> chair synthesizes one verdict.

[![CI](https://github.com/berkayturanci/agent-review-council/actions/workflows/ci.yml/badge.svg)](https://github.com/berkayturanci/agent-review-council/actions/workflows/ci.yml)
[![CodeQL](https://github.com/berkayturanci/agent-review-council/actions/workflows/codeql.yml/badge.svg)](https://github.com/berkayturanci/agent-review-council/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/berkayturanci/agent-review-council/badge)](https://scorecard.dev/viewer/?uri=github.com/berkayturanci/agent-review-council)
[![GitHub release](https://img.shields.io/github/v/release/berkayturanci/agent-review-council)](https://github.com/berkayturanci/agent-review-council/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

![A diff or PR enters; three native vendor agents — Claude Code, Codex, and Antigravity — review it independently and debate each other's findings; a chair agent verifies and synthesizes one verdict (APPROVE / COMMENT / REQUEST CHANGES) plus a markdown report.](docs/assets/hero.png)

> **Install once. Run a cross-vendor review council anywhere.**

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

## Data flow / privacy

What gets sent to each agent is governed by `[council.context]` in
`council.toml` (and overridable per run on the CLI):

```toml
[council.context]
mode = "diff-only"      # "diff-only" (default) or "expanded"
redact_secrets = true   # scrub recognized secrets before sending (default on)
```

**Context modes** — what leaves your machine for each reviewer:

- `diff-only` (**default**): agents receive **only the diff**. Any surrounding PR
  context (title/body) is dropped. This is the smallest data surface.
- `expanded`: agents additionally receive the PR title/body context (when
  reviewing with `--pr`) to improve review quality. Use this only when you trust
  the configured agent endpoints.

Either way, no source files outside the diff, no repository history, and no
environment variables are read or sent.

**Secret redaction** — before anything is sent to an agent, the diff (and any
context) is passed through a redactor (`src/agent_review_council/redaction.py`)
that masks recognized secrets: PEM private keys, AWS access keys, GitHub/OpenAI
tokens, `Bearer` tokens, and generic `api_key`/`secret`/`token` assignments
(including base64-style values). Each hit becomes `[REDACTED:<kind>]`. Redaction
is **on by default**.

**Controls:**

- `council.toml`: `[council.context] mode = "diff-only"|"expanded"` and
  `redact_secrets = true|false`.
- CLI (override config for a single run): `--context-mode diff-only|expanded`,
  and `--redact` / `--no-redact`.

Posting to GitHub (`--post-summary`, `--post-inline`) sends the rendered report
/ comments to the GitHub API; use `--dry-run` with `--post-inline` to preview the
inline payload without any network call. See [SECURITY.md](SECURITY.md) for the
full data-flow and redaction reference.

## Use it from another project (skill)

A Claude Code skill ships in [`skill/review-council/`](skill/review-council/SKILL.md).
Install it as a **plugin** from this repo (it doubles as a single-plugin marketplace):

```text
/plugin marketplace add berkayturanci/agent-review-council
/plugin install review-council@agent-review-council
```

Or drop [`skill/review-council/`](skill/review-council/SKILL.md) into a project's
`.claude/skills/` manually. Either way the agent can convene the council on demand, and
it composes with existing review workflows: run the council for a cross-vendor pass,
then act on the consensus findings. For other platforms (Codex, Antigravity, CI) and
their support status, see the [platform support matrix](docs/platforms.md).

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
workflow. See the [ecosystem comparison & capability matrix](docs/comparison.md) for how
it differs from hosted, API-level, and other native-CLI tools, and
[`docs/feasibility.md`](docs/feasibility.md) for the supporting research.

## Status

MVP (v0.1). Cross-vendor round 1 (review) + round 2 (debate) run end-to-end with the real
CLIs; the offline `--mock` path is covered by tests. Roadmap (from the research): a
verify/audit pass that re-reads code to kill false positives, JSON structurizer +
tiered consensus (consensus / majority / individual), anonymized rebuttal to curb
position bias, and severity-gated CI exit codes. See [`docs/architecture.md`](docs/architecture.md).

The phased plan and how to pick up a session's worth of work is in [`ROADMAP.md`](ROADMAP.md);
issues are tracked under [milestones](https://github.com/berkayturanci/agent-review-council/milestones).

## Security & the Codex sandbox

The council performs **read-only review orchestration** — it sends a diff to each agent CLI and collects their feedback; it does not apply edits.

The Codex adapter pipes the prompt on **stdin** (`codex exec` with no positional prompt) so non-interactive runs never hang waiting for input, and defaults `extra_args` to `["-s", "danger-full-access"]`. Codex's standard sandbox can block the outbound network / `gh` access that PR review needs, which would surface as failures instead of findings; full access keeps runs reliable without changing what the tool itself does. (Avoid `--full-auto`, which implies a stricter workspace sandbox.)

Want tighter sandboxing? Override `extra_args` for the `codex` agent in `council.toml` (e.g. a narrower `-s` mode). See [docs/security.md](docs/security.md) for details.

## Documentation

- [Workflow cookbook](docs/cookbook.md) — copy-paste recipes: local-branch review, PR review & report saving, advisory PR comments, soft/CI review stage, mock smoke tests, one/two-agent setups.
- [Architecture](docs/architecture.md) — components, round structure, adapters, supported platforms.
- [Ecosystem comparison](docs/comparison.md) — capability matrix vs hosted / API-level / native-CLI tools.
- [Feasibility & prior art](docs/feasibility.md) — research grounding and verified CLI invocations.
- [Platform support matrix](docs/platforms.md) — where you can install/run the council and how.
- [Release readiness checklist](docs/release-checklist.md) — the bar before a public release.
- [SECURITY.md](SECURITY.md) — data-flow and secret-redaction reference.
- Agent-readable: [`llms.txt`](llms.txt) (concise) and [`llms-full.txt`](llms-full.txt) (full reference).

## License

MIT — see [LICENSE](LICENSE).
