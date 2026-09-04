"""Local diagnostics for the agent review jury (``jury --doctor``).

The ``--doctor`` command reports local readiness and common configuration
problems. Its output is intentionally SAFE to share:

- It includes tool/Python/OS versions, a redacted config summary, agent
  availability (which agent CLIs are on PATH), each agent's detected CLI
  version and capability summary, and detected config warnings.
- It NEVER includes the raw diff under review or any agent output.
- Secret-like values in the config summary are redacted via
  :func:`ai_jury.redaction.redact`.

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
from .adapters import effort_supported, make_adapter
from .config import ConfigError, load_config
from .panel import shortfall
from .redaction import redact, redact_url_userinfo

#: Version of the machine-readable export emitted by ``jury --doctor --json``.
#: Bump this (and ``tests/test_doctor.py``'s schema test) on any breaking change
#: to the shape produced by :func:`doctor_report_dict`.
DOCTOR_SCHEMA_VERSION = "ai-jury.doctor.v1"

#: Default endpoint assumed for a ``vendor = "local"`` agent with none configured.
_DEFAULT_LOCAL_ENDPOINT = "http://localhost:11434/v1"


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
            "warnings": [f"capability probe raised: {redact(str(exc))[0]}"],
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


def _resolved_command(spec):
    """Absolute path a CLI agent's command resolves to on PATH (issue #296).

    Lets an operator verify *which* binary will run (a poisoned PATH could
    resolve a bare name to a shim). None for a local/HTTP agent (no command) or
    when nothing is found on PATH.
    """
    command = getattr(spec, "command", "") or ""
    vendor = (getattr(spec, "vendor", "") or "").lower()
    has_endpoint = bool(getattr(spec, "endpoint", None))
    if (
        not command
        or vendor in ("local", "anthropic-api", "openai-api", "google-api", "openai-compatible")
        or vendor.endswith("-api")
        or has_endpoint
    ):
        return None
    try:
        return shutil.which(command)
    except Exception:  # noqa: BLE001 - diagnostics must never crash
        return None


def _probe_models(spec):
    """Model ids this agent could be pointed at, or None (issue #662).

    Delegates to the adapter's own ``list_models`` seam — ``agy models`` for
    Antigravity, the OpenAI-compatible ``/models`` listing for a local server —
    and, like every other doctor probe, swallows any failure.
    """
    try:
        models = make_adapter(spec).list_models()
    except Exception:  # noqa: BLE001 - diagnostics must never crash
        return None
    if not models:
        return None
    return [_redact_value(m) for m in models]


def _endpoint_for(spec):
    """The HTTP endpoint an agent talks to, or None for a CLI agent.

    A configured ``endpoint`` wins (with any userinfo credentials stripped); a
    hosted-API vendor reports its adapter's fixed vendor URL, so the export
    answers "where would this actually go?" for every non-CLI transport.
    """
    if getattr(spec, "endpoint", None):
        return redact_url_userinfo(spec.endpoint)
    vendor = (getattr(spec, "vendor", "") or "").lower()
    if vendor == "local":
        return _DEFAULT_LOCAL_ENDPOINT
    try:
        api_url = getattr(make_adapter(spec), "_api_url", None)
        return api_url() if callable(api_url) else None
    except Exception:  # noqa: BLE001 - diagnostics must never crash
        return None


def _unavailable_reason(spec, capability_warnings) -> str:
    """Why an agent is not usable, in one line (pure given its inputs).

    Prefers the adapter's own capability warning (e.g. "ANTHROPIC_API_KEY is not
    set") so the export cannot drift from what the adapter reports, and falls
    back to a transport-appropriate message when the probe said nothing.
    """
    if capability_warnings:
        return "; ".join(capability_warnings)
    vendor = (getattr(spec, "vendor", "") or "").lower()
    if vendor == "local":
        endpoint = redact_url_userinfo(spec.endpoint or _DEFAULT_LOCAL_ENDPOINT)
        return f"endpoint '{endpoint}' is not reachable"
    if vendor in _HOSTED_API_VENDORS or vendor.endswith("-api"):
        return "the hosted API is not reachable"
    if getattr(spec, "command", ""):
        return f"command '{_redact_value(spec.command)}' is not on PATH"
    return "not available"


def _agent_entry(spec, probe_models: bool = False):
    caps = _detect_capabilities(spec)
    available = _is_available(spec)
    capability_warnings = [_redact_value(w) for w in caps.get("warnings", [])]
    return {
        "name": _redact_value(spec.name),
        "command": _redact_value(spec.command),
        "endpoint": _endpoint_for(spec),
        "resolved": _resolved_command(spec),
        "vendor": _redact_value(spec.vendor),
        "available": available,
        "reason": None if available else _unavailable_reason(spec, capability_warnings),
        "version": _redact_value(caps.get("version")),
        "capabilities": {
            "supports_headless": caps.get("supports_headless"),
            "supports_model_selection": caps.get("supports_model_selection"),
            "status": caps.get("status"),
        },
        # Only the JSON export renders a model listing, and discovering one
        # costs a subprocess (`agy models`) or an HTTP round trip per agent. The
        # text report would pay that for nothing, so the probe is opt-in.
        "models": _probe_models(spec) if probe_models else None,
        "effort": _redact_value(getattr(spec, "effort", None)),
        "effort_supported": effort_supported(getattr(spec, "vendor", "")),
        "capability_warnings": capability_warnings,
    }


def _config_summary(cfg):
    """Build a redacted, secret-free summary of the loaded config."""
    return {
        "rounds": cfg.rounds,
        "chair": _redact_value(cfg.chair),
        "context_mode": _redact_value(cfg.context.mode),
        "enabled_agents": [_redact_value(a.name) for a in cfg.enabled_agents],
    }


# Hosted-API vendors (issue #430/#432): no `command`/`endpoint`, so neither
# the "local" nor the "CLI on PATH" branch below is the right diagnosis when
# one is unavailable.
_HOSTED_API_VENDORS = ("anthropic-api", "openai-api", "google-api")


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
        warnings.append(f"chair '{_redact_value(cfg.chair)}' does not match any configured agent")
    for agent in enabled:
        if _is_available(agent):
            continue
        if agent.vendor == "local":
            warnings.append(
                f"agent '{_redact_value(agent.name)}' (local) endpoint "
                f"'{redact_url_userinfo(agent.endpoint or 'http://localhost:11434/v1')}' "
                f"is not reachable"
            )
        elif agent.vendor in _HOSTED_API_VENDORS:
            # Reuse the adapter's own capability warning (issue #430) instead
            # of re-deriving the vendor -> env-var mapping here, so the
            # message can't drift from what the adapter actually reports.
            caps = _detect_capabilities(agent)
            reason = "; ".join(caps.get("warnings", [])) or "the hosted API is not reachable"
            warnings.append(f"agent '{_redact_value(agent.name)}' (hosted API): {reason}")
        else:
            warnings.append(
                f"agent '{_redact_value(agent.name)}' command "
                f"'{_redact_value(agent.command)}' is not on PATH"
            )
    return warnings


def _panel_readiness(cfg, agents) -> dict:
    """How close this machine is to being able to form cross-vendor consensus.

    Doctor is offline and runs no review, so it can only report what it can
    see: how many distinct vendors are ENABLED, and how many of those are
    reachable. ``contributing_vendors`` is therefore always ``None`` here — the
    contributed-vendor count is a property of a run, and lives in the run
    metadata's ``panel.vendors``. Saying so in the export is the point: an
    available CLI that returns nothing is exactly the failure #635 was, and a
    green doctor is not evidence against it.
    """
    entries = {entry["name"]: entry for entry in agents}
    enabled = list(getattr(cfg, "enabled_agents", []) or [])
    configured = {(a.vendor or "").strip().lower() for a in enabled} - {""}
    available = {
        (a.vendor or "").strip().lower()
        for a in enabled
        if entries.get(a.name, {}).get("available")
    } - {""}
    minimum = int(getattr(cfg.ci, "min_vendors", 0) or 0)
    # The number a downstream consumer counts, which doctor never reported and
    # which is not the vendor count (#699): one review per agent that answers.
    # The chair's synthesis record is not added here — a consumer reads it as the
    # panel's consensus, not as a review, and adding it is how a bench with
    # nothing reachable came to advertise one review. Doctor can only see
    # reachability, so this is the CEILING — an agent that runs and returns
    # nothing casts no ballot — which is why it is labelled "at most" below.
    seats = sum(1 for a in enabled if entries.get(a.name, {}).get("available"))
    panel = {
        "vendors_configured": len(configured),
        "vendors_available": len(available),
        "min_vendors": minimum,
        "contributing_vendors": None,
        "panelists_available": seats,
        "reviews_supplied_max": seats,
        "min_reviews": int(getattr(cfg.ci, "min_reviews", 0) or 0),
    }
    # Derived from the same predicate the warning uses, so the field and the
    # warning cannot disagree about the same machine (#682, round 3).
    panel["multi_vendor_ready"] = not _gate_would_fail(panel)
    return panel


#: What a machine with no loadable config can prove about its panel: nothing.
#: ``multi_vendor_ready`` is ``False`` here for a different reason than below —
#: not "the gate would fail" but "there is no config to satisfy", which is not a
#: readiness anyone should build on.
_NO_PANEL = {
    "vendors_configured": 0,
    "vendors_available": 0,
    "min_vendors": 0,
    "contributing_vendors": None,
    "panelists_available": 0,
    "reviews_supplied_max": 0,
    "min_reviews": 0,
    "multi_vendor_ready": False,
}


def _gate_would_fail(panel) -> bool:
    """Would a run on this machine fail the cross-vendor gate?

    The single place doctor decides that, so the ``multi_vendor_ready`` field
    and the warning below are the same judgement rendered twice. It mirrors
    :func:`metadata.collapse_reason`'s scoping term for term: the gate is off at
    ``0``; a config with fewer distinct vendors enabled than the threshold never
    claimed that consensus and is left alone; otherwise every configured vendor
    short of the threshold is a vendor the run cannot hear from.

    Doctor can only see reachability, so this is a *lower* bound on failure: a
    reachable CLI that returns nothing (#635) fails the gate too, and no offline
    check can predict it. That is why the report says so in as many words.
    """
    minimum = panel["min_vendors"]
    if minimum <= 0 or panel["vendors_configured"] < minimum:
        return False
    return panel["vendors_available"] < minimum


def _panel_warning(panel) -> str | None:
    """The one actionable thing offline diagnostics can say about the panel.

    Fires only when a run on this machine would actually fail the gate, because
    then it exits 3 and the operator would rather know now. A warning that does
    not predict the run is worse than none — it is the kind people learn to
    scroll past — which is also why a single-vendor config under the shipped
    ``min_vendors = 2`` is silent: :func:`metadata.collapse_reason` leaves that
    run alone, so there is nothing to warn about.
    """
    if not _gate_would_fail(panel):
        return None
    return (
        f"{panel['vendors_configured']} vendors are enabled but only "
        f"{panel['vendors_available']} is/are reachable; a run would fail the "
        f"cross-vendor guard (min_vendors = {panel['min_vendors']}, exit 3). "
        f"Install the missing CLI, or opt out with `--no-min-vendors` "
        f"(`[jury.ci] min_vendors = 0`)."
    )


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
    if config_path is None and not Path("jury.toml").exists():
        steps.append("No jury.toml found — run `jury init` to create one.")

    if not ready:
        from .adapters import list_local_models

        models = list_local_models()
        if models:
            steps.append(
                f"No agent CLI is available, but a local model server is reachable "
                f"({len(models)} model(s): {', '.join(models[:3])}). Add a free local "
                f"reviewer: `jury init --preset offline` (or `--list-models`)."
            )
        else:
            steps.append(
                "No reviewer is available. Install an agent CLI (claude / codex / agy), "
                "or run a local model (e.g. `ollama serve` + `ollama pull "
                'qwen2.5-coder:7b`) and add a `vendor = "local"` agent — or use '
                "`--mock` for an offline demo."
            )
    else:
        missing = [
            a["name"]
            for a in agents
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


def build_diagnostics(config_path=None, probe_models: bool = False):
    """Build a SAFE diagnostics dict for the given config path.

    Best-effort: if the config cannot be loaded, the error is captured as a
    string under ``config_warnings`` and ``config`` is left ``None``. Never
    raises for a bad/missing config. The returned dict never contains the raw
    diff or any agent output.

    ``probe_models`` opts into discovering each agent's available model ids —
    a subprocess (``agy models``) or an HTTP round trip *per agent*, each
    time-boxed but not free. Only ``--doctor --json`` renders that listing, so
    it defaults off: the human report used to pay ~2 s (and up to the probe
    timeout if a CLI hangs) for a field it never printed.
    """
    config_summary = None
    config_warnings: list[str] = []
    agents: list = []
    panel: dict = dict(_NO_PANEL)

    try:
        cfg = load_config(config_path)
    except FileNotFoundError as exc:
        config_warnings.append(f"config error: {redact(str(exc))[0]}")
    except tomllib.TOMLDecodeError as exc:
        config_warnings.append(f"config error: invalid TOML: {redact(str(exc))[0]}")
    except ConfigError as exc:
        config_warnings.append(f"config error: {redact(str(exc))[0]}")
    except (KeyError, ValueError, TypeError) as exc:
        config_warnings.append(f"config error: {redact(str(exc))[0]}")
    else:
        config_summary = _config_summary(cfg)
        agents = [_agent_entry(spec, probe_models=probe_models) for spec in cfg.agents]
        config_warnings = _detect_warnings(cfg)
        # Fold capability/version probe warnings (e.g. an available CLI whose
        # version could not be detected) into the user-facing warnings list.
        # Probes already ran while building the agent entries above.
        enabled_names = {a.name for a in cfg.enabled_agents}
        for spec, entry in zip(cfg.agents, agents, strict=False):
            if spec.name not in enabled_names:
                continue
            for warning in entry.get("capability_warnings", []):
                config_warnings.append(f"agent '{entry['name']}': {warning}")
        # Cross-vendor readiness (issue #682), after the availability probes the
        # agent entries already ran — this adds no probe of its own.
        panel = _panel_readiness(cfg, agents)
        panel_warning = _panel_warning(panel)
        if panel_warning:
            config_warnings.append(panel_warning)
        # A bench that cannot reach the consumer's minimum is a shortfall this
        # machine can prove offline (#699) — worth saying here rather than after
        # the run, which is where it used to surface.
        short = shortfall(
            panel["panelists_available"],
            panel["min_reviews"],
            stage="on this machine",
        )
        if short:
            config_warnings.append(short)

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
        "panel": panel,
        "recommendations": _recommendations(config_path, config_summary, agents),
    }


# Capability flags -> the short labels shown in both renderers, in a fixed order.
_CAPABILITY_LABELS = (
    ("supports_headless", "headless"),
    ("supports_model_selection", "model-selection"),
)


def capability_labels(capabilities) -> list[str]:
    """Short capability labels for one agent's capability dict (pure)."""
    caps = capabilities or {}
    return [label for key, label in _CAPABILITY_LABELS if caps.get(key)]


def _transport(vendor: str, command: str, endpoint) -> str:
    """Classify how an agent is reached: ``cli``, ``api`` or ``local`` (pure)."""
    name = (vendor or "").strip().lower()
    if name == "local":
        return "local"
    if name.endswith("-api") or name == "openai-compatible":
        return "api"
    if command:
        return "cli"
    return "api" if endpoint else "cli"


def doctor_report_dict(diagnostics) -> dict:
    """Project a diagnostics dict onto the stable ``ai-jury.doctor.v1`` export.

    PURE: it runs no probes and touches no network, PATH or filesystem — every
    fact comes from the dict :func:`build_diagnostics` already built, so the
    probes run exactly once no matter how many renderers consume them.

    Both renderers consume this: ``jury --doctor --json`` serializes it, and
    :func:`render_report` renders its agent rows from it, so the human report
    and the machine export cannot describe the panel differently.

    Secrets are never included — only environment *variable names*, which reach
    this dict through the adapters' own capability warnings.
    """
    agents = []
    for entry in diagnostics.get("agents", []):
        vendor = entry.get("vendor") or ""
        command = entry.get("command") or ""
        endpoint = entry.get("endpoint")
        transport = _transport(vendor, command, endpoint)
        item = {
            "name": entry.get("name"),
            "vendor": vendor,
            "transport": transport,
            "available": bool(entry.get("available")),
            "reason": entry.get("reason"),
        }
        # One address key per agent, named for the transport that reaches it.
        if transport == "cli":
            item["command"] = command
        else:
            item["endpoint"] = endpoint
        models = entry.get("models")
        item["resolved"] = entry.get("resolved")
        item["version"] = entry.get("version")
        item["capabilities"] = capability_labels(entry.get("capabilities"))
        item["models"] = list(models) if models else None
        item["effort_supported"] = bool(entry.get("effort_supported"))
        item["effort"] = entry.get("effort")
        agents.append(item)

    recommendations = diagnostics.get("recommendations") or {}
    return {
        "schema_version": DOCTOR_SCHEMA_VERSION,
        "tool_version": diagnostics.get("tool_version"),
        "python": diagnostics.get("python_version"),
        "config_path": diagnostics.get("config_path"),
        "ready": bool(recommendations.get("ready")),
        # Cross-vendor readiness (issue #682). ``contributing_vendors`` is null
        # by construction: doctor runs no review, so the contributed count is
        # only ever known from a run's metadata (``panel.vendors``). A consumer
        # reading this must not treat availability as contribution.
        "panel": dict(diagnostics.get("panel") or _NO_PANEL),
        "agents": agents,
        "warnings": list(diagnostics.get("config_warnings") or []),
    }


def render_report(diagnostics) -> str:
    """Render a human-readable text report from a diagnostics dict.

    The Agents section and the readiness line are rendered from
    :func:`doctor_report_dict`, the same projection ``--json`` serializes, so
    the two views cannot drift; the capability *probe status* is read alongside
    it from the raw diagnostics (it is a diagnostic detail, not part of the
    exported schema).
    """
    report = doctor_report_dict(diagnostics)
    lines = []
    lines.append("jury doctor")
    lines.append("=" * 40)
    lines.append(f"tool version:   {diagnostics['tool_version']}")
    lines.append(
        f"python:         {diagnostics['python_version']} ({diagnostics['python_implementation']})"
    )
    lines.append(f"python exe:     {diagnostics['python_executable']}")
    lines.append(f"os:             {diagnostics['os']}")
    lines.append(f"config path:    {diagnostics['config_path']}")
    lines.append("")

    lines.append("Agents")
    lines.append("-" * 40)
    agents = report["agents"]
    if not agents:
        lines.append("  (no agents loaded)")
    else:
        for agent, probe in zip(agents, diagnostics["agents"], strict=False):
            status = "available" if agent["available"] else "MISSING"
            # A CLI agent is identified by its command; an api/local agent has
            # none, so name the endpoint it actually talks to rather than
            # printing an empty `command=`.
            if agent["transport"] == "cli":
                address = f"command={agent.get('command', '')}"
            else:
                address = f"endpoint={agent.get('endpoint') or '(unknown)'}"
            lines.append(f"  [{status:>9}] {agent['name']} (vendor={agent['vendor']}, {address})")
            if agent.get("command"):
                resolved = agent.get("resolved") or "(not found on PATH)"
                lines.append(f"              resolved: {resolved}")
            version = agent.get("version") or "unknown"
            cap_summary = ", ".join(agent["capabilities"]) or "none"
            cap_status = (probe.get("capabilities") or {}).get("status") or "unknown"
            summary = (
                f"              version={version}, capabilities=[{cap_summary}] "
                f"(probe: {cap_status})"
            )
            if agent.get("effort"):
                summary += f", effort={agent['effort']}"
            lines.append(summary)
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

    panel = report["panel"]
    lines.append("Cross-vendor readiness")
    lines.append("-" * 40)
    lines.append(f"  vendors enabled:   {panel['vendors_configured']}")
    lines.append(f"  vendors reachable: {panel['vendors_available']}")
    lines.append(f"  min_vendors gate:  {panel['min_vendors'] or 'off'}")
    lines.append(f"  cross-vendor ready: {'yes' if panel['multi_vendor_ready'] else 'no'}")
    # The number a consumer counts, said in the same breath as readiness (#699).
    # "cross-vendor ready: yes" on a bench that cannot supply the reviews a gate
    # requires is a true statement that answers the wrong question.
    lines.append(
        f"  reviews for a consumer: at most {panel['reviews_supplied_max']} "
        f"({panel['panelists_available']} panel ballot(s); the chairing agent "
        f"reviews too, so its ballot is one of them, and the chair's synthesis "
        f"record is not a review)"
    )
    lines.append(f"  min_reviews gate:  {panel['min_reviews'] or 'off'}")
    lines.append(
        "  note: this checks availability, not contribution. A reachable CLI "
        "can still return no review (#635) — only a run can prove the panel."
    )
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
    lines.append(f"  ready to run: {'yes' if report['ready'] else 'no'}")
    for step in rec.get("steps", []):
        lines.append(f"  - {step}")
    lines.append("")

    lines.append(
        "Privacy: no telemetry is collected or sent. This report is "
        "local-only and redacts secret-like values."
    )

    return "\n".join(lines)
