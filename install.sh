#!/bin/sh
# ai-jury standalone installer script
# Usage: curl -fsSL https://ai-jury.dev/install.sh | sh
set -eu

echo "🏛️  Installing ai-jury (cross-vendor multi-agent code review jury)..."

# 1. Prefer Homebrew if on macOS / Linux with brew installed
if command -v brew >/dev/null 2>&1; then
    echo "==> Installing via Homebrew (berkayturanci/ai-jury/ai-jury)..."
    brew install berkayturanci/ai-jury/ai-jury || true
    if command -v jury >/dev/null 2>&1; then
        echo "✨ ai-jury installed successfully via Homebrew!"
        jury --version
        exit 0
    fi
fi

# 2. Prefer pipx if available
if command -v pipx >/dev/null 2>&1; then
    echo "==> Installing via pipx..."
    pipx install ai-jury || pipx upgrade ai-jury
    if command -v jury >/dev/null 2>&1; then
        echo "✨ ai-jury installed successfully via pipx!"
        jury --version
        exit 0
    fi
fi

# 3. Fall back to python3 -m pip
if command -v python3 >/dev/null 2>&1; then
    echo "==> Installing via python3 -m pip..."
    python3 -m pip install --user --upgrade ai-jury
    if command -v jury >/dev/null 2>&1; then
        echo "✨ ai-jury installed successfully via pip!"
        jury --version
        exit 0
    fi
    # Check common user bin paths
    USER_BIN="$HOME/.local/bin"
    if [ -f "$USER_BIN/jury" ]; then
        echo "✨ ai-jury installed to $USER_BIN/jury!"
        echo "👉 Note: Please make sure $USER_BIN is in your PATH."
        echo "   export PATH=\"\$HOME/.local/bin:\$PATH\""
        "$USER_BIN/jury" --version
        exit 0
    fi
fi

echo "❌ Error: Could not install ai-jury. Please ensure Python 3.11+ or Homebrew is installed."
exit 1
