#!/bin/sh
# ai-jury standalone installer script
# Usage: curl -fsSL https://ai-jury.dev/install.sh | sh
#
# Tries, in order: Homebrew, pipx, uv, and finally a private virtual environment
# under ~/.local/share/ai-jury with `jury` linked into ~/.local/bin.
#
# It never runs `pip install` against the system interpreter. On a PEP 668
# "externally managed" Python — stock Debian 12, Ubuntu 23.04+, Fedora 38+,
# Homebrew's python — pip refuses that outright, `--user` included, and the old
# last resort died there with pip's raw error before this script could say
# anything. A virtual environment is exempt from PEP 668 by design.
#
# Everything runs inside `main`, called on the last line. Under `curl … | sh` the
# shell reads this script from stdin as it goes, so a child that reads stdin
# would otherwise swallow the rest of the script; defining the whole body first
# means nothing is left on stdin to swallow. The tool calls get </dev/null too.
set -eu

PACKAGE="ai-jury"
INSTALL_DIR="${AI_JURY_HOME:-$HOME/.local/share/ai-jury}"
BIN_DIR="${AI_JURY_BIN_DIR:-$HOME/.local/bin}"

say() { printf '%s\n' "$*"; }
fail() {
    printf '❌ %s\n' "$*" >&2
    exit 1
}

# The physical directory a path names, so two spellings of one directory compare equal.
real_dir() {
    (cd "$1" 2>/dev/null && pwd -P) || printf '%s\n' "$1"
}

# pipx, uv and Homebrew all install `jury` as a symlink into their own environment,
# and this script's venv fallback links it too. A regular file there, after a tool
# reported success, is one the tool did not write: an older install — the previous
# installer's `pip install --user` put one exactly there — that pipx refuses to
# overwrite, so it keeps running while the tool says it succeeded (#849).
foreign_jury_note() {
    if [ -e "$2/jury" ] && [ ! -L "$2/jury" ]; then
        case "$1" in
            pipx) fix="pipx install --force ai-jury" ;;
            uv) fix="uv tool install --force ai-jury" ;;
            Homebrew) fix="brew link --overwrite ai-jury" ;;
            *) fix="" ;;
        esac
        say "👉 Note: $2/jury is not the link $1 creates — an older jury is in the way,"
        say "   and it is the one that runs. Remove it and run this again."
        if [ -n "$fix" ]; then
            say "   Or let $1 replace it: $fix"
        fi
    fi
}

# Report success and exit. $1 names the method, $2 is the directory the method
# puts `jury` in. That directory may not be on PATH yet, so say where it is
# rather than claim it is runnable.
finish() {
    if command -v jury >/dev/null 2>&1; then
        say "✨ ai-jury installed via $1."
        # A `jury` earlier on PATH — the old installer's `pip install --user`, say —
        # would keep running while this reported success (#849). Compare the real
        # directories, so a symlinked bin dir on PATH is not reported as a stranger.
        on_path=$(command -v jury)
        if [ -x "$2/jury" ] && [ "$(real_dir "$(dirname "$on_path")")" != "$(real_dir "$2")" ]; then
            say "👉 Note: the \`jury\` on your PATH is $on_path, not the one just installed"
            say "   at $2/jury. Remove the older one, or put $2 earlier on PATH."
        fi
        foreign_jury_note "$1" "$2"
        jury --version
    else
        say "✨ ai-jury installed via $1 to $2/jury."
        foreign_jury_note "$1" "$2"
        say "👉 $2 is not on your PATH yet. Add it, e.g.:"
        say "   export PATH=\"$2:\$PATH\""
        "$2/jury" --version
    fi
    exit 0
}

# Where a tool manager put `jury`: on PATH, or in the directory the tool itself
# reports. Judging a tool's success by this script's own BIN_DIR is wrong — with
# PIPX_BIN_DIR, UV_TOOL_BIN_DIR or AI_JURY_BIN_DIR set, a successful install was
# read as a failure and a second copy went into the venv.
#
# A tool too old to answer falls back to its documented default. `pipx
# environment` arrived in pipx 1.1.0, and Ubuntu 22.04 LTS ships pipx 1.0.0 — the
# very `apt install pipx` this script recommends. Returning nothing there read a
# good install as failed, installed the venv on top, and overwrote pipx's own
# `jury` link.
tool_bin_dir() {
    case "$1" in
        pipx)
            pipx environment --value PIPX_BIN_DIR </dev/null 2>/dev/null ||
                printf '%s\n' "${PIPX_BIN_DIR:-$HOME/.local/bin}"
            ;;
        uv)
            # uv's own order: UV_TOOL_BIN_DIR, XDG_BIN_HOME, $XDG_DATA_HOME/../bin,
            # then ~/.local/bin.
            uv tool dir --bin </dev/null 2>/dev/null || {
                if [ -n "${UV_TOOL_BIN_DIR:-}" ]; then
                    printf '%s\n' "$UV_TOOL_BIN_DIR"
                elif [ -n "${XDG_BIN_HOME:-}" ]; then
                    printf '%s\n' "$XDG_BIN_HOME"
                elif [ -n "${XDG_DATA_HOME:-}" ]; then
                    printf '%s\n' "$XDG_DATA_HOME/../bin"
                else
                    printf '%s\n' "$HOME/.local/bin"
                fi
            }
            ;;
    esac
}

found_in() {
    command -v jury >/dev/null 2>&1 || { [ -n "$1" ] && [ -x "$1/jury" ]; }
}

main() {
    say "🏛️  Installing ai-jury (cross-vendor multi-agent code review jury)..."

    # 1. Homebrew
    if command -v brew >/dev/null 2>&1; then
        say "==> Installing via Homebrew (berkayturanci/ai-jury/ai-jury)..."
        # Judge success by Homebrew's own bin directory, not by `command -v jury`:
        # an older jury earlier on PATH would pass that test and be reported as the
        # new install (#850 review).
        if brew install berkayturanci/ai-jury/ai-jury </dev/null; then
            brew_bin="$(brew --prefix </dev/null 2>/dev/null)/bin"
            if [ -x "$brew_bin/jury" ]; then
                finish Homebrew "$brew_bin"
            fi
        fi
        say "   Homebrew did not produce a working jury; trying the next method."
    fi

    # 2. pipx — `install` refuses a package that is already installed, so fall
    #    back to `upgrade`.
    if command -v pipx >/dev/null 2>&1; then
        say "==> Installing via pipx..."
        if pipx install ai-jury </dev/null || pipx upgrade ai-jury </dev/null; then
            dir=$(tool_bin_dir pipx)
            if found_in "$dir"; then
                finish pipx "$dir"
            fi
        fi
        say "   pipx did not produce a working jury; trying the next method."
    fi

    # 3. uv
    if command -v uv >/dev/null 2>&1; then
        say "==> Installing via uv..."
        if uv tool install --force "$PACKAGE" </dev/null; then
            dir=$(tool_bin_dir uv)
            if found_in "$dir"; then
                finish uv "$dir"
            fi
        fi
        say "   uv did not produce a working jury; trying the next method."
    fi

    # 4. A private virtual environment. `python3` first; the versioned names
    #    cover a machine whose default python3 is older than 3.11.
    PY=""
    for candidate in python3 python3.14 python3.13 python3.12 python3.11; do
        if command -v "$candidate" >/dev/null 2>&1 &&
            "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' </dev/null 2>/dev/null; then
            PY="$candidate"
            break
        fi
    done
    if [ -z "$PY" ]; then
        fail "ai-jury needs Python 3.11 or newer (or Homebrew, pipx or uv), and none was found.
   Install one of them and run this again — for example: sudo apt install pipx && pipx install ai-jury"
    fi

    if [ -d "$BIN_DIR/jury" ] && [ ! -L "$BIN_DIR/jury" ]; then
        fail "$BIN_DIR/jury is a directory, so jury cannot be linked there. Move it aside, or set AI_JURY_BIN_DIR."
    fi

    say "==> Installing into a private virtual environment at $INSTALL_DIR (using $PY)..."
    if ! "$PY" -m venv "$INSTALL_DIR" </dev/null; then
        fail "could not create a virtual environment with $PY.
   On Debian or Ubuntu the venv module is packaged separately: sudo apt install python3-venv
   Or install pipx and let it manage this: sudo apt install pipx && pipx install ai-jury"
    fi
    if ! "$INSTALL_DIR/bin/python" -m pip install --upgrade --quiet "$PACKAGE" </dev/null; then
        fail "pip could not install $PACKAGE into $INSTALL_DIR."
    fi
    mkdir -p "$BIN_DIR"
    if [ -e "$BIN_DIR/jury" ] && [ ! -L "$BIN_DIR/jury" ]; then
        say "   Replacing the existing $BIN_DIR/jury (not a link) with a link to this install."
    fi
    ln -sf "$INSTALL_DIR/bin/jury" "$BIN_DIR/jury"
    finish "a private virtual environment ($INSTALL_DIR)" "$BIN_DIR"
}

main "$@"
