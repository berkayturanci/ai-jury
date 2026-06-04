# Ecosystem comparison

Where `ai-jury` sits among adjacent code-review tools. The goal is to help
you pick the right tool — not to claim superiority. Several tools below are mature,
hosted products with capabilities this project deliberately does **not** try to match.

This is a point-in-time snapshot (2026) based on public docs and the research in
[`feasibility.md`](feasibility.md); capabilities of other projects change, so verify
against their current docs before deciding.

## Categories

- **Native-CLI orchestration** — drives each vendor's *own* coding-agent CLI
  (`claude`, `codex`, `agy`/`gemini`, `qwen`, …) as a subprocess. Each reviewer runs in
  its native environment. *This project, Magpie, agent-jury, the-jury.*
- **API-level multi-model** — calls models through provider/aggregator APIs rather than
  their CLIs. *Star Chamber (Mozilla.ai).*
- **Hosted PR reviewers** — managed SaaS that reviews PRs in your repo with a dashboard,
  policies, and inline comments. *e.g. CodeRabbit, Greptile, GitHub Copilot code review.*
- **Single-reviewer CLI** — one model reviews a PR, no debate. *e.g. reviewd.*

## Capability matrix

Legend: ✅ yes · ➖ partial / optional · ❌ no · — not applicable.

| Capability | ai-jury | Native-CLI peers (Magpie / agent-jury / the-jury) | API-level (Star Chamber) | Hosted PR reviewers |
|:--|:--:|:--:|:--:|:--:|
| Native CLI execution (per-vendor agent) | ✅ | ✅ | ❌ | ❌ |
| API-level model calls | ❌ | ❌ | ✅ | ✅ |
| Multiple vendors / models | ✅ | ✅ | ✅ | ➖ |
| Consensus / debate rounds | ✅ | ✅ (Magpie, agent-jury) | ➖ (`--debate`) | ➖ |
| Verification pass (re-read code) | ✅ | ➖ | ❌ | ➖ |
| Panel voting verdict (tally vs. single chair) | ✅ (`--decision vote`) | ❌ | ❌ | ❌ |
| Issue-quality review (completeness, not diffs) | ✅ (`--issue`) | ❌ | ❌ | ❌ |
| Structured findings (severity/file/line) | ✅ | ➖ | ➖ | ✅ |
| Inline PR comments | ✅ (`--post-inline`) | ➖ | ❌ | ✅ |
| CI gating (non-zero on blocking) | ✅ (`--ci`, `--fail-on`) | ➖ | ❌ | ✅ |
| Suggested fixes (inspectable, no auto-apply) | ➖ (`--suggest-patches`, verified findings only) | ➖ | ❌ | ✅ |
| Incremental review (changed-since-last-run) | ✅ (`--incremental`) | ❌ | ❌ | ✅ |
| Risk-aware auto-depth (scale rounds to the diff) | ✅ (`--auto` / `auto_depth`) | ❌ | ❌ | ➖ |
| Live progress on the PR (sticky / phased comments) | ✅ (`--post-progress`, `--post-mode phased`) | ❌ | ❌ | ➖ |
| Comment-triggered runs (`/jury …`) | ➖ (`jury comment` + workflow recipe) | ➖ | ❌ | ✅ |
| Offline / local open-weight reviewer | ✅ (`vendor = "local"`, Ollama/etc., $0) | ➖ | ➖ | ❌ |
| Guided config setup | ✅ (`jury init`, `--wizard`) | ❌ | ❌ | n/a (hosted) |
| Hosted dashboard | ❌ | ❌ | ❌ | ✅ |
| Local-first (no data leaves to a SaaS) | ✅ | ✅ | ➖ | ❌ |
| Secret redaction before send | ✅ | ➖ | ➖ | n/a (hosted) |
| Dependency footprint | ✅ stdlib-only | ➖ (Node/Bun runtimes) | ➖ (Python + `any-llm`) | — (hosted) |
| Project-specific review policy | ✅ (`.jury/policy.toml`) | ➖ | ➖ | ✅ |
| Skill drop-in for an existing repo | ✅ (Claude Code skill) | ➖ (the-jury, agent-jury are skills) | ➖ | ❌ |

Entries for other projects are intentionally conservative; where a project's support is
configurable or undocumented it is marked ➖. Corrections via PR are welcome.

## When to use what

- **Use a hosted PR reviewer** (CodeRabbit, Greptile, Copilot review) when you want a
  zero-maintenance, dashboard-driven product with auto-fix suggestions and are
  comfortable sending code to a third-party service. These are more mature than this
  project and solve a broader product surface.
- **Use an API-level tool** (Star Chamber) when you want multi-model consensus but
  prefer provider APIs over installing vendor CLIs.
- **Use `ai-jury`** when you specifically want each reviewer to run as a
  *native vendor CLI agent* (its own tooling/context), want a **local-first**,
  **stdlib-only** drop-in that snaps into an existing repo's review workflow via a
  Claude Code skill, and want debate + a verification pass + CI gating without a hosted
  service. It can also run **fully offline at $0** with a local open-weight model
  (`vendor = "local"`), or mix one local seat with cloud CLIs for more vendor diversity.
  It is intentionally **small in scope**: not a hosted product, not a general
  multi-agent framework.

## Gaps relative to mature products

Honest gaps, and where they now stand:

- **Auto-apply fixes** — by design this tool does not rewrite code. It can emit
  *inspectable* suggested patches for **verified** findings (`--suggest-patches`), but
  never applies them automatically.
- **Hosted dashboard / website** — a landing/docs site exists under `website/`; there is
  no hosted review SaaS, and that is a deliberate non-goal.
- **Project-specific policy** — supported via `.jury/policy.toml` (high-risk paths,
  focus areas, severity overrides); deeper rule engines remain out of scope.

See the [milestones](https://github.com/berkayturanci/ai-jury/milestones)
for anything still open.
