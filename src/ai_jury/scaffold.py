"""Scaffold a ``jury.toml`` from agent selections (issue #107).

Backs the ``jury init`` command: instead of hand-editing TOML, a user (or a
script) picks agents/rounds/chair and this renders a valid config. The cloud
agent templates reuse the **secure-by-default** entries from
:data:`config.DEFAULT_CONFIG` (issue #100) so generated configs are safe; a
``local`` template targets an OpenAI-compatible server (Ollama by default).

Pure and deterministic: building the config dict and rendering it to TOML are
side-effect-free, so they are fully unit-testable; the CLI layer owns prompting,
availability detection, and writing the file.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from .config import DEFAULT_CONFIG

_LOCAL_TEMPLATE = {
    "name": "qwen",
    "vendor": "local",
    "model": "qwen2.5-coder:7b",
    "endpoint": "http://localhost:11434/v1",
}

# Hosted-API templates (issue #430): no `command`/`endpoint` — see
# adapters._HostedApiAdapter. `model` is left for the user to fill in (a
# hardcoded model id here would go stale as vendors deprecate/rename models;
# `validate_config` already warns when it's missing).
_ANTHROPIC_API_TEMPLATE = {"name": "claude-api", "vendor": "anthropic-api", "model": ""}
_OPENAI_API_TEMPLATE = {"name": "codex-api", "vendor": "openai-api", "model": ""}
_GOOGLE_API_TEMPLATE = {"name": "gemini-api", "vendor": "google-api", "model": ""}
_OPENROUTER_TEMPLATE = {
    "name": "openrouter",
    "vendor": "openai-compatible",
    "endpoint": "https://openrouter.ai/api/v1",
    "api_key_env": "OPENROUTER_API_KEY",
    "model": "anthropic/claude-3.5-sonnet",
}
_DEEPSEEK_TEMPLATE = {
    "name": "deepseek",
    "vendor": "openai-compatible",
    "endpoint": "https://api.deepseek.com/v1",
    "api_key_env": "DEEPSEEK_API_KEY",
    "model": "deepseek-coder",
}
_GROQ_TEMPLATE = {
    "name": "groq",
    "vendor": "openai-compatible",
    "endpoint": "https://api.groq.com/openai/v1",
    "api_key_env": "GROQ_API_KEY",
    "model": "llama-3.3-70b-versatile",
}
_GENERIC_CLI_TEMPLATE = {
    "name": "aider",
    "vendor": "cli",
    "command": "aider",
    "prompt_mode": "stdin",
    "extra_args": ["--no-auto-commits", "--read-only"],
}


def _from_default(name: str) -> dict | None:
    for a in DEFAULT_CONFIG.get("agent", []):
        if a.get("name") == name:
            return dict(a)
    return None


def agent_templates() -> dict[str, dict]:
    """Built-in agent templates keyed by short name (a fresh copy each call)."""
    templates: dict[str, dict] = {}
    for name in ("claude", "codex", "agy"):
        tmpl = _from_default(name)
        if tmpl is not None:
            templates[name] = tmpl
    templates["qwen"] = dict(_LOCAL_TEMPLATE)
    templates["claude-api"] = dict(_ANTHROPIC_API_TEMPLATE)
    templates["codex-api"] = dict(_OPENAI_API_TEMPLATE)
    templates["gemini-api"] = dict(_GOOGLE_API_TEMPLATE)
    templates["openrouter"] = dict(_OPENROUTER_TEMPLATE)
    templates["deepseek"] = dict(_DEEPSEEK_TEMPLATE)
    templates["groq"] = dict(_GROQ_TEMPLATE)
    templates["aider"] = dict(_GENERIC_CLI_TEMPLATE)
    return templates


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def agents_needing_remote_opt_in() -> tuple[str, ...]:
    """Templates whose endpoint the config validator refuses without an opt-in.

    `config` accepts a loopback endpoint and refuses every other host unless
    ``JURY_ALLOW_REMOTE_ENDPOINT`` is set — a deliberate default-closed posture,
    since a config-supplied URL is otherwise a request-forgery primitive. Three
    hosted templates point at real vendors, so a preset that silently includes
    them produces a config `jury init` then refuses to write.

    Derived from the templates rather than listed, so a new hosted template is
    covered the day it lands.
    """
    remote = []
    for name, template in agent_templates().items():
        endpoint = template.get("endpoint")
        if not endpoint:
            continue
        host = urlsplit(endpoint).hostname or ""
        if host.lower() not in _LOOPBACK_HOSTS:
            remote.append(name)
    return tuple(remote)


#: Every agent `jury init` can scaffold, in the order it offers them.
#:
#: Derived from :func:`agent_templates` rather than listed, because a second
#: hand-written copy of the same set is what #589 asked to be fixed and #590
#: did not: four templates — ``openrouter``, ``deepseek``, ``groq``, ``aider`` —
#: shipped without ever reaching this tuple, so ``jury init --list-agents``, the
#: wizard, and ``--preset all`` could not see them, while the error message for
#: an unknown agent named them. The CLI told users to choose from four options
#: it never offered.
#:
#: ``agent_templates`` reads only module constants, so this costs no I/O at
#: import and is deterministic.
KNOWN_AGENTS: tuple[str, ...] = tuple(agent_templates())

# Substrings that hint a local model is code-oriented (preferred for reviews).
_CODER_HINTS: tuple[str, ...] = ("coder", "code", "deepseek", "qwen")


def pick_default_model(models: list[str]) -> str | None:
    """Choose a sensible default from discovered local models (issue #109).

    Prefers a code-oriented model (name contains 'coder'/'code'/etc.), else the
    first listed; returns None for an empty list.
    """
    if not models:
        return None
    for m in models:
        low = m.lower()
        if any(h in low for h in _CODER_HINTS):
            return m
    return models[0]


# Named setup presets (issue: easier config). Each gives default agents +
# settings for a common intent; explicit flags / detected agents override the
# `agents` value ("detected" = the agents available right now, "all" = every
# known agent). Resolved by the CLI, which knows availability.
PRESETS: dict[str, dict] = {
    "offline": {"agents": ["qwen"], "rounds": 1, "verify": False},
    "fast": {"agents": "detected", "rounds": 1, "verify": False},
    "balanced": {"agents": "detected", "rounds": 2, "verify": True, "early_stop": True},
    "thorough": {"agents": "all", "rounds": 2, "verify": True},
}


def build_config(
    agents: list[str],
    *,
    rounds: int = 2,
    chair: str | None = None,
    verify: bool = True,
    early_stop: bool | None = None,
    local_model: str | None = None,
    local_endpoint: str | None = None,
    decision: str | None = None,
    auto_depth: bool | None = None,
    context_mode: str | None = None,
    redact_secrets: bool | None = None,
    ci_fail_on: list[str] | None = None,
    effort: str | None = None,
) -> dict:
    """Build a jury config dict from selected agent names.

    Raises ``ValueError`` on an unknown agent name or an empty selection. The
    chair defaults to the first selected agent. Local agents pick up the
    optional model/endpoint overrides.

    The optional ``decision``/``auto_depth``/``context_mode``/``redact_secrets``/
    ``ci_fail_on`` knobs (used by ``jury init --wizard``) are written ONLY when
    not ``None`` — callers that omit them produce byte-identical output to before,
    keeping the scaffolded file free of redundant built-in defaults.

    ``effort`` (issue #662) is written onto each selected agent whose vendor can
    act on it; agents whose vendor has no effort control are left alone rather
    than scaffolded with a setting that would only warn at run time.
    """
    templates = agent_templates()
    chosen: list[dict] = []
    seen: set[str] = set()
    for name in agents:
        if name in seen:
            continue
        tmpl = templates.get(name)
        if tmpl is None:
            raise ValueError(f"unknown agent '{name}'; choose from {', '.join(templates.keys())}")
        entry = dict(tmpl)
        if entry.get("vendor") == "local":
            if local_model:
                entry["model"] = local_model
            if local_endpoint:
                entry["endpoint"] = local_endpoint
        if effort and _effort_supported(entry.get("vendor", "")):
            entry["effort"] = effort
        chosen.append(entry)
        seen.add(name)

    if not chosen:
        raise ValueError("select at least one agent")

    if chair is None:
        chair = chosen[0]["name"]

    jury: dict = {"rounds": int(rounds), "chair": chair, "verify": bool(verify)}
    if early_stop:
        jury["early_stop"] = True
    if auto_depth is not None:
        jury["auto_depth"] = bool(auto_depth)
    if decision is not None:
        jury["decision"] = decision
    if context_mode is not None or redact_secrets is not None:
        context: dict = {}
        if context_mode is not None:
            context["mode"] = context_mode
        if redact_secrets is not None:
            context["redact_secrets"] = bool(redact_secrets)
        jury["context"] = context
    if ci_fail_on is not None:
        jury["ci"] = {"fail_on": list(ci_fail_on)}
    return {"jury": jury, "agent": chosen}


def _effort_supported(vendor: str) -> bool:
    """Whether *vendor* has an effort control (see ``adapters.effort_args``).

    Imported lazily so this module keeps its light import graph; ``adapters``
    is the single owner of the vendor -> effort mapping.
    """
    from .adapters import effort_supported

    return effort_supported(vendor)


def _scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    raise TypeError(f"cannot render TOML scalar of type {type(value).__name__}")


def _render_value(value) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_scalar(v) for v in value) + "]"
    return _scalar(value)


# Stable key order for agent tables so output is deterministic and readable.
_AGENT_KEY_ORDER = ("name", "vendor", "command", "endpoint", "model", "effort", "extra_args")

#: Commented hint written under every effort-capable agent that has no explicit
#: level, so the setting is discoverable from the generated file itself.
_EFFORT_HINT = '# effort = "medium"    # low | medium | high'


#: Written under a scaffolded ``[jury.ci]`` (issue #682). Commented out, because
#: the shipped default already IS 2 — the hint exists so a reader discovers the
#: knob and its opt-out here rather than only after a run exits 3.
_MIN_VENDORS_HINT = (
    "# Distinct vendors that must have contributed a review before the run can",
    "# stand as cross-vendor consensus (exit 3 otherwise). Defaults to 2 and only",
    "# applies when 2+ vendors are enabled; set 0 (or pass --no-min-vendors) to",
    "# accept a panel that collapsed to one vendor.",
    "# min_vendors = 2",
)


def render_toml(config: dict) -> str:
    """Render a jury config dict to ``jury.toml`` text (minimal, typed).

    Handles exactly the value types this config uses (str/int/bool/list[str]).
    Empty/None values are omitted so a local agent (no ``command``/``extra_args``)
    stays clean.
    """
    lines = [
        "# Generated by `jury init`. Edit freely — see docs/configuration.md",
        "# for the full schema (rounds, ci gate, context policy, diff handling).",
        "",
        "[jury]",
    ]
    jury = config["jury"]
    # Scalar [jury] keys in a stable, readable order. ``decision``/``auto_depth``
    # are emitted here only when present (the wizard sets them on a non-default).
    for key in ("rounds", "chair", "verify", "decision", "auto_depth", "early_stop", "max_rounds"):
        if key in jury:
            lines.append(f"{key} = {_render_value(jury[key])}")
    lines.append("")

    # Optional nested tables, written only when the wizard captured a non-default.
    context = jury.get("context")
    if context:
        lines.append("[jury.context]")
        for key in ("mode", "redact_secrets"):
            if key in context:
                lines.append(f"{key} = {_render_value(context[key])}")
        lines.append("")
    ci = jury.get("ci")
    if ci and "fail_on" in ci:
        lines.append("[jury.ci]")
        lines.append(f"fail_on = {_render_value(ci['fail_on'])}")
        lines.extend(_MIN_VENDORS_HINT)
        lines.append("")

    for agent in config["agent"]:
        lines.append("[[agent]]")
        for key in _AGENT_KEY_ORDER:
            if key not in agent:
                continue
            value = agent[key]
            if value in (None, "", []):
                continue
            lines.append(f"{key} = {_render_value(value)}")
        # Only hint at `effort` where the vendor can actually act on it; a hint
        # under the `claude`/`codex` CLI blocks would invite a setting that only
        # ever produces an "effort unsupported" warning.
        if not agent.get("effort") and _effort_supported(agent.get("vendor", "")):
            lines.append(_EFFORT_HINT)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
