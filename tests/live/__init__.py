"""Live smoke-test package.

These tests exercise the real native agent CLIs (``claude``, ``codex``,
``agy``). They are opt-in only and are skipped entirely unless the
environment variable ``JURY_LIVE=1`` is set. See ``test_live_smoke``
for details.
"""

import os
import sys

_SRC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
)
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
