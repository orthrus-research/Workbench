from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/project-intelligence/src"
sys.path.insert(0, str(SOURCE))

from workbench_project_intelligence import (  # noqa: E402
    ArtifactInput,
    ArtifactScanError,
    build_ap_compatibility_conformance_receipt,
    canonical_json_bytes,
    validate_ap_compatibility_conformance_receipt,
    validate_bound_ap_compatibility_conformance_receipt,
)


POLICY_PATH = (
    ROOT
    / "profiles/platforms/cleanroom/mixins/"
    "cleanmix-ap-compatibility-policy-v1.json"
)
POLICY_BYTES = POLICY_PATH.read_bytes()
SCHEMA = json.loads(
    (
        ROOT
        / "modules/project-intelligence/schemas/"
        "mixin-ap-compatibility-conformance-receipt-v1.schema.json"
    ).read_text(encoding="utf-8")
)
VALIDATOR = Draft202012Validator(SCHEMA)
TOOL = ROOT / "tools/inspect_mixin_ap_compatibility.py"
MIXIN = "Lorg/spongepowered/asm/mixin/Mixin;"
COMPATIBILITY = "Lorg/spongepowered/asm/mixin/Compatibility;"


def _archive(entries: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        for name, payload in sorted(entries.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_STORED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload)
    return output.getvalue()


class _ClassBuilder:
    def __init__(self, class_name: str):
        self.entries: list[bytes] = []
        self.class_name = class_name

    def utf8(self, value: str) -> int:
        raw = value.encode("utf-8")
        self.entries.append(b"\x01" + struct.pack(">H", len(raw)) + raw)
        return len(self.entries)

    def class_ref(self, value: str) -> int:
        name_index = self.utf8(value)
        self.entries.append(b"\x07" + struct.pack(">H", name_index))
        return len(self.entries)

    def annotation_attribute(
        self, annotations: list[tuple[str, str | None]], *, visible: bool = False
    ) -> bytes:
        payload: list[bytes] = [struct.pack(">H", len(annotations))]
        for descriptor, version in annotations:
            payload.append(struct.pack(">H", self.utf8(descriptor)))
            if version is None:
                payload.append(struct.pack(">H", 0))
            else:
                payload.append(struct.pack(">H", 1))
                payload.append(struct.pack(">H", self.utf8("value")))
                payload.append(b"s" + struct.pack(">H", self.utf8(version)))
        raw = b"".join(payload)
        attribute = (
            "RuntimeVisibleAnnotations" if visible else "RuntimeInvisibleAnnotations"
        )
        return struct.pack(">HI", self.utf8(attribute), len(raw)) + raw

    def member(
        self,
        name: str,
        descriptor: str,
        compatibility: str | None = None,
        *,
        kind: str,
    ) -> bytes:
        attributes = (
            []
            if compatibility is None
            else [self.annotation_attribute([(COMPATIBILITY, compatibility)])]
        )
        access = 0x0001 if kind == "field" else 0x0401
        return b"".join(
            [
                struct.pack(">HHHH", access, self.utf8(name), self.utf8(descriptor), len(attributes)),
                *attributes,
            ]
        )

    def build(
        self,
        *,
        mixin: bool = True,
        class_compatibility: str | None = None,
        fields: tuple[tuple[str, str, str | None], ...] = (),
        methods: tuple[tuple[str, str, str | None], ...] = (),
    ) -> bytes:
        this_index = self.class_ref(self.class_name)
        super_index = self.class_ref("java/lang/Object")
        field_rows = [
            self.member(name, descriptor, version, kind="field")
            for name, descriptor, version in fields
        ]
        method_rows = [
            self.member(name, descriptor, version, kind="method")
            for name, descriptor, version in methods
        ]
        annotations: list[tuple[str, str | None]] = []
        if mixin:
            annotations.append((MIXIN, None))
        if class_compatibility is not None:
            annotations.append((COMPATIBILITY, class_compatibility))
        class_attributes = (
            [] if not annotations else [self.annotation_attribute(annotations)]
        )
        return b"".join(
            [
                b"\xca\xfe\xba\xbe",
                struct.pack(">HHH", 0, 52, len(self.entries) + 1),
                *self.entries,
                struct.pack(">HHH", 0x0421, this_index, super_index),
                struct.pack(">H", 0),
                struct.pack(">H", len(field_rows)),
                *field_rows,
                struct.pack(">H", len(method_rows)),
                *method_rows,
                struct.pack(">H", len(class_attributes)),
                *class_attributes,
            ]
        )


def _class_bytes(class_name: str, **kwargs) -> bytes:
    return _ClassBuilder(class_name).build(**kwargs)


def _receipt(metadata: object | None, classes: dict[str, bytes]) -> tuple[bytes, dict]:
    entries = dict(classes)
    if metadata is not None:
        entries["cleanmix_version_compatibility.json"] = canonical_json_bytes(metadata)
    artifact = _archive(entries)
    receipt = build_ap_compatibility_conformance_receipt(
        ArtifactInput("example.jar", artifact), POLICY_BYTES
    )
    return artifact, receipt


def _schema_errors(receipt: dict) -> list[tuple[list[object], str]]:
    errors = sorted(VALIDATOR.iter_errors(receipt), key=lambda error: list(error.path))
    return [(list(error.path), error.message) for error in errors]


class MixinAPCompatibilityTests(unittest.TestCase):
    def test_default_class_and_explicit_class_member_overrides_conform(self) -> None:
        classes = {
            "example/DefaultMixin.class": _class_bytes(
                "example/DefaultMixin",
                fields=(("counter", "I", "0.6.0"),),
                methods=(
                    ("<init>", "()V", "0.1.0"),
                    ("apply", "(I)V", "0.1.0"),
                ),
            ),
            "example/LegacyMixin.class": _class_bytes(
                "example/LegacyMixin", class_compatibility="00.1.000"
            ),
        }
        metadata = {
            "example.DefaultMixin": "0.6.0",
            "example.DefaultMixin::<init>()V": "0.1.0",
            "example.DefaultMixin::apply(I)V": "0.1.0",
            "example.DefaultMixin::counter:I": "0.6.0",
            "example.LegacyMixin": "0.1.0",
        }
        artifact, receipt = _receipt(metadata, classes)
        validate_ap_compatibility_conformance_receipt(receipt)
        validate_bound_ap_compatibility_conformance_receipt(
            receipt, ArtifactInput("example.jar", artifact), POLICY_BYTES
        )
        self.assertEqual([], _schema_errors(receipt))
        self.assertEqual("conformant", receipt["summary"]["conformance_state"])
        self.assertEqual(2, receipt["summary"]["declared_mixin_count"])
        self.assertEqual(3, receipt["summary"]["member_override_count"])
        defaults = [
            row
            for row in receipt["expected_entries"]
            if row["source_kind"] == "policy-default"
        ]
        self.assertEqual(["example.DefaultMixin"], [row["key"] for row in defaults])

    def test_present_resource_missing_default_class_key_is_nonconformant(self) -> None:
        _, receipt = _receipt(
            {},
            {"example/Mixin.class": _class_bytes("example/Mixin")},
        )
        self.assertEqual("nonconformant", receipt["summary"]["conformance_state"])
        self.assertEqual(
            [("missing-key", "example.Mixin")],
            [(row["issue_kind"], row["key"]) for row in receipt["issues"]],
        )

    def test_extra_key_and_member_version_mismatch_are_both_rejected(self) -> None:
        _, receipt = _receipt(
            {
                "example.Mixin": "0.6.0",
                "example.Mixin::run()V": "0.6.0",
                "example.Orphan": "0.6.0",
            },
            {
                "example/Mixin.class": _class_bytes(
                    "example/Mixin", methods=(("run", "()V", "0.1.0"),)
                )
            },
        )
        kinds = {row["issue_kind"] for row in receipt["issues"]}
        self.assertEqual({"extra-key", "version-mismatch"}, kinds)

    def test_noncanonical_metadata_version_is_invalid_not_missing(self) -> None:
        _, receipt = _receipt(
            {"example.Mixin": "00.6.0"},
            {"example/Mixin.class": _class_bytes("example/Mixin")},
        )
        self.assertEqual(1, len(receipt["issues"]))
        self.assertEqual("metadata-entry-invalid", receipt["issues"][0]["issue_kind"])
        self.assertEqual("example.Mixin", receipt["issues"][0]["key"])

    def test_missing_resource_fails_once_at_resource_boundary(self) -> None:
        _, receipt = _receipt(
            None,
            {"example/Mixin.class": _class_bytes("example/Mixin")},
        )
        self.assertEqual(
            ["metadata-resource-missing"],
            [row["issue_kind"] for row in receipt["issues"]],
        )

    def test_no_mixin_and_no_resource_is_not_applicable(self) -> None:
        _, receipt = _receipt(
            None,
            {
                "example/Ordinary.class": _class_bytes(
                    "example/Ordinary", mixin=False
                )
            },
        )
        self.assertEqual("not-applicable", receipt["summary"]["conformance_state"])
        self.assertEqual([], receipt["issues"])

    def test_reidentified_issue_omission_is_rejected(self) -> None:
        _, receipt = _receipt(
            {},
            {"example/Mixin.class": _class_bytes("example/Mixin")},
        )
        forged = deepcopy(receipt)
        forged["issues"] = []
        forged["summary"]["issue_count"] = 0
        forged["summary"]["issue_counts"]["missing-key"] = 0
        forged["summary"]["conformance_state"] = "conformant"
        material = {key: value for key, value in forged.items() if key != "receipt_id"}
        forged["receipt_id"] = (
            "workbench-mixin-ap-compatibility-conformance-receipt:sha256:"
            + hashlib.sha256(canonical_json_bytes(material)).hexdigest()
        )
        with self.assertRaisesRegex(ArtifactScanError, "issues are not reproducible"):
            validate_ap_compatibility_conformance_receipt(forged)

    def test_cli_publishes_negative_receipt_and_exits_two(self) -> None:
        artifact, _ = _receipt(
            {},
            {"example/Mixin.class": _class_bytes("example/Mixin")},
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            jar = root / "example.jar"
            policy = root / "policy.json"
            output = root / "receipt.json"
            jar.write_bytes(artifact)
            policy.write_bytes(POLICY_BYTES)
            result = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    str(jar),
                    "--policy",
                    str(policy),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(2, result.returncode, result.stderr)
            published = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                "nonconformant", published["summary"]["conformance_state"]
            )


if __name__ == "__main__":
    unittest.main()
