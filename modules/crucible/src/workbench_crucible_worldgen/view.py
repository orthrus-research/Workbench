"""Authority-preserving World Studio proving view for CRUCIBLE-M4-V01."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import re
from typing import Any, cast

from workbench_api.service import ServiceExecutionContext
from workbench_api.canonical import canonical_json_bytes, content_id, parse_canonical_json


WORLD_STUDIO_PROVING_SCHEMA_ID = (
    "workbench://schemas/crucible/"
    "crucible-world-studio-proving-view-v1.schema.json"
)
MAX_WORLD_STUDIO_RESULT_BYTES = 4 * 1024 * 1024
MAX_WORLD_STUDIO_QUERY_ROWS = 256
MAX_WORLD_STUDIO_QUERY_ROW_BYTES = 64 * 1024
MAX_WORLD_STUDIO_RESULT_DEPTH = 64
MAX_WORLD_STUDIO_RESULT_NODES = 8192


class WorldStudioProvingViewError(ValueError):
    """A proving-view request or owner input failed closed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WorldStudioProvingViewError(message)


def _content_id(value: Any, prefix: str) -> bool:
    return (
        type(value) is str
        and value.startswith(prefix)
        and re.fullmatch(r"[a-z][a-z0-9-]*:sha256:[0-9a-f]{64}", value)
        is not None
    )


def _detached(value: Any, label: str) -> Any:
    try:
        return parse_canonical_json(canonical_json_bytes(value))
    except (TypeError, ValueError) as exc:
        raise WorldStudioProvingViewError(
            f"{label} is outside canonical JSON V2"
        ) from exc


def _canonical_string_size(value: str) -> int:
    """Measure one canonical JSON string without constructing its encoding."""

    size = 2
    for character in value:
        codepoint = ord(character)
        _require(
            not 0xD800 <= codepoint <= 0xDFFF,
            "JSON string contains a surrogate code point",
        )
        if character in {'"', "\\"}:
            size += 2
        elif codepoint <= 0x1F:
            size += 6
        elif codepoint <= 0x7F:
            size += 1
        elif codepoint <= 0x7FF:
            size += 2
        elif codepoint <= 0xFFFF:
            size += 3
        else:
            size += 4
    return size


def _preflight_json_value(
    value: Any,
    *,
    maximum_bytes: int,
    label: str,
) -> None:
    """Bound an ordinary JSON value before canonicalization or validation."""

    stack = [(value, 0, False)]
    ancestors: set[int] = set()
    nodes = 0
    measured = 0
    while stack:
        item, depth, exiting = stack.pop()
        if exiting:
            ancestors.remove(id(item))
            continue
        nodes += 1
        _require(
            nodes <= MAX_WORLD_STUDIO_RESULT_NODES,
            f"{label} exceeds the structural node bound",
        )
        _require(
            depth <= MAX_WORLD_STUDIO_RESULT_DEPTH,
            f"{label} exceeds the structural depth bound",
        )
        if item is None or type(item) is bool:
            measured += 5
        elif type(item) is int:
            _require(
                -(2**63) <= item <= 2**63 - 1,
                f"{label} contains an integer outside the signed 64-bit domain",
            )
            measured += len(str(item))
        elif type(item) is str:
            _require(
                len(item) <= maximum_bytes,
                f"{label} exceeds the byte bound",
            )
            measured += _canonical_string_size(item)
        elif type(item) is list:
            identity = id(item)
            _require(
                identity not in ancestors,
                f"{label} contains a cyclic array",
            )
            ancestors.add(identity)
            measured += len(item) + 2
            stack.append((item, depth, True))
            stack.extend((nested, depth + 1, False) for nested in item)
        elif type(item) is dict:
            identity = id(item)
            _require(
                identity not in ancestors,
                f"{label} contains a cyclic object",
            )
            ancestors.add(identity)
            measured += len(item) + 2
            stack.append((item, depth, True))
            for key, nested in item.items():
                _require(
                    type(key) is str,
                    f"{label} contains a non-string object key",
                )
                _require(
                    len(key) <= maximum_bytes,
                    f"{label} exceeds the byte bound",
                )
                measured += _canonical_string_size(key) + 1
                stack.append((nested, depth + 1, False))
        else:
            _require(False, f"{label} is outside ordinary JSON values")
        _require(
            measured <= maximum_bytes,
            f"{label} exceeds the byte bound",
        )


def _preflight_query_result(value: Any) -> None:
    """Apply the declared V01 query bounds before canonical owner validation."""

    _require(
        type(value) is dict,
        "worldgen query result must be an ordinary object",
    )
    rows = value.get("results")
    _require(
        type(rows) is list and len(rows) <= MAX_WORLD_STUDIO_QUERY_ROWS,
        "worldgen query result exceeds the declared row bound",
    )
    for row in rows:
        _require(
            type(row) is dict,
            "worldgen query result row must be an ordinary object",
        )
        _preflight_json_value(
            row,
            maximum_bytes=MAX_WORLD_STUDIO_QUERY_ROW_BYTES,
            label="worldgen query result row",
        )
    _preflight_json_value(
        value,
        maximum_bytes=MAX_WORLD_STUDIO_RESULT_BYTES,
        label="worldgen query result",
    )


def _sealed(value: Mapping[str, Any], *, kind: str, identity_field: str) -> bool:
    if type(value) is not dict:
        return False
    body = dict(value)
    supplied = body.pop(identity_field, None)
    return supplied == content_id(kind, body)


def _query_valid(value: Any) -> bool:
    if type(value) is not dict:
        return False
    query_kind = value.get("query")
    if query_kind in {
        "capture-health",
        "known-absence",
        "stale-pattern-conflicts",
    }:
        return set(value) == {"query"}
    if query_kind == "drilldown":
        return (
            set(value) == {"category_id", "query"}
            and value["category_id"] == "atlas.worldgen.realized.v1"
        )
    if query_kind == "site":
        chunk = value.get("chunk")
        return bool(
            set(value)
            == {"category_id", "chunk", "query", "resolution_id"}
            and value["category_id"] == "crucible.worldgen.occurrence.v1"
            and value["resolution_id"] == "worldgen.exact.v1"
            and type(chunk) is dict
            and set(chunk) == {"x", "z"}
            and type(chunk["x"]) is int
            and type(chunk["z"]) is int
        )
    return False


def validate_world_studio_proving_request(value: Mapping[str, Any]) -> bool:
    """Validate the exact V01 service arguments."""

    if type(value) is not dict or set(value) != {
        "action_gate_receipt_id",
        "comparison_graph_set_revision_id",
        "comparison_query",
        "context_ref_id",
        "format",
        "graph_set_revision_id",
        "input_binding_id",
        "operation",
        "proof_index_id",
        "query",
        "schema_version",
    }:
        return False
    comparison_graph = value["comparison_graph_set_revision_id"]
    comparison_query = value["comparison_query"]
    comparison_valid = (
        value["operation"] == "diff"
        and _content_id(comparison_graph, "graph-set-revision:sha256:")
        and comparison_graph != value["graph_set_revision_id"]
        and type(comparison_query) is dict
    ) or (
        value["operation"] in {"query", "visualization"}
        and comparison_graph is None
        and comparison_query is None
    )
    return bool(
        value["format"] == "workbench-world-studio-proving-request-v1"
        and value["schema_version"] == 1
        and value["operation"] in {"query", "diff", "visualization"}
        and _content_id(value["context_ref_id"], "context-ref:sha256:")
        and _content_id(value["input_binding_id"], "input-binding:sha256:")
        and _content_id(
            value["graph_set_revision_id"], "graph-set-revision:sha256:"
        )
        and _content_id(
            value["proof_index_id"], "worldgen-w01-proof-index:sha256:"
        )
        and _content_id(
            value["action_gate_receipt_id"],
            "worldgen-action-gate-receipt:sha256:",
        )
        and _query_valid(value["query"])
        and 1 <= len(canonical_json_bytes(value["query"])) <= 65536
        and (
            comparison_query is None
            or _query_valid(comparison_query)
        )
        and comparison_valid
    )


def _validate_query_result(
    value: Mapping[str, Any],
    graph_set_revision_id: str,
    expected_query: Mapping[str, Any],
) -> dict[str, Any]:
    _preflight_query_result(value)
    result = _detached(value, "worldgen query result")
    _require(
        type(result) is dict
        and set(result)
        == {
            "format",
            "graph_set_revision_id",
            "id",
            "kind",
            "query",
            "raw_archive_open_count",
            "results",
            "schema_version",
            "truncated",
        }
        and result["format"] == "workbench-worldgen-query-result-v1"
        and result["kind"] == "worldgen-query-result"
        and result["schema_version"] == 1
        and result["graph_set_revision_id"] == graph_set_revision_id
        and result["query"] == expected_query
        and _query_valid(result["query"])
        and result["raw_archive_open_count"] == 0
        and type(result["results"]) is list
        and all(type(row) is dict for row in result["results"])
        and type(result["truncated"]) is bool
        and _sealed(result, kind="worldgen-query-result", identity_field="id"),
        "worldgen query result identity, pin, or raw-archive boundary differs",
    )
    return cast(dict[str, Any], result)


def _row_id(row: Mapping[str, Any]) -> str:
    return content_id("world-studio-view-row", row)


def _owner_rows(proof: Mapping[str, Any]) -> list[dict[str, str]]:
    acceptance = proof.get("acceptance")
    authority = (
        acceptance.get("S01-authority")
        if type(acceptance) is dict
        else None
    )
    owners = authority.get("owner_categories") if type(authority) is dict else None
    _require(
        type(owners) is dict
        and authority.get("state") == "pass"
        and bool(owners)
        and all(type(key) is str and type(owner) is str for key, owner in owners.items()),
        "W01 proof has no closed owner-category authority map",
    )
    return [
        {"category_id": category, "owner": owners[category]}
        for category in sorted(owners)
    ]


def _row_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    graph_revision_ids = sorted(
        {
            item
            for key, item in value.items()
            if key.endswith("graph_revision_id") and type(item) is str
        }
    )
    return {
        "category_id": value.get("category_id"),
        "graph_record_id": value.get("graph_record_id"),
        "graph_revision_ids": graph_revision_ids,
        "record_canonical_json": canonical_json_bytes(value).decode("utf-8"),
        "row_id": _row_id(value),
    }


def _query_projection(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "format": "workbench-world-studio-query-projection-v1",
        "graph_set_revision_id": result["graph_set_revision_id"],
        "query": result["query"],
        "query_result_id": result["id"],
        "raw_archive_open_count": result["raw_archive_open_count"],
        "rows": [_row_projection(row) for row in result["results"]],
        "schema_version": 1,
        "truncated": result["truncated"],
    }


def _visualization(result: Mapping[str, Any]) -> dict[str, Any]:
    grouped: dict[str | None, list[str]] = {}
    for value in result["results"]:
        category = value.get("category_id")
        grouped.setdefault(category, []).append(_row_id(value))
    return {
        "kind": "authority-linked-record-list",
        "query_result_id": result["id"],
        "groups": [
            {
                "category_id": category,
                "row_ids": sorted(grouped[category]),
            }
            for category in sorted(
                grouped,
                key=lambda item: b"" if item is None else item.encode("utf-8"),
            )
        ],
    }


def _action_projection(action: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "action": action["action"],
        "context_ref_id": action["context_ref_id"],
        "disposition": action["disposition"],
        "input_binding_id": action["input_binding_id"],
        "policy_sha256": action["policy_sha256"],
        "receipt_canonical_json": canonical_json_bytes(action).decode("utf-8"),
        "receipt_id": action["id"],
    }


def _validate_row_projection(value: Any) -> dict[str, Any]:
    _require(
        type(value) is dict
        and set(value)
        == {
            "category_id",
            "graph_record_id",
            "graph_revision_ids",
            "record_canonical_json",
            "row_id",
        }
        and (
            value["category_id"] is None
            or type(value["category_id"]) is str
        )
        and (
            value["graph_record_id"] is None
            or _content_id(
                value["graph_record_id"], "graph-record:sha256:"
            )
        )
        and type(value["graph_revision_ids"]) is list
        and value["graph_revision_ids"]
        == sorted(set(value["graph_revision_ids"]))
        and all(
            _content_id(item, "graph-revision:sha256:")
            for item in value["graph_revision_ids"]
        )
        and type(value["record_canonical_json"]) is str,
        "World Studio row projection is invalid or open",
    )
    record = parse_canonical_json(value["record_canonical_json"].encode("utf-8"))
    _require(
        type(record) is dict
        and canonical_json_bytes(record).decode("utf-8")
        == value["record_canonical_json"]
        and value["category_id"] == record.get("category_id")
        and value["graph_record_id"] == record.get("graph_record_id")
        and value["graph_revision_ids"]
        == sorted(
            {
                item
                for key, item in record.items()
                if key.endswith("graph_revision_id") and type(item) is str
            }
        )
        and value["row_id"] == _row_id(record),
        "World Studio row projection differs from its canonical record",
    )
    return cast(dict[str, Any], record)


def _validate_query_projection(
    value: Any, graph_set_revision_id: Any
) -> dict[str, Any]:
    projection = _detached(value, "World Studio query projection")
    _require(
        type(projection) is dict
        and set(projection)
        == {
            "format",
            "graph_set_revision_id",
            "query",
            "query_result_id",
            "raw_archive_open_count",
            "rows",
            "schema_version",
            "truncated",
        }
        and projection["format"]
        == "workbench-world-studio-query-projection-v1"
        and projection["schema_version"] == 1
        and projection["graph_set_revision_id"] == graph_set_revision_id
        and _content_id(graph_set_revision_id, "graph-set-revision:sha256:")
        and _content_id(
            projection["query_result_id"],
            "worldgen-query-result:sha256:",
        )
        and _query_valid(projection["query"])
        and projection["raw_archive_open_count"] == 0
        and type(projection["rows"]) is list
        and type(projection["truncated"]) is bool,
        "World Studio query projection is invalid or open",
    )
    rows = [_validate_row_projection(row) for row in projection["rows"]]
    original_body = {
        "format": "workbench-worldgen-query-result-v1",
        "graph_set_revision_id": graph_set_revision_id,
        "kind": "worldgen-query-result",
        "query": projection["query"],
        "raw_archive_open_count": 0,
        "results": rows,
        "schema_version": 1,
        "truncated": projection["truncated"],
    }
    _require(
        projection["query_result_id"]
        == content_id("worldgen-query-result", original_body),
        "World Studio query projection does not retain the owner result identity",
    )
    return cast(dict[str, Any], projection)


def _projection_visualization(
    projection: Mapping[str, Any],
) -> dict[str, Any]:
    grouped: dict[str | None, list[str]] = {}
    for row in projection["rows"]:
        grouped.setdefault(row["category_id"], []).append(row["row_id"])
    return {
        "groups": [
            {
                "category_id": category,
                "row_ids": sorted(grouped[category]),
            }
            for category in sorted(
                grouped,
                key=lambda item: b"" if item is None else item.encode("utf-8"),
            )
        ],
        "kind": "authority-linked-record-list",
        "query_result_id": projection["query_result_id"],
    }


def _action_projection_valid(
    value: Any, context_ref_id: str, input_binding_id: str
) -> bool:
    if type(value) is not dict or set(value) != {
        "action",
        "context_ref_id",
        "disposition",
        "input_binding_id",
        "policy_sha256",
        "receipt_canonical_json",
        "receipt_id",
    }:
        return False
    try:
        receipt = parse_canonical_json(
            value["receipt_canonical_json"].encode("utf-8")
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return bool(
        type(receipt) is dict
        and canonical_json_bytes(receipt).decode("utf-8")
        == value["receipt_canonical_json"]
        and receipt.get("format")
        == "workbench-worldgen-action-gate-receipt-v1"
        and receipt.get("kind") == "worldgen-action-gate-receipt"
        and receipt.get("schema_version") == 1
        and _sealed(
            receipt,
            kind="worldgen-action-gate-receipt",
            identity_field="id",
        )
        and value
        == _action_projection(receipt)
        and value["action"] == "read-query-visualize"
        and value["disposition"] == "allow"
        and value["context_ref_id"] == context_ref_id
        and value["input_binding_id"] == input_binding_id
        and _content_id(
            value["receipt_id"],
            "worldgen-action-gate-receipt:sha256:",
        )
        and type(value["policy_sha256"]) is str
        and re.fullmatch(r"[0-9a-f]{64}", value["policy_sha256"])
        is not None
    )


def _authority_owners_valid(value: Any) -> bool:
    return bool(
        type(value) is list
        and value
        and all(
            type(row) is dict
            and set(row) == {"category_id", "owner"}
            and type(row["category_id"]) is str
            and re.fullmatch(r"[a-z][a-z0-9.-]*", row["category_id"])
            is not None
            and row["owner"] in {"Atlas", "Crucible"}
            for row in value
        )
        and value
        == sorted(value, key=lambda row: row["category_id"].encode("utf-8"))
        and len({row["category_id"] for row in value}) == len(value)
    )


def _authority_input_projection(
    context_record: Mapping[str, Any], input_binding: Mapping[str, Any]
) -> dict[str, Any]:
    profile_rows = []
    profile_scope = context_record["profile_scope"]
    for profile_kind in ("pack", "platform"):
        profile = profile_scope[profile_kind]
        profile_rows.append(
            {
                "profile_kind": f"{profile_kind}-profile-revision",
                "profile_revision_id": profile[
                    f"{profile_kind}_profile_revision_id"
                ],
                "support_decision_ids": sorted(
                    profile["support_decision_ids"]
                ),
                "support_state": profile["support_state"],
            }
        )
    policy_rows = [
        {
            "input_key": row["input_key"],
            "policy_id": row["policy_id"],
        }
        for row in input_binding["policy_bindings"]
    ]
    return {
        "context_ref_canonical_json": canonical_json_bytes(
            context_record
        ).decode("utf-8"),
        "context_ref_id": context_record["id"],
        "input_binding_canonical_json": canonical_json_bytes(
            input_binding
        ).decode("utf-8"),
        "input_binding_id": input_binding["id"],
        "policy_bindings": sorted(policy_rows, key=canonical_json_bytes),
        "profile_support": profile_rows,
    }


def _context_authority_inputs(
    context: ServiceExecutionContext,
    *,
    context_ref_id: str,
    input_binding_id: str,
) -> dict[str, Any]:
    context_bytes, binding_bytes = context.exact_context_bytes()
    context_record = parse_canonical_json(context_bytes)
    input_binding = parse_canonical_json(binding_bytes)
    _require(
        type(context_record) is dict
        and type(input_binding) is dict
        and canonical_json_bytes(context_record) == context_bytes
        and canonical_json_bytes(input_binding) == binding_bytes
        and context_record.get("id") == context_ref_id
        and input_binding.get("id") == input_binding_id
        and input_binding.get("context_ref_id") == context_ref_id
        and _sealed(
            context_record,
            kind="context-ref",
            identity_field="id",
        )
        and _sealed(
            input_binding,
            kind="input-binding",
            identity_field="id",
        ),
        "World Studio exact context/input authority records differ",
    )
    try:
        projection = _authority_input_projection(
            context_record, input_binding
        )
    except (KeyError, TypeError) as exc:
        raise WorldStudioProvingViewError(
            "World Studio context lacks profile support or policy bindings"
        ) from exc
    _require(
        _authority_inputs_valid(
            projection, context_ref_id, input_binding_id
        ),
        "World Studio authority input projection is invalid",
    )
    return projection


def _authority_inputs_valid(
    value: Any, context_ref_id: str, input_binding_id: str
) -> bool:
    if type(value) is not dict or set(value) != {
        "context_ref_canonical_json",
        "context_ref_id",
        "input_binding_canonical_json",
        "input_binding_id",
        "policy_bindings",
        "profile_support",
    }:
        return False
    try:
        context_record = parse_canonical_json(
            value["context_ref_canonical_json"].encode("utf-8")
        )
        input_binding = parse_canonical_json(
            value["input_binding_canonical_json"].encode("utf-8")
        )
        expected = _authority_input_projection(context_record, input_binding)
    except (AttributeError, KeyError, TypeError, ValueError):
        return False
    return bool(
        type(context_record) is dict
        and type(input_binding) is dict
        and canonical_json_bytes(context_record).decode("utf-8")
        == value["context_ref_canonical_json"]
        and canonical_json_bytes(input_binding).decode("utf-8")
        == value["input_binding_canonical_json"]
        and value == expected
        and value["context_ref_id"] == context_ref_id
        and value["input_binding_id"] == input_binding_id
        and input_binding.get("context_ref_id") == context_ref_id
        and _sealed(
            context_record,
            kind="context-ref",
            identity_field="id",
        )
        and _sealed(
            input_binding,
            kind="input-binding",
            identity_field="id",
        )
        and type(value["profile_support"]) is list
        and len(value["profile_support"]) == 2
        and type(value["policy_bindings"]) is list
        and bool(value["policy_bindings"])
    )


QueryResolver = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]
RecordResolver = Callable[[str], Mapping[str, Any]]


class WorldStudioProvingViewHandler:
    """Render one pinned W01 result without copying category authority."""

    def __init__(
        self,
        *,
        query_resolver: QueryResolver,
        proof_index_resolver: RecordResolver,
        action_gate_resolver: RecordResolver,
    ) -> None:
        _require(
            callable(query_resolver)
            and callable(proof_index_resolver)
            and callable(action_gate_resolver),
            "World Studio proving-view owner ports are unavailable",
        )
        self.query_resolver = query_resolver
        self.proof_index_resolver = proof_index_resolver
        self.action_gate_resolver = action_gate_resolver

    def __call__(
        self,
        context: ServiceExecutionContext,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        request = _detached(arguments, "World Studio proving request")
        _require(
            validate_world_studio_proving_request(request),
            "World Studio proving request is invalid or open",
        )
        _require(
            context.context_ref_id == request["context_ref_id"]
            and context.input_binding_id == request["input_binding_id"],
            "World Studio request differs from its service context",
        )
        proof = _detached(
            self.proof_index_resolver(request["proof_index_id"]),
            "W01 proof index",
        )
        _require(
            type(proof) is dict
            and proof.get("format") == "workbench-worldgen-w01-proof-index-v1"
            and proof.get("kind") == "worldgen-w01-proof-index"
            and proof.get("schema_version") == 1
            and proof.get("id") == request["proof_index_id"]
            and proof.get("graph_set_revision_id")
            == request["graph_set_revision_id"]
            and proof.get("profile_state") == "experimental-draft"
            and _sealed(proof, kind="worldgen-w01-proof-index", identity_field="id"),
            "W01 proof index identity, graph pin, or profile state differs",
        )
        action = _detached(
            self.action_gate_resolver(request["action_gate_receipt_id"]),
            "worldgen action-gate receipt",
        )
        _require(
            type(action) is dict
            and action.get("format")
            == "workbench-worldgen-action-gate-receipt-v1"
            and action.get("kind") == "worldgen-action-gate-receipt"
            and action.get("schema_version") == 1
            and action.get("id") == request["action_gate_receipt_id"]
            and action.get("action") == "read-query-visualize"
            and action.get("disposition") == "allow"
            and action.get("context_ref_id") == request["context_ref_id"]
            and action.get("input_binding_id") == request["input_binding_id"]
            and type(action.get("policy_sha256")) is str
            and re.fullmatch(r"[0-9a-f]{64}", action["policy_sha256"])
            is not None
            and _sealed(
                action,
                kind="worldgen-action-gate-receipt",
                identity_field="id",
            ),
            "worldgen action-gate identity, scope, policy, or disposition differs",
        )
        primary = _validate_query_result(
            self.query_resolver(
                request["graph_set_revision_id"], request["query"]
            ),
            request["graph_set_revision_id"],
            request["query"],
        )
        comparison = None
        if request["operation"] == "diff":
            comparison = _validate_query_result(
                self.query_resolver(
                    request["comparison_graph_set_revision_id"],
                    request["comparison_query"],
                ),
                request["comparison_graph_set_revision_id"],
                request["comparison_query"],
            )
        if comparison is None:
            diff = None
        else:
            primary_rows = {_row_id(row) for row in primary["results"]}
            comparison_rows = {
                _row_id(row) for row in comparison["results"]
            }
            diff = {
                "added_row_ids": sorted(primary_rows - comparison_rows),
                "current_query_result_id": primary["id"],
                "prior_query_result_id": comparison["id"],
                "removed_row_ids": sorted(comparison_rows - primary_rows),
                "shared_row_ids": sorted(primary_rows & comparison_rows),
            }
        presentation = (
            _visualization(primary)
            if request["operation"] == "visualization"
            else None
        )
        body = {
            "action_gate": _action_projection(action),
            "authority_inputs": _context_authority_inputs(
                context,
                context_ref_id=request["context_ref_id"],
                input_binding_id=request["input_binding_id"],
            ),
            "authority_owners": _owner_rows(proof),
            "canonicalizer": "workbench-canonical-json-v2",
            "comparison_graph_set_revision_id": request[
                "comparison_graph_set_revision_id"
            ],
            "comparison_query_result": (
                _query_projection(comparison)
                if comparison is not None
                else None
            ),
            "context_ref_id": request["context_ref_id"],
            "diff": diff,
            "format": "workbench-world-studio-proving-view-result-v1",
            "graph_set_revision_id": request["graph_set_revision_id"],
            "input_binding_id": request["input_binding_id"],
            "kind": "world-studio-proving-view-result",
            "limitations": [
                "exact-w01-bounded-worldgen-envelope",
                "verified-experimental-not-tested-supported",
                "no-construction-or-protected-world-authority",
            ],
            "operation": request["operation"],
            "presentation": presentation,
            "profile_state": proof["profile_state"],
            "proof_index_id": proof["id"],
            "query_result": _query_projection(primary),
            "schema_version": 1,
            "semantic_admission_id": None,
            "support_decision_id": None,
            "support_state": "experimental-unadmitted",
        }
        _preflight_json_value(
            body,
            maximum_bytes=MAX_WORLD_STUDIO_RESULT_BYTES,
            label="World Studio proving-view result",
        )
        result_id = content_id("world-studio-proving-view-result", body)
        result = {**body, "id": result_id}
        _require(
            len(canonical_json_bytes(result)) <= MAX_WORLD_STUDIO_RESULT_BYTES,
            "World Studio proving-view result exceeds the exact byte bound",
        )
        _require(
            validate_world_studio_proving_result(result),
            "World Studio proving-view result failed its owner boundary",
        )
        return result


def validate_world_studio_proving_result(value: Any) -> bool:
    """Validate one closed V01 query, diff, or visualization result."""

    if type(value) is not dict or set(value) != {
        "action_gate",
        "authority_inputs",
        "authority_owners",
        "canonicalizer",
        "comparison_graph_set_revision_id",
        "comparison_query_result",
        "context_ref_id",
        "diff",
        "format",
        "graph_set_revision_id",
        "id",
        "input_binding_id",
        "kind",
        "limitations",
        "operation",
        "presentation",
        "profile_state",
        "proof_index_id",
        "query_result",
        "schema_version",
        "semantic_admission_id",
        "support_decision_id",
        "support_state",
    }:
        return False
    body = dict(value)
    supplied = body.pop("id", None)
    if not (
        value["canonicalizer"] == "workbench-canonical-json-v2"
        and value["format"]
        == "workbench-world-studio-proving-view-result-v1"
        and value["kind"] == "world-studio-proving-view-result"
        and value["schema_version"] == 1
        and value["operation"] in {"query", "diff", "visualization"}
        and value["profile_state"] == "experimental-draft"
        and value["support_state"] == "experimental-unadmitted"
        and value["support_decision_id"] is None
        and value["semantic_admission_id"] is None
        and value["limitations"]
        == [
            "exact-w01-bounded-worldgen-envelope",
            "verified-experimental-not-tested-supported",
            "no-construction-or-protected-world-authority",
        ]
        and supplied == content_id("world-studio-proving-view-result", body)
    ):
        return False
    try:
        primary = _validate_query_projection(
            value["query_result"], value["graph_set_revision_id"]
        )
        if value["operation"] == "diff":
            comparison = _validate_query_projection(
                value["comparison_query_result"],
                value["comparison_graph_set_revision_id"],
            )
            primary_row_ids = {row["row_id"] for row in primary["rows"]}
            comparison_row_ids = {
                row["row_id"] for row in comparison["rows"]
            }
            valid_variant = (
                value["diff"]
                == {
                    "added_row_ids": sorted(
                        primary_row_ids - comparison_row_ids
                    ),
                    "current_query_result_id": primary["query_result_id"],
                    "prior_query_result_id": comparison["query_result_id"],
                    "removed_row_ids": sorted(
                        comparison_row_ids - primary_row_ids
                    ),
                    "shared_row_ids": sorted(
                        primary_row_ids & comparison_row_ids
                    ),
                }
                and value["presentation"] is None
            )
        elif value["operation"] == "visualization":
            valid_variant = (
                value["comparison_graph_set_revision_id"] is None
                and value["comparison_query_result"] is None
                and value["diff"] is None
                and value["presentation"] == _projection_visualization(primary)
            )
        else:
            valid_variant = (
                value["comparison_graph_set_revision_id"] is None
                and value["comparison_query_result"] is None
                and value["diff"] is None
                and value["presentation"] is None
            )
    except (TypeError, ValueError, WorldStudioProvingViewError):
        return False
    return bool(
        valid_variant
        and _content_id(value["context_ref_id"], "context-ref:sha256:")
        and _content_id(value["input_binding_id"], "input-binding:sha256:")
        and _content_id(
            value["proof_index_id"], "worldgen-w01-proof-index:sha256:"
        )
        and type(value["authority_owners"]) is list
        and bool(value["authority_owners"])
        and _authority_owners_valid(value["authority_owners"])
        and _authority_inputs_valid(
            value["authority_inputs"],
            value["context_ref_id"],
            value["input_binding_id"],
        )
        and _action_projection_valid(
            value["action_gate"],
            value["context_ref_id"],
            value["input_binding_id"],
        )
    )


__all__ = [
    "MAX_WORLD_STUDIO_QUERY_ROW_BYTES",
    "MAX_WORLD_STUDIO_QUERY_ROWS",
    "MAX_WORLD_STUDIO_RESULT_BYTES",
    "MAX_WORLD_STUDIO_RESULT_DEPTH",
    "MAX_WORLD_STUDIO_RESULT_NODES",
    "WORLD_STUDIO_PROVING_SCHEMA_ID",
    "WorldStudioProvingViewError",
    "WorldStudioProvingViewHandler",
    "validate_world_studio_proving_request",
    "validate_world_studio_proving_result",
]
