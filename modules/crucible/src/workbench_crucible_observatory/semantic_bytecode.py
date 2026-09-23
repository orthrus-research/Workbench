"""Narrow semantic fingerprints for Foundation class-dump comparisons.

Exact Foundation bytes remain the custody identity.  This module derives a
second, explicitly versioned comparison identity by replacing only a UUID
``CONSTANT_Utf8`` which is referenced exclusively by the ``sessionId`` element
of method-level ``MixinMerged`` runtime-visible annotations.  Any uncertainty
about the class-file structure or constant-pool sharing is a hard failure.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
from typing import Any, Mapping, Sequence

from .bundle import CaptureValidationError, canonical_json_bytes
from .cleanroom_runtime_custody import (
    FOUNDATION_CLASS_DUMP_MANIFEST_FORMAT,
    build_foundation_class_dump_manifest,
)


SEMANTIC_BYTECODE_POLICY_FORMAT = "workbench-semantic-bytecode-policy-v1"
SEMANTIC_BYTECODE_MANIFEST_FORMAT = (
    "workbench-foundation-semantic-bytecode-manifest-v1"
)
SEMANTIC_BYTECODE_VIEW_FORMAT = "workbench-foundation-semantic-bytecode-view-v1"
SEMANTIC_BYTECODE_COMPARISON_FORMAT = (
    "workbench-foundation-semantic-bytecode-comparison-v1"
)

_MIXIN_MERGED_DESCRIPTOR = (
    "Lorg/spongepowered/asm/mixin/transformer/meta/MixinMerged;"
)
_SESSION_ELEMENT = "sessionId"
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_CANONICAL_SESSION_UUID = "00000000-0000-0000-0000-000000000000"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_CLASS_FILE_BYTES = 64 * 1024 * 1024
_READ_CHUNK_BYTES = 1024 * 1024
_MIN_CLASS_MAJOR = 45
_MAX_CLASS_MAJOR = 69  # Java 25; policy v1 intentionally has a fixed parser bound.

_SEMANTIC_BYTECODE_POLICY_MATERIAL: Mapping[str, Any] = {
        "format": SEMANTIC_BYTECODE_POLICY_FORMAT,
        "class_file_major_versions": {
            "minimum": _MIN_CLASS_MAJOR,
            "maximum": _MAX_CLASS_MAJOR,
        },
        "input_identity": "exact-foundation-final-class-bytes-sha256",
        "rewrite": {
            "constant_pool_tag": "CONSTANT_Utf8",
            "annotation_attribute": "RuntimeVisibleAnnotations",
            "annotation_owner": "method",
            "annotation_descriptor": _MIXIN_MERGED_DESCRIPTOR,
            "element_name": _SESSION_ELEMENT,
            "accepted_value": "lowercase-hyphenated-uuid",
            "replacement": _CANONICAL_SESSION_UUID,
            "length_preserving": True,
        },
        "safety": {
            "parse_complete_class_file": True,
            "require_exact_target_element_shape": True,
            "require_all_references_to_rewritten_utf8_are_target_elements": True,
            "reject_unknown_attributes_when_rewriting": True,
            "reject_malformed_ambiguous_or_unsafe_sharing": True,
            "preserve_all_other_bytes": True,
        },
        "authority": {
            "comparison_only": True,
            "may_replace_exact_custody_hash": False,
            "may_authorize_actor_binding": False,
        },
    }


def semantic_bytecode_policy_material() -> dict[str, Any]:
    """Return a detached canonical copy of policy V1's identity material."""

    return json.loads(canonical_json_bytes(_SEMANTIC_BYTECODE_POLICY_MATERIAL))


SEMANTIC_BYTECODE_POLICY_SHA256 = hashlib.sha256(
    canonical_json_bytes(_SEMANTIC_BYTECODE_POLICY_MATERIAL)
).hexdigest()
SEMANTIC_BYTECODE_POLICY_ID = (
    "workbench-semantic-bytecode-policy:sha256:"
    + SEMANTIC_BYTECODE_POLICY_SHA256
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CaptureValidationError(message)


def _sha256(value: Any, context: str) -> str:
    _require(
        isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None,
        f"{context} must be a lowercase SHA-256",
    )
    return value


class _Reader:
    __slots__ = ("data", "source", "offset")

    def __init__(self, data: bytes, source: str) -> None:
        self.data = data
        self.source = source
        self.offset = 0

    def remaining(self) -> int:
        return len(self.data) - self.offset

    def take(self, size: int) -> bytes:
        _require(size >= 0, f"negative read size in {self.source}")
        end = self.offset + size
        _require(end <= len(self.data), f"truncated class structure in {self.source}")
        result = self.data[self.offset:end]
        self.offset = end
        return result

    def u1(self) -> int:
        return self.take(1)[0]

    def u2(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def s2(self) -> int:
        return struct.unpack(">h", self.take(2))[0]

    def u4(self) -> int:
        return struct.unpack(">I", self.take(4))[0]

    def s4(self) -> int:
        return struct.unpack(">i", self.take(4))[0]


def _decode_modified_utf8(raw: bytes, context: str) -> str:
    """Strictly decode the JVM's modified UTF-8 form."""

    units: list[int] = []
    offset = 0
    while offset < len(raw):
        first = raw[offset]
        if 0x01 <= first <= 0x7F:
            units.append(first)
            offset += 1
            continue
        if 0xC0 <= first <= 0xDF:
            _require(offset + 1 < len(raw), f"truncated modified UTF-8 in {context}")
            second = raw[offset + 1]
            _require(
                0x80 <= second <= 0xBF,
                f"invalid modified UTF-8 continuation in {context}",
            )
            value = ((first & 0x1F) << 6) | (second & 0x3F)
            _require(
                value == 0 or value >= 0x80,
                f"overlong modified UTF-8 in {context}",
            )
            _require(
                value != 0 or (first == 0xC0 and second == 0x80),
                f"invalid modified UTF-8 null in {context}",
            )
            units.append(value)
            offset += 2
            continue
        if 0xE0 <= first <= 0xEF:
            _require(offset + 2 < len(raw), f"truncated modified UTF-8 in {context}")
            second, third = raw[offset + 1], raw[offset + 2]
            _require(
                0x80 <= second <= 0xBF and 0x80 <= third <= 0xBF,
                f"invalid modified UTF-8 continuation in {context}",
            )
            value = (
                ((first & 0x0F) << 12)
                | ((second & 0x3F) << 6)
                | (third & 0x3F)
            )
            _require(value >= 0x800, f"overlong modified UTF-8 in {context}")
            units.append(value)
            offset += 3
            continue
        raise CaptureValidationError(f"invalid modified UTF-8 lead byte in {context}")

    encoded = b"".join(struct.pack(">H", unit) for unit in units)
    try:
        return encoded.decode("utf-16-be", errors="surrogatepass")
    except UnicodeDecodeError as exc:  # defensive; every collected unit is a u2
        raise CaptureValidationError(f"invalid modified UTF-8 in {context}: {exc}") from exc


@dataclass(frozen=True, slots=True)
class _CpEntry:
    tag: int
    values: tuple[int, ...] = ()
    text: str | None = None
    raw: bytes | None = None
    data_offset: int | None = None


@dataclass(frozen=True, slots=True)
class _CpReference:
    site: str
    authorized_session_element: bool


@dataclass(frozen=True, slots=True)
class _ParsedClass:
    semantic_bytes: bytes
    normalized_utf8_constants: int
    normalized_annotation_references: int


class _ClassParser:
    """A bounded structural parser with exact constant-pool reference custody."""

    def __init__(self, data: bytes, source: str, expected_internal_name: str) -> None:
        _require(0 < len(data) <= _MAX_CLASS_FILE_BYTES, f"class size out of bounds: {source}")
        self.data = data
        self.source = source
        self.expected_internal_name = expected_internal_name
        self.reader = _Reader(data, source)
        self.pool: list[_CpEntry | None] = []
        self.references: dict[int, list[_CpReference]] = {}
        self.target_value_indices: set[int] = set()
        self.target_reference_count = 0
        self.unknown_attributes: list[str] = []
        self.bootstrap_method_count: int | None = None
        self.dynamic_bootstrap_indices: list[int] = []
        self.major = 0

    def parse(self) -> _ParsedClass:
        _require(self.reader.u4() == 0xCAFEBABE, f"invalid class magic: {self.source}")
        minor = self.reader.u2()
        self.major = self.reader.u2()
        _require(
            _MIN_CLASS_MAJOR <= self.major <= _MAX_CLASS_MAJOR,
            f"unsupported class-file version {self.major}.{minor}: {self.source}",
        )
        if self.major >= 56:
            _require(
                minor in {0, 0xFFFF},
                f"invalid class-file minor version {minor}: {self.source}",
            )
        self._parse_constant_pool()
        self._validate_constant_pool()

        self.reader.u2()  # access_flags
        this_index = self._ref(self.reader.u2(), {7}, "class.this_class")
        super_index = self.reader.u2()
        if super_index:
            self._ref(super_index, {7}, "class.super_class")
        this_entry = self._entry(this_index, {7}, "class.this_class")
        this_name = self._utf8(this_entry.values[0], "class.this_class.name")
        _require(
            this_name == self.expected_internal_name,
            f"class this_class {this_name!r} does not match dump path "
            f"{self.expected_internal_name!r}: {self.source}",
        )

        for index in range(self.reader.u2()):
            self._ref(self.reader.u2(), {7}, f"class.interface[{index}]")
        self._parse_members("field")
        self._parse_members("method")
        self._parse_attributes(self.reader, "class", "class")
        _require(self.reader.remaining() == 0, f"trailing class bytes: {self.source}")

        if self.dynamic_bootstrap_indices:
            _require(
                self.bootstrap_method_count is not None,
                f"dynamic constant without BootstrapMethods: {self.source}",
            )
            for index in self.dynamic_bootstrap_indices:
                _require(
                    index < self.bootstrap_method_count,
                    f"dynamic constant has invalid bootstrap index: {self.source}",
                )

        if self.target_value_indices:
            _require(
                not self.unknown_attributes,
                "cannot normalize a class with unknown attributes: "
                + self.source
                + " ("
                + ", ".join(sorted(set(self.unknown_attributes)))
                + ")",
            )
            for index in sorted(self.target_value_indices):
                refs = self.references.get(index, [])
                _require(bool(refs), f"unreferenced target session constant: {self.source}")
                unsafe = [ref.site for ref in refs if not ref.authorized_session_element]
                _require(
                    not unsafe,
                    "MixinMerged.sessionId UTF-8 constant has an unsafe shared "
                    f"reference in {self.source}: {', '.join(unsafe)}",
                )

        normalized = bytearray(self.data)
        replacement = _CANONICAL_SESSION_UUID.encode("ascii")
        for index in sorted(self.target_value_indices):
            entry = self._entry(index, {1}, "normalized session constant")
            _require(
                entry.raw is not None
                and entry.data_offset is not None
                and len(entry.raw) == len(replacement),
                f"session normalization is not length preserving: {self.source}",
            )
            start = entry.data_offset
            normalized[start : start + len(entry.raw)] = replacement
        return _ParsedClass(
            semantic_bytes=bytes(normalized),
            normalized_utf8_constants=len(self.target_value_indices),
            normalized_annotation_references=self.target_reference_count,
        )

    def _parse_constant_pool(self) -> None:
        count = self.reader.u2()
        _require(count > 1, f"invalid constant_pool_count: {self.source}")
        self.pool = [None] * count
        index = 1
        while index < count:
            tag = self.reader.u1()
            if tag == 1:
                size = self.reader.u2()
                data_offset = self.reader.offset
                raw = self.reader.take(size)
                text = _decode_modified_utf8(raw, f"constant_pool[{index}] in {self.source}")
                self.pool[index] = _CpEntry(
                    tag=tag,
                    text=text,
                    raw=raw,
                    data_offset=data_offset,
                )
            elif tag in {3, 4}:
                self.reader.take(4)
                self.pool[index] = _CpEntry(tag=tag)
            elif tag in {5, 6}:
                self.reader.take(8)
                self.pool[index] = _CpEntry(tag=tag)
                _require(index + 1 < count, f"invalid wide constant: {self.source}")
                index += 1
                self.pool[index] = _CpEntry(tag=0)  # unusable second slot
            elif tag in {7, 8, 16, 19, 20}:
                self.pool[index] = _CpEntry(tag=tag, values=(self.reader.u2(),))
            elif tag in {9, 10, 11, 12, 17, 18}:
                self.pool[index] = _CpEntry(
                    tag=tag,
                    values=(self.reader.u2(), self.reader.u2()),
                )
            elif tag == 15:
                self.pool[index] = _CpEntry(
                    tag=tag,
                    values=(self.reader.u1(), self.reader.u2()),
                )
            else:
                raise CaptureValidationError(
                    f"unsupported constant-pool tag {tag}: {self.source}"
                )
            index += 1

    def _validate_constant_pool(self) -> None:
        for index in range(1, len(self.pool)):
            entry = self.pool[index]
            _require(entry is not None, f"missing constant-pool entry: {self.source}")
            tag = entry.tag
            site = f"constant_pool[{index}]"
            if tag in {0, 1, 3, 4, 5, 6}:
                continue
            if tag == 7:
                self._ref(entry.values[0], {1}, site + ".name_index")
            elif tag == 8:
                self._ref(entry.values[0], {1}, site + ".string_index")
            elif tag in {9, 10, 11}:
                self._ref(entry.values[0], {7}, site + ".class_index")
                self._ref(entry.values[1], {12}, site + ".name_and_type_index")
            elif tag == 12:
                self._ref(entry.values[0], {1}, site + ".name_index")
                self._ref(entry.values[1], {1}, site + ".descriptor_index")
            elif tag == 15:
                _require(self.major >= 51, f"MethodHandle before Java 7: {self.source}")
                kind, target = entry.values
                _require(1 <= kind <= 9, f"invalid MethodHandle kind: {self.source}")
                if kind <= 4:
                    allowed = {9}
                elif kind in {5, 8}:
                    allowed = {10}
                elif kind in {6, 7}:
                    allowed = {10, 11} if self.major >= 52 else {10}
                else:
                    allowed = {11}
                self._ref(target, allowed, site + ".reference_index")
            elif tag == 16:
                _require(self.major >= 51, f"MethodType before Java 7: {self.source}")
                self._ref(entry.values[0], {1}, site + ".descriptor_index")
            elif tag in {17, 18}:
                minimum = 55 if tag == 17 else 51
                _require(
                    self.major >= minimum,
                    f"dynamic constant before supported version: {self.source}",
                )
                self.dynamic_bootstrap_indices.append(entry.values[0])
                self._ref(entry.values[1], {12}, site + ".name_and_type_index")
            elif tag in {19, 20}:
                _require(self.major >= 53, f"module constant before Java 9: {self.source}")
                self._ref(entry.values[0], {1}, site + ".name_index")

    def _entry(self, index: int, allowed: set[int], context: str) -> _CpEntry:
        _require(
            0 < index < len(self.pool),
            f"invalid constant-pool index at {context}: {self.source}",
        )
        entry = self.pool[index]
        _require(
            entry is not None and entry.tag in allowed,
            f"wrong constant-pool tag at {context}: {self.source}",
        )
        return entry

    def _ref(
        self,
        index: int,
        allowed: set[int],
        site: str,
        *,
        authorized_session_element: bool = False,
    ) -> int:
        self._entry(index, allowed, site)
        self.references.setdefault(index, []).append(
            _CpReference(site, authorized_session_element)
        )
        return index

    def _optional_ref(self, index: int, allowed: set[int], site: str) -> None:
        if index:
            self._ref(index, allowed, site)

    def _utf8(self, index: int, site: str) -> str:
        self._ref(index, {1}, site)
        value = self._entry(index, {1}, site).text
        assert value is not None
        return value

    def _parse_members(self, kind: str) -> None:
        for index in range(self.reader.u2()):
            context = f"{kind}[{index}]"
            self.reader.u2()  # access_flags
            self._utf8(self.reader.u2(), context + ".name_index")
            self._utf8(self.reader.u2(), context + ".descriptor_index")
            self._parse_attributes(self.reader, kind, context)

    def _parse_attributes(
        self,
        reader: _Reader,
        owner: str,
        context: str,
        *,
        code_boundaries: frozenset[int] | None = None,
        code_length: int | None = None,
    ) -> None:
        seen: set[str] = set()
        for index in range(reader.u2()):
            attr_context = f"{context}.attribute[{index}]"
            name = self._utf8(reader.u2(), attr_context + ".name_index")
            size = reader.u4()
            payload = reader.take(size)
            body = _Reader(payload, f"{attr_context} {name} in {self.source}")
            self._parse_attribute(
                body,
                name,
                owner,
                attr_context,
                code_boundaries=code_boundaries,
                code_length=code_length,
            )
            _require(
                body.remaining() == 0,
                f"trailing bytes in {name} at {attr_context}: {self.source}",
            )
            # Standard attributes parsed here are singletons at a declaration.
            if name not in {"LineNumberTable", "LocalVariableTable", "LocalVariableTypeTable"}:
                _require(name not in seen, f"duplicate {name} at {context}: {self.source}")
            seen.add(name)

    def _parse_attribute(
        self,
        reader: _Reader,
        name: str,
        owner: str,
        context: str,
        *,
        code_boundaries: frozenset[int] | None,
        code_length: int | None,
    ) -> None:
        if name == "ConstantValue":
            _require(owner == "field", f"ConstantValue on {owner}: {self.source}")
            self._ref(reader.u2(), {3, 4, 5, 6, 8}, context + ".constantvalue_index")
        elif name == "Code":
            _require(owner == "method", f"Code on {owner}: {self.source}")
            self._parse_code(reader, context)
        elif name == "StackMapTable":
            _require(owner == "code", f"StackMapTable on {owner}: {self.source}")
            self._parse_stack_map(reader, context, code_length)
        elif name == "Exceptions":
            _require(owner == "method", f"Exceptions on {owner}: {self.source}")
            for index in range(reader.u2()):
                self._ref(reader.u2(), {7}, f"{context}.exception[{index}]")
        elif name == "InnerClasses":
            _require(owner == "class", f"InnerClasses on {owner}: {self.source}")
            for index in range(reader.u2()):
                row = f"{context}.class[{index}]"
                self._optional_ref(reader.u2(), {7}, row + ".inner_class")
                self._optional_ref(reader.u2(), {7}, row + ".outer_class")
                self._optional_ref(reader.u2(), {1}, row + ".inner_name")
                reader.u2()
        elif name == "EnclosingMethod":
            _require(owner == "class", f"EnclosingMethod on {owner}: {self.source}")
            self._ref(reader.u2(), {7}, context + ".class_index")
            self._optional_ref(reader.u2(), {12}, context + ".method_index")
        elif name in {"Synthetic", "Deprecated"}:
            _require(reader.remaining() == 0, f"nonempty {name}: {self.source}")
        elif name == "Signature":
            self._ref(reader.u2(), {1}, context + ".signature_index")
        elif name == "SourceFile":
            _require(owner == "class", f"SourceFile on {owner}: {self.source}")
            self._ref(reader.u2(), {1}, context + ".sourcefile_index")
        elif name == "SourceDebugExtension":
            _require(owner == "class", f"SourceDebugExtension on {owner}: {self.source}")
            reader.take(reader.remaining())  # opaque bytes; the JVMS declares no CP refs
        elif name == "LineNumberTable":
            _require(owner == "code", f"LineNumberTable on {owner}: {self.source}")
            for _ in range(reader.u2()):
                start = reader.u2()
                reader.u2()
                self._code_offset(
                    start,
                    code_boundaries,
                    code_length,
                    False,
                    name,
                )
        elif name in {"LocalVariableTable", "LocalVariableTypeTable"}:
            _require(owner == "code", f"{name} on {owner}: {self.source}")
            for index in range(reader.u2()):
                row = f"{context}.local[{index}]"
                start, length = reader.u2(), reader.u2()
                self._code_range(start, length, code_boundaries, code_length, name)
                self._ref(reader.u2(), {1}, row + ".name_index")
                suffix = "signature_index" if name.endswith("TypeTable") else "descriptor_index"
                self._ref(reader.u2(), {1}, row + "." + suffix)
                reader.u2()
        elif name in {"RuntimeVisibleAnnotations", "RuntimeInvisibleAnnotations"}:
            allow_target = owner == "method" and name == "RuntimeVisibleAnnotations"
            self._parse_annotations(reader, context, allow_target=allow_target)
        elif name in {
            "RuntimeVisibleParameterAnnotations",
            "RuntimeInvisibleParameterAnnotations",
        }:
            _require(owner == "method", f"{name} on {owner}: {self.source}")
            for parameter in range(reader.u1()):
                self._parse_annotations(
                    reader,
                    f"{context}.parameter[{parameter}]",
                    allow_target=False,
                )
        elif name in {
            "RuntimeVisibleTypeAnnotations",
            "RuntimeInvisibleTypeAnnotations",
        }:
            self._parse_type_annotations(reader, context)
        elif name == "AnnotationDefault":
            _require(owner == "method", f"AnnotationDefault on {owner}: {self.source}")
            self._parse_element_value(reader, context + ".default")
        elif name == "BootstrapMethods":
            _require(owner == "class", f"BootstrapMethods on {owner}: {self.source}")
            _require(
                self.bootstrap_method_count is None,
                f"duplicate BootstrapMethods: {self.source}",
            )
            count = reader.u2()
            self.bootstrap_method_count = count
            loadable = {3, 4, 5, 6, 7, 8, 15, 16, 17}
            for index in range(count):
                row = f"{context}.bootstrap[{index}]"
                self._ref(reader.u2(), {15}, row + ".method_ref")
                for argument in range(reader.u2()):
                    self._ref(reader.u2(), loadable, f"{row}.argument[{argument}]")
        elif name == "MethodParameters":
            _require(owner == "method", f"MethodParameters on {owner}: {self.source}")
            for index in range(reader.u1()):
                self._optional_ref(reader.u2(), {1}, f"{context}.parameter[{index}].name")
                reader.u2()
        elif name == "NestHost":
            _require(owner == "class", f"NestHost on {owner}: {self.source}")
            self._ref(reader.u2(), {7}, context + ".host_class")
        elif name in {"NestMembers", "PermittedSubclasses"}:
            _require(owner == "class", f"{name} on {owner}: {self.source}")
            for index in range(reader.u2()):
                self._ref(reader.u2(), {7}, f"{context}.class[{index}]")
        elif name == "Record":
            _require(owner == "class", f"Record on {owner}: {self.source}")
            for index in range(reader.u2()):
                row = f"{context}.component[{index}]"
                self._ref(reader.u2(), {1}, row + ".name_index")
                self._ref(reader.u2(), {1}, row + ".descriptor_index")
                self._parse_attributes(reader, "record_component", row)
        elif name == "Module":
            self._parse_module(reader, owner, context)
        elif name == "ModulePackages":
            _require(owner == "class", f"ModulePackages on {owner}: {self.source}")
            for index in range(reader.u2()):
                self._ref(reader.u2(), {20}, f"{context}.package[{index}]")
        elif name == "ModuleMainClass":
            _require(owner == "class", f"ModuleMainClass on {owner}: {self.source}")
            self._ref(reader.u2(), {7}, context + ".main_class")
        else:
            self.unknown_attributes.append(f"{owner}:{name}")
            reader.take(reader.remaining())

    def _parse_code(self, reader: _Reader, context: str) -> None:
        reader.u2()  # max_stack
        reader.u2()  # max_locals
        code_length = reader.u4()
        _require(0 < code_length < 65536, f"invalid code_length: {self.source}")
        code = reader.take(code_length)
        boundaries = self._parse_bytecode(code, context)
        for index in range(reader.u2()):
            row = f"{context}.exception_table[{index}]"
            start, end, handler, catch = (
                reader.u2(),
                reader.u2(),
                reader.u2(),
                reader.u2(),
            )
            _require(start < end, f"empty exception range at {row}: {self.source}")
            self._code_offset(
                start, boundaries, code_length, False, row + ".start"
            )
            self._code_offset(end, boundaries, code_length, True, row + ".end")
            self._code_offset(
                handler, boundaries, code_length, False, row + ".handler"
            )
            self._optional_ref(catch, {7}, row + ".catch_type")
        self._parse_attributes(
            reader,
            "code",
            context + ".code",
            code_boundaries=boundaries,
            code_length=code_length,
        )

    def _parse_bytecode(self, code: bytes, context: str) -> frozenset[int]:
        reader = _Reader(code, f"bytecode {context} in {self.source}")
        starts: set[int] = set()
        branches: list[tuple[int, int]] = []
        while reader.remaining():
            start = reader.offset
            starts.add(start)
            opcode = reader.u1()
            site = f"{context}.code[{start}]"
            if opcode in {0x10, *range(0x15, 0x1A), *range(0x36, 0x3B), 0xA9}:
                reader.u1()
            elif opcode == 0x11:
                reader.u2()
            elif opcode == 0x12:
                index = reader.u1()
                self._ref(index, {3, 4, 7, 8, 15, 16, 17}, site + ".cp")
            elif opcode in {0x13, 0x14}:
                allowed = {3, 4, 7, 8, 15, 16, 17} if opcode == 0x13 else {5, 6, 17}
                self._ref(reader.u2(), allowed, site + ".cp")
            elif opcode == 0x84:
                reader.u1()
                reader.u1()
            elif 0x99 <= opcode <= 0xA8 or opcode in {0xC6, 0xC7}:
                branches.append((start, start + reader.s2()))
            elif opcode == 0xAA:
                padding = (4 - (reader.offset % 4)) % 4
                _require(
                    reader.take(padding) == b"\0" * padding,
                    f"nonzero tableswitch padding at {site}: {self.source}",
                )
                branches.append((start, start + reader.s4()))
                low, high = reader.s4(), reader.s4()
                _require(high >= low, f"invalid tableswitch range at {site}: {self.source}")
                count = high - low + 1
                _require(
                    count <= reader.remaining() // 4,
                    f"truncated tableswitch at {site}: {self.source}",
                )
                for _ in range(count):
                    branches.append((start, start + reader.s4()))
            elif opcode == 0xAB:
                padding = (4 - (reader.offset % 4)) % 4
                _require(
                    reader.take(padding) == b"\0" * padding,
                    f"nonzero lookupswitch padding at {site}: {self.source}",
                )
                branches.append((start, start + reader.s4()))
                count = reader.s4()
                _require(
                    count >= 0 and count <= reader.remaining() // 8,
                    f"invalid lookupswitch at {site}: {self.source}",
                )
                previous: int | None = None
                for _ in range(count):
                    match, target = reader.s4(), reader.s4()
                    _require(
                        previous is None or match > previous,
                        f"unordered lookupswitch at {site}: {self.source}",
                    )
                    previous = match
                    branches.append((start, start + target))
            elif 0xB2 <= opcode <= 0xB8:
                if opcode <= 0xB5:
                    allowed = {9}
                elif opcode == 0xB6:
                    allowed = {10}
                else:
                    allowed = {10, 11} if self.major >= 52 else {10}
                self._ref(reader.u2(), allowed, site + ".cp")
            elif opcode == 0xB9:
                self._ref(reader.u2(), {11}, site + ".cp")
                _require(
                    reader.u1() > 0 and reader.u1() == 0,
                    f"invalid invokeinterface at {site}: {self.source}",
                )
            elif opcode == 0xBA:
                self._ref(reader.u2(), {18}, site + ".cp")
                _require(reader.u2() == 0, f"invalid invokedynamic at {site}: {self.source}")
            elif opcode in {0xBB, 0xBD, 0xC0, 0xC1}:
                self._ref(reader.u2(), {7}, site + ".cp")
            elif opcode == 0xBC:
                _require(
                    4 <= reader.u1() <= 11,
                    f"invalid newarray type at {site}: {self.source}",
                )
            elif opcode == 0xC4:
                modified = reader.u1()
                if modified == 0x84:
                    reader.u2()
                    reader.u2()
                else:
                    _require(
                        modified in {*range(0x15, 0x1A), *range(0x36, 0x3B), 0xA9},
                        f"invalid wide opcode at {site}: {self.source}",
                    )
                    reader.u2()
            elif opcode == 0xC5:
                self._ref(reader.u2(), {7}, site + ".cp")
                _require(
                    reader.u1() > 0,
                    f"invalid multianewarray dimensions at {site}: {self.source}",
                )
            elif opcode in {0xC8, 0xC9}:
                branches.append((start, start + reader.s4()))
            elif opcode in {0xCA, 0xFE, 0xFF} or 0xCB <= opcode <= 0xFD:
                raise CaptureValidationError(
                    f"reserved bytecode opcode 0x{opcode:02x} "
                    f"at {site}: {self.source}"
                )
            else:
                _require(
                    opcode <= 0xC3,
                    f"unsupported bytecode opcode 0x{opcode:02x} at {site}: {self.source}",
                )
                # All remaining defined opcodes are operand-free.
        for origin, target in branches:
            _require(
                target in starts,
                f"branch from {origin} does not target an instruction boundary: {self.source}",
            )
        return frozenset(starts)

    def _code_offset(
        self,
        offset: int,
        boundaries: frozenset[int] | None,
        code_length: int | None,
        allow_end: bool,
        context: str,
    ) -> None:
        _require(
            boundaries is not None and code_length is not None,
            f"missing code boundaries for {context}: {self.source}",
        )
        valid = offset in boundaries or (allow_end and offset == code_length)
        _require(valid, f"invalid code offset for {context}: {self.source}")

    def _code_range(
        self,
        start: int,
        length: int,
        boundaries: frozenset[int] | None,
        code_length: int | None,
        context: str,
    ) -> None:
        _require(code_length is not None, f"missing code length for {context}: {self.source}")
        self._code_offset(
            start, boundaries, code_length, False, context + ".start"
        )
        end = start + length
        _require(end <= code_length, f"code range exceeds method in {context}: {self.source}")
        if end != code_length:
            self._code_offset(
                end, boundaries, code_length, False, context + ".end"
            )

    def _parse_stack_map(self, reader: _Reader, context: str, code_length: int | None) -> None:
        _require(code_length is not None, f"StackMapTable lacks code context: {self.source}")
        for index in range(reader.u2()):
            frame = reader.u1()
            row = f"{context}.frame[{index}]"
            if frame <= 63:
                continue
            if frame <= 127:
                self._parse_verification_type(reader, row + ".stack[0]", code_length)
            elif frame == 247:
                reader.u2()
                self._parse_verification_type(reader, row + ".stack[0]", code_length)
            elif 248 <= frame <= 251:
                reader.u2()
            elif 252 <= frame <= 254:
                reader.u2()
                for local in range(frame - 251):
                    self._parse_verification_type(reader, f"{row}.local[{local}]", code_length)
            elif frame == 255:
                reader.u2()
                for local in range(reader.u2()):
                    self._parse_verification_type(reader, f"{row}.local[{local}]", code_length)
                for stack in range(reader.u2()):
                    self._parse_verification_type(reader, f"{row}.stack[{stack}]", code_length)
            else:
                raise CaptureValidationError(f"invalid StackMap frame type {frame}: {self.source}")

    def _parse_verification_type(self, reader: _Reader, context: str, code_length: int) -> None:
        tag = reader.u1()
        if tag <= 6:
            return
        if tag == 7:
            self._ref(reader.u2(), {7}, context + ".class")
            return
        if tag == 8:
            _require(reader.u2() < code_length, f"invalid uninitialized offset: {self.source}")
            return
        raise CaptureValidationError(f"invalid verification_type tag {tag}: {self.source}")

    def _parse_annotations(self, reader: _Reader, context: str, *, allow_target: bool) -> None:
        types: set[str] = set()
        for index in range(reader.u2()):
            descriptor = self._parse_annotation(
                reader,
                f"{context}.annotation[{index}]",
                allow_target=allow_target,
            )
            _require(
                descriptor not in types,
                f"duplicate annotation {descriptor}: {self.source}",
            )
            types.add(descriptor)

    def _parse_annotation(
        self,
        reader: _Reader,
        context: str,
        *,
        allow_target: bool,
    ) -> str:
        descriptor = self._utf8(reader.u2(), context + ".type_index")
        is_target = descriptor == _MIXIN_MERGED_DESCRIPTOR
        _require(
            not is_target or allow_target,
            "MixinMerged annotation occurs outside a method "
            f"RuntimeVisibleAnnotations attribute: {self.source}",
        )
        names: set[str] = set()
        session_count = 0
        for index in range(reader.u2()):
            row = f"{context}.element[{index}]"
            name = self._utf8(reader.u2(), row + ".name_index")
            _require(
                name not in names,
                f"duplicate annotation element {name}: {self.source}",
            )
            names.add(name)
            if is_target and name == _SESSION_ELEMENT:
                session_count += 1
                _require(
                    reader.u1() == ord("s"),
                    f"MixinMerged.sessionId is not a string: {self.source}",
                )
                value_index = reader.u2()
                self._ref(
                    value_index,
                    {1},
                    row + ".session_value",
                    authorized_session_element=True,
                )
                value = self._entry(value_index, {1}, row + ".session_value").text
                _require(
                    value is not None and _UUID_RE.fullmatch(value) is not None,
                    f"MixinMerged.sessionId is not a canonical lowercase UUID: {self.source}",
                )
                self.target_value_indices.add(value_index)
                self.target_reference_count += 1
            else:
                self._parse_element_value(reader, row + ".value")
        if is_target:
            _require(
                session_count == 1,
                f"MixinMerged annotation lacks exactly one sessionId: {self.source}",
            )
        return descriptor

    def _parse_element_value(self, reader: _Reader, context: str) -> None:
        tag = chr(reader.u1())
        primitive = {
            "B": {3},
            "C": {3},
            "D": {6},
            "F": {4},
            "I": {3},
            "J": {5},
            "S": {3},
            "Z": {3},
            "s": {1},
        }
        if tag in primitive:
            self._ref(reader.u2(), primitive[tag], context + ".const_value")
        elif tag == "e":
            self._ref(reader.u2(), {1}, context + ".enum_type")
            self._ref(reader.u2(), {1}, context + ".enum_name")
        elif tag == "c":
            self._ref(reader.u2(), {1}, context + ".class_info")
        elif tag == "@":
            self._parse_annotation(reader, context + ".annotation", allow_target=False)
        elif tag == "[":
            for index in range(reader.u2()):
                self._parse_element_value(reader, f"{context}.array[{index}]")
        else:
            raise CaptureValidationError(f"invalid annotation element tag {tag!r}: {self.source}")

    def _parse_type_annotations(self, reader: _Reader, context: str) -> None:
        for index in range(reader.u2()):
            row = f"{context}.type_annotation[{index}]"
            target = reader.u1()
            if target in {0x00, 0x01, 0x16}:
                reader.u1()
            elif target in {0x10, 0x17, 0x42, *range(0x43, 0x47)}:
                reader.u2()
            elif target in {0x11, 0x12}:
                reader.u1()
                reader.u1()
            elif target in {0x13, 0x14, 0x15}:
                pass
            elif target in {0x40, 0x41}:
                for _ in range(reader.u2()):
                    reader.u2()
                    reader.u2()
                    reader.u2()
            elif 0x47 <= target <= 0x4B:
                reader.u2()
                reader.u1()
            else:
                raise CaptureValidationError(
                    f"invalid type_annotation target 0x{target:02x}: {self.source}"
                )
            for _ in range(reader.u1()):
                kind, argument = reader.u1(), reader.u1()
                _require(
                    kind <= 3 and (kind == 3 or argument == 0),
                    f"invalid type_path: {self.source}",
                )
            self._parse_annotation(reader, row, allow_target=False)

    def _parse_module(self, reader: _Reader, owner: str, context: str) -> None:
        _require(owner == "class", f"Module on {owner}: {self.source}")
        self._ref(reader.u2(), {19}, context + ".module_name")
        reader.u2()
        self._optional_ref(reader.u2(), {1}, context + ".module_version")
        for index in range(reader.u2()):
            row = f"{context}.requires[{index}]"
            self._ref(reader.u2(), {19}, row + ".module")
            reader.u2()
            self._optional_ref(reader.u2(), {1}, row + ".version")
        for group in ("exports", "opens"):
            for index in range(reader.u2()):
                row = f"{context}.{group}[{index}]"
                self._ref(reader.u2(), {20}, row + ".package")
                reader.u2()
                for target in range(reader.u2()):
                    self._ref(reader.u2(), {19}, f"{row}.target[{target}]")
        for index in range(reader.u2()):
            self._ref(reader.u2(), {7}, f"{context}.uses[{index}]")
        for index in range(reader.u2()):
            row = f"{context}.provides[{index}]"
            self._ref(reader.u2(), {7}, row + ".service")
            for implementation in range(reader.u2()):
                self._ref(reader.u2(), {7}, f"{row}.implementation[{implementation}]")


@dataclass(frozen=True, slots=True)
class SemanticBytecodeClassReceipt:
    relative_path: str
    size: int
    exact_sha256: str
    semantic_sha256: str
    normalized_utf8_constants: int
    normalized_annotation_references: int


@dataclass(frozen=True, slots=True)
class SemanticBytecodeManifest:
    class_dump_directory: Path
    exact_foundation_manifest_sha256: str
    class_count: int
    total_size_bytes: int
    normalized_class_count: int
    normalized_utf8_constants: int
    normalized_annotation_references: int
    classes: tuple[SemanticBytecodeClassReceipt, ...]
    semantic_dump_sha256: str
    manifest_sha256: str

    def as_dict(self) -> dict[str, Any]:
        material = _manifest_material(self)
        material["manifest_sha256"] = self.manifest_sha256
        return material


def _stable_read(path: Path, expected_size: int, expected_sha256: str) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise CaptureValidationError(f"cannot inspect class {path}: {exc}") from exc
    _require(not stat.S_ISLNK(before.st_mode), f"class must not be a symlink: {path}")
    _require(stat.S_ISREG(before.st_mode), f"class is not a regular file: {path}")
    _require(before.st_size == expected_size, f"class size differs from exact manifest: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CaptureValidationError(f"cannot open class {path}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        _require(
            identity
            == (
                opened.st_dev,
                opened.st_ino,
                opened.st_mode,
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            ),
            f"class mutated before reading: {path}",
        )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, _READ_CHUNK_BYTES)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            _require(total <= _MAX_CLASS_FILE_BYTES, f"class grew outside bounds: {path}")
        after = os.fstat(descriptor)
        _require(
            (
                opened.st_dev,
                opened.st_ino,
                opened.st_mode,
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            )
            == (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ),
            f"class mutated while reading: {path}",
        )
    finally:
        os.close(descriptor)
    data = b"".join(chunks)
    _require(
        len(data) == expected_size,
        f"class size changed while reading: {path}",
    )
    _require(
        hashlib.sha256(data).hexdigest() == expected_sha256,
        f"class digest differs from exact manifest: {path}",
    )
    return data


def _semantic_view_material(
    classes: Sequence[SemanticBytecodeClassReceipt],
    class_count: int,
    total_size_bytes: int,
) -> dict[str, Any]:
    return {
        "format": SEMANTIC_BYTECODE_VIEW_FORMAT,
        "policy_id": SEMANTIC_BYTECODE_POLICY_ID,
        "class_count": class_count,
        "total_size_bytes": total_size_bytes,
        "classes": [
            {
                "relative_path": item.relative_path,
                "size": item.size,
                "semantic_sha256": item.semantic_sha256,
            }
            for item in classes
        ],
    }


def _manifest_material(manifest: SemanticBytecodeManifest) -> dict[str, Any]:
    return {
        "format": SEMANTIC_BYTECODE_MANIFEST_FORMAT,
        "policy": {
            "id": SEMANTIC_BYTECODE_POLICY_ID,
            "sha256": SEMANTIC_BYTECODE_POLICY_SHA256,
            "material": semantic_bytecode_policy_material(),
        },
        "exact_foundation_manifest": {
            "format": FOUNDATION_CLASS_DUMP_MANIFEST_FORMAT,
            "sha256": manifest.exact_foundation_manifest_sha256,
        },
        "class_count": manifest.class_count,
        "total_size_bytes": manifest.total_size_bytes,
        "normalized_class_count": manifest.normalized_class_count,
        "normalized_utf8_constants": manifest.normalized_utf8_constants,
        "normalized_annotation_references": manifest.normalized_annotation_references,
        "semantic_dump_sha256": manifest.semantic_dump_sha256,
        "classes": [
            {
                "relative_path": item.relative_path,
                "size": item.size,
                "exact_sha256": item.exact_sha256,
                "semantic_sha256": item.semantic_sha256,
                "normalized_utf8_constants": item.normalized_utf8_constants,
                "normalized_annotation_references": item.normalized_annotation_references,
            }
            for item in manifest.classes
        ],
    }


def build_semantic_bytecode_manifest(
    class_dump_directory: str | os.PathLike[str],
) -> SemanticBytecodeManifest:
    """Build exact and policy-normalized receipts for a complete dump tree."""

    exact_before = build_foundation_class_dump_manifest(class_dump_directory)
    dump = exact_before.class_dump_directory
    receipts: list[SemanticBytecodeClassReceipt] = []
    for entry in exact_before.entries:
        path = dump.joinpath(*entry.relative_path.split("/"))
        data = _stable_read(path, entry.size, entry.sha256)
        expected_name = entry.relative_path[: -len(".class")]
        parsed = _ClassParser(data, entry.relative_path, expected_name).parse()
        receipts.append(
            SemanticBytecodeClassReceipt(
                relative_path=entry.relative_path,
                size=entry.size,
                exact_sha256=entry.sha256,
                semantic_sha256=hashlib.sha256(parsed.semantic_bytes).hexdigest(),
                normalized_utf8_constants=parsed.normalized_utf8_constants,
                normalized_annotation_references=parsed.normalized_annotation_references,
            )
        )
    receipts.sort(key=lambda item: item.relative_path)
    exact_after = build_foundation_class_dump_manifest(class_dump_directory)
    _require(
        exact_before.manifest_sha256 == exact_after.manifest_sha256,
        "Foundation dump changed while deriving semantic receipts",
    )
    classes = tuple(receipts)
    semantic_digest = hashlib.sha256(
        canonical_json_bytes(
            _semantic_view_material(
                classes,
                exact_before.class_count,
                exact_before.total_size_bytes,
            )
        )
    ).hexdigest()
    provisional = SemanticBytecodeManifest(
        class_dump_directory=dump,
        exact_foundation_manifest_sha256=exact_before.manifest_sha256,
        class_count=exact_before.class_count,
        total_size_bytes=exact_before.total_size_bytes,
        normalized_class_count=sum(
            item.normalized_utf8_constants > 0 for item in classes
        ),
        normalized_utf8_constants=sum(
            item.normalized_utf8_constants for item in classes
        ),
        normalized_annotation_references=sum(
            item.normalized_annotation_references for item in classes
        ),
        classes=classes,
        semantic_dump_sha256=semantic_digest,
        manifest_sha256="0" * 64,
    )
    manifest_digest = hashlib.sha256(
        canonical_json_bytes(_manifest_material(provisional))
    ).hexdigest()
    return SemanticBytecodeManifest(
        class_dump_directory=provisional.class_dump_directory,
        exact_foundation_manifest_sha256=provisional.exact_foundation_manifest_sha256,
        class_count=provisional.class_count,
        total_size_bytes=provisional.total_size_bytes,
        normalized_class_count=provisional.normalized_class_count,
        normalized_utf8_constants=provisional.normalized_utf8_constants,
        normalized_annotation_references=provisional.normalized_annotation_references,
        classes=provisional.classes,
        semantic_dump_sha256=provisional.semantic_dump_sha256,
        manifest_sha256=manifest_digest,
    )


def write_semantic_bytecode_manifest(
    manifest: SemanticBytecodeManifest,
    destination: str | os.PathLike[str],
) -> None:
    """Atomically write one canonical JSON manifest."""

    _require(isinstance(manifest, SemanticBytecodeManifest), "semantic manifest is required")
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    payload = canonical_json_bytes(manifest.as_dict()) + b"\n"
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _plain_int(value: Any, context: str) -> int:
    _require(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0,
        f"{context} must be a nonnegative integer",
    )
    return value


def parse_semantic_bytecode_manifest(
    value: Mapping[str, Any],
    *,
    class_dump_directory: str | os.PathLike[str] = ".",
) -> SemanticBytecodeManifest:
    """Validate a serialized semantic manifest without trusting its digests."""

    _require(isinstance(value, Mapping), "semantic manifest must be an object")
    expected_keys = {
        "format",
        "policy",
        "exact_foundation_manifest",
        "class_count",
        "total_size_bytes",
        "normalized_class_count",
        "normalized_utf8_constants",
        "normalized_annotation_references",
        "semantic_dump_sha256",
        "classes",
        "manifest_sha256",
    }
    _require(set(value) == expected_keys, "semantic manifest fields differ from v1")
    _require(
        value["format"] == SEMANTIC_BYTECODE_MANIFEST_FORMAT,
        "semantic manifest format differs",
    )
    policy = value["policy"]
    _require(
        isinstance(policy, Mapping)
        and set(policy) == {"id", "sha256", "material"}
        and policy["id"] == SEMANTIC_BYTECODE_POLICY_ID
        and policy["sha256"] == SEMANTIC_BYTECODE_POLICY_SHA256
        and policy["material"] == semantic_bytecode_policy_material(),
        "semantic manifest policy differs from supported policy",
    )
    exact = value["exact_foundation_manifest"]
    _require(
        isinstance(exact, Mapping)
        and set(exact) == {"format", "sha256"}
        and exact["format"] == FOUNDATION_CLASS_DUMP_MANIFEST_FORMAT,
        "exact Foundation manifest receipt differs",
    )
    exact_digest = _sha256(exact["sha256"], "exact Foundation manifest digest")
    rows = value["classes"]
    _require(isinstance(rows, list) and bool(rows), "semantic manifest classes must be nonempty")
    classes: list[SemanticBytecodeClassReceipt] = []
    previous = ""
    for index, row in enumerate(rows):
        _require(isinstance(row, Mapping), f"semantic class {index} must be an object")
        _require(
            set(row) == {
                "relative_path", "size", "exact_sha256", "semantic_sha256",
                "normalized_utf8_constants", "normalized_annotation_references",
            },
            f"semantic class {index} fields differ",
        )
        relative = row["relative_path"]
        _require(
            isinstance(relative, str)
            and relative.endswith(".class")
            and relative > previous
            and not relative.startswith("/")
            and "\\" not in relative
            and all(part not in {"", ".", ".."} for part in relative.split("/")),
            f"semantic class {index} path is invalid or unordered",
        )
        previous = relative
        classes.append(
            SemanticBytecodeClassReceipt(
                relative_path=relative,
                size=_plain_int(row["size"], f"semantic class {index} size"),
                exact_sha256=_sha256(
                    row["exact_sha256"],
                    f"semantic class {index} exact digest",
                ),
                semantic_sha256=_sha256(
                    row["semantic_sha256"],
                    f"semantic class {index} semantic digest",
                ),
                normalized_utf8_constants=_plain_int(
                    row["normalized_utf8_constants"],
                    f"semantic class {index} normalized constants",
                ),
                normalized_annotation_references=_plain_int(
                    row["normalized_annotation_references"],
                    f"semantic class {index} normalized references",
                ),
            )
        )
    class_count = _plain_int(value["class_count"], "class count")
    total_size = _plain_int(value["total_size_bytes"], "total size")
    normalized_classes = _plain_int(
        value["normalized_class_count"], "normalized class count"
    )
    normalized_constants = _plain_int(
        value["normalized_utf8_constants"], "normalized constant count"
    )
    normalized_references = _plain_int(
        value["normalized_annotation_references"], "normalized reference count"
    )
    _require(class_count == len(classes), "semantic class count differs")
    _require(
        total_size == sum(item.size for item in classes),
        "semantic total size differs",
    )
    _require(
        normalized_classes
        == sum(item.normalized_utf8_constants > 0 for item in classes),
        "normalized class count differs",
    )
    _require(
        normalized_constants
        == sum(item.normalized_utf8_constants for item in classes),
        "normalized constant count differs",
    )
    _require(
        normalized_references
        == sum(item.normalized_annotation_references for item in classes),
        "normalized reference count differs",
    )
    semantic_digest = _sha256(value["semantic_dump_sha256"], "semantic dump digest")
    _require(
        semantic_digest
        == hashlib.sha256(
            canonical_json_bytes(
                _semantic_view_material(classes, class_count, total_size)
            )
        ).hexdigest(),
        "semantic dump digest does not match class receipts",
    )
    manifest = SemanticBytecodeManifest(
        class_dump_directory=Path(class_dump_directory),
        exact_foundation_manifest_sha256=exact_digest,
        class_count=class_count,
        total_size_bytes=total_size,
        normalized_class_count=normalized_classes,
        normalized_utf8_constants=normalized_constants,
        normalized_annotation_references=normalized_references,
        classes=tuple(classes),
        semantic_dump_sha256=semantic_digest,
        manifest_sha256=_sha256(value["manifest_sha256"], "semantic manifest digest"),
    )
    _require(
        manifest.manifest_sha256
        == hashlib.sha256(canonical_json_bytes(_manifest_material(manifest))).hexdigest(),
        "semantic manifest digest does not match receipts",
    )
    return manifest


def load_semantic_bytecode_manifest(
    source: str | os.PathLike[str],
) -> SemanticBytecodeManifest:
    path = Path(source)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CaptureValidationError(f"cannot load semantic manifest {path}: {exc}") from exc
    return parse_semantic_bytecode_manifest(value, class_dump_directory=path.parent)


def evaluate_semantic_bytecode_manifests(
    manifests: Mapping[str, SemanticBytecodeManifest],
) -> dict[str, Any]:
    """Produce a content-addressed exact-vs-semantic comparison receipt."""

    _require(
        isinstance(manifests, Mapping) and len(manifests) >= 2,
        "at least two semantic manifests are required",
    )
    labels = sorted(manifests)
    _require(
        all(isinstance(label, str) and bool(label) for label in labels),
        "comparison labels must be nonempty strings",
    )
    _require(len(labels) == len(set(labels)), "comparison labels must be unique")
    rows = []
    for label in labels:
        manifest = manifests[label]
        _require(
            isinstance(manifest, SemanticBytecodeManifest),
            f"comparison {label} is not a semantic manifest",
        )
        rows.append(
            {
                "label": label,
                "manifest_sha256": manifest.manifest_sha256,
                "exact_foundation_manifest_sha256": manifest.exact_foundation_manifest_sha256,
                "semantic_dump_sha256": manifest.semantic_dump_sha256,
                "class_count": manifest.class_count,
                "total_size_bytes": manifest.total_size_bytes,
                "normalized_class_count": manifest.normalized_class_count,
                "normalized_utf8_constants": manifest.normalized_utf8_constants,
                "normalized_annotation_references": manifest.normalized_annotation_references,
            }
        )
    exact_values = [row["exact_foundation_manifest_sha256"] for row in rows]
    semantic_values = [row["semantic_dump_sha256"] for row in rows]
    material = {
        "format": SEMANTIC_BYTECODE_COMPARISON_FORMAT,
        "policy_id": SEMANTIC_BYTECODE_POLICY_ID,
        "case_count": len(rows),
        "cases": rows,
        "exact_foundation_manifests_all_distinct": len(set(exact_values)) == len(exact_values),
        "semantic_dumps_all_equal": len(set(semantic_values)) == 1,
    }
    material["comparison_sha256"] = hashlib.sha256(canonical_json_bytes(material)).hexdigest()
    return material


__all__ = [
    "SEMANTIC_BYTECODE_COMPARISON_FORMAT",
    "SEMANTIC_BYTECODE_MANIFEST_FORMAT",
    "SEMANTIC_BYTECODE_POLICY_FORMAT",
    "SEMANTIC_BYTECODE_POLICY_ID",
    "SEMANTIC_BYTECODE_POLICY_SHA256",
    "SEMANTIC_BYTECODE_VIEW_FORMAT",
    "SemanticBytecodeClassReceipt",
    "SemanticBytecodeManifest",
    "build_semantic_bytecode_manifest",
    "evaluate_semantic_bytecode_manifests",
    "load_semantic_bytecode_manifest",
    "parse_semantic_bytecode_manifest",
    "semantic_bytecode_policy_material",
    "write_semantic_bytecode_manifest",
]
