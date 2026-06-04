<!-- A single real moment from the live four-vendor run (example-live-review.md):
     a reviewer that refused to review, and how the jury handled it. Kept as its
     own page because it's the clearest argument for a panel over one reviewer. -->

# Case study: when a reviewer abdicates

A single, real moment from the [live four-vendor review](example-live-review.md)
— the jury reviewing its own repository on **v1.1.0 (2026-06-04)**. It gets its
own page because it shows *why a panel beats a single reviewer* better than any
feature list.

## What happened

On one of the eight chunks, an anonymized reviewer ("Reviewer B") didn't review
the code. It returned only:

> I can't assist with that request.

No findings, no analysis — a model **refusal**. It happens: a safety decline, a
misread prompt, an off day. The interesting part is what the jury did next.

## Why that's a trap

The naive way to combine reviewers is *"this one raised no findings → it's happy
→ count it as APPROVE."* Under that rule a reviewer that **refused** — or errored,
or timed out into an empty reply — silently becomes a green light. And it gets
worse with scale: the more reviewers you add, the more chances for one to quietly
turn into a free approval. "No output" and "looks good" are not the same thing,
but a sloppy tally treats them identically.

## What the jury did

This run used **chair** decision. The chair, synthesizing the chunk, recognized
the reply for what it was and excluded it:

> Reviewer B returned only "I can't assist with that request" — an abdication,
> not a review. It was correctly excluded from consensus rather than counted as a
> clean pass.

The remaining three reviewers carried the verdict for that chunk. A
**single-reviewer** tool has no such fallback: if its one reviewer refuses you
get nothing — or, with the naive tally, a false "all clear." A panel **degrades
gracefully**.

## The honest nuance: chair vs. vote

This graceful handling came from the **chair's judgment** in chair-decision mode.
In `--decision vote` mode the tally currently maps a reviewer with *no findings*
to the "clear" stance (`APPROVE` / `READY`) — so a refusal or empty review would
be counted as an approve vote, indistinguishable from a genuine clean review.
That gap is real and tracked as
[#251](https://github.com/berkayturanci/ai-jury/issues/251) (detect abstentions
and drop them from the tally). The property held *here* because of the chair;
we're hardening the vote path to match. Surfacing exactly this kind of gap is the
point of dogfooding.

## Takeaways

- **A non-answer is not an approval.** Treating "no findings" as "looks good" is
  the subtle failure mode of multi-reviewer setups.
- **Diversity plus a synthesis/verification layer is the moat.** It's what lets
  the jury survive a reviewer that misbehaves — the core argument for a panel over
  a single model.
- **Dogfooding finds the gaps.** The same run that showed the chair handling this
  cleanly also surfaced the vote-mode gap (#251).

See the full run in [example-live-review.md](example-live-review.md).
