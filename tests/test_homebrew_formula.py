"""The Homebrew formula template, the installer script, and the live tap.

There is no `Formula/ai-jury.rb` in this repository any more, and the absence is
the point (#645, #666). A formula names an sdist url and a sha256; PyPI's paths
are content-addressed and the digest belongs to an artifact that does not exist
until the tag is pushed, so a committed copy of that pair is either stale or
unverifiable on every single release. Keeping it honest cost a second write to
`main` after every tag, which failed four ways in three days.

What is committed is the *template*, which names neither. `publish.yml` renders
it after the upload from what PyPI reports, and publishes the result to the
GitHub Release and to the tap.

So the online checks below look at the **tap** — the copy `brew` actually reads —
and never compare it to this repository's version. A tap one release behind still
installs, and failing a pull request over that would recreate the gate that made
every release pull request unmergeable. A tap whose digest is not its artifact's
does not install, and that fails here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "packaging" / "homebrew" / "ai-jury.rb.template"

#: The tap `brew install berkayturanci/ai-jury/ai-jury` resolves to.
HOMEBREW_TAP = "berkayturanci/homebrew-ai-jury"
ONLINE = os.environ.get("AI_JURY_CHECK_EXTERNAL") == "1"

#: Written by the operator once the tap no longer pulls `main:Formula/ai-jury.rb`.
TAP_REPOINTED = REPO_ROOT / "packaging" / "homebrew" / "TAP_REPOINTED"
TAP_PATCH = REPO_ROOT / "packaging" / "homebrew" / "tap-sync-formula.patch"

#: The marker's required line: the tap commit that applied the patch.
MARKER_RE = re.compile(r"^tap-sync-formula: ([0-9a-f]{40})$", re.MULTILINE)

#: The path the tap used to pull, and must not pull any more.
RETIRED_TAP_SOURCE = "contents/Formula/ai-jury.rb"
#: What it must pull instead.
RELEASE_ASSET = "releases/latest/download/ai-jury.rb"

URL_RE = re.compile(r'url "(https://\S+)"')
SHA_RE = re.compile(r'sha256 "([0-9a-f]{64})"')
TEST_VERSION_RE = re.compile(r'assert_match "jury ([0-9][^"]*)"')


def render(template: str, url: str, sha256: str, version: str) -> str:
    """The substitution `publish.yml` performs, in Python.

    Kept here so the template's contract is exercised offline. A placeholder
    renamed in one place and not the other renders a formula that cannot parse,
    and `sed` reports success either way.
    """
    return template.replace("@URL@", url).replace("@SHA256@", sha256).replace("@VERSION@", version)


def url_is_this_projects_sdist(url: str, version: str) -> bool:
    """#645's load-bearing guard, in one line.

    The digest cannot be checked without downloading the artifact, but *this*
    can be checked from the string alone: the url must be a PyPI sdist for this
    project, naming the version the formula declares. #562 shipped a url that
    named no version PyPI had ever served, and the offline suite passed.
    """
    return url.startswith("https://files.pythonhosted.org/packages/") and url.endswith(
        f"/ai_jury-{version}.tar.gz"
    )


def marker_records_a_tap_commit(text: str) -> str | None:
    """The commit in `packaging/homebrew/TAP_REPOINTED`, or None.

    A marker whose only requirement is "exists and is non-empty" is satisfied by
    `echo x >`, which records that someone typed a command rather than that the
    tap was changed. Requiring `tap-sync-formula: <40-hex>` does not make the
    claim true — nothing offline can — but it makes it *checkable*: the sha names
    a commit in the tap that a reviewer can open, and a wrong one is a specific,
    falsifiable statement rather than a shrug.
    """
    found = MARKER_RE.search(text)
    return found.group(1) if found else None


class TheTemplateNamesNoDigest(unittest.TestCase):
    """What is committed must be renderable, and must carry nothing releasable."""

    def setUp(self):
        self.template = TEMPLATE.read_text(encoding="utf-8")

    def test_the_template_exists_and_is_a_formula(self):
        self.assertIn("class AiJury < Formula", self.template)
        self.assertIn("include Language::Python::Virtualenv", self.template)
        self.assertIn("virtualenv_install_with_resources", self.template)

    def test_the_template_carries_placeholders_not_values(self):
        for placeholder in ("@URL@", "@SHA256@", "@VERSION@"):
            self.assertIn(placeholder, self.template)
        self.assertIsNone(
            SHA_RE.search(self.template),
            "the template names a real digest; that is the value it exists to avoid",
        )

    def test_no_formula_is_committed_to_this_repository(self):
        """The assertion that keeps the second write to `main` from coming back.

        Restoring the file restores the requirement that something make it true
        after the tag, which is the whole of #633, #638, #641, #643 and #644.
        """
        self.assertFalse(
            (REPO_ROOT / "Formula" / "ai-jury.rb").exists(),
            "Formula/ai-jury.rb is back; see docs/homebrew-release-chain.md",
        )

    def test_rendering_produces_a_formula_brew_could_read(self):
        url = "https://files.pythonhosted.org/packages/aa/bb/cc/ai_jury-9.9.9.tar.gz"
        digest = "0" * 64
        rendered = render(self.template, url, digest, "9.9.9")
        self.assertEqual(URL_RE.search(rendered).group(1), url)
        self.assertEqual(SHA_RE.search(rendered).group(1), digest)
        self.assertEqual(TEST_VERSION_RE.search(rendered).group(1), "9.9.9")

    def test_rendering_leaves_no_placeholder_behind(self):
        rendered = render(self.template, "https://example.invalid/x.tar.gz", "a" * 64, "1.2.3")
        self.assertEqual(re.findall(r"@[A-Z0-9_]+@", rendered), [])

    def test_the_python_dependency_survives_rendering(self):
        """`python@3.13` contains an `@` and must not be mistaken for a placeholder."""
        rendered = render(self.template, "https://example.invalid/x.tar.gz", "a" * 64, "1.2.3")
        self.assertIn('depends_on "python@3.13"', rendered)


class TheUrlRuleReadsTheVersionInHand(unittest.TestCase):
    """Offline, because the rule itself is what can be wrong — not the network."""

    def test_a_pypi_sdist_for_the_declared_version_passes(self):
        self.assertTrue(
            url_is_this_projects_sdist(
                "https://files.pythonhosted.org/packages/1a/64/ec/ai_jury-1.15.1.tar.gz", "1.15.1"
            )
        )

    def test_an_sdist_for_a_different_version_fails(self):
        self.assertFalse(
            url_is_this_projects_sdist(
                "https://files.pythonhosted.org/packages/1a/64/ec/ai_jury-1.15.0.tar.gz", "1.15.1"
            )
        )

    def test_a_url_outside_pypi_fails(self):
        """#562's shape: a url that resolves somewhere, to something else."""
        self.assertFalse(
            url_is_this_projects_sdist("https://example.invalid/ai_jury-1.15.1.tar.gz", "1.15.1")
        )

    def test_another_projects_sdist_fails(self):
        self.assertFalse(
            url_is_this_projects_sdist(
                "https://files.pythonhosted.org/packages/1a/64/ec/keel-1.15.1.tar.gz", "1.15.1"
            )
        )


class TheTapIsRepointedBeforeTheFormulaMayGo(unittest.TestCase):
    """Deleting the formula breaks a consumer in another repository.

    `berkayturanci/homebrew-ai-jury`'s `sync-formula.yml` curls
    `repos/berkayturanci/ai-jury/contents/Formula/ai-jury.rb` every thirty
    minutes. With the file gone that is a 404 forever — `curl -fsSL` exits 22 and
    the tap's sync job fails on a schedule, which is the exact shape of #630 and
    the reason `docs/homebrew-release-chain.md` exists.

    The tap cannot be changed from this repository, so the ordering is recorded
    here instead. Be precise about what that buys, because two of these checks
    run in different places:

    * **Offline, and blocking on `main`** — `packaging/homebrew/TAP_REPOINTED`
      must exist and must name the tap commit that applied the patch, as
      `tap-sync-formula: <40-hex>`. This is a *claim in a reviewable form*, not
      proof: nothing offline can read another repository. A reviewer can open the
      sha; a wrong one is a falsifiable statement rather than a shrug.
    * **Online, and advisory today** — `AI_JURY_CHECK_EXTERNAL=1` reads the tap's
      live workflow and fails if it still names the retired path. That is the
      real check, but it runs in `ci.yml`'s `Action pins match upstream` job,
      which is **not** currently a required status check on `main`. Making it one
      would turn the claim into an enforced fact; that is the operator's call and
      is noted on the pull request rather than done here.

    The gate is satisfiable at any moment — unlike the digest gate that used to
    block every release pull request, where no value of the file could pass. It
    and the marker exist for one merge and can be deleted afterwards.
    """

    def test_the_repointing_patch_ships_with_the_deletion(self):
        """A note saying "change the tap" is a message nobody is required to act on."""
        self.assertTrue(TAP_PATCH.exists(), f"{TAP_PATCH.name} is missing")
        patch = TAP_PATCH.read_text(encoding="utf-8")
        self.assertIn("sync-formula.yml", patch)
        self.assertIn(RELEASE_ASSET, patch, "the patch does not point the tap at the asset")
        self.assertIn(f'-{" " * 12}"https://api.github.com/repos/', patch)

    def test_the_patched_sync_tolerates_an_asset_that_is_not_published_yet(self):
        """Between the repoint and the next tag there is no asset to fetch.

        The gate orders repoint before merge, and the merge is what makes the
        *next* release attach `ai-jury.rb`. So for one release cycle the asset
        404s — and a patch that fails on that would trade the old red cron for a
        new one, every thirty minutes, which is #630 again wearing a new url.
        """
        patch = TAP_PATCH.read_text(encoding="utf-8")
        self.assertIn("found=false", patch)
        self.assertIn("::notice::formula asset not published yet", patch)
        self.assertIn("if: steps.fetch.outputs.found == 'true'", patch)

    def test_the_formula_may_only_be_absent_once_the_tap_is_repointed(self):
        if (REPO_ROOT / "Formula" / "ai-jury.rb").exists():
            self.skipTest("the formula is still committed, so the tap's pull still resolves")
        self.assertTrue(
            TAP_REPOINTED.exists(),
            "Formula/ai-jury.rb is gone but the tap still pulls it every 30 minutes.\n"
            f"Apply packaging/homebrew/tap-sync-formula.patch to {HOMEBREW_TAP},\n"
            "then record the commit it produced:\n"
            "    echo 'tap-sync-formula: <40-char tap commit sha>' "
            "> packaging/homebrew/TAP_REPOINTED",
        )

    def test_the_marker_names_the_commit_that_did_it(self):
        """`echo x >` satisfies "non-empty"; it does not satisfy a commit sha."""
        if not TAP_REPOINTED.exists():
            self.skipTest("the marker is not present yet")
        text = TAP_REPOINTED.read_text(encoding="utf-8")
        self.assertIsNotNone(
            marker_records_a_tap_commit(text),
            "TAP_REPOINTED must contain a line `tap-sync-formula: <40-char sha>` "
            f"naming the {HOMEBREW_TAP} commit that applied the patch; got: {text!r}",
        )


class TheMarkerRuleRejectsAGesture(unittest.TestCase):
    """Offline, because the rule is what can be wrong — and it must be able to fail.

    Exercised with strings rather than by creating the marker, so these cases run
    whether or not the repointing has happened.
    """

    def test_a_commit_sha_is_accepted(self):
        sha = "a" * 40
        self.assertEqual(
            marker_records_a_tap_commit(f"tap-sync-formula: {sha}\n"),
            sha,
        )

    def test_the_line_may_sit_among_prose(self):
        sha = "0123456789abcdef" * 2 + "01234567"
        text = f"repointed 2026-09-03 by the operator\ntap-sync-formula: {sha}\nsee PR #673\n"
        self.assertEqual(marker_records_a_tap_commit(text), sha)

    def test_a_gesture_is_rejected(self):
        for gesture in ("x\n", "done\n", "repointed the tap\n", ""):
            with self.subTest(marker=gesture):
                self.assertIsNone(marker_records_a_tap_commit(gesture))

    def test_a_short_or_malformed_sha_is_rejected(self):
        for bad in ("tap-sync-formula: abc123\n", "tap-sync-formula: " + "z" * 40 + "\n"):
            with self.subTest(marker=bad):
                self.assertIsNone(marker_records_a_tap_commit(bad))

    def test_the_key_must_be_the_documented_one(self):
        self.assertIsNone(marker_records_a_tap_commit("tap: " + "a" * 40 + "\n"))


class InstallScriptTests(unittest.TestCase):
    def test_install_script_syntax_and_structure(self):
        install_script = REPO_ROOT / "install.sh"
        self.assertTrue(install_script.exists(), "install.sh must exist")

        # Verify shell syntax via sh -n
        res = subprocess.run(["sh", "-n", str(install_script)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"install.sh has syntax error: {res.stderr}")

        content = install_script.read_text(encoding="utf-8")
        self.assertIn("berkayturanci/ai-jury/ai-jury", content)
        self.assertIn("pipx install ai-jury", content)


class TheTapServesSomethingInstallable(unittest.TestCase):
    """The tap is the copy `brew` reads; nothing in this repository is.

    keel fixed its own formula, every check went green, and users kept installing
    the broken one until the tap was synced by hand (keel#787).

    Deliberately never compared against this repository's version. The tap is
    updated after the tag, so between a release and the tap's next sync it is
    legitimately behind — and a check that failed on that would block the only
    sequence able to satisfy it, which is exactly the gate that made every
    release pull request unmergeable before #645.

    Online and opt-in via AI_JURY_CHECK_EXTERNAL=1, so the default suite stays
    hermetic. Network failure skips: being unable to look is not evidence the tap
    is wrong. A 404 on the formula's own url is *not* a skip — that is what
    `brew install` hits.
    """

    def _get(self, url: str, *, headers: dict[str, str] | None = None, timeout: int = 30) -> bytes:
        request = urllib.request.Request(url, headers=headers or {})
        token = os.environ.get("GITHUB_TOKEN")
        if token and url.startswith("https://api.github.com/"):
            request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def _tap_formula(self) -> str:
        try:
            payload = self._get(
                f"https://api.github.com/repos/{HOMEBREW_TAP}/contents/Formula/ai-jury.rb",
                headers={
                    "Accept": "application/vnd.github.raw",
                    "User-Agent": "ai-jury-tests",
                },
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.fail(
                    f"the docs promise `brew install berkayturanci/ai-jury/ai-jury` but "
                    f"{HOMEBREW_TAP} has no Formula/ai-jury.rb"
                )
            raise self.skipTest(f"cannot reach the tap: {exc}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise self.skipTest(f"cannot reach the tap: {exc}") from exc
        return payload.decode("utf-8")

    def setUp(self):
        if not ONLINE:
            self.skipTest("set AI_JURY_CHECK_EXTERNAL=1 to check the live tap")

    def test_the_documented_tap_exists(self):
        """keel#773 was five documented references to a repository nobody created."""
        try:
            self._get(f"https://api.github.com/repos/{HOMEBREW_TAP}")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.fail(
                    f"the docs promise `brew install berkayturanci/ai-jury/ai-jury` "
                    f"but the tap {HOMEBREW_TAP} does not exist"
                )
            raise self.skipTest(f"cannot reach GitHub: {exc}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise self.skipTest(f"cannot reach GitHub: {exc}") from exc

    def test_the_taps_url_is_this_projects_sdist_for_the_version_it_declares(self):
        formula = self._tap_formula()
        url = URL_RE.search(formula)
        declared = TEST_VERSION_RE.search(formula)
        self.assertIsNotNone(url, "the tap's formula names no url")
        self.assertIsNotNone(declared, "the tap's formula's test block names no version")
        self.assertTrue(
            url_is_this_projects_sdist(url.group(1), declared.group(1)),
            f"the tap's url {url.group(1)} is not ai-jury {declared.group(1)}'s sdist",
        )

    def test_the_taps_url_is_what_pypi_publishes_for_that_version(self):
        """A url that happens to resolve is not enough: it has to be that sdist."""
        formula = self._tap_formula()
        url = URL_RE.search(formula).group(1)
        digest = SHA_RE.search(formula).group(1)
        version = TEST_VERSION_RE.search(formula).group(1)
        try:
            release = json.loads(
                self._get(f"https://pypi.org/pypi/ai-jury/{version}/json").decode("utf-8")
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.fail(f"the tap serves {version}, which PyPI has never published")
            raise self.skipTest(f"cannot reach PyPI: {exc}") from exc
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise self.skipTest(f"cannot reach PyPI: {exc}") from exc
        sdists = [f for f in release["urls"] if f["packagetype"] == "sdist"]
        self.assertTrue(sdists, f"PyPI has no sdist for {version}")
        self.assertEqual(sdists[0]["url"], url, "the tap's url is not that version's sdist")
        self.assertEqual(
            sdists[0]["digests"]["sha256"], digest, "the tap's sha256 is not that sdist's"
        )

    def test_the_tap_does_not_pull_a_path_this_repository_no_longer_has(self):
        """The marker is a promise; this is the check.

        A workflow file that is simply gone is fine — a tap fed only by the push
        pulls nothing. What is not fine is one that still curls
        `contents/Formula/ai-jury.rb`, which now 404s on every run.
        """
        try:
            workflow = self._get(
                f"https://api.github.com/repos/{HOMEBREW_TAP}"
                "/contents/.github/workflows/sync-formula.yml",
                headers={
                    "Accept": "application/vnd.github.raw",
                    "User-Agent": "ai-jury-tests",
                },
            ).decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.skipTest("the tap has no sync workflow, so it pulls nothing")
            raise self.skipTest(f"cannot reach the tap: {exc}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise self.skipTest(f"cannot reach the tap: {exc}") from exc
        self.assertNotIn(
            RETIRED_TAP_SOURCE,
            workflow,
            "the tap still pulls Formula/ai-jury.rb from this repository, which no "
            "longer has it; apply packaging/homebrew/tap-sync-formula.patch",
        )
        self.assertIn(
            RELEASE_ASSET,
            workflow,
            "the tap's sync names no source this repository still publishes",
        )

    def test_the_taps_url_downloads_and_hashes_to_its_declared_digest(self):
        """End to end, the way brew does it. This is the check users feel."""
        formula = self._tap_formula()
        url = URL_RE.search(formula).group(1)
        digest = SHA_RE.search(formula).group(1)
        try:
            payload = self._get(url, timeout=60)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.fail(f"the tap points at {url}, which does not exist")
            raise self.skipTest(f"cannot fetch the artifact: {exc}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise self.skipTest(f"cannot fetch the artifact: {exc}") from exc
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            digest,
            "brew install would refuse: the digest is not this artifact's",
        )


if __name__ == "__main__":
    unittest.main()
