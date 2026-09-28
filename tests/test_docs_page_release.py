"""The site's docs page reads the released version's `docs/`, not `main` (#876).

`website/docs.html` fetched every document from `main`, so a visitor could read
behaviour no release had yet, with nothing on the page saying so. It now reads the
release tag named by `DOCS_TAG`, which `scripts/release_surfaces.py` lists as a
release surface: the release pull request bumps it with every other file that names
the version, and `make release-check` fails until it does.

A tag does not exist between a release pull request merging and the tag being
pushed, and a document added since the release is not in the tag at all. For those
404s the page reads `main` and labels the document as unreleased; any other failure
is an error rather than a reason to show unreleased text.

The checks below run the page's own `fetchDoc` and its own URL constants under node,
against a stubbed `fetch`, and record every URL it asks for.
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
DOCS_PAGE = REPO_ROOT / "website" / "docs.html"
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import release_surfaces  # noqa: E402

from ai_jury import __version__  # noqa: E402

_DRIVER = r"""
const fs = require("fs"), vm = require("vm");
const html = fs.readFileSync(process.argv[2], "utf8");
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
const head = html.indexOf('var OWNER = ');
const tail = html.indexOf("\n", html.indexOf("var MAIN_BLOB_ROOT = "));
if (head < 0 || tail < 0) throw new Error("no URL constants");
const constants = html.slice(head, tail);
async function run(statuses) {
  const asked = [];
  const ctx = {
    fetch(url) {
      asked.push(url);
      const status = statuses[asked.length - 1];
      return Promise.resolve({ ok: status === 200, status, text: () => Promise.resolve("# " + url) });
    },
  };
  vm.runInNewContext(constants + ";" + extract("fetchDoc")
    + "; var out = fetchDoc('parameters.md');", ctx);
  try {
    const doc = await ctx.out;
    return { asked, ref: doc.ref, md: doc.md, tag: ctx.DOCS_TAG };
  } catch (e) {
    return { asked, error: e.message, tag: ctx.DOCS_TAG };
  }
}
(async () => {
  console.log(JSON.stringify({
    released: await run([200]),
    tag_missing: await run([404, 200]),
    tag_error: await run([500]),
  }));
})();
"""

TAG = f"v{__version__}"
TAG_URL = f"https://raw.githubusercontent.com/berkayturanci/ai-jury/{TAG}/docs/parameters.md"
MAIN_URL = "https://raw.githubusercontent.com/berkayturanci/ai-jury/main/docs/parameters.md"


@unittest.skipUnless(shutil.which("node"), "needs node to execute the page script")
class TheDocsPageReadsTheRelease(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workdir = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, workdir, True)
        driver = Path(workdir) / "fetchdoc.js"
        driver.write_text(_DRIVER, encoding="utf-8")
        cls.done = subprocess.run(
            [shutil.which("node"), str(driver), str(DOCS_PAGE)],
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )

    def _ran(self) -> dict:
        self.assertEqual(self.done.returncode, 0, self.done.stderr)
        return json.loads(self.done.stdout)

    def test_the_page_names_the_packaged_release(self):
        self.assertEqual(self._ran()["released"]["tag"], TAG)

    def test_a_released_document_is_read_from_the_tag_only(self):
        released = self._ran()["released"]
        self.assertEqual(released["asked"], [TAG_URL])
        self.assertEqual(released["ref"], TAG)
        self.assertEqual(released["md"], "# " + TAG_URL)

    def test_a_document_missing_from_the_tag_is_read_from_main_and_marked(self):
        missing = self._ran()["tag_missing"]
        self.assertEqual(missing["asked"], [TAG_URL, MAIN_URL])
        self.assertEqual(missing["ref"], "main")

    def test_any_other_failure_is_an_error_not_unreleased_text(self):
        failed = self._ran()["tag_error"]
        self.assertEqual(failed["asked"], [TAG_URL])
        self.assertEqual(failed["error"], "HTTP 500")


class TheDocsPageIsAReleaseSurface(unittest.TestCase):
    def test_docs_tag_is_in_the_surface_table(self):
        surfaces = [s for s in release_surfaces.RELEASE_SURFACES if s.path == "website/docs.html"]
        self.assertEqual(len(surfaces), 1)
        page = DOCS_PAGE.read_text(encoding="utf-8")
        self.assertEqual(surfaces[0].find(page), [__version__])

    def test_the_page_labels_what_it_shows(self):
        page = DOCS_PAGE.read_text(encoding="utf-8")
        self.assertIn("release tag", page)
        self.assertIn('" (unreleased: not in " + DOCS_TAG', page)
        self.assertIsNone(re.search(r'BRANCH\s*=\s*"main"', page))


if __name__ == "__main__":
    unittest.main()
