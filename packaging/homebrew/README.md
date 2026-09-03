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
