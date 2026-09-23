#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Mapping
import unittest


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))
sys.path.insert(0, str(MODULE_ROOT / "tests"))

from test_cleanroom_matrix import matrix_inputs  # noqa: E402
from workbench_crucible_observatory import (  # noqa: E402
    CaptureValidationError,
    canonical_json_bytes,
    canonical_json_sha256,
)
from workbench_crucible_observatory.cleanroom_hook_health import (  # noqa: E402
    EXCLUDED_INCOMPLETE_CASE_ROLE,
    HOOK_HEALTH_EVALUATION_PREFIX,
    HOOK_HEALTH_EVALUATION_SCHEMA,
    REQUIRED_CASE_ROLE,
)
from workbench_crucible_observatory.cleanroom_matrix import (  # noqa: E402
    CleanroomProjectionExecution,
    evaluate_cleanroom_projection_matrix,
    project_cleanroom_bundle,
)
from workbench_crucible_observatory.exact_runtime_proof import (  # noqa: E402
    EXPECTED_ARTIFACT_ROLES,
    PROOF_PREFIX,
    PROOF_SPEC_PREFIX,
    REQUIRED_CLAIM_LIMITATIONS,
    assemble_exact_runtime_proof,
    proof_spec_material,
)
from workbench_crucible_observatory.semantic_bytecode import (  # noqa: E402
    SEMANTIC_BYTECODE_COMPARISON_FORMAT,
    SEMANTIC_BYTECODE_POLICY_ID,
)


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def addressed(value: Mapping[str, Any], field: str, prefix: str) -> dict[str, Any]:
    material = dict(value)
    return {field: prefix + canonical_json_sha256(material), **material}


class ExactRuntimeProofFixture:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        self.output = self.root / "proof.json"
        self.limitations = list(REQUIRED_CLAIM_LIMITATIONS)
        self.documents: dict[str, dict[str, Any]] = {}
        self.paths: dict[str, Path] = {}
        self.projections: dict[str, Any] = {}
        self.matrix: dict[str, Any] = {}
        self._build_projections_and_matrix()
        self._build_sessions()
        self._build_workers()
        self._build_hook_health()
        self._build_semantic()
        self._build_atlas()
        self._write_documents()
        self.spec = self.root / "proof-spec.json"
        self.write_spec()

    def _build_projections_and_matrix(self) -> None:
        executions, _ = matrix_inputs()
        sources = {
            "aa-1": "exec-forward-a",
            "aa-2": "exec-forward-b",
            "order-reverse": "exec-reverse",
            "crash-before-seal": "exec-crash",
        }
        execution_ids = {
            "aa-1": "exec-aa1",
            "aa-2": "exec-aa2",
            "order-reverse": "exec-reverse",
            "crash-before-seal": "exec-crash",
        }
        for case_id, source_id in sources.items():
            projection = project_cleanroom_bundle(
                executions[source_id].canonical_bundle,
                execution_id=execution_ids[case_id],
            )
            self.projections[case_id] = projection
            self.documents[f"projection:{case_id}"] = projection.as_dict()

        matrix_executions = {
            "exec-aa1": CleanroomProjectionExecution(
                "exec-aa1",
                True,
                "forward",
                executions["exec-forward-a"].fixture_result,
                self.projections["aa-1"],
            ),
            "exec-aa2": CleanroomProjectionExecution(
                "exec-aa2",
                True,
                "forward",
                executions["exec-forward-b"].fixture_result,
                self.projections["aa-2"],
            ),
            "exec-reverse": CleanroomProjectionExecution(
                "exec-reverse",
                True,
                "reverse",
                executions["exec-reverse"].fixture_result,
                self.projections["order-reverse"],
            ),
            "exec-observer-off": CleanroomProjectionExecution(
                "exec-observer-off",
                False,
                "forward",
                executions["exec-observer-off"].fixture_result,
                None,
            ),
            "exec-crash": CleanroomProjectionExecution(
                "exec-crash",
                True,
                "forward",
                None,
                self.projections["crash-before-seal"],
            ),
        }
        aliases = {
            "aa-1": "exec-aa1",
            "aa-2": "exec-aa2",
            "observer-off": "exec-observer-off",
            "observer-on": "exec-aa1",
            "order-forward": "exec-aa1",
            "order-reverse": "exec-reverse",
            "restart-1": "exec-aa1",
            "restart-2": "exec-aa2",
            "crash-before-seal": "exec-crash",
        }
        self.matrix = evaluate_cleanroom_projection_matrix(matrix_executions, aliases)
        self.documents["evaluation:matrix"] = self.matrix

    def _matrix_row(self, execution_id: str) -> Mapping[str, Any]:
        return next(
            row for row in self.matrix["executions"] if row["execution_id"] == execution_id
        )

    def _build_sessions(self) -> None:
        execution_ids = {
            "aa-1": "exec-aa1",
            "aa-2": "exec-aa2",
            "order-reverse": "exec-reverse",
            "observer-off": "exec-observer-off",
        }
        routes = {
            "aa-1": "forward",
            "aa-2": "forward",
            "order-reverse": "reverse",
            "observer-off": "forward",
        }
        for case_id in ("aa-1", "aa-2", "order-reverse", "observer-off", "crash-before-seal"):
            observed = case_id != "observer-off"
            crash = case_id == "crash-before-seal"
            raw = None
            if observed:
                raw = {
                    "file_sha256": digest("raw:" + case_id),
                    "size_bytes": 1000,
                    "capture_id": "raw-capture:" + case_id,
                    "row_count": 100,
                    "terminal_sequence": 99,
                    "terminal_state": "incomplete_without_stop" if crash else "complete_and_stopped",
                    "stop_control_count": 0 if crash else 1,
                    "fixture_completion_marker_count": 0 if crash else 1,
                }
            result = None
            if not crash:
                matrix_row = self._matrix_row(execution_ids[case_id])
                result = {
                    "file_sha256": matrix_row["fixture_result_sha256"],
                    "canonical_document_sha256": digest("result-document:" + case_id),
                    "completion_state": "complete",
                    "save_state": "flushed",
                    "shutdown_state": "requested",
                    "route_order": routes[case_id],
                    "route_sha256": digest("route:" + routes[case_id]),
                    "semantic_result_sha256": matrix_row["semantic_map_sha256"],
                    "runtime_inventory_sha256": matrix_row["runtime_mod_inventory_sha256"],
                    "runtime_inventory_count": 2,
                }
            material = {
                "schema": "workbench.crucible.exact-runtime-session-audit.v1",
                "case_id": case_id,
                "outcome": "crash" if crash else "completed",
                "observer_enabled": observed,
                "candidate_lock": {
                    "file_sha256": digest("candidate"),
                    "canonical_sha256": digest("candidate-canonical"),
                },
                "fixture_artifact": {
                    "file_sha256": digest("fixture-artifact"),
                    "size_bytes": 100,
                },
                "installed_mod_set": {
                    "file_sha256": digest("installed-mod-set"),
                    "canonical_sha256": digest("installed-canonical"),
                    "runtime_class_source_sha256": digest("runtime-source"),
                    "verified_artifacts": [],
                },
                "configuration_set": {
                    "file_sha256": digest("configuration:" + case_id),
                    "canonical_sha256": digest("configuration-canonical:" + case_id),
                    "verified_files": [],
                    "runtime_settings_sha256": digest("settings:" + case_id),
                },
                "launch_log": {"file_sha256": digest("launch:" + case_id)},
                "raw_capture": raw,
                "fixture_result": result,
                "foundation_class_dump": {
                    "format": "workbench-foundation-class-dump-manifest-v1",
                    "class_count": 3305,
                    "total_size_bytes": 14_000_000,
                    "manifest_sha256": digest("foundation:" + case_id),
                },
            }
            self.documents[f"session:{case_id}"] = addressed(
                material,
                "audit_id",
                "crucible-exact-runtime-session-audit:sha256:",
            )

    def _build_workers(self) -> None:
        routes = {"aa-1": "forward", "aa-2": "forward", "order-reverse": "reverse"}
        for case_id in ("aa-1", "aa-2", "order-reverse", "crash-before-seal"):
            projection = self.projections[case_id]
            session = self.documents[f"session:{case_id}"]
            crash = case_id == "crash-before-seal"
            projection_bytes = canonical_json_bytes(projection.as_dict()) + b"\n"
            result = session["fixture_result"]
            material = {
                "schema": "workbench.crucible.cleanroom-case-worker-receipt.v1",
                "spec_id": "crucible-cleanroom-case-worker-spec:sha256:" + digest("spec:" + case_id),
                "spec_file_sha256": digest("spec-file:" + case_id),
                "raw_ndjson_sha256": session["raw_capture"]["file_sha256"],
                "raw_schema_id": "workbench://worldgen/raw-v1",
                "raw_schema_sha256": digest("raw-schema"),
                "probe_plan_id": "cleanroom-worldgen-observatory-probe-plan:sha256:" + digest("plan"),
                "probe_plan_file_sha256": digest("plan-file"),
                "candidate_lock_file_sha256": session["candidate_lock"]["file_sha256"],
                "fixture_artifact_file_sha256": session["fixture_artifact"]["file_sha256"],
                "installed_mod_set_manifest_file_sha256": session["installed_mod_set"]["file_sha256"],
                "installed_mod_set_manifest_canonical_sha256": session["installed_mod_set"]["canonical_sha256"],
                "configuration_set_manifest_file_sha256": session["configuration_set"]["file_sha256"],
                "configuration_set_manifest_canonical_sha256": session["configuration_set"]["canonical_sha256"],
                "fixture_result_file_sha256": None if crash else result["file_sha256"],
                "fixture_result_canonical_sha256": None if crash else result["canonical_document_sha256"],
                "fixture_route_order": None if crash else routes[case_id],
                "runtime_mod_inventory_sha256": None if crash else result["runtime_inventory_sha256"],
                "capture_nonce": session["raw_capture"]["capture_id"],
                "raw_row_count": session["raw_capture"]["row_count"],
                "raw_completion_state": "incomplete" if crash else "complete",
                "class_dump_inventory_sha256": digest("class-inventory:" + case_id),
                "foundation_class_dump_manifest_format": session["foundation_class_dump"]["format"],
                "foundation_class_dump_class_count": session["foundation_class_dump"]["class_count"],
                "foundation_class_dump_total_size_bytes": session["foundation_class_dump"]["total_size_bytes"],
                "foundation_class_dump_manifest_sha256": session["foundation_class_dump"]["manifest_sha256"],
                "actor_inventory_sha256": digest("actors:" + case_id),
                "run_id": projection.run["run_id"],
                "publication_state": projection.publication_state,
                "publication_id": projection.publication_id,
                "bundle_canonical_sha256": projection.source_bundle_sha256,
                "bundle_file_sha256": digest("bundle-file:" + case_id),
                "bundle_record_count": projection.summary["record_count"],
                "projection_id": projection.projection_id,
                "projection_canonical_sha256": canonical_json_sha256(projection.as_dict()),
                "projection_file_sha256": hashlib.sha256(projection_bytes).hexdigest(),
            }
            self.documents[f"worker:{case_id}"] = addressed(
                material,
                "receipt_id",
                "crucible-cleanroom-case-worker-receipt:sha256:",
            )

    def _build_hook_health(self) -> None:
        cases = []
        for case_id in ("aa-1", "aa-2", "order-reverse", "crash-before-seal"):
            crash = case_id == "crash-before-seal"
            session = self.documents[f"session:{case_id}"]
            hooks = [
                {"hook_id": f"hook-{index:02d}", "latest_state": "reached"}
                for index in range(14)
            ]
            cases.append(
                {
                    "case_id": case_id,
                    "case_role": EXCLUDED_INCOMPLETE_CASE_ROLE if crash else REQUIRED_CASE_ROLE,
                    "verdict": "excluded_incomplete_case" if crash else "all_declared_hooks_reached",
                    "raw_capture": {
                        "file_sha256": session["raw_capture"]["file_sha256"],
                    },
                    "session_custody": {"audit_id": session["audit_id"]},
                    "hook_health": {
                        "declared_hook_count": 14,
                        "observed_hook_count": 8 if crash else 14,
                        "health_record_count": 8 if crash else 14,
                        "missing_hook_ids": ["unknown-after-crash"] if crash else [],
                        "latest_not_reached_hook_ids": [],
                        "hooks": hooks[:8] if crash else hooks,
                    },
                }
            )
        material = {
            "schema": HOOK_HEALTH_EVALUATION_SCHEMA,
            "status": "passed",
            "claim_scope": "all 14 declared hooks reached in every required case",
            "validation_scope": {"full_raw_admission": "not_claimed"},
            "candidate": {"candidate_id": "candidate:test"},
            "raw_contract": {"declared_hook_count": 14},
            "required_case_count": 3,
            "excluded_incomplete_case_count": 1,
            "cases": cases,
        }
        self.documents["evaluation:hook-health"] = addressed(
            material,
            "evaluation_id",
            HOOK_HEALTH_EVALUATION_PREFIX,
        )

    def _build_semantic(self) -> None:
        semantic_digest = digest("same-semantic-dump")
        rows = []
        for case_id in ("aa-1", "aa-2", "order-reverse"):
            session = self.documents[f"session:{case_id}"]
            rows.append(
                {
                    "label": case_id,
                    "manifest_sha256": digest("semantic-manifest:" + case_id),
                    "exact_foundation_manifest_sha256": session["foundation_class_dump"]["manifest_sha256"],
                    "semantic_dump_sha256": semantic_digest,
                    "class_count": 3305,
                    "total_size_bytes": 14_000_000,
                    "normalized_class_count": 12,
                    "normalized_utf8_constants": 12,
                    "normalized_annotation_references": 18,
                }
            )
        material = {
            "format": SEMANTIC_BYTECODE_COMPARISON_FORMAT,
            "policy_id": SEMANTIC_BYTECODE_POLICY_ID,
            "case_count": 3,
            "cases": rows,
            "exact_foundation_manifests_all_distinct": True,
            "semantic_dumps_all_equal": True,
        }
        self.documents["evaluation:semantic-bytecode"] = {
            **material,
            "comparison_sha256": canonical_json_sha256(material),
        }

    @staticmethod
    def _answer(
        query: str,
        status: str,
        capture_ids: list[str],
        result: Any = None,
    ) -> dict[str, Any]:
        return {
            "query": query,
            "status": status,
            "contract_id": "WORKBENCH-ATLAS-WORLDGEN-OBSERVATORY-QUERY-V1",
            "capture_ids": capture_ids,
            "scope": {},
            "evidence_ordinals": [],
            "limitations": [],
            "result": result,
        }

    def _build_atlas(self) -> None:
        writer_mod = "workbench_synthetic_worldgen"
        primary_id = self.projections["aa-1"].publication_id
        comparison_id = self.projections["aa-2"].publication_id
        crash_id = self.projections["crash-before-seal"].run["run_id"]
        writer = self._answer(
            "who-wrote-block",
            "answered",
            [primary_id],
            {
                "final_writer": {
                    "initiating_actor": {"mod_id": writer_mod},
                }
            },
        )
        handler = self._answer(
            "which-handler-changed-event",
            "answered",
            [primary_id],
            {
                "changed_handler_count": 1,
                "handlers": [
                    {"changed": True, "actor": {"mod_id": writer_mod}}
                ],
            },
        )
        divergence = self._answer(
            "first-divergence",
            "equal",
            [primary_id, comparison_id],
            {"first_difference": None},
        )
        self.documents["evaluation:atlas"] = {
            "format": "workbench-atlas-worldgen-exact-query-suite-v1",
            "query_contract_id": "WORKBENCH-ATLAS-WORLDGEN-OBSERVATORY-QUERY-V1",
            "authority": {
                "evidence_owner": "Crucible",
                "interpretation_owner": "Atlas",
                "sorting_spool": "ephemeral-derived-non-authoritative",
            },
            "inputs": {
                "primary_capture_id": primary_id,
                "comparison_capture_id": comparison_id,
                "crash_capture_id": crash_id,
                "dimension_id": 0,
                "block_position": [1028, 80, 1034],
                "event_span_id": "span:event",
                "event_selector": {
                    "mode": "occurrence",
                    "event_class": "net.minecraftforge.event.terraingen.OreGenEvent$GenerateMinable",
                    "bus_id": "forge-bus:ore_gen_bus",
                    "chunk": {"x": 64, "z": 64},
                    "occurrence": 0,
                },
                "comparison_scope_sha256": digest("comparison-scope"),
                "comparison_checkpoint_id": "worldgen-checkpoint:forge.world_generators.final",
                "comparison_chunk": [64, 64],
            },
            "expectations": {
                "divergence_status": "equal",
                "writer_mod_id": writer_mod,
                "handler_mod_id": writer_mod,
                "crash_status": "incomplete-evidence",
            },
            "answers": {
                "who_wrote_block": writer,
                "which_handler_changed_event": handler,
                "first_divergence": divergence,
            },
            "crash_rejections": {
                "who_wrote_block": self._answer(
                    "who-wrote-block", "incomplete-evidence", [crash_id]
                ),
                "which_handler_changed_event": self._answer(
                    "which-handler-changed-event",
                    "incomplete-evidence",
                    [crash_id],
                ),
                "first_divergence": self._answer(
                    "first-divergence", "incomplete-evidence", [crash_id]
                ),
            },
            "gates": {
                "writer_answered": True,
                "event_answered": True,
                "divergence_closed": True,
                "writer_identity_matches": True,
                "handler_identity_matches": True,
                "all_queries_reject_crash": True,
            },
            "passed": True,
        }

    def _write_documents(self) -> None:
        for role, document in self.documents.items():
            path = self.inputs / (role.replace(":", "-") + ".json")
            path.write_bytes(canonical_json_bytes(document) + b"\n")
            self.paths[role] = path

    def write_role(self, role: str) -> None:
        self.paths[role].write_bytes(canonical_json_bytes(self.documents[role]) + b"\n")

    def write_spec(self) -> None:
        artifacts = [
            {
                "role": role,
                "path": str(self.paths[role]),
                "sha256": file_digest(self.paths[role]),
            }
            for role in EXPECTED_ARTIFACT_ROLES
        ]
        value = proof_spec_material(
            artifacts=artifacts,
            claim_limitations=self.limitations,
            output=str(self.output),
        )
        self.spec.write_bytes(canonical_json_bytes(value) + b"\n")


class ExactRuntimeProofTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture = ExactRuntimeProofFixture(self.root)

    def test_assembles_content_addressed_compositional_proof(self) -> None:
        proof = assemble_exact_runtime_proof(self.fixture.spec.resolve())
        written = json.loads(self.fixture.output.read_text(encoding="utf-8"))
        self.assertEqual(proof, written)
        material = dict(proof)
        proof_id = material.pop("proof_id")
        self.assertEqual(PROOF_PREFIX + canonical_json_sha256(material), proof_id)
        self.assertEqual("passed", proof["proof_state"])
        self.assertEqual(self.fixture.limitations, proof["claim_limitations"])
        self.assertFalse(proof["authority"]["creates_new_interpretation"])
        self.assertIsNone(proof["evaluations"]["atlas"]["embedded_content_id"])
        self.assertEqual(
            canonical_json_bytes(proof) + b"\n",
            self.fixture.output.read_bytes(),
        )

    def test_rejects_unknown_duplicate_and_digest_mismatched_inputs(self) -> None:
        value = json.loads(self.fixture.spec.read_text(encoding="utf-8"))
        value["artifacts"][0]["role"] = "session:foreign"
        material = dict(value)
        material.pop("spec_id")
        value["spec_id"] = PROOF_SPEC_PREFIX + canonical_json_sha256(material)
        self.fixture.spec.write_bytes(canonical_json_bytes(value) + b"\n")
        with self.assertRaisesRegex(CaptureValidationError, "unknown proof artifact role"):
            assemble_exact_runtime_proof(self.fixture.spec.resolve())

        self.fixture.write_spec()
        value = json.loads(self.fixture.spec.read_text(encoding="utf-8"))
        value["artifacts"][1]["role"] = value["artifacts"][0]["role"]
        material = dict(value)
        material.pop("spec_id")
        value["spec_id"] = PROOF_SPEC_PREFIX + canonical_json_sha256(material)
        self.fixture.spec.write_bytes(canonical_json_bytes(value) + b"\n")
        with self.assertRaisesRegex(CaptureValidationError, "duplicate proof artifact role"):
            assemble_exact_runtime_proof(self.fixture.spec.resolve())

        self.fixture.write_spec()
        self.fixture.paths["evaluation:atlas"].write_bytes(b"{}\n")
        with self.assertRaisesRegex(CaptureValidationError, "file SHA-256 mismatch"):
            assemble_exact_runtime_proof(self.fixture.spec.resolve())

    def test_cross_artifact_failure_does_not_replace_existing_output(self) -> None:
        self.fixture.output.write_bytes(b"preserve-me\n")
        session = self.fixture.documents["session:aa-1"]
        material = dict(session)
        material.pop("audit_id")
        material["raw_capture"] = dict(material["raw_capture"])
        material["raw_capture"]["file_sha256"] = digest("foreign-raw")
        self.fixture.documents["session:aa-1"] = addressed(
            material,
            "audit_id",
            "crucible-exact-runtime-session-audit:sha256:",
        )
        self.fixture.write_role("session:aa-1")
        self.fixture.write_spec()
        with self.assertRaisesRegex(CaptureValidationError, "raw custody mismatch"):
            assemble_exact_runtime_proof(self.fixture.spec.resolve())
        self.assertEqual(b"preserve-me\n", self.fixture.output.read_bytes())

    def test_rejects_atlas_self_report_when_direct_answer_disagrees(self) -> None:
        atlas = self.fixture.documents["evaluation:atlas"]
        atlas["answers"]["who_wrote_block"]["result"]["final_writer"][
            "initiating_actor"
        ]["mod_id"] = "foreign_writer"
        self.fixture.write_role("evaluation:atlas")
        self.fixture.write_spec()
        with self.assertRaisesRegex(CaptureValidationError, "writer identity answer mismatch"):
            assemble_exact_runtime_proof(self.fixture.spec.resolve())

    def test_rejects_self_consistent_but_wrong_atlas_expectation(self) -> None:
        atlas = self.fixture.documents["evaluation:atlas"]
        atlas["expectations"]["writer_mod_id"] = "foreign_writer"
        atlas["answers"]["who_wrote_block"]["result"]["final_writer"][
            "initiating_actor"
        ]["mod_id"] = "foreign_writer"
        self.fixture.write_role("evaluation:atlas")
        self.fixture.write_spec()
        with self.assertRaisesRegex(
            CaptureValidationError,
            "exact synthetic writer or handler expectation mismatch",
        ):
            assemble_exact_runtime_proof(self.fixture.spec.resolve())

    def test_rejects_omitted_required_claim_limitation(self) -> None:
        value = json.loads(self.fixture.spec.read_text(encoding="utf-8"))
        value["claim_limitations"].pop()
        material = dict(value)
        material.pop("spec_id")
        value["spec_id"] = PROOF_SPEC_PREFIX + canonical_json_sha256(material)
        self.fixture.spec.write_bytes(canonical_json_bytes(value) + b"\n")
        with self.assertRaisesRegex(
            CaptureValidationError,
            "required set",
        ):
            assemble_exact_runtime_proof(self.fixture.spec.resolve())

    def test_rejects_symlinked_artifact(self) -> None:
        atlas_path = self.fixture.paths["evaluation:atlas"]
        target = self.root / "atlas-real.json"
        atlas_path.replace(target)
        atlas_path.symlink_to(target)
        self.fixture.write_spec()
        with self.assertRaisesRegex(CaptureValidationError, "must not be a symlink"):
            assemble_exact_runtime_proof(self.fixture.spec.resolve())

    def test_cli_publishes_only_after_full_admission(self) -> None:
        script = MODULE_ROOT / "tools" / "assemble_worldgen_observatory_proof.py"
        completed = subprocess.run(
            [sys.executable, str(script), "--spec", str(self.fixture.spec.resolve())],
            check=True,
            capture_output=True,
            text=True,
        )
        report = json.loads(completed.stdout)
        proof = json.loads(self.fixture.output.read_text(encoding="utf-8"))
        self.assertEqual(proof["proof_id"], report["proof_id"])
        self.assertEqual("passed", report["proof_state"])


if __name__ == "__main__":
    unittest.main()
