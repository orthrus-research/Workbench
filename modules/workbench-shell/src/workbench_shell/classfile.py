"""Small, dependency-free Java class-file reader for runtime diagnosis.

The reader intentionally exposes only the metadata Workbench needs to explain
bytecode compatibility failures: method descriptors, local variable tables,
and method annotations. It never rewrites a class file.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Any


class ClassFileError(ValueError):
    """Raised when class bytes are malformed or unsupported."""


@dataclass
class _Reader:
    data: bytes
    offset: int = 0

    def take(self, size: int) -> bytes:
        if size < 0 or self.offset + size > len(self.data):
            raise ClassFileError("class file ended unexpectedly")
        start = self.offset
        self.offset += size
        return self.data[start:self.offset]

    def u1(self) -> int:
        return self.take(1)[0]

    def u2(self) -> int:
        return int.from_bytes(self.take(2), "big")

    def u4(self) -> int:
        return int.from_bytes(self.take(4), "big")


class _ConstantPool:
    def __init__(self, entries: list[Any | None]) -> None:
        self._entries = entries

    def _entry(self, index: int) -> tuple[str, Any]:
        if index <= 0 or index >= len(self._entries):
            raise ClassFileError(f"invalid constant-pool index {index}")
        entry = self._entries[index]
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise ClassFileError(f"unusable constant-pool index {index}")
        return entry

    def utf8(self, index: int) -> str:
        kind, value = self._entry(index)
        if kind != "utf8" or not isinstance(value, str):
            raise ClassFileError(
                f"constant-pool index {index} is not UTF-8 text"
            )
        return value

    def class_name(self, index: int) -> str:
        kind, value = self._entry(index)
        if kind != "class" or not isinstance(value, int):
            raise ClassFileError(
                f"constant-pool index {index} is not a class"
            )
        return self.utf8(value)

    def annotation_constant(self, index: int) -> Any:
        kind, value = self._entry(index)
        if kind == "utf8":
            return value
        if kind == "string" and isinstance(value, int):
            return self.utf8(value)
        if kind in {"integer", "long", "float", "double"}:
            return value
        raise ClassFileError(
            f"constant-pool index {index} is not an annotation constant"
        )


def _constant_pool(reader: _Reader) -> _ConstantPool:
    count = reader.u2()
    entries: list[Any | None] = [None] * count
    index = 1
    while index < count:
        tag = reader.u1()
        if tag == 1:
            payload = reader.take(reader.u2())
            entries[index] = (
                "utf8",
                payload.decode("utf-8", errors="replace"),
            )
        elif tag == 3:
            entries[index] = (
                "integer",
                struct.unpack(">i", reader.take(4))[0],
            )
        elif tag == 4:
            entries[index] = (
                "float",
                struct.unpack(">f", reader.take(4))[0],
            )
        elif tag == 5:
            entries[index] = (
                "long",
                struct.unpack(">q", reader.take(8))[0],
            )
            index += 1
        elif tag == 6:
            entries[index] = (
                "double",
                struct.unpack(">d", reader.take(8))[0],
            )
            index += 1
        elif tag == 7:
            entries[index] = ("class", reader.u2())
        elif tag == 8:
            entries[index] = ("string", reader.u2())
        elif tag in {9, 10, 11}:
            reader.take(4)
            entries[index] = ("reference", None)
        elif tag == 12:
            reader.take(4)
            entries[index] = ("name-and-type", None)
        elif tag == 15:
            reader.take(3)
            entries[index] = ("method-handle", None)
        elif tag == 16:
            reader.take(2)
            entries[index] = ("method-type", None)
        elif tag in {17, 18}:
            reader.take(4)
            entries[index] = ("dynamic", None)
        elif tag in {19, 20}:
            reader.take(2)
            entries[index] = ("module-or-package", None)
        else:
            raise ClassFileError(f"unsupported constant-pool tag {tag}")
        index += 1
    return _ConstantPool(entries)


def _annotation_value(reader: _Reader, pool: _ConstantPool) -> Any:
    tag = chr(reader.u1())
    if tag in "BCDFIJSZs":
        return pool.annotation_constant(reader.u2())
    if tag == "e":
        return {
            "enum_type": pool.utf8(reader.u2()),
            "enum_value": pool.utf8(reader.u2()),
        }
    if tag == "c":
        return {"class": pool.utf8(reader.u2())}
    if tag == "@":
        return _annotation(reader, pool)
    if tag == "[":
        return [
            _annotation_value(reader, pool)
            for _ in range(reader.u2())
        ]
    raise ClassFileError(f"unsupported annotation value tag {tag!r}")


def _annotation(reader: _Reader, pool: _ConstantPool) -> dict[str, Any]:
    descriptor = pool.utf8(reader.u2())
    values: dict[str, Any] = {}
    for _ in range(reader.u2()):
        name = pool.utf8(reader.u2())
        values[name] = _annotation_value(reader, pool)
    return {"descriptor": descriptor, "values": values}


def _annotations(payload: bytes, pool: _ConstantPool) -> list[dict[str, Any]]:
    reader = _Reader(payload)
    result = [
        _annotation(reader, pool)
        for _ in range(reader.u2())
    ]
    if reader.offset != len(payload):
        raise ClassFileError("annotation attribute has trailing bytes")
    return result


def _local_variables(
    payload: bytes,
    pool: _ConstantPool,
) -> list[dict[str, Any]]:
    reader = _Reader(payload)
    result = []
    for _ in range(reader.u2()):
        result.append({
            "start_pc": reader.u2(),
            "length": reader.u2(),
            "name": pool.utf8(reader.u2()),
            "descriptor": pool.utf8(reader.u2()),
            "slot": reader.u2(),
        })
    if reader.offset != len(payload):
        raise ClassFileError("local-variable table has trailing bytes")
    return result


def _attributes(
    reader: _Reader,
    pool: _ConstantPool,
) -> list[tuple[str, bytes]]:
    result = []
    for _ in range(reader.u2()):
        name = pool.utf8(reader.u2())
        result.append((name, reader.take(reader.u4())))
    return result


def _code_local_variables(
    payload: bytes,
    pool: _ConstantPool,
) -> list[dict[str, Any]]:
    reader = _Reader(payload)
    reader.take(4)  # max_stack and max_locals
    reader.take(reader.u4())
    reader.take(reader.u2() * 8)
    variables: list[dict[str, Any]] = []
    for name, nested in _attributes(reader, pool):
        if name == "LocalVariableTable":
            variables.extend(_local_variables(nested, pool))
    if reader.offset != len(payload):
        raise ClassFileError("Code attribute has trailing bytes")
    return variables


def _skip_members(reader: _Reader, pool: _ConstantPool) -> None:
    for _ in range(reader.u2()):
        reader.take(6)
        _attributes(reader, pool)


def parse_class_file(payload: bytes) -> dict[str, Any]:
    """Parse diagnostic metadata from one Java class-file payload."""

    reader = _Reader(payload)
    if reader.u4() != 0xCAFEBABE:
        raise ClassFileError("payload is not a Java class file")
    minor = reader.u2()
    major = reader.u2()
    pool = _constant_pool(reader)
    reader.u2()  # class access flags
    this_class = reader.u2()
    reader.u2()  # super class
    reader.take(reader.u2() * 2)
    _skip_members(reader, pool)

    methods = []
    for _ in range(reader.u2()):
        access_flags = reader.u2()
        name = pool.utf8(reader.u2())
        descriptor = pool.utf8(reader.u2())
        local_variables: list[dict[str, Any]] = []
        annotations: list[dict[str, Any]] = []
        for attribute_name, attribute_payload in _attributes(reader, pool):
            if attribute_name == "Code":
                local_variables.extend(
                    _code_local_variables(attribute_payload, pool)
                )
            elif attribute_name in {
                "RuntimeVisibleAnnotations",
                "RuntimeInvisibleAnnotations",
            }:
                annotations.extend(
                    _annotations(attribute_payload, pool)
                )
        methods.append({
            "name": name,
            "descriptor": descriptor,
            "access_flags": access_flags,
            "local_variables": local_variables,
            "annotations": annotations,
        })
    _attributes(reader, pool)
    if reader.offset != len(payload):
        raise ClassFileError("class file has trailing bytes")
    return {
        "class_name": pool.class_name(this_class),
        "major_version": major,
        "minor_version": minor,
        "methods": methods,
    }


def method_argument_slots(
    descriptor: str,
    *,
    is_static: bool,
) -> list[dict[str, Any]]:
    """Return JVM local-variable slots occupied by method arguments."""

    if not descriptor.startswith("("):
        raise ClassFileError(f"invalid method descriptor {descriptor!r}")
    result = []
    offset = 1
    slot = 0 if is_static else 1
    while offset < len(descriptor) and descriptor[offset] != ")":
        start = offset
        while offset < len(descriptor) and descriptor[offset] == "[":
            offset += 1
        if offset >= len(descriptor):
            raise ClassFileError(f"invalid method descriptor {descriptor!r}")
        kind = descriptor[offset]
        if kind == "L":
            terminator = descriptor.find(";", offset)
            if terminator < 0:
                raise ClassFileError(
                    f"invalid method descriptor {descriptor!r}"
                )
            offset = terminator + 1
        elif kind in "BCDFIJSZ":
            offset += 1
        else:
            raise ClassFileError(f"invalid method descriptor {descriptor!r}")
        argument_descriptor = descriptor[start:offset]
        width = (
            2
            if not argument_descriptor.startswith("[")
            and argument_descriptor in {"J", "D"}
            else 1
        )
        result.append({
            "slot": slot,
            "descriptor": argument_descriptor,
        })
        slot += width
    if offset >= len(descriptor) or descriptor[offset] != ")":
        raise ClassFileError(f"invalid method descriptor {descriptor!r}")
    return result


def method_argument_names(method: dict[str, Any]) -> list[str | None]:
    """Resolve each method argument's debug name from its local table."""

    arguments = method_argument_slots(
        method["descriptor"],
        is_static=bool(method["access_flags"] & 0x0008),
    )
    variables = method.get("local_variables", [])
    names: list[str | None] = []
    for argument in arguments:
        candidates = [
            variable
            for variable in variables
            if variable.get("slot") == argument["slot"]
        ]
        candidates.sort(key=lambda item: (
            item.get("start_pc", 2**31),
            -item.get("length", 0),
            item.get("name", ""),
        ))
        names.append(candidates[0]["name"] if candidates else None)
    return names
