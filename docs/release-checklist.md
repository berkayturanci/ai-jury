# Public release readiness checklist

The bar to clear before promoting `ai-jury` broadly, plus the manual
release and rollback steps. Items are split into **Required before public** (must be
true to announce/publish) and **Nice-to-have** (improves polish, not a blocker).

## Required before public

### Package & metadata
- [ ] `pyproject.toml` metadata correct: name, description, `requires-python`, license,
      classifiers, `project.urls`, `console_scripts` (`jury`).
- [ ] `make release-check` passes — every file that names the version agrees with
      `pyproject.toml`. The surface list is [`scripts/release_surfaces.py`](../scripts/release_surfaces.py),
      the single table read by the CI guard (`scripts/verify_merge.py`),
      `tests/test_release_metadata.py`, and `tests/test_homebrew_formula.py`.
      Adding a surface is one line there. (`publish.yml` re-checks the version
      against the tag at release time.)
- [ ] `pip install -e .` and `python -m build` (sdist + wheel) succeed cleanly.
- [ ] PyPI **trusted publishing** (OIDC) configured for the repo (one-time PyPI
      trusted-publisher setup); `publish.yml` publishes without a long-lived token.
- [ ] Release artifacts include **checksums** (`SHA256SUMS`), a **CycloneDX SBOM**
      (`sbom.cdx.json`), and a **build-provenance attestation** — see
      [docs/releasing.md](releasing.md) for how they are built and verified.

### CI & quality
- [ ] CI green on the full matrix (Python 3.11–3.13; Linux + macOS + Windows).
- [ ] Unit tests + mock CLI smoke test pass.
- [ ] `ruff check` and `ruff format --check` clean.
- [ ] CodeQL has no unresolved high-severity alerts.
- [ ] No secrets committed; secret redaction covered by tests.

### Docs
- [ ] README: install, usage, configuration, data-flow/privacy all current.
- [ ] `docs/architecture.md`, `docs/comparison.md`, `docs/feasibility.md` accurate.
- [ ] `llms.txt` / `llms-full.txt` present and current.
- [ ] Skill install instructions verified (`skill/ai-jury/`).
- [ ] `SECURITY.md` data-flow/redaction reference matches the code.
- [ ] No downstream/private project names anywhere (project-agnostic).

### Repository hygiene
- [ ] LICENSE present (MIT) and referenced.
- [ ] Issue templates and PR template present.
- [ ] `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SUPPORT.md` present and accurate.
- [ ] CI / CodeQL / coverage badges resolve in the README.
- [ ] Dependabot configured.

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

> The steps below are the *what*. For the *why* — the reason the formula's
> url and digest cannot both be correct at the same moment, which guard
> catches which failure, and what has already gone wrong four ways — see
> [The Homebrew release chain](homebrew-release-chain.md).

1. Confirm every **Required before public** box above is checked.
2. Determine target version (`MAJOR`, `MINOR`, or `PATCH`) per SemVer rules.
3. Update `CHANGELOG.md`: rename `[Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`.
4. Bump the version everywhere it is named, then run `make release-check` — it
   names any file left behind. Do not work from a list in this document: this
   step used to spell out eight filenames, and the website was still missed for
   two releases (#646). The surfaces live in
   [`scripts/release_surfaces.py`](../scripts/release_surfaces.py), and a new one
   is registered there once rather than in three separate guards (#665).
5. Open a release PR (`release/vX.Y.Z`); wait for green CI; merge to `main`.
6. Tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.
7. Creating the tag triggers `.github/workflows/publish.yml`, which:
   - Builds sdist + wheel, generates SBOM and `SHA256SUMS`, and attests build provenance.
   - Publishes to PyPI via OIDC Trusted Publishing.
   - Creates the GitHub Release with attached assets.
   - Queries PyPI for the immutable sdist URL and SHA-256 digest, updates `Formula/ai-jury.rb`, and syncs to `berkayturanci/homebrew-ai-jury`.
8. Verify all channels:
   - PyPI & CLI: `pipx install --force ai-jury==X.Y.Z && jury --version && jury --doctor`
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
   # bump CHANGELOG.md, pyproject.toml, __init__.py, plugin manifests,
   # Formula/ai-jury.rb, website/index.html, website/app.js
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
   grep -A1 '^ *url' Formula/ai-jury.rb                                        # in-repo formula
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
   `workflow_dispatch` added to the workflow — tracked by #666.
3. After the re-run, check for duplicate side effects rather than assuming the
   whole workflow is idempotent:
   - the GitHub Release exists exactly once (no second `Release vX.Y.Z`);
   - no duplicate formula follow-up PR — `gh pr list --repo berkayturanci/ai-jury --head chore/formula-X.Y.Z`;
   - if the tap was written, that it now matches PyPI's digest (see "bad tap
     write" above for how to check).

### Bad docs/site only

Revert the offending commit on `main`; no version bump needed.

### After any of the above

Record the incident and the fix version in `CHANGELOG.md`.
