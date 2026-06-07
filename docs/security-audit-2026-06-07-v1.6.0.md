# ai-jury — Security Re-Audit Report (v1.6.0)

**Date:** 2026-06-07 (post-v1.6.0 release)
**Scope:** the whole `src/ai_jury/` codebase, `main` @ v1.6.0
**Background:** this is the current Claude security analysis. Across six rounds (v1.3.0 → v1.6.0), the findings from earlier audits were fixed and shipped as #287–#322; this report verifies those fixes hold in the released v1.6.0 source and hunts for anything remaining or new. (The companion [Codex Security Scan — 2026-06-07](security-scan-2026-06-07.md) is the independent Codex analysis.)
**Method:** four parallel independent static reviews across the attack surface (subprocess/sandbox, network/SSRF, prompt-injection/redaction, filesystem/cache + classification), with the key claims confirmed empirically (alternate loopback encodings, IMDS, userinfo tricks, redirect/scheme abuse, cache HMAC tamper/forgery, ReDoS timing).

---

## Executive Summary

**This is the first round with no Critical, High, *or* Medium finding.** Every #287–#322 fix is verified present and holding in the v1.6.0 source, and the four findings closed in v1.6.0 (the M-1 verdicts-slot fence, and the L-1/L-2/L-3 bundle) all hold against the actual released code:

- **M-1** — the synthesis `VERIFICATION VERDICTS` addendum is now wrapped in a `<<<UNTRUSTED_FINDINGS … UNTRUSTED_FINDINGS>>>` fence **and** run through `neutralize_sentinels`. Every untrusted slot reaching an agent prompt is now both fenced and neutralized; the only un-neutralized slot (`{policy}`) is a local, maintainer-authored, size-capped file that is legitimately trusted.
- **L-1** — endpoint userinfo is stripped structurally via the new `redaction.redact_url_userinfo` helper; short and colon-less userinfo no longer leak. `redact()` also gained a colon-less arm + lower password bound for the diff-scrub path.
- **L-2** — the `vulnerab`/`exploit` classification prefix stems now match their full word families (`vulnerability`/`exploitable`/…) via a trailing `\w*`, with no over-match and `auth` still not firing inside `author`.
- **L-3** — redaction no longer re-redacts an already-emitted `[REDACTED:…]` marker (informative kind + accurate count preserved).

Only **Info / defense-in-depth** notes remain, all on paths that are **not attacker-reachable**.

| Severity | Count | Findings |
|----------|-------|----------|
| Critical | 0 | — |
| High | 0 | — |
| Medium | 0 | — |
| Low | 0 | — |
| Info | 5 | below — all optional hardening, none exploitable |

---

## Confirmed-Fixed — all prior fixes hold (v1.6.0)

- **Prompt-injection coverage is now complete.** Every `.format(`/`prompt +=` site in `orchestrator.py` that injects untrusted text (review, debate incl. `prior_txt`, verify, synthesis incl. the new verdicts addendum) is both fenced and neutralized. `neutralize_sentinels` is sentinel-agnostic (`_CLOSER_RE = (UNTRUSTED_[A-Z]+\s*)>>>`), so a forged `UNTRUSTED_FINDINGS>>>` closer inside a verdict is broken to `UNTRUSTED_FINDINGS>·>·>` — verified.
- **Least privilege is fail-closed** (`privilege.py`). Unknown vendor → `--sandbox` injected (sandboxed or the CLI errors on the unknown flag — never runs unsandboxed); `vendor=="local"` checked first; `=`-form `-s=`/`--sandbox=` recognized; the mandatory claude write-tool denylist can't be narrowed by config. Prompts are delivered on stdin, never argv; timeouts kill the whole process group.
- **SSRF is fail-closed** (`config._endpoint_issues`). With no opt-in, only `127.0.0.1`/`localhost`/`[::1]` pass; blocked: decimal/hex/octal-encoded loopback, `0.0.0.0`, `[::ffff:127.0.0.1]`, IMDS `169.254.169.254`, `file://`/`ftp://`/`gopher://`, userinfo-host smuggling (`http://127.0.0.1@evil.com/`), malformed `http://[::1`. `jury init --local-endpoint` runs the same gate **before** any socket (#309 holds). The HTTP opener registers no file/ftp/redirect handler and does not disable TLS verification.
- **Cache integrity is sound** (`cache.py`). SHA-256 digest filenames (path traversal impossible); per-user HMAC fail-closed on content tamper, MAC forgery, and cross-key copy; `clear()` only deletes the 64-hex `.json` shape (no blast radius in a shared `JURY_CACHE_DIR`); atomic `mkstemp` (O_EXCL, no symlink-follow) + `replace`; read bounded before parse; fail-closed on a group/other-writable dir.
- **Dangerous classes absent:** no `pickle`/`eval`/`exec`/`yaml`/`extractall`/`ZipFile`/`tarfile`/`mktemp`/`shell=True`; no path-traversal sink reachable from untrusted input. The report/format/patch/metadata modules are pure (return strings, no file I/O).

---

## Info / defense-in-depth (none exploitable, none attacker-reachable)

1. **`redact_url_userinfo` under-redacts a *scheme-less* URL** (`redaction.py`). `redact_url_userinfo("user:pass@host")` returns the string unchanged: `urlsplit` parses `user` as the scheme and the credential lands in `path`, so `"@" not in netloc` returns early and the `ValueError`→`redact()` fallback never fires. **Not reachable:** both callers (`cli.py`, `doctor.py`) pass a configured local-model endpoint that defaults to a full `http://…` URL and is maintainer-supplied, not attacker-controlled (a scheme-less endpoint would also fail the actual HTTP call). *Optional fix:* when `netloc` is empty but `path` contains `@`, fall through to `redact()`.
2. **No hard byte cap on the diff before `redact()`** (`orchestrator.py`). `redact()` is linear (verified: ~1.3 ms/100 KB, doubling input doubles time — the `{0,40}` bounds and negated classes prevent backtracking), so this is a linear-cost DoS-adjacent note, **not** ReDoS. A top-level input byte cap would harden against resource exhaustion on a multi-MB diff. (`largediff` already filters/chunks downstream.)
3. **`LocalAdapter.run()/available()` rely on config-time SSRF validation, not a runtime gate** (`adapters.py`). Safe today because every config path validates the endpoint and the opener blocks non-http/redirects. *Optional hardening:* call `_endpoint_issues` at the `_open` seam (as `list_local_models` already does) so the gate is unconditional regardless of how a `LocalAdapter` is constructed.
4. **Unknown-vendor sandbox injection leaves the original flags in place** (`privilege.py`). `enforce_read_only("acme","x",["--yolo"])` → `["--sandbox","--yolo"]`. Mitigated in practice (an agy-compatible CLI honors `--sandbox`; an incompatible one errors on the unknown flag, so it never runs unsandboxed). Low value to change.
5. **Loopback allow-list is string-based, not resolved-IP based** (`config.py`). `localhost` could be repointed by a hostile `/etc/hosts`/resolver — the classic but low-severity case for a host the operator controls. Resolving and checking the IP would close it at some complexity cost; acceptable at the current threat model.

> Informational secret-format coverage gaps (deliberate, optional future additions): Azure SAS (`sig=`), GitLab `glpat-`, generic 40-hex AWS *secret* keys (excluded to avoid SHA false positives), Twilio SIDs (identifiers, not secrets).

---

## Prioritized follow-up

There is **nothing required**. If you want to chip away at defense-in-depth, the cheapest and most self-contained item is Info #1 (make `redact_url_userinfo` fall through to `redact()` on a scheme-less URL); Info #3 (gate `_open` unconditionally) is the most valuable architecturally.

> Notes: this report is based on four independent static reviews + manual verification + live SSRF/loopback fuzzing + cache HMAC tamper/forgery tests + ReDoS timing; no dynamic exploit was run. All prior fixes (#287–#322) and the four v1.6.0 fixes were verified against the released v1.6.0 source.
