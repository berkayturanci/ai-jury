# Public release readiness checklist

The bar to clear before promoting `ai-jury` broadly, plus the manual
release and rollback steps. Items are split into **Required before public** (must be
true to announce/publish) and **Nice-to-have** (improves polish, not a blocker).

## Required before public

**Cleared — and re-verified on 2026-09-04 (#686).** This section is the bar for
the *first* public release, and that release happened long ago: 1.15.1 is on
PyPI, served by the Homebrew tap `berkayturanci/homebrew-ai-jury`, and the plugin
manifests ship in the repository. The boxes sat unticked for fifteen releases
anyway, which made the whole section unreadable — a reader could not tell an
outstanding item from a stale one.

Each box now names *where to re-check it* rather than asking anyone to remember.
Most are held by a job that runs on every push or every tag, so re-verifying the
section is reading those jobs, not repeating their work by hand.

### Package & metadata
- [x] `pyproject.toml` metadata correct: name, description, `requires-python`
      (`>=3.11`), `license = "MIT"`, classifiers, `project.urls`, and the console
      script `jury = "ai_jury.cli:main"` — all in `pyproject.toml`'s `[project]`
      table, and pinned by
      `tests/test_release_metadata.py::PackagingMetadataIsComplete`: it asserts
      the identifying fields, that both PyPI sidebar URLs are present and
      `https://`, that the `jury` entry point names a `main()` that exists, and
      that the Python classifiers still agree with `requires-python`. (Before
      #686 this box cited that module for all of it, when every test in it was
      about version lockstep.)
- [x] `make release-check` passes — every file that names the version agrees with
      `pyproject.toml`. The surface list is [`scripts/release_surfaces.py`](../scripts/release_surfaces.py),
      the single table read by the CI guard (`scripts/verify_merge.py`), its
      `--check-surfaces` entry point, and `tests/test_release_metadata.py`.
      Adding a surface is one line there. The Homebrew formula is not among them —
      see [The Homebrew release chain](homebrew-release-chain.md). (`publish.yml` re-checks the version
      against the tag at release time.) The `version-integrity` job in
      `.github/workflows/ci.yml` runs the same check on every push, so this box
      cannot come untucked between releases.
- [x] `pip install -e .` and `python -m build` (sdist + wheel) succeed cleanly —
      the editable install is the first step of every `test` matrix leg
      (`.github/workflows/ci.yml`), and `python -m build --sdist --wheel` is the
      build step of `.github/workflows/publish.yml`, which has produced every
      release through 1.15.1.
- [x] PyPI **trusted publishing** (OIDC) configured for the repo (one-time PyPI
      trusted-publisher setup); `publish.yml` publishes without a long-lived token
      — `id-token: write` in the publish job's `permissions`, and a pinned
      `pypa/gh-action-pypi-publish` step with no password input.
- [x] Release artifacts include **checksums** (`SHA256SUMS`), a **CycloneDX SBOM**
      (`sbom.cdx.json`), and a **build-provenance attestation** — all three are
      produced in `.github/workflows/publish.yml` (`cyclonedx-py environment`,
      `sha256sum … > SHA256SUMS`, `actions/attest-build-provenance`) and attached
      to the GitHub Release; the v1.15.1 Release carries the wheel, the sdist,
      `sbom.cdx.json` and `SHA256SUMS`. See [docs/releasing.md](releasing.md) for
      how they are built and verified.

### CI & quality
- [x] CI green on the matrix in `.github/workflows/ci.yml`: Python 3.11, 3.12 and
      3.13 on Linux, plus 3.13 on macOS and 3.13 on Windows. It is deliberately
      not every version on every OS — the two cross-OS legs exist to prove
      subprocess and path behaviour, and the matrix comment says so.
- [x] Unit tests + mock CLI smoke test pass — every matrix leg runs
      `unittest discover -s tests` and then
      `python -m ai_jury --mock --diff-file examples/sample.diff`, asserting the
      rendered report contains the chair's verdict.
- [x] Adapter invocation contracts locked offline — `tests/test_adapter_contracts.py`
      drives every shipped adapter's real argv / stdin / parse path against the
      recorded fixtures in `tests/fixtures/contracts/`, checked against
      `tests/golden/adapter_contracts.json`. It needs no auth, network or spend,
      so it runs on every matrix leg. A vendor CLI change is expected to fail it;
      re-record the golden **and** say which CLI changed in `CHANGELOG.md`.
- [ ] **Live smokes before any release that touched `src/ai_jury/adapters.py`** —
      `JURY_LIVE=1 make live-smoke` (or `make live-smoke`), on a machine with the
      real `claude`, `codex` and `agy` CLIs installed and authenticated. The
      offline lock proves the adapters still *build* the recorded invocation; only
      a live run proves the installed CLI still *accepts* it. That is the gap
      #635 fell through: every offline check was green while `agy` contributed
      nothing to the panel for a whole release. `tests/live/test_live_contracts.py`
      is the contract half (it asserts a review came back, not merely exit 0) and
      `tests/live/test_live_smoke.py` the end-to-end half; both skip per agent
      whose CLI is absent or unauthenticated. Left unchecked on purpose — it is a
      per-release action, not a standing property.
- [x] `ruff check` and `ruff format --check` clean — the `lint` job, on a ruff
      pinned to the same version as the `ruff-format` pre-commit hook
      (`tests/test_ruff_pin.py` asserts the two never drift apart).
- [x] CodeQL has no unresolved high-severity alerts —
      `.github/workflows/codeql.yml`. Re-check with
      `gh api 'repos/berkayturanci/ai-jury/code-scanning/alerts?state=open'
      --jq '[.[] | select(.tool.name == "CodeQL")]'`. Keep the `tool.name` filter:
      the open alerts in that inbox are mostly *stale Scorecard* findings, left
      behind when #201 stopped uploading Scorecard's SARIF to code scanning on
      2026-06-03 (`.github/workflows/scorecard.yml` uploads an artifact and
      publishes to the OpenSSF API instead, deliberately). They carry `high`
      security severities and reading them as CodeQL results is the mistake this
      box invites — they are the Nice-to-have bar below, not this one.
- [x] No secrets committed; secret redaction covered by tests —
      `src/ai_jury/redaction.py`, `tests/test_redaction.py` and
      `tests/test_git_error_redaction.py`, with the data flow and the detector
      table written up in `SECURITY.md`.

### Docs
The docs boxes were re-verified by a full read-through of README, `docs/`,
`CONTRIBUTING.md` and the website against `origin/main` on 2026-09-04 (#686);
what that audit found stale is fixed in the same change.

- [x] README: install, usage, configuration, data-flow/privacy all current.
- [x] `docs/architecture.md`, `docs/comparison.md`, `docs/feasibility.md` accurate.
- [x] `llms.txt` / `llms-full.txt` present and listing the current docs set.
- [x] Skill install instructions verified — `skill/ai-jury/SKILL.md`, linked from
      the README and from `docs/platforms.md`.
- [x] `SECURITY.md` data-flow/redaction reference matches the code — its
      "Jury data flow & redaction" section names `redaction.py` and its detectors.
- [x] No downstream/private project names anywhere (project-agnostic). Keel is
      named in `docs/cookbook.md` §16 and §21, but as a *public* sibling project
      with a public integration — the documented exception, not a leak.

### Repository hygiene
- [x] LICENSE present (MIT) and referenced — `LICENSE`, plus `license = "MIT"` in
      `pyproject.toml` and the licence badge in the README.
- [x] Issue templates and PR template present — `.github/ISSUE_TEMPLATE/`
      (`bug_report.yml`, `feature_request.yml`, `config.yml`) and
      `.github/pull_request_template.md`.
- [x] `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SUPPORT.md` present and accurate.
- [x] CI / coverage / CodeQL / PyPI / licence badges resolve in the README's first
      screenful; the coverage badge is served from `https://ai-jury.dev`.
- [x] Dependabot configured — `.github/dependabot.yml`: weekly, for the pinned
      GitHub Actions SHAs and for the Python tooling declared in `pyproject.toml`.

## Nice-to-have

- [x] Public website / landing page ([#14](https://github.com/berkayturanci/ai-jury/issues/14)) — shipped, live on GitHub Pages (`pages.yml`).
- [x] Polished README hero visual ([#46](https://github.com/berkayturanci/ai-jury/issues/46)) — `docs/assets/hero.png` + refreshed favicon/OG set.
- [x] Plugin manifests + platform support matrix ([#45](https://github.com/berkayturanci/ai-jury/issues/45)) — `.claude-plugin/{marketplace,plugin}.json` + `docs/platforms.md`.
- [ ] OpenSSF Scorecard score reviewed and low-hanging items addressed (workflow runs per commit).
- [ ] Asciinema/GIF demo of a real run.

## Versioning policy

- Strict **Semantic Versioning 2.0.0** (`MAJOR.MINOR.PATCH`).
- Pre-1.0: minor versions may include breaking changes. Post-1.0: breaking changes require a `MAJOR` bump.
- Calculate next version according to the rules in [docs/releasing.md](releasing.md#semantic-versioning-semver-200-policy).
- The `[Unreleased]` section in `CHANGELOG.md` accumulates changes; renamed to the version + date at release.

## Manual release steps

> The steps below are the *what*. For the *why* — the reason a formula's url and
> digest cannot both be correct at the same moment, why no formula is committed
> here because of it, which guard catches which failure, and what has already
> gone wrong five ways — see
> [The Homebrew release chain](homebrew-release-chain.md).

1. Confirm the **Required before public** section above still holds. Its boxes
   are ticked with the evidence beside each one; a release re-confirms them by
   reading the green CI run for the release commit, not by repeating the checks.
2. Determine target version (`MAJOR`, `MINOR`, or `PATCH`) per SemVer rules.
3. Update `CHANGELOG.md`: rename `[Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`.
4. Bump the version everywhere it is named, then run `make release-check` — it
   names any file left behind. Do not work from a list in this document: this
   step used to spell out eight filenames, and the website was still missed for
   two releases (#646). The surfaces live in
   [`scripts/release_surfaces.py`](../scripts/release_surfaces.py), and a new one
   is registered there once rather than in three separate guards (#665).
   **Not** the Homebrew formula — there isn't one committed here; see
   [The Homebrew release chain](homebrew-release-chain.md).
5. Open a release PR (`release/vX.Y.Z`); wait for green CI; merge to `main`.
6. Tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.
7. Creating the tag triggers `.github/workflows/publish.yml`, which:
   - Builds sdist + wheel, generates SBOM and `SHA256SUMS`, and attests build provenance.
   - Publishes to PyPI via OIDC Trusted Publishing.
   - Queries PyPI for the immutable sdist URL and SHA-256 digest, renders
     `packaging/homebrew/ai-jury.rb.template`, and re-downloads the artifact to
     confirm the digest is that artifact's.
   - Creates the GitHub Release with attached assets, the rendered `ai-jury.rb`
     among them, and pushes the formula to `berkayturanci/homebrew-ai-jury`.
   - Runs the `verify` job: installs `ai-jury==X.Y.Z` from PyPI into a clean
     virtualenv, requires `jury --version` to equal the tag and `jury --doctor`
     to run, and compares the tap's digest with the published sdist's. On failure
     it opens or updates a `release-broken: vX.Y.Z` issue.
   - **Commits nothing.** Step 5 above is the only write to `main` a release makes.
8. Read the `verify` job's log — it has already installed the release and run it —
   then spot-check what it cannot see:
   - Homebrew: `brew update && brew info berkayturanci/ai-jury/ai-jury && brew fetch --formula berkayturanci/ai-jury/ai-jury`
   - Supply-chain: `sha256sum -c SHA256SUMS` and `gh attestation verify <wheel> --repo berkayturanci/ai-jury`
9. Confirm the PyPI page, README rendering, and badges.

## Rollback

> **Rehearsal: pending.** The commands below have not yet been run end-to-end
> against a scratch tag; there is no transcript to link. Treat this as a tracked
> follow-up rather than a verified runbook until one exists.

Pick the row that matches what actually happened — they are not mutually
exclusive (a partial publish can turn into a bad tap write, for example), so
work top to bottom and re-check earlier rows after fixing a later one.

| Situation | Symptom | Action |
|---|---|---|
| **Bad PyPI release only** | The package on PyPI is broken; the tag/GitHub Release/tap are fine (or the workflow hasn't reached them yet). | Yank the release, fix forward with a new patch. |
| **Bad tag / GitHub Release** | The tag points at the wrong commit, or the Release notes/assets are wrong. | Delete the GitHub Release and the tag. |
| **Bad tap write** | `berkayturanci/homebrew-ai-jury` serves a broken `Formula/ai-jury.rb` — check this only *if the tap was actually written*; if nothing has picked up the release yet, there is nothing to revert. | Revert the formula commit in the tap. |
| **Partial publish** | PyPI succeeded but the GitHub Release (and/or formula sync) never ran, or vice versa. | Re-run the workflow from the tag; verify each channel afterward. |

### Bad PyPI release only

PyPI does not allow re-uploading a version, and there is no `gh`/`git`/`brew`
command for yanking — it is a PyPI web-console action:

1. Open `https://pypi.org/manage/project/ai-jury/release/X.Y.Z/` and choose
   **Yank release**, with a reason. This hides the version from new installs
   (`pip install ai-jury` skips it) without breaking existing pins
   (`pip install ai-jury==X.Y.Z` still resolves it).
2. Fix forward with a new `PATCH` — never delete-and-reupload the same version
   number:
   ```bash
   git checkout -b release/vX.Y.Z+1 main
   # bump every surface in scripts/release_surfaces.py, then: make release-check
   git commit -am "release: X.Y.Z+1"
   git push -u origin release/vX.Y.Z+1
   gh pr create --base main --title "release: X.Y.Z+1" \
     --body "Fix-forward after yanking X.Y.Z."
   # after the PR merges:
   git tag vX.Y.Z+1 && git push origin vX.Y.Z+1
   ```

### Bad tag / GitHub Release

```bash
gh release delete vX.Y.Z --repo berkayturanci/ai-jury --yes   # deletes the Release + its notes/assets
git push --delete origin vX.Y.Z                                # deletes the remote tag
git tag -d vX.Y.Z                                               # deletes any local copy too
```

Deleting the tag does **not** undo a PyPI publish (PyPI has no delete — use the
row above) and does **not** revert a tap write that already happened from this
tag's run — check the "bad tap write" row if the tap was written. Once the tag,
the Release, and (if applicable) PyPI are all cleaned up, re-tagging `vX.Y.Z`
against the corrected commit is safe; nothing else pins to the tag name the way
the Homebrew formula pins to a specific PyPI URL.

### Bad tap write

Confirm the tap actually served the broken formula before reverting —
`brew info berkayturanci/ai-jury/ai-jury` or reading
`Formula/ai-jury.rb` in `berkayturanci/homebrew-ai-jury` directly. If the tap
was never written for this release, there is nothing to revert yet.

```bash
gh repo clone berkayturanci/homebrew-ai-jury /tmp/homebrew-ai-jury
cd /tmp/homebrew-ai-jury
git log --oneline -- Formula/ai-jury.rb   # find the last-good commit
git revert <bad-commit-sha> --no-edit
git push origin main
```

Then confirm the tap serves the reverted formula:

```bash
brew update
brew info berkayturanci/ai-jury/ai-jury         # confirm the reverted url/sha256
brew fetch --formula berkayturanci/ai-jury/ai-jury
```

Homebrew's local mirror of the tap typically catches up within about 30
minutes of the revert landing on the tap's default branch. If `brew update`
still shows the bad formula sooner than that, it simply hasn't propagated yet
— retry before assuming the revert failed.

### Partial publish

PyPI succeeded but a later step (GitHub Release, formula sync) failed or the
run was cancelled — or the reverse.

1. Check what actually landed on each channel:
   ```bash
   curl -fsSL "https://pypi.org/pypi/ai-jury/X.Y.Z/json" | jq '.info.version'  # PyPI
   gh release view vX.Y.Z --repo berkayturanci/ai-jury                         # GitHub Release
   gh release view vX.Y.Z --repo berkayturanci/ai-jury --json assets \
     --jq '.assets[].name' | grep ai-jury.rb                                   # rendered formula
   ```
2. Re-run the *existing* tag run — `publish.yml` triggers only on `push: tags:
   v*` and has no `workflow_dispatch`, so `gh workflow run publish.yml --ref
   vX.Y.Z` fails with "workflow does not have workflow_dispatch trigger". Find
   the run for the tag and re-run it instead:
   ```bash
   gh run list --repo berkayturanci/ai-jury --workflow publish.yml --branch vX.Y.Z
   gh run rerun <run-id> --failed --repo berkayturanci/ai-jury
   # or, from the Actions tab: re-run the failed job on the existing vX.Y.Z run
   ```
   `skip-existing` on the PyPI publish step only makes *that step* idempotent —
   re-running it after a successful upload does not fail or duplicate anything on
   PyPI. It says nothing about the steps around it. A full re-run from scratch
   (rather than re-running the existing run) needs the tag re-pushed, or
   `workflow_dispatch` added to the workflow. Neither is available today: #666
   removed the second write to `main` and added the post-publish `verify` job,
   but deliberately did not widen how the workflow can be triggered.
3. After the re-run, check for duplicate side effects rather than assuming the
   whole workflow is idempotent:
   - the GitHub Release exists exactly once (no second `Release vX.Y.Z`);
   - the `ai-jury.rb` asset is attached exactly once (a re-run re-uploads it; it
     must not appear twice, and its digest must still be the published sdist's);
   - if the tap was written, that it now matches PyPI's digest (see "bad tap
     write" above for how to check).

### Bad docs/site only

Revert the offending commit on `main`; no version bump needed.

### After any of the above

Record the incident and the fix version in `CHANGELOG.md`.
