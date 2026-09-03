"""Tests for scripts/bot_push_after_human_push_check.py (#676).

The fixtures are the JSON GitHub actually returns for
``GET /repos/{repo}/pulls/{n}/commits``, taken from this repository's own
history: the optimisation and accessibility bots here commit **as the repository
owner**, so on #648 the bot's four commits and the maintainer's one all carry the
login ``berkayturanci``. That is why :data:`BOT_GIT` and :data:`HUMAN_GIT` differ
only in the way they really differ — the address the push came from — and why the
guard leans on the branch name and the subject marker instead of the account.

Nothing here touches the network or a real repository; the two fetches are
injected. :class:`RealHistoryTests` replays the three branches the classifier was
actually calibrated against.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_script = REPO_ROOT / "scripts" / "bot_push_after_human_push_check.py"
_spec = importlib.util.spec_from_file_location("bot_push_after_human_push_check", _script)
guard = importlib.util.module_from_spec(_spec)
# Registered before execution, not after: the script's `@dataclass` declarations
# run at import time and resolve their string annotations through
# `sys.modules[cls.__module__]`, which a path-loaded module does not have yet.
sys.modules[_spec.name] = guard
_spec.loader.exec_module(guard)


BOT_BRANCH = "bolt-optimize-looks-like-patch-13942939481763764681"
#: The bot commits through the API, so its commits carry the account's noreply
#: address; the maintainer pushes from a working copy under their own.
OWNER_ACCOUNT = {"login": "berkayturanci", "type": "User"}
BOT_GIT = {"name": "berkayturanci", "email": "4395053+berkayturanci@users.noreply.github.com"}
HUMAN_GIT = {"name": "Berkay Turanci", "email": "berkay@example.com"}


def commit(
    sha: str,
    subject: str,
    *,
    author: dict | None = None,
    committer: dict | None = None,
    author_account: object = OWNER_ACCOUNT,
    committer_account: object = OWNER_ACCOUNT,
    parents: int = 1,
) -> dict:
    """One entry of the commits payload."""
    return {
        "sha": sha,
        "commit": {
            "message": f"{subject}\n\nbody",
            "author": author or BOT_GIT,
            "committer": committer or author or BOT_GIT,
        },
        "author": author_account,
        "committer": committer_account,
        "parents": [{"sha": "0" * 40}] * parents,
    }


def bot_commit(sha: str, subject: str = "⚡ Bolt: Optimize the thing", **kw) -> dict:
    return commit(sha, subject, **kw)


def human_commit(sha: str, subject: str = "test: pin the behaviour", **kw) -> dict:
    kw.setdefault("author", HUMAN_GIT)
    return commit(sha, subject, **kw)


def kinds(*entries: dict) -> list[str]:
    return guard.classify_branch(guard.parse_commits(list(entries)))


class BranchOwnershipTests(unittest.TestCase):
    def test_every_bot_branch_prefix_in_this_repository_is_recognised(self):
        for ref in (
            "bolt-optimize-looks-like-patch-1394293948",
            "bolt/optimize-patch-marker-check-1260354613",
            "palette/skip-to-content-docs-3619981363",
            "palette-modal-focus-4806425419",
            "sentinel/fix-git-stderr-leak-1004441052",
            "sentinel-exception-chaining-6518975861",
            "dependabot/github_actions/actions/checkout-7",
        ):
            with self.subTest(ref=ref):
                self.assertTrue(guard.is_bot_branch(ref))

    def test_an_agent_branch_a_person_drives_is_not_bot_owned(self):
        # `claude/…` and `codex/…` branches are worked from a local checkout by
        # the person at the keyboard, who is meant to keep pushing to them. The
        # rule is about a branch something else holds the only copy of.
        for ref in ("claude/issue-676-guard", "codex/fix-adapter", "fix/site-version-guard"):
            with self.subTest(ref=ref):
                self.assertFalse(guard.is_bot_branch(ref))

    def test_a_human_branch_is_reported_as_skipped_rather_than_checked(self):
        ok, report = guard.evaluate(
            "fix/whatever", guard.parse_commits([human_commit("a" * 40), bot_commit("b" * 40)])
        )
        self.assertTrue(ok)
        self.assertIn("not a bot-owned branch", report)


class CommitKindTests(unittest.TestCase):
    def test_each_bot_stamps_its_own_subject(self):
        for subject in (
            "⚡ Bolt: Optimize _looks_like_patch string searches",
            "🎨 Palette: Fix skip-to-content visibility in docs",
            "🛡️ Sentinel: [CRITICAL] Fix git stderr unredacted leakage",
            "Jules: refresh the scaffold",
        ):
            with self.subTest(subject=subject):
                self.assertEqual(kinds(commit("a" * 40, subject)), ["bot"])

    def test_a_conventional_commit_prefix_is_not_a_bot_marker(self):
        # `fix:` and `sec(cli):` are how people write here; matching a bare
        # `word:` prefix would classify the whole repository as bot work.
        for subject in ("fix(adapters): repair the spawn path", "sec(cli): correct the severity"):
            with self.subTest(subject=subject):
                self.assertEqual(kinds(human_commit("a" * 40, subject)), ["human"])

    def test_an_account_that_is_itself_a_bot_needs_no_marker(self):
        entry = commit(
            "a" * 40,
            "ci: bump the github-actions group with 2 updates",
            author={
                "name": "dependabot[bot]",
                "email": "49699333+dependabot[bot]@users.noreply.github.com",
            },
            author_account={"login": "dependabot[bot]", "type": "Bot"},
            committer_account={"login": "dependabot[bot]", "type": "Bot"},
        )
        self.assertEqual(kinds(entry), ["bot"])

    def test_a_merge_from_the_base_branch_is_neither(self):
        # "Merge branch 'main' into <bot-branch>" is the routine Update-branch
        # click. It carries no work of its own, and counting it as a human push
        # would fire this guard on most of the bot pull requests in the history.
        entry = human_commit("a" * 40, f"Merge branch 'main' into {BOT_BRANCH}", parents=2)
        self.assertEqual(kinds(entry), ["neutral"])

    def test_a_commit_rewritten_by_someone_else_reads_as_a_human_touch(self):
        # A bot with its own account, whose commits a maintainer then rebased.
        entry = commit(
            "a" * 40,
            "⚡ Bolt: Optimize the thing",
            author_account={"login": "bolt-app[bot]", "type": "Bot"},
            committer_account={"login": "berkayturanci", "type": "User"},
            committer=HUMAN_GIT,
        )
        self.assertEqual(kinds(entry), ["human"])

    def test_a_commit_naming_nobody_is_neutral(self):
        self.assertEqual(guard.parse_commits([{"sha": "a" * 40}])[0].kind(), "neutral")


class ClassifyBranchTests(unittest.TestCase):
    def test_an_unmarked_bot_commit_is_recovered_from_its_remarked_twin(self):
        # PR #632: Bolt pushed the change once without its marker and once with
        # it. Read commit by commit the first is indistinguishable from a
        # person's; read against the branch it is plainly the same work.
        subject = "Optimize large patch detection to avoid memory allocation"
        self.assertEqual(
            kinds(commit("a" * 40, subject), bot_commit("b" * 40, f"⚡ Bolt: {subject}")),
            ["bot", "bot"],
        )

    def test_a_genuinely_different_subject_stays_human(self):
        self.assertEqual(
            kinds(
                bot_commit("a" * 40, "⚡ Bolt: Optimize the thing"),
                human_commit("b" * 40, "test: pin the behaviour"),
            ),
            ["bot", "human"],
        )

    def test_an_empty_subject_does_not_swallow_every_other_commit(self):
        # An empty subject must not enter the set of known bot subjects, or every
        # other subject-less commit on the branch would be reclassified with it.
        dependabot = commit(
            "a" * 40,
            "",
            author_account={"login": "dependabot[bot]", "type": "Bot"},
            committer_account={"login": "dependabot[bot]", "type": "Bot"},
        )
        self.assertEqual(kinds(dependabot, human_commit("b" * 40, "")), ["bot", "human"])


class RealHistoryTests(unittest.TestCase):
    """The branches this classifier was calibrated against, replayed as fixtures."""

    def test_pr_648_the_incident(self):
        # The maintainer rebased the branch onto main and added a test; Bolt then
        # pushed its stale tree twice from its own checkout, reverting #669/#655.
        commits = guard.parse_commits(
            [
                bot_commit("53a59813" + "0" * 32, committer=HUMAN_GIT),
                human_commit("f39e9fea" + "0" * 32, "test: pin _looks_like_patch after splitlines"),
                bot_commit("28d9cc3c" + "0" * 32),
                bot_commit("b2209668" + "0" * 32),
            ]
        )
        human, bot = guard.find_bot_push_after_human(commits)
        self.assertEqual(human.sha[:8], "f39e9fea")
        self.assertEqual(bot.sha[:8], "28d9cc3c")

        ok, report = guard.evaluate(BOT_BRANCH, commits)
        self.assertFalse(ok)
        self.assertIn("f39e9fea", report)
        self.assertIn("28d9cc3c", report)
        self.assertIn("read-only", report)
        self.assertIn(BOT_BRANCH, report)

    def test_pr_631_the_same_shape_on_a_sentinel_branch(self):
        commits = guard.parse_commits(
            [
                bot_commit("50c53a10" + "0" * 32, "🛡️ Sentinel: [CRITICAL] Fix git stderr leakage"),
                human_commit("a123c267" + "0" * 32, "sec(cli): correct the severity"),
                bot_commit("0bc34114" + "0" * 32, "🛡️ Sentinel: [LOW] Prevent stderr leakage"),
            ]
        )
        ok, report = guard.evaluate("sentinel/fix-git-stderr-leak-1004441052", commits)
        self.assertFalse(ok)
        self.assertIn("a123c267", report)
        self.assertIn("0bc34114", report)

    def test_pr_632_an_unmarked_first_push_is_not_a_false_alarm(self):
        subject = "Optimize large patch detection to avoid memory allocation"
        commits = guard.parse_commits(
            [
                commit("f97c13bf" + "0" * 32, subject),
                bot_commit("9bfc8c43" + "0" * 32, f"⚡ Bolt: {subject}"),
            ]
        )
        ok, _ = guard.evaluate("bolt/optimize-patch-marker-check-1260354613", commits)
        self.assertTrue(ok)

    def test_the_ordinary_bot_pull_request_passes(self):
        commits = guard.parse_commits([bot_commit("a" * 40), bot_commit("b" * 40)])
        self.assertIsNone(guard.find_bot_push_after_human(commits))
        ok, report = guard.evaluate(BOT_BRANCH, commits)
        self.assertTrue(ok)
        self.assertIn("no bot push follows a human push", report)

    def test_a_human_finishing_a_bot_branch_passes(self):
        # Bot opens the branch, a person amends it, nothing lands afterwards:
        # there is nothing a stale push could have overwritten.
        commits = guard.parse_commits([bot_commit("a" * 40), human_commit("b" * 40)])
        self.assertIsNone(guard.find_bot_push_after_human(commits))
        self.assertTrue(guard.evaluate(BOT_BRANCH, commits)[0])

    def test_the_pair_reported_is_the_last_human_and_the_first_bot_after_it(self):
        commits = guard.parse_commits(
            [
                human_commit("1" * 40, "test: one"),
                human_commit("2" * 40, "test: two"),
                bot_commit("3" * 40),
                bot_commit("4" * 40, "⚡ Bolt: Optimize something else"),
            ]
        )
        human, bot = guard.find_bot_push_after_human(commits)
        self.assertEqual((human.sha, bot.sha), ("2" * 40, "3" * 40))

    def test_an_empty_bot_branch_is_not_a_failure(self):
        ok, report = guard.evaluate(BOT_BRANCH, [])
        self.assertTrue(ok)
        self.assertIn("no commits", report)


class ParseCommitsTests(unittest.TestCase):
    def test_both_identities_and_the_parents_survive_the_parse(self):
        (parsed,) = guard.parse_commits([bot_commit("a" * 40, committer=HUMAN_GIT, parents=2)])
        self.assertEqual(parsed.sha, "a" * 40)
        self.assertEqual(parsed.subject, "⚡ Bolt: Optimize the thing")
        self.assertEqual(parsed.author.email, BOT_GIT["email"])
        self.assertEqual(parsed.committer.email, HUMAN_GIT["email"])
        self.assertTrue(parsed.is_merge())

    def test_a_malformed_entry_does_not_crash_the_guard(self):
        commits = guard.parse_commits(
            ["not a dict", {}, {"sha": "b" * 40, "commit": None, "parents": "nope"}]
        )
        self.assertEqual([c.sha for c in commits], ["", "b" * 40])
        self.assertEqual(commits[1].parents, ())

    def test_an_unlinked_account_still_carries_its_git_identity(self):
        (parsed,) = guard.parse_commits([bot_commit("c" * 40, author_account=None)])
        self.assertEqual(parsed.author.login, "")
        self.assertEqual(parsed.author.email, BOT_GIT["email"])


class ActorTests(unittest.TestCase):
    def test_the_web_ui_committer_key_is_neutral(self):
        # GitHub signs every web-UI commit as `web-flow`, on a human's edit and
        # on a bot's alike, so it must not be read as either.
        actor = guard.Actor(login="web-flow", name="GitHub", email="noreply@github.com")
        self.assertTrue(actor.is_neutral())
        self.assertFalse(actor.is_bot_account())

    def test_a_bot_suffixed_login_needs_no_account_type(self):
        # A `gh api` response can omit the linked account's type; the `[bot]`
        # suffix GitHub Apps carry is enough on its own.
        self.assertTrue(guard.Actor(login="dependabot[bot]").is_bot_account())

    def test_a_bot_login_without_the_bot_suffix_is_still_a_bot_account(self):
        self.assertTrue(guard.Actor(login="google-labs-jules").is_bot_account())
        self.assertTrue(guard.Actor(name="renovate").is_bot_account())

    def test_a_person_is_not_a_bot_account(self):
        self.assertFalse(guard.Actor(login="berkayturanci", name="Berkay").is_bot_account())

    def test_identities_are_compared_by_login_when_both_have_one(self):
        left = guard.Actor(login="berkayturanci", email="a@example.com")
        right = guard.Actor(login="BerkayTuranci", email="b@example.com")
        self.assertTrue(left.same_person_as(right))

    def test_identities_without_logins_fall_back_to_name_and_email(self):
        left = guard.Actor(name="Berkay", email="a@example.com")
        self.assertTrue(left.same_person_as(guard.Actor(name="Berkay", email="a@example.com")))
        self.assertFalse(left.same_person_as(guard.Actor(name="Berkay", email="b@example.com")))

    def test_an_unidentified_account_is_labelled(self):
        self.assertEqual(guard.Actor().identity(), "an unidentified account")


class DescribeTests(unittest.TestCase):
    def test_a_commit_with_no_subject_still_describes_itself(self):
        (parsed,) = guard.parse_commits([commit("a" * 40, "")])
        self.assertEqual(parsed.describe(), f"`{'a' * 40}` by berkayturanci")

    def test_a_rebase_names_the_person_who_re_created_the_commit(self):
        entry = bot_commit(
            "a" * 40,
            author_account={"login": "bolt-app[bot]", "type": "Bot"},
            committer_account={"login": "berkayturanci", "type": "User"},
        )
        (parsed,) = guard.parse_commits([entry])
        self.assertIn("committed by berkayturanci", parsed.describe())

    def test_a_web_ui_committer_is_not_named_as_the_pusher(self):
        entry = human_commit(
            "a" * 40,
            committer={"name": "GitHub", "email": "noreply@github.com"},
            committer_account={"login": "web-flow", "type": "User"},
        )
        (parsed,) = guard.parse_commits([entry])
        self.assertNotIn("web-flow", parsed.describe())


class PaginationTests(unittest.TestCase):
    def test_concatenated_pages_are_flattened(self):
        # `gh api --paginate` concatenates one array per page, which json.loads
        # rejects outright; a guard that returned [] here would report a pass.
        stdout = json.dumps([{"sha": "a"}]) + "\n" + json.dumps([{"sha": "b"}]) + "\n"
        self.assertEqual(guard._decode_paginated(stdout), [{"sha": "a"}, {"sha": "b"}])

    def test_empty_output_decodes_to_nothing(self):
        self.assertEqual(guard._decode_paginated("   "), [])

    def test_a_non_list_response_yields_nothing(self):
        self.assertEqual(guard._decode_paginated('{"message":"Not Found"}'), [])


class FetchTests(unittest.TestCase):
    def _run(self, returncode: int, stdout: str = "", stderr: str = ""):
        completed = unittest.mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)
        return unittest.mock.patch.object(guard.subprocess, "run", return_value=completed)

    def test_commits_are_read_through_gh(self):
        with self._run(0, json.dumps([{"sha": "a"}])) as run:
            self.assertEqual(guard.fetch_pr_commits("o/r", 7), [{"sha": "a"}])
        self.assertEqual(
            run.call_args[0][0], ["gh", "api", "--paginate", "repos/o/r/pulls/7/commits"]
        )

    def test_a_failed_gh_call_raises_rather_than_returning_nothing(self):
        with self._run(1, stderr="HTTP 404"), self.assertRaises(RuntimeError) as caught:
            guard.fetch_pr_commits("o/r", 7)
        self.assertIn("HTTP 404", str(caught.exception))

    def test_a_silent_gh_failure_still_names_itself(self):
        with self._run(1), self.assertRaises(RuntimeError) as caught:
            guard.fetch_head_ref("o/r", 7)
        self.assertIn("gh failed", str(caught.exception))

    def test_the_head_ref_is_read_from_the_pull_request(self):
        with self._run(0, json.dumps({"head": {"ref": BOT_BRANCH}})):
            self.assertEqual(guard.fetch_head_ref("o/r", 7), BOT_BRANCH)

    def test_a_response_without_a_head_branch_is_an_error_not_an_empty_ref(self):
        # An empty ref would read as "not a bot branch" and pass silently.
        for body in ("{}", '{"head": {}}', "[]"):
            with self.subTest(body=body), self._run(0, body), self.assertRaises(RuntimeError):
                guard.fetch_head_ref("o/r", 7)


class MainTests(unittest.TestCase):
    def _main(self, head_ref, payload, *, extra_argv=(), ref_error=None):
        def fetch_ref(repo, pr):
            self.assertEqual((repo, pr), ("o/r", 7))
            if ref_error is not None:
                raise ref_error
            return head_ref

        def fetch_commits(*_args):
            return payload

        argv = ["--repo", "o/r", "--pr", "7", *extra_argv]
        # $GITHUB_STEP_SUMMARY is the default for --summary-file and is a real
        # path when this suite itself runs in Actions; blank it so a test that
        # does not ask for a summary cannot append to the running job's.
        with unittest.mock.patch.dict(guard.os.environ, {"GITHUB_STEP_SUMMARY": ""}):
            return guard.main(argv, fetch_ref=fetch_ref, fetch_commits=fetch_commits)

    def test_a_clean_bot_branch_exits_zero(self):
        with unittest.mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = self._main(BOT_BRANCH, [bot_commit("a" * 40)])
        self.assertEqual(code, 0)
        self.assertIn("[OK] Bot push guard", out.getvalue())

    def test_a_bot_push_after_a_human_exits_one_and_reports_on_stderr(self):
        payload = [human_commit("1" * 40), bot_commit("2" * 40)]
        with unittest.mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = self._main(BOT_BRANCH, payload)
        self.assertEqual(code, 1)
        self.assertIn("1" * 40, err.getvalue())
        self.assertIn("2" * 40, err.getvalue())

    def test_a_human_branch_is_not_even_fetched(self):
        def explode(*_args):  # pragma: no cover - must never be reached
            raise AssertionError("the commits should not be fetched for a human branch")

        with (
            unittest.mock.patch.dict(guard.os.environ, {"GITHUB_STEP_SUMMARY": ""}),
            unittest.mock.patch("sys.stdout", new_callable=io.StringIO),
        ):
            code = guard.main(
                ["--repo", "o/r", "--pr", "7"],
                fetch_ref=lambda *_: "fix/whatever",
                fetch_commits=explode,
            )
        self.assertEqual(code, 0)

    def test_an_unreadable_pull_request_fails_rather_than_passing(self):
        with unittest.mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = self._main(BOT_BRANCH, [], ref_error=RuntimeError("HTTP 403"))
        self.assertEqual(code, 2)
        self.assertIn("could not read PR #7", err.getvalue())

    def test_the_report_is_appended_to_the_step_summary(self):
        payload = [human_commit("1" * 40), bot_commit("2" * 40)]
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            summary.write_text("existing\n", encoding="utf-8")
            with unittest.mock.patch("sys.stderr", new_callable=io.StringIO):
                self._main(BOT_BRANCH, payload, extra_argv=["--summary-file", str(summary)])
            written = summary.read_text(encoding="utf-8")
        self.assertTrue(written.startswith("existing\n"))
        self.assertIn("Bot push after a human push", written)
        self.assertIn("2" * 40, written)

    def test_a_passing_run_gets_a_heading_in_the_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            with unittest.mock.patch("sys.stdout", new_callable=io.StringIO):
                self._main(
                    BOT_BRANCH, [bot_commit("a" * 40)], extra_argv=["--summary-file", str(summary)]
                )
            self.assertIn("### Bot push guard", summary.read_text(encoding="utf-8"))

    def test_no_summary_path_writes_no_file(self):
        # The default is $GITHUB_STEP_SUMMARY, which is unset outside Actions.
        with unittest.mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(self._main(BOT_BRANCH, [bot_commit("a" * 40)]), 0)


class WiringTests(unittest.TestCase):
    """The guard only guards anything if CI runs it and the policy is written down."""

    def _read(self, relative: str) -> str:
        return (REPO_ROOT / relative).read_text(encoding="utf-8")

    def test_ci_runs_the_guard_on_pull_requests(self):
        ci = self._read(".github/workflows/ci.yml")
        self.assertIn("bot-push-guard:", ci)
        self.assertIn("scripts/bot_push_after_human_push_check.py", ci)
        self.assertIn("if: github.event_name == 'pull_request'", ci)
        # Reading the commit list needs the pull-requests scope; the workflow's
        # top-level grant is contents-only.
        self.assertIn("pull-requests: read", ci)

    def test_the_read_only_branch_policy_is_written_down(self):
        contributing = self._read("CONTRIBUTING.md")
        self.assertIn("read-only", contributing)
        self.assertIn("bot_push_after_human_push_check.py", contributing)


if __name__ == "__main__":
    unittest.main()
