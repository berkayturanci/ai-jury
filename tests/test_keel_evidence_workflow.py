"""The review-evidence chain must stay wired, and wired the way it works.

#602's record: of the 16 PRs merged into `main` between 2026-08-19 and
2026-08-24, **none** carried a review verdict. `tier3_globs` already listed the
modules where a mistake is most costly — adapters, privilege, redaction,
injection, config — and nothing read that declaration at merge time.

The cause was not the `gates: [build, lint]` line the issue pointed at; keel's
own config declares the same two. There was simply no workflow running the gate.

Each assertion below is a way the workflow can keep existing while doing
nothing. That is the failure mode worth testing: a gate that is present and
inert looks exactly like a gate that works.
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "keel-ship.yml"

#: The keel release this repo's merge contract is pinned to. Bumping it is a
#: reviewed diff, which is the point of pinning.
PINNED_KEEL = 'pip install "keel-workflow==1.19.0"'


def code_lines() -> list[str]:
    """The workflow's lines with whole-line comments removed.

    Every flag and trigger this file asserts on is also *described* in a comment
    a few lines above it, so a plain substring search over the raw text passes
    whether or not the thing is configured. Both of the first two mutations run
    against this file survived exactly that way. There is no YAML parser here —
    the package ships with no runtime dependencies — so the assertions are
    line-anchored instead.
    """
    return [
        line for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


class TheCommentStrippingWorks(unittest.TestCase):
    """The assertions below are only meaningful if this actually strips."""

    def test_comments_are_removed_and_code_is_not(self):
        stripped = code_lines()
        self.assertTrue(
            any(line.lstrip().startswith("#")
                for line in WORKFLOW.read_text(encoding="utf-8").splitlines()),
            "the workflow has no comment lines, so this guard proves nothing",
        )
        self.assertFalse([line for line in stripped if line.lstrip().startswith("#")])
        self.assertGreater(len(stripped), 50)


class TheEvidenceWorkflowIsPresent(unittest.TestCase):
    def setUp(self):
        self.assertTrue(WORKFLOW.exists(), f"{WORKFLOW.name} must exist (#602)")
        self.body = WORKFLOW.read_text(encoding="utf-8")
        self.code = code_lines()

    def _verify_argv(self) -> str:
        """The line that builds `keel evidence-verify`'s arguments.

        Both jobs assemble an `ARGS=` array; only the gate's names a PR. Selected
        on `--pr` rather than on a flag under test, so the selector cannot make
        the assertion circular — and if `--pr` ever goes, this fails loudly
        rather than silently picking the other job's line.
        """
        matches = [
            line for line in self.code
            if "ARGS=(.keel/project.yaml" in line and '--pr "$PR"' in line
        ]
        self.assertEqual(len(matches), 1, f"expected one gate ARGS line, saw {matches}")
        return matches[0]

    def test_the_gate_actually_runs(self):
        self.assertTrue(
            any(line.strip().startswith("keel evidence-verify") for line in self.code),
            "no uncommented line invokes the gate",
        )

    def test_the_gate_cannot_pass_while_unarmed(self):
        """`--require-armed`: a gate that evaluated nothing must not report success.

        Without it a green check is indistinguishable from one that never ran,
        which is the shape of the whole finding. Asserted on the argument line
        itself: the flag is named in a comment directly above it, so a search
        over the file text passes with the flag deleted.
        """
        self.assertIn("--require-armed", self._verify_argv())

    def test_the_gate_asks_for_the_phase_it_can_satisfy(self):
        """Closure comments are posted *after* the merge this gate authorizes."""
        self.assertIn("--phase pre-merge", self._verify_argv())

    def test_the_paid_jury_verdict_is_not_required(self):
        """#602's decision: start without it.

        keel auto-enables a *gating* jury verdict at tier-3, and this repo's
        jury runs against real vendor APIs. A per-PR paid run is not the cost
        posture, and a gate nobody can afford to satisfy is one that gets
        waived — which is worse than not having it.
        """
        self.assertIn("--no-jury", self._verify_argv())

    def test_dropping_the_jury_did_not_drop_the_reviewers(self):
        """The narrow-disarm check: `--no-jury` must not become `--reviewers 1`.

        Tier-3 still requires three distinct reviewer verdicts. Nothing may
        override the count the tier derives, or the gate quietly becomes a
        formality on exactly the files `tier3_globs` marks as riskiest.
        """
        argv = self._verify_argv()
        self.assertNotIn("--reviewers", argv)
        self.assertNotIn("--jury-advisory", argv)
        self.assertNotIn("--deferral all", argv)
        # A blanket deferral is the other way to hollow this out. The workflow
        # accepts one only as an explicit `workflow_dispatch` input, never
        # baked into the argument line.
        self.assertNotIn("--dry-run", argv)

    def test_a_posted_verdict_retriggers_the_gate(self):
        """The event the gate waits for must be an event it listens to.

        A verdict arrives as an issue comment. Without this trigger the only
        other retrigger is a new commit, which changes the head SHA and
        invalidates the very verdicts that would let the check pass — so the
        check would stay incomplete forever and nothing could ever merge.

        Matched as a whole trigger key. `assertIn("issue_comment:")` also
        matches `_disabled_issue_comment:`, which is how the mutation that
        renamed this key went undetected.
        """
        self.assertIn("issue_comment:", [line.strip() for line in self.code])

    def test_the_gating_check_is_not_named_after_the_job(self):
        """Two same-named checks on one commit are ambiguous to branch protection.

        The job's own check reports only that the job ran; the gating verdict is
        the check-run published by the step. They must not collide.
        """
        self.assertIn("name: keel evidence (verify)", self.body)
        self.assertIn("CHECK_NAME: keel evidence (required)", self.body)
        self.assertNotIn("name: keel evidence (required)", self.body)

    def test_the_check_run_is_upserted_rather_than_stacked(self):
        """The check-runs API has no upsert on (name, head_sha)."""
        self.assertIn("-X PATCH", self.body)
        self.assertIn("--method GET", self.body)


class KeelIsPinnedAndIsTheRightPackage(unittest.TestCase):
    def setUp(self):
        self.body = WORKFLOW.read_text(encoding="utf-8")

    def test_keel_is_installed_pinned(self):
        """A floated install lets a keel release change this repo's merge contract."""
        self.assertIn(PINNED_KEEL, self.body)

    def test_every_install_of_keel_is_the_pinned_one(self):
        """Two jobs install keel; a rule about one of them is not a rule.

        Counted rather than spot-checked, and the count is asserted, so adding a
        third job without pinning fails here instead of running unpinned.
        """
        installs = [
            line.strip()
            for line in self.body.splitlines()
            if "pip install" in line and "--upgrade pip" in line
        ]
        self.assertEqual(len(installs), 2, f"expected two keel installs, saw {installs}")
        for line in installs:
            with self.subTest(line=line):
                self.assertIn(PINNED_KEEL, line)

    def test_the_package_is_keel_workflow_not_keel(self):
        """`keel` on PyPI is an unrelated project at 0.1.

        Installing it would give this job someone else's code, or no `keel`
        command at all — and the gate would fail for a reason that looks like a
        keel bug. Asserted as a rule over every install line, not as the absence
        of one string, because `keel-workflow` legitimately contains `keel`.
        """
        for line in self.body.splitlines():
            if "pip install" not in line or "keel" not in line:
                continue
            with self.subTest(line=line.strip()):
                self.assertNotIn('install "keel==', line)
                self.assertNotIn("install keel==", line)
                self.assertNotIn("install keel ", line)


class TheTierDeclarationIsConsumed(unittest.TestCase):
    """`tier3_globs` was already there; the point is that something reads it."""

    def setUp(self):
        self.config = (REPO_ROOT / ".keel" / "project.yaml").read_text(encoding="utf-8")

    def test_the_high_risk_modules_are_still_declared(self):
        for module in (
            "src/ai_jury/adapters.py",
            "src/ai_jury/privilege.py",
            "src/ai_jury/redaction.py",
            "src/ai_jury/injection.py",
            "src/ai_jury/config.py",
        ):
            with self.subTest(module=module):
                self.assertIn(module, self.config)

    def test_the_config_says_where_the_contract_lives(self):
        """An unexplained `gates: [build, lint]` is what produced #602.

        The next reader must not have to re-derive that the evidence chain is
        enforced by a workflow rather than by this list.
        """
        self.assertIn("keel-ship.yml", self.config)
        self.assertIn("branch protection", self.config)
