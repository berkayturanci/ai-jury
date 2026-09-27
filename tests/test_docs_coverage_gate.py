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
        matches = re.findall(
            r"minimum total coverage is \*\*(\d+)%\*\*", README.read_text(encoding="utf-8")
        )
        self.assertEqual(
            len(matches),
            1,
            f"expected exactly one 'minimum total coverage is **N%**' in README.md, got {matches}",
        )


#: Every public page a reader or a model might quote a coverage figure from. The
#: served `website/llms.txt` said "100% test coverage" while the suite measured
#: 98.95% (#862); nothing enforced 100, so nothing noticed.
PUBLIC_TEXT: tuple[Path, ...] = (
    README,
    REPO_ROOT / "llms.txt",
    REPO_ROOT / "llms-full.txt",
    REPO_ROOT / "website" / "llms.txt",
    REPO_ROOT / "website" / "index.html",
    REPO_ROOT / "website" / "docs.html",
    *sorted((REPO_ROOT / "docs").glob("*.md")),
)

#: "100%" (or "100 %") within this many words of "cover…" is a coverage claim.
NEARBY_WORDS = 4
_PERCENT = re.compile(r"100\s?%")
_CLAUSE_BREAK = re.compile(r"[\n<>]|\.(?:\s|$)")
#: A module named just before the figure: `voting.py`, theater.py, `ballots`. A
#: backticked word counts only when it names one of ai-jury's own modules: the
#: docs backtick `ai-jury`, `subprocess` and `tomllib` too, and "`ai-jury` has
#: 100% test coverage" is exactly the claim #862 removed.
_PY_FILE = re.compile(r"\w\.py\b")
_BACKTICKED = re.compile(r"`([^`\s]+)`")
_MODULE_STEMS = frozenset(
    p.stem for p in (REPO_ROOT / "src" / "ai_jury").glob("*.py") if not p.stem.startswith("_")
)


def _names_a_module(words: str) -> bool:
    if _PY_FILE.search(words):
        return True
    return any(name in _MODULE_STEMS for name in _BACKTICKED.findall(words))


def full_coverage_claims(text: str) -> list[str]:
    """Every "100%" in ``text`` that claims coverage for the whole project.

    #862 was "zero runtime dependencies, 100% test coverage" on the served
    `llms.txt`, and the likeliest recurrence drops the qualifier: "100% coverage".
    So any 100% within a few words of "coverage" (or "covered") is refused,
    whatever the wording around it — "coverage: 100%", "100% branch coverage",
    "100 % unit test coverage". The one exception is a figure with a module named
    just before it ("`voting.py` has 100% coverage", "theater.py 100%"): the gate
    is a total, and a single module can truly be fully covered under it.
    """
    claims = []
    for match in _PERCENT.finditer(text):
        before = _CLAUSE_BREAK.split(text[max(0, match.start() - 120) : match.start()])[-1]
        after = _CLAUSE_BREAK.split(text[match.end() : match.end() + 120])[0]
        near_before = before.split()[-NEARBY_WORDS:]
        near_after = after.split()[:NEARBY_WORDS]
        if not re.search(r"cover", " ".join(near_before + near_after), re.IGNORECASE):
            continue
        if _names_a_module(" ".join(near_before)):
            continue
        claims.append(" ".join([*near_before, match.group(), *near_after]))
    return claims


class NoCoverageFigureTheGateDoesNotEnforce(unittest.TestCase):
    def test_no_public_page_claims_full_coverage_unless_it_is_enforced(self):
        """A "100%" claim is only true while `fail_under` makes it so."""
        if _fail_under() == 100:
            self.skipTest("fail_under = 100 enforces the claim")
        for path in PUBLIC_TEXT:
            with self.subTest(file=str(path.relative_to(REPO_ROOT))):
                found = full_coverage_claims(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    found,
                    [],
                    f"{path.name} claims full coverage, but fail_under is {_fail_under()}",
                )

    def test_the_check_refuses_project_claims_and_allows_module_ones(self):
        """Pin what counts as a coverage claim, both ways."""
        for claim in (
            "zero runtime dependencies, 100% test coverage.",
            "zero runtime dependencies, 100% coverage.",
            "100% coverage.",
            "coverage: 100%",
            "100% branch coverage",
            "100 % unit test coverage",
            "100 % total coverage",
            "Test coverage: 100%",
            "overall coverage is 100%",
            "every line is 100% covered",
            "`ai-jury` has 100% test coverage.",
            "Zero runtime dependencies (`subprocess`, `tomllib`), 100% coverage.",
        ):
            with self.subTest(claim=claim):
                self.assertNotEqual(full_coverage_claims(claim), [])
        for statement in (
            "`voting.py` has 100% coverage.",
            "`ballots` has 100% coverage.",
            "`make coverage` gate passing (theater.py 100%)",
            "the four-vendor panel caught 100% of them",
            "all at 100% precision. Coverage is on the badge.",
        ):
            with self.subTest(statement=statement):
                self.assertEqual(full_coverage_claims(statement), [])

    def test_pyproject_records_no_measured_total(self):
        """The comment above `fail_under` said ~99.95% while the suite measured 98.95%.

        A measured figure written into the file goes stale on the next commit; the
        badge and a local `make coverage` run are the measurement. Only a decimal
        percentage is refused, so the integer gate itself stays expressible.
        """
        text = PYPROJECT.read_text(encoding="utf-8")
        section = text[text.index("[tool.coverage.report]") :].split("\n[", 1)[0]
        self.assertIn("fail_under", section)
        self.assertEqual(re.findall(r"\d+\.\d+\s?%", section), [])


if __name__ == "__main__":
    unittest.main()
