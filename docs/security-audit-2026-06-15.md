# Security re-audit — post-v1.9 theater/site changes (2026-06-15)

Scope: everything merged since the previous theater audit
(`security-audit-2026-06-14-theater.md`, v1.9.1) — the live-clock ticker, the
`_wrap_banner` / `_fit` / `_verdict_label` helpers and the light-terminal SGR
change (v1.9.2–v1.9.5), the `jury.toml` theater defaults (#364), the website
demo animation (#365), the disabled-input tooltips (#353/#371), and the switch
to Cloudflare Web Analytics (#373). Threat model unchanged: agent output is
attacker-influenced (it derives from attacker-controlled diffs).

## Result: no Critical / High / Medium findings.

### Terminal rendering (theater.py) — still safe
- Every new agent-derived text path — the wrapped decision banner
  (`_wrap_banner`), the transcript line (`_fit`), and the short verdict label
  (`_verdict_label`) — still routes through `Screen.put`, which scrubs control /
  DEL / C1 / bidi / zero-width characters. Re-verified adversarially: a verdict
  carrying `\x1b[2J\x1b[H … \x1b[5m`, a bidi override (U+202E) and a zero-width
  space renders with **none** of those bytes in the frame (flat *and* pixel),
  while the trusted styling escapes remain. So the v1.9.1 ANSI-injection /
  Trojan-Source mitigation covers the new code unchanged.
- The light-terminal change only swaps trusted style literals (`97`/`37`/`2;37`
  → `1`/`2`/default); no attacker data reaches the `sgr` argument.
- The background ticker is a daemon thread that repaints under a shared `RLock`;
  it carries no attacker-controlled data path and only writes scrubbed frames /
  fixed cursor codes. Cosmetic only.

### Config (config.py, #364) — validated
- `theater` is bool-validated; `theater_style` is enum-validated to
  `{flat,pixel}`, lower-cased in `_from_dict`, and any other value falls back to
  `flat`. Rendering-only (excluded from `config_hash` / cache). No code path is
  selected by attacker-controlled config beyond flat-vs-pixel rendering.

### Website (#365 demo, #353/#371 tooltips, #373 analytics)
- Demo animation & comments: dynamic values are escaped (`esc()`) or written via
  `textContent`; phase labels and the agent keys used in `data-seat` / `d-<key>`
  are a fixed internal set (`claude/codex/agy/qwen`), never user input — no XSS.
- Tooltips: `setAttribute("title", …)` with fixed string literals only.
- Cloudflare Web Analytics: the beacon `TOKEN` is a hardcoded literal and a
  **public** client-side identifier (not a secret), JSON-encoded into a
  `data-cf-beacon` attribute; the script `src` is the fixed official Cloudflare
  URL. No user input, no injection. Cookieless — a privacy improvement over the
  removed GA4 (no cookies, no consent banner required).

### Benign observations (not findings)
- `data-seat="<key>"` / `class="dot d-<key>"` interpolate the agent key without
  escaping; safe today because the key set is fixed, but keep it fixed (don't
  feed user text there).
- The CF beacon (like the prior GA tag) loads third-party JS without SRI; CF
  doesn't offer SRI for the mutable beacon — unchanged trust posture.

Regression at the time of audit: `make test` 1211 passed, `make lint` clean,
`make coverage` gate passing (theater.py 100%), `make smoke` ok.
