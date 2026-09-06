"""The one release-surface table, and the three guards that must read it.

`RELEASE_SURFACES` replaced three disjoint lists — `VERSION_MARKERS` in
`scripts/verify_merge.py`, `SITE_SURFACES` in `tests/test_release_metadata.py`,
and two hard-coded assertions in `tests/test_homebrew_formula.py`. Consolidating
them is only worth anything if every reader really reads the table, so the tests
below monkeypatch it and run the guards themselves:

* remove an entry and every guard must stop looking at that file, even when it
  is visibly stale;
* add an entry and every guard must start looking at it.

A guard that kept a private copy would pass one direction and fail the other,
which is the failure this issue exists to make impossible (#665).

The three are `check_version_integrity` (the merge gate), `check_release_surfaces`
(`make release-check`, which is a different command with a different failure mode
— it does not require git tags) and `tests/test_release_metadata.py`. Three entry
points, two readers: the last two both call `release_surfaces.mismatches()` and
differ only in where the expected version comes from, so what the tests below
prove is that every *entry point* follows the table, not that three independent
scanners exist — every reader funnels through `_scan`, by design (#665). The formula
test was the third until #666 deleted `Formula/ai-jury.rb`: a committed formula
names an sdist url and digest that cannot be known before the tag, so the file
could not be made correct and was removed rather than repaired again. Nothing in
`tests/test_homebrew_formula.py` reads the table now — it checks that rendering
`packaging/homebrew/ai-jury.rb.template` produces a formula, and a template whose
version is the literal `@VERSION@` cannot go stale.

The fixture is a miniature repository — one file per surface, all naming the
same version, with a matching git tag — so the guards can be pointed at it
instead of at the real tree.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import release_surfaces  # noqa: E402
import verify_merge  # noqa: E402

from ai_jury import __version__  # noqa: E402

#: A version-bearing string the table does not list, planted in a file the table
#: *does* list. Unlisted is unwatched, which is the whole failure class —
#: `test_adding_an_entry_...` registers it and every guard has to start objecting.
#:
#: It lived in the fixture formula until #666 deleted the real one; any listed
#: file does as well, now that no guard reads a single file of its own.
UNLISTED_MARKER = "pinned: ai-jury==9.9.9"

#: One file per surface path in the table, each naming `{v}`. Written out rather
#: than derived from the patterns: a fixture generated from the thing under test
#: passes by construction.
FIXTURE_FILES = {
    "pyproject.toml": '[project]\nname = "ai-jury"\nversion = "{v}"\n',
    "src/ai_jury/__init__.py": '__version__ = "{v}"\n',
    "CHANGELOG.md": "# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n## [{v}] - 2026-09-01\n",
    "uv.lock": '[[package]]\nname = "ai-jury"\nversion = "{v}"\n',
    ".claude-plugin/plugin.json": '{{"name": "ai-jury", "version": "{v}"}}\n',
    ".codex-plugin/plugin.json": '{{"name": "ai-jury", "version": "{v}"}}\n',
    "website/index.html": '<a class="ver" id="site-version" href="/latest">v{v}</a>\n',
    "website/app.js": 'config: "repo: x\\n    rev: v{v}\\n"\n',
    "README.md": "    rev: v{v}\n\nActive (v{v}).\n",
    "docs/cookbook.md": "    rev: v{v}\n\n" + UNLISTED_MARKER + "\n",
    "docs/configuration.md": '  "tool_version": "{v}",\n',
}

#: Where that marker sits, and the entry that would register it.
UNLISTED_PATH = "docs/cookbook.md"
UNLISTED_ENTRY = ("docs/cookbook.md", "site", r"pinned: ai-jury==(\d+\.\d+\.\d+)")


def _write(root: Path, rel: str, version: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FIXTURE_FILES[rel].format(v=version), encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _fixture(root: Path, version: str = __version__) -> None:
    """A tree where every surface names `version`, tagged at that version.

    Tagged because `check_version_integrity` treats "no v* tags" as a failure —
    a shallow checkout is not evidence the version is fine (#556) — so an untagged
    fixture would report an error unrelated to what these tests are asking about.
    """
    for rel in FIXTURE_FILES:
        _write(root, rel, version)
    _git(root, "init", "-q", ".")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "fixture")
    _git(root, "tag", f"v{version}")


def _guard_failures(root: Path) -> dict[str, list[str]]:
    """Run all three guards against `root` and collect what each objected to.

    `check_version_integrity` and `check_release_surfaces` are separate commands
    with separate failure modes — the merge gate also compares against git tags,
    `make release-check` deliberately does not — so a table read by one and not
    the other would still let a surface go unwatched from a shallow clone. (The
    release-check and the metadata test share `mismatches()`; they are two entry
    points over one reader, see the module docstring.)

    The test-module guard is executed as the real `TestCase` method it is, with
    its `REPO_ROOT` pointed at the fixture. Re-deriving what it would have said
    would be testing a paraphrase of it.
    """
    failures = {
        "verify_merge": verify_merge.check_version_integrity(root),
        "release_check": verify_merge.check_release_surfaces(root),
    }
    for module_name, case_name, method in (
        (
            "test_release_metadata",
            "NoUserFacingSurfaceCarriesAStaleVersion",
            "test_every_surface_names_the_current_version",
        ),
    ):
        module = importlib.import_module(module_name)
        with unittest.mock.patch.object(module, "REPO_ROOT", root):
            result = unittest.TestResult()
            getattr(module, case_name)(method).run(result)
        failures[module_name] = [message for _, message in result.failures + result.errors]
    return failures


class TheTableDescribesThisTree(unittest.TestCase):
    """The table is only useful if it describes the repository it ships in."""

    def test_every_listed_path_exists(self):
        missing = [
            surface.path
            for surface in release_surfaces.RELEASE_SURFACES
            if not (REPO_ROOT / surface.path).is_file()
        ]
        self.assertEqual(missing, [], "the table lists files this repository does not have")

    def test_every_surface_names_exactly_the_declared_version(self):
        found = release_surfaces.find_versions(REPO_ROOT)
        self.assertEqual(
            {path: sorted(versions) for path, versions in found.items()},
            {path: [__version__] for path in release_surfaces.surface_paths()},
            "a surface names something other than the version the package declares",
        )

    def test_the_declared_version_is_read_from_pyproject(self):
        self.assertEqual(release_surfaces.declared_version(REPO_ROOT), __version__)

    def test_the_surfaces_that_have_gone_stale_before_are_all_listed(self):
        """Vacuity, per incident. Each of these was a real release that shipped wrong."""
        covered = {surface.path for surface in release_surfaces.RELEASE_SURFACES}
        for path in (
            "uv.lock",  # #556
            "website/index.html",  # #646
            "website/app.js",  # #646
            ".claude-plugin/plugin.json",  # #284
            ".codex-plugin/plugin.json",  # #437
        ):
            with self.subTest(path=path):
                self.assertIn(path, covered)

    def test_the_formula_is_not_a_surface_because_there_is_no_formula(self):
        """#562 was a formula surface; #666 removed the file rather than the symptom.

        A committed formula names an sdist url and digest that cannot be known
        until the tag is pushed, so it could not be correct and needed a second
        write to `main` after every release. What is left is a template naming
        `@VERSION@`, which no guard needs to watch because a placeholder cannot
        go stale. Listing it would fail every run.
        """
        covered = {surface.path for surface in release_surfaces.RELEASE_SURFACES}
        self.assertNotIn("Formula/ai-jury.rb", covered)
        self.assertFalse((REPO_ROOT / "Formula" / "ai-jury.rb").exists())
        template = REPO_ROOT / "packaging" / "homebrew" / "ai-jury.rb.template"
        self.assertIn("@VERSION@", template.read_text(encoding="utf-8"))
        self.assertNotIn("packaging/homebrew/ai-jury.rb.template", release_surfaces.surface_paths())

    def test_the_fixture_covers_every_surface(self):
        """Otherwise a new surface would be silently untested by everything below."""
        self.assertEqual(sorted(FIXTURE_FILES), sorted(release_surfaces.surface_paths()))


class ReadingTheTable(unittest.TestCase):
    def test_the_changelog_reads_only_its_top_release_section(self):
        text = "## [Unreleased]\n\n## [2.0.0] - 2026-09-01\n\n## [1.9.9] - 2026-08-01\n"
        self.assertEqual(release_surfaces._changelog_top_section(text), ["2.0.0"])

    def test_a_changelog_with_no_release_yet_names_nothing(self):
        self.assertEqual(release_surfaces._changelog_top_section("## [Unreleased]\n"), [])

    def test_a_manifest_without_a_version_names_nothing(self):
        self.assertEqual(release_surfaces._json_version('{"name": "ai-jury"}'), [])

    def test_a_surface_reports_every_reading_it_finds(self):
        surface = release_surfaces.Surface("README.md", "site", r"rev: v(\d+\.\d+\.\d+)")
        self.assertEqual(surface.find("rev: v1.0.0\nrev: v2.0.0\n"), ["1.0.0", "2.0.0"])

    def test_the_paths_are_the_distinct_ones_in_table_order(self):
        """`README.md` is listed twice and must contribute one entry, where it first appears."""
        table = (
            release_surfaces.Surface("README.md", "site", r"rev: v(\d+)"),
            release_surfaces.Surface("CHANGELOG.md", "changelog", r"v(\d+)"),
            release_surfaces.Surface("README.md", "site", r"Active \(v(\d+)\)"),
        )
        with unittest.mock.patch.object(release_surfaces, "RELEASE_SURFACES", table):
            self.assertEqual(release_surfaces.surface_paths(), ("README.md", "CHANGELOG.md"))

    def test_the_paths_follow_the_table_rather_than_a_copy_taken_at_import(self):
        """#696: the guards substitute the table, so a frozen copy would name files
        the table no longer lists — a success line describing a comparison that did
        not happen, which is the defect #556 was about."""
        with unittest.mock.patch.object(release_surfaces, "RELEASE_SURFACES", ()):
            self.assertEqual(release_surfaces.surface_paths(), ())

    def test_declared_version_is_none_without_a_pyproject(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(release_surfaces.declared_version(Path(tmp)))


class WhatTheTableReportsAboutATree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        _fixture(self.root)

    def test_a_coherent_tree_is_clean(self):
        self.assertEqual(release_surfaces.mismatches(self.root, __version__), [])
        self.assertEqual(release_surfaces.problems(self.root), [])

    def test_one_stale_surface_is_named(self):
        _write(self.root, "website/app.js", "9.9.9")
        errors = release_surfaces.mismatches(self.root, __version__)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("website/app.js", errors[0])
        self.assertIn("9.9.9", errors[0])

    def test_a_surface_whose_pattern_stopped_matching_is_named(self):
        (self.root / "website" / "app.js").write_text("nothing here\n", encoding="utf-8")
        self.assertIn(
            "website/app.js: no version found, so the surface moved and nothing is watching it",
            release_surfaces.problems(self.root),
        )

    def test_a_missing_optional_surface_is_drift_for_a_release_but_not_for_a_checkout(self):
        """`verify_merge` runs against arbitrary trees; a release runs against one."""
        (self.root / "uv.lock").unlink()
        self.assertEqual(release_surfaces.problems(self.root), [])
        self.assertIn(
            "uv.lock is listed as a release surface but is missing",
            release_surfaces.mismatches(self.root, __version__),
        )

    def test_a_missing_required_surface_is_always_drift(self):
        (self.root / "CHANGELOG.md").unlink()
        self.assertIn("CHANGELOG.md not found", release_surfaces.problems(self.root))

    def test_a_path_listed_twice_contributes_both_readings(self):
        (self.root / "README.md").write_text(
            f"    rev: v{__version__}\n\nActive (v9.9.9).\n", encoding="utf-8"
        )
        self.assertEqual(
            release_surfaces.find_versions(self.root)["README.md"], {__version__, "9.9.9"}
        )


class AllThreeGuardsReadTheSameTable(unittest.TestCase):
    """The acceptance criterion for #665, asked of the guards themselves."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        _fixture(self.root)

    def _patch(self, table):
        patcher = unittest.mock.patch.object(release_surfaces, "RELEASE_SURFACES", table)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_fixture_satisfies_all_three_guards_to_begin_with(self):
        """Vacuity: if the fixture failed anyway, neither test below would mean much."""
        failures = _guard_failures(self.root)
        self.assertEqual(failures, dict.fromkeys(failures, []))

    def test_removing_an_entry_makes_every_guard_ignore_that_file(self):
        _write(self.root, "website/app.js", "9.9.9")
        # Listed twice under one path, so dropping the path must drop both rows.
        _write(self.root, "README.md", "9.9.9")

        with_it = _guard_failures(self.root)
        for guard, failures in with_it.items():
            with self.subTest(guard=guard, listed=True):
                self.assertNotEqual(failures, [], f"{guard} did not object to a stale surface")

        self._patch(
            tuple(
                surface
                for surface in release_surfaces.RELEASE_SURFACES
                if surface.path not in ("website/app.js", "README.md")
            )
        )
        for guard, failures in _guard_failures(self.root).items():
            with self.subTest(guard=guard, listed=False):
                self.assertEqual(failures, [], f"{guard} still checks a file the table dropped")

    def test_adding_an_entry_makes_every_guard_check_that_file(self):
        # The fixture cookbook already carries a stale pin; nothing looks at it,
        # because looking at it is what registering it means.
        self.assertIn(UNLISTED_MARKER, (self.root / UNLISTED_PATH).read_text(encoding="utf-8"))
        for guard, failures in _guard_failures(self.root).items():
            with self.subTest(guard=guard, listed=False):
                self.assertEqual(failures, [], f"{guard} objected before the marker was listed")

        self._patch((*release_surfaces.RELEASE_SURFACES, release_surfaces.Surface(*UNLISTED_ENTRY)))
        for guard, failures in _guard_failures(self.root).items():
            with self.subTest(guard=guard, listed=True):
                self.assertNotEqual(failures, [], f"{guard} ignored a newly listed surface")


class TheReleaseCheckEntryPoint(unittest.TestCase):
    """`make release-check` is one command, so there is one thing to run and fail."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        _fixture(self.root)

    def test_a_coherent_tree_passes(self):
        self.assertEqual(verify_merge.check_release_surfaces(self.root), [])
        self.assertEqual(verify_merge.main(["--root", str(self.root), "--check-surfaces"]), 0)

    def test_a_stale_surface_fails(self):
        _write(self.root, "website/index.html", "9.9.9")
        self.assertEqual(verify_merge.main(["--root", str(self.root), "--check-surfaces"]), 1)

    def test_a_tree_with_no_declared_version_fails_rather_than_passing_vacuously(self):
        (self.root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        errors = verify_merge.check_release_surfaces(self.root)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("pyproject.toml", errors[0])

    def test_check_surfaces_alone_does_not_also_demand_git_tags(self):
        """A maintainer's shallow clone must still be able to run the check."""
        _git(self.root, "tag", "-d", f"v{__version__}")
        self.assertEqual(verify_merge.main(["--root", str(self.root), "--check-surfaces"]), 0)

    def test_the_makefile_exposes_it(self):
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("release-check:", makefile)
        self.assertIn("verify_merge.py --check-surfaces", makefile)


class TheChecklistPointsAtTheTable(unittest.TestCase):
    """The docs used to name eight files, which is a ninth list to keep in step."""

    def test_the_checklist_names_the_command_and_the_table(self):
        checklist = (REPO_ROOT / "docs" / "release-checklist.md").read_text(encoding="utf-8")
        self.assertIn("make release-check", checklist)
        self.assertIn("scripts/release_surfaces.py", checklist)

    def test_releasing_mentions_the_table(self):
        releasing = (REPO_ROOT / "docs" / "releasing.md").read_text(encoding="utf-8")
        self.assertIn("scripts/release_surfaces.py", releasing)


if __name__ == "__main__":
    unittest.main()
