"""Secret redaction for prompt text sent to external agents (issue #6).

Deterministic: the same input always yields the same redacted output and count.
Each match is replaced with ``[REDACTED:<kind>]``.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

# An already-emitted redaction marker (`[REDACTED:<kind>]`). Used to avoid
# re-redacting a value an earlier, more-specific pattern already replaced
# (issue v1.5.0/L-3): without this guard a secret inside a basic-auth URL —
# `https://user:AKIA…@host` — is redacted by `aws_access_key`, then the
# resulting `[REDACTED:aws_access_key]` token is re-redacted by `basic_auth`,
# losing the informative kind and double-counting.
_REDACTED_MARKER = re.compile(r"^\[REDACTED:[a-z_]+\]$")

# Ordered list of (kind, compiled pattern). Order matters: more specific
# patterns run before the generic key=value catch-all so secrets are labeled
# with the most informative kind.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "pem_private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"
            r".*?-----END (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
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
    # More provider formats (issue #316/L-2), each anchored on a distinctive
    # prefix to avoid false positives. (Twilio Account SIDs and bare hex are
    # deliberately NOT added — a SID is an identifier, and a broad hex rule would
    # over-match commit SHAs.)
    ("sendgrid_key", re.compile(r"SG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}")),
    ("pypi_token", re.compile(r"pypi-[A-Za-z0-9_\-]{16,}")),
    ("npm_token", re.compile(r"npm_[A-Za-z0-9]{36}")),
    ("slack_webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+")),
    # Basic-auth credentials in a URL (issue #302): `scheme://user:password@host`
    # (e.g. `redis://default:s3cr3t…@cache:6379`). Only the password (group 2) is
    # redacted; the `://user:` prefix (group 1) and `@` suffix (group 3) are kept
    # so the URL stays readable. The trailing `@` requirement means a normal
    # `host:port` (no `@`) never matches. Handled group-wise in `redact`.
    #
    # The username run is `*`, not `+` (review of #302): a token-as-password URL
    # with an EMPTY username — `https://:SECRET@host` — is common, and `+` made
    # the match fail there and leak the secret.
    #
    # The password run is `{1,}`, not `{6,}` (issue v1.5.0/L-1): a short password
    # (`http://user:pass@host`, 4 chars) is still a leaked credential.
    ("basic_auth", re.compile(r"(://[^/:@\s]*:)([^@\s]+)(@)")),
    # Colon-less userinfo — a bare token in the userinfo position with no
    # password colon (`http://apitoken12345@host/v1`) (issue v1.5.0/L-1). The
    # colon form above never matches this (it requires a `:`), so the token would
    # otherwise reach an external agent in cleartext. Group 1 (`://`) and group 3
    # (`@`) are kept; group 2 (the token) is redacted. Disjoint from the colon
    # form: `[^/:@\s]+` stops at a `:`, so a `user:pass@` URL is handled above.
    #
    # Note: this also redacts a bare, non-secret username (`https://user@host` ->
    # `https://[REDACTED:basic_auth]@host`). That is deliberate safe-by-default
    # over-redaction — userinfo in a reviewed diff is rare and a credential more
    # often than not, and over-masking a username never leaks anything.
    ("basic_auth", re.compile(r"(://)([^/:@\s]+)(@)")),
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
    #
    # `account[_-]?key` is included for Azure `AccountKey=…` / connection strings
    # (issue #302). The separator group tolerates an optional closing quote
    # (`["']?`) BEFORE the `=`/`:`, so a JSON-quoted key — `"private_key_id": "…"`
    # in a GCP service-account blob — is matched too (its value is redacted while
    # the structure stays valid). A non-secret like `client_email` is not caught:
    # its value contains `@`/`.` outside the value char class.
    (
        "secret_assignment",
        re.compile(
            r"([A-Za-z0-9_]{0,40}(?:api[_-]?key|secret|token|password|passwd|"
            r"access[_-]?key|account[_-]?key|private[_-]?key|client[_-]?secret|"
            r"credential)"
            r"[A-Za-z0-9_]{0,40})"
            r"([\"']?\s*[=:]\s*)([\"']?)[A-Za-z0-9_\-+/=]{16,}([\"']?)",
            re.IGNORECASE,
        ),
    ),
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
                return f"{m.group(1)}{m.group(2)}{m.group(3)}[REDACTED:{_kind}]{m.group(4)}"

            result = pattern.sub(_sub_assign, result)
        elif kind == "basic_auth":

            def _sub_basic_auth(m, _kind=kind):
                nonlocal count
                # Don't re-redact a value an earlier pattern already replaced
                # (issue v1.5.0/L-3): keep the more-informative kind and the
                # accurate count.
                if _REDACTED_MARKER.match(m.group(2)):
                    return m.group(0)
                count += 1
                # Keep the prefix (group 1: `://user:` or `://`) and `@` suffix
                # (group 3); redact only the credential so the URL stays readable.
                return f"{m.group(1)}[REDACTED:{_kind}]{m.group(3)}"

            result = pattern.sub(_sub_basic_auth, result)
        else:

            def _sub(_m, _kind=kind):
                nonlocal count
                count += 1
                return f"[REDACTED:{_kind}]"

            result = pattern.sub(_sub, result)
    return result, count


def redact_url_userinfo(url: str) -> str:
    """Strip any userinfo (``user[:password]``) from a URL before display.

    Structural counterpart to :func:`redact` for the one place a base endpoint
    URL can carry a credential (issue v1.5.0/L-1). Rather than relying on the
    `basic_auth` regex — which can miss short or colon-less userinfo — this
    parses the URL and replaces the whole ``userinfo@`` run with
    ``[REDACTED]@``, keeping the scheme, host, port, path, and query verbatim
    (so an IPv6 literal or non-default port is preserved exactly). A URL that
    `urlsplit` cannot parse falls back to the regex-based :func:`redact`.
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return redact(url)[0]
    netloc = parts.netloc
    if "@" not in netloc:
        return url
    # The host part has no unencoded '@'; split on the last one so a userinfo
    # that contains a percent-encoded '@' is still handled correctly.
    hostport = netloc[netloc.rfind("@") + 1 :]
    return urlunsplit(parts._replace(netloc=f"[REDACTED]@{hostport}"))
