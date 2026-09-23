"""Bounded source symbol and constant-expression evaluation for materials."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from workbench_material_semantics import (
    ConditionalFlagRule,
    MaterialSemanticsPolicy,
)

from .lexer import Token, literal_string, tokenize


@dataclass(frozen=True, slots=True)
class SourceImportScope:
    type_imports: tuple[tuple[str, str], ...] = ()
    explicit_static_imports: tuple[tuple[str, str], ...] = ()
    static_wildcards: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for values, label in (
            (self.type_imports, "type imports"),
            (self.explicit_static_imports, "explicit static imports"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"material {label} must be unique and sorted")
        if self.static_wildcards != tuple(sorted(set(self.static_wildcards))):
            raise ValueError("material static wildcards must be unique and sorted")

    def to_dict(self) -> dict[str, Any]:
        return {
            "type_imports": [
                {"alias": alias, "qualified_type": qualified}
                for alias, qualified in self.type_imports
            ],
            "explicit_static_imports": [
                {"alias": alias, "qualified_symbol": qualified}
                for alias, qualified in self.explicit_static_imports
            ],
            "static_wildcards": list(self.static_wildcards),
        }


@dataclass(frozen=True, slots=True)
class FlagResolution:
    state: str
    expression: str
    flag_names: tuple[str, ...] = ()
    qualified_symbol: str | None = None
    resolution_kind: str | None = None
    candidates: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "expression": self.expression,
            "flag_names": list(self.flag_names),
            "qualified_symbol": self.qualified_symbol,
            "resolution_kind": self.resolution_kind,
            "candidates": list(self.candidates),
        }


@dataclass(frozen=True, slots=True)
class EvaluatedValue:
    state: str
    value_type: str | None = None
    value: int | bool | str | tuple[int, ...] | None = None
    reason: str | None = None
    trace: tuple[str, ...] = ()

    @classmethod
    def exact(
        cls,
        value_type: str,
        value: int | bool | str | tuple[int, ...],
        *trace: str,
    ) -> "EvaluatedValue":
        return cls("exact", value_type, value, None, tuple(trace))

    @classmethod
    def unknown(cls, reason: str, *trace: str) -> "EvaluatedValue":
        return cls("unknown", None, None, reason, tuple(trace))

    def to_dict(self) -> dict[str, Any]:
        value: Any = list(self.value) if isinstance(self.value, tuple) else self.value
        return {
            "state": self.state,
            "value_type": self.value_type,
            "value": value,
            "reason": self.reason,
            "trace": list(self.trace),
        }


def parse_import_scope(source: str) -> SourceImportScope:
    """Extract one-line Groovy imports from comment-free lexical tokens."""

    tokens = tokenize(source)
    type_imports: set[tuple[str, str]] = set()
    static_imports: set[tuple[str, str]] = set()
    wildcards: set[str] = set()
    cursor = 0
    while cursor < len(tokens):
        if tokens[cursor].value != "import":
            cursor += 1
            continue
        line = tokens[cursor].line
        row: list[Token] = []
        cursor += 1
        while cursor < len(tokens) and tokens[cursor].line == line:
            if tokens[cursor].value == ";":
                cursor += 1
                break
            row.append(tokens[cursor])
            cursor += 1
        is_static = bool(row and row[0].value == "static")
        if is_static:
            row.pop(0)
        alias = None
        if len(row) >= 2 and row[-2].value == "as" and row[-1].kind == "identifier":
            alias = row[-1].value
            row = row[:-2]
        values = [token.value for token in row]
        if not values:
            continue
        wildcard = len(values) >= 2 and values[-2:] == [".", "*"]
        if wildcard:
            values = values[:-2]
        if not values or any(
            value != "." and not value.replace("$", "_").isidentifier()
            for value in values
        ):
            continue
        qualified = "".join(values)
        if not qualified:
            continue
        if is_static and wildcard:
            wildcards.add(qualified)
            type_imports.add((qualified.rsplit(".", 1)[-1], qualified))
        elif is_static and "." in qualified:
            declaring_type, member = qualified.rsplit(".", 1)
            static_imports.add((alias or member, qualified))
            type_imports.add((declaring_type.rsplit(".", 1)[-1], declaring_type))
        elif not wildcard:
            type_imports.add((alias or qualified.rsplit(".", 1)[-1], qualified))
    return SourceImportScope(
        type_imports=tuple(sorted(type_imports)),
        explicit_static_imports=tuple(sorted(static_imports)),
        static_wildcards=tuple(sorted(wildcards)),
    )


def _identifier_parts(tokens: Sequence[Token]) -> tuple[str, ...] | None:
    if not tokens:
        return None
    parts: list[str] = []
    expect_identifier = True
    for token in tokens:
        if expect_identifier and token.kind == "identifier":
            parts.append(token.value)
            expect_identifier = False
        elif not expect_identifier and token.value in {".", "?."}:
            expect_identifier = True
        else:
            return None
    return tuple(parts) if parts and not expect_identifier else None


def _qualified_candidates(
    parts: tuple[str, ...],
    scope: SourceImportScope,
) -> tuple[str, ...]:
    type_imports = dict(scope.type_imports)
    explicit = dict(scope.explicit_static_imports)
    candidates: set[str] = set()
    if len(parts) == 1:
        if parts[0] in explicit:
            candidates.add(explicit[parts[0]])
        candidates.update(f"{owner}.{parts[0]}" for owner in scope.static_wildcards)
    else:
        first = type_imports.get(parts[0])
        candidates.add(".".join((first, *parts[1:])) if first else ".".join(parts))
    return tuple(sorted(candidates))


def resolve_flag_expression(
    expression: str,
    semantics: MaterialSemanticsPolicy,
    scope: SourceImportScope,
) -> FlagResolution:
    tokens = tokenize(expression)
    if len(tokens) == 1 and tokens[0].kind == "string":
        literal = literal_string(tokens[0])
        if literal is None:
            return FlagResolution("unknown", expression)
        matches = tuple(
            sorted(
                name
                for name in semantics.flags_by_name()
                if name.casefold() == literal.casefold()
            )
        )
        if len(matches) == 1:
            return FlagResolution(
                "exact",
                expression,
                matches,
                resolution_kind="runtime-name-string",
            )
        return FlagResolution(
            "ambiguous" if matches else "unknown",
            expression,
            candidates=matches,
        )
    parts = _identifier_parts(tokens)
    if parts is None:
        return FlagResolution("unknown", expression)
    candidates = _qualified_candidates(parts, scope)
    flags = semantics.flags_by_qualified_symbol()
    presets = semantics.presets_by_qualified_symbol()
    resolved: list[tuple[str, tuple[str, ...], str]] = []
    for candidate in candidates:
        if candidate in flags:
            resolved.append((candidate, (flags[candidate].name,), "atomic"))
        if candidate in presets:
            resolved.append((candidate, presets[candidate].flag_names, "preset"))
    if len(resolved) == 1:
        qualified, names, kind = resolved[0]
        return FlagResolution("exact", expression, names, qualified, kind)
    return FlagResolution(
        "ambiguous" if resolved else "unknown",
        expression,
        candidates=tuple(item[0] for item in resolved) or candidates,
    )


def _strip_parentheses(tokens: tuple[Token, ...]) -> tuple[Token, ...]:
    while len(tokens) >= 2 and tokens[0].value == "(" and tokens[-1].value == ")":
        depth = 0
        closes_at_end = False
        for index, token in enumerate(tokens):
            if token.value == "(":
                depth += 1
            elif token.value == ")":
                depth -= 1
                if depth == 0:
                    closes_at_end = index == len(tokens) - 1
                    break
        if not closes_at_end:
            break
        tokens = tokens[1:-1]
    return tokens


def _top_level_operator(
    tokens: tuple[Token, ...], operators: set[str]
) -> int | None:
    depth = 0
    result = None
    for index, token in enumerate(tokens):
        if token.value in {"(", "[", "{"}:
            depth += 1
        elif token.value in {")",
            "]",
            "}",
        }:
            depth -= 1
        elif depth == 0 and token.value in operators and index > 0:
            result = index
    return result


def _resolve_constant_reference(
    tokens: tuple[Token, ...],
    semantics: MaterialSemanticsPolicy,
    scope: SourceImportScope,
) -> EvaluatedValue:
    parts = _identifier_parts(tokens)
    if parts is None:
        return EvaluatedValue.unknown("expression-not-a-constant-reference")
    candidates = _qualified_candidates(parts, scope)
    constants = semantics.constants_by_qualified_symbol()
    matches = [constants[candidate] for candidate in candidates if candidate in constants]
    if len(matches) == 1:
        constant = matches[0]
        return EvaluatedValue.exact(
            constant.value_type,
            constant.value,
            f"constant:{constant.qualified_symbol}",
        )
    material_symbols = semantics.material_symbols_by_qualified_symbol()
    material_matches = [
        material_symbols[candidate]
        for candidate in candidates
        if candidate in material_symbols
    ]
    if len(material_matches) == 1:
        material = material_matches[0]
        return EvaluatedValue.exact(
            "material-reference",
            material.resource_location,
            f"material-symbol:{material.qualified_symbol}",
        )
    if len(matches) != 1:
        return EvaluatedValue.unknown(
            "symbol-reference-ambiguous"
            if matches or material_matches
            else "symbol-reference-unresolved",
            *candidates,
        )
    raise AssertionError("unreachable material expression resolution")


def evaluate_expression(
    expression: str,
    semantics: MaterialSemanticsPolicy,
    scope: SourceImportScope,
    variables: Mapping[str, EvaluatedValue] | None = None,
) -> EvaluatedValue:
    """Evaluate a deliberately small, side-effect-free expression subset."""

    return _evaluate_tokens(
        _strip_parentheses(tokenize(expression)),
        semantics,
        scope,
        {} if variables is None else variables,
    )


def _evaluate_tokens(
    tokens: tuple[Token, ...],
    semantics: MaterialSemanticsPolicy,
    scope: SourceImportScope,
    variables: Mapping[str, EvaluatedValue],
) -> EvaluatedValue:
    tokens = _strip_parentheses(tokens)
    if not tokens:
        return EvaluatedValue.unknown("empty-expression")
    operator = _top_level_operator(tokens, {"+", "-"})
    if operator is not None:
        left = _evaluate_tokens(tokens[:operator], semantics, scope, variables)
        right = _evaluate_tokens(tokens[operator + 1 :], semantics, scope, variables)
        if left.state != "exact" or right.state != "exact":
            return EvaluatedValue.unknown(
                "binary-operand-unresolved", *left.trace, *right.trace
            )
        operation = tokens[operator].value
        if operation == "+" and (
            left.value_type == "string" or right.value_type == "string"
        ):
            return EvaluatedValue.exact(
                "string",
                str(left.value) + str(right.value),
                *left.trace,
                *right.trace,
                "operator:+",
            )
        if left.value_type == right.value_type == "int":
            value = int(left.value) + int(right.value) if operation == "+" else int(left.value) - int(right.value)
            return EvaluatedValue.exact(
                "int", value, *left.trace, *right.trace, f"operator:{operation}"
            )
        return EvaluatedValue.unknown("binary-type-mismatch")

    if tokens[0].value in {"+", "-"} and len(tokens) > 1:
        child = _evaluate_tokens(tokens[1:], semantics, scope, variables)
        if child.state == "exact" and child.value_type == "int":
            value = int(child.value)
            return EvaluatedValue.exact(
                "int",
                -value if tokens[0].value == "-" else value,
                *child.trace,
                f"unary:{tokens[0].value}",
            )
        return EvaluatedValue.unknown("unary-operand-unresolved", *child.trace)

    if len(tokens) == 1:
        token = tokens[0]
        if token.kind == "number":
            raw = token.value.replace("_", "")
            while raw and raw[-1] in "lLiIgG":
                raw = raw[:-1]
            try:
                return EvaluatedValue.exact("int", int(raw, 0), "integer-literal")
            except ValueError:
                return EvaluatedValue.unknown("integer-literal-invalid")
        if token.kind == "string":
            literal = literal_string(token)
            return (
                EvaluatedValue.exact("string", literal, "string-literal")
                if literal is not None
                else EvaluatedValue.unknown("string-literal-dynamic")
            )
        if token.value in {"true", "false"}:
            return EvaluatedValue.exact(
                "boolean", token.value == "true", "boolean-literal"
            )
        if token.kind == "identifier" and token.value in variables:
            return variables[token.value]

    if tokens[-1].value == "]":
        depth = 0
        opening = None
        for index in range(len(tokens) - 1, -1, -1):
            if tokens[index].value == "]":
                depth += 1
            elif tokens[index].value == "[":
                depth -= 1
                if depth == 0:
                    opening = index
                    break
        if opening is not None and opening > 0:
            array = _evaluate_tokens(tokens[:opening], semantics, scope, variables)
            index = _evaluate_tokens(tokens[opening + 1 : -1], semantics, scope, variables)
            if (
                array.state == "exact"
                and array.value_type in {"int-array", "long-array"}
                and index.state == "exact"
                and index.value_type == "int"
                and isinstance(array.value, tuple)
                and 0 <= int(index.value) < len(array.value)
            ):
                return EvaluatedValue.exact(
                    "int",
                    array.value[int(index.value)],
                    *array.trace,
                    *index.trace,
                    "array-index",
                )
            return EvaluatedValue.unknown(
                "array-access-unresolved", *array.trace, *index.trace
            )
    if (
        len(tokens) >= 5
        and tokens[-4].value == "."
        and tokens[-3].value == "toString"
        and tokens[-2].value == "("
        and tokens[-1].value == ")"
    ):
        receiver = _evaluate_tokens(tokens[:-4], semantics, scope, variables)
        if receiver.state == "exact" and receiver.value_type == "material-reference":
            return EvaluatedValue.exact(
                "string",
                str(receiver.value).partition(":")[2],
                *receiver.trace,
                "Material.toString",
            )
        return EvaluatedValue.unknown(
            "material-to-string-receiver-unresolved", *receiver.trace
        )
    return _resolve_constant_reference(tokens, semantics, scope)


def _signed_int32(value: int) -> int:
    normalized = value & 0xFFFFFFFF
    return normalized - 0x100000000 if normalized >= 0x80000000 else normalized


def evaluate_conditional_flag_rule(
    rule: ConditionalFlagRule,
    argument_expressions: Sequence[str],
    property_keys: Sequence[str],
    semantics: MaterialSemanticsPolicy,
    scope: SourceImportScope,
    variables: Mapping[str, EvaluatedValue] | None = None,
) -> dict[str, Any]:
    missing = sorted(set(rule.required_properties) - set(property_keys))
    condition_rows: list[dict[str, Any]] = []
    if missing:
        return {
            "rule_id": rule.rule_id,
            "state": "not-applied",
            "reason": "required-properties-absent",
            "missing_properties": missing,
            "conditions": condition_rows,
            "added_flags": [],
        }
    unknown = False
    false = False
    for condition in rule.conditions:
        if condition.argument_index < len(argument_expressions):
            expression = argument_expressions[condition.argument_index]
            evaluated = evaluate_expression(
                expression, semantics, scope, variables
            )
        elif condition.default is not None:
            expression = None
            value_type = "boolean" if isinstance(condition.default, bool) else "int"
            evaluated = EvaluatedValue.exact(
                value_type, condition.default, "profile-default"
            )
        else:
            expression = None
            evaluated = EvaluatedValue.unknown("argument-missing-without-default")
        actual = evaluated.value
        if (
            evaluated.state == "exact"
            and condition.java_coercion == "signed-int32"
            and evaluated.value_type == "int"
        ):
            actual = _signed_int32(int(actual))
        passed = None
        if evaluated.state == "exact":
            if condition.kind == "boolean-equals" and evaluated.value_type == "boolean":
                passed = actual == condition.expected
            elif (
                condition.kind == "integer-greater-than-or-equal"
                and evaluated.value_type == "int"
            ):
                passed = int(actual) >= int(condition.expected)
        if passed is None:
            unknown = True
        elif not passed:
            false = True
        condition_rows.append(
            {
                "condition": condition.to_dict(),
                "expression": expression,
                "evaluated": evaluated.to_dict(),
                "coerced_value": actual if evaluated.state == "exact" else None,
                "passed": passed,
            }
        )
    state = "not-applied" if false else ("unknown" if unknown else "applied")
    return {
        "rule_id": rule.rule_id,
        "state": state,
        "reason": None,
        "missing_properties": [],
        "conditions": condition_rows,
        "added_flags": list(rule.added_flags) if state == "applied" else [],
    }


__all__ = [
    "EvaluatedValue",
    "FlagResolution",
    "SourceImportScope",
    "evaluate_conditional_flag_rule",
    "evaluate_expression",
    "parse_import_scope",
    "resolve_flag_expression",
]
