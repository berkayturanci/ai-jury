# ai-jury — Gemini / Antigravity Entry Point

[`AGENTS.md`](AGENTS.md) is the canonical cross-vendor instruction file for this
repository.

Gemini CLI and **Antigravity** should read it first — it holds the durable rules: the
zero-runtime-dependency constraint, the read-only-by-default posture reviewers depend on,
the pure-core / thin-I/O split, the module map, and the git/PR workflow. Antigravity reads
`AGENTS.md` natively; this file is the Gemini-family pointer to it.

Then, for the task at hand:

- **The skill** — [`docs/skill.md`](docs/skill.md) documents the artifact, where it lives
  in this repository, and how to install it. It is a document *about* the skill, not the
  skill file itself.
- **Config contract** — [`docs/configuration.md`](docs/configuration.md) for `jury.toml`.
- **Report shape** — [`docs/report-format.md`](docs/report-format.md).

Antigravity is one of the vendor seats this project convenes, so a change to
`adapters.py`'s `AgyAdapter` or to `tests/test_agy_stream_protocol.py` is a change to how
this repository reviews itself.

Installing the skill on Antigravity is **manual** — see the platform table in
[`docs/platforms.md`](docs/platforms.md), which is the one file that says what each host
supports. This one deliberately makes no such claim of its own: an entry point that
promises more than the host does is worse than one that says nothing, because an agent
that believes it will not go and look.

Keep durable rules in `AGENTS.md` instead of duplicating them here.
