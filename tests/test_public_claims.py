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

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
SITE_INDEX = REPO_ROOT / "website" / "index.html"

NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}

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
    return int(word) if word.isdigit() else NUMBER_WORDS.get(word.lower())


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
        ):
            with self.subTest(entry=entry):
                self.assertEqual([_as_count(w) for w in pattern.findall(entry)], [stated])

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


if __name__ == "__main__":
    unittest.main()
