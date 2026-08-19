# Workflow cookbook

Practical, copy-paste recipes for running `ai-jury` in day-to-day
work. Each recipe states its **prerequisites**, the exact **command(s)**, and the
**expected outcome**.

`jury --help` (and [`cli.py`](../src/ai_jury/cli.py)) is the
single source of truth for flags; every command below uses only real flags.
Anything that should run without credentials uses `--mock`, which executes the
full pipeline offline with deterministic mock agents — no live CLIs are called,
no network, no keys.

> **Running from a clone (no install).** All recipes assume `jury` is on PATH
> (`pipx install ai-jury`). From a checkout you can swap `jury`
> for `PYTHONPATH=src python3 -m ai_jury` — every flag is identical.

---

## 0. First-time setup — scaffold a config

**Prerequisites:** none (works before any agent is configured).

Generate a `jury.toml` instead of hand-writing it. `jury init` detects which
agent CLIs are installed and, for a local agent, the models on your Ollama/OpenAI-
compatible server:

```bash
jury init                      # interactive: pick agents, rounds, chair, local model
jury init --wizard             # guided Q&A: numbered options for the most-used settings
jury init --preset offline     # free, local-only ($0); also: fast / balanced / thorough
jury init --list-agents        # show known agents + availability
jury init --list-models        # list local (Ollama) models you can pick
jury init --agents claude,codex,qwen --rounds 2   # non-interactive / scriptable
```

> **Guided setup:** `jury init --wizard` walks through the most-used settings as
> numbered questions — reviewers, depth (1-round / debate / adaptive / auto),
> chair vs. **panel vote**, verification, context mode + secret redaction, and the
> CI fail-on gate. Every question is skippable (press Enter to keep the built-in
> default), and only the keys you explicitly choose are written, so the generated
> file stays minimal. Plain `jury init` is unchanged.

> **Cost-aware depth:** add `--auto` (or `[jury] auto_depth = true`) and the jury
> scales to the diff — a docs-only or few-line change runs shallow (1 round, no
> verify), a large or security-touching change runs full. The panel is never
> trimmed; explicit `--rounds`/`--verify` override it.

> **Zero-config offline:** even without a `jury.toml`, if no agent CLI is
> installed but a local model server is reachable, `jury` adds a local agent
> automatically — so `git diff main... | jury --diff-file -` just works offline.

**Outcome:** a validated `jury.toml` using the secure-by-default agent templates
(Codex read-only, Antigravity sandboxed, Claude write-tool denylist). It won't overwrite
an existing file without `--force`.

---

## 1. Review a local branch before opening a PR

**Prerequisites:** at least one agent CLI (`claude`, `codex`, or `agy`). No `gh`
needed — this reviews a diff, not a PR.

Pipe the branch diff straight into the jury via stdin (`--diff-file -`):

```bash
git diff main... | jury --diff-file -
```

`main...` (three dots) diffs your branch against its merge-base with `main`, so
you review exactly what the PR would contain. Use `origin/HEAD...` if your
default branch isn't `main`:

```bash
git diff origin/HEAD... | jury --diff-file -
```

Or capture the diff to a file first, then review it:

```bash
git diff main... > /tmp/branch.diff
jury --diff-file /tmp/branch.diff
```

**Outcome:** a markdown report on stdout headed `# 🏛️ AI Jury`
with a recommendation, a Findings section, and the per-agent review rounds. Act
on the findings before you open the PR. (If the diff is empty, the CLI exits with
`error: empty diff — nothing to review`.)

---

## 2. Review a GitHub PR and save the report

**Prerequisites:** `gh` installed and authenticated (`gh auth status`), plus at
least one agent CLI.

```bash
jury --pr 123 -o report.md
```

`--pr` fetches the diff via `gh`; `-o/--output` writes the rendered report to a
file instead of stdout. Add `--repo owner/name` if you're not inside the target
repo's checkout:

```bash
jury --pr 123 --repo owner/name -o report.md
```

**Outcome:** `report.md` contains the full verdict and findings. Progress logs
still go to stderr (silence them with `-q/--quiet`); only the report itself is
written to the file.

---

## 3. Post an advisory comment on a PR

**Prerequisites:** `gh` authenticated with write access to the PR, plus at least
one agent CLI.

Post the whole verdict as a single summary comment:

```bash
jury --pr 123 --post-summary
```

`--post` is an accepted alias for `--post-summary`. To attach findings as inline
review comments on the relevant lines instead:

```bash
jury --pr 123 --post-inline
```

**Preview before posting.** Combine `--post-inline` with `--dry-run` to print the
exact inline payload without making any GitHub call — nothing is posted:

```bash
jury --pr 123 --post-inline --dry-run
```

You can also write the report locally and post in the same run:

```bash
jury --pr 123 --post-summary -o report.md
```

**Outcome:** with `--post-summary`/`--post-inline`, the jury's feedback lands
on the PR as advisory comments (`--post`/`--post-summary` also post to an
`--issue`; `--post-inline` is `--pr`-only). With `--dry-run`, you
see what *would* be posted and the network is never touched. These are advisory
by design — they comment, they don't block a merge; gating is the separate `--ci`
concern (see recipe 5).

---

## 3a. Follow a long run live (progress + phased posting)

**Prerequisites:** `--pr` with `gh` write access. A full multi-round run on a big
diff can take many minutes — these surfaces tell you *where* it is instead of
making you wait blind.

Keep a single sticky status comment updated at each milestone (round start, per
chunk, verification, synthesis):

```bash
jury --pr 123 --post-progress
```

The sticky comment is edited in place — one comment, not a flood — and is
replaced by the final verdict when the run finishes. To instead see the *content*
of each stage as it lands, post the run in phases:

```bash
jury --pr 123 --post --post-mode phased
```

`phased` posts Round 1 (independent reviews), the debate round, and the final
decision as **separate** comments as each completes, so reviewers can read and
react to round 1 while the debate is still running. The default `--post-mode
single` posts one consolidated comment at the end. The two combine: add
`--post-progress` for a live status line *and* `--post-mode phased` for the
staged content.

**Outcome:** on a slow run you can watch progress (sticky comment) and read each
phase as it completes (phased posting) instead of waiting for one final drop.

---

## 4. Run the bundled skill from an assistant

**Prerequisites:** Claude Code, the `jury` CLI reachable from the assistant's
shell, and at least one agent CLI. For PR review/posting, `gh` authenticated.

Install the skill as a plugin (this repo doubles as a single-plugin
marketplace):

```text
/plugin marketplace add berkayturanci/ai-jury
/plugin install ai-jury@ai-jury
```

Or drop [`skill/ai-jury/`](../skill/ai-jury/SKILL.md) into a
project's `.claude/skills/` directory manually.

Then ask the assistant for a review, e.g.:

> Convene the review jury on my current branch.

**Outcome:** the skill shells out to `jury` (typically
`git diff origin/HEAD... | jury --diff-file -` for the working branch, or
`jury --pr <n>` for a PR) and reports the chair verdict plus consensus
findings back in the conversation. You decide what to act on. See the
[platform support matrix](platforms.md) for other surfaces.

---

## 5. Add a soft / advisory review stage to a ship workflow

**Prerequisites:** at least one agent CLI in the CI environment. For PR-triggered
runs, `gh` authenticated (CI typically provides a token). Use `--mock` for a
pipeline smoke test with no agents at all.

The jury fits as a **non-blocking, advisory** stage: run it, surface the
verdict, but don't fail the build on its opinion. Keep the step from breaking the
pipeline by neutralizing its exit code:

```bash
# Advisory: always succeeds, posts the verdict, never blocks the merge.
jury --pr "$PR_NUMBER" --post-summary || true
```

As a generic shell stage in any ship script:

```bash
# Soft review gate — report-only, exit status ignored.
git diff origin/HEAD... | jury --diff-file - -o jury-report.md || true
echo "Jury report saved to jury-report.md (advisory)."
```

If you later want the jury to actually **gate** merges, opt in explicitly with
`--ci`, which exits non-zero when blocking findings remain:

```bash
# Hard gate — fails the build on critical/major findings.
jury --pr "$PR_NUMBER" --ci --fail-on critical,major
```

`--fail-on` overrides the `[jury.ci] fail_on` severities in `jury.toml`;
drop the trailing `|| true` to let the stage fail.

**Outcome:** the advisory form always reports and never blocks; the `--ci` form
turns the jury into an enforced quality gate. Start advisory, graduate to
`--ci` once the team trusts the signal.

---

## 6. Run in mock mode for smoke testing

**Prerequisites:** none. `--mock` runs the entire pipeline offline with
deterministic mock agents — no live CLIs, no `gh`, no credentials.

```bash
jury --mock --diff-file - < examples/sample.diff
```

Or against any diff file or piped branch diff:

```bash
jury --mock --diff-file examples/sample.diff
git diff main... | jury --mock --diff-file -
```

You can exercise the CI gate offline too:

```bash
jury --mock --ci --fail-on critical,major --diff-file examples/sample.diff
```

**Outcome:** a complete markdown report (headed `# 🏛️ AI Jury`)
is produced without contacting any agent or GitHub. This is the fastest way to
confirm your install, config, and command shapes are correct before wiring in
real agents — and it's safe to run anywhere.

---

## 7. Use only one or two agent CLIs

**Prerequisites:** at least one agent CLI installed. Missing agents are skipped
with a warning automatically, so you can also simply install fewer.

**Option A — disable agents in `jury.toml`.** Set `enabled = false` on the
agents you don't want. For example, to run *only* Claude Code:

```toml
[[agent]]
name = "claude"
vendor = "anthropic"
command = "claude"

[[agent]]
name = "codex"
vendor = "openai"
command = "codex"
enabled = false

[[agent]]
name = "agy"
vendor = "google"
command = "agy"
enabled = false
```

If the configured `chair` is one of the disabled/unavailable agents, the first
available agent is used instead — set `chair` to an enabled agent to be explicit.

**Option B — point at a custom config.** Keep your default `jury.toml` intact
and pass a slimmed-down one per run with `--config`:

```bash
jury --config ./two-agents.toml --diff-file -
```

**Option C — rely on auto-skip.** If an agent CLI simply isn't installed, the
jury skips it with a warning and continues with whatever is available. (Pass
`--strict` to instead fail when any configured agent CLI is missing.)

**Outcome:** the jury runs with just the agents you enabled/installed. With a
single agent there's no cross-vendor debate (the debate round needs ≥2 successful
reviews), but you still get a structured verdict and report — handy while
trialing the tool with one CLI.

> **Heads-up:** the research lever behind this tool is **vendor heterogeneity** —
> two or three *different* vendors catch more than one reviewer run repeatedly.
> Trimming to one agent is fine for smoke tests and cost control, but use ≥2
> vendors when you want the real jury effect.

---

## 8. Incremental review on PR updates

Re-reviewing a large PR's full diff on every push is slow. With `--incremental`,
the jury records the reviewed head SHA on its summary comment and, on the next
run, reviews only the range since that SHA — falling back to a full review when
no prior marker exists or the head is unchanged.

```bash
# First run establishes the marker on the posted summary.
jury --pr 123 --post-summary

# Later runs review only what changed since the last jury run.
jury --pr 123 --incremental --post-summary
```

The report's header states the scope (`Review scope: Incremental — …` or
`Full — …`). The marker is a hidden HTML comment, so it is invisible to readers.

## 9. Suggested patches for verified findings

`--suggest-patches` emits a **separate**, opt-in section that turns *verified*
findings into inspectable fix suggestions. It is read-only — nothing is applied
automatically, and unverified/rejected findings never produce a suggestion.

```bash
# Append a "Suggested patches" section after the markdown report.
jury --pr 123 --suggest-patches

# Or write the patches to their own file, leaving the report untouched.
jury --diff-file changes.diff --suggest-patches --patches-out patches.md -o report.md
```

## 10. Comment-triggered runs in GitHub Actions

The jury can be triggered from a PR comment such as `/jury review` or
`/jury summary` via a workflow. Commands are parsed by an **allowlist**
(`review`, `summary`; only the `--rounds N` flag) — the comment text is never
passed to a shell, so arbitrary commands in a comment cannot run. Parsing is
exposed through the `jury comment` mode:

```bash
# Resolve a comment to a safe jury argv (used by the workflow):
jury comment --text "/jury review --rounds 1" --pr 123 --print-args
#   -> --rounds 1 --pr 123 --post-summary

# Reject anything not on the allowlist (exit 2):
jury comment --text "/jury deploy" --print-args   # rejected
```

A minimal, safe workflow recipe (gate on a trusted author association and only
on PR comments):

```yaml
# .github/workflows/jury-comment.yml
name: jury-comment
on:
  issue_comment:
    types: [created]
permissions:
  contents: read
  pull-requests: write
jobs:
  jury:
    # Only PR comments, only from maintainers/owners, only the /jury trigger.
    if: >
      github.event.issue.pull_request &&
      contains(github.event.comment.body, '/jury') &&
      contains(fromJSON('["OWNER","MEMBER","COLLABORATOR"]'), github.event.comment.author_association)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.13" }
      - run: pip install ai-jury
      - name: Run jury from the comment command
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          COMMENT_BODY: ${{ github.event.comment.body }}
          PR_NUMBER: ${{ github.event.issue.number }}
        # The comment body is passed as an argument value (never via a shell
        # template), and the jury allowlist rejects anything unsupported.
        run: jury comment --text "$COMMENT_BODY" --pr "$PR_NUMBER"
```

> The `if:` guard restricts *who* can trigger a run; the `jury comment`
> allowlist restricts *what* can run. Both layers matter — keep the author
> association check so untrusted forks cannot trigger agent runs.

---

## 11. Decide by a panel vote instead of a single chair

By default the chair synthesizes the verdict. `--decision vote` (or
`[jury] decision = "vote"`) instead **tallies the reviewers**: each votes from the
worst finding they raised (critical/major → REQUEST CHANGES, minor/nit → COMMENT,
none → APPROVE), majority wins, ties resolve to the stricter stance.

```bash
jury --pr 123 --decision vote
```

The report shows the tally and each reviewer's ballot as the headline verdict and
keeps the chair's synthesis as supporting reasoning; the tally is also written to
`--metadata-json`. It works with `--issue` too — there the panel votes over the
issue vocabulary (**NEEDS-INFO > UNCLEAR > READY**) instead. It's a rendering choice — it doesn't change the cache key, and
the severity-based `--ci` gate is unaffected (a lone critical still fails CI even
on a majority APPROVE).

---

## 12. Review an issue for completeness, not just code

`--issue N` points the same jury at a GitHub **issue** instead of a diff. The
panel evaluates it against an issue-quality rubric (reproduction steps, expected
vs actual, scope/acceptance criteria, missing context) and returns a
**READY / NEEDS-INFO / UNCLEAR** verdict with a checklist of gaps.

```bash
jury --issue 42                 # print the completeness verdict
jury --issue 42 --post          # comment it back on the issue (gh issue comment)
jury --issue 42 --decision vote # decide by panel vote: NEEDS-INFO > UNCLEAR > READY
jury --issue 42 --live --post   # stream each step AND post it to the issue as it lands
```

`--post`/`--post-summary`, `--decision vote`, and `--live` all work for issues
(posting goes through `gh issue comment`). Only PR/diff-only flags — `--post-inline`,
`--label`, `--incremental`, `--post-progress` — don't apply to issues and are
rejected. `--repo owner/name` targets another repo.

---

## 13. Configure Universal Agent Providers (OpenRouter, DeepSeek, Groq, Grok, Cursor CLI, Local Models)

**Prerequisites:** an API key for hosted providers (e.g., `export OPENROUTER_API_KEY="sk-or-v1-..."`, `export DEEPSEEK_API_KEY="sk-..."`, or `export XAI_API_KEY="xai-..."`) or installed CLI tools (`cursor`, `aider`).

Add any of these provider templates to your `jury.toml`:

```toml
# OpenRouter API (Access 200+ models like DeepSeek-R1, Llama 3.3)
[[agent]]
name = "openrouter"
vendor = "openai-compatible"
model = "deepseek/deepseek-r1"
endpoint = "https://openrouter.ai/api/v1/chat/completions"
api_key_env = "OPENROUTER_API_KEY"

# DeepSeek API direct
[[agent]]
name = "deepseek"
vendor = "openai-compatible"
model = "deepseek-reasoner"
endpoint = "https://api.deepseek.com/v1/chat/completions"
api_key_env = "DEEPSEEK_API_KEY"

# Groq ultra-fast Llama inference
[[agent]]
name = "groq"
vendor = "openai-compatible"
model = "llama-3.3-70b-versatile"
endpoint = "https://api.groq.com/openai/v1/chat/completions"
api_key_env = "GROQ_API_KEY"

# Grok / xAI API
[[agent]]
name = "grok"
vendor = "openai-compatible"
model = "grok-2-latest"
endpoint = "https://api.x.ai/v1/chat/completions"
api_key_env = "XAI_API_KEY"

# OmniRoute / Unified LLM Gateways (LiteLLM, One API)
[[agent]]
name = "omni-claude"
vendor = "openai-compatible"
model = "anthropic/claude-3-5-sonnet"
endpoint = "http://localhost:8000/v1/chat/completions"
api_key_env = "OMNIROUTE_API_KEY"

# Local models (Ollama, LM Studio, vLLM, llama.cpp)
[[agent]]
name = "local-qwen"
vendor = "local"
model = "qwen2.5-coder:14b"

# Generic CLI agents (Cursor, Aider, Goose, OpenHands)
[[agent]]
name = "cursor"
vendor = "cli"
command = "cursor-agent"
extra_args = ["--print", "--trust", "--model", "claude-4.6-sonnet-medium"]
prompt_mode = "arg"

[[agent]]
name = "aider"
vendor = "cli"
command = "aider --message"
prompt_mode = "arg"
```

**Outcome:** `jury --config-validate` confirms provider readiness. Run `git diff main... | jury --diff-file -` to deliberate across your choice of hosted HTTP models, coding CLIs, and local models.

---

## 14. Run local pre-commit and pre-push reviews with pre-commit

Add `ai-jury` to your repository's `.pre-commit-config.yaml` to catch security issues and logic bugs locally before committing or pushing:

```yaml
repos:
  - repo: https://github.com/berkayturanci/ai-jury
    rev: v1.14.3
    hooks:
      - id: ai-jury
        # Optional args: e.g. single round or fast preset
        args: [--diff-file, -, --rounds, "1", --preset, fast]
```

---

## 15. Zero-friction CI with the official GitHub Action

Review every pull request automatically using the official composite action:

```yaml
# .github/workflows/ai-jury.yml
name: ai-jury
on:
  pull_request:
    types: [opened, synchronize, reopened]

jobs:
  review:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
      - uses: berkayturanci/ai-jury@v1
        with:
          openai-api-key: ${{ secrets.OPENAI_API_KEY }}
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
          args: "--auto --post --ci"
```

---

## 16. Full delivery governance with Keel + AI Jury

For teams using [Keel](https://github.com/berkayturanci/keel) to automate software delivery from issue intake to verified merge, `ai-jury` serves as the multi-agent review gate in Keel's review phase:

```yaml
# .keel/project.yaml
review:
  engine: ai-jury
  preset: balanced
  gating: true
```

When Keel executes `keel ship`, it convenes `ai-jury` cross-examination across your configured models to guarantee consensus before unlocking the merge window.

---

## 17. Add an AI Jury verified badge to your README

Display your repository's multi-agent review posture with an SVG badge:

```markdown
[![AI Jury Reviewed](https://img.shields.io/badge/ai--jury-consensus%20verified-6366f1)](https://github.com/berkayturanci/ai-jury)
```

## 18. Automatically apply verified suggested patches (`jury apply`)

When reviewers identify a bug and verify a concrete fix, apply it safely with a single command:

```bash
# 1. Run jury with suggested patches enabled
jury --pr 123 --suggest-patches -o report.md

# 2. Review and apply all verified suggestions
jury apply --report report.md

# Or apply a specific patch suggestion by number:
jury apply --report report.md 1
```

---

## 19. Fast, frugal reviews with `--tiered` routing and `--hints`

For cost optimization and noise reduction on large repositories:

```bash
# Combine fast linter pre-pass and tiered model routing with frontier protection
jury --pr 123 --hints --tiered
```

- `--hints` runs fast local linters (Ruff, ESLint) and informs LLMs so they skip trivial formatting and focus on deep bugs.
- `--tiered` routes routine code to economical models while keeping frontier models as anchors for complex or security-sensitive changes.

---

## See also

- [Architecture](architecture.md) — components, round structure, adapters.
- [Configuration](configuration.md) — behavior, budgets, universal provider settings.
- [Platform support matrix](platforms.md) — where you can install and run the jury.
- [Example run](example-run.md) — a deterministic mock report end to end.
- [Live four-vendor review](example-live-review.md) — a real run of the jury
  reviewing its own repository, with honest notes on false positives and cost.
