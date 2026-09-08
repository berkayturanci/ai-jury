"""The repository's context files exist, and only one of them holds the rules (#776).

The root shipped `CLAUDE.md` and nothing else. Every agent that reads the cross-vendor
`AGENTS.md` convention — Codex, Cursor, opencode, Zed, and the standard itself — got no
project context here and worked from whatever it could infer from the tree.

For a project whose whole premise is that different vendors review the same diff, that is
the wrong file to be missing: an agent editing this repository without context is more
likely to break a vendor adapter than in a single-vendor project, and Antigravity is one
of the seats this jury actually convenes.

So `AGENTS.md` is now the canonical file — it is the same text, renamed — and `CLAUDE.md`
and `GEMINI.md` are pointers to it. That shape is the thing worth checking: three copies
of one rule set is a slower version of the same failure, because two of them go stale and
nothing says which is authoritative. What this asserts is therefore not "the files exist"
but "the files exist **and** the rules live in exactly one of them".
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The canonical file. Everything durable lives here.
CANONICAL = "AGENTS.md"
#: Vendor entry points. Each must point at the canonical file and stay short.
POINTERS = ("CLAUDE.md", "GEMINI.md")
#: A pointer that grew past this is no longer a pointer. `AGENTS.md` is ~100 lines, so
#: the bar says "an entry point, not a second rule book" without pinning a paragraph count.
POINTER_MAX_LINES = 40


class TheContextFilesExist(unittest.TestCase):
    def test_the_canonical_file_is_present(self):
        self.assertTrue((REPO_ROOT / CANONICAL).is_file(), f"{CANONICAL} is missing")

    def test_every_vendor_pointer_is_present(self):
        for name in POINTERS:
            with self.subTest(file=name):
                self.assertTrue((REPO_ROOT / name).is_file(), f"{name} is missing")

    def test_each_pointer_names_the_canonical_file(self):
        """A vendor file that does not link `AGENTS.md` is a second rule book."""
        for name in POINTERS:
            with self.subTest(file=name):
                text = (REPO_ROOT / name).read_text(encoding="utf-8")
                self.assertIn(f"]({CANONICAL})", text, f"{name} does not link {CANONICAL}")

    def test_each_pointer_stays_a_pointer(self):
        for name in POINTERS:
            with self.subTest(file=name):
                lines = (REPO_ROOT / name).read_text(encoding="utf-8").splitlines()
                self.assertLessEqual(
                    len(lines),
                    POINTER_MAX_LINES,
                    f"{name} is {len(lines)} lines — put durable rules in {CANONICAL}",
                )

    def test_the_rules_are_not_duplicated_into_a_pointer(self):
        """Sampled by heading, not by prose: `AGENTS.md`'s section headings are its
        structure, and a pointer that reproduces one is reproducing the section."""
        headings = set(
            re.findall(r"^##\s+(.+)$", (REPO_ROOT / CANONICAL).read_text(encoding="utf-8"), re.M)
        )
        self.assertTrue(headings, f"{CANONICAL} has no sections — the check is vacuous")

        for name in POINTERS:
            text = (REPO_ROOT / name).read_text(encoding="utf-8")
            copied = sorted(h for h in headings if re.search(rf"^##\s+{re.escape(h)}$", text, re.M))
            with self.subTest(file=name):
                self.assertEqual([], copied, f"{name} repeats {CANONICAL} sections: {copied}")

    def test_every_link_a_pointer_makes_resolves(self):
        """An entry point whose links are broken is worse than no entry point: it sends
        a reader somewhere and the reader stops there."""
        broken = []
        for name in (CANONICAL, *POINTERS):
            text = (REPO_ROOT / name).read_text(encoding="utf-8")
            for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", text):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                if not (REPO_ROOT / target).exists():
                    broken.append(f"{name} -> {target}")
        self.assertEqual([], broken, "these links go nowhere:\n" + "\n".join(broken))


if __name__ == "__main__":
    unittest.main()
