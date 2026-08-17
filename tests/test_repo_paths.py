"""Guard against case-only duplicate paths in the git index.

macOS and Windows check out files case-insensitively, so a rename that only
changes capitalisation (``.Jules/`` -> ``.jules/``) can leave *both* spellings
in the index while the working tree shows one file. The duplicate is invisible
locally and only splits into two divergent files on a case-sensitive clone
(Linux CI, most contributors' containers).

That is exactly how ``.Jules/palette.md`` survived the ``.jules/`` consolidation
in #418. This pin fails loudly the next time it happens. Network-free; skipped
when git is unavailable or the tree is not a checkout.
"""

from __future__ import annotations

import collections
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).parent.parent


def _tracked_paths() -> list[str] | None:
    """Return every tracked path, or None when git can't answer."""
    git = shutil.which("git")
    if git is None:
        return None
    try:
        proc = subprocess.run(
            [git, "-C", str(REPO), "ls-files", "-z"],
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - env guard
        return None
    if proc.returncode != 0:  # pragma: no cover - env guard
        return None
    return [p for p in proc.stdout.split("\0") if p]


def _ancestors(path: str) -> list[str]:
    """Every ancestor directory of a POSIX-style tracked path."""
    parts = path.split("/")[:-1]
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


class TestRepoPathCasing(unittest.TestCase):
    def test_no_case_only_duplicate_paths(self):
        paths = _tracked_paths()
        if paths is None:  # pragma: no cover - env guard
            self.skipTest("git is unavailable or this is not a checkout")

        by_folded = collections.defaultdict(set)
        for path in paths:
            by_folded[path.lower()].add(path)

        clashes = {
            folded: sorted(spellings)
            for folded, spellings in by_folded.items()
            if len(spellings) > 1
        }
        self.assertEqual(
            clashes,
            {},
            "tracked paths differ only by capitalisation, so a case-sensitive "
            "clone gets two files where macOS/Windows show one; keep a single "
            f"spelling: {clashes}",
        )

    def test_no_case_only_duplicate_directories(self):
        """A parent directory can clash even when no leaf filename does."""
        paths = _tracked_paths()
        if paths is None:  # pragma: no cover - env guard
            self.skipTest("git is unavailable or this is not a checkout")

        by_folded = collections.defaultdict(set)
        for path in paths:
            for parent in _ancestors(path):
                by_folded[parent.lower()].add(parent)

        clashes = {
            folded: sorted(spellings)
            for folded, spellings in by_folded.items()
            if len(spellings) > 1
        }
        self.assertEqual(
            clashes,
            {},
            "tracked directories differ only by capitalisation, which splits "
            f"into separate trees on a case-sensitive clone: {clashes}",
        )


if __name__ == "__main__":  # pragma: no cover - manual entry point
    unittest.main()
