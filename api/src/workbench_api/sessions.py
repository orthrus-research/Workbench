"""Shared session records and manifest validation; no session persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Any


FORMAT_VERSION = "workbench-live-console-session-v1"

EVENTS_NAME = "events-v1.jsonl"

MAX_RAW_STREAMS = 16

_STREAM_RE = re.compile(r"[a-z][a-z0-9-]{0,47}")

_COMMAND_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/+@=-]{0,511}")

_INTENTS = {"execute", "preview", "inert", "inspect"}

class SessionError(RuntimeError):
    """Retained console session state is invalid or unsafe."""

@dataclass(frozen=True)
class RawLocator:
    stream: str
    path: str
    byte_start: int
    byte_end: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "stream": self.stream,
            "path": self.path,
            "byte_start": self.byte_start,
            "byte_end": self.byte_end,
        }

def _validate_command_metadata(
    *,
    command_id: str,
    label: str | None,
    argv: list[str],
    cwd: Path,
    intent: str,
) -> tuple[str, str | None, list[str], Path, str]:
    if not isinstance(command_id, str) or not _COMMAND_ID_RE.fullmatch(command_id):
        raise SessionError("invalid live-console command ID")
    if label is not None:
        if not isinstance(label, str) or not label:
            raise SessionError("console session label must be non-empty text")
        label = _single_line(label, maximum=8192, reject=True)
    if not isinstance(argv, list) or not (1 <= len(argv) <= 4096):
        raise SessionError("console session argv must contain 1 to 4096 tokens")
    exact_argv: list[str] = []
    for index, token in enumerate(argv):
        if not isinstance(token, str) or "\x00" in token or len(token) > 1_048_576:
            raise SessionError(f"invalid console session argv token {index}")
        if index == 0 and token == "":
            raise SessionError("console session executable token cannot be empty")
        exact_argv.append(token)
    if intent not in _INTENTS:
        raise SessionError(f"invalid live-console command intent: {intent!r}")
    resolved_cwd = cwd.expanduser().resolve()
    _single_line(str(resolved_cwd), maximum=8192, reject=True)
    return command_id, label, exact_argv, resolved_cwd, intent

def _single_line(value: str, *, maximum: int, reject: bool = False) -> str:
    invalid = "\x00" in value or "\r" in value or "\n" in value
    if reject and (invalid or not value or len(value) > maximum):
        raise SessionError("console session metadata must be bounded single-line text")
    cleaned = value.replace("\x00", "\\x00").replace("\r", "\\r").replace("\n", "\\n")
    return cleaned[:maximum]

def _validated_listing_manifest(
    value: Any, *, physical_directory: Path
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SessionError("session manifest is not an object")
    allowed = {
        "format_version", "session_id", "state", "started_at", "updated_at",
        "ended_at", "command", "retention", "summary", "exit", "limitations",
    }
    required = {
        "format_version", "session_id", "state", "started_at", "updated_at",
        "command", "retention", "summary",
    }
    if not required.issubset(value) or not set(value).issubset(allowed):
        raise SessionError("session manifest fields are invalid")
    if value.get("format_version") != FORMAT_VERSION:
        raise SessionError("session manifest has an unsupported format")
    if value.get("session_id") != physical_directory.name:
        raise SessionError("session manifest ID does not match its physical directory")
    if value.get("state") not in {
        "running", "complete", "failed", "cancelled", "incomplete"
    }:
        raise SessionError("session manifest state is invalid")
    for key in ("started_at", "updated_at"):
        _validate_manifest_timestamp(value.get(key), key)
    terminal = value["state"] != "running"
    if terminal != ("ended_at" in value and "exit" in value):
        raise SessionError("session manifest terminal fields are inconsistent")
    if terminal:
        _validate_manifest_timestamp(value.get("ended_at"), "ended_at")
    command = _validate_manifest_command(value.get("command"))
    summary = _validate_manifest_summary(value.get("summary"))
    retention = value.get("retention")
    if not isinstance(retention, dict) or set(retention) != {
        "directory", "events", "raw_streams", "raw_is_source_record",
        "events_are_projection",
    }:
        raise SessionError("session manifest retention is missing")
    streams = retention.get("raw_streams")
    if (
        retention.get("events") != EVENTS_NAME
        or retention.get("raw_is_source_record") is not True
        or retention.get("events_are_projection") is not True
        or not isinstance(streams, dict)
        or len(streams) > MAX_RAW_STREAMS
        or any(
            not isinstance(key, str)
            or not _STREAM_RE.fullmatch(key)
            or value != f"{key}.raw"
            for key, value in streams.items()
        )
    ):
        raise SessionError("session manifest retention bindings are invalid")
    recorded = retention.get("directory")
    try:
        recorded_path = Path(recorded).expanduser().resolve()
    except (TypeError, OSError, RuntimeError) as exc:
        raise SessionError("session manifest retention directory is invalid") from exc
    if recorded_path != physical_directory.resolve():
        raise SessionError("session manifest retention directory was tampered")
    if "limitations" in value:
        limitations = value["limitations"]
        if (
            not isinstance(limitations, list)
            or len(limitations) > 256
            or len(set(limitations)) != len(limitations)
            or any(
                not isinstance(item, str)
                or not item
                or len(item) > 8192
                or any(marker in item for marker in ("\r", "\n", "\x00"))
                for item in limitations
            )
        ):
            raise SessionError("session manifest limitations are invalid")
    if terminal:
        _validate_manifest_exit(value.get("exit"))
    # Keep normalized nested objects tied to the exact structural checks above.
    if command is not value["command"] or summary is not value["summary"]:
        raise AssertionError("manifest validators must not rewrite identity bytes")
    return dict(value)

def _validate_manifest_timestamp(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SessionError(f"session manifest {label} is invalid")
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise SessionError(f"session manifest {label} is invalid") from exc

def _validate_manifest_command(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "command_id", "label", "argv", "cwd", "intent", "shell", "pid",
        "process_group_id",
    }:
        raise SessionError("session manifest command is invalid")
    if value.get("shell") is not False:
        raise SessionError("session manifest command shell flag is invalid")
    try:
        command_id, label, argv, cwd, intent = _validate_command_metadata(
            command_id=value.get("command_id"),
            label=value.get("label"),
            argv=value.get("argv"),
            cwd=Path(value.get("cwd")) if isinstance(value.get("cwd"), str) else value.get("cwd"),
            intent=value.get("intent"),
        )
    except (SessionError, TypeError) as exc:
        raise SessionError("session manifest command metadata is invalid") from exc
    if (
        command_id != value["command_id"]
        or label != value["label"]
        or argv != value["argv"]
        or str(cwd) != value["cwd"]
        or intent != value["intent"]
    ):
        raise SessionError("session manifest command metadata drifted")
    for key in ("pid", "process_group_id"):
        field = value[key]
        if field is not None and (type(field) is not int or field <= 0):
            raise SessionError("session manifest process identity is invalid")
    return value

def _validate_manifest_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "event_count", "severity_counts", "subsystem_counts", "kind_counts",
        "outcome_failure_events", "source_locator_count",
    }:
        raise SessionError("session manifest summary is invalid")
    for key in ("event_count", "outcome_failure_events", "source_locator_count"):
        if type(value[key]) is not int or value[key] < 0:
            raise SessionError("session manifest summary count is invalid")
    for key in ("severity_counts", "subsystem_counts", "kind_counts"):
        counts = value[key]
        if (
            not isinstance(counts, dict)
            or len(counts) > 256
            or any(
                not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", name)
                or type(count) is not int
                or count < 0
                for name, count in counts.items()
            )
        ):
            raise SessionError("session manifest summary count map is invalid")
    return value

def _validate_manifest_exit(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {
        "process_exit_code", "effective_exit_code", "outcome", "cancellation",
    }:
        raise SessionError("session manifest exit is invalid")
    if value["process_exit_code"] is not None and type(value["process_exit_code"]) is not int:
        raise SessionError("session manifest process exit code is invalid")
    if type(value["effective_exit_code"]) is not int or value["effective_exit_code"] < 0:
        raise SessionError("session manifest effective exit code is invalid")
    for key, nullable in (("outcome", False), ("cancellation", True)):
        field = value[key]
        if nullable and field is None:
            continue
        if (
            not isinstance(field, str)
            or not field
            or len(field) > 8192
            or any(marker in field for marker in ("\r", "\n", "\x00"))
        ):
            raise SessionError(f"session manifest {key} is invalid")

def validate_session_manifest(
    value: Any, *, physical_directory: Path
) -> dict[str, Any]:
    """Validate the identity and retention bindings of one V1 manifest.

    Callers that ingest a selected session can fail closed with this API;
    ``list_sessions`` intentionally converts the same failures into visible
    incomplete listing rows.
    """

    return _validated_listing_manifest(
        value,
        physical_directory=physical_directory,
    )
