"""Contract tests for Packwiz-default client materialization V2."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


MODULE_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = MODULE_ROOT / "schemas"
RECEIPT_SCHEMA_PATH = (
    SCHEMA_ROOT / "packwiz-materialization-receipt-v2.schema.json"
)
RESULT_SCHEMA_PATH = (
    SCHEMA_ROOT / "packwiz-materialization-result-v2.schema.json"
)
CONTRACT_PATH = MODULE_ROOT / "contracts/runtime-materialize-v2.md"


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _receipt() -> dict[str, object]:
    digest = "a" * 64
    fixture = "file:///tmp/workbench/client/example/variants/packwiz-v2"
    return {
        "format": "workbench-packwiz-materialization-receipt-v2",
        "schema_version": 2,
        "materialization_id": f"sha256:{digest}",
        "operation_class": "local-mutation",
        "state": "materialized",
        "readiness": "pack-payload-installed",
        "plan_id": f"sha256:{'b' * 64}",
        "request": {"launcher": "prism", "side": "client"},
        "workspace": {"root_uri": "file:///tmp/pack"},
        "project": {"name": "Packwiz fixture"},
        "source_snapshot": {"tree_sha256": f"sha256:{digest}"},
        "refreshed_pack": {"manifest_sha256": digest},
        "tools": {"installer": {}, "java": {}, "packwiz": {}},
        "option_policy": {
            "side": "client",
            "optional_files": "pack-declared-defaults",
            "launcher_metadata": "isolated-empty-sentinel",
        },
        "packwiz_options": {
            "policy": "pack-declared-defaults",
            "policy_version": 1,
            "policy_sha256": f"sha256:{'9' * 64}",
            "decisions_sha256": f"sha256:{'c' * 64}",
            "optional_count": 2,
            "enabled_count": 1,
            "disabled_count": 1,
            "installer_state": {
                "relative_path": "packwiz.json",
                "uri": f"{fixture}/instance/.minecraft/packwiz.json",
                "cached_side": "client",
                "initial": {"sha256": "d" * 64, "size": 146},
                "final": {"sha256": "e" * 64, "size": 2031},
            },
            "files": [
                {
                    "metadata_path": "mods/default-off.pw.toml",
                    "metafile_sha256": "1" * 64,
                    "output_path": "mods/default-off.jar",
                    "output_sha256": None,
                    "output_size": None,
                    "name": "Default off",
                    "side": "both",
                    "declared_default": False,
                    "applied": False,
                    "present": False,
                },
                {
                    "metadata_path": "mods/default-on.pw.toml",
                    "metafile_sha256": "2" * 64,
                    "output_path": "mods/default-on.jar",
                    "output_sha256": "3" * 64,
                    "output_size": 42,
                    "name": "Default on",
                    "side": "client",
                    "declared_default": True,
                    "applied": True,
                    "present": True,
                },
            ],
        },
        "bootstrap_source": {
            "fixture_root_uri": "file:///tmp/bootstrap",
            "receipt_uri": "file:///tmp/bootstrap-receipt.json",
            "receipt_sha256": "4" * 64,
            "receipt_size": 2048,
            "launcher_tree": {
                "file_count": 1,
                "total_bytes": 994,
                "tree_sha256": f"sha256:{'6' * 64}",
            },
        },
        "seeds": {"file_count": 0},
        "launcher": {
            "manifest_uri": f"{fixture}/instance/mmc-pack.json",
            "manifest_sha256_before": "5" * 64,
            "manifest_sha256_after": "5" * 64,
        },
        "payload": {
            "root_uri": f"{fixture}/instance/.minecraft",
            "tree_sha256": f"sha256:{'7' * 64}",
            "file_count": 10,
            "total_bytes": 4096,
        },
        "target": {
            "variant": "packwiz-v2",
            "variant_id": f"sha256:{'8' * 64}",
            "variant_root_uri": fixture,
            "fixture_root_uri": fixture,
            "instance_root_uri": f"{fixture}/instance",
            "receipt_uri": (
                f"{fixture}/receipts/packwiz-materialization-v2.json"
            ),
        },
        "evidence": {"installer_log_uri": "file:///tmp/installer.log"},
        "warnings": [],
        "limitations": ["Minecraft has not been launched."],
    }


class RuntimeMaterializeV2ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.receipt_schema = _read_json(RECEIPT_SCHEMA_PATH)
        cls.result_schema = _read_json(RESULT_SCHEMA_PATH)
        Draft202012Validator.check_schema(cls.receipt_schema)
        Draft202012Validator.check_schema(cls.result_schema)
        cls.receipt_validator = Draft202012Validator(
            cls.receipt_schema,
            format_checker=FormatChecker(),
        )
        registry = Registry().with_resource(
            cls.receipt_schema["$id"],
            Resource.from_contents(cls.receipt_schema),
        )
        cls.result_validator = Draft202012Validator(
            cls.result_schema,
            registry=registry,
            format_checker=FormatChecker(),
        )

    def test_representative_receipt_and_result_validate(self) -> None:
        receipt = _receipt()
        self.receipt_validator.validate(receipt)
        self.result_validator.validate({
            "format": "workbench-packwiz-materialization-result-v2",
            "schema_version": 2,
            "outcome": "installed",
            "receipt": receipt,
            "bootstrap_outcome": "reused",
            "java_outcome": "reused",
            "installer_artifact_outcome": "reused",
        })

    def test_pack_default_must_be_the_applied_value(self) -> None:
        receipt = _receipt()
        receipt["packwiz_options"]["files"][0]["applied"] = True
        self.assertFalse(self.receipt_validator.is_valid(receipt))

    def test_absent_output_cannot_claim_byte_identity(self) -> None:
        receipt = _receipt()
        receipt["packwiz_options"]["files"][0]["output_sha256"] = "8" * 64
        self.assertFalse(self.receipt_validator.is_valid(receipt))

    def test_client_default_true_output_must_be_present(self) -> None:
        receipt = _receipt()
        row = receipt["packwiz_options"]["files"][1]
        row["present"] = False
        row["output_sha256"] = None
        row["output_size"] = None
        self.assertFalse(self.receipt_validator.is_valid(receipt))

    def test_installer_state_binds_initial_and_final_bytes(self) -> None:
        receipt = _receipt()
        del receipt["packwiz_options"]["installer_state"]["initial"]
        self.assertFalse(self.receipt_validator.is_valid(receipt))

    def test_v2_cannot_publish_into_an_unversioned_target(self) -> None:
        receipt = _receipt()
        receipt["target"]["variant"] = "legacy"
        self.assertFalse(self.receipt_validator.is_valid(receipt))

    def test_packwiz_option_object_is_closed(self) -> None:
        receipt = _receipt()
        receipt["packwiz_options"]["implicit_override"] = True
        self.assertFalse(self.receipt_validator.is_valid(receipt))

    def test_derived_option_policy_is_closed_and_constant(self) -> None:
        for field, value in (
            ("side", "server"),
            ("optional_files", "enabled-by-installer-cli"),
            ("launcher_metadata", "modified-prism"),
        ):
            with self.subTest(field=field):
                receipt = _receipt()
                receipt["option_policy"][field] = value
                self.assertFalse(self.receipt_validator.is_valid(receipt))

        receipt = _receipt()
        receipt["option_policy"]["implicit_override"] = True
        self.assertFalse(self.receipt_validator.is_valid(receipt))

    def test_contract_names_public_formats_and_paths(self) -> None:
        contract = CONTRACT_PATH.read_text(encoding="utf-8")
        for value in (
            "workbench-packwiz-materialization-receipt-v2",
            "workbench-packwiz-materialization-result-v2",
            "receipts/packwiz-materialization-v2.json",
            "pack-declared-defaults",
            "packwiz-v2",
        ):
            self.assertIn(value, contract)


if __name__ == "__main__":
    unittest.main()
