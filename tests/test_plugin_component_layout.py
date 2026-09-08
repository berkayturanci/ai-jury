"""The skill directory is where every agent looks for it (#775).

`agy plugin install https://github.com/berkayturanci/ai-jury` reported success and
imported **zero** components:

    [ok]    ai-jury
            - skills      : skipped (not found)
            - agents      : skipped (not found)
            - commands    : skipped (not found)

No error, no warning. The plugin landed in `~/.gemini/config/plugins/ai-jury/`,
`agy plugin list` recorded `"components": null`, and the user had an installed,
enabled plugin that could never trigger.

The cause is a discovery rule, not a bad path. `agy` finds components **only by
root-level directory convention** and ignores every path field in `plugin.json`.
Verified with throwaway plugins fed to `agy plugin validate` (agy 2026.09.x, macOS
arm64):

===========================================  =========================
layout                                       result
===========================================  =========================
``skill/demo/SKILL.md`` + ``"skills": "./skill"``   ``skipped (not found)``
``skills/demo/SKILL.md``                            ``1 processed``
``skill/demo/SKILL.md``, no ``skills`` field        ``skipped (not found)``
``.agents/skills/demo/SKILL.md``                    ``skipped (not found)``
===========================================  =========================

Row one was exactly this repository: the skill lived in `skill/` (singular) and both
manifests correctly declared `"skills": "./skill"`. Claude Code and Codex resolve that
field and were unaffected; agy never reads it.

Antigravity is one of the three vendor seats ai-jury itself convenes, so shipping a
package that silently imports nothing on that vendor is the worst failure mode this
project has — it looks installed. `git mv skill skills` satisfies both rules at once:
the manifests still name the directory, and agy now finds it by convention.

What this file asserts is the *conjunction*, because either half alone is what broke:
the directory is at the root under the conventional name, **and** every manifest points
at it. An install that exits 0 having imported nothing cannot be caught any other way.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
#: The name agy discovers by convention. Not configurable, and not read from a manifest —
#: that is the whole point of the defect this pins.
CONVENTIONAL_SKILLS_DIR = "skills"
MANIFESTS = (".claude-plugin/plugin.json", ".codex-plugin/plugin.json")

#: The directory the skill used to live in, derived rather than spelled so this module's
#: own source does not trip the scan below.
LEGACY_SKILLS_DIR = CONVENTIONAL_SKILLS_DIR.removesuffix("s")

#: Files exempt from the stale-path scan, and why each one is.
#:
#: `CHANGELOG.md` describes each release as it shipped. `docs/live-review-report.md` is the
#: verbatim, explicitly not-hand-edited output of the v1.1.0 four-vendor run — in which a
#: reviewer flagged this very mismatch (a manifest naming the singular directory against a
#: skill inside it) as a low-confidence note, in 2026-06; it was right, and it is left
#: standing. Rewriting a path in either would falsify a record rather than fix a link.
#:
#: This module is exempt for the opposite reason: its subject *is* the migration, so it has
#: to be able to name the layout that was replaced.
EXEMPT = (
    "CHANGELOG.md",
    "docs/live-review-report.md",
    "tests/test_plugin_component_layout.py",
)

#: Where a path actually appears, as opposed to where the word does. A markdown link
#: target and an inline code span are paths; prose is not — `skill/workflow consumers` and
#: `skill/plugin mechanism` are alternations, and a scan that flagged them would be edited
#: until it caught nothing. The first cut matched the bare substring `<dir>/ai-jury`, which
#: was narrow in the other direction: it could not see ``points its `skills` field at this
#: same `skill/` directory`` in `docs/skill.md`, which is exactly the sentence two gate
#: reviewers found. Both shapes are checked now.
_LINK_TARGET = re.compile(r"\]\(([^)]+)\)")
_CODE_SPAN = re.compile(r"`([^`]+)`")

#: Every file kind that can carry a path a reader follows.
DOC_GLOBS = ("*.md", "*.txt", "*.json", "*.toml", "*.yml", "*.yaml", "*.py")


class TheSkillsDirectoryIsWhereEveryAgentLooks(unittest.TestCase):
    def _manifests(self) -> list[tuple[str, dict]]:
        found = []
        for name in MANIFESTS:
            path = REPO_ROOT / name
            self.assertTrue(path.is_file(), f"{name} is missing")
            found.append((name, json.loads(path.read_text(encoding="utf-8"))))
        return found

    def test_the_directory_is_at_the_root_under_the_conventional_name(self):
        """agy reads root `skills/` and nothing else — not `skill/`, not `.agents/`."""
        directory = REPO_ROOT / CONVENTIONAL_SKILLS_DIR

        self.assertTrue(directory.is_dir(), f"{CONVENTIONAL_SKILLS_DIR}/ is not at the root")
        self.assertTrue(
            list(directory.glob("*/SKILL.md")),
            f"{CONVENTIONAL_SKILLS_DIR}/ holds no <name>/SKILL.md, so agy imports nothing",
        )

    def test_the_old_singular_directory_is_gone(self):
        """Two directories would be worse than one wrong one: agy would import the
        conventional one and the manifest-reading agents the other, and they could
        drift without anything noticing."""
        self.assertFalse((REPO_ROOT / "skill").exists(), "skill/ and skills/ both exist")

    def test_every_manifest_points_at_that_same_directory(self):
        """Claude Code and Codex resolve the declared path; the convention is not enough
        for them, just as the declaration was not enough for agy."""
        for name, manifest in self._manifests():
            with self.subTest(manifest=name):
                declared = manifest.get("skills")
                self.assertIsInstance(declared, str, f"{name} declares no skills path")
                resolved = (REPO_ROOT / declared.lstrip("./")).resolve()
                self.assertEqual(resolved, (REPO_ROOT / CONVENTIONAL_SKILLS_DIR).resolve())

    def test_the_skill_the_manifests_promise_actually_exists(self):
        """A path that resolves to an empty directory installs just as silently."""
        for name, manifest in self._manifests():
            with self.subTest(manifest=name):
                declared = REPO_ROOT / str(manifest["skills"]).lstrip("./")
                self.assertTrue(list(declared.glob("*/SKILL.md")), f"{name}: no skill there")

    @staticmethod
    def _names_the_old_directory(text: str) -> bool:
        """Is this a *path* into the legacy directory, rather than the word in prose?"""
        return re.match(rf"(?:[^\s]*/)?{re.escape(LEGACY_SKILLS_DIR)}/", text.strip()) is not None

    def test_no_document_still_sends_a_reader_to_the_old_path(self):
        """The rename is only done when the instructions agree with it.

        Link targets and inline code spans, across every file kind that can carry a path —
        not just markdown, since `llms-full.txt` links the skill too.
        """
        stale = []
        exempt = {(REPO_ROOT / name).resolve() for name in EXEMPT}
        for glob in DOC_GLOBS:
            for path in sorted(REPO_ROOT.rglob(glob)):
                if ".git" in path.parts or path.resolve() in exempt:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                for number, line in enumerate(text.splitlines(), 1):
                    hits = _LINK_TARGET.findall(line) + _CODE_SPAN.findall(line)
                    if any(self._names_the_old_directory(hit) for hit in hits):
                        stale.append(f"{path.relative_to(REPO_ROOT)}:{number}  {line.strip()[:80]}")
        self.assertEqual(
            [], stale, "these still point at the pre-#775 path:\n" + "\n".join(sorted(set(stale)))
        )

    def test_that_scan_can_actually_see_both_shapes(self):
        """The first cut matched one substring and missed the sentence two reviewers
        found, so the scan's own reach is asserted rather than assumed."""
        old = LEGACY_SKILLS_DIR
        for line in (
            f"see [the skill]({old}/ai-jury/SKILL.md)",
            f"points its `skills` field at this same `{old}/` directory",
            f"drop `{old}/ai-jury/` into a project",
            f"[`{old}/`](../{old})",
        ):
            with self.subTest(line=line):
                hits = _LINK_TARGET.findall(line) + _CODE_SPAN.findall(line)
                self.assertTrue(any(self._names_the_old_directory(h) for h in hits), line)

    def test_and_does_not_flag_the_word_in_prose(self):
        """`skill/workflow consumers` and `skill/plugin mechanism` are alternations. A
        check that failed on those would be weakened until it caught nothing."""
        for line in (
            "downstream skill/workflow consumers, so it changes only deliberately.",
            "once the platform exposes a stable skill/plugin mechanism.",
            "see [the skill](../skills/ai-jury/SKILL.md)",
        ):
            with self.subTest(line=line):
                hits = _LINK_TARGET.findall(line) + _CODE_SPAN.findall(line)
                self.assertFalse(any(self._names_the_old_directory(h) for h in hits), line)

    def test_the_exempt_files_are_real_files(self):
        """An exemption for a file that no longer exists silently widens the blind spot
        the next time someone adds a path to it."""
        for name in EXEMPT:
            with self.subTest(exempt=name):
                self.assertTrue((REPO_ROOT / name).is_file(), f"{name} is gone")


if __name__ == "__main__":
    unittest.main()
