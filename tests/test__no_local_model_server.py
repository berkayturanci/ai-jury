"""No test reaches a model server on the developer's machine (#853).

The suite ran the same on CI, where nothing listens on the default local endpoint,
and on a laptop with Ollama running, where 82 tests sent 108 real requests to it:
availability probes and model listings for local seats that the tests configured
without mocking the network. Their results only matched by luck — #850 had two tests
that failed once Ollama answered them — so the next assertion added to any of them
would have been decided by the host.

`unittest discover` imports every test module before it runs the first test, so the
guard installed at import time below covers the whole run: every request to the
default local endpoint is refused exactly as a machine with no server refuses it.
Anything else passes through, and a test that patches `adapters._open` itself still
sees its own patch. A single module run on its own (`-m unittest tests.test_x`) does
not import this one and is unguarded.
"""

from __future__ import annotations

import sys
import unittest
import unittest.mock as mock
import urllib.error
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_jury import adapters  # noqa: E402

_PORT = urlsplit(adapters._DEFAULT_LOCAL_ENDPOINT).port
_REFUSED = tuple(f"{host}:{_PORT}" for host in ("localhost", "127.0.0.1", "[::1]"))
_real_open = adapters._open


def _refuses(target) -> bool:
    url = str(getattr(target, "full_url", target))
    return urlsplit(url).netloc.rsplit("@", 1)[-1] in _REFUSED


def _guarded_open(target, timeout):
    if _refuses(target):
        raise urllib.error.URLError("tests do not reach a local model server (#853)")
    return _real_open(target, timeout)


adapters._open = _guarded_open


class TheSuiteReachesNoLocalModelServer(unittest.TestCase):
    def test_the_guard_is_installed_for_the_run(self):
        self.assertIs(adapters._open, _guarded_open)

    def test_the_default_endpoint_is_refused_like_a_machine_with_no_server(self):
        for url in (
            adapters._DEFAULT_LOCAL_ENDPOINT + "/models",
            f"http://127.0.0.1:{_PORT}/v1/models",
            f"http://[::1]:{_PORT}/api/tags",
        ):
            with self.subTest(url), self.assertRaises(urllib.error.URLError):
                adapters._open(url, 1)
        self.assertIsNone(adapters.local_model_listing())

    def test_any_other_target_reaches_the_real_opener(self):
        """The counterweight: only the default endpoint is refused."""
        module = sys.modules[__name__]
        with mock.patch.object(module, "_real_open", return_value="opened") as real:
            self.assertEqual(adapters._open("http://localhost:8080/v1/models", 2), "opened")
            self.assertEqual(adapters._open("https://example.invalid/", 2), "opened")
        self.assertEqual(real.call_count, 2)


if __name__ == "__main__":
    unittest.main()
