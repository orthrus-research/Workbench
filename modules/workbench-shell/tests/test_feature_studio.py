"""Focused Feature Studio composition tests."""

from __future__ import annotations

from copy import deepcopy
import errno
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

from jsonschema import Draft202012Validator


MODULE_ROOT = Path(__file__).resolve().parents[1]
SUITE_ROOT = MODULE_ROOT.parents[1]
for source in (
    MODULE_ROOT / "src",
    SUITE_ROOT / "modules/project-intelligence/src",
    SUITE_ROOT / "modules/atlas/src",
    SUITE_ROOT / "modules/blueprints/src",
    SUITE_ROOT / "modules/crucible/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
sys.path.insert(0, str(Path(__file__).parent))

from packwiz_v2_fixture import seal_packwiz_v2_receipt  # noqa: E402

from workbench_shell.feature_studio import (  # noqa: E402
    FeatureStudioError,
    _canonical_bytes,
    _content_id,
    execute_feature_request,
    explain_feature,
    export_feature,
    feature_studio_capabilities,
    inspect_feature,
    inspect_retained_feature,
    plan_feature,
    validate_feature_request,
    validate_feature_result,
    verify_feature,
)
from workbench_shell.material_fluid_flow import MaterialFluidFlowError  # noqa: E402


PATHS = (
    "groovy/material/PetrochemistryMaterials.groovy",
    "groovy/material/SuSyMaterials.groovy",
    "resources/langfiles/lang/en_us.lang",
)


def _plan(workspace: Path) -> dict:
    operations = []
    for index, relative in enumerate(PATHS, start=1):
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        before = f"before {index}\n".encode()
        target.write_bytes(before)
        after = f"after {index}\n".encode()
        operations.append(
            {
                "operation": "update",
                "path": relative,
                "before_sha256": sha256(before).hexdigest(),
                "content_sha256": sha256(after).hexdigest(),
                "size": len(after),
                "diff": (
                    f"--- a/{relative}\n+++ b/{relative}\n"
                    f"@@ -1 +1 @@\n-before {index}\n+after {index}\n"
                ),
            }
        )
    parameters = {
        "color": "0x425d73",
        "material_id": 20008,
        "name": "Pilot Coolant",
        "registry_name": "pilot_coolant",
        "symbol_name": "PilotCoolant",
        "translation": "Pilot Coolant",
    }
    return {
        "format": "workbench-material-fluid-flow-plan-v2",
        "schema_version": 2,
        "plan_id": "sha256:" + "1" * 64,
        "state": "ready",
        "source": {
            "workspace_uri": workspace.as_uri(),
            "revision": "a" * 40,
        },
        "source_snapshot": {"snapshot_id": "sha256:" + "2" * 64},
        "blueprint": {
            "format": "workbench-material-backed-fluid-plan-v1",
            "plan_id": "sha256:" + "3" * 64,
            "profile_family_id": "workbench-pack:supersymmetry",
            "blueprint": {"effective_parameters": parameters},
        },
        "operations": operations,
        "execution": {
            "launcher": "prism",
            "target_policy": "fresh-unique-disposable-projection",
        },
        "assertions": {
            key: {"state": "pending", "meaning": key}
            for key in (
                "fml_client_load",
                "groovy_compilation",
                "material_registration",
                "fluid_registration",
                "localization",
            )
        },
        "limitations": ["owner limitation"],
    }


def _owner_result(attempt: str, root: Path, workspace: Path, owner_plan: dict) -> dict:
    assertions = {
        key: {"state": "observed", "meaning": key}
        for key in (
            "fluid_registration",
            "fml_client_load",
            "groovy_compilation",
            "localization",
            "material_registration",
        )
    }
    retained = root / attempt
    retained.mkdir()
    receipt_path = retained / "receipt.json"
    session_path = retained / "runtime-observation-v1.json"
    session = {
        "format": "workbench-runtime-observation-session-v1",
        "session_id": "sha256:" + attempt * 64,
        "started_at": "2026-08-10T00:00:00.000Z",
        "observed_at": "2026-08-10T00:00:12.500Z",
        "launch": {
            "process_observation": {
                "attached_at": "2026-08-10T00:00:02.000Z",
                "observed_at": "2026-08-10T00:00:12.000Z",
            }
        },
    }
    session_path.write_text(json.dumps(session, indent=2, sort_keys=True) + "\n")
    instance_root = retained / "instance"
    payload_root = instance_root / ".minecraft"
    payload_root.mkdir(parents=True)
    materialization_path = (
        retained / "receipts/packwiz-materialization-v2.json"
    )
    materialization_path.parent.mkdir()
    materialization = seal_packwiz_v2_receipt({
        "state": "materialized",
        "readiness": "pack-payload-installed",
        "plan_id": "sha256:" + "7" * 64,
        "request": {"side": "client", "launcher": "prism"},
        "workspace": {"root_uri": workspace.as_uri()},
        "payload": {
            "tree_sha256": "sha256:" + "6" * 64,
            "file_count": 0,
            "total_bytes": 0,
        },
        "target": {
            "instance_root_uri": instance_root.as_uri(),
            "receipt_uri": materialization_path.as_uri(),
        },
    })
    materialization_path.write_text(
        json.dumps(materialization, indent=2, sort_keys=True) + "\n"
    )
    materialization_id = materialization["materialization_id"]

    final_path = retained / "runtime-launch-v3.json"
    final_path.write_text(
        json.dumps(
            {
                "format": "workbench-runtime-launch-receipt-v3",
                "launch_id": "sha256:" + "9" * 64,
                "plan_id": "sha256:" + "7" * 64,
                "materialization_id": materialization_id,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    stage_path = retained / "blueprint-stage-v2.json"
    stage_path.write_text(
        json.dumps(
            {
                "format": "workbench-blueprint-stage-receipt-v2",
                "schema_version": 2,
                "stage_id": "sha256:" + "3" * 64,
                "blueprint": {
                    "candidate_id": (
                        "blueprints-convention-candidate:sha256:" + "4" * 64
                    )
                },
                "target": {"tracked_tree_id": "5" * 40},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    receipt = {
        "format": "workbench-material-fluid-flow-receipt-v2",
        "schema_version": 2,
        "receipt_id": "",
        "attempt_id": "uuid:" + attempt * 32,
        "state": "complete",
        "outcome": "runtime-completed",
        "plan": {
            "plan_id": owner_plan["plan_id"],
            "blueprint_plan_id": owner_plan["blueprint"]["plan_id"],
            "effective_parameters": owner_plan["blueprint"]["blueprint"]["effective_parameters"],
        },
        "source": owner_plan["source"],
        "blueprint_stage": {
            "state": "staged",
            "outcome": "reused",
            "stage_id": "sha256:" + "3" * 64,
            "candidate_id": "blueprints-convention-candidate:sha256:" + "4" * 64,
            "revision": "6" * 40,
            "tracked_tree_id": "5" * 40,
            "workspace_uri": (retained / "workspace").as_uri(),
            "receipt_uri": stage_path.as_uri(),
        },
        "runtime": {
            "receipt_uri": session_path.as_uri(),
            "session_id": "sha256:" + attempt * 64,
            "runtime_plan_id": "sha256:" + "7" * 64,
            "materialization_id": materialization_id,
            "final_launch_id": "sha256:" + "9" * 64,
        },
        "profile_observation": {
            "format": "workbench-supersymmetry-material-fluid-assessment-v2",
            "assessment_id": "workbench-atlas-material-fluid-assessment:sha256:" + attempt * 64,
            "state": "observed",
            "profile": {
                "platform_profile_id": "workbench-platform:cleanroom:provisional",
                "pack_profile_id": "workbench-pack:supersymmetry",
            },
            "checks": {
                "color_rgb": True,
                "fluid_name": True,
                "forge_registry_roundtrip": True,
                "has_flammable_flag": True,
                "has_fluid_property": True,
                "localized_name": True,
                "manager_frozen": True,
                "material_id": True,
                "material_resource": True,
            },
            "observed": {
                "color_rgb": 4349299,
                "fluid_name": "pilot_coolant",
                "forge_registry_roundtrip": True,
                "has_flammable_flag": True,
                "has_fluid_property": True,
                "localized_name": "Pilot Coolant",
                "manager_phase": "FROZEN",
                "material_id": 20008,
                "material_resource": "susy:pilot_coolant",
            },
            "failed_checks": [],
            "crucible_snapshot": {
                "snapshot_id": "workbench-crucible-stage-snapshot:sha256:" + attempt * 64,
            },
        },
        "assertions": assertions,
        "target": {"receipt_uri": receipt_path.as_uri()},
        "limitations": ["exact projection only"],
    }
    identity = {key: value for key, value in receipt.items() if key != "receipt_id"}
    receipt["receipt_id"] = "sha256:" + sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return {
        "format": "workbench-material-fluid-flow-result-v2",
        "schema_version": 2,
        "outcome": "runtime-completed",
        "receipt": receipt,
        "runtime_result": {"attempt": attempt},
    }


def _failed_owner_result(
    attempt: str,
    root: Path,
    workspace: Path,
    owner_plan: dict,
) -> dict:
    result = _owner_result(attempt, root, workspace, owner_plan)
    receipt = result["receipt"]
    receipt["state"] = "incomplete"
    receipt["outcome"] = "failed"
    receipt["blueprint_stage"] = None
    receipt["runtime"] = {
        "state": "failed",
        "error": {
            "kind": "RuntimeObserveError",
            "message": "fixture launch failure",
        },
    }
    receipt["profile_observation"] = None
    receipt["assertions"] = {
        key: {"state": "not-observed", "meaning": key}
        for key in (
            "fluid_registration",
            "fml_client_load",
            "groovy_compilation",
            "localization",
            "material_registration",
        )
    }
    identity = {key: value for key, value in receipt.items() if key != "receipt_id"}
    receipt["receipt_id"] = "sha256:" + sha256(_canonical_bytes(identity)).hexdigest()
    receipt_path = Path(urlparse(receipt["target"]["receipt_uri"]).path)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    result["outcome"] = "failed"
    result["runtime_result"] = None
    return result


def _validated_runtime(
    receipt: dict,
    _reviewed_plan: dict,
    _suite_root: Path | str,
    *,
    receipt_uri: str | None = None,
) -> dict:
    session_path = Path(
        urlparse(receipt["runtime"]["receipt_uri"]).path
    )
    final_path = session_path.parent / "runtime-launch-v3.json"
    materialization_path = (
        session_path.parent / "receipts/packwiz-materialization-v2.json"
    )
    stage_path = Path(urlparse(receipt["blueprint_stage"]["receipt_uri"]).path)
    return {
        "blueprint_stage": {
            "receipt": json.loads(stage_path.read_text()),
            "receipt_uri": stage_path.as_uri(),
        },
        "session": json.loads(session_path.read_text()),
        "session_uri": session_path.as_uri(),
        "final_launch_receipt": json.loads(final_path.read_text()),
        "final_launch_uri": final_path.as_uri(),
        "materialization_receipt": json.loads(materialization_path.read_text()),
        "materialization_uri": materialization_path.as_uri(),
    }


class FeatureStudioTests(unittest.TestCase):
    def setUp(self):
        self.result_validator = Draft202012Validator(
            json.loads(
                (MODULE_ROOT / "schemas/feature-studio-result-v2.schema.json").read_text()
            )
        )

    def test_read_only_operations_preserve_owner_plan(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            workspace = Path(temporary) / "source"
            workspace.mkdir()
            owner_plan = _plan(workspace)
            before = {path: (workspace / path).read_bytes() for path in PATHS}
            with patch(
                "workbench_shell.feature_studio.plan_material_fluid_trial",
                return_value=owner_plan,
            ):
                inspected = inspect_feature(
                    SUITE_ROOT, workspace, name="Pilot Coolant", color="0x425d73"
                )
                planned = plan_feature(
                    SUITE_ROOT, workspace, name="Pilot Coolant", color="0x425d73"
                )
                explained = explain_feature(
                    SUITE_ROOT, workspace, name="Pilot Coolant", color="0x425d73"
                )

            self.assertEqual(inspected["format"], "workbench-feature-studio-result-v2")
            for value in (inspected, planned, explained):
                self.result_validator.validate(value)
            self.assertEqual(planned["state"], "ready")
            self.assertEqual(len(planned["source_locations"]), 3)
            self.assertEqual(
                json.loads(planned["owner_artifacts"][0]["canonical_json"]),
                owner_plan,
            )
            self.assertTrue(explained["explanation"])
            self.assertEqual(
                before,
                {path: (workspace / path).read_bytes() for path in PATHS},
            )

    def test_export_is_exact_source_safe_and_plan_bound(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            workspace = root / "source"
            output = root / "exports" / "pilot"
            workspace.mkdir()
            output.parent.mkdir()
            owner_plan = _plan(workspace)
            before = {path: (workspace / path).read_bytes() for path in PATHS}
            with patch(
                "workbench_shell.feature_studio.plan_material_fluid_trial",
                return_value=owner_plan,
            ):
                result = export_feature(
                    SUITE_ROOT,
                    workspace,
                    name="Pilot Coolant",
                    color="0x425d73",
                    output_root=output,
                    expected_plan_id=owner_plan["plan_id"],
                )
                with self.assertRaisesRegex(FeatureStudioError, "changed after review"):
                    export_feature(
                        SUITE_ROOT,
                        workspace,
                        name="Pilot Coolant",
                        color="0x425d73",
                        output_root=root / "exports" / "stale",
                        expected_plan_id="sha256:" + "f" * 64,
                    )
                with self.assertRaisesRegex(FeatureStudioError, "overlap"):
                    export_feature(
                        SUITE_ROOT,
                        workspace,
                        name="Pilot Coolant",
                        color="0x425d73",
                        output_root=workspace / "export",
                        expected_plan_id=owner_plan["plan_id"],
                    )
                linked_parent = root / "linked-export-parent"
                linked_parent.symlink_to(output.parent, target_is_directory=True)
                with self.assertRaisesRegex(FeatureStudioError, "symbolic link"):
                    export_feature(
                        SUITE_ROOT,
                        workspace,
                        name="Pilot Coolant",
                        color="0x425d73",
                        output_root=linked_parent / "escaped",
                        expected_plan_id=owner_plan["plan_id"],
                    )
                crash_output = output.parent / "crash"
                with (
                    patch(
                        "workbench_shell.feature_studio._publish_directory_no_replace",
                        side_effect=OSError("injected crash boundary"),
                    ),
                    self.assertRaisesRegex(FeatureStudioError, "could not publish"),
                ):
                    export_feature(
                        SUITE_ROOT,
                        workspace,
                        name="Pilot Coolant",
                        color="0x425d73",
                        output_root=crash_output,
                        expected_plan_id=owner_plan["plan_id"],
                    )
                self.assertFalse(crash_output.exists())
                raced_output = output.parent / "raced"
                with (
                    patch(
                        "workbench_shell.feature_studio._publish_directory_no_replace",
                        side_effect=OSError(
                            errno.EEXIST,
                            "destination appeared",
                        ),
                    ),
                    self.assertRaisesRegex(
                        FeatureStudioError,
                        "destination appeared during publication",
                    ),
                ):
                    export_feature(
                        SUITE_ROOT,
                        workspace,
                        name="Pilot Coolant",
                        color="0x425d73",
                        output_root=raced_output,
                        expected_plan_id=owner_plan["plan_id"],
                    )
                self.assertFalse(raced_output.exists())
                self.assertFalse(
                    any(
                        child.name.startswith(".feature-studio-export-")
                        for child in output.parent.iterdir()
                    )
                )

            patch_bytes = "".join(row["diff"] for row in owner_plan["operations"]).encode()
            self.assertEqual((output / "feature.patch").read_bytes(), patch_bytes)
            receipt = json.loads((output / "receipt.json").read_text())
            self.result_validator.validate(result)
            self.assertEqual(receipt["patch_sha256"], sha256(patch_bytes).hexdigest())
            self.assertEqual(result["export"]["receipt_id"], receipt["receipt_id"])
            self.assertEqual(
                before,
                {path: (workspace / path).read_bytes() for path in PATHS},
            )

    def test_verification_retains_fresh_ids_but_has_stable_semantic_projection(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            workspace = Path(temporary) / "source"
            workspace.mkdir()
            owner_plan = _plan(workspace)
            results = [
                _owner_result("a", Path(temporary), workspace, owner_plan),
                _owner_result("b", Path(temporary), workspace, owner_plan),
            ]
            with (
                patch(
                    "workbench_shell.feature_studio.plan_material_fluid_trial",
                    return_value=owner_plan,
                ),
                patch(
                    "workbench_shell.feature_studio.execute_material_fluid_trial",
                    side_effect=deepcopy(results),
                ),
                patch(
                    "workbench_shell.feature_studio.validate_retained_material_fluid_success",
                    side_effect=_validated_runtime,
                ),
            ):
                first = verify_feature(
                    SUITE_ROOT,
                    workspace,
                    name="Pilot Coolant",
                    color="0x425d73",
                    launcher_executable="launcher",
                    launcher_root=Path(temporary) / "launcher",
                    expected_plan_id=owner_plan["plan_id"],
                )
                second = verify_feature(
                    SUITE_ROOT,
                    workspace,
                    name="Pilot Coolant",
                    color="0x425d73",
                    launcher_executable="launcher",
                    launcher_root=Path(temporary) / "launcher",
                    expected_plan_id=owner_plan["plan_id"],
                )
            self.assertNotEqual(first["result_id"], second["result_id"])
            self.result_validator.validate(first)
            self.result_validator.validate(second)
            self.assertEqual(
                first["semantic_equivalence"], second["semantic_equivalence"]
            )
            self.assertEqual(first["timings"][0]["value"], 12500)
            self.assertEqual(first["state"], "complete")
            self.assertEqual(
                first["semantic_equivalence"]["profile_semantics"]["checks"],
                second["semantic_equivalence"]["profile_semantics"]["checks"],
            )
            self.assertNotEqual(
                first["context"]["profile_assessment_id"],
                second["context"]["profile_assessment_id"],
            )

    def test_incomplete_verification_revalidates_resealed_owner_receipt(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            workspace = root / "source"
            workspace.mkdir()
            owner_plan = _plan(workspace)
            failed = _failed_owner_result("d", root, workspace, owner_plan)
            with (
                patch(
                    "workbench_shell.feature_studio.plan_material_fluid_trial",
                    return_value=owner_plan,
                ),
                patch(
                    "workbench_shell.feature_studio.execute_material_fluid_trial",
                    return_value=deepcopy(failed),
                ),
            ):
                result = verify_feature(
                    SUITE_ROOT,
                    workspace,
                    name="Pilot Coolant",
                    color="0x425d73",
                    launcher_executable="launcher",
                    launcher_root=root / "launcher",
                    expected_plan_id=owner_plan["plan_id"],
                )

            self.assertEqual("incomplete", result["state"])
            self.assertEqual(
                result,
                validate_feature_result(result, suite_root=SUITE_ROOT),
            )

            artifact = next(
                row
                for row in result["owner_artifacts"]
                if row["role"] == "material-fluid-receipt"
            )
            receipt = json.loads(artifact["canonical_json"])
            receipt["source"]["revision"] = "b" * 40
            identity = {
                key: value for key, value in receipt.items() if key != "receipt_id"
            }
            receipt["receipt_id"] = "sha256:" + sha256(
                _canonical_bytes(identity)
            ).hexdigest()
            canonical = _canonical_bytes(receipt)
            receipt_path = Path(urlparse(artifact["retained"]["uri"]).path)
            retained = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
            receipt_path.write_bytes(retained)
            artifact.update(
                {
                    "canonical_json": canonical.decode(),
                    "canonical_sha256": sha256(canonical).hexdigest(),
                    "canonical_size": len(canonical),
                    "retained": {
                        "uri": receipt_path.as_uri(),
                        "sha256": sha256(retained).hexdigest(),
                        "size": len(retained),
                    },
                }
            )
            material = {
                key: value for key, value in result.items() if key != "result_id"
            }
            result["result_id"] = _content_id("feature-studio-result", material)
            with self.assertRaisesRegex(
                FeatureStudioError,
                "incomplete owner receipt is invalid",
            ):
                validate_feature_result(result, suite_root=SUITE_ROOT)

    def test_inspect_selects_and_revalidates_a_retained_receipt_identity(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            workspace = root / "source"
            workspace.mkdir()
            owner_plan = _plan(workspace)
            owner_result = _owner_result("c", root, workspace, owner_plan)
            receipt = Path(owner_result["receipt"]["target"]["receipt_uri"].removeprefix("file://"))
            with patch(
                "workbench_shell.feature_studio.plan_material_fluid_trial",
                return_value=owner_plan,
            ), patch(
                "workbench_shell.feature_studio.validate_retained_material_fluid_success",
                side_effect=_validated_runtime,
            ):
                result = inspect_retained_feature(SUITE_ROOT, receipt)
            self.result_validator.validate(result)
            self.assertEqual(result["operation"], "inspect")
            self.assertEqual(result["state"], "complete")
            self.assertEqual(result["context"]["evidence_layers"]["runtime"], "observed")
            self.assertEqual(result["timings"][0]["value"], 12500)

            mutations = []
            pending_profile = deepcopy(result)
            pending_profile["context"]["platform_profile_state"] = "pending-verification"
            mutations.append(pending_profile)
            unbound_review = deepcopy(result)
            unbound_review["review_binding"]["state"] = "catalog-v2-bound"
            mutations.append(unbound_review)
            pending_runtime = deepcopy(result)
            pending_runtime["context"]["evidence_layers"]["runtime"] = "pending"
            mutations.append(pending_runtime)
            fake_export = deepcopy(result)
            fake_export["operation"] = "export"
            fake_export["export"] = None
            mutations.append(fake_export)
            impossible_plan = deepcopy(result)
            impossible_plan["operation"] = "plan"
            impossible_plan["state"] = "unavailable"
            mutations.append(impossible_plan)
            failed_check = deepcopy(result)
            failed_check["semantic_equivalence"]["profile_semantics"]["checks"][
                "material_id"
            ] = False
            mutations.append(failed_check)
            failed_assertion = deepcopy(result)
            failed_assertion["assertions"][0]["state"] = "failed"
            mutations.append(failed_assertion)
            retained_projection_drift = deepcopy(result)
            retained_only = next(
                row
                for row in retained_projection_drift["owner_artifacts"]
                if row["projection_encoding"] == "retained-owner-json-bytes"
            )
            retained_only["canonical_sha256"] = "f" * 64
            mutations.append(retained_projection_drift)
            for index, mutated in enumerate(mutations):
                with self.subTest(schema_mutation=index):
                    self.assertTrue(list(self.result_validator.iter_errors(mutated)))

    def test_result_validator_rejects_resealed_authority_and_export_forgery(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            workspace = root / "source"
            workspace.mkdir()
            owner_plan = _plan(workspace)
            owner_result = _owner_result("e", root, workspace, owner_plan)
            with (
                patch(
                    "workbench_shell.feature_studio.plan_material_fluid_trial",
                    return_value=owner_plan,
                ),
                patch(
                    "workbench_shell.feature_studio.execute_material_fluid_trial",
                    return_value=deepcopy(owner_result),
                ),
                patch(
                    "workbench_shell.feature_studio.validate_retained_material_fluid_success",
                    side_effect=_validated_runtime,
                ),
            ):
                baseline = verify_feature(
                    SUITE_ROOT,
                    workspace,
                    name="Pilot Coolant",
                    color="0x425d73",
                    launcher_executable="launcher",
                    launcher_root=root / "launcher",
                    expected_plan_id=owner_plan["plan_id"],
                )
                pending = plan_feature(
                    SUITE_ROOT,
                    workspace,
                    name="Pilot Coolant",
                    color="0x425d73",
                )

            validated_owner = _validated_runtime(
                owner_result["receipt"], owner_plan, SUITE_ROOT
            )

            def owner_validate_stub(
                receipt: dict,
                _reviewed_plan: dict,
                _suite_root: Path | str,
                *,
                receipt_uri: str | None = None,
            ) -> dict:
                identity = {
                    key: item
                    for key, item in receipt.items()
                    if key != "receipt_id"
                }
                expected_id = "sha256:" + sha256(
                    _canonical_bytes(identity)
                ).hexdigest()
                if receipt.get("receipt_id") != expected_id:
                    raise MaterialFluidFlowError("receipt identity is invalid")
                if receipt.get("target", {}).get("receipt_uri") != receipt_uri:
                    raise MaterialFluidFlowError("receipt target is rebound")
                return validated_owner

            with patch(
                "workbench_shell.feature_studio.validate_retained_material_fluid_success",
                side_effect=owner_validate_stub,
            ):
                self.assertEqual(
                    validate_feature_result(baseline, suite_root=SUITE_ROOT),
                    baseline,
                )

            def reseal(value: dict) -> dict:
                semantic = value["semantic_equivalence"]
                semantic_material = {
                    key: item
                    for key, item in semantic.items()
                    if key != "projection_id"
                }
                semantic["projection_id"] = _content_id(
                    "feature-studio-semantic-projection", semantic_material
                )
                material = {
                    key: item for key, item in value.items() if key != "result_id"
                }
                value["result_id"] = _content_id("feature-studio-result", material)
                return value

            mutations: list[tuple[str, dict]] = []

            foreign_source = deepcopy(baseline)
            foreign_source["source_locations"][0]["workspace_uri"] = (
                root / "foreign"
            ).as_uri()
            foreign_source["source_locations"][0]["relative_path"] = "foreign.groovy"
            foreign_source["source_locations"][0]["source_sha256"] = "f" * 64
            mutations.append(("foreign source", reseal(foreign_source)))

            foreign_feature = deepcopy(baseline)
            replacement = {
                **foreign_feature["feature"],
                "name": "Foreign Material",
                "registry_name": "foreign_material",
                "material_id": 29999,
                "symbol_name": "ForeignMaterial",
                "translation": "Foreign Material",
                "planned_material_resource": "foreign:foreign_material",
                "planned_fluid_registry_name": "foreign_material",
            }
            foreign_feature["feature"] = replacement
            foreign_feature["semantic_equivalence"]["feature"] = deepcopy(replacement)
            mutations.append(("foreign feature", reseal(foreign_feature)))

            blocked_plan = deepcopy(baseline)
            blocked_plan["plan"]["state"] = "blocked"
            mutations.append(("blocked plan", reseal(blocked_plan)))

            foreign_launcher = deepcopy(baseline)
            foreign_launcher["context"]["runtime_launcher"] = "multimc"
            mutations.append(("foreign launcher", reseal(foreign_launcher)))

            fake_export = deepcopy(baseline)
            fake_export["operation"] = "export"
            fake_export["export"] = {
                "state": "written",
                "receipt_id": "feature-studio-export-receipt:sha256:" + "f" * 64,
                "directory_uri": (root / "missing-export").as_uri(),
                "patch_uri": (root / "missing-export" / "feature.patch").as_uri(),
                "receipt_uri": (root / "missing-export" / "receipt.json").as_uri(),
                "patch_sha256": "f" * 64,
                "patch_size": 1,
            }
            mutations.append(("forged export", reseal(fake_export)))

            forged_session = deepcopy(baseline)
            session_artifact = next(
                row
                for row in forged_session["owner_artifacts"]
                if row["role"] == "runtime-observation-session"
            )
            session_value = json.loads(session_artifact["canonical_json"])
            session_value["started_at"] = "2000-01-01T00:00:00.000Z"
            canonical_session = _canonical_bytes(session_value)
            forged_session_path = root / "forged-runtime-observation-v1.json"
            forged_session_raw = (
                json.dumps(session_value, indent=2, sort_keys=True) + "\n"
            ).encode()
            forged_session_path.write_bytes(forged_session_raw)
            session_artifact.update(
                {
                    "canonical_json": canonical_session.decode(),
                    "canonical_sha256": sha256(canonical_session).hexdigest(),
                    "canonical_size": len(canonical_session),
                    "retained": {
                        "uri": forged_session_path.as_uri(),
                        "sha256": sha256(forged_session_raw).hexdigest(),
                        "size": len(forged_session_raw),
                    },
                }
            )
            mutations.append(("forged session custody", reseal(forged_session)))

            forged_pending = deepcopy(pending)
            forged_pending["assertions"] = deepcopy(baseline["assertions"])
            forged_pending["semantic_equivalence"]["assertions"] = deepcopy(
                baseline["assertions"]
            )
            forged_pending["semantic_equivalence"]["candidate_id"] = (
                "blueprints-convention-candidate:sha256:" + "a" * 64
            )
            forged_pending["semantic_equivalence"]["staged_tree_id"] = "b" * 40
            mutations.append(("forged pending semantics", reseal(forged_pending)))

            copied_receipt = deepcopy(baseline)
            copied_artifact = next(
                row
                for row in copied_receipt["owner_artifacts"]
                if row["role"] == "material-fluid-receipt"
            )
            original_receipt_path = Path(
                urlparse(copied_artifact["retained"]["uri"]).path
            )
            copied_receipt_path = root / "copied-material-fluid-receipt.json"
            copied_receipt_raw = original_receipt_path.read_bytes()
            copied_receipt_path.write_bytes(copied_receipt_raw)
            copied_artifact["retained"] = {
                "uri": copied_receipt_path.as_uri(),
                "sha256": sha256(copied_receipt_raw).hexdigest(),
                "size": len(copied_receipt_raw),
            }
            mutations.append(("copied top receipt", reseal(copied_receipt)))

            stale_receipt = deepcopy(baseline)
            stale_artifact = next(
                row
                for row in stale_receipt["owner_artifacts"]
                if row["role"] == "material-fluid-receipt"
            )
            stale_value = json.loads(stale_artifact["canonical_json"])
            stale_value["limitations"] = ["forged without a new receipt identity"]
            stale_canonical = _canonical_bytes(stale_value)
            stale_path = root / "stale-material-fluid-receipt.json"
            stale_raw = (json.dumps(stale_value, indent=2, sort_keys=True) + "\n").encode()
            stale_path.write_bytes(stale_raw)
            stale_artifact.update(
                {
                    "canonical_json": stale_canonical.decode(),
                    "canonical_sha256": sha256(stale_canonical).hexdigest(),
                    "canonical_size": len(stale_canonical),
                    "retained": {
                        "uri": stale_path.as_uri(),
                        "sha256": sha256(stale_raw).hexdigest(),
                        "size": len(stale_raw),
                    },
                }
            )
            mutations.append(("stale top receipt identity", reseal(stale_receipt)))

            missing_receipt_custody = deepcopy(baseline)
            next(
                row
                for row in missing_receipt_custody["owner_artifacts"]
                if row["role"] == "material-fluid-receipt"
            )["retained"] = None
            mutations.append(
                ("missing top receipt custody", reseal(missing_receipt_custody))
            )

            with patch(
                "workbench_shell.feature_studio.validate_retained_material_fluid_success",
                side_effect=owner_validate_stub,
            ):
                for label, mutated in mutations:
                    with (
                        self.subTest(label=label),
                        self.assertRaises(FeatureStudioError),
                    ):
                        validate_feature_result(mutated, suite_root=SUITE_ROOT)

    def test_capabilities_are_closed_and_do_not_claim_service_jobs(self):
        capabilities = feature_studio_capabilities()
        self.assertEqual(capabilities["format"], "workbench-feature-studio-capability-projection-v2")
        self.assertEqual(len(capabilities["descriptors"]), 5)
        self.assertTrue(
            all(
                row["embedded_state"] == "embedded"
                for row in capabilities["descriptors"]
            )
        )

        request_schema = json.loads(
            (MODULE_ROOT / "schemas/feature-studio-request-v2.schema.json").read_text()
        )
        Draft202012Validator.check_schema(request_schema)
        request_validator = Draft202012Validator(request_schema)
        source_request = {
            "format": "workbench-feature-studio-request-v2",
            "schema_version": 2,
            "canonicalizer": "workbench-canonical-json-v2",
            "operation": "plan",
            "feature_kind": "material-backed-fluid",
            "selection_mode": "source-plan",
            "workspace_uri": "file:///source",
            "receipt_uri": None,
            "name": "Pilot Coolant",
            "color": "0x425d73",
            "translation": None,
            "symbol": None,
            "launcher": "prism",
            "reviewed_plan_id": None,
            "export_uri": None,
            "runtime": None,
        }
        request_validator.validate(source_request)
        self.assertEqual(
            validate_feature_request(source_request)["operation"], "plan"
        )
        invalid_request = deepcopy(source_request)
        invalid_request["runtime"] = {
            "launcher_executable_uri": "file:///launcher",
            "launcher_root_uri": "file:///launcher-root",
            "launcher_java_uri": None,
            "launcher_java_state_uri": None,
            "launcher_profile": None,
            "packwiz_uri": None,
            "seed_uris": [],
            "memory_mib": 8192,
            "offline_name": "Workbench",
            "launch_timeout_milliseconds": 600000,
            "attach_timeout_milliseconds": 120000,
            "session_timeout_milliseconds": 21600000,
            "state_root_uri": None,
        }
        self.assertTrue(list(request_validator.iter_errors(invalid_request)))
        retained_request = deepcopy(source_request)
        retained_request.update(
            {
                "operation": "inspect",
                "selection_mode": "retained-receipt",
                "workspace_uri": None,
                "receipt_uri": "file:///receipt.json",
                "name": None,
                "color": None,
                "launcher": None,
            }
        )
        request_validator.validate(retained_request)
        self.assertEqual(
            validate_feature_request(retained_request)["selection_mode"],
            "retained-receipt",
        )
        capability_schema = json.loads(
            (MODULE_ROOT / "schemas/feature-studio-capabilities-v2.schema.json").read_text()
        )
        Draft202012Validator(capability_schema).validate(capabilities)

    def test_closed_request_dispatches_all_five_embedded_operations(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            workspace = root / "source"
            workspace.mkdir()
            owner_plan = _plan(workspace)
            owner_result = _owner_result("d", root, workspace, owner_plan)

            def request(operation: str) -> dict:
                runtime = None
                reviewed_plan_id = None
                export_uri = None
                if operation == "verify":
                    reviewed_plan_id = owner_plan["plan_id"]
                    runtime = {
                        "launcher_executable_uri": (root / "launcher").as_uri(),
                        "launcher_root_uri": (root / "launcher-root").as_uri(),
                        "launcher_java_uri": None,
                        "launcher_java_state_uri": None,
                        "launcher_profile": None,
                        "packwiz_uri": None,
                        "seed_uris": [],
                        "memory_mib": 8192,
                        "offline_name": "Workbench",
                        "launch_timeout_milliseconds": 600000,
                        "attach_timeout_milliseconds": 120000,
                        "session_timeout_milliseconds": 21600000,
                        "state_root_uri": None,
                    }
                if operation == "export":
                    reviewed_plan_id = owner_plan["plan_id"]
                    export_uri = (root / "exports" / "request-export").as_uri()
                    (root / "exports").mkdir(exist_ok=True)
                return {
                    "format": "workbench-feature-studio-request-v2",
                    "schema_version": 2,
                    "canonicalizer": "workbench-canonical-json-v2",
                    "operation": operation,
                    "feature_kind": "material-backed-fluid",
                    "selection_mode": "source-plan",
                    "workspace_uri": workspace.as_uri(),
                    "receipt_uri": None,
                    "name": "Pilot Coolant",
                    "color": "0x425d73",
                    "translation": None,
                    "symbol": None,
                    "launcher": "prism",
                    "reviewed_plan_id": reviewed_plan_id,
                    "export_uri": export_uri,
                    "runtime": runtime,
                }

            with (
                patch(
                    "workbench_shell.feature_studio.plan_material_fluid_trial",
                    return_value=owner_plan,
                ),
                patch(
                    "workbench_shell.feature_studio.execute_material_fluid_trial",
                    return_value=deepcopy(owner_result),
                ),
                patch(
                    "workbench_shell.feature_studio.validate_retained_material_fluid_success",
                    side_effect=_validated_runtime,
                ),
            ):
                for operation in (
                    "inspect",
                    "plan",
                    "verify",
                    "explain",
                    "export",
                ):
                    with self.subTest(operation=operation):
                        value = execute_feature_request(
                            SUITE_ROOT,
                            request(operation),
                            expected_operation=operation,
                        )
                        self.result_validator.validate(value)
                        self.assertEqual(value, validate_feature_result(value))
                        self.assertEqual(value["operation"], operation)

                with self.assertRaisesRegex(
                    FeatureStudioError, "another capability operation"
                ):
                    execute_feature_request(
                        SUITE_ROOT,
                        request("plan"),
                        expected_operation="inspect",
                    )
                foreign = request("plan")
                foreign["workspace_uri"] = "https://example.invalid/source"
                with self.assertRaisesRegex(FeatureStudioError, "local file URI"):
                    execute_feature_request(SUITE_ROOT, foreign)

    def test_console_review_binding_is_retained_but_not_semantic_identity(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            workspace = Path(temporary) / "source"
            workspace.mkdir()
            owner_plan = _plan(workspace)
            binding = {
                "WORKBENCH_CONSOLE_CATALOG_DIGEST": "sha256:" + "7" * 64,
                "WORKBENCH_CONSOLE_ACTION_DIGEST": "sha256:" + "8" * 64,
                "WORKBENCH_CONSOLE_REVIEW_DIGEST": "sha256:" + "9" * 64,
                "WORKBENCH_CONSOLE_COMMAND_ID": "feature-studio.plan",
            }
            with (
                patch(
                    "workbench_shell.feature_studio.plan_material_fluid_trial",
                    return_value=owner_plan,
                ),
                patch.dict("os.environ", binding, clear=False),
            ):
                result = plan_feature(
                    SUITE_ROOT, workspace, name="Pilot Coolant", color="0x425d73"
                )
            self.result_validator.validate(result)
            self.assertEqual(result["review_binding"]["state"], "catalog-v2-bound")
            self.assertEqual(
                result["review_binding"]["review_digest"],
                binding["WORKBENCH_CONSOLE_REVIEW_DIGEST"],
            )

            binding["WORKBENCH_CONSOLE_COMMAND_ID"] = "feature-studio.verify"
            with (
                patch(
                    "workbench_shell.feature_studio.plan_material_fluid_trial",
                    return_value=owner_plan,
                ),
                patch.dict("os.environ", binding, clear=False),
                self.assertRaisesRegex(FeatureStudioError, "another operation"),
            ):
                plan_feature(
                    SUITE_ROOT, workspace, name="Pilot Coolant", color="0x425d73"
                )

    def test_foreign_review_binding_rejects_before_export_or_verification(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            workspace = root / "source"
            workspace.mkdir()
            owner_plan = _plan(workspace)
            binding = {
                "WORKBENCH_CONSOLE_CATALOG_DIGEST": "sha256:" + "7" * 64,
                "WORKBENCH_CONSOLE_ACTION_DIGEST": "sha256:" + "8" * 64,
                "WORKBENCH_CONSOLE_REVIEW_DIGEST": "sha256:" + "9" * 64,
                "WORKBENCH_CONSOLE_COMMAND_ID": "feature-studio.plan",
            }
            output = root / "export"
            with (
                patch(
                    "workbench_shell.feature_studio.plan_material_fluid_trial",
                    return_value=owner_plan,
                ),
                patch(
                    "workbench_shell.feature_studio.execute_material_fluid_trial"
                ) as execute,
                patch.dict("os.environ", binding, clear=False),
            ):
                with self.assertRaisesRegex(FeatureStudioError, "another operation"):
                    export_feature(
                        SUITE_ROOT,
                        workspace,
                        name="Pilot Coolant",
                        color="0x425d73",
                        output_root=output,
                        expected_plan_id=owner_plan["plan_id"],
                    )
                self.assertFalse(output.exists())
                with self.assertRaisesRegex(FeatureStudioError, "another operation"):
                    verify_feature(
                        SUITE_ROOT,
                        workspace,
                        name="Pilot Coolant",
                        color="0x425d73",
                        launcher_executable="launcher",
                        launcher_root=root / "launcher",
                        expected_plan_id=owner_plan["plan_id"],
                    )
                execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
