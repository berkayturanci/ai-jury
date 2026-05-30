"""Secret redaction for prompt text sent to external agents (issue #6).

Deterministic: the same input always yields the same redacted output and count.
Each match is replaced with ``[REDACTED:<kind>]``.
"""
from __future__ import annotations

import re

# Ordered list of (kind, compiled pattern). Order matters: more specific
# patterns run before the generic key=value catch-all so secrets are labeled
# with the most informative kind.
_PATTERNS: list[tuple[str, "re.Pattern"]] = [
    ("pem_private_key", re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"
        r".*?-----END (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
        re.DOTALL,
    )),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("bearer_token", re.compile(r"Bearer\s+[A-Za-z0-9._\-]+")),
    ("secret_assignment", re.compile(
        r"(api[_-]?key|secret|token)(\s*[=:]\s*)[\"']?[A-Za-z0-9_\-]{16,}[\"']?",
        re.IGNORECASE,
    )),
]


def redact(text: str) -> tuple[str, int]:
    """Replace recognized secrets with ``[REDACTED:<kind>]``.

    Returns ``(redacted_text, count)`` where count is the number of replacements.
    """
    if not text:
        return text, 0
    count = 0
    result = text
    for kind, pattern in _PATTERNS:
        if kind == "secret_assignment":
            def _sub_assign(m, _kind=kind):
                nonlocal count
                count += 1
                # Preserve the key and separator, redact the value.
                return f"{m.group(1)}{m.group(2)}[REDACTED:{_kind}]"
            result = pattern.sub(_sub_assign, result)
        else:
            def _sub(m, _kind=kind):
                nonlocal count
                count += 1
                return f"[REDACTED:{_kind}]"
            result = pattern.sub(_sub, result)
    return result, count
