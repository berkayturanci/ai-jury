"""Unit tests for the least-privilege agent auditor (OWASP LLM01 defense).

Locks the behaviour that dangerous agent invocations are surfaced as warnings
while a read-only / locked-down config is not. Stdlib + offline.

Since #750 the subject of every audit assertion is the argv the seat is
*spawned* with — `enforce_read_only` applied to the declared `extra_args` — so a
config whose gap the adapter closes is not warned about, and one whose gap it
cannot close still is.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_jury import adapters, privilege
from ai_jury.config import AgentSpec, spec_adapter


class AuditAgentTest(unittest.TestCase):
    def test_claude_without_disallowed_tools_is_spawned_locked_down(self):
        # Issue #750: the recommended configuration — a `claude` seat with no
        # `extra_args` at all — is spawned with the whole write-tool denylist,
        # so it cannot write and must not be reported as though it could.
        spec = AgentSpec(name="claude", vendor="anthropic", command="claude", extra_args=[])
        self.assertEqual(
            privilege.enforce_read_only("anthropic", "claude", []),
            ["--disallowed-tools", "Edit,Write,NotebookEdit,Bash"],
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
            extra_args=["--disallowed-tools=Edit,Write,NotebookEdit,Bash"],
        )
        self.assertEqual(privilege.audit_agent(spec), [])
        self.assertEqual(
            privilege.enforce_read_only("anthropic", "claude", list(spec.extra_args)),
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
            privilege.enforce_read_only("anthropic", "claude", list(spec.extra_args)),
            ["--disallowed-tools=Edit,Write,NotebookEdit,Bash"],
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
            privilege.enforce_read_only("anthropic", "claude", list(spec.extra_args)),
            ["--disallowed-tools", "Edit,Write,NotebookEdit,Bash", "--disallowed-tools"],
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
            privilege.enforce_read_only("anthropic", "claude", list(spec.extra_args)),
            ["--disallowed-tools", "Edit,Write,NotebookEdit,Bash"],
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
            privilege.enforce_read_only("google", "agy", list(spec.extra_args)),
            ["--sandbox", "--dangerously-skip-permissions"],
        )
        self.assertEqual(privilege.audit_agent(spec), [])

    def test_yolo_flag_is_spawned_sandboxed(self):
        spec = AgentSpec(name="gemini", vendor="google", command="gemini", extra_args=["--yolo"])
        self.assertEqual(
            privilege.enforce_read_only("google", "gemini", list(spec.extra_args)),
            ["--sandbox", "--yolo"],
        )
        self.assertEqual(privilege.audit_agent(spec), [])

    def test_full_auto_flag_warns(self):
        # Still warns after #750, and deliberately: unlike `--yolo`, codex's
        # `--full-auto` SELECTS a workspace-write sandbox rather than skipping a
        # prompt, and `_ensure_value_sandbox` looks only for an `-s`/`--sandbox`
        # token — so the enforced `-s read-only` is passed *beside* it and codex,
        # not this module, decides which one wins.
        spec = AgentSpec(name="codex", vendor="openai", command="codex", extra_args=["--full-auto"])
        warnings = privilege.audit_agent(spec)
        self.assertEqual(
            privilege.enforce_read_only("openai", "codex", list(spec.extra_args)),
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
        self.assertEqual(privilege.audit_agent(spec), [])

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
        self.assertEqual(privilege.enforce_read_only("openai", "codex", []), ["-s", "read-only"])
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
            privilege.enforce_read_only("openai", "codex", list(spec.extra_args)),
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
        self.assertEqual(privilege.audit_privilege(specs), [])

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
        out = privilege.enforce_read_only("anthropic", "claude", [])
        self.assertEqual(out, ["--disallowed-tools", "Edit,Write,NotebookEdit,Bash"])

    def test_claude_merges_missing_write_tools_into_existing(self):
        out = privilege.enforce_read_only(
            "anthropic", "claude", ["--disallowed-tools", "Edit,Write"]
        )
        self.assertEqual(out, ["--disallowed-tools", "Edit,Write,NotebookEdit,Bash"])

    def test_claude_shipped_default_is_unchanged(self):
        shipped = [
            "--output-format",
            "text",
            "--disallowed-tools",
            "Edit,Write,NotebookEdit,Bash",
            "--dangerously-skip-permissions",
        ]
        self.assertEqual(privilege.enforce_read_only("anthropic", "claude", shipped), shipped)

    def test_claude_equals_form_disallowed_is_merged(self):
        # Review of #288: the =-form must be merged too, not left to sit after the
        # injected safe set where a last-wins CLI could narrow the deny set.
        out = privilege.enforce_read_only("anthropic", "claude", ["--disallowed-tools=Edit"])
        self.assertEqual(out, ["--disallowed-tools=Edit,Write,NotebookEdit,Bash"])

    def test_codex_injects_read_only_when_no_sandbox(self):
        out = privilege.enforce_read_only("openai", "codex", [])
        self.assertEqual(out, ["-s", "read-only"])

    def test_codex_equals_form_sandbox_is_respected_not_doubled(self):
        out = privilege.enforce_read_only("openai", "codex", ["--sandbox=read-only"])
        self.assertEqual(out, ["--sandbox=read-only"])

    def test_codex_respects_operator_widened_sandbox(self):
        # An explicit (audited) opt-in is preserved, never overridden.
        out = privilege.enforce_read_only("openai", "codex", ["-s", "workspace-write"])
        self.assertEqual(out, ["-s", "workspace-write"])

    def test_agy_injects_sandbox_when_absent(self):
        out = privilege.enforce_read_only("google", "agy", ["--dangerously-skip-permissions"])
        self.assertEqual(out, ["--sandbox", "--dangerously-skip-permissions"])

    def test_unknown_vendor_gets_sandbox(self):
        # Issue #310 (completes #300): an unknown vendor routes to the generic
        # AgyAdapter, so --sandbox is injected — fail-closed, never fail-open.
        out = privilege.enforce_read_only("weirdvendor", "x", ["--foo"])
        self.assertEqual(out, ["--sandbox", "--foo"])

    def test_unknown_vendor_existing_sandbox_not_doubled(self):
        out = privilege.enforce_read_only("weirdvendor", "x", ["--sandbox"])
        self.assertEqual(out, ["--sandbox"])

    def test_local_vendor_name_substring_not_mishandled(self):
        # Review of #310: a local agent whose NAME contains "claude"/"codex" must
        # still be left unchanged (the vendor=="local" fast-path wins).
        self.assertEqual(privilege.enforce_read_only("local", "local-claude", []), [])
        self.assertEqual(privilege.enforce_read_only("local", "my-codex", []), [])

    def test_local_vendor_is_left_untouched(self):
        self.assertEqual(privilege.enforce_read_only("local", "qwen", []), [])

    def test_cli_vendor_is_left_untouched(self):
        self.assertEqual(privilege.enforce_read_only("cli", "cursor", ["--print"]), ["--print"])


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

        self.assertEqual(privilege.audit_agent(spec), [])

    def test_agys_boolean_sandbox_selects_nothing(self):
        """A bare `--sandbox`, or one followed by another flag, names no value."""
        for extra in (["--sandbox"], ["--sandbox", "--yolo"]):
            with self.subTest(extra=extra):
                spec = AgentSpec(name="agy", vendor="google", command="agy", extra_args=extra)

                self.assertEqual(privilege.audit_agent(spec), [])

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
            verdicts.add(tuple(privilege.audit_agent(spec)))

        self.assertEqual(len(argvs), 1)
        self.assertEqual(verdicts, {()})

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
                    privilege.enforce_read_only(
                        spec_adapter(spec), spec.name, list(spec.extra_args)
                    ),
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
                self.assertEqual(privilege.enforce_read_only(vendor, "cursor", ["-p"]), ["-p"])
                self.assertEqual(len(warnings), 1)
                self.assertIn("not running under a recognized read-only sandbox", warnings[0])

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
        # The generic sandbox message, not Claude's: the `cli` adapter speaks no
        # `--disallowed-tools`, and it is the adapter that decides what is spoken.
        self.assertIn("not running under a recognized read-only sandbox", warnings[0])

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


if __name__ == "__main__":
    unittest.main()
