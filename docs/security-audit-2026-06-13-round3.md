# ai-jury — Security Re-Audit (2026-06-13, round 3)

**Date:** 2026-06-13 (third pass, after #342/#343 landed).
**Scope:** `main` @ `9498faf`. Two parallel agents: a **red-team** pass against
the #343 fixes + the items those reports deferred, and a **fresh full-surface
sweep** focused on areas earlier rounds covered less (report/SARIF rendering,
GitHub post paths, consensus/grouping, cache/metadata).
**Result:** no Critical/High. Found a new attack *class* the prior rounds
missed — **markdown output-injection into the report posted verbatim to the
PR/issue** (two Mediums) — plus several Lows and the deferred gh-output cap.
**All actionable items below were fixed this round, each with tests.**

> The machine CI gate / vote tally remain pure functions of the *structured*
> findings and verdicts — re-confirmed again. None of these issues change the
> exit code; they concern the **human-facing report** and resource/robustness.

---

## Findings fixed this round

### M-1 (Medium) — suggestion-fence breakout in `--suggest-patches` — FIXED
`patches.py` rendered a finding's `suggested_fix` raw inside a
```` ```suggestion ```` block. A fix containing a fence closer (```` ``` ````)
plus a forged `## Verdict / APPROVE` escaped the block and injected markdown
into the comment posted to the PR. **Fix:** `fence_safe()` collapses 3+
backtick/tilde runs in the body; the heading text is `flatten_inline()`-d.

### M-2 (Medium) — finding text forges headings/fences in the report body — FIXED
`report.py` rendered `claim`/`evidence`/`suggested_fix`/`status_reasoning`/`file`
without neutralization. A multi-line `claim` could open a `## Verdict` heading or
an unterminated code fence in the posted comment (and confuse downstream greps).
**Fix:** `flatten_inline()` collapses these attacker-influenced fields to a
single line in `_finding_line`/`_group_line`; markdown structure (headings,
list items, fences) requires a line start, so flattening neutralizes it.

### N-3 (Low) — finding can forge the hidden inline-comment markers — FIXED
`github._comment_body` interpolated `claim`/`suggested_fix` next to the jury's
`<!-- arc-inline -->` / `<!-- arc-sig:… -->` markers; an embedded marker could
forge a "jury comment" or perturb inline-comment dedup. **Fix:**
`strip_html_comments()` removes `<!--…-->` from those fields before building the
body.

### D-1 (Low→Med) — unbounded `gh` output on the `--pr`/`--issue` path — FIXED
The deferred item, and the most attacker-reachable: `github._gh` used
`subprocess.run(capture_output=True)`, buffering the **entire** `gh` stdout — a
hostile huge PR diff could OOM before the diff budget engaged (the #343 byte cap
only covered `--diff-file`/stdin). **Fix:** stream stdout via `Popen` with a
bounded `read(cap+1)` and a 64 MiB ceiling; stdout/stderr are drained on
separate threads (no pipe-deadlock) and the timeout/kill behavior is preserved.

### L-2 (Low) — `diff --git` truncation for marker-less segments — FIXED
The #343 fix recovered paths from `+++`/`---` lines, but pure
renames/copies/mode-changes have **no** marker lines and fell back to
`split()[3]`, truncating a space-named path (hiding it from an `include`
allow-list). **Fix:** `_path_from_git_header` splits the header on the last
`` b/`` separator, and the loop reads `rename to`/`copy to` extended headers.

### R-3 (Low) — homoglyph fence: presentation-form gap — FIXED
NFKC enumeration confirmed the dangerous class (chars that fold to ASCII `<`/`>`)
is fully covered. Added the vertical presentation-form angle brackets
(U+FE3D–FE40) for parity with the CJK angle brackets already in the set.
Defense-in-depth; bumps `PROMPT_VERSION` 5 → 6.

### Robustness — `cache.load` / `github` json.loads `RecursionError` — FIXED
For parity with the #343 `findings.py` fix, `cache.load` and the `github`
`json.loads` call sites now also catch `RecursionError` on deeply nested JSON
(neither is attacker-reachable today; one-line consistency fixes).

---

## Verified clean / HOLDS (re-confirmed)
- **JSON & SARIF output** (`formats.py`) — built as native dicts, `json.dumps`
  escapes attacker text; no structural injection. The markdown path was the only
  output-injection surface.
- **#343 fixes hold:** byte-accurate ingest cap (incl. multibyte/surrogate),
  CLI-adapter error redaction, `findings` RecursionError, marker-bearing path
  recovery (quoted/CRLF/tab).
- **CI gate / vote tally / consensus grouping** — a low-severity duplicate can
  only change the *display* representative, never lower a group's severity below
  `fail_on`; `commands.py` `/jury` parser is a strict allowlist; `classification`
  can only *raise* security-sensitivity. All re-confirmed.
- Subprocess stdin-only + process-group kill; SSRF loopback gate + no-redirect
  opener; cache HMAC integrity; no pickle/eval/yaml/tar/zip; ReDoS-free regexes.

## Still open (Info, not attacker-reachable by default)
- Inline-comment `path` taken raw from a finding (`github.build_inline_payload`)
  — bounded: GitHub's reviews API rejects comments on paths absent from the PR
  diff, so it can't anchor to an arbitrary repo file (a defensive path-allowlist
  is the optional hardening).
- Prior Info items unchanged: LocalAdapter runtime SSRF seam, policy loaded from
  CWD vs base-ref in CI, `jury init` symlink, `gh` not under
  `JURY_REQUIRE_ABSOLUTE_COMMAND`.
