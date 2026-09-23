from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_mixin_custody import (  # noqa: E402
    COMPONENT_TOPOLOGY_RECEIPT_PREFIX,
    build_runtime_service_receipt,
)
from workbench_crucible_runtime_snapshot import (  # noqa: E402
    RuntimeSnapshotValidationError,
    bind_known_receipt,
    build_runtime_snapshot,
    capability_from_receipts,
    capability_status,
    compare_runtime_snapshots,
    parse_runtime_snapshot,
    write_runtime_snapshot,
)


SCHEMA = ROOT / "modules/crucible/schemas/runtime-snapshot-v2.schema.json"
TOOL = ROOT / "modules/crucible/tools/assemble_runtime_snapshot.py"
CAPABILITIES = [
    "mixin.config-lifecycle",
    "mixin.final-class-definition",
    "mixin.runtime-service",
    "mixin.service-components",
    "mixin.service-discovery",
    "mixin.transformation-lifecycle",
    "mixin.transformer-chain",
]


def _runtime_service(*, launch: str, session: str, service_name: str = "CleanMix") -> dict:
    return build_runtime_service_receipt(
        session={
            "session_id": session,
            "launch_id": launch,
            "profile_id": "workbench-platform:cleanroom:test",
            "side": "dedicated_server",
            "candidate_toolchain_lock_sha256": "2" * 64,
            "component_topology_receipt_id": COMPONENT_TOPOLOGY_RECEIPT_PREFIX + "4" * 64,
            "component_topology_receipt_sha256": "5" * 64,
        },
        artifacts=[
            {
                "artifact_sha256": "6" * 64,
                "size_bytes": 100,
                "role": "mixin_runtime",
                "label": "cleanmix.jar",
            }
        ],
        evidence=[
            {
                "source_sha256": "7" * 64,
                "size_bytes": 200,
                "kind": "runtime_log",
                "label": "debug.log",
            }
        ],
        observations=[
            {
                "sequence": 1,
                "kind": "selection_report",
                "service_name": service_name,
                "evidence_sha256": "7" * 64,
                "source_record": "line:1",
            }
        ],
        provider_enumeration={
            "state": "not_enumerated",
            "mechanism": None,
            "providers": [],
            "evidence_sha256": None,
            "evidence_id": None,
            "source_record": None,
            "failure": None,
        },
        limitations=["Synthetic V2 fixture."],
    )


def _binding(*, launch: str, session: str, service_name: str = "CleanMix") -> dict:
    encoded = json.dumps(
        _runtime_service(launch=launch, session=session, service_name=service_name),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return bind_known_receipt(encoded, source_label="receipts/runtime-service.json")


def _runtime(*, launch: str, session: str, outcome: str = "complete") -> dict:
    return {
        "launch_id": launch,
        "platform_profile_id": "workbench-platform:cleanroom:test",
        "pack_profile_id": "workbench-pack:supersymmetry:test",
        "physical_side": "dedicated_server",
        "process_outcome": outcome,
        "candidate_lock_sha256": "1" * 64,
        "transformer_toolchain_lock_sha256": "2" * 64,
        "java_runtime_id": "eclipse-temurin-25.0.4+7",
        "session_ids": [session],
        "capture_ids": [],
    }


def _epoch(*, epoch_id: str = "cleanroom:test") -> dict:
    return {
        "profile_epoch_id": epoch_id,
        "adapter_id": "workbench-cleanroom-runtime-adapter:test",
        "adapter_version": "2.0.0",
        "axes": [
            {"name": "cleanmix.distribution", "value": "0.7.0"},
            {"name": "cleanroom.version", "value": "0.6.8-alpha"},
            {"name": "foundation.version", "value": "0.19.11"},
        ],
        "capabilities": CAPABILITIES,
    }


def _capabilities(binding: dict, *, final_state: str = "unavailable") -> list[dict]:
    rows = [
        capability_status(
            "mixin.config-lifecycle",
            state="not_observed",
            reason_code="producer-not-run",
        ),
        capability_status(
            "mixin.final-class-definition",
            state=final_state,
            reason_code="epoch-seam-not-instrumented",
        ),
        capability_from_receipts(
            "mixin.runtime-service",
            state="observed",
            receipt_bindings=[binding],
        ),
        capability_status(
            "mixin.service-components",
            state="not_observed",
            reason_code="producer-not-run",
        ),
        capability_status(
            "mixin.service-discovery",
            state="not_observed",
            reason_code="producer-not-run",
        ),
        capability_status(
            "mixin.transformation-lifecycle",
            state="not_observed",
            reason_code="producer-not-run",
        ),
        capability_status(
            "mixin.transformer-chain",
            state="not_observed",
            reason_code="producer-not-run",
        ),
    ]
    return rows


def _snapshot(
    *,
    launch: str,
    session: str,
    service_name: str = "CleanMix",
    epoch_id: str = "cleanroom:test",
    final_state: str = "unavailable",
) -> dict:
    binding = _binding(launch=launch, session=session, service_name=service_name)
    return build_runtime_snapshot(
        runtime_identity=_runtime(launch=launch, session=session),
        epoch=_epoch(epoch_id=epoch_id),
        receipt_bindings=[binding],
        capabilities=_capabilities(binding, final_state=final_state),
        capture_health={
            "observer_state": "healthy",
            "started": True,
            "completed": True,
            "errors": [],
        },
        limitations=["Synthetic composite fixture."],
    )


class RuntimeSnapshotV2Tests(unittest.TestCase):
    def test_snapshot_is_schema_valid_and_content_addressed(self) -> None:
        snapshot = _snapshot(launch="launch:a", session="session:a")
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(snapshot)
        self.assertEqual(snapshot, parse_runtime_snapshot(snapshot))
        self.assertEqual("Crucible", snapshot["authority"]["owner"])
        self.assertEqual(1, snapshot["summary"]["receipt_count"])
        self.assertEqual(1, snapshot["summary"]["capability_states"]["observed"])

    def test_v1_receipt_is_not_widened_when_required_fields_are_missing(self) -> None:
        stale = _runtime_service(launch="launch:a", session="session:a")
        del stale["provider_enumeration"]["mechanism"]
        encoded = json.dumps(stale, separators=(",", ":"), sort_keys=True).encode("utf-8")
        with self.assertRaisesRegex(RuntimeSnapshotValidationError, "semantic"):
            bind_known_receipt(encoded, source_label="receipts/stale.json")

    def test_receipt_cannot_cross_launch_profile_or_session(self) -> None:
        binding = _binding(launch="launch:a", session="session:a")
        with self.assertRaisesRegex(RuntimeSnapshotValidationError, "crosses launch"):
            build_runtime_snapshot(
                runtime_identity=_runtime(launch="launch:b", session="session:a"),
                epoch=_epoch(),
                receipt_bindings=[binding],
                capabilities=_capabilities(binding),
                capture_health={
                    "observer_state": "healthy",
                    "started": True,
                    "completed": True,
                    "errors": [],
                },
            )

        wrong_lock = _runtime(launch="launch:a", session="session:a")
        wrong_lock["transformer_toolchain_lock_sha256"] = "9" * 64
        with self.assertRaisesRegex(
            RuntimeSnapshotValidationError, "transformer toolchain lock"
        ):
            build_runtime_snapshot(
                runtime_identity=wrong_lock,
                epoch=_epoch(),
                receipt_bindings=[binding],
                capabilities=_capabilities(binding),
                capture_health={
                    "observer_state": "healthy",
                    "started": True,
                    "completed": True,
                    "errors": [],
                },
            )

    def test_incomplete_crash_snapshot_retains_observer_failure(self) -> None:
        binding = _binding(launch="launch:crash", session="session:crash")
        capabilities = _capabilities(binding)
        for row in capabilities:
            if row["name"] == "mixin.runtime-service":
                row.update(
                    {
                        "state": "failed",
                        "semantic_fingerprint": None,
                        "reason_code": "observer-write-failure",
                    }
                )
        snapshot = build_runtime_snapshot(
            runtime_identity=_runtime(
                launch="launch:crash", session="session:crash", outcome="crashed"
            ),
            epoch=_epoch(),
            receipt_bindings=[binding],
            capabilities=capabilities,
            capture_health={
                "observer_state": "failed",
                "started": True,
                "completed": False,
                "errors": [
                    {
                        "capability": "mixin.runtime-service",
                        "code": "observer-write-failure",
                        "message": "The observer retained a typed terminal failure.",
                        "receipt_id": binding["receipt_id"],
                    }
                ],
            },
        )
        self.assertEqual("crashed", snapshot["runtime_identity"]["process_outcome"])
        self.assertEqual("failed", snapshot["capture_health"]["observer_state"])

    def test_comparison_uses_stable_capabilities_not_launch_or_epoch_ids(self) -> None:
        left = _snapshot(launch="launch:left", session="session:left", epoch_id="cleanroom:0.5")
        equivalent = _snapshot(
            launch="launch:right",
            session="session:right",
            epoch_id="cleanroom:0.7",
        )
        comparison = compare_runtime_snapshots(left, equivalent)
        by_name = {row["capability"]: row for row in comparison["capabilities"]}
        self.assertEqual("equivalent", by_name["mixin.runtime-service"]["status"])
        self.assertEqual("unavailable", by_name["mixin.final-class-definition"]["status"])
        self.assertEqual("not-observed", by_name["mixin.config-lifecycle"]["status"])

        changed = _snapshot(
            launch="launch:changed",
            session="session:changed",
            service_name="DifferentService",
            epoch_id="cleanroom:0.7",
        )
        changed_result = compare_runtime_snapshots(left, changed)
        changed_by_name = {
            row["capability"]: row for row in changed_result["capabilities"]
        }
        self.assertEqual("changed", changed_by_name["mixin.runtime-service"]["status"])

    def test_atomic_writer_round_trips(self) -> None:
        snapshot = _snapshot(launch="launch:write", session="session:write")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "snapshot.json"
            write_runtime_snapshot(output, snapshot)
            loaded = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(snapshot, loaded)

    def test_capability_contract_cannot_silently_drop_a_row(self) -> None:
        snapshot = _snapshot(launch="launch:drop", session="session:drop")
        tampered = deepcopy(snapshot)
        tampered["capabilities"].pop()
        with self.assertRaises(RuntimeSnapshotValidationError):
            parse_runtime_snapshot(tampered)

    def test_cli_assembles_known_receipts_from_a_transient_plan(self) -> None:
        receipt = _runtime_service(
            launch="launch:cli",
            session="session:cli",
        )
        plan = {
            "format": "workbench-crucible-runtime-snapshot-plan-v2",
            "schema_version": 2,
            "runtime_identity": _runtime(launch="launch:cli", session="session:cli"),
            "epoch": _epoch(),
            "receipt_sources": [
                {"path": "runtime-service.json", "label": "receipts/runtime-service.json"}
            ],
            "capabilities": [
                {
                    "name": name,
                    "state": "observed" if name == "mixin.runtime-service" else "not_observed",
                    "receipt_roles": ["mixin.runtime-service"] if name == "mixin.runtime-service" else [],
                    "reason_code": None if name == "mixin.runtime-service" else "producer-not-run",
                    "limitations": [],
                }
                for name in CAPABILITIES
            ],
            "capture_health": {
                "observer_state": "healthy",
                "started": True,
                "completed": True,
                "errors": [],
            },
            "limitations": ["Synthetic CLI fixture."],
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "runtime-service.json").write_text(
                json.dumps(receipt), encoding="utf-8"
            )
            (directory / "plan.json").write_text(
                json.dumps(plan), encoding="utf-8"
            )
            output = directory / "snapshot.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--plan",
                    str(directory / "plan.json"),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            assembled = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(assembled, parse_runtime_snapshot(assembled))


if __name__ == "__main__":
    unittest.main()
