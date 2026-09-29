"""Claims in the project docs that the 2026-09-29 docs audit found stale, read from the code.

Each test here derives the expected text from the thing the document describes — the
redaction table from ``redaction._PATTERNS``, the argv from the locked adapter contract,
the module map from ``src/ai_jury``, the version surfaces from ``release_surfaces`` — so the
next change to the code fails a test instead of leaving the page behind. A test that only
restated a sentence would pin the wording, not the claim; where a claim has no source in
the tree to read (a dated history note, a measured example), it is not pinned here.
"""

from __future__ import annotations

import inspect
import json
import re
import sys
import tomllib
import unittest
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import release_surfaces  # noqa: E402

from ai_jury import adapters, cli, configtrust, doctor, redaction, scaffold, theater  # noqa: E402
from ai_jury.config import AgentSpec, ConfigError, load_config  # noqa: E402


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The body under ``heading`` (a full heading line), up to the next heading of its level."""
    level = len(heading) - len(heading.lstrip("#"))
    if heading + "\n" not in text:
        # A failure, not an error: a missing section is the claim being absent.
        raise AssertionError(f"no {heading!r} section")
    start = text.index(heading + "\n")
    rest = text[start + len(heading) + 1 :]
    stop = re.search(rf"^#{{1,{level}}} ", rest, flags=re.MULTILINE)
    return rest[: stop.start()] if stop else rest


def _render_argv(argv: list[str], model: str) -> str:
    """A golden argv as the docs write it: the model as ``<model>``, an empty arg as ``""``."""
    return " ".join("<model>" if a == model else ('""' if a == "" else a) for a in argv)


def _golden() -> dict:
    return json.loads(_read("tests/golden/adapter_contracts.json"))


#: The docs this audit owned. feasibility.md is left out of the stale-argv scan on
#: purpose: its CLI table is a dated research snapshot, labelled as such.
OWNED_DOCS = (
    "docs/cookbook.md",
    "docs/security.md",
    "docs/architecture.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "AGENTS.md",
    "ROADMAP.md",
    "docs/comparison.md",
    "docs/positioning.md",
    "docs/theater-design.md",
    "docs/releasing.md",
    "docs/release-checklist.md",
    "docs/example-live-review.md",
    "docs/benchmark-results.md",
    "docs/case-study-abdication.md",
)


class RedactionTableIsComplete(unittest.TestCase):
    """SE1/RC1: SECURITY.md listed 6 of the redactor's 16 kinds under a ticked box."""

    def test_security_md_names_every_redaction_kind(self):
        section = _section(_read("SECURITY.md"), "## Jury data flow & redaction")
        documented = set(re.findall(r"^\| `([a-z_]+)` \|", section, flags=re.MULTILINE))
        in_code = {kind for kind, _pattern in redaction._PATTERNS}
        self.assertEqual(documented, in_code)


class InvocationsMatchTheLockedContract(unittest.TestCase):
    """A1/A2/SD8/CB4: the argv the docs print is the argv the adapters build."""

    def test_architecture_prints_each_shipped_cli_argv(self):
        text = _read("docs/architecture.md")
        for key in ("claude", "codex", "agy"):
            spec = _golden()[key]
            with self.subTest(adapter=key):
                self.assertIn(_render_argv(spec["argv"], spec["model"]), text)

    def test_security_md_prints_the_codex_argv(self):
        spec = _golden()["codex"]
        self.assertIn(_render_argv(spec["argv"], spec["model"]), _read("docs/security.md"))

    def test_the_local_url_is_the_base_endpoint_plus_chat_completions(self):
        spec = AgentSpec(name="l", vendor="local", endpoint="http://h:1/v1")
        url = adapters.LocalAdapter(spec).completions_url()
        self.assertEqual(url, "http://h:1/v1/chat/completions")
        row = next(
            ln
            for ln in _read("docs/architecture.md").splitlines()
            if ln.startswith("|") and "`LocalAdapter`" in ln
        )
        self.assertIn("`POST {endpoint}/chat/completions`", row)
        self.assertIn("http://localhost:11434/v1", row)
        self.assertEqual(adapters._DEFAULT_LOCAL_ENDPOINT, "http://localhost:11434/v1")

    def test_no_owned_doc_prints_a_retired_invocation(self):
        stale = (
            ("agy --print", r"agy --print\b"),
            ('claude -p "<prompt>"', r'claude -p "<prompt>"'),
            ("{endpoint}/v1/chat/completions", r"\{endpoint\}/v1/chat/completions"),
        )
        hits = [
            f"{rel}: {label}"
            for rel in OWNED_DOCS
            for label, pattern in stale
            if re.search(pattern, _read(rel))
        ]
        self.assertEqual(hits, [])


class HostedApiVendorsAreListed(unittest.TestCase):
    """A3/SD6: xai-api was missing from both hosted-API lists."""

    def _hosted(self) -> dict[str, str]:
        return {
            vendor: cls._ENV_VAR_NAME
            for vendor, cls in adapters._VENDOR_ADAPTERS.items()
            if issubclass(cls, adapters._HostedApiAdapter)
            and not issubclass(cls, adapters.GenericOpenAICompatibleAdapter)
        }

    def test_architecture_and_security_name_each_vendor_and_key(self):
        arch = _read("docs/architecture.md")
        arch_para = arch[arch.index("A **hosted-API agent**") :]
        arch_para = arch_para[: arch_para.index("\n\n")]
        sec = _section(
            _read("docs/security.md"), "### Hosted-API reviewers (no CLI, no sandbox needed)"
        )
        hosted = self._hosted()
        self.assertIn("xai-api", hosted)  # a reading that finds nothing proves nothing
        for vendor, env in hosted.items():
            for where, text in (("architecture.md", arch_para), ("security.md", sec)):
                with self.subTest(vendor=vendor, doc=where):
                    self.assertIn(f"`{vendor}`" if where == "security.md" else vendor, text)
                    self.assertIn(env, text)


class CitedTestsExist(unittest.TestCase):
    """SD1: security.md cited a test class that does not exist."""

    def test_every_cited_test_node_resolves(self):
        missing = []
        for rel in OWNED_DOCS:
            for path, cls, meth in re.findall(
                r"(tests/[\w/]+\.py)::(\w+)(?:\.(\w+)|::(?:\w+))?", _read(rel)
            ):
                source = (
                    (ROOT / path).read_text(encoding="utf-8") if (ROOT / path).is_file() else ""
                )
                if f"class {cls}" not in source or (meth and f"def {meth}(" not in source):
                    missing.append(f"{rel}: {path}::{cls}{'.' + meth if meth else ''}")
        self.assertEqual(missing, [])


class SecurityDocsCoverTheirRecords(unittest.TestCase):
    """SD2/SD3/SD5."""

    def test_every_dated_audit_record_is_linked(self):
        text = _read("docs/security.md")
        records = sorted(p.name for p in (ROOT / "docs").glob("security-audit-*.md"))
        self.assertTrue(records)
        self.assertEqual([r for r in records if f"]({r})" not in text], [])

    def test_the_config_trust_gate_is_documented_as_it_works(self):
        section = _section(
            _read("docs/security.md"), "## An auto-discovered `jury.toml` is trusted like code"
        )
        self.assertIn(configtrust.TRUST_ENV, section)
        self.assertIn(configtrust.trust_store_path().name, section)
        for value in ("1", "true", "yes", "on"):
            self.assertTrue(configtrust._truthy_env(value))
            self.assertIn(f"`{value}`", section)
        self.assertIn("exit `2`", section)

    def test_an_unknown_vendor_without_a_command_is_refused_as_documented(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "jury.toml"
            cfg.write_text('[[agent]]\nname = "a"\nvendor = "acme"\n', encoding="utf-8")
            with self.assertRaises(ConfigError) as caught:
                load_config(str(cfg), validate=True)
        self.assertIn("missing a non-empty 'command'", str(caught.exception))
        flat = " ".join(_read("docs/security.md").split())
        self.assertIn("missing a non-empty 'command'", flat)


class CookbookSamplesRun(unittest.TestCase):
    """CB1/CB13/CB15/CB18/CB10."""

    def _sections(self) -> list[tuple[str, str]]:
        parts = re.split(r"^(## .+)$", _read("docs/cookbook.md"), flags=re.MULTILINE)
        return list(zip(parts[1::2], parts[2::2], strict=True))

    def _agents(self):
        for heading, body in self._sections():
            for fence in re.findall(r"```toml\n(.*?)```", body, flags=re.DOTALL):
                try:
                    data = tomllib.loads(fence)
                except tomllib.TOMLDecodeError:
                    continue
                for agent in data.get("agent", []):
                    yield heading, body, agent

    def test_a_remote_endpoint_sample_names_the_env_opt_in(self):
        loopback = {"localhost", "127.0.0.1", "::1"}
        missing = sorted(
            {
                heading
                for heading, body, agent in self._agents()
                if (urlsplit(agent.get("endpoint", "")).hostname or "localhost") not in loopback
                and "JURY_ALLOW_REMOTE_ENDPOINT=1" not in body
            }
        )
        self.assertEqual(missing, [])

    def test_no_sample_reaches_xai_through_the_generic_adapter(self):
        generic = [
            agent["name"]
            for _h, _b, agent in self._agents()
            if urlsplit(agent.get("endpoint", "")).hostname == "api.x.ai"
        ]
        self.assertEqual(generic, [])

    def test_every_agy_sample_seat_is_disabled(self):
        seats = [
            agent
            for _h, _b, agent in self._agents()
            if agent.get("vendor") == "google" or agent.get("command") == "agy"
        ]
        self.assertTrue(seats)
        self.assertEqual([a["name"] for a in seats if a.get("enabled", True)], [])

    def test_run_agent_examples_use_the_listed_openai_model(self):
        text = _read("docs/cookbook.md")
        ids = re.findall(r"--agent codex:([\w.\-]+)", text)
        self.assertTrue(ids)
        self.assertEqual(set(ids), {scaffold.SAMPLE_MODELS["openai"]})

    def test_the_doctor_panel_sample_carries_every_key(self):
        text = _read("docs/cookbook.md")
        block = text[text.index("jury --doctor --json | jq '.panel'") :]
        block = block[: block.index("```")]
        self.assertEqual(set(re.findall(r'"(\w+)":', block)), set(doctor._NO_PANEL))


class ProjectFilesMatchTheTree(unittest.TestCase):
    """AG1/AG2/AG3/A4/RL1/CN1/CN2/CN3/RC3."""

    def test_agents_md_names_every_argv_intercept(self):
        source = inspect.getsource(cli.main)
        names = set(re.findall(r'raw\[:1\] == \["([\w-]+)"\]', source))
        names |= set(re.findall(r'raw == \["([\w-]+)"\]', source))
        for group in re.findall(r"raw\[:1\] in \(((?:\[\"[\w-]+\"\],?\s*)+)\)", source):
            names |= set(re.findall(r'\["([\w-]+)"\]', group))
        names |= {
            " ".join(m) for m in re.findall(r'raw\[:2\] == \["([\w-]+)", "([\w-]+)"\]', source)
        }
        self.assertGreaterEqual(len(names), 9)
        text = _read("AGENTS.md")
        bullet = text[text.index("**CLI subcommands are argv-intercepts.**") :]
        bullet = bullet[: bullet.index("\n- ")]
        self.assertEqual(sorted(n for n in names if n not in bullet), [])

    def test_agents_md_make_test_line_is_the_makefile_recipe(self):
        makefile = _read("Makefile")
        recipe = re.search(r"^test:\n\t(.+)$", makefile, flags=re.MULTILINE).group(1).strip()
        line = next(ln for ln in _read("AGENTS.md").splitlines() if ln.startswith("make test"))
        self.assertIn(recipe, line)

    def test_agents_md_module_map_names_every_module(self):
        modules = sorted(
            p.stem for p in (ROOT / "src" / "ai_jury").glob("*.py") if not p.stem.startswith("__")
        )
        section = _section(_read("AGENTS.md"), "## Module map")
        self.assertEqual([m for m in modules if f"`{m}`" not in section], [])

    def test_architecture_lists_every_workflow(self):
        section = _section(_read("docs/architecture.md"), "### CI & runners")
        workflows = sorted(p.name for p in (ROOT / ".github" / "workflows").glob("*.yml"))
        self.assertEqual([w for w in workflows if f"`{w}`" not in section], [])

    def test_releasing_md_names_every_version_surface(self):
        section = _section(_read("docs/releasing.md"), "## Which files carry the version")
        self.assertEqual(
            [p for p in release_surfaces.surface_paths() if f"`{p}`" not in section], []
        )

    def test_contributing_names_every_dependabot_ecosystem(self):
        ecosystems = re.findall(r'package-ecosystem: "([\w-]+)"', _read(".github/dependabot.yml"))
        self.assertTrue(ecosystems)
        text = _read("CONTRIBUTING.md")
        bullet = text[text.index("- **Automation.**") :]
        bullet = bullet[: bullet.index("\n- ")]
        self.assertEqual([e for e in ecosystems if f"`{e}` ecosystem" not in bullet], [])

    def test_contributing_describes_the_pin_comment_the_workflows_use(self):
        workflows = "".join(
            p.read_text(encoding="utf-8") for p in (ROOT / ".github" / "workflows").glob("*.yml")
        )
        pins = re.findall(r"uses: \S+@[0-9a-f]{40}\s*#\s*(.+)", workflows)
        self.assertTrue(pins)
        if not any("(pinned" in c for c in pins):
            self.assertNotIn("(pinned", _read("CONTRIBUTING.md"))

    def test_contributing_asks_for_the_checks_ci_runs(self):
        ci = _read(".github/workflows/ci.yml")
        text = _read("CONTRIBUTING.md")
        if "ruff format --check" in ci:
            self.assertIn("ruff format --check", text)
        if re.search(r"^  coverage:", ci, flags=re.MULTILINE):
            self.assertIn("make coverage", text)

    def test_the_checklist_says_the_tap_push_needs_its_token(self):
        if "HOMEBREW_TAP_TOKEN" not in _read(".github/workflows/publish.yml"):
            self.skipTest("publish.yml no longer gates the tap push on a token")
        text = _read("docs/release-checklist.md")
        step = text[text.index("pushes the formula to `berkayturanci/homebrew-ai-jury`") :]
        self.assertIn("HOMEBREW_TAP_TOKEN", step[:400])


class TheaterDesignMatchesTheScene(unittest.TestCase):
    """TH1/TH2."""

    def test_the_no_debate_line_is_quoted_as_the_scene_logs_it(self):
        logged = re.search(
            r'self\.log\.append\("(no debate[^"]*)"\)', inspect.getsource(theater.Courtroom)
        ).group(1)
        self.assertIn(f"`{logged}`", _read("docs/theater-design.md"))

    def test_no_unicode_fallback_is_promised_while_nothing_selects_one(self):
        source = inspect.getsource(cli)
        if "unicode=" in source:
            self.skipTest("the CLI now chooses unicode per terminal; re-read the promise")
        self.assertNotIn("transparently falls back", _read("docs/theater-design.md"))


if __name__ == "__main__":
    unittest.main()
