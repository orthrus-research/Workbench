"""Validated byte-for-byte IDE bridge for a managed Groovy language session.

IntelliJ's supported LSP integration launches a stdio process. The managed
GroovyScript session deliberately exposes one receipt-bound loopback socket.
This module joins those transport shapes without interpreting or authorizing
language-server messages.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import socket
import threading
from typing import Any, BinaryIO

from .managed_model import validate_session_descriptor
from .model import PackProgramError


MAX_DESCRIPTOR_BYTES = 2 * 1024 * 1024
COPY_CHUNK_BYTES = 64 * 1024


def load_live_session_descriptor(path: Path) -> dict[str, Any]:
    """Load an exact ready descriptor and bind it to its retained path."""

    requested = path.expanduser()
    if requested.is_symlink():
        raise PackProgramError(
            f"managed session descriptor cannot be a symlink: {requested}"
        )
    resolved = requested.resolve(strict=True)
    if not resolved.is_file():
        raise PackProgramError(f"managed session descriptor is not a file: {resolved}")
    size = resolved.stat().st_size
    if not 1 <= size <= MAX_DESCRIPTOR_BYTES:
        raise PackProgramError(
            "managed session descriptor size is outside the supported bound: "
            f"{size} bytes"
        )
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PackProgramError(
            f"managed session descriptor is not valid UTF-8 JSON: {exc}"
        ) from exc
    if not isinstance(raw, Mapping):
        raise PackProgramError("managed session descriptor must be a JSON object")
    descriptor = validate_session_descriptor(raw)
    retained = Path(descriptor["artifacts"]["descriptor_path"]).expanduser().resolve()
    if retained != resolved:
        raise PackProgramError(
            "managed session descriptor path does not match its retained artifact binding"
        )
    return descriptor


def proxy_descriptor_stdio(
    descriptor_path: Path,
    *,
    input_stream: BinaryIO,
    output_stream: BinaryIO,
    connect_timeout: float = 5.0,
) -> None:
    """Copy stdio to the descriptor's public TCP endpoint until either side ends."""

    if not 0.05 <= connect_timeout <= 300:
        raise PackProgramError(
            "IDE bridge connect timeout must be between 0.05 and 300 seconds"
        )
    descriptor = load_live_session_descriptor(descriptor_path)
    endpoint = descriptor["endpoint"]
    host = endpoint["host"]
    port = endpoint["port"]

    outbound_error: list[BaseException] = []
    with socket.create_connection((host, port), timeout=connect_timeout) as connection:
        connection.settimeout(None)

        def copy_input() -> None:
            try:
                while chunk := input_stream.read(COPY_CHUNK_BYTES):
                    connection.sendall(chunk)
                try:
                    connection.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
            except (OSError, ValueError) as exc:
                outbound_error.append(exc)
                try:
                    connection.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        sender = threading.Thread(
            target=copy_input,
            name="workbench-ide-bridge-stdin",
            daemon=True,
        )
        sender.start()
        try:
            while chunk := connection.recv(COPY_CHUNK_BYTES):
                output_stream.write(chunk)
                output_stream.flush()
        except (OSError, ValueError) as exc:
            if not outbound_error:
                raise PackProgramError(
                    f"managed session IDE bridge receive failed: {exc}"
                ) from exc
        sender.join(timeout=1.0)

    if outbound_error:
        raise PackProgramError(
            f"managed session IDE bridge send failed: {outbound_error[0]}"
        ) from outbound_error[0]


__all__ = [
    "COPY_CHUNK_BYTES",
    "MAX_DESCRIPTOR_BYTES",
    "load_live_session_descriptor",
    "proxy_descriptor_stdio",
]
