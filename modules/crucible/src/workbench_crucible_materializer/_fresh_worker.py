"""Isolated standard-library worker for closed bounded recipe capabilities.

The parent gives this process one canonical value request over stdin.  The
worker has no store path, resolver, lease, authority callback, application
object, or dynamic implementation loader. Dispatch is solely by one of the
closed capability IDs below.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import NoReturn


PROTOCOL = "workbench-crucible-recipe-worker-v1"
_HANDLER_DOMAIN = b"workbench-crucible-builtin-recipe-handler-v1\x00"
_SYNTHETIC_CAPABILITY_ID = (
    "capability:sha256:"
    "c4f9e22ccf08d3aaac37805f422b396b79d8a9ee6c0734505a8ee9487240164d"
)
_SYNTHETIC_FULL_BUILD_CAPABILITY_ID = (
    "capability:sha256:"
    "d63a61b6d9be3d58e7fdc6d96114bf8cfc5391ef943e0d865b1cb9fb2eadccf5"
)
_OCCURRENCE_CAPABILITY_ID = (
    "capability:sha256:"
    "aaf9e587633a3b20bdc9eb34dc8ef90b3d998ca0383a66e4af9bf3f588042e29"
)
_SEMANTIC_CAPABILITY_ID = (
    "capability:sha256:"
    "b40ac548da7ba0372c71ab1282d7cd400c59f9262b5ad8f79b0610222de1689d"
)
_WORLDGEN_CAPTURE_HEALTH_CAPABILITY_ID = (
    "capability:sha256:"
    "36d09c8c668450b22dc229529d4c8bcdfd6017fe8795cdeaa53271d9f351f271"
)
_WORLDGEN_OCCURRENCE_CAPABILITY_ID = (
    "capability:sha256:"
    "d7c8bdfed0753400b2cb728a1196eb9f7e3926b5d561a0cf88cbaf8adf07fd1d"
)
_WORLDGEN_STABILITY_CAPABILITY_ID = (
    "capability:sha256:"
    "0000d61f88b9e552ee8579a917f28d1acfa1dcef00c95eeb6bf842d99fc9a1e9"
)
_WORLDGEN_GENERATIVE_CAPABILITY_ID = (
    "capability:sha256:"
    "49a20265c2ed3cf4c75dc6d4ff3342fba3d841001bff1a0047b50314e337773c"
)
_WORLDGEN_REALIZED_CAPABILITY_ID = (
    "capability:sha256:"
    "f5ac746b7779eaa31a59c175b26d91cea49509789e1c087824cb6e6e9572d27e"
)
_OCCURRENCE_VALUE_KIND = "workbench-profile-occurrence-graph-v1"
_SEMANTIC_VALUE_KIND = "workbench-profile-semantic-graph-v1"
_BUNDLE_VALUE_KIND = "workbench-profile-graph-bundle-v1"
_WORLDGEN_CAPTURE_HEALTH_VALUE_KIND = "workbench-worldgen-capture-health-graph-v1"
_WORLDGEN_OCCURRENCE_VALUE_KIND = "workbench-worldgen-occurrence-graph-v1"
_WORLDGEN_STABILITY_VALUE_KIND = "workbench-worldgen-stability-graph-v1"
_WORLDGEN_GENERATIVE_VALUE_KIND = "workbench-worldgen-generative-graph-v1"
_WORLDGEN_REALIZED_VALUE_KIND = "workbench-worldgen-realized-graph-v1"
_WORLDGEN_BUNDLE_VALUE_KIND = "workbench-worldgen-graph-bundle-v1"
_VALUE_KIND_BY_CAPABILITY = {
    _OCCURRENCE_CAPABILITY_ID: _OCCURRENCE_VALUE_KIND,
    _SEMANTIC_CAPABILITY_ID: _SEMANTIC_VALUE_KIND,
    _WORLDGEN_CAPTURE_HEALTH_CAPABILITY_ID: _WORLDGEN_CAPTURE_HEALTH_VALUE_KIND,
    _WORLDGEN_OCCURRENCE_CAPABILITY_ID: _WORLDGEN_OCCURRENCE_VALUE_KIND,
    _WORLDGEN_STABILITY_CAPABILITY_ID: _WORLDGEN_STABILITY_VALUE_KIND,
    _WORLDGEN_GENERATIVE_CAPABILITY_ID: _WORLDGEN_GENERATIVE_VALUE_KIND,
    _WORLDGEN_REALIZED_CAPABILITY_ID: _WORLDGEN_REALIZED_VALUE_KIND,
}
_WORLDGEN_BRANCH_BY_CAPABILITY = {
    _WORLDGEN_CAPTURE_HEALTH_CAPABILITY_ID: "capture_health",
    _WORLDGEN_OCCURRENCE_CAPABILITY_ID: "occurrence",
    _WORLDGEN_STABILITY_CAPABILITY_ID: "stability",
    _WORLDGEN_GENERATIVE_CAPABILITY_ID: "generative",
    _WORLDGEN_REALIZED_CAPABILITY_ID: "realized",
}

_MAX_REQUEST_BYTES = 1024 * 1024
_MAX_PAYLOAD_BYTES = 64 * 1024
_MAX_RESPONSE_BYTES = 64 * 1024
_MAX_SOURCE_ARTIFACTS = 8
_MAX_SOURCE_ARTIFACT_BYTES = 32 * 1024
_MAX_TOTAL_SOURCE_BYTES = 48 * 1024
_MAX_SOURCE_ROWS = 256
_MAX_SOURCE_ROW_BYTES = 4096
_MAX_GRAPH_RECORDS = 32
_MAX_SOURCE_ROW_IDS_PER_RECORD = 32
_MAX_VALUE_BYTES = 4096
_MAX_TEXT_BYTES = 512
_MAX_CONTAINER_DEPTH = 64
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
_NAME = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_SEMANTIC = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _fail() -> NoReturn:
    raise ValueError("invalid worker request")


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def _parse_integer(raw: str) -> int:
    value = int(raw, 10)
    if value < _INT64_MIN or value > _INT64_MAX:
        _fail()
    return value


def _reject_number(_: str) -> NoReturn:
    _fail()


def _encoded_string(value: str) -> bytes:
    pieces: list[str] = ['"']
    for character in value:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            _fail()
        if character == '"':
            pieces.append(r'\"')
        elif character == "\\":
            pieces.append(r"\\")
        elif codepoint <= 0x1F:
            pieces.append(f"\\u00{codepoint:02x}")
        else:
            pieces.append(character)
    pieces.append('"')
    return "".join(pieces).encode("utf-8", errors="strict")


def _canonical(value: object, depth: int = 0) -> bytes:
    if value is None:
        return b"null"
    if value is True:
        return b"true"
    if value is False:
        return b"false"
    if type(value) is int:
        if value < _INT64_MIN or value > _INT64_MAX:
            _fail()
        return str(value).encode("ascii")
    if type(value) is str:
        return _encoded_string(value)
    if depth >= _MAX_CONTAINER_DEPTH:
        _fail()
    if type(value) is list:
        return b"[" + b",".join(_canonical(item, depth + 1) for item in value) + b"]"
    if type(value) is dict:
        entries: list[tuple[bytes, object]] = []
        for key, item in value.items():
            if type(key) is not str:
                _fail()
            entries.append((key.encode("utf-8", errors="strict"), item))
        entries.sort(key=lambda entry: entry[0])
        return b"{" + b",".join(
            _encoded_string(key.decode("utf-8"))
            + b":"
            + _canonical(item, depth + 1)
            for key, item in entries
        ) + b"}"
    _fail()


def _parse_canonical(raw: bytes) -> object:
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_float=_reject_number,
            parse_int=_parse_integer,
            parse_constant=_reject_number,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        _fail()
    if _canonical(value) != raw:
        _fail()
    return value


def _installed_handler_id() -> str:
    return "handler:sha256:" + hashlib.sha256(
        _HANDLER_DOMAIN + Path(__file__).read_bytes()
    ).hexdigest()


def _text(value: object, *, maximum: int = _MAX_TEXT_BYTES) -> str:
    if type(value) is not str or "\x00" in value or "\r" in value or "\n" in value:
        _fail()
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError:
        _fail()
    if not 1 <= len(encoded) <= maximum:
        _fail()
    return value


def _semantic(value: object) -> str:
    result = _text(value, maximum=256)
    if _SEMANTIC.fullmatch(result) is None:
        _fail()
    return result


def _strictly_ordered(values: list[str]) -> bool:
    encoded = [value.encode("utf-8") for value in values]
    return all(left < right for left, right in zip(encoded, encoded[1:]))


def _source_row_ids(value: object, available: set[str]) -> list[str]:
    if type(value) is not list or not 1 <= len(value) <= _MAX_SOURCE_ROW_IDS_PER_RECORD:
        _fail()
    result = [_text(item) for item in value]
    if not _strictly_ordered(result) or len(result) != len(set(result)):
        _fail()
    if any(item not in available for item in result):
        _fail()
    return result


def _source_artifacts(value: object) -> set[str]:
    if type(value) is not list or not 1 <= len(value) <= _MAX_SOURCE_ARTIFACTS:
        _fail()
    names: list[str] = []
    row_ids: set[str] = set()
    total_bytes = 0
    total_rows = 0
    for artifact in value:
        if type(artifact) is not dict or set(artifact) != {
            "bytes_base64",
            "name",
            "sha256",
        }:
            _fail()
        name = _text(artifact["name"], maximum=256)
        digest = artifact["sha256"]
        encoded = artifact["bytes_base64"]
        if (
            type(digest) is not str
            or _SHA256.fullmatch(digest) is None
            or type(encoded) is not str
        ):
            _fail()
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, UnicodeError):
            _fail()
        if base64.b64encode(raw).decode("ascii") != encoded:
            _fail()
        if (
            not 1 <= len(raw) <= _MAX_SOURCE_ARTIFACT_BYTES
            or hashlib.sha256(raw).hexdigest() != digest
            or not raw.endswith(b"\n")
        ):
            _fail()
        total_bytes += len(raw)
        if total_bytes > _MAX_TOTAL_SOURCE_BYTES:
            _fail()
        rows = raw[:-1].split(b"\n")
        if not rows or any(not row or len(row) > _MAX_SOURCE_ROW_BYTES for row in rows):
            _fail()
        for raw_row in rows:
            row = _parse_canonical(raw_row)
            if (
                type(row) is not dict
                or ("id" in row) == ("row_id" in row)
            ):
                _fail()
            row_id = _text(row["id"] if "id" in row else row["row_id"])
            if row_id in row_ids:
                _fail()
            row_ids.add(row_id)
        total_rows += len(rows)
        if total_rows > _MAX_SOURCE_ROWS:
            _fail()
        names.append(name)
    if not _strictly_ordered(names) or len(names) != len(set(names)):
        _fail()
    return row_ids


def _graph_branch(value: object, available_rows: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != {
        "emit_evidence_links",
        "partition_key",
        "properties",
        "relations",
        "subjects",
    }:
        _fail()
    partition_key = _semantic(value["partition_key"])
    emit_evidence_links = value["emit_evidence_links"]
    subjects_value = value["subjects"]
    relations_value = value["relations"]
    properties_value = value["properties"]
    if (
        type(emit_evidence_links) is not bool
        or type(subjects_value) is not list
        or type(relations_value) is not list
        or type(properties_value) is not list
        or not subjects_value
    ):
        _fail()
    base_count = len(subjects_value) + len(relations_value) + len(properties_value)
    if base_count * (2 if emit_evidence_links else 1) > _MAX_GRAPH_RECORDS:
        _fail()

    subjects: list[dict[str, object]] = []
    subject_keys: set[str] = set()
    subject_order: list[str] = []
    logical_keys: set[str] = set()
    identities: set[bytes] = set()
    for item in subjects_value:
        if type(item) is not dict or set(item) != {
            "identity",
            "logical_key",
            "source_row_ids",
            "subject_key",
        }:
            _fail()
        subject_key = _text(item["subject_key"])
        logical_key = _text(item["logical_key"])
        identity_bytes = _canonical(item["identity"])
        if (
            len(identity_bytes) > _MAX_VALUE_BYTES
            or subject_key in subject_keys
            or logical_key in logical_keys
            or identity_bytes in identities
        ):
            _fail()
        subject_keys.add(subject_key)
        subject_order.append(subject_key)
        logical_keys.add(logical_key)
        identities.add(identity_bytes)
        subjects.append(
            {
                "identity": item["identity"],
                "logical_key": logical_key,
                "source_row_ids": _source_row_ids(
                    item["source_row_ids"], available_rows
                ),
                "subject_key": subject_key,
            }
        )
    if not _strictly_ordered(subject_order):
        _fail()

    relations: list[dict[str, object]] = []
    relation_order: list[str] = []
    for item in relations_value:
        if type(item) is not dict or set(item) != {
            "direction",
            "logical_key",
            "predicate_id",
            "source_row_ids",
            "source_subject_key",
            "target_subject_key",
        }:
            _fail()
        logical_key = _text(item["logical_key"])
        source_key = _text(item["source_subject_key"])
        target_key = _text(item["target_subject_key"])
        predicate = _semantic(item["predicate_id"])
        direction = item["direction"]
        if (
            logical_key in logical_keys
            or source_key not in subject_keys
            or target_key not in subject_keys
            or direction not in {"directed", "symmetric"}
        ):
            _fail()
        logical_keys.add(logical_key)
        relation_order.append(logical_key)
        relations.append(
            {
                "direction": direction,
                "logical_key": logical_key,
                "predicate_id": predicate,
                "source_row_ids": _source_row_ids(
                    item["source_row_ids"], available_rows
                ),
                "source_subject_key": source_key,
                "target_subject_key": target_key,
            }
        )
    if not _strictly_ordered(relation_order):
        _fail()

    properties: list[dict[str, object]] = []
    property_order: list[str] = []
    for item in properties_value:
        if type(item) is not dict or set(item) != {
            "logical_key",
            "property_key",
            "property_state",
            "source_row_ids",
            "subject_key",
            "value",
        }:
            _fail()
        logical_key = _text(item["logical_key"])
        subject_key = _text(item["subject_key"])
        property_key = _semantic(item["property_key"])
        property_state = _semantic(item["property_state"])
        value_bytes = _canonical(
            {"property_state": property_state, "value": item["value"]}
        )
        if (
            logical_key in logical_keys
            or subject_key not in subject_keys
            or len(value_bytes) > _MAX_VALUE_BYTES
        ):
            _fail()
        logical_keys.add(logical_key)
        property_order.append(logical_key)
        properties.append(
            {
                "logical_key": logical_key,
                "property_key": property_key,
                "property_state": property_state,
                "source_row_ids": _source_row_ids(
                    item["source_row_ids"], available_rows
                ),
                "subject_key": subject_key,
                "value": item["value"],
            }
        )
    if not _strictly_ordered(property_order):
        _fail()
    return {
        "emit_evidence_links": emit_evidence_links,
        "partition_key": partition_key,
        "properties": properties,
        "relations": relations,
        "subjects": subjects,
    }


def _declarative_graph(payload: bytes, capability_id: str) -> dict[str, object]:
    value = _parse_canonical(payload)
    if type(value) is not dict:
        _fail()
    expected_kind = _VALUE_KIND_BY_CAPABILITY.get(capability_id)
    if expected_kind is None:
        _fail()
    if value.get("value_kind") == _WORLDGEN_BUNDLE_VALUE_KIND:
        if set(value) != {
            "branches",
            "source_artifacts",
            "value_kind",
        }:
            _fail()
        branches = value["branches"]
        if type(branches) is not dict or set(branches) != {
            "capture_health",
            "generative",
            "occurrence",
            "realized",
            "stability",
        }:
            _fail()
        branch_key = _WORLDGEN_BRANCH_BY_CAPABILITY.get(capability_id)
        if branch_key is None:
            _fail()
        available_rows = _source_artifacts(value["source_artifacts"])
        selected = _graph_branch(branches[branch_key], available_rows)
    elif value.get("value_kind") == _BUNDLE_VALUE_KIND:
        if set(value) != {
            "occurrence",
            "semantic",
            "source_artifacts",
            "value_kind",
        }:
            _fail()
        available_rows = _source_artifacts(value["source_artifacts"])
        occurrence = _graph_branch(value["occurrence"], available_rows)
        semantic = _graph_branch(value["semantic"], available_rows)
        selected = (
            occurrence
            if capability_id == _OCCURRENCE_CAPABILITY_ID
            else semantic
        )
    else:
        if set(value) != {
            "emit_evidence_links",
            "partition_key",
            "properties",
            "relations",
            "source_artifacts",
            "subjects",
            "value_kind",
        } or value.get("value_kind") != expected_kind:
            _fail()
        available_rows = _source_artifacts(value["source_artifacts"])
        selected = _graph_branch(
            {
                key: item
                for key, item in value.items()
                if key not in {"source_artifacts", "value_kind"}
            },
            available_rows,
        )
    return {"value_kind": expected_kind, **selected}


def _synthetic_before(payload: bytes) -> dict[str, str]:
    value = _parse_canonical(payload)
    if type(value) is not dict or set(value) != {
        "observation",
        "ordinal",
        "value_kind",
    }:
        _fail()
    if (
        value["value_kind"] != "evidence-payload"
        or type(value["observation"]) is not str
        or type(value["ordinal"]) is not int
        or value["ordinal"] != 0
    ):
        _fail()
    match = re.fullmatch(
        r"([a-z][a-z0-9-]{0,63})-before-([a-z][a-z0-9-]{0,63})",
        value["observation"],
    )
    if match is None or match.group(1) == match.group(2):
        _fail()
    return {
        "direction": "directed",
        "partition_key": "synthetic.main",
        "predicate_id": "synthetic.before",
        "source_subject_name": match.group(1),
        "subject_namespace": "workbench.synthetic",
        "target_subject_name": match.group(2),
    }


def _synthetic_full_build_before(payload: bytes) -> dict[str, str]:
    value = _parse_canonical(payload)
    if type(value) is not dict or set(value) != {
        "observation",
        "ordinal",
        "partition_key",
        "value_kind",
    }:
        _fail()
    if (
        value["value_kind"] != "full-build-evidence-payload"
        or type(value["observation"]) is not str
        or type(value["ordinal"]) is not int
        or value["ordinal"] < 0
        or type(value["partition_key"]) is not str
        or _SEMANTIC.fullmatch(value["partition_key"]) is None
    ):
        _fail()
    match = re.fullmatch(
        r"([a-z][a-z0-9-]{0,63})-before-([a-z][a-z0-9-]{0,63})",
        value["observation"],
    )
    if match is None or match.group(1) == match.group(2):
        _fail()
    return {
        "direction": "directed",
        "partition_key": value["partition_key"],
        "predicate_id": "synthetic.before",
        "source_subject_name": match.group(1),
        "subject_namespace": "workbench.synthetic",
        "target_subject_name": match.group(2),
    }


def _occurrence_graph(payload: bytes) -> dict[str, object]:
    return _declarative_graph(payload, _OCCURRENCE_CAPABILITY_ID)


def _semantic_graph(payload: bytes) -> dict[str, object]:
    return _declarative_graph(payload, _SEMANTIC_CAPABILITY_ID)


def _worldgen_capture_health_graph(payload: bytes) -> dict[str, object]:
    return _declarative_graph(payload, _WORLDGEN_CAPTURE_HEALTH_CAPABILITY_ID)


def _worldgen_occurrence_graph(payload: bytes) -> dict[str, object]:
    return _declarative_graph(payload, _WORLDGEN_OCCURRENCE_CAPABILITY_ID)


def _worldgen_stability_graph(payload: bytes) -> dict[str, object]:
    return _declarative_graph(payload, _WORLDGEN_STABILITY_CAPABILITY_ID)


def _worldgen_generative_graph(payload: bytes) -> dict[str, object]:
    return _declarative_graph(payload, _WORLDGEN_GENERATIVE_CAPABILITY_ID)


def _worldgen_realized_graph(payload: bytes) -> dict[str, object]:
    return _declarative_graph(payload, _WORLDGEN_REALIZED_CAPABILITY_ID)


_DISPATCH = {
    _SYNTHETIC_CAPABILITY_ID: _synthetic_before,
    _SYNTHETIC_FULL_BUILD_CAPABILITY_ID: _synthetic_full_build_before,
    _OCCURRENCE_CAPABILITY_ID: _occurrence_graph,
    _SEMANTIC_CAPABILITY_ID: _semantic_graph,
    _WORLDGEN_CAPTURE_HEALTH_CAPABILITY_ID: _worldgen_capture_health_graph,
    _WORLDGEN_OCCURRENCE_CAPABILITY_ID: _worldgen_occurrence_graph,
    _WORLDGEN_STABILITY_CAPABILITY_ID: _worldgen_stability_graph,
    _WORLDGEN_GENERATIVE_CAPABILITY_ID: _worldgen_generative_graph,
    _WORLDGEN_REALIZED_CAPABILITY_ID: _worldgen_realized_graph,
}


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(_MAX_REQUEST_BYTES + 1)
        if len(raw) > _MAX_REQUEST_BYTES:
            _fail()
        request = _parse_canonical(raw)
        if type(request) is not dict or set(request) != {
            "capability_id",
            "derivation_step_id",
            "evidence_kind",
            "execution_binding_id",
            "handler_id",
            "implementation_id",
            "output_contract_key",
            "payload_base64",
            "payload_sha256",
            "protocol",
            "recipe_id",
            "request_nonce",
        }:
            _fail()
        if (
            any(type(item) is not str for item in request.values())
            or request["protocol"] != PROTOCOL
            or request["handler_id"] != _installed_handler_id()
            or request["capability_id"] not in _DISPATCH
            or _SEMANTIC.fullmatch(request["evidence_kind"]) is None
            or _SEMANTIC.fullmatch(request["output_contract_key"]) is None
            or re.fullmatch(
                r"derivation-step:sha256:[0-9a-f]{64}",
                request["derivation_step_id"],
            )
            is None
        ):
            _fail()
        try:
            payload = base64.b64decode(request["payload_base64"], validate=True)
        except (ValueError, UnicodeError):
            _fail()
        if (
            base64.b64encode(payload).decode("ascii")
            != request["payload_base64"]
            or len(payload) > _MAX_PAYLOAD_BYTES
            or hashlib.sha256(payload).hexdigest()
            != request["payload_sha256"]
        ):
            _fail()
        response = {
            "execution_binding_id": request["execution_binding_id"],
            "output": _DISPATCH[request["capability_id"]](payload),
            "protocol": PROTOCOL,
            "request_nonce": request["request_nonce"],
        }
        encoded_response = _canonical(response)
        if len(encoded_response) > _MAX_RESPONSE_BYTES:
            _fail()
        sys.stdout.buffer.write(encoded_response)
        return 0
    except Exception:
        # Never expose worker internals or untrusted values to the parent.
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
