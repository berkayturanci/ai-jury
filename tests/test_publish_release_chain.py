"""A release is one write to `main`, and the thing published is then installed.

`Formula/ai-jury.rb` used to be committed here, naming an sdist url and a sha256.
Neither is knowable until the tag is pushed — PyPI's paths are content-addressed
and the digest belongs to an artifact that does not exist yet — so every release
had to write to `main` a second time to make the file true. That write was tried
four ways in three days: a direct push branch protection refused and `|| true`
swallowed (#633), a loud `::error::` nobody acted on (#638), a pull request whose
refspec could not be created from a detached HEAD (#641), and auto-merge on that
pull request (#643). Seven releases in a row needed a fix-forward commit.

#645 removes the requirement rather than the latest symptom: the pair is never
committed. `publish.yml` renders `packaging/homebrew/ai-jury.rb.template` after
the upload, from what PyPI reports, and publishes the result to the GitHub
Release and to the tap — neither of which is this repository's `main`.

The other half of #666 is that nothing used to install what was published. The
`verify` job does, from the index, into a clean virtualenv.

These tests pin both shapes. They assert over the *code* in the workflow with
comment lines removed first: the prose in that file discusses at length the
pushes and pull requests it no longer performs, and a grep would match that
discussion happily and report the old behaviour as guarded.

Read without a YAML parser, for the reason given in `test_github_action.py`:
ai-jury declares `dependencies = []` and three dev tools, and a test is not a
good enough reason to make PyYAML the exception. The cost is that a restructured
workflow could go unrecognised and quietly assert nothing, so the scan below is
checked against the file's own text before anything is concluded from it.
"""

from __future__ import annotations

import re
import unittest
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish.yml"

PUBLISH_JOB = "build-n-publish"
VERIFY_JOB = "verify"
TEMPLATE = "packaging/homebrew/ai-jury.rb.template"
#: The one wait both jobs run, extracted so there is no second copy (#694).
WAIT_SCRIPT = ".github/scripts/wait-for-pypi-dists.sh"

#: Every command that opens a network connection, mapped to the flags that bound
#: one in time. An empty tuple means the tool has no timeout of its own — `gh`
#: has neither a flag nor an environment variable for it — so the only bound
#: available is a `timeout` wrapper, and that is what the scan then demands.
#:
#: `curl` must carry both: `--connect-timeout` alone leaves the peer that
#: completes the handshake and then stops writing, which is the exact shape that
#: held the render step open, and `--max-time` alone leaves a connect that never
#: completes to the kernel's own patience.
NETWORK_TOOLS: dict[str, tuple[str, ...]] = {
    "curl": ("--connect-timeout", "--max-time"),
    "wget": ("--timeout",),
    "pip": ("--timeout",),
    "pip3": ("--timeout",),
    "gh": (),
    "twine": (),
    "npm": (),
    "npx": (),
    "brew": (),
}

#: `pip` and `git` reach the network in only some of their moods. Both the
#: network set and the local one are named, because the scan has to be able to
#: tell "this is a local subcommand" from "this is a subcommand I do not know",
#: and only the first of those is safe to pass over.
PIP_NETWORK_SUBCOMMANDS = frozenset({"install", "download", "wheel", "index", "search"})
PIP_SUBCOMMANDS = PIP_NETWORK_SUBCOMMANDS | frozenset(
    {"show", "list", "freeze", "uninstall", "check", "config", "cache", "debug", "hash", "inspect"}
)
GIT_NETWORK_SUBCOMMANDS = frozenset({"clone", "fetch", "pull", "push", "ls-remote", "submodule"})
GIT_SUBCOMMANDS = GIT_NETWORK_SUBCOMMANDS | frozenset(
    {"init", "add", "commit", "status", "diff", "log", "checkout", "rev-parse", "tag", "config"}
)

#: Markers the lexer emits: a command substitution opening — the word after it
#: is a command name and not an argument — and the end of one command.
_OPENS = "\x00"
_BREAK = "\x01"

#: Words that stand in front of the command a fragment actually runs.
_LEADING_KEYWORDS = frozenset(
    {"if", "then", "else", "elif", "fi", "while", "until", "for", "in", "do", "done", "!", "{", "("}
)
_ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")

#: Commands whose argument is itself a command — `timeout` is the only bound
#: `gh` can be given, so a scan that could not see through it would report every
#: wrapped call as unbounded.
_WRAPPERS = frozenset({"timeout", "env", "nice", "command", "exec", "stdbuf"})

#: `60`, `1m`, `30s` — a wrapper's duration, which is not its command.
_DURATION = re.compile(r"^\d+(\.\d+)?[smhd]?$")


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def block_after(lines: list[str], index: int) -> list[str]:
    """The run of lines indented deeper than ``lines[index]``, blanks included."""
    indent = indent_of(lines[index])
    body: list[str] = []
    for line in lines[index + 1 :]:
        if line.strip() and indent_of(line) <= indent:
            break
        body.append(line)
    return body


def job_body(lines: list[str], name: str) -> list[str]:
    """The body of the named top-level job."""
    at = [i for i, line in enumerate(lines) if line.strip() == f"{name}:" and indent_of(line) == 2]
    assert len(at) == 1, f"expected one `{name}:` job, found {len(at)}"
    return block_after(lines, at[0])


def shell(block: list[str]) -> str:
    """Every ``run:`` body in ``block``, comment lines removed.

    Both spellings are collected: the folded ``run: |`` blocks and the
    one-liners. A restructure that turned a block into a one-liner would
    otherwise drop it from the scan silently, and every ``assertNotIn`` below
    would go on passing.
    """
    code: list[str] = []
    for index, line in enumerate(block):
        stripped = line.strip()
        if stripped == "run: |":
            code.extend(block_after(block, index))
        elif stripped.startswith("run: "):
            code.append(stripped[len("run: ") :])
    return "\n".join(line for line in code if not line.strip().startswith("#"))


@dataclass(frozen=True)
class NetworkCall:
    """One command in a `run:` block that opens a connection, and its bound."""

    job: str
    tool: str
    command: str
    bounded: bool

    def why(self) -> str:
        wanted = NETWORK_TOOLS.get(self.tool, ())
        need = " and ".join(wanted) if wanted else "a `timeout` wrapper"
        return f"{self.job}: `{self.command}` carries no {need}"


def _lex(code: str) -> list[str]:
    """Words, plus a marker where a command substitution opens and one where a
    command ends.

    Quote-aware, which is the difference between a scan worth having and one
    worth switching off. `;`, `|` and `&&` inside a quoted string do not end a
    command — this workflow writes ``"::error::… does not serve …; brew upgrade
    will not see this release"``, and a naive split turns the tail of that
    sentence into an unbounded `brew` call. Nor does an *escaped* backtick open
    a subshell: the issue body this job files quotes `pip install ai-jury==…` in
    Markdown, and the escape is what says so. But a real ``"$(timeout 60 gh …)"``
    is a command, quoted or not, so substitutions are entered, not skipped.
    """
    out: list[str] = []
    word: list[str] = []
    quote: str | None = None
    #: The quoting context each open substitution suspended, and how it opened.
    #: `"$(timeout 60 gh …)"` restarts quoting inside the parentheses — without
    #: that, every word of the substitution runs together into one, and the `gh`
    #: it contains is never seen as a command.
    suspended: list[tuple[str, str | None]] = []
    index, end = 0, len(code)

    def flush() -> None:
        if word:
            out.append("".join(word))
            word.clear()

    while index < end:
        char = code[index]
        if quote == "'":  # nothing is special inside single quotes, not even \
            if char == "'":
                quote = None
            else:
                word.append(char)
            index += 1
            continue
        if char == "\\" and index + 1 < end:  # an escaped character is literal
            word.append(code[index + 1])
            index += 2
            continue
        if char == "`" and suspended and suspended[-1][0] == "`":
            flush()
            _, quote = suspended.pop()
            index += 1
            continue
        if code.startswith("$(", index) or char == "`":
            flush()
            out.append(_OPENS)
            suspended.append((char, quote))
            quote = None
            index += 2 if char == "$" else 1
            continue
        if quote == '"':
            if char == '"':
                quote = None
            else:
                word.append(char)
            index += 1
            continue
        if char in "'\"":
            quote = char
            index += 1
            continue
        if code.startswith("&&", index) or code.startswith("||", index):
            flush()
            out.append(_BREAK)
            index += 2
            continue
        if char in ";|\n":
            flush()
            out.append(_BREAK)
            index += 1
            continue
        if char == ")":
            flush()
            if suspended and suspended[-1][0] == "$":
                _, quote = suspended.pop()
            index += 1
            continue
        if char.isspace():
            flush()
            index += 1
            continue
        word.append(char)
        index += 1
    flush()
    return out


def _commands(code: str) -> list[list[str]]:
    """One shell command per element, as its words. Continuations joined first."""
    commands: list[list[str]] = []
    current: list[str] = []
    for token in _lex(code.replace("\\\n", " ")):
        if token == _BREAK:
            commands.append(current)
            current = []
        else:
            current.append(token)
    commands.append(current)
    return [command for command in commands if any(t != _OPENS for t in command)]


def _command_positions(tokens: list[str]) -> list[int]:
    """The indices at which a command *name* stands, rather than an argument.

    Position is the whole point. Scanning for a tool's name anywhere in a
    command would find `brew` in `echo "…brew upgrade will not see this…"` and
    `pip` in the issue body this workflow writes when a release breaks — prose
    about the network, reported as unbounded requests to it, which is how a
    scanner earns being switched off. A command name stands at the head of a
    fragment, or just inside a command substitution, or after a wrapper like
    `timeout` and its duration; nowhere else.
    """
    positions: list[int] = []
    expect, after_wrapper = True, False
    for index, token in enumerate(tokens):
        if token == _OPENS:
            expect, after_wrapper = True, False
            continue
        if not expect:
            continue
        name = token.rsplit("/", 1)[-1]
        if after_wrapper:
            # `timeout -k 5 60 gh …`: its own options and its duration first.
            if token.startswith("-") or _DURATION.match(token):
                continue
            after_wrapper = False
        elif name in _LEADING_KEYWORDS or _ASSIGNMENT.match(token):
            continue  # still looking for the command this fragment runs
        positions.append(index)
        if name in _WRAPPERS:
            after_wrapper = True
            continue
        expect = False
    return positions


def _subcommand(tokens: list[str], index: int, known: frozenset[str]) -> str | None:
    """The first recognised subcommand after `tokens[index]`, if any.

    Searched rather than taken positionally: `pip --python <path> install` puts
    two tokens between the tool and its verb.
    """
    return next((token for token in tokens[index + 1 :] if token in known), None)


def network_calls(code: str, job: str) -> list[NetworkCall]:
    """Every network client invoked in one job's shell, and whether it is bounded.

    A tool is located by the basename of the token in command position, so
    `/tmp/verify-venv/bin/python -m pip` and a bare `pip` are the same client.
    `pip` and `git` are network clients only in some of their moods, and the
    mood is the subcommand: `pip show` reads an installed dist and `git status`
    reads a work tree, and demanding a timeout on either would train the reader
    to ignore this scan.
    """
    calls: list[NetworkCall] = []
    for tokens in _commands(code):
        for head in _command_positions(tokens):
            index, name = head, tokens[head].rsplit("/", 1)[-1]
            if name.startswith("python") and tokens[index + 1 : index + 2] == ["-m"]:
                index, name = index + 2, tokens[index + 2] if len(tokens) > index + 2 else ""
            if name in ("pip", "pip3"):
                if _subcommand(tokens, index, PIP_SUBCOMMANDS) not in PIP_NETWORK_SUBCOMMANDS:
                    continue
            elif name == "git":
                if _subcommand(tokens, index, GIT_SUBCOMMANDS) not in GIT_NETWORK_SUBCOMMANDS:
                    continue
            elif name not in NETWORK_TOOLS:
                continue
            wrapped = any(token.rsplit("/", 1)[-1] in _WRAPPERS for token in tokens[:index])
            wanted = NETWORK_TOOLS.get(name, ())
            bounded = wrapped or (bool(wanted) and all(flag in tokens for flag in wanted))
            shown = " ".join(token for token in tokens if token != _OPENS)
            calls.append(NetworkCall(job=job, tool=name, command=shown, bounded=bounded))
    return calls


def scan(source: str) -> list[NetworkCall]:
    """Every network call in both jobs of a `publish.yml`, real or mutated."""
    lines = source.splitlines()
    return [
        call
        for job in (PUBLISH_JOB, VERIFY_JOB)
        for call in network_calls(shell(job_body(lines, job)), job)
    ]


def permissions(block: list[str]) -> list[str]:
    at = [i for i, line in enumerate(block) if line.strip() == "permissions:"]
    assert len(at) == 1, f"expected one permissions block, found {len(at)}"
    return [line.strip() for line in block_after(block, at[0]) if line.strip()]


class WorkflowScan(unittest.TestCase):
    """Shared hand-parse of `publish.yml`."""

    @classmethod
    def setUpClass(cls):
        lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
        cls.lines = lines
        cls.publish = job_body(lines, PUBLISH_JOB)
        cls.verify = job_body(lines, VERIFY_JOB)
        cls.publish_code = shell(cls.publish)
        cls.verify_code = shell(cls.verify)
        cls.publish_permissions = permissions(cls.publish)
        cls.verify_permissions = permissions(cls.verify)


class TheScanReadSomething(WorkflowScan):
    """Vacuity: an empty scan satisfies every `assertNotIn` in this module.

    The hand-read is cheap and the workflow is free to be restructured, so the
    thing worth refusing is a suite that passes because it read nothing.
    """

    def test_both_jobs_have_bodies(self):
        self.assertGreater(len(self.publish_code.splitlines()), 40)
        self.assertGreater(len(self.verify_code.splitlines()), 20)

    def test_no_comment_survived_the_stripper(self):
        for code in (self.publish_code, self.verify_code):
            self.assertEqual(
                [line for line in code.splitlines() if line.strip().startswith("#")], []
            )

    def test_the_permission_blocks_were_found(self):
        self.assertTrue(self.publish_permissions)
        self.assertTrue(self.verify_permissions)


class ReleasingWritesNothingToThisRepository(WorkflowScan):
    """The whole point of #645: the release path never touches `main` again."""

    def test_the_release_commits_nothing(self):
        """A commit is the first half of every mechanism that failed."""
        self.assertNotIn("git commit", self.publish_code)
        self.assertNotIn("git add", self.publish_code)

    def test_the_release_pushes_nothing(self):
        """#633: `git push origin HEAD:main || true` reported a refusal as success.

        #641: the replacement could not create its branch from a detached HEAD.
        Both are gone with the push itself, not patched again.
        """
        self.assertNotIn("git push", self.publish_code)
        self.assertNotIn("HEAD:main", self.publish_code)
        self.assertNotIn("HEAD:refs/heads/", self.publish_code)

    def test_the_release_opens_no_pull_request(self):
        """#638/#643: one machine-written pull request per release, to be landed.

        It needed the *Allow GitHub Actions to create and approve pull requests*
        repository setting and an evidence gate with no bot exemption, so it
        waited for a person exactly as often as the notice it replaced did.
        """
        self.assertNotIn("gh pr create", self.publish_code)
        self.assertNotIn("gh pr merge", self.publish_code)

    def test_no_job_may_open_a_pull_request(self):
        """The permission is the standing capability, and it is no longer needed.

        Asserted separately from the absence of `gh pr create`: a permission left
        behind is the part of a removal nobody notices, and it sits far from the
        step that once used it.
        """
        for name, granted in (
            (PUBLISH_JOB, self.publish_permissions),
            (VERIFY_JOB, self.verify_permissions),
        ):
            with self.subTest(job=name):
                self.assertEqual(
                    [p for p in granted if p.startswith("pull-requests")],
                    [],
                    f"{name} may still open pull requests: {granted}",
                )

    def test_the_formula_is_rendered_from_a_template(self):
        """Nothing in the repository carries a url/digest pair it cannot verify."""
        self.assertIn(TEMPLATE, self.publish_code)
        self.assertFalse(
            (REPO_ROOT / "Formula" / "ai-jury.rb").exists(),
            "a committed formula is a digest this repository cannot know yet",
        )
        self.assertTrue((REPO_ROOT / TEMPLATE).exists(), f"{TEMPLATE} is missing")

    def test_the_digest_is_checked_against_the_artifact_before_publishing(self):
        """A tap one release behind still installs; a tap holding a wrong digest does not."""
        self.assertIn("sha256sum published-sdist.tar.gz", self.publish_code)
        self.assertIn("refusing to publish a formula", self.publish_code)

    def test_an_unrendered_placeholder_stops_the_release(self):
        """`sed` reports success whether or not it substituted anything."""
        self.assertIn("unrendered placeholder", self.publish_code)

    def test_the_tap_push_is_gated_on_the_token_it_needs(self):
        """`if:` cannot read `secrets.*`, so the answer has to become an output.

        Run unconditionally, the push hands `gh` an empty token and `gh api -X
        PUT` returns 401 under `set -euo pipefail` — failing the release job
        *after* PyPI and the GitHub Release have succeeded, which reports a good
        release as broken. `HOMEBREW_TAP_TOKEN` is not configured today, so this
        is the default path, not the edge case.
        """
        body = "\n".join(self.publish)
        self.assertIn("has_tap_token=true", self.publish_code)
        self.assertIn("has_tap_token=false", self.publish_code)
        self.assertIn("if: steps.tap.outputs.has_tap_token == 'true'", body)

    def test_the_missing_token_is_a_warning_that_says_what_to_do(self):
        """A `::notice::` inside a step nobody opens is not a safeguard (#638)."""
        self.assertIn("::warning title=Homebrew tap not updated", self.publish_code)
        self.assertIn("HOMEBREW_TAP_TOKEN repository secret", self.publish_code)
        self.assertIn("releases/latest/download/ai-jury.rb", self.publish_code)

    def test_whether_the_tap_was_pushed_is_visible_to_the_next_job(self):
        """The output must mean the push *happened*, not that a token existed.

        A token is a precondition for trying; `gh api -X PUT` can still fail, or
        the readback can differ. Sourcing the output from the push step's last
        line means a skipped or failed push contributes nothing, and `verify`
        takes its eventually-consistent path instead of demanding a tap that was
        never written.
        """
        outputs = "\n".join(self.publish)
        self.assertIn("pushed-to-tap: ${{ steps.tap-push.outputs.pushed }}", outputs)
        self.assertIn("id: tap-push", outputs)
        self.assertIn('echo "pushed=true" >> "$GITHUB_OUTPUT"', self.publish_code)

    def test_the_render_step_waits_for_the_index_with_the_shared_wait(self):
        """It runs after the upload and before the Release; a short wait half-makes one.

        Sixty seconds against PyPI's index, with the release only partly created
        if it ran out, was the first version of this. Five minutes was the
        second, and it still asked only whether the version endpoint answered —
        which on 1.16.0 it did, with a file list that was still empty (#694).

        So the wait is now one script, asserted here to be the one the `verify`
        job runs, and its behaviour is exercised in `test_pypi_index_wait`.
        """
        self.assertIn(WAIT_SCRIPT, self.publish_code)
        self.assertTrue((REPO_ROOT / WAIT_SCRIPT).exists(), f"{WAIT_SCRIPT} is missing")

    def test_the_render_step_reads_the_sdist_pair_from_the_wait(self):
        """`next(f for f in urls …)` raised `StopIteration` and left an empty url.

        The pair now comes out of the file the wait wrote, so the render cannot
        run before the sdist it names is indexed.
        """
        self.assertIn("read -r sdist_url sdist_sha < published-sdist.txt", self.publish_code)
        self.assertNotIn("packagetype", self.publish_code)

    def test_the_rendered_formula_leaves_the_release(self):
        """Two credential-free routes out, neither of them `main`.

        The tap push needs `HOMEBREW_TAP_TOKEN`, which is not configured, so the
        Release asset is what the tap can actually pull. Dropping it would leave
        the formula reachable only through a secret nobody has set.
        """
        body = "\n".join(self.publish)
        self.assertIn("release/ai-jury.rb", body)
        self.assertIn("base64 -w0 release/ai-jury.rb", self.publish_code)


class ThePublishedReleaseIsInstalledAndRun(WorkflowScan):
    """#666: nothing used to install the artifact a release produced.

    The publish job re-hashed a tarball it had just downloaded, and `ci.yml`'s
    external check runs on pushes to `main`, not on the tag. So a release could
    be green while `pip install ai-jury==<tag>` produced something that would not
    start.
    """

    def test_the_verify_job_runs_after_the_publish_job(self):
        needs = [line.strip() for line in self.verify if line.strip().startswith("needs:")]
        self.assertEqual(needs, [f"needs: {PUBLISH_JOB}"])

    def test_it_installs_from_the_index_into_a_clean_virtualenv(self):
        """Not the built wheel, and not the checkout: what a user would get."""
        self.assertIn("python -m venv", self.verify_code)
        self.assertIn('pip install --timeout 30 "ai-jury==${version}"', self.verify_code)

    def test_it_waits_for_the_index_but_not_forever(self):
        """Publishing is synchronous; indexing is not.

        A job that waits forever for a release that will never appear is
        indistinguishable from a hung runner. The bound lives in the shared
        script now — `seq 1 30` at ten seconds — so it is asserted there.
        """
        self.assertIn(WAIT_SCRIPT, self.verify_code)
        script = (REPO_ROOT / WAIT_SCRIPT).read_text(encoding="utf-8")
        self.assertIn('attempts="${PYPI_ATTEMPTS:-30}"', script)
        self.assertIn('interval="${PYPI_INTERVAL_SECONDS:-10}"', script)
        self.assertIn("never served", script)

    def test_the_wait_is_one_implementation_and_not_two(self):
        """#694: `verify` already had a wait of this shape; it is now *the* wait.

        Both jobs run the same file, so the five-minute budget and the failure
        message cannot drift apart, and there is no second reader of PyPI's file
        list to forget when this one is fixed.
        """
        self.assertIn(WAIT_SCRIPT, self.publish_code)
        self.assertIn(WAIT_SCRIPT, self.verify_code)
        # The fragile read is gone from both jobs, not merely from the one that
        # failed: it is the same three lines, and it raised on an empty list.
        for name, code in ((PUBLISH_JOB, self.publish_code), (VERIFY_JOB, self.verify_code)):
            with self.subTest(job=name):
                self.assertNotIn("packagetype", code)
                self.assertNotIn("StopIteration", code)

    def test_the_verify_job_checks_out_only_the_scripts(self):
        """It must install what the index serves, not what a checkout holds.

        The job had no checkout at all, which is the property worth keeping: a
        full tree would put the package source on the runner beside the release
        being verified. The sparse checkout must bring in the shared wait and
        nothing else — and *nothing else* is why cone mode is off. Cone mode
        materialises every root file whatever the pattern says, so it would
        deliver `jury.toml` to a runner on which `jury --doctor` runs two steps
        later and discovers config from the working directory.
        """
        body = "\n".join(self.verify)
        self.assertIn("sparse-checkout: .github/scripts", body)
        self.assertIn("sparse-checkout-cone-mode: false", body)
        self.assertNotIn("path: src", body)

    def test_the_installed_cli_must_report_the_tag(self):
        self.assertIn("jury --version", self.verify_code)
        self.assertIn('"jury ${version}"', self.verify_code)

    def test_the_installed_cli_must_run_its_diagnostics(self):
        self.assertIn("jury --doctor", self.verify_code)

    def test_the_published_formula_is_checked_against_the_published_sdist(self):
        """The release asset, unconditionally: this run wrote it, so nothing lags.

        `brew` refuses on a digest mismatch, hours later, in another repository.
        """
        self.assertIn("gh release download", self.verify_code)
        self.assertIn("the published formula points at", self.verify_code)
        self.assertIn("the published formula expects", self.verify_code)

    def test_the_tap_is_only_checked_when_this_run_pushed_it(self):
        """Otherwise the tap catches up on a 30-minute cron and this job polls 150s.

        Demanding it here would fail *every* release over latency that is by
        design — a gate blocking the only sequence able to satisfy it, which is
        the mistake the online formula check already made once.
        """
        body = "\n".join(self.verify)
        # Anchored on the tap's path, not a bare `Formula/ai-jury.rb`: this
        # repository no longer has that file, and an assertion that would also
        # match a re-added local copy is not asserting what it says.
        self.assertIn("repos/${TAP}/contents/Formula/ai-jury.rb", self.verify_code)
        self.assertIn("does not serve ${version}", self.verify_code)
        self.assertIn("if: needs.build-n-publish.outputs.pushed-to-tap == 'true'", body)
        self.assertIn("if: needs.build-n-publish.outputs.pushed-to-tap != 'true'", body)

    def test_a_failure_becomes_a_thing_on_a_list(self):
        """A message nobody is required to act on is not a safeguard (#638)."""
        self.assertIn("release-broken: ${GITHUB_REF_NAME}", self.verify_code)
        self.assertIn("gh issue create", self.verify_code)
        self.assertIn("if: failure()", "\n".join(self.verify))

    def test_a_re_run_does_not_open_a_second_issue(self):
        self.assertIn("gh issue list", self.verify_code)
        self.assertIn("gh issue comment", self.verify_code)

    def test_it_may_actually_open_one(self):
        """`gh issue create` without the scope fails at the API, after the tag."""
        self.assertTrue(
            any(p.startswith("issues: write") for p in self.verify_permissions),
            f"the verify job cannot open an issue: {self.verify_permissions}",
        )


class EveryNetworkCommandIsBoundedInTime(unittest.TestCase):
    """No command in a `run:` block may talk to the network without a deadline.

    #694 fixed the index poll — bounded in attempts *and* in wall-clock seconds,
    every request carrying `--connect-timeout` and `--max-time` — and a test
    asserted it. That test read the *script*, so it said nothing about the line
    immediately after the call to it: the render step downloaded the published
    sdist with a bare `curl -fsSL "$sdist_url"`. An index that converges
    normally, a file server that accepts the connection and never writes, and
    the step is still running with no formula: the wait returns cleanly and the
    hang has simply moved one line down. Same host, same step, same half-made
    release — 1.16.0 live on PyPI with no GitHub Release and nothing for the tap
    — reached slowly instead of quickly.

    A test named for every request that only greps one file is how that survived,
    so this one reads the workflow. Both jobs, every `run:` block, every command
    in them that opens a connection: `curl` and `wget` must carry their own
    timeouts, `pip` its socket timeout, and anything with no timeout of its own —
    `gh` above all — must be wrapped in `timeout`.

    Scoped to `publish.yml` deliberately. It is the file where an unbounded
    command lands between a PyPI upload and a GitHub Release, which is a state no
    other workflow here can reach; the other six are worth the same treatment and
    are not in the diff that fixes a broken release.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.calls = scan(cls.source)

    def test_the_scan_found_the_calls_that_are_actually_there(self):
        """Vacuity: an empty scan satisfies the assertion this class exists for."""
        by_tool = Counter(call.tool for call in self.calls)
        self.assertGreaterEqual(by_tool["curl"], 2, "the two sdist downloads were not found")
        self.assertGreaterEqual(by_tool["gh"], 6, f"too few `gh` calls found: {by_tool}")
        self.assertGreaterEqual(by_tool["pip"], 4, f"too few `pip` calls found: {by_tool}")

    def test_no_command_in_either_job_reaches_the_network_unbounded(self):
        unbounded = [call.why() for call in self.calls if not call.bounded]
        self.assertEqual(unbounded, [], "unbounded network commands:\n" + "\n".join(unbounded))

    def test_it_would_have_caught_the_download_that_was_unbounded(self):
        """Put the reviewer's line back and the scan has to name it — twice.

        `verify` had the identical download, so a fix that reached only the step
        that was reported would have left the other one exactly as it was.
        """
        broken, swapped = re.subn(
            r'curl -fsSL \\\n\s*--connect-timeout[^\n]*\\\n\s*--max-time[^\n]*\\\n\s*"\$sdist_url"',
            'curl -fsSL "$sdist_url"',
            self.source,
        )
        self.assertEqual(swapped, 2, "the mutation did not restore both downloads; rewrite it")
        self.assertIn('curl -fsSL "$sdist_url" -o published-sdist.tar.gz', broken)
        caught = [call for call in scan(broken) if not call.bounded]
        self.assertEqual(
            [call.tool for call in caught], ["curl", "curl"], [c.why() for c in caught]
        )
        self.assertEqual({call.job for call in caught}, {PUBLISH_JOB, VERIFY_JOB})

    def test_it_would_have_caught_a_gh_call_stripped_of_its_wrapper(self):
        """`gh` has no timeout of its own, so losing the wrapper is losing the bound."""
        broken = self.source.replace("timeout 30 gh api", "gh api")
        self.assertNotEqual(broken, self.source, "the mutation matched nothing; rewrite it")
        caught = [call for call in scan(broken) if not call.bounded]
        self.assertEqual([call.tool for call in caught], ["gh"], [c.why() for c in caught])
        self.assertIn("a `timeout` wrapper", caught[0].why())

    def test_prose_about_the_network_is_not_read_as_a_call_on_it(self):
        """The other way a scan gets switched off: findings nobody can act on.

        This workflow writes `brew upgrade will not see this release` into an
        `::error::` and `pip install ai-jury==<tag>` into the issue body it files
        when a release breaks. Both sit inside quoted strings, and neither is a
        request to anything.
        """
        self.assertIn("brew upgrade will not see this release", self.source)
        self.assertIn("pip install ai-jury==${GITHUB_REF_NAME#v}", self.source)
        self.assertEqual([call for call in self.calls if call.tool in ("brew", "npm")], [])
        for call in self.calls:
            with self.subTest(command=call.command[:60]):
                self.assertNotIn("Post-publish verification", call.command)


class EveryJobHasACeiling(WorkflowScan):
    """A backstop under the per-command bounds, for the commands nobody has read.

    The bounds above cover the commands this workflow spells out. They cannot
    cover the five actions it `uses:` — checkout, setup-python, the attestation,
    the PyPI publish, the release — whose HTTP is their own, nor `python -m
    build`, which shells out to pip. Without `timeout-minutes` the ceiling on any
    of those is GitHub's six-hour default, in a job that sits between an upload
    and a Release.

    It is a backstop and not the mechanism: a job stopped this way is *cancelled*,
    so `verify`'s `if: failure()` step does not run and no `release-broken` issue
    is opened. A command that fails on its own timeout fails the job, names what
    stalled, and files the report. Both are worth having; only one of them is a
    diagnosis.
    """

    def test_both_jobs_set_a_job_level_timeout(self):
        for name, block in ((PUBLISH_JOB, self.publish), (VERIFY_JOB, self.verify)):
            with self.subTest(job=name):
                found = [
                    line.strip() for line in block if line.strip().startswith("timeout-minutes")
                ]
                self.assertEqual(len(found), 1, f"{name} has no job-level timeout: {found}")

    def test_the_ceilings_are_measured_in_minutes_and_not_in_hours(self):
        """A six-hour ceiling is GitHub's default with extra steps."""
        for name, block in ((PUBLISH_JOB, self.publish), (VERIFY_JOB, self.verify)):
            with self.subTest(job=name):
                line = next(ln for ln in block if ln.strip().startswith("timeout-minutes"))
                minutes = int(line.split(":", 1)[1].strip())
                self.assertGreaterEqual(minutes, 10, f"{name}'s ceiling would fail a slow release")
                self.assertLessEqual(minutes, 60, f"{name}'s ceiling is not a ceiling")

    def test_the_ceiling_is_above_what_the_job_is_allowed_to_spend_waiting(self):
        """`verify`'s own waits: 300s for the index, 60s for pip, 150s for the tap."""
        line = next(ln for ln in self.verify if ln.strip().startswith("timeout-minutes"))
        self.assertGreater(int(line.split(":", 1)[1].strip()) * 60, 300 + 60 + 150)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
