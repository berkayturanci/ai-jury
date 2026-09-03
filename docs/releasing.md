# Releasing: provenance, checksums, and verification

This document describes how `ai-jury` release artifacts are built,
what supply-chain metadata ships with them, and how to verify a release (issue
#25). Releases are cut by pushing a `v*` tag, which triggers
[`.github/workflows/publish.yml`](../.github/workflows/publish.yml).

## What a release contains

For each release, the publish workflow builds and attaches:

- **`*.whl` and `*.tar.gz`** — the wheel and sdist, built with `python -m build`.
- **`SHA256SUMS`** — SHA-256 checksums over every artifact (including the SBOM).
- **`sbom.cdx.json`** — a [CycloneDX](https://cyclonedx.org/) software bill of
  materials. The project has **zero runtime dependencies**, so the SBOM is small
  by design; it is generated on every release rather than deferred.
- **Build provenance attestation** — a signed attestation binding the artifacts
  to the exact workflow run that produced them
  ([SLSA-style](https://slsa.dev/) provenance via GitHub artifact attestations).

## How artifacts are built

1. A maintainer pushes a `v<version>` tag (see [release-checklist](release-checklist.md)).
2. The workflow checks out the tag, builds the sdist + wheel on a pinned Python,
   generates the SBOM and `SHA256SUMS`, attests build provenance, publishes to
   PyPI via **trusted publishing**, and creates the GitHub Release with all
   assets attached.

All GitHub Actions are pinned to full commit SHAs (see
[CONTRIBUTING](../CONTRIBUTING.md)).

### PyPI trusted publishing

Publishing uses [PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/)
(OIDC) instead of a long-lived API token. This required a **one-time** setup on
PyPI — a trusted publisher for this repository and the `publish.yml` workflow — which
is now configured and has been used for every release since. The publish step is
**not** `continue-on-error`: a failed upload fails the release loudly so a broken
publish can't pass silently. `skip-existing` keeps re-runs idempotent.

## Which files carry the version

One table, [`scripts/release_surfaces.py`](../scripts/release_surfaces.py), lists
every file that names the release — package metadata, `uv.lock`, both plugin
manifests, the website, the README, the cookbook, and the Homebrew formula —
together with the pattern that reads the version out of each.

Three guards read that table, and none of them keeps its own copy:

| Guard | Question it asks |
| --- | --- |
| `scripts/verify_merge.py --check-version` (CI, `fetch-depth: 0`) | Do the surfaces present agree, and has the version not gone backwards from the last `v*` tag? |
| `scripts/verify_merge.py --check-surfaces` (`make release-check`) | Does **every** listed surface name what `pyproject.toml` declares? |
| `tests/test_release_metadata.py`, `tests/test_homebrew_formula.py` | The same question, in the unit suite, plus the formula's url and digest. |

Registering a new surface is one line in that table (#665). It used to be three
lines in three files, and the surface that was missed is the one that went stale:
`website/index.html` and `website/app.js` sat at v1.14.4 through two releases
(#646).

## Semantic Versioning (SemVer 2.0.0) Policy

`ai-jury` strictly adheres to [Semantic Versioning 2.0.0](https://semver.org/):

Given a version number `MAJOR.MINOR.PATCH`:

1. **MAJOR (`X.0.0`)** — Increment when making incompatible API or CLI changes (e.g., removing CLI flags, breaking config schema in `jury.toml`, or breaking adapter protocol).
2. **MINOR (`x.Y.0`)** — Increment when adding functionality in a backward-compatible manner (e.g., adding new CLI commands like `jury apply`, introducing `--hints` or `--tiered`, adding new LLM provider adapters, or extending structured finding schema).
3. **PATCH (`x.y.Z`)** — Increment when making backward-compatible bug fixes, internal performance optimizations (e.g., single-pass aggregations in `metadata.py`), security hardening (e.g., stderr secret redactions), documentation synchronization, or distribution/packaging improvements (Homebrew tap, GitHub Action Marketplace).

### How to Calculate the Next Version

Before preparing a release PR:
- Inspect changes since the last release tag: `git log $(git describe --tags --abbrev=0)..HEAD --oneline`
- If **any** change is breaking / backward-incompatible → Bump `MAJOR` (reset `MINOR` and `PATCH` to 0).
- Else if **any** change adds a new user-facing feature/flag/adapter → Bump `MINOR` (reset `PATCH` to 0).
- Else (purely bug fixes, performance improvements, security hardening, docs, CI, packaging) → Bump `PATCH`.

## Distribution Channels

Every release is automatically published across three primary distribution channels:

1. **PyPI (Python Package Index)**:
   - Automated via OIDC Trusted Publishing in `.github/workflows/publish.yml`.
   - Installable via `pip install ai-jury` or `pipx install ai-jury`.
2. **GitHub Releases & GitHub Action Marketplace**:
   - Automated via `publish.yml` using `softprops/action-gh-release`.
   - GitHub Action is consumable as `uses: berkayturanci/ai-jury@v1` or pinned to release tags.
3. **Homebrew Tap (`berkayturanci/homebrew-ai-jury`)**:
   - Automated formula synchronization: `publish.yml` queries PyPI for the uploaded sdist's immutable URL and SHA-256 digest, updates `Formula/ai-jury.rb`, and syncs to `berkayturanci/homebrew-ai-jury`.
   - This is not one automatic pass: the formula's `url` and `sha256` cannot both be
     correct before the tag exists — the release bump moves the `url` ahead of the
     sdist that will satisfy it, so the digest committed in the release PR
     necessarily belongs to the *previous* release. The digest becomes knowable only
     once PyPI has the new sdist, so the workflow corrects the formula immediately
     after publishing rather than leaving the stale digest in place. See
     [The Homebrew release chain](homebrew-release-chain.md) for the full mechanism,
     every guard involved, and what has already gone wrong.
   - Installable via `brew install berkayturanci/ai-jury/ai-jury` or `brew install ai-jury`.

## How to verify a release

After the release workflow completes, verify each distribution channel:

```bash
# 1. Verify PyPI & Local installation
pipx install --force ai-jury==<version>
jury --version
jury --doctor

# 2. Verify Homebrew Tap
brew update
brew info berkayturanci/ai-jury/ai-jury
brew fetch --formula berkayturanci/ai-jury/ai-jury

# 3. Verify Supply-Chain Metadata
sha256sum -c SHA256SUMS
gh attestation verify ai_jury-<version>-py3-none-any.whl --repo berkayturanci/ai-jury
cat sbom.cdx.json | jq '.components[].name'
```

A failed checksum or attestation means the artifact does not match what the
release workflow produced — do not install it, and open a security report (see
[SECURITY.md](../SECURITY.md)).
