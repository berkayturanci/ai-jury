# Security model

The council only **orchestrates read-only reviews**: it sends a diff (and
optional PR context) to each agent CLI, captures their text output, and
synthesizes a verdict. It does not apply edits or run project build/test
commands on your behalf. The per-agent `extra_args` defaults reflect that
read-only posture while keeping non-interactive runs from hanging or failing.

## Codex invocation

The Codex adapter runs:

```
codex exec <extra_args>     # with the prompt piped on stdin
```

Two deliberate choices:

- **Prompt on stdin, not as a positional argument.** Passing the prompt
  positionally can cause `codex exec` to block waiting for stdin in
  non-interactive contexts (CI, hooks, headless shells). Piping the prompt in
  avoids that hang.
- **`-s danger-full-access` by default.** Codex's standard sandbox can block the
  outbound network and `gh` access that PR review relies on, which surfaces as
  spurious failures rather than review feedback. Because this tool performs only
  read-only review orchestration, granting full access keeps runs reliable
  without expanding what the tool itself does.

Note: avoid `--full-auto` here — it implies a stricter workspace sandbox that
reintroduces the access problems above.

### Opting into stricter sandboxing

`extra_args` is fully user-controlled. To tighten the sandbox, override it in
`council.toml`:

```toml
[[agent]]
name = "codex"
vendor = "openai"
command = "codex"
# Drop danger-full-access, or pick a narrower sandbox mode for your setup.
extra_args = ["-s", "read-only"]
```

If you narrow the sandbox, verify that whatever access your review flow needs
(e.g. `gh`, network) is still permitted, or some reviews may fail instead of
reporting findings.
