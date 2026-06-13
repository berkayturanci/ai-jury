# ai-jury — Security Re-Audit (2026-06-13, round 9)

**Date:** 2026-06-13 (ninth pass). **Scope:** `main` @ `f452972`.
**Method:** two independent passes — (a) a determined final red-team of the
round-8 verdict-attach redesign; (b) an independent whole-codebase convergence
confirmation.
**Result:** no Critical/High. The convergence pass **independently confirmed no
new Medium+ and recommended release**; the red-team found **one** more Medium
verdict-attach variant (line-less verdict + similarity tie), now fixed with
tests.

## Fixed this round

### M-1 (Medium) — line-less, claim-ful verdict + similarity tie drops a co-located critical
Two compounding weaknesses survived round 8:
1. `_verdict_matches_group` only enforces line proximity when the verdict
   carries a line. A verdict with a real claim but `line: null` (a documented
   verifier output shape) matched *every* same-file group at Jaccard ≥ 0.5 — a
   file-wide-by-claim wildcard (the claim-ful counterpart of the round-6
   line-less wildcard).
2. `_reject_targets` kept all groups at the maximum `_claim_sim`. Since Jaccard
   ties are easy to craft, a critical and a benign decoy could tie at the top
   similarity and *both* be rejected.

Result: an attacker-steered `unsupported` verdict dropped a real critical and
flipped the gate (under the strict config and in the chunked path).

**Fix — two more fail-closed rules in `_reject_targets`:**
- **Line required:** a rejecting verdict with no line suppresses nothing (it is
  too imprecise; mirrors the round-6 line-less fix for the claim-ful case).
- **Least-severe within a tie:** if the top-similarity tier still spans
  severities, suppress only the *least*-severe groups, so a tie can never drag a
  critical down alongside a benign decoy.

Combined with the round-8 member-tier guard and best-tier selection, a rejecting
verdict can now only suppress the specific, co-located, least-severe finding it
names — never a real critical it merely overlaps.

## Verified clean / HOLDS
- Independent convergence pass: **no new Medium+** across the full module map
  (ReDoS-free regexes; all 8 prompt templates fenced+neutralized; SSRF
  fail-closed; gh argv/cap/refs; cache key+HMAC; no dangerous primitives;
  read-only sandbox fail-closed). Recommends release.
- Round-8 verdict-attach defences re-confirmed under fresh probing (empty-claim,
  merged-member, best-tier, contradiction ordering all hold); rounds 5/6/7
  path/case/wildcard/cross-chunk/reason fixes hold.

## Tracked (Low/Info — accepted residual / policy, unchanged)
A verifier verdict with a matching line that genuinely best-names the *sole*
critical still rejects it (inherent verifier trust, backstopped by consensus +
the `ignore_unverified` default + human review); raw agent transcript in the
posted report is rendered un-fenced by design (independent of the structured
gate); default `ignore_unverified=True`; plus the prior tracked Info items
(`gh` PATH, LocalAdapter runtime SSRF seam, policy-from-CWD-in-CI, `jury init`
symlink, inline-path allowlist, `_gh_with_input` unbounded write-responses,
security-keyword plural/compound forms).
