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
    RAW_SERVICE_COMPONENTS_FORMAT,
    REQUIRED_COMPONENT_ROLES,
    ServiceComponentsValidationError,
    build_service_components_receipt,
    parse_raw_service_components,
    parse_service_components_receipt,
)
from workbench_crucible_runtime_snapshot import bind_known_receipt  # noqa: E402


CAPTURE = "capture:components"
URI_A = "file:/runtime/cleanroom.jar"
URI_B = "file:/runtime/cleanmix.jar"


def _row(event: str, payload: dict) -> dict:
    return {
        "format": RAW_SERVICE_COMPONENTS_FORMAT,
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
                "agent_id": "workbench-cleanmix-service-components-agent-v1",
                "java_version": "25.0.4",
                "target_class": "org.spongepowered.asm.service.MixinService",
                "expected_input_sha256": "a" * 64,
            },
        ),
        _row(
            "transformer_installed",
            {
                "target_class": "org.spongepowered.asm.service.MixinService",
                "expected_input_sha256": "a" * 64,
                "retransform_requested": False,
            },
        ),
        _row(
            "transform_applied",
            {
                "defining_loader_class": "example.Loader",
                "defining_loader_identity": "example.Loader@1",
                "target_code_source_uri": URI_A,
                "input_sha256": "a" * 64,
                "output_sha256": "2" * 64,
                "reason": None,
            },
        ),
    ]
    for role in REQUIRED_COMPONENT_ROLES:
        rows.append(
            _row(
                "component_observed",
                {
                    "role": role,
                    "implementation_class": "example." + role.title().replace("_", ""),
                    "object_identity": "example.Object@" + str(len(rows)),
                    "implementation_loader_class": "example.Loader",
                    "implementation_loader_identity": "example.Loader@1",
                    "code_source_uri": URI_A if role != "logger" else URI_B,
                    "reported_name": "CleanMix" if role in {"service", "logger"} else None,
                },
            )
        )
    rows.append(
        _row(
            "capture_end",
            {
                "health": "healthy",
                "transform_applied_count": 1,
                "observed_roles": sorted(REQUIRED_COMPONENT_ROLES),
                "failed_roles": [],
                "write_failure": False,
            },
        )
    )
    for index, row in enumerate(rows):
        row["sequence"] = index
    return rows


def _encoded(rows: list[dict]) -> bytes:
    return b"".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
        for row in rows
    )


def _receipt() -> dict:
    rows = parse_raw_service_components(_encoded(_rows()))
    return build_service_components_receipt(
        session={
            "capture_id": CAPTURE,
            "launch_id": "launch:components",
            "profile_id": "workbench-platform:cleanroom:test",
            "side": "dedicated_server",
            "candidate_lock_sha256": "b" * 64,
            "toolchain_lock_sha256": "c" * 64,
        },
        inputs={
            "agent_artifact": {"label": "agent.jar", "sha256": "d" * 64, "size_bytes": 10},
            "candidate_lock": {"label": "candidate.json", "sha256": "b" * 64, "size_bytes": 20},
            "fixture_result": {"label": "result.json", "sha256": "3" * 64, "size_bytes": 25},
            "launch_log": {"label": "launch.log", "sha256": "4" * 64, "size_bytes": 28},
            "raw_trace": {"label": "raw.ndjson", "sha256": "e" * 64, "size_bytes": 30},
            "toolchain_lock": {"label": "toolchain.json", "sha256": "c" * 64, "size_bytes": 40},
        },
        artifacts=[
            {
                "artifact_sha256": "f" * 64,
                "size_bytes": 50,
                "label": "cleanroom.jar",
                "code_source_uri": URI_A,
            },
            {
                "artifact_sha256": "1" * 64,
                "size_bytes": 60,
                "label": "cleanmix.jar",
                "code_source_uri": URI_B,
            },
        ],
        raw_events=rows,
        limitations=["Synthetic component fixture."],
    )


class MixinServiceComponentsReceiptTests(unittest.TestCase):
    def test_healthy_receipt_binds_all_required_roles(self) -> None:
        receipt = _receipt()
        admitted = parse_service_components_receipt(receipt)
        schema = json.loads(
            (
                ROOT
                / "modules/crucible/schemas"
                / "mixin-selected-service-components-receipt-v1.schema.json"
            ).read_bytes()
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(admitted)
        self.assertEqual(receipt, admitted)
        self.assertEqual(
            list(REQUIRED_COMPONENT_ROLES),
            admitted["summary"]["observed_roles"],
        )
        self.assertTrue(admitted["summary"]["all_required_components_observed"])
        self.assertTrue(admitted["boundaries"]["selected_service_components_proved"])
        self.assertFalse(admitted["boundaries"]["transformer_order_proved"])

        binding = bind_known_receipt(
            json.dumps(
                admitted,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8"),
            source_label="receipts/service-components.json",
        )
        self.assertEqual(binding["role"], "mixin.service-components")
        self.assertEqual(len(binding["lock_bindings"]), 2)

    def test_healthy_raw_trace_cannot_omit_a_component(self) -> None:
        rows = _rows()
        rows = [
            row
            for row in rows
            if not (
                row["event"] == "component_observed"
                and row["payload"]["role"] == "audit_trail"
            )
        ]
        rows[-1]["payload"]["observed_roles"].remove("audit_trail")
        for index, row in enumerate(rows):
            row["sequence"] = index
        with self.assertRaisesRegex(ServiceComponentsValidationError, "required role"):
            parse_raw_service_components(_encoded(rows))

    def test_receipt_identity_and_derived_summary_fail_closed(self) -> None:
        receipt = _receipt()
        tampered = deepcopy(receipt)
        tampered["summary"]["component_count"] -= 1
        with self.assertRaisesRegex(ServiceComponentsValidationError, "identity mismatch"):
            parse_service_components_receipt(tampered)

    def test_raw_trace_rejects_duplicate_component_roles(self) -> None:
        rows = _rows()
        duplicate = deepcopy(next(row for row in rows if row["event"] == "component_observed"))
        rows.insert(-1, duplicate)
        rows[-1]["payload"]["observed_roles"].append(duplicate["payload"]["role"])
        rows[-1]["payload"]["observed_roles"].sort()
        for index, row in enumerate(rows):
            row["sequence"] = index
        with self.assertRaisesRegex(ServiceComponentsValidationError, "repeats an observed role"):
            parse_raw_service_components(_encoded(rows))


if __name__ == "__main__":
    unittest.main()
