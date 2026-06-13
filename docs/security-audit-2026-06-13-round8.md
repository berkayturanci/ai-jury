# ai-jury — Security Re-Audit (2026-06-13, round 8)

**Date:** 2026-06-13 (eighth pass). **Scope:** `main` @ `66c3395`.
**Method:** two independent passes — (a) an exhaustive red-team of the round-7
verdict-attach fixes; (b) a whole-codebase convergence sweep.
**Result:** no Critical/High. The convergence sweep found **no new Medium+**
and recommended release; the red-team found **two Medium** CI-gate bypasses in
the verdict→group attachment layer (the same area rounds 5–7 hardened), now
fixed with a structural redesign + tests.

## Fixed this round

### M-1 (Medium) — verdict aimed at a co-located lesser finding could drop a critical
Round 7 required claim-relatedness (Jaccard ≥ 0.5) to reject, but that bar is
too coarse to mean "the verifier named *this* finding," and two mechanisms still
dropped a critical:
- **Merged group:** consensus merges co-located findings of *different*
  severities into one group (keeping the max). A verifier `unsupported` verdict
  dismissing the benign lower-severity member then rejected the whole (critical)
  group.
- **Separate decoy / numbered sibling:** a verdict copying a benign neighbour's
  claim (or a sibling claim differing only by a number, `parse_v2`/`parse_v3`
  — which `_normalize_claim` makes ~0.7 similar) collaterally rejected a
  co-located critical, and one verdict was applied to *every* matching group.

**Fix — `_reject_targets` (structural redesign of `_apply_verdicts`):**
1. **Member-tier guard:** a verdict is "about" the member whose claim it best
   matches; if that member is *less severe* than the group's max, the verdict
   cannot suppress the group (so dismissing a merged-in minor can't drop the
   critical).
2. **Best-tier only:** across candidate groups, suppress only those at the
   highest match similarity — a verdict copying a benign neighbour rejects that
   neighbour (and its duplicate phrasings, which tie) but not a separate,
   less-similar critical. The threshold stays 0.5 so the verifier's legitimate
   paraphrased rejections (it drops the reviewer-name prefix, etc.) still apply.

### M-2 (Medium) — contradictory verdicts were order-dependent (reject-first won)
`_apply_verdicts` was first-match-wins over the verifier's array order, so a
`verified` + `unsupported` pair on one critical resolved by emission order —
attacker-steerable. **Fix:** apply verdicts in blocking-priority order
(`verified` → `needs_human_decision` → `unsupported`), so a blocking judgement is
recorded first and can't be flipped to non-blocking by ordering (fail-closed).

## Verified clean / HOLDS
- Convergence sweep: no new Medium+ across the full module map; all prior fixes
  hold (gate purity, output-injection flatten/fence, exec/sandbox stdin-only,
  SSRF fail-closed, gh argv/cap, path recovery, cache MAC, redaction).
- Round-7 fixes re-confirmed under attack: cross-chunk verdict scoping (uses
  `fold_case=False` paths; empty-file findings excluded), CI reason-line flatten,
  r5/r6 path `./`/case + empty-claim+line.
- Full `evaluate_ci` × status × `ignore_unverified` matrix; `_max_severity` never
  down-ranks; severity unicode lookalikes only down-rank the attacker's own
  finding; vote render-only.

## Tracked (Low/Info — accepted residual / policy)
- A verifier verdict that genuinely best-matches the *sole* critical (no decoy)
  at ≥ 0.5 still rejects it — that is the verifier judging the finding it was
  shown (inherent verifier trust; backstopped by consensus + the
  `ignore_unverified` default + human review), not a collateral-matching bug.
- Default `ignore_unverified=True` (unverified critical non-blocking); chunk
  verdict scoping keys off reviewer-reported paths (defense-in-depth, double-
  gated by claim-relatedness — optional `split_diff`-based hardening); prior
  Info items unchanged.
