#!/usr/bin/env bash
# Install one published requirement, retrying while PyPI's index catches up (#770).
#
# `publish.yml`'s verify job waits for PyPI to serve a release and then installs
# it. Those two reads are of DIFFERENT surfaces. `wait-for-pypi-dists.sh` polls
# the JSON API, `https://pypi.org/pypi/<project>/<version>/json`; `pip` resolves
# through the simple index, `https://pypi.org/simple/<project>/`, which is served
# from its own cache and lags behind it.
#
# The step this replaces already knew that — it carried the comment "The JSON API
# can know a version before the installer index serves it" above a six-attempt
# loop. The loop could not do it:
#
#   for attempt in 1 2 3 4 5 6; do
#     if timeout 90 /tmp/verify-venv/bin/python -m pip install --timeout 30 "ai-jury==${version}"
#
# PyPI serves the simple index with `cache-control: max-age=600, public`, and
# pip's HTTP cache honours a fresh response without revalidating. So attempt 1,
# made while the index lags, records the version list that is *missing* the
# release, and attempts 2 through 6 read that list back off local disk without
# opening a connection. Six attempts ten seconds apart span fifty seconds; the
# cached answer stays fresh for six hundred. The loop retried its own answer,
# which is worse than no retry because it made the job look protected.
#
# `--no-cache-dir` is therefore not a detail: it is the line that makes every
# other line here mean something. It also makes each attempt a real download,
# which is what a job called *verify the published release* should be doing.
#
# The budget is `PYPI_ATTEMPTS` x `PYPI_INTERVAL_SECONDS` — the same two
# variables `wait-for-pypi-dists.sh` reads, under the same names and with the
# same defaults, so the two waits cannot be given different patience by an edit
# that only remembers one of them. The old loop's fifty seconds were hardcoded
# separately from the index wait's three hundred, which is exactly that drift.
#
# It is a wait and not a softener. When the budget is genuinely spent this exits
# non-zero with an `::error::` naming the requirement, the attempts made and the
# elapsed seconds, which fails the step and fails the job. There is no path
# through this file that reports success without an install.
#
# Every attempt's own output goes to stdout rather than being captured, because
# the failure this must stay diagnosable for is the other one: a release whose
# wheel really is broken retries for the full budget and then fails with pip's
# own words in the log. Telling the two apart by *parsing* pip's error text was
# the alternative — it saves five minutes on a job that is about to fail anyway,
# in exchange for a guess about wording that changes between pip releases.
#
# Inputs arrive through the environment, never through an Actions `${{ }}`
# expression: those are substituted into this file's source before bash parses it
# (see tests/test_workflow_run_blocks.py).
#
#   INSTALLER              the pip to run                     (required)
#   REQUIREMENT            what to install, e.g. `pkg==1.2.3` (required)
#   PYPI_ATTEMPTS          install attempts                   (default: 30)
#   PYPI_INTERVAL_SECONDS  seconds between attempts           (default: 10)
#
# `INSTALLER` and not `PIP_…`: pip reads every `PIP_<OPTION>` variable in the
# environment as one of its own command-line options, so a name in that space
# would be passed straight back into the thing it names.
set -euo pipefail

installer="${INSTALLER:-}"
requirement="${REQUIREMENT:-}"
attempts="${PYPI_ATTEMPTS:-30}"
interval="${PYPI_INTERVAL_SECONDS:-10}"

# Exit 2, distinct from the exhausted-budget 1: a mistake in the call is not a
# slow index and must not be reported as one. A budget spent waiting for
# something nobody asked for, ending in an error that blames PyPI, is the second
# failure mode this file exists to avoid — the same rule
# `wait-for-pypi-dists.sh` follows for its own configuration.
fail_config() {
  echo "::error::pip-install-with-retry.sh is misconfigured: $1"
  exit 2
}

[ -n "$installer" ] || fail_config "INSTALLER is empty; there is no installer to run"
[ -n "$requirement" ] || fail_config "REQUIREMENT is empty; there is nothing to install"

# An installer that cannot run is a configuration mistake, not a slow index.
# Undiagnosed it exits 127 from every attempt, which the loop below would read as
# "the index has not caught up yet" — burning the whole budget and then blaming
# PyPI for a missing pip.
command -v "$installer" >/dev/null 2>&1 \
  || fail_config "INSTALLER='${installer}' is not an executable this runner can find"

# A count that is not a count must be diagnosed rather than left to `seq`, whose
# complaint is about an operand and reads like a broken index rather than a
# typo'd knob.
require_whole_number() {
  case "$2" in
    "" | *[!0-9]*) fail_config "$1 must be a whole number, not '$2'" ;;
  esac
}

require_whole_number PYPI_ATTEMPTS "$attempts"
require_whole_number PYPI_INTERVAL_SECONDS "$interval"

# Base ten explicitly: `08` is a valid attempt count and an invalid octal literal.
attempts="$((10#$attempts))"
interval="$((10#$interval))"

# Zero attempts would fall straight past the loop into the failure below, so it
# is already loud — but it would fail every release with a message naming PyPI
# for a knob nobody meant to set to nothing.
[ "$attempts" -ge 1 ] \
  || fail_config "PYPI_ATTEMPTS must be at least 1, not '${attempts}'"

started="$(date +%s)"

for attempt in $(seq 1 "$attempts"); do
  if "$installer" install --no-cache-dir --disable-pip-version-check "$requirement"; then
    echo "installed ${requirement} on attempt ${attempt}/${attempts}"
    exit 0
  fi
  # No sleep after the last attempt: the budget is the waiting between tries, and
  # a trailing one only delays the failure it has already decided on.
  if [ "$attempt" -lt "$attempts" ]; then
    echo "the index cannot resolve ${requirement} yet (attempt ${attempt}/${attempts}); retrying in ${interval}s"
    sleep "$interval"
  fi
done

# The first thing a maintainer reads. It names what was waited for and how long,
# because "did PyPI ever serve this" is the question that decides whether the
# recovery is a re-run or a new version.
elapsed="$(($(date +%s) - started))"
echo "::error::PyPI never served an installable ${requirement}: pip could not resolve it in ${attempts} attempts over ${elapsed}s. The JSON API listed both distributions, so the upload itself succeeded; re-run this job once the simple index catches up, and treat it as a broken release only if it does not."
exit 1
