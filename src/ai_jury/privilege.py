"""Least-privilege auditing for review agents (OWASP LLM01 defense-in-depth).

Reviewers process attacker-controlled content (the PR diff and, via ``--pr``, the
PR title/body). If an agent CLI is invoked with write/tool/network powers, a
successful prompt injection could escalate from "bad review text" to real
side effects. The jury mitigates this by running agents read-only.

This module both ENFORCES that restriction (:func:`enforce_read_only`, which
every adapter that spawns a CLI routes its args through) and AUDITS it
(:func:`audit_agent`), and it audits the argv a seat is actually spawned with
rather than the ``extra_args`` written in the config (issue #750). The audit is
advisory by default (a warning, surfaced via ``run_jury``); ``--strict`` promotes
the warnings to a hard failure.

Required read-only invocation per adapter (documented here and in docs/security.md):

- ``claude``  : ``--disallowed-tools Edit,Write,NotebookEdit,Bash`` so the
                reviewer cannot edit files or run shell commands. Injected, and
                merged into a narrower deny list, unconditionally — config can
                add denials, never remove them.
- ``codex``   : ``-s read-only`` (the shipped default, issue #100), injected when
                the config names no sandbox at all. A wider sandbox the operator
                DID name (``workspace-write``/``danger-full-access``) is kept as
                written and flagged here, so the opt-in is a knowing one.
- ``agy``/gemini : ``--sandbox`` (the shipped default), injected when absent.
                ``--dangerously-skip-permissions`` / ``--yolo`` only skip an
                approval prompt, so the sandbox beside them — whether the
                operator wrote it or this module injected it — is what settles
                the question.
- ``cli``/``xai`` : the operator's own binary, for which this tool knows no
                sandbox flag to add. Nothing is enforced, so for these the
                declared ``extra_args`` really are the whole story and an
                unsandboxed seat still warns.
"""

from __future__ import annotations

from .config import GENERIC_CLI_VENDORS, normalise_vendor, spec_adapter

# Flags that grant broad write/tool/network powers — dangerous for a reviewer.
_DANGEROUS_FLAGS: tuple[str, ...] = (
    "--dangerously-skip-permissions",
    "--yolo",
    "danger-full-access",
    "--full-auto",
    "workspace-write",
)

# Tool names that allow filesystem writes or shell execution.
_WRITE_TOOLS: tuple[str, ...] = ("Edit", "Write", "NotebookEdit", "Bash")

# The subset of _DANGEROUS_FLAGS a sandbox does NOT settle, because they SELECT a
# sandbox themselves rather than merely skipping an approval prompt (issue #750).
#
# `--dangerously-skip-permissions` and `--yolo` suppress a confirmation; the
# sandbox beside them still confines the agent, which is why the shipped agy
# default pairs the two (issue #100). codex's `--full-auto` is shorthand for a
# *workspace-write* sandbox, and `_ensure_value_sandbox` looks only for an
# `-s`/`--sandbox` token — so `enforce_read_only` passes `-s read-only` alongside
# `--full-auto` without removing it, and which of the two the CLI honours is that
# CLI's own argument-precedence rule, not something this module can assert. The
# other two entries (`workspace-write`, `danger-full-access`) are sandbox VALUES,
# which enforcement leaves exactly as written and the audit already reaches.
#: codex flags that pick a *different* sandbox, and ones that remove the sandbox
#: altogether. Two lists because the operator has to be told which: `--full-auto`
#: selects workspace-write and leaves a sandbox, while `--yolo` is the documented
#: alias of `--dangerously-bypass-approvals-and-sandbox` and leaves none at all.
#: A name is not a meaning — agy's `--yolo` only skips approval prompts and stays
#: inside its sandbox, so reading one CLI's spelling with another's dictionary is
#: how the most dangerous flag on the codex path went unmentioned (#750).
_CODEX_SANDBOX_SELECTORS: tuple[str, ...] = ("--full-auto",)
_CODEX_SANDBOX_DISABLERS: tuple[str, ...] = (
    "--yolo",
    "--dangerously-bypass-approvals-and-sandbox",
)


def _disallowed_tools_at(args: list[str], i: int) -> tuple[str, int] | None:
    """Read a ``--disallowed-tools`` flag at *args[i]*, in either spelling.

    Returns ``(value, span)`` — the comma-separated tool list and how many
    tokens the flag occupies (2 for ``--disallowed-tools Edit,Write``, 1 for
    ``--disallowed-tools=Edit,Write``) — or ``None`` when the token does not
    start a readable flag, including a trailing space form with no value left.

    Shared by :func:`_ensure_claude_disallowed` (which enforces the flag) and
    :func:`_claude_is_locked_down` (which audits it) so the two spellings
    cannot drift apart again (issue #717): the audit knew only the space form,
    so a seat configured with ``--disallowed-tools=Edit,Write,NotebookEdit,Bash``
    was enforced correctly, reported as *not* read-only, and aborted ``--strict``.
    """
    a = args[i]
    if a == "--disallowed-tools":
        return (args[i + 1], 2) if i + 1 < len(args) else None
    if a.startswith("--disallowed-tools="):
        return a.split("=", 1)[1], 1
    return None


def _args_str(extra_args: list[str]) -> str:
    return " ".join(extra_args)


# Codex sandbox VALUES that actually restrict the agent. A value sandbox like
# ``workspace-write`` / ``danger-full-access`` does NOT (issue #292): the audit
# must not treat the mere presence of a ``-s``/``--sandbox`` token as proof of a
# read-only run when its value grants write/tool powers.
_RESTRICTING_SANDBOX_VALUES: tuple[str, ...] = ("read-only",)


def _is_sandboxed(extra_args: list[str], vendor: str = "") -> bool:
    """True when a non-claude agent runs under a *restricting* sandbox.

    Vendor-aware (issue #292) so a bare ``--sandbox`` token cannot give false
    assurance: only the agy/gemini terminal sandbox is a genuine boolean
    ``--sandbox``; for codex the sandbox takes a VALUE and only ``read-only``
    restricts (``-s read-only`` / ``--sandbox read-only``). A bare ``--sandbox``
    from any other vendor — e.g. ``["--sandbox", "--dangerously-skip-permissions",
    "--yolo"]`` — is no longer accepted as a sandbox. When a restricting sandbox
    is active, an otherwise-broad flag no longer grants real powers (issue #100).
    """
    vendor = normalise_vendor(vendor)
    is_agy = vendor == "google"
    args = list(extra_args)
    for i, a in enumerate(args):
        # Equals form (issue #316/L-6): `-s=read-only` / `--sandbox=read-only`,
        # which `enforce_read_only._ensure_value_sandbox` already recognizes — so
        # the audit must too, or it false-positives a genuinely-safe config under
        # `--strict`.
        if a.startswith(("-s=", "--sandbox=")):
            value = a.split("=", 1)[1]
            if value in _RESTRICTING_SANDBOX_VALUES:
                return True
            if a.startswith("--sandbox=") and is_agy and value == "":
                return True
            continue
        if a in ("-s", "--sandbox"):
            nxt = args[i + 1] if i + 1 < len(args) else ""
            # Codex (and any vendor): an explicit read-only sandbox value.
            if nxt in _RESTRICTING_SANDBOX_VALUES:
                return True
            # agy/gemini: bare boolean --sandbox (no value, or another flag next).
            if a == "--sandbox" and is_agy and (nxt == "" or nxt.startswith("-")):
                return True
    return False


def _is_codex(vendor: str) -> bool:
    """The identity rule :func:`enforce_read_only` uses for the codex branch.

    Kept in one place so the audit cannot key off a different half of the seat
    than enforcement did (#750). The rule is the **adapter key** and nothing
    else (#758): reading the seat's name here made agy's ``--yolo`` — which only
    skips approval prompts — get codex's meaning, where it is the alias of
    ``--dangerously-bypass-approvals-and-sandbox``, so a correctly sandboxed
    google seat that happened to be named ``codex-vs-gemini`` failed ``--strict``
    on the identical argv a seat named ``agy`` passed with.
    """
    return normalise_vendor(vendor) == "openai"


def _present(flag: str, args: list[str]) -> str | None:
    """The token in *args* that spells *flag*, bare or with an ``=`` value.

    This module treats the ``=`` spelling as first-class for ``-s`` (#316) and
    ``--disallowed-tools`` (#717); a selector list matching only the bare token
    let ``--full-auto=true`` through while ``--full-auto`` warned.
    """
    for a in args:
        if a == flag or a.startswith(flag + "="):
            return a
    return None


def _competing_sandboxes(extra_args: list[str], vendor: str = "") -> list[tuple[str, bool]]:
    """Sandbox statements in the argv besides the enforced read-only one.

    Each entry is ``(token, disables)`` — ``disables`` marks a flag that removes
    the sandbox rather than picking a different one, because the operator needs
    to be told which.

    :func:`_is_sandboxed` asks "is a restricting sandbox named here?" and stops at
    the first one. That is the right question for enforcement and the wrong one for
    an audit: codex takes the sandbox as a *value*, so a second ``-s
    workspace-write`` sits beside the enforced ``-s read-only`` — enforcement adds
    nothing, a sandbox token already exists — and which one the CLI honours is its
    own argument precedence, not something this module can read.

    A ``-s``/``--sandbox`` whose next token is another flag, or absent, is agy's
    boolean sandbox and states nothing.
    """
    args = list(extra_args)
    found: list[tuple[str, bool]] = []
    if _is_codex(vendor):
        for flag in _CODEX_SANDBOX_SELECTORS:
            token = _present(flag, args)
            if token:
                found.append((token, False))
        for flag in _CODEX_SANDBOX_DISABLERS:
            token = _present(flag, args)
            if token:
                found.append((token, True))
    for i, a in enumerate(args):
        if a.startswith(("-s=", "--sandbox=")):
            value, shown = a.split("=", 1)[1], a
        elif a in ("-s", "--sandbox"):
            value = args[i + 1] if i + 1 < len(args) else ""
            shown = f"{a} {value}"
        else:
            continue
        if not value or value.startswith("-"):
            continue
        if value not in _RESTRICTING_SANDBOX_VALUES:
            found.append((shown, False))
    return found


def _ensure_claude_disallowed(extra_args: list[str]) -> list[str]:
    """Guarantee ``--disallowed-tools`` covers every write tool (issue #288).

    Merges the mandatory write tools into any existing ``--disallowed-tools``
    value (config may ADD denials, never REMOVE the mandatory ones), or injects
    the flag when absent. Idempotent: the shipped default already lists all four,
    so it is returned unchanged.
    """

    def _merged(value: str) -> str:
        existing = [t.strip() for t in value.split(",") if t.strip()]
        for tool in _WRITE_TOOLS:
            if tool not in existing:
                existing.append(tool)
        return ",".join(existing)

    args = list(extra_args)
    out: list[str] = []
    i = 0
    found = False
    while i < len(args):
        a = args[i]
        # Both spellings, via the shared reader: the space form
        # (--disallowed-tools Edit,Write) and the equals form
        # (--disallowed-tools=Edit,Write — review of #288: the exact-match check
        # missed it, so a narrower =-value could sit after the injected safe set
        # and, if the CLI is last-wins, narrow the deny set). The value is
        # rewritten in the spelling it was written in.
        hit = _disallowed_tools_at(args, i)
        if hit is not None:
            value, span = hit
            found = True
            if span == 2:
                out.extend([a, _merged(value)])
            else:
                out.append("--disallowed-tools=" + _merged(value))
            i += span
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
    # Recognize both the space form (-s read-only) and the equals form
    # (--sandbox=read-only) so an existing sandbox is never double-specified.
    if any(a in ("-s", "--sandbox") or a.startswith(("-s=", "--sandbox=")) for a in args):
        return args
    return [*default, *args]


#: Vendors whose args this module leaves exactly as configured, in either
#: direction — :func:`enforce_read_only` and :func:`enable_write` both return
#: them unchanged. Two reasons land a vendor here. Most reach their model over
#: the network rather than by spawning a CLI, so they have no sandbox/tool
#: surface at all. The generic bring-your-own-CLI profiles (`cli`, and `xai`
#: for a Grok seat driven through Cursor's `cursor-agent`, issue #701) do spawn
#: a CLI, but it is the operator's CLI: there is no vendor-specific sandbox
#: flag this tool could add or remove, and injecting agy's `--sandbox` into an
#: unrelated binary breaks the seat rather than confining it.
_NO_SANDBOX_VENDORS: tuple[str, ...] = (
    "local",
    "anthropic-api",
    "openai-api",
    "google-api",
    "xai-api",
    "openai-compatible",
    *GENERIC_CLI_VENDORS,
)

#: Vendors with no subprocess to audit AT ALL — the network transports above,
#: minus the bring-your-own-CLI seats. Derived rather than re-typed (issue #701,
#: round 3): the hand-written copy this replaces had already drifted (no
#: ``xai-api``), and a bring-your-own CLI does spawn a process, so it stays in
#: scope for :func:`audit_agent` even though the tool knows no sandbox flag for it.
_NO_SUBPROCESS_VENDORS: tuple[str, ...] = tuple(
    v for v in _NO_SANDBOX_VENDORS if v not in GENERIC_CLI_VENDORS
)


def _drop_claude_disallowed(extra_args: list[str]) -> list[str]:
    """Drop every ``--disallowed-tools`` flag, in both its spelling forms.

    The inverse of :func:`_ensure_claude_disallowed`. Removing the flag entirely
    (rather than narrowing its value) restores the CLI's own default tool set,
    which is what an implementer role needs.
    """
    args = list(extra_args)
    out: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--disallowed-tools":
            i += 2 if i + 1 < len(args) else 1
            continue
        if a.startswith("--disallowed-tools="):
            i += 1
            continue
        out.append(a)
        i += 1
    return out


def _set_value_sandbox(extra_args: list[str], flag: str, value: str) -> list[str]:
    """Replace any existing value sandbox with ``flag value`` (codex form)."""
    args = list(extra_args)
    out: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-s", "--sandbox"):
            # Drop the flag AND its value — unless the next token is another
            # flag, in which case there is no value to consume.
            nxt = args[i + 1] if i + 1 < len(args) else ""
            i += 2 if nxt and not nxt.startswith("-") else 1
            continue
        if a.startswith(("-s=", "--sandbox=")):
            i += 1
            continue
        out.append(a)
        i += 1
    return [flag, value, *out]


def _drop_bare_sandbox(extra_args: list[str]) -> list[str]:
    """Drop agy's boolean ``--sandbox`` (both spellings), leaving the rest."""
    return [a for a in extra_args if a != "--sandbox" and not a.startswith("--sandbox=")]


def enable_write(vendor: str, name: str, extra_args: list[str]) -> list[str]:
    """Return ``extra_args`` with the vendor's write/tool mode enabled (issue #661).

    *vendor* is the seat's ADAPTER key — the protocol whose flags this function
    speaks — which is the vendor itself unless the seat named an ``adapter``
    (issue #705). The flags belong to the CLI being spawned, not to whose model
    answers, so a GPT seat driven through ``cursor-agent`` is handled as ``cli``.

    The deliberate mirror image of :func:`enforce_read_only`, and the ONLY place
    the read-only guarantee is lifted. It exists for ``jury run-agent --role
    implement|fix --allow-write``: an orchestrator dispatching an *implementer*
    needs the agent to edit files, which is precisely what a reviewer must never
    do. Nothing on the panel path calls this — a review/debate/verify/synthesis
    invocation still goes through :func:`enforce_read_only`, so a prompt
    injection in an attacker-controlled diff cannot reach it.

    Per vendor: claude drops ``--disallowed-tools`` (restoring its own default
    tool set), codex moves to ``-s workspace-write``, and agy (plus any unknown
    vendor, which routes to the agy adapter) drops the boolean ``--sandbox``.
    Network vendors have no such surface and are returned unchanged.
    """
    vendor = normalise_vendor(vendor)
    name = (name or "").lower()
    args = list(extra_args or [])
    # Network vendors first, for the same reason as in enforce_read_only: a
    # local agent named "local-claude" must not be read as claude.
    if vendor in _NO_SANDBOX_VENDORS or vendor.endswith("-api"):
        return args
    if vendor == "anthropic":
        return _drop_claude_disallowed(args)
    if vendor == "openai":
        return _set_value_sandbox(args, "-s", "workspace-write")
    return _drop_bare_sandbox(args)


def enforce_read_only(vendor: str, name: str, extra_args: list[str]) -> list[str]:
    """Return ``extra_args`` with the mandatory read-only restriction guaranteed.

    *vendor* is the seat's ADAPTER key — the protocol whose flags this function
    speaks — which is the vendor itself unless the seat named an ``adapter``
    (issue #705). The flags belong to the CLI being spawned, not to whose model
    answers, so a GPT seat driven through ``cursor-agent`` is handled as ``cli``.

    The sandbox is enforced here (issue #288) rather than left to config, so on the
    adapters that have enforcement an **empty** ``extra_args`` cannot produce a
    write-capable reviewer of an attacker-controlled diff: the sandbox is injected
    when the config names none.
    It is injection, not override, and the difference is what the audit exists to
    cover (issue #750). Config that names a sandbox keeps it — ``-s
    workspace-write`` is passed through as written — and codex's bypass flags
    (``--yolo``, ``--dangerously-bypass-approvals-and-sandbox``) are passed through
    too, so enforcement alone does not guarantee a restriction survives. The
    ``cli`` and ``xai`` adapters have no enforcement at all. Each of those is a case
    :func:`audit_agent` reports, and ``--strict`` turns into a failure. A ``local`` (network) agent runs no
    subprocess and is returned unchanged; neither does a hosted-API agent
    (issue #430) — it makes one HTTP call with no tool/file/shell access at
    all, so there is no ``extra_args``/sandbox concept to enforce. An
    **unknown vendor** is treated like agy here and gets ``--sandbox`` injected
    (issue #310, completes #300) — fail-closed in the sense that matters, that
    the flag is added rather than omitted. What *runs* is usually
    ``GenericCLIAdapter``, not ``AgyAdapter``: ``make_adapter`` returns the
    generic adapter whenever the seat sets a ``command``, and ``AgyAdapter`` is
    only the no-command fallback. So ``--sandbox`` reaches an unknown binary as a
    passthrough token, which that binary may honour, ignore, or reject — it is not
    agy's confinement. That uncertainty is why :func:`audit_agent` still warns for
    an unknown vendor instead of accepting the injected flag as proof (#292).
    """
    # `normalise_vendor`, not `.lower()`: lowercasing alone left `" XAI "`
    # outside `GENERIC_CLI_VENDORS`, so the xai seat fell through to the agy
    # branch and had `--sandbox` injected into `cursor-agent` — the exact flag
    # the xai profile exists to keep off that CLI (issue #701, review round 3).
    # A spelling validation accepts must be the spelling the guard enforces.
    vendor = normalise_vendor(vendor)
    name = (name or "").lower()
    extra_args = list(extra_args or [])
    # `local`/hosted-API vendors are checked FIRST (review of #310): a network
    # agent runs no subprocess, and the name-substring checks below would
    # otherwise mis-handle e.g. a local agent named "local-claude" / "my-codex".
    if vendor in _NO_SANDBOX_VENDORS or vendor.endswith("-api"):
        return extra_args
    if vendor == "anthropic":
        return _ensure_claude_disallowed(extra_args)
    if vendor == "openai":
        return _ensure_value_sandbox(extra_args, ["-s", "read-only"])
    # google / agy / gemini AND any unknown vendor (issue #310, completes #300):
    # an unknown vendor routes to the generic AgyAdapter (--print/--sandbox), so
    # inject --sandbox like agy. An agy-compatible CLI then runs sandboxed; an
    # incompatible one fails on the unknown flag rather than running UNSANDBOXED
    # — fail-closed either way, never fail-open.
    return _ensure_value_sandbox(extra_args, ["--sandbox"])


def _claude_is_locked_down(extra_args: list[str]) -> bool:
    """True when claude is given --disallowed-tools covering all write tools.

    Reads the flag through :func:`_disallowed_tools_at`, the same reader
    :func:`_ensure_claude_disallowed` enforces it with (issue #717), so a seat
    whose args this module accepts as read-only is exactly a seat that module
    leaves unchanged — in either spelling of the flag.
    """
    disallowed: set[str] = set()
    args = list(extra_args)
    i = 0
    while i < len(args):
        hit = _disallowed_tools_at(args, i)
        if hit is None:
            i += 1
            continue
        value, span = hit
        disallowed |= {t.strip() for t in value.split(",") if t.strip()}
        i += span
    return all(t in disallowed for t in _WRITE_TOOLS)


def audit_agent(spec) -> list[str]:
    """Return least-privilege warnings for a single agent spec.

    The subject is the **effective** argv — what ``adapters._read_only_extra_args``
    will spawn this seat with — not the ``extra_args`` written in the config
    (issue #750). Every panel invocation is routed through
    :func:`enforce_read_only`, so the declared list is only half the command
    line: a bare ``claude`` seat with no ``extra_args`` at all — the documented,
    recommended configuration — is spawned with the full write-tool denylist and
    was nevertheless reported as write-capable, failing ``--strict`` on the one
    configuration the docs tell operators to write. An audit that cries wolf on
    the recommended setup is one operators learn to pass ``--no-strict`` around.

    Auditing the enforced argv is also what keeps the check honest where
    enforcement cannot help, with no special case for it: :func:`enforce_read_only`
    is a **no-op** for the bring-your-own-CLI vendors (``cli``/``xai``), so for
    those the effective argv *is* the declared one and every warning that fired
    before still fires. The same holds for a sandbox the operator widened on
    purpose, which enforcement keeps as written rather than narrowing.

    What this can and cannot promise: the enforcement is only as good as the
    adapter that applies it. Every adapter here that spawns a subprocess builds
    its argv through ``adapters._read_only_extra_args`` — claude, codex, agy, and
    the generic CLI adapter an unknown vendor falls through to — so for those the
    audited argv is the real one. A custom adapter registered through
    ``adapters.register_adapter`` is operator-supplied code that may build its
    argv however it likes; for one of those this reports what enforcement *would*
    produce, which is also why a bare ``--sandbox`` is still not trusted from a
    vendor whose CLI is unknown (issue #292).
    """
    warnings: list[str] = []
    name = (getattr(spec, "name", "") or "").lower()
    # The ADAPTER, not the vendor (issue #705). Every question this function
    # asks — is there a subprocess, which sandbox flag would confine it, is one
    # present — is about the CLI that gets spawned. A seat with
    # `vendor = "openai", adapter = "cli"` spawns the operator's own binary, for
    # which this tool knows no sandbox flag; auditing it as codex would demand a
    # `-s read-only` that `cursor-agent` does not have.
    vendor = spec_adapter(spec)
    label = getattr(spec, "name", "agent")

    # Local/HTTP agents (issue #43) and hosted-API agents (issue #430) run no
    # subprocess to sandbox — there is no write/tool/network surface to flag
    # (a hosted-API call has strictly less access than even a sandboxed CLI:
    # no filesystem, no shell, nothing to disallow), so they are out of scope
    # for this audit.
    has_endpoint = bool(getattr(spec, "endpoint", None))
    if vendor in _NO_SUBPROCESS_VENDORS or vendor.endswith("-api") or has_endpoint:
        return warnings

    # The argv this seat is spawned with, byte for byte what
    # `adapters._read_only_extra_args(spec)` returns: same adapter key, same
    # name, same declared args, same function. (`enforce_read_only` lower-cases
    # the name itself, so passing the already-lowered one changes nothing.)
    extra_args = enforce_read_only(vendor, name, list(getattr(spec, "extra_args", []) or []))
    args_text = _args_str(extra_args)

    is_claude = vendor == "anthropic"

    if is_claude:
        # A tripwire, not a live check: `_ensure_claude_disallowed` merges the write
        # tools into the argv this function just enforced, so a locked-down result is
        # guaranteed and the body below cannot run. It is kept because the guarantee
        # lives in another function — if enforcement ever stops injecting, this is
        # what says so instead of the audit silently passing a writable seat. Before
        # #758 removed the name-based identity it was reachable, via a `cli`-adapter
        # seat whose *name* contained "claude".
        if not _claude_is_locked_down(extra_args):  # pragma: no cover - see above
            warnings.append(
                f"agent '{label}' (claude) is not restricted to read-only: add "
                f"`--disallowed-tools {','.join(_WRITE_TOOLS)}` so a prompt "
                f"injection in the diff cannot edit files or run commands."
            )
        # claude's own default config additionally uses
        # --dangerously-skip-permissions; that is safe only *because* write
        # tools are disallowed, so we don't warn separately when locked down.
        return warnings

    # Non-claude agents must run under a restricting sandbox (issue #100) — one
    # the config named, or one enforcement injected above.
    if _is_sandboxed(extra_args, vendor=vendor):
        # …unless a sandbox-SELECTING flag is sitting in the same argv, in which
        # case two sandboxes are specified and the CLI, not this module, decides
        # which one wins (issue #750). Said plainly rather than through the
        # generic message below, which would recommend the `-s read-only` that
        # is already there.
        competing = _competing_sandboxes(extra_args, vendor=vendor)
        if competing:
            named = ", ".join(f"`{token}`" for token, _ in competing)
            one = len(competing) == 1
            # Say which failure mode it is. `--full-auto` picks a write-capable
            # sandbox; `--yolo` removes the sandbox altogether. Telling an operator
            # that a bypass merely "selects a sandbox of its own" describes the
            # milder of the two and understates what they configured.
            # `any`, not `all`: a seat carrying `--full-auto` *and* `--yolo` must be
            # described by the worse of the two, not the milder.
            if any(disables for _, disables in competing):
                effect = (
                    "disables the sandbox entirely"
                    if one
                    else "include a flag that disables the sandbox entirely"
                )
                tail = (
                    f"the enforced read-only sandbox may not apply at all. "
                    f"Drop {'it' if one else 'them'} — a reviewer only reads its prompt."
                )
            else:
                effect = (
                    "selects a sandbox of its own" if one else "each state a sandbox of their own"
                )
                tail = (
                    f"{'it is' if one else 'they are'} passed alongside the enforced "
                    f"read-only sandbox, so which {'of the two applies' if one else 'applies'} "
                    f"is up to the CLI. Drop {'it' if one else 'them'} — a reviewer only "
                    f"reads its prompt."
                )
            warnings.append(f"agent '{label}' is configured with {named}, which {effect}; {tail}")
        return warnings
    # Not sandboxed. A broad-powers flag gets a specific message…
    for flag in _DANGEROUS_FLAGS:
        if flag in extra_args or flag in args_text:
            warnings.append(
                f"agent '{label}' is configured with `{flag}`, granting "
                f"write/tool/network powers while reviewing untrusted content; "
                f"prefer a read-only sandbox (e.g. codex `-s read-only` or agy "
                f"`--sandbox`)."
            )
            return warnings
    # …otherwise warn that it simply isn't sandboxed. This closes the audit
    # blind spot (issue #300): an unknown-vendor or no-flag agent previously
    # produced ZERO warnings and ran via the generic adapter without the
    # read-only guarantee — and so `--strict` could not fail it on this basis.
    warnings.append(
        f"agent '{label}' is not running under a recognized read-only sandbox "
        f"(no `-s read-only` / `--sandbox`); a prompt injection in the diff could "
        f"reach write/tool/network. Add a sandbox, or run with `--strict` to fail."
    )
    return warnings


def audit_privilege(specs) -> list[str]:
    """Return all least-privilege warnings across the configured agents."""
    warnings: list[str] = []
    for spec in specs:
        warnings.extend(audit_agent(spec))
    return warnings
