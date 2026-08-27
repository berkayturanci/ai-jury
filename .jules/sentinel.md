## 2024-05-18 - [CRITICAL] Prevented gh CLI stdout/stderr Secret Leakage
**Vulnerability:** Subprocesses (`gh` CLI commands in `_gh` and `_gh_with_input`) could print raw output or errors containing sensitive data (e.g. `ghp_` tokens) directly into raised `RuntimeError` messages, bypassing prompt/JSON context redaction layers.
**Learning:** External dependencies (CLIs) running outside the Python process boundary don't inherit the application's memory protections or logging filters. Their raw `stderr` and `stdout` must be aggressively sanitized before crossing back into application exception/logging flows.
**Prevention:** Apply `ai_jury.redaction.redact()` explicitly to the `.stderr` / `.stdout` / decoded string buffers of failed `subprocess.Popen` and `subprocess.run` calls *before* wrapping them in Python exceptions or returning their strings.

## 2024-05-18 - [CRITICAL] Fix exception string secret leakage
**Vulnerability:** Unsanitized exception strings (e.g. str(exc)) could leak secrets into the jury report/logs.
**Learning:** Just like stdout/stderr, exception messages containing raw inputs/urls/commands must be sanitized, as they often include verbatim tokens or paths that crashed the process.
**Prevention:** Always wrap str(exc) with redaction.redact()[0] before logging or returning it in the user report.

## 2024-05-18 - [CRITICAL] Fix exception string secret leakage (cli, findings, policy, commands)
**Vulnerability:** Unsanitized exception strings (e.g. str(exc)) could leak secrets into the jury report/logs in multiple places: `cli.py`, `commands.py`, `findings.py`, and `policy.py`.
**Learning:** Exception messages containing raw inputs/urls/commands must be sanitized across the entire codebase, as they often include verbatim tokens or paths that crashed the process. Furthermore, when adding redaction, one must ensure the `redact` function is correctly imported, placing it *after* any `from __future__ import annotations` statements to avoid `SyntaxError`.
**Prevention:** Always wrap str(exc) with redaction.redact()[0] before logging or returning it in the user report. Verify the `redact` import is present and correctly positioned when modifying files to add redaction.

## 2024-05-18 - [CRITICAL] Properly Scope Stdout Secret Leakage Fix
**Vulnerability:** In an attempt to prevent standard output leaks from `gh` calls, redacting the successful `stdout` globally broke the AI's ability to see and review actual diffs, while simultaneously failing to address the actual log/print paths where successful output was printed to console (e.g. `dry_run` payload dumps).
**Learning:** Redaction must be applied strictly to Error outputs and Log paths. Successful data pathways that feed downstream consumers MUST remain intact.
**Prevention:** Always trace the data flow of the `stdout`. If the `stdout` is data consumed by the app (like a fetched diff), do NOT redact the return value. Apply redaction *only* at the specific points where that data is printed, logged, or bundled into an exception string that crosses the application boundary.

## 2024-05-18 - [CRITICAL] Fix ConfigError unhandled exception in doctor
**Vulnerability:** A `ConfigError` exception triggered by oversized config files (e.g. `jury.toml` exceeding limit) would cause `jury --doctor` to crash, leaking an unredacted exception message to `stderr`.
**Learning:** Top-level diagnostic or CLI paths that load configurations must comprehensively catch domain exceptions (`ConfigError`) alongside underlying parser errors (`TOMLDecodeError`) to prevent unhandled crashes from exposing unredacted data.
**Prevention:** Always verify that `ConfigError` is handled in try/except blocks surrounding `load_config` calls, and explicitly run `redact(str(exc))[0]` on the exception message.

## 2024-05-18 - [MEDIUM] Fix TOMLDecodeError stack trace leak in config loader
**Vulnerability:** When loading `jury.toml`, invalid TOML syntax caused `tomllib.loads` to raise a `TOMLDecodeError` which was not caught in `_read_toml_bounded`, leading to an unhandled exception and stack trace leak.
**Learning:** Top-level configuration loaders must explicitly catch underlying parsing exceptions (like `TOMLDecodeError`) and wrap them in domain exceptions (like `ConfigError`). Catching and securely wrapping the error prevents raw stack traces from exposing potentially sensitive internals or environment information to standard error.
**Prevention:** Always ensure that data deserialization libraries (like `tomllib` or `json`) have their exceptions caught and wrapped inside domain-specific configuration errors in the loader function.
## 2026-08-10 - [MEDIUM] Fix ReplayError exception leakage in replay.py
**Vulnerability:** Unsanitized exception strings in `src/ai_jury/replay.py` (e.g. from JSON parse errors or file reading errors) could leak secrets into the standard error output when loading replay files.
**Learning:** Replay errors, like config errors, require explicit redaction of the `str(exc)` before surfacing the error to users, as malformed or hostile files could cause parser exception messages to contain sensitive snippets.
**Prevention:** Always wrap exception message interpolations with `redact(str(exc))[0]` when raising domain-specific exceptions, and ensure `redact` is properly imported.

## 2026-08-11 - [MEDIUM] Prevent Exception Context Secret Leakage
**Vulnerability:** When catching parsing/execution exceptions (e.g. `OSError`, `ValueError`, `tomllib.TOMLDecodeError`) and wrapping them in domain-specific exceptions (e.g. `ReplayError`, `ConfigError`, `CommandError`, `PolicyError`, `SystemExit`), using `from exc` retained the original unredacted exception in the `__cause__` attribute. This allowed raw stack traces, containing potentially sensitive parsing contents or file paths, to leak if the exception went unhandled.
**Learning:** Just redacting the `str(exc)` in the new domain exception message is insufficient if `from exc` is used, because Python's exception chaining preserves the original, unredacted exception underneath.
**Prevention:** Use `raise DomainError(...) from None` instead of `from exc` when wrapping exceptions in top-level library components to completely sever the chain and prevent unredacted stack trace leakage.
## 2024-05-18 - [CRITICAL] Fix exception string secret leakage in GenericCLIAdapter spawn
**Vulnerability:** Unsanitized exception strings (`str(exc)`) when a CLI spawn failed in `GenericCLIAdapter.run` could leak secrets into standard output logs/responses.
**Learning:** External dependencies (CLIs) running outside the Python process boundary don't inherit the application's memory protections or logging filters. Their spawn errors must also be aggressively sanitized before crossing back into application exception/logging flows.
**Prevention:** Always wrap `str(exc)` with `redaction.redact()[0]` before logging or returning it in the user report in adapter classes.

## 2026-08-22 - [WITHDRAWN] "workspace-write sandbox bypass in Codex" (#600)

**This entry recorded a vulnerability that did not exist.** Retained rather than
deleted so the false finding is not re-derived by the next reader. Withdrawn by
#608 on 2026-08-25.

**Original claim:** Codex reviewers configured with `-s workspace-write` escaped
least-privilege warnings and the `--strict` flag, because `_DANGEROUS_FLAGS` was
missing `workspace-write`.

**Why it was wrong:** `_DANGEROUS_FLAGS` was never the only path to a warning.
`audit_agent` also emits a catch-all for any non-claude agent not under a
*restricting* sandbox — a value sandbox like `workspace-write` does not restrict,
so it fell through to the catch-all. That catch-all was added in `f5797cf`
(2026-06-07), two and a half months before #600. Measured at `3cc3623^` and
`3cc3623`, a `-s workspace-write` spec produces **exactly one warning either
way**; only its wording changed. `--strict` promotes any warning to a failure,
so the configuration already failed. There was nothing to escape.

**What #600 actually did, and it was worth doing:** replaced the generic
"no recognized sandbox" message with one naming `workspace-write` and the
powers it grants. That is a `docs`/`chore` improvement in operator-facing
wording, not a security fix, and it should not have carried a [HIGH].

**Learning — the one that generalises:** a missing entry in a denylist is only
a vulnerability if the denylist is the *sole* control. Before filing, run the
allegedly-bypassing input against the code as it stood, and against the parent
of your own fix. Four review passes asserted this bypass; none of them ran it.

**The refutation was already in the tree, green, in the file under review:**
`tests/test_privilege.py::test_codex_no_sandbox_no_dangerous_flag_warns` asserts
that a codex agent with no sandbox *and no dangerous flag* warns. It was added
alongside the catch-all in the same commit. A claimed bypass whose negation is
a passing test in the diff should not survive one pass, let alone four — so
read the tests around the code you are indicting, and treat a green test that
contradicts your finding as the finding.

**Prevention:** a [HIGH] or [CRITICAL] sentinel entry must record the observed
before-behaviour — the command run and its output on the unfixed code — not
only the reasoning that predicted it. Reasoning about a control in isolation
cannot see the control that sits behind it.

## 2026-08-27 - [LOW] Redact git stderr before it reaches an exception message
**Gap (not an observed leak):** `cli.py`'s `_git_diff` passed the first line of raw
git stderr into `SystemExit` unredacted, while the adjacent error path in the same
function redacted. `redact()` does catch the payload this is aimed at — a token in a
remote URL becomes `[REDACTED:github_token]` — so the fix is worth having.
**Severity is [LOW], deliberately.** Only two commands reach this path,
`git show --format= --patch -m --first-parent <rev> --` and `git diff <rev> --`, and
both are purely local: they never contact a remote, so they cannot print a URL with an
embedded credential. The stderr they actually produce is `fatal: ambiguous argument`.
No path was demonstrated by which a secret reaches this string. Filed as CRITICAL and
corrected on review — see #600/#608 for why an overstated entry here is expensive: the
next reader takes this file as established fact.
**Learning:** Raw `stderr` from external processes must always be sanitized before being raised in exceptions, even if the error seems like a simple git lookup failure, because the output might echo attacker-controlled paths or environment details.
**Prevention:** Always apply `redact(...)[0]` to external command output strings before incorporating them into exception messages.
