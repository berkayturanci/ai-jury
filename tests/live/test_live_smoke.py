"""Opt-in live smoke tests for the real native agent CLI adapters.

Unit tests only exercise :class:`MockAdapter`, so the real ``claude``,
``codex`` and ``agy`` CLIs are never invoked. A change to argv format,
stdin handling, or output capture in the concrete adapters therefore goes
unnoticed until a live run.

These smoke tests close that gap by running a trivial real review prompt
through each *installed* adapter. They are intentionally:

* **Opt-in.** The whole suite is skipped unless ``JURY_LIVE=1`` is set
  in the environment, so they never run in the default test suite or CI.
* **Per-agent skippable.** An agent whose CLI is not on ``PATH`` is skipped
  individually, so a machine with only ``claude`` installed still does
  something useful.
* **Cheap and fast.** The prompt is a tiny two-line diff and each adapter
  runs under a short timeout override so a hung CLI fails fast.

Run them with::

    make live-smoke
    # or
    JURY_LIVE=1 PYTHONPATH=src python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from ai_jury.adapters import make_adapter
from ai_jury.config import DEFAULT_CONFIG, _from_dict

# Short per-agent timeout so a hung CLI fails fast instead of blocking the
# whole suite for the production default (600s).
LIVE_TIMEOUT_S = 120

# A tiny, cheap diff to review. Kept deliberately small to minimise token
# spend and wall-clock time during live runs.
SMOKE_PROMPT = (
    "Briefly review this trivial diff and reply in one short sentence.\n"
    "```diff\n"
    "--- a/greet.py\n"
    "+++ b/greet.py\n"
    "@@\n"
    "-print('hi')\n"
    "+print('hello')\n"
    "```\n"
)


@unittest.skipUnless(
    os.environ.get("JURY_LIVE") == "1",
    "live smoke tests disabled; set JURY_LIVE=1",
)
class LiveSmokeTest(unittest.TestCase):
    """Run a trivial real review through each installed native CLI."""

    def _run_agent(self, raw_spec: dict) -> None:
        # _from_dict expects the raw TOML shape with an "agent" array.
        config = _from_dict({"agent": [raw_spec]})
        spec = config.agents[0]
        # Override the timeout so a hung CLI fails fast.
        spec = dataclasses.replace(spec, timeout=LIVE_TIMEOUT_S)
        adapter = make_adapter(spec, mock=False)

        if not adapter.available():
            self.skipTest(f"CLI not installed: {spec.command}")

        result = adapter.run(SMOKE_PROMPT, phase="review")

        self.assertIsNone(
            result.error_code,
            msg=f"{spec.name}: error_code={result.error_code} error={result.error!r}",
        )
        self.assertTrue(
            result.ok,
            msg=f"{spec.name}: run failed: {result.error!r}",
        )
        self.assertTrue(
            result.output.strip(),
            msg=f"{spec.name}: empty output",
        )


@unittest.skipUnless(
    os.environ.get("JURY_LIVE") == "1",
    "live smoke tests disabled; set JURY_LIVE=1",
)
class LiveRunAgentTest(unittest.TestCase):
    """Drive each installed CLI once through `jury run-agent --role review`.

    The unit suite proves the role policy against mocked adapters, which cannot
    catch a flag a real CLI has since renamed or given an arity. This runs the
    whole command — argv, stdin, capture, JSON export — against the live binary,
    in the read-only role only: a live test must never be given write access.
    """

    def _run_agent(self, name: str) -> None:
        from ai_jury.cli import main

        spec = _from_dict({"agent": [dict(_by_name(name))]}).agents[0]
        adapter = make_adapter(dataclasses.replace(spec, timeout=LIVE_TIMEOUT_S), mock=False)
        if not adapter.available():
            self.skipTest(f"CLI not installed: {spec.command}")

        with tempfile.TemporaryDirectory() as tmp:
            prompt = Path(tmp) / "prompt.md"
            prompt.write_text(SMOKE_PROMPT, encoding="utf-8")
            config = Path(tmp) / "jury.toml"
            config.write_text(_toml_for(name), encoding="utf-8")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = main(
                    [
                        "run-agent",
                        "--agent",
                        name,
                        "--role",
                        "review",
                        "--prompt-file",
                        str(prompt),
                        "--config",
                        str(config),
                        "--timeout",
                        str(LIVE_TIMEOUT_S),
                    ]
                )

        document = json.loads(buffer.getvalue())
        if document.get("error_code") in ("auth_required", "missing_api_key"):
            # An installed-but-not-logged-in CLI is the same situation as one
            # that is not installed, and this module's contract is that such an
            # agent is skipped individually rather than failing the suite. The
            # skip is keyed off the TYPED error code the command already
            # reports, so a real failure still fails.
            self.skipTest(f"{spec.command} is installed but not authenticated")
        self.assertEqual(code, 0, msg=f"{name}: {document.get('error')!r}")
        self.assertTrue(document["ok"], msg=f"{name}: {document.get('error')!r}")
        self.assertTrue(document["text"].strip(), msg=f"{name}: empty text")
        self.assertEqual(document["role"], "review")
        self.assertEqual(document["schema_version"], "ai-jury.run-agent.v1")
        self.assertTrue(document["attribution"]["label"].startswith("agent:"))


def _by_name(name: str) -> dict:
    return next(raw for raw in DEFAULT_CONFIG["agent"] if raw["name"] == name)


def _toml_for(name: str) -> str:
    """A single-agent jury.toml for `name`, from the shipped default entry."""
    raw = _by_name(name)
    args = ", ".join(json.dumps(a) for a in raw.get("extra_args", []))
    return (
        "[[agent]]\n"
        f'name = "{raw["name"]}"\n'
        f'vendor = "{raw["vendor"]}"\n'
        f'command = "{raw["command"]}"\n'
        f"extra_args = [{args}]\n"
    )


def _make_test(raw_spec: dict):
    def _test(self: LiveSmokeTest) -> None:
        self._run_agent(raw_spec)

    return _test


# Generate one test method per agent in the default config, so the suite
# stays in sync with the real adapter set. Each agent is skipped
# individually when its CLI is not installed.
for _raw in DEFAULT_CONFIG["agent"]:
    setattr(
        LiveSmokeTest,
        f"test_{_raw['name']}_smoke",
        _make_test(dict(_raw)),
    )


def _make_run_agent_test(name: str):
    def _test(self: LiveRunAgentTest) -> None:
        self._run_agent(name)

    return _test


# One `jury run-agent --role review` per agent in the default config (#661).
for _raw in DEFAULT_CONFIG["agent"]:
    setattr(
        LiveRunAgentTest,
        f"test_{_raw['name']}_run_agent_review",
        _make_run_agent_test(_raw["name"]),
    )


if __name__ == "__main__":
    unittest.main()
