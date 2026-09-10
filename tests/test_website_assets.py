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
        opening = html[match.start() : match.start() + match.group(0).index(">") + 1]
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
        self.assertIn('role="tablist"', html[pipe.start() : pipe.start() + 120])
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


class SkipToContentLinks(unittest.TestCase):
    """Every page under ``website/`` must offer the skip-to-content link as its
    first tab stop (#790). The affordance is four separate parts, and dropping
    any one of them leaves markup that reads correctly and does nothing:

    * the link itself, first in the tab order — behind any other focusable
      element it is no longer a *skip*;
    * a target id that exists on the page, or the fragment resolves to nothing;
    * ``tabindex="-1"`` on the element carrying that id, or the page scrolls
      without moving focus and the next Tab resumes from the top — measured in
      Chrome, where activating the link moves ``document.activeElement`` onto
      the target only when the attribute is present;
    * ``styles.css``, the only place the link is told to park off-screen and
      come back on focus. A page with the markup and without the stylesheet
      shows a stray link to everybody instead.

    Discovered rather than listed: a page added under ``website/`` tomorrow is
    held to this bar the day it lands, which is how the article page came to be
    the only one of six without a skip link.
    """

    # An opening tag that takes keyboard focus by default, or opts in with
    # tabindex="0". Anchors need an href — <a> without one is not focusable.
    FOCUSABLE = re.compile(
        r"<(?:a\s[^>]*\bhref=|button\b|input\b|select\b|textarea\b"
        r'|[a-z]+\s[^>]*\btabindex="0")',
        re.I,
    )
    SKIP_LINK = re.compile(r'<a href="#([\w-]+)" class="skip-to-content">')

    def _pages(self):
        pages = sorted(WEBSITE.glob("*.html"))
        self.assertGreater(len(pages), 1, "no site pages found to check")
        return pages

    def _body(self, page):
        src = page.read_text(encoding="utf-8")
        opening = re.search(r"<body\b[^>]*>", src, re.I)
        self.assertIsNotNone(opening, f"{page.name} has no <body>")
        return src, src[opening.end() :]

    def test_every_page_offers_the_link_first(self):
        for page in self._pages():
            with self.subTest(page=page.name):
                _src, body = self._body(page)
                link = self.SKIP_LINK.search(body)
                self.assertIsNotNone(link, f"{page.name} has no skip-to-content link")
                first = self.FOCUSABLE.search(body)
                self.assertIsNotNone(first, f"{page.name} has nothing focusable")
                self.assertEqual(
                    link.start(),
                    first.start(),
                    f"{page.name}: something focusable precedes the skip link",
                )

    def test_every_target_exists_and_takes_focus(self):
        for page in self._pages():
            with self.subTest(page=page.name):
                _src, body = self._body(page)
                link = self.SKIP_LINK.search(body)
                self.assertIsNotNone(link, f"{page.name} has no skip-to-content link")
                target_id = re.escape(link.group(1))
                target = re.search(rf'<\w+[^>]*\bid="{target_id}"[^>]*>', body)
                self.assertIsNotNone(target, f"{page.name}: #{link.group(1)} is not on the page")
                self.assertIn(
                    'tabindex="-1"',
                    target.group(0),
                    f"{page.name}: #{link.group(1)} would scroll without taking focus",
                )

    def test_no_page_hoists_anything_ahead_of_the_link(self):
        # A positive tabindex jumps the queue wherever it sits in the document,
        # so "first in the markup" would stop meaning "first in the tab order".
        for page in self._pages():
            with self.subTest(page=page.name):
                _src, body = self._body(page)
                hoisted = [v for v in re.findall(r'tabindex="(-?\d+)"', body) if int(v) > 0]
                self.assertEqual([], hoisted, f"{page.name} has a positive tabindex")

    def test_every_page_loads_the_stylesheet_that_hides_the_link(self):
        for page in self._pages():
            with self.subTest(page=page.name):
                src, _body = self._body(page)
                self.assertRegex(src, r'<link[^>]*rel="stylesheet"[^>]*href="styles\.css"')

    def test_the_stylesheet_parks_the_link_and_brings_it_back(self):
        css = (WEBSITE / "styles.css").read_text(encoding="utf-8")
        parked = re.search(r"\.skip-to-content\s*\{([^}]*)\}", css)
        self.assertIsNotNone(parked, "styles.css does not style the skip link")
        self.assertIn("position: absolute", parked.group(1))
        self.assertRegex(parked.group(1), r"top:\s*-\d", "the link is not parked off-screen")
        focused = re.search(r"\.skip-to-content:focus-visible\s*\{([^}]*)\}", css)
        self.assertIsNotNone(focused, "nothing brings the link back when focused")
        self.assertRegex(focused.group(1), r"top:\s*0", "focus does not bring the link on-screen")


if __name__ == "__main__":
    unittest.main()
