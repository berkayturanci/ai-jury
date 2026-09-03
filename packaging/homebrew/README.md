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

The change the tap needs ships here rather than as a request that someone
remember it:

```console
$ git -C ../homebrew-ai-jury apply /path/to/ai-jury/packaging/homebrew/tap-sync-formula.patch
```

It repoints the sync at `releases/latest/download/ai-jury.rb`, which needs no
credential at either end and is never stale. (Setting `HOMEBREW_TAP_TOKEN` in
this repository is complementary, not an alternative: it makes the push path
carry each release to the tap within seconds instead of within the tap's
schedule.)

### The window where the asset does not exist yet

The ordering is *repoint → merge → next release*, and it is the merge that makes
the next release attach `ai-jury.rb`. So between the repoint and the next tag
there is **no asset to fetch**: releases up to and including v1.15.1 carry only
the wheel, sdist, SBOM and `SHA256SUMS`, and
`https://github.com/berkayturanci/ai-jury/releases/latest/download/ai-jury.rb`
returns 404.

The patch treats that as an ordinary state rather than a fault. The fetch step
records `found=false`, prints `::notice::formula asset not published yet`, and
the verify and commit steps are skipped. The tap keeps serving the formula it
already has, its cron stays green, and the first release cut after this merges
fills the gap. Failing on the 404 would have replaced one red cron with another.

### What is actually enforced, and where

Be precise about this, because the two halves run in different places:

| Check | Where it runs | Blocking on `main`? |
|---|---|---|
| `TAP_REPOINTED` exists and names a tap commit as `tap-sync-formula: <40-hex>` | offline, in the default suite | **yes** — it is in `Tests` |
| the tap's live `sync-formula.yml` no longer names the retired path | `AI_JURY_CHECK_EXTERNAL=1` | **no** — it runs in `Action pins match upstream`, which is not a required check |

So the offline half is a **claim in a reviewable form**, not proof: nothing
offline can read another repository. Requiring the tap commit sha rather than a
non-empty file is what makes it checkable — a reviewer can open the commit, and a
wrong sha is a specific, falsifiable statement instead of a shrug. The online
half is the real check, and making `Action pins match upstream` a required status
check on `main` would turn the claim into an enforced fact. That is the
operator's call, not something this pull request does.

Record the repointing like this:

```console
$ echo "tap-sync-formula: <40-char tap commit sha>" > packaging/homebrew/TAP_REPOINTED
```

Both the marker and the gate are single-use. They can be deleted once the tap has
been repointed for a release or two and nobody is tempted to restore the
committed formula.
