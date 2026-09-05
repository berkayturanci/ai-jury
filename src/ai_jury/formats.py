"""Machine-readable renderers for the jury outcome.

Markdown rendering lives in :mod:`ai_jury.report`. This module
adds structured outputs intended for tooling:

* :func:`to_json` -- a structured JSON report (schema documented in the README).
* :func:`to_sarif` -- a SARIF 2.1.0 document for CI / code scanning upload.
* :func:`to_keel_reviews` -- a per-reviewer review bundle (issue #663) for a
  consumer that renders one head-pinned verdict per panelist.

Both renderers are deterministic for a deterministic outcome (e.g. under
``mock=True``) and only emit legitimate finding fields -- never raw diff or
prompt text.
"""

from __future__ import annotations

import json
from typing import Any

from . import __version__
from .findings import SEVERITIES, Finding
from .metadata import build_run_metadata

#: Version of the JSON report schema produced by :func:`to_json`.
#:
#: 1.1 (issue #663) ADDED the top-level ``reviewers`` array. Nothing was removed,
#: renamed or reshaped, so every existing consumer of ``findings``/``consensus``/
#: ``verdicts``/``verdict``/``metadata`` reads an identical document.
#:
#: 1.2 (issue #700) adds ``scope``, ``testing`` and ``model_source`` to each
#: ``reviewers`` entry — and, the reason this is a version bump rather than a
#: silent addition, CHANGES what ``reviewers[].model`` means. It was "the
#: configured model id, or ``""`` when the CLI's default is in force"; it is now
#: never empty for a slot that has an agent, carrying either the id actually
#: requested or a statement that the CLI chose and does not report which. A
#: consumer testing ``model == ""`` to detect the default case must read
#: ``model_source == "cli_default"`` instead. Every top-level key, and every
#: other ``reviewers`` key, is unchanged.
JSON_SCHEMA_VERSION = "1.2"

#: Canonical SARIF schema URI and version emitted by :func:`to_sarif`.
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
SARIF_VERSION = "2.1.0"

TOOL_NAME = "ai-jury"
TOOL_URI = "https://github.com/berkayturanci/ai-jury"

#: Mapping from finding severity to SARIF result level.
_SARIF_LEVEL = {
    "critical": "error",
    "major": "error",
    "minor": "warning",
    "nit": "note",
    "info": "note",
}


def severity_to_sarif_level(severity: str) -> str:
    """Map a jury severity to a SARIF result ``level``.

    critical/major -> ``error``, minor -> ``warning``, nit/info -> ``note``.
    Unknown severities fall back to ``note``.
    """
    return _SARIF_LEVEL.get(severity, "note")


def _finding_dict(f: Finding) -> dict[str, Any]:
    """Serialise a finding to a stable, ordered dict of legitimate fields."""
    return {
        "severity": f.severity,
        "file": f.file,
        "line": f.line,
        "claim": f.claim,
        "evidence": f.evidence,
        "suggested_fix": f.suggested_fix,
        "confidence": f.confidence,
        "reviewer": f.reviewer,
    }


def _group_dict(g: Any) -> dict[str, Any]:
    """Serialise a consensus group to a stable, ordered dict."""
    return {
        "representative": _finding_dict(g.representative),
        "agreement": len(g.reviewers),
        "reviewers": list(g.reviewers),
        "bucket": g.bucket,
        "verification_status": g.status or None,
    }


def to_json(outcome: Any, config: Any, *, decision=None, vote=None, mode: str = "code") -> str:
    """Render the jury outcome as a structured, pretty-printed JSON report.

    Top-level keys: ``schema_version``, ``metadata`` (from
    :func:`build_run_metadata`), ``findings``, ``consensus``, ``reviewers``,
    ``verdicts`` and ``verdict`` (the chair synthesis text, if any). The result is
    deterministic for a deterministic outcome and contains only legitimate finding
    fields.

    ``decision``/``vote`` are threaded into the metadata so the JSON report
    reflects an effective ``--decision vote`` override (issue #248); when omitted
    the metadata falls back to ``config.decision`` as before. ``mode`` selects the
    ballot vocabulary (``code`` → APPROVE/COMMENT/REQUEST_CHANGES, ``issue`` →
    READY/UNCLEAR/NEEDS_INFO), matching ``--issue``.
    """
    synthesis = getattr(outcome, "synthesis", None)
    verdict_text = ""
    if synthesis is not None and getattr(synthesis, "ok", False):
        verdict_text = (synthesis.output or "").strip()

    # Drop the wall-clock timestamp so the report is deterministic for a
    # deterministic run (matching report.py, which omits generated_at too).
    metadata = build_run_metadata(outcome, config, decision=decision, vote=vote)
    metadata.pop("generated_at", None)

    # Surface the deterministic PR-level classification (issue #7) at the top
    # level for easy machine consumption (it is also embedded in ``metadata``).
    from .ballots import reviewer_ballots
    from .classification import classify

    doc: dict[str, Any] = {
        "schema_version": JSON_SCHEMA_VERSION,
        "metadata": metadata,
        "classification": classify(outcome),
        "findings": [_finding_dict(f) for f in outcome.findings],
        "consensus": [_group_dict(g) for g in outcome.groups],
        # Per-reviewer ballots (issue #663): who said what, with vendor/model
        # provenance. Purely additive — every key above is unchanged.
        "reviewers": reviewer_ballots(outcome, config, vote=vote, mode=mode),
        "verdicts": [
            {
                "file": v.file,
                "line": v.line,
                "claim": v.claim,
                "status": v.status,
                "reasoning": v.reasoning,
            }
            for v in outcome.verdicts
        ],
        "verdict": verdict_text,
    }
    return json.dumps(doc, indent=2, sort_keys=False, ensure_ascii=False)


def to_keel_reviews(outcome: Any, config: Any, *, vote=None, mode: str = "code") -> str:
    """Render the panel as a per-reviewer review bundle (issue #663).

    A JSON **array** — not an object — of
    ``{reviewer, verdict, scope, findings, testing, vendor, model}`` records, one
    per panelist that returned output plus the chair as ``reviewer: "chair"``.
    That is the payload keel's ``keel review --reviews <file>`` accepts, so a
    panel run can *be* the review rather than merely inform one.

    Deterministic for a deterministic outcome. Free text lifted from agent replies
    (``scope``, ``testing``) is flattened and capped in :mod:`ai_jury.ballots`; no
    diff text, prompt text or secret ever reaches this document.
    """
    from .ballots import keel_reviews

    return json.dumps(
        keel_reviews(outcome, config, vote=vote, mode=mode),
        indent=2,
        sort_keys=False,
        ensure_ascii=False,
    )


def _sarif_result(f: Finding) -> dict[str, Any]:
    """Map a finding to a SARIF result object."""
    physical: dict[str, Any] = {"artifactLocation": {"uri": f.file or ""}}
    # SARIF 2.1.0 requires ``region.startLine`` to be a positive (1-based)
    # integer. A reviewer's structured output is attacker-influenced (a finding's
    # ``line`` is parsed from agent JSON that can be steered by the diff), so a
    # forged ``"line": 0`` / negative value would emit an invalid region and make
    # GitHub code-scanning reject the WHOLE SARIF upload — suppressing every
    # finding (denial-of-evidence). Drop the region in that case so the finding
    # still surfaces at file level (security audit 2026-06-13 r5).
    if f.line is not None and f.line >= 1:
        physical["region"] = {"startLine": f.line}
    return {
        "ruleId": f"jury/{f.severity}",
        "level": severity_to_sarif_level(f.severity),
        "message": {"text": f.claim},
        "locations": [{"physicalLocation": physical}],
    }


def to_sarif(outcome: Any, _config: Any) -> str:
    """Render the jury outcome as a SARIF 2.1.0 document.

    Consensus group representatives are preferred as the source of results; if
    there are no groups the raw findings are used. Rules are derived from the
    severities actually present. The output is deterministic.
    """
    if outcome.groups:
        findings = [g.representative for g in outcome.groups]
    else:
        findings = list(outcome.findings)

    # Rules: one per severity actually used, in canonical severity order.
    used = {f.severity for f in findings}
    rules = [
        {
            "id": f"jury/{sev}",
            "name": f"jury-{sev}",
            "shortDescription": {"text": f"{sev} finding reported by the review jury"},
            "defaultConfiguration": {"level": severity_to_sarif_level(sev)},
        }
        for sev in SEVERITIES
        if sev in used
    ]

    doc = {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": TOOL_NAME,
                        "informationUri": TOOL_URI,
                        "version": __version__,
                        "rules": rules,
                    }
                },
                "results": [_sarif_result(f) for f in findings],
            }
        ],
    }
    return json.dumps(doc, indent=2, sort_keys=False, ensure_ascii=False)
