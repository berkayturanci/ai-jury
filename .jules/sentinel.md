## 2024-06-20 - Redacting subprocess stderr in exceptions
**Vulnerability:** Subprocess calls to `gh` CLI were directly embedding `stderr` output into `RuntimeError` exception messages. If the CLI failed and dumped environment variables or authentication tokens into `stderr`, these secrets could be leaked via the unredacted exception trace.
**Learning:** Exception messages that include raw output from external processes (like CLI tools) are a potential vector for secret leakage.
**Prevention:** Always sanitize or redact `stderr` and `stdout` from subprocesses using a tool like `ai_jury.redaction.redact` before embedding the output into exception messages or logs.
