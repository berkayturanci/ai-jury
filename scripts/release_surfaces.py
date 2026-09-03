"""The one table of files that carry this project's version.

Version lockstep used to be guarded by three disjoint lists — `VERSION_MARKERS`
in `scripts/verify_merge.py`, `SITE_SURFACES` in `tests/test_release_metadata.py`,
and a pair of hard-coded assertions in `tests/test_homebrew_formula.py`. A ninth
surface therefore had to be registered in three places, and the one that was
missed is the one that went stale: `website/index.html` and `website/app.js` sat
at v1.14.4 through two releases (#646) while every list that *did* mention them
disagreed about which list was authoritative.

`RELEASE_SURFACES` is now that single list, and #665 is the change that made
adding a surface a one-line edit reviewed once. All three guards read it:

* `scripts/verify_merge.py` — cross-file agreement plus monotonicity vs. git tags,
  run on every merge.
* `scripts/verify_merge.py --check-surfaces` (`make release-check`) — the same
  table without the git-tag requirement, so a maintainer can run it from a
  shallow clone before cutting a release.
* `tests/test_release_metadata.py` — every surface names what `pyproject.toml` does.

The formula was a fourth reader until #666, which deleted `Formula/ai-jury.rb`
outright: a committed formula names an sdist url and digest that are unknowable
until the tag is pushed, so it could never be right. What remains is
`packaging/homebrew/ai-jury.rb.template`, whose version is the literal `@VERSION@`
until the release renders it. A placeholder cannot go stale, so the template is
not a surface, and `tests/test_homebrew_formula.py` checks the rendering rather
than a version.

Patterns are anchored on surrounding context rather than scanning for anything
shaped like a version: `website/app.js` contains numbers such as `1.19.214` and
`1.12.566` in its demo data, and a blanket scan would either fail on those or be
weakened until it caught nothing. Adding a surface is a line here, and that line
is the review.

Stdlib-only by policy, and importable without installing anything: the guards
load it by path so it works from a test, from `python scripts/verify_merge.py`,
and from a checkout that was never pip-installed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import NamedTuple

#: A surface's pattern is either a regex with one capturing group, or a callable
#: that takes the file's text and returns every version it names. JSON manifests
#: use the callable form: a regex over JSON would match the first `"version"`
#: key in the file rather than the manifest's own.
Pattern = str | Callable[[str], Iterable[str]]

SEMVER = r"(\d+\.\d+\.\d+)"


def _json_version(text: str) -> list[str]:
    """The top-level `version` of a JSON manifest."""
    version = json.loads(text).get("version")
    return [version] if isinstance(version, str) else []


def _changelog_top_section(text: str) -> list[str]:
    """The version of the newest released section, ignoring `## [Unreleased]`.

    Only the top one: every past release header is still in the file, and
    returning all of them would make the changelog disagree with itself.
    """
    match = re.search(rf"^##\s*\[{SEMVER}\]", text, flags=re.MULTILINE)
    return [match.group(1)] if match else []


class Surface(NamedTuple):
    """One file that names the version, and how to read the version out of it."""

    #: Repository-relative path, POSIX separators.
    path: str
    #: What kind of surface this is — package metadata, a lock file, a plugin
    #: manifest, or something a user reads. Informational for humans and for
    #: error messages; the guards check every kind.
    kind: str
    #: Regex with one group, or a callable over the file's text.
    pattern: Pattern
    #: True when a checkout without this file is itself a defect. False means
    #: "absent is not drift" — a sparse or partial tree simply has nothing to
    #: compare, which is how `scripts/verify_merge.py` has always treated
    #: `uv.lock`.
    required: bool = False

    def find(self, text: str) -> list[str]:
        """Every version this surface names in `text`."""
        if callable(self.pattern):
            return list(self.pattern(text))
        return re.findall(self.pattern, text, flags=re.MULTILINE)


#: Every file that carries the project's version. A surface missing from here is
#: a surface nothing watches — `uv.lock` was outside the set and sat a release
#: behind on main while the check reported agreement (#556); the website sat two
#: releases behind (#646).
#:
#: The Homebrew formula is deliberately absent. It used to be listed for its
#: version markers, and it was also the one surface that could not be made
#: correct: its url and digest are unknowable until the tag exists. #666 deleted
#: it rather than keep repairing it, so there is no file left to list — the
#: template names `@VERSION@` and the release renders it.
RELEASE_SURFACES: tuple[Surface, ...] = (
    Surface("pyproject.toml", "package", rf'^version\s*=\s*"{SEMVER}"', required=True),
    Surface("src/ai_jury/__init__.py", "package", rf'__version__\s*=\s*"{SEMVER}"', required=True),
    Surface("CHANGELOG.md", "changelog", _changelog_top_section, required=True),
    Surface("uv.lock", "lock", rf'name\s*=\s*"ai-jury"\s*\nversion\s*=\s*"{SEMVER}"'),
    Surface(".claude-plugin/plugin.json", "manifest", _json_version),
    Surface(".codex-plugin/plugin.json", "manifest", _json_version),
    Surface("website/index.html", "site", rf'id="site-version"[^>]*>v{SEMVER}</a>'),
    Surface("website/app.js", "site", rf"rev: v{SEMVER}"),
    Surface("README.md", "site", rf"rev: v{SEMVER}"),
    Surface("README.md", "site", rf"Active \(v{SEMVER}\)"),
    Surface("docs/cookbook.md", "site", rf"rev: v{SEMVER}"),
)

#: The distinct paths, in table order, so a guard's success line can name what it
#: actually compared. `scripts/verify_merge.py` used to print a hard-coded three
#: files and claim ">= latest tag" even where no tag had been read (#556); a
#: success line asserting a check that did not run is worse than no line at all.
SURFACE_PATHS: tuple[str, ...] = tuple(dict.fromkeys(s.path for s in RELEASE_SURFACES))


def declared_version(root: Path) -> str | None:
    """The version `pyproject.toml` declares, or None if it cannot be read.

    The authority the other surfaces are compared against: the package is what
    ships, and everything else is a copy of its number.
    """
    for surface in RELEASE_SURFACES:
        if surface.path != "pyproject.toml":
            continue
        path = root / surface.path
        if not path.is_file():
            return None
        found = surface.find(path.read_text(encoding="utf-8"))
        if found:
            return found[0]
    return None


def _scan(root: Path) -> tuple[dict[str, set[str]], list[str], list[str]]:
    """Read every surface once.

    Returns the versions found per path, the paths listed here but absent from
    `root`, and the paths whose pattern matched nothing.
    """
    found: dict[str, set[str]] = {}
    absent: list[str] = []
    unmatched: list[str] = []
    for surface in RELEASE_SURFACES:
        path = root / surface.path
        if not path.is_file():
            if surface.path not in absent:
                absent.append(surface.path)
            continue
        versions = surface.find(path.read_text(encoding="utf-8"))
        if not versions:
            unmatched.append(surface.path)
            continue
        found.setdefault(surface.path, set()).update(versions)
    return found, absent, unmatched


def find_versions(root: Path) -> dict[str, set[str]]:
    """Every version the tree at `root` names, keyed by the file that names it.

    A path listed twice (`README.md` names the version two ways) contributes one
    entry holding both readings, so a README that disagrees with itself shows up
    as a set of size two rather than silently taking the first.
    """
    return _scan(root)[0]


def problems(root: Path) -> list[str]:
    """Surfaces that cannot be read, without judging *which* version is right.

    Absence is only a defect for `required` surfaces: `scripts/verify_merge.py`
    runs against arbitrary checkouts, and a tree with no `uv.lock` or no website
    is not drift. A file that exists but whose pattern matches nothing always is
    — that is the surface having moved out from under the guard.
    """
    _, absent, unmatched = _scan(root)
    required = {s.path for s in RELEASE_SURFACES if s.required}
    errors = [f"{path} not found" for path in absent if path in required]
    errors.extend(
        f"{path}: no version found, so the surface moved and nothing is watching it"
        for path in unmatched
    )
    return errors


def mismatches(root: Path, expected: str) -> list[str]:
    """Every surface in `root` that does not name `expected`.

    Stricter than `problems`: here a listed file that is missing is a defect,
    because this is the "is this release coherent" question, asked of a full
    checkout. Used by `make release-check` and by the release-metadata guard.
    """
    found, absent, unmatched = _scan(root)
    errors = [f"{path} is listed as a release surface but is missing" for path in absent]
    errors.extend(
        f"{path}: no version found, so the surface moved and nothing is watching it"
        for path in unmatched
    )
    errors.extend(
        f"{path} names {', '.join(sorted(versions - {expected}))}, not {expected}"
        for path, versions in found.items()
        if versions - {expected}
    )
    return errors
