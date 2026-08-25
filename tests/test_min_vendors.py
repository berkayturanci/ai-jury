"""A panel that collapsed to one vendor can be made to fail the run.

#625: `--strict` fails when a configured agent CLI is **missing**. It does not
fail when an agent is present, probes clean, and then returns nothing — which
is how a three-vendor panel silently becomes one. Observed on a real run:

    effective panel: 1 of 3 reviewer(s) (0 returned no review, 2 failed)
    claude — failed (OAuth session expired)
    codex  — ok
    agy    — failed (launcher ate the prompt)

`jury --doctor` had reported all three `[available]` with `probe: ok`
beforehand, and was right: they were installed. They still did not review, and
the run exited 0 with a verdict.

So the tests below use agents that are **configured and available but return
nothing**, never a missing CLI — the missing-CLI case is what `--strict`
already covers and would pass whatever this change does.

`--min-vendors` is opt-in (default 0) because the policy question is real: on
a flaky vendor CLI, failing closed turns a degraded second opinion into no
second opinion. That default is deliberately not decided here.
"""

from __future__ import annotations

import unittest

from ai_jury.metadata import panel_accounting


class _Review:
    """The shape `review_status` actually reads: ``ok``, ``findings``, ``structured``.

    Built from that function rather than guessed. A stub that does not match it
    would make every assertion below vacuous — the first cut of this file used
    a `status` attribute nothing reads, and `panel_accounting` scored every
    review as `failed`.
    """

    def __init__(self, status: str, vendor: str):
        self.vendor = vendor
        self.ok = status != "failed"
        self.findings = [object()] if status == "findings" else []
        # `clean` means "emitted an empty findings block": examined, found
        # nothing. `abstained` means nothing reviewable came back at all.
        self.structured = status == "clean"


def _panel(*pairs):
    return panel_accounting([_Review(status, vendor) for status, vendor in pairs])


class ThePanelAccountingCountsVendorsNotSlots(unittest.TestCase):
    def test_a_healthy_three_vendor_panel(self):
        panel = _panel(("findings", "anthropic"), ("clean", "openai"), ("clean", "google"))
        self.assertEqual(panel["vendors"], 3)
        self.assertEqual(panel["effective"], 3)

    def test_the_observed_collapse(self):
        """The run that motivated this: two agents failed, one reviewed."""
        panel = _panel(("failed", "anthropic"), ("findings", "openai"), ("failed", "google"))
        self.assertEqual(panel["vendors"], 1)
        self.assertEqual(panel["effective"], 1)
        self.assertEqual(panel["configured"], 3)

    def test_three_slots_from_one_vendor_are_one_perspective(self):
        """The distinction the whole flag rests on.

        A run can be at full effective strength and still have formed no
        cross-vendor consensus. Counting slots would report 3 and be wrong.
        """
        panel = _panel(("findings", "openai"), ("clean", "openai"), ("clean", "openai"))
        self.assertEqual(panel["effective"], 3)
        self.assertEqual(panel["vendors"], 1)

    def test_an_abstention_does_not_count_as_a_vendor(self):
        """ "An abstention is not an approval" — and it is not a perspective either."""
        panel = _panel(("findings", "anthropic"), ("abstained", "openai"))
        self.assertEqual(panel["vendors"], 1)


class TheFlagIsOptInAndDistinguishable(unittest.TestCase):
    def setUp(self):
        from pathlib import Path

        self.cli = (Path(__file__).parent.parent / "src" / "ai_jury" / "cli.py").read_text(
            encoding="utf-8"
        )
        self.code = [
            line
            for line in self.cli.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

    def test_the_default_is_off(self):
        """Adding a gate that fires by default would change every caller's run."""
        self.assertIn('"--min-vendors",', [line.strip() for line in self.code])
        self.assertTrue(
            any(line.strip() == "default=0," for line in self.code),
            "--min-vendors must default to 0, i.e. disabled",
        )

    def test_the_check_is_guarded_by_the_flag(self):
        """With the flag unset the run must reach its normal exit path."""
        self.assertIn('if getattr(args, "min_vendors", 0) > 0:', self.cli)

    def test_the_exit_code_is_not_the_findings_one(self):
        """A collapsed panel and a blocking finding are different outcomes.

        `evaluate_ci` returns 0 or 1, so reusing 1 would make a caller unable to
        tell "the reviewers disagreed with you" from "the reviewers never ran".
        """
        self.assertIn("ci_exit = 3", self.cli)
        self.assertNotIn("ci_exit = 1", self.cli)

    def test_it_counts_vendors_rather_than_reviews(self):
        self.assertIn('panel_accounting(outcome.reviews).get("vendors", 0)', self.cli)
