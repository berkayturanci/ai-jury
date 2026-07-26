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
