"""Least-privilege auditing for review agents (OWASP LLM01 defense-in-depth).

Reviewers process attacker-controlled content (the PR diff and, via ``--pr``, the
PR title/body). If an agent CLI is invoked with write/tool/network powers, a
successful prompt injection could escalate from "bad review text" to real
side effects. The jury mitigates this by running agents read-only.

This module inspects each configured agent's ``extra_args`` and WARNS when an
agent could perform write or tool actions during review. It is advisory by
default (a warning, surfaced via ``run_jury``); ``--strict`` promotes the
warnings to a hard failure.

Required read-only invocation per adapter (documented here and in docs/security.md):

- ``claude``  : pass ``--disallowed-tools Edit,Write,NotebookEdit,Bash`` so the
                reviewer cannot edit files or run shell commands.
- ``codex``   : ``-s read-only`` (the shipped default, issue #100). A wider
                sandbox (``workspace-write``/``danger-full-access``) is flagged
                here so operators opt in knowingly.
- ``agy``/gemini : run under ``--sandbox`` (the shipped default). A bare
                ``--dangerously-skip-permissions`` / ``--yolo`` without a sandbox
                is flagged.
"""
from __future__ import annotations

# Flags that grant broad write/tool/network powers — dangerous for a reviewer.
_DANGEROUS_FLAGS: tuple[str, ...] = (
    "--dangerously-skip-permissions",
    "--yolo",
    "danger-full-access",
    "--full-auto",
)

# Tool names that allow filesystem writes or shell execution.
_WRITE_TOOLS: tuple[str, ...] = ("Edit", "Write", "NotebookEdit", "Bash")


def _args_str(extra_args: list[str]) -> str:
    return " ".join(extra_args)


# Codex sandbox VALUES that actually restrict the agent. A value sandbox like
# ``workspace-write`` / ``danger-full-access`` does NOT (issue #292): the audit
# must not treat the mere presence of a ``-s``/``--sandbox`` token as proof of a
# read-only run when its value grants write/tool powers.
_RESTRICTING_SANDBOX_VALUES: tuple[str, ...] = ("read-only",)


def _is_sandboxed(extra_args: list[str], vendor: str = "", name: str = "") -> bool:
    """True when a non-claude agent runs under a *restricting* sandbox.

    Vendor-aware (issue #292) so a bare ``--sandbox`` token cannot give false
    assurance: only the agy/gemini terminal sandbox is a genuine boolean
    ``--sandbox``; for codex the sandbox takes a VALUE and only ``read-only``
    restricts (``-s read-only`` / ``--sandbox read-only``). A bare ``--sandbox``
    from any other vendor — e.g. ``["--sandbox", "--dangerously-skip-permissions",
    "--yolo"]`` — is no longer accepted as a sandbox. When a restricting sandbox
    is active, an otherwise-broad flag no longer grants real powers (issue #100).
    """
    vendor = (vendor or "").lower()
    name = (name or "").lower()
    is_agy = vendor == "google" or "agy" in name or "gemini" in name
    args = list(extra_args)
    for i, a in enumerate(args):
        if a in ("-s", "--sandbox"):
            nxt = args[i + 1] if i + 1 < len(args) else ""
            # Codex (and any vendor): an explicit read-only sandbox value.
            if nxt in _RESTRICTING_SANDBOX_VALUES:
                return True
            # agy/gemini: bare boolean --sandbox (no value, or another flag next).
            if a == "--sandbox" and is_agy and (nxt == "" or nxt.startswith("-")):
                return True
    return False


def _ensure_claude_disallowed(extra_args: list[str]) -> list[str]:
    """Guarantee ``--disallowed-tools`` covers every write tool (issue #288).

    Merges the mandatory write tools into any existing ``--disallowed-tools``
    value (config may ADD denials, never REMOVE the mandatory ones), or injects
    the flag when absent. Idempotent: the shipped default already lists all four,
    so it is returned unchanged.
    """
    args = list(extra_args)
    out: list[str] = []
    i = 0
    found = False
    while i < len(args):
        a = args[i]
        if a == "--disallowed-tools" and i + 1 < len(args):
            found = True
            existing = [t.strip() for t in args[i + 1].split(",") if t.strip()]
            for tool in _WRITE_TOOLS:
                if tool not in existing:
                    existing.append(tool)
            out.extend([a, ",".join(existing)])
            i += 2
            continue
        out.append(a)
        i += 1
    if not found:
        out = ["--disallowed-tools", ",".join(_WRITE_TOOLS), *out]
    return out


def _ensure_value_sandbox(extra_args: list[str], default: list[str]) -> list[str]:
    """Ensure SOME sandbox flag is present; inject ``default`` only when none is.

    If the operator already specified ``-s``/``--sandbox`` (even a wider value
    like codex ``workspace-write``), respect it — that is a documented, audited
    opt-in. We only inject the secure default when no sandbox flag exists at all,
    which is the actual hole (empty/misconfigured ``extra_args``, issue #288).
    """
    args = list(extra_args)
    if any(a in ("-s", "--sandbox") for a in args):
        return args
    return [*default, *args]


def enforce_read_only(vendor: str, name: str, extra_args: list[str]) -> list[str]:
    """Return ``extra_args`` with the mandatory read-only restriction guaranteed.

    The sandbox is enforced here (issue #288) rather than left to config, so an
    empty or misconfigured ``extra_args`` cannot produce a write-capable reviewer
    of an attacker-controlled diff. Config may still WIDEN a codex sandbox
    (``-s workspace-write``) — an explicit opt-in the audit warns about — but it
    can never REMOVE the restriction. Only vendors whose sandbox syntax is known
    are touched; a ``local`` (network) agent or an unknown vendor is returned
    unchanged (the audit still warns).
    """
    vendor = (vendor or "").lower()
    name = (name or "").lower()
    extra_args = list(extra_args or [])
    if "claude" in name or vendor == "anthropic":
        return _ensure_claude_disallowed(extra_args)
    if vendor == "openai" or "codex" in name:
        return _ensure_value_sandbox(extra_args, ["-s", "read-only"])
    if vendor == "google" or "agy" in name or "gemini" in name:
        return _ensure_value_sandbox(extra_args, ["--sandbox"])
    return extra_args


def _claude_is_locked_down(extra_args: list[str]) -> bool:
    """True when claude is given --disallowed-tools covering all write tools."""
    disallowed: set[str] = set()
    args = list(extra_args)
    for i, a in enumerate(args):
        if a == "--disallowed-tools" and i + 1 < len(args):
            disallowed |= {t.strip() for t in args[i + 1].split(",") if t.strip()}
    return all(t in disallowed for t in _WRITE_TOOLS)


def audit_agent(spec) -> list[str]:
    """Return least-privilege warnings for a single agent spec."""
    warnings: list[str] = []
    name = (getattr(spec, "name", "") or "").lower()
    vendor = (getattr(spec, "vendor", "") or "").lower()
    extra_args = list(getattr(spec, "extra_args", []) or [])
    args_text = _args_str(extra_args)
    label = getattr(spec, "name", "agent")

    is_claude = "claude" in name or vendor == "anthropic"

    if is_claude:
        if not _claude_is_locked_down(extra_args):
            warnings.append(
                f"agent '{label}' (claude) is not restricted to read-only: add "
                f"`--disallowed-tools {','.join(_WRITE_TOOLS)}` so a prompt "
                f"injection in the diff cannot edit files or run commands."
            )
        # claude's own default config additionally uses
        # --dangerously-skip-permissions; that is safe only *because* write
        # tools are disallowed, so we don't warn separately when locked down.
        return warnings

    # Non-claude agents: a broad-powers flag is a least-privilege concern UNLESS
    # the agent is also run under a restricting sandbox (issue #100), which
    # neutralizes it.
    if _is_sandboxed(extra_args, vendor=vendor, name=name):
        return warnings
    for flag in _DANGEROUS_FLAGS:
        if flag in extra_args or flag in args_text:
            warnings.append(
                f"agent '{label}' is configured with `{flag}`, granting "
                f"write/tool/network powers while reviewing untrusted content; "
                f"prefer a read-only sandbox (e.g. codex `-s read-only` or agy "
                f"`--sandbox`)."
            )
            break

    return warnings


def audit_privilege(specs) -> list[str]:
    """Return all least-privilege warnings across the configured agents."""
    warnings: list[str] = []
    for spec in specs:
        warnings.extend(audit_agent(spec))
    return warnings
