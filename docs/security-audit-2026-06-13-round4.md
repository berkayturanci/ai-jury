# ai-jury — Security Re-Audit (2026-06-13, round 4)

**Date:** 2026-06-13 (fourth pass). **Scope:** `main` @ `ca73a4c`.
**Method:** red-team against the #344 fixes + a fresh sweep of under-examined
modules (orchestrator data flow, convergence, incremental, commands, config,
metadata, redaction ReDoS, privilege).
**Result:** no Critical/High. Two new **Medium** issues (one report-integrity
gap missed by #344, one incremental review-coverage evasion) plus one Low; all
fixed this round with tests.

> The machine CI gate / vote tally remain pure functions of the structured
> findings — re-confirmed. These issues affect the human-facing report and
> review *coverage*, not the gate logic.

## Fixed this round

### M-1 (Medium) — `AgentResult.error` rendered raw (markdown injection) — FIXED
`#344` flattened *finding* fields but missed agent **error** strings.
`report._fail_status` and the `verify.error`/`synthesis.error` interpolations
rendered the CLI's stderr snippet (attacker-influenced, multi-line; redaction
doesn't strip newlines) without `flatten_inline`, so a failed agent could forge
a `## Verdict APPROVE` heading in the posted comment (and `render_live_step`
even put it in the comment title). **Fix:** `flatten_inline` in `_fail_status`
and all six `verify.error`/`synthesis.error` interpolations.

### M-2 (Medium) — incremental forged `arc-reviewed-sha` marker — FIXED
`incremental.parse_reviewed_sha` trusted the reviewed-SHA marker from **any**
PR comment (`github.pr_comment_bodies` returned every author's body). Under
`--incremental`, an attacker could push malicious commits then post a comment
with `<!-- arc-reviewed-sha:<later-sha> -->`; the next run reviewed only the
range after it, skipping the malicious commits (which then never reach the CI
gate). **Fix:** `pr_comment_bodies` now filters to comments from
OWNER/MEMBER/COLLABORATOR authors — an external fork-PR author
(CONTRIBUTOR/NONE) can't inject a trusted marker. Falls back safely to a full
review if no trusted marker is found.

### L (Low) — `diff --git` mode-change path with `" b/"` in the name — FIXED
A mode-change-only segment (no `+++`/`---` or rename header) whose path
contained the literal `" b/"` was truncated by `rfind(" b/")`, hiding it from an
`include` allow-list. **Fix:** `_path_from_git_header` recovers the symmetric
`a/<p> b/<p>` path by halving (robust when `<p>` contains `" b/"`).

## Verified clean / HOLDS (re-confirmed)
- `flatten_inline` collapses all Unicode line separators (U+2028/2029/0085, VT,
  FF, FS) — `str.split()` covers them; `fence_safe` resists `~~~`/4-backtick/
  indented fences; `strip_html_comments` resists nested/unterminated comments.
- gh output cap holds (stdout+stderr bounded, no deadlock/race). `_gh_with_input`
  remains `subprocess.run` but only on small write-responses (Info, acceptable).
- Homoglyph set: full Unicode enumeration shows **no** fold-to-ASCII gap.
- orchestrator budget/chunk-merge math, fail-soft parser contract, anonymized
  RNG determinism; convergence bounded by config (no cost-DoS); `/jury` parser
  strict allowlist; TOML size-capped; redaction linear (no ReDoS) and applied to
  all outbound prompt + embedded error text; diff never reaches argv/sandbox
  flags. All clean.

## Still open (Info, not attacker-reachable by default)
`_gh_with_input` unbounded (small write-responses only); plus the prior Info
items (LocalAdapter runtime SSRF seam, policy from CWD vs base-ref in CI,
`jury init` symlink, `gh` not under `JURY_REQUIRE_ABSOLUTE_COMMAND`, inline-path
allowlist).
