"""Thin GitHub helpers built on the `gh` CLI.

Used to pull a PR diff in and to post the jury verdict back as a comment.
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


def pr_head_sha(pr: str, repo: str | None = None) -> str:
    """Return the current head commit SHA of a PR (best-effort, '' on failure)."""
    args = ["pr", "view", str(pr), "--json", "headRefOid", "--jq", ".headRefOid"]
    if repo:
        args += ["--repo", repo]
    try:
        return _gh(*args).strip()
    except RuntimeError:
        return ""


def pr_comment_bodies(pr: str, repo: str | None = None) -> list[str]:
    """Return the bodies of a PR's issue comments (best-effort, [] on failure).

    Used by incremental mode (issue #9) to find the jury's prior
    reviewed-SHA marker. Network errors degrade to an empty list so the caller
    safely falls back to a full review.
    """
    args = ["pr", "view", str(pr), "--json", "comments", "--jq", ".comments[].body"]
    if repo:
        args += ["--repo", repo]
    try:
        out = _gh(*args)
    except RuntimeError:
        return []
    return out.splitlines()


def compare_diff(base: str, head: str, repo: str | None = None) -> str:
    """Return the unified diff between two SHAs via the compare API (issue #9).

    Uses the ``application/vnd.github.v3.diff`` media type so the response is a
    ready-to-review unified diff. Returns '' on failure so callers can fall back.
    """
    resolved = _resolve_repo(repo)
    if not resolved:
        return ""
    try:
        return _gh(
            "api", f"repos/{resolved}/compare/{base}...{head}",
            "-H", "Accept: application/vnd.github.v3.diff",
        )
    except RuntimeError:
        return ""


def build_label_args(pr: str, labels, repo: str | None = None) -> list[str]:
    """Build the ``gh pr edit`` arg vector for applying labels (pure).

    Returns ``[]`` when there are no labels (nothing to do). Kept pure and
    network-free so the arg construction can be unit-tested without invoking
    ``gh`` or hitting GitHub.
    """
    clean = [str(label) for label in (labels or []) if str(label).strip()]
    if not clean:
        return []
    args = ["pr", "edit", str(pr)]
    for label in clean:
        args += ["--add-label", label]
    if repo:
        args += ["--repo", repo]
    return args


def apply_labels(pr: str, labels, repo: str | None = None) -> list[str]:
    """Best-effort: apply ``labels`` to ``pr`` via ``gh pr edit --add-label``.

    Only called when labeling is explicitly enabled (CLI ``--label``); it never
    runs by default. No-op (returns ``[]``) when there are no labels. Returns the
    ``gh`` arg vector that was invoked so callers can log it.
    """
    args = build_label_args(pr, labels, repo)
    if not args:
        return args
    _gh(*args)
    return args


# Marker prefix identifying comments authored by the jury (enables dedup).
INLINE_MARKER = "<!-- arc-inline -->"

# Hidden per-finding signature marker, embedded in the comment body so that
# re-runs can match an existing comment back to the finding that produced it.
# Two distinct findings on the same (path, line) get distinct signatures and
# therefore do NOT collapse into one another during dedup.
_SIG_MARKER_RE = re.compile(r"<!-- arc-sig:([0-9a-f]+) -->")


def _finding_signature(finding) -> str:
    """Return a short, stable hash identifying a finding.

    Derived from the normalized ``severity`` and ``claim`` (lowercased and
    stripped) so the same finding yields the same signature across runs, while
    a different severity or claim yields a different one.
    """
    sev = (getattr(finding, "severity", "") or "").strip().lower()
    claim = (getattr(finding, "claim", "") or "").strip().lower()
    raw = f"{sev}|{claim}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _sig_marker(signature: str) -> str:
    return f"<!-- arc-sig:{signature} -->"


def _sig_from_body(body: str | None) -> str:
    """Extract the embedded finding signature from a comment body ('' if none)."""
    if not body:
        return ""
    match = _SIG_MARKER_RE.search(body)
    return match.group(1) if match else ""


def _comment_body(finding) -> str:
    sev = getattr(finding, "severity", "info")
    claim = getattr(finding, "claim", "") or ""
    fix = getattr(finding, "suggested_fix", "") or ""
    text = f"[{sev}] {claim}"
    if fix:
        text += f" — {fix}"
    # The signature marker is hidden (HTML comment); the visible body for humans
    # is unchanged.
    sig = _sig_marker(_finding_signature(finding))
    return f"{INLINE_MARKER}{sig}\n{text}"


def _review_body(n: int) -> str:
    """Top-level review body. GitHub's create-review API requires a non-empty
    ``body`` when ``event`` is COMMENT (omitting it can 422) — issue #122."""
    return f"{INLINE_MARKER}\n🏛️ AI Jury — {n} inline finding(s)."


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
    """Return ``(path, line, signature)`` keys for existing jury comments.

    Best-effort. ``line`` falls back to ``original_line`` when GitHub reports a
    null ``line`` (e.g. for outdated comments). ``signature`` is parsed back out
    of the comment body so distinct findings on the same line are tracked
    independently.
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
        keys.add((c.get("path"), line, _sig_from_body(body)))
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

    Best-effort dedup: skips comments whose ``(path, line, finding-signature)``
    already has a jury inline comment. Keying on the signature means two
    distinct findings on the same line are both posted. When ``dry_run`` is True
    the payload is printed and returned without any network call. Returns the
    review payload (would-be) posted.
    """
    comments = build_inline_payload(findings)

    if dry_run:
        payload = {"event": "COMMENT", "body": _review_body(len(comments)), "comments": comments}
        print(json.dumps(payload, indent=2))
        return payload

    resolved = _resolve_repo(repo)
    existing = _existing_inline_keys(pr, resolved) if resolved else set()
    deduped = [
        c
        for c in comments
        if (c["path"], c["line"], _sig_from_body(c["body"])) not in existing
    ]

    payload = {"event": "COMMENT", "body": _review_body(len(deduped)), "comments": deduped}
    if not deduped:
        return payload

    _gh_with_input(
        ["api", "--method", "POST", f"repos/{resolved}/pulls/{pr}/reviews", "--input", "-"],
        json.dumps(payload),
    )
    return payload
