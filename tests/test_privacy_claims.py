"""What the docs and the site say about where a diff goes (#860, #873).

Two claims drifted from the code. The site said "your code doesn't leave to a
third-party service" and ticked "no code leaves to a SaaS", while every
non-local seat sends the diff to its model vendor. README and SECURITY.md said
the only network activity was the agent CLIs and `gh`, while the jury itself
calls hosted-API and local model endpoints, and probes the default local server.
These checks keep the wrong phrasing out and the accurate sentences in. Stdlib
and offline: they read files.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import sys
import tempfile
import types
import unittest
import unittest.mock as mock
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ai_jury import adapters, cli, configtrust  # noqa: E402
from ai_jury.config import load_config  # noqa: E402

#: Every user-facing surface that makes a privacy or network claim. The
#: CHANGELOG is history and may quote the old wording; it is not listed.
SURFACES = [
    "README.md",
    "SECURITY.md",
    "llms.txt",
    "llms-full.txt",
    "website/index.html",
    "website/app.js",
    *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "docs").glob("*.md")),
]

#: "your code doesn't leave", "no data leaves", "nothing leaves your machine":
#: false unless every seat is local. The subject has to be the code, data or
#: diff (or "nothing"), so "the replay never leaves your browser" — a
#: client-side page, and true — is not caught.
NOTHING_LEAVES = re.compile(
    r"\b(?:code|data|diff)\b[^.;]{0,40}?\b(?:never leaves|doesn['’]t leave|does not leave"
    r"|won['’]t leave|will not leave|stays on your machine)"
    r"|\bno (?:code|data|diff) leaves|\bnothing leaves",
    re.IGNORECASE,
)

#: Marketing phrasing that was on the site and is not a property of the tool.
BANNED = ("100% data privacy",)


def leaks_claim(text: str) -> list[str]:
    """Sentences claiming nothing leaves, unless conditioned on every seat local."""
    found = []
    for sentence in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text)):
        if not NOTHING_LEAVES.search(sentence):
            continue
        lowered = sentence.lower()
        if "every seat" in lowered and "local" in lowered:
            continue  # "with every seat local, the diff does not leave" is true
        found.append(sentence)
    return found


NO_SERVER = (
    "no ai-jury server; your diff goes only to the model vendors you configure "
    "(or nowhere if every seat is local)"
)


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _flat(rel: str) -> str:
    """The file with runs of whitespace folded, so wrapped prose matches."""
    return re.sub(r"\s+", " ", _text(rel))


class TheLeakDetectorIsCalibrated(unittest.TestCase):
    def test_it_catches_the_false_forms(self):
        for sentence in (
            "There's no review server — your code doesn't leave to a third-party service.",
            "Local-first (no code leaves to a SaaS).",
            "Local-first (no data leaves to a SaaS).",
            "Your code never leaves your machine.",
            "Nothing leaves your machine.",
            "The diff does not leave your laptop.",
        ):
            with self.subTest(sentence):
                self.assertTrue(leaks_claim(sentence))

    def test_it_allows_the_true_ones(self):
        for sentence in (
            "With every seat local, the diff does not leave your machine.",
            "Replay an actual run — fully client-side; the file never leaves your browser.",
            "There is no ai-jury server; your diff goes only to the model vendors you configure.",
        ):
            with self.subTest(sentence):
                self.assertEqual(leaks_claim(sentence), [])


class NothingSaysTheDiffNeverLeaves(unittest.TestCase):
    def test_no_surface_says_code_or_data_does_not_leave(self):
        for rel in SURFACES:
            with self.subTest(rel):
                self.assertEqual(leaks_claim(_text(rel)), [], rel)

    def test_no_surface_uses_banned_phrasing(self):
        for rel in SURFACES:
            for phrase in BANNED:
                with self.subTest(rel=rel, phrase=phrase):
                    self.assertNotIn(phrase.lower(), _text(rel).lower())

    def test_the_accurate_sentence_is_where_the_claim_was(self):
        for rel in ("README.md", "SECURITY.md", "llms.txt", "website/index.html"):
            with self.subTest(rel):
                self.assertIn(NO_SERVER, _flat(rel).lower().replace("there is ", ""))
        # llms-full.txt words it as a bullet: "No ai-jury server: your diff goes …"
        self.assertIn(
            "no ai-jury server: your diff goes only to the model vendors you configure "
            "(or nowhere if every seat is local)",
            _flat("llms-full.txt").lower().replace("*", ""),
        )

    def test_both_site_faq_copies_carry_it(self):
        # The FAQ is rendered twice: the visible <details> and the JSON-LD
        # FAQPage search engines read. Both said the code does not leave.
        self.assertEqual(_flat("website/index.html").count("no ai-jury server"), 2)


class TheNetworkListNamesEverythingTheToolCalls(unittest.TestCase):
    NAMES = (
        "agent CLIs",
        "hosted-API endpoints",
        "local model endpoints",
        "`gh`",
    )

    def test_readme_and_security_list_every_destination(self):
        for rel in ("README.md", "SECURITY.md"):
            text = _flat(rel)
            with self.subTest(rel):
                self.assertNotIn("The only network activity", text)
                self.assertIn("Network traffic goes to:", text)
                start = text.index("Network traffic goes to:")
                listing = text[start : start + 900]
                for name in self.NAMES:
                    self.assertIn(name, listing)
                # The loopback probe the zero-config path makes, by the address
                # the code actually uses.
                self.assertIn(adapters._DEFAULT_LOCAL_ENDPOINT, listing)
                self.assertIn("no telemetry", text.lower())

    #: When the default loopback listing happens, as README and SECURITY.md say.
    #: `TheLoopbackListingHappensWhereTheDocsSay` holds the code to it.
    WHEN = (
        "by every `jury init`",
        "by `jury --doctor` when no reviewer is available",
        "by a run with no `jury.toml` and no usable agent CLI",
        "`jury init --local-endpoint URL` asks `URL/models`",
        "only with `JURY_ALLOW_REMOTE_ENDPOINT=1` set",
    )

    def test_the_loopback_listing_says_when(self):
        for rel in ("README.md", "SECURITY.md"):
            text = _flat(rel)
            with self.subTest(rel):
                for clause in self.WHEN:
                    self.assertIn(clause, text)
                self.assertNotIn("`jury init` always", text)

    def test_llms_full_lists_every_destination(self):
        text = _flat("llms-full.txt")
        for name in (
            "agent CLIs",
            "hosted-API and local model endpoints",
            "`gh`",
            "localhost:11434",
            "JURY_ALLOW_REMOTE_ENDPOINT",
        ):
            self.assertIn(name, text)

    def test_positioning_no_longer_says_network_only_when_you_ask(self):
        text = _flat("docs/positioning.md")
        self.assertNotIn("Network only when you ask", text)
        self.assertNotIn("only talks to the network when", text)
        for name in ("hosted-API and local model endpoints", "`gh`"):
            self.assertIn(name, text)


class TheLoopbackListingHappensWhereTheDocsSay(unittest.TestCase):
    """The when-clauses above, measured: every HTTP request each path makes."""

    DEFAULT = f"{adapters._DEFAULT_LOCAL_ENDPOINT}/models"

    @contextlib.contextmanager
    def _record(self, which=lambda _cmd: None, env=None):
        urls: list[str] = []

        def fake_open(target, timeout):
            del timeout
            urls.append(getattr(target, "full_url", target))
            raise urllib.error.URLError("refused")

        def no_spawn(*_a, **_k):
            raise OSError("not spawned in tests")

        with tempfile.TemporaryDirectory() as tmp:
            prev = Path.cwd()
            os.chdir(tmp)
            try:
                with (
                    mock.patch.object(adapters, "_open", fake_open),
                    mock.patch.object(adapters, "_spawn", no_spawn),
                    mock.patch.object(configtrust, "record_trust"),
                    mock.patch("shutil.which", which),
                    mock.patch.dict(os.environ, env or {}, clear=False),
                    mock.patch.object(sys, "stdin", io.StringIO("")),
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    yield urls, Path(tmp)
            finally:
                os.chdir(prev)

    @staticmethod
    def _clis(cmd):
        return f"/usr/bin/{cmd}" if cmd in ("claude", "codex") else None

    def test_every_jury_init_asks_the_default_server(self):
        for argv in (["--agents", "claude"], ["--preset", "balanced"], []):
            with self.subTest(argv), self._record() as (urls, tmp):
                cli._run_init([*argv, "-o", str(tmp / "j.toml")])
                self.assertIn(self.DEFAULT, urls)

    def test_a_local_endpoint_is_asked_only_when_loopback_or_opted_in(self):
        remote = "http://gpu.example:8080/v1"
        with self._record() as (urls, _tmp):
            cli._run_init(["--list-models", "--local-endpoint", remote])
        self.assertEqual(urls, [])
        with self._record(env={"JURY_ALLOW_REMOTE_ENDPOINT": "1"}) as (urls, _tmp):
            cli._run_init(["--list-models", "--local-endpoint", remote])
        self.assertEqual(urls, [f"{remote}/models"])

    def test_doctor_asks_only_when_no_reviewer_is_available(self):
        with self._record() as (urls, _tmp):
            cli.main(["--doctor"])
        self.assertIn(self.DEFAULT, urls)
        with self._record(which=self._clis) as (urls, _tmp):
            cli.main(["--doctor"])
        self.assertEqual(urls, [])

    def test_a_run_asks_only_with_no_config_and_no_usable_cli(self):
        args = types.SimpleNamespace(config=None, mock=False)
        with self._record() as (urls, _tmp):
            cli._maybe_add_local_fallback(load_config(None), args, lambda _m: None)
        self.assertEqual(urls, [self.DEFAULT])
        with self._record(which=self._clis) as (urls, _tmp):
            cli._maybe_add_local_fallback(load_config(None), args, lambda _m: None)
        self.assertEqual(urls, [])
        with self._record() as (urls, tmp):
            (tmp / "jury.toml").write_text("[jury]\n", encoding="utf-8")
            cli._maybe_add_local_fallback(load_config(None), args, lambda _m: None)
        self.assertEqual(urls, [])


class TheSecurityDocStatesTheMeasuredPrecedence(unittest.TestCase):
    def test_precedence_and_modes_are_stated_not_deferred(self):
        text = _flat("docs/security.md")
        self.assertNotIn("its own precedence rule", text)
        self.assertIn("but the flag wins", text)
        self.assertIn("added before or after", text)
        self.assertIn("`--permission-mode manual`, `default` or `plan`", text)
        self.assertIn("accepts it as an alias of `manual`", text)


if __name__ == "__main__":
    unittest.main()
