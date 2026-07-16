"""Website asset pins for the "Load a real run" affordance (issue #450).

The site is static and has no JS test runner, so these are cheap offline
pins: ``node --check`` syntax-validates ``website/app.js`` when node is
installed (skipped otherwise), and greppy assertions pin the new control
IDs / entry points so a refactor that drops them fails loudly. Network-free.
"""

from __future__ import annotations

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
