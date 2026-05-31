# Security Policy

## Supported Versions

Only the latest released version of **agent-review-council** is supported with security
updates.

| Version | Supported |
| ------- | --------- |
| >= 0.1.0 | Yes |
| < 0.1.0 | No |

## Reporting a Vulnerability

Please do **not** open a public issue for security vulnerabilities. Report them
privately to the maintainer:

- Email: [berkayturanci@gmail.com](mailto:berkayturanci@gmail.com)

Please include a clear description, reproduction steps, potential impact, and any
suggested fix. We will acknowledge the report within 48 hours when possible.

## Security Notes

This tool invokes local agent CLIs and may pass PR diffs or repository context to those
tools. Review your configured agent CLIs, authentication state, and `council.toml`
before running it on sensitive repositories.

## Data flow & privacy

What leaves your machine depends on the context policy and redaction settings. These
controls also exist in the README ("Data flow / privacy"); they are restated here so
the security-relevant behaviour is documented in one place.

**What is sent to each agent.** By default only the unified **diff** is passed to the
configured agent CLIs (`claude`, `codex`, `agy`). No repository files outside the diff
and no PR metadata are included unless you opt in.

**Context modes.** Set via `[council.context] mode` in `council.toml` or `--context-mode`
on the CLI:

- `diff-only` (default) — only the diff is sent.
- `expanded` — the PR title and body are added as extra context for the reviewers.

**Secret redaction.** Enabled by default (`[council.context] redact_secrets = true`) and
toggleable with `--redact` / `--no-redact`. When enabled, the diff and any added context
are scrubbed **before** they are sent to any agent. The following secret shapes are
redacted:

- AWS access key IDs (`AKIA…`)
- GitHub tokens (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`)
- OpenAI-style API keys (`sk-…`)
- `Bearer` authorization tokens
- PEM private key blocks
- generic `api_key` / `secret` / `token` assignments

Redaction is best-effort pattern matching, not a guarantee. Treat the agent CLIs you
configure as trusted, and avoid running the council on repositories whose diffs may
contain secrets you cannot afford to expose even after redaction.
