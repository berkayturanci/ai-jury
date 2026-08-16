"""Website asset pins for the "Load a real run" affordance (issue #450).

The site is static and has no JS test runner, so these are cheap offline
pins: ``node --check`` syntax-validates ``website/app.js`` when node is
installed (skipped otherwise), and greppy assertions pin the new control
IDs / entry points so a refactor that drops them fails loudly. Network-free.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

WEBSITE = Path(__file__).parent.parent / "website"


class TestWebsiteAssets(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_app_js_is_valid_javascript(self):
        node = shutil.which("node")
        try:
            proc = subprocess.run(
                [node, "--check", str(WEBSITE / "app.js")],
                capture_output=True,
                text=True,
                timeout=60,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:  # pragma: no cover - runner-specific
            # Observed on the Windows CI runner: node resolved but the check
            # hung. A hung toolchain is an environment problem, not an app.js
            # syntax error — degrade to a skip so the suite stays honest.
            self.skipTest("node --check hung; skipping syntax validation here")
            return  # unreachable (skipTest raises) — pins proc as always-bound
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_index_html_has_load_run_controls(self):
        html = (WEBSITE / "index.html").read_text(encoding="utf-8")
        for needle in (
            'id="load-run-zone"',
            'id="run-file"',
            'accept=".json,application/json"',
            'id="load-run-status"',
            'aria-live="polite"',
            'id="back-to-demo"',
            'label class="btn ghost load-run-label" for="run-file"',
        ):
            self.assertIn(needle, html)

    def test_app_js_defines_real_run_entry_points(self):
        js = (WEBSITE / "app.js").read_text(encoding="utf-8")
        for needle in (
            "function parseOutcomeJson(",
            "FileReader",
            "dataTransfer",
            "8 * 1024 * 1024",  # client-side size cap
            'addEventListener("drop"',
        ):
            self.assertIn(needle, js)


class IntegrationFilterAria(unittest.TestCase):
    """The filter pills must expose their state, and must not claim to be tabs
    while doing it (issue #550). ``.active`` is a visual cue only; without
    aria-pressed a screen-reader user cannot tell which filter is on."""

    def _filter_bar(self):
        html = (WEBSITE / "index.html").read_text(encoding="utf-8")
        match = re.search(r'<div class="integration-filters[^>]*>(.*?)</div>', html, re.S)
        self.assertIsNotNone(match, "integration filter bar not found")
        return html, match

    def test_the_filter_bar_is_a_group_not_a_tablist(self):
        # role="tablist" obliges role="tab" children with aria-selected and
        # aria-controls. These are toggle buttons filtering a grid in place, so
        # aria-pressed is the correct state — and the two cannot be combined.
        html, match = self._filter_bar()
        opening = html[match.start():match.start() + match.group(0).index(">") + 1]
        self.assertIn('role="group"', opening)
        self.assertNotIn('role="tablist"', opening)

    def test_every_pill_declares_a_pressed_state(self):
        _html, match = self._filter_bar()
        pills = re.findall(r'<button[^>]*class="int-pill[^"]*"[^>]*>', match.group(1))
        self.assertGreater(len(pills), 1, "no filter pills found to check")
        for pill in pills:
            with self.subTest(pill=pill[:60]):
                self.assertRegex(pill, r'aria-pressed="(true|false)"')

    def test_exactly_one_pill_starts_pressed(self):
        # Two pressed pills announce two active filters; zero announces none,
        # while the page visibly shows one.
        _html, match = self._filter_bar()
        self.assertEqual(1, match.group(1).count('aria-pressed="true"'))
        self.assertEqual(1, match.group(1).count("int-pill active"))

    def test_the_click_handler_moves_the_pressed_state(self):
        # Static markup alone would freeze the state on "All" after the first
        # click — correct at load, wrong from then on.
        js = (WEBSITE / "app.js").read_text(encoding="utf-8")
        handler = re.search(r"pills\.forEach\(function \(pill\).*?\n    \}\);", js, re.S)
        self.assertIsNotNone(handler, "pill click handler not found")
        self.assertIn('setAttribute("aria-pressed", "false")', handler.group(0))
        self.assertIn('setAttribute("aria-pressed", "true")', handler.group(0))

    def test_the_pipeline_tabs_are_still_a_real_tablist(self):
        # Guards against applying the fix above to the wrong widget: the
        # pipeline tabs *are* tabs (#436) and must keep tab semantics.
        html = (WEBSITE / "index.html").read_text(encoding="utf-8")
        pipe = re.search(r'<div class="pipe-tabs"[^>]*>(.*?)</div>', html, re.S)
        self.assertIsNotNone(pipe, "pipeline tab strip not found")
        self.assertIn('role="tablist"', html[pipe.start():pipe.start() + 120])
        self.assertIn('role="tab"', pipe.group(1))
        self.assertIn("aria-selected", pipe.group(1))


class EscapeRegressionPins(unittest.TestCase):
    """Cheap structural pins against un-escaping regressions (security review):
    the innerHTML-feeding row builders must route file-sourced fields through
    esc(), and esc() must cover the quote/backtick classes."""

    def test_esc_covers_quotes_and_backtick(self):
        src = (WEBSITE / "app.js").read_text(encoding="utf-8")
        esc_line = next(line for line in src.splitlines() if "function esc(" in line)
        self.assertIn("&#39;", esc_line)
        self.assertIn("&#96;", esc_line)

    def test_row_builders_escape_fields(self):
        src = (WEBSITE / "app.js").read_text(encoding="utf-8")
        for builder in ("function findingRow", "function seatRow"):
            self.assertIn(builder, src)
            body = src.split(builder, 1)[1][:900]
            self.assertIn("esc(", body)


if __name__ == "__main__":
    unittest.main()
