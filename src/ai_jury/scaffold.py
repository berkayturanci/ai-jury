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

import math
from urllib.parse import urlsplit

from .config import AGY_AGENT, DEFAULT_CONFIG, GENERIC_CLI_VENDORS, adapter_key

#: The one list of model ids the shipped samples name, keyed by the kind of seat
#: (#870). `jury init` writes the hosted and local ones below; the README, the
#: site's integration cards and "Build your jury" demo, `examples/jury.toml` and
#: the docs name the same ids, and ``tests/test_sample_configs.py`` holds every
#: one of them to this table — so a stale id is changed here and the test lists
#: each copy that still disagrees. Each was checked against the vendor's public
#: model list on 2026-09-28 (no API key used): Anthropic's models overview,
#: OpenAI's models page, Google's Gemini API models page, xAI's models page,
#: DeepSeek's pricing page, Groq's models page, Moonshot's pricing page,
#: OpenRouter's public ``/api/v1/models``, Together's serverless model list and
#: Ollama's library tags.
SAMPLE_MODELS: dict[str, str] = {
    "anthropic": "claude-opus-5-5",
    "openai": "gpt-6-sol",
    "google": "gemini-3.8-flash",
    "xai": "grok-4.7",
    "deepseek": "deepseek-v4-pro",
    "groq": "llama-3.3-70b-versatile",
    "moonshot": "kimi-k3",
    "together": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "openrouter": "anthropic/claude-opus-5.5",
    "local": "qwen2.5-coder:7b",
}

_LOCAL_TEMPLATE = {
    "name": "qwen",
    "vendor": "local",
    "model": SAMPLE_MODELS["local"],
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
    "model": SAMPLE_MODELS["openrouter"],
}
_DEEPSEEK_TEMPLATE = {
    "name": "deepseek",
    "vendor": "openai-compatible",
    "endpoint": "https://api.deepseek.com/v1",
    "api_key_env": "DEEPSEEK_API_KEY",
    "model": SAMPLE_MODELS["deepseek"],
}
_GROQ_TEMPLATE = {
    "name": "groq",
    "vendor": "openai-compatible",
    "endpoint": "https://api.groq.com/openai/v1",
    "api_key_env": "GROQ_API_KEY",
    "model": SAMPLE_MODELS["groq"],
}
#: aider in its own read-only configuration (#859). `--read-only` is not an aider
#: option (its options reference has `--read FILE`), so the template it replaces
#: wrote a seat aider refuses to start. Ask mode "never make[s] changes",
#: `--dry-run` modifies no file, and the rest close every other route aider's
#: options reference names to a commit, a shell command, a lint run, a URL fetch
#: or a `.gitignore` edit. `--message` is last: with `prompt_mode = "arg"` the
#: prompt is appended after it. None of this is enforced by jury — there is no
#: sandbox flag for an arbitrary CLI — so the seat is written under
#: :data:`UNSANDBOXED_LABEL`, and the least-privilege audit still warns about it.
_GENERIC_CLI_TEMPLATE = {
    "name": "aider",
    "vendor": "cli",
    "command": "aider",
    "prompt_mode": "arg",
    "extra_args": [
        "--chat-mode",
        "ask",
        "--dry-run",
        "--no-auto-commits",
        "--no-dirty-commits",
        "--no-suggest-shell-commands",
        "--no-auto-lint",
        "--no-detect-urls",
        "--no-gitignore",
        "--message",
    ],
}

#: Written above every bring-your-own CLI seat `jury init` scaffolds (#859): jury
#: knows no sandbox flag for one, so it runs with whatever its own flags allow.
#: The same words label the seat on the site and in the docs.
UNSANDBOXED_LABEL = "unsandboxed \u2014 runs with your permissions"


def _from_default(name: str) -> dict | None:
    for a in [*DEFAULT_CONFIG.get("agent", []), AGY_AGENT]:
        if a.get("name") == name:
            return dict(a)
    return None


#: Templates `jury init` writes only when they are named: never picked by
#: "detected", "all", or an interactive default. agy cannot be confined for a
#: reviewer of untrusted diffs (see ``config.AGY_AGENT``), so seating it is the
#: operator's explicit decision, and `jury init` says so when it writes one.
OPT_IN_AGENTS: tuple[str, ...] = ("agy",)


def implicit_choices(names) -> list[str]:
    """*names* without the opt-in-only agents: what a default may pick."""
    return [n for n in names if n not in OPT_IN_AGENTS]


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
#:
#: Read by ``cli._init_available``, ``cli._init_interactive``, ``cli._init_wizard``
#: and ``cli._run_init`` — every path through which ``jury init`` offers, detects
#: or defaults an agent. Nothing in *this* module reads it, which is the whole of
#: what CodeQL's intra-module ``py/unused-global-variable`` sees (#696): the
#: readers are real, and deleting this tuple would take ``--list-agents``, the
#: wizard and ``--preset all`` with it.
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


def seat_local_agents(
    agents: list[str], models: list[str] | None
) -> tuple[list[str], str | None, list[str]]:
    """Fill the local seats in *agents* from the models a server lists (issue #864).

    Plain ``jury init`` wrote the local template's model whatever the server had,
    then warned about its own output. *models* is ``adapters.local_model_listing``'s
    answer: a list when the server answered, ``None`` when the listing failed.
    Returns ``(seated, model, left_out)``:

    * the server lists models → every agent stays seated and *model* is the one
      :func:`pick_default_model` prefers;
    * the server lists none, other seats present → the local seats move to
      *left_out*, for the caller to write commented out, so the file names no model
      nobody has pulled and its hash is that of the panel that actually runs;
    * the server lists none, local seats only → they stay seated on the template's
      model. A config with no seat is invalid, and the pull hint `jury init` prints
      for an empty server names that same model, so pulling it completes the file;
    * the listing failed (refused, timed out, an error status, an endpoint the SSRF
      gate refuses) → every agent stays seated on the template's model, as before
      #864. No evidence is not evidence of a fault — the rule
      ``doctor._local_model_gap`` follows (#849): a server that is down says
      nothing about what is pulled on it.

    Pure: the caller lists the models, and only when there is a local seat.
    """
    templates = agent_templates()
    local = [a for a in agents if templates.get(a, {}).get("vendor") == "local"]
    model = pick_default_model(models or [])
    if models is None or model is not None or not local or set(local) == set(agents):
        return list(agents), model, []
    return [a for a in agents if a not in local], None, list(dict.fromkeys(local))


# Named setup presets (issue: easier config). Each gives default agents +
# settings for a common intent; explicit flags / detected agents override the
# `agents` value ("detected" = the agents available right now, "all" = every
# known agent). Resolved by the CLI, which knows availability. Neither
# "detected" nor "all" includes an OPT_IN_AGENTS entry: a preset is a default,
# and agy is only ever seated by name.
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
        # EXEMPT from `normalise_vendor` (issue #701, round 3): `entry` is a copy
        # of one of this module's OWN templates, whose vendor strings are literals
        # written here in normalised form. There is no operator spelling to
        # normalise — the value is not yet configuration, it is what this function
        # is about to write out as configuration.
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
    if isinstance(value, float) and math.isfinite(value):
        # `repr` is a valid TOML float for every finite value ("1.0", "0.7",
        # "1e-05"). A non-finite one is refused below: no config key accepts it.
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    raise TypeError(f"cannot render TOML scalar of type {type(value).__name__}")


def _render_value(value) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_scalar(v) for v in value) + "]"
    return _scalar(value)


# Stable key order for agent tables so output is deterministic and readable.
_AGENT_KEY_ORDER = (
    "name",
    "vendor",
    "command",
    "endpoint",
    "model",
    "effort",
    "temperature",
    "extra_args",
    # Written when a template sets it (#859): the aider seat's argv ends in
    # `--message`, which only works when the prompt is appended after it.
    "prompt_mode",
)

#: Commented hint written under every effort-capable agent that has no explicit
#: level, so the setting is discoverable from the generated file itself.
_EFFORT_HINT = '# effort = "medium"    # low | medium | high'

#: Commented hint written under every local seat with no explicit temperature.
#: Commented, not set: the default model does not need it, and a written value
#: would split every generated config's hash for nothing. It is here so the
#: operator who picks a model that loops at the greedy default finds the knob.
_TEMPERATURE_HINT = "# temperature = 1.0   # default 0; set 1.0 for models that loop at 0 (gpt-oss)"


#: Written under every scaffolded ``[jury.ci]`` (issue #682). Commented out,
#: because the shipped default already IS 2 — the hint exists so a reader
#: discovers the knob and its opt-out here rather than only after a run exits 3.
#:
#: The section it lives under is emitted UNCONDITIONALLY (issue #692). It used to
#: be written only when a caller passed ``ci_fail_on``, which no preset and no
#: plain ``jury init`` ever does — so the hint the module defines never reached a
#: generated file, and `--preset thorough` (three or four vendors, and therefore
#: the config most likely to exit 3 on a one-CLI machine) shipped with nothing
#: about the guard in it at all.
_MIN_VENDORS_HINT = (
    "# Distinct vendors that must have contributed a review before the run can",
    "# stand as cross-vendor consensus (exit 3 otherwise). Defaults to 2 and only",
    "# applies when 2+ vendors are enabled HERE — including when one of their CLIs",
    "# is not installed; set 0 (or pass --no-min-vendors) to accept a panel that",
    "# collapsed to one vendor, or --strict to fail at startup on a missing CLI.",
    "# min_vendors = 2",
)


#: Written above a local seat `jury init` left out because its server listed no
#: model (issue #864). The block under it is the seat, commented, with the
#: template's model — the one the pull command below fetches.
_NO_LOCAL_MODEL_HINT = (
    "# The local server listed no model when `jury init` ran, so this seat is",
    "# left out rather than named after a model nobody has pulled. Pull one (for",
    f"# Ollama: `ollama pull {_LOCAL_TEMPLATE['model']}`) and uncomment the block below,",
    "# or rerun `jury init` with `--local-model <id>`.",
)


def render_toml(config: dict, *, commented_agents: list[dict] | tuple = ()) -> str:
    """Render a jury config dict to ``jury.toml`` text (minimal, typed).

    Handles exactly the value types this config uses (str/int/bool/list[str]).
    Empty/None values are omitted so a local agent (no ``command``/``extra_args``)
    stays clean. *commented_agents* are written after the panel as commented-out
    ``[[agent]]`` blocks under a hint — the local seats :func:`seat_local_agents`
    left out because the server listed no model.
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
    # `[jury.ci]` is always written, with the cross-vendor hint under it (#692).
    # `fail_on` still appears only when a caller chose one, so the file keeps
    # stating no redundant defaults; an otherwise empty section is comments only
    # and parses to `{}`, which is exactly what the run resolves today.
    ci = jury.get("ci") or {}
    lines.append("[jury.ci]")
    if "fail_on" in ci:
        lines.append(f"fail_on = {_render_value(ci['fail_on'])}")
    lines.extend(_MIN_VENDORS_HINT)
    lines.append("")

    for agent in config["agent"]:
        if adapter_key(agent.get("vendor", ""), agent.get("adapter")) in GENERIC_CLI_VENDORS:
            lines.append(f"# {UNSANDBOXED_LABEL}")
        lines.append("[[agent]]")
        lines.extend(_agent_keys(agent))
        # Only hint at `effort` where the vendor can actually act on it; a hint
        # under the `claude`/`codex` CLI blocks would invite a setting that only
        # ever produces an "effort unsupported" warning.
        if not agent.get("effort") and _effort_supported(agent.get("vendor", "")):
            lines.append(_EFFORT_HINT)
        # Only local seats send a temperature, and which adapter a seat runs
        # through decides that, not its vendor, the same rule validation uses.
        if agent.get("temperature") is None and (
            adapter_key(agent.get("vendor", ""), agent.get("adapter")) == "local"
        ):
            lines.append(_TEMPERATURE_HINT)
        lines.append("")

    # A seat left out is still written, commented, so uncommenting it is the whole
    # fix once its model exists (#864). Comments parse to nothing, so the config
    # and its hash are those of the seats above.
    for agent in commented_agents:
        lines.extend(_NO_LOCAL_MODEL_HINT)
        lines.append("# [[agent]]")
        lines.extend(f"# {line}" for line in _agent_keys(agent))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _agent_keys(agent: dict) -> list[str]:
    """The ``key = value`` lines of one ``[[agent]]`` table, in a stable order."""
    lines = []
    for key in _AGENT_KEY_ORDER:
        value = agent.get(key)
        if value in (None, "", []):
            continue
        lines.append(f"{key} = {_render_value(value)}")
    return lines
