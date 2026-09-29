"""A test that registers an adapter leaves every registry as it found it (#904).

`register_adapter` writes three tables in two modules: the adapter classes in
`adapters`, and the registered vendor names and transports in `config`. Tests
put back the ones they remembered. `test_adapters_paths` put back none, so its
`custom-provider` failed two vocabulary checks in `test_adapter_key` — but only
when the modules ran in that order, which discovery never does:

    python -m unittest tests.test_vendor_vocabulary tests.test_adapters_paths \\
        tests.test_adapter_key

The fix is one snapshot of all three tables (`adapters._registry_state`) that
every registering test restores. This module makes the next leak fail loudly: it
runs every test module that writes a registry, in the order #904 was reported
failing first, and names each test that ends with the tables different from how
it started them.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_jury import adapters  # noqa: E402
from ai_jury import config as config_module  # noqa: E402

TESTS = Path(__file__).resolve().parent

#: A module that names a registry, a writer or the snapshot is run by the guard.
#: Named at all, not only written: a module can register without a writer in its
#: own source (the docs snippet test registers through the snippet it runs), and
#: a module that only reads a table is the one a leak makes fail, so it runs
#: after the writers the way it would in a real run. Measured at under 1.5 s.
_TOUCHES = re.compile(
    r"\b(?:register_adapter|register_vendor|_registry_state"
    r"|_VENDOR_ADAPTERS|_REGISTERED_VENDORS|_REGISTERED_ADAPTER_SPAWNS)\b"
)

#: The order #904 was reported failing in; these run first.
_REPORTED_ORDER = ("test_vendor_vocabulary", "test_adapters_paths", "test_adapter_key")

#: The names of the tables in `adapters._registry_state()`, in its order.
_TABLES = (
    "adapters._VENDOR_ADAPTERS",
    "config._REGISTERED_VENDORS",
    "config._REGISTERED_ADAPTER_SPAWNS",
)


def _registry_modules() -> list[str]:
    """Every test module that names a registry, the reported order first."""
    found = {
        path.stem
        for path in TESTS.glob("test_*.py")
        if path.name != Path(__file__).name and _TOUCHES.search(path.read_text(encoding="utf-8"))
    }
    return [
        *(name for name in _REPORTED_ORDER if name in found),
        *sorted(found - set(_REPORTED_ORDER)),
    ]


def _describe(before: tuple, after: tuple) -> str:
    """Which table changed, and which keys were added, removed or rebound."""
    parts = []
    for name, old, new in zip(_TABLES, before, after, strict=True):
        if old == new:
            continue
        changed = sorted(
            k for k in set(old) & set(new) if isinstance(old, dict) and old[k] != new[k]
        )
        parts.append(
            f"{name}: added {sorted(set(new) - set(old))}, removed {sorted(set(old) - set(new))},"
            f" changed {changed}"
        )
    return "; ".join(parts)


class _LeakRecorder(unittest.TestResult):
    """A result that compares the registries before and after every test.

    Per test rather than per run, so the report names the test that leaked
    instead of the one that tripped over it later. It does not put the tables
    back: the tests that run next see the leak exactly as they would in a real run.
    """

    def __init__(self) -> None:
        super().__init__()
        self.leaks: list[str] = []
        self._before: tuple = ()

    def startTest(self, test) -> None:  # noqa: N802 - unittest's name
        super().startTest(test)
        self._before = adapters._registry_state()

    def stopTest(self, test) -> None:  # noqa: N802 - unittest's name
        after = adapters._registry_state()
        if after != self._before:
            self.leaks.append(f"{test.id()} -> {_describe(self._before, after)}")
        super().stopTest(test)


def _run(suite: unittest.TestSuite) -> _LeakRecorder:
    result = _LeakRecorder()
    suite.run(result)
    return result


class RegisteringTestsRestoreEveryRegistry(unittest.TestCase):
    def setUp(self):
        # The guard itself leaves nothing behind, even when a module it runs does.
        self.addCleanup(adapters._restore_registry_state, adapters._registry_state())

    def _load(self, stem: str):
        """A fresh copy of ``tests/<stem>.py``, whatever name this run imported it by."""
        name = f"_registry_isolation_{stem}"
        spec = importlib.util.spec_from_file_location(name, TESTS / f"{stem}.py")
        module = importlib.util.module_from_spec(spec)
        # In `sys.modules` while it runs: the suite finds module fixtures there.
        sys.modules[name] = module
        self.addCleanup(sys.modules.pop, name, None)
        spec.loader.exec_module(module)
        return module

    def test_the_scan_finds_the_modules_that_register(self):
        """Vacuity guard: a scan that finds nothing would pass the test below."""
        modules = _registry_modules()
        self.assertEqual(tuple(modules[:3]), _REPORTED_ORDER)
        self.assertLessEqual({"test_privilege", "test_docs_python_snippets"}, set(modules), modules)

    def test_every_registering_test_restores_every_table(self):
        start = adapters._registry_state()
        loader = unittest.TestLoader()
        suite = unittest.TestSuite(
            loader.loadTestsFromModule(self._load(stem)) for stem in _registry_modules()
        )
        result = _run(suite)
        self.assertGreater(result.testsRun, 0)
        self.assertEqual(result.leaks, [], "these tests left a registry changed")
        broken = [f"{test.id()}: {trace.splitlines()[-1]}" for test, trace in result.failures]
        broken += [f"{test.id()}: {trace.splitlines()[-1]}" for test, trace in result.errors]
        self.assertEqual(broken, [], "the registering modules fail when run in this order")
        # A class or module fixture that registers and never restores is not
        # inside any one test; the whole run must still end where it began.
        self.assertEqual(
            adapters._registry_state(), start, _describe(start, adapters._registry_state())
        )


class TheGuardCatchesALeak(unittest.TestCase):
    """The recorder is what the guard above rests on, so it is tested directly."""

    def setUp(self):
        self.addCleanup(adapters._restore_registry_state, adapters._registry_state())

    def test_a_test_that_registers_and_forgets_is_named(self):
        class Leaks(unittest.TestCase):
            def test_forgets(self):
                adapters.register_adapter("leak-904", adapters.GenericCLIAdapter)

        result = _run(unittest.TestSuite([Leaks("test_forgets")]))
        self.assertEqual(len(result.leaks), 1, result.leaks)
        self.assertIn("Leaks.test_forgets", result.leaks[0])
        # All three tables, by name — the transport table is the one tests forgot.
        for table in _TABLES:
            self.assertIn(f"{table}: added ['leak-904']", result.leaks[0])

    def test_a_rebound_built_in_is_named_as_changed(self):
        class Rebinds(unittest.TestCase):
            def test_rebinds(self):
                adapters.register_adapter("cli", adapters.GenericOpenAICompatibleAdapter)

        result = _run(unittest.TestSuite([Rebinds("test_rebinds")]))
        self.assertEqual(len(result.leaks), 1, result.leaks)
        self.assertIn(
            "adapters._VENDOR_ADAPTERS: added [], removed [], changed ['cli']", result.leaks[0]
        )

    def test_a_test_that_restores_is_not_named(self):
        class Restores(unittest.TestCase):
            def test_restores(self):
                self.addCleanup(adapters._restore_registry_state, adapters._registry_state())
                adapters.register_adapter("leak-904", adapters.GenericCLIAdapter)

        result = _run(unittest.TestSuite([Restores("test_restores")]))
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(result.leaks, [])


class TheSnapshotCoversEveryTable(unittest.TestCase):
    def setUp(self):
        self.addCleanup(adapters._restore_registry_state, adapters._registry_state())

    def test_restoring_puts_back_all_three_tables(self):
        state = adapters._registry_state()
        adapters.register_adapter("snapshot-904", adapters.GenericCLIAdapter)
        adapters.register_adapter("cli", adapters.GenericOpenAICompatibleAdapter)
        adapters._restore_registry_state(state)
        self.assertNotIn("snapshot-904", adapters._VENDOR_ADAPTERS)
        self.assertNotIn("snapshot-904", config_module._REGISTERED_VENDORS)
        self.assertNotIn("snapshot-904", config_module._REGISTERED_ADAPTER_SPAWNS)
        self.assertIs(adapters._VENDOR_ADAPTERS["cli"], adapters.GenericCLIAdapter)
        self.assertEqual(adapters._registry_state(), state)

    def test_restoring_keeps_the_tables_other_modules_hold(self):
        """In place: `from ai_jury.adapters import _VENDOR_ADAPTERS` must still see it."""
        held = (
            adapters._VENDOR_ADAPTERS,
            config_module._REGISTERED_VENDORS,
            config_module._REGISTERED_ADAPTER_SPAWNS,
        )
        state = adapters._registry_state()
        adapters.register_adapter("snapshot-904", adapters.GenericCLIAdapter)
        adapters._restore_registry_state(state)
        self.assertIs(adapters._VENDOR_ADAPTERS, held[0])
        self.assertIs(config_module._REGISTERED_VENDORS, held[1])
        self.assertIs(config_module._REGISTERED_ADAPTER_SPAWNS, held[2])

    def test_the_snapshot_is_a_copy(self):
        state = adapters._registry_state()
        adapters.register_adapter("snapshot-904", adapters.GenericCLIAdapter)
        for name, table in zip(_TABLES, state, strict=True):
            with self.subTest(table=name):
                self.assertNotIn("snapshot-904", table)


if __name__ == "__main__":
    unittest.main()
