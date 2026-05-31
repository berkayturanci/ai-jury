# Positioning

> A small, project-agnostic, stdlib-first native-CLI review council that runs
> independent cross-vendor reviews, debate, and verified consensus for pull
> requests and diffs.

This page pins down what `agent-review-council` *is* — and, just as importantly,
what it is **not** — so the project doesn't drift into being a hosted code-review
SaaS or a general-purpose multi-agent framework.

## Mission

Make a diff better before it merges by convening **native coding-agent CLIs from
different vendors** as an adversarial review panel: each reviews the same change
independently, they cross-examine each other, and a chair synthesizes one
verified verdict. The whole thing should drop into any repository, run locally,
and depend on nothing but the Python standard library and the agent CLIs you
already have installed.

## What makes it different

- **Cross-vendor by design.** Reviewers come from different vendors (Anthropic,
  OpenAI, Google) because the research-backed lever is **vendor heterogeneity**:
  different models miss different things, so a panel surfaces more real issues
  and filters more false positives than any single reviewer.
- **Independent review → debate → verified consensus.** Round 1 is independent
  review; round 2 is cross-examination where each agent sees the others' findings
  and argues; synthesis produces a single verdict with consensus, disputed, and
  notable single-reviewer findings.
- **Native CLI, not API.** Each reviewer runs in its own vendor agent (`claude`,
  `codex`, `agy`) with its own tooling and context handling — not a raw model API
  call.
- **Stdlib-first.** No third-party Python dependencies — just `subprocess`,
  `tomllib`, `concurrent.futures`, and `argparse`. Easy to read, audit, and
  vendor into any repo or CI.
- **Local-first.** It runs on your machine, spawns the CLIs you already have, and
  only talks to the network when *you* point it at a PR. There is no service to
  sign up for and no central server in the loop.
- **Project-agnostic.** Configuration lives in a single `council.toml`. Nothing
  about the council assumes a particular codebase, language, or team.

## Target users

- Maintainers and small teams who want a cross-vendor second opinion on a diff
  before merging, without standing up infrastructure.
- Developers who already run one or more coding-agent CLIs and want to compose
  them into a review pass.
- CI and automation authors who want a self-contained, dependency-free reviewer
  they can vendor into a pipeline.
- Anyone who wants reviews to stay on their own machine and under their own
  control rather than going through a hosted product.

## Non-goals

- **NOT a hosted review SaaS.** There is no managed service, no accounts, no
  dashboard, no central server that ingests your code. The tool runs locally and
  drives CLIs you control. If you want a hosted PR-review product, use one — this
  is the opposite trade-off on purpose.
- **NOT a general-purpose multi-agent framework.** It does one thing: convene a
  review council over a diff (review → debate → synthesis). It is not a toolkit
  for building arbitrary agent workflows, orchestration graphs, or autonomous
  agents. The round structure is deliberately fixed and auditable.
- **Downstream, project-specific policy does not belong here.** House style,
  required checks, severity gates tuned to one team, org-specific rules — these
  belong in the consuming repository's own policy files (e.g. `council.toml`,
  CI configuration, lint/test rules) or in a thin wrapper around the council, not
  baked into this project. Keeping the council policy-neutral is what lets it stay
  small and drop into *any* repo; the moment it encodes one project's opinions it
  stops being project-agnostic. Configure behavior through `council.toml` and CLI
  flags; express bespoke rules in your own repository.

## Design principles

- **Native CLI over API.** Differentiation comes from running each reviewer in
  its own vendor agent, not from calling models directly.
- **Stdlib only.** No external dependencies; the code stays small enough to read
  end to end.
- **Local-first and fail-soft.** Missing CLIs are skipped, not fatal; the run
  continues with whoever is available (unless `--strict`).
- **Orchestrator owns the prompts; adapters own invocation.** The round
  structure lives in one file and stays auditable; adding a vendor is a small,
  isolated adapter.
- **Policy-neutral core.** The council ships sensible defaults and exposes
  configuration; it does not encode any single project's rules.
- **Mock path is first-class.** `--mock` runs the whole pipeline deterministically
  so tests and CI never need credentials or token spend.

## Privacy and local-first expectations

The council runs on your machine and, by default, shares the smallest possible
data surface:

- **Diff-only by default.** Agents receive only the diff. Surrounding PR context
  (title/body) is dropped unless you opt into `expanded` mode.
- **No ambient reads.** No source files outside the diff, no repository history,
  and no environment variables are read or sent.
- **Secret redaction on by default.** The diff and any context pass through a
  redactor that masks recognized secrets before anything reaches an agent.
- **Network only when you ask.** The tool reaches the network only to drive the
  configured agent CLIs and, when you use `--pr` / `--post`, the GitHub API via
  `gh`. There is no telemetry and no third-party service in the path.

See the [README data-flow / privacy section](../README.md#data-flow--privacy)
and `SECURITY.md` for the full reference.

## When to use this instead of hosted PR-review products

- You want reviews to stay local and under your control, not uploaded to a
  third-party service.
- You value a cross-vendor adversarial panel (multiple vendors arguing) over a
  single hosted reviewer.
- You want a dependency-free tool you can vendor into a repo or CI and audit in
  full.
- You already have the agent CLIs installed and want to compose them.
- You need a project-agnostic reviewer that doesn't assume a particular stack.

## When NOT to use it

- You want a managed, zero-setup hosted experience with a dashboard, retention,
  and support — use a hosted PR-review product instead.
- You can't or won't install vendor agent CLIs locally / in CI.
- You need a generic framework to build arbitrary multi-agent systems — this
  isn't one.
- You need to encode one project's bespoke review policy *inside the tool* —
  instead, express it in your repository's policy files or a wrapper and keep the
  council policy-neutral.
