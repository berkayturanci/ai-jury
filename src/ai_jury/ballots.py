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

**And the name has to exist** (issue #710). Mirroring the consumer's *shapes* was
not enough on its own: ``describe_scope`` backticked whatever the reviewer wrote
on its ``Checked:`` line, so ``Checked: nothing`` was rendered as an anchor,
passed the shape test and counted as a review — the form of naming something with
none of the substance, satisfiable by an agent that read nothing. Every stated
token is now resolved against the change the panel was shown
(:class:`ai_jury.largediff.ChangeIndex`, carried on the outcome), and the shape
test is what the *resolved* tokens then have to pass. This module is therefore
deliberately **stricter** than the consumer on that one path and never looser:
a scope it accepts is one the consumer accepts.
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

**And what it says about itself has to be true of the run** (#709/#710, round 2).
Two claims here were still stated more strongly than the evidence behind them:

* the ``Checked:`` line was split into tokens **lexically**, on whitespace, so a
  changed file whose name contains a space arrived as two tokens that name
  nothing and the ballot abstained under ``not_in_change`` — the rule for
  catching a review of nothing, refusing a real review over a space. Tokens are
  now cut against the change index itself, and a span the reviewer quoted is one
  token whatever is inside it;
* a ``model`` id this module *derived* went out under ``model_source:
  requested``, whose whole claim is that the id was on the wire. Deriving one is
  still right where no invocation recorded one, but it is labelled
  :data:`MODEL_RECOMPUTED` and it is never the answer for a stale result-cache
  entry — the record format changed, so the cache refuses it rather than
  recomputing over the gap.
"""

from __future__ import annotations

import re
from typing import Any

from .findings import flatten_inline
from .panel import (
    ABSTAIN,
    ADAPTER_FAILED,
    CAUSE_FIELD,
    CHAIR_ROLE,
    NAMED_NOTHING,
    NOT_IN_CHANGE,
    PANELIST_ROLE,
    REFUSED,
    SILENT,
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
MODEL_RECOMPUTED = "recomputed"  # no invocation recorded one; derived from the config
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
#: Drift in the other direction is fine and intended: since #710 a stated
#: ``Checked:`` token must also *resolve* against the change before it is
#: rendered as one of these shapes, so this tool is stricter there and never
#: looser — a scope it accepts is one the consumer accepts.
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

# --- Resolving a stated `Checked:` line against the change (issue #710) -----
#
# `_tick` backticks any non-empty stated value, and a backticked token is an
# anchor, so `Checked: nothing` — or `everything`, or `the diff` — was rendered
# as an anchor, passed :func:`scope_is_substantive`, and made the ballot a
# review. That is the *shape* of an anchor with none of the substance, and a rule
# satisfied by an agent that read nothing is #700's own failure one layer up.
#
# So a stated token is resolved against the change the panel was shown — the
# paths and symbols of :class:`ai_jury.largediff.ChangeIndex`, carried on the
# outcome. The check is local and cheap: the jury holds the diff at that moment.

#: List punctuation: a **hard** token boundary, kept apart from whitespace,
#: which is not one. See :func:`_scope_tokens` — a changed file whose name
#: contains a space is one token spelled with whitespace in the middle of it,
#: and a lexical split on ``[\s,;]+`` cut it in half (#710, round 2).
_LIST_SEP_SPLIT = re.compile(r"[,;]+")

#: Whitespace: a *candidate* boundary, resolved against the change.
_WHITESPACE_SPLIT = re.compile(r"\s+")

#: A span the reviewer itself marked as one token — backticks, or double quotes,
#: straight or curly. Whatever is inside is one token even when it contains a
#: space or a comma: the reviewer drew the boundary, and re-splitting it is the
#: same defect the joining below exists to fix. Single quotes are deliberately
#: absent: ``'`` and ``’`` are also apostrophes, so ``the reviewer's own file``
#: would open a span at ``'s`` and swallow the rest of the line.
_QUOTED_SPAN_RE = re.compile(r"`([^`\n]+)`|\"([^\"\n]+)\"|“([^”\n]+)”")

#: The same span shapes, anchored: a run that is *entirely* one mark, marks
#: included. See :func:`_unmarked` — the marks a reviewer nested inside its own
#: span come off too, so a path that is quoted *and* backticked resolves the way
#: either alone does.
_NESTED_MARK_RE = re.compile(r"\A`([^`\n]+)`\Z|\A\"([^\"\n]+)\"\Z|\A“([^”\n]+)”\Z")

#: How many nested layers of marks are peeled off a span. Two, because two is as
#: deep as nesting goes: there are three mark shapes and none can sit inside
#: itself (``[^`\n]+`` cannot hold a backtick), so once :func:`_marked_spans` has
#: taken the pair the span opened with, at most two remain. Anything a deeper
#: line left on can only fail to resolve, which is reported.
_MAX_NESTED_MARKS = 2

#: How many whitespace-separated pieces may be joined while looking for a path.
#: A cap rather than the whole line: the search is quadratic in the window, the
#: line is attacker-influenced, and a filename with five spaces in it is already
#: an outlier. Exceeding it can only make a token fail to resolve, which is the
#: safe direction — an unresolved token is reported as unresolved.
_MAX_JOINED_PIECES = 6

#: Punctuation a token may be **wrapped** in: brackets and quotes, plus the
#: markdown emphasis mark. Stripped from either end, so ``(src/a.py)`` and
#: ``src/a.py`` are one token. Nothing here can begin a file name, which is why
#: taking it off either end is unconditional. See :func:`_edge_stripped`.
_WRAP_EDGE = "`'\"“”‘’()[]{}<>*"

#: Sentence punctuation a token may be **trailed** by, stripped from the end
#: only — a full stop ends a sentence far more often than it opens a filename,
#: but a *leading* dot is part of the name and is never touched. A trailing
#: ``:`` goes too, but a ``:42`` does not — that is a line. ``_`` is in neither
#: set: it is part of the identifier, and stripping it turned ``_stated_line``
#: into a symbol the change does not have.
_TRAIL_EDGE = ".,;:!?"

#: ``path:line`` / ``path:line-line`` — the line part is dropped before the path
#: is looked up, because a diff index knows files, not line numbers.
_PATH_LINE_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+(?:-\d+)?)$")

#: A token that *claims* to name something: a path, a ``path:line``, a call, a
#: dotfile, or an identifier (an ``_`` or an interior case change). Ordinary
#: connective prose in the same sentence — "lines", "and", "the tests" — claims
#: nothing, so it is neither counted as an anchor nor reported as a broken one. A
#: token the reviewer backticked counts too: the reviewer marked it as a name.
#:
#: The **leading-dot** alternative is what makes ``.gitignore`` a name (#710,
#: round 4). The trailing-extension one never reached it — ``gitignore`` is nine
#: characters, not a suffix — so an *absent* dotfile was dropped as connective
#: prose and the ballot said the line named nothing, for a path the reviewer
#: named exactly. Prose is untouched by it: a full stop that ends a sentence
#: sits at the end of the piece before it, never at the start of the next.
# The dotted alternative accepts any suffix, not one of one to five characters:
# :func:`_path_shaped` treats every dotted name without ``()`` as a path claim,
# and the two predicates disagreeing put ``foo.kotlin`` in the wrong bucket —
# refused as a path, then dropped as prose instead of reported as a claim that
# failed (#711 round 10).
_NAME_SHAPED_RE = re.compile(r"[/\\]|\A\.[A-Za-z0-9]|\.[A-Za-z0-9]+$|\(\)$|_|[a-z0-9][A-Z]")

#: Anchor-forming characters, removed before an unresolved token is quoted in an
#: abstention. See :func:`_deanchor`.
_DEANCHOR = str.maketrans(dict.fromkeys("`/\\:()[]{}<>", " "))

#: ...and the one word that forms an anchor without punctuation, under
#: :data:`_CHECKED_CLAUSE_RE`. Elided rather than dropped silently.
_CHECKED_WORD_RE = re.compile(r"\bchecked\b", re.IGNORECASE)


def _deanchor(text: str) -> str:
    """A reviewer's token, quoted so it cannot pass for an anchor (pure).

    An unresolved token is named in the abstention — a reader has to see that
    the reviewer claimed ``src/made/up.py`` — but the abstention is
    *deliberately anchorless* (see :func:`abstention_scope`): a consumer applying
    the same substance rule has to reach the same conclusion this module did,
    and it would not if the sentence explaining that nothing was checked itself
    contained a path. So the shapes that make an anchor are removed: the
    punctuation of :data:`_SCOPE_ANCHORS` and the ``checked …`` clause. The
    reviewer's words survive; only their ability to masquerade as evidence does.
    """
    flat = _CHECKED_WORD_RE.sub("…", flatten_inline(text))
    return " ".join(flat.translate(_DEANCHOR).split())[:_CLAUSE_MAX]


def _marked_spans(value: str) -> list[tuple[str, bool]]:
    """*value* as ``(text, quoted)`` runs (pure).

    ``quoted`` runs are the spans the reviewer wrapped in backticks or double
    quotes; the rest is everything between them. Splitting on the marks first is
    what lets a quoted ``docs/my file.py`` survive as one token: a reviewer that
    quoted a name has already said where it begins and ends, and no rule below
    is entitled to a second opinion about that.
    """
    runs: list[tuple[str, bool]] = []
    pos = 0
    for match in _QUOTED_SPAN_RE.finditer(value):
        if match.start() > pos:
            runs.append((value[pos : match.start()], False))
        # Exactly one alternative of :data:`_QUOTED_SPAN_RE` can have matched.
        runs.append((match.group(1) or match.group(2) or match.group(3), True))
        pos = match.end()
    if pos < len(value):
        runs.append((value[pos:], False))
    return runs


def _unmarked(text: str) -> str:
    """One marked span's content, with nested marks peeled off (pure).

    :func:`_marked_spans` removes the pair the span *opened* with, and a
    reviewer that both quoted and backticked a path leaves the inner pair on:
    ``resolve_stated_scope("“`docs/my file.py`”", changed)`` looked up the
    backticked string, matched nothing, and reported the reviewer's own
    ``docs/my file.py`` as a claim that failed (#710, round 3). Quoting a
    backticked path is the same claim about the same file as either mark alone,
    so the marks come off before the lookup — as many layers as
    :data:`_MAX_NESTED_MARKS` allows, whichever mark each layer used.
    """
    inner = text.strip()
    for _ in range(_MAX_NESTED_MARKS):
        match = _NESTED_MARK_RE.match(inner)
        if match is None:
            break
        inner = (match.group(1) or match.group(2) or match.group(3)).strip()
    return inner


def _path_base(token: str) -> str:
    """*token* with a trailing ``:line`` / ``:line-line`` dropped (pure)."""
    match = _PATH_LINE_RE.match(token)
    return match.group("path") if match else token


#: A call marker ``()`` that may sit inside wrapping punctuation and before
#: trailing sentence punctuation: ``(module.env()),`` names the call
#: ``module.env()``. Group 1 is everything before the marker. The trailing class
#: is *derived* from the same two edge sets the plain strip uses, so the two
#: cannot disagree about what counts as wrapping (#711 round 9: the hand-written
#: class lacked ``<>`` and the curly quotes, which ``_WRAP_EDGE`` has).
_WRAPPED_CALL_RE = re.compile(r"^(.*?)\(\)[" + re.escape(_WRAP_EDGE + _TRAIL_EDGE) + r"]*$")


def _edge_stripped(piece: str) -> str:
    """*piece* with its edge punctuation removed (pure).

    Wrapping punctuation — brackets and quotes, :data:`_WRAP_EDGE` — comes off
    either end; trailing sentence punctuation (:data:`_TRAIL_EDGE`) comes off
    the end. **A character that begins a name is never removed**: a dot followed
    by a letter or a digit, a letter, a digit and ``_`` are in neither set, so
    ``(.gitignore).`` is ``.gitignore``, ``src/a.py,`` is ``src/a.py``, and
    ``all.`` is ``all``.

    A leading dot is part of a file name, and the earlier rounds of #710 stripped
    it with everything else and then tried to put it back: the trims that
    restored the stripped characters were offered to the change index,
    most-stripped first, and the first trim the index confirmed was the token.
    That asked the diff a question the token had already answered — and the diff
    answered it wrongly whenever it happened to contain the stripped remnant.
    With ``env`` (or ``bin/env``) changed, ``Checked: .env`` resolved on the
    fully stripped ``env``, matching on a component boundary in a *different*
    file, and the ballot counted as a review of a file the reviewer never named;
    ``.gitignore`` did the same against a changed ``src/gitignore`` (#709/#710,
    round 5). Never stripping the dot removes the question rather than adding a
    case to it: ``.env`` against a changed ``env`` is a name this change does not
    have, reported unresolved under ``not_in_change`` — and one deterministic
    strip is all a token needs, so no trim is enumerated against the index at
    all.
    """
    # A trailing ``()`` is a call marker, not wrapping punctuation: it is what
    # tells :func:`_token_resolves` that ``module.function()`` is a symbol claim
    # rather than a file (#711 round 7), so it is kept whole.
    # The marker is looked for *inside* any wrapping, not at the raw end: on
    # ``(module.env()),`` the last characters are wrap and trail punctuation,
    # and a check at the raw end missed the call and then ate its parentheses
    # as wrapping (#711 round 8).
    marked = _WRAPPED_CALL_RE.match(piece)
    if marked:
        stripped = marked.group(1).lstrip(_WRAP_EDGE)
        return stripped + "()" if stripped else ""
    return piece.lstrip(_WRAP_EDGE).rstrip(_WRAP_EDGE + _TRAIL_EDGE)


def _joined_against_change(pieces: list[str], changed: Any) -> list[str]:
    """One whitespace-separated run's pieces, with spaced paths rejoined (pure).

    **The change index is the tokeniser, not the whitespace.** ``Checked:
    docs/my file.py`` used to split into ``docs/my`` and ``file.py``, neither of
    which is in a diff that changes ``docs/my file.py``: the ballot came back
    ``scope_substantive: false``, ``counts_as_review: false``,
    ``abstention_cause: not_in_change`` — a real review of a real file, refused
    for a space in its name, by the rule that exists to catch reviews of nothing.

    So adjacent pieces are offered to the index joined, longest window first, and
    a join that names a changed path *is* the token. Longest-first matters: with
    both ``my file.py`` and ``docs/my file.py`` changed, the reviewer named the
    second. A join is only ever accepted when the index confirms it, so this can
    turn a non-token into a token but never the reverse — the failure direction
    is an unresolved token, which is reported as one.
    """
    tokens: list[str] = []
    index = 0
    while index < len(pieces):
        joined = ""
        width = 1
        for size in range(min(_MAX_JOINED_PIECES, len(pieces) - index), 1, -1):
            candidate = _edge_stripped(" ".join(pieces[index : index + size]))
            if changed.has_path(_path_base(candidate)):
                joined, width = candidate, size
                break
        tokens.append(joined or _edge_stripped(pieces[index]))
        index += width
    return tokens


def _scope_tokens(value: str, changed: Any) -> list[str]:
    """The distinct candidate tokens of one stated ``Checked:`` value (pure).

    Tokenised **against the change**, not lexically (#710, round 2). Three rules,
    in this order: a span the reviewer quoted is one token whatever is inside it;
    list punctuation is a hard boundary; and whitespace is a boundary only where
    joining across it does not name a changed path.
    """
    seen: list[str] = []
    for text, quoted in _marked_spans(flatten_inline(value or "")):
        if quoted:
            # No edge punctuation is stripped: inside the reviewer's own marks
            # there is none to strip, and stripping it would cost the leading
            # dot of a `.gitignore` the reviewer took care to quote. Only marks
            # the reviewer nested inside its own span come off — see
            # :func:`_unmarked`.
            candidates = [_unmarked(text)]
        else:
            candidates = [
                token
                for segment in _LIST_SEP_SPLIT.split(text)
                for token in _joined_against_change(
                    [p for p in _WHITESPACE_SPLIT.split(segment) if p], changed
                )
            ]
        for token in candidates:
            if token and token not in seen:
                seen.append(token)
    return seen


def _is_name_shaped(token: str, value: str) -> bool:
    """Does *token* claim to name something? (pure — see :data:`_NAME_SHAPED_RE`)

    A token the reviewer *marked* counts too, whichever mark it used: quoting a
    token is the reviewer saying it is a name, and a claim that failed has to be
    reported as one rather than dropped as connective prose.
    """
    if _NAME_SHAPED_RE.search(token):
        return True
    return any(f"{o}{token}{c}" in (value or "") for o, c in (("`", "`"), ('"', '"'), ("“", "”")))


def _path_shaped(base: str) -> bool:
    """Does *base* claim to be a path rather than a symbol? (pure)

    A directory, a dotfile, or any dotted name written without ``()``. The
    extension list this replaced could not be complete — ``foo.proto`` fell
    through to the symbol index and matched a ``proto()`` call (#711 round 7) —
    so the rule is now the reviewer's own punctuation: ``module.function()``
    names code, ``module.function`` and ``foo.proto`` name a file.
    """
    return "/" in base or base.startswith(".") or ("." in base and not base.endswith("()"))


def _token_resolves(token: str, changed: Any) -> bool:
    """Is *token* a path or symbol that is actually in the change? (pure)

    A path-shaped token — a directory, a dotfile, or a dotted name without
    ``()`` — is a path claim and resolves only as a path. It is never split and
    asked of the symbol index: ``.env`` against a hunk that adds ``env(1)`` used
    to resolve on the remnant ``env`` and count as a review of a file the
    reviewer never named (#711 round 6). A symbol claim is a token with no dot,
    or one ending in ``()``; for ``module.function()`` the member ``function``
    is asked, because the qualification is the reviewer's and the member is
    the claim.
    """
    base = _path_base(token)
    if changed.has_path(base):
        return True
    if _path_shaped(base):
        return False
    member = base.rstrip("()").rsplit(".", 1)[-1]
    return bool(member) and changed.has_symbol(member)


def resolve_stated_scope(value: str, changed: Any) -> tuple[list[str], list[str]]:
    """Split one ``Checked:`` value into ``(resolved, unresolved)`` tokens (pure).

    ``resolved`` are the tokens that name a path or a symbol present in
    *changed*; they are what makes the scope substantive. ``unresolved`` are the
    tokens that are shaped like a name and are not in the change — reported, so
    a reader sees the claim that failed, but never anchoring anything.

    **The split into tokens is itself made against the change** (see
    :func:`_scope_tokens`), because a lexical one gets a changed file whose name
    contains a space wrong in the direction that costs a real review.

    **A mixed line is carried by its real tokens.** ``Checked:
    src/ai_jury/ballots.py, src/made/up.py`` is a review of
    ``src/ai_jury/ballots.py``, with the second path listed in the scope as not
    in the change: the reviewer demonstrably read something a reader can go and
    check, and abstaining over the extra token would discard a real review to
    punish a typo. A line with *no* resolving token is not a review, whatever
    else it says.
    """
    resolved: list[str] = []
    unresolved: list[str] = []
    for token in _scope_tokens(value, changed):
        if _token_resolves(token, changed):
            resolved.append(token)
        elif _is_name_shaped(token, value):
            unresolved.append(token)
    return resolved, unresolved


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


def _stated_scope_sentence(stated: str, changed: Any) -> str:
    """The scope sentence for a reviewer's own ``Checked:`` line (pure).

    ``""`` when the line resolved to nothing in the change — the caller then has
    no sentence to add from this source, and the ballot falls through to the
    next one exactly as a reply with no ``Checked:`` line does. The unresolved
    tokens are not silently dropped: :func:`_no_scope_reason` names them in the
    abstention, where they cannot be mistaken for evidence.

    With ``changed`` as ``None`` there is nothing to resolve against — a
    hand-built outcome, a caller that never had a diff — and the pre-#710
    structural rule applies. "Not verifiable here" is not "does not exist".
    """
    if changed is None:
        return f"Checked, as stated by the reviewer: {_tick(stated)}."
    resolved, unresolved = resolve_stated_scope(stated, changed)
    if not resolved:
        return ""
    sentence = f"Checked, as stated by the reviewer: {', '.join(_tick(t) for t in resolved)}."
    if unresolved:
        listed = ", ".join(_deanchor(t) for t in unresolved[:_FILES_LISTED])
        sentence += (
            f" The same line also named {listed} — not in this change, and so"
            f" anchoring nothing; the rest of the line is what this ballot rests on."
        )
    return sentence


def describe_scope(result: Any, findings: list, *, mode: str = "code", changed: Any = None) -> str:
    """What this panelist named that it read — or ``""`` when it named nothing.

    Pure and deterministic. Four sources, most authoritative first:

    1. the reviewer's own ``Checked:`` line, **resolved against the change** —
       a token counts only when it names a path or a symbol that is actually in
       the diff (#710), so ``Checked: nothing`` contributes nothing;
    2. the distinct files it attached to its structured findings;
    3. up to three "checked / examined / reviewed" clauses from its prose;
    4. **in ``--issue`` mode only**, failing a file, the claims it raised.

    An empty return is the meaningful case and the reason this no longer emits a
    sentence unconditionally: with nothing from any of the four, the honest
    output is *nothing*, and the caller turns that into an abstention. The old
    fallback — "Reviewed the supplied diff; named no specific file." — asserted
    coverage from the absence of evidence for it, and read identically whether
    the agent had reviewed all 17 files or returned an empty string.

    ``changed`` is what the first source is resolved against (#710). Until then
    :func:`_tick` backticked whatever the reviewer wrote and
    :func:`scope_is_substantive` accepted any backticked token, so ``Checked:
    nothing`` was rendered as an anchor and the ballot counted as a review — the
    shape of naming something, satisfiable by an agent that read nothing. The
    ``Checked:`` path is now held to the standard the findings-derived path
    already met: it must name a place that exists in the change.

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
        sentence = _stated_scope_sentence(stated, changed)
        if sentence:
            parts.append(sentence)
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


def _named_only_absent(result: Any, changed: Any) -> bool:
    """Did this seat state a ``Checked:`` line that names only absent things? (pure)"""
    if changed is None:
        return False
    stated = _stated_line(result, _STATED_SCOPE_RE)
    if not stated:
        return False
    resolved, unresolved = resolve_stated_scope(stated, changed)
    return not resolved and bool(unresolved)


def abstention_cause(result: Any, changed: Any = None) -> str:
    """Which of :data:`ai_jury.panel.ABSTENTION_CAUSES` this seat's ballot records.

    **The** classification, and the only one: the ballot carries its answer under
    :data:`ai_jury.panel.CAUSE_FIELD`, the two abstention sentences below are
    written from it, and :func:`ai_jury.panel.abstention_buckets` counts by it. A
    second reading of the raw result is a second place for the count and the
    prose to part company, which is how a seat that named a file and then refused
    came to be counted as one that named nothing (#700, round 5).

    ``changed`` is the change under review, and it separates the last two
    causes: without it a seat that named only absent things is indistinguishable
    from one that named nothing, so the cause degrades to ``named_nothing``
    rather than being guessed.

    Silence is tested **first**, so ``silent`` keeps meaning exactly what
    :func:`ai_jury.panel.responded` says and the metadata's ``silent`` is the
    same number whether it is taken from the results or from the ballots. A seat
    that produced no output at all is silent even when its adapter also reported
    failure: "nothing came back" is the fact an operator acts on, and the adapter
    status is on the record beside it.

    Only ever asked of a ballot that did not review; a seat that reviewed has no
    cause, and :func:`reviewer_ballots` records an empty string for it.
    """
    if not responded(result):
        return SILENT
    if not getattr(result, "ok", False):
        return ADAPTER_FAILED
    if is_abstention(getattr(result, "output", "")):
        return REFUSED
    # The two shapes of "its scope did not stand", kept apart because they send
    # their reader to opposite places (#710). A seat whose ``Checked:`` line
    # named `src/made/up.py` did not fail to say what it read — it said it read
    # something this change does not contain, and "named nothing checkable"
    # printed over that ballot is a description its own scope contradicts.
    if _named_only_absent(result, changed):
        return NOT_IN_CHANGE
    return NAMED_NOTHING


#: The sentence each cause contributes to the scope of a ballot that could not
#: state one. Keyed by :func:`abstention_cause` so the reason and the bucket are
#: one classification; ``named_nothing`` is refined below by whether the seat
#: raised findings it attached to nothing.
_NO_SCOPE_REASONS = {
    SILENT: "it ran and returned nothing at all — an empty reply from the CLI",
    ADAPTER_FAILED: "its adapter reported failure and what came back named nothing",
    REFUSED: "it returned a refusal rather than a review",
}


def _no_scope_reason(result: Any, findings: list | None = None, changed: Any = None) -> str:
    """Why nothing checkable could be lifted from this seat (pure).

    Six reasons, kept apart because they ask for different fixes: a broken
    adapter, a CLI that answered with nothing at all, a refusal, a ``Checked:``
    line naming only things this change does not contain, a ``Checked:`` line
    naming nothing at all, a code review whose findings named no location, and a
    reply that reviewed nothing. The first three are :func:`abstention_cause`'s,
    read from the one classifier rather than re-tested here; the rest are the
    shapes of ``named_nothing`` and ``not_in_change``, and the splits matter
    because "said nothing" sends its reader somewhere different from "named a
    file that is not in the diff" and from "raised findings and attached none of
    them to a file".

    Every quoted token passes through :func:`_deanchor` first: this sentence
    lands in the ballot's ``scope``, and a scope explaining that nothing was
    checked must not itself read as an anchor to the consumer applying the same
    rule.
    """
    cause = abstention_cause(result, changed)
    stated = _NO_SCOPE_REASONS.get(cause)
    if stated:
        return stated
    named = _stated_line(result, _STATED_SCOPE_RE)
    if cause == NOT_IN_CHANGE:
        listed = ", ".join(_deanchor(t) for t in resolve_stated_scope(named, changed)[1])
        return (
            f"it stated it read {listed}, and no such path or symbol is in this change — "
            f"so the ballot names a place a reader cannot go to, which is not the same as "
            f"naming none"
        )
    if named:
        return (
            f"it stated it read {_deanchor(named)}, which names no path, line or symbol in "
            f"this change at all — the shape of a scope with nothing in it"
        )
    raised = len(findings or [])
    if raised:
        return (
            f"it raised {raised} finding(s) but attached none of them to a file, and its "
            f"reply named no file, symbol or coverage clause either, so the ballot points "
            f"at no place in the code a reader could go and check"
        )
    return "its reply named no file, symbol, coverage clause or finding"


def abstention_scope(result: Any, findings: list | None = None, changed: Any = None) -> str:
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
        f"{_no_scope_reason(result, findings, changed)}. Recorded as an abstention rather than an "
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


def sent_model(result: Any) -> str:
    """The model id this seat's invocation recorded having sent (``""`` if none).

    :attr:`ai_jury.adapters.AgentResult.model`, stamped by the path that ran the
    adapter from :meth:`ai_jury.adapters.Adapter.resolved_model` — the same call
    that put the id in the argv or the request payload. Reading it back is the
    whole of #709's fix: the ballot quotes the id the run sent instead of
    computing a second one that can differ from it.

    Empty for a record no invocation produced — a hand-built result, a chair
    slot with no round-1 seat — and :func:`requested_model` answers for those
    instead, under :data:`MODEL_RECOMPUTED` rather than :data:`MODEL_REQUESTED`.
    A stale result cache is deliberately *not* on that list: the record format
    gained this field, so :data:`ai_jury.cache.CACHE_SCHEMA` refuses an entry
    written without it rather than serving a recomputation in its place.
    """
    return (getattr(result, "model", "") or "").strip()


def requested_model(spec: Any) -> str:
    """The model id this agent's CLI would be asked for, from the spec alone.

    The fallback for a record that carries no sent id (:func:`sent_model`), and
    it computes the id the way the invocation path computes it — with
    :func:`ai_jury.config.spec_adapter`, **not** ``spec.vendor``. What it returns
    ships under :data:`MODEL_RECOMPUTED`: it is derived here, and the one thing
    it cannot be called is the id that was sent.

    That distinction is #709. ``spec.model`` is what the operator wrote down, but
    it is not always what is sent: where reasoning effort is encoded *in the
    model id*, the ``effort`` knob rewrites it. How effort is expressed is a
    property of the protocol the seat is invoked through, so every adapter keys
    :func:`ai_jury.adapters.effort_args` on the adapter; this keyed it on the
    vendor, and since #705 the two can differ. A seat configured
    ``vendor = google, adapter = cli, model = gemini-3-pro, effort = high`` was
    invoked with ``gemini-3-pro`` and balloted ``gemini-3-pro-high``, under a
    ``model_source: requested`` that claims to be the id actually sent.

    One thing this cannot see, and the reason the sent id is preferred over it:
    the invocation may consult the vendor's live model listing and fall back when
    the mapped id is not offered. That is I/O, and this module is pure.

    An effort level :func:`ai_jury.adapters.effort_args` rejects degrades to the
    configured id — a bad config value is ``validate_config``'s to refuse, never
    a ballot's to crash on.
    """
    from .adapters import effort_args
    from .config import spec_adapter

    configured = (getattr(spec, "model", "") or "").strip()
    try:
        plan = effort_args(spec_adapter(spec), getattr(spec, "effort", None), configured)
    except ValueError:
        return configured
    return (getattr(plan, "model", "") or configured or "").strip()


def describe_model(config: Any, agent_name: str, result: Any = None) -> tuple[str, str]:
    """``(model, source)`` for the agent that answered in this slot.

    ``result`` is that seat's own round-1 result when there is one, and it is
    the first source: it carries the id its invocation sent (#709), so
    ``model_source: "requested"`` names the string that was actually on the wire
    rather than a second derivation of it.

    Never the empty string for a slot that has an agent. An empty ``model`` was
    the provenance half of #700: a ballot naming ``vendor: "openai"`` and
    ``model: ""`` cannot answer "was that the same model as the other seat", and
    provenance is the entire product of a cross-vendor panel. Where the CLI was
    invoked with no id pinned there *is* an honest answer — the CLI's own
    default, which the CLI does not report back — so the field says that instead
    of going blank and letting a reader guess which of the two it meant.

    ``source`` is the same fact as one machine token, so a consumer can tell an
    id from a statement about one without parsing English.

    **A derived id is never labelled as a sent one** (#709, round 2). Where no
    invocation recorded an id — a hand-built result, a chair slot with no
    round-1 seat, a library caller — :func:`requested_model` still answers, and
    that answer goes out under :data:`MODEL_RECOMPUTED` rather than
    :data:`MODEL_REQUESTED`. It is a real id and worth quoting, but it is this
    module's arithmetic over the config, not a reading of the wire: for a Google
    seat at ``effort = high`` whose live model listing forced the adapter back to
    ``gemini-3-pro``, this returns ``gemini-3-pro-high``. Under ``requested``
    that is #709 restated — a model the run did not send, under a token whose
    whole claim is that it did.
    """
    name = (agent_name or "").strip()
    if not name:
        return "", MODEL_NONE
    spec = _spec_for(config, name)
    if spec is None:
        return f"unknown (no agent named '{name}' in this run's config)", MODEL_UNKNOWN
    sent = sent_model(result)
    if sent:
        return sent, MODEL_REQUESTED
    recomputed = requested_model(spec)
    if recomputed:
        return recomputed, MODEL_RECOMPUTED
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


#: The clause each cause contributes to a *scoped* abstention's sentence. Only
#: two causes can reach it: a seat whose scope stands named something, so it was
#: neither silent nor a reply that named nothing.
_SCOPED_ABSTENTION_REASONS = {
    ADAPTER_FAILED: "its adapter reported failure",
    REFUSED: "it returned a refusal rather than a review",
}


def _abstained_because(result: Any) -> str:
    """Why a seat that *did* name something checkable still abstained (pure).

    The companion to :func:`_no_scope_reason`, and deliberately not that
    function: this ballot's scope is substantive, so every reason phrased around
    "it named nothing" would be false of it. Only two of :func:`_verdict_for`'s
    three gates can fire while the scope stands — a failed adapter and a refusal
    — and the other two causes never reach here, because a seat with no scope
    gets :func:`abstention_scope` instead. Read from
    :func:`abstention_cause` all the same, so this sentence and the bucket the
    same seat is counted in cannot name two different things.
    """
    return _SCOPED_ABSTENTION_REASONS.get(abstention_cause(result), "it cast no vote")


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
    # What the panel was actually shown (#710), so a reviewer's `Checked:` line
    # is resolved against the change rather than accepted on its shape. ``None``
    # on an outcome that was not built from a diff; the rule then falls back to
    # the structural test, because "not verifiable" is not "does not exist".
    #
    # ``--issue`` is the one mode that supplies no change to resolve against: the
    # panel is reading an issue's prose, and a reviewer naming "the acceptance
    # criteria section" has named exactly what it was asked to name. That is the
    # same exception :func:`_claims_named` documents — there a finding carries no
    # file by construction — and applying a diff rule to a document with no diff
    # would abstain over a clean triage that did its job.
    changed = None if mode == ISSUE_MODE else getattr(outcome, "changed", None)
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
        scope = describe_scope(r, own_findings, mode=mode, changed=changed)
        scoped = bool(scope)
        if not scoped:
            scope = abstention_scope(r, own_findings, changed)
        model, model_source = describe_model(config, name, r)
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
        # And, when the answer is no, *why* — the fact a reader of the record
        # alone cannot recover, because a seat that fell silent and one that
        # answered without naming anything leave the same two fields behind. It
        # travels with the ballot so every renderer counts and describes the same
        # seat the same way instead of subtracting one bucket from another
        # (#700, round 5). Empty on a ballot that reviewed: it has no cause.
        entry[CAUSE_FIELD] = "" if entry["counts_as_review"] else abstention_cause(r, changed)
        # After the answer, never before it: the chair's sentence *reports* that
        # answer, so it cannot be written while the answer is still unknown.
        if scoped and chaired:
            entry["scope"] += _chaired_ballot_sentence(r, entry)
        entries.append(entry)

    # The chair's own round-1 seat, when it has one: the chairing agent reviews
    # too, and every phase of it runs through the same adapter, so the id its
    # ballot recorded is the id its synthesis was produced with.
    chair_result = next((r for r in seats if getattr(r, "agent", "") == chair_name), None)
    chair_model, chair_model_source = describe_model(config, chair_name, chair_result)
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
            # Empty, and present rather than absent: the chair is not a seat that
            # failed to review, it is the record carried alongside the seats, and
            # a key missing here would read as a cause nobody wrote down.
            CAUSE_FIELD: "",
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
