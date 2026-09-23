"""Pure contracts for retained check snapshots and their read requests.

Validation establishes shape, identity and declared coverage, not custody,
payload completeness, native validity or permission to read/delete a resource.
Core publication and access must establish those separately.
"""

from copy import deepcopy
from functools import lru_cache
from importlib.resources import files
import json

from jsonschema import Draft202012Validator

from .check_storage import seal


class SnapshotContractError(ValueError):
    """A snapshot or read request contradicts its bound contract."""


@lru_cache(maxsize=3)
def _validator(name):
    schema = json.loads(files("workbench_core").joinpath("schemas", name + ".schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _shape(value, name):
    error = next(_validator(name).iter_errors(value), None)
    if error is not None:
        path = "/".join(map(str, error.absolute_path)) or "/"
        raise SnapshotContractError(f"{name} differs at {path}: {error.message}")


def scope_identity(scope):
    """Identity of the independently selected profile/producer section contract."""
    if (not isinstance(scope, dict) or set(scope) != {"name", "sections"}
            or not isinstance(scope["name"], str) or not scope["name"]
            or not isinstance(scope["sections"], dict) or not scope["sections"]
            or any(not isinstance(key, str) or not key or type(required) is not bool
                   for key, required in scope["sections"].items())):
        raise SnapshotContractError("invalid selected section scope")
    return seal("check-snapshot-scope", scope)["id"]


def validate_envelope(value):
    """Verify retained V1 metadata without claiming a supported profile scope."""
    _shape(value, "workbench-check-snapshot-v1")
    body = {key: item for key, item in value.items() if key != "id"}
    if seal("check-snapshot", body) != value:
        raise SnapshotContractError("snapshot content identity differs")
    sections = value["sections"]
    identifiers = [item["id"] for item in sections]
    if identifiers != sorted(set(identifiers)):
        raise SnapshotContractError("snapshot sections must be unique and ordered")
    roles = [item["role"] for item in value["retained_inputs"]]
    if roles != sorted(set(roles)):
        raise SnapshotContractError("retained input roles must be unique and ordered")
    by_id = {item["id"]: item for item in sections}
    for section in sections:
        dependencies = section["dependencies"]
        if dependencies != sorted(set(dependencies)) or any(key not in by_id for key in dependencies):
            raise SnapshotContractError("section dependencies are unordered or unresolved")
        state = section["state"]
        if state == "observed":
            if section["content"] is None or section["count"] is None or section["reason"] is not None:
                raise SnapshotContractError("observed section requires content and count")
            if any(by_id[key]["state"] != "observed" for key in dependencies):
                raise SnapshotContractError("observed section depends on unavailable evidence")
        elif section["reason"] is None:
            raise SnapshotContractError("non-observed section requires a reason")
        if state == "not-applicable" and (
                section["required"] or section["content"] is not None
                or section["count"] is not None or dependencies):
            raise SnapshotContractError("inapplicable section cannot contain or require evidence")
        if section["content"] is None and section["count"] is not None:
            raise SnapshotContractError("missing payload cannot establish a record count")
    complete = all(item["state"] == "observed" for item in sections if item["required"])
    if value["coverage"] != ("complete" if complete else "incomplete"):
        raise SnapshotContractError("snapshot coverage contradicts required sections")
    return value


def validate_snapshot(value, scope):
    """Validate against a caller-verified scope, never a producer-inferred list."""
    # Check obligations before envelope relationships so an omitted required
    # section is reported as a scope violation, not a dangling dependency.
    _shape(value, "workbench-check-snapshot-v1")
    if value["scope_id"] != scope_identity(scope):
        raise SnapshotContractError("snapshot differs from selected scope")
    if {item['id'] for item in value['sections']} != set(scope['sections']):
        raise SnapshotContractError("snapshot sections differ from selected scope")
    if any(item['required'] != scope['sections'][item['id']] for item in value['sections']):
        raise SnapshotContractError("section requirement differs from selected scope")
    return validate_envelope(value)


def seal_snapshot(body, scope):
    """Seal declared metadata; does not publish or certify payloads on disk."""
    if "id" in body:
        raise SnapshotContractError("snapshot body already contains an identity")
    return validate_snapshot(seal("check-snapshot", deepcopy(body)), scope)


def reader_coverage(value, supported_schemas):
    """Report reader limitations separately from retained native/capture outcomes.

    The caller must first validate_snapshot with its independently selected scope.
    Unknown optional schemas remain visible; affected required dependencies block
    complete interpretation even when their parent schema is understood.
    """
    unsupported = {item["id"] for item in value["sections"]
                   if item["state"] != "not-applicable" and item["schema"] not in supported_schemas}
    affected = set(unsupported)
    while True:
        expanded = affected | {item["id"] for item in value["sections"]
                               if affected.intersection(item["dependencies"])}
        if expanded == affected:
            break
        affected = expanded
    required = {item["id"] for item in value["sections"] if item["required"]}
    return {"unsupported_sections": sorted(unsupported), "affected_sections": sorted(affected),
            "required_interpretation_complete": value["coverage"] == "complete" and not bool(required & affected)}


def query_identity(query):
    """Bind pagination to the same snapshot, view generation and query shape."""
    return seal("check-snapshot-query", {key: item for key, item in query.items() if key != "cursor"})["id"]


def read_identity(query):
    """Distinguish individual page reads within a stable query/view."""
    return seal("check-snapshot-read", query)["id"]


def validate_query(query, snapshot):
    """Validate query addressing, not workspace authorization or stored content.

    A blob request addresses bytes of one section record by key and expected
    digest; it never grants arbitrary filesystem or content-store access.
    """
    _shape(query, "workbench-check-snapshot-query-v1")
    if query["snapshot_id"] != snapshot["id"]:
        raise SnapshotContractError("query belongs to another snapshot")
    operation = query["operation"]
    section, key, blob = query["section_id"], query["record_key"], query["blob_sha256"]
    if operation in {"summary", "sections"}:
        valid = section is None and key is None and blob is None
    elif operation == "records":
        valid = section is not None and key is None and blob is None
    elif operation == "record":
        valid = section is not None and key is not None and blob is None
    else:
        valid = section is not None and key is not None and blob is not None
    if not valid:
        raise SnapshotContractError("query arguments differ from operation")
    if section is not None and section not in {item["id"] for item in snapshot["sections"]}:
        raise SnapshotContractError("query section is not in snapshot")
    cursor = query["cursor"]
    if cursor is not None:
        if operation in {"summary", "record"}:
            raise SnapshotContractError("operation does not accept pagination")
        if cursor["snapshot_id"] != snapshot["id"] or cursor["query_id"] != query_identity(query):
            raise SnapshotContractError("cursor belongs to another snapshot, view or query")
    return query


def validate_response(response, query, snapshot):
    """Reject stale replies and contradictory stream-completion envelopes.

    Payload schemas, count/byte ranges and content integrity are established by
    the operation's producer/reader; this function does not execute a query.
    """
    validate_query(query, snapshot)
    _shape(response, "workbench-check-snapshot-response-v1")
    identity = query_identity(query)
    if (response["snapshot_id"] != snapshot["id"] or response["query_id"] != identity
            or response["request_id"] != read_identity(query)
            or response["view_id"] != query["view_id"]):
        raise SnapshotContractError("response belongs to another snapshot, view or query")
    cursor = response["next_cursor"]
    if response["state"] != "ready":
        if (response["complete"] or response["payload"] is not None or cursor is not None
                or not response["reason"]):
            raise SnapshotContractError("unavailable response cannot claim complete or empty data")
        return response
    if response["payload"] is None or response["reason"] is not None:
        raise SnapshotContractError("ready response requires a payload")
    if response["complete"] != (cursor is None):
        raise SnapshotContractError("response completion differs from cursor")
    if cursor is not None:
        if query["operation"] in {"summary", "record"}:
            raise SnapshotContractError("operation does not accept pagination")
        previous = 0 if query["cursor"] is None else query["cursor"]["offset"]
        if (cursor["snapshot_id"] != snapshot["id"] or cursor["query_id"] != identity
                or cursor["offset"] <= previous):
            raise SnapshotContractError("response cursor differs or does not advance")
    return response
