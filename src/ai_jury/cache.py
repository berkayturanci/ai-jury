"""Optional local result cache for repeated jury runs (issue #33).

Re-running the jury against an unchanged diff with an unchanged config
re-spends time and tokens for an identical result. This module adds an opt-in,
on-disk cache keyed by everything that can change the outcome: the diff, the
effective config hash, the prompt-template version, the package version, the
context policy, and the run seed.

Privacy note: a cache entry stores the full structured outcome — including agent
review/debate/synthesis text, which is derived from the diff. Treat the cache
directory as sensitive (same trust level as the diff itself). The cache is OFF
by default and only writes when explicitly enabled with ``--cache``; clear it
with ``--clear-cache`` (or ``jury cache clear``).
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

from . import __version__, prompts
from .adapters import AgentResult
from .config import JuryConfig, config_hash
from .consensus import FindingGroup
from .findings import Finding, Verdict
from .injection import InjectionHit
from .orchestrator import JuryOutcome

CACHE_SCHEMA = 1
_ENV_DIR = "JURY_CACHE_DIR"


def default_cache_dir() -> Path:
    """Cache directory: ``$JURY_CACHE_DIR`` or ``~/.cache/ai-jury``."""
    override = os.environ.get(_ENV_DIR)
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "ai-jury"


def cache_key(
    config: JuryConfig, diff: str, *, seed: int | None = None, mock: bool = False
) -> str:
    """Stable cache key for a run.

    A pure function of the inputs that determine the outcome. The seed is part of
    the key (it changes randomized orchestration), unlike in ``config_hash``
    which describes configuration independent of seed. ``mock`` is included so a
    ``--mock`` run (deterministic canned findings) can NEVER be served as a real
    review for the same diff+config, and vice versa.
    """
    payload = {
        "cache_schema": CACHE_SCHEMA,
        "package_version": __version__,
        "prompt_version": prompts.PROMPT_VERSION,
        "config_hash": config_hash(config),
        "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        "context_mode": config.context.mode,
        "redact_secrets": config.context.redact_secrets,
        "verify": config.verify,
        "seed": seed if seed is not None else config.seed,
        "mock": bool(mock),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _finding(d: dict) -> Finding:
    return Finding(
        severity=d.get("severity", "info"),
        file=d.get("file", ""),
        claim=d.get("claim", ""),
        line=d.get("line"),
        evidence=d.get("evidence", ""),
        suggested_fix=d.get("suggested_fix", ""),
        confidence=d.get("confidence", "medium"),
        reviewer=d.get("reviewer", ""),
    )


def _verdict(d: dict) -> Verdict:
    return Verdict(
        file=d.get("file"),
        line=d.get("line"),
        claim=d.get("claim", ""),
        status=d.get("status", "needs_human_decision"),
        reasoning=d.get("reasoning", ""),
    )


def _agent_result(d: dict | None) -> AgentResult | None:
    if d is None:
        return None
    return AgentResult(
        agent=d["agent"],
        vendor=d["vendor"],
        ok=d["ok"],
        output=d["output"],
        duration_s=d["duration_s"],
        error=d.get("error"),
        findings=[_finding(f) for f in d.get("findings", [])],
        warnings=list(d.get("warnings", [])),
        error_code=d.get("error_code"),
        attempts=d.get("attempts", 1),
    )


def _group(d: dict) -> FindingGroup:
    return FindingGroup(
        representative=_finding(d["representative"]),
        reviewers=list(d.get("reviewers", [])),
        severity=d.get("severity", "info"),
        members=[_finding(m) for m in d.get("members", [])],
        bucket=d.get("bucket", "single_reviewer"),
        status=d.get("status", ""),
        status_reasoning=d.get("status_reasoning", ""),
    )


def _hit(d: dict) -> InjectionHit:
    return InjectionHit(
        kind=d.get("kind", ""),
        source=d.get("source", ""),
        line=d.get("line"),
        snippet=d.get("snippet", ""),
    )


def outcome_to_dict(outcome: JuryOutcome) -> dict:
    """Serialize a JuryOutcome to a JSON-safe dict (dataclasses all the way down)."""
    return asdict(outcome)


def outcome_from_dict(data: dict) -> JuryOutcome:
    """Rebuild a JuryOutcome from :func:`outcome_to_dict` output."""
    return JuryOutcome(
        reviews=[_agent_result(r) for r in data.get("reviews", [])],
        debate=[_agent_result(r) for r in data.get("debate", [])],
        synthesis=_agent_result(data.get("synthesis")),
        chair=data.get("chair", ""),
        findings=[_finding(f) for f in data.get("findings", [])],
        warnings=list(data.get("warnings", [])),
        groups=[_group(g) for g in data.get("groups", [])],
        verify=_agent_result(data.get("verify")),
        verdicts=[_verdict(v) for v in data.get("verdicts", [])],
        context_mode=data.get("context_mode", "diff-only"),
        redact_secrets=data.get("redact_secrets", True),
        redaction_count=data.get("redaction_count", 0),
        injection_hits=[_hit(h) for h in data.get("injection_hits", [])],
        skipped=[tuple(s) for s in data.get("skipped", [])],
        budget_exhausted=data.get("budget_exhausted", False),
        rounds_executed=data.get("rounds_executed", 1),
        stop_reason=data.get("stop_reason", ""),
        from_cache=data.get("from_cache", False),
    )


class Cache:
    """A simple on-disk JSON cache of jury outcomes."""

    def __init__(self, directory: Path | str | None = None):
        self.dir = Path(directory) if directory else default_cache_dir()

    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def load(self, key: str) -> JuryOutcome | None:
        """Return the cached outcome for ``key`` (marked ``from_cache``), or None.

        A corrupt or unreadable entry is treated as a miss rather than an error,
        so a bad cache file never breaks a run.
        """
        path = self._path(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if data.get("cache_schema") != CACHE_SCHEMA:
            return None
        outcome = outcome_from_dict(data.get("outcome", {}))
        outcome.from_cache = True
        return outcome

    def store(self, key: str, outcome: JuryOutcome) -> None:
        """Persist ``outcome`` under ``key`` (best-effort; ignores write errors)."""
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            payload = {"cache_schema": CACHE_SCHEMA, "outcome": outcome_to_dict(outcome)}
            self._path(key).write_text(
                json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8"
            )
        except OSError:
            pass

    def clear(self) -> int:
        """Remove all cache entries; return the number deleted."""
        if not self.dir.exists():
            return 0
        removed = 0
        for path in self.dir.glob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        return removed
