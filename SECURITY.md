# Security Policy

## Supported Versions

Only the latest released version of **ai-jury** is supported with security
updates.

| Version | Supported |
| ------- | --------- |
| Latest release | Yes |
| Earlier releases | No |

## Reporting a Vulnerability

Please do **not** open a public issue for security vulnerabilities. Report them
privately to the maintainer:

- Email: [berkayturanci@gmail.com](mailto:berkayturanci@gmail.com)

Please include a clear description, reproduction steps, potential impact, and any
suggested fix. We will acknowledge the report within 48 hours when possible.

## Security Notes

This tool invokes local agent CLIs and may pass PR diffs or repository context to those
tools. Review your configured agent CLIs, authentication state, and `jury.toml`
before running it on sensitive repositories.

**A project's `jury.toml` can run commands, so it is trusted like code.** A `[[agent]]` with a `command` runs that program when a review runs. When `jury` auto-discovers `./jury.toml` in a directory you did not write — a clone, a fork's pull-request branch — a hostile config could run an arbitrary command. So an *auto-discovered* config that defines any `command` seat — every CLI seat, `claude` and `codex` included — is refused until it is trusted (off a terminal, which includes a piped `git diff | jury --diff-file -` and CI, with exit 2): pass `--config ./jury.toml` if you trust it, set `JURY_TRUST_PROJECT_CONFIG=1`, or confirm once at a terminal (remembered per file, by content). `jury init` trusts the config it writes, so the ordinary flow never prompts. Two existing opt-ins harden the command further: `JURY_REQUIRE_ABSOLUTE_COMMAND=1` rejects even a bare name, and a relative-path `command` is always refused.

**Fail-soft is not a multi-vendor guarantee.** If you gate a merge on this
tool's verdict, note that adapters fail soft by design: an agent that is
missing, broken, unauthenticated, or whose CLI flags changed under it is
skipped and the run continues. A verdict from a panel that collapsed to one
vendor is not a cross-vendor verdict, and it is not visually distinguishable
from one that is. If your process depends on the cross-vendor property, keep
the `--min-vendors` guard on (it is, by default — exit 3 when too few vendors
contributed) rather than opting out with `--no-min-vendors`, and treat a green
`jury --doctor` as evidence of *reachability*, never of participation.

## Jury data flow & redaction

What the jury sends to each configured agent is deliberately narrow.

**By default, only the diff is sent.** In the default `diff-only` context mode each
agent receives the unified diff under review and nothing else. In `expanded` mode the
agent additionally receives the PR title and body. In **neither** mode does the jury
send:

- source files outside the diff,
- repository history (commits, branches, blame), or
- environment variables or shell state.

That is what the **jury** sends. What an agent CLI can reach **on its own**, once
started, depends on the seat (details and measurements in
[docs/security.md](docs/security.md#other-agents)):

- the shipped **`claude`** seat has no tools at all — no file reads, no shell,
  no network, no MCP servers — and loads no CLAUDE.md, hooks, skills or plugins,
  its user's or a checkout's (`--tools ""`, a deny list naming every write, shell,
  read, network and subagent tool, `--strict-mcp-config`, `--safe-mode`,
  `--no-session-persistence`, `--permission-mode dontAsk`);
- the shipped **`codex`** seat (`-s read-only`) cannot write and its shell has no
  network, but it can read any file your user can read by absolute path, the MCP
  servers enabled in your own `~/.codex/config.toml` still load, and your global
  `~/.codex/AGENTS.md` is in its context;
- **`agy` is opt-in only and not in the default panel.** An agy seat
  (`--sandbox --dangerously-skip-permissions`) is not confined by `--sandbox`: it
  can read and write files and reach the network from its terminal. Seat it only
  by name, for diffs you trust; every run with an enabled agy seat warns, and
  `--strict` fails;
- a bring-your-own **`cli`/`xai`** seat runs with whatever permissions its own
  flags give it.

A reviewer is prompted with attacker-controlled content, and what it reads can
surface in the review text the jury posts. The default panel is `claude` +
`codex`. The native `claude`, `codex` and `agy` seats start every read-only call — each panel call, and `jury run-agent`'s
review/gate/chair roles — in a fresh, empty temporary directory rather than the
repository under review, so files the repository carries for them (`CLAUDE.md`,
`.claude/settings.json` and its hooks, `.mcp.json`, `.env`) are not picked up; a
bring-your-own `cli`/`xai` seat runs in the directory `jury` was started from.

**Secret redaction is on by default.** Before any text is handed to an agent, it is
scanned by `redaction.py` and recognized secrets are replaced with
`[REDACTED:<kind>]`. The recognized secret shapes — every kind in
`redaction._PATTERNS`, applied in this order, most specific first — are:

| Kind | What it matches |
| ---- | --------------- |
| `pem_private_key` | PEM private-key blocks (`-----BEGIN ... PRIVATE KEY-----` … `-----END ... PRIVATE KEY-----`, incl. RSA/EC/OPENSSH/DSA/PGP) |
| `aws_access_key` | AWS access key IDs (`AKIA` + 16 chars) |
| `github_token` | GitHub tokens (`ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_` + 20 or more chars) |
| `openai_key` | OpenAI-style keys (`sk-…`, including `sk-proj-…` / `sk-svcacct-…` / `sk-admin-…`) |
| `bearer_token` | `Bearer <token>` authorization values |
| `slack_token` | Slack tokens (`xoxb-`/`xoxa-`/`xoxp-`/`xoxr-`/`xoxs-` …) |
| `google_api_key` | Google API keys (`AIza` + 35 chars) |
| `stripe_key` | Stripe keys (`sk_`/`rk_`/`pk_` + `live_`/`test_` …) |
| `github_pat` | GitHub fine-grained personal access tokens (`github_pat_…`) |
| `jwt` | JSON Web Tokens (`eyJ…` header, three dot-separated parts) |
| `sendgrid_key` | SendGrid API keys (`SG.` + two dot-separated parts) |
| `pypi_token` | PyPI API tokens (`pypi-…`) |
| `npm_token` | npm access tokens (`npm_` + 36 chars) |
| `slack_webhook` | Slack incoming-webhook URLs (`https://hooks.slack.com/services/…`) |
| `basic_auth` | Credentials in a URL's userinfo: the password in `scheme://user:password@host` (an empty user too), and a bare token in `scheme://token@host` (the scheme, user and host are kept) |
| `secret_assignment` | Generic assignments whose key contains `api_key`, `secret`, `token`, `password`/`passwd`, `access_key`, `account_key`, `private_key`, `client_secret` or `credential`, with a value of 16 or more token characters (`key = "…"`; the value is redacted, the key, separator and quotes preserved) |

A secret in any other shape is not recognized and is sent as written: redaction
narrows what a prompt-injected reviewer could repeat, it is not a guarantee that
no secret reaches an agent.

**Toggles.**

- `--context-mode {diff-only,expanded}` — choose what context is sent (default
  `diff-only`).
- `--redact` / `--no-redact` — enable or disable secret redaction. Redaction is on by
  default; `--no-redact` sends the raw text and is intended only for trusted local use.
- The same behavior can be configured under `[jury.context]` in `jury.toml`
  (e.g. `mode = "expanded"`, `redact_secrets = true`).

The `--doctor` diagnostics report is independent of this path: it never includes the
diff or any agent output, and applies the same redaction to config values it prints
(see below).

## Diagnostics & telemetry

This project collects and transmits **no telemetry** of any kind — there is no
analytics, no usage reporting, and no opt-in data collection. The tool never
phones home. There is no ai-jury server; your diff goes only to the model endpoints you configure — vendor CLIs and APIs, or your own model server for local seats.

Network traffic goes to:

- the **agent CLIs** you configure, each of which talks to its own vendor;
- the **hosted-API endpoints** you configure (Anthropic, OpenAI, Google, xAI, or
  any OpenAI-compatible URL), which the jury calls itself with the prompt, diff included;
- the **local model endpoints** you configure, which the jury calls itself the same
  way;
- **`gh`**, for `--pr` and `--issue` (fetching, posting, labelling);
- and the default local model server on loopback (`http://localhost:11434/v1`),
  to offer a free offline reviewer: every `jury init` checks whether it is reachable
  (a status-only request), except `jury init --list-models`, which lists its models
  instead; `--list-agents` and seating a local reviewer list them after that check;
  `jury --doctor` lists them when no reviewer is available, and so does a run with
  no `jury.toml`, no `--config`, not `--mock`, and no usable agent CLI. `jury init --local-endpoint URL` asks
  `URL/models` for those listings instead (`--list-models`, `--list-agents`, and
  seating a local reviewer: interactive, `--wizard`, `--agents qwen`, `--preset
  offline`); a URL that is not loopback is asked only with
  `JURY_ALLOW_REMOTE_ENDPOINT=1` set.

The `jury --doctor` command produces a local diagnostics report intended to
be safe to share when filing a bug report. It includes the tool/Python/OS
versions, a config summary (rounds, chair, context mode, enabled agents) with
secret-like values redacted via `redaction.py`, agent availability on `PATH`,
and detected config warnings. It **never** includes the diff under review or any
agent output. The report is printed locally and only written to disk when you
pass `jury --doctor --write PATH`.
