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
    p.add_argument(
        "--suggest-patches", dest="suggest_patches", action="store_true",
        help="emit a separate, opt-in suggested-patches section for VERIFIED "
             "findings (read-only; never applied automatically) (issue #10)",
    )
    p.add_argument(
        "--patches-out", metavar="PATH",
        help="with --suggest-patches, write the patches to this file instead of "
             "appending them after the report",
    )
    p.add_argument(
        "--incremental", action="store_true",
        help="review only the diff since the last council run on --pr when a prior "
             "marker exists, else fall back to a full review (issue #9)",
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


def _run_comment_command(rest: list[str]) -> int:
    """Handle ``council comment`` (issue #11): parse an allowlisted PR-comment
    command and either print the resolved council args or dispatch the run.

    Returns 2 on a rejected/invalid command (so a workflow can ignore it), else
    the dispatched run's exit code (or 0 with --print-args).
    """
    import shlex

    from .commands import CommandError, parse_comment

    sub = argparse.ArgumentParser(prog="council comment", add_help=True)
    sub.add_argument("--text", required=True, help="the PR comment body to parse")
    sub.add_argument("--pr", help="PR number/URL to review and post back to")
    sub.add_argument("--repo", help="owner/name (defaults to current repo)")
    sub.add_argument(
        "--print-args", dest="print_args", action="store_true",
        help="print the resolved council args instead of running",
    )
    sub.add_argument(
        "--no-post", dest="no_post", action="store_true",
        help="do not post the result back as a summary comment",
    )
    ns = sub.parse_args(rest)

    try:
        parsed = parse_comment(ns.text)
    except CommandError as exc:
        print(f"comment command rejected: {exc}", file=sys.stderr)
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
    """Prompt for council settings; returns kwargs for scaffold.build_config.

    ``input_fn`` and ``models_fn`` are injectable for testing (the latter lists
    local models). Defaults are pre-filled from the detected agents/models so
    pressing Enter accepts a sensible config.
    """
    from .scaffold import KNOWN_AGENTS

    if models_fn is None:
        from .adapters import list_local_models as models_fn

    print("Configure a review council (council.toml).\n", file=sys.stderr)
    for name in KNOWN_AGENTS:
        mark = "available" if available.get(name) else "not found"
        print(f"  - {name}: {_AGENT_BLURB[name]} [{mark}]", file=sys.stderr)
    default_agents = [n for n in KNOWN_AGENTS if available.get(n)] or list(KNOWN_AGENTS)
    raw_agents = input_fn(
        f"\nAgents to include [default: {','.join(default_agents)}]: "
    ).strip()
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


def _run_init(rest: list[str]) -> int:
    """Handle ``council init`` (issue #107): scaffold a council.toml."""
    from .config import ConfigError, validate_config
    from .scaffold import KNOWN_AGENTS, build_config, render_toml

    sub = argparse.ArgumentParser(prog="council init")
    sub.add_argument("--agents", help="comma-separated: claude,codex,agy,qwen")
    sub.add_argument("--rounds", type=int, default=2)
    sub.add_argument("--chair")
    sub.add_argument("--no-verify", dest="verify", action="store_false", default=True)
    sub.add_argument("--local-model", help="model id for a local agent (qwen)")
    sub.add_argument("--local-endpoint", help="OpenAI-compatible base URL for a local agent")
    sub.add_argument("-o", "--output", default="council.toml")
    sub.add_argument("--force", action="store_true", help="overwrite an existing file")
    sub.add_argument("--interactive", action="store_true", help="force interactive prompts")
    sub.add_argument("--list-agents", action="store_true", help="list known agents + availability and exit")
    sub.add_argument("--list-models", action="store_true", help="list local models on the server and exit")
    ns = sub.parse_args(rest)

    from .adapters import list_local_models

    endpoint = ns.local_endpoint or "http://localhost:11434/v1"

    if ns.list_models:
        models = list_local_models(endpoint)
        if not models:
            print(f"No local models found (is a server reachable at {endpoint}?).")
            return 0
        print(f"Local models at {endpoint}:")
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
            print(f"\nlocal models at {endpoint}: {', '.join(models)}")
        return 0

    if ns.agents:
        kwargs = {
            "agents": [a.strip() for a in ns.agents.split(",") if a.strip()],
            "rounds": ns.rounds,
            "chair": ns.chair,
            "verify": ns.verify,
            "local_model": ns.local_model,
            "local_endpoint": ns.local_endpoint,
        }
    elif ns.interactive or sys.stdin.isatty():
        kwargs = _init_interactive(available, local_endpoint=ns.local_endpoint)
        kwargs["local_endpoint"] = ns.local_endpoint
        if ns.local_model:
            kwargs["local_model"] = ns.local_model
    else:
        # Non-interactive with no --agents: default to whatever is available.
        detected = [n for n in KNOWN_AGENTS if available.get(n)]
        if not detected:
            print(
                "error: no agents detected and none specified; pass --agents "
                "(e.g. --agents claude,codex) or run interactively.",
                file=sys.stderr,
            )
            return 2
        kwargs = {
            "agents": detected, "rounds": ns.rounds, "chair": ns.chair,
            "verify": ns.verify, "local_model": ns.local_model,
            "local_endpoint": ns.local_endpoint,
        }

    try:
        config = build_config(**kwargs)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # The scaffolded config must itself be valid (fail loudly if a template drifts).
    try:
        validate_config(config)
    except ConfigError as exc:
        print(f"error: generated config is invalid: {exc}", file=sys.stderr)
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
    print(f"Wrote {out_path} — panel: {chosen} · rounds: {config['council']['rounds']}")
    print(f"Next: council --config-validate --config {out_path}")
    print("Then: git diff main... | council --diff-file -")
    return 0


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

    # Comment-command mode (issue #11): `council comment --text "/council review"`
    # parses an allowlisted PR-comment command and dispatches a safe council run.
    # Handled before the main parser so the comment text is never confused with
    # the council's own flags, and never reaches a shell.
    if raw[:1] == ["comment"]:
        return _run_comment_command(raw[1:])

    # Config scaffolding (issue #107): `council init` writes a council.toml from
    # detected agents / flags / interactive prompts. Intercepted before the main
    # parser so it keeps its own small flag surface.
    if raw[:1] == ["init"]:
        return _run_init(raw[1:])

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

    # Incremental review (issue #9): when --incremental and a prior council
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

    # Optional local result cache (issue #33): a hit skips the run entirely; a
    # miss runs the council and stores the outcome. The key covers the diff,
    # effective config, prompt version, package version, context policy, and seed.
    cache = None
    cache_k = None
    outcome = None
    if args.cache:
        from .cache import Cache, cache_key

        cache = Cache(args.cache_dir)
        cache_k = cache_key(config, diff, mock=args.mock)
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
            review_scope=review_scope,
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

    if args.output:
        with Path(args.output).open("w", encoding="utf-8") as fh:
            fh.write(report + "\n")
        log(f"report written to {args.output}")
    else:
        print(report)

    if args.post_summary:
        if not args.pr:
            raise SystemExit("error: --post-summary requires --pr")
        body = report
        # Record the reviewed head SHA as a hidden marker so a later
        # --incremental run can review only the new range (issue #9).
        from .github import pr_head_sha
        from .incremental import reviewed_sha_marker

        marker_sha = head_sha or pr_head_sha(args.pr, args.repo)
        if marker_sha:
            body = f"{body}\n\n{reviewed_sha_marker(marker_sha)}"
        post_pr_comment(args.pr, body, args.repo)
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
