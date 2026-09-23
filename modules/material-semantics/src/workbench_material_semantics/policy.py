#!/usr/bin/env python3

"""Immutable, profile-owned material semantics with compiled V1 runtime view."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping

from .classification import MaterialClassificationPolicy


class MaterialSemanticsError(ValueError):
    """A semantic policy is ambiguous, inconsistent, or not canonical."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MaterialSemanticsError(
            f"material semantics are not canonical JSON: {exc}"
        ) from exc


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise MaterialSemanticsError(f"{label} must be a non-empty string")
    return value


def _sha256(value: object, label: str) -> str:
    value = _text(value, label)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise MaterialSemanticsError(f"{label} must be a lowercase SHA-256")
    return value


def _sorted_text(values: Iterable[str], label: str) -> tuple[str, ...]:
    result = tuple(values)
    if any(not isinstance(value, str) or not value for value in result):
        raise MaterialSemanticsError(f"{label} contains an invalid name")
    expected = tuple(sorted(set(result), key=lambda value: value.encode("utf-8")))
    if result != expected:
        raise MaterialSemanticsError(f"{label} must be unique and canonically sorted")
    return result


@dataclass(frozen=True)
class SemanticSourceFile:
    path: str
    sha256: str

    def __post_init__(self) -> None:
        _text(self.path, "semantic source path")
        _sha256(self.sha256, f"semantic source {self.path} SHA-256")

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}


@dataclass(frozen=True)
class FlagDefinition:
    """One runtime flag and the source constant that declares it."""

    name: str
    declaring_type: str
    source_symbol: str
    categories: tuple[str, ...]
    required_flags: tuple[str, ...] = ()
    required_properties: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.name, "material flag name")
        _text(self.declaring_type, f"material flag {self.name} declaring type")
        _text(self.source_symbol, f"material flag {self.name} source symbol")
        object.__setattr__(
            self,
            "categories",
            _sorted_text(self.categories, f"material flag {self.name} categories"),
        )
        object.__setattr__(
            self,
            "required_flags",
            _sorted_text(
                self.required_flags,
                f"material flag {self.name} required flags",
            ),
        )
        object.__setattr__(
            self,
            "required_properties",
            _sorted_text(
                self.required_properties,
                f"material flag {self.name} required properties",
            ),
        )

    @property
    def qualified_symbol(self) -> str:
        return f"{self.declaring_type}.{self.source_symbol}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "declaring_type": self.declaring_type,
            "source_symbol": self.source_symbol,
            "categories": list(self.categories),
            "required_flags": list(self.required_flags),
            "required_properties": list(self.required_properties),
        }


@dataclass(frozen=True)
class FlagPresetDefinition:
    """One finite source preset expanded into exact runtime flag names."""

    declaring_type: str
    source_symbol: str
    flag_names: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.declaring_type, "material flag preset declaring type")
        _text(self.source_symbol, "material flag preset symbol")
        if not self.flag_names or any(
            not isinstance(name, str) or not name for name in self.flag_names
        ):
            raise MaterialSemanticsError(
                f"material flag preset {self.source_symbol} has invalid members"
            )
        if len(self.flag_names) != len(set(self.flag_names)):
            raise MaterialSemanticsError(
                f"material flag preset {self.source_symbol} has duplicate members"
            )

    @property
    def qualified_symbol(self) -> str:
        return f"{self.declaring_type}.{self.source_symbol}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "declaring_type": self.declaring_type,
            "source_symbol": self.source_symbol,
            "flag_names": list(self.flag_names),
        }


@dataclass(frozen=True)
class MaterialSymbolDefinition:
    """One source material constant with its exact registry resource identity."""

    declaring_type: str
    source_symbol: str
    resource_location: str

    def __post_init__(self) -> None:
        _text(self.declaring_type, "material symbol declaring type")
        _text(self.source_symbol, "material source symbol")
        resource = _text(self.resource_location, "material symbol resource location")
        namespace, separator, path = resource.partition(":")
        if not separator or not namespace or not path:
            raise MaterialSemanticsError(
                f"invalid material symbol resource location: {resource}"
            )

    @property
    def qualified_symbol(self) -> str:
        return f"{self.declaring_type}.{self.source_symbol}"

    def to_dict(self) -> dict[str, str]:
        return {
            "declaring_type": self.declaring_type,
            "source_symbol": self.source_symbol,
            "resource_location": self.resource_location,
        }


@dataclass(frozen=True)
class SemanticConstant:
    declaring_type: str
    source_symbol: str
    value_type: str
    value: int | bool | str | tuple[int, ...]

    def __post_init__(self) -> None:
        _text(self.declaring_type, "semantic constant declaring type")
        _text(self.source_symbol, "semantic constant symbol")
        if self.value_type not in {"boolean", "int", "int-array", "long-array", "string"}:
            raise MaterialSemanticsError(
                f"unsupported semantic constant type: {self.value_type}"
            )
        if self.value_type == "boolean" and not isinstance(self.value, bool):
            raise MaterialSemanticsError("boolean semantic constant has wrong value")
        if self.value_type == "int" and (
            isinstance(self.value, bool) or not isinstance(self.value, int)
        ):
            raise MaterialSemanticsError("integer semantic constant has wrong value")
        if self.value_type == "string" and not isinstance(self.value, str):
            raise MaterialSemanticsError("string semantic constant has wrong value")
        if self.value_type in {"int-array", "long-array"} and (
            not isinstance(self.value, tuple)
            or any(isinstance(item, bool) or not isinstance(item, int) for item in self.value)
        ):
            raise MaterialSemanticsError("array semantic constant has wrong value")

    @property
    def qualified_symbol(self) -> str:
        return f"{self.declaring_type}.{self.source_symbol}"

    def to_dict(self) -> dict[str, Any]:
        value: Any = list(self.value) if isinstance(self.value, tuple) else self.value
        return {
            "declaring_type": self.declaring_type,
            "source_symbol": self.source_symbol,
            "value_type": self.value_type,
            "value": value,
        }


@dataclass(frozen=True)
class ValueCondition:
    kind: str
    argument_index: int
    expected: int | bool
    default: int | bool | None = None
    java_coercion: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"boolean-equals", "integer-greater-than-or-equal"}:
            raise MaterialSemanticsError(
                f"unsupported conditional value kind: {self.kind}"
            )
        if isinstance(self.argument_index, bool) or self.argument_index < 0:
            raise MaterialSemanticsError(
                "conditional value argument index must be non-negative"
            )
        if self.kind == "boolean-equals" and not isinstance(self.expected, bool):
            raise MaterialSemanticsError("boolean condition has non-boolean expectation")
        if self.kind == "integer-greater-than-or-equal" and (
            isinstance(self.expected, bool) or not isinstance(self.expected, int)
        ):
            raise MaterialSemanticsError("integer condition has non-integer threshold")
        if self.java_coercion not in {None, "signed-int32"}:
            raise MaterialSemanticsError(
                f"unsupported conditional Java coercion: {self.java_coercion}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "argument_index": self.argument_index,
            "expected": self.expected,
            "default": self.default,
            "java_coercion": self.java_coercion,
        }


@dataclass(frozen=True)
class ConditionalFlagRule:
    rule_id: str
    trigger_operation: str
    required_properties: tuple[str, ...]
    conditions: tuple[ValueCondition, ...]
    added_flags: tuple[str, ...]
    source_files: tuple[SemanticSourceFile, ...]

    def __post_init__(self) -> None:
        _text(self.rule_id, "conditional material rule ID")
        _text(self.trigger_operation, "conditional material trigger operation")
        object.__setattr__(
            self,
            "required_properties",
            _sorted_text(
                self.required_properties,
                f"conditional rule {self.rule_id} required properties",
            ),
        )
        object.__setattr__(
            self,
            "added_flags",
            _sorted_text(
                self.added_flags,
                f"conditional rule {self.rule_id} added flags",
            ),
        )
        if not self.conditions:
            raise MaterialSemanticsError(
                f"conditional rule {self.rule_id} has no conditions"
            )
        _canonical_source_files(self.source_files, f"conditional rule {self.rule_id}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "trigger_operation": self.trigger_operation,
            "required_properties": list(self.required_properties),
            "conditions": [condition.to_dict() for condition in self.conditions],
            "added_flags": list(self.added_flags),
            "source_files": [source.to_dict() for source in self.source_files],
        }


def _canonical_source_files(
    values: tuple[SemanticSourceFile, ...],
    label: str,
) -> None:
    expected = tuple(sorted(values, key=lambda value: value.path.encode("utf-8")))
    if values != expected or len({value.path for value in values}) != len(values):
        raise MaterialSemanticsError(
            f"{label} source files must be unique and canonically sorted"
        )


@dataclass(frozen=True)
class MaterialSemanticLayer:
    layer_id: str
    owner: str
    source_id: str
    revision: str
    tree: str
    source_files: tuple[SemanticSourceFile, ...] = ()
    flags: tuple[FlagDefinition, ...] = ()
    presets: tuple[FlagPresetDefinition, ...] = ()
    material_symbols: tuple[MaterialSymbolDefinition, ...] = ()

    def __post_init__(self) -> None:
        for value, label in (
            (self.layer_id, "material semantic layer ID"),
            (self.owner, "material semantic layer owner"),
            (self.source_id, "material semantic source ID"),
            (self.revision, "material semantic source revision"),
            (self.tree, "material semantic source tree"),
        ):
            _text(value, label)
        _canonical_source_files(self.source_files, f"semantic layer {self.layer_id}")
        expected_flags = tuple(
            sorted(self.flags, key=lambda value: value.name.encode("utf-8"))
        )
        if self.flags != expected_flags or len({item.name for item in self.flags}) != len(self.flags):
            raise MaterialSemanticsError(
                f"semantic layer {self.layer_id} flags must be unique and sorted"
            )
        expected_presets = tuple(
            sorted(self.presets, key=lambda value: value.qualified_symbol.encode("utf-8"))
        )
        if self.presets != expected_presets or len(
            {item.qualified_symbol for item in self.presets}
        ) != len(self.presets):
            raise MaterialSemanticsError(
                f"semantic layer {self.layer_id} presets must be unique and sorted"
            )
        expected_symbols = tuple(
            sorted(
                self.material_symbols,
                key=lambda value: value.qualified_symbol.encode("utf-8"),
            )
        )
        if self.material_symbols != expected_symbols or len(
            {item.qualified_symbol for item in self.material_symbols}
        ) != len(self.material_symbols):
            raise MaterialSemanticsError(
                f"semantic layer {self.layer_id} material symbols must be unique and sorted"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer_id": self.layer_id,
            "owner": self.owner,
            "source_id": self.source_id,
            "revision": self.revision,
            "tree": self.tree,
            "source_files": [source.to_dict() for source in self.source_files],
            "flags": [flag.to_dict() for flag in self.flags],
            "presets": [preset.to_dict() for preset in self.presets],
            "material_symbols": [
                symbol.to_dict() for symbol in self.material_symbols
            ],
        }


@dataclass(frozen=True)
class MaterialSemanticsPolicy:
    """One composed policy that compiles source and runtime semantic views."""

    policy_id: str
    pack_profile_id: str
    runtime_policy_id: str
    base_runtime_policy: MaterialClassificationPolicy
    layers: tuple[MaterialSemanticLayer, ...]
    constants: tuple[SemanticConstant, ...] = ()
    conditional_flag_rules: tuple[ConditionalFlagRule, ...] = ()

    def __post_init__(self) -> None:
        _text(self.policy_id, "material semantics policy ID")
        _text(self.pack_profile_id, "material semantics pack profile ID")
        _text(self.runtime_policy_id, "material semantics runtime policy ID")
        if not isinstance(self.base_runtime_policy, MaterialClassificationPolicy):
            raise MaterialSemanticsError(
                "material semantics require a base runtime policy"
            )
        expected_layers = tuple(
            sorted(self.layers, key=lambda value: value.layer_id.encode("utf-8"))
        )
        if self.layers != expected_layers or len(
            {layer.layer_id for layer in self.layers}
        ) != len(self.layers):
            raise MaterialSemanticsError("material semantic layers must be unique and sorted")
        expected_constants = tuple(
            sorted(self.constants, key=lambda value: value.qualified_symbol.encode("utf-8"))
        )
        if self.constants != expected_constants or len(
            {constant.qualified_symbol for constant in self.constants}
        ) != len(self.constants):
            raise MaterialSemanticsError("semantic constants must be unique and sorted")
        expected_rules = tuple(
            sorted(self.conditional_flag_rules, key=lambda value: value.rule_id.encode("utf-8"))
        )
        if self.conditional_flag_rules != expected_rules or len(
            {rule.rule_id for rule in self.conditional_flag_rules}
        ) != len(self.conditional_flag_rules):
            raise MaterialSemanticsError("conditional material rules must be unique and sorted")
        self._validate_flag_graph()

    def _validate_flag_graph(self) -> None:
        base = self.base_runtime_policy.to_dict()
        categories = dict(base["flag_categories"])
        dependencies = dict(base["flag_dependencies"])
        requirements = dict(base["flag_property_requirements"])
        definitions: dict[str, FlagDefinition] = {}
        symbols: set[str] = set()
        material_symbols: set[str] = set()
        for layer in self.layers:
            for material_symbol in layer.material_symbols:
                if material_symbol.qualified_symbol in material_symbols:
                    raise MaterialSemanticsError(
                        "duplicate material source symbol: "
                        f"{material_symbol.qualified_symbol}"
                    )
                material_symbols.add(material_symbol.qualified_symbol)
            for flag in layer.flags:
                if flag.qualified_symbol in symbols:
                    raise MaterialSemanticsError(
                        f"duplicate material flag source symbol: {flag.qualified_symbol}"
                    )
                symbols.add(flag.qualified_symbol)
                prior = definitions.get(flag.name)
                if prior is not None and prior != flag:
                    raise MaterialSemanticsError(
                        f"conflicting material flag definition: {flag.name}"
                    )
                definitions[flag.name] = flag
                if flag.name in categories and tuple(categories[flag.name]) != flag.categories:
                    raise MaterialSemanticsError(
                        f"material flag categories differ from base policy: {flag.name}"
                    )
                if flag.name in dependencies and tuple(dependencies[flag.name]) != flag.required_flags:
                    raise MaterialSemanticsError(
                        f"material flag dependencies differ from base policy: {flag.name}"
                    )
                if flag.name in requirements and tuple(requirements[flag.name]) != flag.required_properties:
                    raise MaterialSemanticsError(
                        f"material flag requirements differ from base policy: {flag.name}"
                    )
        known = set(categories) | set(definitions)
        for flag in definitions.values():
            unknown = set(flag.required_flags) - known
            if unknown:
                raise MaterialSemanticsError(
                    f"material flag {flag.name} requires unknown flags: {sorted(unknown)}"
                )
        for layer in self.layers:
            for preset in layer.presets:
                unknown = set(preset.flag_names) - known
                if unknown:
                    raise MaterialSemanticsError(
                        f"material flag preset {preset.qualified_symbol} has unknown flags: {sorted(unknown)}"
                    )
        for rule in self.conditional_flag_rules:
            unknown = set(rule.added_flags) - known
            if unknown:
                raise MaterialSemanticsError(
                    f"conditional rule {rule.rule_id} adds unknown flags: {sorted(unknown)}"
                )

    @property
    def sha256(self) -> str:
        return hashlib.sha256(_canonical_bytes(self.to_dict())).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "workbench-atlas-material-semantics-policy-v2",
            "policy_id": self.policy_id,
            "pack_profile_id": self.pack_profile_id,
            "runtime_policy_id": self.runtime_policy_id,
            "base_runtime_policy": {
                **self.base_runtime_policy.to_dict(),
                "sha256": self.base_runtime_policy.sha256,
            },
            "layers": [layer.to_dict() for layer in self.layers],
            "constants": [constant.to_dict() for constant in self.constants],
            "conditional_flag_rules": [
                rule.to_dict() for rule in self.conditional_flag_rules
            ],
        }

    def flags_by_name(self) -> dict[str, FlagDefinition]:
        return {
            flag.name: flag
            for layer in self.layers
            for flag in layer.flags
        }

    def flags_by_qualified_symbol(self) -> dict[str, FlagDefinition]:
        return {
            flag.qualified_symbol: flag
            for layer in self.layers
            for flag in layer.flags
        }

    def presets_by_qualified_symbol(self) -> dict[str, FlagPresetDefinition]:
        return {
            preset.qualified_symbol: preset
            for layer in self.layers
            for preset in layer.presets
        }

    def constants_by_qualified_symbol(self) -> dict[str, SemanticConstant]:
        return {constant.qualified_symbol: constant for constant in self.constants}

    def material_symbols_by_qualified_symbol(
        self,
    ) -> dict[str, MaterialSymbolDefinition]:
        return {
            symbol.qualified_symbol: symbol
            for layer in self.layers
            for symbol in layer.material_symbols
        }

    def runtime_policy(self) -> MaterialClassificationPolicy:
        """Compile the composed flag layers into the existing runtime policy."""

        base = self.base_runtime_policy
        categories = dict(base.flag_categories)
        dependencies = dict(base.flag_dependencies)
        requirements = dict(base.flag_property_requirements)
        for flag in self.flags_by_name().values():
            categories[flag.name] = flag.categories
            if flag.required_flags:
                dependencies[flag.name] = flag.required_flags
            if flag.required_properties:
                requirements[flag.name] = flag.required_properties
        return MaterialClassificationPolicy(
            policy_id=self.runtime_policy_id,
            platform_profile=base.platform_profile,
            admitted_material_adapters=base.admitted_material_adapters,
            base_property_keys=base.base_property_keys,
            capability_property_keys=base.capability_property_keys,
            property_dependencies=base.property_dependencies,
            property_any_dependencies=base.property_any_dependencies,
            property_fallback_dependencies=base.property_fallback_dependencies,
            property_incompatibilities=base.property_incompatibilities,
            flag_categories=tuple(sorted(categories.items())),
            flag_dependencies=tuple(sorted(dependencies.items())),
            flag_property_requirements=tuple(sorted(requirements.items())),
            property_implied_flags=base.property_implied_flags,
            generation_constraint_kind=base.generation_constraint_kind,
        )
