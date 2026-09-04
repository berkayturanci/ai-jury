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

### Proving the panel did not collapse

Configuring three vendors is not the same as *hearing from* three. An agent can
be on `PATH`, pass its version probe, be reported `[available]` by `jury
--doctor`, and still contribute nothing — a refusal, a CLI whose flags changed
under it, a reply with no findings block. The run continues (fail-soft), and its
report looks exactly like a healthy one.

Two things stop that being silent.

**Before the run — `jury --doctor`.** The report ends with a readiness block, and
`--doctor --json` carries the same facts under `panel`:

```console
$ jury --doctor
...
Cross-vendor readiness
----------------------------------------
  vendors enabled:   3
  vendors reachable: 1
  min_vendors gate:  2
  cross-vendor ready: no
  note: this checks availability, not contribution. A reachable CLI can still
        return no review (#635) — only a run can prove the panel.
```

```bash
jury --doctor --json | jq '.panel'
# { "vendors_configured": 3, "vendors_available": 1, "min_vendors": 2,
#   "contributing_vendors": null, "multi_vendor_ready": false }
```

`contributing_vendors` is `null` on purpose: doctor runs no review, so it can
only report reachability. Do not read a green doctor as proof of a cross-vendor
panel.

**After the run — the `--min-vendors` guard**, which counts vendors that actually
**contributed a review** and exits **3** when there are too few:

```bash
jury --pr 123 --ci                     # guard on by default (min_vendors = 2)
jury --pr 123 --min-vendors 3          # require all three vendors
jury --pr 123 --no-min-vendors         # accept a collapsed panel (explicit)
```

**From this release the gate is on by default, and it scopes on the vendors your
config NAMES.** A `jury.toml` naming two or more distinct vendors exits **3**
unless at least that many actually contributed a review. That includes the case
where a configured CLI is **not installed on this machine**: the shipped
three-vendor `jury.toml` on a laptop with one CLI now fails, because a
configuration promising three vendors and delivering one is exactly the collapse
this guard exists to catch. A missing CLI is not an exemption.

Only a config that never claimed cross-vendor consensus is left alone — Option A
above, or any config with fewer distinct vendors enabled than the threshold.

Two escapes, and they answer different questions:

```bash
jury --pr 123 --no-min-vendors        # accept a collapsed panel (or [jury.ci] min_vendors = 0)
jury --pr 123 --strict                # fail at STARTUP on a missing CLI, before spending anything
```

`--no-min-vendors` (equivalently `min_vendors = 0` under `[jury.ci]`) says "one
vendor is fine here". `--strict` does not lower the bar — it moves the failure
earlier, so a missing CLI is reported before the run instead of as a collapsed
panel after it. Exit 3 is distinct from the `--ci` findings failure (exit 1), so
a caller can tell "the reviewers disagreed with you" from "the reviewers never
ran", and the failure message names the opt-out:

```console
panel collapsed: 1 vendor(s) contributed a review, 2 required. An abstention is
not an approval; cross-vendor consensus was not formed. To accept a collapsed
panel, pass --no-min-vendors (or set [jury.ci] min_vendors = 0); to catch a
missing CLI at startup instead, run with --strict.
```

The run's own count is in `--metadata-json` under `panel.vendors`:

```bash
jury --pr 123 --metadata-json run.json && jq '.panel' run.json
# { "configured": 3, "effective": 1, "vendors": 1, "abstained": 0, "failed": 2, "short": true }
```

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
    rev: v1.15.1
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
          # min-vendors: "2"   # the default; see below
```

**The Action refuses a collapsed panel by default.** `min-vendors` defaults to
`2`, so a workflow that says nothing still gets the cross-vendor guard — before
this, an Action consumer inherited a fail-soft run that exited 0 on a panel that
had quietly become single-vendor. The input is appended as `--min-vendors N`
unless `args` already names `--min-vendors` or `--no-min-vendors`, so an explicit
choice in `args` always wins.

| Value | Effect |
| --- | --- |
| `"2"` (default) | Fail the step (exit 3) when fewer than 2 distinct vendors contributed a review. |
| `"3"` | Require all three of a three-vendor panel. |
| `"0"` | Opt out — accept a single-vendor run, the pre-#682 behaviour. |
| `""` (empty) | Same as the default. An unset repository or organization variable arrives as the empty string, so `min-vendors: ${{ vars.MIN_VENDORS }}` is a valid way to leave the guard alone. |

The guard is inert for a single-vendor *configuration*, so a workflow running one
reviewer does not need `min-vendors: "0"`. It is **not** inert for a runner that
is missing one of several configured CLIs — that is a collapsed panel, and it
fails: install the CLI, drop the agent from the config, or opt out explicitly. A
non-integer value fails the step with a clear message rather than being spliced
into the command line.

---

## 16. Full delivery governance with Keel + AI Jury

**Prerequisites:** [Keel](https://github.com/berkayturanci/keel) driving the
repository (`.keel/project.yaml`), `jury` on `PATH` wherever `keel ship` runs. Keel
does not depend on `ai-jury` — if the `jury` CLI is absent, the built-in `jury` gate
is a fail-soft no-op.

There is no `review:` key in Keel's project schema — `keel validate` rejects
`review: {…}` with `unknown property 'review'`. The real integration is a built-in
gate named `jury`, declared like `build` and `lint`:

```yaml
# .keel/project.yaml
extends: keel
core_version: "^1.0"
base_branch: main
gates: [build, lint, jury]
knobs:
  build_gate_cmd: "make test"
  jury_timeout_s: 600
  evidence_require_distinct_vendors: true
```

`knobs.jury_timeout_s` bounds the wall-clock seconds the `jury` gate may run
before it is killed (default `600`); a run that is killed, or that produces no
parseable report, is recorded as a blocking `major` finding in gating mode rather
than silently passing. See Keel's
[`gates` and `knobs` reference](https://github.com/berkayturanci/keel/blob/main/docs/keel/configuration.md)
for the full field list.

### Turning the jury on

`keel ship` decides per run whether the jury participates, with flags on the
`ship` command itself:

```bash
keel ship --jury            # force the jury on, gating mode
keel ship --jury-advisory   # force the jury on, report-only (never blocks)
keel ship --no-jury         # force the jury off for this run
```

Precedence is `--no-jury` > `--jury` > tier-3 auto-on > off — a change that
touches a `tier3_globs` path turns the jury on automatically (gating, unless
`--jury-advisory` is also passed), and `--no-jury` always wins over that.

### What keel does with the report

At the `s8 test` step, a `keel ship` run invokes `jury --format json
--diff-file <tmp>` read-only against the PR diff (never `--strict`) and maps each
finding's `ai-jury` severity onto a Keel severity:

| `ai-jury` severity | Keel severity | Effect in gating mode |
|---|---|---|
| `critical`, `blocker` | `critical` | blocks the merge |
| `major` | `major` | blocks the merge |
| `minor` | `minor` | gated suggestion (fix before merge, or explicitly defer) |
| `nit`, `info`, `note` | `nit` | advisory only |
| anything else / unrecognized | `minor` | gated suggestion |

What runs this step is Keel's **ship adapter** — the `ship` command/skill
generated from `src/keel/adapters/commands/ship.md` in the keel repository, not
any code path in `src/keel/*.py`. (Anchors below are keel `main` as of
2026-09-04.) The adapter:

- Posts a single verdict comment to the PR via `keel post-comment --artifact
  jury-verdict`, tagged with the `keel.jury-verdict.v1` marker and `head: <sha>`
  so re-runs on the same commit stay idempotent instead of piling up comments.
- Saves the raw JSON report to `.keel/state/jury/<run-id>.json` by adding
  `--format json -o .keel/state/jury/$RUN_ID.json` to that same jury invocation
  (`commands/ship.md:711`, s8, "Save the jury artifact for visualizers"). It is
  untracked state, never committed, and the write is fail-soft — display-only,
  it never gates. `keel-visual` only *reads* the file, to show the jury verdict
  alongside the run on the activity board.
- Passes the count of **distinct participating vendors** (the ones that
  actually returned output, not just the ones configured) to `keel
  evidence-verify --jury-vendors <N>` at merge time. A panel with fewer than 2
  distinct vendors is downgraded from gating to advisory before that evidence
  check runs, even if the earlier `s8` gate treated the run as gating. Setting
  `knobs.evidence_require_distinct_vendors: true` (shown above) additionally
  requires every *required reviewer* verdict to carry vendor provenance, with
  no two sharing a vendor; the jury verdict is a separate artifact and is
  checked on its own through that participating-vendor count, not this knob.
  See Keel's
  [evidence guide](https://github.com/berkayturanci/keel/blob/main/docs/keel/evidence.md)
  for how the pre-merge evidence gate reads these signals.

### Not yet available

Two `ai-jury` capabilities mentioned elsewhere in this cookbook are not wired
into Keel yet: per-panelist ballots (`--format keel-reviews`) are tracked in
[ai-jury#663](https://github.com/berkayturanci/ai-jury/issues/663), and a
`jury run-agent` recipe for Keel's implementer/reviewer roles is tracked in
[ai-jury#661](https://github.com/berkayturanci/ai-jury/issues/661). Until
those land, the `jury` gate above is the full extent of the Keel integration.

### Use the panel as Keel's reviewers

The gate above consumes the panel's *consolidated* findings. To have each
panelist appear as its own reviewer instead — one head-pinned verdict per agent,
carrying the vendor and model that produced it — render the run with
`--format keel-reviews` and hand the file to `keel review`:

```bash
jury --pr 123 --format keel-reviews -o reviews.json
keel review --reviews reviews.json --dry-run
```

The file is a JSON array of `{reviewer, verdict, scope, findings, testing,
vendor, model}` records, one per panelist that returned output plus the chair as
`reviewer: "chair"` — see [report-format.md](report-format.md#the-keel-reviews-bundle).
Because every record carries its own `vendor`, a three-seat panel spread over
three vendors satisfies Keel's distinct-vendor evidence requirement on its own.
Producing the bundle is an `ai-jury` concern only; the Keel-side consumption of
it is tracked in [keel#1015](https://github.com/berkayturanci/keel/issues/1015).

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

# 2. See what would change — writes nothing
jury apply --report report.md all --dry-run

# 3. Apply, confirming at the prompt
jury apply --report report.md all

# Or a specific suggestion by number:
jury apply --report report.md 1
```

`jury apply` writes to your working tree, so it asks first:

- The paths each suggestion would touch are printed **before** anything is
  written, and they come from git itself — not from what the suggestion claims
  to change. A patch that quietly renames a second file shows that second path
  here.
- The index is required. There is no "apply everything" default; pass `all`
  explicitly when that is what you mean.
- In a script, pass `--yes`. With stdin not a terminal and no `--yes`, the
  command refuses rather than treating silence as consent — which also means
  `cat report.md | jury apply all` needs `--yes`.

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

## 20. Headless CI & Containerized Reviews via Hosted APIs

For lightweight CI runners or Docker containers without Node.js or native CLI agent installations, configure universal hosted API reviewers directly in `jury.toml`:

```toml
# jury.toml
[jury]
rounds = 2
chair = "deepseek"

[[agent]]
name = "deepseek"
vendor = "openai-compatible"
endpoint = "https://api.deepseek.com/v1"
api_key_env = "DEEPSEEK_API_KEY"
model = "deepseek-coder"

[[agent]]
name = "groq"
vendor = "openai-compatible"
endpoint = "https://api.groq.com/openai/v1"
api_key_env = "GROQ_API_KEY"
model = "llama-3.3-70b-versatile"
```

Then run in any container or GitHub Actions workflow:

```bash
export DEEPSEEK_API_KEY="sk-..."
export GROQ_API_KEY="gsk-..."
export JURY_ALLOW_REMOTE_ENDPOINT=1

jury --pr 123 --ci
```

---

## 21. Run one agent for an orchestrator (keel)

The panel is the point of ai-jury, but an orchestrator sometimes needs *one*
agent: dispatch an implementer, ask a single gate reviewer, get a chair's call.
`jury run-agent` is that entry point — the same adapters, the same read-only
flags, the same timeouts and the same typed error codes a panel run uses, for
one agent, with a JSON result on stdout.

```bash
# One reviewer, read-only, JSON on stdout
jury run-agent --agent claude --role review --prompt-file gate.md

# A specific model, with a deliberate wall-clock bound
jury run-agent --agent codex:gpt-5.2 --role gate --prompt-file gate.md --timeout 900

# An implementer that may edit the tree — write access is explicit, never implied
jury run-agent --agent agy --role implement --allow-write --cwd ../worktree \
  --prompt-file task.md

# Just the text, for a shell pipeline
jury run-agent --agent claude --role chair --prompt-file decide.md --format text
```

The result document (`schema_version: "ai-jury.run-agent.v1"`):

```json
{
  "schema_version": "ai-jury.run-agent.v1",
  "ok": true,
  "agent": "codex",
  "vendor": "openai",
  "model": "gpt-5.2",
  "role": "gate",
  "transport": "cli",
  "text": "...the agent's answer...",
  "exit_code": 0,
  "duration_s": 41.2,
  "timed_out": false,
  "error_code": null,
  "error": null,
  "attribution": { "vendor": "openai", "model": "gpt-5.2", "label": "agent:openai model:gpt-5" }
}
```

`attribution.label` is the pair an orchestrator applies verbatim: `agent:<vendor>`
plus a coarse `model:<base>`, so a point-release bump does not fork the
attribution history of otherwise-identical work. Split it on whitespace.

The base is **family + major**, and the grouping is deliberately uneven: it is
byte-for-byte keel's `agents.model_base`, because both projects label the same
issues and a divergent rule would split one project's history. A tier or effort
suffix collapses (`gemini-3.8-flash`, `gemini-3.8-flash-high` and
`gemini-3.8-pro` are all `model:gemini-3`); a hyphen-spelled version does not
(`claude-opus-4-5` and `claude-opus-4-6` stay distinct). Read the `model` field
when you need the exact id.

**Roles decide privilege, and the flag cannot override that.** `review`, `gate`
and `chair` always run under the vendor's read-only invocation — the exact one a
panel review uses (`claude --disallowed-tools …`, `codex -s read-only`, `agy
--sandbox`). Passing `--allow-write` to them warns and is ignored: those roles
read attacker-controlled content, and a flag must not be able to make a reviewer
write-capable. `implement` and `fix` are the only write-capable roles, and only
with `--allow-write`; without it the command exits 2 rather than quietly running
a read-only agent and reporting work that never happened.

**Exit codes:** `0` the agent ran and produced output, `1` it ran and failed
(read `error_code` — the same fail-soft vocabulary as a panel run), `2` the
request itself was refused.

### Long runs: dispatch now, collect later

```bash
# Start it in the background; prints the run id immediately
jury run-agent --agent agy --role implement --allow-write \
  --prompt-file task.md --detach --run-id issue-661

# ... do other work ...

jury run-agent --status                 # list every recorded run
jury run-agent --wait issue-661         # block, then print the result document
jury run-agent --wait issue-661 --wait-timeout 1800   # ...with your own deadline
```

`--timeout` bounds the **agent**; `--wait-timeout` bounds the **wait**. A bare
`--wait` is not unbounded: it defaults to the run's own timeout plus a minute of
head-room to write its state file, so a script cannot hang on a run that died.
A run that was never started is reported immediately rather than waited on, and
both `--status` and `--wait` report a run whose process is gone as `lost`
rather than leaving it `running` forever — `--wait` returns as soon as it sees
that, instead of blocking for a result that is not coming.

**`running` is a claim, not a guarantee.** The status means "a process with
this pid existed and we have not seen it exit". Pids are recycled, so a state
file that outlives its process — across a reboot, or in a `--cache-dir` shared
between machines or containers — can name a pid that some unrelated process now
holds, and the run keeps reporting `running`. The failure is deliberately
one-directional: a stale pid leaves a finished-looking run marked `running`
(and its `started_at` shows how old the claim is), never the reverse, so a live
run is never declared dead. Liveness is probed on POSIX only; on Windows a pid
cannot be probed without terminating it, so every unfinished run there reads as
`running` and `--wait` says so once on stderr before falling back to its
deadline — the wait is still bounded, it just cannot end early. If you share a cache directory, treat `running` from another host as
unknown and read the state file's `started_at` yourself.

State lives in `<cache-dir>/run-agent/<run-id>.json` (0600, in a 0700 directory)
with the child's console log beside it as `<run-id>.out`. `--cache-dir` and
`$JURY_CACHE_DIR` move both. A run id becomes a filename, so it is restricted to
letters, digits, `.`, `_` and `-`; anything that could point outside the runs
directory is refused.

### From keel, and what keel actually does

`jury run-agent` is the entry point for *any* orchestrator that wants one agent,
one role and one JSON result: the caller picks the agent and the role, ai-jury
owns the argv, the sandbox and the error taxonomy, and the result carries the
attribution the caller can write onto an issue.

**Keel is not that caller today.** Keel's `keel delegate run`
(`src/keel/delegate.py` and `src/keel/delegaterun.py`, keel `main` as of
2026-09-04) is an
independent port of the same contract, not a client of this command: its module
docstring names ai-jury's `src/ai_jury/adapters.py` as a *read-only reference*
for the vendor invocations, and it re-derives the argv shapes, the
role-to-privilege policy and the `agent:<vendor>` / `model:<base>` attribution
label on its own. The string `jury run-agent` does not appear anywhere in keel's
tree — `git grep 'jury run-agent'` there returns nothing. Two implementations
of one contract, agreeing by construction and by review, neither running the
other.

The change that will make keel call the jury is
[keel#1015](https://github.com/berkayturanci/keel/issues/1015), which makes the
panel keel's tier-3 review panel, invoked from `s7`. Update this section again
when that merges.

So the invocation below is the shape a caller uses — written with keel's
environment variable names, since a host agent driving a keel worktree is the
obvious first one:

```bash
jury run-agent --agent "$KEEL_DELEGATE" --role implement --allow-write \
  --cwd "$KEEL_WORKTREE" --prompt-file "$KEEL_PROMPT" --timeout 3600 --detach
```

---

## See also

- [Architecture](architecture.md) — components, round structure, adapters.
- [Configuration](configuration.md) — behavior, budgets, universal provider settings.
- [Platform support matrix](platforms.md) — where you can install and run the jury.
- [Example run](example-run.md) — a deterministic mock report end to end.
- [Live four-vendor review](example-live-review.md) — a real run of the jury
  reviewing its own repository, with honest notes on false positives and cost.
