"""The site's "Build your jury" demo writes configs `jury` accepts (docs audit 2026-09-29).

The builder on ai-jury.dev is the jury.toml a first-time reader copies. The audit found it
wrote configs that said one thing and ran another:

* **S4.** Choosing "Panel vote" changed the demo's verdict but never wrote
  ``decision = "vote"``, so the copied file ran a chair verdict.
* **S5.** The DeepSeek, OpenRouter and Groq seats point at remote endpoints, which
  ``jury --config-validate`` and a run refuse unless ``JURY_ALLOW_REMOTE_ENDPOINT`` is
  set; nothing in the generated file said so.
* **S18.** Grok was written as an ``openai-compatible`` seat, though xAI has its own
  hosted adapter (``vendor = "xai-api"``, #701) that needs no endpoint and no opt-in.
* **Theme 1.** The agy seat carried neither the argv `jury init` writes nor a word about
  agy being opt-in only.

These tests run the builder's own ``builderToml`` from ``website/app.js`` under node,
over every seat and option, and hold each output to ``jury --config-validate``. The same
driver runs the page's loaded-run vote re-tally (S19: an ``info`` finding votes APPROVE,
as in ``voting.tally_votes``) and the demo comments' attribution footer (S20: the one
``report.render_footer`` writes).
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from test_sample_configs import config_validate  # noqa: E402

from ai_jury import scaffold  # noqa: E402
from ai_jury.adapters import AgentResult  # noqa: E402
from ai_jury.report import render_footer  # noqa: E402
from ai_jury.voting import tally_votes  # noqa: E402

ROOT = Path(__file__).parent.parent
APP_JS = ROOT / "website" / "app.js"

#: Every seat the builder offers, in its checkbox order.
SEATS = (
    "claude",
    "codex",
    "agy",
    "qwen",
    "deepseek",
    "openrouter",
    "groq",
    "grok",
    "cursor",
    "aider",
)
#: The seats whose endpoint is not loopback.
REMOTE = ("deepseek", "openrouter", "groq")

#: Pulls named functions and `var`s out of app.js and runs them in a bare context.
#: The builder, the vote re-tally and the comment renderer are closures inside the
#: page's IIFE; they touch no DOM except `$` (stubbed to catch `innerHTML`).
_DRIVER = r"""
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[2], "utf8");
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
function skipString(i) {
  const q = src[i];
  for (i++; src[i] !== q; i++) { if (src[i] === "\\") i++; }
  return i;
}
function balancedEnd(i, stopAtSemicolon) {
  let depth = 0;
  for (; i < src.length; i++) {
    const ch = src[i];
    if (ch === '"' || ch === "'") { i = skipString(i); continue; }
    if (ch === "/" && src[i + 1] === "/") { while (src[i] !== "\n") i++; continue; }
    if ("([{".includes(ch)) depth++;
    else if (")]}".includes(ch)) { depth--; if (depth === 0 && !stopAtSemicolon) return i + 1; }
    else if (ch === ";" && depth === 0 && stopAtSemicolon) return i + 1;
  }
  throw new Error("unbalanced source");
}
function fn(name) {
  const start = src.indexOf("function " + name + "(");
  if (start < 0) throw new Error("no function " + name);
  const body = src.indexOf("{", src.indexOf(")", start));
  return src.slice(start, balancedEnd(body, false));
}
function v(name) {
  const start = src.indexOf("var " + name + " = ");
  if (start < 0) throw new Error("no var " + name);
  return src.slice(start, balancedEnd(start, true));
}
const VARS = ["UNSANDBOXED_LABEL", "UNSANDBOXED_HINT", "CURSOR_READ_ONLY_ARGS", "AIDER_READ_ONLY_ARGS",
  "AGY_ARGS", "AGY_OPT_IN_NOTE", "REMOTE_ENDPOINT_NOTE", "AGENTS", "LABEL", "SEV_RANK", "VOTE_VOCAB",
  "VOTE_RANK", "VOTING_SEVERITIES"];
const FNS = ["tomlArray", "isLoopbackEndpoint", "builderToml", "attributionFooter", "esc", "labelOf",
  "sevClassOf", "bySev", "agentVote", "tallyVote", "sevBucket", "clip", "chairHeadline",
  "parseOutcomeJson", "realCon", "verdictClass", "verdictBadge", "findingRow", "gapRow", "commentCard",
  "renderComments"];
const sink = { innerHTML: "" };
const ctx = { $: (id) => (id === "gh-comments" ? sink : null), JSON: JSON };
vm.runInNewContext(VARS.map(v).join("\n") + "\n" + FNS.map(fn).join("\n"), ctx);
const out = cases.map((c) => {
  if (c.kind === "toml") return ctx.builderToml(c.ags, c.opts);
  if (c.kind === "outcome") {
    const r = ctx.parseOutcomeJson(JSON.stringify(c.outcome), "run.json");
    if (r.error) return { error: r.error };
    return { verdict: r.run.verdict, votes: r.run.tally ? r.run.tally.votes : null };
  }
  if (c.kind === "comments") { sink.innerHTML = ""; ctx.renderComments(c.run); return sink.innerHTML; }
  throw new Error("unknown case " + c.kind);
});
console.log(JSON.stringify(out));
"""


def _run_driver(cases: list[dict]) -> list:
    workdir = tempfile.mkdtemp()
    try:
        driver = Path(workdir) / "builder.js"
        driver.write_text(_DRIVER, encoding="utf-8")
        case_file = Path(workdir) / "cases.json"
        case_file.write_text(json.dumps(cases), encoding="utf-8")
        done = subprocess.run(
            [shutil.which("node"), str(driver), str(APP_JS), str(case_file)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            stdin=subprocess.DEVNULL,
        )
    finally:
        shutil.rmtree(workdir, True)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _opts(**over) -> dict:
    opts = {
        "rounds": 2,
        "verify": True,
        "auto": False,
        "decision": "chair",
        "tiered": False,
        "hints": False,
    }
    opts.update(over)
    return opts


def _toml_cases() -> list[dict]:
    """Every seat alone, the whole panel, and each option on a two-seat panel."""
    cases = [{"kind": "toml", "ags": [s], "opts": _opts(rounds=1)} for s in SEATS]
    cases.append({"kind": "toml", "ags": list(SEATS), "opts": _opts()})
    pair = ["claude", "codex"]
    for over in (
        {"decision": "vote"},
        {"decision": "vote", "rounds": 1, "verify": False},
        {"auto": True},
        {"tiered": True},
        {"hints": True},
        {"auto": True, "decision": "vote", "tiered": True, "hints": True},
    ):
        cases.append({"kind": "toml", "ags": pair, "opts": _opts(**over)})
    return cases


def _footer_text(markup: str) -> str:
    """A footer as a reader sees it: markdown links and HTML tags reduced to their text."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", markup)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


@unittest.skipUnless(shutil.which("node"), "needs node to execute the page script")
class TheBuilderWritesConfigsJuryAccepts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = _toml_cases()
        cls.tomls = _run_driver(cls.cases)

    def _outputs(self, pred):
        return [(c, t) for c, t in zip(self.cases, self.tomls, strict=True) if pred(c)]

    def test_every_output_passesconfig_validate(self):
        # With the documented opt-in set, as the remote seats' note tells the reader to.
        self.assertGreaterEqual(len(self.tomls), 17)
        for case, text in zip(self.cases, self.tomls, strict=True):
            with self.subTest(ags=case["ags"], opts=case["opts"]):
                code, out = config_validate(text, allow_remote=True)
                self.assertEqual(code, 0, f"{out}\n{text}")

    def test_panel_vote_writes_decision_vote(self):
        for case, text in self._outputs(lambda _c: True):
            with self.subTest(opts=case["opts"]):
                jury = tomllib.loads(text)["jury"]
                if case["opts"]["decision"] == "vote":
                    self.assertEqual(jury.get("decision"), "vote", text)
                else:
                    self.assertNotIn("decision", jury, text)

    def test_a_remote_seat_says_what_it_needs_and_needs_it(self):
        for case, text in self._outputs(lambda c: len(c["ags"]) == 1):
            seat = case["ags"][0]
            with self.subTest(seat=seat):
                code, out = config_validate(text, allow_remote=False)
                if seat in REMOTE:
                    self.assertIn("JURY_ALLOW_REMOTE_ENDPOINT=1", text)
                    self.assertEqual(code, 2, out)
                else:
                    self.assertNotIn("JURY_ALLOW_REMOTE_ENDPOINT", text)
                    self.assertEqual(code, 0, f"{out}\n{text}")

    def test_grok_is_the_first_class_xai_seat(self):
        (text,) = [t for c, t in self._outputs(lambda c: c["ags"] == ["grok"])]
        seat = tomllib.loads(text)["agent"][0]
        self.assertEqual(seat.get("vendor"), "xai-api")
        self.assertNotIn("endpoint", seat)
        self.assertEqual(seat.get("model"), scaffold.SAMPLE_MODELS["xai"])

    def test_the_agy_seat_is_the_one_jury_init_writes_and_says_it_is_opt_in(self):
        (text,) = [t for c, t in self._outputs(lambda c: c["ags"] == ["agy"])]
        seat = tomllib.loads(text)["agent"][0]
        template = scaffold.agent_templates()["agy"]
        self.assertEqual(seat.get("extra_args"), template["extra_args"])
        self.assertEqual(seat.get("command"), template["command"])
        comments = " ".join(ln for ln in text.splitlines() if ln.startswith("#"))
        self.assertIn("Opt-in only", comments)
        self.assertIn("cannot be confined", comments)


def _group(severity: str, reviewers: list[str], status: str = "verified") -> dict:
    return {
        "severity": severity,
        "status": status,
        "reviewers": reviewers,
        "representative": {"file": "a.py", "line": 1, "claim": "c", "evidence": "e"},
    }


#: Loaded runs with no chair headline, so the page re-tallies a panel vote.
VOTE_SCENARIOS = {
    "info only": [_group("info", ["claude"])],
    "info and nit": [_group("info", ["claude"]), _group("nit", ["codex"])],
    "unknown severity": [_group("style", ["claude", "codex"])],
    "major beside info": [_group("major", ["claude"]), _group("info", ["codex"])],
    "rejected major": [
        _group("major", ["claude", "codex"], "unsupported"),
        _group("info", ["claude"]),
    ],
}


@unittest.skipUnless(shutil.which("node"), "needs node to execute the page script")
class TheLoadedRunVotesLikeTheCli(unittest.TestCase):
    """S19: the page bucketed `info` with `nit`, so an info-only reviewer voted COMMENT."""

    def test_each_verdict_and_ballot_is_the_clis(self):
        reviewers = ["claude", "codex"]
        cases = []
        for groups in VOTE_SCENARIOS.values():
            outcome = {
                "reviews": [{"agent": a, "ok": True, "findings": []} for a in reviewers],
                "groups": groups,
                "rounds_executed": 1,
            }
            cases.append({"kind": "outcome", "outcome": outcome})
        results = _run_driver(cases)
        for (name, groups), got in zip(VOTE_SCENARIOS.items(), results, strict=True):
            with self.subTest(scenario=name):
                want = tally_votes([SimpleNamespace(**g) for g in groups], reviewers)
                self.assertEqual(got["verdict"], want.verdict, got)
                self.assertEqual(got["votes"], {b.reviewer: b.vote for b in want.ballots})


@unittest.skipUnless(shutil.which("node"), "needs node to execute the page script")
class TheDemoCommentsEndWithTheRealFooter(unittest.TestCase):
    """S20: a posted report ends with report.render_footer; the demo's comments did not."""

    RUN = {
        "mode": "pr",
        "ags": ["claude", "codex", "qwen"],
        "pm": "single",
        "progress": False,
        "vm": "chair",
        "finalF": [],
        "surfaced": [],
        "dropped": [],
        "debate": True,
        "verdict": "APPROVE",
        "verdictNote": None,
        "r": 2,
        "auto": False,
        "verifyOn": True,
    }

    @classmethod
    def setUpClass(cls):
        phased = dict(cls.RUN, pm="phased")
        issue = dict(cls.RUN, mode="issue", gaps=[], verdict="READY")
        loaded = dict(
            cls.RUN,
            real=True,
            labels={"claude": "claude", "codex": "codex", "qwen": "qwen"},
            failed={"codex": True},
        )
        cls.single, cls.phased, cls.issue, cls.loaded = _run_driver(
            [{"kind": "comments", "run": r} for r in (cls.RUN, phased, issue, loaded)]
        )

    @staticmethod
    def _footers(markup: str) -> list[str]:
        return [_footer_text(m) for m in re.findall(r'<p class="gh-attrib">.*?</p>', markup)]

    @staticmethod
    def _cli_footer(seats: dict[str, bool]) -> str:
        reviews = [AgentResult(a, "v", ok, "", 0.0) for a, ok in seats.items()]
        return _footer_text(render_footer(reviews).split("\n")[-1])

    def test_every_comment_mode_ends_with_the_cli_footer(self):
        want = self._cli_footer({"claude": True, "codex": True, "qwen": True})
        for name, markup in (
            ("single", self.single),
            ("phased", self.phased),
            ("issue", self.issue),
        ):
            with self.subTest(mode=name):
                self.assertEqual(self._footers(markup), [want])
                self.assertTrue(markup.rstrip().endswith("</p></div></div>"), markup[-200:])

    def test_a_loaded_run_leaves_a_failed_seat_out(self):
        want = self._cli_footer({"claude": True, "codex": False, "qwen": True})
        self.assertEqual(self._footers(self.loaded), [want])

    def test_the_links_are_the_clis(self):
        md = render_footer([AgentResult("claude", "v", True, "", 0.0)])
        self.assertEqual(
            re.findall(r'href="([^"]+)"', self.single),
            re.findall(r"\]\(([^)]+)\)", md),
        )


if __name__ == "__main__":
    unittest.main()
