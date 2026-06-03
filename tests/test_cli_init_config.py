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
        code, _, _ = run(["init", "--agents", "claude,codex", "--rounds", "2", "-o", str(out), "--force"])
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


class ConfigShowTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_config_show_builtin(self):
        code, out, _ = run(["config", "show"])
        self.assertEqual(code, 0)
        self.assertIn("rounds", out)

    def test_config_show_from_file(self):
        cfg = self.d / "jury.toml"
        cfg.write_text('[jury]\nrounds = 1\nchair = "a"\n\n[[agent]]\nname = "a"\nvendor = "anthropic"\ncommand = "x"\n')
        code, out, _ = run(["config", "show", "--config", str(cfg)])
        self.assertEqual(code, 0)
        self.assertIn("a", out)

    def test_config_path(self):
        code, out, _ = run(["config", "path"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
