#!/usr/bin/env python3

"""Portable, canonical Atlas continuation manifests and deltas.

The traversal-specific modules own frontier meaning.  This module owns the
shared immutable envelope, parent ancestry, tamper detection, JSON-tree delta
composition, and cumulative-budget invariants.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import workbench_atlas.knowledge_catalog as catalog

from workbench_atlas.layout import SCHEMA_ROOT


MANIFEST_SCHEMA = SCHEMA_ROOT / "atlas-continuation-manifest-v1.schema.json"
DELTA_SCHEMA = SCHEMA_ROOT / "atlas-continuation-delta-v1.schema.json"

MANIFEST_FORMAT = "susy-atlas-continuation-manifest-v1"
DELTA_FORMAT = "susy-atlas-continuation-delta-v1"
MANIFEST_PREFIX = "atlas-continuation:sha256:"
DELTA_PREFIX = "atlas-continuation-delta:sha256:"
KINDS = frozenset({"route", "recycling"})
STATUSES = frozenset({"active", "complete", "invalidated"})


class AtlasContinuationError(ValueError):
    """Raised when continuation data is malformed or semantically invalid."""


def canonical_json_payload(value: Any) -> bytes:
    """Return the canonical bytes used by every M2 identity."""

    try:
        rendered = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise AtlasContinuationError(
            f"continuation contains non-canonical JSON: {exc}"
        ) from exc
    return rendered.encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_payload(value)).hexdigest()


def _read_schema(path: Path) -> dict[str, Any]:
    try:
        value = catalog.read_json(path)
    except (OSError, catalog.CatalogError) as exc:
        raise AtlasContinuationError(
            f"invalid continuation schema {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise AtlasContinuationError(
            f"continuation schema is not an object: {path}"
        )
    return value


def _validate_schema(value: Any, path: Path, label: str) -> None:
    schema = _read_schema(path)
    try:
        catalog._validate_schema_definition(schema, path.name)
        catalog._validate_schema_value(value, schema, label)
    except catalog.CatalogError as exc:
        raise AtlasContinuationError(f"invalid {label}: {exc}") from exc


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AtlasContinuationError(f"{label} must be a non-empty string")
    return value


def _sha256_text(value: object, label: str) -> str:
    text = _required_text(value, label)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise AtlasContinuationError(f"{label} must be a lowercase sha256")
    return text


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise AtlasContinuationError(
            f"{label} must be an integer greater than or equal to {minimum}"
        )
    return value


def _canonical_scopes(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise AtlasContinuationError("continuation scopes must be non-empty")
    scopes: list[dict[str, str]] = []
    for index, row in enumerate(value):
        if not isinstance(row, dict) or set(row) != {
            "profile",
            "physical_side",
        }:
            raise AtlasContinuationError(
                f"continuation scope {index} has an invalid shape"
            )
        scopes.append(
            {
                "profile": _required_text(
                    row["profile"], f"continuation scope {index} profile"
                ),
                "physical_side": _required_text(
                    row["physical_side"],
                    f"continuation scope {index} physical side",
                ),
            }
        )
    ordered = sorted(scopes, key=lambda row: (row["profile"], row["physical_side"]))
    if scopes != ordered or len({tuple(row.values()) for row in scopes}) != len(scopes):
        raise AtlasContinuationError(
            "continuation scopes must be unique and canonically ordered"
        )
    return scopes


def _canonical_roots(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise AtlasContinuationError("continuation roots must be non-empty")
    fields = {"profile", "physical_side", "node_kind", "node_id"}
    roots: list[dict[str, str]] = []
    for index, row in enumerate(value):
        if not isinstance(row, dict) or set(row) != fields:
            raise AtlasContinuationError(
                f"continuation root {index} has an invalid shape"
            )
        roots.append(
            {
                key: _required_text(
                    row[key], f"continuation root {index} {key}"
                )
                for key in (
                    "profile",
                    "physical_side",
                    "node_kind",
                    "node_id",
                )
            }
        )
    ordered = sorted(
        roots,
        key=lambda row: (
            row["profile"],
            row["physical_side"],
            row["node_kind"],
            row["node_id"],
        ),
    )
    keys = {
        (
            row["profile"],
            row["physical_side"],
            row["node_kind"],
            row["node_id"],
        )
        for row in roots
    }
    if roots != ordered or len(keys) != len(roots):
        raise AtlasContinuationError(
            "continuation roots must be unique and canonically ordered"
        )
    return roots


def validate_binding(binding: object) -> dict[str, Any]:
    """Validate immutable request, algorithm, authority, and policy identity."""

    if not isinstance(binding, dict):
        raise AtlasContinuationError("continuation binding must be an object")
    required = {
        "query_instance_id",
        "query_instance_sha256",
        "original_request_sha256",
        "algorithm",
        "snapshot_id",
        "scopes",
        "roots",
        "structural_limits",
        "fairness_policy_id",
        "evidence_sha256",
        "runtime_projection",
    }
    if set(binding) != required:
        raise AtlasContinuationError(
            "continuation binding fields differ from the v1 contract"
        )
    query_id = _required_text(
        binding["query_instance_id"], "continuation query instance id"
    )
    if not query_id.startswith("atlas-query:sha256:"):
        raise AtlasContinuationError(
            "continuation query instance id is not an Atlas query identity"
        )
    _sha256_text(
        query_id.removeprefix("atlas-query:sha256:"),
        "continuation query identity digest",
    )
    for field in (
        "query_instance_sha256",
        "original_request_sha256",
        "evidence_sha256",
    ):
        _sha256_text(binding[field], f"continuation {field}")
    algorithm = binding["algorithm"]
    if not isinstance(algorithm, dict) or set(algorithm) != {"id", "version"}:
        raise AtlasContinuationError(
            "continuation algorithm must contain exactly id and version"
        )
    _required_text(algorithm["id"], "continuation algorithm id")
    _integer(algorithm["version"], "continuation algorithm version", minimum=1)
    _required_text(binding["snapshot_id"], "continuation snapshot id")
    _canonical_scopes(binding["scopes"])
    _canonical_roots(binding["roots"])
    limits = binding["structural_limits"]
    if not isinstance(limits, dict) or not limits:
        raise AtlasContinuationError(
            "continuation structural limits must be a non-empty object"
        )
    for key, value in limits.items():
        _required_text(key, "continuation structural-limit key")
        if type(value) not in {int, bool} or (type(value) is int and value < 0):
            raise AtlasContinuationError(
                f"continuation structural limit {key} is invalid"
            )
    if list(limits) != sorted(limits):
        raise AtlasContinuationError(
            "continuation structural limits must be key ordered"
        )
    _required_text(
        binding["fairness_policy_id"], "continuation fairness policy id"
    )
    projection = binding["runtime_projection"]
    if not isinstance(projection, dict) or set(projection) != {
        "format",
        "bytes",
        "sha256",
        "sqlite_application_id",
        "sqlite_user_version",
    }:
        raise AtlasContinuationError(
            "continuation runtime projection binding has an invalid shape"
        )
    if (
        projection["format"]
        != "susy-runtime-graph-query-projection-v1"
    ):
        raise AtlasContinuationError(
            "continuation runtime projection format is unsupported"
        )
    _integer(
        projection["bytes"],
        "continuation runtime projection bytes",
        minimum=1,
    )
    _sha256_text(
        projection["sha256"],
        "continuation runtime projection digest",
    )
    _integer(
        projection["sqlite_application_id"],
        "continuation runtime projection application id",
        minimum=1,
    )
    _integer(
        projection["sqlite_user_version"],
        "continuation runtime projection user version",
        minimum=1,
    )
    return binding


def binding_sha256(binding: object) -> str:
    validated = validate_binding(binding)
    return canonical_sha256(validated)


def _manifest_semantic(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in manifest.items()
        if key != "continuation_id"
    }


def continuation_id(manifest: Mapping[str, Any]) -> str:
    return MANIFEST_PREFIX + canonical_sha256(_manifest_semantic(manifest))


def _delta_semantic(delta: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in delta.items() if key != "delta_id"}


def delta_id(delta: Mapping[str, Any]) -> str:
    return DELTA_PREFIX + canonical_sha256(_delta_semantic(delta))


def _validate_page_ranges(value: object) -> None:
    if not isinstance(value, list):
        raise AtlasContinuationError(
            "continuation page ranges must be an array"
        )
    previous: tuple[str, str, int, int] | None = None
    previous_end_by_subject: dict[tuple[str, str], int] = {}
    for index, row in enumerate(value):
        if not isinstance(row, dict) or set(row) != {
            "stream",
            "subject_id",
            "start",
            "end",
        }:
            raise AtlasContinuationError(
                f"continuation page range {index} has an invalid shape"
            )
        key = (
            _required_text(
                row["stream"], f"continuation page range {index} stream"
            ),
            _required_text(
                row["subject_id"],
                f"continuation page range {index} subject",
            ),
            _integer(
                row["start"], f"continuation page range {index} start"
            ),
            _integer(row["end"], f"continuation page range {index} end"),
        )
        if key[3] <= key[2]:
            raise AtlasContinuationError(
                f"continuation page range {index} must consume work"
            )
        if previous is not None and key <= previous:
            raise AtlasContinuationError(
                "continuation page ranges must be unique and ordered"
            )
        subject = key[:2]
        prior_end = previous_end_by_subject.get(subject)
        if prior_end is not None and key[2] < prior_end:
            raise AtlasContinuationError(
                "continuation page ranges must not overlap"
            )
        previous_end_by_subject[subject] = key[3]
        previous = key


def create_manifest(
    *,
    kind: str,
    binding: dict[str, Any],
    state: dict[str, Any],
    result: dict[str, Any],
    frontier: dict[str, Any],
    allocated_work_items: int,
    consumed_work_items: int,
    cumulative_work_items: int,
    page_ranges: list[dict[str, Any]] | None = None,
    status: str = "active",
    parent: dict[str, Any] | None = None,
    segment_index: int = 0,
    ancestry: list[str] | None = None,
    invalidation_reasons: list[str] | None = None,
) -> dict[str, Any]:
    """Create and validate one immutable continuation manifest."""

    if kind not in KINDS:
        raise AtlasContinuationError(f"unsupported continuation kind: {kind}")
    validate_binding(binding)
    allocated = _integer(
        allocated_work_items, "continuation allocated work items", minimum=1
    )
    consumed = _integer(
        consumed_work_items, "continuation consumed work items"
    )
    cumulative = _integer(
        cumulative_work_items, "continuation cumulative work items"
    )
    if consumed > allocated:
        raise AtlasContinuationError(
            "continuation consumed work exceeds its segment allocation"
        )
    if status not in STATUSES:
        raise AtlasContinuationError(
            f"unsupported continuation status: {status}"
        )
    reasons = [] if invalidation_reasons is None else invalidation_reasons
    if (
        not isinstance(reasons, list)
        or any(not isinstance(reason, str) or not reason for reason in reasons)
        or reasons != sorted(set(reasons))
    ):
        raise AtlasContinuationError(
            "continuation invalidation reasons must be unique and ordered"
        )
    if status == "invalidated" and not reasons:
        raise AtlasContinuationError(
            "invalidated continuation must name a reason"
        )
    if status != "invalidated" and reasons:
        raise AtlasContinuationError(
            "valid continuation cannot carry invalidation reasons"
        )
    ranges = [] if page_ranges is None else page_ranges
    _validate_page_ranges(ranges)
    ranged_work = sum(row["end"] - row["start"] for row in ranges)
    if ranged_work != cumulative:
        raise AtlasContinuationError(
            "continuation cumulative page ranges differ from cumulative work"
        )
    ancestors = [] if ancestry is None else ancestry
    if (
        not isinstance(ancestors, list)
        or any(
            not isinstance(identifier, str)
            or not identifier.startswith(MANIFEST_PREFIX)
            for identifier in ancestors
        )
        or len(set(ancestors)) != len(ancestors)
    ):
        raise AtlasContinuationError(
            "continuation ancestry must contain unique manifest identities"
        )
    if parent is None:
        if segment_index != 0 or ancestors:
            raise AtlasContinuationError(
                "root continuation must have segment zero and no ancestry"
            )
    else:
        if not isinstance(parent, dict) or set(parent) != {
            "continuation_id",
            "manifest_sha256",
        }:
            raise AtlasContinuationError(
                "continuation parent binding has an invalid shape"
            )
        if segment_index < 1 or not ancestors or ancestors[-1] != parent["continuation_id"]:
            raise AtlasContinuationError(
                "continuation parent must be the last ancestry member"
            )
        _sha256_text(
            parent["manifest_sha256"],
            "continuation parent manifest digest",
        )
    document: dict[str, Any] = {
        "schema_version": 1,
        "format": MANIFEST_FORMAT,
        "continuation_id": "",
        "kind": kind,
        "status": status,
        "segment_index": segment_index,
        "parent": parent,
        "ancestry": ancestors,
        "binding": binding,
        "binding_sha256": binding_sha256(binding),
        "segment_budget": {
            "allocated_work_items": allocated,
            "consumed_work_items": consumed,
        },
        "cumulative_budget": {
            "consumed_work_items": cumulative,
        },
        "page_ranges": ranges,
        "frontier": frontier,
        "state": state,
        "state_sha256": canonical_sha256(state),
        "result": result,
        "result_sha256": canonical_sha256(result),
        "invalidation": {
            "valid": status != "invalidated",
            "reasons": reasons,
        },
    }
    document["continuation_id"] = continuation_id(document)
    validate_manifest(document)
    return document


def validate_manifest(manifest: object) -> dict[str, Any]:
    """Validate schema, canonical identity, digests, and ancestry semantics."""

    if not isinstance(manifest, dict):
        raise AtlasContinuationError(
            "continuation manifest must be an object"
        )
    _validate_schema(manifest, MANIFEST_SCHEMA, "continuation manifest")
    if manifest["format"] != MANIFEST_FORMAT:
        raise AtlasContinuationError("unsupported continuation manifest format")
    if manifest["kind"] not in KINDS:
        raise AtlasContinuationError("unsupported continuation kind")
    if manifest["status"] not in STATUSES:
        raise AtlasContinuationError("unsupported continuation status")
    frontier = manifest["frontier"]
    phase = frontier.get("phase")
    if (
        manifest["status"] == "complete"
        and phase != "complete"
    ):
        raise AtlasContinuationError(
            "complete continuation must have a complete frontier"
        )
    if (
        manifest["status"] == "active"
        and phase == "complete"
    ):
        raise AtlasContinuationError(
            "active continuation cannot have a complete frontier"
        )
    validate_binding(manifest["binding"])
    if manifest["binding_sha256"] != binding_sha256(manifest["binding"]):
        raise AtlasContinuationError("continuation binding digest differs")
    if manifest["state_sha256"] != canonical_sha256(manifest["state"]):
        raise AtlasContinuationError("continuation state digest differs")
    if manifest["result_sha256"] != canonical_sha256(manifest["result"]):
        raise AtlasContinuationError("continuation result digest differs")
    if manifest["continuation_id"] != continuation_id(manifest):
        raise AtlasContinuationError("continuation manifest identity differs")
    allocated = _integer(
        manifest["segment_budget"]["allocated_work_items"],
        "continuation allocated work items",
        minimum=1,
    )
    consumed = _integer(
        manifest["segment_budget"]["consumed_work_items"],
        "continuation consumed work items",
    )
    cumulative = _integer(
        manifest["cumulative_budget"]["consumed_work_items"],
        "continuation cumulative work items",
    )
    if consumed > allocated or cumulative < consumed:
        raise AtlasContinuationError(
            "continuation budget accounting is inconsistent"
        )
    _validate_page_ranges(manifest["page_ranges"])
    ranged_work = sum(
        row["end"] - row["start"]
        for row in manifest["page_ranges"]
    )
    if ranged_work != cumulative:
        raise AtlasContinuationError(
            "continuation cumulative page ranges differ from cumulative work"
        )
    reasons = manifest["invalidation"]["reasons"]
    valid = manifest["invalidation"]["valid"]
    if reasons != sorted(set(reasons)):
        raise AtlasContinuationError(
            "continuation invalidation reasons are not canonical"
        )
    if valid != (manifest["status"] != "invalidated") or valid == bool(reasons):
        raise AtlasContinuationError(
            "continuation invalidation status is inconsistent"
        )
    parent = manifest["parent"]
    ancestry = manifest["ancestry"]
    segment = _integer(
        manifest["segment_index"], "continuation segment index"
    )
    if parent is None:
        if segment != 0 or ancestry:
            raise AtlasContinuationError(
                "root continuation ancestry is inconsistent"
            )
    else:
        if (
            segment < 1
            or not ancestry
            or ancestry[-1] != parent["continuation_id"]
            or len(set(ancestry)) != len(ancestry)
        ):
            raise AtlasContinuationError(
                "continuation parent ancestry is inconsistent"
            )
        _sha256_text(
            parent["manifest_sha256"],
            "continuation parent manifest digest",
        )
    return manifest


def manifest_sha256(manifest: object) -> str:
    validated = validate_manifest(manifest)
    return canonical_sha256(validated)


def _escape_pointer(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _unescape_pointer(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _tree_diff(parent: Any, child: Any, path: str = "") -> list[dict[str, Any]]:
    if isinstance(parent, dict) and isinstance(child, dict):
        operations: list[dict[str, Any]] = []
        for key in sorted(set(parent) - set(child), reverse=True):
            operations.append(
                {"op": "remove", "path": path + "/" + _escape_pointer(key)}
            )
        for key in sorted(set(child) - set(parent)):
            operations.append(
                {
                    "op": "add",
                    "path": path + "/" + _escape_pointer(key),
                    "value": child[key],
                }
            )
        for key in sorted(set(parent) & set(child)):
            operations.extend(
                _tree_diff(
                    parent[key],
                    child[key],
                    path + "/" + _escape_pointer(key),
                )
            )
        return operations
    if isinstance(parent, list) and isinstance(child, list):
        operations = []
        common = min(len(parent), len(child))
        for index in range(common):
            operations.extend(
                _tree_diff(
                    parent[index],
                    child[index],
                    path + "/" + str(index),
                )
            )
        for index in range(len(parent) - 1, len(child) - 1, -1):
            operations.append(
                {"op": "remove", "path": path + "/" + str(index)}
            )
        for index in range(common, len(child)):
            operations.append(
                {
                    "op": "add",
                    "path": path + "/" + str(index),
                    "value": child[index],
                }
            )
        return operations
    if parent == child:
        return []
    return [{"op": "replace", "path": path, "value": child}]


def _pointer_parent(document: Any, pointer: str) -> tuple[Any, str]:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise AtlasContinuationError(
            "continuation delta path must be a non-root JSON pointer"
        )
    tokens = [_unescape_pointer(token) for token in pointer[1:].split("/")]
    current = document
    for token in tokens[:-1]:
        if isinstance(current, dict) and token in current:
            current = current[token]
            continue
        if isinstance(current, list):
            try:
                index = int(token)
            except ValueError as exc:
                raise AtlasContinuationError(
                    f"continuation delta array path is invalid: {pointer}"
                ) from exc
            if 0 <= index < len(current):
                current = current[index]
                continue
        raise AtlasContinuationError(
            f"continuation delta path does not exist: {pointer}"
        )
    return current, tokens[-1]


def apply_patch(document: Any, operations: object) -> Any:
    """Apply the bounded object-tree patch dialect used by continuation deltas."""

    if not isinstance(operations, list):
        raise AtlasContinuationError(
            "continuation delta operations must be an array"
        )
    result = copy.deepcopy(document)
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise AtlasContinuationError(
                f"continuation delta operation {index} is not an object"
            )
        op = operation.get("op")
        path = operation.get("path")
        expected_fields = (
            {"op", "path"} if op == "remove" else {"op", "path", "value"}
        )
        if op not in {"add", "remove", "replace"} or set(operation) != expected_fields:
            raise AtlasContinuationError(
                f"continuation delta operation {index} has an invalid shape"
            )
        if not isinstance(path, str) or not path.startswith("/"):
            raise AtlasContinuationError(
                f"continuation delta operation {index} has an invalid path"
            )
        parent, key = _pointer_parent(result, path)
        if isinstance(parent, list):
            try:
                list_index = int(key)
            except ValueError as exc:
                raise AtlasContinuationError(
                    f"continuation delta array index is invalid: {path}"
                ) from exc
            if op == "add":
                if not 0 <= list_index <= len(parent):
                    raise AtlasContinuationError(
                        f"continuation delta add index is invalid: {path}"
                    )
                parent.insert(
                    list_index,
                    copy.deepcopy(operation["value"]),
                )
            elif op == "remove":
                if not 0 <= list_index < len(parent):
                    raise AtlasContinuationError(
                        f"continuation delta remove index is invalid: {path}"
                    )
                parent.pop(list_index)
            else:
                if not 0 <= list_index < len(parent):
                    raise AtlasContinuationError(
                        f"continuation delta replace index is invalid: {path}"
                    )
                parent[list_index] = copy.deepcopy(operation["value"])
            continue
        if not isinstance(parent, dict):
            raise AtlasContinuationError(
                f"continuation delta path parent is not a container: {path}"
            )
        if op == "add":
            if key in parent:
                raise AtlasContinuationError(
                    f"continuation delta add path already exists: {path}"
                )
            parent[key] = copy.deepcopy(operation["value"])
        elif op == "remove":
            if key not in parent:
                raise AtlasContinuationError(
                    f"continuation delta remove path does not exist: {path}"
                )
            del parent[key]
        else:
            if key not in parent:
                raise AtlasContinuationError(
                    f"continuation delta replace path does not exist: {path}"
                )
            parent[key] = copy.deepcopy(operation["value"])
    return result


def create_delta(
    parent: dict[str, Any],
    child: dict[str, Any],
) -> dict[str, Any]:
    """Create the canonical parent-to-child delta after lineage validation."""

    validate_manifest(parent)
    validate_manifest(child)
    if parent["status"] != "active":
        raise AtlasContinuationError(
            "continuation delta parent must be active"
        )
    parent_digest = manifest_sha256(parent)
    if child["parent"] != {
        "continuation_id": parent["continuation_id"],
        "manifest_sha256": parent_digest,
    }:
        raise AtlasContinuationError(
            "continuation child does not bind the supplied parent"
        )
    if child["kind"] != parent["kind"] or child["binding"] != parent["binding"]:
        raise AtlasContinuationError(
            "continuation child changed kind or immutable binding"
        )
    if child["segment_index"] != parent["segment_index"] + 1:
        raise AtlasContinuationError(
            "continuation child segment is not consecutive"
        )
    expected_ancestry = [
        *parent["ancestry"],
        parent["continuation_id"],
    ]
    if child["ancestry"] != expected_ancestry:
        raise AtlasContinuationError(
            "continuation child ancestry is not consecutive"
        )
    parent_budget = parent["cumulative_budget"]["consumed_work_items"]
    child_budget = child["cumulative_budget"]["consumed_work_items"]
    segment_consumed = child["segment_budget"]["consumed_work_items"]
    if child_budget != parent_budget + segment_consumed:
        raise AtlasContinuationError(
            "continuation cumulative budget drifted from its parent"
        )
    parent_ranges = {
        (
            row["stream"],
            row["subject_id"],
            row["start"],
            row["end"],
        )
        for row in parent["page_ranges"]
    }
    child_ranges = {
        (
            row["stream"],
            row["subject_id"],
            row["start"],
            row["end"],
        )
        for row in child["page_ranges"]
    }
    if not parent_ranges.issubset(child_ranges):
        raise AtlasContinuationError(
            "continuation child removed consumed page ranges"
        )
    added_ranges = child_ranges - parent_ranges
    added_work = sum(end - start for _, _, start, end in added_ranges)
    if added_work != segment_consumed:
        raise AtlasContinuationError(
            "continuation page-range increment differs from segment work"
        )
    semantic_parent = _manifest_semantic(parent)
    semantic_child = _manifest_semantic(child)
    operations = _tree_diff(semantic_parent, semantic_child)
    delta: dict[str, Any] = {
        "schema_version": 1,
        "format": DELTA_FORMAT,
        "delta_id": "",
        "kind": child["kind"],
        "parent_continuation_id": parent["continuation_id"],
        "parent_manifest_sha256": parent_digest,
        "child_continuation_id": child["continuation_id"],
        "child_manifest_sha256": manifest_sha256(child),
        "binding_sha256": child["binding_sha256"],
        "segment_index": child["segment_index"],
        "budget_increment": segment_consumed,
        "page_ranges": child["page_ranges"],
        "operations": operations,
    }
    delta["delta_id"] = delta_id(delta)
    validate_delta(delta)
    return delta


def validate_delta(delta: object) -> dict[str, Any]:
    if not isinstance(delta, dict):
        raise AtlasContinuationError("continuation delta must be an object")
    _validate_schema(delta, DELTA_SCHEMA, "continuation delta")
    if delta["format"] != DELTA_FORMAT or delta["kind"] not in KINDS:
        raise AtlasContinuationError("unsupported continuation delta")
    if delta["delta_id"] != delta_id(delta):
        raise AtlasContinuationError("continuation delta identity differs")
    for field in (
        "parent_manifest_sha256",
        "child_manifest_sha256",
        "binding_sha256",
    ):
        _sha256_text(delta[field], f"continuation delta {field}")
    _integer(delta["segment_index"], "continuation delta segment", minimum=1)
    _integer(delta["budget_increment"], "continuation delta budget increment")
    _validate_page_ranges(delta["page_ranges"])
    # Applying to an empty object is not meaningful; shape is checked here and
    # exact path validity is checked against the bound parent during compose.
    if not isinstance(delta["operations"], list):
        raise AtlasContinuationError(
            "continuation delta operations must be an array"
        )
    return delta


def compose_continuation(
    parent: dict[str, Any],
    delta: dict[str, Any],
    *,
    seen_delta_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Reconstruct and validate a child manifest from its exact parent."""

    validate_manifest(parent)
    validate_delta(delta)
    if delta["delta_id"] in set(seen_delta_ids):
        raise AtlasContinuationError(
            "continuation delta replay is not permitted"
        )
    if (
        delta["parent_continuation_id"] != parent["continuation_id"]
        or delta["parent_manifest_sha256"] != manifest_sha256(parent)
    ):
        raise AtlasContinuationError(
            "continuation delta parent binding differs"
        )
    if (
        delta["kind"] != parent["kind"]
        or delta["binding_sha256"] != parent["binding_sha256"]
        or delta["segment_index"] != parent["segment_index"] + 1
    ):
        raise AtlasContinuationError(
            "continuation delta lineage differs from its parent"
        )
    semantic = apply_patch(_manifest_semantic(parent), delta["operations"])
    child = {
        **semantic,
        "continuation_id": delta["child_continuation_id"],
    }
    validate_manifest(child)
    if manifest_sha256(child) != delta["child_manifest_sha256"]:
        raise AtlasContinuationError(
            "continuation composed child digest differs"
        )
    if (
        child["cumulative_budget"]["consumed_work_items"]
        != parent["cumulative_budget"]["consumed_work_items"]
        + delta["budget_increment"]
    ):
        raise AtlasContinuationError(
            "continuation composed budget drifted"
        )
    if child["page_ranges"] != delta["page_ranges"]:
        raise AtlasContinuationError(
            "continuation composed page ranges differ"
        )
    if create_delta(parent, child) != delta:
        raise AtlasContinuationError(
            "continuation delta is not the canonical parent/child patch"
        )
    return child


def _query_binding_fields(query_instance: object) -> dict[str, Any]:
    if not isinstance(query_instance, dict):
        raise AtlasContinuationError(
            "continuation query instance must be an object"
        )
    query_identifier = _required_text(
        query_instance.get("query_instance_id"),
        "continuation query instance id",
    )
    semantic = {
        key: value
        for key, value in query_instance.items()
        if key != "query_instance_id"
    }
    semantic_sha256 = canonical_sha256(semantic)
    if query_identifier != "atlas-query:sha256:" + semantic_sha256:
        raise AtlasContinuationError(
            "continuation query identity does not match its semantic request"
        )
    snapshot_id = _required_text(
        query_instance.get("snapshot_id"),
        "continuation query snapshot id",
    )
    scopes = copy.deepcopy(query_instance.get("scopes"))
    _canonical_scopes(scopes)
    return {
        "query_instance_id": query_identifier,
        "query_instance_sha256": canonical_sha256(query_instance),
        "original_request_sha256": semantic_sha256,
        "snapshot_id": snapshot_id,
        "scopes": scopes,
    }


def _session_roots(session: object) -> list[dict[str, str]]:
    targets = getattr(session, "root_targets", None)
    if not isinstance(targets, tuple) or not targets:
        raise AtlasContinuationError(
            "continuation session has no exact root targets"
        )
    roots = [
        {
            "profile": target.scope.profile,
            "physical_side": target.scope.physical_side,
            "node_kind": str(target.node.get("kind", "")),
            "node_id": str(target.node.get("id", "")),
        }
        for target in targets
    ]
    roots.sort(
        key=lambda row: (
            row["profile"],
            row["physical_side"],
            row["node_kind"],
            row["node_id"],
        )
    )
    return _canonical_roots(roots)


def traversal_binding(
    query_instance: dict[str, Any],
    session: object,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    """Bind one decoded traversal session to accepted M1 request identity."""

    from workbench_atlas.runtime_graph_chain_query import RuntimeGraphChainContinuation
    from workbench_atlas.runtime_graph_recycling_query import (
        RuntimeGraphRecyclingContinuation,
    )

    fields = _query_binding_fields(query_instance)
    if not isinstance(evidence, list) or any(
        not isinstance(row, dict) for row in evidence
    ):
        raise AtlasContinuationError(
            "continuation evidence must be an array of records"
        )
    evidence_ids = [
        _required_text(
            row.get("id"),
            f"continuation evidence {index} id",
        )
        for index, row in enumerate(evidence)
    ]
    if evidence_ids != sorted(evidence_ids) or len(set(evidence_ids)) != len(
        evidence_ids
    ):
        raise AtlasContinuationError(
            "continuation evidence must have unique canonically ordered ids"
        )
    if isinstance(session, RuntimeGraphChainContinuation):
        algorithm_id = session.ALGORITHM_ID
        algorithm_version = session.ALGORITHM_VERSION
        fairness = session.FAIRNESS_POLICY_ID
        limits = session._options_document(session.options)
    elif isinstance(session, RuntimeGraphRecyclingContinuation):
        algorithm_id = session.ALGORITHM_ID
        algorithm_version = session.ALGORITHM_VERSION
        fairness = session.FAIRNESS_POLICY_ID
        limits = session.options.limits()
    else:
        raise AtlasContinuationError(
            "continuation session kind is unsupported"
        )
    roots = _session_roots(session)
    available_scopes = {
        (row["profile"], row["physical_side"])
        for row in fields["scopes"]
    }
    if any(
        (root["profile"], root["physical_side"]) not in available_scopes
        for root in roots
    ):
        raise AtlasContinuationError(
            "continuation roots escape the query scopes"
        )
    binding = {
        **fields,
        "algorithm": {
            "id": algorithm_id,
            "version": algorithm_version,
        },
        "roots": roots,
        "structural_limits": {
            key: limits[key] for key in sorted(limits)
        },
        "fairness_policy_id": fairness,
        "evidence_sha256": canonical_sha256(evidence),
        "runtime_projection": session.reader.projection_identity(),
    }
    validate_binding(binding)
    return binding


def _validate_session_binding(
    session: object,
    binding: dict[str, Any],
) -> None:
    from workbench_atlas.runtime_graph_chain_query import RuntimeGraphChainContinuation
    from workbench_atlas.runtime_graph_recycling_query import (
        RuntimeGraphRecyclingContinuation,
    )

    validate_binding(binding)
    if isinstance(session, RuntimeGraphChainContinuation):
        algorithm = {
            "id": session.ALGORITHM_ID,
            "version": session.ALGORITHM_VERSION,
        }
        limits = session._options_document(session.options)
        fairness = session.FAIRNESS_POLICY_ID
    elif isinstance(session, RuntimeGraphRecyclingContinuation):
        algorithm = {
            "id": session.ALGORITHM_ID,
            "version": session.ALGORITHM_VERSION,
        }
        limits = session.options.limits()
        fairness = session.FAIRNESS_POLICY_ID
    else:
        raise AtlasContinuationError(
            "continuation session kind is unsupported"
        )
    if (
        binding["algorithm"] != algorithm
        or binding["roots"] != _session_roots(session)
        or binding["structural_limits"]
        != {key: limits[key] for key in sorted(limits)}
        or binding["fairness_policy_id"] != fairness
        or binding["runtime_projection"]
        != session.reader.projection_identity()
    ):
        raise AtlasContinuationError(
            "continuation session differs from its immutable binding"
        )


def _manifest_from_segment(
    session: object,
    binding: dict[str, Any],
    segment: dict[str, Any],
    *,
    parent: dict[str, Any] | None,
) -> dict[str, Any]:
    from workbench_atlas.runtime_graph_chain_query import RuntimeGraphChainContinuation
    from workbench_atlas.runtime_graph_recycling_query import (
        RuntimeGraphRecyclingContinuation,
    )

    if isinstance(session, RuntimeGraphChainContinuation):
        kind = "route"
    elif isinstance(session, RuntimeGraphRecyclingContinuation):
        kind = "recycling"
    else:
        raise AtlasContinuationError(
            "continuation session kind is unsupported"
        )
    if parent is None:
        parent_row = None
        ancestry: list[str] = []
        segment_index = 0
    else:
        validate_manifest(parent)
        parent_row = {
            "continuation_id": parent["continuation_id"],
            "manifest_sha256": manifest_sha256(parent),
        }
        ancestry = [*parent["ancestry"], parent["continuation_id"]]
        segment_index = int(parent["segment_index"]) + 1
    page_ranges = sorted(
        [
            *(
                []
                if parent is None
                else copy.deepcopy(parent["page_ranges"])
            ),
            *segment["page_ranges"],
        ],
        key=lambda row: (
            row["stream"],
            row["subject_id"],
            row["start"],
            row["end"],
        ),
    )
    return create_manifest(
        kind=kind,
        binding=binding,
        state=segment["state"],
        result=segment["result"],
        frontier=segment["frontier"],
        allocated_work_items=segment["allocated_work_items"],
        consumed_work_items=segment["consumed_work_items"],
        cumulative_work_items=segment["cumulative_work_items"],
        page_ranges=page_ranges,
        status="complete" if segment["complete"] else "active",
        parent=parent_row,
        segment_index=segment_index,
        ancestry=ancestry,
    )


def start_traversal_continuation(
    session: object,
    query_instance: dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    max_work_items: int,
) -> dict[str, Any]:
    """Advance a new session and emit its root manifest."""

    if getattr(session, "cumulative_work_items", None) != 0:
        raise AtlasContinuationError(
            "new continuation session has already consumed work"
        )
    binding = traversal_binding(query_instance, session, evidence)
    segment = session.step(max_work_items)
    manifest = _manifest_from_segment(
        session,
        binding,
        segment,
        parent=None,
    )
    return {"manifest": manifest, "delta": None}


def resume_traversal_continuation(
    reader: object,
    parent: dict[str, Any],
    *,
    max_work_items: int,
) -> dict[str, Any]:
    """Decode a parent frontier, advance it, and emit child plus delta."""

    from workbench_atlas.runtime_graph_chain_query import RuntimeGraphChainContinuation
    from workbench_atlas.runtime_graph_recycling_query import (
        RuntimeGraphRecyclingContinuation,
    )

    validate_manifest(parent)
    if parent["status"] != "active":
        raise AtlasContinuationError(
            "only an active continuation can be resumed"
        )
    if (
        parent["binding"]["runtime_projection"]
        != reader.projection_identity()
    ):
        raise AtlasContinuationError(
            "continuation runtime projection differs from its immutable "
            "binding"
        )
    if parent["kind"] == "route":
        session = RuntimeGraphChainContinuation.from_state(
            reader,
            parent["state"],
        )
    else:
        session = RuntimeGraphRecyclingContinuation.from_state(
            reader,
            parent["state"],
        )
    _validate_session_binding(session, parent["binding"])
    if (
        session.cumulative_work_items
        != parent["cumulative_budget"]["consumed_work_items"]
        or canonical_sha256(session.result()) != parent["result_sha256"]
        or session.frontier() != parent["frontier"]
    ):
        raise AtlasContinuationError(
            "decoded continuation state differs from its parent projection"
        )
    segment = session.step(max_work_items)
    child = _manifest_from_segment(
        session,
        parent["binding"],
        segment,
        parent=parent,
    )
    delta = create_delta(parent, child)
    return {"manifest": child, "delta": delta}


def invalidate_traversal_continuation(
    parent: dict[str, Any],
    reasons: list[str],
) -> dict[str, Any]:
    """Close an active lineage with explicit, canonical invalidation reasons."""

    validate_manifest(parent)
    if parent["status"] != "active":
        raise AtlasContinuationError(
            "only an active continuation can be invalidated"
        )
    if reasons != sorted(set(reasons)) or any(
        not isinstance(reason, str) or not reason for reason in reasons
    ):
        raise AtlasContinuationError(
            "continuation invalidation reasons must be unique and ordered"
        )
    child = create_manifest(
        kind=parent["kind"],
        binding=copy.deepcopy(parent["binding"]),
        state=copy.deepcopy(parent["state"]),
        result=copy.deepcopy(parent["result"]),
        frontier=copy.deepcopy(parent["frontier"]),
        allocated_work_items=1,
        consumed_work_items=0,
        cumulative_work_items=parent["cumulative_budget"][
            "consumed_work_items"
        ],
        page_ranges=copy.deepcopy(parent["page_ranges"]),
        status="invalidated",
        parent={
            "continuation_id": parent["continuation_id"],
            "manifest_sha256": manifest_sha256(parent),
        },
        segment_index=parent["segment_index"] + 1,
        ancestry=[*parent["ancestry"], parent["continuation_id"]],
        invalidation_reasons=reasons,
    )
    return {
        "manifest": child,
        "delta": create_delta(parent, child),
    }


def compose_traversal_continuation(
    parent: dict[str, Any],
    delta: dict[str, Any],
    *,
    seen_delta_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Compose a delta and independently validate supported result semantics."""

    child = compose_continuation(
        parent,
        delta,
        seen_delta_ids=seen_delta_ids,
    )
    try:
        if child["kind"] == "route":
            from workbench_atlas.runtime_graph_chain_query import (
                validate_route_continuation_projection,
            )

            validate_route_continuation_projection(
                child["state"],
                child["result"],
                child["frontier"],
                cumulative_work_items=child["cumulative_budget"][
                    "consumed_work_items"
                ],
                status=child["status"],
            )
        else:
            from workbench_atlas.runtime_graph_recycling_query import (
                validate_recycling_continuation_projection,
            )

            validate_recycling_continuation_projection(
                child["state"],
                child["result"],
                child["frontier"],
                cumulative_work_items=child["cumulative_budget"][
                    "consumed_work_items"
                ],
                status=child["status"],
            )
    except ValueError as exc:
        raise AtlasContinuationError(
            "continuation child state projection is inconsistent"
        ) from exc
    return child
