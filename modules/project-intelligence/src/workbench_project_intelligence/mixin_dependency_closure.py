"""Exact, declared dependency closure and JVM class-header resolution facts.

This is a compatible supplement to the Mixin topology receipt V1. It does not
change that receipt's scan shape or identity semantics.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from io import BytesIO
import hashlib
import json
from pathlib import PurePosixPath
import re
import struct
from typing import Any, Iterable, Mapping, Sequence
from zipfile import BadZipFile, LargeZipFile, ZipFile

from .mixin_topology import (
    ArtifactScanError,
    MAX_ARCHIVE_BYTES,
    _inventory_archive,
    _read_member,
    canonical_json_bytes,
)


DEPENDENCY_CLOSURE_FORMAT = (
    "workbench-project-intelligence-mixin-dependency-closure-receipt-v1"
)
DEPENDENCY_CLOSURE_ID_PREFIX = (
    "workbench-mixin-dependency-closure-receipt:sha256:"
)
DEPENDENCY_ARTIFACT_ID_PREFIX = "workbench-mixin-dependency-artifact:sha256:"
DEPENDENCY_EDGE_ID_PREFIX = "workbench-mixin-dependency-edge:sha256:"
CLASS_HEADER_ID_PREFIX = "workbench-jvm-class-header:sha256:"
CLASS_RESOLUTION_ID_PREFIX = "workbench-jvm-class-resolution:sha256:"
CANONICALIZATION_ID = "workbench-canonical-json-v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CLASS_NAME_RE = re.compile(
    r"^[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*$"
)
_HEADER_CLASS_NAME_RE = re.compile(r"^[^.;\[/]+(?:\.[^.;\[/]+)*$")
_MULTI_RELEASE_RE = re.compile(
    r"^META-INF/versions/[1-9][0-9]*/(?P<logical>.+)$"
)
_ARTIFACT_ROLES = {
    "root",
    "required-runtime",
    "optional-runtime",
    "provided-runtime",
    "compile-only",
    "observation-only",
}
_RELATIONSHIPS = {
    "required-runtime",
    "optional-runtime",
    "provided-runtime",
    "compile-only",
    "observation-only",
}
_RUNTIME_RELATIONSHIPS = {
    "required-runtime",
    "optional-runtime",
    "provided-runtime",
}
_PURPOSES = {"config-plugin", "mixin-connector", "class-resolution"}
_RESOLUTION_STATES = {"resolved", "missing", "unresolved", "ambiguous"}

_RECEIPT_KEYS = {
    "artifacts",
    "boundaries",
    "canonicalization_id",
    "class_headers",
    "closure",
    "dependencies",
    "format",
    "limitations",
    "receipt_id",
    "resolutions",
    "schema_version",
    "summary",
}
_BOUNDARIES = {
    "caller_assertion_is_dependency_metadata_proof": False,
    "candidate_lock_v1_mutated": False,
    "runtime_classloader_order_proved": False,
    "static_class_resolution_only": True,
}
_ARTIFACT_KEYS = {
    "artifact_id",
    "class_count",
    "class_inventory_sha256",
    "file_name",
    "role",
    "sha256",
    "size_bytes",
}
_DEPENDENCY_KEYS = {
    "edge_id",
    "relationship",
    "source_artifact_sha256",
    "target_artifact_sha256",
}
_HEADER_KEYS = {
    "access_flags",
    "artifact_sha256",
    "class_file_major",
    "class_file_minor",
    "class_name",
    "direct_interfaces",
    "entry_path",
    "entry_sha256",
    "header_id",
    "path_state",
    "superclass_name",
}
_RESOLUTION_KEYS = {
    "class_name",
    "providers",
    "purpose",
    "requester_artifact_sha256",
    "resolution_id",
    "resolution_state",
    "selected_header_id",
    "selected_path",
}
_CLOSURE_KEYS = {
    "artifact_set_sha256",
    "closure_state",
    "completeness_assertion",
    "dependency_set_sha256",
    "request_set_sha256",
    "root_artifact_sha256s",
}


@dataclass(frozen=True)
class ClosureArtifactInput:
    label: str
    data: bytes
    role: str


@dataclass(frozen=True)
class DependencyEdgeInput:
    source_label: str
    target_label: str
    relationship: str


@dataclass(frozen=True)
class ClassResolutionRequest:
    requester_label: str
    class_name: str
    purpose: str


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    return prefix + _sha256(canonical_json_bytes(value))


def _logical_class_path(entry_path: str) -> str:
    match = _MULTI_RELEASE_RE.fullmatch(entry_path)
    return match.group("logical") if match is not None else entry_path


def _class_header(raw: bytes, context: str) -> dict[str, Any]:
    """Decode the bounded header fields needed for class-resolution closure."""

    position = 0

    def take(size: int) -> bytes:
        nonlocal position
        if position + size > len(raw):
            raise ArtifactScanError(f"{context} has a truncated class header")
        value = raw[position : position + size]
        position += size
        return value

    def u1() -> int:
        return take(1)[0]

    def u2() -> int:
        return struct.unpack(">H", take(2))[0]

    if take(4) != b"\xca\xfe\xba\xbe":
        raise ArtifactScanError(f"{context} has an invalid class magic")
    minor, major = struct.unpack(">HH", take(4))
    pool_count = u2()
    pool: list[tuple[str, Any] | None] = [None] * pool_count
    index = 1
    while index < pool_count:
        tag = u1()
        if tag == 1:
            length = u2()
            pool[index] = ("utf8", take(length))
        elif tag in {3, 4}:
            take(4)
        elif tag in {5, 6}:
            take(8)
            index += 1
        elif tag == 7:
            pool[index] = ("class", u2())
        elif tag == 8:
            take(2)
        elif tag in {9, 10, 11, 12, 17, 18}:
            take(4)
        elif tag == 15:
            take(3)
        elif tag in {16, 19, 20}:
            take(2)
        else:
            raise ArtifactScanError(
                f"{context} has unknown class constant tag {tag}"
            )
        index += 1

    def class_name(class_index: int, field: str) -> str:
        try:
            class_entry = pool[class_index]
            if class_entry is None or class_entry[0] != "class":
                raise IndexError
            name_entry = pool[class_entry[1]]
            if name_entry is None or name_entry[0] != "utf8":
                raise IndexError
            internal = name_entry[1].decode("utf-8")
        except (IndexError, TypeError, UnicodeError, AttributeError):
            raise ArtifactScanError(
                f"{context} has an invalid {field} class reference"
            ) from None
        dotted = internal.replace("/", ".")
        if _HEADER_CLASS_NAME_RE.fullmatch(dotted) is None:
            raise ArtifactScanError(
                f"{context} has invalid {field} class name {internal!r}"
            )
        return dotted

    access_flags = u2()
    this_index = u2()
    super_index = u2()
    interface_indices = [u2() for _ in range(u2())]
    interfaces = sorted(class_name(item, "interface") for item in interface_indices)
    if len(interfaces) != len(set(interfaces)):
        raise ArtifactScanError(f"{context} repeats a direct interface")
    return {
        "access_flags": access_flags,
        "class_file_major": major,
        "class_file_minor": minor,
        "class_name": class_name(this_index, "this"),
        "direct_interfaces": interfaces,
        "superclass_name": (
            None if super_index == 0 else class_name(super_index, "superclass")
        ),
    }


def _scan_closure_artifact(item: ClosureArtifactInput) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(item.label, str) or not item.label or "\x00" in item.label:
        raise ArtifactScanError("closure artifact label must be a non-empty string")
    if not isinstance(item.data, bytes):
        raise TypeError("closure artifact data must be bytes")
    if item.role not in _ARTIFACT_ROLES:
        raise ArtifactScanError(f"unsupported closure artifact role {item.role!r}")
    if len(item.data) > MAX_ARCHIVE_BYTES:
        raise ArtifactScanError(
            f"archive exceeds {MAX_ARCHIVE_BYTES} input bytes: {item.label}"
        )
    artifact_sha256 = _sha256(item.data)
    headers: list[dict[str, Any]] = []
    try:
        with ZipFile(BytesIO(item.data), "r", allowZip64=True) as archive:
            members, _, _ = _inventory_archive(archive)
            for entry_path, info in sorted(members.items()):
                if info.is_dir() or not entry_path.endswith(".class"):
                    continue
                raw = _read_member(archive, info, f"class {entry_path}")
                material = {
                    "artifact_sha256": artifact_sha256,
                    "entry_path": entry_path,
                    "entry_sha256": _sha256(raw),
                    **_class_header(raw, f"class {entry_path}"),
                }
                expected_path = material["class_name"].replace(".", "/") + ".class"
                material["path_state"] = (
                    "exact" if _logical_class_path(entry_path) == expected_path else "mismatch"
                )
                header = dict(material)
                header["header_id"] = _content_id(CLASS_HEADER_ID_PREFIX, material)
                headers.append(header)
    except (BadZipFile, LargeZipFile, OSError, EOFError) as exc:
        raise ArtifactScanError(
            f"{item.label} is not a valid ZIP archive: {exc}"
        ) from exc
    headers.sort(key=lambda row: row["header_id"])
    inventory_material = [
        {key: value for key, value in row.items() if key != "header_id"}
        for row in headers
    ]
    artifact_material = {
        "class_count": len(headers),
        "class_inventory_sha256": _sha256(canonical_json_bytes(inventory_material)),
        "file_name": item.label,
        "role": item.role,
        "sha256": artifact_sha256,
        "size_bytes": len(item.data),
    }
    artifact = dict(artifact_material)
    artifact["artifact_id"] = _content_id(
        DEPENDENCY_ARTIFACT_ID_PREFIX, artifact_material
    )
    return artifact, headers


def _runtime_adjacency(
    artifact_sha256s: set[str], dependencies: Sequence[Mapping[str, Any]]
) -> dict[str, list[str]]:
    adjacency = {digest: [] for digest in artifact_sha256s}
    for edge in dependencies:
        if edge["relationship"] in _RUNTIME_RELATIONSHIPS:
            adjacency[edge["source_artifact_sha256"]].append(
                edge["target_artifact_sha256"]
            )
    for values in adjacency.values():
        values.sort()
    return adjacency


def _shortest_paths(adjacency: Mapping[str, Sequence[str]], start: str) -> dict[str, list[str]]:
    paths = {start: [start]}
    queue: deque[str] = deque([start])
    while queue:
        current = queue.popleft()
        for target in adjacency[current]:
            candidate = paths[current] + [target]
            if target not in paths:
                paths[target] = candidate
                queue.append(target)
            elif len(candidate) == len(paths[target]) and candidate < paths[target]:
                paths[target] = candidate
    return paths


def _resolution_material(
    *,
    requester_sha256: str,
    class_name: str,
    purpose: str,
    closure_complete: bool,
    headers: Sequence[Mapping[str, Any]],
    adjacency: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    paths = _shortest_paths(adjacency, requester_sha256)
    providers = [
        {
            "artifact_sha256": row["artifact_sha256"],
            "header_id": row["header_id"],
            "resolution_path": paths[row["artifact_sha256"]],
        }
        for row in headers
        if row["path_state"] == "exact"
        and row["class_name"] == class_name
        and row["artifact_sha256"] in paths
    ]
    providers.sort(key=lambda row: (row["artifact_sha256"], row["header_id"]))
    if len(providers) == 1:
        state = "resolved"
        selected_header_id = providers[0]["header_id"]
        selected_path = providers[0]["resolution_path"]
    elif len(providers) > 1:
        state = "ambiguous"
        selected_header_id = None
        selected_path = None
    elif closure_complete:
        state = "missing"
        selected_header_id = None
        selected_path = None
    else:
        state = "unresolved"
        selected_header_id = None
        selected_path = None
    return {
        "class_name": class_name,
        "providers": providers,
        "purpose": purpose,
        "requester_artifact_sha256": requester_sha256,
        "resolution_state": state,
        "selected_header_id": selected_header_id,
        "selected_path": selected_path,
    }


def build_dependency_closure_receipt(
    artifacts: Iterable[ClosureArtifactInput],
    dependencies: Iterable[DependencyEdgeInput],
    requests: Iterable[ClassResolutionRequest],
    *,
    closure_complete: bool,
) -> dict[str, Any]:
    """Build a deterministic receipt from exact bytes and caller-declared edges."""

    if not isinstance(closure_complete, bool):
        raise TypeError("closure_complete must be a boolean")
    scanned = [_scan_closure_artifact(item) for item in artifacts]
    if not scanned:
        raise ArtifactScanError("at least one closure artifact is required")
    artifact_rows = sorted((row for row, _ in scanned), key=lambda row: row["artifact_id"])
    header_rows = sorted(
        (header for _, headers in scanned for header in headers),
        key=lambda row: row["header_id"],
    )
    labels = [row["file_name"] for row in artifact_rows]
    digests = [row["sha256"] for row in artifact_rows]
    if len(labels) != len(set(labels)):
        raise ArtifactScanError("dependency closure repeats an artifact label")
    if len(digests) != len(set(digests)):
        raise ArtifactScanError("dependency closure repeats exact artifact bytes")
    by_label = {row["file_name"]: row for row in artifact_rows}
    roots = sorted(row["sha256"] for row in artifact_rows if row["role"] == "root")
    if not roots:
        raise ArtifactScanError("dependency closure requires at least one root artifact")

    edge_rows: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str]] = set()
    for edge in dependencies:
        if edge.relationship not in _RELATIONSHIPS:
            raise ArtifactScanError(
                f"unsupported dependency relationship {edge.relationship!r}"
            )
        if edge.source_label not in by_label or edge.target_label not in by_label:
            raise ArtifactScanError("dependency edge references an unknown artifact label")
        source = by_label[edge.source_label]["sha256"]
        target = by_label[edge.target_label]["sha256"]
        if source == target:
            raise ArtifactScanError("dependency edge cannot reference itself")
        if (source, target) in seen_edges:
            raise ArtifactScanError("dependency closure repeats a directed edge")
        seen_edges.add((source, target))
        material = {
            "relationship": edge.relationship,
            "source_artifact_sha256": source,
            "target_artifact_sha256": target,
        }
        row = dict(material)
        row["edge_id"] = _content_id(DEPENDENCY_EDGE_ID_PREFIX, material)
        edge_rows.append(row)
    edge_rows.sort(key=lambda row: row["edge_id"])

    request_rows: list[tuple[str, str, str]] = []
    for request in requests:
        if request.requester_label not in by_label:
            raise ArtifactScanError("class request references an unknown requester")
        if _CLASS_NAME_RE.fullmatch(request.class_name) is None:
            raise ArtifactScanError(
                f"class request has invalid class name {request.class_name!r}"
            )
        if request.purpose not in _PURPOSES:
            raise ArtifactScanError(
                f"class request has unsupported purpose {request.purpose!r}"
            )
        request_rows.append(
            (
                by_label[request.requester_label]["sha256"],
                request.class_name,
                request.purpose,
            )
        )
    if len(request_rows) != len(set(request_rows)):
        raise ArtifactScanError("dependency closure repeats a class request")
    request_rows.sort()

    artifact_sha256s = set(digests)
    adjacency = _runtime_adjacency(artifact_sha256s, edge_rows)
    resolutions: list[dict[str, Any]] = []
    for requester, class_name, purpose in request_rows:
        material = _resolution_material(
            requester_sha256=requester,
            class_name=class_name,
            purpose=purpose,
            closure_complete=closure_complete,
            headers=header_rows,
            adjacency=adjacency,
        )
        row = dict(material)
        row["resolution_id"] = _content_id(CLASS_RESOLUTION_ID_PREFIX, material)
        resolutions.append(row)
    resolutions.sort(key=lambda row: row["resolution_id"])

    closure = {
        "artifact_set_sha256": _sha256(
            canonical_json_bytes(
                [
                    {
                        "file_name": row["file_name"],
                        "role": row["role"],
                        "sha256": row["sha256"],
                        "size_bytes": row["size_bytes"],
                    }
                    for row in sorted(
                        artifact_rows, key=lambda row: (row["sha256"], row["file_name"])
                    )
                ]
            )
        ),
        "closure_state": "complete" if closure_complete else "incomplete",
        "completeness_assertion": (
            "caller-declared-complete"
            if closure_complete
            else "caller-declared-incomplete"
        ),
        "dependency_set_sha256": _sha256(
            canonical_json_bytes(
                [
                    {key: value for key, value in row.items() if key != "edge_id"}
                    for row in edge_rows
                ]
            )
        ),
        "request_set_sha256": _sha256(
            canonical_json_bytes(
                [
                    {
                        "class_name": class_name,
                        "purpose": purpose,
                        "requester_artifact_sha256": requester,
                    }
                    for requester, class_name, purpose in request_rows
                ]
            )
        ),
        "root_artifact_sha256s": roots,
    }
    material = {
        "artifacts": artifact_rows,
        "boundaries": {
            "caller_assertion_is_dependency_metadata_proof": False,
            "candidate_lock_v1_mutated": False,
            "runtime_classloader_order_proved": False,
            "static_class_resolution_only": True,
        },
        "canonicalization_id": CANONICALIZATION_ID,
        "class_headers": header_rows,
        "closure": closure,
        "dependencies": edge_rows,
        "format": DEPENDENCY_CLOSURE_FORMAT,
        "limitations": [
            "Closure completeness is a caller assertion and requires external dependency-metadata custody.",
            "Multiple reachable providers are ambiguous because runtime classpath order is not claimed.",
            "Multi-release variants remain separate providers without a bound runtime selection policy.",
        ],
        "resolutions": resolutions,
        "schema_version": 1,
        "summary": {
            "ambiguous_resolution_count": sum(
                row["resolution_state"] == "ambiguous" for row in resolutions
            ),
            "artifact_count": len(artifact_rows),
            "class_header_count": len(header_rows),
            "dependency_count": len(edge_rows),
            "missing_resolution_count": sum(
                row["resolution_state"] == "missing" for row in resolutions
            ),
            "request_count": len(resolutions),
            "resolved_count": sum(
                row["resolution_state"] == "resolved" for row in resolutions
            ),
            "unresolved_count": sum(
                row["resolution_state"] == "unresolved" for row in resolutions
            ),
        },
    }
    receipt = dict(material)
    receipt["receipt_id"] = _content_id(DEPENDENCY_CLOSURE_ID_PREFIX, material)
    validate_dependency_closure_receipt(receipt)
    return receipt


def validate_dependency_closure_receipt(receipt: Mapping[str, Any]) -> None:
    """Validate content identities, graph references, paths, and resolutions."""

    if set(receipt) != _RECEIPT_KEYS:
        raise ArtifactScanError("dependency closure has unexpected or missing fields")
    if (
        receipt.get("format") != DEPENDENCY_CLOSURE_FORMAT
        or receipt.get("schema_version") != 1
    ):
        raise ArtifactScanError("dependency closure has the wrong format or version")
    if receipt.get("canonicalization_id") != CANONICALIZATION_ID:
        raise ArtifactScanError("dependency closure canonicalization is unsupported")
    if receipt.get("boundaries") != _BOUNDARIES:
        raise ArtifactScanError("dependency closure boundaries are invalid")
    limitations = receipt.get("limitations")
    if (
        not isinstance(limitations, list)
        or not limitations
        or len(limitations) != len(set(limitations))
        or any(not isinstance(item, str) or not item for item in limitations)
    ):
        raise ArtifactScanError("dependency closure limitations are malformed")
    material = {key: value for key, value in receipt.items() if key != "receipt_id"}
    if receipt.get("receipt_id") != _content_id(DEPENDENCY_CLOSURE_ID_PREFIX, material):
        raise ArtifactScanError("dependency closure receipt content ID does not match")
    artifacts = receipt.get("artifacts")
    dependencies = receipt.get("dependencies")
    headers = receipt.get("class_headers")
    resolutions = receipt.get("resolutions")
    if not all(isinstance(rows, list) for rows in (artifacts, dependencies, headers, resolutions)):
        raise ArtifactScanError("dependency closure record arrays are malformed")

    def validate_records(
        rows: list[Any], id_field: str, prefix: str, label: str
    ) -> None:
        if any(not isinstance(row, Mapping) for row in rows):
            raise ArtifactScanError(f"dependency closure {label} contain a non-object")
        ids = [row.get(id_field) if isinstance(row, Mapping) else None for row in rows]
        if ids != sorted(ids) or len(ids) != len(set(ids)):
            raise ArtifactScanError(f"dependency closure {label} are not uniquely ID-sorted")
        for row in rows:
            row_material = {key: value for key, value in row.items() if key != id_field}
            if row.get(id_field) != _content_id(prefix, row_material):
                raise ArtifactScanError(f"dependency closure {label} content ID is wrong")

    validate_records(artifacts, "artifact_id", DEPENDENCY_ARTIFACT_ID_PREFIX, "artifacts")
    validate_records(dependencies, "edge_id", DEPENDENCY_EDGE_ID_PREFIX, "dependencies")
    validate_records(headers, "header_id", CLASS_HEADER_ID_PREFIX, "class headers")
    validate_records(resolutions, "resolution_id", CLASS_RESOLUTION_ID_PREFIX, "resolutions")

    if any(set(row) != _ARTIFACT_KEYS for row in artifacts):
        raise ArtifactScanError("dependency closure artifact shape is invalid")
    if any(set(row) != _DEPENDENCY_KEYS for row in dependencies):
        raise ArtifactScanError("dependency closure dependency shape is invalid")
    if any(set(row) != _HEADER_KEYS for row in headers):
        raise ArtifactScanError("dependency closure class-header shape is invalid")
    if any(set(row) != _RESOLUTION_KEYS for row in resolutions):
        raise ArtifactScanError("dependency closure resolution shape is invalid")

    artifact_sha256s = {row.get("sha256") for row in artifacts}
    if len(artifact_sha256s) != len(artifacts) or any(
        not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None
        for value in artifact_sha256s
    ):
        raise ArtifactScanError("dependency closure artifact SHA-256 identities are invalid")
    artifact_labels = [row.get("file_name") for row in artifacts]
    if (
        len(artifact_labels) != len(set(artifact_labels))
        or any(not isinstance(value, str) or not value or "\x00" in value for value in artifact_labels)
        or any(row.get("role") not in _ARTIFACT_ROLES for row in artifacts)
        or any(type(row.get("class_count")) is not int or row["class_count"] < 0 for row in artifacts)
        or any(type(row.get("size_bytes")) is not int or row["size_bytes"] < 0 for row in artifacts)
        or any(
            not isinstance(row.get("class_inventory_sha256"), str)
            or _SHA256_RE.fullmatch(row["class_inventory_sha256"]) is None
            for row in artifacts
        )
    ):
        raise ArtifactScanError("dependency closure artifact metadata is invalid")
    if not any(row.get("role") == "root" for row in artifacts):
        raise ArtifactScanError("dependency closure has no root artifact")
    for edge in dependencies:
        if edge.get("source_artifact_sha256") not in artifact_sha256s or edge.get(
            "target_artifact_sha256"
        ) not in artifact_sha256s:
            raise ArtifactScanError("dependency edge references an unknown artifact")
        if edge.get("relationship") not in _RELATIONSHIPS:
            raise ArtifactScanError("dependency edge has an invalid relationship")
    pairs = [
        (row["source_artifact_sha256"], row["target_artifact_sha256"])
        for row in dependencies
    ]
    if len(pairs) != len(set(pairs)):
        raise ArtifactScanError("dependency closure repeats a directed edge")
    header_ids = {row["header_id"] for row in headers}
    for row in headers:
        if row.get("artifact_sha256") not in artifact_sha256s:
            raise ArtifactScanError("class header references an unknown artifact")
        direct_interfaces = row.get("direct_interfaces")
        superclass = row.get("superclass_name")
        if (
            any(
                type(row.get(field)) is not int or not 0 <= row[field] <= 65535
                for field in ("access_flags", "class_file_major", "class_file_minor")
            )
            or not isinstance(row.get("entry_path"), str)
            or not row["entry_path"]
            or not isinstance(row.get("entry_sha256"), str)
            or _SHA256_RE.fullmatch(row["entry_sha256"]) is None
            or not isinstance(row.get("class_name"), str)
            or _HEADER_CLASS_NAME_RE.fullmatch(row["class_name"]) is None
            or (
                superclass is not None
                and (
                    not isinstance(superclass, str)
                    or _HEADER_CLASS_NAME_RE.fullmatch(superclass) is None
                )
            )
            or not isinstance(direct_interfaces, list)
            or direct_interfaces != sorted(direct_interfaces)
            or len(direct_interfaces) != len(set(direct_interfaces))
            or any(
                not isinstance(value, str)
                or _HEADER_CLASS_NAME_RE.fullmatch(value) is None
                for value in direct_interfaces
            )
            or row.get("path_state") not in {"exact", "mismatch"}
        ):
            raise ArtifactScanError("dependency closure class-header metadata is invalid")
        expected_path = row["class_name"].replace(".", "/") + ".class"
        expected_state = (
            "exact" if _logical_class_path(row["entry_path"]) == expected_path else "mismatch"
        )
        if row["path_state"] != expected_state:
            raise ArtifactScanError("dependency closure class-header path state is wrong")
    for artifact in artifacts:
        selected_headers = [
            {key: value for key, value in row.items() if key != "header_id"}
            for row in headers
            if row["artifact_sha256"] == artifact["sha256"]
        ]
        if artifact.get("class_count") != len(selected_headers):
            raise ArtifactScanError("dependency artifact class_count is wrong")
        if artifact.get("class_inventory_sha256") != _sha256(
            canonical_json_bytes(selected_headers)
        ):
            raise ArtifactScanError("dependency artifact class inventory digest is wrong")

    closure = receipt.get("closure")
    if not isinstance(closure, Mapping) or set(closure) != _CLOSURE_KEYS:
        raise ArtifactScanError("dependency closure metadata is malformed")
    closure_complete = closure.get("closure_state") == "complete"
    if closure.get("closure_state") not in {"complete", "incomplete"}:
        raise ArtifactScanError("dependency closure state is invalid")
    expected_assertion = (
        "caller-declared-complete" if closure_complete else "caller-declared-incomplete"
    )
    if closure.get("completeness_assertion") != expected_assertion:
        raise ArtifactScanError("dependency closure completeness assertion is inconsistent")
    expected_roots = sorted(row["sha256"] for row in artifacts if row["role"] == "root")
    if closure.get("root_artifact_sha256s") != expected_roots:
        raise ArtifactScanError("dependency closure root set is wrong")
    expected_artifact_set = _sha256(
        canonical_json_bytes(
            [
                {
                    "file_name": row["file_name"],
                    "role": row["role"],
                    "sha256": row["sha256"],
                    "size_bytes": row["size_bytes"],
                }
                for row in sorted(
                    artifacts, key=lambda row: (row["sha256"], row["file_name"])
                )
            ]
        )
    )
    expected_dependency_set = _sha256(
        canonical_json_bytes(
            [
                {key: value for key, value in row.items() if key != "edge_id"}
                for row in dependencies
            ]
        )
    )
    if closure.get("artifact_set_sha256") != expected_artifact_set:
        raise ArtifactScanError("dependency closure artifact-set digest is wrong")
    if closure.get("dependency_set_sha256") != expected_dependency_set:
        raise ArtifactScanError("dependency closure dependency-set digest is wrong")

    adjacency = _runtime_adjacency(artifact_sha256s, dependencies)
    expected_resolutions: list[dict[str, Any]] = []
    request_keys: set[tuple[str, str, str]] = set()
    for row in resolutions:
        key = (
            row.get("requester_artifact_sha256"),
            row.get("class_name"),
            row.get("purpose"),
        )
        if key in request_keys:
            raise ArtifactScanError("dependency closure repeats a class request")
        request_keys.add(key)
        if (
            key[0] not in artifact_sha256s
            or not isinstance(key[1], str)
            or _CLASS_NAME_RE.fullmatch(key[1]) is None
            or key[2] not in _PURPOSES
            or row.get("resolution_state") not in _RESOLUTION_STATES
        ):
            raise ArtifactScanError("dependency closure class request is invalid")
        expected = _resolution_material(
            requester_sha256=key[0],
            class_name=key[1],
            purpose=key[2],
            closure_complete=closure_complete,
            headers=headers,
            adjacency=adjacency,
        )
        expected_row = dict(expected)
        expected_row["resolution_id"] = _content_id(CLASS_RESOLUTION_ID_PREFIX, expected)
        expected_resolutions.append(expected_row)
        if row.get("selected_header_id") is not None and row["selected_header_id"] not in header_ids:
            raise ArtifactScanError("class resolution selects an unknown header")
    expected_resolutions.sort(key=lambda row: row["resolution_id"])
    if expected_resolutions != resolutions:
        raise ArtifactScanError("dependency closure class resolutions are not reproducible")
    expected_request_set = _sha256(
        canonical_json_bytes(
            [
                {
                    "class_name": class_name,
                    "purpose": purpose,
                    "requester_artifact_sha256": requester,
                }
                for requester, class_name, purpose in sorted(request_keys)
            ]
        )
    )
    if closure.get("request_set_sha256") != expected_request_set:
        raise ArtifactScanError("dependency closure request-set digest is wrong")

    expected_summary = {
        "ambiguous_resolution_count": sum(
            row["resolution_state"] == "ambiguous" for row in resolutions
        ),
        "artifact_count": len(artifacts),
        "class_header_count": len(headers),
        "dependency_count": len(dependencies),
        "missing_resolution_count": sum(
            row["resolution_state"] == "missing" for row in resolutions
        ),
        "request_count": len(resolutions),
        "resolved_count": sum(
            row["resolution_state"] == "resolved" for row in resolutions
        ),
        "unresolved_count": sum(
            row["resolution_state"] == "unresolved" for row in resolutions
        ),
    }
    if receipt.get("summary") != expected_summary:
        raise ArtifactScanError("dependency closure summary is wrong")


def render_dependency_closure_receipt(
    receipt: Mapping[str, Any], *, compact: bool = False
) -> bytes:
    if compact:
        return canonical_json_bytes(receipt) + b"\n"
    return (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def find_class_resolution(
    receipt: Mapping[str, Any],
    *,
    requester_artifact_sha256: str,
    class_name: str,
    purpose: str,
) -> Mapping[str, Any] | None:
    matches = [
        row
        for row in receipt["resolutions"]
        if row["requester_artifact_sha256"] == requester_artifact_sha256
        and row["class_name"] == class_name
        and row["purpose"] == purpose
    ]
    if len(matches) > 1:
        raise ArtifactScanError("dependency closure repeats a class resolution key")
    return matches[0] if matches else None


def resolve_class_in_dependency_closure(
    receipt: Mapping[str, Any],
    *,
    requester_artifact_sha256: str,
    class_name: str,
    purpose: str = "class-resolution",
) -> dict[str, Any]:
    """Resolve an additional class query under an already validated closure."""

    validate_dependency_closure_receipt(receipt)
    if requester_artifact_sha256 not in {
        row["sha256"] for row in receipt["artifacts"]
    }:
        raise ArtifactScanError("class query requester is outside the closure")
    if _CLASS_NAME_RE.fullmatch(class_name) is None:
        raise ArtifactScanError(f"class query has invalid class name {class_name!r}")
    if purpose not in _PURPOSES:
        raise ArtifactScanError(f"class query has unsupported purpose {purpose!r}")
    adjacency = _runtime_adjacency(
        {row["sha256"] for row in receipt["artifacts"]},
        receipt["dependencies"],
    )
    return _resolution_material(
        requester_sha256=requester_artifact_sha256,
        class_name=class_name,
        purpose=purpose,
        closure_complete=receipt["closure"]["closure_state"] == "complete",
        headers=receipt["class_headers"],
        adjacency=adjacency,
    )
