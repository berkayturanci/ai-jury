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

## Diagnostics & telemetry

This project collects and transmits **no telemetry** of any kind — there is no
analytics, no usage reporting, and no opt-in data collection. The tool never
phones home; the only network activity is performed by the agent CLIs you
explicitly configure (and `gh` for `--pr` / `--post*`).

The `council --doctor` command produces a local diagnostics report intended to
be safe to share when filing a bug report. It includes the tool/Python/OS
versions, a config summary (rounds, chair, context mode, enabled agents) with
secret-like values redacted via `redaction.py`, agent availability on `PATH`,
and detected config warnings. It **never** includes the diff under review or any
agent output. The report is printed locally and only written to disk when you
pass `council --doctor --write PATH`.
