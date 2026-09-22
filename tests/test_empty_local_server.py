"""A local model server with nothing to serve is not a reviewer (#849).

The pre-launch audit's reproduction: `ollama serve` with no model pulled, then

* `jury init </dev/null` wrote `model = "qwen2.5-coder:7b"` with no warning, while
  `jury init --list-models` on the same machine said `No local models found`;
* `jury --doctor` reported `[available] qwen … (probe: ok)` and `ready to run: yes`;
* the advised `git diff main... | jury --diff-file -` exited **0** with every section
  `HTTP 404: model 'qwen2.5-coder:7b' not found` and `effective panel: 0 of 1`.

Each is pinned here. Two rules are deliberately narrow, and each has a counterweight:

* An **empty** model list makes a seat unusable; a list that merely lacks the
  configured model is only a warning — a llama.cpp server ignores the model name and
  serves the one it loaded, so "not listed" is not "cannot answer".
* A run exits 3 only when **every** seat failed to return a result. A seat that
  answered "looks good" is an abstention, not a failure, and a clean single-seat run
  has to stay green.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_jury import cli, doctor  # noqa: E402
from ai_jury import config as cfgmod  # noqa: E402
from ai_jury.adapters import AgentResult, MockAdapter  # noqa: E402

_LOCAL = {"name": "qwen", "vendor": "local", "model": "qwen2.5-coder:7b"}


def _local_config(**overrides):
    agent = {**_LOCAL, **overrides}
    return cfgmod._from_dict({"jury": {"rounds": 1, "chair": agent["name"]}, "agent": [agent]})


def _spec(**fields):
    return SimpleNamespace(
        **{"name": "qwen", "vendor": "local", "endpoint": None, "model": "", **fields}
    )


def _entry(spec):
    """The seat's doctor entry, computed the way the doctor does it."""
    return doctor._agent_entry(spec, local_gaps={})


class TheDoctorSeesAnEmptyServer(unittest.TestCase):
    def test_a_server_listing_no_models_makes_the_seat_unusable(self):
        spec = _local_config().enabled_agents[0]
        with (
            mock.patch.object(doctor, "_is_available", return_value=True),
            mock.patch.object(doctor, "local_model_listing", return_value=[]),
        ):
            entry = _entry(spec)

        self.assertFalse(entry["available"])
        self.assertIn("lists no models", entry["reason"])
        self.assertIn("ollama pull qwen2.5-coder:7b", entry["reason"])

    def test_the_only_seat_being_unusable_means_not_ready(self):
        spec = _local_config().enabled_agents[0]
        with (
            mock.patch.object(doctor, "_is_available", return_value=True),
            mock.patch.object(doctor, "local_model_listing", return_value=[]),
            mock.patch("ai_jury.adapters.list_local_models", return_value=[]),
        ):
            entry = _entry(spec)
            recs = doctor._recommendations("jury.toml", {"enabled_agents": ["qwen"]}, [entry])

        self.assertFalse(recs["ready"], "ready to run: yes for a panel with nothing to run")

    def _diagnose(self, listing, available=True):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jury.toml"
            path.write_text(
                '[jury]\nrounds = 1\nchair = "qwen"\n\n'
                '[[agent]]\nname = "qwen"\nvendor = "local"\nmodel = "qwen2.5-coder:7b"\n',
                encoding="utf-8",
            )
            with (
                mock.patch.object(doctor, "_is_available", return_value=available),
                mock.patch.object(doctor, "_detect_capabilities", return_value={}),
                mock.patch.object(doctor, "local_model_listing", return_value=listing) as call,
            ):
                return doctor.build_diagnostics(str(path)), call

    def test_the_doctor_lists_the_server_once_and_the_text_report_says_why(self):
        """#850 third seat, twice: the entry and the warnings each listed the server;
        and with the warning dropped to avoid saying it twice, the text report — which
        prints warnings, not reasons — lost `ollama pull` altogether."""
        diag, listing = self._diagnose([])

        self.assertEqual(listing.call_count, 1)
        self.assertIn("lists no models", diag["agents"][0]["reason"])
        self.assertTrue(any("ollama pull" in w for w in diag["config_warnings"]))
        self.assertIn("ollama pull qwen2.5-coder:7b", doctor.render_report(diag))

    def test_an_unavailable_seat_is_never_listed(self):
        """A server that is down costs one probe, not a probe and a listing."""
        _diag, listing = self._diagnose([], available=False)

        listing.assert_not_called()

    def test_recording_a_run_makes_no_listing_request(self):
        """A run's metadata builds the same entries; it must not probe the server."""
        spec = _local_config().enabled_agents[0]
        with (
            mock.patch.object(doctor, "_is_available", return_value=True),
            mock.patch.object(doctor, "local_model_listing") as listing,
        ):
            entry = doctor._agent_entry(spec)

        listing.assert_not_called()
        self.assertTrue(entry["available"])

    def test_a_model_the_server_does_not_list_is_a_warning_not_a_verdict(self):
        """llama.cpp ignores the name and serves the model it loaded, so a seat
        whose model is unlisted stays available; the doctor only warns."""
        cfg = _local_config()
        spec = cfg.enabled_agents[0]
        with (
            mock.patch.object(doctor, "_is_available", return_value=True),
            mock.patch.object(doctor, "local_model_listing", return_value=["llama3:8b"]),
        ):
            gaps: dict = {}
            entry = doctor._agent_entry(spec, local_gaps=gaps)
            warnings = doctor._detect_warnings(cfg, gaps)

        self.assertTrue(entry["available"])
        self.assertTrue(any("is not among the models" in w for w in warnings), warnings)
        self.assertTrue(any("llama3:8b" in w for w in warnings), warnings)

    def test_an_untagged_model_is_served_by_its_latest_tag(self):
        with mock.patch.object(
            doctor, "local_model_listing", return_value=["qwen2.5-coder:latest"]
        ):
            self.assertIsNone(doctor._local_model_gap(_spec(model="qwen2.5-coder")))

    def test_a_listed_model_is_fine(self):
        with mock.patch.object(doctor, "local_model_listing", return_value=["qwen2.5-coder:7b"]):
            self.assertIsNone(doctor._local_model_gap(_spec(model="qwen2.5-coder:7b")))

    def test_a_failed_listing_changes_nothing(self):
        """No evidence is not evidence of a fault."""
        spec = _local_config().enabled_agents[0]
        with (
            mock.patch.object(doctor, "_is_available", return_value=True),
            mock.patch.object(doctor, "local_model_listing", return_value=None),
        ):
            entry = _entry(spec)
            gap = doctor._local_model_gap(spec)

        self.assertTrue(entry["available"])
        self.assertIsNone(gap)

    def test_a_listing_that_raises_changes_nothing_either(self):
        """Diagnostics never crash: a listing that raises is treated as no evidence."""
        with mock.patch.object(doctor, "local_model_listing", side_effect=OSError("boom")):
            self.assertIsNone(doctor._local_model_gap(_spec(model="qwen2.5-coder:7b")))

    def test_a_seat_that_is_not_local_is_never_listed(self):
        with mock.patch.object(doctor, "local_model_listing") as listing:
            self.assertIsNone(doctor._local_model_gap(_spec(vendor="anthropic", model="x")))
        listing.assert_not_called()

    def test_a_seat_naming_no_model_is_told_the_default_to_pull(self):
        with mock.patch.object(doctor, "local_model_listing", return_value=[]):
            gap = doctor._local_model_gap(_spec(model=""))
        self.assertIn("ollama pull qwen2.5-coder:7b", gap[1])


class InitSaysSoWhenItWritesTheSeat(unittest.TestCase):
    def _init(self, listing):
        with tempfile.TemporaryDirectory() as tmp:
            out, err = io.StringIO(), io.StringIO()
            with (
                mock.patch.object(doctor, "local_model_listing", return_value=listing),
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
            ):
                code = cli.main(
                    ["init", "--agents", "qwen", "--output", str(Path(tmp) / "jury.toml")]
                )
            written = (Path(tmp) / "jury.toml").exists()
        return code, err.getvalue(), written

    def test_an_empty_server_is_named_at_init(self):
        code, err, written = self._init([])

        self.assertEqual(code, 0)
        self.assertTrue(written, "the config is valid and should still be written")
        self.assertIn("warning: agent 'qwen'", err)
        self.assertIn("lists no models", err)

    def test_a_server_serving_the_model_draws_no_warning(self):
        """The counterweight."""
        code, err, _ = self._init(["qwen2.5-coder:7b"])

        self.assertEqual(code, 0)
        self.assertNotIn("warning: agent", err)


_DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
 def f(x):
-    return x
+    return x + 1
+
"""

_ONE_SEAT = """
[jury]
rounds = 1
chair = "claude"
verify = false

[[agent]]
name = "claude"
vendor = "anthropic"
command = "claude"
"""

_TWO_SEATS = (
    _ONE_SEAT
    + """
[[agent]]
name = "codex"
vendor = "openai"
command = "codex"
"""
)


def _run_with(config_toml: str, failing: set[str], argv=(), abstaining: frozenset = frozenset()):
    """Run the panel with `failing` seats returning no result at all (ok=False), and
    `abstaining` seats answering in prose with no findings block (ok=True)."""
    real = MockAdapter.run

    def _run(self, prompt, phase="review", timeout=None, role_policy=None):
        if phase == "review" and self.name in failing:
            return AgentResult(self.name, self.spec.vendor, False, "", 0.0, error="HTTP 404")
        if phase == "review" and self.name in abstaining:
            return AgentResult(self.name, self.spec.vendor, True, "Looks good to me.", 0.0)
        return real(self, prompt, phase=phase, timeout=timeout, role_policy=role_policy)

    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "jury.toml"
        config.write_text(config_toml, encoding="utf-8")
        diff = Path(tmp) / "changes.diff"
        diff.write_text(_DIFF, encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(MockAdapter, "run", _run),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = cli.main(["--mock", "--diff-file", str(diff), "--config", str(config), *argv])
    return code, err.getvalue()


class ARunThatReviewedNothingIsNotAPass(unittest.TestCase):
    def test_every_seat_failing_exits_three(self):
        code, err = _run_with(_ONE_SEAT, {"claude"})

        self.assertEqual(code, 3)
        self.assertIn("no reviewer returned a result", err)

    def test_it_outranks_the_ci_severity_gate(self):
        """`--ci` finds no findings in an empty run; that must not launder it green."""
        code, _ = _run_with(_ONE_SEAT, {"claude"}, ["--ci"])

        self.assertEqual(code, 3)

    def test_a_seat_that_answered_keeps_the_run_green(self):
        """The counterweight: a clean single-seat run is untouched."""
        code, err = _run_with(_ONE_SEAT, set())

        self.assertEqual(code, 0)
        self.assertNotIn("no reviewer returned a result", err)

    def test_a_single_seat_that_only_says_it_looks_good_stays_green(self):
        """The trap this rule avoids: a prose "looks good" is an abstention — it
        names nothing, so it is not a review — but it is not a failure either, and
        failing it would turn every clean single-seat install red."""
        code, err = _run_with(_ONE_SEAT, set(), abstaining=frozenset({"claude"}))

        self.assertEqual(code, 0)
        self.assertNotIn("no reviewer returned a result", err)

    def test_one_seat_failing_of_two_is_not_this_rule(self):
        """Too few reviews is `min_reviews` / `min_vendors`' business, not this one's."""
        _code, err = _run_with(_TWO_SEATS, {"codex"}, ["--no-min-vendors"])

        self.assertNotIn("no reviewer returned a result", err)


if __name__ == "__main__":
    unittest.main()
