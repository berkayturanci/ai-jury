<!-- A real, live council run: the tool reviewing its OWN repository with a
     four-vendor panel. Findings are quoted from the actual report; machine-
     specific paths were scrubbed. The deterministic mock sample is in
     example-run.md. -->

# Example: a live four-vendor review (the council reviewing itself)

This walks through a **real** council run — not the mock — where the council
reviewed its **own repository** end to end. It's the most honest demo we have:
a heterogeneous panel, a full codebase, real findings (and real false positives
the panel caught itself).

## Setup

- **Panel (4 vendors):** `claude` (Anthropic), `codex` (OpenAI), `agy` (Google),
  and `qwen` — `qwen2.5-coder:7b` running **locally and free** via Ollama (the
  [local adapter](../README.md#local--open-weight-reviewer-free-offline)).
- **Scope:** the whole repository, supplied as one diff. Large-diff handling
  filtered binaries and split it into **10 chunks**.
- **Depth:** every chunk ran **round 1 (independent reviews) → round 2 (debate /
  cross-examination) → chair verification → synthesis**, then the per-chunk
  outcomes were merged into one report.
- **Verdict:** `REQUEST CHANGES` · risk **high** · security-sensitive **yes**.

## The flow

```
whole-repo diff
  └─ measure + filter (drop binaries; apply path policy)         [largediff]
  └─ split into chunks within the size budget                    [largediff]
       └─ for each chunk:
            round 1   claude ‖ codex ‖ agy ‖ qwen  (independent)
            round 2   debate — each agent sees the others' (anonymized) findings
            verify    the chair judges each candidate finding
            synthesis the chair writes one verdict
  └─ merge chunk outcomes → consensus groups → final report      [orchestrator]
```

## A few real findings it caught

Quoted from the run (the council found these in its *own* code, and the chair
**verified** each):

- **`[critical] .github/workflows/local-ci.yml` — arbitrary code execution on the
  self-hosted runner via `pull_request`** _(raised by `agy`)_. A forked PR could
  run code on the runner host.
- **`[critical] cache.py` — `cache_key` omits the `mock` flag, so mock and real
  runs share a cache entry** _(raised by `claude`)_ — i.e. `--mock --cache` could
  serve canned findings as a real review.
- **`[major] adapters.py` — a nonzero-exit CLI with any stdout is counted as a
  passing reviewer** _(verified):_ "the only nonzero-exit failure branch requires
  `returncode != 0 AND not out`; a nonzero exit with any stdout … returns
  `ok=True` despite the nonzero exit code."
- **`[major] report.py` — `render()` sort key crashes when a finding's `file` is
  `None`** _(raised by `claude`)_.

The first and second-to-fourth were fixed in follow-up PRs the same session; the
security-posture findings became tracked issues. The point: a *diverse* panel
plus a verification round turned up real, actionable defects.

## What makes this honest

- **The panel caught its own false positives.** Several `critical` "syntax error"
  findings were artifacts of the secret-redaction layer rewriting the project's
  *secret-fixture test files* before reviewers saw them — and the council
  **diagnosed exactly that** and rejected them ("the redaction is lossy enough to
  fabricate phantom syntax errors"). A verification round earns its keep.
- **The local model is a weaker, but useful, seat.** On one chunk `claude` noted
  in debate that the local `qwen` reviewer "produced no findings — only a docs
  summary." That's the documented trade-off: a small local model adds *diversity*
  and zero-cost coverage, not parity. Mix it with cloud CLIs.
- **No secrets leave the box.** The report contains only the agents' review text,
  never their auth; tokens/account info live in each CLI's own config. Secret
  redaction is on by default (this run scrubbed 63 token-shaped strings before
  sending), and run metadata is built to exclude prompt/diff/output/secrets.

## Reproduce it

```bash
# Configure a 4-vendor panel (claude/codex/agy + a local Ollama model),
# then review the whole repo as one diff with chunking + debate:
EMPTY=$(git hash-object -t tree /dev/null)
git diff "$EMPTY" HEAD > whole-repo.diff
council --diff-file whole-repo.diff --config council.toml --chunk --rounds 2
```

See the [cookbook](cookbook.md) for incremental reviews, suggested patches, and
comment-triggered runs; and [example-run.md](example-run.md) for the deterministic
mock sample.
