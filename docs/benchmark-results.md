<!-- Published results of the config-sweep lift benchmark. Regenerate with
     benchmark/sweep.py (per-model) and update the date/version + table below. -->

# Benchmark: does the panel actually help? — v1.1.0 · 2026-06-05

A **small, directional** benchmark that isolates *what the jury adds over one
model*. It runs the labeled fixtures in [`benchmark/`](https://github.com/berkayturanci/ai-jury/tree/main/benchmark)
— each a diff with a hand-authored answer key (`expected.json`: which bugs exist,
where, and which correct code must **not** be flagged) — and scores the
structured findings against that ground truth.

This is the honest counterpart to the [live four-vendor run](example-live-review.md):
that one is a real-world demo with no ground truth; **this one has answer keys**,
so it can report detection (recall) and false-positive rate (precision). To avoid
the result depending on *which* model we picked as the baseline, **every reviewer
is also run solo**.

## The run

- **Panel:** `claude` (Anthropic), `codex` (OpenAI), `agy` (Google), and a free
  local `qwen2.5-coder:7b` (Ollama). Each is also run **alone**.
- **Models:** the run did **not pin** model versions, so each cloud reviewer used
  its CLI's **default model on 2026-06-05** (whatever `claude` / `codex` / `agy`
  resolve to by default); the local seat is the pinned `qwen2.5-coder:7b`. To
  benchmark specific versions, pin `model = …` per agent in `jury.toml` and re-run.
- **Fixtures:** 5 labeled diffs (obvious logic bug · subtle boolean-guard ·
  missing error handling · a false-positive trap · a docs-only no-op).
- **When:** v1.1.0, 2026-06-05, run live on a 16 GB M1 Pro.

| Configuration | passed | recall (bugs found) | precision (no false alarms) |
|:--|:--:|:--:|:--:|
| single: `claude` | 4/5 | 0.67 | 1.00 |
| single: `codex` | 3/5 | 0.33 | 1.00 |
| single: `agy` | 3/5 | 0.33 | 1.00 |
| single: `qwen` (local) | 4/5 | 0.67 | 1.00 |
| **panel** — 4 vendors, 1 round | 4/5 | **1.00** | 0.75 |
| jury — 4 vendors, 2 rounds + verify | 4/5 | **1.00** | 0.60 |

## What it shows — and what it doesn't

- **Diversity lifts detection, robustly.** *Every* single model missed seeded
  bugs: the best (Claude, Qwen) caught **67%**; Codex and Agy **33%**. The
  four-vendor **panel caught 100%** — so the lift holds *whichever* single model
  you'd otherwise have picked, not just against a weak baseline. This is the
  research-backed lever — **heterogeneity** (see [feasibility](feasibility.md)),
  and it is the reproducible headline here.
- **Precision / the verification round: inconclusive at this scale.** More
  reviewers also surface more false positives (panel precision 0.75; this run's
  jury 0.60). A separate earlier run had the verification round clean the false
  positives back to 1.00 — so the precision effect sits **within the noise of an
  N=5, nondeterministic benchmark**. We are *not* claiming it; it needs a bigger
  fixture set before we'd cite a number.

So the honest, reproducible takeaway is the **recall lift from vendor diversity**:
no single model — local or frontier — caught everything, and the panel did.

## Honest caveats

- **N = 5**, one run per config, and LLM output is **nondeterministic** — so
  per-config numbers (precision especially) move run to run. This is a smoke
  signal and a regression guard, not a universal quality claim.
- It measures **direction**, not an absolute score: read the numbers as a
  comparison *between configs on this fixture set*, not a leaderboard. For where
  ai-jury sits among similar tools, see the [ecosystem comparison](comparison.md).

## Reproduce it

```bash
# Enable a local seat (uncomment the qwen agent in jury.toml) for the 4-vendor panel, then:
PYTHONPATH=src python3 benchmark/sweep.py --config jury.toml
# mechanics only, free:  PYTHONPATH=src python3 benchmark/sweep.py --mock
```

`sweep.py` runs each reviewer solo, then the panel, then the full jury, scoring
each against the fixtures' answer keys. See
[`benchmark/README.md`](https://github.com/berkayturanci/ai-jury/blob/main/benchmark/README.md)
for the fixture schema and the offline scorer.
