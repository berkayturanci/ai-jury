"""CI-safe adapter contract probes (issue #682).

#635 is the failure these exist to catch. `agy` 1.1 gave `--print` an argument,
so the adapter's invocation died in the launcher before the model was reached.
Every check the project had still passed: the CLI was on `PATH`, `--version`
exited 0, `jury --doctor` printed `[available] probe: ok`. The agent contributed
nothing, the run stayed fail-soft, and a three-vendor jury shipped a verdict
formed by one vendor.

The tests that would have caught it existed — `tests/live/test_live_smoke.py` —
and are excluded from CI for a good reason: they cost real model calls. So the
hole was never "nobody thought to test the adapters", it was that the only test
of the *invocation* needed auth and spend.

This module closes that. It drives each shipped adapter's REAL argv builder,
stdin encoder, and stdout parser, against recorded fixtures:

* the invocation shape is locked in `tests/golden/adapter_contracts.json`, which
  is deliberately hand-maintained rather than generated from the source — a
  golden regenerated from the code under test asserts nothing;
* the transport is a recorder standing in for `adapters._spawn`, so what the
  adapter would have executed is captured exactly;
* the responses are `tests/fixtures/contracts/*.stdout`, recorded from the real
  CLIs, so the parse path runs on bytes a vendor actually emitted.

No network, no auth, no spend, no subprocess: it runs on every matrix entry.
The live variant of the same contract is `tests/live/test_live_contracts.py`,
still behind `JURY_LIVE=1`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_jury import adapters  # noqa: E402
from ai_jury.adapters import (  # noqa: E402
    ERR_EMPTY_OUTPUT,
    ERR_NO_REVIEW,
    RETRYABLE_ERROR_CODES,
    make_adapter,
    no_review_reason,
)
from ai_jury.config import AgentSpec  # noqa: E402
from ai_jury.findings import emitted_findings_block  # noqa: E402
from ai_jury.metadata import panel_accounting, review_status  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "contracts"
CONTRACTS = json.loads(
    (Path(__file__).resolve().parent / "golden" / "adapter_contracts.json").read_text(
        encoding="utf-8"
    )
)
CONTRACTS.pop("_comment", None)

TINY_DIFF = (FIXTURES / "tiny.diff").read_text(encoding="utf-8")
PROMPT = f"Review this diff and report findings.\n\n```diff\n{TINY_DIFF}```\n"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class Recorder:
    """Stands in for :func:`adapters._spawn` and records the invocation.

    The point of substituting here rather than at `subprocess.Popen` is that
    everything above it — `build_argv_for_role`, `_stdin_for`, the timeout
    arithmetic, `_text_from_stdout`, the exit-code and empty-output branches —
    is the real code path, exactly as a live run would take it.
    """

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode
        self.calls: list[tuple[list[str], str | None, int]] = []

    def __call__(self, argv, stdin, timeout):
        self.calls.append((list(argv), stdin, timeout))
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)

    @property
    def argv(self) -> list[str]:
        return self.calls[-1][0]

    @property
    def stdin(self) -> str | None:
        return self.calls[-1][1]


def spec_for(name: str) -> AgentSpec:
    """The `AgentSpec` the locked contract describes."""
    contract = CONTRACTS[name]
    kwargs = {
        "name": name,
        "vendor": contract["vendor"],
        "command": contract["command"],
        "model": contract["model"],
        "extra_args": list(contract["extra_args"]),
    }
    if contract.get("prompt_mode"):
        kwargs["prompt_mode"] = contract["prompt_mode"]
    return AgentSpec(**kwargs)


def probe(name: str, *, stdout: str = "", stderr: str = "", returncode: int = 0):
    """Run one adapter end to end against a recorded response.

    Returns ``(AgentResult, Recorder)``. ``shutil.which`` is patched because the
    contract under test is the *invocation*, which a real run only reaches after
    the availability check — and CI has none of these CLIs installed.
    """
    adapter = make_adapter(spec_for(name))
    recorder = Recorder(stdout=stdout, stderr=stderr, returncode=returncode)
    with (
        mock.patch.object(adapters.shutil, "which", lambda cmd: f"/usr/local/bin/{cmd}"),
        mock.patch.object(adapters, "_spawn", recorder),
    ):
        result = adapter.run(PROMPT, phase="review")
    return result, recorder


class TheLockedInvocationShapes(unittest.TestCase):
    """What each adapter would actually execute, argument for argument.

    An assertion per contract key rather than one big equality, so a failure
    names which property of the invocation moved.
    """

    def test_every_shipped_cli_adapter_is_covered(self):
        """A new subprocess adapter must arrive with a locked contract.

        Without this the suite would keep passing while a whole vendor went
        unlocked — which is the state #635 shipped in.
        """
        locked = {CONTRACTS[name]["vendor"] for name in CONTRACTS}
        shipped = {
            vendor
            for vendor, cls in adapters._VENDOR_ADAPTERS.items()
            # Network adapters build no argv; they are covered by the
            # fixture-based response tests instead (see below).
            if issubclass(cls, adapters.Adapter)
            and not issubclass(cls, adapters._HostedApiAdapter)
            and cls is not adapters.LocalAdapter
        }
        self.assertTrue(shipped, "the vendor registry went empty; this check is vacuous")
        self.assertEqual(shipped - locked, set(), "adapter vendor with no locked contract")

    def test_read_only_argv_matches_the_lock(self):
        for name, contract in CONTRACTS.items():
            with self.subTest(name):
                argv = make_adapter(spec_for(name)).build_argv(PROMPT)
                expected = [a if a != "<PROMPT>" else PROMPT for a in contract["argv"]]
                self.assertEqual(argv, expected)

    def test_write_argv_matches_the_lock(self):
        """`jury run-agent --role implement` is the only caller (#661).

        Locked here too because the read-only guarantee is expressed as the
        DIFFERENCE between these two lists: drop the wrong flag and a reviewer
        of an attacker-controlled diff becomes tool-capable.
        """
        for name, contract in CONTRACTS.items():
            with self.subTest(name):
                argv = make_adapter(spec_for(name)).build_write_argv(PROMPT)
                expected = [a if a != "<PROMPT>" else PROMPT for a in contract["write_argv"]]
                self.assertEqual(argv, expected)

    def test_the_sandbox_flag_survives_into_the_read_only_argv(self):
        for name, contract in CONTRACTS.items():
            with self.subTest(name):
                argv = make_adapter(spec_for(name)).build_argv(PROMPT)
                for flag in contract["sandbox_flags"]:
                    self.assertIn(flag, argv)

    def test_the_prompt_never_reaches_argv_where_the_contract_says_so(self):
        """#287: the redacted diff must not be readable in `ps`."""
        secret = "SENSITIVE-DIFF-CONTENT"
        for name, contract in CONTRACTS.items():
            if not contract["prompt_absent_from_argv"]:
                continue
            with self.subTest(name):
                argv = make_adapter(spec_for(name)).build_argv(secret)
                self.assertFalse([a for a in argv if secret in a], argv)


class TheTransportCarriesThePrompt(unittest.TestCase):
    """The half of the contract argv alone cannot express."""

    def test_stdin_mode_matches_the_lock(self):
        for name, contract in CONTRACTS.items():
            with self.subTest(name):
                _result, recorder = probe(name, stdout=fixture(contract["stdout_fixture"]))
                mode = contract["stdin"]
                if mode == "prompt":
                    self.assertEqual(recorder.stdin, PROMPT)
                elif mode == "none":
                    self.assertIsNone(recorder.stdin)
                else:
                    self.assertEqual(mode, "stream-json")
                    frames = [json.loads(x) for x in recorder.stdin.splitlines() if x.strip()]
                    self.assertEqual(len(frames), 1)
                    self.assertEqual(frames[0]["event"], "user")
                    self.assertEqual(frames[0]["message"]["content"], PROMPT)

    def test_the_recorded_invocation_is_the_locked_one(self):
        """Belt and braces: what `run` executes, not only what `build_argv` returns."""
        for name, contract in CONTRACTS.items():
            with self.subTest(name):
                _result, recorder = probe(name, stdout=fixture(contract["stdout_fixture"]))
                expected = [a if a != "<PROMPT>" else PROMPT for a in contract["argv"]]
                self.assertEqual(recorder.argv, expected)


class ARecordedResponseParsesIntoAReview(unittest.TestCase):
    """The parse path, on bytes the real CLIs emitted."""

    def test_every_adapter_returns_a_usable_review(self):
        for name, contract in CONTRACTS.items():
            with self.subTest(name):
                result, _recorder = probe(name, stdout=fixture(contract["stdout_fixture"]))
                self.assertTrue(result.ok, result.error)
                self.assertIsNone(result.error_code)
                self.assertEqual(result.exit_code, 0)
                self.assertTrue(result.output.strip())

    def test_the_parsed_text_carries_the_findings_block(self):
        """Not merely non-empty: a review the panel can actually count.

        For `agy` this is the whole stream-json unwrap — the NDJSON envelope
        must be gone and the model's markdown left behind.
        """
        for name, contract in CONTRACTS.items():
            with self.subTest(name):
                result, _recorder = probe(name, stdout=fixture(contract["stdout_fixture"]))
                self.assertTrue(emitted_findings_block(result.output))
                self.assertNotIn('"event"', result.output)


class ADeadSeatIsRecordedAsOne(unittest.TestCase):
    """Exit 0 plus text is not a review. The #635 shape, and its neighbours."""

    def test_the_635_shape_is_a_typed_failure(self):
        """agy answered the launcher, not the diff, and exited 0.

        This is the regression test the issue asks for: before #682 the same
        bytes came back `ok=True` with the usage text as the "review".
        """
        result, _recorder = probe("agy", stdout=fixture("usage_echo.stdout"))
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ERR_NO_REVIEW)
        self.assertIn("usage", result.error)

    def test_a_refusal_is_a_typed_failure(self):
        for name in CONTRACTS:
            with self.subTest(name):
                result, _recorder = probe(name, stdout=fixture("refusal.stdout"))
                self.assertFalse(result.ok)
                self.assertEqual(result.error_code, ERR_NO_REVIEW)

    def test_a_probe_echo_is_a_typed_failure(self):
        result, _recorder = probe("claude", stdout=fixture("version_echo.stdout"))
        self.assertEqual(result.error_code, ERR_NO_REVIEW)

    def test_whitespace_only_output_keeps_its_own_code(self):
        """`empty_output` predates this and consumers key off it; don't move it."""
        result, _recorder = probe("codex", stdout="   \n\n  ")
        self.assertEqual(result.error_code, ERR_EMPTY_OUTPUT)

    def test_a_dead_seat_is_not_retried(self):
        """A misinvoked CLI prints the same banner every time."""
        self.assertNotIn(ERR_NO_REVIEW, RETRYABLE_ERROR_CODES)

    def test_the_agent_text_is_not_smuggled_into_the_output_field(self):
        """Downstream reads `output`; a refusal there would be synthesized."""
        result, _recorder = probe("claude", stdout=fixture("refusal.stdout"))
        self.assertEqual(result.output, "")

    def test_a_real_review_that_mentions_a_limit_is_still_a_review(self):
        """The guard is shape, not sentiment. False positives cost a panelist."""
        long_review = (
            "I cannot verify the migration without the schema, but the diff itself "
            "is reviewable and I have read it in full. " + ("Detail. " * 80) + "\n"
            "```json\n[]\n```\n"
        )
        result, _recorder = probe("claude", stdout=long_review)
        self.assertTrue(result.ok, result.error)
        self.assertIsNone(no_review_reason(long_review))


class ADeadSeatCannotCountTowardConsensus(unittest.TestCase):
    """The reason the typed code matters: the arithmetic downstream."""

    def test_a_no_review_seat_scores_as_failed(self):
        result, _recorder = probe("agy", stdout=fixture("usage_echo.stdout"))
        self.assertEqual(review_status(result), "failed")

    def test_a_panel_of_one_real_review_and_one_dead_seat_is_one_vendor(self):
        alive, _ = probe("claude", stdout=fixture("review.stdout"))
        dead, _ = probe("agy", stdout=fixture("usage_echo.stdout"))
        # `findings` are attached by the orchestrator; the accounting reads the
        # findings block via `structured`, so mirror what a real run would set.
        alive.structured = True
        panel = panel_accounting([alive, dead])
        self.assertEqual(panel["configured"], 2)
        self.assertEqual(panel["effective"], 1)
        self.assertEqual(panel["vendors"], 1)
        self.assertEqual(panel["failed"], 1)


class _FakeResp:
    """The stdlib response shape `adapters._open` yields."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def read(self, *_args):
        return self._payload


class TheNetworkAdaptersRefuseANonReviewToo(unittest.TestCase):
    """Same rule, the other transport (issue #682 A.3).

    A hosted or local model has no argv to get wrong, but it can still answer
    with a refusal — and a refusal counted as a review is the same collapse.
    Fixture-based, no network.
    """

    def test_a_local_model_refusal_is_a_typed_failure(self):
        payload = json.dumps(
            {"choices": [{"message": {"content": fixture("refusal.stdout")}}]}
        ).encode()
        spec = AgentSpec(name="qwen", vendor="local", model="qwen2.5-coder:7b")
        with mock.patch.object(adapters, "_open", return_value=_FakeResp(payload)):
            result = adapters.LocalAdapter(spec).run(PROMPT)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ERR_NO_REVIEW)

    def test_a_local_model_review_still_passes(self):
        payload = json.dumps(
            {"choices": [{"message": {"content": fixture("review.stdout")}}]}
        ).encode()
        spec = AgentSpec(name="qwen", vendor="local", model="qwen2.5-coder:7b")
        with mock.patch.object(adapters, "_open", return_value=_FakeResp(payload)):
            result = adapters.LocalAdapter(spec).run(PROMPT)
        self.assertTrue(result.ok, result.error)

    def test_a_hosted_api_refusal_is_a_typed_failure(self):
        payload = json.dumps(
            {"content": [{"type": "text", "text": fixture("refusal.stdout")}]}
        ).encode()
        spec = AgentSpec(name="claude-api", vendor="anthropic-api", model="claude-probe-1")
        with (
            mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "x"}, clear=False),
            mock.patch.object(adapters, "_open", return_value=_FakeResp(payload)),
        ):
            result = adapters.AnthropicApiAdapter(spec).run(PROMPT)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ERR_NO_REVIEW)


class TheClassifierItself(unittest.TestCase):
    """`no_review_reason` is the rule both transports and the live probe share."""

    def test_nothing_at_all(self):
        self.assertEqual(no_review_reason(""), "the agent produced no output")
        self.assertEqual(no_review_reason("  \n "), "the agent produced no output")
        self.assertEqual(no_review_reason(None), "the agent produced no output")

    def test_a_plain_review_passes(self):
        self.assertIsNone(no_review_reason(fixture("review.stdout")))


class ReviewsThatMustSurvive(unittest.TestCase):
    """The cost of a false positive is the whole point of the guard.

    `no_review_reason` fails CLOSED: a discarded review is a dead seat, and a
    dead seat can drop the panel under `min_vendors` and exit 3. So a review
    that merely *talks about* usage text, invalid arguments or `--help` must
    come through untouched. Every case here was a false positive of the first
    cut of the predicate.
    """

    def test_a_review_that_opens_with_the_word_usage(self):
        text = (
            "Usage of int() is unsafe here without handling ValueError: line 42 "
            "parses untrusted input straight from the request body, so a "
            "non-numeric value raises and the handler returns a 500 instead of "
            "a 400.\n\n```json\n[]\n```\n"
        )
        self.assertIsNone(no_review_reason(text))

    def test_a_finding_about_an_invalid_argument(self):
        text = (
            "Invalid argument passed to calculate_total() on line 42: the "
            "function expects a Decimal and the caller hands it a float, so "
            "the rounding is wrong for every currency with two decimals.\n"
        )
        self.assertIsNone(no_review_reason(text))

    def test_a_review_written_in_another_language(self):
        """English-shaped patterns must not swallow a non-English review."""
        text = (
            "Die Änderung ist korrekt, aber der Fehlerpfad in zeile 42 wird "
            "nicht getestet: ein leerer Eingabewert führt zu einer "
            "unbehandelten Ausnahme.\n"
        )
        self.assertIsNone(no_review_reason(text))

    def test_a_short_but_genuine_review(self):
        """Brevity is not a refusal. Some reviews really are one sentence."""
        text = "Looks correct to me; the null check on line 7 covers the case.\n"
        self.assertIsNone(no_review_reason(text))

    def test_a_review_that_points_at_the_docs(self):
        """`for more information, see …` is a sentence a human writes too."""
        text = (
            "The retry budget is undocumented at this call site; for more "
            "information, see the docs on backoff before changing it.\n"
        )
        self.assertIsNone(no_review_reason(text))

    def test_a_review_that_quotes_a_flag_name(self):
        """Reviews discuss flags; a leading dash is not banner structure."""
        text = (
            "Unknown option --min-vendors is what a user gets on 1.14, so this "
            "README snippet needs a version note.\n"
        )
        self.assertIsNone(no_review_reason(text))


class OutputsThatMustNotCountAsReviews(unittest.TestCase):
    """The other half of the same bargain: real banners still die."""

    def test_the_recorded_usage_banner(self):
        self.assertEqual(
            no_review_reason(fixture("usage_echo.stdout")),
            "the CLI printed usage or argument-error text instead of a review",
        )

    def test_a_bare_version_string(self):
        self.assertEqual(
            no_review_reason(fixture("version_echo.stdout")),
            "the CLI printed only a version banner instead of a review",
        )
        self.assertEqual(
            no_review_reason("1.1.22"),
            "the CLI printed only a version banner instead of a review",
        )

    def test_a_real_refusal(self):
        self.assertEqual(
            no_review_reason(fixture("refusal.stdout")), "the agent declined to review"
        )

    def test_nothing_and_whitespace(self):
        self.assertEqual(no_review_reason(""), "the agent produced no output")
        self.assertEqual(no_review_reason("\t\n  \n"), "the agent produced no output")

    def test_a_synopsis_with_an_argument_error_prefix(self):
        text = "error: unknown flag: --print\nRun 'agy --help' for usage.\n"
        self.assertIsNotNone(no_review_reason(text))

    def test_a_go_style_usage_of_line(self):
        text = "Usage of /usr/local/bin/agy:\n  -print string\n    \tthe prompt\n"
        self.assertIsNotNone(no_review_reason(text))

    def test_a_bare_argument_error_with_banner_structure(self):
        text = "unknown flag: --print\nUsage: agy [options] [prompt]\n  --model id\n"
        self.assertIsNotNone(no_review_reason(text))

    def test_a_shell_not_found_line(self):
        self.assertIsNotNone(no_review_reason("bash: agy: command not found\n"))

    def test_a_long_help_dump_is_still_a_banner(self):
        """A full `--help` blows the length bound, so structure carries it."""
        text = "Usage: agy [options] [prompt]\n\nOptions:\n" + (
            "".join(f"  --opt{i} <value>   option number {i} for the CLI\n" for i in range(30))
        )
        self.assertGreater(len(text), 600)
        self.assertIsNotNone(no_review_reason(text))

    def test_a_long_review_that_merely_names_a_flag_is_not(self):
        """Length alone must not flip a review into a banner, and vice versa."""
        text = "The --model flag is undocumented. " + ("Detail about the diff. " * 40)
        self.assertGreater(len(text), 600)
        self.assertIsNone(no_review_reason(text))


class TheFixturesAreRealInputs(unittest.TestCase):
    """Cheap guards against a fixture rotting into something vacuous."""

    def test_the_tiny_diff_is_a_diff(self):
        self.assertIn("diff --git", TINY_DIFF)
        self.assertIn("@@", TINY_DIFF)

    def test_the_agy_fixture_is_the_stream_json_envelope(self):
        frames = [
            json.loads(line) for line in fixture("agy.stdout.ndjson").splitlines() if line.strip()
        ]
        self.assertEqual(frames[-1]["event"], "result")
        self.assertIn("response", frames[-1]["result"])

    def test_every_fixture_is_referenced(self):
        used = {c["stdout_fixture"] for c in CONTRACTS.values()} | {
            "usage_echo.stdout",
            "refusal.stdout",
            "version_echo.stdout",
            "tiny.diff",
            "README.md",
        }
        on_disk = {p.name for p in FIXTURES.iterdir()}
        self.assertEqual(on_disk - used, set(), "unused contract fixture")


if __name__ == "__main__":
    unittest.main()
