"""Optional repository review policy.

The review *policy* is distinct from the agent-runtime ``jury.toml`` handled
by :mod:`ai_jury.config`. A policy file is authored and committed
by the maintainers of the repository being reviewed and expresses
project-specific review expectations (high-risk paths, focus areas, forbidden
output behaviour, severity overrides, a free-form checklist, and links to docs
reviewers should consider).

Because the policy is maintainer-authored it is treated as **trusted** content
and is rendered into the review prompt in a clearly separated section, distinct
from the untrusted diff/context fences.

Policy files are entirely optional: when none is found, loaders return ``None``
and the pipeline proceeds with an empty policy section. Only a *malformed*
policy file raises an error, so a typo is surfaced loudly rather than silently
ignored.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .redaction import redact

# Standard discovery locations, searched in order when no explicit path is given.
DEFAULT_POLICY_NAMES = (".jury/policy.toml", "jury-policy.toml")

# Upper bound on a policy TOML file (issue #316/L-5); a real policy is a few KB.
_MAX_POLICY_BYTES = 4 * 1024 * 1024


class PolicyError(Exception):
    """Raised when a policy file exists but cannot be parsed or is malformed."""


@dataclass
class SeverityOverride:
    """Override the severity for findings touching paths matching ``glob``."""

    glob: str
    severity: str


@dataclass
class ReviewPolicy:
    """A repository's review policy.

    Every field is optional; an empty policy is a valid (if pointless) policy.
    """

    high_risk_paths: list[str] = field(default_factory=list)
    focus_areas: list[str] = field(default_factory=list)
    forbidden_output: list[str] = field(default_factory=list)
    severity_overrides: list[SeverityOverride] = field(default_factory=list)
    checklist: str = ""
    doc_links: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Return True when the policy carries no actionable content."""
        return not (
            self.high_risk_paths
            or self.focus_areas
            or self.forbidden_output
            or self.severity_overrides
            or self.checklist.strip()
            or self.doc_links
        )


SENTINEL = "_(no repository policy configured)_"


def load_policy(path: Path | None = None) -> ReviewPolicy | None:
    """Load a repository review policy from a TOML file.

    When ``path`` is given it must exist; a missing explicit path is treated as
    a malformed configuration and raises :class:`PolicyError`. When ``path`` is
    ``None`` the standard discovery locations in :data:`DEFAULT_POLICY_NAMES`
    are searched in the current working directory.

    Returns ``None`` when no policy file is present (the common case). Raises
    :class:`PolicyError` when a file is found but cannot be parsed or has the
    wrong shape.
    """
    policy_path = _resolve_path(path)
    if policy_path is None:
        return None

    try:
        # Size-cap the read (issue #316/L-5): a real policy is a few KB; refuse a
        # multi-MB / pathological file rather than feed it whole to tomllib.
        with policy_path.open("rb") as handle:
            raw = handle.read(_MAX_POLICY_BYTES + 1)
        if len(raw) > _MAX_POLICY_BYTES:
            raise PolicyError(
                f"policy file {policy_path} exceeds the {_MAX_POLICY_BYTES}-byte limit."
            )
        data = tomllib.loads(raw.decode("utf-8"))
    except OSError as exc:
        raise PolicyError(f"could not read policy file {policy_path}: {redact(str(exc))[0]}") from None
    except UnicodeDecodeError as exc:
        # TOML is UTF-8 by spec (review of #316).
        raise PolicyError(f"policy file {policy_path} is not valid UTF-8.") from None
    except tomllib.TOMLDecodeError as exc:
        raise PolicyError(f"invalid TOML in policy file {policy_path}: {redact(str(exc))[0]}") from None

    return _from_dict(data, source=policy_path)


def _resolve_path(path: Path | None) -> Path | None:
    """Return an explicit policy path, or discover one, or ``None``."""
    if path is not None:
        path = Path(path)
        if not path.exists():
            raise PolicyError(f"policy file not found: {path}")
        return path
    for name in DEFAULT_POLICY_NAMES:
        candidate = Path(name)
        if candidate.exists():
            return candidate
    return None


def _from_dict(data: dict, *, source: Path | None = None) -> ReviewPolicy:
    """Build a :class:`ReviewPolicy` from a parsed TOML mapping."""
    where = f" in {source}" if source is not None else ""

    def str_list(key: str) -> list[str]:
        value = data.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise PolicyError(f"'{key}' must be a list of strings{where}")
        return list(value)

    checklist = data.get("checklist", "")
    if not isinstance(checklist, str):
        raise PolicyError(f"'checklist' must be a string{where}")

    overrides_raw = data.get("severity_overrides", [])
    if not isinstance(overrides_raw, list):
        raise PolicyError(f"'severity_overrides' must be a list of tables{where}")
    overrides: list[SeverityOverride] = []
    for entry in overrides_raw:
        if not isinstance(entry, dict):
            raise PolicyError(f"each severity override must be a table{where}")
        glob = entry.get("glob")
        severity = entry.get("severity")
        if not isinstance(glob, str) or not isinstance(severity, str):
            raise PolicyError(f"each severity override needs string 'glob' and 'severity'{where}")
        overrides.append(SeverityOverride(glob=glob, severity=severity))

    return ReviewPolicy(
        high_risk_paths=str_list("high_risk_paths"),
        focus_areas=str_list("focus_areas"),
        forbidden_output=str_list("forbidden_output"),
        severity_overrides=overrides,
        checklist=checklist,
        doc_links=str_list("doc_links"),
    )


def render_policy_section(policy: ReviewPolicy | None) -> str:
    """Render a policy as a trusted, human/agent-readable Markdown block.

    Returns :data:`SENTINEL` when there is no (effective) policy so the review
    prompt remains stable and self-explanatory.
    """
    if policy is None or policy.is_empty():
        return SENTINEL

    lines: list[str] = []

    def bullet_list(title: str, items: list[str]) -> None:
        if not items:
            return
        lines.append(f"**{title}:**")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")

    bullet_list("High-risk paths (review with extra care)", policy.high_risk_paths)
    bullet_list("Required review focus areas", policy.focus_areas)
    bullet_list("Forbidden output behaviour", policy.forbidden_output)

    if policy.severity_overrides:
        lines.append("**Severity overrides by path pattern:**")
        for override in policy.severity_overrides:
            lines.append(f"- `{override.glob}` -> {override.severity}")
        lines.append("")

    if policy.checklist.strip():
        lines.append("**Project review checklist:**")
        lines.append(policy.checklist.strip())
        lines.append("")

    bullet_list("Reference docs to consider", policy.doc_links)

    return "\n".join(lines).rstrip()
