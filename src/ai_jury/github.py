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
import threading

from .findings import strip_html_comments
from .redaction import redact

# Every `gh` invocation is bounded (#246): a stalled network call or an
# interactive auth/2FA prompt would otherwise block `subprocess.run` forever and
# hang the whole jury run with no per-call ceiling. On timeout we fail soft with
# a clear, actionable error like any other gh failure.
_GH_TIMEOUT_S = 90

# Ceiling on `gh` stdout. A hostile/huge PR diff pulled via `--pr`/`--issue`
# would otherwise be buffered whole by `subprocess.run`, OOMing the process
# before the diff budget engages (security audit 2026-06-13 r3). We stream the
# output and stop at the cap; stdout/stderr are drained on separate threads so a
# full stderr pipe can't deadlock the stdout read.
_GH_MAX_OUTPUT_BYTES = 64 * 1024 * 1024  # 64 MiB


def _gh(*args: str) -> str:
    if shutil.which("gh") is None:
        raise RuntimeError("the GitHub CLI `gh` is not installed or not on PATH")
    label = " ".join(args)
    proc = subprocess.Popen(["gh", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    holder: dict[str, bytes] = {}

    def _drain(stream, key: str) -> None:
        # read(N+1) is bounded: at most N+1 bytes, never the whole stream if it
        # is larger.
        holder[key] = stream.read(_GH_MAX_OUTPUT_BYTES + 1)

    t_out = threading.Thread(target=_drain, args=(proc.stdout, "out"), daemon=True)
    t_err = threading.Thread(target=_drain, args=(proc.stderr, "err"), daemon=True)
    t_out.start()
    t_err.start()
    t_out.join(_GH_TIMEOUT_S)
    if t_out.is_alive():
        proc.kill()
        raise RuntimeError(f"gh {label} timed out after {_GH_TIMEOUT_S}s")
    out = holder.get("out", b"")
    if len(out) > _GH_MAX_OUTPUT_BYTES:
        proc.kill()
        raise RuntimeError(f"gh {label} output exceeds the {_GH_MAX_OUTPUT_BYTES}-byte limit")
    try:
        proc.wait(timeout=_GH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError(f"gh {label} timed out after {_GH_TIMEOUT_S}s") from None
    t_err.join(_GH_TIMEOUT_S)
    if proc.returncode != 0:
        err = holder.get("err", b"").decode("utf-8", "replace")
        safe_err = redact(err.strip())[0]
        raise RuntimeError(f"gh {label} failed: {safe_err}")
    return out.decode("utf-8", "replace")


def pr_diff(pr: str, repo: str | None = None) -> str:
    args = ["pr", "diff"]
    if repo:
        args += ["--repo", repo]
    args += ["--", str(pr)]
    return _gh(*args)


def pr_context(pr: str, repo: str | None = None) -> str:
    """Return 'title\\n\\nbody' for a PR, best-effort."""
    args = ["pr", "view", "--json", "title,body", "--jq", '.title + "\\n\\n" + (.body // "")']
    if repo:
        args += ["--repo", repo]
    args += ["--", str(pr)]
    try:
        return _gh(*args).strip()
    except RuntimeError:
        return ""


def post_pr_comment(pr: str, body: str, repo: str | None = None) -> None:
    args = ["pr", "comment", "--body", body]
    if repo:
        args += ["--repo", repo]
    args += ["--", str(pr)]
    _gh(*args)


def issue_body(number: str, repo: str | None = None) -> str:
    """Return a reviewable text rendering of a GitHub issue, best-effort.

    Formats the issue as ``"# <title>\\n\\n_labels: a, b_\\n\\n<body>"`` so the
    reviewer sees the title, labels, and description as one prose block. Mirrors
    :func:`pr_context`'s error handling: any ``gh`` failure degrades to a minimal
    string (the bare number) rather than crashing the run.
    """
    args = [
        "issue",
        "view",
        "--json",
        "title,body,labels",
        "--jq",
        '"# " + .title + "\\n\\n_labels: " '
        '+ ((.labels | map(.name)) | join(", ")) + "_\\n\\n" + (.body // "")',
    ]
    if repo:
        args += ["--repo", repo]
    args += ["--", str(number)]
    try:
        return _gh(*args).strip()
    except RuntimeError:
        return f"# issue #{number}"


def post_issue_comment(number: str, body: str, repo: str | None = None) -> None:
    """Post a comment on a plain GitHub issue.

    A separate function from :func:`post_pr_comment` because ``gh pr comment``
    only works for pull requests; ``gh issue comment`` is the issue-side command.
    """
    args = ["issue", "comment", "--body", body]
    if repo:
        args += ["--repo", repo]
    args += ["--", str(number)]
    _gh(*args)


def pr_head_sha(pr: str, repo: str | None = None) -> str:
    """Return the current head commit SHA of a PR (best-effort, '' on failure)."""
    args = ["pr", "view", "--json", "headRefOid", "--jq", ".headRefOid"]
    if repo:
        args += ["--repo", repo]
    args += ["--", str(pr)]
    try:
        return _gh(*args).strip()
    except RuntimeError:
        return ""


def pr_comment_bodies(pr: str, repo: str | None = None) -> list[str]:
    """Return bodies of a PR's issue comments from TRUSTED authors only.

    Used by incremental mode (issue #9) to find the jury's prior reviewed-SHA
    marker. The marker is security-sensitive: a forged ``arc-reviewed-sha``
    marker would let an attacker narrow the reviewed range and skip malicious
    commits (audit 2026-06-13 r4/M-1). So we only return comments authored by a
    repo OWNER/MEMBER/COLLABORATOR — an external fork-PR author (CONTRIBUTOR /
    FIRST_TIME_CONTRIBUTOR / NONE) cannot inject a trusted marker. (Run the jury
    under such an identity for incremental mode; otherwise it safely falls back
    to a full review.) Network errors degrade to an empty list.
    """
    jq = (
        '.comments[] | select(.authorAssociation=="OWNER" or '
        '.authorAssociation=="MEMBER" or .authorAssociation=="COLLABORATOR") | .body'
    )
    args = ["pr", "view", "--json", "comments", "--jq", jq]
    if repo:
        args += ["--repo", repo]
    args += ["--", str(pr)]
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
            "api",
            "-H",
            "Accept: application/vnd.github.v3.diff",
            "--",
            f"repos/{resolved}/compare/{base}...{head}",
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
    args = ["pr", "edit"]
    for label in clean:
        args += ["--add-label", label]
    if repo:
        args += ["--repo", repo]
    args += ["--", str(pr)]
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
    # Strip HTML comments so a finding can't forge the hidden inline markers
    # (``<!-- arc-inline -->`` / ``<!-- arc-sig:… -->``) and perturb dedup
    # (audit 2026-06-13 r3/N-3).
    claim = strip_html_comments(getattr(finding, "claim", "") or "")
    fix = strip_html_comments(getattr(finding, "suggested_fix", "") or "")
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
    except (RuntimeError, json.JSONDecodeError, RecursionError):
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
        out = _gh("api", "--paginate", "--", f"repos/{repo}/pulls/{pr}/comments")
        data = json.loads(out)
    except (RuntimeError, json.JSONDecodeError, RecursionError):
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
    try:
        proc = subprocess.run(
            ["gh", *args],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"gh {' '.join(args)} timed out after {_GH_TIMEOUT_S}s") from None
    if proc.returncode != 0:
        safe_err = redact(proc.stderr.strip())[0]
        raise RuntimeError(f"gh {' '.join(args)} failed: {safe_err}")
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
        c for c in comments if (c["path"], c["line"], _sig_from_body(c["body"])) not in existing
    ]

    payload = {"event": "COMMENT", "body": _review_body(len(deduped)), "comments": deduped}
    if not deduped:
        return payload

    _gh_with_input(
        ["api", "--method", "POST", "--input", "-", "--", f"repos/{resolved}/pulls/{pr}/reviews"],
        json.dumps(payload),
    )
    return payload


# Hidden marker identifying the jury's single sticky progress comment (issue #125).
PROGRESS_MARKER = "<!-- arc-progress -->"


def render_progress_body(stages: list[str], *, done: bool = False, final: str | None = None) -> str:
    """Render the sticky progress-comment body (pure, issue #125).

    ``stages`` is the ordered list of milestones reached. When ``done`` and a
    ``final`` report is given, the comment becomes the verdict (with the marker
    kept so the same comment is reused on a re-run).
    """
    if done and final is not None:
        return f"{PROGRESS_MARKER}\n{final}"
    header = "🏛️ **AI Jury** — review complete." if done else "🏛️ **AI Jury** — review in progress…"
    lines = [PROGRESS_MARKER, header, ""]
    for s in stages:
        lines.append(f"- {s}")
    if not done:
        lines.append("\n_Updating live; the verdict will replace this when done._")
    return "\n".join(lines)


def _create_issue_comment(pr: str, body: str, repo: str) -> int | None:
    """Create a PR/issue comment, returning its numeric id (or None)."""
    try:
        out = _gh_with_input(
            ["api", "--method", "POST", "--input", "-", "--", f"repos/{repo}/issues/{pr}/comments"],
            json.dumps({"body": body}),
        )
        return json.loads(out).get("id")
    except (RuntimeError, json.JSONDecodeError, RecursionError):
        return None


def _edit_issue_comment(comment_id: int, body: str, repo: str) -> bool:
    try:
        _gh_with_input(
            [
                "api",
                "--method",
                "PATCH",
                "--input",
                "-",
                "--",
                f"repos/{repo}/issues/comments/{comment_id}",
            ],
            json.dumps({"body": body}),
        )
        return True
    except RuntimeError:
        return False


class ProgressReporter:
    """Maintains ONE sticky PR comment, updated as the run advances (issue #125).

    Best-effort and resilient: a resolve/create/edit failure is swallowed so a
    GitHub hiccup never crashes the review. The first ``update`` creates the
    comment; subsequent updates edit it in place; ``finish`` turns it into the
    final verdict.
    """

    def __init__(self, pr: str, repo: str | None = None):
        self.pr = str(pr)
        self.repo = _resolve_repo(repo)
        self.comment_id: int | None = None
        self.stages: list[str] = []

    def _push(self, body: str) -> None:
        if not self.repo:
            return
        if self.comment_id is None:
            self.comment_id = _create_issue_comment(self.pr, body, self.repo)
        else:
            _edit_issue_comment(self.comment_id, body, self.repo)

    def update(self, milestone: str) -> None:
        self.stages.append(milestone)
        self._push(render_progress_body(self.stages, done=False))

    def finish(self, final_report: str) -> None:
        self._push(render_progress_body(self.stages, done=True, final=final_report))
