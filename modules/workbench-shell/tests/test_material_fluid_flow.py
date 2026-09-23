"""Focused tests for the reviewed disposable material-fluid owner flow."""

from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlparse


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

from workbench_shell.material_fluid_flow import (  # noqa: E402
    _apply_profile_assertions,
    _pending_assertions,
    _profile_compatibility_policy,
    _profile_observation_authority,
    _profile_runtime_policy_authority,
    _preflight_explicit_java_state,
    _preflight_runtime_state,
    _runtime_summary,
    _validate_compatibility_records,
    MaterialFluidFlowError,
    execute_material_fluid_trial,
    plan_material_fluid_trial,
    validate_retained_material_fluid_success,
    validate_retained_material_fluid_runtime,
)
from workbench_shell.runtime_observe import RuntimeObserveError  # noqa: E402


def _blueprint_plan(workspace: Path) -> dict:
    if not (workspace / ".git").is_dir():
        if not any(workspace.iterdir()):
            (workspace / ".fixture-source").write_text("fixture\n", encoding="utf-8")
        for arguments in (
            ("init", "--quiet"),
            ("config", "user.name", "Workbench Test"),
            ("config", "user.email", "workbench@example.invalid"),
            ("add", "."),
            ("commit", "--quiet", "-m", "fixture"),
        ):
            subprocess.run(
                ["git", "-C", str(workspace), *arguments],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
    revision = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    parameters = {
        "color": "0x425d73",
        "material_id": 20008,
        "name": "Pilot Coolant",
        "registry_name": "pilot_coolant",
        "symbol_name": "PilotCoolant",
        "translation": "Pilot Coolant",
    }
    paths = (
        "groovy/material/PetrochemistryMaterials.groovy",
        "groovy/material/SuSyMaterials.groovy",
        "resources/langfiles/lang/en_us.lang",
    )
    return {
        "format": "workbench-material-backed-fluid-plan-v1",
        "plan_id": "sha256:" + ("1" * 64),
        "source": {
            "workspace_uri": workspace.as_uri(),
            "revision": revision,
        },
        "blueprint": {"effective_parameters": parameters},
        "operations": [
            {
                "operation": "update",
                "path": path,
                "diff": f"--- a/{path}\n+++ b/{path}\n",
            }
            for path in paths
        ],
    }


def _stage_result(staged: Path, receipt: Path) -> dict:
    return {
        "outcome": "staged",
        "receipt": {
            "state": "ready",
            "stage_id": "sha256:" + ("2" * 64),
            "blueprint": {"candidate_id": "sha256:" + ("3" * 64)},
            "target": {
                "workspace_uri": staged.as_uri(),
                "revision": "staged-revision",
                "receipt_uri": receipt.as_uri(),
            },
        },
    }


def _runtime_result(projection: Path, receipt: Path) -> dict:
    return {
        "outcome": "completed",
        "launch_receipt": {"outcome": "checkpoint-reached"},
        "receipt": {
            "outcome": "completed",
            "launch": {"process_observation": {"state": "exited"}},
            "target": {"receipt_uri": receipt.as_uri()},
        },
    }


def _observed_profile(
    _suite,
    _runtime,
    _spec,
    _probe,
    _compatibility,
    _runtime_plan,
    _staged,
    _evidence_root,
    assertions,
):
    for value in assertions.values():
        value["state"] = "observed"
    return ({
        "format": "workbench-supersymmetry-material-fluid-assessment-v2",
        "schema_version": 2,
        "state": "observed",
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
    }, assertions)


def _real_profile_runtime(root: Path):
    authority = _profile_observation_authority(SUITE_ROOT)
    spec = authority.MaterialFluidProbeSpec(
        registry_name="pilot_coolant",
        symbol_name="PilotCoolant",
        material_id=20008,
        color_rgb=0x425D73,
        translation="Pilot Coolant",
        source_plan_id="sha256:" + "1" * 64,
    )
    checks = {
        "color_rgb": True,
        "fluid_name": True,
        "forge_registry_roundtrip": True,
        "has_flammable_flag": True,
        "has_fluid_property": True,
        "localized_name": True,
        "manager_frozen": True,
        "material_id": True,
        "material_resource": True,
    }
    marker = {
        "actual": {
            "color_rgb": 0x425D73,
            "fluid_name": "pilot_coolant",
            "forge_registry_roundtrip": True,
            "has_flammable_flag": True,
            "has_fluid_property": True,
            "localized_name": "Pilot Coolant",
            "manager_phase": "FROZEN",
            "material_id": 20008,
            "material_resource": "susy:pilot_coolant",
        },
        "checks": checks,
        "error_kind": None,
        "format": "workbench-material-fluid-observation-v1",
        "probe_id": spec.probe_id,
        "source_plan_id": spec.source_plan_id,
        "stage": "postInit",
        "state": "observed",
    }
    marker_raw = json.dumps(
        marker, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    token = base64.urlsafe_b64encode(marker_raw).rstrip(b"=")
    log = b"[INFO] " + authority.MARKER_PREFIX.encode() + token + b"\n"
    plan_id = "sha256:" + "6" * 64
    instance_id = "workbench-material-fluid-fixture"
    staged = root / "staged"
    staged.mkdir()
    evidence_root = root / "evidence/runtime"
    run_root = evidence_root / ("6" * 16) / "launches" / instance_id
    final_root = run_root / "final"
    final_root.mkdir(parents=True)
    log_path = final_root / "minecraft-groovy.log"
    log_path.write_bytes(log)
    fml_marker = "Forge Mod Loader has successfully loaded 210 mods"
    latest_log = ("[main/INFO] " + fml_marker + "\n").encode()
    latest_log_path = final_root / "minecraft-latest.log"
    latest_log_path.write_bytes(latest_log)

    overlay = authority.build_material_fluid_probe_overlay(spec)
    script = authority.build_material_fluid_probe(spec)
    overlay_raw = json.dumps(
        overlay, ensure_ascii=False, indent=2, sort_keys=True
    ).encode() + b"\n"
    probe = {
        "probe_id": spec.probe_id,
        "overlay_id": overlay["patch_id"],
        "overlay_spec_sha256": sha256(overlay_raw).hexdigest(),
        "script_sha256": sha256(script).hexdigest(),
        "script_size": len(script),
        "projection_target": overlay["target"]["path"],
    }
    _authorized, compatibility_policy = _profile_compatibility_policy(
        SUITE_ROOT, ()
    )
    patch_policy = compatibility_policy["patches"][0]
    profile_patch = {
        "description": "fixture profile compatibility",
        "entry": patch_policy["target_entry"],
        "entry_sha256_after": "a" * 64,
        "entry_sha256_before": patch_policy["target_entry_sha256"],
        "find_utf8": patch_policy["find_utf8"],
        "jar_sha256_after": "b" * 64,
        "jar_sha256_before": patch_policy["target_sha256"],
        "matches": patch_policy["expected_matches"],
        "operation": "archive-entry-replacement",
        "patch_id": patch_policy["patch_id"],
        "replace_utf8": patch_policy["replace_utf8"],
        "spec_sha256": patch_policy["spec_sha256"],
        "target_path": patch_policy["target_path"],
    }
    probe_patch = {
        "description": "fixture observation probe",
        "file_sha256_after": probe["script_sha256"],
        "file_size_after": probe["script_size"],
        "operation": "file-overlay",
        "patch_id": probe["overlay_id"],
        "source_path": "MaterialBackedFluidAssertion.groovy",
        "source_sha256": probe["script_sha256"],
        "source_size": probe["script_size"],
        "spec_sha256": probe["overlay_spec_sha256"],
        "target_path": probe["projection_target"],
    }
    compatibility_records = [profile_patch, probe_patch]

    fixture_root = root / "fixture"
    instance_root = fixture_root / "instance"
    payload_root = instance_root / ".minecraft"
    payload_root.mkdir(parents=True)
    payload = {
        "file_count": 1,
        "root_uri": payload_root.as_uri(),
        "total_bytes": 1,
        "tree_sha256": "sha256:" + "7" * 64,
    }
    materialization_receipt_path = (
        fixture_root / "receipts/packwiz-materialization-v2.json"
    )
    materialization_receipt_path.parent.mkdir()
    source_snapshot = {"tree_sha256": "sha256:" + "8" * 64}
    refreshed_pack = {
        "manifest_sha256": "9" * 64,
        "index": {"actual_sha256": "a" * 64},
    }
    tools = {
        "packwiz": {"sha256": "b" * 64, "size": 1},
        "installer": {
            "url": "https://example.invalid/installer.jar",
            "source_revision": "fixture",
            "sha256": "c" * 64,
            "size": 1,
            "entrypoint": "fixture.Main",
        },
        "java": {"identity": {"runtime_id": "sha256:" + "d" * 64}},
    }
    launcher_manifest_sha256 = "e" * 64
    expected_runtime_plan = {
        "plan_id": plan_id,
        "request": {"side": "client", "launcher": "prism"},
        "workspace": {
            "root_uri": staged.as_uri(),
            "revision": "staged-revision",
            "dirty": False,
        },
        "target": {"fixture_root_uri": fixture_root.as_uri()},
    }
    materialization_receipt = seal_packwiz_v2_receipt({
        "state": "materialized",
        "readiness": "pack-payload-installed",
        "plan_id": plan_id,
        "request": expected_runtime_plan["request"],
        "workspace": expected_runtime_plan["workspace"],
        "source_snapshot": source_snapshot,
        "refreshed_pack": refreshed_pack,
        "tools": tools,
        "launcher": {"manifest_sha256_after": launcher_manifest_sha256},
        "payload": payload,
        "target": {
            "instance_root_uri": instance_root.as_uri(),
            "receipt_uri": materialization_receipt_path.as_uri(),
        },
    })
    materialization_id = materialization_receipt["materialization_id"]
    materialization_receipt_path.write_text(
        json.dumps(materialization_receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    projection = root / "launcher/instances" / instance_id
    projection.mkdir(parents=True)
    projection_record = {
        "compatibility_patches": compatibility_records,
        "payload": {
            "materialized": {
                key: payload[key]
                for key in ("tree_sha256", "file_count", "total_bytes")
            }
        },
        "projection_uri": projection.as_uri(),
        "source_instance_uri": instance_root.as_uri(),
    }
    parent_path = run_root / "runtime-launch-v1.json"
    parent = {
        "format": "workbench-runtime-launch-receipt-v2",
        "schema_version": 2,
        "state": "observed",
        "outcome": "checkpoint-reached",
        "plan_id": plan_id,
        "materialization_id": materialization_id,
        "started_at": "2026-01-01T00:00:00.000Z",
        "project": {"name": "Fixture"},
        "launcher": {
            "family": "prism",
            "instance_id": instance_id,
            "projection_uri": projection.as_uri(),
            "sha256": "f" * 64,
            "version_output": "fixture",
        },
        "java": {"runtime_id": "sha256:" + "1" * 64},
        "projection": projection_record,
        "compatibility_patches": compatibility_records,
        "observation": {
            "checkpoint": {
                "id": "fml-client-loaded",
                "marker": fml_marker,
                "source": "minecraft-latest-log",
            },
        },
        "target": {
            "receipt_uri": parent_path.as_uri(),
            "run_root_uri": run_root.as_uri(),
        },
    }
    parent_identity = {
        "plan_id": plan_id,
        "materialization_id": materialization_id,
        "instance_id": instance_id,
        "started_at": parent["started_at"],
        "launcher": {
            "family": "prism",
            "sha256": "f" * 64,
            "version_output": "fixture",
        },
        "java_runtime_id": parent["java"]["runtime_id"],
        "projection_uri": projection.as_uri(),
        "compatibility_patches": compatibility_records,
    }
    parent["launch_id"] = "sha256:" + sha256(
        json.dumps(
            parent_identity,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    parent_raw = json.dumps(parent, indent=2, sort_keys=True).encode() + b"\n"
    parent_path.write_bytes(parent_raw)

    source_logs = projection / ".minecraft/logs"
    source_logs.mkdir(parents=True)
    source_latest_log_path = source_logs / "latest.log"
    source_groovy_log_path = source_logs / "groovy.log"
    source_latest_log_path.write_bytes(latest_log)
    source_groovy_log_path.write_bytes(log)

    process = {
        "state": "exited",
        "method": "fixture-process-observer",
        "samples": 2,
        "observed_pids": [42],
        "attached_at": "2026-01-01T00:01:00.000Z",
        "observed_at": "2026-01-01T00:02:00.000Z",
    }
    launch_path = run_root / "runtime-launch-v3.json"
    launch = deepcopy(parent)
    launch.update({
        "format": "workbench-runtime-launch-receipt-v3",
        "schema_version": 3,
        "parent_launch_receipt": {
            "launch_id": parent["launch_id"],
            "sha256": sha256(parent_raw).hexdigest(),
            "size": len(parent_raw),
            "uri": parent_path.as_uri(),
        },
        "evidence": [
            {
                "capture_uri": latest_log_path.as_uri(),
                "label": "minecraft-latest-log",
                "sha256": sha256(latest_log).hexdigest(),
                "size": len(latest_log),
                "source_uri": source_latest_log_path.as_uri(),
                "state": "captured",
            },
            {
                "capture_uri": log_path.as_uri(),
                "label": "minecraft-groovy-log",
                "sha256": sha256(log).hexdigest(),
                "size": len(log),
                "source_uri": source_groovy_log_path.as_uri(),
                "state": "captured",
            },
        ],
        "observation": {
            "checkpoint": parent["observation"]["checkpoint"],
            "session_exit": process,
        },
        "launch_policy": {
            "observation_boundary": "projected-client-process-exit",
        },
        "target": {
            "parent_receipt_uri": parent_path.as_uri(),
            "receipt_uri": launch_path.as_uri(),
            "run_root_uri": run_root.as_uri(),
        },
    })
    final_identity = {
        "parent_launch_id": parent["launch_id"],
        "session_exit": process,
        "evidence": [
            {
                key: item.get(key)
                for key in ("label", "state", "sha256", "size")
            }
            for item in launch["evidence"]
        ],
    }
    launch["launch_id"] = "sha256:" + sha256(
        json.dumps(
            final_identity,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    launch_raw = json.dumps(launch, indent=2, sort_keys=True).encode() + b"\n"
    launch_path.write_bytes(launch_raw)
    session_path = run_root / "runtime-observation-v1.json"
    session = {
        "format": "workbench-runtime-observation-session-v1",
        "schema_version": 1,
        "operation_class": "local-mutation",
        "state": "complete",
        "outcome": "completed",
        "launch": {
            "parent_launch_id": parent["launch_id"],
            "parent_receipt": {
                "sha256": sha256(parent_raw).hexdigest(),
                "size": len(parent_raw),
                "uri": parent_path.as_uri(),
            },
            "final_launch_id": launch["launch_id"],
            "instance_id": instance_id,
            "projection_uri": projection.as_uri(),
            "process_observation": process,
            "final_receipt": {
                "sha256": sha256(launch_raw).hexdigest(),
                "size": len(launch_raw),
                "uri": launch_path.as_uri(),
            },
        },
        "evidence": launch["evidence"],
        "target": {
            "receipt_uri": session_path.as_uri(),
            "run_root_uri": run_root.as_uri(),
        },
    }
    session["session_id"] = "sha256:" + sha256(
        json.dumps(
            session,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    session_path.write_text(
        json.dumps(session, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    runtime = {
        "outcome": "completed",
        "launch_receipt": launch,
        "receipt": session,
    }
    return {
        "compatibility_policy": compatibility_policy,
        "evidence_root": evidence_root,
        "expected_runtime_plan": expected_runtime_plan,
        "launcher_root": root / "launcher",
        "probe": probe,
        "projection": projection,
        "runtime": runtime,
        "spec": spec,
        "staged": staged,
    }


def _retain_rebound_runtime(runtime: dict) -> None:
    launch = runtime["launch_receipt"]
    final_identity = {
        "parent_launch_id": launch["parent_launch_receipt"]["launch_id"],
        "session_exit": launch["observation"]["session_exit"],
        "evidence": [
            {
                key: item.get(key)
                for key in ("label", "state", "sha256", "size")
            }
            for item in launch["evidence"]
        ],
    }
    launch["launch_id"] = "sha256:" + sha256(
        json.dumps(
            final_identity,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    launch_path = Path(urlparse(launch["target"]["receipt_uri"]).path)
    launch_raw = json.dumps(launch, indent=2, sort_keys=True).encode() + b"\n"
    launch_path.write_bytes(launch_raw)

    session = runtime["receipt"]
    session["launch"]["final_launch_id"] = launch["launch_id"]
    session["launch"]["process_observation"] = launch["observation"][
        "session_exit"
    ]
    session["launch"]["final_receipt"] = {
        "sha256": sha256(launch_raw).hexdigest(),
        "size": len(launch_raw),
        "uri": launch_path.as_uri(),
    }
    session["evidence"] = launch["evidence"]
    session.pop("session_id", None)
    session["session_id"] = "sha256:" + sha256(
        json.dumps(
            session,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    session_path = Path(urlparse(session["target"]["receipt_uri"]).path)
    session_path.write_text(
        json.dumps(session, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _completed_flow_receipt(fixture: dict) -> dict:
    runtime_result = fixture["runtime"]
    session = runtime_result["receipt"]
    launch = runtime_result["launch_receipt"]
    session_path = Path(urlparse(session["target"]["receipt_uri"]).path)
    launch_path = Path(urlparse(launch["target"]["receipt_uri"]).path)
    materialization_path = (
        Path(
            urlparse(
                fixture["expected_runtime_plan"]["target"]["fixture_root_uri"]
            ).path
        )
        / "receipts/packwiz-materialization-v2.json"
    )
    session_raw = session_path.read_bytes()
    launch_raw = launch_path.read_bytes()
    materialization_raw = materialization_path.read_bytes()
    materialization = json.loads(materialization_raw)
    payload = {
        key: materialization["payload"][key]
        for key in ("tree_sha256", "file_count", "total_bytes")
    }
    capture = {
        "groovy_log_sha256": next(
            row["sha256"]
            for row in launch["evidence"]
            if row["label"] == "minecraft-groovy-log"
        ),
        "groovy_log_uri": next(
            row["capture_uri"]
            for row in launch["evidence"]
            if row["label"] == "minecraft-groovy-log"
        ),
        "session_outcome": session["outcome"],
        "session_receipt_uri": session_path.as_uri(),
        "session_receipt_sha256": sha256(session_raw).hexdigest(),
        "session_receipt_size": len(session_raw),
        "launch_id": launch["launch_id"],
        "launch_receipt_uri": launch_path.as_uri(),
        "launch_receipt_sha256": sha256(launch_raw).hexdigest(),
        "materialization_id": launch["materialization_id"],
        "materialization_receipt_uri": materialization_path.as_uri(),
        "materialization_receipt_sha256": sha256(materialization_raw).hexdigest(),
        "materialization_receipt_size": len(materialization_raw),
        "payload": payload,
        "probe_id": fixture["probe"]["probe_id"],
        "probe_overlay_id": fixture["probe"]["overlay_id"],
        "probe_script_sha256": fixture["probe"]["script_sha256"],
    }
    authority = _profile_observation_authority(SUITE_ROOT)
    groovy_log = Path(urlparse(capture["groovy_log_uri"]).path).read_bytes()
    assessment = authority.interpret_material_fluid_observation(
        fixture["spec"],
        groovy_log_bytes=groovy_log,
        capture=capture,
    )
    parameters = {
        "color": f"0x{fixture['spec'].color_rgb:06x}",
        "material_id": fixture["spec"].material_id,
        "name": fixture["spec"].translation,
        "registry_name": fixture["spec"].registry_name,
        "symbol_name": fixture["spec"].symbol_name,
        "translation": fixture["spec"].translation,
    }
    assertion_states = {
        "fml_client_load": "observed",
        **assessment["developer_assertions"],
    }
    return {
        "format": "workbench-material-fluid-flow-receipt-v2",
        "schema_version": 2,
        "state": "complete",
        "outcome": "runtime-completed",
        "plan": {
            "blueprint_plan_id": fixture["spec"].source_plan_id,
            "effective_parameters": parameters,
        },
        "runtime": {
            "state": "observed",
            "outcome": session["outcome"],
            "receipt_uri": session_path.as_uri(),
            "session_id": session["session_id"],
            "final_launch_id": launch["launch_id"],
            "runtime_plan_id": launch["plan_id"],
            "materialization_id": launch["materialization_id"],
            "materialization_receipt_uri": materialization_path.as_uri(),
            "materialization_receipt_sha256": sha256(materialization_raw).hexdigest(),
            "materialization_receipt_size": len(materialization_raw),
            "instance_id": launch["launcher"]["instance_id"],
            "projection_uri": launch["launcher"]["projection_uri"],
            "payload": payload,
            "target_policy": "fresh-unique-disposable-projection",
        },
        "profile_observation": assessment,
        "assertions": {
            key: {
                **_pending_assertions()[key],
                "state": state,
            }
            for key, state in assertion_states.items()
        },
    }


class MaterialFluidFlowTest(unittest.TestCase):
    def test_retained_success_validates_its_own_identity_and_selected_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt_path = root / "receipt.json"
            parameters = {
                "color": "0x425d73",
                "material_id": 20008,
                "name": "Pilot Coolant",
                "registry_name": "pilot_coolant",
                "symbol_name": "PilotCoolant",
                "translation": "Pilot Coolant",
            }
            reviewed_plan = {
                "plan_id": "sha256:" + "1" * 64,
                "source": {
                    "workspace_uri": (root / "source").as_uri(),
                    "revision": "a" * 40,
                },
                "blueprint": {
                    "plan_id": "sha256:" + "2" * 64,
                    "blueprint": {"effective_parameters": parameters},
                },
                "execution": {
                    "profile_compatibility": {"identity_patches": []}
                },
            }
            receipt = {
                "format": "workbench-material-fluid-flow-receipt-v2",
                "schema_version": 2,
                "receipt_id": "",
                "state": "complete",
                "outcome": "runtime-completed",
                "plan": {
                    "plan_id": reviewed_plan["plan_id"],
                    "blueprint_plan_id": reviewed_plan["blueprint"]["plan_id"],
                    "profile_compatibility": [],
                    "effective_parameters": parameters,
                },
                "source": reviewed_plan["source"],
                "blueprint_stage": {"stage_id": "sha256:" + "3" * 64},
                "target": {"receipt_uri": receipt_path.as_uri()},
            }
            identity = {
                key: value for key, value in receipt.items() if key != "receipt_id"
            }
            receipt["receipt_id"] = "sha256:" + sha256(
                json.dumps(
                    identity,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            receipt_path.write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with (
                patch(
                    "workbench_shell.material_fluid_flow.validate_retained_blueprint_stage",
                    return_value={"stage": "validated"},
                ),
                patch(
                    "workbench_shell.material_fluid_flow.validate_retained_material_fluid_runtime",
                    return_value={"runtime": "validated"},
                ),
            ):
                validated = validate_retained_material_fluid_success(
                    receipt,
                    reviewed_plan,
                    SUITE_ROOT,
                    receipt_uri=receipt_path.as_uri(),
                )
                self.assertEqual(validated["runtime"], "validated")

                stale = deepcopy(receipt)
                stale["limitations"] = ["forged without a new receipt identity"]
                with self.assertRaisesRegex(
                    MaterialFluidFlowError, "receipt identity"
                ):
                    validate_retained_material_fluid_success(
                        stale,
                        reviewed_plan,
                        SUITE_ROOT,
                        receipt_uri=receipt_path.as_uri(),
                    )

                copied = root / "copied-receipt.json"
                copied.write_bytes(receipt_path.read_bytes())
                with self.assertRaisesRegex(
                    MaterialFluidFlowError, "target is rebound"
                ):
                    validate_retained_material_fluid_success(
                        receipt,
                        reviewed_plan,
                        SUITE_ROOT,
                        receipt_uri=copied.as_uri(),
                    )

    def test_retained_runtime_reader_reuses_full_owner_custody_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _real_profile_runtime(Path(temporary))
            receipt = _completed_flow_receipt(fixture)
            validated = validate_retained_material_fluid_runtime(receipt)
            self.assertEqual(
                validated["session"]["session_id"],
                receipt["runtime"]["session_id"],
            )
            self.assertEqual(
                validated["final_launch_receipt"]["launch_id"],
                receipt["runtime"]["final_launch_id"],
            )

            forged_meaning = deepcopy(receipt)
            forged_meaning["assertions"]["material_registration"]["meaning"] = (
                "forged developer explanation"
            )
            with self.assertRaisesRegex(
                MaterialFluidFlowError, "assertions contradict"
            ):
                validate_retained_material_fluid_runtime(forged_meaning)

            for field in ("session_id", "final_launch_id", "projection_uri"):
                tampered = deepcopy(receipt)
                tampered["runtime"][field] = "sha256:" + "f" * 64
                with self.subTest(field=field), self.assertRaisesRegex(
                    MaterialFluidFlowError, "custody disagree"
                ):
                    validate_retained_material_fluid_runtime(tampered)

            session_path = Path(urlparse(receipt["runtime"]["receipt_uri"]).path)
            session = json.loads(session_path.read_text())
            session["tampered"] = True
            session.pop("session_id")
            session["session_id"] = "sha256:" + sha256(
                json.dumps(
                    session,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            session_path.write_text(
                json.dumps(session, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                MaterialFluidFlowError, "custody disagree"
            ):
                validate_retained_material_fluid_runtime(receipt)

    def test_profile_construction_policy_is_exact_and_semantic_drift_fails(self) -> None:
        authority = _profile_runtime_policy_authority(SUITE_ROOT)
        policy = authority.material_fluid_runtime_compatibility_policy(SUITE_ROOT)
        patch_record = policy["patches"][0]
        self.assertEqual("flag", patch_record["find_utf8"])
        self.assertEqual("arg3", patch_record["replace_utf8"])
        self.assertEqual(
            "c9ca5da88b005f85ab443192d2d9188805f97ab1109e868da48d82e7759a8b70",
            patch_record["spec_sha256"],
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relative = Path(
                "profiles/packs/supersymmetry/compatibility/"
                "recurrent-complex-1.4.8.6-structure-context-arg3-v1.json"
            )
            destination = root / relative
            destination.parent.mkdir(parents=True)
            value = json.loads((SUITE_ROOT / relative).read_text(encoding="utf-8"))
            value["target"]["replace_utf8"] = "other"
            destination.write_text(
                json.dumps(value, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "policy drifted"):
                authority.material_fluid_runtime_compatibility_policy(root)

    def test_runtime_state_preflight_rejects_nested_output_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            suite = root / "suite"
            source = root / "source"
            launcher = root / "launcher"
            java_state = root / "java-state"
            for path in (suite / ".workbench", source, launcher, java_state):
                path.mkdir(parents=True)
            (suite / ".workbench/evidence").symlink_to(
                source, target_is_directory=True
            )
            with self.assertRaisesRegex(
                MaterialFluidFlowError, "cannot traverse a symbolic link"
            ):
                _preflight_runtime_state(
                    suite / ".workbench", launcher, protected=(source,)
                )
            (suite / ".workbench/evidence").unlink()
            (java_state / "artifacts").symlink_to(
                source, target_is_directory=True
            )
            with self.assertRaisesRegex(
                MaterialFluidFlowError, "cannot traverse a symbolic link"
            ):
                _preflight_explicit_java_state(
                    java_state, protected=(source,)
                )
            self.assertEqual([], list(source.iterdir()))

    def test_reviewed_run_retains_receipt_and_never_changes_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "source"
            staged = root / "staged"
            launcher_root = root / "launcher"
            projection = launcher_root / "instances/workbench-material-fluid-fixture"
            state = root / "state"
            workspace.mkdir()
            staged.mkdir()
            projection.mkdir(parents=True)
            source_marker = workspace / "source.txt"
            source_marker.write_text("unchanged\n", encoding="utf-8")
            blueprint = _blueprint_plan(workspace)
            stage = _stage_result(staged, root / "stage-receipt.json")
            runtime = _runtime_result(projection, root / "runtime-receipt.json")

            with (
                patch(
                    "workbench_shell.material_fluid_flow.plan_material_backed_fluid",
                    return_value=blueprint,
                ),
                patch(
                    "workbench_shell.material_fluid_flow.stage_material_backed_fluid",
                    return_value=stage,
                ) as stage_call,
                patch(
                    "workbench_shell.material_fluid_flow.observe_project_runtime",
                    return_value=runtime,
                ) as observe_call,
                patch(
                    "workbench_shell.material_fluid_flow.plan_project_runtime",
                    return_value={
                        "state": "ready",
                        "blockers": [],
                        "plan_id": "sha256:" + ("6" * 64),
                        "workspace": {
                            "root_uri": staged.as_uri(),
                            "revision": "staged-revision",
                            "dirty": False,
                        },
                    },
                ),
                patch(
                    "workbench_shell.material_fluid_flow._runtime_summary",
                    return_value=({
                        "state": "observed",
                        "outcome": "completed",
                    }, {
                        key: {**value, "state": (
                            "observed" if key == "fml_client_load" else "not-observed"
                        )}
                        for key, value in _pending_assertions().items()
                    }),
                ),
                patch(
                    "workbench_shell.material_fluid_flow._apply_profile_assertions",
                    side_effect=_observed_profile,
                ),
            ):
                plan = plan_material_fluid_trial(
                    SUITE_ROOT,
                    workspace,
                    name="Pilot Coolant",
                    color="0x425d73",
                )
                self.assertEqual(
                    "required-exact-profile-patch-set",
                    plan["execution"]["profile_compatibility"]["mode"],
                )
                self.assertEqual("workbench-material-fluid-flow-plan-v2", plan["format"])
                self.assertEqual(2, plan["schema_version"])
                result = execute_material_fluid_trial(
                    SUITE_ROOT,
                    workspace,
                    name="Pilot Coolant",
                    color="0x425d73",
                    launcher_executable=root / "prismlauncher",
                    launcher_root=launcher_root,
                    expected_plan_id=plan["plan_id"],
                    state_root=state,
                )

            self.assertEqual(result["outcome"], "runtime-completed")
            self.assertEqual("workbench-material-fluid-flow-result-v2", result["format"])
            self.assertEqual(
                "workbench-material-fluid-flow-receipt-v2",
                result["receipt"]["format"],
            )
            self.assertEqual(source_marker.read_text(encoding="utf-8"), "unchanged\n")
            self.assertEqual(result["receipt"]["assertions"]["fml_client_load"]["state"], "observed")
            for key in (
                "groovy_compilation",
                "material_registration",
                "fluid_registration",
                "localization",
            ):
                self.assertEqual(result["receipt"]["assertions"][key]["state"], "observed")
            retained = Path(
                unquote(
                    urlparse(result["receipt"]["target"]["receipt_uri"]).path
                )
            )
            self.assertTrue(retained.is_file())
            self.assertTrue(retained.is_relative_to(state.resolve()))
            self.assertEqual(stage_call.call_args.kwargs["expected_plan_id"], blueprint["plan_id"])
            self.assertEqual(observe_call.call_args.args[1], staged)
            probe = result["receipt"]["observation_probe"]
            self.assertEqual(
                ".minecraft/groovy/postInit/WorkbenchMaterialBackedFluidAssertion.groovy",
                probe["projection_target"],
            )
            overlay = Path(unquote(urlparse(probe["overlay_spec_uri"]).path))
            script = Path(unquote(urlparse(probe["script_uri"]).path))
            self.assertTrue(overlay.is_file())
            self.assertTrue(script.is_file())
            self.assertEqual(
                observe_call.call_args.kwargs["compatibility_patches"][-1].name,
                "overlay-v1.json",
            )

    def test_runtime_failure_is_retained_without_false_assertions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "source"
            staged = root / "staged"
            launcher_root = root / "launcher"
            state = root / "state"
            workspace.mkdir()
            staged.mkdir()
            blueprint = _blueprint_plan(workspace)
            stage = _stage_result(staged, root / "stage-receipt.json")

            with (
                patch(
                    "workbench_shell.material_fluid_flow.plan_material_backed_fluid",
                    return_value=blueprint,
                ),
                patch(
                    "workbench_shell.material_fluid_flow.stage_material_backed_fluid",
                    return_value=stage,
                ),
                patch(
                    "workbench_shell.material_fluid_flow.observe_project_runtime",
                    side_effect=RuntimeObserveError("fixture launch failure"),
                ),
                patch(
                    "workbench_shell.material_fluid_flow.plan_project_runtime",
                    return_value={
                        "state": "ready",
                        "blockers": [],
                        "plan_id": "sha256:" + ("6" * 64),
                        "workspace": {
                            "root_uri": staged.as_uri(),
                            "revision": "staged-revision",
                            "dirty": False,
                        },
                    },
                ),
            ):
                plan = plan_material_fluid_trial(
                    SUITE_ROOT,
                    workspace,
                    name="Pilot Coolant",
                    color="0x425d73",
                )
                result = execute_material_fluid_trial(
                    SUITE_ROOT,
                    workspace,
                    name="Pilot Coolant",
                    color="0x425d73",
                    launcher_executable=root / "prismlauncher",
                    launcher_root=launcher_root,
                    expected_plan_id=plan["plan_id"],
                    state_root=state,
                )

            self.assertEqual(result["outcome"], "failed")
            self.assertEqual(result["receipt"]["runtime"]["state"], "failed")
            self.assertTrue(
                all(
                    assertion["state"] == "not-observed"
                    for assertion in result["receipt"]["assertions"].values()
                )
            )
            receipt = Path(
                unquote(
                    urlparse(result["receipt"]["target"]["receipt_uri"]).path
                )
            )
            self.assertTrue(receipt.is_file())

    def test_staged_runtime_drift_rejects_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "source"
            staged = root / "staged"
            workspace.mkdir()
            staged.mkdir()
            blueprint = _blueprint_plan(workspace)
            stage = _stage_result(staged, root / "stage-receipt.json")
            with (
                patch(
                    "workbench_shell.material_fluid_flow.plan_material_backed_fluid",
                    return_value=blueprint,
                ),
                patch(
                    "workbench_shell.material_fluid_flow.stage_material_backed_fluid",
                    return_value=stage,
                ),
                patch(
                    "workbench_shell.material_fluid_flow.plan_project_runtime",
                    return_value={
                        "state": "ready",
                        "blockers": [],
                        "plan_id": "sha256:" + "6" * 64,
                        "workspace": {
                            "root_uri": staged.as_uri(),
                            "revision": "different-revision",
                            "dirty": True,
                        },
                    },
                ),
                patch(
                    "workbench_shell.material_fluid_flow.observe_project_runtime"
                ) as observe,
            ):
                plan = plan_material_fluid_trial(
                    SUITE_ROOT,
                    workspace,
                    name="Pilot Coolant",
                    color="0x425d73",
                )
                result = execute_material_fluid_trial(
                    SUITE_ROOT,
                    workspace,
                    name="Pilot Coolant",
                    color="0x425d73",
                    launcher_executable=root / "prismlauncher",
                    launcher_root=root / "launcher",
                    expected_plan_id=plan["plan_id"],
                    state_root=root / "state",
                )
            self.assertEqual("failed", result["outcome"])
            self.assertIn(
                "not exactly ready",
                result["receipt"]["runtime"]["error"]["message"],
            )
            observe.assert_not_called()

    def test_source_change_during_runtime_prevents_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "source"
            staged = root / "staged"
            projection = root / "launcher/instances/workbench-fixture"
            for path in (workspace, staged, projection):
                path.mkdir(parents=True)
            marker = workspace / "source.txt"
            marker.write_text("before\n", encoding="utf-8")
            blueprint = _blueprint_plan(workspace)
            with patch(
                "workbench_shell.material_fluid_flow.plan_material_backed_fluid",
                return_value=blueprint,
            ):
                reviewed = plan_material_fluid_trial(
                    SUITE_ROOT,
                    workspace,
                    name="Pilot Coolant",
                    color="0x425d73",
                )
            changed = deepcopy(reviewed)
            changed["source"] = {
                **reviewed["source"],
                "untracked_excluded": {"paths_sha256": "sha256:" + "0" * 64},
            }
            runtime = _runtime_result(projection, root / "runtime.json")

            def mutate_source(*_args, **_kwargs):
                (workspace / "runtime-created-untracked.txt").write_text(
                    "untracked\n", encoding="utf-8"
                )
                return runtime

            with (
                patch(
                    "workbench_shell.material_fluid_flow.plan_material_fluid_trial",
                    side_effect=(reviewed, reviewed, changed),
                ),
                patch(
                    "workbench_shell.material_fluid_flow.stage_material_backed_fluid",
                    return_value=_stage_result(staged, root / "stage.json"),
                ),
                patch(
                    "workbench_shell.material_fluid_flow.plan_project_runtime",
                    return_value={
                        "state": "ready",
                        "blockers": [],
                        "plan_id": "sha256:" + "6" * 64,
                        "workspace": {
                            "root_uri": staged.as_uri(),
                            "revision": "staged-revision",
                            "dirty": False,
                        },
                    },
                ),
                patch(
                    "workbench_shell.material_fluid_flow.observe_project_runtime",
                    side_effect=mutate_source,
                ),
                patch(
                    "workbench_shell.material_fluid_flow._runtime_summary",
                    return_value=(
                        {"state": "observed", "outcome": "completed"},
                        {
                            key: {**value, "state": "observed"}
                            for key, value in _pending_assertions().items()
                        },
                    ),
                ),
                patch(
                    "workbench_shell.material_fluid_flow._apply_profile_assertions",
                    side_effect=_observed_profile,
                ),
            ):
                result = execute_material_fluid_trial(
                    SUITE_ROOT,
                    workspace,
                    name="Pilot Coolant",
                    color="0x425d73",
                    launcher_executable=root / "prismlauncher",
                    launcher_root=root / "launcher",
                    expected_plan_id=reviewed["plan_id"],
                    state_root=root / "state",
                )
            self.assertEqual("failed", result["outcome"])
            self.assertIn(
                "source changed",
                result["receipt"]["runtime"]["error"]["message"],
            )
            self.assertIn(
                "does not claim",
                result["receipt"]["limitations"][0],
            )

    def test_execution_without_review_or_confirmation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "source"
            workspace.mkdir()
            with patch(
                "workbench_shell.material_fluid_flow.plan_material_backed_fluid",
                return_value=_blueprint_plan(workspace),
            ):
                with self.assertRaisesRegex(
                    MaterialFluidFlowError,
                    "requires a reviewed plan ID or confirmation",
                ):
                    execute_material_fluid_trial(
                        SUITE_ROOT,
                        workspace,
                        name="Pilot Coolant",
                        color="0x425d73",
                        launcher_executable=root / "prismlauncher",
                        launcher_root=root / "launcher",
                        state_root=root / "state",
                    )
            self.assertFalse((root / "state").exists())

    def test_output_roots_cannot_overlap_the_developer_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "source"
            workspace.mkdir()
            plan = _blueprint_plan(workspace)
            with patch(
                "workbench_shell.material_fluid_flow.plan_material_backed_fluid",
                return_value=plan,
            ):
                reviewed = plan_material_fluid_trial(
                    SUITE_ROOT,
                    workspace,
                    name="Pilot Coolant",
                    color="0x425d73",
                )
                for state_root, launcher_root, java_state in (
                    (workspace / "state", root / "launcher", None),
                    (root / "state", workspace / "launcher", None),
                    (root / "state", root / "launcher", workspace / "java"),
                ):
                    with self.subTest(
                        state_root=state_root,
                        launcher_root=launcher_root,
                        java_state=java_state,
                    ):
                        with self.assertRaisesRegex(
                            MaterialFluidFlowError,
                            "cannot overlap",
                        ):
                            execute_material_fluid_trial(
                                SUITE_ROOT,
                                workspace,
                                name="Pilot Coolant",
                                color="0x425d73",
                                launcher_executable=root / "prismlauncher",
                                launcher_root=launcher_root,
                                expected_plan_id=reviewed["plan_id"],
                                state_root=state_root,
                                launcher_java_state=java_state,
                            )
            self.assertFalse((workspace / "state").exists())
            self.assertFalse((workspace / "launcher").exists())
            self.assertFalse((workspace / "java").exists())

            symlink_state = root / "symlink-state"
            symlink_state.mkdir()
            (symlink_state / "material-fluid").symlink_to(
                workspace,
                target_is_directory=True,
            )
            with patch(
                "workbench_shell.material_fluid_flow.plan_material_backed_fluid",
                return_value=plan,
            ):
                with self.assertRaisesRegex(
                    MaterialFluidFlowError,
                    "cannot traverse a symbolic link",
                ):
                    execute_material_fluid_trial(
                        SUITE_ROOT,
                        workspace,
                        name="Pilot Coolant",
                        color="0x425d73",
                        launcher_executable=root / "prismlauncher",
                        launcher_root=root / "launcher",
                        expected_plan_id=reviewed["plan_id"],
                        state_root=symlink_state,
                    )
            self.assertFalse((workspace / "attempts").exists())

            unreviewed_patch = root / "unreviewed-patch.json"
            unreviewed_patch.write_text("{}\n", encoding="utf-8")
            with patch(
                "workbench_shell.material_fluid_flow.plan_material_backed_fluid",
                return_value=plan,
            ):
                with self.assertRaisesRegex(
                    MaterialFluidFlowError,
                    "only its exact profile-authorized",
                ):
                    plan_material_fluid_trial(
                        SUITE_ROOT,
                        workspace,
                        name="Pilot Coolant",
                        color="0x425d73",
                        compatibility_patches=[unreviewed_patch],
                    )

    def test_real_profile_authority_projects_the_bound_runtime_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _real_profile_runtime(Path(temporary))
            assessment, assertions = _apply_profile_assertions(
                SUITE_ROOT,
                fixture["runtime"],
                fixture["spec"],
                fixture["probe"],
                fixture["compatibility_policy"],
                fixture["expected_runtime_plan"],
                fixture["staged"],
                fixture["evidence_root"],
                _pending_assertions(),
            )
            self.assertEqual("observed", assessment["state"])
            self.assertTrue(all(assessment["checks"].values()))
            self.assertEqual(
                {
                    "fluid_registration": "observed",
                    "groovy_compilation": "observed",
                    "localization": "observed",
                    "material_registration": "observed",
                },
                assessment["developer_assertions"],
            )
            for key in assessment["developer_assertions"]:
                self.assertEqual("observed", assertions[key]["state"])

            runtime = fixture["runtime"]
            runtime["outcome"] = "analysis-incomplete"
            runtime["receipt"]["outcome"] = "analysis-incomplete"
            runtime["receipt"]["state"] = "incomplete"
            session = dict(runtime["receipt"])
            session.pop("session_id")
            runtime["receipt"]["session_id"] = "sha256:" + sha256(
                json.dumps(
                    session,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            session_path = Path(
                urlparse(runtime["receipt"]["target"]["receipt_uri"]).path
            )
            session_path.write_text(
                json.dumps(runtime["receipt"], indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            assessment, assertions = _apply_profile_assertions(
                SUITE_ROOT,
                runtime,
                fixture["spec"],
                fixture["probe"],
                fixture["compatibility_policy"],
                fixture["expected_runtime_plan"],
                fixture["staged"],
                fixture["evidence_root"],
                _pending_assertions(),
            )
            self.assertEqual("observed", assessment["state"])
            self.assertTrue(
                all(
                    assertions[key]["state"] == "observed"
                    for key in assessment["developer_assertions"]
                )
            )

    def test_profile_capture_rejects_stale_or_tampered_launch_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = _real_profile_runtime(root)
            runtime = fixture["runtime"]
            runtime["receipt"]["launch"]["final_launch_id"] = "sha256:" + "9" * 64
            assessment, assertions = _apply_profile_assertions(
                SUITE_ROOT,
                runtime,
                fixture["spec"],
                fixture["probe"],
                fixture["compatibility_policy"],
                fixture["expected_runtime_plan"],
                fixture["staged"],
                fixture["evidence_root"],
                _pending_assertions(),
            )
            self.assertEqual("inconclusive", assessment["state"])
            self.assertIn("disagree", assessment["error"]["message"])
            for key in (
                "groovy_compilation",
                "material_registration",
                "fluid_registration",
                "localization",
            ):
                self.assertEqual("not-observed", assertions[key]["state"])

            missing = root / "missing-session"
            missing.mkdir()
            fixture = _real_profile_runtime(missing)
            session_path = Path(
                urlparse(
                    fixture["runtime"]["receipt"]["target"]["receipt_uri"]
                ).path
            )
            session_path.unlink()
            assessment, _assertions = _apply_profile_assertions(
                SUITE_ROOT,
                fixture["runtime"],
                fixture["spec"],
                fixture["probe"],
                fixture["compatibility_policy"],
                fixture["expected_runtime_plan"],
                fixture["staged"],
                fixture["evidence_root"],
                _pending_assertions(),
            )
            self.assertEqual("inconclusive", assessment["state"])
            self.assertIn("session target", assessment["error"]["message"])

            drifted = root / "drifted-materialization"
            drifted.mkdir()
            fixture = _real_profile_runtime(drifted)
            receipt_path = Path(
                urlparse(
                    fixture["expected_runtime_plan"]["target"][
                        "fixture_root_uri"
                    ]
                ).path
            ) / "receipts/packwiz-materialization-v2.json"
            value = json.loads(receipt_path.read_text(encoding="utf-8"))
            value["workspace"]["root_uri"] = (drifted / "other").as_uri()
            receipt_path.write_text(
                json.dumps(value, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            assessment, _assertions = _apply_profile_assertions(
                SUITE_ROOT,
                fixture["runtime"],
                fixture["spec"],
                fixture["probe"],
                fixture["compatibility_policy"],
                fixture["expected_runtime_plan"],
                fixture["staged"],
                fixture["evidence_root"],
                _pending_assertions(),
            )
            self.assertEqual("inconclusive", assessment["state"])
            self.assertIn("staged materialization", assessment["error"]["message"])

    def test_final_launch_requires_exact_profile_and_probe_patch_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _real_profile_runtime(Path(temporary))
            original = fixture["runtime"]["launch_receipt"]
            for label, records in (
                ("missing", original["compatibility_patches"][1:]),
                ("extra", [*original["compatibility_patches"], {"patch_id": "x"}]),
            ):
                with self.subTest(label=label):
                    launch = deepcopy(original)
                    launch["compatibility_patches"] = records
                    launch["projection"]["compatibility_patches"] = records
                    with self.assertRaisesRegex(
                        MaterialFluidFlowError, "exact reviewed compatibility set"
                    ):
                        _validate_compatibility_records(
                            launch,
                            fixture["compatibility_policy"],
                            fixture["probe"],
                        )
            launch = deepcopy(original)
            launch["compatibility_patches"][0]["spec_sha256"] = "0" * 64
            launch["projection"]["compatibility_patches"] = launch[
                "compatibility_patches"
            ]
            with self.assertRaisesRegex(
                MaterialFluidFlowError, "differs from its reviewed policy"
            ):
                _validate_compatibility_records(
                    launch,
                    fixture["compatibility_policy"],
                    fixture["probe"],
                )

    def test_runtime_summary_binds_final_receipt_and_staged_plan_before_fml(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            fixture = _real_profile_runtime(root)
            runtime = fixture["runtime"]
            wrong_plan = deepcopy(fixture["expected_runtime_plan"])
            wrong_plan["plan_id"] = "sha256:" + ("7" * 64)
            with self.assertRaisesRegex(
                MaterialFluidFlowError,
                "exact staged runtime plan",
            ):
                _runtime_summary(
                    runtime,
                    expected_evidence_root=fixture["evidence_root"],
                    launcher_root=fixture["launcher_root"],
                    source_workspace=source,
                    staged_workspace=fixture["staged"],
                    expected_runtime_plan=wrong_plan,
                    compatibility_policy=fixture["compatibility_policy"],
                    probe=fixture["probe"],
                )

            runtime["receipt"]["launch"]["final_launch_id"] = "sha256:" + "9" * 64
            with self.assertRaisesRegex(
                MaterialFluidFlowError,
                "disagree",
            ):
                _runtime_summary(
                    runtime,
                    expected_evidence_root=fixture["evidence_root"],
                    launcher_root=fixture["launcher_root"],
                    source_workspace=source,
                    staged_workspace=fixture["staged"],
                    expected_runtime_plan=fixture["expected_runtime_plan"],
                    compatibility_policy=fixture["compatibility_policy"],
                    probe=fixture["probe"],
                )

            second = root / "second"
            second.mkdir()
            fixture = _real_profile_runtime(second)
            runtime = fixture["runtime"]
            launch_path = Path(
                urlparse(runtime["launch_receipt"]["target"]["receipt_uri"]).path
            )
            launch_path.write_text("not a receipt\n", encoding="utf-8")
            assessment, assertions = _apply_profile_assertions(
                SUITE_ROOT,
                runtime,
                fixture["spec"],
                fixture["probe"],
                fixture["compatibility_policy"],
                fixture["expected_runtime_plan"],
                fixture["staged"],
                fixture["evidence_root"],
                _pending_assertions(),
            )
            self.assertEqual("inconclusive", assessment["state"])
            self.assertIn("differs", assessment["error"]["message"])
            for key in (
                "groovy_compilation",
                "material_registration",
                "fluid_registration",
                "localization",
            ):
                self.assertEqual("not-observed", assertions[key]["state"])

    def test_runtime_summary_rejects_captured_minecraft_crash_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            fixture = _real_profile_runtime(root)
            runtime = fixture["runtime"]
            launch = runtime["launch_receipt"]
            crash_path = Path(
                urlparse(launch["target"]["run_root_uri"]).path
            ) / "final/minecraft-crash-report.txt"
            crash = b"fixture crash report\n"
            crash_path.write_bytes(crash)
            launch["evidence"].append({
                "capture_uri": crash_path.as_uri(),
                "label": "minecraft-crash-report",
                "sha256": sha256(crash).hexdigest(),
                "size": len(crash),
                "state": "captured",
            })
            _retain_rebound_runtime(runtime)

            with self.assertRaisesRegex(
                MaterialFluidFlowError,
                "captured a Minecraft crash report",
            ):
                _runtime_summary(
                    runtime,
                    expected_evidence_root=fixture["evidence_root"],
                    launcher_root=fixture["launcher_root"],
                    source_workspace=source,
                    staged_workspace=fixture["staged"],
                    expected_runtime_plan=fixture["expected_runtime_plan"],
                    compatibility_policy=fixture["compatibility_policy"],
                    probe=fixture["probe"],
                )

    def test_runtime_summary_rejects_incomplete_exit_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            fixture = _real_profile_runtime(root)
            runtime = fixture["runtime"]
            lifecycle = {"state": "exited"}
            runtime["launch_receipt"]["observation"]["session_exit"] = lifecycle
            runtime["receipt"]["launch"]["process_observation"] = lifecycle
            _retain_rebound_runtime(runtime)

            with self.assertRaisesRegex(
                MaterialFluidFlowError,
                "exact projected-client process-exit evidence",
            ):
                _runtime_summary(
                    runtime,
                    expected_evidence_root=fixture["evidence_root"],
                    launcher_root=fixture["launcher_root"],
                    source_workspace=source,
                    staged_workspace=fixture["staged"],
                    expected_runtime_plan=fixture["expected_runtime_plan"],
                    compatibility_policy=fixture["compatibility_policy"],
                    probe=fixture["probe"],
                )

    def test_runtime_summary_requires_bound_fml_log_and_v3_lane(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            fixture = _real_profile_runtime(root)
            runtime = fixture["runtime"]
            latest = next(
                item
                for item in runtime["launch_receipt"]["evidence"]
                if item["label"] == "minecraft-latest-log"
            )
            latest_path = Path(urlparse(latest["capture_uri"]).path)
            payload = b"latest log without the loader checkpoint\n"
            latest_path.write_bytes(payload)
            latest["sha256"] = sha256(payload).hexdigest()
            latest["size"] = len(payload)
            _retain_rebound_runtime(runtime)
            with self.assertRaisesRegex(
                MaterialFluidFlowError,
                "does not prove the FML checkpoint",
            ):
                _runtime_summary(
                    runtime,
                    expected_evidence_root=fixture["evidence_root"],
                    launcher_root=fixture["launcher_root"],
                    source_workspace=source,
                    staged_workspace=fixture["staged"],
                    expected_runtime_plan=fixture["expected_runtime_plan"],
                    compatibility_policy=fixture["compatibility_policy"],
                    probe=fixture["probe"],
                )

            duplicate = root / "duplicate-evidence"
            duplicate.mkdir()
            fixture = _real_profile_runtime(duplicate)
            runtime = fixture["runtime"]
            runtime["launch_receipt"]["evidence"].append({
                "label": "minecraft-groovy-log",
                "state": "absent",
                "source_uri": (
                    fixture["projection"] / ".minecraft/logs/groovy.log"
                ).as_uri(),
            })
            _retain_rebound_runtime(runtime)
            with self.assertRaisesRegex(
                MaterialFluidFlowError,
                "duplicate or invalid evidence labels",
            ):
                _runtime_summary(
                    runtime,
                    expected_evidence_root=fixture["evidence_root"],
                    launcher_root=fixture["launcher_root"],
                    source_workspace=source,
                    staged_workspace=fixture["staged"],
                    expected_runtime_plan=fixture["expected_runtime_plan"],
                    compatibility_policy=fixture["compatibility_policy"],
                    probe=fixture["probe"],
                )

            other = root / "other-v3"
            other.mkdir()
            fixture = _real_profile_runtime(other)
            runtime = fixture["runtime"]
            escaped = other / "escaped-runtime-launch-v3.json"
            runtime["launch_receipt"]["target"]["receipt_uri"] = escaped.as_uri()
            _retain_rebound_runtime(runtime)
            with self.assertRaisesRegex(
                MaterialFluidFlowError,
                "outside the bound runtime run root",
            ):
                _runtime_summary(
                    runtime,
                    expected_evidence_root=fixture["evidence_root"],
                    launcher_root=fixture["launcher_root"],
                    source_workspace=source,
                    staged_workspace=fixture["staged"],
                    expected_runtime_plan=fixture["expected_runtime_plan"],
                    compatibility_policy=fixture["compatibility_policy"],
                    probe=fixture["probe"],
                )

    def test_malformed_profile_assessment_preserves_independent_fml_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _real_profile_runtime(Path(temporary))
            spec = fixture["spec"]
            probe = fixture["probe"]
            runtime = fixture["runtime"]
            authority = _profile_observation_authority(SUITE_ROOT)
            contradictory = {
                "format": "workbench-supersymmetry-material-fluid-assessment-v2",
                "schema_version": 2,
                "state": "observed",
                "developer_assertions": {
                    "fluid_registration": "observed",
                    "groovy_compilation": "observed",
                    "localization": "failed",
                    "material_registration": "observed",
                },
            }
            for malformed in (
                None,
                {"developer_assertions": {}},
                contradictory,
            ):
                with self.subTest(malformed=malformed):
                    assertions = _pending_assertions()
                    assertions["fml_client_load"]["state"] = "observed"
                    with patch.object(
                        authority,
                        "interpret_material_fluid_observation",
                        return_value=malformed,
                    ):
                        assessment, projected = _apply_profile_assertions(
                            SUITE_ROOT,
                            runtime,
                            spec,
                            probe,
                            fixture["compatibility_policy"],
                            fixture["expected_runtime_plan"],
                            fixture["staged"],
                            fixture["evidence_root"],
                            assertions,
                        )
                    self.assertEqual("inconclusive", assessment["state"])
                    self.assertEqual(
                        "observed", projected["fml_client_load"]["state"]
                    )
                    for key in (
                        "groovy_compilation",
                        "material_registration",
                        "fluid_registration",
                        "localization",
                    ):
                        self.assertEqual("not-observed", projected[key]["state"])

if __name__ == "__main__":
    unittest.main()
