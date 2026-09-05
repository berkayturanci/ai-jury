"""A reviewer that returns no review is an abstention, not an approval (issue #501).

Observed on keel PR #660 round 3: two of three reviewer slots emitted assistant-style
replies about a flag they saw *inside the reviewed diff*, rather than findings. The
chair noticed and said so in prose — but the machine-readable output still described
a three-agent panel, so nothing downstream could tell "three reviewed, two found
nothing" from "one reviewed, two wandered off".
"""

import unittest

from ai_jury.adapters import AgentResult
from ai_jury.findings import emitted_findings_block
from ai_jury.metadata import REVIEW_STATUSES, panel_accounting, review_status


def _result(agent="claude", vendor="anthropic", *, ok=True, findings=(), structured=False):
    return AgentResult(
        agent=agent,
        vendor=vendor,
        ok=ok,
        output="",
        duration_s=0.1,
        findings=list(findings),
        structured=structured,
    )


class TestEmittedFindingsBlock(unittest.TestCase):
    """The mechanical line, drawn without judging content."""

    def test_a_clean_review_emits_an_empty_block(self):
        text = "I checked the guard, the tests and the migration.\n\n```json\n[]\n```\n"
        self.assertTrue(emitted_findings_block(text))

    def test_a_review_with_findings_emits_a_block(self):
        self.assertTrue(emitted_findings_block('```json\n[{"severity": "minor"}]\n```'))

    def test_prose_with_no_block_is_not_a_review(self):
        # The actual shape from #660: responsive to text in the diff, not to the diff.
        text = (
            "Hi! I notice you mentioned --dangerously-skip-permissions. "
            "I can't help with bypassing safety controls."
        )
        self.assertFalse(emitted_findings_block(text))

    def test_empty_output_is_not_a_review(self):
        self.assertFalse(emitted_findings_block(""))
        self.assertFalse(emitted_findings_block(None))

    def test_a_malformed_block_still_counts_as_an_attempt_to_review(self):
        # Malformed JSON already produces a warning elsewhere; the agent did try to
        # answer in the expected shape, which is not the same as wandering off.
        self.assertTrue(emitted_findings_block("```json\n{not valid\n```"))

    def test_a_non_json_fence_is_not_a_block(self):
        self.assertFalse(emitted_findings_block("```python\nprint(1)\n```"))


class TestReviewStatus(unittest.TestCase):
    def test_findings(self):
        self.assertEqual(review_status(_result(findings=[1], structured=True)), "findings")

    def test_clean_is_a_real_review(self):
        self.assertEqual(review_status(_result(structured=True)), "clean")

    def test_abstained_when_nothing_reviewable_came_back(self):
        self.assertEqual(review_status(_result(structured=False)), "abstained")

    def test_failed_outranks_everything(self):
        self.assertEqual(review_status(_result(ok=False, findings=[1])), "failed")

    def test_every_status_is_in_the_documented_vocabulary(self):
        cases = [
            _result(findings=[1], structured=True),
            _result(structured=True),
            _result(structured=False),
            _result(ok=False),
        ]
        for result in cases:
            self.assertIn(review_status(result), REVIEW_STATUSES)

    def test_findings_outrank_a_missing_block(self):
        # Belt and braces: a finding proves the slot reviewed, whatever the parse flag.
        self.assertEqual(review_status(_result(findings=[1], structured=False)), "findings")


class TestPanelAccounting(unittest.TestCase):
    def test_the_660_round_three_panel(self):
        """One reviewer, two chatbots — reported as three until now."""
        panel = [
            _result("claude", "anthropic", findings=[1], structured=True),
            _result("codex", "openai", structured=False),
            _result("agy", "google", structured=False),
        ]
        self.assertEqual(
            panel_accounting(panel),
            {
                "configured": 3,
                "effective": 1,
                "vendors": 1,
                "abstained": 2,
                "failed": 0,
                "short": True,
                # Every seat that ran gets a ballot record, silent ones included
                # (#700, round 2) — that is how the report can name the agent
                # that returned nothing. These fixtures carry no output at all,
                # so all three are silent.
                "ballots": 3,
                "silent": 3,
                # No ballot records were passed, and the count is not guessed:
                # whether a seat reviewed is a fact about the record it produced,
                # and every guess available from the raw results over-counts.
                "reviews_supplied": None,
                # Every cause but silence is unanswerable without the ballots:
                # which of the five happened is a fact the record carries, and
                # the subtraction that used to stand in for it is the defect
                # #700's fifth round removed. `not_in_change` (#710) joined them
                # additively — a seat that named only things absent from the
                # change is not a seat that named nothing.
                "insubstantial": None,
                "not_in_change": None,
                "refused": None,
                "adapter_failed": None,
                "chair": "",
                "chair_ballot": False,
            },
        )

    def test_the_count_is_read_from_the_ballots_it_is_given(self):
        # The #700 round-2 defect at this line: a seat that answered and named
        # nothing abstained on its ballot and was still counted here, so
        # ``--min-reviews`` could be satisfied by seats that reviewed nothing.
        panel = [
            _result("claude", "anthropic", findings=[1], structured=True),
            _result("codex", "openai", structured=True),
        ]
        ballots = [
            {
                "name": "claude",
                "role": "panelist",
                "scope_substantive": True,
                "verdict": "APPROVE",
            },
            {
                "name": "codex",
                "role": "panelist",
                "scope_substantive": False,
                "verdict": "ABSTAIN",
            },
            {"name": "chair", "role": "chair", "scope_substantive": True, "verdict": "APPROVE"},
        ]
        accounting = panel_accounting(panel, chair="claude", ballots=ballots)
        self.assertEqual(accounting["ballots"], 2)
        self.assertEqual(accounting["reviews_supplied"], 1)
        self.assertTrue(accounting["chair_ballot"])

    def test_a_chairing_agent_that_abstained_supplied_no_review(self):
        # "Did it cast a ballot" is the wrong question now that every seat does.
        panel = [_result("claude", "anthropic", structured=True)]
        ballots = [
            {
                "name": "claude",
                "role": "panelist",
                "scope_substantive": False,
                "verdict": "ABSTAIN",
            },
        ]
        accounting = panel_accounting(panel, chair="claude", ballots=ballots)
        self.assertEqual(accounting["reviews_supplied"], 0)
        self.assertFalse(accounting["chair_ballot"])

    def test_a_full_panel_is_not_short(self):
        panel = [
            _result("claude", "anthropic", findings=[1], structured=True),
            _result("codex", "openai", structured=True),
        ]
        accounting = panel_accounting(panel)
        self.assertFalse(accounting["short"])
        self.assertEqual((accounting["effective"], accounting["vendors"]), (2, 2))

    def test_vendors_counts_distinct_vendors_not_slots(self):
        # Three slots from one vendor are not three perspectives — the number that
        # matters for cross-vendor consensus.
        panel = [
            _result("a", "anthropic", structured=True),
            _result("b", "anthropic", structured=True),
            _result("c", "anthropic", structured=True),
        ]
        accounting = panel_accounting(panel)
        self.assertEqual((accounting["effective"], accounting["vendors"]), (3, 1))

    def test_an_abstaining_vendor_does_not_count_toward_vendors(self):
        panel = [
            _result("claude", "anthropic", structured=True),
            _result("codex", "openai", structured=False),
        ]
        self.assertEqual(panel_accounting(panel)["vendors"], 1)

    def test_a_failed_agent_is_counted_apart_from_an_abstention(self):
        # Different causes, different fixes: one is a broken CLI, the other is a
        # reviewer that ran fine and said nothing reviewable.
        panel = [_result("a", "anthropic", ok=False), _result("b", "openai", structured=False)]
        accounting = panel_accounting(panel)
        self.assertEqual((accounting["failed"], accounting["abstained"]), (1, 1))
        self.assertEqual(accounting["effective"], 0)

    def test_an_empty_panel_is_not_short(self):
        self.assertEqual(
            panel_accounting([]),
            {
                "configured": 0,
                "effective": 0,
                "vendors": 0,
                "abstained": 0,
                "failed": 0,
                "short": False,
                "ballots": 0,
                "silent": 0,
                "reviews_supplied": None,
                "insubstantial": None,
                "not_in_change": None,
                "refused": None,
                "adapter_failed": None,
                "chair": "",
                "chair_ballot": False,
            },
        )

    def test_none_is_tolerated(self):
        self.assertEqual(panel_accounting(None)["configured"], 0)


class TestShortPanelIsStatedInTheReport(unittest.TestCase):
    """Silence was the failure mode, so the human report must say it too (#501)."""

    BASE = {
        "rounds_executed": 2,
        "verify_enabled": True,
        "context_mode": "diff-only",
        "total_wall_clock_s": 205.0,
    }

    def _meta(self, panel, agents):
        return {**self.BASE, "panel": panel, "agents": agents}

    def _agent(self, name, vendor, review_status):
        return {
            "name": name,
            "vendor": vendor,
            "status": "ok",
            "duration_s": 1.0,
            "error_code": None,
            "attempts": 1,
            "review_status": review_status,
        }

    def test_a_short_panel_is_called_out(self):
        from ai_jury.report import _metadata_block

        text = "\n".join(
            _metadata_block(
                self._meta(
                    {
                        "configured": 3,
                        "effective": 1,
                        "vendors": 1,
                        "abstained": 2,
                        "failed": 0,
                        "short": True,
                    },
                    [
                        self._agent("claude", "anthropic", "findings"),
                        self._agent("codex", "openai", "abstained"),
                        self._agent("agy", "google", "abstained"),
                    ],
                )
            )
        )
        self.assertIn("effective panel: 1 of 3", text)
        self.assertIn("2 returned no review", text)
        self.assertIn("An abstention is not an approval", text)
        # And the per-slot status, so the table matches the summary.
        self.assertIn("ok, abstained", text)

    def test_a_full_panel_adds_no_warning_and_no_row_noise(self):
        from ai_jury.report import _metadata_block

        text = "\n".join(
            _metadata_block(
                self._meta(
                    {
                        "configured": 2,
                        "effective": 2,
                        "vendors": 2,
                        "abstained": 0,
                        "failed": 0,
                        "short": False,
                    },
                    [
                        self._agent("claude", "anthropic", "findings"),
                        self._agent("codex", "openai", "findings"),
                    ],
                )
            )
        )
        self.assertNotIn("effective panel", text)
        self.assertNotIn("abstained", text)

    def test_a_clean_reviewer_is_labelled_without_a_panel_warning(self):
        # `clean` is a real review, so it annotates the row but never shortens
        # the panel.
        from ai_jury.report import _metadata_block

        text = "\n".join(
            _metadata_block(
                self._meta(
                    {
                        "configured": 1,
                        "effective": 1,
                        "vendors": 1,
                        "abstained": 0,
                        "failed": 0,
                        "short": False,
                    },
                    [self._agent("claude", "anthropic", "clean")],
                )
            )
        )
        self.assertIn("ok, clean", text)
        self.assertNotIn("effective panel", text)

    def test_metadata_without_a_panel_block_still_renders(self):
        # Replayed outcomes from an older schema carry no panel key.
        from ai_jury.report import _metadata_block

        text = "\n".join(
            _metadata_block(
                {
                    **self.BASE,
                    "agents": [
                        {
                            "name": "claude",
                            "vendor": "anthropic",
                            "status": "ok",
                            "duration_s": 1.0,
                            "error_code": None,
                            "attempts": 1,
                        },
                    ],
                }
            )
        )
        self.assertIn("claude", text)
        self.assertNotIn("effective panel", text)
