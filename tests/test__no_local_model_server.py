"""No test reaches a model server on the developer's machine (#853).

The suite ran the same on CI, where nothing listens on the default local endpoint,
and on a laptop with Ollama running, where 82 tests sent 108 real requests to it:
availability probes and model listings for local seats that the tests configured
without mocking the network. Their results only matched by luck — #850 had two tests
that failed once Ollama answered them — so the next assertion added to any of them
would have been decided by the host.

`unittest discover` imports every test module before it runs the first test, so the
guard installed at import time below covers the whole run: every request to the
default local endpoint is refused with the `URLError` a refused connection raises.
Anything else passes through, and a test that patches `adapters._open` itself still
sees its own patch. A run that does not import this module is unguarded: a single
module (`-m unittest tests.test_x`) or a narrowed pattern (`discover -p test_x.py`).
"""

from __future__ import annotations

import ipaddress
import socket
import sys
import unittest
import unittest.mock as mock
import urllib.error
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_jury import adapters  # noqa: E402

_PORT = urlsplit(adapters._DEFAULT_LOCAL_ENDPOINT).port
_real_open = adapters._open


def _is_local(host: str) -> bool:
    """This machine: `localhost`, a loopback address, or the unspecified address
    (`0.0.0.0`, `::`), which a client connects to as this machine."""
    if host.rstrip(".") == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:  # the legacy IPv4 spellings a resolver accepts: `127.1`, `0`, `2130706433`
            ip = ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(host)))
        except OSError:
            return False
    return ip.is_loopback or ip.is_unspecified


def _refuses(target) -> bool:
    """Any spelling of this machine on the default port: `urlsplit` lowercases the
    host, and `127.1` or `[0:0:0:0:0:0:0:1]` are loopback addresses too."""
    parts = urlsplit(str(getattr(target, "full_url", target)))
    try:
        port = parts.port
    except ValueError:  # a malformed port is the real opener's to report
        return False
    return port == _PORT and _is_local(parts.hostname or "")


def _guarded_open(target, timeout):
    if _refuses(target):
        raise urllib.error.URLError("tests do not reach a local model server (#853)")
    return _real_open(target, timeout)


adapters._open = _guarded_open


class TheSuiteReachesNoLocalModelServer(unittest.TestCase):
    def test_the_guard_is_installed_for_the_run(self):
        self.assertIs(adapters._open, _guarded_open)

    def test_the_default_endpoint_is_refused_like_a_machine_with_no_server(self):
        module = sys.modules[__name__]
        for url in (
            adapters._DEFAULT_LOCAL_ENDPOINT + "/models",
            f"http://127.0.0.1:{_PORT}/v1/models",
            f"http://[::1]:{_PORT}/api/tags",
            f"http://LOCALHOST:{_PORT}/v1",
            f"http://localhost.:{_PORT}/v1",
            f"http://0.0.0.0:{_PORT}/v1",
            f"http://127.0.0.2:{_PORT}/v1",
            f"http://127.1:{_PORT}/v1",
            f"http://[0:0:0:0:0:0:0:1]:{_PORT}/v1",
            f"http://2130706433:{_PORT}/v1",
            f"http://0:{_PORT}/v1",
            f"http://[::]:{_PORT}/v1",
        ):
            # A server that answers with an error raises URLError too, so the proof is
            # that the real opener is never reached, not that something raised.
            with self.subTest(url), mock.patch.object(module, "_real_open") as real:
                with self.assertRaises(urllib.error.URLError):
                    adapters._open(url, 1)
                real.assert_not_called()
        self.assertIsNone(adapters.local_model_listing())

    def test_a_malformed_port_is_left_to_the_real_opener(self):
        module = sys.modules[__name__]
        with mock.patch.object(module, "_real_open", return_value="opened"):
            self.assertEqual(adapters._open("http://localhost:abc/v1", 1), "opened")

    def test_any_other_target_reaches_the_real_opener(self):
        """The counterweight: only the default endpoint is refused."""
        module = sys.modules[__name__]
        with mock.patch.object(module, "_real_open", return_value="opened") as real:
            self.assertEqual(adapters._open("http://localhost:8080/v1/models", 2), "opened")
            self.assertEqual(adapters._open("https://example.invalid/", 2), "opened")
        self.assertEqual(real.call_count, 2)


if __name__ == "__main__":
    unittest.main()
