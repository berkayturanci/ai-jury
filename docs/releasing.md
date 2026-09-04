# Releasing: provenance, checksums, and verification

This document describes how `ai-jury` release artifacts are built,
what supply-chain metadata ships with them, and how to verify a release (issue
#25). Releases are cut by pushing a `v*` tag, which triggers
[`.github/workflows/publish.yml`](../.github/workflows/publish.yml).

For *why* the Homebrew half of the chain is shaped the way it is — why no formula
is committed to this repository, and what each guard catches — see
[The Homebrew release chain](homebrew-release-chain.md).

## What a release contains

For each release, the publish workflow builds and attaches:

- **`*.whl` and `*.tar.gz`** — the wheel and sdist, built with `python -m build`.
- **`ai-jury.rb`** — the Homebrew formula, rendered from
  `packaging/homebrew/ai-jury.rb.template` with the url and SHA-256 digest PyPI
  reports for the sdist that was just uploaded. Always available at
  `https://github.com/berkayturanci/ai-jury/releases/latest/download/ai-jury.rb`,
  which is how the tap gets it without a credential at either end.
- **`SHA256SUMS`** — SHA-256 checksums over every artifact (including the SBOM
  and the formula).
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
   PyPI via **trusted publishing**, renders the Homebrew formula from what PyPI
   reports, and creates the GitHub Release with all assets attached.
3. A second job, `verify`, then installs the *published* release the way a user
   would — a clean virtualenv, `pip install ai-jury==<version>` from the index —
   and checks that `jury --version` equals the tag, that `jury --doctor` runs,
   and that the digest the Homebrew tap tells `brew` to expect is the digest of
   the sdist PyPI serves. It opens (or comments on) a `release-broken: <tag>`
   issue when any of that fails, because a red workflow nobody is watching is
   how six of the last seven releases went out broken.

Nothing after the tag commits to this repository. A release is exactly one write
to `main`: the release pull request.

Uploading to PyPI is synchronous; being indexed by it is not, and the formula is
rendered from the *published* sdist — a read-after-write against a service that
is only eventually consistent. Both jobs therefore run one shared wait,
`.github/scripts/wait-for-pypi-dists.sh`, which polls
`https://pypi.org/pypi/ai-jury/<version>/json` for five minutes (30 × 10s) until
**both** distributions appear in its `urls` array, and hands back the sdist's own
url and sha256. The five minutes is a ceiling and not an estimate: every request
carries a connect timeout and a maximum time, and the poll stops once the budget
is spent, so a response that stalls after the connection is accepted cannot hold
the step open. Before #694 the render waited only for that endpoint to answer at
all: on v1.16.0 it answered with an empty file list, the read raised
`StopIteration`, and `curl` was handed an empty url — failing *after* the upload
and *before* the GitHub Release, which left 1.16.0 live on PyPI with no release
and no `releases/latest/download/ai-jury.rb`. If the index genuinely never
converges the job now fails with an `::error::` naming the version and the
distribution that never appeared; re-running the job is the recovery, and
`skip-existing: true` keeps the upload step a no-op.

The same rule covers every other command in that workflow that opens a
connection, because the index converging is not the file server answering: the
sdist downloads carry `--connect-timeout` and `--max-time`
(`SDIST_CONNECT_TIMEOUT`/`SDIST_MAX_TIME`, 10s and 120s by default), every `pip`
carries `--timeout`, and every `gh` call is wrapped in `timeout`, which is the
only bound `gh` accepts. Underneath them both jobs set `timeout-minutes` — 30
for the publish job, 20 for `verify` — which bounds the actions the workflow
`uses:` and anything a future step forgets to bound. That ceiling is a backstop
and not the mechanism: a job stopped by `timeout-minutes` is cancelled, so
`verify`'s failure step does not run and no `release-broken` issue is opened,
whereas a command that fails on its own timeout fails the job and files the
report. `tests/test_publish_release_chain.py` enforces the rule over both jobs'
`run:` blocks.

All GitHub Actions are pinned to full commit SHAs (see
[CONTRIBUTING](../CONTRIBUTING.md)).

### PyPI trusted publishing

Publishing uses [PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/)
(OIDC) instead of a long-lived API token. This required a **one-time** setup on
PyPI — a trusted publisher for this repository and the `publish.yml` workflow —
which is now configured and used by every release. The publish step is **not**
`continue-on-error`: a failed upload fails the release loudly so a broken publish
can't pass silently. `skip-existing` keeps re-runs idempotent.

## Which files carry the version

One table, [`scripts/release_surfaces.py`](../scripts/release_surfaces.py), lists
every file that names the release — package metadata, `uv.lock`, both plugin
manifests, the website, the README, and the cookbook — together with the pattern
that reads the version out of each.

The Homebrew formula is not on that list, and its absence is deliberate: #666
deleted `Formula/ai-jury.rb` rather than keep repairing a file whose url and
digest cannot be known before the tag. What is left is
`packaging/homebrew/ai-jury.rb.template`, which names `@VERSION@` until the
release renders it — a placeholder cannot go stale, so nothing needs to watch it.

Three guards read that table, and none of them keeps its own copy:

| Guard | Question it asks |
| --- | --- |
| `scripts/verify_merge.py --check-version` (CI, `fetch-depth: 0`) | Do the surfaces present agree, and has the version not gone backwards from the last `v*` tag? |
| `scripts/verify_merge.py --check-surfaces` (`make release-check`) | Does **every** listed surface name what `pyproject.toml` declares? |
| `tests/test_release_metadata.py` | The same question, in the unit suite, on every pull request. |

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
   - No formula is committed to this repository. `publish.yml` queries PyPI for the uploaded sdist's immutable URL and SHA-256 digest, renders `packaging/homebrew/ai-jury.rb.template`, attaches the result to the GitHub Release, and pushes it to `berkayturanci/homebrew-ai-jury` when `HOMEBREW_TAP_TOKEN` is set.
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
