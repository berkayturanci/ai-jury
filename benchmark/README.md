# Jury review-quality benchmark (issue #12)

A **small, directional** benchmark that measures whether a jury's structured
findings line up with hand-authored expectations for a set of fixture diffs.

> **This is not a universal quality claim.** It is a handful of fixtures and a
> deterministic scorer. The offline (default) mode validates the *scorer* and a
> set of *recorded baselines*; it does **not** measure live review quality.
> Only the optional live mode runs real agents. Treat the numbers as a smoke
> signal and a regression guard, not a benchmark of "how good the jury is".

## Why offline mode does not use `--mock`

With `mock=True` the `MockAdapter` emits a **fixed** canned finding regardless of
the diff. Running `--mock` per fixture and scoring it would be fake signal: the
output never reflects the fixture. So instead each fixture ships a **recorded**
sample jury output (`<id>.findings.json`) that the scorer scores against the
expected spec. This is deterministic, offline, and exercises the scorer +
fixtures without any live CLI.

## Fixtures

One fixture per category:

| id | category | expectation |
| --- | --- | --- |
| `obvious-logic-bug` | obvious logic bug | a `major` off-by-one finding |
| `subtle-boolean-guard` | subtle boolean-guard bug | a `major` guard finding |
| `missing-error-handling` | missing error handling | a finding on the unguarded I/O |
| `false-positive-trap` | suspicious-but-correct code | **no blocking finding** |
| `docs-only` | documentation-only change | **no code finding** |

Each fixture is three files:

- `<id>.diff` — the diff under review.
- `<id>.expected.json` — hand-authored expectations (schema below).
- `<id>.findings.json` — a recorded sample of jury output for that diff,
  a JSON array of finding dicts (`severity, file, line, claim, evidence, ...`).

### `expected.json` schema

```json
{
  "id": "obvious-logic-bug",
  "description": "off-by-one in loop bound",
  "expect": {
    "min_findings": 1,
    "must_match": [
      {"file": "src/foo.py", "line": 12, "severity": "major",
       "keywords": ["off-by-one", "index"]}
    ],
    "must_not_flag": []
  }
}
```

`expect` keys (all optional):

- `must_match` — each entry **should** be matched by at least one finding. An
  unmatched entry is a *missed* finding.
- `must_not_flag` — findings matching any of these entries are *false positives*
  (the code is correct here; flagging it is wrong).
- `min_findings` — the run must produce at least this many findings.
- `max_blocking` — at most this many *blocking* (critical/major) findings are
  allowed. The false-positive-trap and docs-only fixtures use `{"max_blocking": 0}`
  to encode "no blocking finding".

## Match rule

A finding matches a `must_match` / `must_not_flag` entry when **all** of these
hold (see `finding_matches_expected` in `src/ai_jury/benchmark.py`):

- **file**: same file path (exact match) when the entry specifies `file`.
- **line**: the finding's line is within ±3 (`LINE_TOLERANCE`) of the entry's
  `line`; an entry with no `line` is not positionally constrained.
- **severity**: the finding is at least as severe as the entry's `severity`
  (e.g. an expected `major` is satisfied by `major` or `critical`).
- **keywords**: at least one keyword appears (case-insensitive) in the finding's
  `claim` or `evidence`; an empty list imposes no keyword constraint.

A fixture **passes** when every `must_match` entry is matched, no `must_not_flag`
entry is matched, `min_findings` is met, and the blocking count does not exceed
`max_blocking`. Precision/recall are computed over the `must_match` entries and
are directional indicators only.

## Running it

Offline (default; deterministic, no live CLIs, no network):

```bash
make benchmark
# or
PYTHONPATH=src python3 -m ai_jury.benchmark
```

Live (opt-in; runs the real agent CLIs per fixture diff; never in CI):

```bash
JURY_BENCH_LIVE=1 PYTHONPATH=src python3 -m ai_jury.benchmark
```

The live path mirrors the `JURY_LIVE=1` gate used by the live smoke tests
(`tests/live/`). There is also an opt-in live benchmark test:

```bash
JURY_BENCH_LIVE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## What the tests cover

`tests/test_benchmark.py` (offline, in CI):

- exact matched/missed/false-positive counts and pass/fail (scorer math);
- every shipped fixture parses and has a valid schema;
- the false-positive-trap and docs-only fixtures encode "no blocking finding";
- `run_offline()` returns a result for every fixture and is deterministic.

It never requires live agents.

## Local / open-weight reviewer (issue #43)

The [local adapter](../README.md#local--open-weight-reviewer-free-offline) lets a
free, offline open-weight model sit on the panel. A directional spot-check (not a
rigorous benchmark — one diff, one model, one run) using `qwen2.5-coder:7b` via
Ollama on `examples/sample.diff`:

- **Caught the real bug.** It independently flagged the `payments.py` retry logic
  returning the last (possibly failed) response as a `major` finding with a
  correct suggested fix, and emitted well-formed structured JSON that flowed
  through consensus → synthesis like any CLI agent.
- **Cost / latency.** ~40s on a laptop, **$0**. A frontier CLI is faster and a
  stronger reviewer, but the local model adds a genuinely *different* perspective
  at zero marginal cost.

**Takeaway (honest):** a small local model is a weaker standalone reviewer; its
value is *diversity* — the documented load-bearing lever for a jury (Smit et
al. 2024; Cohere PoLL 2024). The recommended setup is one local panelist
alongside one or two cloud CLIs: more heterogeneity, lower spend. Quantifying the
exact lift across the full fixture set is future work — run the live benchmark
(`JURY_BENCH_LIVE=1`) with a local agent configured to measure it on your
hardware.
