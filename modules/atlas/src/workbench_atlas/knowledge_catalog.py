#!/usr/bin/env python3

"""Shared validation and SQLite support for the packaged knowledge catalog."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

if __package__:
    from . import source_lock
    from .layout import (
        CATALOG_ROOT,
        DATA_ROOT,
        LEXICON_SCHEMA_ROOT,
        OBSERVATION_ROOT,
        WORKBENCH_ROOT,
    )
else:  # Direct module loading.
    import workbench_atlas.source_lock as source_lock
    from workbench_atlas.layout import (
        CATALOG_ROOT,
        DATA_ROOT,
        LEXICON_SCHEMA_ROOT,
        OBSERVATION_ROOT,
        WORKBENCH_ROOT,
    )


REPO_ROOT = WORKBENCH_ROOT
KNOWLEDGE_ROOT = DATA_ROOT
SCHEMA_ROOT = LEXICON_SCHEMA_ROOT
MANIFEST_PATH = CATALOG_ROOT / "manifest.json"
SNAPSHOT_ID = "SNAPSHOT-SUSY-0-1-16-11-9D3AA7AE0"
OBSERVATION_FORMAT = "workbench-atlas-observation-v1"
OBSERVATION_SET_ID = "OBSERVATION-SET-SUPERSYMMETRY-LEGACY-FORGE"
OBSERVATION_RELATIVE_PATH = Path(
    "observations/supersymmetry-legacy-forge.jsonl"
)
OBSERVATION_PATH = OBSERVATION_ROOT / "supersymmetry-legacy-forge.jsonl"

CATALOGS = ("entities.jsonl", "relations.jsonl", "claims.jsonl")
CATALOG_SCHEMAS = {
    "entities.jsonl": "knowledge-record-v1.schema.json",
    "relations.jsonl": "knowledge-relation-v1.schema.json",
    "claims.jsonl": "knowledge-claim-v3.schema.json",
}
STATUSES = {"planned", "researching", "inferred", "verified", "stale"}
PREDICATES = {
    "declares", "owns", "mutates", "removes", "consumes", "produces",
    "executes_on", "registered_during", "configured_by", "serialized_by",
    "rendered_by", "integrates_with", "validated_by", "affects",
    "documented_by", "supersedes",
}
EVIDENCE_BASES = {"pinned-source", "generated-state"}


class CatalogError(ValueError):
    """Raised for malformed or inconsistent canonical knowledge."""


def _strict_json_loads(value: str) -> Any:
    def reject_constant(token: str) -> None:
        raise ValueError(f"non-finite JSON number: {token}")

    def parse_finite_float(token: str) -> float:
        parsed = float(token)
        if not math.isfinite(parsed):
            raise ValueError(f"JSON number exceeds finite float range: {token}")
        return parsed

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key: {key}")
            result[key] = item
        return result

    return json.loads(
        value,
        parse_float=parse_finite_float,
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicate_keys,
    )


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    try:
        return _strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CatalogError(f"cannot read JSON {path}: {exc}") from exc


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError as exc:
        raise CatalogError(f"cannot read catalog {path}: {exc}") from exc
    for number, raw_line in enumerate(lines, 1):
        line = raw_line[:-1] if raw_line.endswith("\n") else raw_line
        if not line.strip():
            raise CatalogError(f"blank JSONL row at {path}:{number}")
        try:
            value = _strict_json_loads(line)
        except ValueError as exc:
            raise CatalogError(f"invalid JSON at {path}:{number}: {exc}") from exc
        if not isinstance(value, dict):
            raise CatalogError(f"catalog row is not an object at {path}:{number}")
        if not isinstance(value.get("id"), str) or not value["id"]:
            raise CatalogError(f"catalog row lacks a non-empty id at {path}:{number}")
        if raw_line.encode("utf-8") != canonical_json(value):
            raise CatalogError(f"catalog row is not canonical at {path}:{number}")
        records.append(value)
    if records != sorted(records, key=lambda item: item["id"]):
        raise CatalogError(f"catalog is not sorted by id: {path}")
    return records


def _matches_schema_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return type(value) is bool
    if expected == "integer":
        return type(value) is int
    if expected == "number":
        return (
            type(value) in {int, float, Decimal}
            and (
                not isinstance(value, Decimal)
                or value.is_finite()
            )
        )
    raise CatalogError(f"unsupported committed JSON Schema type: {expected}")


def _schema_unique_key(value: Any) -> Any:
    """Return a hashable, type-preserving key for JSON-like values."""

    if isinstance(value, dict):
        return (
            "object",
            tuple(
                (key, _schema_unique_key(child))
                for key, child in sorted(value.items())
            ),
        )
    if isinstance(value, list):
        return (
            "array",
            tuple(_schema_unique_key(child) for child in value),
        )
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise CatalogError(
                "non-finite decimal in committed JSON Schema value"
            )
        return ("number", str(value))
    return (type(value).__name__, value)


def _validate_schema_definition(schema: dict[str, Any], path: str) -> None:
    supported = {
        "$id",
        "$schema",
        "additionalProperties",
        "const",
        "enum",
        "items",
        "minItems",
        "minLength",
        "minimum",
        "oneOf",
        "pattern",
        "properties",
        "required",
        "title",
        "type",
        "uniqueItems",
    }
    unsupported = sorted(set(schema) - supported)
    if unsupported:
        raise CatalogError(
            f"unsupported committed JSON Schema keywords at {path}: {unsupported}"
        )
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise CatalogError(f"invalid committed JSON Schema properties at {path}")
    for name, child in properties.items():
        if not isinstance(child, dict):
            raise CatalogError(f"invalid committed JSON Schema property at {path}.{name}")
        _validate_schema_definition(child, f"{path}.{name}")
    items = schema.get("items")
    if items is not None:
        if not isinstance(items, dict):
            raise CatalogError(f"invalid committed JSON Schema items at {path}")
        _validate_schema_definition(items, f"{path}[]")
    one_of = schema.get("oneOf")
    if one_of is not None:
        if not isinstance(one_of, list) or not one_of or not all(
            isinstance(item, dict) for item in one_of
        ):
            raise CatalogError(f"invalid committed JSON Schema oneOf at {path}")
        for index, child in enumerate(one_of):
            _validate_schema_definition(child, f"{path}.oneOf[{index}]")


def _validate_schema_value(value: Any, schema: dict[str, Any], path: str) -> None:
    declared_type = schema.get("type")
    if declared_type is not None:
        expected_types = (
            [declared_type]
            if isinstance(declared_type, str)
            else declared_type
        )
        if not isinstance(expected_types, list) or not all(
            isinstance(item, str) for item in expected_types
        ):
            raise CatalogError(f"invalid committed JSON Schema type at {path}")
        if not any(_matches_schema_type(value, item) for item in expected_types):
            raise CatalogError(
                f"JSON Schema type mismatch at {path}: expected {expected_types}"
            )

    if "enum" in schema and not any(
        type(value) is type(candidate) and value == candidate
        for candidate in schema["enum"]
    ):
        raise CatalogError(f"JSON Schema enum mismatch at {path}")

    if "const" in schema and not (
        type(value) is type(schema["const"]) and value == schema["const"]
    ):
        raise CatalogError(f"JSON Schema const mismatch at {path}")

    one_of = schema.get("oneOf")
    if one_of is not None:
        matches = 0
        for child in one_of:
            try:
                _validate_schema_value(value, child, path)
            except CatalogError:
                continue
            matches += 1
        if matches != 1:
            raise CatalogError(f"JSON Schema oneOf mismatch at {path}: matched {matches}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        if not isinstance(required, list):
            raise CatalogError(f"invalid committed JSON Schema required list at {path}")
        missing = sorted(set(required) - set(value))
        if missing:
            raise CatalogError(f"JSON Schema required fields missing at {path}: {missing}")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise CatalogError(f"invalid committed JSON Schema properties at {path}")
        if schema.get("additionalProperties") is False:
            unexpected = sorted(set(value) - set(properties))
            if unexpected:
                raise CatalogError(
                    f"JSON Schema additional fields at {path}: {unexpected}"
                )
        for key, child in value.items():
            child_schema = properties.get(key)
            if child_schema is not None:
                if not isinstance(child_schema, dict):
                    raise CatalogError(
                        f"invalid committed JSON Schema property at {path}.{key}"
                    )
                _validate_schema_value(child, child_schema, f"{path}.{key}")

    if isinstance(value, list):
        minimum_items = schema.get("minItems")
        if isinstance(minimum_items, int) and len(value) < minimum_items:
            raise CatalogError(f"JSON Schema minItems mismatch at {path}")
        if schema.get("uniqueItems") is True:
            encoded = [_schema_unique_key(item) for item in value]
            if len(encoded) != len(set(encoded)):
                raise CatalogError(f"JSON Schema uniqueItems mismatch at {path}")
        item_schema = schema.get("items")
        if item_schema is not None:
            if not isinstance(item_schema, dict):
                raise CatalogError(f"invalid committed JSON Schema items at {path}")
            for index, item in enumerate(value):
                _validate_schema_value(item, item_schema, f"{path}[{index}]")

    if isinstance(value, str):
        minimum_length = schema.get("minLength")
        if isinstance(minimum_length, int) and len(value) < minimum_length:
            raise CatalogError(f"JSON Schema minLength mismatch at {path}")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            raise CatalogError(f"JSON Schema pattern mismatch at {path}")

    minimum = schema.get("minimum")
    if minimum is not None and type(value) in {int, float} and value < minimum:
        raise CatalogError(f"JSON Schema minimum mismatch at {path}")


def _validate_committed_schemas(
    catalogs: dict[str, list[dict[str, Any]]],
) -> None:
    for catalog_name, schema_name in CATALOG_SCHEMAS.items():
        schema = read_json(SCHEMA_ROOT / schema_name)
        if not isinstance(schema, dict):
            raise CatalogError(f"committed JSON Schema is not an object: {schema_name}")
        _validate_schema_definition(schema, schema_name)
        for index, row in enumerate(catalogs[catalog_name], 1):
            _validate_schema_value(row, schema, f"{catalog_name}:{index}")


def _exact_keys(record: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(record)
    if actual != expected:
        raise CatalogError(
            f"{label} fields differ: missing={sorted(expected-actual)} "
            f"unexpected={sorted(actual-expected)}"
        )


def load_catalogs(root: Path = CATALOG_ROOT) -> dict[str, list[dict[str, Any]]]:
    return {name: read_jsonl(root / name) for name in CATALOGS}


def validate_catalogs(root: Path = CATALOG_ROOT) -> dict[str, int]:
    manifest_path = root / "manifest.json"
    manifest = read_json(manifest_path)
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise CatalogError(f"cannot read JSON {manifest_path}: {exc}") from exc
    if manifest_bytes != canonical_json(manifest):
        raise CatalogError("knowledge manifest is not canonical")
    _exact_keys(
        manifest,
        {
            "catalogs",
            "format",
            "observation_set",
            "pack_revision",
            "snapshot_id",
            "source_lock_id",
            "status",
        },
        "knowledge manifest",
    )
    if manifest.get("format") != "workbench-atlas-catalog-v1":
        raise CatalogError("knowledge manifest has an unsupported format")
    try:
        source_lock_document = source_lock.load_source_lock()
    except source_lock.SourceLockError as exc:
        raise CatalogError(f"active source lock is invalid: {exc}") from exc
    pack = source_lock_document["pack"]
    if (
        manifest.get("source_lock_id") != source_lock_document["lock_id"]
        or manifest.get("snapshot_id") != pack["snapshot_id"]
        or manifest.get("snapshot_id") != SNAPSHOT_ID
        or manifest.get("pack_revision") != pack["revision"]
    ):
        raise CatalogError("knowledge manifest is not cross-bound to the source lock")
    observation_binding = manifest.get("observation_set")
    if not isinstance(observation_binding, dict) or set(observation_binding) != {
        "format", "path", "records", "set_id", "sha256"
    }:
        raise CatalogError("knowledge manifest lacks its observation-set binding")
    if (
        observation_binding.get("format") != OBSERVATION_FORMAT
        or observation_binding.get("set_id") != OBSERVATION_SET_ID
        or observation_binding.get("path") != OBSERVATION_RELATIVE_PATH.as_posix()
    ):
        raise CatalogError("knowledge manifest names the wrong observation set")
    if (
        not OBSERVATION_PATH.is_file()
        or observation_binding.get("sha256") != sha256_path(OBSERVATION_PATH)
    ):
        raise CatalogError("knowledge manifest observation-set hash mismatch")
    catalogs = load_catalogs(root)
    _validate_committed_schemas(catalogs)
    manifest_items = manifest.get("catalogs")
    if not isinstance(manifest_items, list):
        raise CatalogError("knowledge manifest catalogs must be a list")
    by_path = {item.get("path"): item for item in manifest_items if isinstance(item, dict)}
    if set(by_path) != set(CATALOGS):
        raise CatalogError("knowledge manifest catalog set differs from the contract")
    for name in CATALOGS:
        item = by_path[name]
        if set(item) != {"path", "schema", "records", "sha256"}:
            raise CatalogError(f"knowledge manifest catalog binding fields differ: {name}")
        if item["schema"] != CATALOG_SCHEMAS[name]:
            raise CatalogError(f"knowledge manifest schema mapping mismatch: {name}")
        if item.get("sha256") != sha256_path(root / name):
            raise CatalogError(f"knowledge manifest hash mismatch: {name}")
        if item.get("records") != len(catalogs[name]):
            raise CatalogError(f"knowledge manifest count mismatch: {name}")

    entities = catalogs["entities.jsonl"]
    relations = catalogs["relations.jsonl"]
    claims = catalogs["claims.jsonl"]
    all_rows = entities + relations + claims
    ids = [row.get("id") for row in all_rows]
    if any(not isinstance(item, str) or not item for item in ids):
        raise CatalogError("every catalog row requires a non-empty id")
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise CatalogError(f"duplicate canonical ids: {duplicates}")

    entity_ids = {row["id"] for row in entities}
    claim_ids = {row["id"] for row in claims}
    entities_by_id = {row["id"]: row for row in entities}
    locked_sources = {
        item.get("source_id"): item
        for item in source_lock_document["sources"]
        if isinstance(item, dict) and isinstance(item.get("source_id"), str)
    }

    for row in entities:
        _exact_keys(row, {"id", "kind", "name", "snapshot_id", "status", "aliases", "attributes", "provenance"}, row["id"])
        if row["snapshot_id"] != SNAPSHOT_ID or row["status"] not in STATUSES:
            raise CatalogError(f"invalid snapshot/status on {row['id']}")
        if not isinstance(row["aliases"], list) or len(row["aliases"]) != len(set(row["aliases"])):
            raise CatalogError(f"duplicate aliases on {row['id']}")
        if not isinstance(row["attributes"], dict) or not isinstance(row["provenance"], list):
            raise CatalogError(f"invalid attributes/provenance on {row['id']}")
        for citation in row["provenance"]:
            required = {"source_id", "revision", "path", "symbol", "line_start", "line_end", "sha256"}
            _exact_keys(citation, required, f"{row['id']} provenance")
            if citation["source_id"] not in entity_ids:
                raise CatalogError(f"provenance source entity missing for {row['id']}")
            if citation["line_start"] is not None and (
                citation["line_end"] is None or citation["line_end"] < citation["line_start"]
            ):
                raise CatalogError(f"invalid provenance line range on {row['id']}")
            digest = citation["sha256"]
            if digest is not None and (
                not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            ):
                raise CatalogError(f"invalid provenance digest for {row['id']}")

    for source_id, locked in locked_sources.items():
        source = entities_by_id.get(source_id)
        expected_attributes = {
            "revision": locked.get("revision"),
            "tree": locked.get("tree"),
            "scope": locked.get("scope"),
        }
        if (
            source is None
            or source.get("kind") != "source"
            or source.get("aliases") != [locked.get("revision")]
            or source.get("attributes") != expected_attributes
            or any(
                citation.get("source_id") != source_id
                or citation.get("revision") != locked.get("revision")
                for citation in source.get("provenance", [])
            )
        ):
            raise CatalogError(f"source entity differs from source lock: {source_id}")

    snapshot_entities = {row["id"] for row in entities if row["kind"] == "snapshot"}
    if snapshot_entities != {SNAPSHOT_ID}:
        raise CatalogError("catalog must contain exactly the source-locked snapshot")
    if entities_by_id[SNAPSHOT_ID]["snapshot_id"] != SNAPSHOT_ID:
        raise CatalogError("snapshot entity must be self-scoped")

    observations = read_jsonl(OBSERVATION_PATH)
    if observation_binding.get("records") != len(observations):
        raise CatalogError("knowledge manifest observation-set count mismatch")
    for observation in observations:
        _exact_keys(
            observation,
            {
                "format",
                "id",
                "selector",
                "snapshot_id",
                "source_artifact_sha256",
                "value",
            },
            observation["id"],
        )
        digests = observation["source_artifact_sha256"]
        if (
            observation["format"] != OBSERVATION_FORMAT
            or observation["snapshot_id"] != SNAPSHOT_ID
            or re.fullmatch(r"OBS-[A-Z0-9-]+", observation["id"]) is None
            or not isinstance(observation["selector"], dict)
            or not isinstance(observation["value"], dict)
            or not isinstance(digests, list)
            or not digests
            or digests != sorted(set(digests))
            or any(re.fullmatch(r"[0-9a-f]{64}", digest) is None for digest in digests)
        ):
            raise CatalogError(f"invalid observation record: {observation['id']}")

    observation_digest = sha256_path(OBSERVATION_PATH)
    observation_entity = entities_by_id.get(OBSERVATION_SET_ID)
    expected_observation_provenance = [{
        "line_end": None,
        "line_start": None,
        "path": "modules/atlas/data/observations/supersymmetry-legacy-forge.jsonl",
        "revision": observation_digest,
        "sha256": observation_digest,
        "source_id": OBSERVATION_SET_ID,
        "symbol": None,
    }]
    if (
        observation_entity is None
        or observation_entity.get("kind") != "artifact"
        or observation_entity.get("aliases") != [
            "supersymmetry-legacy-forge-observations"
        ]
        or observation_entity.get("attributes") != {
            "format": OBSERVATION_FORMAT,
            "records": len(observations),
            "sha256": observation_digest,
        }
        or observation_entity.get("provenance") != expected_observation_provenance
    ):
        raise CatalogError("catalog observation-set entity differs from its file")

    expected_generated = {row["id"]: row for row in observations}
    catalog_generated = {
        row["id"]: row for row in entities if row["kind"] == "generated_fact"
    }
    if set(catalog_generated) != set(expected_generated):
        raise CatalogError("generated-state entity set differs from the observation set")
    for identifier, fact in expected_generated.items():
        if catalog_generated[identifier]["snapshot_id"] != fact["snapshot_id"]:
            raise CatalogError(
                f"generated-state entity is scoped to the wrong snapshot: {identifier}"
            )
        attributes = catalog_generated[identifier]["attributes"]
        if attributes != {
            "fact": fact,
            "observation_set_id": OBSERVATION_SET_ID,
        }:
            raise CatalogError(f"generated-state entity differs from observation: {identifier}")
        if catalog_generated[identifier]["provenance"] != expected_observation_provenance:
            raise CatalogError(f"generated-state provenance differs: {identifier}")

    for row in claims:
        _exact_keys(row, {"id", "snapshot_id", "subject", "predicate", "value", "value_kind", "status", "source_refs", "evidence", "derivation"}, row["id"])
        if row["snapshot_id"] != SNAPSHOT_ID or row["status"] not in STATUSES:
            raise CatalogError(f"invalid snapshot/status on {row['id']}")
        if row["subject"] not in entity_ids:
            raise CatalogError(f"claim subject missing for {row['id']}")
        if (
            not isinstance(row["source_refs"], list)
            or not row["source_refs"]
            or len(row["source_refs"]) != len(set(row["source_refs"]))
            or not set(row["source_refs"]).issubset(entity_ids)
        ):
            raise CatalogError(f"claim source reference missing for {row['id']}")
        evidence = row["evidence"]
        _exact_keys(
            evidence,
            {"basis", "record_ids", "generated_fact_ids"},
            f"{row['id']} evidence",
        )
        basis = evidence["basis"]
        record_ids = evidence["record_ids"]
        fact_ids = evidence["generated_fact_ids"]
        if basis not in EVIDENCE_BASES:
            raise CatalogError(f"invalid evidence basis on {row['id']}")
        if any(
            not isinstance(values, list) or len(values) != len(set(values))
            for values in (record_ids, fact_ids)
        ):
            raise CatalogError(f"invalid evidence reference list on {row['id']}")
        if not set(record_ids).issubset(entity_ids):
            raise CatalogError(f"claim evidence record missing for {row['id']}")
        if not set(fact_ids).issubset(entity_ids):
            raise CatalogError(f"generated fact entity missing for {row['id']}")
        if any(entities_by_id[identifier]["kind"] != "generated_fact" for identifier in fact_ids):
            raise CatalogError(f"generated fact entity has wrong kind for {row['id']}")
        if basis == "pinned-source" and (not record_ids or fact_ids):
            raise CatalogError(f"pinned-source claim has the wrong evidence on {row['id']}")
        if basis == "generated-state" and (record_ids or not fact_ids):
            raise CatalogError(f"generated-state claim has the wrong evidence on {row['id']}")

    for row in relations:
        _exact_keys(row, {"id", "snapshot_id", "subject", "predicate", "object", "status", "claim_id"}, row["id"])
        if row["snapshot_id"] != SNAPSHOT_ID or row["status"] not in STATUSES:
            raise CatalogError(f"invalid snapshot/status on {row['id']}")
        if row["subject"] not in entity_ids or row["object"] not in entity_ids:
            raise CatalogError(f"relation endpoint missing for {row['id']}")
        if row["predicate"] not in PREDICATES:
            raise CatalogError(f"unknown relation predicate on {row['id']}")
        if row["claim_id"] is not None and row["claim_id"] not in claim_ids:
            raise CatalogError(f"relation claim missing for {row['id']}")

    return {name: len(rows) for name, rows in catalogs.items()}


def build_database(output: Path, root: Path = CATALOG_ROOT) -> dict[str, int]:
    counts = validate_catalogs(root)
    catalogs = load_catalogs(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            CREATE TABLE entities(id TEXT PRIMARY KEY, kind TEXT, name TEXT, status TEXT, json TEXT);
            CREATE TABLE aliases(entity_id TEXT, alias TEXT, PRIMARY KEY(entity_id, alias));
            CREATE TABLE relations(id TEXT PRIMARY KEY, subject TEXT, predicate TEXT, object TEXT, status TEXT, claim_id TEXT, json TEXT);
            CREATE TABLE claims(id TEXT PRIMARY KEY, subject TEXT, predicate TEXT, status TEXT, value_kind TEXT, json TEXT);
            CREATE VIRTUAL TABLE search USING fts5(id UNINDEXED, text, tokenize='unicode61');
            """
        )
        for row in catalogs["entities.jsonl"]:
            encoded = json.dumps(row, sort_keys=True, separators=(",", ":"))
            connection.execute("INSERT INTO entities VALUES(?,?,?,?,?)", (row["id"], row["kind"], row["name"], row["status"], encoded))
            terms = [row["name"], *row["aliases"], *[str(v) for v in row["attributes"].values() if isinstance(v, (str, int, float))]]
            connection.execute("INSERT INTO search VALUES(?,?)", (row["id"], " ".join(terms)))
            connection.executemany("INSERT INTO aliases VALUES(?,?)", ((row["id"], alias) for alias in row["aliases"]))
        for row in catalogs["relations.jsonl"]:
            connection.execute("INSERT INTO relations VALUES(?,?,?,?,?,?,?)", (row["id"], row["subject"], row["predicate"], row["object"], row["status"], row["claim_id"], json.dumps(row, sort_keys=True, separators=(",", ":"))))
        for row in catalogs["claims.jsonl"]:
            connection.execute("INSERT INTO claims VALUES(?,?,?,?,?,?)", (row["id"], row["subject"], row["predicate"], row["status"], row["value_kind"], json.dumps(row, sort_keys=True, separators=(",", ":"))))
        connection.commit()
        connection.execute("VACUUM")
        connection.close()
        temporary.replace(output)
    finally:
        if connection:
            try:
                connection.close()
            except sqlite3.Error:
                pass
        temporary.unlink(missing_ok=True)
    return counts


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=lambda item: item["id"])
    path.write_bytes(b"".join(canonical_json(row) for row in ordered))
