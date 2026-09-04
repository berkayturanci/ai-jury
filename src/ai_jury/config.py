"""Configuration loading for the jury.

Config is TOML (see ``jury.toml``). The loader is tolerant: a missing config
file falls back to a sensible built-in default so the tool runs out of the box.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .redaction import ENV_VAR_NAME_RULE, redact, safe_env_var_name

# Hosts that are safe to reach over plaintext http and never an SSRF target.
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]")

# Upper bound on a config/policy TOML file (issue #316/L-5). A real config is a
# few KB; refuse a multi-MB / pathological file so `tomllib` can't be driven to
# exhaust memory (the file may be attacker-supplied when jury runs from a PR
# checkout). Mirrors the cache's _MAX_CACHE_BYTES.
_MAX_CONFIG_BYTES = 4 * 1024 * 1024


def _read_toml_bounded(path: Path) -> dict:
    """Parse a TOML file with a size cap (issue #316/L-5)."""
    with path.open("rb") as fh:
        raw = fh.read(_MAX_CONFIG_BYTES + 1)
    if len(raw) > _MAX_CONFIG_BYTES:
        raise ConfigError(f"config file '{path}' exceeds the {_MAX_CONFIG_BYTES}-byte limit.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # TOML is UTF-8 by spec; surface a clean error instead of a raw
        # UnicodeDecodeError (review of #316 — the prior tomllib.load crashed the
        # same way on bad bytes; now it's a ConfigError).
        raise ConfigError(f"config file '{path}' is not valid UTF-8.") from None
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in config file '{path}': {redact(str(exc))[0]}") from None


def _is_relative_path_command(command: str) -> bool:
    """True for a relative command that contains a path separator (#293/F-6).

    A bare name (``codex``) is fine — it is resolved on PATH. An absolute path
    (``/usr/bin/codex``) is fine — it is explicit. A relative path with a
    separator (``./tools/codex``, ``bin/agy``) is rejected because it resolves a
    binary from an attacker-influenceable working-directory-relative location.
    """
    cmd_str = str(command or "")
    has_sep = "/" in cmd_str or "\\" in cmd_str or (os.altsep is not None and os.altsep in cmd_str)
    return has_sep and not Path(cmd_str).is_absolute()


# Env opt-in for a non-loopback local endpoint. It lives in the environment, NOT
# in jury.toml, on purpose (review of #291): the threat model is an
# attacker-controlled config, so the opt-in must sit OUTSIDE the surface the
# attacker controls. Without it, a non-loopback host (incl. cloud-metadata
# 169.254.169.254) is a hard error so an attacker config cannot drive an
# SSRF POST to an internal address — matching the default-secure F-1 posture.
_ALLOW_REMOTE_ENDPOINT_ENV = "JURY_ALLOW_REMOTE_ENDPOINT"

# Opt-in strict mode (issue #296): when set, every agent ``command`` must be an
# absolute path — rejecting even a bare name, whose PATH resolution an attacker
# who controls the CI runner's PATH could hijack with a shim. Off by default so
# the convenient bare-name (``claude``) keeps working for local use.
_REQUIRE_ABSOLUTE_COMMAND_ENV = "JURY_REQUIRE_ABSOLUTE_COMMAND"


def _endpoint_issues(endpoint: str, label: str) -> tuple[list[str], list[str]]:
    """Validate a local-agent ``endpoint`` URL (issue #291, SSRF defense).

    Returns ``(errors, warnings)``. A non-``http``/``https`` scheme is a hard
    error (blocks ``file://``/``ftp://`` and other SSRF primitives). A non-loopback
    host is also a hard error UNLESS the operator opts in via the
    ``JURY_ALLOW_REMOTE_ENDPOINT`` environment variable (a remote model server is
    a legitimate but riskier choice the attacker-controlled config must not be
    able to select on its own); when opted in it degrades to a warning, plus a
    cleartext warning for plaintext ``http``.
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(endpoint, str):
        errors.append(f"agent '{label}' endpoint must be a string (got {endpoint!r}).")
        return errors, warnings
    # `urlsplit` raises ValueError on a malformed URL (e.g. `http://[::1`,
    # "Invalid IPv6 URL"). Convert that to a hard config error (issue #315) so
    # `validate_config` reports it cleanly instead of crashing with a stack trace
    # — the malformed string is, by definition, not a usable endpoint.
    try:
        parsed = urlsplit(endpoint)
        parsed.hostname  # noqa: B018 - also raises ValueError on a bad IPv6 host
    except (ValueError, TypeError, AttributeError):
        errors.append(f"agent '{label}' endpoint '{endpoint}' is not a valid URL.")
        return errors, warnings
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        errors.append(
            f"agent '{label}' endpoint scheme '{parsed.scheme or '(none)'}' is "
            f"not allowed; use http or https."
        )
        return errors, warnings
    host = (parsed.hostname or "").lower()
    if host in _LOOPBACK_HOSTS:
        return errors, warnings
    if not os.environ.get(_ALLOW_REMOTE_ENDPOINT_ENV):
        errors.append(
            f"agent '{label}' endpoint host '{host or '(none)'}' is not loopback; "
            f"a non-loopback model server (incl. internal/metadata addresses) is "
            f"refused by default. Set {_ALLOW_REMOTE_ENDPOINT_ENV}=1 in the "
            f"environment to allow a trusted remote endpoint."
        )
        return errors, warnings
    warnings.append(
        f"agent '{label}' endpoint host '{host or '(none)'}' is not loopback; "
        f"the (redacted) diff is sent to a remote server — ensure it is trusted "
        f"and not an internal/metadata address."
    )
    if scheme == "http":
        warnings.append(
            f"agent '{label}' endpoint uses plaintext http to a non-loopback "
            f"host; prefer https so the prompt is not sent in cleartext."
        )
    return errors, warnings


#: Distinct vendors a run must have heard from before it may call itself a
#: cross-vendor consensus (issue #682). The shipped default of 2 FAILS CLOSED:
#: before it, a three-vendor panel that collapsed to one exited 0 with a verdict
#: and nothing said so (#635). Lower it, or set 0, to opt out — see
#: ``[jury.ci] min_vendors`` and ``--no-min-vendors``.
DEFAULT_MIN_VENDORS = 2


def _non_negative_int(value, default: int) -> int:
    """A non-negative int from raw config data, falling back to ``default``.

    A malformed value falls back to the default rather than raising: the default
    is the *safe* direction for a fail-closed gate, and a typo in a CI knob must
    not stop a review from running.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, number)


DEFAULT_CONFIG: dict = {
    "jury": {
        "rounds": 2,
        "chair": "claude",
        "timeout": 600,
        "parallel": True,
        "verify": True,
        "ci": {
            "fail_on": ["critical", "major"],
            "ignore_unverified": True,
            "min_vendors": DEFAULT_MIN_VENDORS,
        },
        "context": {"mode": "diff-only", "redact_secrets": True},
    },
    # Execution controls (issue #30) are optional and conservative by default:
    # no overall/per-phase budget and zero retries, so out-of-the-box behaviour
    # is unchanged. They live under [jury] and are documented in
    # docs/configuration.md.
    "agent": [
        {
            "name": "claude",
            "vendor": "anthropic",
            "command": "claude",
            "extra_args": [
                "--output-format",
                "text",
                "--disallowed-tools",
                "Edit,Write,NotebookEdit,Bash",
                # Avoid `-p` blocking on a permission prompt in non-interactive mode.
                "--dangerously-skip-permissions",
            ],
        },
        {
            "name": "codex",
            "vendor": "openai",
            "command": "codex",
            # `codex exec` reads the prompt from stdin (see CodexAdapter) and only
            # needs to READ it and print a review — the diff is fetched by the
            # jury process (`gh`), not the agent. So the secure default is a
            # read-only sandbox (issue #100); widen it (e.g. `-s workspace-write`
            # or `danger-full-access`) only if your workflow truly needs it.
            "extra_args": ["-s", "read-only"],
        },
        {
            "name": "agy",
            "vendor": "google",
            "command": "agy",
            # `--dangerously-skip-permissions` avoids a non-interactive permission
            # prompt hanging the run; `--sandbox` keeps the agent's tools
            # restricted while it reviews untrusted content (issue #100).
            "extra_args": ["--dangerously-skip-permissions", "--sandbox"],
        },
    ],
}


# Vendors that talk HTTP directly (no CLI subprocess), so they need no
# `command`: `local` (a user-supplied OpenAI-compatible server, issue #43) and
# the hosted-API adapters (a real vendor API keyed by an env-var API key,
# issue #430/#432).
_NO_COMMAND_VENDORS = ("local", "anthropic-api", "openai-api", "google-api", "openai-compatible")

KNOWN_VENDORS = (
    "anthropic",
    "openai",
    "google",
    "local",
    "anthropic-api",
    "openai-api",
    "google-api",
    "openai-compatible",
    "cli",
)

KNOWN_TOP_LEVEL_KEYS = ("jury", "agent")
KNOWN_JURY_KEYS = (
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
    # Demote a local-only finding to non-blocking severity (issue #442).
    "demote_local_only",
    # Execution controls (issue #30).
    "total_timeout",
    "phase_timeout",
    "retries",
    # Adaptive rounds (issue #40).
    "max_rounds",
    "early_stop",
    # Risk-aware auto-depth (issue #120).
    "auto_depth",
    # Full-transcript / verbose rendering (rendering-only; not in config_hash).
    "transcript",
    # Final-verdict mode: "chair" synthesis or panel "vote" (rendering-only).
    "decision",
    # Animated theater view defaults (rendering-only; issue #364).
    "theater",
    "theater_style",
    # Large-diff handling (issue #31).
    "diff",
)
KNOWN_AGENT_KEYS = (
    "name",
    "vendor",
    "command",
    "model",
    "timeout",
    "enabled",
    "extra_args",
    # OpenAI-compatible local/open-weight endpoint (issue #43).
    "endpoint",
    # Universal agent extensions
    "api_key_env",
    "prompt_mode",
    "headers",
    # Reasoning effort (issue #662): mapped per vendor in adapters.effort_args.
    "effort",
)

#: Accepted values for ``[[agent]] effort`` / ``--effort`` (issue #662).
#: Duplicated as a literal rather than imported from ``adapters`` so config
#: validation stays free of any adapter import (``adapters`` imports ``config``).
#: ``tests/test_adapters.py`` pins the two lists together.
KNOWN_EFFORTS = ("low", "medium", "high")


class ConfigError(Exception):
    """Raised when a jury configuration is invalid."""


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

    jury = data.get("jury", {})
    if not isinstance(jury, dict):
        raise ConfigError("[jury] must be a table.")

    for key in jury:
        if key not in KNOWN_JURY_KEYS:
            warnings.append(
                f"unknown key 'jury.{key}' (expected one of {', '.join(KNOWN_JURY_KEYS)})."
            )

    # rounds >= 1 (hard).
    rounds = jury.get("rounds", 1)
    if not isinstance(rounds, int) or isinstance(rounds, bool) or rounds < 1:
        errors.append(f"jury.rounds must be an integer >= 1 (got {rounds!r}).")

    # timeout > 0 (hard).
    timeout = jury.get("timeout", 600)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        errors.append(f"jury.timeout must be a positive integer (got {timeout!r}).")

    # Execution controls (issue #30): optional positive budgets, non-negative
    # retries (hard when present and invalid).
    for key in ("total_timeout", "phase_timeout"):
        val = jury.get(key)
        if val is not None and (not isinstance(val, int) or isinstance(val, bool) or val <= 0):
            errors.append(f"jury.{key} must be a positive integer when set (got {val!r}).")
    retries = jury.get("retries", 0)
    if not isinstance(retries, int) or isinstance(retries, bool) or retries < 0:
        errors.append(f"jury.retries must be an integer >= 0 (got {retries!r}).")

    # Final-verdict mode (issue #220): "chair" or "vote".
    decision = jury.get("decision")
    if decision is not None and str(decision).strip().lower() not in ("chair", "vote"):
        errors.append(f"jury.decision must be 'chair' or 'vote' (got {decision!r}).")

    # Animated theater defaults (issue #364): theater is a bool, style is enum.
    theater = jury.get("theater")
    if theater is not None and not isinstance(theater, bool):
        errors.append(f"jury.theater must be true or false (got {theater!r}).")
    style = jury.get("theater_style")
    if style is not None and str(style).strip().lower() not in ("flat", "pixel"):
        errors.append(f"jury.theater_style must be 'flat' or 'pixel' (got {style!r}).")

    # Adaptive rounds (issue #40): max_rounds >= 1 (hard); early_stop is a bool.
    max_rounds = jury.get("max_rounds")
    if max_rounds is not None and (
        not isinstance(max_rounds, int) or isinstance(max_rounds, bool) or max_rounds < 1
    ):
        errors.append(f"jury.max_rounds must be an integer >= 1 when set (got {max_rounds!r}).")

    # Large-diff handling (issue #31): [jury.diff] sizes are positive ints.
    diff_cfg = jury.get("diff", {})
    if not isinstance(diff_cfg, dict):
        errors.append("[jury.diff] must be a table.")
    else:
        for key in ("max_bytes", "chunk_max_bytes"):
            val = diff_cfg.get(key)
            if val is not None and (not isinstance(val, int) or isinstance(val, bool) or val <= 0):
                errors.append(f"jury.diff.{key} must be a positive integer when set (got {val!r}).")

    agents_data = data.get("agent", [])
    if not isinstance(agents_data, list):
        raise ConfigError("[[agent]] must be an array of tables.")

    # At least one agent (hard).
    if not agents_data:
        errors.append("no agents configured; define at least one [[agent]] entry.")

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

        # A local OpenAI-compatible agent (issue #43) talks to an HTTP
        # ``endpoint`` (default ``http://localhost:11434/v1``) instead of a CLI,
        # so it does not require a ``command``; it does need a ``model``. A
        # hosted-API agent (issue #430) likewise talks HTTP instead of a CLI —
        # to the vendor's fixed, non-configurable endpoint — so it also needs
        # no ``command``, but does need a ``model``; it has no ``endpoint`` to
        # validate since the URL isn't a config value. Every other vendor
        # requires a non-empty ``command``.
        command = agent.get("command", "")
        vendor_value = agent.get("vendor", "")
        has_endpoint = bool(agent.get("endpoint"))
        is_local_or_http = (
            vendor_value in _NO_COMMAND_VENDORS or vendor_value.endswith("-api") or has_endpoint
        )
        if is_local_or_http:
            if not agent.get("model"):
                warnings.append(
                    f"agent '{label}' (vendor '{vendor_value}') has no 'model'; the "
                    f"server or API call will likely reject the request."
                )
            endpoint = agent.get("endpoint")
            if endpoint:
                e_errors, e_warnings = _endpoint_issues(endpoint, label)
                errors.extend(e_errors)
                warnings.extend(e_warnings)
        elif not command:
            errors.append(f"agent '{label}' is missing a non-empty 'command'.")
        elif _is_relative_path_command(command):
            # A relative path with separators (e.g. ./tools/codex, bin/agy) could
            # resolve a binary from an attacker-influenced location (#293/F-6).
            # Require a bare name (resolved on PATH) or an absolute path.
            errors.append(
                f"agent '{label}' command '{command}' is a relative path; use a "
                f"bare name (resolved on PATH) or an absolute path."
            )
        elif os.environ.get(_REQUIRE_ABSOLUTE_COMMAND_ENV) and not Path(command).is_absolute():
            # Strict opt-in (issue #296): in a hardened/CI context, refuse even a
            # bare name so a poisoned PATH can't resolve a shim — require an
            # absolute path for every agent command.
            errors.append(
                f"agent '{label}' command '{command}' is not an absolute path; "
                f"{_REQUIRE_ABSOLUTE_COMMAND_ENV} requires every agent command to "
                f"be an absolute path."
            )

        # Per-agent timeout (hard if present and invalid).
        a_timeout = agent.get("timeout", 600)
        if not isinstance(a_timeout, int) or isinstance(a_timeout, bool) or a_timeout <= 0:
            errors.append(
                f"agent '{label}' timeout must be a positive integer (got {a_timeout!r})."
            )

        # `api_key_env` names an environment variable and is echoed into
        # diagnostics, so it is bounded to a real env var name (see
        # redaction.safe_env_var_name). Warn rather than fail: the vendor
        # default still works, but the operator should not discover the
        # silent fallback by wondering why their variable is ignored.
        #
        # The rejected value is deliberately NOT quoted back. Reaching this
        # branch means it contains characters outside the safe set — which is
        # exactly the class (control characters, ANSI escapes, quotes) that must
        # not be written to a terminal or spliced into a report. Naming the
        # agent and stating the rule locates the problem without reproducing it.
        env_var = agent.get("api_key_env")
        if env_var is not None and safe_env_var_name(env_var, "") != env_var:
            warnings.append(
                f"agent '{label}' api_key_env is not a valid environment variable "
                f"name (expected {ENV_VAR_NAME_RULE}); the vendor default will be used."
            )

        # Reasoning effort (hard when present and not a known level, issue
        # #662): a typo like `effort = "max"` would otherwise be silently
        # dropped, and the operator would pay for a run they think is deeper
        # than it is.
        effort = agent.get("effort")
        if effort is not None and (
            not isinstance(effort, str) or effort.strip().lower() not in KNOWN_EFFORTS
        ):
            errors.append(
                f"agent '{label}' effort must be one of "
                f"{', '.join(KNOWN_EFFORTS)} (got {effort!r})."
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
    chair = jury.get("chair", "claude")
    if enabled_names and chair != "rotate" and chair not in enabled_names:
        warnings.append(
            f"jury.chair '{chair}' is not an enabled agent (enabled: "
            f"{', '.join(sorted(enabled_names)) or 'none'}); the first "
            "enabled agent will be used as fallback."
        )

    if errors:
        raise ConfigError("invalid configuration:\n  - " + "\n  - ".join(errors))

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
    command: str = ""
    model: str | None = None
    timeout: int = 600
    enabled: bool = True
    extra_args: list[str] = field(default_factory=list)
    # OpenAI-compatible base URL for a local/open-weight or hosted API agent (issue #43).
    endpoint: str | None = None
    # Universal agent extensions
    api_key_env: str | None = None
    prompt_mode: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    # Reasoning effort: "low" | "medium" | "high" (issue #662). None leaves the
    # vendor default alone. Mapped per vendor by ``adapters.effort_args``.
    effort: str | None = None


@dataclass
class CiConfig:
    fail_on: list[str] = field(default_factory=lambda: ["critical", "major"])
    ignore_unverified: bool = True
    # Distinct vendors that must have CONTRIBUTED a review before the run is
    # allowed to stand as cross-vendor consensus (issue #682). Fails closed at
    # 2: the product claim is a cross-vendor jury, and a panel that collapsed to
    # one vendor is a different thing wearing the same output. Only applies when
    # the run claimed consensus in the first place — a panel with fewer distinct
    # vendors enabled than this is never failed by the default (see
    # ``metadata.collapse_reason``). ``0`` disables the guard entirely.
    min_vendors: int = DEFAULT_MIN_VENDORS
    # Review RECORDS the run must be able to hand a downstream consumer before it
    # is worth running at all (issue #699) — panel ballots plus the chair record,
    # which is the number a gate like `keel review` counts. ``0`` (the default)
    # disables it: most consumers have no minimum, and a gate that fails closed
    # here would break every single-agent install. Set it to what your consumer
    # requires and the shortfall is named before the panel runs, instead of after
    # the review has already been paid for.
    #
    # Deliberately NOT in ``config_hash`` (unlike ``min_vendors``): it changes
    # neither the orchestration nor what the panel finds, only whether the
    # resulting bundle is accepted, and it is re-evaluated on every run including
    # a cache hit — so a cached outcome cannot smuggle a shortfall past it, and
    # adding it would invalidate every existing cache entry for nothing.
    min_reviews: int = 0


@dataclass
class ContextConfig:
    mode: str = "diff-only"  # "diff-only" or "expanded"
    redact_secrets: bool = True


@dataclass
class DiffConfig:
    """Large-diff handling policy (issue #31).

    ``max_bytes`` is the size (UTF-8 bytes, measured after filtering) above which
    a diff is either chunked or rejected. ``chunk`` enables per-file chunking;
    ``chunk_max_bytes`` bounds each chunk (defaults to ``max_bytes``).
    ``exclude_generated`` drops binary and common generated/vendored files;
    ``exclude``/``include`` are extra path-glob deny/allow lists.
    """

    max_bytes: int = 200_000
    chunk: bool = False
    chunk_max_bytes: int | None = None
    exclude_generated: bool = True
    exclude: list[str] = field(default_factory=list)
    include: list[str] = field(default_factory=list)


@dataclass
class JuryConfig:
    rounds: int = 2
    chair: str = "claude"
    timeout: int = 600
    parallel: bool = True
    verify: bool = True
    agents: list[AgentSpec] = field(default_factory=list)
    ci: CiConfig = field(default_factory=CiConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    diff: DiffConfig = field(default_factory=DiffConfig)
    # Optional run seed. Controls the shared run RNG used by randomized
    # orchestration features (see orchestrator.run_jury). LLM output itself
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
    # Demote a finding to non-blocking severity when every reviewer who raised it
    # is vendor "local" and no cloud reviewer corroborates it (issue #442).
    # Rejected alternative: a numeric per-reviewer trust weight — this categorical
    # rule is auditable in one line where a coefficient invites silent drift.
    # Off by default so the out-of-the-box CI gate is unchanged.
    demote_local_only: bool = False
    # Execution controls (issue #30). All optional and off by default so the
    # out-of-the-box run is unchanged. ``total_timeout``/``phase_timeout`` cap the
    # whole run / a single phase (None = uncapped); the effective per-agent-call
    # timeout is the minimum of the agent timeout, the phase budget, and the
    # remaining total budget. ``retries`` is the number of EXTRA attempts for
    # transient (retryable) failures — 0 means try once.
    total_timeout: int | None = None
    phase_timeout: int | None = None
    retries: int = 0
    # Adaptive rounds (issue #40). When ``early_stop`` is True the orchestrator
    # decides whether to run the debate round(s) from the round-1 convergence
    # signal instead of always honouring a fixed ``rounds``: a unanimous panel
    # stops after round 1, and disagreement runs debate up to ``max_rounds``.
    # A CLI ``--rounds`` (or any explicit fixed-N intent) disables early stop so
    # benchmarking stays reproducible. ``max_rounds`` defaults to ``rounds``.
    max_rounds: int | None = None
    early_stop: bool = False
    # Risk-aware auto-depth (issue #120): when True, the CLI sets rounds/verify/
    # early_stop from a cheap pre-review diff profile (size/paths/security), so a
    # trivial diff runs shallow and a risky one runs full. Off by default; the
    # panel is never trimmed; explicit --rounds/--verify/--early-stop override it.
    auto_depth: bool = False
    # Full-transcript output (issue: full transcript). When True, the markdown
    # report defaults to the chronological play-by-play (each agent's raw review,
    # the debate, and the chair's reasoning) instead of the consensus-first
    # summary. Rendering-only: it does NOT affect orchestration, so it is
    # deliberately excluded from ``config_hash`` and the cache key. The CLI
    # ``--transcript``/``--no-transcript`` override it; ``--verbose`` is summary +
    # transcript in one document.
    transcript: bool = False
    # Final-verdict mode (issue #220): "chair" = the chair's synthesis is the
    # verdict (default, historical); "vote" = the panel verdict is a tally of the
    # reviewers (each votes from the worst finding they raised). Rendering-only —
    # it does not change orchestration, so it is excluded from ``config_hash`` and
    # the cache key. The chair still runs (its reasoning is shown as supporting
    # narrative), and the severity-based CI gate is unaffected. CLI: ``--decision``.
    decision: str = "chair"
    # Animated theater view defaults (issue #364). Rendering-only side channel —
    # excluded from ``config_hash`` and the cache key (it never touches the
    # outcome). ``theater`` defaults the scene on; ``theater_style`` is "flat"
    # (ANSI line scene) or "pixel" (pixel-art room). The CLI ``--theater`` /
    # ``--theater-style`` flags override these per run. Theater is TTY-only, so
    # even when defaulted on it falls back to ``--live`` off an interactive
    # terminal (and ``pixel`` falls back to ``flat`` without truecolor/unicode).
    theater: bool = False
    theater_style: str = "flat"
    # Risk-aware tiered model routing (issue #524): "standard" (uniform panel) | "tiered" (cost-optimized with frontier anchor)
    routing: str = "standard"
    # Pre-pass static analysis hints (issue #523): inject linter hints into prompt context
    hints: bool = False

    @property
    def effective_max_rounds(self) -> int:
        """Round ceiling for adaptive mode: ``max_rounds`` or ``rounds``."""
        return self.max_rounds if self.max_rounds is not None else self.rounds

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
        min_vendors=_non_negative_int(data.get("min_vendors"), DEFAULT_MIN_VENDORS),
        min_reviews=_non_negative_int(data.get("min_reviews"), 0),
    )


def _context_from_dict(data: dict) -> ContextConfig:
    mode = str(data.get("mode", "diff-only")).strip().lower()
    if mode not in ("diff-only", "expanded"):
        mode = "diff-only"
    return ContextConfig(mode=mode, redact_secrets=bool(data.get("redact_secrets", True)))


def _str_list(value) -> list[str]:
    """Coerce a config value into a clean list of non-empty strings."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _diff_from_dict(data: dict) -> DiffConfig:
    default = DiffConfig()
    return DiffConfig(
        max_bytes=_opt_positive_int(data.get("max_bytes")) or default.max_bytes,
        chunk=bool(data.get("chunk", default.chunk)),
        chunk_max_bytes=_opt_positive_int(data.get("chunk_max_bytes")),
        exclude_generated=bool(data.get("exclude_generated", default.exclude_generated)),
        exclude=_str_list(data.get("exclude", [])),
        include=_str_list(data.get("include", [])),
    )


def _seed_from_dict(jury: dict) -> int | None:
    """Parse ``[jury] seed`` into an int, or None when absent/invalid.

    A non-integer or boolean seed is treated as "no seed" rather than an error:
    the seed only governs orchestration randomness, so a malformed value should
    degrade gracefully to the unseeded (still deterministic-orchestration) path.
    """
    raw = jury.get("seed")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _opt_positive_int(raw) -> int | None:
    """Coerce an optional positive-int config value, else None.

    Used for the optional execution budgets (issue #30) and ``max_rounds``
    (issue #40). A missing, boolean, non-numeric, or non-positive value degrades
    to None (uncapped) rather than raising, so ``_from_dict`` stays tolerant when
    called without validation; :func:`validate_config` is what reports the hard
    error for an explicit bad value.
    """
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _from_dict(data: dict) -> JuryConfig:
    jury = data.get("jury", {})
    default_timeout = int(jury.get("timeout", 600))
    agents: list[AgentSpec] = []
    for raw in data.get("agent", []):
        raw_headers = raw.get("headers", {})
        headers_dict = (
            {str(k): str(v) for k, v in raw_headers.items()}
            if isinstance(raw_headers, dict)
            else {}
        )
        api_key_env_val = str(raw["api_key_env"]) if raw.get("api_key_env") else None
        prompt_mode_val = str(raw["prompt_mode"]) if raw.get("prompt_mode") else None
        raw_effort = raw.get("effort")
        effort_val = str(raw_effort).strip().lower() if isinstance(raw_effort, str) else None
        agents.append(
            AgentSpec(
                name=raw["name"],
                vendor=raw.get("vendor", "unknown"),
                # ``command`` is optional for local/HTTP agents (issue #43).
                command=raw.get("command", ""),
                model=raw.get("model"),
                timeout=int(raw.get("timeout", default_timeout)),
                enabled=bool(raw.get("enabled", True)),
                extra_args=list(raw.get("extra_args", [])),
                endpoint=raw.get("endpoint"),
                api_key_env=api_key_env_val,
                prompt_mode=prompt_mode_val,
                headers=headers_dict,
                effort=effort_val or None,
            )
        )
    return JuryConfig(
        rounds=int(jury.get("rounds", 2)),
        chair=jury.get("chair", agents[0].name if agents else "claude"),
        timeout=default_timeout,
        parallel=bool(jury.get("parallel", True)),
        verify=bool(jury.get("verify", True)),
        agents=agents,
        ci=_ci_from_dict(jury.get("ci", {})),
        context=_context_from_dict(jury.get("context", {})),
        diff=_diff_from_dict(jury.get("diff", {})),
        seed=_seed_from_dict(jury),
        anonymize_debate=bool(jury.get("anonymize_debate", True)),
        prefer_non_reviewer_chair=bool(jury.get("prefer_non_reviewer_chair", False)),
        demote_local_only=bool(jury.get("demote_local_only", False)),
        total_timeout=_opt_positive_int(jury.get("total_timeout")),
        phase_timeout=_opt_positive_int(jury.get("phase_timeout")),
        retries=max(0, int(jury.get("retries", 0) or 0)),
        max_rounds=_opt_positive_int(jury.get("max_rounds")),
        early_stop=bool(jury.get("early_stop", False)),
        auto_depth=bool(jury.get("auto_depth", False)),
        transcript=bool(jury.get("transcript", False)),
        decision=(str(jury.get("decision", "chair")).strip().lower() or "chair"),
        theater=bool(jury.get("theater", False)),
        theater_style=(str(jury.get("theater_style", "flat")).strip().lower() or "flat"),
        routing=(str(jury.get("routing", "standard")).strip().lower() or "standard"),
        hints=bool(jury.get("hints", False)),
    )


def config_hash(config: JuryConfig) -> str:
    """Return a stable SHA-256 hash of the EFFECTIVE jury configuration.

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
        "total_timeout": config.total_timeout,
        "phase_timeout": config.phase_timeout,
        "retries": config.retries,
        "max_rounds": config.max_rounds,
        "early_stop": config.early_stop,
        "auto_depth": config.auto_depth,
        # Orchestration-affecting toggles (issue #122): both change how a run is
        # conducted, so the "same hash ⇒ same orchestration" promise must include
        # them.
        "anonymize_debate": config.anonymize_debate,
        "prefer_non_reviewer_chair": config.prefer_non_reviewer_chair,
        "demote_local_only": config.demote_local_only,
        "ci": {
            "fail_on": list(config.ci.fail_on),
            "ignore_unverified": config.ci.ignore_unverified,
            # `min_vendors` belongs here for the same reason the other two do: it
            # decides the outcome of a run, so two runs that disagree about it are
            # not the same run. Adding it invalidates every existing review cache
            # entry ONCE on upgrade — noted in the CHANGELOG, since a user's first
            # run after the bump is a full one.
            "min_vendors": config.ci.min_vendors,
        },
        "context": {
            "mode": config.context.mode,
            "redact_secrets": config.context.redact_secrets,
        },
        "diff": {
            "max_bytes": config.diff.max_bytes,
            "chunk": config.diff.chunk,
            "chunk_max_bytes": config.diff.chunk_max_bytes,
            "exclude_generated": config.diff.exclude_generated,
            "exclude": list(config.diff.exclude),
            "include": list(config.diff.include),
        },
        "agents": [
            {
                "name": a.name,
                "vendor": a.vendor,
                "command": a.command,
                "endpoint": a.endpoint,
                "model": a.model,
                "timeout": a.timeout,
                "enabled": a.enabled,
                "extra_args": list(a.extra_args),
                # Effort changes the model id / request body an agent sends, so
                # it is orchestration-affecting and must split the cache key.
                "effort": a.effort,
            }
            for a in config.agents
        ],
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_raw_config(path: str | Path | None = None) -> dict:
    """Return the raw config dict for *path*, or the built-in default.

    If *path* is None, look for ``jury.toml`` in the current directory and
    fall back to :data:`DEFAULT_CONFIG` when it is absent. An explicit *path*
    that does not exist raises ``FileNotFoundError``.
    """
    if path is None:
        candidate = Path("jury.toml")
        if not candidate.exists():
            return DEFAULT_CONFIG
        path = candidate
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    return _read_toml_bounded(path)


def load_config(
    path: str | Path | None = None,
    validate: bool = False,
    strict: bool = False,
) -> JuryConfig:
    """Load jury config from *path*, or fall back to the built-in default.

    If *path* is None, look for ``jury.toml`` in the current directory.

    When *validate* is True, the resolved config dict is checked with
    :func:`validate_config` before being materialized; a ``ConfigError`` is
    raised on hard-invalid input (and on warnings when *strict* is True).
    Validation is opt-in so existing callers stay unaffected.
    """
    data = load_raw_config(path)
    if validate:
        validate_config(data, strict=strict)
    return _from_dict(data)
