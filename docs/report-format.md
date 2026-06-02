# Report format contract

The markdown report produced by `ai_jury.report.render` is the
tool's primary **user-facing output**. Downstream skill and workflow consumers
(for example the Claude Code skill in `skill/ai-jury/`, and anything that
posts the report to GitHub) depend on its structure, so the format is treated as
a contract: it should only change deliberately.

## How the contract is enforced

`tests/test_report_golden.py` contains golden-file (snapshot) tests. For each
scenario it renders a report, normalizes the non-deterministic bits, and
compares the result against a committed snapshot under `tests/golden/`. If the
rendered output drifts from the snapshot, the test fails — so accidental
formatting changes are caught in CI.

Scenarios with committed fixtures:

| Fixture | Scenario |
| --- | --- |
| `tests/golden/full_report.md` | Standard full jury run: 3 agents, two rounds, consensus + structured findings. |
| `tests/golden/single_round_report.md` | `rounds = 1`, so there is no Round 2 (debate) section. |
| `tests/golden/verified_finding_report.md` | `verify = true`, adding the Verification section and per-group verification statuses. |
| `tests/golden/failed_agent_report.md` | A reviewer (and a debater) errored (`ok = False`). |
| `tests/golden/missing_agent_report.md` | A configured agent did not run and the chair synthesis failed. |

The structured/verified cases are produced by running the real pipeline with
`run_jury(..., mock=True)` (the `MockAdapter` emits stable, phase-aware
output). The failed/missing-agent cases hand-construct `AgentResult` lists so the
error paths are exercised deterministically.

## Determinism

The only non-deterministic part of a rendered report is the per-agent duration
token, printed as `{duration_s:.0f}s` (e.g. `3s`). The tests normalize every such
token to `0s` before comparing or writing fixtures (and the mock pipeline already
emits `duration_s = 0.0`), so timing never causes a spurious diff.

## Reviewing an intentional format change

When you intentionally change the report format, the diff shows up directly in
the relevant `tests/golden/*.md` file. Reviewers can therefore read the new
report verbatim in the pull request, rather than mentally reconstructing it from
renderer code.

## Regenerating the fixtures

After an intentional change, regenerate the snapshots by setting `UPDATE_GOLDEN=1`:

```bash
UPDATE_GOLDEN=1 PYTHONPATH=src python3 -m unittest tests.test_report_golden
```

This rewrites the `.md` files under `tests/golden/` with the current output
instead of asserting. Review the resulting fixture diff like any other change,
then commit it alongside the renderer change. Running the suite normally (without
the env var) asserts the output matches the committed fixtures:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```
