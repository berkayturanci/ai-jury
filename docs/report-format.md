# Report format contract

The markdown report produced by `ai_jury.report.render` is the
tool's primary **user-facing output**. Downstream skill and workflow consumers
(for example the Claude Code skill in `skill/ai-jury/`, and anything that
posts the report to GitHub) depend on its structure, so the format is treated as
a contract: it should only change deliberately.

## How the contract is enforced

`tests/test_report_golden.py` contains golden-file (snapshot) tests. For each
scenario it renders a report, normalizes the non-deterministic bits, and
compares the result against a committed snapshot under `tests/golden/`. If the
rendered output drifts from the snapshot, the test fails — so accidental
formatting changes are caught in CI.

Scenarios with committed fixtures:

| Fixture | Scenario |
| --- | --- |
| `tests/golden/full_report.md` | Standard full jury run: 3 agents, two rounds, consensus + structured findings. |
| `tests/golden/single_round_report.md` | `rounds = 1`, so there is no Round 2 (debate) section. |
| `tests/golden/verified_finding_report.md` | `verify = true`, adding the Verification section and per-group verification statuses. |
| `tests/golden/failed_agent_report.md` | A reviewer (and a debater) errored (`ok = False`). |
| `tests/golden/missing_agent_report.md` | A configured agent did not run and the chair synthesis failed. |

The structured/verified cases are produced by running the real pipeline with
`run_jury(..., mock=True)` (the `MockAdapter` emits stable, phase-aware
output). The failed/missing-agent cases hand-construct `AgentResult` lists so the
error paths are exercised deterministically.

## Determinism

The only non-deterministic part of a rendered report is the per-agent duration
token, printed as `{duration_s:.0f}s` (e.g. `3s`). The tests normalize every such
token to `0s` before comparing or writing fixtures (and the mock pipeline already
emits `duration_s = 0.0`), so timing never causes a spurious diff.

## Reviewing an intentional format change

When you intentionally change the report format, the diff shows up directly in
the relevant `tests/golden/*.md` file. Reviewers can therefore read the new
report verbatim in the pull request, rather than mentally reconstructing it from
renderer code.

## Regenerating the fixtures

After an intentional change, regenerate the snapshots by setting `UPDATE_GOLDEN=1`:

```bash
UPDATE_GOLDEN=1 PYTHONPATH=src python3 -m unittest tests.test_report_golden
```

This rewrites the `.md` files under `tests/golden/` with the current output
instead of asserting. Review the resulting fixture diff like any other change,
then commit it alongside the renderer change. Running the suite normally (without
the env var) asserts the output matches the committed fixtures:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

## Per-reviewer ballots (`reviewers`)

The consolidated `findings` and `consensus` views say *what the panel found*.
They do not say *who said what* — and a consumer that wants the panel to **be**
its review (one head-pinned verdict per panelist, attributed to the vendor that
produced it) needs exactly that. Since v1.1 of the JSON schema the
`jury --format json` report therefore carries a top-level `reviewers` array
(issue #663).

`reviewers` is **purely additive**. No existing key was removed, renamed or
reshaped, so a consumer of `findings`/`consensus`/`verdicts`/`verdict`/`metadata`
reads an identical document; `tests/test_formats.py::BackwardCompatibility` pins
that field for field. `schema_version` moved `1.0` → `1.1` to signal the addition.
Markdown and SARIF output are unchanged.

v1.2 ([#700]) adds `scope`, `testing`, `model_source`, `scope_substantive` and
`counts_as_review` to each entry, and is a version bump rather than a silent
addition because it also changes two things a consumer may have keyed on:

- **What `model` means.** It was the configured model id or `""` when the CLI's
  own default was in force, and it is now never empty for a slot that has an
  agent. A consumer testing `model == ""` to detect the default case reads
  `model_source == "cli_default"` instead.
- **What the array contains.** There is now one entry per seat that **ran**, not
  per seat that returned output: an agent that came back with nothing is recorded
  as an abstention naming it rather than dropped, so the report can say *which*
  seat fell silent. A consumer counting the non-`chair` entries as reviews must
  read `counts_as_review` — which is the same rule its own
  `review-verdict-insubstantial` gate applies, so the number ai-jury announces
  and the number the consumer accepts are the same by construction.

Every top-level key, and every other `reviewers` key, is unchanged.

[#700]: https://github.com/berkayturanci/ai-jury/issues/700

One entry per seat that **ran**, in the stable panel order, then the chair:

| Field | Meaning |
| --- | --- |
| `name` | The agent slot's name, as configured. |
| `vendor` | The adapter's vendor (`anthropic`, `openai`, …). |
| `model` | The model id actually requested of that agent's CLI — the configured id, remapped when the vendor encodes reasoning effort in the id — or, where no id was pinned, a statement that the CLI's own default answered and that the CLI does not report which model that was. Never empty for a slot that has an agent. |
| `model_source` | The same fact as one machine token, so a consumer need not parse English: `requested` (an id was pinned and sent), `cli_default` (nothing pinned; the CLI chose and does not say), `unknown` (the answering slot has no spec in this run's config), `none` (no agent in the slot at all — an unchaired run). |
| `verdict` | That panelist's own round-1 stance (see below). |
| `scope` | What that panelist named that it read, or — when it named nothing checkable — an abstention stating why. Derived as in the `keel-reviews` table below; the two renderings are the same text by construction. |
| `scope_substantive` | Does that `scope` name something a reader could go and check (a file, a `path:line`, a backticked symbol, a called identifier, or a `Checked …` clause)? `false` on every abstention, whose scope is deliberately anchorless. |
| `counts_as_review` | Is this entry one of the reviews a consumer receives? `role == "panelist"` **and** `scope_substantive` **and** `verdict != "ABSTAIN"` — `ai_jury.panel.is_review`, the single definition every count in the tool resolves to. Always `false` on the `chair` entry. |
| `testing` | What that panelist said it ran, or a statement that nothing was run. |
| `findings` | Indexes into the report's top-level `findings` array. |
| `round1_ok` | Did the adapter report success? Adapters fail soft, so a slot can carry a review *and* a nonzero exit. |
| `verified_count` | Consensus groups this reviewer contributed to that the verifier upheld. |
| `duration_s` | Wall-clock seconds for that slot's round-1 review. |

The chair's entry is the one carrying `role: "chair"`, is always **last**, and
carries `name` (`"chair"`), `role`, `vendor`, `model`, `model_source`, `scope`,
`scope_substantive`, `counts_as_review` (always `false`), `testing` and `verdict`
— the run's final verdict, which is the panel vote under `--decision vote` and
otherwise the label the chair opened its synthesis with. It also carries `agent`,
`ballot_counted` (is the chairing agent's own ballot one of the counted reviews?)
and `reviews_supplied` (how many of the entries above it are).

### How a ballot's verdict is derived

Exactly as the panel vote derives its ballots — `ai_jury.voting.tally_votes` is
*called*, not reimplemented, so the two renderings cannot drift apart. The stance
comes from the worst-severity finding that reviewer raised which the verifier did
not reject: critical/major → `REQUEST_CHANGES`, minor/nit → `COMMENT`, none →
`APPROVE`. Under `--issue` the vocabulary is the issue one: `NEEDS_INFO`,
`UNCLEAR`, `READY`.

Verdicts are emitted as a single machine token: the markdown report's
`REQUEST CHANGES` is `REQUEST_CHANGES` here, `NEEDS-INFO` is `NEEDS_INFO`.

There is a fourth value, `ABSTAIN`, for a seat that ran but did not review —
nothing at all, a refusal, an adapter that failed, or (since [#700]) a reply that
exited 0 and **named nothing checkable**. **A non-answer is not an approval**
(issue #251): the vote tally drops such a reviewer from the tally entirely, and a
ballot list that has to name every seat names the abstention rather than
inventing a stance for it.

The fourth cause is the same principle one step further out. A reviewer whose
reply names no file, symbol, coverage clause or located finding raised no
findings the tally can weigh, so it would hand it the clear stance
(`APPROVE`/`READY`) — an approval inferred from silence, which is exactly what
#251 refuses. Its `scope` carries the reason, and is deliberately **anchorless**
— no path, no backticked symbol, no "checked …" clause — so a consumer applying
the same substance rule to the record reaches the same conclusion rather than
being talked past it by prose.

### What counts as a review

**A review is a ballot that reviewed.** `ai_jury.panel.is_review` is the one
definition, and every count in the tool resolves to it: the pre-run announcement,
both halves of the `--min-reviews` gate, the markdown report's *reviews for a
downstream consumer* line, the metadata's `panel.reviews_supplied`, the chair
record's `reviews_supplied`, and `--doctor`'s ceiling. A record counts when it is
a `panelist` entry, its `scope_substantive` is `true`, and its `verdict` is not
`ABSTAIN`.

Three things are therefore **in the bundle and not in the count**: the chair's
synthesis record, a seat that returned nothing, and a seat that answered without
naming anything checkable. All three are recorded because a report that drops
them cannot say which agent produced what; none is counted, because the consumer
would refuse it. Counting one is the mismatch [#699] was about, and counting an
abstention is that same mismatch one step out — a bench of three "Looks good to
me, no concerns." replies used to satisfy `--min-reviews 3` and exit `0`.

## The `keel-reviews` bundle

`jury --format keel-reviews` renders the same panel as a JSON **array** of review
records — the payload keel's `keel review --reviews <file>` accepts. One record
per seat that ran, plus the chair as `reviewer: "chair"`:

| Field | Derivation |
| --- | --- |
| `reviewer` | The panelist's name, or `"chair"`. |
| `verdict` | The ballot verdict above (a single token). |
| `scope` | One paragraph naming what that panelist read, from four sources, most authoritative first: its own `Checked:` line (which the review prompt asks every reviewer to open with); the distinct files it attached to its structured findings (capped at 8, with a `(+N more)` tail); up to three "checked / examined / inspected / reviewed" clauses from its prose; and — **under `--issue` only** — failing a file, the claims it raised. A reply that yields **none** of those has no scope: the record becomes an `ABSTAIN` whose scope states why. The chair's record additionally names the agent that chaired, says whether that agent's own ballot is one of the counted reviews, states how many reviews the bundle carries and that this record is not one of them; the ballot cast by the chairing agent says so on its own scope too. |
| `findings` | That panelist's own findings as `{severity, path, line, message}` — ai-jury's `file` → `path`, `claim` → `message`. The chair carries the consensus-group representatives the verifier did **not** reject. |
| `testing` | The panelist's own `Tested:` line, else the first verification clause in its prose — both lifted verbatim (flattened and capped), because a testing claim carried downstream as evidence must be the reviewer's words and not a paraphrase. With neither, it says plainly that nothing was run. The chair's comes from the verification round. |
| `vendor` | The adapter's vendor. |
| `model` | As in the `reviewers` table above: the id actually requested, or a statement that the CLI's default answered and the CLI does not report which. |
| `model_source` | As in the `reviewers` table above. It rides along here too because `model` changed meaning in the same release and this is the shape a machine consumer actually parses — without it, telling a requested id from a CLI default meant reading English ([#700]). |
| `counts_as_review` | As in the `reviewers` table above. The bundle carries a record for every seat, abstentions included, so a consumer counting the array needs the discriminator. |

**A claim is a scope only under `--issue`.** There, a finding carries an empty
`file` by construction — the panel is reading an issue's prose rather than a diff
— so the claims a reviewer raised are the only thing it can name, and that is the
one legitimate exception to "a scope names a file, line or symbol, or carries a
`Checked …` clause". In code-review mode a claim raised against no file names no
place in the code: the fallback used to backtick it into an anchor, so a ballot
raising one `major` finding with `file: ""` produced `REQUEST_CHANGES` while
naming nowhere. That ballot abstains now, and the abstention reason says which of
the two happened — "said nothing" reads differently from "named nowhere".

**Where the scope comes from: both — asked for, and derived.** The review prompt
asks every reviewer to open with a `Checked:` / `Tested:` pair and says that a
ballot naming nothing checkable is recorded as an abstention, so the reviewer's
own statement of its coverage is the first source and beats anything inferred.
Derivation stays as the floor, because a prompt is a request and not a guarantee:
a reviewer that ignores the instruction but attaches files to its findings still
produces a scope. Only a reply that satisfies neither abstains. ([#700])

`scope` and `testing` are the only fields lifted from free text, and reviewer
output is attacker-influenced. Both are therefore flattened to a single line,
capped per clause and per count, and taken only from **prose**: a review's fenced
`json` block is skipped, because it is already parsed into `findings` and would
otherwise swamp the summary with its own serialized claim text. Marker matching
is word-anchored, which is load-bearing rather than tidy — the house phrasing
"un**checked** return value" is a finding, not a claim to have checked something.

**The chairing agent sits on the panel.** The chair is drawn from the *usable*
agents and round 1 runs every usable agent, so that agent reviews like anyone
else: an *n*-agent bench yields *n* ballots — one of them the chairing agent's —
and one further record, the chair's synthesis.

**A ballot is not automatically a review.** The synthesis record is the panel's
consensus, not an *n+1*-th review, and neither is an abstaining ballot: a
consumer splits the report's `reviewers` array on `role`, keeps the `chair` entry
aside as the consensus record, and then refuses any of the rest whose scope names
nothing. The chairing agent's ballot carries `role: "panelist"` like any other and
counts when it reviewed. So an *n*-agent bench supplies **at most** *n* reviews —
which is what the pre-run line announces — and the number it actually supplied is
what the report, the metadata and the gate all state afterwards ([#699], [#700]).

The bundle says which agent chaired and what became of its ballot, because a
record that does not say is indistinguishable from a synthesis-only entry, and a
reader who guesses hands on the wrong number. A chair that ran and returned
nothing has a ballot in the bundle that is not a review; that record says so,
rather than looking identical to one whose chair reviewed.

Use `[jury.ci] min_reviews` / `--min-reviews N` to require a review count: the
ceiling is checked before the panel runs, and the count it actually supplied is
checked after — no pre-flight can predict an agent that runs and says nothing.

[#699]: https://github.com/berkayturanci/ai-jury/issues/699

Neither format ever carries diff text, prompt text or secrets.
