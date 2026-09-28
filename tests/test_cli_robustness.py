"""Four CLI robustness fixes: #864, #865, #866 and #893.

Each fails as an assertion on the code before its fix: a crash is caught by
``_run`` and returned as the "exit code", so a traceback reads as a wrong code
rather than as a test error. Offline: the local-model listing and the agent
probes are mocked, and review seats are the mock adapter.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import tomllib
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import adapters, cli, doctor, scaffold  # noqa: E402
from ai_jury.config import config_hash, load_config  # noqa: E402

_SAMPLE_DIFF = Path(__file__).parent.parent / "examples" / "sample.diff"
_NOTHING_AVAILABLE = mock.patch.object(cli, "_init_available", return_value={})


def _run(argv, stdin=""):
    """``cli.main(argv)`` → (code, stdout, stderr); an escaped exception is the code."""
    out, err = io.StringIO(), io.StringIO()
    with (
        mock.patch("sys.stdin", stdin if not isinstance(stdin, str) else io.StringIO(stdin)),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        try:
            code = cli.main(argv)
        except SystemExit as exc:
            code = exc.code
        except Exception as exc:  # noqa: BLE001 - a traceback is the failure measured
            code = exc
    return code, out.getvalue(), err.getvalue()


class PlainInitPicksAListedLocalModel(unittest.TestCase):
    """#864: plain `jury init` names a model the local server has, or none."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def _init(self, agents, listing, *extra):
        path = self.d / f"{agents.replace(',', '-')}.toml"
        with (
            _NOTHING_AVAILABLE,
            mock.patch("ai_jury.adapters.list_local_models", return_value=listing) as listed,
        ):
            code, _, err = _run(["init", "--agents", agents, "-o", str(path), "--force", *extra])
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        return code, err, path, text, listed

    def test_the_seat_names_the_model_the_server_lists(self):
        code, _, path, _, _ = self._init("claude,qwen", ["llama3:8b", "qwen2.5-coder:14b"])

        self.assertEqual(code, 0)
        seats = {a["name"]: a for a in tomllib.loads(path.read_text("utf-8"))["agent"]}
        # pick_default_model's choice — a code model — not the template's 7b.
        self.assertEqual(seats["qwen"]["model"], "qwen2.5-coder:14b")

    def test_an_empty_server_leaves_the_local_seat_commented_out(self):
        code, err, path, text, _ = self._init("qwen,claude,codex", [])

        self.assertEqual(code, 0)
        data = tomllib.loads(text)
        self.assertEqual([a["name"] for a in data["agent"]], ["claude", "codex"])
        # The chair defaulted to the first seat, and the seat left out is not it.
        self.assertEqual(data["jury"]["chair"], "claude")
        self.assertIn('# [[agent]]\n# name = "qwen"\n# vendor = "local"', text)
        self.assertIn("ollama pull qwen2.5-coder:7b", text)
        self.assertIn("--local-model", text)
        self.assertIn("'qwen' is written commented out", err)
        load_config(str(path), validate=True)

    def test_the_commented_seat_leaves_the_config_hash_of_the_panel_that_runs(self):
        _, _, with_seat, _, _ = self._init("claude,codex,qwen", [])
        _, _, without, _, _ = self._init("claude,codex", [])

        self.assertEqual(
            config_hash(load_config(str(with_seat))), config_hash(load_config(str(without)))
        )

    def test_uncommenting_the_block_gives_a_valid_local_seat(self):
        _, _, _, text, _ = self._init("claude,qwen", [])

        uncommented = text.replace("# [[agent]]", "[[agent]]")
        for key in ("name", "vendor", "endpoint", "model"):
            uncommented = uncommented.replace(f"# {key} = ", f"{key} = ")
        seats = tomllib.loads(uncommented)["agent"]
        self.assertEqual(seats[-1]["model"], "qwen2.5-coder:7b")

    def test_a_named_model_is_written_without_asking_the_server(self):
        code, _, path, _, listed = self._init("claude,qwen", [], "--local-model", "m:1b")

        self.assertEqual(code, 0)
        listed.assert_not_called()
        seats = {a["name"]: a for a in tomllib.loads(path.read_text("utf-8"))["agent"]}
        self.assertEqual(seats["qwen"]["model"], "m:1b")

    def test_a_panel_without_a_local_seat_never_asks_the_server(self):
        code, _, _, text, listed = self._init("claude,codex", ["qwen2.5-coder:7b"])

        self.assertEqual(code, 0)
        listed.assert_not_called()
        self.assertNotIn("# [[agent]]", text)

    def test_a_named_chair_on_the_left_out_seat_is_refused_before_writing(self):
        """Neither a chair over a commented seat nor a silently different chair."""
        code, err, path, _, _ = self._init("claude,qwen", [], "--chair", "qwen")

        self.assertEqual(code, 2)
        self.assertIn("error: the chair qwen is the local seat, but the server", err)
        self.assertIn("pass --local-model <model> or choose another --chair", err)
        self.assertFalse(path.exists(), "nothing may be written")

    def test_a_named_chair_on_a_seat_that_stays_is_kept(self):
        code, _, path, _, _ = self._init("qwen,claude,codex", [], "--chair", "codex")

        self.assertEqual(code, 0)
        self.assertEqual(tomllib.loads(path.read_text("utf-8"))["jury"]["chair"], "codex")

    def test_a_local_only_panel_keeps_its_seat(self):
        """A config needs a seat, so the only one stays, on the model the pull hint names."""
        code, _, path, text, _ = self._init("qwen", [])

        self.assertEqual(code, 0)
        self.assertEqual(tomllib.loads(text)["agent"][0]["model"], "qwen2.5-coder:7b")
        self.assertNotIn("# [[agent]]", text)
        load_config(str(path), validate=True)


class SeatLocalAgentsTests(unittest.TestCase):
    """The pure half of #864."""

    def test_a_listed_model_seats_everyone(self):
        self.assertEqual(
            scaffold.seat_local_agents(["claude", "qwen"], ["gemma:2b", "deepseek-coder"]),
            (["claude", "qwen"], "deepseek-coder", []),
        )

    def test_no_model_moves_the_local_seat_out_once(self):
        self.assertEqual(
            scaffold.seat_local_agents(["qwen", "claude", "qwen"], []),
            (["claude"], None, ["qwen"]),
        )

    def test_no_model_and_no_other_seat_keeps_it(self):
        self.assertEqual(scaffold.seat_local_agents(["qwen"], []), (["qwen"], None, []))

    def test_no_local_seat_is_left_alone(self):
        self.assertEqual(
            scaffold.seat_local_agents(["claude", "nope"], []), (["claude", "nope"], None, [])
        )

    def test_commented_agents_parse_to_nothing(self):
        config = scaffold.build_config(["claude"])
        seat = scaffold.build_config(["qwen"])["agent"]
        plain = scaffold.render_toml(config)
        commented = scaffold.render_toml(config, commented_agents=seat)

        self.assertTrue(commented.startswith(plain.rstrip("\n")))
        self.assertEqual(tomllib.loads(commented), tomllib.loads(plain))


class WizardNeedsATerminal(unittest.TestCase):
    """#865: `jury init --wizard` with no terminal exits 2 instead of an EOFError."""

    def test_a_pipe_is_refused_with_a_usage_error(self):
        out = Path(tempfile.mkdtemp()) / "jury.toml"
        with _NOTHING_AVAILABLE as probes:
            code, _, err = _run(["init", "--wizard", "-o", str(out)], stdin="")

        self.assertEqual(code, 2)
        self.assertIn("error: the wizard needs a terminal; use `jury init --preset <name>`", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(out.exists())
        # Refused up front: no agent was probed for a wizard that cannot run.
        probes.assert_not_called()

    def test_no_stdin_at_all_is_refused_too(self):
        with _NOTHING_AVAILABLE:
            code, _, err = _run(["init", "--wizard"], stdin=None)

        self.assertEqual(code, 2)
        self.assertIn("the wizard needs a terminal", err)

    def test_a_listing_still_answers_without_a_terminal(self):
        with mock.patch("ai_jury.adapters.list_local_models", return_value=["gemma:2b"]):
            code, out, _ = _run(["init", "--wizard", "--list-models"], stdin="")

        self.assertEqual(code, 0)
        self.assertIn("gemma:2b", out)


class PostingFlagsAreCheckedBeforeTheReview(unittest.TestCase):
    """#866: a posting flag with no target fails before any seat runs."""

    def _review(self, *flags):
        real_run = adapters.MockAdapter.run
        with mock.patch.object(
            adapters.MockAdapter, "run", autospec=True, side_effect=real_run
        ) as seat:
            code, _, _ = _run(["--mock", "--diff-file", str(_SAMPLE_DIFF), "-q", *flags])
        return code, seat

    def test_each_flag_is_refused_with_no_seat_called(self):
        for flag, message in (
            ("--post-summary", "error: --post-summary requires --pr"),
            ("--post", "error: --post-summary requires --pr"),
            ("--post-inline", "error: --post-inline requires --pr"),
            ("--label", "error: --label requires --pr"),
        ):
            with self.subTest(flag=flag):
                code, seat = self._review(flag)
                self.assertEqual(code, message)
                self.assertEqual(seat.call_count, 0, f"{flag}: a seat ran before the refusal")

    def test_the_review_itself_still_calls_seats(self):
        """The counterweight: the spy sees seats when nothing is refused."""
        code, seat = self._review()

        self.assertEqual(code, 0)
        self.assertGreater(seat.call_count, 0)


class UnreadableConfigIsAnError(unittest.TestCase):
    """#893: a config that exists but cannot be read exits 2 with one line."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.as_dir = self.d / "cfgdir"
        self.as_dir.mkdir()
        self.prompt = self.d / "prompt.txt"
        self.prompt.write_text("review this", encoding="utf-8")

    def _assert_clean(self, code, err, path):
        self.assertEqual(code, 2)
        self.assertIn(f"error: cannot read config {path}: ", err)
        self.assertNotIn("Traceback", err)

    def test_a_directory_is_refused_at_every_load_site(self):
        """Portable: `open()` on a directory fails on every OS (EISDIR, or EACCES on Windows)."""
        d = str(self.as_dir)
        for argv in (
            ["--mock", "--diff-file", str(_SAMPLE_DIFF), "-q", "--config", d],
            ["--config-validate", "--config", d],
            ["config", "show", "--config", d],
            ["run-agent", "--agent", "claude", "--role", "review", "--mock"]
            + ["--prompt-file", str(self.prompt), "--config", d],
        ):
            with self.subTest(argv=argv[0]):
                code, _, err = _run(argv)
                self._assert_clean(code, err, d)

    def test_the_doctor_reports_it_as_a_config_error(self):
        try:
            report = doctor.build_diagnostics(str(self.as_dir))
        except OSError as exc:
            self.fail(f"the doctor raised {exc!r} instead of reporting it")

        self.assertTrue(
            any(f"cannot read config {self.as_dir}" in w for w in report["config_warnings"]),
            report["config_warnings"],
        )

    @unittest.skipIf(os.name == "nt", "chmod 000 does not deny the owner on Windows")
    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads a 000 file")
    def test_an_unreadable_discovered_jury_toml(self):
        toml = self.d / "jury.toml"
        toml.write_text("[jury]\nrounds = 1\n", encoding="utf-8")
        toml.chmod(0)
        try:
            with contextlib.chdir(self.d):
                code, _, err = _run(["--mock", "--diff-file", str(_SAMPLE_DIFF), "-q"])
        finally:
            toml.chmod(0o600)

        self._assert_clean(code, err, "jury.toml")
        self.assertIn("Permission denied", err)


if __name__ == "__main__":
    unittest.main()
