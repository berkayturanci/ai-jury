"""Machine-readable renderers for the jury outcome.

Markdown rendering lives in :mod:`ai_jury.report`. This module
adds structured outputs intended for tooling:

* :func:`to_json` -- a structured JSON report (schema documented in the README).
* :func:`to_sarif` -- a SARIF 2.1.0 document for CI / code scanning upload.

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
JSON_SCHEMA_VERSION = "1.0"

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


def to_json(outcome: Any, config: Any) -> str:
    """Render the jury outcome as a structured, pretty-printed JSON report.

    Top-level keys: ``schema_version``, ``metadata`` (from
    :func:`build_run_metadata`), ``findings``, ``consensus``, ``verdicts`` and
    ``verdict`` (the chair synthesis text, if any). The result is deterministic
    for a deterministic outcome and contains only legitimate finding fields.
    """
    synthesis = getattr(outcome, "synthesis", None)
    verdict_text = ""
    if synthesis is not None and getattr(synthesis, "ok", False):
        verdict_text = (synthesis.output or "").strip()

    # Drop the wall-clock timestamp so the report is deterministic for a
    # deterministic run (matching report.py, which omits generated_at too).
    metadata = build_run_metadata(outcome, config)
    metadata.pop("generated_at", None)

    # Surface the deterministic PR-level classification (issue #7) at the top
    # level for easy machine consumption (it is also embedded in ``metadata``).
    from .classification import classify

    doc: dict[str, Any] = {
        "schema_version": JSON_SCHEMA_VERSION,
        "metadata": metadata,
        "classification": classify(outcome),
        "findings": [_finding_dict(f) for f in outcome.findings],
        "consensus": [_group_dict(g) for g in outcome.groups],
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


def _sarif_result(f: Finding) -> dict[str, Any]:
    """Map a finding to a SARIF result object."""
    physical: dict[str, Any] = {"artifactLocation": {"uri": f.file}}
    if f.line is not None:
        physical["region"] = {"startLine": f.line}
    return {
        "ruleId": f"jury/{f.severity}",
        "level": severity_to_sarif_level(f.severity),
        "message": {"text": f.claim},
        "locations": [{"physicalLocation": physical}],
    }


def to_sarif(outcome: Any, config: Any) -> str:
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
            "shortDescription": {
                "text": f"{sev} finding reported by the review jury"
            },
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
