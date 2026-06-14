# ai-jury — Security Re-Audit (2026-06-13, round 6)

**Date:** 2026-06-13 (sixth pass). **Scope:** `main` @ `5e59499`.
**Method:** two independent passes concentrated on the CI-gate integrity flow
(the area round 5's worst finding lived in): (a) verify→verdict→group attachment
and `_verdict_matches_group`; (b) re-attack of the round-5 fixes + a fresh sweep
of the comment subcommand, doctor, scaffold, policy, and argv/exec.
**Result:** no Critical/High. **Two Medium** CI-gate bypasses and one Low; all
fixed this round with tests.

## Fixed this round

### M-1 (Medium) — claim-less + line-less verdict was a file-wide gate wildcard
`orchestrator._verdict_matches_group` honored an empty verdict `claim` as a
"match by location," but a verdict with **neither** `claim` **nor** `line`
(only `file` + `status` — a normal verifier output shape) matched **every**
finding group in that file. An attacker-coached `unsupported` verdict of that
shape rejected unrelated `critical` groups (→ `rejected` bucket), flipping the CI
gate FAIL→PASS; under the default `ignore_unverified=True`, mere verdict
*ordering* decided the outcome. **Fix:** an empty-claim match now requires a
concrete `line` on both the verdict and the finding.

### M-2 (Medium) — `_normalize_path` case-collapse rejected criticals on case-sensitive FS
Round 5 fixed the `./`-prefix collision but left the pre-existing `.lower()`.
On a case-sensitive filesystem (Linux — the CI target) `Config.py` and
`config.py` are distinct files, yet folded to the same key, so a verifier
`unsupported` verdict on the benign sibling could swallow a real critical and
pass the gate (same class as round-5 M-1, different vector). **Fix:**
`_normalize_path` gains `fold_case`; grouping/dedup keep case-folding (correct
for case-insensitive filesystems), but the gate-critical
`_verdict_matches_group` comparison is now case-exact (`fold_case=False`,
fail-closed).

### L (Low) — run-metadata report strings not flattened
The metadata block rendered `stop_reason`, skipped agent name/reason, and the
agent table's name/vendor without `flatten_inline`. All are config/internal
today (not attacker-reachable), but inconsistent. **Fix:** flattened for
defense-in-depth so a future source carrying agent/diff text can't break the
table or forge structure.

## Verified clean / HOLDS (re-attacked)
- Other `_verdict_matches_group` matchers: the Jaccard ≥ 0.5 claim path and the
  `abs(line) ≤ 3` proximity do not let a distinct critical be cross-rejected by a
  verdict targeting a nearby benign finding.
- `group_findings` bucketing / `reviewer_count`: `reviewer` is set from real
  agent identity (not attacker JSON); duplicate findings stay `single_reviewer`
  and can't be pushed into `rejected`/`disputed`.
- Severity unicode lookalikes only down-rank the attacker's *own* finding;
  `_max_severity` never down-ranks a group. `MODE_TOO_LARGE` fails closed
  (raises). Representative selection is cosmetic.
- Round-5 fixes hold: `_normalize_path` `./` loop (backslash/homoglyph/`..`/
  repeated/trailing — only the case vector above), SARIF `region` guard
  (float/bool/huge/string lines), quoted-path recovery (nested quotes, `--cc`,
  `" b/"` inside a name; and `split_diff` always prefers the `+++` marker).
- `jury comment`/`commands.parse_comment`: strict allowlist → `--rounds <1-3>`;
  fuzzed inputs (`0x10`, `1e1`, `; rm -rf /`, embedded `--pr`, multi-line) all
  rejected/bounded; `--print-args` uses `shlex.quote`.
- argv/exec: attacker diff travels on **stdin** for every adapter (incl. the
  unknown-vendor fallback); `gh` uses argv lists with `--`/`--input -` guards.
- `policy.py` trusted slot is fed only by the maintainer TOML (attacker diff
  never reaches it); `doctor.py` redacts secrets/endpoints; `scaffold.py` checks
  agent names and TOML-escapes values.

## Tracked (Low/Info — policy/tuning, unchanged)
Default `ignore_unverified=True` + auto-depth verify-skip; security-keyword path
detector misses plural/compound forms; prior Info items.
