from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from workbench_crucible_mixins import (
    DirectApplicationValidationError,
    build_direct_application_receipt,
    canonical_direct_application_json_bytes,
    parse_direct_application_receipt,
    parse_raw_direct_application,
    write_direct_application_receipt,
)


TARGET = "example.Target"
MIXIN = "example.TargetMixin"
APPLICATOR = "org.spongepowered.asm.mixin.transformer.MixinApplicatorStandard"
INPUT_SHA = "b0bd92ba11df86ec1eb3027a665729877e87fe3ca4c3574995d18b0fd210cc46"
OUTPUT_SHA = "7" * 64
ARTIFACT_SHA = "8" * 64
URI = "jar:file:/ignored/cleanmix.jar!/org/spongepowered/asm/mixin/transformer/MixinApplicatorStandard.class"


def events() -> list[dict[str, object]]:
    payloads = [
        ("capture_start", {
            "agent_id": "workbench-cleanmix-direct-application-agent-v1",
            "java_version": "25.0.4",
            "targets": [TARGET],
            "redefine_supported": False,
            "retransform_supported": False,
        }),
        ("transformer_installed", {
            "target_class": APPLICATOR.replace(".", "/"),
            "expected_input_sha256": INPUT_SHA,
            "retransform_requested": False,
        }),
        ("transform_applied", {
            "target_class": APPLICATOR,
            "defining_loader_class": "net.minecraft.launchwrapper.LaunchClassLoader",
            "defining_loader_identity": "LaunchClassLoader@1",
            "target_code_source_uri": URI,
            "input_sha256": INPUT_SHA,
            "output_sha256": OUTPUT_SHA,
            "reason": None,
        }),
        ("application_started", {
            "application_id": 1,
            "target_class": TARGET,
            "mixins": [MIXIN],
            "thread_name": "Server thread",
        }),
        ("application_completed", {
            "application_id": 1,
            "target_class": TARGET,
            "mixins": [MIXIN],
            "thread_name": "Server thread",
        }),
        ("capture_end", {
            "health": "healthy",
            "requested_targets": [TARGET],
            "started_counts": [{"target_class": TARGET, "count": 1}],
            "completed_counts": [{"target_class": TARGET, "count": 1}],
            "missing_targets": [],
            "non_singleton_targets": [],
            "application_failure_count": 0,
            "observer_failure_count": 0,
            "write_failure": False,
        }),
    ]
    return [
        {
            "format": "workbench-cleanmix-direct-application-raw-v1",
            "capture_id": "capture-1",
            "sequence": index,
            "event": event,
            "payload": payload,
        }
        for index, (event, payload) in enumerate(payloads)
    ]


def encoded() -> bytes:
    return b"".join(
        json.dumps(row, separators=(",", ":")).encode() + b"\n"
        for row in events()
    )


class DirectApplicationTests(unittest.TestCase):
    def test_healthy_exact_stream_is_admitted(self) -> None:
        admitted = parse_raw_direct_application(encoded())
        self.assertEqual("application_completed", admitted[-2]["event"])
        self.assertEqual("healthy", admitted[-1]["payload"]["health"])

    def test_stale_footer_and_byte_guard_fail_closed(self) -> None:
        stale = events()
        stale[-1]["payload"]["completed_counts"][0]["count"] = 0
        with self.assertRaisesRegex(
            DirectApplicationValidationError, "completed counts are stale"
        ):
            parse_raw_direct_application(
                b"".join(
                    json.dumps(row, separators=(",", ":")).encode() + b"\n"
                    for row in stale
                )
            )
        drift = events()
        drift[2]["payload"]["input_sha256"] = "9" * 64
        with self.assertRaisesRegex(
            DirectApplicationValidationError, "exact byte guard"
        ):
            parse_raw_direct_application(
                b"".join(
                    json.dumps(row, separators=(",", ":")).encode() + b"\n"
                    for row in drift
                )
            )

    def test_transformer_install_and_transform_precede_application(self) -> None:
        reordered = events()
        install = reordered.pop(1)
        reordered.insert(-1, install)
        for sequence, event in enumerate(reordered):
            event["sequence"] = sequence
        with self.assertRaisesRegex(
            DirectApplicationValidationError,
            "install/transform/application order is invalid",
        ):
            parse_raw_direct_application(
                b"".join(
                    json.dumps(row, separators=(",", ":")).encode() + b"\n"
                    for row in reordered
                )
            )

    def test_receipt_is_content_addressed_and_written_create_new(self) -> None:
        rows = parse_raw_direct_application(encoded())
        record = {"label": "input", "sha256": "a" * 64, "size_bytes": 1}
        receipt = build_direct_application_receipt(
            launch_id="launch-1",
            profile_id="profile-1",
            side="dedicated_server",
            inputs={name: record for name in (
                "agent_artifact", "fixture_result", "launch_log", "raw_trace"
            )},
            target_artifact={
                "label": "CleanMix",
                "sha256": ARTIFACT_SHA,
                "size_bytes": 10,
                "code_source_uri": URI,
            },
            raw_events=rows,
        )
        self.assertEqual(receipt, parse_direct_application_receipt(receipt))
        material = deepcopy(receipt)
        supplied = material.pop("receipt_id")
        self.assertEqual(
            "crucible-mixin-direct-application:sha256:"
            + hashlib.sha256(
                canonical_direct_application_json_bytes(material)
            ).hexdigest(),
            supplied,
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary).resolve() / "receipt.json"
            write_direct_application_receipt(output, receipt)
            self.assertEqual(receipt, json.loads(output.read_bytes()))
            with self.assertRaisesRegex(
                DirectApplicationValidationError, "must be absent"
            ):
                write_direct_application_receipt(output, receipt)

    def test_rehashed_malformed_receipt_is_rejected(self) -> None:
        rows = parse_raw_direct_application(encoded())
        record = {"label": "input", "sha256": "a" * 64, "size_bytes": 1}
        receipt = build_direct_application_receipt(
            launch_id="launch-1",
            profile_id="profile-1",
            side="dedicated_server",
            inputs={name: record for name in (
                "agent_artifact", "fixture_result", "launch_log", "raw_trace"
            )},
            target_artifact={
                "label": "CleanMix",
                "sha256": ARTIFACT_SHA,
                "size_bytes": 10,
                "code_source_uri": URI,
            },
            raw_events=rows,
        )
        malformed = deepcopy(receipt)
        malformed["observer"] = {}
        material = deepcopy(malformed)
        material.pop("receipt_id")
        malformed["receipt_id"] = (
            "crucible-mixin-direct-application:sha256:"
            + hashlib.sha256(canonical_direct_application_json_bytes(material)).hexdigest()
        )
        with self.assertRaisesRegex(
            DirectApplicationValidationError, "observer lacks fields"
        ):
            parse_direct_application_receipt(malformed)


if __name__ == "__main__":
    unittest.main()
