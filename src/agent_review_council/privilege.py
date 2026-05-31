"""Least-privilege auditing for review agents (OWASP LLM01 defense-in-depth).

Reviewers process attacker-controlled content (the PR diff and, via ``--pr``, the
PR title/body). If an agent CLI is invoked with write/tool/network powers, a
successful prompt injection could escalate from "bad review text" to real
side effects. The council mitigates this by running agents read-only.

This module inspects each configured agent's ``extra_args`` and WARNS when an
agent could perform write or tool actions during review. It is advisory by
default (a warning, surfaced via ``run_council``); ``--strict`` promotes the
warnings to a hard failure.

Required read-only invocation per adapter (documented here and in docs/security.md):

- ``claude``  : pass ``--disallowed-tools Edit,Write,NotebookEdit,Bash`` so the
                reviewer cannot edit files or run shell commands.
- ``codex``   : prefer ``-s read-only`` (or ``--sandbox read-only``). The shipped
                default uses ``-s danger-full-access`` for network access during
                ``--pr`` review; that is flagged here so operators opt in knowingly.
- ``agy``/gemini : avoid ``--dangerously-skip-permissions`` / ``--yolo``; rely on
                the default permission prompts or an explicit read-only mode.
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

    # Non-claude agents: any broad-powers flag is a least-privilege concern.
    for flag in _DANGEROUS_FLAGS:
        if flag in extra_args or flag in args_text:
            warnings.append(
                f"agent '{label}' is configured with `{flag}`, granting "
                f"write/tool/network powers while reviewing untrusted content; "
                f"prefer a read-only sandbox (e.g. codex `-s read-only`)."
            )
            break

    return warnings


def audit_privilege(specs) -> list[str]:
    """Return all least-privilege warnings across the configured agents."""
    warnings: list[str] = []
    for spec in specs:
        warnings.extend(audit_agent(spec))
    return warnings
