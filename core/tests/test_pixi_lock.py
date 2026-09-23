"""Target-scoped Pixi closure contract tests."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
import yaml


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parent
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_core.pixi_lock import (  # noqa: E402
    PixiLockError,
    bind_pixi_inputs,
    build_environment_closure,
    build_environment_closure_from_bound_inputs,
    build_materialization_binding,
    build_materialization_binding_from_bound_inputs,
    feature_dependency_version,
    parse_pixi_lock_bytes,
    required_pixi_version,
)
import workbench_core.pixi_lock as pixi_lock_module  # noqa: E402


class PixiLockTests(unittest.TestCase):
    def _validate(self, closure: dict, binding: dict | None = None) -> None:
        schema_root = Path(pixi_lock_module.__file__).parent / "schemas"
        closure_schema = json.loads(
            (schema_root / "workbench-pixi-environment-closure-v1.schema.json")
            .read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(closure_schema)
        Draft202012Validator(closure_schema).validate(closure)
        if binding is not None:
            binding_schema = json.loads(
                (schema_root / "workbench-pixi-materialization-binding-v1.schema.json")
                .read_text(encoding="utf-8")
            )
            Draft202012Validator.check_schema(binding_schema)
            registry = Registry().with_resource(
                "workbench-pixi-environment-closure-v1.schema.json",
                Resource.from_contents(closure_schema),
            )
            Draft202012Validator(binding_schema, registry=registry).validate(binding)

    def test_repository_native_closure_is_exact_and_target_scoped(self) -> None:
        closure = build_environment_closure(
            REPOSITORY_ROOT / "pixi.toml",
            REPOSITORY_ROOT / "pixi.lock",
            environment="compatibility",
            platform="linux-x86-64",
        )
        self.assertEqual("workbench-pixi-environment-closure-v1", closure["format"])
        self.assertEqual("linux-64", closure["platform"]["subdir"])
        self.assertEqual("compatibility", closure["environment"])
        self.assertTrue(closure["packages"])
        self.assertEqual(
            len(closure["packages"]),
            len({row["location"] for row in closure["packages"]}),
        )
        self.assertTrue(
            any(
                row["name"] == "python" and row["version"] == "3.13.14"
                for row in closure["packages"]
            )
        )
        self.assertFalse(any(row["name"] == "pixi-pack" for row in closure["packages"]))
        self._validate(closure)

    def test_unrelated_environment_change_does_not_change_closure_id(self) -> None:
        manifest = (REPOSITORY_ROOT / "pixi.toml").read_bytes()
        lock = yaml.safe_load((REPOSITORY_ROOT / "pixi.lock").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "pixi.toml"
            lock_path = root / "pixi.lock"
            manifest_path.write_bytes(manifest)
            lock_path.write_text(yaml.safe_dump(lock, sort_keys=False), encoding="utf-8")
            before = build_environment_closure(
                manifest_path,
                lock_path,
                environment="compatibility",
                platform="linux-x86-64",
            )
            changed = deepcopy(lock)
            changed["environments"]["release"]["indexes"].append(
                "https://example.invalid/simple"
            )
            lock_path.write_text(yaml.safe_dump(changed, sort_keys=False), encoding="utf-8")
            after = build_environment_closure(
                manifest_path,
                lock_path,
                environment="compatibility",
                platform="linux-x86-64",
            )
            self.assertEqual(before["closure_id"], after["closure_id"])
            self.assertNotEqual(
                hashlib.sha256(yaml.safe_dump(lock).encode()).hexdigest(),
                hashlib.sha256(yaml.safe_dump(changed).encode()).hexdigest(),
            )

    def test_binding_changes_when_exact_source_bytes_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "pixi.toml"
            lock_path = root / "pixi.lock"
            manifest_path.write_bytes((REPOSITORY_ROOT / "pixi.toml").read_bytes())
            lock_path.write_bytes((REPOSITORY_ROOT / "pixi.lock").read_bytes())
            before = build_materialization_binding(
                manifest_path,
                lock_path,
                environment="default",
                platform="windows-arm64",
            )
            manifest_path.write_bytes(manifest_path.read_bytes() + b"\n")
            after = build_materialization_binding(
                manifest_path,
                lock_path,
                environment="default",
                platform="windows-arm64",
            )
            self.assertEqual(before["closure"]["closure_id"], after["closure"]["closure_id"])
            self.assertNotEqual(before["binding_id"], after["binding_id"])
            self._validate(before["closure"], before)

    def test_bound_inputs_prevent_post_read_path_mutation_from_mixing_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "pixi.toml"
            lock_path = root / "pixi.lock"
            manifest_raw = (REPOSITORY_ROOT / "pixi.toml").read_bytes()
            lock_raw = (REPOSITORY_ROOT / "pixi.lock").read_bytes()
            manifest_path.write_bytes(manifest_raw)
            lock_path.write_bytes(lock_raw)

            inputs = bind_pixi_inputs(manifest_path, lock_path)
            expected_closure = build_environment_closure_from_bound_inputs(
                inputs,
                environment="compatibility",
                platform="linux-x86-64",
            )
            manifest_path.write_bytes(manifest_raw + b"\n")
            lock_path.write_bytes(lock_raw + b"\n")

            binding = build_materialization_binding_from_bound_inputs(
                inputs,
                environment="compatibility",
                platform="linux-x86-64",
            )
            self.assertEqual(expected_closure, binding["closure"])
            self.assertEqual(
                hashlib.sha256(manifest_raw).hexdigest(),
                binding["manifest"]["sha256"],
            )
            self.assertEqual(
                hashlib.sha256(lock_raw).hexdigest(),
                binding["lock"]["sha256"],
            )
            fresh = build_materialization_binding(
                manifest_path,
                lock_path,
                environment="compatibility",
                platform="linux-x86-64",
            )
            self.assertEqual(expected_closure, fresh["closure"])
            self.assertNotEqual(binding["binding_id"], fresh["binding_id"])

    @unittest.skipIf(os.name == "nt", "open-file replacement is platform-specific")
    def test_bound_reader_rejects_path_replacement_during_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "pixi.toml"
            lock_path = root / "pixi.lock"
            manifest_path.write_bytes((REPOSITORY_ROOT / "pixi.toml").read_bytes())
            lock_raw = (REPOSITORY_ROOT / "pixi.lock").read_bytes()
            lock_path.write_bytes(lock_raw)
            replacement = root / "replacement.lock"
            replacement.write_bytes(lock_raw + b"\n")
            real_read = os.read
            replaced = False

            def replace_after_first_lock_read(descriptor: int, size: int) -> bytes:
                nonlocal replaced
                raw = real_read(descriptor, size)
                if not replaced and os.fstat(descriptor).st_size == len(lock_raw):
                    os.replace(replacement, lock_path)
                    replaced = True
                return raw

            with (
                patch.object(
                    pixi_lock_module.os,
                    "read",
                    side_effect=replace_after_first_lock_read,
                ),
                self.assertRaisesRegex(PixiLockError, "changed while it was read"),
            ):
                bind_pixi_inputs(manifest_path, lock_path)
            self.assertTrue(replaced)

    def test_symlinked_inputs_and_duplicate_yaml_keys_fail_closed(self) -> None:
        with self.assertRaisesRegex(PixiLockError, "duplicate mapping key"):
            parse_pixi_lock_bytes(b"version: 7\nversion: 8\n")

        for indirect in ("manifest", "lock"):
            with self.subTest(indirect=indirect), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                manifest_path = root / "pixi.toml"
                lock_path = root / "pixi.lock"
                manifest_source = REPOSITORY_ROOT / "pixi.toml"
                lock_source = REPOSITORY_ROOT / "pixi.lock"
                if indirect == "manifest":
                    manifest_path.symlink_to(manifest_source)
                    lock_path.write_bytes(lock_source.read_bytes())
                else:
                    manifest_path.write_bytes(manifest_source.read_bytes())
                    lock_path.symlink_to(lock_source)
                with self.assertRaisesRegex(
                    PixiLockError,
                    "not one bounded ordinary file",
                ):
                    bind_pixi_inputs(manifest_path, lock_path)

    def test_environment_platform_membership_fails_closed(self) -> None:
        with self.assertRaisesRegex(PixiLockError, "has no closure"):
            build_environment_closure(
                REPOSITORY_ROOT / "pixi.toml",
                REPOSITORY_ROOT / "pixi.lock",
                environment="compatibility",
                platform="windows-arm64",
            )

    def test_exact_pins_are_exposed_from_manifest(self) -> None:
        manifest = REPOSITORY_ROOT / "pixi.toml"
        self.assertEqual("0.75.0", required_pixi_version(manifest))
        self.assertEqual(
            "26.1.2",
            feature_dependency_version(
                manifest, feature="release-tools", dependency="pip"
            ),
        )

    def test_closure_is_json_round_trip_safe(self) -> None:
        closure = build_environment_closure(
            REPOSITORY_ROOT / "pixi.toml",
            REPOSITORY_ROOT / "pixi.lock",
            environment="default",
            platform="macos-arm64",
        )
        self.assertEqual(closure, json.loads(json.dumps(closure)))


if __name__ == "__main__":
    unittest.main()
