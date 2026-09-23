"""Private material-declaration parsing shared by the V2 projections.

This module is intentionally source-side.  It preserves lexical operations and
computes a policy-bound *expected* verification closure, but it never claims
that Groovy executed or that a material reached a runtime registry.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from .declarations import build_source_declarations, source_declaration
from .lexer import Argument, Call, Token, calls, tokenize
from .model import PackProgramError, canonical_bytes, content_id, sha256_bytes


# This frozen private marker participates in established V2 hashes. It is not a
# supported public format or entrypoint.
_BASE_MATERIAL_DECLARATION_NORMALIZER = (
    "workbench-pack-material-declaration-normalizer-v1"
)
_OPERATION_KINDS = {
    "composition",
    "flags",
    "fluid-form",
    "metadata",
    "property",
    "terminator",
}
_FLUID_STORAGE_KEYS = {"gas", "liquid", "plasma"}
_MAX_SOURCE_FILES = 20_000
_MAX_SOURCE_BYTES = 128 * 1024 * 1024


def _sorted_names(values: Sequence[str], label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise PackProgramError(f"{label} must be a sequence")
    result = tuple(values)
    if any(not isinstance(value, str) or not value for value in result):
        raise PackProgramError(f"{label} contains an invalid name")
    expected = tuple(sorted(set(result), key=lambda value: value.encode("utf-8")))
    if result != expected:
        raise PackProgramError(f"{label} must be unique and canonically sorted")
    return result


@dataclass(frozen=True, slots=True)
class ExpectedMaterialClosurePolicy:
    """Immutable canonical copy of the Atlas material classification policy."""

    canonical_json: str

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any]
    ) -> "ExpectedMaterialClosurePolicy":
        if not isinstance(value, Mapping):
            raise PackProgramError("material closure policy must be an object")
        required = {
            "policy_id",
            "platform_profile",
            "admitted_material_adapters",
            "property_classes",
            "property_dependencies",
            "property_any_dependencies",
            "property_fallback_dependencies",
            "property_incompatibilities",
            "flag_categories",
            "flag_dependencies",
            "flag_property_requirements",
            "property_implied_flags",
            "generation_constraint_kind",
        }
        if set(value) != required:
            raise PackProgramError("material closure policy has unexpected keys")
        if not isinstance(value.get("policy_id"), str) or not value["policy_id"]:
            raise PackProgramError("material closure policy ID is missing")
        if not isinstance(value.get("platform_profile"), str) or not value["platform_profile"]:
            raise PackProgramError("material closure platform profile is missing")
        try:
            canonical = canonical_bytes(value).decode("utf-8")
        except (TypeError, ValueError) as exc:
            raise PackProgramError(
                f"material closure policy is not canonical JSON: {exc}"
            ) from exc
        # Round-trip now so non-JSON mappings cannot survive inside the policy.
        decoded = json.loads(canonical)
        classes = decoded.get("property_classes")
        if not isinstance(classes, dict) or set(classes) != {"base", "capability"}:
            raise PackProgramError("material closure property classes are invalid")
        _sorted_names(classes["base"], "material base property keys")
        _sorted_names(classes["capability"], "material capability property keys")
        for key in (
            "property_dependencies",
            "property_any_dependencies",
            "property_fallback_dependencies",
            "flag_categories",
            "flag_dependencies",
            "flag_property_requirements",
            "property_implied_flags",
        ):
            rows = decoded.get(key)
            if not isinstance(rows, dict):
                raise PackProgramError(f"material closure {key} must be an object")
            for name, children in rows.items():
                if not isinstance(name, str) or not name:
                    raise PackProgramError(f"material closure {key} has an invalid key")
                _sorted_names(children, f"material closure {key}.{name}")
        incompatibilities = decoded.get("property_incompatibilities")
        if not isinstance(incompatibilities, list) or any(
            not isinstance(pair, list)
            or len(pair) != 2
            or any(not isinstance(name, str) or not name for name in pair)
            for pair in incompatibilities
        ):
            raise PackProgramError("material property incompatibilities are invalid")
        return cls(canonical)

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self.canonical_json)

    @property
    def policy_id(self) -> str:
        return str(self.to_dict()["policy_id"])

    @property
    def platform_profile(self) -> str:
        return str(self.to_dict()["platform_profile"])

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MaterialBuilderOperation:
    """One profile-owned operation on the fluent material builder."""

    terminal: str
    kind: str
    property_keys: tuple[str, ...] = ()
    fluid_storage_key: str | None = None
    allowed_arities: tuple[int, ...] = ()
    required_property_keys: tuple[str, ...] = ()
    conditional_effects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.terminal or self.kind not in _OPERATION_KINDS:
            raise PackProgramError("material builder operation is invalid")
        object.__setattr__(
            self,
            "property_keys",
            _sorted_names(self.property_keys, f"{self.terminal} property keys"),
        )
        object.__setattr__(
            self,
            "required_property_keys",
            _sorted_names(
                self.required_property_keys,
                f"{self.terminal} required property keys",
            ),
        )
        object.__setattr__(
            self,
            "conditional_effects",
            _sorted_names(
                self.conditional_effects,
                f"{self.terminal} conditional effects",
            ),
        )
        arities = tuple(self.allowed_arities)
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in arities
        ) or arities != tuple(sorted(set(arities))):
            raise PackProgramError(
                f"{self.terminal} arities must be unique non-negative integers"
            )
        object.__setattr__(self, "allowed_arities", arities)
        if self.fluid_storage_key not in {None, "from-arguments", *_FLUID_STORAGE_KEYS}:
            raise PackProgramError(
                f"{self.terminal} has an invalid fluid storage key rule"
            )
        if self.kind == "fluid-form" and "fluid" not in self.property_keys:
            raise PackProgramError(
                f"{self.terminal} fluid operation must declare the fluid property"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "terminal": self.terminal,
            "kind": self.kind,
            "property_keys": list(self.property_keys),
            "fluid_storage_key": self.fluid_storage_key,
            "allowed_arities": list(self.allowed_arities),
            "required_property_keys": list(self.required_property_keys),
            "conditional_effects": list(self.conditional_effects),
        }


@dataclass(frozen=True, slots=True)
class MaterialDeclarationPolicy:
    """Profile vocabulary plus its exact expected runtime-closure binding."""

    policy_id: str
    pack_profile_id: str
    builder_callee: str
    operations: tuple[MaterialBuilderOperation, ...]
    registry_helpers: tuple[tuple[str, str], ...]
    expected_closure: ExpectedMaterialClosurePolicy
    empty_property_key: str = "empty"
    registry_name_normalization: str = "preserve"

    def __post_init__(self) -> None:
        for value, label in (
            (self.policy_id, "material declaration policy ID"),
            (self.pack_profile_id, "material declaration pack profile"),
            (self.builder_callee, "material builder callee"),
            (self.empty_property_key, "empty material property key"),
        ):
            if not isinstance(value, str) or not value:
                raise PackProgramError(f"{label} is missing")
        if self.registry_name_normalization not in {"lowercase", "preserve"}:
            raise PackProgramError("material registry name normalization is invalid")
        operations = tuple(self.operations)
        terminals = [operation.terminal for operation in operations]
        if terminals != sorted(terminals) or len(terminals) != len(set(terminals)):
            raise PackProgramError(
                "material builder operations must have unique sorted terminals"
            )
        helpers = tuple(self.registry_helpers)
        if helpers != tuple(sorted(helpers)) or len(helpers) != len(set(helpers)):
            raise PackProgramError(
                "material registry helpers must be unique and sorted"
            )
        for callee, namespace in helpers:
            if not callee or not namespace or ":" in namespace:
                raise PackProgramError("material registry helper is invalid")
        if (
            self.expected_closure.platform_profile
            != self.expected_closure.to_dict()["platform_profile"]
        ):
            raise PackProgramError("material closure platform profile is unstable")
        object.__setattr__(self, "operations", operations)
        object.__setattr__(self, "registry_helpers", helpers)

    def to_dict(self) -> dict[str, Any]:
        value = {
            "format": _BASE_MATERIAL_DECLARATION_NORMALIZER,
            "policy_id": self.policy_id,
            "pack_profile_id": self.pack_profile_id,
            "platform_profile_id": self.expected_closure.platform_profile,
            "builder_callee": self.builder_callee,
            "operations": [operation.to_dict() for operation in self.operations],
            "registry_helpers": [
                {"callee": callee, "namespace": namespace}
                for callee, namespace in self.registry_helpers
            ],
            "empty_property_key": self.empty_property_key,
            "expected_closure_policy": {
                "policy_id": self.expected_closure.policy_id,
                "sha256": self.expected_closure.sha256,
            },
        }
        # Preserve the frozen policy bytes consumed by V2 identity. Case
        # normalization is additive for profiles whose runtime helper does it.
        if self.registry_name_normalization != "preserve":
            value["registry_name_normalization"] = self.registry_name_normalization
        return value

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical_bytes(self.to_dict())).hexdigest()


def _normalize_material_declarations(
    sources: Mapping[str, str | bytes],
    policy: MaterialDeclarationPolicy,
) -> dict[str, Any]:
    """Normalize a bounded exact set of Groovy sources into the source feed."""

    prepared = _prepare_sources(sources)
    drafts: list[dict[str, Any]] = []
    for path, source_bytes, source in prepared:
        drafts.extend(
            _source_drafts(
                path,
                source_bytes,
                source,
                policy,
            )
        )
    _attach_identity_collisions(drafts)
    declarations = [_declaration_from_draft(draft, policy) for draft in drafts]
    files = [
        {"path": path, "sha256": sha256_bytes(source_bytes), "bytes": len(source_bytes)}
        for path, source_bytes, _ in prepared
    ]
    manifest_sha256 = hashlib.sha256(canonical_bytes(files)).hexdigest()
    program_id = content_id(
        "workbench-pack-material-source-program:sha256:",
        {
            "files": files,
            "normalizer_policy_sha256": policy.sha256,
            "expected_closure_policy_sha256": policy.expected_closure.sha256,
        },
    )
    return build_source_declarations(
        program_id=program_id,
        pack_profile_id=policy.pack_profile_id,
        platform_profile_id=policy.expected_closure.platform_profile,
        source_sha256=manifest_sha256,
        declarations=declarations,
        source_kind=_BASE_MATERIAL_DECLARATION_NORMALIZER,
    )


def _prepare_sources(
    sources: Mapping[str, str | bytes],
) -> list[tuple[str, bytes, str]]:
    if not isinstance(sources, Mapping) or len(sources) > _MAX_SOURCE_FILES:
        raise PackProgramError("material source set is invalid or exceeds its file bound")
    prepared: list[tuple[str, bytes, str]] = []
    total = 0
    for path, value in sources.items():
        if not isinstance(path, str) or not path or "\\" in path:
            raise PackProgramError("material source path must be portable and relative")
        parsed = PurePosixPath(path)
        if parsed.is_absolute() or ".." in parsed.parts or str(parsed) != path:
            raise PackProgramError("material source path must be portable and relative")
        if isinstance(value, str):
            source = value
            source_bytes = value.encode("utf-8")
        elif isinstance(value, bytes):
            source_bytes = value
            try:
                source = value.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise PackProgramError(
                    f"material source {path} is not UTF-8"
                ) from exc
        else:
            raise PackProgramError(f"material source {path} is not text")
        total += len(source_bytes)
        if total > _MAX_SOURCE_BYTES:
            raise PackProgramError("material source set exceeds its byte bound")
        prepared.append((path, source_bytes, source))
    prepared.sort(key=lambda row: row[0].encode("utf-8"))
    return prepared


def _source_drafts(
    path: str,
    source_bytes: bytes,
    source: str,
    policy: MaterialDeclarationPolicy,
) -> list[dict[str, Any]]:
    tokens = tokenize(source)
    parsed_calls = calls(tokens)
    brace_pairs = _balanced_pairs(tokens, "{", "}")
    material_closure_calls = _dotted_closure_calls(tokens, brace_pairs)
    direct_calls = {
        call.token_start: (
            replace(
                call,
                end=tokens[brace_pairs[call.token_end]].end,
                token_end=brace_pairs[call.token_end],
            )
            if call.closure_form and call.token_end in brace_pairs
            else call
        )
        for call in (*parsed_calls, *material_closure_calls)
        if not call.constructor or call.callee == policy.builder_callee
    }
    builders = [
        call
        for call in parsed_calls
        if call.constructor and call.callee == policy.builder_callee
    ]
    return [
        _builder_draft(
            path,
            source_bytes,
            source,
            tokens,
            builder,
            direct_calls,
            policy,
        )
        for builder in builders
    ]


def _builder_draft(
    path: str,
    source_bytes: bytes,
    source: str,
    tokens: tuple[Token, ...],
    builder: Call,
    direct_calls: Mapping[int, Call],
    policy: MaterialDeclarationPolicy,
) -> dict[str, Any]:
    operations_by_name = {operation.terminal: operation for operation in policy.operations}
    chain: list[Call] = []
    cursor = builder.token_end + 1
    while cursor + 1 < len(tokens) and tokens[cursor].value in {".", "?."}:
        candidate = direct_calls.get(cursor + 1)
        if candidate is None:
            break
        chain.append(candidate)
        cursor = candidate.token_end + 1

    operation_rows: list[dict[str, Any]] = []
    declared_properties: set[str] = set()
    declared_flags: set[str] = set()
    unresolved_flags: list[dict[str, Any]] = []
    fluid_forms: list[dict[str, Any]] = []
    components: list[dict[str, Any]] = []
    elements: list[dict[str, Any]] = []
    presentation: list[dict[str, Any]] = []
    issues: set[str] = set()
    uncertainties: set[str] = set()
    terminator_positions: list[int] = []

    if len(builder.arguments) != 2:
        issues.add("constructor:arity")
    identity = _identity(builder.arguments, policy)
    if identity["numeric_id_state"] != "exact-literal":
        uncertainties.add("identity:numeric-id-unresolved")
    if identity["registry_name_state"] != "exact-literal":
        uncertainties.add("identity:registry-name-unresolved")

    for index, call in enumerate(chain):
        operation = operations_by_name.get(call.terminal)
        row = _call_row(source, call, operation)
        operation_rows.append(row)
        if operation is None:
            uncertainties.add(f"operation:unsupported:{call.terminal}")
            continue
        effective_arity = 1 if call.closure_form else len(call.arguments)
        if operation.allowed_arities and effective_arity not in operation.allowed_arities:
            issues.add(f"operation:arity:{call.terminal}")
        declared_properties.update(operation.property_keys)
        for required in operation.required_property_keys:
            if required not in declared_properties:
                issues.add(f"operation:{call.terminal}:requires-property:{required}")
        uncertainties.update(operation.conditional_effects)
        if operation.kind == "terminator":
            terminator_positions.append(index)
        elif operation.kind == "flags":
            for argument_index, argument in enumerate(call.arguments):
                flag = _known_flag(argument, policy.expected_closure)
                if flag is None:
                    unresolved_flags.append(
                        {
                            "argument_index": argument_index,
                            "expression": argument.expression,
                            "span": _argument_span(argument),
                        }
                    )
                else:
                    declared_flags.add(flag)
        elif operation.kind == "fluid-form":
            storage_key = _fluid_storage_key(call, operation)
            if storage_key is None:
                uncertainties.add(f"form:storage-key-unresolved:{call.terminal}")
            fluid_forms.append(
                {
                    "storage_key": storage_key,
                    "operation_index": index,
                    "terminal": call.terminal,
                    "span": _span(call),
                    "arguments": [argument.expression for argument in call.arguments],
                }
            )
        elif operation.kind == "composition":
            if call.terminal == "element":
                if len(call.arguments) == 1:
                    elements.append(
                        {
                            "expression": call.arguments[0].expression,
                            "span": _argument_span(call.arguments[0]),
                        }
                    )
                else:
                    issues.add("composition:element-arity")
            elif call.terminal == "components":
                parsed_components = _component_pairs(call.arguments)
                if parsed_components is None:
                    uncertainties.add("composition:overload-unresolved")
                else:
                    components.extend(parsed_components)
        elif operation.kind == "metadata":
            presentation.append(
                {
                    "terminal": call.terminal,
                    "arguments": [argument.expression for argument in call.arguments],
                    "span": _span(call),
                }
            )

    complete = bool(terminator_positions) and terminator_positions[-1] == len(chain) - 1
    if not complete:
        uncertainties.add("builder:incomplete-chain")
    if len(terminator_positions) > 1 or (
        terminator_positions and terminator_positions[0] != len(chain) - 1
    ):
        issues.add("builder:terminator-position")
    if unresolved_flags:
        uncertainties.add("flag:expression-unresolved")
    duplicate_storage = sorted(
        key
        for key, count in _counts(
            row["storage_key"] for row in fluid_forms if row["storage_key"] is not None
        ).items()
        if count > 1
    )
    for key in duplicate_storage:
        issues.add(f"form:duplicate-storage-key:{key}")
    if len([row for row in operation_rows if row["terminal"] == "components"]) > 1:
        uncertainties.add("composition:multiple-components-calls")

    understood = not any(
        uncertainty.startswith("operation:unsupported:") for uncertainty in uncertainties
    )
    closure = _expected_closure(
        declared_properties,
        declared_flags,
        policy,
        complete=complete,
        understood=understood,
        flags_understood=not unresolved_flags,
    )
    issues.update(closure.pop("issues"))
    if components and not (
        {
            "decomposition_by_centrifuging",
            "decomposition_by_electrolyzing",
            "disable_decomposition",
        }
        & set(closure["flags"]["names"])
    ):
        uncertainties.add("flag:composition-derived-decomposition-unresolved")

    chain_end = chain[-1].end if chain else builder.end
    source_occurrence = f"{path}#char={builder.start}"
    symbol = _assigned_symbol(tokens, builder)
    composition = {
        "basis": _composition_basis(components, elements),
        "components": components,
        "elements": elements,
    }
    form_lens = {
        "fluid_storage_relationships": fluid_forms,
        "duplicate_storage_keys": duplicate_storage,
    }
    core = {
        "identity": identity,
        "symbol": symbol,
        "declared_property_keys": sorted(declared_properties),
        "declared_flag_names": sorted(declared_flags),
        "unresolved_flag_expressions": unresolved_flags,
        "expected_verified_closure": closure,
        "composition": composition,
        "presentation": presentation,
    }
    return {
        "path": path,
        "source_sha256": sha256_bytes(source_bytes),
        "source_occurrence": source_occurrence,
        "symbol": symbol,
        "identity": identity,
        "constructor": _call_row(source, builder, None),
        "operations": operation_rows,
        "builder_span": {
            **_span(builder),
            "end": chain_end,
            "source_text": source[builder.start:chain_end],
        },
        "complete": complete,
        "core": core,
        "core_sha256": hashlib.sha256(canonical_bytes(core)).hexdigest(),
        "form_lens": form_lens,
        "issues": issues,
        "uncertainties": uncertainties,
        "collisions": [],
    }


def _identity(
    arguments: tuple[Argument, ...],
    policy: MaterialDeclarationPolicy,
) -> dict[str, Any]:
    numeric = arguments[0].integer() if arguments else None
    registry_argument = arguments[1] if len(arguments) > 1 else None
    registry_name = registry_argument.wrapped_string() if registry_argument else None
    namespace = None
    resource_location = None
    helper = _argument_callee(registry_argument) if registry_argument else None
    helper_namespaces = dict(policy.registry_helpers)
    if registry_name is not None and policy.registry_name_normalization == "lowercase":
        registry_name = registry_name.lower()
    if registry_name is not None and helper in helper_namespaces:
        namespace = helper_namespaces[helper]
        resource_location = f"{namespace}:{registry_name}"
    elif registry_name is not None and ":" in registry_name:
        namespace, _, local_name = registry_name.partition(":")
        if namespace and local_name:
            resource_location = registry_name
            registry_name = local_name
        else:
            namespace = None
    return {
        "numeric_id": numeric,
        "numeric_id_state": "exact-literal" if numeric is not None else "unresolved-expression",
        "numeric_id_expression": arguments[0].expression if arguments else None,
        "registry_name": registry_name,
        "registry_name_state": (
            "exact-literal" if registry_name is not None else "unresolved-expression"
        ),
        "registry_expression": registry_argument.expression if registry_argument else None,
        "registry_helper": helper,
        "namespace": namespace,
        "resource_location": resource_location,
    }


def _balanced_pairs(
    tokens: tuple[Token, ...],
    opening: str,
    closing: str,
) -> dict[int, int]:
    result: dict[int, int] = {}
    stack: list[int] = []
    for position, token in enumerate(tokens):
        if token.value == opening:
            stack.append(position)
        elif token.value == closing and stack:
            result[stack.pop()] = position
    return result


def _dotted_closure_calls(
    tokens: tuple[Token, ...],
    brace_pairs: Mapping[int, int],
) -> tuple[Call, ...]:
    """Admit only dotted closure calls; chain adjacency supplies the receiver."""

    result: list[Call] = []
    for position in range(1, len(tokens) - 1):
        token = tokens[position]
        brace = position + 1
        if (
            token.kind != "identifier"
            or tokens[position - 1].value not in {".", "?."}
            or tokens[brace].value != "{"
            or brace not in brace_pairs
        ):
            continue
        closing = brace_pairs[brace]
        result.append(
            Call(
                callee=token.value,
                terminal=token.value,
                constructor=False,
                arguments=(),
                start=token.start,
                end=tokens[closing].end,
                line=token.line,
                column=token.column,
                token_start=position,
                token_end=closing,
                closure_form=True,
            )
        )
    return tuple(result)


def _argument_callee(argument: Argument | None) -> str | None:
    if argument is None:
        return None
    parts: list[str] = []
    expect_identifier = True
    for token in argument.tokens:
        if token.value == "new" and not parts:
            continue
        if token.value == "(":
            break
        if expect_identifier and token.kind == "identifier":
            parts.append(token.value)
            expect_identifier = False
        elif not expect_identifier and token.value in {".", "?."}:
            expect_identifier = True
        else:
            return None
    return ".".join(parts) if parts and not expect_identifier else None


def _assigned_symbol(tokens: tuple[Token, ...], builder: Call) -> str | None:
    cursor = builder.token_start - 1
    if cursor >= 1 and tokens[cursor].value == "=" and tokens[cursor - 1].kind == "identifier":
        return tokens[cursor - 1].value
    return None


def _call_row(
    source: str,
    call: Call,
    operation: MaterialBuilderOperation | None,
) -> dict[str, Any]:
    return {
        "callee": call.callee,
        "terminal": call.terminal,
        "constructor": call.constructor,
        "closure_form": call.closure_form,
        "recognized": operation is not None or call.constructor,
        "operation_kind": (
            operation.kind if operation else ("constructor" if call.constructor else None)
        ),
        "arguments": [
            {
                "expression": argument.expression,
                "source_text": _argument_source(source, argument),
                "integer_literal": argument.integer(),
                "string_literal": argument.exact_string(),
                "span": _argument_span(argument),
            }
            for argument in call.arguments
        ],
        "span": _span(call),
        "source_text": source[call.start:call.end],
    }


def _span(call: Call) -> dict[str, int]:
    return {
        "start": call.start,
        "end": call.end,
        "line": call.line,
        "column": call.column,
    }


def _argument_span(argument: Argument) -> dict[str, int] | None:
    if not argument.tokens:
        return None
    first = argument.tokens[0]
    return {
        "start": first.start,
        "end": argument.tokens[-1].end,
        "line": first.line,
        "column": first.column,
    }


def _argument_source(source: str, argument: Argument) -> str:
    span = _argument_span(argument)
    return "" if span is None else source[span["start"]:span["end"]]


def _known_flag(
    argument: Argument,
    closure: ExpectedMaterialClosurePolicy,
) -> str | None:
    tokens = argument.tokens
    if not tokens:
        return None
    expect_identifier = True
    identifiers: list[str] = []
    for token in tokens:
        if expect_identifier and token.kind == "identifier":
            identifiers.append(token.value)
            expect_identifier = False
        elif not expect_identifier and token.value in {".", "?."}:
            expect_identifier = True
        else:
            return None
    if expect_identifier:
        return None
    candidate = identifiers[-1].lower()
    known = set(closure.to_dict()["flag_categories"])
    return candidate if candidate in known else None


def _fluid_storage_key(
    call: Call,
    operation: MaterialBuilderOperation,
) -> str | None:
    rule = operation.fluid_storage_key
    if rule in _FLUID_STORAGE_KEYS:
        return rule
    if rule != "from-arguments":
        return None
    if not call.arguments:
        return "liquid"
    index = 1 if len(call.arguments) == 4 else 0
    if index >= len(call.arguments):
        return None
    identifiers = [
        token.value.lower()
        for token in call.arguments[index].tokens
        if token.kind == "identifier"
    ]
    for candidate in reversed(identifiers):
        if candidate in _FLUID_STORAGE_KEYS:
            return candidate
    return None


def _component_pairs(
    arguments: tuple[Argument, ...],
) -> list[dict[str, Any]] | None:
    if not arguments or len(arguments) % 2:
        return None
    result: list[dict[str, Any]] = []
    for ordinal in range(0, len(arguments), 2):
        amount = arguments[ordinal + 1].integer()
        if amount is None:
            return None
        result.append(
            {
                "ordinal": ordinal // 2,
                "material_expression": arguments[ordinal].expression,
                "amount": amount,
                "material_span": _argument_span(arguments[ordinal]),
                "amount_span": _argument_span(arguments[ordinal + 1]),
            }
        )
    return result


def _composition_basis(
    components: Sequence[Mapping[str, Any]],
    elements: Sequence[Mapping[str, Any]],
) -> str:
    if components and elements:
        return "elemental_composite"
    if components:
        return "composite"
    if elements:
        return "elemental"
    return "unspecified"


def _expected_closure(
    declared_properties: set[str],
    declared_flags: set[str],
    policy: MaterialDeclarationPolicy,
    *,
    complete: bool,
    understood: bool,
    flags_understood: bool,
) -> dict[str, Any]:
    value = policy.expected_closure.to_dict()
    properties = set(declared_properties)
    property_reasons: dict[str, set[str]] = defaultdict(set)
    dependencies = value["property_dependencies"]
    alternatives = value["property_any_dependencies"]
    fallbacks = value["property_fallback_dependencies"]
    changed = True
    while changed:
        changed = False
        for key, required in dependencies.items():
            if key not in properties:
                continue
            for dependency in required:
                if dependency not in properties:
                    properties.add(dependency)
                    property_reasons[dependency].add(f"required-by:{key}")
                    changed = True
        for key, defaults in fallbacks.items():
            if key not in properties or properties.intersection(alternatives.get(key, ())):
                continue
            for dependency in defaults:
                if dependency not in properties:
                    properties.add(dependency)
                    property_reasons[dependency].add(f"fallback-for:{key}")
                    changed = True
    if complete and understood and not properties:
        properties.add(policy.empty_property_key)
        property_reasons[policy.empty_property_key].add("empty-builder-verification")

    flags = set(declared_flags)
    flag_reasons: dict[str, set[str]] = defaultdict(set)
    for property_key, implied in value["property_implied_flags"].items():
        if property_key not in properties:
            continue
        for flag in implied:
            if flag not in flags:
                flags.add(flag)
                flag_reasons[flag].add(f"implied-by-property:{property_key}")
    changed = True
    while changed:
        changed = False
        for flag, required in value["flag_dependencies"].items():
            if flag not in flags:
                continue
            for dependency in required:
                if dependency not in flags:
                    flags.add(dependency)
                    flag_reasons[dependency].add(f"required-by:{flag}")
                    changed = True

    issues: set[str] = set()
    for left, right in value["property_incompatibilities"]:
        if left in properties and right in properties:
            issues.add(f"property:incompatible:{left}|{right}")
    for flag, required in value["flag_property_requirements"].items():
        if flag not in flags:
            continue
        for property_key in required:
            if property_key not in properties:
                issues.add(f"flag:{flag}:missing-property:{property_key}")
    for key, required in alternatives.items():
        if key in properties and not properties.intersection(required):
            issues.add(f"property:{key}:missing-any:{'|'.join(required)}")

    base = set(value["property_classes"]["base"])
    capability = set(value["property_classes"]["capability"])
    property_names = sorted(properties)
    flag_names = sorted(flags)
    categories = value["flag_categories"]
    return {
        "state": "exact-static" if complete and understood and flags_understood else "lower-bound-static",
        "closure_policy_id": policy.expected_closure.policy_id,
        "closure_policy_sha256": policy.expected_closure.sha256,
        "properties": {
            "keys": property_names,
            "base_keys": [name for name in property_names if name in base],
            "capability_keys": [name for name in property_names if name in capability],
            "profile_extension_keys": [
                name for name in property_names if name not in base and name not in capability
            ],
            "added_by_verification": [
                {"key": key, "reasons": sorted(reasons)}
                for key, reasons in sorted(property_reasons.items())
            ],
        },
        "flags": {
            "names": flag_names,
            "added_by_verification": [
                {"name": name, "reasons": sorted(reasons)}
                for name, reasons in sorted(flag_reasons.items())
            ],
            "classifications": [
                {
                    "name": name,
                    "categories": list(categories.get(name, ["profile_extension"])),
                }
                for name in flag_names
            ],
        },
        "issues": issues,
    }


def _counts(values: Sequence[str] | Any) -> dict[str, int]:
    result: dict[str, int] = defaultdict(int)
    for value in values:
        result[value] += 1
    return dict(result)


def _attach_identity_collisions(drafts: list[dict[str, Any]]) -> None:
    indexes: dict[tuple[str, Any], list[dict[str, Any]]] = defaultdict(list)
    for draft in drafts:
        identity = draft["identity"]
        if identity["numeric_id"] is not None:
            indexes[("numeric-id", identity["numeric_id"])].append(draft)
        if identity["resource_location"] is not None:
            indexes[("resource-location", identity["resource_location"])].append(draft)
    for (kind, value), rows in sorted(indexes.items(), key=lambda item: repr(item[0])):
        if len(rows) < 2:
            continue
        collision = {
            "identity_kind": kind,
            "value": value,
            "evidence_state": "static-candidate",
            "occurrences": [
                {
                    "source_occurrence": row["source_occurrence"],
                    "path": row["path"],
                    "start": row["builder_span"]["start"],
                    "line": row["builder_span"]["line"],
                    "symbol": row["symbol"],
                }
                for row in rows
            ],
        }
        for row in rows:
            row["collisions"].append(collision)
            row["issues"].add(f"identity:duplicate-{kind}")


def _declaration_from_draft(
    draft: Mapping[str, Any],
    policy: MaterialDeclarationPolicy,
) -> dict[str, Any]:
    identity = draft["identity"]
    key = (
        {"resource_location": identity["resource_location"]}
        if identity["resource_location"] is not None
        else {"source_occurrence": draft["source_occurrence"]}
    )
    issues = sorted(draft["issues"])
    uncertainties = sorted(draft["uncertainties"])
    return source_declaration(
        semantic_descriptor={
            "domain": "material",
            "kind": "material-declaration",
            "key": key,
        },
        attributes={
            "normalizer": {
                "format": _BASE_MATERIAL_DECLARATION_NORMALIZER,
                "policy_id": policy.policy_id,
                "policy_sha256": policy.sha256,
                "expected_closure_policy_id": policy.expected_closure.policy_id,
                "expected_closure_policy_sha256": policy.expected_closure.sha256,
            },
            "normalization_status": (
                "exact-static" if not issues and not uncertainties else "frontier-static"
            ),
            "identity": identity,
            "symbol": draft["symbol"],
            "constructor": draft["constructor"],
            "operations": draft["operations"],
            "builder_complete": draft["complete"],
            "declared_material_core": draft["core"],
            "declared_material_core_sha256": draft["core_sha256"],
            "form_lens": draft["form_lens"],
            "collision_candidates": draft["collisions"],
            "issues": issues,
            "uncertainties": uncertainties,
        },
        lifecycle={"stage": _lifecycle_stage(str(draft["path"]))},
        provenance={
            "authority": "Pack Program Studio",
            "source": {
                "path": draft["path"],
                "sha256": draft["source_sha256"],
                "span": draft["builder_span"],
                "offset_units": "UTF-8-decoded Unicode code points",
            },
            "source_occurrence": draft["source_occurrence"],
        },
    )


def _lifecycle_stage(path: str) -> str:
    known = (
        "preInit",
        "postInit",
        "prePostInit",
        "init",
        "material",
        "classes",
    )
    parts = PurePosixPath(path).parts
    return next((stage for stage in known if stage in parts), "source")
