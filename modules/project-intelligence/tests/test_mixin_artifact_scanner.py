#!/usr/bin/env python3

from __future__ import annotations

from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
import warnings
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/project-intelligence/src"
sys.path.insert(0, str(SOURCE))

from workbench_project_intelligence import (  # noqa: E402
    ArtifactInput,
    ArtifactScanError,
    build_topology_receipt,
    canonical_json_bytes,
    scan_artifact_bytes,
    scan_artifact_paths,
    validate_topology_receipt,
)


SCHEMA = json.loads(
    (
        ROOT
        / "modules/project-intelligence/schemas/"
        "mixin-component-topology-receipt-v1.schema.json"
    ).read_text(encoding="utf-8")
)
VALIDATOR = Draft202012Validator(SCHEMA)


def json_bytes(value: object) -> bytes:
    return canonical_json_bytes(value)


def archive_bytes(entries: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        for name, payload in sorted(entries.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_STORED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload)
    return output.getvalue()


def class_with_interface(class_name: str, interface_name: str) -> bytes:
    def utf8(value: str) -> bytes:
        encoded = value.encode("ascii")
        return b"\x01" + struct.pack(">H", len(encoded)) + encoded

    def class_ref(index: int) -> bytes:
        return b"\x07" + struct.pack(">H", index)

    constant_pool = b"".join(
        [
            utf8(class_name),
            class_ref(1),
            utf8("java/lang/Object"),
            class_ref(3),
            utf8(interface_name),
            class_ref(5),
        ]
    )
    return b"".join(
        [
            b"\xca\xfe\xba\xbe",
            struct.pack(">HHH", 0, 52, 7),
            constant_pool,
            struct.pack(">HHHH", 0x0021, 2, 4, 1),
            struct.pack(">H", 6),
            struct.pack(">HHH", 0, 0, 0),
        ]
    )


def fixture_archive(*, owner: str = "Example-Mod") -> bytes:
    manifest = (
        b"Manifest-Version: 1.0\r\n"
        b"Implementation-Version: 1.2.3\r\n"
        b"FMLCorePlugin: example.\r\n"
        b" Loader\r\n"
        b"MixinConfigs: mixins.example.\r\n"
        b" config.json\r\n"
        b"\r\n"
    )
    config = {
        "compatibilityLevel": "JAVA_8",
        "injectors": {"defaultRequire": 1},
        "minVersion": "0.8",
        "mixins": ["Probe"],
        "client": ["ClientProbe"],
        "package": "example.mixin",
        "plugin": "example.Plugin",
        "refmap": "mixins.example.refmap.json",
        "required": True,
        "requiredFeatures": ["example:feature"],
        "target": "@env(PREINIT)",
    }
    return archive_bytes(
        {
            "META-INF/MANIFEST.MF": manifest,
            "cleanmix_version_compatibility.json": json_bytes(
                {
                    "example.mixin.ClientProbe": "0.6.0",
                    "example.mixin.Probe": "0.6.0",
                }
            ),
            "com/google/common/Thing.class": b"guava",
            "com/llamalad7/mixinextras/Thing.class": b"mixinextras",
            "example/Loader.class": class_with_interface(
                "example/Loader",
                "zone/rong/mixinbooter/IEarlyMixinLoader",
            ),
            "example/Plugin.class": class_with_interface(
                "example/Plugin",
                "org/spongepowered/asm/mixin/extensibility/IMixinConfigPlugin",
            ),
            "example/mixin/ClientProbe.class": b"client-mixin-class",
            "example/mixin/Probe.class": b"common-mixin-class",
            "mcmod.info": json_bytes([{"modid": owner}]),
            "mixins.example.config.json": json_bytes(config),
            "mixins.example.refmap.json": json_bytes(
                {
                    "data": {"searge": {}},
                    "mappings": {"example/mixin/Probe": {"a()V": "b()V"}},
                }
            ),
            "org/objectweb/asm/Thing.class": b"asm",
            "org/spongepowered/asm/Thing.class": b"mixin",
        }
    )


def assert_schema(test: unittest.TestCase, receipt: dict) -> None:
    errors = sorted(VALIDATOR.iter_errors(receipt), key=lambda error: list(error.path))
    test.assertEqual([], [(list(error.path), error.message) for error in errors])


def recompute_id(record: dict, id_field: str, prefix: str) -> str:
    material = {key: value for key, value in record.items() if key != id_field}
    return prefix + hashlib.sha256(canonical_json_bytes(material)).hexdigest()


class MixinArtifactScannerTest(unittest.TestCase):
    def test_static_topology_receipt_is_schema_valid_and_exact(self) -> None:
        data = fixture_archive()
        scan = scan_artifact_bytes(data, label="example-1.2.3.jar")

        attributes = {
            row["name"]: row["value"]
            for row in scan["manifest"]["main_attributes"]
        }
        self.assertEqual("example.Loader", attributes["FMLCorePlugin"])
        self.assertEqual(
            "mixins.example.config.json", attributes["MixinConfigs"]
        )
        self.assertEqual(
            ["manifest-mixin-configs", "forge-core-plugin", "mixinbooter-early-loader"],
            [
                kind
                for kind in (
                    "manifest-mixin-configs",
                    "forge-core-plugin",
                    "mixinbooter-early-loader",
                )
                if kind in {route["kind"] for route in scan["registration_routes"]}
            ],
        )
        self.assertEqual(2, len(scan["mixin_configs"][0]["mixins"]))
        self.assertEqual(
            "examplemod", scan["owner_id_inputs"][0]["normalized_owner_id"]
        )
        self.assertTrue(scan["compatibility_metadata"]["present"])
        self.assertTrue(all(row["present"] for row in scan["embedded_packages"]))

        receipt = build_topology_receipt(
            [ArtifactInput(label="example-1.2.3.jar", data=data)]
        )
        assert_schema(self, receipt)
        self.assertEqual(
            "workbench-project-intelligence-mixin-component-topology-receipt-v1",
            receipt["format"],
        )
        self.assertEqual("offline-artifact-set", receipt["scope"]["scope_kind"])
        self.assertEqual("OFFLINE", receipt["scope"]["physical_side"])
        self.assertEqual("not-observed", receipt["summary"]["runtime_observation_state"])
        self.assertEqual("complete", receipt["summary"]["compatibility_state"])
        self.assertEqual(1, receipt["summary"]["configuration_count"])
        self.assertEqual(
            ["example:feature"], receipt["configurations"][0]["required_features"]
        )
        self.assertNotIn(
            "mixin-compatibility-level:JAVA_8",
            receipt["configurations"][0]["required_features"],
        )
        self.assertEqual(2, len([row for row in receipt["components"] if row["component_kind"] == "mixin-class"]))
        self.assertIn(
            "programmatic-config",
            {row["registration_kind"] for row in receipt["registrations"]},
        )

    def test_all_contract_records_are_content_addressed_and_sorted(self) -> None:
        receipt = build_topology_receipt(
            [ArtifactInput("example.jar", fixture_archive())]
        )
        arrays = {
            "artifacts": ("artifact_id", "workbench-mixin-artifact:sha256:"),
            "components": ("component_id", "workbench-mixin-component:sha256:"),
            "configurations": (
                "configuration_id",
                "workbench-mixin-configuration:sha256:",
            ),
            "registrations": (
                "registration_id",
                "workbench-mixin-registration:sha256:",
            ),
            "mixin_bindings": ("binding_id", "workbench-mixin-binding:sha256:"),
            "compatibility_epochs": (
                "compatibility_id",
                "workbench-mixin-compatibility:sha256:",
            ),
            "evidence": ("evidence_id", "workbench-mixin-evidence:sha256:"),
            "findings": ("finding_id", "workbench-mixin-finding:sha256:"),
        }
        for array_name, (id_field, prefix) in arrays.items():
            identities = [row[id_field] for row in receipt[array_name]]
            self.assertEqual(sorted(identities), identities, array_name)
            for row in receipt[array_name]:
                self.assertEqual(
                    recompute_id(row, id_field, prefix),
                    row[id_field],
                    f"{array_name} {row[id_field]}",
                )
        self.assertEqual(
            recompute_id(
                receipt["scope"],
                "scope_id",
                "workbench-mixin-scope:sha256:",
            ),
            receipt["scope"]["scope_id"],
        )
        self.assertEqual(
            recompute_id(
                receipt,
                "receipt_id",
                "workbench-mixin-topology-receipt:sha256:",
            ),
            receipt["receipt_id"],
        )
        validate_topology_receipt(receipt)

    def test_semantic_validator_rejects_bad_counts_and_references(self) -> None:
        receipt = build_topology_receipt(
            [ArtifactInput("example.jar", fixture_archive())]
        )

        wrong_count = deepcopy(receipt)
        wrong_count["summary"]["component_count"] += 1
        wrong_count["receipt_id"] = recompute_id(
            wrong_count,
            "receipt_id",
            "workbench-mixin-topology-receipt:sha256:",
        )
        with self.assertRaisesRegex(ArtifactScanError, "component_count"):
            validate_topology_receipt(wrong_count)

        dangling = deepcopy(receipt)
        registration = next(
            row
            for row in dangling["registrations"]
            if row["registration_kind"] == "config-mixin"
        )
        registration["target"]["id"] = (
            "workbench-mixin-component:sha256:" + "0" * 64
        )
        registration["registration_id"] = recompute_id(
            registration,
            "registration_id",
            "workbench-mixin-registration:sha256:",
        )
        dangling["registrations"].sort(key=lambda row: row["registration_id"])
        dangling["receipt_id"] = recompute_id(
            dangling,
            "receipt_id",
            "workbench-mixin-topology-receipt:sha256:",
        )
        with self.assertRaisesRegex(ArtifactScanError, "unknown IDs"):
            validate_topology_receipt(dangling)

    def test_semantic_validator_rejects_reidentified_truth_tampering(self) -> None:
        receipt = build_topology_receipt(
            [ArtifactInput("example.jar", fixture_archive())]
        )

        wrong_scope = deepcopy(receipt)
        wrong_scope["scope"]["artifact_set_sha256"] = "0" * 64
        wrong_scope["scope"]["scope_id"] = recompute_id(
            wrong_scope["scope"],
            "scope_id",
            "workbench-mixin-scope:sha256:",
        )
        wrong_scope["receipt_id"] = recompute_id(
            wrong_scope,
            "receipt_id",
            "workbench-mixin-topology-receipt:sha256:",
        )
        with self.assertRaisesRegex(ArtifactScanError, "artifact-set"):
            validate_topology_receipt(wrong_scope)

        wrong_producer = deepcopy(receipt)
        wrong_producer["producer"]["tool_version"] = "forged"
        wrong_producer["receipt_id"] = recompute_id(
            wrong_producer,
            "receipt_id",
            "workbench-mixin-topology-receipt:sha256:",
        )
        with self.assertRaisesRegex(ArtifactScanError, "tool_version"):
            validate_topology_receipt(wrong_producer)

        runtime_claim = deepcopy(receipt)
        registration = next(
            row
            for row in runtime_claim["registrations"]
            if row["registration_kind"] == "programmatic-config"
        )
        registration["runtime_state"] = "active"
        registration["registration_id"] = recompute_id(
            registration,
            "registration_id",
            "workbench-mixin-registration:sha256:",
        )
        runtime_claim["registrations"].sort(
            key=lambda row: row["registration_id"]
        )
        runtime_claim["receipt_id"] = recompute_id(
            runtime_claim,
            "receipt_id",
            "workbench-mixin-topology-receipt:sha256:",
        )
        with self.assertRaisesRegex(ArtifactScanError, "claims runtime state"):
            validate_topology_receipt(runtime_claim)

        unknown_endpoint = deepcopy(receipt)
        registration = next(
            row
            for row in unknown_endpoint["registrations"]
            if row["registration_kind"] == "programmatic-config"
        )
        registration["target"]["kind"] = "invented-endpoint"
        registration["registration_id"] = recompute_id(
            registration,
            "registration_id",
            "workbench-mixin-registration:sha256:",
        )
        unknown_endpoint["registrations"].sort(
            key=lambda row: row["registration_id"]
        )
        unknown_endpoint["receipt_id"] = recompute_id(
            unknown_endpoint,
            "receipt_id",
            "workbench-mixin-topology-receipt:sha256:",
        )
        with self.assertRaisesRegex(ArtifactScanError, "invalid endpoint"):
            validate_topology_receipt(unknown_endpoint)

    def test_exact_finding_cannot_rely_only_on_missing_evidence(self) -> None:
        data = archive_bytes(
            {
                "mixins.example.json": json_bytes(
                    {"mixins": ["Probe"], "package": "example.mixin"}
                ),
                "example/mixin/Probe.class": b"mixin",
            }
        )
        receipt = build_topology_receipt([ArtifactInput("missing-metadata.jar", data)])
        forged = deepcopy(receipt)
        finding = next(
            row
            for row in forged["findings"]
            if row["finding_kind"] == "compatibility"
        )
        finding["evidence_ids"] = [
            identity
            for identity in finding["evidence_ids"]
            if next(
                evidence
                for evidence in forged["evidence"]
                if evidence["evidence_id"] == identity
            )["collection_state"]
            == "missing"
        ]
        finding["finding_id"] = recompute_id(
            finding,
            "finding_id",
            "workbench-mixin-finding:sha256:",
        )
        forged["findings"].sort(key=lambda row: row["finding_id"])
        forged["receipt_id"] = recompute_id(
            forged,
            "receipt_id",
            "workbench-mixin-topology-receipt:sha256:",
        )
        with self.assertRaisesRegex(ArtifactScanError, "no admitted evidence"):
            validate_topology_receipt(forged)

    def test_multi_artifact_results_are_order_independent_and_find_collisions(self) -> None:
        first = archive_bytes(
            {
                "mcmod.info": json_bytes([{"modid": "a-b"}]),
                "org/objectweb/asm/First.class": b"first",
            }
        )
        second = archive_bytes(
            {
                "mcmod.info": json_bytes([{"modid": "a_b"}]),
                "org/objectweb/asm/Second.class": b"second",
            }
        )
        forward = build_topology_receipt(
            [ArtifactInput("first.jar", first), ArtifactInput("second.jar", second)]
        )
        reverse = build_topology_receipt(
            [ArtifactInput("second.jar", second), ArtifactInput("first.jar", first)]
        )
        self.assertEqual(forward, reverse)
        assert_schema(self, forward)
        statements = [row["statement"] for row in forward["findings"]]
        self.assertTrue(any("normalize static owner-ID inputs to 'ab'" in row for row in statements))
        self.assertTrue(any("Multiple artifacts embed the asm package" in row for row in statements))

    def test_invalid_compatibility_entry_is_retained_as_a_contradiction(self) -> None:
        data = archive_bytes(
            {
                "META-INF/MANIFEST.MF": (
                    b"Manifest-Version: 1.0\r\n"
                    b"MixinConfigs: mixins.example.json\r\n\r\n"
                ),
                "cleanmix_version_compatibility.json": json_bytes(
                    {"example.mixin.Probe": "0.1000.0"}
                ),
                "example/mixin/Probe.class": b"mixin",
                "mixins.example.json": json_bytes(
                    {"mixins": ["Probe"], "package": "example.mixin"}
                ),
            }
        )
        scan = scan_artifact_bytes(data, label="invalid-compatibility.jar")
        self.assertEqual({}, scan["compatibility_metadata"]["entries"])
        self.assertEqual(
            "0.1000.0",
            scan["compatibility_metadata"]["invalid_entries"][0]["value"],
        )
        receipt = build_topology_receipt(
            [ArtifactInput("invalid-compatibility.jar", data)]
        )
        assert_schema(self, receipt)
        self.assertEqual(1, receipt["summary"]["contradiction_count"])
        self.assertEqual("partial", receipt["summary"]["compatibility_state"])

    def test_multiple_invalid_compatibility_entries_count_one_retained_finding(self) -> None:
        data = archive_bytes(
            {
                "cleanmix_version_compatibility.json": json_bytes(
                    {
                        "example.mixin.First": "0.1000.0",
                        "example.mixin.Second": "not-a-version",
                    }
                )
            }
        )
        receipt = build_topology_receipt(
            [ArtifactInput("invalid-compatibility.jar", data)]
        )
        assert_schema(self, receipt)
        validate_topology_receipt(receipt)
        self.assertEqual(1, receipt["summary"]["unresolved_count"])
        self.assertEqual(1, receipt["summary"]["contradiction_count"])
        self.assertEqual("incomplete", receipt["summary"]["receipt_state"])

    def test_runtime_package_resolution_is_exact_and_missing_package_is_orphaned(self) -> None:
        already_qualified = archive_bytes(
            {
                "mixins.qualified.json": json_bytes(
                    {
                        "mixins": ["example.mixin.Probe"],
                        "package": "example.mixin",
                    }
                ),
                "example/mixin/Probe.class": b"not-the-runtime-name",
            }
        )
        scan = scan_artifact_bytes(already_qualified, label="qualified.jar")
        mixin = scan["mixin_configs"][0]["mixins"][0]
        self.assertEqual(
            "example.mixin.example.mixin.Probe", mixin["class_name"]
        )
        self.assertFalse(mixin["present"])

        missing_package = archive_bytes(
            {
                "mixins.orphaned.json": json_bytes(
                    {"mixins": ["example.mixin.Probe"]}
                ),
                "example/mixin/Probe.class": b"present-but-orphaned",
            }
        )
        receipt = build_topology_receipt(
            [ArtifactInput("orphaned.jar", missing_package)]
        )
        assert_schema(self, receipt)
        self.assertTrue(
            any(
                "without a package" in finding["statement"]
                for finding in receipt["findings"]
            )
        )

    def test_malformed_archives_and_metadata_fail_closed(self) -> None:
        cases = {
            "not-zip": b"this is not a zip",
            "manifest-continuation": archive_bytes(
                {"META-INF/MANIFEST.MF": b" orphaned\r\n"}
            ),
            "manifest-duplicate-config": archive_bytes(
                {
                    "META-INF/MANIFEST.MF": (
                        b"Manifest-Version: 1.0\r\n"
                        b"MixinConfigs: mixins.a.json, mixins.a.json\r\n\r\n"
                    ),
                    "mixins.a.json": json_bytes({"mixins": []}),
                }
            ),
            "mixin-config-json": archive_bytes(
                {"mixins.broken.json": b"{"}
            ),
            "compatibility-json": archive_bytes(
                {"cleanmix_version_compatibility.json": b"[]"}
            ),
            "mcmod-json": archive_bytes({"mcmod.info": b"not json"}),
            "mcmod-empty-normalized-owner": archive_bytes(
                {"mcmod.info": json_bytes([{"modid": "___"}])}
            ),
            "refmap-json": archive_bytes(
                {
                    "mixins.valid.json": json_bytes(
                        {"mixins": [], "refmap": "mixins.valid.refmap.json"}
                    ),
                    "mixins.valid.refmap.json": b"not json",
                }
            ),
        }
        for name, data in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ArtifactScanError):
                    scan_artifact_bytes(data, label=f"{name}.jar")

        duplicate = BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with ZipFile(duplicate, "w") as archive:
                archive.writestr("same", b"one")
                archive.writestr("same", b"two")
        with self.assertRaisesRegex(ArtifactScanError, "duplicate member"):
            scan_artifact_bytes(duplicate.getvalue(), label="duplicate.jar")

    def test_cli_matches_library_and_rejects_partial_output(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "example.jar"
            artifact.write_bytes(fixture_archive())
            direct = scan_artifact_paths([artifact])
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools/inspect_mixin_artifacts.py"),
                    "--compact",
                    str(artifact),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(direct, json.loads(completed.stdout))

            invalid = root / "invalid.jar"
            invalid.write_bytes(b"invalid")
            output = root / "receipt.json"
            failed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools/inspect_mixin_artifacts.py"),
                    "--output",
                    str(output),
                    str(invalid),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(0, failed.returncode)
            self.assertFalse(output.exists())

    def test_retained_real_fixture_when_available(self) -> None:
        artifact = (
            ROOT
            / ".workbench/evidence/worldgen-observatory/exact-runtime-v2/"
            "aa-1/server/mods/worldgen-observatory-fixture-0.1.0.jar"
        )
        if not artifact.is_file():
            self.skipTest("ignored exact-runtime fixture is not retained")
        receipt = scan_artifact_paths([artifact])
        assert_schema(self, receipt)
        mixins = [
            row
            for row in receipt["components"]
            if row["component_kind"] == "mixin-class"
        ]
        self.assertEqual(6, len(mixins))
        self.assertTrue(all(row["identity_state"] == "exact-entry" for row in mixins))
        self.assertEqual(1, receipt["summary"]["configuration_count"])
        self.assertIn(
            "programmatic-config",
            {row["registration_kind"] for row in receipt["registrations"]},
        )
        self.assertTrue(
            any(
                "does not contain cleanmix_version_compatibility.json"
                in row["statement"]
                for row in receipt["findings"]
            )
        )


if __name__ == "__main__":
    unittest.main()
