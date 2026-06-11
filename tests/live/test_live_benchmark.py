"""Opt-in live benchmark: run the real jury per fixture diff and score it.

The offline benchmark (``tests/test_benchmark.py``) scores *recorded* findings
and never touches a live CLI. This module exercises the optional live path,
which runs ``run_jury(..., mock=False)`` against the real agent CLIs and
scores the live findings against each fixture's expected spec.

It is intentionally:

* **Opt-in.** Skipped entirely unless ``JURY_BENCH_LIVE=1`` is set, so it
  never runs in the default suite or CI. This mirrors the ``JURY_LIVE=1``
  gate used by ``tests/live/test_live_smoke.py``.
* **Honest.** Live mode is the only mode that measures real review quality; the
  offline benchmark validates the scorer and recorded baselines.

Run with::

    JURY_BENCH_LIVE=1 PYTHONPATH=src python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from ai_jury import benchmark  # noqa: E402


@unittest.skipUnless(
    os.environ.get("JURY_BENCH_LIVE") == "1",
    "live benchmark disabled; set JURY_BENCH_LIVE=1",
)
class LiveBenchmarkTest(unittest.TestCase):
    """Run the real jury over the fixtures and score the live findings."""

    def test_live_benchmark_scores_every_fixture(self) -> None:
        scores, summary = benchmark.run_live()
        ids = {s.id for s in scores}
        self.assertTrue(ids, "no fixtures scored")
        self.assertEqual(summary["fixtures"], len(scores))
        # We deliberately do NOT assert a passing score: live agent quality
        # varies and this benchmark is directional, not a hard gate.
        print(benchmark.format_table(scores, summary))


if __name__ == "__main__":
    unittest.main()
