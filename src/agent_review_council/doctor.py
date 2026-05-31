"""Local diagnostics for the agent review council (``council --doctor``).

The ``--doctor`` command reports local readiness and common configuration
problems. Its output is intentionally SAFE to share:

- It includes tool/Python/OS versions, a redacted config summary, agent
  availability (which agent CLIs are on PATH), and detected config warnings.
- It NEVER includes the raw diff under review or any agent output.
- Secret-like values in the config summary are redacted via
  :func:`agent_review_council.redaction.redact`.

This project collects and transmits NO telemetry. Diagnostics are built
locally and only written where you explicitly ask (stdout, or ``--write``).
"""

from __future__ import annotations

import platform
import shutil
import sys
import tomllib
from pathlib import Path

from . import __version__
from .config import load_config
from .redaction import redact


def _redact_value(value):
    """Redact a single config value if it looks secret-like.

    ``redact`` operates on text and returns ``(text, count)``; non-string
    values are returned unchanged.
    """
    if isinstance(value, str):
        return redact(value)[0]
    return value


def _agent_entry(spec):
    return {
        "name": _redact_value(spec.name),
        "command": _redact_value(spec.command),
        "vendor": _redact_value(spec.vendor),
        "available": shutil.which(spec.command) is not None,
    }


def _config_summary(cfg):
    """Build a redacted, secret-free summary of the loaded config."""
    return {
        "rounds": cfg.rounds,
        "chair": _redact_value(cfg.chair),
        "context_mode": _redact_value(cfg.context.mode),
        "enabled_agents": [_redact_value(a.name) for a in cfg.enabled_agents],
    }


def _detect_warnings(cfg) -> list[str]:
    """Best-effort config sanity checks reported to the user."""
    warnings: list[str] = []
    if not cfg.agents:
        warnings.append("no agents are configured")
    enabled = cfg.enabled_agents
    if cfg.agents and not enabled:
        warnings.append("all configured agents are disabled")
    names = {a.name for a in cfg.agents}
    if cfg.chair not in names:
        warnings.append(
            f"chair '{_redact_value(cfg.chair)}' does not match any configured agent"
        )
    for agent in enabled:
        if shutil.which(agent.command) is None:
            warnings.append(
                f"agent '{_redact_value(agent.name)}' command "
                f"'{_redact_value(agent.command)}' is not on PATH"
            )
    return warnings


def build_diagnostics(config_path=None):
    """Build a SAFE diagnostics dict for the given config path.

    Best-effort: if the config cannot be loaded, the error is captured as a
    string under ``config_warnings`` and ``config`` is left ``None``. Never
    raises for a bad/missing config. The returned dict never contains the raw
    diff or any agent output.
    """
    config_summary = None
    config_warnings: list[str] = []
    agents: list = []

    try:
        cfg = load_config(config_path)
    except FileNotFoundError as exc:
        config_warnings.append(f"config error: {exc}")
    except tomllib.TOMLDecodeError as exc:
        config_warnings.append(f"config error: invalid TOML: {exc}")
    except (KeyError, ValueError, TypeError) as exc:
        config_warnings.append(f"config error: {exc}")
    else:
        config_summary = _config_summary(cfg)
        agents = [_agent_entry(spec) for spec in cfg.agents]
        config_warnings = _detect_warnings(cfg)

    return {
        "tool_version": __version__,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_executable": sys.executable,
        "os": platform.platform(),
        "config_path": str(config_path) if config_path else "(default)",
        "agents": agents,
        "config": config_summary,
        "config_warnings": config_warnings,
    }


def render_report(diagnostics) -> str:
    """Render a human-readable text report from a diagnostics dict."""
    lines = []
    lines.append("council doctor")
    lines.append("=" * 40)
    lines.append(f"tool version:   {diagnostics['tool_version']}")
    lines.append(
        f"python:         {diagnostics['python_version']} "
        f"({diagnostics['python_implementation']})"
    )
    lines.append(f"python exe:     {diagnostics['python_executable']}")
    lines.append(f"os:             {diagnostics['os']}")
    lines.append(f"config path:    {diagnostics['config_path']}")
    lines.append("")

    lines.append("Agents")
    lines.append("-" * 40)
    agents = diagnostics["agents"]
    if not agents:
        lines.append("  (no agents loaded)")
    else:
        for agent in agents:
            status = "available" if agent["available"] else "MISSING"
            lines.append(
                f"  [{status:>9}] {agent['name']} "
                f"(vendor={agent['vendor']}, command={agent['command']})"
            )
    lines.append("")

    lines.append("Config summary")
    lines.append("-" * 40)
    config = diagnostics["config"]
    if config is None:
        lines.append("  (config could not be loaded)")
    else:
        lines.append(f"  rounds:        {config['rounds']}")
        lines.append(f"  chair:         {config['chair']}")
        lines.append(f"  context mode:  {config['context_mode']}")
        enabled = ", ".join(config["enabled_agents"]) or "(none)"
        lines.append(f"  enabled:       {enabled}")
    lines.append("")

    lines.append("Warnings")
    lines.append("-" * 40)
    warnings = diagnostics["config_warnings"]
    if not warnings:
        lines.append("  (none)")
    else:
        for warning in warnings:
            lines.append(f"  - {warning}")
    lines.append("")

    lines.append(
        "Privacy: no telemetry is collected or sent. This report is "
        "local-only and redacts secret-like values."
    )

    return "\n".join(lines)
