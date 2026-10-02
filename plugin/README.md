# AI Jury

AI Jury convenes a panel of code reviewers from different vendors on the same diff, pull
request or issue. Each reviewer works independently, the reviewers cross-examine each
other's findings, a verification round checks those findings against the diff, and the
run ends in one verdict: a chair's synthesis or a panel vote.

This plugin contains one skill, `ai-jury`. The skill tells your coding agent when and how
to run the `jury` command-line tool and how to summarise its report. The plugin has no
hooks, no MCP server, no commands and no executable code of its own.

## Requirements

The skill drives the `jury` command-line tool, which is **installed separately** and is
not part of this plugin. It needs Python 3.11 or newer and has no runtime dependencies.
Install it from PyPI with any one of:

```bash
pipx install ai-jury
uv tool install ai-jury
pip install ai-jury
```

Homebrew users can run `brew install berkayturanci/ai-jury/ai-jury` instead.

The jury also needs at least one reviewer it can reach: the Claude Code CLI (`claude`) or
the Codex CLI (`codex`), a hosted model API key, or a local model server such as Ollama.
Reviewing or commenting on GitHub pull requests uses the GitHub CLI (`gh`), signed in.
Run `jury --doctor` to see what is available; it collects and sends no telemetry.

## Data flow

Everything below is done by the `jury` tool when the skill runs it. The plugin itself
reads, sends and writes nothing.

**What it reads.** The change under review: a diff file or standard input (for example
the output of `git diff`), or a pull request or issue fetched with `gh pr diff`,
`gh pr view` and `gh issue view`. For a pull request the title and description are
fetched too; they are sent only with `[jury.context] mode = "expanded"`, and by default
only the diff is. It reads a `jury.toml` configuration file from the repository when one
exists.

**What it sends, and to whom.** The diff, and any pull request or issue text it read, is
sent to **every reviewer you configure**, and each one is operated by its vendor:

- Agent CLIs run as local processes, which send the prompt to their vendor's service
  under your existing sign-in: `claude` (Anthropic), `codex` (OpenAI), and `agy`
  (Google Antigravity, only when a `jury.toml` names it).
- Hosted API seats are called over HTTPS: `api.anthropic.com`, `api.openai.com`,
  `generativelanguage.googleapis.com` and `api.x.ai`, or another OpenAI-compatible
  endpoint you configure (OpenRouter, DeepSeek, Groq and others; a non-local endpoint
  must be allowed with `JURY_ALLOW_REMOTE_ENDPOINT=1`). Each seat reads its key from an
  environment variable (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`,
  `XAI_API_KEY`, or the variable a `jury.toml` names) and sends that key only to that
  seat's own endpoint.
- A local model seat posts to the endpoint you configure, by default a server on your own
  machine.

Before anything is sent, common secret shapes (API keys, tokens, private keys) are
redacted from the prompt; this is on by default. Agent CLIs are started read-only and
sandboxed where the vendor supports it (`agy` cannot be confined, which is why it is
opt-in). There is no telemetry and no other network traffic.
The optional `--hints` pre-pass runs `ruff` and, in a JavaScript project, `npx eslint`,
which may download ESLint.

**What it writes.** By default only the report, to standard output or to the file named
with `-o`. Nothing is posted anywhere unless you ask: `--post` adds one comment to the pull
request or issue, `--post-inline` adds inline review comments, and `--label` applies
labels, all through `gh`. `--cache` stores run results under `~/.cache/ai-jury`.
`jury init` writes a `jury.toml`, and `jury apply` writes suggested patches into your
working tree only after showing them and asking.

## Using it

Ask your agent to "convene the review jury" on the current branch, a pull request or an
issue. The skill runs `jury`, then reports the verdict, the findings two or more reviewers
agreed on, and any disputed findings worth a human decision. A run costs tokens with every
vendor on the panel, so the skill avoids re-running the jury on an unchanged diff.

## More

- Source, issues and full documentation: <https://github.com/berkayturanci/ai-jury>
- Security model: <https://github.com/berkayturanci/ai-jury/blob/main/docs/security.md>
- License: MIT, in [`LICENSE`](LICENSE).
