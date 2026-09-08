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
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
#: The name agy discovers by convention. Not configurable, and not read from a manifest —
#: that is the whole point of the defect this pins.
CONVENTIONAL_SKILLS_DIR = "skills"
MANIFESTS = (".claude-plugin/plugin.json", ".codex-plugin/plugin.json")

#: Documents that record what was true at a past moment. Rewriting a path in one of these
#: would falsify the record rather than fix a link. `CHANGELOG.md` describes each release
#: as it shipped; `docs/live-review-report.md` is the verbatim, explicitly not-hand-edited
#: output of the v1.1.0 four-vendor run — in which a reviewer flagged this very mismatch
#: (`"skills": "./skill"` against a skill at `skill/ai-jury/`) as a low-confidence note, in
#: 2026-06. It was right, and it is left standing.
HISTORICAL_RECORDS = ("CHANGELOG.md", "docs/live-review-report.md")


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

    def test_no_document_still_sends_a_reader_to_the_old_path(self):
        """The rename is only done when the instructions agree with it.

        `HISTORICAL_RECORDS` is exempt, and the exemption is the point rather than a
        convenience: those files say what was true when they were written.
        """
        stale = []
        exempt = {(REPO_ROOT / name).resolve() for name in HISTORICAL_RECORDS}
        for path in sorted(REPO_ROOT.rglob("*.md")):
            if ".git" in path.parts or path.resolve() in exempt:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "skill/ai-jury" in line:
                    stale.append(f"{path.relative_to(REPO_ROOT)}:{number}")
        self.assertEqual([], stale, "these still point at the pre-#775 path:\n" + "\n".join(stale))

    def test_the_exempt_records_are_real_files(self):
        """An exemption for a file that no longer exists silently widens the check's
        blind spot the next time someone adds a path to it."""
        for name in HISTORICAL_RECORDS:
            with self.subTest(record=name):
                self.assertTrue((REPO_ROOT / name).is_file(), f"{name} is gone")


if __name__ == "__main__":
    unittest.main()
