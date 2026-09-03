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

What this check does and does not see
-------------------------------------
Its accuracy is bounded by the marker convention, not by the rule. Where the bots
push under the maintainer's own account, the subject prefix is the *only* thing
separating their commits from a person's, so:

* **an unmarked bot push reads as a human commit** — Bolt did exactly that on
  #632 — which turns the bot's own next push into a reported hit. One branch-wide
  pass recovers the common case (an unmarked push later re-pushed *with* the
  marker), and nothing recovers the rest;
* **a human subject that opens with a bot's name** (``Bolt: revert the change``)
  reads as a bot commit, so a human push wearing that shape is invisible;
* **a rebase is only visible where the bot has its own account.** Where it commits
  as the maintainer, a rebase leaves both identities on the same login and this
  cannot tell it from a plain bot push;
* **a merge is judged by its subject**, since the API reports a first-parent diff
  for a clean merge exactly as it does for a hand-resolved one (verified: the
  clean ``Merge branch 'main' into sentinel/…`` at 92a1d481 reports 4 files and
  +252/-58). A merge of the base branch is therefore read as carrying no work of
  its own even when someone resolved conflicts inside it.

Measured against this repository's history — all 114 bot-owned pull requests —
the check fires on two: #648, the incident, and #631, where a Sentinel push
landed on top of a maintainer's ``sec(cli)`` fix in exactly the same shape.
Within the bound above it errs toward firing: a false positive costs one re-land
on a fresh branch, which is what the policy asks for on a bot branch anyway, and
a false negative costs a silent revert nobody sees.

Design notes
------------
Every decision is a pure function over the JSON GitHub already returns for
``/repos/{repo}/pulls/{n}`` and ``…/commits``; the only I/O is two injectable
fetch callables, and neither runs at all when the branch name says the rule does
not apply. That is what lets the tests be fixture-driven and offline.
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
#:
#: This is the one table to edit when a new automation arrives: the subject
#: markers below are built from it, so a registered bot cannot be recognised on
#: its branch and missed on its commits. Add its login to :data:`BOT_LOGINS` as
#: well when it pushes under an account of its own.
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
#: GitHub App accounts already end in ``[bot]`` and are caught by shape; this is
#: for the spellings that do not.
BOT_LOGINS = frozenset(
    {
        "copilot",
        "copilot-swe-agent",
        "dependabot",
        "dependabot-preview",
        "google-labs-jules",
        "renovate",
        "renovate-bot",
    }
)

#: The name each bot stamps on its own commit subjects, after whatever emoji it
#: leads with: ``⚡ Bolt: Optimize …``, ``🛡️ Sentinel: [CRITICAL] Fix …``. Derived
#: from :data:`BOT_BRANCH_PREFIXES` so the two cannot drift apart. A bare
#: ``word:`` prefix is not enough — ``fix:`` and ``sec(cli):`` are how people
#: write here — so only a registered name counts.
BOT_SUBJECT_MARKER = re.compile(
    r"^[^\w]*(?:" + "|".join(sorted(BOT_BRANCH_PREFIXES)) + r")\s*:",
    re.IGNORECASE,
)

#: ``Merge branch 'main' into <branch>`` — GitHub's "Update branch" click, and
#: ``git merge main`` by hand. Group 1 is the ref that was merged *in*, kept whole:
#: ``topic/main`` is a topic branch somebody wrote, not the base branch, and
#: reading only the last path segment excused it as routine.
MERGE_OF_BRANCH = re.compile(
    r"^merge\s+(?:remote-tracking\s+)?branch\s+'?([^\s']+)'?",
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
    """An identity or branch string reduced to ``lower-case-words-joined-by-hyphens``."""
    return "-".join(t for t in _TOKEN_SPLIT.split(value.strip().lower()) if t)


def is_bot_branch(head_ref: str) -> bool:
    """Whether a branch name marks the branch as owned by an automation.

    A prefix match on the normalized name, not a match on its first delimited
    segment: ``sentinel/fix-…``, ``sentinel-fix-…``, ``bolt_optimize_…`` and
    ``boltfix/…`` are all the same bot naming the same branch four ways, and a
    guard that recognised only some of them would be off wherever it mattered
    most. The over-match is the safe direction — it can only add a branch to the
    rule, never drop one out of it.
    """
    normalized = _normalize(head_ref)
    return any(normalized.startswith(prefix) for prefix in BOT_BRANCH_PREFIXES)


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

    def merges_in(self) -> str:
        """The whole ref this commit merged in, from its subject, or ``""``."""
        found = MERGE_OF_BRANCH.match(self.subject.strip())
        return found.group(1).strip().lower() if found else ""

    def merges_in_the_base(self, base_ref: str) -> bool:
        """Whether this commit merged *the base branch* in, and nothing else.

        Compared against an explicit set — the base, and the two remote spellings
        git writes for it — rather than by taking the last path segment of
        whatever was merged. ``Merge branch 'topic/main' into bolt-x`` ends in the
        base's name while being somebody's topic branch, and excusing it as
        routine is a hole a subject line can be written straight through.
        """
        base = base_ref.strip().lower()
        merged = self.merges_in()
        return bool(base and merged) and merged in {base, f"origin/{base}", f"upstream/{base}"}

    def taken_over_by_a_person(self) -> bool:
        """Whether somebody other than the author created this commit object.

        A rebase or a cherry-pick: the content is still the author's, but a
        person took hold of the branch to put it there. Only visible where the
        two identities are distinguishable — where the bot commits *as* the
        maintainer, both land on the same login and this is always false.
        """
        return (
            not self.committer.is_neutral()
            and not self.committer.same_person_as(self.author)
            and not self.committer.is_bot_account()
        )

    def kind(self, base_ref: str = "") -> str:
        """``"bot"``, ``"human"`` or ``"neutral"`` for the commit as a whole.

        A merge **of the base branch** is ``neutral``: ``Merge branch 'main' into
        <bot-branch>`` is the routine "Update branch" click, it carries no work
        of its own, and counting it as a human push would fire this guard on most
        of the bot pull requests in the history. Only that shape is excused — any
        other merge is classified like any other commit, so a merge of some third
        branch, or one whose subject was rewritten, still registers as a person.

        The exemption is applied **last**, after the bot signals: a push made *as*
        a merge of the base branch is still a push. Testing it first excused a
        merge commit authored by a bot account, which is the incident's own shape
        wearing a routine subject line.
        """
        if self.taken_over_by_a_person():
            return "human"
        if BOT_SUBJECT_MARKER.match(self.subject) or self.author.is_bot_account():
            return "bot"
        if self.is_merge() and self.merges_in_the_base(base_ref):
            return "neutral"
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


def classify_branch(commits: Sequence[Commit], base_ref: str = "") -> list[str]:
    """Each commit's kind, with one correction only the whole branch can make.

    A bot sometimes pushes a commit *without* its own subject marker and then
    re-pushes the same change with one — #632 in this repository is exactly that,
    and read commit by commit the unmarked one is indistinguishable from a
    person's. Seeing the whole branch resolves it: a commit whose subject is a
    **later** bot commit's subject with the marker taken off is that bot's own
    first push of the same work.

    Two guards keep the correction from running backwards, because it can only
    ever turn a human commit into a bot one and so can only ever hide a hit:

    * only *later* bot commits are consulted. Matching an earlier one would erase
      a person's hand-redo of a bot commit that keeps its summary line — the
      sequence ``⚡ Bolt: X`` / ``X`` / ``⚡ Bolt: X more`` is a human push between
      two bot pushes, not three bot pushes;
    * a commit somebody else re-created (:meth:`Commit.taken_over_by_a_person`)
      is never reclassified. A maintainer rebasing Dependabot's branch and
      Dependabot then force-pushing the same commit back is the incident's own
      shape, and the subjects match by construction.
    """
    kinds = [commit.kind(base_ref) for commit in commits]
    stripped = [BOT_SUBJECT_MARKER.sub("", commit.subject).strip().lower() for commit in commits]
    for index, commit in enumerate(commits):
        subject = commit.subject.strip().lower()
        if kinds[index] != "human" or not subject or commit.taken_over_by_a_person():
            continue
        later_bot_subjects = {
            stripped[later]
            for later in range(index + 1, len(commits))
            if kinds[later] == "bot" and stripped[later]
        }
        if subject in later_bot_subjects:
            kinds[index] = "bot"
    return kinds


def find_bot_push_after_human(
    commits: Sequence[Commit], base_ref: str = ""
) -> tuple[Commit, Commit] | None:
    """The ``(human, bot)`` pair proving a bot pushed on top of a human's work.

    Reports the *last* human commit and the *first* bot commit after it: those two
    bracket the window the overwrite happened in, which is what a reader needs in
    order to find the tree that was replaced.
    """
    last_human: Commit | None = None
    for commit, kind in zip(commits, classify_branch(commits, base_ref), strict=True):
        if kind == "human":
            last_human = commit
        elif kind == "bot" and last_human is not None:
            return last_human, commit
    return None


def evaluate(head_ref: str, commits: Sequence[Commit], base_ref: str = "") -> tuple[bool, str]:
    """``(ok, report)`` for one pull request's branch.

    ``report`` is Markdown and is the whole of what the job writes to its step
    summary. A failing check that does not name the two commits is the check that
    got ignored on 2026-09-03, when CI went red and nobody could see why.
    """
    if not is_bot_branch(head_ref):
        return True, (
            f"Out of scope: `{head_ref}` is not a bot-owned branch — its name matches "
            f"none of {', '.join(sorted(BOT_BRANCH_PREFIXES))} — so the read-only-branch "
            "rule does not apply and no commits were read."
        )
    if not commits:
        return True, f"`{head_ref}` has no commits, so there is nothing to check."

    hit = find_bot_push_after_human(commits, base_ref)
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


def fetch_refs(repo: str, pr: int) -> tuple[str, str]:
    """``(head_ref, base_ref)`` for a pull request.

    Only needed when the workflow did not pass them: on a ``pull_request`` event
    both are already in the payload, and taking them from there means a branch
    the rule does not cover costs no API call at all.
    """
    data = json.loads(_gh(["api", f"repos/{repo}/pulls/{pr}"]))
    head = data.get("head") if isinstance(data, dict) else None
    base = data.get("base") if isinstance(data, dict) else None
    if not isinstance(head, dict) or not head.get("ref"):
        raise RuntimeError(f"no head branch in the response for PR #{pr}")
    base_ref = base.get("ref") if isinstance(base, dict) else ""
    return str(head["ref"]), str(base_ref or "")


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
    fetch_ref: Callable[[str, int], tuple[str, str]] = fetch_refs,
    fetch_commits: Callable[[str, int], list[dict]] = fetch_pr_commits,
) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when a bot pushed to a bot-owned pull request branch after a human did."
    )
    parser.add_argument("--repo", required=True, help="owner/name of the repository")
    parser.add_argument("--pr", required=True, type=int, help="pull request number")
    parser.add_argument(
        "--head-ref",
        default="",
        help="the pull request's branch, from the event payload; looked up if omitted",
    )
    parser.add_argument(
        "--base-ref",
        default="",
        help="the branch being merged into, from the event payload; looked up if omitted",
    )
    parser.add_argument(
        "--summary-file",
        default=os.environ.get("GITHUB_STEP_SUMMARY"),
        help="append the report here (defaults to $GITHUB_STEP_SUMMARY)",
    )
    args = parser.parse_args(argv)

    head_ref, base_ref = args.head_ref, args.base_ref
    try:
        if not head_ref:
            head_ref, fetched_base = fetch_ref(args.repo, args.pr)
            base_ref = base_ref or fetched_base
        # A branch the rule does not cover is answered from its name alone, so a
        # repository-read failure can only ever fail the pull requests the guard
        # actually judges.
        payload = fetch_commits(args.repo, args.pr) if is_bot_branch(head_ref) else []
    except (OSError, RuntimeError, ValueError) as exc:
        # A guard that could not read the branch has not cleared it. Reporting
        # "could not check" as a pass is the failure mode #676 is about.
        print(f"[FAIL] Bot push guard could not read PR #{args.pr}: {exc}", file=sys.stderr)
        return 2

    ok, report = evaluate(head_ref, parse_commits(payload), base_ref)
    try:
        write_summary(report, args.summary_file)
    except OSError as exc:
        # The verdict is not the summary's to change. Crashing here would exit
        # non-zero out of a *passing* run and read exactly like a hit; going
        # quiet would drop the report on a failing one. Say so, and carry on —
        # the full report also goes to stderr below.
        print(f"[warn] Bot push guard could not write the step summary: {exc}", file=sys.stderr)

    if ok:
        print(f"[OK] Bot push guard: {report}")
        return 0
    print(f"\n[FAIL] Bot push guard\n\n{report}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
