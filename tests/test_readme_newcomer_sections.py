"""The README answers a newcomer's first questions, and each answer is the code's (#875).

The pre-launch audit found no uninstall steps, no word on what a run costs, nothing
on Windows although CI runs there, and three contributor sections — coverage, live
smoke tests, the benchmark — standing between Install and Usage. Each check below
derives what the README must say from the thing it describes rather than from a
copy: the install routes from the Install block and `install.sh`, the leftover
directories from the functions that choose them, the call count from a mock run,
the vendor list from `config.KNOWN_VENDORS`, and the Windows leg from `ci.yml`.
"""

from __future__ import annotations

import re
import sys
import unittest
import unittest.mock as mock
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_jury import cache, configtrust  # noqa: E402
from ai_jury.adapters import MockAdapter  # noqa: E402
from ai_jury.config import KNOWN_VENDORS, _from_dict  # noqa: E402
from ai_jury.orchestrator import run_jury  # noqa: E402

README = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
INSTALLER = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
CI = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
SAMPLE_DIFF = (REPO_ROOT / "examples" / "sample.diff").read_text(encoding="utf-8")


def _heading_at(pattern: str) -> int:
    match = re.search(rf"^#{{2,3}} {pattern}\s*$", README, flags=re.M)
    assert match, f"no heading {pattern!r}"
    return match.start()


def section(title: str) -> str:
    """A `##`/`###` section's text, up to the next heading of the same or higher level."""
    match = re.search(rf"^(#{{2,3}}) {re.escape(title)}\s*$", README, flags=re.M)
    assert match, f"no section {title!r}"
    level = len(match.group(1))
    rest = README[match.end() :]
    following = re.search(rf"^#{{2,{level}}} ", rest, flags=re.M)
    return rest[: following.start()] if following else rest


def _home_relative(path: Path, home: Path) -> str:
    return "~/" + str(PurePosixPath(*path.relative_to(home).parts))


class ContributorSectionsFollowUsage(unittest.TestCase):
    def test_coverage_smoke_tests_and_benchmark_come_after_usage(self):
        usage = _heading_at("Usage")
        for title in ("Coverage", "Live smoke tests", "Review-quality benchmark"):
            self.assertGreater(_heading_at(re.escape(title)), usage, title)

    def test_the_dev_install_is_not_in_install(self):
        self.assertNotIn('pip install -e ".[dev]"', section("Install"))
        self.assertIn('pip install -e ".[dev]"', section("Development"))


class UninstallCoversEveryRoute(unittest.TestCase):
    def test_each_package_manager_has_its_uninstall(self):
        install, uninstall = section("Install"), section("Uninstall")
        routes = {
            "brew install berkayturanci/ai-jury/ai-jury": (
                "brew uninstall ai-jury",
                "brew untap berkayturanci/ai-jury",
            ),
            "pipx install ai-jury": ("pipx uninstall ai-jury",),
            "install.sh": ("uv tool uninstall ai-jury", "pip uninstall ai-jury"),
        }
        for installed_by, removals in routes.items():
            self.assertIn(installed_by, install)
            for removal in removals:
                self.assertIn(removal, uninstall)

    def test_the_installers_own_directories_are_named(self):
        uninstall = section("Uninstall")
        for var in ("INSTALL_DIR", "BIN_DIR"):
            match = re.search(rf'^{var}="\$\{{(\w+):-\$HOME/([^}}]+)\}}"$', INSTALLER, flags=re.M)
            self.assertIsNotNone(match, var)
            override, default = match.groups()
            self.assertIn(override, uninstall)
            self.assertIn(f"~/{default}", uninstall)

    def test_the_action_and_the_plugins_are_covered(self):
        uninstall = section("Uninstall")
        self.assertIn("uses: berkayturanci/ai-jury@", uninstall)
        for command in (
            "claude plugin uninstall ai-jury@ai-jury",
            "codex plugin remove ai-jury@ai-jury",
            "agy plugin uninstall ai-jury",
            "~/.cursor/plugins/local/ai-jury",
        ):
            self.assertIn(command, uninstall)

    def test_the_directories_the_tool_writes_are_named(self):
        home = Path("/home/someone")
        env = dict.fromkeys(("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "JURY_CACHE_DIR"), "")
        with mock.patch.object(Path, "home", return_value=home), mock.patch.dict("os.environ", env):
            trust = configtrust.trust_store_path()
            cache_dir = cache.default_cache_dir()
        uninstall = section("Uninstall")
        self.assertIn(f"`{_home_relative(trust.parent, home)}/`", uninstall)
        self.assertIn(f"`{trust.name}`", uninstall)
        self.assertIn(f"`{_home_relative(cache_dir, home)}/`", uninstall)
        for var in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "JURY_CACHE_DIR"):
            self.assertIn(var, uninstall)


class TheCostNoteCountsTheCalls(unittest.TestCase):
    AGENTS = [("anthropic", "claude"), ("openai", "codex"), ("google", "agy"), ("cli", "aider")]

    def _calls(self, seats: int, **jury) -> int:
        agents = [
            {"name": f"seat{i}", "vendor": vendor, "command": command}
            for i, (vendor, command) in enumerate(self.AGENTS[:seats])
        ]
        config = _from_dict({"jury": {"chair": "seat0", **jury}, "agent": agents})
        with mock.patch.object(
            MockAdapter, "run", autospec=True, side_effect=MockAdapter.run
        ) as run:
            run_jury(config, SAMPLE_DIFF, mock=True)
        return run.call_count

    def test_the_default_run_makes_two_n_plus_two_calls(self):
        for seats in (2, 3, 4):
            self.assertEqual(self._calls(seats), 2 * seats + 2, seats)
        self.assertIn("2N + 2 calls", section("What a run costs"))

    def test_the_named_switches_do_what_the_note_says(self):
        cost = section("What a run costs")
        self.assertEqual(self._calls(3, rounds=1), 3 + 2)
        self.assertIn("`--rounds 1` skips the debate", cost)
        self.assertEqual(self._calls(3, verify=False), 2 * 3 + 1)
        self.assertIn("`--no-verify` skips the verification call", cost)

    def test_every_vendor_is_placed(self):
        cost = section("What a run costs")
        for vendor in KNOWN_VENDORS:
            self.assertIn(f"`{vendor}`", cost, vendor)

    def test_it_is_free_and_names_no_price(self):
        cost = section("What a run costs")
        self.assertIn("ai-jury itself is free", cost)
        self.assertIsNone(re.search(r"[$€£]\s?\d", cost))


class TheWindowsNoteIsWhatCiRuns(unittest.TestCase):
    def test_the_windows_leg_is_named_as_ci_defines_it(self):
        leg = re.search(r"- os: (windows-\S+)\s*\n\s*python-version: \"([\d.]+)\"", CI)
        self.assertIsNotNone(leg, "ci.yml has no Windows leg")
        os_label, python = leg.groups()
        windows = section("Windows")
        self.assertIn(f"`{os_label}`", windows)
        self.assertIn(f"Python {python}", windows)

    def test_what_it_runs_is_what_ci_runs(self):
        self.assertIn("python -m unittest discover -s tests", CI)
        self.assertIn("python -m ai_jury --mock --diff-file", CI)
        windows = section("Windows")
        self.assertIn("offline unit suite", windows)
        self.assertIn("`--mock` review", windows)


if __name__ == "__main__":
    unittest.main()
