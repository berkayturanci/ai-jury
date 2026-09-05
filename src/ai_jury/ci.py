"""Severity-gated CI exit policy (issue #4).

A pure decision function over the consensus groups: given the configured blocking
severities and how to treat unverified findings, decide a process exit code.
"""

from __future__ import annotations

from .findings import SEVERITY_INPUTS, canonical_severity, flatten_inline


def resolve_fail_on(fail_on) -> tuple[set[str], list[str]]:
    """Split a configured fail-on list into canonical severities and unknowns.

    Blank entries are dropped. Recognised entries come back canonicalised — so
    the documented ``blocker`` alias matches ``critical`` groups instead of
    silently never firing — and anything outside the vocabulary is returned, in
    the spelling the operator wrote, for a caller to refuse.
    """
    known: set[str] = set()
    unknown: list[str] = []
    for entry in fail_on or []:
        text = str(entry).strip()
        if not text:
            continue
        severity = canonical_severity(text)
        if severity is None:
            unknown.append(text)
        else:
            known.add(severity)
    return known, unknown


def _vocabulary_error(unknown: list[str], where: str) -> str:
    return (
        f"{where} contains unknown severities {unknown!r} "
        f"(expected one of {', '.join(SEVERITY_INPUTS)})."
    )


def fail_on_error(fail_on, where: str) -> str | None:
    """Message naming every unrecognised severity in ``fail_on``, else ``None``.

    Shared by ``validate_config`` and the ``--fail-on`` flag so a typo is
    reported the same way whichever surface it was written on (issue #718).
    """
    _, unknown = resolve_fail_on(fail_on)
    return _vocabulary_error(unknown, where) if unknown else None


def evaluate_ci(groups_with_status, fail_on, ignore_unverified: bool) -> tuple[int, str]:
    """Decide a CI exit code from consensus groups.

    Returns ``(exit_code, reason)``.

    A group fails CI when its severity is in ``fail_on`` AND it is either
    verified (status == "verified") or, when ``ignore_unverified`` is False, has
    any non-"unsupported" status. Findings the verifier marked "unsupported"
    never fail CI. When ``ignore_unverified`` is True, groups that were never
    verified (empty status) do not fail CI; only explicitly verified ones can.

    Raises ``ValueError`` when ``fail_on`` names a severity outside the
    vocabulary. A misspelling matches no group, so treating it as "blocks
    nothing" would report a green PASS quoting the typo and disable the gate
    forever; the CLI and ``validate_config`` refuse one first, and this guard
    keeps a caller that bypasses both from reopening the hole (issue #718).
    """
    fail_set, unknown = resolve_fail_on(fail_on)
    if unknown:
        raise ValueError(_vocabulary_error(unknown, "fail_on"))
    blocking = []
    for g in groups_with_status:
        severity = getattr(g, "severity", "")
        status = getattr(g, "status", "") or ""
        if severity not in fail_set:
            continue
        if status == "unsupported":
            continue
        if ignore_unverified and status != "verified":
            continue
        blocking.append(g)

    if blocking:
        bits = []
        for g in blocking:
            rep = getattr(g, "representative", None)
            loc = ""
            if rep is not None and getattr(rep, "file", None):
                loc = flatten_inline(rep.file)
                if getattr(rep, "line", None) is not None:
                    loc += f":{rep.line}"
            # Flatten the attacker-influenced file/claim: this reason line is
            # posted to the PR as the CI-gate section, so a multi-line claim could
            # otherwise forge a heading/marker in the comment (audit 2026-06-13
            # r7/M). This stays a pure function.
            claim = flatten_inline(getattr(rep, "claim", "")) if rep is not None else ""
            bits.append(f"[{g.severity}] {loc or '(no location)'} {claim}".strip())
        reason = (
            f"FAIL: {len(blocking)} blocking finding(s) at severities "
            f"{sorted(fail_set)}: " + "; ".join(bits)
        )
        return 1, reason

    reason = (
        f"PASS: no blocking findings at severities {sorted(fail_set)} "
        f"(ignore_unverified={ignore_unverified})."
    )
    return 0, reason
