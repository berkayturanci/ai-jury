#!/usr/bin/env python3
"""Rewrite README relative links and images to absolute GitHub URLs, for PyPI.

Ported from keel (berkayturanci/keel), where it was written for the same defect and
hardened through two rounds of review; the issue numbers below are keel's.

Publish-only. `pyproject.toml` uses `README.md` as the PyPI long description, but
PyPI's `readme_renderer` cannot resolve a relative `](docs/…)` href or a relative
`<img src="docs/…">`: every in-repo link 404s and the hero image breaks. So the
publish workflow runs this against the working-tree README right before
`python -m build`, and never commits the result — the committed README stays
relative so GitHub renders it and in-repo links keep working in a checkout.

What it rewrites (only outside fenced code blocks):

- relative Markdown links   `[text](docs/…)`        → `…/blob/main/docs/…`
  (a trailing-slash target → `…/tree/main/…`; any `#anchor` is preserved)
- relative reference defs   `[label]: docs/…`        → the same, title preserved
- relative Markdown images  `![alt](docs/…)`         → `raw.githubusercontent.com/…/main/docs/…`
- relative HTML `href`      `<a href="docs/…">`      → `…/blob/main/docs/…`
- relative HTML `src`/`srcset` (the hero `<picture>`) → the same raw host,
  every candidate in a multi-candidate `srcset`, not just the first

Left untouched: anything carrying a URL scheme (`https:`, `mailto:`, `tel:`,
`data:`, `ftp:`…), a protocol-relative `//host/…` target, and same-page
`#anchors`. The old guard named only `https?://`, `#` and `mailto:`, so a
future `[x](tel:…)` or `<img src="data:…">` would have been rewritten into
`…/blob/main/tel:…` — and the real-README guard would not have caught it,
because the mangled target still starts with `https://` (berkayturanci/keel#1261).

The transform is deterministic and idempotent (a second run is a no-op), so the
reproducible-build rebuild in `publish.yml` sees the same bytes and digests match.

`scripts/` is maintenance tooling outside the coverage gate;
`tests/test_absolutize_readme.py` is what holds this.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_OWNER_REPO = "berkayturanci/ai-jury"
_BLOB = f"https://github.com/{_OWNER_REPO}/blob/main/"
_TREE = f"https://github.com/{_OWNER_REPO}/tree/main/"
_RAW = f"https://raw.githubusercontent.com/{_OWNER_REPO}/main/"

# "Not already pointing somewhere of its own": any URL scheme (`https:`, `tel:`,
# `data:`, `mailto:`…), a protocol-relative `//host/…`, or a same-page `#anchor`.
# Ported from keel's `scripts/absolutize_readme.py` (berkayturanci/keel#1260 and berkayturanci/keel#1261).
_REL = r"(?![a-zA-Z][a-zA-Z0-9+.\-]*:|//|#)"

# A fenced code block boundary. The marker is captured whole, because CommonMark
# §4.5 makes closing a fence stricter than opening one: a closer uses the same
# character, is **at least as long**, and carries no info string. Matching a fixed
# three characters broke both ways — a `~~~` line inside a ``` block closed it, and
# a ````markdown block showing ``` examples closed on its own content.
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})([^\n]*)$")
# Markdown image: ![alt](target) where target is not already absolute.
_MD_IMAGE = re.compile(rf"!\[([^\]]*)\]\({_REL}([^)]+)\)")
# Markdown link: [text](target), not an image itself (negative lookbehind for !),
# where target is relative. The text may be plain OR a nested image — the
# badge-in-link pattern `[![alt](badge)](LICENSE)` — so the alternation keeps the
# whole label intact while only the outer target moves.
_MD_LINK = re.compile(rf"(?<!!)\[(!\[[^\]]*\]\([^)]*\)|[^\]]*)\]\({_REL}([^)]+)\)")
# Reference-style definition: `[label]: docs/x "optional title"`, up to three
# leading spaces. CommonMark §4.7 allows **only** an optional quoted title after the
# destination; anything else makes the line ordinary paragraph text. Accepting an
# arbitrary tail turned every GFM footnote (`[^1]: Keel is a tool`) and every
# `[NOTE]: remember to …` line into a definition and rewrote its first word into a
# blob URL. A `[^…]` label is a footnote, never a definition.
_MD_REFDEF = re.compile(
    rf"^([ \t]{{0,3}}\[(?!\^)[^\]]+\]:[ \t]+){_REL}(\S+)"
    r"((?:[ \t]+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?[ \t]*)$"
)
# HTML src="…" with a relative value (the hero <picture>/<img>).
_HTML_SRC = re.compile(rf'\bsrc="{_REL}([^"]+)"')
# HTML srcset="…". Deliberately **not** guarded at the attribute level: a srcset is
# a list, and its candidates can mix. Guarding the whole attribute on its first
# candidate meant `srcset="https://cdn/a.svg 1x, docs/b.svg 2x"` matched nothing at
# all and the relative second candidate survived. Each candidate is guarded on its
# own instead.
_HTML_SRCSET = re.compile(r'\bsrcset="([^"]+)"')
# HTML href="…" with a relative value (<a href="docs/…">).
# `\bhref=` also matched `data-href=` (a hyphen is not a word character), and `blob/`
# is the wrong host for a `<link rel="stylesheet">` or an SVG `<image href>` — those
# want raw bytes, not a GitHub page — so the tag is named rather than assumed.
_HTML_HREF = re.compile(rf'(<a\b[^>]*?(?<![-\w])href="){_REL}([^"]+)"')


def _link_host(target: str) -> str:
    """Directory targets (trailing slash) resolve under `tree/`, files under `blob/`."""
    return _TREE if target.endswith("/") else _BLOB


def _absolutize_srcset(value: str) -> str:
    """Prefix every candidate in a `srcset`, not only the first.

    `srcset` is a comma-separated list of `url [descriptor]` candidates, so a
    single prefix left `docs/b.svg 2x` relative in `"docs/a.svg 1x, docs/b.svg 2x"`.
    """
    out = []
    for candidate in value.split(","):
        stripped = candidate.strip()
        if not stripped:
            out.append(candidate)
            continue
        # The HTML standard separates a candidate's URL from its descriptor with any
        # ASCII whitespace, so splitting on one space glued a tab-separated descriptor
        # onto the URL.
        parts = stripped.split(None, 1)
        url, descriptor = parts[0], (parts[1] if len(parts) > 1 else "")
        if re.match(_REL + r".", url) is None:
            out.append(candidate)
            continue
        rebuilt = f"{_RAW}{url}" + (f" {descriptor.strip()}" if descriptor.strip() else "")
        out.append(rebuilt)
    return ", ".join(part.strip() for part in out)


def absolutize(text: str) -> str:
    """Return *text* with relative README links/images made absolute (pure)."""
    out: list[str] = []
    fence: str | None = None  # the opening marker, or None outside a block
    for line in text.splitlines(keepends=True):
        boundary = _FENCE.match(line.rstrip("\r\n"))
        if boundary is not None:
            marker, info = boundary.group(1), boundary.group(2)
            if fence is None:
                # An opening fence may carry an info string (```python).
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not info.strip():
                fence = None
            out.append(line)
            continue
        if fence is not None:
            out.append(line)
            continue
        # Images first, so a relative `![](…)` is not also seen as a link.
        line = _MD_IMAGE.sub(lambda m: f"![{m.group(1)}]({_RAW}{m.group(2)})", line)
        line = _MD_LINK.sub(lambda m: f"[{m.group(1)}]({_link_host(m.group(2))}{m.group(2)})", line)
        line = _MD_REFDEF.sub(
            lambda m: f"{m.group(1)}{_link_host(m.group(2))}{m.group(2)}{m.group(3)}", line
        )
        line = _HTML_SRC.sub(lambda m: f'src="{_RAW}{m.group(1)}"', line)
        line = _HTML_SRCSET.sub(lambda m: f'srcset="{_absolutize_srcset(m.group(1))}"', line)
        line = _HTML_HREF.sub(lambda m: f'{m.group(1)}{_link_host(m.group(2))}{m.group(2)}"', line)
        out.append(line)
    return "".join(out)


def main(argv: list[str]) -> int:
    paths = argv[1:] or ["README.md"]
    for name in paths:
        path = Path(name)
        path.write_text(absolutize(path.read_text(encoding="utf-8")), encoding="utf-8")
        print(f"absolutized {name} for PyPI")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
