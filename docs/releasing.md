# Releasing: provenance, checksums, and verification

This document describes how `agent-review-council` release artifacts are built,
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
(OIDC) instead of a long-lived API token. This requires a **one-time** setup on
PyPI: add a trusted publisher for this repository and the `publish.yml` workflow.
Until that is configured, the publish step is non-fatal (`continue-on-error`) so
a release still produces verifiable GitHub artifacts.

## How to verify a release

Download the artifacts and `SHA256SUMS` from the GitHub Release, then:

```bash
# 1. Integrity — checksums must match.
sha256sum -c SHA256SUMS

# 2. Provenance — verify the attestation against this repository.
gh attestation verify agent_review_council-<version>-py3-none-any.whl \
  --repo berkayturanci/agent-review-council

# 3. SBOM — inspect the bill of materials.
cat sbom.cdx.json | jq '.components[].name'
```

A failed checksum or attestation means the artifact does not match what the
release workflow produced — do not install it, and open a security report (see
[SECURITY.md](../SECURITY.md)).
