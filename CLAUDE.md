# CLAUDE.md

Guidance for working in this repository.

## What this is

`ai-jury` is a small, **stdlib-only** Python CLI (`jury`) that
orchestrates native coding-agent CLIs from different vendors — plus an optional
local / open-weight model — to review the same diff/PR, debate, verify, and
synthesize one verdict. Entry point: `ai_jury.cli:main`.

## Hard constraints

- **Zero runtime dependencies.** Standard library only (`subprocess`, `tomllib`,
  `urllib`, `concurrent.futures`, `argparse`, …). Do **not** add a runtime
  dependency. Dev-only tools (`ruff`, `build`, `coverage`) live in the `dev`
  extra. Talk to local model servers over HTTP via `urllib`, not `requests`.
- **Python ≥ 3.11.** `requires-python` in `pyproject.toml` is the source of truth.
- **Read-only / secure by default.** Reviewers process attacker-controlled diffs,
  so agents run sandboxed (Claude `--disallowed-tools …`, Codex `-s read-only`,
  Antigravity `--sandbox`). `privilege.py` audits this. Don't loosen defaults.
- **Project-agnostic.** No downstream/private project names or workflows in core.

## Commands

```bash
make test        # PYTHONPATH=src python3 -m unittest discover -s tests  (offline, no network)
make smoke       # jury --mock --diff-file examples/sample.diff
make lint        # ruff check .
make coverage    # coverage gate (fail_under in pyproject.toml [tool.coverage.report])
```

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
- **CLI subcommands are argv-intercepts.** `jury init|config|comment|cache clear`
  are handled in `cli.main` *before* `argparse`, so the main flag surface stays
  flat. The public flag set is locked by `tests/test_cli_contract.py`
  (`DOCUMENTED_FLAGS`) — update it when you add a top-level flag.
- **Determinism.** Identical inputs ⇒ identical output (no wall-clock/random in
  logic). The markdown report is golden-tested.

## When you change…

- **Report rendering** → regenerate goldens: `UPDATE_GOLDEN=1 PYTHONPATH=src
  python3 -m unittest tests.test_report_golden`; review the fixture diff.
- **CLI `--help` / flags** → regenerate the help golden + update `DOCUMENTED_FLAGS`:
  `UPDATE_GOLDEN=1 PYTHONPATH=src python3 -m unittest tests.test_cli_contract`.
- **Run metadata shape** → bump `metadata.SCHEMA_VERSION` and update
  `tests/test_metadata.py`.
- **Prompt templates** → bump `prompts.PROMPT_VERSION` (invalidates the cache).
- **`jury.toml` schema** → update `config.py` (dataclass, `_from_dict`,
  `validate_config`, `config_hash`, `KNOWN_*`), `docs/configuration.md`, and the
  `scaffold.py` templates used by `jury init`.
- Any user-visible change → add a `CHANGELOG.md` entry under `[Unreleased]`.

## Module map

`orchestrator` (pipeline + `RunBudget` + `review_diff`/chunk-merge) · `adapters`
(per-vendor + `LocalAdapter` + error taxonomy) · `config` · `findings`/`consensus`
(structured findings + tiered grouping) · `convergence` (adaptive early-stop) ·
`largediff` (filter + chunk) · `cache` · `incremental` · `patches` · `commands`
(comment parsing) · `scaffold` (`jury init`) · `doctor` · `privilege` ·
`redaction` · `injection` · `classification` · `metadata` · `report`/`formats`
(markdown/json/sarif) · `ci` · `github` · `policy`.

## Git / PR workflow

- Branch off `main`; **merge only via `gh pr merge`** (squash). No direct pushes
  to `main`, no force-push, no bulk branch deletes.
- The **authoritative CI** is the hosted `ci.yml` (cross-OS × Python matrix +
  coverage gate), run per push/PR on free public-repo minutes. CodeQL + Scorecard
  also run per-commit. There is no self-hosted runner. A `v*` tag triggers
  `publish.yml` (PyPI trusted publishing); any push to `main` (plus on-demand)
  deploys Pages via `pages.yml`.
- Keep changes focused; one concern per PR. Reference the issue (`Closes #N`).
