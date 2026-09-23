"""Bounded JSON-RPC/LSP client for GroovyScript's embedded language server."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import select
import socket
import time
from typing import Any, Mapping, Sequence

from .model import PackProgramError


@dataclass(frozen=True, slots=True)
class LspLimits:
    max_header_bytes: int
    max_message_bytes: int
    max_transcript_messages: int
    max_diagnostics: int


class LspProtocolError(PackProgramError):
    """The peer did not provide a bounded, valid LSP exchange."""


class _DuplicateKey(ValueError):
    pass


def _pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        if key in result:
            raise _DuplicateKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


class LspConnection:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        connect_timeout: float,
        limits: LspLimits,
    ) -> None:
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.limits = limits
        self.socket: socket.socket | None = None
        self.buffer = bytearray()
        self.next_id = 1
        self.sequence = 0
        self.transcript: list[dict[str, Any]] = []
        self.method_counts: Counter[str] = Counter()
        self.total_diagnostics = 0

    def connect(self) -> int:
        started = time.monotonic()
        self.socket = socket.create_connection(
            (self.host, self.port), timeout=self.connect_timeout
        )
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return round((time.monotonic() - started) * 1000)

    def close(self) -> None:
        if self.socket is not None:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.socket.close()
            self.socket = None

    def request(self, method: str, params: Mapping[str, Any] | None) -> int:
        request_id = self.next_id
        self.next_id += 1
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = dict(params)
        self._send(message)
        return request_id

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = dict(params)
        self._send(message)

    def await_response(
        self,
        request_id: int,
        *,
        timeout: float,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        deadline = time.monotonic() + timeout
        notifications: list[dict[str, Any]] = []
        while True:
            message = self._read(deadline)
            if self._handle_server_request(message):
                continue
            if message.get("id") == request_id and "method" not in message:
                return message, notifications
            if "method" not in message:
                raise LspProtocolError(
                    f"unexpected JSON-RPC response id while awaiting {request_id}"
                )
            # Notifications are represented by the bounded, content-hashed
            # transcript. Retaining peer-controlled message bodies here would
            # multiply memory use while callers wait for a response.

    def await_diagnostics(
        self,
        uri: str,
        *,
        timeout: float,
        response_id: int | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
        deadline = time.monotonic() + timeout
        diagnostics: list[dict[str, Any]] | None = None
        response: dict[str, Any] | None = None
        notifications: list[dict[str, Any]] = []
        while diagnostics is None or (response_id is not None and response is None):
            message = self._read(deadline)
            if self._handle_server_request(message):
                continue
            if response_id is not None and message.get("id") == response_id and "method" not in message:
                if response is not None:
                    raise LspProtocolError(
                        f"duplicate JSON-RPC response id {response_id}"
                    )
                response = message
                continue
            if "method" not in message:
                expected = "no response" if response_id is None else str(response_id)
                raise LspProtocolError(
                    f"unexpected JSON-RPC response id while awaiting {expected}"
                )
            method = message.get("method")
            if method != "textDocument/publishDiagnostics":
                continue
            params = message.get("params")
            if not isinstance(params, Mapping) or params.get("uri") != uri:
                continue
            raw_diagnostics = params.get("diagnostics")
            if not isinstance(raw_diagnostics, list):
                raise LspProtocolError("publishDiagnostics diagnostics must be an array")
            diagnostics = [self._diagnostic(item) for item in raw_diagnostics]
            self.total_diagnostics += len(diagnostics)
            if self.total_diagnostics > self.limits.max_diagnostics:
                raise LspProtocolError(
                    f"language server exceeded {self.limits.max_diagnostics} diagnostics"
                )
        return diagnostics, response, notifications

    def transcript_summary(self) -> dict[str, Any]:
        return {
            "messages": self.transcript,
            "message_count": len(self.transcript),
            "method_counts": dict(sorted(self.method_counts.items())),
            "transcript_sha256": hashlib.sha256(
                json.dumps(
                    self.transcript,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
        }

    def _send(self, message: Mapping[str, Any]) -> None:
        if self.socket is None:
            raise LspProtocolError("language-server socket is not connected")
        body = json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(body) > self.limits.max_message_bytes:
            raise LspProtocolError(
                f"outbound LSP message exceeds {self.limits.max_message_bytes} bytes"
            )
        frame = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        self.socket.sendall(frame)
        self._record("outbound", message, body)

    def _read(self, deadline: float) -> dict[str, Any]:
        header_end = self._fill_until(b"\r\n\r\n", deadline, header=True)
        header = bytes(self.buffer[:header_end])
        del self.buffer[: header_end + 4]
        content_length: int | None = None
        seen: set[str] = set()
        try:
            header_text = header.decode("ascii")
        except UnicodeError as exc:
            raise LspProtocolError("LSP header is not ASCII") from exc
        for line in header_text.split("\r\n"):
            if not line or ":" not in line:
                raise LspProtocolError("LSP header line is malformed")
            key, raw_value = line.split(":", 1)
            normalized = key.strip().casefold()
            if normalized in seen:
                raise LspProtocolError(f"duplicate LSP header {normalized}")
            seen.add(normalized)
            if normalized == "content-length":
                value = raw_value.strip()
                if not value.isdigit():
                    raise LspProtocolError("LSP Content-Length is not numeric")
                content_length = int(value)
        if content_length is None:
            raise LspProtocolError("LSP message has no Content-Length")
        if content_length > self.limits.max_message_bytes:
            raise LspProtocolError(
                f"inbound LSP message exceeds {self.limits.max_message_bytes} bytes"
            )
        self._fill_size(content_length, deadline)
        body = bytes(self.buffer[:content_length])
        del self.buffer[:content_length]
        try:
            value = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=_pairs,
                parse_constant=lambda item: (_ for _ in ()).throw(
                    ValueError(f"non-finite JSON number {item}")
                ),
            )
        except (UnicodeError, json.JSONDecodeError, _DuplicateKey, ValueError) as exc:
            raise LspProtocolError(f"malformed LSP JSON: {exc}") from exc
        if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
            raise LspProtocolError("LSP message is not a JSON-RPC 2.0 object")
        _validate_message(value)
        self._record("inbound", value, body)
        return value

    def _fill_until(self, marker: bytes, deadline: float, *, header: bool) -> int:
        while True:
            position = self.buffer.find(marker)
            if position >= 0:
                if header and position > self.limits.max_header_bytes:
                    raise LspProtocolError(
                        f"LSP header exceeds {self.limits.max_header_bytes} bytes"
                    )
                return position
            if header and len(self.buffer) > self.limits.max_header_bytes:
                raise LspProtocolError(
                    f"LSP header exceeds {self.limits.max_header_bytes} bytes"
                )
            self._receive(deadline)

    def _fill_size(self, size: int, deadline: float) -> None:
        while len(self.buffer) < size:
            self._receive(deadline)

    def _receive(self, deadline: float) -> None:
        if self.socket is None:
            raise LspProtocolError("language-server socket is not connected")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LspProtocolError("timed out waiting for the language server")
        readable, _, _ = select.select([self.socket], [], [], remaining)
        if not readable:
            raise LspProtocolError("timed out waiting for the language server")
        chunk = self.socket.recv(65536)
        if not chunk:
            raise LspProtocolError("language server closed the connection")
        self.buffer.extend(chunk)
        if len(self.buffer) > self.limits.max_header_bytes + self.limits.max_message_bytes + 4:
            raise LspProtocolError("language-server receive buffer exceeded its bound")

    def _record(self, direction: str, message: Mapping[str, Any], body: bytes) -> None:
        self.sequence += 1
        if self.sequence > self.limits.max_transcript_messages:
            raise LspProtocolError(
                "language server exceeded "
                f"{self.limits.max_transcript_messages} transcript messages"
            )
        method = message.get("method")
        if isinstance(method, str):
            self.method_counts[method] += 1
        kind = (
            "request"
            if isinstance(method, str) and "id" in message
            else "notification"
            if isinstance(method, str)
            else "error-response"
            if "error" in message
            else "response"
        )
        self.transcript.append(
            {
                "sequence": self.sequence,
                "direction": direction,
                "kind": kind,
                "method": method if isinstance(method, str) else None,
                "id": message.get("id") if isinstance(message.get("id"), (int, str)) else None,
                "size": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        )

    def _handle_server_request(self, message: Mapping[str, Any]) -> bool:
        method = message.get("method")
        if not isinstance(method, str) or "id" not in message:
            return False
        request_id = message["id"]
        params = message.get("params")
        if method == "workspace/configuration":
            items = params.get("items", []) if isinstance(params, Mapping) else []
            result: Any = [None for _ in items] if isinstance(items, list) else []
            self._send({"jsonrpc": "2.0", "id": request_id, "result": result})
        elif method in {"client/registerCapability", "window/workDoneProgress/create"}:
            self._send({"jsonrpc": "2.0", "id": request_id, "result": None})
        elif method == "workspace/workspaceFolders":
            self._send({"jsonrpc": "2.0", "id": request_id, "result": None})
        else:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": "Method not supported by Workbench broker"},
                }
            )
        return True

    def _diagnostic(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise LspProtocolError("language-server diagnostic must be an object")
        message = value.get("message")
        if not isinstance(message, str) or not message:
            raise LspProtocolError("language-server diagnostic message must be text")
        severity = value.get("severity")
        if severity is not None and (
            isinstance(severity, bool) or not isinstance(severity, int) or severity not in {1, 2, 3, 4}
        ):
            raise LspProtocolError("language-server diagnostic severity is invalid")
        diagnostic_range = _range(value.get("range"))
        code = value.get("code")
        if not isinstance(code, (str, int)) or isinstance(code, bool):
            code = None
        elif isinstance(code, str):
            code = _safe_text(code, 256) or None
        source = value.get("source")
        if not isinstance(source, str):
            source = None
        else:
            source = _safe_text(source, 256) or None
        return {
            "range": diagnostic_range,
            "severity": severity,
            "code": code,
            "source": source,
            "message": _safe_text(message, 8192),
        }


def _validate_message(value: Mapping[str, Any]) -> None:
    """Reject ambiguous JSON-RPC envelopes before the broker acts on them."""

    has_method = "method" in value
    has_id = "id" in value
    if has_method:
        method = value["method"]
        if not isinstance(method, str) or not method:
            raise LspProtocolError("JSON-RPC method must be non-empty text")
        if has_id and (
            isinstance(value["id"], bool)
            or not isinstance(value["id"], (int, str))
        ):
            raise LspProtocolError("JSON-RPC request id must be text or an integer")
        if "params" in value and not isinstance(value["params"], (Mapping, list)):
            raise LspProtocolError("JSON-RPC params must be an object or array")
        if "result" in value or "error" in value:
            raise LspProtocolError("JSON-RPC request cannot contain result or error")
        return
    if not has_id or isinstance(value["id"], bool) or not isinstance(
        value["id"], (int, str)
    ):
        raise LspProtocolError("JSON-RPC response id must be text or an integer")
    if ("result" in value) == ("error" in value):
        raise LspProtocolError("JSON-RPC response must contain exactly one result or error")


def probe_language_server(
    *,
    host: str,
    port: int,
    workspace_uri: str,
    files: Sequence[Mapping[str, Any]],
    canary_prefix: str,
    connect_timeout: float,
    diagnostic_timeout: float,
    limits: LspLimits,
) -> dict[str, Any]:
    """Check exact in-memory source through the embedded upstream compiler."""

    connection = LspConnection(
        host,
        port,
        connect_timeout=connect_timeout,
        limits=limits,
    )
    checked: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    capabilities: dict[str, Any] | None = None
    connect_ms: int | None = None
    try:
        connect_ms = connection.connect()
        initialize_id = connection.request(
            "initialize",
            {
                "processId": None,
                "clientInfo": {"name": "Workbench Pack Program Studio", "version": "1"},
                "rootUri": workspace_uri,
                "capabilities": {
                    "textDocument": {
                        "publishDiagnostics": {"relatedInformation": False},
                        "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                    },
                    "workspace": {"workspaceFolders": False},
                },
                "workspaceFolders": None,
            },
        )
        initialize, _ = connection.await_response(
            initialize_id, timeout=diagnostic_timeout
        )
        if "error" in initialize:
            raise LspProtocolError(
                "language server rejected initialize: "
                + _safe_text(_error_message(initialize["error"]), 1024)
            )
        result = initialize.get("result")
        if not isinstance(result, Mapping) or not isinstance(result.get("capabilities"), Mapping):
            raise LspProtocolError("language server initialize result has no capabilities")
        capabilities = _capabilities(result["capabilities"])
        connection.notify("initialized", {})

        for selected in files:
            checked.append(
                _check_file(
                    connection,
                    selected,
                    canary_prefix=canary_prefix,
                    timeout=diagnostic_timeout,
                )
            )
        shutdown_id = connection.request("shutdown", None)
        shutdown, _ = connection.await_response(
            shutdown_id, timeout=diagnostic_timeout
        )
        if "error" in shutdown:
            raise LspProtocolError(
                "language server rejected shutdown: "
                + (_safe_text(_error_message(shutdown["error"]), 1024) or "empty error")
            )
        connection.notify("exit")
    except (OSError, LspProtocolError, ValueError) as exc:
        failure = {
            "kind": type(exc).__name__,
            "message": _safe_text(str(exc), 2048) or "language-service probe failed",
            "checked_files": len(checked),
        }
    finally:
        connection.close()
    return {
        "state": "completed" if failure is None else "partial" if checked else "blocked",
        "endpoint": {
            "host": host,
            "port": port,
            "transport": "lsp-jsonrpc-tcp",
            "connect_ms": connect_ms,
            "identity_binding": "unavailable-upstream-protocol",
        },
        "workspace_uri": workspace_uri,
        "capabilities": capabilities,
        "files": checked,
        "failure": failure,
        "transcript": connection.transcript_summary(),
    }


def _check_file(
    connection: LspConnection,
    selected: Mapping[str, Any],
    *,
    canary_prefix: str,
    timeout: float,
) -> dict[str, Any]:
    uri = str(selected["server_uri"])
    language_id = "groovy"
    connection.notify(
        "textDocument/didOpen",
        {
            "textDocument": {
                "uri": uri,
                "languageId": language_id,
                "version": 1,
                "text": canary_prefix + str(selected["text"]),
            }
        },
    )
    canary_started = time.monotonic()
    canary_diagnostics, _, _ = connection.await_diagnostics(uri, timeout=timeout)
    canary_ms = round((time.monotonic() - canary_started) * 1000)
    canary_confirmed = any(row["severity"] == 1 for row in canary_diagnostics)
    if not canary_confirmed:
        raise LspProtocolError(
            f"diagnostic canary was not observed for {selected['path']}"
        )

    connection.notify(
        "textDocument/didChange",
        {
            "textDocument": {"uri": uri, "version": 2},
            "contentChanges": [{"text": str(selected["text"])}],
        },
    )
    request_id = connection.request(
        "textDocument/documentSymbol",
        {"textDocument": {"uri": uri}},
    )
    compile_started = time.monotonic()
    diagnostics, response, _ = connection.await_diagnostics(
        uri,
        timeout=timeout,
        response_id=request_id,
    )
    compile_ms = round((time.monotonic() - compile_started) * 1000)
    connection.notify(
        "textDocument/didClose", {"textDocument": {"uri": uri}}
    )
    request_error = None
    symbol_count = None
    if response is not None and "error" in response:
        request_error = (
            _safe_text(_error_message(response["error"]), 2048)
            or "language server returned an empty error message"
        )
    elif response is not None:
        symbols = response.get("result")
        if isinstance(symbols, list):
            symbol_count = len(symbols)
        elif symbols is None:
            symbol_count = 0
    state = (
        "inconclusive"
        if request_error is not None
        else "diagnostics"
        if diagnostics
        else "no-diagnostics"
    )
    return {
        "path": selected["path"],
        "server_uri": uri,
        "sha256": selected["sha256"],
        "size": selected["size"],
        "stage": selected["stage"],
        "execution_state": selected["execution_state"],
        "state": state,
        "canary": {
            "state": "confirmed",
            "diagnostic_count": len(canary_diagnostics),
            "latency_ms": canary_ms,
        },
        "diagnostics": diagnostics,
        "diagnostic_count": len(diagnostics),
        "symbol_count": symbol_count,
        "request_error": request_error,
        "compile_latency_ms": compile_ms,
    }


def _capabilities(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "textDocumentSync",
        "documentSymbolProvider",
        "workspaceSymbolProvider",
        "completionProvider",
        "hoverProvider",
        "definitionProvider",
        "typeDefinitionProvider",
        "referencesProvider",
        "renameProvider",
        "signatureHelpProvider",
        "experimental",
    }
    return {key: value[key] for key in sorted(value) if key in allowed}


def _range(value: Any) -> dict[str, dict[str, int]]:
    if not isinstance(value, Mapping):
        raise LspProtocolError("language-server diagnostic range must be an object")
    result: dict[str, dict[str, int]] = {}
    for endpoint in ("start", "end"):
        position = value.get(endpoint)
        if not isinstance(position, Mapping):
            raise LspProtocolError("language-server diagnostic position is malformed")
        line = position.get("line")
        character = position.get("character")
        if (
            isinstance(line, bool)
            or not isinstance(line, int)
            or line < 0
            or isinstance(character, bool)
            or not isinstance(character, int)
            or character < 0
        ):
            raise LspProtocolError("language-server diagnostic position is invalid")
        result[endpoint] = {"line": line, "character": character}
    return result


def _error_message(value: Any) -> str:
    if isinstance(value, Mapping) and isinstance(value.get("message"), str):
        return value["message"]
    return str(value)


def _safe_text(value: str, maximum: int) -> str:
    normalized = "".join(
        character
        if character in "\t\n" or ord(character) >= 32 and ord(character) != 127
        else f"\\x{ord(character):02x}"
        for character in value
    )
    return normalized[:maximum]


__all__ = [
    "LspConnection",
    "LspLimits",
    "LspProtocolError",
    "probe_language_server",
]
