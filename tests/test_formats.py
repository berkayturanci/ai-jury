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
    MODEL_CLI_DEFAULT,
    MODEL_NONE,
    MODEL_REQUESTED,
    MODEL_UNKNOWN,
    NOT_STATED,
    chair_verdict,
    describe_scope,
    describe_testing,
    keel_reviews,
    normalize_verdict,
    requested_model,
    reviewer_ballots,
    scope_is_substantive,
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
        self.assertEqual(
            [e.get("role") for e in entries],
            ["panelist", "panelist", "panelist", "chair"],
        )

    def test_ballots_carry_vendor_and_effective_model(self):
        outcome, config = _panel_outcome()
        entries = json.loads(to_json(outcome, config))["reviewers"]
        by_name = {e["name"]: e for e in entries}
        self.assertEqual(by_name["alpha"]["vendor"], "acme")
        self.assertEqual(by_name["alpha"]["model"], "acme-1")
        self.assertEqual(by_name["alpha"]["model_source"], MODEL_REQUESTED)
        self.assertEqual(by_name["beta"]["vendor"], "acme")
        self.assertEqual(by_name["beta"]["model"], "acme-2")
        self.assertEqual(by_name["gamma"]["vendor"], "globex")
        # gamma configures no model, so the CLI's own default answered. The field
        # SAYS that (#700) rather than going blank: a blank cannot be told apart
        # from "we never recorded it", and provenance is the point of the panel.
        self.assertEqual(by_name["gamma"]["model_source"], MODEL_CLI_DEFAULT)
        self.assertIn("gamma", by_name["gamma"]["model"])
        self.assertIn("does not report", by_name["gamma"]["model"])
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

    def test_a_silent_seat_is_recorded_and_is_not_a_review(self):
        # It used to be dropped, so the report could not say which seat had
        # returned nothing (#700, round 2). It is named now — and excluded from
        # the count by the same rule that excludes every other abstention.
        silent = AgentResult(agent="alpha", vendor="acme", ok=True, output="   ", duration_s=0.0)
        entries = reviewer_ballots(self._fake_outcome([silent]), self._config())
        self.assertEqual([e["name"] for e in entries], ["alpha", "chair"])
        self.assertEqual(entries[0]["verdict"], ABSTAIN)
        self.assertFalse(entries[0]["counts_as_review"])
        self.assertIn("'alpha'", entries[0]["scope"])
        self.assertIn("returned nothing at all", entries[0]["scope"])
        self.assertEqual(entries[-1]["reviews_supplied"], 0)

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

    def test_a_clean_reviewer_that_names_what_it_read_approves(self):
        # A clean review is a real outcome and must stay expressible: no finding,
        # no file, but it says what it covered — so it votes.
        clean = AgentResult(
            agent="alpha",
            vendor="acme",
            ok=True,
            output=(
                "Checked: src/parser.py, src/cli.py\n"
                "Tested: nothing run\n"
                "Nothing blocking. I examined the parser changes end to end."
            ),
            duration_s=0.0,
        )
        entry = reviewer_ballots(self._fake_outcome([clean]), self._config())[0]
        self.assertEqual(entry["verdict"], "APPROVE")
        self.assertEqual(entry["findings"], [])
        self.assertEqual(entry["verified_count"], 0)
        self.assertIn("src/parser.py", entry["scope"])

    def test_unknown_agent_name_states_the_gap_instead_of_going_blank(self):
        stranger = AgentResult(
            agent="delta",
            vendor="",
            ok=True,
            output="Checked: src/delta.py\na review",
            duration_s=0.0,
        )
        entry = reviewer_ballots(self._fake_outcome([stranger], chair="nobody"), self._config())
        self.assertEqual(entry[0]["vendor"], "")
        # No spec by that name, so no model can be reported — and the field says
        # which of the two "no model" situations this is (#700).
        self.assertEqual(entry[0]["model_source"], MODEL_UNKNOWN)
        self.assertIn("delta", entry[0]["model"])
        self.assertEqual(entry[-1]["vendor"], "")
        self.assertEqual(entry[-1]["model_source"], MODEL_UNKNOWN)

    def test_a_run_with_no_chair_reports_no_model_rather_than_a_wrong_one(self):
        entries = reviewer_ballots(self._fake_outcome([], chair=""), self._config())
        self.assertEqual(entries[-1]["model"], "")
        self.assertEqual(entries[-1]["model_source"], MODEL_NONE)

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

    def test_scope_is_empty_when_the_reply_names_nothing(self):
        # The #700 shape. There is no sentence to write here: a reviewer that
        # named nothing has no scope, and the caller turns the empty string into
        # an abstention rather than an APPROVE with a placeholder.
        self.assertEqual(describe_scope(self._result("Looks fine."), []), "")

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
        output = "\n".join(f"I checked the parser in area {i}." for i in range(9))
        scope = describe_scope(self._result(output), [])
        self.assertIn("I checked the parser in area 2.", scope)
        self.assertNotIn("I checked the parser in area 3.", scope)

    def test_unchecked_is_not_a_coverage_claim(self):
        # A word-boundary match, not a substring one: the panel's own house
        # phrasing is "unchecked return value", and folding that into the
        # coverage summary would attribute a check the reviewer never made.
        scope = describe_scope(
            self._result("Checked: src/example.py\nThe unchecked return value swallows an error."),
            [],
        )
        self.assertIn("src/example.py", scope)
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
        scope = describe_scope(
            self._result("Checked: src/example.py\n```\nI checked everything."), []
        )
        self.assertIn("src/example.py", scope)
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

    def test_not_stated_says_nothing_was_run_rather_than_shrugging(self):
        # "not stated" described the FIELD. This describes the review (#700): a
        # reader can tell "the reviewer ran nothing" from "nobody filled this in".
        self.assertIn("Nothing run", NOT_STATED)
        self.assertNotEqual(NOT_STATED.strip().lower(), "not stated")

    def test_the_reviewers_own_checked_and_tested_lines_win(self):
        # What the review prompt now asks for. The reviewer's own statement of
        # its coverage beats anything inferred from the surrounding prose.
        result = self._result(
            "**Checked:** src/ai_jury/ballots.py, src/ai_jury/panel.py\n"
            "Tested: PYTHONPATH=src python3 -m unittest tests.test_formats — 42 passed\n"
            "No blocking issues found."
        )
        scope = describe_scope(result, [])
        self.assertIn("src/ai_jury/ballots.py", scope)
        self.assertIn("as stated by the reviewer", scope)
        self.assertIn("42 passed", describe_testing(result))

    def test_a_reviewer_that_ran_nothing_and_says_so_is_recorded_saying_so(self):
        self.assertEqual(
            describe_testing(self._result("Checked: a.py\nTested: nothing run\nfine.")),
            "Tested, as stated by the reviewer: nothing run",
        )

    def test_a_crafted_path_cannot_forge_structure_in_the_scope(self):
        # Scope tokens are backticked so they anchor; the token itself is
        # attacker-influenced, so its own backticks come out first.
        scope = describe_scope(self._result("Checked: `x` and ``` fences"), [])
        self.assertNotIn("```", scope)
        self.assertIn("x and  fences", scope)


class BallotsMustNameSomething(unittest.TestCase):
    """#700: a ballot that names nothing is an abstention, never an approval.

    The observed run returned three ballots reading ``scope: "Reviewed the
    supplied diff; named no specific file."``, ``testing: "not stated"``,
    ``model: ""`` — and on a tier whose review *is* the panel, that was the whole
    review. The consumer refuses a hand-posted verdict shaped like that. These
    assertions are the two halves of the fix: the placeholder cannot come back,
    and a slot that genuinely named nothing is reported as an abstention that
    says why.
    """

    #: The exact strings the defective run emitted. Pinned verbatim, because a
    #: regression here is not "the wording drifted" — it is the placeholder
    #: returning, and a substring search is the only thing that catches that.
    PLACEHOLDER_SCOPE = "Reviewed the supplied diff; named no specific file."

    @staticmethod
    def _stub(output, *, name="alpha", ok=True):
        return AgentResult(agent=name, vendor="acme", ok=ok, output=output, duration_s=0.1)

    @staticmethod
    def _config():
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jury.toml"
            path.write_text(PANEL_TOML, encoding="utf-8")
            return load_config(path)

    def _outcome_with(self, results):
        class FakeOutcome:
            pass

        o = FakeOutcome()
        o.reviews = results
        o.groups = []
        o.findings = []
        o.synthesis = None
        o.verify = None
        o.chair = "alpha"
        return o

    def test_the_placeholder_scope_is_gone_from_every_rendering(self):
        outcome, config = _panel_outcome()
        self.assertNotIn(self.PLACEHOLDER_SCOPE, to_keel_reviews(outcome, config))
        self.assertNotIn(self.PLACEHOLDER_SCOPE, to_json(outcome, config))

    def test_every_ballot_of_a_real_run_names_something_checkable(self):
        # The acceptance criterion, stated as the consumer states it: a path, a
        # path:line, a backticked symbol, a called identifier, or a "checked …"
        # clause. Applied to the chair record too — it is a verdict as well.
        records = json.loads(to_keel_reviews(*_panel_outcome()))
        self.assertEqual(len(records), 4)
        for record in records:
            self.assertTrue(
                scope_is_substantive(record["scope"]),
                f"{record['reviewer']} would be refused as insubstantial: {record['scope']!r}",
            )

    def test_no_ballot_reports_an_empty_model(self):
        for record in json.loads(to_keel_reviews(*_panel_outcome())):
            self.assertTrue(record["model"].strip(), f"{record['reviewer']} reports no model")

    def test_a_stubbed_agent_returning_nothing_useful_abstains_by_name(self):
        # The acceptance criterion's other half. This agent exits 0 and says
        # something — so it IS a ballot — but names nothing, which on main was
        # rendered as APPROVE with the placeholder scope.
        outcome = self._outcome_with([self._stub("Looks good to me, no concerns.")])
        entry = reviewer_ballots(outcome, self._config())[0]
        self.assertEqual(entry["verdict"], ABSTAIN)
        self.assertIn("Abstention", entry["scope"])
        self.assertIn("alpha", entry["scope"])
        self.assertIn("named no file, symbol, coverage clause or finding", entry["scope"])
        # And the reason is itself anchorless, so a consumer applying the same
        # rule reaches the same conclusion rather than being talked past it.
        self.assertFalse(scope_is_substantive(entry["scope"]))

    def test_the_abstention_reason_names_which_kind_of_nothing_came_back(self):
        by_output = {
            "refusal": self._stub("I cannot assist with that request."),
            "adapter": self._stub("garbled", ok=False),
            # The case that used to leave no record at all (#700, round 2).
            "silent": self._stub(""),
        }
        entries = {
            key: reviewer_ballots(self._outcome_with([result]), self._config())[0]
            for key, result in by_output.items()
        }
        self.assertIn("returned a refusal", entries["refusal"]["scope"])
        self.assertIn("adapter reported failure", entries["adapter"]["scope"])
        self.assertIn("returned nothing at all", entries["silent"]["scope"])
        for entry in entries.values():
            self.assertEqual(entry["verdict"], ABSTAIN)
            self.assertFalse(entry["scope_substantive"])
            self.assertFalse(entry["counts_as_review"])

    def test_a_reviewer_with_findings_but_no_file_still_names_its_claims(self):
        # Issue mode attaches no file to any finding, so the file list is empty
        # for a reviewer that did real work. Its claims are what it named — and
        # that exception is scoped to issue mode (#700, round 2), because in a
        # code review a claim raised against no file names no place in the code.
        findings = [Finding(severity="major", file="", claim="no reproduction steps")]
        scope = describe_scope(self._stub("The issue is thin."), findings, mode="issue")
        self.assertIn("no reproduction steps", scope)
        self.assertTrue(scope_is_substantive(scope))
        self.assertEqual(describe_scope(self._stub("The issue is thin."), findings), "")

    def test_the_bundle_and_the_json_report_state_the_same_scope_and_verdict(self):
        # The bundle is a projection of the ballots, so the verdict a consumer is
        # handed and the scope meant to justify it cannot drift apart.
        outcome, config = _panel_outcome()
        ballots = {b["name"]: b for b in reviewer_ballots(outcome, config)}
        for record in keel_reviews(outcome, config):
            ballot = ballots[record["reviewer"]]
            self.assertEqual(record["scope"], ballot["scope"])
            self.assertEqual(record["testing"], ballot["testing"])
            self.assertEqual(record["verdict"], ballot["verdict"])
            self.assertEqual(record["model"], ballot["model"])

    def test_an_effort_remapped_model_is_reported_as_the_id_actually_sent(self):
        # `agy` encodes reasoning effort in the model id, so the configured key
        # is not what was requested. A ballot naming the unmapped id would name a
        # model the run never asked for.
        class Spec:
            name = "a"
            vendor = "google"
            command = "agy"
            model = "gemini-3-pro"
            effort = "high"

        self.assertEqual(requested_model(Spec()), "gemini-3-pro-high")

    def test_an_unmappable_effort_degrades_to_the_configured_id(self):
        class Spec:
            name = "a"
            vendor = "google"
            command = "agy"
            model = ""
            effort = "high"

        # Effort-as-model-id with nothing configured maps to nothing, so there is
        # still no id to report — and the caller states the CLI-default case.
        self.assertEqual(requested_model(Spec()), "")

    def test_a_garbage_effort_level_does_not_crash_a_ballot(self):
        class Spec:
            name = "a"
            vendor = "acme"
            command = "acme"
            model = "acme-1"
            effort = "stupendous"

        # `effort_args` raises on an unknown level — validate_config's job to
        # refuse, never a ballot's to die on. The configured id stands.
        self.assertEqual(requested_model(Spec()), "acme-1")

    def test_a_checked_line_carrying_only_markup_does_not_consume_the_real_one(self):
        # A reviewer that emits the header with nothing but emphasis markers on
        # it and its files on the next line. The markup is not a statement of
        # coverage, so the scan keeps looking rather than recording "" and
        # stopping at the first line that merely has the right shape.
        result = self._stub("Checked: * *\nChecked: src/ai_jury/ballots.py\nfine.")
        self.assertIn("src/ai_jury/ballots.py", describe_scope(result, []))

    def test_repeated_and_empty_claims_are_named_once(self):
        # The claim list is what an issue-mode ballot names, so a reviewer that
        # raised the same gap twice must not have it counted twice — the count
        # in the scope is a statement about coverage, not about output volume.
        findings = [
            Finding(severity="major", file="", claim="no reproduction steps"),
            Finding(severity="minor", file="", claim=""),
            Finding(severity="major", file="", claim="no reproduction steps"),
        ]
        scope = describe_scope(self._stub("The issue is thin."), findings, mode="issue")
        self.assertIn("Raised 1 finding(s)", scope)

    def test_issue_mode_findings_with_no_claim_at_all_name_nothing(self):
        # The exception is "the claims are what it named", so a finding carrying
        # no claim names nothing and the ballot abstains — issue mode does not
        # get a free pass, it gets one extra source.
        findings = [Finding(severity="major", file="", claim="")]
        self.assertEqual(
            describe_scope(self._stub("The issue is thin."), findings, mode="issue"), ""
        )


class AClaimIsNotAPlaceInTheCode(unittest.TestCase):
    """#700, round 2: the fallback that let a code review name nowhere.

    ``describe_scope`` fell back to the reviewer's *claims* whenever its findings
    carried no file, and ``_tick`` backticked each one — which is an anchor under
    the substance rule. So a code-review ballot raising one ``major`` finding
    against ``file: ""`` produced a scope that passed the gate and a verdict of
    ``REQUEST_CHANGES``, having named no file, line or symbol at all. keel's rule
    is that a scope must name a place or carry a ``Checked …`` clause; a claim is
    neither.

    ``--issue`` is the one legitimate exception and stays: there a finding
    carries ``file: ""`` by construction, because the panel is reading an issue's
    prose rather than a diff, so the claims are the only thing it can name.
    """

    @staticmethod
    def _config():
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jury.toml"
            path.write_text(PANEL_TOML, encoding="utf-8")
            return load_config(path)

    def _seat(self, with_findings):
        class FakeOutcome:
            pass

        finding = Finding(
            severity="major",
            file="",
            line=None,
            claim="the retry loop can spin forever",
            evidence="e",
            reviewer="alpha",
        )
        group = FindingGroup(
            representative=finding,
            reviewers=["alpha"],
            severity="major",
            bucket="single",
            status="verified",
        )
        o = FakeOutcome()
        o.reviews = [
            AgentResult(
                agent="alpha",
                vendor="acme",
                ok=True,
                output="There is a serious problem here.",
                duration_s=0.1,
            )
        ]
        o.findings = [finding] if with_findings else []
        o.groups = [group] if with_findings else []
        o.synthesis = None
        o.verify = None
        # Not the chair: a chaired ballot appends its own sentence to the scope,
        # which would confuse what is being measured here.
        o.chair = "beta"
        return o

    def test_a_code_review_naming_no_file_abstains(self):
        entry = reviewer_ballots(self._seat(True), self._config(), mode="code")[0]
        # On e01e4f1 this was REQUEST_CHANGES with a scope reading
        # "Raised 1 finding(s) against no file: `the retry loop can spin forever`."
        self.assertEqual(entry["verdict"], ABSTAIN)
        self.assertFalse(entry["scope_substantive"])
        self.assertFalse(entry["counts_as_review"])
        self.assertNotIn("the retry loop can spin forever", entry["scope"])
        # And the reason distinguishes "said nothing" from "named nowhere".
        self.assertIn("raised 1 finding(s) but attached none of them to a file", entry["scope"])

    def test_the_same_ballot_in_issue_mode_still_votes(self):
        entry = reviewer_ballots(self._seat(True), self._config(), mode="issue")[0]
        self.assertEqual(entry["verdict"], "NEEDS_INFO")
        self.assertTrue(entry["counts_as_review"])
        self.assertIn("the retry loop can spin forever", entry["scope"])

    def test_a_reviewer_that_said_nothing_gets_the_other_reason(self):
        entry = reviewer_ballots(self._seat(False), self._config(), mode="code")[0]
        self.assertEqual(entry["verdict"], ABSTAIN)
        self.assertIn("named no file, symbol, coverage clause or finding", entry["scope"])


class TheBundleCarriesTheModelDiscriminator(unittest.TestCase):
    """#700, round 2: ``model`` changed meaning and only half the output said so.

    ``to_keel_reviews`` emits the same ``model`` field as the JSON report, where
    a CLI default is now an English sentence rather than ``""``. The report grew
    ``model_source`` to say which of the two a value is; this projection did not,
    so a machine consumer of the bundle — the shape keel actually parses — had to
    read prose to tell a requested id from a default.
    """

    def test_every_record_carries_model_source_from_its_ballot(self):
        outcome, config = _panel_outcome()
        ballots = {b["name"]: b for b in reviewer_ballots(outcome, config)}
        for record in json.loads(to_keel_reviews(outcome, config)):
            self.assertEqual(record["model_source"], ballots[record["reviewer"]]["model_source"])

    def test_the_default_case_is_machine_readable_without_parsing_prose(self):
        # `gamma` pins no model, so its `model` is a sentence about the CLI.
        by_name = {r["reviewer"]: r for r in json.loads(to_keel_reviews(*_panel_outcome()))}
        self.assertEqual(by_name["gamma"]["model_source"], MODEL_CLI_DEFAULT)
        self.assertEqual(by_name["alpha"]["model_source"], MODEL_REQUESTED)

    def test_the_bundle_says_which_records_are_reviews(self):
        records = json.loads(to_keel_reviews(*_panel_outcome()))
        self.assertFalse(records[-1]["counts_as_review"])
        self.assertTrue(all(r["counts_as_review"] for r in records[:-1]))

    def test_the_record_still_satisfies_the_vendored_consumer_contract(self):
        # Two added keys must not break the payload keel accepts.
        items = parse_reviews_contract(json.loads(to_keel_reviews(*_panel_outcome())))
        self.assertEqual(len(items), 4)


if __name__ == "__main__":
    unittest.main()
