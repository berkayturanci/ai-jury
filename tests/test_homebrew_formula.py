"""Unit tests for Formula/ai-jury.rb and install.sh installer script."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import release_surfaces  # noqa: E402

from ai_jury import __version__  # noqa: E402

#: The formula's path in the shared surface table. *Which* strings in the formula
#: carry the version is decided in `scripts/release_surfaces.py`, alongside every
#: other surface, rather than restated here — three disjoint lists is what let the
#: website sit two releases behind (#665).
FORMULA = "Formula/ai-jury.rb"


class HomebrewFormulaTests(unittest.TestCase):
    def test_formula_file_exists_and_matches_version(self):
        formula_path = REPO_ROOT / FORMULA
        self.assertTrue(formula_path.exists(), "Formula/ai-jury.rb must exist")

        content = formula_path.read_text(encoding="utf-8")
        self.assertIn("class AiJury < Formula", content)
        self.assertIn("include Language::Python::Virtualenv", content)

        # Validate sha256 pattern (64 hex characters)
        sha_match = re.search(r'sha256\s+"([0-9a-f]{64})"', content)
        self.assertIsNotNone(sha_match, "Formula must contain a valid 64-char sha256 checksum")

        # Validate test block
        self.assertIn("assert_match", content)

        # Every version the table says this formula names — the sdist filename in
        # `url` and the `jury X.Y.Z` the formula's own `test do` block asserts.
        for surface in release_surfaces.RELEASE_SURFACES:
            if surface.path != FORMULA:
                continue
            with self.subTest(pattern=surface.pattern):
                found = surface.find(content)
                self.assertTrue(found, f"{surface.pattern} matched nothing; the formula moved")
                self.assertEqual(
                    set(found),
                    {__version__},
                    "the formula names a version the package is not",
                )

    def test_the_formula_is_a_registered_release_surface(self):
        """Vacuity: the loop above is a no-op if the table stops listing it.

        The formula is the surface a `brew install` reads, and it is bumped by a
        workflow rather than by hand, so nothing else would notice its removal.
        """
        covered = {surface.path for surface in release_surfaces.RELEASE_SURFACES}
        self.assertIn(FORMULA, covered)

    def test_install_script_syntax_and_structure(self):
        install_script = REPO_ROOT / "install.sh"
        self.assertTrue(install_script.exists(), "install.sh must exist")

        # Verify shell syntax via sh -n
        res = subprocess.run(["sh", "-n", str(install_script)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"install.sh has syntax error: {res.stderr}")

        content = install_script.read_text(encoding="utf-8")
        self.assertIn("berkayturanci/ai-jury/ai-jury", content)
        self.assertIn("pipx install ai-jury", content)


#: The tap `brew install berkayturanci/ai-jury/ai-jury` resolves to.
HOMEBREW_TAP = "berkayturanci/homebrew-ai-jury"
ONLINE = os.environ.get("AI_JURY_CHECK_EXTERNAL") == "1"


def missing_artifact_is_a_defect(version: str, pypi_has) -> bool:
    """Whether a 404 on the formula's url is a real defect.

    The formula must name the version in `pyproject.toml` — the offline test
    demands it — but that version's sdist does not exist until the tag is pushed.
    Between the release pull request and the tag, the formula is therefore ahead
    of PyPI *by design*, and its url legitimately 404s. Failing there makes the
    release pull request unmergeable, which is a gate that blocks the only
    sequence able to satisfy it.

    A 404 is still a real defect once the version is published: then the url
    names an artifact that should be there and is not, which is what `brew` will
    hit. #562 is that case, and it must keep failing.

    The sibling repository hit the identical bind and resolved it the same way
    (its #839): its formula url is pinned to the declared version, that version's
    tag does not exist until the release commit is made, and no value of the
    formula satisfies both tests at once inside that window. Only the window is
    exempt.

    Returns True when the 404 means the formula is wrong.
    """
    return pypi_has(version)


class TheMissingArtifactRuleDistinguishesTwoCases(unittest.TestCase):
    """Offline, because the rule itself is what can be wrong — not the network.

    Both cases below are 404s on the formula's url and they must be read
    oppositely, so the decision is pulled out where it can be exercised with a
    stub instead of by choosing a moment in the release cycle to run the suite.
    """

    def test_a_404_before_the_version_is_published_is_not_a_defect(self):
        """The state every release passes through, between the PR and the tag.

        The offline test requires the formula to name the version in
        `pyproject.toml`; that version's sdist does not exist until the tag is
        pushed. Failing here would make the release pull request unmergeable —
        a gate that blocks the only sequence able to satisfy it.
        """
        self.assertFalse(missing_artifact_is_a_defect("9.9.9", lambda _: False))

    def test_a_404_on_a_published_version_is_a_defect(self):
        """#562: the url returned 404 and the digest belonged to nothing.

        Once PyPI has the version, a dead url is what `brew install` hits.
        """
        self.assertTrue(missing_artifact_is_a_defect("1.0.0", lambda _: True))

    def test_the_question_is_asked_about_the_version_in_hand(self):
        """A rule that asked about some other version would pass both cases above."""
        asked = []
        missing_artifact_is_a_defect("4.5.6", lambda version: asked.append(version) or False)
        self.assertEqual(asked, ["4.5.6"])


class FormulaResolvesToARealArtifact(unittest.TestCase):
    """The formula must point at something that exists and hashes to what it says.

    `test_formula_file_exists_and_matches_version` compares the formula to the
    project, and passed while the url returned 404 and the digest belonged to
    nothing (#562). The project is not what `brew` downloads.

    PyPI paths are content-addressed, so a wrong path is a dead link rather than
    a wrong file - the download fails before any checksum is even checked.

    Online and opt-in via AI_JURY_CHECK_EXTERNAL=1, so the offline suite stays
    hermetic. Network failure skips: being unable to look is not evidence the
    formula is wrong.
    """

    def _formula(self):
        return (REPO_ROOT / "Formula" / "ai-jury.rb").read_text(encoding="utf-8")

    def _pypi_has(self, version: str) -> bool:
        """Whether PyPI has published `version` at all."""
        try:
            with urllib.request.urlopen(
                f"https://pypi.org/pypi/ai-jury/{version}/json", timeout=30
            ) as response:
                json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            raise
        return True

    def test_the_formula_names_a_url_and_a_digest(self):
        # Offline, and guards the online cases from passing vacuously.
        formula = self._formula()
        self.assertRegex(formula, r'url "https://\S+"')
        self.assertRegex(formula, r'sha256 "[0-9a-f]{64}"')

    def test_the_url_matches_what_pypi_publishes_for_this_version(self):
        """Compared against PyPI's metadata, not just fetched.

        A url that happens to resolve is not enough - it has to be *this*
        version's sdist, which is the thing the digest belongs to.
        """
        if not ONLINE:
            self.skipTest("set AI_JURY_CHECK_EXTERNAL=1 to query PyPI")
        formula = self._formula()
        url = re.search(r'url "(https://\S+)"', formula).group(1)
        digest = re.search(r'sha256 "([0-9a-f]{64})"', formula).group(1)
        try:
            with urllib.request.urlopen(
                f"https://pypi.org/pypi/ai-jury/{__version__}/json", timeout=30
            ) as response:
                release = json.load(response)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise self.skipTest(f"cannot reach PyPI: {exc}") from exc
        sdists = [f for f in release["urls"] if f["packagetype"] == "sdist"]
        self.assertTrue(sdists, f"PyPI has no sdist for {__version__}")
        self.assertEqual(sdists[0]["url"], url, "formula url is not this version's sdist")
        self.assertEqual(
            sdists[0]["digests"]["sha256"], digest, "formula sha256 is not that sdist's"
        )

    def test_the_url_downloads_and_hashes_to_the_declared_digest(self):
        """End to end, the way brew does it. Catches a stale digest on a live url."""
        if not ONLINE:
            self.skipTest("set AI_JURY_CHECK_EXTERNAL=1 to fetch the artifact")
        formula = self._formula()
        url = re.search(r'url "(https://\S+)"', formula).group(1)
        digest = re.search(r'sha256 "([0-9a-f]{64})"', formula).group(1)
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.assertFalse(
                    missing_artifact_is_a_defect(__version__, self._pypi_has),
                    f"the formula points at {url}, which does not exist",
                )
                raise self.skipTest(
                    f"{__version__} is not on PyPI yet, so the formula is ahead of it "
                    "— the state between the release PR and the tag"
                ) from exc
            raise self.skipTest(f"cannot fetch the artifact: {exc}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise self.skipTest(f"cannot fetch the artifact: {exc}") from exc
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            digest,
            "brew install would refuse: the digest is not this artifact's",
        )

    def test_the_documented_tap_exists(self):
        """`brew install <owner>/<tap>/<formula>` needs the tap repository.

        A formula in this repo is not installable by anyone; keel#773 was the same
        shape, five documented references to a repository nobody had created.
        """
        if not ONLINE:
            self.skipTest("set AI_JURY_CHECK_EXTERNAL=1 to check the tap")
        request = urllib.request.Request(
            f"https://api.github.com/repos/{HOMEBREW_TAP}", method="HEAD"
        )
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                self.assertLess(response.status, 400)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.fail(
                    f"the docs promise `brew install berkayturanci/ai-jury/ai-jury` "
                    f"but the tap {HOMEBREW_TAP} does not exist"
                )
            raise self.skipTest(f"cannot reach GitHub: {exc}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise self.skipTest(f"cannot reach GitHub: {exc}") from exc

    def test_the_tap_publishes_the_same_formula(self):
        """The tap is what `brew` reads; this repo's copy is not.

        keel fixed its own formula, every check went green, and users kept
        installing the broken one until the tap was synced by hand (keel#787).
        The tap pulls on a schedule, so a brief lag after a release is expected
        and is not what this catches — a permanent divergence is.
        """
        if not ONLINE:
            self.skipTest("set AI_JURY_CHECK_EXTERNAL=1 to compare against the tap")
        request = urllib.request.Request(
            f"https://api.github.com/repos/{HOMEBREW_TAP}/contents/Formula/ai-jury.rb",
            headers={"Accept": "application/vnd.github.raw", "User-Agent": "ai-jury-tests"},
        )
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                published = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.fail(f"{HOMEBREW_TAP} has no Formula/ai-jury.rb; brew install would fail")
            raise self.skipTest(f"cannot reach the tap: {exc}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise self.skipTest(f"cannot reach the tap: {exc}") from exc
        if published != self._formula():
            # A release just landed and the tap has not pulled yet. Distinguish
            # "behind" from "wrong": a tap serving an installable older formula is
            # a lag, and failing on it would make every release briefly red.
            published_version = re.search(r"ai_jury-([0-9.]+)\.tar\.gz", published)
            self.assertIsNotNone(published_version, "the tap's formula names no version")
            self.assertNotEqual(
                published_version.group(1),
                __version__,
                "the tap claims this version but its formula differs from this repo's",
            )


if __name__ == "__main__":
    unittest.main()
