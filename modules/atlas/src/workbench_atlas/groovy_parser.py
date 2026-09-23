"""Focused Groovy lexical parser used by the Supersymmetry Atlas profile.

This module intentionally contains no repository scanning, catalog generation, or
validation command. It parses caller-supplied immutable bytes only.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence


PHASE_DIRECTORIES = [
    "classes",
    "globals",
    "material",
    "postInit",
    "preInit",
    "prePostInit",
]
TYPE_KEYWORDS = {"class", "enum", "interface", "record", "trait"}
MODIFIERS = {
    "abstract",
    "final",
    "native",
    "private",
    "protected",
    "public",
    "static",
    "strictfp",
    "synchronized",
    "transient",
    "volatile",
}
CONTROL_KEYWORDS = {
    "assert",
    "case",
    "catch",
    "else",
    "for",
    "if",
    "new",
    "return",
    "switch",
    "throw",
    "try",
    "while",
}


class GroovyParserError(ValueError):
    """Raised when lexical parsing cannot preserve its declared invariants."""


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    start: int
    end: int
    line: int
    column: int


@dataclass
class TypeRegion:
    name: str
    qualified_name: str
    declaration_index: int
    body_start: int
    body_end: int


@dataclass
class CallableRegion:
    name: str
    declaration_index: int
    parameters_start: int
    parameters_end: int
    body_start: int
    body_end: int
    open_paren_index: int


@dataclass
class SourceAnalysis:
    path: str
    source: str
    tokens: list[Token]
    declarations: list[dict[str, str]]
    callables: list[CallableRegion]
    excluded_reference_lines: set[int]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def stable_id(prefix: str, *parts: object) -> str:
    canonical = "\0".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(canonical).hexdigest()[:20]}"


def phase_metadata(path: str, lifecycle: str) -> tuple[str, str, str]:
    parts = path.split("/")
    if len(parts) < 3 or parts[0] != "groovy" or not parts[-1].endswith(".groovy"):
        raise GroovyParserError(f"invalid Groovy source path: {path}")
    phase = parts[1]
    if phase not in PHASE_DIRECTORIES:
        raise GroovyParserError(f"unknown Groovy phase directory: {path}")
    domain_parts = parts[2:-1]
    domain = "/".join(domain_parts) if domain_parts else phase
    return phase, lifecycle, domain


def slashy_can_start(tokens: Sequence[Token]) -> bool:
    if not tokens:
        return True
    previous = tokens[-1]
    return previous.value in {"(", "[", "{", ",", "=", ":", "return", "case", "->"}


def tokenize(source: str, path: str = "<memory>") -> list[Token]:
    tokens: list[Token] = []
    index = 0
    line = 1
    column = 1
    length = len(source)

    def advance(text: str) -> None:
        nonlocal line, column
        newline_count = text.count("\n")
        if newline_count:
            line += newline_count
            column = len(text.rsplit("\n", 1)[-1]) + 1
        else:
            column += len(text)

    while index < length:
        character = source[index]
        if character.isspace():
            advance(character)
            index += 1
            continue

        if source.startswith("//", index):
            end = source.find("\n", index + 2)
            end = length if end == -1 else end
            advance(source[index:end])
            index = end
            continue

        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end == -1:
                raise GroovyParserError(f"unterminated block comment in {path}:{line}")
            end += 2
            advance(source[index:end])
            index = end
            continue

        token_line = line
        token_column = column
        token_start = index

        if source.startswith("$/", index):
            end = source.find("/$", index + 2)
            if end == -1:
                raise GroovyParserError(f"unterminated dollar-slashy string in {path}:{line}")
            end += 2
            raw = source[index:end]
            tokens.append(Token("STRING", raw, index, end, line, column))
            advance(raw)
            index = end
            continue

        if character in {"'", '"'}:
            delimiter = character * 3 if source.startswith(character * 3, index) else character
            cursor = index + len(delimiter)
            escaped = False
            while cursor < length:
                if not escaped and source.startswith(delimiter, cursor):
                    cursor += len(delimiter)
                    break
                current = source[cursor]
                if current == "\\" and not escaped:
                    escaped = True
                else:
                    escaped = False
                cursor += 1
            else:
                raise GroovyParserError(f"unterminated string in {path}:{line}")
            raw = source[index:cursor]
            tokens.append(Token("STRING", raw, index, cursor, line, column))
            advance(raw)
            index = cursor
            continue

        if character == "/" and slashy_can_start(tokens):
            cursor = index + 1
            escaped = False
            while cursor < length:
                current = source[cursor]
                if current == "\n":
                    break
                if current == "/" and not escaped:
                    cursor += 1
                    raw = source[index:cursor]
                    tokens.append(Token("STRING", raw, index, cursor, line, column))
                    advance(raw)
                    index = cursor
                    break
                if current == "\\" and not escaped:
                    escaped = True
                else:
                    escaped = False
                cursor += 1
            if index == token_start:
                # No closing slash was found; treat it as an operator.
                tokens.append(Token("SYMBOL", character, index, index + 1, line, column))
                advance(character)
                index += 1
            continue

        identifier = re.match(r"[A-Za-z_$][A-Za-z0-9_$]*", source[index:])
        if identifier:
            raw = identifier.group(0)
            end = index + len(raw)
            tokens.append(Token("IDENT", raw, index, end, line, column))
            advance(raw)
            index = end
            continue

        number = re.match(
            r"(?:0[xX][0-9A-Fa-f_]+|(?:\d[\d_]*)(?:\.\d[\d_]*)?(?:[eE][+-]?\d[\d_]*)?)[A-Za-z]*",
            source[index:],
        )
        if number:
            raw = number.group(0)
            end = index + len(raw)
            tokens.append(Token("NUMBER", raw, index, end, line, column))
            advance(raw)
            index = end
            continue

        multi = next(
            (
                operator
                for operator in (
                    "..<",
                    "<=>",
                    "===",
                    "!==",
                    "?.",
                    "*.",
                    "->",
                    "::",
                    "..",
                    "==",
                    "!=",
                    "<=",
                    ">=",
                    "&&",
                    "||",
                    "++",
                    "--",
                    "+=",
                    "-=",
                    "*=",
                    "/=",
                    "<<",
                    ">>",
                    "**",
                )
                if source.startswith(operator, index)
            ),
            None,
        )
        if multi:
            value = "." if multi in {"?.", "*."} else multi
            end = index + len(multi)
            tokens.append(Token("SYMBOL", value, index, end, line, column))
            advance(multi)
            index = end
            continue

        tokens.append(
            Token("SYMBOL", character, token_start, token_start + 1, token_line, token_column)
        )
        advance(character)
        index += 1

    return tokens


def bracket_pairs(tokens: Sequence[Token], path: str) -> dict[int, int]:
    opening = {"(": ")", "[": "]", "{": "}"}
    closing = {value: key for key, value in opening.items()}
    stacks: dict[str, list[int]] = {key: [] for key in opening}
    pairs: dict[int, int] = {}
    for index, token in enumerate(tokens):
        if token.value in opening:
            stacks[token.value].append(index)
        elif token.value in closing:
            expected = closing[token.value]
            if not stacks[expected]:
                raise GroovyParserError(
                    f"unmatched {token.value!r} in {path}:{token.line}:{token.column}"
                )
            start = stacks[expected].pop()
            pairs[start] = index
            pairs[index] = start
    leftovers = [tokens[index] for stack in stacks.values() for index in stack]
    if leftovers:
        token = sorted(leftovers, key=lambda item: item.start)[0]
        raise GroovyParserError(
            f"unmatched {token.value!r} in {path}:{token.line}:{token.column}"
        )
    return pairs


def tokens_by_line(tokens: Sequence[Token]) -> dict[int, list[int]]:
    output: dict[int, list[int]] = defaultdict(list)
    for index, token in enumerate(tokens):
        output[token.line].append(index)
    return output


def split_arguments(
    tokens: Sequence[Token], start: int, end: int
) -> list[list[int]]:
    if start >= end:
        return []
    output: list[list[int]] = []
    current: list[int] = []
    depth = Counter()
    for index in range(start, end):
        value = tokens[index].value
        if value in {"(", "[", "{"}:
            depth[value] += 1
        elif value == ")":
            depth["("] -= 1
        elif value == "]":
            depth["["] -= 1
        elif value == "}":
            depth["{"] -= 1
        if value == "," and not any(depth.values()):
            output.append(current)
            current = []
        else:
            current.append(index)
    output.append(current)
    return output


def source_slice(source: str, tokens: Sequence[Token], indices: Sequence[int]) -> str:
    if not indices:
        return ""
    return source[tokens[indices[0]].start : tokens[indices[-1]].end]


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def decode_string(raw: str) -> tuple[str, bool]:
    if raw.startswith("$/") and raw.endswith("/$"):
        value = raw[2:-2]
        return value, "$" in value
    if raw.startswith("/") and raw.endswith("/"):
        return raw[1:-1].replace("\\/", "/"), False
    delimiter = raw[:3] if raw.startswith(("'''", '\"\"\"')) else raw[:1]
    value = raw[len(delimiter) : -len(delimiter)]
    interpolated = delimiter.startswith('"') and bool(re.search(r"(?<!\\)\$", value))
    replacements = {
        "\\n": "\n",
        "\\r": "\r",
        "\\t": "\t",
        "\\\\": "\\",
        "\\'": "'",
        '\\"': '"',
    }
    for escaped, replacement in replacements.items():
        value = value.replace(escaped, replacement)
    return value, interpolated


def expression_value(
    source: str,
    tokens: Sequence[Token],
    indices: Sequence[int],
) -> tuple[str, str, str, str]:
    if not indices:
        raw = ""
        return "", sha256_bytes(raw.encode("utf-8")), "", "missing-expression"
    raw = source_slice(source, tokens, indices)
    preview = normalize_whitespace(raw)
    if len(preview) > 240:
        preview = preview[:237] + "..."
    digest = sha256_bytes(raw.encode("utf-8"))
    values = [tokens[index] for index in indices]
    if len(values) == 1 and values[0].kind == "STRING":
        normalized, interpolated = decode_string(values[0].value)
        return (
            preview,
            digest,
            normalized,
            "interpolated-literal" if interpolated else "exact-literal",
        )
    if len(values) == 1 and values[0].kind == "NUMBER":
        return preview, digest, values[0].value, "exact-number"
    if len(values) == 1 and values[0].kind == "IDENT":
        return preview, digest, values[0].value, "identifier-expression"
    if all(
        token.kind == "IDENT" or token.value == "."
        for token in values
    ) and values[0].kind == "IDENT" and values[-1].kind == "IDENT":
        return preview, digest, "".join(token.value for token in values), "qualified-expression"
    if any(token.value == "+" for token in values):
        return preview, digest, "", "concatenated-expression"
    return preview, digest, "", "complex-expression"



def parse_package_and_import_lines(
    path: str,
    tokens: Sequence[Token],
) -> tuple[str, set[int]]:
    """Return the declared package and lines excluded from call-site parsing."""

    package_name = ""
    excluded_lines: set[int] = set()
    by_line = tokens_by_line(tokens)
    for line, indices in sorted(by_line.items()):
        line_tokens = [tokens[index] for index in indices]
        if not line_tokens or line_tokens[0].kind != "IDENT":
            continue
        keyword = line_tokens[0].value
        if keyword == "package":
            excluded_lines.add(line)
            target_parts = [
                token.value
                for token in line_tokens[1:]
                if token.kind == "IDENT" or token.value == "."
            ]
            value = "".join(target_parts).rstrip(".")
            if package_name and package_name != value:
                raise GroovyParserError(f"multiple package declarations in {path}")
            package_name = value
            continue
        if keyword != "import":
            continue

        excluded_lines.add(line)
        cursor = 1
        if cursor < len(line_tokens) and line_tokens[cursor].value == "static":
            cursor += 1
        target_tokens: list[Token] = []
        while cursor < len(line_tokens):
            token = line_tokens[cursor]
            if token.value in {";", "as"}:
                break
            if token.kind == "IDENT" or token.value in {".", "*"}:
                target_tokens.append(token)
            cursor += 1
        target = "".join(token.value for token in target_tokens).rstrip(".")
        if not target:
            raise GroovyParserError(f"empty import target in {path}:{line}")
    return package_name, excluded_lines


def containing_region(
    token_index: int,
    regions: Sequence[TypeRegion | CallableRegion],
) -> TypeRegion | CallableRegion | None:
    matches = [
        region
        for region in regions
        if region.body_start < token_index < region.body_end
    ]
    if not matches:
        return None
    return min(matches, key=lambda region: region.body_end - region.body_start)


def modifiers_before(tokens: Sequence[Token], keyword_index: int) -> list[str]:
    line = tokens[keyword_index].line
    values: list[str] = []
    cursor = keyword_index - 1
    while cursor >= 0 and tokens[cursor].line == line:
        if tokens[cursor].value in MODIFIERS:
            values.append(tokens[cursor].value)
            cursor -= 1
            continue
        break
    return list(reversed(values))


def declaration_row(
    *,
    path: str,
    phase_hint: str,
    package_name: str,
    token: Token,
    symbol_kind: str,
    symbol_name: str,
    qualified_hint: str,
    declared_type: str,
    modifiers: Iterable[str],
    enclosing_type: str,
    enclosing_callable: str,
    scope_hint: str,
    sharing_hint: str,
    extraction_state: str,
    notes: str,
) -> dict[str, str]:
    declaration_id = stable_id(
        "GRV-DECL",
        path,
        token.line,
        token.column,
        symbol_kind,
        symbol_name,
    )
    return {
        "declaration_id": declaration_id,
        "path": path,
        "line": str(token.line),
        "column": str(token.column),
        "phase_hint": phase_hint,
        "package_name": package_name,
        "symbol_kind": symbol_kind,
        "symbol_name": symbol_name,
        "qualified_hint": qualified_hint,
        "declared_type": declared_type,
        "modifiers": "|".join(sorted(set(modifiers))),
        "enclosing_type": enclosing_type,
        "enclosing_callable": enclosing_callable,
        "scope_hint": scope_hint,
        "sharing_hint": sharing_hint,
        "extraction_state": extraction_state,
        "notes": notes,
    }


def parameter_parts(
    tokens: Sequence[Token], indices: Sequence[int]
) -> tuple[Token | None, str]:
    if not indices:
        return None, ""
    before_default: list[int] = []
    depth = Counter()
    for index in indices:
        value = tokens[index].value
        if value in {"(", "[", "{"}:
            depth[value] += 1
        elif value == ")":
            depth["("] -= 1
        elif value == "]":
            depth["["] -= 1
        elif value == "}":
            depth["{"] -= 1
        if value == "=" and not any(depth.values()):
            break
        before_default.append(index)
    identifiers = [index for index in before_default if tokens[index].kind == "IDENT"]
    if not identifiers:
        return None, ""
    name_index = identifiers[-1]
    name_token = tokens[name_index]
    type_indices = [index for index in before_default if index < name_index]
    declared_type = normalize_whitespace(
        "".join(tokens[index].value for index in type_indices)
    )
    if declared_type in {"def", "final"}:
        declared_type = ""
    return name_token, declared_type


def find_type_regions(
    path: str,
    phase_hint: str,
    package_name: str,
    tokens: Sequence[Token],
    pairs: dict[int, int],
) -> tuple[list[TypeRegion], list[dict[str, str]], set[int]]:
    raw_regions: list[tuple[int, int, int, str, str, int | None, int | None]] = []
    type_parameter_indices: set[int] = set()
    for index, token in enumerate(tokens):
        if token.kind != "IDENT" or token.value not in TYPE_KEYWORDS:
            continue
        if index + 1 >= len(tokens) or tokens[index + 1].kind != "IDENT":
            continue
        name_index = index + 1
        body_start = None
        open_paren = None
        close_paren = None
        cursor = name_index + 1
        while cursor < len(tokens) and cursor <= name_index + 300:
            value = tokens[cursor].value
            if value == "(" and open_paren is None:
                open_paren = cursor
                close_paren = pairs.get(cursor)
                if close_paren is None:
                    raise GroovyParserError(
                        f"unmatched type parameter list in {path}:{token.line}"
                    )
                cursor = close_paren
            elif value == "{":
                body_start = cursor
                break
            elif value == ";":
                break
            cursor += 1
        if body_start is None or body_start not in pairs:
            raise GroovyParserError(
                f"type declaration has no balanced body in {path}:{token.line}"
            )
        raw_regions.append(
            (
                index,
                body_start,
                pairs[body_start],
                token.value,
                tokens[name_index].value,
                open_paren,
                close_paren,
            )
        )

    regions: list[TypeRegion] = []
    declarations: list[dict[str, str]] = []
    for index, body_start, body_end, kind, name, open_paren, close_paren in sorted(
        raw_regions, key=lambda item: item[0]
    ):
        parents = [
            region
            for region in regions
            if region.body_start < index < region.body_end
        ]
        parent = min(parents, key=lambda region: region.body_end - region.body_start) if parents else None
        qualified = (
            f"{parent.qualified_name}.{name}"
            if parent
            else f"{package_name}.{name}" if package_name else name
        )
        region = TypeRegion(name, qualified, index, body_start, body_end)
        regions.append(region)
        modifiers = modifiers_before(tokens, index)
        sharing = (
            "static-member-candidate"
            if parent and "static" in modifiers
            else "nested-type-candidate"
            if parent
            else "package-type"
        )
        declarations.append(
            declaration_row(
                path=path,
                phase_hint=phase_hint,
                package_name=package_name,
                token=tokens[index + 1],
                symbol_kind=f"type-{kind}",
                symbol_name=name,
                qualified_hint=qualified,
                declared_type=kind,
                modifiers=modifiers,
                enclosing_type=parent.qualified_name if parent else "",
                enclosing_callable="",
                scope_hint="type-member" if parent else "package",
                sharing_hint=sharing,
                extraction_state="lexical-exact",
                notes="type keyword and balanced body; no runtime loading claim",
            )
        )
        if kind == "record" and open_paren is not None and close_paren is not None:
            for component in split_arguments(tokens, open_paren + 1, close_paren):
                name_token, declared_type = parameter_parts(tokens, component)
                if name_token is None:
                    continue
                type_parameter_indices.update(component)
                declarations.append(
                    declaration_row(
                        path=path,
                        phase_hint=phase_hint,
                        package_name=package_name,
                        token=name_token,
                        symbol_kind="record-component",
                        symbol_name=name_token.value,
                        qualified_hint=f"{qualified}.{name_token.value}",
                        declared_type=declared_type,
                        modifiers=[],
                        enclosing_type=qualified,
                        enclosing_callable=name,
                        scope_hint="record-component",
                        sharing_hint="instance-member",
                        extraction_state="lexical-exact",
                        notes="record parameter syntax only",
                    )
                )
    return regions, declarations, type_parameter_indices


def line_prefix_indices(
    tokens: Sequence[Token], name_index: int
) -> list[int]:
    line = tokens[name_index].line
    start = name_index
    while (
        start > 0
        and tokens[start - 1].line == line
        and tokens[start - 1].value not in {"{", "}", ";"}
    ):
        start -= 1
    return list(range(start, name_index))


def find_callable_regions(
    path: str,
    phase_hint: str,
    package_name: str,
    source: str,
    tokens: Sequence[Token],
    pairs: dict[int, int],
    types: Sequence[TypeRegion],
) -> tuple[list[CallableRegion], list[dict[str, str]], set[int], set[int]]:
    regions: list[CallableRegion] = []
    declarations: list[dict[str, str]] = []
    parameter_indices: set[int] = set()
    declaration_parens: set[int] = set()
    for open_index, token in enumerate(tokens):
        if token.value != "(" or open_index not in pairs or open_index == 0:
            continue
        name_index = open_index - 1
        name_token = tokens[name_index]
        if name_token.kind != "IDENT":
            continue
        if name_index > 0 and tokens[name_index - 1].value == ".":
            continue
        name = name_token.value
        if name in CONTROL_KEYWORDS or name in TYPE_KEYWORDS:
            continue
        prefix_indices = line_prefix_indices(tokens, name_index)
        prefix_values = [tokens[index].value for index in prefix_indices]
        if any(
            value in {"=", ".", "->", "(", ")", "{"}
            for value in prefix_values
        ):
            continue
        non_modifiers = [value for value in prefix_values if value not in MODIFIERS]
        containing_type = containing_region(name_index, types)
        is_constructor = bool(
            isinstance(containing_type, TypeRegion)
            and name == containing_type.name
            and not non_modifiers
        )
        is_def_method = bool(non_modifiers and non_modifiers[0] == "def")
        is_typed_method = bool(
            non_modifiers
            and non_modifiers[0] not in CONTROL_KEYWORDS | TYPE_KEYWORDS | {"new", "@"}
        )
        if not (is_constructor or is_def_method or is_typed_method):
            continue
        close_index = pairs[open_index]
        body_start = None
        cursor = close_index + 1
        while cursor < len(tokens) and cursor <= close_index + 30:
            if tokens[cursor].value == "{":
                body_start = cursor
                break
            if tokens[cursor].value in {";", "}", ")", "]"}:
                break
            if tokens[cursor].line > tokens[close_index].line + 2:
                break
            cursor += 1
        if body_start is None or body_start not in pairs:
            continue
        modifiers = [value for value in prefix_values if value in MODIFIERS]
        return_type_indices = [
            index
            for index in prefix_indices
            if tokens[index].value not in MODIFIERS and tokens[index].value != "def"
        ]
        declared_type = normalize_whitespace(
            source_slice(source, tokens, return_type_indices)
        )
        symbol_kind = "constructor" if is_constructor else "method"
        enclosing_type_name = (
            containing_type.qualified_name
            if isinstance(containing_type, TypeRegion)
            else ""
        )
        qualified = (
            f"{enclosing_type_name}.{name}"
            if enclosing_type_name
            else f"{package_name}.{name}" if package_name else name
        )
        sharing = (
            "static-member-candidate"
            if "static" in modifiers
            else "instance-member" if enclosing_type_name else "script-binding-candidate"
        )
        declarations.append(
            declaration_row(
                path=path,
                phase_hint=phase_hint,
                package_name=package_name,
                token=name_token,
                symbol_kind=symbol_kind,
                symbol_name=name,
                qualified_hint=qualified,
                declared_type=declared_type,
                modifiers=modifiers,
                enclosing_type=enclosing_type_name,
                enclosing_callable="",
                scope_hint="type-member" if enclosing_type_name else "script-top-level",
                sharing_hint=sharing,
                extraction_state="lexical-exact",
                notes="callable signature and balanced body; dispatch unverified",
            )
        )
        region = CallableRegion(
            name,
            name_index,
            open_index + 1,
            close_index,
            body_start,
            pairs[body_start],
            open_index,
        )
        regions.append(region)
        declaration_parens.add(open_index)
        for parameter in split_arguments(tokens, open_index + 1, close_index):
            name_parameter, parameter_type = parameter_parts(tokens, parameter)
            if name_parameter is None:
                continue
            parameter_indices.update(parameter)
            declarations.append(
                declaration_row(
                    path=path,
                    phase_hint=phase_hint,
                    package_name=package_name,
                    token=name_parameter,
                    symbol_kind="parameter",
                    symbol_name=name_parameter.value,
                    qualified_hint=f"{qualified}({name_parameter.value})",
                    declared_type=parameter_type,
                    modifiers=[],
                    enclosing_type=enclosing_type_name,
                    enclosing_callable=qualified,
                    scope_hint="parameter",
                    sharing_hint="parameter-only",
                    extraction_state="lexical-exact",
                    notes="callable parameter syntax only",
                )
            )
    return regions, declarations, parameter_indices, declaration_parens


def find_closure_parameters(
    path: str,
    phase_hint: str,
    package_name: str,
    tokens: Sequence[Token],
    pairs: dict[int, int],
    types: Sequence[TypeRegion],
    callables: Sequence[CallableRegion],
) -> tuple[list[dict[str, str]], set[int]]:
    declarations: list[dict[str, str]] = []
    parameter_indices: set[int] = set()
    for open_index, token in enumerate(tokens):
        if token.value != "{" or open_index not in pairs:
            continue
        close_index = pairs[open_index]
        arrow_index = None
        depth = Counter()
        cursor = open_index + 1
        while cursor < close_index:
            value = tokens[cursor].value
            if value in {"(", "[", "{"}:
                depth[value] += 1
            elif value == ")":
                depth["("] -= 1
            elif value == "]":
                depth["["] -= 1
            elif value == "}":
                depth["{"] -= 1
            if value == "->" and not any(depth.values()):
                arrow_index = cursor
                break
            # A closure parameter arrow belongs at the opening of the body.
            # Stop once a statement terminator or a second body line is reached.
            if value == ";" or tokens[cursor].line > token.line + 2:
                break
            cursor += 1
        if arrow_index is None:
            continue
        enclosing_type, enclosing_callable = enclosing_names(
            open_index, types, callables
        )
        closure_hint = (
            f"{enclosing_callable or enclosing_type or package_name or path}"
            f"::<closure@{token.line}:{token.column}>"
        )
        for parameter in split_arguments(tokens, open_index + 1, arrow_index):
            name_token, declared_type = parameter_parts(tokens, parameter)
            if name_token is None:
                continue
            parameter_indices.update(parameter)
            declarations.append(
                declaration_row(
                    path=path,
                    phase_hint=phase_hint,
                    package_name=package_name,
                    token=name_token,
                    symbol_kind="parameter",
                    symbol_name=name_token.value,
                    qualified_hint=f"{closure_hint}({name_token.value})",
                    declared_type=declared_type,
                    modifiers=[],
                    enclosing_type=enclosing_type,
                    enclosing_callable=closure_hint,
                    scope_hint="closure-parameter",
                    sharing_hint="parameter-only",
                    extraction_state="lexical-exact",
                    notes="closure parameter before lexical arrow; invocation unverified",
                )
            )
    return declarations, parameter_indices


def brace_depths(tokens: Sequence[Token]) -> list[int]:
    output: list[int] = []
    depth = 0
    for token in tokens:
        output.append(depth)
        if token.value == "{":
            depth += 1
        elif token.value == "}":
            depth -= 1
    return output


def find_variable_declarations(
    path: str,
    phase_hint: str,
    package_name: str,
    source: str,
    tokens: Sequence[Token],
    types: Sequence[TypeRegion],
    callables: Sequence[CallableRegion],
    excluded_lines: set[int],
    excluded_indices: set[int],
    existing_locations: set[tuple[int, int]],
) -> list[dict[str, str]]:
    declarations: list[dict[str, str]] = []
    by_line = tokens_by_line(tokens)
    depths = brace_depths(tokens)
    for line, all_indices in sorted(by_line.items()):
        if line in excluded_lines:
            continue
        indices = [index for index in all_indices if index not in excluded_indices]
        if not indices:
            continue
        first = 0
        while first < len(indices) and tokens[indices[first]].value in {"}", ";"}:
            first += 1
        if first >= len(indices):
            continue
        indices = indices[first:]
        values = [tokens[index].value for index in indices]
        if values[0] in CONTROL_KEYWORDS | TYPE_KEYWORDS | {"import", "package", "@", "new"}:
            continue
        top_level_equals = None
        nesting = Counter()
        for offset, index in enumerate(indices):
            value = tokens[index].value
            if value in {"(", "[", "{"}:
                nesting[value] += 1
            elif value == ")":
                nesting["("] -= 1
            elif value == "]":
                nesting["["] -= 1
            elif value == "}":
                nesting["{"] -= 1
            if value == "=" and not any(nesting.values()):
                top_level_equals = offset
                break
        lhs = indices[:top_level_equals] if top_level_equals is not None else indices
        lhs = [index for index in lhs if tokens[index].value not in {";", ","}]
        if not lhs or any(tokens[index].value == "(" for index in lhs):
            continue
        modifiers: list[str] = []
        cursor = 0
        while cursor < len(lhs) and tokens[lhs[cursor]].value in MODIFIERS:
            modifiers.append(tokens[lhs[cursor]].value)
            cursor += 1
        remaining = lhs[cursor:]
        is_def = bool(remaining and tokens[remaining[0]].value == "def")
        if is_def:
            remaining = remaining[1:]
        identifier_indices = [index for index in remaining if tokens[index].kind == "IDENT"]
        assignment_only = False
        if is_def and identifier_indices:
            name_index = identifier_indices[0]
            type_indices: list[int] = []
        elif len(identifier_indices) >= 2:
            name_index = identifier_indices[-1]
            type_indices = [index for index in remaining if index < name_index]
            if not remaining or remaining[-1] != name_index:
                continue
            allowed_type_symbols = {".", "<", ">", "?", "[", "]", ",", "*"}
            if any(
                tokens[index].kind != "IDENT"
                and tokens[index].value not in allowed_type_symbols
                for index in type_indices
            ):
                continue
            first_type = next(
                (tokens[index].value for index in type_indices if tokens[index].kind == "IDENT"),
                "",
            )
            primitive_or_inferred = {
                "boolean",
                "byte",
                "char",
                "double",
                "float",
                "int",
                "long",
                "short",
                "var",
                "void",
            }
            if not (
                first_type in primitive_or_inferred
                or first_type[:1].isupper()
                or any(tokens[index].value == "." for index in type_indices)
            ):
                continue
            previous_index = name_index - 1
            separating_text = (
                source[tokens[previous_index].end : tokens[name_index].start]
                if previous_index >= 0
                else ""
            )
            if not separating_text or not separating_text.isspace():
                # A lexical member or index assignment (for example, obj.field =)
                # is not a variable declaration. Qualified types still have
                # whitespace between their final type token and the name.
                continue
        elif (
            len(identifier_indices) == 1
            and modifiers
            and top_level_equals is not None
        ):
            name_index = identifier_indices[0]
            type_indices = []
        elif (
            len(identifier_indices) == 1
            and top_level_equals is not None
            and depths[identifier_indices[0]] == 0
        ):
            name_index = identifier_indices[0]
            type_indices = []
            assignment_only = True
        else:
            continue
        name_token = tokens[name_index]
        if (name_token.line, name_token.column) in existing_locations:
            continue
        declared_type = normalize_whitespace(source_slice(source, tokens, type_indices))
        containing_type = containing_region(name_index, types)
        containing_callable = containing_region(name_index, callables)
        if isinstance(containing_callable, CallableRegion):
            scope = "callable-local"
            enclosing_callable = containing_callable.name
        elif isinstance(containing_type, TypeRegion):
            scope = "type-member"
            enclosing_callable = ""
        elif depths[name_index] == 0:
            scope = "script-top-level"
            enclosing_callable = ""
        else:
            scope = "script-block-local"
            enclosing_callable = ""
        enclosing_type_name = (
            containing_type.qualified_name
            if isinstance(containing_type, TypeRegion)
            else ""
        )
        if assignment_only:
            symbol_kind = "binding-assignment"
            extraction_state = "lexical-candidate"
        elif scope == "type-member":
            symbol_kind = "field"
            extraction_state = "lexical-exact"
        else:
            symbol_kind = "variable"
            extraction_state = "lexical-exact" if is_def or declared_type else "lexical-candidate"
        if scope == "type-member":
            sharing = (
                "static-member-candidate"
                if "static" in modifiers
                else "instance-member"
            )
        elif scope == "script-top-level":
            sharing = "script-binding-candidate"
        else:
            sharing = "local-only"
        qualified = (
            f"{enclosing_type_name}.{name_token.value}"
            if enclosing_type_name
            else f"{package_name}.{name_token.value}" if package_name else name_token.value
        )
        declarations.append(
            declaration_row(
                path=path,
                phase_hint=phase_hint,
                package_name=package_name,
                token=name_token,
                symbol_kind=symbol_kind,
                symbol_name=name_token.value,
                qualified_hint=qualified,
                declared_type=declared_type,
                modifiers=modifiers,
                enclosing_type=enclosing_type_name,
                enclosing_callable=enclosing_callable,
                scope_hint=scope,
                sharing_hint=sharing,
                extraction_state=extraction_state,
                notes=(
                    "untyped top-level assignment; binding semantics unverified"
                    if assignment_only
                    else "explicit lexical declaration; runtime visibility unverified"
                ),
            )
        )
    return declarations


def member_chain(tokens: Sequence[Token], name_index: int) -> str:
    """Return the dotted identifier suffix ending at ``name_index``."""

    start = name_index
    while (
        start >= 2
        and tokens[start - 1].value == "."
        and tokens[start - 2].kind == "IDENT"
    ):
        start -= 2
    return "".join(token.value for token in tokens[start : name_index + 1])


def enclosing_names(
    token_index: int,
    types: Sequence[TypeRegion],
    callables: Sequence[CallableRegion],
) -> tuple[str, str]:
    type_region = containing_region(token_index, types)
    callable_region = containing_region(token_index, callables)
    enclosing_type = (
        type_region.qualified_name if isinstance(type_region, TypeRegion) else ""
    )
    enclosing_callable = (
        callable_region.name
        if isinstance(callable_region, CallableRegion)
        else ""
    )
    return enclosing_type, enclosing_callable



def analyze_bytes(
    file_row: dict[str, str],
    source_bytes: bytes,
) -> SourceAnalysis:
    """Parse immutable caller-supplied Groovy bytes."""

    path = file_row["path"]
    _, phase_hint, _ = phase_metadata(path, file_row["lifecycle"])
    try:
        source = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GroovyParserError(f"Groovy source is not UTF-8: {path}: {exc}") from exc
    tokens = tokenize(source, path)
    pairs = bracket_pairs(tokens, path)
    package_name, excluded_lines = parse_package_and_import_lines(path, tokens)
    types, type_declarations, type_parameter_indices = find_type_regions(
        path, phase_hint, package_name, tokens, pairs
    )
    callables, callable_declarations, parameter_indices, _ = find_callable_regions(
        path,
        phase_hint,
        package_name,
        source,
        tokens,
        pairs,
        types,
    )
    closure_declarations, closure_parameter_indices = find_closure_parameters(
        path,
        phase_hint,
        package_name,
        tokens,
        pairs,
        types,
        callables,
    )
    declarations = type_declarations + callable_declarations + closure_declarations
    existing_locations = {
        (int(item["line"]), int(item["column"])) for item in declarations
    }
    declarations.extend(
        find_variable_declarations(
            path,
            phase_hint,
            package_name,
            source,
            tokens,
            types,
            callables,
            excluded_lines,
            type_parameter_indices | parameter_indices | closure_parameter_indices,
            existing_locations,
        )
    )
    declarations.sort(
        key=lambda item: (
            item["path"].encode("utf-8"),
            int(item["line"]),
            int(item["column"]),
            item["symbol_kind"].encode("utf-8"),
            item["symbol_name"].encode("utf-8"),
        )
    )
    return SourceAnalysis(
        path=path,
        source=source,
        tokens=tokens,
        declarations=declarations,
        callables=callables,
        excluded_reference_lines=excluded_lines,
    )
