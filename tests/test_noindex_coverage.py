"""`scripts/noindex_coverage.py` keeps the generated coverage pages out of search.

`coverage html -d website/coverage` writes ~one page per source file and Pages
publishes them; Google indexed them. The script adds a noindex meta to each. These
tests hold it, since `scripts/` is outside the coverage gate.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "noindex_coverage", REPO_ROOT / "scripts" / "noindex_coverage.py"
)
nc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nc)

PAGE = (
    '<!DOCTYPE html>\n<html>\n<head>\n<meta charset="utf-8">\n<title>t</title>\n'
    "</head>\n<body></body></html>\n"
)


class TestMarkNoindex(unittest.TestCase):
    def test_inserts_right_after_head_once(self):
        out = nc.mark_noindex(PAGE)
        self.assertIn("<head>" + nc.META, out)
        self.assertEqual(out.count(nc.META), 1)

    def test_idempotent(self):
        once = nc.mark_noindex(PAGE)
        self.assertEqual(nc.mark_noindex(once), once)

    def test_head_with_attributes_and_case(self):
        out = nc.mark_noindex('<HTML><HEAD lang="en"><title>x</title></HEAD></HTML>')
        self.assertIn('<HEAD lang="en">' + nc.META, out)

    def test_no_head_is_unchanged(self):
        self.assertEqual(nc.mark_noindex("<p>fragment</p>"), "<p>fragment</p>")

    def test_does_not_match_a_header_tag(self):
        self.assertEqual(nc.mark_noindex("<header>x</header>"), "<header>x</header>")


class TestMarkTree(unittest.TestCase):
    def test_marks_html_recursively_and_leaves_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "coverage"
            (target / "sub").mkdir(parents=True)
            (target / "z_abc_cli_py.html").write_text(PAGE, encoding="utf-8")
            (target / "sub" / "function_index.html").write_text(PAGE, encoding="utf-8")
            (target / "style.css").write_text("<head>", encoding="utf-8")
            (target / "fragment.html").write_text("<p>no head</p>", encoding="utf-8")
            (root / "coverage.html").write_text(PAGE, encoding="utf-8")  # outside the dir
            self.assertEqual(nc.mark_tree(target), 2)
            for rel in ("z_abc_cli_py.html", "sub/function_index.html"):
                text = (target / rel).read_text(encoding="utf-8")
                self.assertEqual(text.count(nc.META), 1, rel)
            self.assertEqual((target / "style.css").read_text(encoding="utf-8"), "<head>")
            self.assertEqual(
                (target / "fragment.html").read_text(encoding="utf-8"), "<p>no head</p>"
            )
            self.assertEqual((root / "coverage.html").read_text(encoding="utf-8"), PAGE)
            self.assertEqual(nc.mark_tree(target), 0)  # second run changes nothing


class TestMain(unittest.TestCase):
    def _run(self, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = nc.main(args)
        return code, out.getvalue(), err.getvalue()

    def test_marks_a_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.html").write_text(PAGE, encoding="utf-8")
            code, out, _ = self._run([tmp])
            self.assertEqual(code, 0)
            self.assertIn("marked 1 page(s)", out)

    def test_usage_and_missing_directory_fail(self):
        self.assertEqual(self._run([])[0], 2)
        self.assertEqual(self._run(["/nonexistent-noindex-dir"])[0], 1)


class TestTheBuildRunsIt(unittest.TestCase):
    def _after_html(self, text, name):
        m = re.search(r"coverage html -d website/coverage\b.*", text)
        self.assertIsNotNone(m, f"{name} no longer builds the coverage report")
        self.assertIn("scripts/noindex_coverage.py website/coverage", text[m.start() :], name)

    def test_pages_workflow_marks_the_report_after_building_it(self):
        text = (REPO_ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        self._after_html(text, "pages.yml")
