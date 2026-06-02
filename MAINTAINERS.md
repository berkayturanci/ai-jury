# Maintainers and governance

This is a small, single-purpose open-source project. Governance is deliberately
lightweight: enough structure to set expectations, not so much that it gets in
the way. Nothing here promises a service level — the project is provided as-is
under the [LICENSE](LICENSE).

## Maintainers

| Name           | Role         | Areas                          |
| -------------- | ------------ | ------------------------------ |
| Berkay Turancı | Lead maintainer | Orchestration, releases, security triage |

A maintainer's job is to keep the project **small, inspectable, and
project-agnostic** — not to grow it indefinitely. Concretely, maintainers:

- triage incoming issues and pull requests (see cadence below);
- review changes for correctness, scope, and the project-agnostic principle;
- cut releases and keep the [CHANGELOG](CHANGELOG.md) honest;
- handle security reports privately (see [SECURITY.md](SECURITY.md));
- say "no" to scope that belongs in a downstream wrapper rather than here.

## Issue triage

Issues and PRs are triaged on a **best-effort** basis — typically within a week,
but there is no guaranteed response time. Triage assigns labels:

- **Priority.** `priority:high` (correctness, security, or a broken core path),
  `priority:medium` (most roadmap work), `priority:low` (nice-to-have).
- **Effort.** `effort:S` (one focused evening), `effort:M` (a few evenings /
  has sub-steps), `effort:L` (large; decompose before starting).
- **Area.** `area:cli`, `area:security`, `area:performance`, `area:docs`, etc.,
  describing the part of the project a change touches.
- **Type.** `enhancement`, `bug`, `documentation`, `roadmap`.

See the [ROADMAP](ROADMAP.md) for how milestones and `effort:` labels organize
the work into session-sized chunks.

## Release cadence

Releases are **as-needed**, not on a fixed calendar. A release is cut when a
coherent set of changes has landed and the [release readiness
checklist](docs/release-checklist.md) passes. Pre-1.0, expect frequent small
releases and occasional breaking changes (see below).

## Compatibility and deprecation policy

The project follows [semantic versioning](https://semver.org/) intent:

- **Pre-1.0 (`0.x`).** The CLI surface and `jury.toml` schema may change
  between minor versions. Breaking changes are called out in the
  [CHANGELOG](CHANGELOG.md). The [public CLI compatibility
  contract](tests/test_cli_contract.py) guards the flags that downstream
  automation depends on.
- **Deprecations.** When a flag or config key is renamed, the old name keeps
  working for at least one minor release and emits a warning, then is removed.
- **Config.** Unknown keys warn rather than fail by default (`--strict-config`
  turns warnings into errors), so a newer config stays loadable by an older
  build where possible.

## Decision-making

For day-to-day changes, a maintainer's review is enough. For **major roadmap
changes** — new milestones, a shift in scope, dropping a supported agent, or
anything that touches the project-agnostic principle:

1. Open an issue describing the change and the trade-off.
2. Allow time for discussion (a few days for non-urgent changes).
3. The lead maintainer makes the final call, recorded in the issue and, when it
   changes direction, in the [ROADMAP](ROADMAP.md).

There is no formal voting process; this is a small project and decisions favor
keeping it focused over adding surface area.

## Security reports

Do **not** open a public issue for a vulnerability. Follow the private process
in [SECURITY.md](SECURITY.md). Security fixes take priority over feature work,
and a new high-severity finding from CodeQL or OpenSSF Scorecard is treated as a
release blocker.

## Downstream and project-specific requests

This project stays **project-agnostic**. Requests that encode a specific
repository's workflow, an organization's process, or a private integration are
generally declined *in core* and pointed at the right home instead:

- Need it for your repo? Wrap the `jury` CLI in your own automation — the
  [cookbook](docs/cookbook.md) shows the integration points.
- Think it's broadly useful? Open a feature request framing it generically (see
  [CONTRIBUTING.md](CONTRIBUTING.md)), and explain why it belongs in core rather
  than in a wrapper.

This keeps the jury reusable across projects instead of accreting any single
project's assumptions.
