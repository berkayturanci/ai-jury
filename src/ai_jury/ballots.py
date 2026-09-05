"""Per-reviewer ballots: each panelist's own stance, with vendor provenance (issue #663).

The consolidated ``findings``/``consensus`` views answer *what the panel found*.
They do not answer *who said what*, which is the question a downstream gate asks
when it wants the panel to **be** its review: one head-pinned verdict per
panelist, attributed to the vendor and model that produced it. Until now that
existed only as prose in the markdown report's vote block.

Everything here is pure and deterministic — a function of the outcome, the config
and the (optional) vote. No I/O, no wall-clock, no randomness. Two renderings are
built on it:

* :func:`reviewer_ballots` — the ``reviewers`` array of the JSON report.
* :func:`keel_reviews` — the ``--format keel-reviews`` bundle, shaped for a
  consumer that accepts a JSON array of ``{reviewer, verdict, scope, findings,
  testing, vendor, model}`` records (keel's ``keel review --reviews``).

Only legitimate, already-reported fields are emitted: severities, locations,
claims, vendor/model provenance and durations. Never raw diff text, prompt text
or secrets. Free-text lifted out of an agent's reply (``scope``, ``testing``) is
attacker-influenced, so it is flattened to a single line and length-capped before
it leaves this module.

**A ballot has to name something** (issue #700). Every ballot of a real run read
``scope: "Reviewed the supplied diff; named no specific file."``, ``testing:
"not stated"``, ``model: ""`` — and on a tier whose review *is* the panel, those
ballots were the whole review. The consumer refuses a hand-posted verdict shaped
like that (:data:`_SCOPE_ANCHORS` mirrors the rule: a path, a ``path:line``, a
backticked symbol, a called identifier, or a "checked …" clause), precisely
because a verdict naming nothing cannot be told apart from one never performed.
So the placeholder is gone in three directions:

* the review prompt asks the reviewer for its own ``Checked:`` / ``Tested:``
  lines, rather than this module inferring coverage from whatever prose landed;
* a scope that still names nothing is not written — the ballot is reported as an
  :data:`ABSTAIN` whose scope states *why*, because an agent that returned
  nothing useful must not be counted as one that reviewed;
* ``model`` is never blank: it is the model id actually requested of the agent
  that answered, or a statement that the CLI's default was used and the CLI does
  not report which model that was. A ballot naming its vendor but not its model
  cannot answer whether the same model sat twice, which is the whole product of
  a cross-vendor panel.

**And a ballot that named nothing is not a review** (#700, round 2). Recording the
abstention was only half of it: the abstaining ballot still counted toward the
number of reviews the run announced and toward ``--min-reviews``, so a panel of
three "Looks good to me, no concerns." replies satisfied a gate that exists to
refuse exactly that. :func:`ai_jury.panel.is_review` is now the single definition
— a ``panelist`` record with a substantive scope and a voting verdict — every
ballot carries the two fields it reads (``scope_substantive`` and ``verdict``)
plus the answer itself (``counts_as_review``), and three further consequences
follow from it:

* every seat that ran gets a record, including one that returned nothing at all.
  Dropping it left the bundle unable to say *which* agent fell silent; the record
  is an abstention naming the seat and the reason, and the count excludes it.
* a finding attached to no file is a scope only under ``--issue``, where a
  finding carries no file by construction. In code-review mode a claim is not a
  place in the code, and a ballot with nothing but claims abstains.
* ``model_source`` rides along in the ``keel-reviews`` projection too, so a
  machine consumer of that shape can tell a requested id from a statement about
  the CLI's default without parsing English.
"""

from __future__ import annotations

import re
from typing import Any

from .findings import flatten_inline
from .panel import (
    ABSTAIN,
    CHAIR_ROLE,
    PANELIST_ROLE,
    ballot_seats,
    bundle_records,
    is_review,
    responded,
    review_count,
)
from .voting import is_abstention, tally_votes

# ``ABSTAIN`` is the stance recorded for a panelist that did not actually review
# — nothing at all, an empty reply, a refusal, an adapter that failed, or a reply
# naming nothing checkable. Counting such a seat as the "clear" stance
# (APPROVE/READY) is precisely the bug :mod:`ai_jury.voting` refuses to have
# (issue #251): a non-answer is not an approval. The vote tally drops those
# reviewers entirely; a ballot list has to name every seat that ran, so it names
# the abstention instead of inventing a stance for it. It is *defined* in
# :mod:`ai_jury.panel` and re-exported here for the module's own callers, because
# :func:`ai_jury.panel.is_review` — the one definition of what counts as a review
# — has to test for it, and two copies of the token are two places for the count
# and the record to disagree.

#: ``reviewer`` / ``name`` used for the chair's entry.
CHAIR_NAME = "chair"

#: The ``mode`` that selects the issue-review vocabulary (``--issue``), and with
#: it the single exception to "a scope must name a file": there a finding carries
#: no file by construction, so the claims a reviewer raised are what it named.
ISSUE_MODE = "issue"

#: What ``testing`` says when the reviewer named no verification at all. It says
#: it plainly rather than shrugging: "not stated" reads like a field nobody
#: filled in, and a reader cannot tell that apart from a reviewer that ran
#: nothing and said so (#700). Both are "no verification was run"; only one of
#: them used to be legible.
NOT_STATED = "Nothing run: this reviewer named no command, test run or reproduction."

#: ``model`` for an agent whose CLI was invoked with no model id pinned. Filled
#: in from the agent's command so the field states the situation instead of
#: being blank — "which model answered" has an honest answer here, and it is
#: "the CLI's default, which the CLI does not report".
_CLI_DEFAULT_MODEL = "{command} default (the CLI does not report which model answered)"

#: Where a ballot's ``model`` came from, as one machine token, so a consumer can
#: tell a real model id from a statement about one without parsing prose.
MODEL_REQUESTED = "requested"  # an id was pinned (or mapped from `effort`) and sent
MODEL_CLI_DEFAULT = "cli_default"  # nothing pinned; the CLI chose, and does not say
MODEL_UNKNOWN = "unknown"  # the answering slot has no spec in this config
MODEL_NONE = "none"  # there is no agent in this slot at all (an unchaired run)

#: Caps on free text lifted out of agent output. A reviewer's reply is
#: attacker-influenced, so a forged 100 kB "scope" must not become the bulk of a
#: bundle posted downstream.
_CLAUSE_MAX = 240
_SCOPE_CLAUSES = 3
_FILES_LISTED = 8

# Sentence boundary: end punctuation followed by whitespace. Lines are split
# first, so a bullet list yields one clause per bullet.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

#: Clauses that describe what a reviewer looked at (folded into ``scope``).
#: Anchored on word boundaries, which is load-bearing rather than tidy: the most
#: common finding wording in this tool's own fixtures is "**un**checked return
#: value", and a bare substring test folds that claim into the coverage summary
#: as though the reviewer had said it checked something.
_COVERAGE_RE = re.compile(r"\b(checked|examined|inspected|reviewed|covered)\b", re.IGNORECASE)

#: Clauses that describe what a reviewer did to verify a claim (``testing``).
_TESTING_RE = re.compile(
    r"\b(ran\s+(?:the\s+)?tests?|test\s+suite|unit\s+tests?|i\s+ran|reproduced|"
    r"verified|verification|tested)\b",
    re.IGNORECASE,
)

#: The two lines :mod:`ai_jury.prompts` asks every reviewer to open with. Matched
#: after the line has been flattened and stripped of list/heading/emphasis
#: markers, so ``- **Checked:** src/a.py`` and ``Checked: src/a.py`` both land.
_STATED_SCOPE_RE = re.compile(r"^checked\s*\**\s*:\s*\**\s*(.+)$", re.IGNORECASE)
_STATED_TESTING_RE = re.compile(r"^tested\s*\**\s*:\s*\**\s*(.+)$", re.IGNORECASE)

#: A concrete thing a review points at, as the downstream consumer defines it —
#: a path, a ``path:line``, a backticked symbol, or a called identifier. This is
#: a test for *structure*, never a judgement about whether the review was any
#: good; it distinguishes a review from a receipt. Kept deliberately identical
#: to the consumer's own rule (keel's ``review-verdict-insubstantial``): a scope
#: this tool is happy with but the consumer rejects is the defect in #700, and
#: the only way the two cannot drift is for the same shapes to be listed here.
_SCOPE_ANCHORS = (
    re.compile(r"[\w./-]+\.[A-Za-z0-9]{1,5}:\d+"),  # path/to/file.py:42
    re.compile(r"[\w-]+/[\w./-]+\.[A-Za-z0-9]{1,5}\b"),  # src/ai_jury/thing.py
    re.compile(r"`[^`\n]{2,}`"),  # `a_symbol`, `--a-flag`
    re.compile(r"\b\w+\.\w+\(\)"),  # module.function()
)

#: The escape hatch the consumer's rule keeps, and this one keeps with it: a
#: genuinely clean review ("checked X, Y and Z; found nothing") is a real
#: outcome and must not be forced to invent a file reference.
_CHECKED_CLAUSE_RE = re.compile(r"\bchecked\b[^.\n]{8,}", re.IGNORECASE)


def normalize_verdict(verdict: str) -> str:
    """Fold a display verdict into a single machine token.

    ``REQUEST CHANGES`` → ``REQUEST_CHANGES``, ``NEEDS-INFO`` → ``NEEDS_INFO``,
    ``NO QUORUM`` → ``NO_QUORUM``. The markdown report keeps the spaced form for
    humans; a machine consumer keys on one word, and a verdict that changes shape
    between the two renderings is a verdict that gets matched wrong.
    """
    token = flatten_inline(verdict or "").strip().upper()
    return re.sub(r"[\s\-]+", "_", token)


def _prose_lines(text: str) -> list[str]:
    """The agent's prose with fenced blocks removed.

    A review's fenced ``json`` block is the *structured* findings — it is already
    parsed into :class:`~ai_jury.findings.Finding` objects and rendered as the
    ballot's ``findings``. Left in, its serialized claim/evidence text is by far
    the longest thing in the reply and swamps every prose clause after it.
    Unterminated fences swallow the remainder, which is the fail-safe direction:
    less lifted text, never more.
    """
    lines: list[str] = []
    in_fence = False
    for raw in (text or "").splitlines():
        if raw.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(raw)
    return lines


def _clauses(text: str) -> list[str]:
    """Split agent output into flattened, capped candidate clauses (pure)."""
    out: list[str] = []
    for raw in _prose_lines(text):
        line = flatten_inline(raw).strip().lstrip("-*#> ").strip()
        if not line:
            continue
        # The split pattern consumes the whitespace after the end punctuation and
        # ``line`` is already stripped, so no part can be blank — hence no guard.
        for part in _SENTENCE_SPLIT.split(line):
            out.append(part[:_CLAUSE_MAX])
    return out


def _first_matching(clauses: list[str], pattern: re.Pattern[str]) -> str:
    for clause in clauses:
        if pattern.search(clause):
            return clause
    return ""


def _matching(clauses: list[str], pattern: re.Pattern[str], limit: int) -> list[str]:
    hits: list[str] = []
    for clause in clauses:
        if pattern.search(clause) and clause not in hits:
            hits.append(clause)
            if len(hits) >= limit:
                break
    return hits


def _files_named(findings: list) -> list[str]:
    """Distinct file paths a reviewer named, in first-reported order."""
    seen: list[str] = []
    for f in findings:
        path = flatten_inline(getattr(f, "file", "") or "").strip()
        if path and path not in seen:
            seen.append(path)
    return seen


def scope_is_substantive(scope: str) -> bool:
    """Does this scope name something a reader could go and check? (pure)

    The gate between a ballot and an abstention. Note what it does *not* do: it
    never asks whether the review was correct, thorough or agreeable — it cannot,
    and trying would make this a critic. It asks only whether the text points at
    anything, which is the single question separating "this agent reviewed the
    diff" from "this agent returned a string".
    """
    text = (scope or "").strip()
    if not text:
        return False
    return bool(_CHECKED_CLAUSE_RE.search(text)) or any(p.search(text) for p in _SCOPE_ANCHORS)


def _tick(text: str) -> str:
    """One concrete token, backticked for a scope line.

    Two jobs, and the second is why this is not an f-string at the call site.
    Backticks make the token an anchor under :data:`_SCOPE_ANCHORS`, so a scope
    built from a bare filename (``notes.md`` — no directory, so it matches none
    of the path shapes) still names something checkable. And the token is
    attacker-influenced, so its own backticks are stripped first: otherwise a
    crafted path could close the quoting and forge structure around it.
    """
    return "`" + flatten_inline(text).replace("`", "").strip() + "`"


def _stated_line(result: Any, pattern: re.Pattern[str]) -> str:
    """The value of the first ``Checked:``/``Tested:`` line in the reply (pure).

    The reviewer's own statement of its coverage, which beats anything this
    module can infer from prose — inference is exactly how a ballot that named
    nothing still shipped a scope sentence (#700). Fenced blocks are skipped as
    everywhere else here, and the value is flattened and capped before use.
    """
    for raw in _prose_lines(getattr(result, "output", "")):
        line = flatten_inline(raw).strip().lstrip("-*#> ").strip()
        match = pattern.match(line)
        if match:
            value = match.group(1).strip().strip("*").strip()
            if value:
                return value[:_CLAUSE_MAX]
    return ""


def _free_clauses(result: Any) -> list[str]:
    """Candidate prose clauses with the reviewer's own stated lines removed.

    ``Checked: …`` matches the coverage pattern, so without this the stated line
    is folded in twice — once quoted as the reviewer's statement and once again
    as an inferred clause — and the second copy is unquoted, which is how a
    crafted path gets into the scope without :func:`_tick` seeing it.
    """
    return [
        clause
        for clause in _clauses(getattr(result, "output", ""))
        if not _STATED_SCOPE_RE.match(clause) and not _STATED_TESTING_RE.match(clause)
    ]


def _claims_named(findings: list) -> list[str]:
    """Distinct claims a reviewer raised, for a review that attached no file.

    Not decoration, and **scoped to ``--issue``**: there, every finding carries
    ``file: ""`` by construction — the panel is reading an issue's prose, not a
    diff, so there is no file for a finding to name — and the file list is empty
    for a reviewer that did real work. Its claims are the only thing it *can*
    name, and naming them keeps an issue-mode ballot out of the abstention branch
    it does not belong in. That is the one legitimate exception to keel's rule
    that a scope must name a file, line or symbol, or carry a ``Checked …``
    clause, and :func:`describe_scope` applies it in issue mode only.

    In code-review mode the same fallback was a hole (#700, round 2): a finding
    with ``file: ""`` is a claim about a diff that failed to say *where*, and
    :func:`_tick` backticked it into an anchor, so a scope naming no place in the
    code passed the substance test and the ballot cast a voting verdict.
    """
    seen: list[str] = []
    for f in findings:
        claim = flatten_inline(getattr(f, "claim", "") or "").strip()
        if claim and claim not in seen:
            seen.append(claim[:_CLAUSE_MAX])
    return seen


def describe_scope(result: Any, findings: list, *, mode: str = "code") -> str:
    """What this panelist named that it read — or ``""`` when it named nothing.

    Pure and deterministic. Four sources, most authoritative first:

    1. the reviewer's own ``Checked:`` line, which the review prompt asks for;
    2. the distinct files it attached to its structured findings;
    3. up to three "checked / examined / reviewed" clauses from its prose;
    4. **in ``--issue`` mode only**, failing a file, the claims it raised.

    An empty return is the meaningful case and the reason this no longer emits a
    sentence unconditionally: with nothing from any of the four, the honest
    output is *nothing*, and the caller turns that into an abstention. The old
    fallback — "Reviewed the supplied diff; named no specific file." — asserted
    coverage from the absence of evidence for it, and read identically whether
    the agent had reviewed all 17 files or returned an empty string.

    ``mode`` gates the fourth source, and that gate is the whole of #700's third
    round. keel's rule is that a scope must name a file, line or symbol, or carry
    a ``Checked …`` clause; a backticked *claim* is none of those, and letting one
    stand as a scope meant a code-review ballot raising one ``major`` finding
    against ``file: ""`` cast ``REQUEST_CHANGES`` while naming no place in the
    code. Issue mode is the one legitimate exception — see :func:`_claims_named`
    — because there a finding genuinely has no file to name.
    """
    parts: list[str] = []
    stated = _stated_line(result, _STATED_SCOPE_RE)
    if stated:
        parts.append(f"Checked, as stated by the reviewer: {_tick(stated)}.")
    files = _files_named(findings)
    if files:
        listed = ", ".join(_tick(f) for f in files[:_FILES_LISTED])
        more = len(files) - _FILES_LISTED
        suffix = f" (+{more} more)" if more > 0 else ""
        parts.append(f"Named {len(files)} file(s): {listed}{suffix}.")
    parts.extend(_matching(_free_clauses(result), _COVERAGE_RE, _SCOPE_CLAUSES))
    if not files and mode == ISSUE_MODE:
        claims = _claims_named(findings)
        if claims:
            listed = ", ".join(_tick(c) for c in claims[:_FILES_LISTED])
            more = len(claims) - _FILES_LISTED
            suffix = f" (+{more} more)" if more > 0 else ""
            parts.append(f"Raised {len(claims)} finding(s) against no file: {listed}{suffix}.")
    scope = " ".join(parts)
    return scope if scope_is_substantive(scope) else ""


def _no_scope_reason(result: Any, findings: list | None = None) -> str:
    """Why nothing checkable could be lifted from this seat (pure).

    Five causes, kept apart because they ask for different fixes: a broken
    adapter, a CLI that answered with nothing at all, a refusal, a code review
    whose findings named no location, and a reply that reviewed nothing. Two of
    them are new in #700's second round — the empty reply did not reach here at
    all (the seat was dropped from the bundle rather than recorded, so the report
    could not name the agent that had gone quiet), and the location-less findings
    used to be dressed up as a scope instead.
    """
    if not getattr(result, "ok", False):
        return "its adapter reported failure and what came back named nothing"
    if not responded(result):
        return "it ran and returned nothing at all — an empty reply from the CLI"
    if is_abstention(getattr(result, "output", "")):
        return "it returned a refusal rather than a review"
    raised = len(findings or [])
    if raised:
        return (
            f"it raised {raised} finding(s) but attached none of them to a file, and its "
            f"reply named no file, symbol or coverage clause either, so the ballot points "
            f"at no place in the code a reader could go and check"
        )
    return "its reply named no file, symbol, coverage clause or finding"


def abstention_scope(result: Any, findings: list | None = None) -> str:
    """The scope of a ballot that could not state one: the reason, in the field.

    Deliberately anchorless — no path, no backticked symbol, no "checked …"
    clause — so a consumer applying the same substance rule reaches the same
    conclusion this module did instead of being talked past it. The record is
    here to say *nothing was reviewed*; dressing it up to survive the gate would
    reinstate the defect with better prose.

    ``findings`` is this reviewer's own findings, and it is passed so the reason
    can tell "said nothing" from "said something that named nowhere" — the second
    is a reviewer that worked and skipped the locations, and a reason that called
    it "named no finding" would send its author looking for the wrong problem.
    """
    name = flatten_inline(getattr(result, "agent", "") or "").strip() or "this reviewer"
    return (
        f"Abstention: no scope can be stated for '{name}' because "
        f"{_no_scope_reason(result, findings)}. Recorded as an abstention rather than an "
        f"approval — an agent that named nothing did not review, and counting it "
        f"as one that did is the difference between a panel and a receipt."
    )


def describe_testing(result: Any) -> str:
    """What this panelist ran to verify its claims, or :data:`NOT_STATED`.

    The reviewer's ``Tested:`` line first, then any verification clause in its
    prose. Both are lifted verbatim (flattened and capped) rather than
    summarized: a testing claim carried downstream as evidence must be the
    reviewer's words, not this module's paraphrase of them. With neither, the
    field says plainly that nothing was run — which is a statement about the
    review, where "not stated" was a statement about the field.
    """
    stated = _stated_line(result, _STATED_TESTING_RE)
    if stated:
        return f"Tested, as stated by the reviewer: {stated}"
    clause = _first_matching(_free_clauses(result), _TESTING_RE)
    return clause or NOT_STATED


def _spec_for(config: Any, agent_name: str) -> Any:
    for spec in getattr(config, "agents", []) or []:
        if spec.name == agent_name:
            return spec
    return None


def requested_model(spec: Any) -> str:
    """The model id this agent's CLI was actually asked for (``""`` for none).

    ``spec.model`` is what the operator wrote down, but it is not always what
    was sent: for a vendor that encodes reasoning effort *in the model id*, the
    ``effort`` knob rewrites it, and a ballot reporting the unmapped id would
    name a model the run never asked for. :func:`ai_jury.adapters.effort_args` is
    pure and is the one place that mapping lives, so it is called rather than
    re-derived. An effort level it rejects degrades to the configured id — a bad
    config value is ``validate_config``'s to refuse, never a ballot's to crash on.
    """
    from .adapters import effort_args

    configured = (getattr(spec, "model", "") or "").strip()
    try:
        plan = effort_args(
            getattr(spec, "vendor", "") or "", getattr(spec, "effort", None), configured
        )
    except ValueError:
        return configured
    return (getattr(plan, "model", "") or configured or "").strip()


def describe_model(config: Any, agent_name: str) -> tuple[str, str]:
    """``(model, source)`` for the agent that answered in this slot.

    Never the empty string for a slot that has an agent. An empty ``model`` was
    the provenance half of #700: a ballot naming ``vendor: "openai"`` and
    ``model: ""`` cannot answer "was that the same model as the other seat", and
    provenance is the entire product of a cross-vendor panel. Where the CLI was
    invoked with no id pinned there *is* an honest answer — the CLI's own
    default, which the CLI does not report back — so the field says that instead
    of going blank and letting a reader guess which of the two it meant.

    ``source`` is the same fact as one machine token, so a consumer can tell an
    id from a statement about one without parsing English.
    """
    name = (agent_name or "").strip()
    if not name:
        return "", MODEL_NONE
    spec = _spec_for(config, name)
    if spec is None:
        return f"unknown (no agent named '{name}' in this run's config)", MODEL_UNKNOWN
    model = requested_model(spec)
    if model:
        return model, MODEL_REQUESTED
    command = (getattr(spec, "command", "") or getattr(spec, "vendor", "") or name).strip()
    return _CLI_DEFAULT_MODEL.format(command=command), MODEL_CLI_DEFAULT


def _vendor_for(config: Any, agent_name: str) -> str:
    for spec in getattr(config, "agents", []) or []:
        if spec.name == agent_name:
            return spec.vendor or ""
    return ""


def participating(outcome: Any) -> list:
    """Every round-1 seat that ran, in the stable panel order — one ballot each.

    It used to be "seats that returned output at all", which quietly dropped a
    silent agent from the bundle: an `alpha` result with empty output left no
    `alpha` entry, and the report could not say which seat had returned nothing
    (#700, round 2). It is recorded as an abstention naming the seat and the
    reason instead, and :func:`ai_jury.panel.is_review` — not this function's
    length — is what decides whether it counts as a review.

    That is the change from #699, where the length of this *was* the count.
    Recording a seat and counting it as a review are now two different questions,
    because a seat can ballot without reviewing; the count lives in
    :mod:`ai_jury.panel` and reads the produced records.
    """
    return ballot_seats(getattr(outcome, "reviews", []) or [])


def _stance_by_reviewer(outcome: Any, names: list[str], mode: str) -> dict[str, str]:
    """Per-panelist stance, derived exactly as the vote tally derives ballots.

    :func:`ai_jury.voting.tally_votes` is the single source of truth for turning
    "the worst supported finding this reviewer raised" into a stance, so it is
    called rather than reimplemented — a second copy of that mapping is a second
    place for the severity thresholds to drift.
    """
    result = tally_votes(getattr(outcome, "groups", []) or [], names, mode=mode)
    return {b.reviewer: normalize_verdict(b.vote) for b in result.ballots}


def _verdict_for(result: Any, stances: dict[str, str], *, scoped: bool) -> str:
    """This panelist's stance, or :data:`ABSTAIN` (pure).

    ``scoped`` is the third way to abstain and the one #700 added: a seat that
    exited 0, said something, and named nothing checkable — including, since
    round 2, one whose only "scope" was a claim raised against no file in a
    code review. The other two — a failed adapter, an empty reply or a refusal —
    were already here, and this is the same principle applied one step further
    out. A reviewer whose scope is empty raised no findings *with a location*
    either, so the tally would have handed it the clear stance
    (``APPROVE``/``READY``): an approval inferred from silence, which is
    precisely what :mod:`ai_jury.voting` refuses to do (#251).
    """
    if not getattr(result, "ok", False):
        return ABSTAIN
    if is_abstention(getattr(result, "output", "")):
        return ABSTAIN
    if not scoped:
        return ABSTAIN
    return stances.get(getattr(result, "agent", ""), ABSTAIN)


def chair_verdict(outcome: Any, vote: Any = None) -> str:
    """The run's final verdict as one machine token.

    The panel vote when voting; otherwise the label the chair opened its
    synthesis with. The headline lift is
    :func:`ai_jury.report._verdict_headline` — reused rather than duplicated, so
    the JSON verdict and the markdown TL;DR can never disagree. The headline is a
    label plus a sentence (``REQUEST CHANGES — one confirmed major issue.``); only
    the label is a verdict, so the sentence is dropped.
    """
    from .report import _verdict_headline

    headline = _verdict_headline(getattr(outcome, "synthesis", None), vote)
    if not headline:
        return ABSTAIN
    label = re.split(r"[—–:.]| - ", headline, maxsplit=1)[0]
    return normalize_verdict(label) or ABSTAIN


def _abstained_because(result: Any) -> str:
    """Why a seat that *did* name something checkable still abstained (pure).

    The companion to :func:`_no_scope_reason`, and deliberately not that
    function: this ballot's scope is substantive, so every reason phrased around
    "it named nothing" would be false of it. Only two of :func:`_verdict_for`'s
    three gates can fire while the scope stands — a failed adapter and a refusal
    — and the third (an empty scope) never reaches here, because a seat with no
    scope gets :func:`abstention_scope` instead.
    """
    if not getattr(result, "ok", False):
        return "its adapter reported failure"
    return "it returned a refusal rather than a review"


def _chaired_ballot_sentence(result: Any, ballot: dict) -> str:
    """What the chairing agent's own ballot is, read off the record (#700, round 3).

    Said on the ballot as well as on the chair record, because the two are read
    in different places: a consumer that posts one verdict per review shows this
    text on its own, with no chair record beside it.

    The sentence is a function of ``counts_as_review`` — the answer
    :func:`ai_jury.panel.is_review` already gave for this record — and never of
    "did a scope come back". Round 2 keyed it on the scope alone and appended it
    before the verdict was in, so a chaired seat that named a file and then
    refused shipped ``verdict: ABSTAIN``, ``counts_as_review: false`` and a scope
    telling the human reading it that this ballot was one of the panel's reviews.
    The count and the prose beside it are one statement, or the prose is a second
    definition of a review that nothing keeps honest.
    """
    lead = " This reviewer also chaired the run (verification and synthesis);"
    tail = " the chair record is the panel's consensus rather than a further review."
    if ballot.get("counts_as_review"):
        return f"{lead} this ballot is one of the panel's reviews, and{tail}"
    return (
        f"{lead} this ballot abstained — {_abstained_because(result)} — so it is"
        f" NOT one of the panel's reviews, and{tail}"
    )


def _verified_count(outcome: Any, name: str) -> int:
    """Consensus groups this reviewer contributed to that the verifier upheld."""
    return sum(
        1
        for g in getattr(outcome, "groups", []) or []
        if name in (getattr(g, "reviewers", []) or [])
        and (getattr(g, "status", "") or "") == "verified"
    )


def reviewer_ballots(
    outcome: Any, config: Any, *, vote: Any = None, mode: str = "code"
) -> list[dict]:
    """The JSON report's ``reviewers`` array: one ballot per seat, then the chair.

    Panelist entries carry ``name``, ``role: "panelist"``, ``chaired``,
    ``vendor``, ``model``, ``model_source``, ``verdict``, ``scope``,
    ``scope_substantive``, ``counts_as_review``, ``testing``, ``findings``
    (indexes into the report's top-level ``findings`` array), ``round1_ok``,
    ``verified_count`` and ``duration_s``. The chair's entry is the one carrying
    ``role: "chair"`` and is always last.

    ``role`` is what a consumer splits on: the ``chair`` entry is the panel's
    consensus record and every other entry is a ballot (#699). But a ballot is
    not automatically a review — ``scope_substantive`` and ``verdict`` are the
    two facts :func:`ai_jury.panel.is_review` reads, and ``counts_as_review``
    carries its answer so the consumer need not re-derive it. Every seat that ran
    gets an entry, a silent one included, because the report has to be able to
    say *which* agent returned nothing; the count is what excludes it.

    ``chaired`` and the chair entry's ``agent``/``ballot_counted`` exist because
    the chairing agent reviews too: without them a reader cannot tell that the
    ``claude`` ballot and the ``chair`` record are the same agent, nor whether
    that agent contributed a review at all — and a reader who guesses drops a
    review the panel cast.

    ``scope`` and ``testing`` live here rather than only in the bundle (#700) so
    that the two renderings are the same text by construction: the JSON report
    said who voted and the bundle said what they read, and a reader comparing
    them had no guarantee the second described the first.
    """
    seats = participating(outcome)
    names = [getattr(r, "agent", "") for r in seats]
    stances = _stance_by_reviewer(outcome, names, mode)
    all_findings = list(getattr(outcome, "findings", []) or [])
    chair_name = getattr(outcome, "chair", "") or ""

    entries: list[dict] = []
    for r in seats:
        name = getattr(r, "agent", "")
        chaired = bool(chair_name) and name == chair_name
        indexes = [i for i, f in enumerate(all_findings) if f.reviewer == name]
        own_findings = [all_findings[i] for i in indexes]
        scope = describe_scope(r, own_findings, mode=mode)
        scoped = bool(scope)
        if not scoped:
            scope = abstention_scope(r, own_findings)
        model, model_source = describe_model(config, name)
        entry = {
            "name": name,
            "role": PANELIST_ROLE,
            "chaired": chaired,
            "vendor": getattr(r, "vendor", "") or "",
            "model": model,
            "model_source": model_source,
            "verdict": _verdict_for(r, stances, scoped=scoped),
            "scope": scope,
            # The two facts the count is made of, stated structurally rather than
            # left to be inferred from the prose in ``scope`` — a consumer that
            # had to parse English for this is a consumer that will get it wrong.
            "scope_substantive": scoped,
            "testing": describe_testing(r),
            "findings": indexes,
            "round1_ok": bool(getattr(r, "ok", False)),
            "verified_count": _verified_count(outcome, name),
            "duration_s": round(float(getattr(r, "duration_s", 0.0) or 0.0), 3),
        }
        # Derived, never asserted: the answer this record carries is the one the
        # gate and the announcements use, computed by the same function.
        entry["counts_as_review"] = is_review(entry)
        # After the answer, never before it: the chair's sentence *reports* that
        # answer, so it cannot be written while the answer is still unknown.
        if scoped and chaired:
            entry["scope"] += _chaired_ballot_sentence(r, entry)
        entries.append(entry)

    chair_model, chair_model_source = describe_model(config, chair_name)
    verify = getattr(outcome, "verify", None)
    chair_scope = _chair_scope(outcome, entries)
    entries.append(
        {
            "name": CHAIR_NAME,
            "role": CHAIR_ROLE,
            "agent": chair_name,
            # Whether the chairing agent's own ballot is one of the counted
            # reviews — the fact the bundle never stated (#699). A chairing agent
            # that ran and abstained has a ballot in the bundle and is not a
            # review, so "did it ballot" is the wrong question to answer here.
            "ballot_counted": any(e["counts_as_review"] for e in entries if e["chaired"]),
            "reviews_supplied": review_count(entries),
            "vendor": _vendor_for(config, chair_name),
            "model": chair_model,
            "model_source": chair_model_source,
            "verdict": chair_verdict(outcome, vote),
            "scope": chair_scope,
            # Measured, not asserted: the chair record is a verdict too, and a
            # consumer applying one substance rule applies it here as well.
            "scope_substantive": scope_is_substantive(chair_scope),
            # This synthesis record is NOT one of the reviews, whatever its
            # scope says: the consumer reads it as the panel's consensus.
            "counts_as_review": False,
            "testing": describe_testing(verify) if verify is not None else NOT_STATED,
        }
    )
    return entries


def _keel_finding(f: Any) -> dict:
    """One finding in the consumer's shape: ``file`` → ``path``, ``claim`` → ``message``."""
    line = getattr(f, "line", None)
    return {
        "severity": getattr(f, "severity", "") or "",
        "path": getattr(f, "file", "") or "",
        "line": line if isinstance(line, int) else None,
        "message": flatten_inline(getattr(f, "claim", "") or ""),
    }


def _chair_findings(outcome: Any) -> list:
    """The chair's surviving evidence: group representatives the verifier did not reject."""
    return [
        g.representative
        for g in getattr(outcome, "groups", []) or []
        if (getattr(g, "status", "") or "") != "unsupported"
    ]


def _chair_role_sentence(chair_agent: str, chaired_ballot: dict | None) -> str:
    """State, in the record itself, whether this chair's ballot is counted (#699).

    A chair that reviewed and a chair that only synthesised produce records that
    are otherwise identical, and a reader who cannot tell them apart guesses —
    which is how a panel's ballots get handed on short. So the record says which
    agent chaired and where (or whether) its own ballot is in the bundle, and it
    says plainly that this synthesis record is not itself one of the reviews.
    """
    who = f"`{chair_agent}`" if chair_agent else "This run's chair"
    if chaired_ballot is not None and chaired_ballot.get("counts_as_review"):
        return (
            f"The chair is {who}, which also sat on the panel: its ballot is the "
            f"'{chaired_ballot['name']}' review in this bundle and counts as one "
            f"of the reviews. This synthesis record does not — it is the panel's "
            f"consensus, not a ballot."
        )
    if chaired_ballot is not None:
        # It balloted and the ballot is not a review. Saying "no ballot from it"
        # would be false and saying "its ballot counts" would be the defect, so
        # the record says both facts. It does *not* say why: "named nothing
        # checkable" was one of the three ways to abstain asserted as if it were
        # the only one, and it is plainly false of a seat that named a file and
        # then refused. The ballot's own scope carries the cause (#700, round 3).
        return (
            f"The chair is {who}, which also sat on the panel but abstained: its "
            f"'{chaired_ballot['name']}' ballot is in this bundle and is NOT counted "
            f"as a review — that ballot's own scope says why. This "
            f"synthesis record is not counted either — it is the panel's consensus, "
            f"not a ballot."
        )
    return (
        f"The chair is {who}, which returned no panel review of its own, so this "
        f"bundle carries no ballot from it. This synthesis record is not counted "
        f"as a review either — it is the panel's consensus, not a ballot."
    )


def _chair_scope(outcome: Any, panelists: list[dict]) -> str:
    """What the chair's synthesis covered (pure).

    Backticked file names, like every other scope here (#700): the chair record
    is a verdict too, and a consumer applying one substance rule applies it to
    this record as well. The chairing agent's name is already backticked by
    :func:`_chair_role_sentence`, so a chaired run anchors either way.

    The two numbers it quotes are deliberately different (#700, round 2): how
    many of the ballots are **reviews**, and how many **records** the bundle
    carries. They used to be the same integer, which is exactly how an abstaining
    ballot got announced as a review.
    """
    groups = getattr(outcome, "groups", []) or []
    files = _files_named([g.representative for g in groups])
    listed = ", ".join(_tick(f) for f in files[:_FILES_LISTED]) if files else "no specific file"
    more = len(files) - _FILES_LISTED
    suffix = f" (+{more} more)" if more > 0 else ""
    chair_agent = getattr(outcome, "chair", "") or ""
    chaired = next((p for p in panelists if p.get("chaired")), None)
    reviews = review_count(panelists)
    # "Further" than the chair's own ballot, which the sentence before this one
    # has already accounted for — and no cause is named, because these seats
    # abstained for whichever of the three reasons applied to each and their own
    # scopes say which (#700, round 3).
    abstained = sum(1 for p in panelists if not p.get("chaired") and not is_review(p))
    abstained_clause = (
        f" {abstained} further ballot(s) abstained; they are recorded here but"
        f" are not reviews, and each one's scope says why."
        if abstained
        else ""
    )
    return (
        f"Chair synthesis over {reviews} panel review(s) and "
        f"{len(groups)} consensus group(s), across {listed}{suffix}. "
        f"{_chair_role_sentence(chair_agent, chaired)}{abstained_clause} This bundle carries "
        f"{reviews} review(s) plus this record, "
        f"{bundle_records(len(panelists))} records in all."
    )


def keel_reviews(outcome: Any, config: Any, *, vote: Any = None, mode: str = "code") -> list[dict]:
    """The ``--format keel-reviews`` bundle: one record per seat, plus the chair.

    Each record is ``{reviewer, verdict, scope, findings, testing, vendor, model,
    model_source, counts_as_review}`` where ``findings`` are ``{severity, path,
    line, message}`` objects — the shape a consumer of head-pinned per-reviewer
    verdicts accepts. Pure: the caller serializes it.

    A projection of :func:`reviewer_ballots`, not a second derivation of the same
    facts (#700). Every field but ``findings`` is renamed or copied straight
    across, so the verdict the JSON report shows for a panelist and the verdict
    the consumer is handed for it cannot disagree — and neither can the scope
    that is supposed to justify it.

    Two of those fields are the projection catching up with the ballot (#700,
    round 2). ``model_source`` because ``model`` here changed meaning in the same
    release — a CLI default is now an English sentence — and a machine consumer
    of *this* shape could not tell a requested id from a default without parsing
    prose; the ``reviewers`` array grew the discriminator and this one did not.
    ``counts_as_review`` because the bundle now carries abstention records for
    seats that returned nothing, and a consumer counting the array would count
    them.
    """
    ballots = reviewer_ballots(outcome, config, vote=vote, mode=mode)
    all_findings = list(getattr(outcome, "findings", []) or [])

    records: list[dict] = []
    for b in ballots:
        chair = b.get("role") == CHAIR_ROLE
        own = _chair_findings(outcome) if chair else [all_findings[i] for i in b["findings"]]
        records.append(
            {
                "reviewer": b["name"],
                "verdict": b["verdict"],
                "scope": b["scope"],
                "findings": [_keel_finding(f) for f in own],
                "testing": b["testing"],
                "vendor": b["vendor"],
                "model": b["model"],
                "model_source": b["model_source"],
                "counts_as_review": b["counts_as_review"],
            }
        )
    return records
