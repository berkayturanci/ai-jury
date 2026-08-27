"""The release must repair its own formula, not leave a note asking someone to.

The Homebrew formula names a URL and a sha256 for the sdist of the release being
cut. Neither is knowable until the tag exists, so at the moment `publish.yml`
runs, the file in the repository is still describing the *previous* release.

Both halves of that gap have now failed in production, three days apart:

* This repo *did* try to commit it, with `git push origin HEAD:main || true`.
  Branch protection was added the same morning (#620), so the push was refused
  and the `|| true` swallowed the refusal. 1.15.0 shipped a stale digest and the
  tap — which pulls from `main` on a schedule — refused every sync for a day,
  one failure email per hour, with nothing in the release log to explain it.
* The sibling repo emitted a `::notice::` carrying the correct digest and left
  the edit to a human. For its 1.19.1 nobody did it, and its tap failed the same
  way. A notice nobody is required to act on is not a safeguard.

The surviving design is a pull request opened by the workflow itself: it is the
only write to `main` that protection permits, and unlike a notice it exists as a
thing on a list rather than a line in a log nobody reads after a green release.

These tests pin that shape. They assert over the *code* in the step, with
comment lines removed first — the prose in this workflow discusses the direct
push it no longer performs, and a grep would happily match that discussion and
report the old behaviour as still guarded.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "publish.yml"

#: The job holding the formula step, and what makes that step recognisable.
#:
#: Selected by what the step *does* — it is the one that edits the file — rather
#: than by its name, which is free to change.
JOB = "build-n-publish"
MARKER = "Formula/ai-jury.rb"
EDITS = "sed -i"


def code_of(run: str) -> str:
    """`run` with whole-line `#` comments dropped.

    Deliberately line-based rather than a strip to end-of-line: a `#` can appear
    inside a quoted string, and mangling those would make the assertions below
    depend on quoting rather than on behaviour.
    """
    return "\n".join(
        line for line in run.splitlines() if not line.strip().startswith("#")
    )


class TheFormulaStepOpensAPullRequest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        cls.job = workflow["jobs"][JOB]
        steps = [
            s
            for s in cls.job["steps"]
            if MARKER in (s.get("run") or "") and EDITS in s["run"]
        ]
        assert len(steps) == 1, f"expected one formula step, found {len(steps)}"
        cls.step = steps[0]
        cls.code = code_of(cls.step["run"])

    def test_the_comment_stripper_leaves_the_script_behind(self):
        """Vacuity: a stripper that returned "" would satisfy every `assertNotIn`."""
        self.assertIn(MARKER, self.code)
        self.assertGreater(len(self.code.splitlines()), 5)
        self.assertEqual(
            [line for line in self.code.splitlines() if line.strip().startswith("#")],
            [],
            "a comment line survived the stripper",
        )

    def test_the_digest_is_written_not_merely_reported(self):
        """The sibling repo's failure: it knew the digest and only printed it."""
        self.assertIn("sed -i", self.code, "the step does not edit the formula at all")

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

        Cheap to assert and invisible in review: the permission block sits ~40
        lines above the step that needs it.
        """
        self.assertEqual(self.job["permissions"].get("pull-requests"), "write")
        self.assertEqual(self.job["permissions"].get("contents"), "write")

    def test_the_step_is_given_a_token(self):
        self.assertIn("GH_TOKEN", self.step.get("env") or {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
