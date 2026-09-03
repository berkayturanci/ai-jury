# Contributing to ai-jury

Thank you for your interest in contributing to **ai-jury**. The goal of
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
5. Fill in the PR template: a real **Summary** of your own and a **Related issues**
   reference — `Closes #N` (or `Relates to #N`), or write `no issue` if it touches
   none. The required `pr-lint` check enforces this, so PRs that replace the template
   without a summary + issue reference will fail until both are present.
6. Keep changes project-agnostic. Do not add references to a downstream repository,
   private workflow, or organization-specific process unless it is clearly framed as
   an external example.

## Bot-owned branches are read-only

A pull request branch opened by an automation is a **read-only input**. The
registered prefixes are `bolt`, `palette`, `sentinel`, `jules`, `dependabot`,
`copilot` and `renovate`, in any spelling (`bolt-x`, `palette/x`, `sentinel.x`).
Do not rebase such a branch, amend it, or push fixes to it: the bot pushes from
its own checkout, so its next push replaces the branch with that stale copy and
silently reverts anything that landed in between. On #648 that cost 25 files and
two already-merged pull requests. Instead, re-land the reviewed changes on a
fresh `fix/`, `perf/` or `docs/` branch cut from `main`, and close the bot's
pull request with a link to the replacement.

Branches a person drives from a working copy (`claude/…`, `codex/…`, `fix/…`)
are deliberately outside the rule — it is about a branch something else holds the
only copy of, not about who wrote the code.

The `Bot push guard` job in [`ci.yml`](.github/workflows/ci.yml) enforces the
half of this a machine can see: `scripts/bot_push_after_human_push_check.py`
fails the run when a bot commit lands after a human commit on such a branch, and
names both in the job summary. These bots commit *as the repository owner*, so it
recognises them by the branch prefix and by the subject marker each stamps
(`⚡ Bolt:`, `🎨 Palette:`, `🛡️ Sentinel:`) rather than by the account.

**The job is not self-enforcing.** On a `pull_request` event GitHub runs the
workflow from the pull request's own head, so a stale bot push that predates the
guard — 28d9cc3c on #648 deleted 25 files, two workflows among them — removes the
job and no check run is ever created for it. The fix is branch protection: `Bot
push guard` must be listed in `main`'s **required status checks**, where a
context that never reports blocks the merge. Its name is load-bearing once
registered there; renaming the job leaves the old context required and never
reported, which blocks every merge permanently.

**Registering a new automation** means, in that script:

1. its branch prefix in `BOT_BRANCH_PREFIXES` — mandatory, and it also supplies
   the subject marker, since `BOT_SUBJECT_MARKER` is built from that table so the
   two cannot drift apart;
2. its login in `BOT_LOGINS` — mandatory *where the bot pushes under an account
   of its own* (Dependabot, Renovate, Copilot); a `[bot]`-suffixed login or a
   `Bot` account type is recognised without an entry.

A bot registered in only one of the two is recognised half-way: on its branch but
not on its commits, or the reverse. `tests/test_bot_push_guard.py` asserts the
tables and this document agree.

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
