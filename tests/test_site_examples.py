"""What the site and the example run show is what `jury` prints today (docs audit 2026-09-29).

The audit of ai-jury.dev found the pages describing an older tool: a "real report from
`jury --mock`" seating agy (the mock panel is claude + codex) under the footer #911
replaced (S2, S3, ER1); a FAQ whose search-engine copy and visible copy said different
things (S10); `--preset` offered on `jury` itself when only `jury init` takes it (S1);
plugin cards with an invented manifest and command (S6, S7); social images given as
relative URLs (S27); and the coverage pages' sidebars listing half of the documents
(S29). Each is checked here against its source: the command's own output, the other
copy, `docs/install.md`, and the docs page's registry.
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_sample_configs import _site_cards  # noqa: E402

ROOT = Path(__file__).parent.parent
SITE = ROOT / "website"
INDEX = SITE / "index.html"
EXAMPLE_RUN = ROOT / "docs" / "example-run.md"
#: The command docs/example-run.md is the output of.
MOCK_ARGS = ["--mock", "--diff-file", str(ROOT / "examples" / "sample.diff")]


def _text(markup: str) -> str:
    """Markup as a reader sees it: tags dropped, entities decoded, whitespace collapsed."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", markup))).strip()


def _md_text(markdown: str) -> str:
    """A markdown line as a reader sees it: links reduced to their text, code marks dropped."""
    return _text(
        re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", markdown).replace("`", "").replace("**", "")
    )


def _mock_report() -> str:
    """Standard output of ``jury --mock --diff-file examples/sample.diff``, run clean.

    From an empty directory under an empty HOME, so no jury.toml, trusted-config list
    or cache of the machine running the tests can change it.
    """
    with tempfile.TemporaryDirectory() as home:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("JURY_", "XDG_"))}
        env.update(
            HOME=home,
            USERPROFILE=home,
            XDG_CONFIG_HOME=str(Path(home) / "config"),
            XDG_CACHE_HOME=str(Path(home) / "cache"),
            PYTHONPATH=str(ROOT / "src"),
            PYTHONIOENCODING="utf-8",
        )
        done = subprocess.run(
            [sys.executable, "-m", "ai_jury", *MOCK_ARGS],
            cwd=home,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
            stdin=subprocess.DEVNULL,
        )
    assert done.returncode == 0, done.stderr
    return done.stdout


class TheExampleRunIsTheMockOutput(unittest.TestCase):
    """ER1: docs/example-run.md is `jury --mock` output, whole and unedited."""

    @classmethod
    def setUpClass(cls):
        cls.report = _mock_report()

    def test_the_page_is_the_command_output_after_its_header(self):
        text = EXAMPLE_RUN.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("<!--"), "the header comment says where it came from")
        header, body = text.split("-->\n\n", 1)
        self.assertIn("jury --mock --diff-file examples/sample.diff", header)
        self.assertEqual(
            body.replace("\r\n", "\n"),
            self.report.replace("\r\n", "\n"),
            "docs/example-run.md drifted from `jury --mock --diff-file examples/sample.diff`: "
            "regenerate its body from that command's output",
        )

    def test_the_site_sample_report_shows_the_mock_panel_and_footer(self):
        page = INDEX.read_text(encoding="utf-8")
        section = page[page.index('<section id="report">') : page.index('<section id="faq">')]
        lines = self.report.splitlines()
        panel = _md_text(next(ln for ln in lines if ln.startswith("**Panel:**")))
        footer = _md_text(next(ln for ln in reversed(lines) if ln.startswith("<sub>")))
        rmeta = _text(re.search(r'<p class="rmeta">(.*?)</p>', section).group(1))
        self.assertTrue(rmeta.startswith(panel), (rmeta, panel))
        foot = re.search(r'<p class="report-foot">(.*?)</p>', section).group(1)
        self.assertEqual(_text(foot), footer)
        self.assertNotIn("d-agy", section.split('<a class="liveref')[0])


def _faq_pairs(markup: str) -> list[tuple[str, str]]:
    faq = markup[markup.index('<section id="faq">') :]
    faq = faq[: faq.index("</section>")]
    return [
        (_text(q), _text(a))
        for q, a in re.findall(
            r'<summary>(.*?)<span class="chev"></span></summary>\s*<div class="qa-body">(.*?)</div>',
            faq,
            flags=re.S,
        )
    ]


def _faq_ld(markup: str) -> list[tuple[str, str]]:
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', markup, re.S):
        data = json.loads(block)
        if data.get("@type") == "FAQPage":
            return [(q["name"], q["acceptedAnswer"]["text"]) for q in data["mainEntity"]]
    raise AssertionError("no FAQPage structured data")


class TheSiteSaysWhatTheToolDoes(unittest.TestCase):
    def setUp(self):
        self.page = INDEX.read_text(encoding="utf-8")
        self.app = (SITE / "app.js").read_text(encoding="utf-8")

    def test_the_structured_faq_is_the_visible_faq(self):
        # S10: search engines were shown answers the page did not show.
        visible = _faq_pairs(self.page)
        self.assertEqual(len(visible), 7)
        self.assertEqual(_faq_ld(self.page), visible)

    def test_preset_is_only_offered_on_jury_init(self):
        # S1: `jury --preset offline --pr 123` is "unrecognized arguments".
        for rel, text in (("website/index.html", _text(self.page)), ("website/app.js", self.app)):
            for m in re.finditer(r"--preset", text):
                with self.subTest(file=rel, at=m.start()):
                    self.assertTrue(
                        text[: m.start()].rstrip().endswith("jury init"),
                        text[m.start() - 60 : m.end() + 20],
                    )

    def test_the_pr_vocabulary_is_the_clis(self):
        # S16: the verdict is "REQUEST CHANGES"; nothing prints a hyphenated one.
        self.assertNotIn("REQUEST-CHANGES", self.page)

    def test_a_hosted_api_key_is_never_said_to_seat_a_reviewer_alone(self):
        # S11: a key with no [[agent]] seat forms no panel.
        text = _text(self.page)
        self.assertNotIn("no CLI at all", text)
        self.assertEqual(text.count("seats nothing"), 3)  # both FAQ copies + quickstart

    def test_the_plugin_cards_run_the_install_commands_the_install_guide_measured(self):
        # S6/S7: the cards showed an invented manifest and `codex plugins run ai-jury`.
        guide = (ROOT / "docs" / "install.md").read_text(encoding="utf-8")
        cards = _site_cards(self.app)
        for card_id, heading in (("claude-plugin", "## Claude Code"), ("codex-plugin", "## Codex")):
            section = guide[guide.index(heading + "\n") :]
            section = section[: section.index("\n---\n")]
            documented = [
                ln
                for block in re.findall(r"```bash\n(.*?)```", section, re.S)
                for ln in block.splitlines()
                if ln.strip()
            ]
            shown = [
                ln for ln in cards[card_id]["config"].splitlines() if ln and not ln.startswith("#")
            ]
            with self.subTest(card=card_id):
                self.assertTrue(documented)
                self.assertEqual(shown, documented)
                self.assertNotIn("plugin.json", cards[card_id]["config"])

    def test_the_claude_and_agy_cards_describe_the_seat_jury_runs(self):
        # S8: the claude juror runs with no tools; S9: agy is opt-in and cannot be confined.
        cards = _site_cards(self.app)
        self.assertIn('--tools ""', cards["claude-code"]["desc"])
        self.assertNotIn("bash", cards["claude-code"]["desc"].lower())
        for where, text in (
            ("agy card", cards["antigravity"]["desc"]),
            ("agy checkbox", re.search(r'id="ag-agy".*?</label>', self.page).group(0)),
        ):
            with self.subTest(where=where):
                self.assertIn("opt-in", text.lower())
                self.assertIn("cannot be confined", text)


class ThePagesPointAtWhatExists(unittest.TestCase):
    PAGES = ("index.html", "docs.html", "coverage.html", "coverage-report.html")

    def test_social_images_are_absolute(self):
        # S27: a relative og:image is resolved by nothing that unfurls a link.
        for page in self.PAGES:
            text = (SITE / page).read_text(encoding="utf-8")
            images = re.findall(
                r'<meta (?:property="og:image"|name="twitter:image") content="([^"]+)"', text
            )
            with self.subTest(page=page):
                self.assertEqual(len(images), 2)
                for url in images:
                    self.assertTrue(url.startswith("https://ai-jury.dev/"), url)

    def test_the_coverage_sidebars_list_every_registered_document(self):
        # S29: the sidebars listed 16 of the documents docs.html registers.
        docs = (SITE / "docs.html").read_text(encoding="utf-8")
        registry = docs[docs.index("var GROUPS = [") :]
        registry = registry[: registry.index("\n    ];")]
        slugs = re.findall(r'\{ slug: "([^"]+)"', registry)
        self.assertGreaterEqual(len(slugs), 30)
        for page in ("coverage.html", "coverage-report.html"):
            text = (SITE / page).read_text(encoding="utf-8")
            side = text[text.index('<aside class="docs-side') : text.index("</aside>")]
            with self.subTest(page=page):
                self.assertEqual(re.findall(r'href="docs\.html#([^"]+)"', side), slugs)


if __name__ == "__main__":
    unittest.main()
