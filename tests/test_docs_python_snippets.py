"""Guard the ``python`` code snippets embedded in `docs/configuration.md` (#25).

The "Custom Pluggable Python Adapter" example imported a class that does not
exist (`BaseAdapter`; the base is `Adapter`), overrode a method the panel never
calls (`invoke`), and built an `AgentResult` with keywords it does not accept
(`text=`; the field is `output`). None of it would import or run — and nothing
caught it, because the docs-snippet guard only looked at fenced ``yaml`` keel
blocks, never ``python``.

So this executes the documented adapter example the way a reader would:

1. Every fenced ``python`` block in `configuration.md` must at least *compile*
   (catches the typo class above in any snippet).
2. The block tagged ``# docs-exec: custom-adapter`` is executed verbatim, and the
   adapter it defines is built through the real ``make_adapter`` path and run —
   so the example is guaranteed to import, register, and produce a result.
"""

from __future__ import annotations

import importlib.util
import re
import tempfile
import unittest
from pathlib import Path

from ai_jury import adapters
from ai_jury import config as config_module
from ai_jury.adapters import AgentResult, make_adapter
from ai_jury.config import AgentSpec

CONFIGURATION = Path(__file__).resolve().parent.parent / "docs" / "configuration.md"

_PY_FENCE_RE = re.compile(r"```python\n(.*?)\n```", re.DOTALL)
_EXEC_MARKER = "# docs-exec: custom-adapter"


def _python_blocks(markdown_text: str) -> list[str]:
    return [m.group(1) for m in _PY_FENCE_RE.finditer(markdown_text)]


def _run_block_as_module(source: str, name: str):
    """Execute a doc snippet by importing it as a throwaway module.

    Via the import machinery (``Loader.exec_module``) rather than the ``exec``
    builtin, so the snippet runs exactly as a reader's ``.py`` file would and the
    test carries no ``exec``/``eval``.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{name}.py"
        path.write_text(source, encoding="utf-8")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class ConfigurationPythonSnippets(unittest.TestCase):
    def setUp(self):
        self.blocks = _python_blocks(CONFIGURATION.read_text(encoding="utf-8"))

    def _forget(self, vendor: str) -> None:
        config_module._REGISTERED_VENDORS.discard(vendor)
        adapters._VENDOR_ADAPTERS.pop(vendor, None)

    def test_there_is_at_least_one_python_snippet(self):
        # Vacuity guard: if the fence or marker is reworded away, the checks below
        # would silently stop running.
        self.assertGreater(len(self.blocks), 0, f"no fenced ```python block in {CONFIGURATION}")

    def test_every_python_snippet_compiles(self):
        for block in self.blocks:
            try:
                compile(block, str(CONFIGURATION), "exec")
            except SyntaxError as exc:
                self.fail(f"python snippet does not compile: {exc}\n---\n{block}")

    def test_the_custom_adapter_example_imports_registers_and_runs(self):
        matching = [b for b in self.blocks if _EXEC_MARKER in b]
        self.assertEqual(
            len(matching),
            1,
            f"expected exactly one ```python block tagged {_EXEC_MARKER!r}",
        )
        self.addCleanup(self._forget, "company-llm")

        module = _run_block_as_module(matching[0], "docs_custom_adapter")

        # The class the reader defines really is an Adapter subclass.
        adapter_cls = module.CustomCompanyAdapter
        self.assertTrue(issubclass(adapter_cls, adapters.Adapter))

        # register_adapter taught the build the vendor name.
        self.assertIn("company-llm", adapters._VENDOR_ADAPTERS)

        # The seat in the doc resolves to that adapter through the real path, and
        # running it returns a well-formed AgentResult.
        spec = AgentSpec(name="internal-llm", vendor="company-llm", model="company-v1")
        adapter = make_adapter(spec)
        self.assertIsInstance(adapter, adapter_cls)
        result = adapter.run("review this diff")
        self.assertIsInstance(result, AgentResult)
        self.assertTrue(result.ok)
        self.assertEqual(result.vendor, "company-llm")
        self.assertIn("Checked:", result.output)

    def test_the_driver_snippet_names_a_real_api(self):
        # The second snippet drives the jury from Python; guard that the functions
        # it imports actually exist with those names.
        from ai_jury.config import load_config  # noqa: F401
        from ai_jury.orchestrator import review_diff  # noqa: F401

        driver = [b for b in self.blocks if "review_diff(" in b]
        self.assertTrue(driver, "no driver snippet calling review_diff() found")
        self.assertIn("from ai_jury.orchestrator import review_diff", driver[0])


if __name__ == "__main__":
    unittest.main()
