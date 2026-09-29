"""The seats we ship as samples are read-only where they can be and current (#859, #870).

A sample config is what a reader copies, so it is the configuration most seats
actually run with. Two things went wrong in them at once:

* **#859.** The Cursor samples passed ``--force``, which approves every command,
  and the Aider samples passed ``--message`` alone, which is aider's editing mode
  (and aider commits its edits by default). A ``cli`` seat is one jury cannot
  confine — ``privilege.py`` knows no sandbox flag for an arbitrary binary and
  warns about every such seat — so the argv in the sample *is* the whole
  defence. Each sample now asks the CLI for its own read-only mode, and every
  place a bring-your-own CLI seat is shown says it is
  "unsandboxed — runs with your permissions".
* **#870.** The site's samples named models the vendors have retired or no
  longer list (``claude-3-7-sonnet-20250219``, ``gpt-4o``, ``grok-2-latest`` …)
  and one card claimed a context window nothing sourced. The ids are now one
  table, :data:`ai_jury.scaffold.SAMPLE_MODELS`, and every surface is held to it.

The surfaces are read as a reader sees them: TOML fences in the docs, the
example configs, and the site's cards rebuilt from ``website/app.js`` by the
same string concatenation the browser performs.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import cli, scaffold  # noqa: E402
from ai_jury.config import GENERIC_CLI_VENDORS, AgentSpec, adapter_key  # noqa: E402
from ai_jury.privilege import audit_agent  # noqa: E402

ROOT = Path(__file__).parent.parent
APP_JS = ROOT / "website" / "app.js"
INDEX_HTML = ROOT / "website" / "index.html"

#: The words that label a seat jury cannot confine, wherever it is shown.
LABEL = "unsandboxed — runs with your permissions"

#: Files a reader copies a ``[[agent]]`` block from.
TOML_SURFACES = (
    "README.md",
    "jury.toml",
    "llms-full.txt",
    "examples/jury.toml",
    "docs/configuration.md",
    "docs/cookbook.md",
    "docs/security.md",
)

#: Flags that approve an agent's actions without asking, in the CLIs the samples
#: drive: cursor-agent's ``--force``/``-f`` and its alias ``--yolo``, aider's
#: ``--yes-always``, and the claude/codex bypasses for completeness.
AUTO_APPROVE_FLAGS = frozenset(
    {
        "--force",
        "-f",
        "--yolo",
        "--yes-always",
        "--dangerously-skip-permissions",
        "--dangerously-bypass-approvals-and-sandbox",
        "--full-auto",
    }
)

#: Model ids the samples must not name, with where that was established
#: (checked 2026-09-28 against the vendor's public pages, no API key).
OUTDATED_MODEL_IDS: dict[str, str] = {
    "claude-3-7-sonnet-20250219": "Anthropic model deprecations: retired 2026-02-19",
    "claude-3-5-sonnet": "Anthropic model deprecations: both snapshots retired",
    "claude-sonnet-4-5": "Anthropic model deprecations: retirement not sooner than 2026-09-29",
    "anthropic/claude-3.5-sonnet": "not in OpenRouter's /api/v1/models",
    "anthropic/claude-3-5-sonnet": "not in OpenRouter's /api/v1/models",
    "anthropic/claude-3.7-sonnet": "not in OpenRouter's /api/v1/models",
    "gpt-4o": "not on OpenAI's models page; gpt-4o-2024-05-13 shuts down 2026-10-23",
    "gpt-5-codex": "OpenAI deprecations: shut down 2026-07-23",
    "gemini-2.5-pro": "superseded on Google's Gemini API models page",
    "grok-2-latest": "not on xAI's models page",
    "deepseek-chat": "not on DeepSeek's pricing page",
    "deepseek-reasoner": "not on DeepSeek's pricing page",
    "deepseek-coder": "not on DeepSeek's pricing page",
    "moonshot-v1-8k": "not on Moonshot's pricing page",
    "deepseek-ai/DeepSeek-Coder-V2-Lite": "no such Hugging Face repo (the -Instruct one exists)",
}

#: Claims the site made that no source backs.
UNSOURCED_CLAIMS = ("2M token", "$0.27 / 1M", "300+ tok/s")

#: Model names the site's card titles and badges used for retired models.
STALE_MODEL_NAMES = ("GPT-4o", "o1 /", "/ o1", "Grok-2", "Sonnet 3.7", "Gemini 2.5")

#: Where the outdated-id scan looks: everything a reader can see except the
#: changelog, which records what earlier releases shipped.
SCANNED = (
    "README.md",
    "jury.toml",
    "llms.txt",
    "llms-full.txt",
    "examples",
    "docs",
    "website",
    "skills",
    "src/ai_jury",
)
SCANNED_SUFFIXES = frozenset({".md", ".toml", ".txt", ".js", ".html", ".py", ".json", ".yml"})


# --------------------------------------------------------------------------- #
# Reading the surfaces                                                         #
# --------------------------------------------------------------------------- #


class Seat:
    """One ``[[agent]]`` block as a reader would copy it."""

    def __init__(self, where: str, label_lines: list[str], body: list[str]):
        self.where = where
        #: The comment lines directly above ``[[agent]]`` and inside the block.
        self.comments = [ln for ln in label_lines + body if ln.lstrip().startswith("#")]
        self.data = tomllib.loads("\n".join(["[[agent]]", *body]))["agent"][0]
        commented = [
            m.group(1) for ln in body if (m := re.match(r'\s*#\s*model\s*=\s*"([^"]+)"', ln))
        ]
        #: The model id, written or shown commented out (``# model = "…"``).
        self.model = self.data.get("model") or (commented[0] if commented else None)

    @property
    def args(self) -> list[str]:
        return [str(a) for a in self.data.get("extra_args", [])]

    @property
    def is_cli(self) -> bool:
        return adapter_key(self.data.get("vendor", ""), self.data.get("adapter")) in (
            GENERIC_CLI_VENDORS
        )

    @property
    def labelled(self) -> bool:
        return any(LABEL in c for c in self.comments)

    def __repr__(self) -> str:
        return f"{self.where}: {self.data.get('name')!r}"


def _value_after(args: list[str], flag: str) -> str | None:
    """The token after *flag* in *args*, or ``None`` when *flag* is absent."""
    if flag not in args:
        return None
    rest = args[args.index(flag) + 1 :]
    return rest[0] if rest else None


def _toml_texts(rel: str) -> list[str]:
    text = (ROOT / rel).read_text(encoding="utf-8")
    if rel.endswith(".toml"):
        return [text]
    return re.findall(r"```toml\n(.*?)```", text, flags=re.S)


def _seats_in(text: str, where: str) -> list[Seat]:
    """Split *text* into seats; a header ``[...]`` or blank run ends a block."""
    lines = text.splitlines()
    seats: list[Seat] = []
    for i, line in enumerate(lines):
        if line.strip() != "[[agent]]":
            continue
        above: list[str] = []
        j = i - 1
        while j >= 0 and lines[j].lstrip().startswith("#"):
            above.insert(0, lines[j])
            j -= 1
        body: list[str] = []
        for nxt in lines[i + 1 :]:
            stripped = nxt.strip()
            if stripped.startswith("["):
                break
            body.append(nxt)
        # Trailing comments belong to the next seat's label, not this body.
        while body and (not body[-1].strip() or body[-1].lstrip().startswith("#")):
            body.pop()
        seats.append(Seat(where, above, body))
    return seats


def _js_string(literal: str) -> str:
    """Decode one JS string literal (single- or double-quoted)."""
    inner = literal[1:-1]
    return re.sub(
        r"\\(u[0-9a-fA-F]{4}|.)",
        lambda m: (
            chr(int(m.group(1)[1:], 16))
            if m.group(1).startswith("u")
            else {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1))
        ),
        inner,
    )


def _js_array(src: str, name: str) -> list[str] | None:
    m = re.search(rf"var {name} = (\[.*?\]);", src, flags=re.S)
    if not m:
        return None
    return [_js_string(s) for s in re.findall(r'"(?:[^"\\]|\\.)*"', m.group(1))]


def _js_const(src: str, name: str) -> str | None:
    m = re.search(rf'var {name} = ("(?:[^"\\]|\\.)*");', src)
    return _js_string(m.group(1)) if m else None


def _eval_config(expr: str, src: str) -> str:
    """Evaluate a card's ``config:`` expression the way the browser does.

    The expressions are concatenations of string literals, the label constant
    and ``tomlArray(NAME)`` calls, so that is all this understands; anything
    else is left in as its source text, which then fails the TOML parse loudly.
    """
    out = []
    token = re.compile(
        r"""\s*(?:(?P<s>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")|tomlArray\((?P<a>\w+)\)|(?P<c>[A-Z_]+)|(?P<plus>\+))"""
    )
    pos = 0
    while pos < len(expr):
        m = token.match(expr, pos)
        if not m:
            out.append(expr[pos:])
            break
        pos = m.end()
        if m.group("s"):
            out.append(_js_string(m.group("s")))
        elif m.group("a"):
            items = _js_array(src, m.group("a")) or []
            out.append("[" + ", ".join(f'"{a}"' for a in items) + "]")
        elif m.group("c"):
            out.append(_js_const(src, m.group("c")) or m.group("c"))
    return "".join(out)


def _site_cards(src: str) -> dict[str, dict[str, str]]:
    """``id -> {config, desc, badge, name}`` for every integration card."""
    cards: dict[str, dict[str, str]] = {}
    for block in re.findall(r"\{\s*\n\s*id: \"[^\"]+\",.*?\n\s*\}", src, flags=re.S):
        fields: dict[str, str] = {}
        for key, expr in re.findall(r"\n\s*(\w+): (.*?),?\s*(?=\n)", block):
            fields[key] = _eval_config(expr.rstrip(","), src)
        cards[fields["id"]] = fields
    return cards


def _builder_agents(src: str) -> dict[str, dict[str, str]]:
    """The "Build your jury" presets: ``name -> {key: raw value}``."""
    m = re.search(r"var AGENTS = \{(.*?)\n\s*\};", src, flags=re.S)
    agents: dict[str, dict[str, str]] = {}
    if not m:
        return agents
    for name, body in re.findall(r"\n\s*(\w+):\s*\{(.*?)\}", m.group(1)):
        agents[name] = dict(re.findall(r'(\w+): ("[^"]*"|\w+)', body))
    return agents


def _all_seats() -> list[Seat]:
    seats: list[Seat] = []
    for rel in TOML_SURFACES:
        for text in _toml_texts(rel):
            seats.extend(_seats_in(text, rel))
    src = APP_JS.read_text(encoding="utf-8")
    for card_id, card in _site_cards(src).items():
        if "[[agent]]" in card.get("config", ""):
            seats.extend(_seats_in(card["config"], f"website/app.js card {card_id}"))
    return seats


def _slot(seat: Seat) -> str | None:
    """Which :data:`SAMPLE_MODELS` entry a seat's model has to match, if any."""
    vendor = seat.data.get("vendor", "")
    host = (urlsplit(seat.data.get("endpoint", "")).hostname or "").lower()
    if seat.is_cli:
        return None  # a CLI's own model namespace (e.g. cursor-agent --list-models)
    by_host = {
        "api.deepseek.com": "deepseek",
        "api.x.ai": "xai",
        "api.groq.com": "groq",
        "api.moonshot.cn": "moonshot",
        "api.moonshot.ai": "moonshot",
        "api.together.xyz": "together",
        "openrouter.ai": "openrouter",
    }
    if host in by_host:
        return by_host[host]
    return {
        "anthropic": "anthropic",
        "anthropic-api": "anthropic",
        "openai": "openai",
        "openai-api": "openai",
        "google-api": "google",
        "xai-api": "xai",
    }.get(vendor)


def config_validate(text: str, *, allow_remote: bool) -> tuple[int, str]:
    """Exit status and output of ``jury --config <file> --config-validate`` on *text*.

    ``allow_remote`` sets the documented opt-in, ``JURY_ALLOW_REMOTE_ENDPOINT=1``,
    which a non-loopback ``endpoint`` needs; without it the variable is unset.
    """
    workdir = tempfile.mkdtemp()
    try:
        path = Path(workdir) / "jury.toml"
        path.write_text(text, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "JURY_ALLOW_REMOTE_ENDPOINT"}
        if allow_remote:
            env["JURY_ALLOW_REMOTE_ENDPOINT"] = "1"
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.dict(os.environ, env, clear=True),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = cli.main(["--config", str(path), "--config-validate"])
    finally:
        shutil.rmtree(workdir, True)
    return code, out.getvalue() + err.getvalue()


# --------------------------------------------------------------------------- #
# #859 — bring-your-own CLI seats                                             #
# --------------------------------------------------------------------------- #


class CliSeatsAreReadOnlyAndLabelled(unittest.TestCase):
    """Every shipped ``cli`` seat is in its CLI's read-only mode, and says so."""

    def setUp(self):
        self.seats = _all_seats()
        self.cli = [s for s in self.seats if s.is_cli]

    def test_the_surfaces_are_read(self):
        # A reader that finds nothing proves nothing: the docs, the example
        # config and the site each carry Cursor and Aider seats.
        commands = {(s.where.split(" ")[0], s.data.get("command")) for s in self.cli}
        for where in ("docs/configuration.md", "docs/cookbook.md", "examples/jury.toml"):
            self.assertIn((where, "cursor-agent"), commands)
            self.assertIn((where, "aider"), commands)
        self.assertIn(("website/app.js", "cursor-agent"), commands)
        self.assertIn(("website/app.js", "aider"), commands)

    def test_no_cli_seat_passes_an_auto_approve_flag(self):
        offenders = [(s, sorted(AUTO_APPROVE_FLAGS & set(s.args))) for s in self.cli]
        self.assertEqual([o for o in offenders if o[1]], [])

    def test_every_cli_seat_is_labelled_unsandboxed(self):
        self.assertEqual([s for s in self.cli if not s.labelled], [])

    def test_cursor_seats_run_in_ask_mode(self):
        # `--print` alone "has access to all tools, including write and shell"
        # (cursor-agent --help); measured, `--print --trust` wrote a file.
        cursor = [s for s in self.cli if s.data.get("command") == "cursor-agent"]
        self.assertTrue(cursor)
        for seat in cursor:
            with self.subTest(seat=seat):
                args = seat.args
                in_ask = "--mode=ask" in args or any(
                    a == "--mode" and args[i + 1 : i + 2] == ["ask"] for i, a in enumerate(args)
                )
                self.assertTrue(in_ask, f"{seat} runs {args}")
                # Cursor's own sandbox, asked for explicitly: the user's global
                # config may have it off, and `--sandbox` "overrides config".
                self.assertEqual(_value_after(args, "--sandbox"), "enabled", seat)

    def test_cursor_seats_take_the_prompt_on_stdin(self):
        # #901: the samples mixed `arg` and stdin. `cursor-agent -p` reads its
        # prompt from stdin (2026.09.26: empty stdin stops with "No prompt
        # provided for print mode"; text on stdin reaches the model call), and
        # stdin has no size cap, while one argument is capped at 128 KiB on Linux.
        cursor = [s for s in self.cli if s.data.get("command") == "cursor-agent"]
        self.assertGreaterEqual(len(cursor), 6)
        for seat in cursor:
            with self.subTest(seat=seat):
                self.assertIn(seat.data.get("prompt_mode"), (None, "stdin"), seat)

    def test_aider_seats_ask_dry_run_and_never_commit(self):
        aider = [s for s in self.cli if s.data.get("command") == "aider"]
        self.assertTrue(aider)
        for seat in aider:
            with self.subTest(seat=seat):
                args = seat.args
                self.assertEqual(_value_after(args, "--chat-mode"), "ask", seat)
                for flag in ("--dry-run", "--no-auto-commits", "--no-dirty-commits"):
                    self.assertIn(flag, args, seat)
                if "--message" in args:
                    # The prompt is appended after it, so it must be last and
                    # the prompt must go on argv.
                    self.assertEqual(args[-1], "--message", seat)
                    self.assertEqual(seat.data.get("prompt_mode"), "arg", seat)

    def test_the_site_builder_seats_are_read_only_and_labelled(self):
        src = APP_JS.read_text(encoding="utf-8")
        agents = _builder_agents(src)
        cursor = _js_array(src, "CURSOR_READ_ONLY_ARGS") or []
        aider = _js_array(src, "AIDER_READ_ONLY_ARGS") or []
        self.assertEqual(_js_const(src, "UNSANDBOXED_LABEL"), LABEL)
        self.assertIn("ask", cursor)
        self.assertEqual(aider[-1:], ["--message"])
        for name, argv, mode in (
            ("cursor", "CURSOR_READ_ONLY_ARGS", None),
            ("aider", "AIDER_READ_ONLY_ARGS", '"arg"'),
        ):
            with self.subTest(seat=name):
                seat = agents.get(name, {})
                self.assertEqual(seat.get("extra_args"), argv)
                self.assertEqual(seat.get("unsandboxed"), "true")
                # Cursor reads its prompt from stdin; aider's `--message` needs argv (#901).
                self.assertEqual(seat.get("prompt_mode"), mode)
        self.assertFalse(AUTO_APPROVE_FLAGS & (set(cursor) | set(aider)))
        # …and the generated TOML writes both the argv and the label.
        self.assertIn(
            'if (a.unsandboxed) lines.push("# " + UNSANDBOXED_LABEL, UNSANDBOXED_HINT);', src
        )
        self.assertIn('lines.push("extra_args = " + tomlArray(a.extra_args));', src)

    def test_the_site_shows_the_label_where_the_seat_is_picked_and_described(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        for box in ("ag-cursor", "ag-aider"):
            with self.subTest(checkbox=box):
                m = re.search(rf'<label[^>]*><input[^>]*id="{box}"[^>]*/>(.*?)</label>', html)
                self.assertIsNotNone(m)
                self.assertIn(LABEL, m.group(1))
        cards = _site_cards(APP_JS.read_text(encoding="utf-8"))
        for card_id in ("cursor-cli", "aider", "generic-cli"):
            with self.subTest(card=card_id):
                self.assertIn(LABEL.lower(), cards[card_id]["desc"].lower())

    def test_jury_init_writes_aider_read_only_and_labelled(self):
        # `--read-only` is not an aider option; the old template wrote a seat
        # aider refuses to start, and `prompt_mode` was never written at all.
        config = scaffold.build_config(["aider"])
        text = scaffold.render_toml(config)
        seats = _seats_in(text, "jury init")
        self.assertEqual(len(seats), 1)
        seat = seats[0]
        self.assertNotIn("--read-only", seat.args)
        self.assertTrue(seat.labelled, text)
        self.assertEqual(seat.data.get("prompt_mode"), "arg", text)
        self.assertEqual(_value_after(seat.args, "--chat-mode"), "ask", text)
        self.assertIn("--dry-run", seat.args)

    def test_jury_init_labels_only_cli_seats(self):
        text = scaffold.render_toml(scaffold.build_config(["claude", "qwen", "aider"]))
        self.assertEqual(text.count(LABEL), 1)


# --------------------------------------------------------------------------- #
# #870 — model ids                                                            #
# --------------------------------------------------------------------------- #


def _scanned_files():
    for rel in SCANNED:
        path = ROOT / rel
        if path.is_file():
            yield path
            continue
        for f in sorted(path.rglob("*")):
            if f.is_file() and f.suffix in SCANNED_SUFFIXES and "vendor" not in f.parts:
                yield f


#: Claims the first round made about the CLI seats that their flags do not
#: support (#859 review): aider's flags do not close "every" route to a command —
#: its checkout config can turn on test/lint/load — and `--dry-run` gates edits
#: and commits, not every file write (the chat and input history are written).
#: "knows no sandbox flag" read as though cursor-agent had none; it has
#: `--sandbox`, jury just does not add or check one.
OVERCLAIMS = (
    "every path to an edit",
    "close every other route",
    "no file is modified",
    "modifies no file",
    "writes no file",
    "knows no sandbox flag",
)

#: Where the CLI seats are described, and so where an overclaim could live.
WORDING_SURFACES = (
    "docs/configuration.md",
    "docs/cookbook.md",
    "docs/security.md",
    "examples/jury.toml",
    "website/app.js",
    "src/ai_jury/scaffold.py",
)


def _unreleased() -> str:
    """The current release notes: [Unreleased], or the newest release right after a cut.

    A release cut moves every [Unreleased] entry under a version heading and leaves
    [Unreleased] empty; those notes are still the current wording, so an empty
    [Unreleased] means the newest released section (as in test_privacy_claims).
    """
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    head = text.index("## [Unreleased]")
    newest = text.index("\n## [", head + 1)
    if text[head:newest].strip() != "## [Unreleased]":
        return text[head:newest]
    end = text.find("\n## [", newest + 1)
    return text[newest : end if end != -1 else len(text)]


def _changelog_entry(ref: str) -> str:
    """The CHANGELOG bullet tagged ``(<ref>)``, whichever section it sits in.

    Its wording is fixed once written, so a claim about what that entry says reads the
    entry itself. Reading "the current notes" instead broke as soon as any other change
    added an [Unreleased] bullet, since the entry then sat in a released section.
    """
    lines = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("- **") and f"({ref})" in line]
    if len(starts) != 1:
        raise AssertionError(f"expected one CHANGELOG bullet tagged ({ref}), found {len(starts)}")
    body = [lines[starts[0]]]
    for line in lines[starts[0] + 1 :]:
        if line.startswith(("- **", "### ", "## ")) or not line.strip():
            break
        body.append(line)
    return "\n".join(body)


class CliSeatWordingIsScoped(unittest.TestCase):
    """What the CLI seats' flags control is said, and what they do not (#859)."""

    def test_no_surface_overclaims_what_the_flags_do(self):
        hits = []
        texts = {rel: (ROOT / rel).read_text(encoding="utf-8") for rel in WORDING_SURFACES}
        texts["CHANGELOG.md [Unreleased]"] = _unreleased()
        for where, text in texts.items():
            flat = " ".join(text.replace("#:", " ").replace("#", " ").split())
            hits += [f"{where}: {c!r}" for c in OVERCLAIMS if c in flat]
        self.assertEqual(hits, [])

    def test_every_aider_description_names_the_checkout_config(self):
        # aider reads .aider.conf.yml and .env from the checkout; those can turn
        # on test/lint/load commands no flag in the seat turns off.
        cards = _site_cards(APP_JS.read_text(encoding="utf-8"))
        texts = {
            rel: (ROOT / rel).read_text(encoding="utf-8")
            for rel in (
                "docs/configuration.md",
                "docs/cookbook.md",
                "docs/security.md",
                "examples/jury.toml",
                "src/ai_jury/scaffold.py",
            )
        }
        texts["website/app.js aider card"] = cards["aider"]["desc"]
        texts["CHANGELOG.md (#859) entry"] = _changelog_entry("#859")
        missing = [
            f"{where}: {needle}"
            for where, text in texts.items()
            for needle in (".aider.conf.yml", ".env")
            if needle not in text
        ]
        self.assertEqual(missing, [])

    def test_every_cursor_description_names_the_trusted_workspace_hooks(self):
        cards = _site_cards(APP_JS.read_text(encoding="utf-8"))
        texts = {
            rel: (ROOT / rel).read_text(encoding="utf-8")
            for rel in ("docs/configuration.md", "docs/cookbook.md", "docs/security.md")
        }
        texts["examples/jury.toml"] = (ROOT / "examples/jury.toml").read_text(encoding="utf-8")
        texts["website/app.js cursor card"] = cards["cursor-cli"]["desc"]
        self.assertEqual([w for w, t in texts.items() if ".cursor/" not in t], [])

    def test_every_cli_seat_description_says_trusted_checkouts_only(self):
        cards = _site_cards(APP_JS.read_text(encoding="utf-8"))
        texts = {
            rel: (ROOT / rel).read_text(encoding="utf-8")
            for rel in ("docs/configuration.md", "docs/cookbook.md", "docs/security.md")
        }
        texts["examples/jury.toml"] = (ROOT / "examples/jury.toml").read_text(encoding="utf-8")
        for card_id in ("cursor-cli", "aider"):
            texts[f"website/app.js {card_id} card"] = cards[card_id]["desc"]
        texts["jury init"] = scaffold.render_toml(scaffold.build_config(["aider"]))
        flat = {w: " ".join(t.replace("#", " ").split()) for w, t in texts.items()}
        self.assertEqual([w for w, t in flat.items() if "checkouts you trust" not in t], [])

    def test_the_audit_names_the_checkout_config_risk(self):
        aider = AgentSpec(name="aider", vendor="cli", command="aider")
        cursor = AgentSpec(name="gpt", vendor="openai", adapter="cli", command="/opt/cursor-agent")
        other = AgentSpec(name="mine", vendor="cli", command="my-tool")
        self.assertTrue(any(".aider.conf.yml" in w and ".env" in w for w in audit_agent(aider)))
        self.assertTrue(any(".cursor/hooks.json" in w for w in audit_agent(cursor)))
        self.assertFalse(any("checkouts you trust" in w for w in audit_agent(other)))
        # Named by path, on Windows too.
        windows = AgentSpec(name="aider", vendor="cli", command="C:\\tools\\aider.exe")
        self.assertTrue(any(".aider.conf.yml" in w for w in audit_agent(windows)))


class SampleModelIdsAreCurrent(unittest.TestCase):
    def test_no_surface_names_an_outdated_model_id(self):
        hits = []
        for f in _scanned_files():
            text = f.read_text(encoding="utf-8", errors="replace")
            for model in OUTDATED_MODEL_IDS:
                pattern = r"(?<![\w./:-])" + re.escape(model) + r"(?![\w.:/-])"
                for m in re.finditer(pattern, text):
                    line = text.count("\n", 0, m.start()) + 1
                    hits.append(f"{f.relative_to(ROOT)}:{line}: {model}")
        self.assertEqual(hits, [])

    def test_every_sample_seat_names_the_one_listed_id(self):
        # README, jury.toml, llms-full.txt, the docs, the example config and the
        # site's cards all name the id SAMPLE_MODELS holds for their kind of seat.
        checked = 0
        wrong = []
        for seat in _all_seats():
            slot = _slot(seat)
            if slot is None or seat.model is None:
                continue
            checked += 1
            if seat.model != scaffold.SAMPLE_MODELS[slot]:
                wrong.append(f"{seat}: {seat.model} (want {scaffold.SAMPLE_MODELS[slot]})")
        self.assertEqual(wrong, [])
        self.assertGreaterEqual(checked, 20)

    def test_the_site_and_readme_use_the_same_ids(self):
        src = APP_JS.read_text(encoding="utf-8")
        cards = _site_cards(src)
        readme = [s for t in _toml_texts("README.md") for s in _seats_in(t, "README.md")]
        # A seat with no model at all is `jury init`'s verbatim output, which pins
        # none (the CLI's own default runs); only an id the README names is checked.
        claude = [s.model for s in readme if s.data.get("vendor") == "anthropic" and s.model]
        local = [s.model for s in readme if s.data.get("vendor") == "local"]
        site_claude = _seats_in(cards["anthropic-api"]["config"], "site")[0].model
        site_local = _seats_in(cards["ollama-local"]["config"], "site")[0].model
        self.assertEqual(claude, [scaffold.SAMPLE_MODELS["anthropic"]])
        self.assertEqual(site_claude, scaffold.SAMPLE_MODELS["anthropic"])
        self.assertEqual(local, [scaffold.SAMPLE_MODELS["local"]])
        self.assertEqual(site_local, scaffold.SAMPLE_MODELS["local"])
        # The README's own reviewer ballot example names the same Claude id.
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f'"model": "{scaffold.SAMPLE_MODELS["anthropic"]}"', text)

    def test_the_site_builder_matches_jury_init(self):
        # The demo's hosted seats are the templates `jury init` writes.
        agents = _builder_agents(APP_JS.read_text(encoding="utf-8"))
        templates = scaffold.agent_templates()
        for name in ("qwen", "deepseek", "openrouter", "groq"):
            with self.subTest(seat=name):
                self.assertEqual(agents[name]["model"], f'"{templates[name]["model"]}"')
        self.assertEqual(agents["grok"]["model"], f'"{scaffold.SAMPLE_MODELS["xai"]}"')

    def test_the_site_writes_the_cli_seat_jury_init_writes(self):
        # #901 item 2: the site carried only the one-line label, not the two-line
        # hint `jury init` writes under it. The demo's aider block and the aider
        # card are now byte for byte the block `jury init --agents aider` writes.
        src = APP_JS.read_text(encoding="utf-8")
        hint = "\n".join(scaffold.UNSANDBOXED_HINT)
        self.assertEqual(_js_const(src, "UNSANDBOXED_HINT"), hint)
        rendered = scaffold.render_toml(scaffold.build_config(["aider"]))
        init_block = rendered[rendered.index(f"# {LABEL}") :].strip()

        # The demo, rendered the way its generator does (its source lines are
        # pinned in test_the_site_builder_seats_are_read_only_and_labelled).
        seat = _builder_agents(src)["aider"]
        demo = []
        if seat.get("unsandboxed") == "true":
            demo += [f"# {_js_const(src, 'UNSANDBOXED_LABEL')}", _js_const(src, "UNSANDBOXED_HINT")]
        demo += ["[[agent]]", 'name = "aider"', f"vendor = {seat['vendor']}"]
        demo.append(f"command = {seat['command']}")
        argv = _js_array(src, seat["extra_args"]) or []
        demo.append("extra_args = [" + ", ".join(f'"{a}"' for a in argv) + "]")
        demo.append(f"prompt_mode = {seat['prompt_mode']}")
        self.assertEqual("\n".join(demo), init_block)

        cards = _site_cards(src)
        self.assertEqual(cards["aider"]["config"].strip(), init_block)
        for card_id in ("cursor-cli", "generic-cli"):
            with self.subTest(card=card_id):
                self.assertTrue(
                    cards[card_id]["config"].startswith(f"# {LABEL}\n{hint}\n[[agent]]\n"),
                    cards[card_id]["config"],
                )

    def test_the_site_cli_argv_is_the_one_every_sample_uses(self):
        # One argv per CLI (#859 review): the site's aider argv is the template
        # `jury init` writes, and its cursor argv is every cursor sample's argv
        # once the model choice is taken out — so no copy can drift.
        src = APP_JS.read_text(encoding="utf-8")
        agents = _builder_agents(src)
        self.assertEqual(agents["aider"]["extra_args"], "AIDER_READ_ONLY_ARGS")
        self.assertEqual(agents["cursor"]["extra_args"], "CURSOR_READ_ONLY_ARGS")
        aider = _js_array(src, "AIDER_READ_ONLY_ARGS")
        cursor = _js_array(src, "CURSOR_READ_ONLY_ARGS")
        self.assertEqual(aider, scaffold.agent_templates()["aider"]["extra_args"])

        def without_model(args: list[str]) -> list[str]:
            out, skip = [], False
            for a in args:
                if skip:
                    skip = False
                elif a == "--model":
                    skip = True
                else:
                    out.append("--print" if a == "-p" else a)
            return out

        drift = []
        for seat in _all_seats():
            command = seat.data.get("command")
            want = {"aider": aider, "cursor-agent": cursor}.get(command)
            if want is not None and without_model(seat.args) != want:
                drift.append(f"{seat}: {seat.args}")
        self.assertEqual(drift, [])

    def test_the_site_makes_no_unsourced_or_stale_model_claims(self):
        src = APP_JS.read_text(encoding="utf-8")
        cards = _site_cards(src)
        shown = " ".join(c.get(k, "") for c in cards.values() for k in ("name", "badge", "desc"))
        self.assertEqual([c for c in UNSOURCED_CLAIMS if c in shown], [])
        self.assertEqual([n for n in STALE_MODEL_NAMES if n in shown], [])


class TheSiteCardsPassConfigValidate(unittest.TestCase):
    """Every integration card's ``[[agent]]`` block is a config `jury` accepts (docs audit
    2026-09-29).

    A card is copied as it stands, so each is held to ``jury --config-validate`` — with
    ``JURY_ALLOW_REMOTE_ENDPOINT=1`` exactly when the card's own run command sets it, and
    that opt-in must be one the card needs, not decoration. The Grok card was a remote
    ``openai-compatible`` seat needing the opt-in; xAI has its own hosted adapter.
    """

    def test_every_agent_card_validates_as_its_command_runs_it(self):
        cards = _site_cards(APP_JS.read_text(encoding="utf-8"))
        checked = 0
        for card_id, card in cards.items():
            config = card.get("config", "")
            if "[[agent]]" not in config or card.get("cat") == "cicd":
                continue  # the Actions card is YAML + TOML; test_action_example covers it
            checked += 1
            remote = "JURY_ALLOW_REMOTE_ENDPOINT=1" in card.get("command", "")
            with self.subTest(card=card_id):
                code, out = config_validate(config, allow_remote=remote)
                self.assertEqual(code, 0, f"{out}\n{config}")
                if remote:
                    self.assertEqual(config_validate(config, allow_remote=False)[0], 2, config)
        self.assertGreaterEqual(checked, 15)

    def test_the_grok_card_is_the_first_class_xai_seat(self):
        cards = _site_cards(APP_JS.read_text(encoding="utf-8"))
        seat = _seats_in(cards["xai-grok-api"]["config"], "site")[0]
        self.assertEqual(seat.data.get("vendor"), "xai-api")
        self.assertNotIn("endpoint", seat.data)
        self.assertNotIn("JURY_ALLOW_REMOTE_ENDPOINT", cards["xai-grok-api"]["command"])


if __name__ == "__main__":
    unittest.main()
