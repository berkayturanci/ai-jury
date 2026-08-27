"""The release must repair its own formula, not report that someone should.

`Formula/ai-jury.rb` names the sdist url and sha256 of the release being cut.
Neither is knowable until the tag exists, so at the moment `publish.yml` runs,
the file in this repository still describes the *previous* release.

Both halves of that gap have now failed in production, three days apart:

* This repo *did* try to commit it, with `git push origin HEAD:main || true`.
  Branch protection was added the same morning (#620), so the push was refused
  and the `|| true` reported success. 1.15.0 shipped a stale digest and the tap
  — which pulls from `main` on a schedule — refused every sync for a day, one
  failure email per hour, with nothing near the release to explain it. #633
  turned that swallow into a loud `::error::`, which made it visible without
  making it fixed.
* The sibling repository emitted a `::notice::` carrying the correct digest and
  left the edit to a human. For its 1.19.1 nobody did it, and its tap failed the
  same way. A message no one is required to act on is not a safeguard.

The surviving design is a pull request opened by the workflow itself: the only
write to a protected `main` that can succeed, and unlike a message it is a thing
on a list rather than a line in the log of a release that already went green.

These tests pin that shape. They assert over the *code* in the step, with comment
lines removed first — the prose in this workflow discusses at length the direct
push it no longer performs, and a grep would match that discussion happily and
report the old behaviour as guarded.

Read without a YAML parser, for the reason given in `test_github_action.py`:
ai-jury declares `dependencies = []` and three dev tools, and a test is not a
good enough reason to make PyYAML the exception. The cost is that a restructured
workflow could go unrecognised and quietly assert nothing, so the scan below is
checked against the file's own text before anything is concluded from it.
"""

from __future__ import annotations

import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "publish.yml"

#: The step that edits the formula, identified by what it does rather than by its
#: name, which is free to change.
STEP = "- name: Update in-repo Homebrew Formula"
JOB = "build-n-publish:"
MARKER = "Formula/ai-jury.rb"


def block_after(lines: list[str], index: int) -> list[str]:
    """The run of lines indented deeper than ``lines[index]``, blanks included."""
    indent = len(lines[index]) - len(lines[index].lstrip())
    body: list[str] = []
    for line in lines[index + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line)
    return body


class TheFormulaStepOpensAPullRequest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
        step_at = [i for i, line in enumerate(cls.lines) if line.strip() == STEP]
        assert len(step_at) == 1, f"expected one formula step, found {len(step_at)}"
        cls.step = block_after(cls.lines, step_at[0])

        run_at = [i for i, line in enumerate(cls.step) if line.strip().startswith("run:")]
        assert len(run_at) == 1, f"expected one run body, found {len(run_at)}"
        cls.code = "\n".join(
            line for line in block_after(cls.step, run_at[0]) if not line.strip().startswith("#")
        )

        job_at = [i for i, line in enumerate(cls.lines) if line.strip() == JOB]
        assert len(job_at) == 1
        job = block_after(cls.lines, job_at[0])
        perms_at = [i for i, line in enumerate(job) if line.strip() == "permissions:"]
        assert len(perms_at) == 1
        cls.permissions = [line.strip() for line in block_after(job, perms_at[0]) if line.strip()]

    def test_the_scan_found_the_real_step(self):
        """Vacuity: an empty scan would satisfy every `assertNotIn` below.

        The hand-read is cheap and the file is free to be restructured, so the
        thing worth refusing is a test that passes because it read nothing.
        """
        self.assertIn(MARKER, self.code)
        self.assertGreater(len(self.code.splitlines()), 10)
        self.assertEqual(
            [line for line in self.code.splitlines() if line.strip().startswith("#")],
            [],
            "a comment line survived the stripper",
        )
        self.assertTrue(self.permissions, "the job's permissions block was not found")

    def test_the_digest_is_written_not_merely_reported(self):
        """The sibling repo's failure: it knew the digest and only printed it.

        Asserted on a `sed` that edits the *sha256* specifically. A bare
        `assertIn("sed -i")` passes with the digest edit deleted, because the
        step also rewrites the url and the test line — which is exactly the
        half that stays correct on its own when the digest goes stale.
        """
        edits = [line for line in self.code.splitlines() if "sed -i" in line and "sha256" in line]
        self.assertTrue(edits, "nothing in the step writes the sha256")

    def test_a_pull_request_is_opened(self):
        self.assertIn("gh pr create", self.code)

    def test_nothing_is_pushed_straight_to_main(self):
        """Protection refuses it, and the refusal is trivially easy to swallow.

        This is the assertion that would have caught 1.15.0 before it shipped.
        """
        self.assertNotIn("HEAD:main", self.code)
        self.assertNotIn("push origin main", self.code)

    def test_the_job_may_actually_open_one(self):
        """`gh pr create` without the permission fails at the API, after the tag.

        Cheap to assert and invisible in review: the permission block sits far
        above the step that needs it, in a part of the file nobody edits when
        adding a step.
        """
        self.assertTrue(
            any(p.startswith("pull-requests: write") for p in self.permissions),
            f"the job cannot open a pull request: {self.permissions}",
        )
        self.assertTrue(any(p.startswith("contents: write") for p in self.permissions))

    def test_the_step_is_given_a_token(self):
        """`gh` without a token fails on the first API call, after the release."""
        env_at = [i for i, line in enumerate(self.step) if line.strip() == "env:"]
        self.assertEqual(len(env_at), 1, "the step declares no env block")
        env = [line.strip() for line in block_after(self.step, env_at[0]) if line.strip()]
        self.assertTrue(any(e.startswith("GH_TOKEN:") for e in env), f"no GH_TOKEN in {env}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
