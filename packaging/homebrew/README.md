# The Homebrew formula lives in the tap, not here

`brew install berkayturanci/ai-jury/ai-jury` reads
[`berkayturanci/homebrew-ai-jury`](https://github.com/berkayturanci/homebrew-ai-jury).
That repository holds the only formula anyone installs. This repository holds the
*template* it is rendered from, and nothing else.

The split exists because a formula names two things that cannot both be correct
before a release exists:

```ruby
url    "https://files.pythonhosted.org/packages/…/ai_jury-1.15.1.tar.gz"
sha256 "9dfe787e…"
```

PyPI's paths are content-addressed, so neither the url nor the digest is knowable
until the tag is pushed and the sdist is uploaded. A copy of that pair committed
here is therefore either stale or unverifiable, every single release — which is
what produced seven consecutive fix-forward commits
([#666](https://github.com/berkayturanci/ai-jury/issues/666)).

So the pair is never committed. `.github/workflows/publish.yml` renders this
template *after* the upload, from the url and digest PyPI reports, re-downloads
the artifact to confirm the digest is the artifact's, and publishes the result to
two places that are not this repository's `main`:

1. the GitHub Release, as `ai-jury.rb` — permanently at
   `https://github.com/berkayturanci/ai-jury/releases/latest/download/ai-jury.rb`;
2. the tap itself, when `HOMEBREW_TAP_TOKEN` is configured.

See [`docs/homebrew-release-chain.md`](../../docs/homebrew-release-chain.md) for
the whole chain and for what each guard catches.

## Before `Formula/ai-jury.rb` could be deleted: repoint the tap

The tap's `.github/workflows/sync-formula.yml` pulled
`repos/berkayturanci/ai-jury/contents/Formula/ai-jury.rb` every thirty minutes.
With that file gone the curl 404s and the tap's sync job fails on a schedule
forever — the same shape as the hourly failures that produced this whole
document.

The change the tap needs is one line, and it ships here rather than as a request
that someone remember it:

```console
$ git -C ../homebrew-ai-jury apply /path/to/ai-jury/packaging/homebrew/tap-sync-formula.patch
```

It repoints the sync at `releases/latest/download/ai-jury.rb`, which needs no
credential at either end and is never stale. (Setting `HOMEBREW_TAP_TOKEN` in
this repository is complementary, not an alternative: it makes the push path
carry each release to the tap within seconds instead of within the tap's
schedule.)

**This repository refuses the deletion until that is recorded.**
`tests/test_homebrew_formula.py` fails while `Formula/ai-jury.rb` is absent and
`packaging/homebrew/TAP_REPOINTED` does not exist. Once the tap is repointed:

```console
$ echo "<tap commit sha> — sync-formula.yml reads releases/latest/download/ai-jury.rb" \
    > packaging/homebrew/TAP_REPOINTED
```

With `AI_JURY_CHECK_EXTERNAL=1` the same test file also reads the tap's live
workflow and fails if it still names the retired path, so the marker records a
change that is independently checked rather than merely asserted.

Both the marker and the gate are single-use. They can be deleted once the tap
has been repointed for a release or two and nobody is tempted to restore the
committed formula.
