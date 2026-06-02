"""Tests for secret redaction and context policy (issue #6)."""
import unittest

from ai_jury.config import (
    AgentSpec,
    ContextConfig,
    JuryConfig,
    _from_dict,
)
from ai_jury.orchestrator import run_jury
from ai_jury.redaction import redact


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
        out, n = redact('api_key = "%s"' % secret)
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


class ContextConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = ContextConfig()
        self.assertEqual(cfg.mode, "diff-only")
        self.assertTrue(cfg.redact_secrets)

    def test_load_expanded(self):
        cfg = _from_dict(
            {"jury": {"context": {"mode": "expanded", "redact_secrets": False}}}
        )
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
        outcome = run_jury(
            _cfg("diff-only"), "some diff", context="pr body", mock=True
        )
        self.assertEqual(outcome.context_mode, "diff-only")

    def test_expanded_mode_recorded(self):
        outcome = run_jury(
            _cfg("expanded"), "some diff", context="pr body", mock=True
        )
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
