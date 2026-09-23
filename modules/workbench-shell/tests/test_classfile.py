"""Focused tests for the metadata-only Java class reader."""

from __future__ import annotations

from pathlib import Path
import struct
import sys
import unittest


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_shell.classfile import (  # noqa: E402
    method_argument_names,
    parse_class_file,
)


def _u2(value: int) -> bytes:
    return struct.pack(">H", value)


def _u4(value: int) -> bytes:
    return struct.pack(">I", value)


class _Pool:
    def __init__(self) -> None:
        self.entries: list[bytes] = []

    def utf8(self, value: str) -> int:
        payload = value.encode("utf-8")
        self.entries.append(b"\x01" + _u2(len(payload)) + payload)
        return len(self.entries)

    def class_(self, value: str) -> int:
        name = self.utf8(value)
        self.entries.append(b"\x07" + _u2(name))
        return len(self.entries)

    def integer(self, value: int) -> int:
        self.entries.append(b"\x03" + struct.pack(">i", value))
        return len(self.entries)

    def bytes(self) -> bytes:
        return _u2(len(self.entries) + 1) + b"".join(self.entries)


def target_class() -> bytes:
    pool = _Pool()
    this_class = pool.class_("example/Target")
    super_class = pool.class_("java/lang/Object")
    method_name = pool.utf8("setBlock")
    method_descriptor = pool.utf8(
        "(Ljava/lang/Object;Ljava/lang/Object;I)Z"
    )
    code_name = pool.utf8("Code")
    local_table_name = pool.utf8("LocalVariableTable")
    variables = [
        (pool.utf8("this"), pool.utf8("Lexample/Target;"), 0),
        (pool.utf8("pos"), pool.utf8("Ljava/lang/Object;"), 1),
        (pool.utf8("state"), pool.utf8("Ljava/lang/Object;"), 2),
        (pool.utf8("flag"), pool.utf8("I"), 3),
    ]
    local_table = _u2(len(variables)) + b"".join(
        _u2(0) + _u2(2) + _u2(name) + _u2(descriptor) + _u2(slot)
        for name, descriptor, slot in variables
    )
    code = (
        _u2(1)
        + _u2(4)
        + _u4(2)
        + b"\x04\xac"
        + _u2(0)
        + _u2(1)
        + _u2(local_table_name)
        + _u4(len(local_table))
        + local_table
    )
    method = (
        _u2(0x0001)
        + _u2(method_name)
        + _u2(method_descriptor)
        + _u2(1)
        + _u2(code_name)
        + _u4(len(code))
        + code
    )
    return (
        b"\xca\xfe\xba\xbe"
        + _u2(0)
        + _u2(52)
        + pool.bytes()
        + _u2(0x0021)
        + _u2(this_class)
        + _u2(super_class)
        + _u2(0)
        + _u2(0)
        + _u2(1)
        + method
        + _u2(0)
    )


def mixin_class() -> bytes:
    pool = _Pool()
    this_class = pool.class_("example/Mixin")
    super_class = pool.class_("java/lang/Object")
    method_name = pool.utf8("setBlock")
    method_descriptor = pool.utf8("(I)I")
    annotations_name = pool.utf8("RuntimeVisibleAnnotations")
    annotation_type = pool.utf8(
        "Lorg/spongepowered/asm/mixin/injection/ModifyVariable;"
    )
    method_key = pool.utf8("method")
    method_value = pool.utf8("setBlock")
    name_key = pool.utf8("name")
    name_value = pool.utf8("arg3")
    args_only_key = pool.utf8("argsOnly")
    require_key = pool.utf8("require")
    one = pool.integer(1)
    annotation = (
        _u2(1)
        + _u2(annotation_type)
        + _u2(4)
        + _u2(method_key)
        + b"["
        + _u2(1)
        + b"s"
        + _u2(method_value)
        + _u2(name_key)
        + b"["
        + _u2(1)
        + b"s"
        + _u2(name_value)
        + _u2(args_only_key)
        + b"Z"
        + _u2(one)
        + _u2(require_key)
        + b"I"
        + _u2(one)
    )
    method = (
        _u2(0x0401)
        + _u2(method_name)
        + _u2(method_descriptor)
        + _u2(1)
        + _u2(annotations_name)
        + _u4(len(annotation))
        + annotation
    )
    return (
        b"\xca\xfe\xba\xbe"
        + _u2(0)
        + _u2(52)
        + pool.bytes()
        + _u2(0x0421)
        + _u2(this_class)
        + _u2(super_class)
        + _u2(0)
        + _u2(0)
        + _u2(1)
        + method
        + _u2(0)
    )


class ClassFileTest(unittest.TestCase):
    def test_reads_argument_names_and_modify_variable_annotation(self) -> None:
        target = parse_class_file(target_class())
        self.assertEqual(target["class_name"], "example/Target")
        self.assertEqual(
            method_argument_names(target["methods"][0]),
            ["pos", "state", "flag"],
        )

        mixin = parse_class_file(mixin_class())
        annotation = mixin["methods"][0]["annotations"][0]
        self.assertTrue(annotation["descriptor"].endswith("ModifyVariable;"))
        self.assertEqual(annotation["values"]["method"], ["setBlock"])
        self.assertEqual(annotation["values"]["name"], ["arg3"])
        self.assertEqual(annotation["values"]["require"], 1)


if __name__ == "__main__":
    unittest.main()
