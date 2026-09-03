"""Every stated coverage floor must be the one `pyproject.toml` enforces.

The README said the gate was **99%** while `[tool.coverage.report] fail_under`
had been `98` for fifteen releases (#686), and `website/coverage.html` — the page
the README sends people to — said 99% in four more places, including the
JavaScript that labels the published figure. At the real total that mislabel was
not cosmetic: a passing 98.5% run rendered as "Below the 99% gate" in warning
colours on the public site.

Nothing caught any of it. The number lives as prose in two documents and as
configuration in a third, and a reader has no reason to doubt the one in front of
them — the wrong one is the one a contributor plans around.

The assertion is deliberately one-directional. It does not care what the floor
*is*, only that every place quoting it quotes the value actually in force, so
raising `fail_under` fails here until each sentence is raised with it. That
failure is the reminder, and it costs one line per site to clear.

Stdlib only (`tomllib` ships with the 3.11 minimum this project supports), like
the rest of the suite.
"""

from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
COVERAGE_PAGE = REPO_ROOT / "website" / "coverage.html"
PYPROJECT = REPO_ROOT / "pyproject.toml"

#: Where the gate is stated, and how each place spells it. Anchored on the
#: surrounding phrase rather than on any bare percentage, so an unrelated number
#: (the measured total, a chart bound) is never mistaken for the floor.
#:
#: The `pct >= N` line is in the list because it is the *behaviour*, not prose: it
#: decides whether the published figure is labelled as passing the gate. It is
#: matched together with the `state.textContent` assignment that follows it, so the
#: colour ramp's own thresholds (`pct >= 90`, `>= 80`, `>= 60`) are not mistaken
#: for the gate.
GATE_STATEMENTS: dict[Path, tuple[re.Pattern[str], ...]] = {
    README: (re.compile(r"minimum total coverage is \*\*(\d+)%\*\*"),),
    COVERAGE_PAGE: (
        re.compile(r"minimum total coverage is <strong>(\d+)%</strong>"),
        re.compile(r"gated at a minimum (\d+)% in CI"),
        re.compile(r"the (\d+)% gate"),
        re.compile(r"if \(pct >= (\d+)\) \{ state\.textContent"),
    ),
}


def _fail_under() -> object:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return config["tool"]["coverage"]["report"]["fail_under"]


class TheStatedCoverageGateIsTheEnforcedOne(unittest.TestCase):
    def test_pyproject_declares_a_numeric_fail_under(self):
        """A missing or non-numeric gate would make every comparison vacuous."""
        fail_under = _fail_under()
        self.assertNotIsInstance(fail_under, bool)
        self.assertIsInstance(fail_under, (int, float))

    def test_every_documented_gate_matches_pyproject(self):
        fail_under = _fail_under()
        for path, patterns in GATE_STATEMENTS.items():
            text = path.read_text(encoding="utf-8")
            for pattern in patterns:
                with self.subTest(file=path.name, pattern=pattern.pattern):
                    stated = [int(n) for n in pattern.findall(text)]
                    self.assertTrue(
                        stated,
                        f"{path.name} no longer states the gate as "
                        f"'{pattern.pattern}' — the guard has stopped watching it",
                    )
                    for number in stated:
                        self.assertEqual(
                            number,
                            fail_under,
                            f"{path.name} says the coverage gate is {number}%, "
                            f"but pyproject.toml sets fail_under = {fail_under}",
                        )

    def test_the_readme_states_the_gate_exactly_once(self):
        """Two statements of one number is the drift this test exists to stop."""
        matches = re.findall(r"minimum total coverage is \*\*(\d+)%\*\*", README.read_text("utf-8"))
        self.assertEqual(
            len(matches),
            1,
            f"expected exactly one 'minimum total coverage is **N%**' in README.md, got {matches}",
        )


if __name__ == "__main__":
    unittest.main()
