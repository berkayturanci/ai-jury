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

Since #751 the pin CI installs lives in `uv.lock`, not in a hand-written
`ruff==` beside the install step. That is one version rather than two: the
`lint` job used to pin a ruff by hand while the `coverage` job installed
`ruff>=0.6` through the dev extra, so a single CI run could hold two formatters
and only one of them was the one the gate ran. Both jobs now sync from the lock.

So the comparison below reads the lock *and* any `ruff==` still written into the
workflow, and requires every version it finds to be the hook's. Both halves
matter: dropping the workflow half would let a reintroduced hand-pin drift
unwatched, and dropping the lock half would let the version CI actually installs
drift instead.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PRE_COMMIT = REPO_ROOT / ".pre-commit-config.yaml"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
LOCK = REPO_ROOT / "uv.lock"


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


def lock_versions() -> list[str]:
    """The `ruff` version `uv.lock` pins — the one CI's `uv sync` installs."""
    return re.findall(
        r'^name = "ruff"\nversion = "([0-9]+(?:\.[0-9]+)*)"',
        LOCK.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )


def ci_versions() -> list[str]:
    """Every explicitly pinned `ruff==X` still written into the CI workflow."""
    return re.findall(r"ruff==([0-9]+(?:\.[0-9]+)*)", CI.read_text(encoding="utf-8"))


class TheRuffVersionIsOneVersion(unittest.TestCase):
    def test_the_hook_declares_a_version(self):
        """Vacuity: everything below compares against this."""
        self.assertIsNotNone(
            hook_version(),
            "no ruff-pre-commit `rev:` found — the comparison below would be empty",
        )

    def test_the_lock_pins_ruff_exactly_once(self):
        """A floating formatter behind a format gate breaks on ruff's schedule."""
        self.assertEqual(
            len(lock_versions()),
            1,
            "uv.lock does not pin exactly one ruff, so `ruff format --check` runs "
            "against whatever the dev extra resolves to that day",
        )

    def test_ci_installs_ruff_from_the_lock(self):
        """The pin is only the installed one if the workflow syncs from it."""
        body = CI.read_text(encoding="utf-8")
        self.assertIn(
            "uv sync --locked",
            body,
            "CI does not install from uv.lock, so the version pinned there is not "
            "the version the format gate runs",
        )

    def test_every_pin_ci_can_reach_is_the_hook_s(self):
        hook = hook_version()
        found = lock_versions() + ci_versions()
        self.assertTrue(found, "nothing pins ruff; the comparison would be empty")
        for version in found:
            with self.subTest(pinned=version):
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
