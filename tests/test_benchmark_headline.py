"""The benchmark headline states what the published run measured, precision included.

The README and the site said the four-vendor "panel caught 100%" of the seeded bugs.
That was 3 of 3 bugs, on 5 fixtures, from one run on v1.1.0 with unpinned models, and
it left out the other half of the same table: the panel's precision fell to 0.75 and
the full jury's to 0.60, against 1.00 for every model run alone (#871).

The numbers here are read from the published table, `docs/benchmark-results.md`, and
from the answer keys in `benchmark/`, and every place quoting the headline must quote
them: the recall as bugs caught out of bugs seeded, both precisions, the version and
the date. Re-running the benchmark and updating the table fails this test until the
headlines are updated with it. Nothing here calls a model.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS = REPO_ROOT / "docs" / "benchmark-results.md"
FIXTURES = REPO_ROOT / "benchmark"
README = REPO_ROOT / "README.md"
SITE = REPO_ROOT / "website" / "index.html"

_ROW = re.compile(
    r"^\|\s*(?P<config>[^|]+?)\s*\|\s*[\d/]+\s*\|\s*(?P<recall>[\d.*]+)\s*\|\s*(?P<precision>[\d.*]+)\s*\|$"
)
_TITLE = re.compile(
    r"^# Benchmark:.*— v(?P<version>\d+\.\d+\.\d+) · (?P<date>\d{4}-\d{2}-\d{2})$", re.M
)


def _number(cell: str) -> float:
    return float(cell.strip("*"))


def published_run() -> dict:
    """The rows, version and date of the published sweep."""
    text = RESULTS.read_text(encoding="utf-8")
    rows = {}
    for line in text.splitlines():
        match = _ROW.match(line)
        if match:
            config = match["config"].replace("*", "").replace("`", "")
            rows[config] = (_number(match["recall"]), _number(match["precision"]))
    title = _TITLE.search(text)
    return {"rows": rows, "version": title["version"], "date": title["date"]}


def seeded_bugs() -> int:
    """Bugs seeded across the fixtures: every `must_match` entry is one."""
    return sum(
        len(json.loads(path.read_text(encoding="utf-8"))["expect"].get("must_match", []))
        for path in FIXTURES.glob("*.expected.json")
    )


def headline_facts() -> list[str]:
    """What a faithful headline has to say, in the words the headlines use."""
    run = published_run()
    rows = run["rows"]
    solo = {name: rec for name, rec in rows.items() if name.startswith("single:")}
    (panel,) = (rec for name, rec in rows.items() if name.startswith("panel"))
    (jury,) = (rec for name, rec in rows.items() if name.startswith("jury"))
    bugs = seeded_bugs()
    best_solo_recall = max(recall for recall, _ in solo.values())
    solo_precisions = {precision for _, precision in solo.values()}
    assert len(solo_precisions) == 1, "the headline says one precision for every solo model"
    return [
        f"v{run['version']}",
        run["date"],
        f"{bugs} seeded bugs",
        f"{round(panel[0] * bugs)}/{bugs}",
        f"{round(best_solo_recall * bugs)}/{bugs}",
        f"{panel[1]:.2f}",
        f"{jury[1]:.2f}",
        f"{solo_precisions.pop():.2f}",
    ]


def _readme_headline() -> str:
    text = README.read_text(encoding="utf-8")
    start = text.index("**The lift, measured.**")
    return text[start : text.index("\n\n", start)]


def _site_headline() -> str:
    text = SITE.read_text(encoding="utf-8")
    start = text.index("Measured on a small labeled benchmark")
    return re.sub(r"<[^>]+>", "", text[start : text.index("</p>", start)])


class TheBenchmarkHeadlineMatchesThePublishedRun(unittest.TestCase):
    def test_the_published_table_is_read(self):
        # Guards the parser: a table it could not read would make every check
        # below compare against nothing.
        run = published_run()
        self.assertEqual(len(run["rows"]), 6, run["rows"])
        self.assertEqual(seeded_bugs(), 3)

    def test_the_readme_states_recall_and_precision(self):
        headline = " ".join(_readme_headline().split())
        for fact in headline_facts():
            self.assertIn(fact, headline, f"README benchmark headline omits {fact!r}")
        self.assertNotIn("100%", headline)

    def test_the_site_states_recall_and_precision(self):
        headline = " ".join(_site_headline().split())
        for fact in headline_facts():
            self.assertIn(fact, headline, f"site benchmark headline omits {fact!r}")
        self.assertNotIn("100%", headline)


if __name__ == "__main__":
    unittest.main()
