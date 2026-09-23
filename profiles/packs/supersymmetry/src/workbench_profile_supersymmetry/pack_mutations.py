#!/usr/bin/env python3

"""Extract ordered, static pack declarations and mutation candidates for Atlas."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

from .source_span_index import (
    AtlasSourceIndexError,
    SOURCE_LOCK_PATH,
    SourceResolver,
    _char_byte_offsets,
    _groovy_lifecycle,
    json_config_entries,
    validate_index,
)
from workbench_atlas.atlas_causal_provenance_contract import (
    canonical_json,
    canonical_sha256,
)
from workbench_atlas.groovy_parser import (
    analyze_bytes,
    bracket_pairs,
    expression_value,
    member_chain,
    split_arguments,
)
from workbench_atlas.knowledge_catalog import (
    _validate_schema_definition,
    _validate_schema_value,
    read_json,
    sha256_path,
)
from workbench_atlas.layout import (
    DATA_ROOT,
    SCHEMA_ROOT,
    atlas_knowledge_root,
    atlas_source_root,
)


CORPUS_ROOT = DATA_ROOT
POLICY_PATH = DATA_ROOT / "atlas-pack-mutation-policy-v1.json"
SCHEMA_PATH = SCHEMA_ROOT / "atlas-pack-mutation-extraction-v1.schema.json"
def default_source_index_path() -> Path:
    return (
        atlas_knowledge_root()
        / "SNAPSHOT-SUSY-0-1-16-11-9D3AA7AE0"
        / "atlas-source-spans.json"
    )


def default_output_path() -> Path:
    return (
        atlas_knowledge_root()
        / "SNAPSHOT-SUSY-0-1-16-11-9D3AA7AE0"
        / "atlas-pack-mutations.json"
    )

FORMAT = "susy-atlas-pack-mutation-extraction-v1"
EXTRACTION_PREFIX = "atlas-pack-mutation-extraction:sha256:"
STAGE_PREFIX = "atlas-pack-stage-selection:sha256:"
DECLARATION_PREFIX = "atlas-pack-declaration:sha256:"
OPERATION_PREFIX = "atlas-pack-operation:sha256:"
CONFIG_PREFIX = "atlas-pack-configuration:sha256:"
BOUNDARY_PREFIX = "atlas-pack-unresolved-boundary:sha256:"
POLICY_ID = "ATLAS-PACK-MUTATION-EXTRACTION-V1"
RUN_CONFIG_PATH = "groovy/runConfig.json"

EXACT_VALUE_STATES = {"exact-literal", "exact-number"}
SYMBOLIC_VALUE_STATES = {"exact-symbol", "qualified-expression"}
UNRESOLVED_VALUE_STATES = {
    "complex-expression",
    "concatenated-expression",
    "identifier-expression",
    "interpolated-literal",
    "missing-expression",
}


class AtlasPackMutationError(ValueError):
    """Raised when ordered pack extraction cannot retain fidelity."""


def _load_policy() -> dict[str, Any]:
    try:
        policy = read_json(POLICY_PATH)
    except (OSError, ValueError) as exc:
        raise AtlasPackMutationError(f"cannot read mutation policy: {exc}") from exc
    required = {
        "schema_version",
        "policy_id",
        "source_language",
        "execution_claim",
        "stage_order",
        "target_states",
        "rules",
        "fidelity",
    }
    if not isinstance(policy, dict) or set(policy) != required:
        raise AtlasPackMutationError("pack mutation policy fields differ")
    if (
        policy["schema_version"] != 1
        or policy["policy_id"] != POLICY_ID
        or policy["source_language"] != "groovy"
        or policy["execution_claim"] != "none-static-occurrence-only"
    ):
        raise AtlasPackMutationError("pack mutation policy identity differs")
    if policy["target_states"] != ["exact", "symbolic", "unresolved"]:
        raise AtlasPackMutationError("pack mutation target states differ")
    loaders = [item.get("loader") for item in policy["stage_order"]]
    if loaders != ["preInit", "postInit"]:
        raise AtlasPackMutationError("pack mutation stage order differs")
    rule_ids = [item.get("rule_id") for item in policy["rules"]]
    if len(rule_ids) != len(set(rule_ids)):
        raise AtlasPackMutationError("pack mutation rule IDs are not unique")
    for rule in policy["rules"]:
        if set(rule) != {
            "rule_id",
            "operation_kind",
            "match",
            "values",
            "target_argument_index",
        }:
            raise AtlasPackMutationError(
                f"pack mutation rule fields differ: {rule.get('rule_id')}"
            )
        if rule["operation_kind"] not in {
            "addition",
            "copy",
            "property-change",
            "removal",
            "replacement",
        }:
            raise AtlasPackMutationError(
                f"unknown pack mutation kind: {rule['operation_kind']}"
            )
        if rule["match"] not in {"leaf-in", "leaf-prefix"}:
            raise AtlasPackMutationError(
                f"unknown pack mutation matcher: {rule['match']}"
            )
        if (
            not isinstance(rule["values"], list)
            or not rule["values"]
            or any(not isinstance(value, str) or not value for value in rule["values"])
        ):
            raise AtlasPackMutationError(
                f"invalid pack mutation values: {rule['rule_id']}"
            )
        if rule["target_argument_index"] not in {None, 0}:
            raise AtlasPackMutationError(
                f"unsupported target argument: {rule['rule_id']}"
            )
    return policy


def _with_id(prefix: str, field: str, value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    result[field] = prefix + canonical_sha256(value)
    return result


def _source_records(
    source_index: dict[str, Any],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[tuple[str, int, int, str], dict[str, Any]],
]:
    by_id: dict[str, dict[str, Any]] = {}
    scripts: dict[str, dict[str, Any]] = {}
    spans: dict[tuple[str, int, int, str], dict[str, Any]] = {}
    for record in source_index["records"]:
        identifier = record["source_span_id"]
        by_id[identifier] = record
        identity = record["identity"]
        if identity["source_id"] != "SRC-PACK":
            continue
        key = (
            identity["path"],
            identity["byte_start"],
            identity["byte_end"],
            record["span_kind"],
        )
        if key in spans and spans[key] != record:
            raise AtlasPackMutationError(
                f"ambiguous source spans at {identity['path']}:{identity['byte_start']}"
            )
        spans[key] = record
        if record["span_kind"] == "script":
            path = identity["path"]
            if path in scripts:
                raise AtlasPackMutationError(f"duplicate script source span: {path}")
            scripts[path] = record
    return by_id, scripts, spans


def _leaf(callee: str) -> str:
    return callee.rsplit(".", 1)[-1]


def _matching_rule(
    policy: dict[str, Any],
    callee: str,
) -> dict[str, Any] | None:
    leaf = _leaf(callee)
    matches: list[dict[str, Any]] = []
    for rule in policy["rules"]:
        if rule["match"] == "leaf-in" and leaf in rule["values"]:
            matches.append(rule)
        elif rule["match"] == "leaf-prefix" and any(
            leaf.startswith(value) and len(leaf) > len(value)
            for value in rule["values"]
        ):
            matches.append(rule)
    if len(matches) > 1:
        raise AtlasPackMutationError(
            f"operation matches multiple classification rules: {callee}"
        )
    return matches[0] if matches else None


def _target_from_expression(
    source: str,
    tokens: Sequence[Any],
    indices: Sequence[int],
) -> dict[str, Any]:
    preview, digest, normalized, value_state = expression_value(
        source, tokens, indices
    )
    if value_state in EXACT_VALUE_STATES:
        state = "exact"
        reason = "literal-target"
    elif value_state in SYMBOLIC_VALUE_STATES:
        state = "symbolic"
        reason = "static-symbol-requires-runtime-resolution"
    elif value_state in UNRESOLVED_VALUE_STATES:
        state = "unresolved"
        reason = "dynamic-target-expression"
    else:
        raise AtlasPackMutationError(f"unknown Groovy value state: {value_state}")
    return {
        "state": state,
        "value_state": value_state,
        "normalized_value": normalized,
        "expression_sha256": digest,
        "expression_preview": preview,
        "reason": reason,
    }


def _receiver_target(callee: str) -> dict[str, Any]:
    if "." not in callee:
        return {
            "state": "unresolved",
            "value_state": "missing-expression",
            "normalized_value": "",
            "expression_sha256": hashlib.sha256(b"").hexdigest(),
            "expression_preview": "",
            "reason": "receiver-not-statically-identified",
        }
    receiver = callee.rsplit(".", 1)[0]
    return {
        "state": "symbolic",
        "value_state": "qualified-expression",
        "normalized_value": receiver,
        "expression_sha256": hashlib.sha256(receiver.encode("utf-8")).hexdigest(),
        "expression_preview": receiver,
        "reason": "receiver-symbol-requires-runtime-resolution",
    }


def _builder_target(tokens: Sequence[Any], call_index: int) -> dict[str, Any]:
    cursor = call_index - 1
    while cursor >= 0:
        token = tokens[cursor]
        if token.value in {";", "{", "}"}:
            break
        if (
            token.kind == "IDENT"
            and token.value == "recipeBuilder"
            and cursor > 0
            and tokens[cursor - 1].value == "."
        ):
            callee = member_chain(tokens, cursor)
            receiver = callee.rsplit(".", 1)[0]
            return {
                "state": "symbolic",
                "value_state": "qualified-expression",
                "normalized_value": receiver,
                "expression_sha256": hashlib.sha256(
                    receiver.encode("utf-8")
                ).hexdigest(),
                "expression_preview": receiver,
                "reason": "recipe-map-symbol-requires-runtime-resolution",
            }
        cursor -= 1
    return {
        "state": "unresolved",
        "value_state": "missing-expression",
        "normalized_value": "",
        "expression_sha256": hashlib.sha256(b"").hexdigest(),
        "expression_preview": "",
        "reason": "builder-origin-not-statically-identified",
    }


def _target(
    *,
    rule: dict[str, Any],
    callee: str,
    source: str,
    tokens: Sequence[Any],
    call_index: int,
    arguments: Sequence[Sequence[int]],
) -> dict[str, Any]:
    if _leaf(callee) == "buildAndRegister":
        return _builder_target(tokens, call_index)
    argument = rule["target_argument_index"]
    if argument is None:
        return _receiver_target(callee)
    indices = list(arguments[argument]) if argument < len(arguments) else []
    return _target_from_expression(source, tokens, indices)


def _configured_stages(
    resolver: SourceResolver,
    policy: dict[str, Any],
    config_selections: list[dict[str, Any]],
    script_records: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    selections = {item["key_path"]: item for item in config_selections}
    stages: list[dict[str, Any]] = []
    path_context: dict[str, dict[str, Any]] = {}
    stage_ordinal = 0
    for stage_policy in policy["stage_order"]:
        loader = stage_policy["loader"]
        array = selections.get(f"/loaders/{loader}")
        if array is None or not isinstance(array["selected_value"], list):
            raise AtlasPackMutationError(
                f"runConfig lacks exact loader roots for {loader}"
            )
        roots: list[dict[str, Any]] = []
        scripts: list[dict[str, Any]] = []
        for root_ordinal, root in enumerate(array["selected_value"]):
            if (
                not isinstance(root, str)
                or not root.endswith("/")
                or "/" in root[:-1]
            ):
                raise AtlasPackMutationError(
                    f"unsupported configured Groovy root: {root!r}"
                )
            key_path = f"/loaders/{loader}/{root_ordinal}"
            selected_root = selections.get(key_path)
            if selected_root is None or selected_root["selected_value"] != root:
                raise AtlasPackMutationError(
                    f"runConfig root selection differs at {key_path}"
                )
            roots.append(
                {
                    "root": root,
                    "root_ordinal": root_ordinal,
                    "configuration_source_span_id": selected_root["source_span_id"],
                }
            )
            prefix = "groovy/" + root
            # GroovyScript discovers descendants; repository path ordering is
            # retained after the configured root, independent of OS traversal.
            root_paths = [
                path for path in script_records if path.startswith(prefix)
            ]
            root_paths.sort(key=lambda item: item.encode("utf-8"))
            for file_ordinal, path in enumerate(root_paths):
                if path in path_context:
                    raise AtlasPackMutationError(
                        f"script selected by multiple configured roots: {path}"
                    )
                context = {
                    "stage_id": stage_policy["stage_id"],
                    "lifecycle_stage_id": stage_policy["lifecycle_stage_id"],
                    "stage_ordinal": stage_ordinal,
                    "loader": loader,
                    "root": root,
                    "root_ordinal": root_ordinal,
                    "file_ordinal": file_ordinal,
                    "script_ordinal": len(scripts),
                }
                path_context[path] = context
                scripts.append(
                    {
                        "path": path,
                        "root": root,
                        "root_ordinal": root_ordinal,
                        "file_ordinal": file_ordinal,
                        "script_ordinal": context["script_ordinal"],
                        "script_source_span_id": script_records[path][
                            "source_span_id"
                        ],
                        "selection_state": "configured-static-candidate",
                    }
                )
        stage_payload = {
            "stage_id": stage_policy["stage_id"],
            "lifecycle_stage_id": stage_policy["lifecycle_stage_id"],
            "stage_ordinal": stage_ordinal,
            "loader": loader,
            "configured_roots": roots,
            "selected_scripts": scripts,
            "execution_state": "not-observed-static-selection",
        }
        stages.append(
            _with_id(
                STAGE_PREFIX,
                "stage_selection_id",
                stage_payload,
            )
        )
        stage_ordinal += 1
    missing = sorted(set(script_records) - set(path_context), key=lambda item: item.encode())
    if missing:
        raise AtlasPackMutationError(
            f"Groovy scripts are outside configured loader roots: {missing[:5]}"
        )
    return stages, path_context


def _configuration_selections(
    resolver: SourceResolver,
    source_index_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    _, raw = json_config_entries(resolver, "SRC-PACK", RUN_CONFIG_PATH)
    result: list[dict[str, Any]] = []
    for item in raw:
        span_id = item["source_span_id"]
        if span_id not in source_index_by_id:
            raise AtlasPackMutationError(
                f"source index lacks runConfig span: {item['key_path']}"
            )
        value = item["selected_value"]
        payload = {
            "source_span_id": span_id,
            "key_path": item["key_path"],
            "selected_value": value,
            "selected_value_sha256": item["selected_value_sha256"],
            "selection_state": "locked-pack-value",
        }
        result.append(_with_id(CONFIG_PREFIX, "configuration_selection_id", payload))
    return sorted(result, key=lambda item: item["key_path"].encode("utf-8"))


def _span_at(
    spans: dict[tuple[str, int, int, str], dict[str, Any]],
    *,
    path: str,
    byte_start: int,
    byte_end: int,
    span_kind: str,
) -> dict[str, Any]:
    try:
        return spans[(path, byte_start, byte_end, span_kind)]
    except KeyError as exc:
        raise AtlasPackMutationError(
            f"source index lacks {span_kind} at {path}:{byte_start}-{byte_end}"
        ) from exc


def _script_facts(
    *,
    resolver: SourceResolver,
    policy: dict[str, Any],
    path: str,
    context: dict[str, Any],
    spans: dict[tuple[str, int, int, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source_bytes = resolver.read_bytes("SRC-PACK", path)
    analysis = analyze_bytes(
        {"path": path, "lifecycle": _groovy_lifecycle(path)}, source_bytes
    )
    pairs = bracket_pairs(analysis.tokens, path)
    offsets = _char_byte_offsets(analysis.source)
    declarations: list[dict[str, Any]] = []
    token_locations: dict[tuple[int, int, str], list[tuple[int, Any]]] = {}
    for index, token in enumerate(analysis.tokens):
        token_locations.setdefault(
            (token.line, token.column, token.value), []
        ).append((index, token))
    for item in analysis.declarations:
        location = (int(item["line"]), int(item["column"]), item["symbol_name"])
        matches = token_locations.get(location, [])
        if len(matches) != 1:
            raise AtlasPackMutationError(
                f"declaration token is ambiguous: {path}:{location}"
            )
        _, token = matches[0]
        span_kind = (
            "method"
            if item["symbol_kind"] in {"constructor", "method"}
            else "field"
            if item["symbol_kind"] == "field"
            else "declaration"
        )
        span = _span_at(
            spans,
            path=path,
            byte_start=offsets[token.start],
            byte_end=offsets[token.end] - 1,
            span_kind=span_kind,
        )
        payload = {
            "source_span_id": span["source_span_id"],
            "stage_id": context["stage_id"],
            "script_ordinal": context["script_ordinal"],
            "path": path,
            "symbol_kind": item["symbol_kind"],
            "symbol_name": item["symbol_name"],
            "qualified_hint": item["qualified_hint"],
            "extraction_state": item["extraction_state"],
            "execution_state": "not-observed-static-declaration",
        }
        declarations.append(
            _with_id(DECLARATION_PREFIX, "declaration_id", payload)
        )

    declaration_parens = {item.open_paren_index for item in analysis.callables}
    operations: list[dict[str, Any]] = []
    for index, token in enumerate(analysis.tokens[:-1]):
        if (
            token.kind != "IDENT"
            or analysis.tokens[index + 1].value != "("
            or index + 1 not in pairs
            or index + 1 in declaration_parens
            or token.line in analysis.excluded_reference_lines
        ):
            continue
        callee = member_chain(analysis.tokens, index)
        rule = _matching_rule(policy, callee)
        if rule is None:
            continue
        close_index = pairs[index + 1]
        close = analysis.tokens[close_index]
        span = _span_at(
            spans,
            path=path,
            byte_start=offsets[token.start],
            byte_end=offsets[close.end] - 1,
            span_kind="call-site",
        )
        arguments = split_arguments(analysis.tokens, index + 2, close_index)
        target = _target(
            rule=rule,
            callee=callee,
            source=analysis.source,
            tokens=analysis.tokens,
            call_index=index,
            arguments=arguments,
        )
        payload = {
            "source_span_id": span["source_span_id"],
            "stage_id": context["stage_id"],
            "lifecycle_stage_id": context["lifecycle_stage_id"],
            "root": context["root"],
            "root_ordinal": context["root_ordinal"],
            "script_ordinal": context["script_ordinal"],
            "path": path,
            "byte_start": span["identity"]["byte_start"],
            "operation_kind": rule["operation_kind"],
            "callee": callee,
            "rule_id": rule["rule_id"],
            "target": target,
            "execution_state": "not-observed-static-candidate",
        }
        operations.append(payload)
    operations.sort(key=lambda item: item["byte_start"])
    return declarations, operations


def extract(
    source_index: dict[str, Any],
    resolver: SourceResolver | None = None,
) -> dict[str, Any]:
    if resolver is None:
        resolver = SourceResolver()
    try:
        validate_index(source_index, resolver)
    except AtlasSourceIndexError as exc:
        raise AtlasPackMutationError(f"invalid P01 source index: {exc}") from exc
    policy = _load_policy()
    by_id, scripts, spans = _source_records(source_index)
    config = _configuration_selections(resolver, by_id)
    stages, contexts = _configured_stages(resolver, policy, config, scripts)
    declarations: list[dict[str, Any]] = []
    raw_operations: list[dict[str, Any]] = []
    for path in sorted(contexts, key=lambda item: (
        contexts[item]["stage_ordinal"],
        contexts[item]["script_ordinal"],
    )):
        script_declarations, script_operations = _script_facts(
            resolver=resolver,
            policy=policy,
            path=path,
            context=contexts[path],
            spans=spans,
        )
        declarations.extend(script_declarations)
        raw_operations.extend(script_operations)
    operations: list[dict[str, Any]] = []
    stage_counts: dict[str, int] = {
        stage["stage_id"]: 0 for stage in stages
    }
    for payload in raw_operations:
        operation_index = stage_counts[payload["stage_id"]]
        stage_counts[payload["stage_id"]] += 1
        payload = copy.deepcopy(payload)
        payload["operation_index"] = operation_index
        operation_sha256 = canonical_sha256(payload)
        payload["operation_sha256"] = operation_sha256
        operations.append(_with_id(OPERATION_PREFIX, "operation_id", payload))
    unresolved: list[dict[str, Any]] = []
    for operation in operations:
        if operation["target"]["state"] != "unresolved":
            continue
        payload = {
            "operation_id": operation["operation_id"],
            "source_span_id": operation["source_span_id"],
            "reason": operation["target"]["reason"],
            "boundary_state": "unresolved",
        }
        unresolved.append(
            _with_id(BOUNDARY_PREFIX, "boundary_id", payload)
        )
    referenced_ids = {
        item["source_span_id"] for item in config + declarations + operations
    }
    referenced_ids.update(
        script["script_source_span_id"]
        for stage in stages
        for script in stage["selected_scripts"]
    )
    referenced_ids.update(
        root["configuration_source_span_id"]
        for stage in stages
        for root in stage["configured_roots"]
    )
    referenced_records = [by_id[item] for item in sorted(referenced_ids)]
    counts = {
        kind: sum(1 for item in operations if item["operation_kind"] == kind)
        for kind in (
            "addition",
            "copy",
            "property-change",
            "removal",
            "replacement",
        )
    }
    document: dict[str, Any] = {
        "schema_version": 1,
        "format": FORMAT,
        "extraction_id": "",
        "snapshot_id": resolver.snapshot_id,
        "source_lock_id": resolver.source_lock_id,
        "source_lock_sha256": sha256_path(SOURCE_LOCK_PATH),
        "pack_commit": resolver.pack_commit,
        "pack_tree": resolver.pack_tree,
        "source_index_id": source_index["index_id"],
        "referenced_source_span_count": len(referenced_records),
        "referenced_source_spans_sha256": canonical_sha256(referenced_records),
        "policy_id": policy["policy_id"],
        "policy_sha256": canonical_sha256(policy),
        "configuration_selections": config,
        "stages": stages,
        "declarations": declarations,
        "operations": operations,
        "unresolved_boundaries": unresolved,
        "summary": {
            "stage_count": len(stages),
            "script_count": len(scripts),
            "declaration_count": len(declarations),
            "operation_count": len(operations),
            "operation_kind_counts": counts,
            "unresolved_target_count": len(unresolved),
            "execution_claim": "none-static-occurrence-only",
        },
    }
    document["extraction_id"] = EXTRACTION_PREFIX + canonical_sha256(
        {key: value for key, value in document.items() if key != "extraction_id"}
    )
    validate_extraction(document, source_index, resolver)
    return document


def _validate_content_id(
    record: dict[str, Any],
    *,
    field: str,
    prefix: str,
) -> None:
    expected = prefix + canonical_sha256(
        {key: value for key, value in record.items() if key != field}
    )
    if record.get(field) != expected:
        raise AtlasPackMutationError(f"{field} content identity differs")


def validate_extraction(
    document: dict[str, Any],
    source_index: dict[str, Any],
    resolver: SourceResolver | None = None,
) -> dict[str, Any]:
    if resolver is None:
        resolver = SourceResolver()
    try:
        validate_index(source_index, resolver)
        schema = read_json(SCHEMA_PATH)
        _validate_schema_definition(schema, SCHEMA_PATH.name)
        _validate_schema_value(document, schema, FORMAT)
    except (AtlasSourceIndexError, OSError, ValueError) as exc:
        raise AtlasPackMutationError(f"pack mutation schema failed: {exc}") from exc
    policy = _load_policy()
    expected_id = EXTRACTION_PREFIX + canonical_sha256(
        {key: value for key, value in document.items() if key != "extraction_id"}
    )
    if document["extraction_id"] != expected_id:
        raise AtlasPackMutationError("pack mutation extraction ID differs")
    if (
        document["snapshot_id"] != resolver.snapshot_id
        or document["source_lock_id"] != resolver.source_lock_id
        or document["source_lock_sha256"] != sha256_path(SOURCE_LOCK_PATH)
        or document["pack_commit"] != resolver.pack_commit
        or document["pack_tree"] != resolver.pack_tree
        or document["source_index_id"] != source_index["index_id"]
        or document["policy_id"] != policy["policy_id"]
        or document["policy_sha256"] != canonical_sha256(policy)
    ):
        raise AtlasPackMutationError("pack mutation extraction binding differs")
    by_id = {item["source_span_id"]: item for item in source_index["records"]}
    referenced: set[str] = set()
    configuration_by_path: dict[str, dict[str, Any]] = {}
    for item in document["configuration_selections"]:
        _validate_content_id(
            item, field="configuration_selection_id", prefix=CONFIG_PREFIX
        )
        if canonical_sha256(item["selected_value"]) != item["selected_value_sha256"]:
            raise AtlasPackMutationError("configuration selected-value digest differs")
        source_record = by_id.get(item["source_span_id"])
        if (
            source_record is None
            or source_record["span_kind"] != "configuration-entry"
            or source_record["identity"]["source_id"] != "SRC-PACK"
            or source_record["identity"]["path"] != RUN_CONFIG_PATH
            or source_record["identity"]["symbol"]
            != f"json-pointer:{item['key_path']}"
        ):
            raise AtlasPackMutationError(
                "configuration selection does not bind its exact P01 span"
            )
        if item["key_path"] in configuration_by_path:
            raise AtlasPackMutationError("duplicate configuration key path")
        configuration_by_path[item["key_path"]] = item
        referenced.add(item["source_span_id"])
    expected_stage_ordinal = 0
    selected_paths: set[str] = set()
    script_contexts: dict[str, dict[str, Any]] = {}
    stage_operation_indices: dict[str, list[int]] = {}
    if len(document["stages"]) != len(policy["stage_order"]):
        raise AtlasPackMutationError("extracted stage set differs from policy")
    for stage, stage_policy in zip(
        document["stages"], policy["stage_order"], strict=True
    ):
        _validate_content_id(
            stage, field="stage_selection_id", prefix=STAGE_PREFIX
        )
        if stage["stage_ordinal"] != expected_stage_ordinal:
            raise AtlasPackMutationError("stage ordinals are not contiguous")
        if (
            stage["stage_id"] != stage_policy["stage_id"]
            or stage["lifecycle_stage_id"]
            != stage_policy["lifecycle_stage_id"]
            or stage["loader"] != stage_policy["loader"]
        ):
            raise AtlasPackMutationError(
                "extracted stage identity differs from policy"
            )
        expected_stage_ordinal += 1
        if stage["execution_state"] != "not-observed-static-selection":
            raise AtlasPackMutationError("stage selection makes an execution claim")
        loader_selection = configuration_by_path.get(
            f"/loaders/{stage['loader']}"
        )
        if (
            loader_selection is None
            or not isinstance(loader_selection["selected_value"], list)
        ):
            raise AtlasPackMutationError(
                "stage lacks its exact runConfig loader selection"
            )
        configured_values = loader_selection["selected_value"]
        if [item["root"] for item in stage["configured_roots"]] != configured_values:
            raise AtlasPackMutationError(
                "configured stage roots differ from runConfig"
            )
        for root_index, root in enumerate(stage["configured_roots"]):
            key_path = f"/loaders/{stage['loader']}/{root_index}"
            root_selection = configuration_by_path.get(key_path)
            if (
                root["root_ordinal"] != root_index
                or root_selection is None
                or root_selection["selected_value"] != root["root"]
                or root_selection["source_span_id"]
                != root["configuration_source_span_id"]
            ):
                raise AtlasPackMutationError(
                    "configured root does not bind its exact runConfig entry"
                )
            referenced.add(root["configuration_source_span_id"])
        expected_script_ordinal = 0
        for script in stage["selected_scripts"]:
            if script["script_ordinal"] != expected_script_ordinal:
                raise AtlasPackMutationError("script ordinals are not contiguous")
            expected_script_ordinal += 1
            if script["path"] in selected_paths:
                raise AtlasPackMutationError("script is selected more than once")
            selected_paths.add(script["path"])
            if script["root_ordinal"] >= len(configured_values):
                raise AtlasPackMutationError("script root ordinal is outside stage")
            if configured_values[script["root_ordinal"]] != script["root"]:
                raise AtlasPackMutationError("script root differs from runConfig")
            source_record = by_id.get(script["script_source_span_id"])
            if (
                source_record is None
                or source_record["span_kind"] != "script"
                or source_record["identity"]["source_id"] != "SRC-PACK"
                or source_record["identity"]["path"] != script["path"]
            ):
                raise AtlasPackMutationError(
                    "selected script does not bind its exact P01 span"
                )
            script_contexts[script["path"]] = {
                "stage_id": stage["stage_id"],
                "lifecycle_stage_id": stage["lifecycle_stage_id"],
                "stage_ordinal": stage["stage_ordinal"],
                "root": script["root"],
                "root_ordinal": script["root_ordinal"],
                "script_ordinal": script["script_ordinal"],
            }
            referenced.add(script["script_source_span_id"])
        expected_paths: list[str] = []
        for root_index, root in enumerate(configured_values):
            root_paths = [
                record["identity"]["path"]
                for record in by_id.values()
                if record["span_kind"] == "script"
                and record["identity"]["source_id"] == "SRC-PACK"
                and record["identity"]["path"].startswith(f"groovy/{root}")
            ]
            root_paths.sort(key=lambda item: item.encode("utf-8"))
            for file_index, path in enumerate(root_paths):
                expected_paths.append(path)
                script = stage["selected_scripts"][len(expected_paths) - 1]
                if (
                    script["path"] != path
                    or script["root_ordinal"] != root_index
                    or script["file_ordinal"] != file_index
                ):
                    raise AtlasPackMutationError(
                        "selected script order differs from configured root/path order"
                    )
        if len(expected_paths) != len(stage["selected_scripts"]):
            raise AtlasPackMutationError(
                "stage script selection is not complete"
            )
    source_script_paths = {
        record["identity"]["path"]
        for record in by_id.values()
        if record["span_kind"] == "script"
        and record["identity"]["source_id"] == "SRC-PACK"
    }
    if selected_paths != source_script_paths:
        raise AtlasPackMutationError(
            "configured stages do not select every P01 pack script"
        )
    for item in document["declarations"]:
        _validate_content_id(
            item, field="declaration_id", prefix=DECLARATION_PREFIX
        )
        if item["execution_state"] != "not-observed-static-declaration":
            raise AtlasPackMutationError("declaration makes an execution claim")
        context = script_contexts.get(item["path"])
        source_record = by_id.get(item["source_span_id"])
        if (
            context is None
            or item["stage_id"] != context["stage_id"]
            or item["script_ordinal"] != context["script_ordinal"]
            or source_record is None
            or source_record["span_kind"]
            not in {"declaration", "field", "method"}
            or source_record["identity"]["path"] != item["path"]
        ):
            raise AtlasPackMutationError(
                "declaration does not bind its selected script and P01 span"
            )
        referenced.add(item["source_span_id"])
    operation_ids: set[str] = set()
    unresolved_operation_ids: set[str] = set()
    operation_order: list[tuple[int, int, int]] = []
    for item in document["operations"]:
        _validate_content_id(item, field="operation_id", prefix=OPERATION_PREFIX)
        operation_payload = {
            key: value
            for key, value in item.items()
            if key not in {"operation_id", "operation_sha256"}
        }
        if item["operation_sha256"] != canonical_sha256(operation_payload):
            raise AtlasPackMutationError("operation semantic digest differs")
        if item["execution_state"] != "not-observed-static-candidate":
            raise AtlasPackMutationError("static operation makes an execution claim")
        context = script_contexts.get(item["path"])
        source_record = by_id.get(item["source_span_id"])
        rule = next(
            (
                candidate
                for candidate in policy["rules"]
                if candidate["rule_id"] == item["rule_id"]
            ),
            None,
        )
        if (
            context is None
            or item["stage_id"] != context["stage_id"]
            or item["lifecycle_stage_id"] != context["lifecycle_stage_id"]
            or item["root"] != context["root"]
            or item["root_ordinal"] != context["root_ordinal"]
            or item["script_ordinal"] != context["script_ordinal"]
            or source_record is None
            or source_record["span_kind"] != "call-site"
            or source_record["identity"]["source_id"] != "SRC-PACK"
            or source_record["identity"]["path"] != item["path"]
            or source_record["identity"]["byte_start"] != item["byte_start"]
        ):
            raise AtlasPackMutationError(
                "operation does not bind its selected script and P01 call span"
            )
        if (
            rule is None
            or _matching_rule(policy, item["callee"]) != rule
            or item["operation_kind"] != rule["operation_kind"]
        ):
            raise AtlasPackMutationError(
                "operation classification differs from policy"
            )
        target = item["target"]
        expected_value_states = {
            "exact": EXACT_VALUE_STATES,
            "symbolic": SYMBOLIC_VALUE_STATES,
            "unresolved": UNRESOLVED_VALUE_STATES,
        }[target["state"]]
        if target["value_state"] not in expected_value_states:
            raise AtlasPackMutationError(
                "operation target state differs from expression state"
            )
        stage_operation_indices.setdefault(item["stage_id"], []).append(
            item["operation_index"]
        )
        operation_order.append(
            (
                context["stage_ordinal"],
                context["script_ordinal"],
                item["byte_start"],
            )
        )
        operation_ids.add(item["operation_id"])
        referenced.add(item["source_span_id"])
        if item["target"]["state"] == "unresolved":
            unresolved_operation_ids.add(item["operation_id"])
    if operation_order != sorted(operation_order):
        raise AtlasPackMutationError(
            "operations are not in stage/script/source order"
        )
    for values in stage_operation_indices.values():
        if values != list(range(len(values))):
            raise AtlasPackMutationError("operation indices are not stage-contiguous")
    boundary_operations: set[str] = set()
    for item in document["unresolved_boundaries"]:
        _validate_content_id(item, field="boundary_id", prefix=BOUNDARY_PREFIX)
        if item["operation_id"] not in operation_ids:
            raise AtlasPackMutationError("unresolved boundary references no operation")
        if item["operation_id"] in boundary_operations:
            raise AtlasPackMutationError("duplicate unresolved operation boundary")
        boundary_operations.add(item["operation_id"])
        referenced.add(item["source_span_id"])
    if boundary_operations != unresolved_operation_ids:
        raise AtlasPackMutationError("unresolved target boundaries are incomplete")
    missing = referenced - set(by_id)
    if missing:
        raise AtlasPackMutationError(
            f"extraction references absent source spans: {sorted(missing)[:3]}"
        )
    referenced_records = [by_id[item] for item in sorted(referenced)]
    if (
        document["referenced_source_span_count"] != len(referenced_records)
        or document["referenced_source_spans_sha256"]
        != canonical_sha256(referenced_records)
    ):
        raise AtlasPackMutationError("referenced source-span set digest differs")
    summary = document["summary"]
    if (
        summary["stage_count"] != len(document["stages"])
        or summary["script_count"] != len(selected_paths)
        or summary["declaration_count"] != len(document["declarations"])
        or summary["operation_count"] != len(document["operations"])
        or summary["unresolved_target_count"] != len(document["unresolved_boundaries"])
        or summary["execution_claim"] != "none-static-occurrence-only"
    ):
        raise AtlasPackMutationError("pack mutation summary differs")
    expected_counts = {
        kind: sum(
            1 for item in document["operations"]
            if item["operation_kind"] == kind
        )
        for kind in (
            "addition",
            "copy",
            "property-change",
            "removal",
            "replacement",
        )
    }
    if summary["operation_kind_counts"] != expected_counts:
        raise AtlasPackMutationError("operation-kind counts differ")
    return document


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    action = result.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true")
    result.add_argument("--source-index", type=Path)
    result.add_argument("--output", type=Path)
    result.add_argument("--source-root", type=Path)
    result.add_argument("--pack-root", type=Path)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        source_index_path = args.source_index or default_source_index_path()
        output = args.output or default_output_path()
        source_root = args.source_root or atlas_source_root()
        source_index = json.loads(source_index_path.read_text(encoding="utf-8"))
        resolver = SourceResolver(pack_root=args.pack_root, source_root=source_root)
        if args.write:
            document = extract(source_index, resolver)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(canonical_json(document) + b"\n")
        else:
            document = json.loads(output.read_text(encoding="utf-8"))
            validate_extraction(document, source_index, resolver)
            expected = extract(source_index, resolver)
            if document != expected:
                raise AtlasPackMutationError(
                    "pack mutation extraction differs from deterministic regeneration"
                )
        print(
            "Atlas pack mutation extraction passed: "
            f"{document['summary']['script_count']} scripts, "
            f"{document['summary']['operation_count']} operations, "
            f"{document['summary']['unresolved_target_count']} unresolved targets; "
            f"{document['extraction_id']}"
        )
        return 0
    except (AtlasPackMutationError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
