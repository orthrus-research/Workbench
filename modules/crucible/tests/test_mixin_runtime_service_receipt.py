from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
CRUCIBLE_SOURCE = ROOT / "modules/crucible/src"
if str(CRUCIBLE_SOURCE) not in sys.path:
    sys.path.insert(0, str(CRUCIBLE_SOURCE))

from workbench_crucible_mixin_custody import (  # noqa: E402
    COMPONENT_TOPOLOGY_RECEIPT_PREFIX,
    RUNTIME_SERVICE_OBSERVATION_SET_FORMAT,
    RUNTIME_SERVICE_RECEIPT_FORMAT,
    RUNTIME_SERVICE_RECEIPT_PREFIX,
    PROVIDER_ENUMERATION_EVIDENCE_PREFIX,
    SERVICE_LOADER_ITERATOR_MECHANISM,
    RuntimeServiceValidationError,
    build_runtime_service_receipt,
    canonical_runtime_service_json_bytes,
    parse_runtime_service_receipt,
    write_runtime_service_receipt,
)


SCHEMA_PATH = (
    ROOT / "modules/crucible/schemas/mixin-runtime-service-receipt-v1.schema.json"
)
TOOL_PATH = (
    ROOT / "modules/crucible/tools/assemble_mixin_runtime_service_receipt.py"
)
ARTIFACT_SHA = "a" * 64
PROVIDER_SHA = "9" * 64
LOG_SHA = "b" * 64
ENUMERATION_SHA = "c" * 64
LOCK_SHA = "d" * 64
TOPOLOGY_FILE_SHA = "e" * 64
TOPOLOGY_ID = COMPONENT_TOPOLOGY_RECEIPT_PREFIX + "f" * 64
ENUMERATION_ID = PROVIDER_ENUMERATION_EVIDENCE_PREFIX + "8" * 64


def session() -> dict:
    return {
        "session_id": "crucible-session:runtime-service-test",
        "launch_id": "launch:runtime-service-test",
        "profile_id": "workbench-platform:test",
        "side": "dedicated_server",
        "candidate_toolchain_lock_sha256": LOCK_SHA,
        "component_topology_receipt_id": TOPOLOGY_ID,
        "component_topology_receipt_sha256": TOPOLOGY_FILE_SHA,
    }


def artifacts() -> list[dict]:
    return [
        {
            "artifact_sha256": ARTIFACT_SHA,
            "size_bytes": 1094214,
            "role": "mixin_runtime",
            "label": "mixin-runtime.jar",
        }
    ]


def log_evidence() -> list[dict]:
    return [
        {
            "source_sha256": LOG_SHA,
            "size_bytes": 8192,
            "kind": "runtime_log",
            "label": "debug.log",
        }
    ]


def provider_artifact() -> dict:
    return {
        "artifact_sha256": PROVIDER_SHA,
        "size_bytes": 4096,
        "role": "service_provider",
        "label": "platform-service-provider.jar",
    }


def not_enumerated() -> dict:
    return {
        "state": "not_enumerated",
        "mechanism": None,
        "providers": [],
        "evidence_sha256": None,
        "evidence_id": None,
        "source_record": None,
        "failure": None,
    }


def reports() -> list[dict]:
    return [
        {
            "sequence": 1,
            "kind": "initialization_report",
            "service_name": "NativeService",
            "evidence_sha256": LOG_SHA,
            "source_record": "line:10",
            "logger": "Platform",
            "level": "INFO",
        },
        {
            "sequence": 2,
            "kind": "selection_report",
            "service_name": "NativeService",
            "evidence_sha256": LOG_SHA,
            "source_record": "line:20",
            "thread": "main",
        },
        {
            "sequence": 3,
            "kind": "subsystem_report",
            "service_name": "NativeService",
            "subsystem_version": "0.8.7",
            "environment": "SERVER",
            "source_artifact_sha256": ARTIFACT_SHA,
            "evidence_sha256": LOG_SHA,
            "source_record": "line:30",
        },
    ]


def build_default(**changes: object) -> dict:
    material = {
        "session": session(),
        "artifacts": artifacts(),
        "evidence": log_evidence(),
        "observations": reports(),
        "provider_enumeration": not_enumerated(),
        "limitations": ["Synthetic fixture only."],
    }
    material.update(changes)
    return build_runtime_service_receipt(**material)


def reidentify(receipt: dict) -> None:
    material = deepcopy(receipt)
    material.pop("receipt_id")
    receipt["receipt_id"] = RUNTIME_SERVICE_RECEIPT_PREFIX + hashlib.sha256(
        canonical_runtime_service_json_bytes(material)
    ).hexdigest()


class MixinRuntimeServiceReceiptTests(unittest.TestCase):
    def test_reported_selection_remains_independent_from_enumeration(self) -> None:
        receipt = build_default()
        self.assertEqual(receipt["format"], RUNTIME_SERVICE_RECEIPT_FORMAT)
        self.assertEqual(
            receipt["summary"]["reported_selection_state"],
            "one_name_reported",
        )
        self.assertEqual(
            receipt["summary"]["reported_selection_report_count"], 2
        )
        self.assertEqual(
            receipt["summary"]["reported_service_names"], ["NativeService"]
        )
        self.assertEqual(
            receipt["summary"]["enumerated_provider_uniqueness"], "unknown"
        )
        self.assertIsNone(receipt["summary"]["enumerated_provider_count"])
        self.assertTrue(
            receipt["boundaries"][
                "reported_selection_is_not_provider_enumeration"
            ]
        )
        self.assertFalse(receipt["boundaries"]["final_transformations_proved"])
        self.assertFalse(
            receipt["boundaries"][
                "reported_service_name_establishes_implementation_class"
            ]
        )
        self.assertTrue(
            receipt["boundaries"][
                "mixin_runtime_code_source_is_not_service_provider_attribution"
            ]
        )
        self.assertEqual(parse_runtime_service_receipt(receipt), receipt)

    def test_initialization_report_does_not_claim_selection(self) -> None:
        receipt = build_default(observations=[reports()[0]])
        self.assertEqual(
            receipt["summary"]["reported_selection_state"], "not_reported"
        )
        self.assertEqual(
            receipt["summary"]["reported_selection_report_count"], 0
        )
        self.assertEqual(receipt["summary"]["reported_service_names"], [])

    def test_enumerated_unique_provider_does_not_claim_runtime_selection(self) -> None:
        evidence = log_evidence() + [
            {
                "source_sha256": ENUMERATION_SHA,
                "size_bytes": 512,
                "kind": "service_loader_enumeration",
                "label": "provider-enumeration.json",
            }
        ]
        enumeration = {
            "state": "enumerated",
            "mechanism": SERVICE_LOADER_ITERATOR_MECHANISM,
            "providers": [
                {
                    "service_class": "example.NativeMixinService",
                    "service_name": "NativeService",
                    "provider_artifact_sha256": PROVIDER_SHA,
                }
            ],
            "evidence_sha256": ENUMERATION_SHA,
            "evidence_id": ENUMERATION_ID,
            "source_record": ENUMERATION_ID,
            "failure": None,
        }
        receipt = build_default(
            artifacts=artifacts() + [provider_artifact()],
            evidence=evidence,
            observations=[],
            provider_enumeration=enumeration,
        )
        self.assertEqual(
            receipt["summary"]["reported_selection_state"], "not_reported"
        )
        self.assertEqual(
            receipt["summary"]["enumerated_provider_uniqueness"], "exactly_one"
        )
        self.assertEqual(receipt["summary"]["enumerated_provider_count"], 1)

    def test_enumerated_none_multiple_and_failed_are_distinct(self) -> None:
        second_artifact = {
            "artifact_sha256": "1" * 64,
            "size_bytes": 42,
            "role": "service_provider",
            "label": "second-provider.jar",
        }
        evidence = log_evidence() + [
            {
                "source_sha256": ENUMERATION_SHA,
                "size_bytes": 512,
                "kind": "service_loader_enumeration",
                "label": "provider-probe.json",
            }
        ]
        common = {
            "mechanism": SERVICE_LOADER_ITERATOR_MECHANISM,
            "evidence_sha256": ENUMERATION_SHA,
            "evidence_id": ENUMERATION_ID,
            "source_record": ENUMERATION_ID,
            "failure": None,
        }
        none_receipt = build_default(
            evidence=evidence,
            observations=[],
            provider_enumeration={"state": "enumerated", "providers": [], **common},
        )
        self.assertEqual(
            none_receipt["summary"]["enumerated_provider_uniqueness"], "none"
        )

        multiple_receipt = build_default(
            artifacts=artifacts() + [provider_artifact(), second_artifact],
            evidence=evidence,
            observations=[],
            provider_enumeration={
                "state": "enumerated",
                "providers": [
                    {
                        "service_class": "example.SecondService",
                        "service_name": "SharedName",
                        "provider_artifact_sha256": "1" * 64,
                    },
                    {
                        "service_class": "example.NativeMixinService",
                        "service_name": "SharedName",
                        "provider_artifact_sha256": PROVIDER_SHA,
                    },
                ],
                **common,
            },
        )
        self.assertEqual(
            multiple_receipt["summary"]["enumerated_provider_uniqueness"],
            "multiple",
        )
        self.assertEqual(
            multiple_receipt["summary"]["enumerated_distinct_service_name_count"],
            1,
        )
        self.assertEqual(
            multiple_receipt["summary"]["duplicate_enumerated_service_names"],
            ["SharedName"],
        )

        failed_receipt = build_default(
            evidence=evidence,
            observations=[],
            provider_enumeration={
                "state": "failed",
                "providers": [],
                **{
                    **common,
                    "failure": {
                        "stage": "service_loader_iteration",
                        "exception_class": "java.util.ServiceConfigurationError",
                        "message": "provider construction failed",
                    },
                },
            },
        )
        self.assertEqual(
            failed_receipt["summary"]["enumerated_provider_uniqueness"],
            "unknown",
        )
        self.assertIsNone(failed_receipt["summary"]["enumerated_provider_count"])

    def test_conflicting_reported_names_are_not_called_multiple_providers(self) -> None:
        conflicting = reports()
        conflicting[2]["service_name"] = "AnotherService"
        receipt = build_default(observations=conflicting)
        self.assertEqual(
            receipt["summary"]["reported_selection_state"],
            "conflicting_names_reported",
        )
        self.assertEqual(
            receipt["summary"]["reported_service_names"],
            ["AnotherService", "NativeService"],
        )
        self.assertEqual(
            receipt["summary"]["enumerated_provider_uniqueness"], "unknown"
        )

    def test_subsystem_report_requires_exact_source_binding(self) -> None:
        incomplete = reports()[2]
        incomplete["sequence"] = 1
        incomplete.pop("source_artifact_sha256")
        with self.assertRaisesRegex(
            RuntimeServiceValidationError,
            "subsystem_report requires source_artifact_sha256",
        ):
            build_default(observations=[incomplete])

        dangling = reports()[2]
        dangling["sequence"] = 1
        dangling["source_artifact_sha256"] = "2" * 64
        with self.assertRaisesRegex(
            RuntimeServiceValidationError,
            "does not resolve to artifacts",
        ):
            build_default(observations=[dangling])

    def test_enumeration_requires_dedicated_service_loader_evidence(self) -> None:
        enumeration = {
            "state": "enumerated",
            "mechanism": SERVICE_LOADER_ITERATOR_MECHANISM,
            "providers": [],
            "evidence_sha256": LOG_SHA,
            "evidence_id": ENUMERATION_ID,
            "source_record": ENUMERATION_ID,
            "failure": None,
        }
        with self.assertRaisesRegex(
            RuntimeServiceValidationError,
            "require.*service-loader enumeration evidence",
        ):
            build_default(observations=[], provider_enumeration=enumeration)

    def test_enumeration_cannot_promote_mixin_code_source_to_provider(self) -> None:
        evidence = log_evidence() + [
            {
                "source_sha256": ENUMERATION_SHA,
                "size_bytes": 512,
                "kind": "service_loader_enumeration",
                "label": "provider-enumeration.json",
            }
        ]
        enumeration = {
            "state": "enumerated",
            "mechanism": SERVICE_LOADER_ITERATOR_MECHANISM,
            "providers": [
                {
                    "service_class": "example.NativeMixinService",
                    "service_name": "NativeService",
                    "provider_artifact_sha256": ARTIFACT_SHA,
                }
            ],
            "evidence_sha256": ENUMERATION_SHA,
            "evidence_id": ENUMERATION_ID,
            "source_record": ENUMERATION_ID,
            "failure": None,
        }
        with self.assertRaisesRegex(
            RuntimeServiceValidationError, "does not name a service_provider"
        ):
            build_default(
                evidence=evidence,
                observations=[],
                provider_enumeration=enumeration,
            )

    def test_final_transformation_claims_are_outside_v1(self) -> None:
        observation = reports()[1]
        observation["final_bytecode_sha256"] = "3" * 64
        with self.assertRaisesRegex(RuntimeServiceValidationError, "unknown fields"):
            build_default(observations=[observation])
        encoded = json.dumps(build_default(), sort_keys=True)
        self.assertNotIn("final_bytecode_sha256", encoded)
        self.assertNotIn("transformation_succeeded", encoded)

    def test_component_receipt_id_and_candidate_lock_are_exactly_bound(self) -> None:
        receipt = build_default()
        self.assertEqual(
            receipt["session"]["candidate_toolchain_lock_sha256"], LOCK_SHA
        )
        self.assertEqual(
            receipt["session"]["component_topology_receipt_id"], TOPOLOGY_ID
        )
        self.assertEqual(
            receipt["session"]["component_topology_receipt_sha256"],
            TOPOLOGY_FILE_SHA,
        )
        invalid = session()
        invalid["component_topology_receipt_id"] = "wrong:sha256:" + "f" * 64
        with self.assertRaisesRegex(
            RuntimeServiceValidationError, "unsupported prefix"
        ):
            build_default(session=invalid)

    def test_semantic_parser_rejects_reidentified_summary_tampering(self) -> None:
        tampered = deepcopy(build_default())
        tampered["summary"]["enumerated_provider_uniqueness"] = "exactly_one"
        tampered["summary"]["enumerated_provider_count"] = 1
        reidentify(tampered)
        with self.assertRaisesRegex(
            RuntimeServiceValidationError, "not canonical V1 material"
        ):
            parse_runtime_service_receipt(tampered)

    def test_schema_accepts_constructed_receipt(self) -> None:
        try:
            from jsonschema import Draft202012Validator
        except ModuleNotFoundError:
            self.skipTest("jsonschema is not installed")
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(build_default())

    def test_cli_writes_semantically_valid_receipt(self) -> None:
        spec = {
            "format": RUNTIME_SERVICE_OBSERVATION_SET_FORMAT,
            "session": session(),
            "artifacts": artifacts(),
            "evidence": log_evidence(),
            "observations": reports(),
            "provider_enumeration": not_enumerated(),
            "limitations": ["CLI fixture."],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "observations.json"
            output_path = root / "receipt.json"
            input_path.write_text(json.dumps(spec), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(TOOL_PATH),
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            receipt = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(parse_runtime_service_receipt(receipt), receipt)
            self.assertEqual(completed.stdout.strip(), receipt["receipt_id"])

    def test_cli_rejects_duplicate_json_keys_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "observations.json"
            output_path = root / "receipt.json"
            input_path.write_text(
                '{"format":"first","format":"second"}', encoding="utf-8"
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(TOOL_PATH),
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, completed.returncode)
            self.assertIn("repeats JSON key 'format'", completed.stderr)
            self.assertFalse(output_path.exists())

    def test_writer_rejects_a_broken_destination_symlink(self) -> None:
        receipt = build_default()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "missing" / "captured.json"
            destination = root / "receipt.json"
            destination.symlink_to(outside)
            self.assertTrue(destination.is_symlink())
            self.assertFalse(destination.exists())
            with self.assertRaisesRegex(
                RuntimeServiceValidationError, "cannot be a symlink"
            ):
                write_runtime_service_receipt(destination, receipt)
            self.assertTrue(destination.is_symlink())
            self.assertFalse(outside.exists())


if __name__ == "__main__":
    unittest.main()
