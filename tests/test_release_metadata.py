"""Lock release-version sources of truth together.

`pyproject.toml [project] version`, `.claude-plugin/plugin.json version`, and
`.codex-plugin/plugin.json version` must stay in lockstep so a `v*` release
can't ship with a stale plugin manifest (issue #284 — the Claude manifest sat
at 0.1.0 across v1.0.0 -> v1.2.0, which made `/plugin update` surface a cached
old label instead of the real release; the Codex manifest added in #437 is
just as exposed to the same drift if it isn't checked here too).

The same lockstep applies to every *user-facing* surface that names a version.
The website sat at **v1.14.4 for two releases** — 1.15.0 and 1.15.1 — while the
package, both plugin manifests, the README and the cookbook were all current.
The release checklist asks for those files by name; a checklist line is a request
that someone remember, and nobody did.

Worse, it looked automatic: the element carries `id="site-version"` and links to
`/releases/latest`, so a reader would reasonably assume it resolves the version
rather than hard-coding it.

Stdlib-only by policy: `tomllib` + `json` + `re`.
"""

from __future__ import annotations

import json
import re
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


#: Every user-facing surface that names a version, with the pattern that finds it.
#:
#: Anchored on surrounding context rather than scanning for anything shaped like a
#: version: `website/app.js` contains numbers such as `1.19.214` and `1.12.566` in
#: its demo data, and a blanket scan would either fail on those or be weakened
#: until it caught nothing. Adding a surface is a line here, and that line is the
#: review.
SITE_SURFACES = [
    ("website/index.html", r'id="site-version"[^>]*>v(\d+\.\d+\.\d+)</a>'),
    ("website/app.js", r"rev: v(\d+\.\d+\.\d+)"),
    ("README.md", r"rev: v(\d+\.\d+\.\d+)"),
    ("README.md", r"Active \(v(\d+\.\d+\.\d+)\)"),
    ("docs/cookbook.md", r"rev: v(\d+\.\d+\.\d+)"),
]


class NoUserFacingSurfaceCarriesAStaleVersion(unittest.TestCase):
    """What a visitor is told the current release is, checked against what it is."""

    def setUp(self):
        with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
            self.version = tomllib.load(handle)["project"]["version"]

    def test_every_surface_names_the_current_version(self):
        for rel, pattern in SITE_SURFACES:
            with self.subTest(path=rel, pattern=pattern):
                path = REPO_ROOT / rel
                self.assertTrue(path.exists(), f"{rel} is listed here but missing")
                found = re.findall(pattern, path.read_text(encoding="utf-8"))
                self.assertTrue(found, f"pattern matched nothing in {rel}; the surface moved")
                self.assertEqual(
                    set(found),
                    {self.version},
                    f"{rel} tells visitors a version the package is not",
                )

    def test_the_website_is_covered(self):
        """Vacuity: the site is the surface that actually went stale.

        A table that quietly stopped listing it would make the check above pass
        while the exact failure it exists for went unnoticed again.
        """
        covered = {rel for rel, _ in SITE_SURFACES}
        self.assertIn("website/index.html", covered)
        self.assertIn("website/app.js", covered)
