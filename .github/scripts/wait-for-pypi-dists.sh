#!/usr/bin/env bash
# Wait until PyPI's JSON API serves BOTH distributions of one version (#694).
#
# Uploading to PyPI is synchronous; being indexed by it is not. Cutting v1.16.0
# the release job uploaded the wheel and the sdist, asked
# `https://pypi.org/pypi/ai-jury/1.16.0/json` for the file list moments later,
# and got a 200 whose `urls` array did not yet contain the sdist. The render
# step read `packagetype == 'sdist'` out of that response with a bare
# `next(...)`, so it raised `StopIteration`, `sdist_url` stayed empty and `curl`
# rejected the empty string — exit 3, *after* the upload and *before* the GitHub
# Release. 1.16.0 was live on PyPI with no release, therefore no
# `releases/latest/download/ai-jury.rb`, and the tap correctly kept serving
# 1.15.1.
#
# The old wait asked the wrong question. It polled until the *version endpoint*
# answered at all, which it already did; what had not converged was the file
# list inside the answer. So this waits on the thing that is actually read.
#
# One implementation, called from three steps in `publish.yml`: the render step
# in `build-n-publish`, and both the index wait and the formula check in
# `verify`. `verify` had a five-minute poll of its own shape — this is that
# poll, made to check the file list and moved into a file both jobs can run, so
# there is no second copy to drift.
#
# On success:
#   - `$PYPI_JSON_OUT`  (default `pypi.json`) holds the metadata document, and
#   - `$PYPI_SDIST_OUT` (default `published-sdist.txt`) holds one line,
#     `<url> <sha256>`, for the published sdist — both straight from PyPI,
#     neither constructed. Read it with `read -r url sha < published-sdist.txt`.
#
# On failure the last line is a `::error::` naming the version and which
# distribution never appeared, so that — and not a Python traceback followed by
# a malformed-URL error — is what a maintainer reads first.
#
# Every input arrives through the environment, never through an Actions
# `${{ }}` expression: those are substituted into the script source before bash
# parses it (see tests/test_workflow_run_blocks.py).
#
#   PYPI_PROJECT           project name on PyPI            (default: ai-jury)
#   PYPI_VERSION           version to wait for             (default: ${GITHUB_REF_NAME#v})
#   PYPI_BASE_URL          index origin                    (default: https://pypi.org)
#   PYPI_ATTEMPTS          poll attempts                   (default: 30)
#   PYPI_INTERVAL_SECONDS  seconds between attempts        (default: 10)
#   PYPI_JSON_OUT          where to write the metadata     (default: pypi.json)
#   PYPI_SDIST_OUT         where to write "<url> <sha256>" (default: published-sdist.txt)
#   PYPI_PYTHON            interpreter for the JSON read   (default: python3)
#
# The defaults are 30 × 10s — the same five-minute budget `verify` has always
# used, and the budget the render step was given for the same reason.
set -euo pipefail

project="${PYPI_PROJECT:-ai-jury}"
version="${PYPI_VERSION:-${GITHUB_REF_NAME:-}}"
version="${version#v}"
base_url="${PYPI_BASE_URL:-https://pypi.org}"
attempts="${PYPI_ATTEMPTS:-30}"
interval="${PYPI_INTERVAL_SECONDS:-10}"
json_out="${PYPI_JSON_OUT:-pypi.json}"
sdist_out="${PYPI_SDIST_OUT:-published-sdist.txt}"
python_bin="${PYPI_PYTHON:-python3}"

if [ -z "$version" ]; then
  echo "::error::wait-for-pypi-dists.sh: no version to wait for (set PYPI_VERSION or GITHUB_REF_NAME)"
  exit 2
fi

url="${base_url}/pypi/${project}/${version}/json"

# Read the file list out of the fetched document. Exit 0 with both
# distributions present (and `$sdist_out` written), 3 with the missing
# packagetypes on stdout, 4 when the document cannot be read as the expected
# shape. Nothing here may raise: a traceback in place of a diagnosis is the
# failure this script exists to remove.
read_file_list() {
  "$python_bin" - "$json_out" "$sdist_out" <<'PY'
import json
import sys

json_path, sdist_path = sys.argv[1], sys.argv[2]
WANTED = ("sdist", "bdist_wheel")

try:
    with open(json_path, encoding="utf-8") as handle:
        payload = json.load(handle)
except (OSError, ValueError) as exc:
    print(f"the metadata document is unreadable: {exc}", file=sys.stderr)
    raise SystemExit(4)

if not isinstance(payload, dict):
    print("the metadata document is not a JSON object", file=sys.stderr)
    raise SystemExit(4)

found = {}
for entry in payload.get("urls") or ():
    if isinstance(entry, dict):
        found.setdefault(entry.get("packagetype"), []).append(entry)

missing = [want for want in WANTED if not found.get(want)]
if missing:
    print(" ".join(missing))
    raise SystemExit(3)

sdist = found["sdist"][0]
sdist_url = sdist.get("url") or ""
sdist_sha = (sdist.get("digests") or {}).get("sha256") or ""
if not sdist_url or not sdist_sha:
    # An sdist PyPI lists without a url or a sha256 is not one we can render a
    # formula from, and "" is exactly the value that produced the malformed-URL
    # error. Report it as not-yet-served rather than writing an empty pair.
    print("sdist", file=sys.stdout)
    print("PyPI lists an sdist with no url or no sha256 digest", file=sys.stderr)
    raise SystemExit(3)

with open(sdist_path, "w", encoding="utf-8") as handle:
    handle.write(f"{sdist_url} {sdist_sha}\n")
PY
}

budget="$((attempts * interval))"
missing="metadata"

for attempt in $(seq 1 "$attempts"); do
  if curl -fsSL "$url" -o "$json_out"; then
    set +e
    missing="$(read_file_list)"
    status=$?
    set -e
    case "$status" in
      0)
        echo "PyPI serves ${project} ${version}: sdist and wheel both indexed (attempt ${attempt}/${attempts})"
        cat "$sdist_out"
        exit 0
        ;;
      3) : ;;  # reported below, then retried
      *)
        missing="a readable file list"
        ;;
    esac
  else
    missing="metadata"
  fi
  if [ "$attempt" -lt "$attempts" ]; then
    echo "PyPI has not served ${project} ${version} yet — missing ${missing} (attempt ${attempt}/${attempts}); retrying in ${interval}s"
    sleep "$interval"
  fi
done

# The first thing a maintainer reads. It names the version and the distribution
# that never appeared, because "which artifact is PyPI still missing" is the
# only question worth answering here, and the recovery is to re-run the job.
echo "::error::PyPI never served ${project} ${version}: missing ${missing} after ${attempts} attempts over ${budget}s. Nothing was rendered or published from it. Check ${url} and re-run this job once the file list is complete."
exit 1
