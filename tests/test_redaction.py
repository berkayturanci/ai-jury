"""Tests for secret redaction and context policy (issue #6)."""

import unittest

from ai_jury.config import (
    AgentSpec,
    ContextConfig,
    JuryConfig,
    _from_dict,
)
from ai_jury.orchestrator import run_jury
from ai_jury.redaction import redact, redact_url_userinfo


class RedactionTests(unittest.TestCase):
    def test_aws_access_key(self):
        out, n = redact("key AKIAABCDEFGHIJKLMNOP here")
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:aws_access_key]", out)
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", out)

    def test_github_token(self):
        out, n = redact("ghp_" + "a" * 30)
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:github_token]", out)

    def test_openai_key(self):
        out, n = redact("sk-" + "B" * 32)
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:openai_key]", out)

    def test_modern_openai_keys_redacted(self):
        # Issue #122: project/service/admin keys embed hyphens; the old pattern
        # stopped at the first one and leaked them.
        for key in (
            "sk-proj-ABCDEF0123456789ABCDEFmoremore",
            "sk-svcacct-ABCDEF0123456789ABCDEF",
            "sk-admin-ABCDEF0123456789ABCDEF",
        ):
            out, n = redact("OPENAI_API_KEY=" + key)
            self.assertEqual(n, 1, key)
            self.assertNotIn(key, out)
            self.assertIn("[REDACTED:openai_key]", out)

    def test_bearer_token(self):
        out, n = redact("Authorization: Bearer abc.def-123_XYZ")
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:bearer_token]", out)
        self.assertNotIn("abc.def-123_XYZ", out)

    def test_pem_private_key(self):
        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEvgIBADANBgkqhkiG9w0BAQEFAASC\n"
            "-----END RSA PRIVATE KEY-----"
        )
        out, n = redact(f"before {pem} after")
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:pem_private_key]", out)
        self.assertNotIn("MIIEvgIBAD", out)

    def test_generic_secret_assignment(self):
        out, n = redact('api_key = "abcdef0123456789ABCDEF"')
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:secret_assignment]", out)
        self.assertIn("api_key", out)  # key name preserved

    def test_token_assignment(self):
        out, n = redact("token: supersecretvalue1234567")
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:secret_assignment]", out)

    def test_base64_style_secret_assignment(self):
        secret = "dGhpcytpcy9hPXNlY3JldA+b/c=="  # contains +, /, = and is 16+ chars
        out, n = redact(f'api_key = "{secret}"')
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:secret_assignment]", out)
        self.assertNotIn(secret, out)
        self.assertIn("api_key", out)  # key name preserved

    def test_quotes_preserved_keeps_valid_syntax(self):
        # Issue #102: redacting a quoted assignment must keep the quotes so the
        # line stays a valid string literal (no fabricated syntax errors).
        out, n = redact('api_key = "abcdef0123456789ABCDEF"')
        self.assertEqual(n, 1)
        self.assertEqual(out, 'api_key = "[REDACTED:secret_assignment]"')
        # The redacted line is still syntactically valid Python.
        compile(out, "<redacted>", "exec")

    def test_single_quotes_preserved(self):
        out, _ = redact("token = 'supersecretvalue1234567'")
        self.assertEqual(out, "token = '[REDACTED:secret_assignment]'")

    def test_unquoted_assignment_unchanged_shape(self):
        out, n = redact("token: supersecretvalue1234567")
        self.assertEqual(n, 1)
        self.assertEqual(out, "token: [REDACTED:secret_assignment]")

    def test_no_secret(self):
        out, n = redact("just some normal code text")
        self.assertEqual(n, 0)
        self.assertEqual(out, "just some normal code text")

    def test_deterministic(self):
        text = "AKIAABCDEFGHIJKLMNOP and sk-" + "C" * 30
        self.assertEqual(redact(text), redact(text))

    def test_count_multiple(self):
        text = "AKIAABCDEFGHIJKLMNOP AKIAQRSTUVWXYZ012345"
        _out, n = redact(text)
        self.assertEqual(n, 2)

    # Issue #289: key names the old anchored pattern missed.
    def test_password_assignment_redacted(self):
        out, n = redact('password = "hunter2hunter2hunter2"')
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:secret_assignment]", out)
        self.assertNotIn("hunter2hunter2hunter2", out)
        self.assertIn("password", out)  # key name preserved

    def test_aws_secret_access_key_redacted(self):
        # The canonical AWS secret-key variable name: `secret` is followed by
        # `_access_key`, so the old `(secret)(=)` anchor never matched it.
        secret = "wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY"
        out, n = redact(f"aws_secret_access_key={secret}")
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:secret_assignment]", out)
        self.assertNotIn(secret, out)
        self.assertIn("aws_secret_access_key", out)

    # Issue #290: common provider token formats.
    def test_slack_token_redacted(self):
        # Synthetic and assembled at runtime so no realistic token literal is
        # committed (GitHub push protection flags Slack-shaped literals).
        token = "xox" + "b-" + "EXAMPLE" + "0" * 12 + "NOTAREALTOKEN"
        out, n = redact(token)
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:slack_token]", out)

    def test_google_api_key_redacted(self):
        out, n = redact("AIza" + "A" * 35)
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:google_api_key]", out)

    def test_stripe_key_redacted(self):
        out, n = redact("sk_live_" + "a" * 24)
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:stripe_key]", out)

    def test_github_pat_redacted(self):
        out, n = redact("github_pat_" + "A1b2" * 8)
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:github_pat]", out)

    def test_jwt_redacted(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.dBjftJeZ4CVP_mB92K27u"
        out, n = redact(f"the value is {jwt}")
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:jwt]", out)
        self.assertNotIn(jwt, out)

    def test_a_long_key_like_input_is_not_an_assignment(self):
        """The correctness half of the old ReDoS test, kept separate."""
        _out, n = redact("secret_" + "a" * 200_000)
        self.assertEqual(n, 0)  # no separator/value -> not an assignment

    def test_secret_assignment_scan_scales_linearly(self):
        # `secret_assignment` puts a bounded identifier run (`{0,40}`) on each
        # side of the keyword. Unbounded (`*`) they overlap, and a long
        # adversarial identifier with no separator makes the scan quadratic.
        #
        # This asserts the *growth*, not a wall-clock ceiling. A ceiling on one
        # input size cannot tell quadratic from linear-but-slow, and #614
        # measured the old 3 s bound as pure scheduler noise: 200 000 chars came
        # out faster than 50 000, and the test failed under `coverage` for
        # reasons unrelated to the code.
        #
        # `process_time` is this process's CPU time, so load elsewhere on the
        # machine cannot inflate it, and `min` over repeats is the standard
        # estimator for a timed body — noise only ever adds. Measured here:
        # linear 1.7-2.2x per doubling (identical under `coverage`), the `*`
        # mutation 3.9x. The 3.0 threshold sits between with room either way.
        import time as _t

        def cpu_min(size: int, repeats: int = 3) -> float:
            text = "secret_" + "a" * size
            best = float("inf")
            for _ in range(repeats):
                start = _t.process_time()
                redact(text)
                best = min(best, _t.process_time() - start)
            return best

        # A ratio between two sub-millisecond numbers measures the clock, not
        # the regex. Grow until the base case is comfortably above that floor,
        # so a faster future machine raises the sizes instead of going flaky.
        size = 6_000
        for _ in range(5):
            base = cpu_min(size)
            if base >= 0.005:
                break
            size *= 4
        else:  # pragma: no cover - only on a machine ~1000x faster than 2026's
            self.skipTest(f"redact is too fast to time at {size} chars")

        doubled = cpu_min(size * 2)
        ratio = doubled / base
        self.assertLess(
            ratio,
            3.0,
            f"doubling the input multiplied the scan by {ratio:.2f}x "
            f"({base:.4f}s -> {doubled:.4f}s at {size} chars); linear is ~2x "
            "and quadratic ~4x, so the bounded identifier runs in "
            "`secret_assignment` have probably become unbounded",
        )

    # Issue #302: basic-auth URLs, Azure AccountKey, GCP JSON keys.
    def test_basic_auth_url_password_redacted(self):
        out, n = redact("redis://default:" + "S3cr3tP4ss" * 2 + "@cache:6379")
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:basic_auth]", out)
        self.assertIn("redis://default:", out)  # prefix preserved
        self.assertIn("@cache:6379", out)  # suffix preserved
        self.assertNotIn("S3cr3tP4ss", out)

    # Issue #316/L-2: more provider token formats.
    def test_more_provider_formats_redacted(self):
        cases = {
            "sendgrid_key": "SG." + "A" * 22 + "." + "B" * 43,
            "pypi_token": "pypi-" + "AgEIcHlwaS5vcmcCJ" + "x" * 12,
            "npm_token": "npm_" + "a" * 36,
            "slack_webhook": "https://hooks.slack.com/services/T00000/B00000/" + "abcd1234efgh5678",
        }
        for kind, value in cases.items():
            out, n = redact(value)
            self.assertEqual(n, 1, kind)
            self.assertIn(f"[REDACTED:{kind}]", out, kind)

    def test_basic_auth_empty_username_redacted(self):
        # Review of #302: a token-as-password URL with no username
        # (https://:TOKEN@host) must still be redacted (the `+` quantifier leaked).
        out, n = redact("https://:" + "SuperSecretToken" + "@api.example.com")
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:basic_auth]", out)
        self.assertNotIn("SuperSecretToken", out)
        self.assertIn("@api.example.com", out)

    def test_url_without_auth_not_matched(self):
        # A normal host:port with no `@` credentials must not be touched.
        out, n = redact("https://example.com:8080/v1/models")
        self.assertEqual(n, 0)
        self.assertEqual(out, "https://example.com:8080/v1/models")

    def test_basic_auth_short_password_redacted(self):
        # Issue v1.5.0/L-1: a short (<6 char) password is still a credential and
        # was leaking past the old `{6,}` quantifier.
        out, n = redact("http://user:pass@host")
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:basic_auth]", out)
        self.assertNotIn("pass@", out)
        self.assertIn("http://user:", out)
        self.assertIn("@host", out)

    def test_basic_auth_colonless_token_redacted(self):
        # Issue v1.5.0/L-1: a bare token in the userinfo position (no `:`) was
        # not matched by the colon-form pattern and leaked in cleartext.
        out, n = redact("http://apitoken12345@host:11434/v1")
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:basic_auth]", out)
        self.assertNotIn("apitoken12345", out)
        self.assertIn("http://", out)
        self.assertIn("@host:11434/v1", out)

    def test_nested_redaction_keeps_informative_kind(self):
        # Issue v1.5.0/L-3: a secret inside a basic-auth URL is redacted by its
        # specific pattern; the resulting marker must NOT be re-redacted by
        # basic_auth (which would lose the kind and inflate the count).
        out, n = redact("https://user:AKIAIOSFODNN7EXAMPLE@host")
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:aws_access_key]", out)
        self.assertNotIn("[REDACTED:basic_auth]", out)


class RedactUrlUserinfoTests(unittest.TestCase):
    """Issue v1.5.0/L-1: structural userinfo strip for endpoint display."""

    def test_strips_colonless_token(self):
        self.assertEqual(
            redact_url_userinfo("http://apitoken12345@host:11434/v1"),
            "http://[REDACTED]@host:11434/v1",
        )

    def test_strips_user_password(self):
        self.assertEqual(
            redact_url_userinfo("http://user:pw@host/v1"),
            "http://[REDACTED]@host/v1",
        )

    def test_no_userinfo_unchanged(self):
        url = "http://localhost:11434/v1"
        self.assertEqual(redact_url_userinfo(url), url)

    def test_ipv6_host_preserved(self):
        self.assertEqual(
            redact_url_userinfo("http://user:pw@[::1]:11434/v1"),
            "http://[REDACTED]@[::1]:11434/v1",
        )

    def test_scheme_less_user_password_redacted(self):
        # Security audit 2026-06-13: a scheme-less URL puts the authority in the
        # path, so urlsplit leaves netloc empty and the credential would leak.
        self.assertEqual(
            redact_url_userinfo("user:pass@host/v1"),
            "[REDACTED]@host/v1",
        )

    def test_scheme_less_colonless_token_redacted(self):
        self.assertEqual(
            redact_url_userinfo(":secret@host:11434"),
            "[REDACTED]@host:11434",
        )

    def test_scheme_less_no_userinfo_unchanged(self):
        # No credential present: must be returned verbatim, not mangled.
        self.assertEqual(redact_url_userinfo("host:11434/v1"), "host:11434/v1")

    def test_empty_unchanged(self):
        self.assertEqual(redact_url_userinfo(""), "")

    def test_malformed_url_falls_back_to_redact(self):
        # urlsplit raises on this; the helper must not crash.
        out = redact_url_userinfo("http://[::1")
        self.assertIsInstance(out, str)

    def test_azure_account_key_redacted(self):
        out, n = redact("AccountKey=" + "Ab12Cd34Ef56Gh78" + "+/==")
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:secret_assignment]", out)
        self.assertIn("AccountKey", out)

    def test_gcp_private_key_id_json_redacted(self):
        out, n = redact('"private_key_id": "' + "0123456789abcdef" * 2 + '"')
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:secret_assignment]", out)
        self.assertIn("private_key_id", out)

    def test_gcp_client_email_not_redacted(self):
        # client_email is an identifier, not a secret; must not be redacted.
        out, n = redact('"client_email": "svc@proj.iam.gserviceaccount.com"')
        self.assertEqual(n, 0)


class ContextConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = ContextConfig()
        self.assertEqual(cfg.mode, "diff-only")
        self.assertTrue(cfg.redact_secrets)

    def test_load_expanded(self):
        cfg = _from_dict({"jury": {"context": {"mode": "expanded", "redact_secrets": False}}})
        self.assertEqual(cfg.context.mode, "expanded")
        self.assertFalse(cfg.context.redact_secrets)

    def test_invalid_mode_falls_back(self):
        cfg = _from_dict({"jury": {"context": {"mode": "everything"}}})
        self.assertEqual(cfg.context.mode, "diff-only")


def _cfg(mode="diff-only", redact_secrets=True):
    cfg = JuryConfig(
        rounds=1,
        chair="claude",
        verify=False,
        agents=[AgentSpec(name="claude", vendor="anthropic", command="claude")],
    )
    cfg.context.mode = mode
    cfg.context.redact_secrets = redact_secrets
    return cfg


class ContextSelectionTests(unittest.TestCase):
    def test_diff_only_mode_recorded(self):
        outcome = run_jury(_cfg("diff-only"), "some diff", context="pr body", mock=True)
        self.assertEqual(outcome.context_mode, "diff-only")

    def test_expanded_mode_recorded(self):
        outcome = run_jury(_cfg("expanded"), "some diff", context="pr body", mock=True)
        self.assertEqual(outcome.context_mode, "expanded")

    def test_redaction_counted_in_outcome(self):
        outcome = run_jury(
            _cfg("diff-only", True), "leak AKIAABCDEFGHIJKLMNOP", context="", mock=True
        )
        self.assertTrue(outcome.redact_secrets)
        self.assertEqual(outcome.redaction_count, 1)

    def test_redaction_off(self):
        outcome = run_jury(
            _cfg("diff-only", False), "leak AKIAABCDEFGHIJKLMNOP", context="", mock=True
        )
        self.assertFalse(outcome.redact_secrets)
        self.assertEqual(outcome.redaction_count, 0)


if __name__ == "__main__":
    unittest.main()
