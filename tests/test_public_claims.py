"""Claims the public text makes about the project must be read off the project.

Two drifts from the pre-launch audit of 2026-09-23:

- The site said `[jury.ci]` has "Exactly three keys" while `CiConfig` had four
  (#869). The count and the key names are now checked against the dataclass the
  validator itself reads (`config.KNOWN_CI_KEYS`), so adding a fifth key fails here
  until every page that counts them is corrected.
- The one-line description said four different things in `pyproject.toml` (the PyPI
  summary), the README, `llms.txt` and `llms-full.txt`, and two still told readers to
  install from git "until published" (#872). There is now one sentence, and every
  surface must carry it verbatim.

Stdlib only, like the rest of the suite.
"""

from __future__ import annotations

import html
import re
import tomllib
import unittest
import unittest.mock
from pathlib import Path

from ai_jury.config import KNOWN_CI_KEYS
from ai_jury.formats import JSON_SCHEMA_VERSION
from ai_jury.metadata import SCHEMA_VERSION as METADATA_SCHEMA_VERSION

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
SITE_INDEX = REPO_ROOT / "website" / "index.html"

NUMBER_WORDS = {
    word: n
    for n, word in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
            "twenty",
        ]
    )
}
#: A number word past the table ("thirty", "forty") must not be mistaken for "no
#: count stated": it fails loudly instead.
_UNLISTED_NUMBER = re.compile(r"(?i)(?:thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)\w*")

#: Every place that may count the `[jury.ci]` keys, and where the count would sit.
#: A page may drop the count (#869 allows it); only a number it does state is
#: checked. The names are pinned separately, by `test_the_site_names_every_key`.
CI_KEY_COUNTS: dict[str, re.Pattern[str]] = {
    "website/index.html": re.compile(r"<code>\[jury\.ci\]</code><span>(?:Exactly )?(\w+) keys:"),
    "llms.txt": re.compile(r"`\[jury\.ci\]` takes (\w+) keys"),
    "llms-full.txt": re.compile(r"\[jury\.ci\]\s+# exactly these (\w+) keys"),
}

#: Surfaces that carry the one-line description as a Markdown blockquote.
DESCRIPTION_BLOCKQUOTES = ("README.md", "llms.txt", "llms-full.txt", "website/llms.txt")


def _description() -> str:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["description"]


def _as_count(word: str) -> int | None:
    """The number a word states, or ``None`` when it states none."""
    if word.isdigit():
        return int(word)
    if word.lower() in NUMBER_WORDS:
        return NUMBER_WORDS[word.lower()]
    if _UNLISTED_NUMBER.fullmatch(word):
        raise AssertionError(f"{word!r} looks like a number NUMBER_WORDS does not list")
    return None


def _first_blockquote(path: Path) -> str:
    """The first run of `> ` lines in a file, joined into one line."""
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("> "):
            lines.append(line[2:].strip())
        elif lines:
            break
    return " ".join(lines)


class TheJuryCiKeyCountIsTheSchemas(unittest.TestCase):
    def test_every_stated_count_matches_the_config_schema(self):
        for rel, pattern in CI_KEY_COUNTS.items():
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            for word in pattern.findall(text):
                count = _as_count(word)
                if count is None:
                    continue  # "The keys:", say — no number is claimed
                with self.subTest(file=rel, stated=word):
                    self.assertEqual(
                        count,
                        len(KNOWN_CI_KEYS),
                        f"{rel} says [jury.ci] has {word} keys; the schema has "
                        f"{len(KNOWN_CI_KEYS)}: {', '.join(KNOWN_CI_KEYS)}",
                    )

    def test_a_page_may_drop_the_count(self):
        pattern = CI_KEY_COUNTS["website/index.html"]
        for entry, stated in (
            ("<code>[jury.ci]</code><span>The keys: ", None),
            ("<code>[jury.ci]</code><span>Exactly three keys: ", 3),
            ("<code>[jury.ci]</code><span>4 keys: ", 4),
            ("<code>[jury.ci]</code><span>Eight keys: ", 8),
        ):
            with self.subTest(entry=entry):
                self.assertEqual([_as_count(w) for w in pattern.findall(entry)], [stated])

    def test_an_unlisted_number_word_is_not_read_as_no_count(self):
        with self.assertRaises(AssertionError):
            _as_count("thirty")

    def test_the_site_names_every_key(self):
        text = SITE_INDEX.read_text(encoding="utf-8")
        start = text.index("<code>[jury.ci]</code>")
        entry = text[start : text.index("</div>", start)]
        named = set(re.findall(r"<code>(\w+)</code>", html.unescape(entry)))
        self.assertEqual(sorted(set(KNOWN_CI_KEYS) - named), [])


class OneDescriptionEverywhere(unittest.TestCase):
    def test_every_surface_carries_the_pypi_summary_verbatim(self):
        description = _description()
        for rel in DESCRIPTION_BLOCKQUOTES:
            with self.subTest(file=rel):
                self.assertEqual(_first_blockquote(REPO_ROOT / rel), description)

    def test_no_surface_still_waits_for_the_first_publish(self):
        """ai-jury is on PyPI; "once published; until then pipx install git+…" is stale."""
        for rel in ("README.md", "llms.txt", "llms-full.txt", "website/llms.txt"):
            with self.subTest(file=rel):
                text = (REPO_ROOT / rel).read_text(encoding="utf-8")
                self.assertNotIn("once published", text)
                self.assertNotIn("pipx install git+", text)


#: Every public surface that could state which report schema is current.
_SCHEMA_SURFACES = ("README.md", "action.yml", "llms.txt", "llms-full.txt")
_SCHEMA_SURFACE_GLOBS = ("docs/**/*.md", "website/**/*.html", "website/**/*.js", "website/*.txt")
#: "(currently `1.5`)" on a line that names a schema. Historical "since …" lines do
#: not say "currently" and are left alone: they are true forever.
_CURRENTLY = re.compile(r"schema[^\n]*?currently\s+`?([0-9][0-9.]*)`?", re.IGNORECASE)


def _schema_surfaces():
    paths = [REPO_ROOT / rel for rel in _SCHEMA_SURFACES]
    for pattern in _SCHEMA_SURFACE_GLOBS:
        paths.extend(sorted(REPO_ROOT.glob(pattern)))
    return [p for p in paths if p.is_file()]


class TheCurrentSchemaVersionIsTheCodes(unittest.TestCase):
    """A page saying which report schema is current reads it off the code (#905).

    The README said `1.4` after the JSON report moved to `1.5`. A dotted version is
    the JSON report's (`formats.JSON_SCHEMA_VERSION`), a whole number the run
    metadata's (`metadata.SCHEMA_VERSION`).
    """

    def test_every_currently_statement_matches(self):
        found = []
        for path in _schema_surfaces():
            text = path.read_text(encoding="utf-8")
            for match in _CURRENTLY.finditer(text):
                stated = match.group(1)
                expected = JSON_SCHEMA_VERSION if "." in stated else str(METADATA_SCHEMA_VERSION)
                found.append(path)
                with self.subTest(file=str(path.relative_to(REPO_ROOT)), stated=stated):
                    self.assertEqual(stated, expected)
        # The README's statement is the one known to exist; a regex that stops
        # finding it would pass every file vacuously.
        self.assertIn(REPO_ROOT / "README.md", found)

    def test_the_readme_states_the_json_schema(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            f"| `schema_version` | Version of this JSON schema (currently `{JSON_SCHEMA_VERSION}`). |",
            readme,
        )


# --------------------------------------------------------------------------- #
# The docs audit of 2026-09-29: README, llms, the skill and the platform page    #
# --------------------------------------------------------------------------- #

README_PATH = REPO_ROOT / "README.md"
SAMPLE_DIFF = REPO_ROOT / "examples" / "sample.diff"
#: The surfaces a reader learns "which reviewers do I need" from.
REVIEWER_SURFACES = (
    "README.md",
    "llms-full.txt",
    "skills/ai-jury/SKILL.md",
    "docs/skill.md",
    "docs/platforms.md",
)


def _readme() -> str:
    return README_PATH.read_text(encoding="utf-8")


def _readme_section(title: str) -> str:
    """A `##`/`###` README section, up to the next heading of the same or higher level."""
    text = _readme()
    match = re.search(rf"^(#{{2,3}}) {re.escape(title)}\s*$", text, flags=re.M)
    assert match, f"no README section {title!r}"
    rest = text[match.end() :]
    following = re.search(rf"^#{{2,{len(match.group(1))}}} ", rest, flags=re.M)
    return rest[: following.start()] if following else rest


def _exit_table() -> str:
    return (
        _readme()
        .split("**Stable error messages and exit codes:**", 1)[1]
        .split("**Stable report headings**", 1)[0]
    )


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    """`cli.main(argv)` → (exit code, stdout, stderr), with the user dirs in a temp dir."""
    import contextlib
    import io
    import os
    import tempfile
    import unittest.mock as mock

    from ai_jury import cli

    out, err = io.StringIO(), io.StringIO()
    with tempfile.TemporaryDirectory() as home:
        env = {"XDG_CONFIG_HOME": f"{home}/config", "XDG_CACHE_HOME": f"{home}/cache"}
        with (
            mock.patch.dict(os.environ, env),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                code = cli.main(argv)
            except SystemExit as exc:
                if isinstance(exc.code, str):
                    print(exc.code, file=err)
                    code = 1
                else:
                    code = exc.code or 0
    return code, out.getvalue(), err.getvalue()


class TheReadmeJsonKeyTableIsTheReports(unittest.TestCase):
    """The table lacked `classification`, which every `--format json` report carries."""

    def test_the_table_lists_exactly_the_reports_top_level_keys(self):
        import json

        code, out, err = _run_cli(
            ["--mock", "--diff-file", str(SAMPLE_DIFF), "-q", "--format", "json"]
        )
        self.assertEqual(code, 0, err[-2000:])
        report_keys = list(json.loads(out))
        table = _readme_section("JSON").split("| Key | Description |", 1)[1]
        documented = re.findall(r"^\| `(\w+)` \|", table, flags=re.M)
        self.assertEqual(sorted(documented), sorted(report_keys))


class TheReadmeInitSampleIsWhatInitWrites(unittest.TestCase):
    """The README showed `timeout = 300` and `parallel = true`; `jury init` writes neither."""

    LEAD = "`jury init --agents claude,codex` writes exactly this:\n\n```toml\n"

    def test_the_sample_is_the_scaffold_output_verbatim(self):
        from ai_jury import scaffold

        text = _readme()
        self.assertIn(self.LEAD, text)
        shown = text.split(self.LEAD, 1)[1].split("```", 1)[0]
        written = scaffold.render_toml(scaffold.build_config(["claude", "codex"]))
        self.assertEqual(shown, written)


class TheExitTableNamesTheStartupRefusals(unittest.TestCase):
    """`no usable agents` and `--strict` exit 2 before any review; the table had neither."""

    def _config(self, tmp: str) -> str:
        from pathlib import Path as _Path

        path = _Path(tmp) / "jury.toml"
        # An absolute path that does not exist, on every OS: "/nonexistent/…" is not
        # absolute on Windows, where validation refuses it as a relative command. A
        # TOML literal string ('…') keeps a Windows path's backslashes as written.
        missing = _Path(tmp).resolve() / "missing" / "claude"
        path.write_text(
            f'[[agent]]\nname = "claude"\nvendor = "anthropic"\ncommand = \'{missing}\'\n',
            encoding="utf-8",
        )
        return str(path)

    def _row(self, *needles: str) -> str:
        rows = [r for r in _exit_table().splitlines() if all(n in r for n in needles)]
        self.assertEqual(len(rows), 1, f"expected one exit-table row naming {needles}")
        return rows[0]

    def test_no_usable_agents(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            argv = ["--config", self._config(tmp), "--diff-file", str(SAMPLE_DIFF), "-q"]
            code, _out, err = _run_cli(argv)
        self.assertEqual(code, 2, err[-2000:])
        self.assertIn("error: no usable agents — ", err)
        self.assertIn("`2`", self._row("`error: no usable agents — …`"))

    def test_strict_with_a_missing_cli(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            argv = [
                "--config",
                self._config(tmp),
                "--diff-file",
                str(SAMPLE_DIFF),
                "-q",
                "--strict",
            ]
            code, _out, err = _run_cli(argv)
        self.assertEqual(code, 2, err[-2000:])
        self.assertIn("error: agent 'claude' CLI not available: ", err)
        self.assertIn("`2`", self._row("`--strict`", "CLI not available: <command>"))

    def test_a_pr_only_flag_with_issue(self):
        import tempfile

        # An explicit --config, so the repository's own jury.toml (and the trust
        # gate it would raise off a terminal) is not what answers.
        with tempfile.TemporaryDirectory() as tmp:
            argv = ["--config", self._config(tmp), "--issue", "1", "--post-inline", "-q"]
            code, _out, err = _run_cli(argv)
        self.assertNotEqual(code, 0)
        message = err.strip().splitlines()[-1]
        self.assertTrue(message.startswith("error: --post-inline is not supported with --issue"))
        self._row(f"`{message}`")


class TheReplayNoteNamesAFileThatReplays(unittest.TestCase):
    """The README said to replay `-o run.json`; that file is the markdown report."""

    def test_a_report_does_not_replay_and_a_cache_entry_does(self):
        import tempfile
        from pathlib import Path as _Path

        with tempfile.TemporaryDirectory() as tmp:
            report = _Path(tmp) / "run.json"
            cache_dir = _Path(tmp) / "cache"
            base = ["--mock", "--diff-file", str(SAMPLE_DIFF), "-q"]
            self.assertEqual(_run_cli([*base, "-o", str(report)])[0], 0)
            code, _out, err = _run_cli(["replay", str(report)])
            self.assertEqual(code, 2)
            self.assertIn("not valid JSON", err)
            argv = [*base, "--cache", "--cache-dir", str(cache_dir), "-o", str(_Path(tmp) / "r.md")]
            self.assertEqual(_run_cli(argv)[0], 0)
            (entry,) = cache_dir.glob("*.json")
            self.assertEqual(_run_cli(["replay", str(entry)])[0], 0)
        readme = _readme()
        self.assertNotIn("outcome dump (`-o run.json`", readme)
        replay = _readme_section("Replay a saved run — `jury replay`")
        self.assertIn("jury --pr 123 --cache", replay)
        self.assertIn("not valid JSON", replay)


class TheDoctorJsonIsNotTheWriteFile(unittest.TestCase):
    """`--doctor --json` was described as "the same facts" as `--write`; the shapes differ."""

    def test_the_two_documents_differ_and_the_readme_says_so(self):
        import json
        import tempfile
        from pathlib import Path as _Path

        code, out, _err = _run_cli(["--doctor", "--json"])
        self.assertEqual(code, 0)
        as_json = set(json.loads(out))
        with tempfile.TemporaryDirectory() as tmp:
            written = _Path(tmp) / "d.json"
            self.assertEqual(_run_cli(["--doctor", "--write", str(written)])[0], 0)
            as_written = set(json.loads(written.read_text(encoding="utf-8")))
        self.assertNotEqual(as_json, as_written)
        self.assertIn("recommendations", as_written - as_json)
        doctor = _readme_section("Diagnostics — `jury --doctor`")
        self.assertNotIn("same facts", doctor)
        self.assertIn("It is not the `--write` file", doctor)


class TheLiveSmokeNoteNamesTheBuiltInPanel(unittest.TestCase):
    """The live tests iterate `DEFAULT_CONFIG`'s agents; the README also required `agy`."""

    def test_the_required_clis_are_the_default_panels(self):
        from ai_jury.config import DEFAULT_CONFIG

        listed = ", ".join(f"`{a['command']}`" for a in DEFAULT_CONFIG["agent"])
        section = _readme_section("Live smoke tests")
        self.assertIn(f"({listed}) **and authenticated**", section)


class RunAgentExamplesUseTheSampleModelIds(unittest.TestCase):
    """`codex:gpt-5.2` was not the id `scaffold.SAMPLE_MODELS` holds for openai."""

    VENDOR_OF = {"claude": "anthropic", "codex": "openai", "agy": "google"}

    def test_every_pinned_example_model_is_the_listed_one(self):
        from ai_jury import scaffold

        seen = 0
        for rel in ("README.md", "llms.txt", "llms-full.txt", "skills/ai-jury/SKILL.md"):
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            for agent, model in re.findall(r"--agent (claude|codex|agy):([\w.:/-]+)", text):
                seen += 1
                with self.subTest(file=rel, agent=agent):
                    self.assertEqual(model, scaffold.SAMPLE_MODELS[self.VENDOR_OF[agent]])
        self.assertGreaterEqual(seen, 1, "no `--agent <cli>:<model>` example found")


class AgyIsOptInOnEveryReviewerSurface(unittest.TestCase):
    """#877 took agy out of the default panel; five surfaces still offered it as a reviewer."""

    #: The sentence that tells a reader what the minimum is.
    AT_LEAST_ONE = re.compile(r"(?i)at least (?:\*\*)?one")

    def test_the_minimum_reviewer_paragraph_says_agy_is_opt_in(self):
        """Each page's "at least one reviewer" paragraph offered `agy` as that one.

        An agy-only machine has no reviewer: the built-in panel is claude + codex,
        and agy runs only when a `jury.toml` seats it by name. So wherever the
        minimum is stated next to `agy`, the same paragraph must say opt-in —
        the word elsewhere on the page ("`--suggest-patches` — opt-in") does not
        reach the reader of that sentence.
        """
        for rel in REVIEWER_SURFACES:
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            paragraphs = [
                p
                for p in re.split(r"\n\s*\n", text)
                if self.AT_LEAST_ONE.search(p) and "`agy`" in p
            ]
            with self.subTest(file=rel):
                self.assertTrue(paragraphs, "no paragraph states the minimum next to `agy`")
                for paragraph in paragraphs:
                    self.assertIn("opt-in", paragraph)

    def test_the_plugin_description_does_not_seat_antigravity_by_default(self):
        import json

        manifest = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text("utf-8"))
        description = manifest["description"]
        self.assertNotIn("Claude Code + Codex + Antigravity", description)
        if "Antigravity" in description:
            self.assertIn("opt-in", description)


class TheLlmsFilesNameEveryConfigTable(unittest.TestCase):
    """`[jury.output]` (#911) was missing from both agent-readable references."""

    def test_every_nested_jury_table_is_named(self):
        from ai_jury.config import KNOWN_NESTED_JURY_KEYS

        for rel in ("llms.txt", "llms-full.txt"):
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            for table in KNOWN_NESTED_JURY_KEYS:
                with self.subTest(file=rel, table=table):
                    self.assertIn(f"[jury.{table}]", text)

    def test_llms_full_lists_the_flags_it_was_missing(self):
        text = (REPO_ROOT / "llms-full.txt").read_text(encoding="utf-8")
        for flag in ("--min-reviews", "--no-attribution"):
            with self.subTest(flag=flag):
                self.assertIn(f"`{flag}", text)


class EveryExitThreeCauseIsNamed(unittest.TestCase):
    """Exit 3 has three causes in `cli.main`; the llms reference and the skill named one."""

    def test_llms_full_and_the_skill_name_all_three(self):
        full = (REPO_ROOT / "llms-full.txt").read_text(encoding="utf-8")
        codes = full.split("### Exit codes", 1)[1].split("\n## ", 1)[0]
        skill = (REPO_ROOT / "skills" / "ai-jury" / "SKILL.md").read_text(encoding="utf-8")
        for where, text in (("llms-full.txt", codes), ("SKILL.md", skill)):
            for cause in ("min_vendors", "every seat", "min_reviews", "no usable"):
                with self.subTest(file=where, cause=cause):
                    self.assertIn(cause, text)


class TheSarifWorkflowPassesTheKeysItsSeatsRead(unittest.TestCase):
    """With no reviewer the SARIF step exited 2 (`no usable agents`); it now passes keys."""

    def test_the_step_passes_every_key_the_readme_jury_toml_reads(self):
        import tomllib as _toml

        from ai_jury.adapters import make_adapter
        from ai_jury.config import _from_dict

        text = _readme()
        toml = text.split("```toml\n# jury.toml\n", 1)[1].split("```", 1)[0]
        needed = {
            make_adapter(agent)._ENV_VAR_NAME for agent in _from_dict(_toml.loads(toml)).agents
        }
        sarif = _readme_section("SARIF")
        step = sarif.split("- name: Produce SARIF from the PR diff", 1)[1].split("- name:", 1)[0]
        for var in sorted(needed):
            with self.subTest(var=var):
                self.assertIn(f"{var}: ${{{{ secrets.{var} }}}}", step)
        self.assertIn("`error: no usable agents`", sarif)


if __name__ == "__main__":
    unittest.main()


# These runs go through the real CLI path, not --mock: an operator's $JURY_EVENTS_DIR
# (a Claude Code mod sets it) must not collect them, nor prune the real runs there.
_EVENTS_ENV = unittest.mock.patch.dict("os.environ", {"JURY_EVENTS_DIR": "off"})


def setUpModule():
    _EVENTS_ENV.start()


def tearDownModule():
    _EVENTS_ENV.stop()
