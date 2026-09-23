from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_core.storage import (  # noqa: E402
    RuntimeManagerError,
    inventory_storage,
    validate_managed_runtime,
    validate_storage_inventory,
)
import workbench_core.storage.manager as manager  # noqa: E402


INVENTORY_PREFIX = "workbench-storage-inventory:sha256:"
ITEM_PREFIX = "workbench-storage-item:sha256:"
COMPONENT_PREFIX = "workbench-storage-component:sha256:"
RUNTIME_PREFIX = "workbench-managed-runtime:sha256:"
NOW = "2026-08-04T21:00:00Z"


def _identity(prefix: str, value: object) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return prefix + hashlib.sha256(canonical).hexdigest()


def _resign_inventory(report: dict[str, object]) -> dict[str, object]:
    material = dict(report)
    material.pop("inventory_id", None)
    report["inventory_id"] = _identity(INVENTORY_PREFIX, material)
    return report


def _resign_runtime(manifest: dict[str, object]) -> dict[str, object]:
    material = dict(manifest)
    material.pop("runtime_id", None)
    manifest["runtime_id"] = _identity(RUNTIME_PREFIX, material)
    return manifest


class RuntimeManagerSemanticValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.storage = self.root / ".workbench"
        alpha = self.storage / "fixtures/alpha"
        beta = self.storage / "fixtures/beta"
        (alpha / "world").mkdir(parents=True)
        (alpha / "world/level.dat").write_bytes(b"level")
        beta.mkdir(parents=True)
        (beta / "payload.bin").write_bytes(b"payload")
        self.inventory = inventory_storage(self.root, now=NOW)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _items(self, report: dict[str, object]) -> list[dict[str, object]]:
        return report["items"]  # type: ignore[return-value]

    def _item(self, report: dict[str, object], suffix: str) -> dict[str, object]:
        return next(
            item
            for item in self._items(report)
            if str(item["relative_path"]).endswith(suffix)
        )

    def _managed_runtime(self) -> dict[str, object]:
        manifest: dict[str, object] = {
            "format": "workbench-managed-runtime-v1",
            "schema_version": 1,
            "canonicalization_id": "workbench-canonical-json-v1",
            "state": "ready",
            "created_at": NOW,
            "last_used_at": None,
            "workspace_root": str(self.root),
            "storage_root": str(self.storage),
            "relative_path": ".workbench/fixtures/managed-runtime--fixture--valid",
            "producer": {
                "producer_id": "workbench-crucible-runtime-manager-v1",
                "implementation_path": str(Path(manager.__file__).resolve()),
                "implementation_sha256": "1" * 64,
            },
            "profile": {
                "pack": "fixture",
                "profile_id": "workbench-pack:fixture:worldgen-v1",
                "profile_path": str(self.root / "profiles/packs/fixture/profile.json"),
                "profile_file_sha256": "2" * 64,
                "profile_canonical_sha256": "3" * 64,
                "platform_profile_id": "workbench-platform:cleanroom:test",
                "minecraft_version": "1.12.2",
                "cleanroom_version": "0.6.8-alpha",
                "side": "dedicated-server",
            },
            "source_template": {
                "path": str(self.storage / "templates/runtime-template"),
                "inventory_sha256": "4" * 64,
                "server_jar": {
                    "relative_path": "cleanroom-server.jar",
                    "size_bytes": 12,
                    "sha256": "5" * 64,
                },
                "audit_safe": True,
            },
            "world": {
                "level_name": "world",
                "relative_path": "world",
                "state": "reserved-absent",
                "seed": 42,
                "world_type": "DEFAULT",
            },
            "content": {
                "tree_observation_sha256": "6" * 64,
                "logical_bytes": 12,
                "unique_allocated_bytes": 4096,
                "file_count": 1,
                "directory_count": 1,
                "symlink_count": 0,
                "independent_copy": True,
            },
            "references": [
                {
                    "relation": "copied-from",
                    "resource_id": None,
                    "path": str(self.storage / "templates/runtime-template"),
                    "sha256": "4" * 64,
                    "required": True,
                }
            ],
            "limitations": [
                "A reserved-absent world is not a generated world."
            ],
        }
        return _resign_runtime(manifest)

    def test_generated_inventory_and_managed_runtime_are_semantically_valid(self) -> None:
        validate_storage_inventory(self.inventory)
        validate_managed_runtime(self._managed_runtime())

    def test_runtime_manager_schemas_are_valid_draft_2020_12(self) -> None:
        schema_root = Path(manager.__file__).parent / "schemas"
        for name in (
            "storage-inventory-v1",
            "managed-runtime-v1",
            "world-snapshot-v1",
            "storage-operation-plan-v1",
            "storage-operation-receipt-v1",
        ):
            with self.subTest(schema=name):
                schema = json.loads(
                    (schema_root / f"{name}.schema.json").read_text(encoding="utf-8")
                )
                Draft202012Validator.check_schema(schema)

    def test_inventory_rejects_order_duplicate_paths_and_rebound_item_ids(self) -> None:
        reversed_report = deepcopy(self.inventory)
        self._items(reversed_report).reverse()
        _resign_inventory(reversed_report)
        with self.assertRaises(RuntimeManagerError):
            validate_storage_inventory(reversed_report)

        duplicate_path = deepcopy(self.inventory)
        first, second = self._items(duplicate_path)
        second["relative_path"] = first["relative_path"]
        second["path"] = first["path"]
        second["item_id"] = first["item_id"]
        _resign_inventory(duplicate_path)
        with self.assertRaises(RuntimeManagerError):
            validate_storage_inventory(duplicate_path)

        rebound_id = deepcopy(self.inventory)
        self._items(rebound_id)[0]["item_id"] = ITEM_PREFIX + "0" * 64
        _resign_inventory(rebound_id)
        with self.assertRaises(RuntimeManagerError):
            validate_storage_inventory(rebound_id)

    def test_inventory_rejects_component_identity_and_containment_forgery(self) -> None:
        invalid_id = deepcopy(self.inventory)
        alpha = self._item(invalid_id, "fixtures/alpha")
        component = alpha["components"][0]  # type: ignore[index]
        component["component_id"] = COMPONENT_PREFIX + "0" * 64
        _resign_inventory(invalid_id)
        with self.assertRaises(RuntimeManagerError):
            validate_storage_inventory(invalid_id)

        escaped = deepcopy(self.inventory)
        alpha = self._item(escaped, "fixtures/alpha")
        component = alpha["components"][0]  # type: ignore[index]
        component["relative_path"] = ".workbench/fixtures/beta/world"
        component["component_id"] = _identity(
            COMPONENT_PREFIX,
            {
                "storage_root": str(self.storage),
                "relative_path": component["relative_path"],
            },
        )
        _resign_inventory(escaped)
        with self.assertRaises(RuntimeManagerError):
            validate_storage_inventory(escaped)

    def test_inventory_rejects_inconsistent_aggregate_and_problem_totals(self) -> None:
        mutations = (
            lambda report: report["totals"].__setitem__(  # type: ignore[union-attr]
                "logical_bytes", report["totals"]["logical_bytes"] + 1  # type: ignore[index,operator]
            ),
            lambda report: report["totals"].__setitem__(  # type: ignore[union-attr]
                "eligible_bytes", report["totals"]["eligible_bytes"] + 1  # type: ignore[index,operator]
            ),
            lambda report: report["totals"].__setitem__(  # type: ignore[union-attr]
                "unique_allocated_bytes",
                sum(
                    item["size"]["unique_allocated_bytes"]  # type: ignore[index]
                    for item in report["items"]  # type: ignore[union-attr]
                )
                + 1,
            ),
            lambda report: report["scan"].__setitem__(  # type: ignore[union-attr]
                "problem_count", report["scan"]["problem_count"] + 1  # type: ignore[index,operator]
            ),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                report = deepcopy(self.inventory)
                mutation(report)
                _resign_inventory(report)
                with self.assertRaises(RuntimeManagerError):
                    validate_storage_inventory(report)

    def test_inventory_requires_reference_closure_and_exact_backlinks(self) -> None:
        referenced = deepcopy(self.inventory)
        source = self._item(referenced, "fixtures/alpha")
        target = self._item(referenced, "fixtures/beta")
        source["references"] = [
            {
                "relation": "references",
                "target_item_id": target["item_id"],
                "target_path": target["relative_path"],
                "required": True,
                "evidence_path": source["relative_path"] + "/reference.json",
            }
        ]
        target["deletion"]["referenced_by"] = [source["item_id"]]  # type: ignore[index]
        _resign_inventory(referenced)
        validate_storage_inventory(referenced)

        missing_backlink = deepcopy(referenced)
        self._item(missing_backlink, "fixtures/beta")["deletion"][  # type: ignore[index]
            "referenced_by"
        ] = []
        _resign_inventory(missing_backlink)
        with self.assertRaises(RuntimeManagerError):
            validate_storage_inventory(missing_backlink)

        missing_target = deepcopy(referenced)
        self._item(missing_target, "fixtures/alpha")["references"][0][  # type: ignore[index]
            "target_item_id"
        ] = ITEM_PREFIX + "f" * 64
        _resign_inventory(missing_target)
        with self.assertRaises(RuntimeManagerError):
            validate_storage_inventory(missing_target)

    def test_inventory_parent_must_resolve_and_contain_child(self) -> None:
        report = deepcopy(self.inventory)
        alpha = self._item(report, "fixtures/alpha")
        beta = self._item(report, "fixtures/beta")
        beta["parent_item_id"] = alpha["item_id"]
        _resign_inventory(report)
        with self.assertRaises(RuntimeManagerError):
            validate_storage_inventory(report)

    def test_managed_runtime_rejects_root_template_and_world_path_forgery(self) -> None:
        root_mismatch = self._managed_runtime()
        root_mismatch["storage_root"] = str(self.root / "other-storage")
        _resign_runtime(root_mismatch)
        with self.assertRaises(RuntimeManagerError):
            validate_managed_runtime(root_mismatch)

        external_template = self._managed_runtime()
        external_template["source_template"]["path"] = str(  # type: ignore[index]
            self.root / "templates/runtime-template"
        )
        _resign_runtime(external_template)
        with self.assertRaises(RuntimeManagerError):
            validate_managed_runtime(external_template)

        nested_template = self._managed_runtime()
        nested_template["source_template"]["path"] = str(  # type: ignore[index]
            self.root / nested_template["relative_path"] / "template"
        )
        _resign_runtime(nested_template)
        with self.assertRaises(RuntimeManagerError):
            validate_managed_runtime(nested_template)

        incoherent_world = self._managed_runtime()
        incoherent_world["world"]["relative_path"] = "other"  # type: ignore[index]
        _resign_runtime(incoherent_world)
        with self.assertRaises(RuntimeManagerError):
            validate_managed_runtime(incoherent_world)

        external_profile = self._managed_runtime()
        external_profile["profile"]["profile_path"] = str(  # type: ignore[index]
            self.root.parent / "external-profile.json"
        )
        _resign_runtime(external_profile)
        with self.assertRaises(RuntimeManagerError):
            validate_managed_runtime(external_profile)


if __name__ == "__main__":
    unittest.main()
