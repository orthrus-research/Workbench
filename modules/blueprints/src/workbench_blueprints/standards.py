#!/usr/bin/env python3

"""Strict Blueprints implementation-standard compiler and registry lock."""

from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
import unicodedata
from typing import Any, Iterable, NoReturn

from jsonschema import Draft202012Validator, SchemaError
import yaml
from yaml.events import AliasEvent

from workbench_blueprints.layout import (
    SCHEMA_ROOT,
    WORKBENCH_ROOT,
)

REPO_ROOT = WORKBENCH_ROOT
STANDARD_SCHEMA_PATH = SCHEMA_ROOT / "blueprints-standard-v1.schema.json"
REGISTRY_SCHEMA_PATH = (
    SCHEMA_ROOT / "blueprints-standard-registry-v1.schema.json"
)
LEDGER_SCHEMA_PATH = (
    SCHEMA_ROOT / "blueprints-allocation-ledger-v1.schema.json"
)
REGISTRY_FILENAME = "registry.json"
CONTRACT_ID = "BLUEPRINTS-IMPLEMENTATION-STANDARD-V1"
AUTHORING_FORMAT = "susy-blueprints-standard-authoring-v1"
COMPILED_FORMAT = "susy-blueprints-standard-compiled-v1"
REGISTRY_FORMAT = "susy-blueprints-standard-registry-v1"
MAX_SOURCE_BYTES = 1024 * 1024
MAX_CACHED_SCHEMA_BYTES = 1024 * 1024
PROTECTED_PREFIXES = (".git/", ".workbench/blueprints/")
SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class StandardDiagnostic(Exception):
    """One stable fail-closed compiler diagnostic."""

    def __init__(self, code: str, location: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.location = location
        self.message = message

    def __str__(self) -> str:
        return f"{self.code} {self.location}: {self.message}"


class _StrictLoader(yaml.SafeLoader):
    """Safe YAML loader which additionally rejects ambiguity-bearing syntax."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(AliasEvent):
            event = self.peek_event()
            raise StandardDiagnostic(
                "BPS101_YAML_ALIAS",
                f"line:{event.start_mark.line + 1}",
                "YAML aliases are forbidden",
            )
        event = self.peek_event()
        if getattr(event, "anchor", None) is not None:
            raise StandardDiagnostic(
                "BPS102_YAML_ANCHOR",
                f"line:{event.start_mark.line + 1}",
                "YAML anchors are forbidden",
            )
        return super().compose_node(parent, index)

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Any, Any]:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise StandardDiagnostic(
                    "BPS103_YAML_KEY",
                    f"line:{key_node.start_mark.line + 1}",
                    "mapping keys must be strings",
                )
            if key in mapping:
                raise StandardDiagnostic(
                    "BPS104_DUPLICATE_KEY",
                    f"line:{key_node.start_mark.line + 1}",
                    f"duplicate mapping key {key!r}",
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StandardDiagnostic(
            "BPS105_JSON_READ", str(path), str(exc)
        ) from exc
    if not isinstance(value, dict):
        raise StandardDiagnostic(
            "BPS106_JSON_ROOT", str(path), "JSON root must be an object"
        )
    return value


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise StandardDiagnostic(
                "BPS107_DUPLICATE_JSON_KEY", "/", f"duplicate key {key!r}"
            )
        value[key] = item
    return value


@lru_cache(maxsize=32)
def _check_schema_content(
    serialized: str, validator_class: type[Draft202012Validator]
) -> None:
    # Only successful checks are cached. Preserve key order so invalid schema
    # diagnostics use the same first violation as the freshly loaded object.
    validator_class.check_schema(json.loads(serialized))


def _schema(path: Path) -> dict[str, Any]:
    value = _load_json(path)
    serialized = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    try:
        if len(serialized) <= MAX_CACHED_SCHEMA_BYTES:
            _check_schema_content(serialized, Draft202012Validator)
        else:
            # Large inputs retain validation behavior without occupying the
            # bounded cache. Every call still reads and returns a fresh object.
            Draft202012Validator.check_schema(value)
    except SchemaError as exc:
        raise StandardDiagnostic(
            "BPS108_INVALID_SCHEMA", str(path), exc.message
        ) from exc
    return value


def _json_pointer(parts: Iterable[Any]) -> str:
    encoded = [
        str(part).replace("~", "~0").replace("/", "~1") for part in parts
    ]
    return "/" + "/".join(encoded) if encoded else "/"


def _validate_schema(
    value: dict[str, Any], schema_path: Path, source: str
) -> None:
    validator = Draft202012Validator(_schema(schema_path))
    errors = sorted(
        validator.iter_errors(value),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    if errors:
        error = errors[0]
        raise StandardDiagnostic(
            "BPS200_SCHEMA",
            f"{source}#{_json_pointer(error.absolute_path)}",
            error.message,
        )


def _normalize_json(value: Any, location: str = "/") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return unicodedata.normalize("NFC", value) if isinstance(value, str) else value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise StandardDiagnostic(
                "BPS109_NONFINITE_NUMBER", location, "number must be finite"
            )
        if value == 0:
            return 0
        if value.is_integer():
            return int(value)
        return value
    if isinstance(value, list):
        return [
            _normalize_json(item, f"{location}/{index}")
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise StandardDiagnostic(
                    "BPS103_YAML_KEY", location, "mapping keys must be strings"
                )
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in normalized:
                raise StandardDiagnostic(
                    "BPS110_NORMALIZED_KEY_COLLISION",
                    location,
                    f"keys collide after NFC normalization: {normalized_key!r}",
                )
            normalized[normalized_key] = _normalize_json(
                item, f"{location}/{normalized_key}"
            )
        return normalized
    raise StandardDiagnostic(
        "BPS111_NON_JSON_VALUE",
        location,
        f"YAML value has forbidden type {type(value).__name__}",
    )


def _canonical_number(value: float) -> str:
    rendered = repr(value).lower()
    if "e" in rendered:
        mantissa, exponent = rendered.split("e", 1)
        mantissa = mantissa.removesuffix(".0")
        sign = ""
        if exponent.startswith(("+", "-")):
            if exponent[0] == "-":
                sign = "-"
            exponent = exponent[1:]
        exponent = exponent.lstrip("0") or "0"
        return f"{mantissa}e{sign}{exponent}"

    fixed = rendered.removesuffix(".0")
    negative = fixed.startswith("-")
    unsigned = fixed[1:] if negative else fixed
    if "." not in unsigned:
        return fixed
    integer, fractional = unsigned.split(".", 1)
    combined = integer + fractional
    first = next(
        (index for index, character in enumerate(combined) if character != "0"),
        len(combined) - 1,
    )
    digits = combined[first:].rstrip("0")
    exponent = len(integer) - first - 1
    mantissa = digits[0] + (("." + digits[1:]) if len(digits) > 1 else "")
    scientific = ("-" if negative else "") + mantissa + f"e{exponent}"
    return min((fixed, scientific), key=lambda item: (len(item), item))


def canonical_json(value: Any) -> str:
    """Serialize the contract's normalized, shortest-lossless JSON subset."""

    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite canonical number")
        return _canonical_number(value)
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            f"{canonical_json(key)}:{canonical_json(value[key])}"
            for key in sorted(value)
        ) + "}"
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_json(value: Any) -> str:
    return _digest_bytes(canonical_json(value).encode("utf-8"))


def _read_yaml(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        source = path.read_bytes()
    except OSError as exc:
        raise StandardDiagnostic("BPS112_SOURCE_READ", str(path), str(exc)) from exc
    if len(source) > MAX_SOURCE_BYTES:
        raise StandardDiagnostic(
            "BPS113_SOURCE_SIZE",
            str(path),
            f"source exceeds {MAX_SOURCE_BYTES} bytes",
        )
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StandardDiagnostic(
            "BPS114_SOURCE_ENCODING", str(path), "source is not UTF-8"
        ) from exc
    try:
        documents = list(yaml.load_all(text, Loader=_StrictLoader))
    except StandardDiagnostic:
        raise
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = (
            f"{path}:line:{mark.line + 1}" if mark is not None else str(path)
        )
        raise StandardDiagnostic("BPS115_YAML_PARSE", location, str(exc)) from exc
    if len(documents) != 1:
        raise StandardDiagnostic(
            "BPS116_YAML_DOCUMENTS",
            str(path),
            f"expected one YAML document, found {len(documents)}",
        )
    value = _normalize_json(documents[0])
    if not isinstance(value, dict):
        raise StandardDiagnostic(
            "BPS117_YAML_ROOT", str(path), "YAML root must be a mapping"
        )
    return value, source


def _semver(value: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(value)
    if match is None:
        raise ValueError(value)
    return tuple(int(group) for group in match.groups())  # type: ignore[return-value]


def _unique(rows: list[Any], key: str | None, location: str) -> set[Any]:
    values = [row[key] if key is not None else row for row in rows]
    seen: set[Any] = set()
    for index, value in enumerate(values):
        if value in seen:
            raise StandardDiagnostic(
                "BPS201_DUPLICATE_ID",
                f"{location}/{index}",
                f"duplicate value {value!r}",
            )
        seen.add(value)
    return seen


def _safe_path(value: str, *, directory: bool, location: str) -> None:
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or value.endswith("/") != directory
    ):
        raise StandardDiagnostic(
            "BPS202_UNSAFE_PATH", location, f"invalid path {value!r}"
        )
    stripped = value[:-1] if directory else value
    parts = stripped.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise StandardDiagnostic(
            "BPS202_UNSAFE_PATH", location, f"invalid path {value!r}"
        )
    normalized = str(PurePosixPath(stripped)) + ("/" if directory else "")
    if normalized != value:
        raise StandardDiagnostic(
            "BPS202_UNSAFE_PATH", location, f"path is not normalized: {value!r}"
        )
    candidate = value if directory else value + "/"
    if any(candidate.startswith(prefix) for prefix in PROTECTED_PREFIXES):
        raise StandardDiagnostic(
            "BPS203_PROTECTED_PATH", location, f"protected path {value!r}"
        )


def _path_is_allowed(path: str, prefixes: Iterable[str]) -> bool:
    return any(path.startswith(prefix) for prefix in prefixes)


def _value_matches_type(value: Any, value_type: str) -> bool:
    if value_type == "json":
        return True
    if value_type == "string":
        return isinstance(value, str)
    if value_type == "boolean":
        return isinstance(value, bool)
    if value_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if value_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def _check_constraints(
    value: Any, parameter: dict[str, Any], location: str
) -> None:
    value_type = parameter["value_type"]
    if not _value_matches_type(value, value_type):
        raise StandardDiagnostic(
            "BPS204_DEFAULT_TYPE",
            location,
            f"default does not match {value_type}",
        )
    constraints = parameter["constraints"]
    if "enum" in constraints and value not in constraints["enum"]:
        raise StandardDiagnostic(
            "BPS205_DEFAULT_CONSTRAINT", location, "default is not in enum"
        )
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in constraints and value < constraints["minimum"]:
            raise StandardDiagnostic(
                "BPS205_DEFAULT_CONSTRAINT", location, "default is below minimum"
            )
        if "maximum" in constraints and value > constraints["maximum"]:
            raise StandardDiagnostic(
                "BPS205_DEFAULT_CONSTRAINT", location, "default is above maximum"
            )
    if isinstance(value, str):
        if "min_length" in constraints and len(value) < constraints["min_length"]:
            raise StandardDiagnostic(
                "BPS205_DEFAULT_CONSTRAINT", location, "default is too short"
            )
        if "max_length" in constraints and len(value) > constraints["max_length"]:
            raise StandardDiagnostic(
                "BPS205_DEFAULT_CONSTRAINT", location, "default is too long"
            )
        if "pattern" in constraints:
            try:
                matches = re.fullmatch(constraints["pattern"], value) is not None
            except re.error as exc:
                raise StandardDiagnostic(
                    "BPS206_INVALID_PATTERN",
                    location,
                    f"invalid constraint pattern: {exc}",
                ) from exc
            if not matches:
                raise StandardDiagnostic(
                    "BPS205_DEFAULT_CONSTRAINT",
                    location,
                    "default does not match pattern",
                )
    if (
        "minimum" in constraints
        and "maximum" in constraints
        and constraints["minimum"] > constraints["maximum"]
    ):
        raise StandardDiagnostic(
            "BPS207_CONSTRAINT_RANGE", location, "minimum exceeds maximum"
        )
    if (
        "min_length" in constraints
        and "max_length" in constraints
        and constraints["min_length"] > constraints["max_length"]
    ):
        raise StandardDiagnostic(
            "BPS207_CONSTRAINT_RANGE", location, "min_length exceeds max_length"
        )


def _check_expression(
    expression: Any, parameter_names: set[str], location: str
) -> None:
    if not isinstance(expression, dict):
        raise StandardDiagnostic(
            "BPS208_EXPRESSION", location, "expression must be an object"
        )
    if set(expression) != {"operator", "arguments"}:
        raise StandardDiagnostic(
            "BPS208_EXPRESSION", location, "expression fields are not closed"
        )
    operator = expression["operator"]
    arguments = expression["arguments"]
    arities: dict[str, tuple[int, int | None]] = {
        "literal": (1, 1),
        "parameter": (1, 1),
        "concat": (1, None),
        "lowercase": (1, 1),
        "uppercase": (1, 1),
        "replace": (3, 3),
        "slugify": (1, 1),
        "posix-path-join": (1, None),
        "json-pointer-get": (2, 2),
    }
    if operator not in arities or not isinstance(arguments, list):
        raise StandardDiagnostic(
            "BPS208_EXPRESSION", location, "unknown operator or invalid arguments"
        )
    minimum, maximum = arities[operator]
    if len(arguments) < minimum or (
        maximum is not None and len(arguments) > maximum
    ):
        raise StandardDiagnostic(
            "BPS209_EXPRESSION_ARITY",
            location,
            f"{operator} expects {minimum}"
            + (f"..{maximum}" if maximum != minimum else "")
            + " arguments",
        )
    if operator == "literal":
        return
    if operator == "parameter":
        if not isinstance(arguments[0], str) or arguments[0] not in parameter_names:
            raise StandardDiagnostic(
                "BPS210_PARAMETER_REFERENCE",
                f"{location}/arguments/0",
                f"unknown parameter {arguments[0]!r}",
            )
        return
    for index, argument in enumerate(arguments):
        _check_expression(argument, parameter_names, f"{location}/arguments/{index}")


def _expression_parameter_refs(expression: dict[str, Any]) -> set[str]:
    if expression["operator"] == "parameter":
        return {expression["arguments"][0]}
    refs: set[str] = set()
    if expression["operator"] != "literal":
        for argument in expression["arguments"]:
            refs.update(_expression_parameter_refs(argument))
    return refs


def _reject_symlink_components(root: Path, relative_path: str, location: str) -> None:
    cursor = root
    for part in PurePosixPath(relative_path).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise StandardDiagnostic(
                "BPS239_ASSET_SYMLINK",
                location,
                f"asset path contains symlink component {cursor}",
            )


def _verify_asset(
    asset_root: Path, record: dict[str, Any], location: str
) -> None:
    _safe_path(record["path"], directory=False, location=f"{location}/path")
    root = asset_root.resolve()
    _reject_symlink_components(root, record["path"], location)
    candidate = (root / record["path"]).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise StandardDiagnostic(
            "BPS211_ASSET_ESCAPE", location, "asset resolves outside asset root"
        ) from exc
    if not candidate.is_file() or candidate.is_symlink():
        raise StandardDiagnostic(
            "BPS212_ASSET_MISSING", location, f"missing regular file {record['path']!r}"
        )
    actual = _digest_bytes(candidate.read_bytes())
    if actual != record["sha256"]:
        raise StandardDiagnostic(
            "BPS213_ASSET_DIGEST",
            location,
            f"asset digest is {actual}, expected {record['sha256']}",
        )


def _validate_semantics(
    value: dict[str, Any], *, source: str, asset_root: Path
) -> None:
    def fail(code: str, path: str, message: str) -> NoReturn:
        raise StandardDiagnostic(code, f"{source}#{path}", message)

    if value["format"] != AUTHORING_FORMAT:
        fail("BPS214_AUTHORING_FORMAT", "/format", "source must use authoring format")
    if value["kind"] == "primary":
        if value["standard_key"] != value["feature_family"]:
            fail(
                "BPS215_PRIMARY_KEY",
                "/standard_key",
                "primary standard_key must equal feature_family",
            )
    elif value["standard_key"] == value["feature_family"]:
        fail(
            "BPS216_COMPONENT_KEY",
            "/standard_key",
            "component standard_key must differ from feature_family",
        )

    _verify_asset(asset_root, value["mandatory_core"], f"{source}#/mandatory_core")
    variants = value["variants"]
    variant_ids = _unique(variants, "id", f"{source}#/variants")
    template_ids = {value["mandatory_core"]["id"]}
    for index, variant in enumerate(variants):
        if variant["template"]["id"] in template_ids:
            fail(
                "BPS201_DUPLICATE_ID",
                f"/variants/{index}/template/id",
                f"duplicate template id {variant['template']['id']!r}",
            )
        template_ids.add(variant["template"]["id"])
        _verify_asset(
            asset_root, variant["template"], f"{source}#/variants/{index}/template"
        )

    parameters = value["parameters"]
    parameter_names = _unique(parameters, "name", f"{source}#/parameters")
    for index, parameter in enumerate(parameters):
        location = f"/parameters/{index}"
        present = {
            name
            for name in ("default", "derivation", "allocation_domain")
            if name in parameter
        }
        expected = {
            "required": set(),
            "derived": {"derivation"},
            "allocated": {"allocation_domain"},
            "defaulted": {"default"},
            "optional": set(),
        }[parameter["class"]]
        if present != expected:
            fail(
                "BPS217_PARAMETER_CLASS",
                location,
                f"{parameter['class']} parameter requires exactly {sorted(expected)}",
            )
        if "default" in parameter:
            _check_constraints(
                parameter["default"], parameter, f"{source}#{location}/default"
            )
        else:
            constraints = parameter["constraints"]
            if (
                "minimum" in constraints
                and "maximum" in constraints
                and constraints["minimum"] > constraints["maximum"]
            ):
                fail("BPS207_CONSTRAINT_RANGE", location, "minimum exceeds maximum")
            if (
                "min_length" in constraints
                and "max_length" in constraints
                and constraints["min_length"] > constraints["max_length"]
            ):
                fail(
                    "BPS207_CONSTRAINT_RANGE",
                    location,
                    "min_length exceeds max_length",
                )
            if "pattern" in constraints:
                try:
                    re.compile(constraints["pattern"])
                except re.error as exc:
                    fail("BPS206_INVALID_PATTERN", location, str(exc))
        constraints = parameter["constraints"]
        numeric_fields = {"minimum", "maximum"} & set(constraints)
        string_fields = {"min_length", "max_length", "pattern"} & set(constraints)
        if numeric_fields and parameter["value_type"] not in {"integer", "number"}:
            fail(
                "BPS234_CONSTRAINT_TYPE",
                f"{location}/constraints",
                "numeric constraints require integer or number value_type",
            )
        if string_fields and parameter["value_type"] != "string":
            fail(
                "BPS234_CONSTRAINT_TYPE",
                f"{location}/constraints",
                "string constraints require string value_type",
            )
        if "enum" in constraints:
            canonical_values = [
                canonical_json(item) for item in constraints["enum"]
            ]
            if len(canonical_values) != len(set(canonical_values)):
                fail(
                    "BPS201_DUPLICATE_ID",
                    f"{location}/constraints/enum",
                    "constraint enum contains duplicate values",
                )
            if any(
                not _value_matches_type(item, parameter["value_type"])
                for item in constraints["enum"]
            ):
                fail(
                    "BPS234_CONSTRAINT_TYPE",
                    f"{location}/constraints/enum",
                    "enum value does not match parameter value_type",
                )
        if "derivation" in parameter:
            _check_expression(
                parameter["derivation"],
                parameter_names,
                f"{source}#{location}/derivation",
            )

    derivation_graph = {
        parameter["name"]: (
            _expression_parameter_refs(parameter["derivation"])
            & {
                row["name"]
                for row in parameters
                if row["class"] == "derived"
            }
        )
        for parameter in parameters
        if parameter["class"] == "derived"
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit_derivation(name: str) -> None:
        if name in visiting:
            fail(
                "BPS240_DERIVATION_CYCLE",
                "/parameters",
                f"derived parameter cycle includes {name!r}",
            )
        if name in visited:
            return
        visiting.add(name)
        for dependency in derivation_graph.get(name, set()):
            visit_derivation(dependency)
        visiting.remove(name)
        visited.add(name)

    for parameter_name in derivation_graph:
        visit_derivation(parameter_name)

    parameter_by_name = {row["name"]: row for row in parameters}
    for variant_index, variant in enumerate(variants):
        for condition_index, condition in enumerate(variant["when"]):
            if condition["parameter"] not in parameter_names:
                fail(
                    "BPS210_PARAMETER_REFERENCE",
                    f"/variants/{variant_index}/when/{condition_index}/parameter",
                    f"unknown parameter {condition['parameter']!r}",
                )
            has_value = "value" in condition
            if (condition["operator"] == "exists") == has_value:
                fail(
                    "BPS218_CONDITION_VALUE",
                    f"/variants/{variant_index}/when/{condition_index}",
                    "exists forbids value; every other operator requires value",
                )
            if has_value:
                parameter = parameter_by_name[condition["parameter"]]
                condition_value = condition["value"]
                if condition["operator"] == "in":
                    if not isinstance(condition_value, list) or any(
                        not _value_matches_type(item, parameter["value_type"])
                        for item in condition_value
                    ):
                        fail(
                            "BPS241_CONDITION_TYPE",
                            f"/variants/{variant_index}/when/{condition_index}/value",
                            "in requires a list of values matching the parameter type",
                        )
                elif condition["operator"] == "matches":
                    if (
                        parameter["value_type"] != "string"
                        or not isinstance(condition_value, str)
                    ):
                        fail(
                            "BPS241_CONDITION_TYPE",
                            f"/variants/{variant_index}/when/{condition_index}/value",
                            "matches requires a string parameter and string pattern",
                        )
                    try:
                        re.compile(condition_value)
                    except re.error as exc:
                        fail(
                            "BPS206_INVALID_PATTERN",
                            f"/variants/{variant_index}/when/{condition_index}/value",
                            str(exc),
                        )
                elif not _value_matches_type(
                    condition_value, parameter["value_type"]
                ):
                    fail(
                        "BPS241_CONDITION_TYPE",
                        f"/variants/{variant_index}/when/{condition_index}/value",
                        "condition value does not match the parameter type",
                    )

    targets = value["targets"]
    target_pairs = [
        (target["repository_id"], target["integration_surface"])
        for target in targets
    ]
    target_pair_set = set(target_pairs)
    if len(target_pairs) != len(target_pair_set):
        fail("BPS201_DUPLICATE_ID", "/targets", "duplicate repository/surface")
    repositories = {target["repository_id"] for target in targets}
    allowed_by_repository: dict[str, set[str]] = {}
    for target_index, target in enumerate(targets):
        _unique(
            target["allowed_paths"],
            None,
            f"{source}#/targets/{target_index}/allowed_paths",
        )
        allowed_by_repository.setdefault(target["repository_id"], set()).update(
            target["allowed_paths"]
        )
        for path_index, path in enumerate(target["allowed_paths"]):
            _safe_path(
                path,
                directory=True,
                location=f"{source}#/targets/{target_index}/allowed_paths/{path_index}",
            )

    allowed_components = value["composition"]["allowed"]
    component_keys = _unique(
        allowed_components, "standard_key", f"{source}#/composition/allowed"
    )
    if value["standard_key"] in component_keys:
        fail(
            "BPS219_SELF_COMPONENT",
            "/composition/allowed",
            "standard cannot compose itself",
        )
    if value["kind"] == "component" and (
        allowed_components or value["composition"]["precedence"]
    ):
        fail(
            "BPS220_COMPONENT_COMPOSITION",
            "/composition",
            "v1 component standards cannot compose other standards",
        )
    for index, component in enumerate(allowed_components):
        _unique(
            component["versions"],
            None,
            f"{source}#/composition/allowed/{index}/versions",
        )
        _unique(
            component["primary_variants"],
            None,
            f"{source}#/composition/allowed/{index}/primary_variants",
        )
        _unique(
            component["component_variants"],
            None,
            f"{source}#/composition/allowed/{index}/component_variants",
        )
        unknown = set(component["primary_variants"]) - variant_ids
        if unknown:
            fail(
                "BPS221_VARIANT_REFERENCE",
                f"/composition/allowed/{index}/primary_variants",
                f"unknown primary variants {sorted(unknown)}",
            )
    for index, variant in enumerate(variants):
        _unique(
            variant["compatible_components"],
            None,
            f"{source}#/variants/{index}/compatible_components",
        )
        unknown = set(variant["compatible_components"]) - component_keys
        if unknown:
            fail(
                "BPS222_COMPONENT_REFERENCE",
                f"/variants/{index}/compatible_components",
                f"unknown components {sorted(unknown)}",
            )
    participants = {"primary", *component_keys}
    precedence_keys: set[tuple[str, str, str]] = set()
    precedence_graphs: dict[str, dict[str, set[str]]] = {}
    for index, rule in enumerate(value["composition"]["precedence"]):
        key = (rule["surface"], rule["winner"], rule["loser"])
        if key in precedence_keys:
            fail(
                "BPS201_DUPLICATE_ID",
                f"/composition/precedence/{index}",
                f"duplicate precedence {key}",
            )
        precedence_keys.add(key)
        if (
            rule["winner"] not in participants
            or rule["loser"] not in participants
            or rule["winner"] == rule["loser"]
        ):
            fail(
                "BPS223_PRECEDENCE",
                f"/composition/precedence/{index}",
                "winner and loser must be distinct possible participants",
            )
        reverse = (rule["surface"], rule["loser"], rule["winner"])
        if reverse in precedence_keys:
            fail(
                "BPS224_PRECEDENCE_CYCLE",
                f"/composition/precedence/{index}",
                "direct precedence cycle",
            )
        precedence_graphs.setdefault(rule["surface"], {}).setdefault(
            rule["winner"], set()
        ).add(rule["loser"])

    for surface, graph in precedence_graphs.items():
        visiting_nodes: set[str] = set()
        visited_nodes: set[str] = set()

        def visit_precedence(node: str) -> None:
            if node in visiting_nodes:
                fail(
                    "BPS224_PRECEDENCE_CYCLE",
                    "/composition/precedence",
                    f"precedence cycle on surface {surface!r}",
                )
            if node in visited_nodes:
                return
            visiting_nodes.add(node)
            for loser in graph.get(node, set()):
                visit_precedence(loser)
            visiting_nodes.remove(node)
            visited_nodes.add(node)

        for participant in graph:
            visit_precedence(participant)

    queries = value["atlas"]["queries"]
    _unique(queries, "id", f"{source}#/atlas/queries")
    for query_index, query in enumerate(queries):
        _unique(
            query["invariants"],
            "id",
            f"{source}#/atlas/queries/{query_index}/invariants",
        )
        for invariant_index, invariant in enumerate(query["invariants"]):
            location = (
                f"/atlas/queries/{query_index}/invariants/{invariant_index}"
            )
            if (
                invariant["operator"] == "exists"
                and not isinstance(invariant["expected"], bool)
            ):
                fail(
                    "BPS249_INVARIANT_EXPECTED",
                    f"{location}/expected",
                    "exists invariant requires a boolean expected value",
                )
            if invariant["operator"] == "matches":
                if not isinstance(invariant["expected"], str):
                    fail(
                        "BPS249_INVARIANT_EXPECTED",
                        f"{location}/expected",
                        "matches invariant requires a string pattern",
                    )
                try:
                    re.compile(invariant["expected"])
                except re.error as exc:
                    fail(
                        "BPS206_INVALID_PATTERN",
                        f"{location}/expected",
                        str(exc),
                    )

    outputs = value["rendering"]["outputs"]
    _unique(outputs, "id", f"{source}#/rendering/outputs")
    hook_by_id = {hook["id"]: hook for hook in value["hooks"]}
    core_referenced = False
    variant_template_refs: dict[str, bool] = {
        variant["id"]: False for variant in variants
    }
    template_to_variant = {
        variant["template"]["id"]: variant["id"] for variant in variants
    }
    for output_index, output in enumerate(outputs):
        location = f"/rendering/outputs/{output_index}"
        pair = (output["repository_id"], output["integration_surface"])
        if pair not in target_pair_set:
            fail(
                "BPS243_RENDER_TARGET",
                location,
                f"render output target is not declared: {pair}",
            )
        _unique(
            output["variants"],
            None,
            f"{source}#{location}/variants",
        )
        unknown_variants = set(output["variants"]) - variant_ids
        if unknown_variants:
            fail(
                "BPS221_VARIANT_REFERENCE",
                f"{location}/variants",
                f"unknown output variants {sorted(unknown_variants)}",
            )
        _check_expression(
            output["path"], parameter_names, f"{source}#{location}/path"
        )
        render_source = output["source"]
        if (output["operation"] == "delete") != (render_source is None):
            fail(
                "BPS248_RENDER_SOURCE",
                f"{location}/source",
                "delete requires null source; create and update require a source",
            )
        if render_source is None:
            continue
        if render_source["kind"] == "template":
            template_id = render_source["id"]
            if template_id not in template_ids:
                fail(
                    "BPS244_TEMPLATE_REFERENCE",
                    f"{location}/source/id",
                    f"unknown template {template_id!r}",
                )
            if template_id == value["mandatory_core"]["id"]:
                if output["variants"]:
                    fail(
                        "BPS245_MANDATORY_CORE",
                        f"{location}/variants",
                        "mandatory core output cannot be variant-gated",
                    )
                core_referenced = True
            if template_id in template_to_variant:
                variant_id = template_to_variant[template_id]
                if variant_id not in output["variants"]:
                    fail(
                        "BPS246_VARIANT_TEMPLATE",
                        f"{location}/variants",
                        f"variant template {template_id!r} requires variant {variant_id!r}",
                    )
                variant_template_refs[variant_id] = True
        else:
            hook = hook_by_id.get(render_source["id"])
            if hook is None:
                fail(
                    "BPS247_RENDER_HOOK",
                    f"{location}/source/id",
                    f"unknown render hook {render_source['id']!r}",
                )
            if hook["stage"] != "render":
                fail(
                    "BPS247_RENDER_HOOK",
                    f"{location}/source/id",
                    "render output requires a render-stage trusted hook",
                )
    if not core_referenced:
        fail(
            "BPS245_MANDATORY_CORE",
            "/rendering/outputs",
            "one unconditional output must reference mandatory_core",
        )
    missing_variant_templates = sorted(
        variant_id
        for variant_id, referenced in variant_template_refs.items()
        if not referenced
    )
    if missing_variant_templates:
        fail(
            "BPS246_VARIANT_TEMPLATE",
            "/rendering/outputs",
            f"variant templates are not rendered: {missing_variant_templates}",
        )

    validation = value["validation"]
    diagnostics = value["diagnostics"]
    diagnostic_codes = _unique(diagnostics, "code", f"{source}#/diagnostics")
    fixture_ids = _unique(
        validation["fixtures"], "id", f"{source}#/validation/fixtures"
    )
    test_ids = _unique(validation["tests"], "id", f"{source}#/validation/tests")
    formatter_ids = _unique(
        validation["formatters"], "id", f"{source}#/validation/formatters"
    )
    del formatter_ids
    hook_ids = _unique(value["hooks"], "id", f"{source}#/hooks")

    for group_name in ("formatters", "tests", "fixtures"):
        for index, record in enumerate(validation[group_name]):
            repository = record["repository_id"]
            if repository not in repositories:
                fail(
                    "BPS225_REPOSITORY_REFERENCE",
                    f"/validation/{group_name}/{index}/repository_id",
                    f"unknown repository {repository!r}",
                )
            path_field = "paths"
            for path_index, path in enumerate(record.get(path_field, [])):
                directory = group_name == "formatters"
                _safe_path(
                    path,
                    directory=directory,
                    location=(
                        f"{source}#/validation/{group_name}/{index}/"
                        f"{path_field}/{path_index}"
                    ),
                )
                if not _path_is_allowed(path, allowed_by_repository[repository]):
                    fail(
                        "BPS226_PATH_NOT_ALLOWED",
                        f"/validation/{group_name}/{index}/{path_field}/{path_index}",
                        f"path {path!r} is outside target authorization",
                    )
            if group_name == "tests":
                unknown = set(record["fixture_ids"]) - fixture_ids
                if unknown:
                    fail(
                        "BPS227_FIXTURE_REFERENCE",
                        f"/validation/tests/{index}/fixture_ids",
                        f"unknown fixtures {sorted(unknown)}",
                    )

    _unique(validation["gates"], "id", f"{source}#/validation/gates")
    for index, gate in enumerate(validation["gates"]):
        runners = test_ids if gate["runner"] == "test" else hook_ids
        if gate["runner_id"] not in runners:
            fail(
                "BPS228_RUNNER_REFERENCE",
                f"/validation/gates/{index}/runner_id",
                f"unknown {gate['runner']} {gate['runner_id']!r}",
            )
        if gate["runner"] == "trusted-hook":
            hook = next(
                row for row in value["hooks"] if row["id"] == gate["runner_id"]
            )
            if hook["stage"] != "validate":
                fail(
                    "BPS242_GATE_HOOK_STAGE",
                    f"/validation/gates/{index}/runner_id",
                    "a gate can invoke only a validate-stage trusted hook",
                )
        if gate["diagnostic_code"] not in diagnostic_codes:
            fail(
                "BPS229_DIAGNOSTIC_REFERENCE",
                f"/validation/gates/{index}/diagnostic_code",
                f"unknown diagnostic {gate['diagnostic_code']!r}",
            )

    for index, hook in enumerate(value["hooks"]):
        _verify_asset(asset_root, hook, f"{source}#/hooks/{index}")
        if not hook["repositories"] or not hook["allowed_paths"]:
            fail(
                "BPS235_HOOK_SCOPE",
                f"/hooks/{index}",
                "trusted hook requires repositories and allowed_paths",
            )
        _unique(
            hook["repositories"], None, f"{source}#/hooks/{index}/repositories"
        )
        _unique(
            hook["allowed_paths"], None, f"{source}#/hooks/{index}/allowed_paths"
        )
        for repository in hook["repositories"]:
            if repository not in repositories:
                fail(
                    "BPS225_REPOSITORY_REFERENCE",
                    f"/hooks/{index}/repositories",
                    f"unknown repository {repository!r}",
                )
        for path_index, path in enumerate(hook["allowed_paths"]):
            _safe_path(
                path,
                directory=True,
                location=f"{source}#/hooks/{index}/allowed_paths/{path_index}",
            )
            if not all(
                _path_is_allowed(path, allowed_by_repository[repository])
                for repository in hook["repositories"]
            ):
                fail(
                    "BPS226_PATH_NOT_ALLOWED",
                    f"/hooks/{index}/allowed_paths/{path_index}",
                    f"path {path!r} is outside a hook repository authorization",
                )

    _unique(value["reconciliation"], "id", f"{source}#/reconciliation")
    _unique(
        [rule["when"] for rule in value["reconciliation"]],
        "kind",
        f"{source}#/reconciliation",
    )
    for index, rule in enumerate(value["reconciliation"]):
        if rule["diagnostic_code"] not in diagnostic_codes:
            fail(
                "BPS229_DIAGNOSTIC_REFERENCE",
                f"/reconciliation/{index}/diagnostic_code",
                f"unknown diagnostic {rule['diagnostic_code']!r}",
            )
        unknown = set(rule["when"]["fields"]) - parameter_names
        if unknown:
            fail(
                "BPS210_PARAMETER_REFERENCE",
                f"/reconciliation/{index}/when/fields",
                f"unknown parameters {sorted(unknown)}",
            )

    _unique(value["migrations"], "id", f"{source}#/migrations")
    for index, migration in enumerate(value["migrations"]):
        if _semver(migration["from_version"]) >= _semver(value["version"]):
            fail(
                "BPS230_MIGRATION_VERSION",
                f"/migrations/{index}/from_version",
                "migration source must be older than standard version",
            )
        if migration["diagnostic_code"] not in diagnostic_codes:
            fail(
                "BPS229_DIAGNOSTIC_REFERENCE",
                f"/migrations/{index}/diagnostic_code",
                f"unknown diagnostic {migration['diagnostic_code']!r}",
            )
        all_prefixes = set().union(*allowed_by_repository.values())
        for step_index, step in enumerate(migration["steps"]):
            has_value = "value" in step
            if step["operation"] == "remove" and has_value:
                fail(
                    "BPS236_MIGRATION_VALUE",
                    f"/migrations/{index}/steps/{step_index}",
                    "remove migration step forbids value",
                )
            if step["operation"] != "remove" and not has_value:
                fail(
                    "BPS236_MIGRATION_VALUE",
                    f"/migrations/{index}/steps/{step_index}",
                    f"{step['operation']} migration step requires value",
                )
            if step["operation"] == "rename":
                if not isinstance(step["value"], str):
                    fail(
                        "BPS236_MIGRATION_VALUE",
                        f"/migrations/{index}/steps/{step_index}/value",
                        "rename migration value must be a target path",
                    )
                _safe_path(
                    step["value"],
                    directory=False,
                    location=(
                        f"{source}#/migrations/{index}/steps/"
                        f"{step_index}/value"
                    ),
                )
                if not _path_is_allowed(step["value"], all_prefixes):
                    fail(
                        "BPS226_PATH_NOT_ALLOWED",
                        f"/migrations/{index}/steps/{step_index}/value",
                        f"path {step['value']!r} is outside target authorization",
                    )
            _safe_path(
                step["path"],
                directory=False,
                location=f"{source}#/migrations/{index}/steps/{step_index}/path",
            )
            if not _path_is_allowed(step["path"], all_prefixes):
                fail(
                    "BPS226_PATH_NOT_ALLOWED",
                    f"/migrations/{index}/steps/{step_index}/path",
                    f"path {step['path']!r} is outside target authorization",
                )

    domains = value["allocation"]["domains"]
    domain_names = _unique(domains, "name", f"{source}#/allocation/domains")
    referenced_domains = {
        parameter["allocation_domain"]
        for parameter in parameters
        if parameter["class"] == "allocated"
    }
    unknown_domains = referenced_domains - domain_names
    if unknown_domains:
        fail(
            "BPS231_ALLOCATION_DOMAIN",
            "/parameters",
            f"unknown allocation domains {sorted(unknown_domains)}",
        )
    unused_domains = domain_names - referenced_domains
    if unused_domains:
        fail(
            "BPS232_UNUSED_ALLOCATION_DOMAIN",
            "/allocation/domains",
            f"unused allocation domains {sorted(unused_domains)}",
        )
    for index, domain in enumerate(domains):
        _unique(
            domain["evidence"],
            None,
            f"{source}#/allocation/domains/{index}/evidence",
        )
        if domain["mode"] == "blueprints-ledger":
            if domain["authority_id"] != "blueprints-allocation-ledger-v1":
                fail(
                    "BPS237_ALLOCATION_AUTHORITY",
                    f"/allocation/domains/{index}/authority_id",
                    "Blueprints ledger domains require blueprints-allocation-ledger-v1",
                )
            if "pool" not in domain:
                fail(
                    "BPS233_ALLOCATION_POOL",
                    f"/allocation/domains/{index}",
                    "blueprints-ledger requires a pool",
                )
            if domain["pool"]["minimum"] > domain["pool"]["maximum"]:
                fail(
                    "BPS233_ALLOCATION_POOL",
                    f"/allocation/domains/{index}/pool",
                    "pool minimum exceeds maximum",
                )
        elif "pool" in domain:
            fail(
                "BPS233_ALLOCATION_POOL",
                f"/allocation/domains/{index}/pool",
                "existing-authority forbids a Blueprints pool",
            )
        for evidence_index, evidence in enumerate(domain["evidence"]):
            if re.fullmatch(r"SRC-[A-Z0-9-]+@[0-9a-f]{40}", evidence):
                continue
            if re.fullmatch(r"[a-z][a-z0-9-]*:sha256:[0-9a-f]{64}", evidence):
                continue
            fail(
                "BPS238_ALLOCATION_EVIDENCE",
                f"/allocation/domains/{index}/evidence/{evidence_index}",
                "allocation evidence must be a pinned source or content identity",
            )


def _sort_unique_strings(values: list[str]) -> list[str]:
    return sorted(values)


def _compile_value(value: dict[str, Any]) -> dict[str, Any]:
    compiled = json.loads(canonical_json(value))
    compiled["format"] = COMPILED_FORMAT
    compiled["variants"] = sorted(compiled["variants"], key=lambda row: row["id"])
    for variant in compiled["variants"]:
        variant["compatible_components"] = _sort_unique_strings(
            variant["compatible_components"]
        )
    compiled["parameters"] = sorted(
        compiled["parameters"], key=lambda row: row["name"]
    )
    compiled["targets"] = sorted(
        compiled["targets"],
        key=lambda row: (row["repository_id"], row["integration_surface"]),
    )
    for target in compiled["targets"]:
        target["allowed_paths"] = _sort_unique_strings(target["allowed_paths"])
    composition = compiled["composition"]
    composition["allowed"] = sorted(
        composition["allowed"], key=lambda row: row["standard_key"]
    )
    for component in composition["allowed"]:
        for field in ("versions", "primary_variants", "component_variants"):
            component[field] = _sort_unique_strings(component[field])
    composition["precedence"] = sorted(
        composition["precedence"],
        key=lambda row: (row["surface"], row["winner"], row["loser"]),
    )
    compiled["atlas"]["queries"] = sorted(
        compiled["atlas"]["queries"], key=lambda row: row["id"]
    )
    for query in compiled["atlas"]["queries"]:
        query["invariants"] = sorted(
            query["invariants"], key=lambda row: row["id"]
        )
    compiled["rendering"]["outputs"] = sorted(
        compiled["rendering"]["outputs"], key=lambda row: row["id"]
    )
    for output in compiled["rendering"]["outputs"]:
        output["variants"] = _sort_unique_strings(output["variants"])
    validation = compiled["validation"]
    for field in ("formatters", "tests", "fixtures", "gates"):
        validation[field] = sorted(validation[field], key=lambda row: row["id"])
    for formatter in validation["formatters"]:
        formatter["paths"] = _sort_unique_strings(formatter["paths"])
    for test in validation["tests"]:
        test["fixture_ids"] = _sort_unique_strings(test["fixture_ids"])
    for fixture in validation["fixtures"]:
        fixture["paths"] = _sort_unique_strings(fixture["paths"])
    compiled["diagnostics"] = sorted(
        compiled["diagnostics"], key=lambda row: row["code"]
    )
    compiled["reconciliation"] = sorted(
        compiled["reconciliation"], key=lambda row: row["id"]
    )
    compiled["migrations"] = sorted(
        compiled["migrations"],
        key=lambda row: (_semver(row["from_version"]), row["id"]),
    )
    compiled["hooks"] = sorted(compiled["hooks"], key=lambda row: row["id"])
    for hook in compiled["hooks"]:
        hook["repositories"] = _sort_unique_strings(hook["repositories"])
        hook["allowed_paths"] = _sort_unique_strings(hook["allowed_paths"])
    compiled["allocation"]["domains"] = sorted(
        compiled["allocation"]["domains"], key=lambda row: row["name"]
    )
    for domain in compiled["allocation"]["domains"]:
        domain["evidence"] = _sort_unique_strings(domain["evidence"])
    return compiled


def _apply_authoring_defaults(value: dict[str, Any]) -> dict[str, Any]:
    """Insert only defaults explicitly named by the S01 contract."""

    defaulted = json.loads(canonical_json(value))
    for field in (
        "variants",
        "diagnostics",
        "reconciliation",
        "migrations",
        "hooks",
    ):
        defaulted.setdefault(field, [])
    for variant in defaulted.get("variants", []):
        if isinstance(variant, dict):
            variant.setdefault("compatible_components", [])
    composition = defaulted.get("composition")
    if isinstance(composition, dict):
        composition.setdefault("allowed", [])
        composition.setdefault("precedence", [])
    validation = defaulted.get("validation")
    if isinstance(validation, dict):
        for field in ("formatters", "tests", "fixtures", "gates"):
            validation.setdefault(field, [])
    allocation = defaulted.get("allocation")
    if isinstance(allocation, dict):
        allocation.setdefault("domains", [])
    return defaulted


def compile_file(
    path: Path,
    *,
    registry_root: Path,
    asset_root: Path = REPO_ROOT,
    require_admitted_path: bool = True,
) -> tuple[dict[str, Any], bytes]:
    """Compile one source, returning the canonical object and source bytes."""

    supplied_path = path.absolute()
    root = registry_root.resolve()
    if path.suffix != ".yaml":
        raise StandardDiagnostic(
            "BPS300_SOURCE_SUFFIX", str(path), "standard source must end in .yaml"
        )
    if supplied_path.is_symlink():
        raise StandardDiagnostic(
            "BPS301_SOURCE_SYMLINK", str(path), "standard source cannot be a symlink"
        )
    try:
        supplied_relative = supplied_path.relative_to(root)
    except ValueError:
        supplied_relative = None
    if supplied_relative is not None:
        _reject_symlink_components(root, supplied_relative.as_posix(), str(path))
    path = supplied_path.resolve()
    if require_admitted_path:
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise StandardDiagnostic(
                "BPS302_SOURCE_OUTSIDE_REGISTRY",
                str(path),
                f"source is outside {root}",
            ) from exc
    value, source_bytes = _read_yaml(path)
    value = _apply_authoring_defaults(value)
    _validate_schema(value, STANDARD_SCHEMA_PATH, str(path))
    _validate_semantics(value, source=str(path), asset_root=asset_root)
    compiled = _compile_value(value)
    _validate_schema(compiled, STANDARD_SCHEMA_PATH, str(path))
    return compiled, source_bytes


def _scan_sources(root: Path) -> list[Path]:
    if not root.is_dir() or root.is_symlink():
        raise StandardDiagnostic(
            "BPS303_REGISTRY_ROOT", str(root), "registry root is not a directory"
        )
    sources: list[Path] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise StandardDiagnostic(
                "BPS304_REGISTRY_SYMLINK", relative, "registry symlinks are forbidden"
            )
        if path.is_dir():
            continue
        if path.suffix == ".yaml":
            sources.append(path)
        elif relative not in {"README.md", REGISTRY_FILENAME}:
            raise StandardDiagnostic(
                "BPS305_REGISTRY_FILE",
                relative,
                "only .yaml standards, README.md, and registry.json are permitted",
            )
    return sources


def _registry_without_id(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "format": REGISTRY_FORMAT,
        "contract_id": CONTRACT_ID,
        "standards": entries,
    }


def _registry(entries: list[dict[str, Any]]) -> dict[str, Any]:
    value = _registry_without_id(entries)
    value["registry_id"] = (
        "blueprints-standard-registry:sha256:" + _digest_json(value)
    )
    return value


def compile_registry(
    root: Path, *, asset_root: Path = REPO_ROOT
) -> dict[str, Any]:
    """Compile and cross-validate every admitted standard."""

    records: list[tuple[dict[str, Any], dict[str, Any]]] = []
    identities: set[tuple[str, str]] = set()
    for source in _scan_sources(root):
        compiled, source_bytes = compile_file(
            source, registry_root=root, asset_root=asset_root
        )
        identity = (compiled["standard_key"], compiled["version"])
        if identity in identities:
            raise StandardDiagnostic(
                "BPS306_DUPLICATE_VERSION",
                source.relative_to(root).as_posix(),
                f"duplicate standard version {identity[0]}@{identity[1]}",
            )
        identities.add(identity)
        standard_sha256 = _digest_json(compiled)
        entry = {
            "standard_key": compiled["standard_key"],
            "version": compiled["version"],
            "lifecycle": compiled["lifecycle"],
            "kind": compiled["kind"],
            "feature_family": compiled["feature_family"],
            "source_path": source.relative_to(root).as_posix(),
            "source_sha256": _digest_bytes(source_bytes),
            "standard_id": "blueprints-standard:sha256:" + standard_sha256,
            "standard_sha256": standard_sha256,
        }
        records.append((compiled, entry))

    by_identity = {
        (compiled["standard_key"], compiled["version"]): compiled
        for compiled, _entry in records
    }
    domain_definitions: dict[str, str] = {}
    for compiled, entry in records:
        for domain in compiled["allocation"]["domains"]:
            definition = canonical_json(domain)
            old = domain_definitions.get(domain["name"])
            if old is not None and old != definition:
                raise StandardDiagnostic(
                    "BPS314_ALLOCATION_DOMAIN_CONFLICT",
                    entry["source_path"],
                    f"global allocation domain {domain['name']!r} has conflicting definitions",
                )
            domain_definitions[domain["name"]] = definition
    for compiled, entry in records:
        for component_index, component in enumerate(
            compiled["composition"]["allowed"]
        ):
            for version in component["versions"]:
                target = by_identity.get((component["standard_key"], version))
                location = (
                    f"{entry['source_path']}#/composition/allowed/"
                    f"{component_index}/versions"
                )
                if target is None:
                    raise StandardDiagnostic(
                        "BPS307_UNADMITTED_COMPONENT",
                        location,
                        f"missing {component['standard_key']}@{version}",
                    )
                if target["kind"] != "component" or target["lifecycle"] != "active":
                    raise StandardDiagnostic(
                        "BPS308_INVALID_COMPONENT",
                        location,
                        "component version must be active and kind component",
                    )
                unknown = set(component["component_variants"]) - {
                    variant["id"] for variant in target["variants"]
                }
                if unknown:
                    raise StandardDiagnostic(
                        "BPS221_VARIANT_REFERENCE",
                        location,
                        f"unknown component variants {sorted(unknown)}",
                    )

    entries = [
        entry
        for _compiled, entry in sorted(
            records,
            key=lambda record: (
                record[0]["standard_key"],
                _semver(record[0]["version"]),
            ),
        )
    ]
    registry = _registry(entries)
    _validate_schema(registry, REGISTRY_SCHEMA_PATH, str(root / REGISTRY_FILENAME))
    return registry


def validate_allocation_ledger(
    path: Path,
    *,
    registry_root: Path,
    asset_root: Path = REPO_ROOT,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate ledger identity, non-reuse, and admitted domain bindings."""

    value = _load_json(path)
    _validate_schema(value, LEDGER_SCHEMA_PATH, str(path))
    projected = dict(value)
    projected.pop("ledger_id")
    expected_ledger_id = (
        "blueprints-allocation-ledger:sha256:" + _digest_json(projected)
    )
    if value["ledger_id"] != expected_ledger_id:
        raise StandardDiagnostic(
            "BPS315_LEDGER_ID",
            str(path),
            f"ledger identity is not recomputable; expected {expected_ledger_id}",
        )
    if (value["generation"] == 0) != (value["parent_ledger_id"] is None):
        raise StandardDiagnostic(
            "BPS316_LEDGER_PARENT",
            str(path),
            "only generation zero has a null parent_ledger_id",
        )

    reservations = value["reservations"]
    expected_order = sorted(
        reservations,
        key=lambda row: (
            row["domain_name"],
            row["value"],
            row["reservation_id"],
        ),
    )
    if reservations != expected_order:
        raise StandardDiagnostic(
            "BPS317_LEDGER_ORDER",
            str(path),
            "reservations are not in canonical domain/value/identity order",
        )
    seen_ids: set[str] = set()
    seen_values: set[tuple[str, int]] = set()
    for index, reservation in enumerate(reservations):
        projected_reservation = dict(reservation)
        projected_reservation.pop("reservation_id")
        expected_reservation_id = (
            "blueprints-allocation-reservation:sha256:"
            + _digest_json(projected_reservation)
        )
        if reservation["reservation_id"] != expected_reservation_id:
            raise StandardDiagnostic(
                "BPS318_RESERVATION_ID",
                f"{path}#/reservations/{index}",
                f"reservation identity is not recomputable; expected {expected_reservation_id}",
            )
        if reservation["reservation_id"] in seen_ids:
            raise StandardDiagnostic(
                "BPS319_RESERVATION_DUPLICATE",
                f"{path}#/reservations/{index}",
                "duplicate reservation identity",
            )
        seen_ids.add(reservation["reservation_id"])
        domain_value = (reservation["domain_name"], reservation["value"])
        if domain_value in seen_values:
            raise StandardDiagnostic(
                "BPS320_ALLOCATION_REUSE",
                f"{path}#/reservations/{index}",
                f"domain/value already reserved: {domain_value}",
            )
        seen_values.add(domain_value)
        retired_binding = reservation["retired_by_release_id"]
        if (reservation["status"] == "retired") != (retired_binding is not None):
            raise StandardDiagnostic(
                "BPS321_RETIREMENT_BINDING",
                f"{path}#/reservations/{index}",
                "retired rows require, and active rows forbid, retired_by_release_id",
            )

    if registry is None:
        registry = compile_registry(registry_root, asset_root=asset_root)
    standard_entries = {
        entry["standard_id"]: entry for entry in registry["standards"]
    }
    domain_definitions: dict[str, dict[str, Any]] = {}
    domain_standard_ids: dict[str, set[str]] = {}
    for entry in registry["standards"]:
        source = registry_root / entry["source_path"]
        compiled, _source = compile_file(
            source, registry_root=registry_root, asset_root=asset_root
        )
        for domain in compiled["allocation"]["domains"]:
            if domain["mode"] != "blueprints-ledger":
                continue
            domain_definitions[domain["name"]] = domain
            domain_standard_ids.setdefault(domain["name"], set()).add(
                entry["standard_id"]
            )

    for index, reservation in enumerate(reservations):
        location = f"{path}#/reservations/{index}"
        standard_id = reservation["standard_id"]
        if standard_id not in standard_entries:
            raise StandardDiagnostic(
                "BPS322_RESERVATION_STANDARD",
                location,
                f"reservation standard is not admitted: {standard_id}",
            )
        domain = domain_definitions.get(reservation["domain_name"])
        if domain is None:
            raise StandardDiagnostic(
                "BPS323_RESERVATION_DOMAIN",
                location,
                f"reservation domain is not an admitted ledger domain: {reservation['domain_name']}",
            )
        if standard_id not in domain_standard_ids[reservation["domain_name"]]:
            raise StandardDiagnostic(
                "BPS324_RESERVATION_BINDING",
                location,
                "bound standard does not declare the reservation domain",
            )
        if not (
            domain["pool"]["minimum"]
            <= reservation["value"]
            <= domain["pool"]["maximum"]
        ):
            raise StandardDiagnostic(
                "BPS325_RESERVATION_RANGE",
                location,
                f"value {reservation['value']} is outside the admitted pool",
            )
    return value


def validate_ledger_transition(
    previous: dict[str, Any], current: dict[str, Any]
) -> None:
    """Require one append-only or active-to-retired ledger generation."""

    if current["generation"] != previous["generation"] + 1:
        raise StandardDiagnostic(
            "BPS326_LEDGER_GENERATION",
            "/generation",
            "ledger transition must advance exactly one generation",
        )
    if current["parent_ledger_id"] != previous["ledger_id"]:
        raise StandardDiagnostic(
            "BPS327_LEDGER_CAS",
            "/parent_ledger_id",
            "ledger transition does not bind the exact previous identity",
        )
    current_by_value = {
        (row["domain_name"], row["value"]): row
        for row in current["reservations"]
    }
    for old in previous["reservations"]:
        key = (old["domain_name"], old["value"])
        new = current_by_value.get(key)
        if new is None:
            raise StandardDiagnostic(
                "BPS328_RESERVATION_REMOVAL",
                "/reservations",
                f"reservation cannot be removed: {key}",
            )
        if old == new:
            continue
        stable_fields = {
            "domain_name",
            "value",
            "feature_identity",
            "standard_id",
            "request_id",
            "release_id",
        }
        if any(old[field] != new[field] for field in stable_fields):
            raise StandardDiagnostic(
                "BPS329_RESERVATION_MUTATION",
                "/reservations",
                f"reservation binding cannot change: {key}",
            )
        if not (
            old["status"] == "active"
            and old["retired_by_release_id"] is None
            and new["status"] == "retired"
            and new["retired_by_release_id"] is not None
        ):
            raise StandardDiagnostic(
                "BPS329_RESERVATION_MUTATION",
                "/reservations",
                f"only active-to-retired mutation is permitted: {key}",
            )


def _read_locked_registry(root: Path) -> dict[str, Any]:
    path = root / REGISTRY_FILENAME
    if not path.is_file() or path.is_symlink():
        raise StandardDiagnostic(
            "BPS309_REGISTRY_LOCK_MISSING",
            str(path),
            "generated registry lock is missing",
        )
    value = _load_json(path)
    _validate_schema(value, REGISTRY_SCHEMA_PATH, str(path))
    expected = _registry(value["standards"])["registry_id"]
    if value["registry_id"] != expected:
        raise StandardDiagnostic(
            "BPS310_REGISTRY_ID",
            str(path),
            f"registry identity is not recomputable; expected {expected}",
        )
    return value


def check_registry(
    root: Path, *, asset_root: Path = REPO_ROOT
) -> dict[str, Any]:
    compiled = compile_registry(root, asset_root=asset_root)
    locked = _read_locked_registry(root)
    if canonical_json(compiled) != canonical_json(locked):
        raise StandardDiagnostic(
            "BPS311_REGISTRY_STALE",
            str(root / REGISTRY_FILENAME),
            "registry lock does not match admitted YAML; run build",
        )
    return compiled


def build_registry(
    root: Path, *, asset_root: Path = REPO_ROOT
) -> dict[str, Any]:
    compiled = compile_registry(root, asset_root=asset_root)
    path = root / REGISTRY_FILENAME
    if path.exists():
        locked = _read_locked_registry(root)
        current = {
            (entry["standard_key"], entry["version"]): entry
            for entry in compiled["standards"]
        }
        for old in locked["standards"]:
            identity = (old["standard_key"], old["version"])
            new = current.get(identity)
            if new is None:
                raise StandardDiagnostic(
                    "BPS312_IMMUTABLE_REMOVAL",
                    str(path),
                    f"cannot remove immutable standard {identity[0]}@{identity[1]}",
                )
            if old["standard_sha256"] != new["standard_sha256"]:
                raise StandardDiagnostic(
                    "BPS313_IMMUTABLE_VERSION",
                    new["source_path"],
                    f"{identity[0]}@{identity[1]} changed; increment version",
                )
    serialized = canonical_json(compiled).encode("utf-8")
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=".registry.", delete=False
        ) as handle:
            temporary_name = handle.name
            os.fchmod(handle.fileno(), 0o644)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
    return compiled


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile and lock admitted Blueprints standards"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    compile_parser = subparsers.add_parser("compile")
    compile_parser.add_argument("path", type=Path)
    for command in ("compile", "check", "build"):
        target = compile_parser if command == "compile" else subparsers.add_parser(command)
        target.add_argument("--registry-root", type=Path, required=True)
        target.add_argument("--asset-root", type=Path, default=REPO_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "compile":
            compiled, _source = compile_file(
                args.path,
                registry_root=args.registry_root,
                asset_root=args.asset_root,
            )
            sys.stdout.write(canonical_json(compiled))
        elif args.command == "check":
            registry = check_registry(
                args.registry_root, asset_root=args.asset_root
            )
            print(
                f"{registry['registry_id']} standards={len(registry['standards'])}"
            )
        else:
            registry = build_registry(
                args.registry_root, asset_root=args.asset_root
            )
            print(
                f"{registry['registry_id']} standards={len(registry['standards'])}"
            )
    except StandardDiagnostic as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
