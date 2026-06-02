# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Result cache key now includes the `--mock` flag, so a mock run can never be
  served as a real review (or vice versa) for the same diff+config.
- `total_timeout` now bounds a whole **chunked** review instead of resetting per
  chunk: `review_diff` threads one shared budget through every chunk's
  `run_council` (which gained an optional `budget` parameter).
- The structured-findings report no longer crashes (`TypeError`) when two
  same-severity findings include one with no `file`/`line`; the sort key is
  None-safe.
- Self-hosted CI (`local-ci.yml`) no longer runs untrusted **forked-PR** code on
  the self-hosted runner — both jobs are guarded to same-repo pushes/PRs,
  closing an arbitrary-code-execution exposure on the runner host.
- Large-diff binary detection (#31) no longer misclassifies a *source* file as
  binary just because its content mentions `Binary files` / `GIT binary patch`;
  it now matches only the diff's unprefixed binary-marker header line.
- `council --doctor` reports local/open-weight agents (#43) by their endpoint
  reachability instead of a (non-existent) CLI on PATH, so a reachable local
  server no longer shows as `MISSING`.

### Added

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
  last council run on a PR (the reviewed head SHA is recorded as a hidden marker
  on the summary comment), falling back to a full review when no prior marker
  exists or the head is unchanged. The report states the review scope.
- Suggested patches (#10): `--suggest-patches` emits a separate, opt-in
  suggested-patches section for **verified** findings only — read-only, never
  applied automatically. `--patches-out PATH` writes them to a file instead.
- GitHub comment commands (#11): a `council comment --text "/council review …"`
  mode parses allowlisted PR-comment commands (`review`, `summary`; `--rounds`
  only) into a safe council run — comment text never reaches a shell. Includes a
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
  generated/vendored files, and `[council.diff]` include/exclude globs) before
  review; an over-budget diff is chunked by file (`chunk = true`) or rejected
  with an actionable message. CLI reports size and the selected mode. CLI:
  `--max-diff-bytes`, `--chunk` / `--no-chunk`, `--exclude`, `--include`.
- Optional local result cache (#33): `--cache` reuses a stored outcome for an
  unchanged diff+config (key covers diff, config hash, prompt version, package
  version, context policy, seed) and stores on a miss; cache hits are marked in
  logs and metadata. Clear with `council cache clear` or `--clear-cache`.
  Privacy implications documented in `docs/configuration.md`.
- Maintainer governance (#26): `MAINTAINERS.md` documents triage labels, release
  cadence, the compatibility/deprecation policy, decision-making, security
  routing, and how project-specific requests are handled; linked from
  CONTRIBUTING.

### Changed

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

- Initial project-agnostic release of the cross-vendor review council.
- Native CLI adapters for Claude Code, Codex CLI, and Antigravity.
- Review, debate, and synthesis orchestration pipeline.
- Offline mock mode with unit tests and CLI smoke coverage.
- GitHub PR diff input and optional PR comment output through the GitHub CLI.
- Bundled Claude Code skill for invoking the council from another project.
