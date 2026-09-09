# Platform support matrix

> Install once. Run a cross-vendor review jury anywhere.

`ai-jury` is a single Python CLI (`jury`) plus a skill, packaged for four agent hosts —
Claude Code, Codex, Antigravity and Cursor ([install.md](install.md)). The
goal of this page is to make that same capability easy to install where you already
work — **not** to become a generic MCP/hook platform. The scope stays on ai-jury
orchestration.

## Status legend

- **supported** — first-class, documented, exercised.
- **manual** — works today by invoking the `jury` CLI directly; no platform-native
  packaging yet.
- **out of scope** — deliberately not pursued.

## Matrix

| Platform | Status | How you install / invoke | Prerequisites |
|:--|:--|:--|:--|
| **Claude Code** (plugin) | supported | `/plugin marketplace add berkayturanci/ai-jury` → `/plugin install ai-jury@ai-jury`, or the CLI equivalents. Updating is **not** a re-install; see [install.md](install.md#claude-code) | `jury`, ≥1 agent CLI, `gh` |
| **Claude Code** (manual skill) | supported | Copy [`skills/ai-jury/`](../skills/ai-jury/SKILL.md) into a project's `.claude/skills/` | `jury`, ≥1 agent CLI, `gh` |
| **Any shell / CI** | supported | Run the CLI: `jury --pr <n>` or `jury --ci --fail-on critical,major` | `jury`, ≥1 agent CLI, `gh` (for `--pr`) |
| **OpenAI Codex CLI** | supported (plugin) | `codex plugin marketplace add https://github.com/berkayturanci/ai-jury` → `codex plugin add ai-jury@ai-jury`. See [install.md](install.md#codex) | `jury`, `codex`, `gh` |
| **Google Antigravity / Gemini CLI** | supported (plugin) | `agy plugin install https://github.com/berkayturanci/ai-jury` **then** `agy plugin enable ai-jury` — `install` alone leaves it disabled. agy finds the root `skills/` directory by convention (#775); it reads no manifest path. See [install.md](install.md#antigravity) | `jury`, `agy`, `gh` |
| **Cursor** | supported (local plugin) | `git clone --depth 1 <repo> ~/.cursor/plugins/local/ai-jury`, restart Cursor. There is **no** `cursor-agent plugin install`; the marketplace route registers more than the local one. See [install.md](install.md#cursor). `.cursor-plugin/plugin.json` carries the fields Cursor's plugin reference documents — `logo`, its listing asset, included — and names the same root `skills/` directory as the other manifests; it changes how the plugin *presents*, not what works, and the GUI listing has not been confirmed from here | `jury`, ≥1 agent CLI |
| **Other IDE/agent CLIs** | manual | Run the `jury` CLI from the integrated terminal | `jury`, ≥1 agent CLI |
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
- `plugin.json` — declares the `ai-jury` plugin and points at the
  [`skills/`](../skills) directory, which is also where Antigravity finds it by
  root-directory convention (#775). One directory serves every agent; nothing is
  duplicated per platform.

Install, in a session:

```text
/plugin marketplace add berkayturanci/ai-jury
/plugin install ai-jury@ai-jury
```

**Updating is not a re-install** — `plugin install` is a no-op on an installed
plugin. That command, and the other three agents, are in
[install.md](install.md#claude-code).

The plugin only bundles the ai-jury skill; it does not register hooks, MCP
servers, or unrelated commands.

## Codex CLI

Codex has a plugin marketplace now, and `.codex-plugin/plugin.json` is this
repository's manifest for it — `codex plugin marketplace add` then
`codex plugin add ai-jury@ai-jury`, with the exact commands and the update path in
[install.md](install.md#codex). The matrix row moved from *manual / planned* to
*supported (plugin)* accordingly.

The CLI route below still works and needs no plugin at all, which is what makes it
useful in a container or a CI job. A minimal `AGENTS.md` snippet you can drop into
a repo:

```markdown
## Review jury

To run a cross-vendor review of the current branch, call:

    git diff origin/HEAD... | jury --diff-file -

Report the chair verdict and consensus findings.
```

The capability — running `jury` — is identical across platforms; only the packaging
differs. Installing ai-jury as a plugin into any of the four agents is documented in
one place: [install.md](install.md).

## What this is not

- Not a generic agent-hooking or MCP platform.
- Not a hosted review service.
- Not a promise of support for platforms that cannot safely run a local CLI — those stay
  *manual* or *out of scope* rather than being listed as supported.
