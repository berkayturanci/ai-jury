# Contributing to agent-review-council

Thank you for your interest in contributing to **agent-review-council**. The goal of
this project is to keep cross-vendor code review orchestration small, inspectable,
and easy to run from existing repositories.

## Code of Conduct

By participating in this project, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

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

## Release Notes

User-visible changes should update [CHANGELOG.md](CHANGELOG.md).
