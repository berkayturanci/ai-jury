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

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish.yml"

PUBLISH_JOB = "build-n-publish"
VERIFY_JOB = "verify"
TEMPLATE = "packaging/homebrew/ai-jury.rb.template"


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

    def test_the_render_step_waits_as_long_as_the_verify_job_does(self):
        """It runs after the upload and before the Release; a short wait half-makes one.

        Sixty seconds against PyPI's index, with the release only partly
        created if it ran out, is the state this whole change removes.
        """
        waits = [line for line in self.publish_code.splitlines() if "seq 1 30" in line]
        self.assertTrue(waits, "the render step no longer uses the five-minute budget")

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
        self.assertIn('pip install "ai-jury==${version}"', self.verify_code)

    def test_it_waits_for_the_index_but_not_forever(self):
        """Publishing is synchronous; indexing is not.

        A job that waits forever for a release that will never appear is
        indistinguishable from a hung runner.
        """
        self.assertIn("did not serve", self.verify_code)
        self.assertIn("seq 1 30", self.verify_code)

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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
