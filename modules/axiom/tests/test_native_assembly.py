"""Assembly resource custody; real compiler/byte parity is a native acceptance lane."""

from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from workbench_axiom import native_assembly as assembly


class NativeAssemblyTests(unittest.TestCase):
    def test_declaring_class_mapping_preserves_concrete_receiver_admission(self):
        native = "func_77625_d(I)Lnet/minecraft/item/Item;"
        policy = {"nativeMethodMappings": {"net.minecraft.item.Item": {"setMaxStackSize": "func_77625_d"}},
                  "nativeMethods": {"biomesoplenty.common.item.ItemMudball": [native],
                                    "com.codetaylor.mc.pyrotech.modules.tech.machine.item.ItemCog": [native]}}
        raw = b"MD: net/minecraft/item/Item/func_77625_d (I)Lnet/minecraft/item/Item; net/minecraft/item/Item/setMaxStackSize (I)Lnet/minecraft/item/Item;\n"
        observed = assembly.verify_method_mappings(policy, raw)
        self.assertEqual([native], observed["methods"][0]["descriptors"])
        self.assertEqual("net.minecraft.item.Item", observed["methods"][0]["owner"])
        self.assertNotIn("net.minecraft.item.Item", policy["nativeMethods"])
        for changed in (raw.replace(b"(I)", b"(J)"), raw.replace(b"func_77625_d", b"func_wrong"),
                        raw.replace(b"net/minecraft/item/Item/func", b"unrelated/Owner/func")):
            with self.subTest(raw=changed), self.assertRaisesRegex(ValueError, "mapping differs"):
                assembly.verify_method_mappings(policy, changed)
        policy["nativeMethods"] = {}
        with self.assertRaisesRegex(ValueError, "mapping differs"):
            assembly.verify_method_mappings(policy, raw)

    def test_installed_resource_declaration_contains_the_actual_compilation_closure(self):
        root = Path(__file__).resolve().parents[1]
        package = tomllib.loads((root / "pyproject.toml").read_text())
        declared = package["tool"]["setuptools"]["package-data"]["workbench_resources.modules.axiom"]
        selected = [path.relative_to(root).as_posix()
                    for paths in assembly.source_groups().values() for path in paths]
        self.assertEqual(set(selected), set(declared))
        self.assertEqual(len(declared), len(set(declared)))
        self.assertTrue(all((root / path).is_file() for path in declared))

    def test_changed_resources_cannot_certify_the_compilation_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "module"
            root.mkdir()
            builder, host = root / "Builder.java", root / "Host.java"
            builder.write_text("class Builder {}")
            host.write_text("class Host {}")
            work = Path(temporary) / "work"
            work.mkdir()
            inputs = []

            def run(label, argv, cwd):
                inputs.append(Path(argv[-1]).read_text())
                if label == "compile-builder":
                    host.write_text("class ChangedHost {}")

            with patch.object(assembly, "source_groups", return_value={"builder": [builder], "host": [host]}), \
                    patch.object(assembly, "module_root", return_value=root):
                with self.assertRaisesRegex(ValueError, "resources changed"):
                    assembly.compile_inputs(Path(temporary) / "jdk", [], work, run)
            self.assertEqual(["class Builder {}", "class Host {}"], inputs)

    def test_original_compiler_failure_stops_before_host_compilation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "module"
            root.mkdir()
            builder, host = root / "Builder.java", root / "Host.java"
            builder.write_text("class Builder {}")
            host.write_text("class Host {}")
            work = Path(temporary) / "work"
            work.mkdir()
            failure = RuntimeError("original compiler failure")
            calls = []

            def run(label, argv, cwd):
                calls.append(label)
                raise failure

            with patch.object(assembly, "source_groups", return_value={"builder": [builder], "host": [host]}), \
                    patch.object(assembly, "module_root", return_value=root):
                with self.assertRaises(RuntimeError) as caught:
                    assembly.compile_inputs(Path(temporary) / "jdk", [], work, run)
            self.assertIs(failure, caught.exception)
            self.assertEqual(["compile-builder"], calls)
            self.assertFalse((work / "host").exists())


if __name__ == "__main__":
    unittest.main()
