# Public release readiness checklist

The bar to clear before promoting `agent-review-council` broadly, plus the manual
release and rollback steps. Items are split into **Required before public** (must be
true to announce/publish) and **Nice-to-have** (improves polish, not a blocker).

## Required before public

### Package & metadata
- [ ] `pyproject.toml` metadata correct: name, description, `requires-python`, license,
      classifiers, `project.urls`, `console_scripts` (`council`).
- [ ] Version bumped and consistent across `pyproject.toml`, `__init__.__version__`,
      and `CHANGELOG.md`.
- [ ] `pip install -e .` and `python -m build` (sdist + wheel) succeed cleanly.
- [ ] PyPI **trusted publishing** (OIDC) configured for the repo (one-time PyPI
      trusted-publisher setup); `publish.yml` publishes without a long-lived token.
- [ ] Release artifacts include **checksums** (`SHA256SUMS`), a **CycloneDX SBOM**
      (`sbom.cdx.json`), and a **build-provenance attestation** — see
      [docs/releasing.md](releasing.md) for how they are built and verified (#25).

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
- [ ] Skill install instructions verified (`skill/review-council/`).
- [ ] `SECURITY.md` data-flow/redaction reference matches the code.
- [ ] No downstream/private project names anywhere (project-agnostic).

### Repository hygiene
- [ ] LICENSE present (MIT) and referenced.
- [ ] Issue templates and PR template present.
- [ ] `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SUPPORT.md` present and accurate.
- [ ] CI / CodeQL / Scorecard badges resolve in the README.
- [ ] Dependabot configured.

## Nice-to-have

- [ ] Public website / landing page (roadmap [#14](https://github.com/berkayturanci/agent-review-council/issues/14)).
- [ ] Polished README hero visual (roadmap [#46](https://github.com/berkayturanci/agent-review-council/issues/46)).
- [ ] Plugin manifests + platform support matrix (roadmap [#45](https://github.com/berkayturanci/agent-review-council/issues/45)).
- [ ] OpenSSF Scorecard score reviewed and low-hanging items addressed.
- [ ] Asciinema/GIF demo of a real run.

## Versioning policy

- Semantic Versioning. Pre-1.0: minor versions may include breaking changes; document
  them in `CHANGELOG.md` under the new version.
- The `[Unreleased]` section accumulates changes; renamed to the version + date at release.

## Manual release steps

1. Confirm every **Required before public** box above is checked.
2. Update `CHANGELOG.md`: rename `[Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`.
3. Bump the version in `pyproject.toml` and `src/agent_review_council/__init__.py`.
4. Open a release PR; wait for green CI; merge.
5. Tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.
6. Create the GitHub Release from the tag (paste the changelog section). This triggers
   `publish.yml` to build sdist+wheel, generate the SBOM and `SHA256SUMS`, attest build
   provenance, publish to PyPI via trusted publishing, and attach all assets.
7. Verify the artifact: `pipx install agent-review-council==X.Y.Z` then
   `council --version` and `council --mock --diff-file examples/sample.diff`.
8. Verify supply-chain metadata per [docs/releasing.md](releasing.md): `sha256sum -c
   SHA256SUMS` and `gh attestation verify <wheel> --repo berkayturanci/agent-review-council`.
9. Confirm the PyPI page, README rendering, and badges.

## Rollback notes

- **Bad release on PyPI:** PyPI does not allow re-uploading a version. **Yank** the
  affected version on PyPI (hides it from new installs without breaking pins), then
  publish a fixed `X.Y.Z+1`. Never delete-and-reupload the same version number.
- **Bad GitHub Release/tag:** delete the GitHub Release and the tag
  (`git push --delete origin vX.Y.Z`), fix, and re-tag a new patch version.
- **Bad docs/site only:** revert the offending commit on `main`; no version bump needed.
- Record the incident and the fix version in `CHANGELOG.md`.
