"""What the docs and the site say about where a diff goes (#860, #873).

Two claims drifted from the code. The site said "your code doesn't leave to a
third-party service" and ticked "no code leaves to a SaaS", while every
non-local seat sends the diff to its model vendor. README and SECURITY.md said
the only network activity was the agent CLIs and `gh`, while the jury itself
calls hosted-API and local model endpoints, and probes the default local server.
These checks keep the wrong phrasing out and the accurate sentences in. Stdlib
and offline: they read files.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ai_jury import adapters  # noqa: E402

#: Every user-facing surface that makes a privacy or network claim. The
#: CHANGELOG is history and may quote the old wording; it is not listed.
SURFACES = [
    "README.md",
    "SECURITY.md",
    "llms.txt",
    "llms-full.txt",
    "website/index.html",
    "website/app.js",
    *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "docs").glob("*.md")),
]

#: "doesn't leave", "no code leaves" and the like: false unless every seat is
#: local, which is not what those sentences were describing.
NOTHING_LEAVES = re.compile(
    r"doesn['’]t leave|does not leave|no (?:code|data|diff) leaves", re.IGNORECASE
)

NO_SERVER = (
    "no ai-jury server; your diff goes only to the model vendors you configure "
    "(or nowhere if every seat is local)"
)


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _flat(rel: str) -> str:
    """The file with runs of whitespace folded, so wrapped prose matches."""
    return re.sub(r"\s+", " ", _text(rel))


class NothingSaysTheDiffNeverLeaves(unittest.TestCase):
    def test_no_surface_says_code_or_data_does_not_leave(self):
        for rel in SURFACES:
            with self.subTest(rel):
                self.assertIsNone(NOTHING_LEAVES.search(_text(rel)), rel)

    def test_the_accurate_sentence_is_where_the_claim_was(self):
        for rel in ("README.md", "SECURITY.md", "llms.txt", "website/index.html"):
            with self.subTest(rel):
                self.assertIn(NO_SERVER, _flat(rel).lower().replace("there is ", ""))

    def test_both_site_faq_copies_carry_it(self):
        # The FAQ is rendered twice: the visible <details> and the JSON-LD
        # FAQPage search engines read. Both said the code does not leave.
        self.assertEqual(_flat("website/index.html").count("no ai-jury server"), 2)


class TheNetworkListNamesEverythingTheToolCalls(unittest.TestCase):
    NAMES = (
        "agent CLIs",
        "hosted-API endpoints",
        "local model endpoints",
        "`gh`",
    )

    def test_readme_and_security_list_every_destination(self):
        for rel in ("README.md", "SECURITY.md"):
            text = _flat(rel)
            with self.subTest(rel):
                self.assertNotIn("The only network activity", text)
                self.assertIn("Network traffic goes to:", text)
                start = text.index("Network traffic goes to:")
                listing = text[start : start + 900]
                for name in self.NAMES:
                    self.assertIn(name, listing)
                # The loopback probe the zero-config path makes, by the address
                # the code actually uses.
                self.assertIn(adapters._DEFAULT_LOCAL_ENDPOINT, listing)
                self.assertIn("no telemetry", text.lower())

    def test_positioning_no_longer_says_network_only_when_you_ask(self):
        text = _flat("docs/positioning.md")
        self.assertNotIn("Network only when you ask", text)
        self.assertNotIn("only talks to the network when", text)
        for name in ("hosted-API and local model endpoints", "`gh`"):
            self.assertIn(name, text)


if __name__ == "__main__":
    unittest.main()
