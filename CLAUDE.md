# ai-jury — Claude Code Entry Point

[`AGENTS.md`](AGENTS.md) is the canonical cross-vendor instruction file for this
repository.

Claude Code should read it first — it holds the durable rules: the zero-runtime-dependency
constraint, the read-only-by-default posture reviewers depend on, the pure-core / thin-I/O
split, the module map, and the git/PR workflow.

Then, for the task at hand:

- **The skill** — [`docs/skill.md`](docs/skill.md), installed from the plugin manifest in
  [`.claude-plugin/`](.claude-plugin/plugin.json).
- **Config contract** — [`docs/configuration.md`](docs/configuration.md) for `jury.toml`.
- **Report shape** — [`docs/report-format.md`](docs/report-format.md).

Keep durable rules in `AGENTS.md` instead of duplicating them here.
