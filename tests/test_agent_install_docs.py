"""The README's install boxes and `docs/install.md` name the same agents.

`docs/install.md` exists because the README churns and a release note cannot link
into it stably. Two documents covering one subject is how #781's `@v1` came about:
three pages agreed with each other and none of them agreed with the repository. So
the pair is checked against each other *and* against the surfaces they describe —
an agent added to one page and not the other is the drift this file refuses, and a
box with no update instructions is the half of #783 that is not guessable by
analogy.

Read as text, with no YAML or Markdown parser, for the reason given in
`tests/test_github_action.py`: ai-jury declares ``dependencies = []`` and three dev
tools, and a test is not a good enough reason to make one of them the exception.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
INSTALL_DOC = REPO_ROOT / "docs" / "install.md"
PLATFORMS = REPO_ROOT / "docs" / "platforms.md"

#: The anchor each agent's box and section carries. The README puts it in the
#: `<summary>` so the badge above links into the collapsed box; `install.md` puts
#: it under the heading. One spelling, so a badge cannot point at nothing.
ANCHOR = re.compile(r'<a id="([a-z0-9-]+)"></a>')

#: `[![Name](badge-url)](#anchor)` — the badge row that doubles as the index.
BADGE = re.compile(r"\[!\[[^\]]+\]\([^)]+\)\]\(#([a-z0-9-]+)\)")

#: The agents both documents must cover. Named here on purpose: this is the one
#: fact the two pages cannot derive from each other, and adding a fifth agent
#: should be a deliberate edit to this line rather than something a page silently
#: drops.
AGENTS = ("claude-code", "codex", "antigravity", "cursor")


def anchors(text: str) -> list[str]:
    return ANCHOR.findall(text)


class TheDocumentsWereRead(unittest.TestCase):
    """Vacuity: an empty read satisfies every set comparison below."""

    @classmethod
    def setUpClass(cls):
        cls.readme = README.read_text(encoding="utf-8")
        cls.install = INSTALL_DOC.read_text(encoding="utf-8")

    def test_both_files_exist_and_are_not_empty(self):
        self.assertGreater(len(self.readme.splitlines()), 100)
        self.assertGreater(len(self.install.splitlines()), 50)

    def test_the_anchor_pattern_matches_what_the_files_write(self):
        self.assertGreaterEqual(len(anchors(self.readme)), len(AGENTS))
        self.assertGreaterEqual(len(anchors(self.install)), len(AGENTS))


class EveryAgentIsInBothDocuments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.readme = README.read_text(encoding="utf-8")
        cls.install = INSTALL_DOC.read_text(encoding="utf-8")

    def test_the_readme_boxes_cover_every_agent(self):
        self.assertLessEqual(set(AGENTS), set(anchors(self.readme)))

    def test_the_install_page_covers_every_agent(self):
        self.assertLessEqual(set(AGENTS), set(anchors(self.install)))

    def test_the_two_documents_cover_the_same_agents(self):
        """The drift this file exists for: one page gains an agent, the other does not.

        The **whole** anchor set on each side, not the intersection with `AGENTS`.
        Intersecting was a tautology: the two tests above already assert
        `AGENTS <= anchors`, so `anchors & AGENTS` is `AGENTS` on both sides and
        the comparison reduced to `AGENTS == AGENTS`. A fifth agent added to one
        page and not the other — exactly the drift named in the docstring — passed.
        """
        self.assertEqual(set(anchors(self.readme)), set(anchors(self.install)))

    def test_a_fifth_agent_on_one_page_only_is_caught(self):
        """The mutation the intersecting version survived."""
        readme = self.readme + '\n<a id="zed"></a>\n'
        self.assertNotEqual(set(anchors(readme)), set(anchors(self.install)))

    def test_every_badge_points_at_an_anchor_that_exists(self):
        """context-mode's badges are `href="#"` and go nowhere; these must not.

        A badge row is only an index if clicking a badge lands on that agent's box.
        """
        targets = BADGE.findall(self.readme)
        self.assertEqual(set(targets), set(AGENTS), targets)
        for target in targets:
            with self.subTest(badge=target):
                self.assertIn(f'<a id="{target}"></a>', self.readme)


class EveryBoxSaysHowToUpdate(unittest.TestCase):
    """Install is guessable by analogy; update is not, and it differs on all four.

    This is the half of #783 a reader cannot work out for themselves: `claude
    plugin install` is a no-op on an installed plugin, Cursor has no install
    command at all, and `agy install` overwrites in place. A box with only an
    install command sends somebody to a version they cannot move off.
    """

    @classmethod
    def setUpClass(cls):
        cls.readme = README.read_text(encoding="utf-8")
        cls.install = INSTALL_DOC.read_text(encoding="utf-8")

    def sections(self, text: str) -> dict[str, str]:
        """Each agent's prose: from its anchor to the next one, or to its own end.

        "Or to the end of the file" was wrong for the **last** agent. `cursor` is
        last in the README, so its section ran to the end of the document and any
        later `**update` — in eight hundred lines of unrelated prose — would have
        satisfied the check for a box that had none. The last section stops at the
        `</details>` that closes its box, or at the next horizontal rule on a page
        that does not use them.
        """
        found = {}
        positions = [(m.group(1), m.start()) for m in ANCHOR.finditer(text)]
        for index, (name, start) in enumerate(positions):
            if index + 1 < len(positions):
                end = positions[index + 1][1]
            else:
                tail = text[start:]
                for closer in ("</details>", "\n---\n", "\n## "):
                    at = tail.find(closer)
                    if at != -1:
                        end = start + at
                        break
                else:
                    end = len(text)
            found[name] = text[start:end]
        return found

    def test_the_last_section_stops_at_its_own_box(self):
        """Vacuity, on the one section that had none: it used to run to the file end."""
        for text, where in ((self.readme, "README.md"), (self.install, "docs/install.md")):
            with self.subTest(document=where):
                last = self.sections(text)[AGENTS[-1]]
                self.assertLess(len(last), 2000, f"{where}: the last section runs on")

    def test_the_split_returns_a_body_per_agent(self):
        for text, where in ((self.readme, "README.md"), (self.install, "docs/install.md")):
            with self.subTest(document=where):
                bodies = self.sections(text)
                for agent in AGENTS:
                    self.assertGreater(len(bodies[agent]), 120, f"{where}: {agent}")

    def test_every_agent_box_has_an_install_and_an_update(self):
        for text, where in ((self.readme, "README.md"), (self.install, "docs/install.md")):
            bodies = self.sections(text)
            for agent in AGENTS:
                with self.subTest(document=where, agent=agent):
                    body = bodies[agent].lower()
                    self.assertIn("**install", body, f"{where}: {agent} has no Install")
                    self.assertIn("**update", body, f"{where}: {agent} has no Update")


#: Every document that gives somebody an install instruction. The finding this
#: set exists for: the stale "Codex has no plugin manifest" claim was fixed in the
#: matrix and left standing in `docs/skill.md`, which is a third page nobody
#: thought to check — the same shape as ai-jury#781 one document further out.
INSTRUCTION_PAGES = ("docs/platforms.md", "docs/skill.md", "docs/install.md", "README.md")

#: A markdown link into another document's anchor, as `](target.md#anchor)`.
#: A markdown link into another document's anchor, as `](../target.md#anchor)`.
#:
#: The relative prefix is **inside** the capture. Left outside it, `../README.md`
#: was captured as `README.md` and resolved against the linking page's own
#: directory — `docs/README.md`, which does not exist — and the check then skipped
#: it silently. Two of the three links this test was written for were invisible to
#: it, and a deliberately broken anchor passed.
#:
#: Case-insensitive on the filename for the same reason: `README.md` is not
#: lowercase.
DOC_ANCHOR_LINK = re.compile(r"\]\(((?:\.\.?/)*[A-Za-z0-9./_-]+\.md)#([a-z0-9-]+)\)")


class NoPageStillTellsAReaderTheOldStory(unittest.TestCase):
    """The matrix said Codex had no plugin format long after it had one.

    `docs/platforms.md` is the page a reader checks for *whether* a surface is
    supported; `docs/install.md` tells them how; `docs/skill.md` was giving its own
    install recipe and still carried the sentence the matrix had just lost. A page
    that gives an install instruction has to agree with the page that owns them.
    """

    def pages(self):
        return {name: (REPO_ROOT / name).read_text(encoding="utf-8") for name in INSTRUCTION_PAGES}

    def test_the_pages_were_read(self):
        for name, text in self.pages().items():
            with self.subTest(document=name):
                self.assertGreater(len(text.splitlines()), 20)

    def test_no_page_still_says_an_agent_has_no_plugin_format(self):
        """The exact claim, in the two spellings this repository used."""
        stale = ("does not yet expose a stable plugin manifest", "does not yet have a stable")
        for name, text in self.pages().items():
            for phrase in stale:
                with self.subTest(document=name, phrase=phrase):
                    self.assertNotIn(phrase, text)

    def test_no_matrix_row_is_still_called_planned(self):
        matrix = (REPO_ROOT / "docs" / "platforms.md").read_text(encoding="utf-8")
        for row in matrix.splitlines():
            if row.startswith("| **"):
                with self.subTest(row=row[:60]):
                    self.assertNotIn("planned", row.lower())

    def test_every_agent_row_sends_the_reader_to_the_install_page(self):
        """A matrix cell is read as a cheat-sheet, so a partial recipe is a wrong one.

        The Antigravity row gave `agy plugin install` and stopped, while the install
        page says `install` alone leaves the plugin **disabled**. A reader using the
        matrix never runs `enable`.
        """
        matrix = (REPO_ROOT / "docs" / "platforms.md").read_text(encoding="utf-8")
        rows = [row for row in matrix.splitlines() if row.startswith("| **")]
        self.assertGreater(len(rows), 4)
        named = [row for row in rows if "plugin " in row.lower() and "out of scope" not in row]
        self.assertGreater(len(named), 2, named)
        for row in named:
            with self.subTest(row=row[:60]):
                self.assertIn("install.md", row)

    def test_every_instruction_page_sends_the_reader_to_the_install_page(self):
        """A page that gives its own recipe has to point at the one that owns them.

        `docs/skill.md` gave a Claude-only install and no link; that is how an
        install-only recipe survives beside a page whose whole point is that
        `plugin install` is not an upgrade path.
        """
        for name, text in self.pages().items():
            if name == "docs/install.md":
                continue
            with self.subTest(document=name):
                self.assertIn("install.md", text, f"{name} never points at the install page")

    def test_the_anchor_pattern_sees_the_links_that_are_there(self):
        """Vacuity: a pattern matching nothing passes the check below.

        It matched nothing on the one page that matters — `docs/install.md`'s two
        links are into `README.md`, and the first cut of the pattern accepted only
        lowercase filenames.
        """
        found = DOC_ANCHOR_LINK.findall((REPO_ROOT / "docs" / "install.md").read_text())
        self.assertTrue(any(target.endswith("README.md") for target, _ in found), found)

    def test_the_site_registers_every_page_it_links_between(self):
        """A link out of a site-rendered page has to stay in the docs app.

        `website/docs.html` routes in-site only for files it registers, so a page
        the guides list does not know about drops the reader out of the app — and
        `docs/platforms.md` links into `install.md` from three rows.
        """
        site = (REPO_ROOT / "website" / "docs.html").read_text(encoding="utf-8")
        self.assertIn('file: "install.md"', site)
        self.assertIn('file: "platforms.md"', site)

    def test_every_cross_document_anchor_resolves(self):
        """The renamed heading left `platforms.md#codex-cli-template--manual` dangling.

        A link into a heading breaks silently when the heading is reworded, which is
        exactly what happened while fixing the sentence above.
        """
        for name, text in self.pages().items():
            base = (REPO_ROOT / name).parent
            for target, anchor in DOC_ANCHOR_LINK.findall(text):
                path = (base / target).resolve()
                with self.subTest(document=name, link=f"{target}#{anchor}"):
                    # A missing file is a finding, not a reason to skip. Skipping is
                    # how a mis-resolved path turned this whole check into a no-op.
                    self.assertTrue(path.is_file(), f"{name} links to {target}, which is not there")
                    body = path.read_text(encoding="utf-8")
                    headings = {
                        re.sub(r"[^a-z0-9 -]", "", line.lstrip("#").strip().lower()).replace(
                            " ", "-"
                        )
                        for line in body.splitlines()
                        if line.startswith("#")
                    }
                    self.assertTrue(
                        f'<a id="{anchor}"></a>' in body or anchor in headings,
                        f"{name} links to {target}#{anchor}, which is not there",
                    )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
