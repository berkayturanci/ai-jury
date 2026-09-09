"""Every text read and write names its encoding, including the ones ruff cannot see.

On 2026-09-09 `tests/test_publish_release_chain.py` called
``(REPO_ROOT / "CHANGELOG.md").read_text()`` with no encoding. It passed on macOS
and on every Linux leg of CI and raised ``UnicodeDecodeError`` only on ``Tests
(py3.13 · windows-latest)``, because Windows defaults to cp1252 and the file is
UTF-8. The detector was a CI leg that runs late, on one platform, after everything
else is green.

`PLW1514` is the standard rule for this and it is now enabled — but **it would not
have caught that line**, which is why this file exists. Measured against ruff
0.16.5/0.16.6, the rule fires on:

* ``open(...)`` in text mode;
* ``Path("f").read_text()`` — a receiver it can see is a ``Path``;
* ``d.read_text()`` where ``d: Path`` is annotated, and the same for a parameter.

and does **not** fire on:

* ``(ROOT / "CHANGELOG.md").read_text()`` — the incident's exact shape, annotated
  root or not;
* ``p.read_text()`` where ``p = ROOT / "x.md"``.

Those are the shapes this repository actually writes. Ruff needs to know the
receiver is a ``Path``; this check does not care what the receiver is, because
``read_text``/``write_text`` mean one thing whatever they are called on. The two
overlap rather than duplicate: ruff sees ``open`` and annotated receivers, this
sees every attribute call by that name.

Read with ``ast`` rather than a regex: a comment or a docstring mentioning
``read_text()`` is not a call, and a scan that cannot tell the difference gets
silenced the first time it is wrong.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Text APIs whose default encoding is the platform's, not UTF-8. `read_bytes`
#: and `write_bytes` are absent on purpose: they take no encoding.
TEXT_METHODS = frozenset({"read_text", "write_text"})

#: Directories that are not this repository's source.
SKIPPED = frozenset({".git", ".venv", "venv", "build", "dist", "__pycache__", "htmlcov"})


def _binary_mode(call: ast.Call) -> bool:
    """Does this `open(...)` ask for bytes? Binary mode takes no encoding."""
    modes = list(call.args[1:2])
    modes += [kw.value for kw in call.keywords if kw.arg == "mode"]
    return any(isinstance(node, ast.Constant) and "b" in str(node.value) for node in modes)


#: Where `encoding` sits when it is passed positionally. `Path.read_text` takes it
#: first, `Path.write_text` second (after the data), and `open` fourth. Reading only
#: the keyword form reported `read_text("utf-8")` — which is explicit — as a
#: violation, and the first pass over this repository then added a second encoding
#: to three correct calls and broke them with `got multiple values`.
ENCODING_POSITION = {"read_text": 0, "write_text": 1, "open": 3}


def _has_encoding(call: ast.Call, name: str) -> bool:
    if any(kw.arg == "encoding" for kw in call.keywords):
        return True
    at = ENCODING_POSITION[name]
    return len(call.args) > at


def unencoded_calls(source: str) -> list[tuple[int, str]]:
    """Every text read/write in `source` that names no encoding, as `(line, what)`."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in TEXT_METHODS:
            if not _has_encoding(node, func.attr):
                found.append((node.lineno, f".{func.attr}()"))
        elif (
            isinstance(func, ast.Name)
            and func.id == "open"
            and not _binary_mode(node)
            and not _has_encoding(node, "open")
        ):
            found.append((node.lineno, "open()"))
    return found


def python_files() -> list[Path]:
    return [
        path
        for path in sorted(REPO_ROOT.rglob("*.py"))
        if not SKIPPED & set(path.relative_to(REPO_ROOT).parts)
    ]


class TheScanReadsThisRepository(unittest.TestCase):
    """Vacuity: a walk that finds no files satisfies the assertion below."""

    def test_it_found_the_source_and_the_tests(self):
        names = {path.relative_to(REPO_ROOT).as_posix() for path in python_files()}
        self.assertIn("src/ai_jury/cli.py", names)
        self.assertIn("tests/test_publish_release_chain.py", names)
        self.assertGreater(len(names), 50, sorted(names)[:5])


class TheScanSeesEachShape(unittest.TestCase):
    """The parser, checked against what it must and must not report."""

    def test_it_reports_the_shape_that_broke_windows_ci(self):
        source = 'x = (REPO_ROOT / "CHANGELOG.md").read_text()\n'
        self.assertEqual(unencoded_calls(source), [(1, ".read_text()")])

    def test_it_reports_a_receiver_ruff_cannot_type(self):
        source = "p = root / 'x.md'\ny = p.read_text()\nz = p.write_text('a')\n"
        self.assertEqual(unencoded_calls(source), [(2, ".read_text()"), (3, ".write_text()")])

    def test_a_positional_encoding_is_explicit_too(self):
        """`read_text("utf-8")` names the encoding; it just does not say the word.

        Reading only the keyword form reported three correct calls in this
        repository as violations, and the fix then added a second encoding to each
        and broke them with `got multiple values for argument 'encoding'`. A check
        that is wrong about correct code is worse than no check: it teaches the
        next person that its findings are noise.
        """
        self.assertEqual(unencoded_calls('a = (R / "f").read_text("utf-8")\n'), [])
        self.assertEqual(unencoded_calls('b = (R / "f").write_text("x", "utf-8")\n'), [])
        self.assertEqual(unencoded_calls('c = open("f", "r", -1, "utf-8")\n'), [])
        # …and one short of the encoding position is still a violation.
        self.assertEqual(unencoded_calls('d = (R / "f").write_text("x")\n'), [(1, ".write_text()")])

    def test_an_explicit_encoding_is_accepted(self):
        source = (
            'a = (R / "f").read_text(encoding="utf-8")\n'
            'b = open("f", encoding="utf-8")\n'
            'c = (R / "f").write_text("x", encoding="utf-8")\n'
        )
        self.assertEqual(unencoded_calls(source), [])

    def test_binary_mode_needs_no_encoding(self):
        for source in ('open("f", "rb")\n', 'open("f", mode="wb")\n'):
            with self.subTest(source=source.strip()):
                self.assertEqual(unencoded_calls(source), [])
        self.assertEqual(unencoded_calls('open("f")\n'), [(1, "open()")])

    def test_prose_about_read_text_is_not_a_call(self):
        """A docstring or comment naming the method is not one.

        This is why the check parses. A regex over the text would report this
        module's own docstring, and a scan that is wrong about itself gets an
        exemption written for it and then stops being read.
        """
        source = '"""Do not call read_text() without an encoding."""\n# p.read_text()\n'
        self.assertEqual(unencoded_calls(source), [])


class EveryTextCallNamesItsEncoding(unittest.TestCase):
    def test_no_file_reads_or_writes_text_without_saying_how(self):
        for path in python_files():
            where = path.relative_to(REPO_ROOT).as_posix()
            calls = unencoded_calls(path.read_text(encoding="utf-8"))
            with self.subTest(document=where):
                self.assertEqual(
                    [],
                    calls,
                    f"{where}: "
                    + ", ".join(f"line {line}: {what}" for line, what in calls)
                    + " — the platform default is not UTF-8 everywhere",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
