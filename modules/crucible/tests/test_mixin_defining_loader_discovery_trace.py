from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from jsonschema import Draft202012Validator, ValidationError


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
SCHEMA = (
    ROOT / "modules/crucible/schemas"
    / "mixin-defining-loader-discovery-trace-receipt-v2.schema.json"
)
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_mixins import (  # noqa: E402
    DefiningLoaderTraceValidationError,
    build_defining_loader_trace_receipt,
    parse_defining_loader_trace_receipt,
    parse_raw_discovery_trace,
    render_defining_loader_trace_receipt,
)
from workbench_crucible_mixins.discovery_trace import (  # noqa: E402
    AGENT_PAYLOAD_ENTRY,
    RAW_TRACE_FORMAT,
)


BOOTSTRAP = "example.CleanMixBootstrap"
SERVICE = "example.CleanMixService"
LOADER = "example.DefiningLoader"


def _jar(path: Path, entries: dict[str, bytes]) -> bytes:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return path.read_bytes()


def _provider_payload(provider: str, uri: str) -> dict:
    return {
        "provider_class": provider,
        "provider_loader_class": LOADER,
        "provider_loader_identity": LOADER + "@1",
        "provider_code_source_uri": uri,
    }


def _raw(engine: Path, selected: Path, original: bytes, patched: bytes) -> bytes:
    rows: list[dict] = []

    def add(event: str, stage, attempt, payload: dict) -> None:
        rows.append(
            {
                "format": RAW_TRACE_FORMAT,
                "capture_id": "capture:test",
                "sequence": len(rows),
                "event": event,
                "stage": stage,
                "attempt": attempt,
                "payload": payload,
            }
        )

    add("capture_start", None, None, {
        "agent_id": "agent:v2",
        "health": "starting",
        "java_version": "25.0.4",
        "redefine_supported": False,
        "retransform_supported": False,
    })
    add("transformer_installed", None, None, {
        "target_class": "org.spongepowered.asm.service.MixinService",
        "expected_input_sha256": hashlib.sha256(original).hexdigest(),
        "retransform_requested": False,
    })
    add("transform_applied", None, None, {
        "target_class": "org.spongepowered.asm.service.MixinService",
        "defining_loader_class": LOADER,
        "defining_loader_identity": LOADER + "@1",
        "defining_loader_parent_class": "example.Parent",
        "defining_loader_parent_identity": "example.Parent@2",
        "target_code_source_uri": "jar:" + engine.resolve().as_uri()
            + "!/org/spongepowered/asm/service/MixinService.class",
        "input_sha256": hashlib.sha256(original).hexdigest(),
        "output_sha256": hashlib.sha256(patched).hexdigest(),
        "reason": None,
    })
    add("stage_start", "bootstrap", None, {
        "defining_loader_class": LOADER,
        "defining_loader_identity": LOADER + "@1",
        "defining_loader_parent_class": "example.Parent",
        "defining_loader_parent_identity": "example.Parent@2",
        "bypass_property_name": "mixin.bootstrapService",
        "bypass_property_value": None,
        "mechanism": "service_loader",
    })
    add("attempt_start", "bootstrap", 1, {"mechanism": "service_loader"})
    bootstrap_payload = _provider_payload(
        BOOTSTRAP,
        "jar:" + selected.resolve().as_uri() + "!/example/CleanMixBootstrap.class",
    )
    add("provider_constructed", "bootstrap", 1, bootstrap_payload)
    add("bootstrap_returned", "bootstrap", 1, bootstrap_payload)
    add("bootstrap_service_class_name", "bootstrap", 1, {
        **bootstrap_payload,
        "service_class_name": SERVICE,
    })
    add("attempt_end", "bootstrap", 1, {"outcome": "completed"})
    add("stage_end", "bootstrap", None, {"outcome": "completed"})
    add("stage_start", "service", None, {
        "defining_loader_class": LOADER,
        "defining_loader_identity": LOADER + "@1",
        "defining_loader_parent_class": "example.Parent",
        "defining_loader_parent_identity": "example.Parent@2",
        "bypass_property_name": "mixin.service",
        "bypass_property_value": None,
        "mechanism": "service_loader",
    })
    add("attempt_start", "service", 2, {"mechanism": "service_loader"})
    service_payload = _provider_payload(
        SERVICE,
        "jar:" + selected.resolve().as_uri() + "!/example/CleanMixService.class",
    )
    add("provider_constructed", "service", 2, service_payload)
    add("service_name_returned", "service", 2, {
        **service_payload,
        "service_name": "CleanMix",
    })
    add("validity_returned", "service", 2, {**service_payload, "valid": True})
    add("provider_selected", "service", 2, service_payload)
    add("attempt_end", "service", 2, {"outcome": "selected"})
    add("stage_end", "service", None, {"outcome": "selected"})
    add("capture_end", None, None, {
        "health": "healthy",
        "transform_applied_count": 1,
        "bootstrap_started": True,
        "bootstrap_outcome": "completed",
        "service_started": True,
        "service_outcome": "selected",
        "selected_count": 1,
        "stage_failure": False,
        "write_failure": False,
    })
    return b"".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        for row in rows
    )


class DefiningLoaderDiscoveryTraceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.original = b"exact original MixinService"
        self.patched = b"exact observed MixinService"
        self.engine = self.root / "cleanmix.jar"
        self.selected = self.root / "cleanroom.jar"
        self.agent = self.root / "agent.jar"
        _jar(self.engine, {
            "org/spongepowered/asm/service/MixinService.class": self.original,
        })
        _jar(self.selected, {
            "example/CleanMixBootstrap.class": b"bootstrap",
            "example/CleanMixService.class": b"service",
            "META-INF/services/org.spongepowered.asm.service.IMixinServiceBootstrap":
                (BOOTSTRAP + "\n").encode(),
            "META-INF/services/org.spongepowered.asm.service.IMixinService":
                (SERVICE + "\n").encode(),
        })
        _jar(self.agent, {AGENT_PAYLOAD_ENTRY: self.patched})
        self.raw = self.root / "trace.ndjson"
        self.raw.write_bytes(
            _raw(self.engine, self.selected, self.original, self.patched)
        )
        self.candidate = self.root / "candidate.json"
        self.candidate.write_bytes(b"candidate lock\n")
        self.toolchain = self.root / "toolchain.json"
        self.toolchain.write_bytes(b"toolchain lock\n")
        self.launch = self.root / "launch.log"
        self.launch.write_bytes(b"BUILD SUCCESSFUL\n")
        self.fixture = self.root / "fixture.json"
        self.fixture.write_bytes(b"{}\n")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def build(self) -> dict:
        return build_defining_loader_trace_receipt(
            raw_trace_path=self.raw,
            candidate_lock_path=self.candidate,
            toolchain_lock_path=self.toolchain,
            observer_agent_path=self.agent,
            mixin_engine_artifact_path=self.engine,
            selected_provider_artifact_path=self.selected,
            launch_log_path=self.launch,
            fixture_result_path=self.fixture,
            launch_id="launch:test",
            profile_id="profile:test",
            side="dedicated_server",
        )

    def test_builds_ordered_defining_loader_receipt(self) -> None:
        receipt = self.build()
        parsed = parse_defining_loader_trace_receipt(receipt)
        self.assertEqual(parsed["health"]["end_health"], "healthy")
        self.assertEqual(
            [(row["stage"], row["attempt_id"]) for row in parsed["ordered_attempts"]],
            [("bootstrap", 1), ("service", 2)],
        )
        self.assertEqual(parsed["selection"]["provider_class"], SERVICE)
        self.assertTrue(parsed["boundaries"]["cleanmix_defining_loader_path_observed"])
        self.assertFalse(parsed["boundaries"]["thread_context_enumeration_performed"])
        rendered = render_defining_loader_trace_receipt(parsed)
        self.assertEqual(json.loads(rendered), parsed)

    def test_closed_receipt_schema_accepts_v2_and_rejects_nested_extras(self) -> None:
        schema = json.loads(SCHEMA.read_bytes())
        Draft202012Validator.check_schema(schema)
        pending = [schema]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertIs(node.get("additionalProperties"), False)
                pending.extend(node.values())
            elif isinstance(node, list):
                pending.extend(node)
        validator = Draft202012Validator(schema)
        receipt = self.build()
        validator.validate(receipt)
        receipt["selection"]["undeclared"] = True
        with self.assertRaises(ValidationError):
            validator.validate(receipt)

    def test_raw_sequence_tamper_is_rejected(self) -> None:
        rows = self.raw.read_text().splitlines()
        row = json.loads(rows[4])
        row["sequence"] = 99
        rows[4] = json.dumps(row, separators=(",", ":"), sort_keys=True)
        with self.assertRaisesRegex(DefiningLoaderTraceValidationError, "not contiguous"):
            parse_raw_discovery_trace(("\n".join(rows) + "\n").encode())

    def test_forged_healthy_footer_is_rejected(self) -> None:
        rows = self.raw.read_text().splitlines()
        row = json.loads(rows[-1])
        row["payload"]["selected_count"] = 2
        rows[-1] = json.dumps(row, separators=(",", ":"), sort_keys=True)
        with self.assertRaisesRegex(DefiningLoaderTraceValidationError, "selection count"):
            parse_raw_discovery_trace(("\n".join(rows) + "\n").encode())

    def test_selected_source_must_name_measured_artifact(self) -> None:
        other = self.root / "other.jar"
        other.write_bytes(self.selected.read_bytes())
        with self.assertRaisesRegex(
            DefiningLoaderTraceValidationError,
            "does not name the measured artifact",
        ):
            build_defining_loader_trace_receipt(
                raw_trace_path=self.raw,
                candidate_lock_path=self.candidate,
                toolchain_lock_path=self.toolchain,
                observer_agent_path=self.agent,
                mixin_engine_artifact_path=self.engine,
                selected_provider_artifact_path=other,
                launch_log_path=self.launch,
                fixture_result_path=self.fixture,
                launch_id="launch:test",
                profile_id="profile:test",
                side="dedicated_server",
            )

    def test_receipt_identity_tamper_is_rejected(self) -> None:
        receipt = deepcopy(self.build())
        receipt["selection"]["provider_class"] = "forged.Service"
        with self.assertRaisesRegex(
            DefiningLoaderTraceValidationError,
            "selection projection disagrees",
        ):
            parse_defining_loader_trace_receipt(receipt)


if __name__ == "__main__":
    unittest.main()
