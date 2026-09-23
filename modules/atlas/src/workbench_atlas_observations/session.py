"""Serial JSONL observation requests within one Core-owned command lifetime."""

from __future__ import annotations

from collections.abc import Callable, Iterator
import io
import json
import math
import os
from pathlib import Path
import select
import sqlite3
import sys
import time
from typing import Any, BinaryIO, TextIO

from .view import ObservationError, open_observations


PREFIX = "workbench-atlas-observation-session-"
MAX_REQUEST_BYTES = 1024 * 1024
OPERATIONS = ("context", "search", "inspect", "relationships", "evidence", "crafting-exposure", "close")


class SessionFramingError(ObservationError):
    """The stream cannot safely identify another complete request."""


def _frames(source: BinaryIO | TextIO, cancel: Callable[[], None]) -> Iterator[bytes]:
    """Poll pipe/file input without an idle thread or an uninterruptible readline."""
    memory = isinstance(source, (io.BytesIO, io.StringIO))
    descriptor = None
    blocking = None
    if not memory:
        try:
            descriptor = source.fileno()
            if source.isatty():
                raise SessionFramingError("session input must be a pipe or file, not a terminal")
            blocking = os.get_blocking(descriptor)
            os.set_blocking(descriptor, False)
        except SessionFramingError:
            raise
        except (AttributeError, OSError, ValueError) as error:
            raise SessionFramingError("session input does not support interruptible pipe/file reads") from error
    pending = bytearray()
    try:
        while True:
            cancel()
            if memory:
                chunk = source.read(65536)
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
            else:
                try:
                    chunk = os.read(descriptor, 65536)
                except BlockingIOError:
                    if os.name == "nt":
                        time.sleep(0.1)
                    else:
                        select.select([descriptor], [], [], 0.1)
                    continue
            cancel()
            if not chunk:
                if pending:
                    raise SessionFramingError("session ended with an unterminated request; each request requires LF")
                return
            pending.extend(chunk)
            while (newline := pending.find(b"\n")) >= 0:
                if newline > MAX_REQUEST_BYTES:
                    raise SessionFramingError("session request exceeds the 1 MiB framing limit")
                frame = bytes(pending[:newline])
                del pending[:newline + 1]
                cancel()
                yield frame
            if len(pending) > MAX_REQUEST_BYTES:
                raise SessionFramingError("session request exceeds the 1 MiB framing limit")
    finally:
        if descriptor is not None and blocking is not None:
            os.set_blocking(descriptor, blocking)


def _decode(frame: bytes) -> Any:
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON member")
            value[key] = item
        return value

    def invalid_constant(_value):
        raise ValueError("nonfinite JSON number")

    def finite_float(token):
        value = float(token)
        if not math.isfinite(value):
            raise ValueError("nonfinite JSON number")
        return value

    try:
        value = json.loads(frame.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=invalid_constant, parse_float=finite_float)
        # Escaped unpaired surrogates are not Unicode scalar values and cannot
        # round-trip through the declared UTF-8 transport.
        json.dumps(value, ensure_ascii=False).encode("utf-8")
        return value
    except (UnicodeError, ValueError, RecursionError) as error:
        raise SessionFramingError("session request must be one UTF-8 JSON value without duplicate members or nonfinite numbers") from error


def _request_id(value: Any) -> str | None:
    identifier = value.get("request_id") if type(value) is dict else None
    if (type(identifier) is str and 1 <= len(identifier) <= 128
            and all(32 <= ord(character) <= 126 for character in identifier)):
        return identifier
    return None


def _request(value: Any, graph_id: str) -> tuple[str, dict[str, Any]]:
    if (type(value) is not dict
            or set(value) != {"format", "schema_version", "request_id", "graph_set_id", "operation", "arguments"}
            or value["format"] != PREFIX + "request-v1"
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or _request_id(value) is None
            or value["graph_set_id"] != graph_id
            or type(value["operation"]) is not str or value["operation"] not in OPERATIONS
            or type(value["arguments"]) is not dict):
        raise ObservationError("request must match session V1 and bind this exact graph_set_id")
    operation, arguments = value["operation"], value["arguments"]
    required = {"search": {"query"}, "inspect": {"selection_id"}, "relationships": {"selection_id"},
                "evidence": {"selection_id"}, "crafting-exposure": {"selection_id"}}.get(operation, set())
    optional = {"search": {"kind", "limit", "cursor"},
                "relationships": {"direction", "relation", "limit", "cursor"},
                "evidence": {"limit", "cursor", "snapshot", "pack_profile"},
                "crafting-exposure": {"max_depth", "max_nodes"}}.get(operation, set())
    if not required <= set(arguments) or not set(arguments) <= required | optional:
        raise ObservationError("request arguments do not match the selected operation")
    if operation == "evidence":
        if ("snapshot" in arguments) != ("pack_profile" in arguments):
            raise ObservationError("original evidence resolution requires both snapshot and pack_profile")
        for key in ("snapshot", "pack_profile"):
            if key in arguments and (type(arguments[key]) is not str or not arguments[key] or "\x00" in arguments[key]):
                raise ObservationError(f"{key} must be nonempty text")
    return operation, arguments


def _execute(view, operation: str, arguments: dict[str, Any], cancel: Callable[[], None]) -> dict[str, Any]:
    if operation == "context":
        return view.describe()
    if operation == "search":
        return view.search(arguments["query"], **{key: value for key, value in arguments.items() if key != "query"})
    if operation == "inspect":
        return view.inspect(arguments["selection_id"])
    if operation == "relationships":
        return view.relationships(**arguments)
    if operation == "crafting-exposure":
        from .exposure import derive_crafting_reference_exposure
        return derive_crafting_reference_exposure(view, **arguments)
    from .cli import _resolve_evidence
    record = view.evidence(**{key: value for key, value in arguments.items() if key not in {"snapshot", "pack_profile"}})
    return _resolve_evidence(record, snapshot=Path(arguments["snapshot"]) if "snapshot" in arguments else None,
                             pack_profile=arguments.get("pack_profile"), cancel=cancel)


def _write(output: TextIO, record: dict[str, Any], cancel: Callable[[], None],
           check_current: Callable[[], None] | None = None) -> None:
    raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    cancel()
    if check_current is not None:
        check_current()
    cancel()
    try:
        descriptor = output.fileno()
    except (AttributeError, OSError, ValueError):
        descriptor = None
    if descriptor is None:
        offset = 0
        while offset < len(raw):
            cancel()
            if check_current is not None:
                check_current()
            count = output.write(raw[offset:])
            if type(count) is not int or not 1 <= count <= len(raw) - offset:
                raise SessionFramingError("session output stopped before writing a complete frame")
            offset += count
        output.flush()
        return
    encoded = raw.encode("utf-8")
    blocking = os.get_blocking(descriptor)
    os.set_blocking(descriptor, False)
    try:
        offset = 0
        while offset < len(encoded):
            cancel()
            if check_current is not None:
                check_current()
            try:
                count = os.write(descriptor, encoded[offset:offset + 65536])
            except BlockingIOError:
                if os.name == "nt":
                    time.sleep(0.1)
                else:
                    select.select([], [descriptor], [], 0.1)
                continue
            if count <= 0:
                raise SessionFramingError("session output stopped before writing a complete frame")
            offset += count
    finally:
        os.set_blocking(descriptor, blocking)


def serve_observation_session(path: Path, *, input: BinaryIO | TextIO | None = None,
                              output: TextIO | None = None,
                              check_cancelled: Callable[[], None] | None = None) -> int:
    """Verify once, answer serial requests, and close on EOF, close or failure.

    This owns no process, daemon, socket or durable store. Core owns the command
    lifetime; request identifiers are correlation labels, not credentials.
    """
    source = input if input is not None else sys.stdin.buffer
    target = output if output is not None else sys.stdout
    cancel = check_cancelled or (lambda: None)
    with open_observations(path, check_cancelled=cancel) as view:
        context = view.describe()
        graph_id = context["graph_set_id"]
        _write(target, {"format": PREFIX + "ready-v1", "schema_version": 1, "state": "ready",
                        "graph_set_id": graph_id, "context": context, "operations": list(OPERATIONS),
                        "maximum_request_bytes": MAX_REQUEST_BYTES}, cancel, view.check_current)
        frames = _frames(source, cancel)
        try:
            for frame in frames:
                value = _decode(frame)
                cancel()
                view.check_current()
                response = {"format": PREFIX + "response-v1", "schema_version": 1,
                            "request_id": _request_id(value), "graph_set_id": graph_id}
                try:
                    operation, arguments = _request(value, graph_id)
                except ObservationError as error:
                    _write(target, {**response, "state": "error", "error": {"code": "invalid-request", "message": str(error)}}, cancel)
                    continue
                if operation == "close":
                    _write(target, {**response, "state": "closed"}, cancel)
                    return 0
                try:
                    result = _execute(view, operation, arguments, cancel)
                except (ValueError, OSError, sqlite3.Error) as error:
                    cancel()
                    view.check_current()
                    _write(target, {**response, "state": "error", "error": {"code": "operation-failed", "message": str(error)}}, cancel)
                    continue
                view.check_current()
                _write(target, {**response, "state": "complete", "result": result}, cancel, view.check_current)
        finally:
            frames.close()
    return 0
