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
from .github import post_inline_comments, post_pr_comment, pr_context, pr_diff
from .metadata import build_run_metadata
from .orchestrator import run_council
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
    p.add_argument("--rounds", type=int, help="override number of rounds (1=review, 2=+debate)")
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
        "--ci", action="store_true",
        help="CI mode: exit non-zero when blocking findings remain",
    )
    p.add_argument(
        "--fail-on",
        help="comma-separated severities that fail CI (overrides config)",
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
    args = build_parser().parse_args(argv)

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
    if args.chair:
        config.chair = args.chair
    if args.verify is not None:
        config.verify = args.verify
    if args.context_mode is not None:
        config.context.mode = args.context_mode
    if args.redact is not None:
        config.context.redact_secrets = args.redact

    def log(msg: str) -> None:
        if not args.quiet:
            print(f"[council] {msg}", file=sys.stderr)

    diff, context = _read_diff(args)
    if not diff.strip():
        raise SystemExit("error: empty diff — nothing to review")

    outcome = run_council(
        config, diff, context=context, mock=args.mock, strict=args.strict, log=log
    )
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

    return ci_exit


if __name__ == "__main__":
    raise SystemExit(main())
