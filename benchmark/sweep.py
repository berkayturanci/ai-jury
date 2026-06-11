"""Config-sweep benchmark — show the *lift* a panel + debate adds over one model.

Runs the labeled fixtures (see this directory) under three configurations and
scores each against its ground-truth `expected.json`, so the difference between
them is the evidence:

    single  — the chair model alone, 1 round, no verify   (baseline)
    panel   — all configured reviewers, 1 round, no verify (diversity only)
    jury    — all reviewers, 2 rounds + verification        (debate + verify)

Detection lift shows up as recall (panel finds what one model misses); the
verification round shows up as precision (it drops the panel's false positives).

Usage (from the repo root):

    # mechanics smoke-test — no live CLIs, numbers are meaningless:
    PYTHONPATH=src python3 benchmark/sweep.py --mock

    # real run — invokes the configured agent CLIs (costs time/$):
    PYTHONPATH=src python3 benchmark/sweep.py --config jury.toml

The published numbers in `docs/benchmark-results.md` were produced with a config
whose panel was claude + codex + agy + a local qwen seat. Enable a local seat
(uncomment the `qwen` agent in `jury.toml`) to reproduce the four-vendor result.
This is a small, directional benchmark, not a universal quality claim.
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
import time

from ai_jury import benchmark
from ai_jury.config import load_config
from ai_jury.orchestrator import run_jury


def build_configs(base):
    """Per-model singles + panel + jury, derived from a loaded JuryConfig.

    Each enabled reviewer is run *solo* (1 round, no verify) so the panel's lift
    is measured against **every** single model — not just one hand-picked baseline,
    which would make the result depend on which model you chose.
    """
    enabled = [a for a in base.agents if getattr(a, "enabled", True)]
    configs = [
        (f"single: {a.name}",
         dataclasses.replace(base, agents=[a], chair=a.name, rounds=1, verify=False, early_stop=False))
        for a in enabled
    ]
    configs.append(("panel (all reviewers, 1 round)",
                    dataclasses.replace(base, agents=enabled, rounds=1, verify=False, early_stop=False)))
    configs.append(("jury (all reviewers, 2r + verify)",
                    dataclasses.replace(base, agents=enabled, rounds=2, verify=True, early_stop=False)))
    return configs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="config-sweep lift benchmark")
    ap.add_argument("--config", default="jury.toml", help="path to a jury.toml (default: ./jury.toml)")
    ap.add_argument("--mock", action="store_true", help="mechanics smoke-test (no live CLIs)")
    args = ap.parse_args(argv)

    base = load_config(args.config)
    configs = build_configs(base)
    fixtures = benchmark.load_fixtures()
    print(f"mode={'mock' if args.mock else 'live'} · fixtures={len(fixtures)} · "
          f"configs={len(configs)} · chair={base.chair}\n")

    results = []
    for label, cfg in configs:
        t0 = time.time()
        scores = []
        for fx in fixtures:
            outcome = run_jury(cfg, fx.diff, mock=args.mock)
            findings = [benchmark._finding_to_dict(f) for f in outcome.findings]
            scores.append(benchmark.score_fixture(findings, fx.expected))
        agg = benchmark.aggregate(scores)
        results.append({"config": label, "agents": [a.name for a in cfg.agents],
                        "rounds": cfg.rounds, "verify": cfg.verify, "agg": agg,
                        "secs": round(time.time() - t0, 1)})
        print(f"=== {label} ({results[-1]['secs']:.0f}s) ===")
        print(benchmark.format_table(scores, agg), "\n")

    print("===== LIFT COMPARISON =====")
    print(f"{'config':<34} {'pass':<7} {'recall':<7} {'prec':<6} {'miss':<5} {'fp':<4}")
    for r in results:
        a = r["agg"]
        print(f"{r['config']:<34} {str(a['passed']) + '/' + str(a['fixtures']):<7} "
              f"{a['recall']:<7.2f} {a['precision']:<6.2f} {a['missed']:<5} {a['false_positives']:<4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
