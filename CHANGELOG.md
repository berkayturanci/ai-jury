# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- **Post-v1.4.0 security re-audit** (`docs/security-audit-2026-06-07-v1.4.0.md`). A third four-surface re-audit of the released code confirms every #287–#303 fix holds in source (no Critical/High). It surfaces two **Medium** residuals — the unknown-vendor adapter path still runs **fail-open** (no sandbox injected) in default mode even though #300 made the audit warn, and `jury init --local-endpoint` reaches an arbitrary host **without** the `_endpoint_issues` SSRF gate the config path enforces — plus minor items (init endpoint not redacted in stdout, explicit TLS context, `prior_txt` debate slot not neutralized, more secret formats, `cache clear()` glob blast-radius, atomic-write temp via `mkstemp`). Tracked for follow-up.

## [1.4.0] - 2026-06-07

> Security re-audit follow-up (#300–#303). All #287–#296 fixes re-confirmed in
> source; the remaining defense-in-depth gaps are closed. Behavior notes: the
> least-privilege audit now warns for **any** unsandboxed non-claude reviewer
> (more `--strict` failures for loose configs), and `PROMPT_VERSION` was bumped
> (the result cache invalidates once). Each change was cross-vendor jury-reviewed.

### Security

- **Post-v1.3.0 security re-audit** (`docs/security-audit-2026-06-07-v1.3.0.md`). A four-surface re-audit of the released code confirms every #287–#296 fix holds in source (no Critical/High) and documents the remaining defense-in-depth gaps: the read-only sandbox guarantee is not yet unconditional for an **unknown vendor** (no sandbox injected, no privilege warning), the **untrusted-content sentinel fences aren't neutralized** against an embedded closing token, redaction still misses **basic-auth URLs / Azure / GCP** secret formats, and the `detect_capabilities` version probe doesn't kill its process group on timeout. Tracked as #300–#303.
- **The privilege audit now flags any unsandboxed non-claude reviewer** (#300). `audit_agent` previously warned only when a *dangerous flag* was present, so an unknown-vendor (or no-flag) agent that ran via the generic adapter produced **zero** warnings — and `--strict` couldn't fail it on that basis. It now warns whenever a non-claude agent isn't under a recognized read-only sandbox (`-s read-only` / `--sandbox`), closing the audit blind spot; a `local`/HTTP agent (no subprocess to sandbox) is correctly out of scope. (`--strict` already rejected an unknown vendor via config validation; this makes the default-mode gap visible too.)
- **Untrusted-content sentinel fences are neutralized before interpolation** (#301). The prompt templates wrap the diff/context/reviews in `<<<UNTRUSTED_X … UNTRUSTED_X>>>` fences; a diff that embedded a closing/opening sentinel verbatim could break out of (or forge) a fence. `prompts.neutralize_sentinels` now breaks any `<<<`/`>>>` run adjacent to an `UNTRUSTED_` marker in untrusted values (using a visible middle dot, not a zero-width char the injection scanner would flag) before every `format()`; the injection scanner still surfaces the attempt and the structured CI gate remains authoritative. `PROMPT_VERSION` bumped to 3 (cache invalidation).
- **Redaction now covers basic-auth URLs, Azure `AccountKey=`, and GCP service-account JSON keys** (#302). Added a `basic_auth` pattern that redacts the password in `scheme://user:password@host` (preserving the `://user:`…`@` structure so the URL stays readable; an empty username `https://:TOKEN@host` is covered too), added `account[_-]?key` to the assignment keywords (Azure `AccountKey=`/connection strings; `SharedAccessKey=` was already covered via `access_key`), and made the assignment separator tolerate a quoted key so a GCP `"private_key_id": "…"` JSON field is redacted while a non-secret like `"client_email"` is not.
- **Low-severity hardening bundle from the re-audit** (#303). The `detect_capabilities` version probe now runs through `_spawn`, so it too starts a new process group and kills the whole group on timeout (L-1, matching the main run path). The injection scanner's invisible-character set is extended to direction marks (LRM/RLM/ALM), invisible math operators, soft hyphen, CGJ, Mongolian vowel separator, and Hangul fillers, and its base64 heuristic now covers URL-safe `-_` (L-2). The cache `clear()` also rotates (deletes) the per-user `.hmac_key` (L-3); entries are written atomically via a temp file + `replace` (L-4); and a cache file is size-capped (8 MiB) before parsing so a giant attacker-planted entry can't be read into memory before its MAC is rejected (L-5).

## [1.3.0] - 2026-06-07

> Security-hardening release (audit + fixes for #287–#296). A few defaults are
> tightened and may require action: a **non-loopback local-model `endpoint`** is
> now rejected unless you set `JURY_ALLOW_REMOTE_ENDPOINT=1`, a **relative-path
> agent `command`** (e.g. `./bin/x`) is rejected (use a bare name or absolute
> path), and the **read-only sandbox is always enforced** for reviewers.

### Changed

- **README: the version badge now reads from PyPI** (#281). The `github/v/release` shields.io badge frequently rendered "Unable to select next GitHub token from pool" (a transient failure of shields.io's shared GitHub token pool); swapped for `pypi/v/ai-jury`, which uses a different, more reliable source and shows the actually-installable version.

### Added

- **Docs: a live dogfood case study** (`docs/case-study-dogfood-v1.2.0.md`, in the docs portal). Logs the jury (codex + agy + qwen, no Claude) reviewing the five PRs that became v1.2.0: 5 real bugs caught before merge (incl. a crash and a check-disabling bypass) **and** 3 false positives the chair wrongly "verified" — with the honest lesson that a non-executing verifier confirms plausible-but-wrong findings, so a panel is a high-recall finder that still needs executed verification.

### Security

- **A whole-codebase security audit** (`docs/security-audit-2026-06-07.md`) — static review across four attack surfaces (subprocess/sandbox, network/SSRF, prompt-injection/redaction, filesystem/parsing) with the High findings verified in source. Tracked as #288–#293.
- **The read-only sandbox is now enforced at the adapter layer, not left to config** (#288). The mandatory restriction flags lived entirely in each agent's `extra_args`, so an empty or misconfigured `jury.toml` (`extra_args = []`, a dropped `--disallowed-tools`/`-s read-only`/`--sandbox`) produced a write/tool-capable reviewer of an attacker-controlled diff, and the privilege audit only *warned*. `build_argv` now passes `extra_args` through `privilege.enforce_read_only`, which **guarantees** claude's write tools are in `--disallowed-tools` (merging into any existing value), and injects the secure-default sandbox for codex (`-s read-only`) / agy (`--sandbox`) when none is configured. Config may still knowingly *widen* a codex sandbox (an audited opt-in), but can no longer *remove* the restriction. Local (network) and unknown vendors are left untouched.
- **The privilege audit no longer trusts a bare `--sandbox` token blindly** (#292). `_is_sandboxed` is now vendor-aware: only the agy/gemini boolean `--sandbox` counts as a bare sandbox, while a codex sandbox must carry an explicit restricting value (`read-only`). A misleading `["--sandbox", "--dangerously-skip-permissions", "--yolo"]` on a non-agy vendor — previously judged "fully safe" — is now correctly surfaced.
- **Review prompts are delivered on stdin, never as process arguments** (#287). `claude -p <prompt>` and `agy --print <prompt>` placed the prompt — which embeds the redacted diff and PR/issue context — in argv, where any same-host process/user could read it via `ps`/`/proc/<pid>/cmdline`. Both now pipe the prompt on stdin (matching codex), so no real adapter exposes prompt content in the process list. Verified the CLIs read stdin in print mode (claude 2.1.x, agy 1.0.6).
- **Local-model endpoint is validated and reached over an http(s)-only, redirect-free opener** (#291). The `endpoint` from `jury.toml`/`--local-endpoint` flowed straight into `urllib`, whose default opener honors `file://`/`ftp://` — an SSRF/local-file-read primitive given this tool reviews attacker-controlled PRs/configs. `validate_config` now rejects any non-`http`/`https` scheme (hard error); a **non-loopback host is a hard error by default** and only allowed when the operator opts in via the `JURY_ALLOW_REMOTE_ENDPOINT` env var (kept out of the attacker-controlled config), then degrading to a warning (plus a cleartext warning for plaintext `http`). Every LocalAdapter request goes through an `OpenerDirector` that registers **no file/ftp handlers and no redirect handler**, so non-http(s) URLs raise `URLError` and a `3xx` to an internal/metadata host (e.g. `169.254.169.254`) is never followed — both surfaced after a cross-model jury review of the initial fix.
- **Low-severity hardening bundle** (#293): a **relative-path agent `command`** (`./tools/codex`) is now a config error — use a bare name (PATH) or absolute path, so a binary can't be run from an attacker-influenced relative location (F-6); agent subprocesses are spawned in their **own process group and the whole group is killed on timeout**, so a wrapper CLI can't leak orphaned grandchildren (F-7); the untrusted local-endpoint **error body is redacted** before it reaches the report (F-8); local-model **HTTP responses are capped** at 16 MiB so a malicious endpoint can't OOM the process (F-9); and cache entries now **embed and verify their key** and the cache dir is created **owner-only (0700)**, so another local user can't plant a forged verdict (F-10).
- **Redaction now covers `password=`, `aws_secret_access_key=`, and common provider tokens** (#289, #290). The `secret_assignment` pattern only recognized `api_key`/`secret`/`token` *anchored* at the start of the key, so `password = "…"` was missed entirely and `aws_secret_access_key=…` slipped through (the required `=` followed `_access_key`, not `secret`). The key side now allows surrounding identifier chars (so a keyword embedded mid-name still matches) and includes `password`/`passwd`/`access_key`/`private_key`/`client_secret`/`credential`. Added explicit patterns for Slack (`xox…`), Google (`AIza…`), Stripe (`sk_live_…`/`rk_live_…`), GitHub fine-grained PATs (`github_pat_…`), and JWTs. These ran verbatim into agent prompts and the rendered report before.
- **Cache entries are now MAC-protected and an untrusted cache dir is refused** (#295, follow-up to F-10). Each entry carries a **per-user HMAC-SHA256** keyed by a secret stored `0o600` at `<cache_dir>/.hmac_key`; an entry with a missing or wrong MAC (a forgery, or a legacy pre-MAC entry) is a miss. `store`/`load` also **fail closed** on a group/other-writable cache dir — `store` tightens a dir it owns to `0700` and refuses to write if it can't (no longer silently suppressing the `chmod` failure), and `load` never trusts a world-writable dir. POSIX only (Windows ACLs aren't represented in `st_mode`).
- **Opt-in strict absolute-path agent commands** (#296, follow-up to F-6). Setting `JURY_REQUIRE_ABSOLUTE_COMMAND=1` makes every agent `command` require an **absolute path** — rejecting even a bare name, whose `PATH` resolution an attacker controlling a CI runner's `PATH` could hijack with a shim. Off by default (bare names stay convenient for local use). `--doctor` now also prints the **resolved absolute path** each CLI command maps to, so an operator can verify which binary will run.

## [1.2.0] - 2026-06-05

### Fixed

- **`gh` calls are now time-bounded** (#246). `_gh` and `_gh_with_input` ran `subprocess.run` without a `timeout=`, so a stalled network call or an interactive auth/2FA prompt could hang the whole jury run indefinitely. Both now pass a 90 s ceiling and convert `TimeoutExpired` into a clear, fail-soft `RuntimeError` (`gh … timed out after 90s`), consistent with other gh failures.
- **`redaction_count` no longer inflated for a chunked review with expanded context** (#249). The same context is reviewed against every chunk, so redacting it inside each per-chunk `run_jury` counted its secrets once per chunk and `_merge_chunk_outcomes` summed them (a 1-secret context over 8 chunks reported 8). The context is now redacted **once** in `review_diff` before fan-out and its count added back a single time; per-chunk diff redactions are still counted per chunk (correct, each diff is distinct). Full (non-chunked) reviews are unaffected.
- **Anti-bias: the verification prompt no longer exposes reviewer identities** (#250). `_format_findings_for_verify` emitted `(by {reviewer})`, so the chair could see which agent raised each candidate finding while judging it — a self-preference gap the debate (#37) and synthesis (#38) anonymization already closed, but the verify phase never did. Reviewer attribution is dropped from the verify input (verdicts match back by `file`/`line`/`claim`); the rendered report still attributes every finding by real agent name.

### Added

- **PR descriptions are now enforced, not just templated** (#271). A GitHub PR template only pre-fills the body — it can't stop a PR being opened/merged empty (as #270 was). Added a `Related issues` (`Closes #N`) section to `.github/pull_request_template.md` and a `pr-lint` workflow (`.github/workflows/pr-lint.yml`) that fails a PR whose description has no real summary (< 20 chars of prose, excluding headings/checklists) or no issue reference (`#N`, or an explicit `no issue` opt-out). Pure stdlib Python, reads the body from the event payload via an env var (no shell injection). To make it blocking, add **PR description lint / PR has a real description + linked issue** to the branch-protection required checks (one-time repo setting).
- **Friendly first-impression CLI surface** (#265). Running bare `jury` with no arguments **in a terminal** now prints a compact overview — one line on what it does plus the handful of commands most people use — and exits 0, instead of the argparse error. Non-interactive use (piped/CI) keeps the strict `provide one of --pr/--issue/--diff-file` error + non-zero exit. Adds two argv-intercept subcommands: `jury examples` (common example commands) and `jury guide` (a short end-to-end walkthrough).
- **TL;DR verdict callout at the top of every report.** The report now opens with a one-line `> ⚡ **TL;DR · <verdict>**` callout so the outcome is the first thing a reader sees — above the panel and the full breakdown. The headline is the panel vote's verdict when voting, otherwise the chair's `## Verdict` line lifted verbatim from the synthesis (works for both PR review — APPROVE/COMMENT/REQUEST CHANGES — and issue triage — READY/NEEDS-INFO/UNCLEAR). Purely additive and deterministic: omitted when no verdict is available (failed/absent synthesis), never replacing a section. Report goldens regenerated.

## [1.1.1] - 2026-06-05

### Fixed

- **Credibility-cluster bugs the jury found reviewing its own repo**:
  - #245 — `--fail-on blocker` now fires: `blocker` is normalized to its documented `critical` alias instead of a gate that silently never triggers.
  - #247 — a verifier-**rejected** (`unsupported`) major finding no longer drives a `high` risk level.
  - #248 — the **JSON** report's metadata now reflects an effective `--decision vote` (the JSON path threads `decision`/`vote`, not just markdown).
  - #251 — a reviewer that **abstained** (empty reply or a short refusal) is dropped from a panel vote instead of counting as a 'clear' (APPROVE/READY) vote.
  - #252 — docs (`architecture.md`, `CLAUDE.md`) now match the actual Pages deploy trigger (push to `main`).
  - #253 — corrected the stale `CodexAdapter` comment (shipped default is `-s read-only`).
  - #254 — the release **SBOM** is built from an isolated wheel-only venv (the package + its declared, empty runtime deps), not `cyclonedx-py environment` over the whole runner (`pyproject` is not a valid `cyclonedx-py` subcommand, which would have failed the publish).

### Changed

- **Benchmark: measured the panel's lift over each model, published honestly.** Added `benchmark/sweep.py` — runs each reviewer **solo** vs the **panel** vs the **full jury** over the labeled fixtures (`benchmark/`) — and `docs/benchmark-results.md`. Live v1.1.0 (2026-06-05, 4 vendors: claude/codex/agy + local `qwen2.5-coder:7b`; each cloud reviewer used its CLI's default model at run time, not pinned): run alone, **every** model missed seeded bugs (best 67%, worst 33%, all at 100% precision); the **panel caught 100%** — so **vendor diversity robustly lifts recall**, whichever single you'd have picked. The precision/verification effect is **within noise at N=5** (jury precision varied 1.00↔0.60 between runs) and is **not** claimed. Surfaced on the homepage "Why a panel" section + the README benchmark section; Magpie stays credited in the prior-art/comparison docs (not re-stated on the results page). No sitemap/robots change — docs pages are `docs.html` fragments, not separate URLs.
- **Docs: dropped the stale "MVP" framing.** `feasibility.md` (research grounding) still called the now-shipped v1.1.0 project an "MVP" in the present tense ("this MVP", "MVP run observations", "running the MVP"). Reworded to "first version / v1 / ai-jury" and retitled the run notes "Live-run observations"; the historical research context is unchanged.
- **Docs: broadened the ecosystem comparison & prior art.** Added two categories to `comparison.md` — *host-assistant plugins* (e.g. open-code-review: multi-persona debate inside one host/vendor) and *per-rule CI checks* (e.g. Continue: no cross-agent debate) — and noted Calimero (Anthropic-only consensus) under API-level. README prior-art now cites VulTrial (ICSE 2026), the academic prosecutor/defense/judge/jury approach. Framing verified against each project's own docs.
- **Website: refreshed hero + OG banner art.** Updated `website/assets/` (`hero.svg`/`hero-light.svg`, `hero.png` 2400×1260, `og-banner.png` 1200×630) to the new design and bumped `sitemap.xml` lastmod to 2026-06-05.
- **Taglines now reflect the full scope.** The one-line descriptions in the README, the skill (`SKILL.md`), the plugin manifest, and `positioning.md` said only "review a PR/diff → a chair synthesizes one verdict"; they now read "review a diff, PR, or issue → cross-examine → verify → one verdict, a chair's synthesis **or a panel vote**", matching the shipped feature set.
- **Docs/site: published the full live-run report.** The verbatim Markdown report from the v1.1.0 four-vendor run is now a docs page (`docs/live-review-report.md`, dated *v1.1.0 · 2026-06-04*), registered in the portal and linked from the live-review page and the homepage "Real run" card — every finding/verification/verdict exactly as the tool wrote it. Future re-runs (after fixes) can be added as their own dated reports.
- **README refresh** (closes #255). The **Status** "Shipped" list now includes issue review, chair-or-panel-vote, live play-by-play, full-transcript/verbose, and the `jury init --wizard`; the **CLI compatibility contract** "Stable flags" list was expanded to the full current parser surface (grouped by intent); and the error-message table no longer implies `--post-summary` is PR-only (it posts to `--issue` too).
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
