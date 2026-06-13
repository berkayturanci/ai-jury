# ai-jury — Security Re-Audit (2026-06-13, round 5)

**Date:** 2026-06-13 (fifth pass). **Scope:** `main` @ `66f7fca`.
**Method:** two independent passes — (a) red-team against the round-4 fixes +
a fresh deep dive (SARIF/JSON validity, verdict TL;DR, label/inline argv, cache
canonicalization, redaction completeness, prompt fencing); (b) a from-scratch
review of the deterministic core that feeds the CI gate (consensus grouping/
dedup, voting, `ci.evaluate_ci`, `Finding.from_obj`, classification).
**Result:** no Critical/High. **Two Medium** issues (one a real CI-gate bypass)
and one Low; all fixed this round with tests.

## Fixed this round

### M-1 (Medium) — `_normalize_path` collision lets a forged verdict drop a critical from the CI gate
`consensus._normalize_path` used `str.lstrip("./")`, which removes a whole
leading **run** of `.`/`/` rather than a single `./` prefix. So distinct paths
collide: `.github/workflows/deploy.yml` ≡ `github/workflows/deploy.yml`,
`../auth.py` ≡ `./auth.py`. This function backs finding grouping, sorting, **and**
`orchestrator._verdict_matches_group`. A `critical` finding at
`.github/workflows/deploy.yml` could be swallowed by a verifier `unsupported`
verdict naming the (benign) sibling `github/workflows/deploy.yml` — the critical
lands in the `rejected` bucket and the gate **passes**. This is the first
audit finding that affects the *machine gate*, not just the human report.
**Fix:** strip only a real leading `./` (`while p.startswith("./")`), proven to
keep `.github/x` ≠ `github/x` while still folding `./x` → `x`.

### M-2 (Medium) — invalid SARIF `region.startLine` → whole upload rejected (denial-of-evidence)
`formats._sarif_result` emitted `region.startLine` straight from `Finding.line`
(parsed from attacker-influenceable reviewer JSON, plain `int()`, no range
check). A forged `"line": 0`/negative produces an invalid SARIF region; GitHub
code-scanning rejects the **entire** SARIF upload, suppressing every jury
finding. Prior rounds checked SARIF for *text* injection but not numeric/schema
validity. **Fix:** emit `region` only when `line >= 1`; a non-positive line
drops the region so the finding still surfaces at file level.

### L (Low) — quoted spaced path in a marker-less segment evades `include`
A `diff --git` mode-change segment with a git-C-quoted spaced path
(`"a/x y.py" "b/x y.py"`, no `+++`/`---`) was truncated, hiding the file from an
`include` allow-list. **Fix:** `_path_from_git_header` recovers the quoted b-side
(` "b/` separator). Closes the last path-truncation file-hiding vector.

## Verified clean / HOLDS (re-confirmed under re-attack)
- All round-4 fixes hold: error-string flatten is complete across report/
  formats/patches/github sinks; `authorAssociation` is server-computed and not
  spoofable, read consistently; `_path_from_git_header` halving handles
  odd-length/empty.
- Cache `_canonical` (sorted, tight separators) + full-entry MAC + key↔filename
  binding: no collision. Prompts: every untrusted slot fenced **and**
  neutralized (only the maintainer `{policy}` block is trusted, by design).
- `Finding.from_obj` coercion robust (bool/nan/str/huge/negative `line`,
  case/alias severity normalization); `_max_severity` never down-ranks; grouping
  drops nothing; voting is cosmetic and independent of the gate; `evaluate_ci`
  severity matching is correct for the default `fail_on`.
- Redaction covers `github_pat_`, Slack, GCP, OpenSSH, JWT, Stripe; only bare
  prefix-less high-entropy secrets are out of scope (deliberate, to avoid
  false-positives on commit SHAs).

## Tracked (Low/Info, not raised this round)
- Default `ignore_unverified=True` + auto-depth can skip `verify` on a
  mis-classified security file, leaving criticals unverified (so non-blocking).
  Consider forcing `verify=on` when `security_sensitive`, or failing closed on
  unverified `fail_on` findings when verify didn't run. (Documented default;
  policy decision.)
- The security-keyword path detector misses plural/compound forms (`secrets.py`,
  `crypto.py`, `permissions.py`, …). Broadening risks precision; left as a tuning
  decision. (Affects risk-banding/effort labels, not the gate.)
- Prior Info items unchanged (`_gh_with_input` unbounded write-responses,
  LocalAdapter runtime SSRF seam, policy-from-CWD in CI, `jury init` symlink).
