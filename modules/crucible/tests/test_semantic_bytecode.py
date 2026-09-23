#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_crucible_observatory import CaptureValidationError  # noqa: E402
from workbench_crucible_observatory.semantic_bytecode import (  # noqa: E402
    SEMANTIC_BYTECODE_POLICY_ID,
    build_semantic_bytecode_manifest,
    evaluate_semantic_bytecode_manifests,
    load_semantic_bytecode_manifest,
    parse_semantic_bytecode_manifest,
    write_semantic_bytecode_manifest,
)


TARGET = "Lorg/spongepowered/asm/mixin/transformer/meta/MixinMerged;"
UUID_A = "11111111-2222-4333-8444-555555555555"
UUID_B = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


class Pool:
    def __init__(self) -> None:
        self.entries: list[bytes | None] = []

    def utf8(self, value: str | bytes) -> int:
        raw = value.encode("utf-8") if isinstance(value, str) else value
        self.entries.append(b"\x01" + struct.pack(">H", len(raw)) + raw)
        return len(self.entries)

    def clazz(self, name_index: int) -> int:
        self.entries.append(b"\x07" + struct.pack(">H", name_index))
        return len(self.entries)

    def integer(self, value: int) -> int:
        self.entries.append(b"\x03" + struct.pack(">i", value))
        return len(self.entries)

    def string(self, value_index: int) -> int:
        self.entries.append(b"\x08" + struct.pack(">H", value_index))
        return len(self.entries)

    def bytes(self) -> bytes:
        return b"".join(entry for entry in self.entries if entry is not None)


def annotated_class(
    session: str,
    *,
    annotation_descriptor: str = TARGET,
    session_name: str = "sessionId",
    session_tag: str = "s",
    include_session: bool = True,
    method_count: int = 1,
    source_file_shares_session: bool = False,
    constant_string_shares_session: bool = False,
    unknown_attribute: bool = False,
    annotation_attribute_name: str = "RuntimeVisibleAnnotations",
    duplicate_session: bool = False,
    method_name: str = "probe",
) -> bytes:
    pool = Pool()
    this_name = pool.utf8("example/Observed")
    this_class = pool.clazz(this_name)
    parent_name = pool.utf8("java/lang/Object")
    parent_class = pool.clazz(parent_name)
    method_name_index = pool.utf8(method_name)
    descriptor_index = pool.utf8("()V")
    annotations_name = pool.utf8(annotation_attribute_name)
    annotation_type = pool.utf8(annotation_descriptor)
    element_name = pool.utf8(session_name)
    session_value = pool.utf8(session)
    if constant_string_shares_session:
        pool.string(session_value)
    source_file_name = pool.utf8("SourceFile")
    unknown_name = pool.utf8("TestOpaqueAttribute")

    pairs = []
    if include_session:
        pairs.append(
            struct.pack(">H", element_name)
            + session_tag.encode("ascii")
            + struct.pack(">H", session_value)
        )
    if duplicate_session:
        pairs.append(
            struct.pack(">H", element_name)
            + b"s"
            + struct.pack(">H", session_value)
        )
    annotation = (
        struct.pack(">H", 1)
        + struct.pack(">HH", annotation_type, len(pairs))
        + b"".join(pairs)
    )
    annotation_attribute = (
        struct.pack(">HI", annotations_name, len(annotation)) + annotation
    )
    methods = []
    for _ in range(method_count):
        methods.append(
            struct.pack(">HHHH", 0x0401, method_name_index, descriptor_index, 1)
            + annotation_attribute
        )

    class_attributes = []
    if source_file_shares_session:
        class_attributes.append(
            struct.pack(">HIH", source_file_name, 2, session_value)
        )
    if unknown_attribute:
        class_attributes.append(struct.pack(">HI", unknown_name, 0))

    return b"".join(
        (
            struct.pack(">IHHH", 0xCAFEBABE, 0, 52, len(pool.entries) + 1),
            pool.bytes(),
            struct.pack(">HHH", 0x0421, this_class, parent_class),
            struct.pack(">H", 0),  # interfaces
            struct.pack(">H", 0),  # fields
            struct.pack(">H", len(methods)),
            b"".join(methods),
            struct.pack(">H", len(class_attributes)),
            b"".join(class_attributes),
        )
    )


class SemanticBytecodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def dump(self, name: str, data: bytes) -> Path:
        root = self.root / name
        target = root / "example" / "Observed.class"
        target.parent.mkdir(parents=True)
        target.write_bytes(data)
        return root

    def test_only_targeted_session_uuid_is_semantically_normalized(self) -> None:
        first = self.dump("first", annotated_class(UUID_A))
        second = self.dump("second", annotated_class(UUID_B))

        a = build_semantic_bytecode_manifest(first)
        b = build_semantic_bytecode_manifest(second)

        self.assertNotEqual(
            a.exact_foundation_manifest_sha256,
            b.exact_foundation_manifest_sha256,
        )
        self.assertNotEqual(a.classes[0].exact_sha256, b.classes[0].exact_sha256)
        self.assertEqual(a.classes[0].semantic_sha256, b.classes[0].semantic_sha256)
        self.assertEqual(a.semantic_dump_sha256, b.semantic_dump_sha256)
        self.assertEqual(a.normalized_class_count, 1)
        self.assertEqual(a.normalized_utf8_constants, 1)
        self.assertEqual(a.normalized_annotation_references, 1)

        comparison = evaluate_semantic_bytecode_manifests({"a": a, "b": b})
        self.assertEqual(comparison["policy_id"], SEMANTIC_BYTECODE_POLICY_ID)
        self.assertTrue(comparison["exact_foundation_manifests_all_distinct"])
        self.assertTrue(comparison["semantic_dumps_all_equal"])

    def test_safe_sharing_between_target_annotations_is_allowed(self) -> None:
        manifest = build_semantic_bytecode_manifest(
            self.dump("shared", annotated_class(UUID_A, method_count=2))
        )
        self.assertEqual(manifest.normalized_utf8_constants, 1)
        self.assertEqual(manifest.normalized_annotation_references, 2)

    def test_uuid_in_another_annotation_is_preserved(self) -> None:
        first = build_semantic_bytecode_manifest(
            self.dump(
                "first-other",
                annotated_class(
                    UUID_A,
                    annotation_descriptor="Lexample/Other;",
                    unknown_attribute=True,
                ),
            )
        )
        second = build_semantic_bytecode_manifest(
            self.dump(
                "second-other",
                annotated_class(UUID_B, annotation_descriptor="Lexample/Other;"),
            )
        )
        self.assertEqual(first.normalized_utf8_constants, 0)
        self.assertEqual(first.classes[0].exact_sha256, first.classes[0].semantic_sha256)
        self.assertNotEqual(first.semantic_dump_sha256, second.semantic_dump_sha256)

    def test_every_other_class_byte_remains_semantic(self) -> None:
        first = build_semantic_bytecode_manifest(
            self.dump("method-a", annotated_class(UUID_A, method_name="probe"))
        )
        second = build_semantic_bytecode_manifest(
            self.dump("method-b", annotated_class(UUID_B, method_name="other"))
        )
        self.assertNotEqual(first.semantic_dump_sha256, second.semantic_dump_sha256)

    def test_rejects_unsafe_shared_session_constant(self) -> None:
        for index, options in enumerate(
            (
                {"source_file_shares_session": True},
                {"constant_string_shares_session": True},
            )
        ):
            with self.subTest(index=index):
                with self.assertRaisesRegex(
                    CaptureValidationError, "unsafe shared reference"
                ):
                    build_semantic_bytecode_manifest(
                        self.dump(
                            f"unsafe-{index}",
                            annotated_class(UUID_A, **options),
                        )
                    )

    def test_rejects_target_annotation_outside_visible_method_metadata(self) -> None:
        with self.assertRaisesRegex(
            CaptureValidationError,
            "outside a method RuntimeVisibleAnnotations",
        ):
            build_semantic_bytecode_manifest(
                self.dump(
                    "invisible-target",
                    annotated_class(
                        UUID_A,
                        annotation_attribute_name="RuntimeInvisibleAnnotations",
                    ),
                )
            )

    def test_rejects_unknown_attributes_when_rewriting(self) -> None:
        with self.assertRaisesRegex(CaptureValidationError, "unknown attributes"):
            build_semantic_bytecode_manifest(
                self.dump(
                    "unknown",
                    annotated_class(UUID_A, unknown_attribute=True),
                )
            )

    def test_rejects_malformed_or_ambiguous_target_elements(self) -> None:
        cases = (
            ("not-a-uuid", {}, "canonical lowercase UUID"),
            (UUID_A, {"include_session": False}, "lacks exactly one sessionId"),
            (UUID_A, {"session_tag": "c"}, "is not a string"),
            (UUID_A, {"duplicate_session": True}, "duplicate annotation element"),
            (UUID_A, {"session_name": "other"}, "lacks exactly one sessionId"),
        )
        for index, (value, options, message) in enumerate(cases):
            with self.subTest(index=index):
                with self.assertRaisesRegex(CaptureValidationError, message):
                    build_semantic_bytecode_manifest(
                        self.dump(f"malformed-{index}", annotated_class(value, **options))
                    )

    def test_strict_class_parser_rejects_trailing_and_bad_modified_utf8(self) -> None:
        with self.assertRaisesRegex(CaptureValidationError, "trailing class bytes"):
            build_semantic_bytecode_manifest(
                self.dump("trailing", annotated_class(UUID_A) + b"x")
            )

        malformed = annotated_class(UUID_A).replace(b"example/Observed", b"example\x00Observed")
        with self.assertRaisesRegex(CaptureValidationError, "modified UTF-8"):
            build_semantic_bytecode_manifest(self.dump("mutf", malformed))

    def test_manifest_round_trip_and_digest_validation(self) -> None:
        manifest = build_semantic_bytecode_manifest(
            self.dump("roundtrip", annotated_class(UUID_A))
        )
        destination = self.root / "manifest.json"
        write_semantic_bytecode_manifest(manifest, destination)
        loaded = load_semantic_bytecode_manifest(destination)
        self.assertEqual(loaded.manifest_sha256, manifest.manifest_sha256)
        self.assertEqual(loaded.semantic_dump_sha256, manifest.semantic_dump_sha256)

        value = json.loads(destination.read_text(encoding="utf-8"))
        value["classes"][0]["semantic_sha256"] = hashlib.sha256(b"tampered").hexdigest()
        with self.assertRaisesRegex(CaptureValidationError, "semantic dump digest"):
            parse_semantic_bytecode_manifest(value)


if __name__ == "__main__":
    unittest.main()
