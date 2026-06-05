<!-- A live dogfood log: the jury (codex + agy + qwen, NO claude) reviewing the
     five fix/feature PRs that became v1.2.0. Kept as its own page because it's
     the most honest evidence we have — real PRs, real bugs the author missed,
     AND the panel's false-positive failure mode, labelled after the fact. -->

# Case study: the jury reviewing its own v1.2.0 PRs

A **live dogfood log**. While building the v1.2.0 release (2026-06-05), every
fix/feature PR was reviewed by the jury itself before merge — a three-vendor
panel of **codex + agy + qwen (no Claude on the panel)**, run with the
iterate-until-clean rule: review → fix the verified findings → re-run → merge.

Unlike the [labelled benchmark](benchmark-results.md) (seeded fixtures with
ground truth), this is **real PRs with real bugs the author didn't plant**. The
trade-off: there's no pre-registered ground truth, so each finding is labelled
*after the fact* (by the implementer) as a true or false positive. Read it as a
case study, not a controlled measurement — the [caveats](#caveats) are real.

## The runs

Panel: `codex` (OpenAI), `agy` (Google Antigravity), `qwen2.5-coder:7b` (local).
Chair rotates among the three. No Claude anywhere in the loop.

| PR | Change | Verdict (rounds) | Real bugs caught | False positives |
|----|--------|------------------|------------------|-----------------|
| #273 | #250 verify anonymization | COMMENT | — | — (1 style nit) |
| #274 | #249 redaction_count fix | REQUEST CHANGES | — | **2** (chair-verified, wrong) |
| #275 | #246 gh timeout | APPROVE | — | — |
| #276 | #265 CLI overview | REQUEST CHANGES → clean | **2** | — |
| #277 | #271 PR-lint | REQUEST CHANGES → clean | **3** | 1 (re-run) |

**Net: the panel found 5 real bugs the author had missed, and produced 3
false positives that the chair wrongly "verified."** Both halves matter.

## What it caught (true positives)

These are real defects the author shipped into the PR and the panel flagged
before merge — the recall argument for a panel over a single self-review.

**#276 / issue #265 — friendly CLI overview (2 bugs):**

- `agy`: `sys.stdin.isatty()` on bare `jury` would raise **`AttributeError`
  when `sys.stdin is None`** — exactly the detached-I/O case a background process
  hits. Real crash; fixed with a `sys.stdin is not None` guard.
- `agy`: `jury examples foo` **silently swallowed the trailing argument** because
  the intercept used a prefix match (`raw[:1] == ["examples"]`). Tightened to an
  exact match so junk falls through to argparse and errors.

**#277 / issue #271 — PR-description lint (3 bugs):**

- `codex`: the template's placeholder prose (`Describe the change…`) **counted as
  a real summary**, so an untouched template passed the length check.
- `agy` (critical): the issue-reference check scanned the **unstripped** PR body,
  and the template's own HTML comment literally contains the words `no issue` — so
  the `no issue` opt-out matched **every** default PR, disabling the check entirely.
- `agy`: `s.startswith("#")` excluded valid prose lines like `#123 fixes…` from
  the word count.

All three were fixed (compare against the template file to drop boilerplate;
check refs in the comment-stripped text; use a real markdown-heading regex), and
the re-run came back clean.

## What it got wrong (false positives)

The same panel also confidently "verified" findings that were **factually
incorrect** — the failure mode worth being honest about.

- **#274 / #249:** `agy` claimed the config path `config.context` was wrong (it's
  the flattened `[jury]` path the existing code already uses) and, on the re-run,
  that a test used a `[REDACTED:…]` placeholder instead of a real secret (it uses
  a real fake AWS key; the tests pass). The chair marked both **verified**.
- **#277 / #271 re-run:** after the three real bugs were fixed, `codex`
  **re-asserted** the (now-closed) placeholder bypass, and the chair verified it
  again — even though executing the exact claimed input fails the check.

> **Postscript — the panel reviewed this page too.** On its dogfood run of the
> PR that added this case study, `agy` flagged a wrong issue number in the
> paragraph above (it originally read "#277 / #249"; #277 was issue #271). The
> chair happened to mark it *unsupported* — another verifier miss — but the
> finding was right, and it's fixed here. A fitting illustration of the whole
> lesson: the panel surfaces real things the author missed, and the verifier's
> verdict is not the last word.

In every case the verdict was overridden **with executed evidence** (run the
code, show the output), and the override was recorded on the PR.

## The lesson

Two things are true at once:

1. **A diverse panel catches real bugs a single self-review misses.** Five
   genuine defects — including a crash and a check-disabling bypass — were caught
   before merge by models that didn't write the code.
2. **A model "verifier" that can't execute the code will confirm plausible-
   sounding-but-wrong findings.** Every false positive here shared a shape: a
   static reader reasoning about behaviour it couldn't run. The verifier rejected
   many weak findings correctly, but it is **not** a substitute for running the
   thing — `make test`, the coverage gate, and a human adjudicating with evidence
   stay in the loop. This is the same spirit as the
   [abdication case study](case-study-abdication.md): *"a finding asserted" and "a
   finding true" are not the same thing.*

The practical workflow that fell out of it: treat the panel as a **high-recall
finder**, then verify each blocking finding by executing it. Cheap findings that
survive execution are gold; the rest are noise you can refute in one command.

## Caveats

- **Small N** — five PRs, one repo, one release cycle.
- **Self-review** — the panel reviewed this project's own PRs.
- **Non-deterministic** — live models; a re-run can change the verdict (it did).
- **Post-hoc labelling** — true/false-positive labels are the implementer's, applied
  after the fact, not a pre-registered ground truth. The
  [fixture benchmark](benchmark-results.md) is the controlled counterpart.
