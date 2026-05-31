# `council.toml` configuration reference

The council reads `./council.toml` by default (override with `--config PATH`).
If the file is absent, built-in defaults are used.

## Validation

Check a config without running a review:

```
council --config-validate --config council.toml
```

Exit codes: `0` valid (warnings printed if any), `2` invalid. During a normal
run the config is validated too; pass `--strict-config` to turn warnings into
hard errors (exit `2`).

- **Hard errors** (always fail): `rounds < 1`, non-positive `timeout` (council
  or per-agent), duplicate agent names, missing/empty agent `name` or
  `command`, no `[[agent]]` entries at all, malformed tables.
- **Warnings** (fail only under `--strict-config`): unknown vendor, `chair`
  not matching an enabled agent, unknown top-level/section/agent keys.

## `[council]`

| Field      | Type   | Default    | Allowed / notes                                   |
| ---------- | ------ | ---------- | ------------------------------------------------- |
| `rounds`   | int    | `2`        | Integer `>= 1` (1 = review only, 2 = + debate).   |
| `chair`    | string | `"claude"` | Name of an enabled agent (else fallback warning). |
| `timeout`  | int    | `600`      | Positive seconds; per-agent wall-clock bound.     |
| `parallel` | bool   | `true`     | Run agents concurrently.                          |
| `verify`   | bool   | `true`     | Run the verification round.                       |

### `[council.ci]`

| Field               | Type      | Default                  | Notes                                  |
| ------------------- | --------- | ------------------------ | -------------------------------------- |
| `fail_on`           | list[str] | `["critical", "major"]`  | Severities that fail CI mode.          |
| `ignore_unverified` | bool      | `true`                   | Skip findings not confirmed by verify. |

### `[council.context]`

| Field            | Type   | Default       | Notes                                          |
| ---------------- | ------ | ------------- | ---------------------------------------------- |
| `mode`           | string | `"diff-only"` | `"diff-only"` or `"expanded"`.                 |
| `redact_secrets` | bool   | `true`        | Scrub recognized secrets before sending.       |

## `[[agent]]`

At least one entry is required.

| Field        | Type      | Default | Allowed / notes                                           |
| ------------ | --------- | ------- | -------------------------------------------------------- |
| `name`       | string    | —       | Required, non-empty, unique.                             |
| `vendor`     | string    | —       | `anthropic`, `openai`, `google`; unknown → fallback warn. |
| `command`    | string    | —       | Required, non-empty CLI command.                         |
| `model`      | string    | unset   | Optional model identifier.                               |
| `timeout`    | int       | `600`   | Positive seconds (inherits `council.timeout`).           |
| `enabled`    | bool      | `true`  | Disabled agents are skipped.                             |
| `extra_args` | list[str] | `[]`    | Extra CLI args passed to the agent command.              |
