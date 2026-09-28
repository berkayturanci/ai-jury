"""Every file in `docs/` is on the site's docs page, or left off it on purpose (#876).

`website/docs.html` renders only the documents its `GROUPS` registry names, and it
had left out 12 of them, every security audit from 2026-06-13 onwards among them:
each new document was one nobody remembered to add. The check below enumerates
`docs/*.md` and fails on a file that is neither indexed nor in `EXCLUDED` with a
reason, so the next one cannot go missing silently.

The audits are indexed as their own group, newest first. Each is a record of the
day it was run, so the page names it by that date and says, on its card and on its
page, that its findings may since be fixed; none of them is presented as a current
document. The node checks run the page's own registry, `renderHome` and `renderDoc`
against stubs, so what they see is what a reader sees.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"
DOCS_PAGE = REPO_ROOT / "website" / "docs.html"
sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_jury import __version__  # noqa: E402

#: The one list of `docs/*.md` files the docs page leaves out on purpose, each with
#: the reason it does. A file here must exist and must not also be indexed. Every
#: document in `docs/` is indexed today, so the list is empty; add an entry as
#: `"name.md": "why a reader of the site should not be sent to it",`.
EXCLUDED: dict[str, str] = {}

#: The index group that holds the dated security records, and the files it holds.
AUDIT_GROUP = "Security audits"
AUDIT_FILE = re.compile(r"^security-(?:audit|scan)-(\d{4}-\d\d-\d\d)(?:-[\w.]+)?\.md$")

_GROUPS_BLOCK = re.compile(r"var GROUPS = \[(.*?)\n    \];", re.DOTALL)
_GROUP = re.compile(
    r'\{ title: "(?P<title>[^"]+)"(?P<records>, records: true)?, items: \[(?P<body>.*?)\n\s*\]\}',
    re.DOTALL,
)
_ITEM = re.compile(
    r'\{ slug: "(?P<slug>[^"]+)", file: "(?P<file>[^"]+)"'
    r'(?:, date: "(?P<date>[^"]+)")?, title: "(?P<title>[^"]+)" \}'
)


def index_groups(page: str) -> list[dict]:
    """The docs page's `GROUPS`, read from its source: title, records flag, items."""
    block = _GROUPS_BLOCK.search(page)
    if block is None:
        raise AssertionError("docs.html has no `var GROUPS = [ ... ];` block")
    groups = []
    for group in _GROUP.finditer(block.group(1)):
        items = [m.groupdict() for m in _ITEM.finditer(group.group("body"))]
        if len(items) != group.group("body").count("slug:"):
            raise AssertionError(
                f"an item in {group.group('title')!r} is not in the expected shape"
            )
        groups.append(
            {"title": group.group("title"), "records": bool(group.group("records")), "items": items}
        )
    if sum(len(g["items"]) for g in groups) != block.group(1).count("slug:"):
        raise AssertionError("a group in docs.html's GROUPS is not in the expected shape")
    return groups


def unaccounted(on_disk: list[str], indexed: list[str], excluded: dict[str, str]) -> dict:
    """What is wrong between `docs/`, the page's index and the exclusion list.

    Empty when every document is indexed exactly once or excluded with a reason, and
    every name in the index or the exclusion list is a document that exists.
    """
    disk, listed = set(on_disk), set(indexed)
    problems = {
        "neither indexed nor excluded": sorted(disk - listed - set(excluded)),
        "indexed but not in docs/": sorted(listed - disk),
        "indexed twice": sorted({f for f in indexed if indexed.count(f) > 1}),
        "excluded but not in docs/": sorted(set(excluded) - disk),
        "both indexed and excluded": sorted(listed & set(excluded)),
        "excluded without a reason": sorted(f for f, why in excluded.items() if not why.strip()),
    }
    return {kind: files for kind, files in problems.items() if files}


def _page() -> str:
    return DOCS_PAGE.read_text(encoding="utf-8")


def _indexed_files() -> list[str]:
    return [item["file"] for group in index_groups(_page()) for item in group["items"]]


class EveryDocumentIsAccountedFor(unittest.TestCase):
    def test_every_doc_is_indexed_or_excluded_with_a_reason(self):
        on_disk = sorted(p.name for p in DOCS.glob("*.md"))
        self.assertGreater(len(on_disk), 30)
        self.assertEqual(
            unaccounted(on_disk, _indexed_files(), EXCLUDED),
            {},
            "add the file to GROUPS in website/docs.html, or to EXCLUDED in "
            "tests/test_docs_index.py with the reason it is left out",
        )

    def test_a_new_document_nobody_listed_is_caught(self):
        self.assertEqual(
            unaccounted(["a.md", "new.md"], ["a.md"], {}),
            {"neither indexed nor excluded": ["new.md"]},
        )

    def test_an_exclusion_with_a_reason_accounts_for_a_file(self):
        self.assertEqual(unaccounted(["a.md", "b.md"], ["a.md"], {"b.md": "internal notes"}), {})

    def test_stale_and_contradictory_entries_are_caught(self):
        self.assertEqual(
            unaccounted(
                ["a.md", "b.md"], ["a.md", "a.md", "b.md", "gone.md"], {"b.md": " ", "old.md": "x"}
            ),
            {
                "indexed but not in docs/": ["gone.md"],
                "indexed twice": ["a.md"],
                "excluded but not in docs/": ["old.md"],
                "both indexed and excluded": ["b.md"],
                "excluded without a reason": ["b.md"],
            },
        )

    def test_a_malformed_registry_is_an_error_not_a_shorter_index(self):
        good = '{ slug: "a", file: "a.md", title: "A" }'
        bad = '{ slug: "b", file: "b.md", titel: "B" }'
        page = 'var GROUPS = [\n{ title: "G", items: [\n' + good + ",\n" + bad + "\n]}\n    ];"
        with self.assertRaisesRegex(AssertionError, "expected shape"):
            index_groups(page)
        with self.assertRaisesRegex(AssertionError, "no `var GROUPS"):
            index_groups("<html></html>")


class TheAuditsAreDatedRecords(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        groups = index_groups(_page())
        cls.groups = {g["title"]: g for g in groups}
        cls.audits = cls.groups[AUDIT_GROUP]["items"] if AUDIT_GROUP in cls.groups else []

    def test_the_audits_are_a_group_of_their_own(self):
        self.assertIn(AUDIT_GROUP, self.groups)
        self.assertTrue(self.groups[AUDIT_GROUP]["records"])
        on_disk = sorted(p.name for p in DOCS.glob("*.md") if AUDIT_FILE.match(p.name))
        self.assertEqual(sorted(item["file"] for item in self.audits), on_disk)
        for title, group in self.groups.items():
            if title != AUDIT_GROUP:
                with self.subTest(group=title):
                    self.assertFalse(group["records"])
                    self.assertEqual(
                        [i["file"] for i in group["items"] if AUDIT_FILE.match(i["file"])], []
                    )
                    self.assertEqual([i["file"] for i in group["items"] if i["date"]], [])

    def test_each_audit_carries_the_date_its_file_names_and_a_scope(self):
        self.assertTrue(self.audits)
        for item in self.audits:
            with self.subTest(file=item["file"]):
                self.assertEqual(item["date"], AUDIT_FILE.match(item["file"]).group(1))
                self.assertGreater(len(item["title"].split()), 3, "a one-line scope")

    def test_the_audits_are_newest_first(self):
        dates = [item["date"] for item in self.audits]
        self.assertTrue(dates)
        self.assertEqual(dates, sorted(dates, reverse=True))


_DRIVER = r"""
const fs = require("fs"), vm = require("vm");
const html = fs.readFileSync(process.argv[2], "utf8");
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
function extract(name) {
  const start = html.indexOf("function " + name + "(");
  if (start < 0) throw new Error("no function " + name);
  let depth = 0;
  for (let i = html.indexOf("{", start); i < html.length; i++) {
    if (html[i] === "{") depth++;
    else if (html[i] === "}" && --depth === 0) return html.slice(start, i + 1);
  }
  throw new Error("unbalanced " + name);
}
function between(from, to) {
  const a = html.indexOf(from), b = html.indexOf(to, a);
  if (a < 0 || b < 0) throw new Error("no " + from);
  return html.slice(a, b);
}
// The page's URL constants and its whole registry, with the loop that names records.
const setup = between("var OWNER = ", "var $ = function")
  + extract("buildSidebar") + extract("renderHome") + extract("fetchDoc") + extract("renderDoc");
function node() {
  const n = { writes: [], children: [], className: "",
    appendChild(c) { this.children.push(c); return c; }, querySelectorAll() { return []; } };
  Object.defineProperty(n, "innerHTML", { set(v) { n.writes.push(String(v)); }, get() { return ""; } });
  return n;
}
function context(statuses) {
  const asked = [], made = [];
  const ctx = {
    window: { scrollTo() {}, DOMPurify: { sanitize: () => "<p>ok</p>" } },
    DOMPurify: { sanitize: () => "<p>ok</p>" }, marked: { parse: () => "<p>ok</p>" },
    document: { createElement() { const n = node(); made.push(n); return n; }, title: "" },
    contentEl: node(), sideEl: node(), URL,
    rewrite() {}, buildTOC() {}, scrollToAnchor() {},
    fetch(url) {
      asked.push(url);
      const status = statuses[asked.length - 1];
      return Promise.resolve({ ok: status === 200, status, text: () => Promise.resolve("# t") });
    },
  };
  ctx.contentEl.querySelector = () => node();
  vm.runInNewContext(setup, ctx);
  return { ctx, asked, made };
}
(async () => {
  const home = context([]);
  vm.runInNewContext("renderHome();", home.ctx);
  const out = { home: home.ctx.contentEl.writes.join(""), sidebar: home.ctx.sideEl.writes.join(""),
    tag: home.ctx.DOCS_TAG, docs: [] };
  for (const c of cases) {
    const run = context(c.statuses);
    vm.runInNewContext("renderDoc(" + JSON.stringify(c.slug) + ", null);", run.ctx);
    await new Promise((r) => setTimeout(r, 5));
    const meta = run.made.flatMap((n) => n.writes).filter((w) => w.includes("doc-source"));
    out.docs.push({ slug: c.slug, statuses: c.statuses, asked: run.asked, meta,
      title: run.ctx.document.title });
  }
  console.log(JSON.stringify(out));
})();
"""

TAG = f"v{__version__}"
RAW = "https://raw.githubusercontent.com/berkayturanci/ai-jury"
NOTE = "record of {date}; findings may since be fixed"


def _files_at_tag() -> set[str] | None:
    """The `docs/` files in the release tag, or None when this checkout lacks the tag."""
    git = shutil.which("git")
    if git is None:
        return None
    listed = subprocess.run(
        [git, "ls-tree", "--name-only", f"refs/tags/{TAG}", "docs/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        stdin=subprocess.DEVNULL,
    )
    if listed.returncode != 0:
        return None
    return {Path(line).name for line in listed.stdout.splitlines()}


@unittest.skipUnless(shutil.which("node"), "needs node to execute the page script")
class ThePageShowsEachEntry(unittest.TestCase):
    """Each entry resolves at the release tag, or through the 404-only fallback, labelled."""

    @classmethod
    def setUpClass(cls):
        cls.items = [item for group in index_groups(_page()) for item in group["items"]]
        cls.at_tag = _files_at_tag()
        cases = []
        for item in cls.items:
            cases.append({"slug": item["slug"], "statuses": [200]})
            cases.append({"slug": item["slug"], "statuses": [404, 200]})
        workdir = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, workdir, True)
        driver = Path(workdir) / "index.js"
        driver.write_text(_DRIVER, encoding="utf-8")
        cases_file = Path(workdir) / "cases.json"
        cases_file.write_text(json.dumps(cases), encoding="utf-8")
        cls.done = subprocess.run(
            [shutil.which("node"), str(driver), str(DOCS_PAGE), str(cases_file)],
            capture_output=True,
            text=True,
            # node writes UTF-8; Windows would decode it as cp1252 (the ` · ` separator)
            encoding="utf-8",
            timeout=60,
            stdin=subprocess.DEVNULL,
        )

    def _ran(self) -> dict:
        self.assertEqual(self.done.returncode, 0, self.done.stderr)
        return json.loads(self.done.stdout)

    def _runs(self, slug: str, statuses: list[int]) -> dict:
        found = [d for d in self._ran()["docs"] if d["slug"] == slug and d["statuses"] == statuses]
        self.assertTrue(found, (slug, statuses))
        return found[0]

    def _check(self, item: dict, statuses: list[int]) -> None:
        run = self._runs(item["slug"], statuses)
        tag_url = f"{RAW}/{TAG}/docs/{item['file']}"
        main_url = f"{RAW}/main/docs/{item['file']}"
        self.assertEqual(len(run["meta"]), 1, run["meta"])
        meta = run["meta"][0]
        if statuses == [200]:
            self.assertEqual(run["asked"], [tag_url])
            self.assertIn(f"docs/{item['file']} · {TAG}</span>", meta)
            self.assertNotIn("unreleased", meta)
        else:
            self.assertEqual(run["asked"], [tag_url, main_url])
            self.assertIn(f"docs/{item['file']} · main (unreleased: not in {TAG})</span>", meta)
        if item["date"]:
            self.assertIn(f'doc-record">{NOTE.format(date=item["date"])}</span>', meta)
            self.assertTrue(run["title"].startswith(f"{item['date']} · "), run["title"])
        else:
            self.assertNotIn("doc-record", meta)
            self.assertNotIn("findings may since be fixed", meta)

    def test_every_entry_reads_the_tag_or_falls_back_labelled(self):
        self.assertEqual(self._ran()["tag"], TAG)
        for item in self.items:
            for statuses in ([200], [404, 200]):
                with self.subTest(file=item["file"], statuses=statuses):
                    self._check(item, statuses)

    def test_every_audit_is_in_the_release_tag(self):
        # An audit is a dated record, so the page should read it from the release tag;
        # one that exists only on main would render through the fallback, labelled
        # unreleased, and pass every rendering check above.
        if self.at_tag is None:
            self.skipTest(f"this checkout has no {TAG} tag")
        audits = [item for item in self.items if item["date"]]
        self.assertTrue(audits, "no dated audit entries were read from the page")
        for item in audits:
            with self.subTest(file=item["file"]):
                self.assertIn(item["file"], self.at_tag)

    def test_the_home_page_shows_audits_as_dated_records(self):
        home = self._ran()["home"]
        start = home.find("<h2>Security audits</h2>")
        self.assertGreaterEqual(start, 0, "the home page has no Security audits block")
        section = home[start : home.index("</div></div>", start)]
        self.assertIn("Its findings may since be fixed", section)
        self.assertIn('<a href="#security">Security</a> describes the code as it is.', section)
        audits = [i for i in self.items if i["date"]]
        for item in audits:
            with self.subTest(file=item["file"]):
                card = (
                    f'<a class="dh-card" href="#{item["slug"]}"><span class="dh-t">'
                    f"{item['date']} · {item['title']}</span>"
                    f'<span class="dh-f mono">{NOTE.format(date=item["date"])}</span></a>'
                )
                self.assertIn(card, section)
        for item in self.items:
            if not item["date"]:
                with self.subTest(file=item["file"]):
                    self.assertNotIn(f'class="dh-card" href="#{item["slug"]}"', section)
                    self.assertIn(f'<span class="dh-f mono">docs/{item["file"]}</span>', home)
        self.assertEqual(home.count("findings may since be fixed"), len(audits) + 1)

    def test_the_sidebar_names_each_audit_by_its_date(self):
        sidebar = self._ran()["sidebar"]
        for item in self.items:
            with self.subTest(file=item["file"]):
                name = f"{item['date']} · {item['title']}" if item["date"] else item["title"]
                self.assertIn(f'href="#{item["slug"]}">{name}</a>', sidebar)


if __name__ == "__main__":
    unittest.main()
