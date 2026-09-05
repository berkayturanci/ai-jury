"""What a ballot claims about itself has to be true of the run (#709, #710).

Two claims, both added by #700 to make a ballot answer *was this checked, and by
what*, and both false in exactly the configurations the features around them
exist for. The first real jury run after #705 and #700 merged — three shipped
CLIs, ``--rounds 1``, on the diff of #707 — raised each of them against itself.

* **#709 — the model a ballot names was not always the model the run sent.**
  ``ballots.requested_model`` keyed the effort→model-id mapping on
  ``spec.vendor``; every path that actually invokes a seat keys it on the
  *adapter*, because how effort is expressed is a property of the protocol the
  seat is invoked through. Before #705 those were one string. After it, a seat
  configured ``vendor = google, adapter = cli`` was invoked with ``gemini-3-pro``
  and balloted ``gemini-3-pro-high`` — under ``model_source: "requested"``, whose
  whole claim is that the id is the one actually sent.

* **#710 — any backticked ``Checked:`` token was a substantive scope.**
  ``describe_scope`` backticked whatever the reviewer wrote and
  ``scope_is_substantive`` accepted any backticked text, so ``Checked: nothing``
  was rendered as an anchor and the ballot counted as a review. That is the shape
  of naming something with none of the substance, and it is satisfiable by an
  agent that read nothing — #700's own failure one layer up.

Round 2 of each, from review of #711, is at the foot of this file. Both fixes
overstated what they knew: the ``Checked:`` line was cut into tokens on
whitespace, so a changed file whose *name* contains a space was two tokens that
name nothing and the ballot abstained under ``not_in_change``; and a ``model``
id this tool *derived*, for a record no invocation stamped, still went out under
``model_source: requested``.

Every test here is offline: no CLI is spawned, no network is touched. The #709
tests compare a ballot against the **argv the adapter actually built**, which is
the only comparison that cannot be satisfied by two copies of the same mistake.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import tempfile
import unittest
import unittest.mock as mock
from dataclasses import replace
from pathlib import Path

from ai_jury import cli, panel
from ai_jury.adapters import AgentResult, MockAdapter, effort_args, make_adapter
from ai_jury.ballots import (
    MODEL_RECOMPUTED,
    MODEL_REQUESTED,
    ballot_seats,
    describe_scope,
    resolve_stated_scope,
    reviewer_ballots,
)
from ai_jury.config import _from_dict, spec_adapter
from ai_jury.largediff import change_index
from ai_jury.orchestrator import JuryOutcome, RunBudget, _run_with_retry

#: The diff the panel is shown in the #710 tests. Two files, one of them nested,
#: so path resolution is exercised at more than one depth.
_DIFF = """diff --git a/src/ai_jury/ballots.py b/src/ai_jury/ballots.py
--- a/src/ai_jury/ballots.py
+++ b/src/ai_jury/ballots.py
@@ -1,3 +1,4 @@
 def describe_scope(result, findings):
-    return ""
+    stated = _stated_line(result)
+    return stated
diff --git a/notes.md b/notes.md
--- a/notes.md
+++ b/notes.md
@@ -1 +1 @@
-old
+new
"""

_CHANGED = change_index(_DIFF)

#: keel's ``review-verdict-insubstantial`` anchors, vendored as
#: ``tests/test_formats.py`` and ``tests/test_panel_arithmetic.py`` vendor them:
#: a path, a ``path:line``, a backticked symbol, a called identifier, or a
#: "checked …" clause. An abstention that matches one of these is an abstention
#: the consumer will read as a review, which is the defect wearing better prose.
_ANCHORS = (
    re.compile(r"[\w./-]+\.[A-Za-z0-9]{1,5}:\d+"),
    re.compile(r"[\w-]+/[\w./-]+\.[A-Za-z0-9]{1,5}\b"),
    re.compile(r"`[^`\n]{2,}`"),
    re.compile(r"\b\w+\.\w+\(\)"),
    re.compile(r"\bchecked\b[^.\n]{8,}", re.IGNORECASE),
)


def _model_in_argv(argv: list[str]) -> str:
    """The model id an adapter put on its command line, or ``""``."""
    for flag in ("--model", "-m"):
        if flag in argv:
            return argv[argv.index(flag) + 1]
    return ""


def _config(agents: list[dict], **jury):
    return _from_dict({"jury": {"rounds": 1, "verify": False, **jury}, "agent": agents})


def _outcome(results: list[AgentResult], *, changed=None, chair: str = "") -> JuryOutcome:
    return JuryOutcome(
        reviews=results,
        debate=[],
        synthesis=None,
        chair=chair or (results[0].agent if results else ""),
        changed=changed,
    )


def _reply(checked: str) -> str:
    return f"Checked: {checked}\n\nTested: nothing.\n\nNo concerns.\n"


class TheBallotNamesTheModelTheRunSent(unittest.TestCase):
    """#709: one source of truth for the id, and the argv is the witness.

    The fix records the id on the result at the moment the adapter is run
    (:meth:`ai_jury.adapters.Adapter.resolved_model`, stamped by
    ``_run_with_retry`` and by ``jury run-agent``) and the ballot reads it back.
    So these tests build the argv, run the seat through the real invocation
    helper, and compare the ballot to the string in that argv — never to a second
    computation of it, which is what allowed the two to drift.
    """

    #: Every shipped adapter key whose effort mapping rewrites the MODEL ID
    #: (rather than adding a request-body field or being ignored). Derived, not
    #: listed, so a vendor that grows a model-id mapping is covered the day it
    #: does and this test cannot quietly stop applying to it.
    def _model_mapping_adapters(self) -> list[str]:
        from ai_jury.adapters import _VENDOR_ADAPTERS

        return [
            key
            for key in sorted(_VENDOR_ADAPTERS)
            if effort_args(key, "high", "base-model").model not in (None, "base-model")
        ]

    def _ballot_for(self, spec_dict: dict, *, list_models=None) -> tuple[dict, list[str]]:
        """``(ballot, argv)`` for one seat, from one run through the real path.

        The argv is built by the same adapter instance the run uses, and the run
        goes through :func:`ai_jury.orchestrator._run_with_retry` — the helper
        that stamps the sent id onto the result — so nothing here re-derives the
        id that the assertion then compares.

        ``list_models`` is what the vendor's listing probe returns, and it is
        always stubbed: ``agy`` may genuinely be installed on the machine running
        these tests, and a test whose expected id depends on that machine's
        model catalogue is not a test. ``None`` means "no listing available",
        which is what the probe reports for every CLI that is not there.
        """
        config = _config([spec_dict])
        spec = config.agents[0]
        adapter = make_adapter(spec)
        canned = AgentResult(spec.name, spec.vendor, True, _reply("`x`"), 0.0)
        with (
            mock.patch.object(type(adapter), "run", lambda *_a, **_k: canned),
            mock.patch.object(type(adapter), "list_models", lambda _self: list_models),
        ):
            argv = adapter.build_argv("prompt")
            result = _run_with_retry(
                adapter, "prompt", "review", RunBudget(None, None), 0, lambda _m: None
            )
        ballots = reviewer_ballots(_outcome([result]), config)
        return ballots[0], argv

    def test_every_model_mapping_adapter_ballots_the_id_in_its_own_argv(self):
        """The acceptance criterion, for every adapter the mapping can reach.

        Fails on d08beb1 for `google`: the seat is invoked with `gemini-3-pro`
        (its adapter is `cli`, which has no effort mapping) and the ballot said
        `gemini-3-pro-high`, because `requested_model` asked `effort_args` about
        the *vendor*.
        """
        adapters = self._model_mapping_adapters()
        self.assertTrue(adapters, "no shipped adapter maps effort into the model id")
        for vendor in adapters:
            with self.subTest(vendor=vendor, adapter="cli"):
                spec_dict = {
                    "name": "seat",
                    "vendor": vendor,
                    "adapter": "cli",
                    "command": "some-cli",
                    "model": "gemini-3-pro",
                    "effort": "high",
                    # How such a seat is really configured: the generic `cli`
                    # adapter has no model flag of its own, so the operator puts
                    # the id on the command line. That id is what the run sends,
                    # and it is what the ballot has to name.
                    "extra_args": ["--model", "gemini-3-pro"],
                }
                self.assertNotEqual(spec_adapter(self._spec(spec_dict)), vendor)
                ballot, argv = self._ballot_for(spec_dict)
                self.assertEqual(_model_in_argv(argv), "gemini-3-pro")
                self.assertEqual(ballot["model"], _model_in_argv(argv))
                self.assertEqual(ballot["model_source"], "requested")

    @staticmethod
    def _spec(spec_dict: dict):
        return _config([spec_dict]).agents[0]

    def test_the_issues_differential_produces_one_string_in_both_columns(self):
        """The table in #709, executed. Both rows fail on d08beb1, opposite ways."""
        rows = (
            # (vendor, adapter, extra_args, the id the run sends)
            ("google", "cli", ["--model", "gemini-3-pro"], "gemini-3-pro"),
            ("cli", "google", [], "gemini-3-pro-high"),
        )
        for vendor, adapter, extra_args, sent in rows:
            with self.subTest(vendor=vendor, adapter=adapter):
                ballot, argv = self._ballot_for(
                    {
                        "name": "seat",
                        "vendor": vendor,
                        "adapter": adapter,
                        "command": "agy",
                        "model": "gemini-3-pro",
                        "effort": "high",
                        "extra_args": extra_args,
                    }
                )
                # One string in both columns of the table in #709. On d08beb1
                # the first row balloted `gemini-3-pro-high` for a run that sent
                # `gemini-3-pro`, and the second row got it exactly backwards.
                self.assertEqual(_model_in_argv(argv), sent)
                self.assertEqual(ballot["model"], sent)

    def test_the_id_sent_wins_when_the_live_listing_forced_a_fallback(self):
        """The `known_models` half of #709, which no pure recomputation can see.

        `agy` expresses effort as a model-id suffix and checks the mapped id
        against `agy models`; an id the CLI does not offer falls back to the
        configured one. The ballot used to keep the suffix, so it named a model
        that had been deliberately *not* sent. Fails on d08beb1.
        """
        ballot, argv = self._ballot_for(
            {
                "name": "seat",
                "vendor": "google",
                "adapter": "google",
                "command": "agy",
                "model": "gemini-3-pro",
                "effort": "high",
            },
            list_models=["gemini-3-pro"],
        )
        self.assertEqual(_model_in_argv(argv), "gemini-3-pro")
        self.assertEqual(ballot["model"], "gemini-3-pro")

    def test_a_seat_with_no_model_pinned_still_reports_the_cli_default(self):
        """The #700 behaviour the fix must not disturb."""
        ballot, argv = self._ballot_for(
            {"name": "seat", "vendor": "anthropic", "command": "claude"}
        )
        self.assertEqual(_model_in_argv(argv), "")
        self.assertEqual(ballot["model_source"], "cli_default")
        self.assertIn("claude", ballot["model"])


class AStatedScopeHasToNameSomethingInTheChange(unittest.TestCase):
    """#710: a `Checked:` token counts only when it resolves against the diff."""

    def _ballot(self, checked: str, *, changed=_CHANGED) -> dict:
        config = _config([{"name": "seat", "vendor": "anthropic", "command": "claude"}])
        result = AgentResult("seat", "anthropic", True, _reply(checked), 0.0)
        return reviewer_ballots(_outcome([result], changed=changed), config)[0]

    def test_checked_nothing_is_an_abstention_that_names_the_token(self):
        """The acceptance criterion, and the reported defect. Fails on d08beb1,
        where this ballot came back `scope_substantive: true` and counted as a
        review because `nothing` had been wrapped in backticks."""
        ballot = self._ballot("nothing")
        self.assertFalse(ballot["scope_substantive"])
        self.assertFalse(ballot["counts_as_review"])
        self.assertEqual(ballot["verdict"], panel.ABSTAIN)
        self.assertIn("nothing", ballot["scope"])
        self.assertIn("names no path, line or symbol in this change", ballot["scope"])
        self.assertEqual(ballot["abstention_cause"], panel.NAMED_NOTHING)

    def test_the_other_shapes_of_the_same_non_answer(self):
        for value in ("everything", "the diff", "all of it"):
            with self.subTest(checked=value):
                ballot = self._ballot(value)
                self.assertFalse(ballot["counts_as_review"])
                self.assertEqual(ballot["abstention_cause"], panel.NAMED_NOTHING)

    def test_a_path_in_the_diff_is_a_review(self):
        ballot = self._ballot("src/ai_jury/ballots.py")
        self.assertTrue(ballot["scope_substantive"])
        self.assertTrue(ballot["counts_as_review"])
        self.assertEqual(ballot["abstention_cause"], "")
        self.assertIn("`src/ai_jury/ballots.py`", ballot["scope"])

    def test_a_path_line_and_a_symbol_in_the_diff_are_reviews_too(self):
        for value in ("src/ai_jury/ballots.py:3", "notes.md", "describe_scope()", "_stated_line"):
            with self.subTest(checked=value):
                self.assertTrue(self._ballot(value)["counts_as_review"], value)

    def test_a_path_that_is_not_in_the_change_abstains_under_its_own_cause(self):
        """Named-nothing and named-what-does-not-exist are different failures
        with different fixes, so they are different buckets."""
        ballot = self._ballot("src/made/up.py")
        self.assertFalse(ballot["counts_as_review"])
        self.assertEqual(ballot["abstention_cause"], panel.NOT_IN_CHANGE)
        self.assertIn("no such path or symbol is in this change", ballot["scope"])
        self.assertIn("made", ballot["scope"])

    def test_that_abstention_is_still_anchorless_to_the_consumer(self):
        """The abstention names the token the reviewer invented — and must not
        thereby become the anchor that token would have been. A consumer applying
        the same substance rule has to reach the same conclusion."""
        scope = self._ballot("src/made/up.py")["scope"]
        for pattern in _ANCHORS:
            self.assertIsNone(pattern.search(scope), f"{pattern.pattern} matched: {scope}")

    def test_a_mixed_line_is_carried_by_the_token_that_resolves(self):
        """The decision #710 asks for, stated in a test: the real path carries
        the ballot, and the invented one is listed as not in the change."""
        ballot = self._ballot("src/ai_jury/ballots.py, src/made/up.py")
        self.assertTrue(ballot["counts_as_review"])
        self.assertIn("`src/ai_jury/ballots.py`", ballot["scope"])
        self.assertIn("The same line also named", ballot["scope"])
        self.assertIn("not in this change", ballot["scope"])
        # ...and the unresolved half is de-anchored even here, so the *only*
        # anchor in this scope is the file the reviewer really read.
        self.assertNotIn("src/made/up.py", ballot["scope"])

    def test_ordinary_prose_beside_a_real_path_is_not_reported_as_missing(self):
        """Connective words claim to name nothing, so they are neither anchors
        nor broken anchors — listing them would drown the real finding."""
        ballot = self._ballot("src/ai_jury/ballots.py lines 1-4 and the tests")
        self.assertTrue(ballot["counts_as_review"])
        self.assertNotIn("The same line also named", ballot["scope"])

    def test_issue_mode_keeps_its_own_scope_exception(self):
        """``--issue`` supplies no change to resolve against: the panel is
        reading prose, and a reviewer naming a section of it named what it was
        asked to name. Applying the diff rule there would abstain over a clean
        triage that did its job."""
        config = _config([{"name": "seat", "vendor": "anthropic", "command": "claude"}])
        result = AgentResult("seat", "anthropic", True, _reply("the acceptance criteria"), 0.0)
        outcome = _outcome([result], changed=_CHANGED)
        self.assertTrue(reviewer_ballots(outcome, config, mode="issue")[0]["scope_substantive"])
        self.assertFalse(reviewer_ballots(outcome, config, mode="code")[0]["scope_substantive"])

    def test_resolution_is_skipped_when_the_outcome_carries_no_change(self):
        """Not-verifiable-here is not does-not-exist: a hand-built outcome or a
        caller that never had a diff keeps the pre-#710 structural rule."""
        ballot = self._ballot("nothing", changed=None)
        self.assertTrue(ballot["scope_substantive"])
        self.assertTrue(ballot["counts_as_review"])

    def test_the_buckets_still_sum_to_the_ballots(self):
        """The invariant the new cause has to keep: every seat in exactly one."""
        config = _config(
            [
                {"name": "a", "vendor": "anthropic", "command": "claude"},
                {"name": "b", "vendor": "openai", "command": "codex"},
                {"name": "c", "vendor": "google", "command": "agy"},
            ]
        )
        results = [
            AgentResult("a", "anthropic", True, _reply("src/ai_jury/ballots.py"), 0.0),
            AgentResult("b", "openai", True, _reply("nothing"), 0.0),
            AgentResult("c", "google", True, _reply("src/made/up.py"), 0.0),
        ]
        ballots = reviewer_ballots(_outcome(results, changed=_CHANGED, chair="a"), config)
        buckets = panel.abstention_buckets(ballots)
        self.assertEqual(buckets[panel.NAMED_NOTHING], 1)
        self.assertEqual(buckets[panel.NOT_IN_CHANGE], 1)
        self.assertEqual(
            panel.review_count(ballots) + sum(buckets.values()),
            len(ballot_seats(results)),
        )


class TheResolutionRuleItself(unittest.TestCase):
    """The pure half, exercised directly: what resolves and what does not."""

    def test_a_bare_english_word_is_never_a_symbol(self):
        """Even when the diff's own prose contains it. `nothing` appearing in a
        comment must not make `Checked: nothing` a review — the token has to look
        like an identifier, or be called somewhere in the change."""
        changed = change_index(
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@\n+# nothing to see here\n"
        )
        self.assertEqual(resolve_stated_scope("nothing", changed), ([], []))

    def test_a_called_identifier_resolves_even_when_it_is_one_lowercase_word(self):
        changed = change_index(
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@\n+    run(1)\n"
        )
        self.assertEqual(resolve_stated_scope("run", changed)[0], ["run"])

    def test_a_path_resolves_from_either_root(self):
        """Which root a path is quoted against is a property of how the diff was
        produced, not of whether the reviewer read the file."""
        self.assertEqual(resolve_stated_scope("ballots.py", _CHANGED)[0], ["ballots.py"])
        self.assertEqual(
            resolve_stated_scope("ai_jury/ballots.py", _CHANGED)[0], ["ai_jury/ballots.py"]
        )
        self.assertEqual(resolve_stated_scope("lots.py", _CHANGED), ([], ["lots.py"]))

    def test_a_repeated_or_punctuation_only_token_is_folded_away(self):
        """One token per distinct thing named, whatever the punctuation around
        it: `` `notes.md`, notes.md, — `` is one file, listed once."""
        resolved, unresolved = resolve_stated_scope("`notes.md`, notes.md, —", _CHANGED)
        self.assertEqual(resolved, ["notes.md"])
        self.assertEqual(unresolved, [])

    def test_an_empty_path_names_nothing(self):
        self.assertFalse(_CHANGED.has_path(""))
        self.assertFalse(_CHANGED.has_symbol(""))

    def test_describe_scope_returns_nothing_for_an_unresolved_stated_line(self):
        result = AgentResult("seat", "anthropic", True, _reply("nothing"), 0.0)
        self.assertEqual(describe_scope(result, [], changed=_CHANGED), "")


class TheRuleIsWiredIntoARealRun(unittest.TestCase):
    """The unit tests hand `reviewer_ballots` a change index. A real run has to
    *build* one and carry it, or the rule is enforced only in the tests."""

    _TOML = """
[jury]
rounds = 1
chair = "claude"
verify = false

[[agent]]
name = "claude"
vendor = "anthropic"
command = "claude"
"""

    def _run(self, checked: str) -> list[dict]:
        reply = _reply(checked)
        # Bound before the patch, or the fall-through below re-enters the patch.
        real = MockAdapter.run

        def _mock_run(self, prompt, phase="review", timeout=None, role_policy=None):
            if phase == "review":
                return AgentResult(self.name, self.spec.vendor, True, reply, 0.0)
            return real(self, prompt, phase=phase, timeout=timeout, role_policy=role_policy)

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "jury.toml"
            config.write_text(self._TOML, encoding="utf-8")
            diff = Path(tmp) / "changes.diff"
            diff.write_text(_DIFF, encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with (
                mock.patch.object(MockAdapter, "run", _mock_run),
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
            ):
                code = cli.main(
                    [
                        "--mock",
                        "--diff-file",
                        str(diff),
                        "--config",
                        str(config),
                        "--format",
                        "json",
                        "-q",
                    ]
                )
            self.assertIn(code, (0, 3), err.getvalue())
            return json.loads(out.getvalue())["reviewers"]

    def test_checked_nothing_does_not_survive_a_real_run(self):
        ballot = self._run("nothing")[0]
        self.assertFalse(ballot["counts_as_review"])
        self.assertEqual(ballot["abstention_cause"], panel.NAMED_NOTHING)

    def test_a_file_in_the_diff_does(self):
        ballot = self._run("src/ai_jury/ballots.py")[0]
        self.assertTrue(ballot["counts_as_review"])


#: A change whose paths make the *tokeniser* the thing under test: one file with
#: a space in its name, and one with a hyphen and two dots. Both are ordinary
#: repository filenames; neither survives a lexical split intact.
_SPACED_DIFF = """diff --git a/docs/my file.py b/docs/my file.py
--- a/docs/my file.py
+++ b/docs/my file.py
@@ -1,2 +1,2 @@
-def old_name():
+def new_name():
diff --git a/docs/release-notes.v2.md b/docs/release-notes.v2.md
--- a/docs/release-notes.v2.md
+++ b/docs/release-notes.v2.md
@@ -1 +1 @@
-old
+new
"""

_SPACED = change_index(_SPACED_DIFF)

#: A change to a dotfile: the name whose first character is the one `_TOKEN_EDGE`
#: strips, and whose lookup has no component boundary to fall back on.
_DOTFILE = change_index(
    "diff --git a/.gitignore b/.gitignore\n"
    "--- a/.gitignore\n+++ b/.gitignore\n@@ -1 +1 @@\n-old\n+new\n"
)


class AStatedScopeIsTokenisedAgainstTheChange(unittest.TestCase):
    """#710, round 2: the change index cuts the tokens, not the whitespace.

    The old `_SCOPE_TOKEN_SPLIT` split a stated value on whitespace and list
    punctuation alike, so `Checked: docs/my
    file.py` became `docs/my` and `file.py` — two tokens, neither of them in a
    diff that changes `docs/my file.py`. The ballot came back
    `scope_substantive: false`, `counts_as_review: false`,
    `abstention_cause: not_in_change`: a real review of a real file, refused for
    a space in its name, by the rule that exists to catch reviews of nothing.

    The whole point of #710 is that the tool holds the diff at that moment, so
    the diff — not the whitespace — is what says where a token ends.

    Round 3 carries the same principle into the two steps either side of the
    split, which were still deciding on their own say-so. Edge-stripping ran
    `.gitignore` down to `gitignore` and the ballot said the line named no path
    at all; a span that was quoted *and* backticked was looked up with its inner
    marks still on. Both are now answered by the change: a strip is retried
    against the index, and nested marks come off before the lookup.
    """

    def _ballot(self, checked: str) -> dict:
        config = _config([{"name": "seat", "vendor": "anthropic", "command": "claude"}])
        result = AgentResult("seat", "anthropic", True, _reply(checked), 0.0)
        return reviewer_ballots(_outcome([result], changed=_SPACED), config)[0]

    def test_a_changed_path_with_a_space_in_it_is_a_review(self):
        """The reported defect. Fails on bd8aaae with `not_in_change`."""
        ballot = self._ballot("docs/my file.py")
        self.assertTrue(ballot["scope_substantive"])
        self.assertTrue(ballot["counts_as_review"])
        self.assertEqual(ballot["abstention_cause"], "")
        self.assertIn("`docs/my file.py`", ballot["scope"])

    def test_a_changed_path_with_a_hyphen_and_a_dot_is_a_review(self):
        ballot = self._ballot("docs/release-notes.v2.md")
        self.assertTrue(ballot["counts_as_review"])
        self.assertIn("`docs/release-notes.v2.md`", ballot["scope"])

    def test_a_backticked_path_is_one_token_whatever_is_inside_it(self):
        """A reviewer that quoted a name has already said where it begins and
        ends; no rule here is entitled to a second opinion about that."""
        ballot = self._ballot("`docs/my file.py`")
        self.assertTrue(ballot["counts_as_review"])
        self.assertIn("`docs/my file.py`", ballot["scope"])

    def test_a_double_quoted_path_is_too(self):
        self.assertTrue(self._ballot('"docs/my file.py"')["counts_as_review"])

    def test_and_a_curly_quoted_one(self):
        """A reviewer writing prose types the quotes its own model produces."""
        self.assertTrue(self._ballot("\u201cdocs/my file.py\u201d")["counts_as_review"])

    def test_a_quoted_span_may_sit_inside_the_line(self):
        """The reviewer's marks bound one token; the text either side of them is
        still tokenised against the change like any other."""
        ballot = self._ballot("docs/release-notes.v2.md and `docs/my file.py`")
        self.assertTrue(ballot["counts_as_review"])
        self.assertIn("`docs/release-notes.v2.md`", ballot["scope"])
        self.assertIn("`docs/my file.py`", ballot["scope"])

    def test_the_join_stops_at_list_punctuation(self):
        """A comma is a boundary the reviewer wrote down; whitespace is not. The
        mixed-line rule still applies across it."""
        ballot = self._ballot("docs/my file.py, src/made/up.py")
        self.assertTrue(ballot["counts_as_review"])
        self.assertIn("`docs/my file.py`", ballot["scope"])
        self.assertIn("The same line also named", ballot["scope"])

    def test_joining_never_invents_a_token_the_change_does_not_have(self):
        """The join is only ever accepted when the index confirms it, so the
        failure direction stays `unresolved` — `Checked: nothing at all` is not
        rescued by having three words to try."""
        ballot = self._ballot("nothing at all")
        self.assertFalse(ballot["counts_as_review"])
        self.assertEqual(ballot["abstention_cause"], panel.NAMED_NOTHING)

    def test_the_longest_join_the_change_confirms_wins(self):
        """With `my file.py` and `docs/my file.py` both changed, a reviewer that
        wrote the second named the second."""
        changed = change_index(
            _SPACED_DIFF + "diff --git a/my file.py b/my file.py\n"
            "--- a/my file.py\n+++ b/my file.py\n@@ -1 +1 @@\n-old\n+new\n"
        )
        self.assertEqual(resolve_stated_scope("docs/my file.py", changed)[0], ["docs/my file.py"])

    def test_a_spaced_path_with_a_line_number_resolves_on_its_path(self):
        self.assertEqual(
            resolve_stated_scope("docs/my file.py:2", _SPACED)[0], ["docs/my file.py:2"]
        )

    def test_a_quoted_token_that_is_absent_is_still_reported_as_a_claim(self):
        """Quoting a token is the reviewer saying it is a name. A name that is
        not in the change is a claim that failed, and has to be reported as one
        rather than dropped as connective prose."""
        resolved, unresolved = resolve_stated_scope('"the whole repo"', _SPACED)
        self.assertEqual(resolved, [])
        self.assertEqual(unresolved, ["the whole repo"])

    def test_a_quoted_leading_dot_file_keeps_its_dot(self):
        """Inside the reviewer's own marks there is no stray punctuation to
        strip, and stripping it would cost `.gitignore` its first character."""
        self.assertEqual(resolve_stated_scope("`.gitignore`", _DOTFILE)[0], [".gitignore"])

    def test_an_unquoted_leading_dot_file_keeps_its_dot_too(self):
        """#710, round 3. Fails on d3d0ef7 with `named_nothing`.

        `_TOKEN_EDGE` contains `.`, so the unquoted token stripped down to
        `gitignore`, which `has_path` does not match — it wants a path-component
        boundary, and `.gitignore` has none before the `g`. The remnant is not
        name-shaped either, so it was dropped as prose and the scope claimed the
        line named no path at all, for a file the reviewer named exactly.
        """
        self.assertEqual(resolve_stated_scope(".gitignore", _DOTFILE)[0], [".gitignore"])

    def test_the_dot_survives_trailing_punctuation_around_it(self):
        """The strip still has to do its job at the other end: `.gitignore,` and
        `(.gitignore)` are the same claim as `.gitignore`."""
        for stated in (".gitignore, and nothing else", "(.gitignore).", ".gitignore;"):
            with self.subTest(stated=stated):
                self.assertEqual(resolve_stated_scope(stated, _DOTFILE)[0], [".gitignore"])

    def test_a_dotfile_inside_a_directory_resolves_from_either_root(self):
        """The component boundary is the only thing the leading dot was ever in
        the way of: `.env` names `docs/.env`, and so does the full path."""
        changed = change_index(
            "diff --git a/docs/.env b/docs/.env\n"
            "--- a/docs/.env\n+++ b/docs/.env\n@@ -1 +1 @@\n-old\n+new\n"
        )
        self.assertEqual(resolve_stated_scope(".env", changed)[0], [".env"])
        self.assertEqual(resolve_stated_scope("docs/.env", changed)[0], ["docs/.env"])

    def test_stripping_still_never_puts_back_what_is_not_a_path(self):
        """The retry is answered by the change, not by the punctuation: nothing
        the index confirms leaves the full strip exactly as it was."""
        ballot = self._ballot("nothing at all.")
        self.assertFalse(ballot["counts_as_review"])
        self.assertEqual(ballot["abstention_cause"], panel.NAMED_NOTHING)

    def test_a_path_both_quoted_and_backticked_resolves_like_either_alone(self):
        """#710, round 3. Fails on d3d0ef7: the span kept its inner backticks,
        the lookup was of ``“`docs/my file.py`”``'s literal text, and the
        reviewer's own path came back as a claim that failed."""
        for stated in (
            "“`docs/my file.py`”",
            '"`docs/my file.py`"',
            '`"docs/my file.py"`',
            # All three marks at once — the deepest nesting there is, since no
            # mark can sit inside itself.
            '“"`docs/my file.py`"”',
        ):
            with self.subTest(stated=stated):
                self.assertEqual(resolve_stated_scope(stated, _SPACED), (["docs/my file.py"], []))

    def test_a_nested_span_that_is_absent_is_still_reported_as_a_claim(self):
        """Peeling the marks off changes what is looked up, not whether the
        reviewer marked it: an absent nested token is still a name that failed."""
        self.assertEqual(
            resolve_stated_scope("“`the whole repo`”", _SPACED),
            ([], ["the whole repo"]),
        )


class ARecomputedModelIdIsNotLabelledAsSent(unittest.TestCase):
    """#709, round 2: the fallback needs its own provenance token.

    `sent_model` is empty for every record no invocation stamped — a hand-built
    result, a chair slot with no round-1 seat — and `describe_model` fell back to
    `requested_model` while keeping `model_source: requested`, the token whose
    whole claim is that the id was on the wire.

    The seat from #709's own report is the witness. A Google seat at `effort =
    high` whose live model listing does not offer `gemini-3-pro-high` is invoked
    with `gemini-3-pro`, and its fresh ballot says so. The same seat with no
    recorded id recomputes `gemini-3-pro-high` — the overstated provenance #709
    removed, restated for every record the invocation did not stamp.

    `recomputed` rather than reusing `unknown`: `unknown` already means *this
    slot has no spec in the run's config*, and folding two different facts into
    one token is the same defect one level down. The id is real and worth
    quoting; what it is not is evidence of what was sent, and the token says so.
    """

    _SPEC = {
        "name": "seat",
        "vendor": "google",
        "adapter": "google",
        "command": "agy",
        "model": "gemini-3-pro",
        "effort": "high",
    }

    def _ballots(self) -> tuple[dict, dict]:
        """``(fresh, legacy)`` ballots for one seat: stamped, and with it cleared."""
        config = _config([dict(self._SPEC)])
        spec = config.agents[0]
        adapter = make_adapter(spec)
        canned = AgentResult(spec.name, spec.vendor, True, _reply("`x`"), 0.0)
        with (
            mock.patch.object(type(adapter), "run", lambda *_a, **_k: canned),
            # The live listing that forces the fallback — stubbed, because a
            # test whose expected id depends on the machine's `agy` catalogue is
            # not a test.
            mock.patch.object(type(adapter), "list_models", lambda _self: ["gemini-3-pro"]),
        ):
            argv = adapter.build_argv("prompt")
            fresh = _run_with_retry(
                adapter, "prompt", "review", RunBudget(None, None), 0, lambda _m: None
            )
        self.assertEqual(_model_in_argv(argv), "gemini-3-pro")
        legacy = replace(fresh, model="")  # the shape of a record written before #709
        return (
            reviewer_ballots(_outcome([fresh]), config)[0],
            reviewer_ballots(_outcome([legacy]), config)[0],
        )

    def test_the_fresh_ballot_still_quotes_the_id_the_adapter_sent(self):
        fresh, _ = self._ballots()
        self.assertEqual(fresh["model"], "gemini-3-pro")
        self.assertEqual(fresh["model_source"], MODEL_REQUESTED)

    def test_a_record_with_no_recorded_id_is_recomputed_never_requested(self):
        """Fails on bd8aaae: the same `gemini-3-pro-high` came back under
        `requested`, which is exactly the claim #709 exists to stop making."""
        fresh, legacy = self._ballots()
        self.assertEqual(legacy["model"], "gemini-3-pro-high")
        self.assertNotEqual(legacy["model"], fresh["model"])
        self.assertEqual(legacy["model_source"], MODEL_RECOMPUTED)

    def test_a_seat_with_nothing_pinned_is_still_the_cli_default(self):
        """The recomputation has to be empty before `cli_default` is reached, so
        the new branch must not swallow the #700 case."""
        config = _config([{"name": "seat", "vendor": "anthropic", "command": "claude"}])
        result = AgentResult("seat", "anthropic", True, _reply("`x`"), 0.0)
        ballot = reviewer_ballots(_outcome([result]), config)[0]
        self.assertEqual(ballot["model_source"], "cli_default")
        self.assertIn("claude", ballot["model"])


#: A change that touches one ordinary path and no dotfile — the index against
#: which every dotfile a reviewer might name is *absent*.
_NO_DOTFILE = change_index(
    "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-old\n+new\n"
)


class AnAbsentDotfileIsNamedNotNothing(unittest.TestCase):
    """#710, round 4: the strip may not eat the name on the way to the fallback.

    Round 3 taught :func:`ai_jury.ballots._edge_stripped` to put a stripped edge
    character back when the change index confirms the restored trim, which fixed
    the dotfile the diff *contains*. The dotfile it does not contain fell
    through the other side: no trim resolves, the full strip is returned,
    ``.gitignore`` arrives as ``gitignore`` — not name-shaped, so dropped as
    connective prose — and the ballot recorded ``named_nothing``.

    That is the wrong half of the documented split. ``named_nothing`` says the
    line named nothing checkable; ``not_in_change`` says it named things and
    none of them are here. A reviewer that wrote ``Checked: .gitignore`` named a
    file exactly, and the two causes send their reader to opposite places. The
    fallback now returns the first name-shaped trim, so an absent dotfile stays
    a name and is reported as the claim that failed.
    """

    def _ballot(self, checked: str) -> dict:
        config = _config([{"name": "seat", "vendor": "anthropic", "command": "claude"}])
        result = AgentResult("seat", "anthropic", True, _reply(checked), 0.0)
        return reviewer_ballots(_outcome([result], changed=_NO_DOTFILE), config)[0]

    def test_an_absent_top_level_dotfile_is_not_in_change(self):
        """The reported defect. Fails on cd5a122 with `named_nothing`."""
        ballot = self._ballot(".gitignore")
        self.assertFalse(ballot["counts_as_review"])
        self.assertEqual(ballot["abstention_cause"], panel.NOT_IN_CHANGE)
        self.assertIn("gitignore", ballot["scope"])
        self.assertIn("no such path or symbol is in this change", ballot["scope"])

    def test_the_absent_dotfile_is_the_token_the_scope_reports(self):
        """De-anchored in the sentence, but a name in the split it came from."""
        self.assertEqual(resolve_stated_scope(".gitignore", _NO_DOTFILE), ([], [".gitignore"]))

    def test_an_absent_nested_dotfile_buckets_the_same_way(self):
        ballot = self._ballot("docs/.env")
        self.assertEqual(ballot["abstention_cause"], panel.NOT_IN_CHANGE)
        self.assertEqual(resolve_stated_scope("docs/.env", _NO_DOTFILE), ([], ["docs/.env"]))

    def test_a_dotfile_the_change_does_contain_is_still_a_review(self):
        """Round 3's case is untouched: the index still confirms the restored
        trim before the name-shaped fallback is ever reached."""
        config = _config([{"name": "seat", "vendor": "anthropic", "command": "claude"}])
        result = AgentResult("seat", "anthropic", True, _reply(".gitignore"), 0.0)
        ballot = reviewer_ballots(_outcome([result], changed=_DOTFILE), config)[0]
        self.assertTrue(ballot["counts_as_review"])
        self.assertEqual(ballot["abstention_cause"], "")
        self.assertIn("`.gitignore`", ballot["scope"])

    def test_prose_is_not_rescued_by_the_fallback(self):
        """The fallback restores nothing that is not a name, so a line of prose
        is still a line that named nothing — the full strip, dropped."""
        for stated in ("nothing at all.", "nothing", "everything, really."):
            with self.subTest(stated=stated):
                ballot = self._ballot(stated)
                self.assertFalse(ballot["counts_as_review"])
                self.assertEqual(ballot["abstention_cause"], panel.NAMED_NOTHING)

    def test_a_wrapped_path_still_resolves_and_still_strips(self):
        """`(src/a.py),` is one token with the change, and an absent one comes
        back fully stripped rather than in its parentheses: the fallback puts
        back the *fewest* characters that leave a name, not the most."""
        self.assertEqual(resolve_stated_scope("(src/a.py),", _NO_DOTFILE)[0], ["src/a.py"])
        self.assertEqual(
            resolve_stated_scope("(src/made/up.py),", _NO_DOTFILE)[1], ["src/made/up.py"]
        )


def _change_to(path: str):
    """A one-file change index touching *path* and nothing else."""
    return change_index(
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old\n+new\n"
    )


class AStrippedDotIsNotAnotherFile(unittest.TestCase):
    """#710, round 5: the strip may not turn one file's name into another's.

    Rounds 3 and 4 stripped the leading dot with the rest of the edge
    punctuation and then tried to undo it — the fully stripped form first, then
    the trims that put the stripped characters back, and the first trim the
    change index confirmed was the token. Offering the fully stripped form first
    is what breaks: ``.env`` is offered as ``env``, and in a diff that changes a
    file *named* ``env`` — or ``bin/env``, where the lookup matches on a
    component boundary — the index confirms it. The ballot then counted as a
    review of a file the reviewer never named, on a token the strip invented.
    Same shape for ``.gitignore`` against a changed ``src/gitignore``.

    The rule is simpler now instead of longer: a leading dot is part of the file
    name and is never stripped, so ``.env`` stays ``.env`` and a change that
    does not contain it reports the claim that failed under ``not_in_change``.
    """

    def _ballot(self, checked: str, changed) -> dict:
        config = _config([{"name": "seat", "vendor": "anthropic", "command": "claude"}])
        result = AgentResult("seat", "anthropic", True, _reply(checked), 0.0)
        return reviewer_ballots(_outcome([result], changed=changed), config)[0]

    def test_a_dotfile_is_not_the_undotted_file_of_the_same_name(self):
        """The reported defect. Fails on c49eec4: `.env` resolves on `env`."""
        ballot = self._ballot(".env", _change_to("env"))
        self.assertFalse(ballot["counts_as_review"])
        self.assertEqual(ballot["abstention_cause"], panel.NOT_IN_CHANGE)
        self.assertIn(".env", ballot["scope"])
        self.assertEqual(resolve_stated_scope(".env", _change_to("env")), ([], [".env"]))

    def test_a_dotfile_is_not_the_undotted_file_in_a_directory(self):
        """Fails on c49eec4 too: stripped to `env`, `bin/env` matches on the
        component boundary the dot was hiding."""
        ballot = self._ballot(".env", _change_to("bin/env"))
        self.assertFalse(ballot["counts_as_review"])
        self.assertEqual(ballot["abstention_cause"], panel.NOT_IN_CHANGE)
        self.assertIn(".env", ballot["scope"])
        self.assertEqual(resolve_stated_scope(".env", _change_to("bin/env")), ([], [".env"]))

    def test_the_same_holds_for_a_longer_dotfile_name(self):
        """`.gitignore` against a changed `src/gitignore`. Fails on c49eec4."""
        changed = _change_to("src/gitignore")
        ballot = self._ballot(".gitignore", changed)
        self.assertFalse(ballot["counts_as_review"])
        self.assertEqual(ballot["abstention_cause"], panel.NOT_IN_CHANGE)
        self.assertIn(".gitignore", ballot["scope"])
        self.assertEqual(resolve_stated_scope(".gitignore", changed), ([], [".gitignore"]))


if __name__ == "__main__":  # pragma: no cover - manual runs
    unittest.main()


class APathShapedTokenIsNeverReadAsASymbol(unittest.TestCase):
    """Round 6. ``.env`` against a hunk that adds ``env(1)`` resolved on the
    remnant ``env`` and counted as a review of a file the reviewer never named;
    and prose ``nothing (just wording)`` in the diff indexed ``nothing`` as a
    callable, so ``Checked: nothing`` counted too. A path claim resolves only
    as a path, and only ``name(`` — no space — is a call."""

    _CALL_HUNK = (
        "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
        "@@ -1 +1,2 @@\n old\n+    env(1)\n"
    )
    _PROSE_HUNK = (
        "diff --git a/docs/x.md b/docs/x.md\n--- a/docs/x.md\n+++ b/docs/x.md\n"
        "@@ -1 +1 @@\n-old\n+This changes nothing (just wording).\n"
    )

    def test_a_dotfile_does_not_resolve_on_a_called_remnant(self):
        changed = change_index(self._CALL_HUNK)
        self.assertTrue(changed.has_symbol("env"))
        self.assertEqual(resolve_stated_scope(".env", changed), ([], [".env"]))
        ballot = _reviewers_for(self._CALL_HUNK, "Checked: .env")[0]
        self.assertFalse(ballot["counts_as_review"])
        self.assertEqual(ballot["abstention_cause"], "not_in_change")

    def test_a_dotted_name_without_parentheses_is_a_path_claim(self):
        """Round 7: an extension list cannot be complete — `foo.proto` fell
        through to the symbol index and matched a `proto()` call."""
        hunk = self._CALL_HUNK.replace("env(1)", "proto(1)")
        changed = change_index(hunk)
        self.assertTrue(changed.has_symbol("proto"))
        for token in ("env.py", "foo.proto", "module.env"):
            with self.subTest(token=token):
                self.assertEqual(resolve_stated_scope(token, changed), ([], [token]))

    def test_a_wrapped_call_keeps_its_marker(self):
        """Round 8: the marker was looked for at the raw end, so wrapping
        parentheses hid it and then were stripped along with it."""
        changed = change_index(self._CALL_HUNK)
        for token in (
            "(module.env()),",
            "[module.env()]",
            "{module.env()}",
            "`module.env()`.",
            "(env())",
        ):
            with self.subTest(token=token):
                resolved, _ = resolve_stated_scope(token, changed)
                self.assertEqual(len(resolved), 1, token)
                self.assertTrue(resolved[0].endswith("()"), resolved)

    def test_an_identifier_still_resolves_as_the_member_named(self):
        changed = change_index(self._CALL_HUNK)
        self.assertEqual(resolve_stated_scope("module.env()", changed), (["module.env()"], []))
        self.assertEqual(resolve_stated_scope("env", changed), (["env"], []))

    def test_prose_followed_by_a_space_and_a_parenthesis_is_not_a_call(self):
        changed = change_index(self._PROSE_HUNK)
        self.assertFalse(changed.has_symbol("nothing"))
        ballot = _reviewers_for(self._PROSE_HUNK, "Checked: nothing")[0]
        self.assertFalse(ballot["counts_as_review"])
        self.assertEqual(ballot["abstention_cause"], "named_nothing")

    def test_a_real_call_is_still_indexed(self):
        changed = change_index(self._PROSE_HUNK.replace("nothing (just", "nothing(just"))
        self.assertTrue(changed.has_symbol("nothing"))


def _reviewers_for(diff: str, first_line: str) -> list[dict]:
    """Ballot records for one seat whose reply opens with *first_line*, against *diff*."""
    changed = change_index(diff)
    result = AgentResult(
        "claude", "anthropic", True, f"{first_line}\n\nTested: nothing.\n\nNo concerns.\n", 0.0
    )
    config = _config([{"name": "claude", "vendor": "anthropic", "command": "claude"}])
    return [
        r
        for r in reviewer_ballots(_outcome([result], changed=changed), config)
        if r.get("role") == "panelist"
    ]
