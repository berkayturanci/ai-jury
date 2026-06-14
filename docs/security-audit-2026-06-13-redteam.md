# ai-jury — Security Re-Audit (2026-06-13, red-team round)

**Date:** 2026-06-13 (same day, after the seventh audit's fixes landed in #342).
**Scope:** `main` @ `e150ff8`. Two parallel agents: (a) a **red-team** pass that
adversarially attacked each #342 fix to find bypasses, and (b) a **fresh
full-surface sweep** for anything new or missed.
**Result:** no Critical/High. The red-team found the homoglyph fix incomplete
and the ingest cap byte-inaccurate; the sweep found three new Low issues. **All
six items below were fixed in this round, each with tests.**

---

## Red-team findings against the #342 fixes

### R-1 — homoglyph fence neutralization was incomplete — **FIXED**
The first homoglyph set (7 codepoints/side) missed many angle lookalikes that an
LLM reads as `<`/`>`. Proven survivors: `﹤﹥` (U+FE64/65, which **NFKC-fold to
ASCII `<`/`>`**), `❮❯` (U+276E/F), `≪≫` (U+226A/B), `ᐸᐳ` (Canadian syllabics),
`«»` (guillemets), and **mixed runs** like `<<﹤` (2 ASCII + 1 homoglyph). Any of
these could forge a real `UNTRUSTED_*` fence boundary and — since `own_review`
and the other slots are fenced — break out of the data block.
**Fix:** broaden `_LANGLES`/`_RANGLES` to a comprehensive curated set
(`prompts.py`); membership is per-character so mixed ASCII/homoglyph runs of 3+
adjacent to the marker are broken too. Empirically re-verified all listed
bypasses are now neutralized and benign content (git conflict markers) is
untouched. Bumps `PROMPT_VERSION` 4 → 5. A character class is inherently an arms
race; this is defense-in-depth and the structured-consensus gate remains the
authoritative protection.

### R-2 — ingest cap counted characters, not bytes — **FIXED**
`_read_capped` used `fh.read(CAP+1)` on a **text** stream, so a UTF-8 input of
multi-byte chars (e.g. `界`×N, `😀`×N) was admitted at **3–4× the intended 64
MiB** before the cap fired; the error string even said "byte".
**Fix:** read from the **binary** stream (`sys.stdin.buffer` / file opened
`"rb"`) and enforce on byte length, decoding only after the check (`cli.py`).
A text stream is still accepted (measured by UTF-8 length) for test doubles.

### R-3 — redaction holds; cosmetic only — **NO FIX NEEDED**
~30 adversarial URLs (percent-encoded `@`, multiple `@`, scheme-less IPv6,
uppercase scheme, query-string `@`) — **no credential leaked**. The only blemish
is a dropped scheme on single-colon non-`//` URLs (`mailto:user:pass@host` →
`[REDACTED]@host`), which loses no secret. Left as-is.

### R-4 — `own_review` fence holds structurally
Confirmed fenced in both `DEBATE`/`DEBATE_ISSUE` and neutralized in the
orchestrator; no other slot was un-fenced. It inherited R-1's weakness, now
closed by the R-1 fix.

## Fresh-sweep findings

### N-1 (Low) — CLI-adapter error snippet was not redacted — **FIXED**
`adapters.py` built the nonzero-exit `AgentResult.error` from the agent CLI's
raw stderr/stdout **without** `redaction.redact`, unlike the LocalAdapter path
(#293/F-8). That error is rendered into the report and posted to the PR, so a
crashing CLI dumping an env var/token into stderr would publish it.
**Fix:** run the detail through `redaction.redact` before embedding (classify
still uses the raw text; codes carry no secrets).

### N-2 (Low) — parsers could raise `RecursionError` — **FIXED**
`parse_findings`/`parse_verdicts` caught only `(ValueError, TypeError)`;
`json.loads` raises `RecursionError` on deeply nested input (`[`×100000),
breaking the documented "never raises" contract and letting one steerable
reviewer abort the whole run.
**Fix:** add `RecursionError` to both except clauses (`findings.py`).

### N-3 / L-4 (Low) — `diff --git` space/quoted-path truncation — **FIXED**
`split_diff` took the path via `line.split()[3]`, truncating
`b/evil with space.py` to `evil`. With an `include` allow-list this **hid the
file from review** (dropped as not-included), not merely mislabeled it.
**Fix:** recover the path from the unambiguous `+++ b/` / `--- a/` marker lines,
unquoting git's C-quoted form, with the header kept as fallback (`largediff.py`).

---

## Still open (re-verified accurate, not attacker-reachable by default)

- **gh output not byte-capped on the `--pr`/`--issue` paths** (`github.py` `_gh`
  buffers full subprocess stdout). The stdin/file path is now byte-capped;
  extending the ceiling to the gh paths needs a streaming `Popen` refactor with
  careful stderr-deadlock handling, deferred to avoid destabilizing the live
  PR-review path. Pre-existing; memory-DoS hardening.
- **L-3** (`gh` not under `JURY_REQUIRE_ABSOLUTE_COMMAND`), and the three Info
  items from the prior report (LocalAdapter runtime SSRF seam, policy loaded
  from CWD vs base-ref in CI, `jury init` symlink) — all re-confirmed accurate,
  none newly exploitable.

## Confirmed solid (re-verified)
CI gate / vote tally remain pure functions of structured findings; subprocess is
stdin-only with process-group kill; SSRF loopback gate + no-redirect opener hold;
cache HMAC integrity holds; no pickle/eval/yaml/tar/zip; ReDoS-free regexes
(`injection`, `redaction`, `classification` measured linear); determinism intact.
