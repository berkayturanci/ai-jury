<!-- A real, live jury run: the tool reviewing its OWN repository with a
     four-vendor panel. Findings are quoted from the actual report; machine-
     specific paths were scrubbed. The deterministic mock sample is in
     example-run.md. Refresh this by re-running the panel (see "Reproduce it"). -->

# Example: a live four-vendor review (the jury reviewing itself)

This walks through a **real** jury run — not the mock — where the jury
reviewed its **own repository** end to end on **v1.1.0 (2026-06-04)**. It's the
most honest demo we have: a heterogeneous panel, a full codebase, real findings
(and real false positives the panel caught itself).

> 📄 Want the raw output? The complete, un-edited report is published verbatim at
> [**Full report (live run)**](live-review-report.md) — every finding,
> verification, and per-chunk verdict, exactly as the tool wrote it.

## Setup

- **Panel (4 vendors):** `claude` (Anthropic), `codex` (OpenAI), `agy` (Google),
  and `qwen` — `qwen2.5-coder:7b` running **locally and free** via Ollama (the
  [local adapter](../README.md#local--open-weight-reviewer-free-offline)).
- **Scope:** the whole repository, supplied as one diff (~1.4 MB, 194 files).
  Large-diff handling dropped 10 binary/generated files and split the rest into
  **8 chunks**. Secret redaction scrubbed **22** token-shaped strings first.
- **Depth:** every chunk ran **round 1 (independent reviews) → round 2 (debate /
  cross-examination) → chair verification → synthesis**, then the per-chunk
  outcomes were merged into one report.
- **Outcome:** classification **risk: high · security-sensitive: yes · needs
  human attention: yes**; per-chunk chair verdicts ranged from `COMMENT` to
  `REQUEST CHANGES`. The verifier marked **25** findings verified and rejected
  **6** as unsupported (the panel's own false positives).

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

Quoted from the run — the jury found these in its *own* code, and the chair
**verified** each against the diff:

- **`[minor] .github/workflows/publish.yml` — the SBOM captures the whole runner
  environment, not the package** _(raised by `agy`, affirmed by `claude` + `codex`
  in debate)._ `cyclonedx-py environment` runs after `pip install`-ing build
  tooling into the runner's interpreter, so the emitted SBOM mixes runner
  packages with the project's real (empty — stdlib-only) dependency set —
  especially misleading for a project whose selling point is *zero runtime
  dependencies*.
- **`[minor] .github/workflows/pages.yml` — the Pages deploy trigger contradicts
  the docs** _(raised by `claude`, affirmed by `codex`)._ The workflow is
  `on: push: branches: [main]` with no `paths:` filter, while `docs/architecture.md`
  and `CLAUDE.md` both say Pages deploys on `push to website/**`.
- **`[major]` — the CI gate silently ignores the documented `blocker` severity
  alias** _(verified)._ `blocker` is documented as an alias for `critical` but
  isn't normalized on the `--fail-on` path.
- **`[major]` — the JSON metadata path can't reflect `--decision vote`**
  _(verified):_ "`to_json` calls `build_run_metadata(outcome, config)` with no
  decision/vote args … so `metadata['decision']` always falls back to
  `config.decision` and `metadata['vote']` is always None."
- **`[major]` — `_gh` / `_gh_with_input` run `subprocess.run` with no `timeout=`**
  _(verified):_ these run outside the orchestrator's `RunBudget`, so a hung `gh`
  would block indefinitely with no enforced cap.

A *diverse* panel plus a verification round turned up real, actionable defects —
across CI/release plumbing, docs contracts, and the orchestrator itself.

## What makes this honest

- **The panel caught its own false positives.** It rejected a `critical` claim
  that a comma-separated `subject-path` "breaks attestation" — both `claude` and
  `codex` disputed it and verification marked it **unsupported**:
  `actions/attest-build-provenance` accepts a comma- or newline-separated list, so
  `"dist/*.whl, dist/*.tar.gz"` is valid. Six findings were thrown out this way.
- **A security tool flagging its own security docs.** The prompt-injection scanner
  fired on `docs/security.md` — and the chair reasoned it through: "the
  override / role / coercion patterns *do* exist, but they appear inside
  `docs/security.md` as benign documentation of the threat model, not a real
  attack." Detection is real; benignity is a human call.
- **An abdication is not a clean pass.** On one chunk a cloud reviewer returned
  only *"I can't assist with that request."* The jury **excluded it from
  consensus** — "an abdication, not a review" — instead of silently counting the
  non-answer as an APPROVE. A single-reviewer tool has no such guard. →
  [Full case study](case-study-abdication.md).
- **The local model is a cheaper, useful seat.** Free, offline `qwen` was the
  *fastest* panelist (≈253 s vs. 660–850 s for the cloud CLIs) and adds vendor
  diversity at zero marginal cost — diversity, not frontier parity. Mix it with
  cloud CLIs.
- **No secrets leave the box.** Secret redaction is on by default (this run
  scrubbed **22** token-shaped strings before sending), the report contains only
  the agents' review text — never their auth — and run metadata is built to
  exclude prompt/diff/output/secrets.

## Reproduce it

```bash
# Configure a 4-vendor panel (claude/codex/agy + a local Ollama model — e.g.
# `ollama pull qwen2.5-coder:7b`), then review the whole repo as one diff with
# chunking + debate:
EMPTY=$(git hash-object -t tree /dev/null)
git diff "$EMPTY" HEAD > whole-repo.diff
jury --diff-file whole-repo.diff --config jury.toml --chunk --rounds 2
```

Numbers above are from one real run; re-running on a later commit will surface a
different (and probably larger) set. See the [cookbook](cookbook.md) for
incremental reviews, suggested patches, and comment-triggered runs; and
[example-run.md](example-run.md) for the deterministic mock sample.
