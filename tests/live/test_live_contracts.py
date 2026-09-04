"""The live half of the adapter contract (issue #682). Opt-in: ``JURY_LIVE=1``.

`tests/test_adapter_contracts.py` proves, offline and on every CI matrix entry,
that each adapter still *builds* the locked invocation and still parses a
recorded response. What it cannot prove is the other half: that the locked
invocation is still one the installed CLI accepts. Only a real binary can answer
that, and answering it costs a model call — so it lives here, behind the same
opt-in as the rest of `tests/live/`, and is a release-checklist step rather than
a pull-request gate (see `docs/release-checklist.md`).

The two halves fail differently on purpose:

* the CLI grew a flag arity, renamed a flag, or changed its output format
  (#635) — offline stays green, THIS goes red;
* somebody edited an adapter — offline goes red immediately, at no cost.

The assertion here is deliberately the same one #635 defeated: not "the process
exited 0", but "a review came back". `no_review_reason` is what separates them,
and it is the runtime rule, not a test-local copy of it.
"""

from __future__ import annotations

import dataclasses
import os
import unittest
from pathlib import Path

from ai_jury.adapters import make_adapter, no_review_reason
from ai_jury.config import DEFAULT_CONFIG, _from_dict

#: Short per-agent timeout so a hung CLI fails fast rather than blocking.
LIVE_TIMEOUT_S = 180

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "contracts"

#: The same tiny diff the offline probes use, so the two halves are asking the
#: same question of the same input.
TINY_DIFF = (FIXTURES / "tiny.diff").read_text(encoding="utf-8")

CONTRACT_PROMPT = (
    "Review this diff. Reply with one or two sentences, then a fenced ```json\n"
    "block containing a JSON array of findings (an empty array is fine).\n\n"
    f"```diff\n{TINY_DIFF}```\n"
)


@unittest.skipUnless(
    os.environ.get("JURY_LIVE") == "1",
    "live contract probes disabled; set JURY_LIVE=1",
)
class LiveAdapterContractTest(unittest.TestCase):
    """Drive the locked invocation through each installed CLI, for real."""

    def _probe(self, raw_spec: dict) -> None:
        spec = _from_dict({"agent": [raw_spec]}).agents[0]
        spec = dataclasses.replace(spec, timeout=LIVE_TIMEOUT_S)
        adapter = make_adapter(spec, mock=False)

        if not adapter.available():
            self.skipTest(f"CLI not installed: {spec.command}")

        result = adapter.run(CONTRACT_PROMPT, phase="review")

        if result.error_code in ("auth_required", "missing_api_key", "rate_limited"):
            # Installed but not usable by this machine right now. Same situation
            # as not installed, and keyed off the typed code so a REAL contract
            # break (a flag arity, an output format) still fails the test.
            self.skipTest(f"{spec.command}: {result.error_code}")

        self.assertTrue(result.ok, msg=f"{spec.name}: {result.error!r}")
        self.assertIsNone(result.error_code, msg=f"{spec.name}: {result.error!r}")
        # The #635 assertion. `ok` alone was true on the run that shipped the
        # collapsed panel; this is the question that was not being asked.
        self.assertIsNone(
            no_review_reason(result.output),
            msg=f"{spec.name}: the CLI answered, but not with a review: {result.output[:200]!r}",
        )


def _make_probe(raw_spec: dict):
    def _test(self: LiveAdapterContractTest) -> None:
        self._probe(raw_spec)

    return _test


# One probe per agent in the shipped default config, so the live half cannot
# drift out of step with the adapter set the offline lock covers.
for _raw in DEFAULT_CONFIG["agent"]:
    setattr(
        LiveAdapterContractTest,
        f"test_{_raw['name']}_contract",
        _make_probe(dict(_raw)),
    )


if __name__ == "__main__":
    unittest.main()
