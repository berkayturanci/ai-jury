"""Every version-pinned CDN script on the site carries a Subresource Integrity hash (#817).

A pinned URL says what to ask for, not what arrives: whatever the CDN answers runs with the
page's origin, and one of the docs page's three CDN scripts is the HTML sanitizer itself.
``integrity="sha384-…"`` makes the browser refuse a script whose bytes differ, and since #816
the docs page fails closed when one is refused.

Two halves, as in ``tests/test_action_pins.py``:

* **Offline, always.** A version-pinned external script must carry ``integrity`` and
  ``crossorigin="anonymous"``; any other external script must be on the short allowlist
  below, so a new CDN script cannot arrive unpinned.
* **Online, opt-in** (``AI_JURY_CHECK_EXTERNAL=1``; CI sets it in the network job). Each
  pinned script is fetched and hashed. Nothing offline can tell a right hash from a wrong
  one, and a wrong one breaks the page for every reader — a version bumped without its
  hash is the way that happens.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import unittest
import urllib.error
import urllib.request
from pathlib import Path

WEBSITE = Path(__file__).parent.parent / "website"
ONLINE = os.environ.get("AI_JURY_CHECK_EXTERNAL") == "1"

#: External scripts that cannot carry a hash, and why. Anything else must be pinned.
UNPINNABLE = {
    # Unversioned, and Cloudflare updates it in place: a hash would break analytics on
    # their next release. It is the documented way to load Web Analytics.
    "https://static.cloudflareinsights.com/beacon.min.js",
}

_SCRIPT = re.compile(r"<script\b([^>]*)>", re.IGNORECASE)
_ATTR = re.compile(r"""([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
#: A script loaded from a URL by *code*: `el.src = "https://…"`, `import … from "https://…"`
#: or `import("https://…")`. No attribute on those carries a hash, so none may exist off the
#: allowlist — the Firebase modules this site once imported from gstatic.com arrived this way.
_LOADED_BY_CODE = re.compile(
    r"""(?:\.src\s*=\s*|\bimport\s*(?:[^"';()]*?\sfrom\s*)?|\bimport\(\s*)["'](https?://[^"']+)["']"""
)
#: `@1.2.3` in the URL: an exact release, which is what makes the bytes stable enough to hash.
_EXACT_VERSION = re.compile(r"@\d+\.\d+\.\d+(?=/)")
_SHA384 = re.compile(r"sha384-[A-Za-z0-9+/]{64}")


def _external_scripts() -> list[tuple[str, dict[str, str]]]:
    """``(page, attributes)`` for every ``<script src="http(s)://…">`` on the site."""
    found = []
    for page in sorted(WEBSITE.glob("*.html")):
        for tag in _SCRIPT.findall(page.read_text(encoding="utf-8")):
            attrs = {name.lower(): a or b for name, a, b in _ATTR.findall(tag)}
            if re.match(r"https?://", attrs.get("src", "")):
                found.append((page.name, attrs))
    return found


def _pinned() -> list[tuple[str, dict[str, str]]]:
    return [(page, a) for page, a in _external_scripts() if _EXACT_VERSION.search(a["src"])]


class TestExternalScriptsArePinnedOrNamed(unittest.TestCase):
    """Offline: the shape the browser needs, and no script outside the two lists."""

    def test_every_pinned_script_carries_a_sha384_and_crossorigin(self):
        bare = [
            f"{page}: {attrs['src']}"
            for page, attrs in _pinned()
            if not _SHA384.fullmatch(attrs.get("integrity", ""))
            # Without CORS mode the browser cannot read a cross-origin response to hash
            # it, and refuses the script outright.
            or attrs.get("crossorigin") != "anonymous"
        ]
        self.assertEqual([], bare, 'needs integrity="sha384-…" and crossorigin="anonymous"')

    def test_every_other_external_script_is_on_the_allowlist(self):
        loose = [
            f"{page}: {attrs['src']}"
            for page, attrs in _external_scripts()
            if not _EXACT_VERSION.search(attrs["src"]) and attrs["src"] not in UNPINNABLE
        ]
        self.assertEqual([], loose, "pin an exact version and add its hash, or name it above")

    def test_code_loads_no_script_from_a_url_off_the_allowlist(self):
        loose = [
            f"{source.name}: {url}"
            for source in sorted([*WEBSITE.glob("*.js"), *WEBSITE.glob("*.html")])
            for url in _LOADED_BY_CODE.findall(source.read_text(encoding="utf-8"))
            if url not in UNPINNABLE
        ]
        self.assertEqual([], loose, "a script loaded by code cannot carry an integrity hash")

    def test_the_code_load_pattern_sees_each_shape(self):
        for line in (
            's.src = "https://cdn.example/a.js";',
            'import { x } from "https://cdn.example/a.js";',
            "import 'https://cdn.example/a.js';",
            'const m = await import("https://cdn.example/a.js");',
        ):
            self.assertEqual(_LOADED_BY_CODE.findall(line), ["https://cdn.example/a.js"], line)
        # A relative module and an image are not external scripts.
        self.assertEqual(_LOADED_BY_CODE.findall('import { y } from "./app.js";'), [])
        self.assertEqual(_LOADED_BY_CODE.findall('img.setAttribute("src", RAW_ROOT + rel);'), [])

    def test_the_patterns_match_something(self):
        # Vacuity: a pattern that matched nothing would pass both tests above.
        self.assertGreaterEqual(len(_pinned()), 3, "the docs page loads three CDN scripts")
        self.assertTrue(UNPINNABLE & {attrs["src"] for _, attrs in _external_scripts()})
        # No site script loads one by code any more: `analytics.js`, the last that did, went
        # in #829. `test_the_code_load_pattern_sees_each_shape` is what keeps the pattern honest.
        self.assertFalse((WEBSITE / "analytics.js").exists())

    def test_a_tag_is_read_whichever_way_it_is_quoted(self):
        tag = """ defer src='https://cdn.example/x@1.2.3/x.js' integrity="sha384-abc" """
        attrs = {name.lower(): a or b for name, a, b in _ATTR.findall(tag)}
        self.assertEqual(attrs["src"], "https://cdn.example/x@1.2.3/x.js")
        self.assertEqual(attrs["integrity"], "sha384-abc")
        self.assertTrue(_EXACT_VERSION.search(attrs["src"]))
        # A major-only or `latest` URL is not an exact release, so it is not hashable.
        self.assertFalse(_EXACT_VERSION.search("https://cdn.example/x@1/x.js"))
        self.assertFalse(_EXACT_VERSION.search("https://cdn.example/x@latest/x.js"))


@unittest.skipUnless(ONLINE, "set AI_JURY_CHECK_EXTERNAL=1 to fetch the scripts and hash them")
class TestIntegrityHashesMatchWhatTheCdnServes(unittest.TestCase):
    """Online: the hash beside each URL is the hash of what that URL returns."""

    def test_each_script_hashes_to_its_integrity_attribute(self):
        wrong = []
        for page, attrs in _pinned():
            request = urllib.request.Request(attrs["src"], headers={"User-Agent": "ai-jury-tests"})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    body = response.read()
            except urllib.error.HTTPError as exc:
                # The CDN answered, and not with the script: readers get the same answer.
                wrong.append(f"{page}: {attrs['src']} -> HTTP {exc.code}")
                continue
            except (urllib.error.URLError, OSError) as exc:
                # Being unable to look is not evidence the hash is wrong.
                self.skipTest(f"could not reach {attrs['src']}: {exc}")
            actual = "sha384-" + base64.b64encode(hashlib.sha384(body).digest()).decode()
            if actual != attrs["integrity"]:
                wrong.append(f"{page}: {attrs['src']} is {actual}, page says {attrs['integrity']}")
        self.assertEqual([], wrong)


if __name__ == "__main__":
    unittest.main()
