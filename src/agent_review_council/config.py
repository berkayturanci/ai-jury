"""Configuration loading for the council.

Config is TOML (see ``council.toml``). The loader is tolerant: a missing config
file falls back to a sensible built-in default so the tool runs out of the box.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG: dict = {
    "council": {
        "rounds": 2,
        "chair": "claude",
        "timeout": 600,
        "parallel": True,
        "verify": True,
        "ci": {"fail_on": ["critical", "major"], "ignore_unverified": True},
        "context": {"mode": "diff-only", "redact_secrets": True},
    },
    "agent": [
        {
            "name": "claude",
            "vendor": "anthropic",
            "command": "claude",
            "extra_args": [
                "--output-format", "text",
                "--disallowed-tools", "Edit,Write,NotebookEdit,Bash",
                # Avoid `-p` blocking on a permission prompt in non-interactive mode.
                "--dangerously-skip-permissions",
            ],
        },
        {
            "name": "codex",
            "vendor": "openai",
            "command": "codex",
            # `codex exec` reads the prompt from stdin (see CodexAdapter). The
            # default sandbox can block `gh`/network used during PR review, so
            # `-s danger-full-access` keeps non-interactive runs from failing.
            # Override extra_args for stricter sandboxing.
            "extra_args": ["-s", "danger-full-access"],
        },
        {
            "name": "agy",
            "vendor": "google",
            "command": "agy",
            "extra_args": ["--dangerously-skip-permissions"],
        },
    ],
}


KNOWN_VENDORS = ("anthropic", "openai", "google")

KNOWN_TOP_LEVEL_KEYS = ("council", "agent")
KNOWN_COUNCIL_KEYS = (
    "rounds",
    "chair",
    "timeout",
    "parallel",
    "verify",
    "ci",
    "context",
    "seed",
    "anonymize_debate",
    "prefer_non_reviewer_chair",
)
KNOWN_AGENT_KEYS = (
    "name",
    "vendor",
    "command",
    "model",
    "timeout",
    "enabled",
    "extra_args",
)


class ConfigError(Exception):
    """Raised when a council configuration is invalid."""


def validate_config(data: dict, strict: bool = False) -> list:
    """Validate a raw config dict.

    Raises ``ConfigError`` with an actionable message on hard-invalid input
    (rounds < 1, timeout <= 0, duplicate agent names, empty/missing command,
    no agents at all). Returns a list of warning strings for soft issues
    (unknown vendor, chair not an enabled agent, unknown keys).

    When ``strict`` is True, soft issues raise ``ConfigError`` instead of
    being returned as warnings.
    """
    warnings: list = []
    errors: list = []

    if not isinstance(data, dict):
        raise ConfigError("config root must be a table/dict.")

    # Unknown top-level keys (soft).
    for key in data:
        if key not in KNOWN_TOP_LEVEL_KEYS:
            warnings.append(
                f"unknown top-level key '{key}' (expected one of "
                f"{', '.join(KNOWN_TOP_LEVEL_KEYS)})."
            )

    council = data.get("council", {})
    if not isinstance(council, dict):
        raise ConfigError("[council] must be a table.")

    for key in council:
        if key not in KNOWN_COUNCIL_KEYS:
            warnings.append(
                f"unknown key 'council.{key}' (expected one of "
                f"{', '.join(KNOWN_COUNCIL_KEYS)})."
            )

    # rounds >= 1 (hard).
    rounds = council.get("rounds", 1)
    if not isinstance(rounds, int) or isinstance(rounds, bool) or rounds < 1:
        errors.append(
            f"council.rounds must be an integer >= 1 (got {rounds!r})."
        )

    # timeout > 0 (hard).
    timeout = council.get("timeout", 600)
    if (
        not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or timeout <= 0
    ):
        errors.append(
            f"council.timeout must be a positive integer (got {timeout!r})."
        )

    agents_data = data.get("agent", [])
    if not isinstance(agents_data, list):
        raise ConfigError("[[agent]] must be an array of tables.")

    # At least one agent (hard).
    if not agents_data:
        errors.append(
            "no agents configured; define at least one [[agent]] entry."
        )

    seen_names: set = set()
    enabled_names: set = set()
    for idx, agent in enumerate(agents_data):
        if not isinstance(agent, dict):
            errors.append(f"agent[{idx}] must be a table.")
            continue

        for key in agent:
            if key not in KNOWN_AGENT_KEYS:
                warnings.append(
                    f"unknown key 'agent[{idx}].{key}' (expected one of "
                    f"{', '.join(KNOWN_AGENT_KEYS)})."
                )

        name = agent.get("name", "")
        label = name or f"agent[{idx}]"

        # Unique, non-empty name (hard for duplicates).
        if not name:
            errors.append(f"agent[{idx}] is missing a non-empty 'name'.")
        elif name in seen_names:
            errors.append(f"duplicate agent name '{name}'.")
        else:
            seen_names.add(name)

        # Non-empty command (hard).
        command = agent.get("command", "")
        if not command:
            errors.append(f"agent '{label}' is missing a non-empty 'command'.")

        # Per-agent timeout (hard if present and invalid).
        a_timeout = agent.get("timeout", 600)
        if (
            not isinstance(a_timeout, int)
            or isinstance(a_timeout, bool)
            or a_timeout <= 0
        ):
            errors.append(
                f"agent '{label}' timeout must be a positive integer "
                f"(got {a_timeout!r})."
            )

        # Known vendor (soft).
        vendor = agent.get("vendor", "")
        if vendor not in KNOWN_VENDORS:
            warnings.append(
                f"agent '{label}' has unknown vendor '{vendor}' (expected one "
                f"of {', '.join(KNOWN_VENDORS)}); using generic fallback."
            )

        if name and agent.get("enabled", True):
            enabled_names.add(name)

    # Chair must reference an enabled agent (soft). The literal "rotate" is a
    # valid special value (deterministic per-run rotation) and never warns.
    chair = council.get("chair", "claude")
    if enabled_names and chair != "rotate" and chair not in enabled_names:
        warnings.append(
            f"council.chair '{chair}' is not an enabled agent (enabled: "
            f"{', '.join(sorted(enabled_names)) or 'none'}); the first "
            "enabled agent will be used as fallback."
        )

    if errors:
        raise ConfigError(
            "invalid configuration:\n  - " + "\n  - ".join(errors)
        )

    if strict and warnings:
        raise ConfigError(
            "configuration warnings treated as errors (strict mode):\n  - "
            + "\n  - ".join(warnings)
        )

    return warnings


@dataclass
class AgentSpec:
    name: str
    vendor: str
    command: str
    model: str | None = None
    timeout: int = 600
    enabled: bool = True
    extra_args: list[str] = field(default_factory=list)


@dataclass
class CiConfig:
    fail_on: list[str] = field(default_factory=lambda: ["critical", "major"])
    ignore_unverified: bool = True


@dataclass
class ContextConfig:
    mode: str = "diff-only"  # "diff-only" or "expanded"
    redact_secrets: bool = True


@dataclass
class CouncilConfig:
    rounds: int = 2
    chair: str = "claude"
    timeout: int = 600
    parallel: bool = True
    verify: bool = True
    agents: list[AgentSpec] = field(default_factory=list)
    ci: CiConfig = field(default_factory=CiConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    # Optional run seed. Controls the shared run RNG used by randomized
    # orchestration features (see orchestrator.run_council). LLM output itself
    # is never made deterministic by this; only the orchestration around it.
    seed: int | None = None
    # Anonymize peer reviews shown in the round-2 debate (Chatham House rule,
    # issue #37): strip vendor/agent identity, relabel as "Reviewer A/B/...",
    # and randomize per-debater presentation order via the shared run RNG so
    # neither identity nor position is a stable signal. The rendered report
    # still attributes findings by real name. Set False for the old
    # identity-labeled debate path.
    anonymize_debate: bool = True
    # Prefer a chair that was NOT a round-1 reviewer when a usable non-reviewer
    # is available (issue #38), mitigating chair self-preference bias. Has no
    # effect when chair == "rotate" (rotation already picks among usable agents)
    # or when an explicit usable chair name is configured.
    prefer_non_reviewer_chair: bool = False

    @property
    def enabled_agents(self) -> list[AgentSpec]:
        return [a for a in self.agents if a.enabled]


def _ci_from_dict(data: dict) -> CiConfig:
    fail_on = data.get("fail_on", ["critical", "major"])
    if not isinstance(fail_on, list):
        fail_on = [fail_on]
    fail_on = [str(s).strip().lower() for s in fail_on if str(s).strip()]
    return CiConfig(
        fail_on=fail_on,
        ignore_unverified=bool(data.get("ignore_unverified", True)),
    )


def _context_from_dict(data: dict) -> ContextConfig:
    mode = str(data.get("mode", "diff-only")).strip().lower()
    if mode not in ("diff-only", "expanded"):
        mode = "diff-only"
    return ContextConfig(mode=mode, redact_secrets=bool(data.get("redact_secrets", True)))


def _seed_from_dict(council: dict) -> int | None:
    """Parse ``[council] seed`` into an int, or None when absent/invalid.

    A non-integer or boolean seed is treated as "no seed" rather than an error:
    the seed only governs orchestration randomness, so a malformed value should
    degrade gracefully to the unseeded (still deterministic-orchestration) path.
    """
    raw = council.get("seed")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _from_dict(data: dict) -> CouncilConfig:
    council = data.get("council", {})
    default_timeout = int(council.get("timeout", 600))
    agents: list[AgentSpec] = []
    for raw in data.get("agent", []):
        agents.append(
            AgentSpec(
                name=raw["name"],
                vendor=raw.get("vendor", "unknown"),
                command=raw["command"],
                model=raw.get("model"),
                timeout=int(raw.get("timeout", default_timeout)),
                enabled=bool(raw.get("enabled", True)),
                extra_args=list(raw.get("extra_args", [])),
            )
        )
    return CouncilConfig(
        rounds=int(council.get("rounds", 2)),
        chair=council.get("chair", agents[0].name if agents else "claude"),
        timeout=default_timeout,
        parallel=bool(council.get("parallel", True)),
        verify=bool(council.get("verify", True)),
        agents=agents,
        ci=_ci_from_dict(council.get("ci", {})),
        context=_context_from_dict(council.get("context", {})),
        seed=_seed_from_dict(council),
        anonymize_debate=bool(council.get("anonymize_debate", True)),
        prefer_non_reviewer_chair=bool(council.get("prefer_non_reviewer_chair", False)),
    )


def config_hash(config: CouncilConfig) -> str:
    """Return a stable SHA-256 hash of the EFFECTIVE council configuration.

    The hash is a function of the resolved configuration only (no timestamps,
    no diff text), so the same config always produces the same digest and a
    changed config produces a different one. This anchors reproducibility
    metadata: two runs with an identical config hash were orchestrated under
    identical settings.

    The seed is intentionally excluded so the hash describes the *configuration*
    independent of which run seed was chosen; the seed is recorded separately in
    run metadata.
    """
    import hashlib
    import json

    canonical = {
        "rounds": config.rounds,
        "chair": config.chair,
        "timeout": config.timeout,
        "parallel": config.parallel,
        "verify": config.verify,
        "ci": {
            "fail_on": list(config.ci.fail_on),
            "ignore_unverified": config.ci.ignore_unverified,
        },
        "context": {
            "mode": config.context.mode,
            "redact_secrets": config.context.redact_secrets,
        },
        "agents": [
            {
                "name": a.name,
                "vendor": a.vendor,
                "command": a.command,
                "model": a.model,
                "timeout": a.timeout,
                "enabled": a.enabled,
                "extra_args": list(a.extra_args),
            }
            for a in config.agents
        ],
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_raw_config(path: str | Path | None = None) -> dict:
    """Return the raw config dict for *path*, or the built-in default.

    If *path* is None, look for ``council.toml`` in the current directory and
    fall back to :data:`DEFAULT_CONFIG` when it is absent. An explicit *path*
    that does not exist raises ``FileNotFoundError``.
    """
    if path is None:
        candidate = Path("council.toml")
        if not candidate.exists():
            return DEFAULT_CONFIG
        path = candidate
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_config(
    path: str | Path | None = None,
    validate: bool = False,
    strict: bool = False,
) -> CouncilConfig:
    """Load council config from *path*, or fall back to the built-in default.

    If *path* is None, look for ``council.toml`` in the current directory.

    When *validate* is True, the resolved config dict is checked with
    :func:`validate_config` before being materialized; a ``ConfigError`` is
    raised on hard-invalid input (and on warnings when *strict* is True).
    Validation is opt-in so existing callers stay unaffected.
    """
    data = load_raw_config(path)
    if validate:
        validate_config(data, strict=strict)
    return _from_dict(data)
