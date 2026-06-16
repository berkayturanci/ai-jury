## 2024-06-16 - [Medium] Prevent secret leakage in gh CLI error messages
**Vulnerability:** Information Leakage via Subprocess Stderr
**Learning:** In wrapper scripts that execute external CLIs (like `gh`), error output dumped to stderr might contain sensitive authentication URLs or tokens if a command fails during setup or fetch. Unsanitized `raise RuntimeError(f"gh ... failed: {proc.stderr}")` exposes these directly to logs.
**Prevention:** Always run stderr outputs through `redact()` before using them in exception messages or surfacing them, providing defense-in-depth against secret leakage.
