"""Bounded material generator expansion and ordered mutation extraction."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
from typing import Any, Mapping, Sequence

from workbench_material_semantics import MaterialSemanticsPolicy

from .declarations import (
    build_source_declarations,
    source_declaration,
    validate_source_declarations,
)
from .lexer import Argument, Call, Token, calls, tokenize
from ._material_declarations_core import (
    MaterialBuilderOperation,
    MaterialDeclarationPolicy,
    _call_row,
    _expected_closure,
)
from .material_expressions import (
    EvaluatedValue,
    SourceImportScope,
    evaluate_expression,
    parse_import_scope,
    resolve_flag_expression,
)
from .model import canonical_bytes, content_id, sha256_bytes


MATERIAL_PROGRAM_FORMAT = "workbench-pack-material-program-v2"
_CONTROL_CALLS = {"catch", "else", "for", "if", "switch", "while"}


@dataclass(frozen=True, slots=True)
class MaterialMutationMethod:
    terminal: str
    kind: str
    property_key: str | None = None

    def __post_init__(self) -> None:
        if not self.terminal or self.kind not in {
            "add-flags",
            "add-property",
            "set-formula",
            "set-presentation",
            "set-property",
        }:
            raise ValueError("material mutation method is invalid")
        if self.kind == "add-property" and not self.property_key:
            raise ValueError("material add-property method needs a property key")

    def to_dict(self) -> dict[str, Any]:
        return {
            "terminal": self.terminal,
            "kind": self.kind,
            "property_key": self.property_key,
        }


@dataclass(frozen=True, slots=True)
class MaterialMutationHelper:
    callee: str
    property_keys: tuple[str, ...]
    fluid_storage_keys: tuple[str, ...] = ()
    fluid_storage_argument: int | None = None
    target_mode: str = "argument"
    target_argument: int = 0

    def __post_init__(self) -> None:
        if not self.callee or not self.property_keys:
            raise ValueError("material mutation helper is invalid")
        if self.target_mode not in {"argument", "receiver"}:
            raise ValueError("material helper target mode is invalid")
        if self.target_argument < 0:
            raise ValueError("material helper target argument is invalid")
        if self.fluid_storage_argument is not None and self.fluid_storage_argument < 0:
            raise ValueError("material helper storage argument is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "callee": self.callee,
            "property_keys": list(self.property_keys),
            "fluid_storage_keys": list(self.fluid_storage_keys),
            "fluid_storage_argument": self.fluid_storage_argument,
            "target_mode": self.target_mode,
            "target_argument": self.target_argument,
        }


@dataclass(frozen=True, slots=True)
class MaterialProgramPolicy:
    policy_id: str
    mutation_methods: tuple[MaterialMutationMethod, ...]
    mutation_helpers: tuple[MaterialMutationHelper, ...] = ()
    lifecycle_overrides: tuple[tuple[str, str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.policy_id:
            raise ValueError("material program policy ID is missing")
        methods = tuple(sorted(self.mutation_methods, key=lambda item: item.terminal))
        if self.mutation_methods != methods or len(
            {item.terminal for item in methods}
        ) != len(methods):
            raise ValueError("material mutation methods must be unique and sorted")
        helpers = tuple(sorted(self.mutation_helpers, key=lambda item: item.callee))
        if self.mutation_helpers != helpers or len(
            {item.callee for item in helpers}
        ) != len(helpers):
            raise ValueError("material mutation helpers must be unique and sorted")
        if self.lifecycle_overrides != tuple(sorted(set(self.lifecycle_overrides))):
            raise ValueError("material lifecycle overrides must be unique and sorted")

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": MATERIAL_PROGRAM_FORMAT,
            "policy_id": self.policy_id,
            "mutation_methods": [method.to_dict() for method in self.mutation_methods],
            "mutation_helpers": [helper.to_dict() for helper in self.mutation_helpers],
            "lifecycle_overrides": [
                {"path": path, "function": function, "stage": stage}
                for path, function, stage in self.lifecycle_overrides
            ],
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical_bytes(self.to_dict())).hexdigest()


@dataclass(frozen=True, slots=True)
class _Function:
    name: str
    parameters: tuple[str, ...]
    declaration_call: Call
    body_token_start: int
    body_token_end: int
    body_start: int
    body_end: int


@dataclass(frozen=True, slots=True)
class _ParsedSource:
    path: str
    source: str
    sha256: str
    tokens: tuple[Token, ...]
    calls: tuple[Call, ...]
    calls_by_token: Mapping[int, Call]
    brace_pairs: Mapping[int, int]
    functions: tuple[_Function, ...]
    imports: SourceImportScope


def _decode_sources(sources: Mapping[str, str | bytes]) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for path, value in sources.items():
        raw = value.encode("utf-8") if isinstance(value, str) else value
        source = value if isinstance(value, str) else value.decode("utf-8")
        result[path] = (source, sha256_bytes(raw))
    return result


def _balanced(tokens: tuple[Token, ...], opening: str, closing: str) -> dict[int, int]:
    result: dict[int, int] = {}
    stack: list[int] = []
    for index, token in enumerate(tokens):
        if token.value == opening:
            stack.append(index)
        elif token.value == closing and stack:
            result[stack.pop()] = index
    return result


def _parameter_name(argument: Argument) -> str | None:
    identifiers = [token.value for token in argument.tokens if token.kind == "identifier"]
    return identifiers[-1] if identifiers else None


def _parse_source(path: str, source: str, sha256: str) -> _ParsedSource:
    tokens = tokenize(source)
    parsed_calls = calls(tokens)
    braces = _balanced(tokens, "{", "}")
    functions: list[_Function] = []
    for call in parsed_calls:
        next_token = call.token_end + 1
        if (
            call.callee != call.terminal
            or call.terminal in _CONTROL_CALLS
            or next_token not in braces
            or tokens[next_token].value != "{"
        ):
            continue
        parameters = tuple(_parameter_name(argument) or "" for argument in call.arguments)
        if not parameters or any(not parameter for parameter in parameters):
            # Zero-argument functions are still useful for mutation lifecycle.
            if call.arguments:
                continue
            parameters = ()
        closing = braces[next_token]
        functions.append(
            _Function(
                call.terminal,
                parameters,
                call,
                next_token + 1,
                closing,
                tokens[next_token].end,
                tokens[closing].start,
            )
        )
    return _ParsedSource(
        path,
        source,
        sha256,
        tokens,
        parsed_calls,
        {call.token_start: call for call in parsed_calls},
        braces,
        tuple(functions),
        parse_import_scope(source),
    )


def _material_variable_candidates(
    feed: Mapping[str, Any],
) -> dict[str, tuple[str, tuple[str, ...]]]:
    candidates: dict[str, set[str]] = {}
    paths: dict[str, set[str]] = {}
    for row in feed["declarations"]:
        attributes = row["attributes"]
        symbol = attributes.get("symbol")
        identity = attributes.get("identity", {})
        resource = identity.get("resource_location")
        if isinstance(symbol, str) and isinstance(resource, str):
            candidates.setdefault(symbol, set()).add(resource)
            path = row["provenance"]["source"].get("path")
            if isinstance(path, str):
                paths.setdefault(symbol, set()).add(path)
    return {
        symbol: (next(iter(resources)), tuple(sorted(paths.get(symbol, ()))))
        for symbol, resources in candidates.items()
        if len(resources) == 1
    }


def _material_variables_for_source(
    candidates: Mapping[str, tuple[str, tuple[str, ...]]],
    parsed: _ParsedSource,
) -> dict[str, EvaluatedValue]:
    static_wildcards = set(parsed.imports.static_wildcards)
    explicit = dict(parsed.imports.explicit_static_imports)
    imports_susy_materials = "material.SuSyMaterials" in static_wildcards
    result: dict[str, EvaluatedValue] = {}
    for symbol, (resource, declaration_paths) in candidates.items():
        explicitly_imported = explicit.get(symbol) == f"material.SuSyMaterials.{symbol}"
        if not (
            imports_susy_materials
            or explicitly_imported
            or parsed.path in declaration_paths
        ):
            continue
        result[symbol] = EvaluatedValue.exact(
            "material-reference",
            resource,
            f"material-symbol:material.SuSyMaterials.{symbol}",
        )
    return result


def _enclosing_function(
    parsed: _ParsedSource, start: int
) -> _Function | None:
    matches = [
        function
        for function in parsed.functions
        if function.body_start <= start < function.body_end
    ]
    return min(matches, key=lambda item: item.body_end - item.body_start) if matches else None


def _call_sites(parsed: _ParsedSource, function: _Function) -> tuple[Call, ...]:
    return tuple(
        call
        for call in parsed.calls
        if call.terminal == function.name
        and call.start != function.declaration_call.start
        and len(call.arguments) == len(function.parameters)
        and not (function.body_start <= call.start < function.body_end)
    )


def _condition_ranges(
    parsed: _ParsedSource, function: _Function
) -> tuple[tuple[int, int, str], ...]:
    rows: list[tuple[int, int, str]] = []
    for call in parsed.calls:
        brace = call.token_end + 1
        if (
            call.terminal == "if"
            and len(call.arguments) == 1
            and brace in parsed.brace_pairs
            and function.body_start <= call.start < function.body_end
        ):
            closing = parsed.brace_pairs[brace]
            rows.append(
                (
                    parsed.tokens[brace].end,
                    parsed.tokens[closing].start,
                    call.arguments[0].expression,
                )
            )
    return tuple(rows)


def _guard_state(
    position: int,
    ranges: Sequence[tuple[int, int, str]],
    semantics: MaterialSemanticsPolicy,
    imports: SourceImportScope,
    variables: Mapping[str, EvaluatedValue],
) -> tuple[str, list[dict[str, Any]]]:
    trace: list[dict[str, Any]] = []
    unknown = False
    for start, end, expression in ranges:
        if not start <= position < end:
            continue
        value = evaluate_expression(expression, semantics, imports, variables)
        passed = (
            bool(value.value)
            if value.state == "exact" and value.value_type == "boolean"
            else None
        )
        trace.append(
            {"expression": expression, "evaluated": value.to_dict(), "passed": passed}
        )
        if passed is False:
            return "excluded", trace
        if passed is None:
            unknown = True
    return ("unknown" if unknown else "included"), trace


def _evaluate_registry_expression(
    expression: str,
    declaration_policy: MaterialDeclarationPolicy,
    semantics: MaterialSemanticsPolicy,
    imports: SourceImportScope,
    variables: Mapping[str, EvaluatedValue],
) -> tuple[str | None, str | None, EvaluatedValue]:
    tokens = tokenize(expression)
    helper_namespaces = dict(declaration_policy.registry_helpers)
    for call in calls(tokens):
        if call.callee not in helper_namespaces or len(call.arguments) != 1:
            continue
        evaluated = evaluate_expression(
            call.arguments[0].expression, semantics, imports, variables
        )
        if evaluated.state == "exact" and evaluated.value_type == "string":
            local_name = str(evaluated.value)
            if declaration_policy.registry_name_normalization == "lowercase":
                local_name = local_name.lower()
            return (
                local_name,
                f"{helper_namespaces[call.callee]}:{local_name}",
                evaluated,
            )
        return None, None, evaluated
    evaluated = evaluate_expression(expression, semantics, imports, variables)
    if evaluated.state == "exact" and evaluated.value_type == "string":
        raw = str(evaluated.value)
        if declaration_policy.registry_name_normalization == "lowercase":
            raw = raw.lower()
        if ":" in raw:
            _, _, local_name = raw.partition(":")
            return local_name, raw, evaluated
    return None, None, evaluated


def _builder_followup_calls(
    parsed: _ParsedSource,
    function: _Function,
    symbol: str | None,
    after: int,
) -> tuple[Call, ...]:
    if not symbol:
        return ()
    prefix = f"{symbol}."
    return tuple(
        call
        for call in parsed.calls
        if after < call.start < function.body_end and call.callee.startswith(prefix)
    )


def _operation_effect(
    attributes: dict[str, Any],
    operation: MaterialBuilderOperation,
    call_row: dict[str, Any],
    operation_index: int,
) -> None:
    core = attributes["declared_material_core"]
    declared = set(core["declared_property_keys"])
    declared.update(operation.property_keys)
    core["declared_property_keys"] = sorted(declared)
    if operation.kind == "fluid-form":
        storage_key = operation.fluid_storage_key
        if storage_key == "from-arguments":
            storage_key = None
        attributes["form_lens"]["fluid_storage_relationships"].append(
            {
                "storage_key": storage_key,
                "operation_index": operation_index,
                "terminal": operation.terminal,
                "span": call_row["span"],
                "arguments": [argument["expression"] for argument in call_row["arguments"]],
            }
        )


def _expanded_registration(
    template: Mapping[str, Any],
    parsed: _ParsedSource,
    function: _Function,
    call_site: Call,
    call_ordinal: int,
    declaration_policy: MaterialDeclarationPolicy,
    semantics: MaterialSemanticsPolicy,
    global_variables: Mapping[str, EvaluatedValue],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    variables = dict(global_variables)
    argument_values: list[dict[str, Any]] = []
    for parameter, argument in zip(function.parameters, call_site.arguments):
        value = evaluate_expression(
            argument.expression, semantics, parsed.imports, global_variables
        )
        variables[parameter] = value
        argument_values.append(
            {
                "parameter": parameter,
                "expression": argument.expression,
                "value": value.to_dict(),
            }
        )
    attributes = deepcopy(template["attributes"])
    template_start = template["provenance"]["source"]["span"]["start"]
    ranges = _condition_ranges(parsed, function)
    guard, guard_trace = _guard_state(
        template_start, ranges, semantics, parsed.imports, variables
    )
    if guard == "excluded":
        return None, []
    identity = attributes["identity"]
    numeric = evaluate_expression(
        identity["numeric_id_expression"], semantics, parsed.imports, variables
    )
    registry_name, resource, registry_value = _evaluate_registry_expression(
        identity["registry_expression"],
        declaration_policy,
        semantics,
        parsed.imports,
        variables,
    )
    identity.update(
        {
            "numeric_id": numeric.value
            if numeric.state == "exact" and numeric.value_type == "int"
            else None,
            "numeric_id_state": "exact-specialized"
            if numeric.state == "exact" and numeric.value_type == "int"
            else "unresolved-expression",
            "registry_name": registry_name,
            "registry_name_state": "exact-specialized"
            if registry_name is not None
            else "unresolved-expression",
            "namespace": resource.partition(":")[0] if resource else None,
            "resource_location": resource,
        }
    )
    core = attributes["declared_material_core"]
    core["identity"] = identity
    uncertainties = set(attributes["uncertainties"])
    if identity["numeric_id"] is not None:
        uncertainties.discard("identity:numeric-id-unresolved")
    if resource is not None:
        uncertainties.discard("identity:registry-name-unresolved")
    if guard == "unknown":
        uncertainties.add("metaprogramming:guard-unresolved")

    # GroovyMaterialBuilderExpansion treats a Material argument as amount one.
    for operation in attributes["operations"]:
        if operation["terminal"] != "components" or len(operation["arguments"]) != 1:
            continue
        component = evaluate_expression(
            operation["arguments"][0]["expression"],
            semantics,
            parsed.imports,
            variables,
        )
        if component.state == "exact" and component.value_type == "material-reference":
            core["composition"] = {
                "basis": "composite",
                "components": [
                    {
                        "ordinal": 0,
                        "material_expression": operation["arguments"][0]["expression"],
                        "material_resource_location": component.value,
                        "amount": 1,
                        "material_span": operation["arguments"][0]["span"],
                        "amount_span": None,
                    }
                ],
                "elements": [],
            }
            uncertainties.discard("composition:overload-unresolved")

    operation_map = {
        operation.terminal: operation for operation in declaration_policy.operations
    }
    followups = _builder_followup_calls(
        parsed,
        function,
        attributes.get("symbol"),
        template["provenance"]["source"]["span"]["end"],
    )
    for followup in followups:
        operation = operation_map.get(followup.terminal)
        if operation is None:
            continue
        followup_guard, followup_trace = _guard_state(
            followup.start, ranges, semantics, parsed.imports, variables
        )
        if followup_guard == "excluded":
            continue
        if followup_guard == "unknown":
            uncertainties.add("metaprogramming:guard-unresolved")
        row = _call_row(parsed.source, followup, operation)
        row["specialization_guard"] = followup_trace
        attributes["operations"].append(row)
        _operation_effect(attributes, operation, row, len(attributes["operations"]) - 1)
        if operation.kind == "terminator":
            attributes["builder_complete"] = True
            uncertainties.discard("builder:incomplete-chain")

    declared_properties = set(core["declared_property_keys"])
    declared_flags = set(core["declared_flag_names"])
    understood = not any(value.startswith("operation:unsupported:") for value in uncertainties)
    closure = _expected_closure(
        declared_properties,
        declared_flags,
        declaration_policy,
        complete=attributes["builder_complete"],
        understood=understood,
        flags_understood=not core["unresolved_flag_expressions"],
    )
    issues = set(attributes["issues"])
    issues.update(closure.pop("issues"))
    core["expected_verified_closure"] = closure
    attributes["issues"] = sorted(issues)
    attributes["uncertainties"] = sorted(uncertainties)
    attributes["normalization_status"] = (
        "exact-static" if not issues and not uncertainties else "frontier-static"
    )
    attributes["source_role"] = "expanded-registration"
    attributes["symbol"] = _assigned_symbol(parsed.tokens, call_site)
    core["symbol"] = attributes["symbol"]
    attributes["expansion"] = {
        "state": "exact-specialization" if guard == "included" else "frontier-specialization",
        "function": function.name,
        "call_ordinal": call_ordinal,
        "arguments": argument_values,
        "guard": guard_trace,
        "numeric_id": numeric.to_dict(),
        "registry_name": registry_value.to_dict(),
        "template_source_declaration_id": template["source_declaration_id"],
    }
    attributes["declared_material_core_sha256"] = hashlib.sha256(
        canonical_bytes(core)
    ).hexdigest()
    source_occurrence = (
        f"{parsed.path}#char={call_site.start}/"
        f"template={template_start}/ordinal={call_ordinal}"
    )
    provenance = deepcopy(template["provenance"])
    provenance["source_occurrence"] = source_occurrence
    provenance["template"] = {
        "source_declaration_id": template["source_declaration_id"],
        "path": parsed.path,
        "span": template["provenance"]["source"]["span"],
    }
    provenance["call_site"] = {
        "path": parsed.path,
        "sha256": parsed.sha256,
        "span": _span(call_site),
        "source_text": parsed.source[call_site.start : call_site.end],
    }
    descriptor_key = (
        {"resource_location": resource}
        if resource is not None
        else {"source_occurrence": source_occurrence}
    )
    registration = source_declaration(
        semantic_descriptor={
            "domain": "material",
            "kind": "material-registration",
            "key": descriptor_key,
        },
        attributes=attributes,
        lifecycle=template["lifecycle"],
        provenance=provenance,
    )
    return registration, _post_call_mutations(
        registration,
        parsed,
        call_site,
        declaration_policy,
        semantics,
        variables,
    )


def _assigned_symbol(tokens: tuple[Token, ...], call: Call) -> str | None:
    cursor = call.token_start - 1
    if cursor >= 1 and tokens[cursor].value == "=" and tokens[cursor - 1].kind == "identifier":
        return tokens[cursor - 1].value
    return None


def _span(call: Call) -> dict[str, int]:
    return {
        "start": call.start,
        "end": call.end,
        "line": call.line,
        "column": call.column,
    }


def _post_call_mutations(
    registration: Mapping[str, Any],
    parsed: _ParsedSource,
    call_site: Call,
    declaration_policy: MaterialDeclarationPolicy,
    semantics: MaterialSemanticsPolicy,
    variables: Mapping[str, EvaluatedValue],
) -> list[dict[str, Any]]:
    resource = registration["attributes"]["identity"].get("resource_location")
    if not isinstance(resource, str):
        return []
    result: list[dict[str, Any]] = []
    cursor = call_site.token_end + 1
    operation_map = {
        operation.terminal: operation for operation in declaration_policy.operations
    }
    while cursor + 1 < len(parsed.tokens) and parsed.tokens[cursor].value in {".", "?."}:
        chained = parsed.calls_by_token.get(cursor + 1)
        if chained is None:
            break
        if chained.terminal == "addFlags":
            resolutions = [
                resolve_flag_expression(argument.expression, semantics, parsed.imports)
                for argument in chained.arguments
            ]
            names = sorted(
                {
                    name
                    for resolution in resolutions
                    if resolution.state == "exact"
                    for name in resolution.flag_names
                }
            )
            result.append(
                _mutation_record(
                    parsed,
                    chained,
                    resource,
                    "add-flags",
                    {
                        "must_include_flags": _flag_closure(names, semantics),
                        "flag_resolution": [item.to_dict() for item in resolutions],
                    },
                    "material",
                    extra_provenance={
                        "expanded_registration_id": registration["source_declaration_id"]
                    },
                    resolution_state=(
                        "exact-static"
                        if all(item.state == "exact" for item in resolutions)
                        else "frontier-static"
                    ),
                    uncertainties=(
                        ()
                        if all(item.state == "exact" for item in resolutions)
                        else ("flag:expression-unresolved",)
                    ),
                )
            )
        elif chained.terminal in {"addIngot", "addDust", "addGem", "addOre"}:
            property_key = chained.terminal[3:].lower()
            result.append(
                _mutation_record(
                    parsed,
                    chained,
                    resource,
                    "add-property",
                    {"must_include_properties": [property_key]},
                    "material",
                    extra_provenance={
                        "expanded_registration_id": registration["source_declaration_id"]
                    },
                )
            )
        elif chained.terminal in operation_map:
            # A builder method cannot legally occur after the helper returned a Material.
            break
        cursor = chained.token_end + 1
    return result


def _flag_closure(
    names: Sequence[str], semantics: MaterialSemanticsPolicy
) -> list[str]:
    dependencies = dict(semantics.runtime_policy().flag_dependencies)
    result = set(names)
    changed = True
    while changed:
        changed = False
        for name in tuple(result):
            for dependency in dependencies.get(name, ()):
                if dependency not in result:
                    result.add(dependency)
                    changed = True
    return sorted(result)


def _property_key(expression: str) -> str | None:
    tokens = tokenize(expression)
    if not tokens or any(
        token.kind != "identifier" and token.value not in {".", "?."}
        for token in tokens
    ):
        return None
    identifiers = [token.value for token in tokens if token.kind == "identifier"]
    if len(tokens) != len(identifiers) * 2 - 1:
        return None
    terminal = identifiers[-1]
    # GCYM's Java field is ALLOY_BLAST, but the frozen MaterialProperties key
    # registered at runtime is blast_alloy.  Source symbols are normalized to
    # registry keys, not mechanically lower-cased field names.
    mapping = {"ALLOY_BLAST": "blast_alloy", "FIBER": "fiber"}
    return mapping.get(terminal, terminal.lower())


def _fluid_storage_key(expression: str) -> str | None:
    tokens = tokenize(expression)
    if not tokens or any(
        token.kind != "identifier" and token.value not in {".", "?."}
        for token in tokens
    ):
        return None
    identifiers = [token.value.lower() for token in tokens if token.kind == "identifier"]
    if len(tokens) != len(identifiers) * 2 - 1:
        return None
    return identifiers[-1] if identifiers else None


def _lifecycle_stage(
    policy: MaterialProgramPolicy,
    path: str,
    function: _Function | None,
    default: str,
) -> str:
    name = function.name if function else ""
    for expected_path, expected_function, stage in policy.lifecycle_overrides:
        if path.endswith(expected_path) and name == expected_function:
            return stage
    return default


def _mutation_record(
    parsed: _ParsedSource,
    call: Call,
    resource: str,
    operation: str,
    constraints: Mapping[str, Any],
    stage: str,
    *,
    extra_provenance: Mapping[str, Any] | None = None,
    resolution_state: str = "exact-static",
    uncertainties: Sequence[str] = (),
) -> dict[str, Any]:
    occurrence = f"{parsed.path}#char={call.start}"
    return source_declaration(
        semantic_descriptor={
            "domain": "material",
            "kind": "material-mutation",
            "key": {
                "resource_location": resource,
                "source_occurrence": occurrence,
            },
        },
        attributes={
            "source_role": "material-mutation",
            "target": {"resource_location": resource},
            "operation": operation,
            "constraints": dict(constraints),
            "resolution_state": resolution_state,
            "uncertainties": sorted(set(uncertainties)),
        },
        lifecycle={"stage": stage},
        provenance={
            "authority": "Pack Program Studio",
            "source": {
                "path": parsed.path,
                "sha256": parsed.sha256,
                "span": _span(call),
                "source_text": parsed.source[call.start : call.end],
            },
            "source_occurrence": occurrence,
            **({} if extra_provenance is None else dict(extra_provenance)),
        },
    )


def _unresolved_mutation_record(
    parsed: _ParsedSource,
    call: Call,
    target_expression: str,
    operation: str,
    constraints: Mapping[str, Any],
    stage: str,
    *uncertainties: str,
) -> dict[str, Any]:
    occurrence = f"{parsed.path}#char={call.start}"
    return source_declaration(
        semantic_descriptor={
            "domain": "material",
            "kind": "material-mutation",
            "key": {"source_occurrence": occurrence},
        },
        attributes={
            "source_role": "material-mutation",
            "target": {
                "resource_location": None,
                "expression": target_expression,
            },
            "operation": operation,
            "constraints": dict(constraints),
            "resolution_state": "frontier-static",
            "uncertainties": sorted(set(uncertainties)),
        },
        lifecycle={"stage": stage},
        provenance={
            "authority": "Pack Program Studio",
            "source": {
                "path": parsed.path,
                "sha256": parsed.sha256,
                "span": _span(call),
                "source_text": parsed.source[call.start : call.end],
            },
            "source_occurrence": occurrence,
        },
    )


def _direct_mutations(
    parsed: _ParsedSource,
    program_policy: MaterialProgramPolicy,
    declaration_policy: MaterialDeclarationPolicy,
    semantics: MaterialSemanticsPolicy,
    variables: Mapping[str, EvaluatedValue],
) -> list[dict[str, Any]]:
    methods = {method.terminal: method for method in program_policy.mutation_methods}
    helpers = {helper.callee: helper for helper in program_policy.mutation_helpers}
    result: list[dict[str, Any]] = []
    declaration_starts = {
        function.declaration_call.start for function in parsed.functions
    }
    for call in parsed.calls:
        function = _enclosing_function(parsed, call.start)
        if call.start in declaration_starts or (
            function is not None and function.name in helpers
        ):
            # Profile-admitted helper bodies are summarized at their call sites.
            continue
        default_stage = "classes" if "/classes/" in f"/{parsed.path}" else "material"
        stage = _lifecycle_stage(
            program_policy, parsed.path, function, default_stage
        )
        if call.terminal in methods and "." in call.callee:
            receiver = call.callee.rsplit(".", 1)[0]
            target = evaluate_expression(
                receiver,
                semantics,
                parsed.imports,
                variables,
            )
            if target.state != "exact" or target.value_type != "material-reference":
                result.append(
                    _unresolved_mutation_record(
                        parsed,
                        call,
                        receiver,
                        methods[call.terminal].kind,
                        {"target_evaluation": target.to_dict()},
                        stage,
                        "mutation:target-unresolved",
                    )
                )
                continue
            method = methods[call.terminal]
            constraints: dict[str, Any] = {}
            uncertainties: list[str] = []
            if method.kind == "add-flags":
                resolutions = [
                    resolve_flag_expression(argument.expression, semantics, parsed.imports)
                    for argument in call.arguments
                ]
                names = sorted(
                    {
                        name
                        for resolution in resolutions
                        if resolution.state == "exact"
                        for name in resolution.flag_names
                    }
                )
                constraints = {
                    "must_include_flags": _flag_closure(names, semantics),
                    "flag_resolution": [item.to_dict() for item in resolutions],
                }
                if any(resolution.state != "exact" for resolution in resolutions):
                    uncertainties.append("flag:expression-unresolved")
            elif method.kind == "set-property":
                if not call.arguments or (key := _property_key(call.arguments[0].expression)) is None:
                    constraints = {
                        "property_key_expression": (
                            call.arguments[0].expression if call.arguments else None
                        ),
                        "value_expression": (
                            call.arguments[1].expression
                            if len(call.arguments) > 1
                            else None
                        ),
                    }
                    uncertainties.append("property:key-unresolved")
                else:
                    closure = _expected_closure(
                        {key},
                        set(),
                        declaration_policy,
                        complete=True,
                        understood=True,
                        flags_understood=True,
                    )
                    closure.pop("issues")
                    constraints = {
                        "must_include_properties": closure["properties"]["keys"],
                        "property_key": key,
                        "value_expression": call.arguments[1].expression
                        if len(call.arguments) > 1
                        else None,
                    }
            elif method.kind == "add-property":
                constraints = {"must_include_properties": [method.property_key]}
            elif method.kind == "set-formula":
                value = (
                    evaluate_expression(
                        call.arguments[0].expression,
                        semantics,
                        parsed.imports,
                        variables,
                    )
                    if call.arguments
                    else EvaluatedValue.unknown("formula-argument-missing")
                )
                constraints = {"value_evaluation": value.to_dict()}
                if value.state == "exact" and value.value_type == "string":
                    constraints["chemical_formula_equals"] = value.value
                else:
                    uncertainties.append("formula:value-unresolved")
            elif method.kind == "set-presentation":
                value = (
                    evaluate_expression(
                        call.arguments[0].expression,
                        semantics,
                        parsed.imports,
                        variables,
                    )
                    if call.arguments
                    else EvaluatedValue.unknown("presentation-argument-missing")
                )
                constraints = {
                    "presentation_field": method.property_key,
                    "value_evaluation": value.to_dict(),
                }
                if value.state == "exact":
                    constraints["equals"] = value.value
                else:
                    uncertainties.append("presentation:value-unresolved")
            result.append(
                _mutation_record(
                    parsed,
                    call,
                    str(target.value),
                    method.kind,
                    constraints,
                    stage,
                    resolution_state=(
                        "frontier-static" if uncertainties else "exact-static"
                    ),
                    uncertainties=uncertainties,
                )
            )
        else:
            helper = helpers.get(call.callee)
            if helper is None and "." in call.callee:
                receiver_helper = helpers.get(call.terminal)
                if (
                    receiver_helper is not None
                    and receiver_helper.target_mode == "receiver"
                ):
                    helper = receiver_helper
            if helper is None:
                continue
            if helper.target_mode == "receiver":
                target_expression = call.callee.rsplit(".", 1)[0]
            else:
                target_expression = (
                    call.arguments[helper.target_argument].expression
                    if helper.target_argument < len(call.arguments)
                    else ""
                )
            target = (
                evaluate_expression(
                    target_expression,
                    semantics,
                    parsed.imports,
                    variables,
                )
                if target_expression
                else EvaluatedValue.unknown("helper-target-missing")
            )
            if target.state != "exact" or target.value_type != "material-reference":
                result.append(
                    _unresolved_mutation_record(
                        parsed,
                        call,
                        target_expression,
                        "helper-mutation",
                        {
                            "helper": helper.callee,
                            "target_evaluation": target.to_dict(),
                        },
                        stage,
                        "mutation:target-unresolved",
                    )
                )
                continue
            storage_keys = list(helper.fluid_storage_keys)
            uncertainties = []
            if (
                helper.fluid_storage_argument is not None
                and helper.fluid_storage_argument < len(call.arguments)
            ):
                dynamic = _fluid_storage_key(
                    call.arguments[helper.fluid_storage_argument].expression
                )
                if dynamic is not None:
                    storage_keys.append(dynamic)
                else:
                    uncertainties.append("form:storage-key-unresolved")
            elif helper.fluid_storage_argument is not None:
                uncertainties.append("form:storage-key-argument-missing")
            result.append(
                _mutation_record(
                    parsed,
                    call,
                    str(target.value),
                    "helper-mutation",
                    {
                        "must_include_properties": list(helper.property_keys),
                        "requested_fluid_storage_keys": sorted(set(storage_keys)),
                        "helper": helper.callee,
                    },
                    stage,
                    resolution_state=(
                        "frontier-static" if uncertainties else "exact-static"
                    ),
                    uncertainties=uncertainties,
                )
            )
    return result


def _program_identity_collisions(
    declarations: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    indexes: dict[tuple[str, Any], list[int]] = {}
    result = [deepcopy(row) for row in declarations]
    for index, row in enumerate(result):
        attributes = row["attributes"]
        if attributes.get("source_role") not in {
            "direct-registration",
            "expanded-registration",
        }:
            continue
        identity = attributes.get("identity", {})
        for kind, value in (
            ("numeric-id", identity.get("numeric_id")),
            ("resource-location", identity.get("resource_location")),
        ):
            if value is not None:
                indexes.setdefault((kind, value), []).append(index)
    affected: set[int] = set()
    for (kind, value), indexes_for_value in sorted(
        indexes.items(), key=lambda item: repr(item[0])
    ):
        if len(indexes_for_value) < 2:
            continue
        occurrences = []
        for index in indexes_for_value:
            row = result[index]
            source = row["provenance"]["source"]
            occurrences.append(
                {
                    "source_occurrence": row["provenance"]["source_occurrence"],
                    "path": source["path"],
                    "start": source["span"]["start"],
                    "symbol": row["attributes"].get("symbol"),
                }
            )
        collision = {
            "identity_kind": kind,
            "value": value,
            "evidence_state": "static-candidate",
            "occurrences": occurrences,
        }
        for index in indexes_for_value:
            attributes = result[index]["attributes"]
            existing = attributes.setdefault("collision_candidates", [])
            if collision not in existing:
                existing.append(collision)
            issues = set(attributes.get("issues", []))
            issues.add(f"identity:duplicate-{kind}")
            attributes["issues"] = sorted(issues)
            attributes["normalization_status"] = "frontier-static"
            affected.add(index)
    replacements: dict[str, str] = {}
    for index in affected:
        row = result[index]
        old_id = row["source_declaration_id"]
        rebuilt = source_declaration(
            semantic_descriptor=row["semantic_descriptor"],
            attributes=row["attributes"],
            lifecycle=row["lifecycle"],
            provenance=row["provenance"],
            source_effect_id=row["source_effect_id"],
        )
        result[index] = rebuilt
        replacements[old_id] = rebuilt["source_declaration_id"]
    return result, replacements


def _rebind_expanded_mutations(
    mutations: Sequence[Mapping[str, Any]],
    replacements: Mapping[str, str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source in mutations:
        row = deepcopy(source)
        registration_id = row["provenance"].get("expanded_registration_id")
        if registration_id in replacements:
            row["provenance"]["expanded_registration_id"] = replacements[
                registration_id
            ]
            row = source_declaration(
                semantic_descriptor=row["semantic_descriptor"],
                attributes=row["attributes"],
                lifecycle=row["lifecycle"],
                provenance=row["provenance"],
                source_effect_id=row["source_effect_id"],
            )
        result.append(row)
    return result


def normalize_material_program_v2(
    sources: Mapping[str, str | bytes],
    declaration_feed: Mapping[str, Any],
    declaration_policy: MaterialDeclarationPolicy,
    semantics: MaterialSemanticsPolicy,
    program_policy: MaterialProgramPolicy,
) -> dict[str, Any]:
    """Expand finite generator calls and emit ordered material mutations."""

    feed = validate_source_declarations(declaration_feed)
    if feed["binding"]["pack_profile_id"] != semantics.pack_profile_id:
        raise ValueError("material program source and semantic profiles differ")
    decoded = _decode_sources(sources)
    parsed = {
        path: _parse_source(path, source, sha256)
        for path, (source, sha256) in decoded.items()
    }
    variable_candidates = _material_variable_candidates(feed)
    variables_by_path = {
        path: _material_variables_for_source(variable_candidates, source)
        for path, source in parsed.items()
    }
    template_ids: set[str] = set()
    expanded: list[dict[str, Any]] = []
    post_mutations: list[dict[str, Any]] = []
    for row in feed["declarations"]:
        path = row["provenance"]["source"]["path"]
        source = parsed[path]
        start = row["provenance"]["source"]["span"]["start"]
        function = _enclosing_function(source, start)
        identity = row["attributes"].get("identity", {})
        if function is None or (
            identity.get("numeric_id_state") == "exact-literal"
            and identity.get("registry_name_state") == "exact-literal"
        ):
            continue
        sites = _call_sites(source, function)
        if not sites:
            continue
        template_ids.add(row["source_declaration_id"])
        ordinal = 0
        for site in sites:
            registration, mutations = _expanded_registration(
                row,
                source,
                function,
                site,
                ordinal,
                declaration_policy,
                semantics,
                variables_by_path[path],
            )
            if registration is not None:
                expanded.append(registration)
                post_mutations.extend(mutations)
                ordinal += 1

    # Exact specializations introduce source symbols (for example
    # HighPurityGermanium) that later lifecycle files may import and mutate.
    expanded_variable_candidates = _material_variable_candidates(
        {"declarations": [*feed["declarations"], *expanded]}
    )
    variables_by_path = {
        path: _material_variables_for_source(expanded_variable_candidates, source)
        for path, source in parsed.items()
    }

    declarations: list[dict[str, Any]] = []
    for row in feed["declarations"]:
        attributes = deepcopy(row["attributes"])
        is_template = row["source_declaration_id"] in template_ids
        attributes["source_role"] = (
            "material-generator-template" if is_template else "direct-registration"
        )
        descriptor = deepcopy(row["semantic_descriptor"])
        descriptor["kind"] = (
            "material-generator-template" if is_template else "material-registration"
        )
        declarations.append(
            source_declaration(
                semantic_descriptor=descriptor,
                attributes=attributes,
                lifecycle=row["lifecycle"],
                provenance=row["provenance"],
                source_effect_id=row["source_effect_id"],
            )
        )
    declarations.extend(expanded)
    declarations, registration_replacements = _program_identity_collisions(
        declarations
    )
    declarations.extend(
        _rebind_expanded_mutations(post_mutations, registration_replacements)
    )
    for source in parsed.values():
        declarations.extend(
            _direct_mutations(
                source,
                program_policy,
                declaration_policy,
                semantics,
                variables_by_path[source.path],
            )
        )
    declarations.sort(
        key=lambda row: (
            row["provenance"].get("source", {}).get("path", "").encode("utf-8"),
            row["provenance"].get("source", {}).get("span", {}).get("start", -1),
            row["semantic_descriptor"]["kind"],
            row["source_declaration_id"],
        )
    )
    program_id = content_id(
        "workbench-pack-material-program-v2:sha256:",
        {
            "source_declaration_set_id": feed["declaration_set_id"],
            "material_semantics_policy_sha256": semantics.sha256,
            "material_program_policy_sha256": program_policy.sha256,
        },
    )
    return build_source_declarations(
        program_id=program_id,
        pack_profile_id=feed["binding"]["pack_profile_id"],
        platform_profile_id=feed["binding"]["platform_profile_id"],
        source_sha256=feed["binding"]["source_sha256"],
        declarations=declarations,
        source_kind=MATERIAL_PROGRAM_FORMAT,
    )


__all__ = [
    "MATERIAL_PROGRAM_FORMAT",
    "MaterialMutationHelper",
    "MaterialMutationMethod",
    "MaterialProgramPolicy",
    "normalize_material_program_v2",
]
