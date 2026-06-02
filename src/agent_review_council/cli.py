"""Command-line entry point: ``council``.

Examples:
  council --pr 123                        # review a GitHub PR
  council --pr 123 --post                 # ...and post the verdict as a comment
  council --diff-file changes.diff        # review a local diff file
  council --diff-file -                   # read a diff from stdin
  council --mock                          # offline pipeline demo (no live CLIs)
  council --doctor                        # local readiness diagnostics
  council --config-validate               # validate council.toml and exit
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import doctor as doctor_module
from .ci import evaluate_ci
from .config import ConfigError, load_config, load_raw_config, validate_config
from .classification import classify, label_strings
from .github import (
    apply_labels,
    post_inline_comments,
    post_pr_comment,
    pr_context,
    pr_diff,
)
from .metadata import build_run_metadata
from .orchestrator import review_diff
from .policy import PolicyError, load_policy
from .report import render


def _read_diff(args) -> tuple[str, str]:
    """Return (diff, context)."""
    if args.pr:
        return pr_diff(args.pr, args.repo), pr_context(args.pr, args.repo)
    if args.diff_file:
        if args.diff_file == "-":
            return sys.stdin.read(), ""
        with Path(args.diff_file).open(encoding="utf-8") as fh:
            return fh.read(), ""
    raise SystemExit("error: provide one of --pr, --diff-file (or --diff-file - for stdin)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="council",
        description="Cross-vendor multi-agent PR review council.",
    )
    src = p.add_argument_group("input")
    src.add_argument("--pr", help="GitHub PR number/URL to review (uses `gh`)")
    src.add_argument("--repo", help="owner/name for --pr (defaults to current repo)")
    src.add_argument("--diff-file", help="path to a diff file, or '-' for stdin")

    p.add_argument("--config", help="path to council.toml (default: ./council.toml or built-in)")
    p.add_argument(
        "--policy",
        type=Path,
        default=None,
        help="path to an optional repository review policy file (default: "
             "auto-discover .council/policy.toml or council-policy.toml); "
             "missing policy files are allowed",
    )
    p.add_argument(
        "--context-mode", choices=["diff-only", "expanded"], default=None,
        help="context policy: diff-only sends only the diff; expanded includes PR context",
    )
    p.add_argument(
        "--redact", dest="redact", action="store_true", default=None,
        help="redact secrets from prompt text before sending (default: from config)",
    )
    p.add_argument(
        "--no-redact", dest="redact", action="store_false",
        help="do not redact secrets before sending",
    )
    p.add_argument(
        "--rounds", type=int,
        help="override number of rounds (1=review, 2=+debate); a fixed value "
             "disables early-stop for reproducible benchmarking",
    )
    p.add_argument(
        "--max-rounds", type=int,
        help="ceiling on adaptive rounds when early-stop is on (issue #40)",
    )
    p.add_argument(
        "--early-stop", dest="early_stop", action="store_true", default=None,
        help="stop after round 1 when reviewers agree; debate only on disagreement",
    )
    p.add_argument(
        "--no-early-stop", dest="early_stop", action="store_false",
        help="disable adaptive early-stop (honour a fixed number of rounds)",
    )
    p.add_argument(
        "--total-timeout", type=int,
        help="overall wall-clock budget (seconds) for the whole run (issue #30)",
    )
    p.add_argument(
        "--phase-timeout", type=int,
        help="per-phase wall-clock budget (seconds) (issue #30)",
    )
    p.add_argument(
        "--retries", type=int,
        help="extra attempts for transient (timeout/rate-limit/spawn) failures",
    )
    p.add_argument(
        "--max-diff-bytes", type=int,
        help="size budget for the (filtered) diff before chunking/too-large (issue #31)",
    )
    p.add_argument(
        "--chunk", dest="chunk", action="store_true", default=None,
        help="chunk an over-budget diff by file instead of failing",
    )
    p.add_argument(
        "--no-chunk", dest="chunk", action="store_false",
        help="disable diff chunking (fail clearly when over budget)",
    )
    p.add_argument(
        "--exclude", action="append", metavar="GLOB", default=None,
        help="exclude files matching this path glob (repeatable)",
    )
    p.add_argument(
        "--include", action="append", metavar="GLOB", default=None,
        help="only review files matching this path glob (repeatable)",
    )
    p.add_argument(
        "--seed", type=int,
        help="run seed for reproducible orchestration; mock runs with the same seed "
             "produce byte-identical reports (overrides [council] seed)",
    )
    p.add_argument("--chair", help="override the synthesizing chair agent")
    p.add_argument("--mock", action="store_true", help="offline demo: use deterministic mock agents")
    p.add_argument("--strict", action="store_true", help="fail if any configured agent CLI is missing")
    p.add_argument(
        "--verify", dest="verify", action="store_true", default=None,
        help="run the verification round (default: from config)",
    )
    p.add_argument(
        "--no-verify", dest="verify", action="store_false",
        help="skip the verification round",
    )
    p.add_argument(
        "--doctor", action="store_true",
        help="print a local readiness diagnostics report and exit (no telemetry is collected or sent)",
    )
    p.add_argument(
        "--write",
        help="with --doctor, also write the diagnostics as JSON to this path (secrets redacted)",
    )
    p.add_argument("-o", "--output", help="write the report to a file instead of stdout")
    p.add_argument(
        "--metadata-json", metavar="PATH",
        help="write machine-readable run metadata (durations, status, rounds) as JSON",
    )
    p.add_argument(
        "--format", choices=["markdown", "json", "sarif"], default="markdown",
        help="output format for stdout/--output (default: markdown)",
    )
    p.add_argument(
        "--post-summary", "--post", dest="post_summary", action="store_true",
        help="post the report as a single summary comment on --pr",
    )
    p.add_argument(
        "--post-inline", dest="post_inline", action="store_true",
        help="post inline review comments for located findings on --pr",
    )
    p.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="with --post-inline, print what would be posted without calling GitHub",
    )
    p.add_argument(
        "--label", dest="label", action="store_true",
        help="apply classification labels (review effort / risk / security) to "
             "--pr (off by default; never applied automatically)",
    )
    p.add_argument(
        "--ci", action="store_true",
        help="CI mode: exit non-zero when blocking findings remain",
    )
    p.add_argument(
        "--fail-on",
        help="comma-separated severities that fail CI (overrides config)",
    )
    p.add_argument(
        "--cache", action="store_true",
        help="use the local result cache: reuse a cached outcome for an unchanged "
             "diff+config, else run and store it (issue #33; off by default)",
    )
    p.add_argument(
        "--clear-cache", action="store_true",
        help="delete all local cache entries and exit (also: `council cache clear`)",
    )
    p.add_argument(
        "--cache-dir",
        help="override the cache directory (default: $COUNCIL_CACHE_DIR or "
             "~/.cache/agent-review-council)",
    )
    p.add_argument("-q", "--quiet", action="store_true", help="suppress progress logs on stderr")
    p.add_argument(
        "--config-validate", action="store_true",
        help="validate the resolved config and exit (0 valid, 2 invalid)",
    )
    p.add_argument(
        "--strict-config", action="store_true",
        help="treat configuration warnings as errors",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # Documented `council cache clear` UX (issue #33): handled before argparse so
    # the rest of the CLI keeps its flat flag surface (no subcommands).
    if raw[:2] == ["cache", "clear"]:
        from .cache import Cache

        # An optional --cache-dir may follow.
        cache_dir = None
        if "--cache-dir" in raw:
            idx = raw.index("--cache-dir")
            if idx + 1 < len(raw):
                cache_dir = raw[idx + 1]
        removed = Cache(cache_dir).clear()
        print(f"Cleared {removed} cache entr{'y' if removed == 1 else 'ies'}.")
        return 0

    args = build_parser().parse_args(argv)

    if args.clear_cache:
        from .cache import Cache

        removed = Cache(args.cache_dir).clear()
        print(f"Cleared {removed} cache entr{'y' if removed == 1 else 'ies'}.")
        return 0

    if args.doctor:
        diagnostics = doctor_module.build_diagnostics(args.config)
        print(doctor_module.render_report(diagnostics))
        if args.write:
            try:
                Path(args.write).write_text(
                    json.dumps(diagnostics, indent=2) + "\n", encoding="utf-8"
                )
            except OSError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print(f"\nWrote diagnostics to {args.write}")
        return 0

    if args.config_validate:
        source = args.config or "council.toml (or built-in defaults)"
        try:
            data = load_raw_config(args.config)
            warnings = validate_config(data, strict=args.strict_config)
        except (ConfigError, FileNotFoundError) as exc:
            print(f"Config invalid ({source}): {exc}", file=sys.stderr)
            return 2
        if warnings:
            print(f"Config valid with warnings ({source}):")
            for w in warnings:
                print(f"  - {w}")
        else:
            print(f"Config valid ({source}).")
        return 0

    try:
        config = load_config(args.config, validate=True, strict=args.strict_config)
    except ConfigError as exc:
        print(f"Config invalid: {exc}", file=sys.stderr)
        return 2
    if args.rounds is not None:
        config.rounds = args.rounds
        # A fixed --rounds is a hard override: it disables adaptive early-stop so
        # the run is reproducible fixed-N (issue #40), unless --early-stop is also
        # passed explicitly (handled below).
        config.early_stop = False
    if args.max_rounds is not None:
        config.max_rounds = args.max_rounds
    if args.early_stop is not None:
        config.early_stop = args.early_stop
    if args.total_timeout is not None:
        config.total_timeout = args.total_timeout
    if args.phase_timeout is not None:
        config.phase_timeout = args.phase_timeout
    if args.retries is not None:
        config.retries = max(0, args.retries)
    if args.seed is not None:
        config.seed = args.seed
    if args.chair:
        config.chair = args.chair
    if args.verify is not None:
        config.verify = args.verify
    if args.context_mode is not None:
        config.context.mode = args.context_mode
    if args.redact is not None:
        config.context.redact_secrets = args.redact
    if args.max_diff_bytes is not None:
        config.diff.max_bytes = args.max_diff_bytes
    if args.chunk is not None:
        config.diff.chunk = args.chunk
    if args.exclude:
        config.diff.exclude = list(config.diff.exclude) + list(args.exclude)
    if args.include:
        config.diff.include = list(config.diff.include) + list(args.include)

    try:
        policy = load_policy(args.policy)
    except PolicyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    def log(msg: str) -> None:
        if not args.quiet:
            print(f"[council] {msg}", file=sys.stderr)

    diff, context = _read_diff(args)
    if not diff.strip():
        raise SystemExit("error: empty diff — nothing to review")

    # Optional local result cache (issue #33): a hit skips the run entirely; a
    # miss runs the council and stores the outcome. The key covers the diff,
    # effective config, prompt version, package version, context policy, and seed.
    cache = None
    cache_k = None
    outcome = None
    if args.cache:
        from .cache import Cache, cache_key

        cache = Cache(args.cache_dir)
        cache_k = cache_key(config, diff)
        outcome = cache.load(cache_k)
        if outcome is not None:
            log(f"cache hit ({cache_k[:12]}…) — reusing stored outcome")
        else:
            log(f"cache miss ({cache_k[:12]}…) — running council")

    if outcome is None:
        try:
            outcome, _plan = review_diff(
                config, diff, context=context, mock=args.mock, strict=args.strict,
                policy=policy, log=log,
            )
        except KeyboardInterrupt:
            # Graceful cancellation (issue #30): a council run can be long, so
            # Ctrl-C should exit cleanly with the conventional 130 rather than
            # dumping a traceback. Work already completed is not partially
            # rendered here because the orchestrator returns atomically; we just
            # report the cancellation.
            print("\n[council] cancelled (interrupted) — no report produced", file=sys.stderr)
            return 130
        except RuntimeError as exc:
            # Large-diff "too large / nothing to review" (issue #31) and "no
            # usable agents" are actionable user errors, not crashes.
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if cache is not None and cache_k is not None:
            cache.store(cache_k, outcome)
            log(f"cached outcome ({cache_k[:12]}…)")

    metadata = build_run_metadata(outcome, config)

    if args.format == "json":
        from .formats import to_json
        report = to_json(outcome, config)
    elif args.format == "sarif":
        from .formats import to_sarif
        report = to_sarif(outcome, config)
    else:
        report = render(
            outcome.reviews,
            outcome.debate,
            outcome.synthesis,
            chair=outcome.chair,
            findings=outcome.findings,
            warnings=outcome.warnings,
            groups=outcome.groups,
            verify=outcome.verify,
            context_mode=outcome.context_mode,
            redact_secrets=outcome.redact_secrets,
            redaction_count=outcome.redaction_count,
            metadata=metadata,
        )

    if args.metadata_json:
        with Path(args.metadata_json).open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(metadata, indent=2) + "\n")
        log(f"metadata written to {args.metadata_json}")

    ci_exit = 0
    if args.ci:
        fail_on = config.ci.fail_on
        if args.fail_on:
            fail_on = [s.strip().lower() for s in args.fail_on.split(",") if s.strip()]
        ci_exit, ci_reason = evaluate_ci(
            outcome.groups, fail_on, config.ci.ignore_unverified
        )
        # Only the markdown report carries the human-readable CI gate section;
        # json/sarif documents stay machine-clean. The exit code is unchanged.
        if args.format == "markdown":
            report += f"\n\n## CI gate\n\n{ci_reason}\n"

    if args.output:
        with Path(args.output).open("w", encoding="utf-8") as fh:
            fh.write(report + "\n")
        log(f"report written to {args.output}")
    else:
        print(report)

    if args.post_summary:
        if not args.pr:
            raise SystemExit("error: --post-summary requires --pr")
        post_pr_comment(args.pr, report, args.repo)
        log(f"posted verdict to PR #{args.pr}")

    if args.post_inline:
        if not args.pr:
            raise SystemExit("error: --post-inline requires --pr")
        post_inline_comments(args.pr, outcome.findings, repo=args.repo, dry_run=args.dry_run)
        log(f"posted inline comments to PR #{args.pr}")

    # Optional GitHub labels (issue #7): OFF by default. Only applied when
    # --label is passed AND a --pr target exists; never automatic.
    if args.label:
        if not args.pr:
            raise SystemExit("error: --label requires --pr")
        labels = label_strings(classify(outcome))
        apply_labels(args.pr, labels, args.repo)
        log(f"applied labels to PR #{args.pr}: {', '.join(labels)}")

    return ci_exit


if __name__ == "__main__":
    raise SystemExit(main())
