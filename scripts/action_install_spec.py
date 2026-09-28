"""The pip requirement the GitHub Action installs (#867).

`action.yml`'s `version` input used to default to the empty string, and empty meant
`pip install ai-jury`: the newest release on PyPI. So `uses: berkayturanci/ai-jury@v1.19.1`
ran whatever had shipped since, and pinning the Action pinned only its YAML.

The default is now the version the Action's own tree declares in `pyproject.toml`.
That file is read from `GITHUB_ACTION_PATH`, which GitHub documents for composite
actions as "the path where an action is located" — the checkout of the ref the
workflow named. It answers every ref shape the same way:

* `@vX.Y.Z` — `publish.yml` refuses to publish a tag that disagrees with
  `pyproject.toml`, so this is exactly that release.
* `@v1` — the `major-tag` job moves the alias only after `verify` has installed the
  release from PyPI and run it, so the tree it points at names a published version.
* a branch or SHA — the release that tree was cut from or is heading to. Between a
  version bump and its publication that version is not on PyPI yet, and the install
  fails rather than silently running something else; `version:` overrides it.

`github.action_ref` was the other candidate and is not used: it names only the ref,
which for `@v1`, a branch or a SHA is not a version at all, and GitHub's own
reference says it does not work in a composite action's `run:` (actions/runner#2473).

An explicit `version:` still wins. `latest` restores the old unpinned install, for
anyone who wants it by name. Anything else must look like a version, because it is
caller-supplied and becomes part of a pip argument.

Stdlib-only and importable by path, like the other scripts here, so the tests load
it without installing anything.
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path

PACKAGE = "ai-jury"

#: The explicit opt-out: install the newest release, the pre-#867 default.
LATEST = "latest"

#: What `version:` may hold. PEP 440 accepts a leading `v`, so `v1.20.1` is taken as
#: written. No whitespace, quotes, `;`, `<`, `>`, `=` or `@`: those would turn one
#: pinned requirement into a range, a marker, a URL or a second argument. Each
#: separator must be followed by a run of alphanumerics and a run can only end at a
#: separator, so there is one way to match any string and no backtracking blow-up.
_VERSION = re.compile(r"v?\d[0-9A-Za-z]*(?:[._+!-][0-9A-Za-z]+)*")


class SpecError(ValueError):
    """The Action cannot say which ai-jury to install."""


def requirement(explicit: str, pyproject_text: str | None) -> str:
    """The pip requirement for this run.

    *explicit* is the `version:` input (empty when not given); *pyproject_text* is
    the Action tree's `pyproject.toml`, or ``None`` when it could not be read.
    """
    wanted = explicit.strip()
    if wanted == LATEST:
        return PACKAGE
    if wanted:
        if not _VERSION.fullmatch(wanted):
            raise SpecError(f"version must be a release number or {LATEST!r} (got {wanted!r})")
        return f"{PACKAGE}=={wanted}"
    if pyproject_text is None:
        raise SpecError(
            "cannot read the Action's own pyproject.toml to pin the release; "
            f"pass `version:` (a release number, or {LATEST!r})"
        )
    try:
        declared = tomllib.loads(pyproject_text)["project"]["version"]
    except (tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise SpecError(f"the Action's pyproject.toml names no project version ({exc})") from exc
    if not isinstance(declared, str) or not _VERSION.fullmatch(declared):
        raise SpecError(
            f"the Action's pyproject.toml version is not a release number: {declared!r}"
        )
    return f"{PACKAGE}=={declared}"


def _read(action_path: str) -> str | None:
    if not action_path:
        return None
    try:
        return (Path(action_path) / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None


def main(environ: dict[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    try:
        spec = requirement(env.get("INPUT_VERSION", ""), _read(env.get("GITHUB_ACTION_PATH", "")))
    except SpecError as exc:
        print(f"ai-jury: {exc}", file=sys.stderr)
        return 2
    print(spec)
    return 0


if __name__ == "__main__":
    sys.exit(main())
