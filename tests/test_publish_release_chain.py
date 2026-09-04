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
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOW_DIR / "publish.yml"

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
    # `pip`'s `--timeout` is its *socket* timeout: it bounds one quiet read, not
    # the call, so a peer that sends a byte inside every window holds a resolve
    # for as long as it likes. Accepting it here while `_BOUND_TOKENS` below
    # refuses to count it had the two halves of this module disagreeing — the
    # scan called pip bounded, the ceiling counted it as zero, and the residual
    # path was a `verify` cancelled at its own ceiling with no report filed for a
    # release already on PyPI. An empty tuple demands the `timeout` wrapper, the
    # one bound that is both real and countable.
    "pip": (),
    "pip3": (),
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

#: A job's key under `jobs:` — two spaces in, a name, a colon, nothing else.
_JOB_NAME = re.compile(r"^  ([A-Za-z_][\w-]*):[ \t]*$")

#: Commands whose argument is itself a command — `timeout` is the only bound
#: `gh` can be given, so a scan that could not see through it would report every
#: wrapped call as unbounded.
_WRAPPERS = frozenset({"timeout", "env", "nice", "command", "exec", "stdbuf"})

#: `60`, `1m`, `30s` — a wrapper's duration, which is not its command.
_DURATION = re.compile(r"^\d+(\.\d+)?[smhd]?$")

#: The header of a ``run:`` whose body is the lines below it. All seven of
#: YAML's block-scalar spellings: `|` and `>`, each with an optional chomping
#: indicator (`-`/`+`) and an optional indentation indicator (`1`-`9`), in
#: either order. `run: |` is what this file writes; the other six are what an
#: equality test silently stopped reading (see :func:`shell`).
_BLOCK_SCALAR = re.compile(r"^run:[ \t]*[|>](?:[+-][1-9]?|[1-9][+-]?)?[ \t]*$")

#: A quoted heredoc's opening: `<<'EOF'`, `<< "EOF"`, `<<-'EOF'`, `<<\\EOF`. Its
#: body is literal text, so the words in it are not commands. The backslash form
#: is the third spelling bash accepts for a *non-expanding* heredoc, and missing
#: it made prose in a document that happens to mention `curl` read as an
#: unbounded request: a false alarm, which is how a scanner gets switched off.
#:
#: `<<<` is a herestring, not a heredoc, and it takes *both* guards. The
#: lookahead alone only refuses the match that starts at the first `<`; the lexer
#: advances a character at a time, so on the next pass `<<<"$x"` matches from the
#: second `<` with `"$x"` read as the delimiter — and a delimiter that never
#: appears on a line of its own makes the lexer skip the whole remaining script.
#: `keel-ship.yml` writes `read -r id stamp <<<"$existing"` in the middle of the
#: step that publishes the gating check-run, so every command after it —
#: including the two `gh api` calls that write the check — was invisible to this
#: scan, which would have reported them bounded by never seeing them at all.
_HEREDOC = re.compile(r"(?<!<)<<(?!<)-?[ \t]*(?:'([^']*)'|\"([^\"]*)\"|\\(\w+))")

#: A wrapper's or a loop's duration as it is actually written: `30`, `1m`,
#: `${SDIST_MAX_TIME:-120}` — the defaulted form the workflow reads its bounds
#: from — or `$max_time`, a reference to one of those.
_SUFFIXES = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
_LITERAL_SECONDS = re.compile(r"^(\d+)([smhd]?)$")
_DEFAULTED_SECONDS = re.compile(r"^\$\{[A-Za-z_]\w*:-(\d+)\}$")
_ASSIGNS_DEFAULT = re.compile(r"^([A-Za-z_]\w*)=\$\{[A-Za-z_]\w*:-(\d+)\}$")
_REFERENCE = re.compile(r"^\$\{?([A-Za-z_]\w*)\}?$")

#: Tokens whose next argument is a number of seconds this job may spend. Matched
#: as whole tokens, which is what keeps `--timeout` out: that is pip's *socket*
#: timeout, a bound on one quiet read rather than on the call, and counting it
#: would put a number in the sum that no stall is limited to.
_BOUND_TOKENS = frozenset({"timeout", "sleep", "--max-time", "--connect-timeout"})


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

    Every spelling is collected: the block scalars and the one-liners. A
    restructure that turned a block into a one-liner would otherwise drop it
    from the scan silently, and every ``assertNotIn`` below would go on passing.

    The header is matched with a pattern rather than compared to ``"run: |"``,
    which is the form this file happens to use today. YAML spells a block scalar
    six more ways — ``|-``, ``|+``, ``>``, ``>-``, ``>+`` and any of those with
    an indentation indicator — GitHub accepts all of them, and against an
    equality test every one of them collected nothing: the body was never read,
    so each ``assertNotIn`` passed on a step it had not seen and each network
    call in it went unscanned. A step written ``run: >-`` holding an unbounded
    ``curl``, an unbounded ``gh api`` and ``git push origin HEAD:main || true``
    left this whole module green.

    A folded scalar (``>``) is read line by line here, not folded into one line
    as YAML would. That is the stricter reading of the two: folding joins a
    command with its continuation, so a bound spelled on the next line would
    count, while splitting reports the head of the command as carrying no
    bound. A false alarm is fixable; a step nobody scans is not.
    """
    code: list[str] = []
    for index, line in enumerate(block):
        stripped = line.strip()
        if _BLOCK_SCALAR.match(stripped):
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
        need = " and ".join(wanted) if wanted else "`timeout` wrapper"
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

    A *quoted* heredoc is skipped whole. Its body is literal text — no
    expansion, no substitution — so `curl` written inside one is a word in a
    document, not a request, and reporting it would be the same false alarm as
    reporting `brew` inside an `::error::`. The unquoted form is left alone:
    `$(…)` inside it does run, so its words are still worth reading.
    """
    out: list[str] = []
    word: list[str] = []
    quote: str | None = None
    #: Delimiters of quoted heredocs opened on the line being lexed; their
    #: bodies start after the next newline, in the order they were opened.
    heredocs: list[str] = []
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
        if quote is None:
            opener = _HEREDOC.match(code, index)
            if opener:
                flush()
                heredocs.append(opener.group(1) or opener.group(2) or opener.group(3) or "")
                index = opener.end()
                continue
        if quote == '"':
            if char == '"':
                quote = None
            else:
                word.append(char)
            index += 1
            continue
        if char == "\n" and heredocs:
            flush()
            out.append(_BREAK)
            index += 1
            for delimiter in heredocs:
                while index < end:
                    stop = code.find("\n", index)
                    stop = end if stop == -1 else stop
                    body, index = code[index:stop], stop + 1
                    if body.strip() == delimiter:
                        break
            heredocs.clear()
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


def job_names(lines: list[str]) -> list[str]:
    """Every top-level job in the workflow, in the order the file declares them.

    Read from the file rather than named here. A hardcoded pair is a scan that
    covers the jobs somebody thought of: add a third job — a smoke test, a
    rollback, an announcement — and it would reach the network with nothing
    checking its bounds and no ceiling required of it, while both tests went on
    reporting success over the two jobs they know.
    """
    at = [i for i, line in enumerate(lines) if line.rstrip() == "jobs:"]
    assert len(at) == 1, f"expected one top-level `jobs:` mapping, found {len(at)}"
    names = [
        match.group(1)
        for line in block_after(lines, at[0])
        for match in [_JOB_NAME.match(line)]
        if match
    ]
    assert names, "no jobs were found; the workflow has been restructured"
    return names


def scan(source: str) -> list[NetworkCall]:
    """Every network call in every job of one workflow's text, real or mutated."""
    lines = source.splitlines()
    return [
        call for job in job_names(lines) for call in network_calls(shell(job_body(lines, job)), job)
    ]


def _seconds(token: str, assigned: dict[str, int]) -> int | None:
    """``30``, ``1m``, ``${SDIST_MAX_TIME:-120}`` or ``$max_time``, in seconds."""
    literal = _LITERAL_SECONDS.match(token)
    if literal:
        return int(literal.group(1)) * _SUFFIXES[literal.group(2)]
    defaulted = _DEFAULTED_SECONDS.match(token)
    if defaulted:
        return int(defaulted.group(1))
    reference = _REFERENCE.match(token)
    if reference:
        return assigned.get(reference.group(1))
    return None


def _iterations(words: list[str]) -> int:
    """How many times a ``for … in …`` loop runs its body.

    Refused rather than guessed when the line does not say. A loop of unknown
    length around a wrapped request is precisely what a fixed ceiling cannot
    survive, so it has to stop this sum rather than be estimated into it.
    """
    shown = " ".join(words)
    assert "in" in words, f"a loop with no readable iteration count: `{shown}`"
    items = words[words.index("in") + 1 :]
    if items[:1] == ["seq"]:
        numbers = [word for word in items[1:] if word.isdigit()]
        assert len(numbers) == 2, f"a `seq` this cannot count: `{shown}`"
        return int(numbers[1]) - int(numbers[0]) + 1
    assert items and all(not word.startswith("$") for word in items), (
        f"a loop with no readable iteration count: `{shown}`"
    )
    return len(items)


def script_budget(script: str) -> int:
    """The wall-clock ceiling of one `wait-for-pypi-dists.sh` call, in seconds.

    Read out of the script's own defaults rather than restated: attempts ×
    interval is the budget it holds itself to, and the guard that enforces it is
    at the *top* of each attempt — so the last attempt may still spend a whole
    request and the sleep after it beyond that line.
    """
    defaults = dict(re.findall(r'^(\w+)="\$\{(?:\w+):-(\d+)\}"$', script, re.MULTILINE))
    attempts, interval = int(defaults["attempts"]), int(defaults["interval"])
    return attempts * interval + int(defaults["max_time"]) + interval


def wait_seconds(code: str, budget: int) -> int:
    """The seconds one job's shell may spend inside bounds it sets for itself.

    Every ``timeout N``, every ``--max-time``/``--connect-timeout``, every
    ``sleep N`` multiplied by the iterations of the loop it sits in, and one
    ``budget`` per call to the shared wait. Computed from the text because the
    alternative — three numbers written down beside the ceiling — is what let
    the ceiling fall below the sum: the figure in the test was the same
    undercount as the figure in the comment, so neither could catch the other.

    Deliberately an over-estimate in two places. ``--connect-timeout`` is added
    to ``--max-time`` though curl's ``--max-time`` already covers connecting,
    and the branches of an ``if``/``else`` are summed rather than maximised. A
    ceiling has to clear the worst case; an over-estimate keeps clearing it.
    """
    commands = _commands(code)
    assigned = {
        match.group(1): int(match.group(2))
        for tokens in commands
        for token in tokens
        for match in [_ASSIGNS_DEFAULT.match(token)]
        if match
    }
    total, factors = 0, [1]
    for tokens in commands:
        words = [token for token in tokens if token != _OPENS]
        if not words:
            continue
        if words[0] == "done":
            if len(factors) > 1:
                factors.pop()
            continue
        if words[0] in ("for", "while", "until"):
            factors.append(factors[-1] * _iterations(words))
            continue
        here = 0
        for index, token in enumerate(words):
            if token.rsplit("/", 1)[-1] == WAIT_SCRIPT.rsplit("/", 1)[-1]:
                here += budget
                continue
            if token not in _BOUND_TOKENS:
                continue
            # `timeout`'s own options stand between it and its duration.
            rest = [word for word in words[index + 1 :] if not word.startswith("-")]
            seconds = _seconds(rest[0], assigned) if rest else None
            assert seconds is not None, (
                f"`{token}` in `{' '.join(words)}` names no readable number of seconds"
            )
            here += seconds
        total += here * factors[-1]
    return total


def permissions(block: list[str]) -> list[str]:
    at = [i for i, line in enumerate(block) if line.strip() == "permissions:"]
    assert len(at) == 1, f"expected one permissions block, found {len(at)}"
    return [line.strip() for line in block_after(block, at[0]) if line.strip()]


#: A ``run:`` key, whatever it carries. Not ``runs-on:``, and not a line of a
#: script that happens to mention one: a key stands alone on its line.
_RUN_KEY = re.compile(r"^run:(?:[ \t].*)?$")


def workflow_paths() -> list[Path]:
    """Every workflow, discovered from the directory rather than listed.

    A list is a scan of the files somebody remembered. `#695` scanned exactly
    one, and the six it left out had unbounded `pip`, `gh` and `git` calls and
    no `timeout-minutes` between them — including the two jobs that publish the
    check-run a merge is gated on. Reading the directory means the next workflow
    arrives inside these checks rather than beside them.
    """
    found = sorted(path for path in WORKFLOW_DIR.iterdir() if path.suffix in (".yml", ".yaml"))
    assert found, f"no workflows found under {WORKFLOW_DIR}"
    return found


@dataclass(frozen=True)
class Job:
    """One job of one workflow, and the lines of its body."""

    workflow: str
    name: str
    body: list[str]

    def where(self) -> str:
        return f".github/workflows/{self.workflow} ({self.name})"


def every_job() -> list[Job]:
    """Every job of every workflow, discovered the same way.

    A job, not a file: a workflow gains jobs too, and a new one reaching the
    network with no ceiling over it is the same hole one file lower down.
    """
    found = [
        Job(path.name, name, job_body(lines, name))
        for path in workflow_paths()
        for lines in [path.read_text(encoding="utf-8").splitlines()]
        for name in job_names(lines)
    ]
    assert found, "no jobs were found in any workflow"
    return found


def ceiling_seconds(body: list[str]) -> int | None:
    """One job's ``timeout-minutes`` in seconds, or ``None`` if it sets none.

    Job level only — four spaces in, under a job named two spaces in. A step may
    carry its own `timeout-minutes`, and counting one of those as the job's would
    report a ceiling over a single step as a ceiling over the whole job.
    """
    found = [
        line.strip()
        for line in body
        if indent_of(line) == 4 and line.strip().startswith("timeout-minutes:")
    ]
    if len(found) != 1:
        return None
    return int(found[0].split(":", 1)[1].strip()) * 60


def unreadable_runs(lines: list[str]) -> list[str]:
    """Every ``run:`` key this scan would not read, with its line number.

    :func:`shell` reads two spellings: a block-scalar header, whose body is the
    lines below it, and a plain one-liner. YAML has three more that Actions runs
    identically — a quoted flow scalar, a block header carrying a trailing
    comment, and a value on the line below the key — and telling those apart
    from the two it reads needs the file *parsed* rather than lexed, which needs
    a YAML library this project does not depend on (``dependencies = []``; the
    reasoning is in ``test_github_action.py``).

    So rather than claim the scan reads every spelling, this refuses the three
    it does not. A step written any of those ways is a finding here, and the fix
    is to write it as a block scalar — which turns a silent hole into a failing
    test on the pull request that opens it. The hole is not hypothetical: an
    equality test against ``"run: |"`` left a step spelled ``run: >-`` unread by
    *every* assertion in this module, and the same step is what a ``run:`` on the
    following line would be today.

    A quoted flow scalar is refused for a sharper reason than the other two. Its
    first line *is* collected, by the one-liner branch, so the step looks read —
    while every continuation line is dropped. Half a command read is worse than
    none: the half that is read can carry the bound, and the half that is not can
    carry the request.
    """
    unreadable: list[str] = []
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not _RUN_KEY.match(stripped) or _BLOCK_SCALAR.match(stripped):
            continue
        value = stripped[len("run:") :].strip()
        if value and not value.startswith(("'", '"', "|", ">")):
            continue  # a plain one-liner, which `shell()` collects
        unreadable.append(f"{number}: {stripped}")
    return unreadable


class WorkflowScan(unittest.TestCase):
    """Shared hand-parse of `publish.yml`."""

    @classmethod
    def setUpClass(cls):
        lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
        cls.lines = lines
        cls.jobs = job_names(lines)
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

    def test_the_jobs_are_read_from_the_file_and_not_named_here(self):
        """Both known jobs, and nothing invented: the list is the file's, not ours.

        `scan()` and the ceiling test both walk this list. A job added to
        `publish.yml` therefore arrives inside both checks rather than beside
        them — which is the difference between a scan of the workflow and a scan
        of the two jobs somebody remembered.
        """
        self.assertLessEqual({PUBLISH_JOB, VERIFY_JOB}, set(self.jobs))
        for name in self.jobs:
            with self.subTest(job=name):
                self.assertTrue(job_body(self.lines, name), f"`{name}:` is not a job")


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

    This class stays on `publish.yml`, where the mutations it runs live: that is
    the file where an unbounded command lands between a PyPI upload and a GitHub
    Release, a state no other workflow can reach. The other six get the same
    treatment from `EveryWorkflowBoundsItsNetworkCalls` below, which discovers
    them rather than naming them (#697).
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
        self.assertIn("`timeout` wrapper", caught[0].why())

    def test_it_would_have_caught_a_pip_call_left_to_its_socket_timeout(self):
        """`--timeout` is not a bound on the call, and used to be accepted as one.

        pip's `--timeout` limits one quiet read. A peer that sends a byte inside
        every window keeps a resolve alive indefinitely, so the six-attempt loop
        in `verify` could outlive the job's ceiling — and a job stopped that way
        is cancelled, which files no report for a release already on PyPI. The
        scan called those calls bounded while `wait_seconds` counted them as
        zero; this is the mutation that would have shown the disagreement.
        """
        broken = self.source.replace(
            "timeout 90 /tmp/verify-venv/bin/python -m pip", "/tmp/verify-venv/bin/python -m pip"
        )
        self.assertNotEqual(broken, self.source, "the mutation matched nothing; rewrite it")
        caught = [call for call in scan(broken) if not call.bounded]
        self.assertEqual([call.tool for call in caught], ["pip"], [c.why() for c in caught])
        self.assertIn("`timeout` wrapper", caught[0].why())

    def test_every_pip_call_is_counted_by_the_ceiling_it_is_bounded_by(self):
        """The two halves agreeing: what makes pip bounded is what makes it countable.

        `_BOUND_TOKENS` has always refused to count `--timeout`, so a pip call
        whose only bound was that flag put nothing in the sum the ceiling is
        chosen against. Requiring the wrapper closes the gap in both directions
        at once.
        """
        self.assertEqual(NETWORK_TOOLS["pip"], ())
        self.assertNotIn("--timeout", _BOUND_TOKENS)
        for call in self.calls:
            if call.tool in ("pip", "pip3"):
                with self.subTest(command=call.command):
                    self.assertTrue(call.bounded, call.why())
                    self.assertGreater(wait_seconds(call.command, 0), 0, call.command)

    def test_a_backslash_heredoc_is_literal_text_and_not_a_run_of_commands(self):
        """`<<\\EOF` is bash's third spelling for a non-expanding heredoc.

        The pattern knew the two quoted forms only, so a document written with
        this one had its prose lexed as commands — a `curl` mentioned in a
        sentence reported as an unbounded request. A finding nobody can act on
        is how a scanner gets switched off, which is the same failure the quoted
        forms are excluded to avoid.
        """
        body = "cat <<\\EOF > notes.md\ncurl https://example.invalid/x\nEOF\n"
        self.assertEqual(network_calls(body, VERIFY_JOB), [])
        # The quoted spellings still behave, and an *unquoted* heredoc still is
        # not a licence to hide a call: only the opener changed.
        self.assertEqual(network_calls(body.replace("<<\\EOF", "<<'EOF'"), VERIFY_JOB), [])

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


#: A step of the shape the reviewer inserted: an unbounded `curl`, an unbounded
#: `gh api`, and the `git push origin HEAD:main || true` of #633 — the exact
#: line whose absence three tests in this module assert. `{header}` is spelled
#: with each of YAML's block scalars in turn.
_HOSTILE_STEP = """      - name: A step written the other way
        run: {header}
          curl -fsSL https://example.invalid/thing -o thing
          gh api repos/example/example
          git push origin HEAD:main || true
"""

#: Where to graft it: a step of `build-n-publish`, named uniquely in the file.
_ANCHOR = "      - name: Build package distributions\n"

#: Every block-scalar header GitHub accepts, `run: |` included so the test also
#: proves the graft itself works.
_HEADERS = ("|", "|-", "|+", ">", ">-", ">+", "|2", "|-2", "|2-")


class EveryBlockScalarSpellingIsRead(unittest.TestCase):
    """`run: |` is one of seven spellings, and only one of them was collected.

    `shell()` decided a step's body had begun by comparing the line to the
    string `"run: |"`. YAML writes the same block six other ways — `|-`, `|+`,
    `>`, `>-`, `>+`, and any of those carrying an indentation indicator — and
    GitHub Actions runs all of them identically. Against an equality test every
    one of them collected *nothing*: `block_after` was never called, the body
    never reached the scan, and each `assertNotIn` in this module passed on a
    step it had not read.

    Block scalars are not every spelling of a `run:`, and this class does not
    claim they are. A quoted flow scalar, a block header carrying a trailing
    comment, and a value on the following line are all legal YAML that Actions
    runs, and reading them needs the file parsed rather than lexed — a YAML
    library this project does not depend on. Since #697 they are refused instead
    of tolerated: `NoWorkflowSpellsARunTheScanCannotRead` fails on any of the
    three, in any workflow, so the hole cannot be opened without a red check.

    That is the failure worth a test of its own, because it is silent and
    total. A step spelled `run: >-` holding an unbounded `curl`, an unbounded
    `gh api` and `git push origin HEAD:main || true` — the push of #633, the
    thing `ReleasingWritesNothingToThisRepository` exists to forbid — left this
    entire module green.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def graft(self, header: str) -> list[str]:
        """`publish.yml` with the hostile step added to `build-n-publish`."""
        step = _HOSTILE_STEP.format(header=header)
        self.assertIn(_ANCHOR, self.source, "the anchor step is gone; rewrite this test")
        return self.source.replace(_ANCHOR, step + _ANCHOR, 1).splitlines()

    def test_the_header_pattern_matches_what_yaml_actually_writes(self):
        for header in _HEADERS:
            with self.subTest(header=header):
                self.assertTrue(_BLOCK_SCALAR.match(f"run: {header}"))
        for other in ("run: echo |", "run: ./script.sh", "runs-on: |", "run: |x"):
            with self.subTest(line=other):
                self.assertIsNone(_BLOCK_SCALAR.match(other))

    def test_a_step_spelled_any_other_way_is_still_read(self):
        """The bodies must be collected, whatever header stands above them."""
        for header in _HEADERS:
            with self.subTest(header=header):
                code = shell(job_body(self.graft(header), PUBLISH_JOB))
                self.assertIn("curl -fsSL https://example.invalid/thing", code)
                self.assertIn("git push origin HEAD:main", code)

    def test_the_module_fails_on_a_step_spelled_any_other_way(self):
        """The assertions this module is made of, against the grafted step.

        Not "the body was collected" but "the checks that read it now fail" —
        which is what the reviewer got no failure from, and the only property
        that makes the fix worth anything.
        """
        for header in _HEADERS:
            with self.subTest(header=header):
                lines = self.graft(header)
                code = shell(job_body(lines, PUBLISH_JOB))
                # `ReleasingWritesNothingToThisRepository`, verbatim.
                self.assertIn("git push", code)
                self.assertIn("HEAD:main", code)
                # `EveryNetworkCommandIsBoundedInTime`, verbatim.
                unbounded = [call.tool for call in scan("\n".join(lines)) if not call.bounded]
                self.assertEqual(sorted(unbounded), ["curl", "gh", "git"], unbounded)

    def test_a_quoted_heredoc_is_not_read_as_a_command(self):
        """The other half of reading shell: text that only looks like a call.

        A quoted heredoc expands nothing, so `curl` inside one is a word in a
        document. Reporting it would be the false alarm that gets a scanner
        switched off — the same mistake as reading `brew upgrade …` out of an
        `::error::` string. Nothing in `publish.yml` writes one today; the lexer
        handles it so that the first step that does is not a puzzle.
        """
        code = (
            'cat <<"EOF" > note.md\n'
            "curl https://example.invalid/x\n"
            "gh api repos/example/example\n"
            "EOF\n"
            "timeout 60 gh api repos/example/example\n"
        )
        found = network_calls(code, VERIFY_JOB)
        self.assertEqual([call.tool for call in found], ["gh"])
        self.assertTrue(found[0].bounded)
        # Unquoted, `$(…)` inside the body does run, so it is still read.
        self.assertEqual(
            [call.tool for call in network_calls(code.replace('<<"EOF"', "<<EOF"), VERIFY_JOB)],
            ["curl", "gh", "gh"],
        )


class EveryWorkflowBoundsItsNetworkCalls(unittest.TestCase):
    """The same scan, over every workflow in the directory rather than one.

    #695 bounded `publish.yml` because that is the file whose stall left 1.16.0
    on PyPI with no Release. The scan it wrote was scoped there, and the six
    workflows it did not read had, between them, eleven unbounded `pip install`
    calls, four unbounded `gh` calls, an unbounded `git fetch`, and no
    `timeout-minutes` on any of their twelve jobs.

    `keel-ship.yml` is the one that mattered. Its `evidence` job publishes the
    check-run branch protection gates the merge on, and a hung `gh api` there
    leaves that check *open* — not failed. A failed check is a thing somebody
    fixes; an open one is a pull request that cannot merge with nothing to read,
    which is the state #668, #670, #671, #672 and #674 were each rerun out of by
    hand.

    Discovery is the point, not the seven files. A workflow added tomorrow, or a
    job added to one of these, is inside this test the day it lands rather than
    the day somebody remembers to add it here.
    """

    @classmethod
    def setUpClass(cls):
        cls.workflows = workflow_paths()
        cls.jobs = every_job()
        cls.calls = [
            call for job in cls.jobs for call in network_calls(shell(job.body), job.where())
        ]

    def test_the_directory_is_read_and_every_file_in_it_declares_jobs(self):
        """Vacuity, twice over: a glob that matched nothing passes everything.

        The named files are an anchor against a typo'd suffix or a moved
        directory — not the list the scan walks, which is whatever is there.
        """
        names = {path.name for path in self.workflows}
        self.assertLessEqual(
            {
                "ci.yml",
                "codeql.yml",
                "keel-ship.yml",
                "pages.yml",
                "pr-lint.yml",
                "publish.yml",
                "scorecard.yml",
            },
            names,
            f"the workflow directory read as {sorted(names)}",
        )
        self.assertEqual(
            names,
            {job.workflow for job in self.jobs},
            "a workflow in the directory contributed no jobs to the scan",
        )

    def test_the_scan_found_the_calls_that_are_actually_there(self):
        """Vacuity again: an empty scan satisfies the assertion below it.

        Anchored per workflow, because a scan that reads six files and silently
        collects nothing from one of them is the exact failure #695 shipped.
        """
        by_workflow = Counter(call.job.split(" (")[0].rsplit("/", 1)[-1] for call in self.calls)
        for workflow in ("ci.yml", "keel-ship.yml", "pages.yml", "publish.yml"):
            with self.subTest(workflow=workflow):
                self.assertTrue(
                    by_workflow[workflow],
                    f"{workflow} contributed no network calls; the scan read {by_workflow}",
                )
        tools = Counter(call.tool for call in self.calls)
        self.assertGreaterEqual(tools["pip"], 12, f"too few `pip` calls found: {tools}")
        self.assertGreaterEqual(tools["gh"], 11, f"too few `gh` calls found: {tools}")
        self.assertGreaterEqual(tools["git"], 1, f"the `git fetch` was not found: {tools}")

    def test_no_command_in_any_workflow_reaches_the_network_unbounded(self):
        """Nothing is listed as known-unbounded: every call found took a bound.

        If one ever cannot take one, this is the assertion to relax — by naming
        that call and the reason, in this file, where the next reader meets it.
        """
        unbounded = [call.why() for call in self.calls if not call.bounded]
        self.assertEqual(unbounded, [], "unbounded network commands:\n" + "\n".join(unbounded))

    def test_it_would_have_caught_the_check_run_writes_left_unbounded(self):
        """The two `gh api` calls that write the gating check, stripped.

        `gh` has no timeout flag and no environment variable for one, so the
        wrapper is the whole bound. These are the writes that leave a required
        check incomplete when they hang, which is why they are the ones mutated.
        """
        source = (WORKFLOW_DIR / "keel-ship.yml").read_text(encoding="utf-8")
        broken, swapped = re.subn(r"timeout 60 gh api -X (PATCH|POST)", r"gh api -X \1", source)
        self.assertEqual(swapped, 2, "the mutation did not strip both writes; rewrite it")
        caught = [call for call in scan(broken) if not call.bounded]
        self.assertEqual([call.tool for call in caught], ["gh", "gh"], [c.why() for c in caught])
        self.assertIn("`timeout` wrapper", caught[0].why())

    def test_it_would_have_caught_a_pip_install_left_to_its_socket_timeout(self):
        """`--timeout` is pip's socket timeout, and is not accepted as a bound.

        It limits one quiet read, not the call: a peer that sends a byte inside
        every window holds the resolve open for as long as it likes. The rule is
        `NETWORK_TOOLS["pip"] == ()` — only a wrapper counts — and it has to
        hold in the workflows this change widened to, not just in `publish.yml`.
        """
        source = (WORKFLOW_DIR / "ci.yml").read_text(encoding="utf-8")
        broken = source.replace(
            "timeout 300 python -m pip install -e .", "python -m pip install --timeout 30 -e ."
        )
        self.assertNotEqual(broken, source, "the mutation matched nothing; rewrite it")
        caught = [call for call in scan(broken) if not call.bounded]
        self.assertEqual([call.tool for call in caught], ["pip"], [c.why() for c in caught])
        self.assertIn("`timeout` wrapper", caught[0].why())

    def test_it_would_have_caught_the_base_branch_fetch_left_unbounded(self):
        """`git fetch` is a network call in a job that gates every merge."""
        source = (WORKFLOW_DIR / "keel-ship.yml").read_text(encoding="utf-8")
        broken = source.replace("timeout 60 git fetch", "git fetch")
        self.assertNotEqual(broken, source, "the mutation matched nothing; rewrite it")
        caught = [call for call in scan(broken) if not call.bounded]
        self.assertEqual([call.tool for call in caught], ["git"], [c.why() for c in caught])

    def test_a_herestring_does_not_swallow_the_rest_of_the_script(self):
        """`<<<` is not a heredoc, and reading it as one hid a whole step.

        The lexer walks a character at a time, so a lookahead alone only refuses
        the match starting at the *first* `<`: on the next pass `<<<"$existing"`
        matched from the second one with `"$existing"` read as the delimiter.
        A delimiter that never appears on a line of its own means the skip runs
        to the end of the script — so in `keel-ship.yml`'s `evidence` step,
        everything after `read -r id stamp <<<"$existing"` was invisible,
        including both `gh api` calls that publish the gating check-run. The scan
        reported them bounded by never having seen them, which is worse than
        reporting them unbounded.
        """
        code = 'read -r a b <<<"$pair"\ngh api repos/example/example\n'
        self.assertEqual([call.tool for call in network_calls(code, "x")], ["gh"])
        self.assertFalse(network_calls(code, "x")[0].bounded)
        # A real quoted heredoc is still skipped whole.
        self.assertEqual(
            network_calls('cat <<"EOF" > n.md\ngh api repos/example/example\nEOF\n', "x"), []
        )
        # And the workflow this was found in has both writes in the scan again.
        writes = [
            call
            for call in self.calls
            if "keel-ship.yml" in call.job and "check-runs" in call.command
        ]
        self.assertEqual(len(writes), 3, [call.command[:60] for call in writes])


class NoWorkflowSpellsARunTheScanCannotRead(unittest.TestCase):
    """The blind spot that is left, turned into a rule instead of a footnote.

    `shell()` reads block scalars and plain one-liners. A quoted flow scalar, a
    block header with a trailing comment, and a value on the line below the key
    are legal YAML that Actions runs and this scan does not read — and a step it
    does not read is a step every assertion in this module passes on without
    having seen. That is precisely how a `run: >-` holding an unbounded `curl`,
    an unbounded `gh api` and `git push origin HEAD:main || true` left the whole
    module green.

    Reading them needs a YAML parser and this project declares no runtime
    dependencies, so the honest move is not to widen the reader but to narrow
    what the files are allowed to contain. Every workflow here already writes
    only the two readable spellings; this keeps it that way, and says so in the
    failure message rather than in a comment nobody reads.
    """

    def test_every_run_in_every_workflow_is_written_a_way_the_scan_reads(self):
        for path in workflow_paths():
            with self.subTest(workflow=path.name):
                found = unreadable_runs(path.read_text(encoding="utf-8").splitlines())
                self.assertEqual(
                    found,
                    [],
                    f"{path.name} spells a `run:` this scan cannot read, so nothing in "
                    f"this module checks it; write it as a block scalar (`run: |`):\n"
                    + "\n".join(found),
                )

    def test_the_readable_spellings_are_accepted(self):
        readable = [f"        run: {header}" for header in _HEADERS]
        readable.append("        run: python -m pip install -e .")
        readable.append("        runs-on: ubuntu-latest")
        readable.append("        # a comment about a run: block")
        self.assertEqual(unreadable_runs(readable), [])

    def test_each_spelling_the_scan_cannot_read_is_named(self):
        """Prove it fails: the three spellings, one at a time."""
        for line, why in (
            ('        run: "python -m pip install -e ."', "a quoted flow scalar"),
            ("        run: | # install the package", "a header with a trailing comment"),
            ("        run:", "a value on the following line"),
        ):
            with self.subTest(spelling=why):
                self.assertEqual(len(unreadable_runs([line])), 1, why)

    def test_the_scan_really_cannot_read_them(self):
        """The rule is not arbitrary: each spelling loses script to the scan.

        Without this the guard could outlive the limitation it exists for, and
        go on forbidding a spelling that had since become readable.
        """
        # A block header carrying a trailing comment: the whole body is lost,
        # because the header is not recognised as one and the lines below it are
        # never collected.
        commented = ["      - name: s", "        run: | # install", "          gh api repos/e/e"]
        self.assertNotIn("repos/e/e", shell(commented))
        # A value on the following line: the same, for the same reason.
        below = ["      - name: s", "        run:", "          gh api repos/e/e"]
        self.assertNotIn("repos/e/e", shell(below))
        # A quoted flow scalar is the subtle one. Its *first* line is collected
        # by the one-liner branch and the lexer happens to strip the quote, so
        # the step looks read — while every continuation line is silently
        # dropped. A scan that reads half a command is worse than one that reads
        # none of it: the half it reads can carry the bound the other half spends.
        quoted = [
            "      - name: s",
            '        run: "gh api repos/e/e',
            '          && curl http://x"',
        ]
        self.assertIn("repos/e/e", shell(quoted))
        self.assertNotIn("curl", shell(quoted))


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

    Which is why the ceiling has to clear the sum of the bounds beneath it. A
    ceiling *below* that sum converts the failure this file works to produce — a
    named stall, on a list — into the one it works to avoid: a cancelled job, no
    issue, and a release already on PyPI. `verify` was in that state at twenty
    minutes against ~1620s of its own waits.

    Since #697 this runs over every job of every workflow, discovered the same
    way the network scan is. Twelve of the fourteen jobs here had no ceiling at
    all — including both jobs of `keel-ship.yml`, where the six-hour default sat
    under the writes that publish a required check.
    """

    def ceiling(self, name: str) -> int:
        """The `timeout-minutes` of one job of `publish.yml`, in seconds."""
        found = ceiling_seconds(job_body(self.lines, name))
        self.assertIsNotNone(found, f"{name} has no job-level timeout")
        return found

    def test_every_job_of_every_workflow_sets_a_job_level_timeout(self):
        for job in every_job():
            with self.subTest(job=job.where()):
                self.assertIsNotNone(
                    ceiling_seconds(job.body),
                    f"{job.where()} sets no `timeout-minutes`, so its ceiling is GitHub's "
                    "six-hour default and a stall there is never reported",
                )

    def test_a_job_that_loses_its_ceiling_is_named(self):
        """Prove it fails: the assertion above, against a job stripped of one."""
        stripped = [
            line for line in job_body(self.lines, VERIFY_JOB) if "timeout-minutes" not in line
        ]
        self.assertIsNone(ceiling_seconds(stripped))
        self.assertIsNotNone(ceiling_seconds(job_body(self.lines, VERIFY_JOB)))

    def test_the_ceilings_are_measured_in_minutes_and_not_in_hours(self):
        """A six-hour ceiling is GitHub's default with extra steps."""
        for job in every_job():
            with self.subTest(job=job.where()):
                minutes = ceiling_seconds(job.body) // 60
                self.assertGreaterEqual(
                    minutes, 10, f"{job.where()}'s ceiling would fail on a slow runner"
                )
                self.assertLessEqual(minutes, 60, f"{job.where()}'s ceiling is not a ceiling")

    def test_the_ceiling_is_above_what_the_job_is_allowed_to_spend_waiting(self):
        """The sum is computed from the file, because a written-down one was wrong.

        This test used to assert `20 × 60 > 300 + 60 + 150`, three numbers typed
        beside a comment that listed the same three. Both were the same
        undercount, and the arithmetic they agreed on left out the *second* call
        to the shared wait, the sdist download, the `gh release download` and
        every `timeout 30` inside the tap poll — about 1440s of bounds under a
        1200s ceiling. So the job could be cancelled while still inside its own
        deadlines, and a cancelled job runs no `if: failure()` step: no
        `release-broken` issue, for a release already on PyPI.

        A restatement cannot catch a miscount of the thing it restates. This
        reads the bounds out of the workflow instead, over every job the file
        declares, so a wait added tomorrow is in the sum tomorrow.
        """
        budget = script_budget((REPO_ROOT / WAIT_SCRIPT).read_text(encoding="utf-8"))
        for job in every_job():
            with self.subTest(job=job.where()):
                spend = wait_seconds(shell(job.body), budget)
                ceiling = ceiling_seconds(job.body)
                self.assertGreater(
                    ceiling,
                    spend,
                    f"{job.where()} may spend {spend}s inside its own bounds under a "
                    f"{ceiling}s ceiling; a job cancelled that way files no report",
                )

    def test_the_prose_a_maintainer_reads_states_the_ceiling_the_file_sets(self):
        """The drift this class exists to prevent, one document further out.

        `docs/releasing.md` restates both numbers — the ceiling and the sum it
        must clear — and it is where somebody chooses the next one. Raising the
        ceiling in the workflow and leaving the old minutes in the prose is how
        a reader concludes the ceiling sits *below* the sum, which is the
        cancellation this whole change is against. It happened on this branch.

        Scoped to `publish.yml`'s jobs on purpose, and not widened with the two
        tests above: `docs/releasing.md` is the release runbook, and demanding it
        state `pr-lint`'s ten minutes would make the guard about coverage rather
        than about the numbers a releaser reads. The ceilings on the other
        workflows are stated where they are set, in a comment beside each one.
        """
        prose = (REPO_ROOT / "docs" / "releasing.md").read_text(encoding="utf-8")
        budget = script_budget((REPO_ROOT / WAIT_SCRIPT).read_text(encoding="utf-8"))
        for name in self.jobs:
            with self.subTest(job=name):
                minutes = self.ceiling(name) // 60
                # `assertTrue`, not `assertIn`: the haystack is a whole document,
                # and a failure that prints it buries the one number that is wrong.
                self.assertTrue(
                    str(minutes) in prose,
                    f"docs/releasing.md never states {name}'s {minutes}-minute ceiling",
                )
        spend = wait_seconds(self.verify_code, budget)
        self.assertTrue(
            f"{spend}s" in prose,
            f"docs/releasing.md states a spend for `verify` that is not the {spend}s "
            "the workflow now permits",
        )

    def test_the_sum_it_checks_against_is_not_an_empty_one(self):
        """Vacuity again: `0 < any ceiling`, so a sum of nothing would pass.

        The two waits, the pip loop, the two downloads and the tap poll are all
        in `verify`, and they come to more than the twenty minutes this job used
        to allow — which is the finding the test above exists to keep.
        """
        budget = script_budget((REPO_ROOT / WAIT_SCRIPT).read_text(encoding="utf-8"))
        self.assertEqual(budget, 30 * 10 + 30 + 10)
        self.assertGreater(wait_seconds(self.verify_code, budget), 20 * 60)
        self.assertGreater(wait_seconds(self.publish_code, budget), budget)

    def test_a_wait_added_to_a_loop_is_counted_once_per_iteration(self):
        """`10 × timeout 30` is 300 seconds, not 30 — the miscount, in miniature."""
        one = "timeout 30 gh api repos/x/y"
        looped = f"for attempt in $(seq 1 10); do\n{one}\nsleep 15\ndone"
        self.assertEqual(wait_seconds(one, 0), 30)
        self.assertEqual(wait_seconds(looped, 0), 10 * (30 + 15))

    def test_a_loop_it_cannot_count_stops_the_sum_instead_of_being_guessed(self):
        with self.assertRaises(AssertionError):
            wait_seconds('while [ -z "$x" ]; do\ntimeout 30 gh api repos/x/y\ndone', 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
