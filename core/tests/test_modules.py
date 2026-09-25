from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

from workbench_api import Capability, ExecutionContext, Module, ModuleError
from workbench_core.modules import discover, dispatch
from workbench_core.module_cli import disabled_modules, _set_disabled, main, configuration_path


def entry(name="sample", *, module=None, failure=None, distribution=None):
    descriptor = module or Module(name, "0.1.0")
    def load():
        if failure is not None:
            raise failure
        return lambda: descriptor
    return SimpleNamespace(name=name, value="sample:module", load=load,
                           dist=SimpleNamespace(metadata={"Name": distribution or "workbench-" + name}, version="0.1.0"))


class ModuleAdmissionTests(unittest.TestCase):
    def test_core_without_modules(self):
        self.assertEqual((), discover(entries=[]))

    def test_bad_loader_does_not_hide_healthy_module(self):
        for failure in (ImportError("missing optional library"), RuntimeError("broken"), SystemExit(9)):
            with self.subTest(failure=type(failure).__name__):
                result = discover(entries=[entry("broken", failure=failure), entry()])
                self.assertEqual(["unavailable", "available"], [r.state for r in result])

    def test_disabled_loader_is_not_executed(self):
        result = discover(entries=[entry(failure=AssertionError())], disabled=["sample"])
        self.assertEqual("disabled", result[0].state)

    def test_metadata_identity_must_match(self):
        result = discover(entries=[entry(module=Module("different", "0.1.0"))])
        self.assertEqual("unavailable", result[0].state)

    def test_core_routes_cannot_be_shadowed(self):
        for command in ("setup", "settings", "storage", "modules", "version"):
            result = discover(entries=[entry(module=Module("sample", "0.1.0", (Capability("sample.run", (command,), "sample:run", "run"),)))])
            self.assertEqual("unavailable", result[0].state)

    def test_conflicting_module_ids_fail_closed(self):
        self.assertTrue(all(row.state == "unavailable" for row in discover(entries=[entry(), entry()])))

    def test_conflicting_commands_fail_closed(self):
        rows = [entry(name, module=Module(name,"0.1.0", (Capability(name + ".run", ("sample",), "sample:run", "run"),))) for name in ("a", "b")]
        self.assertTrue(all(row.state == "unavailable" for row in discover(entries=rows)))

    def test_missing_and_cyclic_dependencies_fail_closed(self):
        cases = [[entry(module=Module("sample", "0.1.0", requires=("missing",)))],
                 [entry("a", module=Module("a","0.1.0",requires=("b",))), entry("b",module=Module("b","0.1.0",requires=("a",)))]]
        for entries in cases:
            self.assertTrue(all(row.state == "unavailable" for row in discover(entries=entries)))

    def test_dependencies_admitted_in_order_independent_of_names(self):
        entries = [entry("a",module=Module("a","0.1.0",requires=("z",))), entry("z")]
        self.assertTrue(all(row.state == "available" for row in discover(entries=entries)))

    def test_handler_import_is_lazy_and_context_is_passed(self):
        module = Module("sample","0.1.0", (Capability("sample.run",("sample",),"sample_plugin:run","run"),))
        result = discover(entries=[entry(module=module)])
        self.assertNotIn("sample_plugin", sys.modules)
        context = ExecutionContext(Path.cwd(),Path.cwd())
        calls = []
        def run(argv, *, context):
            calls.append((argv,context))
            return 7
        with patch.dict(sys.modules, {"sample_plugin": SimpleNamespace(run=run)}):
            self.assertEqual(7, dispatch(["sample", "argument"],context,result))
        self.assertEqual([(["argument"],context)],calls)

    def test_cancelled_dispatch_never_imports_handler(self):
        module = Module("sample","0.1.0", (Capability("sample.run",("sample",),"missing_plugin:run","run"),))
        context = ExecutionContext(Path.cwd(),Path.cwd())
        context.cancelled.set()
        with self.assertRaisesRegex(ModuleError,"cancelled"):
            dispatch(["sample"],context,discover(entries=[entry(module=module)]))

    def test_invalid_handler_exit_code_rejected(self):
        module = Module("sample","0.1.0", (Capability("sample.run",("sample",),"sample_plugin:run","run"),))
        with patch.dict(sys.modules,{"sample_plugin": SimpleNamespace(run=lambda *args,**kwargs:True)}):
            with self.assertRaisesRegex(ModuleError,"exit code"):
                dispatch(["sample"],ExecutionContext(Path.cwd(),Path.cwd()),discover(entries=[entry(module=module)]))

    def test_missing_profile_does_not_fall_back_to_shorter_command(self):
        module = Module("sample", "0.1.0", (
            Capability("sample.root", ("sample",), "sample_plugin:run", "root"),
            Capability("sample.profile", ("sample", "profile"), "sample_plugin:run", "profile", requires_profiles=("missing",)),
        ))
        with patch("workbench_api.profiles.profiles", return_value=()), patch("workbench_core.modules.import_module") as importer:
            with self.assertRaisesRegex(ModuleError, "enabled, admitted profiles"):
                dispatch(["sample", "profile"], ExecutionContext(Path.cwd(), Path.cwd()), discover(entries=[entry(module=module)]))
            importer.assert_not_called()


class ModuleConfigurationTests(unittest.TestCase):
    def test_installed_core_does_not_adopt_enclosing_checkout(self):
        from workbench_core import cli
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "core").mkdir()
            (root / "core/pyproject.toml").touch()
            (root / "workbench.toml").touch()
            installed = root / ".workbench/venv/site-packages/workbench_core/cli.py"
            with patch.object(cli, "__file__", str(installed)), patch("pathlib.Path.cwd", return_value=root / "outside"):
                self.assertEqual(installed.parent, cli.source_root())

    def test_symlinked_state_root_does_not_create_outside_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "outside").mkdir()
            (root / "state").symlink_to(root / "outside", target_is_directory=True)
            with self.assertRaises(ModuleError): _set_disabled(root / "state", "sample", True)
            self.assertFalse((root / "outside/modules").exists())

    def test_enable_disable_round_trip_retains_other_modules(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual((), disabled_modules(root))
            _set_disabled(root,"sample",True)
            _set_disabled(root,"other",True)
            _set_disabled(root,"sample",False)
            self.assertEqual(("other",),disabled_modules(root))

    def test_invalid_configuration_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = configuration_path(root, "modules")
            path.parent.mkdir(parents=True)
            path.write_text('{"disabled":[],"schema_version":999}')
            with self.assertRaises(ModuleError): disabled_modules(root)

    def test_configuration_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = configuration_path(root, "modules")
            path.parent.mkdir(parents=True)
            (root / "other").write_text('{}')
            path.symlink_to(root / "other")
            with self.assertRaises(ModuleError): disabled_modules(root)


if __name__ == "__main__":
    unittest.main()
