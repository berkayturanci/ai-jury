# The Homebrew release chain

Why this document exists: between 2026-08-25 and 2026-08-27 this chain failed
four separate ways, and each failure was diagnosed from scratch because nothing
wrote down how the pieces fit. This is that write-up. It is not a runbook —
`docs/release-checklist.md` is — it is the explanation a runbook assumes you
already have.

The sibling repository has the same chain with two deliberate differences, noted
where they matter: [`berkayturanci/keel` → `docs/keel/homebrew-release-chain.md`](https://github.com/berkayturanci/keel/blob/main/docs/keel/homebrew-release-chain.md).

## The contradiction, and how it was removed

A Homebrew formula names two things:

```ruby
url    "https://files.pythonhosted.org/packages/…/ai_jury-1.15.1.tar.gz"
sha256 "9dfe787e174510f61e1fe4736b3d358e70c2d7fb522123009043e9067b649ce8"
```

**They cannot both be correct at the same moment.** PyPI's paths are
content-addressed, so the url is not derivable from the version; the digest
belongs to an artifact that does not exist until the tag is pushed and the
publish workflow uploads it. Any copy of that pair committed to this repository
is therefore stale or unverifiable on every release, by construction.

For seven releases the answer was to keep the copy and repair it afterwards,
which required a **second write to `main` after the tag**. That write was
attempted four ways in three days and every one of them failed
([#666](https://github.com/berkayturanci/ai-jury/issues/666) lists the
fix-forward commits).

[#645](https://github.com/berkayturanci/ai-jury/issues/645) removed the
requirement instead of the latest symptom, and that is the design now:

> **Nothing in this repository names an sdist url or a digest.**

`Formula/ai-jury.rb` is gone. What is committed is
`packaging/homebrew/ai-jury.rb.template`, which names `@URL@`, `@SHA256@` and
`@VERSION@` and can never be stale. `publish.yml` renders it *after* the upload,
from the url and digest PyPI reports, re-downloads the artifact to confirm the
digest is the artifact's, and publishes the result to two places that are not
`main`:

1. the **GitHub Release**, as `ai-jury.rb` — permanently reachable at
   `https://github.com/berkayturanci/ai-jury/releases/latest/download/ai-jury.rb`
   and covered by that release's `SHA256SUMS`;
2. the **tap**, `berkayturanci/homebrew-ai-jury`, when `HOMEBREW_TAP_TOKEN` is
   configured.

**A release is now exactly one write to `main`: the release pull request.**

## The chain, end to end

| # | Step | Who | Where |
|---|---|---|---|
| 1 | Bump the version across every surface, cut the changelog | human | release PR |
| 2 | Merge, then `git tag vX.Y.Z && git push origin vX.Y.Z` | human | — |
| 3 | Build, publish to PyPI, attest | automatic | `publish.yml` |
| 4 | Render the formula from PyPI's url + digest, verify it against the artifact | automatic | `publish.yml` |
| 5 | Attach the formula to the GitHub Release; push it to the tap | automatic | `publish.yml` |
| 6 | Install the published release and run it; check the tap's digest | automatic | `publish.yml` (`verify`) |
| 7 | Verify what the tap now serves | automatic | tap's `verify-formula.yml` |

Step 1 is the only write to `main`. Nothing after the tag commits anything here.

**Difference from the sibling:** keel's tap pulls a formula from keel's `main`.
This repo's tap has no `main` copy to pull, so the formula travels as a release
asset (credential-free at both ends) with a direct push as the fast path.

### The one thing the tap has to be told, and the gate that records it

`berkayturanci/homebrew-ai-jury`'s `sync-formula.yml` pulled
`berkayturanci/ai-jury` → `main` → `Formula/ai-jury.rb`, which no longer exists.
With the file gone that curl 404s, `curl -fsSL` exits 22, and the tap's sync job
fails every thirty minutes forever — the shape of #630, arriving from a new
direction.

So the source moves to the release asset:

```
https://github.com/berkayturanci/ai-jury/releases/latest/download/ai-jury.rb
```

`packaging/homebrew/tap-sync-formula.patch` is that change, ready to `git apply`.
It also makes the fetch tolerate an asset that does not exist yet, which matters
for exactly one window: the ordering is *repoint → merge → next release*, and it
is the merge that makes the next release attach the formula. Until then the asset
404s, so the patched fetch records `found=false`, prints a `::notice::`, and skips
the verify and commit steps. The tap keeps serving what it has and its cron stays
green. A patch that failed on that 404 would have traded one red cron for another.

**What the gate actually enforces** is worth stating exactly, because the two
halves run in different places:

| Check | Where | Blocking on `main` today |
|---|---|---|
| `packaging/homebrew/TAP_REPOINTED` exists and names the tap commit as `tap-sync-formula: <40-hex>` | offline, default suite | **yes** |
| the tap's live `sync-formula.yml` no longer names the retired path | `AI_JURY_CHECK_EXTERNAL=1` | **no** — it runs in `Action pins match upstream`, which is not a required check |

The offline half is therefore a claim in a reviewable form, not proof: nothing
offline can read another repository. Requiring the tap's commit sha rather than a
non-empty file is what makes the claim checkable — a reviewer can open it, and a
wrong sha is falsifiable rather than a shrug. The online half is the real check;
making `Action pins match upstream` a required status check would turn the claim
into an enforced fact, and that is the operator's decision.

This is a gate a person satisfies deliberately, and it is satisfiable at any
moment — unlike the digest gate that used to block every release pull request,
where no value of the file could pass. It and the marker are single-use.

Setting `HOMEBREW_TAP_TOKEN` in this repository is complementary: it makes the
push path carry every release to the tap within seconds instead of within the
tap's schedule. Until it is set, `publish.yml` skips the push (guarded on a step
output, since `if:` cannot read `secrets.*`), emits a `::warning::` naming both
fixes, and the `verify` job checks the *release asset* rather than polling a tap
nothing pushed.

## Every guard, and what it catches

| Guard | Where | Kind | Catches |
|---|---|---|---|
| version lockstep | `tests/test_release_metadata.py` | offline | a plugin manifest left behind (#284) |
| user-facing version strings | `tests/test_release_metadata.py` | offline | the website telling visitors the wrong release |
| the template names no digest | `tests/test_homebrew_formula.py` | offline | a committed url/digest pair coming back |
| the url belongs to this project and names the declared version | `tests/test_homebrew_formula.py` | offline rule | a url that resolves but to the wrong file (#562) |
| the tap's url downloads and hashes to its digest | `tests/test_homebrew_formula.py` | online | a tap `brew` would refuse (#633) |
| the release path writes nothing to `main` | `tests/test_publish_release_chain.py` | offline | the second write being reintroduced |
| the tap push is gated on its token | `tests/test_publish_release_chain.py` | offline | a 401 failing a release that already succeeded |
| the tap repointing is recorded (offline) and true (online) | `tests/test_homebrew_formula.py` | offline claim + online check | the tap's sync 404ing every 30 minutes |
| the published release installs and runs | `publish.yml` `verify` | online | a green release nobody can install (#666) |
| what the tap actually serves | tap's `tests/` | online | anything the sync produced that cannot install |

The online ones in the test suite are opt-in via `AI_JURY_CHECK_EXTERNAL=1` so
the default suite stays hermetic. **They are wired into CI** — that is not
automatic and it was once the whole bug: the tests existed and ran nowhere.

They are also, deliberately, never compared against this branch's version. The
tap is written after the tag, so between a release and the tap's next sync it is
legitimately behind. A check that failed on that would block the only sequence
able to satisfy it — which is precisely what made every release pull request
unmergeable before #645, and why the suite used to need a rule for reading a 404
two different ways. That rule is gone with the copy it protected.

### Why the tap has its own tests

The tap is the copy `brew` downloads. Its formula is written without a pull
request, so a source repository's guards never look at the result. Until
2026-08-27 the tap had three files and no test of any kind.

The tap's suite downloads **every** url in the formula and re-hashes it, not just
the top-level one. A formula can vendor dependencies as `resource` blocks, each
with its own url and digest, and a wrong one fails the install exactly as hard.
keel#787 is what that looks like from a user's side: every command dying on an
import before printing anything.

## What has already gone wrong

Read this before adding a mechanism — four of these were fixed by adding one, and
the fifth removed all four.

| Failure | What actually happened |
|---|---|
| [#633](https://github.com/berkayturanci/ai-jury/issues/633) | The formula fix was pushed with `git push origin HEAD:main \|\| true`. Branch protection had been added the same day (#620), so the push was refused and `\|\| true` reported success. 1.15.0 shipped a stale digest; the tap refused every sync for a day. |
| [#638](https://github.com/berkayturanci/ai-jury/issues/638) | The follow-up made that failure loud. Loud is not fixed: the repair was still a human editing a file after reading the log of a green release. |
| [#641](https://github.com/berkayturanci/ai-jury/issues/641) | The auto-PR's first live run could not push. `HEAD:${branch}` is refused from the detached HEAD a tag build checks out — git will not guess the remote namespace when a refspec's source is a bare commit. Needs `HEAD:refs/heads/${branch}`. |
| [#643](https://github.com/berkayturanci/ai-jury/issues/643) | Opening the pull request was not enough; the tap only recovers when it merges. Auto-merge was armed — and then needed a repository setting that was off, and an evidence gate with no bot exemption. |
| the release gate | The online formula check turned an unpublished version's 404 into a failure, which made *every release pull request* unmergeable — a gate blocking the only sequence able to satisfy it. |
| the site | `website/index.html` sat at v1.14.4 for two releases while everything else was current. It even looked automatic — the element carries `id="site-version"` and links to `/releases/latest` — but the text was hard-coded. |

Three patterns run through all of these:

1. **A message nobody is required to act on is not a safeguard.** A `::notice::`,
   an `::error::`, a checklist line — each was tried, each was missed.
2. **A guard that nothing runs is not a guard.** The digest tests existed for a
   week behind an environment variable that was set nowhere.
3. **Five mechanisms for one unsatisfiable requirement is four too many.** #633,
   #638, #641 and #643 each made the repair of a file that could not be right
   more reliable. Deleting the file ended all four.

## Diagnosing the next one

| Symptom | Look here first |
|---|---|
| The release is green but `pip install ai-jury==X.Y.Z` fails | The `verify` job, and the `release-broken: vX.Y.Z` issue it opens. PyPI does not allow re-uploading a version — yank and ship the next patch. |
| The release log says the tap was not updated | `HOMEBREW_TAP_TOKEN` is unset, so the push was skipped by design. The formula is on the release; the tap's own sync delivers it. |
| `brew upgrade` sees nothing new | The tap's sync source. It must be the release asset, not `main`. |
| The tap fails on a schedule, hours after a green release | The formula's digest. Compare it against the sdist PyPI actually serves. |
| A pull request shows **no runs at all** for its head | The head commit's message. A CI-skip marker in it — even quoted in prose — suppresses every workflow. |
| Something wants to add `Formula/ai-jury.rb` back | Read "The contradiction" above. `tests/test_homebrew_formula.py` refuses it. |
