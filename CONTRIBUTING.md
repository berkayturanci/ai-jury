# Contributing to agent-review-council

Thank you for your interest in contributing to **agent-review-council**. The goal of
this project is to keep cross-vendor code review orchestration small, inspectable,
and easy to run from existing repositories.

## Code of Conduct

By participating in this project, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Governance and maintenance

How the project is maintained — triage, release cadence, the compatibility and
deprecation policy, how major roadmap decisions are made, and how
project-specific requests are routed — is documented in
[MAINTAINERS.md](MAINTAINERS.md). Support is best-effort and the project makes no
service-level promise.

## Reporting Bugs

Before opening a bug report, please search existing issues. Include:

1. The command you ran.
2. Your operating system and Python version.
3. Which agent CLIs are installed (`claude`, `codex`, `agy`) and their versions if known.
4. Whether the issue reproduces with `--mock`.
5. Any relevant stderr/stdout with secrets removed.

## Requesting Features

Feature requests are welcome. Please describe the review workflow you want to support,
why the current CLI does not cover it, and whether the feature should be generic or
implemented as a project-specific wrapper outside this package.

## Pull Requests

1. Fork the repository and create a branch from `main`.
2. Set up a local environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   make install
   ```
3. Run the checks before submitting:
   ```bash
   make test
   make smoke
   make lint
   ```
4. Update documentation and tests when behavior changes.
5. Keep changes project-agnostic. Do not add references to a downstream repository,
   private workflow, or organization-specific process unless it is clearly framed as
   an external example.

## Dependency and tooling updates

This project pins GitHub Actions to commit SHAs and keeps its runtime dependency
footprint at zero. Updates are proposed automatically and reviewed by hand:

- **Automation.** [Dependabot](.github/dependabot.yml) opens grouped weekly PRs for
  GitHub Actions (the `github-actions` ecosystem) and for Python tooling declared in
  `pyproject.toml` (the `pip` ecosystem).
- **Review policy.**
  1. Action bumps must keep the `uses:` reference pinned to a full commit SHA with a
     trailing `# vX.Y.Z (pinned <date>)` comment — never a floating tag.
  2. CI (test matrix, CodeQL) must be green before merge.
  3. Major-version action or tooling bumps get a changelog/behavior check, not just a
     version-number merge.
  4. Grouped PRs are preferred; an unrelated bump is split out when it needs scrutiny.
- **Security posture.** [CodeQL](.github/workflows/codeql.yml) scans Python on every
  push/PR and weekly, and [OpenSSF Scorecard](.github/workflows/scorecard.yml)
  publishes a repository-health result from `main`. Treat new high-severity findings
  from either as release blockers (see [SECURITY.md](SECURITY.md)).

## Release Notes

User-visible changes should update [CHANGELOG.md](CHANGELOG.md). Before a public
release, work through the [release readiness checklist](docs/release-checklist.md),
which covers packaging, CI, docs, security, versioning, and rollback steps.
