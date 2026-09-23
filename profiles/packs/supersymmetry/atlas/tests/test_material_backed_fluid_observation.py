from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import json
import unittest

from workbench_profile_supersymmetry.material_backed_fluid_observation import (
    MARKER_PREFIX,
    MaterialFluidObservationError,
    MaterialFluidProbeSpec,
    build_material_fluid_probe,
    build_material_fluid_probe_overlay,
    interpret_material_fluid_observation,
    validate_material_fluid_assessment,
)


SPEC = MaterialFluidProbeSpec(
    registry_name="workbench_pilot_coolant",
    symbol_name="WorkbenchPilotCoolant",
    material_id=20008,
    color_rgb=0x425D73,
    translation="Workbench Pilot Coolant",
    source_plan_id="sha256:" + "a" * 64,
)


def _marker(*, failed: str | None = None) -> bytes:
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
    actual = {
        "color_rgb": 0x425D73,
        "fluid_name": "workbench_pilot_coolant",
        "forge_registry_roundtrip": True,
        "has_flammable_flag": True,
        "has_fluid_property": True,
        "localized_name": "Workbench Pilot Coolant",
        "manager_phase": "FROZEN",
        "material_id": 20008,
        "material_resource": "susy:workbench_pilot_coolant",
    }
    if failed is not None:
        checks[failed] = False
        actual_key = {
            "manager_frozen": "manager_phase",
        }.get(failed, failed)
        if actual_key in {
            "forge_registry_roundtrip",
            "has_flammable_flag",
            "has_fluid_property",
        }:
            actual[actual_key] = False
        else:
            actual[actual_key] = None
    value = {
        "actual": actual,
        "checks": checks,
        "error_kind": None,
        "format": "workbench-material-fluid-observation-v1",
        "probe_id": SPEC.probe_id,
        "source_plan_id": SPEC.source_plan_id,
        "stage": "postInit",
        "state": "mismatch" if failed else "observed",
    }
    raw = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).rstrip(b"=")
    return b"[INFO] unrelated\n[INFO] " + MARKER_PREFIX.encode() + token + b"\n"


def _capture(log: bytes) -> dict:
    return {
        "groovy_log_sha256": sha256(log).hexdigest(),
        "groovy_log_uri": "file:///capture/groovy.log",
        "launch_id": "sha256:" + "b" * 64,
        "launch_receipt_sha256": "c" * 64,
        "launch_receipt_uri": "file:///capture/runtime-launch-v3.json",
        "materialization_id": "sha256:" + "d" * 64,
        "materialization_receipt_sha256": "e" * 64,
        "materialization_receipt_size": 1024,
        "materialization_receipt_uri": "file:///capture/materialization.json",
        "payload": {
            "file_count": 1,
            "total_bytes": 1,
            "tree_sha256": "sha256:" + "f" * 64,
        },
        "probe_id": SPEC.probe_id,
        "probe_overlay_id": build_material_fluid_probe_overlay(SPEC)["patch_id"],
        "probe_script_sha256": sha256(build_material_fluid_probe(SPEC)).hexdigest(),
        "session_outcome": "completed",
        "session_receipt_sha256": "1" * 64,
        "session_receipt_size": 1024,
        "session_receipt_uri": "file:///capture/runtime-observation-v1.json",
    }


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _reseal_assessment(assessment: dict) -> None:
    snapshot = assessment["crucible_snapshot"]
    record = snapshot["registry"][0]
    record_material = {
        key: value for key, value in record.items() if key != "runtime_record_id"
    }
    record["runtime_record_id"] = (
        "workbench-crucible-registry-observation:sha256:"
        + sha256(_canonical_bytes(record_material)).hexdigest()
    )
    snapshot_material = {
        key: value for key, value in snapshot.items() if key != "snapshot_id"
    }
    snapshot["snapshot_id"] = (
        "workbench-crucible-stage-snapshot:sha256:"
        + sha256(_canonical_bytes(snapshot_material)).hexdigest()
    )
    assessment_material = {
        "probe_id": SPEC.probe_id,
        "snapshot_id": snapshot["snapshot_id"],
        "source_plan_id": SPEC.source_plan_id,
        "state": assessment["state"],
        "checks": dict(assessment["checks"]),
        "developer_assertions": dict(assessment["developer_assertions"]),
        "failed_checks": list(assessment["failed_checks"]),
    }
    assessment["assessment_id"] = (
        "workbench-atlas-material-fluid-assessment:sha256:"
        + sha256(_canonical_bytes(assessment_material)).hexdigest()
    )


class MaterialBackedFluidObservationTests(unittest.TestCase):
    def test_probe_binds_every_required_runtime_property(self) -> None:
        script = build_material_fluid_probe(SPEC).decode("utf-8")
        for token in (
            "susy:workbench_pilot_coolant",
            "workbench_pilot_coolant",
            "20008",
            "getMaterialRGB",
            "PropertyKey.FLUID",
            "MaterialFlags.FLAMMABLE",
            "FluidRegistry.getFluid",
            "getLocalizedName",
            "FROZEN",
            MARKER_PREFIX,
        ):
            self.assertIn(token, script)
        self.assertNotIn("new Material.Builder", script)
        self.assertNotIn("groovy.json", script)
        self.assertIn("out.append((char) 92)", script)
        self.assertEqual(script.encode("utf-8"), build_material_fluid_probe(SPEC))
        overlay = build_material_fluid_probe_overlay(SPEC)
        self.assertEqual(
            ".minecraft/groovy/postInit/WorkbenchMaterialBackedFluidAssertion.groovy",
            overlay["target"]["path"],
        )
        self.assertTrue(overlay["target"]["must_be_absent"])
        self.assertEqual(
            sha256(script.encode()).hexdigest(),
            overlay["source"]["sha256"],
        )

    def test_observed_marker_seals_crucible_snapshot_and_atlas_assessment(self) -> None:
        log = _marker()
        result = interpret_material_fluid_observation(
            SPEC,
            groovy_log_bytes=log,
            capture=_capture(log),
        )
        self.assertEqual("observed", result["state"])
        self.assertEqual([], result["failed_checks"])
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(
            {
                "fluid_registration": "observed",
                "groovy_compilation": "observed",
                "localization": "observed",
                "material_registration": "observed",
            },
            result["developer_assertions"],
        )
        self.assertEqual("Atlas", result["authority"]["owner"])
        snapshot = result["crucible_snapshot"]
        self.assertEqual("Crucible", snapshot["authority"]["owner"])
        self.assertEqual(1, snapshot["summary"]["registry_records"])
        self.assertEqual("postInit", snapshot["binding"]["stage"])

        capture = _capture(log)
        capture["session_outcome"] = "analysis-incomplete"
        retained = interpret_material_fluid_observation(
            SPEC,
            groovy_log_bytes=log,
            capture=capture,
        )
        self.assertEqual("observed", retained["state"])

    def test_valid_mismatch_is_retained_as_failure_not_success(self) -> None:
        log = _marker(failed="localized_name")
        result = interpret_material_fluid_observation(
            SPEC,
            groovy_log_bytes=log,
            capture=_capture(log),
        )
        self.assertEqual("mismatch", result["state"])
        self.assertEqual(["localized_name"], result["failed_checks"])
        self.assertEqual("failed", result["developer_assertions"]["localization"])
        self.assertEqual(
            "observed", result["developer_assertions"]["material_registration"]
        )

    def test_retained_assessment_revalidates_profile_checks_and_identity(self) -> None:
        log = _marker()
        assessment = interpret_material_fluid_observation(
            SPEC,
            groovy_log_bytes=log,
            capture=_capture(log),
        )
        self.assertEqual(
            assessment,
            validate_material_fluid_assessment(SPEC, assessment),
        )
        for mutation in ("check", "profile", "identity"):
            tampered = deepcopy(assessment)
            if mutation == "check":
                tampered["checks"]["material_id"] = False
            elif mutation == "profile":
                tampered["profile"]["platform_profile_id"] = "wrong"
            else:
                tampered["assessment_id"] = (
                    "workbench-atlas-material-fluid-assessment:sha256:" + "f" * 64
                )
            with self.subTest(mutation=mutation), self.assertRaises(
                MaterialFluidObservationError
            ):
                validate_material_fluid_assessment(SPEC, tampered)

    def test_retained_assessment_rejects_resealed_authority_and_snapshot_rebinding(
        self,
    ) -> None:
        log = _marker()
        assessment = interpret_material_fluid_observation(
            SPEC,
            groovy_log_bytes=log,
            capture=_capture(log),
        )
        for mutation in (
            "atlas-authority",
            "atlas-limitations",
            "registry-descriptor",
            "source-provenance",
        ):
            tampered = deepcopy(assessment)
            if mutation == "atlas-authority":
                tampered["authority"]["owner"] = "Blueprints"
            elif mutation == "atlas-limitations":
                tampered["limitations"] = ["forged general runtime authority"]
            elif mutation == "registry-descriptor":
                tampered["crucible_snapshot"]["registry"][0][
                    "semantic_descriptor"
                ]["key"]["material_resource"] = "susy:foreign_material"
            else:
                tampered["crucible_snapshot"]["registry"][0]["provenance"][
                    "source_plan_id"
                ] = "sha256:" + "c" * 64
            _reseal_assessment(tampered)
            with self.subTest(mutation=mutation), self.assertRaises(
                MaterialFluidObservationError
            ):
                validate_material_fluid_assessment(SPEC, tampered)

    def test_absent_duplicate_noncanonical_and_rebound_markers_fail_closed(self) -> None:
        with self.assertRaises(MaterialFluidObservationError):
            interpret_material_fluid_observation(
                SPEC,
                groovy_log_bytes=b"ordinary log\n",
                capture=_capture(b"ordinary log\n"),
            )
        with self.assertRaises(MaterialFluidObservationError):
            interpret_material_fluid_observation(
                SPEC,
                groovy_log_bytes=_marker() + _marker(),
                capture=_capture(_marker() + _marker()),
            )

        line = _marker().splitlines()[-1]
        token = line.split(MARKER_PREFIX.encode(), 1)[1]
        raw = base64.urlsafe_b64decode(token + b"=" * (-len(token) % 4))
        rebound = json.loads(raw)
        rebound["source_plan_id"] = "sha256:" + "c" * 64
        rebound_raw = json.dumps(
            rebound, separators=(",", ":"), sort_keys=True
        ).encode()
        rebound_token = base64.urlsafe_b64encode(rebound_raw).rstrip(b"=")
        with self.assertRaises(MaterialFluidObservationError):
            interpret_material_fluid_observation(
                SPEC,
                groovy_log_bytes=MARKER_PREFIX.encode() + rebound_token + b"\n",
                capture=_capture(MARKER_PREFIX.encode() + rebound_token + b"\n"),
            )

        pretty = json.dumps(json.loads(raw), indent=2, sort_keys=True).encode()
        pretty_token = base64.urlsafe_b64encode(pretty).rstrip(b"=")
        with self.assertRaises(MaterialFluidObservationError):
            interpret_material_fluid_observation(
                SPEC,
                groovy_log_bytes=MARKER_PREFIX.encode() + pretty_token + b"\n",
                capture=_capture(MARKER_PREFIX.encode() + pretty_token + b"\n"),
            )

    def test_marker_cannot_claim_observed_with_failed_check(self) -> None:
        log = _marker(failed="fluid_name")
        line = log.splitlines()[-1]
        token = line.split(MARKER_PREFIX.encode(), 1)[1]
        raw = base64.urlsafe_b64decode(token + b"=" * (-len(token) % 4))
        value = json.loads(raw)
        value["state"] = "observed"
        forged_raw = json.dumps(
            value, separators=(",", ":"), sort_keys=True
        ).encode()
        forged = base64.urlsafe_b64encode(forged_raw).rstrip(b"=")
        with self.assertRaises(MaterialFluidObservationError):
            interpret_material_fluid_observation(
                SPEC,
                groovy_log_bytes=MARKER_PREFIX.encode() + forged + b"\n",
                capture=_capture(MARKER_PREFIX.encode() + forged + b"\n"),
            )


if __name__ == "__main__":
    unittest.main()
