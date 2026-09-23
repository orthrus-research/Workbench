from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
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
    LEDGER_FORMAT,
    OBSERVATION_SET_FORMAT,
    LedgerValidationError,
    build_ledger,
    canonical_json_bytes,
    parse_ledger,
    write_ledger,
)


SCHEMA_PATH = (
    ROOT
    / "modules/crucible/schemas/mixin-transformation-ledger-v1.schema.json"
)
TOOL_PATH = ROOT / "modules/crucible/tools/assemble_mixin_transformation_ledger.py"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def session() -> dict:
    return {
        "session_id": "crucible-session:test",
        "launch_id": "launch:test",
        "profile_id": "workbench-platform:cleanroom:test",
        "side": "dedicated_server",
        "component_receipt_sha256": SHA_A,
    }


def evidence(kind: str, source: str = SHA_B) -> dict:
    return {
        "kind": kind,
        "source_sha256": source,
        "source_record": "line:1",
        "limitations": [],
    }


def apply_started(sequence: int = 1) -> dict:
    return {
        "sequence": sequence,
        "stage": "apply_started",
        "outcome": "observed",
        "phase": "DEFAULT",
        "thread": "Server thread",
        "subject": {
            "artifact_sha256": SHA_A,
            "owner_label": "example_mod",
            "config": "mixins.example.json",
            "mixin": "example.MixinTarget",
            "target_class": "example.Target",
        },
        "evidence": evidence("cleanmix_audit"),
    }


def reidentify(ledger: dict) -> None:
    material = deepcopy(ledger)
    material.pop("ledger_id")
    ledger["ledger_id"] = "crucible-mixin-ledger:sha256:" + hashlib.sha256(
        canonical_json_bytes(material)
    ).hexdigest()


class MixinTransformationLedgerTests(unittest.TestCase):
    def test_builds_canonical_bounded_ledger(self) -> None:
        ledger = build_ledger(
            session=session(),
            launch_state="complete",
            observations=[
                apply_started(),
                {
                    "sequence": 2,
                    "stage": "plugin_post_apply_seen",
                    "outcome": "observed",
                    "phase": "DEFAULT",
                    "subject": apply_started()["subject"],
                    "evidence": evidence("plugin_callback", SHA_C),
                },
                {
                    "sequence": 3,
                    "stage": "final_member_attributed",
                    "outcome": "observed",
                    "phase": "UNKNOWN",
                    "subject": {
                        "artifact_sha256": SHA_A,
                        "config": "mixins.example.json",
                        "mixin": "example.MixinTarget",
                        "target_class": "example.Target",
                        "target_member": "workbench$handler()V",
                    },
                    "evidence": evidence("foundation_final_bytes", SHA_C),
                    "final_bytecode_sha256": SHA_B,
                },
            ],
            limitations=["Bounded synthetic fixture."],
        )
        self.assertEqual(ledger["format"], LEDGER_FORMAT)
        self.assertEqual(ledger["summary"]["evidence_state"], "bounded")
        self.assertEqual(ledger["summary"]["apply_started_count"], 1)
        self.assertEqual(ledger["summary"]["final_class_defined_count"], 0)
        self.assertTrue(
            ledger["ledger_id"].startswith("crucible-mixin-ledger:sha256:")
        )
        self.assertIn(
            "A CleanMix APPLY audit entry proves only that application started, not that the applicator or later transformer chain completed.",
            ledger["limitations"],
        )
        self.assertEqual(parse_ledger(ledger), ledger)

    def test_apply_started_cannot_claim_generic_success(self) -> None:
        row = apply_started()
        row["outcome"] = "succeeded"
        with self.assertRaisesRegex(LedgerValidationError, "outcome is unsupported"):
            build_ledger(
                session=session(),
                launch_state="complete",
                observations=[row],
            )

    def test_cleanmix_postprocess_is_not_plugin_post_apply(self) -> None:
        wrong_plugin = {
            "sequence": 1,
            "stage": "plugin_post_apply_seen",
            "outcome": "observed",
            "phase": "DEFAULT",
            "subject": apply_started()["subject"],
            "evidence": evidence("cleanmix_audit"),
        }
        with self.assertRaisesRegex(LedgerValidationError, "requires plugin_callback"):
            build_ledger(
                session=session(),
                launch_state="complete",
                observations=[wrong_plugin],
            )

        postprocess = {
            "sequence": 1,
            "stage": "mixin_postprocess_seen",
            "outcome": "observed",
            "phase": "DEFAULT",
            "subject": {"target_class": "example.Accessor"},
            "evidence": evidence("cleanmix_audit"),
        }
        ledger = build_ledger(
            session=session(),
            launch_state="complete",
            observations=[postprocess],
        )
        self.assertEqual(
            ledger["summary"]["stage_counts"], {"mixin_postprocess_seen": 1}
        )

    def test_stage_to_evidence_relation_is_closed(self) -> None:
        subjects = {
            "artifact_discovered": {"artifact_sha256": SHA_A},
            "config_declared": {"config": "mixins.example.json"},
            "config_registered": {"config": "mixins.example.json"},
            "config_prepared": {"config": "mixins.example.json"},
            "apply_started": apply_started()["subject"],
            "plugin_post_apply_seen": apply_started()["subject"],
            "mixin_postprocess_seen": {"target_class": "example.Target"},
            "generated": {"generated_class": "example.Generated"},
            "final_class_defined": {"target_class": "example.Target"},
            "final_member_attributed": {
                "mixin": "example.MixinTarget",
                "target_class": "example.Target",
                "target_member": "workbench$handler()V",
            },
            "failure": {"target_class": "example.Target"},
        }
        valid = {
            "artifact_discovered": ("artifact_scan",),
            "config_declared": ("manifest",),
            "config_registered": ("config_lifecycle_probe",),
            "config_prepared": ("config_lifecycle_probe",),
            "apply_started": ("cleanmix_audit", "mixin_stage_export"),
            "plugin_post_apply_seen": ("plugin_callback",),
            "mixin_postprocess_seen": ("cleanmix_audit",),
            "generated": ("cleanmix_audit",),
            "final_class_defined": ("foundation_final_bytes",),
            "final_member_attributed": ("foundation_final_bytes",),
            "failure": ("crash_report",),
        }

        def row(stage: str, kind: str) -> dict:
            value = {
                "sequence": 1,
                "stage": stage,
                "outcome": "failed" if stage == "failure" else "observed",
                "phase": "UNKNOWN",
                "subject": deepcopy(subjects[stage]),
                "evidence": evidence(kind),
            }
            if stage in {"final_class_defined", "final_member_attributed"}:
                value["final_bytecode_sha256"] = SHA_C
            if stage == "failure":
                value["message"] = "bounded failure"
            return value

        for stage, kinds in valid.items():
            for kind in kinds:
                with self.subTest(stage=stage, valid_kind=kind):
                    ledger = build_ledger(
                        session=session(),
                        launch_state="complete",
                        observations=[row(stage, kind)],
                    )
                    self.assertEqual(stage, ledger["observations"][0]["stage"])

        invalid = {
            "artifact_discovered": "manifest",
            "config_declared": "artifact_scan",
            "config_registered": "manifest",
            "config_prepared": "manifest",
            "apply_started": "config_lifecycle_probe",
            "plugin_post_apply_seen": "cleanmix_audit",
            "mixin_postprocess_seen": "plugin_callback",
            "generated": "mixin_stage_export",
            "final_class_defined": "mixin_stage_export",
            "final_member_attributed": "plugin_callback",
            "failure": "cleanmix_audit",
        }
        for stage, kind in invalid.items():
            with self.subTest(stage=stage, invalid_kind=kind):
                with self.assertRaisesRegex(
                    LedgerValidationError, rf"{stage} requires .* evidence"
                ):
                    build_ledger(
                        session=session(),
                        launch_state="complete",
                        observations=[row(stage, kind)],
                    )

    def test_static_and_service_evidence_cannot_claim_runtime_config_lifecycle(
        self,
    ) -> None:
        declared = {
            "sequence": 1,
            "stage": "config_declared",
            "outcome": "observed",
            "phase": "UNKNOWN",
            "subject": {"config": "mixins.example.json"},
            "evidence": evidence("manifest"),
        }
        ledger = build_ledger(
            session=session(),
            launch_state="complete",
            observations=[declared],
        )
        self.assertEqual(
            ledger["summary"]["stage_counts"], {"config_declared": 1}
        )

        registered = deepcopy(declared)
        registered["stage"] = "config_registered"
        with self.assertRaisesRegex(
            LedgerValidationError, "config_registered requires config_lifecycle_probe"
        ):
            build_ledger(
                session=session(),
                launch_state="complete",
                observations=[registered],
            )

        prepared = deepcopy(declared)
        prepared["stage"] = "config_prepared"
        prepared["evidence"] = evidence("runtime_service")
        with self.assertRaisesRegex(
            LedgerValidationError, "evidence.kind is unsupported: 'runtime_service'"
        ):
            build_ledger(
                session=session(),
                launch_state="complete",
                observations=[prepared],
            )

    def test_cleanmix_audit_cannot_claim_failure(self) -> None:
        failure = {
            "sequence": 1,
            "stage": "failure",
            "outcome": "failed",
            "phase": "UNKNOWN",
            "subject": {"target_class": "example.Target"},
            "evidence": evidence("cleanmix_audit"),
            "message": "unsubstantiated failure",
        }
        with self.assertRaisesRegex(
            LedgerValidationError, "failure requires crash_report"
        ):
            build_ledger(
                session=session(),
                launch_state="crashed",
                observations=[failure],
            )

    def test_unknown_outcome_cannot_assert_or_count_final_truth(self) -> None:
        for stage, subject in (
            ("final_class_defined", {"target_class": "example.Target"}),
            (
                "final_member_attributed",
                {
                    "mixin": "example.MixinTarget",
                    "target_class": "example.Target",
                    "target_member": "workbench$handler()V",
                },
            ),
        ):
            row = {
                "sequence": 1,
                "stage": stage,
                "outcome": "unknown",
                "phase": "UNKNOWN",
                "subject": subject,
                "evidence": evidence("foundation_final_bytes"),
                "final_bytecode_sha256": SHA_C,
            }
            with self.subTest(stage=stage):
                with self.assertRaisesRegex(
                    LedgerValidationError, rf"{stage} requires outcome observed"
                ):
                    build_ledger(
                        session=session(),
                        launch_state="complete",
                        observations=[row],
                    )

        final = {
            "sequence": 1,
            "stage": "final_class_defined",
            "outcome": "observed",
            "phase": "UNKNOWN",
            "subject": {"target_class": "example.Target"},
            "evidence": evidence("foundation_final_bytes"),
            "final_bytecode_sha256": SHA_C,
        }
        tampered = deepcopy(
            build_ledger(
                session=session(),
                launch_state="complete",
                observations=[final],
            )
        )
        tampered["observations"][0]["outcome"] = "unknown"
        reidentify(tampered)
        with self.assertRaisesRegex(
            LedgerValidationError,
            "final_class_defined requires outcome observed",
        ):
            parse_ledger(tampered)

    def test_reidentified_invalid_stage_evidence_is_still_rejected(self) -> None:
        ledger = build_ledger(
            session=session(),
            launch_state="complete",
            observations=[apply_started()],
        )
        tampered = deepcopy(ledger)
        tampered["observations"][0]["evidence"]["kind"] = "config_lifecycle_probe"
        reidentify(tampered)
        with self.assertRaisesRegex(
            LedgerValidationError, "apply_started requires .* evidence"
        ):
            parse_ledger(tampered)

    def test_final_bytes_require_foundation_custody(self) -> None:
        final = {
            "sequence": 1,
            "stage": "final_class_defined",
            "outcome": "observed",
            "phase": "UNKNOWN",
            "subject": {"target_class": "example.Target"},
            "evidence": evidence("mixin_stage_export"),
            "final_bytecode_sha256": SHA_C,
        }
        with self.assertRaisesRegex(
            LedgerValidationError, "requires foundation_final_bytes"
        ):
            build_ledger(
                session=session(),
                launch_state="complete",
                observations=[final],
            )

        final["evidence"] = evidence("foundation_final_bytes")
        ledger = build_ledger(
            session=session(),
            launch_state="complete",
            observations=[final],
        )
        self.assertEqual(ledger["summary"]["final_class_defined_count"], 1)

    def test_lifecycle_cannot_move_backward_for_same_subject(self) -> None:
        later = {
            "sequence": 1,
            "stage": "plugin_post_apply_seen",
            "outcome": "observed",
            "phase": "DEFAULT",
            "subject": apply_started()["subject"],
            "evidence": evidence("plugin_callback"),
        }
        earlier = apply_started(sequence=2)
        with self.assertRaisesRegex(LedgerValidationError, "moves backward"):
            build_ledger(
                session=session(),
                launch_state="complete",
                observations=[later, earlier],
            )

    def test_crash_prefix_is_incomplete_not_a_failed_ledger(self) -> None:
        failure = {
            "sequence": 2,
            "stage": "failure",
            "outcome": "failed",
            "phase": "DEFAULT",
            "subject": apply_started()["subject"],
            "evidence": evidence("crash_report", SHA_C),
            "message": "applicator threw",
        }
        ledger = build_ledger(
            session=session(),
            launch_state="crashed",
            observations=[apply_started(), failure],
        )
        self.assertEqual(ledger["summary"]["evidence_state"], "incomplete")
        self.assertEqual(ledger["summary"]["failure_count"], 1)

    def test_identity_and_summary_tampering_are_rejected(self) -> None:
        ledger = build_ledger(
            session=session(),
            launch_state="complete",
            observations=[apply_started()],
        )
        tampered = deepcopy(ledger)
        tampered["summary"]["apply_started_count"] = 0
        with self.assertRaisesRegex(LedgerValidationError, "identity mismatch"):
            parse_ledger(tampered)

    def test_schema_accepts_constructed_ledger(self) -> None:
        try:
            from jsonschema import Draft202012Validator
        except ModuleNotFoundError:
            self.skipTest("jsonschema is not installed")
        schema_value = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema_value)
        ledger = build_ledger(
            session=session(),
            launch_state="complete",
            observations=[apply_started()],
        )
        Draft202012Validator(schema_value).validate(ledger)

    def test_cli_writes_valid_ledger(self) -> None:
        spec = {
            "format": OBSERVATION_SET_FORMAT,
            "session": session(),
            "launch_state": "complete",
            "observations": [apply_started()],
            "limitations": ["CLI fixture."],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.json"
            output_path = root / "ledger.json"
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
            ledger = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(parse_ledger(ledger), ledger)
            self.assertEqual(completed.stdout.strip(), ledger["ledger_id"])

    def test_cli_rejects_duplicate_json_keys_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.json"
            output_path = root / "ledger.json"
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
        ledger = build_ledger(
            session=session(),
            launch_state="complete",
            observations=[apply_started()],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "missing" / "captured.json"
            destination = root / "ledger.json"
            destination.symlink_to(outside)
            self.assertTrue(destination.is_symlink())
            self.assertFalse(destination.exists())
            with self.assertRaisesRegex(LedgerValidationError, "cannot be a symlink"):
                write_ledger(destination, ledger)
            self.assertTrue(destination.is_symlink())
            self.assertFalse(outside.exists())


if __name__ == "__main__":
    unittest.main()
