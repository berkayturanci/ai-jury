# ai-jury — Security Re-Audit (2026-06-13, round 7)

**Date:** 2026-06-13 (seventh pass). **Scope:** `main` @ `b1ec1e1`.
**Method:** two independent passes — (a) an exhaustive probe of the CI-gate
flow (verify→verdict→group→`evaluate_ci`) with the full status × `ignore_unverified`
matrix; (b) a wide net over chunked-merge verdict scoping, cache-key
completeness, the CI reason line, gh argv/refs, and silent-failure paths.
**Result:** no Critical/High. **Three Medium** issues (all CI-gate / posted-
comment integrity) fixed this round with tests.

## Fixed this round

### M-1 (Medium) — empty-/unrelated-claim verdict collaterally rejects a co-located finding
Round 6 required a `line` for an empty-claim match, but distinct findings share
a line and one verdict is applied to *every* matching group, so a line-ful but
claim-less `unsupported` verdict (a normal "refer-by-position" shape, attacker-
steerable) also rejected a co-located `critical` → `rejected` bucket → gate
PASS under the strict (`ignore_unverified=False`) config. **Fix (fail-closed):**
a *rejecting* verdict (`unsupported`/`needs_human_decision`) may only suppress a
finding it actually names — `_apply_verdicts` now requires claim-relatedness
(exact or Jaccard ≥ 0.5) before honouring a rejection. Location-only verdicts
can still *verify* by position, never reject. Closes the "comb" file-wide
amplification too.

### M-2 (Medium) — cross-chunk verdict cross-attachment
On a chunked (large-diff) review, `_merge_chunk_outcomes` pooled all chunks'
verdicts and re-applied them to the globally-merged groups. A verdict produced
while verifying chunk B (whose attacker-controlled diff text could steer it, but
which saw only chunk B's findings) could then reject a real `critical` from
chunk A → gate FAIL→PASS. This violated the "free text can't steer the
structured gate" invariant across the chunk boundary. **Fix:** chunks are
file-disjoint, so each chunk's verdicts are now applied only to groups whose
location is one of that chunk's files.

### M-3 (Medium) — CI gate reason line rendered finding text unflattened
`ci.evaluate_ci` built the FAIL reason from the blocking finding's `file`/`claim`
without `flatten_inline`. That reason becomes the `CI gate` section of the
comment posted to the PR, so a multi-line `claim` (attacker-influenceable) could
forge a `## Verdict APPROVE` heading or a hidden `<!-- arc-inline -->` marker —
the output-injection class rounds 3/4 closed everywhere else but missed here.
**Fix:** flatten `file`/`claim` in the reason line (`ci.py` stays pure).

## Verified clean / HOLDS (matrix probed)
- Full `evaluate_ci` × status({none,verified,unsupported,needs_human_decision})
  × `ignore_unverified`: a `verified` critical always blocks; a critical is
  non-blocking only when unverified/unsupported under the documented defaults.
  `blocker` alias folds to `critical`. No display-vs-rank mismatch.
- `group_findings` merge-masking impossible (`_max_severity` never down-ranks;
  representative is most-severe). Severity unicode lookalikes only down-rank the
  attacker's own finding. `--decision vote` is render-only; exit code always
  from `evaluate_ci`. `MODE_TOO_LARGE` fails closed.
- Cache key covers every outcome-affecting input (include/exclude/context/verify/
  redact/fail_on differ → different keys); `--fail-on`/`--decision` are applied
  *after* cache load so a stale entry is always re-gated with current flags.
- gh argv/refs use `--` separators; `compare_diff` base is hex-constrained +
  trusted-author; JSON/SARIF escape attacker text; no `# nosec`/TODO; broad
  `except` sites are diagnostics only and never swallow a security failure into a
  silent PASS; the gate is independent of dict/set iteration order.

## Tracked (Low/Info — policy/tuning, unchanged)
Default `ignore_unverified=True` makes an unverified critical non-blocking
(consider failing closed on `needs_human_decision`/unverified for `fail_on`
severities — a policy decision); security-keyword path detector misses
plural/compound forms; prior Info items.
