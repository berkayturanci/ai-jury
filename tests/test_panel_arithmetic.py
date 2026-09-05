"""The number a downstream consumer receives, said out loud (#699, #700).

The reported failure: a machine with the three shipped CLIs ran the panel,
``jury --doctor`` said ``cross-vendor ready: yes``, the run exited 0 — and the
consumer refused the bundle with *supplied 2 review(s) but tier requires at
least 3*. Nothing in the tool had ever stated the number the consumer counts.

Four facts had to be established against the code rather than assumed, and they
are the first four classes below:

1. **A review is a ballot that reviewed.** The consumer splits the report's
   ``reviewers`` array on ``role``, keeps the ``chair`` entry aside as the
   panel's consensus record, and counts the rest — then refuses any of those
   whose scope names nothing checkable, under its own
   ``review-verdict-insubstantial`` rule. So the number to announce is the
   ballots that named something and voted. Adding the chair record advertises
   one review the consumer will not find; so does counting an abstention, which
   is the same defect one step further out and is #700's second round.
2. **The chairing agent already sits on the panel.** ``resolve_chair`` only ever
   picks from the *usable* agents and round 1 runs every usable agent, so an
   *n*-agent bench yields *n* ballots — one of them the chairing agent's, an
   ordinary ``panelist`` record the consumer counts like any other — not *n-1*.
   What was missing was any statement of that: the trailing ``chair`` record is
   indistinguishable from a synthesis-only entry unless it says otherwise.
3. **A bundle can still supply fewer reviews than it has seats.** An agent that
   is installed, runs, and returns nothing is recorded as an abstention naming it
   (#635's failure, restated for ballots), and so is one that answers with prose
   naming nothing. Neither is a review, so the count is knowable only as an upper
   bound before the run — hence a gate on both sides of it.
4. **Every seat that ran is in the bundle.** A silent seat used to be dropped
   entirely, which left the report unable to say *which* agent had gone quiet.

The panel is mocked throughout: no CLI, no network, no spend.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

from ai_jury import adapters as adapters_module
from ai_jury import cli, doctor, panel
from ai_jury.adapters import AgentResult, MockAdapter
from ai_jury.config import _from_dict

_DIFF = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"

#: The bench a normal machine has: the three CLIs the project ships defaults for.
_THREE_CLI_TOML = """
[jury]
rounds = 1
chair = "claude"
verify = false

[[agent]]
name = "claude"
vendor = "anthropic"
command = "claude"

[[agent]]
name = "codex"
vendor = "openai"
command = "codex"

[[agent]]
name = "agy"
vendor = "google"
command = "agy"
"""

#: What keel requires of a tier-3 change, and the number the report refused.
_TIER_THREE = 3


#: The reply that satisfied ``--min-reviews`` while reviewing nothing. It exits
#: 0, it is prose, it is not a refusal — and it names no file, line or symbol, so
#: the consumer refuses the verdict built from it.
_PROSE_ONLY = "Looks good to me, no concerns."

#: The reply that names what it read and then declines to review it. The pair the
#: prose could not describe (#700, round 3): ``scope_substantive`` is **true** —
#: the ``Checked:`` line is exactly the anchor the consumer looks for — while the
#: verdict is ``ABSTAIN``, because a refusal is not a review (#251).
_SCOPED_REFUSAL = "Checked: src/a.py\n\nI cannot assist with reviewing this change."

#: The same pair reached the other way: the adapter reports failure, and what it
#: managed to emit before dying still names a file.
_SCOPED_FAILURE = "Checked: src/a.py\n\nThe CLI exited before writing a review."


def _prose_run(
    prose_only: frozenset[str],
    silent: frozenset[str],
    refusing: frozenset[str],
    broken: frozenset[str],
):
    """A ``MockAdapter.run`` where some agents answer without reviewing.

    The other way a seat produces no review, and the one no earlier fixture had:
    ``_silent_run``'s agents return nothing, which every layer already treated as
    an abstention. These return a cheerful sentence.

    ``refusing`` and ``broken`` are round 3's addition, and the two of them are
    the only ways a ballot can carry a substantive scope and still abstain: they
    name a file and then refuse, or name a file and then fail. Every earlier
    fixture abstained by naming nothing, which is why nothing caught a scope
    sentence keyed on "did a scope come back" rather than on the count.
    """
    real = MockAdapter.run

    def _run(self, prompt, phase="review", timeout=None, role_policy=None):
        if phase == "review" and self.name in silent:
            return AgentResult(
                self.name,
                self.spec.vendor,
                False,
                "",
                0.0,
                "no review returned: the agent declined to review",
                error_code=adapters_module.ERR_NO_REVIEW,
            )
        if phase == "review" and self.name in prose_only:
            return AgentResult(self.name, self.spec.vendor, True, _PROSE_ONLY, 0.0)
        if phase == "review" and self.name in refusing:
            return AgentResult(self.name, self.spec.vendor, True, _SCOPED_REFUSAL, 0.0)
        if phase == "review" and self.name in broken:
            return AgentResult(
                self.name,
                self.spec.vendor,
                False,
                _SCOPED_FAILURE,
                0.0,
                "the agent CLI exited nonzero",
            )
        return real(self, prompt, phase=phase, timeout=timeout, role_policy=role_policy)

    return _run


def _jury(
    argv: list[str],
    *,
    silent: frozenset[str] = frozenset(),
    prose_only: frozenset[str] = frozenset(),
    refusing: frozenset[str] = frozenset(),
    broken: frozenset[str] = frozenset(),
    toml: str = _THREE_CLI_TOML,
):
    """Run ``jury --mock`` and return ``(exit code, stdout, stderr)``."""
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "jury.toml"
        config.write_text(toml, encoding="utf-8")
        diff = Path(tmp) / "changes.diff"
        diff.write_text(_DIFF, encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(MockAdapter, "run", _prose_run(prose_only, silent, refusing, broken)),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                code = cli.main(
                    ["--mock", "--diff-file", str(diff), "--config", str(config), *argv]
                )
            except SystemExit as exc:  # pragma: no cover - argparse only
                code = exc.code
    return code, out.getvalue(), err.getvalue()


def _jury_with_metadata(argv: list[str], **kwargs):
    """``_jury``, plus the ``--metadata-json`` document that same run wrote.

    The count is published in three places — the run's own log, the markdown
    report, and this document — and a test that only reads one of them cannot
    see them disagree, which is the whole defect.
    """
    with tempfile.TemporaryDirectory() as tmp:
        meta = Path(tmp) / "metadata.json"
        code, out, err = _jury([*argv, "--metadata-json", str(meta)], **kwargs)
        return code, out, err, json.loads(meta.read_text(encoding="utf-8"))


def _bundle(**kwargs) -> list[dict]:
    code, out, _err = _jury(["--format", "keel-reviews", "-q"], **kwargs)
    assert code in (0, 3), code
    return json.loads(out)


def _reviewers(**kwargs) -> list[dict]:
    """The JSON report's ``reviewers`` array — what the consumer actually parses."""
    code, out, _err = _jury(["--format", "json", "-q"], **kwargs)
    assert code in (0, 3), code
    return json.loads(out)["reviewers"]


#: ``keel.jury.CHAIR_ROLE``. The consumer separates the entry carrying this role
#: out of the ballots into ``Panel.chair`` — "the chair is the consensus record,
#: not a panelist ballot" — so a review, to it, is any other entry.
_CHAIR_ROLE = "chair"


#: keel's ``review-verdict-insubstantial`` anchors, vendored the way the reviews
#: contract is vendored in ``test_formats``: a path, a ``path:line``, a backticked
#: symbol, a called identifier, or a "checked …" clause.
_ANCHORS = (
    re.compile(r"[\w./-]+\.[A-Za-z0-9]{1,5}:\d+"),
    re.compile(r"[\w-]+/[\w./-]+\.[A-Za-z0-9]{1,5}\b"),
    re.compile(r"`[^`\n]{2,}`"),
    re.compile(r"\b\w+\.\w+\(\)"),
    re.compile(r"\bchecked\b[^.\n]{8,}", re.IGNORECASE),
)


def _names_something_checkable(scope: str) -> bool:
    return any(p.search(scope) for p in _ANCHORS)


def _ballots_as_the_consumer_counts_them(reviewers: list[dict]) -> list[dict]:
    """Count the report the way the consumer does, applying **both** its rules.

    Derived from the document rather than restated: an expected integer written
    down here would let the two sides drift apart again while the test stayed
    green, which is precisely the failure #699 is.

    Two rules, because the consumer has two. ``keel/jury.py::parse_panel`` lifts
    the entry carrying ``role: "chair"`` out of the ballots into ``Panel.chair``
    — "the chair is the consensus record, not a panelist ballot". Then its
    ``review-verdict-insubstantial`` gate refuses any verdict whose scope names
    no file, line or symbol and carries no ``Checked …`` clause. A count applying
    only the first counts ballots keel then throws away — #699's mismatch from
    the other side, and #700's second round. The report now says the answer
    structurally in ``counts_as_review``; this restates the *rule* rather than
    reading that field, so the field cannot be wrong and green at once.
    """
    panelists = [e for e in reviewers if (e.get("role") or "") != _CHAIR_ROLE]
    return [
        e
        for e in panelists
        if e.get("verdict") != "ABSTAIN" and _names_something_checkable(e.get("scope") or "")
    ]


class TheCountMatchesWhatTheConsumerCounts(unittest.TestCase):
    """The defect, from the side the consumer sees it.

    ai-jury announced ballots + 1; keel counts ballots. Every assertion here
    derives the expected number from the report by ``role``, the way
    ``keel/jury.py::parse_panel`` does, rather than restating an integer — a
    restated number is exactly how the two sides drifted apart in the first
    place.
    """

    def test_the_announced_count_equals_the_ballots_the_consumer_parses(self):
        reviewers = _reviewers()
        counted = _ballots_as_the_consumer_counts_them(reviewers)
        chair = next(e for e in reviewers if e["role"] == _CHAIR_ROLE)
        self.assertEqual(chair["reviews_supplied"], len(counted))
        # And the chair's own record is not among what the consumer counted.
        self.assertNotIn(chair, counted)

    def test_it_still_matches_when_an_agent_falls_silent(self):
        reviewers = _reviewers(silent=frozenset({"codex"}))
        chair = next(e for e in reviewers if e["role"] == _CHAIR_ROLE)
        self.assertEqual(
            chair["reviews_supplied"], len(_ballots_as_the_consumer_counts_them(reviewers))
        )

    def test_the_run_metadata_agrees_with_the_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "run.json"
            code, out, _err = _jury(["--format", "json", "-q", "--metadata-json", str(meta)])
            self.assertIn(code, (0, 3))
            recorded = json.loads(meta.read_text(encoding="utf-8"))["panel"]
        counted = _ballots_as_the_consumer_counts_them(json.loads(out)["reviewers"])
        self.assertEqual(recorded["reviews_supplied"], len(counted))

    def test_three_shipped_clis_supply_three_reviews_and_a_consensus_record(self):
        records = _bundle()
        self.assertEqual([r["reviewer"] for r in records], ["claude", "codex", "agy", "chair"])
        counted = _ballots_as_the_consumer_counts_them(_reviewers())
        self.assertEqual([e["name"] for e in counted], ["claude", "codex", "agy"])
        # The tier that refused the original bundle is met, and met honestly.
        self.assertGreaterEqual(len(counted), _TIER_THREE)

    def test_the_bundle_carries_one_record_more_than_it_carries_reviews(self):
        self.assertEqual(panel.bundle_records(3), 4)
        self.assertEqual(panel.bundle_records(0), 1)

    def test_the_review_count_is_the_ballots_that_actually_reviewed(self):
        # The definition, in the pure function: a panelist record with a
        # substantive scope and a vote. The chair record is not one, an
        # abstention is not one, and neither is a ballot that named nothing —
        # which is the case #700's second round added.
        reviewed = {"role": "panelist", "scope_substantive": True, "verdict": "APPROVE"}
        abstained = {"role": "panelist", "scope_substantive": False, "verdict": "ABSTAIN"}
        # The shape that has to be refused even though it votes: a scope that
        # names nothing cannot justify the verdict beside it.
        anchorless = {"role": "panelist", "scope_substantive": False, "verdict": "APPROVE"}
        chair = {"role": "chair", "scope_substantive": True, "verdict": "APPROVE"}
        self.assertEqual(panel.review_count([reviewed, abstained, anchorless, chair]), 1)
        self.assertEqual(panel.review_count([]), 0)
        self.assertEqual(panel.review_count(None), 0)


class TheChairsRoleIsStatedPlainly(unittest.TestCase):
    """Acceptance criterion 2 — and the half the old bundle got silently wrong.

    Before this change the chair record read *"Chair synthesis over 3 panel
    review(s) and 4 consensus group(s), across src/example.py."* — no agent
    named, no statement that the chair had also cast one of those ballots, and
    nothing to distinguish it from a chair that only synthesised. A reader
    deciding whether that record is a review has nothing to decide on.
    """

    def test_the_chair_record_names_the_agent_that_chaired(self):
        self.assertIn("`claude`", _bundle()[-1]["scope"])

    def test_the_chair_record_says_its_ballot_is_counted(self):
        scope = _bundle()[-1]["scope"]
        self.assertIn("also sat on the panel", scope)
        self.assertIn("'claude' review in this bundle", scope)
        self.assertIn("counts as one of the reviews", scope)

    def test_the_chair_record_disclaims_itself_as_a_review(self):
        # The half that was wrong: this record is the panel's consensus, and a
        # consumer does not count it. Saying so is what keeps the two arithmetics
        # from parting company again.
        scope = _bundle()[-1]["scope"]
        self.assertIn("This synthesis record does not", scope)
        self.assertIn("not a ballot", scope)

    def test_the_chair_record_states_the_reviews_and_the_record_count_apart(self):
        scope = _bundle()[-1]["scope"]
        self.assertIn("carries 3 review(s) plus this record, 4 records in all", scope)

    def test_the_chairs_own_ballot_is_a_separate_record_with_no_chair_role(self):
        # Read on its own, one head-pinned verdict per review, with no chair
        # record beside it — so the ballot has to carry the fact too. And it is
        # emphatically not the synthesis entry: separate record, role
        # ``panelist``, counted.
        records = _bundle()
        self.assertIn("also chaired the run", records[0]["scope"])
        self.assertIn("one of the panel's reviews", records[0]["scope"])
        self.assertNotEqual(records[0]["reviewer"], records[-1]["reviewer"])

        chairing_ballot = next(e for e in _reviewers() if e.get("chaired"))
        self.assertEqual(chairing_ballot["role"], "panelist")
        self.assertIn(chairing_ballot, _ballots_as_the_consumer_counts_them(_reviewers()))

    def test_a_panelist_that_did_not_chair_claims_nothing(self):
        for record in _bundle()[1:3]:
            self.assertNotIn("chaired the run", record["scope"])

    def test_a_chair_that_returned_no_review_says_so(self):
        # The observed run: the chair was installed, ran, and came back empty.
        # It is now IN the bundle as an abstention naming it (#700, round 2) —
        # the report could not previously say which agent had gone quiet — and
        # the chair record says its ballot supplies no review.
        records = _bundle(silent=frozenset({"claude"}))
        self.assertEqual([r["reviewer"] for r in records], ["claude", "codex", "agy", "chair"])
        self.assertFalse(records[0]["counts_as_review"])
        self.assertEqual(records[0]["verdict"], "ABSTAIN")
        self.assertIn("named nothing", records[0]["scope"])
        scope = records[-1]["scope"]
        self.assertIn("also sat on the panel but abstained", scope)
        self.assertIn("NOT counted as a review", scope)
        self.assertIn("carries 2 review(s) plus this record, 4 records in all", scope)
        for record in records[1:-1]:
            self.assertNotIn("chaired the run", record["scope"])


#: The claim a chaired ballot's scope makes about itself, verbatim. Read from the
#: document rather than paraphrased: this is the sentence a human is handed, and
#: the invariant under test is that it and ``counts_as_review`` are one statement.
_CLAIMS_TO_BE_A_REVIEW = "this ballot is one of the panel's reviews"


class TheChairedBallotsProseMatchesTheCount(unittest.TestCase):
    """The chaired seat's scope says what ``counts_as_review`` says (#700, round 3).

    The reported failure: a chaired seat answered ``Checked: src/a.py`` and then
    refused. Round 2 appended the chair sentence on the strength of the scope
    being non-empty — before the verdict was derived and before the count was
    taken — so the record shipped ``verdict: ABSTAIN``, ``counts_as_review:
    false``, and a scope telling the human reading it that this ballot was one of
    the panel's reviews. Every machine field was right and the only prose a
    reviewer actually reads was wrong, which is the same defect #699 and #700
    were: two definitions of "a review", and nothing keeping them equal.

    ``ai_jury.panel.is_review`` is the definition. The sentence is now a
    *rendering* of the answer it gave, and these tests read the answer and the
    sentence out of the same record so the two cannot be green while disagreeing.
    """

    def test_a_chaired_ballot_that_refused_does_not_claim_to_be_a_review(self):
        # Fails on 66aab48: the scope claimed to be one of the panel's reviews
        # while the record beside it said ABSTAIN / counts_as_review false.
        ballot = _reviewers(refusing=frozenset({"claude"}))[0]
        self.assertEqual(ballot["name"], "claude")
        self.assertTrue(ballot["scope_substantive"])
        self.assertEqual(ballot["verdict"], "ABSTAIN")
        self.assertFalse(ballot["counts_as_review"])
        self.assertNotIn(_CLAIMS_TO_BE_A_REVIEW, ballot["scope"])
        self.assertIn("also chaired the run", ballot["scope"])
        self.assertIn("NOT one of the panel's reviews", ballot["scope"])
        self.assertIn("returned a refusal rather than a review", ballot["scope"])

    def test_a_chaired_ballot_whose_adapter_failed_names_that_cause_instead(self):
        # The other way a substantive scope can accompany an abstention. The two
        # are kept apart because they ask for different fixes: one is a CLI to go
        # and look at, the other is an agent that declined.
        ballot = _reviewers(broken=frozenset({"claude"}))[0]
        self.assertTrue(ballot["scope_substantive"])
        self.assertFalse(ballot["counts_as_review"])
        self.assertNotIn(_CLAIMS_TO_BE_A_REVIEW, ballot["scope"])
        self.assertIn("its adapter reported failure", ballot["scope"])

    def test_a_chaired_ballot_that_reviewed_still_says_it_is_counted(self):
        ballot = _reviewers()[0]
        self.assertTrue(ballot["counts_as_review"])
        self.assertIn(_CLAIMS_TO_BE_A_REVIEW, ballot["scope"])

    def test_no_ballots_prose_and_count_disagree_in_any_run(self):
        # The invariant, over every shape of run this module can produce. An
        # expected string per case would let the two drift apart again while the
        # test stayed green; this compares the document against itself.
        for kwargs in (
            {},
            {"refusing": frozenset({"claude"})},
            {"broken": frozenset({"claude"})},
            {"silent": frozenset({"claude"})},
            {"prose_only": frozenset({"claude"})},
            {"refusing": frozenset({"claude", "codex"})},
        ):
            with self.subTest(**{k: sorted(v) for k, v in kwargs.items()}):
                entries = _reviewers(**kwargs)
                # No record claims a status the count denies. One direction, not
                # equality: a panelist that did not chair says nothing about the
                # panel's arithmetic at all, which is the correct silence.
                for entry in entries:
                    if _CLAIMS_TO_BE_A_REVIEW in entry["scope"]:
                        self.assertTrue(entry["counts_as_review"], entry["name"])
                # And the chaired ballot — the only one that speaks — says
                # exactly what the count says, either way round.
                chaired = next(e for e in entries if e.get("chaired"))
                self.assertEqual(
                    _CLAIMS_TO_BE_A_REVIEW in chaired["scope"],
                    chaired["counts_as_review"],
                )

    def test_the_chair_record_stops_asserting_why_its_own_ballot_abstained(self):
        # It said "because it named nothing a reader could check" — one of the
        # three ways to abstain, asserted as though it were the only one, and
        # false of a chair that named a file and then refused. The status stays
        # (it is derived); the cause moves to the ballot that knows it.
        scope = _reviewers(refusing=frozenset({"claude"}))[-1]["scope"]
        self.assertIn("also sat on the panel but abstained", scope)
        self.assertIn("NOT counted as a review", scope)
        self.assertNotIn("named nothing a reader could check", scope)
        # And the chair's ballot is not double-counted as a "further" abstention.
        self.assertNotIn("further ballot(s)", scope)

    def test_further_abstentions_are_counted_apart_from_the_chairs_own(self):
        scope = _reviewers(refusing=frozenset({"claude", "codex"}))[-1]["scope"]
        self.assertIn("1 further ballot(s) abstained", scope)
        self.assertIn("carries 1 review(s) plus this record, 4 records in all", scope)


class TheJsonReportMarksTheRoles(unittest.TestCase):
    def _reviewers(self, **kwargs) -> list[dict]:
        return _reviewers(**kwargs)

    def test_every_entry_declares_a_role(self):
        entries = self._reviewers()
        self.assertEqual(
            [e["role"] for e in entries], ["panelist", "panelist", "panelist", "chair"]
        )

    def test_exactly_one_panelist_is_flagged_as_the_chair(self):
        entries = self._reviewers()
        self.assertEqual([e["name"] for e in entries if e.get("chaired")], ["claude"])

    def test_the_chair_entry_names_its_agent_and_the_review_count(self):
        chair = self._reviewers()[-1]
        self.assertEqual(chair["agent"], "claude")
        self.assertTrue(chair["ballot_counted"])
        self.assertEqual(chair["reviews_supplied"], 3)

    def test_a_silent_chair_is_not_reported_as_counted(self):
        chair = self._reviewers(silent=frozenset({"claude"}))[-1]
        self.assertEqual(chair["agent"], "claude")
        self.assertFalse(chair["ballot_counted"])
        self.assertEqual(chair["reviews_supplied"], 2)


class TheRunSaysHowManyReviewsItWillSupply(unittest.TestCase):
    """Acceptance criterion 3: stated before the panel, not after the refusal."""

    def test_the_count_is_logged_before_round_one(self):
        _code, _out, err = _jury([])
        announcement = err.index("3 available agent(s) → at most 3 review(s)")
        self.assertLess(announcement, err.index("round 1: 3 agents reviewing"))
        self.assertIn("plus 1 chair synthesis record (not counted as a review)", err)

    def test_the_markdown_report_states_the_chairs_role(self):
        _code, out, _err = _jury(["-q"])
        self.assertIn("reviews for a downstream consumer: 3 of 3 ballot(s)", out)
        self.assertIn("a ballot counts only when it names what it read and votes", out)
        self.assertIn("the chair's synthesis record is carried alongside them", out)
        self.assertIn("chair `claude` also sat on the panel — its review is one of them", out)

    def test_the_markdown_report_states_a_chair_that_supplied_no_review(self):
        _code, out, _err = _jury(["-q"], silent=frozenset({"claude"}))
        # Three ballots, two reviews: the silent seat is recorded and not counted.
        self.assertIn("reviews for a downstream consumer: 2 of 3 ballot(s)", out)
        self.assertIn("chair `claude` supplied no review of its own", out)
        self.assertIn("1 returned nothing", out)


class TheCountIsStatedEvenWhenItIsZero(unittest.TestCase):
    """The count is a number, not a signal — and zero is a number (round 3).

    The report line was guarded by the count's *truthiness*, so the one run that
    supplies no reviews at all — every seat installed, invoked, and silent — was
    the one run whose report said nothing about it, while the pre-flight log
    still announced the three seats the bench had. A reader comparing the two
    saw an announcement of three and a report that had gone quiet. Presence, not
    truth: all three publications of the count agree on 0 the way they agree
    on 3.
    """

    #: Every seat back with nothing: no ballots, so no reviews for a consumer.
    _ALL_SILENT = frozenset({"claude", "codex", "agy"})

    def test_the_markdown_report_prints_the_zero_instead_of_dropping_the_line(self):
        _code, out, _err, metadata = _jury_with_metadata(["-q"], silent=self._ALL_SILENT)
        supplied = metadata["panel"]["reviews_supplied"]
        self.assertEqual(supplied, 0)
        # Derived from the metadata rather than restated, so the two cannot drift.
        self.assertIn(f"reviews for a downstream consumer: {supplied} of 3 ballot(s)", out)
        self.assertIn("chair `claude` supplied no review of its own", out)

    def test_the_report_the_log_and_the_metadata_all_state_the_same_zero(self):
        # ``--min-reviews`` makes the run announce the count it actually ended
        # with, which is the announcement the report has to agree with.
        code, out, err, metadata = _jury_with_metadata(
            ["--min-reviews", str(_TIER_THREE)], silent=self._ALL_SILENT
        )
        self.assertEqual(code, 3)
        supplied = metadata["panel"]["reviews_supplied"]
        self.assertEqual(supplied, 0)
        self.assertIn(f"panel too small after the panel ran: {supplied} review(s) available", err)
        self.assertIn(f"reviews for a downstream consumer: {supplied} of 3 ballot(s)", out)


class TheShortfallIsNamedBeforeThePanelRuns(unittest.TestCase):
    """The other branch of criterion 1: fail early, naming the shortfall."""

    def test_a_bench_that_cannot_reach_the_minimum_never_reviews(self):
        code, _out, err = _jury(["--min-reviews", "4"])
        self.assertEqual(code, 2)
        self.assertIn("panel too small before the panel runs", err)
        self.assertIn("3 review(s) available", err)
        self.assertIn("the chair's synthesis record is not one of them", err)
        self.assertIn("requires 4", err)
        # Named before a single agent was invoked, which is the whole point.
        self.assertNotIn("round 1:", err)

    def test_a_bench_that_reaches_it_runs_normally(self):
        code, _out, err = _jury(["--min-reviews", "3", "-q"])
        self.assertEqual(code, 0)
        self.assertNotIn("panel too small", err)

    def test_the_config_key_sets_it_too(self):
        code, _out, err = _jury(
            [],
            toml=_THREE_CLI_TOML.replace("rounds = 1", "rounds = 1\n\n[jury.ci]\nmin_reviews = 9"),
        )
        self.assertEqual(code, 2)
        self.assertIn("requires 9", err)


class TheShortfallIsAlsoCaughtOnTheResult(unittest.TestCase):
    """No pre-flight can predict an agent that runs and returns nothing."""

    def test_a_silent_agent_that_shrinks_the_bundle_fails_the_gate(self):
        # No ``-q``: the shortfall is a log line, and a gate nobody can read is
        # the failure mode this issue was about.
        code, _out, err = _jury(["--min-reviews", "3"], silent=frozenset({"codex"}))
        # Pre-flight passed: three agents were available, so three reviews were
        # possible. One of them then said nothing.
        self.assertEqual(code, 3)
        self.assertIn("panel too small after the panel ran", err)
        self.assertIn("2 review(s) available", err)
        self.assertIn("1 seat(s) ran without producing a review", err)
        self.assertIn("1 returned nothing at all", err)

    def test_the_tier_three_case_the_consumer_actually_refused(self):
        # The reported mismatch, now impossible: a silent agent leaves two
        # ballots, ``--min-reviews 3`` refuses it here rather than letting keel
        # refuse it later with *supplied 2 review(s) but tier requires 3*.
        code, out, _err = _jury(
            ["--min-reviews", "3", "--format", "json"], silent=frozenset({"codex"})
        )
        self.assertEqual(code, 3)
        self.assertEqual(len(_ballots_as_the_consumer_counts_them(json.loads(out)["reviewers"])), 2)

    def test_the_exit_code_is_the_panel_one_not_the_findings_one(self):
        # Same family as a collapsed panel (#682): the panel fell short, not the
        # diff. A caller must be able to tell those apart.
        code, _out, _err = _jury(["--min-reviews", "3", "-q", "--ci"], silent=frozenset({"codex"}))
        self.assertEqual(code, 3)

    def test_off_by_default(self):
        code, _out, err = _jury([], silent=frozenset({"codex", "agy"}))
        self.assertNotIn("panel too small", err)
        # min_vendors still has its own say; this gate contributed nothing.
        self.assertIn(code, (0, 3))


class DoctorReportsWhatAConsumerWouldReceive(unittest.TestCase):
    """``cross-vendor ready: yes`` was true and answered the wrong question."""

    @staticmethod
    def _diagnostics(toml: str = _THREE_CLI_TOML):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jury.toml"
            path.write_text(toml, encoding="utf-8")
            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch.object(doctor, "_is_available", return_value=True),
                mock.patch.object(doctor, "_detect_capabilities", return_value={}),
                mock.patch.object(doctor, "_probe_models", return_value=None),
            ):
                return doctor.build_diagnostics(path)

    @staticmethod
    def _unreachable(toml: str = _THREE_CLI_TOML):
        """The same bench with every command absent from PATH."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jury.toml"
            path.write_text(toml, encoding="utf-8")
            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch.object(doctor, "_is_available", return_value=False),
                mock.patch.object(doctor, "_detect_capabilities", return_value={}),
                mock.patch.object(doctor, "_probe_models", return_value=None),
            ):
                return doctor.build_diagnostics(path)

    def test_the_export_carries_the_number_a_consumer_counts(self):
        report = doctor.doctor_report_dict(self._diagnostics())
        self.assertEqual(report["panel"]["panelists_available"], 3)
        self.assertEqual(report["panel"]["reviews_supplied_max"], 3)

    def test_the_text_report_says_it_beside_readiness(self):
        text = doctor.render_report(self._diagnostics())
        self.assertIn("reviews for a consumer: at most 3", text)
        self.assertIn("the chairing agent reviews too", text)
        self.assertIn("the chair's synthesis record is not a review", text)

    def test_nothing_reachable_promises_nothing(self):
        # The bound used to read "at most 1" on a machine where a real run exits
        # 2 with *no usable agents* — the chair record counted as a review that
        # no agent was there to write.
        diagnostics = self._unreachable()
        self.assertEqual(doctor.doctor_report_dict(diagnostics)["panel"]["reviews_supplied_max"], 0)
        self.assertIn("reviews for a consumer: at most 0", doctor.render_report(diagnostics))

    def test_nothing_reachable_warns_against_a_minimum_of_one(self):
        # The smallest minimum there is, and the one the old arithmetic silently
        # satisfied on an empty bench.
        diagnostics = self._unreachable(
            _THREE_CLI_TOML.replace("rounds = 1", "rounds = 1\n\n[jury.ci]\nmin_reviews = 1")
        )
        warnings = " ".join(diagnostics["config_warnings"])
        self.assertIn("panel too small on this machine", warnings)
        self.assertIn("0 review(s) available", warnings)
        self.assertIn("requires 1", warnings)

    def test_a_configured_minimum_this_machine_cannot_meet_is_warned(self):
        diagnostics = self._diagnostics(
            _THREE_CLI_TOML.replace("rounds = 1", "rounds = 1\n\n[jury.ci]\nmin_reviews = 6")
        )
        warnings = " ".join(diagnostics["config_warnings"])
        self.assertIn("panel too small on this machine", warnings)
        self.assertIn("requires 6", warnings)

    def test_a_minimum_this_machine_meets_is_silent(self):
        diagnostics = self._diagnostics(
            _THREE_CLI_TOML.replace("rounds = 1", "rounds = 1\n\n[jury.ci]\nmin_reviews = 3")
        )
        self.assertNotIn("panel too small", " ".join(diagnostics["config_warnings"]))

    def test_a_machine_with_no_config_claims_nothing(self):
        self.assertEqual(doctor._NO_PANEL["reviews_supplied_max"], 0)


class ThePureArithmetic(unittest.TestCase):
    def test_describe_reads_as_one_sentence_with_and_without_a_bench_count(self):
        # Pre-run the number is a CEILING and says so: a seat is a seat that
        # might review, and one that names nothing abstains (#700, round 2).
        # The old line promised "3 ballot(s) = 3 review(s)" before a single
        # agent had answered.
        pre_run = panel.describe(3, available=3)
        self.assertIn("3 available agent(s) → at most 3 review(s)", pre_run)
        self.assertIn("names what it read and votes", pre_run)
        self.assertIn("plus 1 chair synthesis record (not counted as a review)", pre_run)
        self.assertIn("2 review(s) for a downstream consumer", panel.describe(2))
        self.assertNotIn("available agent(s)", panel.describe(2))
        self.assertNotIn("at most", panel.describe(2))

    def test_zero_disables_the_gate(self):
        self.assertIsNone(panel.shortfall(0, 0, stage="anywhere"))
        self.assertIsNone(panel.shortfall(0, None, stage="anywhere"))

    def test_a_satisfied_minimum_is_silent(self):
        self.assertIsNone(panel.shortfall(3, 3, stage="anywhere"))

    def test_a_minimum_of_one_is_not_satisfied_by_the_chair_record(self):
        # The doctor half of the defect, in the pure function: an empty bench
        # supplies no review, and the chair's record does not make it one.
        self.assertIsNotNone(panel.shortfall(0, 1, stage="anywhere"))

    def test_the_message_names_the_shortfall_and_both_opt_outs(self):
        message = panel.shortfall(1, 4, stage="before the panel runs", silent=2, insubstantial=1)
        self.assertIn("1 review(s) available", message)
        self.assertIn("the chair's synthesis record is not one of them", message)
        self.assertIn("requires 4", message)
        # The two ways a seat that ran supplies no review, named apart: they ask
        # for different fixes (#700, round 2).
        self.assertIn("3 seat(s) ran without producing a review", message)
        self.assertIn("2 returned nothing at all", message)
        self.assertIn("1 answered but named nothing checkable", message)
        self.assertIn("--min-reviews", message)
        self.assertIn("[jury.ci] min_reviews", message)

    def test_a_silent_count_of_zero_is_not_mentioned(self):
        self.assertNotIn("without producing a review", panel.shortfall(1, 4, stage="x"))

    def test_every_seat_that_ran_gets_a_ballot_record(self):
        # Including the silent one (#700, round 2): dropping it left the bundle
        # unable to say which agent had returned nothing. `responded` is what
        # the record's own reason sentence keys on, not what the count does.
        ok = AgentResult("a", "acme", True, "a review", 0.0)
        failed_with_output = AgentResult("b", "acme", False, "partial review", 0.0)
        empty = AgentResult("c", "acme", True, "   ", 0.0)
        self.assertTrue(panel.responded(ok))
        self.assertTrue(panel.responded(failed_with_output))
        self.assertFalse(panel.responded(empty))
        self.assertEqual(
            [r.agent for r in panel.ballot_seats([ok, failed_with_output, empty])],
            ["a", "b", "c"],
        )

    def test_none_is_tolerated(self):
        self.assertEqual(panel.ballot_seats(None), [])


class AnAbstentionIsNotAReviewEither(unittest.TestCase):
    """#700, round 2: the gate the abstention walked straight through.

    Recording the abstention was only half the fix. ``panel.review_count`` still
    counted any seat that had *returned output*, so three replies of
    "Looks good to me, no concerns." — each one an ``ABSTAIN`` with an anchorless
    scope, each one refused by the consumer's own ``review-verdict-insubstantial``
    rule — satisfied ``--min-reviews 3`` and exited 0. The run announced three
    reviews and shipped a bundle carrying none, which is #699's mismatch with the
    abstention wearing the review's clothes.
    """

    _ALL = frozenset({"claude", "codex", "agy"})

    def test_a_panel_of_prose_only_replies_supplies_no_reviews(self):
        reviewers = _reviewers(prose_only=self._ALL)
        # Every seat is in the bundle — that half was already right.
        self.assertEqual([e["name"] for e in reviewers[:-1]], ["claude", "codex", "agy"])
        for entry in reviewers[:-1]:
            self.assertEqual(entry["verdict"], "ABSTAIN")
            self.assertFalse(entry["scope_substantive"])
            self.assertFalse(entry["counts_as_review"])
        # And the consumer's own arithmetic agrees with the announced number.
        self.assertEqual(_ballots_as_the_consumer_counts_them(reviewers), [])
        self.assertEqual(reviewers[-1]["reviews_supplied"], 0)

    def test_min_reviews_is_no_longer_satisfied_by_them(self):
        code, out, err, metadata = _jury_with_metadata(
            ["--min-reviews", str(_TIER_THREE)], prose_only=self._ALL
        )
        # Exit 3, the panel-size family: on e01e4f1 this exited 0.
        self.assertEqual(code, 3)
        self.assertEqual(metadata["panel"]["reviews_supplied"], 0)
        self.assertEqual(metadata["panel"]["ballots"], 3)
        self.assertEqual(metadata["panel"]["insubstantial"], 3)
        self.assertEqual(metadata["panel"]["silent"], 0)
        self.assertIn("panel too small after the panel ran: 0 review(s) available", err)
        self.assertIn("3 answered but named nothing checkable", err)
        self.assertIn("reviews for a downstream consumer: 0 of 3 ballot(s)", out)

    def test_one_real_review_among_two_abstentions_counts_as_one(self):
        # Not a blanket refusal: the seat that reviewed still supplies a review,
        # and the count is the difference rather than the total.
        code, _out, _err, metadata = _jury_with_metadata(
            ["-q"], prose_only=frozenset({"codex", "agy"})
        )
        self.assertIn(code, (0, 3))
        self.assertEqual(metadata["panel"]["reviews_supplied"], 1)
        self.assertEqual(metadata["panel"]["insubstantial"], 2)
        self.assertEqual(
            len(
                _ballots_as_the_consumer_counts_them(
                    _reviewers(prose_only=frozenset({"codex", "agy"}))
                )
            ),
            1,
        )

    def test_the_silent_seat_is_named_in_the_bundle(self):
        # The other half of the record (#700, round 2): `codex` returned nothing
        # and used to vanish from the report entirely, so nothing said which of
        # the three seats had gone quiet.
        records = _bundle(silent=frozenset({"codex"}))
        self.assertEqual([r["reviewer"] for r in records], ["claude", "codex", "agy", "chair"])
        codex = records[1]
        self.assertEqual(codex["verdict"], "ABSTAIN")
        self.assertFalse(codex["counts_as_review"])
        self.assertIn("'codex'", codex["scope"])

    def test_the_bundle_carries_the_model_discriminator_too(self):
        # `model` became an English sentence for a CLI default in this same
        # release, and only the JSON report grew the machine token that tells it
        # apart from a requested id. The bundle is the shape a consumer actually
        # parses, so it carries it as well.
        for record in _bundle():
            self.assertIn(record["model_source"], {"requested", "cli_default", "unknown", "none"})
            self.assertIsInstance(record["counts_as_review"], bool)
        self.assertFalse(_bundle()[-1]["counts_as_review"])


class TheConfigKey(unittest.TestCase):
    def test_it_defaults_to_off(self):
        self.assertEqual(_from_dict({"agent": []}).ci.min_reviews, 0)

    def test_it_is_read_from_the_ci_table(self):
        config = _from_dict({"jury": {"ci": {"min_reviews": 3}}, "agent": []})
        self.assertEqual(config.ci.min_reviews, 3)

    def test_a_malformed_value_falls_back_to_off(self):
        # The safe direction here is the opposite of `min_vendors`: a typo must
        # not invent a gate that refuses to run the panel at all.
        config = _from_dict({"jury": {"ci": {"min_reviews": "three"}}, "agent": []})
        self.assertEqual(config.ci.min_reviews, 0)

    def test_it_stays_out_of_the_cache_key(self):
        # It changes neither the orchestration nor the findings, and it is
        # re-evaluated on every run including a cache hit — so putting it in the
        # hash would invalidate every cache entry for nothing.
        from ai_jury.config import config_hash

        base = _from_dict({"agent": []})
        gated = _from_dict({"jury": {"ci": {"min_reviews": 3}}, "agent": []})
        self.assertEqual(config_hash(base), config_hash(gated))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
