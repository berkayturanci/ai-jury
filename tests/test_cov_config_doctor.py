"""Offline coverage tests for config.py and doctor.py error/edge branches.

All tests are deterministic and network/subprocess-free: PATH-dependent and
local-model probes are mocked, and TOML is fed via tempfiles. Import style and
fixture conventions match tests/test_config_validation.py and the
DoctorDiagnosticsTests in tests/test_perfile_coverage.py.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import config as cfgmod  # noqa: E402
from ai_jury import doctor  # noqa: E402
from ai_jury.config import (  # noqa: E402
    ConfigError,
    _ci_from_dict,
    _from_dict,
    _opt_positive_int,
    _seed_from_dict,
    _str_list,
    load_raw_config,
    validate_config,
)


def _agent(**over):
    a = {"name": "a", "vendor": "anthropic", "command": "x"}
    a.update(over)
    return a


def _full(jury=None, agents=None):
    return {
        "jury": jury or {"rounds": 1, "chair": "a"},
        "agent": agents if agents is not None else [_agent()],
    }


class ConfigValidationBranches(unittest.TestCase):
    """Hard-error branches in validate_config not covered elsewhere."""

    def test_diff_max_bytes_non_positive_errors(self):
        # config.py:194 — jury.diff.{key} must be a positive integer.
        with self.assertRaises(ConfigError) as ctx:
            validate_config(_full(jury={"rounds": 1, "chair": "a", "diff": {"max_bytes": 0}}))
        self.assertIn("jury.diff.max_bytes", str(ctx.exception))

    def test_diff_chunk_max_bytes_non_int_errors(self):
        with self.assertRaises(ConfigError) as ctx:
            validate_config(
                _full(jury={"rounds": 1, "chair": "a", "diff": {"chunk_max_bytes": "big"}})
            )
        self.assertIn("jury.diff.chunk_max_bytes", str(ctx.exception))

    def test_agent_timeout_non_positive_errors(self):
        # config.py:256 — per-agent timeout must be a positive integer.
        with self.assertRaises(ConfigError) as ctx:
            validate_config(_full(agents=[_agent(timeout=0)]))
        self.assertIn("timeout must be a positive integer", str(ctx.exception))

    def test_agent_timeout_bool_errors(self):
        # bool is explicitly rejected even though bool is an int subclass.
        with self.assertRaises(ConfigError):
            validate_config(_full(agents=[_agent(timeout=True)]))


class CiFromDictBranches(unittest.TestCase):
    def test_fail_on_scalar_is_wrapped(self):
        # config.py:404 — a non-list fail_on is coerced to a single-item list.
        ci = _ci_from_dict({"fail_on": "Critical"})
        self.assertEqual(ci.fail_on, ["critical"])

    def test_fail_on_list_is_normalized(self):
        ci = _ci_from_dict({"fail_on": [" Major ", "", "MINOR"]})
        self.assertEqual(ci.fail_on, ["major", "minor"])


class StrListBranches(unittest.TestCase):
    def test_string_wrapped_to_list(self):
        # config.py:422 — a bare string becomes a single-element list.
        self.assertEqual(_str_list("a.py"), ["a.py"])

    def test_non_list_non_string_returns_empty(self):
        # config.py:424 — anything else (e.g. int/dict) coerces to [].
        self.assertEqual(_str_list(5), [])
        self.assertEqual(_str_list({"k": "v"}), [])

    def test_list_entries_stripped_and_filtered(self):
        self.assertEqual(_str_list([" a ", "", "b"]), ["a", "b"])


class SeedFromDictBranches(unittest.TestCase):
    def test_none_seed(self):
        self.assertIsNone(_seed_from_dict({}))

    def test_bool_seed_is_none(self):
        self.assertIsNone(_seed_from_dict({"seed": True}))

    def test_numeric_string_seed_parsed(self):
        # config.py:450-451 — int() succeeds on a numeric string.
        self.assertEqual(_seed_from_dict({"seed": "42"}), 42)

    def test_int_seed_passthrough(self):
        self.assertEqual(_seed_from_dict({"seed": 7}), 7)

    def test_invalid_seed_degrades_to_none(self):
        # config.py:452-453 — int() raises ValueError -> None.
        self.assertIsNone(_seed_from_dict({"seed": "not-a-number"}))


class OptPositiveIntBranches(unittest.TestCase):
    def test_none_and_bool(self):
        self.assertIsNone(_opt_positive_int(None))
        self.assertIsNone(_opt_positive_int(True))

    def test_invalid_string_degrades_to_none(self):
        # config.py:469-470 — int() raises ValueError -> None.
        self.assertIsNone(_opt_positive_int("nope"))

    def test_non_positive_is_none(self):
        self.assertIsNone(_opt_positive_int(0))
        self.assertIsNone(_opt_positive_int(-3))

    def test_positive_value(self):
        self.assertEqual(_opt_positive_int(5), 5)

    def test_numeric_string_value(self):
        self.assertEqual(_opt_positive_int("9"), 9)


class LoadRawConfigBranches(unittest.TestCase):
    def test_default_when_no_jury_toml(self):
        # config.py:591 — path=None and no jury.toml in cwd -> DEFAULT_CONFIG.
        with tempfile.TemporaryDirectory(), mock.patch("ai_jury.config.Path") as path_cls:
            # Make Path("jury.toml").exists() return False.
            candidate = mock.Mock()
            candidate.exists.return_value = False
            path_cls.return_value = candidate
            data = load_raw_config(None)
        self.assertIs(data, cfgmod.DEFAULT_CONFIG)

    def test_explicit_missing_path_raises(self):
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / "nope.toml"
            with self.assertRaises(FileNotFoundError):
                load_raw_config(missing)


class FromDictIntegration(unittest.TestCase):
    """_from_dict threads through the helper branches above end-to-end."""

    def test_from_dict_coerces_helpers(self):
        data = {
            "jury": {
                "rounds": 3,
                "chair": "a",
                "seed": "11",
                "total_timeout": "bad",
                "max_rounds": "5",
                "ci": {"fail_on": "critical"},
                "context": {"mode": "EXPANDED"},
                "diff": {"exclude": "vendor/**", "include": ["x.py"]},
            },
            "agent": [_agent()],
        }
        cfg = _from_dict(data)
        self.assertEqual(cfg.seed, 11)
        self.assertIsNone(cfg.total_timeout)  # invalid -> None
        self.assertEqual(cfg.max_rounds, 5)
        self.assertEqual(cfg.ci.fail_on, ["critical"])
        self.assertEqual(cfg.context.mode, "expanded")
        self.assertEqual(cfg.diff.exclude, ["vendor/**"])


class DoctorWarningBranches(unittest.TestCase):
    """build_diagnostics warning/error branches in doctor.py."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_no_agents_configured_warning(self):
        # doctor.py:107 — empty [[agent]] list -> "no agents are configured".
        cfg = self.d / "jury.toml"
        cfg.write_text('[jury]\nrounds = 1\nchair = "a"\n')
        # load_config tolerates an empty agent list (validate is off in doctor).
        diag = doctor.build_diagnostics(str(cfg))
        self.assertIn("no agents are configured", diag["config_warnings"])

    def test_local_agent_unreachable_endpoint_warning(self):
        # doctor.py:119-120 — enabled local agent whose endpoint is unreachable.
        cfg = self.d / "jury.toml"
        cfg.write_text(
            '[jury]\nrounds = 1\nchair = "q"\n\n'
            '[[agent]]\nname = "q"\nvendor = "local"\nmodel = "m"\n'
            'endpoint = "http://localhost:11434/v1"\n'
        )
        with mock.patch("ai_jury.doctor._is_available", return_value=False):
            diag = doctor.build_diagnostics(str(cfg))
        self.assertTrue(
            any("(local) endpoint" in w and "not reachable" in w for w in diag["config_warnings"]),
            diag["config_warnings"],
        )

    def test_cli_agent_not_on_path_warning(self):
        cfg = self.d / "jury.toml"
        cfg.write_text(
            '[jury]\nrounds = 1\nchair = "a"\n\n'
            '[[agent]]\nname = "a"\nvendor = "anthropic"\ncommand = "ghost-cli"\n'
        )
        with mock.patch("ai_jury.doctor._is_available", return_value=False):
            diag = doctor.build_diagnostics(str(cfg))
        self.assertTrue(
            any("is not on PATH" in w for w in diag["config_warnings"]),
            diag["config_warnings"],
        )

    def test_keyerror_config_captured(self):
        # doctor.py:199-200 — an agent table missing "name" makes _from_dict
        # raise KeyError, which build_diagnostics catches into config_warnings.
        cfg = self.d / "jury.toml"
        cfg.write_text('[jury]\nrounds = 1\n\n[[agent]]\nvendor = "anthropic"\ncommand = "x"\n')
        diag = doctor.build_diagnostics(str(cfg))
        self.assertIsNone(diag["config"])
        self.assertTrue(
            any(w.startswith("config error:") for w in diag["config_warnings"]),
            diag["config_warnings"],
        )


class DoctorRecommendationBranches(unittest.TestCase):
    """_recommendations branches surfaced via build_diagnostics."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_no_config_suggests_init(self):
        # doctor.py:146 — config_path is None and no jury.toml in cwd.
        with mock.patch("ai_jury.doctor.Path") as path_cls:
            candidate = mock.Mock()
            candidate.exists.return_value = False
            path_cls.return_value = candidate
            # No agents -> not ready; with no local models -> install step.
            with mock.patch("ai_jury.adapters.list_local_models", return_value=[]):
                rec = doctor._recommendations(None, None, [])
        self.assertTrue(any("jury init" in s for s in rec["steps"]), rec["steps"])
        self.assertFalse(rec["ready"])

    def test_not_ready_no_local_models_install_step(self):
        # doctor.py:159 — no available agents and no local models.
        with mock.patch("ai_jury.adapters.list_local_models", return_value=[]):
            rec = doctor._recommendations(
                "jury.toml",
                {"enabled_agents": []},
                [{"name": "a", "available": False}],
            )
        self.assertFalse(rec["ready"])
        self.assertTrue(any("No reviewer is available" in s for s in rec["steps"]), rec["steps"])

    def test_not_ready_local_models_offline_step(self):
        with mock.patch("ai_jury.adapters.list_local_models", return_value=["qwen2.5-coder:7b"]):
            rec = doctor._recommendations(
                "jury.toml",
                {"enabled_agents": []},
                [{"name": "a", "available": False}],
            )
        self.assertTrue(
            any("local model server is reachable" in s for s in rec["steps"]), rec["steps"]
        )

    def test_ready_with_enabled_unavailable_step(self):
        # doctor.py:172-173 — ready, but an enabled agent is unavailable.
        agents = [
            {"name": "up", "available": True},
            {"name": "down", "available": False},
        ]
        rec = doctor._recommendations(
            "jury.toml",
            {"enabled_agents": ["up", "down"]},
            agents,
        )
        self.assertTrue(rec["ready"])
        self.assertTrue(
            any("Enabled but unavailable" in s and "down" in s for s in rec["steps"]), rec["steps"]
        )


class DoctorRenderCapabilityBranches(unittest.TestCase):
    """render_report capability branches (261->263, 263->265)."""

    def _diag(self, *, headless, model_sel):
        return {
            "tool_version": "0.0.0",
            "python_version": "3.11.0",
            "python_implementation": "CPython",
            "python_executable": "/usr/bin/python3",
            "os": "TestOS",
            "config_path": "(default)",
            "agents": [
                {
                    "name": "a",
                    "command": "x",
                    "vendor": "anthropic",
                    "available": True,
                    "version": "1.2.3",
                    "capabilities": {
                        "supports_headless": headless,
                        "supports_model_selection": model_sel,
                        "status": "ok",
                    },
                    "capability_warnings": [],
                }
            ],
            "config": {
                "rounds": 1,
                "chair": "a",
                "context_mode": "diff-only",
                "enabled_agents": ["a"],
            },
            "config_warnings": [],
            "recommendations": {"ready": True, "steps": []},
        }

    def test_both_capabilities_present(self):
        report = doctor.render_report(self._diag(headless=True, model_sel=True))
        self.assertIn("headless, model-selection", report)

    def test_no_capabilities(self):
        report = doctor.render_report(self._diag(headless=False, model_sel=False))
        self.assertIn("capabilities=[none]", report)

    def test_only_headless(self):
        report = doctor.render_report(self._diag(headless=True, model_sel=False))
        self.assertIn("capabilities=[headless]", report)


if __name__ == "__main__":
    unittest.main()
