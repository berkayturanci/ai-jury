"""Guard the `.keel/project.yaml` snippets embedded in the docs.

Written after `docs/cookbook.md` §16 told readers to add a `review:` block to
`.keel/project.yaml` (#664). Keel's project schema has no such key —
`keel validate` rejects it with `unknown property 'review'` — so the cookbook
was teaching a config that could never validate. Nothing caught that because
the snippet lived in prose, not in anything the test suite looked at.

This does not shell out to `keel validate` (this project has no dependency on
Keel, live or dev), and it does not depend on PyYAML (zero runtime deps is a
hard constraint here too). Instead every fenced ```yaml block in the docs that
is explicitly marked as a Keel project file — its first line is the comment
`# .keel/project.yaml` — is checked two ways:

1. It parses under a small stdlib-only structural reader (consistent
   indentation, no tabs, well-formed `key:`/`- item` lines) — not full YAML,
   but enough to catch the syntax errors a reader would hit copy-pasting it.
2. Every *top-level* key it defines is a member of a vendored allowlist of
   Keel's actual top-level `project.yaml` keys. `review` is not on that list,
   so this is exactly the regression #664 needs pinned.

The allowlist is copied by hand from keel's
`src/keel/schema/project.schema.json` (top-level `properties`) rather than
imported, since this project does not depend on keel. Update it here if keel
adds or renames a top-level key and the cookbook is meant to use it.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

COOKBOOK = Path(__file__).resolve().parent.parent / "docs" / "cookbook.md"

#: Keel `project.yaml` top-level keys, as of keel's bundled JSON Schema
#: (`src/keel/schema/project.schema.json`, `properties`). This project does
#: not depend on keel, so the list is vendored by hand rather than imported.
KEEL_TOP_LEVEL_KEYS = {
    "extends",
    "core_version",
    "owner",
    "repo",
    "base_branch",
    "platform",
    "timezone",
    "merge_window",
    "merge_window_mode",
    "consent_mode",
    "knobs",
    "gates",
    "extensions",
    "extensions_dir",
    "policy_pack",
    "automation",
}

#: A block is a Keel project-file snippet only if its first content line is
#: exactly this comment — anything else fenced as ```yaml (a `jury.toml`-ish
#: example, a GitHub Actions step, etc.) is out of scope for this check.
KEEL_PROJECT_MARKER = "# .keel/project.yaml"

_FENCE_RE = re.compile(r"```yaml\n(.*?)```", re.DOTALL)
_TOP_LEVEL_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(?:\s|$)")
_INDENTED_LINE_RE = re.compile(r"^( +)\S")


def extract_keel_project_yaml_blocks(markdown_text: str) -> list[str]:
    """Return every fenced ```yaml block whose first line marks it as a
    `.keel/project.yaml` snippet, with that marker comment stripped."""
    blocks = []
    for match in _FENCE_RE.finditer(markdown_text):
        body = match.group(1)
        lines = body.splitlines()
        if not lines or lines[0].strip() != KEEL_PROJECT_MARKER:
            continue
        blocks.append("\n".join(lines[1:]))
    return blocks


def parse_top_level_keys(yaml_snippet: str) -> list[str]:
    """A minimal, stdlib-only structural read of a `key: value` mapping.

    Not a general YAML parser. It only needs to (a) reject the syntax errors
    that would also break a real YAML parser, and (b) recover the top-level
    keys so they can be checked against the allowlist. Raises ``ValueError``
    on anything that looks malformed (a tab, a non-2-space indent step, a
    line with no recognizable shape).
    """
    top_level_keys: list[str] = []
    for raw_line in yaml_snippet.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if "\t" in raw_line:
            raise ValueError(f"tab character in YAML line: {raw_line!r}")

        indent_match = _INDENTED_LINE_RE.match(line)
        if indent_match:
            if len(indent_match.group(1)) % 2 != 0:
                raise ValueError(f"odd indentation (not a 2-space step): {line!r}")
            content = line.strip()
            if content.startswith("- "):
                continue  # list item under a top-level `key:` sequence
            if _TOP_LEVEL_KEY_RE.match(content) is None and ":" not in content:
                raise ValueError(f"unrecognized indented line: {line!r}")
            continue  # nested `subkey: value` under a top-level mapping

        top_match = _TOP_LEVEL_KEY_RE.match(line)
        if top_match is None:
            raise ValueError(f"unrecognized top-level line: {line!r}")
        top_level_keys.append(top_match.group(1))
    return top_level_keys


class CookbookKeelSnippets(unittest.TestCase):
    def setUp(self):
        self.markdown_text = COOKBOOK.read_text(encoding="utf-8")
        self.blocks = extract_keel_project_yaml_blocks(self.markdown_text)

    def test_there_is_at_least_one_keel_project_yaml_snippet(self):
        """Vacuity: if the marker line ever moves or is reworded, the blocks
        below silently stop being checked at all."""
        self.assertGreater(
            len(self.blocks),
            0,
            f"no fenced ```yaml block in {COOKBOOK} starts with {KEEL_PROJECT_MARKER!r}",
        )

    def test_snippets_parse_as_well_formed_yaml_mappings(self):
        for block in self.blocks:
            try:
                parse_top_level_keys(block)
            except ValueError as exc:
                self.fail(f"snippet does not parse as YAML: {exc}\n---\n{block}")

    def test_snippets_use_only_known_keel_top_level_keys(self):
        for block in self.blocks:
            keys = parse_top_level_keys(block)
            unknown = sorted(set(keys) - KEEL_TOP_LEVEL_KEYS)
            self.assertEqual(
                unknown,
                [],
                "snippet uses top-level key(s) keel's schema does not define: "
                f"{unknown} (keel validate would fail with "
                f"\"unknown property '{unknown[0] if unknown else ''}'\")\n"
                f"---\n{block}",
            )

    def test_review_key_regression_is_pinned(self):
        """The literal bug from #664: a `review:` block is not valid keel
        config. Written directly (not just "not in the allowlist") so a
        future rewrite of the allowlist can't accidentally re-admit it."""
        for block in self.blocks:
            self.assertNotIn(
                "review",
                parse_top_level_keys(block),
                "a .keel/project.yaml snippet uses the invalid `review:` key "
                "(#664) — keel's schema has no such top-level property",
            )


if __name__ == "__main__":
    unittest.main()
