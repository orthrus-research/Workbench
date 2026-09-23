"""Canonical JSON and domain-separated content identities for Crucible V2.

V1 encoders are deliberately not imported or redirected here.  The byte rules
implemented by this module are the future-only ``workbench-canonical-json-v2``
domain accepted in ``docs/architecture/CRUCIBLE-GRAPH-MODEL.md``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any, NoReturn


CANONICALIZER_ID = "workbench-canonical-json-v2"
CONTENT_DOMAIN = b"workbench-content-v2\n"
INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1
MAX_NESTING_DEPTH = 256

_KIND_RE = re.compile(r"[a-z][a-z0-9-]*\Z")
_CONTENT_ID_RE = re.compile(
    r"(?P<kind>[a-z][a-z0-9-]*):sha256:(?P<digest>[0-9a-f]{64})\Z"
)
_SURROGATE_RE = re.compile("[\ud800-\udfff]")
_STRING_ESCAPES = {
    ord('"'): r'\"',
    ord("\\"): r"\\",
    **{codepoint: f"\\u00{codepoint:02x}" for codepoint in range(32)},
}


class CanonicalJsonError(ValueError):
    """Raised when a value or byte stream is outside the V2 canonical domain."""


def _fail(message: str, path: str) -> NoReturn:
    raise CanonicalJsonError(f"{path}: {message}")


def _validate_string(value: str, path: str) -> None:
    match = _SURROGATE_RE.search(value)
    if match is not None:
        index = match.start()
        codepoint = ord(value[index])
        _fail(f"surrogate code point U+{codepoint:04X} is not a Unicode scalar", f"{path}[{index}]")


def _encode_string(value: str, path: str) -> bytes:
    _validate_string(value, path)
    # V2 requires hexadecimal escapes even for newline/tab; json.dumps uses
    # short escapes and cannot be substituted as an identity encoder.
    return ('"' + value.translate(_STRING_ESCAPES) + '"').encode("utf-8", errors="strict")


def _canonical_parts(
    value: Any,
    path: str,
    ancestors: set[int],
    container_depth: int,
) -> list[bytes]:
    if value is None:
        return [b"null"]
    if value is True:
        return [b"true"]
    if value is False:
        return [b"false"]
    if type(value) is int:
        if value < INT64_MIN or value > INT64_MAX:
            _fail("integer is outside the signed 64-bit domain", path)
        return [str(value).encode("ascii")]
    if type(value) is str:
        return [_encode_string(value, path)]
    if type(value) is list:
        next_depth = container_depth + 1
        if next_depth > MAX_NESTING_DEPTH:
            _fail(
                f"container nesting exceeds {MAX_NESTING_DEPTH}",
                path,
            )
        identity = id(value)
        if identity in ancestors:
            _fail("cyclic array is outside the JSON value domain", path)
        ancestors.add(identity)
        try:
            parts: list[bytes] = [b"["]
            for index, item in enumerate(value):
                if index:
                    parts.append(b",")
                parts.extend(
                    _canonical_parts(item, f"{path}[{index}]", ancestors, next_depth)
                )
            parts.append(b"]")
            return parts
        finally:
            ancestors.remove(identity)
    if type(value) is dict:
        next_depth = container_depth + 1
        if next_depth > MAX_NESTING_DEPTH:
            _fail(
                f"container nesting exceeds {MAX_NESTING_DEPTH}",
                path,
            )
        identity = id(value)
        if identity in ancestors:
            _fail("cyclic object is outside the JSON value domain", path)
        ancestors.add(identity)
        try:
            entries: list[tuple[bytes, str, Any]] = []
            for key, item in value.items():
                if type(key) is not str:
                    _fail("object key is not a string", path)
                _validate_string(key, f"{path}.<key>")
                encoded_key = key.encode("utf-8", errors="strict")
                entries.append((encoded_key, key, item))
            entries.sort(key=lambda entry: entry[0])
            parts = [b"{"]
            for index, (_, key, item) in enumerate(entries):
                if index:
                    parts.append(b",")
                parts.append(_encode_string(key, f"{path}.<key>"))
                parts.append(b":")
                parts.extend(
                    _canonical_parts(item, f"{path}.{key}", ancestors, next_depth)
                )
            parts.append(b"}")
            return parts
        finally:
            ancestors.remove(identity)
    if isinstance(value, (float, tuple, set, bytes, bytearray, Mapping, Sequence)):
        _fail(f"unsupported semantic type {type(value).__name__}", path)
    _fail(f"unsupported semantic type {type(value).__name__}", path)


def canonical_json_bytes(value: Any) -> bytes:
    """Return the unique V2 canonical UTF-8 representation of *value*.

    Only JSON null/booleans/strings/signed-64-bit integers/arrays/objects are
    accepted. Arrays must be Python lists and objects must be ordinary dicts so
    callers cannot smuggle custom iteration or coercion behavior into identity.
    """

    try:
        return b"".join(_canonical_parts(value, "$", set(), 0))
    except RecursionError as exc:
        raise CanonicalJsonError("$: value nesting exceeds the implementation limit") from exc


def _reject_float(raw: str) -> NoReturn:
    raise CanonicalJsonError(f"$: floating-point token {raw!r} is forbidden")


def _reject_constant(raw: str) -> NoReturn:
    raise CanonicalJsonError(f"$: non-finite token {raw!r} is forbidden")


def _parse_integer(raw: str) -> int:
    value = int(raw, 10)
    if value < INT64_MIN or value > INT64_MAX:
        raise CanonicalJsonError(f"$: integer token {raw!r} is outside signed 64-bit range")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CanonicalJsonError(f"$: duplicate object key {key!r}")
        result[key] = value
    return result


def _parse_json_strict_with_canonical(
    raw: bytes | bytearray | memoryview | str,
) -> tuple[Any, bytes]:
    if isinstance(raw, str):
        text = raw
    elif isinstance(raw, (bytes, bytearray, memoryview)):
        try:
            text = bytes(raw).decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise CanonicalJsonError(f"$: input is not valid UTF-8: {exc}") from exc
    else:
        raise TypeError("raw JSON must be bytes-like or str")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_float=_reject_float,
            parse_int=_parse_integer,
            parse_constant=_reject_constant,
        )
    except CanonicalJsonError:
        raise
    except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        raise CanonicalJsonError(f"$: invalid JSON: {exc}") from exc
    canonical = canonical_json_bytes(value)
    return value, canonical


def parse_json_strict(raw: bytes | bytearray | memoryview | str) -> Any:
    """Parse JSON without accepting duplicate keys, floats, or invalid scalars.

    This accepts non-canonical whitespace and key order. Use
    :func:`parse_canonical_json` when exact canonical input is required.
    """

    value, _ = _parse_json_strict_with_canonical(raw)
    return value


def parse_canonical_json(raw: bytes | bytearray | memoryview | str) -> Any:
    """Parse and require an exact canonical V2 byte representation."""

    if isinstance(raw, str):
        try:
            encoded = raw.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise CanonicalJsonError(f"$: input is not valid Unicode: {exc}") from exc
    elif isinstance(raw, (bytes, bytearray, memoryview)):
        encoded = bytes(raw)
    else:
        raise TypeError("raw JSON must be bytes-like or str")
    value, canonical = _parse_json_strict_with_canonical(encoded)
    if encoded != canonical:
        raise CanonicalJsonError("$: input bytes are valid JSON but not canonical V2 JSON")
    return value


def _validate_kind(kind: str) -> None:
    if type(kind) is not str or _KIND_RE.fullmatch(kind) is None:
        raise CanonicalJsonError("kind must match [a-z][a-z0-9-]*")


def content_id(kind: str, body: Any) -> str:
    """Compute the domain-separated content ID for a closed semantic body."""

    _validate_kind(kind)
    digest = hashlib.sha256(
        CONTENT_DOMAIN
        + kind.encode("ascii")
        + b"\n"
        + canonical_json_bytes(body)
    ).hexdigest()
    return f"{kind}:sha256:{digest}"


def record_content_id(record: Mapping[str, Any]) -> str:
    """Compute a semantic record ID with exactly the top-level ``id`` omitted."""

    if type(record) is not dict:
        raise CanonicalJsonError("record must be an ordinary object")
    kind = record.get("kind")
    if type(kind) is not str:
        raise CanonicalJsonError("record.kind must be a string")
    _validate_kind(kind)
    if record.get("canonicalizer") != CANONICALIZER_ID:
        raise CanonicalJsonError(
            f"record.canonicalizer must equal {CANONICALIZER_ID!r}"
        )
    format_id = record.get("format")
    if type(format_id) is not str or not format_id:
        raise CanonicalJsonError("record.format must be a non-empty string")
    body = dict(record)
    body.pop("id", None)
    return content_id(kind, body)


def validate_content_id(record: Mapping[str, Any]) -> str:
    """Recompute and validate an identity-bearing semantic record."""

    if type(record) is not dict:
        raise CanonicalJsonError("record must be an ordinary object")
    actual = record.get("id")
    if type(actual) is not str:
        raise CanonicalJsonError("record.id must be a string")
    expected = record_content_id(record)
    if actual != expected:
        raise CanonicalJsonError(
            f"record.id mismatch: expected {expected!r}, got {actual!r}"
        )
    match = _CONTENT_ID_RE.fullmatch(actual)
    if match is None or match.group("kind") != record["kind"]:
        raise CanonicalJsonError("record.id is not a canonical ID for record.kind")
    return actual
