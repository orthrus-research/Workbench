"""Bounded, comment-aware Groovy lexical utilities.

This is deliberately not a Groovy parser.  It extracts a conservative call
surface without evaluating source or pretending to understand arbitrary
metaprogramming.  Every consumer must retain that static-candidate boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class Token:
    kind: str
    value: str
    start: int
    end: int
    line: int
    column: int


@dataclass(frozen=True, slots=True)
class Argument:
    tokens: tuple[Token, ...]

    @property
    def expression(self) -> str:
        return normalize_tokens(self.tokens)

    def integer(self) -> int | None:
        values = list(self.tokens)
        sign = 1
        if values and values[0].value in {"+", "-"}:
            sign = -1 if values.pop(0).value == "-" else 1
        if len(values) != 1 or values[0].kind != "number":
            return None
        raw = values[0].value.replace("_", "")
        while raw and raw[-1] in "lLiIgG":
            raw = raw[:-1]
        try:
            return sign * int(raw, 0)
        except ValueError:
            return None

    def exact_string(self) -> str | None:
        if len(self.tokens) != 1 or self.tokens[0].kind != "string":
            return None
        return literal_string(self.tokens[0])

    def first_string(self) -> str | None:
        for token in self.tokens:
            if token.kind == "string":
                return literal_string(token)
        return None

    def wrapped_string(self) -> str | None:
        """Return one literal optionally wrapped by a simple constructor/call.

        Operators, multiple arguments, interpolation, and concatenation make
        the identity dynamic and therefore unresolved.
        """

        if (exact := self.exact_string()) is not None:
            return exact
        strings = [token for token in self.tokens if token.kind == "string"]
        if len(strings) != 1:
            return None
        allowed_symbols = {".", "?.", "(", ")"}
        if any(
            token.kind not in {"identifier", "string"}
            and token.value not in allowed_symbols
            for token in self.tokens
        ):
            return None
        if not any(token.value == "(" for token in self.tokens):
            return None
        return literal_string(strings[0])


@dataclass(frozen=True, slots=True)
class Call:
    callee: str
    terminal: str
    constructor: bool
    arguments: tuple[Argument, ...]
    start: int
    end: int
    line: int
    column: int
    token_start: int
    token_end: int
    closure_form: bool = False

    @property
    def qualifier(self) -> str | None:
        if "." not in self.callee:
            return None
        return self.callee.rsplit(".", 1)[0]


_MULTI_SYMBOLS = tuple(
    sorted(
        {
            "<=>",
            "===",
            "!==",
            "==~",
            "=~",
            "?.",
            "*.",
            ".@",
            ".&",
            "::",
            "->",
            "..",
            "..<",
            "**",
            "++",
            "--",
            "&&",
            "||",
            "==",
            "!=",
            "<=",
            ">=",
            "<<",
            ">>",
            "+=",
            "-=",
            "*=",
            "/=",
            "%=",
            "?:",
        },
        key=len,
        reverse=True,
    )
)


def tokenize(source: str) -> tuple[Token, ...]:
    """Tokenize enough Groovy syntax to find bounded calls and declarations."""

    tokens: list[Token] = []
    index = 0
    line = 1
    column = 1
    length = len(source)

    def advance(end: int) -> None:
        nonlocal index, line, column
        segment = source[index:end]
        newlines = segment.count("\n")
        if newlines:
            line += newlines
            column = len(segment.rsplit("\n", 1)[-1]) + 1
        else:
            column += len(segment)
        index = end

    while index < length:
        character = source[index]
        if character.isspace():
            advance(index + 1)
            continue
        if source.startswith("#!", index) and index == 0:
            end = source.find("\n", index)
            advance(length if end < 0 else end)
            continue
        if source.startswith("//", index):
            end = source.find("\n", index + 2)
            advance(length if end < 0 else end)
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            advance(length if end < 0 else end + 2)
            continue
        if character in {"'", '"'}:
            start = index
            start_line = line
            start_column = column
            delimiter = character * 3 if source.startswith(character * 3, index) else character
            cursor = index + len(delimiter)
            while cursor < length:
                if source.startswith(delimiter, cursor):
                    cursor += len(delimiter)
                    break
                if source[cursor] == "\\":
                    cursor = min(length, cursor + 2)
                else:
                    cursor += 1
            raw = source[start:cursor]
            tokens.append(
                Token("string", raw, start, cursor, start_line, start_column)
            )
            advance(cursor)
            continue
        if character.isalpha() or character in {"_", "$"}:
            start = index
            start_line = line
            start_column = column
            cursor = index + 1
            while cursor < length and (
                source[cursor].isalnum() or source[cursor] in {"_", "$"}
            ):
                cursor += 1
            tokens.append(
                Token(
                    "identifier",
                    source[start:cursor],
                    start,
                    cursor,
                    start_line,
                    start_column,
                )
            )
            advance(cursor)
            continue
        if character.isdigit():
            start = index
            start_line = line
            start_column = column
            cursor = index + 1
            while cursor < length and (
                source[cursor].isalnum() or source[cursor] in {"_", "."}
            ):
                cursor += 1
            tokens.append(
                Token(
                    "number",
                    source[start:cursor],
                    start,
                    cursor,
                    start_line,
                    start_column,
                )
            )
            advance(cursor)
            continue
        symbol = next(
            (candidate for candidate in _MULTI_SYMBOLS if source.startswith(candidate, index)),
            character,
        )
        tokens.append(Token("symbol", symbol, index, index + len(symbol), line, column))
        advance(index + len(symbol))
    return tuple(tokens)


def literal_string(token: Token) -> str | None:
    """Return a bounded non-interpolated string literal value when available."""

    raw = token.value
    if len(raw) < 2:
        return None
    delimiter = raw[:3] if raw.startswith(("'''", '\"\"\"')) else raw[0]
    if not raw.endswith(delimiter) or len(raw) < len(delimiter) * 2:
        return None
    inner = raw[len(delimiter) : -len(delimiter)]
    if delimiter.startswith('"') and "$" in inner:
        return None
    # Registry and recipe identities in the supported profiles are simple
    # ASCII literals.  Avoid claiming Groovy escape semantics for harder cases.
    if "\\" in inner:
        return None
    if any(character in inner for character in "\r\n\x00"):
        return None
    return inner


def normalize_tokens(tokens: Iterable[Token]) -> str:
    values = [token.value for token in tokens]
    return " ".join(values)


def calls(tokens: tuple[Token, ...]) -> tuple[Call, ...]:
    """Return ordinary and closure-form call candidates in source order."""

    parens: dict[int, int] = {}
    stack: list[int] = []
    for position, token in enumerate(tokens):
        if token.value == "(":
            stack.append(position)
        elif token.value == ")" and stack:
            opening = stack.pop()
            parens[opening] = position

    result: list[Call] = []
    ordinary_starts: set[int] = set()
    for opening, closing in sorted(parens.items()):
        if opening == 0 or tokens[opening - 1].kind != "identifier":
            continue
        chain_start, callee = _callee_before(tokens, opening)
        if not callee:
            continue
        constructor = chain_start > 0 and tokens[chain_start - 1].value == "new"
        start_index = chain_start - 1 if constructor else chain_start
        arguments = _arguments(tokens, opening + 1, closing)
        result.append(
            Call(
                callee=callee,
                terminal=callee.rsplit(".", 1)[-1],
                constructor=constructor,
                arguments=arguments,
                start=tokens[start_index].start,
                end=tokens[closing].end,
                line=tokens[start_index].line,
                column=tokens[start_index].column,
                token_start=start_index,
                token_end=closing,
            )
        )
        ordinary_starts.add(chain_start)

    for brace in range(1, len(tokens)):
        if tokens[brace].value != "{" or tokens[brace - 1].kind != "identifier":
            continue
        chain_start, callee = _callee_before(tokens, brace)
        if not callee or chain_start in ordinary_starts:
            continue
        # Only a qualified closure form is useful here.  This avoids treating
        # control-flow keywords followed by a block as calls.
        if "." not in callee:
            continue
        result.append(
            Call(
                callee=callee,
                terminal=callee.rsplit(".", 1)[-1],
                constructor=False,
                arguments=(),
                start=tokens[chain_start].start,
                end=tokens[brace].end,
                line=tokens[chain_start].line,
                column=tokens[chain_start].column,
                token_start=chain_start,
                token_end=brace,
                closure_form=True,
            )
        )
    return tuple(sorted(result, key=lambda item: (item.start, item.end, item.callee)))


def linked_calls(tokens, call_by_token, builder, terminator="buildAndRegister"):
    """Exact adjacent fluent calls; this does not evaluate or infer control flow."""
    linked = [builder]
    cursor = builder.token_end + 1
    while cursor < len(tokens) and tokens[cursor].value == ".":
        candidate = call_by_token.get(cursor + 1)
        if candidate is None or candidate.qualifier is not None or candidate.closure_form:
            break
        linked.append(candidate)
        if candidate.terminal == terminator:
            break
        cursor = candidate.token_end + 1
    return linked


def _callee_before(tokens: tuple[Token, ...], opening: int) -> tuple[int, str]:
    cursor = opening - 1
    if cursor < 0 or tokens[cursor].kind != "identifier":
        return opening, ""
    parts = [tokens[cursor].value]
    start = cursor
    while cursor >= 2:
        dot = tokens[cursor - 1]
        previous = tokens[cursor - 2]
        if dot.value not in {".", "?.", "*."} or previous.kind != "identifier":
            break
        parts.insert(0, previous.value)
        cursor -= 2
        start = cursor
    return start, ".".join(parts)


def _arguments(
    tokens: tuple[Token, ...], start: int, end: int
) -> tuple[Argument, ...]:
    if start >= end:
        return ()
    result: list[Argument] = []
    current: list[Token] = []
    depths = {"(": 0, "[": 0, "{": 0}
    closing = {")": "(", "]": "[", "}": "{"}
    for token in tokens[start:end]:
        if token.value in depths:
            depths[token.value] += 1
        elif token.value in closing and depths[closing[token.value]]:
            depths[closing[token.value]] -= 1
        if token.value == "," and not any(depths.values()):
            result.append(Argument(tuple(current)))
            current = []
        else:
            current.append(token)
    result.append(Argument(tuple(current)))
    return tuple(result)


__all__ = [
    "Argument",
    "Call",
    "Token",
    "calls",
    "literal_string",
    "normalize_tokens",
    "tokenize",
]
