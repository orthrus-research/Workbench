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
    ArtifactScanError,
    ClassResolutionRequest,
    ClosureArtifactInput,
    DependencyEdgeInput,
    build_dependency_closure_receipt,
    canonical_json_bytes,
    validate_dependency_closure_receipt,
)


SCHEMA = json.loads(
    (
        ROOT
        / "modules/project-intelligence/schemas/"
        "mixin-dependency-closure-receipt-v1.schema.json"
    ).read_text(encoding="utf-8")
)
VALIDATOR = Draft202012Validator(SCHEMA)
TOOL = ROOT / "tools/inspect_mixin_dependency_closure.py"


def _archive(entries: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        for name, payload in sorted(entries.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_STORED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload)
    return output.getvalue()


def _class_bytes(
    class_name: str,
    *,
    superclass: str | None = "java/lang/Object",
    interfaces: tuple[str, ...] = (),
) -> bytes:
    entries: list[bytes] = []

    def utf8(value: str) -> int:
        encoded = value.encode("ascii")
        entries.append(b"\x01" + struct.pack(">H", len(encoded)) + encoded)
        return len(entries)

    def class_ref(value: str) -> int:
        name_index = utf8(value)
        entries.append(b"\x07" + struct.pack(">H", name_index))
        return len(entries)

    this_index = class_ref(class_name)
    super_index = 0 if superclass is None else class_ref(superclass)
    interface_indices = [class_ref(value) for value in interfaces]
    return b"".join(
        [
            b"\xca\xfe\xba\xbe",
            struct.pack(">HHH", 0, 52, len(entries) + 1),
            *entries,
            struct.pack(">HHH", 0x0021, this_index, super_index),
            struct.pack(">H", len(interface_indices)),
            b"".join(struct.pack(">H", value) for value in interface_indices),
            struct.pack(">HHH", 0, 0, 0),
        ]
    )


def _assert_schema(test: unittest.TestCase, receipt: dict) -> None:
    errors = sorted(VALIDATOR.iter_errors(receipt), key=lambda error: list(error.path))
    test.assertEqual([], [(list(error.path), error.message) for error in errors])


class MixinDependencyClosureTests(unittest.TestCase):
    def test_exact_headers_edges_and_resolution_paths_are_retained(self) -> None:
        root = _archive(
            {
                "example/Connector.class": _class_bytes(
                    "example/Connector", superclass="example/BaseConnector"
                )
            }
        )
        dependency = _archive(
            {
                "example/BaseConnector.class": _class_bytes(
                    "example/BaseConnector",
                    interfaces=(
                        "org/spongepowered/asm/mixin/connect/IMixinConnector",
                    ),
                )
            }
        )
        receipt = build_dependency_closure_receipt(
            [
                ClosureArtifactInput("root.jar", root, "root"),
                ClosureArtifactInput("dependency.jar", dependency, "required-runtime"),
            ],
            [DependencyEdgeInput("root.jar", "dependency.jar", "required-runtime")],
            [ClassResolutionRequest("root.jar", "example.Connector", "mixin-connector")],
            closure_complete=True,
        )
        validate_dependency_closure_receipt(receipt)
        _assert_schema(self, receipt)
        resolution = receipt["resolutions"][0]
        root_sha = next(
            row["sha256"] for row in receipt["artifacts"] if row["file_name"] == "root.jar"
        )
        self.assertEqual("resolved", resolution["resolution_state"])
        self.assertEqual([root_sha], resolution["selected_path"])
        connector = next(
            row for row in receipt["class_headers"] if row["class_name"] == "example.Connector"
        )
        base = next(
            row for row in receipt["class_headers"] if row["class_name"] == "example.BaseConnector"
        )
        self.assertEqual("example.BaseConnector", connector["superclass_name"])
        self.assertEqual(
            ["org.spongepowered.asm.mixin.connect.IMixinConnector"],
            base["direct_interfaces"],
        )

    def test_undeclared_adjacent_provider_is_not_reachable(self) -> None:
        root = _archive({})
        adjacent = _archive(
            {"example/Plugin.class": _class_bytes("example/Plugin")}
        )
        receipt = build_dependency_closure_receipt(
            [
                ClosureArtifactInput("root.jar", root, "root"),
                ClosureArtifactInput("adjacent.jar", adjacent, "observation-only"),
            ],
            [],
            [ClassResolutionRequest("root.jar", "example.Plugin", "config-plugin")],
            closure_complete=True,
        )
        self.assertEqual("missing", receipt["resolutions"][0]["resolution_state"])
        self.assertEqual([], receipt["resolutions"][0]["providers"])

    def test_duplicate_reachable_class_is_ambiguous(self) -> None:
        root = _archive({})
        first = _archive({"example/Plugin.class": _class_bytes("example/Plugin")})
        second = _archive(
            {
                "example/Plugin.class": _class_bytes(
                    "example/Plugin", interfaces=("java/io/Serializable",)
                )
            }
        )
        receipt = build_dependency_closure_receipt(
            [
                ClosureArtifactInput("root.jar", root, "root"),
                ClosureArtifactInput("first.jar", first, "required-runtime"),
                ClosureArtifactInput("second.jar", second, "required-runtime"),
            ],
            [
                DependencyEdgeInput("root.jar", "first.jar", "required-runtime"),
                DependencyEdgeInput("root.jar", "second.jar", "required-runtime"),
            ],
            [ClassResolutionRequest("root.jar", "example.Plugin", "config-plugin")],
            closure_complete=True,
        )
        resolution = receipt["resolutions"][0]
        self.assertEqual("ambiguous", resolution["resolution_state"])
        self.assertEqual(2, len(resolution["providers"]))
        self.assertTrue(all(len(row["resolution_path"]) == 2 for row in resolution["providers"]))

    def test_incomplete_closure_never_converts_absence_to_missing(self) -> None:
        receipt = build_dependency_closure_receipt(
            [ClosureArtifactInput("root.jar", _archive({}), "root")],
            [],
            [ClassResolutionRequest("root.jar", "example.Plugin", "config-plugin")],
            closure_complete=False,
        )
        self.assertEqual("unresolved", receipt["resolutions"][0]["resolution_state"])
        self.assertEqual("caller-declared-incomplete", receipt["closure"]["completeness_assertion"])

    def test_reidentified_resolution_tampering_is_rejected(self) -> None:
        receipt = build_dependency_closure_receipt(
            [ClosureArtifactInput("root.jar", _archive({}), "root")],
            [],
            [ClassResolutionRequest("root.jar", "example.Plugin", "config-plugin")],
            closure_complete=True,
        )
        forged = deepcopy(receipt)
        resolution = forged["resolutions"][0]
        resolution["resolution_state"] = "unresolved"
        material = {key: value for key, value in resolution.items() if key != "resolution_id"}
        resolution["resolution_id"] = (
            "workbench-jvm-class-resolution:sha256:"
            + hashlib.sha256(canonical_json_bytes(material)).hexdigest()
        )
        forged["resolutions"].sort(key=lambda row: row["resolution_id"])
        material = {key: value for key, value in forged.items() if key != "receipt_id"}
        forged["receipt_id"] = (
            "workbench-mixin-dependency-closure-receipt:sha256:"
            + hashlib.sha256(canonical_json_bytes(material)).hexdigest()
        )
        with self.assertRaisesRegex(ArtifactScanError, "not reproducible"):
            validate_dependency_closure_receipt(forged)

    def test_cli_publishes_declared_closure_without_loading_classes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "root.jar"
            spec = root / "closure.json"
            output = root / "receipt.json"
            artifact.write_bytes(_archive({}))
            spec.write_text(
                json.dumps(
                    {
                        "artifacts": [
                            {"label": "root.jar", "path": "root.jar", "role": "root"}
                        ],
                        "closure_complete": False,
                        "dependencies": [],
                        "format": "workbench-mixin-dependency-closure-input-v1",
                        "requests": [],
                        "schema_version": 1,
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--compact",
                    "--spec",
                    str(spec),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            receipt = json.loads(output.read_text(encoding="utf-8"))
            validate_dependency_closure_receipt(receipt)


if __name__ == "__main__":
    unittest.main()
