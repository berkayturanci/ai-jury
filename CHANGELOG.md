# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Published **test-coverage report + badge** on the project site (no external
  service): the Pages deploy now runs the suite under coverage, publishes a
  browsable HTML report at `/coverage/`, and writes a shields-endpoint badge
  (`coverage-badge.json`) shown in the README and site badge row.

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
