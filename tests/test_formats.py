"""Tests for machine-readable JSON and SARIF renderers."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_jury import __version__  # noqa: E402
from ai_jury.config import load_config  # noqa: E402
from ai_jury.consensus import FindingGroup  # noqa: E402
from ai_jury.findings import Finding  # noqa: E402
from ai_jury.formats import (  # noqa: E402
    JSON_SCHEMA_VERSION,
    SARIF_SCHEMA,
    SARIF_VERSION,
    severity_to_sarif_level,
    to_json,
    to_sarif,
)
from ai_jury.orchestrator import run_jury  # noqa: E402

SARIF_LEVELS = {"error", "warning", "note"}

# A secret that, if it ever leaked from agent output, must not reach a report.
FAKE_SECRET = "sk-SEEDED-FAKE-SECRET-DO-NOT-LEAK-0123456789"

# Mirrors the mock pipeline's keyed diff (see tests/test_cli_contract.py).
SAMPLE_DIFF = (
    "diff --git a/src/example.py b/src/example.py\n"
    "@@ -1,3 +1,4 @@\n"
    "+def parse(x):\n"
    "+    return int(x)\n"
)


def _mock_outcome(diff: str = SAMPLE_DIFF):
    config = load_config(None)
    outcome = run_jury(config, diff, mock=True, log=lambda _m: None)
    return outcome, config


class TestJSON(unittest.TestCase):
    def test_parses_and_has_required_keys(self):
        outcome, config = _mock_outcome()
        doc = json.loads(to_json(outcome, config))
        for key in ("schema_version", "metadata", "findings", "consensus", "verdicts", "verdict"):
            self.assertIn(key, doc)
        self.assertEqual(doc["schema_version"], JSON_SCHEMA_VERSION)
        self.assertIsInstance(doc["metadata"], dict)
        self.assertIn("agents", doc["metadata"])

    def test_findings_carry_severity_location_claim(self):
        outcome, config = _mock_outcome()
        self.assertTrue(outcome.findings, "mock pipeline should report findings")
        doc = json.loads(to_json(outcome, config))
        self.assertTrue(doc["findings"])
        for f in doc["findings"]:
            for key in (
                "severity",
                "file",
                "line",
                "claim",
                "evidence",
                "suggested_fix",
                "confidence",
                "reviewer",
            ):
                self.assertIn(key, f)
            self.assertTrue(f["severity"])
            self.assertTrue(f["file"])
            self.assertTrue(f["claim"])

    def test_consensus_and_verdicts_present(self):
        outcome, config = _mock_outcome()
        doc = json.loads(to_json(outcome, config))
        self.assertIsInstance(doc["consensus"], list)
        self.assertIsInstance(doc["verdicts"], list)
        self.assertTrue(doc["consensus"], "mock pipeline should produce consensus groups")
        for g in doc["consensus"]:
            self.assertIn("representative", g)
            self.assertIn("agreement", g)
            self.assertIn("verification_status", g)
            self.assertIsInstance(g["agreement"], int)

    def test_deterministic_across_two_runs(self):
        out1, cfg1 = _mock_outcome()
        out2, cfg2 = _mock_outcome()
        self.assertEqual(to_json(out1, cfg1), to_json(out2, cfg2))

    def test_seeded_secret_does_not_leak(self):
        diff = SAMPLE_DIFF + f"+API_KEY = '{FAKE_SECRET}'\n"
        outcome, config = _mock_outcome(diff)
        self.assertNotIn(FAKE_SECRET, to_json(outcome, config))


class TestSARIF(unittest.TestCase):
    def test_parses_and_top_level(self):
        outcome, config = _mock_outcome()
        doc = json.loads(to_sarif(outcome, config))
        self.assertEqual(doc["version"], "2.1.0")
        self.assertEqual(doc["version"], SARIF_VERSION)
        self.assertEqual(doc["$schema"], SARIF_SCHEMA)
        self.assertEqual(len(doc["runs"]), 1)

    def test_driver_metadata(self):
        outcome, config = _mock_outcome()
        driver = json.loads(to_sarif(outcome, config))["runs"][0]["tool"]["driver"]
        self.assertEqual(driver["name"], "ai-jury")
        self.assertEqual(driver["version"], __version__)
        self.assertIn("rules", driver)
        self.assertTrue(driver["rules"], "rules should be present for used severities")
        for rule in driver["rules"]:
            self.assertTrue(rule["id"].startswith("jury/"))

    def test_results_shape(self):
        outcome, config = _mock_outcome()
        results = json.loads(to_sarif(outcome, config))["runs"][0]["results"]
        self.assertTrue(results)
        for r in results:
            self.assertTrue(r["ruleId"].startswith("jury/"))
            self.assertIn(r["level"], SARIF_LEVELS)
            self.assertTrue(r["message"]["text"])
            phys = r["locations"][0]["physicalLocation"]
            self.assertTrue(phys["artifactLocation"]["uri"])

    def test_region_present_when_line_set(self):
        outcome, config = _mock_outcome()
        result = json.loads(to_sarif(outcome, config))["runs"][0]["results"][0]
        rep = outcome.groups[0].representative
        phys = result["locations"][0]["physicalLocation"]
        if rep.line is not None:
            self.assertEqual(phys["region"]["startLine"], rep.line)
        else:
            self.assertNotIn("region", phys)

    def test_region_omitted_when_line_none(self):
        finding = Finding(severity="minor", file="a.py", claim="no line", line=None)
        group = FindingGroup(representative=finding, members=[finding])

        class FakeOutcome:
            findings = [finding]
            groups = [group]
            verdicts = []
            synthesis = None

        config = load_config(None)
        result = json.loads(to_sarif(FakeOutcome(), config))["runs"][0]["results"][0]
        phys = result["locations"][0]["physicalLocation"]
        self.assertNotIn("region", phys)
        self.assertEqual(phys["artifactLocation"]["uri"], "a.py")
        self.assertEqual(result["ruleId"], "jury/minor")
        self.assertEqual(result["level"], "warning")

    def test_level_mapping(self):
        self.assertEqual(severity_to_sarif_level("critical"), "error")
        self.assertEqual(severity_to_sarif_level("major"), "error")
        self.assertEqual(severity_to_sarif_level("minor"), "warning")
        self.assertEqual(severity_to_sarif_level("nit"), "note")
        self.assertEqual(severity_to_sarif_level("info"), "note")
        self.assertEqual(severity_to_sarif_level("bogus"), "note")

    def test_deterministic_across_two_runs(self):
        out1, cfg1 = _mock_outcome()
        out2, cfg2 = _mock_outcome()
        self.assertEqual(to_sarif(out1, cfg1), to_sarif(out2, cfg2))

    def test_seeded_secret_does_not_leak(self):
        diff = SAMPLE_DIFF + f"+API_KEY = '{FAKE_SECRET}'\n"
        outcome, config = _mock_outcome(diff)
        self.assertNotIn(FAKE_SECRET, to_sarif(outcome, config))


if __name__ == "__main__":
    unittest.main()
