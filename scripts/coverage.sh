#!/usr/bin/env bash
# Measure test coverage and enforce the minimum gate.
#
# Configuration (source, branch coverage, omit/exclude rules and the
# ``fail_under`` threshold) lives in pyproject.toml under [tool.coverage.*],
# so this is just a thin one-command entry point that works the same locally
# and in CI.
#
# Usage: ./scripts/coverage.sh
set -euo pipefail

PYTHON="${PYTHON:-python3}"

"$PYTHON" -m coverage run -m unittest discover -s tests
"$PYTHON" -m coverage report
"$PYTHON" -m coverage html
echo "HTML report written to htmlcov/index.html"
