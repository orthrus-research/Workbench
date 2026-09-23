"""Write, validate, rebuild, and query categorical Atlas graph bundles.

The JSONL partition streams and the manifest fields covered by ``graph_set_id``
are authoritative.  The SQLite database is a derived cache: authoritative
validation neither requires nor trusts it, and query use verifies it separately.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import sqlite3
import stat
import tempfile
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
from urllib.parse import quote


BUNDLE_FORMAT = "workbench-atlas-categorical-graph-bundle-v2"
OBSERVATION_BUNDLE_FORMAT = "workbench-atlas-categorical-graph-bundle-v3"
NODE_FORMAT = "workbench-atlas-categorical-node-v2"
EDGE_FORMAT = "workbench-atlas-categorical-edge-v2"
STRICT_VALIDATION_PROFILE = (
    "workbench-atlas-categorical-graph-declared-dependency-closure-v1"
)
LEGACY_VALIDATION_PROFILE = "legacy-v2-earlier-partition-closure"

_GRAPH_SET_ID_PREFIX = "workbench-atlas-graph-set-v2:sha256:"
_OBSERVATION_GRAPH_SET_ID_PREFIX = "workbench-atlas-graph-set-v3:sha256:"
_AUTHORITIES = {
    "crucible-occurrence-v1": {
        "owner": "Atlas",
        "claim": "derived categorical projection over retained Crucible occurrence evidence",
    },
    "retained-observations-v1": {
        "owner": "Atlas",
        "claim": "derived categorical projection over versioned retained observations",
    },
}
_PARTITION_ID_PREFIX = "workbench-atlas-graph-partition-v2:sha256:"
_QUERY_INDEX_FILE = "query-index.sqlite3"
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_MAX_JSONL_ROW_BYTES = 16 * 1024 * 1024
_INDEX_BATCH_SIZE = 2_048
_HEX_DIGITS = frozenset("0123456789abcdef")

_IndexProgress = Callable[[Mapping[str, Any]], None]

_LEGACY_MANIFEST_KEYS = {
    "format",
    "schema_version",
    "graph_set_id",
    "authority",
    "scope",
    "evidence_binding",
    "partitions",
    "summary",
    "query_index",
}
_STRICT_MANIFEST_KEYS = _LEGACY_MANIFEST_KEYS | {"validation_profile"}
_PARTITION_KEYS = {
    "partition_id",
    "classification",
    "dependencies",
    "evidence_categories",
    "nodes",
    "edges",
    "limitations",
    "partition_content_id",
}
_NODE_DESCRIPTOR_KEYS = {"file", "count", "size", "sha256", "kinds"}
_EDGE_DESCRIPTOR_KEYS = {"file", "count", "size", "sha256", "relations"}
_QUERY_INDEX_KEYS = {
    "role",
    "file",
    "size",
    "sha256",
    "derived_from_graph_set_id",
}
_NODE_KEYS = {
    "format",
    "schema_version",
    "id",
    "kind",
    "semantic_key",
    "properties",
    "evidence",
}
_EDGE_KEYS = {
    "format",
    "schema_version",
    "id",
    "relation",
    "source",
    "target",
    "semantic_key",
    "properties",
    "evidence",
}


class AtlasCategoricalGraphError(ValueError):
    """A categorical graph cannot be represented without ambiguity."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AtlasCategoricalGraphError(
            f"categorical graph value is not canonical JSON: {exc}"
        ) from exc


def _canonical_text(value: Any) -> str:
    return _canonical_bytes(value).decode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _required_text(value: object, label: str) -> str:
    if type(value) is not str or not value or any(c in value for c in "\r\n\x00"):
        raise AtlasCategoricalGraphError(f"{label} must be nonempty single-line text")
    return value


def _nonnegative_integer(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise AtlasCategoricalGraphError(f"{label} must be a nonnegative integer")
    return value


def _sha256(value: object, label: str) -> str:
    value = _required_text(value, label)
    if len(value) != 64 or any(character not in _HEX_DIGITS for character in value):
        raise AtlasCategoricalGraphError(f"{label} must be lowercase SHA-256 hex")
    return value


def _json_object(value: object, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise AtlasCategoricalGraphError(f"{label} must be an ordinary object")
    result = dict(value)
    _canonical_bytes(result)
    return result


def _evidence_rows(value: object) -> list[dict[str, Any]]:
    if type(value) not in (list, tuple):
        raise AtlasCategoricalGraphError("categorical graph evidence must be an array")
    result: list[dict[str, Any]] = []
    for raw in value:
        row = _json_object(raw, "categorical graph evidence")
        if not row:
            raise AtlasCategoricalGraphError("categorical graph evidence cannot be empty")
        result.append(row)
    return result


def _node_id(kind: str, semantic_key: str) -> str:
    return (
        "workbench-atlas-node-v2:"
        + quote(kind, safe="")
        + ":"
        + quote(semantic_key, safe="")
    )


def _edge_id(relation: str, source: str, target: str, semantic_key: str) -> str:
    identity = {
        "relation": relation,
        "source": source,
        "target": target,
        "semantic_key": semantic_key,
    }
    return "workbench-atlas-edge-v2:sha256:" + _sha256_bytes(
        _canonical_bytes(identity)
    )


def node_record(
    kind: str,
    semantic_key: str,
    properties: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build one semantic node whose identity is stable across observations."""

    kind = _required_text(kind, "categorical node kind")
    semantic_key = _required_text(semantic_key, "categorical node semantic key")
    return {
        "format": NODE_FORMAT,
        "schema_version": 2,
        "id": _node_id(kind, semantic_key),
        "kind": kind,
        "semantic_key": semantic_key,
        "properties": _json_object(properties, "categorical node properties"),
        "evidence": _evidence_rows(evidence),
    }


def edge_record(
    relation: str,
    source_id: str,
    target_id: str,
    properties: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]] = (),
    *,
    semantic_key: str = "singleton",
) -> dict[str, Any]:
    """Build one relation occurrence with content-independent endpoint IDs."""

    relation = _required_text(relation, "categorical edge relation")
    source_id = _required_text(source_id, "categorical edge source")
    target_id = _required_text(target_id, "categorical edge target")
    semantic_key = _required_text(semantic_key, "categorical edge semantic key")
    return {
        "format": EDGE_FORMAT,
        "schema_version": 2,
        "id": _edge_id(relation, source_id, target_id, semantic_key),
        "relation": relation,
        "source": source_id,
        "target": target_id,
        "semantic_key": semantic_key,
        "properties": _json_object(properties, "categorical edge properties"),
        "evidence": _evidence_rows(evidence),
    }


def _validate_node_record(row: object) -> dict[str, Any]:
    if type(row) is not dict or set(row) != _NODE_KEYS:
        raise AtlasCategoricalGraphError("categorical node fields differ")
    if (
        row.get("format") != NODE_FORMAT
        or type(row.get("schema_version")) is not int
        or row.get("schema_version") != 2
    ):
        raise AtlasCategoricalGraphError("categorical node format differs")
    kind = _required_text(row.get("kind"), "categorical node kind")
    semantic_key = _required_text(
        row.get("semantic_key"), "categorical node semantic key"
    )
    if row.get("id") != _node_id(kind, semantic_key):
        raise AtlasCategoricalGraphError("categorical node identity is invalid")
    _json_object(row.get("properties"), "categorical node properties")
    if type(row.get("evidence")) is not list:
        raise AtlasCategoricalGraphError("categorical node evidence must be an array")
    _evidence_rows(row["evidence"])
    return row


def _validate_edge_record(row: object) -> dict[str, Any]:
    if type(row) is not dict or set(row) != _EDGE_KEYS:
        raise AtlasCategoricalGraphError("categorical edge fields differ")
    if (
        row.get("format") != EDGE_FORMAT
        or type(row.get("schema_version")) is not int
        or row.get("schema_version") != 2
    ):
        raise AtlasCategoricalGraphError("categorical edge format differs")
    relation = _required_text(row.get("relation"), "categorical edge relation")
    source = _required_text(row.get("source"), "categorical edge source")
    target = _required_text(row.get("target"), "categorical edge target")
    semantic_key = _required_text(
        row.get("semantic_key"), "categorical edge semantic key"
    )
    if row.get("id") != _edge_id(relation, source, target, semantic_key):
        raise AtlasCategoricalGraphError("categorical edge identity is invalid")
    _json_object(row.get("properties"), "categorical edge properties")
    if type(row.get("evidence")) is not list:
        raise AtlasCategoricalGraphError("categorical edge evidence must be an array")
    _evidence_rows(row["evidence"])
    return row


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> tuple[int, str, int]:
    digest = hashlib.sha256()
    size = 0
    count = 0
    with path.open("xb") as stream:
        for raw in rows:
            encoded = _canonical_bytes(dict(raw)) + b"\n"
            stream.write(encoded)
            digest.update(encoded)
            size += len(encoded)
            count += 1
    return count, digest.hexdigest(), size


def _bundle_identity(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("graph_set_id", None)
    payload.pop("query_index", None)
    prefix = (_OBSERVATION_GRAPH_SET_ID_PREFIX
              if value.get("format") == OBSERVATION_BUNDLE_FORMAT else _GRAPH_SET_ID_PREFIX)
    return prefix + _sha256_bytes(_canonical_bytes(payload))


def _safe_relative_path(value: object, label: str, *, one_segment: bool = False) -> str:
    value = _required_text(value, label)
    if "\\" in value:
        raise AtlasCategoricalGraphError(f"{label} must use a safe relative POSIX path")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or posix.as_posix() != value
        or any(part in ("", ".", "..") for part in posix.parts)
        or (one_segment and len(posix.parts) != 1)
    ):
        raise AtlasCategoricalGraphError(f"{label} must use a safe relative POSIX path")
    return value


def _bundle_root(path: Path) -> Path:
    candidate = Path(path)
    try:
        if candidate.is_symlink():
            raise AtlasCategoricalGraphError("categorical graph bundle root cannot be a symlink")
        root = candidate.absolute()
        if not root.is_dir():
            raise AtlasCategoricalGraphError("categorical graph bundle root is not a directory")
    except OSError as exc:
        raise AtlasCategoricalGraphError(
            f"categorical graph bundle root cannot be inspected: {exc}"
        ) from exc
    return root


def _bundle_file(
    root: Path,
    relative: object,
    label: str,
    *,
    must_exist: bool = True,
) -> Path:
    relative = _safe_relative_path(relative, label)
    result = root.joinpath(*PurePosixPath(relative).parts)
    current = root
    try:
        for part in PurePosixPath(relative).parts:
            current = current / part
            try:
                mode = os.lstat(current).st_mode
            except FileNotFoundError:
                if must_exist or current != result:
                    raise AtlasCategoricalGraphError(f"{label} is missing")
                break
            if stat.S_ISLNK(mode):
                raise AtlasCategoricalGraphError(f"{label} cannot traverse a symlink")
        if must_exist:
            mode = os.lstat(result).st_mode
            if not stat.S_ISREG(mode):
                raise AtlasCategoricalGraphError(f"{label} must be a regular file")
    except AtlasCategoricalGraphError:
        raise
    except OSError as exc:
        raise AtlasCategoricalGraphError(f"{label} cannot be inspected: {exc}") from exc
    return result


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AtlasCategoricalGraphError(
                f"categorical graph JSON object duplicates field {key!r}"
            )
        result[key] = value
    return result


def _decode_json(data: bytes, label: str) -> Any:
    try:
        return json.loads(data, object_pairs_hook=_reject_duplicate_object)
    except AtlasCategoricalGraphError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AtlasCategoricalGraphError(f"{label} is not valid UTF-8 JSON: {exc}") from exc


def _manifest_profile(value: Mapping[str, Any]) -> str:
    keys = set(value)
    if keys == _LEGACY_MANIFEST_KEYS:
        return LEGACY_VALIDATION_PROFILE
    if keys != _STRICT_MANIFEST_KEYS:
        raise AtlasCategoricalGraphError("categorical graph manifest fields differ")
    if value.get("validation_profile") != STRICT_VALIDATION_PROFILE:
        raise AtlasCategoricalGraphError("categorical graph validation profile differs")
    return STRICT_VALIDATION_PROFILE


def _validate_count_map(value: object, label: str) -> dict[str, int]:
    if type(value) is not dict:
        raise AtlasCategoricalGraphError(f"{label} must be an ordinary object")
    result: dict[str, int] = {}
    for raw_key, raw_count in value.items():
        key = _required_text(raw_key, f"{label} key")
        result[key] = _nonnegative_integer(raw_count, f"{label} count")
    return result


def _validate_stream_descriptor(
    value: object, partition_id: str, family: str
) -> dict[str, Any]:
    expected_keys = _NODE_DESCRIPTOR_KEYS if family == "nodes" else _EDGE_DESCRIPTOR_KEYS
    if type(value) is not dict or set(value) != expected_keys:
        raise AtlasCategoricalGraphError(
            f"categorical graph {partition_id} {family} descriptor fields differ"
        )
    expected_file = f"{partition_id}/{family}.jsonl"
    if _safe_relative_path(
        value.get("file"), f"categorical graph {family} file"
    ) != expected_file:
        raise AtlasCategoricalGraphError(
            f"categorical graph {partition_id} {family} file differs"
        )
    count = _nonnegative_integer(
        value.get("count"), f"categorical graph {partition_id} {family} count"
    )
    _nonnegative_integer(
        value.get("size"), f"categorical graph {partition_id} {family} size"
    )
    _sha256(
        value.get("sha256"), f"categorical graph {partition_id} {family} digest"
    )
    count_key = "kinds" if family == "nodes" else "relations"
    counts = _validate_count_map(
        value.get(count_key),
        f"categorical graph {partition_id} {family} {count_key}",
    )
    if sum(counts.values()) != count:
        raise AtlasCategoricalGraphError(
            f"categorical graph {partition_id} {family} count summary differs"
        )
    return value


def _validate_query_descriptor(
    value: object, graph_set_id: str
) -> dict[str, Any] | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != _QUERY_INDEX_KEYS:
        raise AtlasCategoricalGraphError("categorical graph query index fields differ")
    if value.get("role") != "derived-disposable-index":
        raise AtlasCategoricalGraphError("categorical graph query index role differs")
    if _safe_relative_path(
        value.get("file"), "categorical graph query index file"
    ) != _QUERY_INDEX_FILE:
        raise AtlasCategoricalGraphError("categorical graph query index file differs")
    _nonnegative_integer(value.get("size"), "categorical graph query index size")
    _sha256(value.get("sha256"), "categorical graph query index digest")
    if value.get("derived_from_graph_set_id") != graph_set_id:
        raise AtlasCategoricalGraphError("categorical graph query index binding differs")
    return value


def validate_bundle_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the complete manifest shape without reading bundle files.

    Legacy manifests have no ``validation_profile`` field and retain their exact
    historical graph identities.  Newly built manifests carry the strict,
    identity-bearing declared-dependency validation profile.
    """

    if type(value) is not dict:
        raise AtlasCategoricalGraphError("categorical graph manifest must be an object")
    profile = _manifest_profile(value)
    observation_bundle = value.get("format") == OBSERVATION_BUNDLE_FORMAT
    if (
        value.get("format") not in {BUNDLE_FORMAT, OBSERVATION_BUNDLE_FORMAT}
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != (3 if observation_bundle else 2)
    ):
        raise AtlasCategoricalGraphError("categorical graph manifest format differs")
    if observation_bundle and profile != STRICT_VALIDATION_PROFILE:
        raise AtlasCategoricalGraphError("observation graph requires declared dependency closure")
    graph_set_id = _required_text(value.get("graph_set_id"), "categorical graph set ID")
    prefix = _OBSERVATION_GRAPH_SET_ID_PREFIX if observation_bundle else _GRAPH_SET_ID_PREFIX
    if not graph_set_id.startswith(prefix):
        raise AtlasCategoricalGraphError("categorical graph set identity differs")
    authority = value.get("authority")
    authority_key = "retained-observations-v1" if observation_bundle else "crucible-occurrence-v1"
    if authority != _AUTHORITIES[authority_key]:
        raise AtlasCategoricalGraphError("categorical graph authority differs")
    _json_object(value.get("scope"), "categorical graph scope")
    _json_object(value.get("evidence_binding"), "categorical graph evidence binding")

    partitions = value.get("partitions")
    if type(partitions) is not list or not partitions:
        raise AtlasCategoricalGraphError("categorical graph partitions must be nonempty")
    seen: set[str] = set()
    closures: dict[str, set[str]] = {}
    node_count = edge_count = 0
    for raw_partition in partitions:
        if type(raw_partition) is not dict or set(raw_partition) != _PARTITION_KEYS:
            raise AtlasCategoricalGraphError("categorical graph partition fields differ")
        partition = raw_partition
        partition_id = _safe_relative_path(
            partition.get("partition_id"),
            "categorical graph partition ID",
            one_segment=True,
        )
        if partition_id in seen:
            raise AtlasCategoricalGraphError("categorical graph partition identity differs")
        _required_text(
            partition.get("classification"), "categorical graph partition classification"
        )
        dependencies = partition.get("dependencies")
        if type(dependencies) is not list:
            raise AtlasCategoricalGraphError("categorical graph dependencies must be an array")
        checked_dependencies = [
            _required_text(item, "categorical graph partition dependency")
            for item in dependencies
        ]
        if (
            checked_dependencies != sorted(set(checked_dependencies))
            or not set(checked_dependencies).issubset(seen)
        ):
            raise AtlasCategoricalGraphError(
                "categorical graph partition dependencies must be closed, unique, and sorted"
            )
        closure = set(checked_dependencies)
        for dependency in checked_dependencies:
            closure.update(closures[dependency])
        closures[partition_id] = closure

        categories = partition.get("evidence_categories")
        if type(categories) is not list:
            raise AtlasCategoricalGraphError(
                "categorical graph partition evidence categories must be an array"
            )
        checked_categories = [
            _required_text(item, "categorical graph evidence category")
            for item in categories
        ]
        if not checked_categories or checked_categories != sorted(set(checked_categories)):
            raise AtlasCategoricalGraphError(
                "categorical graph partition evidence categories must be nonempty, unique, and sorted"
            )
        limitations = partition.get("limitations")
        if type(limitations) is not list:
            raise AtlasCategoricalGraphError(
                "categorical graph partition limitations must be an array"
            )
        for limitation in limitations:
            _required_text(limitation, "categorical graph partition limitation")

        nodes = _validate_stream_descriptor(partition.get("nodes"), partition_id, "nodes")
        edges = _validate_stream_descriptor(partition.get("edges"), partition_id, "edges")
        content_payload = dict(partition)
        content_id = content_payload.pop("partition_content_id")
        expected_content_id = _PARTITION_ID_PREFIX + _sha256_bytes(
            _canonical_bytes(content_payload)
        )
        if content_id != expected_content_id:
            raise AtlasCategoricalGraphError(
                "categorical graph partition content identity differs"
            )
        node_count += nodes["count"]
        edge_count += edges["count"]
        seen.add(partition_id)

    summary = value.get("summary")
    expected_summary = {
        "partition_count": len(partitions),
        "node_count": node_count,
        "edge_count": edge_count,
    }
    if type(summary) is not dict or set(summary) != set(expected_summary):
        raise AtlasCategoricalGraphError("categorical graph summary fields differ")
    for key in expected_summary:
        _nonnegative_integer(summary.get(key), f"categorical graph summary {key}")
    if summary != expected_summary:
        raise AtlasCategoricalGraphError("categorical graph summary is stale")
    _validate_query_descriptor(value.get("query_index"), graph_set_id)
    if graph_set_id != _bundle_identity(value):
        raise AtlasCategoricalGraphError("categorical graph set identity differs")
    return dict(value)


def _load_manifest(root: Path) -> dict[str, Any]:
    path = _bundle_file(root, "manifest.json", "categorical graph manifest")
    try:
        size = path.stat().st_size
        if size > _MAX_MANIFEST_BYTES:
            raise AtlasCategoricalGraphError("categorical graph manifest exceeds byte bound")
        raw = path.read_bytes()
    except AtlasCategoricalGraphError:
        raise
    except OSError as exc:
        raise AtlasCategoricalGraphError(
            f"categorical graph manifest cannot be read: {exc}"
        ) from exc
    return validate_bundle_manifest(_decode_json(raw, "categorical graph manifest"))


def _iter_canonical_jsonl(path: Path, label: str) -> Iterator[tuple[dict[str, Any], bytes]]:
    try:
        with path.open("rb") as stream:
            ordinal = 0
            while True:
                line = stream.readline(_MAX_JSONL_ROW_BYTES + 1)
                if not line:
                    return
                ordinal += 1
                if len(line) > _MAX_JSONL_ROW_BYTES:
                    raise AtlasCategoricalGraphError(
                        f"{label} row {ordinal} exceeds byte bound"
                    )
                if not line.endswith(b"\n"):
                    raise AtlasCategoricalGraphError(
                        f"{label} row {ordinal} is not newline terminated"
                    )
                row = _decode_json(line[:-1], f"{label} row {ordinal}")
                if type(row) is not dict:
                    raise AtlasCategoricalGraphError(
                        f"{label} row {ordinal} must be an object"
                    )
                if _canonical_bytes(row) + b"\n" != line:
                    raise AtlasCategoricalGraphError(
                        f"{label} row {ordinal} is not canonical JSONL"
                    )
                yield row, line
    except AtlasCategoricalGraphError:
        raise
    except OSError as exc:
        raise AtlasCategoricalGraphError(f"{label} cannot be read: {exc}") from exc


def _check_stream_measurement(
    descriptor: Mapping[str, Any],
    *,
    size: int,
    digest: hashlib._Hash,
    count: int,
    label: str,
) -> None:
    if (
        size != descriptor["size"]
        or digest.hexdigest() != descriptor["sha256"]
        or count != descriptor["count"]
    ):
        raise AtlasCategoricalGraphError(f"{label} stream differs")


def _partition_closures(manifest: Mapping[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for partition in manifest["partitions"]:
        closure = set(partition["dependencies"])
        for dependency in partition["dependencies"]:
            closure.update(result[dependency])
        result[partition["partition_id"]] = closure
    return result


def validate_bundle_directory(
    path: Path, *, check_cancelled: Callable[[], None] | None = None
) -> dict[str, Any]:
    """Validate only the authoritative manifest and canonical JSONL streams.

    A missing, stale, corrupt, or malicious ``query-index.sqlite3`` does not
    affect this result.  Call :func:`verify_query_index` separately before using
    an existing derived index.
    """

    check = check_cancelled or (lambda: None)
    check()
    root = _bundle_root(path)
    manifest = _load_manifest(root)
    profile = _manifest_profile(manifest)
    closures = _partition_closures(manifest)
    node_partitions: dict[str, str] = {}
    edge_ids: set[str] = set()
    seen_partitions: set[str] = set()

    for partition in manifest["partitions"]:
        partition_id = partition["partition_id"]
        node_descriptor = partition["nodes"]
        node_path = _bundle_file(
            root,
            node_descriptor["file"],
            f"categorical graph {partition_id} nodes",
        )
        node_digest = hashlib.sha256()
        node_size = node_count = 0
        kind_counts: Counter[str] = Counter()
        local_node_ids: set[str] = set()
        for row, raw in _iter_canonical_jsonl(
            node_path, f"categorical graph {partition_id} nodes"
        ):
            check()
            _validate_node_record(row)
            node_id = row["id"]
            if node_id in node_partitions or node_id in local_node_ids:
                raise AtlasCategoricalGraphError(
                    "categorical node identity is duplicated"
                )
            local_node_ids.add(node_id)
            kind_counts[row["kind"]] += 1
            node_digest.update(raw)
            node_size += len(raw)
            node_count += 1
        _check_stream_measurement(
            node_descriptor,
            size=node_size,
            digest=node_digest,
            count=node_count,
            label=f"categorical graph {partition_id} nodes",
        )
        if dict(sorted(kind_counts.items())) != node_descriptor["kinds"]:
            raise AtlasCategoricalGraphError(
                f"categorical graph {partition_id} node-kind summary differs"
            )
        for node_id in local_node_ids:
            node_partitions[node_id] = partition_id

        if profile == STRICT_VALIDATION_PROFILE:
            allowed_partitions = closures[partition_id] | {partition_id}
        else:
            # Exact compatibility for retained pre-hardening V2 identities.
            allowed_partitions = seen_partitions | {partition_id}

        edge_descriptor = partition["edges"]
        edge_path = _bundle_file(
            root,
            edge_descriptor["file"],
            f"categorical graph {partition_id} edges",
        )
        edge_digest = hashlib.sha256()
        edge_size = edge_count = 0
        relation_counts: Counter[str] = Counter()
        for row, raw in _iter_canonical_jsonl(
            edge_path, f"categorical graph {partition_id} edges"
        ):
            check()
            _validate_edge_record(row)
            edge_id = row["id"]
            if edge_id in edge_ids:
                raise AtlasCategoricalGraphError(
                    "categorical edge identity is duplicated"
                )
            source_partition = node_partitions.get(row["source"])
            target_partition = node_partitions.get(row["target"])
            if (
                source_partition not in allowed_partitions
                or target_partition not in allowed_partitions
            ):
                raise AtlasCategoricalGraphError(
                    "categorical edge endpoint is outside dependency closure (declared)"
                )
            edge_ids.add(edge_id)
            relation_counts[row["relation"]] += 1
            edge_digest.update(raw)
            edge_size += len(raw)
            edge_count += 1
        _check_stream_measurement(
            edge_descriptor,
            size=edge_size,
            digest=edge_digest,
            count=edge_count,
            label=f"categorical graph {partition_id} edges",
        )
        if dict(sorted(relation_counts.items())) != edge_descriptor["relations"]:
            raise AtlasCategoricalGraphError(
                f"categorical graph {partition_id} relation summary differs"
            )
        seen_partitions.add(partition_id)

    if (
        len(node_partitions) != manifest["summary"]["node_count"]
        or len(edge_ids) != manifest["summary"]["edge_count"]
    ):
        raise AtlasCategoricalGraphError("categorical graph unique-count summary differs")
    check()
    return manifest


def _file_measurement(
    path: Path, *, check_cancelled: Callable[[], None] | None = None
) -> tuple[int, str]:
    check = check_cancelled or (lambda: None)
    check()
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                check()
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise AtlasCategoricalGraphError(
            f"categorical graph query index cannot be read: {exc}"
        ) from exc
    check()
    return size, digest.hexdigest()


_EXPECTED_TABLE_INFO = {
    "metadata": [
        (0, "key", "TEXT", 0, None, 1),
        (1, "value", "TEXT", 1, None, 0),
    ],
    "nodes": [
        (0, "node_key", "INTEGER", 0, None, 1),
        (1, "id", "TEXT", 1, None, 0),
        (2, "partition_id", "TEXT", 1, None, 0),
        (3, "kind", "TEXT", 1, None, 0),
        (4, "semantic_key", "TEXT", 1, None, 0),
        (5, "properties_json", "TEXT", 1, None, 0),
        (6, "evidence_json", "TEXT", 1, None, 0),
    ],
    "edges": [
        (0, "edge_key", "INTEGER", 0, None, 1),
        (1, "partition_id", "TEXT", 1, None, 0),
        (2, "relation", "TEXT", 1, None, 0),
        (3, "source_node", "INTEGER", 1, None, 0),
        (4, "target_node", "INTEGER", 1, None, 0),
        (5, "semantic_key", "TEXT", 1, None, 0),
        (6, "properties_json", "TEXT", 1, None, 0),
        (7, "evidence_json", "TEXT", 1, None, 0),
    ],
}
_EXPECTED_SQLITE_OBJECTS = {
    ("table", "metadata", "metadata"),
    ("table", "nodes", "nodes"),
    ("table", "edges", "edges"),
    ("index", "sqlite_autoindex_metadata_1", "metadata"),
    ("index", "sqlite_autoindex_nodes_1", "nodes"),
    ("index", "nodes_kind_key", "nodes"),
    ("index", "edges_source_relation", "edges"),
    ("index", "edges_target_relation", "edges"),
}
_EXPECTED_INDEX_COLUMNS = {
    "sqlite_autoindex_metadata_1": ["key"],
    "sqlite_autoindex_nodes_1": ["id"],
    "nodes_kind_key": ["kind", "semantic_key"],
    "edges_source_relation": ["source_node", "relation"],
    "edges_target_relation": ["target_node", "relation"],
}
_EXPECTED_INDEX_LIST = {
    "metadata": {("sqlite_autoindex_metadata_1", 1, "pk", 0)},
    "nodes": {
        ("nodes_kind_key", 0, "c", 0),
        ("sqlite_autoindex_nodes_1", 1, "u", 0),
    },
    "edges": {
        ("edges_source_relation", 0, "c", 0),
        ("edges_target_relation", 0, "c", 0),
    },
}
_EXPECTED_SCHEMA_SQL = {
    "metadata": "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "nodes": (
        "CREATE TABLE nodes ( node_key INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, "
        "partition_id TEXT NOT NULL, kind TEXT NOT NULL, semantic_key TEXT NOT NULL, "
        "properties_json TEXT NOT NULL, evidence_json TEXT NOT NULL )"
    ),
    "edges": (
        "CREATE TABLE edges ( edge_key INTEGER PRIMARY KEY, partition_id TEXT NOT NULL, "
        "relation TEXT NOT NULL, source_node INTEGER NOT NULL, target_node INTEGER NOT NULL, "
        "semantic_key TEXT NOT NULL, properties_json TEXT NOT NULL, evidence_json TEXT NOT NULL )"
    ),
    "nodes_kind_key": "CREATE INDEX nodes_kind_key ON nodes(kind, semantic_key)",
    "edges_source_relation": (
        "CREATE INDEX edges_source_relation ON edges(source_node, relation)"
    ),
    "edges_target_relation": (
        "CREATE INDEX edges_target_relation ON edges(target_node, relation)"
    ),
}


def _open_immutable_index(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path), safe='/')}?mode=ro&immutable=1"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.execute("PRAGMA query_only=ON")
        # Bound the read-only mapping. Repeated small reads on mounted evidence
        # volumes otherwise dominate verification and indexed recipe queries.
        # SQLite may use a smaller mapping or its ordinary pager when unsupported.
        connection.execute("PRAGMA mmap_size=268435456")
        return connection
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        raise AtlasCategoricalGraphError(
            f"categorical graph query index cannot be opened read-only: {exc}"
        ) from exc


def _verify_index_connection(
    connection: sqlite3.Connection, manifest: Mapping[str, Any], *,
    check_cancelled: Callable[[], None] | None = None,
) -> None:
    check = check_cancelled or (lambda: None)
    check()
    interrupted: list[BaseException] = []
    if check_cancelled is not None:
        def poll() -> int:
            try:
                check()
            except BaseException as error:
                interrupted.append(error)
                return 1
            return 0
        connection.set_progress_handler(poll, 4096)
    try:
        schema_rows = list(
            connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
            )
        )
        objects = {
            (row[0], row[1], row[2])
            for row in schema_rows
        }
        if objects != _EXPECTED_SQLITE_OBJECTS:
            raise AtlasCategoricalGraphError(
                "categorical graph query index schema objects differ"
            )
        for object_type, name, _, sql in schema_rows:
            if object_type == "index" and name.startswith("sqlite_autoindex_"):
                if sql is not None:
                    raise AtlasCategoricalGraphError(
                        "categorical graph query index automatic schema differs"
                    )
                continue
            if (
                type(sql) is not str
                or " ".join(sql.split()) != _EXPECTED_SCHEMA_SQL.get(name)
            ):
                raise AtlasCategoricalGraphError(
                    f"categorical graph query index {name} definition differs"
                )
        for table, expected in _EXPECTED_TABLE_INFO.items():
            actual = list(connection.execute(f'PRAGMA table_info("{table}")'))
            if actual != expected:
                raise AtlasCategoricalGraphError(
                    f"categorical graph query index {table} schema differs"
                )
            actual_indexes = {
                (row[1], row[2], row[3], row[4])
                for row in connection.execute(f'PRAGMA index_list("{table}")')
            }
            if actual_indexes != _EXPECTED_INDEX_LIST[table]:
                raise AtlasCategoricalGraphError(
                    f"categorical graph query index {table} indexes differ"
                )
        for index, expected_columns in _EXPECTED_INDEX_COLUMNS.items():
            actual_columns = [
                row[2] for row in connection.execute(f'PRAGMA index_info("{index}")')
            ]
            if actual_columns != expected_columns:
                raise AtlasCategoricalGraphError(
                    f"categorical graph query index {index} schema differs"
                )
        metadata = list(
            connection.execute("SELECT key,value FROM metadata ORDER BY key")
        )
        if metadata != [("graph_set_id", manifest["graph_set_id"])]:
            raise AtlasCategoricalGraphError("categorical query index is stale")
        node_count = int(connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])
        edge_count = int(connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0])
        if (
            node_count != manifest["summary"]["node_count"]
            or edge_count != manifest["summary"]["edge_count"]
        ):
            raise AtlasCategoricalGraphError(
                "categorical graph query index row counts differ"
            )
        expected_nodes = {
            partition["partition_id"]: partition["nodes"]["count"]
            for partition in manifest["partitions"]
            if partition["nodes"]["count"]
        }
        expected_edges = {
            partition["partition_id"]: partition["edges"]["count"]
            for partition in manifest["partitions"]
            if partition["edges"]["count"]
        }
        actual_nodes = dict(
            connection.execute(
                "SELECT partition_id,COUNT(*) FROM nodes GROUP BY partition_id"
            )
        )
        actual_edges = dict(
            connection.execute(
                "SELECT partition_id,COUNT(*) FROM edges GROUP BY partition_id"
            )
        )
        if actual_nodes != expected_nodes or actual_edges != expected_edges:
            raise AtlasCategoricalGraphError(
                "categorical graph query index partition counts differ"
            )
        dangling = connection.execute(
            "SELECT 1 FROM edges e "
            "LEFT JOIN nodes s ON s.node_key=e.source_node "
            "LEFT JOIN nodes t ON t.node_key=e.target_node "
            "WHERE s.node_key IS NULL OR t.node_key IS NULL LIMIT 1"
        ).fetchone()
        if dangling is not None:
            raise AtlasCategoricalGraphError(
                "categorical graph query index has dangling endpoints"
            )
        _verify_index_semantics(connection, manifest, check_cancelled=check_cancelled)
        check()
    except AtlasCategoricalGraphError:
        raise
    except sqlite3.Error as exc:
        if interrupted:
            raise interrupted[0]
        raise AtlasCategoricalGraphError(
            f"categorical graph query index verification failed: {exc}"
        ) from exc
    finally:
        if check_cancelled is not None:
            connection.set_progress_handler(None, 0)


def _decode_index_json(value: object, label: str) -> Any:
    if type(value) is not str:
        raise AtlasCategoricalGraphError(f"{label} must be canonical JSON text")
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise AtlasCategoricalGraphError(
            f"{label} is not valid UTF-8 JSON: {exc}"
        ) from exc
    decoded = _decode_json(raw, label)
    if _canonical_text(decoded) != value:
        raise AtlasCategoricalGraphError(f"{label} is not canonical JSON text")
    return decoded


def _verify_index_semantics(
    connection: sqlite3.Connection, manifest: Mapping[str, Any], *,
    check_cancelled: Callable[[], None] | None = None,
) -> None:
    """Reconstruct exact stream rows and bind cache semantics to stream digests."""

    check = check_cancelled or (lambda: None)
    check()
    for partition in manifest["partitions"]:
        partition_id = partition["partition_id"]
        node_descriptor = partition["nodes"]
        node_digest = hashlib.sha256()
        node_size = node_count = 0
        kind_counts: Counter[str] = Counter()
        node_rows = connection.execute(
            "SELECT id,kind,semantic_key,properties_json,evidence_json "
            "FROM nodes WHERE partition_id=? ORDER BY node_key",
            (partition_id,),
        )
        for ordinal, values in enumerate(node_rows, 1):
            check()
            properties = _decode_index_json(
                values[3],
                f"categorical graph query index {partition_id} node {ordinal} properties",
            )
            evidence = _decode_index_json(
                values[4],
                f"categorical graph query index {partition_id} node {ordinal} evidence",
            )
            row = {
                "format": NODE_FORMAT,
                "schema_version": 2,
                "id": values[0],
                "kind": values[1],
                "semantic_key": values[2],
                "properties": properties,
                "evidence": evidence,
            }
            _validate_node_record(row)
            raw = _canonical_bytes(row) + b"\n"
            node_digest.update(raw)
            node_size += len(raw)
            node_count += 1
            kind_counts[row["kind"]] += 1
        _check_stream_measurement(
            node_descriptor,
            size=node_size,
            digest=node_digest,
            count=node_count,
            label=f"categorical graph query index {partition_id} nodes semantic",
        )
        if dict(sorted(kind_counts.items())) != node_descriptor["kinds"]:
            raise AtlasCategoricalGraphError(
                f"categorical graph query index {partition_id} node-kind semantics differ"
            )

        edge_descriptor = partition["edges"]
        edge_digest = hashlib.sha256()
        edge_size = edge_count = 0
        relation_counts: Counter[str] = Counter()
        edge_rows = connection.execute(
            "SELECT e.relation,s.id,t.id,e.semantic_key,e.properties_json,e.evidence_json "
            "FROM edges e JOIN nodes s ON s.node_key=e.source_node "
            "JOIN nodes t ON t.node_key=e.target_node "
            "WHERE e.partition_id=? ORDER BY e.edge_key",
            (partition_id,),
        )
        for ordinal, values in enumerate(edge_rows, 1):
            check()
            relation = _required_text(
                values[0],
                f"categorical graph query index {partition_id} edge {ordinal} relation",
            )
            source = _required_text(
                values[1],
                f"categorical graph query index {partition_id} edge {ordinal} source",
            )
            target = _required_text(
                values[2],
                f"categorical graph query index {partition_id} edge {ordinal} target",
            )
            semantic_key = _required_text(
                values[3],
                f"categorical graph query index {partition_id} edge {ordinal} semantic key",
            )
            properties = _decode_index_json(
                values[4],
                f"categorical graph query index {partition_id} edge {ordinal} properties",
            )
            evidence = _decode_index_json(
                values[5],
                f"categorical graph query index {partition_id} edge {ordinal} evidence",
            )
            row = {
                "format": EDGE_FORMAT,
                "schema_version": 2,
                "id": _edge_id(relation, source, target, semantic_key),
                "relation": relation,
                "source": source,
                "target": target,
                "semantic_key": semantic_key,
                "properties": properties,
                "evidence": evidence,
            }
            _validate_edge_record(row)
            raw = _canonical_bytes(row) + b"\n"
            edge_digest.update(raw)
            edge_size += len(raw)
            edge_count += 1
            relation_counts[row["relation"]] += 1
        _check_stream_measurement(
            edge_descriptor,
            size=edge_size,
            digest=edge_digest,
            count=edge_count,
            label=f"categorical graph query index {partition_id} edges semantic",
        )
        if dict(sorted(relation_counts.items())) != edge_descriptor["relations"]:
            raise AtlasCategoricalGraphError(
                f"categorical graph query index {partition_id} relation semantics differ"
            )
    check()


def _verified_index_connection(
    root: Path, manifest: Mapping[str, Any], *,
    check_cancelled: Callable[[], None] | None = None,
) -> sqlite3.Connection:
    check = check_cancelled or (lambda: None)
    check()
    descriptor = _validate_query_descriptor(
        manifest.get("query_index"), manifest["graph_set_id"]
    )
    if descriptor is None:
        raise AtlasCategoricalGraphError(
            "categorical graph query index descriptor is absent; rebuild is required"
        )
    path = _bundle_file(
        root, descriptor["file"], "categorical graph query index"
    )
    try:
        before = path.stat()
        measured_size, measured_digest = _file_measurement(path, check_cancelled=check_cancelled)
        if (
            measured_size != descriptor["size"]
            or measured_digest != descriptor["sha256"]
        ):
            raise AtlasCategoricalGraphError(
                "categorical graph query index bytes differ"
            )
        connection = _open_immutable_index(path)
        try:
            def unchanged() -> None:
                after = path.stat()
                fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
                if any(getattr(before, field) != getattr(after, field) for field in fields):
                    raise AtlasCategoricalGraphError(
                        "categorical graph query index changed during verification"
                    )

            unchanged()
            _verify_index_connection(connection, manifest, check_cancelled=check_cancelled)
            check()
            unchanged()
        except BaseException:
            connection.close()
            raise
        return connection
    except AtlasCategoricalGraphError:
        raise
    except OSError as exc:
        raise AtlasCategoricalGraphError(
            f"categorical graph query index cannot be inspected: {exc}"
        ) from exc


def verify_query_index(
    path: Path, *, check_cancelled: Callable[[], None] | None = None
) -> dict[str, Any]:
    """Verify authoritative streams and the derived index's exact semantics."""

    root = _bundle_root(path)
    manifest = validate_bundle_directory(root, check_cancelled=check_cancelled)
    connection = _verified_index_connection(root, manifest, check_cancelled=check_cancelled)
    connection.close()
    return manifest


def inspect_query_index(
    path: Path, *, check_cancelled: Callable[[], None] | None = None
) -> dict[str, Any]:
    """Describe whether a derived index can execute queries, with exact repair state.

    Authoritative streams are validated once.  A present index is then opened
    read-only and verified against those streams.  The returned status is
    operational metadata, not graph evidence and not part of graph identity.
    """

    root = _bundle_root(path)
    manifest = validate_bundle_directory(root, check_cancelled=check_cancelled)
    descriptor = manifest["query_index"]
    requirements = [
        "the bundle root must be writable",
        "bounded staging disk must be available",
        "authoritative manifest and JSONL streams must remain unchanged",
    ]
    if descriptor is None:
        status = {
            "state": "missing",
            "usable": False,
            "reason_code": "query-index-descriptor-absent",
            "reason": "The graph manifest has no derived query-index descriptor.",
            "repair_supported": True,
            "repair_requirements": requirements,
        }
    else:
        index_path = root / descriptor["file"]
        try:
            mode = os.lstat(index_path).st_mode
        except FileNotFoundError:
            status = {
                "state": "missing",
                "usable": False,
                "reason_code": "query-index-file-missing",
                "reason": "The declared derived query-index file is missing.",
                "repair_supported": True,
                "repair_requirements": requirements,
            }
        except OSError as exc:
            status = {
                "state": "unusable",
                "usable": False,
                "reason_code": "query-index-file-uninspectable",
                "reason": f"The declared derived query-index file cannot be inspected: {exc}",
                "repair_supported": False,
                "repair_requirements": requirements,
            }
        else:
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                status = {
                    "state": "unusable",
                    "usable": False,
                    "reason_code": "query-index-file-unsafe",
                    "reason": (
                        "The declared derived query-index path is not a regular "
                        "non-symlink file."
                    ),
                    "repair_supported": False,
                    "repair_requirements": [
                        "the unsafe derived path must first be removed or relocated",
                        *requirements,
                    ],
                }
            else:
                try:
                    connection = _verified_index_connection(root, manifest, check_cancelled=check_cancelled)
                except AtlasCategoricalGraphError as exc:
                    status = {
                        "state": "unusable",
                        "usable": False,
                        "reason_code": "query-index-verification-failed",
                        "reason": str(exc),
                        "repair_supported": True,
                        "repair_requirements": requirements,
                    }
                else:
                    connection.close()
                    status = {
                        "state": "verified",
                        "usable": True,
                        "reason_code": "query-index-verified",
                        "reason": (
                            "The derived query index exactly matches the "
                            "authoritative graph streams."
                        ),
                        "repair_supported": False,
                        "repair_requirements": [],
                    }
    return {
        "root": str(root.resolve()),
        "manifest": manifest,
        "query_index": {**status, "descriptor": descriptor},
    }


def _create_index_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE nodes (
            node_key INTEGER PRIMARY KEY,
            id TEXT NOT NULL UNIQUE,
            partition_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            semantic_key TEXT NOT NULL,
            properties_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL
        );
        CREATE TABLE edges (
            edge_key INTEGER PRIMARY KEY,
            partition_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            source_node INTEGER NOT NULL,
            target_node INTEGER NOT NULL,
            semantic_key TEXT NOT NULL,
            properties_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL
        );
        CREATE INDEX nodes_kind_key ON nodes(kind, semantic_key);
        CREATE INDEX edges_source_relation ON edges(source_node, relation);
        CREATE INDEX edges_target_relation ON edges(target_node, relation);
        """
    )


def _flush_batch(
    connection: sqlite3.Connection, statement: str, batch: list[tuple[Any, ...]]
) -> None:
    if batch:
        connection.executemany(statement, batch)
        batch.clear()


def _build_index_from_streams(
    root: Path,
    manifest: Mapping[str, Any],
    path: Path,
    *,
    max_index_bytes: int | None = None,
    progress: _IndexProgress | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> None:
    check = check_cancelled or (lambda: None)
    check()
    try:
        connection = sqlite3.connect(path)
    except sqlite3.Error as exc:
        raise AtlasCategoricalGraphError(
            f"categorical graph query index staging failed: {exc}"
        ) from exc
    interrupted: list[BaseException] = []
    try:
        if check_cancelled is not None:
            def cancelled_progress() -> int:
                try:
                    check()
                except BaseException as error:
                    interrupted.append(error)
                    return 1
                return 0
            connection.set_progress_handler(cancelled_progress, 4096)
        if max_index_bytes is not None:
            page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
            maximum_pages = max_index_bytes // page_size
            if maximum_pages < 16:
                raise AtlasCategoricalGraphError(
                    "categorical graph query index byte bound is too small"
                )
            connection.execute(f"PRAGMA max_page_count={maximum_pages}")
        _create_index_schema(connection)
        connection.execute(
            "INSERT INTO metadata(key,value) VALUES ('graph_set_id',?)",
            (manifest["graph_set_id"],),
        )
        node_keys: dict[str, int] = {}
        next_node_key = 1
        total_records = sum(
            partition[family]["count"]
            for partition in manifest["partitions"]
            for family in ("nodes", "edges")
        )
        total_source_bytes = sum(
            partition[family]["size"]
            for partition in manifest["partitions"]
            for family in ("nodes", "edges")
        )
        completed_records = 0
        completed_source_bytes = 0

        def report_progress(phase: str, partition_id: str) -> None:
            if progress is not None:
                progress(
                    {
                        "phase": phase,
                        "partition_id": partition_id,
                        "records_completed": completed_records,
                        "records_total": total_records,
                        "source_bytes_completed": completed_source_bytes,
                        "source_bytes_total": total_source_bytes,
                    }
                )

        node_statement = "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?)"
        for partition in manifest["partitions"]:
            partition_id = partition["partition_id"]
            descriptor = partition["nodes"]
            stream_path = _bundle_file(
                root,
                descriptor["file"],
                f"categorical graph {partition_id} nodes",
            )
            batch: list[tuple[Any, ...]] = []
            digest = hashlib.sha256()
            size = count = 0
            kinds: Counter[str] = Counter()
            for row, raw in _iter_canonical_jsonl(
                stream_path, f"categorical graph {partition_id} nodes"
            ):
                _validate_node_record(row)
                node_keys[row["id"]] = next_node_key
                batch.append(
                    (
                        next_node_key,
                        row["id"],
                        partition_id,
                        row["kind"],
                        row["semantic_key"],
                        _canonical_text(row["properties"]),
                        _canonical_text(row["evidence"]),
                    )
                )
                digest.update(raw)
                size += len(raw)
                count += 1
                kinds[row["kind"]] += 1
                next_node_key += 1
                if len(batch) >= _INDEX_BATCH_SIZE:
                    check()
                    _flush_batch(connection, node_statement, batch)
            _flush_batch(connection, node_statement, batch)
            _check_stream_measurement(
                descriptor,
                size=size,
                digest=digest,
                count=count,
                label=f"categorical graph {partition_id} nodes used for index rebuild",
            )
            if dict(sorted(kinds.items())) != descriptor["kinds"]:
                raise AtlasCategoricalGraphError(
                    f"categorical graph {partition_id} node-kind summary used for index rebuild differs"
                )
            completed_records += count
            completed_source_bytes += size
            report_progress("nodes", partition_id)

        edge_statement = (
            "INSERT INTO edges(partition_id,relation,source_node,target_node,semantic_key,"
            "properties_json,evidence_json) VALUES (?, ?, ?, ?, ?, ?, ?)"
        )
        for partition in manifest["partitions"]:
            partition_id = partition["partition_id"]
            descriptor = partition["edges"]
            stream_path = _bundle_file(
                root,
                descriptor["file"],
                f"categorical graph {partition_id} edges",
            )
            batch = []
            digest = hashlib.sha256()
            size = count = 0
            relations: Counter[str] = Counter()
            for row, raw in _iter_canonical_jsonl(
                stream_path, f"categorical graph {partition_id} edges"
            ):
                _validate_edge_record(row)
                try:
                    source_key = node_keys[row["source"]]
                    target_key = node_keys[row["target"]]
                except KeyError as exc:
                    raise AtlasCategoricalGraphError(
                        "categorical graph index rebuild found a missing endpoint"
                    ) from exc
                batch.append(
                    (
                        partition_id,
                        row["relation"],
                        source_key,
                        target_key,
                        row["semantic_key"],
                        _canonical_text(row["properties"]),
                        _canonical_text(row["evidence"]),
                    )
                )
                digest.update(raw)
                size += len(raw)
                count += 1
                relations[row["relation"]] += 1
                if len(batch) >= _INDEX_BATCH_SIZE:
                    check()
                    _flush_batch(connection, edge_statement, batch)
            _flush_batch(connection, edge_statement, batch)
            _check_stream_measurement(
                descriptor,
                size=size,
                digest=digest,
                count=count,
                label=f"categorical graph {partition_id} edges used for index rebuild",
            )
            if dict(sorted(relations.items())) != descriptor["relations"]:
                raise AtlasCategoricalGraphError(
                    f"categorical graph {partition_id} relation summary used for index rebuild differs"
                )
            completed_records += count
            completed_source_bytes += size
            report_progress("edges", partition_id)
        connection.commit()
        connection.execute("VACUUM")
        check()
    except AtlasCategoricalGraphError:
        raise
    except sqlite3.Error as exc:
        if interrupted:
            raise interrupted[0]
        check()
        raise AtlasCategoricalGraphError(
            f"categorical graph query index rebuild failed: {exc}"
        ) from exc
    finally:
        connection.close()


def _index_descriptor(
    path: Path, graph_set_id: str, *, check_cancelled: Callable[[], None] | None = None
) -> dict[str, Any]:
    size, digest = _file_measurement(path, check_cancelled=check_cancelled)
    return {
        "role": "derived-disposable-index",
        "file": _QUERY_INDEX_FILE,
        "size": size,
        "sha256": digest,
        "derived_from_graph_set_id": graph_set_id,
    }


def _manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _write_manifest_atomically(root: Path, manifest: Mapping[str, Any]) -> None:
    destination = _bundle_file(
        root, "manifest.json", "categorical graph manifest", must_exist=True
    )
    descriptor, temporary_name = tempfile.mkstemp(
        dir=root, prefix=".manifest.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_manifest_bytes(manifest))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except OSError as exc:
        raise AtlasCategoricalGraphError(
            f"categorical graph manifest replacement failed: {exc}"
        ) from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def rebuild_query_index(
    path: Path,
    *,
    max_source_bytes: int | None = None,
    max_index_bytes: int | None = None,
    progress: _IndexProgress | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Deterministically rebuild the derived index and atomically replace it.

    The authoritative streams are fully validated first.  Construction uses a
    bounded row batch in a staging directory; the database is measured by
    streaming and is never loaded wholly into memory.  A crash between the two
    atomic replacements can only leave a descriptor mismatch, which fails
    closed and is recoverable by calling this function again.
    """

    check = check_cancelled or (lambda: None)
    check()
    for value, label in (
        (max_source_bytes, "source byte"),
        (max_index_bytes, "query index byte"),
    ):
        if value is not None and (type(value) is not int or value < 1):
            raise AtlasCategoricalGraphError(
                f"categorical graph {label} bound must be a positive integer"
            )
    if max_index_bytes is not None and max_index_bytes < 64 * 1024:
        raise AtlasCategoricalGraphError(
            "categorical graph query index byte bound is below 65536"
        )
    if progress is not None and not callable(progress):
        raise AtlasCategoricalGraphError(
            "categorical graph query index progress callback must be callable"
        )

    root = _bundle_root(path)
    declared_manifest = _load_manifest(root)
    declared_source_bytes = sum(
        partition[family]["size"]
        for partition in declared_manifest["partitions"]
        for family in ("nodes", "edges")
    )
    if (
        max_source_bytes is not None
        and declared_source_bytes > max_source_bytes
    ):
        raise AtlasCategoricalGraphError(
            "categorical graph authoritative stream bytes exceed the declared rebuild bound"
        )
    free_bytes = shutil.disk_usage(root).free
    required_free_bytes = (
        0 if max_index_bytes is None else max_index_bytes * 2
    )
    if required_free_bytes and free_bytes < required_free_bytes:
        raise AtlasCategoricalGraphError(
            "categorical graph query index rebuild lacks bounded staging disk space"
        )

    manifest = validate_bundle_directory(root, check_cancelled=check_cancelled)
    if manifest != declared_manifest:
        raise AtlasCategoricalGraphError(
            "categorical graph manifest changed during query index preflight"
        )
    source_bytes = sum(
        partition[family]["size"]
        for partition in manifest["partitions"]
        for family in ("nodes", "edges")
    )
    source_records = sum(
        partition[family]["count"]
        for partition in manifest["partitions"]
        for family in ("nodes", "edges")
    )
    if max_source_bytes is not None and source_bytes > max_source_bytes:
        raise AtlasCategoricalGraphError(
            "categorical graph authoritative stream bytes exceed the declared rebuild bound"
        )
    free_bytes = shutil.disk_usage(root).free
    if required_free_bytes and free_bytes < required_free_bytes:
        raise AtlasCategoricalGraphError(
            "categorical graph query index rebuild lacks bounded staging disk space"
        )
    if progress is not None:
        progress(
            {
                "phase": "validated",
                "root": str(root.resolve()),
                "graph_set_id": manifest["graph_set_id"],
                "previous_query_index": manifest["query_index"],
                "records_completed": 0,
                "records_total": source_records,
                "source_bytes_completed": 0,
                "source_bytes_total": source_bytes,
                "free_bytes_before": free_bytes,
                "required_free_bytes": required_free_bytes,
            }
        )
    target = root / _QUERY_INDEX_FILE
    try:
        if target.is_symlink():
            raise AtlasCategoricalGraphError(
                "categorical graph query index cannot be a symlink"
            )
        if target.exists() and not target.is_file():
            raise AtlasCategoricalGraphError(
                "categorical graph query index must be a regular file"
            )
    except OSError as exc:
        raise AtlasCategoricalGraphError(
            f"categorical graph query index cannot be inspected: {exc}"
        ) from exc

    with tempfile.TemporaryDirectory(
        dir=root, prefix=".query-index-rebuild-"
    ) as temporary:
        staged = Path(temporary) / _QUERY_INDEX_FILE
        if max_index_bytes is None and progress is None and check_cancelled is None:
            # Preserve the original call surface for V2 callers and focused
            # construction interposition tests.
            _build_index_from_streams(root, manifest, staged)
        else:
            _build_index_from_streams(
                root,
                manifest,
                staged,
                max_index_bytes=max_index_bytes,
                progress=progress,
                check_cancelled=check_cancelled,
            )
        descriptor = _index_descriptor(staged, manifest["graph_set_id"], check_cancelled=check_cancelled)
        if max_index_bytes is not None and descriptor["size"] > max_index_bytes:
            raise AtlasCategoricalGraphError(
                "categorical graph rebuilt query index exceeds the declared byte bound"
            )
        candidate_manifest = dict(manifest)
        candidate_manifest["query_index"] = descriptor
        validate_bundle_manifest(candidate_manifest)
        candidate_connection = _open_immutable_index(staged)
        try:
            _verify_index_connection(candidate_connection, candidate_manifest, check_cancelled=check_cancelled)
        finally:
            candidate_connection.close()
        if progress is not None:
            progress(
                {
                    "phase": "verified",
                    "records_completed": source_records,
                    "records_total": source_records,
                    "source_bytes_completed": source_bytes,
                    "source_bytes_total": source_bytes,
                    "index_size": descriptor["size"],
                    "index_sha256": descriptor["sha256"],
                }
            )
        current_manifest = validate_bundle_directory(root, check_cancelled=check_cancelled)
        if current_manifest["graph_set_id"] != manifest["graph_set_id"]:
            raise AtlasCategoricalGraphError(
                "categorical graph authority changed during query index rebuild"
            )
        check()
        try:
            os.replace(staged, target)
        except OSError as exc:
            raise AtlasCategoricalGraphError(
                f"categorical graph query index replacement failed: {exc}"
            ) from exc
    _write_manifest_atomically(root, candidate_manifest)
    if progress is not None:
        progress(
            {
                "phase": "published",
                "records_completed": source_records,
                "records_total": source_records,
                "source_bytes_completed": source_bytes,
                "source_bytes_total": source_bytes,
                "index_size": descriptor["size"],
                "index_sha256": descriptor["sha256"],
            }
        )
    return candidate_manifest


class CategoricalGraphBundleBuilder:
    """Create a strict graph bundle with declared transitive dependency closure."""

    def __init__(
        self,
        output: Path,
        *,
        scope: Mapping[str, Any],
        evidence_binding: Mapping[str, Any],
        evidence_authority: str = "crucible-occurrence-v1",
        check_cancelled: Callable[[], None] | None = None,
    ) -> None:
        if evidence_authority not in _AUTHORITIES:
            raise AtlasCategoricalGraphError("unsupported categorical evidence authority")
        self.evidence_authority = evidence_authority
        self.check_cancelled = check_cancelled or (lambda: None)
        self.check_cancelled()
        self.output = output.absolute()
        if self.output.exists():
            raise AtlasCategoricalGraphError(
                f"categorical graph output already exists: {self.output}"
            )
        self.output.mkdir(parents=True)
        self.scope = _json_object(scope, "categorical graph scope")
        self.evidence_binding = _json_object(
            evidence_binding, "categorical graph evidence binding"
        )
        self.partitions: list[dict[str, Any]] = []
        self.partition_ids: set[str] = set()
        self.node_ids: set[str] = set()
        self.edge_ids: set[str] = set()
        self._partition_closures: dict[str, set[str]] = {}
        self._partition_node_ids: dict[str, set[str]] = {}
        self._closed = False

    def add_partition(
        self,
        partition_id: str,
        *,
        classification: str,
        dependencies: Sequence[str],
        nodes: Iterable[Mapping[str, Any]],
        edges: Iterable[Mapping[str, Any]],
        evidence_categories: Sequence[str],
        limitations: Sequence[str] = (),
    ) -> dict[str, Any]:
        if self._closed:
            raise AtlasCategoricalGraphError("categorical graph bundle is already closed")
        partition_id = _safe_relative_path(
            partition_id, "categorical partition ID", one_segment=True
        )
        classification = _required_text(classification, "categorical classification")
        if partition_id in self.partition_ids:
            raise AtlasCategoricalGraphError("categorical partition ID is duplicated")
        if type(dependencies) not in (list, tuple):
            raise AtlasCategoricalGraphError(
                "categorical partition dependencies must be an array"
            )
        dependency_rows = tuple(
            _required_text(value, "categorical partition dependency")
            for value in dependencies
        )
        if (
            tuple(sorted(set(dependency_rows))) != dependency_rows
            or not set(dependency_rows).issubset(self.partition_ids)
        ):
            raise AtlasCategoricalGraphError(
                "categorical partition dependencies must be closed, unique, and sorted"
            )
        if type(evidence_categories) not in (list, tuple):
            raise AtlasCategoricalGraphError(
                "categorical partition evidence categories must be an array"
            )
        category_rows = tuple(
            _required_text(value, "categorical graph evidence category")
            for value in evidence_categories
        )
        if tuple(sorted(set(category_rows))) != category_rows or not category_rows:
            raise AtlasCategoricalGraphError(
                "categorical partition evidence categories must be nonempty, unique, and sorted"
            )
        if type(limitations) not in (list, tuple):
            raise AtlasCategoricalGraphError(
                "categorical partition limitations must be an array"
            )
        limitation_rows = [
            _required_text(value, "categorical partition limitation")
            for value in limitations
        ]
        partition_dir = self.output / partition_id
        partition_dir.mkdir()

        node_kinds: Counter[str] = Counter()
        local_nodes: set[str] = set()

        def checked_nodes() -> Iterable[Mapping[str, Any]]:
            for raw in nodes:
                self.check_cancelled()
                row = _validate_node_record(dict(raw))
                node_id = row["id"]
                if node_id in self.node_ids or node_id in local_nodes:
                    raise AtlasCategoricalGraphError(
                        "categorical node identity is invalid or duplicated"
                    )
                local_nodes.add(node_id)
                node_kinds[row["kind"]] += 1
                yield row

        node_count, node_sha256, node_size = _write_jsonl(
            partition_dir / "nodes.jsonl", checked_nodes()
        )
        closure = set(dependency_rows)
        for dependency in dependency_rows:
            closure.update(self._partition_closures[dependency])
        available_nodes = set(local_nodes)
        for dependency in closure:
            available_nodes.update(self._partition_node_ids[dependency])
        relation_counts: Counter[str] = Counter()
        local_edges: set[str] = set()

        def checked_edges() -> Iterable[Mapping[str, Any]]:
            for raw in edges:
                self.check_cancelled()
                row = _validate_edge_record(dict(raw))
                edge_id = row["id"]
                if edge_id in self.edge_ids or edge_id in local_edges:
                    raise AtlasCategoricalGraphError(
                        "categorical edge identity is invalid or duplicated"
                    )
                if row["source"] not in available_nodes or row["target"] not in available_nodes:
                    raise AtlasCategoricalGraphError(
                        "categorical edge endpoint is outside dependency closure (declared)"
                    )
                local_edges.add(edge_id)
                relation_counts[row["relation"]] += 1
                yield row

        edge_count, edge_sha256, edge_size = _write_jsonl(
            partition_dir / "edges.jsonl", checked_edges()
        )
        partition = {
            "partition_id": partition_id,
            "classification": classification,
            "dependencies": list(dependency_rows),
            "evidence_categories": list(category_rows),
            "nodes": {
                "file": f"{partition_id}/nodes.jsonl",
                "count": node_count,
                "size": node_size,
                "sha256": node_sha256,
                "kinds": dict(sorted(node_kinds.items())),
            },
            "edges": {
                "file": f"{partition_id}/edges.jsonl",
                "count": edge_count,
                "size": edge_size,
                "sha256": edge_sha256,
                "relations": dict(sorted(relation_counts.items())),
            },
            "limitations": limitation_rows,
        }
        partition["partition_content_id"] = _PARTITION_ID_PREFIX + _sha256_bytes(
            _canonical_bytes(partition)
        )
        self.partitions.append(partition)
        self.partition_ids.add(partition_id)
        self.node_ids.update(local_nodes)
        self.edge_ids.update(local_edges)
        self._partition_closures[partition_id] = closure
        self._partition_node_ids[partition_id] = local_nodes
        return partition

    def close(self) -> dict[str, Any]:
        self.check_cancelled()
        if self._closed:
            raise AtlasCategoricalGraphError("categorical graph bundle is already closed")
        if not self.partitions:
            raise AtlasCategoricalGraphError("categorical graph bundle has no partitions")
        manifest: dict[str, Any] = {
            "format": (OBSERVATION_BUNDLE_FORMAT
                       if self.evidence_authority == "retained-observations-v1" else BUNDLE_FORMAT),
            "schema_version": 3 if self.evidence_authority == "retained-observations-v1" else 2,
            "validation_profile": STRICT_VALIDATION_PROFILE,
            "graph_set_id": "",
            "authority": dict(_AUTHORITIES[self.evidence_authority]),
            "scope": self.scope,
            "evidence_binding": self.evidence_binding,
            "partitions": self.partitions,
            "summary": {
                "partition_count": len(self.partitions),
                "node_count": len(self.node_ids),
                "edge_count": len(self.edge_ids),
            },
            "query_index": None,
        }
        manifest["graph_set_id"] = _bundle_identity(manifest)
        validate_bundle_manifest(manifest)
        index_path = self.output / _QUERY_INDEX_FILE
        _build_index_from_streams(self.output, manifest, index_path,
                                 check_cancelled=self.check_cancelled)
        manifest["query_index"] = _index_descriptor(
            index_path, manifest["graph_set_id"], check_cancelled=self.check_cancelled
        )
        validate_bundle_manifest(manifest)
        self.check_cancelled()
        try:
            (self.output / "manifest.json").write_bytes(_manifest_bytes(manifest))
        except OSError as exc:
            raise AtlasCategoricalGraphError(
                f"categorical graph manifest cannot be written: {exc}"
            ) from exc
        self._closed = True
        return manifest


class CategoricalGraphQuery:
    """Small exact traversal API over a verified, immutable SQLite index."""

    def __init__(self, bundle: Path, *, rebuild_if_missing: bool = False,
                 check_cancelled: Callable[[], None] | None = None) -> None:
        if type(rebuild_if_missing) is not bool:
            raise AtlasCategoricalGraphError(
                "categorical graph rebuild-if-missing flag must be boolean"
            )
        self.bundle = _bundle_root(bundle)
        self.manifest = validate_bundle_directory(self.bundle, check_cancelled=check_cancelled)
        descriptor = self.manifest["query_index"]
        index_missing = descriptor is None
        if descriptor is not None:
            candidate = self.bundle / descriptor["file"]
            index_missing = not candidate.exists()
        if index_missing and rebuild_if_missing:
            self.manifest = rebuild_query_index(self.bundle, check_cancelled=check_cancelled)
        elif index_missing:
            raise AtlasCategoricalGraphError(
                "categorical graph query index is missing; explicit rebuild is required"
            )
        self.connection = _verified_index_connection(self.bundle, self.manifest, check_cancelled=check_cancelled)

    def close(self) -> None:
        self.connection.close()

    def node(self, kind: str, semantic_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT id,kind,semantic_key,properties_json,evidence_json FROM nodes WHERE kind=? AND semantic_key=?",
            (kind, semantic_key),
        ).fetchone()
        return None if row is None else self._node_row(row)

    @staticmethod
    def _node_row(row: Sequence[str]) -> dict[str, Any]:
        return {
            "format": NODE_FORMAT,
            "schema_version": 2,
            "id": row[0],
            "kind": row[1],
            "semantic_key": row[2],
            "properties": json.loads(row[3]),
            "evidence": json.loads(row[4]),
        }

    @staticmethod
    def _edge_row(row: Sequence[str]) -> dict[str, Any]:
        return {
            "format": EDGE_FORMAT,
            "schema_version": 2,
            "id": _edge_id(row[0], row[1], row[2], row[3]),
            "relation": row[0],
            "source": row[1],
            "target": row[2],
            "semantic_key": row[3],
            "properties": json.loads(row[4]),
            "evidence": json.loads(row[5]),
        }

    def _node_key(self, node_id: str) -> int | None:
        row = self.connection.execute(
            "SELECT node_key FROM nodes WHERE id=?", (node_id,)
        ).fetchone()
        return None if row is None else int(row[0])

    @staticmethod
    def _limit(limit: int) -> int:
        if type(limit) is not int or not 1 <= limit <= 100_000:
            raise AtlasCategoricalGraphError(
                "categorical query limit is outside 1..100000"
            )
        return limit

    def outgoing(
        self, node_id: str, relation: str | None = None, *, limit: int = 10_000
    ) -> list[dict[str, Any]]:
        limit = self._limit(limit)
        node_key = self._node_key(node_id)
        if node_key is None:
            return []
        if relation is None:
            rows = self.connection.execute(
                "SELECT e.relation,s.id,t.id,e.semantic_key,e.properties_json,e.evidence_json FROM edges e JOIN nodes s ON s.node_key=e.source_node JOIN nodes t ON t.node_key=e.target_node WHERE e.source_node=? ORDER BY e.relation,t.id,e.edge_key LIMIT ?",
                (node_key, limit),
            )
        else:
            rows = self.connection.execute(
                "SELECT e.relation,s.id,t.id,e.semantic_key,e.properties_json,e.evidence_json FROM edges e JOIN nodes s ON s.node_key=e.source_node JOIN nodes t ON t.node_key=e.target_node WHERE e.source_node=? AND e.relation=? ORDER BY t.id,e.edge_key LIMIT ?",
                (node_key, relation, limit),
            )
        return [self._edge_row(row) for row in rows]

    def incoming(
        self, node_id: str, relation: str | None = None, *, limit: int = 10_000
    ) -> list[dict[str, Any]]:
        limit = self._limit(limit)
        node_key = self._node_key(node_id)
        if node_key is None:
            return []
        if relation is None:
            rows = self.connection.execute(
                "SELECT e.relation,s.id,t.id,e.semantic_key,e.properties_json,e.evidence_json FROM edges e JOIN nodes s ON s.node_key=e.source_node JOIN nodes t ON t.node_key=e.target_node WHERE e.target_node=? ORDER BY e.relation,s.id,e.edge_key LIMIT ?",
                (node_key, limit),
            )
        else:
            rows = self.connection.execute(
                "SELECT e.relation,s.id,t.id,e.semantic_key,e.properties_json,e.evidence_json FROM edges e JOIN nodes s ON s.node_key=e.source_node JOIN nodes t ON t.node_key=e.target_node WHERE e.target_node=? AND e.relation=? ORDER BY s.id,e.edge_key LIMIT ?",
                (node_key, relation, limit),
            )
        return [self._edge_row(row) for row in rows]

    def __enter__(self) -> "CategoricalGraphQuery":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
