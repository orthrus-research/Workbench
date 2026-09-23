from __future__ import annotations

from copy import deepcopy
import hashlib
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
    CONFIG_LIFECYCLE_RECEIPT_PREFIX,
    ConfigLifecycleValidationError,
    build_config_lifecycle_receipt,
    canonical_config_lifecycle_json_bytes,
    parse_config_lifecycle_receipt,
    parse_raw_config_lifecycle,
)
from workbench_crucible_runtime_snapshot import bind_known_receipt  # noqa: E402


SCHEMA = ROOT / "modules/crucible/schemas/mixin-config-lifecycle-receipt-v1.schema.json"
CAPTURE = "capture:config-lifecycle:test"
CONFIG = "mixins.fixture.early.json"
OWNER_URI = "file:/fixture.jar"
RESOURCE_URL = f"jar:{OWNER_URI}!/{CONFIG}"
TARGETS = {
    "org.spongepowered.asm.mixin.Mixins": "1" * 64,
    "org.spongepowered.asm.mixin.transformer.Config": "2" * 64,
    "org.spongepowered.asm.mixin.transformer.MixinConfig": "3" * 64,
    "org.spongepowered.asm.mixin.transformer.MixinProcessor": "4" * 64,
}


def _raw_rows() -> list[dict]:
    rows: list[dict] = []

    def add(event: str, payload: dict) -> None:
        rows.append(
            {
                "format": "workbench-cleanmix-config-lifecycle-raw-v1",
                "capture_id": CAPTURE,
                "sequence": len(rows),
                "event": event,
                "payload": payload,
            }
        )

    add(
        "capture_start",
        {
            "agent_id": "test-agent",
            "java_version": "25",
            "targets": [
                {"target_class": target, "expected_input_sha256": digest}
                for target, digest in TARGETS.items()
            ],
            "redefine_supported": False,
            "retransform_supported": False,
        },
    )
    add("transformer_installed", {"target_count": 4, "retransform_requested": False})
    for target, digest in TARGETS.items():
        add(
            "transform_applied",
            {
                "target_class": target,
                "defining_loader_class": "net.minecraft.launchwrapper.LaunchClassLoader",
                "defining_loader_identity": "LaunchClassLoader@test",
                "target_code_source_uri": f"jar:file:/cleanmix.jar!/{target.replace('.', '/')}.class",
                "input_sha256": digest,
                "output_sha256": "9" * 64,
                "reason": None,
            },
        )
    add(
        "config_create_started",
        {
            "attempt": 1,
            "requested_config": CONFIG,
            "fallback_phase": "DEFAULT",
            "source_id": "fixture",
            "source_description": OWNER_URI,
            "resolved_resource_url": RESOURCE_URL,
            "resource_url_basis": "config_source_description_plus_requested_path",
        },
    )
    add(
        "feature_check_started",
        {"attempt": 1, "config_name": CONFIG, "required": True, "required_features": [], "outcome": None},
    )
    add(
        "feature_check_completed",
        {"attempt": 1, "config_name": CONFIG, "required": True, "required_features": [], "outcome": "passed"},
    )
    config_identity = "org.spongepowered.asm.mixin.transformer.Config@test"
    add(
        "config_create_completed",
        {
            "attempt": 1,
            "requested_config": CONFIG,
            "config_name": CONFIG,
            "config_identity": config_identity,
            "resource_url": RESOURCE_URL,
            "resource_url_basis": "config_source_description_plus_requested_path",
            "required": True,
            "queued_phase": "DEFAULT",
            "source_id": "fixture",
            "source_description": OWNER_URI,
            "outcome": "returned_config",
        },
    )
    add(
        "registration_started",
        {
            "attempt": 1,
            "requested_config": CONFIG,
            "config_name": CONFIG,
            "config_identity": config_identity,
            "previously_admitted": False,
        },
    )
    add(
        "admission_decision",
        {
            "attempt": 1,
            "requested_config": CONFIG,
            "config_name": CONFIG,
            "config_identity": config_identity,
            "resource_url": RESOURCE_URL,
            "resource_url_basis": "config_source_description_plus_requested_path",
            "required": True,
            "queued_phase": "DEFAULT",
            "source_id": "fixture",
            "source_description": OWNER_URI,
            "decision": "admitted",
            "reason_code": "queued",
        },
    )
    for phase, eligible in (("PREINIT", False), ("INIT", False), ("DEFAULT", True)):
        add(
            "phase_eligibility",
            {
                "attempt": 1,
                "config_name": CONFIG,
                "config_identity": config_identity,
                "queued_phase": "DEFAULT",
                "consumption_phase": phase,
                "eligible": eligible,
                "outcome": "consumed" if eligible else "deferred",
                "reason_code": "phase_reached" if eligible else "phase_not_reached",
            },
        )
    for stage in ("onSelect", "prepare", "post_initialise"):
        for outcome in ("started", "completed"):
            add(
                "config_stage",
                {
                    "attempt": 1,
                    "config_name": CONFIG,
                    "config_identity": config_identity,
                    "stage": stage,
                    "outcome": outcome,
                    "consumption_phase": "DEFAULT",
                },
            )
    add(
        "batch_promotion",
        {
            "attempt": 1,
            "config_name": CONFIG,
            "config_identity": config_identity,
            "consumption_phase": "DEFAULT",
            "outcome": "active",
            "reason_code": "promoted_to_active",
        },
    )
    add(
        "terminal_state",
        {"attempt": 1, "requested_config": CONFIG, "config_name": CONFIG, "outcome": "active", "reason_code": "promoted_to_active"},
    )
    add(
        "capture_end",
        {
            "health": "healthy",
            "transformed_targets": sorted(TARGETS),
            "attempt_count": 1,
            "admitted_count": 1,
            "active_count": 1,
            "deferred_count": 0,
            "failure_count": 0,
            "observer_failure_count": 0,
            "write_failure": False,
        },
    )
    return rows


def _encoded(rows: list[dict]) -> bytes:
    return b"".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True).encode() + b"\n"
        for row in rows
    )


def _receipt() -> dict:
    rows = parse_raw_config_lifecycle(_encoded(_raw_rows()))
    files = {
        name: {"label": f"{name}.bin", "sha256": str(index) * 64, "size_bytes": index}
        for index, name in enumerate(
            ("agent_artifact", "candidate_lock", "fixture_result", "launch_log", "raw_trace", "toolchain_lock"),
            start=1,
        )
    }
    artifacts = [
        {
            "artifact_sha256": "a" * 64,
            "size_bytes": 100,
            "label": "cleanmix.jar",
            "code_source_uri": f"jar:file:/cleanmix.jar!/{target.replace('.', '/')}.class",
        }
        for target in TARGETS
    ]
    artifacts.append(
        {"artifact_sha256": "b" * 64, "size_bytes": 200, "label": "fixture.jar", "code_source_uri": OWNER_URI}
    )
    return build_config_lifecycle_receipt(
        session={
            "capture_id": CAPTURE,
            "launch_id": "launch:test",
            "profile_id": "workbench-platform:cleanroom:test",
            "side": "dedicated_server",
            "candidate_lock_sha256": files["candidate_lock"]["sha256"],
            "toolchain_lock_sha256": files["toolchain_lock"]["sha256"],
        },
        inputs=files,
        artifacts=artifacts,
        resource_evidence=[
            {"attempt": 1, "resource_url": RESOURCE_URL, "artifact_sha256": "b" * 64, "resource_entry_sha256": "c" * 64}
        ],
        raw_events=rows,
    )


def _reseal(receipt: dict) -> dict:
    material = deepcopy(receipt)
    material.pop("receipt_id")
    return {
        **material,
        "receipt_id": CONFIG_LIFECYCLE_RECEIPT_PREFIX
        + hashlib.sha256(canonical_config_lifecycle_json_bytes(material)).hexdigest(),
    }


class ConfigLifecycleReceiptTests(unittest.TestCase):
    def test_active_config_receipt_is_closed_content_addressed_and_schema_valid(self) -> None:
        receipt = _receipt()
        Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(receipt)
        self.assertEqual(receipt, parse_config_lifecycle_receipt(receipt))
        config = receipt["configurations"][0]
        self.assertEqual("active", config["terminal"]["outcome"])
        self.assertEqual([False, False, True], [row["eligible"] for row in config["phase_checks"]])
        self.assertEqual("b" * 64, config["resource"]["artifact_sha256"])

    def test_active_attempt_requires_every_completed_stage(self) -> None:
        rows = _raw_rows()
        rows = [
            row for row in rows
            if not (
                row["event"] == "config_stage"
                and row["payload"]["stage"] == "prepare"
                and row["payload"]["outcome"] == "completed"
            )
        ]
        for sequence, row in enumerate(rows):
            row["sequence"] = sequence
        with self.assertRaisesRegex(ConfigLifecycleValidationError, "preparation stage"):
            parse_raw_config_lifecycle(_encoded(rows))

    def test_exact_transform_guard_cannot_be_bypassed(self) -> None:
        rows = _raw_rows()
        next(row for row in rows if row["event"] == "transform_applied")["payload"]["input_sha256"] = "f" * 64
        with self.assertRaisesRegex(ConfigLifecycleValidationError, "exact guard"):
            parse_raw_config_lifecycle(_encoded(rows))

    def test_verified_resource_must_match_observed_url(self) -> None:
        receipt = _receipt()
        mutated = deepcopy(receipt)
        mutated["configurations"][0]["resource"]["url"] = "jar:file:/wrong.jar!/wrong.json"
        with self.assertRaisesRegex(ConfigLifecycleValidationError, "identity mismatch"):
            parse_config_lifecycle_receipt(mutated)

    def test_snapshot_v2_binds_the_config_lifecycle_capability(self) -> None:
        encoded = json.dumps(
            _receipt(), separators=(",", ":"), sort_keys=True
        ).encode()
        binding = bind_known_receipt(
            encoded, source_label="receipts/config-lifecycle.json"
        )
        self.assertEqual("mixin.config-lifecycle", binding["role"])
        self.assertEqual(CAPTURE, binding["scope"]["capture_id"])
        self.assertEqual(
            ["candidate", "transformer-toolchain"],
            [row["kind"] for row in binding["lock_bindings"]],
        )

    def test_self_hashed_malformed_nested_fact_is_rejected(self) -> None:
        mutated = _receipt()
        mutated["configurations"][0]["phase_checks"][0]["eligible"] = "false"
        mutated["configurations"][0]["phase_checks"][0]["outcome"] = "deferred"
        with self.assertRaisesRegex(ConfigLifecycleValidationError, "must be boolean"):
            parse_config_lifecycle_receipt(_reseal(mutated))

    def test_self_hashed_unknown_nested_field_is_rejected(self) -> None:
        mutated = _receipt()
        mutated["configurations"][0]["resource"]["guessed_owner"] = "no"
        with self.assertRaisesRegex(ConfigLifecycleValidationError, "unknown fields"):
            parse_config_lifecycle_receipt(_reseal(mutated))


if __name__ == "__main__":
    unittest.main()
