"""Tests for machine-readable JSON and SARIF renderers."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_jury import __version__  # noqa: E402
from ai_jury.adapters import AgentResult  # noqa: E402
from ai_jury.ballots import (  # noqa: E402
    ABSTAIN,
    NOT_STATED,
    chair_verdict,
    describe_scope,
    describe_testing,
    keel_reviews,
    normalize_verdict,
    reviewer_ballots,
)
from ai_jury.classification import classify  # noqa: E402
from ai_jury.cli import main as cli_main  # noqa: E402
from ai_jury.config import load_config  # noqa: E402
from ai_jury.consensus import FindingGroup  # noqa: E402
from ai_jury.findings import Finding  # noqa: E402
from ai_jury.formats import (  # noqa: E402
    JSON_SCHEMA_VERSION,
    SARIF_SCHEMA,
    SARIF_VERSION,
    severity_to_sarif_level,
    to_json,
    to_keel_reviews,
    to_sarif,
)
from ai_jury.metadata import build_run_metadata  # noqa: E402
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

    def test_region_dropped_for_nonpositive_line(self):
        # A reviewer's structured output is attacker-influenced; a forged
        # non-positive ``line`` must NOT emit an invalid SARIF region (which
        # would make GitHub code-scanning reject the whole upload). The region
        # is dropped; the finding still surfaces at file level.
        for bad_line in (0, -5):
            finding = Finding(severity="major", file="a.py", claim="forged", line=bad_line)
            group = FindingGroup(representative=finding, members=[finding])

            class FakeOutcome:
                findings = [finding]
                groups = [group]
                verdicts = []
                synthesis = None

            config = load_config(None)
            result = json.loads(to_sarif(FakeOutcome(), config))["runs"][0]["results"][0]
            phys = result["locations"][0]["physicalLocation"]
            self.assertNotIn("region", phys, f"line={bad_line} must not emit a region")
            self.assertEqual(phys["artifactLocation"]["uri"], "a.py")

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


# --------------------------------------------------------------------------
# Per-reviewer ballots (issue #663)
# --------------------------------------------------------------------------

#: A three-seat panel across TWO vendors — the shape the deliverable names, and
#: the one the default config does not have (it is three seats across three
#: vendors, which cannot tell "one entry per seat" apart from "one per vendor").
PANEL_TOML = """
[jury]
rounds = 2
chair = "alpha"
verify = true

[[agent]]
name = "alpha"
vendor = "acme"
command = "alpha"
model = "acme-1"

[[agent]]
name = "beta"
vendor = "acme"
command = "beta"
model = "acme-2"

[[agent]]
name = "gamma"
vendor = "globex"
command = "gamma"
"""


def _panel_outcome(diff: str = SAMPLE_DIFF):
    """Run the real pipeline over the two-vendor fixture panel, mock adapters only."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "jury.toml"
        path.write_text(PANEL_TOML, encoding="utf-8")
        config = load_config(path)
    outcome = run_jury(config, diff, mock=True, log=lambda _m: None)
    return outcome, config


# --- vendored consumer contract -------------------------------------------
#
# A minimal, deliberately independent re-statement of keel's ``parse_reviews``
# rules (keel/src/keel/review.py). Vendored rather than imported: ai-jury ships
# with zero runtime dependencies and the test suite must pass with keel absent.
# The point of the copy is that it is written from the *contract*, so a change
# to ai-jury's renderer that quietly stops satisfying the consumer fails here
# instead of at the consumer.


class ReviewContractError(Exception):
    """Raised by the vendored validator, mirroring keel's ``ReviewError``."""


def parse_reviews_contract(raw):
    """Validate a ``--reviews`` payload the way keel's ``parse_reviews`` does."""
    if not isinstance(raw, list):
        raise ReviewContractError("reviews file must contain a JSON array of review objects")
    items = []
    for index, entry in enumerate(raw):
        n = index + 1
        if not isinstance(entry, dict):
            raise ReviewContractError(f"review #{n} must be a JSON object")
        reviewer = entry.get("reviewer")
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise ReviewContractError(f"review #{n} requires a non-empty 'reviewer' string")
        verdict = entry.get("verdict")
        if not isinstance(verdict, str) or not verdict.strip():
            raise ReviewContractError(f"review #{n} requires a non-empty 'verdict' string")
        for optional in ("scope", "testing", "vendor", "model"):
            value = entry.get(optional)
            if value is not None and not isinstance(value, str):
                raise ReviewContractError(f"review #{n} '{optional}' must be a string when present")
        findings = entry.get("findings")
        if findings is not None:
            if not isinstance(findings, list):
                raise ReviewContractError(f"review #{n} 'findings' must be a list when present")
            for f_index, finding in enumerate(findings):
                if not isinstance(finding, dict):
                    raise ReviewContractError(
                        f"review #{n} finding #{f_index + 1} must be a JSON object"
                    )
        items.append(entry)
    return tuple(items)


class TheVendoredContractActuallyRejects(unittest.TestCase):
    """A validator that accepts everything proves nothing about the renderer."""

    def test_rejects_non_array(self):
        with self.assertRaises(ReviewContractError):
            parse_reviews_contract({"reviewer": "a", "verdict": "APPROVE"})

    def test_rejects_missing_reviewer_and_verdict(self):
        with self.assertRaises(ReviewContractError):
            parse_reviews_contract([{"verdict": "APPROVE"}])
        with self.assertRaises(ReviewContractError):
            parse_reviews_contract([{"reviewer": "a"}])
        with self.assertRaises(ReviewContractError):
            parse_reviews_contract([{"reviewer": "   ", "verdict": "APPROVE"}])

    def test_rejects_wrong_shapes(self):
        with self.assertRaises(ReviewContractError):
            parse_reviews_contract(["not an object"])
        with self.assertRaises(ReviewContractError):
            parse_reviews_contract([{"reviewer": "a", "verdict": "APPROVE", "scope": 7}])
        with self.assertRaises(ReviewContractError):
            parse_reviews_contract([{"reviewer": "a", "verdict": "APPROVE", "findings": {}}])
        with self.assertRaises(ReviewContractError):
            parse_reviews_contract([{"reviewer": "a", "verdict": "APPROVE", "findings": ["x"]}])

    def test_accepts_a_minimal_valid_record(self):
        self.assertEqual(len(parse_reviews_contract([{"reviewer": "a", "verdict": "APPROVE"}])), 1)


class ReviewersArray(unittest.TestCase):
    def test_three_ballots_plus_chair(self):
        outcome, config = _panel_outcome()
        doc = json.loads(to_json(outcome, config))
        entries = doc["reviewers"]
        self.assertEqual([e["name"] for e in entries], ["alpha", "beta", "gamma", "chair"])
        self.assertEqual([e.get("role") for e in entries], [None, None, None, "chair"])

    def test_ballots_carry_vendor_and_effective_model(self):
        outcome, config = _panel_outcome()
        entries = json.loads(to_json(outcome, config))["reviewers"]
        by_name = {e["name"]: e for e in entries}
        self.assertEqual(by_name["alpha"]["vendor"], "acme")
        self.assertEqual(by_name["alpha"]["model"], "acme-1")
        self.assertEqual(by_name["beta"]["vendor"], "acme")
        self.assertEqual(by_name["beta"]["model"], "acme-2")
        self.assertEqual(by_name["gamma"]["vendor"], "globex")
        # gamma configures no model, so the CLI default is in force and there is
        # no id to report — an empty string, not an invented one.
        self.assertEqual(by_name["gamma"]["model"], "")
        # The chair's provenance is its own agent slot's, resolved by name.
        self.assertEqual(by_name["chair"]["vendor"], "acme")
        self.assertEqual(by_name["chair"]["model"], "acme-1")

    def test_two_vendors_across_three_seats(self):
        # The count that matters to a cross-vendor consumer: three ballots do not
        # mean three independent perspectives.
        entries = json.loads(to_json(*_panel_outcome()))["reviewers"]
        vendors = {e["vendor"] for e in entries if e.get("role") != "chair"}
        self.assertEqual(vendors, {"acme", "globex"})

    def test_finding_indexes_point_at_that_reviewers_findings(self):
        outcome, config = _panel_outcome()
        doc = json.loads(to_json(outcome, config))
        findings = doc["findings"]
        for entry in doc["reviewers"]:
            if entry.get("role") == "chair":
                continue
            self.assertTrue(entry["findings"], f"{entry['name']} raised nothing in the fixture")
            for index in entry["findings"]:
                self.assertEqual(findings[index]["reviewer"], entry["name"])

    def test_operational_fields(self):
        outcome, config = _panel_outcome()
        for entry in json.loads(to_json(outcome, config))["reviewers"]:
            if entry.get("role") == "chair":
                continue
            self.assertTrue(entry["round1_ok"])
            self.assertIsInstance(entry["verified_count"], int)
            self.assertGreaterEqual(entry["verified_count"], 1)
            self.assertIsInstance(entry["duration_s"], float)

    def test_verdicts_are_single_machine_tokens(self):
        entries = json.loads(to_json(*_panel_outcome()))["reviewers"]
        for entry in entries:
            self.assertNotIn(" ", entry["verdict"])
            self.assertIn(entry["verdict"], {"APPROVE", "COMMENT", "REQUEST_CHANGES", ABSTAIN})

    def test_issue_mode_uses_the_issue_vocabulary(self):
        outcome, config = _panel_outcome()
        entries = json.loads(to_json(outcome, config, mode="issue"))["reviewers"]
        panel = [e["verdict"] for e in entries if e.get("role") != "chair"]
        self.assertTrue(panel)
        for verdict in panel:
            self.assertIn(verdict, {"READY", "UNCLEAR", "NEEDS_INFO", ABSTAIN})

    def test_deterministic_across_two_runs(self):
        self.assertEqual(to_json(*_panel_outcome()), to_json(*_panel_outcome()))

    def test_seeded_secret_does_not_leak_into_ballots(self):
        diff = SAMPLE_DIFF + f"+API_KEY = '{FAKE_SECRET}'\n"
        outcome, config = _panel_outcome(diff)
        self.assertNotIn(FAKE_SECRET, json.dumps(json.loads(to_json(outcome, config))["reviewers"]))


class BackwardCompatibility(unittest.TestCase):
    """`reviewers` is additive. An existing consumer must read the same document."""

    #: Every top-level key the 1.0 report carried, and the key order it used.
    LEGACY_KEYS = [
        "schema_version",
        "metadata",
        "classification",
        "findings",
        "consensus",
        "verdicts",
        "verdict",
    ]

    def test_every_legacy_key_survives_in_its_original_order(self):
        doc = json.loads(to_json(*_mock_outcome()))
        self.assertEqual([k for k in doc if k in self.LEGACY_KEYS], self.LEGACY_KEYS)

    def test_only_reviewers_was_added(self):
        doc = json.loads(to_json(*_mock_outcome()))
        self.assertEqual(set(doc) - set(self.LEGACY_KEYS), {"reviewers"})

    def test_legacy_sections_are_byte_identical_without_the_new_key(self):
        # The strongest form of "no change": drop ``reviewers`` and the document
        # must be exactly what the previous renderer emitted, field for field.
        outcome, config = _mock_outcome()
        doc = json.loads(to_json(outcome, config))
        doc.pop("reviewers")
        legacy = {
            "schema_version": doc["schema_version"],
            "metadata": build_run_metadata(outcome, config),
            "classification": classify(outcome),
            "findings": [
                {
                    "severity": f.severity,
                    "file": f.file,
                    "line": f.line,
                    "claim": f.claim,
                    "evidence": f.evidence,
                    "suggested_fix": f.suggested_fix,
                    "confidence": f.confidence,
                    "reviewer": f.reviewer,
                }
                for f in outcome.findings
            ],
            "consensus": doc["consensus"],
            "verdicts": doc["verdicts"],
            "verdict": doc["verdict"],
        }
        legacy["metadata"].pop("generated_at", None)
        self.assertEqual(doc, legacy)

    def test_sarif_is_untouched_by_the_ballots(self):
        outcome, config = _mock_outcome()
        sarif = json.loads(to_sarif(outcome, config))
        self.assertNotIn("reviewers", sarif)
        self.assertNotIn("reviewers", json.dumps(sarif))


class KeelReviewsBundle(unittest.TestCase):
    def test_bundle_satisfies_the_consumer_contract(self):
        outcome, config = _panel_outcome()
        items = parse_reviews_contract(json.loads(to_keel_reviews(outcome, config)))
        self.assertEqual(len(items), 4)

    def test_one_record_per_panelist_plus_the_chair_last(self):
        outcome, config = _panel_outcome()
        records = json.loads(to_keel_reviews(outcome, config))
        self.assertEqual([r["reviewer"] for r in records], ["alpha", "beta", "gamma", "chair"])

    def test_findings_are_remapped_to_path_and_message(self):
        outcome, config = _panel_outcome()
        records = json.loads(to_keel_reviews(outcome, config))
        alpha = records[0]
        self.assertTrue(alpha["findings"])
        for finding in alpha["findings"]:
            self.assertEqual(set(finding), {"severity", "path", "line", "message"})
            self.assertTrue(finding["path"])
            self.assertTrue(finding["message"])
            # The consumer's own field names, not ai-jury's.
            self.assertNotIn("file", finding)
            self.assertNotIn("claim", finding)

    def test_scope_names_the_files_the_panelist_covered(self):
        outcome, config = _panel_outcome()
        records = json.loads(to_keel_reviews(outcome, config))
        self.assertIn("src/example.py", records[0]["scope"])
        self.assertIn("panel review(s)", records[-1]["scope"])

    def test_provenance_rides_along(self):
        outcome, config = _panel_outcome()
        by_name = {r["reviewer"]: r for r in json.loads(to_keel_reviews(outcome, config))}
        self.assertEqual(by_name["alpha"]["vendor"], "acme")
        self.assertEqual(by_name["alpha"]["model"], "acme-1")
        self.assertEqual(by_name["gamma"]["vendor"], "globex")

    def test_chair_reports_the_surviving_evidence_only(self):
        outcome, config = _panel_outcome()
        chair = json.loads(to_keel_reviews(outcome, config))[-1]
        rejected = {
            g.representative.claim for g in outcome.groups if (g.status or "") == "unsupported"
        }
        self.assertTrue(rejected, "the fixture must produce at least one rejected group")
        for finding in chair["findings"]:
            self.assertNotIn(finding["message"], rejected)

    def test_deterministic_and_leaks_no_secret(self):
        diff = SAMPLE_DIFF + f"+API_KEY = '{FAKE_SECRET}'\n"
        first = to_keel_reviews(*_panel_outcome(diff))
        second = to_keel_reviews(*_panel_outcome(diff))
        self.assertEqual(first, second)
        self.assertNotIn(FAKE_SECRET, first)

    def test_cli_writes_the_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            diff = d / "x.diff"
            diff.write_text(SAMPLE_DIFF, encoding="utf-8")
            out = d / "reviews.json"
            code = cli_main(
                [
                    "--mock",
                    "--diff-file",
                    str(diff),
                    "-q",
                    "--format",
                    "keel-reviews",
                    "-o",
                    str(out),
                ]
            )
            self.assertEqual(code, 0)
            items = parse_reviews_contract(json.loads(out.read_text(encoding="utf-8")))
            self.assertEqual(items[-1]["reviewer"], "chair")


class BallotDerivation(unittest.TestCase):
    """The pure derivation, exercised on hand-built results."""

    @staticmethod
    def _config():
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jury.toml"
            path.write_text(PANEL_TOML, encoding="utf-8")
            return load_config(path)

    # NB: not ``_outcome`` — ``unittest.TestCase`` sets an instance attribute
    # by that exact name while running, which shadows a class-level helper.
    @staticmethod
    def _fake_outcome(reviews, **kwargs):
        class FakeOutcome:
            pass

        o = FakeOutcome()
        o.reviews = reviews
        o.groups = kwargs.get("groups", [])
        o.findings = kwargs.get("findings", [])
        o.synthesis = kwargs.get("synthesis")
        o.verify = kwargs.get("verify")
        o.chair = kwargs.get("chair", "alpha")
        return o

    def test_normalize_verdict_folds_spaces_and_hyphens(self):
        self.assertEqual(normalize_verdict("REQUEST CHANGES"), "REQUEST_CHANGES")
        self.assertEqual(normalize_verdict("NEEDS-INFO"), "NEEDS_INFO")
        self.assertEqual(normalize_verdict("NO QUORUM"), "NO_QUORUM")
        self.assertEqual(normalize_verdict("approve"), "APPROVE")
        self.assertEqual(normalize_verdict(""), "")

    def test_a_silent_slot_is_not_a_ballot(self):
        silent = AgentResult(agent="alpha", vendor="acme", ok=True, output="   ", duration_s=0.0)
        entries = reviewer_ballots(self._fake_outcome([silent]), self._config())
        self.assertEqual([e["name"] for e in entries], ["chair"])

    def test_a_refusal_abstains_rather_than_approving(self):
        # The #251 property, restated for ballots: a slot that declined to review
        # must never be rendered as the clear stance.
        refusal = AgentResult(
            agent="alpha",
            vendor="acme",
            ok=True,
            output="I cannot assist with that request.",
            duration_s=1.0,
        )
        entry = reviewer_ballots(self._fake_outcome([refusal]), self._config())[0]
        self.assertEqual(entry["verdict"], ABSTAIN)
        self.assertTrue(entry["round1_ok"])

    def test_a_failed_adapter_with_stdout_is_recorded_and_abstains(self):
        # Adapters fail soft: a nonzero exit can still carry a review. The slot
        # is listed (there IS output to attribute) but does not vote.
        failed = AgentResult(
            agent="beta",
            vendor="acme",
            ok=False,
            output="partial review text",
            duration_s=2.5,
        )
        entry = reviewer_ballots(self._fake_outcome([failed]), self._config())[0]
        self.assertEqual(entry["name"], "beta")
        self.assertFalse(entry["round1_ok"])
        self.assertEqual(entry["verdict"], ABSTAIN)
        self.assertEqual(entry["duration_s"], 2.5)

    def test_a_clean_reviewer_approves(self):
        clean = AgentResult(
            agent="alpha",
            vendor="acme",
            ok=True,
            output="Nothing blocking. I examined the parser changes end to end.",
            duration_s=0.0,
        )
        entry = reviewer_ballots(self._fake_outcome([clean]), self._config())[0]
        self.assertEqual(entry["verdict"], "APPROVE")
        self.assertEqual(entry["findings"], [])
        self.assertEqual(entry["verified_count"], 0)

    def test_unknown_agent_name_yields_empty_provenance(self):
        stranger = AgentResult(agent="delta", vendor="", ok=True, output="a review", duration_s=0.0)
        entry = reviewer_ballots(self._fake_outcome([stranger], chair="nobody"), self._config())
        self.assertEqual(entry[0]["vendor"], "")
        self.assertEqual(entry[0]["model"], "")
        self.assertEqual(entry[-1]["vendor"], "")
        self.assertEqual(entry[-1]["model"], "")

    def test_chair_verdict_prefers_the_vote(self):
        class FakeVote:
            verdict = "REQUEST CHANGES"

        self.assertEqual(chair_verdict(self._fake_outcome([]), FakeVote()), "REQUEST_CHANGES")

    def test_chair_verdict_lifts_the_synthesis_label_without_the_sentence(self):
        synthesis = AgentResult(
            agent="alpha",
            vendor="acme",
            ok=True,
            output="## Verdict\nAPPROVE — nothing blocking was found.\n\n## Notes\n- fine",
            duration_s=0.0,
        )
        self.assertEqual(chair_verdict(self._fake_outcome([], synthesis=synthesis)), "APPROVE")

    def test_chair_abstains_without_a_synthesis(self):
        self.assertEqual(chair_verdict(self._fake_outcome([])), ABSTAIN)
        failed = AgentResult(agent="alpha", vendor="acme", ok=False, output="", duration_s=0.0)
        self.assertEqual(chair_verdict(self._fake_outcome([], synthesis=failed)), ABSTAIN)

    def test_chair_scope_reports_an_empty_panel(self):
        record = keel_reviews(self._fake_outcome([]), self._config())[-1]
        self.assertIn("0 panel review(s)", record["scope"])
        self.assertIn("no specific file", record["scope"])


class ScopeAndTestingProse(unittest.TestCase):
    @staticmethod
    def _result(output):
        return AgentResult(agent="alpha", vendor="acme", ok=True, output=output, duration_s=0.0)

    def test_scope_without_files_states_the_reviewers_actual_position(self):
        scope = describe_scope(self._result("Looks fine."), [])
        self.assertIn("named no specific file", scope)

    def test_scope_lists_files_and_truncates_a_long_list(self):
        findings = [Finding(severity="nit", file=f"f{i}.py", claim="c") for i in range(11)]
        scope = describe_scope(self._result(""), findings)
        self.assertIn("Named 11 file(s)", scope)
        self.assertIn("f0.py", scope)
        self.assertIn("(+3 more)", scope)
        self.assertNotIn("f8.py", scope)

    def test_scope_folds_in_coverage_clauses(self):
        scope = describe_scope(
            self._result("I checked the error paths.\nI examined the new parser."),
            [],
        )
        self.assertIn("I checked the error paths.", scope)
        self.assertIn("I examined the new parser.", scope)

    def test_scope_caps_the_number_of_coverage_clauses(self):
        # An attacker-influenced reply must not be able to make the scope
        # arbitrarily long by repeating coverage-shaped sentences.
        output = "\n".join(f"I checked area {i}." for i in range(9))
        scope = describe_scope(self._result(output), [])
        self.assertIn("I checked area 2.", scope)
        self.assertNotIn("I checked area 3.", scope)

    def test_unchecked_is_not_a_coverage_claim(self):
        # A word-boundary match, not a substring one: the panel's own house
        # phrasing is "unchecked return value", and folding that into the
        # coverage summary would attribute a check the reviewer never made.
        scope = describe_scope(self._result("The unchecked return value swallows an error."), [])
        self.assertNotIn("unchecked", scope)

    def test_fenced_structured_findings_are_not_lifted_as_prose(self):
        output = (
            "I checked the parser.\n"
            "```json\n"
            '[{"claim": "reviewed nothing at all, checked nothing"}]\n'
            "```\n"
        )
        scope = describe_scope(self._result(output), [])
        self.assertIn("I checked the parser.", scope)
        self.assertNotIn("reviewed nothing at all", scope)

    def test_an_unterminated_fence_swallows_the_rest(self):
        scope = describe_scope(self._result("```\nI checked everything."), [])
        self.assertNotIn("I checked everything.", scope)

    def test_clauses_are_length_capped(self):
        scope = describe_scope(self._result("I checked " + "x" * 5000), [])
        self.assertLess(len(scope), 600)

    def test_testing_is_lifted_verbatim(self):
        self.assertEqual(
            describe_testing(self._result("No blockers.\nI ran the tests locally; all green.")),
            "I ran the tests locally; all green.",
        )

    def test_testing_falls_back_to_not_stated(self):
        self.assertEqual(describe_testing(self._result("Looks fine.")), NOT_STATED)
        self.assertEqual(describe_testing(self._result("")), NOT_STATED)


if __name__ == "__main__":
    unittest.main()
