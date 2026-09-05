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
from pathlib import Path

from ai_jury import cli, panel
from ai_jury.adapters import AgentResult, MockAdapter, effort_args, make_adapter
from ai_jury.ballots import (
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


if __name__ == "__main__":  # pragma: no cover - manual runs
    unittest.main()
