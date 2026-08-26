"""Command-line entry point: ``jury``.

Examples:
  jury --pr 123                        # review a GitHub PR
  jury --pr 123 --post                 # ...and post the verdict as a comment
  jury --diff-file changes.diff        # review a local diff file
  jury --diff-file -                   # read a diff from stdin
  jury --mock                          # offline pipeline demo (no live CLIs)
  jury --doctor                        # local readiness diagnostics
  jury --config-validate               # validate jury.toml and exit
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from pathlib import Path

from . import __version__
from . import doctor as doctor_module
from .ci import evaluate_ci
from .classification import classify, label_strings
from .config import ConfigError, load_config, load_raw_config, validate_config
from .github import (
    apply_labels,
    issue_body,
    post_inline_comments,
    post_issue_comment,
    post_pr_comment,
    pr_context,
    pr_diff,
)
from .metadata import build_run_metadata, panel_accounting
from .orchestrator import review_diff, run_jury
from .policy import PolicyError, load_policy
from .redaction import redact, redact_url_userinfo
from .report import render, render_live_step, render_transcript

# Hard ceiling on raw diff ingestion. The per-run ``diff.max_bytes`` budget is
# only applied *after* the full diff is read and split, so an unbounded
# ``stdin``/``--diff-file`` read could OOM the process before that cap engages
# (security audit 2026-06-13). This ceiling sits far above any realistic review
# budget; it exists solely to bound memory against a hostile/huge input.
_MAX_DIFF_INGEST_BYTES = 64 * 1024 * 1024  # 64 MiB


def _read_capped(fh, source: str) -> str:
    """Read from ``fh``, refusing inputs above the ingest ceiling.

    The cap is enforced on **bytes**, not characters: a text read of N chars can
    hold up to 4N bytes for multi-byte UTF-8, so a char ceiling would admit
    several times the intended memory (security audit 2026-06-13, red-team).
    Callers pass a binary stream for real input (``sys.stdin.buffer`` / a file
    opened ``"rb"``); a text stream is also accepted (its read is measured by its
    UTF-8 byte length) so test doubles and unusual streams still work.
    """
    data = fh.read(_MAX_DIFF_INGEST_BYTES + 1)
    if isinstance(data, str):
        if len(data.encode("utf-8", "replace")) > _MAX_DIFF_INGEST_BYTES:
            raise SystemExit(
                f"error: {source} exceeds the {_MAX_DIFF_INGEST_BYTES}-byte ingest limit"
            )
        return data
    if len(data) > _MAX_DIFF_INGEST_BYTES:
        raise SystemExit(f"error: {source} exceeds the {_MAX_DIFF_INGEST_BYTES}-byte ingest limit")
    return data.decode("utf-8", errors="replace")


def _checked_revision(value: str, flag: str) -> str:
    """Reject a revision that cannot safely reach ``git``'s argv (issue #367).

    ``run`` uses argv, never a shell, so quoting is not the risk — a value starting
    with ``-`` is: git would read it as an option rather than a revision. Refused
    rather than escaped, and ``--`` is passed at the call site as a second guard.
    Empty is refused too, since it would silently widen the diff.
    """
    revision = (value or "").strip()
    if not revision:
        raise SystemExit(f"error: {flag} needs a revision")
    if revision.startswith("-"):
        raise SystemExit(
            f"error: {flag} revision {revision!r} may not start with '-' "
            "(git would read it as an option)"
        )
    return revision


def _git_diff(argv: list[str], label: str) -> str:
    """Run a read-only git command and return its stdout, or exit with its error."""
    import subprocess  # local: keeps the module importable where git is absent

    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"error: could not run git for {label}: {redact(str(exc))[0]}") from None
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        raise SystemExit(
            f"error: git could not resolve {label}" + (f": {redact(detail[0])[0]}" if detail else "")
        )
    if not proc.stdout.strip():
        raise SystemExit(f"error: {label} produced an empty diff — nothing to review")
    # Same ingest ceiling every other source honours; _read_capped wants a handle.
    return _read_capped(io.StringIO(proc.stdout), label)


def _read_diff(args) -> tuple[str, str]:
    """Return (diff, context)."""
    if getattr(args, "commit", None):
        rev = _checked_revision(args.commit, "--commit")
        # `git show` of a merge commit prints no diff by default; -m picks the
        # first-parent view so a merge is reviewable rather than silently empty.
        return _git_diff(
            ["git", "show", "--format=", "--patch", "-m", "--first-parent", rev, "--"],
            f"commit {rev}",
        ), ""
    if getattr(args, "commits", None):
        rev = _checked_revision(args.commits, "--commits")
        return _git_diff(["git", "diff", rev, "--"], f"range {rev}"), ""
    if args.pr:
        return pr_diff(args.pr, args.repo), pr_context(args.pr, args.repo)
    if args.issue:
        # Issue mode (issue #221): the issue's rendered text takes the diff slot;
        # there is no separate context block (title/labels are folded into it).
        return issue_body(args.issue, args.repo), ""
    if args.diff_file:
        if args.diff_file == "-":
            # Prefer the byte stream so the cap is exact; fall back to the text
            # stream (e.g. a StringIO test double) which lacks ``.buffer``.
            return _read_capped(getattr(sys.stdin, "buffer", sys.stdin), "stdin"), ""
        try:
            with Path(args.diff_file).open("rb") as fh:
                return _read_capped(fh, args.diff_file), ""
        except (OSError, UnicodeDecodeError) as exc:
            raise SystemExit(f"error reading diff file '{args.diff_file}': {exc}") from None
    raise SystemExit(
        "error: provide one of --pr, --issue, --diff-file, --commit, --commits "
        "(or --diff-file - for stdin)"
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jury",
        description="Cross-vendor multi-agent PR review jury.",
    )
    src = p.add_argument_group("input")
    src.add_argument("--pr", help="GitHub PR number/URL to review (uses `gh`)")
    src.add_argument(
        "--issue",
        help="GitHub issue number/URL to review for completeness/clarity (uses "
        "`gh`); runs the full jury with an issue-quality rubric",
    )
    src.add_argument("--repo", help="owner/name for --pr/--issue (defaults to current repo)")
    src.add_argument("--diff-file", help="path to a diff file, or '-' for stdin")
    src.add_argument("--commit", help="review the diff one commit introduces (needs a git repo)")
    src.add_argument(
        "--commits",
        help="review a commit range, e.g. origin/main..HEAD or HEAD~5..HEAD (needs a git repo)",
    )

    p.add_argument("--config", help="path to jury.toml (default: ./jury.toml or built-in)")
    p.add_argument(
        "--policy",
        type=Path,
        default=None,
        help="path to an optional repository review policy file (default: "
        "auto-discover .jury/policy.toml or jury-policy.toml); "
        "missing policy files are allowed",
    )
    p.add_argument(
        "--context-mode",
        choices=["diff-only", "expanded"],
        default=None,
        help="context policy: diff-only sends only the diff; expanded includes PR context",
    )
    p.add_argument(
        "--redact",
        dest="redact",
        action="store_true",
        default=None,
        help="redact secrets from prompt text before sending (default: from config)",
    )
    p.add_argument(
        "--no-redact",
        dest="redact",
        action="store_false",
        help="do not redact secrets before sending",
    )
    p.add_argument(
        "--rounds",
        type=int,
        help="override number of rounds (1=review, 2=+debate); a fixed value "
        "disables early-stop for reproducible benchmarking",
    )
    p.add_argument(
        "--max-rounds",
        type=int,
        help="ceiling on adaptive rounds when early-stop is on",
    )
    p.add_argument(
        "--early-stop",
        dest="early_stop",
        action="store_true",
        default=None,
        help="stop after round 1 when reviewers agree; debate only on disagreement",
    )
    p.add_argument(
        "--no-early-stop",
        dest="early_stop",
        action="store_false",
        help="disable adaptive early-stop (honour a fixed number of rounds)",
    )
    p.add_argument(
        "--auto",
        dest="auto",
        action="store_true",
        default=None,
        help="risk-aware auto-depth: scale rounds/verify to the diff",
    )
    p.add_argument(
        "--no-auto",
        dest="auto",
        action="store_false",
        help="disable auto-depth (use configured/fixed rounds)",
    )
    p.add_argument(
        "--total-timeout",
        type=int,
        help="overall wall-clock budget (seconds) for the whole run",
    )
    p.add_argument(
        "--phase-timeout",
        type=int,
        help="per-phase wall-clock budget (seconds)",
    )
    p.add_argument(
        "--retries",
        type=int,
        help="extra attempts for transient (timeout/rate-limit/spawn) failures",
    )
    p.add_argument(
        "--max-diff-bytes",
        type=int,
        help="size budget for the (filtered) diff before chunking/too-large",
    )
    p.add_argument(
        "--chunk",
        dest="chunk",
        action="store_true",
        default=None,
        help="chunk an over-budget diff by file instead of failing",
    )
    p.add_argument(
        "--no-chunk",
        dest="chunk",
        action="store_false",
        help="disable diff chunking (fail clearly when over budget)",
    )
    p.add_argument(
        "--exclude",
        action="append",
        metavar="GLOB",
        default=None,
        help="exclude files matching this path glob (repeatable)",
    )
    p.add_argument(
        "--include",
        action="append",
        metavar="GLOB",
        default=None,
        help="only review files matching this path glob (repeatable)",
    )
    p.add_argument(
        "--seed",
        type=int,
        help="run seed for reproducible orchestration; mock runs with the same seed "
        "produce byte-identical reports (overrides [jury] seed)",
    )
    p.add_argument("--chair", help="override the synthesizing chair agent")
    p.add_argument(
        "--mock", action="store_true", help="offline demo: use deterministic mock agents"
    )
    p.add_argument(
        "--strict", action="store_true", help="fail if any configured agent CLI is missing"
    )
    p.add_argument(
        "--min-vendors",
        type=int,
        default=0,
        metavar="N",
        help=(
            "fail (exit 3) unless at least N distinct vendors contributed a "
            "review; 0 disables. --strict checks availability at startup, this "
            "checks participation at the end"
        ),
    )
    p.add_argument(
        "--verify",
        dest="verify",
        action="store_true",
        default=None,
        help="run the verification round (default: from config)",
    )
    p.add_argument(
        "--no-verify",
        dest="verify",
        action="store_false",
        help="skip the verification round",
    )
    p.add_argument(
        "--doctor",
        action="store_true",
        help="print a local readiness diagnostics report and exit (no telemetry is collected or sent)",
    )
    p.add_argument(
        "--write",
        help="with --doctor, also write the diagnostics as JSON to this path (secrets redacted)",
    )
    p.add_argument("-o", "--output", help="write the report to a file instead of stdout")
    p.add_argument(
        "--metadata-json",
        metavar="PATH",
        help="write machine-readable run metadata (durations, status, rounds) as JSON",
    )
    p.add_argument(
        "--format",
        choices=["markdown", "json", "sarif"],
        default="markdown",
        help="output format for stdout/--output (default: markdown)",
    )
    p.add_argument(
        "--decision",
        choices=["chair", "vote"],
        default=None,
        help="final verdict: 'chair' synthesis (default) or panel 'vote' (tally "
        "the reviewers); overrides [jury] decision",
    )
    p.add_argument(
        "--transcript",
        dest="transcript",
        action="store_true",
        default=None,
        help="render the full play-by-play transcript (each agent's review, the "
        "debate, and the chair's reasoning) instead of the summary report",
    )
    p.add_argument(
        "--no-transcript",
        dest="transcript",
        action="store_false",
        help="force the summary report even if [jury] transcript is set",
    )
    p.add_argument(
        "--verbose",
        dest="verbose",
        action="store_true",
        help="summary report followed by the full transcript, in one document",
    )
    p.add_argument(
        "--live",
        dest="live",
        action="store_true",
        help="stream each step (review, debate, verdict) to stdout as it happens; "
        "add --pr --post to also post each step as its own PR comment",
    )
    p.add_argument(
        "--theater",
        dest="theater",
        action="store_true",
        default=None,
        help="animated deliberation view of the live run (each model seated "
        "around a table, speaking per phase, panel-vote/chair finale); needs an "
        "interactive terminal, else falls back to --live. Can be defaulted on in "
        "jury.toml ([jury] theater = true)",
    )
    p.add_argument(
        "--no-theater",
        dest="theater",
        action="store_false",
        help="disable the theater scene even if jury.toml enables it",
    )
    p.add_argument(
        "--theater-style",
        dest="theater_style",
        choices=("flat", "pixel"),
        default=None,
        help="--theater scene style: 'flat' (ANSI line scene, default) or "
        "'pixel' (pixel-art room; needs a truecolor+unicode terminal). Defaults "
        "from jury.toml ([jury] theater_style)",
    )
    p.add_argument(
        "--post-summary",
        "--post",
        dest="post_summary",
        action="store_true",
        help="post the report as a single summary comment on --pr",
    )
    p.add_argument(
        "--post-inline",
        dest="post_inline",
        action="store_true",
        help="post inline review comments for located findings on --pr",
    )
    p.add_argument(
        "--post-progress",
        dest="post_progress",
        action="store_true",
        help="keep a live, sticky status comment on --pr updated per round/chunk",
    )
    p.add_argument(
        "--post-mode",
        choices=["single", "phased"],
        default="single",
        help="with --post-summary: 'single' (one comment) or 'phased' (separate "
        "Round 1 / debate / decision comments)",
    )
    p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="with --post-inline, print what would be posted without calling GitHub",
    )
    p.add_argument(
        "--label",
        dest="label",
        action="store_true",
        help="apply classification labels (review effort / risk / security) to "
        "--pr (off by default; never applied automatically)",
    )
    p.add_argument(
        "--ci",
        action="store_true",
        help="CI mode: exit non-zero when blocking findings remain",
    )
    p.add_argument(
        "--fail-on",
        help="comma-separated severities that fail CI (overrides config)",
    )
    p.add_argument(
        "--cache",
        action="store_true",
        help="use the local result cache: reuse a cached outcome for an unchanged "
        "diff+config, else run and store it (off by default)",
    )
    p.add_argument(
        "--clear-cache",
        action="store_true",
        help="delete all local cache entries and exit (also: `jury cache clear`)",
    )
    p.add_argument(
        "--cache-dir",
        help="override the cache directory (default: $JURY_CACHE_DIR or ~/.cache/ai-jury)",
    )
    p.add_argument(
        "--suggest-patches",
        dest="suggest_patches",
        action="store_true",
        help="emit a separate, opt-in suggested-patches section for VERIFIED "
        "findings (read-only; never applied automatically)",
    )
    p.add_argument(
        "--patches-out",
        metavar="PATH",
        help="with --suggest-patches, write the patches to this file instead of "
        "appending them after the report",
    )
    p.add_argument(
        "--incremental",
        action="store_true",
        help="review only the diff since the last jury run on --pr when a prior "
        "marker exists, else fall back to a full review",
    )
    p.add_argument("-q", "--quiet", action="store_true", help="suppress progress logs on stderr")
    p.add_argument(
        "--config-validate",
        action="store_true",
        help="validate the resolved config and exit (0 valid, 2 invalid)",
    )
    p.add_argument(
        "--strict-config",
        action="store_true",
        help="treat configuration warnings as errors",
    )
    p.add_argument(
        "--tiered",
        action="store_true",
        help="opt-in risk-aware tiered model routing with frontier anchor (issue #524)",
    )
    p.add_argument(
        "--hints",
        action="store_true",
        help="run local static analysis pre-pass (Ruff/ESLint) to inject hints (issue #523)",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _run_apply(rest: list[str]) -> int:
    """Handle `jury apply` (issue #521): apply verified patch suggestions."""
    from .patches import (
        apply_patch_suggestion,
        parse_patch_suggestions,
        preview_patch_suggestion,
    )

    sub = argparse.ArgumentParser(
        prog="jury apply", description="Apply verified suggested patches to the repository."
    )
    sub.add_argument(
        "index",
        nargs="?",
        default=None,
        help="1-indexed patch suggestion number to apply, or 'all'. Required — "
        "applying every suggestion is the wrong default for a command that "
        "writes to the working tree",
    )
    sub.add_argument(
        "--report",
        "-r",
        help="Path to a markdown report file or patch file (defaults to stdin)",
    )
    sub.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the paths each suggestion would touch and write nothing",
    )
    sub.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the confirmation prompt; required when stdin is not a terminal",
    )
    ns = sub.parse_args(rest)

    content = ""
    if ns.report:
        p = Path(ns.report)
        try:
            if not p.is_file():
                print(f"Error: report file not found: {ns.report}", file=sys.stderr)
                return 2
            content = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"Error reading report file '{ns.report}': {exc}", file=sys.stderr)
            return 2
    elif sys.stdin is not None and not sys.stdin.isatty():
        content = sys.stdin.read()
    else:
        print(
            "Error: provide a report file via --report <file> or pipe a report via stdin",
            file=sys.stderr,
        )
        return 2

    suggestions = parse_patch_suggestions(content)
    if not suggestions:
        print("No verified patch suggestions found in the provided report.", file=sys.stderr)
        return 1

    if ns.index is None:
        print(
            f"Error: choose what to apply — an index from 1 to {len(suggestions)}, or 'all'.\n"
            "       `jury apply --dry-run all` shows what each one would touch.",
            file=sys.stderr,
        )
        return 2

    if ns.index.lower() == "all":
        selected = list(enumerate(suggestions, 1))
    else:
        try:
            target_idx = int(ns.index) - 1
        except ValueError:
            print(f"Error: invalid index '{ns.index}'", file=sys.stderr)
            return 2
        if not (0 <= target_idx < len(suggestions)):
            print(
                f"Error: patch index {ns.index} out of range (found {len(suggestions)} suggestions)",
                file=sys.stderr,
            )
            return 2
        selected = [(target_idx + 1, suggestions[target_idx])]

    # Preview before writing, always. The report is derived from a diff that may be
    # attacker-influenced and its suggestions were written by an LLM, so the
    # operator gets git's own answer about what would change *before* anything
    # changes. The old output was printed after the write had happened (#605).
    print("These suggestions would touch:", file=sys.stderr)
    for number, suggestion in selected:
        paths, refusal = preview_patch_suggestion(suggestion)
        listed = ", ".join(paths) if paths else "(nothing git could read)"
        print(f"  [{number}] {suggestion.file}: {listed}", file=sys.stderr)
        if refusal is not None:
            print(f"        would be refused: {refusal}", file=sys.stderr)

    if ns.dry_run:
        print("Dry run: nothing was written.", file=sys.stderr)
        return 0

    if not ns.yes:
        # Piping a report in is exactly the unattended case, and stdin may already
        # be consumed by the report itself — so silence is refused rather than read
        # as consent.
        if sys.stdin is None or not sys.stdin.isatty():
            print(
                "Error: refusing to write without confirmation. Re-run with --yes to "
                "confirm, or --dry-run to preview.",
                file=sys.stderr,
            )
            return 2
        answer = input(f"Apply {len(selected)} suggestion(s)? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted; nothing was written.", file=sys.stderr)
            return 1

    success_count = 0
    total = len(selected)
    for number, suggestion in selected:
        ok, msg = apply_patch_suggestion(suggestion)
        if ok:
            success_count += 1
            print(f"✓ [{number}/{total}] {msg}")
        else:
            print(f"✗ [{number}/{total}] {msg}", file=sys.stderr)
    return 0 if success_count > 0 else 1


def _run_comment_command(rest: list[str]) -> int:
    """Handle ``jury comment`` (issue #11): parse an allowlisted PR-comment
    command and either print the resolved jury args or dispatch the run.

    Returns 2 on a rejected/invalid command (so a workflow can ignore it), else
    the dispatched run's exit code (or 0 with --print-args).
    """
    import shlex

    from .commands import CommandError, parse_comment

    sub = argparse.ArgumentParser(prog="jury comment", add_help=True)
    sub.add_argument("--text", required=True, help="the PR comment body to parse")
    sub.add_argument("--pr", help="PR number/URL to review and post back to")
    sub.add_argument("--repo", help="owner/name (defaults to current repo)")
    sub.add_argument(
        "--print-args",
        dest="print_args",
        action="store_true",
        help="print the resolved jury args instead of running",
    )
    sub.add_argument(
        "--no-post",
        dest="no_post",
        action="store_true",
        help="do not post the result back as a summary comment",
    )
    ns = sub.parse_args(rest)

    try:
        parsed = parse_comment(ns.text)
    except CommandError as exc:
        print(f"comment command rejected: {redact(str(exc))[0]}", file=sys.stderr)
        return 2

    inner = parsed.to_cli_args()
    if ns.pr:
        inner += ["--pr", ns.pr]
        if not ns.no_post:
            inner += ["--post-summary"]
    if ns.repo:
        inner += ["--repo", ns.repo]

    if ns.print_args:
        print(" ".join(shlex.quote(a) for a in inner))
        return 0
    return main(inner)


_AGENT_BLURB = {
    "claude": "Claude Code (Anthropic)",
    "codex": "Codex CLI (OpenAI)",
    "agy": "Antigravity (Google)",
    "qwen": "local / open-weight via Ollama (free, offline)",
    "claude-api": "hosted Anthropic API (ANTHROPIC_API_KEY, no CLI needed)",
    "codex-api": "hosted OpenAI API (OPENAI_API_KEY, no CLI needed)",
    "gemini-api": "hosted Google Gemini API (GEMINI_API_KEY, no CLI needed)",
    "openrouter": "hosted OpenRouter API (OPENROUTER_API_KEY)",
    "deepseek": "hosted DeepSeek API (DEEPSEEK_API_KEY)",
    "groq": "hosted Groq API (GROQ_API_KEY)",
    "aider": "generic CLI coding agent (Aider)",
}


def _init_available() -> dict:
    """Map each known agent name to whether it is reachable right now."""
    from .adapters import make_adapter
    from .config import AgentSpec
    from .scaffold import KNOWN_AGENTS, agent_templates

    templates = agent_templates()
    out = {}
    for name in KNOWN_AGENTS:
        try:
            out[name] = make_adapter(AgentSpec(**templates[name])).available()
        except Exception:  # noqa: BLE001 - detection is best-effort
            out[name] = False
    return out


def _init_interactive(available: dict, input_fn=input, local_endpoint=None, models_fn=None) -> dict:
    """Prompt for jury settings; returns kwargs for scaffold.build_config.

    ``input_fn`` and ``models_fn`` are injectable for testing (the latter lists
    local models). Defaults are pre-filled from the detected agents/models so
    pressing Enter accepts a sensible config.
    """
    from .scaffold import KNOWN_AGENTS

    if models_fn is None:
        from .adapters import list_local_models as models_fn

    print("Configure a review jury (jury.toml).\n", file=sys.stderr)
    for name in KNOWN_AGENTS:
        mark = "available" if available.get(name) else "not found"
        print(f"  - {name}: {_AGENT_BLURB[name]} [{mark}]", file=sys.stderr)
    default_agents = [n for n in KNOWN_AGENTS if available.get(n)] or list(KNOWN_AGENTS)
    raw_agents = input_fn(f"\nAgents to include [default: {','.join(default_agents)}]: ").strip()
    agents = [a.strip() for a in raw_agents.split(",") if a.strip()] or default_agents

    rounds_raw = input_fn("Rounds — 1=review, 2=+debate [2]: ").strip()
    rounds = int(rounds_raw) if rounds_raw.isdigit() else 2

    chair_default = agents[0] if agents else "claude"
    chair = input_fn(f"Chair agent [{chair_default}]: ").strip() or chair_default

    verify = (input_fn("Run verification round? [Y/n]: ").strip().lower() or "y") != "n"

    local_model = None
    has_local = any(a in agents for a in ("qwen", "local"))
    if has_local:
        from .scaffold import pick_default_model

        models = models_fn(local_endpoint or "http://localhost:11434/v1")
        if models:
            default = pick_default_model(models)
            print("\nLocal models available on the server:", file=sys.stderr)
            for i, m in enumerate(models, 1):
                star = " (default)" if m == default else ""
                print(f"  {i}. {m}{star}", file=sys.stderr)
            raw = input_fn(f"Pick a local model [number or name, default: {default}]: ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(models):
                local_model = models[int(raw) - 1]
            elif raw:
                local_model = raw
            else:
                local_model = default
        else:
            print(
                "\n(could not reach the local server to list models; using the default)",
                file=sys.stderr,
            )
            local_model = input_fn("Local model name [qwen2.5-coder:7b]: ").strip() or None

    return {
        "agents": agents,
        "rounds": rounds,
        "chair": chair,
        "verify": verify,
        "local_model": local_model,
    }


def _init_wizard(available: dict, input_fn=input, local_endpoint=None, models_fn=None) -> dict:
    """Guided, numbered-option setup for ``jury init --wizard`` (issue #231).

    Mirrors :func:`_init_interactive`'s injectable params for offline testing.
    Every question is SKIPPABLE: pressing Enter leaves the setting unset, so it
    falls back to the built-in default and is NOT written to ``jury.toml`` (which
    keeps the generated file minimal). Returns kwargs for ``scaffold.build_config``
    containing only the values the user explicitly chose.
    """
    from .scaffold import KNOWN_AGENTS

    if models_fn is None:
        from .adapters import list_local_models as models_fn

    def ask(prompt: str) -> str:
        return input_fn(prompt).strip()

    def choose(prompt: str, options: list[str], default_idx: int) -> int | None:
        """Print numbered options and read a 1-based pick. Enter -> None (skip)."""
        print(prompt, file=sys.stderr)
        for i, label in enumerate(options, 1):
            star = " (default)" if i - 1 == default_idx else ""
            print(f"  {i}. {label}{star}", file=sys.stderr)
        raw = ask("Pick a number [Enter to keep default]: ")
        if not raw:
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        return None

    print(
        "jury init --wizard — guided setup (writes jury.toml).\n"
        "Every question is optional: press Enter to keep the default and skip it;\n"
        "skipped settings are left at their built-in defaults (not written).\n",
        file=sys.stderr,
    )

    # Reviewers (always written — like plain init).
    for name in KNOWN_AGENTS:
        mark = "available" if available.get(name) else "not found"
        print(f"  - {name}: {_AGENT_BLURB[name]} [{mark}]", file=sys.stderr)
    default_agents = [n for n in KNOWN_AGENTS if available.get(n)] or list(KNOWN_AGENTS)
    raw_agents = ask(f"\nReviewers to include [default: {','.join(default_agents)}]: ")
    agents = [a.strip() for a in raw_agents.split(",") if a.strip()] or default_agents

    kwargs: dict = {"agents": agents}

    # Depth -> rounds / early_stop / auto_depth.
    depth = choose(
        "\nDepth:",
        [
            "1 round (review only)",
            "2 rounds + debate",
            "adaptive (early-stop)",
            "auto-depth (scale to the diff)",
        ],
        default_idx=1,
    )
    if depth == 0:
        kwargs["rounds"] = 1
    elif depth == 1:
        kwargs["rounds"] = 2
    elif depth == 2:
        kwargs["rounds"] = 2
        kwargs["early_stop"] = True
    elif depth == 3:
        kwargs["auto_depth"] = True

    # Decision: chair (default) or panel vote. Only written on a non-default.
    decision = choose("\nDecision:", ["chair synthesis", "panel vote"], default_idx=0)
    if decision == 1:
        kwargs["decision"] = "vote"

    # Verification (always written — like plain init).
    verify_raw = ask("\nRun verification round? [Y/n]: ").lower()
    if verify_raw:
        kwargs["verify"] = verify_raw != "n"

    # Context: diff-only (default) or expanded; redact secrets Y/n.
    ctx = choose(
        "\nContext sent to reviewers:",
        ["diff-only", "expanded (include PR context)"],
        default_idx=0,
    )
    if ctx == 1:
        kwargs["context_mode"] = "expanded"
    redact_raw = ask("Redact secrets before sending? [Y/n]: ").lower()
    if redact_raw == "n":
        kwargs["redact_secrets"] = False

    # CI gate fail-on. Only write [jury.ci] on a non-default pick.
    gate = choose(
        "\nCI gate — fail on which severities?",
        ["critical,major", "critical only", "skip (never fail CI)"],
        default_idx=0,
    )
    if gate == 1:
        kwargs["ci_fail_on"] = ["critical"]
    elif gate == 2:
        kwargs["ci_fail_on"] = []

    # Chair (always written — like plain init; default = first reviewer).
    chair_default = agents[0] if agents else "claude"
    chair = ask(f"\nChair agent [{chair_default}]: ") or chair_default
    kwargs["chair"] = chair

    # Local model pick when a local reviewer is chosen (reuse init's logic).
    if any(a in agents for a in ("qwen", "local")):
        from .scaffold import pick_default_model

        models = models_fn(local_endpoint or "http://localhost:11434/v1")
        if models:
            default = pick_default_model(models)
            print("\nLocal models available on the server:", file=sys.stderr)
            for i, m in enumerate(models, 1):
                star = " (default)" if m == default else ""
                print(f"  {i}. {m}{star}", file=sys.stderr)
            raw = ask(f"Pick a local model [number or name, default: {default}]: ")
            if raw.isdigit() and 1 <= int(raw) <= len(models):
                kwargs["local_model"] = models[int(raw) - 1]
            elif raw:
                kwargs["local_model"] = raw
            else:
                kwargs["local_model"] = default
        else:
            print(
                "\n(could not reach the local server to list models; using the default)",
                file=sys.stderr,
            )
            typed = ask("Local model name [qwen2.5-coder:7b]: ")
            if typed:
                kwargs["local_model"] = typed

    return kwargs


def _run_init(rest: list[str]) -> int:
    """Handle ``jury init`` (issue #107): scaffold a jury.toml."""
    from .config import ConfigError, validate_config
    from .scaffold import (
        KNOWN_AGENTS,
        PRESETS,
        agents_needing_remote_opt_in,
        build_config,
        render_toml,
    )

    sub = argparse.ArgumentParser(prog="jury init")
    sub.add_argument(
        "--preset",
        choices=sorted(PRESETS),
        help="setup preset: offline (local-only), fast (1 round), balanced "
        "(debate + early-stop), thorough (all agents + debate + verify)",
    )
    sub.add_argument("--agents", help="comma-separated: claude,codex,agy,qwen")
    sub.add_argument("--rounds", type=int, default=None)
    sub.add_argument("--chair")
    sub.add_argument("--verify", dest="verify", action="store_true", default=None)
    sub.add_argument("--no-verify", dest="verify", action="store_false")
    sub.add_argument("--local-model", help="model id for a local agent (qwen)")
    sub.add_argument("--local-endpoint", help="OpenAI-compatible base URL for a local agent")
    sub.add_argument("-o", "--output", default="jury.toml")
    sub.add_argument("--force", action="store_true", help="overwrite an existing file")
    sub.add_argument("--interactive", action="store_true", help="force interactive prompts")
    sub.add_argument(
        "--wizard",
        action="store_true",
        help="guided, numbered-option setup; every question is skippable (Enter "
        "keeps the built-in default) and only chosen keys are written",
    )
    sub.add_argument(
        "--list-agents", action="store_true", help="list known agents + availability and exit"
    )
    sub.add_argument(
        "--list-models", action="store_true", help="list local models on the server and exit"
    )
    ns = sub.parse_args(rest)

    from .adapters import list_local_models

    endpoint = ns.local_endpoint or "http://localhost:11434/v1"
    # Strip any userinfo credentials before echoing the endpoint to stdout/CI
    # logs (issue #316/L-7, completed in v1.5.0/L-1: structural strip catches
    # short and colon-less userinfo the regex missed), mirroring doctor.py.
    endpoint_disp = redact_url_userinfo(endpoint)

    if ns.list_models:
        models = list_local_models(endpoint)
        if not models:
            print(f"No local models found (is a server reachable at {endpoint_disp}?).")
            return 0
        print(f"Local models at {endpoint_disp}:")
        for m in models:
            print(f"  - {m}")
        return 0

    available = _init_available()

    if ns.list_agents:
        for name in KNOWN_AGENTS:
            mark = "available" if available.get(name) else "not found"
            print(f"{name:8} {_AGENT_BLURB[name]:45} [{mark}]")
        # Show discovered local models so the user sees what they can pick.
        models = list_local_models(endpoint)
        if models:
            print(f"\nlocal models at {endpoint_disp}: {', '.join(models)}")
        return 0

    preset = PRESETS.get(ns.preset, {})

    def _detected_agents():
        return [n for n in KNOWN_AGENTS if available.get(n)]

    def _selectable_agents():
        """Every known agent whose template scaffolds to a *valid* config here.

        Three hosted templates point at real vendor hosts, and `config` refuses
        a non-loopback endpoint unless `JURY_ALLOW_REMOTE_ENDPOINT` is set. A
        preset that silently includes them writes a config `jury init` then
        rejects — so `--preset thorough` failed outright rather than producing
        something usable. They stay in `--list-agents` and remain selectable by
        name; they are only excluded from "all" until the opt-in is present.
        """
        if os.environ.get("JURY_ALLOW_REMOTE_ENDPOINT"):
            return list(KNOWN_AGENTS)
        needs_opt_in = set(agents_needing_remote_opt_in())
        return [n for n in KNOWN_AGENTS if n not in needs_opt_in]

    def _resolve_preset_agents(spec):
        if spec == "all":
            return _selectable_agents()
        if spec == "detected":
            return _detected_agents() or _selectable_agents()
        return list(spec)

    # rounds / verify / early_stop: explicit flag > preset > built-in default.
    rounds = ns.rounds if ns.rounds is not None else preset.get("rounds", 2)
    verify = ns.verify if ns.verify is not None else preset.get("verify", True)
    early_stop = preset.get("early_stop")

    # Guided wizard (issue #231): opt-in via --wizard. A numbered-option flow
    # where every question is skippable; only explicitly-chosen settings are
    # written, so the file stays minimal. Runs regardless of TTY (it is explicit).
    if ns.wizard:
        kwargs = _init_wizard(available, local_endpoint=ns.local_endpoint)
        kwargs["local_endpoint"] = ns.local_endpoint
        if ns.local_model:
            kwargs["local_model"] = ns.local_model
    # Interactive only when neither --agents nor --preset was given and we're on a
    # TTY (or --interactive). Presets/flags are non-interactive by design.
    elif not ns.agents and not ns.preset and (ns.interactive or sys.stdin.isatty()):
        kwargs = _init_interactive(available, local_endpoint=ns.local_endpoint)
        kwargs["local_endpoint"] = ns.local_endpoint
        if ns.local_model:
            kwargs["local_model"] = ns.local_model
    else:
        if ns.agents:
            agents = [a.strip() for a in ns.agents.split(",") if a.strip()]
        elif ns.preset:
            agents = _resolve_preset_agents(preset["agents"])
        else:
            agents = _detected_agents()
            if not agents:
                print(
                    "error: no agents detected and none specified; pass --agents "
                    "or --preset (e.g. --preset offline), or run interactively.",
                    file=sys.stderr,
                )
                return 2
        kwargs = {
            "agents": agents,
            "rounds": rounds,
            "chair": ns.chair,
            "verify": verify,
            "early_stop": early_stop,
            "local_model": ns.local_model,
            "local_endpoint": ns.local_endpoint,
        }

    try:
        config = build_config(**kwargs)
    except ValueError as exc:
        print(f"error: {redact(str(exc))[0]}", file=sys.stderr)
        return 2

    # The scaffolded config must itself be valid (fail loudly if a template drifts).
    try:
        validate_config(config)
    except ConfigError as exc:
        print(f"error: generated config is invalid: {redact(str(exc))[0]}", file=sys.stderr)
        return 2

    out_path = Path(ns.output)
    if out_path.exists() and not ns.force:
        print(
            f"error: {out_path} already exists; pass --force to overwrite.",
            file=sys.stderr,
        )
        return 2

    out_path.write_text(render_toml(config), encoding="utf-8")
    chosen = ", ".join(a["name"] for a in config["agent"])
    print(f"Wrote {out_path} — panel: {chosen} · rounds: {config['jury']['rounds']}")
    print(f"Next: jury --config-validate --config {out_path}")
    print("Then: git diff main... | jury --diff-file -")
    return 0


def _config_source(config_arg) -> str:
    """Human-readable source of the config the jury would load."""
    if config_arg:
        return str(config_arg)
    return "jury.toml" if Path("jury.toml").exists() else "(built-in defaults)"


def _render_effective_config(cfg) -> str:
    """Render the EFFECTIVE resolved config as a readable summary (config show)."""
    on = lambda b: "on" if b else "off"  # noqa: E731
    lines = []
    lines.append(
        f"[jury] rounds={cfg.rounds} chair={cfg.chair} verify={on(cfg.verify)} "
        f"parallel={on(cfg.parallel)} timeout={cfg.timeout}s"
    )
    adaptive = f"early_stop={on(cfg.early_stop)} max_rounds={cfg.effective_max_rounds}"
    budget = (
        f"total_timeout={cfg.total_timeout or '—'} "
        f"phase_timeout={cfg.phase_timeout or '—'} retries={cfg.retries}"
    )
    lines.append(
        f"          {adaptive}  ·  {budget}  ·  seed={cfg.seed if cfg.seed is not None else '—'}"
    )
    lines.append(
        f"[jury.ci] fail_on={cfg.ci.fail_on} ignore_unverified={on(cfg.ci.ignore_unverified)}"
    )
    lines.append(
        f"[jury.context] mode={cfg.context.mode} redact_secrets={on(cfg.context.redact_secrets)}"
    )
    d = cfg.diff
    lines.append(
        f"[jury.diff] max_bytes={d.max_bytes} chunk={on(d.chunk)} "
        f"exclude_generated={on(d.exclude_generated)} "
        f"exclude={d.exclude or '[]'} include={d.include or '[]'}"
    )
    lines.append("agents:")
    for a in cfg.agents:
        flag = "" if a.enabled else "  (disabled)"
        target = a.endpoint if a.vendor == "local" else (a.command or "—")
        model = f" model={a.model}" if a.model else ""
        lines.append(f"  - {a.name} ({a.vendor}) → {target}{model}{flag}")
    return "\n".join(lines)


def _run_config(rest: list[str]) -> int:
    """Handle ``jury config show|path``."""
    from .config import ConfigError, load_config

    sub = argparse.ArgumentParser(prog="jury config")
    sub.add_argument("action", choices=["show", "path"])
    sub.add_argument("--config", help="path to jury.toml (default: ./jury.toml or built-in)")
    ns = sub.parse_args(rest)

    source = _config_source(ns.config)
    if ns.action == "path":
        print(source)
        return 0

    try:
        cfg = load_config(ns.config, validate=True)
    except (ConfigError, FileNotFoundError) as exc:
        print(f"error: {redact(str(exc))[0]}", file=sys.stderr)
        return 2
    print(f"source: {source}")
    print(_render_effective_config(cfg))
    return 0


def _run_replay(rest: list[str]) -> int:
    """Handle ``jury replay <outcome.json>`` (issue #449).

    Replays a saved run in the deliberation theater — or, off a TTY / without
    ``--theater``, as the same plain step stream ``--live`` prints. Pure
    presentation: no orchestration, no network, no agents.
    """
    from .replay import ReplayError, load_outcome, replay_events, replay_into

    sub = argparse.ArgumentParser(
        prog="jury replay",
        description="Replay a saved jury outcome (a result-cache entry or a "
        "serialized outcome dict) in the deliberation theater. No agents run.",
    )
    sub.add_argument(
        "outcome",
        help="path to a saved outcome JSON (cache entry or outcome dict)",
    )
    sub.add_argument(
        "--theater",
        action="store_true",
        help="replay in the animated deliberation scene (needs a wide TTY; "
        "falls back to plain transcript lines otherwise)",
    )
    sub.add_argument(
        "--theater-style",
        choices=["flat", "pixel"],
        default="flat",
        help="--theater scene style: 'flat' (ANSI line scene, default) or "
        "'pixel' (half-block pixel-art room)",
    )
    sub.add_argument(
        "--decision",
        choices=["chair", "vote"],
        default="chair",
        help="finale mode: 'chair' shows the stored synthesis verdict (default); "
        "'vote' re-tallies the panel ballots for the vote finale",
    )
    sub.add_argument(
        "--mode",
        choices=["code", "issue"],
        default="code",
        help="vote vocabulary for --decision vote (the serialized outcome does "
        "not record the run mode): 'code' (APPROVE/COMMENT/REQUEST CHANGES, "
        "default) or 'issue' (READY/UNCLEAR/NEEDS-INFO)",
    )
    ns = sub.parse_args(rest)

    try:
        outcome = load_outcome(Path(ns.outcome))
    except ReplayError as exc:
        print(f"error: {redact(str(exc))[0]}", file=sys.stderr)
        return 2

    # Panel-vote finale (mirrors the live path): re-tally from the stored
    # groups/reviews — deterministic, no agents involved.
    vote = None
    if ns.decision == "vote":
        from .voting import is_abstention, tally_votes

        voters = [
            r.agent for r in outcome.reviews if r.ok and not is_abstention(getattr(r, "output", ""))
        ]
        vote = tally_votes(outcome.groups, voters, mode=ns.mode)

    # Same TTY gate as the live path: the scene needs a wide TTY, otherwise
    # degrade to the plain --live step stream.
    court = None
    if ns.theater:
        from . import theater as _theater

        if _theater.supports_scene(sys.stdout):
            seats: dict[str, str] = {}
            for r in outcome.reviews:
                seats.setdefault(r.agent, r.vendor)
            court = _theater.Courtroom(
                list(seats.items()),
                outcome.chair or "chair",
                case=Path(ns.outcome).name,
                decision=ns.decision,
                style=ns.theater_style,
            )

    if court is not None:
        replay_into(court, outcome, vote=vote)
    else:
        for kind, result, round_no in replay_events(outcome):
            title, body = render_live_step(kind, result, round_no)
            print(f"## {title}\n\n{body}\n", flush=True)
        if vote is not None:
            # The vote finale must survive the transcript fallback too (review
            # finding: --decision vote was computed then silently dropped here).
            print("## Panel vote\n", flush=True)
            for ballot in vote.ballots:
                print(f"- {ballot.reviewer}: {ballot.vote} ({ballot.reason})", flush=True)
            print(f"\nVerdict: {vote.verdict}\n", flush=True)
    return 0


_PROGRESS_PREFIXES = (
    "round ",
    "reviewing chunk",
    "verification",
    "synthesis",
    "diff size",
    "early stop",
    "auto-depth",
)


def _is_progress_milestone(msg: str) -> bool:
    """Whether a log line is a coarse milestone worth a sticky-comment update."""
    return msg.startswith(_PROGRESS_PREFIXES)


def _maybe_add_local_fallback(config, args, log) -> None:
    """Append a local agent when nothing else can run, offline (issue: zero-config).

    Only fires in the safe "fresh user" case: no explicit `--config`, no
    `./jury.toml`, not `--mock`, none of the configured agents are available,
    and a local OpenAI-compatible server is reachable with at least one model.
    Mutates ``config`` in place and points the chair at the local agent.
    """
    if args.config or args.mock or Path("jury.toml").exists():
        return
    from .adapters import list_local_models, make_adapter
    from .config import AgentSpec
    from .scaffold import pick_default_model

    try:
        if any(make_adapter(s).available() for s in config.enabled_agents):
            return
    except Exception:  # noqa: BLE001 - availability probing must never crash a run
        return
    models = list_local_models()
    model = pick_default_model(models)
    if not model:
        return
    config.agents.append(
        AgentSpec(name="local", vendor="local", model=model, endpoint="http://localhost:11434/v1")
    )
    config.chair = "local"
    log(f"no agent CLIs found; using local model '{model}' (offline, $0)")


def _force_utf8_output() -> None:
    """Ensure stdout/stderr can emit the report's Unicode (emoji, arrows).

    On Windows the console defaults to a legacy code page (e.g. cp1252) that
    can't encode the report's `🏛️`/`⇄` characters, so `print(report)` raises
    `UnicodeEncodeError`. Reconfigure the real streams to UTF-8 when possible;
    `reconfigure` is absent on replaced streams (tests' StringIO, some pipes),
    so this is a best-effort no-op there.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8")


_OVERVIEW = """\
🏛️  ai-jury — a cross-vendor multi-agent review jury.

It runs several coding-agent CLIs (Claude, Codex, Antigravity) plus an optional
local model over the same diff, PR, or issue; they cross-examine and verify each
other, and a chair (or a panel vote) synthesizes one verdict.

Common commands:
  jury init --wizard              guided setup — writes a jury.toml (skippable)
  jury --pr 123                   review a pull request
  jury --issue 42                 review an issue for completeness
  git diff | jury --diff-file -   review the current branch's diff
  jury examples                   more example commands
  jury guide                      a short end-to-end walkthrough
  jury --help                     every option

Docs: https://github.com/berkayturanci/ai-jury"""

_EXAMPLES = """\
ai-jury — example commands

Setup
  jury init --wizard                 guided setup (writes jury.toml)
  jury init --preset thorough        non-interactive preset
  jury config show                   print the effective, resolved config
  jury doctor                        check which agents/CLIs are available

Review
  jury --pr 123                      review a pull request
  jury --issue 42                    review an issue for completeness
  git diff | jury --diff-file -      review the current branch's diff
  jury --diff-file changes.patch     review a saved patch
  jury --pr 123 --verbose            full play-by-play (rounds + transcript)

Decide & gate
  jury --pr 123 --decision vote      verdict by panel vote (not a single chair)
  jury --pr 123 --ci                 exit non-zero on a blocking finding (CI gate)

Post results back to GitHub
  jury --pr 123 --post-summary       post one rollup comment
  jury --pr 123 --post-inline        post line-level review comments
  jury --issue 42 --post-summary     post the triage verdict on the issue

Run `jury guide` for a walkthrough, or `jury --help` for every option."""

_GUIDE = """\
ai-jury — a short walkthrough

1. Install the agent CLIs you have (any subset works): Claude Code, Codex,
   Antigravity. Optionally run a local model via Ollama for a free panelist.
   Check what's available:
       jury doctor

2. Create a config (picks reviewers, rounds, chair/vote, verify):
       jury init --wizard
   Every question is skippable — Enter keeps the built-in default.

3. Run your first review:
       jury --pr 123                  # a pull request
       jury --issue 42                # an issue's completeness
       git diff | jury --diff-file -  # the current branch

   The panel reviews independently, cross-examines (debate), the chair verifies
   candidate findings to cut false positives, then synthesizes one verdict.

4. Post the verdict back to GitHub (optional):
       jury --pr 123 --post-summary   # one rollup comment
       jury --pr 123 --post-inline    # line-level comments

5. Gate CI on blocking findings (optional):
       jury --pr 123 --ci             # non-zero exit on critical/major

Reviewers run sandboxed/read-only over attacker-controlled diffs by default.
See `jury examples` for more, or `jury --help` for every option.
Docs: https://github.com/berkayturanci/ai-jury"""


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    raw = list(sys.argv[1:] if argv is None else argv)

    # First-impression UX (#265): a newcomer running bare `jury` in a terminal
    # gets a friendly overview and exits 0 — not the argparse error. The strict
    # "provide one of --pr/--issue/--diff-file" error + non-zero exit is kept for
    # non-interactive use (piped/CI), so scripts that forget an input still fail.
    # `sys.stdin` can be None when stdin is detached (e.g. a background process),
    # so guard before calling isatty().
    if not raw and sys.stdin is not None and sys.stdin.isatty():
        print(_OVERVIEW)
        return 0

    # Plain-language command overview / walkthrough (#265), argv-intercepts like
    # the other subcommands so the main flag surface stays flat. Match exactly so
    # trailing junk (`jury examples foo`) falls through to argparse and errors
    # rather than being silently ignored.
    if raw == ["examples"]:
        print(_EXAMPLES)
        return 0
    if raw == ["guide"]:
        print(_GUIDE)
        return 0
    # Documented `jury cache clear` UX (issue #33): handled before argparse so
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

    # Comment-command mode (issue #11): `jury comment --text "/jury review"`
    # parses an allowlisted PR-comment command and dispatches a safe jury run.
    # Handled before the main parser so the comment text is never confused with
    # the jury's own flags, and never reaches a shell.
    if raw[:1] == ["comment"]:
        return _run_comment_command(raw[1:])

    # Config scaffolding (issue #107): `jury init` writes a jury.toml from
    # detected agents / flags / interactive prompts. Intercepted before the main
    # parser so it keeps its own small flag surface.
    if raw[:1] == ["init"]:
        return _run_init(raw[1:])

    # Config introspection: `jury config show` prints the EFFECTIVE resolved
    # config + its source so you can see exactly what will run; `config path`
    # prints just the source.
    if raw[:1] == ["config"]:
        return _run_config(raw[1:])

    # Apply verified suggested patches (issue #521): `jury apply` applies
    # suggested patches directly to the working directory.
    if raw[:1] == ["apply"]:
        return _run_apply(raw[1:])

    # Theater replay (issue #449): `jury replay <outcome.json>` re-drives the
    # deliberation scene from a saved outcome — no agents, no network.
    # Intercepted before the main parser like the other subcommands.
    if raw[:1] == ["replay"]:
        return _run_replay(raw[1:])

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
                print(f"error: {redact(str(exc))[0]}", file=sys.stderr)
                return 2
            print(f"\nWrote diagnostics to {args.write}")
        return 0

    if args.config_validate:
        source = args.config or "jury.toml (or built-in defaults)"
        try:
            data = load_raw_config(args.config)
            warnings = validate_config(data, strict=args.strict_config)
        except (ConfigError, FileNotFoundError) as exc:
            print(redact(f"Config invalid ({source}): {exc}")[0], file=sys.stderr)
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
        print(f"Config invalid: {redact(str(exc))[0]}", file=sys.stderr)
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
        print(f"error: {redact(str(exc))[0]}", file=sys.stderr)
        return 2

    # Issue mode (issue #221) reviews prose, not a diff, so the PR/diff-only
    # concepts below have no meaning. Reject them up front with a clear message
    # rather than silently ignoring them.
    # Exactly one source (issue #367). Listed rather than pairwise so adding a
    # source cannot quietly skip the check.
    _sources = [
        ("--pr", args.pr),
        ("--issue", args.issue),
        ("--diff-file", args.diff_file),
        ("--commit", getattr(args, "commit", None)),
        ("--commits", getattr(args, "commits", None)),
    ]
    _given = [flag for flag, value in _sources if value]
    if len(_given) > 1:
        raise SystemExit(f"error: choose one input source, got {', '.join(_given)}")
    if args.issue:
        for flag, on in (
            ("--post-inline", args.post_inline),
            ("--post-progress", args.post_progress),
            ("--label", args.label),
            ("--incremental", args.incremental),
        ):
            if on:
                raise SystemExit(
                    f"error: {flag} is not supported with --issue (it is a PR/diff concept)"
                )

    # Live progress on the PR (issue #125): a single sticky comment updated at
    # each round/chunk milestone. Opt-in and requires --pr.
    progress = None
    if args.post_progress:
        if not args.pr:
            raise SystemExit("error: --post-progress requires --pr")
        from .github import ProgressReporter

        progress = ProgressReporter(args.pr, args.repo)

    def log(msg: str) -> None:
        if not args.quiet:
            print(f"[jury] {msg}", file=sys.stderr)
        if progress is not None and _is_progress_milestone(msg):
            progress.update(msg)

    # Smart offline fallback: with NO config file and NO usable agent CLI, but a
    # local model server reachable, add a local agent so `jury` just works
    # offline out of the box (issue: easier zero-config). Never overrides an
    # explicit config or a working CLI panel.
    _maybe_add_local_fallback(config, args, log)

    diff, context = _read_diff(args)

    # Incremental review (issue #9): when --incremental and a prior jury
    # marker exists, narrow the diff to the range since the last reviewed SHA;
    # otherwise fall back safely to the full diff. The reviewed head SHA is also
    # recorded on the posted summary so a later run can go incremental.
    review_scope = None
    head_sha = ""
    if args.incremental:
        if not args.pr:
            raise SystemExit("error: --incremental requires --pr")
        from . import incremental as inc
        from .github import compare_diff, pr_comment_bodies, pr_head_sha

        head_sha = pr_head_sha(args.pr, args.repo)
        prev_sha = inc.parse_reviewed_sha(pr_comment_bodies(args.pr, args.repo))
        mode, reason = inc.decide_review(prev_sha, head_sha)
        if mode == inc.MODE_INCREMENTAL:
            inc_diff = compare_diff(prev_sha, head_sha, args.repo)
            if inc_diff.strip():
                diff = inc_diff
            else:
                mode, reason = inc.MODE_FULL, "incremental range unavailable — full review"
        review_scope = inc.scope_note(mode, reason)
        log(reason)

    if not diff.strip():
        raise SystemExit("error: empty diff — nothing to review")

    # Risk-aware auto-depth (issue #120): scale rounds/verify to the diff when
    # enabled. Explicit --rounds/--verify/--early-stop always win; the panel is
    # never trimmed. Off unless --auto or [jury] auto_depth.
    if args.auto if args.auto is not None else config.auto_depth:
        from .diffprofile import depth_for, describe, profile_diff

        prof = profile_diff(diff)
        rounds, verify, early_stop = depth_for(prof.risk)
        if args.rounds is None:
            config.rounds = rounds
            if args.early_stop is None:
                config.early_stop = early_stop
        if args.verify is None:
            config.verify = verify
        log(describe(prof))

    if getattr(args, "tiered", False):
        config.routing = "tiered"
    if getattr(args, "hints", False):
        config.hints = True

    if config.hints:
        from .hints import collect_static_hints

        sh = collect_static_hints()
        if sh:
            context = (context + "\n\n" + sh) if context else sh
            log("injected static analysis hints into review context")

    # Optional local result cache (issue #33): a hit skips the run entirely; a
    # miss runs the jury and stores the outcome. The key covers the diff,
    # effective config, prompt version, package version, context policy, and seed.
    cache = None
    cache_k = None
    outcome = None
    if args.cache:
        from .cache import Cache, cache_key

        cache = Cache(args.cache_dir)
        cache_k = cache_key(
            config, diff, mock=args.mock, policy=policy, mode=("issue" if args.issue else "code")
        )
        outcome = cache.load(cache_k)
        if outcome is not None:
            log(f"cache hit ({cache_k[:12]}…) — reusing stored outcome")
        else:
            log(f"cache miss ({cache_k[:12]}…) — running jury")

    # Live play-by-play (issue #210, #229): stream each step as it happens. Prints
    # a titled block to stdout the moment a phase result lands. Posting each step to
    # the PR/issue is OPT-IN — it requires BOTH a target (--pr or --issue) AND
    # --post (a bare target only selects the source, never auto-posts), so `--live`
    # alone just streams locally. Posting is best-effort: a GitHub hiccup is logged
    # and never aborts the run.
    live_target = args.pr or args.issue
    # Theater defaults can come from jury.toml (issue #364); the CLI flags
    # (--theater / --no-theater, --theater-style) override per run. Sentinels
    # (None) distinguish "not passed" from an explicit choice.
    theater_on = args.theater if args.theater is not None else config.theater
    theater_style = args.theater_style or config.theater_style
    live_posts = bool((args.live or theater_on) and args.post_summary and live_target)
    live_post = post_issue_comment if args.issue else post_pr_comment
    # Opt-in animated "courtroom" scene (--theater): an interactive TTY view of
    # the REAL run (each model seated, speaking per phase, gavel/vote finale). It
    # needs a wide TTY and an actual run (a cache hit has nothing to replay), so
    # it falls back to the plain --live step stream otherwise. The structured
    # outcome / report / CI gate are untouched — this is a side channel.
    court = None
    if theater_on and outcome is None and not args.quiet:
        from . import theater as _theater

        if _theater.supports_scene(sys.stdout):
            # Display-only chair label for the scene title. The run resolves the
            # REAL chair internally (resolve_chair needs the usable/reviewer sets
            # and run RNG, which don't exist yet here), so use a best-effort name.
            chair_name = (
                config.chair
                if config.chair and config.chair != "rotate"
                else (config.agents[0].name if config.agents else "chair")
            )
            case = (
                f"PR #{args.pr}"
                if args.pr
                else f"issue #{args.issue}"
                if args.issue
                else f"commit {args.commit}"
                if getattr(args, "commit", None)
                else f"range {args.commits}"
                if getattr(args, "commits", None)
                else "local diff"
            )
            court = _theater.Courtroom(
                [(a.name, a.vendor) for a in config.agents],
                chair_name,
                case=case,
                mode=("issue" if args.issue else "code"),
                decision=(args.decision or config.decision),
                style=theater_style,
            )
            court.open()

    on_event = None
    if args.live or theater_on:

        def on_event(kind, result, round_no=None):
            if court is not None:
                court.step(kind, result, round_no)
            else:
                # plain step stream (--live, or --theater fallback off a TTY)
                title, body = render_live_step(kind, result, round_no)
                print(f"## {title}\n\n{body}\n", flush=True)
            if live_posts:
                try:
                    title, body = render_live_step(kind, result, round_no)
                    live_post(live_target, f"## {title}\n\n{body}", args.repo)
                except Exception as exc:  # noqa: BLE001 - best-effort, never crash
                    log(f"live: failed to post step to #{live_target}: {redact(str(exc))[0]}")

    # We stream live only when actually running the jury; a cache hit has nothing
    # to replay, so the consolidated report is still printed in that case.
    live_streamed = bool(args.live or theater_on) and outcome is None

    if outcome is None:
        try:
            if args.issue:
                # Issue prose bypasses large-diff planning (filter/size/chunk is
                # meaningless for an issue body); run the jury directly with the
                # issue-quality rubric.
                outcome = run_jury(
                    config,
                    diff,
                    context=context,
                    mock=args.mock,
                    strict=args.strict,
                    policy=policy,
                    log=log,
                    on_event=on_event,
                    mode="issue",
                )
            else:
                outcome, _plan = review_diff(
                    config,
                    diff,
                    context=context,
                    mock=args.mock,
                    strict=args.strict,
                    policy=policy,
                    log=log,
                    on_event=on_event,
                )
        except KeyboardInterrupt:
            # Graceful cancellation (issue #30): a jury run can be long, so
            # Ctrl-C should exit cleanly with the conventional 130 rather than
            # dumping a traceback. Work already completed is not partially
            # rendered here because the orchestrator returns atomically; we just
            # report the cancellation.
            print("\n[jury] cancelled (interrupted) — no report produced", file=sys.stderr)
            return 130
        except RuntimeError as exc:
            # Large-diff "too large / nothing to review" (issue #31) and "no
            # usable agents" are actionable user errors, not crashes.
            print(f"error: {redact(str(exc))[0]}", file=sys.stderr)
            return 2
        if cache is not None and cache_k is not None:
            cache.store(cache_k, outcome)
            log(f"cached outcome ({cache_k[:12]}…)")

    # Final-verdict mode (issue #220): a panel vote (tally the reviewers) vs the
    # chair's synthesis. Rendering-only — the outcome is identical; the severity-
    # based CI gate below is unaffected. Effective = CLI flag else config.
    decision = args.decision or config.decision
    vote = None
    if decision == "vote":
        from .voting import is_abstention, tally_votes

        # A reviewer that abstained (empty reply or a refusal) is excluded from
        # the tally — a non-answer must not count as a "clear" vote (issue #251).
        voters = [
            r.agent for r in outcome.reviews if r.ok and not is_abstention(getattr(r, "output", ""))
        ]
        vote = tally_votes(
            outcome.groups,
            voters,
            mode=("issue" if args.issue else "code"),
        )

    # Close the courtroom scene (after the vote is tallied, so the panel-vote
    # finale can show the ballots/verdict).
    if court is not None:
        if vote is not None:
            court.set_vote(vote)
        court.close()

    metadata = build_run_metadata(outcome, config, decision=decision, vote=vote)

    if args.format == "json":
        from .formats import to_json

        report = to_json(outcome, config, decision=decision, vote=vote)
    elif args.format == "sarif":
        from .formats import to_sarif

        report = to_sarif(outcome, config)
    else:
        # Output mode (issue: full transcript). --verbose => summary + transcript;
        # --transcript (or [jury] transcript, unless --no-transcript) => the
        # chronological play-by-play; otherwise the consensus-first summary.
        # Rendering-only — the orchestration/outcome is identical either way.
        transcript_default = args.transcript if args.transcript is not None else config.transcript
        if args.verbose or transcript_default:
            report = render_transcript(
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
                review_scope=review_scope,
                lead_with_summary=bool(args.verbose),
                vote=vote,
            )
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
                review_scope=review_scope,
                vote=vote,
            )

    if args.metadata_json:
        with Path(args.metadata_json).open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(metadata, indent=2) + "\n")
        log(f"metadata written to {args.metadata_json}")

    ci_exit = 0
    # A run whose panel collapsed is a different thing wearing the same output
    # (#625). `--strict` fails when a configured CLI is *missing*; this fails
    # when one was present, probed fine, and returned nothing — which is how a
    # three-vendor panel silently becomes one. Opt-in, so the default is
    # unchanged, and exit 3 so it is distinguishable from a findings failure.
    if getattr(args, "min_vendors", 0) > 0:
        contributed = panel_accounting(outcome.reviews).get("vendors", 0)
        if contributed < args.min_vendors:
            log(
                f"panel collapsed: {contributed} vendor(s) contributed a review, "
                f"--min-vendors {args.min_vendors} required. An abstention is not "
                "an approval; cross-vendor consensus was not formed."
            )
            ci_exit = 3
    if args.ci:
        fail_on = config.ci.fail_on
        if args.fail_on:
            fail_on = [s.strip().lower() for s in args.fail_on.split(",") if s.strip()]
        ci_exit, ci_reason = evaluate_ci(outcome.groups, fail_on, config.ci.ignore_unverified)
        # Only the markdown report carries the human-readable CI gate section;
        # json/sarif documents stay machine-clean. The exit code is unchanged.
        if args.format == "markdown":
            report += f"\n\n## CI gate\n\n{ci_reason}\n"

    # Suggested patches (issue #10): opt-in and kept separate from the default
    # report. Written to a file with --patches-out, else appended after the
    # markdown report under its own heading. The default flow stays read-only.
    if args.suggest_patches:
        from .patches import render_patch_suggestions

        patches_section = render_patch_suggestions(outcome.groups)
        if not patches_section:
            log("no verified findings with a suggested fix — no patches emitted")
        elif args.patches_out:
            Path(args.patches_out).write_text(patches_section, encoding="utf-8")
            log(f"suggested patches written to {args.patches_out}")
        elif args.format == "markdown":
            report += "\n\n" + patches_section.rstrip()
        else:
            log("--suggest-patches needs markdown output or --patches-out; skipped")

    # Turn the live progress comment into the final verdict (issue #125).
    if progress is not None:
        progress.finish(report)
        log(f"progress comment finalized on PR #{args.pr}")

    if args.output:
        with Path(args.output).open("w", encoding="utf-8") as fh:
            fh.write(report + "\n")
        log(f"report written to {args.output}")
    elif not (live_streamed and args.format == "markdown"):
        # In --live markdown mode the step stream WAS the stdout output; don't also
        # dump the consolidated report (it would duplicate everything just shown).
        # For json/sarif the stream is human-readable markdown, so the requested
        # machine-readable document must still go to stdout.
        print(report)

    if args.post_summary:
        if args.issue:
            # Plain issues use `gh issue comment`; phased/SHA-marker posting is
            # PR-only, so the issue path posts the single rendered report.
            post_issue_comment(args.issue, report, args.repo)
            log(f"posted verdict to issue #{args.issue}")
            return ci_exit
        if not args.pr:
            raise SystemExit("error: --post-summary requires --pr")
        # Record the reviewed head SHA as a hidden marker so a later
        # --incremental run can review only the new range (issue #9).
        from .github import pr_head_sha
        from .incremental import reviewed_sha_marker

        marker_sha = head_sha or pr_head_sha(args.pr, args.repo)
        marker = f"\n\n{reviewed_sha_marker(marker_sha)}" if marker_sha else ""

        if args.post_mode == "phased":
            # Post the flow as separate, readable comments (issue #127):
            # Round 1 → debate → decision. The SHA marker rides the last one.
            from .report import render_sections

            sections = render_sections(
                outcome.reviews,
                outcome.debate,
                outcome.synthesis,
                chair=outcome.chair,
                findings=outcome.findings,
                warnings=outcome.warnings,
                groups=outcome.groups,
                verify=outcome.verify,
                vote=vote,
            )
            for i, (title, body) in enumerate(sections):
                tail = marker if i == len(sections) - 1 else ""
                post_pr_comment(args.pr, f"## {title}\n\n{body}{tail}", args.repo)
            log(f"posted {len(sections)} phased comments to PR #{args.pr}")
        else:
            post_pr_comment(args.pr, f"{report}{marker}", args.repo)
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
