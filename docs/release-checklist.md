# Public release readiness checklist

The bar to clear before promoting `ai-jury` broadly, plus the manual
release and rollback steps. Items are split into **Required before public** (must be
true to announce/publish) and **Nice-to-have** (improves polish, not a blocker).

## Required before public

### Package & metadata
- [ ] `pyproject.toml` metadata correct: name, description, `requires-python`, license,
      classifiers, `project.urls`, `console_scripts` (`jury`).
- [ ] Version bumped and consistent across `pyproject.toml`, `__init__.__version__`,
      `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, and `CHANGELOG.md`.
      (CI enforces the pyproject ↔ plugin.json equality via `tests/test_release_metadata.py`;
      `publish.yml` re-checks both against the tag at release time.)
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

1. Confirm every **Required before public** box above is checked.
2. Determine target version (`MAJOR`, `MINOR`, or `PATCH`) per SemVer rules.
3. Update `CHANGELOG.md`: rename `[Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`.
4. Bump the version in `pyproject.toml`, `src/ai_jury/__init__.py`, `uv.lock`,
   `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, `Formula/ai-jury.rb`,
   `website/index.html`, and `website/app.js`.
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

## Rollback notes

- **Bad release on PyPI:** PyPI does not allow re-uploading a version. **Yank** the
  affected version on PyPI (hides it from new installs without breaking pins), then
  publish a fixed `X.Y.Z+1`. Never delete-and-reupload the same version number.
- **Bad GitHub Release/tag:** delete the GitHub Release and the tag
  (`git push --delete origin vX.Y.Z`), fix, and re-tag a new patch version.
- **Bad docs/site only:** revert the offending commit on `main`; no version bump needed.
- Record the incident and the fix version in `CHANGELOG.md`.
