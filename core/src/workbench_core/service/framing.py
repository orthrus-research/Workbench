"""Bounded Content-Length JSON framing."""
from __future__ import annotations
import json
from typing import Any, BinaryIO
MAX_HEADER_BYTES = 16 * 1024
MAX_MESSAGE_BYTES = 4 * 1024 * 1024

class FramingError(ValueError):
    """Raised when a message boundary cannot be trusted."""


class JsonPayloadError(ValueError):
    """Raised when a complete frame does not contain valid JSON."""


def read_message(stream: BinaryIO) -> Any | None:
    """Read one Content-Length framed JSON value, or None at clean EOF."""

    headers: dict[str, str] = {}
    consumed = 0
    while True:
        line = stream.readline(MAX_HEADER_BYTES + 1)
        if line == b"":
            if not headers:
                return None
            raise FramingError("unexpected EOF while reading headers")
        consumed += len(line)
        if consumed > MAX_HEADER_BYTES:
            raise FramingError("message headers exceed the size limit")
        if not line.endswith(b"\n"):
            raise FramingError("unterminated message header")
        stripped = line.rstrip(b"\r\n")
        if not stripped:
            break
        if b":" not in stripped:
            raise FramingError("malformed message header")
        raw_name, raw_value = stripped.split(b":", 1)
        try:
            name = raw_name.decode("ascii").strip().lower()
            value = raw_value.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise FramingError("message headers must be ASCII") from exc
        if not name or name in headers:
            raise FramingError("empty or duplicate message header")
        headers[name] = value

    raw_length = headers.get("content-length")
    if raw_length is None or not raw_length.isdigit():
        raise FramingError("missing or invalid Content-Length")
    length = int(raw_length)
    if length > MAX_MESSAGE_BYTES:
        raise FramingError("message exceeds the size limit")
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = stream.read(remaining)
        if chunk == b"":
            raise FramingError("unexpected EOF while reading message body")
        chunks.append(chunk)
        remaining -= len(chunk)
    body = b"".join(chunks)
    try:
        decoded = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise JsonPayloadError("message body is not valid UTF-8") from exc
    try:
        return json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise JsonPayloadError("message body is not valid JSON") from exc


def write_message(stream: BinaryIO, message: Any) -> None:
    """Write one deterministic UTF-8 JSON frame."""

    body = json.dumps(
        message,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(body) > MAX_MESSAGE_BYTES:
        raise FramingError("response exceeds the size limit")

    def write_all(raw: bytes) -> None:
        view = memoryview(raw)
        while view:
            written = stream.write(view)
            if written is None or written <= 0:
                raise FramingError("message stream closed during write")
            view = view[written:]

    write_all(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    write_all(body)
    stream.flush()
