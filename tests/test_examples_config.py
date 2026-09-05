"""Every jury config we ship as an example still validates (issue #730).

`examples/jury.toml` carried a `[jury] panel = [...]` key that no version of
`config.py` ever read: the panel has always been the set of `[[agent]]` entries
that are not `enabled = false`. Nothing failed on it, so the file we ship as the
worked example was the one file `--strict-config` rejected — a user's first run
was where it surfaced. This module is where it surfaces now.

The hosted-API seats point at real remote endpoints, and `validate_config`
reports every non-loopback endpoint: a hard error by default, a warning under
`JURY_ALLOW_REMOTE_ENDPOINT` (see `config._endpoint_issues`). That advisory is
inherent to a file whose purpose is to demonstrate hosted providers, so the
tests opt in and then assert those advisories are the *only* thing the
validator has to say — any other warning, a stale key above all, fails here.
"""

from __future__ import annotations

import os
import sys
import tomllib
import unittest
import unittest.mock
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury.config import ConfigError, validate_config  # noqa: E402

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"

#: The substring shared by both spellings of the non-loopback endpoint message.
ENDPOINT_ADVISORY = "endpoint host"


@contextmanager
def _example_env() -> Iterator[None]:
    """The environment a reader of the examples is assumed to have.

    ``JURY_ALLOW_REMOTE_ENDPOINT`` is the documented opt-in for the hosted-API
    seats. ``JURY_REQUIRE_ABSOLUTE_COMMAND`` is dropped rather than inherited:
    a developer who exports it would otherwise see this test fail on every
    example's bare ``command = "claude"``, which the examples spell that way on
    purpose.
    """
    env = dict(os.environ)
    env["JURY_ALLOW_REMOTE_ENDPOINT"] = "1"
    env.pop("JURY_REQUIRE_ABSOLUTE_COMMAND", None)
    with unittest.mock.patch.dict(os.environ, env, clear=True):
        yield


def _shipped_jury_configs() -> list[tuple[Path, dict]]:
    """Every `examples/*.toml` that is a jury config, parsed.

    ``examples/policy.toml`` is a review *policy* — a different schema, read by
    `ai_jury.policy` — so the presence of a `[jury]` table or an `[[agent]]`
    array, not the file name, is what makes a file this module's business.
    """
    found = []
    for path in sorted(EXAMPLES_DIR.glob("*.toml")):
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        if {"jury", "agent"} & set(data):
            found.append((path, data))
    return found


class ShippedExamplesValidate(unittest.TestCase):
    """The shipped examples are held to the config schema they demonstrate."""

    def test_examples_dir_holds_at_least_one_jury_config(self):
        """A filter that silently matched nothing would pass every test below."""
        self.assertTrue(_shipped_jury_configs(), f"no jury config in {EXAMPLES_DIR}")

    def test_no_warning_beyond_the_remote_endpoint_advisories(self):
        """A stale or misspelled key in a shipped example fails here (#730)."""
        for path, data in _shipped_jury_configs():
            with self.subTest(example=path.name), _example_env():
                # Hard errors raise out of this call; warnings come back.
                warnings = validate_config(data)
                unexpected = [w for w in warnings if ENDPOINT_ADVISORY not in w]
                self.assertEqual(
                    unexpected,
                    [],
                    f"{path.name} does not validate cleanly: {unexpected}",
                )

    def test_strict_config_reports_nothing_but_those_advisories(self):
        """`--strict-config` on an example fails only for its remote seats."""
        for path, data in _shipped_jury_configs():
            with self.subTest(example=path.name), _example_env():
                warnings = validate_config(data)
                try:
                    validate_config(data, strict=True)
                    strict_error = ""
                except ConfigError as exc:
                    strict_error = str(exc)
                self.assertEqual(
                    bool(strict_error),
                    bool(warnings),
                    f"{path.name}: strict mode and warnings disagree",
                )
                for warning in warnings:
                    self.assertIn(warning, strict_error)


if __name__ == "__main__":
    unittest.main()
