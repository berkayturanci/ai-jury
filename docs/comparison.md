# Ecosystem comparison

Where `agent-review-council` sits among adjacent code-review tools. The goal is to help
you pick the right tool — not to claim superiority. Several tools below are mature,
hosted products with capabilities this project deliberately does **not** try to match.

This is a point-in-time snapshot (2026) based on public docs and the research in
[`feasibility.md`](feasibility.md); capabilities of other projects change, so verify
against their current docs before deciding.

## Categories

- **Native-CLI orchestration** — drives each vendor's *own* coding-agent CLI
  (`claude`, `codex`, `agy`/`gemini`, `qwen`, …) as a subprocess. Each reviewer runs in
  its native environment. *This project, Magpie, agent-council, the-council.*
- **API-level multi-model** — calls models through provider/aggregator APIs rather than
  their CLIs. *Star Chamber (Mozilla.ai).*
- **Hosted PR reviewers** — managed SaaS that reviews PRs in your repo with a dashboard,
  policies, and inline comments. *e.g. CodeRabbit, Greptile, GitHub Copilot code review.*
- **Single-reviewer CLI** — one model reviews a PR, no debate. *e.g. reviewd.*

## Capability matrix

Legend: ✅ yes · ➖ partial / optional · ❌ no · — not applicable.

| Capability | agent-review-council | Native-CLI peers (Magpie / agent-council / the-council) | API-level (Star Chamber) | Hosted PR reviewers |
|:--|:--:|:--:|:--:|:--:|
| Native CLI execution (per-vendor agent) | ✅ | ✅ | ❌ | ❌ |
| API-level model calls | ❌ | ❌ | ✅ | ✅ |
| Multiple vendors / models | ✅ | ✅ | ✅ | ➖ |
| Consensus / debate rounds | ✅ | ✅ (Magpie, agent-council) | ➖ (`--debate`) | ➖ |
| Verification pass (re-read code) | ✅ | ➖ | ❌ | ➖ |
| Structured findings (severity/file/line) | ✅ | ➖ | ➖ | ✅ |
| Inline PR comments | ✅ (`--post-inline`) | ➖ | ❌ | ✅ |
| CI gating (non-zero on blocking) | ✅ (`--ci`, `--fail-on`) | ➖ | ❌ | ✅ |
| Suggested fixes / auto-patch | ❌ | ➖ | ❌ | ✅ |
| Hosted dashboard | ❌ | ❌ | ❌ | ✅ |
| Local-first (no data leaves to a SaaS) | ✅ | ✅ | ➖ | ❌ |
| Secret redaction before send | ✅ | ➖ | ➖ | n/a (hosted) |
| Dependency footprint | ✅ stdlib-only | ➖ (Node/Bun runtimes) | ➖ (Python + `any-llm`) | — (hosted) |
| Project-specific review policy | ➖ (`council.toml` + prompts) | ➖ | ➖ | ✅ |
| Skill drop-in for an existing repo | ✅ (Claude Code skill) | ➖ (the-council, agent-council are skills) | ➖ | ❌ |

Entries for other projects are intentionally conservative; where a project's support is
configurable or undocumented it is marked ➖. Corrections via PR are welcome.

## When to use what

- **Use a hosted PR reviewer** (CodeRabbit, Greptile, Copilot review) when you want a
  zero-maintenance, dashboard-driven product with auto-fix suggestions and are
  comfortable sending code to a third-party service. These are more mature than this
  project and solve a broader product surface.
- **Use an API-level tool** (Star Chamber) when you want multi-model consensus but
  prefer provider APIs over installing vendor CLIs.
- **Use `agent-review-council`** when you specifically want each reviewer to run as a
  *native vendor CLI agent* (its own tooling/context), want a **local-first**,
  **stdlib-only** drop-in that snaps into an existing repo's review workflow via a
  Claude Code skill, and want debate + a verification pass + CI gating without a hosted
  service. It is intentionally **small in scope**: not a hosted product, not a general
  multi-agent framework.

## Gaps that map to the roadmap

Honest gaps relative to mature products, each tracked as a roadmap issue/milestone:

- **Suggested fixes / auto-patch** — not implemented; this tool reports, it does not
  rewrite.
- **Hosted dashboard / website** — landing/docs site is roadmap
  ([#14](https://github.com/berkayturanci/agent-review-council/issues/14)); there is no
  hosted review SaaS and that is a non-goal.
- **Richer project-specific policy** — currently `council.toml` + prompt templates;
  deeper rule support is future work.
- **Plugin/distribution maturity** — platform support matrix and manifests are roadmap
  ([#45](https://github.com/berkayturanci/agent-review-council/issues/45)).

See the [milestones](https://github.com/berkayturanci/agent-review-council/milestones)
for the current plan.
