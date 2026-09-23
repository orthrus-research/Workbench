from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
CRUCIBLE_SOURCE = ROOT / "modules/crucible/src"
if str(CRUCIBLE_SOURCE) not in sys.path:
    sys.path.insert(0, str(CRUCIBLE_SOURCE))

from workbench_crucible_mixin_custody import (  # noqa: E402
    MIXIN_SERVICE_INTERFACE,
    PROVIDER_ENUMERATION_EVIDENCE_FORMAT,
    PROVIDER_ENUMERATION_EVIDENCE_PREFIX,
    PROVIDER_ENUMERATION_PROBE_ID,
    SERVICE_LOADER_ITERATOR_MECHANISM,
    ProviderEnumerationValidationError,
    build_provider_enumeration_evidence,
    canonical_provider_enumeration_json_bytes,
    parse_provider_enumeration_evidence,
    render_provider_enumeration_evidence,
)


SCHEMA_PATH = (
    ROOT
    / "modules/crucible/schemas"
    / "mixin-service-provider-enumeration-evidence-v1.schema.json"
)
SHA_A = "a" * 64
SHA_B = "b" * 64


def session() -> dict:
    return {
        "session_id": "session:provider-enumeration",
        "launch_id": "launch:provider-enumeration",
        "profile_id": "workbench-platform:test",
        "side": "dedicated_server",
        "candidate_toolchain_lock_sha256": "c" * 64,
        "component_topology_receipt_id": (
            "workbench-mixin-topology-receipt:sha256:" + "d" * 64
        ),
        "component_topology_receipt_sha256": "e" * 64,
    }


def probe() -> dict:
    return {
        "probe_id": PROVIDER_ENUMERATION_PROBE_ID,
        "mechanism": SERVICE_LOADER_ITERATOR_MECHANISM,
        "service_interface": MIXIN_SERVICE_INTERFACE,
        "class_loader_kind": "thread_context",
        "class_loader_class": "example.LaunchClassLoader",
    }


def providers() -> list[dict]:
    return [
        {
            "service_class": "example.SecondProvider",
            "service_name": "SharedName",
            "source_uri": "file:///runtime/second.jar",
            "provider_artifact_sha256": SHA_B,
            "provider_artifact_size_bytes": 200,
        },
        {
            "service_class": "example.FirstProvider",
            "service_name": "SharedName",
            "source_uri": "file:///runtime/first.jar",
            "provider_artifact_sha256": SHA_A,
            "provider_artifact_size_bytes": 100,
        },
    ]


def reidentify(value: dict) -> None:
    material = deepcopy(value)
    material.pop("evidence_id")
    value["evidence_id"] = PROVIDER_ENUMERATION_EVIDENCE_PREFIX + hashlib.sha256(
        canonical_provider_enumeration_json_bytes(material)
    ).hexdigest()


class MixinServiceProviderEnumerationTests(unittest.TestCase):
    def test_duplicate_names_remain_two_provider_identities(self) -> None:
        evidence = build_provider_enumeration_evidence(
            session=session(),
            probe=probe(),
            state="enumerated",
            providers=providers(),
            failure=None,
        )
        self.assertEqual(evidence["format"], PROVIDER_ENUMERATION_EVIDENCE_FORMAT)
        self.assertEqual(
            [row["service_class"] for row in evidence["providers"]],
            ["example.FirstProvider", "example.SecondProvider"],
        )
        self.assertEqual(evidence["summary"]["provider_count"], 2)
        self.assertEqual(evidence["summary"]["distinct_service_name_count"], 1)
        self.assertEqual(
            evidence["summary"]["duplicate_service_names"], ["SharedName"]
        )
        self.assertFalse(evidence["boundaries"]["runtime_selection_proved"])
        self.assertEqual(parse_provider_enumeration_evidence(evidence), evidence)

    def test_duplicate_provider_identity_is_rejected(self) -> None:
        duplicate = providers()[0]
        with self.assertRaisesRegex(
            ProviderEnumerationValidationError, "repeats provider identity"
        ):
            build_provider_enumeration_evidence(
                session=session(),
                probe=probe(),
                state="enumerated",
                providers=[duplicate, deepcopy(duplicate)],
                failure=None,
            )

    def test_failed_enumeration_discards_partial_set(self) -> None:
        failure = {
            "stage": "service_loader_iteration",
            "exception_class": "java.util.ServiceConfigurationError",
            "message": "provider could not be constructed",
        }
        evidence = build_provider_enumeration_evidence(
            session=session(),
            probe=probe(),
            state="failed",
            providers=[],
            failure=failure,
        )
        self.assertEqual(evidence["providers"], [])
        self.assertEqual(evidence["failure"], failure)
        self.assertEqual(evidence["summary"]["provider_count"], 0)
        with self.assertRaisesRegex(
            ProviderEnumerationValidationError, "cannot publish partial providers"
        ):
            build_provider_enumeration_evidence(
                session=session(),
                probe=probe(),
                state="failed",
                providers=providers()[:1],
                failure=failure,
            )

    def test_forged_summary_with_recomputed_outer_identity_is_rejected(self) -> None:
        evidence = build_provider_enumeration_evidence(
            session=session(),
            probe=probe(),
            state="enumerated",
            providers=providers(),
            failure=None,
        )
        forged = deepcopy(evidence)
        forged["summary"]["provider_count"] = 1
        reidentify(forged)
        with self.assertRaisesRegex(
            ProviderEnumerationValidationError, "not canonical V1 material"
        ):
            parse_provider_enumeration_evidence(forged)

    def test_forged_provider_without_recomputed_identity_is_rejected(self) -> None:
        evidence = build_provider_enumeration_evidence(
            session=session(),
            probe=probe(),
            state="enumerated",
            providers=providers(),
            failure=None,
        )
        evidence["providers"][0]["service_class"] = "forged.Provider"
        with self.assertRaisesRegex(
            ProviderEnumerationValidationError, "identity mismatch"
        ):
            parse_provider_enumeration_evidence(evidence)

    def test_mixin_code_source_cannot_replace_provider_source(self) -> None:
        row = providers()[0]
        row.pop("source_uri")
        with self.assertRaisesRegex(
            ProviderEnumerationValidationError, "lacks fields.*source_uri"
        ):
            build_provider_enumeration_evidence(
                session=session(),
                probe=probe(),
                state="enumerated",
                providers=[row],
                failure=None,
            )

    def test_renderer_binds_complete_canonical_bytes(self) -> None:
        evidence = build_provider_enumeration_evidence(
            session=session(),
            probe=probe(),
            state="enumerated",
            providers=providers(),
            failure=None,
        )
        rendered = render_provider_enumeration_evidence(evidence)
        self.assertTrue(rendered.endswith(b"\n"))
        self.assertEqual(json.loads(rendered), evidence)
        raw_sha = hashlib.sha256(rendered).hexdigest()
        self.assertEqual(len(raw_sha), 64)
        self.assertNotEqual(
            raw_sha,
            evidence["evidence_id"].removeprefix(
                PROVIDER_ENUMERATION_EVIDENCE_PREFIX
            ),
        )

    def test_schema_accepts_constructed_evidence(self) -> None:
        try:
            from jsonschema import Draft202012Validator
        except ModuleNotFoundError:
            self.skipTest("jsonschema is not installed")
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        evidence = build_provider_enumeration_evidence(
            session=session(),
            probe=probe(),
            state="enumerated",
            providers=providers(),
            failure=None,
        )
        Draft202012Validator(schema).validate(evidence)


if __name__ == "__main__":
    unittest.main()
