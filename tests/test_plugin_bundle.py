"""`plugin/` is a folder both plugin directories can accept as it stands.

The root marketplace entry is `"source": "./"`, so the plugin a Claude installer
received was the whole repository: hundreds of files, a changelog over 400 KB, binary
images. Anthropic's Claude plugin directory holds a submission for any of those, and
OpenAI's shared ChatGPT + Codex directory wants a ZIP of one folder with a portable
`plugin.json` at its root. `plugin/` is that folder, and `scripts/plugin_bundle.py`
regenerates the parts of it that are copies of root files.

Root `skills/` cannot move — Antigravity only discovers a root `skills/`
(`tests/test_plugin_component_layout.py`, #775) — so the bundle duplicates it, and a
duplicate is only safe while something fails when the two disagree. That is the first
class below. The rest pin the directories' published limits, read 2026-10-02 from
claude.com/docs/plugins/pre-submission-checklist and developers.openai.com/codex/plugins/
build: a limit nobody checks is one the next edit crosses without noticing, and the
symptom is a reviewer hold weeks later rather than a red test now.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
import unittest.mock
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "src"))

import plugin_bundle  # noqa: E402

from ai_jury.redaction import redact  # noqa: E402

BUNDLE = REPO_ROOT / plugin_bundle.BUNDLE_DIR

#: Claude directory: a non-image, non-font file over this size is held for review.
MAX_FILE_BYTES = 256 * 1024
#: Claude directory: more files than this is held for review.
MAX_FILES = 512
#: Names the Claude directory reserves.
RESERVED_NAMES = ("claude", "anthropic", "official", "plugin", "mcp", "test")
#: Portable plugin name: lowercase words joined by hyphens, at most 64 characters.
PORTABLE_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _load(rel: str) -> dict:
    return json.loads((BUNDLE / rel).read_text(encoding="utf-8"))


def _pyproject_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]["version"]


class TheBundleIsACopyOfTheRoot(unittest.TestCase):
    """The skill the directories ship has to be the skill this repository maintains."""

    def test_every_generated_file_matches_what_the_root_would_generate(self):
        self.assertEqual(
            [], plugin_bundle.drift(), "run `python3 scripts/plugin_bundle.py` and commit"
        )

    def test_the_skill_is_byte_identical_to_the_root_skill(self):
        """Stated outright, not only through `drift`: this is the copy that matters."""
        self.assertEqual(
            (BUNDLE / "skills/ai-jury/SKILL.md").read_bytes(),
            (REPO_ROOT / "skills/ai-jury/SKILL.md").read_bytes(),
        )

    def test_every_link_in_the_skill_resolves_inside_the_folder(self):
        """An installer gets only `plugin/`; a link out of it is a dead link there."""
        skill = BUNDLE / "skills/ai-jury/SKILL.md"
        targets = re.findall(r"\]\(([^)#\s]+)", skill.read_text(encoding="utf-8"))
        local = [t for t in targets if "://" not in t]
        self.assertTrue(local, "the skill links nothing — this check went vacuous")
        for target in local:
            with self.subTest(target=target):
                resolved = (skill.parent / target).resolve()
                self.assertTrue(resolved.is_relative_to(BUNDLE.resolve()), target)
                self.assertTrue(resolved.is_file(), target)

    def test_the_check_reports_a_drifted_copy(self):
        """Vacuity: a `drift` that always returned [] would pass the first test."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for src in {*plugin_bundle.COPIES.values(), ".claude-plugin/plugin.json"}:
                (root / src).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPO_ROOT / src, root / src)
            shutil.copytree(BUNDLE, root / plugin_bundle.BUNDLE_DIR)
            self.assertEqual([], plugin_bundle.drift(root))

            (root / "skills/ai-jury/SKILL.md").write_text("edited\n", encoding="utf-8")
            self.assertEqual(
                ["plugin/skills/ai-jury/SKILL.md differs from its source"],
                plugin_bundle.drift(root),
            )
            self.assertEqual(["plugin/skills/ai-jury/SKILL.md"], plugin_bundle.write(root))
            self.assertEqual([], plugin_bundle.drift(root))

    @unittest.skipUnless(
        shutil.which("git") and (REPO_ROOT / ".git").exists(), "needs a git checkout"
    )
    def test_every_compared_file_is_checked_out_with_lf(self):
        """Windows CI checks text out with CRLF (core.autocrlf). A byte copy survives that
        because its source converts too, but the generated `plugin.json` is written with
        "\n" and its committed copy did not — the first Windows run failed on exactly that.
        `.gitattributes` pins `eol=lf` on both sides, which also keeps the upload ZIP the
        same bytes on every OS; a new copy added to `COPIES` must be covered as well."""
        paths = sorted(
            {*(f"{plugin_bundle.BUNDLE_DIR}/{rel}" for rel in plugin_bundle.expected_files())}
            | set(plugin_bundle.COPIES.values())
        )
        out = subprocess.run(
            ["git", "check-attr", "eol", "--", *paths],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        unpinned = [line for line in out.splitlines() if not line.endswith(": eol: lf")]
        self.assertEqual([], unpinned, "add these to .gitattributes with `text eol=lf`")

    def test_the_check_command_exits_nonzero_on_drift(self):
        with (
            unittest.mock.patch.object(plugin_bundle, "drift", return_value=["x"]),
            unittest.mock.patch("sys.stderr"),
        ):
            self.assertEqual(1, plugin_bundle.main(["--check"]))
            self.assertEqual(1, plugin_bundle.main(["zip"]))


class TheFolderFitsTheClaudeDirectory(unittest.TestCase):
    def _files(self) -> list[Path]:
        return sorted(p for p in BUNDLE.rglob("*") if p.is_file() or p.is_symlink())

    def test_nothing_in_it_is_a_symlink(self):
        links = [p for p in BUNDLE.rglob("*") if p.is_symlink()]
        self.assertEqual([], links)

    def test_it_is_small(self):
        files = self._files()
        self.assertLessEqual(len(files), MAX_FILES)
        large = [str(p) for p in files if p.stat().st_size > MAX_FILE_BYTES]
        self.assertEqual([], large)

    def test_every_file_is_text(self):
        """No binaries at all — `.ico`, `.pdf`, `.zip` are held, and none is needed."""
        for path in self._files():
            with self.subTest(path=path.relative_to(BUNDLE).as_posix()):
                data = path.read_bytes()
                self.assertNotIn(b"\x00", data)
                data.decode("utf-8")

    def test_no_hidden_file_but_the_manifest_directory(self):
        """`.DS_Store` and friends are blocked; `.claude-plugin/` is the one dot-path."""
        hidden = []
        for path in self._files():
            parts = path.relative_to(BUNDLE).parts
            if parts[0] == ".claude-plugin":
                parts = parts[1:]
            if any(part.startswith(".") for part in parts):
                hidden.append(path.relative_to(BUNDLE).as_posix())
        self.assertEqual([], hidden)

    def test_no_file_carries_a_credential(self):
        """The repository's own redactor, which already knows sixteen secret shapes."""
        for path in self._files():
            with self.subTest(path=path.relative_to(BUNDLE).as_posix()):
                self.assertEqual(0, redact(path.read_text(encoding="utf-8"))[1])

    def test_the_readme_has_at_least_forty_words_of_prose(self):
        """Words inside code blocks do not count toward the directory's minimum."""
        text = (BUNDLE / "README.md").read_text(encoding="utf-8")
        prose = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
        prose = re.sub(r"`[^`]*`", " ", prose)
        self.assertGreaterEqual(len(re.findall(r"[A-Za-z]{2,}", prose)), 40)

    def test_the_readme_discloses_what_it_runs_sends_and_writes(self):
        """The security scan compares the README with what the plugin does."""
        text = (BUNDLE / "README.md").read_text(encoding="utf-8")
        self.assertIn("## Data flow", text)
        for needle in (
            "installed separately",
            "pipx install ai-jury",
            "What it reads",
            "What it sends",
            "What it writes",
            "ANTHROPIC_API_KEY",
            "`--post`",
            "telemetry",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_there_is_a_license(self):
        self.assertEqual((BUNDLE / "LICENSE").read_bytes(), (REPO_ROOT / "LICENSE").read_bytes())
        self.assertEqual("MIT", _load(".claude-plugin/plugin.json")["license"])

    def test_the_name_is_not_reserved(self):
        name = _load(".claude-plugin/plugin.json")["name"]
        self.assertNotIn(name, RESERVED_NAMES)

    def test_every_manifest_path_stays_inside_the_folder(self):
        claude = _load(".claude-plugin/plugin.json")
        interface = _load("plugin.json")["extensions"]["com.openai"]["interface"]
        paths = [claude["skills"], interface["logo"], interface["composerIcon"]]
        for rel in paths:
            with self.subTest(path=rel):
                self.assertTrue(rel.startswith("./"), rel)
                resolved = (BUNDLE / rel).resolve()
                self.assertTrue(resolved.is_relative_to(BUNDLE.resolve()), rel)
                self.assertTrue(resolved.exists(), rel)

    def test_every_skill_has_frontmatter_with_a_string_description(self):
        skills = sorted((BUNDLE / "skills").glob("*/SKILL.md"))
        self.assertTrue(skills)
        for skill in skills:
            with self.subTest(skill=skill.parent.name):
                match = re.match(r"---\n(.*?)\n---\n", skill.read_text(encoding="utf-8"), re.S)
                self.assertIsNotNone(match, "no YAML frontmatter")
                fields = dict(
                    line.split(":", 1) for line in match.group(1).splitlines() if ":" in line
                )
                self.assertEqual(skill.parent.name, fields.get("name", "").strip())
                description = fields.get("description", "").strip()
                self.assertTrue(description)
                self.assertFalse(description.startswith(("[", "{", "|", ">")), description[:20])


class ThePortableManifestFitsTheOpenAIDirectory(unittest.TestCase):
    def setUp(self):
        self.manifest = _load("plugin.json")
        self.interface = self.manifest["extensions"]["com.openai"]["interface"]

    def test_it_names_the_schema(self):
        self.assertEqual(
            "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", self.manifest["$schema"]
        )

    def test_the_identity_fields_are_present_and_well_formed(self):
        m = self.manifest
        self.assertRegex(m["name"], PORTABLE_NAME)
        self.assertLessEqual(len(m["name"]), 64)
        self.assertRegex(m["version"], SEMVER)
        for key in ("description", "homepage", "repository", "license"):
            with self.subTest(key=key):
                self.assertIsInstance(m[key], str)
        self.assertEqual({"name", "url"}, set(m["author"]))
        self.assertTrue(m["keywords"])

    def test_the_interface_respects_the_length_limits(self):
        i = self.interface
        self.assertLessEqual(len(i["displayName"]), 30)
        self.assertLessEqual(len(i["shortDescription"]), 30)
        self.assertLessEqual(len(i["longDescription"]), 4000)
        self.assertLessEqual(len(i["developerName"]), 80)
        self.assertIsInstance(i["capabilities"], list)
        self.assertLessEqual(len(i["defaultPrompt"]), 3)
        for prompt in i["defaultPrompt"]:
            with self.subTest(prompt=prompt):
                self.assertLessEqual(len(prompt), 128)

    def test_the_logo_is_a_square_svg_of_at_least_48(self):
        for key in ("logo", "composerIcon"):
            with self.subTest(key=key):
                svg = (BUNDLE / self.interface[key]).read_text(encoding="utf-8")
                box = re.search(r'viewBox="([\d.\s-]+)"', svg)
                self.assertIsNotNone(box, "an SVG logo needs a viewBox")
                _, _, width, height = (float(v) for v in box.group(1).split())
                self.assertEqual(width, height, "the viewBox is not square")
                self.assertGreaterEqual(width, 48)

    def test_the_folder_has_nothing_the_directory_refuses(self):
        """A ZIP with hooks or an `.app.json` cannot be submitted."""
        names = plugin_bundle.bundle_files()
        self.assertFalse([n for n in names if n.startswith("hooks/") or n.endswith(".app.json")])
        self.assertNotIn("hooks", self.manifest)


class TheVersionIsTheRelease(unittest.TestCase):
    def test_both_bundle_manifests_name_the_pyproject_version(self):
        expected = _pyproject_version()
        for rel in (".claude-plugin/plugin.json", "plugin.json"):
            with self.subTest(manifest=rel):
                self.assertEqual(expected, _load(rel)["version"])


class TheUploadZipIsDeterministic(unittest.TestCase):
    def test_two_builds_are_byte_identical_and_hold_the_portable_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = plugin_bundle.build_zip(Path(tmp) / "a.zip")
            second = plugin_bundle.build_zip(Path(tmp) / "b.zip")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as zf:
                names = zf.namelist()
                self.assertEqual(sorted(names), names)
                self.assertIn("plugin.json", names)
                self.assertIn("skills/ai-jury/SKILL.md", names)
                self.assertIn("assets/logo.svg", names)
                self.assertFalse([n for n in names if n.startswith(".claude-plugin/")])
                self.assertEqual(zf.read("plugin.json"), (BUNDLE / "plugin.json").read_bytes())

    def test_the_zip_lands_in_an_ignored_directory(self):
        self.assertIn("dist/", (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").split())


if __name__ == "__main__":
    unittest.main()
