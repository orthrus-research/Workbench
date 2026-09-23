#!/usr/bin/env python3

from __future__ import annotations

from contextlib import ExitStack
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
from typing import Any
import unittest
from unittest import mock


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_crucible_observatory import (  # noqa: E402
    CaptureValidationError,
    canonical_json_bytes,
    canonical_json_sha256,
)
from workbench_crucible_observatory.cleanroom_matrix import (  # noqa: E402
    FIXED_WORLD_SEED_SHA256,
)


WORKER_PATH = MODULE_ROOT / "tools" / "run_cleanroom_worldgen_case.py"
SPEC = importlib.util.spec_from_file_location("cleanroom_case_worker_test_module", WORKER_PATH)
assert SPEC is not None and SPEC.loader is not None
worker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = worker
SPEC.loader.exec_module(worker)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FakeProjection:
    def __init__(self, source_bundle_sha256: str, execution_id: str) -> None:
        self.projection_id = "crucible-worldgen-cleanroom-bundle-projection:sha256:" + digest(
            execution_id
        )
        self.source_bundle_sha256 = source_bundle_sha256
        self._document = {
            "projection_id": self.projection_id,
            "source_bundle_sha256": self.source_bundle_sha256,
            "execution_id": execution_id,
        }

    def as_dict(self) -> dict[str, str]:
        return dict(self._document)


class CleanroomCaseWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.raw = self.root / "raw.ndjson"
        self.raw.write_text("{}\n", encoding="utf-8")
        self.schema = self.root / "raw-schema.json"
        self.schema.write_text(
            json.dumps({"$id": "workbench://test/raw-schema"}, separators=(",", ":")),
            encoding="utf-8",
        )
        plan_material = {
            "format": "workbench-cleanroom-worldgen-observatory-probe-plan-v1",
            "hooks": [],
            "raw_transport": {"schema_id": "workbench://test/raw-schema"},
        }
        self.plan_id = worker.SPEC_PREFIX.replace(
            "crucible-cleanroom-case-worker-spec",
            "cleanroom-worldgen-observatory-probe-plan",
        ) + canonical_json_sha256(plan_material)
        self.plan = self.root / "probe-plan.json"
        self.plan.write_bytes(
            canonical_json_bytes({"plan_id": self.plan_id, **plan_material})
        )
        self.candidate = self.root / "candidate-lock.json"
        self.candidate.write_bytes(canonical_json_bytes({"candidate": "cleanroom-test"}))
        self.artifact = self.root / "fixture.jar"
        self.artifact.write_bytes(b"exact-fixture-artifact")
        self.mod_manifest = self.root / "installed-mod-set.json"
        self.mod_manifest_document = {"artifacts": [{"sha256": digest("fixture-mod")} ]}
        self.mod_manifest.write_bytes(
            json.dumps(self.mod_manifest_document, indent=2).encode("utf-8") + b"\n"
        )
        self.config_manifest = self.root / "configuration-set.json"
        self.config_manifest_document = {"files": [{"sha256": digest("server.properties")} ]}
        self.config_manifest.write_bytes(
            json.dumps(self.config_manifest_document, indent=2).encode("utf-8") + b"\n"
        )
        self.case = self.root / "runtime-case"
        self.case.mkdir()
        self.result = self.root / "fixture-result.json"
        self.result.write_text('{"fixture":"mock"}\n', encoding="utf-8")
        self.source_map = {
            "minecraft": digest("minecraft-runtime"),
            "workbench_probe": digest("observer-artifact"),
        }

    def build_spec(self, *, completed: bool) -> tuple[Path, dict[str, Any]]:
        result_input: dict[str, str] | None
        if completed:
            result_input = {"path": str(self.result), "sha256": file_digest(self.result)}
        else:
            result_input = None
        plan_sha = file_digest(self.plan)
        value: dict[str, Any] = {
            "format": worker.SPEC_FORMAT,
            "spec_id": "pending",
            "inputs": {
                "raw_ndjson": {"path": str(self.raw), "sha256": file_digest(self.raw)},
                "raw_schema": {
                    "path": str(self.schema),
                    "sha256": file_digest(self.schema),
                    "schema_id": "workbench://test/raw-schema",
                },
                "probe_plan": {
                    "path": str(self.plan),
                    "sha256": plan_sha,
                    "plan_id": self.plan_id,
                },
                "candidate_lock": {
                    "path": str(self.candidate),
                    "sha256": file_digest(self.candidate),
                },
                "fixture_artifact": {
                    "path": str(self.artifact),
                    "sha256": file_digest(self.artifact),
                },
                "installed_mod_set_manifest": {
                    "path": str(self.mod_manifest),
                    "sha256": file_digest(self.mod_manifest),
                },
                "configuration_set_manifest": {
                    "path": str(self.config_manifest),
                    "sha256": file_digest(self.config_manifest),
                },
                "runtime_case_directory": {"path": str(self.case)},
                "fixture_result": result_input,
            },
            "outputs": {
                "canonical_bundle": str(self.root / "out" / "bundle.json"),
                "cleanroom_projection": str(self.root / "out" / "projection.json"),
                "worker_receipt": str(self.root / "out" / "receipt.json"),
            },
            "admission": {
                "requested_mode": "lossless-fixture",
                "required_roles": ["block_write.chunk_primer"] if completed else [],
                "allow_incomplete": not completed,
                "expected_publication_state": "completed" if completed else "incomplete",
            },
            "custody": {
                "trusted_class_prefix_owners": {
                    "net.minecraft": "minecraft",
                    "dev.workbench": "workbench_probe",
                },
                "loaded_mod_source_sha256": dict(self.source_map),
                "runtime_mod_inventory_sha256": (
                    digest("runtime-inventory") if completed else None
                ),
            },
            "run": {
                "capture_mode": "lossless-fixture",
                "capture_plan_sha256": plan_sha,
                "fixture_id": "crucible-fixture:cleanroom-test",
                "fixture_sha256": file_digest(self.artifact),
                "environment": {
                    "minecraft_version": "1.12.2",
                    "platform_profile_id": "candidate:cleanroom-test",
                    "platform_profile_sha256": file_digest(self.candidate),
                    "pack_profile_id": None,
                    "pack_profile_sha256": None,
                    "snapshot_id": "snapshot:test",
                    "physical_side": "DEDICATED_SERVER",
                    "runtime_java": "25-test",
                    "mapping_namespace": "mcp-stable_39",
                    "transformed_runtime_sha256": digest("transformed-runtime"),
                    "mod_set_sha256": canonical_json_sha256(self.mod_manifest_document),
                    "configuration_set_sha256": canonical_json_sha256(
                        self.config_manifest_document
                    ),
                },
                "world": {
                    "world_instance_id": "world-instance:test-case",
                    "world_seed_sha256": FIXED_WORLD_SEED_SHA256,
                    "world_type": "wb_observe",
                    "generator_options_sha256": digest("{}"),
                    "dimension_ids": [0],
                },
            },
            "normalization": {
                "selection_id": "worldgen-selection:test-fixed-region",
                "execution_id": "test-completed" if completed else "test-crash",
                "checkpoint_domains": ["block_states"],
                "workbench_identity": {
                    "binding": "workbench",
                    "mod_id": "workbench_probe",
                    "class_name": "dev.workbench.ProbeDriver",
                    "method_name": "run",
                    "method_descriptor": "()V",
                    "mapping_namespace": "java_source",
                    "code_source_sha256": self.source_map["workbench_probe"],
                    "transformed_class_sha256": None,
                },
            },
        }
        material = dict(value)
        material.pop("spec_id")
        value["spec_id"] = worker.SPEC_PREFIX + canonical_json_sha256(material)
        path = self.root / ("completed-spec.json" if completed else "crash-spec.json")
        path.write_bytes(canonical_json_bytes(value))
        return path, value

    def pipeline_mocks(self, spec: dict[str, Any], *, state: str) -> ExitStack:
        stack = ExitStack()
        self.addCleanup(stack.close)
        admission = SimpleNamespace(
            schema_id=spec["inputs"]["raw_schema"]["schema_id"],
            schema_sha256=spec["inputs"]["raw_schema"]["sha256"],
            probe_plan_id=spec["inputs"]["probe_plan"]["plan_id"],
            capture_nonce="capture-test",
            coverage_summary={"row_count": 7},
            control_summary={"completion_state": "complete" if state == "completed" else "incomplete"},
        )
        self.admit_mock = stack.enter_context(
            mock.patch.object(worker, "admit_cleanroom_raw", return_value=admission)
        )
        custody = SimpleNamespace(
            actor_inventory=(
                {
                    "mod_id": "workbench_probe",
                    "code_source_sha256": self.source_map["workbench_probe"],
                    "class_name": "dev.workbench.ProbeDriver",
                    "method_name": "run",
                    "method_descriptor": "()V",
                    "mapping_namespace": "java_source",
                },
            ),
            class_dump_sha256={"dev.workbench.ProbeDriver": digest("class-dump")},
            class_dump_manifest=SimpleNamespace(
                format="workbench-foundation-class-dump-manifest-v1",
                class_count=1,
                total_size_bytes=128,
                manifest_sha256=spec["run"]["environment"]["transformed_runtime_sha256"],
            ),
        )
        stack.enter_context(
            mock.patch.object(worker, "collect_cleanroom_runtime_custody", return_value=custody)
        )

        def normalize(_admission: Any, **kwargs: Any) -> SimpleNamespace:
            publication = (
                {
                    "state": "completed",
                    "completion_seal": {
                        "capture_id": "crucible-worldgen-capture:sha256:"
                        + digest("capture:test")
                    },
                }
                if state == "completed"
                else {
                    "state": "incomplete",
                    "crash_residue": {
                        "residue_id": "crucible-worldgen-residue:sha256:"
                        + digest("residue:test")
                    },
                }
            )
            return SimpleNamespace(
                bundle={
                    "run": kwargs["run"],
                    "publication": publication,
                    "summary": {"record_count": 6},
                    "records": [],
                }
            )

        normalize_mock = stack.enter_context(
            mock.patch.object(
                worker,
                "_normalize_cleanroom_raw_publication",
                side_effect=normalize,
            )
        )
        self.normalize_mock = normalize_mock
        stack.enter_context(
            mock.patch.object(
                worker,
                "_validated_bundle_publication_state",
                return_value=state,
            )
        )

        def write_bundle(path: Path, publication: SimpleNamespace) -> SimpleNamespace:
            bundle = publication.bundle
            encoded = canonical_json_bytes(bundle) + b"\n"
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(encoded)
            publication_id = (
                bundle["publication"]["completion_seal"]["capture_id"]
                if state == "completed"
                else bundle["publication"]["crash_residue"]["residue_id"]
            )
            return SimpleNamespace(
                canonical_json_sha256=canonical_json_sha256(bundle),
                file_sha256=hashlib.sha256(encoded).hexdigest(),
                byte_count=len(encoded),
                run_id=bundle["run"]["run_id"],
                publication_state=state,
                publication_id=publication_id,
                record_count=bundle["summary"]["record_count"],
            )

        stack.enter_context(
            mock.patch.object(worker, "_write_validated_bundle", side_effect=write_bundle)
        )

        def write_projection(
            path: Path,
            _publication: SimpleNamespace,
            bundle_write: SimpleNamespace,
            *,
            execution_id: str,
        ) -> FakeProjection:
            projection = FakeProjection(
                bundle_write.canonical_json_sha256,
                execution_id,
            )
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(canonical_json_bytes(projection.as_dict()) + b"\n")
            return projection

        stack.enter_context(
            mock.patch.object(
                worker,
                "_write_validated_cleanroom_bundle_projection",
                side_effect=write_projection,
            )
        )
        return stack

    def test_completed_case_checks_result_and_writes_content_addressed_receipt(self) -> None:
        spec_path, spec = self.build_spec(completed=True)
        fixture = SimpleNamespace(
            artifact_sha256=file_digest(self.result),
            canonical_document_sha256=digest("fixture-result-document"),
            route_order="forward",
            runtime_mod_inventory=tuple(
                SimpleNamespace(mod_id=mod_id, source_sha256=source)
                for mod_id, source in self.source_map.items()
            ),
            inventory_sha256=lambda: spec["custody"]["runtime_mod_inventory_sha256"],
        )
        with self.pipeline_mocks(spec, state="completed"):
            with mock.patch.object(worker, "load_fixture_result", return_value=fixture) as loader:
                receipt = worker.run_worker(spec_path)

        loader.assert_called_once_with(self.result)
        self.assertEqual(receipt["publication_state"], "completed")
        self.assertEqual(receipt, worker.parse_cleanroom_case_worker_receipt(receipt))
        self.assertEqual(receipt["fixture_result_file_sha256"], file_digest(self.result))
        receipt_path = Path(spec["outputs"]["worker_receipt"])
        written = json.loads(receipt_path.read_text(encoding="utf-8"))
        material = dict(written)
        receipt_id = material.pop("receipt_id")
        self.assertEqual(receipt_id, worker.RECEIPT_PREFIX + canonical_json_sha256(material))
        kwargs = self.normalize_mock.call_args.kwargs
        clock = kwargs["monotonic_ns"]
        self.assertEqual([clock(), clock(), clock()], [0, 1, 2])

    def test_crash_case_forbids_result_and_publishes_incomplete_receipt(self) -> None:
        spec_path, spec = self.build_spec(completed=False)
        with self.pipeline_mocks(spec, state="incomplete"):
            with mock.patch.object(worker, "load_fixture_result") as loader:
                receipt = worker.run_worker(spec_path)

        loader.assert_not_called()
        self.assertEqual(receipt["publication_state"], "incomplete")
        self.assertIsNone(receipt["fixture_result_file_sha256"])
        admission_kwargs = self.admit_mock.call_args.kwargs
        self.assertTrue(admission_kwargs["allow_incomplete"])
        self.assertEqual(admission_kwargs["required_roles"], [])

    def test_rejects_spec_identity_and_input_identity_tampering(self) -> None:
        spec_path, spec = self.build_spec(completed=False)
        tampered = json.loads(spec_path.read_text(encoding="utf-8"))
        tampered["run"]["environment"]["configuration_set_sha256"] = digest("tampered")
        spec_path.write_bytes(canonical_json_bytes(tampered))
        with self.assertRaisesRegex(CaptureValidationError, "content identity mismatch"):
            worker.load_worker_spec(spec_path)

        spec_path.write_bytes(canonical_json_bytes(spec))
        self.raw.write_text('{"changed":true}\n', encoding="utf-8")
        with self.assertRaisesRegex(CaptureValidationError, "raw NDJSON digest mismatch"):
            worker.run_worker(spec_path)


if __name__ == "__main__":
    unittest.main()
