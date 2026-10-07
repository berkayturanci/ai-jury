"""Mark every generated coverage HTML page as not indexable.

``coverage html -d website/coverage`` writes one page per measured source file
(``z_<hash>_cli_py.html``, ``function_index.html``, ...). They are published with
the site, and Google indexed them next to the real pages. Only the hand-written
pages belong in search, so this inserts ``<meta name="robots" content="noindex,
follow">`` right after ``<head>`` in every ``.html`` file under the given
directory. ``follow`` keeps the report's links to the rest of the site crawlable.

Do not block ``/coverage/`` in robots.txt instead: a crawler that is not allowed to
fetch a page never sees its noindex, and the URL can stay indexed.

Idempotent (a page that already carries the tag is left alone) and stdlib only.
Files that are not ``.html`` or have no ``<head>`` are left alone.

Usage::

    python scripts/noindex_coverage.py website/coverage
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

META = '<meta name="robots" content="noindex, follow">'
_HEAD = re.compile(r"<head(?:\s[^>]*)?>", re.IGNORECASE)
_ALREADY = re.compile(r'<meta\s+name=["\']robots["\']\s+content=["\']noindex', re.IGNORECASE)


def mark_noindex(html: str) -> str:
    """Return ``html`` with the noindex meta right after ``<head>``, or unchanged."""
    if _ALREADY.search(html):
        return html
    match = _HEAD.search(html)
    if match is None:
        return html
    return html[: match.end()] + META + html[match.end() :]


def mark_tree(directory: Path) -> int:
    """Mark every ``.html`` file under ``directory``; return how many changed."""
    changed = 0
    for path in sorted(directory.rglob("*.html")):
        original = path.read_text(encoding="utf-8")
        marked = mark_noindex(original)
        if marked != original:
            path.write_text(marked, encoding="utf-8")
            changed += 1
    return changed


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: noindex_coverage.py <coverage-html-dir>", file=sys.stderr)
        return 2
    directory = Path(args[0])
    if not directory.is_dir():
        print(f"noindex_coverage: {directory} is not a directory", file=sys.stderr)
        return 1
    print(f"noindex_coverage: marked {mark_tree(directory)} page(s) under {directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
