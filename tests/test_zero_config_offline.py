"""The documented zero-config offline path exits 0 (#863).

`docs/cookbook.md` says that with no `jury.toml` and no agent CLI, but a local
model server, ``git diff main... | jury --diff-file -`` just works offline. It
exited 3: the fallback seats one local reviewer beside the built-in claude and
codex seats it found missing, and the default cross-vendor guard counted those
as a three-vendor claim that one local review could not meet.

Offline: nothing is on PATH, the model listing is mocked, the local seat speaks
through the mock adapter, and the HTTP seam is a spy that must stay unused.
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

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import adapters, cli, doctor  # noqa: E402

_SAMPLE_DIFF = (Path(__file__).parent.parent / "examples" / "sample.diff").read_bytes()

_LOCAL_SEAT_TOML = (
    '[jury]\nrounds = 1\nchair = "claude"\n\n'
    '[[agent]]\nname = "claude"\nvendor = "anthropic"\ncommand = "claude"\n\n'
    '[[agent]]\nname = "codex"\nvendor = "openai"\ncommand = "codex"\n\n'
    '[[agent]]\nname = "qwen"\nvendor = "local"\nmodel = "qwen2.5-coder:7b"\n'
)


def _local_review(self, prompt, phase="review", timeout=None, role_policy=None):
    """The local seat answers as the mock reviewer does, without a server."""
    return adapters.MockAdapter.run(self, prompt, phase, timeout, role_policy)


def _main(argv, config_text=None, models=("qwen2.5-coder:7b",), which=None):
    """``jury <argv>`` in an empty directory, the diff on stdin → (code, out, err, listings).

    Nothing is on PATH (or only what ``which`` resolves) and the local server
    lists ``models``; ``listings`` counts the requests made for that listing.
    """
    out, err = io.StringIO(), io.StringIO()
    stdin = io.TextIOWrapper(io.BytesIO(_SAMPLE_DIFF), encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp):
        if config_text is not None:
            Path("jury.toml").write_text(config_text, encoding="utf-8")
        with (
            mock.patch("shutil.which", which or (lambda _command: None)),
            mock.patch.object(adapters, "list_local_models", return_value=list(models)) as listed,
            mock.patch.object(doctor, "local_model_listing", return_value=list(models)),
            mock.patch.object(adapters.LocalAdapter, "available", return_value=True),
            mock.patch.object(adapters.LocalAdapter, "run", _local_review),
            mock.patch.object(adapters, "_open") as network,
            mock.patch("sys.stdin", stdin),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                code = cli.main(list(argv))
            except SystemExit as exc:
                code = exc.code
    assert network.call_count == 0, "a test reached the network"
    return code, out.getvalue(), err.getvalue(), listed.call_count


def _jury(*flags, config_text=None):
    """``git diff … | jury --diff-file - <flags>`` → (code, out, err)."""
    return _main(["--diff-file", "-", *flags], config_text)[:3]


def _doctor(*flags, config_text=None, models=("qwen2.5-coder:7b",)):
    """``jury --doctor --json <flags>`` → (export, listings)."""
    code, out, _, listings = _main(["--doctor", "--json", *flags], config_text, models)
    assert code == 0, code
    return json.loads(out), listings


class TheDocumentedOfflineCommandPasses(unittest.TestCase):
    def test_no_config_no_cli_and_a_local_model_exits_0(self):
        code, out, err = _jury()

        self.assertNotIn("panel collapsed", err)
        self.assertEqual(code, 0)
        self.assertIn("using local model 'qwen2.5-coder:7b'", err)
        self.assertIn("single-vendor panel", err)
        # The seats it could not run are still reported, not hidden.
        self.assertIn("skipped agents (never ran): claude", out)
        self.assertIn("| local | local | ok |", out)

    def test_a_threshold_named_on_the_command_line_is_still_enforced(self):
        code, _, err = _jury("--min-vendors", "2")

        self.assertEqual(code, 3)
        self.assertIn("panel collapsed: 1 vendor(s) contributed a review, 2 required", err)

    def test_a_config_that_names_three_vendors_still_fails_the_guard(self):
        """The scoping is the fallback's alone: a `jury.toml` claiming three vendors,
        of which only the local one can run, is the collapse the guard exists for."""
        code, _, err = _jury("--config", "jury.toml", config_text=_LOCAL_SEAT_TOML)

        self.assertNotIn("using local model", err)
        self.assertEqual(code, 3)
        self.assertIn("panel collapsed", err)

    def test_strict_still_refuses_the_missing_built_in_seats(self):
        """Unchanged by #863: the fallback keeps the built-in seats, so `--strict`
        still fails at startup on the first missing CLI, before any review."""
        code, out, err = _jury("--strict")

        self.assertEqual(code, 2)
        self.assertIn("error: agent 'claude' CLI not available: claude", err)
        self.assertNotIn("panel collapsed", err)
        self.assertEqual(out, "")


_NOTE = "- single local seat (zero-config fallback: no jury.toml and no agent CLI)"


class TheReportSaysItWasTheFallback(unittest.TestCase):
    """The report and the metadata say the panel was the fallback's seat (#863),
    so `--quiet`, which drops the stderr line, cannot hide why the run passed."""

    def test_the_markdown_report_says_so_under_quiet(self):
        code, out, err = _jury("--quiet")

        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn(_NOTE, out)

    def test_the_json_report_carries_the_field(self):
        code, out, _ = _jury("--quiet", "--format", "json")

        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual((doc["schema_version"], doc["metadata"]["schema_version"]), ("1.5", 8))
        self.assertIs(doc["metadata"]["panel"]["zero_config_fallback"], True)

    def test_the_metadata_file_carries_the_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "meta.json"
            code, _, _ = _jury("--quiet", "--metadata-json", str(path))
            meta = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertIs(meta["panel"]["zero_config_fallback"], True)

    def test_a_configured_panel_is_not_the_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "meta.json"
            _, out, _ = _jury(
                "--config",
                "jury.toml",
                "--metadata-json",
                str(path),
                config_text=_LOCAL_SEAT_TOML,
            )
            meta = json.loads(path.read_text(encoding="utf-8"))

        self.assertIs(meta["panel"]["zero_config_fallback"], False)
        self.assertNotIn(_NOTE, out)

    def test_the_log_line_names_a_threshold_you_named(self):
        _, _, default = _jury()
        _, _, named = _jury("--min-vendors", "2")

        self.assertIn("the default cross-vendor guard (min_vendors) does not apply", default)
        self.assertIn("single-vendor panel, held to the --min-vendors 2 you named", named)
        self.assertNotIn("does not apply", named)

    def test_the_log_line_says_an_opted_out_guard_is_off(self):
        _, _, off = _jury("--no-min-vendors")

        self.assertIn(
            "single-vendor panel, with the cross-vendor guard off (--no-min-vendors)", off
        )
        self.assertNotIn("--min-vendors 0", off)


_GUARD = "a run would fail the cross-vendor guard"


class TheDoctorPredictsTheFallbackRun(unittest.TestCase):
    """`jury --doctor` models the panel the zero-config run forms (#863)."""

    def test_no_config_no_cli_and_a_local_model_is_ready(self):
        export, listings = _doctor()

        panel = export["panel"]
        self.assertTrue(panel["multi_vendor_ready"])
        self.assertEqual((panel["vendors_configured"], panel["vendors_available"]), (1, 1))
        self.assertEqual(panel["panelists_available"], 1)
        self.assertEqual(panel["min_vendors"], 2)
        self.assertTrue(export["ready"])
        self.assertFalse([w for w in export["warnings"] if _GUARD in w])
        # One listing, shared by the prediction and the next steps.
        self.assertEqual(listings, 1)

    def test_the_text_report_names_the_panel_and_does_not_warn(self):
        _, out, _, _ = _main(["--doctor"])

        self.assertIn("panel of a run:    the local model 'qwen2.5-coder:7b' alone", out)
        self.assertIn(
            "cross-vendor ready: yes (the gate would not fail: one seat claims no "
            "cross-vendor consensus)",
            out,
        )
        self.assertIn("ready to run: yes", out)
        self.assertNotIn(_GUARD, out)

    def test_the_ready_reason_is_the_one_that_applies(self):
        """ "One seat claims no consensus" is the reason only when the default threshold
        was scoped away; a met or disabled threshold says so instead."""
        for flags, reason in (
            ((), "(the gate would not fail: one seat claims no cross-vendor consensus)"),
            (
                ("--min-vendors", "1"),
                "(the gate would not fail: 1 vendor(s) reachable, 1 required)",
            ),
            (("--no-min-vendors",), "(the gate is off)"),
        ):
            with self.subTest(flags=flags):
                _, out, _, _ = _main(["--doctor", *flags])
                self.assertIn(f"cross-vendor ready: yes {reason}", out)
                if flags:
                    self.assertNotIn("one seat claims", out)

    def test_a_named_threshold_is_predicted_to_fail(self):
        export, _ = _doctor("--min-vendors", "2")

        self.assertFalse(export["panel"]["multi_vendor_ready"])
        self.assertIn(
            "--min-vendors 2 asks for 2 vendors but only 1 is/are reachable; " + _GUARD,
            "\n".join(export["warnings"]),
        )

    def test_a_config_that_names_three_vendors_is_predicted_to_fail(self):
        export, _ = _doctor("--config", "jury.toml", config_text=_LOCAL_SEAT_TOML)

        self.assertFalse(export["panel"]["multi_vendor_ready"])
        self.assertEqual(export["panel"]["vendors_configured"], 3)
        self.assertIn(
            "3 vendors are enabled but only 1 is/are reachable; " + _GUARD,
            "\n".join(export["warnings"]),
        )

    def test_an_empty_server_seats_nothing_and_the_doctor_says_not_ready(self):
        export, listings = _doctor(models=())

        self.assertFalse(export["ready"])
        self.assertEqual(export["panel"]["vendors_configured"], 2)
        self.assertEqual(listings, 1)

    def test_an_agy_only_machine_is_told_why_its_cli_sat_out(self):
        """The run's fallback says why agy was not seated; so do the doctor's steps."""
        from ai_jury.config import AGY_OPT_IN_NOTE

        def only_agy(command):
            return "/usr/local/bin/agy" if command == "agy" else None

        _, out, _, _ = _main(["--doctor"], which=only_agy)

        self.assertIn("reviews with the local model 'qwen2.5-coder:7b' alone", out)
        self.assertIn(f"Note: {AGY_OPT_IN_NOTE}.", out)
        self.assertIn("ready to run: yes", out)

    def test_the_doctor_and_the_run_agree(self):
        """The prediction is the run's outcome, case by case: ready ⇔ not exit 3."""
        for flags, config in (
            ((), None),
            (("--min-vendors", "2"), None),
            (("--no-min-vendors",), None),
            (("--config", "jury.toml"), _LOCAL_SEAT_TOML),
            (("--config", "jury.toml", "--min-vendors", "1"), _LOCAL_SEAT_TOML),
        ):
            with self.subTest(flags=flags):
                export, _ = _doctor(*flags, config_text=config)
                code, _, _ = _jury(*flags, config_text=config)
                self.assertEqual(export["panel"]["multi_vendor_ready"], code != 3, code)


class TheFallbackReturnsTheSeatItAdded(unittest.TestCase):
    def test_the_seat_or_none(self):
        from ai_jury.config import load_config

        args = cli.build_parser().parse_args(["--diff-file", "-"])
        with (
            tempfile.TemporaryDirectory() as tmp,
            contextlib.chdir(tmp),
            mock.patch("shutil.which", return_value=None),
        ):
            with mock.patch.object(adapters, "list_local_models", return_value=["m:1b"]):
                config = load_config(None)
                seat = cli._maybe_add_local_fallback(config, args, lambda _m: None)
            with mock.patch.object(adapters, "list_local_models", return_value=[]):
                none = cli._maybe_add_local_fallback(load_config(None), args, print)

        self.assertIs(seat, config.agents[-1])
        self.assertEqual((seat.name, seat.vendor, seat.model), ("local", "local", "m:1b"))
        self.assertIsNone(none)


if __name__ == "__main__":
    unittest.main()
