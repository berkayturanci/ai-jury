"""Severity-gated CI exit policy (issue #4).

A pure decision function over the consensus groups: given the configured blocking
severities and how to treat unverified findings, decide a process exit code.
"""
from __future__ import annotations


def evaluate_ci(groups_with_status, fail_on, ignore_unverified: bool) -> tuple[int, str]:
    """Decide a CI exit code from consensus groups.

    Returns ``(exit_code, reason)``.

    A group fails CI when its severity is in ``fail_on`` AND it is either
    verified (status == "verified") or, when ``ignore_unverified`` is False, has
    any non-"unsupported" status. Findings the verifier marked "unsupported"
    never fail CI. When ``ignore_unverified`` is True, groups that were never
    verified (empty status) do not fail CI; only explicitly verified ones can.
    """
    fail_set = {str(s).strip().lower() for s in (fail_on or [])}
    # `blocker` is a documented alias for `critical` (group severities are only
    # ever critical/major/minor/nit/info), so a `--fail-on blocker` gate must
    # match `critical` groups instead of silently never firing.
    if "blocker" in fail_set:
        fail_set.add("critical")
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
                loc = rep.file
                if getattr(rep, "line", None) is not None:
                    loc += f":{rep.line}"
            claim = getattr(rep, "claim", "") if rep is not None else ""
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
