---
name: ai-jury
description: Convene a cross-vendor multi-agent review jury on a diff, PR, or issue and produce one report — Claude Code, Codex, Antigravity, and/or a free local/open-weight model each review independently, cross-examine each other, verify, and reach one verdict — a chair's synthesis or a panel vote. Handles the whole flow end to end (scaffold config if needed → review → report → summarize). Use when the user wants a multi-model review of a pull request, a diff, an issue, or the current branch, or says "review jury", "convene the jury", or "cross-model review".
---

# AI Jury

Convene a panel of native coding-agent CLIs from **different vendors** to review the
same change, debate each other's findings, and produce one consolidated verdict.
This is multi-model review where each agent runs in its own native CLI (with its own
tooling), not API-level prompting.

## Prerequisites

- `ai-jury` is installed and `jury` is on PATH
  (`pipx install ai-jury` or `pip install -e /path/to/ai-jury`).
- At least one reviewer: an agent CLI (`claude`, `codex`, and/or `agy`) **or** a free,
  offline **local / open-weight** model via Ollama (configured as a `vendor = "local"`
  agent). Missing/unreachable agents are skipped; the jury runs with whoever is available.
- `gh` is authenticated for `--pr` / `--post`.
- First time in a repo? Run `jury init` to scaffold a `jury.toml` (it detects
  installed agents and local models).

## How to run

Pick the form that matches the request:

| Intent | Command |
|:--|:--|
| Review a GitHub PR | `jury --pr <number>` |
| Review and post the verdict as a PR comment | `jury --pr <number> --post` |
| Review the current branch vs default | `git diff origin/HEAD... \| jury --diff-file -` |
| Review a diff file | `jury --diff-file path/to/changes.diff` |
| Single-round (no debate) | add `--rounds 1` |
| Offline smoke test (no live CLIs) | `jury --mock --diff-file examples/sample.diff` |

Stream progress goes to stderr; the markdown report goes to stdout (or `-o file.md`).

## Parameters (the ones you'll reach for)

Grouped by intent — the common flags and what they do. The full, exhaustive
reference (every flag + `jury.toml` key, with values and examples) is
[`docs/parameters.md`](../../docs/parameters.md).

**Choose what to review** (one of):
- `--pr <n>` — a GitHub PR (via `gh`); `--repo owner/name` to target another repo.
- `--issue <n>` — a GitHub issue, reviewed for completeness/clarity with an issue-quality rubric (verdict READY / NEEDS-INFO / UNCLEAR); `--post` comments back via `gh issue comment`.
- `--diff-file <path>` / `--diff-file -` — a diff file, or stdin.

**Depth** (how hard to look):
- `--rounds {1,2}` — 1 = independent review only, 2 = + debate. Fixed value disables early-stop.
- `--verify` / `--no-verify` — run the chair's verification round (default on).
- `--auto` — risk-aware auto-depth: scale rounds/verify to the diff.
- `--chair <agent|rotate>` — who synthesizes the verdict.

**Output & how much to show:**
- `--format {markdown,json,sarif}` — report format (default markdown).
- `-o <file>` — write the report to a file instead of stdout.
- `--transcript` — full play-by-play (each agent's review, the debate, the chair's reasoning) instead of the summary.
- `--verbose` — summary **and** the full transcript in one document.
- `--live` — stream each step as it happens; add `--pr --post` to post each step to the PR live.

**Post to the PR** (require `--pr`):
- `--post` — post the report as one summary comment (`--post-mode phased` splits it into Round 1 / debate / decision).
- `--post-inline` — inline comments on located findings. `--label` — apply effort/risk/security labels.

**Gate a merge (CI):**
- `--ci --fail-on critical,major` — exit non-zero when a blocking finding remains.

**Scope & cost:**
- `--incremental` — only the diff since the last jury run on the PR.
- `--suggest-patches` — opt-in, inspectable fixes for verified findings (never auto-applied).
- `--cache` — reuse a cached outcome for an unchanged diff+config.

Run `jury --help` for the complete flag list, or `jury config show` for the effective config.

## End-to-end flow (setup → review → report)

When asked to "review" something, run the whole flow in one go — `jury` already
combines the review and the report, so you do not need separate steps for them:

1. **Ensure a config exists.** If there's no `jury.toml` in the repo, scaffold one
   non-interactively: `jury init --agents <detected>` (or `jury init --preset
   offline` for a free, local-only setup). If agent CLIs are already installed, this
   step is optional — `jury` falls back to built-in defaults, and with no CLIs but a
   local model server up it auto-adds a local agent.
2. **Review and capture the report in one command:**
   - PR: `jury --pr <n> -o jury-report.md` (add `--post-summary` to also post it).
   - Branch/diff: `git diff origin/HEAD... | jury --diff-file - -o jury-report.md`.
   - Gate a merge: add `--ci --fail-on critical,major` (non-zero exit blocks).
3. **Summarize back** the verdict + consensus findings (see below), and point the user
   at `jury-report.md` for the full report.

Useful add-ons in the same run: `--incremental` (only changes since the last jury
run on a PR), `--suggest-patches` (inspectable fixes for verified findings),
`--format json|sarif` (machine-readable). `jury config show` prints the effective
config; `jury --doctor` checks readiness and suggests next steps.

## What to report back to the user

1. The **chair verdict** (APPROVE / COMMENT / REQUEST CHANGES) and its one-line reason.
2. The **consensus findings** (issues 2+ agents agreed on) with `path:line`.
3. Any **disputed** findings worth a human decision.

Do not re-run the jury more than twice for the same diff without new input — it
spends real tokens across every configured vendor.

## Configuration

Behavior is driven by `jury.toml` (rounds, chair, per-agent model/timeout/flags).
Override per run with `--rounds`, `--chair`, or `--config <path>`.

## Integration notes

Drop this skill into a project's `.claude/skills/` (Claude Code) and/or expose the
`jury` CLI in CI. It composes with existing review workflows: run the jury first
for a cross-vendor pass, then let the project's own reviewers act on the consensus
findings.

## Scope / non-goals

This skill is the reusable, project-agnostic layer: convene the jury and report the
verdict. Project-specific workflow behavior — ship gates, branch policies, custom
reporting, or wiring into a particular repo's review process — belongs in a downstream
wrapper, **not** in this package. Keep this skill portable across projects.
