"""Bounded exact equivalence for full and incremental shard assemblies.

The checker treats every supplied :class:`PartitionAssembly` as untrusted
transport.  It reconstructs each shard from canonical descriptor and NDJSON
bytes, rederives row scope, counts, and roots, and compares every
identity-bearing value byte-for-byte.  ``reused_coordinates`` is the sole
non-identity field: it is validated independently and retained in the receipt.

This module owns no materialization, dependency-impact, storage, publication,
or reference behavior.  A successful receipt proves equality only for the two
bounded assemblies supplied to this call.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from workbench_crucible import ValidatedRecord, load_canonical_record
from workbench_api.canonical import canonical_json_bytes

from .kernel import (
    GRAPH_RECORD_KINDS,
    AuthorityBinding,
    GraphAssemblyScope,
    GraphRecordCounts,
    GraphRecordRoots,
    KernelValidationError,
    OwnerPolicyBinding,
    PartitionAssembly,
    ShardArtifact,
    ShardCoordinate,
    ShardKernelConfig,
    _counts_and_roots,
    _scope_from_record,
    _snapshot_config,
    _validate_prior_artifact,
)


_MAX_SHARDS = 4_096
_MAX_RECORDS = 100_000
_MAX_ROW_BYTES = 64 * 1024 * 1024
_MAX_DESCRIPTOR_BYTES = 64 * 1024 * 1024
_MAX_SHARD_BYTES = 128 * 1024 * 1024
_MAX_CANONICAL_BYTES = 512 * 1024 * 1024
_RECEIPT_DOMAIN = b"workbench-crucible-partition-equivalence-v1\n"
_GRAPH_KIND_ORDER = {kind: index for index, kind in enumerate(GRAPH_RECORD_KINDS)}


@dataclass(frozen=True, slots=True)
class PartitionAssemblyEquivalenceReceipt:
    """Immutable proof that two validated assembly identities are exact equals."""

    canonical_projection_bytes: bytes
    canonical_projection_sha256: str
    reused_coordinates: tuple[ShardCoordinate, ...]
    receipt_sha256: str


def _fail(code: str, path: str, message: str) -> None:
    raise KernelValidationError(code, path, message)


def _coordinate_projection(
    value: Any,
    path: str,
    *,
    error_code: str,
) -> tuple[ShardCoordinate, dict[str, Any]]:
    if type(value) is not ShardCoordinate:
        _fail(error_code, path, "coordinate must be an exact ShardCoordinate")
    if (
        type(value.partition_key) is not str
        or not value.partition_key
        or len(value.partition_key) > 8192
    ):
        _fail(error_code, path + "/partition_key", "partition key must be non-empty")
    if (
        "\r" in value.partition_key
        or "\n" in value.partition_key
        or "\x00" in value.partition_key
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value.partition_key)
    ):
        _fail(error_code, path + "/partition_key", "partition key is not semantic text")
    if type(value.record_kind) is not str or value.record_kind not in _GRAPH_KIND_ORDER:
        _fail(error_code, path + "/record_kind", "record kind is not a graph shard kind")
    if (
        type(value.shard_ordinal) is not int
        or value.shard_ordinal < 0
        or value.shard_ordinal > (1 << 63) - 1
    ):
        _fail(error_code, path + "/shard_ordinal", "shard ordinal is out of bounds")
    coordinate = ShardCoordinate(
        value.partition_key,
        value.record_kind,
        value.shard_ordinal,
    )
    return coordinate, {
        "partition_key": coordinate.partition_key,
        "record_kind": coordinate.record_kind,
        "shard_ordinal": coordinate.shard_ordinal,
    }


def _coordinate_key(value: ShardCoordinate) -> tuple[bytes, int, int]:
    return (
        value.partition_key.encode("utf-8"),
        _GRAPH_KIND_ORDER[value.record_kind],
        value.shard_ordinal,
    )


def _authority_projection(value: Any, path: str) -> dict[str, str]:
    if type(value) is not AuthorityBinding:
        _fail(
            "kernel.equivalence-invalid-assembly",
            path,
            "authority must be an exact AuthorityBinding",
        )
    fields = {
        "owner_authority_id": value.owner_authority_id,
        "owner_revision_id": value.owner_revision_id,
        "authority_adapter_id": value.authority_adapter_id,
    }
    for field, item in fields.items():
        if type(item) is not str:
            _fail(
                "kernel.equivalence-invalid-assembly",
                f"{path}/{field}",
                "authority field must be an exact string",
            )
    return fields


def _policy_projection(value: Any, path: str) -> dict[str, Any]:
    if type(value) is not OwnerPolicyBinding:
        _fail(
            "kernel.equivalence-invalid-assembly",
            path,
            "policy must be an exact OwnerPolicyBinding",
        )
    if type(value.policy_id) is not str:
        _fail(
            "kernel.equivalence-invalid-assembly",
            path + "/policy_id",
            "policy ID must be an exact string",
        )
    return {
        "policy_id": value.policy_id,
        "authority": _authority_projection(value.authority, path + "/authority"),
    }


def _scope_projection(value: Any, path: str) -> dict[str, Any]:
    if type(value) is not GraphAssemblyScope:
        _fail(
            "kernel.equivalence-invalid-assembly",
            path,
            "non-empty assembly scope must be an exact GraphAssemblyScope",
        )
    fields = {
        "graph_family_id": value.graph_family_id,
        "category_id": value.category_id,
        "resolution_id": value.resolution_id,
        "context_ref_id": value.context_ref_id,
        "recipe_id": value.recipe_id,
    }
    for field, item in fields.items():
        if type(item) is not str:
            _fail(
                "kernel.equivalence-invalid-assembly",
                f"{path}/{field}",
                "scope field must be an exact string",
            )
    return {
        "authority_owner": _authority_projection(
            value.authority_owner, path + "/authority_owner"
        ),
        **fields,
    }


def _counts_projection(value: Any, path: str) -> dict[str, int]:
    if type(value) is not GraphRecordCounts:
        _fail(
            "kernel.equivalence-invalid-assembly",
            path,
            "record counts must be an exact GraphRecordCounts",
        )
    result = value.to_dict()
    for field, item in result.items():
        if type(item) is not int or item < 0:
            _fail(
                "kernel.equivalence-invalid-assembly",
                f"{path}/{field}",
                "record count must be a non-negative exact integer",
            )
    return result


def _roots_projection(value: Any, path: str) -> dict[str, str | None]:
    if type(value) is not GraphRecordRoots:
        _fail(
            "kernel.equivalence-invalid-assembly",
            path,
            "record roots must be an exact GraphRecordRoots",
        )
    result = value.to_dict()
    for field, item in result.items():
        if item is not None and (
            type(item) is not str
            or len(item) != 64
            or any(character not in "0123456789abcdef" for character in item)
        ):
            _fail(
                "kernel.equivalence-invalid-assembly",
                f"{path}/{field}",
                "record root must be null or one lowercase SHA-256 digest",
            )
    return result


def _validated_config(source: PartitionAssembly, path: str) -> tuple[ShardKernelConfig, dict[str, Any]]:
    if type(source.record_schema_object_descriptor_id) is not str:
        _fail(
            "kernel.equivalence-invalid-assembly",
            path + "/record_schema_object_descriptor_id",
            "schema descriptor ID must be an exact string",
        )
    total_order = _policy_projection(
        source.total_order_policy, path + "/total_order_policy"
    )
    partition = _policy_projection(
        source.partition_policy, path + "/partition_policy"
    )
    try:
        config = _snapshot_config(
            ShardKernelConfig(
                source.record_schema_object_descriptor_id,
                OwnerPolicyBinding(
                    total_order["policy_id"],
                    AuthorityBinding(**total_order["authority"]),
                ),
                OwnerPolicyBinding(
                    partition["policy_id"],
                    AuthorityBinding(**partition["authority"]),
                ),
                # Shard width is not retained by PartitionAssembly.  A value of
                # one validates every other exact config field without making a
                # claim about how the already-framed shards were planned.
                1,
            )
        )
    except KernelValidationError as exc:
        suffix = exc.path.removeprefix("/config")
        raise KernelValidationError(
            "kernel.equivalence-invalid-assembly",
            path + suffix,
            "assembly policy or schema binding is invalid",
        ) from exc
    return config, {
        "record_schema_object_descriptor_id": config.record_schema_object_descriptor_id,
        "total_order_policy": total_order,
        "partition_policy": partition,
    }


def _preflight_artifact(
    candidate: Any,
    path: str,
    *,
    prior_record_count: int,
    prior_canonical_bytes: int,
) -> tuple[int, int]:
    """Bound one artifact without splitting, parsing, or copying its bytes."""

    if type(candidate) is not ShardArtifact:
        _fail(
            "kernel.equivalence-invalid-artifact",
            path,
            "shard must be an exact ShardArtifact",
        )
    raw = candidate.ndjson_bytes
    if type(raw) is not bytes or not raw:
        _fail(
            "kernel.equivalence-invalid-artifact",
            path + "/ndjson_bytes",
            "canonical NDJSON must be non-empty immutable bytes",
        )
    descriptor = candidate.object_descriptor
    if type(descriptor) is not ValidatedRecord or type(
        descriptor.canonical_bytes
    ) is not bytes:
        _fail(
            "kernel.equivalence-invalid-artifact",
            path + "/object_descriptor",
            "descriptor must carry exact immutable canonical bytes",
        )
    descriptor_bytes = descriptor.canonical_bytes
    if len(raw) > _MAX_SHARD_BYTES:
        _fail(
            "kernel.equivalence-bound",
            path + "/ndjson_bytes",
            "shard exceeds the canonical NDJSON byte bound",
        )
    if len(descriptor_bytes) > _MAX_DESCRIPTOR_BYTES:
        _fail(
            "kernel.equivalence-bound",
            path + "/object_descriptor",
            "descriptor exceeds the canonical descriptor byte bound",
        )
    canonical_bytes = len(raw) + len(descriptor_bytes)
    if prior_canonical_bytes + canonical_bytes > _MAX_CANONICAL_BYTES:
        _fail(
            "kernel.equivalence-bound",
            path,
            "assembly exceeds the aggregate canonical-byte bound",
        )

    # Scan exact LF framing with offsets.  ``bytes.split`` would allocate every
    # row before the row-count and row-byte ceilings had been established.
    row_count = 0
    start = 0
    while start < len(raw):
        newline = raw.find(b"\n", start)
        if newline < 0:
            _fail(
                "kernel.equivalence-invalid-artifact",
                path + "/ndjson_bytes",
                "canonical NDJSON contains an unterminated row",
            )
        if newline == start:
            _fail(
                "kernel.equivalence-invalid-artifact",
                path + "/ndjson_bytes",
                "canonical NDJSON contains an empty row",
            )
        if newline - start > _MAX_ROW_BYTES:
            _fail(
                "kernel.equivalence-bound",
                f"{path}/rows/{row_count}",
                "canonical NDJSON row exceeds the row byte bound",
            )
        row_count += 1
        if prior_record_count + row_count > _MAX_RECORDS:
            _fail(
                "kernel.equivalence-bound",
                path + "/ndjson_bytes",
                f"assembly exceeds the {_MAX_RECORDS} record bound",
            )
        start = newline + 1
    for field, metadata in (
        ("record_ids", candidate.record_ids),
        ("logical_keys", candidate.logical_keys),
    ):
        if type(metadata) is not tuple:
            _fail(
                "kernel.equivalence-invalid-artifact",
                f"{path}/{field}",
                "row metadata must be an exact immutable tuple",
            )
        if len(metadata) != row_count:
            _fail(
                "kernel.equivalence-invalid-artifact",
                f"{path}/{field}",
                "row metadata length must equal the bounded NDJSON row count",
            )
    return row_count, canonical_bytes


def _artifact_projection(
    candidate: Any,
    path: str,
    *,
    config: ShardKernelConfig,
) -> tuple[ShardArtifact, dict[str, Any], dict[str, Any]]:
    try:
        artifact = _validate_prior_artifact(candidate, path)
    except KernelValidationError as exc:
        raise KernelValidationError(
            "kernel.equivalence-invalid-artifact",
            exc.path,
            "shard cannot be reconstructed from its canonical bytes",
        ) from exc

    descriptor = artifact.object_descriptor.to_dict()
    expected_descriptor = {
        "described_schema_object_descriptor_id": config.record_schema_object_descriptor_id,
        "total_order_policy_id": config.total_order_policy.policy_id,
        "total_order_authority": config.total_order_policy.authority.to_dict(),
    }
    for field, expected in expected_descriptor.items():
        if descriptor.get(field) != expected:
            _fail(
                "kernel.equivalence-invalid-artifact",
                f"{path}/object_descriptor/{field}",
                "shard descriptor disagrees with its assembly binding",
            )

    coordinate, coordinate_value = _coordinate_projection(
        artifact.coordinate,
        path + "/coordinate",
        error_code="kernel.equivalence-invalid-artifact",
    )
    row_scopes: list[dict[str, Any]] = []
    for index, raw in enumerate(artifact.ndjson_bytes.splitlines()):
        # _validate_prior_artifact already proved exact LF framing and C01
        # canonical rows.  Reloading here derives scope only from those bytes.
        record = load_canonical_record(raw)
        row_scopes.append(
            _scope_projection(
                _scope_from_record(record.to_dict()),
                f"{path}/rows/{index}/scope",
            )
        )
    first_scope = row_scopes[0]
    if any(scope != first_scope for scope in row_scopes[1:]):
        _fail(
            "kernel.equivalence-invalid-artifact",
            path + "/rows",
            "one shard contains rows from different graph scopes",
        )

    exact = {
        "coordinate": coordinate_value,
        "record_ids": list(artifact.record_ids),
        "logical_keys": list(artifact.logical_keys),
        "ndjson_bytes": artifact.ndjson_bytes,
        "object_descriptor_bytes": artifact.object_descriptor.canonical_bytes,
        "partition": artifact.partition.to_dict(),
    }
    canonical = {
        "coordinate": coordinate_value,
        "record_ids": list(artifact.record_ids),
        "logical_keys": list(artifact.logical_keys),
        "ndjson_byte_length": len(artifact.ndjson_bytes),
        "ndjson_sha256": hashlib.sha256(artifact.ndjson_bytes).hexdigest(),
        "object_descriptor_id": artifact.object_descriptor.id,
        "object_descriptor_byte_length": len(
            artifact.object_descriptor.canonical_bytes
        ),
        "object_descriptor_sha256": hashlib.sha256(
            artifact.object_descriptor.canonical_bytes
        ).hexdigest(),
        "partition": artifact.partition.to_dict(),
    }
    return artifact, exact, {"canonical": canonical, "scope": first_scope}


def _normalize_assembly(
    source: Any,
    path: str,
) -> tuple[dict[str, Any], dict[str, Any], tuple[ShardCoordinate, ...]]:
    if type(source) is not PartitionAssembly:
        _fail(
            "kernel.equivalence-invalid-assembly",
            path,
            "input must be an exact PartitionAssembly",
        )
    if type(source.shards) is not tuple:
        _fail(
            "kernel.equivalence-invalid-assembly",
            path + "/shards",
            "shards must be an immutable tuple",
        )
    if len(source.shards) > _MAX_SHARDS:
        _fail(
            "kernel.equivalence-bound",
            path + "/shards",
            f"assembly exceeds the {_MAX_SHARDS} shard bound",
        )
    if type(source.reused_coordinates) is not tuple:
        _fail(
            "kernel.equivalence-invalid-reuse",
            path + "/reused_coordinates",
            "reuse coordinates must be an immutable tuple",
        )
    if len(source.reused_coordinates) > len(source.shards):
        _fail(
            "kernel.equivalence-invalid-reuse",
            path + "/reused_coordinates",
            "reuse coordinates cannot outnumber current shards",
        )
    config, config_projection = _validated_config(source, path)

    # Complete the cheap structural/byte preflight for the whole assembly
    # before any artifact validator splits NDJSON, parses C01, or constructs
    # reconstructed rows.  Aggregate bounds therefore cannot arrive after a
    # large prefix has already been parsed.
    total_records = 0
    total_bytes = 0
    for index, candidate in enumerate(source.shards):
        record_count, canonical_bytes = _preflight_artifact(
            candidate,
            f"{path}/shards/{index}",
            prior_record_count=total_records,
            prior_canonical_bytes=total_bytes,
        )
        total_records += record_count
        total_bytes += canonical_bytes

    artifacts: list[ShardArtifact] = []
    exact_shards: list[dict[str, Any]] = []
    canonical_shards: list[dict[str, Any]] = []
    row_scope: dict[str, Any] | None = None
    record_ids: set[str] = set()
    logical_keys: set[str] = set()
    for index, candidate in enumerate(source.shards):
        artifact, exact, derived = _artifact_projection(
            candidate,
            f"{path}/shards/{index}",
            config=config,
        )
        if any(item in record_ids for item in artifact.record_ids):
            _fail(
                "kernel.equivalence-invalid-assembly",
                f"{path}/shards/{index}/record_ids",
                "record IDs must be globally unique",
            )
        if any(item in logical_keys for item in artifact.logical_keys):
            _fail(
                "kernel.equivalence-invalid-assembly",
                f"{path}/shards/{index}/logical_keys",
                "logical keys must be globally unique",
            )
        record_ids.update(artifact.record_ids)
        logical_keys.update(artifact.logical_keys)
        if row_scope is None:
            row_scope = derived["scope"]
        elif row_scope != derived["scope"]:
            _fail(
                "kernel.equivalence-invalid-assembly",
                f"{path}/shards/{index}",
                "assembly contains rows from different graph scopes",
            )
        artifacts.append(artifact)
        exact_shards.append(exact)
        canonical_shards.append(derived["canonical"])

    coordinates = tuple(artifact.coordinate for artifact in artifacts)
    if coordinates != tuple(sorted(coordinates, key=_coordinate_key)):
        _fail(
            "kernel.equivalence-invalid-assembly",
            path + "/shards",
            "shards are not in canonical coordinate order",
        )
    if len(coordinates) != len(set(coordinates)):
        _fail(
            "kernel.equivalence-invalid-assembly",
            path + "/shards",
            "shard coordinates must be unique",
        )

    groups: dict[tuple[str, str], list[ShardArtifact]] = {}
    for artifact in artifacts:
        groups.setdefault(
            (artifact.coordinate.partition_key, artifact.coordinate.record_kind),
            [],
        ).append(artifact)
    for group in groups.values():
        if tuple(item.coordinate.shard_ordinal for item in group) != tuple(
            range(len(group))
        ):
            _fail(
                "kernel.equivalence-invalid-assembly",
                path + "/shards",
                "shard ordinals must be contiguous from zero within each partition",
            )
        for left, right in zip(group, group[1:]):
            if not left.logical_keys[-1].encode("utf-8") < right.logical_keys[0].encode(
                "utf-8"
            ):
                _fail(
                    "kernel.equivalence-invalid-assembly",
                    path + "/shards",
                    "logical-key ranges overlap or regress across shards",
                )

    if artifacts:
        supplied_scope = _scope_projection(source.scope, path + "/scope")
        if supplied_scope != row_scope:
            _fail(
                "kernel.equivalence-invalid-assembly",
                path + "/scope",
                "assembly scope disagrees with its canonical graph rows",
            )
    else:
        if source.scope is not None:
            _fail(
                "kernel.equivalence-invalid-assembly",
                path + "/scope",
                "empty assembly scope must be null",
            )
        supplied_scope = None

    expected_counts, expected_roots = _counts_and_roots(tuple(artifacts))
    counts = _counts_projection(source.record_counts, path + "/record_counts")
    roots = _roots_projection(source.record_roots, path + "/record_roots")
    if counts != expected_counts.to_dict():
        _fail(
            "kernel.equivalence-invalid-assembly",
            path + "/record_counts",
            "record counts disagree with canonical shard membership",
        )
    if roots != expected_roots.to_dict():
        _fail(
            "kernel.equivalence-invalid-assembly",
            path + "/record_roots",
            "record roots disagree with canonical shard membership",
        )

    reuse: list[ShardCoordinate] = []
    reuse_projection: list[dict[str, Any]] = []
    for index, item in enumerate(source.reused_coordinates):
        coordinate, projection = _coordinate_projection(
            item,
            f"{path}/reused_coordinates/{index}",
            error_code="kernel.equivalence-invalid-reuse",
        )
        reuse.append(coordinate)
        reuse_projection.append(projection)
    reuse_tuple = tuple(reuse)
    if reuse_tuple != tuple(sorted(reuse_tuple, key=_coordinate_key)):
        _fail(
            "kernel.equivalence-invalid-reuse",
            path + "/reused_coordinates",
            "reuse coordinates must be in canonical order",
        )
    if len(reuse_tuple) != len(set(reuse_tuple)):
        _fail(
            "kernel.equivalence-invalid-reuse",
            path + "/reused_coordinates",
            "reuse coordinates must be unique",
        )
    available = set(coordinates)
    if any(item not in available for item in reuse_tuple):
        _fail(
            "kernel.equivalence-invalid-reuse",
            path + "/reused_coordinates",
            "reuse coordinates must name exact current shards",
        )

    exact_identity = {
        "scope": supplied_scope,
        **config_projection,
        "shards": exact_shards,
        "record_counts": counts,
        "record_roots": roots,
    }
    canonical_identity = {
        "format": "workbench-crucible-partition-assembly-identity-v1",
        "schema_version": 1,
        "scope": supplied_scope,
        **config_projection,
        "shards": canonical_shards,
        "record_counts": counts,
        "record_roots": roots,
    }
    return exact_identity, canonical_identity, reuse_tuple


def _first_difference(left: Any, right: Any, path: str = "") -> str | None:
    if type(left) is not type(right):
        return path or "/"
    if type(left) is dict:
        if tuple(left) != tuple(right):
            return path or "/"
        for key in left:
            difference = _first_difference(left[key], right[key], f"{path}/{key}")
            if difference is not None:
                return difference
        return None
    if type(left) is list:
        if len(left) != len(right):
            return path or "/"
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            difference = _first_difference(
                left_item, right_item, f"{path}/{index}"
            )
            if difference is not None:
                return difference
        return None
    return None if left == right else (path or "/")


def verify_partition_assembly_equivalence(
    clean: PartitionAssembly,
    incremental: PartitionAssembly,
) -> PartitionAssemblyEquivalenceReceipt:
    """Validate and compare one clean and one incremental K01 assembly.

    Every identity-bearing primitive and canonical byte string must agree.
    Reuse coordinates are excluded from that comparison, validated as exact
    coordinates of current incremental shards, and retained in the receipt.
    """

    clean_exact, clean_projection, clean_reuse = _normalize_assembly(clean, "/clean")
    incremental_exact, incremental_projection, incremental_reuse = _normalize_assembly(
        incremental, "/incremental"
    )
    if clean_reuse:
        _fail(
            "kernel.equivalence-clean-reuse",
            "/clean/reused_coordinates",
            "the clean assembly must not claim shard reuse",
        )
    difference = _first_difference(clean_exact, incremental_exact)
    if difference is not None:
        _fail(
            "kernel.equivalence-mismatch",
            "/identity" + ("" if difference == "/" else difference),
            "clean and incremental identity-bearing assemblies differ",
        )
    # Both normalized identity projections are derived from exact-equal bytes.
    # Comparing them as a defensive invariant also guards projection drift.
    if clean_projection != incremental_projection:
        _fail(
            "kernel.equivalence-mismatch",
            "/canonical_projection",
            "equal assemblies produced different canonical projections",
        )

    projection_bytes = canonical_json_bytes(clean_projection)
    projection_sha256 = hashlib.sha256(projection_bytes).hexdigest()
    reuse_projection = [
        {
            "partition_key": item.partition_key,
            "record_kind": item.record_kind,
            "shard_ordinal": item.shard_ordinal,
        }
        for item in incremental_reuse
    ]
    receipt_projection = canonical_json_bytes(
        {
            "format": "workbench-crucible-partition-assembly-equivalence-receipt-v1",
            "schema_version": 1,
            "canonical_projection_sha256": projection_sha256,
            "reused_coordinates": reuse_projection,
        }
    )
    receipt_sha256 = hashlib.sha256(
        _RECEIPT_DOMAIN + receipt_projection
    ).hexdigest()
    return PartitionAssemblyEquivalenceReceipt(
        canonical_projection_bytes=projection_bytes,
        canonical_projection_sha256=projection_sha256,
        reused_coordinates=incremental_reuse,
        receipt_sha256=receipt_sha256,
    )


__all__ = [
    "PartitionAssemblyEquivalenceReceipt",
    "verify_partition_assembly_equivalence",
]
