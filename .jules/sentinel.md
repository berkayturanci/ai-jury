## 2024-06-18 - Prevent Secret Leakage in Subprocess Errors
**Vulnerability:** Subprocess calls (e.g., to the `gh` CLI) in `src/ai_jury/github.py` embedded raw, unredacted standard error output into `RuntimeError` exceptions when the subprocess failed. This could expose sensitive tokens or credentials if the underlying command failed and printed environment variables or arguments to stderr.
**Learning:** This existed because the error handling blindly passed the subprocess output to the exception message for debugging purposes, missing the fact that CLI tools can leak secrets in error traces.
**Prevention:** Always use `ai_jury.redaction.redact` to sanitize subprocess output before logging or raising exceptions, ensuring secrets are replaced with `[REDACTED:<kind>]`.
