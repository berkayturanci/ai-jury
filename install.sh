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
set -eu

PACKAGE="ai-jury"
INSTALL_DIR="${AI_JURY_HOME:-$HOME/.local/share/ai-jury}"
BIN_DIR="${AI_JURY_BIN_DIR:-$HOME/.local/bin}"

say() { printf '%s\n' "$*"; }
fail() {
    printf '❌ %s\n' "$*" >&2
    exit 1
}

# Report success. `jury` may have landed in a bin directory that is not on
# PATH yet (pipx, uv and the venv fallback all default to ~/.local/bin), so say
# where it is rather than claim it is runnable.
finish() {
    if command -v jury >/dev/null 2>&1; then
        say "✨ ai-jury installed via $1."
        jury --version
    else
        say "✨ ai-jury installed via $1 to $BIN_DIR/jury."
        say "👉 $BIN_DIR is not on your PATH yet. Add it, e.g.:"
        say "   export PATH=\"$BIN_DIR:\$PATH\""
        "$BIN_DIR/jury" --version
    fi
    exit 0
}

installed() {
    command -v jury >/dev/null 2>&1 || [ -x "$BIN_DIR/jury" ]
}

say "🏛️  Installing ai-jury (cross-vendor multi-agent code review jury)..."

# 1. Homebrew
if command -v brew >/dev/null 2>&1; then
    say "==> Installing via Homebrew (berkayturanci/ai-jury/ai-jury)..."
    if brew install berkayturanci/ai-jury/ai-jury && command -v jury >/dev/null 2>&1; then
        finish Homebrew
    fi
    say "   Homebrew did not produce a working jury; trying the next method."
fi

# 2. pipx
if command -v pipx >/dev/null 2>&1; then
    say "==> Installing via pipx..."
    if { pipx install ai-jury || pipx upgrade ai-jury; } && installed; then
        finish pipx
    fi
    say "   pipx did not produce a working jury; trying the next method."
fi

# 3. uv
if command -v uv >/dev/null 2>&1; then
    say "==> Installing via uv..."
    if uv tool install --force "$PACKAGE" && installed; then
        finish uv
    fi
    say "   uv did not produce a working jury; trying the next method."
fi

# 4. A private virtual environment. `python3` first; the versioned names cover a
#    machine whose default python3 is older than 3.11.
PY=""
for candidate in python3 python3.14 python3.13 python3.12 python3.11; do
    if command -v "$candidate" >/dev/null 2>&1 &&
        "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        PY="$candidate"
        break
    fi
done
if [ -z "$PY" ]; then
    fail "ai-jury needs Python 3.11 or newer (or Homebrew, pipx or uv), and none was found.
   Install one of them and run this again — for example: sudo apt install pipx && pipx install ai-jury"
fi

say "==> Installing into a private virtual environment at $INSTALL_DIR (using $PY)..."
if ! "$PY" -m venv "$INSTALL_DIR"; then
    fail "could not create a virtual environment with $PY.
   On Debian or Ubuntu the venv module is packaged separately: sudo apt install python3-venv
   Or install pipx and let it manage this: sudo apt install pipx && pipx install ai-jury"
fi
if ! "$INSTALL_DIR/bin/python" -m pip install --upgrade --quiet "$PACKAGE"; then
    fail "pip could not install $PACKAGE into $INSTALL_DIR."
fi
mkdir -p "$BIN_DIR"
ln -sf "$INSTALL_DIR/bin/jury" "$BIN_DIR/jury"
finish "a private virtual environment ($INSTALL_DIR)"
