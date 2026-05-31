---
name: review-council
description: Convene a cross-vendor multi-agent review council on a PR or diff — Claude Code, Codex, and Antigravity each review independently, cross-examine each other, and a chair synthesizes a verdict. Use when the user wants a multi-model review of a pull request, a diff, or the current branch, or says "review council", "convene the council", or "cross-model review".
---

# Review Council

Convene a panel of native coding-agent CLIs from **different vendors** to review the
same change, debate each other's findings, and produce one consolidated verdict.
This is multi-model review where each agent runs in its own native CLI (with its own
tooling), not API-level prompting.

## Prerequisites

- `agent-review-council` is installed and `council` is on PATH
  (`pipx install agent-review-council` or `pip install -e /path/to/agent-review-council`).
- At least one agent CLI is installed: `claude`, `codex`, and/or `agy`.
  Missing CLIs are skipped automatically; the council runs with whoever is available.
- `gh` is authenticated for `--pr` / `--post`.

## How to run

Pick the form that matches the request:

| Intent | Command |
|:--|:--|
| Review a GitHub PR | `council --pr <number>` |
| Review and post the verdict as a PR comment | `council --pr <number> --post` |
| Review the current branch vs default | `git diff origin/HEAD... \| council --diff-file -` |
| Review a diff file | `council --diff-file path/to/changes.diff` |
| Single-round (no debate) | add `--rounds 1` |
| Offline smoke test (no live CLIs) | `council --mock --diff-file examples/sample.diff` |

Stream progress goes to stderr; the markdown report goes to stdout (or `-o file.md`).

## What to report back to the user

1. The **chair verdict** (APPROVE / COMMENT / REQUEST CHANGES) and its one-line reason.
2. The **consensus findings** (issues 2+ agents agreed on) with `path:line`.
3. Any **disputed** findings worth a human decision.

Do not re-run the council more than twice for the same diff without new input — it
spends real tokens across every configured vendor.

## Configuration

Behavior is driven by `council.toml` (rounds, chair, per-agent model/timeout/flags).
Override per run with `--rounds`, `--chair`, or `--config <path>`.

## Integration notes

Drop this skill into a project's `.claude/skills/` (Claude Code) and/or expose the
`council` CLI in CI. It composes with existing review workflows: run the council first
for a cross-vendor pass, then let the project's own reviewers act on the consensus
findings.

## Scope / non-goals

This skill is the reusable, project-agnostic layer: convene the council and report the
verdict. Project-specific workflow behavior — ship gates, branch policies, custom
reporting, or wiring into a particular repo's review process — belongs in a downstream
wrapper, **not** in this package. Keep this skill portable across projects.
