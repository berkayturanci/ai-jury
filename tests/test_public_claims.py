"""Claims the public text makes about the project must be read off the project.

Two drifts from the pre-launch audit of 2026-09-23:

- The site said `[jury.ci]` has "Exactly three keys" while `CiConfig` had four
  (#869). The count and the key names are now checked against the dataclass the
  validator itself reads (`config.KNOWN_CI_KEYS`), so adding a fifth key fails here
  until every page that counts them is corrected.
- The one-line description said four different things in `pyproject.toml` (the PyPI
  summary), the README, `llms.txt` and `llms-full.txt`, and two still told readers to
  install from git "until published" (#872). There is now one sentence, and every
  surface must carry it verbatim.

Stdlib only, like the rest of the suite.
"""

from __future__ import annotations

import html
import re
import tomllib
import unittest
from pathlib import Path

from ai_jury.config import KNOWN_CI_KEYS
from ai_jury.formats import JSON_SCHEMA_VERSION
from ai_jury.metadata import SCHEMA_VERSION as METADATA_SCHEMA_VERSION

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
SITE_INDEX = REPO_ROOT / "website" / "index.html"

NUMBER_WORDS = {
    word: n
    for n, word in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
            "twenty",
        ]
    )
}
#: A number word past the table ("thirty", "forty") must not be mistaken for "no
#: count stated": it fails loudly instead.
_UNLISTED_NUMBER = re.compile(r"(?i)(?:thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)\w*")

#: Every place that may count the `[jury.ci]` keys, and where the count would sit.
#: A page may drop the count (#869 allows it); only a number it does state is
#: checked. The names are pinned separately, by `test_the_site_names_every_key`.
CI_KEY_COUNTS: dict[str, re.Pattern[str]] = {
    "website/index.html": re.compile(r"<code>\[jury\.ci\]</code><span>(?:Exactly )?(\w+) keys:"),
    "llms.txt": re.compile(r"`\[jury\.ci\]` takes (\w+) keys"),
    "llms-full.txt": re.compile(r"\[jury\.ci\]\s+# exactly these (\w+) keys"),
}

#: Surfaces that carry the one-line description as a Markdown blockquote.
DESCRIPTION_BLOCKQUOTES = ("README.md", "llms.txt", "llms-full.txt", "website/llms.txt")


def _description() -> str:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["description"]


def _as_count(word: str) -> int | None:
    """The number a word states, or ``None`` when it states none."""
    if word.isdigit():
        return int(word)
    if word.lower() in NUMBER_WORDS:
        return NUMBER_WORDS[word.lower()]
    if _UNLISTED_NUMBER.fullmatch(word):
        raise AssertionError(f"{word!r} looks like a number NUMBER_WORDS does not list")
    return None


def _first_blockquote(path: Path) -> str:
    """The first run of `> ` lines in a file, joined into one line."""
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("> "):
            lines.append(line[2:].strip())
        elif lines:
            break
    return " ".join(lines)


class TheJuryCiKeyCountIsTheSchemas(unittest.TestCase):
    def test_every_stated_count_matches_the_config_schema(self):
        for rel, pattern in CI_KEY_COUNTS.items():
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            for word in pattern.findall(text):
                count = _as_count(word)
                if count is None:
                    continue  # "The keys:", say — no number is claimed
                with self.subTest(file=rel, stated=word):
                    self.assertEqual(
                        count,
                        len(KNOWN_CI_KEYS),
                        f"{rel} says [jury.ci] has {word} keys; the schema has "
                        f"{len(KNOWN_CI_KEYS)}: {', '.join(KNOWN_CI_KEYS)}",
                    )

    def test_a_page_may_drop_the_count(self):
        pattern = CI_KEY_COUNTS["website/index.html"]
        for entry, stated in (
            ("<code>[jury.ci]</code><span>The keys: ", None),
            ("<code>[jury.ci]</code><span>Exactly three keys: ", 3),
            ("<code>[jury.ci]</code><span>4 keys: ", 4),
            ("<code>[jury.ci]</code><span>Eight keys: ", 8),
        ):
            with self.subTest(entry=entry):
                self.assertEqual([_as_count(w) for w in pattern.findall(entry)], [stated])

    def test_an_unlisted_number_word_is_not_read_as_no_count(self):
        with self.assertRaises(AssertionError):
            _as_count("thirty")

    def test_the_site_names_every_key(self):
        text = SITE_INDEX.read_text(encoding="utf-8")
        start = text.index("<code>[jury.ci]</code>")
        entry = text[start : text.index("</div>", start)]
        named = set(re.findall(r"<code>(\w+)</code>", html.unescape(entry)))
        self.assertEqual(sorted(set(KNOWN_CI_KEYS) - named), [])


class OneDescriptionEverywhere(unittest.TestCase):
    def test_every_surface_carries_the_pypi_summary_verbatim(self):
        description = _description()
        for rel in DESCRIPTION_BLOCKQUOTES:
            with self.subTest(file=rel):
                self.assertEqual(_first_blockquote(REPO_ROOT / rel), description)

    def test_no_surface_still_waits_for_the_first_publish(self):
        """ai-jury is on PyPI; "once published; until then pipx install git+…" is stale."""
        for rel in ("README.md", "llms.txt", "llms-full.txt", "website/llms.txt"):
            with self.subTest(file=rel):
                text = (REPO_ROOT / rel).read_text(encoding="utf-8")
                self.assertNotIn("once published", text)
                self.assertNotIn("pipx install git+", text)


#: Every public surface that could state which report schema is current.
_SCHEMA_SURFACES = ("README.md", "action.yml", "llms.txt", "llms-full.txt")
_SCHEMA_SURFACE_GLOBS = ("docs/**/*.md", "website/**/*.html", "website/**/*.js", "website/*.txt")
#: "(currently `1.5`)" on a line that names a schema. Historical "since …" lines do
#: not say "currently" and are left alone: they are true forever.
_CURRENTLY = re.compile(r"schema[^\n]*?currently\s+`?([0-9][0-9.]*)`?", re.IGNORECASE)


def _schema_surfaces():
    paths = [REPO_ROOT / rel for rel in _SCHEMA_SURFACES]
    for pattern in _SCHEMA_SURFACE_GLOBS:
        paths.extend(sorted(REPO_ROOT.glob(pattern)))
    return [p for p in paths if p.is_file()]


class TheCurrentSchemaVersionIsTheCodes(unittest.TestCase):
    """A page saying which report schema is current reads it off the code (#905).

    The README said `1.4` after the JSON report moved to `1.5`. A dotted version is
    the JSON report's (`formats.JSON_SCHEMA_VERSION`), a whole number the run
    metadata's (`metadata.SCHEMA_VERSION`).
    """

    def test_every_currently_statement_matches(self):
        found = []
        for path in _schema_surfaces():
            text = path.read_text(encoding="utf-8")
            for match in _CURRENTLY.finditer(text):
                stated = match.group(1)
                expected = JSON_SCHEMA_VERSION if "." in stated else str(METADATA_SCHEMA_VERSION)
                found.append(path)
                with self.subTest(file=str(path.relative_to(REPO_ROOT)), stated=stated):
                    self.assertEqual(stated, expected)
        # The README's statement is the one known to exist; a regex that stops
        # finding it would pass every file vacuously.
        self.assertIn(REPO_ROOT / "README.md", found)

    def test_the_readme_states_the_json_schema(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            f"| `schema_version` | Version of this JSON schema (currently `{JSON_SCHEMA_VERSION}`). |",
            readme,
        )


if __name__ == "__main__":
    unittest.main()
