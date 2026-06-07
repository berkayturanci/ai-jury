"""Secret redaction for prompt text sent to external agents (issue #6).

Deterministic: the same input always yields the same redacted output and count.
Each match is replaced with ``[REDACTED:<kind>]``.
"""
from __future__ import annotations

import re

# Ordered list of (kind, compiled pattern). Order matters: more specific
# patterns run before the generic key=value catch-all so secrets are labeled
# with the most informative kind.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("pem_private_key", re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"
        r".*?-----END (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
        re.DOTALL,
    )),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    # Classic `sk-…` AND modern project/service keys `sk-proj-…` /
    # `sk-svcacct-…` / `sk-admin-…`, which embed hyphens the old `[A-Za-z0-9]`
    # class stopped at (issue #122). First char after `sk-` is alphanumeric, then
    # 18+ of alphanumeric / hyphen / underscore.
    ("openai_key", re.compile(r"sk-[A-Za-z0-9][A-Za-z0-9_-]{18,}")),
    ("bearer_token", re.compile(r"Bearer\s+[A-Za-z0-9._\-]+")),
    # Common provider token formats (issue #290). Each runs BEFORE the generic
    # `secret_assignment` catch-all so the value is replaced with its most
    # informative kind and cannot be double-counted by the assignment pattern.
    # Stripe keys use an underscore form (`sk_live_…`) distinct from the OpenAI
    # `sk-…` hyphen pattern above; GitHub fine-grained PATs (`github_pat_…`) are
    # not covered by the `gh[pousr]_` class.
    ("slack_token", re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("stripe_key", re.compile(r"(?:sk|rk|pk)_(?:live|test)_[0-9A-Za-z]{16,}")),
    ("github_pat", re.compile(r"github_pat_[0-9A-Za-z_]{20,}")),
    ("jwt", re.compile(r"eyJ[0-9A-Za-z_\-]+\.[0-9A-Za-z_\-]+\.[0-9A-Za-z_\-]+")),
    # Capture the surrounding quotes (groups 3 and 4) so they are PRESERVED in
    # the replacement (issue #102): redacting only the value keeps a quoted
    # assignment a valid string literal instead of producing a broken,
    # unterminated string that misleads reviewers into phantom syntax findings.
    #
    # The key side (group 1) allows surrounding identifier chars so a keyword
    # embedded mid-name is still recognized (issue #289): `aws_secret_access_key`
    # matches via `secret`, where the old anchored `(secret)` required the `=`
    # to follow `secret` directly and so leaked the canonical AWS variable name.
    # `password`/`passwd` and a few more key names are included for the same
    # reason. The surrounding identifier runs are BOUNDED (`{0,40}`), not `*`:
    # a real secret variable name is short, and an unbounded run on both sides of
    # the keyword makes the scan quadratic on a long word-char input with no
    # separator (a ReDoS vector). The bound keeps it linear.
    ("secret_assignment", re.compile(
        r"([A-Za-z0-9_]{0,40}(?:api[_-]?key|secret|token|password|passwd|"
        r"access[_-]?key|private[_-]?key|client[_-]?secret|credential)"
        r"[A-Za-z0-9_]{0,40})"
        r"(\s*[=:]\s*)([\"']?)[A-Za-z0-9_\-+/=]{16,}([\"']?)",
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
                # Preserve the key, separator, AND surrounding quotes; redact
                # only the value so a quoted assignment stays syntactically valid.
                return (
                    f"{m.group(1)}{m.group(2)}{m.group(3)}"
                    f"[REDACTED:{_kind}]{m.group(4)}"
                )
            result = pattern.sub(_sub_assign, result)
        else:
            def _sub(m, _kind=kind):
                nonlocal count
                count += 1
                return f"[REDACTED:{_kind}]"
            result = pattern.sub(_sub, result)
    return result, count
