## 2024-05-18 - Prevent Subprocess Stderr Secret Leakage
**Vulnerability:** Subprocess calls (e.g. `gh` CLI) might print credentials or secrets in their `stderr` output when they fail. This output was previously included directly in `RuntimeError` exception messages, which could be logged or reported, resulting in secret leakage.
**Learning:** Never assume external process output is safe to propagate. Subprocess `stderr` must be properly redacted before being included in exceptions or logs to ensure sensitive details are scrubbed. The `redaction.redact` utility should be used for this.
**Prevention:** Apply `redact` to `stderr` output of subprocesses before throwing exceptions with their error contents.
