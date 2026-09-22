"""Targeted offline coverage for ``ai_jury.cli`` branches not hit by the other
CLI suites: comment-command dispatch, init interactive/list/error paths, the
local-fallback helper, cache-clear argv parsing, doctor write errors, the
--incremental/--auto/--label guard rails, and the CI/suggest-patches branches.

All offline and deterministic — `gh`/network/subprocess and local-model
discovery are mocked; agents run via ``--mock`` with a fixed ``--seed``."""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import cli, github  # noqa: E402

DIFF = """diff --git a/app.py b/app.py
index 0000000..1111111 100644
--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
 def f(x):
-    return x
+    return x + 1
+    # added
"""


def run(args, stdin=None):
    out, err = io.StringIO(), io.StringIO()
    prev = sys.stdin
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    code = None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(args)
    except SystemExit as exc:
        code = exc.code
    finally:
        sys.stdin = prev
    return code, out.getvalue(), err.getvalue()


@contextlib.contextmanager
def gh_mocked(diff=DIFF, head="abc123def456789", comments=None):
    comments = comments or []
    with (
        mock.patch("ai_jury.cli.pr_diff", return_value=diff),
        mock.patch("ai_jury.cli.pr_context", return_value="title\n\nbody"),
        mock.patch("ai_jury.cli.post_pr_comment") as ppc,
        mock.patch("ai_jury.cli.post_inline_comments") as pic,
        mock.patch("ai_jury.cli.apply_labels") as al,
        mock.patch("ai_jury.github.pr_head_sha", return_value=head),
        mock.patch("ai_jury.github.pr_comment_bodies", return_value=comments),
        mock.patch("ai_jury.github.compare_diff", return_value=diff),
    ):
        yield {"post": ppc, "inline": pic, "labels": al}


@contextlib.contextmanager
def offline_review():
    """Force ``cli.review_diff`` onto the deterministic mock agents so a
    dispatched run never invokes a real vendor CLI (which would block).

    Also trusts the project config: these tests dispatch a real review against the
    repository's own ``jury.toml`` (command seats), and the #831 trust gate would
    otherwise refuse it non-interactively. The gate is exercised on its own in
    ``tests/test_configtrust.py``; here it is not what is under test."""
    real = cli.review_diff

    def _offline(config, diff, **kw):
        kw["mock"] = True
        return real(config, diff, **kw)

    with (
        mock.patch("ai_jury.cli.review_diff", side_effect=_offline),
        mock.patch.dict(os.environ, {"JURY_TRUST_PROJECT_CONFIG": "1"}),
    ):
        yield


@contextlib.contextmanager
def chdir(path):
    """Run with cwd set to ``path`` so Path('jury.toml') resolves there."""
    prev = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


class CommentCommandDispatch(unittest.TestCase):
    """cli._run_comment_command: --repo append (285), no-pr branch (280->284),
    and the real dispatch path (290 -> main)."""

    def test_print_args_with_repo_no_pr(self):
        # No --pr: skips the pr/post block (280->284), still appends --repo (285).
        code, out, _ = run(["comment", "--text", "/jury review", "--repo", "o/r", "--print-args"])
        self.assertEqual(code, 0)
        self.assertIn("--repo", out)
        self.assertIn("o/r", out)

    def test_dispatch_runs_main(self):
        # Without --print-args the comment command dispatches into main(inner).
        with gh_mocked() as m, offline_review():
            code, _, _ = run(["comment", "--text", "/jury review", "--pr", "5", "--repo", "o/r"])
        self.assertEqual(code, 0)
        m["post"].assert_called()  # --post-summary was added because --pr given

    def test_dispatch_no_post(self):
        with gh_mocked() as m, offline_review():
            code, _, _ = run(["comment", "--text", "/jury review", "--pr", "5", "--no-post"])
        self.assertEqual(code, 0)
        m["post"].assert_not_called()

    def test_rejected_command(self):
        code, _, err = run(["comment", "--text", "not a jury command"])
        self.assertEqual(code, 2)
        self.assertIn("rejected", err)


class InitDetectionPaths(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_init_available_swallows_errors(self):
        # _init_available: make_adapter raising -> detection records False (312-313).
        # Patched in the cli namespace: make_adapter is imported at module level
        # there now (the effort warnings need it), so the adapters-module name is
        # no longer the one this function resolves.
        with mock.patch("ai_jury.cli.make_adapter", side_effect=RuntimeError("boom")):
            avail = cli._init_available()
        self.assertTrue(all(v is False for v in avail.values()))

    def test_list_agents_shows_local_models(self):
        # _run_init --list-agents with discoverable models (429->431 true branch).
        with (
            mock.patch("ai_jury.adapters.list_local_models", return_value=["gemma:2b"]),
            mock.patch(
                "ai_jury.cli._init_available",
                return_value=dict.fromkeys(("claude", "codex", "agy", "qwen"), False),
            ),
        ):
            code, out, _ = run(["init", "--list-agents"])
        self.assertEqual(code, 0)
        self.assertIn("gemma:2b", out)

    def test_list_agents_no_local_models(self):
        # --list-agents with no discoverable models (429->431 false branch).
        with (
            mock.patch("ai_jury.adapters.list_local_models", return_value=[]),
            mock.patch(
                "ai_jury.cli._init_available",
                return_value=dict.fromkeys(("claude", "codex", "agy", "qwen"), False),
            ),
        ):
            code, out, _ = run(["init", "--list-agents"])
        self.assertEqual(code, 0)
        self.assertNotIn("local models at", out)

    def test_no_agents_detected_errors(self):
        # Non-interactive, no --agents/--preset, nothing detected (463-470).
        with (
            mock.patch(
                "ai_jury.cli._init_available",
                return_value=dict.fromkeys(("claude", "codex", "agy", "qwen"), False),
            ),
            mock.patch("ai_jury.cli.sys.stdin") as stdin,
        ):
            stdin.isatty.return_value = False
            code, _, err = run(["init", "-o", str(self.d / "x.toml")])
        self.assertEqual(code, 2)
        self.assertIn("no agents detected", err)

    def test_invalid_generated_config_errors(self):
        # validate_config raising inside _run_init (486-488). Force a bad chair so
        # build_config succeeds but validation fails.
        from ai_jury.config import ConfigError

        with mock.patch(
            "ai_jury.config.validate_config", side_effect=ConfigError("template drift")
        ):
            code, _, err = run(
                ["init", "--agents", "claude", "-o", str(self.d / "y.toml"), "--force"]
            )
        self.assertEqual(code, 2)
        self.assertIn("generated config is invalid", err)


class InitInteractive(unittest.TestCase):
    """_init_interactive: default models_fn import (327) + local-model defaulting
    on empty input (365)."""

    def test_default_models_fn_used(self):
        # models_fn left None -> imports list_local_models (line 327). Choose qwen
        # so the local-model block runs; empty pick -> local_model = default (365).
        answers = iter(["qwen", "", "", "", "", ""])  # agents/rounds/chair/verify/model/effort

        def fake_input(_prompt):
            return next(answers)

        with mock.patch(
            "ai_jury.adapters.list_local_models", return_value=["qwen2.5-coder:7b", "gemma:2b"]
        ):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                kwargs = cli._init_interactive({"qwen": True}, input_fn=fake_input)
        self.assertEqual(kwargs["agents"], ["qwen"])
        # pick_default_model picks a coder model; empty input falls back to it.
        self.assertTrue(kwargs["local_model"])

    def test_interactive_branch_in_run_init(self):
        # _run_init interactive branch (453-456): force --interactive; the
        # interactive prompting itself is exercised separately, so stub it here
        # and assert --local-model overrides the returned kwargs.
        d = Path(tempfile.mkdtemp())
        out = d / "i.toml"
        with (
            mock.patch(
                "ai_jury.cli._init_available",
                return_value={"claude": True, "codex": False, "agy": False, "qwen": False},
            ),
            mock.patch(
                "ai_jury.cli._init_interactive",
                return_value={
                    "agents": ["claude"],
                    "rounds": 2,
                    "chair": "claude",
                    "verify": True,
                    "local_model": None,
                },
            ) as ii,
        ):
            code, _, _ = run(
                ["init", "--interactive", "--local-model", "x:1b", "-o", str(out), "--force"]
            )
        self.assertEqual(code, 0)
        self.assertTrue(out.exists())
        ii.assert_called_once()

    def test_interactive_branch_without_local_model(self):
        # Interactive branch, no --local-model (455->477 false branch).
        d = Path(tempfile.mkdtemp())
        out = d / "j.toml"
        with (
            mock.patch(
                "ai_jury.cli._init_available",
                return_value={"claude": True, "codex": False, "agy": False, "qwen": False},
            ),
            mock.patch(
                "ai_jury.cli._init_interactive",
                return_value={
                    "agents": ["claude"],
                    "rounds": 2,
                    "chair": "claude",
                    "verify": True,
                    "local_model": None,
                },
            ),
        ):
            code, _, _ = run(["init", "--interactive", "-o", str(out), "--force"])
        self.assertEqual(code, 0)
        self.assertTrue(out.exists())


class LocalFallbackHelper(unittest.TestCase):
    """cli._maybe_add_local_fallback edge branches (599-601, 605)."""

    def _args(self):
        return cli.build_parser().parse_args(["--diff-file", "-"])

    def test_returns_when_an_agent_is_available(self):
        from ai_jury.config import load_config

        cfg = load_config(None, validate=True)
        with (
            tempfile.TemporaryDirectory() as tmp,
            chdir(tmp),
            mock.patch("ai_jury.adapters.make_adapter") as ma,
            mock.patch("ai_jury.adapters.list_local_models") as llm,
        ):
            ma.return_value.available.return_value = True  # 598-599 -> return
            cli._maybe_add_local_fallback(cfg, self._args(), lambda _m: None)
            llm.assert_not_called()

    def test_returns_when_availability_probe_raises(self):
        from ai_jury.config import load_config

        cfg = load_config(None, validate=True)
        n_before = len(cfg.agents)
        with (
            tempfile.TemporaryDirectory() as tmp,
            chdir(tmp),
            mock.patch("ai_jury.adapters.make_adapter", side_effect=RuntimeError("probe failed")),
        ):  # 600-601
            cli._maybe_add_local_fallback(cfg, self._args(), lambda _m: None)
        self.assertEqual(len(cfg.agents), n_before)

    def test_returns_when_no_local_model(self):
        from ai_jury.config import load_config

        cfg = load_config(None, validate=True)
        n_before = len(cfg.agents)
        with (
            tempfile.TemporaryDirectory() as tmp,
            chdir(tmp),
            mock.patch("ai_jury.adapters.make_adapter") as ma,
            mock.patch("ai_jury.adapters.list_local_models", return_value=[]),
        ):
            ma.return_value.available.return_value = False
            cli._maybe_add_local_fallback(cfg, self._args(), lambda _m: None)  # 605
        self.assertEqual(len(cfg.agents), n_before)

    def test_appends_local_agent_when_model_found(self):
        # Success path (606-611): no CLI available + a discoverable model -> a
        # local agent is appended and the chair is pointed at it.
        from ai_jury.config import load_config

        cfg = load_config(None, validate=True)
        n_before = len(cfg.agents)
        logged = []
        with (
            tempfile.TemporaryDirectory() as tmp,
            chdir(tmp),
            mock.patch("ai_jury.adapters.make_adapter") as ma,
            mock.patch("ai_jury.adapters.list_local_models", return_value=["qwen2.5-coder:7b"]),
        ):
            ma.return_value.available.return_value = False
            cli._maybe_add_local_fallback(cfg, self._args(), logged.append)
        self.assertEqual(len(cfg.agents), n_before + 1)
        self.assertEqual(cfg.chair, "local")
        self.assertTrue(any("local model" in m for m in logged))


class CacheClearArgv(unittest.TestCase):
    """`jury cache clear` argv parsing (640->644, 642->644) and --clear-cache
    flag (670-674)."""

    def test_cache_clear_without_dir(self):
        # --cache-dir absent: 640->644 false branch.
        with mock.patch("ai_jury.cache.Cache") as cache_cls:
            cache_cls.return_value.clear.return_value = 0
            code, out, _ = run(["cache", "clear"])
        self.assertEqual(code, 0)
        self.assertIn("Cleared 0 cache entries", out)

    def test_cache_clear_dir_flag_at_end_no_value(self):
        # `--cache-dir` is the last token with no value: 642->644 false branch.
        with mock.patch("ai_jury.cache.Cache") as cache_cls:
            cache_cls.return_value.clear.return_value = 1
            code, out, _ = run(["cache", "clear", "--cache-dir"])
        self.assertEqual(code, 0)
        self.assertIn("Cleared 1 cache entry", out)
        cache_cls.assert_called_once_with(None)

    def test_clear_cache_flag(self):
        # The --clear-cache top-level flag path (670-674).
        with mock.patch("ai_jury.cache.Cache") as cache_cls:
            cache_cls.return_value.clear.return_value = 3
            code, out, _ = run(["--clear-cache", "--cache-dir", "/tmp/x"])
        self.assertEqual(code, 0)
        self.assertIn("Cleared 3 cache entries", out)
        cache_cls.assert_called_once_with("/tmp/x")


class DoctorWriteError(unittest.TestCase):
    def test_doctor_write_oserror(self):
        # Path.write_text raising OSError -> exit 2 (684-686).
        with mock.patch("ai_jury.cli.Path") as path_cls:
            path_cls.return_value.write_text.side_effect = OSError("disk full")
            code, _, err = run(["--doctor", "--write", "/nope/diag.json"])
        self.assertEqual(code, 2)
        self.assertIn("error:", err)


class IncrementalAndGuards(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.diff = self.d / "x.diff"
        self.diff.write_text(DIFF, encoding="utf-8")

    def test_incremental_requires_pr(self):
        # 784: --incremental without --pr raises SystemExit with the message as code.
        code, _, _ = run(["--mock", "--diff-file", str(self.diff), "--incremental", "-q"])
        self.assertIn("--incremental requires --pr", str(code))

    def test_incremental_narrows_diff(self):
        # MODE_INCREMENTAL with a usable narrowed diff (792-794).
        from ai_jury import incremental as inc

        marker = inc.reviewed_sha_marker("deadbeefcafe1234")
        narrowed = DIFF.replace("# added", "# narrowed")
        with (
            gh_mocked(head="abc123def456789", comments=[marker]),
            mock.patch("ai_jury.github.compare_diff", return_value=narrowed),
        ):
            code, _, _ = run(["--mock", "--pr", "9", "--incremental", "-q", "--seed", "1"])
        self.assertEqual(code, 0)

    def test_incremental_empty_range_falls_back(self):
        # MODE_INCREMENTAL but compare_diff is empty -> MODE_FULL fallback (795-796).
        from ai_jury import incremental as inc

        marker = inc.reviewed_sha_marker("deadbeefcafe1234")
        with (
            gh_mocked(head="abc123def456789", comments=[marker]),
            mock.patch("ai_jury.github.compare_diff", return_value="   \n"),
        ):
            code, _, _ = run(["--mock", "--pr", "9", "--incremental", "-q", "--seed", "1"])
        self.assertEqual(code, 0)

    def test_label_requires_pr(self):
        # 970: --label without --pr raises SystemExit with the message as code.
        code, _, _ = run(["--mock", "--diff-file", str(self.diff), "--label", "-q"])
        self.assertIn("--label requires --pr", str(code))


class AutoDepthBranches(unittest.TestCase):
    """Auto-depth respects explicit --rounds/--early-stop/--verify (813->815,
    815->817): when those flags are given, the config values are NOT overwritten."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.diff = self.d / "x.diff"
        self.diff.write_text(DIFF, encoding="utf-8")

    def test_auto_with_explicit_rounds_and_verify(self):
        # --rounds given -> skip the rounds override (813->815 false);
        # --verify given -> skip the verify override (815->817 false).
        code, out, _ = run(
            [
                "--mock",
                "--diff-file",
                str(self.diff),
                "--auto",
                "--rounds",
                "2",
                "--verify",
                "--seed",
                "1",
                "-q",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("AI Jury", out)

    def test_auto_with_explicit_early_stop(self):
        # --auto without --rounds but WITH --early-stop: rounds override runs but
        # the inner early_stop override is skipped (813->815 true, inner false).
        code, out, _ = run(
            ["--mock", "--diff-file", str(self.diff), "--auto", "--early-stop", "--seed", "1", "-q"]
        )
        self.assertEqual(code, 0)


class CiAndPatchesBranches(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.diff = self.d / "x.diff"
        self.diff.write_text(DIFF, encoding="utf-8")

    def test_ci_json_skips_gate_section(self):
        # --ci with non-markdown format: the CI gate section is not appended
        # (899->905 false branch); exit code still reflects the gate.
        code, out, _ = run(
            [
                "--mock",
                "--diff-file",
                str(self.diff),
                "-q",
                "--seed",
                "1",
                "--ci",
                "--format",
                "json",
            ]
        )
        self.assertIn(code, (0, 1, 2))
        self.assertNotIn("## CI gate", out)

    def test_suggest_patches_none_emitted(self):
        # When no verified finding carries a fix, render_patch_suggestions is
        # empty and the helper logs "no patches" (line 910). Force the empty case.
        with mock.patch("ai_jury.patches.render_patch_suggestions", return_value=""):
            code, _, err = run(
                ["--mock", "--diff-file", str(self.diff), "--suggest-patches", "--seed", "1"]
            )
        self.assertEqual(code, 0)
        self.assertIn("no patches emitted", err)


class InitInteractiveModelPicks(unittest.TestCase):
    """_init_interactive model-selection branches: pick by number (361),
    unreachable-server fallback prompt (367-371)."""

    def test_pick_model_by_number(self):
        answers = iter(["qwen", "1", "qwen", "y", "2", ""])  # pick model #2, then skip effort

        def fake_input(_prompt):
            return next(answers)

        with (
            mock.patch("ai_jury.adapters.list_local_models", return_value=["a:1b", "b:2b"]),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            kwargs = cli._init_interactive({"qwen": True}, input_fn=fake_input)
        self.assertEqual(kwargs["local_model"], "b:2b")

    def test_pick_model_by_name(self):
        answers = iter(["qwen", "1", "qwen", "y", "custom:3b", ""])

        def fake_input(_prompt):
            return next(answers)

        with (
            mock.patch("ai_jury.adapters.list_local_models", return_value=["a:1b", "b:2b"]),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            kwargs = cli._init_interactive({"qwen": True}, input_fn=fake_input)
        self.assertEqual(kwargs["local_model"], "custom:3b")

    def test_server_unreachable_fallback_prompt(self):
        # No models discoverable -> the "could not reach server" prompt (367-371).
        answers = iter(["qwen", "1", "qwen", "y", "mymodel:1b", ""])

        def fake_input(_prompt):
            return next(answers)

        with (
            mock.patch("ai_jury.adapters.list_local_models", return_value=[]),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            kwargs = cli._init_interactive({"qwen": True}, input_fn=fake_input)
        self.assertEqual(kwargs["local_model"], "mymodel:1b")


class InitPresetAgentSpecs(unittest.TestCase):
    """_resolve_preset_agents 'all'/'detected' specs (440, 442) via presets."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_preset_thorough_all_agents(self):
        # 'thorough' preset uses spec 'all' (440).
        out = self.d / "t.toml"
        with (
            mock.patch(
                "ai_jury.cli._init_available",
                return_value=dict.fromkeys(("claude", "codex", "agy", "qwen"), True),
            ),
            mock.patch("ai_jury.adapters.list_local_models", return_value=["qwen2.5-coder:7b"]),
        ):
            code, _, _ = run(["init", "--preset", "thorough", "-o", str(out), "--force"])
        self.assertEqual(code, 0)
        self.assertTrue(out.exists())

    def test_preset_balanced_detected_agents(self):
        # 'balanced' preset uses spec 'detected' (442).
        out = self.d / "b.toml"
        with mock.patch(
            "ai_jury.cli._init_available",
            return_value={"claude": True, "codex": True, "agy": False, "qwen": False},
        ):
            code, _, _ = run(["init", "--preset", "balanced", "-o", str(out), "--force"])
        self.assertEqual(code, 0)
        self.assertTrue(out.exists())


class ConfigShowError(unittest.TestCase):
    def test_config_show_invalid_file(self):
        # _run_config load error (564-566).
        cfg = Path(tempfile.mkdtemp()) / "bad.toml"
        cfg.write_text("[jury]\nrounds = 0\n", encoding="utf-8")
        code, _, err = run(["config", "show", "--config", str(cfg)])
        self.assertEqual(code, 2)
        self.assertIn("error:", err)


class PolicyAndPostGuards(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.diff = self.d / "x.diff"
        self.diff.write_text(DIFF, encoding="utf-8")

    def test_policy_load_error(self):
        # load_policy raising PolicyError -> exit 2 (748-750).
        from ai_jury.policy import PolicyError

        with mock.patch("ai_jury.cli.load_policy", side_effect=PolicyError("bad policy")):
            code, _, err = run(
                ["--mock", "--diff-file", str(self.diff), "--policy", "p.toml", "-q"]
            )
        self.assertEqual(code, 2)
        self.assertIn("bad policy", err)

    def test_post_progress_requires_pr(self):
        # 757: --post-progress without --pr.
        code, _, _ = run(["--mock", "--diff-file", str(self.diff), "--post-progress", "-q"])
        self.assertIn("--post-progress requires --pr", str(code))

    def test_post_summary_requires_pr(self):
        # 933: --post-summary without --pr.
        code, _, _ = run(["--mock", "--diff-file", str(self.diff), "--post", "-q"])
        self.assertIn("--post-summary requires --pr", str(code))

    def test_post_inline_requires_pr(self):
        # 962: --post-inline without --pr.
        code, _, _ = run(["--mock", "--diff-file", str(self.diff), "--post-inline", "-q"])
        self.assertIn("--post-inline requires --pr", str(code))

    def test_suggest_patches_to_file(self):
        # --patches-out writes the section to a file (912-913). Force a non-empty
        # section so the write branch runs.
        outp = self.d / "patches.md"
        with mock.patch(
            "ai_jury.patches.render_patch_suggestions", return_value="## Suggested patches\n\nfix\n"
        ):
            code, _, _ = run(
                [
                    "--mock",
                    "--diff-file",
                    str(self.diff),
                    "--seed",
                    "1",
                    "--suggest-patches",
                    "--patches-out",
                    str(outp),
                    "-q",
                ]
            )
        self.assertEqual(code, 0)
        self.assertTrue(outp.exists())
        self.assertIn("Suggested patches", outp.read_text(encoding="utf-8"))


class PostReviewBlockSurvivesAGhFailure(unittest.TestCase):
    """#844: a `gh` failure after the review has run must not become a traceback.

    By the time these run the panel has finished and the verdict is on stdout. The two
    kinds are pinned separately because they answer differently, and that difference is
    the whole point: `--post-summary` is a contract and exits 2, while `--post-inline`
    and `--label` are additions to a delivered review and leave `ci_exit` — the gate's
    answer about the *code* — intact.
    """

    def setUp(self):
        # `--pr` is the only way into the post block — the CLI refuses it beside
        # `--diff-file` ("choose one input source") — so the PR read is mocked and the
        # run reaches the post block the way a real `--pr` review does.
        # Kept so one test can put the genuine implementation back and cut the
        # `gh` call underneath it instead — the only honest way to reach its
        # failure branch.
        self.real_pr_head_sha = github.pr_head_sha
        patches = [
            mock.patch("ai_jury.cli.pr_diff", return_value=DIFF),
            mock.patch("ai_jury.cli.pr_context", return_value=""),
            mock.patch("ai_jury.github.pr_head_sha", return_value="a" * 40),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def _boom(*_a, **_k):
        raise RuntimeError("gh pr comment failed: HTTP 403")

    def _run(self, argv):
        """`run()` re-raises anything that is not SystemExit, so an unguarded `gh`
        failure would surface as a test *error*. What is under test is that it does not
        escape at all, so the escape is converted into an assertion failure — by this
        repo's own rule (#1289) an error is not a test failing.

        `raise self.failureException` rather than `self.fail()`: the two are the same
        assertion failure, but `self.fail()` reads to a static analyser as a branch that
        falls through and returns `None` beside an explicit `return` (CodeQL flagged it).
        Raising says what happens, and `from exc` keeps the escaped `gh` error attached."""
        try:
            return run(argv)
        except RuntimeError as exc:  # pragma: no cover - only on a regression
            raise self.failureException(
                f"the gh failure escaped main() instead of being reported: {exc!r}"
            ) from exc

    def test_a_failed_summary_post_exits_2_instead_of_raising(self):
        with mock.patch("ai_jury.cli.post_pr_comment", side_effect=self._boom):
            code, _, err = self._run(["--mock", "--pr", "7", "--post", "-q"])

        self.assertEqual(2, code)
        self.assertIn("error: could not post the verdict to PR #7", err)
        self.assertIn("HTTP 403", err)

    def test_a_failed_inline_post_reports_and_keeps_the_gate_exit(self):
        """Run without `-q`: the success line goes through `log()`, which `-q`
        silences, so a quiet run cannot see a failure that still claims success."""
        with mock.patch("ai_jury.cli.post_inline_comments", side_effect=self._boom):
            code, _, err = self._run(["--mock", "--pr", "7", "--post-inline"])

        self.assertEqual(0, code)
        self.assertIn("warning: could not post inline comments to PR #7", err)
        self.assertNotIn("posted inline comments", err)

    def test_a_failed_label_apply_reports_and_keeps_the_gate_exit(self):
        with mock.patch("ai_jury.cli.apply_labels", side_effect=self._boom):
            code, _, err = self._run(["--mock", "--pr", "7", "--label"])

        self.assertEqual(0, code)
        self.assertIn("warning: could not apply labels to PR #7", err)
        self.assertNotIn("applied labels", err)

    def test_a_landed_decoration_still_logs_its_success(self):
        """The counterweight: the two assertions above would also pass if the
        success line were simply deleted."""
        with (
            mock.patch("ai_jury.cli.post_inline_comments"),
            mock.patch("ai_jury.cli.apply_labels"),
        ):
            code, _, err = self._run(["--mock", "--pr", "7", "--post-inline", "--label"])

        self.assertEqual(0, code)
        self.assertIn("posted inline comments to PR #7", err)
        self.assertIn("applied labels to PR #7", err)

    def test_a_failed_issue_post_exits_2_as_well(self):
        """The issue arm is a separate return path from the PR arm."""
        with (
            mock.patch("ai_jury.cli.issue_body", return_value=DIFF),
            mock.patch("ai_jury.cli.post_issue_comment", side_effect=self._boom),
        ):
            code, _, err = self._run(["--mock", "--issue", "9", "--post", "-q"])

        self.assertEqual(2, code)
        self.assertIn("error: could not post the verdict to issue #9", err)

    def test_phased_posting_stops_at_the_first_failure(self):
        """Each phased comment is its own post; the run must not carry on writing the
        rest of a conversation whose opening never landed."""
        calls = []

        def fail_after_first(*a, **_k):
            calls.append(a)
            if len(calls) > 1:
                raise RuntimeError("gh pr comment failed: HTTP 403")

        with mock.patch("ai_jury.cli.post_pr_comment", side_effect=fail_after_first):
            code, _, err = self._run(
                ["--mock", "--pr", "7", "--post", "--post-mode", "phased", "-q"]
            )

        self.assertEqual(2, code)
        self.assertIn("error: could not post phased comment 2", err)
        self.assertEqual(2, len(calls), "posting continued past the failure")

    def test_a_known_head_sha_is_not_read_a_second_time(self):
        """A regression guard on behaviour this change must not lose, **not** a new
        behaviour: the pre-existing `marker_sha = head_sha or pr_head_sha(...)`
        already short-circuited. Rewriting it as an explicit if/else is what this
        test holds — an `or` is easy to turn back into an unconditional call."""
        with (
            mock.patch("ai_jury.cli.post_pr_comment"),
            mock.patch("ai_jury.github.pr_head_sha", return_value="b" * 40) as asked,
            mock.patch("ai_jury.github.pr_comment_bodies", return_value=[]),
        ):
            code, _, _ = self._run(["--mock", "--pr", "7", "--post", "--incremental", "-q"])

        self.assertEqual(0, code)
        self.assertEqual(1, asked.call_count, "gh was asked for a head sha it already had")

    def test_an_unreadable_head_sha_warns_and_still_posts(self):
        """The marker only narrows a later --incremental run; losing it is not worth
        refusing to post the verdict over.

        The failure is injected at `github._gh` — the process call — not at
        `pr_head_sha`. `pr_head_sha` is best-effort: it catches its own `RuntimeError`
        and returns "", so mocking *it* to raise tests a path production never takes,
        which is how an unreachable `try/except` here passed a green test.
        """
        posted = []
        with (
            mock.patch("ai_jury.github.pr_head_sha", self.real_pr_head_sha),
            mock.patch("ai_jury.github._gh", side_effect=self._boom),
            mock.patch(
                "ai_jury.cli.post_pr_comment", side_effect=lambda *a, **_k: posted.append(a)
            ),
        ):
            code, _, err = self._run(["--mock", "--pr", "7", "--post", "-q"])

        self.assertEqual(0, code)
        self.assertIn("will not carry an incremental marker", err)
        self.assertEqual(1, len(posted), "the verdict was not posted")

    def test_a_readable_head_sha_still_rides_the_verdict(self):
        """Counterweight: the warning test also passes if the marker is never
        attached at all."""
        posted = []
        with (
            mock.patch("ai_jury.github.pr_head_sha", return_value="c" * 40),
            mock.patch(
                "ai_jury.cli.post_pr_comment", side_effect=lambda *a, **_k: posted.append(a)
            ),
        ):
            code, _, err = self._run(["--mock", "--pr", "7", "--post", "-q"])

        self.assertEqual(0, code)
        self.assertNotIn("will not carry an incremental marker", err)
        self.assertIn("c" * 40, posted[0][1], "the reviewed-sha marker is missing")


if __name__ == "__main__":
    unittest.main()
