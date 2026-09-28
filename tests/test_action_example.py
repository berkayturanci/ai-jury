"""The published GitHub Action example works as pasted (#867).

The README, the cookbook and the site's Actions card showed a workflow that passed
API keys and nothing else. The keys alone do not form a panel: without a
`jury.toml` the jury seats its built-in agent CLIs, a runner has none of them, and
the run exits with `no usable agents`. Each copy now carries the `jury.toml` that
seats a hosted-API reviewer per key, and this module runs every copy through the
jury in `--mock` mode. `ci.yml` runs this suite on every push and pull request, so
the example is exercised in CI and cannot drift from the Action again.

Read by hand rather than with a YAML parser, for the reason the other Action tests
give: ai-jury declares `dependencies = []`, and a test is not a good enough reason
to make PyYAML the exception.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import tempfile
import tomllib
import unittest
from pathlib import Path

from ai_jury import cli
from ai_jury.adapters import make_adapter
from ai_jury.config import DEFAULT_CONFIG, load_config

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The fenced code blocks of a Markdown document, as (info string, body).
_FENCE = re.compile(r"^```([^\n`]*)\n(.*?)^```", re.M | re.S)
_ACTION_USES = "uses: berkayturanci/ai-jury@"
_TOML_MARKER = "# jury.toml"
#: The Actions card's `config` literal in `website/app.js`.
_SITE_CARD = re.compile(r'id: "github-action",[\s\S]*?config: "((?:[^"\\]|\\.)*)"')
#: `ENV_NAME: ${{ inputs.<input> }}` in an `env:` block of action.yml.
_ENV_FROM_INPUT = re.compile(r"^\s+([A-Z][A-Z0-9_]*): \$\{\{ inputs\.([a-z0-9-]+) \}\}\s*$", re.M)
_WITH_KEY = re.compile(r"^(\s*)([a-z][a-z0-9-]*):")


def _markdown_example(text: str) -> tuple[str | None, str | None]:
    """(workflow block, jury.toml block) of the first Action example in *text*.

    The `jury.toml` must be the very next fenced block. Taking the next *matching*
    one read an unrelated CLI-seat config three sections further down the cookbook
    as the example's, and passed half of these tests with the fix reverted.
    """
    blocks = [(info.strip(), body) for info, body in _FENCE.findall(text)]
    at = next((i for i, (_, body) in enumerate(blocks) if _ACTION_USES in body), None)
    if at is None:
        return None, None
    following = blocks[at + 1 : at + 2]
    toml = next(
        (
            body
            for info, body in following
            if info == "toml" and body.lstrip().startswith(_TOML_MARKER)
        ),
        None,
    )
    return blocks[at][1], toml


def _site_example(text: str) -> tuple[str | None, str | None]:
    """(workflow step, jury.toml) out of the site card's single `config` string."""
    match = _SITE_CARD.search(text)
    if match is None:
        return None, None
    config = json.loads(f'"{match.group(1)}"')
    if _TOML_MARKER not in config:
        return config, None
    workflow, toml = config.split(_TOML_MARKER, 1)
    return workflow, _TOML_MARKER + toml


def _with_keys(workflow: str) -> set[str]:
    """The `with:` keys passed to the ai-jury step of *workflow*."""
    lines = workflow.splitlines()
    start = next(i for i, line in enumerate(lines) if _ACTION_USES in line)
    keys: set[str] = set()
    depth = None
    for line in lines[start + 1 :]:
        if not line.strip():
            continue
        if depth is None:
            if line.strip() != "with:":
                break
            depth = len(line) - len(line.lstrip())
            continue
        match = _WITH_KEY.match(line)
        if match is None or len(match.group(1)) <= depth:
            break
        keys.add(match.group(2))
    return keys


def _env_for_input() -> dict[str, str]:
    """Action input name → the environment variable the run step receives it as."""
    text = (REPO_ROOT / "action.yml").read_text(encoding="utf-8")
    return {name: env for env, name in _ENV_FROM_INPUT.findall(text)}


def _min_vendors_default() -> int:
    text = (REPO_ROOT / "action.yml").read_text(encoding="utf-8")
    block = text.split("\n  min-vendors:", 1)[1].split("\n  version:", 1)[0]
    return int(re.search(r'default: "(\d+)"', block).group(1))


SURFACES = {
    "README.md": _markdown_example,
    "docs/cookbook.md": _markdown_example,
    "website/app.js": _site_example,
}


class TheActionExampleWorksAsPasted(unittest.TestCase):
    def _example(self, surface: str) -> tuple[str, str]:
        text = (REPO_ROOT / surface).read_text(encoding="utf-8")
        workflow, toml = SURFACES[surface](text)
        self.assertIsNotNone(workflow, f"{surface}: no `{_ACTION_USES}` example found")
        self.assertIsNotNone(
            toml,
            f"{surface}: the Action example shows no `{_TOML_MARKER}` — the keys alone "
            "do not form a panel, so the example exits with `no usable agents`",
        )
        return workflow, toml

    def _config(self, toml: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jury.toml"
            path.write_text(toml, encoding="utf-8")
            return load_config(path, validate=True, strict=True)

    def test_the_example_parses_and_seats_a_hosted_api_reviewer_per_seat(self):
        for surface in SURFACES:
            with self.subTest(surface=surface):
                _, toml = self._example(surface)
                tomllib.loads(toml)  # a syntax error is a broken example, not a skip
                config = self._config(toml)
                self.assertTrue(config.agents, f"{surface}: the jury.toml seats nobody")
                for agent in config.agents:
                    self.assertEqual(agent.command, "", f"{surface}: {agent.name} needs a CLI")
                    self.assertTrue(agent.model, f"{surface}: {agent.name} has no model")
                    self.assertTrue(
                        getattr(make_adapter(agent), "_ENV_VAR_NAME", ""),
                        f"{surface}: {agent.name} is not keyed by an API-key variable",
                    )

    def test_every_seat_reads_a_key_the_workflow_passes(self):
        env_for_input = _env_for_input()
        for surface in SURFACES:
            with self.subTest(surface=surface):
                workflow, toml = self._example(surface)
                keys = _with_keys(workflow)
                passed = {env_for_input[key] for key in keys if key in env_for_input}
                for agent in self._config(toml).agents:
                    needed = getattr(make_adapter(agent), "_ENV_VAR_NAME", "")
                    self.assertTrue(needed, f"{surface}: {agent.name} reads no API key")
                    self.assertIn(
                        needed,
                        passed,
                        f"{surface}: seat {agent.name} reads {needed}, which the "
                        "workflow does not pass to the Action",
                    )

    def test_the_panel_meets_the_actions_min_vendors_default(self):
        """Two keys and one seat would still fail: the Action's guard defaults to 2."""
        floor = _min_vendors_default()
        for surface in SURFACES:
            with self.subTest(surface=surface):
                _, toml = self._example(surface)
                vendors = {a.vendor for a in self._config(toml).agents}
                self.assertGreaterEqual(len(vendors), floor, f"{surface}: {sorted(vendors)}")

    def test_the_example_runs_through_the_jury_in_mock_mode(self):
        """What the Action runs, minus `--post` and the network: exit 0, those seats."""
        sample = REPO_ROOT / "examples" / "sample.diff"
        floor = str(_min_vendors_default())
        for surface in SURFACES:
            with self.subTest(surface=surface):
                _, toml = self._example(surface)
                with tempfile.TemporaryDirectory() as tmp:
                    config = Path(tmp) / "jury.toml"
                    config.write_text(toml, encoding="utf-8")
                    report = Path(tmp) / "report.md"
                    argv = ["--config", str(config), "--mock", "--diff-file", str(sample)]
                    argv += ["--auto", "--min-vendors", floor, "-q", "-o", str(report)]
                    with contextlib.redirect_stderr(io.StringIO()) as err:
                        code = cli.main(argv)
                    self.assertEqual(code, 0, f"{surface}: {err.getvalue()[-2000:]}")
                    panel = next(
                        line
                        for line in report.read_text(encoding="utf-8").splitlines()
                        if line.startswith("**Panel:**")
                    )
                for agent in self._config(toml).agents:
                    self.assertIn(f"`{agent.name}`", panel, surface)

    def test_without_the_jury_toml_the_panel_is_agent_clis(self):
        """Why the file is needed at all: the built-in panel is CLIs a runner lacks."""
        seats = DEFAULT_CONFIG["agent"]
        self.assertTrue(seats)
        self.assertTrue(all(seat.get("command") for seat in seats), seats)


class TheExampleIsExercisedInCI(unittest.TestCase):
    """The tests above only guard the example if CI actually runs them."""

    def test_ci_discovers_this_module(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("python -m unittest discover -s tests", ci)
        here = Path(__file__)
        self.assertEqual(here.parent.name, "tests")
        self.assertTrue(here.name.startswith("test") and here.suffix == ".py", here.name)


if __name__ == "__main__":
    unittest.main()
