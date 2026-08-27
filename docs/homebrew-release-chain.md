# The Homebrew release chain

Why this document exists: between 2026-08-25 and 2026-08-27 this chain failed
four separate ways, and each failure was diagnosed from scratch because nothing
wrote down how the pieces fit. This is that write-up. It is not a runbook —
`docs/release-checklist.md` is — it is the explanation a runbook assumes you
already have.

The sibling repository has the same chain with two deliberate differences, noted
where they matter: [`berkayturanci/keel` → `docs/keel/homebrew-release-chain.md`](https://github.com/berkayturanci/keel/blob/main/docs/keel/homebrew-release-chain.md).

## The contradiction everything else is arranged around

`Formula/ai-jury.rb` names two things:

```ruby
url "https://files.pythonhosted.org/packages/…/ai_jury-1.15.1.tar.gz"
sha256 "9dfe787e174510f61e1fe4736b3d358e70c2d7fb522123009043e9067b649ce8"
```

**They cannot both be correct at the same moment.** The release bump moves the
url to the version being released; that sdist does not exist until the tag is
pushed and the publish workflow uploads it. So from the release pull request
until the tag, the formula necessarily declares a digest that belongs to the
*previous* release.

Every mechanism below exists because of that gap. None of them removes it —
[#645](https://github.com/berkayturanci/ai-jury/issues/645) proposes doing that.

## The chain, end to end

| # | Step | Who | Where |
|---|---|---|---|
| 1 | Bump the version across every surface, cut the changelog | human | release PR |
| 2 | Merge, then `git tag vX.Y.Z && git push origin vX.Y.Z` | human | — |
| 3 | Build, publish to PyPI, create the GitHub Release, attest | automatic | `publish.yml` |
| 4 | Read the sdist url + digest back from PyPI, rewrite the formula | automatic | `publish.yml` |
| 5 | Push `chore/formula-<version>`, open a PR, arm auto-merge | automatic | `publish.yml` |
| 6 | Push the formula to the tap | automatic | `publish.yml` |
| 7 | Verify what the tap now serves | automatic | tap's `verify-formula.yml` |

Steps 4–5 are the repair for the contradiction: the digest becomes knowable
exactly at step 3, so the release fixes its own formula immediately afterwards
instead of leaving a note.

**Difference from the sibling:** this repo *pushes* to its tap (step 6,
`gh api -X PUT`). keel's tap *pulls* on a schedule instead, because writing
another repository needs a PAT to create, store and rotate. Both work; the pull
design has one less credential, the push design has no latency.

## Every guard, and what it catches

| Guard | Where | Kind | Catches |
|---|---|---|---|
| version lockstep | `tests/test_release_metadata.py` | offline | a plugin manifest left behind (#284) |
| user-facing version strings | `tests/test_release_metadata.py` | offline | the website telling visitors the wrong release |
| formula names the current version | `tests/test_homebrew_formula.py` | offline | a formula left behind by a release entirely |
| url is *this version's* sdist | `tests/test_homebrew_formula.py` | online | a url that resolves but to the wrong file |
| url downloads and hashes to its digest | `tests/test_homebrew_formula.py` | online | the stale digest (#633) |
| the publish step's shape | `tests/test_publish_formula_followup.py` | offline | the repair mechanism being silently undone |
| what the tap actually serves | tap's `tests/` | online | anything the sync produced that cannot install |

The online ones are opt-in via `AI_JURY_CHECK_EXTERNAL=1` so the default suite
stays hermetic. **They are wired into CI** — that is not automatic and it was
once the whole bug: the tests existed and ran nowhere.

### Why the tap has its own tests

The tap is the copy `brew` downloads. Its formula is written without a pull
request, so the source repo's guards — which run on pull requests, against a file
that is *copied* there — never look at the result. Until 2026-08-27 the tap had
three files and no test of any kind.

The tap's suite downloads **every** url in the formula and re-hashes it, not just
the top-level one. A formula can vendor dependencies as `resource` blocks, each
with its own url and digest, and a wrong one fails the install exactly as hard.
keel#787 is what that looks like from a user's side: every command dying on an
import before printing anything.

## What has already gone wrong

Read this before adding a mechanism — three of these were fixed by adding one.

| Failure | What actually happened |
|---|---|
| [#633](https://github.com/berkayturanci/ai-jury/issues/633) | The formula fix was pushed with `git push origin HEAD:main \|\| true`. Branch protection had been added the same day (#620), so the push was refused and `\|\| true` reported success. 1.15.0 shipped a stale digest; the tap refused every sync for a day. |
| [#638](https://github.com/berkayturanci/ai-jury/issues/638) | The follow-up made that failure loud. Loud is not fixed: the repair was still a human editing a file after reading the log of a green release. |
| [#641](https://github.com/berkayturanci/ai-jury/issues/641) | The auto-PR's first live run could not push. `HEAD:${branch}` is refused from the detached HEAD a tag build checks out — git will not guess the remote namespace when a refspec's source is a bare commit. Needs `HEAD:refs/heads/${branch}`. |
| the release gate | The online formula check turned an unpublished version's 404 into a failure, which made *every release pull request* unmergeable — a gate blocking the only sequence able to satisfy it. Now a 404 is read against PyPI: unpublished ⇒ skip, published ⇒ fail. |
| the site | `website/index.html` sat at v1.14.4 for two releases while everything else was current. It even looked automatic — the element carries `id="site-version"` and links to `/releases/latest` — but the text was hard-coded. |

Two patterns run through all of these:

1. **A message nobody is required to act on is not a safeguard.** A `::notice::`,
   an `::error::`, a checklist line — each was tried, each was missed.
2. **A guard that nothing runs is not a guard.** The digest tests existed for a
   week behind an environment variable that was set nowhere.

## Still manual, and why

Two repository settings stand between step 5 and a hands-off chain. Neither is a
code defect; both are decisions.

- **Actions may not create pull requests.** *Settings → Actions → General →
  "Allow GitHub Actions to create and approve pull requests"*. With it off,
  step 5 falls through to its notice path and someone opens the PR by hand.
- **The evidence gate has no bot exemption.** Measured on keel#989, a one-line
  machine-generated digest change: it required three reviewer verdicts and an
  `agent:<vendor>` label. The PR-description lint *does* exempt bots by account
  type; the evidence gate does not. Exempting a review gate is a larger decision
  than exempting a description lint, which is why it has not simply been done.

Interim cost: one hand-opened, hand-merged pull request per release, with the
digest already computed, pushed and independently verified. Materially better
than the state before #638, where the value existed only as a line in a log.

## Diagnosing the next one

| Symptom | Look here first |
|---|---|
| The tap fails on a schedule, hours after a green release | The formula's digest. Compare it against the sdist PyPI actually serves. |
| A pull request shows **no runs at all** for its head | The head commit's message. A CI-skip marker in it — even quoted in prose — suppresses every workflow. |
| The release PR fails on a 404 for its own sdist | Expected before the tag exists. If it *fails* rather than skips, the 404 rule regressed. |
| `publish.yml` pushed nothing after the tag | The refspec. A tag build is on a detached HEAD. |
| `brew upgrade` sees nothing new | The tap's own CI. It runs after every sync and asserts the tap serves the current release. |
