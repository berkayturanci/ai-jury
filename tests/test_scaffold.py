"""Tests for `jury init` config scaffolding (issue #107)."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import tomllib
import unittest
import unittest.mock as mock
from pathlib import Path

from ai_jury import scaffold

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import cli  # noqa: E402
from ai_jury.config import _from_dict, validate_config  # noqa: E402
from ai_jury.scaffold import (  # noqa: E402
    build_config,
    render_toml,
)


class BuildConfigTest(unittest.TestCase):
    def test_cloud_panel(self):
        cfg = build_config(["claude", "codex"], rounds=2)
        names = [a["name"] for a in cfg["agent"]]
        self.assertEqual(names, ["claude", "codex"])
        self.assertEqual(cfg["jury"]["chair"], "claude")  # defaults to first
        self.assertEqual(cfg["jury"]["rounds"], 2)

    def test_secure_defaults_carried_over(self):
        cfg = build_config(["codex", "agy"])
        codex = next(a for a in cfg["agent"] if a["name"] == "codex")
        agy = next(a for a in cfg["agent"] if a["name"] == "agy")
        self.assertIn("read-only", codex["extra_args"])  # issue #100 default
        self.assertIn("--sandbox", agy["extra_args"])

    def test_local_agent_overrides(self):
        cfg = build_config(["qwen"], local_model="llama3", local_endpoint="http://h:1/v1")
        qwen = cfg["agent"][0]
        self.assertEqual(qwen["vendor"], "local")
        self.assertEqual(qwen["model"], "llama3")
        self.assertEqual(qwen["endpoint"], "http://h:1/v1")
        self.assertNotIn("command", {k: v for k, v in qwen.items() if v})

    def test_unknown_agent_raises(self):
        with self.assertRaises(ValueError):
            build_config(["gpt5"])

    def test_empty_selection_raises(self):
        with self.assertRaises(ValueError):
            build_config([])

    def test_dedup_preserves_order(self):
        cfg = build_config(["codex", "claude", "codex"])
        self.assertEqual([a["name"] for a in cfg["agent"]], ["codex", "claude"])


class RenderTomlTest(unittest.TestCase):
    def test_renders_valid_loadable_toml(self):
        cfg = build_config(["claude", "codex", "qwen"], rounds=1, verify=False)
        text = render_toml(cfg)
        # Parses as TOML and round-trips through the real loader + validator.
        parsed = tomllib.loads(text)
        self.assertEqual(parsed["jury"]["rounds"], 1)
        self.assertFalse(parsed["jury"]["verify"])
        warnings = validate_config(parsed)  # no ConfigError
        self.assertIsInstance(warnings, list)
        loaded = _from_dict(parsed)
        self.assertEqual([a.name for a in loaded.agents], ["claude", "codex", "qwen"])

    def test_wizard_keys_emit_sections(self):
        cfg = build_config(
            ["claude", "codex"],
            decision="vote",
            auto_depth=True,
            context_mode="expanded",
            redact_secrets=False,
            ci_fail_on=["critical"],
        )
        text = render_toml(cfg)
        parsed = tomllib.loads(text)
        self.assertEqual(parsed["jury"]["decision"], "vote")
        self.assertTrue(parsed["jury"]["auto_depth"])
        self.assertEqual(parsed["jury"]["context"]["mode"], "expanded")
        self.assertFalse(parsed["jury"]["context"]["redact_secrets"])
        self.assertEqual(parsed["jury"]["ci"]["fail_on"], ["critical"])
        validate_config(parsed)  # no ConfigError
        # Sections render under [jury].
        self.assertIn("[jury.context]", text)
        self.assertIn("[jury.ci]", text)
        # The cross-vendor knob is discoverable where the CI gate is configured
        # (#682) — commented out, so it changes nothing a reader did not ask for.
        self.assertIn("# min_vendors = 2", text)
        self.assertNotIn("\nmin_vendors", text)

    def test_no_wizard_keys_byte_identical(self):
        # Omitting the new kwargs must produce output with none of the new sections.
        text = render_toml(build_config(["claude", "codex"], rounds=2))
        self.assertNotIn("[jury.ci]", text)
        self.assertNotIn("[jury.context]", text)
        self.assertNotIn("decision", text)

    def test_redact_only_context_section(self):
        # redact_secrets set WITHOUT context_mode still emits [jury.context].
        cfg = build_config(["claude"], redact_secrets=False)
        parsed = tomllib.loads(render_toml(cfg))
        self.assertFalse(parsed["jury"]["context"]["redact_secrets"])
        self.assertNotIn("mode", parsed["jury"]["context"])

    def test_local_agent_has_no_empty_command(self):
        text = render_toml(build_config(["qwen"]))
        self.assertNotIn('command = ""', text)
        self.assertIn('endpoint = "http://localhost:11434/v1"', text)


class InitCliTest(unittest.TestCase):
    def _run(self, argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        prev = sys.stdin
        sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(argv)
        finally:
            sys.stdin = prev
        return code, out.getvalue(), err.getvalue()

    def test_flag_driven_writes_valid_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jury.toml"
            code, out, _ = self._run(
                ["init", "--agents", "claude,codex", "--rounds", "2", "-o", str(path)]
            )
            self.assertEqual(code, 0)
            self.assertIn("Wrote", out)
            # Validate the file the same way a real run would.
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            validate_config(data)
            self.assertEqual([a["name"] for a in data["agent"]], ["claude", "codex"])

    def test_refuses_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jury.toml"
            path.write_text("existing", encoding="utf-8")
            code, _, err = self._run(["init", "--agents", "claude", "-o", str(path)])
            self.assertEqual(code, 2)
            self.assertIn("already exists", err)
            self.assertEqual(path.read_text(encoding="utf-8"), "existing")

    def test_force_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jury.toml"
            path.write_text("existing", encoding="utf-8")
            code, _, _ = self._run(["init", "--agents", "claude", "-o", str(path), "--force"])
            self.assertEqual(code, 0)
            self.assertIn("[[agent]]", path.read_text(encoding="utf-8"))

    def test_unknown_agent_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jury.toml"
            code, _, err = self._run(["init", "--agents", "nope", "-o", str(path)])
            self.assertEqual(code, 2)
            self.assertIn("unknown agent", err)

    def test_list_agents(self):
        code, out, _ = self._run(["init", "--list-agents"])
        self.assertEqual(code, 0)
        for name in ("claude", "codex", "agy", "qwen"):
            self.assertIn(name, out)

    def test_interactive_picks_discovered_model_by_number(self):
        # qwen selected + a server that lists models -> user picks one by number.
        available = {"claude": True, "codex": True, "agy": False, "qwen": True}
        answers = iter(["claude,qwen", "1", "claude", "n", "2", ""])
        kwargs = cli._init_interactive(
            available,
            input_fn=lambda _p: next(answers),
            models_fn=lambda _ep: ["gemma:2b", "qwen2.5-coder:7b"],
        )
        self.assertEqual(kwargs["agents"], ["claude", "qwen"])
        self.assertEqual(kwargs["rounds"], 1)
        self.assertFalse(kwargs["verify"])
        self.assertEqual(kwargs["local_model"], "qwen2.5-coder:7b")  # picked #2
        validate_config(build_config(**kwargs))

    def test_interactive_no_server_falls_back_to_typed_model(self):
        available = {"claude": False, "codex": False, "agy": False, "qwen": True}
        answers = iter(["qwen", "2", "qwen", "y", "deepseek-coder:6.7b", ""])
        kwargs = cli._init_interactive(
            available, input_fn=lambda _p: next(answers), models_fn=lambda _ep: []
        )
        self.assertEqual(kwargs["local_model"], "deepseek-coder:6.7b")


class LocalModelDiscoveryTest(unittest.TestCase):
    def test_pick_default_prefers_coder_model(self):
        from ai_jury.scaffold import pick_default_model

        self.assertEqual(
            pick_default_model(["gemma:2b", "deepseek-coder:6.7b"]),
            "deepseek-coder:6.7b",
        )

    def test_pick_default_first_when_no_coder(self):
        from ai_jury.scaffold import pick_default_model

        self.assertEqual(pick_default_model(["gemma:2b", "phi3:mini"]), "gemma:2b")

    def test_pick_default_none_when_empty(self):
        from ai_jury.scaffold import pick_default_model

        self.assertIsNone(pick_default_model([]))

    def test_list_local_models_parses_openai_shape(self):
        # Parse the OpenAI-compatible /v1/models response shape offline.
        import json

        from ai_jury.adapters import list_local_models

        payload = json.dumps({"data": [{"id": "gemma:2b"}, {"id": "qwen2.5-coder:7b"}]}).encode()

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, *_args):
                return payload

        with mock.patch("ai_jury.adapters._open", return_value=_Resp()):
            models = list_local_models("http://localhost:11434/v1")
        self.assertEqual(models, ["gemma:2b", "qwen2.5-coder:7b"])

    def test_list_local_models_empty_on_failure(self):
        import urllib.error

        from ai_jury.adapters import list_local_models

        with mock.patch("ai_jury.adapters._open", side_effect=urllib.error.URLError("down")):
            self.assertEqual(list_local_models("http://localhost:11434/v1"), [])


class PresetTest(unittest.TestCase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_offline_preset_is_local_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jury.toml"
            code, _, _ = self._run(["init", "--preset", "offline", "-o", str(path)])
            self.assertEqual(code, 0)
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            validate_config(data)
            self.assertEqual([a["name"] for a in data["agent"]], ["qwen"])
            self.assertEqual(data["jury"]["rounds"], 1)
            self.assertFalse(data["jury"]["verify"])

    def test_balanced_preset_sets_early_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jury.toml"
            self._run(["init", "--preset", "balanced", "--agents", "claude,codex", "-o", str(path)])
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["jury"]["rounds"], 2)
            self.assertTrue(data["jury"]["verify"])
            self.assertTrue(data["jury"]["early_stop"])

    def test_thorough_preset_uses_all_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jury.toml"
            self._run(["init", "--preset", "thorough", "-o", str(path)])
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            # Derived, not listed: a hardcoded roster here is a second copy of
            # KNOWN_AGENTS, and two copies drifting apart is what #589 was about.
            expected = [
                n
                for n in scaffold.KNOWN_AGENTS
                if n not in set(scaffold.agents_needing_remote_opt_in())
            ]
            self.assertEqual([a["name"] for a in data["agent"]], expected)
            self.assertNotEqual(
                expected,
                list(scaffold.KNOWN_AGENTS),
                "no agent needs the remote opt-in, so this case proves nothing",
            )

    def test_explicit_flag_overrides_preset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jury.toml"
            # offline preset defaults rounds=1; explicit --rounds 2 wins.
            self._run(["init", "--preset", "offline", "--rounds", "2", "-o", str(path)])
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["jury"]["rounds"], 2)


class OfflineFallbackTest(unittest.TestCase):
    def _args(self, config=None, mock=False):
        return type("A", (), {"config": config, "mock": mock})()

    def test_adds_local_agent_when_nothing_else_available(self):
        import ai_jury.adapters as adapters
        import ai_jury.cli as climod
        from ai_jury.config import DEFAULT_CONFIG, _from_dict

        cfg = _from_dict(DEFAULT_CONFIG)  # claude/codex/agy, no local
        logs = []
        with (
            mock.patch.object(adapters.Adapter, "available", return_value=False),
            mock.patch.object(
                adapters, "list_local_models", return_value=["gemma:2b", "qwen2.5-coder:7b"]
            ),
            mock.patch.object(climod.Path, "exists", return_value=False),
        ):
            climod._maybe_add_local_fallback(cfg, self._args(), logs.append)

        local = next((a for a in cfg.agents if a.name == "local"), None)
        self.assertIsNotNone(local)
        self.assertEqual(local.model, "qwen2.5-coder:7b")  # coder preferred
        self.assertEqual(cfg.chair, "local")
        self.assertTrue(any("offline" in m for m in logs))

    def test_no_fallback_when_config_file_present(self):
        import ai_jury.cli as climod
        from ai_jury.config import DEFAULT_CONFIG, _from_dict

        cfg = _from_dict(DEFAULT_CONFIG)
        before = len(cfg.agents)
        climod._maybe_add_local_fallback(cfg, self._args(config="jury.toml"), lambda _m: None)
        self.assertEqual(len(cfg.agents), before)  # explicit --config -> no-op


class ConfigShowTest(unittest.TestCase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_config_show_renders_effective_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jury.toml"
            cli.main(["init", "--agents", "claude,qwen", "--rounds", "1", "-o", str(path)])
            code, out, _ = self._run(["config", "show", "--config", str(path)])
            self.assertEqual(code, 0)
            self.assertIn(f"source: {path}", out)
            self.assertIn("[jury] rounds=1", out)
            self.assertIn("claude (anthropic)", out)
            self.assertIn("qwen (local)", out)

    def test_config_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jury.toml"
            cli.main(["init", "--agents", "claude", "-o", str(path)])
            code, out, _ = self._run(["config", "path", "--config", str(path)])
            self.assertEqual(code, 0)
            self.assertEqual(out.strip(), str(path))

    def test_config_show_invalid_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.toml"
            path.write_text(
                "[jury]\nrounds = 0\n[[agent]]\nname='x'\nvendor='anthropic'\ncommand='c'\n",
                encoding="utf-8",
            )
            code, _, err = self._run(["config", "show", "--config", str(path)])
            self.assertEqual(code, 2)
            self.assertIn("error", err)


class KnownAgentsMatchesTheTemplates(unittest.TestCase):
    """The two lists of agents must be one list.

    #589 asked for exactly this assertion and #590 shipped without it, so four
    templates existed that `jury init` could not offer. The symptom a user hit
    was the CLI naming agents in an error message that its own `--list-agents`
    never printed.
    """

    def test_known_agents_is_exactly_the_template_set(self):
        self.assertEqual(set(scaffold.KNOWN_AGENTS), set(scaffold.agent_templates()))

    def test_no_agent_is_listed_twice(self):
        self.assertEqual(len(scaffold.KNOWN_AGENTS), len(set(scaffold.KNOWN_AGENTS)))

    def test_every_agent_the_error_suggests_can_actually_be_chosen(self):
        """The behavioural half — this is what the user actually ran into.

        `build_config` rejects an unknown agent by listing `templates.keys()`.
        If that list is wider than `KNOWN_AGENTS`, the CLI is telling people to
        pick options it never offers; if it is narrower, a scaffoldable agent is
        undiscoverable. Either way the two must agree.
        """
        with self.assertRaises(ValueError) as caught:
            scaffold.build_config(["definitely-not-an-agent"])
        message = str(caught.exception)
        suggested = {part.strip() for part in message.split("choose from", 1)[1].split(",")}
        self.assertEqual(suggested, set(scaffold.KNOWN_AGENTS))

    def test_every_listed_agent_scaffolds(self):
        """A name offered by the CLI must produce a config, not an exception."""
        for name in scaffold.KNOWN_AGENTS:
            with self.subTest(agent=name):
                config = scaffold.build_config([name])
                self.assertTrue(config, f"{name} is offered but scaffolds nothing")


class RemoteAgentsAreOfferedButNotForcedIntoPresets(unittest.TestCase):
    """Three hosted templates point at real vendor hosts.

    `config` refuses a non-loopback endpoint unless `JURY_ALLOW_REMOTE_ENDPOINT`
    is set — a deliberate default-closed posture, since a config-supplied URL is
    otherwise a request-forgery primitive. So adding them to `KNOWN_AGENTS`
    naively made `jury init --preset thorough` produce a config it then refused
    to write, and the command failed outright.

    The line is: listed and selectable by name always, included in "all" only
    once the opt-in is present.
    """

    def test_the_remote_set_is_derived_from_the_templates(self):
        needs = set(scaffold.agents_needing_remote_opt_in())
        self.assertTrue(needs, "no template has a remote endpoint; this guard is vacuous")
        for name in needs:
            with self.subTest(agent=name):
                endpoint = scaffold.agent_templates()[name].get("endpoint", "")
                self.assertTrue(endpoint, f"{name} is flagged remote but has no endpoint")
                self.assertNotIn("localhost", endpoint)
                self.assertNotIn("127.0.0.1", endpoint)

    def test_a_loopback_template_is_not_flagged_remote(self):
        """The counterweight: `qwen` points at a local Ollama and must stay in."""
        self.assertNotIn("qwen", scaffold.agents_needing_remote_opt_in())

    def test_every_remote_agent_is_still_listed_and_selectable(self):
        for name in scaffold.agents_needing_remote_opt_in():
            with self.subTest(agent=name):
                self.assertIn(name, scaffold.KNOWN_AGENTS, "not discoverable")
                self.assertTrue(
                    scaffold.build_config([name]),
                    f"{name} is offered but cannot be chosen by name",
                )


class EffortScaffoldingTest(unittest.TestCase):
    """`jury init` records a chosen effort and hints at it otherwise (issue #662)."""

    def test_effort_is_written_only_for_vendors_that_support_it(self):
        cfg = build_config(["claude", "codex", "gemini-api"], effort="high")
        by_name = {a["name"]: a for a in cfg["agent"]}
        # A hosted API takes an effort level; the claude/codex CLIs do not, so
        # scaffolding one there would only ever produce a runtime warning.
        self.assertEqual(by_name["gemini-api"]["effort"], "high")
        self.assertNotIn("effort", by_name["claude"])
        self.assertNotIn("effort", by_name["codex"])

    def test_no_effort_argument_writes_no_effort_key(self):
        cfg = build_config(["gemini-api"])
        self.assertNotIn("effort", cfg["agent"][0])

    def test_rendered_effort_round_trips_through_the_loader(self):
        text = render_toml(build_config(["gemini-api"], effort="low"))
        self.assertIn('effort = "low"', text)
        parsed = tomllib.loads(text)
        # The template leaves `model` for the operator, so the only warning is
        # the pre-existing "no model" one; effort itself is valid.
        self.assertFalse(
            [w for w in validate_config(parsed) if "effort" in w],
            validate_config(parsed),
        )
        self.assertEqual(_from_dict(parsed).agents[0].effort, "low")

    def test_commented_hint_is_written_for_effort_capable_agents(self):
        text = render_toml(build_config(["gemini-api"]))
        self.assertIn('# effort = "medium"', text)
        # A hint is a comment: the config still parses with no effort set.
        self.assertIsNone(_from_dict(tomllib.loads(text)).agents[0].effort)

    def test_no_hint_where_the_vendor_has_no_effort_control(self):
        text = render_toml(build_config(["claude", "codex"]))
        self.assertNotIn("effort", text)

    def test_explicit_effort_replaces_the_hint(self):
        text = render_toml(build_config(["gemini-api"], effort="high"))
        self.assertIn('effort = "high"', text)
        self.assertNotIn('# effort = "medium"', text)


class InteractiveEffortQuestionTest(unittest.TestCase):
    """The effort question exists only on the interactive path (issue #662)."""

    AVAILABLE = {"claude": True}

    def _ask(self, effort_answer):
        answers = iter(["claude", "", "", "", effort_answer])
        with contextlib.redirect_stderr(io.StringIO()):
            return cli._init_interactive(
                self.AVAILABLE, input_fn=lambda _p: next(answers), models_fn=lambda _e: []
            )

    def test_chosen_effort_is_recorded(self):
        self.assertEqual(self._ask("high")["effort"], "high")

    def test_answer_is_case_insensitive(self):
        self.assertEqual(self._ask(" Medium ")["effort"], "medium")

    def test_enter_skips_and_writes_nothing(self):
        self.assertIsNone(self._ask("")["effort"])

    def test_an_unrecognized_answer_is_treated_as_a_skip(self):
        self.assertIsNone(self._ask("turbo")["effort"])

    def test_the_wizard_does_not_ask(self):
        # The guided wizard's question set is deliberately unchanged.
        answers = iter(["claude", "", "", "", "", "", "", ""])
        with contextlib.redirect_stderr(io.StringIO()):
            kwargs = cli._init_wizard(
                self.AVAILABLE, input_fn=lambda _p: next(answers), models_fn=lambda _e: []
            )
        self.assertNotIn("effort", kwargs)


if __name__ == "__main__":
    unittest.main()
