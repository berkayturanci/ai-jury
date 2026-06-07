# Codex Security Scan - 2026-06-07

This note records the repository-wide Codex Security scan run on commit
`a358fdc`.

## Result

The scan found one reportable issue:

- [#287: Avoid passing review prompts through process arguments](https://github.com/berkayturanci/ai-jury/issues/287)

No other scanned high-impact surfaces produced a reportable finding. The scan
closed the GitHub CLI integration, prompt-injection controls, secret redaction,
local HTTP adapter, output/cache paths, report rendering, static website DOM,
parser/deserialization/archive, and auth/session/tenant rows as no issue found
or not applicable.

## Finding Summary

The Claude and Antigravity/Gemini adapters currently pass the full review prompt
through process arguments:

- `ClaudeAdapter.build_argv()` uses `claude -p <prompt>`.
- `AgyAdapter.build_argv()` uses `agy --print <prompt>`.
- `CodexAdapter` is the safer in-tree pattern because it sends the prompt on
  stdin instead of argv.

The prompt contains the redacted diff and, depending on context mode, PR or
issue context. Redaction masks common secret formats, but it does not remove
private source code, proprietary PR context, or credentials in formats the
redactor does not recognize.

On hosts where another local user or process can inspect process arguments while
the reviewer is running, prompt content can be exposed outside the intended
agent channel. This is a local confidentiality issue, not command injection.

Severity: medium / P2.

## Recommended Remediation

- Deliver Claude and Antigravity/Gemini prompts through stdin when supported.
- If stdin is unavailable, use an owner-only temporary file or another non-argv
  private channel, then delete it promptly.
- Add adapter regression tests asserting sensitive prompt text does not appear
  in `build_argv()` for non-mock adapters.
- Keep secret redaction enabled by default, but treat redaction as defense in
  depth rather than the primary control for process-argument exposure.

## Local Scan Artifacts

The scan bundle was written locally under:

```text
/tmp/codex-security-scans/ai-jury/a358fdc_20260607T085100Z/
```

Key artifacts:

- `report.html` - primary readable report.
- `report.md` - Markdown source for the report.
- `artifacts/03_coverage/repository_coverage_ledger.md` - reviewed surface
  closure ledger.
- `artifacts/05_findings/AJ-SEC-001/validation_report.md` - validation detail.
- `artifacts/05_findings/AJ-SEC-001/attack_path_analysis_report.md` -
  attack-path and severity calibration.
