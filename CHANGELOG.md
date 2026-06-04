# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Docs/site: abdication case study.** Expanded the "a reviewer refused, the jury excluded it" moment from the live run into a dedicated `docs/case-study-abdication.md` (registered in the docs portal), linked from `example-live-review.md`, and surfaced as an expandable `<details>` under the homepage "Real run" card. Explains why "no findings" must not be read as APPROVE, and notes the chair-vs-vote nuance (tracked as #251).
- **Website + docs: refreshed the "Real run" with a current v1.1.0 review.** Re-ran the four-vendor panel (Claude, Codex, Antigravity + a free local Qwen) over the whole repo on v1.1.0 (2026-06-04): 8 chunks, 25 verified findings, 6 of the panel's own false positives rejected, 22 secrets redacted. Updated the homepage "Real run" card (date + version + fresh stats/quote) and rewrote `docs/example-live-review.md` with the new findings — including the jury excluding a reviewer that refused to answer rather than scoring the non-answer as a pass.
- **Website hero: distinct issue-verdict colours.** Issue verdicts now read green / amber / indigo (READY / NEEDS-INFO / UNCLEAR) instead of reusing the PR red for NEEDS-INFO — the verdict badge and the animated verdict-stage glow both follow the new palette. Visual-only.
- **Website hero pipeline: PR / Issue mode tabs.** The animated hero pipeline now
  has a `Pull request` / `Issue` tablist — switching modes restarts the loop with
  mode-appropriate scenarios and verdicts (PR: `--pr 123/124/125` →
  APPROVE / REQUEST CHANGES / APPROVE · issue: `--issue 42/43/44` →
  READY / NEEDS-INFO / UNCLEAR), and the input label (`diff / PR` ↔ `issue`) and
  synthesis stage (`chair` ↔ `vote ✓`) follow the active scenario. Adds the
  `.pipe-tabs`/`.pipe-tab` styles and the issue verdict colours
  (`ready`/`needsinfo`/`unclear`) to `styles.css`.
- **Website interactive demo: panel vote now works for issues.** The "Build your
  jury" demo (`website/app.js`) used to force the chair and disable the vote
  option whenever the target was an issue ("n/a for issues") — stale since #230
  shipped issue voting, and contradicting the site's own FAQ. The demo now offers
  panel vote for issues with the mode-aware tally (`NEEDS-INFO > UNCLEAR > READY`,
  majority wins, ties to the stricter stance), emits `--decision vote` in the
  generated command, and shows the per-reviewer ballots + vote footer — matching
  the real CLI. PR-only output toggles (phased / live progress) stay issue-disabled.
- **Docs accuracy sweep across every doc.** Full pass over `docs/`, `README.md`,
  and the checklists fixing stale/incorrect content: README status bumped to
  v1.1.0 and the "no input" error example now lists `--issue`; `feasibility.md`
  no longer calls early-stop / verify / anonymized feedback "roadmap" (all
  shipped); `releasing.md` corrected — trusted publishing is configured and the
  publish step is **not** `continue-on-error`; `release-checklist.md` marks the
  shipped website / hero visual / plugin-matrix items done; `architecture.md`
  clarifies Codex is fed on stdin and that the verdict vocabulary is mode-aware
  (issue verdicts + panel vote); `comparison.md` gains panel-voting and
  issue-review rows; fixed broken in-doc anchors (`#presets`, `#comment-actions`,
  and a benchmark cross-link). No behaviour change.
- **Docs & website: complete the issue / voting / wizard coverage** (#236).
  Corrected PR-only phrasing now that `--post`/`--post-summary` post to issues
  too (via `gh issue comment`): the parameter-reference "GitHub posting" section,
  the cookbook, and the skill doc no longer imply posting requires `--pr`. Added
  the guided wizard (`jury init --wizard`) to the Common recipes and cookbook
  setup; documented `decision = chair|vote` and the mode-aware verdict
  vocabulary (PR `REQUEST CHANGES > COMMENT > APPROVE`; issue `NEEDS-INFO >
  UNCLEAR > READY`) in the Enumerations and configuration behaviour sections. The
  website gains an issue step in the Quickstart, more `--issue` examples under
  "More commands", a `decision` key in the Configuration list, and an
  issue-review FAQ entry (visible + structured-data).

## [1.1.0] - 2026-06-04

### Added

- **Guided init wizard** (#231): `jury init --wizard` runs an opt-in,
  numbered-option setup for the most-used settings (reviewers, depth,
  chair-vs-vote decision, verification, context policy + secret redaction, CI
  gate). Every question is skippable — pressing Enter keeps the built-in
  default — and the wizard writes only the keys you explicitly chose, so the
  generated `jury.toml` stays minimal. Plain `jury init` is unchanged.
- **Live per-step posting for issues** (#229): `jury --issue N --live --post` now
  posts each step to the issue thread as it happens (via `gh issue comment`),
  symmetric with the PR flow. Opt-in (requires `--post`); `--issue --live` alone
  still just streams to the terminal, and `--issue --post` alone still posts one
  summary comment.
- **Panel voting for issues** (#230): `--decision vote` now works with `--issue`
  too. The vote is mode-aware — a diff/PR tallies REQUEST CHANGES > COMMENT >
  APPROVE, an issue tallies **NEEDS-INFO > UNCLEAR > READY** (each reviewer votes
  from the worst gap they raised; majority wins, ties resolve to the stricter
  stance, the chair stays the default). Replaces the previous chair-only fallback.
- **Issue review** (#221): `jury --issue N` reviews a GitHub **issue** for
  completeness and clarity (reproduction steps, expected vs actual, scope /
  acceptance criteria, missing context, actionability) using the same
  multi-agent jury machinery (panel → debate → verify → synthesis) with an
  issue-quality rubric. The verdict vocabulary is **READY / NEEDS-INFO /
  UNCLEAR**. `--post`/`--post-summary` comments back via `gh issue comment`;
  PR/diff-only flags (`--post-inline`, `--post-progress`, `--label`,
  `--incremental`) are rejected for `--issue`.
- **Panel voting verdict** (#220): `--decision vote` (or `[jury] decision = "vote"`)
  derives the final verdict by **tallying the reviewers** instead of letting a single
  chair decide — each reviewer votes from the worst finding they raised
  (critical/major → REQUEST CHANGES, minor/nit → COMMENT, none → APPROVE), majority
  wins, ties resolve to the stricter stance. The report shows the tally + per-reviewer
  ballots as the headline verdict and keeps the chair's synthesis as supporting
  reasoning; the tally is also written to `--metadata-json`. Pure/deterministic and
  rendering-only — it doesn't change orchestration, the cache key, or the
  severity-based `--ci` gate (which stays the independent hard safety check).
- **Live play-by-play** (#210): `--live` streams the deliberation as it happens —
  each reviewer's output, each debate turn, the verification, and the chair's
  decision are emitted the moment they land, instead of only at the end. With
  `--pr` each step is also posted as its own PR comment ("post after post", as if
  watching the jury live). The orchestrator stays pure: it fires an optional
  `on_event(kind, result, round_no)` callback in deterministic per-phase order;
  the CLI does the I/O. PR posting is best-effort and never aborts the run.
- **Full-transcript / verbose output** (#208): `--transcript` renders the whole
  play-by-play — each agent's raw review, the debate exchanges, and the chair's
  decision *and its reasoning* — instead of the consensus-first summary;
  `--verbose` shows the summary followed by the transcript in one document; and
  `[jury] transcript = true` makes the transcript the default (`--no-transcript`
  overrides). It works with `-o FILE` (a shareable Markdown artifact) and posts to
  a PR via the existing `--post` flow. Rendering-only: the orchestration, outcome,
  and cache key are unchanged.

### Changed

- **Example-rich parameter reference, surfaced on the site** (#209): `docs/parameters.md`
  now opens with a **Common recipes** block (copy-pasteable commands for the everyday
  jobs) and carries a worked **Example** for every flag group, with enum values and
  depends-on/conflicts notes spelled out. The docs portal (`website/docs.html`) now
  lists it first under a **Reference** group ("Parameter reference — start here") and
  leads with the practical Reference/Guides over the Overview material, so the flag
  reference is the obvious entry point instead of being invisible.
- The **skill** (`skill/ai-jury/SKILL.md`) now includes a curated **Parameters**
  section (#213) — the common flags grouped by intent (what to review, depth,
  output incl. `--transcript`/`--verbose`/`--live`, PR posting, CI gating, scope)
  with a pointer to the full `docs/parameters.md` reference — so the option surface
  is discoverable when ai-jury is invoked as a Claude Code skill.
- Removed internal `(issue #N)` references from user-facing surfaces (#212): the
  `jury --help` text and the docs pages no longer cite this repo's issue tracker,
  which means nothing to a reader. The `CHANGELOG` and source-code comments keep
  their references for history/developer context.
- **Rebuilt the project website from a fresh design direction** (closes #159).
  New landing page plus dedicated `docs.html`, `coverage.html`,
  `coverage-report.html`, and `404.html`; refreshed favicon set, OG banner, and
  convergence logo mark. `make assets` now also regenerates the favicon set
  from `website/favicon.svg`.
- Wired **Google Analytics 4 (gtag.js)** into the website via
  `website/analytics.js` (loaded from every page's `<head>`). The active GA4
  property is set by `MEASUREMENT_ID` in that file. Scope is website-only —
  the `ai-jury` CLI itself remains telemetry-free.

## [1.0.0] - 2026-06-03

First public release. `ai-jury` orchestrates native coding-agent CLIs (Claude
Code, Codex, Antigravity) plus an optional local/open-weight model to review,
debate, verify, and synthesize one verdict on a diff or PR — stdlib-only, secure
by default, project-agnostic.

### Changed

- **CI runs entirely on GitHub-hosted runners now that the repo is public.** The
  hosted `ci.yml` matrix (ubuntu/macOS/windows × Python 3.11–3.13) plus a
  coverage gate is the authoritative per-push/PR signal, and CodeQL + OpenSSF
  Scorecard run per-commit again. The self-hosted macOS runner and its
  `local-ci.yml` workflow were **removed**: running untrusted forked-PR code on a
  maintainer machine is an arbitrary-code-execution risk, and free public-repo
  minutes make it unnecessary. The website (GitHub Pages) and PyPI publishing
  (OIDC trusted publishing on `v*` tags) are now live.

### Fixed

- Printing the report no longer crashes with `UnicodeEncodeError` on a Windows
  console (legacy cp1252 code page): the CLI now reconfigures stdout/stderr to
  UTF-8 at startup so the report's `🏛️`/`⇄` characters encode cleanly. Surfaced
  by the now-active hosted Windows CI job.

### Security

- Secure-by-default agent sandboxing (#100): the shipped reviewer defaults no
  longer grant broad powers while reading untrusted PR content. `codex` now runs
  `-s read-only` (was `danger-full-access`) — the diff is fetched by the jury,
  not the agent, so the reviewer needs no write/network; `agy` now runs
  `--sandbox`; `claude` keeps its write-tool denylist. The least-privilege audit
  recognizes a sandbox as a mitigation, and the shipped defaults raise no
  warnings. Widen a sandbox only if your workflow needs it.

### Fixed

- The result cache key now includes the repository review policy (#122); a
  `--policy` change no longer collides on the same key and serves a stale outcome.
- `config_hash` now covers `anonymize_debate` and `prefer_non_reviewer_chair`
  (#122), so the "same hash ⇒ same orchestration" guarantee (and the cache key)
  holds.
- Inline review posting sends a top-level `body` (#122); GitHub's create-review
  API requires it for a COMMENT event, so `--post-inline` no longer risks a 422.
- docs/security.md least-privilege table corrected to the secure-by-default
  sandboxing (codex `-s read-only`, agy `--sandbox`) — it still described the old
  `danger-full-access` default (#122).
- Secret redaction now scrubs modern OpenAI keys `sk-proj-…` / `sk-svcacct-…` /
  `sk-admin-…` (#122); the old pattern stopped at the first hyphen and would have
  sent them to review agents (no real key actually leaked).
- Removed an accidentally-committed machine-local `cache/projects.json` (local
  paths, no secret) and gitignored `cache/` (#122).
- Secret redaction now preserves the surrounding quotes of a redacted
  assignment (#102), so scrubbing a secret-fixture file keeps it a valid string
  literal instead of fabricating phantom "syntax error" findings for reviewers.
- A reviewer CLI that exits **nonzero** is now always treated as a failure, even
  when it printed to stdout (#101); partial/error output no longer silently
  counts as a clean review feeding consensus, synthesis, and the CI gate.
- Result cache key now includes the `--mock` flag, so a mock run can never be
  served as a real review (or vice versa) for the same diff+config.
- `total_timeout` now bounds a whole **chunked** review instead of resetting per
  chunk: `review_diff` threads one shared budget through every chunk's
  `run_jury` (which gained an optional `budget` parameter).
- The structured-findings report no longer crashes (`TypeError`) when two
  same-severity findings include one with no `file`/`line`; the sort key is
  None-safe.
- Self-hosted CI (`local-ci.yml`) no longer runs untrusted **forked-PR** code on
  the self-hosted runner — both jobs are guarded to same-repo pushes/PRs,
  closing an arbitrary-code-execution exposure on the runner host.
- Large-diff binary detection (#31) no longer misclassifies a *source* file as
  binary just because its content mentions `Binary files` / `GIT binary patch`;
  it now matches only the diff's unprefixed binary-marker header line.
- `jury --doctor` reports local/open-weight agents (#43) by their endpoint
  reachability instead of a (non-existent) CLI on PATH, so a reachable local
  server no longer shows as `MISSING`.

### Added

- Published **test-coverage report + badge** on the project site (no external
  service): the Pages deploy runs the suite under coverage, publishes a
  browsable HTML report at `/coverage/`, and writes a shields-endpoint badge
  (`coverage-badge.json`) shown in the README and site badge row. Statement+branch
  coverage is **95%**, with every module ≥90%.
- The consensus section now shows each finding's supporting **evidence** (the
  reviewer's "why"), so a verdict is auditable rather than just asserted.
- `--post-mode {single,phased}` (#127): with `--post-summary`, post the review as
  one comment (default) or as separate **Round 1 / Debate / Decision** comments,
  so the flow (each reviewer's findings → cross-examination → verdict & why) is
  easy to follow. Pairs with `--post-progress` for live stage tracking.
- `--post-progress` (#125): keeps a single, sticky status comment on the PR,
  updated live at each round/chunk milestone (round 1 → debate → verify →
  synthesis; chunk i/N), then replaced with the final verdict. Off by default;
  requires `--pr`; best-effort (a GitHub hiccup never crashes the run).
- Risk-aware auto-depth (#120): `--auto` / `[jury] auto_depth` scales review
  intensity to a cheap pre-review diff profile (size / files / docs-or-generated
  / security-sensitive paths) — a trivial or docs-only diff runs shallow
  (`rounds=1`, no verify) while a large or security-touching diff runs full.
  The panel (vendor diversity) is never trimmed; explicit `--rounds`/`--verify`/
  `--early-stop` override it; the chosen depth is logged.
- `jury init --preset offline|fast|balanced|thorough` — one-command setup for
  common intents (offline = free local-only; fast = 1 round; balanced = debate +
  early-stop; thorough = all agents + debate + verify). Explicit flags override
  the preset's defaults.
- Smart offline fallback: with no config file, no available agent CLI, and a
  reachable local model server, `jury` automatically adds a local agent so it
  works offline out of the box (never overrides an explicit config or a working
  CLI panel).
- `jury config show` / `jury config path` print the **effective resolved
  config** (and its source file) so you can see exactly what a run will use.
- `jury --doctor` now ends with a **Next steps** section: a `ready to run:
  yes/no` verdict plus actionable fixes (scaffold a config, install a CLI, or use
  a reachable local model).
- `jury init` scaffolds a `jury.toml` (#107): detects which agent CLIs are
  available and writes a valid config from interactive prompts or flags
  (`--agents claude,codex,qwen --rounds 2`), using the secure-by-default agent
  templates. `--list-agents` shows availability; existing files are not
  overwritten without `--force`.
- `jury init` discovers **local (Ollama/OpenAI-compatible) models** (#109):
  the interactive flow lists the models on your server and lets you pick one
  (preferring a coder model); `jury init --list-models` prints them. Falls
  back gracefully to a typed default when no server is reachable.
- Local / open-weight reviewer (#43): a new `vendor = "local"` agent talks to any
  OpenAI-compatible server (Ollama, llama.cpp, vLLM, LM Studio) over plain HTTP
  via the stdlib — no new dependencies, no subprocess. Configure with an
  `endpoint` (default `http://localhost:11434/v1`) and a `model`; it participates
  in every round and the consensus like a CLI agent, adding vendor diversity at
  zero marginal cost and enabling fully offline reviews. An unreachable server
  fails with the typed `connection_error` code. README + benchmark note document
  one concrete setup (Ollama) and the cost/quality trade-off.

- Release provenance, checksums, and SBOM (#25): the publish workflow now emits a
  `SHA256SUMS` checksum file, a CycloneDX SBOM (`sbom.cdx.json`), and a signed
  build-provenance attestation, and publishes to PyPI via trusted publishing
  (OIDC, no long-lived token). New `docs/releasing.md` documents how artifacts
  are built and verified (`sha256sum -c`, `gh attestation verify`); the release
  checklist references it.

- Incremental review mode (#9): `--incremental` reviews only the diff since the
  last jury run on a PR (the reviewed head SHA is recorded as a hidden marker
  on the summary comment), falling back to a full review when no prior marker
  exists or the head is unchanged. The report states the review scope.
- Suggested patches (#10): `--suggest-patches` emits a separate, opt-in
  suggested-patches section for **verified** findings only — read-only, never
  applied automatically. `--patches-out PATH` writes them to a file instead.
- GitHub comment commands (#11): a `jury comment --text "/jury review …"`
  mode parses allowlisted PR-comment commands (`review`, `summary`; `--rounds`
  only) into a safe jury run — comment text never reaches a shell. Includes a
  documented, author-gated GitHub Actions recipe in the cookbook.
- Run budget, retries, and partial-result policy (#30): optional `total_timeout`
  / `phase_timeout` budgets and opt-in `retries` for transient
  (timeout/rate-limit/spawn) failures. The effective per-call timeout is the min
  of the agent timeout and the budgets; skipped/failed/retried/timed-out agents
  are surfaced in the report and run metadata; `Ctrl-C` exits cleanly (130). CLI:
  `--total-timeout`, `--phase-timeout`, `--retries`.
- Convergence-based early stop / adaptive debate rounds (#40): with
  `early_stop = true`, a unanimous round-1 panel skips the debate and
  disagreement runs debate up to `max_rounds`, stopping when disputes resolve.
  Rounds executed and the stop reason appear in logs and metadata. A fixed
  `--rounds` keeps reproducible fixed-N behaviour. CLI: `--early-stop` /
  `--no-early-stop`, `--max-rounds`.
- Large-diff handling (#31): the diff is measured and filtered (binary,
  generated/vendored files, and `[jury.diff]` include/exclude globs) before
  review; an over-budget diff is chunked by file (`chunk = true`) or rejected
  with an actionable message. CLI reports size and the selected mode. CLI:
  `--max-diff-bytes`, `--chunk` / `--no-chunk`, `--exclude`, `--include`.
- Optional local result cache (#33): `--cache` reuses a stored outcome for an
  unchanged diff+config (key covers diff, config hash, prompt version, package
  version, context policy, seed) and stores on a miss; cache hits are marked in
  logs and metadata. Clear with `jury cache clear` or `--clear-cache`.
  Privacy implications documented in `docs/configuration.md`.
- Maintainer governance (#26): `MAINTAINERS.md` documents triage labels, release
  cadence, the compatibility/deprecation policy, decision-making, security
  routing, and how project-specific requests are handled; linked from
  CONTRIBUTING.

### Changed

- The `ai-jury` skill is now a compound, end-to-end flow: scaffold a config
  if needed (`jury init`) → review → capture the report (`-o`) → summarize,
  noting that `jury` already combines review + report in one command. Covers the
  local/open-weight option and add-ons (`--incremental`, `--suggest-patches`,
  `config show`, `--doctor`).
- README and hero visual updated to cover current capabilities (#112): the hero now
  shows the **fourth, local / open-weight** panelist (alongside Claude/Codex/Antigravity)
  and the broader pipeline; the README leads with free/offline reviews, `jury init`,
  and secure-by-default sandboxing, and the Status section reflects shipped features.
- Run metadata schema bumped to v2: adds `stop_reason`, `skipped`, `retried`,
  `budget_exhausted`, `from_cache`, an `execution` block, and per-agent
  `attempts`.

- Public CLI compatibility contract: `tests/test_cli_contract.py` locks the
  CLI's flags, `--help` text (width/color-pinned golden under `tests/golden/`),
  error messages, exit codes, and report headings, with a documented stability
  policy in the README. **Breaking changes to these surfaces require a
  CHANGELOG entry.**

- Multi-version, multi-OS CI test matrix (Python 3.11–3.13 on Linux; 3.13 on
  macOS and Windows) covering unit tests and the mock CLI smoke test.
- OpenSSF Scorecard, CodeQL (Python), and Dependabot automation for supply-chain
  and repository-security signals, plus a documented dependency-update policy.
- Ecosystem comparison & capability matrix (`docs/comparison.md`) positioning the
  project against hosted, API-level, and other native-CLI review tools.
- Agent-readable docs: `llms.txt` (concise) and `llms-full.txt` (full reference).
- Public release readiness checklist (`docs/release-checklist.md`).
- Claude Code plugin distribution: `.claude-plugin/plugin.json` and
  `.claude-plugin/marketplace.json` make the repo installable as a single-plugin
  marketplace, reusing the existing `skill/` without moving it.
- Platform support matrix (`docs/platforms.md`) with honest per-platform statuses
  (supported / manual / planned / out of scope) and install snippets.
- Reusable-skill packaging guide (`docs/skill.md`): directory layout, install into
  Codex/Claude-compatible skill folders, required external tools, skill-to-CLI
  versioning policy, examples, and a smoke-test checklist. Skill behavior changes are
  noted in these release notes alongside the CLI change that motivates them.
- Polished README hero visual (`docs/assets/hero.svg` + rendered `hero.png`) with
  meaningful alt text and the project tagline.
- Static landing site under `website/` (HTML/CSS, no build step) reusing the hero asset,
  plus a GitHub Pages deploy workflow and local-preview/custom-domain instructions.

## [0.1.0] - 2026-05-30

### Added

- Initial project-agnostic release of the cross-vendor review jury.
- Native CLI adapters for Claude Code, Codex CLI, and Antigravity.
- Review, debate, and synthesis orchestration pipeline.
- Offline mock mode with unit tests and CLI smoke coverage.
- GitHub PR diff input and optional PR comment output through the GitHub CLI.
- Bundled Claude Code skill for invoking the jury from another project.
