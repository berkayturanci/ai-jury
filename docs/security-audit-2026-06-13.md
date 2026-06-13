# ai-jury — Security Audit Report (2026-06-13)

**Date:** 2026-06-13 (post-v1.6.1)
**Scope:** the whole `src/ai_jury/` codebase, `main` @ `4dd9d5f`.
**Background:** seventh-round audit. Follows the v1.6.0 re-audit
([security-audit-2026-06-07-v1.6.0.md](security-audit-2026-06-07-v1.6.0.md)),
re-verifying that the #287–#322 fixes still hold and hunting for anything new
introduced since (notably the #336 combined-regex change and the website work).
**Method:** four parallel independent static reviews across the attack surface
(subprocess/sandbox, network/SSRF, prompt-injection/redaction,
filesystem/cache + classification), with key claims confirmed empirically
(loopback-encoding bypasses, IMDS, userinfo tricks, redirect/scheme abuse, cache
HMAC tamper/forgery, combined-regex equivalence fuzz, ReDoS timing,
homoglyph-fence bypass).

---

## Executive Summary

**No Critical or High findings.** The core architectural defense — the CI gate
and vote tally are pure functions of the *structured* findings/verdicts, so
injected free-text cannot steer the pass/fail outcome — was re-verified and
holds. This round surfaced **two genuine Medium prompt-injection gaps** and
several Low/Info items. The two Mediums plus two of the Lows (continuations of
prior Info notes) **were fixed in this pass** and are covered by new tests.

| Severity | Count | Status |
|----------|-------|--------|
| Critical | 0 | — |
| High | 0 | — |
| Medium | 2 | **both fixed this pass** |
| Low | 4 | 2 fixed, 2 open |
| Info | 3 | open (optional hardening) |

The previously-merged #336 (combined security-keyword regex in `diffprofile`)
was **verified equivalent** to the prior per-regex `any()` (200k-input fuzz, 0
mismatches) and **free of catastrophic backtracking** (pure alternation of
`\b…\b` / `\b…\w*`; ~1 ms on 100k adversarial input).

---

## Findings

### M-1 (Medium) — debater's own round-1 review was not fenced — **FIXED**
`prompts.py` `DEBATE` / `DEBATE_ISSUE`: every untrusted-derived peer slot sat
inside a `<<<UNTRUSTED_REVIEW … UNTRUSTED_REVIEW>>>` fence except the
debater's *own* round-1 review, which appeared bare under a "YOUR ROUND-1
REVIEW" heading. Because that review transitively quotes the attacker diff and
the standing notice tells the model to distrust only *fenced* blocks, injected
text surviving into the reviewer's own output landed in a region the preamble
implicitly treats as trusted. It was already run through `neutralize_sentinels`
(`orchestrator.py:269`), so this was the missing *structural* boundary.
**Fix:** wrap the `own_review` slot in `UNTRUSTED_REVIEW` in both templates and
bump `PROMPT_VERSION` 3 → 4 (cache invalidation).

### M-2 (Medium) — `neutralize_sentinels` only defended ASCII angle brackets — **FIXED**
`prompts.py`: `_OPENER_RE`/`_CLOSER_RE` matched literal `<<<`/`>>>` only. A
fence forged from fullwidth/homoglyph brackets (e.g. `＜＜＜UNTRUSTED_DIFF`,
U+FF1C) survived neutralization while still reading as a real fence to an LLM
that treats the homoglyphs as equivalent.
**Fix:** broaden the angle-run classes to include common left/right-angle
homoglyphs (fullwidth U+FF1C/U+FF1E, guillemets, math/CJK/ornament angle
brackets) and match a run of 3+ adjacent to the `UNTRUSTED_` marker. Benign
`<<<`/`>>>` not adjacent to a marker (e.g. git conflict markers) remain
untouched.

### L-1 (Low) — `redact_url_userinfo` leaked credentials for scheme-less URLs — **FIXED**
`redaction.py`: a scheme-less endpoint (`user:pass@host/v1`) makes `urlsplit`
leave `netloc` empty (authority lands in scheme/path), so the `"@" not in
netloc` early-return handed the credential back verbatim. Config-controlled, so
Low, but a direct defeat of the function's sole purpose (continues v1.5.0/L-1,
noted Info in the v1.6.0 audit).
**Fix:** when `netloc` has no `@` but the URL has no `://` authority and
contains `@`, re-parse with a synthetic `//` authority, redact, and strip it
back off. Verified for `user:pass@host/v1`, `:secret@host`, and the no-userinfo
case `host:11434/v1` (returned verbatim).

### L-2 (Low) — no pre-filter byte cap on diff ingestion — **FIXED**
`cli.py` `_read_diff`: `sys.stdin.read()` / `fh.read()` pulled the entire diff
into memory; `diff.max_bytes` only applies *after* the split, so a multi-GB
`--diff-file`/stdin could OOM the process first (local DoS; continues the
v1.6.0 audit Info note).
**Fix:** read through `_read_capped` with a 64 MiB hard ceiling (far above any
review budget), rejecting larger inputs with a clear error.

### L-3 (Low) — `gh` binary not covered by `JURY_REQUIRE_ABSOLUTE_COMMAND` — **OPEN**
`github.py:23,302` resolve `gh` via bare `shutil.which("gh")` unconditionally;
the agent-command hardening (`config.py`) does not extend to `gh`, which carries
the user's GitHub token. Only exploitable if `PATH` is already attacker-
controlled (pre-existing compromise). *Suggested fix:* when
`JURY_REQUIRE_ABSOLUTE_COMMAND` is set, also require/resolve `gh` from an
absolute path (or a `JURY_GH_PATH`), and have `doctor` report `which gh`.

### L-4 (Low) — `diff --git` header parser truncates paths with spaces — **OPEN**
`largediff.py` `split_diff` takes the path via `line.split()[3]`, so
`diff --git a/safe.py b/evil with space.py` yields path `evil`. Git also
quotes such paths (`"b/evil with space.py"`), which the parser doesn't unquote.
Impact is bounded — the file is still kept and fully shown to the agents; only
the classification *label* and include/exclude glob matching see the truncated
string, so a security-keyword path token or a glob rule can be evaded.
*Suggested fix:* parse git's real header (handle quoted paths; prefer the
`+++ b/…` line or split on the ` b/` separator).

### Info — open, optional hardening (not attacker-reachable today)
1. **`LocalAdapter` SSRF gate is config-time, not at the `_open` seam**
   (`adapters.py`). Safe on every validated config path and the opener blocks
   non-http/redirects; `doctor` loads config without `validate=True`, so the
   loopback host check isn't unconditional. *Fix:* call `_endpoint_issues`
   inside `_open` (as `list_local_models` already does).
2. **TRUSTED policy section is loaded from CWD** (`policy.py`:
   `.jury/policy.toml` / `jury-policy.toml`). It is rendered un-neutralized as
   trusted by design. In a CI workflow that checks out and runs `jury` from a
   **fork/PR head**, an attacker-supplied policy file could inject directives
   the agent obeys (e.g. an "emit empty findings" checklist). Blast radius is
   limited by the consensus gate, but a trusted "approve everything" directive
   could subvert the per-reviewer structured output the gate depends on.
   *Fix:* load policy from a pinned **base-ref** path in CI, never from the PR
   head; document this requirement.
3. **`jury init` follows symlinks on the scaffold write** (`cli.py`). Guarded
   by `exists()` + `--force`, but a pre-planted `jury.toml` symlink would be
   written through. Requires attacker write access to the user's CWD beforehand.
   *Fix:* `O_NOFOLLOW`/`O_EXCL` open (as the cache writes already do).

---

## Confirmed solid (re-verified, no change needed)

- **CI gate / vote tally are injection-proof by construction** — `ci.evaluate_ci`
  and `voting.tally_votes` decide purely from `FindingGroup.severity` + verifier
  `status`; no free-text path overrides the structured tally. This is the
  authoritative defense and it holds.
- **Every other untrusted slot is fenced *and* neutralized** across all four
  rounds (review, debate incl. `prior_txt`, verify, synthesis incl. the
  verdicts addendum) — the v1.5.0/M-1 fix holds; `evidence` is never
  interpolated.
- **Subprocess/sandbox:** no `shell=True`/`os.system`/`eval`; diff delivered on
  **stdin** only (never argv); sandbox flags enforced at the adapter layer and
  not removable by config; unknown vendor fail-closed; timeout kills the whole
  process group; response body capped at 16 MB.
- **SSRF:** loopback allow-list blocks decimal/octal/IPv4-mapped IPs, `0.0.0.0`,
  IMDS, userinfo smuggling, and redirect-to-internal (no redirect handler
  registered); remote opt-in lives in the environment, outside attacker-
  controlled config. No TLS verification disabled anywhere.
- **Cache:** SHA-256 key over all outcome-affecting inputs; per-user HMAC
  (0o600, `O_EXCL`), constant-time compare, JSON-only (no pickle/eval),
  key-to-filename binding, group/other-writable dir refusal, atomic write (no
  symlink follow), `clear()` scoped to the 64-hex `.json` shape.
- **#336 combined regex:** equivalent to the old per-regex `any()` and free of
  ReDoS (verified by fuzz + timing).
- **Determinism:** no wall-clock/random in any logic module (`secrets` is only
  the HMAC key, never in output).

---

## Remediation status

Fixed this pass (with tests): **M-1, M-2, L-1, L-2.**
Tracked open: **L-3, L-4** (Low) and the three Info items — none attacker-
reachable in the default local-run threat model; L-4 and the policy-provenance
Info item are the most worthwhile next hardening steps for CI integrations.
