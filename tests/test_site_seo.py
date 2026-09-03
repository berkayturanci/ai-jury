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

import pathlib
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SITE = REPO_ROOT / "website"
BASE = "https://ai-jury.dev/"
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
                self.assertLessEqual(len(title.group(1).replace("&amp;", "&")), TITLE_LIMIT)

    def test_descriptions_are_present_and_not_truncated(self):
        for page in INDEXED_PAGES:
            with self.subTest(page=page):
                found = re.search(r'<meta name="description" content="([^"]+)"', _head(page))
                self.assertIsNotNone(found, f"{page} has no meta description")
                self.assertLessEqual(
                    len(found.group(1)),
                    DESCRIPTION_LIMIT,
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
            term for term in ("ai code review", "pull request", "claude code") if term not in text
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


class TestAdvertisedUrlsResolve(unittest.TestCase):
    """Every ai-jury.dev URL we hand people must actually be published.

    The README told users to run
    ``curl -fsSL https://ai-jury.dev/install.sh | sh`` while install.sh sat at
    the repo root and the Pages artifact was built from website/ — so the
    headline install command 404'd. A canonical/sitemap check cannot catch that
    class of break, because the URL is advertised in prose, not in the site.
    """

    #: Paths the Pages workflow generates into website/ at build time, each
    #: mapped to the workflow text that must still produce it. Committed files
    #: are checked on disk; these cannot be, so pin their build step instead.
    BUILD_TIME = {
        "install.sh": "cp install.sh website/install.sh",
        "coverage/": "coverage html -d website/coverage",
        "coverage-badge.json": 'open("website/coverage-badge.json", "w")',
    }

    def _advertised(self) -> set[str]:
        sources = [REPO_ROOT / "README.md", *sorted((REPO_ROOT / "docs").glob("*.md"))]
        urls: set[str] = set()
        for path in sources:
            urls.update(
                re.findall(
                    r"https://ai-jury\.dev/([A-Za-z0-9._/-]*)",
                    path.read_text(encoding="utf-8"),
                )
            )
        return urls

    def test_every_advertised_url_is_published(self):
        for suffix in sorted(self._advertised()):
            with self.subTest(url=BASE + suffix):
                if suffix == "":  # the homepage
                    self.assertTrue((SITE / "index.html").is_file())
                elif suffix in self.BUILD_TIME:
                    continue  # covered by the build-step test below
                else:
                    self.assertTrue(
                        (SITE / suffix).is_file(),
                        f"{BASE}{suffix} is advertised but website/{suffix} does "
                        "not exist and no build step creates it, so the link 404s",
                    )

    def test_build_time_paths_are_still_produced_by_the_workflow(self):
        workflow = (REPO_ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        for suffix, step in self.BUILD_TIME.items():
            with self.subTest(url=BASE + suffix):
                self.assertIn(
                    step,
                    workflow,
                    f"{BASE}{suffix} is advertised but pages.yml no longer "
                    f"produces it ({step!r} is gone), so the link would 404",
                )

    def test_the_readme_install_command_points_at_the_site(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"curl -fsSL {BASE}install.sh | sh", readme)

    def test_nothing_still_points_at_the_retired_pages_url(self):
        stale = "berkayturanci.github.io/ai-jury"
        for path in (
            REPO_ROOT / "README.md",
            REPO_ROOT / "install.sh",
            REPO_ROOT / "packaging/homebrew/ai-jury.rb.template",
            *sorted(SITE.glob("*.html")),
            SITE / "robots.txt",
            SITE / "sitemap.xml",
            SITE / "llms.txt",
        ):
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                self.assertNotIn(stale, path.read_text(encoding="utf-8"))


class TestIndexNow(unittest.TestCase):
    """The IndexNow key must agree in three places or submissions bounce.

    IndexNow proves host ownership by fetching the key back from ``keyLocation``
    and comparing it to the key in the payload. So the filename, the file's
    contents, and the key the workflow sends all have to match. Rotate one and
    forget another and every submission is rejected — silently, because the
    deploy step is ``continue-on-error`` by design (a search-engine ping must
    never fail a deploy that already succeeded).

    The key is public on purpose; it is served at the site root. It is not a
    secret and must not be moved into repository secrets.
    """

    WORKFLOW = REPO_ROOT / ".github/workflows/pages.yml"

    def _key_files(self) -> list[pathlib.Path]:
        return [f for f in sorted(SITE.glob("*.txt")) if f.name not in {"llms.txt", "robots.txt"}]

    def test_exactly_one_key_file_is_published(self):
        keys = [f.name for f in self._key_files()]
        self.assertEqual(
            len(keys),
            1,
            "IndexNow needs exactly one published key; a stale second file makes "
            f"host ownership ambiguous. Found: {keys}",
        )

    def test_the_key_file_contains_its_own_name(self):
        key_file = self._key_files()[0]
        self.assertEqual(
            key_file.read_text(encoding="utf-8").strip(),
            key_file.stem,
            "IndexNow fetches this file and compares it to the filename",
        )

    def test_the_key_is_well_formed(self):
        stem = self._key_files()[0].stem
        # IndexNow: 8-128 chars, [a-zA-Z0-9-] only.
        self.assertRegex(stem, r"^[A-Za-z0-9-]{8,128}$")

    def test_the_workflow_sends_the_published_key(self):
        workflow = self.WORKFLOW.read_text(encoding="utf-8")
        stem = self._key_files()[0].stem
        self.assertIn(
            f'KEY = "{stem}"',
            workflow,
            "pages.yml would submit a key that is not the one published at the "
            "site root, so every IndexNow submission would be rejected",
        )

    def test_the_ping_runs_after_the_deploy(self):
        """Announcing a URL before it is live invites a crawl of the old page."""
        workflow = self.WORKFLOW.read_text(encoding="utf-8")
        deploy = workflow.find("- name: Deploy to GitHub Pages")
        ping = workflow.find("- name: Notify IndexNow")
        self.assertNotEqual(deploy, -1, "the deploy step is gone")
        self.assertNotEqual(ping, -1, "the IndexNow step is gone")
        self.assertLess(deploy, ping, "IndexNow is pinged before the deploy publishes")

    def test_a_failed_ping_cannot_fail_the_deploy(self):
        workflow = self.WORKFLOW.read_text(encoding="utf-8")
        step = workflow[workflow.find("- name: Notify IndexNow") :]
        self.assertIn("continue-on-error: true", step.split("- name:")[1])


class TestAnalyticsCoverage(unittest.TestCase):
    """Every indexed page must carry the analytics beacon.

    The long-tail article existed to attract search traffic and was the one
    page without a beacon — so the single page whose performance mattered most
    was the single page not measured. The sibling repo had the identical gap on
    its own article. This pin keeps the next added page from repeating it.
    """

    def test_every_indexed_page_carries_the_beacon(self):
        for page in INDEXED_PAGES:
            with self.subTest(page=page):
                self.assertIn(
                    "beacon.min.js",
                    (SITE / page).read_text(encoding="utf-8"),
                    f"{page} is indexed but unmeasured — add the standard "
                    "Cloudflare Web Analytics tag",
                )


if __name__ == "__main__":
    unittest.main()
