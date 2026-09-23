"""Regression tests for runtime-scoped provider dependency identities."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml


MODULE_ROOT = Path(__file__).resolve().parents[1]
SUITE_ROOT = MODULE_ROOT.parents[1]
for source in (
    MODULE_ROOT / "src",
    SUITE_ROOT / "modules/project-intelligence/src",
    SUITE_ROOT / "modules/atlas/src",
    SUITE_ROOT / "modules/blueprints/src",
    SUITE_ROOT / "modules/crucible/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_shell.feature_studio_registry import (  # noqa: E402
    FEATURE_STUDIO_RUNTIME_DEPENDENCY_LOCK_FORMAT_V2,
    build_feature_studio_registry_v3,
)
from workbench_shell.service_control_registry import (  # noqa: E402
    SERVICE_CONTROL_RUNTIME_DEPENDENCY_LOCK_FORMAT_V2,
    ServiceControlRegistryV3Error,
    build_service_control_registry_v3,
)
from workbench_shell import (  # noqa: E402
    runtime_dependency_identity as runtime_dependency_identity,
)
from workbench_shell.world_studio_registry import (  # noqa: E402
    WORLD_STUDIO_RUNTIME_DEPENDENCY_LOCK_FORMAT_V2,
    WorldStudioRegistryV3Error,
    build_world_studio_registry_v3,
)


class RuntimeProviderDependencyIdentityV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name).resolve()
        for name in ("pixi.toml", "pixi.lock", "pyproject.toml", "api/pyproject.toml", "core/pyproject.toml", "modules/crucible/pyproject.toml", "modules/workbench-shell/pyproject.toml"):
            (cls.root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SUITE_ROOT / name, cls.root / name)
        for relative in (
            Path("api/src"),
            Path("core/src"),
            Path("modules/crucible/src"),
            Path("modules/crucible/schemas"),
            Path("modules/workbench-shell/src/workbench_shell"),
            Path("modules/workbench-shell/schemas"),
        ):
            shutil.copytree(SUITE_ROOT / relative, cls.root / relative)
        cls.lock_bytes = (cls.root / "pixi.lock").read_bytes()
        cls.pyproject_bytes = (cls.root / "core/pyproject.toml").read_bytes()
        cls.baseline = cls._identities()
        cls.source_baseline = cls._source_provider_identities()

    def setUp(self) -> None:
        lock_path = self.root / "pixi.lock"
        if lock_path.is_symlink():
            lock_path.unlink()
        lock_path.write_bytes(self.lock_bytes)
        (self.root / "core/pyproject.toml").write_bytes(self.pyproject_bytes)

    @classmethod
    def _bundles(cls):
        return {
            "service-control": build_service_control_registry_v3(cls.root),
            "feature-studio": build_feature_studio_registry_v3(cls.root),
            "world-studio": build_world_studio_registry_v3(cls.root),
        }

    @classmethod
    def _identities(cls):
        provider_keys = {
            "service-control": "workbench.provider.crucible.service-control",
            "feature-studio": "workbench.provider.shell.feature-studio",
            "world-studio": "workbench.provider.world-studio",
        }
        result = {}
        for name, bundle in cls._bundles().items():
            provider = next(
                row
                for row in bundle.registry["providers"]
                if row["provider_key"] == provider_keys[name]
            )
            result[name] = (
                bundle.dependency_lock_manifest["id"],
                provider["provider_id"],
            )
        return result

    @classmethod
    def _source_provider_identities(cls):
        provider_keys = {
            "service-control": "workbench.provider.crucible.service-control",
            "world-studio": "workbench.provider.world-studio",
        }
        result = {}
        bundles = cls._bundles()
        for name in ("service-control", "world-studio"):
            bundle = bundles[name]
            provider = next(
                row
                for row in bundle.registry["providers"]
                if row["provider_key"] == provider_keys[name]
            )
            result[name] = (
                bundle.source_tree_manifest["id"],
                provider["provider_id"],
            )
        return result

    def _load_lock(self) -> dict:
        value = yaml.safe_load((self.root / "pixi.lock").read_text(encoding="utf-8"))
        self.assertIs(type(value), dict)
        return value

    def _write_lock(self, value: dict) -> None:
        (self.root / "pixi.lock").write_text(
            yaml.safe_dump(value, sort_keys=False), encoding="utf-8"
        )

    def test_producers_emit_host_independent_v2_contracts(self) -> None:
        self.assertFalse((self.root / "requirements-dev.lock").exists())
        expected_formats = {
            "service-control": SERVICE_CONTROL_RUNTIME_DEPENDENCY_LOCK_FORMAT_V2,
            "feature-studio": FEATURE_STUDIO_RUNTIME_DEPENDENCY_LOCK_FORMAT_V2,
            "world-studio": WORLD_STUDIO_RUNTIME_DEPENDENCY_LOCK_FORMAT_V2,
        }
        expected_platforms = {
            "linux-arm64",
            "linux-x86-64",
            "macos-arm64",
            "macos-x86-64",
            "windows-arm64",
            "windows-x86-64",
        }
        for name, bundle in self._bundles().items():
            manifest = bundle.dependency_lock_manifest
            self.assertEqual(expected_formats[name], manifest["format"])
            self.assertEqual(2, manifest["schema_version"])
            self.assertNotIn("files", manifest)
            contract = manifest["source_runtime_contract"]
            self.assertEqual("default", contract["environment"])
            self.assertEqual(
                "supported-source-runtime", contract["execution_scope"]
            )
            self.assertEqual(
                "all-declared-targets", contract["host_platform_selection"]
            )
            self.assertEqual(
                "external-distribution-receipt-required",
                contract["packaged_materialization_evidence"],
            )
            self.assertEqual(
                expected_platforms,
                {
                    closure["platform"]["name"]
                    for closure in contract["target_closures"]
                },
            )

    def test_release_and_workspace_rows_do_not_rotate_provider_ids(self) -> None:
        lock = self._load_lock()
        lock["environments"]["release"]["channels"] = [
            {"url": "https://release-only.invalid/"}
        ]
        lock["environments"]["workspace"]["indexes"] = [
            "https://workspace-only.invalid/simple"
        ]
        self._write_lock(lock)
        self.assertEqual(self.baseline, self._identities())

    def test_selected_default_package_rotates_provider_ids(self) -> None:
        lock = self._load_lock()
        package_refs = next(
            iter(lock["environments"]["default"]["packages"].values())
        )
        selected_reference = package_refs[0]
        ecosystem, location = next(iter(selected_reference.items()))
        selected_package = next(
            row
            for row in lock["packages"]
            if row.get(ecosystem) == location
        )
        selected_package["sha256"] = (
            "e" * 64 if selected_package["sha256"] == "f" * 64 else "f" * 64
        )
        self._write_lock(lock)
        changed = self._identities()
        for name in self.baseline:
            self.assertNotEqual(self.baseline[name][0], changed[name][0])
            self.assertNotEqual(self.baseline[name][1], changed[name][1])

    def test_project_runtime_contract_rotates_provider_ids(self) -> None:
        path = self.root / "core/pyproject.toml"
        value = path.read_text(encoding="utf-8")
        changed_value = value.replace(
            'packaging>=', 'packaging!=99,>=', 1
        )
        self.assertNotEqual(value, changed_value)
        path.write_text(changed_value, encoding="utf-8")
        changed = self._identities()
        for name in self.baseline:
            self.assertNotEqual(self.baseline[name][0], changed[name][0])
            self.assertNotEqual(self.baseline[name][1], changed[name][1])

    def test_unrelated_crucible_files_do_not_rotate_current_registries(self) -> None:
        source_path = (
            self.root
            / "modules/crucible/src/workbench_crucible_kernel/"
            "unrelated_identity_probe.py"
        )
        schema_path = (
            self.root
            / "modules/crucible/schemas/"
            "unrelated-identity-probe-v1.schema.json"
        )
        source_path.write_text("UNRELATED = True\n", encoding="utf-8")
        schema_path.write_text(
            '{"$id":"workbench://schemas/crucible/'
            'unrelated-identity-probe-v1.schema.json",'
            '"$schema":"https://json-schema.org/draft/2020-12/schema",'
            '"type":"object"}\n',
            encoding="utf-8",
        )
        try:
            self.assertEqual(
                self.source_baseline,
                self._source_provider_identities(),
            )
        finally:
            source_path.unlink()
            schema_path.unlink()

    def test_worldgen_facade_loads_synthetic_graph_only_on_demand(self) -> None:
        source = SUITE_ROOT / "modules/crucible/src"
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    f"sys.path.insert(0, {str(source)!r}); "
                    f"sys.path.insert(0, {str(SUITE_ROOT / 'api/src')!r}); "
                    "import workbench_crucible_worldgen as package; "
                    "assert 'workbench_crucible_worldgen.graph' not in sys.modules; "
                    "assert 'workbench_crucible.synthetic' not in sys.modules; "
                    "assert package.WorldStudioProvingViewHandler; "
                    "assert 'workbench_crucible_worldgen.graph' not in sys.modules; "
                    "assert 'workbench_crucible.synthetic' not in sys.modules; "
                    "assert package.WorldgenGraphStore; "
                    "assert 'workbench_crucible_worldgen.graph' in sys.modules; "
                    "assert 'workbench_crucible.synthetic' in sys.modules"
                ),
            ],
            cwd="/tmp",
            env={"PYTHONIOENCODING": "utf-8"},
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_selected_implementation_rotates_only_its_distribution(self) -> None:
        cases = (
            (
                "service-control",
                "world-studio",
                self.root
                / "modules/workbench-shell/src/workbench_shell/"
                "service_control_registry.py",
            ),
            (
                "world-studio",
                "service-control",
                self.root
                / "modules/crucible/src/"
                "workbench_crucible_worldgen/view.py",
            ),
        )
        for selected, unrelated, path in cases:
            with self.subTest(distribution=selected):
                original = path.read_bytes()
                path.write_bytes(original + b"\n# source-identity regression probe\n")
                try:
                    changed = self._source_provider_identities()
                    self.assertNotEqual(
                        self.source_baseline[selected][0], changed[selected][0]
                    )
                    self.assertNotEqual(
                        self.source_baseline[selected][1], changed[selected][1]
                    )
                    self.assertEqual(
                        self.source_baseline[unrelated], changed[unrelated]
                    )
                finally:
                    path.write_bytes(original)

    def test_selected_implementation_files_are_required(self) -> None:
        cases = (
            (
                build_service_control_registry_v3,
                ServiceControlRegistryV3Error,
                self.root
                / "modules/crucible/src/"
                "workbench_crucible_service/service.py",
            ),
            (
                build_world_studio_registry_v3,
                WorldStudioRegistryV3Error,
                self.root
                / "modules/crucible/src/"
                "workbench_crucible_worldgen/view.py",
            ),
        )
        for builder, error_type, path in cases:
            with self.subTest(builder=builder.__name__):
                original = path.read_bytes()
                path.unlink()
                try:
                    with self.assertRaisesRegex(error_type, "unsafe or absent"):
                        builder(self.root)
                finally:
                    path.write_bytes(original)

    def test_dependency_identity_uses_one_bound_snapshot_if_lock_changes(self) -> None:
        lock_path = self.root / "pixi.lock"
        real_bind = runtime_dependency_identity.bind_pixi_inputs

        def bind_then_mutate(manifest: Path, lock: Path):
            inputs = real_bind(manifest, lock)
            lock.write_bytes(inputs.lock_bytes + b"\n")
            return inputs

        with patch.object(
            runtime_dependency_identity,
            "bind_pixi_inputs",
            side_effect=bind_then_mutate,
        ):
            bundle = build_service_control_registry_v3(self.root)
        self.assertEqual(
            self.baseline["service-control"][0],
            bundle.dependency_lock_manifest["id"],
        )
        self.assertNotEqual(
            self.lock_bytes,
            lock_path.read_bytes(),
        )

    def test_service_control_rejects_indirect_or_duplicate_lock_inputs(self) -> None:
        lock_path = self.root / "pixi.lock"
        target = self.root / "indirect-pixi.lock"
        target.write_bytes(self.lock_bytes)
        lock_path.unlink()
        lock_path.symlink_to(target)
        with self.assertRaisesRegex(
            ServiceControlRegistryV3Error,
            "not one bounded ordinary file",
        ):
            build_service_control_registry_v3(self.root)

        lock_path.unlink()
        lock_path.write_bytes(b"version: 7\nversion: 8\n")
        with self.assertRaisesRegex(
            ServiceControlRegistryV3Error,
            "duplicate mapping key",
        ):
            build_service_control_registry_v3(self.root)


if __name__ == "__main__":
    unittest.main()
