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
