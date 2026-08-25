"""CI and the pre-commit hook must format with the same ruff.

Two versions of a formatter are two formatters. Before #621 the hook was pinned
to `v0.4.4` while any modern ruff produced a different result — measured, not
assumed: `ruff format --diff` differs between 0.4.4 and 0.16.3, and they
disagree on five files about whether they are already formatted. A contributor
running the hooks and a contributor running their own ruff would have undone
each other indefinitely, with CI silent because it ran neither.

CI now gates on `ruff format --check`, which makes the version a contract
rather than a detail: if CI's ruff and the hook's ruff drift apart, the gate
starts refusing exactly the output the hook produces.

The version is deliberately **pinned** on both sides rather than floated. The
dev extra is `ruff>=0.6`, which is right for linting — new rules are worth
picking up — but a format gate on a floating formatter goes red the day ruff
changes its style, for reasons that have nothing to do with the change under
review. That is the failure mode that teaches people to re-run rather than
read.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PRE_COMMIT = REPO_ROOT / ".pre-commit-config.yaml"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def hook_version() -> str | None:
    """The `rev:` of the ruff-pre-commit block, without its leading `v`."""
    lines = PRE_COMMIT.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if "astral-sh/ruff-pre-commit" not in line:
            continue
        # `rev:` is the next key in the same block.
        for following in lines[index + 1 : index + 4]:
            match = re.search(r"rev:\s*v?([0-9]+(?:\.[0-9]+)*)", following)
            if match:
                return match.group(1)
    return None


def ci_versions() -> list[str]:
    """Every explicitly pinned `ruff==X` in the CI workflow."""
    return re.findall(r"ruff==([0-9]+(?:\.[0-9]+)*)", CI.read_text(encoding="utf-8"))


class TheRuffVersionIsOneVersion(unittest.TestCase):
    def test_the_hook_declares_a_version(self):
        """Vacuity: everything below compares against this."""
        self.assertIsNotNone(
            hook_version(),
            "no ruff-pre-commit `rev:` found — the comparison below would be empty",
        )

    def test_ci_pins_ruff_explicitly(self):
        """A floating formatter behind a format gate breaks on ruff's schedule."""
        self.assertTrue(
            ci_versions(),
            "CI installs no pinned ruff, so `ruff format --check` runs against "
            "whatever the dev extra resolves to that day",
        )

    def test_ci_and_the_hook_agree(self):
        hook = hook_version()
        for version in ci_versions():
            with self.subTest(ci=version):
                self.assertEqual(
                    version,
                    hook,
                    "CI formats with a different ruff than the pre-commit hook, "
                    "so the gate will refuse the output the hook produces",
                )

    def test_ci_runs_both_ruff_commands(self):
        """A pin is only useful if something uses it."""
        body = CI.read_text(encoding="utf-8")
        code = [
            line.strip()
            for line in body.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertTrue(
            any(line == "run: ruff check ." for line in code),
            "CI does not lint",
        )
        self.assertTrue(
            any(line == "run: ruff format --check ." for line in code),
            "CI does not check formatting, so the tree drifts back",
        )
