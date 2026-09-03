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

Which files those are is no longer decided here. `scripts/release_surfaces.py`
holds the one table, shared with `scripts/verify_merge.py` and its
`--check-surfaces` entry point; this module used to keep a third copy called
`SITE_SURFACES`, and a surface registered in one list and not the others is
exactly how the website went stale (#665).

Stdlib-only by policy: `tomllib` + `json`.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import release_surfaces  # noqa: E402


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


class NoUserFacingSurfaceCarriesAStaleVersion(unittest.TestCase):
    """What a visitor is told the current release is, checked against what it is."""

    def setUp(self):
        with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
            self.version = tomllib.load(handle)["project"]["version"]

    def test_every_surface_names_the_current_version(self):
        self.assertEqual(
            [],
            release_surfaces.mismatches(REPO_ROOT, self.version),
            "a release surface names a version the package is not; "
            "the table is scripts/release_surfaces.py",
        )

    def test_the_website_is_covered(self):
        """Vacuity: the site is the surface that actually went stale.

        A table that quietly stopped listing it would make the check above pass
        while the exact failure it exists for went unnoticed again.
        """
        covered = {surface.path for surface in release_surfaces.RELEASE_SURFACES}
        self.assertIn("website/index.html", covered)
        self.assertIn("website/app.js", covered)


class PackagingMetadataIsComplete(unittest.TestCase):
    """The `[project]` fields the release checklist claims are correct.

    The checklist's first "Required before public" box names seven of them and
    cited *this module* as the thing that pins them (#686). It did not: every
    test above is about version lockstep, so the box pointed at a guard that was
    not watching. Rather than weaken the anchor to "checked by hand once", these
    are the assertions that make it true.

    Each one is drift a release would ship silently: a `requires-python` bump
    that leaves the classifiers claiming a version the code no longer runs on,
    a renamed `cli.main` that turns the `jury` entry point into an ImportError
    at install time, a dropped `project.urls` that empties the PyPI sidebar.
    """

    @classmethod
    def setUpClass(cls) -> None:
        with (REPO_ROOT / "pyproject.toml").open("rb") as f:
            cls.project = tomllib.load(f)["project"]

    def test_the_identifying_fields_are_present(self):
        self.assertEqual(self.project["name"], "ai-jury")
        self.assertTrue(self.project["description"].strip())
        self.assertEqual(self.project["license"], "MIT")
        self.assertEqual(self.project["readme"], "README.md")

    def test_the_pypi_sidebar_links_are_present(self):
        self.assertLessEqual({"Homepage", "Issues"}, set(self.project["urls"]))
        for name, url in self.project["urls"].items():
            with self.subTest(url=name):
                self.assertTrue(url.startswith("https://"), url)

    def test_the_console_script_points_at_something_that_exists(self):
        self.assertEqual(self.project["scripts"], {"jury": "ai_jury.cli:main"})
        module, _, attribute = self.project["scripts"]["jury"].partition(":")
        source = REPO_ROOT / "src" / Path(*module.split(".")).with_suffix(".py")
        self.assertTrue(source.is_file(), f"{source} does not exist")
        self.assertIn(
            f"def {attribute}(",
            source.read_text(encoding="utf-8"),
            f"{module} defines no {attribute}(), so the `jury` entry point cannot resolve",
        )

    def test_the_classifiers_agree_with_requires_python(self):
        """Bumping the floor and leaving a stale classifier is the drift here."""
        floor = self.project["requires-python"]
        match = re.fullmatch(r">=3\.(\d+)", floor)
        self.assertIsNotNone(match, f"unexpected requires-python spelling: {floor!r}")
        minimum = int(match.group(1))

        supported = re.compile(r"Programming Language :: Python :: 3\.(\d+)")
        declared = sorted(
            int(found.group(1))
            for found in (supported.fullmatch(c) for c in self.project["classifiers"])
            if found
        )
        self.assertTrue(declared, "no `Programming Language :: Python :: 3.x` classifier")
        self.assertEqual(
            declared[0],
            minimum,
            f"requires-python is {floor} but the lowest declared classifier is 3.{declared[0]}",
        )
        self.assertEqual(
            declared,
            list(range(declared[0], declared[-1] + 1)),
            f"the declared Python classifiers have a gap: {declared}",
        )


if __name__ == "__main__":
    unittest.main()
