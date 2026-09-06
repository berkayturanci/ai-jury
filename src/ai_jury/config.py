"""Configuration loading for the jury.

Config is TOML (see ``jury.toml``). The loader is tolerant: a missing config
file falls back to a sensible built-in default so the tool runs out of the box.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from pathlib import Path
from urllib.parse import urlsplit

from .ci import fail_on_error
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
# issue #430/#432, joined by `xai-api` in #701).
_NO_COMMAND_VENDORS = (
    "local",
    "anthropic-api",
    "openai-api",
    "google-api",
    "xai-api",
    "openai-compatible",
)

KNOWN_VENDORS = (
    "anthropic",
    "openai",
    "google",
    "xai",
    "local",
    "anthropic-api",
    "openai-api",
    "google-api",
    "xai-api",
    "openai-compatible",
    "cli",
)

#: The vendor identity every unrecognised vendor collapses into (issue #701).
#: ``cli`` is a real, documented vendor — "some CLI I brought myself" — and the
#: generic fallback lands a seat in exactly that bucket, so that is the identity
#: it carries for the cross-vendor gate.
GENERIC_VENDOR = "cli"

#: Vendors serviced by the generic bring-your-own-CLI adapter. The operator
#: supplies ``command``/``extra_args``; the tool knows no vendor-specific
#: sandbox flag to add or remove for them (issue #701).
GENERIC_CLI_VENDORS = ("cli", "xai")

#: Vendors registered at runtime through ``adapters.register_adapter`` — the
#: documented extension point for a custom adapter. Registering an adapter is
#: what makes a vendor name *known*: without it the name is a typo as far as
#: this tool can tell, and :func:`vendor_identity` folds it into
#: ``GENERIC_VENDOR``. Module-level mutable state, deliberately: it mirrors
#: ``adapters._VENDOR_ADAPTERS``, which is mutable for the same reason.
_REGISTERED_VENDORS: set[str] = set()


def register_vendor(vendor: str) -> None:
    """Record *vendor* as a recognised name (called by ``register_adapter``)."""
    name = normalise_vendor(vendor)
    if name:
        _REGISTERED_VENDORS.add(name)


def recognised_vendors() -> tuple[str, ...]:
    """Every vendor name this run understands: shipped plus runtime-registered."""
    return (*KNOWN_VENDORS, *sorted(_REGISTERED_VENDORS - set(KNOWN_VENDORS)))


def normalise_vendor(vendor) -> str:
    """The single spelling of a configured vendor every rule reads (pure).

    ``vendor = "XAI-API"`` and ``vendor = " xai-api "`` name the same vendor as
    ``vendor = "xai-api"``, so they must normalise to it *before* any rule looks
    at them. This is the one place that decides what a configured vendor string
    means: validation, the adapter lookup and :func:`vendor_identity` all go
    through it, so a seat cannot pass validation under one spelling and reach
    the cross-vendor gate under another (issue #701, review round 2).

    Anything that is not a string is not a vendor name, so it normalises to
    ``""`` rather than raising: ``vendor = 3`` is a config mistake to warn
    about, not a crash.
    """
    return vendor.strip().lower() if isinstance(vendor, str) else ""


def is_recognised_vendor(vendor) -> bool:
    """Whether *vendor* names a vendor the tool actually knows (pure-ish)."""
    key = normalise_vendor(vendor)
    return bool(key) and key in set(recognised_vendors())


def is_commandless_vendor(vendor) -> bool:
    """Whether *vendor* talks HTTP directly and so needs no ``command`` (pure).

    The one reader of :data:`_NO_COMMAND_VENDORS`. Validation asked this
    question of the raw string while doctor asked it of a copy of the same
    tuple, which is how ``vendor = "XAI-API"`` could be a recognised vendor and
    still be told it was missing a ``command``.
    """
    key = normalise_vendor(vendor)
    return bool(key) and (key in _NO_COMMAND_VENDORS or key.endswith("-api"))


def vendor_identity(vendor: str) -> str:
    """The identity a seat carries for the cross-vendor gate (issue #701).

    A recognised vendor keeps its own name. Everything else — a typo, a vendor
    this build predates, anything routed to the generic fallback — answers to
    ``GENERIC_VENDOR``, because that is what it is: two seats the tool could not
    identify are not two perspectives, and counting them as two is how a bench
    satisfies ``min_vendors`` without being diverse (#682, reached through
    configuration). The raw string is still what the ballots and the report
    carry; only the *gate* collapses, so provenance is never rewritten.

    Returns ``""`` for an empty vendor, which counts as no vendor at all.
    """
    name = normalise_vendor(vendor)
    if not name:
        return ""
    return name if name in set(recognised_vendors()) else GENERIC_VENDOR


def recognised_adapters() -> tuple[str, ...]:
    """Every name a seat may give ``[[agent]] adapter`` (issue #705).

    The adapter vocabulary IS the vendor vocabulary, because ``adapters``' own
    registry is keyed by vendor name: ``anthropic`` selects the claude protocol,
    ``cli`` the bring-your-own-CLI passthrough, ``openai-api`` the hosted API
    call. Derived rather than re-typed so the two cannot drift, and so
    ``register_adapter`` — which teaches this build a name — makes that name
    usable as an ``adapter`` in the same breath.
    """
    return recognised_vendors()


def is_recognised_adapter(adapter) -> bool:
    """Whether *adapter* names an adapter this build actually has (pure-ish)."""
    key = normalise_vendor(adapter)
    return bool(key) and key in set(recognised_adapters())


def unknown_adapter_error(adapter, label: str = "") -> str | None:
    """The ONE diagnosis for an ``adapter`` name this build does not have (pure).

    Returns the message, or ``None`` when the seat names no adapter (it inherits
    its vendor's) or names one that exists.

    Single because the name is read in two places that must not disagree
    (issue #708). :func:`validate_config` reads it before a run and turns this
    into a hard error; ``adapters.make_adapter`` reads it again at the moment the
    command line is built, and refuses instead of falling through to the generic
    adapter. Both are needed. With only the first, every caller that loads a
    config *without* validation — ``--doctor``, ``jury run-agent`` — silently got
    a ``GenericCLIAdapter``, so ``--doctor`` printed three ``[available]`` seats
    and ``ready to run: yes`` for a file a real run refused outright. With only
    the second, the same typo would surface mid-run rather than before the panel
    starts.

    The fall-through was the silent guess #705 exists to remove: a name this
    build does not have is a typo, or a plugin that was not loaded, and invoking
    the seat through *some other* protocol answers neither.
    """
    if adapter is None or is_recognised_adapter(adapter):
        return None
    who = f"agent '{label}'" if label else "agent"
    return (
        f"{who} has unknown adapter {adapter!r} (expected one "
        f"of {', '.join(recognised_adapters())}). 'adapter' names the protocol "
        f"used to build the command line; 'vendor' names the identity the "
        f"cross-vendor gate counts."
    )


def adapter_key(vendor, adapter=None) -> str:
    """The protocol a seat is invoked through, from its two config keys (pure).

    ``vendor`` answers "what is this seat?" — the identity the cross-vendor gate
    counts (:func:`vendor_identity`). ``adapter`` answers "how is its command
    line built?". Before issue #705 one key answered both, so a GPT model
    reached through Cursor's ``cursor-agent`` had to choose between running
    (``vendor = "cli"``, and three such seats are then one vendor) and being
    counted (``vendor = "openai"``, and ``cursor-agent exec`` is not a command).

    An unset ``adapter`` falls back to the vendor, which is what makes every
    configuration written before this key existed byte-identical in argv.
    """
    return normalise_vendor(adapter) or normalise_vendor(vendor)


def spec_adapter(spec) -> str:
    """:func:`adapter_key` for any spec-like object (duck-typed, pure).

    Readers outside this module — ``privilege``, ``doctor``, ``adapters`` — are
    handed specs by tests as well as by the loader, so they ask here instead of
    reaching for two attributes each and disagreeing about the fallback.
    """
    return adapter_key(getattr(spec, "vendor", ""), getattr(spec, "adapter", None))


KNOWN_TOP_LEVEL_KEYS = ("jury", "agent")
KNOWN_JURY_KEYS = (
    "rounds",
    "chair",
    "timeout",
    "parallel",
    "verify",
    # Nested tables. The keys inside them are checked too, against
    # `KNOWN_NESTED_JURY_KEYS` — defined below, beside the dataclasses it
    # derives them from (issue #719).
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
    # Large-diff handling (issue #31); a nested table, like `ci`/`context`.
    "diff",
    # Risk-aware tiered model routing (issue #524) and the static-analysis
    # pre-pass (issue #523). Both are read by `_from_dict` and documented in
    # docs/configuration.md, so `--strict-config` must not reject them (#715).
    "routing",
    "hints",
)
KNOWN_AGENT_KEYS = (
    "name",
    "vendor",
    # The protocol used to build this seat's command line (issue #705).
    # Optional: it defaults to the vendor's shipped adapter.
    "adapter",
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
    # Cost tier (issue #714): what tiered routing reads to decide which seats
    # sit on a routine diff and which one anchors it.
    "tier",
)

#: Accepted values for ``[[agent]] effort`` / ``--effort`` (issue #662).
#: Duplicated as a literal rather than imported from ``adapters`` so config
#: validation stays free of any adapter import (``adapters`` imports ``config``).
#: ``tests/test_adapters.py`` pins the two lists together.
KNOWN_EFFORTS = ("low", "medium", "high")

#: Accepted values for ``[[agent]] tier`` (issue #714). ``frontier`` is the
#: default and the anchor-capable kind; ``economical`` is a seat tiered routing
#: may put on a routine diff in place of the frontier seats it benches. The
#: operator says which is which — no model-name heuristics.
KNOWN_TIERS = ("frontier", "economical")
DEFAULT_TIER = "frontier"

#: Accepted values for ``[jury] routing`` (issue #524). ``standard`` is the
#: default uniform panel; ``tiered`` is the risk-aware panel that reads each
#: seat's :data:`KNOWN_TIERS` value. The closed vocabulary is the companion of
#: ``tier``'s (#747): the two keys are one feature, and a misspelling of either
#: silently produces the panel the operator did not ask for.
KNOWN_ROUTINGS = ("standard", "tiered")
DEFAULT_ROUTING = "standard"


class ConfigError(Exception):
    """Raised when a jury configuration is invalid."""


# The `[[agent]] headers` messages live here, not inline, because TWO paths
# reject the same shapes and must say the same thing (issue #716, review r1):
# `validate_config` collects them into its error list, and `_from_dict` raises
# one directly for a config that reached materialisation unvalidated —
# `load_config` defaults to `validate=False` and `jury run-agent` deliberately
# keeps it that way so one seat's mistake cannot stop a single-seat run.
#
# Every message names the AGENT and, where it helps, the header NAME, and none
# of them quotes the offending VALUE back: a header is exactly where an
# `Authorization: Bearer …` credential lives, and these are printed to terminals
# and pasted into issues. The type says what is wrong without reproducing what
# is secret — the precedent `api_key_env` set for a rejected value.
def _headers_not_a_table_message(label: str, value) -> str:
    """`headers` is not a table at all — it cannot become headers (hard)."""
    return f"agent '{label}' headers must be a table of string keys (got {type(value).__name__})."


def _headers_bad_key_message(label: str, key) -> str:
    """A key that is not a string cannot be a header name (hard).

    tomllib cannot produce one — every TOML key, bare or quoted, parses to
    `str` — so this guards a config dict built in Python (a test, an embedder
    calling `validate_config`/`_from_dict` directly) rather than a written file.
    """
    return (
        f"agent '{label}' headers has a non-string header name "
        f"({type(key).__name__}); header names must be strings."
    )


def _headers_coerced_value_warning(label: str, header: str, value) -> str:
    """A non-string value still becomes a header — so this is soft.

    `X-Retries = 3` is a working fallback: it is sent as `X-Retries: 3`. Under
    this module's split (unusable ⇒ hard error, working fallback ⇒ warning, as
    for a malformed `api_key_env` name) that is a warning, and `--strict-config`
    is where an operator asks for it to be fatal.
    """
    return (
        f"agent '{label}' headers value for '{header}' is not a string "
        f"({type(value).__name__}); it was coerced to a string before being sent."
    )


# The nested `[jury.*]` shape message lives here for the same reason the
# `headers` ones do (issue #729): TWO paths reject the same shape and must say
# the same thing. `validate_config` collects it into its error list, and the
# `_*_from_dict` readers raise it directly for a config that reached
# materialisation unvalidated — `load_config` defaults to `validate=False`, and
# `jury run-agent` keeps it that way on purpose.
#
# `[jury.ci]` and `[jury.diff]` were checked with this wording written out
# inline; `[jury.context]` was not checked at all, so `context = "diff-only"`
# (written where `[jury.context] mode = "diff-only"` was meant) passed
# `--config-validate --strict-config` and then died in `_context_from_dict` with
# `'str' object has no attribute 'get'`.
#: Top-level shape messages, shared by ``validate_config`` and ``_from_dict`` so the
#: validating and the non-validating path (``jury run-agent``, ``load_config``'s
#: default) say the same thing (issue #732).
_JURY_NOT_A_TABLE = "[jury] must be a table."
_AGENTS_NOT_AN_ARRAY = "[[agent]] must be an array of tables."


def _agent_not_a_table_message(idx: int) -> str:
    return f"agent[{idx}] must be a table."


def _nested_table_message(table: str) -> str:
    """`[jury.<table>]` is not a table, so its reader cannot read it (hard)."""
    return f"[jury.{table}] must be a table."


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
        raise ConfigError(_JURY_NOT_A_TABLE)

    for key in jury:
        if key not in KNOWN_JURY_KEYS:
            warnings.append(
                f"unknown key 'jury.{key}' (expected one of {', '.join(KNOWN_JURY_KEYS)})."
            )

    # Unknown keys INSIDE the nested `[jury.*]` tables (soft, issue #719).
    #
    # The loop above stops at the table names: it sees `jury.ci` and is happy.
    # `_ci_from_dict`/`_context_from_dict`/`_diff_from_dict` then take the keys
    # they know and drop the rest, so `[jury.ci] min_vendor = 3` (the
    # cross-vendor gate, one `s` short) passed `--config-validate
    # --strict-config` clean and the panel ran on the default of 2. Same soft
    # severity as the top level — the config still loads, it just does not mean
    # what it says — with the dotted path in the message so the operator is
    # told which table to look in.
    #
    # A non-table is reported as a shape error and NOT iterated for unknown
    # keys: iterating a string would produce one warning per character, and the
    # value cannot carry keys anyway. All three tables are checked in this one
    # loop (issue #729) — `ci` and `diff` had the check written out inline below
    # and `context` had none, which is exactly how the missing one went unnoticed.
    for table, known_nested in KNOWN_NESTED_JURY_KEYS.items():
        nested = jury.get(table)
        if nested is None:
            continue
        if not isinstance(nested, dict):
            errors.append(_nested_table_message(table))
            continue
        for key in nested:
            if key not in known_nested:
                warnings.append(
                    f"unknown key 'jury.{table}.{key}' (expected one of {', '.join(known_nested)})."
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

    # Panel routing (hard when present and not a known kind, issue #747), for the
    # reason `[[agent]] tier` is hard: nothing equals "tiered" but "tiered", so
    # `routing = "teired"` would otherwise be read as the default and the run
    # would quietly buy the uniform panel the operator asked it not to.
    routing = jury.get("routing")
    if routing is not None and (
        not isinstance(routing, str) or routing.strip().lower() not in KNOWN_ROUTINGS
    ):
        errors.append(f"jury.routing must be one of {', '.join(KNOWN_ROUTINGS)} (got {routing!r}).")

    # Adaptive rounds (issue #40): max_rounds >= 1 (hard); early_stop is a bool.
    max_rounds = jury.get("max_rounds")
    if max_rounds is not None and (
        not isinstance(max_rounds, int) or isinstance(max_rounds, bool) or max_rounds < 1
    ):
        errors.append(f"jury.max_rounds must be an integer >= 1 when set (got {max_rounds!r}).")

    # Large-diff handling (issue #31): [jury.diff] sizes are positive ints.
    diff_cfg = jury.get("diff", {})
    if isinstance(diff_cfg, dict):
        for key in ("max_bytes", "chunk_max_bytes"):
            val = diff_cfg.get(key)
            if val is not None and (not isinstance(val, int) or isinstance(val, bool) or val <= 0):
                errors.append(f"jury.diff.{key} must be a positive integer when set (got {val!r}).")

    # CI gate severities (issue #718): hard, like `effort`, and for the same
    # reason. `fail_on = ["majr"]` matches no group, so the one setting that
    # decides whether CI fails would report a green PASS quoting the typo, on
    # every run, forever — a silently disabled gate is worse than no gate.
    ci_cfg = jury.get("ci", {})
    if isinstance(ci_cfg, dict):
        fail_on = ci_cfg.get("fail_on")
        if fail_on is not None:
            # A scalar is accepted here because `_ci_from_dict` wraps one in a
            # list; validating the raw value would refuse a config that loads.
            message = fail_on_error(
                fail_on if isinstance(fail_on, list) else [fail_on], "jury.ci.fail_on"
            )
            if message:
                errors.append(message)

    agents_data = data.get("agent", [])
    if not isinstance(agents_data, list):
        raise ConfigError(_AGENTS_NOT_AN_ARRAY)

    # At least one agent (hard).
    if not agents_data:
        errors.append("no agents configured; define at least one [[agent]] entry.")

    seen_names: set = set()
    enabled_names: set = set()
    for idx, agent in enumerate(agents_data):
        if not isinstance(agent, dict):
            errors.append(_agent_not_a_table_message(idx))
            continue

        for key in agent:
            if key not in KNOWN_AGENT_KEYS:
                warnings.append(
                    f"unknown key 'agent[{idx}].{key}' (expected one of "
                    f"{', '.join(KNOWN_AGENT_KEYS)})."
                )

        name = agent.get("name", "")
        label = name or f"agent[{idx}]"

        # Normalise the vendor ONCE, here, and let every later rule read the
        # normalised value (issue #701, review round 2). A vendor that is
        # recognised must be recognised by every rule: deciding "commandless
        # API vendor" on the raw string made `vendor = "XAI-API"` a known
        # vendor that was nonetheless failed for having no `command`.
        # `vendor_value` is kept for the messages, which quote what the
        # operator actually wrote.
        vendor_value = agent.get("vendor", "")
        vendor = normalise_vendor(vendor_value)

        # The adapter is the PROTOCOL, the vendor is the IDENTITY (issue #705).
        # Every question below of the form "how is this seat invoked?" — does it
        # need a `command`, which sandbox flag does it accept — is asked of the
        # adapter, which is the vendor itself unless the operator said otherwise.
        adapter_value = agent.get("adapter")
        adapter = adapter_key(vendor, adapter_value)

        # Unknown adapter (HARD, unlike an unknown vendor). An unknown vendor
        # still names a seat that can run; it just answers to `cli` at the gate.
        # An unknown adapter names a protocol this build does not have, and the
        # only fallbacks are to guess — which is precisely the failure #705 is
        # about: `openai` guessed `codex exec` onto `cursor-agent` and the seat
        # died in half a second, mid-run, having already been paid for. A typo
        # here is named before the panel starts, like `effort`. The message
        # itself lives in `unknown_adapter_error`, because `make_adapter` asks
        # the same question at the other end and must give the same answer
        # (issue #708).
        adapter_error = unknown_adapter_error(adapter_value, label)
        if adapter_error is not None:
            errors.append(adapter_error)

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
        has_endpoint = bool(agent.get("endpoint"))
        # Asked of the ADAPTER, not the vendor (#705): whether a seat needs a
        # `command` is a fact about how it is invoked. `vendor = "openai",
        # adapter = "cli"` runs a CLI and needs one; `vendor = "openai",
        # adapter = "openai-api"` makes an HTTP call and does not.
        is_local_or_http = is_commandless_vendor(adapter) or has_endpoint
        if is_local_or_http:
            if not agent.get("model"):
                who = (
                    f"vendor '{vendor_value}'"
                    if adapter == vendor
                    else f"adapter '{adapter_value}'"
                )
                warnings.append(
                    f"agent '{label}' ({who}) has no 'model'; the "
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

        # `headers` carries the extra HTTP headers a hosted adapter sends
        # (issue #716). It was never checked at all: `_from_dict` turned every
        # non-table into `{}`, so `headers = "Authorization = Bearer y"` passed
        # `--config-validate` AND `--strict-config` and the seat ran with no
        # extra headers — failing at the remote API, or, for a routing header
        # the provider honours a default for, reviewing quietly against a
        # backend nobody chose.
        #
        # The severity follows this module's split, not the fact that the key is
        # newly checked: a shape that CANNOT become headers is hard (like an
        # unknown `effort`), a shape that becomes working headers by a documented
        # coercion is a warning (like a malformed `api_key_env` name that falls
        # back to the vendor default). Hard: not a table, and a non-string key —
        # neither can be a header name or map. Soft: a non-string VALUE, which
        # `_from_dict` sends as `str(value)`.
        raw_headers = agent.get("headers")
        if raw_headers is not None and not isinstance(raw_headers, dict):
            errors.append(_headers_not_a_table_message(label, raw_headers))
        elif isinstance(raw_headers, dict):
            for header_name, header_value in raw_headers.items():
                if not isinstance(header_name, str):
                    errors.append(_headers_bad_key_message(label, header_name))
                elif not isinstance(header_value, str):
                    warnings.append(
                        _headers_coerced_value_warning(label, header_name, header_value)
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

        # Cost tier (hard when present and not a known kind, issue #714), for
        # the reason `effort` is hard: `tier = "cheap"` would otherwise be read
        # as the default and the seat silently treated as frontier — the exact
        # opposite of what the operator wrote.
        tier = agent.get("tier")
        if tier is not None and (
            not isinstance(tier, str) or tier.strip().lower() not in KNOWN_TIERS
        ):
            errors.append(
                f"agent '{label}' tier must be one of {', '.join(KNOWN_TIERS)} (got {tier!r})."
            )

        # Known vendor (soft). The warning names the CONSEQUENCE, not just the
        # fact: the fallback seat still runs, but it answers to `cli` at the
        # cross-vendor gate, so two of them are one vendor (issue #701).
        if not is_recognised_vendor(vendor):
            warnings.append(
                f"agent '{label}' has unknown vendor '{vendor_value}' (expected one "
                f"of {', '.join(recognised_vendors())}); using the generic "
                f"'{GENERIC_VENDOR}' fallback, which counts as vendor "
                f"'{GENERIC_VENDOR}' for min_vendors — two such seats are one "
                f"vendor, not two."
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
    """One configured seat on the panel.

    ``vendor`` is normalised on construction (issue #701, review round 3) and is
    therefore the ONLY spelling any reader ever sees. Round 2 normalised at each
    rule instead, which left every new reader free to forget: ``_detect_warnings``
    compared the raw string while ``_unavailable_reason`` compared the normalised
    one, so a single ``vendor = "XAI-API"`` seat got two different diagnoses out
    of one ``--doctor`` run. Doing it here — in the one place a spec comes into
    existence, whatever built it — is what makes "every rule reads one value"
    true by construction rather than by review.

    Normalising is only ``strip().lower()``, so provenance survives it: the
    operator's vendor *name* is preserved, only its whitespace and case are not.
    Messages that must quote the file verbatim (``validate_config``) read the raw
    TOML dict, not this field. A non-string vendor normalises to ``""`` — a
    config mistake to warn about, not a crash in a later ``.lower()``.
    """

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
    # The protocol this seat is invoked through (issue #705): which adapter
    # builds its command line. ``None`` means "the vendor's shipped adapter",
    # which is what every configuration written before this key existed means —
    # so their argv is unchanged, byte for byte. Declared last, after every
    # other field, so no positional construction site is silently re-bound.
    # Read it through :attr:`adapter_key`, never directly: the fallback to the
    # vendor belongs in one place.
    adapter: str | None = None
    # Cost tier (issue #714): "frontier" (default; may anchor a routed panel) or
    # "economical" (seated on routine diffs in place of benched frontier seats).
    # Normalised on construction like `vendor`; an unknown value is refused by
    # `validate_config` and falls back to the default here so the reader never
    # carries a spelling no rule knows.
    tier: str = DEFAULT_TIER

    def __post_init__(self) -> None:
        # The single normalisation point. Every construction site — `_from_dict`,
        # `runagent`'s built-in templates, `cli`'s ad-hoc local seat, a test —
        # goes through it, so no reader downstream can be handed the raw form.
        self.vendor = normalise_vendor(self.vendor)
        tier = str(self.tier).strip().lower() if isinstance(self.tier, str) else ""
        self.tier = tier if tier in KNOWN_TIERS else DEFAULT_TIER
        # Same treatment for the adapter, and for the same reason (#701 r3):
        # `adapter = "CLI"` and `adapter = "cli"` name one protocol, so they must
        # be one string before any lookup sees them. A key that is *present* but
        # normalises to nothing — `7`, `"   "` — is refused here rather than read
        # as "unset": `validate_config` already calls it an unknown adapter, and
        # `jury run-agent` builds seats without validating, so treating it as a
        # fallback to the vendor let that one path run what the other two refused.
        if self.adapter is not None:
            key = normalise_vendor(self.adapter)
            if not key:
                raise ConfigError(
                    unknown_adapter_error(self.adapter, self.name)
                    or f"agent {self.name!r}: adapter {self.adapter!r} names no protocol"
                )
            self.adapter = key

    @property
    def adapter_key(self) -> str:
        """The adapter name this seat resolves to — its ``adapter`` or its vendor."""
        return adapter_key(self.vendor, self.adapter)


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
    # REVIEWS the run must be able to hand a downstream consumer before it is
    # worth running at all (issue #699) — one per ballot that *reviewed*, i.e.
    # ``panel.is_review``: a substantive scope and a voting verdict, so an
    # abstention is not one (#700). That is the
    # number a gate like `keel review --from-jury` counts: it splits the
    # ``reviewers`` array on ``role`` and reads the ``chair`` entry as the
    # panel's consensus, not as a review. ``0`` (the default)
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


#: Known keys inside each nested ``[jury.*]`` table (issue #719).
#:
#: Every one is DERIVED from the dataclass the matching ``*_from_dict`` reader
#: builds, rather than written out a second time, so a new field cannot be added
#: to ``CiConfig``/``ContextConfig``/``DiffConfig`` and leave this list behind —
#: which is how the top-level ``KNOWN_JURY_KEYS`` list has drifted before (#715).
#: The readers happen to accept exactly their dataclass's field names and no
#: aliases; ``tests/test_config_validation.py`` pins each tuple to the keys its
#: reader actually reads, so an alias added later must be added here too.
#:
#: These live here, below the dataclasses, rather than beside ``KNOWN_JURY_KEYS``
#: because deriving them needs the classes to exist. ``validate_config`` reads
#: them at call time, so the ordering in the module is not a problem.
KNOWN_CI_KEYS = tuple(f.name for f in dataclass_fields(CiConfig))
KNOWN_CONTEXT_KEYS = tuple(f.name for f in dataclass_fields(ContextConfig))
KNOWN_DIFF_KEYS = tuple(f.name for f in dataclass_fields(DiffConfig))

#: The nested ``[jury.*]`` tables ``_from_dict`` reads, and the keys each one
#: knows. ``ci``/``context``/``diff`` are the complete set: every other member of
#: ``KNOWN_JURY_KEYS`` is a scalar (``theater`` is a bool, ``routing`` a string),
#: so there is no other sub-table for a typo to disappear into.
KNOWN_NESTED_JURY_KEYS: dict[str, tuple[str, ...]] = {
    "ci": KNOWN_CI_KEYS,
    "context": KNOWN_CONTEXT_KEYS,
    "diff": KNOWN_DIFF_KEYS,
}


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
    # Risk-aware tiered model routing (issue #524): "standard" (uniform panel) |
    # "tiered" (cost-optimized with frontier anchor). Normalised on construction
    # like `AgentSpec.tier`, its companion key; an unknown value is refused by
    # `validate_config` and falls back to the default here so no reader carries a
    # spelling the vocabulary lacks (#747).
    routing: str = DEFAULT_ROUTING
    # Pre-pass static analysis hints (issue #523): inject linter hints into prompt context
    hints: bool = False

    def __post_init__(self) -> None:
        # The single normalisation point for `routing`, for the reason
        # `AgentSpec.__post_init__` is one for `tier`: `_from_dict` is not the
        # only way a config is built — `--doctor` and `jury run-agent` load
        # without validating, and tests and programmatic callers construct
        # `JuryConfig` directly — and every one of those readers compares against
        # the literal "tiered". A value that survives to a reader unnormalised
        # takes the `standard` path while claiming to be something else.
        routing = str(self.routing).strip().lower() if isinstance(self.routing, str) else ""
        self.routing = routing if routing in KNOWN_ROUTINGS else DEFAULT_ROUTING

    @property
    def effective_max_rounds(self) -> int:
        """Round ceiling for adaptive mode: ``max_rounds`` or ``rounds``."""
        return self.max_rounds if self.max_rounds is not None else self.rounds

    @property
    def enabled_agents(self) -> list[AgentSpec]:
        return [a for a in self.agents if a.enabled]


def _ci_from_dict(data: dict) -> CiConfig:
    # `ConfigError`, not the `AttributeError` a `.get` on a string would raise:
    # materialisation is reached WITHOUT validation on real paths — `load_config`
    # defaults to `validate=False` and `jury run-agent` keeps it that way — and
    # those callers handle `ConfigError` (`run-agent` prints it and exits 2)
    # where a traceback would just be a crash (issue #729, following #716).
    if not isinstance(data, dict):
        raise ConfigError(_nested_table_message("ci"))
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
    if not isinstance(data, dict):
        raise ConfigError(_nested_table_message("context"))
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
    if not isinstance(data, dict):
        raise ConfigError(_nested_table_message("diff"))
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
    # The top-level tables get the same treatment the nested ones got in #731: this
    # function is reached without validation on real paths (`jury run-agent`,
    # `load_config`'s default), and a scalar where a table was meant used to fall
    # through to `AttributeError` here instead of the `ConfigError` those callers
    # already handle (issue #732).
    if not isinstance(jury, dict):
        raise ConfigError(_JURY_NOT_A_TABLE)
    agents_raw = data.get("agent", [])
    if not isinstance(agents_raw, list):
        raise ConfigError(_AGENTS_NOT_AN_ARRAY)
    default_timeout = int(jury.get("timeout", 600))
    agents: list[AgentSpec] = []
    for idx, raw in enumerate(agents_raw):
        if not isinstance(raw, dict):
            raise ConfigError(_agent_not_a_table_message(idx))
        # `headers` no longer coerces a non-table to `{}` (issue #716). That
        # coercion is what made the mistake invisible: the seat materialised
        # cleanly carrying no headers, and only the remote API — or nobody —
        # ever noticed.
        #
        # It raises `ConfigError` rather than falling through to an
        # `AttributeError` from `.items()`, because materialisation is reached
        # WITHOUT validation on real paths (review r1): `load_config` defaults
        # to `validate=False`, and `jury run-agent` keeps it that way on purpose
        # so one seat's mistake cannot stop a single-seat run. Those callers
        # already handle `ConfigError` — `run-agent` prints it and exits 2 —
        # and an uncaught traceback would be a regression for them, not a fix.
        # The messages are the shared builders, so the two paths say the same
        # thing and neither echoes the value.
        #
        # A non-string VALUE is coerced, as before, and warned about in
        # `validate_config`: `X-Retries = 3` is a header that works.
        headers_label = raw.get("name") or f"agent[{len(agents)}]"
        raw_headers = raw.get("headers", {})
        if not isinstance(raw_headers, dict):
            raise ConfigError(_headers_not_a_table_message(headers_label, raw_headers))
        for key in raw_headers:
            if not isinstance(key, str):
                raise ConfigError(_headers_bad_key_message(headers_label, key))
        headers_dict = {k: str(v) for k, v in raw_headers.items()}
        api_key_env_val = str(raw["api_key_env"]) if raw.get("api_key_env") else None
        prompt_mode_val = str(raw["prompt_mode"]) if raw.get("prompt_mode") else None
        raw_effort = raw.get("effort")
        effort_val = str(raw_effort).strip().lower() if isinstance(raw_effort, str) else None
        raw_tier = raw.get("tier")
        tier_val = str(raw_tier).strip().lower() if isinstance(raw_tier, str) else ""
        agents.append(
            AgentSpec(
                name=raw["name"],
                # Passed through raw on purpose: `AgentSpec.__post_init__` is the
                # one place that normalises a vendor, so normalising here as well
                # would create a second place to keep in step (issue #701, r3).
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
                tier=tier_val or DEFAULT_TIER,
                # Raw, like `vendor`: `AgentSpec.__post_init__` normalises both,
                # and a second normalisation here would be a second place to keep
                # in step (issue #701 r3, extended by #705).
                adapter=raw.get("adapter"),
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
        # Passed through raw on purpose, like `AgentSpec`'s `vendor`:
        # `JuryConfig.__post_init__` is the one place that normalises a routing
        # kind, so normalising here as well would create a second place to keep
        # in step (#747).
        routing=jury.get("routing", DEFAULT_ROUTING),
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
        # The static-analysis pre-pass changes what Round 1 is shown, and the
        # routing mode changes which models the panel is built from, so two runs
        # that disagree about either are not the same run and must not share a
        # cache entry (#715). Like `min_vendors` before them, adding these keys
        # invalidates every existing review cache entry ONCE on upgrade — noted
        # in the CHANGELOG, since a user's first run after the bump is a full one.
        "hints": config.hints,
        "routing": config.routing,
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
                # The adapter decides which command line is built, so two seats
                # that differ only in it are not the same run and must not share
                # a cache entry (issue #705). Present ONLY when it differs from
                # the vendor: a seat with no `adapter`, or one naming its own
                # vendor, means exactly what it meant before this key existed, so
                # its canonical payload — and therefore every existing cache
                # entry — stays byte-identical. The conditional is the point,
                # not an optimisation: this change invalidates no one's cache.
                **({"adapter": a.adapter_key} if a.adapter_key != a.vendor else {}),
                # The tier decides which seats a routed panel keeps, so two
                # benches that differ in it are not the same run (issue #714).
                # Conditional for the reason `adapter` is: a seat with no `tier`
                # means exactly what it meant before the key existed, so every
                # existing cache entry stays byte-identical.
                **({"tier": a.tier} if a.tier != DEFAULT_TIER else {}),
                # How the prompt reaches the seat: piped to stdin, or appended to
                # argv as the last argument (issue #746). That is the invocation
                # protocol, so a cached outcome produced under one must not be
                # served for a run configured with the other — the rule that put
                # `adapter` here, one field along. Conditional for the reason
                # `adapter` is: `None` and `""` both mean the `stdin` fallback in
                # `GenericCLIAdapter._prompt_mode`, which is what a config that never
                # named the key has always meant, so its canonical payload — and
                # therefore every existing cache entry — stays byte-identical.
                #
                # The test is "was it written", not "does it resolve to something
                # other than the default", which is where this parts company with
                # `adapter` and `tier`: an explicit `prompt_mode = "stdin"`, or an
                # `"ARG"` beside an `"arg"`, splits the key even though the seat is
                # invoked identically. Deliberate. Unlike those two, this field is
                # neither validated nor normalised on construction, so folding
                # spellings together here would put a second, more precise reader
                # of the vocabulary next to the adapter's own — the duplication
                # #701 r3 removed. The cost is one extra run for a config that
                # writes the default out longhand; the alternative is a stale hit.
                **({"prompt_mode": a.prompt_mode} if a.prompt_mode else {}),
                # Where the request goes and under whose key (issue #716). A
                # provider-routing header — `X-Route: premium`, an OpenRouter
                # `HTTP-Referer`, an Azure deployment selector — can put a
                # byte-identical seat in front of a different model, and
                # `api_key_env` can put it in front of a different account. Both
                # are therefore orchestration-affecting under the same rule as
                # `min_vendors` above, and two configs that disagree about
                # either are not the same run.
                #
                # Unconditional, unlike `adapter`: there is no spelling of these
                # keys that means "what it meant before", so this DOES invalidate
                # every existing review cache entry once on upgrade. That is the
                # honest price of a key that was wrong, and it is noted in the
                # CHANGELOG — the user's first run after the bump is a full one.
                # Sorted so the digest depends on the mapping, not on the order
                # the table happened to be written in.
                "api_key_env": a.api_key_env,
                "headers": sorted(a.headers.items()),
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
