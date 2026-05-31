"""Tests for the optional repository review policy (#8)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_review_council import policy as policy_mod  # noqa: E402
from agent_review_council import prompts  # noqa: E402
from agent_review_council.config import DEFAULT_CONFIG, _from_dict  # noqa: E402
from agent_review_council.orchestrator import run_council  # noqa: E402
from agent_review_council.policy import (  # noqa: E402
    PolicyError,
    ReviewPolicy,
    SeverityOverride,
    load_policy,
    render_policy_section,
)
from agent_review_council.report import render  # noqa: E402

SAMPLE_DIFF = (
    "diff --git a/src/example.py b/src/example.py\n"
    "@@ -1,3 +1,4 @@\n"
    "+def parse(x):\n"
    "+    return int(x)\n"
)

VALID_POLICY = """\
high_risk_paths = ["src/auth/**", "infra/**"]
focus_areas = ["Backward compatibility", "Input validation"]
forbidden_output = ["Do not leak secrets."]
checklist = "Check error paths.\\nCheck docs."
doc_links = ["docs/architecture.md"]

[[severity_overrides]]
glob = "src/auth/**"
severity = "blocker"
"""


def _config():
    return _from_dict(DEFAULT_CONFIG)


class LoadPolicyTest(unittest.TestCase):
    def test_valid_file_returns_populated_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.toml"
            path.write_text(VALID_POLICY, encoding="utf-8")
            loaded = load_policy(path)

        self.assertIsInstance(loaded, ReviewPolicy)
        assert loaded is not None
        self.assertEqual(loaded.high_risk_paths, ["src/auth/**", "infra/**"])
        self.assertEqual(
            loaded.focus_areas, ["Backward compatibility", "Input validation"]
        )
        self.assertEqual(loaded.forbidden_output, ["Do not leak secrets."])
        self.assertEqual(loaded.doc_links, ["docs/architecture.md"])
        self.assertIn("Check error paths.", loaded.checklist)
        self.assertEqual(
            loaded.severity_overrides,
            [SeverityOverride(glob="src/auth/**", severity="blocker")],
        )

    def test_missing_explicit_path_raises(self):
        with self.assertRaises(PolicyError):
            load_policy(Path("/no/such/policy-file.toml"))

    def test_no_discovered_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path.cwd()
            try:
                os.chdir(tmp)
                self.assertIsNone(load_policy(None))
            finally:
                os.chdir(cwd)

    def test_discovery_finds_default_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path.cwd()
            try:
                os.chdir(tmp)
                Path(".council").mkdir()
                Path(".council/policy.toml").write_text(VALID_POLICY, encoding="utf-8")
                self.assertIsNotNone(load_policy(None))
            finally:
                os.chdir(cwd)

    def test_discovery_finds_flat_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path.cwd()
            try:
                os.chdir(tmp)
                Path("council-policy.toml").write_text(VALID_POLICY, encoding="utf-8")
                self.assertIsNotNone(load_policy(None))
            finally:
                os.chdir(cwd)

    def test_malformed_toml_raises_policy_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.toml"
            path.write_text("this = = invalid", encoding="utf-8")
            with self.assertRaises(PolicyError):
                load_policy(path)

    def test_wrong_field_type_raises_policy_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.toml"
            path.write_text('high_risk_paths = "not-a-list"', encoding="utf-8")
            with self.assertRaises(PolicyError):
                load_policy(path)

    def test_malformed_severity_override_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.toml"
            path.write_text(
                "[[severity_overrides]]\nglob = 1\nseverity = 2\n", encoding="utf-8"
            )
            with self.assertRaises(PolicyError):
                load_policy(path)

    def test_example_policy_file_loads(self):
        # The shipped example must be valid and generic (project-agnostic).
        example = Path(__file__).resolve().parent.parent / "examples" / "policy.toml"
        loaded = load_policy(example)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertFalse(loaded.is_empty())


class RenderPolicySectionTest(unittest.TestCase):
    def test_none_renders_sentinel(self):
        self.assertEqual(render_policy_section(None), policy_mod.SENTINEL)

    def test_empty_policy_renders_sentinel(self):
        self.assertEqual(render_policy_section(ReviewPolicy()), policy_mod.SENTINEL)

    def test_populated_policy_includes_all_fields(self):
        pol = ReviewPolicy(
            high_risk_paths=["src/auth/**"],
            focus_areas=["Backward compatibility"],
            forbidden_output=["No secrets"],
            severity_overrides=[SeverityOverride(glob="docs/**", severity="nit")],
            checklist="Check error paths.",
            doc_links=["docs/architecture.md"],
        )
        rendered = render_policy_section(pol)
        for fragment in (
            "src/auth/**",
            "Backward compatibility",
            "No secrets",
            "docs/**",
            "nit",
            "Check error paths.",
            "docs/architecture.md",
        ):
            self.assertIn(fragment, rendered)
        self.assertNotEqual(rendered, policy_mod.SENTINEL)


class PromptInclusionTest(unittest.TestCase):
    """The policy must reach the REVIEW prompt as a trusted, separated section."""

    def _review_prompt(self, policy):
        return prompts.REVIEW.format(
            name="Alpha",
            notice=prompts._UNTRUSTED_NOTICE,
            policy=render_policy_section(policy),
            context="the context",
            diff="the diff",
        )

    def test_policy_text_appears_in_review_prompt(self):
        pol = ReviewPolicy(focus_areas=["Distinctive focus marker XYZ"])
        prompt = self._review_prompt(pol)
        self.assertIn("Distinctive focus marker XYZ", prompt)
        self.assertIn("maintainer-provided, TRUSTED", prompt)
        # The trusted policy section precedes the untrusted PR CONTEXT / DIFF
        # data sections (the untrusted data lives at the end of the prompt).
        self.assertLess(
            prompt.index("Distinctive focus marker XYZ"),
            prompt.index("=== PR CONTEXT"),
        )

    def test_policy_is_outside_untrusted_fences(self):
        pol = ReviewPolicy(focus_areas=["Distinctive focus marker XYZ"])
        prompt = self._review_prompt(pol)
        # The untrusted data lives strictly between the real opening/closing
        # sentinels at the END of the prompt. Use rindex so we anchor on the
        # actual data fences, not the example sentinels quoted in the notice.
        ctx_open = prompt.index("<<<UNTRUSTED_CONTEXT\n")
        diff_close = prompt.rindex("UNTRUSTED_DIFF>>>")
        # The policy marker must appear before any untrusted data block begins.
        self.assertLess(
            prompt.index("Distinctive focus marker XYZ"), ctx_open
        )
        # And, defensively, it must not appear anywhere within the data region.
        self.assertNotIn(
            "Distinctive focus marker XYZ", prompt[ctx_open:diff_close]
        )

    def test_no_policy_uses_sentinel(self):
        prompt = self._review_prompt(None)
        self.assertIn(policy_mod.SENTINEL, prompt)


class RunCouncilWithPolicyTest(unittest.TestCase):
    def test_run_with_policy_succeeds(self):
        pol = ReviewPolicy(focus_areas=["Something"])
        outcome = run_council(_config(), SAMPLE_DIFF, mock=True, policy=pol, seed=1)
        self.assertEqual(len(outcome.reviews), 3)
        self.assertTrue(all(r.ok for r in outcome.reviews))

    def test_run_without_policy_succeeds(self):
        outcome = run_council(_config(), SAMPLE_DIFF, mock=True, seed=1)
        self.assertEqual(len(outcome.reviews), 3)

    def test_policy_does_not_change_mock_report(self):
        config = _config()
        with_p = run_council(
            config, SAMPLE_DIFF, mock=True, policy=ReviewPolicy(focus_areas=["X"]), seed=3
        )
        without_p = run_council(config, SAMPLE_DIFF, mock=True, seed=3)
        self.assertEqual(
            render(with_p.reviews, with_p.debate, with_p.synthesis, chair=with_p.chair),
            render(
                without_p.reviews,
                without_p.debate,
                without_p.synthesis,
                chair=without_p.chair,
            ),
        )


if __name__ == "__main__":
    unittest.main()
