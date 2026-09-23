"""Identity and semantic checks for managed Groovy language-session records."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

from .model import PackProgramError, canonical_bytes, content_id


DESCRIPTOR_FORMAT = "workbench-groovy-language-session-descriptor-v1"
RECEIPT_FORMAT = "workbench-groovy-managed-language-session-v1"
SCHEMA_VERSION = 1
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SESSION = re.compile(
    r"^workbench-groovy-language-session:[0-9a-f]{8}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def descriptor_identity(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("descriptor_id", None)
    return content_id("workbench-groovy-language-session-descriptor:sha256:", payload)


def receipt_identity(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("receipt_id", None)
    return content_id("workbench-groovy-managed-language-session:sha256:", payload)


def command_identity(command: list[str]) -> str:
    return hashlib.sha256(canonical_bytes(command)).hexdigest()


def validate_session_descriptor(value: Mapping[str, Any]) -> dict[str, Any]:
    descriptor = _exact(
        value,
        {
            "format",
            "schema_version",
            "descriptor_id",
            "session_id",
            "state",
            "emitted_at",
            "profile",
            "program",
            "runtime",
            "endpoint",
            "readiness",
            "clients",
            "artifacts",
            "limitations",
        },
        "managed session descriptor",
    )
    if descriptor["format"] != DESCRIPTOR_FORMAT or descriptor["schema_version"] != 1:
        raise PackProgramError("unsupported managed session descriptor")
    if descriptor["descriptor_id"] != descriptor_identity(descriptor):
        raise PackProgramError("managed session descriptor identity is stale")
    _session_id(descriptor["session_id"])
    if descriptor["state"] != "ready":
        raise PackProgramError("managed session descriptor state is invalid")
    _text(descriptor["emitted_at"], "managed session descriptor time", 256)
    _profile(descriptor["profile"])
    _program(descriptor["program"])
    _runtime(descriptor["runtime"])
    endpoint = _endpoint(descriptor["endpoint"])
    readiness = _readiness(descriptor["readiness"], allow_failure=False)
    if readiness["state"] != "confirmed":
        raise PackProgramError("managed session descriptor readiness is not confirmed")
    if endpoint["port"] != readiness["port"]:
        raise PackProgramError("managed session descriptor readiness port is stale")
    clients = _exact(
        descriptor["clients"],
        {
            "consumers",
            "connection_ownership",
            "reconnect_policy",
            "shutdown_owner",
        },
        "managed session client handoff",
    )
    if clients != {
        "consumers": ["terminal", "intellij", "vscode"],
        "connection_ownership": "client-connects-directly",
        "reconnect_policy": "retry-bounded-after-readiness-handoff",
        "shutdown_owner": "workbench-session-process",
    }:
        raise PackProgramError("managed session client handoff is invalid")
    artifacts = _exact(
        descriptor["artifacts"],
        {"descriptor_path", "pending_receipt_path", "events_path"},
        "managed session descriptor artifacts",
    )
    for key, item in artifacts.items():
        _text(item, f"managed session artifact {key}", 8192)
    _limitations(descriptor["limitations"])
    return dict(descriptor)


def validate_managed_session_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    receipt = _exact(
        value,
        {
            "format",
            "schema_version",
            "receipt_id",
            "session_id",
            "operation_class",
            "started_at",
            "ready_at",
            "ended_at",
            "state",
            "outcome",
            "profile",
            "program",
            "runtime",
            "launch",
            "endpoint",
            "readiness",
            "overlays",
            "handoff",
            "shutdown",
            "events",
            "limitations",
        },
        "managed session receipt",
    )
    if receipt["format"] != RECEIPT_FORMAT or receipt["schema_version"] != 1:
        raise PackProgramError("unsupported managed session receipt")
    if receipt["receipt_id"] != receipt_identity(receipt):
        raise PackProgramError("managed session receipt identity is stale")
    _session_id(receipt["session_id"])
    if receipt["operation_class"] != "local-mutation":
        raise PackProgramError("managed session operation class is invalid")
    for key in ("started_at", "ended_at"):
        _text(receipt[key], f"managed session {key}", 256)
    if receipt["ready_at"] is not None:
        _text(receipt["ready_at"], "managed session ready_at", 256)
    if receipt["state"] not in {"complete", "blocked"}:
        raise PackProgramError("managed session final state is invalid")
    _text(receipt["outcome"], "managed session outcome", 256)
    _profile(receipt["profile"])
    _program(receipt["program"])
    _runtime(receipt["runtime"])
    launch = _launch(receipt["launch"])
    endpoint = _endpoint(receipt["endpoint"])
    readiness = _readiness(receipt["readiness"], allow_failure=True)
    if endpoint["port"] != readiness["port"]:
        raise PackProgramError("managed session receipt readiness port is stale")
    if receipt["ready_at"] is None and readiness["state"] == "confirmed":
        raise PackProgramError("managed session receipt omits its ready time")

    overlays = receipt["overlays"]
    if not isinstance(overlays, list) or len(overlays) != 2:
        raise PackProgramError("managed session receipt must bind both checked overlays")
    roles: set[str] = set()
    for row in overlays:
        overlay = _overlay(row)
        if overlay["role"] in roles:
            raise PackProgramError("managed session overlay role is duplicate")
        roles.add(overlay["role"])
    if roles != {"launcher-jvm", "language-server-port"}:
        raise PackProgramError("managed session overlay roles are incomplete")

    handoff = receipt["handoff"]
    if handoff is not None:
        validated = validate_session_descriptor(handoff)
        if validated["session_id"] != receipt["session_id"]:
            raise PackProgramError("managed session handoff belongs to another session")
        for key in ("profile", "program", "runtime", "endpoint", "readiness"):
            if validated[key] != receipt[key]:
                raise PackProgramError(
                    f"managed session handoff {key} differs from its final receipt"
                )
    shutdown = _exact(
        receipt["shutdown"],
        {"reason", "graceful_attempted", "forced", "orphaned_pids"},
        "managed session shutdown",
    )
    _text(shutdown["reason"], "managed session shutdown reason", 256)
    if not isinstance(shutdown["graceful_attempted"], bool) or not isinstance(shutdown["forced"], bool):
        raise PackProgramError("managed session shutdown flags are invalid")
    _pids(shutdown["orphaned_pids"], "managed session orphaned processes")
    events = _exact(
        receipt["events"],
        {"path", "sha256", "size", "count"},
        "managed session events",
    )
    _text(events["path"], "managed session events path", 8192)
    _digest(events["sha256"], "managed session events hash")
    _nonnegative(events["size"], "managed session events size")
    _positive(events["count"], "managed session events count")
    _limitations(receipt["limitations"])

    if receipt["state"] == "complete":
        if any(not row["restore"]["state"].startswith("restored-") for row in overlays):
            raise PackProgramError("complete managed session did not restore every overlay")
        if shutdown["orphaned_pids"]:
            raise PackProgramError("complete managed session retained an orphan process")
        if launch["ownership"] != "session-owned":
            raise PackProgramError("complete managed session lacks exact process ownership")
        if not launch["client_processes"]:
            raise PackProgramError("complete managed session lacks an owned client process")
    return dict(receipt)


def _profile(value: Any) -> Mapping[str, Any]:
    profile = _exact(
        value,
        {
            "pack_program_profile_id",
            "pack_program_profile_sha256",
            "language_service_profile_id",
            "language_service_profile_sha256",
            "managed_session_profile_id",
            "managed_session_profile_sha256",
            "platform_profile_id",
        },
        "managed session profile binding",
    )
    for key, item in profile.items():
        if key.endswith("sha256"):
            _digest(item, f"managed session profile {key}")
        else:
            _text(item, f"managed session profile {key}", 2048)
    return profile


def _program(value: Any) -> Mapping[str, Any]:
    program = _exact(
        value,
        {
            "program_id",
            "source_sha256",
            "source_root",
            "workspace_uri",
            "server_workspace_uri",
        },
        "managed session program binding",
    )
    _text(program["program_id"], "managed session program ID", 256)
    _digest(program["source_sha256"], "managed session source hash")
    _text(program["source_root"], "managed session source root", 8192)
    if not _text(program["workspace_uri"], "managed session workspace URI", 16384).startswith("file:"):
        raise PackProgramError("managed session workspace URI must be a file URI")
    if not _text(
        program["server_workspace_uri"],
        "managed session server workspace URI",
        16384,
    ).startswith("file:"):
        raise PackProgramError("managed session server workspace URI must be a file URI")
    return program


def _runtime(value: Any) -> Mapping[str, Any]:
    runtime = _exact(
        value,
        {
            "runtime_id",
            "root",
            "mod_graph_id",
            "java_sha256",
            "launch_receipt_path",
            "launch_receipt_sha256",
            "launch_receipt_size",
        },
        "managed session runtime binding",
    )
    for key in ("runtime_id", "root", "mod_graph_id", "launch_receipt_path"):
        _text(runtime[key], f"managed session runtime {key}", 8192)
    if runtime["java_sha256"] is not None:
        _digest(runtime["java_sha256"], "managed session Java hash")
    _digest(runtime["launch_receipt_sha256"], "managed session launch receipt hash")
    _positive(runtime["launch_receipt_size"], "managed session launch receipt size")
    return runtime


def _endpoint(value: Any) -> Mapping[str, Any]:
    endpoint = _exact(
        value,
        {
            "host",
            "port",
            "transport",
            "allocation",
            "connection_model",
            "identity_binding",
            "route",
            "upstream",
        },
        "managed session endpoint",
    )
    if endpoint != {
        **endpoint,
        "host": "127.0.0.1",
        "transport": "lsp-jsonrpc-tcp",
        "allocation": endpoint["allocation"],
        "connection_model": "one-active-client-sequential-reaccept",
        "identity_binding": "managed-launch-custody; no-upstream-identity-challenge",
        "route": endpoint["route"],
        "upstream": endpoint["upstream"],
    }:
        raise PackProgramError("managed session endpoint policy is invalid")
    if endpoint["allocation"] not in {"reserved-random-loopback", "explicit-free-loopback"}:
        raise PackProgramError("managed session endpoint allocation is invalid")
    if endpoint["route"] not in {
        "direct-loopback",
        "wsl-windows-stdio-bridge",
    }:
        raise PackProgramError("managed session endpoint route is invalid")
    if isinstance(endpoint["port"], bool) or not isinstance(endpoint["port"], int) or not 1 <= endpoint["port"] <= 65535:
        raise PackProgramError("managed session endpoint port is invalid")
    upstream = _exact(endpoint["upstream"], {"host", "port"}, "managed session upstream endpoint")
    if upstream["host"] != "127.0.0.1":
        raise PackProgramError("managed session upstream host is invalid")
    if isinstance(upstream["port"], bool) or not isinstance(upstream["port"], int) or not 1 <= upstream["port"] <= 65535:
        raise PackProgramError("managed session upstream port is invalid")
    if endpoint["route"] == "direct-loopback" and upstream["port"] != endpoint["port"]:
        raise PackProgramError("direct managed endpoint has a stale upstream port")
    if endpoint["route"] == "wsl-windows-stdio-bridge" and upstream["port"] == endpoint["port"]:
        raise PackProgramError("bridged managed endpoint must use a distinct upstream port")
    return endpoint


def _readiness(value: Any, *, allow_failure: bool) -> Mapping[str, Any]:
    readiness = _exact(
        value,
        {
            "state",
            "port",
            "attempts",
            "latency_ms",
            "canary_state",
            "capabilities",
            "transcript_sha256",
            "failure",
        },
        "managed session readiness",
    )
    allowed = {"confirmed", "blocked"} if allow_failure else {"confirmed"}
    if readiness["state"] not in allowed:
        raise PackProgramError("managed session readiness state is invalid")
    if isinstance(readiness["port"], bool) or not isinstance(readiness["port"], int) or not 1 <= readiness["port"] <= 65535:
        raise PackProgramError("managed session readiness port is invalid")
    _positive(readiness["attempts"], "managed session readiness attempts")
    _nonnegative(readiness["latency_ms"], "managed session readiness latency")
    if readiness["state"] == "confirmed":
        if readiness["canary_state"] != "confirmed" or not isinstance(readiness["capabilities"], Mapping):
            raise PackProgramError("managed session readiness canary is stale")
        _digest(readiness["transcript_sha256"], "managed session readiness transcript")
        if readiness["failure"] is not None:
            raise PackProgramError("confirmed managed session readiness has a failure")
    else:
        if readiness["failure"] is None:
            raise PackProgramError("blocked managed session readiness lacks a failure")
    return readiness


def _launch(value: Any) -> Mapping[str, Any]:
    launch = _exact(
        value,
        {
            "command",
            "command_sha256",
            "cwd",
            "launcher_path",
            "launcher_sha256",
            "launcher_size",
            "launcher_pid",
            "launcher_returncode",
            "client_processes",
            "ownership",
        },
        "managed session launch",
    )
    command = launch["command"]
    if not isinstance(command, list) or not command or len(command) > 256:
        raise PackProgramError("managed session command is malformed")
    for item in command:
        _text(item, "managed session command argument", 16384)
    if launch["command_sha256"] != command_identity(command):
        raise PackProgramError("managed session command identity is stale")
    for key in ("cwd", "launcher_path"):
        _text(launch[key], f"managed session launch {key}", 8192)
    _digest(launch["launcher_sha256"], "managed session launcher hash")
    _positive(launch["launcher_size"], "managed session launcher size")
    if launch["launcher_pid"] is not None:
        _positive(launch["launcher_pid"], "managed session launcher PID")
    elif launch["ownership"] != "incomplete":
        raise PackProgramError("owned managed session launch has no launcher PID")
    if launch["launcher_returncode"] is not None and (
        isinstance(launch["launcher_returncode"], bool)
        or not isinstance(launch["launcher_returncode"], int)
    ):
        raise PackProgramError("managed session launcher return code is invalid")
    processes = launch["client_processes"]
    if not isinstance(processes, list) or len(processes) > 64:
        raise PackProgramError("managed session client process inventory is malformed")
    pids: list[int] = []
    for row in processes:
        process = _exact(row, {"pid", "tracker", "creation_date"}, "managed client process")
        _positive(process["pid"], "managed client PID")
        _text(process["tracker"], "managed client tracker", 256)
        if process["creation_date"] is not None:
            _text(process["creation_date"], "managed client creation date", 256)
        pids.append(process["pid"])
    if len(pids) != len(set(pids)):
        raise PackProgramError("managed session client PID is duplicate")
    if launch["ownership"] not in {"session-owned", "incomplete"}:
        raise PackProgramError("managed session process ownership is invalid")
    return launch


def _overlay(value: Any) -> Mapping[str, Any]:
    overlay = _exact(
        value,
        {"role", "path", "original", "applied", "restore"},
        "managed session overlay",
    )
    if overlay["role"] not in {"launcher-jvm", "language-server-port"}:
        raise PackProgramError("managed session overlay role is invalid")
    _text(overlay["path"], "managed session overlay path", 8192)
    original = _exact(
        overlay["original"], {"sha256", "size", "backup_path"}, "overlay original"
    )
    applied = _exact(overlay["applied"], {"sha256", "size"}, "overlay applied")
    restore = _exact(
        overlay["restore"],
        {"state", "sha256", "preserved_external_changes"},
        "overlay restore",
    )
    for record, label in ((original, "original"), (applied, "applied")):
        _digest(record["sha256"], f"overlay {label} hash")
        _nonnegative(record["size"], f"overlay {label} size")
    _text(original["backup_path"], "overlay backup path", 8192)
    if restore["state"] not in {"restored-exact", "restored-merged", "conflict", "not-applied"}:
        raise PackProgramError("managed session overlay restore state is invalid")
    if not isinstance(restore["preserved_external_changes"], bool):
        raise PackProgramError("managed session overlay merge flag is invalid")
    if restore["sha256"] is not None:
        _digest(restore["sha256"], "overlay restored hash")
    if restore["state"] == "restored-exact" and restore["sha256"] != original["sha256"]:
        raise PackProgramError("managed session overlay did not restore original bytes")
    if restore["preserved_external_changes"] != (restore["state"] == "restored-merged"):
        raise PackProgramError("managed session overlay merge claim is stale")
    return overlay


def _limitations(value: Any) -> None:
    if not isinstance(value, list) or not value or len(value) > 128 or len(value) != len(set(value)):
        raise PackProgramError("managed session limitations are malformed")
    for item in value:
        _text(item, "managed session limitation", 8192)


def _pids(value: Any, context: str) -> list[int]:
    if not isinstance(value, list) or len(value) > 64:
        raise PackProgramError(f"{context} are malformed")
    for item in value:
        _positive(item, context)
    if len(value) != len(set(value)):
        raise PackProgramError(f"{context} contain duplicates")
    return value


def _session_id(value: Any) -> str:
    if not isinstance(value, str) or not _SESSION.fullmatch(value):
        raise PackProgramError("managed language-session ID is malformed")
    return value


def _digest(value: Any, context: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise PackProgramError(f"{context} is malformed")
    return value


def _positive(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PackProgramError(f"{context} must be a positive integer")
    return value


def _nonnegative(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PackProgramError(f"{context} must be a non-negative integer")
    return value


def _text(value: Any, context: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise PackProgramError(f"{context} must be bounded non-empty text")
    if any(ord(character) < 32 and character not in "\t\n" for character in value):
        raise PackProgramError(f"{context} contains control characters")
    return value


def _exact(value: Any, keys: set[str], context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise PackProgramError(f"{context} has unexpected keys")
    return value


__all__ = [
    "DESCRIPTOR_FORMAT",
    "RECEIPT_FORMAT",
    "SCHEMA_VERSION",
    "command_identity",
    "descriptor_identity",
    "receipt_identity",
    "validate_managed_session_receipt",
    "validate_session_descriptor",
]
