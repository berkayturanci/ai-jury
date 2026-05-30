"""Tests for the structured finding schema and parser.

Run with: python -m unittest discover -s tests
No third-party dependencies, no live CLIs, no network.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_review_council.findings import (  # noqa: E402
    CONFIDENCES,
    SEVERITIES,
    SEVERITY_ORDER,
    Finding,
    parse_findings,
)

VALID_BLOCK = """Some markdown findings here.
- **[major]** `src/a.py:10` — boom

```json
[
  {"severity": "major", "file": "src/a.py", "line": 10, "claim": "boom",
   "evidence": "ev", "suggested_fix": "fix it", "confidence": "high",
   "reviewer": "ignored"}
]
```
"""


class FindingSchemaTest(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(SEVERITIES, ("critical", "major", "minor", "nit", "info"))
        self.assertEqual(CONFIDENCES, ("high", "medium", "low"))
        self.assertEqual(SEVERITY_ORDER["critical"], 0)
        self.assertLess(SEVERITY_ORDER["major"], SEVERITY_ORDER["minor"])

    def test_legacy_blocker_maps_to_critical(self):
        f = Finding(severity="blocker", file="x.py", claim="c")
        self.assertEqual(f.severity, "critical")

    def test_invalid_severity_coerced(self):
        f = Finding(severity="whatever", file="x.py", claim="c")
        self.assertEqual(f.severity, "info")

    def test_invalid_confidence_coerced(self):
        f = Finding(severity="major", file="x.py", claim="c", confidence="sure")
        self.assertEqual(f.confidence, "medium")

    def test_defaults(self):
        f = Finding(severity="major", file="x.py", claim="c")
        self.assertIsNone(f.line)
        self.assertEqual(f.evidence, "")
        self.assertEqual(f.suggested_fix, "")
        self.assertEqual(f.confidence, "medium")

    def test_line_coercion(self):
        self.assertEqual(Finding("major", "x.py", "c", line="42").line, 42)
        self.assertIsNone(Finding("major", "x.py", "c", line="nope").line)
        self.assertIsNone(Finding("major", "x.py", "c", line=True).line)


class ParseFindingsTest(unittest.TestCase):
    def test_valid_block_preserves_reviewer(self):
        findings, warnings = parse_findings(VALID_BLOCK, "claude")
        self.assertEqual(warnings, [])
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.severity, "major")
        self.assertEqual(f.file, "src/a.py")
        self.assertEqual(f.line, 10)
        self.assertEqual(f.claim, "boom")
        self.assertEqual(f.confidence, "high")
        # reviewer is forced to the passed-in name, not the JSON value.
        self.assertEqual(f.reviewer, "claude")

    def test_malformed_json_returns_warning_no_exception(self):
        text = "```json\n[ {not valid json } ]\n```"
        findings, warnings = parse_findings(text, "codex")
        self.assertEqual(findings, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("codex", warnings[0])
        self.assertIn("malformed", warnings[0])

    def test_missing_block_is_silent(self):
        findings, warnings = parse_findings("no json here, just prose", "agy")
        self.assertEqual(findings, [])
        self.assertEqual(warnings, [])

    def test_empty_array_is_no_findings_no_warning(self):
        findings, warnings = parse_findings("```json\n[]\n```", "claude")
        self.assertEqual(findings, [])
        self.assertEqual(warnings, [])

    def test_non_array_json_warns(self):
        findings, warnings = parse_findings('```json\n{"a": 1}\n```', "claude")
        self.assertEqual(findings, [])
        self.assertEqual(len(warnings), 1)

    def test_last_block_wins(self):
        text = (
            '```json\n[{"severity":"minor","file":"a","claim":"first"}]\n```\n'
            '```json\n[{"severity":"major","file":"b","claim":"second"}]\n```'
        )
        findings, warnings = parse_findings(text, "claude")
        self.assertEqual(warnings, [])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].claim, "second")

    def test_non_dict_item_warns_but_keeps_others(self):
        text = (
            '```json\n[42, {"severity":"major","file":"a","claim":"ok"}]\n```'
        )
        findings, warnings = parse_findings(text, "claude")
        self.assertEqual(len(findings), 1)
        self.assertEqual(len(warnings), 1)


if __name__ == "__main__":
    unittest.main()
