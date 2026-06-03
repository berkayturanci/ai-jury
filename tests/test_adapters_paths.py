"""Coverage for adapter run() error/edge paths + build_argv + make_adapter.
Network/subprocess-free (all mocked)."""
from __future__ import annotations

import subprocess
import sys
import unittest
import unittest.mock as mock
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import adapters  # noqa: E402
from ai_jury.config import AgentSpec  # noqa: E402


def _spec(**kw):
    base = {"name": "claude", "vendor": "anthropic", "command": "claude"}
    base.update(kw)
    return AgentSpec(**base)


def _proc(returncode=0, stdout="", stderr=""):
    p = mock.Mock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


class BuildArgvTests(unittest.TestCase):
    def test_claude_argv(self):
        a = adapters.ClaudeAdapter(_spec(model="m", extra_args=["-x"]))
        self.assertEqual(a.build_argv("P"), ["claude", "-p", "P", "--model", "m", "-x"])

    def test_codex_argv_and_stdin(self):
        a = adapters.CodexAdapter(_spec(name="codex", vendor="openai", command="codex", extra_args=["-s", "read-only"]))
        self.assertEqual(a.build_argv("P"), ["codex", "exec", "-s", "read-only"])
        self.assertEqual(a._stdin_for("P"), "P")

    def test_agy_argv(self):
        a = adapters.AgyAdapter(_spec(name="agy", vendor="google", command="agy"))
        self.assertEqual(a.build_argv("P"), ["agy", "--print", "P"])


class AdapterRunTests(unittest.TestCase):
    def _adapter(self):
        return adapters.ClaudeAdapter(_spec())

    def test_missing_cli(self):
        with mock.patch("shutil.which", return_value=None):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, adapters.ERR_MISSING_CLI)

    def test_success(self):
        with mock.patch("shutil.which", return_value="/usr/bin/claude"), \
             mock.patch("subprocess.run", return_value=_proc(0, "a review")):
            r = self._adapter().run("p", timeout=5)
        self.assertTrue(r.ok)
        self.assertEqual(r.output, "a review")

    def test_timeout(self):
        with mock.patch("shutil.which", return_value="/usr/bin/claude"), \
             mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 1)):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, adapters.ERR_TIMEOUT)

    def test_spawn_failure(self):
        with mock.patch("shutil.which", return_value="/usr/bin/claude"), \
             mock.patch("subprocess.run", side_effect=OSError("nope")):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, adapters.ERR_SPAWN_FAILED)

    def test_nonzero_exit(self):
        with mock.patch("shutil.which", return_value="/usr/bin/claude"), \
             mock.patch("subprocess.run", return_value=_proc(1, "partial", "boom")):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)
        self.assertIn("exit 1", r.error)

    def test_empty_output(self):
        with mock.patch("shutil.which", return_value="/usr/bin/claude"), \
             mock.patch("subprocess.run", return_value=_proc(0, "   ")):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, adapters.ERR_EMPTY_OUTPUT)


class _Resp:
    def __init__(self, body):
        self._b = body
    def read(self):
        return self._b.encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class LocalAdapterRunTests(unittest.TestCase):
    def _adapter(self):
        return adapters.LocalAdapter(_spec(name="qwen", vendor="local", command="", model="qwen2.5-coder:7b",
                                           endpoint="http://localhost:11434/v1"))

    def test_success(self):
        body = '{"choices": [{"message": {"content": "local review"}}]}'
        with mock.patch("urllib.request.urlopen", return_value=_Resp(body)):
            r = self._adapter().run("p", timeout=10)
        self.assertTrue(r.ok)
        self.assertIn("local review", r.output)

    def test_http_error(self):
        err = urllib.error.HTTPError("u", 500, "err", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)
        self.assertIn("HTTP 500", r.error)

    def test_timeout(self):
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError()):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, adapters.ERR_TIMEOUT)

    def test_url_error_connection(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, adapters.ERR_CONNECTION)

    def test_generic_error(self):
        with mock.patch("urllib.request.urlopen", side_effect=ValueError("weird")):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)


class MakeAdapterTests(unittest.TestCase):
    def test_each_vendor(self):
        self.assertIsInstance(adapters.make_adapter(_spec(vendor="anthropic")), adapters.ClaudeAdapter)
        self.assertIsInstance(adapters.make_adapter(_spec(vendor="openai", command="codex")), adapters.CodexAdapter)
        self.assertIsInstance(adapters.make_adapter(_spec(vendor="google", command="agy")), adapters.AgyAdapter)
        self.assertIsInstance(
            adapters.make_adapter(_spec(vendor="local", command="", endpoint="http://x/v1")), adapters.LocalAdapter
        )

    def test_unknown_vendor_falls_back(self):
        self.assertIsInstance(adapters.make_adapter(_spec(vendor="acme", command="acme")), adapters.AgyAdapter)

    def test_mock_overrides(self):
        self.assertIsInstance(adapters.make_adapter(_spec(), mock=True), adapters.MockAdapter)


if __name__ == "__main__":
    unittest.main()
