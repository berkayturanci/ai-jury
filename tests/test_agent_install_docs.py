"""The README's install boxes and `docs/install.md` name the same agents.

`docs/install.md` exists because the README churns and a release note cannot link
into it stably. Two documents covering one subject is how #781's `@v1` came about:
three pages agreed with each other and none of them agreed with the repository.

**What these tests check is structure, not content.** Which agents appear on each
page, that every badge resolves to an anchor that exists, that every box carries
an Update as well as an Install, that every page printing an install command
points at the page that owns them, and that every cross-document anchor is real.
They do **not** verify a command against the tool it names — both pages can agree
on a wrong marketplace name and stay green. That check would have to run the
CLIs, and the commands here were measured by hand instead, with the date and the
version written into the pages themselves.

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

#: An explicit anchor. The README needs them — a `<details>` box has no heading to
#: take a slug from — and each sits *before* its box, because navigating to an id
#: inside `<summary>` scrolls to it without expanding it. `install.md` has headings
#: and uses those; writing both would give the rendered page two elements with one
#: id.
ANCHOR = re.compile(r'<a id="([a-z0-9-]+)"></a>')

#: `[![Name](badge-url)](#anchor)` — the badge row that doubles as the index.
BADGE = re.compile(r"\[!\[[^\]]+\]\([^)]+\)\]\(#([a-z0-9-]+)\)")

#: A `- [Claude Code](#claude-code)` line in a page's own Contents list.
CONTENTS_ENTRY = re.compile(r"^- \[[^\]]+\]\(#([a-z0-9-]+)\)", re.M)

#: The agents both documents must cover. Named here on purpose: this is the one
#: fact the two pages cannot derive from each other, and adding a fifth agent
#: should be a deliberate edit to this line rather than something a page silently
#: drops.
AGENTS = ("claude-code", "codex", "antigravity", "cursor")

#: The install page, as the failure messages name it.
INSTALL_NAME = "docs/install.md"


#: A markdown heading, whose GitHub slug is an anchor as real as an explicit one.
HEADING = re.compile(r"^#{2,3} +(.+?)\s*$", re.M)


def heading_slug(title: str) -> str:
    """GitHub's heading anchor, near enough for the names these pages use."""
    return re.sub(r"[^a-z0-9 -]", "", title.lower()).strip().replace(" ", "-")


def anchors(text: str) -> list[str]:
    """Every id this document offers — explicit anchors and heading slugs alike.

    Explicit anchors are what the README needs, because a `<details>` box has no
    heading. The install page has headings, and writing an `<a id>` under one gave
    the rendered page **two** elements with the same id — a hazard for anything
    that resolves an anchor by lookup. A heading is an anchor; both count.
    """
    return ANCHOR.findall(text) + [heading_slug(title) for title in HEADING.findall(text)]


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

    def declared(self, text: str) -> set[str]:
        """The agents a document *declares* — its badge targets, or its contents list.

        Both are explicit lists an author edits when adding an agent, which is what
        makes them comparable. Two earlier versions of this test compared anchor
        sets and then filtered them back down to `AGENTS`, which is the tautology
        it is named after wearing a different filter: a fifth host added to one
        page only was invisible both times. Nothing is filtered here.
        """
        badges = set(BADGE.findall(text))
        if badges:
            return badges
        return {m.group(1) for m in CONTENTS_ENTRY.finditer(text)}

    def test_each_document_declares_the_agents_it_covers(self):
        """Vacuity: two empty declarations are equal, and would prove nothing."""
        self.assertGreaterEqual(len(self.declared(self.readme)), len(AGENTS))
        self.assertGreaterEqual(len(self.declared(self.install)), len(AGENTS))

    def test_the_two_documents_cover_the_same_agents(self):
        """The drift this file exists for: one page gains an agent, the other does not.

        Whole sets, unfiltered. `anchors() & AGENTS` on both sides reduced to
        `AGENTS == AGENTS`; keeping "ids in AGENTS or starting with zed" was the
        same filter with a hole cut for one test's fixture. Both seats said so, in
        both repositories.
        """
        self.assertEqual(self.declared(self.readme), self.declared(self.install))

    def test_a_fifth_agent_on_one_page_only_is_caught(self):
        """The mutation both filtered versions survived, through the real assertion."""
        readme = self.readme.replace(
            "](#cursor)",
            "](#cursor)\n[![Zed](https://img.shields.io/badge/Zed-install-000?style=flat-square)](#zed)",
        )
        self.assertNotEqual(self.declared(readme), self.declared(self.install))

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
        """Each agent's prose, from its own boundary to the next one.

        A boundary is an explicit `<a id>` **or** a heading — the README uses the
        first because a `<details>` box has no heading, the install page uses the
        second, and writing both under one heading gave the rendered page two
        elements with the same id. Splitting on either keeps one implementation
        for two shapes.

        "Or to the end of the file" was wrong for the **last** agent: `cursor` is
        last in the README, so its section ran to the end of the document and any
        later `**update` would have satisfied the check for a box that had none.
        The last section stops at the `</details>` that closes its box, or at the
        next rule on a page that does not use them.
        """
        marks = [(m.group(1), m.start()) for m in ANCHOR.finditer(text)]
        marks += [(heading_slug(m.group(1)), m.start()) for m in HEADING.finditer(text)]
        marks.sort(key=lambda pair: pair[1])
        found = {}
        for index, (name, start) in enumerate(marks):
            end = marks[index + 1][1] if index + 1 < len(marks) else len(text)
            # A box ends at its own `</details>`, whatever the next mark is. With
            # headings among the marks, "the next mark" put the Cursor box's end
            # hundreds of lines away — so a missing `**Update**` was satisfied by
            # unrelated prose, which is the hole this bound exists to close.
            closer = text.find("</details>", start)
            if closer != -1 and closer < end:
                end = closer
            found[name] = text[start:end]
        return found

    def test_the_last_section_stops_at_its_own_box(self):
        """Vacuity, on the one section that had none: it used to run to the file end.

        The property, not a character budget. A budget failed the day the Cursor
        box legitimately grew a second update command, which is the wrong thing to
        refuse; what matters is that the split **cut** something rather than
        handing back the rest of the document.
        """
        for text, where in ((self.readme, "README.md"), (self.install, INSTALL_NAME)):
            with self.subTest(document=where):
                sections = self.sections(text)
                last = sections[AGENTS[-1]]
                self.assertIn(last, text)
                self.assertLess(
                    len(last), len(text) - text.index(last), f"{where}: the last section runs on"
                )
                self.assertNotIn("## See also", last)

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


#: The commands that make a page an install instruction, whatever it calls itself.
INSTALL_COMMANDS = (
    "/plugin install",
    "/plugin marketplace add",
    "plugin marketplace add",
    "agy plugin install",
    "codex plugin add",
    "cursor-agent plugin marketplace add",
)

#: Records of what shipped, not instructions to follow.
INSTRUCTION_EXEMPT = ("CHANGELOG.md", "docs/live-review-report.md")

#: `llms-full.txt` is the machine-readable summary of this repository, and it had
#: its own one-agent distribution paragraph. It is not markdown, so the walk above
#: does not reach it; it is named here because there is exactly one of it.
MACHINE_SUMMARY = "llms-full.txt"

#: Directories with no documents of ours in them. `tests` included: this file
#: quotes the commands it looks for, and a walk that read it would report itself.
SKIPPED_DIRS = {".git", ".venv", "node_modules", "htmlcov", "__pycache__", "tests", "benchmark"}


def instruction_pages() -> dict[str, str]:
    """Every tracked document that tells somebody how to install this plugin.

    **Discovered, not listed.** A hardcoded four-name tuple was the same defect
    this module exists to refuse, one level in: `docs/cookbook.md` and a later
    section of the README both print an install-only recipe, and neither was in
    the tuple, so `test_every_instruction_page_sends_the_reader_to_the_install_page`
    passed while two pages sent readers to a version they could not move off.
    A page that prints one of these commands is an install page whatever its
    title says.
    """
    found = {}
    for path in sorted(REPO_ROOT.rglob("*.md")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if SKIPPED_DIRS & set(Path(relative).parts) or relative in INSTRUCTION_EXEMPT:
            continue
        text = path.read_text(encoding="utf-8")
        if any(command in text for command in INSTALL_COMMANDS):
            found[relative] = text
    return found


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
        return instruction_pages()

    def test_the_pages_were_read(self):
        pages = self.pages()
        self.assertLessEqual(
            {"README.md", "docs/install.md", "docs/skill.md", "docs/cookbook.md"},
            set(pages),
            f"the walk did not reach the pages that carry a recipe: {sorted(pages)}",
        )
        for name, text in pages.items():
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

    def test_the_machine_summary_covers_the_same_four_agents(self):
        """`llms-full.txt` is what a model reads about this repository.

        Its distribution paragraph described a Claude Code skill, a `.claude/skills/`
        copy and a `.claude-plugin/` marketplace, and named none of the other three
        agents or the page that documents them — the same one-agent story, on the
        surface written specifically to be summarised.
        """
        summary = (REPO_ROOT / MACHINE_SUMMARY).read_text(encoding="utf-8")
        self.assertIn("docs/install.md", summary)
        # And not the claim the landing page had to drop: Cursor has no CLI
        # install command, so "each with its own install command" is wrong
        # wherever it appears — including the file written to be summarised.
        self.assertNotIn("each with its own install command", summary)
        for agent in ("Codex", "Antigravity", "Cursor"):
            with self.subTest(agent=agent):
                self.assertIn(agent, summary)

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

    def test_the_public_site_does_not_lead_with_one_agent(self):
        """The site is the surface most people see, and it told the old story.

        The landing page's drop-in-skill blurb offered "a Claude Code plugin or
        copy the skill" and left the other three agents to a sentence about
        platforms in general — the same collision #783 is named after, where an
        agent appears as a *reviewer* everywhere and as a *host* nowhere.
        """
        # The landing page is where the four-agent statement belongs, and it can
        # hold a link. The integration gallery is *per agent* — a Claude card
        # beside a Codex card — so a four-agent sentence inside one of them
        # contradicts its own name, and its `desc` is written with `textContent`,
        # so a URL there would render as literal text anyway.
        landing = (REPO_ROOT / "website" / "index.html").read_text(encoding="utf-8")
        # The sentence, not the substrings. Every agent name appears elsewhere on
        # this page as a *reviewer*, which is the collision this issue is named
        # after — so finding "Cursor" somewhere in the file proves nothing about
        # the paragraph that tells a reader where to install.
        blurb = next(
            (line for line in landing.splitlines() if "Install as a plugin in" in line), ""
        )
        self.assertTrue(blurb, "the drop-in-skill blurb is not there to check")
        # In-site, like every other doc link on the page: the docs app renders the
        # markdown, and sending a reader to the GitHub blob leaves the site to read
        # a page the site can show.
        self.assertIn('href="docs.html#install"', blurb)
        for agent in ("Codex", "Antigravity", "Cursor"):
            with self.subTest(agent=agent):
                self.assertIn(agent, blurb)

    def test_the_site_registers_every_page_it_links_between(self):
        """A link out of a site-rendered page has to stay in the docs app.

        `website/docs.html` routes in-site only for files it registers, so a page
        the guides list does not know about drops the reader out of the app — and
        `docs/platforms.md` links into `install.md` from three rows.
        """
        site = (REPO_ROOT / "website" / "docs.html").read_text(encoding="utf-8")
        self.assertIn('file: "install.md"', site)
        self.assertIn('file: "platforms.md"', site)

    def test_the_site_keeps_the_fragment_a_link_carried(self):
        """Registering the page is half of it; landing on the right box is the rest.

        `rewrite()` used to read `(frag && frag !== "#" ? "" : "")` — both arms
        empty — so `install.md#cursor` became `#install` and every cross-page
        anchor in the docs quietly lost its destination. A location hash cannot
        hold two `#`, so the sub-anchor rides after `--` and the router splits it.
        """
        site = (REPO_ROOT / "website" / "docs.html").read_text(encoding="utf-8")
        self.assertNotIn('(frag && frag !== "#" ? "" : "")', site)
        self.assertIn('"--" + frag.slice(1)', site)
        self.assertIn('indexOf("--")', site)
        self.assertIn("renderDoc(slug, anchor)", site)

    def test_the_site_treats_a_bare_hash_as_a_place_on_the_page(self):
        """Every document's own table of contents is written as `#heading`.

        `rewrite()` leaves those alone by design, and `route()` then looked the
        anchor up as a *document slug*, found nothing, and rendered the home page.
        Adding a Contents list to `install.md` is what made a pre-existing bug
        reachable: four links that each took the reader somewhere they did not ask
        to go.
        """
        site = (REPO_ROOT / "website" / "docs.html").read_text(encoding="utf-8")
        self.assertIn("else if (BY_SLUG[slug]) { renderDoc(slug, anchor); }", site)
        self.assertIn("else if (scrollToAnchor(slug)) { return; }", site)

    def test_an_in_page_anchor_carries_the_document_it_is_in(self):
        """`#cursor` works while the page is open and dies on reload.

        A bare fragment is a location hash with no document in it, so the same
        link a reader clicks happily is a URL that lands on the home page when
        shared or reloaded. Rewriting it to `#install--cursor` makes it an
        address.
        """
        site = (REPO_ROOT / "website" / "docs.html").read_text(encoding="utf-8")
        self.assertIn('"#" + slug + "--" + href.slice(1)', site)
        self.assertIn("rewrite(wrap, slug)", site)

    def test_a_jump_within_the_open_document_does_not_re_render_it(self):
        """Carrying the slug made every in-page jump a full page load.

        `#install--cursor` matched a known document first, so `renderDoc` replaced
        the article with a spinner and re-fetched the markdown — to land on a
        heading already on screen. The router checks the document it is showing
        before it decides to fetch one.
        """
        site = (REPO_ROOT / "website" / "docs.html").read_text(encoding="utf-8")
        self.assertIn("slug === currentSlug && scrollToAnchor(anchor)", site)
        self.assertIn("currentSlug = slug;", site)
        self.assertIn("currentSlug = null;", site)

    def test_the_site_scrolls_with_the_offset_the_sticky_nav_needs(self):
        """One scroll helper, so a jump cannot land under the header on one path."""
        site = (REPO_ROOT / "website" / "docs.html").read_text(encoding="utf-8")
        self.assertEqual(site.count("function scrollToAnchor"), 1)
        # The id comes from `location.hash`. Built into a CSS selector, a crafted
        # `#a"]` throws out of `querySelector`; `getElementById` parses nothing.
        self.assertNotIn("querySelector('[id=\"' + id", site)
        self.assertIn("document.getElementById(id)", site)
        # GitHub keeps `---` in a heading slug where this page's `slugify` collapses
        # it, so a markdown link can carry an id the document never assigned.
        self.assertIn("document.getElementById(slugify(id))", site)
        # The helper grew two fallbacks and a comment explaining each; bound the
        # window by the function's own end rather than by a character count that
        # fails the next time it earns a paragraph.
        body = site[site.index("function scrollToAnchor") :]
        body = body[: body.index("\n    }")]
        self.assertIn("- 76", body)
        # One implementation, actually: the table of contents inlined its own copy
        # of the same scroll, so "both jumps go through one helper" was not true of
        # the file that sentence described.
        self.assertEqual(site.count('behavior: "smooth"'), 1)
        self.assertIn('scrollToAnchor(link.getAttribute("data-id"))', site)

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
