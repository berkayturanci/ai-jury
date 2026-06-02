# Workflow cookbook

Practical, copy-paste recipes for running `agent-review-council` in day-to-day
work. Each recipe states its **prerequisites**, the exact **command(s)**, and the
**expected outcome**.

`council --help` (and [`cli.py`](../src/agent_review_council/cli.py)) is the
single source of truth for flags; every command below uses only real flags.
Anything that should run without credentials uses `--mock`, which executes the
full pipeline offline with deterministic mock agents — no live CLIs are called,
no network, no keys.

> **Running from a clone (no install).** All recipes assume `council` is on PATH
> (`pipx install agent-review-council`). From a checkout you can swap `council`
> for `PYTHONPATH=src python3 -m agent_review_council` — every flag is identical.

---

## 0. First-time setup — scaffold a config

**Prerequisites:** none (works before any agent is configured).

Generate a `council.toml` instead of hand-writing it. `council init` detects which
agent CLIs are installed and, for a local agent, the models on your Ollama/OpenAI-
compatible server:

```bash
council init                      # interactive: pick agents, rounds, chair, local model
council init --preset offline     # free, local-only ($0); also: fast / balanced / thorough
council init --list-agents        # show known agents + availability
council init --list-models        # list local (Ollama) models you can pick
council init --agents claude,codex,qwen --rounds 2   # non-interactive / scriptable
```

> **Zero-config offline:** even without a `council.toml`, if no agent CLI is
> installed but a local model server is reachable, `council` adds a local agent
> automatically — so `git diff main... | council --diff-file -` just works offline.

**Outcome:** a validated `council.toml` using the secure-by-default agent templates
(Codex read-only, Antigravity sandboxed, Claude write-tool denylist). It won't overwrite
an existing file without `--force`.

---

## 1. Review a local branch before opening a PR

**Prerequisites:** at least one agent CLI (`claude`, `codex`, or `agy`). No `gh`
needed — this reviews a diff, not a PR.

Pipe the branch diff straight into the council via stdin (`--diff-file -`):

```bash
git diff main... | council --diff-file -
```

`main...` (three dots) diffs your branch against its merge-base with `main`, so
you review exactly what the PR would contain. Use `origin/HEAD...` if your
default branch isn't `main`:

```bash
git diff origin/HEAD... | council --diff-file -
```

Or capture the diff to a file first, then review it:

```bash
git diff main... > /tmp/branch.diff
council --diff-file /tmp/branch.diff
```

**Outcome:** a markdown report on stdout headed `# 🏛️ Agent Review Council`
with a recommendation, a Findings section, and the per-agent review rounds. Act
on the findings before you open the PR. (If the diff is empty, the CLI exits with
`error: empty diff — nothing to review`.)

---

## 2. Review a GitHub PR and save the report

**Prerequisites:** `gh` installed and authenticated (`gh auth status`), plus at
least one agent CLI.

```bash
council --pr 123 -o report.md
```

`--pr` fetches the diff via `gh`; `-o/--output` writes the rendered report to a
file instead of stdout. Add `--repo owner/name` if you're not inside the target
repo's checkout:

```bash
council --pr 123 --repo owner/name -o report.md
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
council --pr 123 --post-summary
```

`--post` is an accepted alias for `--post-summary`. To attach findings as inline
review comments on the relevant lines instead:

```bash
council --pr 123 --post-inline
```

**Preview before posting.** Combine `--post-inline` with `--dry-run` to print the
exact inline payload without making any GitHub call — nothing is posted:

```bash
council --pr 123 --post-inline --dry-run
```

You can also write the report locally and post in the same run:

```bash
council --pr 123 --post-summary -o report.md
```

**Outcome:** with `--post-summary`/`--post-inline`, the council's feedback lands
on the PR as advisory comments (`--post-*` require `--pr`). With `--dry-run`, you
see what *would* be posted and the network is never touched. These are advisory
by design — they comment, they don't block a merge; gating is the separate `--ci`
concern (see recipe 5).

---

## 4. Run the bundled skill from an assistant

**Prerequisites:** Claude Code, the `council` CLI reachable from the assistant's
shell, and at least one agent CLI. For PR review/posting, `gh` authenticated.

Install the skill as a plugin (this repo doubles as a single-plugin
marketplace):

```text
/plugin marketplace add berkayturanci/agent-review-council
/plugin install review-council@agent-review-council
```

Or drop [`skill/review-council/`](../skill/review-council/SKILL.md) into a
project's `.claude/skills/` directory manually.

Then ask the assistant for a review, e.g.:

> Convene the review council on my current branch.

**Outcome:** the skill shells out to `council` (typically
`git diff origin/HEAD... | council --diff-file -` for the working branch, or
`council --pr <n>` for a PR) and reports the chair verdict plus consensus
findings back in the conversation. You decide what to act on. See the
[platform support matrix](platforms.md) for other surfaces.

---

## 5. Add a soft / advisory review stage to a ship workflow

**Prerequisites:** at least one agent CLI in the CI environment. For PR-triggered
runs, `gh` authenticated (CI typically provides a token). Use `--mock` for a
pipeline smoke test with no agents at all.

The council fits as a **non-blocking, advisory** stage: run it, surface the
verdict, but don't fail the build on its opinion. Keep the step from breaking the
pipeline by neutralizing its exit code:

```bash
# Advisory: always succeeds, posts the verdict, never blocks the merge.
council --pr "$PR_NUMBER" --post-summary || true
```

As a generic shell stage in any ship script:

```bash
# Soft review gate — report-only, exit status ignored.
git diff origin/HEAD... | council --diff-file - -o council-report.md || true
echo "Council report saved to council-report.md (advisory)."
```

If you later want the council to actually **gate** merges, opt in explicitly with
`--ci`, which exits non-zero when blocking findings remain:

```bash
# Hard gate — fails the build on critical/major findings.
council --pr "$PR_NUMBER" --ci --fail-on critical,major
```

`--fail-on` overrides the `[council.ci] fail_on` severities in `council.toml`;
drop the trailing `|| true` to let the stage fail.

**Outcome:** the advisory form always reports and never blocks; the `--ci` form
turns the council into an enforced quality gate. Start advisory, graduate to
`--ci` once the team trusts the signal.

---

## 6. Run in mock mode for smoke testing

**Prerequisites:** none. `--mock` runs the entire pipeline offline with
deterministic mock agents — no live CLIs, no `gh`, no credentials.

```bash
council --mock --diff-file - < examples/sample.diff
```

Or against any diff file or piped branch diff:

```bash
council --mock --diff-file examples/sample.diff
git diff main... | council --mock --diff-file -
```

You can exercise the CI gate offline too:

```bash
council --mock --ci --fail-on critical,major --diff-file examples/sample.diff
```

**Outcome:** a complete markdown report (headed `# 🏛️ Agent Review Council`)
is produced without contacting any agent or GitHub. This is the fastest way to
confirm your install, config, and command shapes are correct before wiring in
real agents — and it's safe to run anywhere.

---

## 7. Use only one or two agent CLIs

**Prerequisites:** at least one agent CLI installed. Missing agents are skipped
with a warning automatically, so you can also simply install fewer.

**Option A — disable agents in `council.toml`.** Set `enabled = false` on the
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

**Option B — point at a custom config.** Keep your default `council.toml` intact
and pass a slimmed-down one per run with `--config`:

```bash
council --config ./two-agents.toml --diff-file -
```

**Option C — rely on auto-skip.** If an agent CLI simply isn't installed, the
council skips it with a warning and continues with whatever is available. (Pass
`--strict` to instead fail when any configured agent CLI is missing.)

**Outcome:** the council runs with just the agents you enabled/installed. With a
single agent there's no cross-vendor debate (the debate round needs ≥2 successful
reviews), but you still get a structured verdict and report — handy while
trialing the tool with one CLI.

> **Heads-up:** the research lever behind this tool is **vendor heterogeneity** —
> two or three *different* vendors catch more than one reviewer run repeatedly.
> Trimming to one agent is fine for smoke tests and cost control, but use ≥2
> vendors when you want the real council effect.

---

## 8. Incremental review on PR updates (issue #9)

Re-reviewing a large PR's full diff on every push is slow. With `--incremental`,
the council records the reviewed head SHA on its summary comment and, on the next
run, reviews only the range since that SHA — falling back to a full review when
no prior marker exists or the head is unchanged.

```bash
# First run establishes the marker on the posted summary.
council --pr 123 --post-summary

# Later runs review only what changed since the last council run.
council --pr 123 --incremental --post-summary
```

The report's header states the scope (`Review scope: Incremental — …` or
`Full — …`). The marker is a hidden HTML comment, so it is invisible to readers.

## 9. Suggested patches for verified findings (issue #10)

`--suggest-patches` emits a **separate**, opt-in section that turns *verified*
findings into inspectable fix suggestions. It is read-only — nothing is applied
automatically, and unverified/rejected findings never produce a suggestion.

```bash
# Append a "Suggested patches" section after the markdown report.
council --pr 123 --suggest-patches

# Or write the patches to their own file, leaving the report untouched.
council --diff-file changes.diff --suggest-patches --patches-out patches.md -o report.md
```

## 10. Comment-triggered runs in GitHub Actions (issue #11)

The council can be triggered from a PR comment such as `/council review` or
`/council summary` via a workflow. Commands are parsed by an **allowlist**
(`review`, `summary`; only the `--rounds N` flag) — the comment text is never
passed to a shell, so arbitrary commands in a comment cannot run. Parsing is
exposed through the `council comment` mode:

```bash
# Resolve a comment to a safe council argv (used by the workflow):
council comment --text "/council review --rounds 1" --pr 123 --print-args
#   -> --rounds 1 --pr 123 --post-summary

# Reject anything not on the allowlist (exit 2):
council comment --text "/council deploy" --print-args   # rejected
```

A minimal, safe workflow recipe (gate on a trusted author association and only
on PR comments):

```yaml
# .github/workflows/council-comment.yml
name: council-comment
on:
  issue_comment:
    types: [created]
permissions:
  contents: read
  pull-requests: write
jobs:
  council:
    # Only PR comments, only from maintainers/owners, only the /council trigger.
    if: >
      github.event.issue.pull_request &&
      contains(github.event.comment.body, '/council') &&
      contains(fromJSON('["OWNER","MEMBER","COLLABORATOR"]'), github.event.comment.author_association)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.13" }
      - run: pip install agent-review-council
      - name: Run council from the comment command
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          COMMENT_BODY: ${{ github.event.comment.body }}
          PR_NUMBER: ${{ github.event.issue.number }}
        # The comment body is passed as an argument value (never via a shell
        # template), and the council allowlist rejects anything unsupported.
        run: council comment --text "$COMMENT_BODY" --pr "$PR_NUMBER"
```

> The `if:` guard restricts *who* can trigger a run; the `council comment`
> allowlist restricts *what* can run. Both layers matter — keep the author
> association check so untrusted forks cannot trigger agent runs.

---

## See also

- [Architecture](architecture.md) — components, round structure, adapters.
- [Platform support matrix](platforms.md) — where you can install and run the council.
- [Example run](example-run.md) — a deterministic mock report end to end.
- [Live four-vendor review](example-live-review.md) — a real run of the council
  reviewing its own repository, with honest notes on false positives and cost.
