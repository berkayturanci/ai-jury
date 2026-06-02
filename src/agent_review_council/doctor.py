"""Local diagnostics for the agent review council (``council --doctor``).

The ``--doctor`` command reports local readiness and common configuration
problems. Its output is intentionally SAFE to share:

- It includes tool/Python/OS versions, a redacted config summary, agent
  availability (which agent CLIs are on PATH), each agent's detected CLI
  version and capability summary, and detected config warnings.
- It NEVER includes the raw diff under review or any agent output.
- Secret-like values in the config summary are redacted via
  :func:`agent_review_council.redaction.redact`.

This project collects and transmits NO telemetry. Diagnostics are built
locally and only written where you explicitly ask (stdout, or ``--write``).
"""

from __future__ import annotations

import platform
import sys
import tomllib
from pathlib import Path

from . import __version__
from .adapters import make_adapter
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


def _detect_capabilities(spec):
    """Best-effort capability/version probe for one agent spec.

    Uses the real adapter (NOT the mock) so doctor reports actual installed
    versions, but guards against any failure: an unavailable CLI just reports
    ``status="unavailable"`` and a crashing probe degrades to ``unknown_version``.
    This must stay fast (short subprocess timeout) and never crash doctor.
    """
    try:
        adapter = make_adapter(spec)
        return adapter.detect_capabilities()
    except Exception as exc:  # noqa: BLE001 - diagnostics must never crash
        return {
            "version": None,
            "supports_headless": None,
            "supports_model_selection": None,
            "raw_version_output": "",
            "status": "unknown_version",
            "warnings": [f"capability probe raised: {exc}"],
        }


def _is_available(spec) -> bool:
    """Whether an agent is reachable, via its adapter's own check.

    Uses ``adapter.available()`` rather than ``shutil.which`` so a local/HTTP
    agent (issue #43), which has no ``command`` and probes its endpoint instead,
    is reported correctly. Guarded — any failure reads as unavailable.
    """
    try:
        return make_adapter(spec).available()
    except Exception:  # noqa: BLE001 - diagnostics must never crash
        return False


def _agent_entry(spec):
    caps = _detect_capabilities(spec)
    return {
        "name": _redact_value(spec.name),
        "command": _redact_value(spec.command),
        "vendor": _redact_value(spec.vendor),
        "available": _is_available(spec),
        "version": _redact_value(caps.get("version")),
        "capabilities": {
            "supports_headless": caps.get("supports_headless"),
            "supports_model_selection": caps.get("supports_model_selection"),
            "status": caps.get("status"),
        },
        "capability_warnings": [_redact_value(w) for w in caps.get("warnings", [])],
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
        if _is_available(agent):
            continue
        if agent.vendor == "local":
            warnings.append(
                f"agent '{_redact_value(agent.name)}' (local) endpoint "
                f"'{_redact_value(agent.endpoint or 'http://localhost:11434/v1')}' "
                f"is not reachable"
            )
        else:
            warnings.append(
                f"agent '{_redact_value(agent.name)}' command "
                f"'{_redact_value(agent.command)}' is not on PATH"
            )
    return warnings


def _recommendations(config_path, config_summary, agents) -> dict:
    """Build actionable next-steps from the diagnostics (issue: doctor UX).

    Returns ``{"ready": bool, "steps": [str, ...]}``. ``ready`` is true when at
    least one agent is reachable. Steps point the user at the cheapest fix:
    scaffold a config, install a CLI, or use a reachable local model.
    """
    steps: list[str] = []
    available = [a for a in agents if a.get("available")]
    ready = bool(available)

    # No config file in play -> suggest scaffolding one.
    if config_path is None and not Path("council.toml").exists():
        steps.append("No council.toml found — run `council init` to create one.")

    if not ready:
        from .adapters import list_local_models

        models = list_local_models()
        if models:
            steps.append(
                f"No agent CLI is available, but a local model server is reachable "
                f"({len(models)} model(s): {', '.join(models[:3])}). Add a free local "
                f"reviewer: `council init --preset offline` (or `--list-models`)."
            )
        else:
            steps.append(
                "No reviewer is available. Install an agent CLI (claude / codex / agy), "
                "or run a local model (e.g. `ollama serve` + `ollama pull "
                "qwen2.5-coder:7b`) and add a `vendor = \"local\"` agent — or use "
                "`--mock` for an offline demo."
            )
    else:
        missing = [
            a["name"] for a in agents
            if not a.get("available")
            and config_summary
            and a["name"] in config_summary.get("enabled_agents", [])
        ]
        if missing:
            steps.append(
                f"Enabled but unavailable (will be skipped): {', '.join(missing)}. "
                f"Install them or run with `--strict` to fail instead."
            )

    return {"ready": ready, "steps": steps}


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
        # Fold capability/version probe warnings (e.g. an available CLI whose
        # version could not be detected) into the user-facing warnings list.
        # Probes already ran while building the agent entries above.
        enabled_names = {a.name for a in cfg.enabled_agents}
        for spec, entry in zip(cfg.agents, agents, strict=False):
            if spec.name not in enabled_names:
                continue
            for warning in entry.get("capability_warnings", []):
                config_warnings.append(
                    f"agent '{entry['name']}': {warning}"
                )

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
        "recommendations": _recommendations(config_path, config_summary, agents),
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
            version = agent.get("version") or "unknown"
            caps = agent.get("capabilities") or {}
            cap_bits = []
            if caps.get("supports_headless"):
                cap_bits.append("headless")
            if caps.get("supports_model_selection"):
                cap_bits.append("model-selection")
            cap_summary = ", ".join(cap_bits) or "none"
            cap_status = caps.get("status") or "unknown"
            lines.append(
                f"              version={version}, capabilities=[{cap_summary}] "
                f"(probe: {cap_status})"
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

    rec = diagnostics.get("recommendations") or {}
    lines.append("Next steps")
    lines.append("-" * 40)
    lines.append(f"  ready to run: {'yes' if rec.get('ready') else 'no'}")
    for step in rec.get("steps", []):
        lines.append(f"  - {step}")
    lines.append("")

    lines.append(
        "Privacy: no telemetry is collected or sent. This report is "
        "local-only and redacts secret-like values."
    )

    return "\n".join(lines)
