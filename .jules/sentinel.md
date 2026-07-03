## 2024-05-18 - [CRITICAL] Prevented gh CLI stdout/stderr Secret Leakage
**Vulnerability:** Subprocesses (`gh` CLI commands in `_gh` and `_gh_with_input`) could print raw output or errors containing sensitive data (e.g. `ghp_` tokens) directly into raised `RuntimeError` messages, bypassing prompt/JSON context redaction layers.
**Learning:** External dependencies (CLIs) running outside the Python process boundary don't inherit the application's memory protections or logging filters. Their raw `stderr` and `stdout` must be aggressively sanitized before crossing back into application exception/logging flows.
**Prevention:** Apply `ai_jury.redaction.redact()` explicitly to the `.stderr` / `.stdout` / decoded string buffers of failed `subprocess.Popen` and `subprocess.run` calls *before* wrapping them in Python exceptions or returning their strings.

## 2024-05-18 - [CRITICAL] Fix exception string secret leakage
**Vulnerability:** Unsanitized exception strings (e.g. str(exc)) could leak secrets into the jury report/logs.
**Learning:** Just like stdout/stderr, exception messages containing raw inputs/urls/commands must be sanitized, as they often include verbatim tokens or paths that crashed the process.
**Prevention:** Always wrap str(exc) with redaction.redact()[0] before logging or returning it in the user report.
