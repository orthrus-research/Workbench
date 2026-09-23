"""Exact, non-loading JVM class and archive structure inventory.

The output is Project Intelligence static evidence.  It identifies classfile
members and references from exact bytes but does not prove classpath selection,
loading, transformation, linkage, or execution.
"""

from __future__ import annotations

from io import BytesIO
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any, Iterable
from zipfile import BadZipFile, LargeZipFile, ZipFile

from .mixin_topology import (
    ArtifactScanError,
    MAX_ARCHIVE_BYTES,
    _inventory_archive,
    _read_member,
)


CLASS_FORMAT = "workbench-project-intelligence-jvm-class-surface-v1"
ARCHIVE_FORMAT = "workbench-project-intelligence-jvm-artifact-surface-v1"
MAX_CLASS_BYTES = 64 * 1024 * 1024
MAX_CLASSES = 100_000
MAX_CONSTANT_STRINGS = 4_096
MAX_REFERENCES = 65_535
MAX_LINE_NUMBERS = 65_535

_INTERNAL_NAME_RE = re.compile(r"^[^.;\\\[\]\x00]+(?:/[^.;\\\[\]\x00]+)*$")
_MULTI_RELEASE_RE = re.compile(
    r"^META-INF/versions/(?P<version>[1-9][0-9]*)/(?P<logical>.+)$"
)

_CLASS_FLAGS = {
    0x0001: "public",
    0x0010: "final",
    0x0020: "super",
    0x0200: "interface",
    0x0400: "abstract",
    0x1000: "synthetic",
    0x2000: "annotation",
    0x4000: "enum",
    0x8000: "module",
}
_FIELD_FLAGS = {
    0x0001: "public",
    0x0002: "private",
    0x0004: "protected",
    0x0008: "static",
    0x0010: "final",
    0x0040: "volatile",
    0x0080: "transient",
    0x1000: "synthetic",
    0x4000: "enum",
}
_METHOD_FLAGS = {
    0x0001: "public",
    0x0002: "private",
    0x0004: "protected",
    0x0008: "static",
    0x0010: "final",
    0x0020: "synchronized",
    0x0040: "bridge",
    0x0080: "varargs",
    0x0100: "native",
    0x0400: "abstract",
    0x0800: "strict",
    0x1000: "synthetic",
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _identified(prefix: str, material: Any) -> str:
    return prefix + hashlib.sha256(_canonical_bytes(material)).hexdigest()


class _Reader:
    def __init__(self, raw: bytes, context: str):
        self.raw = raw
        self.context = context
        self.offset = 0

    @property
    def remaining(self) -> int:
        return len(self.raw) - self.offset

    def take(self, count: int) -> bytes:
        if count < 0 or self.offset + count > len(self.raw):
            raise ArtifactScanError(f"{self.context} is truncated")
        value = self.raw[self.offset : self.offset + count]
        self.offset += count
        return value

    def u1(self) -> int:
        return int.from_bytes(self.take(1), "big")

    def u2(self) -> int:
        return int.from_bytes(self.take(2), "big")

    def u4(self) -> int:
        return int.from_bytes(self.take(4), "big")

    def child(self, count: int, context: str) -> "_Reader":
        return _Reader(self.take(count), context)

    def require_end(self) -> None:
        if self.remaining:
            raise ArtifactScanError(
                f"{self.context} has {self.remaining} unexpected trailing bytes"
            )


def _modified_utf8(raw: bytes, context: str) -> str:
    # Classfile identifiers are ordinarily ASCII.  This decoder additionally
    # handles modified-UTF-8's encoded NUL and surrogate pairs without silently
    # replacing malformed input.
    units: list[int] = []
    index = 0
    while index < len(raw):
        first = raw[index]
        if first == 0:
            raise ArtifactScanError(f"{context} contains a raw NUL in modified UTF-8")
        if first < 0x80:
            units.append(first)
            index += 1
            continue
        if first & 0xE0 == 0xC0:
            if index + 1 >= len(raw) or raw[index + 1] & 0xC0 != 0x80:
                raise ArtifactScanError(f"{context} contains malformed modified UTF-8")
            value = ((first & 0x1F) << 6) | (raw[index + 1] & 0x3F)
            if value != 0 and value < 0x80:
                raise ArtifactScanError(f"{context} contains overlong modified UTF-8")
            units.append(value)
            index += 2
            continue
        if first & 0xF0 == 0xE0:
            if (
                index + 2 >= len(raw)
                or raw[index + 1] & 0xC0 != 0x80
                or raw[index + 2] & 0xC0 != 0x80
            ):
                raise ArtifactScanError(f"{context} contains malformed modified UTF-8")
            value = (
                ((first & 0x0F) << 12)
                | ((raw[index + 1] & 0x3F) << 6)
                | (raw[index + 2] & 0x3F)
            )
            if value < 0x800:
                raise ArtifactScanError(f"{context} contains overlong modified UTF-8")
            units.append(value)
            index += 3
            continue
        raise ArtifactScanError(f"{context} contains unsupported modified UTF-8")
    characters: list[str] = []
    index = 0
    while index < len(units):
        unit = units[index]
        if 0xD800 <= unit <= 0xDBFF:
            if index + 1 >= len(units) or not 0xDC00 <= units[index + 1] <= 0xDFFF:
                raise ArtifactScanError(f"{context} contains an unpaired UTF-16 surrogate")
            codepoint = 0x10000 + ((unit - 0xD800) << 10) + (units[index + 1] - 0xDC00)
            characters.append(chr(codepoint))
            index += 2
            continue
        if 0xDC00 <= unit <= 0xDFFF:
            raise ArtifactScanError(f"{context} contains an unpaired UTF-16 surrogate")
        characters.append(chr(unit))
        index += 1
    return "".join(characters)


class _Pool:
    def __init__(self, reader: _Reader):
        count = reader.u2()
        if count < 1:
            raise ArtifactScanError(f"{reader.context} has an invalid constant pool")
        self.entries: list[tuple[int, Any] | None] = [None] * count
        index = 1
        while index < count:
            tag = reader.u1()
            if tag == 1:
                raw = reader.take(reader.u2())
                self.entries[index] = (tag, _modified_utf8(raw, reader.context))
            elif tag in {3, 4}:
                self.entries[index] = (tag, reader.take(4).hex())
            elif tag in {5, 6}:
                self.entries[index] = (tag, reader.take(8).hex())
                index += 1
            elif tag in {7, 8, 16, 19, 20}:
                self.entries[index] = (tag, reader.u2())
            elif tag in {9, 10, 11, 12, 17, 18}:
                self.entries[index] = (tag, (reader.u2(), reader.u2()))
            elif tag == 15:
                self.entries[index] = (tag, (reader.u1(), reader.u2()))
            else:
                raise ArtifactScanError(
                    f"{reader.context} has unknown constant-pool tag {tag}"
                )
            index += 1

    def entry(self, index: int, tags: int | set[int]) -> Any:
        allowed = {tags} if isinstance(tags, int) else tags
        try:
            entry = self.entries[index]
        except IndexError:
            entry = None
        if entry is None or entry[0] not in allowed:
            raise ArtifactScanError("class file has an invalid constant-pool reference")
        return entry[1]

    def utf8(self, index: int) -> str:
        return str(self.entry(index, 1))

    def internal_class(self, index: int) -> str:
        return self.utf8(int(self.entry(index, 7)))

    def class_name(self, index: int) -> str:
        value = self.internal_class(index)
        if value.startswith("["):
            return _descriptor_type(value)[0]
        if not _INTERNAL_NAME_RE.fullmatch(value):
            raise ArtifactScanError(f"class file contains an invalid internal name {value!r}")
        return value.replace("/", ".")

    def name_and_type(self, index: int) -> tuple[str, str]:
        name_index, descriptor_index = self.entry(index, 12)
        return self.utf8(name_index), self.utf8(descriptor_index)


def _flags(value: int, names: dict[int, str]) -> list[str]:
    return [name for flag, name in sorted(names.items()) if value & flag]


def _descriptor_type(value: str, offset: int = 0) -> tuple[str, int]:
    arrays = 0
    while offset < len(value) and value[offset] == "[":
        arrays += 1
        offset += 1
    if offset >= len(value):
        raise ArtifactScanError("class file contains a truncated descriptor")
    primitive = {
        "B": "byte",
        "C": "char",
        "D": "double",
        "F": "float",
        "I": "int",
        "J": "long",
        "S": "short",
        "Z": "boolean",
        "V": "void",
    }
    token = value[offset]
    if token in primitive:
        result = primitive[token]
        offset += 1
    elif token == "L":
        end = value.find(";", offset)
        if end < 0:
            raise ArtifactScanError("class file contains an unterminated object descriptor")
        internal = value[offset + 1 : end]
        if not _INTERNAL_NAME_RE.fullmatch(internal):
            raise ArtifactScanError("class file contains an invalid object descriptor")
        result = internal.replace("/", ".")
        offset = end + 1
    else:
        raise ArtifactScanError(f"class file contains an invalid descriptor token {token!r}")
    return result + "[]" * arrays, offset


def _field_type(descriptor: str) -> str:
    value, offset = _descriptor_type(descriptor)
    if offset != len(descriptor) or value == "void":
        raise ArtifactScanError(f"class file contains an invalid field descriptor {descriptor!r}")
    return value


def _method_signature(descriptor: str) -> tuple[list[str], str]:
    if not descriptor.startswith("("):
        raise ArtifactScanError(f"class file contains an invalid method descriptor {descriptor!r}")
    offset = 1
    parameters: list[str] = []
    while offset < len(descriptor) and descriptor[offset] != ")":
        parameter, offset = _descriptor_type(descriptor, offset)
        if parameter == "void":
            raise ArtifactScanError("method parameter cannot be void")
        parameters.append(parameter)
    if offset >= len(descriptor) or descriptor[offset] != ")":
        raise ArtifactScanError(f"class file contains an invalid method descriptor {descriptor!r}")
    result, offset = _descriptor_type(descriptor, offset + 1)
    if offset != len(descriptor):
        raise ArtifactScanError(f"class file contains an invalid method descriptor {descriptor!r}")
    return parameters, result


def _line_table(reader: _Reader) -> list[dict[str, int]]:
    count = reader.u2()
    if count > MAX_LINE_NUMBERS:
        raise ArtifactScanError("class method line table exceeds its JVM bound")
    rows = [
        {"bytecode_offset": reader.u2(), "source_line": reader.u2()}
        for _ in range(count)
    ]
    reader.require_end()
    return rows


def _code_attribute(reader: _Reader, pool: _Pool) -> dict[str, Any]:
    max_stack = reader.u2()
    max_locals = reader.u2()
    code_length = reader.u4()
    reader.take(code_length)
    exception_count = reader.u2()
    reader.take(exception_count * 8)
    lines: list[dict[str, int]] = []
    for _ in range(reader.u2()):
        name = pool.utf8(reader.u2())
        child = reader.child(reader.u4(), f"{reader.context} attribute {name}")
        if name == "LineNumberTable":
            if lines:
                raise ArtifactScanError(f"{reader.context} repeats LineNumberTable")
            lines = _line_table(child)
        # Unknown Code attributes are retained only by their exact parent
        # class hash; skipping them is explicit in the surface limitations.
    reader.require_end()
    return {
        "code_bytes": code_length,
        "exception_handlers": exception_count,
        "max_locals": max_locals,
        "max_stack": max_stack,
        "line_numbers": lines,
        "source_line_min": min((row["source_line"] for row in lines), default=None),
        "source_line_max": max((row["source_line"] for row in lines), default=None),
    }


def _attributes(
    reader: _Reader,
    pool: _Pool,
    *,
    allow_code: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "signature": None,
        "source_file": None,
        "exceptions": [],
        "code": None,
    }
    seen: set[str] = set()
    for _ in range(reader.u2()):
        name = pool.utf8(reader.u2())
        child = reader.child(reader.u4(), f"{reader.context} attribute {name}")
        if name in {"Signature", "SourceFile", "Exceptions", "Code"}:
            if name in seen:
                raise ArtifactScanError(f"{reader.context} repeats attribute {name}")
            seen.add(name)
        if name == "Signature":
            result["signature"] = pool.utf8(child.u2())
            child.require_end()
        elif name == "SourceFile":
            result["source_file"] = pool.utf8(child.u2())
            child.require_end()
        elif name == "Exceptions":
            result["exceptions"] = [
                pool.class_name(child.u2()) for _ in range(child.u2())
            ]
            child.require_end()
        elif name == "Code" and allow_code:
            result["code"] = _code_attribute(child, pool)
        # Every attribute payload was already bounded and consumed from the
        # parent reader. Unsupported semantic content is not reinterpreted.
    return result


def _member(reader: _Reader, pool: _Pool, owner: str, kind: str) -> dict[str, Any]:
    access = reader.u2()
    name = pool.utf8(reader.u2())
    descriptor = pool.utf8(reader.u2())
    if not name or any(character in name for character in ".;/[\x00"):
        if name not in {"<init>", "<clinit>"}:
            raise ArtifactScanError(f"class file contains an invalid {kind} name")
    attributes = _attributes(reader, pool, allow_code=kind == "method")
    if kind == "field":
        display_type = _field_type(descriptor)
        parameters: list[str] | None = None
        return_type: str | None = None
        flags = _flags(access, _FIELD_FLAGS)
        key = f"{owner}#{name}:{descriptor}"
    else:
        parameters, return_type = _method_signature(descriptor)
        display_type = None
        flags = _flags(access, _METHOD_FLAGS)
        key = f"{owner}#{name}{descriptor}"
    material = {
        "access": access,
        "class_name": owner,
        "descriptor": descriptor,
        "kind": kind,
        "name": name,
    }
    return {
        "member_id": _identified("workbench-jvm-member:sha256:", material),
        "kind": kind,
        "name": name,
        "qualified_name": f"{owner}#{name}",
        "descriptor": descriptor,
        "descriptor_key": key,
        "display_type": display_type,
        "parameter_types": parameters,
        "return_type": return_type,
        "access": access,
        "access_flags": flags,
        "signature": attributes["signature"],
        "exceptions": attributes["exceptions"],
        "code": attributes["code"],
    }


def inspect_class_bytes(raw: bytes, *, entry_path: str = "unknown.class") -> dict[str, Any]:
    """Decode one exact classfile structure without defining the class."""

    if not isinstance(raw, bytes):
        raise TypeError("class input must be bytes")
    if not raw or len(raw) > MAX_CLASS_BYTES:
        raise ArtifactScanError(
            f"class input must contain 1 through {MAX_CLASS_BYTES} bytes"
        )
    reader = _Reader(raw, f"class {entry_path}")
    if reader.take(4) != b"\xca\xfe\xba\xbe":
        raise ArtifactScanError(f"class {entry_path} has invalid magic")
    minor = reader.u2()
    major = reader.u2()
    pool = _Pool(reader)
    access = reader.u2()
    class_name = pool.class_name(reader.u2())
    super_index = reader.u2()
    super_class = None if super_index == 0 else pool.class_name(super_index)
    interfaces = [pool.class_name(reader.u2()) for _ in range(reader.u2())]
    fields = [_member(reader, pool, class_name, "field") for _ in range(reader.u2())]
    methods = [_member(reader, pool, class_name, "method") for _ in range(reader.u2())]
    attributes = _attributes(reader, pool, allow_code=False)
    reader.require_end()

    class_references: set[str] = set()
    member_references: dict[tuple[str, str, str, str], dict[str, str]] = {}
    constants: set[str] = set()
    for pool_index, entry in enumerate(pool.entries[1:], 1):
        if entry is None:
            continue
        tag, payload = entry
        if tag == 7:
            class_references.add(pool.class_name(pool_index))
        elif tag in {9, 10, 11}:
            class_index, name_type_index = payload
            owner = pool.class_name(class_index)
            name, descriptor = pool.name_and_type(name_type_index)
            reference_kind = "field" if tag == 9 else "interface-method" if tag == 11 else "method"
            member_references[(reference_kind, owner, name, descriptor)] = {
                "kind": reference_kind,
                "owner": owner,
                "name": name,
                "descriptor": descriptor,
            }
        elif tag == 8:
            value = pool.utf8(int(payload))
            if value and len(value) <= 8192 and not any(character in value for character in "\r\n\x00"):
                constants.add(value)
    class_references.discard(class_name)
    if len(class_references) > MAX_REFERENCES or len(member_references) > MAX_REFERENCES:
        raise ArtifactScanError("class constant-pool references exceed JVM bounds")
    constant_strings = sorted(constants)[:MAX_CONSTANT_STRINGS]
    constant_strings_truncated = len(constants) > len(constant_strings)
    digest = hashlib.sha256(raw).hexdigest()
    material = {
        "class_name": class_name,
        "entry_path": entry_path,
        "sha256": digest,
    }
    return {
        "format": CLASS_FORMAT,
        "schema_version": 1,
        "class_id": _identified("workbench-jvm-class:sha256:", material),
        "class_name": class_name,
        "entry_path": entry_path,
        "sha256": digest,
        "bytes": len(raw),
        "classfile_version": {"major": major, "minor": minor},
        "access": access,
        "access_flags": _flags(access, _CLASS_FLAGS),
        "super_class": super_class,
        "interfaces": sorted(interfaces),
        "signature": attributes["signature"],
        "source_file": attributes["source_file"],
        "fields": fields,
        "methods": methods,
        "class_references": sorted(class_references),
        "member_references": [
            member_references[key] for key in sorted(member_references)
        ],
        "constant_strings": constant_strings,
        "constant_strings_truncated": constant_strings_truncated,
        "limitations": [
            "Classfile structure is static exact-byte evidence; it does not prove classpath selection, loading, transformation, linkage, or execution.",
            "Unsupported attributes and bytecode instructions remain bound by the class SHA-256 but are not semantically decoded in V1.",
        ],
    }


def scan_jvm_archive_bytes(
    data: bytes,
    *,
    label: str,
    max_classes: int = MAX_CLASSES,
) -> dict[str, Any]:
    """Inventory exact class structures and resources in one JAR/ZIP."""

    if not isinstance(data, bytes):
        raise TypeError("archive input must be bytes")
    if not isinstance(label, str) or not label or any(char in label for char in "\r\n\x00"):
        raise ArtifactScanError("archive label must be non-empty single-line text")
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ArtifactScanError(f"archive exceeds {MAX_ARCHIVE_BYTES} bytes: {label}")
    if isinstance(max_classes, bool) or not isinstance(max_classes, int) or not 1 <= max_classes <= MAX_CLASSES:
        raise ArtifactScanError(f"max_classes must be from 1 through {MAX_CLASSES}")
    archive_sha256 = hashlib.sha256(data).hexdigest()
    try:
        with ZipFile(BytesIO(data), "r", allowZip64=True) as archive:
            members, inventory, uncompressed = _inventory_archive(archive)
            hashes = {row["path"]: row["sha256"] for row in inventory}
            class_paths = sorted(
                path
                for path, info in members.items()
                if path.endswith(".class") and not info.is_dir()
            )
            truncated = len(class_paths) > max_classes
            classes = [
                inspect_class_bytes(
                    _read_member(archive, members[path], f"class {path}"),
                    entry_path=path,
                )
                for path in class_paths[:max_classes]
            ]
    except (BadZipFile, LargeZipFile, OSError, EOFError) as exc:
        raise ArtifactScanError(f"{label} is not a valid ZIP archive: {exc}") from exc
    resources = [
        row for row in inventory if not str(row["path"]).endswith(".class")
    ]
    for row in classes:
        if hashes.get(row["entry_path"]) != row["sha256"]:
            raise ArtifactScanError(
                f"class inventory hash differs from archive member: {row['entry_path']}"
            )
        match = _MULTI_RELEASE_RE.fullmatch(str(row["entry_path"]))
        logical = match.group("logical") if match else row["entry_path"]
        expected = row["class_name"].replace(".", "/") + ".class"
        if logical != expected and row["class_name"] not in {"module-info", "package-info"}:
            raise ArtifactScanError(
                f"class {row['class_name']} is stored at non-matching path {row['entry_path']}"
            )
        row["multi_release_version"] = int(match.group("version")) if match else None
    material = {
        "archive_sha256": archive_sha256,
        "class_ids": [row["class_id"] for row in classes],
        "resource_inventory": resources,
    }
    return {
        "format": ARCHIVE_FORMAT,
        "schema_version": 1,
        "surface_id": _identified("workbench-jvm-artifact-surface:sha256:", material),
        "label": label,
        "artifact": {
            "sha256": archive_sha256,
            "bytes": len(data),
            "uncompressed_bytes": uncompressed,
            "member_count": len(inventory),
        },
        "coverage": {
            "complete": not truncated,
            "class_entries": len(class_paths),
            "scanned_classes": len(classes),
            "resource_entries": len(resources),
        },
        "classes": classes,
        "resources": resources,
        "limitations": [
            "Archive inventory is static exact-byte evidence; it does not prove runtime classpath selection or defining-loader identity.",
            "V1 exposes class headers, members, references, source-file names, and line tables; it does not decompile instructions.",
        ],
    }
