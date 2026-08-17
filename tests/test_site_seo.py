"""The site's search-facing fields must stay present, sized, and reachable.

ai-jury's copy was already in search language — "AI code review" appears on the
homepage, unlike the sibling repo's, which had invented its own vocabulary. What
was wrong here was length: the meta description was 301 characters and the social
ones 188 and 196, so Google and every link preview cut them mid-sentence. The
part that gets cut is the part that would have earned the click.

And a page nothing links to and no sitemap lists is a page nothing crawls — the
article added alongside this is exactly that risk.

Offline and cheap: these are facts about the files, not about Google.
"""

from __future__ import annotations

import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SITE = REPO_ROOT / "website"
BASE = "https://berkayturanci.github.io/ai-jury/"
SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

#: Deliberately a list rather than a glob: 404.html must *not* be indexed, and a
#: glob would quietly accept it.
INDEXED_PAGES = (
    "index.html",
    "docs.html",
    "coverage.html",
    "coverage-report.html",
    "agent-pr-said-one-line.html",
)

#: Roughly what a search result and a link preview show.
TITLE_LIMIT = 70
DESCRIPTION_LIMIT = 165


def _head(name: str) -> str:
    text = (SITE / name).read_text(encoding="utf-8")
    return text[: text.find("</head>")]


class TestSearchFacingFields(unittest.TestCase):
    def test_every_indexed_page_exists(self):
        # Guards the loops below from iterating over nothing after a rename.
        for page in INDEXED_PAGES:
            with self.subTest(page=page):
                self.assertTrue((SITE / page).is_file(), f"{page} is listed but missing")

    def test_titles_fit_a_search_result(self):
        for page in INDEXED_PAGES:
            with self.subTest(page=page):
                title = re.search(r"<title>([^<]+)</title>", _head(page))
                self.assertIsNotNone(title, f"{page} has no <title>")
                self.assertLessEqual(
                    len(title.group(1).replace("&amp;", "&")), TITLE_LIMIT
                )

    def test_descriptions_are_present_and_not_truncated(self):
        for page in INDEXED_PAGES:
            with self.subTest(page=page):
                found = re.search(
                    r'<meta name="description" content="([^"]+)"', _head(page)
                )
                self.assertIsNotNone(found, f"{page} has no meta description")
                self.assertLessEqual(
                    len(found.group(1)), DESCRIPTION_LIMIT,
                    f"{page} description will be cut mid-sentence",
                )

    def test_social_descriptions_are_not_truncated(self):
        # A link preview truncates harder than a search result, and this is the
        # text someone sees when the project is shared.
        head = _head("index.html")
        for prop in ('property="og:description"', 'name="twitter:description"'):
            with self.subTest(prop=prop):
                found = re.search(rf'{re.escape(prop)}\s+content="([^"]+)"', head)
                self.assertIsNotNone(found, f"index.html has no {prop}")
                self.assertLessEqual(len(found.group(1)), DESCRIPTION_LIMIT)

    def test_every_indexed_page_declares_a_canonical_url(self):
        for page in INDEXED_PAGES:
            with self.subTest(page=page):
                self.assertRegex(_head(page), rf'rel="canonical"\s+href="{re.escape(BASE)}')

    def test_the_homepage_speaks_in_terms_people_search_for(self):
        text = (SITE / "index.html").read_text(encoding="utf-8").lower()
        missing = [
            term
            for term in ("ai code review", "pull request", "claude code")
            if term not in text
        ]
        self.assertEqual([], missing, "the homepage never uses these search terms")


class TestSitemap(unittest.TestCase):
    def _locs(self) -> set[str]:
        root = ET.parse(SITE / "sitemap.xml").getroot()
        return {e.text.strip() for e in root.iter(f"{SITEMAP_NS}loc") if e.text}

    def test_the_sitemap_lists_every_indexed_page(self):
        locs = self._locs()
        self.assertTrue(locs, "the sitemap is empty")
        for page in INDEXED_PAGES:
            expected = BASE + ("" if page == "index.html" else page)
            with self.subTest(page=page):
                self.assertIn(expected, locs, f"{page} is not in the sitemap")

    def test_the_sitemap_does_not_list_the_error_page(self):
        self.assertNotIn(BASE + "404.html", self._locs())

    def test_robots_points_at_the_sitemap(self):
        robots = (SITE / "robots.txt").read_text(encoding="utf-8")
        self.assertIn(f"Sitemap: {BASE}sitemap.xml", robots)


class TestArticleIsReachable(unittest.TestCase):
    """A page nothing links to is a page nothing crawls."""

    def test_the_article_is_linked_from_the_homepage(self):
        self.assertIn(
            'href="agent-pr-said-one-line.html"',
            (SITE / "index.html").read_text(encoding="utf-8"),
        )

    def test_the_article_carries_article_structured_data(self):
        head = _head("agent-pr-said-one-line.html")
        self.assertIn("application/ld+json", head)
        self.assertIn('"@type": "TechArticle"', head)
        self.assertIn('property="og:type" content="article"', head)


if __name__ == "__main__":
    unittest.main()
