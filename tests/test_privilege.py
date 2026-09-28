"""Unit tests for the least-privilege agent auditor (OWASP LLM01 defense).

Locks the behaviour that dangerous agent invocations are surfaced as warnings
while a read-only / locked-down config is not. Stdlib + offline.

Since #750 the subject of every audit assertion is the argv the seat is
*spawned* with — `enforce_read_only` applied to the declared `extra_args` — so a
config whose gap the adapter closes is not warned about, and one whose gap it
cannot close still is.
"""

import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_jury import adapters, privilege
from ai_jury.config import DEFAULT_CONFIG, AgentSpec, spawns_process, spec_adapter

#: The whole claude deny list, in the order enforcement writes it.
DENY = "Edit,Write,NotebookEdit,Bash,Read,Grep,Glob,WebFetch,WebSearch,Task,Agent"


#: What enforcement puts in front of a claude argv that has neither flag.
def _from_dict_with_claude(extra_args):
    """A one-seat claude config with *extra_args*."""
    from ai_jury.config import _from_dict

    seat = {"name": "claude", "vendor": "anthropic", "command": "claude", "extra_args": extra_args}
    return _from_dict({"jury": {"chair": "claude"}, "agent": [seat]})


LOCKDOWN = [
    "--tools",
    "",
    "--strict-mcp-config",
    "--safe-mode",
    "--no-session-persistence",
    "--permission-mode",
    "dontAsk",
]


def agy_warning(label: str) -> str:
    """The one warning every agy seat draws: `--sandbox` does not confine agy."""
    return (
        f"agent '{label}' (agy) cannot be confined: even with --sandbox it reads and "
        f"writes files and reaches the network; do not use it on untrusted diffs."
    )


class AuditAgentTest(unittest.TestCase):
    def test_claude_without_disallowed_tools_is_spawned_locked_down(self):
        # Issue #750: the recommended configuration — a `claude` seat with no
        # `extra_args` at all — is spawned with the whole no-tool lockdown,
        # so it cannot write and must not be reported as though it could.
        spec = AgentSpec(name="claude", vendor="anthropic", command="claude", extra_args=[])
        self.assertEqual(
            privilege.enforce_read_only("anthropic", []),
            [*LOCKDOWN, "--disallowed-tools", DENY],
        )
        self.assertEqual(privilege.audit_agent(spec), [])

    def test_claude_locked_down_has_no_warning(self):
        spec = AgentSpec(
            name="claude",
            vendor="anthropic",
            command="claude",
            extra_args=["--disallowed-tools", "Edit,Write,NotebookEdit,Bash"],
        )
        self.assertEqual(privilege.audit_agent(spec), [])

    def test_claude_locked_down_equals_form_has_no_warning(self):
        # Issue #717: the audit knew only the space form, so this seat — which
        # `enforce_read_only` leaves untouched, because it is already locked
        # down — was reported as not read-only and aborted `--strict`.
        spec = AgentSpec(
            name="claude",
            vendor="anthropic",
            command="claude",
            extra_args=[*LOCKDOWN, "--disallowed-tools=" + DENY],
        )
        self.assertEqual(privilege.audit_agent(spec), [])
        self.assertEqual(
            privilege.enforce_read_only("anthropic", list(spec.extra_args)),
            list(spec.extra_args),
        )

    def test_claude_partial_disallowed_equals_form_is_merged_before_spawn(self):
        # Config may ADD denials, never REMOVE the mandatory ones (#288), in
        # either spelling (#717) — so the missing two are merged in and there is
        # nothing left to warn about (#750).
        spec = AgentSpec(
            name="claude",
            vendor="anthropic",
            command="claude",
            extra_args=["--disallowed-tools=Edit,Write"],
        )
        self.assertEqual(
            privilege.enforce_read_only("anthropic", list(spec.extra_args)),
            [*LOCKDOWN, "--disallowed-tools=" + DENY],
        )
        self.assertEqual(privilege.audit_agent(spec), [])

    def test_claude_valueless_disallowed_flag_is_backed_by_the_injected_denylist(self):
        # A trailing `--disallowed-tools` with no value after it denies nothing,
        # so enforcement reads the seat as having no deny list and injects the
        # full one ahead of it (#750).
        spec = AgentSpec(
            name="claude",
            vendor="anthropic",
            command="claude",
            extra_args=["--disallowed-tools"],
        )
        self.assertEqual(
            privilege.enforce_read_only("anthropic", list(spec.extra_args)),
            [*LOCKDOWN, "--disallowed-tools", DENY, "--disallowed-tools"],
        )
        self.assertEqual(privilege.audit_agent(spec), [])

    def test_claude_partial_disallowed_is_merged_before_spawn(self):
        # The space form of the case above: Bash/NotebookEdit are missing from
        # the config and present in the argv, which is what the audit reads.
        spec = AgentSpec(
            name="claude",
            vendor="anthropic",
            command="claude",
            extra_args=["--disallowed-tools", "Edit,Write"],
        )
        self.assertEqual(
            privilege.enforce_read_only("anthropic", list(spec.extra_args)),
            [*LOCKDOWN, "--disallowed-tools", DENY],
        )
        self.assertEqual(privilege.audit_agent(spec), [])

    def test_codex_danger_full_access_warns(self):
        spec = AgentSpec(
            name="codex",
            vendor="openai",
            command="codex",
            extra_args=["-s", "danger-full-access"],
        )
        warnings = privilege.audit_agent(spec)
        self.assertTrue(warnings)
        self.assertIn("danger-full-access", warnings[0])

    def test_agy_dangerously_skip_permissions_is_spawned_sandboxed(self):
        # The flag only skips an approval prompt; `--sandbox` is what confines
        # the agent (#100), and enforcement injects it when the config forgot
        # (#288), so there is no write capability left to warn about (#750).
        spec = AgentSpec(
            name="agy",
            vendor="google",
            command="agy",
            extra_args=["--dangerously-skip-permissions"],
        )
        self.assertEqual(
            privilege.enforce_read_only("google", list(spec.extra_args)),
            ["--sandbox", "--dangerously-skip-permissions"],
        )
        # Sandboxed, so nothing about the sandbox — only that agy is agy.
        self.assertEqual(privilege.audit_agent(spec), [agy_warning("agy")])

    def test_yolo_flag_is_spawned_sandboxed(self):
        spec = AgentSpec(name="gemini", vendor="google", command="gemini", extra_args=["--yolo"])
        self.assertEqual(
            privilege.enforce_read_only("google", list(spec.extra_args)),
            ["--sandbox", "--yolo"],
        )
        self.assertEqual(privilege.audit_agent(spec), [agy_warning("gemini")])

    def test_full_auto_flag_warns(self):
        # Still warns after #750, and deliberately: unlike `--yolo`, codex's
        # `--full-auto` SELECTS a workspace-write sandbox rather than skipping a
        # prompt, and `_ensure_value_sandbox` looks only for an `-s`/`--sandbox`
        # token — so the enforced `-s read-only` is passed *beside* it and codex,
        # not this module, decides which one wins.
        spec = AgentSpec(name="codex", vendor="openai", command="codex", extra_args=["--full-auto"])
        warnings = privilege.audit_agent(spec)
        self.assertEqual(
            privilege.enforce_read_only("openai", list(spec.extra_args)),
            ["-s", "read-only", "--full-auto"],
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("--full-auto", warnings[0])
        self.assertIn("which of the two applies is up to the CLI", warnings[0])

    def test_read_only_codex_has_no_warning(self):
        spec = AgentSpec(
            name="codex", vendor="openai", command="codex", extra_args=["-s", "read-only"]
        )
        self.assertEqual(privilege.audit_agent(spec), [])

    def test_agy_skip_permissions_with_sandbox_has_no_warning(self):
        # Issue #100: --sandbox neutralizes --dangerously-skip-permissions, so the
        # shipped agy default is not flagged.
        spec = AgentSpec(
            name="agy",
            vendor="google",
            command="agy",
            extra_args=["--dangerously-skip-permissions", "--sandbox"],
        )
        # No sandbox warning; the one that remains is that agy is not confinable.
        self.assertEqual(privilege.audit_agent(spec), [agy_warning("agy")])

    # Issue #300: an unsandboxed non-claude agent must warn even with no
    # dangerous flag (closes the audit blind spot).
    def test_unknown_vendor_unsandboxed_warns(self):
        spec = AgentSpec(name="x", vendor="acme", command="claude", extra_args=[])
        warnings = privilege.audit_agent(spec)
        self.assertTrue(warnings)
        self.assertIn("sandbox", warnings[0].lower())

    def test_codex_no_sandbox_no_dangerous_flag_is_spawned_read_only(self):
        # A codex seat that names no sandbox is spawned with `-s read-only`
        # injected (#288), so after #750 there is nothing to warn about.
        #
        # The catch-all this used to exercise is not gone, and the lesson it
        # carried is not either: `_DANGEROUS_FLAGS` was never the only path to a
        # warning (#608), and `test_an_unlisted_wide_sandbox_still_warns` below
        # still proves it — with a sandbox the operator wrote, which is the shape
        # enforcement leaves alone and the audit therefore still reaches.
        spec = AgentSpec(name="codex", vendor="openai", command="codex", extra_args=[])
        self.assertEqual(privilege.enforce_read_only("openai", []), ["-s", "read-only"])
        self.assertEqual(privilege.audit_agent(spec), [])

    def test_an_unlisted_wide_sandbox_still_warns(self):
        """The generalisation: no `_DANGEROUS_FLAGS` entry is needed to warn.

        A value sandbox that restricts nothing and appears on no list is the
        exact shape #600 believed was a bypass. One warning, via the catch-all.

        Enforcement cannot rescue this one and does not try (#750): a sandbox the
        operator named is respected as written, so the argv codex is spawned with
        is the argv the config asked for, restricting nothing.
        """
        spec = AgentSpec(
            name="codex",
            vendor="openai",
            command="codex",
            extra_args=["-s", "some-future-mode-nobody-listed"],
        )
        warnings = privilege.audit_agent(spec)
        self.assertEqual(
            privilege.enforce_read_only("openai", list(spec.extra_args)),
            list(spec.extra_args),
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("not running under a recognized read-only sandbox", warnings[0])

    def test_local_vendor_is_not_audited(self):
        # A local/HTTP agent runs no subprocess to sandbox — out of scope.
        spec = AgentSpec(
            name="qwen",
            vendor="local",
            command="",
            model="m",
            endpoint="http://localhost:11434/v1",
        )
        self.assertEqual(privilege.audit_agent(spec), [])


class AuditPrivilegeTest(unittest.TestCase):
    def test_dangerous_config_produces_warnings(self):
        specs = [
            # A sandbox the operator widened on purpose: kept as written.
            AgentSpec(
                name="codex",
                vendor="openai",
                command="codex",
                extra_args=["-s", "danger-full-access"],
            ),
            # A second sandbox selected beside the enforced one.
            AgentSpec(
                name="codex-auto",
                vendor="openai",
                command="codex",
                extra_args=["--full-auto"],
            ),
            # A bring-your-own CLI, for which nothing is enforced at all.
            AgentSpec(name="cursor", vendor="cli", command="cursor-agent", extra_args=["-p"]),
        ]
        warnings = privilege.audit_privilege(specs)
        # One warning per dangerous agent.
        self.assertEqual(len(warnings), 3)

    def test_a_config_whose_gaps_the_adapter_closes_produces_no_warnings(self):
        """Issue #750, at the surface `run_jury` and `--strict` actually call.

        Every seat here declares a gap and every gap is closed at spawn time, so
        the three warnings this used to raise were three false alarms on configs
        that cannot write. `--strict` failed all three.
        """
        specs = [
            AgentSpec(name="claude", vendor="anthropic", command="claude", extra_args=[]),
            AgentSpec(name="codex", vendor="openai", command="codex", extra_args=[]),
            AgentSpec(
                name="agy",
                vendor="google",
                command="agy",
                extra_args=["--dangerously-skip-permissions"],
            ),
        ]
        # Every sandbox gap is closed; agy's own warning is not about a gap.
        self.assertEqual(privilege.audit_privilege(specs), [agy_warning("agy")])

    def test_codex_workspace_write_produces_dangerous_flag_warning(self):
        spec = AgentSpec(
            name="codex",
            vendor="openai",
            command="codex",
            extra_args=["-s", "workspace-write"],
        )
        warnings = privilege.audit_agent(spec)
        self.assertEqual(len(warnings), 1)
        self.assertIn("workspace-write", warnings[0])
        self.assertIn("granting write/tool/network powers", warnings[0])

    def test_locked_down_config_has_no_warnings(self):
        specs = [
            AgentSpec(
                name="claude",
                vendor="anthropic",
                command="claude",
                extra_args=["--disallowed-tools", "Edit,Write,NotebookEdit,Bash"],
            ),
            AgentSpec(
                name="codex",
                vendor="openai",
                command="codex",
                extra_args=["-s", "read-only"],
            ),
        ]
        self.assertEqual(privilege.audit_privilege(specs), [])

    def test_empty_specs_has_no_warnings(self):
        self.assertEqual(privilege.audit_privilege([]), [])

    def test_shipped_default_config_has_no_warnings(self):
        # Issue #100: the out-of-the-box defaults must be secure (read-only codex,
        # sandboxed agy, locked-down claude) — no least-privilege warnings.
        from ai_jury.config import DEFAULT_CONFIG, _from_dict

        cfg = _from_dict(DEFAULT_CONFIG)
        self.assertEqual(privilege.audit_privilege(cfg.enabled_agents), [])


class IsSandboxedVendorAwareTest(unittest.TestCase):
    """Issue #292: a bare --sandbox token must not give false assurance."""

    def test_bare_sandbox_with_dangerous_flags_not_trusted_for_non_agy(self):
        # The F-5 example: --sandbox followed by broad-powers flags on a vendor
        # whose --sandbox is NOT a boolean restricting sandbox is no longer
        # accepted, so the dangerous flags are surfaced.
        spec = AgentSpec(
            name="custom",
            vendor="openai",
            command="x",
            extra_args=["--sandbox", "--dangerously-skip-permissions", "--yolo"],
        )
        warnings = privilege.audit_agent(spec)
        self.assertTrue(warnings)

    def test_codex_wide_value_sandbox_is_not_sandboxed(self):
        # --sandbox workspace-write takes a non-restricting value -> not a sandbox.
        self.assertFalse(privilege._is_sandboxed(["--sandbox", "workspace-write"], vendor="openai"))

    def test_agy_bare_sandbox_still_trusted(self):
        # The shipped agy default must keep passing (issue #100 not regressed).
        self.assertTrue(
            privilege._is_sandboxed(
                ["--dangerously-skip-permissions", "--sandbox"], vendor="google"
            )
        )

    def test_codex_read_only_value_is_sandboxed(self):
        self.assertTrue(privilege._is_sandboxed(["-s", "read-only"], vendor="openai"))

    def test_equals_form_sandbox_recognized(self):
        # Issue #316/L-6: the audit must recognize the =-form the enforcement
        # already accepts, or it false-positives a safe config under --strict.
        self.assertTrue(privilege._is_sandboxed(["--sandbox=read-only"], vendor="openai"))
        self.assertTrue(privilege._is_sandboxed(["-s=read-only"], vendor="openai"))
        self.assertFalse(privilege._is_sandboxed(["--sandbox=workspace-write"], vendor="openai"))

    def test_codex_equals_read_only_has_no_audit_warning(self):
        spec = AgentSpec(
            name="codex",
            vendor="openai",
            command="codex",
            extra_args=["--sandbox=read-only"],
        )
        self.assertEqual(privilege.audit_agent(spec), [])


class EnforceReadOnlyTest(unittest.TestCase):
    """Issue #288: the sandbox is guaranteed at the adapter layer, not config."""

    def test_claude_injects_disallowed_tools_when_absent(self):
        out = privilege.enforce_read_only("anthropic", [])
        self.assertEqual(out, [*LOCKDOWN, "--disallowed-tools", DENY])

    def test_claude_merges_missing_write_tools_into_existing(self):
        out = privilege.enforce_read_only("anthropic", ["--disallowed-tools", "Edit,Write"])
        self.assertEqual(out, [*LOCKDOWN, "--disallowed-tools", DENY])

    def test_claude_shipped_default_is_unchanged(self):
        shipped = next(a for a in DEFAULT_CONFIG["agent"] if a["name"] == "claude")["extra_args"]
        self.assertEqual(privilege.enforce_read_only("anthropic", list(shipped)), shipped)

    def test_claude_equals_form_disallowed_is_merged(self):
        # Review of #288: the =-form must be merged too, not left to sit after the
        # injected safe set where a last-wins CLI could narrow the deny set.
        out = privilege.enforce_read_only("anthropic", ["--disallowed-tools=Edit"])
        self.assertEqual(out, [*LOCKDOWN, "--disallowed-tools=" + DENY])

    def test_codex_injects_read_only_when_no_sandbox(self):
        out = privilege.enforce_read_only("openai", [])
        self.assertEqual(out, ["-s", "read-only"])

    def test_codex_equals_form_sandbox_is_respected_not_doubled(self):
        out = privilege.enforce_read_only("openai", ["--sandbox=read-only"])
        self.assertEqual(out, ["--sandbox=read-only"])

    def test_codex_respects_operator_widened_sandbox(self):
        # An explicit (audited) opt-in is preserved, never overridden.
        out = privilege.enforce_read_only("openai", ["-s", "workspace-write"])
        self.assertEqual(out, ["-s", "workspace-write"])

    def test_agy_injects_sandbox_when_absent(self):
        out = privilege.enforce_read_only("google", ["--dangerously-skip-permissions"])
        self.assertEqual(out, ["--sandbox", "--dangerously-skip-permissions"])

    def test_unknown_vendor_gets_sandbox(self):
        # Issue #310 (completes #300): an unknown vendor routes to the generic
        # AgyAdapter, so --sandbox is injected — fail-closed, never fail-open.
        out = privilege.enforce_read_only("weirdvendor", ["--foo"])
        self.assertEqual(out, ["--sandbox", "--foo"])

    def test_unknown_vendor_existing_sandbox_not_doubled(self):
        out = privilege.enforce_read_only("weirdvendor", ["--sandbox"])
        self.assertEqual(out, ["--sandbox"])

    def test_the_seat_name_is_not_an_argument_at_all(self):
        """What #758 made true, #768 makes structural.

        The review of #310 asked what an agent NAMED `local-claude` or
        `my-codex` gets spawned with, and the answer used to depend on branch
        order inside `enforce_read_only`: name-substring tests sat below the
        no-sandbox fast path and would otherwise have read `claude` out of
        `local-claude`. #758 deleted those tests and left the `name` parameter
        standing with nothing reading its value — a dead argument still in the
        shape of the old rule, and an invitation to reach for it again. It is
        gone from both signatures now, so a name has nowhere to enter: the
        adapter key and the args are the whole input.
        """
        for fn in (privilege.enforce_read_only, privilege.enable_write):
            with self.subTest(fn=fn.__name__):
                self.assertEqual(list(inspect.signature(fn).parameters), ["vendor", "extra_args"])

    def test_local_vendor_is_left_untouched(self):
        self.assertEqual(privilege.enforce_read_only("local", []), [])

    def test_cli_vendor_is_left_untouched(self):
        self.assertEqual(privilege.enforce_read_only("cli", ["--print"]), ["--print"])


class ASandboxIsNotSettledByTheFirstOneNamed(unittest.TestCase):
    """Issue #750, second round: every sandbox selector in the argv, not the first.

    Enforcement injects `-s read-only` only when no sandbox token exists, and
    `_is_sandboxed` returns on the first restricting value it sees. Both are right
    for what they do and wrong for an audit: a second selector rides along in the
    same argv, and codex's own argument precedence — not this module — decides
    which one the CLI honours.
    """

    def _codex(self, extra):
        return AgentSpec(name="codex", vendor="openai", command="codex", extra_args=list(extra))

    def test_a_second_sandbox_value_beside_the_enforced_one_is_named(self):
        spec = self._codex(["-s", "read-only", "-s", "workspace-write"])

        # Enforcement leaves this argv alone: a sandbox token is already present.
        self.assertEqual(
            adapters._read_only_extra_args(spec), ["-s", "read-only", "-s", "workspace-write"]
        )
        warnings = privilege.audit_agent(spec)

        self.assertTrue(warnings)
        self.assertIn("-s workspace-write", warnings[0])

    def test_codex_yolo_is_a_sandbox_bypass_not_an_approval_skip(self):
        """`--yolo` means different things to different CLIs.

        codex documents it as the alias of
        `--dangerously-bypass-approvals-and-sandbox`, which leaves no sandbox at
        all. Read with agy's dictionary it looks like a prompt-skipping flag the
        sandbox still confines, and the audit said nothing about the most
        dangerous flag on the codex path.
        """
        for flag in ("--yolo", "--dangerously-bypass-approvals-and-sandbox"):
            with self.subTest(flag=flag):
                warnings = privilege.audit_agent(self._codex([flag]))

                self.assertTrue(warnings, f"{flag} audited clean")
                self.assertIn(flag, warnings[0])

    def test_the_same_flag_on_agy_stays_clean(self):
        """agy's `--yolo` only skips approvals; its sandbox still holds."""
        spec = AgentSpec(name="agy", vendor="google", command="agy", extra_args=["--yolo"])

        self.assertEqual(privilege.audit_agent(spec), [agy_warning("agy")])

    def test_agys_boolean_sandbox_selects_nothing(self):
        """A bare `--sandbox`, or one followed by another flag, names no value."""
        for extra in (["--sandbox"], ["--sandbox", "--yolo"]):
            with self.subTest(extra=extra):
                spec = AgentSpec(name="agy", vendor="google", command="agy", extra_args=extra)

                self.assertEqual(privilege.audit_agent(spec), [agy_warning("agy")])

    def test_the_equals_spelling_of_a_selector_is_matched_too(self):
        """`-s=` (#316) and `--disallowed-tools=` (#717) are already first-class here."""
        for flag in ("--full-auto", "--yolo", "--dangerously-bypass-approvals-and-sandbox"):
            with self.subTest(flag=flag):
                warnings = privilege.audit_agent(self._codex([f"{flag}=true"]))

                self.assertTrue(warnings, f"{flag}=true audited clean")
                self.assertIn(f"{flag}=true", warnings[0])

    def test_a_name_does_not_decide_which_cli_is_being_spawned(self):
        """The adapter key decides, which is what `enforce_read_only` documents.

        `name` is free text an operator picks to tell two seats apart in a
        report; `adapter`/`vendor` is the declared fact about what will run.
        While the name could override it, three configurations were spawned with
        another CLI's flags and audited clean: a seat named `claude` with an
        unknown vendor got Claude's denylist and no sandbox, one named `codex`
        got codex's `-s read-only` passed to an unknown binary, and a real codex
        seat named `claude-4` got the denylist instead of a sandbox.
        """
        unknown_claude = AgentSpec(name="claude", vendor="acme", command="x")
        unknown_codex = AgentSpec(
            name="codex", vendor="acme", command="x", extra_args=["--full-auto"]
        )
        codex_named_claude = AgentSpec(name="claude-4", vendor="openai", command="codex")

        # An unknown vendor gets agy's boolean sandbox injected, fail-closed, and
        # the audit does not accept that as proof (#292) whatever the name says.
        self.assertEqual(adapters._read_only_extra_args(unknown_claude), ["--sandbox"])
        self.assertTrue(privilege.audit_agent(unknown_claude))
        self.assertTrue(privilege.audit_agent(unknown_codex))
        # And a codex seat is a codex seat however it is named.
        self.assertEqual(adapters._read_only_extra_args(codex_named_claude), ["-s", "read-only"])
        self.assertEqual(privilege.audit_agent(codex_named_claude), [])

    def test_a_google_seat_is_not_read_with_codex_s_dictionary(self):
        """Same argv, same verdict, whatever the seat is called.

        `--yolo` only skips approval prompts on agy and its sandbox still holds;
        on codex it is the alias of `--dangerously-bypass-approvals-and-sandbox`.
        While `_is_codex` consulted the name, a google seat named
        `codex-vs-gemini` failed `--strict` on the identical argv a seat named
        `agy` passed with.
        """
        argvs, verdicts = set(), set()
        for name in ("codex-vs-gemini", "agy"):
            spec = AgentSpec(name=name, vendor="google", command="agy", extra_args=["--yolo"])
            argvs.add(tuple(adapters._read_only_extra_args(spec)))
            # The label differs by construction; the verdict must not.
            verdicts.add(
                tuple(w.replace(f"'{name}'", "'<seat>'") for w in privilege.audit_agent(spec))
            )

        self.assertEqual(len(argvs), 1)
        self.assertEqual(verdicts, {(agy_warning("<seat>"),)})

    def test_a_seat_carrying_both_kinds_is_described_by_the_worse_one(self):
        """`all(...)` picked the milder sentence when a seat had a selector too."""
        warning = privilege.audit_agent(self._codex(["--full-auto", "--yolo"]))[0]

        self.assertIn("disables the sandbox entirely", warning)
        self.assertNotIn("state a sandbox of their own", warning)

    def test_a_bypass_is_not_described_as_a_second_sandbox(self):
        """`--full-auto` picks a sandbox; `--yolo` removes one. Say which."""
        selects = privilege.audit_agent(self._codex(["--full-auto"]))[0]
        disables = privilege.audit_agent(self._codex(["--yolo"]))[0]

        self.assertIn("selects a sandbox of its own", selects)
        self.assertIn("which of the two applies", selects)
        self.assertIn("disables the sandbox entirely", disables)
        self.assertIn("may not apply at all", disables)

    def test_a_seat_with_only_the_enforced_sandbox_is_still_clean(self):
        self.assertEqual(privilege.audit_agent(self._codex([])), [])


class AClaudeReviewerHasNoToolsAtAll(unittest.TestCase):
    """The reviewer reads its prompt and nothing else: no file, no network, no MCP.

    Denying only Edit/Write/NotebookEdit/Bash left every other tool auto-approved
    under `--dangerously-skip-permissions`, so an instruction planted in the diff
    could `Read` the repository's `.env` and `WebFetch` it to a server of its
    choosing.
    """

    OLD_DEFAULT = [
        "--output-format",
        "text",
        "--disallowed-tools",
        "Edit,Write,NotebookEdit,Bash",
        "--dangerously-skip-permissions",
    ]

    def _seat(self, *extra_args):
        return AgentSpec(
            name="claude", vendor="anthropic", command="claude", extra_args=list(extra_args)
        )

    def test_the_shipped_default_is_the_no_tool_lockdown(self):
        shipped = next(a for a in DEFAULT_CONFIG["agent"] if a["name"] == "claude")["extra_args"]
        self.assertEqual(
            shipped,
            [
                "--output-format",
                "text",
                "--tools",
                "",
                "--disallowed-tools",
                DENY,
                "--strict-mcp-config",
                "--safe-mode",
                "--no-session-persistence",
                "--permission-mode",
                "dontAsk",
            ],
        )
        self.assertNotIn("--dangerously-skip-permissions", shipped)
        self.assertNotIn("--mcp-config", " ".join(shipped))

    def test_the_deny_list_names_every_read_network_and_subagent_tool(self):
        self.assertEqual(DENY, ",".join(privilege._CLAUDE_DENIED_TOOLS))
        for tool in ("Read", "Grep", "Glob", "WebFetch", "WebSearch"):
            self.assertIn(tool, privilege._CLAUDE_DENIED_TOOLS)
        for tool in ("Task", "Agent", *privilege._WRITE_TOOLS):
            self.assertIn(tool, privilege._CLAUDE_DENIED_TOOLS)

    def test_the_deny_list_names_no_tool_the_cli_has_dropped(self):
        # Claude Code 2.1.236 warns on stderr about a deny rule for a tool it no
        # longer ships, on every run, and `classify_stderr` then reads any other
        # failure of the seat as `permission_prompt`.
        for dropped in ("LS", "NotebookRead", "MultiEdit", "SlashCommand"):
            self.assertNotIn(dropped, privilege._CLAUDE_DENIED_TOOLS)

    def test_the_old_default_is_spawned_without_read_or_network_tools(self):
        # A jury.toml that copied the pre-fix default keeps working, and gets the
        # lockdown: config can add restrictions, never remove them.
        argv = privilege.enforce_read_only("anthropic", list(self.OLD_DEFAULT))
        self.assertEqual(argv[: len(LOCKDOWN)], LOCKDOWN)
        self.assertEqual(argv[argv.index("--disallowed-tools") + 1], DENY)
        self.assertEqual(privilege._claude_tools(argv), [])
        # Its bypass flag is kept beside the injected dontAsk, and reported once —
        # as what it is: it wins over dontAsk (measured), with nothing to approve.
        warnings = privilege.audit_agent(self._seat(*self.OLD_DEFAULT))
        self.assertEqual(len(warnings), 1)
        self.assertIn("`--dangerously-skip-permissions`", warnings[0])
        # No mode was named, so dontAsk was injected beside it, and that is the one named.
        self.assertIn("overrides the `--permission-mode dontAsk` beside it", warnings[0])
        self.assertIn("runs in bypass mode", warnings[0])
        self.assertIn("grants nothing today", warnings[0])

    def test_a_deny_list_of_write_tools_alone_is_not_locked_down(self):
        self.assertFalse(
            privilege._claude_is_locked_down(["--disallowed-tools", "Edit,Write,NotebookEdit,Bash"])
        )
        self.assertTrue(privilege._claude_is_locked_down(["--disallowed-tools", DENY]))

    def test_re_enabled_read_and_network_tools_warn_when_permissions_are_skipped(self):
        warnings = privilege.audit_agent(
            self._seat("--tools", "Read,WebFetch", "--dangerously-skip-permissions")
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("`--tools Read,WebFetch`", warnings[0])
        self.assertIn("`--dangerously-skip-permissions`", warnings[0])
        self.assertIn("reach the network", warnings[0])

    def test_every_approving_permission_mode_counts_as_skipping(self):
        for bypass in (
            ["--permission-mode", "bypassPermissions"],
            ["--permission-mode=bypassPermissions"],
            ["--permission-mode", "auto"],
        ):
            with self.subTest(bypass):
                warnings = privilege.audit_agent(self._seat("--tools", "WebSearch", *bypass))
                self.assertEqual(len(warnings), 1)
                self.assertIn("skips permission checks", warnings[0])

    def test_re_enabled_tools_warn_without_a_bypass_too(self):
        # `dontAsk` denies what is not pre-approved — but the user's own Claude
        # settings can pre-approve, so a reviewer given a tool is still flagged.
        warnings = privilege.audit_agent(
            self._seat("--tools", "Read", "--permission-mode", "dontAsk")
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("`--tools Read`", warnings[0])
        self.assertIn("pre-approve", warnings[0])

    def test_the_default_tool_set_warns(self):
        warnings = privilege.audit_agent(self._seat("--tools", "default"))
        self.assertEqual(len(warnings), 1)
        self.assertIn("`--tools default`", warnings[0])

    def test_mcp_servers_the_operator_loads_warn(self):
        for spelling in (["--mcp-config", "servers.json"], ["--mcp-config=servers.json"]):
            with self.subTest(spelling):
                warnings = privilege.audit_agent(self._seat(*spelling))
                self.assertEqual(len(warnings), 1)
                self.assertIn("MCP servers from `--mcp-config`", warnings[0])

    def test_a_variadic_tools_list_is_read_whole_and_kept(self):
        args = ["--tools", "Read", "Grep", "--output-format", "text"]
        self.assertEqual(privilege._claude_tools(args), ["Read", "Grep"])
        argv = privilege.enforce_read_only("anthropic", list(args))
        self.assertEqual(argv.count("--tools"), 1, "a configured --tools must not be doubled")
        self.assertIn("`--tools Read,Grep`", privilege.audit_agent(self._seat(*args))[0])

    def test_repeated_tools_flags_accumulate(self):
        self.assertEqual(
            privilege._claude_tools(["--tools=Read", "--tools", "WebFetch Glob"]),
            ["Read", "WebFetch", "Glob"],
        )

    def test_an_empty_tools_value_in_either_spelling_is_no_tools(self):
        for args in (["--tools", ""], ["--tools="]):
            with self.subTest(args):
                self.assertEqual(privilege._claude_tools(args), [])
                argv = privilege.enforce_read_only("anthropic", list(args))
                self.assertEqual(argv.count("--tools") + argv.count("--tools="), 1)
                self.assertEqual(privilege.audit_agent(self._seat(*args)), [])

    def test_a_configured_strict_mcp_config_is_not_doubled(self):
        argv = privilege.enforce_read_only("anthropic", ["--strict-mcp-config"])
        self.assertEqual(argv.count("--strict-mcp-config"), 1)
        self.assertEqual(argv[:2], ["--tools", ""])

    def test_a_valueless_permission_mode_is_not_a_bypass(self):
        self.assertIsNone(privilege._claude_permission_bypass(["--permission-mode"]))
        self.assertIsNone(privilege._claude_permission_bypass(["--permission-mode", "dontAsk"]))


class AFlagSpelledAsAnotherOptionsValueIsNotThatFlag(unittest.TestCase):
    """claude's parser hands a value-taking option the next token, dashes and all.

    In `--append-system-prompt --tools`, `--tools` is prompt text. The presence
    checks were token-based, so a configured value that spelled a lockdown flag
    stopped the real flag from being injected.
    """

    def test_each_lockdown_flag_is_injected_despite_a_value_that_spells_it(self):
        for flag in ("--tools", "--strict-mcp-config", "--safe-mode", "--no-session-persistence"):
            with self.subTest(flag):
                args = ["--append-system-prompt", flag]
                argv = privilege.enforce_read_only("anthropic", list(args))
                self.assertEqual(argv[: len(LOCKDOWN)], LOCKDOWN)
                self.assertEqual(argv[-2:], args, "the configured value was altered")

    def test_a_value_that_spells_the_deny_flag_neither_hides_nor_gets_rewritten(self):
        args = ["--system-prompt", "--disallowed-tools", "--output-format", "text"]
        argv = privilege.enforce_read_only("anthropic", list(args))
        self.assertEqual(argv[: len(LOCKDOWN)], LOCKDOWN)
        self.assertEqual(argv[len(LOCKDOWN) : len(LOCKDOWN) + 2], ["--disallowed-tools", DENY])
        self.assertEqual(argv[-4:], args)
        self.assertTrue(privilege._claude_is_locked_down(argv))

    def test_a_value_that_spells_a_bypass_or_mcp_config_is_not_one(self):
        args = ["--tools", "Read", "--append-system-prompt", "--dangerously-skip-permissions"]
        warnings = privilege.audit_agent(
            AgentSpec(name="c", vendor="anthropic", command="claude", extra_args=args)
        )
        # Not read as the flag (no "skips permission checks"), but reported
        # fail-closed as a token jury cannot vouch for (#908 review).
        self.assertEqual(len(warnings), 2)
        self.assertNotIn("skips permission checks", warnings[0])
        self.assertIn(
            "item 4 of `extra_args` (it mentions `dangerously-skip-permissions`)", warnings[1]
        )
        self.assertIn("jury cannot tell", warnings[1])
        self.assertFalse(privilege._claude_flag_present("--mcp-config", ["--name", "--mcp-config"]))

    def test_a_variadic_option_takes_its_first_value_whatever_it_spells(self):
        self.assertEqual(privilege._claude_tools(["--tools", "--safe-mode"]), ["--safe-mode"])
        self.assertEqual(
            privilege._claude_value_positions(["--add-dir", "a", "b", "--safe-mode"]),
            frozenset({1, 2}),
        )

    def test_the_write_role_leaves_a_value_that_spells_a_lockdown_flag_alone(self):
        args = ["--append-system-prompt", "--safe-mode", "--safe-mode"]
        self.assertEqual(
            privilege.enable_write("anthropic", args), ["--append-system-prompt", "--safe-mode"]
        )


class EveryReadOnlyClaudeCallRunsInDontAsk(unittest.TestCase):
    """The reviewer's permission mode is enforced, not only shipped.

    The docs said every read-only claude call runs in `--permission-mode
    dontAsk`; only the shipped default carried it, and a seat configured without
    it was spawned in the CLI's default mode.
    """

    def _seat(self, *extra_args):
        return AgentSpec(
            name="claude", vendor="anthropic", command="claude", extra_args=list(extra_args)
        )

    def test_it_is_injected_when_no_mode_is_named(self):
        for args in ([], ["--output-format", "text"], ["--dangerously-skip-permissions"]):
            with self.subTest(args):
                argv = privilege.enforce_read_only("anthropic", list(args))
                self.assertIn("--permission-mode", argv)
                i = argv.index("--permission-mode")
                self.assertEqual(argv[i + 1], "dontAsk")
                self.assertEqual(argv.count("--permission-mode"), 1)

    def test_a_value_that_spells_the_flag_does_not_stop_the_injection(self):
        argv = privilege.enforce_read_only(
            "anthropic", ["--append-system-prompt", "--permission-mode"]
        )
        self.assertEqual(argv[: len(LOCKDOWN)], LOCKDOWN)
        self.assertEqual(argv[-2:], ["--append-system-prompt", "--permission-mode"])

    #: What the warning must say for each mode — what that mode really does.
    #: `default` is missing from the choices Claude Code 2.1.236 prints but is
    #: accepted as an alias of `manual` (measured), so it says what `manual` says.
    MODE_SAYS = {
        "bypassPermissions": ("approves every tool call without asking", "grants nothing today"),
        "auto": ("approve tool calls without asking you", "grants nothing today"),
        "acceptEdits": ("approves file edits without asking", "grants nothing today"),
        "manual": ("is used instead of the reviewer's `dontAsk`", "changes nothing today"),
        "plan": ("is used instead of the reviewer's `dontAsk`", "changes nothing today"),
        # Not in the list Claude Code prints, but accepted as an alias of manual.
        "default": ("is used instead of the reviewer's `dontAsk`", "changes nothing today"),
    }

    def test_a_configured_mode_is_kept_and_reported_once_as_what_it_does(self):
        for mode, says in self.MODE_SAYS.items():
            for spelling in (["--permission-mode", mode], [f"--permission-mode={mode}"]):
                with self.subTest(spelling):
                    argv = privilege.enforce_read_only("anthropic", list(spelling))
                    self.assertNotIn("dontAsk", argv, "a named mode was overridden")
                    self.assertEqual(argv[-len(spelling) :], spelling)
                    warnings = privilege.audit_agent(self._seat(*spelling))
                    self.assertEqual(len(warnings), 1)
                    self.assertIn(f"`--permission-mode {mode}`", warnings[0])
                    for phrase in says:
                        self.assertIn(phrase, warnings[0])
                    self.assertNotIn("a reviewer runs with `--permission-mode", warnings[0])

    def test_a_mode_claude_code_rejects_is_named_as_rejected(self):
        # A value outside the accepted set, an empty value and no value at all.
        for spelling in (
            ["--permission-mode", "bogus"],
            ["--permission-mode="],
            ["--permission-mode"],
        ):
            with self.subTest(spelling):
                warnings = privilege.audit_agent(self._seat(*spelling))
                self.assertEqual(len(warnings), 1)
                self.assertIn("Claude Code 2.1.236 rejects", warnings[0])
                self.assertIn("fails before it reviews anything", warnings[0])

    def test_the_bypass_flag_beside_a_named_mode_does_not_claim_dont_ask_was_injected(self):
        for mode in ("plan", "auto"):
            with self.subTest(mode):
                args = ["--permission-mode", mode, "--dangerously-skip-permissions"]
                self.assertNotIn("dontAsk", privilege.enforce_read_only("anthropic", list(args)))
                warnings = privilege.audit_agent(self._seat(*args))
                self.assertEqual(len(warnings), 1)
                # It names the mode that is actually there, and no dontAsk.
                self.assertIn(f"overrides the `--permission-mode {mode}` beside it", warnings[0])
                self.assertNotIn("dontAsk", warnings[0].split("which", 1)[1].split("(")[0])
                self.assertNotIn("injected", warnings[0])

    def test_a_rejected_mode_beside_the_bypass_flag_is_reported_as_rejected(self):
        # Claude Code 2.1.236 refuses to start on the mode whatever else is there
        # (measured for skip + bogus and skip + a missing value), so the seat fails:
        # it does not run in bypass mode, and the warning must not say it does.
        cases = {
            "bogus": (
                ["--dangerously-skip-permissions", "--permission-mode", "bogus"],
                "`--permission-mode <value>`",
            ),
            "missing": (
                ["--dangerously-skip-permissions", "--permission-mode"],
                "`--permission-mode` and no value",
            ),
            "empty": (
                ["--permission-mode=", "--dangerously-skip-permissions"],
                "an empty `--permission-mode=`",
            ),
        }
        for name, (args, shown) in cases.items():
            with self.subTest(name):
                warnings = privilege.audit_agent(self._seat(*args))
                self.assertEqual(len(warnings), 1)
                self.assertIn(shown, warnings[0])
                self.assertIn("Claude Code 2.1.236 rejects", warnings[0])
                self.assertIn("fails before it reviews anything", warnings[0])
                self.assertNotIn("bypass mode", warnings[0])
                self.assertNotIn("`--permission-mode `", warnings[0])

    #: Each refused by Claude Code 2.1.236 (probed with an invalid model name, so
    #: no model call): the seat never starts, so nothing runs in bypass mode.
    REJECTED_WITH_SKIP = {
        "bogus": (
            ["--dangerously-skip-permissions", "--permission-mode", "bogus"],
            "`--permission-mode <value>`",
        ),
        "missing": (
            ["--dangerously-skip-permissions", "--permission-mode"],
            "`--permission-mode` and no value",
        ),
        "empty": (
            ["--dangerously-skip-permissions", "--permission-mode="],
            "an empty `--permission-mode=`",
        ),
        "value is the skip flag": (
            ["--permission-mode", "--dangerously-skip-permissions"],
            "`--permission-mode <value>`",
        ),
        "= value is the skip flag": (
            ["--permission-mode=--dangerously-skip-permissions"],
            "`--permission-mode <value>`",
        ),
    }

    def test_a_rejected_mode_wins_with_or_without_tools(self):
        for name, (args, shown) in self.REJECTED_WITH_SKIP.items():
            for tools in ([], ["--tools", "Read"]):
                with self.subTest(name, tools=bool(tools)):
                    warnings = privilege.audit_agent(self._seat(*tools, *args))
                    joined = " | ".join(warnings)
                    self.assertIn(shown, joined)
                    self.assertIn("Claude Code 2.1.236 rejects", joined)
                    self.assertNotIn("bypass mode", joined)
                    self.assertNotIn("skips permission checks", joined)
                    self.assertEqual(len(warnings), 2 if tools else 1)
                    self.assertIsNone(
                        privilege._claude_permission_bypass(
                            privilege.enforce_read_only("anthropic", [*tools, *args])
                        )
                    )

    def test_a_mode_beside_configured_tools_says_it_applies_to_them(self):
        warnings = privilege.audit_agent(
            self._seat("--tools", "Read", "--permission-mode", "acceptEdits")
        )
        self.assertEqual(len(warnings), 2)
        self.assertIn("so this applies to them", warnings[1])
        self.assertNotIn("grants nothing today", warnings[1])

    def test_dont_ask_named_explicitly_is_clean(self):
        self.assertEqual(privilege.audit_agent(self._seat("--permission-mode", "dontAsk")), [])

    def test_strict_refuses_a_seat_with_another_mode(self):
        from ai_jury.orchestrator import run_jury

        config = _from_dict_with_claude(["--permission-mode", "bypassPermissions"])
        with self.assertRaises(RuntimeError) as ctx:
            run_jury(config, "diff --git a/x b/x\n", strict=True, seed=1)
        self.assertIn("least-privilege check failed (--strict)", str(ctx.exception))
        self.assertIn("--permission-mode bypassPermissions", str(ctx.exception))

    def test_the_write_role_is_unchanged(self):
        shipped = next(a for a in DEFAULT_CONFIG["agent"] if a["name"] == "claude")["extra_args"]
        self.assertEqual(
            privilege.enable_write("anthropic", list(shipped)),
            ["--output-format", "text", "--dangerously-skip-permissions"],
        )
        self.assertEqual(privilege.enable_write("anthropic", []), [])


class ConfigurationBeyondThePromptIsReported(unittest.TestCase):
    """`--settings`, `--setting-sources`, plugins, `--add-dir` and agents.

    Measured on Claude Code 2.1.236: under `--safe-mode` a `--settings` file's
    hooks did not run, `--setting-sources project` in a checkout loaded neither
    its hooks nor its CLAUDE.md, and an `--add-dir` CLAUDE.md was not loaded —
    so they are kept, but an operator is told they have no effect on a reviewer.
    """

    CASES = {
        "--settings": ["--settings", "/etc/claude.json"],
        "--setting-sources": ["--setting-sources", "project"],
        "--plugin-dir": ["--plugin-dir", "/opt/plugin"],
        "--plugin-url": ["--plugin-url", "https://example.invalid/p.zip"],
        "--add-dir": ["--add-dir", "/opt/extra"],
        "--agents": ["--agents", '{"r": {"description": "d", "prompt": "p"}}'],
        "--agent": ["--agent", "reviewer"],
    }

    def test_each_is_kept_and_reported(self):
        for flag, args in self.CASES.items():
            with self.subTest(flag):
                argv = privilege.enforce_read_only("anthropic", list(args))
                self.assertEqual(argv[-len(args) :], args)
                self.assertIn("--safe-mode", argv)
                spec = AgentSpec(name="c", vendor="anthropic", command="claude", extra_args=args)
                warnings = privilege.audit_agent(spec)
                self.assertEqual(len(warnings), 1)
                self.assertIn(f"`{flag}`", warnings[0])
                self.assertIn("`--safe-mode` keeps them from loading", warnings[0])

    def test_equals_spelling_counts_and_a_value_that_spells_one_does_not(self):
        spec = AgentSpec(
            name="c", vendor="anthropic", command="claude", extra_args=["--settings=/x.json"]
        )
        self.assertEqual(len(privilege.audit_agent(spec)), 1)
        spec = AgentSpec(
            name="c",
            vendor="anthropic",
            command="claude",
            extra_args=["--append-system-prompt", "--settings"],
        )
        self.assertEqual(privilege.audit_agent(spec), [])

    def test_the_shipped_default_names_none(self):
        shipped = next(a for a in DEFAULT_CONFIG["agent"] if a["name"] == "claude")["extra_args"]
        self.assertEqual(privilege._claude_config_options(list(shipped)), [])


class TheClaudeWriteRoleLiftsTheLockdown(unittest.TestCase):
    """`jury run-agent --role implement --allow-write` (#661) gets its tools back."""

    def test_the_shipped_default_becomes_the_implementer_it_always_was(self):
        shipped = next(a for a in DEFAULT_CONFIG["agent"] if a["name"] == "claude")["extra_args"]
        self.assertEqual(
            privilege.enable_write("anthropic", list(shipped)),
            ["--output-format", "text", "--dangerously-skip-permissions"],
        )

    def test_the_injected_lockdown_is_removed_whole(self):
        # Every lockdown flag goes; the reviewer's dontAsk becomes the bypass an
        # implementer needs. (The write role is built from the configured
        # extra_args, never from this enforced argv — this only pins the mapping.)
        locked = privilege.enforce_read_only("anthropic", [])
        self.assertEqual(
            privilege.enable_write("anthropic", locked), ["--dangerously-skip-permissions"]
        )

    def test_the_equals_spellings_are_lifted_and_a_bypass_is_not_doubled(self):
        args = ["--tools=", "--permission-mode=dontAsk", "--dangerously-skip-permissions", "-x"]
        self.assertEqual(
            privilege.enable_write("anthropic", args), ["--dangerously-skip-permissions", "-x"]
        )

    def test_another_permission_mode_is_the_operators_and_is_kept(self):
        args = ["--permission-mode", "acceptEdits"]
        self.assertEqual(privilege.enable_write("anthropic", list(args)), args)


class TheAuditReadsTheArgvTheSeatIsSpawnedWith(unittest.TestCase):
    """Issue #750: the audit's subject is the effective argv, not the config.

    Every seat on the panel path is spawned through
    `adapters._read_only_extra_args`, so the declared `extra_args` are half the
    command line. Reading only that half made the audit answer a question nobody
    asked — "what did the operator type" — instead of the one it claims to
    answer: can this reviewer write.
    """

    def test_the_audited_argv_is_the_one_the_adapter_spawns(self):
        """The anti-drift assertion, in the shape #717 asked for.

        Two functions in two modules must agree about one command line, so they
        are compared directly rather than each being described in prose. A
        change to either that the other does not follow fails here.
        """
        specs = [
            AgentSpec(name="claude", vendor="anthropic", command="claude", extra_args=[]),
            AgentSpec(
                name="claude",
                vendor="anthropic",
                command="claude",
                extra_args=["--disallowed-tools=Edit"],
            ),
            AgentSpec(name="codex", vendor="openai", command="codex", extra_args=[]),
            AgentSpec(
                name="codex",
                vendor="openai",
                command="codex",
                extra_args=["-s", "workspace-write"],
            ),
            AgentSpec(name="agy", vendor="google", command="agy", extra_args=["--yolo"]),
            AgentSpec(name="grok", vendor="xai", command="cursor-agent", extra_args=["-p"]),
            AgentSpec(name="gpt", vendor="openai", adapter="cli", command="x", extra_args=["-p"]),
            AgentSpec(name="x", vendor="acme", command="x", extra_args=[]),
        ]
        for spec in specs:
            with self.subTest(agent=spec.name, vendor=spec.vendor, adapter=spec.adapter):
                self.assertEqual(
                    privilege.enforce_read_only(spec_adapter(spec), list(spec.extra_args)),
                    adapters._read_only_extra_args(spec),
                )

    def test_an_adapter_with_no_enforcement_to_fall_back_on_still_warns(self):
        # `cli` and `xai` spawn the operator's own binary, for which this tool
        # knows no sandbox flag to add — `enforce_read_only` is a no-op — so for
        # these the declared list really is the whole story, and an unsandboxed
        # seat warns exactly as it did before #750.
        for vendor in ("cli", "xai"):
            with self.subTest(vendor=vendor):
                spec = AgentSpec(
                    name="cursor", vendor=vendor, command="cursor-agent", extra_args=["-p"]
                )
                warnings = privilege.audit_agent(spec)
                self.assertEqual(privilege.enforce_read_only(vendor, ["-p"]), ["-p"])
                self.assertEqual(len(warnings), 1)
                # Not "no `--sandbox` … add a sandbox" (#901): jury adds and checks
                # none for a bring-your-own CLI, which may well carry its own.
                self.assertIn("not under a sandbox jury can verify", warnings[0])
                self.assertNotIn("Add a sandbox", warnings[0])

    def test_the_same_seat_is_clean_or_warned_according_to_its_adapter(self):
        """The pair that isolates what changed: one config, two adapters.

        Identical name and identical (empty) `extra_args`; the only difference is
        whether the protocol has an enforcement to fall back on. It is the
        adapter that decides, which is the whole claim of this change.
        """
        native = AgentSpec(name="seat", vendor="anthropic", command="claude", extra_args=[])
        fronted = AgentSpec(
            name="seat", vendor="anthropic", adapter="cli", command="some-cli", extra_args=[]
        )
        self.assertEqual(privilege.audit_agent(native), [])
        warnings = privilege.audit_agent(fronted)
        self.assertEqual(len(warnings), 1)
        # The bring-your-own-CLI message, not Claude's: the `cli` adapter speaks no
        # `--disallowed-tools`, and it is the adapter that decides what is spoken.
        self.assertIn("not under a sandbox jury can verify", warnings[0])

    def test_extra_args_that_re_enable_writing_are_still_caught(self):
        # The audit must not go blind on the configs it exists for. A sandbox the
        # operator widened is an explicit, documented opt-in that
        # `_ensure_value_sandbox` preserves rather than narrows — so it survives
        # into the spawned argv, and the audit still names it.
        for value in ("workspace-write", "danger-full-access"):
            with self.subTest(sandbox=value):
                spec = AgentSpec(
                    name="codex", vendor="openai", command="codex", extra_args=["-s", value]
                )
                warnings = privilege.audit_agent(spec)
                self.assertEqual(adapters._read_only_extra_args(spec), ["-s", value])
                self.assertEqual(len(warnings), 1)
                self.assertIn(value, warnings[0])
                self.assertIn("granting write/tool/network powers", warnings[0])

    def test_an_injected_sandbox_is_not_trusted_from_an_unknown_cli(self):
        # `enforce_read_only` injects agy's `--sandbox` for an unknown vendor so
        # the seat fails closed (#310), but a bare `--sandbox` is only known to
        # be a sandbox on agy/gemini (#292) — an unknown binary may ignore it.
        # So the injection does not buy this seat a clean audit.
        spec = AgentSpec(name="x", vendor="acme", command="x", extra_args=[])
        warnings = privilege.audit_agent(spec)
        self.assertEqual(adapters._read_only_extra_args(spec), ["--sandbox"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("not running under a recognized read-only sandbox", warnings[0])


class CheckoutConfigRiskIsReportedWhateverTheSandbox(unittest.TestCase):
    """aider and cursor-agent obey the checkout's config; no argv sandbox stops it (#901).

    `_is_sandboxed` accepted codex's value form, `--sandbox read-only`, from every
    vendor, and the checkout-config sentence was attached only after the
    sandboxed early return. A seat that added the flag the warning itself named
    was therefore audited clean and passed `--strict`.
    """

    SEATS = {
        "aider": AgentSpec(
            name="aider", vendor="cli", command="aider", extra_args=["--sandbox", "read-only"]
        ),
        "cursor-agent": AgentSpec(
            name="cursor",
            vendor="cli",
            command="cursor-agent",
            extra_args=["-p", "--trust", "--mode", "ask", "--sandbox", "read-only"],
        ),
        "cursor-agent (xai, equals form)": AgentSpec(
            name="grok",
            vendor="xai",
            command="cursor-agent",
            extra_args=["-p", "--sandbox=read-only"],
        ),
        "aider (-s)": AgentSpec(
            name="aider-s", vendor="cli", command="aider", extra_args=["-s", "read-only"]
        ),
    }
    RISK = {"aider": ".aider.conf.yml", "cursor-agent": ".cursor/hooks.json"}

    def test_a_read_only_value_is_not_a_sandbox_for_a_bring_your_own_cli(self):
        for name, spec in self.SEATS.items():
            with self.subTest(seat=name):
                self.assertFalse(privilege._is_sandboxed(spec.extra_args, vendor=spec.vendor))

    def test_codex_read_only_is_still_a_sandbox(self):
        for args in (["-s", "read-only"], ["--sandbox", "read-only"], ["--sandbox=read-only"]):
            with self.subTest(args=args):
                self.assertTrue(privilege._is_sandboxed(args, vendor="openai"))
                spec = AgentSpec(name="codex", vendor="openai", command="codex", extra_args=args)
                self.assertEqual(privilege.audit_agent(spec), [])

    def test_each_seat_warns_with_its_checkout_config_risk(self):
        for name, spec in self.SEATS.items():
            with self.subTest(seat=name):
                warnings = privilege.audit_agent(spec)
                self.assertEqual(len(warnings), 1, warnings)
                self.assertIn(self.RISK[spec.command], warnings[0])
                self.assertIn("checkouts you trust", warnings[0])

    def test_strict_refuses_each_seat(self):
        from ai_jury.config import _from_dict
        from ai_jury.orchestrator import run_jury

        diff = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
        for name, spec in self.SEATS.items():
            with self.subTest(seat=name):
                agent = {
                    "name": spec.name,
                    "vendor": spec.vendor,
                    "command": spec.command,
                    "extra_args": list(spec.extra_args),
                }
                config = _from_dict({**DEFAULT_CONFIG, "agent": [agent]})
                with self.assertRaises(RuntimeError) as ctx:
                    run_jury(config, diff, strict=True, seed=1)
                message = str(ctx.exception)
                self.assertIn("least-privilege check failed (--strict)", message)
                self.assertIn(self.RISK[spec.command], message)

    def test_the_risk_is_reported_on_a_seat_the_audit_otherwise_accepts(self):
        # A codex-protocol seat with a recognized sandbox, and a claude-protocol
        # seat under the lockdown, that nonetheless spawn aider: the flags jury
        # recognizes do not cover the checkout's config either.
        for spec in (
            AgentSpec(name="odd", vendor="openai", command="aider", extra_args=["-s", "read-only"]),
            AgentSpec(name="odder", vendor="anthropic", command="/opt/bin/aider"),
        ):
            with self.subTest(seat=spec.name):
                warnings = privilege.audit_agent(spec)
                self.assertEqual(len(warnings), 1, warnings)
                self.assertIn(".aider.conf.yml", warnings[0])

    def test_another_cli_gets_no_checkout_sentence(self):
        spec = AgentSpec(name="mine", vendor="cli", command="my-tool", extra_args=[])
        warnings = privilege.audit_agent(spec)
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("checkouts you trust", warnings[0])


_ENDPOINT = "http://localhost:9/v1"
_DIFF = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"


def _strict_error(spec) -> str:
    """The `--strict` refusal `run_jury` raises for a one-seat panel, or ""."""
    from ai_jury.config import _from_dict
    from ai_jury.orchestrator import run_jury

    agent = {
        k: v
        for k, v in {
            "name": spec.name,
            "vendor": spec.vendor,
            "adapter": spec.adapter,
            "command": spec.command,
            "endpoint": spec.endpoint,
            "extra_args": list(spec.extra_args),
        }.items()
        if v
    }
    try:
        run_jury(_from_dict({**DEFAULT_CONFIG, "agent": [agent]}), _DIFF, strict=True, seed=1)
    except RuntimeError as exc:
        return str(exc)
    return ""  # pragma: no cover - reached only when the audit lets the seat through


class TheAuditAsksTheAdapterTheSpawnerBuilds(unittest.TestCase):
    """An `endpoint` does not make a CLI seat an HTTP seat (#901 review).

    `make_adapter` picks the adapter by key before it looks at `endpoint`, so
    each seat below is spawned as a CLI; the audit skipped every one because it
    had an endpoint, and `--strict` let them through.
    """

    SHAPES = {
        "cli aider": AgentSpec(name="a", vendor="cli", command="aider", endpoint=_ENDPOINT),
        "claude adapter running aider": AgentSpec(
            name="b", vendor="anthropic", command="aider", endpoint=_ENDPOINT
        ),
        "codex adapter running cursor-agent": AgentSpec(
            name="c", vendor="openai", command="cursor-agent", endpoint=_ENDPOINT
        ),
        "codex danger-full-access": AgentSpec(
            name="d",
            vendor="openai",
            command="codex",
            extra_args=["-s", "danger-full-access"],
            endpoint=_ENDPOINT,
        ),
        "claude skipping permissions": AgentSpec(
            name="e",
            vendor="anthropic",
            command="claude",
            extra_args=["--dangerously-skip-permissions"],
            endpoint=_ENDPOINT,
        ),
    }

    def test_each_shape_is_spawned_as_a_cli(self):
        for name, spec in self.SHAPES.items():
            with self.subTest(seat=name):
                self.assertTrue(spawns_process(spec))
                self.assertIsInstance(
                    adapters.make_adapter(spec),
                    (
                        adapters.ClaudeAdapter,
                        adapters.CodexAdapter,
                        adapters.GenericCLIAdapter,
                    ),
                )

    def test_each_shape_warns(self):
        for name, spec in self.SHAPES.items():
            with self.subTest(seat=name):
                self.assertNotEqual(privilege.audit_agent(spec), [])

    def test_strict_refuses_each_shape(self):
        for name, spec in self.SHAPES.items():
            with self.subTest(seat=name):
                self.assertIn("least-privilege check failed (--strict)", _strict_error(spec))

    def test_an_http_seat_with_an_endpoint_still_audits_clean(self):
        for spec in (
            AgentSpec(name="l", vendor="local", model="m", endpoint=_ENDPOINT),
            AgentSpec(name="o", vendor="openai-compatible", model="m", endpoint=_ENDPOINT),
            AgentSpec(name="u", vendor="acme", command="x", model="m", endpoint=_ENDPOINT),
            AgentSpec(name="h", vendor="anthropic-api", model="m"),
            AgentSpec(name="r", vendor="openai", adapter="openai-api", model="m"),
        ):
            with self.subTest(seat=spec.name):
                self.assertFalse(spawns_process(spec))
                self.assertEqual(privilege.audit_agent(spec), [])

    def test_the_spawn_question_is_the_one_make_adapter_answers(self):
        http = (adapters.LocalAdapter, adapters._HostedApiAdapter)
        for spec in (
            *self.SHAPES.values(),
            AgentSpec(name="l", vendor="local", model="m"),
            # An unknown vendor with neither command nor endpoint: agy's fallback.
            AgentSpec(name="n", vendor="acme"),
        ):
            with self.subTest(seat=spec.name):
                built = adapters.make_adapter(spec)
                self.assertIs(type(built), adapters.adapter_class(spec))
                self.assertEqual(spawns_process(spec), not isinstance(built, http))
                self.assertEqual(spawns_process(spec), built.SPAWNS_PROCESS)

    def test_config_answers_as_make_adapter_does_for_every_key_and_fallback(self):
        # `config.spawns_process` answers without importing `adapters` (no import
        # cycle, CodeQL py/cyclic-import), so it is held here to the class
        # `make_adapter` really builds: every built-in key, with and without a
        # stray endpoint, and every fallback shape of an unregistered key.
        from ai_jury.config import KNOWN_VENDORS

        http = (adapters.LocalAdapter, adapters._HostedApiAdapter)
        specs = [
            AgentSpec(name=f"{key}{i}", vendor=key, model="m", **extra)
            for key in KNOWN_VENDORS
            for i, extra in enumerate(({}, {"command": "x"}, {"endpoint": _ENDPOINT}))
        ]
        specs += [
            AgentSpec(name="f1", vendor="acme", command="x"),
            AgentSpec(name="f2", vendor="acme", command="x", endpoint=_ENDPOINT),
            AgentSpec(name="f3", vendor="acme", api_key_env="K"),
            AgentSpec(name="f4", vendor="acme", command="x", api_key_env="K"),
            AgentSpec(name="f5", vendor="acme"),
            AgentSpec(name="f6", vendor="acme-api", command="x"),
            AgentSpec(name="f7", vendor="openai", adapter="openai-api", model="m"),
            AgentSpec(name="f8", vendor="openai", adapter="cli", command="cursor-agent"),
        ]
        for spec in specs:
            with self.subTest(seat=spec.name):
                built = adapters.make_adapter(spec)
                self.assertEqual(spawns_process(spec), not isinstance(built, http))
                self.assertEqual(spawns_process(spec), built.SPAWNS_PROCESS)

    def test_a_registered_adapter_is_answered_by_its_class(self):
        from ai_jury import config as config_module

        class HttpShim(adapters.GenericOpenAICompatibleAdapter):
            pass

        class CliShim(adapters.GenericCLIAdapter):
            pass

        # The documented custom adapter: a direct `Adapter` subclass that calls
        # its backend over HTTP and says so (#903).
        class DirectHttpShim(adapters.Adapter):
            SPAWNS_PROCESS = False

        http = (adapters.LocalAdapter, adapters._HostedApiAdapter)
        # `cli` re-registered with an HTTP class, a new key with a CLI one, and a
        # new key with a direct subclass that opts out of spawning.
        for key, cls in (
            ("cli", HttpShim),
            ("shim-cli", CliShim),
            ("shim-direct-http", DirectHttpShim),
        ):
            saved = adapters._VENDOR_ADAPTERS.get(key)
            saved_spawn = config_module._REGISTERED_ADAPTER_SPAWNS.get(key)
            try:
                adapters.register_adapter(key, cls)
                spec = AgentSpec(name="s", vendor=key, command="x", endpoint=_ENDPOINT)
                with self.subTest(key=key):
                    built = adapters.make_adapter(spec)
                    self.assertEqual(spawns_process(spec), built.SPAWNS_PROCESS)
                    if cls is not DirectHttpShim:
                        self.assertEqual(spawns_process(spec), not isinstance(built, http))
                    else:
                        self.assertFalse(spawns_process(spec))
                        self.assertEqual(privilege.audit_agent(spec), [])
            finally:
                if saved is None:
                    adapters._VENDOR_ADAPTERS.pop(key, None)
                    config_module._REGISTERED_VENDORS.discard(key)
                else:
                    adapters._VENDOR_ADAPTERS[key] = saved
                if saved_spawn is None:
                    config_module._REGISTERED_ADAPTER_SPAWNS.pop(key, None)
                else:  # pragma: no cover - no earlier registration of these keys
                    config_module._REGISTERED_ADAPTER_SPAWNS[key] = saved_spawn


class RegisteringATransportIgnoresAnEmptyName(unittest.TestCase):
    def test_an_empty_name_records_nothing(self):
        from ai_jury import config as config_module

        before = dict(config_module._REGISTERED_ADAPTER_SPAWNS)
        config_module.register_adapter_transport("   ", spawns=False)
        self.assertEqual(config_module._REGISTERED_ADAPTER_SPAWNS, before)


class ThePackageHasNoImportCycle(unittest.TestCase):
    """No module of `ai_jury` imports one that imports it back (CodeQL py/cyclic-import).

    `privilege` asked `adapters` whether a seat spawns a process, from inside
    `audit_agent`, while `adapters` imports `privilege` at the top — a cycle a
    lazy import only hides. Every relative import is read, top-level or not.
    """

    def test_no_cycle(self):
        import ast

        pkg = Path(__file__).resolve().parent.parent / "src" / "ai_jury"
        modules = {p.stem for p in pkg.glob("*.py")}
        graph: dict[str, set[str]] = {}
        for path in pkg.glob("*.py"):
            deps = set()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.level == 1:
                    names = [node.module] if node.module else [a.name for a in node.names]
                    deps.update(n.split(".")[0] for n in names)
            graph[path.stem] = (deps & modules) - {path.stem}

        # Depth-first with three colours: an edge back to a module still on the
        # stack closes a cycle. Linear in modules + imports.
        cycles, done, stack = [], set(), []

        def visit(node):
            stack.append(node)
            for dep in sorted(graph[node]):
                if dep in stack:
                    cycles.append(" -> ".join([*stack[stack.index(dep) :], dep]))
                elif dep not in done:
                    visit(dep)
            stack.pop()
            done.add(node)

        for start in sorted(graph):
            if start not in done:
                visit(start)
        self.assertEqual(cycles, [])


class TheCheckoutRiskMatchesTheStem(unittest.TestCase):
    """`cursor-agent.cmd` is cursor-agent (#901 review): only `.exe` was stripped."""

    def test_windows_launchers_and_case_are_matched(self):
        for command, risk in (
            ("cursor-agent.cmd", ".cursor/hooks.json"),
            ("C:\\Tools\\cursor-agent.bat", ".cursor/hooks.json"),
            ("cursor-agent.ps1", ".cursor/hooks.json"),
            ("CURSOR-AGENT.EXE", ".cursor/hooks.json"),
            ("Aider.CMD", ".aider.conf.yml"),
        ):
            for vendor in ("openai", "cli"):
                with self.subTest(command=command, vendor=vendor):
                    spec = AgentSpec(name="s", vendor=vendor, command=command)
                    warnings = privilege.audit_agent(spec)
                    self.assertTrue(any(risk in w for w in warnings), warnings)

    def test_a_codex_seat_through_a_cmd_launcher_is_not_clean(self):
        spec = AgentSpec(name="s", vendor="openai", command="cursor-agent.cmd")
        self.assertNotEqual(privilege.audit_agent(spec), [])
        self.assertIn("least-privilege check failed (--strict)", _strict_error(spec))


class TheAuditReportsThePermissionModeClaudeCodeApplies(unittest.TestCase):
    """Of several `--permission-mode` flags Claude Code uses the last (#888).

    Measured on Claude Code 2.1.236: `plan` then `dontAsk` runs as `dontAsk`, and
    `dontAsk` then `plan` runs as `plan`. The audit reported the first mode that
    was not `dontAsk`, so the first order was warned about as running `plan`.
    """

    def _seat(self, *extra_args):
        return AgentSpec(
            name="claude", vendor="anthropic", command="claude", extra_args=list(extra_args)
        )

    def test_the_last_accepted_mode_is_the_one_reported(self):
        cases = {
            ("plan", "dontAsk"): None,
            ("dontAsk", "plan"): "--permission-mode plan",
            ("acceptEdits", "plan"): "--permission-mode plan",
            ("plan", "default"): "--permission-mode default",
        }
        for (first, last), expected in cases.items():
            for spell in (
                lambda m: ["--permission-mode", m],
                lambda m: [f"--permission-mode={m}"],
            ):
                args = [*spell(first), *spell(last)]
                with self.subTest(args=args):
                    self.assertEqual(privilege._claude_mode_override(args), expected)

    def test_a_seat_whose_last_mode_is_dont_ask_audits_clean(self):
        self.assertEqual(
            privilege.audit_agent(
                self._seat("--permission-mode", "plan", "--permission-mode", "dontAsk")
            ),
            [],
        )

    def test_the_warning_names_the_mode_the_seat_runs_in(self):
        warnings = privilege.audit_agent(
            self._seat("--permission-mode", "acceptEdits", "--permission-mode", "plan")
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("`--permission-mode plan`", warnings[0])
        self.assertNotIn("acceptEdits", warnings[0])

    def test_an_approving_mode_followed_by_dont_ask_is_not_a_bypass(self):
        self.assertIsNone(
            privilege._claude_permission_bypass(
                ["--permission-mode", "bypassPermissions", "--permission-mode", "dontAsk"]
            )
        )
        self.assertEqual(
            privilege._claude_permission_bypass(
                ["--permission-mode", "dontAsk", "--permission-mode", "bypassPermissions"]
            ),
            "--permission-mode bypassPermissions",
        )

    def test_tools_beside_an_overridden_approving_mode_are_not_called_unasked(self):
        warnings = privilege.audit_agent(
            self._seat(
                "--tools",
                "Read",
                "--permission-mode",
                "bypassPermissions",
                "--permission-mode",
                "dontAsk",
            )
        )
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("skips permission checks", warnings[0])

    def test_the_skip_flag_overrides_every_mode_in_either_order(self):
        for args in (
            ["--permission-mode", "plan", "--dangerously-skip-permissions"],
            ["--dangerously-skip-permissions", "--permission-mode", "plan"],
            [
                "--permission-mode",
                "plan",
                "--permission-mode",
                "dontAsk",
                "--dangerously-skip-permissions",
            ],
        ):
            with self.subTest(args=args):
                self.assertEqual(
                    privilege._claude_mode_override(args), "--dangerously-skip-permissions"
                )

    def test_a_rejected_mode_anywhere_still_wins(self):
        for args in (
            ["--permission-mode", "bogus", "--permission-mode", "plan"],
            ["--permission-mode", "plan", "--permission-mode", "bogus"],
        ):
            with self.subTest(args=args):
                self.assertEqual(privilege._claude_mode_override(args), "--permission-mode <value>")

    def test_the_write_role_adds_the_skip_flag_once_for_repeated_dont_ask(self):
        argv = privilege.enable_write(
            "anthropic", ["--permission-mode", "dontAsk", "--permission-mode=dontAsk"]
        )
        self.assertEqual(argv.count("--dangerously-skip-permissions"), 1)
        self.assertNotIn("dontAsk", " ".join(argv))

    def test_the_tools_warning_is_conditional_on_a_seat_that_cannot_start(self):
        """#888 follow-up: a rejected mode means the seat never runs its tools."""
        warnings = privilege.audit_agent(
            self._seat("--tools", "Read", "--permission-mode", "bogus")
        )
        self.assertEqual(len(warnings), 2)
        self.assertIn("once the seat can start", warnings[0])
        self.assertNotIn("runs unasked", warnings[0])
        self.assertIn("Claude Code 2.1.236 rejects", warnings[1])


class AnAgySeatAlwaysGetsItsOwnSandbox(unittest.TestCase):
    """codex's `-s` is not agy's sandbox (#902).

    `agy --help` (1.2.12) lists one sandbox flag, the boolean `--sandbox`, and no
    `-s`. Enforcement skipped the injection when any `-s`/`--sandbox=` token was
    present, so `-s read-only` on an agy seat was spawned without `--sandbox`.
    """

    def _agy(self, *extra_args):
        return AgentSpec(name="agy", vendor="google", command="agy", extra_args=list(extra_args))

    def test_the_boolean_sandbox_is_injected_beside_a_codex_spelling(self):
        for vendor in ("google", "weirdvendor"):
            for args in (
                ["-s", "read-only"],
                ["-s=read-only"],
                ["--sandbox", "read-only"],
            ):
                with self.subTest(vendor=vendor, args=args):
                    self.assertEqual(
                        privilege.enforce_read_only(vendor, list(args)), ["--sandbox", *args]
                    )

    def test_the_boolean_sandbox_is_not_doubled(self):
        for args in (["--sandbox"], ["--sandbox", "--yolo"], ["--yolo", "--sandbox"]):
            with self.subTest(args=args):
                self.assertEqual(privilege.enforce_read_only("google", list(args)), args)

    def test_the_spawned_argv_carries_it(self):
        self.assertEqual(
            adapters._read_only_extra_args(self._agy("-s", "read-only")),
            ["--sandbox", "-s", "read-only"],
        )

    def test_the_audit_and_the_enforcement_read_the_same_flag(self):
        for args in (["-s", "read-only"], ["--sandbox="], ["--sandbox", "read-only"]):
            with self.subTest(args=args):
                self.assertFalse(privilege._is_sandboxed(args, vendor="google"))
                self.assertTrue(
                    privilege._is_sandboxed(
                        privilege.enforce_read_only("google", list(args)), vendor="google"
                    )
                )

    def test_the_codex_spelling_is_reported_as_not_an_agy_flag(self):
        warnings = privilege.audit_agent(self._agy("-s", "read-only"))
        self.assertEqual(len(warnings), 2)
        self.assertEqual(warnings[0], agy_warning("agy"))
        self.assertIn("`-s read-only`", warnings[1])
        self.assertIn("not agy's sandbox", warnings[1])
        self.assertIn("no `-s`", warnings[1])

    def test_every_spelling_agy_does_not_read_is_named_as_written(self):
        cases = {
            ("-s=read-only",): ["-s=read-only"],
            ("-s", "--yolo"): ["-s"],
            ("--sandbox", "read-only"): ["--sandbox read-only"],
        }
        for args, named in cases.items():
            with self.subTest(args=args):
                self.assertEqual(
                    privilege._agy_foreign_sandbox_tokens(
                        privilege.enforce_read_only("google", list(args))
                    ),
                    named,
                )
                warnings = privilege.audit_agent(self._agy(*args))
                self.assertEqual(len(warnings), 2)
                for token in named:
                    self.assertIn(f"`{token}`", warnings[1])

    def test_a_valueless_codex_sandbox_token_states_no_second_sandbox(self):
        self.assertEqual(
            privilege._competing_sandboxes(
                ["-s", "read-only", "--sandbox", "--full-auto"], vendor="openai"
            ),
            [("--full-auto", False)],
        )

    def test_a_codex_value_is_not_described_as_a_second_sandbox(self):
        warnings = privilege.audit_agent(self._agy("-s", "workspace-write"))
        self.assertTrue(any("not agy's sandbox" in w for w in warnings))
        self.assertFalse(any("a sandbox of its own" in w for w in warnings), warnings)

    def test_a_plain_boolean_sandbox_draws_only_the_agy_warning(self):
        self.assertEqual(
            privilege.audit_agent(self._agy("--sandbox", "--yolo")), [agy_warning("agy")]
        )

    def test_the_generic_warning_speaks_agys_terms_on_an_agy_seat(self):
        # A leading bare token leaves the injected `--sandbox` followed by a value,
        # the one argv the audit still does not accept as sandboxed on agy.
        warnings = privilege.audit_agent(self._agy("review-this"))
        generic = [w for w in warnings if "recognized read-only sandbox" in w]
        self.assertEqual(len(generic), 1)
        self.assertIn("agy needs its boolean `--sandbox`", generic[0])
        self.assertNotIn("-s read-only", generic[0])

    def test_the_generic_warning_does_not_deny_an_injected_sandbox(self):
        spec = AgentSpec(name="x", vendor="acme", command="x")
        warning = privilege.audit_agent(spec)[0]
        self.assertIn("the `--sandbox` it adds is agy's", warning)
        self.assertNotIn("no `-s read-only` / `--sandbox`", warning)

    def test_the_codex_generic_warning_names_codexs_flag(self):
        spec = AgentSpec(
            name="codex", vendor="openai", command="codex", extra_args=["-s", "future-mode"]
        )
        self.assertIn("codex needs `-s read-only`", privilege.audit_agent(spec)[0])


class ACustomHttpAdapterCanSayItSpawnsNothing(unittest.TestCase):
    """`SPAWNS_PROCESS = False` on a direct `Adapter` subclass (#903).

    `register_adapter` decided by base class, so a custom HTTP adapter written the
    way docs/configuration.md shows — a direct `Adapter` subclass — was audited as
    a CLI and failed `--strict`.
    """

    def _register(self, key, cls):
        from ai_jury import config as config_module

        adapters.register_adapter(key, cls)

        def forget():
            adapters._VENDOR_ADAPTERS.pop(key, None)
            config_module._REGISTERED_VENDORS.discard(key)
            config_module._REGISTERED_ADAPTER_SPAWNS.pop(key, None)

        self.addCleanup(forget)

    def test_a_direct_subclass_that_opts_out_is_not_audited(self):
        class DirectHttp(adapters.Adapter):
            SPAWNS_PROCESS = False

        self._register("direct-http", DirectHttp)
        spec = AgentSpec(name="d", vendor="direct-http", model="m")
        built = adapters.make_adapter(spec)
        self.assertIsInstance(built, DirectHttp)
        self.assertFalse(spawns_process(spec))
        self.assertEqual(spawns_process(spec), built.SPAWNS_PROCESS)
        self.assertEqual(privilege.audit_privilege([spec]), [])

    def test_only_an_explicit_false_opts_out(self):
        class Unsure(adapters.Adapter):
            SPAWNS_PROCESS = None

        self._register("unsure", Unsure)
        spec = AgentSpec(name="u", vendor="unsure", command="x")
        self.assertTrue(spawns_process(spec))
        self.assertTrue(privilege.audit_agent(spec))

    def test_a_direct_subclass_that_says_nothing_still_spawns(self):
        class DirectCli(adapters.Adapter):
            pass

        self._register("direct-cli", DirectCli)
        spec = AgentSpec(name="d", vendor="direct-cli", command="x")
        self.assertTrue(spawns_process(spec))
        self.assertTrue(privilege.audit_agent(spec))

    def test_every_built_in_adapter_declares_what_it_does(self):
        http = (adapters.LocalAdapter, adapters._HostedApiAdapter)
        for key, cls in adapters._VENDOR_ADAPTERS.items():
            with self.subTest(key=key):
                self.assertEqual(cls.SPAWNS_PROCESS, not issubclass(cls, http))


class AnAgySandboxValueCannotSwitchItOff(unittest.TestCase):
    """`--sandbox=false` must not undo the injected `--sandbox` (#908 review).

    agy parses `--sandbox` as a Go bool (`--sandbox=` answers `invalid boolean
    value … strconv.ParseBool` on 1.2.12), and Go flags are last-wins, so
    `--sandbox --sandbox=false` ran agy with no sandbox while the audit called it
    sandboxed. Enforcement now removes every `--sandbox=<value>` first.
    """

    def _agy(self, *extra_args):
        return AgentSpec(name="agy", vendor="google", command="agy", extra_args=list(extra_args))

    def test_every_valued_spelling_is_removed_and_the_flag_is_injected(self):
        for vendor in ("google", "weirdvendor"):
            for args in (
                ["--sandbox=false"],
                ["--sandbox=0"],
                ["--sandbox=FALSE"],
                ["--sandbox=f"],
                ["--sandbox=true"],
                ["--sandbox=read-only"],
                ["--sandbox="],
                ["-sandbox=false"],
                ["--sandbox", "--sandbox=false"],
                ["--sandbox=false", "--sandbox"],
            ):
                with self.subTest(vendor=vendor, args=args):
                    argv = privilege.enforce_read_only(vendor, list(args))
                    self.assertEqual(argv, ["--sandbox"])

    def test_the_rest_of_the_argv_is_kept(self):
        self.assertEqual(
            privilege.enforce_read_only("google", ["--yolo", "--sandbox=false", "--mode", "plan"]),
            ["--sandbox", "--yolo", "--mode", "plan"],
        )

    def test_a_false_value_is_reported_as_turning_the_sandbox_off(self):
        for args in (
            ["--sandbox=false"],
            ["--sandbox=0"],
            ["--sandbox=FALSE"],
            ["--sandbox", "--sandbox=false"],
        ):
            with self.subTest(args=args):
                warnings = privilege.audit_agent(self._agy(*args))
                self.assertEqual(len(warnings), 2, warnings)
                self.assertEqual(warnings[0], agy_warning("agy"))
                token = next(a for a in args if "=" in a)
                self.assertIn(f"`{token}`", warnings[1])
                self.assertIn("would turn agy's sandbox off", warnings[1])
                self.assertIn("jury removes it", warnings[1])
                self.assertNotIn("not agy's sandbox", warnings[1])

    def test_an_unreadable_value_is_reported_as_one(self):
        for args in (["--sandbox=read-only"], ["--sandbox="]):
            with self.subTest(args=args):
                warnings = privilege.audit_agent(self._agy(*args))
                self.assertEqual(len(warnings), 2, warnings)
                self.assertIn("not a value agy reads as true or false", warnings[1])

    def test_both_kinds_are_named_in_their_own_sentences(self):
        warnings = privilege.audit_agent(self._agy("--sandbox=0", "--sandbox=off", "--sandbox=F"))
        self.assertEqual(len(warnings), 3, warnings)
        self.assertIn("`--sandbox=0`, `--sandbox=F`", warnings[1])
        self.assertIn("jury removes them", warnings[1])
        self.assertIn("`--sandbox=<value>`", warnings[2])

    def test_a_true_value_is_only_redundant(self):
        for args in (["--sandbox=true"], ["--sandbox=1"], ["--sandbox", "--sandbox=True"]):
            with self.subTest(args=args):
                self.assertEqual(privilege.audit_agent(self._agy(*args)), [agy_warning("agy")])


class AnAgySandboxThatIsAnotherOptionsValueIsNotTheSandbox(unittest.TestCase):
    """Go's `flag` gives a string option the next token, whatever it is (#908 review).

    In `--model --sandbox` the `--sandbox` is the model name: nothing was
    injected, the audit said sandboxed, and agy ran without one. Parsing also
    stops at the first positional, so a `--sandbox` after one is prompt text.
    """

    def test_every_value_option_consumes_the_sandbox(self):
        for option in sorted(privilege._AGY_VALUE_OPTIONS):
            for dashes in ("--", "-"):
                args = [f"{dashes}{option}", "--sandbox"]
                with self.subTest(args=args):
                    self.assertFalse(privilege._is_sandboxed(args, vendor="google"))
                    argv = privilege.enforce_read_only("google", list(args))
                    self.assertEqual(argv, ["--sandbox", *args])
                    self.assertTrue(privilege._is_sandboxed(argv, vendor="google"))

    def test_the_list_holds_the_value_options_agy_help_names(self):
        # From `agy --help` 1.2.12; each answered "flag needs an argument" alone.
        for option in ("model", "log-file", "add-dir", "agent", "project", "conversation"):
            with self.subTest(option=option):
                self.assertIn(option, privilege._AGY_VALUE_OPTIONS)
        for boolean in ("sandbox", "dangerously-skip-permissions", "continue", "c"):
            with self.subTest(boolean=boolean):
                self.assertNotIn(boolean, privilege._AGY_VALUE_OPTIONS)

    def test_an_inline_value_consumes_nothing(self):
        args = ["--model=gemini", "--sandbox"]
        self.assertEqual(privilege.enforce_read_only("google", list(args)), args)

    def test_a_sandbox_after_a_positional_or_terminator_is_not_a_flag(self):
        # `---x` and `-=x` are Go's "bad flag syntax": parsing stops there too.
        for args in (
            ["review-this", "--sandbox"],
            ["--", "--sandbox"],
            ["-", "--sandbox"],
            ["---x", "--sandbox"],
            ["-=x", "--sandbox"],
        ):
            with self.subTest(args=args):
                self.assertFalse(privilege._is_sandboxed(args, vendor="google"))
                self.assertEqual(
                    privilege.enforce_read_only("google", list(args)), ["--sandbox", *args]
                )

    def test_a_valued_sandbox_in_a_value_position_is_left_as_the_value(self):
        args = ["--model", "--sandbox=false"]
        self.assertEqual(privilege.enforce_read_only("google", list(args)), ["--sandbox", *args])
        self.assertEqual(
            privilege.audit_agent(
                AgentSpec(name="agy", vendor="google", command="agy", extra_args=args)
            ),
            [agy_warning("agy")],
        )

    def test_codex_spellings_in_a_value_position_are_not_reported(self):
        spec = AgentSpec(name="agy", vendor="google", command="agy", extra_args=["--model", "-s"])
        self.assertEqual(privilege.audit_agent(spec), [agy_warning("agy")])


class NothingAfterTheOptionTerminatorIsAFlag(unittest.TestCase):
    """`--` ends the options for claude (commander) and codex (clap) (#908 review).

    `--permission-mode=bypassPermissions -- --permission-mode=dontAsk` runs claude
    in bypass mode: the last token is prompt text. The audit scanned past the
    terminator, took `dontAsk` as the mode that applies and passed the seat, and
    `--strict` let it through. Enforcement read flags there too, so a `--tools`,
    `--disallowed-tools` or `--permission-mode` after `--` stopped the real one
    being injected in front of it.
    """

    def _claude(self, *extra_args):
        return AgentSpec(
            name="claude", vendor="anthropic", command="claude", extra_args=list(extra_args)
        )

    def test_a_mode_after_the_terminator_does_not_hide_a_bypass(self):
        args = ["--permission-mode=bypassPermissions", "--", "--permission-mode=dontAsk"]
        self.assertEqual(privilege._claude_effective_mode(args), "bypassPermissions")
        self.assertEqual(
            privilege._claude_mode_override(args), "--permission-mode bypassPermissions"
        )
        warnings = privilege.audit_agent(self._claude(*args))
        self.assertEqual(len(warnings), 1)
        self.assertIn("`--permission-mode bypassPermissions`", warnings[0])
        self.assertIn("least-privilege check failed (--strict)", _strict_error(self._claude(*args)))

    def test_the_skip_flag_after_the_terminator_is_prompt_text(self):
        args = ["--", "--dangerously-skip-permissions"]
        self.assertIsNone(privilege._claude_mode_override(args))
        self.assertIsNone(privilege._claude_permission_bypass(args))
        # The readers do not take it as the flag; the audit still reports it,
        # fail-closed, rather than vouch that Claude never reads it (#908 review).
        warnings = privilege.audit_agent(self._claude(*args))
        self.assertEqual(len(warnings), 1)
        self.assertIn("jury cannot tell", warnings[0])
        self.assertNotIn("bypass mode", warnings[0])
        warnings = privilege.audit_agent(
            self._claude("--permission-mode", "plan", "--", "--dangerously-skip-permissions")
        )
        self.assertEqual(len(warnings), 2)
        self.assertIn("`--permission-mode plan`", warnings[0])
        self.assertNotIn("bypass mode", warnings[0])
        self.assertIn(
            "item 4 of `extra_args` (it mentions `dangerously-skip-permissions`)", warnings[1]
        )

    def test_the_lockdown_is_injected_before_an_existing_terminator(self):
        for tail in (
            ["--permission-mode", "plan"],
            ["--tools", "Read"],
            ["--disallowed-tools", "Edit"],
            ["--strict-mcp-config"],
            ["--safe-mode", "--no-session-persistence"],
        ):
            with self.subTest(tail=tail):
                argv = privilege.enforce_read_only("anthropic", ["--", *tail])
                cut = argv.index("--")
                self.assertEqual(argv[cut + 1 :], tail, "text after `--` was rewritten")
                head = argv[:cut]
                for flag in LOCKDOWN:
                    self.assertIn(flag, head)
                self.assertEqual(head[head.index("--disallowed-tools") + 1], DENY)
                self.assertTrue(privilege._claude_is_locked_down(argv))
                self.assertEqual(privilege._claude_tools(argv), [])

    def test_a_terminator_that_is_an_options_value_ends_nothing(self):
        args = ["--model", "--", "--permission-mode", "plan"]
        self.assertEqual(privilege._claude_terminator(args), len(args))
        argv = privilege.enforce_read_only("anthropic", list(args))
        self.assertNotIn("dontAsk", argv)
        warnings = privilege.audit_agent(self._claude(*args))
        self.assertIn("`--permission-mode plan`", warnings[0])

    def test_the_write_role_leaves_text_after_the_terminator_alone(self):
        self.assertEqual(
            privilege.enable_write(
                "anthropic",
                [
                    "--permission-mode",
                    "dontAsk",
                    "--",
                    "--tools",
                    "Read",
                    "--disallowed-tools",
                    "X",
                ],
            ),
            ["--dangerously-skip-permissions", "--", "--tools", "Read", "--disallowed-tools", "X"],
        )

    def test_every_direct_reader_stops_at_the_terminator(self):
        self.assertFalse(privilege._claude_is_locked_down(["--", "--disallowed-tools", DENY]))
        self.assertIsNone(privilege._claude_rejected_mode(["--", "--permission-mode", "bogus"]))
        warnings = privilege.audit_agent(self._claude("--", "--permission-mode", "bogus"))
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("rejects", warnings[0])
        self.assertIn("jury cannot tell", warnings[0])
        said = privilege._claude_skip_overrides(
            [
                "--dangerously-skip-permissions",
                "--permission-mode",
                "plan",
                "--",
                "--permission-mode",
                "auto",
            ]
        )
        self.assertIn("`--permission-mode plan`", said)
        self.assertNotIn("`--permission-mode auto`", said)

    def test_codex_reads_no_sandbox_after_the_terminator(self):
        self.assertEqual(
            privilege.enforce_read_only("openai", ["--", "-s", "read-only"]),
            ["-s", "read-only", "--", "-s", "read-only"],
        )
        self.assertFalse(privilege._is_sandboxed(["--", "-s", "read-only"], vendor="openai"))
        self.assertEqual(
            privilege._competing_sandboxes(["-s", "read-only", "--", "--full-auto"], "openai"),
            [],
        )
        self.assertEqual(
            privilege.enable_write("openai", ["-s", "read-only", "--", "-s", "x"]),
            ["-s", "workspace-write", "--", "-s", "x"],
        )

    def test_codex_adds_its_own_flags_when_they_are_only_prompt_text(self):
        spec = AgentSpec(
            name="codex",
            vendor="openai",
            command="codex",
            extra_args=["--", "--skip-git-repo-check"],
        )
        argv = adapters.CodexAdapter(spec).build_argv("p")
        self.assertIn("--skip-git-repo-check", argv[: argv.index("--")])


class TheAuditFailsClosedOnTokensItCannotPlace(unittest.TestCase):
    """A risky token the readers skip is reported, not trusted (#908 review, round 4).

    `-pn -- --permission-mode=bypassPermissions`: commander reads `-pn` as `-p -n`,
    `-n` takes `--` as the session name, and the mode after it is applied. The
    readers took `--` as the terminator and audited the seat clean. They now model
    combined short options, and any risky token they still do not read as a flag
    draws a warning of its own, so `--strict` refuses the seat either way.
    """

    def _claude(self, *extra_args):
        return AgentSpec(
            name="claude", vendor="anthropic", command="claude", extra_args=list(extra_args)
        )

    def _codex(self, *extra_args):
        return AgentSpec(
            name="codex", vendor="openai", command="codex", extra_args=list(extra_args)
        )

    def test_a_combined_short_option_takes_the_terminator_as_its_value(self):
        args = ["-pn", "--", "--permission-mode=bypassPermissions"]
        self.assertEqual(privilege._claude_terminator(args), len(args))
        self.assertEqual(privilege._claude_effective_mode(args), "bypassPermissions")
        warnings = privilege.audit_agent(self._claude(*args))
        self.assertEqual(len(warnings), 1)
        self.assertIn("`--permission-mode bypassPermissions`", warnings[0])
        self.assertIn("least-privilege check failed (--strict)", _strict_error(self._claude(*args)))

    def test_a_short_value_option_last_in_a_cluster_takes_the_next_token(self):
        for token, kind in (
            ("-n", "required"),
            ("-pn", "required"),
            ("-cpn", "required"),
            ("-pd", "optional"),
            ("-r", "optional"),
            ("-np", None),  # `-n` takes `p` inline
            ("-nfoo", None),
            ("-pc", None),
            ("-px", None),  # claude does not know `-x`
            ("--name", None),
        ):
            with self.subTest(token=token):
                self.assertEqual(privilege._claude_short_cluster(token), kind)

    def test_an_optional_value_takes_only_a_token_that_is_not_an_option(self):
        self.assertEqual(privilege._claude_value_positions(["-r", "abc", "--x"]), {1})
        self.assertEqual(privilege._claude_value_positions(["--resume", "--", "x"]), set())
        self.assertEqual(privilege._claude_terminator(["-d", "--", "x"]), 1)
        self.assertEqual(privilege._claude_value_positions(["-pd", "api", "--x"]), {1})
        self.assertEqual(privilege._claude_value_positions(["-pd"]), set())
        self.assertEqual(privilege._claude_value_positions(["--resume", "abc", "--x"]), {1})
        self.assertEqual(
            privilege._claude_terminator(["--worktree", "wt", "--", "--permission-mode=plan"]), 2
        )

    def test_a_bypass_the_readers_do_not_place_still_warns(self):
        cases = (
            ["-np", "--", "--permission-mode=bypassPermissions"],
            ["-pn", "x", "--", "--permission-mode=bypassPermissions"],
            ["--", "--permission-mode", "acceptEdits"],
            ["--model", "--dangerously-skip-permissions"],
            ["--", "--allow-dangerously-skip-permissions"],
            ["--settings", '{"permissions": {"defaultMode": "bypassPermissions"}}'],
            ["--", "--tools", "Read"],
            ["--append-system-prompt", "--mcp-config=/tmp/servers.json"],
        )
        for args in cases:
            with self.subTest(args=args):
                warnings = privilege.audit_agent(self._claude(*args))
                self.assertTrue(any("jury cannot tell" in w for w in warnings), warnings)
                self.assertIn(
                    "least-privilege check failed (--strict)", _strict_error(self._claude(*args))
                )

    def test_a_short_value_option_with_its_value_and_no_risk_audits_clean(self):
        for args in (["-n", "--"], ["-pn", "review"], ["-n", "x", "--", "just text"]):
            with self.subTest(args=args):
                self.assertEqual(privilege.audit_agent(self._claude(*args)), [])

    def test_the_readers_own_findings_are_not_repeated(self):
        for args in (
            ["--permission-mode", "bypassPermissions"],
            ["--permission-mode=plan"],
            ["--dangerously-skip-permissions"],
            ["--tools", "Read"],
            ["--mcp-config", "/tmp/s.json"],
            ["--permission-mode", "dontAsk"],
            ["--", "--permission-mode", "dontAsk"],
            ["--", "--tools", ""],
        ):
            with self.subTest(args=args):
                self.assertEqual(privilege._claude_unread_risks(args), [])

    def test_codex_bypass_tokens_the_readers_do_not_place_warn(self):
        for args in (
            ["--", "--yolo"],
            ["--", "-s", "danger-full-access"],
            ["-c", 'sandbox_mode="danger-full-access"'],
            ["--", "--dangerously-bypass-approvals-and-sandbox"],
        ):
            with self.subTest(args=args):
                warnings = privilege.audit_agent(self._codex(*args))
                self.assertTrue(any("jury cannot tell" in w for w in warnings), warnings)

    def test_codex_tokens_already_reported_are_not_repeated(self):
        warnings = privilege.audit_agent(self._codex("--full-auto"))
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("jury cannot tell", warnings[0])
        self.assertEqual(privilege.audit_agent(self._codex("--", "-s", "read-only")), [])


#: Secret-shaped test values (#908 review, round 5). The first is the shape the
#: redaction helper knows; the second is one it does not, so only the audit's
#: own refusal to repeat argv values keeps it out.
FAKE_KEY = "sk-ant-api03-FAKEFAKEFAKEFAKEFAKEFAKEFAKE0123456789"
FAKE_PLAIN = "KEY=hunter2hunter2"
SETTINGS = (
    '{"permissions": {"defaultMode": "bypassPermissions"}, '
    f'"env": {{"ANTHROPIC_API_KEY": "{FAKE_KEY}", "OTHER": "{FAKE_PLAIN}"}}}}'
)


class NoWarningRepeatsAnArgvValue(unittest.TestCase):
    """A warning names what it found; it never prints the token (#908 review, round 5).

    The fail-closed warning printed a whole `--settings` JSON, API key included,
    and every other warning printed the argv value it was about.
    """

    SEATS = {
        "claude settings JSON": AgentSpec(
            name="c", vendor="anthropic", command="claude", extra_args=["--settings", SETTINGS]
        ),
        "claude token after --": AgentSpec(
            name="c",
            vendor="anthropic",
            command="claude",
            extra_args=["--", f"--permission-mode={FAKE_PLAIN}{FAKE_KEY}"],
        ),
        "claude rejected mode": AgentSpec(
            name="c",
            vendor="anthropic",
            command="claude",
            extra_args=["--permission-mode", f"{FAKE_PLAIN}{FAKE_KEY}"],
        ),
        "claude tool names": AgentSpec(
            name="c",
            vendor="anthropic",
            command="claude",
            extra_args=["--tools", f"Read,{FAKE_PLAIN},{FAKE_KEY}"],
        ),
        "codex -c override": AgentSpec(
            name="x",
            vendor="openai",
            command="codex",
            extra_args=["-c", f'sandbox_mode="danger-full-access" {FAKE_PLAIN} {FAKE_KEY}'],
        ),
        "codex second sandbox value": AgentSpec(
            name="x",
            vendor="openai",
            command="codex",
            extra_args=["-s", "read-only", "-s", f"{FAKE_PLAIN}{FAKE_KEY}"],
        ),
        "codex selector value": AgentSpec(
            name="x",
            vendor="openai",
            command="codex",
            extra_args=[f"--full-auto={FAKE_PLAIN}{FAKE_KEY}"],
        ),
        "agy sandbox value": AgentSpec(
            name="g",
            vendor="google",
            command="agy",
            extra_args=[f"--sandbox={FAKE_PLAIN}{FAKE_KEY}"],
        ),
        "agy codex spellings": AgentSpec(
            name="g",
            vendor="google",
            command="agy",
            extra_args=["-s", f"{FAKE_PLAIN}{FAKE_KEY}", f"-s={FAKE_PLAIN}{FAKE_KEY}"],
        ),
    }

    def _assert_clean(self, text):
        self.assertNotIn("hunter2", text)
        self.assertNotIn("FAKEFAKE", text)
        self.assertNotIn("sk-ant-api03", text)

    def test_no_warning_repeats_a_secret(self):
        for name, spec in self.SEATS.items():
            with self.subTest(seat=name):
                warnings = privilege.audit_agent(spec)
                self.assertTrue(warnings, "the seat must still be warned about")
                self._assert_clean("\n".join(warnings))

    def test_the_strict_refusal_repeats_no_secret(self):
        for name, spec in self.SEATS.items():
            with self.subTest(seat=name):
                error = _strict_error(spec)
                self.assertIn("least-privilege check failed (--strict)", error)
                self._assert_clean(error)

    def test_the_settings_json_is_named_by_position_and_marker(self):
        warnings = privilege.audit_agent(self.SEATS["claude settings JSON"])
        self.assertTrue(
            any("item 2 of `extra_args` (it mentions `bypassPermissions`)" in w for w in warnings),
            warnings,
        )

    def test_known_words_are_still_named(self):
        spec = AgentSpec(
            name="x", vendor="openai", command="codex", extra_args=["-s", "danger-full-access"]
        )
        self.assertIn("danger-full-access", privilege.audit_agent(spec)[0])
        self.assertIn(
            "`--tools Read,<tool>`",
            privilege.audit_agent(
                AgentSpec(
                    name="c",
                    vendor="anthropic",
                    command="claude",
                    extra_args=["--tools", "Read,mcp__x__y"],
                )
            )[0],
        )

    def test_every_warning_is_redacted_as_well(self):
        # Defence in depth: a secret-shaped seat name is not an argv value the
        # sentences guard against, and the redaction pass still masks it.
        spec = AgentSpec(name=FAKE_KEY, vendor="google", command="agy")
        warnings = privilege.audit_agent(spec)
        self.assertTrue(warnings)
        self._assert_clean("\n".join(warnings))


if __name__ == "__main__":
    unittest.main()
