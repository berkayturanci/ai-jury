# Installing ai-jury into an agent

The [README's Install section](../README.md#install) covers the **`jury` CLI** —
Homebrew, the curl installer, pipx. This page covers the other half: getting
ai-jury in front of an agent as a **plugin**, so its skill is available inside a
session rather than only on your `$PATH`.

The two are independent. The plugin gives an agent the skill; the skill shells
out to `jury`, so **the CLI has to be installed either way**.

> Agent names appear throughout the README as *reviewers the jury convenes* —
> `claude`, `codex`, `agy`, `cursor` on the panel. That is a different role from
> the one on this page, where the same agent is the **host** ai-jury is installed
> into. An agent can be one, the other, or both.

Every command below was run against the tooling on a real machine on
**2026-09-09**, with ai-jury **1.17.1**. Where a claim could not be exercised
end to end, it says so.

## Contents

- [Claude Code](#claude-code)
- [Codex](#codex)
- [Antigravity (`agy`)](#antigravity)
- [Cursor](#cursor)

---

## Claude Code

<a id="claude-code"></a>

The repository doubles as a single-plugin marketplace, so the marketplace is
added once and the plugin installed from it.

**Install**

```bash
claude plugin marketplace add https://github.com/berkayturanci/ai-jury
claude plugin install ai-jury@ai-jury
```

**Update**

```bash
claude plugin marketplace update ai-jury
claude plugin update ai-jury@ai-jury
```

Two things are worth knowing, both measured:

- **`claude plugin install` is a no-op on an installed plugin.** It prints
  `✔ Plugin "ai-jury@ai-jury" is already installed (scope: user)` and changes
  nothing, so it is not an upgrade path.
- **`plugin update` needs the qualified `name@marketplace`.** The bare name
  fails — `✘ Failed to update plugin "ai-jury": Plugin "ai-jury" not found`,
  exit status 1 — while `ai-jury@ai-jury` answers
  `✔ ai-jury is already at the latest version (1.17.1).`

`claude plugin update` reports that a restart is required to apply the change.
Confirm what you have with `claude plugin list`.

---

## Codex

<a id="codex"></a>

**Install**

```bash
codex plugin marketplace add https://github.com/berkayturanci/ai-jury
codex plugin add ai-jury@ai-jury
```

Two steps: adding the marketplace registers the source, it does not install the
plugin.

**Update**

```bash
codex plugin marketplace upgrade
codex plugin add ai-jury@ai-jury
```

`codex plugin marketplace upgrade` is the documented refresh — *"Refresh
configured Git marketplace snapshots"* — and `plugin add` then installs from the
refreshed snapshot. `codex plugin list` shows what is installed and from which
marketplace.

---

## Antigravity

<a id="antigravity"></a>

**Install**

```bash
agy plugin install https://github.com/berkayturanci/ai-jury
agy plugin enable ai-jury
```

`install` alone leaves the plugin **disabled**; the `enable` is not optional.

**Update**

```bash
agy plugin install https://github.com/berkayturanci/ai-jury
```

It overwrites in place and keeps the enabled flag.

**Where it lands.** `~/.gemini/config/plugins/ai-jury/`, not the
`~/.gemini/antigravity-cli/plugins/` path Antigravity's own documentation
suggests. Verified by listing that directory.

**What agy actually reads.** Components are discovered by **root-directory
convention only** — a root `skills/` directory, and likewise `commands/`,
`agents/`, `mcp_config.json`, `hooks.json`. No manifest path field is consulted.
That is why [#775](https://github.com/berkayturanci/ai-jury/issues/775) had
`agy plugin install` report `[ok]` while importing nothing: the skill lived in a
directory the convention does not name, so every component was skipped and the
install still exited 0. The directory is `skills/` now, which is the name agy
looks for.

---

## Cursor

<a id="cursor"></a>

**Cursor has no CLI install command.** `cursor-agent plugin` exposes only
`marketplace` (`add`, `list`, `remove`, `update`) — there is no
`cursor-agent plugin install`. So there are two routes, and the local checkout is
the one with a straightforward update.

**Install — local checkout (recommended)**

```bash
git clone --depth 1 https://github.com/berkayturanci/ai-jury \
  ~/.cursor/plugins/local/ai-jury
```

Restart Cursor. It appears in **Settings → Plugins** as `ai-jury (Local)`.

**Update — local checkout**

```bash
git -C ~/.cursor/plugins/local/ai-jury pull
```

Restart Cursor.

**Install — marketplace**

```bash
cursor-agent plugin marketplace add https://github.com/berkayturanci/ai-jury
```

Then install it from Cursor's `/plugins` screen. `cursor-agent plugin
marketplace update <nameOrUrl>` re-indexes the marketplace from its git
repository.

---

## What each route actually registers

A **locally installed** Cursor plugin registers **skills only** — no commands, no
subagents, no MCP servers. That is Cursor's local-plugin behaviour rather than
anything about this repository's manifest; a plugin declaring no components at
all behaves the same way. If you need more than the skill in Cursor, the
marketplace route is the one that registers it.

## See also

- [`docs/platforms.md`](platforms.md) — the support matrix, per surface.
- [`docs/skill.md`](skill.md) — what the skill does once it is installed.
- [`README.md`](../README.md#install) — installing the `jury` CLI itself.
