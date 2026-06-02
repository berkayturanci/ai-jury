# Roadmap

How the work is organized, and how to pick up a session's worth of it.

## Two layers

- **Milestones = phases.** A strategic arc, shipped in order. Each is a coherent release.
- **`effort:` labels = session sizing.** How much one focused evening buys you.
  - `effort:S` — one focused evening.
  - `effort:M` — a few evenings, or has sub-steps worth ticking off as you go.
  - `effort:L` — large; decompose into sub-tasks before starting (today: only #1).

A nightly goal is usually **"close 1–2 `effort:S` issues, or push one `effort:M` forward"** — not "finish a milestone." Milestones close when their issues do.

## Working a session

Pick tonight's target from the current milestone, smallest-first:

```bash
# what's small and ready in the current phase?
gh issue list --milestone "v0.2 Must-Have Core" --label "effort:S" --state open

# start one
gh issue develop <n> --checkout        # branch off the issue (optional)
gh issue edit <n> --add-assignee @me

# finish: reference the issue in the commit/PR so it auto-closes
git commit -m "...

Closes #<n>"
```

> Tip: the jury can review its own PRs — `git diff origin/HEAD... | jury --diff-file -` (or `--mock` while the core is unstable). Dogfooding is the best test.

## Phases

### v0.2 — Must-Have Core  *(trust: structured, safe, reliable output)*
The keystone chain. Everything downstream depends on a structured, trustworthy verdict.

- **#1 structured finding schema** `L` — *the keystone; #2, #3, #4, #32, #42 all build on it. Decompose first.*
- #2 deterministic consensus grouping `M` · #3 verification round `M` · #39 prompt-injection hardening `M` · #13 reusable skill `M` · #17 cookbook `M`
- #28 config validation `S` · #29 typed error taxonomy `S` · #27 golden-file tests `S` · #36 codex hardening `S` · #34 doctor command `S` · #22 CLI compatibility contract `S` · #15 positioning/non-goals `S`

**Suggested first sessions** (the `S` issues with *no* dependency on #1, so you get wins while #1 is decomposed): #29 → #28 → #36 → #34. Then decompose and start #1; once it lands, #2 and #3 unblock.

### v0.3 — Public Launch  *(presentation & project health)*
#46 README visual & tagline `S` · #16 ecosystem comparison `S` · #18 release checklist `S` · #44 llms.txt `S` · #14 website `M` · #20 multi-OS/version matrix `M` · #24 Scorecard/CodeQL/deps `M` · #45 plugin manifests `M`

### v0.4 — Practical Integrations  *(make it a CI citizen)*
#4 severity-gated CI exit `S` · #21 coverage gate `S` · #23 live smoke tests `S` · #32 run metadata & cost `S` · #35 agent version detection `S` · #42 JSON/SARIF output `M` · #5 inline GitHub comments `M`

### v0.5 — Advanced Review Quality  *(the principled identity: bias + reproducibility)*
#6 secret-redaction `S` · #7 risk labels `S` · #8 repo review policy `S` · #12 benchmark fixtures `M` · #37 anonymized rebuttal `M` · #38 chair self-preference `M` · #41 reproducibility/seed `M`

### v0.6 — Nice-to-Have / Future Lab  *(wait for real demand)*
#26 governance `S` · #33 local cache `S` · #40 convergence early-stop `S` · #9 incremental review `M` · #10 suggested patches `M` · #11 comment commands `M` · #25 provenance/SBOM `M` · #30 budget/retries `M` · #31 large-diff handling `M` · #43 open-weight/local adapter `M`

## An open question on sequencing

The two highest-leverage *adoption* levers currently sit late:

- **#43 (open-weight / local-model adapter)** is in v0.6. But it's the only path to a **credit-card-free, offline first run** — today a real run needs three paid CLIs installed and authed, which is the biggest onboarding wall. Arguably an early-adoption lever, not "future lab."
- **#12 (benchmark fixtures)** is in v0.5. The "jury caught bugs single reviewers missed" table is the single most *shareable* artifact — the thing that earns attention. A case for pulling it forward.

Both are deliberately deferred here ("wait for real demand"), which is a defensible product call. Flagged so the trade-off is explicit, not accidental.
