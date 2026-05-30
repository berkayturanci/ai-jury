"""Command-line entry point: ``council``.

Examples:
  council --pr 123                        # review a GitHub PR
  council --pr 123 --post                 # ...and post the verdict as a comment
  council --diff-file changes.diff        # review a local diff file
  council --diff-file -                   # read a diff from stdin
  council --mock                          # offline pipeline demo (no live CLIs)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import load_config
from .github import post_pr_comment, pr_context, pr_diff
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
    p.add_argument("-o", "--output", help="write the report to a file instead of stdout")
    p.add_argument("--post", action="store_true", help="post the report as a comment on --pr")
    p.add_argument("-q", "--quiet", action="store_true", help="suppress progress logs on stderr")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    config = load_config(args.config)
    if args.rounds is not None:
        config.rounds = args.rounds
    if args.chair:
        config.chair = args.chair
    if args.verify is not None:
        config.verify = args.verify

    def log(msg: str) -> None:
        if not args.quiet:
            print(f"[council] {msg}", file=sys.stderr)

    diff, context = _read_diff(args)
    if not diff.strip():
        raise SystemExit("error: empty diff — nothing to review")

    outcome = run_council(
        config, diff, context=context, mock=args.mock, strict=args.strict, log=log
    )
    report = render(
        outcome.reviews,
        outcome.debate,
        outcome.synthesis,
        chair=outcome.chair,
        findings=outcome.findings,
        warnings=outcome.warnings,
        groups=outcome.groups,
        verify=outcome.verify,
    )

    if args.output:
        with Path(args.output).open("w", encoding="utf-8") as fh:
            fh.write(report + "\n")
        log(f"report written to {args.output}")
    else:
        print(report)

    if args.post:
        if not args.pr:
            raise SystemExit("error: --post requires --pr")
        post_pr_comment(args.pr, report, args.repo)
        log(f"posted verdict to PR #{args.pr}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
