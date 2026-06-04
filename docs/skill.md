# Publishing & installing the ai-jury skill

> The CLI is the stable executable surface. The skill is the assistant-facing layer
> that knows *when* and *how* to invoke that CLI.

`ai-jury` ships two things that travel together:

- the **`jury` CLI** — the stable, testable executable surface; and
- the **ai-jury skill** — a small `SKILL.md` that teaches a coding agent when to
  convene the jury and how to read back the result.

This page covers the skill as a reusable artifact: its layout, how to install it into
Codex/Claude-compatible skill folders, the external tools it expects, how its version
relates to the CLI, worked examples, and a smoke-test checklist. For *where* the jury
runs across platforms (and the support status of each), see the
[platform support matrix](platforms.md); for the Claude Code plugin manifests, see
[`.claude-plugin/`](../.claude-plugin/).

## Skill directory layout

The skill is a self-contained directory under [`skill/`](../skill):

```text
skill/
└── ai-jury/
    └── SKILL.md      # YAML front matter (name, description) + instructions
```

`SKILL.md` is the entire artifact. Its front matter declares the skill `name`
(`ai-jury`) and a `description` that tells the host agent when to trigger it
("review jury", "convene the jury", "cross-model review", a PR/diff/branch review).
The body documents prerequisites, the command table, what to report back, and how the
skill composes with an existing review workflow.

Nothing else is required for the skill to work — it carries no code of its own; it drives
the `jury` CLI. The repository's [`.claude-plugin/plugin.json`](../.claude-plugin/plugin.json)
points its `skills` field at this same `skill/` directory, so the plugin install path and
the manual copy path serve the identical artifact (no duplication).

## Install into a skill folder

The skill is platform-portable because every host ultimately runs the same `jury`
command. Two install routes exist; pick by host.

### Claude Code (plugin — recommended)

This repo doubles as a single-plugin marketplace. Install the bundled skill as a plugin:

```text
/plugin marketplace add berkayturanci/ai-jury
/plugin install ai-jury@ai-jury
```

The manifests that make this work are in [`.claude-plugin/`](../.claude-plugin/)
(`marketplace.json` + `plugin.json`); they are documented in the
[platform matrix](platforms.md#claude-code-plugin) and not repeated here.

### Claude Code (manual skill copy)

Copy the directory into the host project's skill folder:

```bash
cp -R skill/ai-jury <your-project>/.claude/skills/ai-jury
```

### Codex / other Claude-compatible skill folders

Codex does not yet expose a stable plugin manifest equivalent. Until it does, install the
skill the same way — copy `skill/ai-jury/` into the host's skill directory — or
reference the `jury` command from an `AGENTS.md`. The
[Codex template in the platform matrix](platforms.md#codex-cli-template--manual) shows the
minimal `AGENTS.md` snippet; the underlying capability (running `jury`) is identical
across hosts, only the packaging differs.

## Required external tools

The skill itself adds no dependencies beyond what the CLI needs. At review time the host
must have:

- **`jury`** — required. The CLI entry point on `PATH`
  (`pipx install ai-jury`). The skill is inert without it.
- **`gh`** — required only for GitHub-sourced or GitHub-posted runs (`--pr`,
  `--issue`, `--post-summary` / `--post-inline`). Must be authenticated.
- **`claude` / `codex` / `agy`** — optional native agent CLIs. At least **one** must be
  installed for a live review; missing CLIs are skipped automatically (unless `--strict`).
  The jury runs with whoever is available.

The same prerequisite detail lives in the [platform matrix](platforms.md); a future
`jury doctor` command will check these per host.

## Versioning policy (skill ↔ CLI)

The skill is versioned to track **CLI compatibility**, not to advertise new prose:

- The skill version follows the CLI/plugin version in
  [`.claude-plugin/plugin.json`](../.claude-plugin/plugin.json). A consumer reading the
  skill version knows which `jury` CLI it was written against.
- A **breaking CLI change** — a renamed/removed flag, a changed command surface, or
  changed output contract that the skill instructs the agent to rely on — **bumps the
  skill version**. The skill must never instruct an agent to call a flag the pinned CLI
  no longer accepts.
- Additive, backward-compatible CLI changes (new optional flags the skill does not yet
  use) do not require a skill bump.
- Wording-only edits to `SKILL.md` that do not change which commands the agent runs are
  not breaking and do not force a version bump.

In short: the skill version is a compatibility marker for the CLI it drives. Skill
behavior changes are called out in release notes alongside the CLI change that motivated
them.

## Examples

All examples assume `jury` is on `PATH` and at least one agent CLI is installed.

### PR review

Review a GitHub pull request and report the verdict:

```bash
jury --pr 123
```

Post the verdict back as a comment (requires authenticated `gh`) — works for a
PR (`gh pr comment`) or an issue (`gh issue comment`):

```bash
jury --pr 123 --post
```

### Issue review

Point the same jury at a GitHub **issue** to judge it for completeness and
clarity (reproduction steps, expected vs actual, scope, missing context). The
verdict vocabulary is **READY / NEEDS-INFO / UNCLEAR**:

```bash
jury --issue 42                 # print the completeness verdict
jury --issue 42 --post          # comment it back on the issue
jury --issue 42 --decision vote # decide by panel vote (NEEDS-INFO > UNCLEAR > READY)
```

### Diff-file review

Review the current branch against the default branch, or a saved diff:

```bash
git diff origin/HEAD... | jury --diff-file -
jury --diff-file path/to/changes.diff
```

### Advisory ship/review integration

The jury is an **advisory** cross-vendor pass that composes with — and does not
replace — a host project's own review workflow. A typical pattern: run the jury first
for a cross-vendor read, surface its consensus findings, then let the project's existing
reviewers (or a ship gate) act on them.

```bash
# advisory cross-vendor pass before the project's own gate
git diff origin/HEAD... | jury --diff-file - -o jury-report.md
```

The skill reports the chair verdict (APPROVE / COMMENT / REQUEST CHANGES for a PR
or diff; READY / NEEDS-INFO / UNCLEAR for an `--issue`), the consensus
findings (issues 2+ agents agreed on, with `path:line`), and any disputed findings worth
a human decision — leaving the actual ship/block decision to the host workflow.

## Smoke-test checklist

A minimal pass to confirm the skill is installed and wired correctly:

- [ ] `jury --help` runs (CLI is on `PATH`).
- [ ] The skill directory is present in the host's skill folder
      (`.claude/skills/ai-jury/SKILL.md`), or the plugin shows as installed.
- [ ] Offline dry run produces a report with no live CLIs:
      `jury --mock --diff-file examples/sample.diff`
- [ ] At least one agent CLI is resolvable (`claude`, `codex`, or `agy`), or a live run is
      not expected.
- [ ] For PR runs: `gh auth status` is authenticated.
- [ ] Invoking the skill by name/trigger ("convene the jury") prompts the host agent to
      run the matching `jury` command and report the verdict + consensus findings.
