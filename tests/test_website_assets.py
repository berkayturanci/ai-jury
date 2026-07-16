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
        proc = subprocess.run(
            ["node", "--check", str(WEBSITE / "app.js")],
            capture_output=True,
            text=True,
            timeout=60,
        )
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


if __name__ == "__main__":
    unittest.main()
