"""Reused native assemblies retain exact wheels and selected dependencies."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import platform
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import native_distribution as distribution
import build_native_distribution as cli
from build_tree_custody import publish_build_tree
from verify_wheelhouse import WheelhouseError


class NativeReuseTests(unittest.TestCase):
    def assembly(self, directory, *, requirement="helper[feature]>=1"):
        (directory / "wheels").mkdir(parents=True)
        definitions = {
            "workbench-core": ("0.1.0", ["workbench-api>=0.1", requirement]),
            "workbench-api": ("0.1.0", []),
            "workbench-unrelated": ("0.1.0", []),
            "helper": ("1.0", ["extra-dependency>=1; extra == 'feature'", "absent>=1; python_version < '2'"]),
            "extra-dependency": ("1.0", []),
            "pip": ("26.1.2", []),
        }
        records = []
        for name, (version, dependencies) in definitions.items():
            prefix = name.replace("-", "_") + "-" + version
            wheel = directory / "wheels" / f"{prefix}-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(prefix + ".dist-info/METADATA", f"Name: {name}\nVersion: {version}\n" + "".join(f"Requires-Dist: {dependency}\n" for dependency in dependencies))
                archive.writestr("payload.txt", name)
            records.append(distribution.wheel_record(wheel))
        records.sort(key=lambda row: row["name"])
        manifest = {"format": distribution.FORMAT, "source_sha256": "a" * 64,
                    "selected_components": ["workbench-core", "workbench-api", "workbench-unrelated"],
                    "native_versions": {name: version for name, (version, _) in definitions.items() if name.startswith("workbench-")},
                    "target": {"python": f"{sys.version_info.major}.{sys.version_info.minor}", "platform": sys.platform, "machine": platform.machine()},
                    "wheels": records, "qualified": False}
        distribution._write_assembly(directory, manifest, root=ROOT)
        return manifest

    def derive(self, source, output, *, staged=False):
        selected = (["workbench-core"], [{"id": "workbench-core", "version": "0.1.0"}, {"id": "workbench-api", "version": "0.1.0"}])
        with patch.object(distribution, "source_identity", return_value="a" * 64), patch.object(distribution, "selected_components", return_value=selected), patch.object(distribution, "_run", side_effect=AssertionError("reuse must never build/download")):
            if staged:
                return distribution._derive(source, output)
            return distribution.derive(
                source, output, configuration_home=source.parent / "core-home",
            )

    def test_reuse_selects_metadata_closure_and_preserves_every_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / "source", Path(temporary) / "core"
            original = self.assembly(source)
            result = self.derive(source, output)
            self.assertEqual({"workbench-core", "workbench-api"}, set(result["native_versions"]))
            self.assertEqual({"workbench-core", "workbench-api", "helper", "extra-dependency", "pip"}, {row["name"] for row in result["wheels"]})
            self.assertTrue(all(row in original["wheels"] for row in result["wheels"]))
            self.assertEqual(result, distribution.verify(output))
            for row in result["wheels"]:
                self.assertEqual((source / "wheels" / row["filename"]).read_bytes(), (output / "wheels" / row["filename"]).read_bytes())
            from workbench_core.storage.registered import ResourceCatalog
            catalog = ResourceCatalog(source.parent / "core-home")
            trees = catalog.inventory(workspace=ROOT)["trees"]
            self.assertEqual(
                [("native-build", str(output))],
                [(row["owner_id"], row["path"]) for row in trees],
            )
            reference = catalog.trees.describe(trees[0]["tree_id"], workspace=ROOT)
            self.assertEqual(
                "workbench-native-wheelhouse-v1:sha256:"
                + distribution._digest(output / "wheelhouse.json"),
                reference.domain_id,
            )

    def test_corruption_missing_dependency_and_wrong_version_are_rejected(self):
        for requirement in ("missing>=1", "helper>=99", "helper @ https://example.invalid/helper.whl"):
            with self.subTest(requirement=requirement), tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary) / "source"
                self.assembly(source, requirement=requirement)
                with self.assertRaisesRegex(distribution.DistributionError, "cannot satisfy"):
                    self.derive(source, Path(temporary) / "out")
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            manifest = self.assembly(source)
            (source / "wheels" / manifest["wheels"][0]["filename"]).write_bytes(b"corruption")
            with self.assertRaises(WheelhouseError):
                self.derive(source, Path(temporary) / "out")

    def test_stale_source_or_other_target_cannot_reuse_wheels(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            manifest = self.assembly(source)
            with patch.object(distribution, "source_identity", return_value="b" * 64):
                with self.assertRaisesRegex(distribution.DistributionError, "source differs"):
                    distribution.current_assembly(source)
            manifest["target"]["machine"] = "another-architecture"
            distribution._write_assembly(source, manifest, root=ROOT)
            with self.assertRaisesRegex(distribution.DistributionError, "target differs"):
                distribution.current_assembly(source)

    def test_unexpected_native_dependency_does_not_broaden_core_install(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            self.assembly(source, requirement="workbench-unrelated>=0.1")
            with self.assertRaisesRegex(distribution.DistributionError, "closure differs"):
                self.derive(source, Path(temporary) / "out")

    def test_copy_time_corruption_is_caught_and_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / "source", Path(temporary) / "out"
            self.assembly(source)
            copy = distribution.shutil.copyfile

            def corrupt(original, destination):
                result = copy(original, destination)
                Path(destination).write_bytes(b"wrong bytes")
                return result

            with patch.object(distribution.shutil, "copyfile", side_effect=corrupt):
                with self.assertRaises(WheelhouseError):
                    self.derive(source, output)
            self.assertFalse(output.exists())
            self.assertTrue(list(Path(temporary).glob(
                ".workbench-tree-*.pending/payload/wheels/*.whl"
            )))
            output.mkdir()
            retained = output / "retain.txt"
            retained.write_text("retain")
            with self.assertRaisesRegex(distribution.DistributionError, "new directory"):
                self.derive(source, output)
            self.assertEqual("retain", retained.read_text())

    def test_native_cli_custody_publishes_verified_tree_at_selected_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, output = base / "source", base / "native-output"
            self.assembly(source)
            result, reference = publish_build_tree(
                output,
                lambda staged: self.derive(source, staged, staged=True),
                lambda path, expected: self.assertEqual(expected, distribution.verify(path)),
                lambda path, _result: "workbench-native-wheelhouse-v1:sha256:" + distribution._digest(path / "wheelhouse.json"),
                owner_id="native-build",
                configuration_home=base / "core-home",
            )
            self.assertEqual(result, distribution.verify(output))
            self.assertEqual(output, reference.path)
            self.assertEqual("native-build", reference.owner_id)
            self.assertEqual("artifacts", reference.role)
            self.assertTrue(reference.domain_id.startswith("workbench-native-wheelhouse-v1:sha256:"))
            from workbench_core.storage.registered import ResourceCatalog
            rows = ResourceCatalog(base / "core-home").inventory(workspace=ROOT)["trees"]
            self.assertEqual([reference.tree_id], [row["tree_id"] for row in rows])
            (output / "requirements.lock").write_text("tampered\n")
            with self.assertRaises(WheelhouseError):
                distribution.verify(output)

    def test_native_derive_cli_uses_one_core_stage_producer(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, output = base / "source", base / "native-output"
            staged = base / "stage"
            manifest = {
                "format": distribution.FORMAT,
                "selected_components": ["workbench-core"],
                "source_sha256": "a" * 64,
                "target": {},
                "wheels": [],
            }
            reference = SimpleNamespace(tree_id="native-tree", path=output)

            def publish(selected, produce):
                self.assertEqual(output, selected)
                self.assertEqual(manifest, produce(staged))
                return manifest, reference

            printed = io.StringIO()
            with patch.object(cli, "publish_assembly", side_effect=publish), patch.object(
                cli, "_derive", return_value=manifest,
            ) as producer, redirect_stdout(printed):
                self.assertEqual(0, cli.main([
                    "--from-wheelhouse", str(source), "--output", str(output),
                    "--diagnostics", str(base / "diagnostics"),
                ]))
            producer.assert_called_once_with(source, staged, None, suite=False)
            self.assertEqual(
                {**manifest, "artifact_tree_id": reference.tree_id,
                 "artifact_path": str(reference.path)},
                json.loads(printed.getvalue()),
            )

    def test_direct_native_build_uses_core_without_running_pip(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = base / "native-output"
            forbidden_command = lambda _argv: self.fail("native process launched")
            with patch.object(distribution, "_build", side_effect=lambda staged, *_args, **_kwargs: self.assembly(staged)) as producer:
                manifest = distribution.build(
                    output,
                    command_runner=forbidden_command,
                    configuration_home=base / "core-home",
                )
            self.assertEqual(manifest, distribution.verify(output))
            self.assertNotEqual(output, producer.call_args.args[0])
            self.assertEqual("payload", producer.call_args.args[0].name)
            self.assertIs(producer.call_args.kwargs["command_runner"], forbidden_command)
            from workbench_core.storage.registered import ResourceCatalog
            catalog = ResourceCatalog(base / "core-home")
            rows = catalog.inventory(workspace=ROOT)["trees"]
            self.assertEqual(
                [("native-build", str(output))],
                [(row["owner_id"], row["path"]) for row in rows],
            )
            reference = catalog.trees.describe(rows[0]["tree_id"], workspace=ROOT)
            self.assertEqual(
                "workbench-native-wheelhouse-v1:sha256:"
                + distribution._digest(output / "wheelhouse.json"),
                reference.domain_id,
            )

    def test_native_build_cli_uses_one_core_stage_producer(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output, staged = base / "native-output", base / "stage"
            manifest = {
                "format": distribution.FORMAT, "source_sha256": "a" * 64,
                "target": {}, "wheels": [],
            }
            reference = SimpleNamespace(tree_id="native-tree", path=output)

            def publish(selected, produce):
                self.assertEqual(output, selected)
                self.assertEqual(manifest, produce(staged))
                return manifest, reference

            printed = io.StringIO()
            with patch.object(cli, "publish_assembly", side_effect=publish), patch.object(
                cli, "_build", return_value=manifest,
            ) as producer, redirect_stdout(printed):
                self.assertEqual(0, cli.main([
                    "--output", str(output), "--diagnostics", str(base / "diagnostics"),
                ]))
            self.assertEqual(staged, producer.call_args.args[0])
            self.assertIsNone(producer.call_args.args[1])
            self.assertFalse(producer.call_args.kwargs["suite"])
            self.assertTrue(callable(producer.call_args.kwargs["command_runner"]))
            self.assertEqual(
                {**manifest, "artifact_tree_id": reference.tree_id,
                 "artifact_path": str(reference.path)},
                json.loads(printed.getvalue()),
            )

    def test_failed_native_build_retains_staging_without_publishing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = base / "native-output"

            def fail(staged):
                staged.mkdir()
                (staged / "partial.txt").write_text("unqualified bytes")
                raise RuntimeError("build interrupted")

            with patch.object(distribution, "_build", side_effect=lambda staged, *_args, **_kwargs: fail(staged)):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    distribution.build(output, configuration_home=base / "core-home")
            self.assertFalse(output.exists())
            self.assertEqual(
                [b"unqualified bytes"],
                [path.read_bytes() for path in base.glob(".workbench-tree-*.pending/payload/partial.txt")],
            )

    def test_core_selects_new_default_output_each_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "source"
            self.assembly(source)
            outputs = []
            for _ in range(2):
                result, reference = publish_build_tree(
                    None,
                    lambda staged: self.derive(source, staged, staged=True),
                    lambda path, expected: self.assertEqual(expected, distribution.verify(path)),
                    lambda path, _result: "workbench-native-wheelhouse-v1:sha256:"
                    + distribution._digest(path / "wheelhouse.json"),
                    owner_id="native-build",
                    configuration_home=base / "core-home",
                    default_output_root=base / "fresh",
                )
                self.assertEqual(result, distribution.verify(reference.path))
                outputs.append(reference.path)
            self.assertEqual(2, len(set(outputs)))
            self.assertTrue(all(path.is_relative_to(base / "fresh") for path in outputs))
