# Platform support matrix

> Install once. Run a cross-vendor review jury anywhere.

`ai-jury` is a single Python CLI (`jury`) plus a Claude Code skill. The
goal of this page is to make that same capability easy to install where you already
work — **not** to become a generic MCP/hook platform. The scope stays on ai-jury
orchestration.

## Status legend

- **supported** — first-class, documented, exercised.
- **manual** — works today by invoking the `jury` CLI directly; no platform-native
  packaging yet.
- **planned** — intended once the platform exposes a stable skill/plugin mechanism.
- **out of scope** — deliberately not pursued.

## Matrix

| Platform | Status | How you install / invoke | Prerequisites |
|:--|:--|:--|:--|
| **Claude Code** (plugin) | supported | `/plugin marketplace add berkayturanci/ai-jury` → `/plugin install ai-jury@ai-jury` | `jury`, ≥1 agent CLI, `gh` |
| **Claude Code** (manual skill) | supported | Copy [`skill/ai-jury/`](../skill/ai-jury/SKILL.md) into a project's `.claude/skills/` | `jury`, ≥1 agent CLI, `gh` |
| **Any shell / CI** | supported | Run the CLI: `jury --pr <n>` or `jury --ci --fail-on high,critical` | `jury`, ≥1 agent CLI, `gh` (for `--pr`) |
| **OpenAI Codex CLI** | manual / planned | Invoke `jury` from a Codex session or `AGENTS.md`; native skill manifest planned when Codex stabilizes one (see template below) | `jury`, `codex`, `gh` |
| **Google Antigravity / Gemini CLI** | manual | Invoke `jury` from the agent session | `jury`, `agy`, `gh` |
| **Other IDE/agent CLIs** (Cursor, etc.) | manual | Run the `jury` CLI from the integrated terminal | `jury`, ≥1 agent CLI |
| **Hosted SaaS install** | out of scope | — (this is a local-first tool, not a hosted product) | — |

Prerequisites in detail:

- **`jury`** — `pipx install ai-jury` (entry point on PATH). Run
  `jury init` to scaffold a `jury.toml`.
- **A reviewer** — at least one agent CLI (`claude`, `codex`, `agy`) **or** a free,
  offline **local / open-weight** model via Ollama or any OpenAI-compatible server
  (configured as a `vendor = "local"` agent). Missing/unreachable agents are skipped
  unless `--strict`.
- **`gh`** — GitHub CLI, authenticated, for `--pr` input and `--post-*` output.

`jury --doctor` checks these prerequisites and reports what is missing (per-agent
availability + versions); `jury init --list-agents` / `--list-models` do the same for
config setup.

## Claude Code plugin

This repository doubles as a single-plugin Claude Code marketplace. The manifests live
in [`.claude-plugin/`](../.claude-plugin/):

- `marketplace.json` — declares the `ai-jury` marketplace with one plugin.
- `plugin.json` — declares the `ai-jury` plugin and points at the existing
  [`skill/`](../skill) directory (no skill is duplicated or moved).

Install:

```text
/plugin marketplace add berkayturanci/ai-jury
/plugin install ai-jury@ai-jury
```

The plugin only bundles the ai-jury skill; it does not register hooks, MCP
servers, or unrelated commands.

## Codex CLI (template / manual)

Codex does not yet have a stable, documented plugin manifest equivalent to Claude Code's
`plugin.json`. Until it does, expose the jury to a Codex session by invoking the CLI.
A minimal `AGENTS.md` snippet you can drop into a repo:

```markdown
## Review jury

To run a cross-vendor review of the current branch, call:

    git diff origin/HEAD... | jury --diff-file -

Report the chair verdict and consensus findings.
```

When Codex ships a first-class skill/plugin format, a native manifest will be added here
and the matrix status updated from *manual / planned* to *supported*. The capability —
running `jury` — is identical across platforms; only the packaging differs.

## What this is not

- Not a generic agent-hooking or MCP platform.
- Not a hosted review service.
- Not a promise of support for platforms that cannot safely run a local CLI — those stay
  *manual* or *out of scope* rather than being listed as supported.
