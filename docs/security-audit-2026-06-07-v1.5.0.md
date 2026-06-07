# ai-jury — Security Re-Audit Report (v1.5.0)

**Date:** 2026-06-07 (post-v1.5.0 release)
**Scope:** the whole `src/ai_jury/` codebase, `main` @ v1.5.0
**Background:** this is the current Claude security analysis. Across five rounds (v1.3.0 → v1.5.0), the findings from earlier audits were fixed and shipped as #287–#316; this report verifies those fixes hold in the released v1.5.0 source and hunts for anything remaining or new. (The companion [Codex Security Scan — 2026-06-07](security-scan-2026-06-07.md) is the independent Codex analysis.)
**Method:** four parallel static reviews across the attack surface (subprocess/sandbox, network/SSRF, prompt-injection/redaction, filesystem/cache), plus live stress/ReDoS timing and alternate-loopback fuzzing. The two headline findings were confirmed empirically in source.

---

## Executive Summary

**Every #287–#316 fix is verified present and holding in the v1.5.0 source.** The filesystem/cache surface is fully clean (no new findings); the subprocess/sandbox surface yields only two Info notes. There are **no Critical or High findings**.

Two genuine findings remain. The most important is an **incomplete-coverage gap in the #316/L-1 fix**: one untrusted-content slot was neutralized, but a second, structurally-identical addendum in the same module was missed.

| Severity | Count | Findings |
|----------|-------|----------|
| Critical | 0 | — |
| High | 0 | — |
| Medium | 1 | M-1 (synthesis "VERIFICATION VERDICTS" slot un-fenced / un-neutralized) |
| Low | 3 | L-1 (init-endpoint redaction misses short / bare-token userinfo), L-2 (classification `vulnerab`/`exploit` keyword stems never match), L-3 (nested redaction — cosmetic) |
| Info | a few | below |

---

## Confirmed-Fixed — prior fixes hold (v1.5.0)

- **#314** — `injection.scan` is O(N) (newline offsets computed once + `bisect` line lookup + per-kind hit cap). Line numbers correct at every edge (index 0, just-after-newline, multi-line); the cap can't hide a real injection (different kinds are capped independently); linear at 100k–500k (68→342 ms).
- **#315** — a malformed endpoint (`http://[::1`, `http://[fe80::1%25eth0]`, `://x`, `''`, …) is a clean `ConfigError`, never a crash.
- **#316** — L-1 `prior_txt` fenced + neutralized (✓ but see M-1); L-2 SendGrid/PyPI/npm/Slack-webhook patterns (no false positives / ReDoS); L-3 `clear()` is 64-hex-gated (won't delete unrelated files); L-4 `mkstemp` atomic write (O_EXCL, no symlink-follow, unlink-on-error); L-5 TOML 4 MiB cap + clean error on non-UTF-8; L-6 `_is_sandboxed` recognizes the `=`-form (no divergence from enforcement); L-7 init-endpoint redaction (✓ but see L-1).
- **#287–#313** — prompts on stdin (never argv); adapter-layer sandbox enforcement; unknown-vendor fail-closed (`vendor=="local"` checked first); `_spawn` process-group kill (probe included); the opener registers no file/ftp/redirect handler; cache HMAC fail-closed; TLS verification on (no `CERT_NONE`). Two-pass `neutralize_sentinels` is applied at every review/debate/verify/synthesis slot.
- **Dangerous classes absent:** no `pickle`/`eval`/`exec`/`yaml`/zip-slip/`mktemp`/`shell=True`; no path-traversal sink; cache-key path traversal is impossible (SHA-256 digest filenames).

---

## Medium

### M-1 (Medium) — the "VERIFICATION VERDICTS" addendum appended to the synthesis prompt is un-fenced and un-neutralized
- **Location:** `orchestrator.py:802-803` — `prompt += f"\n\n=== VERIFICATION VERDICTS ===\n{_format_verdicts(verdicts)}\n"`; renderer `_format_verdicts` (`:688-697`) emits `v.claim`/`v.reasoning` raw.
- **Verified:** confirmed in source at line 803. Every other untrusted slot (diff/context/reviews/debate/findings/prior_txt) gets `neutralize_sentinels` + a `<<<UNTRUSTED_…>>>` fence; this addendum gets **neither**.
- **Description:** `verdict.claim`/`reasoning` quote the candidate finding (which quotes untrusted diff text), and `parse_verdicts` only `.strip()`s them. The #316/L-1 fix neutralized `prior_txt` but missed this **structurally-identical** verdicts addendum in the same module.
- **Exploit scenario:** an attacker diff carries text that a reviewer echoes into a finding claim → the verifier copies it into `verdict.claim` → it lands in the synthesis prompt **outside** any UNTRUSTED fence, where it can forge a `UNTRUSTED_REVIEW>>>` to break out of the preceding fence or inject a fake `SYSTEM:` directive the chair reads as a trusted instruction. The CI gate is consensus-derived, so the verdict can't be flipped, but the synthesis verdict *text* shown to the human can be manipulated.
- **Fix:** mirror the `prior_txt` pattern — fence and neutralize:
  ```python
  prompt += (
      "\n\n=== VERIFICATION VERDICTS (may quote UNTRUSTED text) ===\n"
      "<<<UNTRUSTED_FINDINGS\n"
      + prompts.neutralize_sentinels(_format_verdicts(verdicts))
      + "\nUNTRUSTED_FINDINGS>>>\n"
  )
  ```

---

## Low

### L-1 (Low/Medium) — init-endpoint redaction misses short / bare-token userinfo (residual of #316/L-7)
- **Location:** `redaction.py` `basic_auth` pattern `(://[^/:@\s]*:)([^@\s]{6,})(@)`, surfaced via `cli.py:591,596,598,612`.
- **Description:** the pattern requires a **≥6-char** password and a **`user:pass` colon form**. Two leak classes pass through: short passwords (`http://user:pass@host`, 4-char) and colon-less bare-token userinfo (`http://apitoken12345@host:11434/v1`). `jury init --list-models --local-endpoint http://token@internal/v1` then echoes the credential to stdout / CI logs. L-7's intent ("redact any userinfo before echoing") is not fully met.
- **Fix:** don't rely on `redact()`; strip the userinfo structurally before display (rebuild the netloc from `urlsplit` keeping only `hostname[:port]`, or replace `userinfo@` with `[REDACTED]@`) in a single helper shared by `cli.py` and `doctor.py`. As defense in depth, add a colon-less `(://[^/:@\s]+)(@)` arm to `basic_auth` and lower the min length.

### L-2 (Low) — the `vulnerab`/`exploit` classification keyword stems never match their intended words
- **Location:** `classification.py:69,71` (keyword list) + `:77-79` (`\b…\b` compilation).
- **Verified (empirically):** `is_security_finding(Finding(claim="this is a vulnerability"))` → **False**; `"exploitable bug"` → False; `"exploited"` → False. (Full words like `injection`/`auth` work.)
- **Description:** the `vulnerab`/`exploit` stems are compiled as `\bvulnerab\b`; the trailing `\b` fails inside `vulnerability` (followed by `i`). The stems only match the bare strings "vulnerab"/"exploit", which never occur in natural text.
- **Exploit scenario:** a finding whose only security signal is "vulnerability"/"exploitable" (severity major/minor, no other keyword, not critical) is mis-classified as non-security: `security_sensitive=False`, suppressing the "possible security issue" label and the `needs_human_attention` escalation.
- **Fix:** for prefix stems use a trailing `\w*` instead of `\b` — `r"\bvulnerab\w*"` / `r"\bexploit\w*"` — or add the full words to the list.

### L-3 (Low) — nested redaction (cosmetic / telemetry)
`redaction.py` — for `https://user:AKIA…@host`, `aws_access_key` redacts first, then `basic_auth` re-redacts the `[REDACTED:aws_access_key]` token → `[REDACTED:basic_auth]`, `count=2`. Not a leak (the secret is gone), but the more-informative kind is lost and `redaction_count` (surfaced to the user) is inflated. **Fix:** exclude an existing `[REDACTED:…]` token from the value char-classes, or run the URL-userinfo strip (L-1 fix) before the generic key patterns.

---

## Info / verified-safe

- **TOCTOU between `available()` and `_spawn`** — `build_argv`/`_spawn` pass the bare `spec.command`, so the kernel re-resolves PATH at exec time. No privilege crossing (same uid/PATH); `JURY_REQUIRE_ABSOLUTE_COMMAND` eliminates it. Optional: pass the absolute path to `Popen` in strict mode. Info.
- **`gh --repo <value>` not `--`-guarded** (`github.py`) — `repo` is operator-supplied (not diff-derived), argv is list-form (no shell); unreachable from the diff threat model. Info.
- **Still-missed secret formats** (intentional): GitLab `glpat-`, HuggingFace `hf_`, Google OAuth refresh `1//`, Azure SAS `sig=`. (Anthropic `sk-ant-` is already caught by the `sk-` rule.) Optional additions.
- **No ReDoS in redaction:** `secret_assignment`/base64/pem run ~2 s at a 400k char-run (high constant, but linear thanks to the `{0,40}` bound); diffs are size-capped upstream by `largediff`.

---

## Prioritized fix order

1. **M-1** — fence + neutralize the synthesis verdicts addendum (completes the #316/L-1 fix's coverage).
2. **L-1** — structurally strip userinfo in the init/doctor endpoint display (short / bare-token leak).
3. **L-2** — fix the `vulnerab`/`exploit` classification stems with `\w*`.
4. **L-3** — nested redaction (cosmetic).

> Notes: this report is based on static analysis + manual verification + live stress/ReDoS timing + loopback fuzzing; no dynamic exploit was run. M-1 and L-2 were confirmed empirically in source. All prior fixes (#287–#316) were verified by hand in the v1.5.0 source.
