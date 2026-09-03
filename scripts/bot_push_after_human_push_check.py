#!/usr/bin/env python3
"""Fail a pull request whose bot-owned branch was pushed by a bot *after* a human.

The incident this exists for (#676): on 2026-09-03 the reviewed head of #648 — a
Bolt-owned branch — was rebased onto ``main`` by the maintainer, who then added a
test commit of their own. The bot pushed once more on top of that from its own
stale checkout, and the push replaced the tree with the bot's old copy: 25 files,
2,491 deletions, silently reverting two already-merged pull requests. The suite
went red, but nothing named the cause, and on a smaller bot pull request the same
shape would have gone green and shipped the revert.

The policy that follows from it is that **a bot-owned branch is a read-only
input**: reviewed changes are re-landed on a fresh branch cut from ``main`` and
the bot's pull request is closed with a link to it. This check enforces the half
of that policy a machine can see — a bot commit landing after a human one.

How a bot is recognised
-----------------------
Not by the pushing account. The optimisation and accessibility bots used here
(Bolt, Palette, Sentinel) commit through the GitHub API **as the repository
owner**: on #648 every commit, the bot's and the human's alike, carries the login
``berkayturanci``. An account-based guard would have seen four human commits and
passed. What actually separates them is:

* the **branch** the work sits on — these bots open ``bolt-…`` / ``palette/…`` /
  ``sentinel-…`` / ``dependabot/…`` branches, and that prefix is what "bot-owned"
  means here;
* the **commit subject**, which each bot stamps with its own name (``⚡ Bolt:``,
  ``🎨 Palette:``, ``🛡️ Sentinel:``);
* the **account**, for the ones that do have their own (``dependabot[bot]``).

Everything else on such a branch is a person, and a person's commit is the thing
a later bot push destroys.

Measured against this repository's history — all 114 bot-owned pull requests —
the check fires on two: #648, the incident, and #631, where a Sentinel push
landed on top of a maintainer's `sec(cli)` fix in exactly the same shape. It errs
toward firing on purpose: a false positive costs one re-land on a fresh branch,
which is what the policy asks for on a bot branch anyway, and a false negative
costs a silent revert nobody sees.

Design notes
------------
Every decision is a pure function over the JSON GitHub already returns for
``/repos/{repo}/pulls/{n}`` and ``…/commits``; the only I/O is two injectable
fetch callables. That is what lets the tests be fixture-driven and offline.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

#: Branch-name prefixes that mark a branch as owned by an automation that pushes
#: from its own checkout. Deliberately **not** ``claude`` or ``codex``: those
#: branches are driven interactively from a working copy by the person at the
#: keyboard, who is expected to keep pushing to them. The distinction that
#: matters is not "an agent wrote it" but "something else holds the only copy of
#: this branch and will overwrite yours".
BOT_BRANCH_PREFIXES = frozenset(
    {
        "bolt",
        "copilot",
        "dependabot",
        "jules",
        "palette",
        "renovate",
        "sentinel",
    }
)

#: Logins belonging to automation, compared against the whole normalized login or
#: name rather than a token of it — ``jules`` alone is also somebody's first name.
#: GitHub App accounts already end in ``[bot]`` and are caught by shape.
BOT_LOGINS = frozenset(
    {"dependabot", "dependabot-preview", "google-labs-jules", "renovate", "renovate-bot"}
)

#: The name each bot stamps on its own commit subjects, after whatever emoji it
#: leads with: ``⚡ Bolt: Optimize …``, ``🛡️ Sentinel: [CRITICAL] Fix …``.
BOT_SUBJECT_MARKER = re.compile(
    r"^[^\w]*(bolt|palette|sentinel|jules|dependabot)\s*:",
    re.IGNORECASE,
)

#: Committer identities that say nothing about who pushed. ``web-flow`` is the
#: key GitHub signs every web-UI commit with, so it appears on a human's edit and
#: on a bot's alike; reading it either way would invent a push nobody made.
NEUTRAL_TOKENS = frozenset({"web", "flow", "github", "noreply"})

#: Split a login / name / e-mail local part into comparable tokens.
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _tokens(value: str) -> set[str]:
    """Lower-case word tokens of an identity string (``[bot]`` becomes ``bot``)."""
    return {t for t in _TOKEN_SPLIT.split(value.strip().lower()) if t}


def _normalize(value: str) -> str:
    """An identity string reduced to ``lower-case-words-joined-by-hyphens``."""
    return "-".join(t for t in _TOKEN_SPLIT.split(value.strip().lower()) if t)


def first_branch_segment(head_ref: str) -> str:
    """The leading ``/``- or ``-``-delimited segment of a branch name, lower-cased.

    ``sentinel/fix-git-stderr-leak-1004…`` and ``sentinel-fix-workspace-write-33…``
    are the same bot; both spellings are in this repository's history.
    """
    return re.split(r"[/-]", head_ref.strip().lower(), maxsplit=1)[0]


def is_bot_branch(head_ref: str) -> bool:
    """Whether a branch name marks the branch as owned by an automation."""
    return first_branch_segment(head_ref) in BOT_BRANCH_PREFIXES


@dataclass(frozen=True)
class Actor:
    """One identity attached to a commit, reduced to what gets compared."""

    login: str = ""
    name: str = ""
    email: str = ""
    #: GitHub's own ``type`` for the linked account ("User" / "Bot"), when there is one.
    account_type: str = ""

    def identity(self) -> str:
        """The most specific label available, for use in an error message."""
        return self.login or self.name or self.email or "an unidentified account"

    def is_bot_account(self) -> bool:
        """Whether this identity is automation *in its own right* — Dependabot, say.

        False for the bots that commit as the repository owner; those are caught
        by the branch and the subject marker instead.
        """
        if self.account_type.strip().lower() == "bot":
            return True
        if "bot" in _tokens(self.login) | _tokens(self.name):
            return True
        return any(_normalize(value) in BOT_LOGINS for value in (self.login, self.name) if value)

    def is_neutral(self) -> bool:
        """Whether this identity names nobody in particular (``web-flow``, empty)."""
        tokens = _tokens(self.login) | _tokens(self.name)
        if self.email:
            tokens |= _tokens(self.email.split("@", 1)[0].split("+", 1)[-1])
        return not tokens or tokens <= NEUTRAL_TOKENS

    def same_person_as(self, other: Actor) -> bool:
        """Whether two identities are the same account, as far as can be told."""
        if self.login and other.login:
            return self.login.lower() == other.login.lower()
        return (self.email.lower(), self.name.lower()) == (other.email.lower(), other.name.lower())


@dataclass(frozen=True)
class Commit:
    """One commit on the pull request branch, with both of its identities."""

    sha: str
    subject: str
    author: Actor
    committer: Actor
    parents: tuple[str, ...] = field(default=())

    def is_merge(self) -> bool:
        return len(self.parents) > 1

    def kind(self) -> str:
        """``"bot"``, ``"human"`` or ``"neutral"`` for the commit as a whole.

        A merge is ``neutral``: ``Merge branch 'main' into <bot-branch>`` is the
        routine "Update branch" click, it carries no work of its own, and losing
        it to a force-push costs nothing — treating it as a human push would fire
        this guard on most of the bot pull requests in the history.

        A commit whose author and committer are different people is ``human``
        even when the *content* is the bot's: that is a rebase or a cherry-pick,
        which is a person taking hold of the branch, and it is the only trace
        left when a maintainer rebases a bot branch and adds nothing of their
        own. It is a secondary signal, not a load-bearing one — where the bot
        commits *as* the maintainer, as Bolt and Sentinel do here, a rebase
        leaves both identities on the same account and this cannot see it.
        """
        if self.is_merge():
            return "neutral"
        rewritten_by_someone_else = not self.committer.is_neutral() and not (
            self.committer.same_person_as(self.author)
        )
        if rewritten_by_someone_else and not self.committer.is_bot_account():
            return "human"
        if BOT_SUBJECT_MARKER.match(self.subject) or self.author.is_bot_account():
            return "bot"
        if self.author.is_neutral():
            return "neutral"
        return "human"

    def describe(self) -> str:
        """``<sha> by <who> — <subject>``, the one line a reader has to act on."""
        who = self.author.identity()
        if not self.committer.is_neutral() and not self.committer.same_person_as(self.author):
            who = f"{who}, committed by {self.committer.identity()}"
        line = f"`{self.sha}` by {who}"
        return f"{line} — {self.subject}" if self.subject else line


def _actor(api_account: object, git_identity: object) -> Actor:
    """Build an :class:`Actor` from the two halves the commits endpoint returns.

    ``api_account`` is the linked GitHub account (``null`` when the e-mail matches
    nobody); ``git_identity`` is the raw name/e-mail off the commit object itself.
    """
    account = api_account if isinstance(api_account, dict) else {}
    identity = git_identity if isinstance(git_identity, dict) else {}
    return Actor(
        login=str(account.get("login") or ""),
        name=str(identity.get("name") or ""),
        email=str(identity.get("email") or ""),
        account_type=str(account.get("type") or ""),
    )


def parse_commits(payload: Sequence[object]) -> list[Commit]:
    """Normalize ``GET /repos/{repo}/pulls/{n}/commits`` into :class:`Commit` objects.

    The endpoint returns oldest-first, which is the order every rule below relies
    on. Entries are kept in the order given rather than re-sorted by date: a
    rebase rewrites committer dates and an amended commit can carry any author
    date at all, so a date sort would reorder exactly the histories that matter.
    """
    commits: list[Commit] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        raw = entry.get("commit")
        raw_commit = raw if isinstance(raw, dict) else {}
        message = str(raw_commit.get("message") or "")
        raw_parents = entry.get("parents")
        parents = raw_parents if isinstance(raw_parents, list) else []
        commits.append(
            Commit(
                sha=str(entry.get("sha") or ""),
                subject=message.splitlines()[0] if message else "",
                author=_actor(entry.get("author"), raw_commit.get("author")),
                committer=_actor(entry.get("committer"), raw_commit.get("committer")),
                parents=tuple(str(p.get("sha") or "") for p in parents if isinstance(p, dict)),
            )
        )
    return commits


def classify_branch(commits: Sequence[Commit]) -> list[str]:
    """Each commit's kind, with one correction only the whole branch can make.

    A bot sometimes pushes a commit *without* its own subject marker and then
    re-pushes the same change with one — #632 in this repository is exactly that,
    and read commit by commit the unmarked one is indistinguishable from a
    person's. Seeing the whole branch resolves it: a commit whose subject is a
    later bot commit's subject with the marker taken off is that bot's own work.
    """
    kinds = [commit.kind() for commit in commits]
    bot_subjects = {
        BOT_SUBJECT_MARKER.sub("", commit.subject).strip().lower()
        for commit, kind in zip(commits, kinds, strict=True)
        if kind == "bot" and commit.subject.strip()
    }
    return [
        "bot" if kind == "human" and commit.subject.strip().lower() in bot_subjects else kind
        for commit, kind in zip(commits, kinds, strict=True)
    ]


def find_bot_push_after_human(commits: Sequence[Commit]) -> tuple[Commit, Commit] | None:
    """The ``(human, bot)`` pair proving a bot pushed on top of a human's work.

    Reports the *last* human commit and the *first* bot commit after it: those two
    bracket the window the overwrite happened in, which is what a reader needs in
    order to find the tree that was replaced.
    """
    last_human: Commit | None = None
    for commit, kind in zip(commits, classify_branch(commits), strict=True):
        if kind == "human":
            last_human = commit
        elif kind == "bot" and last_human is not None:
            return last_human, commit
    return None


def evaluate(head_ref: str, commits: Sequence[Commit]) -> tuple[bool, str]:
    """``(ok, report)`` for one pull request's branch.

    ``report`` is Markdown and is the whole of what the job writes to its step
    summary. A failing check that does not name the two commits is the check that
    got ignored on 2026-09-03, when CI went red and nobody could see why.
    """
    if not is_bot_branch(head_ref):
        return True, (
            f"`{head_ref}` is not a bot-owned branch, so the read-only-branch rule does not apply."
        )
    if not commits:
        return True, f"`{head_ref}` has no commits, so there is nothing to check."

    hit = find_bot_push_after_human(commits)
    if hit is None:
        return True, (
            f"`{head_ref}` is bot-owned, {len(commits)} commit(s), and no bot push "
            "follows a human push."
        )

    human, bot = hit
    return False, (
        "### Bot push after a human push on a bot-owned branch\n\n"
        f"Branch `{head_ref}`:\n\n"
        f"- Human commit: {human.describe()}\n"
        f"- Bot commit after it: {bot.describe()}\n\n"
        "A bot-owned pull request branch is a **read-only input**. The bot pushes "
        "from its own checkout, so a push landing after a human touched the branch "
        "replaces the reviewed tree with the bot's copy of it and silently reverts "
        "whatever went in between — on #648 that was 25 files and two merged pull "
        "requests (#676).\n\n"
        "To resolve: re-land the reviewed changes on a fresh `fix/`, `perf/` or "
        "`docs/` branch cut from `main`, then close this pull request with a link "
        "to the replacement. Do not push to this branch again."
    )


def _decode_paginated(stdout: str) -> list[dict]:
    """Flatten ``gh api --paginate`` output, which is one JSON array per page.

    ``gh`` concatenates the pages rather than merging them, so a two-page result
    is ``[...][...]`` — not a document any single ``json.loads`` accepts.
    """
    decoder = json.JSONDecoder()
    items: list[dict] = []
    text = stdout.strip()
    index = 0
    while index < len(text):
        value, index = decoder.raw_decode(text, index)
        if isinstance(value, list):
            items.extend(v for v in value if isinstance(v, dict))
        while index < len(text) and text[index].isspace():
            index += 1
    return items


def _gh(args: Sequence[str]) -> str:
    res = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {res.stderr.strip() or 'gh failed'}")
    return res.stdout


def fetch_head_ref(repo: str, pr: int) -> str:
    """The pull request's branch name — the thing that decides bot ownership."""
    data = json.loads(_gh(["api", f"repos/{repo}/pulls/{pr}"]))
    head = data.get("head") if isinstance(data, dict) else None
    if not isinstance(head, dict) or not head.get("ref"):
        raise RuntimeError(f"no head branch in the response for PR #{pr}")
    return str(head["ref"])


def fetch_pr_commits(repo: str, pr: int) -> list[dict]:
    """Read the branch's commits through ``gh``."""
    return _decode_paginated(_gh(["api", "--paginate", f"repos/{repo}/pulls/{pr}/commits"]))


def write_summary(report: str, summary_path: str | None) -> None:
    """Append the report to the GitHub step summary, when running inside Actions."""
    if not summary_path:
        return
    heading = "" if report.lstrip().startswith("#") else "### Bot push guard\n\n"
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write(f"{heading}{report}\n")


def main(
    argv: Sequence[str] | None = None,
    *,
    fetch_ref: Callable[[str, int], str] = fetch_head_ref,
    fetch_commits: Callable[[str, int], list[dict]] = fetch_pr_commits,
) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when a bot pushed to a bot-owned pull request branch after a human did."
    )
    parser.add_argument("--repo", required=True, help="owner/name of the repository")
    parser.add_argument("--pr", required=True, type=int, help="pull request number")
    parser.add_argument(
        "--summary-file",
        default=os.environ.get("GITHUB_STEP_SUMMARY"),
        help="append the report here (defaults to $GITHUB_STEP_SUMMARY)",
    )
    args = parser.parse_args(argv)

    try:
        head_ref = fetch_ref(args.repo, args.pr)
        payload = fetch_commits(args.repo, args.pr) if is_bot_branch(head_ref) else []
    except (OSError, RuntimeError, ValueError) as exc:
        # A guard that could not read the branch has not cleared it. Reporting
        # "could not check" as a pass is the failure mode #676 is about.
        print(f"[FAIL] Bot push guard could not read PR #{args.pr}: {exc}", file=sys.stderr)
        return 2

    ok, report = evaluate(head_ref, parse_commits(payload))
    write_summary(report, args.summary_file)

    if ok:
        print(f"[OK] Bot push guard: {report}")
        return 0
    print(f"\n[FAIL] Bot push guard\n\n{report}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
