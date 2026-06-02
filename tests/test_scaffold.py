"""Tests for `council init` config scaffolding (issue #107)."""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_review_council import cli  # noqa: E402
from agent_review_council.config import _from_dict, validate_config  # noqa: E402
from agent_review_council.scaffold import (  # noqa: E402
    build_config,
    render_toml,
)


class BuildConfigTest(unittest.TestCase):
    def test_cloud_panel(self):
        cfg = build_config(["claude", "codex"], rounds=2)
        names = [a["name"] for a in cfg["agent"]]
        self.assertEqual(names, ["claude", "codex"])
        self.assertEqual(cfg["council"]["chair"], "claude")  # defaults to first
        self.assertEqual(cfg["council"]["rounds"], 2)

    def test_secure_defaults_carried_over(self):
        cfg = build_config(["codex", "agy"])
        codex = next(a for a in cfg["agent"] if a["name"] == "codex")
        agy = next(a for a in cfg["agent"] if a["name"] == "agy")
        self.assertIn("read-only", codex["extra_args"])  # issue #100 default
        self.assertIn("--sandbox", agy["extra_args"])

    def test_local_agent_overrides(self):
        cfg = build_config(["qwen"], local_model="llama3", local_endpoint="http://h:1/v1")
        qwen = cfg["agent"][0]
        self.assertEqual(qwen["vendor"], "local")
        self.assertEqual(qwen["model"], "llama3")
        self.assertEqual(qwen["endpoint"], "http://h:1/v1")
        self.assertNotIn("command", {k: v for k, v in qwen.items() if v})

    def test_unknown_agent_raises(self):
        with self.assertRaises(ValueError):
            build_config(["gpt5"])

    def test_empty_selection_raises(self):
        with self.assertRaises(ValueError):
            build_config([])

    def test_dedup_preserves_order(self):
        cfg = build_config(["codex", "claude", "codex"])
        self.assertEqual([a["name"] for a in cfg["agent"]], ["codex", "claude"])


class RenderTomlTest(unittest.TestCase):
    def test_renders_valid_loadable_toml(self):
        cfg = build_config(["claude", "codex", "qwen"], rounds=1, verify=False)
        text = render_toml(cfg)
        # Parses as TOML and round-trips through the real loader + validator.
        parsed = tomllib.loads(text)
        self.assertEqual(parsed["council"]["rounds"], 1)
        self.assertFalse(parsed["council"]["verify"])
        warnings = validate_config(parsed)  # no ConfigError
        self.assertIsInstance(warnings, list)
        loaded = _from_dict(parsed)
        self.assertEqual([a.name for a in loaded.agents], ["claude", "codex", "qwen"])

    def test_local_agent_has_no_empty_command(self):
        text = render_toml(build_config(["qwen"]))
        self.assertNotIn('command = ""', text)
        self.assertIn('endpoint = "http://localhost:11434/v1"', text)


class InitCliTest(unittest.TestCase):
    def _run(self, argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        prev = sys.stdin
        sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(argv)
        finally:
            sys.stdin = prev
        return code, out.getvalue(), err.getvalue()

    def test_flag_driven_writes_valid_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "council.toml"
            code, out, _ = self._run(
                ["init", "--agents", "claude,codex", "--rounds", "2", "-o", str(path)]
            )
            self.assertEqual(code, 0)
            self.assertIn("Wrote", out)
            # Validate the file the same way a real run would.
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            validate_config(data)
            self.assertEqual([a["name"] for a in data["agent"]], ["claude", "codex"])

    def test_refuses_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "council.toml"
            path.write_text("existing", encoding="utf-8")
            code, _, err = self._run(["init", "--agents", "claude", "-o", str(path)])
            self.assertEqual(code, 2)
            self.assertIn("already exists", err)
            self.assertEqual(path.read_text(encoding="utf-8"), "existing")

    def test_force_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "council.toml"
            path.write_text("existing", encoding="utf-8")
            code, _, _ = self._run(
                ["init", "--agents", "claude", "-o", str(path), "--force"]
            )
            self.assertEqual(code, 0)
            self.assertIn("[[agent]]", path.read_text(encoding="utf-8"))

    def test_unknown_agent_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "council.toml"
            code, _, err = self._run(["init", "--agents", "nope", "-o", str(path)])
            self.assertEqual(code, 2)
            self.assertIn("unknown agent", err)

    def test_list_agents(self):
        code, out, _ = self._run(["init", "--list-agents"])
        self.assertEqual(code, 0)
        for name in ("claude", "codex", "agy", "qwen"):
            self.assertIn(name, out)

    def test_interactive_with_injected_input(self):
        # Drive the interactive helper directly with a fake input function.
        available = {"claude": True, "codex": True, "agy": False, "qwen": True}
        answers = iter(["claude,qwen", "1", "claude", "n", "mymodel"])
        kwargs = cli._init_interactive(available, input_fn=lambda _p: next(answers))
        self.assertEqual(kwargs["agents"], ["claude", "qwen"])
        self.assertEqual(kwargs["rounds"], 1)
        self.assertEqual(kwargs["chair"], "claude")
        self.assertFalse(kwargs["verify"])
        self.assertEqual(kwargs["local_model"], "mymodel")
        # And the result builds a valid config.
        validate_config(build_config(**kwargs))


if __name__ == "__main__":
    unittest.main()
