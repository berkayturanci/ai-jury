<!-- Published results of the config-sweep lift benchmark. Regenerate with
     benchmark/sweep.py and update the date/version below. -->

# Benchmark: does the panel actually help? — v1.1.0 · 2026-06-05

A **small, directional** benchmark that isolates *what the jury adds over one
model*. It runs the labeled fixtures in [`benchmark/`](https://github.com/berkayturanci/ai-jury/tree/main/benchmark)
— each a diff with a hand-authored answer key (`expected.json`: which bugs exist,
where, and which correct code must **not** be flagged) — under three configs and
scores the structured findings against that ground truth.

This is the honest counterpart to the [live four-vendor run](example-live-review.md):
that one is a real-world demo with no ground truth; **this one has answer keys**,
so it can report detection (recall) and false-positive rate (precision).

## The run

- **Panel:** `claude` (Anthropic), `codex` (OpenAI), `agy` (Google), and a free
  local `qwen2.5-coder:7b` (Ollama) — four vendors. `single` is the chair
  (`claude`) alone.
- **Fixtures:** 5 labeled diffs (obvious logic bug · subtle boolean-guard ·
  missing error handling · a false-positive trap · a docs-only no-op).
- **When:** v1.1.0, 2026-06-05, run live on a 16 GB M1 Pro.

| Configuration | passed | recall (bugs found) | precision (no false alarms) | missed | false positives |
|:--|:--:|:--:|:--:|:--:|:--:|
| **single** — chair only, 1 round | 4/5 | 0.67 | 1.00 | 1 | 0 |
| **panel** — 4 vendors, 1 round | 4/5 | **1.00** | 0.60 | 0 | 2 |
| **jury** — 4 vendors, 2 rounds + verify | **5/5** | **1.00** | **1.00** | 0 | 0 |

## What it shows

Two independent levers, each visible in a different column:

1. **Diversity raises detection (recall 0.67 → 1.00).** One strong model alone
   missed a seeded bug; adding three other-vendor reviewers caught **every**
   seeded bug. This is the research-backed lever — *heterogeneity*, not more
   rounds (see [feasibility](feasibility.md)).
2. **Verification raises precision (0.60 → 1.00).** More reviewers also produced
   **2 false positives**; the debate + chair-verification round dropped both
   without losing any real finding. The full **jury was the only config to pass
   all five fixtures**.

So the panel and the verification round are not redundant — they fix *different*
failure modes (misses vs. noise), and you need both to land at 5/5.

## Honest caveats

- **N = 5.** This is a smoke signal and a regression guard, not a universal
  quality claim. Magpie's [published benchmark](https://milvus.io/blog/ai-code-review-gets-better-when-models-debate-claude-vs-gemini-vs-codex-vs-qwen-vs-minimax.md)
  (15 real bug-shipping PRs, ~80% detection after debate) is larger; our result
  **reproduces the same direction** on a smaller, reproducible set — it is not a
  head-to-head ranking.
- **One run**, on one machine; `single` is one model (the chair), not an average
  over each vendor solo.

## Reproduce it

```bash
# Enable a local seat (uncomment the qwen agent in jury.toml) for the 4-vendor panel, then:
PYTHONPATH=src python3 benchmark/sweep.py --config jury.toml
# mechanics only, free:  PYTHONPATH=src python3 benchmark/sweep.py --mock
```

See [`benchmark/README.md`](https://github.com/berkayturanci/ai-jury/blob/main/benchmark/README.md)
for the fixture schema and the offline scorer.
