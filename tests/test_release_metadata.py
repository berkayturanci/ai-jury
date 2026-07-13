"""Lock release-version sources of truth together.

`pyproject.toml [project] version`, `.claude-plugin/plugin.json version`, and
`.codex-plugin/plugin.json version` must stay in lockstep so a `v*` release
can't ship with a stale plugin manifest (issue #284 — the Claude manifest sat
at 0.1.0 across v1.0.0 -> v1.2.0, which made `/plugin update` surface a cached
old label instead of the real release; the Codex manifest added in #437 is
just as exposed to the same drift if it isn't checked here too).

Stdlib-only by policy: `tomllib` + `json`.
"""

from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class ReleaseVersionLockstep(unittest.TestCase):
    def test_pyproject_and_plugin_versions_match(self) -> None:
        with (REPO_ROOT / "pyproject.toml").open("rb") as f:
            pyproject_version = tomllib.load(f)["project"]["version"]
        with (REPO_ROOT / ".claude-plugin" / "plugin.json").open("rb") as f:
            plugin_version = json.load(f)["version"]
        self.assertEqual(
            pyproject_version,
            plugin_version,
            "pyproject.toml version and .claude-plugin/plugin.json version "
            "have drifted. Bump both in the same release-prep PR (#284).",
        )

    def test_pyproject_and_codex_plugin_versions_match(self) -> None:
        with (REPO_ROOT / "pyproject.toml").open("rb") as f:
            pyproject_version = tomllib.load(f)["project"]["version"]
        with (REPO_ROOT / ".codex-plugin" / "plugin.json").open("rb") as f:
            plugin_version = json.load(f)["version"]
        self.assertEqual(
            pyproject_version,
            plugin_version,
            "pyproject.toml version and .codex-plugin/plugin.json version "
            "have drifted. Bump both in the same release-prep PR (#437).",
        )


if __name__ == "__main__":
    unittest.main()
