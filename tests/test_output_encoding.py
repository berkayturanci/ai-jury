"""Report output must not crash on a legacy-codepage stdout (Windows).

The markdown report contains Unicode (e.g. `🏛️`, `⇄`). On Windows the console
defaults to a legacy code page (cp1252) that cannot encode those characters, so
`print(report)` raised `UnicodeEncodeError`. `cli._force_utf8_output()`
reconfigures the real streams to UTF-8; this test locks that in. Network-free.
"""
from __future__ import annotations

import io
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import cli  # noqa: E402


class ForceUtf8OutputTest(unittest.TestCase):
    def test_noop_on_stream_without_reconfigure(self):
        # Replaced streams (tests' StringIO, some pipes) lack `reconfigure`;
        # the helper must treat that as a safe no-op, not raise.
        prev_out, prev_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        try:
            cli._force_utf8_output()  # must not raise
        finally:
            sys.stdout, sys.stderr = prev_out, prev_err

    def test_reconfigures_legacy_codepage_stream_to_utf8(self):
        # A real text stream opened with a legacy encoding gets switched to UTF-8.
        prev = sys.stdout
        with Path(os.devnull).open("w", encoding="cp1252") as stream:
            sys.stdout = stream
            try:
                cli._force_utf8_output()
                encoding = stream.encoding
            finally:
                sys.stdout = prev
        self.assertEqual(encoding.lower().replace("-", ""), "utf8")

    def test_emoji_report_prints_under_cp1252_stream(self):
        # End-to-end: a cp1252-backed stream would raise on emoji without the
        # reconfigure; after it, the report's Unicode encodes cleanly.
        prev = sys.stdout
        with Path(os.devnull).open("w", encoding="cp1252") as stream:
            sys.stdout = stream
            try:
                cli._force_utf8_output()
                print("🏛️ AI Jury · debate ⇄ verdict")  # would raise pre-fix
            finally:
                sys.stdout = prev


if __name__ == "__main__":
    unittest.main()
