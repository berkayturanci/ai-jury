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

## Per-reviewer ballots (`reviewers`)

The consolidated `findings` and `consensus` views say *what the panel found*.
They do not say *who said what* — and a consumer that wants the panel to **be**
its review (one head-pinned verdict per panelist, attributed to the vendor that
produced it) needs exactly that. Since v1.1 of the JSON schema the
`jury --format json` report therefore carries a top-level `reviewers` array
(issue #663).

`reviewers` is **purely additive**. No existing key was removed, renamed or
reshaped, so a consumer of `findings`/`consensus`/`verdicts`/`verdict`/`metadata`
reads an identical document; `tests/test_formats.py::BackwardCompatibility` pins
that field for field. `schema_version` moved `1.0` → `1.1` to signal the addition.
Markdown and SARIF output are unchanged.

One entry per panelist that **returned output**, in the stable panel order, then
the chair:

| Field | Meaning |
| --- | --- |
| `name` | The agent slot's name, as configured. |
| `vendor` | The adapter's vendor (`anthropic`, `openai`, …), **as configured**. A seat on the generic fallback keeps its own string here and counts as `cli` only in `metadata.panel.vendors`. |
| `model` | The effective model id, or `""` when the CLI's own default is in force. |
| `verdict` | That panelist's own round-1 stance (see below). |
| `findings` | Indexes into the report's top-level `findings` array. |
| `round1_ok` | Did the adapter report success? Adapters fail soft, so a slot can carry a review *and* a nonzero exit. |
| `verified_count` | Consensus groups this reviewer contributed to that the verifier upheld. |
| `duration_s` | Wall-clock seconds for that slot's round-1 review. |

The chair's entry is the one carrying `role: "chair"`, is always **last**, and
carries only `name` (`"chair"`), `role`, `vendor`, `model` and `verdict` — the
run's final verdict, which is the panel vote under `--decision vote` and
otherwise the label the chair opened its synthesis with.

### How a ballot's verdict is derived

Exactly as the panel vote derives its ballots — `ai_jury.voting.tally_votes` is
*called*, not reimplemented, so the two renderings cannot drift apart. The stance
comes from the worst-severity finding that reviewer raised which the verifier did
not reject: critical/major → `REQUEST_CHANGES`, minor/nit → `COMMENT`, none →
`APPROVE`. Under `--issue` the vocabulary is the issue one: `NEEDS_INFO`,
`UNCLEAR`, `READY`.

Verdicts are emitted as a single machine token: the markdown report's
`REQUEST CHANGES` is `REQUEST_CHANGES` here, `NEEDS-INFO` is `NEEDS_INFO`.

There is a fourth value, `ABSTAIN`, for a slot that returned output but did not
review — an empty reply, a refusal, or an adapter that failed. **A non-answer is
not an approval** (issue #251): the vote tally drops such a reviewer from the
tally entirely, and a ballot list that has to name every slot names the
abstention rather than inventing a stance for it.

## The `keel-reviews` bundle

`jury --format keel-reviews` renders the same panel as a JSON **array** of review
records — the payload keel's `keel review --reviews <file>` accepts. One record
per panelist that returned output, plus the chair as `reviewer: "chair"`:

| Field | Derivation |
| --- | --- |
| `reviewer` | The panelist's name, or `"chair"`. |
| `verdict` | The ballot verdict above (a single token). |
| `scope` | One paragraph: the distinct files that panelist named in its structured findings (capped at 8, with a `(+N more)` tail), followed by up to three "checked / examined / inspected / reviewed" clauses lifted from its prose. |
| `findings` | That panelist's own findings as `{severity, path, line, message}` — ai-jury's `file` → `path`, `claim` → `message`. The chair carries the consensus-group representatives the verifier did **not** reject. |
| `testing` | The panelist's stated verification, lifted verbatim from its prose, or `"not stated"`. The chair's comes from the verification round. |
| `vendor` | The adapter's vendor, **as configured** — provenance, not the identity the cross-vendor gate collapses to. |
| `model` | The effective model id (`""` when the CLI default is in force). |

`scope` and `testing` are the only fields lifted from free text, and reviewer
output is attacker-influenced. Both are therefore flattened to a single line,
capped per clause and per count, and taken only from **prose**: a review's fenced
`json` block is skipped, because it is already parsed into `findings` and would
otherwise swamp the summary with its own serialized claim text. Marker matching
is word-anchored, which is load-bearing rather than tidy — the house phrasing
"un**checked** return value" is a finding, not a claim to have checked something.

Neither format ever carries diff text, prompt text or secrets.
