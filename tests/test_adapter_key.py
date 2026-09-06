"""`adapter` names the protocol; `vendor` still names the identity (issue #705).

A seat used to have one key for two jobs. `vendor` selected the adapter that
builds the command line AND the identity the cross-vendor gate counts, so a real
bench could not be built out of Cursor's CLI, which fronts several vendors'
models through one command:

    [[agent]]
    name = "gpt"
    vendor = "openai"          # -> CodexAdapter -> `cursor-agent exec ...`
    command = "cursor-agent"

Every such seat died in half a second with `nonzero_exit`: `cursor-agent exec`
is not a command. The only adapter that passes `extra_args` through untouched
was reachable only as `vendor = "cli"` — which made all three seats the same
vendor at the gate. Run, or be counted; not both.

`adapter` separates them. It defaults to the vendor's shipped adapter, so a
configuration that names only `vendor` builds the same argv it always did —
asserted below over every shipped vendor, byte for byte, because "nothing else
moved" is the claim on which the rest of this change rests.

**A mismatched pair is not an error.** `vendor = "openai", adapter = "cli"` IS
the supported configuration this key exists for, and the tool has no way to know
which CLI fronts which vendor — that ignorance is why #705 exists. So a pair the
tool finds surprising is accepted silently, and `--doctor` prints both fields so
a reader can tell a Codex seat (openai/openai) from a GPT-through-Cursor seat
(openai/cli). What IS an error is an adapter name this build does not have: an
unknown *vendor* still names a seat that runs (it answers to `cli` at the gate),
while an unknown *adapter* names a protocol that does not exist, and guessing
one is exactly the failure above — paid for at run time, per seat.

The adapter vocabulary is the vendor vocabulary, because the registry is keyed
by vendor name: the claude protocol is `anthropic`, not `claude`. So the issue's
example `adapter = "claude"` is rejected for its spelling, not for its mismatch;
`vendor = "openai", adapter = "anthropic"` is the same pair spelled in the
vocabulary the file already uses for `vendor`, and it is accepted.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_jury import adapters, cli, doctor  # noqa: E402
from ai_jury import config as config_module  # noqa: E402
from ai_jury.adapters import _VENDOR_ADAPTERS, make_adapter  # noqa: E402
from ai_jury.config import (  # noqa: E402
    KNOWN_VENDORS,
    AgentSpec,
    ConfigError,
    _from_dict,
    adapter_key,
    config_hash,
    is_recognised_adapter,
    load_config,
    recognised_adapters,
    spec_adapter,
    validate_config,
)
from ai_jury.doctor import doctor_report_dict  # noqa: E402
from ai_jury.privilege import audit_agent  # noqa: E402

CONTRACTS = json.loads(
    (Path(__file__).resolve().parent / "golden" / "adapter_contracts.json").read_text(
        encoding="utf-8"
    )
)
CONTRACTS.pop("_comment", None)

_DIFF = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
 import os
+os.system(input())
"""

#: The `extra_args` from the issue: the real `cursor-agent` invocation.
_CURSOR_ARGS = ["-p", "--model", "gpt-5.3-codex-high", "--force", "--output-format", "text"]

#: The configuration from #705, verbatim in shape: a GPT seat driven through
#: Cursor's CLI, which now says what it is *and* how it is invoked.
_ONE_CURSOR_SEAT = """
[jury]
rounds = 1
chair = "gpt"
verify = false

[[agent]]
name = "gpt"
vendor = "openai"
adapter = "cli"
command = "cursor-agent"
extra_args = ["-p", "--model", "gpt-5.3-codex-high", "--force", "--output-format", "text"]
"""

#: The bench #705 could not build: three vendors, one CLI.
_THREE_CURSOR_SEATS = """
[jury]
rounds = 1
chair = "gpt"
verify = false

[jury.ci]
min_vendors = 2

[[agent]]
name = "gpt"
vendor = "openai"
adapter = "cli"
command = "cursor-agent"
extra_args = ["-p", "--model", "gpt-5.3-codex-high"]

[[agent]]
name = "gemini"
vendor = "google"
adapter = "cli"
command = "cursor-agent"
extra_args = ["-p", "--model", "gemini-3-pro"]

[[agent]]
name = "grok"
vendor = "xai"
adapter = "cli"
command = "cursor-agent"
extra_args = ["-p", "--model", "grok-4.6"]
"""


def _jury(config_toml: str, argv: list[str] | None = None):
    """A full `--mock` run over a config file. Returns (exit code, stderr, metadata)."""
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "jury.toml"
        config.write_text(config_toml, encoding="utf-8")
        diff = Path(tmp) / "changes.diff"
        diff.write_text(_DIFF, encoding="utf-8")
        meta = Path(tmp) / "meta.json"
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(
                [
                    "--mock",
                    "--diff-file",
                    str(diff),
                    "--config",
                    str(config),
                    "--metadata-json",
                    str(meta),
                    *(argv or []),
                ]
            )
        metadata = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
    return code, err.getvalue(), metadata


def _ballots(config_toml: str) -> list[dict]:
    """The `--format keel-reviews` bundle: one entry per panel ballot."""
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "jury.toml"
        config.write_text(config_toml, encoding="utf-8")
        diff = Path(tmp) / "changes.diff"
        diff.write_text(_DIFF, encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            cli.main(
                [
                    "--mock",
                    "--diff-file",
                    str(diff),
                    "--config",
                    str(config),
                    "--format",
                    "keel-reviews",
                ]
            )
    return json.loads(out.getvalue())


def _argv_for(spec: AgentSpec) -> list[str]:
    """The argv a real run would execute for *spec* (availability is not the point)."""
    with mock.patch.object(adapters.shutil, "which", lambda cmd: f"/bin/{cmd}"):
        return make_adapter(spec).build_argv("PROMPT")


class TheCursorFrontedGptSeatRuns(unittest.TestCase):
    """Acceptance criterion 1: the configuration from the issue runs, as openai."""

    def test_the_argv_is_the_pass_through_cursor_agent_understands(self):
        """`cursor-agent exec` is what killed it; the adapter must add nothing."""
        spec = AgentSpec(
            name="gpt",
            vendor="openai",
            adapter="cli",
            command="cursor-agent",
            extra_args=list(_CURSOR_ARGS),
        )
        self.assertEqual(_argv_for(spec), ["cursor-agent", *_CURSOR_ARGS])

    def test_no_codex_sandbox_flag_is_spliced_into_an_unrelated_binary(self):
        """`-s read-only` belongs to codex, not to whoever answers as openai."""
        spec = AgentSpec(
            name="gpt", vendor="openai", adapter="cli", command="cursor-agent", extra_args=[]
        )
        self.assertEqual(_argv_for(spec), ["cursor-agent"])

    def test_the_same_seat_without_the_adapter_key_is_still_codex(self):
        """The defect, pinned: this is what #705 was reported against."""
        spec = AgentSpec(
            name="gpt", vendor="openai", command="cursor-agent", extra_args=list(_CURSOR_ARGS)
        )
        self.assertEqual(_argv_for(spec)[:2], ["cursor-agent", "exec"])

    def test_the_run_succeeds_and_the_ballot_carries_vendor_openai(self):
        code, err, meta = _jury(_ONE_CURSOR_SEAT)
        self.assertEqual(code, 0, err)
        self.assertEqual({a["name"]: a["vendor"] for a in meta["agents"]}, {"gpt": "openai"})

    def test_the_keel_reviews_bundle_says_openai_too(self):
        ballots = _ballots(_ONE_CURSOR_SEAT)
        vendors = {b["reviewer"]: b["vendor"] for b in ballots if b.get("vendor")}
        self.assertEqual(vendors.get("gpt"), "openai")

    def test_the_audit_reads_the_seat_as_the_bring_your_own_cli_it_is(self):
        """The privilege audit follows the adapter for the same reason argv does.

        It says of this seat exactly what it says of the identical `cli` seat —
        no `-s read-only` demanded of a binary that has no such flag — because
        the sandbox surface is a property of the CLI, not of whose model answers.
        """
        kwargs = {"command": "cursor-agent", "extra_args": list(_CURSOR_ARGS)}
        fronted = AgentSpec(name="gpt", vendor="openai", adapter="cli", **kwargs)
        generic = AgentSpec(name="gpt", vendor="cli", **kwargs)
        self.assertEqual(audit_agent(fronted), audit_agent(generic))


class ThreeCursorFrontedSeatsAreThreeVendors(unittest.TestCase):
    """Acceptance criterion 2: one CLI, three vendors, `min_vendors = 2` satisfied."""

    def test_the_panel_counts_three_vendors_and_passes_the_gate(self):
        code, err, meta = _jury(_THREE_CURSOR_SEATS)
        self.assertEqual(code, 0, err)
        self.assertNotIn("panel collapsed", err)
        self.assertEqual(meta["panel"]["vendors"], 3)

    def test_it_produces_three_ballots(self):
        """Three, plus the chair's synthesis record, which is not a ballot (#699)."""
        ballots = _ballots(_THREE_CURSOR_SEATS)
        seats = [b for b in ballots if b["reviewer"] != "chair"]
        self.assertEqual(sorted(b["reviewer"] for b in seats), ["gemini", "gpt", "grok"])
        self.assertEqual(
            {b["reviewer"]: b["vendor"] for b in seats},
            {"gpt": "openai", "gemini": "google", "grok": "xai"},
        )

    def test_every_seat_keeps_its_own_vendor(self):
        _code, _err, meta = _jury(_THREE_CURSOR_SEATS)
        self.assertEqual(
            {a["name"]: a["vendor"] for a in meta["agents"]},
            {"gpt": "openai", "gemini": "google", "grok": "xai"},
        )

    def test_the_same_bench_without_the_adapter_key_is_the_old_dilemma(self):
        """Three `cli` seats run and are one vendor — the other horn of #705."""
        collapsed = _THREE_CURSOR_SEATS.replace('vendor = "openai"', 'vendor = "cli"')
        collapsed = collapsed.replace('vendor = "google"', 'vendor = "cli"')
        collapsed = collapsed.replace('vendor = "xai"', 'vendor = "cli"')
        _code, _err, meta = _jury(collapsed)
        self.assertEqual(meta["panel"]["vendors"], 1)


def _protocol_fingerprint(spec: AgentSpec) -> tuple:
    """Everything about *spec* that decides how it is invoked.

    Not just argv: the adapter class, the write-mode argv (`jury run-agent
    --allow-write`), and the stdin encoding are all part of "how this seat is
    invoked", and a change to any of them would be a behaviour change smuggled
    past an argv-only assertion. HTTP adapters have no argv at all, so a raised
    ``NotImplementedError`` is recorded as itself and compared like any other
    answer.
    """

    def _call(fn, *args):
        try:
            return ("ok", fn(*args))
        except Exception as exc:  # noqa: BLE001 - the exception IS the answer here
            return ("raised", type(exc).__name__)

    with mock.patch.object(adapters.shutil, "which", lambda cmd: f"/bin/{cmd}"):
        adapter = make_adapter(spec)
        return (
            type(adapter).__name__,
            _call(adapter.build_argv, "PROMPT"),
            _call(adapter.build_write_argv, "PROMPT"),
            _call(adapter._stdin_for, "PROMPT"),
        )


class NamingTheVendorsOwnAdapterChangesNothing(unittest.TestCase):
    """Acceptance criterion 3, the load-bearing one: existing configs are unmoved.

    `adapter` defaults to the vendor, so for every shipped vendor the seat that
    omits the key and the seat that names its own vendor must be the same seat —
    byte for byte in argv, and in everything else that decides an invocation.
    """

    def _spec(self, vendor: str, **kw) -> AgentSpec:
        return AgentSpec(
            name=f"seat-{vendor}",
            vendor=vendor,
            command="some-cli",
            model="a-model",
            extra_args=["--flag", "value"],
            **kw,
        )

    def test_every_shipped_vendor_builds_an_identical_invocation(self):
        self.assertEqual(set(_VENDOR_ADAPTERS), set(KNOWN_VENDORS))
        for vendor in sorted(KNOWN_VENDORS):
            with self.subTest(vendor=vendor):
                self.assertEqual(
                    _protocol_fingerprint(self._spec(vendor)),
                    _protocol_fingerprint(self._spec(vendor, adapter=vendor)),
                )

    def test_the_locked_adapter_contracts_are_unchanged_by_naming_the_adapter(self):
        """The hand-maintained argv goldens, re-asserted with the key set."""
        for name, contract in CONTRACTS.items():
            with self.subTest(contract=name):
                spec = AgentSpec(
                    name=name,
                    vendor=contract["vendor"],
                    adapter=contract["vendor"],
                    command=contract["command"],
                    model=contract["model"],
                    extra_args=list(contract["extra_args"]),
                    **(
                        {"prompt_mode": contract["prompt_mode"]}
                        if contract.get("prompt_mode")
                        else {}
                    ),
                )
                expected = [a if a != "<PROMPT>" else "PROMPT" for a in contract["argv"]]
                self.assertEqual(_argv_for(spec), expected)

    def test_the_cache_key_of_an_existing_config_is_untouched(self):
        """No one's review cache is invalidated by a key they did not set.

        The adapter reaches `config_hash` only when it DIFFERS from the vendor,
        so the canonical payload of every configuration written before #705 is
        byte-identical to what it hashed to before.
        """
        base = {
            "jury": {"rounds": 1},
            "agent": [{"name": "codex", "vendor": "openai", "command": "codex"}],
        }
        named = {
            "jury": {"rounds": 1},
            "agent": [
                {"name": "codex", "vendor": "openai", "adapter": "openai", "command": "codex"}
            ],
        }
        differs = {
            "jury": {"rounds": 1},
            "agent": [{"name": "codex", "vendor": "openai", "adapter": "cli", "command": "codex"}],
        }
        self.assertEqual(config_hash(_from_dict(base)), config_hash(_from_dict(named)))
        self.assertNotEqual(config_hash(_from_dict(base)), config_hash(_from_dict(differs)))

    def test_a_config_that_names_no_adapter_leaves_the_field_unset(self):
        agents = load_config(None).agents
        self.assertEqual([a.adapter for a in agents], [None] * len(agents))
        self.assertEqual([a.adapter_key for a in agents], [a.vendor for a in agents])


class AMismatchedPairIsUnusualNotInvalid(unittest.TestCase):
    """The decision #705 asks for, written down as behaviour.

    Accepted, silently. `vendor = "openai", adapter = "cli"` is the supported
    configuration this key exists for; any rule that flagged "the adapter is not
    the vendor's own" would fire on it. And the tool cannot tell a sensible pair
    from a silly one without knowing which CLI fronts which vendor — the
    knowledge whose absence is the issue.
    """

    _PAIR = """
[jury]
rounds = 1
chair = "odd"
verify = false

[[agent]]
name = "odd"
vendor = "openai"
adapter = "anthropic"
command = "claude"
"""

    def test_validation_accepts_it_without_a_warning(self):
        data = {
            "jury": {"rounds": 1, "chair": "odd"},
            "agent": [
                {"name": "odd", "vendor": "openai", "adapter": "anthropic", "command": "claude"}
            ],
        }
        self.assertEqual(validate_config(data), [])
        # Strict mode is the same answer, louder: it must not raise either.
        self.assertEqual(validate_config(data, strict=True), [])

    def test_it_is_invoked_as_the_adapter_and_counted_as_the_vendor(self):
        spec = AgentSpec(name="odd", vendor="openai", adapter="anthropic", command="claude")
        self.assertEqual(_argv_for(spec)[:2], ["claude", "-p"])
        _code, _err, meta = _jury(self._PAIR)
        self.assertEqual(meta["agents"][0]["vendor"], "openai")


class AnUnknownAdapterIsRejectedBeforeTheRun(unittest.TestCase):
    """An unknown vendor degrades; an unknown adapter cannot.

    There is nothing to fall back to except a guess, and the guess is what #705
    is about — `codex exec` onto `cursor-agent`, discovered half a second into a
    run that had already been paid for. Named up front, like a bad `effort`.
    """

    def _validate(self, adapter):
        return validate_config(
            {
                "jury": {"rounds": 1, "chair": "seat"},
                "agent": [
                    {"name": "seat", "vendor": "openai", "adapter": adapter, "command": "codex"}
                ],
            }
        )

    def test_a_typo_is_a_hard_error_naming_the_vocabulary(self):
        with self.assertRaises(ConfigError) as caught:
            self._validate("codex-cli")
        message = str(caught.exception)
        self.assertIn("unknown adapter 'codex-cli'", message)
        self.assertIn("cli", message)

    def test_the_cli_name_of_a_vendor_is_not_the_adapter_name(self):
        """The vocabulary is the vendor vocabulary: `anthropic`, not `claude`."""
        with self.assertRaises(ConfigError):
            self._validate("claude")
        self.assertFalse(is_recognised_adapter("claude"))
        self.assertTrue(is_recognised_adapter("anthropic"))

    def test_a_non_string_adapter_is_rejected_rather_than_ignored(self):
        with self.assertRaises(ConfigError):
            self._validate(7)

    def test_an_unknown_vendor_is_still_only_a_warning(self):
        """The asymmetry is deliberate; this pins it."""
        warnings = validate_config(
            {
                "jury": {"rounds": 1, "chair": "seat"},
                "agent": [{"name": "seat", "vendor": "grok-cli", "command": "cursor-agent"}],
            }
        )
        self.assertTrue(any("unknown vendor" in w for w in warnings))


#: The reviewer's bench for #708, reproduced without mocks: three vendors, one
#: adapter name this build does not have, and a `command` that exists on every
#: machine so nothing about PATH is in play. Every seat is `[available]` if the
#: doctor is allowed to reach the availability probe at all.
_UNKNOWN_ADAPTER_BENCH = """
[jury]
rounds = 1
chair = "gpt"
verify = false

[jury.ci]
min_vendors = 2

[[agent]]
name = "gpt"
vendor = "openai"
adapter = "claude"
command = "ls"

[[agent]]
name = "gemini"
vendor = "google"
adapter = "claude"
command = "ls"

[[agent]]
name = "grok"
vendor = "xai"
adapter = "claude"
command = "ls"
"""

#: The same bench, spelled in the vocabulary the file already uses for `vendor`
#: (`cli`, the pass-through — the claude protocol would be `anthropic`). The
#: run accepts it, so the doctor must call it ready: the equality below has to
#: hold in BOTH directions or it is satisfied by a doctor that says no to
#: everything.
_KNOWN_ADAPTER_BENCH = _UNKNOWN_ADAPTER_BENCH.replace('adapter = "claude"', 'adapter = "cli"')


def _run_accepts(config_toml: str) -> bool:
    """Whether a real (mocked-agent) run accepts this config file at all."""
    code, _err, _meta = _jury(config_toml)
    return code == 0


def _doctor(config_toml: str):
    """``build_diagnostics`` over this config, with availability forced on.

    Forcing availability removes the only variable that is not the config: what
    is under test is whether the doctor accepts the FILE, so every seat that
    reaches the probe must come back reachable. On the pre-#708 code all three
    did — that is the finding.
    """
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "jury.toml"
        config.write_text(config_toml, encoding="utf-8")
        with mock.patch.object(doctor, "_is_available", return_value=True):
            return doctor.build_diagnostics(str(config))


class TheDoctorsReadinessEqualsTheRunsAcceptance(unittest.TestCase):
    """Issue #708: `--doctor` called ready a bench the run refuses.

    `build_diagnostics` loaded the config with validation OFF, so an `adapter`
    this build does not have survived into the specs, `make_adapter` missed the
    registry and fell through to `GenericCLIAdapter`, and the report printed
    three `[available]` rows, `cross-vendor ready: yes` and `ready to run: yes`
    for a file `jury` itself refuses with a hard config error. `docs/
    configuration.md` promises the doctor's arithmetic equals what a run counts;
    this class is that promise, asserted in both directions.

    Fixed at the seam rather than in the report: the doctor validates the config
    the way a run does, AND `make_adapter` refuses a named adapter it does not
    have instead of guessing one. Either alone leaves a reader that disagrees —
    `jury run-agent` also loads without validating.
    """

    def test_a_config_the_run_refuses_is_not_ready(self):
        self.assertFalse(_run_accepts(_UNKNOWN_ADAPTER_BENCH))
        self.assertFalse(_doctor(_UNKNOWN_ADAPTER_BENCH)["recommendations"]["ready"])

    def test_a_config_the_run_accepts_is_ready(self):
        """The other direction: the fix must not be "say no to everything"."""
        self.assertTrue(_run_accepts(_KNOWN_ADAPTER_BENCH))
        self.assertTrue(_doctor(_KNOWN_ADAPTER_BENCH)["recommendations"]["ready"])

    def test_readiness_equals_acceptance_for_both_benches(self):
        for bench, label in (
            (_UNKNOWN_ADAPTER_BENCH, "unknown adapter"),
            (_KNOWN_ADAPTER_BENCH, "known adapter"),
        ):
            with self.subTest(bench=label):
                self.assertEqual(
                    _doctor(bench)["recommendations"]["ready"], _run_accepts(bench), label
                )

    def test_the_verdict_names_the_seat_and_the_unknown_adapter(self):
        diagnostics = _doctor(_UNKNOWN_ADAPTER_BENCH)
        warnings = "\n".join(diagnostics["config_warnings"])
        self.assertIn("unknown adapter 'claude'", warnings)
        self.assertIn("agent 'gpt'", warnings)
        self.assertIsNone(diagnostics["config"])
        self.assertEqual(diagnostics["agents"], [])

    def test_no_seat_is_reported_available_and_the_gate_is_not_reported_met(self):
        """The three claims the reviewer read off the report, all reversed."""
        report = doctor.render_report(_doctor(_UNKNOWN_ADAPTER_BENCH))
        self.assertNotIn("[available]", report)
        self.assertIn("cross-vendor ready: no", report)
        self.assertIn("ready to run: no", report)

    def test_the_json_export_agrees_with_the_text_report(self):
        exported = doctor_report_dict(_doctor(_UNKNOWN_ADAPTER_BENCH))
        self.assertEqual(exported["agents"], [])
        self.assertFalse(exported["panel"]["multi_vendor_ready"])

    def test_make_adapter_refuses_the_name_instead_of_guessing_one(self):
        """The other half of the seam: the fall-through WAS the silent guess."""
        spec = AgentSpec(name="gpt", vendor="openai", adapter="claude", command="ls")
        with self.assertRaises(ConfigError) as caught:
            make_adapter(spec)
        self.assertIn("unknown adapter 'claude'", str(caught.exception))
        self.assertIn("agent 'gpt'", str(caught.exception))

    def test_make_adapter_still_falls_through_for_an_unnamed_adapter(self):
        """An unknown VENDOR named no protocol, so inheriting the generic one
        stays the documented behaviour — the refusal is only for a named one."""
        spec = AgentSpec(name="seat", vendor="grok-cli", command="cursor-agent")
        self.assertIsInstance(make_adapter(spec), adapters.GenericCLIAdapter)

    def test_run_agent_refuses_the_same_name_it_loads_without_validating(self):
        """`jury run-agent` drives one seat and does not validate the file, so
        the refusal has to live where the adapter is built, not only at load."""
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "jury.toml"
            config.write_text(_UNKNOWN_ADAPTER_BENCH, encoding="utf-8")
            prompt = Path(tmp) / "prompt.txt"
            prompt.write_text("review this", encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(
                    [
                        "run-agent",
                        "--role",
                        "review",
                        "--agent",
                        "gpt",
                        "--config",
                        str(config),
                        "--prompt-file",
                        str(prompt),
                    ]
                )
        self.assertEqual(code, 2, err.getvalue())
        self.assertIn("unknown adapter 'claude'", err.getvalue())

    def test_an_adapter_that_normalises_to_nothing_is_refused_on_every_path(self):
        """`adapter = 7` and `adapter = "   "` used to normalise to `None` — read as
        "unset", so `run-agent` built the vendor's adapter and ran while a full run
        and `--doctor` refused the same file. The refusal is now at construction."""
        for raw in (7, "   ", ""):
            with self.subTest(adapter=repr(raw)), self.assertRaises(ConfigError):
                AgentSpec(name="gpt", vendor="openai", command="ls", adapter=raw)
        with tempfile.TemporaryDirectory() as tmp:
            for raw in ("7", '"   "'):
                config = Path(tmp) / "jury.toml"
                config.write_text(
                    _UNKNOWN_ADAPTER_BENCH.replace('adapter = "claude"', f"adapter = {raw}"),
                    encoding="utf-8",
                )
                prompt = Path(tmp) / "prompt.txt"
                prompt.write_text("review this", encoding="utf-8")
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = cli.main(
                        [
                            "run-agent",
                            "--role",
                            "review",
                            "--agent",
                            "gpt",
                            "--config",
                            str(config),
                            "--prompt-file",
                            str(prompt),
                        ]
                    )
                with self.subTest(adapter=raw, path="run-agent"):
                    self.assertEqual(code, 2, err.getvalue())
                    self.assertIn("adapter", err.getvalue())


class TheVocabularyIsOneVocabulary(unittest.TestCase):
    def test_adapters_and_vendors_are_the_same_names(self):
        """One vocabulary, derived — not a second list to keep in step."""
        self.assertEqual(set(recognised_adapters()), set(_VENDOR_ADAPTERS))
        self.assertEqual(set(recognised_adapters()), set(KNOWN_VENDORS))

    def test_a_registered_adapter_is_selectable_as_an_adapter(self):
        class ShimAdapter(adapters.GenericCLIAdapter):
            pass

        registered = "shim-protocol-705"
        try:
            adapters.register_adapter(registered, ShimAdapter)
            self.assertTrue(is_recognised_adapter(registered))
            spec = AgentSpec(name="s", vendor="openai", adapter=registered, command="shim")
            with mock.patch.object(adapters.shutil, "which", lambda cmd: f"/bin/{cmd}"):
                self.assertIsInstance(make_adapter(spec), ShimAdapter)
        finally:
            _VENDOR_ADAPTERS.pop(registered, None)
            config_module._REGISTERED_VENDORS.discard(registered)

    def test_the_key_is_normalised_like_the_vendor(self):
        spec = AgentSpec(name="s", vendor="openai", adapter="  CLI  ", command="cursor-agent")
        self.assertEqual(spec.adapter, "cli")
        self.assertEqual(spec.adapter_key, "cli")

    def test_only_an_absent_adapter_falls_back_to_the_vendor(self):
        """This used to assert that `""`, `"   "` and `3` fall back too. That was
        the defect a reviewer found on #708: `validate_config` called those an
        unknown adapter while the spec read them as unset, so `run-agent` — which
        builds seats without validating — ran what a full run refused. Only the
        key's absence means "the vendor's adapter" now."""
        spec = AgentSpec(name="s", vendor="openai", adapter=None, command="codex")
        self.assertIsNone(spec.adapter)
        self.assertEqual(spec.adapter_key, "openai")
        for value in ("", "   ", 3):
            with self.subTest(value=value):
                with self.assertRaises(ConfigError) as caught:
                    AgentSpec(name="s", vendor="openai", adapter=value, command="codex")
                self.assertIn("agent 's'", str(caught.exception))
                self.assertNotIn("agent 'agent", str(caught.exception))

    def test_the_helpers_agree_about_the_fallback(self):
        spec = AgentSpec(name="s", vendor="openai", adapter="cli", command="cursor-agent")
        self.assertEqual(spec_adapter(spec), "cli")
        self.assertEqual(adapter_key("openai", "cli"), "cli")
        self.assertEqual(adapter_key("openai", None), "openai")
        self.assertEqual(spec_adapter(object()), "")


class TheDoctorPrintsBothFields(unittest.TestCase):
    """A reader must be able to tell a Codex seat from a GPT-through-Cursor seat."""

    @contextlib.contextmanager
    def _diagnostics(self, text):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(doctor, "_is_available", lambda _spec: True),
            mock.patch.object(doctor, "_probe_models", return_value=None),
        ):
            path = Path(tmp) / "jury.toml"
            path.write_text(text, encoding="utf-8")
            yield doctor.build_diagnostics(str(path))

    def test_the_json_export_carries_vendor_and_adapter(self):
        with self._diagnostics(_ONE_CURSOR_SEAT) as diag:
            report = doctor_report_dict(diag)
        seat = report["agents"][0]
        self.assertEqual((seat["vendor"], seat["adapter"]), ("openai", "cli"))

    def test_the_text_report_prints_both_on_the_seat_row(self):
        with self._diagnostics(_ONE_CURSOR_SEAT) as diag:
            text = doctor.render_report(diag)
        self.assertIn("vendor=openai", text)
        self.assertIn("adapter=cli", text)

    def test_a_plain_seat_shows_its_vendors_own_adapter(self):
        plain = """
[jury]
chair = "codex"

[[agent]]
name = "codex"
vendor = "openai"
command = "codex"
"""
        with self._diagnostics(plain) as diag:
            report = doctor_report_dict(diag)
            text = doctor.render_report(diag)
        self.assertEqual(report["agents"][0]["adapter"], "openai")
        self.assertIn("adapter=openai", text)

    def test_the_transport_follows_the_adapter(self):
        """A seat reached over a CLI is `cli`, whatever vendor answers."""
        with self._diagnostics(_ONE_CURSOR_SEAT) as diag:
            report = doctor_report_dict(diag)
        self.assertEqual(report["agents"][0]["transport"], "cli")


class TheConfigListingNamesTheAdapterOnlyWhenItDiffers(unittest.TestCase):
    def _config_output(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jury.toml"
            path.write_text(text, encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cli.main(["config", "show", "--config", str(path)])
        return out.getvalue()

    def test_a_cursor_fronted_seat_says_via_cli(self):
        self.assertIn("gpt (openai via cli)", self._config_output(_ONE_CURSOR_SEAT))

    def test_an_ordinary_seat_keeps_its_line(self):
        plain = """
[jury]
chair = "codex"

[[agent]]
name = "codex"
vendor = "openai"
command = "codex"
"""
        self.assertIn("codex (openai) →", self._config_output(plain))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
