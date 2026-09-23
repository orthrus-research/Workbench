#!/usr/bin/env python3

"""A01 conformance tests for release, application, history, and proof."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any
import unittest

from jsonschema import Draft202012Validator

from _support import SCHEMA_ROOT, SOURCE_ROOT, WORKBENCH_ROOT

REPO_ROOT = WORKBENCH_ROOT
BLUEPRINTS_TOOLS = SOURCE_ROOT

if str(BLUEPRINTS_TOOLS) not in sys.path:
    sys.path.insert(0, str(BLUEPRINTS_TOOLS))

from workbench_blueprints import lifecycle  # noqa: E402
from workbench_blueprints import planner  # noqa: E402
from workbench_blueprints import simulation  # noqa: E402
from workbench_blueprints import standards  # noqa: E402
import test_simulation as simulation_fixture  # noqa: E402


class LifecycleTest(unittest.TestCase):

    def setUp(self) -> None:
        self.fixture = simulation_fixture.SimulationTest()
        self.fixture.setUp()
        self.local_root = (
            self.fixture.repository / ".workbench/blueprints"
        )
        self.artifact_store = lifecycle.ArtifactStore(
            self.local_root / "release"
        )
        self.history_store = lifecycle.HistoryStore(
            self.local_root / "history"
        )

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def _engine(
        self,
        *,
        mutation_hook: lifecycle.MutationHook | None = None,
    ) -> lifecycle.LifecycleEngine:
        return lifecycle.LifecycleEngine(
            registry_root=self.fixture.registry,
            asset_root=REPO_ROOT,
            ledger_path=self.fixture.ledger,
            target_repository=self.fixture.repository,
            sealed_store=self.fixture.sealed_store,
            simulation_evidence_store=simulation.SimulationEvidenceStore(
                self.fixture.evidence_root
            ),
            artifact_store=self.artifact_store,
            history_store=self.history_store,
            mutation_hook=mutation_hook,
        )

    def _prepare(
        self,
        output_mode: str,
        *,
        failed_compile: bool = False,
    ) -> tuple[
        lifecycle.LifecycleEngine,
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
    ]:
        intake = copy.deepcopy(self.fixture.intake)
        intake["output_mode"] = output_mode
        intake["consent"]["allow_direct_apply"] = output_mode == "direct-apply"
        planning_result = self.fixture._planner().execute(
            intake,
            self.fixture.target,
            self.fixture.planning_evidence,
        )
        if failed_compile:
            original_compile = self.fixture.tools["synthetic-compile"]
            self.fixture.tools["synthetic-compile"] = b"#!/bin/sh\nexit 7\n"
            environment = self.fixture._environment()
        else:
            environment = copy.deepcopy(self.fixture.environment)
        try:
            simulation_result = self.fixture._simulator().execute(
                planning_result,
                intake=intake,
                target_manifest=self.fixture.target,
                planning_evidence=self.fixture.planning_evidence,
                environment_lock=environment,
            )
        finally:
            if failed_compile:
                self.fixture.tools["synthetic-compile"] = original_compile
        return self._engine(), planning_result, simulation_result, environment

    def _release(
        self,
        output_mode: str,
    ) -> tuple[lifecycle.LifecycleEngine, dict[str, Any]]:
        engine, planning_result, simulation_result, environment = self._prepare(
            output_mode
        )
        result = engine.release(
            planning_result,
            simulation_result,
            target_manifest=self.fixture.target,
            environment_lock=environment,
        )
        return engine, result

    def _released_transaction_context(
        self,
        engine: lifecycle.LifecycleEngine,
        released: dict[str, Any],
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build a self-consistent admitted release for transaction-only cases."""
        result = copy.deepcopy(released)
        public_operations = [
            {
                "ordinal": row["ordinal"],
                "operation": row["operation"],
                "path": row["path"],
                "content_sha256": (
                    None if row["after"] is None else row["after"]["sha256"]
                ),
            }
            for row in operations
        ]
        operations_sha256 = lifecycle._digest_json(public_operations)
        plan = result["plan"]
        plan["operations"] = public_operations
        plan["plan_id"] = lifecycle._identity(
            "blueprints-plan:sha256:", plan, "plan_id"
        )
        candidate = result["candidate"]
        candidate["plan_id"] = plan["plan_id"]
        candidate["content_manifest_sha256"] = operations_sha256
        candidate["candidate_id"] = lifecycle._identity(
            "blueprints-candidate:sha256:", candidate, "candidate_id"
        )
        simulation_record = result["run"]["simulation"]
        simulation_record["candidate_id"] = candidate["candidate_id"]
        simulation_record["simulation_id"] = lifecycle._identity(
            "blueprints-simulation:sha256:",
            simulation_record,
            "simulation_id",
        )
        run = lifecycle._base_run(
            result["request"], plan, candidate, simulation_record
        )
        bundle = {
            "schema_version": 1,
            "format": "susy-blueprints-release-bundle-v1",
            "contract_id": lifecycle.ENGINE_CONTRACT_ID,
            "candidate_id": candidate["candidate_id"],
            "target_state_id": run["target_state_id"],
            "operations_sha256": operations_sha256,
            "operations": operations,
        }
        lifecycle._validate_release_bundle(bundle)
        instructions = lifecycle._instructions(bundle)
        direct_diff = lifecycle._direct_diff(bundle)
        bundle_locator = self.artifact_store.put_json(bundle)
        instructions_locator = self.artifact_store.put_json(instructions)
        direct_diff_locator = self.artifact_store.put_json(direct_diff)
        patch_projection = {
            "bundle_sha256": bundle_locator.rsplit(":", 1)[1],
            "operations_sha256": operations_sha256,
        }
        patch_projection["patch_id"] = lifecycle._identity(
            "blueprints-patch:sha256:", patch_projection, "patch_id"
        )
        release = {
            "candidate_id": candidate["candidate_id"],
            "simulation_id": simulation_record["simulation_id"],
            "operations_sha256": operations_sha256,
            "projections": {
                "instructions": {
                    "manifest_sha256": instructions_locator.rsplit(":", 1)[1],
                    "operations_sha256": operations_sha256,
                },
                "patch": patch_projection,
                "direct_diff": {
                    "diff_sha256": direct_diff_locator.rsplit(":", 1)[1],
                    "operations_sha256": operations_sha256,
                },
            },
            "released_sequence": len(run["events"]),
        }
        release["release_id"] = lifecycle._identity(
            "blueprints-release:sha256:", release, "release_id"
        )
        run["release"] = copy.deepcopy(release)
        lifecycle._append_event(
            run,
            "generate",
            "released",
            "succeeded",
            [release["release_id"], patch_projection["patch_id"]],
        )
        result.update(
            {
                "run": run,
                "plan": plan,
                "candidate": candidate,
                "release": release,
                "bundle": bundle,
                "bundle_locator": bundle_locator,
                "release_locator": self.artifact_store.put_json(release),
                "projection_locators": {
                    "instructions": instructions_locator,
                    "patch": bundle_locator,
                    "direct_diff": direct_diff_locator,
                },
                "delivery": {
                    "output_mode": "direct-apply",
                    "artifact": direct_diff,
                },
                "additional_history_artifacts": [],
            }
        )
        result["proof_artifacts"] = engine._proof_artifacts(result)
        return result

    def test_new_schemas_are_valid(self) -> None:
        for name in (
            "blueprints-release-bundle-v1.schema.json",
            "blueprints-history-manifest-v1.schema.json",
            "blueprints-proof-export-v1.schema.json",
        ):
            schema = json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)

    def test_instructions_release_is_exact_deterministic_and_non_mutating(
        self,
    ) -> None:
        before = planner.capture_target_state(
            self.fixture.repository, "pack"
        )
        engine, first = self._release("instructions")
        self.assertEqual(first["run"]["state"], "released")
        self.assertEqual(first["proof"]["closure"], "release-validated")
        self.assertIsNone(first["proof"]["export"])
        self.assertEqual(
            first["release"]["released_sequence"],
            first["run"]["events"][-1]["sequence"],
        )
        operation_digests = {
            first["release"]["operations_sha256"],
            *[
                value["operations_sha256"]
                for value in first["release"]["projections"].values()
            ],
        }
        self.assertEqual(len(operation_digests), 1)
        steps = first["delivery"]["artifact"]["steps"]
        self.assertEqual(
            [row["path"] for row in steps],
            [
                "groovy/material/syntheticium.groovy",
                "groovy/material/syntheticiumFluid.groovy",
            ],
        )
        self.assertTrue(all(row["content_utf8"] for row in steps))
        self.assertEqual(
            before,
            planner.capture_target_state(self.fixture.repository, "pack"),
        )
        public_proof = json.dumps(first["proof"], sort_keys=True)
        self.assertNotIn("sealed_locator", public_proof)
        self.assertNotIn("content_base64", public_proof)
        self.assertNotIn(".workbench", public_proof)
        private = [
            row
            for row in first["proof"]["artifacts"]
            if row["privacy"] == "local-private"
        ]
        self.assertTrue(private)
        self.assertTrue(
            all(not row["included_in_export"] for row in private)
        )

        engine2, planning2, simulation2, environment2 = self._prepare(
            "instructions"
        )
        repeat = engine2.release(
            planning2,
            simulation2,
            target_manifest=self.fixture.target,
            environment_lock=environment2,
        )
        self.assertEqual(first["release"], repeat["release"])
        self.assertEqual(first["delivery"], repeat["delivery"])
        self.assertEqual(first["proof"], repeat["proof"])

    def test_patch_release_and_portable_export_close_without_private_data(
        self,
    ) -> None:
        engine, released = self._release("patch-bundle")
        exported = engine.export_proof(released)
        self.assertEqual(exported["run"]["state"], "released")
        self.assertEqual(
            exported["run"]["events"][-1]["phase"], "export-proof"
        )
        proof = exported["proof"]
        self.assertEqual(proof["closure"], "release-validated")
        self.assertTrue(proof["export"]["excludes_private"])
        self.assertEqual(
            proof["export"]["manifest_sha256"],
            hashlib.sha256(
                standards.canonical_json(
                    exported["export_bundle"]["manifest"]
                ).encode("utf-8")
            ).hexdigest(),
        )
        exported_ids = set(proof["export"]["artifact_ids"])
        self.assertEqual(
            exported_ids,
            {
                row["artifact_id"]
                for row in proof["artifacts"]
                if row["privacy"] == "exportable"
            },
        )
        portable = json.dumps(exported["export_bundle"], sort_keys=True)
        payload_ids = {
            row["artifact_id"]
            for row in exported["export_bundle"]["artifacts"]
        }
        self.assertNotIn("simulation-evidence", payload_ids)
        self.assertNotIn("sealed-candidate", payload_ids)
        self.assertNotIn("sealed_locator", portable)
        self.assertIn("patch-bundle", portable)

    def test_failed_simulation_and_invalidation_release_no_bytes(self) -> None:
        engine, planning_result, failed, environment = self._prepare(
            "instructions", failed_compile=True
        )
        result = engine.release(
            planning_result,
            failed,
            target_manifest=self.fixture.target,
            environment_lock=environment,
        )
        self.assertEqual(result["run"]["state"], "simulation-failed")
        self.assertIsNone(result["release"])
        self.assertIsNone(result["delivery"])
        self.assertIsNone(result["proof"])

        engine2, planning2, passing, environment2 = self._prepare(
            "instructions"
        )
        invalidated = engine2.release(
            planning2,
            passing,
            target_manifest=self.fixture.target,
            environment_lock=environment2,
            invalidated_ids=[planning2["candidate"]["candidate_id"]],
        )
        self.assertEqual(invalidated["run"]["state"], "simulated")
        self.assertEqual(
            invalidated["run"]["events"][-1]["result"], "failed"
        )
        self.assertIsNone(invalidated["delivery"])

    def test_stale_release_exposes_no_candidate(self) -> None:
        engine, planning_result, sim_result, environment = self._prepare(
            "instructions"
        )
        (self.fixture.repository / "README.md").write_text(
            "release drift\n", encoding="utf-8"
        )
        result = engine.release(
            planning_result,
            sim_result,
            target_manifest=self.fixture.target,
            environment_lock=environment,
        )
        self.assertIsNone(result["release"])
        self.assertIsNone(result["delivery"])
        self.assertEqual(result["diagnostics"], ["BPA135_STALE_TARGET"])
        self.assertFalse(
            (self.fixture.repository / "groovy/material/syntheticium.groovy").exists()
        )

    def test_authority_drift_after_simulation_is_rejected(self) -> None:
        engine, planning_result, sim_result, environment = self._prepare(
            "instructions"
        )
        ledger = json.loads(self.fixture.ledger.read_text(encoding="utf-8"))
        ledger["ledger_id"] = "blueprints-allocation-ledger:sha256:" + "0" * 64
        self.fixture.ledger.write_text(
            json.dumps(ledger), encoding="utf-8"
        )
        with self.assertRaisesRegex(
            (lifecycle.LifecycleDiagnostic, simulation.SimulationDiagnostic),
            "AUTHORITY",
        ):
            engine.release(
                planning_result,
                sim_result,
                target_manifest=self.fixture.target,
                environment_lock=environment,
            )

    def test_direct_apply_verify_and_target_proof_export(self) -> None:
        engine, released = self._release("direct-apply")
        baseline_index = {
            row["path"]: row["index_sha256"]
            for row in self.fixture.target["entries"]
        }
        applied = engine.apply(released)
        self.assertEqual(applied["run"]["state"], "applied")
        self.assertEqual(applied["application"]["status"], "applied")
        self.assertTrue(applied["application"]["atomic"])
        self.assertEqual(applied["application"]["rollback"], "not-needed")
        current = planner.capture_target_state(self.fixture.repository, "pack")
        current_rows = {row["path"]: row for row in current["entries"]}
        for path in (
            "groovy/material/syntheticium.groovy",
            "groovy/material/syntheticiumFluid.groovy",
        ):
            self.assertIn(path, current_rows)
            self.assertIsNone(current_rows[path]["index_sha256"])
        for path, index_sha in baseline_index.items():
            self.assertEqual(current_rows[path]["index_sha256"], index_sha)
        verified = engine.verify(
            applied,
            post_checks={
                "synthetic-registration-check": lambda root: (
                    (root / "groovy/material/syntheticium.groovy").is_file(),
                    {"registration": "present"},
                )
            },
        )
        self.assertEqual(verified["run"]["state"], "verified")
        self.assertEqual(verified["verification"]["status"], "passed")
        self.assertEqual(verified["proof"]["closure"], "target-verified")
        self.assertEqual(
            verified["proof"]["target"]["verified_target_state_id"],
            applied["application"]["post_target"]["target_state_id"],
        )

        exported = engine.export_proof(verified)
        self.assertEqual(exported["proof"]["closure"], "target-verified")
        self.assertEqual(
            exported["proof"]["run_id"], exported["run"]["run_id"]
        )
        private_ids = {
            row["artifact_id"]
            for row in exported["proof"]["artifacts"]
            if row["privacy"] == "local-private"
        }
        payload_ids = {
            row["artifact_id"]
            for row in exported["export_bundle"]["artifacts"]
        }
        self.assertTrue(private_ids.isdisjoint(payload_ids))

    def test_stale_application_is_rejected_without_candidate_mutation(self) -> None:
        engine, released = self._release("direct-apply")
        (self.fixture.repository / "README.md").write_text(
            "application drift\n", encoding="utf-8"
        )
        observed = planner.capture_target_state(self.fixture.repository, "pack")
        rejected = engine.apply(released)
        self.assertEqual(rejected["run"]["state"], "application-rejected")
        self.assertEqual(rejected["application"]["status"], "rejected")
        self.assertFalse(rejected["application"]["atomic"])
        self.assertIsNone(rejected["application"]["post_target"])
        self.assertEqual(
            observed,
            planner.capture_target_state(self.fixture.repository, "pack"),
        )
        self.assertFalse(
            (self.fixture.repository / "groovy/material/syntheticium.groovy").exists()
        )

    def test_partial_application_rolls_back_exactly(self) -> None:
        _engine, released = self._release("direct-apply")
        before = planner.capture_target_state(self.fixture.repository, "pack")

        def fail_after_first(ordinal: int, _path: str) -> None:
            if ordinal == 0:
                raise RuntimeError("synthetic failure")

        engine = self._engine(mutation_hook=fail_after_first)
        rejected = engine.apply(released)
        self.assertEqual(rejected["application"]["rollback"], "succeeded")
        self.assertTrue(rejected["application"]["atomic"])
        self.assertEqual(rejected["run"]["state"], "application-rejected")
        self.assertEqual(
            before,
            planner.capture_target_state(self.fixture.repository, "pack"),
        )
        self.assertFalse(
            (self.history_store.root / "active-transaction.json").exists()
        )
        self.assertFalse(
            (self.history_store.root / "active-transaction.lock").exists()
        )

    def test_update_and_delete_apply_from_one_bound_transaction(self) -> None:
        engine, released = self._release("direct-apply")
        baseline = {
            row["path"]: row for row in self.fixture.target["entries"]
        }
        update_path = "groovy/material/SyntheticMaterial.groovy"
        delete_path = "groovy/material/fixtures/runtime.txt"
        update_before = lifecycle._content_record(
            baseline[update_path]["kind"],
            baseline[update_path]["mode"],
            (self.fixture.repository / update_path).read_bytes(),
        )
        delete_before = lifecycle._content_record(
            baseline[delete_path]["kind"],
            baseline[delete_path]["mode"],
            (self.fixture.repository / delete_path).read_bytes(),
        )
        operations = [
            {
                "ordinal": 0,
                "operation": "update",
                "path": update_path,
                "before": update_before,
                "after": lifecycle._content_record(
                    "file", update_before["mode"], b"updated material\n"
                ),
            },
            {
                "ordinal": 1,
                "operation": "delete",
                "path": delete_path,
                "before": delete_before,
                "after": None,
            },
        ]
        transaction = self._released_transaction_context(
            engine, released, operations
        )
        applied = engine.apply(transaction)
        self.assertEqual(applied["application"]["status"], "applied")
        self.assertEqual(
            (self.fixture.repository / update_path).read_bytes(),
            b"updated material\n",
        )
        self.assertFalse((self.fixture.repository / delete_path).exists())
        observed = {
            row["path"]: row
            for row in planner.capture_target_state(
                self.fixture.repository, "pack"
            )["entries"]
        }
        self.assertEqual(observed[delete_path]["kind"], "deleted")
        self.assertEqual(
            observed[update_path]["worktree_sha256"],
            hashlib.sha256(b"updated material\n").hexdigest(),
        )

    def test_rollback_failure_is_critical_and_retains_recovery_journal(
        self,
    ) -> None:
        _engine, released = self._release("direct-apply")

        def corrupt_unrelated_then_fail(ordinal: int, _path: str) -> None:
            if ordinal == 0:
                (self.fixture.repository / "README.md").write_text(
                    "external mutation during transaction\n", encoding="utf-8"
                )
                raise RuntimeError("synthetic failure")

        engine = self._engine(mutation_hook=corrupt_unrelated_then_fail)
        rejected = engine.apply(released)
        self.assertEqual(rejected["application"]["rollback"], "failed")
        self.assertFalse(rejected["application"]["atomic"])
        self.assertEqual(
            rejected["diagnostics"], ["BPA143_ROLLBACK_FAILED"]
        )
        self.assertTrue(
            (self.history_store.root / "active-transaction.json").exists()
        )
        self.assertTrue(
            (self.history_store.root / "active-transaction.lock").exists()
        )

    def test_verification_drift_and_side_effects_fail_closed(self) -> None:
        engine, released = self._release("direct-apply")
        applied = engine.apply(released)
        generated = (
            self.fixture.repository / "groovy/material/syntheticium.groovy"
        )
        generated.write_text("drift\n", encoding="utf-8")
        failed = engine.verify(applied)
        self.assertEqual(failed["run"]["state"], "verification-failed")
        self.assertEqual(failed["verification"]["status"], "failed")
        self.assertEqual(
            failed["diagnostics"], ["BPA147_VERIFICATION_FAILED"]
        )
        with self.assertRaisesRegex(
            lifecycle.LifecycleDiagnostic, "BPA148_EXPORT_NOT_ADMITTED"
        ):
            engine.export_proof(failed)

        # Use a fresh target to prove that read-only post-check enforcement is exact.
        self.tearDown()
        self.setUp()
        engine2, released2 = self._release("direct-apply")
        applied2 = engine2.apply(released2)

        def mutating_check(root: Path) -> tuple[bool, Any]:
            (root / "README.md").write_text(
                "verification mutation\n", encoding="utf-8"
            )
            return True, {"claimed": "pass"}

        side_effect = engine2.verify(
            applied2, post_checks={"mutating-check": mutating_check}
        )
        self.assertEqual(side_effect["verification"]["status"], "failed")
        self.assertEqual(
            side_effect["verification"]["checks"][-1]["check_id"],
            "verification-side-effect",
        )

    def test_tampered_release_bundle_is_rejected_before_mutation(self) -> None:
        engine, released = self._release("direct-apply")
        before = planner.capture_target_state(self.fixture.repository, "pack")
        tampered = copy.deepcopy(released["bundle"])
        tampered["operations"][0]["after"]["content_base64"] = "dGFtcGVy"
        tampered["operations"][0]["after"]["sha256"] = hashlib.sha256(
            b"tamper"
        ).hexdigest()
        tampered["operations_sha256"] = lifecycle._digest_json(
            [
                {
                    "ordinal": row["ordinal"],
                    "operation": row["operation"],
                    "path": row["path"],
                    "content_sha256": (
                        None
                        if row["after"] is None
                        else row["after"]["sha256"]
                    ),
                }
                for row in tampered["operations"]
            ]
        )
        released["bundle_locator"] = self.artifact_store.put_json(tampered)
        with self.assertRaisesRegex(
            lifecycle.LifecycleDiagnostic, "BPA137_RELEASE_DRIFT"
        ):
            engine.apply(released)
        self.assertEqual(
            before,
            planner.capture_target_state(self.fixture.repository, "pack"),
        )

    def test_bulky_retention_prunes_payload_but_keeps_compact_manifest(
        self,
    ) -> None:
        _engine, released = self._release("instructions")
        bulky = self.history_store.artifacts.put_bytes(b"bulky build log")
        descriptor = lifecycle.HistoryStore.descriptor(
            "synthetic-build-log",
            "log",
            bulky.rsplit(":", 1)[1],
            "local-private",
            retention_class="bulky",
        )
        manifest_sha, manifest_locator = self.history_store.record(
            released["run"],
            [
                *self._engine()._history_descriptors(released),
                descriptor,
            ],
        )
        successor_sha, successor_locator = self.history_store.prune_bulky(
            manifest_locator
        )
        self.assertNotEqual(manifest_sha, successor_sha)
        successor = self.history_store.artifacts.read_json(successor_locator)
        row = next(
            item
            for item in successor["artifacts"]
            if item["artifact_id"] == "synthetic-build-log"
        )
        self.assertFalse(row["retained"])
        with self.assertRaisesRegex(
            lifecycle.LifecycleDiagnostic, "BPA111_ARTIFACT_MISSING"
        ):
            self.history_store.artifacts.read_bytes(bulky)


if __name__ == "__main__":
    unittest.main()
