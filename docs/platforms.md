# Platform support matrix

> Install once. Run a cross-vendor review council anywhere.

`agent-review-council` is a single Python CLI (`council`) plus a Claude Code skill. The
goal of this page is to make that same capability easy to install where you already
work — **not** to become a generic MCP/hook platform. The scope stays on review-council
orchestration.

## Status legend

- **supported** — first-class, documented, exercised.
- **manual** — works today by invoking the `council` CLI directly; no platform-native
  packaging yet.
- **planned** — intended once the platform exposes a stable skill/plugin mechanism.
- **out of scope** — deliberately not pursued.

## Matrix

| Platform | Status | How you install / invoke | Prerequisites |
|:--|:--|:--|:--|
| **Claude Code** (plugin) | supported | `/plugin marketplace add berkayturanci/agent-review-council` → `/plugin install review-council@agent-review-council` | `council`, ≥1 agent CLI, `gh` |
| **Claude Code** (manual skill) | supported | Copy [`skill/review-council/`](../skill/review-council/SKILL.md) into a project's `.claude/skills/` | `council`, ≥1 agent CLI, `gh` |
| **Any shell / CI** | supported | Run the CLI: `council --pr <n>` or `council --ci --fail-on high,critical` | `council`, ≥1 agent CLI, `gh` (for `--pr`) |
| **OpenAI Codex CLI** | manual / planned | Invoke `council` from a Codex session or `AGENTS.md`; native skill manifest planned when Codex stabilizes one (see template below) | `council`, `codex`, `gh` |
| **Google Antigravity / Gemini CLI** | manual | Invoke `council` from the agent session | `council`, `agy`, `gh` |
| **Other IDE/agent CLIs** (Cursor, etc.) | manual | Run the `council` CLI from the integrated terminal | `council`, ≥1 agent CLI |
| **Hosted SaaS install** | out of scope | — (this is a local-first tool, not a hosted product) | — |

Prerequisites in detail:

- **`council`** — `pipx install agent-review-council` (entry point on PATH).
- **Agent CLIs** — at least one of `claude` (Claude Code), `codex` (OpenAI Codex CLI),
  `agy` (Google Antigravity). Missing CLIs are skipped automatically unless `--strict`.
- **`gh`** — GitHub CLI, authenticated, for `--pr` input and `--post-*` output.

A future `council doctor` command will check these prerequisites per platform and report
what is missing (tracked in
[#34](https://github.com/berkayturanci/agent-review-council/issues/34)).

## Claude Code plugin

This repository doubles as a single-plugin Claude Code marketplace. The manifests live
in [`.claude-plugin/`](../.claude-plugin/):

- `marketplace.json` — declares the `agent-review-council` marketplace with one plugin.
- `plugin.json` — declares the `review-council` plugin and points at the existing
  [`skill/`](../skill) directory (no skill is duplicated or moved).

Install:

```text
/plugin marketplace add berkayturanci/agent-review-council
/plugin install review-council@agent-review-council
```

The plugin only bundles the review-council skill; it does not register hooks, MCP
servers, or unrelated commands.

## Codex CLI (template / manual)

Codex does not yet have a stable, documented plugin manifest equivalent to Claude Code's
`plugin.json`. Until it does, expose the council to a Codex session by invoking the CLI.
A minimal `AGENTS.md` snippet you can drop into a repo:

```markdown
## Review council

To run a cross-vendor review of the current branch, call:

    git diff origin/HEAD... | council --diff-file -

Report the chair verdict and consensus findings.
```

When Codex ships a first-class skill/plugin format, a native manifest will be added here
and the matrix status updated from *manual / planned* to *supported*. The capability —
running `council` — is identical across platforms; only the packaging differs.

## What this is not

- Not a generic agent-hooking or MCP platform.
- Not a hosted review service.
- Not a promise of support for platforms that cannot safely run a local CLI — those stay
  *manual* or *out of scope* rather than being listed as supported.
