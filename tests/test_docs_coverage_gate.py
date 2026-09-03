"""The coverage floor the README states must be the one `pyproject.toml` enforces.

The README said the gate was **99%** while `[tool.coverage.report] fail_under`
had been `98` for fifteen releases (#686). Nothing caught it: the number lives as
prose in one file and as configuration in another, and a reader has no reason to
doubt either — the wrong one is the one a contributor plans around.

The assertion is deliberately one-directional. It does not care what the floor
*is*, only that the README quotes the value actually in force, so raising
`fail_under` fails here until the sentence is updated with it. That failure is
the reminder, and it costs one line to clear.

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
PYPROJECT = REPO_ROOT / "pyproject.toml"

#: The README's own statement of the gate — "minimum total coverage is **98%**".
#: Anchored on the phrase rather than on any bare percentage in the file, so an
#: unrelated number elsewhere in the coverage section is not mistaken for it.
STATED_GATE = re.compile(r"minimum total coverage is \*\*(\d+)%\*\*")


def _fail_under() -> object:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return config["tool"]["coverage"]["report"]["fail_under"]


class TheReadmeStatesTheEnforcedCoverageGate(unittest.TestCase):
    def setUp(self) -> None:
        self.readme = README.read_text(encoding="utf-8")

    def test_pyproject_declares_a_numeric_fail_under(self):
        """A missing or non-numeric gate would make the comparison vacuous."""
        fail_under = _fail_under()
        self.assertNotIsInstance(fail_under, bool)
        self.assertIsInstance(fail_under, (int, float))

    def test_the_readme_states_the_gate_exactly_once(self):
        """Two statements of one number is the drift this test exists to stop."""
        matches = STATED_GATE.findall(self.readme)
        self.assertEqual(
            len(matches),
            1,
            f"expected exactly one 'minimum total coverage is **N%**' in README.md, got {matches}",
        )

    def test_the_stated_gate_is_the_enforced_one(self):
        match = STATED_GATE.search(self.readme)
        assert match is not None  # pinned by the test above
        stated = int(match.group(1))
        fail_under = _fail_under()
        self.assertEqual(
            stated,
            fail_under,
            f"README.md says the coverage gate is {stated}%, but "
            f"pyproject.toml sets fail_under = {fail_under}",
        )


if __name__ == "__main__":
    unittest.main()
