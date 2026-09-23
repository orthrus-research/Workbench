"""Content-Length framed standard-I/O host for Workbench JSON-RPC."""

from __future__ import annotations

from io import BufferedReader, BufferedWriter
import json
from pathlib import Path
import sys
from typing import Any, BinaryIO, TextIO

from .protocol import ProtocolSession
from workbench_core.service.framing import FramingError, JsonPayloadError, read_message, write_message


MAX_HEADER_BYTES = 16 * 1024
MAX_MESSAGE_BYTES = 4 * 1024 * 1024










def _log(error_stream: TextIO, message: str) -> None:
    print(f"workbench-shell: {message}", file=error_stream, flush=True)


def serve(
    input_stream: BinaryIO,
    output_stream: BinaryIO,
    error_stream: TextIO,
    *,
    suite_root: Path | str | None = None,
    config_path: Path | str | None = None,
) -> int:
    """Serve one Workbench protocol session until shutdown or EOF."""

    session = ProtocolSession(
        suite_root=suite_root,
        config_path=config_path,
        logger=lambda message: _log(error_stream, message)
    )
    _log(error_stream, "stdio host started")
    while True:
        try:
            message = read_message(input_stream)
        except JsonPayloadError as exc:
            _log(error_stream, str(exc))
            write_message(
                output_stream,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32700,
                        "message": "Parse error",
                        "data": {"kind": "invalid_json"},
                    },
                },
            )
            continue
        except FramingError as exc:
            _log(error_stream, f"fatal framing error: {exc}")
            return 2

        if message is None:
            _log(error_stream, "input closed")
            return 0
        response = session.handle_message(message)
        if response is not None:
            try:
                write_message(output_stream, response)
            except FramingError as exc:
                _log(error_stream, f"fatal response framing error: {exc}")
                return 3
        if session.shutdown_requested:
            return 0


def main(
    *,
    suite_root: Path | str | None = None,
    config_path: Path | str | None = None,
) -> int:
    input_stream: BinaryIO
    output_stream: BinaryIO
    if isinstance(sys.stdin, BufferedReader):
        input_stream = sys.stdin
    else:
        input_stream = sys.stdin.buffer
    if isinstance(sys.stdout, BufferedWriter):
        output_stream = sys.stdout
    else:
        output_stream = sys.stdout.buffer
    return serve(
        input_stream,
        output_stream,
        sys.stderr,
        suite_root=suite_root,
        config_path=config_path,
    )


if __name__ == "__main__":
    raise SystemExit(main())
