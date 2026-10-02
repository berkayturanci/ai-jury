# AGENTS.md

The canonical cross-vendor instruction file for this repository. `CLAUDE.md` and
`GEMINI.md` are pointers to it, so there is one copy of every rule below and nothing to
drift.

Guidance for working in this repository.

## What this is

`ai-jury` is a small, **stdlib-only** Python CLI (`jury`) that
orchestrates native coding-agent CLIs from different vendors — plus optional
hosted-API seats (vendor APIs called over HTTP, keyed by an env var) and an
optional local / open-weight model — to review the same diff/PR, debate, verify,
and synthesize one verdict. Entry point: `ai_jury.cli:main`.

## Hard constraints

- **Zero runtime dependencies.** Standard library only (`subprocess`, `tomllib`,
  `urllib`, `concurrent.futures`, `argparse`, …). Do **not** add a runtime
  dependency. Dev-only tools (`ruff`, `build`, `coverage`) live in the `dev`
  extra. Talk to local model servers over HTTP via `urllib`, not `requests`.
- **Python ≥ 3.11.** `requires-python` in `pyproject.toml` is the source of truth.
- **Read-only / secure by default.** Reviewers process attacker-controlled diffs,
  so agents run sandboxed (Claude with no tools: `--tools ""`, `--disallowed-tools …`,
  `--strict-mcp-config`, `--safe-mode`; Codex `-s read-only`;
  Antigravity `--sandbox`, which does not confine it — so agy is opt-in only,
  never in the default panel, and always flagged). `privilege.py` audits this.
  Don't loosen defaults.
- **Project-agnostic.** No downstream/private project names or workflows in core.

## Commands

```bash
make test        # python3 -m unittest discover -s tests -v  (offline, no network; needs `make install`)
make smoke       # jury --mock --diff-file examples/sample.diff
make lint        # ruff check .
make coverage    # coverage gate (fail_under in pyproject.toml [tool.coverage.report])
```

`make test` sets no `PYTHONPATH`, so it imports the installed package — run
`make install` (editable) first, or run the suite from a bare checkout with
`PYTHONPATH=src python3 -m unittest discover -s tests`.
Run a single test module: `PYTHONPATH=src python3 -m unittest tests.test_<name>`.
Live agent tests are opt-in: `JURY_LIVE=1` (CLIs) / `JURY_LOCAL_LIVE=1`
(local model). Tests must pass offline with no credentials.

## Design conventions (match these)

- **Pure core + thin I/O.** Put deterministic logic in a pure, unit-tested
  function/module; keep network/subprocess/prompting in a thin wrapper. Examples:
  `consensus.py`, `convergence.py`, `largediff.py`, `scaffold.py`, `classification.py`.
- **Orchestrator owns prompts; adapters are thin.** `orchestrator.py` owns the
  round structure (review → debate → verify → synthesis); each adapter in
  `adapters.py` only knows how to invoke one agent. Adding a vendor is ~20 lines.
- **Adapters fail soft.** A missing CLI, nonzero exit (even with stdout), timeout,
  or unreachable local endpoint → non-fatal `AgentResult(ok=False, …)` with a
  typed `ERR_*` code. The run continues unless `--strict`.
- **CLI subcommands are argv-intercepts.** `jury init|config|comment|apply|run-agent|replay`
  and `jury cache clear` (plus `jury examples` / `jury guide`, matched only as the
  whole argv) are handled in `cli.main` *before* `argparse`, so the main flag surface stays
  flat. The public flag set is locked by `tests/test_cli_contract.py`, which
  compares the parser with the README's "Stable flags" list and the flag tables
  of `docs/parameters.md` — list a new top-level flag in both.
- **Determinism.** Identical inputs ⇒ identical output (no wall-clock/random in
  logic). The markdown report is golden-tested.

## When you change…

- **Report rendering** → regenerate goldens: `UPDATE_GOLDEN=1 PYTHONPATH=src
  python3 -m unittest tests.test_report_golden`; review the fixture diff.
- **CLI `--help` / flags** → regenerate the help golden, and list the flag in the
  README's "Stable flags" and `docs/parameters.md`:
  `UPDATE_GOLDEN=1 PYTHONPATH=src python3 -m unittest tests.test_cli_contract`.
- **Run metadata shape** → bump `metadata.SCHEMA_VERSION` and update
  `tests/test_metadata.py`.
- **Prompt templates** → bump `prompts.PROMPT_VERSION` (invalidates the cache).
- **`jury.toml` schema** → update `config.py` (dataclass, `_from_dict`,
  `validate_config`, `config_hash`, `KNOWN_*`), `docs/configuration.md`, and the
  `scaffold.py` templates used by `jury init`.
- Any user-visible change → add a `CHANGELOG.md` entry under `[Unreleased]`.

## Module map

`cli` (argv intercepts + the main flag surface) · `orchestrator` (pipeline +
`RunBudget` + `review_diff`/chunk-merge) · `prompts` (the phase templates) ·
`adapters` (native CLIs, `LocalAdapter`, the hosted-API adapters —
`anthropic-api`/`openai-api`/`google-api`/`xai-api`/`openai-compatible` — the
generic `cli` adapter, and the error taxonomy) · `config` · `configtrust` (the
trust gate for an auto-discovered `jury.toml`) · `findings`/`consensus`
(structured findings + tiered grouping) · `convergence` (adaptive early-stop) ·
`voting` (`decision = "vote"` tally + abstentions) · `panel` (review arithmetic for
`min_reviews`) · `diffprofile` (risk profile for `--auto`) · `routing` (`--tiered`
panels) · `hints` (`--hints` linter pre-pass) · `largediff` (filter + chunk) ·
`cache` · `incremental` · `patches` · `commands` (comment parsing) · `scaffold`
(`jury init`) · `doctor` · `privilege` · `redaction` · `injection` ·
`classification` · `metadata` · `report`/`formats`
(markdown/json/sarif/keel-reviews) · `ballots` (per-reviewer ballots + the
keel-reviews bundle) · `ci` · `github` · `policy` · `runagent` (`jury run-agent`) ·
`theater`/`replay` (`--theater` scene, `jury replay`) · `events` (`--events-file`
NDJSON progress for an outside watcher) · `benchmark` (offline
review-quality benchmark).

## Git / PR workflow

- Branch off `main`; **merge only via `gh pr merge`** (squash). No direct pushes
  to `main`, no force-push, no bulk branch deletes.
- The **authoritative CI** is the hosted `ci.yml` (cross-OS × Python matrix +
  coverage gate), run per push/PR on free public-repo minutes. CodeQL + Scorecard
  also run per-commit. There is no self-hosted runner. A `v*` tag triggers
  `publish.yml` (PyPI trusted publishing); any push to `main` (plus on-demand)
  deploys Pages via `pages.yml`.
- Keep changes focused; one concern per PR. Reference the issue (`Closes #N`).
- Every PR needs a real description + a linked issue: the `pr-lint` workflow
  (`.github/workflows/pr-lint.yml`) fails an empty/near-empty body or one with no
  `#N` reference (write `no issue` to opt out). Fill the PR template's
  `Related issues` section.
