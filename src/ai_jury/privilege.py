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


def _is_sandboxed(extra_args: list[str]) -> bool:
    """True when a non-claude agent runs under a restricting sandbox.

    Recognizes ``--sandbox`` (agy/gemini terminal-restricted sandbox) and a
    read-only codex sandbox (``-s read-only`` / ``--sandbox read-only``). When a
    sandbox is active, an otherwise-broad flag like ``--dangerously-skip-
    permissions`` no longer grants real write/tool/network powers, so it is not
    flagged (issue #100).
    """
    args = list(extra_args)
    for i, a in enumerate(args):
        if a in ("-s", "--sandbox"):
            nxt = args[i + 1] if i + 1 < len(args) else ""
            # Bare --sandbox (agy), or an explicit read-only codex sandbox.
            if a == "--sandbox" and (nxt == "" or nxt.startswith("-") or nxt == "read-only"):
                return True
            if nxt == "read-only":
                return True
    return False


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
    if _is_sandboxed(extra_args):
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
