"""Exact source-location API compatibility exports and PPS JSON parsing."""

from __future__ import annotations
import json
import re
from typing import Any

from workbench_api.source_locations import (
    SourceLocationError,
    character_location,
    source_location,
    validate_location_shape,
    verify_source_location,
)


def _char_byte_offsets(source: str) -> list[int]:
    result = [0]
    for character in source:
        result.append(result[-1] + len(character.encode("utf-8")))
    return result


def _json_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


class JsonSource:
    def __init__(self, source: bytes, path: str) -> None:
        if len(source) > 8 * 1024 * 1024:
            raise SourceLocationError("JSON exceeds its byte bound")
        try:
            self.text = source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SourceLocationError(f"JSON is not UTF-8: {path}: {exc}") from exc
        self.source = source
        self.path = path
        self.offsets = _char_byte_offsets(self.text)
        self.index = 0
        self.depth = 0
        self.entries: list[tuple[str, int, int, Any]] = []
        self.value = None
        self.parse()

    def location(self, pointer: str) -> dict[str, Any]:
        matches = [
            (start, end) for key, start, end, _ in self.entries if key == pointer
        ]
        if pointer == "":
            start = len(self.source) - len(self.source.lstrip())
            end = len(self.source.rstrip())
        elif len(matches) == 1:
            start, inclusive = matches[0]
            end = inclusive + 1
        else:
            raise SourceLocationError("JSON field location is missing or ambiguous")
        return source_location(self.source, self.path, start, end)

    def parse(self) -> list[tuple[str, int, int, Any]]:
        if self.index:
            return self.entries
        self._space()
        self.value = self._value("", False)
        self._space()
        if self.index != len(self.text):
            raise SourceLocationError(
                f"trailing content in JSON configuration: {self.path}"
            )
        return self.entries

    def _space(self) -> None:
        while self.index < len(self.text) and self.text[self.index] in " \t\r\n":
            self.index += 1

    def _string(self) -> str:
        start = self.index
        if self.index >= len(self.text) or self.text[self.index] != '"':
            raise SourceLocationError(f"expected JSON string in {self.path}")
        self.index += 1
        escaped = False
        while self.index < len(self.text):
            character = self.text[self.index]
            self.index += 1
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                raw = self.text[start : self.index]
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise SourceLocationError(
                        f"invalid JSON string in {self.path}: {exc}"
                    ) from exc
                if not isinstance(value, str):
                    raise SourceLocationError("JSON key did not decode to a string")
                return value
        raise SourceLocationError(f"unterminated JSON string in {self.path}")

    def _value(self, pointer: str, record: bool) -> Any:
        self.depth += 1
        if self.depth > 64 or len(self.entries) > 100_000:
            raise SourceLocationError("JSON exceeds its structural bound")
        self._space()
        start = self.index
        if start >= len(self.text):
            raise SourceLocationError(f"missing JSON value in {self.path}")
        character = self.text[self.index]
        if character == "{":
            value = self._object(pointer)
        elif character == "[":
            value = self._array(pointer)
        elif character == '"':
            value = self._string()
        else:
            match = re.match(
                r"(?:true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?"
                r"(?:[eE][+-]?[0-9]+)?)",
                self.text[self.index :],
            )
            if match is None:
                raise SourceLocationError(
                    f"invalid JSON value in {self.path} at character {self.index}"
                )
            raw = match.group(0)
            self.index += len(raw)
            value = json.loads(raw)
        end = self.index
        if record:
            self.entries.append(
                (pointer, self.offsets[start], self.offsets[end] - 1, value)
            )
        self.depth -= 1
        return value

    def _object(self, pointer: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        self.index += 1
        self._space()
        if self.index < len(self.text) and self.text[self.index] == "}":
            self.index += 1
            return result
        while True:
            self._space()
            key = self._string()
            if key in result:
                raise SourceLocationError(
                    f"duplicate JSON configuration key in {self.path}: {key}"
                )
            self._space()
            if self.index >= len(self.text) or self.text[self.index] != ":":
                raise SourceLocationError(f"expected JSON colon in {self.path}")
            self.index += 1
            child_pointer = pointer + "/" + _json_pointer_token(key)
            result[key] = self._value(child_pointer, True)
            self._space()
            if self.index < len(self.text) and self.text[self.index] == "}":
                self.index += 1
                return result
            if self.index >= len(self.text) or self.text[self.index] != ",":
                raise SourceLocationError(f"expected JSON comma in {self.path}")
            self.index += 1

    def _array(self, pointer: str) -> list[Any]:
        result: list[Any] = []
        self.index += 1
        self._space()
        if self.index < len(self.text) and self.text[self.index] == "]":
            self.index += 1
            return result
        while True:
            child_pointer = pointer + "/" + str(len(result))
            result.append(self._value(child_pointer, True))
            self._space()
            if self.index < len(self.text) and self.text[self.index] == "]":
                self.index += 1
                return result
            if self.index >= len(self.text) or self.text[self.index] != ",":
                raise SourceLocationError(f"expected JSON comma in {self.path}")
            self.index += 1
