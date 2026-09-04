"""Coverage for the `jury init` and `jury config show|path` subcommand paths
(cli._run_init, _init_available, presets, _render_effective_config). Offline:
local-model discovery is mocked."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import cli  # noqa: E402


def run(args):
    out, err = io.StringIO(), io.StringIO()
    code = None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(args)
    except SystemExit as exc:
        code = exc.code
    return code, out.getvalue(), err.getvalue()


class InitTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_init_non_interactive_writes_valid_config(self):
        out = self.d / "jury.toml"
        code, _, _ = run(
            ["init", "--agents", "claude,codex", "--rounds", "2", "-o", str(out), "--force"]
        )
        self.assertEqual(code, 0)
        self.assertTrue(out.exists())
        from ai_jury.config import load_config

        load_config(str(out), validate=True)  # must be valid

    def test_init_preset_offline(self):
        out = self.d / "off.toml"
        with mock.patch("ai_jury.adapters.list_local_models", return_value=["qwen2.5-coder:7b"]):
            code, _, _ = run(["init", "--preset", "offline", "-o", str(out), "--force"])
        self.assertEqual(code, 0)
        self.assertTrue(out.exists())

    def test_every_preset_scaffolds_the_cross_vendor_guard(self):
        """`jury init --preset <p>` ships `[jury.ci]` + the hint, for all four (#692).

        `scaffold.py` has defined a min-vendors hint since #682, but `render_toml`
        emitted `[jury.ci]` only when the caller passed `ci_fail_on` — which no
        preset sets. So every generated `jury.toml` was silent about the guard
        that exits 3, `--preset thorough` included: three or four vendors, and
        therefore exactly the config that collapses on a one-CLI machine.

        Driven through the real CLI so the whole path is covered, and over
        `PRESETS` rather than a literal list so a fifth preset is covered too.
        Local-model discovery is mocked; nothing here touches the network.
        """
        from ai_jury.config import load_config
        from ai_jury.scaffold import PRESETS

        self.assertEqual(sorted(PRESETS), ["balanced", "fast", "offline", "thorough"])
        for name in sorted(PRESETS):
            with self.subTest(preset=name):
                out = self.d / f"{name}.toml"
                with mock.patch(
                    "ai_jury.adapters.list_local_models", return_value=["qwen2.5-coder:7b"]
                ):
                    code, _, _ = run(["init", "--preset", name, "-o", str(out), "--force"])
                self.assertEqual(code, 0)
                text = out.read_text(encoding="utf-8")
                self.assertIn("[jury.ci]", text)
                self.assertIn("# min_vendors = 2", text)
                self.assertIn("exit 3", text)
                self.assertIn("--no-min-vendors", text)
                # Comments only: the file must still load, and must not pin a
                # value that would outrank the shipped default.
                config = load_config(str(out), validate=True)
                self.assertEqual(config.ci.min_vendors, 2)

    def test_init_list_agents(self):
        code, out, _ = run(["init", "--list-agents"])
        self.assertEqual(code, 0)
        self.assertIn("claude", out)

    def test_init_list_models(self):
        with mock.patch("ai_jury.adapters.list_local_models", return_value=["gemma:2b"]):
            code, out, _ = run(["init", "--list-models"])
        self.assertEqual(code, 0)
        self.assertIn("gemma:2b", out)

    def test_init_list_models_none(self):
        with mock.patch("ai_jury.adapters.list_local_models", return_value=[]):
            code, out, _ = run(["init", "--list-models"])
        self.assertEqual(code, 0)
        self.assertIn("No local models", out)

    def test_init_refuses_overwrite_without_force(self):
        out = self.d / "exists.toml"
        out.write_text("[jury]\nrounds = 1\n")
        code, _, err = run(["init", "--agents", "claude", "-o", str(out)])
        self.assertNotEqual(code, 0)


class InitWizardCoverageTests(unittest.TestCase):
    """Drive the remaining wizard branches (issue #231)."""

    available = {"claude": True, "codex": True, "agy": False, "qwen": True}

    def _wizard(self, answers, models=("gemma:2b", "qwen2.5-coder:7b")):
        it = iter(answers)
        return cli._init_wizard(
            self.available, input_fn=lambda _p: next(it), models_fn=lambda _ep: list(models)
        )

    def test_depth_one_round_and_ci_skip_and_invalid_pick(self):
        # depth=1 (rounds=1), decision pick invalid (-> skipped), ci=skip (-> []).
        kw = self._wizard(["claude", "1", "x", "", "", "", "3", ""])
        self.assertEqual(kw["rounds"], 1)
        self.assertNotIn("decision", kw)  # invalid pick = skip
        self.assertEqual(kw["ci_fail_on"], [])

    def test_depth_two_explicit(self):
        kw = self._wizard(["claude", "2", "", "", "", "", "", ""])
        self.assertEqual(kw["rounds"], 2)
        self.assertNotIn("early_stop", kw)

    def test_local_model_typed_name(self):
        # A local reviewer + a typed (non-numeric) model name.
        kw = self._wizard(["claude,qwen", "", "", "", "", "", "", "", "my-model:1b"])
        self.assertEqual(kw["local_model"], "my-model:1b")

    def test_local_server_unreachable_typed_fallback(self):
        # models_fn returns [] -> the "could not reach" path, then a typed name.
        kw = self._wizard(["qwen", "", "", "", "", "", "", "", "custom:3b"], models=[])
        self.assertEqual(kw["local_model"], "custom:3b")

    def test_local_server_unreachable_typed_skipped(self):
        # Unreachable server + Enter (no typed name) -> local_model stays unset.
        kw = self._wizard(["qwen", "", "", "", "", "", "", "", ""], models=[])
        self.assertNotIn("local_model", kw)

    def test_run_init_wizard_local_model_flag_override(self):
        # `init --wizard --local-model X` threads the flag value into the config.
        out = Path(tempfile.mkdtemp()) / "w.toml"
        with (
            mock.patch.object(
                cli, "_init_wizard", return_value={"agents": ["claude"], "chair": "claude"}
            ),
            mock.patch.object(
                cli,
                "_init_available",
                return_value={"claude": True, "codex": False, "agy": False, "qwen": False},
            ),
        ):
            code, _, _ = run(
                ["init", "--wizard", "--local-model", "m:1b", "-o", str(out), "--force"]
            )
        self.assertEqual(code, 0)
        self.assertTrue(out.exists())


class InitWizardTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.available = {"claude": True, "codex": True, "agy": False, "qwen": True}

    def test_full_set_captured(self):
        # Reviewers, depth=adaptive(3), decision=vote(2), verify=n, context=expanded(2),
        # redact=n, ci gate=critical only(2), chair, local model by number.
        answers = iter(
            [
                "claude,qwen",  # reviewers
                "3",  # depth -> adaptive (early_stop)
                "2",  # decision -> vote
                "n",  # verify off
                "2",  # context -> expanded
                "n",  # redact off
                "2",  # ci fail_on -> critical only
                "codex",  # chair
                "1",  # local model #1
            ]
        )
        kwargs = cli._init_wizard(
            self.available,
            input_fn=lambda _p: next(answers),
            models_fn=lambda _ep: ["gemma:2b", "qwen2.5-coder:7b"],
        )
        self.assertEqual(kwargs["agents"], ["claude", "qwen"])
        self.assertEqual(kwargs["rounds"], 2)
        self.assertTrue(kwargs["early_stop"])
        self.assertEqual(kwargs["decision"], "vote")
        self.assertFalse(kwargs["verify"])
        self.assertEqual(kwargs["context_mode"], "expanded")
        self.assertFalse(kwargs["redact_secrets"])
        self.assertEqual(kwargs["ci_fail_on"], ["critical"])
        self.assertEqual(kwargs["chair"], "codex")
        self.assertEqual(kwargs["local_model"], "gemma:2b")
        from ai_jury.config import validate_config
        from ai_jury.scaffold import build_config

        validate_config(build_config(**kwargs))

    def test_all_skips_only_defaults(self):
        # Press Enter to every question -> only agents/chair captured (no new keys).
        kwargs = cli._init_wizard(
            self.available,
            input_fn=lambda _p: "",
            models_fn=lambda _ep: ["qwen2.5-coder:7b"],
        )
        self.assertEqual(kwargs["agents"], ["claude", "codex", "qwen"])  # detected
        self.assertEqual(kwargs["chair"], "claude")  # default = first reviewer
        # No optional knobs captured at all.
        for k in (
            "rounds",
            "early_stop",
            "auto_depth",
            "decision",
            "verify",
            "context_mode",
            "redact_secrets",
            "ci_fail_on",
        ):
            self.assertNotIn(k, kwargs)
        # local picked the default since qwen is in the detected set + Enter.
        self.assertEqual(kwargs["local_model"], "qwen2.5-coder:7b")

    def test_auto_depth_choice(self):
        answers = iter(["claude", "4", "", "", "", "", "", ""])
        kwargs = cli._init_wizard(self.available, input_fn=lambda _p: next(answers))
        self.assertTrue(kwargs["auto_depth"])
        self.assertNotIn("rounds", kwargs)

    def test_wizard_end_to_end_writes_minimal_valid_file(self):
        out = self.d / "jury.toml"
        answers = iter(
            [
                "claude,codex",  # reviewers
                "",  # depth skip
                "2",  # decision -> vote
                "",  # verify skip
                "2",  # context -> expanded
                "",  # redact skip
                "",  # ci gate skip (default critical,major -> NOT written)
                "",  # chair skip -> claude
            ]
        )
        # Drive the real wizard offline to capture kwargs, then exercise the
        # write path through main with those kwargs injected (no real stdin).
        kwargs = cli._init_wizard(self.available, input_fn=lambda _p: next(answers))
        with (
            mock.patch.object(cli, "_init_wizard", return_value=kwargs),
            mock.patch("ai_jury.adapters.list_local_models", return_value=[]),
        ):
            code, _, _ = run(["init", "--wizard", "-o", str(out), "--force"])
        self.assertEqual(code, 0)
        self.assertTrue(out.exists())
        text = out.read_text(encoding="utf-8")
        from ai_jury.config import load_config

        load_config(str(out), validate=True)  # must be valid
        self.assertIn('decision = "vote"', text)
        self.assertIn("[jury.context]", text)
        self.assertIn('mode = "expanded"', text)
        # Skipped CI gate -> the section is still written (#692), but with no
        # `fail_on` in it: the wizard records only what was actually chosen.
        self.assertIn("[jury.ci]", text)
        self.assertNotIn("fail_on", text)

    def test_wizard_plain_init_unchanged(self):
        # Plain `jury init --agents` must not gain any wizard-only *values*.
        # `[jury.ci]` is unconditional since #692, and carries only the commented
        # cross-vendor hint here — no key, so nothing a reader did not ask for.
        out = self.d / "plain.toml"
        code, _, _ = run(["init", "--agents", "claude,codex", "-o", str(out), "--force"])
        self.assertEqual(code, 0)
        text = out.read_text(encoding="utf-8")
        self.assertIn("[jury.ci]", text)
        self.assertNotIn("fail_on", text)
        self.assertNotIn("[jury.context]", text)
        self.assertNotIn("decision", text)
        self.assertNotIn("auto_depth", text)


class ConfigShowTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_config_show_builtin(self):
        code, out, _ = run(["config", "show"])
        self.assertEqual(code, 0)
        self.assertIn("rounds", out)

    def test_config_show_from_file(self):
        cfg = self.d / "jury.toml"
        cfg.write_text(
            '[jury]\nrounds = 1\nchair = "a"\n\n[[agent]]\nname = "a"\nvendor = "anthropic"\ncommand = "x"\n'
        )
        code, out, _ = run(["config", "show", "--config", str(cfg)])
        self.assertEqual(code, 0)
        self.assertIn("a", out)

    def test_config_path(self):
        code, out, _ = run(["config", "path"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
