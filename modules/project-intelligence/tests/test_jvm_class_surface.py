from __future__ import annotations

from io import BytesIO
from pathlib import Path
import struct
import sys
import unittest
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/project-intelligence/src"
sys.path.insert(0, str(SOURCE))

from workbench_project_intelligence import (  # noqa: E402
    ArtifactScanError,
    inspect_class_bytes,
    scan_jvm_archive_bytes,
)


def _u1(value: int) -> bytes:
    return struct.pack(">B", value)


def _u2(value: int) -> bytes:
    return struct.pack(">H", value)


def _u4(value: int) -> bytes:
    return struct.pack(">I", value)


def _utf8(value: str) -> bytes:
    raw = value.encode("utf-8")
    return _u1(1) + _u2(len(raw)) + raw


def _index(tag: int, value: int) -> bytes:
    return _u1(tag) + _u2(value)


def _pair(tag: int, left: int, right: int) -> bytes:
    return _u1(tag) + _u2(left) + _u2(right)


def _class_bytes(*, method_descriptor: str = "(Ljava/lang/String;)I") -> bytes:
    pool = [
        _utf8("example/Thing"),  # 1
        _index(7, 1),  # 2 Class
        _utf8("java/lang/Object"),  # 3
        _index(7, 3),  # 4 Class
        _utf8("value"),  # 5
        _utf8("I"),  # 6
        _utf8("run"),  # 7
        _utf8(method_descriptor),  # 8
        _utf8("SourceFile"),  # 9
        _utf8("Thing.java"),  # 10
        _utf8("java/lang/String"),  # 11
        _index(7, 11),  # 12 Class
        _utf8("other/Helper"),  # 13
        _index(7, 13),  # 14 Class
        _utf8("call"),  # 15
        _utf8("()V"),  # 16
        _pair(12, 15, 16),  # 17 NameAndType
        _pair(10, 14, 17),  # 18 Methodref
        _utf8("example:literal"),  # 19
        _index(8, 19),  # 20 String
    ]
    field = _u2(0x0001) + _u2(5) + _u2(6) + _u2(0)
    method = _u2(0x0401) + _u2(7) + _u2(8) + _u2(0)
    source_attribute = _u2(9) + _u4(2) + _u2(10)
    return b"".join(
        [
            b"\xca\xfe\xba\xbe",
            _u2(0),
            _u2(52),
            _u2(len(pool) + 1),
            *pool,
            _u2(0x0401),
            _u2(2),
            _u2(4),
            _u2(0),
            _u2(1),
            field,
            _u2(1),
            method,
            _u2(1),
            source_attribute,
        ]
    )


class JvmClassSurfaceTests(unittest.TestCase):
    def test_decodes_exact_class_members_references_and_source_metadata(self) -> None:
        value = inspect_class_bytes(
            _class_bytes(), entry_path="example/Thing.class"
        )

        self.assertEqual("example.Thing", value["class_name"])
        self.assertEqual({"major": 52, "minor": 0}, value["classfile_version"])
        self.assertEqual("Thing.java", value["source_file"])
        self.assertEqual("int", value["fields"][0]["display_type"])
        self.assertEqual(["java.lang.String"], value["methods"][0]["parameter_types"])
        self.assertEqual("int", value["methods"][0]["return_type"])
        self.assertIn("java.lang.String", value["class_references"])
        self.assertEqual("other.Helper", value["member_references"][0]["owner"])
        self.assertEqual(["example:literal"], value["constant_strings"])

    def test_archive_surface_binds_classes_and_resources_to_same_bytes(self) -> None:
        buffer = BytesIO()
        with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("example/Thing.class", _class_bytes())
            archive.writestr("assets/example/config.json", b"{}\n")
        first = scan_jvm_archive_bytes(buffer.getvalue(), label="example.jar")
        second = scan_jvm_archive_bytes(buffer.getvalue(), label="example.jar")

        self.assertEqual(first, second)
        self.assertTrue(first["coverage"]["complete"])
        self.assertEqual(1, first["coverage"]["scanned_classes"])
        self.assertEqual("example.Thing", first["classes"][0]["class_name"])
        self.assertEqual(
            "assets/example/config.json", first["resources"][0]["path"]
        )

    def test_rejects_invalid_descriptors_and_non_class_bytes(self) -> None:
        with self.assertRaisesRegex(ArtifactScanError, "method parameter"):
            inspect_class_bytes(
                _class_bytes(method_descriptor="(V)V"),
                entry_path="example/Thing.class",
            )
        with self.assertRaisesRegex(ArtifactScanError, "invalid magic"):
            inspect_class_bytes(b"not-a-class", entry_path="Bad.class")


if __name__ == "__main__":
    unittest.main()
