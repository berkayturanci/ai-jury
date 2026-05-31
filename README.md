# 🏛️ agent-review-council

> Convene a **cross-vendor multi-agent review council**: native coding-agent CLIs from
> different vendors review the *same* pull request, cross-examine each other, and a
> chair synthesizes one verdict.

[![CI](https://github.com/berkayturanci/agent-review-council/actions/workflows/ci.yml/badge.svg)](https://github.com/berkayturanci/agent-review-council/actions/workflows/ci.yml)
[![CodeQL](https://github.com/berkayturanci/agent-review-council/actions/workflows/codeql.yml/badge.svg)](https://github.com/berkayturanci/agent-review-council/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/berkayturanci/agent-review-council/badge)](https://scorecard.dev/viewer/?uri=github.com/berkayturanci/agent-review-council)
[![GitHub release](https://img.shields.io/github/v/release/berkayturanci/agent-review-council)](https://github.com/berkayturanci/agent-review-council/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

![A diff or PR enters; three native vendor agents — Claude Code, Codex, and Antigravity — review it independently and debate each other's findings; a chair agent verifies and synthesizes one verdict (APPROVE / COMMENT / REQUEST CHANGES) plus a markdown report.](docs/assets/hero.png)

> **Install once. Run a cross-vendor review council anywhere.**

Most "multi-model review" tools call models at the **API level**. This one drives each
vendor's **native CLI agent** — `claude` (Claude Code), `codex` (OpenAI Codex CLI), and
`agy` (Google Antigravity) — so every reviewer runs in its own native environment with
its own tooling. Each agent runs headless; the orchestrator owns the round structure.

```
        ┌──────── round 1 ────────┐   ┌──── round 2 ────┐   ┌── synthesis ──┐
diff ──▶ claude  codex  agy  (review) ▶ each rebuts the   ▶ chair agent ▶ verdict
         (parallel, independent)         others' findings    consolidates    + report
```

## Why

Different models miss different things. Running them as an adversarial panel — each
seeing the others' findings and arguing — surfaces more real issues and filters more
false positives than any single reviewer. The research-backed lever is **vendor
heterogeneity**, not more rounds. See [`docs/architecture.md`](docs/architecture.md)
and [`docs/feasibility.md`](docs/feasibility.md).

## Install

```bash
pipx install agent-review-council         # or: pip install -e .
```

Requires Python 3.11+. Install at least one agent CLI (`claude`, `codex`, `agy`);
missing ones are skipped automatically. `gh` is needed for `--pr` / `--post`.

For development, install the dev extras (linting, build, and coverage tooling):

```bash
pip install -e ".[dev]"   # or: make install
```

## Coverage

Test coverage is measured with [`coverage.py`](https://coverage.readthedocs.io/)
— a **dev-only** dependency. The runtime stays standard-library-only.

Measure it locally with one command:

```bash
make coverage          # run the suite under coverage, print the report, write htmlcov/
# or, without make:
./scripts/coverage.sh
```

Either entry point runs:

```bash
python3 -m coverage run -m unittest discover -s tests
python3 -m coverage report
```

**Threshold.** The minimum total coverage is **80%**, configured once in
`pyproject.toml` under `[tool.coverage.report] fail_under` and enforced by a
dedicated `coverage` job in CI (`.github/workflows/ci.yml`, Ubuntu / Python
3.13). CI fails if total coverage drops below that floor. The gate runs in a
single job rather than across the whole test matrix to keep CI cheap and free of
cross-OS path noise.

**Measurement method.** Branch coverage is enabled (`branch = true`) and the
package is measured by import name (`source = ["agent_review_council"]`).

**Exclusions.** Intentionally-untested paths are excluded so the number stays
honest:

- `src/agent_review_council/__main__.py` is omitted (a thin `python -m` entry shim).
- Lines matching these patterns are excluded from the count: `pragma: no cover`,
  `if __name__ == "__main__":`, `raise NotImplementedError`, `if TYPE_CHECKING:`,
  and abstract-method decorators.

Add `# pragma: no cover` to any new line that is genuinely not worth testing.

## Live smoke tests

The default test suite uses **mock adapters only**, so the real native CLIs
are never invoked — a breakage in argv format, stdin handling, or output
capture in the concrete adapters would go unnoticed until a live run. The
optional **live smoke tests** close that gap: they run a tiny, cheap review
prompt (a two-line diff) through each *installed* real adapter and assert the
run succeeds (`ok`, non-empty output, `error_code is None`).

They are **opt-in** and skipped entirely unless `COUNCIL_LIVE=1` is set, so
they never run in `make test` or in CI.

**Requirements** for a meaningful live run:

- The agent CLIs you want to exercise must be installed and on your `PATH`
  (`claude`, `codex`, `agy`) **and authenticated** for non-interactive use.
- Any agent whose CLI is not installed is **skipped individually**, so a
  machine with only `claude` still exercises that one adapter.

**Run them:**

```bash
make live-smoke
# equivalent to:
COUNCIL_LIVE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
```

They are **intentionally excluded from the CI matrix** (no CLIs, auth, or
secrets are available there) and are meant to be run locally before a release
or when touching the adapter layer.

## Usage

```bash
council --pr 123                          # review a GitHub PR
council --pr 123 --post                   # ...and post the verdict as a comment
git diff origin/HEAD... | council --diff-file -   # review the current branch
council --diff-file examples/sample.diff  # review a diff file
council --rounds 1                        # independent review only (no debate)
council --mock --diff-file examples/sample.diff   # offline demo, no live CLIs
council --doctor                          # local readiness check (versions, agents, config)
council --doctor --write diagnostics.json # ...and write a safe JSON report
```

A sample report is in [`docs/example-run.md`](docs/example-run.md).

## Output formats

Use `--format {markdown,json,sarif}` (default `markdown`) to control what is
written to stdout or `--output`. `--metadata-json` is independent and always
writes the metadata block to its own file, and the `--ci` exit code is computed
the same way regardless of format.

```bash
council --diff-file changes.diff --format json  -o report.json
council --diff-file changes.diff --format sarif -o report.sarif
```

### JSON

A structured report with these top-level keys:

| Key | Description |
| --- | --- |
| `schema_version` | Version of this JSON schema (currently `1.0`). |
| `metadata` | Run metadata (agents, rounds, context mode, redaction stats, wall-clock proxy). |
| `findings` | All raw findings; each carries `severity`, `file`, `line`, `claim`, `evidence`, `suggested_fix`, `confidence`, `reviewer`. |
| `consensus` | Per consensus group: `representative` finding, `agreement` count, `reviewers`, `bucket`, `verification_status`. |
| `verdicts` | Verification verdicts (`file`, `line`, `claim`, `status`, `reasoning`). |
| `verdict` | The chair synthesis text, if any. |

The output is deterministic for a deterministic run (e.g. `--mock`) and contains
only legitimate finding fields — never raw diff or prompt text.

### SARIF

Valid [SARIF 2.1.0](https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html)
suitable for GitHub code scanning. Results are drawn from consensus group
representatives (falling back to raw findings). Each result maps to a
`physicalLocation` (`artifactLocation.uri` = file, `region.startLine` = line when
known), `message.text` = the claim, and a stable `ruleId` of `council/<severity>`.
Severity maps to the SARIF `level` as:

| Severity | SARIF level |
| --- | --- |
| `critical`, `major` | `error` |
| `minor` | `warning` |
| `nit`, `info` | `note` |

Upload to GitHub code scanning:

The standard way is the `github/codeql-action/upload-sarif` GitHub Action.
Results then show up in the PR's **Code scanning** view and the repo's
**Security** tab. The job needs `security-events: write` (to upload) and
`contents: read`:

```yaml
name: Council code scanning

on:
  pull_request:

permissions:
  contents: read
  security-events: write   # required by upload-sarif

jobs:
  council:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # need the base commit to diff against

      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      - name: Install council
        run: pip install agent-review-council   # or: pip install .

      - name: Produce SARIF from the PR diff
        run: |
          git diff "origin/${{ github.base_ref }}...HEAD" > pr.diff
          council --diff-file pr.diff --format sarif -o council.sarif

      - name: Upload to code scanning
        uses: github/codeql-action/upload-sarif@4dd16135b69a43b6c8efb853346f8437d92d3c93 # v3.26.6
        with:
          sarif_file: council.sarif
```

This uses a diff file so no agent CLIs or `gh` token are required to *generate*
the SARIF. To review the PR via `--pr` instead (which shells out to `gh`), set
`GH_TOKEN: ${{ github.token }}` on that step and ensure the agent CLIs are
installed and authenticated on the runner.

As a manual alternative, upload an existing SARIF file with `gh`:

```bash
gh api -X POST repos/OWNER/REPO/code-scanning/sarifs \
  -f commit_sha="$SHA" -f ref="$REF" \
  -f sarif="$(gzip -c report.sarif | base64 -w0)"
```

## Configuration — `council.toml`

```toml
[council]
rounds = 2          # 1 = review only, 2 = review + debate
chair  = "claude"   # which agent synthesizes the verdict
timeout = 300       # per-agent wall-clock seconds (a hung CLI is killed at this bound)
parallel = true

[[agent]]
name = "claude"
vendor = "anthropic"   # anthropic | openai | google
command = "claude"
# model = "claude-opus-4-8"
extra_args = ["--output-format", "text", "--disallowed-tools", "Edit,Write,NotebookEdit,Bash", "--dangerously-skip-permissions"]
```

Override per run with `--rounds`, `--chair`, `--config`.

The config is validated on every run. Check it without running a review with `council --config-validate` (exit `0` valid, `2` invalid); add `--strict-config` to turn warnings into errors. See [docs/configuration.md](docs/configuration.md) for the full schema — every field, allowed values, defaults, and which problems are hard errors vs. warnings.

## Data flow / privacy

What gets sent to each agent is governed by `[council.context]` in
`council.toml` (and overridable per run on the CLI):

```toml
[council.context]
mode = "diff-only"      # "diff-only" (default) or "expanded"
redact_secrets = true   # scrub recognized secrets before sending (default on)
```

**Context modes** — what leaves your machine for each reviewer:

- `diff-only` (**default**): agents receive **only the diff**. Any surrounding PR
  context (title/body) is dropped. This is the smallest data surface.
- `expanded`: agents additionally receive the PR title/body context (when
  reviewing with `--pr`) to improve review quality. Use this only when you trust
  the configured agent endpoints.

Either way, no source files outside the diff, no repository history, and no
environment variables are read or sent.

**Secret redaction** — before anything is sent to an agent, the diff (and any
context) is passed through a redactor (`src/agent_review_council/redaction.py`)
that masks recognized secrets: PEM private keys, AWS access keys, GitHub/OpenAI
tokens, `Bearer` tokens, and generic `api_key`/`secret`/`token` assignments
(including base64-style values). Each hit becomes `[REDACTED:<kind>]`. Redaction
is **on by default**.

**Controls:**

- `council.toml`: `[council.context] mode = "diff-only"|"expanded"` and
  `redact_secrets = true|false`.
- CLI (override config for a single run): `--context-mode diff-only|expanded`,
  and `--redact` / `--no-redact`.

Posting to GitHub (`--post-summary`, `--post-inline`) sends the rendered report
/ comments to the GitHub API; use `--dry-run` with `--post-inline` to preview the
inline payload without any network call. See [SECURITY.md](SECURITY.md) for the
full data-flow and redaction reference.

**No telemetry (by default and always)** — this project collects and sends **no
telemetry** and **no analytics**, not now and not behind any opt-in flag. The
tool never phones home. The only network activity is performed by the agent CLIs
you explicitly configure (and `gh` for `--pr` / `--post*`).

### Diagnostics — `council --doctor`

Run a local readiness check that surfaces common configuration problems:

```bash
council --doctor                          # print a readable report
council --doctor --write diagnostics.json # also write the report as JSON
```

The report covers the tool version, Python version, OS, a config summary
(rounds, chair, context mode, enabled agents), which agent CLIs are available on
your `PATH`, and any detected config warnings. The output is **safe to share**:
secret-like config values are redacted via the same redactor used for prompts,
and the report **never** includes the diff under review or any agent output.
Diagnostics are built locally and only written to disk when you pass
`--write PATH`.

## Use it from another project (skill)

A Claude Code skill ships in [`skill/review-council/`](skill/review-council/SKILL.md).
Install it as a **plugin** from this repo (it doubles as a single-plugin marketplace):

```text
/plugin marketplace add berkayturanci/agent-review-council
/plugin install review-council@agent-review-council
```

Or drop [`skill/review-council/`](skill/review-council/SKILL.md) into a project's
`.claude/skills/` manually. Either way the agent can convene the council on demand, and
it composes with existing review workflows: run the council for a cross-vendor pass,
then act on the consensus findings. For other platforms (Codex, Antigravity, CI) and
their support status, see the [platform support matrix](docs/platforms.md).

## How it works

| Module | Responsibility |
|:--|:--|
| `config.py` | Load `council.toml` (or built-in default) |
| `adapters.py` | One adapter per vendor CLI; turns a prompt into a headless subprocess |
| `orchestrator.py` | Round structure: review → debate → synthesis (agents run in parallel) |
| `prompts.py` | The three prompt templates |
| `report.py` | Render the run as one markdown report |
| `github.py` | `gh`-based PR diff in / comment out |

### Report format contract

The markdown report is the tool's user-facing output and a contract for
downstream skill/workflow consumers, so it changes only deliberately.
`tests/test_report_golden.py` renders the report for several scenarios (full
council run, single-round, verified-finding, failed-agent, missing-agent) and
compares each against a committed snapshot in `tests/golden/*.md`. Unintended
formatting drift fails CI; an intentional change shows up as a reviewable
fixture diff. Durations (the only non-deterministic token) are normalized to
`0s` before comparison. Regenerate fixtures after an intentional change with
`UPDATE_GOLDEN=1 PYTHONPATH=src python3 -m unittest tests.test_report_golden`.
See [`docs/report-format.md`](docs/report-format.md) for details.

## Prior art & how this differs

This is a known pattern, not a new invention. The closest project is
**[Magpie](https://github.com/liliu-z/magpie)** (multi-vendor CLI review + debate, with a
[benchmark](https://milvus.io/blog/ai-code-review-gets-better-when-models-debate-claude-vs-gemini-vs-codex-vs-qwen-vs-minimax.md)
showing debate lifts bug detection to ~80%); see also
[agent-council](https://github.com/yogirk/agent-council),
[the-council](https://github.com/DantesPeak85/the-council), and Mozilla.ai's
[Star Chamber](https://blog.mozilla.ai/the-star-chamber-multi-llm-consensus-for-code-quality/).
`agent-review-council` aims to be the **smallest drop-in** version: stdlib-only Python, a
single `council.toml`, and a Claude Code skill that snaps into an existing repo's review
workflow. See the [ecosystem comparison & capability matrix](docs/comparison.md) for how
it differs from hosted, API-level, and other native-CLI tools, and
[`docs/feasibility.md`](docs/feasibility.md) for the supporting research.

## Status

MVP (v0.1). Cross-vendor round 1 (review) + round 2 (debate) run end-to-end with the real
CLIs; the offline `--mock` path is covered by tests. Roadmap (from the research): a
verify/audit pass that re-reads code to kill false positives, JSON structurizer +
tiered consensus (consensus / majority / individual), anonymized rebuttal to curb
position bias, and severity-gated CI exit codes. See [`docs/architecture.md`](docs/architecture.md).

The phased plan and how to pick up a session's worth of work is in [`ROADMAP.md`](ROADMAP.md);
issues are tracked under [milestones](https://github.com/berkayturanci/agent-review-council/milestones).

## Security & the Codex sandbox

The council performs **read-only review orchestration** — it sends a diff to each agent CLI and collects their feedback; it does not apply edits.

The Codex adapter pipes the prompt on **stdin** (`codex exec` with no positional prompt) so non-interactive runs never hang waiting for input, and defaults `extra_args` to `["-s", "danger-full-access"]`. Codex's standard sandbox can block the outbound network / `gh` access that PR review needs, which would surface as failures instead of findings; full access keeps runs reliable without changing what the tool itself does. (Avoid `--full-auto`, which implies a stricter workspace sandbox.)

Want tighter sandboxing? Override `extra_args` for the `codex` agent in `council.toml` (e.g. a narrower `-s` mode). See [docs/security.md](docs/security.md) for details.

## CLI compatibility contract

The `council` command is this project's public API. The surfaces below are
**stable** and are locked by `tests/test_cli_contract.py` (including a
width/color-pinned snapshot of `council --help` under `tests/golden/`) so
accidental changes are caught in review.

**Stable flags** (names, short aliases, and semantics):
`--pr`, `--repo`, `--diff-file`, `--config`,
`--context-mode {diff-only,expanded}`, `--redact` / `--no-redact`, `--rounds`,
`--chair`, `--mock`, `--strict`, `--verify` / `--no-verify`, `-o` / `--output`,
`--post-summary` / `--post`, `--post-inline`, `--dry-run`, `--ci`, `--fail-on`,
`-q` / `--quiet`, `--version`, `-h` / `--help`.

**Stable error messages and exit codes:**

| Condition | Behavior |
| --- | --- |
| No input source given | exits non-zero with `error: provide one of --pr, --diff-file (or --diff-file - for stdin)` |
| Empty diff | exits non-zero with `error: empty diff — nothing to review` |
| `--post-summary` without `--pr` | exits non-zero with `error: --post-summary requires --pr` |
| `--post-inline` without `--pr` | exits non-zero with `error: --post-inline requires --pr` |
| Unknown flag / bad arguments | argparse exits with code `2` |
| `--version` | prints `council <version>` and exits `0` |
| Successful review (no `--ci`) | exits `0` |
| `--ci` with blocking findings remaining | exits non-zero (see `ci.evaluate_ci`) |

**Stable report headings** (substrings other tooling may parse):
`Agent Review Council`, `Chair verdict`, `Round 1` (and subsequent `Round N`).

**Policy:** Any breaking change to the surfaces above — renaming or removing a
flag, changing an error message or exit code, or altering a report heading —
**requires a `CHANGELOG.md` entry** describing the break. When the change is
intentional, regenerate the help snapshot with
`UPDATE_GOLDEN=1 PYTHONPATH=src python3 -m unittest tests.test_cli_contract`.
The help-snapshot exact match is pinned to Python 3.13 argparse formatting; the
flag-presence checks run on all supported versions (3.11–3.13).

## Documentation

- [Positioning](docs/positioning.md) — mission, what makes it different, principles, and non-goals.
- [Cookbook](docs/cookbook.md) — copy-paste recipes for local, PR, advisory-comment, CI, mock, and assistant workflows.
- [Architecture](docs/architecture.md) — components, round structure, adapters, supported platforms.
- [Ecosystem comparison](docs/comparison.md) — capability matrix vs hosted / API-level / native-CLI tools.
- [Feasibility & prior art](docs/feasibility.md) — research grounding and verified CLI invocations.
- [Platform support matrix](docs/platforms.md) — where you can install/run the council and how.
- [Skill packaging & install](docs/skill.md) — install/version the review council as a reusable skill artifact.
- [Release readiness checklist](docs/release-checklist.md) — the bar before a public release.
- [Report format contract](docs/report-format.md) — the golden-file snapshot tests and how to regenerate them.
- [SECURITY.md](SECURITY.md) — data-flow and secret-redaction reference.
- Agent-readable: [`llms.txt`](llms.txt) (concise) and [`llms-full.txt`](llms-full.txt) (full reference).

## License

MIT — see [LICENSE](LICENSE).
