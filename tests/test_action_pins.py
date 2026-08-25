"""Pinned GitHub Action SHAs must match the version they claim to be (issue #549).

Pinning by SHA is the security control; the ``# vX.Y.Z`` beside it is how a
reviewer decides whether that SHA is worth trusting. When the two disagree the
annotation is worse than absent, because it invites a check it cannot support.

They *will* disagree without something watching: Dependabot rewrites the SHA and
leaves the comment alone. #549 found nine pins misannotated that way, four of
them off by a whole major version.

This resolves each comment against the upstream tag, so it needs the network and
runs only when ``AI_JURY_CHECK_PINS=1`` — the offline suite stays hermetic. The
shape assertions below do run offline, because a malformed pin is a defect no
matter where you are.
"""

from __future__ import annotations

import json
import os
import re
import unittest
import urllib.error
import urllib.request
from pathlib import Path

WORKFLOWS = Path(__file__).parent.parent / ".github" / "workflows"
ONLINE = os.environ.get("AI_JURY_CHECK_PINS") == "1"

# owner/repo[/subpath]@<40-hex>  # vX.Y.Z
_PIN = re.compile(r"uses:\s*([\w.-]+/[\w.-]+(?:/[\w.-]+)*)@([0-9a-f]{40})[ \t]*#[ \t]*(\S+)")
_UNPINNED = re.compile(r"uses:\s*([\w.-]+/[\w.-]+(?:/[\w.-]+)*)@(?![0-9a-f]{40})(\S+)")


def _pins():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        for action, sha, claim in _PIN.findall(text):
            yield path.name, action, sha, claim


def _tags_for(repo, sha):
    """Every tag in ``repo`` pointing at ``sha``, or None if the lookup failed."""
    url = f"https://api.github.com/repos/{repo}/tags?per_page=100"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        # Being unable to look is not evidence the pin is wrong.
        return None
    return {tag["name"] for tag in payload if tag.get("commit", {}).get("sha") == sha}


class TestActionPinsAreAnnotated(unittest.TestCase):
    """Offline: the pins are shaped so the online check can say something."""

    def test_every_action_is_pinned_to_a_sha(self):
        loose = [
            (path.name, action, ref)
            for path in sorted(WORKFLOWS.glob("*.yml"))
            for action, ref in _UNPINNED.findall(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual([], loose, "actions must be pinned to a 40-hex commit SHA")

    def test_every_pin_carries_an_exact_version_comment(self):
        # A bare major ("# v4") is not enough: major tags move, so the comment
        # stops identifying a specific artifact the moment upstream retags. Four
        # of #549's wrong annotations were bare majors that had drifted two
        # majors behind the SHA beside them.
        vague = [
            (name, action, claim)
            for name, action, _sha, claim in _pins()
            if not re.fullmatch(r"v\d+\.\d+\.\d+", claim)
        ]
        self.assertEqual([], vague, "pin comments must name an exact vX.Y.Z release")

    def test_the_pin_pattern_matches_something(self):
        # Guards the assertions above from passing vacuously if the regex or the
        # comment convention changes: zero pins found would make them trivially
        # true while checking nothing.
        self.assertGreater(len(list(_pins())), 0, "no pinned actions found to check")

    def test_one_sha_is_never_given_two_different_versions(self):
        # Offline and free: the same SHA annotated differently in two files means
        # at least one is wrong, without asking GitHub which.
        claims = {}
        for name, action, sha, claim in _pins():
            claims.setdefault((action, sha), {}).setdefault(claim, []).append(name)
        conflicting = {k: v for k, v in claims.items() if len(v) > 1}
        self.assertEqual({}, conflicting, "one SHA is annotated with two versions")


@unittest.skipUnless(ONLINE, "set AI_JURY_CHECK_PINS=1 to resolve pins against GitHub")
class TestActionPinsMatchUpstream(unittest.TestCase):
    """Online: the version comment is the version the SHA actually is."""

    def test_each_pin_resolves_to_the_release_it_claims(self):
        wrong = []
        for name, action, sha, claim in _pins():
            repo = "/".join(action.split("/")[:2])
            tags = _tags_for(repo, sha)
            if tags is None:
                self.skipTest(f"could not reach GitHub for {repo}")
            if not tags:
                wrong.append(f"{name}: {action}@{sha[:8]} is not any released tag")
            elif claim not in tags:
                wrong.append(f"{name}: {action}@{sha[:8]} says {claim}, is {sorted(tags)}")
        self.assertEqual([], wrong)


if __name__ == "__main__":
    unittest.main()
