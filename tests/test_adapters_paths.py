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
        # The prompt is delivered on stdin, not argv (issue #287); the mandatory
        # --disallowed-tools is injected at the adapter layer (issue #288).
        a = adapters.ClaudeAdapter(_spec(model="m", extra_args=["-x"]))
        self.assertEqual(
            a.build_argv("P"),
            [
                "claude",
                "-p",
                "--model",
                "m",
                "--disallowed-tools",
                "Edit,Write,NotebookEdit,Bash",
                "-x",
            ],
        )

    def test_claude_prompt_via_stdin_not_argv(self):
        a = adapters.ClaudeAdapter(_spec())
        argv = a.build_argv("SENSITIVE PROMPT")
        self.assertNotIn("SENSITIVE PROMPT", argv)
        self.assertEqual(a._stdin_for("SENSITIVE PROMPT"), "SENSITIVE PROMPT")

    def test_codex_argv_and_stdin(self):
        a = adapters.CodexAdapter(
            _spec(name="codex", vendor="openai", command="codex", extra_args=["-s", "read-only"])
        )
        self.assertEqual(a.build_argv("P"), ["codex", "exec", "-s", "read-only"])
        self.assertEqual(a._stdin_for("P"), "P")

    def test_agy_argv(self):
        # Prompt on stdin, not argv (issue #287); --sandbox is injected (#288).
        a = adapters.AgyAdapter(_spec(name="agy", vendor="google", command="agy"))
        argv = a.build_argv("P")
        self.assertEqual(argv, ["agy", "--print", "--sandbox"])
        self.assertNotIn("P", argv)
        self.assertEqual(a._stdin_for("P"), "P")


class AdapterRunTests(unittest.TestCase):
    def _adapter(self):
        return adapters.ClaudeAdapter(_spec())

    def test_missing_cli(self):
        with mock.patch("shutil.which", return_value=None):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, adapters.ERR_MISSING_CLI)

    def test_success(self):
        with (
            mock.patch("shutil.which", return_value="/usr/bin/claude"),
            mock.patch("ai_jury.adapters._spawn", return_value=_proc(0, "a review")),
        ):
            r = self._adapter().run("p", timeout=5)
        self.assertTrue(r.ok)
        self.assertEqual(r.output, "a review")

    def test_timeout(self):
        with (
            mock.patch("shutil.which", return_value="/usr/bin/claude"),
            mock.patch(
                "ai_jury.adapters._spawn", side_effect=subprocess.TimeoutExpired("claude", 1)
            ),
        ):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, adapters.ERR_TIMEOUT)

    def test_spawn_failure(self):
        with (
            mock.patch("shutil.which", return_value="/usr/bin/claude"),
            mock.patch("ai_jury.adapters._spawn", side_effect=OSError("nope")),
        ):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, adapters.ERR_SPAWN_FAILED)

    def test_spawn_failure_redacts_secret_in_exception(self):
        # OSError/Exception text from a failed spawn can echo back env or argv
        # content (e.g. a token embedded in a broken PATH/shell wrapper); it
        # must be redacted before landing in the report like stderr is.
        leak = "nope token=ghp_" + "a" * 36
        with (
            mock.patch("shutil.which", return_value="/usr/bin/claude"),
            mock.patch("ai_jury.adapters._spawn", side_effect=OSError(leak)),
        ):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)
        self.assertNotIn("ghp_" + "a" * 36, r.error)
        self.assertIn("[REDACTED", r.error)

    def test_version_probe_failure_redacts_secret_in_exception(self):
        # Same _spawn call, but via detect_capabilities()'s own except-branch
        # (distinct from run()'s), which builds a separate warning string.
        leak = "boom token=ghp_" + "a" * 36
        with (
            mock.patch("shutil.which", return_value="/usr/bin/claude"),
            mock.patch("ai_jury.adapters._spawn", side_effect=RuntimeError(leak)),
        ):
            caps = self._adapter().detect_capabilities()
        self.assertEqual(caps["status"], adapters.CAP_UNKNOWN_VERSION)
        warning = caps["warnings"][0]
        self.assertNotIn("ghp_" + "a" * 36, warning)
        self.assertIn("[REDACTED", warning)

    def test_version_probe_raw_output_redacts_secret(self):
        # The version probe's combined stdout+stderr is stored verbatim in
        # raw_version_output and surfaced via `jury doctor`; a CLI that
        # echoes a token in its version banner (e.g. a misconfigured wrapper
        # script) must not leak it there either.
        leak = "v1.0.0 token=ghp_" + "a" * 36
        with (
            mock.patch("shutil.which", return_value="/usr/bin/claude"),
            mock.patch("ai_jury.adapters._spawn", return_value=_proc(0, leak, "")),
        ):
            caps = self._adapter().detect_capabilities()
        self.assertNotIn("ghp_" + "a" * 36, caps["raw_version_output"])
        self.assertIn("[REDACTED", caps["raw_version_output"])

    def test_nonzero_exit(self):
        with (
            mock.patch("shutil.which", return_value="/usr/bin/claude"),
            mock.patch("ai_jury.adapters._spawn", return_value=_proc(1, "partial", "boom")),
        ):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)
        self.assertIn("exit 1", r.error)

    def test_nonzero_exit_redacts_secret_in_stderr(self):
        # A crashing CLI can dump a token into stderr; the error string is
        # rendered into the report and posted to the PR, so it must be redacted
        # like the LocalAdapter path (audit 2026-06-13/N-1).
        leak = "boom AKIAIOSFODNN7EXAMPLE failed token=ghp_" + "a" * 36
        with (
            mock.patch("shutil.which", return_value="/usr/bin/claude"),
            mock.patch("ai_jury.adapters._spawn", return_value=_proc(1, "", leak)),
        ):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)
        self.assertNotIn("ghp_" + "a" * 36, r.error)
        self.assertIn("[REDACTED", r.error)

    def test_empty_output(self):
        with (
            mock.patch("shutil.which", return_value="/usr/bin/claude"),
            mock.patch("ai_jury.adapters._spawn", return_value=_proc(0, "   ")),
        ):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, adapters.ERR_EMPTY_OUTPUT)


class _Resp:
    def __init__(self, body):
        self._b = body

    def read(self, *_args):
        return self._b.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class LocalAdapterRunTests(unittest.TestCase):
    def _adapter(self):
        return adapters.LocalAdapter(
            _spec(
                name="qwen",
                vendor="local",
                command="",
                model="qwen2.5-coder:7b",
                endpoint="http://localhost:11434/v1",
            )
        )

    def test_success(self):
        body = '{"choices": [{"message": {"content": "local review"}}]}'
        with mock.patch("ai_jury.adapters._open", return_value=_Resp(body)):
            r = self._adapter().run("p", timeout=10)
        self.assertTrue(r.ok)
        self.assertIn("local review", r.output)

    def test_http_error(self):
        err = urllib.error.HTTPError("u", 500, "err", {}, None)
        with mock.patch("ai_jury.adapters._open", side_effect=err):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)
        self.assertIn("HTTP 500", r.error)

    def test_timeout(self):
        with mock.patch("ai_jury.adapters._open", side_effect=TimeoutError()):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, adapters.ERR_TIMEOUT)

    def test_url_error_connection(self):
        with mock.patch("ai_jury.adapters._open", side_effect=urllib.error.URLError("refused")):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, adapters.ERR_CONNECTION)

    def test_url_error_redacts_secret_in_reason(self):
        # exc.reason can be an arbitrary OSError/str carrying proxy or DNS
        # error text; if a token ends up embedded there it must be redacted
        # like the adjacent generic-Exception branch already is.
        leak = "refused token=ghp_" + "a" * 36
        with mock.patch("ai_jury.adapters._open", side_effect=urllib.error.URLError(leak)):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)
        self.assertNotIn("ghp_" + "a" * 36, r.error)
        self.assertIn("[REDACTED", r.error)

    def test_generic_error(self):
        with mock.patch("ai_jury.adapters._open", side_effect=ValueError("weird")):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)

    def test_generic_error_redacts_secret(self):
        leak = "weird token=ghp_" + "a" * 36
        with mock.patch("ai_jury.adapters._open", side_effect=ValueError(leak)):
            r = self._adapter().run("p")
        self.assertFalse(r.ok)
        self.assertNotIn("ghp_" + "a" * 36, r.error)
        self.assertIn("[REDACTED", r.error)


class SpawnTests(unittest.TestCase):
    """Issue #293/F-7: _spawn runs in its own group and kills it on timeout."""

    def test_happy_path_returns_completed_process(self):
        proc = adapters._spawn(
            [sys.executable, "-c", "print('hi', end='')"],
            None,
            timeout=10,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "hi")

    def test_stdin_is_delivered(self):
        proc = adapters._spawn(
            [
                sys.executable,
                "-c",
                "import sys; print(sys.stdin.read(), end='')",
            ],
            "piped-input",
            timeout=10,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "piped-input")

    def test_timeout_raises(self):
        import subprocess as _sp

        with self.assertRaises(_sp.TimeoutExpired):
            adapters._spawn([sys.executable, "-c", "import time; time.sleep(30)"], None, timeout=1)


class HttpOnlyOpenerTests(unittest.TestCase):
    """Issue #291: the local-adapter opener must refuse non-http(s) schemes."""

    def test_file_scheme_rejected(self):
        # No FileHandler is registered, so file:// raises rather than reading
        # a local file — defense in depth behind config endpoint validation.
        with self.assertRaises(urllib.error.URLError):
            adapters._open("file:///etc/passwd", timeout=1)

    def test_ftp_scheme_rejected(self):
        with self.assertRaises(urllib.error.URLError):
            adapters._open("ftp://localhost/x", timeout=1)

    def test_no_redirect_handler_registered(self):
        # Review of #291: a 3xx redirect must NOT be followed (an endpoint could
        # 302 to an internal/metadata host, bypassing the configured-URL check).
        import urllib.request

        opener = adapters._http_only_opener()
        self.assertFalse(
            any(isinstance(h, urllib.request.HTTPRedirectHandler) for h in opener.handlers)
        )


class MakeAdapterTests(unittest.TestCase):
    def test_each_vendor(self):
        self.assertIsInstance(
            adapters.make_adapter(_spec(vendor="anthropic")), adapters.ClaudeAdapter
        )
        self.assertIsInstance(
            adapters.make_adapter(_spec(vendor="openai", command="codex")), adapters.CodexAdapter
        )
        self.assertIsInstance(
            adapters.make_adapter(_spec(vendor="google", command="agy")), adapters.AgyAdapter
        )
        self.assertIsInstance(
            adapters.make_adapter(_spec(vendor="local", command="", endpoint="http://x/v1")),
            adapters.LocalAdapter,
        )
        self.assertIsInstance(
            adapters.make_adapter(
                _spec(vendor="openai-compatible", endpoint="https://api.deepseek.com/v1")
            ),
            adapters.GenericOpenAICompatibleAdapter,
        )
        self.assertIsInstance(
            adapters.make_adapter(_spec(vendor="cli", command="aider")),
            adapters.GenericCLIAdapter,
        )

    def test_unknown_vendor_falls_back(self):
        self.assertIsInstance(
            adapters.make_adapter(_spec(vendor="acme", command="acme")), adapters.GenericCLIAdapter
        )

    def test_unknown_vendor_with_endpoint_falls_back_to_http(self):
        self.assertIsInstance(
            adapters.make_adapter(
                _spec(vendor="openrouter", endpoint="https://openrouter.ai/api/v1")
            ),
            adapters.GenericOpenAICompatibleAdapter,
        )

    def test_register_adapter(self):
        class CustomAdapter(adapters.Adapter):
            pass

        adapters.register_adapter("custom-provider", CustomAdapter)
        spec = _spec(vendor="custom-provider")
        adapter = adapters.make_adapter(spec)
        self.assertIsInstance(adapter, CustomAdapter)

    def test_mock_overrides(self):
        self.assertIsInstance(adapters.make_adapter(_spec(), mock=True), adapters.MockAdapter)


class UniversalAdapterTests(unittest.TestCase):
    def test_generic_openai_adapter_urls_and_headers(self):
        spec = _spec(
            name="deepseek",
            vendor="openai-compatible",
            endpoint="https://api.deepseek.com/v1",
            api_key_env="DEEPSEEK_API_KEY",
            headers={"HTTP-Referer": "https://ai-jury.org"},
        )
        a = adapters.GenericOpenAICompatibleAdapter(spec)
        self.assertEqual(a._api_url(), "https://api.deepseek.com/v1/chat/completions")
        with mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "sk-secret-123"}):
            self.assertEqual(a._api_key(), "sk-secret-123")
            self.assertTrue(a.available())
            hdrs = a._headers()
            self.assertEqual(hdrs["Authorization"], "Bearer sk-secret-123")
            self.assertEqual(hdrs["HTTP-Referer"], "https://ai-jury.org")

    def test_generic_openai_adapter_parse_content(self):
        data_str = {"choices": [{"message": {"content": "  Review content here  "}}]}
        self.assertEqual(
            adapters.GenericOpenAICompatibleAdapter.parse_content(data_str), "Review content here"
        )

        data_list = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "Part 1 "},
                            {"type": "text", "text": "Part 2"},
                        ]
                    }
                }
            ]
        }
        self.assertEqual(
            adapters.GenericOpenAICompatibleAdapter.parse_content(data_list), "Part 1 Part 2"
        )

        self.assertEqual(adapters.GenericOpenAICompatibleAdapter.parse_content({}), "")

    @mock.patch("shutil.which", return_value="/usr/local/bin/aider")
    @mock.patch("ai_jury.adapters._spawn")
    def test_generic_cli_adapter_run_stdin(self, mock_spawn, _mock_which):
        mock_spawn.return_value = _proc(0, "Aider review complete")
        spec = _spec(name="aider", vendor="cli", command="aider", prompt_mode="stdin")
        a = adapters.GenericCLIAdapter(spec)
        self.assertTrue(a.available())

        res = a.run("PROMPT", timeout=30)
        self.assertTrue(res.ok)
        self.assertEqual(res.output, "Aider review complete")
        mock_spawn.assert_called_once()
        argv, stdin = mock_spawn.call_args[0]
        self.assertEqual(argv[0], "aider")
        self.assertEqual(stdin, "PROMPT")

    @mock.patch("shutil.which", return_value="/usr/local/bin/aider")
    @mock.patch("ai_jury.adapters._spawn")
    def test_generic_cli_adapter_run_arg(self, mock_spawn, _mock_which):
        mock_spawn.return_value = _proc(0, "Arg review complete")
        spec = _spec(name="aider", vendor="cli", command="aider", prompt_mode="arg")
        a = adapters.GenericCLIAdapter(spec)

        res = a.run("PROMPT", phase="debate", timeout=15)
        self.assertTrue(res.ok)
        self.assertEqual(res.output, "Arg review complete")
        argv, stdin = mock_spawn.call_args[0]
        self.assertIn("PROMPT", argv)
        self.assertIsNone(stdin)

    def test_generic_openai_adapter_missing_key_reporting(self):
        spec = _spec(
            name="openrouter",
            vendor="openai-compatible",
            endpoint="https://openrouter.ai/api/v1",
            api_key_env="OPENROUTER_API_KEY",
        )
        a = adapters.GenericOpenAICompatibleAdapter(spec)
        with mock.patch.dict("os.environ", {}, clear=True):
            caps = a.detect_capabilities()
            self.assertIn("OPENROUTER_API_KEY is not set in the environment", caps["warnings"])
            res = a.run("PROMPT")
            self.assertFalse(res.ok)
            self.assertIn("OPENROUTER_API_KEY is not set in the environment", res.error)
            self.assertEqual(res.error_code, adapters.ERR_MISSING_API_KEY)

    @mock.patch("shutil.which", return_value="/usr/local/bin/aider")
    @mock.patch("ai_jury.adapters._spawn")
    def test_generic_cli_adapter_spawn_failure_redacts_secret_in_exception(
        self, mock_spawn, _mock_which
    ):
        leak = "boom token=ghp_" + "a" * 36
        mock_spawn.side_effect = RuntimeError(leak)
        spec = _spec(name="aider", vendor="cli", command="aider", prompt_mode="stdin")
        a = adapters.GenericCLIAdapter(spec)

        res = a.run("PROMPT")
        self.assertFalse(res.ok)
        self.assertEqual(res.error_code, adapters.ERR_SPAWN_FAILED)
        self.assertNotIn("ghp_" + "a" * 36, res.error)
        self.assertIn("[REDACTED", res.error)

    @mock.patch("shutil.which", return_value="/usr/local/bin/aider")
    @mock.patch("ai_jury.adapters._spawn")
    def test_generic_cli_adapter_error_classification_and_redaction(self, mock_spawn, _mock_which):
        mock_spawn.return_value = _proc(
            1, "", stderr="Error: unauthorized 401 secret sk-proj-1234567890abcdef"
        )
        spec = _spec(name="aider", vendor="cli", command="aider", prompt_mode="stdin")
        a = adapters.GenericCLIAdapter(spec)

        res = a.run("PROMPT")
        self.assertFalse(res.ok)
        self.assertEqual(res.error_code, adapters.ERR_AUTH_REQUIRED)
        self.assertNotIn("sk-proj-1234567890abcdef", res.error)
        self.assertIn("[REDACTED", res.error)

    @mock.patch("shutil.which", return_value=None)
    def test_generic_cli_adapter_unavailable_returns_missing_cli(self, _mock_which):
        spec = _spec(name="aider", vendor="cli", command="aider", prompt_mode="stdin")
        a = adapters.GenericCLIAdapter(spec)
        res = a.run("PROMPT")
        self.assertFalse(res.ok)
        self.assertEqual(res.error_code, adapters.ERR_MISSING_CLI)
        self.assertIn("command 'aider' not found on PATH", res.error)


if __name__ == "__main__":
    unittest.main()
