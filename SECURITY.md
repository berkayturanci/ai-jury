# Security Policy

## Supported Versions

Only the latest released version of **ai-jury** is supported with security
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
tools. Review your configured agent CLIs, authentication state, and `jury.toml`
before running it on sensitive repositories.

## Jury data flow & redaction

What the jury sends to each configured agent is deliberately narrow.

**By default, only the diff is sent.** In the default `diff-only` context mode each
agent receives the unified diff under review and nothing else. In `expanded` mode the
agent additionally receives the PR title and body. In **neither** mode does the jury
send:

- source files outside the diff,
- repository history (commits, branches, blame), or
- environment variables or shell state.

**Secret redaction is on by default.** Before any text is handed to an agent, it is
scanned by `redaction.py` and recognized secrets are replaced with
`[REDACTED:<kind>]`. The recognized secret shapes are:

| Kind | What it matches |
| ---- | --------------- |
| `pem_private_key` | PEM private-key blocks (`-----BEGIN ... PRIVATE KEY-----` … `-----END ... PRIVATE KEY-----`, incl. RSA/EC/OPENSSH/DSA/PGP) |
| `aws_access_key` | AWS access key IDs (`AKIA` + 16 chars) |
| `github_token` | GitHub tokens (`ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_` …) |
| `openai_key` | OpenAI-style keys (`sk-` …) |
| `bearer_token` | `Bearer <token>` authorization values |
| `secret_assignment` | Generic `api_key` / `secret` / `token` assignments (`key = "…"`, the value is redacted, the key and separator preserved) |

**Toggles.**

- `--context-mode {diff-only,expanded}` — choose what context is sent (default
  `diff-only`).
- `--redact` / `--no-redact` — enable or disable secret redaction. Redaction is on by
  default; `--no-redact` sends the raw text and is intended only for trusted local use.
- The same behavior can be configured under `[jury.context]` in `jury.toml`
  (e.g. `mode = "expanded"`, `redact_secrets = true`).

The `--doctor` diagnostics report is independent of this path: it never includes the
diff or any agent output, and applies the same redaction to config values it prints
(see below).

## Diagnostics & telemetry

This project collects and transmits **no telemetry** of any kind — there is no
analytics, no usage reporting, and no opt-in data collection. The tool never
phones home; the only network activity is performed by the agent CLIs you
explicitly configure (and `gh` for `--pr` / `--post*`).

The `jury --doctor` command produces a local diagnostics report intended to
be safe to share when filing a bug report. It includes the tool/Python/OS
versions, a config summary (rounds, chair, context mode, enabled agents) with
secret-like values redacted via `redaction.py`, agent availability on `PATH`,
and detected config warnings. It **never** includes the diff under review or any
agent output. The report is printed locally and only written to disk when you
pass `jury --doctor --write PATH`.
