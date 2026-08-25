"""Guard against git conflict markers reaching a tracked file.

``CHANGELOG.md`` carried three of them on ``main`` from #601 (2026-08-24) until
#615 — published in the rendered changelog, and green on every CI run in
between, because nothing asserted their absence.

The residue was asymmetric: an orphan ``=======``/``>>>>>>>`` pair, and twenty
lines later an orphan ``<<<<<<<``. Neither region looked like a complete
conflict block, which is why reading the diff did not catch it. A mechanical
scan does not care whether the block is well formed.

All three markers are refused, ``=======`` included. A bare seven-character
equals line is a plausible setext heading underline, but no tracked file in
this repository uses one, and dropping it from the set would let a resolution
that leaves *only* the separator behind pass — which is the same class of
half-finished merge. If a legitimate underline is ever needed, change its
length; the marker is exactly seven characters.

Network-free; skipped when git is unavailable or the tree is not a checkout.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).parent.parent

# Built by repetition so this file does not match its own scan.
MARKERS = ("<" * 7, "=" * 7, ">" * 7)


def _tracked_paths() -> list[str] | None:
    """Return every tracked path, or None when git can't answer."""
    git = shutil.which("git")
    if git is None:  # pragma: no cover - env guard
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


def _marker_hits(text: str) -> list[tuple[int, str]]:
    """Every ``(line number, marker)`` at the start of a line."""
    hits = []
    for number, line in enumerate(text.splitlines(), start=1):
        for marker in MARKERS:
            # A marker is the seven characters alone or followed by a space —
            # so `>>>>>>>>` (eight, a quoted-reply chain) does not match.
            if line == marker or line.startswith(marker + " "):
                hits.append((number, marker))
    return hits


class TestNoConflictMarkers(unittest.TestCase):
    def test_no_tracked_file_carries_a_conflict_marker(self):
        paths = _tracked_paths()
        if paths is None:  # pragma: no cover - env guard
            self.skipTest("git is unavailable or this is not a checkout")

        scanned = 0
        offenders: dict[str, list[tuple[int, str]]] = {}
        for path in paths:
            full = REPO / path
            try:
                text = full.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                # Binary, a symlink into nothing, or a submodule pointer —
                # not text this guard can speak about.
                continue
            scanned += 1
            hits = _marker_hits(text)
            if hits:
                offenders[path] = hits

        # Vacuity: a scan that read nothing passes for the wrong reason. Pin a
        # floor and the one file the guard exists because of.
        self.assertGreater(
            scanned,
            50,
            f"only {scanned} tracked text files were read — the scan is not "
            "reaching the tree, so its passing says nothing",
        )
        self.assertIn(
            "CHANGELOG.md",
            paths,
            "CHANGELOG.md is the file this guard was written for; if it is no "
            "longer tracked, the guard needs rethinking rather than deleting",
        )

        self.assertEqual(
            offenders,
            {},
            "tracked files carry git conflict markers — a merge was resolved "
            f"without finishing: {offenders}",
        )

    def test_the_scan_finds_a_marker_that_is_there(self):
        """The counterweight: an empty result must mean 'clean', not 'blind'."""
        # The exact residue #601 left in CHANGELOG.md, rebuilt.
        residue = "\n".join(["- an entry", MARKERS[1], "", MARKERS[2] + " eb43eaa (#607)"])
        self.assertEqual(
            _marker_hits(residue),
            [(2, MARKERS[1]), (4, MARKERS[2])],
        )
        self.assertEqual(_marker_hits(MARKERS[0] + " HEAD"), [(1, MARKERS[0])])

    def test_a_longer_run_is_not_a_marker(self):
        """Eight angle brackets is a quoted reply, not a conflict."""
        self.assertEqual(_marker_hits(">" * 8 + " quoted"), [])
        self.assertEqual(_marker_hits("=" * 8), [])
        self.assertEqual(_marker_hits("  " + MARKERS[0] + " indented"), [])
