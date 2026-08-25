"""A release section may appear at most once per version.

Written after `## [Unreleased]` accumulated `### Added` and `### Changed`
**twice each** over one working day. Every entry was correct and none was lost;
they were simply inserted above the previous top section each time, so the
document grew alternating headings.

It matters at release time rather than while editing: `## [Unreleased]` becomes
`## [x.y.z]` verbatim, so a duplicated heading ships to PyPI and to the GitHub
Release notes, where a reader looking for "what changed" finds two lists and no
reason to think either is complete.

Cheap to detect, invisible to review — a reviewer sees the diff hunk, not the
shape of the whole section.
"""

from __future__ import annotations

import collections
import re
import unittest
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"

#: Keep a Changelog's six, plus the three this project has actually used.
#:
#: Deliberately a fixed list rather than one derived from the file, which would
#: pass by construction. The point is to catch a *near-miss* — `### Fixes` is
#: silently a different bucket from `### Fixed`, and a reader scanning for one
#: heading will not find the other. Adding a genuinely new section is a
#: one-line edit here, and that edit is the review.
KNOWN = {
    "Added",
    "Changed",
    "Deprecated",
    "Removed",
    "Fixed",
    "Security",
    "Documentation",
    "Performance",
    "Internal",
}


def versions() -> dict[str, list[str]]:
    """Every `## [version]` block mapped to the `### Section` names inside it."""
    blocks: dict[str, list[str]] = {}
    current = None
    for line in CHANGELOG.read_text(encoding="utf-8").splitlines():
        version = re.match(r"^## \[([^\]]+)\]", line)
        if version:
            current = version.group(1)
            blocks[current] = []
            continue
        section = re.match(r"^### (.+?)\s*$", line)
        if section and current is not None:
            blocks[current].append(section.group(1))
    return blocks


class EachSectionAppearsOnce(unittest.TestCase):
    def setUp(self):
        self.blocks = versions()

    def test_there_are_versions_to_check(self):
        """Vacuity: a parser that matched nothing would pass everything below."""
        self.assertGreater(len(self.blocks), 5, "the changelog was not parsed")
        self.assertIn("Unreleased", self.blocks)

    def test_no_version_repeats_a_section(self):
        offenders = {}
        for version, sections in self.blocks.items():
            repeated = [s for s, n in collections.Counter(sections).items() if n > 1]
            if repeated:
                offenders[version] = repeated
        self.assertEqual(
            offenders,
            {},
            "a release section appears more than once under one version, so the "
            f"released notes carry two lists of the same kind: {offenders}",
        )

    def test_every_section_name_is_a_known_one(self):
        """`### Fixes` is silently a different bucket from `### Fixed`."""
        unknown = {
            version: sorted(set(sections) - KNOWN)
            for version, sections in self.blocks.items()
            if set(sections) - KNOWN
        }
        self.assertEqual(unknown, {}, f"unrecognised changelog sections: {unknown}")

    def test_the_unreleased_block_has_content(self):
        """An empty Unreleased block at release time means entries went missing."""
        self.assertTrue(
            self.blocks["Unreleased"],
            "## [Unreleased] carries no sections; a release cut from it would be blank",
        )
