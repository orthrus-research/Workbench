from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_mixins import (  # noqa: E402
    RAW_FINAL_DEFINITION_FORMAT,
    FinalDefinitionValidationError,
    build_final_definition_receipt,
    parse_final_definition_receipt,
    parse_raw_final_definition,
)
from workbench_crucible_runtime_snapshot import bind_known_receipt  # noqa: E402


CAPTURE = "capture:final-definition"
TARGET = "example.Target"
FOUNDATION_URI = "file:/runtime/foundation.jar"
TARGET_URI = "file:/runtime/fixture.jar"


def _row(event: str, payload: dict) -> dict:
    return {
        "format": RAW_FINAL_DEFINITION_FORMAT,
        "capture_id": CAPTURE,
        "sequence": 0,
        "event": event,
        "payload": payload,
    }


def _rows() -> list[dict]:
    rows = [
        _row(
            "capture_start",
            {
                "agent_id": "workbench-foundation-final-definition-agent-v1",
                "java_version": "25.0.4",
                "targets": [TARGET],
                "foundation_dump_enabled": True,
                "redefine_supported": False,
                "retransform_supported": False,
            },
        ),
        _row(
            "transformer_installed",
            {
                "target_class": "top/outlands/foundation/boot/ActualClassLoader",
                "expected_input_sha256": "a" * 64,
                "retransform_requested": False,
            },
        ),
        _row(
            "transform_applied",
            {
                "target_class": "top.outlands.foundation.boot.ActualClassLoader",
                "defining_loader_class": "jdk.internal.loader.ClassLoaders$AppClassLoader",
                "defining_loader_identity": "jdk.internal.loader.ClassLoaders$AppClassLoader@1",
                "target_code_source_uri": FOUNDATION_URI,
                "input_sha256": "a" * 64,
                "output_sha256": "b" * 64,
                "reason": None,
            },
        ),
        _row(
            "find_started",
            {
                "target_class": TARGET,
                "defining_loader_class": "net.minecraft.launchwrapper.LaunchClassLoader",
                "defining_loader_identity": "net.minecraft.launchwrapper.LaunchClassLoader@2",
                "cached_before": False,
            },
        ),
        _row(
            "final_definition",
            {
                "target_class": TARGET,
                "defined_class": TARGET,
                "class_identity": "java.lang.Class@3",
                "defining_loader_class": "net.minecraft.launchwrapper.LaunchClassLoader",
                "defining_loader_identity": "net.minecraft.launchwrapper.LaunchClassLoader@2",
                "code_source_uri": TARGET_URI,
                "dump_relative_path": "example/Target.class",
                "final_bytecode_sha256": "c" * 64,
                "final_bytecode_size": 100,
                "cached_before": False,
            },
        ),
        _row(
            "capture_end",
            {
                "health": "healthy",
                "requested_targets": [TARGET],
                "defined_targets": [TARGET],
                "missing_targets": [],
                "definition_count": 1,
                "cached_return_target_count": 0,
                "definition_failure_count": 0,
                "observer_failure_count": 0,
                "write_failure": False,
            },
        ),
    ]
    for index, row in enumerate(rows):
        row["sequence"] = index
    return rows


def _encoded(rows: list[dict]) -> bytes:
    return b"".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True).encode() + b"\n"
        for row in rows
    )


def _receipt() -> dict:
    rows = parse_raw_final_definition(_encoded(_rows()))
    return build_final_definition_receipt(
        session={
            "capture_id": CAPTURE,
            "launch_id": "launch:final-definition",
            "profile_id": "workbench-platform:cleanroom:test",
            "side": "dedicated_server",
            "candidate_lock_sha256": "d" * 64,
            "toolchain_lock_sha256": "e" * 64,
        },
        inputs={
            "agent_artifact": {"label": "agent.jar", "sha256": "1" * 64, "size_bytes": 10},
            "candidate_lock": {"label": "candidate.json", "sha256": "d" * 64, "size_bytes": 20},
            "fixture_result": {"label": "result.json", "sha256": "2" * 64, "size_bytes": 30},
            "launch_log": {"label": "launch.log", "sha256": "3" * 64, "size_bytes": 40},
            "raw_trace": {"label": "definitions.ndjson", "sha256": "4" * 64, "size_bytes": 50},
            "toolchain_lock": {"label": "toolchain.json", "sha256": "e" * 64, "size_bytes": 60},
        },
        artifacts=[
            {"artifact_sha256": "5" * 64, "size_bytes": 70, "label": "foundation.jar", "code_source_uri": FOUNDATION_URI},
            {"artifact_sha256": "6" * 64, "size_bytes": 80, "label": "fixture.jar", "code_source_uri": TARGET_URI},
        ],
        foundation_manifest={
            "format": "workbench-foundation-class-dump-manifest-v1",
            "manifest_sha256": "7" * 64,
            "class_count": 10,
            "total_size_bytes": 1000,
            "foundation_artifact_sha256": "5" * 64,
        },
        raw_events=rows,
        limitations=["Synthetic final-definition fixture."],
    )


class MixinFinalDefinitionReceiptTests(unittest.TestCase):
    def test_complete_receipt_proves_named_final_bytes_and_binds_snapshot(self) -> None:
        receipt = parse_final_definition_receipt(_receipt())
        schema = json.loads(
            (ROOT / "modules/crucible/schemas/mixin-final-class-definition-receipt-v1.schema.json").read_bytes()
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(receipt)
        self.assertTrue(receipt["boundaries"]["final_class_bytes_proved"])
        self.assertEqual("c" * 64, receipt["definitions"][0]["final_bytecode_sha256"])
        binding = bind_known_receipt(
            json.dumps(receipt, separators=(",", ":"), sort_keys=True).encode(),
            source_label="receipts/final-definition.json",
        )
        self.assertEqual("mixin.final-class-definition", binding["role"])

    def test_raw_rejects_dump_only_without_successful_return(self) -> None:
        rows = [row for row in _rows() if row["event"] != "final_definition"]
        rows[-1]["payload"].update(
            {
                "health": "failed",
                "defined_targets": [],
                "missing_targets": [TARGET],
                "definition_count": 0,
            }
        )
        for index, row in enumerate(rows):
            row["sequence"] = index
        parsed = parse_raw_final_definition(_encoded(rows))
        self.assertEqual("failed", parsed[-1]["payload"]["health"])

    def test_identity_rejects_final_digest_tampering(self) -> None:
        receipt = deepcopy(_receipt())
        receipt["definitions"][0]["final_bytecode_sha256"] = "f" * 64
        with self.assertRaisesRegex(FinalDefinitionValidationError, "identity mismatch"):
            parse_final_definition_receipt(receipt)


if __name__ == "__main__":
    unittest.main()
