"""Tiered routing is a pure function of the bench, the usable seats and the risk band (#714)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_jury import routing  # noqa: E402
from ai_jury.config import AgentSpec  # noqa: E402
from ai_jury.consensus import FindingGroup  # noqa: E402
from ai_jury.findings import Finding  # noqa: E402


def _bench(*seats):
    """``(name, vendor, tier)`` triples → specs in config order."""
    return [AgentSpec(name, vendor, command=name, tier=tier) for name, vendor, tier in seats]


BENCH = _bench(
    ("claude", "anthropic", "frontier"),
    ("gpt", "openai", "frontier"),
    ("cheap", "google", "economical"),
    ("local", "local", "economical"),
)
NAMES = [s.name for s in BENCH]


def _group(severity):
    return FindingGroup(
        representative=Finding(severity=severity, file="a.py", claim="x"), severity=severity
    )


class ThePanelFollowsTheRiskBand(unittest.TestCase):
    def test_a_high_risk_diff_seats_everybody(self):
        plan = routing.plan_panel(BENCH, NAMES, "high", chair="claude")
        self.assertEqual(plan.panel, NAMES)
        self.assertEqual(plan.benched, [])
        self.assertIsNone(plan.anchor)
        self.assertIn("full panel", plan.reason)

    def test_a_routine_diff_seats_the_economical_seats_and_one_anchor(self):
        for risk in ("low", "medium"):
            with self.subTest(risk=risk):
                plan = routing.plan_panel(BENCH, NAMES, risk, chair="claude")
                self.assertEqual(plan.panel, ["claude", "cheap", "local"])
                self.assertEqual(plan.benched, ["gpt"])
                self.assertEqual(plan.anchor, "claude")
                self.assertEqual(plan.mode, "tiered")
                self.assertEqual(plan.risk, risk)

    def test_the_anchor_is_the_chair_when_it_is_frontier_else_the_first_frontier(self):
        as_chair = routing.plan_panel(BENCH, NAMES, "low", chair="gpt")
        self.assertEqual(as_chair.anchor, "gpt")
        self.assertEqual(as_chair.benched, ["claude"])
        economical_chair = routing.plan_panel(BENCH, NAMES, "low", chair="cheap")
        self.assertEqual(economical_chair.anchor, "claude")
        rotating = routing.plan_panel(BENCH, NAMES, "low", chair="rotate")
        self.assertEqual(rotating.anchor, "claude")

    def test_an_unknown_band_is_treated_as_high(self):
        plan = routing.plan_panel(BENCH, NAMES, "weird", chair="claude")
        self.assertEqual(plan.panel, NAMES)
        self.assertIn("treated as high", plan.reason)

    def test_the_panel_and_the_bench_keep_config_order(self):
        bench = _bench(
            ("cheap", "google", "economical"),
            ("claude", "anthropic", "frontier"),
            ("gpt", "openai", "frontier"),
            ("grok", "xai", "frontier"),
        )
        plan = routing.plan_panel(bench, [s.name for s in bench], "low", chair="gpt")
        self.assertEqual(plan.panel, ["cheap", "gpt"])
        self.assertEqual(plan.benched, ["claude", "grok"])

    def test_only_usable_seats_are_planned(self):
        plan = routing.plan_panel(BENCH, ["claude", "cheap"], "low", chair="gpt")
        # gpt is configured chair but not usable: claude anchors, nobody is benched.
        self.assertEqual(plan.panel, ["claude", "cheap"])
        self.assertEqual(plan.anchor, "claude")
        self.assertEqual(plan.benched, [])


class NothingToSaveOrNothingToAnchorWith(unittest.TestCase):
    def test_no_economical_seat_means_the_full_panel(self):
        bench = _bench(("claude", "anthropic", "frontier"), ("gpt", "openai", "frontier"))
        plan = routing.plan_panel(bench, ["claude", "gpt"], "low", chair="claude")
        self.assertEqual(plan.panel, ["claude", "gpt"])
        self.assertIn("no economical seat", plan.reason)

    def test_no_frontier_seat_means_the_full_panel(self):
        bench = _bench(("cheap", "google", "economical"), ("local", "local", "economical"))
        plan = routing.plan_panel(bench, ["cheap", "local"], "low", chair="cheap")
        self.assertEqual(plan.panel, ["cheap", "local"])
        self.assertIn("no frontier seat", plan.reason)


class TheFloorsAlwaysHold(unittest.TestCase):
    def test_min_vendors_unbenches_the_seat_that_brings_a_new_vendor(self):
        bench = _bench(
            ("claude", "anthropic", "frontier"),
            ("gpt", "openai", "frontier"),
            ("cheap", "anthropic", "economical"),
        )
        names = [s.name for s in bench]
        without = routing.plan_panel(bench, names, "low", chair="claude", min_vendors=0)
        self.assertEqual(without.panel, ["claude", "cheap"])
        with_floor = routing.plan_panel(bench, names, "low", chair="claude", min_vendors=2)
        self.assertEqual(with_floor.panel, ["claude", "gpt", "cheap"])
        self.assertEqual(with_floor.benched, [])
        self.assertIn("min_vendors=2", with_floor.reason)

    def test_min_vendors_prefers_a_new_vendor_over_config_order(self):
        bench = _bench(
            ("claude", "anthropic", "frontier"),
            ("opus", "anthropic", "frontier"),
            ("gpt", "openai", "frontier"),
            ("cheap", "anthropic", "economical"),
        )
        names = [s.name for s in bench]
        plan = routing.plan_panel(bench, names, "low", chair="claude", min_vendors=2)
        self.assertIn("gpt", plan.panel)
        self.assertNotIn("opus", plan.panel)

    def test_a_floor_the_bench_cannot_reach_takes_everyone_and_stops(self):
        bench = _bench(("claude", "anthropic", "frontier"), ("cheap", "anthropic", "economical"))
        plan = routing.plan_panel(bench, ["claude", "cheap"], "low", chair="claude", min_vendors=3)
        self.assertEqual(plan.panel, ["claude", "cheap"])

    def test_min_reviews_unbenches_in_config_order(self):
        plan = routing.plan_panel(BENCH, NAMES, "low", chair="claude", min_reviews=4)
        self.assertEqual(plan.panel, NAMES)
        self.assertIn("min_reviews=4", plan.reason)


class EscalationReadsTheGroups(unittest.TestCase):
    def test_a_critical_or_major_group_escalates(self):
        for severity in ("critical", "major"):
            with self.subTest(severity=severity):
                escalated, why = routing.should_escalate([_group("minor"), _group(severity)])
                self.assertTrue(escalated)
                self.assertIn(severity, why)

    def test_the_reason_names_the_worst_severity(self):
        _, why = routing.should_escalate([_group("major"), _group("critical")])
        self.assertIn("critical-or-worse", why)

    def test_minor_findings_do_not(self):
        escalated, why = routing.should_escalate([_group("minor"), _group("nit"), _group("info")])
        self.assertFalse(escalated)
        self.assertIn("no critical or major", why)

    def test_no_groups_do_not(self):
        self.assertFalse(routing.should_escalate([])[0])


class TheRecordAndTheLogLine(unittest.TestCase):
    def test_the_standard_plan_seats_everybody_and_says_so(self):
        plan = routing.standard_plan(["a", "b"])
        self.assertEqual(plan.as_dict()["mode"], "standard")
        self.assertEqual(plan.panel, ["a", "b"])
        self.assertEqual(routing.describe(plan), "routing: standard (full panel)")

    def test_the_plan_dict_has_every_key_the_report_documents(self):
        plan = routing.plan_panel(BENCH, NAMES, "low", chair="claude")
        self.assertEqual(
            set(plan.as_dict()),
            {
                "mode",
                "risk",
                "panel",
                "benched",
                "anchor",
                "reason",
                "escalated",
                "escalation_reason",
            },
        )
        self.assertIn("tiered routing: risk=low", routing.describe(plan))
        self.assertIn("panel claude, cheap, local", routing.describe(plan))

    def test_frontier_names_are_the_usable_frontier_seats_in_config_order(self):
        self.assertEqual(
            routing.frontier_names(BENCH, ["local", "gpt", "claude"]), ["claude", "gpt"]
        )


if __name__ == "__main__":
    unittest.main()
