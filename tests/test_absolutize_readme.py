"""`scripts/absolutize_readme.py` — README links must be absolute on PyPI.

`pyproject.toml` ships `README.md` as the PyPI long description, but
`readme_renderer` cannot resolve a relative `](docs/…)` href or `<img src="docs/…">`:
every in-repo link 404s and the hero image breaks. The publish workflow rewrites
the working-tree README to absolute GitHub URLs right before `python -m build`,
and never commits it (the committed README stays relative for GitHub and for
in-repo links in a checkout).

These tests assert the transform's properties on small fixtures and on the real
README, and that `publish.yml` actually runs it before the build. `scripts/` is
outside the coverage gate, so this file is what holds it.
"""

from __future__ import annotations

import importlib.util
import re
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "absolutize_readme.py"
_spec = importlib.util.spec_from_file_location("absolutize_readme", _SCRIPT)
absolutize_readme = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(absolutize_readme)

absolutize = absolutize_readme.absolutize

_BLOB = "https://github.com/berkayturanci/ai-jury/blob/main/"
_TREE = "https://github.com/berkayturanci/ai-jury/tree/main/"
_RAW = "https://raw.githubusercontent.com/berkayturanci/ai-jury/main/"


class TransformProperties(unittest.TestCase):
    def test_a_relative_file_link_becomes_a_blob_url(self):
        self.assertEqual(
            absolutize("See [the docs](docs/architecture.md)."),
            f"See [the docs]({_BLOB}docs/architecture.md).",
        )

    def test_a_link_anchor_is_preserved(self):
        self.assertEqual(
            absolutize("[team](docs/configuration.md#agents)"),
            f"[team]({_BLOB}docs/configuration.md#agents)",
        )

    def test_a_trailing_slash_target_uses_tree(self):
        self.assertEqual(absolutize("[dir](docs/)"), f"[dir]({_TREE}docs/)")

    def test_a_relative_html_image_becomes_a_raw_url(self):
        self.assertEqual(
            absolutize('<img src="docs/assets/hero.svg" alt="hero">'),
            f'<img src="{_RAW}docs/assets/hero.svg" alt="hero">',
        )

    def test_a_relative_srcset_becomes_a_raw_url(self):
        self.assertEqual(
            absolutize('<source srcset="docs/assets/hero.svg">'),
            f'<source srcset="{_RAW}docs/assets/hero.svg">',
        )

    def test_a_relative_markdown_image_becomes_a_raw_url(self):
        self.assertEqual(
            absolutize("![hero](docs/assets/hero.svg)"),
            f"![hero]({_RAW}docs/assets/hero.svg)",
        )

    def test_a_badge_linking_to_a_file_moves_only_the_outer_target(self):
        # `[![alt](absolute-badge)](LICENSE)`: the badge image stays absolute, the
        # LICENSE link becomes a blob URL.
        src = "[![License](https://img.shields.io/badge/x.svg)](LICENSE)"
        self.assertEqual(
            absolutize(src),
            f"[![License](https://img.shields.io/badge/x.svg)]({_BLOB}LICENSE)",
        )

    def test_absolute_links_are_untouched(self):
        src = "[home](https://github.com/berkayturanci/ai-jury) and [x](http://e.com)"
        self.assertEqual(absolutize(src), src)

    def test_same_page_anchors_and_mailto_are_untouched(self):
        src = "[jump](#install) and [mail](mailto:a@b.com)"
        self.assertEqual(absolutize(src), src)

    def test_links_inside_fenced_code_blocks_are_untouched(self):
        for fence in ("```", "~~~"):
            src = f"{fence}\nsee [x](docs/y.md)\n{fence}\n"
            self.assertEqual(absolutize(src), src, f"fence {fence!r} not respected")

    def test_it_is_idempotent(self):
        src = '<img src="docs/assets/hero.svg">\n[a](docs/a.md) [b](#anchor) [c](https://x.com)\n'
        once = absolutize(src)
        self.assertEqual(absolutize(once), once)


class OnTheRealReadme(unittest.TestCase):
    def setUp(self):
        self.readme = _REPO_ROOT / "README.md"
        self.original = self.readme.read_text(encoding="utf-8")
        self.out = absolutize(self.original)

    def test_no_relative_markdown_link_survives(self):
        remaining = [
            t
            for t in re.findall(r"\]\(([^)]+)\)", self.out)
            if not t.startswith(("https://", "http://", "#", "mailto:"))
        ]
        self.assertEqual(remaining, [], f"relative links remain: {remaining}")

    def test_no_relative_image_source_survives(self):
        remaining = [
            v
            for v in re.findall(r'\bsrc(?:set)?="([^"]+)"', self.out)
            if not v.startswith(("https://", "http://"))
        ]
        self.assertEqual(remaining, [], f"relative image sources remain: {remaining}")

    def test_the_transform_does_not_touch_the_file_on_disk(self):
        # `absolutize` is pure; only the publish workflow (never a test, never a
        # commit) writes the rewritten bytes.
        self.assertEqual(self.readme.read_text(encoding="utf-8"), self.original)

    def test_it_is_idempotent_on_the_real_readme(self):
        self.assertEqual(absolutize(self.out), self.out)


class PublishWorkflowWiring(unittest.TestCase):
    def test_the_workflow_runs_the_script_before_building(self):
        workflow = (_REPO_ROOT / ".github" / "workflows" / "publish.yml").read_text(
            encoding="utf-8"
        )
        # Match the actual `run:` command lines, not prose mentions in comments.
        script_at = workflow.find("run: python scripts/absolutize_readme.py")
        build_at = workflow.find("run: python -m build --sdist --wheel --outdir dist/")
        self.assertNotEqual(script_at, -1, "publish.yml never runs absolutize_readme.py")
        self.assertNotEqual(build_at, -1, "publish.yml has no dist build step")
        self.assertLess(
            script_at,
            build_at,
            "absolutize_readme.py must run before the dist build so the rewritten "
            "README reaches the sdist and wheel long-description",
        )


class MainEntrypoint(unittest.TestCase):
    def test_main_rewrites_the_named_file_in_place(self):
        # Exercises the file read/write wrapper `publish.yml` actually calls.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "README.md"
            path.write_text('[x](docs/y.md)\n<img src="docs/z.svg">\n', encoding="utf-8")
            rc = absolutize_readme.main(["absolutize_readme.py", str(path)])
            self.assertEqual(rc, 0)
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                f'[x]({_BLOB}docs/y.md)\n<img src="{_RAW}docs/z.svg">\n',
            )


class LatentFormsTheOldGuardWouldHaveMangled(unittest.TestCase):
    """berkayturanci/keel#1261, from keel#1260's tier-3 review. None of these forms is in today's README,
    so none was a merge blocker — and none would have been *caught* either, which is
    the point: the real-README guard asks whether a target still looks relative, and
    a mangled `…/blob/main/tel:+1` starts with `https://` like any correct rewrite.
    """

    def test_a_non_http_scheme_is_left_alone(self):
        """The old guard named only `https?://`, `#` and `mailto:`, so every other
        scheme was treated as a relative path and prefixed."""
        for target in ("tel:+15551234", "ftp://example.org/x", "irc://example.org"):
            with self.subTest(target=target):
                self.assertEqual(absolutize(f"[x]({target})\n"), f"[x]({target})\n")

    def test_a_data_uri_image_is_left_alone(self):
        src = '<img src="data:image/svg+xml;base64,AAAA">\n'
        self.assertEqual(absolutize(src), src)

    def test_a_protocol_relative_target_is_left_alone(self):
        for line in ("[x](//cdn.example.org/a.png)\n", '<img src="//cdn.example.org/a.png">\n'):
            with self.subTest(line=line):
                self.assertEqual(absolutize(line), line)

    def test_every_srcset_candidate_is_rewritten(self):
        """A single prefix left the second candidate relative, so a 2x display fell
        back to a 404."""
        out = absolutize('<img srcset="docs/a.svg 1x, docs/b.svg 2x">\n')
        self.assertEqual(out, f'<img srcset="{_RAW}docs/a.svg 1x, {_RAW}docs/b.svg 2x">\n')

    def test_a_srcset_candidate_that_is_already_absolute_is_left_alone(self):
        line = '<img srcset="https://cdn/a.svg 1x, docs/b.svg 2x">\n'
        self.assertEqual(
            absolutize(line), f'<img srcset="https://cdn/a.svg 1x, {_RAW}docs/b.svg 2x">\n'
        )

    def test_a_tilde_fence_does_not_close_a_backtick_block(self):
        """The class berkayturanci/keel#1218 fixed in keel's `test_docs_links`: tracking only "inside a fence
        or not" let the wrong marker flip the state, leaving the rest of the file
        relative — here, silently shipping a 404 to PyPI."""
        text = "```\n~~~\n[x](docs/a.md)\n```\n[y](docs/b.md)\n"
        expected = f"```\n~~~\n[x](docs/a.md)\n```\n[y]({_BLOB}docs/b.md)\n"
        self.assertEqual(absolutize(text), expected)

    def test_a_backtick_fence_does_not_close_a_tilde_block(self):
        text = "~~~\n```\n[x](docs/a.md)\n~~~\n[y](docs/b.md)\n"
        expected = f"~~~\n```\n[x](docs/a.md)\n~~~\n[y]({_BLOB}docs/b.md)\n"
        self.assertEqual(absolutize(text), expected)

    def test_a_reference_style_definition_is_rewritten(self):
        self.assertEqual(absolutize("[label]: docs/a.md\n"), f"[label]: {_BLOB}docs/a.md\n")

    def test_a_reference_style_definition_keeps_its_title(self):
        self.assertEqual(
            absolutize('[label]: docs/a.md "A title"\n'),
            f'[label]: {_BLOB}docs/a.md "A title"\n',
        )

    def test_an_absolute_reference_style_definition_is_left_alone(self):
        line = "[label]: https://example.org/a\n"
        self.assertEqual(absolutize(line), line)

    def test_a_relative_html_href_is_rewritten(self):
        self.assertEqual(
            absolutize('<a href="docs/a.md">x</a>\n'), f'<a href="{_BLOB}docs/a.md">x</a>\n'
        )

    def test_a_directory_href_uses_tree(self):
        self.assertEqual(absolutize('<a href="docs/">x</a>\n'), f'<a href="{_TREE}docs/">x</a>\n')

    def test_the_new_forms_are_idempotent(self):
        text = (
            "[label]: docs/a.md\n"
            '<a href="docs/b.md">x</a>\n'
            '<img srcset="docs/c.svg 1x, docs/d.svg 2x">\n'
            "[x](tel:+15551234)\n"
        )
        once = absolutize(text)
        self.assertEqual(absolutize(once), once)


class TheRealReadmeGuardSeesTheNewForms(unittest.TestCase):
    """The guard that missed all of this asked only whether a target still *looks*
    relative. These add the forms it could not see at all."""

    def setUp(self):
        self.out = absolutize((_REPO_ROOT / "README.md").read_text(encoding="utf-8"))

    def test_no_relative_reference_definition_survives(self):
        remaining = [
            t
            for t in re.findall(r"(?m)^[ \t]{0,3}\[[^\]]+\]:[ \t]+(\S+)", self.out)
            if not re.match(r"[a-zA-Z][a-zA-Z0-9+.\-]*:|//|#", t)
        ]
        self.assertEqual(remaining, [], f"relative reference definitions remain: {remaining}")

    def test_no_relative_html_href_survives(self):
        remaining = [
            v
            for v in re.findall(r'\bhref="([^"]+)"', self.out)
            if not re.match(r"[a-zA-Z][a-zA-Z0-9+.\-]*:|//|#", v)
        ]
        self.assertEqual(remaining, [], f"relative hrefs remain: {remaining}")

    def test_no_srcset_candidate_stays_relative(self):
        remaining = []
        for value in re.findall(r'\bsrcset="([^"]+)"', self.out):
            for candidate in value.split(","):
                url = candidate.strip().split(" ")[0]
                if url and not re.match(r"[a-zA-Z][a-zA-Z0-9+.\-]*:|//", url):
                    remaining.append(url)
        self.assertEqual(remaining, [], f"relative srcset candidates remain: {remaining}")

    def test_no_target_was_mangled_into_a_scheme_bearing_path(self):
        """The assertion the old guard could not make: a rewritten target must not
        contain a second scheme after the host."""
        bad = re.findall(r"(?:blob|tree)/main/([a-zA-Z][a-zA-Z0-9+.\-]*:)", self.out)
        bad += re.findall(r"main/([a-zA-Z][a-zA-Z0-9+.\-]*:)", self.out)
        self.assertEqual(bad, [], f"a scheme was prefixed as if it were a path: {bad}")


class FormsThatOnlyLOOKLikeTheOnesWeRewrite(unittest.TestCase):
    """berkayturanci/keel#1261 round 1, agy gate. Each new rule widened what the script matches, and
    each widening reached something it should not. Matching more is not the same as
    matching right."""

    def test_a_footnote_is_not_a_reference_definition(self):
        """`[^1]: Keel is a tool` is a GFM footnote. Reading an arbitrary tail after
        the destination made it one, and rewrote `Keel` into a blob URL."""
        for line in ("[^1]: Keel is a tool\n", "[^note]: see the docs directory\n"):
            with self.subTest(line=line):
                self.assertEqual(absolutize(line), line)

    def test_a_footnote_shaped_like_a_definition_is_still_a_footnote(self):
        """The case the `[^…]` exclusion is actually for. Prose after the first word
        is already refused by the strict tail, so an ordinary footnote never reaches
        the exclusion — but `[^1]: docs/a.md "A title"` is a *valid* footnote whose
        body happens to have the shape of a destination and a title, and GFM resolves
        a `[^…]` label as a footnote definition, never a link one."""
        line = '[^1]: docs/a.md "A title"\n'
        self.assertEqual(absolutize(line), line)

    def test_a_label_followed_by_prose_is_not_a_reference_definition(self):
        """CommonMark §4.7: only an optional quoted title may follow the destination.
        Anything else and the line is ordinary paragraph text."""
        for line in ("[NOTE]: remember to update docs/x\n", "[TODO]: fix docs/y later\n"):
            with self.subTest(line=line):
                self.assertEqual(absolutize(line), line)

    def test_a_reference_definition_with_each_title_form_is_still_rewritten(self):
        """The counterweight: refusing prose must not refuse the real thing."""
        for tail in ('"A title"', "'A title'", "(A title)"):
            with self.subTest(tail=tail):
                self.assertEqual(
                    absolutize(f"[label]: docs/a.md {tail}\n"),
                    f"[label]: {_BLOB}docs/a.md {tail}\n",
                )

    def test_a_longer_fence_is_not_closed_by_a_shorter_one(self):
        """A ````markdown block exists to show ``` examples. Treating any three-char
        run as a boundary closed it on its own content and rewrote the example."""
        text = "````markdown\n```\n[x](docs/a.md)\n```\n````\n[y](docs/b.md)\n"
        expected = f"````markdown\n```\n[x](docs/a.md)\n```\n````\n[y]({_BLOB}docs/b.md)\n"
        self.assertEqual(absolutize(text), expected)

    def test_a_line_with_an_info_string_does_not_close_a_block(self):
        """CommonMark §4.5: a closing fence carries no info string."""
        text = "```\n```foo\n[x](docs/a.md)\n```\n"
        self.assertEqual(absolutize(text), text)

    def test_an_opening_fence_may_carry_an_info_string(self):
        """The counterweight: ```python must still open a block."""
        text = "```python\n[x](docs/a.md)\n```\n"
        self.assertEqual(absolutize(text), text)

    def test_a_data_attribute_ending_in_href_is_not_an_href(self):
        """`\\bhref=` matched `data-href=`, because a hyphen is not a word character."""
        for line in ('<div data-href="docs/x"></div>\n', '<div x-href="docs/x"></div>\n'):
            with self.subTest(line=line):
                self.assertEqual(absolutize(line), line)

    def test_an_href_outside_an_anchor_is_left_alone(self):
        """`blob/` is a GitHub *page*. A stylesheet or an SVG `<image>` wants bytes,
        so those are not this script's to rewrite."""
        for line in (
            '<link rel="stylesheet" href="docs/a.css">\n',
            '<image href="docs/a.svg"/>\n',
        ):
            with self.subTest(line=line):
                self.assertEqual(absolutize(line), line)

    def test_a_tab_separated_srcset_descriptor_is_not_glued_to_the_url(self):
        """The HTML standard allows any ASCII whitespace between a candidate's URL
        and its descriptor; splitting on one space made `a.svg\\t1x` the URL."""
        self.assertEqual(
            absolutize('<img srcset="docs/a.svg\t1x">\n'),
            f'<img srcset="{_RAW}docs/a.svg 1x">\n',
        )

    def test_these_forms_are_idempotent_too(self):
        text = (
            "[^1]: Keel is a tool\n"
            "[NOTE]: remember to update\n"
            '<div data-href="docs/x"></div>\n'
            "````markdown\n```\n[x](docs/a.md)\n```\n````\n"
            '<img srcset="docs/a.svg\t1x">\n'
        )
        once = absolutize(text)
        self.assertEqual(absolutize(once), once)


if __name__ == "__main__":
    unittest.main()
