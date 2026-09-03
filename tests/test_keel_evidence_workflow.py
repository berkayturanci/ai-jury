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

import re
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
        line
        for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


class TheCommentStrippingWorks(unittest.TestCase):
    """The assertions below are only meaningful if this actually strips."""

    def test_comments_are_removed_and_code_is_not(self):
        stripped = code_lines()
        self.assertTrue(
            any(
                line.lstrip().startswith("#")
                for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
            ),
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
            line
            for line in self.code
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


#: `${{ … }}` interpolations, one at a time.
_EXPRESSION = re.compile(r"\$\{\{(.+?)\}\}")


def _lookup(context: dict, path: str) -> object:
    """Resolve a dotted Actions context path; a missing key reads as empty."""
    node: object = context
    for part in path.split("."):
        if not isinstance(node, dict):
            return ""
        node = node.get(part, "")
    return node


def _render(expression: str, context: dict) -> str:
    """Render the slice of the Actions expression language the group uses.

    ``a || b || …`` over dotted context paths and quoted literals — enough to
    compute the group *GitHub* would compute for a given event. There is no YAML
    parser and no expression evaluator available here (the package ships with no
    runtime dependencies), and asserting on the expression's *text* instead
    would pass for a group that merely mentions ``github.event_name`` and still
    renders one string for every event — which is exactly the bug.
    """

    def one(match: re.Match[str]) -> str:
        for raw in match.group(1).split("||"):
            operand = raw.strip()
            value = operand[1:-1] if operand.startswith("'") else _lookup(context, operand)
            if value not in ("", None, False):
                return str(value)
        return ""

    return _EXPRESSION.sub(one, expression)


def _concurrency() -> dict[str, str]:
    """The `concurrency:` block's keys, read off the uncommented lines.

    Line-anchored rather than parsed: every value asserted on below is also
    *described* in a comment directly above it, so a search over the raw text
    passes whether or not the block is configured that way.
    """
    lines = code_lines()
    start = lines.index("concurrency:")
    block: dict[str, str] = {}
    for line in lines[start + 1 :]:
        if not line.startswith("  "):
            break
        key, _, value = line.strip().partition(":")
        block[key] = value.strip()
    return block


def _group(context: dict) -> str:
    return _render(_concurrency()["group"], context)


def _event(name: str, ref: str = "refs/heads/main", **payload: object) -> dict:
    return {"github": {"event_name": name, "event": payload, "ref": ref}}


class TheRendererRendersSomething(unittest.TestCase):
    """The group assertions are only meaningful if this evaluates the expression."""

    def test_a_group_expression_is_found_and_interpolated(self):
        group = _concurrency()["group"]
        self.assertIn("${{", group, "no expression in the concurrency group")
        rendered = _group(_event("pull_request", pull_request={"number": 7}))
        self.assertNotIn("${{", rendered, "the renderer left the expression untouched")
        self.assertIn("7", rendered)

    def test_an_unset_operand_falls_through_to_the_next(self):
        self.assertEqual(_render("${{ a.b || 'x' }}", {"a": {"b": ""}}), "x")


class OneEventCannotCancelAnotherEventsRun(unittest.TestCase):
    """A verdict comment used to cancel the assessment run, and cancelled
    check-runs cannot be deleted.

    One `concurrency` group for the whole workflow keyed on the pull request
    number alone put the `issue_comment` run and the `pull_request` run in the
    same group; `keel review --live` posts its verdicts seconds after the push,
    so the comment run cancelled the assessment run and left
    `keel ship (assessment)` / `keel evidence (verify)` `cancelled` on the head.
    GitHub then reports the pull request as UNSTABLE and `keel merge` refuses on
    "CI failing" with every required check green — every ai-jury PR merged on
    2026-09-03 needed a hand-run `gh run rerun` (#679, port of
    berkayturanci/keel#1038).

    These assert on the group GitHub would *render*, not on the expression's
    text: a group that reads `github.event_name` and still renders one string
    per pull request would satisfy any `assertIn` and reproduce the bug.
    """

    PR = 679
    PUSH = _event("pull_request", ref="refs/pull/679/merge", pull_request={"number": PR})
    VERDICT = _event("issue_comment", issue={"number": PR, "pull_request": {"url": "…"}})
    DISPATCH = _event("workflow_dispatch", inputs={"pr": str(PR)})

    def test_no_two_events_share_a_group_for_one_pull_request(self):
        groups = {
            "pull_request": _group(self.PUSH),
            "issue_comment": _group(self.VERDICT),
            "workflow_dispatch": _group(self.DISPATCH),
        }
        self.assertEqual(
            len(set(groups.values())),
            len(groups),
            f"two events share a concurrency group, so one cancels the other: {groups}",
        )

    def test_every_event_still_scopes_its_group_to_the_pull_request(self):
        """Per-event is not enough on its own — a group that dropped the number
        would serialize every open pull request against every other."""
        for name, context in (
            ("pull_request", self.PUSH),
            ("issue_comment", self.VERDICT),
            ("workflow_dispatch", self.DISPATCH),
        ):
            with self.subTest(event=name):
                self.assertIn(str(self.PR), _group(context))

    def test_two_pull_requests_never_share_a_group(self):
        other = _event("pull_request", ref="refs/pull/9/merge", pull_request={"number": 9})
        self.assertNotEqual(_group(self.PUSH), _group(other))

    def test_a_superseded_run_of_the_same_event_is_still_cancelled(self):
        """Cancelling within one event is the point of the group and stays on.

        The cancelled run is never the last word on a head that still matters: a
        superseded `pull_request` run belongs to a superseded head SHA, and an
        `issue_comment` run always runs from the default branch, so its job
        check-runs land there rather than on the pull request's head.
        """
        again = _event("pull_request", ref="refs/pull/679/merge", pull_request={"number": self.PR})
        self.assertEqual(_group(self.PUSH), _group(again))
        self.assertEqual(_concurrency()["cancel-in-progress"], "true")

    def test_the_group_falls_back_to_the_ref_without_a_number(self):
        """A dispatch from a branch with no `pr` input still gets a group, and
        still one of its own — the fallback chain is per-event too."""
        bare = _event("workflow_dispatch", ref="refs/heads/main", inputs={"pr": ""})
        self.assertTrue(_group(bare).endswith("refs/heads/main"))
        self.assertNotEqual(_group(bare), _group(_event("pull_request", ref="refs/heads/main")))


class AStaleEvaluationCannotOverwriteANewerVerdict(unittest.TestCase):
    """Splitting the groups lets two runs publish the same check-run.

    That race is what the shared group prevented, at the cost of the cancelled
    check-runs above. The replacement is in `publish_check`: each run stamps when
    it read the pull request into `external_id`, and refuses to overwrite a
    check-run stamped later. Without it a `pull_request` run that started before
    a verdict was posted can finish after the comment run and put its "waiting"
    answer back over "verified" — blocking the merge with nothing left to
    retrigger the workflow.
    """

    def setUp(self):
        self.body = WORKFLOW.read_text(encoding="utf-8")
        publisher = re.search(r"publish_check\(\)\s*\{(.*?)\n {10}\}", self.body, re.DOTALL)
        self.assertIsNotNone(publisher, "the publisher is gone")
        self.publisher = publisher.group(1)

    def test_the_check_records_when_it_was_evaluated(self):
        self.assertIn("external_id=${EVALUATED_AT}", self.publisher)

    def test_a_declined_write_is_not_a_failed_job(self):
        """Returning 0 from both outcomes reintroduces the defect via the fix.

        `publish_check` returning 0 whether it published or declined, with the
        caller replaying this run's RC either way, means an older
        `pull_request` run that correctly declined to overwrite a newer success
        still exits 1 on its own stale violation code. Actions marks that job
        FAILURE on the *live* head, keel's rollup scores FAILURE exactly like
        CANCELLED, and `keel merge` refuses on "CI failing" again — the same
        defect this change is about. Reachable whenever the answer differs
        between two reads, which subscribing to comment `edited` guarantees.
        """
        self.assertIn("return 3", self.publisher, "the declined path is not distinguishable")
        self.assertRegex(
            self.body,
            r'if \[ "\$PUBLISHED" -eq 3 \]; then\n\s*exit 0\n\s*fi',
            "a declined write does not exit 0 unconditionally",
        )
        declined = self.body[self.body.index('if [ "$PUBLISHED" -eq 3 ]') :]
        self.assertNotIn("$RC", declined[: declined.index('if [ "$PUBLISHED" -eq 0 ]')])

    def test_the_violation_exit_is_scoped_to_the_run_that_published(self):
        """This run's RC may only fail the job when this run's verdict is on the
        check — otherwise a stale violation is replayed over a newer success."""
        published = self.body[self.body.index('if [ "$PUBLISHED" -eq 0 ]') :]
        self.assertRegex(
            published[: published.index("\n\n")],
            r'\[ "\$RC" -ne 0 \] && \[ "\$RC" -ne 2 \]',
            "the violation exit is not scoped to the branch that published",
        )

    def test_the_stamp_orders_reads_that_land_in_the_same_second(self):
        """Whole seconds plus a strict `-gt` means neither of two runs declines.

        Two runs racing over one head are seconds apart at most, so the same
        second is the common case, not the corner.
        """
        self.assertIn("date -u +%s%N", self.body)

    def test_the_stamp_is_taken_before_the_pull_request_is_read(self):
        """A completion time would rank the stale answer first.

        A run that started before the verdict existed can still *finish* after
        the run that saw it, so ordering by when each run finished picks exactly
        the wrong one.
        """
        self.assertRegex(self.body, r"EVALUATED_AT=\$\(date -u \+%s%N\)")
        self.assertLess(
            self.body.index("EVALUATED_AT=$(date"),
            self.body.index('keel evidence-verify "${ARGS[@]}"'),
            "the stamp is taken after the read, so it orders runs by completion",
        )

    def test_a_newer_stamp_stops_the_write_before_it_happens(self):
        self.assertRegex(
            self.publisher,
            r'\[\s+"\$existing_stamp"\s+-gt\s+"\$EVALUATED_AT"\s+\]',
            "nothing compares this run's evaluation time with the published one",
        )
        guard = self.publisher.index("-gt")
        self.assertLess(guard, self.publisher.index("-X PATCH"), "the guard runs after the update")
        self.assertLess(guard, self.publisher.index("-X POST"), "the guard runs after the create")
        self.assertRegex(
            self.publisher[guard : self.publisher.index("-X PATCH")],
            r"return 3",
            "the guard does not actually skip the write",
        )
        self.assertIn("::notice", self.publisher, "a skipped write is invisible in the log")

    def test_an_unstamped_or_malformed_check_is_still_overwritten(self):
        """A check-run published before this guard existed carries no stamp.

        Refusing to write then would freeze the gate on whatever it last said,
        so both the empty and the non-numeric case fall through to the write —
        the safe direction, since this run has read the newer state.
        """
        self.assertIn('[ -n "$existing_stamp" ]', self.publisher)
        self.assertIn("${existing_stamp//[0-9]/}", self.publisher)

    def test_the_lookup_reads_the_stamp_as_well_as_the_id(self):
        """`select(.)` is load-bearing in the two-field shape: `max_by` over an
        empty list is null, which renders as a single space — not empty, and
        parsed as an existing run with no id, so every run would POST a second
        check under the gating name.
        """
        self.assertIn("map(select(.id))", self.publisher)
        self.assertIn("| select(.) |", self.publisher, "an absent run renders as a space")
        self.assertIn("\\(.id)", self.publisher)
        self.assertIn(".external_id", self.publisher)
        self.assertIn("read -r existing_id existing_stamp", self.publisher)

    def test_the_stamp_is_read_off_the_newest_run_not_an_arbitrary_one(self):
        """The list endpoint's order is not documented as newest-first.

        A head that carries more than one check-run under the gating name — the
        stacking the upsert exists to prevent, which a pre-guard workflow could
        have left behind — would otherwise have its stamp read off whichever row
        came back first, and an old stamp there disarms the staleness guard
        silently.
        """
        self.assertIn("max_by(.external_id | tonumber? // 0)", self.publisher)
        # On the invocation, not the block: the comment above it names
        # `.check_runs[0]` to explain what it replaced, so a block-wide
        # assertion passes with the lookup reverted.
        lookup = next(line for line in self.publisher.splitlines() if "--jq" in line)
        self.assertNotIn(".check_runs[0]", lookup, "the lookup is back on an arbitrary row")
