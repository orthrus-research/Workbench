"""Packaged Mixin AP compatibility-metadata conformance facts.

This scanner compares CLASS-retained annotations in exact archive bytes with
the annotation-processor metadata packaged beside them.  It deliberately does
not claim that an annotation processor ran, that a compiler invocation was
reproducible, or that the runtime selects this resource.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
from pathlib import PurePosixPath
import re
import struct
from typing import Any, Mapping, Sequence
from zipfile import BadZipFile, LargeZipFile, ZipFile

from .mixin_topology import (
    ArtifactInput,
    ArtifactScanError,
    MAX_ARCHIVE_BYTES,
    _inventory_archive,
    _read_member,
    canonical_json_bytes,
)


AP_COMPATIBILITY_FORMAT = (
    "workbench-project-intelligence-mixin-ap-compatibility-"
    "conformance-receipt-v1"
)
AP_COMPATIBILITY_POLICY_FORMAT = "workbench-mixin-ap-compatibility-policy-v1"
CANONICALIZATION_ID = "workbench-canonical-json-v1"

_RECEIPT_ID_PREFIX = (
    "workbench-mixin-ap-compatibility-conformance-receipt:sha256:"
)
_ARTIFACT_ID_PREFIX = "workbench-mixin-ap-compatibility-artifact:sha256:"
_DECLARATION_ID_PREFIX = "workbench-mixin-ap-compatibility-declaration:sha256:"
_EXPECTED_ENTRY_ID_PREFIX = "workbench-mixin-ap-compatibility-expected-entry:sha256:"
_ACTUAL_ENTRY_ID_PREFIX = "workbench-mixin-ap-compatibility-actual-entry:sha256:"
_ISSUE_ID_PREFIX = "workbench-mixin-ap-compatibility-issue:sha256:"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_VERSION_RE = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)$")
_DESCRIPTOR_RE = re.compile(r"^L[^.;\[]+(?:/[^.;\[]+)*;$")
_MULTI_RELEASE_RE = re.compile(
    r"^META-INF/versions/[1-9][0-9]*/(?P<logical>.+)$"
)

_ANNOTATION_ATTRIBUTES = {
    "RuntimeInvisibleAnnotations",
    "RuntimeVisibleAnnotations",
}
_ISSUE_KINDS = {
    "metadata-entry-invalid",
    "metadata-json-invalid",
    "metadata-resource-missing",
    "metadata-root-invalid",
    "missing-key",
    "extra-key",
    "unexpected-metadata-resource",
    "version-mismatch",
}
_PARSE_STATES = {"exact", "invalid-json", "invalid-root", "missing"}
_CONFORMANCE_STATES = {"conformant", "nonconformant", "not-applicable"}

_BOUNDARIES = {
    "annotation_processor_execution_proved": False,
    "candidate_lock_v1_mutated": False,
    "compiler_invocation_reproducible": False,
    "packaged_bytecode_metadata_conformance_only": True,
    "runtime_resource_selection_proved": False,
}
_LIMITATIONS = [
    "The receipt proves packaged bytecode-to-metadata conformance, not that the annotation processor executed.",
    "Compiler inputs, processor options, diagnostics, incremental-build state, and refmap output are not reproduced.",
    "Runtime classpath order, configuration source ownership, and compatibility-resource selection are not observed.",
    "Duplicate logical Mixin classes, including unresolved multi-release variants, fail closed before publication.",
]


class _DuplicateJsonKey(ValueError):
    pass


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    return prefix + _sha256(canonical_json_bytes(value))


def _identified(prefix: str, id_field: str, material: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(material)
    row[id_field] = _content_id(prefix, material)
    return row


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value}")


def _strict_json(raw: bytes) -> Any:
    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_json_object,
        parse_constant=_reject_constant,
    )


def _normalize_version(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("version is not a string")
    match = _VERSION_RE.fullmatch(value)
    if match is None:
        raise ValueError("version is not major.minor.patch")
    parts = tuple(int(item) for item in match.groups())
    if any(item > 999 for item in parts):
        raise ValueError("version component exceeds 999")
    return ".".join(str(item) for item in parts)


def _resource_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ArtifactScanError("AP compatibility policy metadata resource is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        raise ArtifactScanError("AP compatibility policy metadata resource is unsafe")
    return value


def _descriptor_type(value: str, position: int, *, allow_void: bool) -> int | None:
    if position >= len(value):
        return None
    token = value[position]
    if token == "V":
        return position + 1 if allow_void else None
    if token in "BCDFIJSZ":
        return position + 1
    if token == "L":
        end = value.find(";", position + 1)
        if end < 0:
            return None
        internal = value[position + 1 : end]
        if (
            not internal
            or "." in internal
            or "[" in internal
            or "\\" in internal
            or any(not part for part in internal.split("/"))
        ):
            return None
        return end + 1
    if token == "[":
        while position < len(value) and value[position] == "[":
            position += 1
        return _descriptor_type(value, position, allow_void=False)
    return None


def _valid_field_descriptor(value: str) -> bool:
    end = _descriptor_type(value, 0, allow_void=False)
    return end == len(value)


def _valid_method_descriptor(value: str) -> bool:
    if not value.startswith("("):
        return False
    position = 1
    while position < len(value) and value[position] != ")":
        next_position = _descriptor_type(value, position, allow_void=False)
        if next_position is None:
            return False
        position = next_position
    if position >= len(value) or value[position] != ")":
        return False
    end = _descriptor_type(value, position + 1, allow_void=True)
    return end == len(value)


def _parse_policy(raw: bytes) -> tuple[dict[str, Any], str]:
    try:
        value = _strict_json(raw)
    except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
        raise ArtifactScanError(f"AP compatibility policy is invalid JSON: {exc}") from exc
    expected = {
        "compatibility_annotation_descriptor",
        "component",
        "default_class_compatibility",
        "format",
        "member_key_encoding",
        "metadata_resource",
        "mixin_annotation_descriptor",
        "policy_id",
        "schema_version",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or value.get("format") != AP_COMPATIBILITY_POLICY_FORMAT
        or value.get("schema_version") != 1
        or not isinstance(value.get("policy_id"), str)
        or not value["policy_id"]
        or value.get("member_key_encoding") != "cleanmix-class-member-v1"
    ):
        raise ArtifactScanError("AP compatibility policy has an unsupported V1 shape")
    component = value.get("component")
    if (
        not isinstance(component, dict)
        or set(component)
        != {"distribution", "distribution_version", "source_revision"}
        or not all(isinstance(component.get(key), str) and component[key] for key in component)
        or _REVISION_RE.fullmatch(component["source_revision"]) is None
    ):
        raise ArtifactScanError("AP compatibility policy component binding is invalid")
    for field in ("mixin_annotation_descriptor", "compatibility_annotation_descriptor"):
        descriptor = value.get(field)
        if not isinstance(descriptor, str) or _DESCRIPTOR_RE.fullmatch(descriptor) is None:
            raise ArtifactScanError(f"AP compatibility policy {field} is invalid")
    value["metadata_resource"] = _resource_path(value.get("metadata_resource"))
    try:
        default_version = _normalize_version(value.get("default_class_compatibility"))
    except ValueError as exc:
        raise ArtifactScanError(f"AP compatibility policy default is invalid: {exc}") from exc
    if default_version != value["default_class_compatibility"]:
        raise ArtifactScanError("AP compatibility policy default is not canonical")
    return value, _sha256(raw)


class _Reader:
    def __init__(self, raw: bytes, context: str):
        self.raw = raw
        self.context = context
        self.position = 0

    def take(self, size: int) -> bytes:
        if size < 0 or self.position + size > len(self.raw):
            raise ArtifactScanError(f"{self.context} is truncated")
        result = self.raw[self.position : self.position + size]
        self.position += size
        return result

    def u1(self) -> int:
        return self.take(1)[0]

    def u2(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def u4(self) -> int:
        return struct.unpack(">I", self.take(4))[0]

    def child(self, size: int, context: str) -> "_Reader":
        return _Reader(self.take(size), context)

    def require_end(self) -> None:
        if self.position != len(self.raw):
            raise ArtifactScanError(f"{self.context} contains trailing bytes")


def _modified_utf8(raw: bytes, context: str) -> str:
    units: list[int] = []
    index = 0
    while index < len(raw):
        first = raw[index]
        index += 1
        if 0x01 <= first <= 0x7F:
            units.append(first)
            continue
        if 0xC0 <= first <= 0xDF:
            if index >= len(raw):
                raise ArtifactScanError(f"{context} has truncated modified UTF-8")
            second = raw[index]
            index += 1
            if second & 0xC0 != 0x80:
                raise ArtifactScanError(f"{context} has invalid modified UTF-8")
            value = ((first & 0x1F) << 6) | (second & 0x3F)
            if value == 0:
                if first != 0xC0 or second != 0x80:
                    raise ArtifactScanError(f"{context} has invalid null encoding")
            elif value < 0x80:
                raise ArtifactScanError(f"{context} has overlong modified UTF-8")
            units.append(value)
            continue
        if 0xE0 <= first <= 0xEF:
            if index + 1 >= len(raw):
                raise ArtifactScanError(f"{context} has truncated modified UTF-8")
            second, third = raw[index], raw[index + 1]
            index += 2
            if second & 0xC0 != 0x80 or third & 0xC0 != 0x80:
                raise ArtifactScanError(f"{context} has invalid modified UTF-8")
            value = ((first & 0x0F) << 12) | ((second & 0x3F) << 6) | (third & 0x3F)
            if value < 0x800:
                raise ArtifactScanError(f"{context} has overlong modified UTF-8")
            units.append(value)
            continue
        raise ArtifactScanError(f"{context} has unsupported modified UTF-8")
    result: list[str] = []
    index = 0
    while index < len(units):
        value = units[index]
        index += 1
        if 0xD800 <= value <= 0xDBFF:
            if index >= len(units) or not 0xDC00 <= units[index] <= 0xDFFF:
                raise ArtifactScanError(f"{context} has an unpaired high surrogate")
            low = units[index]
            index += 1
            result.append(chr(0x10000 + ((value - 0xD800) << 10) + low - 0xDC00))
        elif 0xDC00 <= value <= 0xDFFF:
            raise ArtifactScanError(f"{context} has an unpaired low surrogate")
        else:
            result.append(chr(value))
    return "".join(result)


class _ConstantPool:
    def __init__(self, reader: _Reader):
        count = reader.u2()
        self.entries: list[tuple[str, Any] | None] = [None] * count
        index = 1
        while index < count:
            tag = reader.u1()
            if tag == 1:
                raw = reader.take(reader.u2())
                self.entries[index] = ("utf8", _modified_utf8(raw, reader.context))
            elif tag in {3, 4}:
                self.entries[index] = ("number", reader.take(4))
            elif tag in {5, 6}:
                self.entries[index] = ("number", reader.take(8))
                index += 1
            elif tag == 7:
                self.entries[index] = ("class", reader.u2())
            elif tag == 8:
                self.entries[index] = ("string", reader.u2())
            elif tag in {9, 10, 11, 12, 17, 18}:
                self.entries[index] = ("pair", (reader.u2(), reader.u2()))
            elif tag == 15:
                self.entries[index] = ("handle", (reader.u1(), reader.u2()))
            elif tag in {16, 19, 20}:
                self.entries[index] = ("index", reader.u2())
            else:
                raise ArtifactScanError(f"{reader.context} has unknown constant tag {tag}")
            index += 1

    def entry(self, index: int, kind: str) -> Any:
        try:
            entry = self.entries[index]
        except IndexError:
            entry = None
        if entry is None or entry[0] != kind:
            raise ArtifactScanError(f"class file has invalid {kind} constant reference")
        return entry[1]

    def utf8(self, index: int) -> str:
        return str(self.entry(index, "utf8"))

    def class_name(self, index: int) -> str:
        return self.utf8(int(self.entry(index, "class")))


@dataclass(frozen=True)
class _Annotation:
    descriptor: str
    elements: Mapping[str, tuple[str, Any]]
    attribute: str


def _element_value(reader: _Reader, pool: _ConstantPool) -> tuple[str, Any]:
    tag = chr(reader.u1())
    if tag in "BCDFIJSZs":
        index = reader.u2()
        return ("string", pool.utf8(index)) if tag == "s" else ("constant", index)
    if tag == "e":
        return "enum", (pool.utf8(reader.u2()), pool.utf8(reader.u2()))
    if tag == "c":
        return "class", pool.utf8(reader.u2())
    if tag == "@":
        return "annotation", _annotation(reader, pool, "nested")
    if tag == "[":
        return "array", [_element_value(reader, pool) for _ in range(reader.u2())]
    raise ArtifactScanError(f"{reader.context} has unknown annotation value tag {tag!r}")


def _annotation(reader: _Reader, pool: _ConstantPool, attribute: str) -> _Annotation:
    descriptor = pool.utf8(reader.u2())
    elements: dict[str, tuple[str, Any]] = {}
    for _ in range(reader.u2()):
        name = pool.utf8(reader.u2())
        if name in elements:
            raise ArtifactScanError(f"{reader.context} repeats annotation element {name!r}")
        elements[name] = _element_value(reader, pool)
    return _Annotation(descriptor, elements, attribute)


def _attributes(reader: _Reader, pool: _ConstantPool) -> list[_Annotation]:
    annotations: list[_Annotation] = []
    seen_annotation_attributes: set[str] = set()
    for _ in range(reader.u2()):
        name = pool.utf8(reader.u2())
        payload = reader.child(reader.u4(), f"{reader.context} attribute {name}")
        if name not in _ANNOTATION_ATTRIBUTES:
            continue
        if name in seen_annotation_attributes:
            raise ArtifactScanError(f"{reader.context} repeats {name}")
        seen_annotation_attributes.add(name)
        annotations.extend(_annotation(payload, pool, name) for _ in range(payload.u2()))
        payload.require_end()
    return annotations


def _compatibility(
    annotations: Sequence[_Annotation], descriptor: str, context: str
) -> tuple[str, str, str] | None:
    matches = [annotation for annotation in annotations if annotation.descriptor == descriptor]
    if not matches:
        return None
    if len(matches) != 1:
        raise ArtifactScanError(f"{context} repeats the Compatibility annotation")
    annotation = matches[0]
    if set(annotation.elements) != {"value"}:
        raise ArtifactScanError(f"{context} has a malformed Compatibility annotation")
    kind, raw_value = annotation.elements["value"]
    if kind != "string" or not isinstance(raw_value, str):
        raise ArtifactScanError(f"{context} Compatibility value is not a string")
    try:
        normalized = _normalize_version(raw_value)
    except ValueError as exc:
        raise ArtifactScanError(f"{context} Compatibility value is invalid: {exc}") from exc
    return raw_value, normalized, annotation.attribute


def _member(
    reader: _Reader, pool: _ConstantPool, compatibility_descriptor: str, kind: str
) -> dict[str, Any] | None:
    reader.u2()  # access flags
    name = pool.utf8(reader.u2())
    descriptor = pool.utf8(reader.u2())
    descriptor_valid = (
        _valid_method_descriptor(descriptor)
        if kind == "method"
        else _valid_field_descriptor(descriptor)
    )
    if not name or not descriptor_valid:
        raise ArtifactScanError(
            f"{reader.context} has an invalid {kind} name or descriptor"
        )
    annotations = _attributes(reader, pool)
    compatibility = _compatibility(
        annotations, compatibility_descriptor, f"{reader.context} {kind} {name}{descriptor}"
    )
    if compatibility is None:
        return None
    raw_version, version, attribute = compatibility
    return {
        "annotation_attribute": attribute,
        "declared_version": raw_version,
        "descriptor": descriptor,
        "effective_version": version,
        "kind": kind,
        "member_name": name,
    }


def _class_declaration(
    raw: bytes, entry_path: str, policy: Mapping[str, Any]
) -> dict[str, Any] | None:
    reader = _Reader(raw, f"class {entry_path}")
    if reader.take(4) != b"\xca\xfe\xba\xbe":
        raise ArtifactScanError(f"class {entry_path} has invalid magic")
    reader.take(4)  # minor and major
    pool = _ConstantPool(reader)
    reader.u2()  # access flags
    internal_name = pool.class_name(reader.u2())
    reader.u2()  # superclass
    for _ in range(reader.u2()):
        reader.u2()
    fields = [
        _member(reader, pool, policy["compatibility_annotation_descriptor"], "field")
        for _ in range(reader.u2())
    ]
    methods = [
        _member(reader, pool, policy["compatibility_annotation_descriptor"], "method")
        for _ in range(reader.u2())
    ]
    class_annotations = _attributes(reader, pool)
    reader.require_end()
    mixin_annotations = [
        annotation
        for annotation in class_annotations
        if annotation.descriptor == policy["mixin_annotation_descriptor"]
    ]
    if not mixin_annotations:
        return None
    if len(mixin_annotations) != 1:
        raise ArtifactScanError(f"class {entry_path} repeats the Mixin annotation")
    if (
        not internal_name
        or internal_name.startswith("[")
        or "." in internal_name
        or ";" in internal_name
        or "\\" in internal_name
        or any(not part for part in internal_name.split("/"))
    ):
        raise ArtifactScanError(f"class {entry_path} has an invalid internal name")
    class_name = internal_name.replace("/", ".")
    expected_path = internal_name + ".class"
    multi_release = _MULTI_RELEASE_RE.fullmatch(entry_path)
    logical_path = multi_release.group("logical") if multi_release else entry_path
    if logical_path != expected_path:
        raise ArtifactScanError(
            f"Mixin class {class_name} is stored at non-matching path {entry_path}"
        )
    compatibility = _compatibility(
        class_annotations,
        policy["compatibility_annotation_descriptor"],
        f"class {class_name}",
    )
    if compatibility is None:
        declared_version = None
        effective_version = policy["default_class_compatibility"]
        annotation_attribute = None
        source_kind = "policy-default"
    else:
        declared_version, effective_version, annotation_attribute = compatibility
        source_kind = "class-annotation"
    overrides: list[dict[str, Any]] = []
    for member in [*fields, *methods]:
        if member is None:
            continue
        separator = "" if member["kind"] == "method" else ":"
        material = dict(member)
        material["key"] = (
            f"{class_name}::{member['member_name']}{separator}{member['descriptor']}"
        )
        overrides.append(material)
    overrides.sort(key=lambda row: row["key"])
    override_keys = [row["key"] for row in overrides]
    if len(override_keys) != len(set(override_keys)):
        raise ArtifactScanError(f"Mixin class {class_name} repeats a member metadata key")
    material = {
        "annotation_attribute": annotation_attribute,
        "class_name": class_name,
        "class_version_source": source_kind,
        "declared_class_version": declared_version,
        "effective_class_version": effective_version,
        "entry_path": entry_path,
        "entry_sha256": _sha256(raw),
        "member_overrides": overrides,
    }
    return _identified(_DECLARATION_ID_PREFIX, "declaration_id", material)


def _expected_entries(declarations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for declaration in declarations:
        class_material = {
            "key": declaration["class_name"],
            "source_kind": declaration["class_version_source"],
            "source_locator": declaration["entry_path"],
            "version": declaration["effective_class_version"],
        }
        rows.append(
            _identified(_EXPECTED_ENTRY_ID_PREFIX, "entry_id", class_material)
        )
        for member in declaration["member_overrides"]:
            material = {
                "key": member["key"],
                "source_kind": "member-annotation",
                "source_locator": (
                    f"{declaration['entry_path']}#{member['kind']}:"
                    f"{member['member_name']}{member['descriptor']}"
                ),
                "version": member["effective_version"],
            }
            rows.append(_identified(_EXPECTED_ENTRY_ID_PREFIX, "entry_id", material))
    keys = [row["key"] for row in rows]
    if len(keys) != len(set(keys)):
        raise ArtifactScanError(
            "archive contains duplicate logical Mixin classes or compatibility keys"
        )
    return sorted(rows, key=lambda row: row["entry_id"])


def _metadata(
    archive: ZipFile, members: Mapping[str, Any], path: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    info = members.get(path)
    if info is None or info.is_dir():
        return {
            "error": None,
            "invalid_entries": [],
            "parse_state": "missing",
            "path": path,
            "present": False,
            "sha256": None,
            "size_bytes": None,
        }, []
    raw = _read_member(archive, info, path)
    base = {
        "invalid_entries": [],
        "path": path,
        "present": True,
        "sha256": _sha256(raw),
        "size_bytes": len(raw),
    }
    try:
        value = _strict_json(raw)
    except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
        return {
            **base,
            "error": str(exc),
            "parse_state": "invalid-json",
        }, []
    if not isinstance(value, dict):
        return {
            **base,
            "error": f"root JSON type is {type(value).__name__}",
            "parse_state": "invalid-root",
        }, []
    actual: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for key, raw_version in sorted(value.items()):
        reason: str | None = None
        normalized: str | None = None
        if not key:
            reason = "empty-key"
        else:
            try:
                normalized = _normalize_version(raw_version)
            except ValueError as exc:
                reason = str(exc)
            else:
                if normalized != raw_version:
                    reason = "version is not AP-canonical"
        if reason is not None:
            invalid.append(
                {
                    "key": key,
                    "observed": raw_version,
                    "reason": reason,
                }
            )
            continue
        material = {"key": key, "version": normalized}
        actual.append(_identified(_ACTUAL_ENTRY_ID_PREFIX, "entry_id", material))
    base["invalid_entries"] = invalid
    return {**base, "error": None, "parse_state": "exact"}, sorted(
        actual, key=lambda row: row["entry_id"]
    )


def _issue(kind: str, key: str | None, locator: str, observed: Mapping[str, Any]) -> dict[str, Any]:
    material = {
        "issue_kind": kind,
        "key": key,
        "locator": locator,
        "observed": dict(observed),
    }
    return _identified(_ISSUE_ID_PREFIX, "issue_id", material)


def _issues(
    declarations: Sequence[Mapping[str, Any]],
    expected_entries: Sequence[Mapping[str, Any]],
    actual_entries: Sequence[Mapping[str, Any]],
    metadata: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    path = metadata["path"]
    state = metadata["parse_state"]
    if state == "missing":
        if declarations:
            rows.append(
                _issue(
                    "metadata-resource-missing",
                    None,
                    path,
                    {"declared_mixin_count": len(declarations), "present": False},
                )
            )
        return rows
    if state == "invalid-json":
        return [
            _issue(
                "metadata-json-invalid",
                None,
                path,
                {"error": metadata["error"], "sha256": metadata["sha256"]},
            )
        ]
    if state == "invalid-root":
        return [
            _issue(
                "metadata-root-invalid",
                None,
                path,
                {"error": metadata["error"], "sha256": metadata["sha256"]},
            )
        ]
    if not declarations:
        rows.append(
            _issue(
                "unexpected-metadata-resource",
                None,
                path,
                {"actual_entry_count": len(actual_entries), "present": True},
            )
        )
    invalid_keys: set[str] = set()
    for invalid in metadata["invalid_entries"]:
        invalid_keys.add(invalid["key"])
        rows.append(
            _issue(
                "metadata-entry-invalid",
                invalid["key"],
                f"{path}:{invalid['key']}",
                {"reason": invalid["reason"], "value": invalid["observed"]},
            )
        )
    expected = {row["key"]: row for row in expected_entries}
    actual = {row["key"]: row for row in actual_entries}
    for key in sorted(set(expected) - set(actual) - invalid_keys):
        rows.append(
            _issue(
                "missing-key",
                key,
                f"{path}:{key}",
                {"expected_version": expected[key]["version"], "present": False},
            )
        )
    for key in sorted(set(actual) - set(expected)):
        rows.append(
            _issue(
                "extra-key",
                key,
                f"{path}:{key}",
                {"actual_version": actual[key]["version"], "expected": False},
            )
        )
    for key in sorted(set(expected) & set(actual)):
        if expected[key]["version"] == actual[key]["version"]:
            continue
        rows.append(
            _issue(
                "version-mismatch",
                key,
                f"{path}:{key}",
                {
                    "actual_version": actual[key]["version"],
                    "expected_version": expected[key]["version"],
                },
            )
        )
    return sorted(rows, key=lambda row: row["issue_id"])


def build_ap_compatibility_conformance_receipt(
    artifact: ArtifactInput, policy_bytes: bytes
) -> dict[str, Any]:
    """Build a content-addressed conformance receipt from exact packaged bytes."""

    if not isinstance(artifact.label, str) or not artifact.label or "\x00" in artifact.label:
        raise ArtifactScanError("AP compatibility artifact label must be non-empty")
    if not isinstance(artifact.data, bytes) or not isinstance(policy_bytes, bytes):
        raise TypeError("artifact data and policy_bytes must be bytes")
    if len(artifact.data) > MAX_ARCHIVE_BYTES:
        raise ArtifactScanError(f"archive exceeds {MAX_ARCHIVE_BYTES} input bytes")
    policy, policy_sha256 = _parse_policy(policy_bytes)
    declarations: list[dict[str, Any]] = []
    try:
        with ZipFile(BytesIO(artifact.data), "r", allowZip64=True) as archive:
            members, _, _ = _inventory_archive(archive)
            for entry_path, info in sorted(members.items()):
                if info.is_dir() or not entry_path.endswith(".class"):
                    continue
                declaration = _class_declaration(
                    _read_member(archive, info, f"class {entry_path}"),
                    entry_path,
                    policy,
                )
                if declaration is not None:
                    declarations.append(declaration)
            metadata, actual_entries = _metadata(
                archive, members, policy["metadata_resource"]
            )
    except (BadZipFile, LargeZipFile, OSError, EOFError) as exc:
        raise ArtifactScanError(f"{artifact.label} is not a valid ZIP archive: {exc}") from exc
    declarations.sort(key=lambda row: row["declaration_id"])
    class_names = [row["class_name"] for row in declarations]
    if len(class_names) != len(set(class_names)):
        raise ArtifactScanError(
            "archive repeats a logical Mixin class; runtime variant selection is unbound"
        )
    expected_entries = _expected_entries(declarations)
    issues = _issues(declarations, expected_entries, actual_entries, metadata)
    artifact_material = {
        "file_name": artifact.label,
        "sha256": _sha256(artifact.data),
        "size_bytes": len(artifact.data),
    }
    artifact_row = _identified(
        _ARTIFACT_ID_PREFIX, "artifact_id", artifact_material
    )
    if issues:
        conformance_state = "nonconformant"
    elif not declarations:
        conformance_state = "not-applicable"
    else:
        conformance_state = "conformant"
    issue_counts = {
        kind: sum(row["issue_kind"] == kind for row in issues)
        for kind in sorted(_ISSUE_KINDS)
    }
    material = {
        "actual_entries": actual_entries,
        "artifact": artifact_row,
        "boundaries": _BOUNDARIES,
        "canonicalization_id": CANONICALIZATION_ID,
        "declarations": declarations,
        "expected_entries": expected_entries,
        "format": AP_COMPATIBILITY_FORMAT,
        "issues": issues,
        "limitations": _LIMITATIONS,
        "metadata_resource": metadata,
        "policy": {
            "compatibility_annotation_descriptor": policy[
                "compatibility_annotation_descriptor"
            ],
            "default_class_compatibility": policy["default_class_compatibility"],
            "member_key_encoding": policy["member_key_encoding"],
            "metadata_resource": policy["metadata_resource"],
            "mixin_annotation_descriptor": policy["mixin_annotation_descriptor"],
            "policy_id": policy["policy_id"],
            "sha256": policy_sha256,
            "source_revision": policy["component"]["source_revision"],
        },
        "schema_version": 1,
        "summary": {
            "actual_entry_count": len(actual_entries),
            "conformance_state": conformance_state,
            "declared_mixin_count": len(declarations),
            "expected_entry_count": len(expected_entries),
            "issue_count": len(issues),
            "issue_counts": issue_counts,
            "member_override_count": sum(
                len(row["member_overrides"]) for row in declarations
            ),
        },
    }
    receipt = dict(material)
    receipt["receipt_id"] = _content_id(_RECEIPT_ID_PREFIX, material)
    validate_ap_compatibility_conformance_receipt(receipt)
    return receipt


def _validate_records(
    rows: Any, id_field: str, prefix: str, label: str
) -> list[Mapping[str, Any]]:
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise ArtifactScanError(f"AP compatibility {label} must be an object array")
    ids = [row.get(id_field) for row in rows]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise ArtifactScanError(f"AP compatibility {label} are not uniquely ID-sorted")
    for row in rows:
        material = {key: value for key, value in row.items() if key != id_field}
        if row.get(id_field) != _content_id(prefix, material):
            raise ArtifactScanError(f"AP compatibility {label} content ID is wrong")
    return rows


def validate_ap_compatibility_conformance_receipt(receipt: Mapping[str, Any]) -> None:
    """Validate identities and every derivable conformance decision."""

    expected_top = {
        "actual_entries",
        "artifact",
        "boundaries",
        "canonicalization_id",
        "declarations",
        "expected_entries",
        "format",
        "issues",
        "limitations",
        "metadata_resource",
        "policy",
        "receipt_id",
        "schema_version",
        "summary",
    }
    if set(receipt) != expected_top:
        raise ArtifactScanError("AP compatibility receipt has unexpected fields")
    if (
        receipt.get("format") != AP_COMPATIBILITY_FORMAT
        or receipt.get("schema_version") != 1
        or receipt.get("canonicalization_id") != CANONICALIZATION_ID
        or receipt.get("boundaries") != _BOUNDARIES
        or receipt.get("limitations") != _LIMITATIONS
    ):
        raise ArtifactScanError("AP compatibility receipt constants are invalid")
    material = {key: value for key, value in receipt.items() if key != "receipt_id"}
    if receipt.get("receipt_id") != _content_id(_RECEIPT_ID_PREFIX, material):
        raise ArtifactScanError("AP compatibility receipt content ID is wrong")
    artifact = receipt.get("artifact")
    if not isinstance(artifact, Mapping) or set(artifact) != {
        "artifact_id", "file_name", "sha256", "size_bytes"
    }:
        raise ArtifactScanError("AP compatibility artifact binding is malformed")
    artifact_material = {
        key: value for key, value in artifact.items() if key != "artifact_id"
    }
    if (
        artifact.get("artifact_id")
        != _content_id(_ARTIFACT_ID_PREFIX, artifact_material)
        or not isinstance(artifact.get("file_name"), str)
        or not artifact["file_name"]
        or not isinstance(artifact.get("sha256"), str)
        or _SHA256_RE.fullmatch(artifact["sha256"]) is None
        or type(artifact.get("size_bytes")) is not int
        or artifact["size_bytes"] < 0
    ):
        raise ArtifactScanError("AP compatibility artifact binding is invalid")
    declarations = _validate_records(
        receipt.get("declarations"),
        "declaration_id",
        _DECLARATION_ID_PREFIX,
        "declarations",
    )
    expected_entries = _validate_records(
        receipt.get("expected_entries"),
        "entry_id",
        _EXPECTED_ENTRY_ID_PREFIX,
        "expected entries",
    )
    actual_entries = _validate_records(
        receipt.get("actual_entries"),
        "entry_id",
        _ACTUAL_ENTRY_ID_PREFIX,
        "actual entries",
    )
    issues = _validate_records(
        receipt.get("issues"), "issue_id", _ISSUE_ID_PREFIX, "issues"
    )
    for row in expected_entries:
        if set(row) != {"entry_id", "key", "source_kind", "source_locator", "version"}:
            raise ArtifactScanError("AP compatibility expected-entry shape is invalid")
        try:
            canonical_version = _normalize_version(row.get("version"))
        except ValueError as exc:
            raise ArtifactScanError("AP compatibility expected-entry version is invalid") from exc
        if (
            not isinstance(row.get("key"), str)
            or not row["key"]
            or row.get("source_kind")
            not in {"class-annotation", "member-annotation", "policy-default"}
            or not isinstance(row.get("source_locator"), str)
            or not row["source_locator"]
            or canonical_version != row["version"]
        ):
            raise ArtifactScanError("AP compatibility expected entry is invalid")
    actual_keys: list[str] = []
    for row in actual_entries:
        if set(row) != {"entry_id", "key", "version"}:
            raise ArtifactScanError("AP compatibility actual-entry shape is invalid")
        actual_keys.append(row.get("key"))
        try:
            canonical_version = _normalize_version(row.get("version"))
        except ValueError as exc:
            raise ArtifactScanError("AP compatibility actual-entry version is invalid") from exc
        if (
            not isinstance(row.get("key"), str)
            or not row["key"]
            or canonical_version != row["version"]
        ):
            raise ArtifactScanError("AP compatibility actual entry is invalid")
    if len(actual_keys) != len(set(actual_keys)):
        raise ArtifactScanError("AP compatibility actual entries repeat a key")
    for row in issues:
        if (
            set(row) != {"issue_id", "issue_kind", "key", "locator", "observed"}
            or row.get("issue_kind") not in _ISSUE_KINDS
            or (row.get("key") is not None and not isinstance(row["key"], str))
            or not isinstance(row.get("locator"), str)
            or not row["locator"]
            or not isinstance(row.get("observed"), Mapping)
        ):
            raise ArtifactScanError("AP compatibility issue shape is invalid")
    class_names: list[str] = []
    for row in declarations:
        required = {
            "annotation_attribute",
            "class_name",
            "class_version_source",
            "declaration_id",
            "declared_class_version",
            "effective_class_version",
            "entry_path",
            "entry_sha256",
            "member_overrides",
        }
        if set(row) != required:
            raise ArtifactScanError("AP compatibility declaration shape is invalid")
        class_names.append(row["class_name"])
        logical = _MULTI_RELEASE_RE.fullmatch(str(row.get("entry_path")))
        logical_path = logical.group("logical") if logical else row.get("entry_path")
        expected_path = str(row.get("class_name", "")).replace(".", "/") + ".class"
        try:
            effective_version = _normalize_version(row["effective_class_version"])
        except ValueError as exc:
            raise ArtifactScanError("AP compatibility declaration version is invalid") from exc
        if (
            not isinstance(row["class_name"], str)
            or not row["class_name"]
            or any(token in row["class_name"] for token in ("/", ";", "["))
            or any(not part for part in row["class_name"].split("."))
            or row["class_version_source"] not in {"class-annotation", "policy-default"}
            or effective_version != row["effective_class_version"]
            or not isinstance(row["entry_path"], str)
            or not row["entry_path"]
            or logical_path != expected_path
            or _SHA256_RE.fullmatch(str(row["entry_sha256"])) is None
            or not isinstance(row["member_overrides"], list)
        ):
            raise ArtifactScanError("AP compatibility declaration is invalid")
        if row["class_version_source"] == "policy-default":
            if row["declared_class_version"] is not None or row["annotation_attribute"] is not None:
                raise ArtifactScanError("AP compatibility default declaration is inconsistent")
        elif (
            not isinstance(row["declared_class_version"], str)
            or row["annotation_attribute"] not in _ANNOTATION_ATTRIBUTES
        ):
            raise ArtifactScanError("AP compatibility annotated declaration is inconsistent")
        else:
            try:
                declared_version = _normalize_version(row["declared_class_version"])
            except ValueError as exc:
                raise ArtifactScanError(
                    "AP compatibility annotated declaration is invalid"
                ) from exc
            if declared_version != row["effective_class_version"]:
                raise ArtifactScanError(
                    "AP compatibility annotated declaration is inconsistent"
                )
        override_keys: list[str] = []
        for member in row["member_overrides"]:
            if not isinstance(member, Mapping) or set(member) != {
                "annotation_attribute",
                "declared_version",
                "descriptor",
                "effective_version",
                "key",
                "kind",
                "member_name",
            }:
                raise ArtifactScanError("AP compatibility member override shape is invalid")
            override_keys.append(member["key"])
            separator = "" if member["kind"] == "method" else ":"
            expected_key = (
                f"{row['class_name']}::{member['member_name']}"
                f"{separator}{member['descriptor']}"
            )
            try:
                declared_version = _normalize_version(member.get("declared_version"))
            except ValueError as exc:
                raise ArtifactScanError(
                    "AP compatibility member override version is invalid"
                ) from exc
            if (
                member["kind"] not in {"field", "method"}
                or member["key"] != expected_key
                or member["annotation_attribute"] not in _ANNOTATION_ATTRIBUTES
                or not isinstance(member.get("member_name"), str)
                or not member["member_name"]
                or not isinstance(member.get("descriptor"), str)
                or not member["descriptor"]
                or (
                    not _valid_method_descriptor(member["descriptor"])
                    if member["kind"] == "method"
                    else not _valid_field_descriptor(member["descriptor"])
                )
                or declared_version != member["effective_version"]
            ):
                raise ArtifactScanError("AP compatibility member override is invalid")
        if override_keys != sorted(set(override_keys)):
            raise ArtifactScanError("AP compatibility member overrides are not key-sorted")
    if len(class_names) != len(set(class_names)):
        raise ArtifactScanError("AP compatibility receipt repeats a Mixin class")
    derived_expected = _expected_entries(declarations)
    if derived_expected != expected_entries:
        raise ArtifactScanError("AP compatibility expected entries are not reproducible")
    metadata = receipt.get("metadata_resource")
    if not isinstance(metadata, Mapping) or set(metadata) != {
        "error",
        "invalid_entries",
        "parse_state",
        "path",
        "present",
        "sha256",
        "size_bytes",
    }:
        raise ArtifactScanError("AP compatibility metadata binding is malformed")
    if metadata.get("parse_state") not in _PARSE_STATES:
        raise ArtifactScanError("AP compatibility metadata parse state is invalid")
    if metadata["parse_state"] == "missing":
        if metadata != {
            "error": None,
            "invalid_entries": [],
            "parse_state": "missing",
            "path": metadata["path"],
            "present": False,
            "sha256": None,
            "size_bytes": None,
        }:
            raise ArtifactScanError("AP compatibility missing metadata binding is inconsistent")
    elif (
        metadata.get("present") is not True
        or not isinstance(metadata.get("sha256"), str)
        or _SHA256_RE.fullmatch(metadata["sha256"]) is None
        or type(metadata.get("size_bytes")) is not int
        or metadata["size_bytes"] < 0
    ):
        raise ArtifactScanError("AP compatibility present metadata binding is invalid")
    if not isinstance(metadata.get("path"), str) or not metadata["path"]:
        raise ArtifactScanError("AP compatibility metadata path is invalid")
    if metadata["parse_state"] == "exact" and metadata.get("error") is not None:
        raise ArtifactScanError("AP compatibility exact metadata has an error")
    if metadata["parse_state"] in {"invalid-json", "invalid-root"} and (
        not isinstance(metadata.get("error"), str)
        or not metadata["error"]
        or metadata.get("invalid_entries") != []
        or actual_entries
    ):
        raise ArtifactScanError("AP compatibility invalid metadata is inconsistent")
    if metadata["parse_state"] == "missing" and actual_entries:
        raise ArtifactScanError("AP compatibility missing metadata has actual entries")
    invalid_entries = metadata.get("invalid_entries")
    if not isinstance(invalid_entries, list) or any(
        not isinstance(row, Mapping)
        or set(row) != {"key", "observed", "reason"}
        or not isinstance(row["key"], str)
        or not isinstance(row["reason"], str)
        or not row["reason"]
        for row in invalid_entries
    ):
        raise ArtifactScanError("AP compatibility invalid-entry facts are malformed")
    invalid_keys = [row["key"] for row in invalid_entries]
    if invalid_keys != sorted(invalid_keys) or len(invalid_keys) != len(set(invalid_keys)):
        raise ArtifactScanError("AP compatibility invalid-entry facts are not key-sorted")
    if set(invalid_keys) & set(actual_keys):
        raise ArtifactScanError("AP compatibility metadata key is both valid and invalid")
    policy = receipt.get("policy")
    if not isinstance(policy, Mapping) or set(policy) != {
        "compatibility_annotation_descriptor",
        "default_class_compatibility",
        "member_key_encoding",
        "metadata_resource",
        "mixin_annotation_descriptor",
        "policy_id",
        "sha256",
        "source_revision",
    }:
        raise ArtifactScanError("AP compatibility policy binding is malformed")
    if (
        policy.get("member_key_encoding") != "cleanmix-class-member-v1"
        or policy.get("metadata_resource") != metadata.get("path")
        or not isinstance(policy.get("policy_id"), str)
        or not policy["policy_id"]
        or _DESCRIPTOR_RE.fullmatch(str(policy.get("mixin_annotation_descriptor"))) is None
        or _DESCRIPTOR_RE.fullmatch(str(policy.get("compatibility_annotation_descriptor"))) is None
        or _SHA256_RE.fullmatch(str(policy.get("sha256"))) is None
        or _REVISION_RE.fullmatch(str(policy.get("source_revision"))) is None
    ):
        raise ArtifactScanError("AP compatibility policy binding is invalid")
    try:
        _resource_path(policy["metadata_resource"])
    except ArtifactScanError as exc:
        raise ArtifactScanError("AP compatibility policy resource path is invalid") from exc
    try:
        policy_default = _normalize_version(policy.get("default_class_compatibility"))
    except ValueError as exc:
        raise ArtifactScanError("AP compatibility policy default is invalid") from exc
    if policy_default != policy["default_class_compatibility"]:
        raise ArtifactScanError("AP compatibility policy default is not canonical")
    if any(
        row["class_version_source"] == "policy-default"
        and row["effective_class_version"] != policy_default
        for row in declarations
    ):
        raise ArtifactScanError("AP compatibility declaration does not use policy default")
    derived_issues = _issues(declarations, expected_entries, actual_entries, metadata)
    if derived_issues != issues:
        raise ArtifactScanError("AP compatibility issues are not reproducible")
    state = (
        "nonconformant"
        if issues
        else "not-applicable"
        if not declarations
        else "conformant"
    )
    expected_summary = {
        "actual_entry_count": len(actual_entries),
        "conformance_state": state,
        "declared_mixin_count": len(declarations),
        "expected_entry_count": len(expected_entries),
        "issue_count": len(issues),
        "issue_counts": {
            kind: sum(row["issue_kind"] == kind for row in issues)
            for kind in sorted(_ISSUE_KINDS)
        },
        "member_override_count": sum(
            len(row["member_overrides"]) for row in declarations
        ),
    }
    if receipt.get("summary") != expected_summary or state not in _CONFORMANCE_STATES:
        raise ArtifactScanError("AP compatibility summary is wrong")


def validate_bound_ap_compatibility_conformance_receipt(
    receipt: Mapping[str, Any], artifact: ArtifactInput, policy_bytes: bytes
) -> None:
    """Re-scan exact artifact and policy bytes and require byte-for-byte facts."""

    validate_ap_compatibility_conformance_receipt(receipt)
    expected = build_ap_compatibility_conformance_receipt(artifact, policy_bytes)
    if receipt != expected:
        raise ArtifactScanError(
            "AP compatibility receipt does not match the bound artifact and policy"
        )


def render_ap_compatibility_conformance_receipt(
    receipt: Mapping[str, Any], *, compact: bool = False
) -> bytes:
    validate_ap_compatibility_conformance_receipt(receipt)
    if compact:
        return canonical_json_bytes(receipt) + b"\n"
    return (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
