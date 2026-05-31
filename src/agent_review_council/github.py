"""Thin GitHub helpers built on the `gh` CLI.

Used to pull a PR diff in and to post the council verdict back as a comment.
Kept dependency-free; if `gh` is unavailable these raise a clear error.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess


def _gh(*args: str) -> str:
    if shutil.which("gh") is None:
        raise RuntimeError("the GitHub CLI `gh` is not installed or not on PATH")
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def pr_diff(pr: str, repo: str | None = None) -> str:
    args = ["pr", "diff", str(pr)]
    if repo:
        args += ["--repo", repo]
    return _gh(*args)


def pr_context(pr: str, repo: str | None = None) -> str:
    """Return 'title\\n\\nbody' for a PR, best-effort."""
    args = ["pr", "view", str(pr), "--json", "title,body",
            "--jq", '.title + "\\n\\n" + (.body // "")']
    if repo:
        args += ["--repo", repo]
    try:
        return _gh(*args).strip()
    except RuntimeError:
        return ""


def post_pr_comment(pr: str, body: str, repo: str | None = None) -> None:
    args = ["pr", "comment", str(pr), "--body", body]
    if repo:
        args += ["--repo", repo]
    _gh(*args)


# Marker prefix identifying comments authored by the council (enables dedup).
INLINE_MARKER = "<!-- arc-inline -->"
# Per-finding signature marker embedded in the body. Lets dedup distinguish two
# different findings that happen to land on the same (path, line).
_SIG_RE = re.compile(r"<!-- arc-inline-sig:([0-9a-f]{12}) -->")


def _finding_signature(finding) -> str:
    """Stable 12-hex signature of a finding (severity + normalized claim)."""
    sev = (getattr(finding, "severity", "") or "").strip().lower()
    claim = " ".join((getattr(finding, "claim", "") or "").split()).lower()
    return hashlib.sha256(f"{sev}|{claim}".encode("utf-8")).hexdigest()[:12]


def _body_signature(body: str) -> str | None:
    """Extract the per-finding signature from a comment body, if present."""
    m = _SIG_RE.search(body or "")
    return m.group(1) if m else None


def _comment_body(finding) -> str:
    sev = getattr(finding, "severity", "info")
    claim = getattr(finding, "claim", "") or ""
    fix = getattr(finding, "suggested_fix", "") or ""
    text = f"[{sev}] {claim}"
    if fix:
        text += f" — {fix}"
    sig = _finding_signature(finding)
    return f"{INLINE_MARKER}\n<!-- arc-inline-sig:{sig} -->\n{text}"


def build_inline_payload(findings) -> list[dict]:
    """Build the inline review-comment array for the GitHub reviews API.

    Pure: one comment per finding that has BOTH a file and a line. Findings
    without a file or line are skipped (they cannot be anchored inline).
    """
    payload: list[dict] = []
    for f in findings or []:
        path = getattr(f, "file", None)
        line = getattr(f, "line", None)
        if not path or line is None:
            continue
        payload.append(
            {
                "path": str(path),
                "line": int(line),
                "side": "RIGHT",
                "body": _comment_body(f),
            }
        )
    return payload


def _resolve_repo(repo: str | None) -> str:
    if repo:
        return repo
    try:
        out = _gh("repo", "view", "--json", "nameWithOwner")
        return json.loads(out).get("nameWithOwner", "")
    except (RuntimeError, json.JSONDecodeError):
        return ""


def _existing_inline_keys(pr: str, repo: str) -> set:
    """Return (path, line, signature) keys for existing council inline comments.

    Best-effort. The line falls back to ``original_line`` when GitHub reports
    ``line`` as null (e.g. comments that became outdated after a force-push).
    """
    keys: set = set()
    try:
        out = _gh("api", f"repos/{repo}/pulls/{pr}/comments", "--paginate")
        data = json.loads(out)
    except (RuntimeError, json.JSONDecodeError):
        return keys
    if not isinstance(data, list):
        return keys
    for c in data:
        if not isinstance(c, dict):
            continue
        body = c.get("body", "") or ""
        if INLINE_MARKER not in body:
            continue
        line = c.get("line")
        if line is None:
            line = c.get("original_line")
        keys.add((c.get("path"), line, _body_signature(body)))
    return keys


def _gh_with_input(args: list[str], stdin_data: str) -> str:
    if shutil.which("gh") is None:
        raise RuntimeError("the GitHub CLI `gh` is not installed or not on PATH")
    proc = subprocess.run(["gh", *args], input=stdin_data, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def post_inline_comments(
    pr: str,
    findings,
    repo: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Post inline review comments as a single PR review.

    Best-effort dedup: skips comments whose (path, line, finding-signature)
    already has a council inline comment, so re-runs don't repost the same
    finding while still allowing distinct findings to share a line. When
    ``dry_run`` is True the payload is printed and returned without any network
    call. Returns the review payload (would-be) posted.
    """
    comments = build_inline_payload(findings)

    if dry_run:
        payload = {"event": "COMMENT", "comments": comments}
        print(json.dumps(payload, indent=2))
        return payload

    resolved = _resolve_repo(repo)
    existing = _existing_inline_keys(pr, resolved) if resolved else set()
    deduped = [
        c for c in comments
        if (c["path"], c["line"], _body_signature(c["body"])) not in existing
    ]

    payload = {"event": "COMMENT", "comments": deduped}
    if not deduped:
        return payload

    _gh_with_input(
        ["api", "--method", "POST", f"repos/{resolved}/pulls/{pr}/reviews", "--input", "-"],
        json.dumps(payload),
    )
    return payload
