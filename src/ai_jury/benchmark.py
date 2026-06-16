"""Offline benchmark for jury review quality (issue #12).

Measures, in a small and *directional* way, whether a jury's structured
findings line up with hand-authored expectations for a set of fixture diffs.

Design (honesty matters here)
-----------------------------
The :class:`~ai_jury.adapters.MockAdapter` emits a *fixed* canned
finding regardless of the diff it is given, so ``--mock`` output does **not**
reflect a fixture's content. Running ``--mock`` per fixture and scoring it would
be fake signal. We therefore separate two concerns:

* A **scorer** (:func:`score_fixture`) that compares an arbitrary list of
  finding dicts against a fixture's ``expected`` spec. This is pure and
  deterministic.
* A **finding source**:
  - *Offline / CI default ("recorded" mode):* every fixture ships a
    hand-authored ``<id>.expected.json`` AND a recorded ``<id>.findings.json``
    (a realistic sample of what a jury produced for that diff). The offline
    benchmark scores recorded -> expected. It validates the SCORER + FIXTURES
    and provides recorded baselines. It runs with no live CLIs and no network.
  - *Optional live mode:* gated behind ``JURY_BENCH_LIVE=1``. It runs the
    real jury (``run_jury(..., mock=False)``) per fixture diff and scores
    the live findings. OFF by default; never in CI.

What this benchmark does and does NOT claim
-------------------------------------------
It is small and directional. The offline run does not measure live review
quality; it validates the scorer and the recorded baselines. True quality
measurement requires live mode against real agent CLIs. See ``benchmark/README.md``.

This module is stdlib-only and has no import-time dependency on the live
adapters; ``run_live`` imports the orchestrator lazily.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# Severity ranking, kept local so this module does not require the rest of the
# package at import time (the orchestrator is only imported lazily for live mode).
SEVERITIES: tuple[str, ...] = ("critical", "major", "minor", "nit", "info")
_SEVERITY_RANK: dict[str, int] = {sev: i for i, sev in enumerate(SEVERITIES)}

# Severities that count as "blocking" for the must_not_flag / max_blocking
# checks. Mirrors the default CI ``fail_on`` set (critical, major).
BLOCKING_SEVERITIES: frozenset[str] = frozenset({"critical", "major"})

# Default line tolerance for a positional match. A finding at line L matches an
# expected line E when ``abs(L - E) <= LINE_TOLERANCE``. Diffs shift line
# numbers slightly between reviewers, so an exact match is too strict.
LINE_TOLERANCE = 3

#: Directory holding the shipped fixtures (``benchmark/`` at the repo root).
BENCHMARK_DIR = Path(__file__).resolve().parents[2] / "benchmark"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Fixture:
    """A parsed benchmark fixture: a diff plus its expected/recorded data."""

    id: str
    description: str
    diff: str
    expected: dict
    recorded: list[dict] = field(default_factory=list)


@dataclass
class FixtureScore:
    """The scored result of one fixture."""

    id: str
    passed: bool
    matched: int
    missed: int
    false_positives: int
    expected_count: int
    precision: float
    recall: float
    reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Match rule
# ---------------------------------------------------------------------------
def _severity_rank(sev: object) -> int:
    if isinstance(sev, str):
        return _SEVERITY_RANK.get(sev.strip().lower(), len(SEVERITIES) - 1)
    return len(SEVERITIES) - 1


def _is_blocking(finding: dict) -> bool:
    sev = finding.get("severity")
    return isinstance(sev, str) and sev.strip().lower() in BLOCKING_SEVERITIES


def _line_matches(finding_line: object, expected_line: object, tol: int) -> bool:
    """A finding line matches the expected line when within +/- ``tol``.

    When the expected entry has no line, any finding line matches (the entry is
    file/keyword scoped). When the finding has no line but the expected entry
    does, it cannot positionally match.
    """
    if expected_line is None:
        return True
    if finding_line is None:
        return False
    try:
        return abs(int(finding_line) - int(expected_line)) <= tol
    except (TypeError, ValueError):
        return False


def _keywords_match(finding: dict, keywords: list) -> bool:
    """At least one keyword (case-insensitive) appears in claim or evidence.

    An empty/absent keyword list is treated as "no keyword constraint" -> match.
    """
    if not keywords:
        return True
    haystack = (str(finding.get("claim", "")) + " " + str(finding.get("evidence", ""))).lower()
    return any(str(kw).lower() in haystack for kw in keywords)


def finding_matches_expected(finding: dict, entry: dict, tol: int = LINE_TOLERANCE) -> bool:
    """Return True when ``finding`` satisfies a ``must_match`` ``entry``.

    Match rule (all conditions must hold):

    * **file**: same file path (exact string match) when the entry specifies a
      ``file``; if the entry omits ``file`` the file is not constrained.
    * **line**: the finding's line is within ``+/- tol`` of the entry's ``line``
      (default :data:`LINE_TOLERANCE`); an entry without a ``line`` is not
      positionally constrained.
    * **severity**: the finding is at least as severe as the entry's
      ``severity`` (e.g. an expected ``major`` is satisfied by ``major`` or
      ``critical``); an entry without a ``severity`` is not constrained.
    * **keywords**: at least one of the entry's ``keywords`` appears
      (case-insensitive) in the finding's ``claim`` or ``evidence``; an empty
      list imposes no keyword constraint.
    """
    exp_file = entry.get("file")
    if exp_file is not None and str(finding.get("file", "")) != str(exp_file):
        return False
    if not _line_matches(finding.get("line"), entry.get("line"), tol):
        return False
    exp_sev = entry.get("severity")
    if exp_sev is not None and _severity_rank(finding.get("severity")) > _severity_rank(exp_sev):
        return False
    return _keywords_match(finding, entry.get("keywords", []))


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def score_fixture(findings: list[dict], expected: dict, tol: int = LINE_TOLERANCE) -> FixtureScore:
    """Score a list of finding dicts against a fixture's ``expected`` spec.

    The ``expected`` spec is the ``"expect"`` object documented in the fixture
    schema and supports these (all optional) keys:

    * ``must_match`` (list of entries): each entry SHOULD be matched by at least
      one finding (see :func:`finding_matches_expected`). An unmatched entry is a
      *missed* finding.
    * ``must_not_flag`` (list of entries): findings matching any of these entries
      are *false positives* (the diff is correct here; flagging it is wrong).
    * ``min_findings`` (int): the run must produce at least this many findings.
    * ``max_blocking`` (int): at most this many *blocking* (critical/major)
      findings are allowed. Used by false-positive-trap and docs-only fixtures
      to encode "no blocking finding" via ``{"max_blocking": 0}``.

    Pass/fail: a fixture passes when every ``must_match`` entry is matched, no
    ``must_not_flag`` entry is matched, ``min_findings`` is met, and the blocking
    count does not exceed ``max_blocking``.

    Precision/recall are computed over the ``must_match`` entries:

    * recall = matched / number_of_must_match_entries (1.0 when there are none)
    * precision = matched / (matched + false_positives) (1.0 when both are 0)

    These are directional indicators, not statistically rigorous metrics.
    """
    must_match = expected.get("must_match", []) or []
    must_not_flag = expected.get("must_not_flag", []) or []
    reasons: list[str] = []

    matched = 0
    for entry in must_match:
        if any(finding_matches_expected(f, entry, tol) for f in findings):
            matched += 1
        else:
            reasons.append(f"missed expected finding: {entry}")
    missed = len(must_match) - matched

    false_positives = 0
    for entry in must_not_flag:
        hits = [f for f in findings if finding_matches_expected(f, entry, tol)]
        if hits:
            false_positives += len(hits)
            reasons.append(f"flagged must_not_flag entry: {entry}")

    passed = matched == len(must_match) and false_positives == 0

    min_findings = expected.get("min_findings")
    if isinstance(min_findings, int) and len(findings) < min_findings:
        passed = False
        reasons.append(f"min_findings not met: got {len(findings)}, want >= {min_findings}")

    max_blocking = expected.get("max_blocking")
    if isinstance(max_blocking, int):
        blocking = sum(1 for f in findings if _is_blocking(f))
        if blocking > max_blocking:
            passed = False
            false_positives += blocking - max_blocking
            reasons.append(f"too many blocking findings: got {blocking}, want <= {max_blocking}")

    recall = matched / len(must_match) if must_match else 1.0
    denom = matched + false_positives
    precision = matched / denom if denom else 1.0

    return FixtureScore(
        id=expected.get("id", ""),
        passed=passed,
        matched=matched,
        missed=missed,
        false_positives=false_positives,
        expected_count=len(must_match),
        precision=precision,
        recall=recall,
        reasons=reasons,
    )


def aggregate(scores: list[FixtureScore]) -> dict:
    """Aggregate per-fixture scores into a summary dict."""
    total = len(scores)

    # bolt: Explicitly loop over the list once to calculate aggregates,
    # rather than passing generators to multiple sum() calls.
    # Avoids generator instantiation overhead and reduces loops from 5 to 1.
    passed = matched = missed = false_positives = expected_count = 0
    for s in scores:
        if s.passed:
            passed += 1
        matched += s.matched
        missed += s.missed
        false_positives += s.false_positives
        expected_count += s.expected_count

    recall = matched / expected_count if expected_count else 1.0
    denom = matched + false_positives
    precision = matched / denom if denom else 1.0
    return {
        "fixtures": total,
        "passed": passed,
        "failed": total - passed,
        "matched": matched,
        "missed": missed,
        "false_positives": false_positives,
        "expected_count": expected_count,
        "precision": precision,
        "recall": recall,
    }


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------
def load_fixture(fixture_id: str, base_dir: Path | None = None) -> Fixture:
    """Load a single fixture (diff + expected.json + findings.json) by id."""
    base = base_dir or BENCHMARK_DIR
    diff_path = base / f"{fixture_id}.diff"
    expected_path = base / f"{fixture_id}.expected.json"
    findings_path = base / f"{fixture_id}.findings.json"

    diff = diff_path.read_text(encoding="utf-8")
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected.setdefault("id", fixture_id)
    recorded: list[dict] = []
    if findings_path.exists():
        recorded = json.loads(findings_path.read_text(encoding="utf-8"))
    description = str(expected.get("description", ""))
    return Fixture(
        id=fixture_id,
        description=description,
        diff=diff,
        expected=expected.get("expect", {}) | {"id": expected.get("id", fixture_id)},
        recorded=recorded,
    )


def discover_fixture_ids(base_dir: Path | None = None) -> list[str]:
    """Return the sorted ids of all fixtures (those with an expected.json)."""
    base = base_dir or BENCHMARK_DIR
    if not base.exists():
        return []
    return sorted(p.name[: -len(".expected.json")] for p in base.glob("*.expected.json"))


def load_fixtures(base_dir: Path | None = None) -> list[Fixture]:
    """Load every shipped fixture, sorted by id (stable, deterministic)."""
    return [load_fixture(fid, base_dir) for fid in discover_fixture_ids(base_dir)]


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------
def run_offline(base_dir: Path | None = None) -> tuple[list[FixtureScore], dict]:
    """Score each fixture's *recorded* findings against its expected spec.

    Deterministic, offline, no live CLIs. Returns ``(scores, aggregate)``.
    """
    fixtures = load_fixtures(base_dir)
    scores = [score_fixture(fx.recorded, fx.expected) for fx in fixtures]
    return scores, aggregate(scores)


def live_enabled() -> bool:
    """True when the optional live benchmark mode is explicitly enabled."""
    return os.environ.get("JURY_BENCH_LIVE") == "1"


def run_live(base_dir: Path | None = None) -> tuple[list[FixtureScore], dict]:
    """Run the real jury per fixture diff and score the live findings.

    Only meaningful when ``JURY_BENCH_LIVE=1``. Imports the orchestrator
    lazily so the offline path never touches the live machinery. This invokes
    real agent CLIs and is never run in CI.
    """
    if not live_enabled():
        raise RuntimeError("live benchmark is disabled; set JURY_BENCH_LIVE=1 to enable it")
    # Lazy imports: keep the offline/import path free of live dependencies.
    from .config import DEFAULT_CONFIG, _from_dict
    from .orchestrator import run_jury

    config = _from_dict(DEFAULT_CONFIG)
    fixtures = load_fixtures(base_dir)
    scores: list[FixtureScore] = []
    for fx in fixtures:
        outcome = run_jury(config, fx.diff, mock=False)
        findings = [_finding_to_dict(f) for f in outcome.findings]
        scores.append(score_fixture(findings, fx.expected))
    return scores, aggregate(scores)


def _finding_to_dict(finding) -> dict:
    """Convert a Finding dataclass into the plain dict the scorer consumes."""
    return {
        "severity": getattr(finding, "severity", "info"),
        "file": getattr(finding, "file", ""),
        "line": getattr(finding, "line", None),
        "claim": getattr(finding, "claim", ""),
        "evidence": getattr(finding, "evidence", ""),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def format_table(scores: list[FixtureScore], summary: dict) -> str:
    """Render a per-fixture + aggregate score table as plain text."""
    header = (
        f"{'fixture':<24} {'pass':<5} {'match':<6} {'miss':<5} {'fp':<4} {'prec':<5} {'recall':<6}"
    )
    lines = [header, "-" * len(header)]
    for s in scores:
        lines.append(
            f"{s.id:<24} {('yes' if s.passed else 'NO'):<5} "
            f"{s.matched:<6} {s.missed:<5} {s.false_positives:<4} "
            f"{s.precision:<5.2f} {s.recall:<6.2f}"
        )
    lines.append("-" * len(header))
    pass_ratio = f"{summary['passed']}/{summary['fixtures']}"
    lines.append(
        f"{'TOTAL':<24} {pass_ratio:<5} "
        f"{summary['matched']:<6} {summary['missed']:<5} {summary['false_positives']:<4} "
        f"{summary['precision']:<5.2f} {summary['recall']:<6.2f}"
    )
    return "\n".join(lines)


def main() -> int:
    """Module entry point: print the score table (offline by default)."""
    live = live_enabled()
    mode = "live (JURY_BENCH_LIVE=1)" if live else "offline/recorded"
    print(f"ai-jury benchmark — mode: {mode}")
    print(
        "NOTE: small and directional, not a universal quality claim. "
        "Offline mode validates the scorer + recorded baselines, not live quality.\n"
    )
    scores, summary = run_live() if live else run_offline()
    print(format_table(scores, summary))
    # Exit non-zero when any fixture failed, so the table is usable as a check.
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
