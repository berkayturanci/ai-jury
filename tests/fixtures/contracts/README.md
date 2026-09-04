# Recorded adapter-contract fixtures (issue #682)

The tiny diff and the canned CLI outputs that `tests/test_adapter_contracts.py`
drives every shipped adapter against, offline. Each `*.stdout` file is what a
real vendor CLI wrote to stdout for that shape, trimmed to the smallest sample
that still exercises the adapter's parse path — `agy.stdout.ndjson` is the
stream-json event log recorded from agy 1.1.22.

They exist so a vendor CLI flag or output-format change fails a pull request
instead of a production run. Nothing here needs auth, network, or spend, which
is why these run on every CI matrix entry while the live smokes stay behind
`JURY_LIVE=1`.

Re-record one only when a vendor really changed, and say so in `CHANGELOG.md` —
`tests/golden/adapter_contracts.json` is the invocation half of the same lock.
