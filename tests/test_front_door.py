"""The two things a new user runs before anything else: the installer, and `brew test`.

Both drifted without anything noticing, and a pre-launch audit found each.

**The Homebrew formula's `test do` block.** It asserted that bare `jury --mock` fails
with "error: provide one of". #841 made bare `--mock` run the offline demo on a diff
bundled with the package, so every formula rendered from that template would ship a
`test do` that fails `brew test ai-jury`. Nothing ran it: the tap's own checks verify
the digest and the URL, never the test block. `FormulaTestBlockMatchesTheCli` runs each
`assert_match … shell_output("#{bin}/jury …")` line against the real CLI, so the block
cannot fall behind the code again without a red test.

**`install.sh` on a stock Linux.** Its last resort was `python3 -m pip install --user`.
On a PEP 668 "externally managed" interpreter — Debian 12, Ubuntu 23.04+, Fedora 38+,
Homebrew's python — pip refuses that, and under `set -eu` the script died with pip's raw
error before printing its own help. That is the machine a visitor pastes
`curl … | sh` into. `InstallScriptOnAPep668Machine` runs the script against fake
interpreters on a PATH that has nothing else, so it exercises the real control flow
without a network: the fake refuses `-m pip` exactly as an externally-managed Python
does, and the test fails if the script ever reaches for it.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "packaging" / "homebrew" / "ai-jury.rb.template"
INSTALL_SH = REPO_ROOT / "install.sh"

sys.path.insert(0, str(REPO_ROOT / "src"))
from ai_jury import __version__  # noqa: E402

# `assert_match "<needle>", shell_output("#{bin}/jury <args>[ 2>&1]"[, <exit>])`
_ASSERTION = re.compile(
    r'assert_match\s+"(?P<needle>[^"]*)",\s*'
    r'shell_output\("#\{bin\}/jury(?P<args>[^"]*?)(?P<merge>\s+2>&1)?"'
    r"(?:,\s*(?P<code>\d+))?\)"
)


def formula_assertions() -> list[tuple[str, list[str], bool, int]]:
    """Every `assert_match` in the template's `test do` block, as run-able parts."""
    body = TEMPLATE.read_text(encoding="utf-8")
    block = body[body.index("test do") :]
    found = []
    for m in _ASSERTION.finditer(block):
        needle = m.group("needle").replace("@VERSION@", __version__)
        found.append(
            (needle, m.group("args").split(), bool(m.group("merge")), int(m.group("code") or 0))
        )
    return found


def run_jury(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "src"))
    for key in list(env):
        if key.endswith("_API_KEY"):
            env.pop(key)
    with tempfile.TemporaryDirectory() as cwd:
        # UTF-8, because the CLI forces its own streams to UTF-8 and the report
        # carries emoji; the locale codec (cp1252 on Windows) cannot decode them.
        # `input=""` is a pipe: DEVNULL answers isatty() True on Windows, which
        # sends a bare `jury` down the first-impression overview instead of the
        # no-source error the formula asserts.
        return subprocess.run(
            [sys.executable, "-c", "from ai_jury.cli import main; raise SystemExit(main())", *args],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            env=env,
            input="",
            timeout=120,
        )


@unittest.skipIf(os.name == "nt", "Homebrew runs a formula's test block on macOS and Linux only")
class FormulaTestBlockMatchesTheCli(unittest.TestCase):
    def test_the_block_is_found_and_not_empty(self):
        """Vacuity: a reshaped block would otherwise pass every check below."""
        self.assertGreaterEqual(len(formula_assertions()), 3)

    def test_every_assertion_holds_against_the_real_cli(self):
        for needle, args, merged, code in formula_assertions():
            with self.subTest(args=args):
                result = run_jury(args)
                # `shell_output` fails the test when the exit status differs.
                self.assertEqual(result.returncode, code, result.stderr[-500:])
                seen = result.stdout + (result.stderr if merged else "")
                self.assertIn(needle, seen)

    def test_the_block_exercises_the_offline_demo(self):
        """The assertion that matters most: `brew test` runs the whole pipeline once."""
        self.assertIn(["--mock"], [args for _, args, _, _ in formula_assertions()])


def _executable(path: Path, text: str) -> None:
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@unittest.skipIf(os.name == "nt", "install.sh is a POSIX shell script")
class InstallScriptOnAPep668Machine(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.calls = self.tmp / "calls.log"
        # Only what the script needs from the system, so a real python, brew, pipx
        # or uv on the host can never be picked up instead of the fakes.
        for tool in ("mkdir", "ln", "rm", "cat", "dirname", "chmod"):
            real = shutil.which(tool)
            if real:
                (self.bin / tool).symlink_to(real)

    def fake_python(
        self, name: str = "python3", *, version_ok: bool = True, venv_ok: bool = True
    ) -> None:
        """An externally-managed interpreter: `-m pip` is refused, as PEP 668 does."""
        venv_bin = '"$3/bin"'
        _executable(
            self.bin / name,
            f"""
            #!/bin/sh
            echo "{name} $*" >> "{self.calls}"
            case "$1" in
              -c) exit {0 if version_ok else 1} ;;
              -m)
                case "$2" in
                  pip)
                    echo "error: externally-managed-environment" >&2
                    exit 1 ;;
                  venv)
                    [ "{int(venv_ok)}" = 1 ] || {{ echo "ensurepip is not available" >&2; exit 1; }}
                    mkdir -p {venv_bin}
                    cat > {venv_bin}/python <<'EOF'
            #!/bin/sh
            case "$1 $2" in
              "-m pip") d=$(dirname "$0"); printf '#!/bin/sh\\necho "jury 9.9.9"\\n' > "$d/jury"; chmod +x "$d/jury"; exit 0 ;;
            esac
            exit 1
            EOF
                    chmod +x {venv_bin}/python
                    exit 0 ;;
                esac ;;
            esac
            exit 1
            """,
        )

    def run_installer(
        self, *, piped: bool = False, **extra_env: str
    ) -> subprocess.CompletedProcess[str]:
        """Run install.sh. `piped` feeds it on stdin, as `curl … | sh` does."""
        env = {"PATH": str(self.bin), "HOME": str(self.home), **extra_env}
        if piped:
            return subprocess.run(
                ["/bin/sh"],
                input=INSTALL_SH.read_text(encoding="utf-8"),
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
        return subprocess.run(
            ["/bin/sh", str(INSTALL_SH)], capture_output=True, text=True, env=env, timeout=60
        )

    def fake_tool(self, name: str, body: str) -> None:
        _executable(self.bin / name, f'#!/bin/sh\necho "{name} $*" >> "{self.calls}"\n{body}\n')

    def calls_text(self) -> str:
        return self.calls.read_text(encoding="utf-8") if self.calls.exists() else ""

    def test_it_installs_into_a_private_venv_and_never_asks_system_pip(self):
        self.fake_python()
        result = self.run_installer()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        linked = self.home / ".local" / "bin" / "jury"
        self.assertTrue(linked.is_symlink(), "jury was not linked into ~/.local/bin")
        self.assertEqual(
            os.path.realpath(linked),
            os.path.realpath(self.home / ".local/share/ai-jury/bin/jury"),
        )
        self.assertIn("jury 9.9.9", result.stdout)
        self.assertNotIn("python3 -m pip", self.calls_text())
        self.assertNotIn("externally-managed", result.stderr)

    def test_it_says_where_jury_is_when_that_is_not_on_path(self):
        self.fake_python()
        result = self.run_installer()

        self.assertIn("is not on your PATH yet", result.stdout)

    def test_a_missing_venv_module_ends_in_advice_not_a_pip_error(self):
        """Debian ships the venv module separately (`python3-venv`)."""
        self.fake_python(venv_ok=False)
        result = self.run_installer()

        self.assertEqual(result.returncode, 1)
        self.assertIn("sudo apt install python3-venv", result.stderr)
        self.assertIn("pipx install ai-jury", result.stderr)
        self.assertNotIn("python3 -m pip", self.calls_text())

    def test_a_python_older_than_3_11_is_not_used(self):
        self.fake_python(version_ok=False)
        result = self.run_installer()

        self.assertEqual(result.returncode, 1)
        self.assertIn("Python 3.11 or newer", result.stderr)
        self.assertNotIn("-m venv", self.calls_text())

    def test_a_newer_versioned_python_is_found_behind_an_old_default(self):
        self.fake_python("python3", version_ok=False)
        self.fake_python("python3.12")
        result = self.run_installer()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("python3.12 -m venv", self.calls_text())

    def test_pipx_is_preferred_when_present(self):
        """The counterweight: the venv fallback must not run when a tool manager can."""
        self.fake_python()
        _executable(
            self.bin / "pipx",
            f"""
            #!/bin/sh
            echo "pipx $*" >> "{self.calls}"
            if [ "$1" = environment ]; then echo "$HOME/.local/bin"; exit 0; fi
            mkdir -p "$HOME/.local/bin" "$HOME/.pipx-env"
            printf '#!/bin/sh\\necho "jury 9.9.9"\\n' > "$HOME/.pipx-env/jury"
            chmod +x "$HOME/.pipx-env/jury"
            ln -sf "$HOME/.pipx-env/jury" "$HOME/.local/bin/jury"
            """,
        )
        result = self.run_installer()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("via pipx", result.stdout)
        self.assertNotIn("-m venv", self.calls_text())

    # Shell that installs a fake `jury` into $D the way pipx / uv / brew do: the
    # executable lives in the tool's environment and $D/jury is a symlink to it.
    _WRITE_JURY = (
        'mkdir -p "$D/.tool-env"; '
        'printf \'#!/bin/sh\\necho "jury 9.9.9"\\n\' > "$D/.tool-env/jury"; '
        'chmod +x "$D/.tool-env/jury"; ln -sf "$D/.tool-env/jury" "$D/jury"'
    )

    def test_pipx_into_its_own_bin_dir_is_not_mistaken_for_a_failure(self):
        """Lead round 1: success was judged by this script's BIN_DIR, so a pipx
        install into PIPX_BIN_DIR was read as a failure and a second copy went into
        the venv. The tool is now asked where it put `jury`."""
        self.fake_python()
        pipx_bin = self.home / "pipxbin"
        self.fake_tool(
            "pipx",
            f"""case "$1" in
              environment) echo "{pipx_bin}" ;;
              *) D="{pipx_bin}"; {self._WRITE_JURY} ;;
            esac""",
        )
        result = self.run_installer()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("via pipx", result.stdout)
        self.assertIn(str(pipx_bin), result.stdout)
        self.assertNotIn("-m venv", self.calls_text())
        self.assertFalse(
            (self.home / ".local/share/ai-jury").exists(), "a second copy was installed"
        )

    def test_a_pipx_too_old_to_report_its_bin_dir_still_counts(self):
        """Lead round 2. `pipx environment` arrived in pipx 1.1.0; Ubuntu 22.04 LTS
        ships 1.0.0, which exits 2 with usage. Reading that as "no bin dir" turned a
        good install into a failure: a venv went on top and overwrote pipx's link.
        The fallback is pipx's documented default, ~/.local/bin."""
        self.fake_python()
        d = self.home / ".local/bin"
        self.fake_tool(
            "pipx",
            f"""case "$1" in
              environment) echo "usage: pipx [-h] ..." >&2; exit 2 ;;
              *) D="{d}"; {self._WRITE_JURY} ;;
            esac""",
        )
        result = self.run_installer()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("via pipx", result.stdout)
        self.assertNotIn("-m venv", self.calls_text())
        self.assertIn(
            ".tool-env", str((d / "jury").readlink()), "pipx's link was replaced by a venv link"
        )

    def test_a_uv_too_old_to_report_its_bin_dir_still_counts(self):
        self.fake_python()
        d = self.home / ".local/bin"
        self.fake_tool(
            "uv",
            f"""case "$1 $2" in
              "tool dir") echo "error: unexpected argument '--bin'" >&2; exit 2 ;;
              "tool install") D="{d}"; {self._WRITE_JURY} ;;
            esac""",
        )
        result = self.run_installer()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("via uv", result.stdout)
        self.assertNotIn("-m venv", self.calls_text())

    def _pipx_into_local_bin(self):
        d = self.home / ".local/bin"
        self.fake_tool(
            "pipx",
            f"""case "$1" in
              environment) echo "{d}" ;;
              *) D="{d}"; {self._WRITE_JURY} ;;
            esac""",
        )

    def test_an_older_jury_earlier_on_path_is_named(self):
        """#849: an older `jury` on PATH (the old installer's `pip install --user`)
        kept running while the script reported the new install."""
        self.fake_python()
        self.fake_tool("jury", 'echo "jury 1.0.0-old"')
        self._pipx_into_local_bin()
        result = self.run_installer()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("the `jury` on your PATH is", result.stdout)
        self.assertIn(str(self.bin / "jury"), result.stdout)

    def test_a_symlinked_bin_dir_on_path_is_not_called_a_stranger(self):
        """The counterweight: ~/bin -> ~/.local/bin is the same directory."""
        self.fake_python()
        (self.home / ".local/bin").mkdir(parents=True)
        link = self.home / "linkbin"
        link.symlink_to(self.home / ".local/bin")
        self._pipx_into_local_bin()
        result = self.run_installer(PATH=f"{self.bin}:{link}")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("via pipx", result.stdout)
        self.assertNotIn("the `jury` on your PATH is", result.stdout)

    def test_an_old_uv_under_xdg_data_home_is_found(self):
        """uv's documented fallback includes $XDG_DATA_HOME/../bin before ~/.local/bin."""
        self.fake_python()
        data = self.home / "data"
        data.mkdir()
        d = self.home / "bin"
        self.fake_tool(
            "uv",
            f"""case "$1 $2" in
              "tool dir") echo "error: unexpected argument '--bin'" >&2; exit 2 ;;
              "tool install") D="{d}"; {self._WRITE_JURY} ;;
            esac""",
        )
        result = self.run_installer(XDG_DATA_HOME=str(data))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("via uv", result.stdout)
        self.assertNotIn("-m venv", self.calls_text())

    def test_an_older_jury_in_the_tools_own_bin_dir_is_named(self):
        """#850 lead: the old installer's `pip install --user` wrote a regular
        ~/.local/bin/jury — the same directory pipx uses. pipx refuses to overwrite a
        file it did not create and still exits 0, so the old binary keeps running."""
        self.fake_python()
        d = self.home / ".local/bin"
        d.mkdir(parents=True)
        (d / "jury").write_text('#!/bin/sh\necho "jury 1.0.0-old"\n', encoding="utf-8")
        (d / "jury").chmod(0o755)
        self.fake_tool(
            "pipx",
            f"""case "$1" in
              environment) echo "{d}" ;;
              *) echo "⚠️  File exists at {d}/jury, skipping." >&2; exit 0 ;;
            esac""",
        )
        result = self.run_installer()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("is not the link pipx creates", result.stdout)
        self.assertIn("pipx install --force ai-jury", result.stdout)

    def test_a_tool_link_draws_no_foreign_note(self):
        """The counterweight: the link a tool creates is not a stranger."""
        self.fake_python()
        self._pipx_into_local_bin()
        result = self.run_installer()

        self.assertIn("via pipx", result.stdout)
        self.assertNotIn("is not the link", result.stdout)

    def test_a_custom_bin_dir_does_not_cause_a_second_install(self):
        """The same defect through AI_JURY_BIN_DIR, the variable this script adds."""
        self.fake_python()
        default_bin = self.home / ".local/bin"
        self.fake_tool(
            "pipx",
            f"""case "$1" in
              environment) echo "{default_bin}" ;;
              *) D="{default_bin}"; {self._WRITE_JURY} ;;
            esac""",
        )
        result = self.run_installer(AI_JURY_BIN_DIR=str(self.home / "bin"))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("via pipx", result.stdout)
        self.assertNotIn("-m venv", self.calls_text())

    def test_an_already_installed_pipx_package_is_upgraded(self):
        """`pipx install` refuses an installed package; the script falls back to upgrade."""
        self.fake_python()
        d = self.home / ".local/bin"
        self.fake_tool(
            "pipx",
            f"""case "$1" in
              install) echo "already installed" >&2; exit 1 ;;
              upgrade) D="{d}"; {self._WRITE_JURY} ;;
              environment) echo "{d}" ;;
            esac""",
        )
        result = self.run_installer()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pipx upgrade ai-jury", self.calls_text())
        self.assertIn("via pipx", result.stdout)

    def test_uv_is_used_when_pipx_is_absent(self):
        self.fake_python()
        uv_bin = self.home / "uvbin"
        self.fake_tool(
            "uv",
            f"""case "$1 $2" in
              "tool dir") echo "{uv_bin}" ;;
              "tool install") D="{uv_bin}"; {self._WRITE_JURY} ;;
            esac""",
        )
        result = self.run_installer()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("via uv", result.stdout)
        self.assertNotIn("-m venv", self.calls_text())

    def fake_brew(self, prefix: Path, install: str | None = None) -> None:
        """A Homebrew that answers `--prefix` and links `jury` into `<prefix>/bin`."""
        body = install if install is not None else f'D="{prefix}/bin"; {self._WRITE_JURY}'
        self.fake_tool(
            "brew",
            f"""case "$1" in
              --prefix) echo "{prefix}" ;;
              *) {body} ;;
            esac""",
        )

    def test_homebrew_is_used_first_when_it_works(self):
        self.fake_python()
        self.fake_brew(self.tmp)
        result = self.run_installer()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("via Homebrew", result.stdout)
        self.assertNotIn("-m venv", self.calls_text())
        self.assertNotIn("Note:", result.stdout)

    def test_an_older_jury_earlier_on_path_is_not_taken_for_homebrews(self):
        """#850 third seat: success was judged by `command -v jury`, so an old
        `pip install --user` jury earlier on PATH was reported as Homebrew's, and the
        foreign-file note then told a Homebrew user to run pipx."""
        self.fake_python()
        old = self.home / ".local/bin"
        old.mkdir(parents=True)
        (old / "jury").write_text('#!/bin/sh\necho "jury 1.0.0-old"\n', encoding="utf-8")
        (old / "jury").chmod(0o755)
        self.fake_brew(self.tmp)
        result = self.run_installer(PATH=f"{old}:{self.bin}")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("via Homebrew", result.stdout)
        self.assertIn(f"the `jury` on your PATH is {old}/jury", result.stdout)
        self.assertIn(f"{self.bin}/jury", result.stdout)
        self.assertNotIn("pipx", result.stdout)

    def test_a_brew_that_links_nothing_is_not_a_success(self):
        """The same rule's other edge: brew exits 0, links no `jury`, and an older one
        on PATH must not stand in for it."""
        self.fake_python()
        old = self.home / ".local/bin"
        old.mkdir(parents=True)
        (old / "jury").write_text('#!/bin/sh\necho "jury 1.0.0-old"\n', encoding="utf-8")
        (old / "jury").chmod(0o755)
        self.fake_brew(self.tmp, install="exit 0")
        result = self.run_installer(PATH=f"{old}:{self.bin}")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Homebrew did not produce a working jury", result.stdout)

    def test_a_file_in_homebrews_bin_is_named_with_homebrews_fix(self):
        self.fake_python()
        (self.bin / "jury").write_text('#!/bin/sh\necho "jury 1.0.0-old"\n', encoding="utf-8")
        (self.bin / "jury").chmod(0o755)
        self.fake_brew(self.tmp, install="exit 0")
        result = self.run_installer()

        self.assertIn("is not the link Homebrew creates", result.stdout)
        self.assertIn("brew link --overwrite ai-jury", result.stdout)
        self.assertNotIn("pipx install --force", result.stdout)

    def test_a_failed_homebrew_install_falls_through(self):
        self.fake_python()
        self.fake_tool("brew", "exit 1")
        result = self.run_installer()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Homebrew did not produce a working jury", result.stdout)
        self.assertIn("private virtual environment", result.stdout)

    def test_a_tool_that_reads_stdin_cannot_swallow_a_piped_script(self):
        """Lead round 1: under `curl … | sh` a child reading stdin ate the rest of the
        script, and the run exited 0 with nothing installed."""
        self.fake_python()
        self.fake_tool("pipx", "cat >/dev/null; exit 1")
        result = self.run_installer(piped=True)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.home / ".local/bin/jury").is_symlink(), "nothing was installed")

    def test_a_second_run_succeeds(self):
        self.fake_python()
        first = self.run_installer()
        second = self.run_installer()

        self.assertEqual(
            (first.returncode, second.returncode), (0, 0), second.stdout + second.stderr
        )

    def test_a_directory_where_jury_would_go_is_refused_before_installing(self):
        self.fake_python()
        (self.home / ".local/bin/jury").mkdir(parents=True)
        result = self.run_installer()

        self.assertEqual(result.returncode, 1)
        self.assertIn("is a directory", result.stderr)
        self.assertNotIn("-m venv", self.calls_text())

    def test_a_non_link_jury_is_replaced_with_a_notice(self):
        self.fake_python()
        target = self.home / ".local/bin/jury"
        target.parent.mkdir(parents=True)
        target.write_text("#!/bin/sh\\necho mine\\n", encoding="utf-8")
        result = self.run_installer()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Replacing the existing", result.stdout)
        self.assertTrue(target.is_symlink())


if __name__ == "__main__":
    unittest.main()
