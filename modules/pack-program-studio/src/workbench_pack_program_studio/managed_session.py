"""Managed physical-client lifecycle for GroovyScript language sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import socket
import stat
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote, urlsplit
from urllib.request import url2pathname
import uuid

from .analyzer import AnalysisContext, analyze_program
from .language_profile import LoadedLanguageProfile
from .language_service import inventory_language_runtime
from .lsp import LspLimits, probe_language_server
from .managed_model import (
    DESCRIPTOR_FORMAT,
    RECEIPT_FORMAT,
    command_identity,
    descriptor_identity,
    receipt_identity,
    validate_managed_session_receipt,
    validate_session_descriptor,
)
from .managed_profile import LoadedManagedSessionProfile
from .model import PackProgramError, canonical_bytes
from .profile import LoadedProfile, safe_regular_bytes, strict_json_file


_MAX_CONTROL_BYTES = 64 * 1024 * 1024
_MAX_OVERLAY_BYTES = 4 * 1024 * 1024
_LOCK_NAME = ".workbench-groovy-language-service.lock"
_EVENTS_NAME = "events-v1.jsonl"
_DESCRIPTOR_NAME = "session-descriptor-v1.json"
_RECEIPT_NAME = "session-receipt-v1.json"
_POWERSHELL_QUERY = r'''
$needle = $env:WORKBENCH_GROOVY_INSTANCE_TOKEN
if ([string]::IsNullOrWhiteSpace($needle)) {
  throw "missing Workbench instance token"
}
$port = [int]$env:WORKBENCH_GROOVY_ENDPOINT_PORT
$portPids = @()
if ($port -gt 0) {
  $portPids = @(
    Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue |
      ForEach-Object { [int]$_.OwningProcess }
  )
}
$rows = @(
  Get-CimInstance Win32_Process |
    Where-Object {
      ($_.Name -eq "java.exe" -or $_.Name -eq "javaw.exe") -and
      (($_.CommandLine -and $_.CommandLine.Contains($needle)) -or
       ($portPids -contains [int]$_.ProcessId))
    } |
    ForEach-Object {
      [pscustomobject]@{
        pid = [int]$_.ProcessId
        creation_date = [string]$_.CreationDate
        executable_path = [string]$_.ExecutablePath
        tracker = $(if ($portPids -contains [int]$_.ProcessId) { "windows-tcp-endpoint-port" } else { "windows-cim-instance-id" })
      }
    }
)
ConvertTo-Json -Compress -InputObject $rows
'''.strip()
_POWERSHELL_QUERY_PIDS = r'''
$ids = @($env:WORKBENCH_GROOVY_PROCESS_IDS -split ',' | Where-Object { $_ } | ForEach-Object { [int]$_ })
if ($ids.Count -eq 0) {
  throw "missing Workbench process IDs"
}
$rows = @(
  Get-CimInstance Win32_Process |
    Where-Object { $ids -contains [int]$_.ProcessId } |
    ForEach-Object {
      [pscustomobject]@{
        pid = [int]$_.ProcessId
        creation_date = [string]$_.CreationDate
        executable_path = [string]$_.ExecutablePath
        tracker = "windows-pid-revalidation"
      }
    }
)
ConvertTo-Json -Compress -InputObject $rows
'''.strip()
_POWERSHELL_CLOSE = r'''
$expected = @(ConvertFrom-Json -InputObject $env:WORKBENCH_GROOVY_PROCESS_IDENTITIES)
if ($expected.Count -eq 0) {
  throw "missing Workbench process identities"
}
$matched = @(
  Get-CimInstance Win32_Process |
    Where-Object {
      $candidate = $_
      @($expected | Where-Object {
        [int]$_.pid -eq [int]$candidate.ProcessId -and
        [string]$_.creation_date -eq [string]$candidate.CreationDate -and
        [string]$_.executable_path -ieq [string]$candidate.ExecutablePath
      }).Count -eq 1
    } |
    ForEach-Object {
      $process = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
      if ($process -and $process.MainWindowHandle -ne 0) {
        [void]$process.CloseMainWindow()
      }
      [int]$_.ProcessId
    }
)
ConvertTo-Json -Compress -InputObject $matched
'''.strip()
_POWERSHELL_FORCE = r'''
$expected = @(ConvertFrom-Json -InputObject $env:WORKBENCH_GROOVY_PROCESS_IDENTITIES)
if ($expected.Count -eq 0) {
  throw "missing Workbench process identities"
}
$matched = @(
  Get-CimInstance Win32_Process |
    Where-Object {
      $candidate = $_
      @($expected | Where-Object {
        [int]$_.pid -eq [int]$candidate.ProcessId -and
        [string]$_.creation_date -eq [string]$candidate.CreationDate -and
        [string]$_.executable_path -ieq [string]$candidate.ExecutablePath
      }).Count -eq 1
    } |
    ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      [int]$_.ProcessId
    }
)
ConvertTo-Json -Compress -InputObject $matched
'''.strip()
_POWERSHELL_PORT_AVAILABLE = r'''
$port = [int]$env:WORKBENCH_GROOVY_ENDPOINT_PORT
if ($port -le 0) {
  throw "missing Workbench endpoint port"
}
$listener = $null
$available = $false
try {
  if (@(Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue).Count -gt 0) {
    ConvertTo-Json -Compress -InputObject $false
    exit 0
  }
  $listener = [System.Net.Sockets.TcpListener]::new(
    [System.Net.IPAddress]::IPv6Any,
    $port
  )
  $listener.Server.DualMode = $true
  $listener.Server.ExclusiveAddressUse = $true
  $listener.Start()
  $available = $true
} catch [System.Net.Sockets.SocketException] {
  $available = $false
} finally {
  if ($null -ne $listener) {
    $listener.Stop()
  }
}
ConvertTo-Json -Compress -InputObject $available
'''.strip()
_POWERSHELL_EPHEMERAL_PORT = r'''
$listener = [System.Net.Sockets.TcpListener]::new(
  [System.Net.IPAddress]::IPv6Any,
  0
)
try {
  $listener.Server.DualMode = $true
  $listener.Server.ExclusiveAddressUse = $true
  $listener.Start()
  [int]$listener.LocalEndpoint.Port | ConvertTo-Json -Compress
} finally {
  $listener.Stop()
}
'''.strip()
_POWERSHELL_STDIO_PROXY = r'''
$port = [int]$env:WORKBENCH_GROOVY_UPSTREAM_PORT
if ($port -le 0) {
  throw "missing Workbench upstream port"
}
$client = [System.Net.Sockets.TcpClient]::new()
try {
  $client.NoDelay = $true
  $client.Connect([System.Net.IPAddress]::Loopback, $port)
  $network = $client.GetStream()
  $standardInput = [Console]::OpenStandardInput()
  $standardOutput = [Console]::OpenStandardOutput()
  $toNetwork = $standardInput.CopyToAsync($network)
  $toOutput = $network.CopyToAsync($standardOutput)
  [void][System.Threading.Tasks.Task]::WhenAny(@($toNetwork, $toOutput)).GetAwaiter().GetResult()
} finally {
  if ($client.Connected) {
    try { $client.Client.Shutdown([System.Net.Sockets.SocketShutdown]::Both) } catch {}
  }
  $client.Dispose()
}
'''.strip()


@dataclass(frozen=True, slots=True)
class PrismLaunchBinding:
    receipt_path: Path
    receipt_sha256: str
    receipt_size: int
    value: Mapping[str, Any]
    instance_root: Path
    runtime_root: Path
    instance_id: str
    command: tuple[str, ...]
    cwd: Path
    launcher_path: Path
    launcher_sha256: str
    launcher_size: int
    java_path: Path | None
    host_os: str


@dataclass(slots=True)
class OverlayRecord:
    role: str
    path: Path
    original: bytes
    applied: bytes
    backup_path: Path
    applied_to_target: bool = False
    restore_state: str = "not-applied"
    restore_sha256: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "path": str(self.path),
            "original": {
                "sha256": _sha(self.original),
                "size": len(self.original),
                "backup_path": str(self.backup_path),
            },
            "applied": {
                "sha256": _sha(self.applied),
                "size": len(self.applied),
            },
            "restore": {
                "state": self.restore_state,
                "sha256": self.restore_sha256,
                "preserved_external_changes": self.restore_state == "restored-merged",
            },
        }


class EventJournal:
    def __init__(self, path: Path, session_id: str) -> None:
        self.path = path
        self.session_id = session_id
        self.sequence = 0
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        self.handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")

    def emit(self, state: str, **details: Any) -> dict[str, Any]:
        self.sequence += 1
        event = {
            "format": "workbench-groovy-language-session-event-v1",
            "schema_version": 1,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "observed_at": _utc(),
            "state": state,
            "details": details,
        }
        self.handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        self.handle.flush()
        os.fsync(self.handle.fileno())
        return event

    def close(self) -> None:
        if not self.handle.closed:
            self.handle.close()


class WindowsStdioTcpBridge:
    """Route one WSL public connection to a distinct Windows loopback port."""

    def __init__(
        self, listener: socket.socket, endpoint_port: int, upstream_port: int
    ) -> None:
        self.listener = listener
        self.endpoint_port = endpoint_port
        self.upstream_port = upstream_port
        self.stop_requested = threading.Event()
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.active_connection: socket.socket | None = None
        self.active_process: subprocess.Popen[bytes] | None = None
        self.errors: list[str] = []

    def start(self) -> None:
        self.listener.listen(4)
        self.listener.settimeout(0.25)
        self.thread = threading.Thread(
            target=self._serve,
            name=f"workbench-groovy-windows-bridge-{self.endpoint_port}",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> list[str]:
        self.stop_requested.set()
        try:
            self.listener.close()
        except OSError:
            pass
        with self.lock:
            connection = self.active_connection
            process = self.active_process
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        if self.thread is not None:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                self.errors.append("Windows stdio TCP bridge did not stop within its bound")
        return list(dict.fromkeys(self.errors))

    def _serve(self) -> None:
        while not self.stop_requested.is_set():
            try:
                connection, _address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError as exc:
                if not self.stop_requested.is_set():
                    self.errors.append(
                        f"Windows stdio TCP bridge accept failed: {_safe_text(str(exc), 1024)}"
                    )
                return
            try:
                self._proxy(connection)
            except (OSError, ValueError, PackProgramError, subprocess.SubprocessError) as exc:
                self.errors.append(
                    f"Windows stdio TCP bridge failed: {_safe_text(str(exc), 1024)}"
                )
                return
            finally:
                connection.close()

    def _proxy(self, connection: socket.socket) -> None:
        process = _spawn_windows_stdio_proxy(self.upstream_port)
        with self.lock:
            self.active_connection = connection
            self.active_process = process
        upstream = threading.Thread(
            target=_copy_socket_to_pipe,
            args=(connection, process.stdin),
            name="workbench-groovy-bridge-upstream",
            daemon=True,
        )
        downstream = threading.Thread(
            target=_copy_pipe_to_socket,
            args=(process.stdout, connection),
            name="workbench-groovy-bridge-downstream",
            daemon=True,
        )
        upstream.start()
        downstream.start()
        try:
            while upstream.is_alive() and downstream.is_alive() and not self.stop_requested.is_set():
                upstream.join(timeout=0.1)
                downstream.join(timeout=0.1)
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            if process.poll() is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                process.wait(timeout=3)
            upstream.join(timeout=1)
            downstream.join(timeout=1)
            if upstream.is_alive() or downstream.is_alive():
                self.errors.append("Windows stdio TCP bridge pump did not stop within its bound")
        finally:
            with self.lock:
                if self.active_process is process:
                    self.active_process = None
                    self.active_connection = None


def _copy_socket_to_pipe(
    connection: socket.socket, pipe: Any | None
) -> None:
    if pipe is None:
        return
    try:
        while True:
            payload = connection.recv(64 * 1024)
            if not payload:
                break
            pipe.write(payload)
            pipe.flush()
    except (BrokenPipeError, ConnectionError, OSError, ValueError):
        pass
    finally:
        try:
            pipe.close()
        except (OSError, ValueError):
            pass


def _copy_pipe_to_socket(pipe: Any | None, connection: socket.socket) -> None:
    if pipe is None:
        return
    try:
        while True:
            payload = pipe.read(64 * 1024)
            if not payload:
                break
            connection.sendall(payload)
    except (BrokenPipeError, ConnectionError, OSError, ValueError):
        pass
    finally:
        try:
            connection.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        try:
            pipe.close()
        except (OSError, ValueError):
            pass


def run_managed_language_session(
    *,
    source: Path,
    pack_profile: LoadedProfile,
    language_profile: LoadedLanguageProfile,
    managed_profile: LoadedManagedSessionProfile,
    context: AnalysisContext,
    runtime_root: Path,
    launch_receipt: Path,
    session_storage: Path,
    requested_port: int | None,
    readiness_timeout: float | None,
    session_timeout: float | None,
    connect_timeout: float,
    diagnostic_timeout: float,
    stop_event: threading.Event | None = None,
    on_event: Callable[[Mapping[str, Any]], None] | None = None,
    on_ready: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Launch, prove, expose, supervise, and close one exact client session."""

    if context.side != "client":
        raise PackProgramError("managed Groovy language sessions require client side")
    if pack_profile.platform_profile_id != language_profile.platform_profile_id:
        raise PackProgramError("managed session pack and language profiles differ")
    if managed_profile.language_service_profile_id != language_profile.profile_id:
        raise PackProgramError("managed session and language-service profiles differ")
    lifecycle = managed_profile.value["lifecycle"]
    ready_limit = _timeout(
        lifecycle["default_readiness_timeout_seconds"]
        if readiness_timeout is None
        else readiness_timeout,
        "managed session readiness timeout",
        maximum=3600,
    )
    session_limit = _timeout(
        lifecycle["default_session_timeout_seconds"]
        if session_timeout is None
        else session_timeout,
        "managed session timeout",
        maximum=86400,
    )
    connect_limit = _timeout(connect_timeout, "managed session connect timeout", maximum=300)
    diagnostic_limit = _timeout(
        diagnostic_timeout, "managed session diagnostic timeout", maximum=300
    )

    program = analyze_program(source, pack_profile, context=context)
    binding = load_prism_launch_binding(
        launch_receipt,
        runtime_root=runtime_root,
        managed_profile=managed_profile,
    )
    bridge_required = _requires_windows_stdio_bridge(binding)
    runtime = inventory_language_runtime(
        binding.runtime_root,
        program=program,
        language_profile=language_profile,
        java=binding.java_path,
        runtime_receipt=binding.receipt_path,
    )
    workspace_root = Path(program["binding"]["groovy_root"]).resolve()
    workspace_uri = workspace_root.as_uri()
    server_workspace_uri = _server_workspace_uri(
        workspace_root,
        bridge_required=bridge_required,
    )
    profile_binding = {
        "pack_program_profile_id": pack_profile.profile_id,
        "pack_program_profile_sha256": pack_profile.sha256,
        "language_service_profile_id": language_profile.profile_id,
        "language_service_profile_sha256": language_profile.sha256,
        "managed_session_profile_id": managed_profile.profile_id,
        "managed_session_profile_sha256": managed_profile.sha256,
        "platform_profile_id": language_profile.platform_profile_id,
    }
    program_binding = {
        "program_id": program["program_id"],
        "source_sha256": program["binding"]["source_sha256"],
        "source_root": str(Path(program["binding"]["groovy_root"]).resolve()),
        "workspace_uri": workspace_uri,
        "server_workspace_uri": server_workspace_uri,
    }
    runtime_binding = {
        "runtime_id": runtime["runtime_id"],
        "root": runtime["root"],
        "mod_graph_id": runtime["mod_graph"]["mod_graph_id"],
        "java_sha256": runtime["java"]["sha256"],
        "launch_receipt_path": str(binding.receipt_path),
        "launch_receipt_sha256": binding.receipt_sha256,
        "launch_receipt_size": binding.receipt_size,
    }

    session_id = f"workbench-groovy-language-session:{uuid.uuid4()}"
    session_dir = _create_session_directory(session_storage, session_id)
    descriptor_path = session_dir / _DESCRIPTOR_NAME
    receipt_path = session_dir / _RECEIPT_NAME
    journal = EventJournal(session_dir / _EVENTS_NAME, session_id)
    started_at = _utc()
    emitted = _emitter(journal, on_event)
    emitted(
        "preflight-complete",
        runtime_id=runtime["runtime_id"],
        instance_id=binding.instance_id,
        command=list(binding.command),
        cwd=str(binding.cwd),
        intended_mutations=[
            str(binding.instance_root / managed_profile.value["launcher"]["instance_config"]),
            str(binding.runtime_root / managed_profile.value["overlay"]["runtime_config"]),
            str(binding.instance_root / _LOCK_NAME),
        ],
    )

    reservation, endpoint_port, upstream_port, allocation = _reserve_managed_endpoint(
        requested_port,
        bridge_required=bridge_required,
    )
    endpoint = {
        "host": "127.0.0.1",
        "port": endpoint_port,
        "upstream": {"host": "127.0.0.1", "port": upstream_port},
        "transport": "lsp-jsonrpc-tcp",
        "allocation": allocation,
        "connection_model": "one-active-client-sequential-reaccept",
        "identity_binding": "managed-launch-custody; no-upstream-identity-challenge",
        "route": (
            "wsl-windows-stdio-bridge"
            if bridge_required
            else "direct-loopback"
        ),
    }
    emitted(
        "endpoint-reserved",
        host="127.0.0.1",
        port=endpoint_port,
        upstream_port=upstream_port,
        allocation=allocation,
        route=endpoint["route"],
    )

    try:
        overlay_records = _prepare_overlays(
            binding,
            managed_profile=managed_profile,
            session_dir=session_dir,
            port=upstream_port,
        )
    except Exception:
        reservation.close()
        journal.close()
        raise
    lock_path = binding.instance_root / _LOCK_NAME
    lock_payload = canonical_bytes(
        {"format": "workbench-groovy-language-session-lock-v1", "session_id": session_id}
    ) + b"\n"
    process: subprocess.Popen[bytes] | None = None
    bridge: WindowsStdioTcpBridge | None = None
    previous_windows_processes: dict[int, dict[str, Any]] = {}
    client_processes: dict[int, dict[str, Any]] = {}
    readiness: dict[str, Any] = {
        "state": "blocked",
        "port": endpoint_port,
        "attempts": 1,
        "latency_ms": 0,
        "canary_state": None,
        "capabilities": None,
        "transcript_sha256": None,
        "failure": {"kind": "not-started", "message": "launch did not start"},
    }
    descriptor: dict[str, Any] | None = None
    ready_at: str | None = None
    outcome = "launch-failed"
    final_state = "blocked"
    shutdown_reason = "launch-failed"
    shutdown_graceful = False
    shutdown_forced = False
    orphaned: list[int] = []
    cleanup_errors: list[str] = []
    lock_created = False

    try:
        _create_lock(lock_path, lock_payload)
        lock_created = True
        if binding.host_os == "windows":
            previous_windows_processes = _query_windows_processes(binding.instance_id)
            if previous_windows_processes:
                raise PackProgramError(
                    "the disposable Prism instance already has a matching Java process"
                )
        _apply_overlays(overlay_records)
        emitted(
            "overlays-applied",
            files=[record.path.name for record in overlay_records],
            upstream_port=upstream_port,
        )
        if bridge_required:
            bridge = WindowsStdioTcpBridge(
                reservation,
                endpoint_port,
                upstream_port,
            )
            bridge.start()
            reservation = None
            emitted(
                "transport-bridge-started",
                route=endpoint["route"],
                host="127.0.0.1",
                port=endpoint_port,
                upstream_port=upstream_port,
            )
        else:
            reservation.close()
            reservation = None
        process = _launch(binding)
        emitted(
            "client-launch-started",
            launcher_pid=process.pid,
            command_sha256=command_identity(list(binding.command)),
        )

        readiness, discovered = _await_readiness(
            process=process,
            binding=binding,
            language_profile=language_profile,
            managed_profile=managed_profile,
            workspace_uri=server_workspace_uri,
            port=endpoint_port,
            process_port=upstream_port,
            readiness_timeout=ready_limit,
            connect_timeout=connect_limit,
            diagnostic_timeout=diagnostic_limit,
            previous_windows_processes=previous_windows_processes,
            stop_event=stop_event,
            emit=emitted,
        )
        client_processes.update(discovered)
        if readiness["state"] != "confirmed":
            outcome = "readiness-failed"
            shutdown_reason = "readiness-failed"
        else:
            ready_at = _utc()
            descriptor = _build_descriptor(
                session_id=session_id,
                emitted_at=ready_at,
                profile=profile_binding,
                program=program_binding,
                runtime=runtime_binding,
                endpoint=endpoint,
                readiness=readiness,
                descriptor_path=descriptor_path,
                receipt_path=receipt_path,
                events_path=journal.path,
                limitations=managed_profile.value["limitations"],
            )
            _write_fresh_json(descriptor_path, descriptor)
            emitted(
                "ready",
                descriptor_id=descriptor["descriptor_id"],
                descriptor_path=str(descriptor_path),
                host="127.0.0.1",
                port=endpoint_port,
                descriptor=descriptor,
            )
            if on_ready is not None:
                on_ready(descriptor)
            shutdown_reason = _hold_session(
                process=process,
                binding=binding,
                owned_windows_processes=client_processes,
                port=upstream_port,
                stop_event=stop_event,
                timeout=session_limit,
            )
            outcome = "ready-session-closed"
            final_state = "complete"
    except (OSError, ValueError, PackProgramError, subprocess.SubprocessError) as exc:
        outcome = "session-failed"
        shutdown_reason = "session-failed"
        readiness["failure"] = {
            "kind": type(exc).__name__,
            "message": _safe_text(str(exc), 4096) or "managed session failed",
        }
        emitted("failure", kind=type(exc).__name__, message=_safe_text(str(exc), 4096))
    finally:
        if reservation is not None:
            reservation.close()
        if bridge is not None:
            cleanup_errors.extend(bridge.stop())
        if process is not None:
            try:
                shutdown_graceful, shutdown_forced, orphaned, discovered = _shutdown(
                    process,
                    binding=binding,
                    known_windows_processes=client_processes,
                    port=upstream_port,
                    graceful_seconds=float(lifecycle["graceful_shutdown_seconds"]),
                    force_seconds=float(lifecycle["force_shutdown_seconds"]),
                )
                client_processes.update(discovered)
            except (OSError, ValueError, PackProgramError, subprocess.SubprocessError) as exc:
                cleanup_errors.append(f"process shutdown failed: {_safe_text(str(exc), 2048)}")
        for record in reversed(overlay_records):
            try:
                _restore_overlay(record)
            except (OSError, PackProgramError) as exc:
                cleanup_errors.append(
                    f"overlay restoration failed for {record.path}: {_safe_text(str(exc), 2048)}"
                )
        if lock_created:
            try:
                _remove_exact_lock(lock_path, lock_payload)
            except (OSError, PackProgramError) as exc:
                cleanup_errors.append(f"session lock cleanup failed: {_safe_text(str(exc), 2048)}")
        if orphaned:
            cleanup_errors.append(
                "session-owned client processes remain: " + ", ".join(str(item) for item in orphaned)
            )
        if any(not record.restore_state.startswith("restored-") for record in overlay_records):
            cleanup_errors.append("one or more checked overlays were not restored")
        if cleanup_errors:
            final_state = "blocked"
            outcome = "cleanup-incomplete"
        emitted(
            "closed" if final_state == "complete" else "blocked",
            outcome=outcome,
            shutdown_reason=shutdown_reason,
            cleanup_errors=cleanup_errors,
        )
        journal.close()

    events_identity = _file_identity(journal.path, maximum=_MAX_CONTROL_BYTES)
    limitations = list(
        dict.fromkeys(
            [
                *managed_profile.value["limitations"],
                "The descriptor is a local session handoff, not Atlas evidence or Blueprint construction authority.",
                *cleanup_errors,
            ]
        )
    )
    launch_public = {
        "command": list(binding.command),
        "command_sha256": command_identity(list(binding.command)),
        "cwd": str(binding.cwd),
        "launcher_path": str(binding.launcher_path),
        "launcher_sha256": binding.launcher_sha256,
        "launcher_size": binding.launcher_size,
        "launcher_pid": None if process is None else process.pid,
        "launcher_returncode": None if process is None else process.poll(),
        "client_processes": [
            {
                "pid": client_processes[key]["pid"],
                "tracker": client_processes[key]["tracker"],
                "creation_date": client_processes[key].get("creation_date"),
            }
            for key in sorted(client_processes)
        ],
        "ownership": (
            "session-owned"
            if process is not None and client_processes and not orphaned
            else "incomplete"
        ),
    }
    if final_state == "complete" and launch_public["ownership"] != "session-owned":
        final_state = "blocked"
        outcome = "cleanup-incomplete"
        limitations.append("The launcher did not enter exact session ownership.")
    receipt: dict[str, Any] = {
        "format": RECEIPT_FORMAT,
        "schema_version": 1,
        "receipt_id": "",
        "session_id": session_id,
        "operation_class": "local-mutation",
        "started_at": started_at,
        "ready_at": ready_at,
        "ended_at": _utc(),
        "state": final_state,
        "outcome": outcome,
        "profile": profile_binding,
        "program": program_binding,
        "runtime": runtime_binding,
        "launch": launch_public,
        "endpoint": endpoint,
        "readiness": readiness,
        "overlays": [record.public() for record in overlay_records],
        "handoff": descriptor,
        "shutdown": {
            "reason": shutdown_reason,
            "graceful_attempted": shutdown_graceful,
            "forced": shutdown_forced,
            "orphaned_pids": sorted(orphaned),
        },
        "events": {
            "path": str(journal.path),
            "sha256": events_identity["sha256"],
            "size": events_identity["size"],
            "count": journal.sequence,
        },
        "limitations": limitations,
    }
    receipt["receipt_id"] = receipt_identity(receipt)
    validated = validate_managed_session_receipt(receipt)
    _write_fresh_json(receipt_path, validated)
    return validated


def load_prism_launch_binding(
    path: Path,
    *,
    runtime_root: Path,
    managed_profile: LoadedManagedSessionProfile,
) -> PrismLaunchBinding:
    requested = path.expanduser().resolve()
    value, raw = strict_json_file(requested, maximum=_MAX_CONTROL_BYTES)
    if not isinstance(value, Mapping):
        raise PackProgramError("managed session launch receipt must be an object")
    accepted = managed_profile.value["launcher"]["accepted_receipt_formats"]
    if value.get("format") not in accepted or value.get("schema_version") != 3:
        raise PackProgramError("managed session requires a completed V3 runtime launch receipt")
    if value.get("operation_class") != "local-mutation":
        raise PackProgramError("managed session launch receipt is not a local mutation receipt")
    observation = value.get("observation")
    session_exit = observation.get("session_exit") if isinstance(observation, Mapping) else None
    if not isinstance(session_exit, Mapping) or session_exit.get("state") != "exited":
        raise PackProgramError("managed session launch receipt has no completed client-exit observation")

    launcher = value.get("launcher")
    projection = value.get("projection")
    java = value.get("java")
    if not isinstance(launcher, Mapping) or launcher.get("family") != "prism":
        raise PackProgramError("managed session launch receipt is not a Prism launch")
    if not isinstance(projection, Mapping):
        raise PackProgramError("managed session launch receipt has no projection")
    instance_id = _bounded_text(launcher.get("instance_id"), "Prism instance ID", 256)
    prefix = managed_profile.value["launcher"]["disposable_instance_prefix"]
    if not instance_id.startswith(prefix) or not re.fullmatch(r"[A-Za-z0-9_.-]+", instance_id):
        raise PackProgramError("managed session requires an exact disposable Workbench instance ID")
    instance_root = _file_uri_path(
        projection.get("projection_uri"), "Prism projection URI"
    )
    if instance_root.name != instance_id or not instance_root.is_dir() or instance_root.is_symlink():
        raise PackProgramError("Prism projection URI does not bind the disposable instance")
    resolved_runtime = runtime_root.expanduser().resolve()
    expected_runtime = (instance_root / ".minecraft").resolve()
    if resolved_runtime != expected_runtime or not resolved_runtime.is_dir() or resolved_runtime.is_symlink():
        raise PackProgramError("managed runtime root is not the receipt-bound Prism projection")

    command = launcher.get("command")
    if not isinstance(command, list) or not command or len(command) > 256:
        raise PackProgramError("Prism launch receipt command is malformed")
    normalized_command = tuple(
        _bounded_text(item, "Prism launch command argument", 16384) for item in command
    )
    launch_flag = managed_profile.value["launcher"]["launch_flag"]
    data_flag = managed_profile.value["launcher"]["data_root_flag"]
    if _flag_value(normalized_command, launch_flag) != instance_id:
        raise PackProgramError("Prism command does not launch the receipt-bound instance")
    _bounded_text(_flag_value(normalized_command, data_flag), "Prism data root argument", 8192)

    launcher_path = Path(normalized_command[0]).expanduser().resolve()
    executable_uri = _file_uri_path(launcher.get("executable_uri"), "Prism executable URI")
    if launcher_path != executable_uri:
        raise PackProgramError("Prism command executable differs from its receipt identity")
    launcher_identity = _file_identity(launcher_path, maximum=512 * 1024 * 1024)
    if (
        launcher_identity["sha256"] != launcher.get("sha256")
        or launcher_identity["size"] != launcher.get("size")
    ):
        raise PackProgramError("Prism launcher bytes differ from the launch receipt")
    data_root = launcher.get("data_root")
    if not isinstance(data_root, Mapping):
        raise PackProgramError("Prism launch receipt has no data-root binding")
    cwd = _file_uri_path(data_root.get("root_uri"), "Prism data-root URI")
    if not cwd.is_dir() or cwd.is_symlink():
        raise PackProgramError("Prism data root is unavailable or unsafe")

    java_path: Path | None = None
    if isinstance(java, Mapping) and java.get("java_uri") is not None:
        candidate = _file_uri_path(java.get("java_uri"), "managed Java URI")
        if candidate.is_file() and not candidate.is_symlink():
            java_path = candidate
    host = launcher.get("host")
    host_os = str(host.get("os", "")) if isinstance(host, Mapping) else ""
    if host_os not in {"linux", "macos", "windows"}:
        raise PackProgramError("Prism launch receipt host OS is unsupported")
    return PrismLaunchBinding(
        receipt_path=requested,
        receipt_sha256=_sha(raw),
        receipt_size=len(raw),
        value=value,
        instance_root=instance_root,
        runtime_root=resolved_runtime,
        instance_id=instance_id,
        command=normalized_command,
        cwd=cwd,
        launcher_path=launcher_path,
        launcher_sha256=launcher_identity["sha256"],
        launcher_size=launcher_identity["size"],
        java_path=java_path,
        host_os=host_os,
    )


def _prepare_overlays(
    binding: PrismLaunchBinding,
    *,
    managed_profile: LoadedManagedSessionProfile,
    session_dir: Path,
    port: int,
) -> list[OverlayRecord]:
    launcher_policy = managed_profile.value["launcher"]
    overlay_policy = managed_profile.value["overlay"]
    instance_config = _safe_projection_file(
        binding.instance_root,
        PurePosixPath(launcher_policy["instance_config"]),
        "Prism instance config",
    )
    runtime_config = _safe_projection_file(
        binding.runtime_root,
        PurePosixPath(overlay_policy["runtime_config"]),
        "GroovyScript runtime config",
    )
    instance_raw = safe_regular_bytes(instance_config, maximum=_MAX_OVERLAY_BYTES)
    runtime_raw = safe_regular_bytes(runtime_config, maximum=_MAX_OVERLAY_BYTES)
    patched_instance = _patch_instance_config(
        instance_raw,
        jvm_key=launcher_policy["jvm_arguments_key"],
        override_key=launcher_policy["override_jvm_arguments_key"],
        start_argument=overlay_policy["start_jvm_argument"],
    )
    patched_runtime = _patch_forge_int(
        runtime_raw,
        key=overlay_policy["port_property_key"],
        value=port,
    )
    backup_root = session_dir / "overlay-originals"
    backup_root.mkdir(mode=0o700)
    records = [
        OverlayRecord(
            role="launcher-jvm",
            path=instance_config,
            original=instance_raw,
            applied=patched_instance,
            backup_path=backup_root / "instance.cfg",
        ),
        OverlayRecord(
            role="language-server-port",
            path=runtime_config,
            original=runtime_raw,
            applied=patched_runtime,
            backup_path=backup_root / "groovyscript.cfg",
        ),
    ]
    for record in records:
        _write_fresh_bytes(record.backup_path, record.original, mode=0o600)
    return records


def _apply_overlays(records: Sequence[OverlayRecord]) -> None:
    applied: list[OverlayRecord] = []
    try:
        for record in records:
            _replace_if_hash(record.path, record.original, record.applied)
            record.applied_to_target = True
            applied.append(record)
    except (OSError, PackProgramError):
        for record in reversed(applied):
            _restore_overlay(record)
        raise


def _restore_overlay(record: OverlayRecord) -> None:
    if not record.applied_to_target:
        record.restore_state = "not-applied"
        record.restore_sha256 = _sha(record.original)
        return
    current = safe_regular_bytes(record.path, maximum=_MAX_OVERLAY_BYTES)
    if current == record.original:
        record.restore_state = "restored-exact"
        record.restore_sha256 = _sha(current)
        return
    if current == record.applied:
        restored_payload = record.original
    else:
        restored_payload = _merge_overlay_restoration(record, current)
    if restored_payload is None:
        record.restore_state = "conflict"
        record.restore_sha256 = _sha(current)
        return
    _replace_if_hash(record.path, current, restored_payload)
    restored = safe_regular_bytes(record.path, maximum=_MAX_OVERLAY_BYTES)
    record.restore_sha256 = _sha(restored)
    if restored != restored_payload:
        record.restore_state = "conflict"
    elif restored == record.original:
        record.restore_state = "restored-exact"
    else:
        record.restore_state = "restored-merged"


def _merge_overlay_restoration(record: OverlayRecord, current: bytes) -> bytes | None:
    """Restore only owned fields when the launcher changed unrelated bytes."""

    if record.role == "launcher-jvm":
        original_text = _decode_overlay(record.original, "original Prism instance config")
        applied_text = _decode_overlay(record.applied, "applied Prism instance config")
        current_text = _decode_overlay(current, "current Prism instance config")
        original_jvm = _single_ini_value(original_text, "JvmArgs")
        original_override = _single_ini_value(original_text, "OverrideJavaArgs")
        applied_jvm = _single_ini_value(applied_text, "JvmArgs")
        applied_override = _single_ini_value(applied_text, "OverrideJavaArgs")
        current_jvm = _single_ini_value(current_text, "JvmArgs")
        current_override = _single_ini_value(current_text, "OverrideJavaArgs")
        jvm_is_applied = _prism_value_equivalent(current_jvm, applied_jvm)
        jvm_is_original = _prism_value_equivalent(current_jvm, original_jvm)
        override_is_applied = current_override.casefold() == applied_override.casefold()
        override_is_original = current_override.casefold() == original_override.casefold()
        if not (jvm_is_applied or jvm_is_original) or not (
            override_is_applied or override_is_original
        ):
            return None
        merged = _replace_single_ini_value(current_text, "JvmArgs", original_jvm)
        merged = _replace_single_ini_value(
            merged, "OverrideJavaArgs", original_override
        )
        return merged.encode("utf-8")
    if record.role == "language-server-port":
        original_text = _decode_overlay(record.original, "original GroovyScript config")
        applied_text = _decode_overlay(record.applied, "applied GroovyScript config")
        current_text = _decode_overlay(current, "current GroovyScript config")
        key = "languageServerPort"
        original_value = _forge_int_value(original_text, key)
        applied_value = _forge_int_value(applied_text, key)
        current_value = _forge_int_value(current_text, key)
        if current_value not in {original_value, applied_value}:
            return None
        return _replace_forge_int_text(current_text, key, original_value).encode("utf-8")
    raise PackProgramError(f"unsupported managed overlay role: {record.role}")


def _prism_value_equivalent(left: str, right: str) -> bool:
    def normalize(value: str) -> str:
        selected = value.strip()
        if len(selected) >= 2 and selected[0] == selected[-1] == '"':
            selected = selected[1:-1]
        return selected

    return normalize(left) == normalize(right)


def _launch(binding: PrismLaunchBinding) -> subprocess.Popen[bytes]:
    kwargs: dict[str, Any] = {
        "cwd": binding.cwd,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "posix":
        kwargs["start_new_session"] = True
    elif os.name == "nt":  # pragma: no cover - exercised on Windows hosts
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(list(binding.command), **kwargs)


def _await_readiness(
    *,
    process: subprocess.Popen[bytes],
    binding: PrismLaunchBinding,
    language_profile: LoadedLanguageProfile,
    managed_profile: LoadedManagedSessionProfile,
    workspace_uri: str,
    port: int,
    process_port: int,
    readiness_timeout: float,
    connect_timeout: float,
    diagnostic_timeout: float,
    previous_windows_processes: Mapping[int, Mapping[str, Any]],
    stop_event: threading.Event | None,
    emit: Callable[..., Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    started = time.monotonic()
    deadline = started + readiness_timeout
    attempts = 0
    last_failure: Mapping[str, Any] = {
        "kind": "endpoint-unavailable",
        "message": "language server has not accepted an LSP readiness canary",
    }
    candidate: dict[str, Any] | None = None
    owned: dict[int, dict[str, Any]] = {}
    active: dict[int, dict[str, Any]] = {}
    ever_owned = False
    interval = float(managed_profile.value["lifecycle"]["readiness_poll_interval_seconds"])
    prepared = _readiness_source(workspace_uri)
    bounds = language_profile.value["bounds"]
    limits = LspLimits(
        max_header_bytes=int(bounds["max_header_bytes"]),
        max_message_bytes=int(bounds["max_message_bytes"]),
        max_transcript_messages=int(bounds["max_transcript_messages"]),
        max_diagnostics=int(bounds["max_diagnostics"]),
    )
    while time.monotonic() < deadline:
        if stop_event is not None and stop_event.is_set():
            last_failure = {"kind": "canceled", "message": "session canceled during readiness"}
            break
        if binding.host_os == "windows":
            current = _query_windows_processes(binding.instance_id, port=process_port)
            active = {
                pid: _internal_windows_process(row)
                for pid, row in current.items()
                if pid not in previous_windows_processes
            }
            for pid, row in active.items():
                owned.setdefault(pid, row)
            ever_owned = ever_owned or bool(active)
        else:
            active = {
                process.pid: {
                    "pid": process.pid,
                    "tracker": "owned-process-group",
                    "creation_date": None,
                }
            }
            owned.update(active)
        if candidate is not None and active:
            candidate["latency_ms"] = round((time.monotonic() - started) * 1000)
            emit("readiness-confirmed", attempts=attempts, latency_ms=candidate["latency_ms"])
            time.sleep(min(interval, 0.25))
            return candidate, owned
        if candidate is None:
            attempts += 1
            service = probe_language_server(
                host="127.0.0.1",
                port=port,
                workspace_uri=workspace_uri,
                files=[prepared],
                canary_prefix=str(language_profile.value["canary"]["prefix"]),
                connect_timeout=min(connect_timeout, 1.0),
                diagnostic_timeout=diagnostic_timeout,
                limits=limits,
            )
            if _readiness_is_exact(service):
                checked = service["files"][0]
                candidate = {
                    "state": "confirmed",
                    "port": port,
                    "attempts": attempts,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "canary_state": checked["canary"]["state"],
                    "capabilities": service["capabilities"],
                    "transcript_sha256": service["transcript"]["transcript_sha256"],
                    "failure": None,
                }
            elif service.get("state") == "completed":
                capabilities = service.get("capabilities")
                last_failure = {
                    "kind": "capability-mismatch",
                    "message": (
                        "the endpoint completed the readiness exchange but did not "
                        "advertise the exact GroovyScript compiler capability set"
                    ),
                    "capability_keys": (
                        sorted(capabilities) if isinstance(capabilities, Mapping) else []
                    ),
                }
            elif service.get("failure") is not None:
                last_failure = dict(service["failure"])
        if process.poll() is not None and not active and (
            binding.host_os != "windows" or ever_owned
        ):
            last_failure = {
                "kind": "client-exited",
                "message": f"launcher exited with {process.returncode} before process custody and readiness",
            }
            break
        time.sleep(interval)
    return (
        {
            "state": "blocked",
            "port": port,
            "attempts": max(1, attempts),
            "latency_ms": round((time.monotonic() - started) * 1000),
            "canary_state": None,
            "capabilities": None,
            "transcript_sha256": None,
            "failure": {
                "kind": _safe_text(str(last_failure.get("kind", "readiness-failed")), 256),
                "message": _safe_text(str(last_failure.get("message", "readiness failed")), 4096),
            },
        },
        owned,
    )


def _readiness_is_exact(service: Mapping[str, Any]) -> bool:
    if service.get("state") != "completed" or service.get("failure") is not None:
        return False
    files = service.get("files")
    capabilities = service.get("capabilities")
    if not isinstance(files, list) or len(files) != 1 or not isinstance(capabilities, Mapping):
        return False
    checked = files[0]
    if checked.get("state") != "no-diagnostics" or checked.get("canary", {}).get("state") != "confirmed":
        return False
    required = ("completionProvider", "hoverProvider", "signatureHelpProvider")
    if any(key not in capabilities for key in required):
        return False
    experimental = capabilities.get("experimental")
    return isinstance(experimental, Mapping) and experimental.get("textureDecorationProvider") is True


def _hold_session(
    *,
    process: subprocess.Popen[bytes],
    binding: PrismLaunchBinding,
    owned_windows_processes: dict[int, dict[str, Any]],
    port: int,
    stop_event: threading.Event | None,
    timeout: float,
) -> str:
    deadline = time.monotonic() + timeout
    missing_since: float | None = None
    while time.monotonic() < deadline:
        if stop_event is not None and stop_event.is_set():
            return "requested-stop"
        if binding.host_os == "windows":
            current = _query_windows_processes(binding.instance_id, port=port)
            for pid, row in current.items():
                owned_windows_processes.setdefault(pid, _internal_windows_process(row))
            active = _query_windows_pids(owned_windows_processes)
            if active:
                missing_since = None
            elif missing_since is None:
                missing_since = time.monotonic()
            elif time.monotonic() - missing_since >= 1.0:
                return "client-exited"
        elif process.poll() is not None:
            return "client-exited"
        time.sleep(0.25)
    return "session-timeout"


def _shutdown(
    process: subprocess.Popen[bytes],
    *,
    binding: PrismLaunchBinding,
    known_windows_processes: Mapping[int, Mapping[str, Any]],
    port: int,
    graceful_seconds: float,
    force_seconds: float,
) -> tuple[bool, bool, list[int], dict[int, dict[str, Any]]]:
    graceful = False
    forced = False
    discovered = dict(known_windows_processes)
    if binding.host_os == "windows":
        current = _query_windows_processes(binding.instance_id, port=port)
        if known_windows_processes:
            current.update(_query_windows_pids(known_windows_processes))
        for pid, row in current.items():
            discovered.setdefault(pid, _internal_windows_process(row))
        active = _query_windows_pids(discovered)
        if active:
            graceful = True
            _windows_process_action(discovered, active, force=False)
            if _wait_windows_exit(discovered, graceful_seconds):
                active = {}
        if active:
            forced = True
            active = _query_windows_pids(discovered)
            _windows_process_action(discovered, active, force=True)
            _wait_windows_exit(discovered, force_seconds)
        remaining = sorted(set(discovered).intersection(_query_windows_pids(discovered)))
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=min(5.0, graceful_seconds))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=min(5.0, force_seconds))
                forced = True
        return graceful, forced, remaining, discovered

    if process.poll() is None:
        graceful = True
        _signal_process_group(process, signal.SIGTERM)
        try:
            process.wait(timeout=graceful_seconds)
        except subprocess.TimeoutExpired:
            forced = True
            _signal_process_group(process, signal.SIGKILL)
            try:
                process.wait(timeout=force_seconds)
            except subprocess.TimeoutExpired:
                return graceful, forced, [process.pid], discovered
    return graceful, forced, [], discovered


def _signal_process_group(process: subprocess.Popen[bytes], selected: signal.Signals) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, selected)
        except ProcessLookupError:
            pass
    elif selected == signal.SIGKILL:  # pragma: no cover - Windows host
        process.kill()
    else:  # pragma: no cover - Windows host
        process.terminate()


def _query_windows_processes(
    instance_id: str, *, port: int | None = None
) -> dict[int, dict[str, Any]]:
    output = _powershell(_POWERSHELL_QUERY, instance_id=instance_id, port=port)
    return _parse_windows_processes(output)


def _query_windows_pids(
    expected: Mapping[int, Mapping[str, Any]],
) -> dict[int, dict[str, Any]]:
    if not expected:
        return {}
    output = _powershell(
        _POWERSHELL_QUERY_PIDS,
        instance_id="revalidate-owned-pids",
        pids=sorted(expected),
    )
    current = _parse_windows_processes(output, require_identity=False)
    result: dict[int, dict[str, Any]] = {}
    for pid, row in current.items():
        prior = expected.get(pid)
        if prior is None:
            continue
        if not row.get("creation_date") or not row.get("executable_path"):
            result[pid] = {
                **row,
                "tracker": "windows-pid-identity-unavailable",
            }
            continue
        if prior.get("creation_date") and row.get("creation_date") != prior.get("creation_date"):
            continue
        prior_executable = prior.get("_executable_path")
        if prior_executable and str(row.get("executable_path", "")).casefold() != str(prior_executable).casefold():
            continue
        result[pid] = row
    return result


def _parse_windows_processes(
    output: str, *, require_identity: bool = True
) -> dict[int, dict[str, Any]]:
    try:
        value = json.loads(output or "[]")
    except json.JSONDecodeError as exc:
        raise PackProgramError("Windows process inventory returned malformed JSON") from exc
    if not isinstance(value, list) or len(value) > 64:
        raise PackProgramError("Windows process inventory is malformed or excessive")
    result: dict[int, dict[str, Any]] = {}
    for row in value:
        if not isinstance(row, Mapping):
            raise PackProgramError("Windows process inventory row is malformed")
        pid = row.get("pid")
        creation = row.get("creation_date")
        executable_path = row.get("executable_path")
        tracker = row.get("tracker")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or pid in result:
            raise PackProgramError("Windows process inventory PID is invalid")
        if not isinstance(creation, str) or (require_identity and not creation):
            raise PackProgramError("Windows process creation date is invalid")
        if not isinstance(executable_path, str) or (require_identity and not executable_path):
            raise PackProgramError("Windows process executable path is invalid")
        if not isinstance(tracker, str) or not tracker:
            raise PackProgramError("Windows process tracker is invalid")
        result[pid] = {
            "pid": pid,
            "creation_date": creation,
            "executable_path": executable_path,
            "tracker": tracker,
        }
    return result


def _internal_windows_process(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "pid": int(row["pid"]),
        "tracker": row.get("tracker", "windows-cim-instance-id"),
        "creation_date": row["creation_date"],
        "_executable_path": row["executable_path"],
    }


def _windows_process_action(
    expected: Mapping[int, Mapping[str, Any]],
    active: Mapping[int, Mapping[str, Any]],
    *,
    force: bool,
) -> None:
    if not active:
        return
    selected = {pid: expected[pid] for pid in active if pid in expected}
    _powershell(
        _POWERSHELL_FORCE if force else _POWERSHELL_CLOSE,
        instance_id="act-on-revalidated-owned-pids",
        pids=sorted(selected),
        identities=selected,
    )


def _wait_windows_exit(
    expected: Mapping[int, Mapping[str, Any]], timeout: float
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _query_windows_pids(expected):
            return True
        time.sleep(0.25)
    return not _query_windows_pids(expected)


def _powershell(
    script: str,
    *,
    instance_id: str,
    pids: Sequence[int] = (),
    port: int | None = None,
    identities: Mapping[int, Mapping[str, Any]] | None = None,
) -> str:
    executable = _powershell_executable()
    environment = _windows_helper_environment(
        executable,
        {
            "WORKBENCH_GROOVY_INSTANCE_TOKEN": instance_id,
            "WORKBENCH_GROOVY_PROCESS_IDS": ",".join(str(pid) for pid in pids),
            "WORKBENCH_GROOVY_ENDPOINT_PORT": str(0 if port is None else port),
            "WORKBENCH_GROOVY_PROCESS_IDENTITIES": json.dumps(
                [
                    {
                        "pid": pid,
                        "creation_date": row.get("creation_date"),
                        "executable_path": row.get("_executable_path"),
                    }
                    for pid, row in sorted((identities or {}).items())
                ],
                separators=(",", ":"),
            ),
        },
    )
    completed = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=15,
        env=environment,
    )
    if completed.returncode:
        message = _safe_text(completed.stderr.decode("utf-8", "replace"), 2048)
        raise PackProgramError(f"Windows process custody failed: {message or completed.returncode}")
    if len(completed.stdout) > 1024 * 1024:
        raise PackProgramError("Windows process custody output exceeded its bound")
    return completed.stdout.decode("utf-8", "strict").strip()


def _powershell_executable() -> str:
    executable = (
        shutil.which("powershell.exe")
        or shutil.which("powershell")
        or shutil.which("pwsh")
    )
    if executable is None:
        raise PackProgramError("PowerShell is required for exact Windows Prism process custody")
    return executable


def _windows_helper_environment(
    executable: str, values: Mapping[str, str]
) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(values)
    if os.environ.get("WSL_INTEROP") and Path(executable).name.casefold().endswith(".exe"):
        existing = [item for item in environment.get("WSLENV", "").split(":") if item]
        known = {item.split("/", 1)[0] for item in existing}
        environment["WSLENV"] = ":".join(
            [*existing, *(item for item in values if item not in known)]
        )
    return environment


def _spawn_windows_stdio_proxy(port: int) -> subprocess.Popen[bytes]:
    executable = _powershell_executable()
    environment = _windows_helper_environment(
        executable,
        {"WORKBENCH_GROOVY_UPSTREAM_PORT": str(port)},
    )
    return subprocess.Popen(
        [executable, "-NoProfile", "-NonInteractive", "-Command", _POWERSHELL_STDIO_PROXY],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
        close_fds=True,
        env=environment,
    )


def _build_descriptor(
    *,
    session_id: str,
    emitted_at: str,
    profile: Mapping[str, Any],
    program: Mapping[str, Any],
    runtime: Mapping[str, Any],
    endpoint: Mapping[str, Any],
    readiness: Mapping[str, Any],
    descriptor_path: Path,
    receipt_path: Path,
    events_path: Path,
    limitations: Sequence[str],
) -> dict[str, Any]:
    descriptor: dict[str, Any] = {
        "format": DESCRIPTOR_FORMAT,
        "schema_version": 1,
        "descriptor_id": "",
        "session_id": session_id,
        "state": "ready",
        "emitted_at": emitted_at,
        "profile": dict(profile),
        "program": dict(program),
        "runtime": dict(runtime),
        "endpoint": dict(endpoint),
        "readiness": dict(readiness),
        "clients": {
            "consumers": ["terminal", "intellij", "vscode"],
            "connection_ownership": "client-connects-directly",
            "reconnect_policy": "retry-bounded-after-readiness-handoff",
            "shutdown_owner": "workbench-session-process",
        },
        "artifacts": {
            "descriptor_path": str(descriptor_path),
            "pending_receipt_path": str(receipt_path),
            "events_path": str(events_path),
        },
        "limitations": list(
            dict.fromkeys(
                [
                    *limitations,
                    "The descriptor exposes one local LSP route; only one IDE or terminal client may be active at a time.",
                ]
            )
        ),
    }
    descriptor["descriptor_id"] = descriptor_identity(descriptor)
    return validate_session_descriptor(descriptor)


def _readiness_source(workspace_uri: str) -> dict[str, Any]:
    text = "def __workbench_managed_readiness__ = 1\n"
    raw = text.encode("utf-8")
    return {
        "path": ".workbench-managed-readiness.groovy",
        "server_uri": workspace_uri.rstrip("/") + "/.workbench-managed-readiness.groovy",
        "sha256": _sha(raw),
        "size": len(raw),
        "stage": "managed-readiness",
        "execution_state": "enabled",
        "text": text,
    }


def _patch_instance_config(raw: bytes, *, jvm_key: str, override_key: str, start_argument: str) -> bytes:
    text = _decode_overlay(raw, "Prism instance config")
    current = _single_ini_value(text, jvm_key)
    property_name = start_argument.split("=", 1)[0]
    pattern = re.compile(r"(?<!\S)" + re.escape(property_name) + r"=(?:true|false)(?!\S)")
    matches = list(pattern.finditer(current))
    if len(matches) > 1 or property_name in current and not matches:
        raise PackProgramError("Prism JVM arguments contain an ambiguous GroovyScript start property")
    updated = pattern.sub(start_argument, current) if matches else (current.rstrip() + " " + start_argument).strip()
    text = _replace_single_ini_value(text, jvm_key, updated)
    text = _replace_single_ini_value(text, override_key, "true")
    return text.encode("utf-8")


def _patch_forge_int(raw: bytes, *, key: str, value: int) -> bytes:
    text = _decode_overlay(raw, "GroovyScript config")
    return _replace_forge_int_text(text, key, value).encode("utf-8")


def _forge_int_value(text: str, key: str) -> int:
    pattern = re.compile(
        rf"^(?P<indent>[ \t]*)I:{re.escape(key)}=(?P<value>-?[0-9]+)(?P<ending>\r?\n|$)",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise PackProgramError(f"GroovyScript config must contain exactly one I:{key} property")
    return int(matches[0].group("value"))


def _replace_forge_int_text(text: str, key: str, value: int) -> str:
    pattern = re.compile(
        rf"^(?P<indent>[ \t]*)I:{re.escape(key)}=(?P<value>-?[0-9]+)(?P<ending>\r?\n|$)",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise PackProgramError(f"GroovyScript config must contain exactly one I:{key} property")
    match = matches[0]
    replacement = f"{match.group('indent')}I:{key}={value}{match.group('ending')}"
    return text[: match.start()] + replacement + text[match.end() :]


def _single_ini_value(text: str, key: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}=(?P<value>[^\r\n]*)(?:\r?\n|$)", re.MULTILINE)
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise PackProgramError(f"Prism instance config must contain exactly one {key} property")
    return matches[0].group("value")


def _replace_single_ini_value(text: str, key: str, value: str) -> str:
    if "\r" in value or "\n" in value or "\x00" in value:
        raise PackProgramError(f"unsafe Prism {key} value")
    pattern = re.compile(
        rf"^{re.escape(key)}=[^\r\n]*(?P<ending>\r?\n|$)", re.MULTILINE
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise PackProgramError(f"Prism instance config must contain exactly one {key} property")
    match = matches[0]
    replacement = f"{key}={value}{match.group('ending')}"
    return text[: match.start()] + replacement + text[match.end() :]


def _decode_overlay(raw: bytes, context: str) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise PackProgramError(f"{context} is not UTF-8") from exc
    if "\x00" in text:
        raise PackProgramError(f"{context} contains NUL bytes")
    return text


def _replace_if_hash(path: Path, expected: bytes, replacement: bytes) -> None:
    current = safe_regular_bytes(path, maximum=_MAX_OVERLAY_BYTES)
    if current != expected:
        raise PackProgramError(f"overlay precondition changed before apply: {path}")
    _atomic_replace(path, replacement)


def _atomic_replace(path: Path, payload: bytes) -> None:
    metadata = path.stat(follow_symlinks=False)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".partial", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, stat.S_IMODE(metadata.st_mode))
        total = 0
        while total < len(payload):
            total += os.write(descriptor, payload[total:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _create_lock(path: Path, payload: bytes) -> None:
    try:
        _write_fresh_bytes(path, payload, mode=0o600)
    except FileExistsError as exc:
        raise PackProgramError(f"managed language-session lock already exists: {path}") from exc


def _remove_exact_lock(path: Path, payload: bytes) -> None:
    current = safe_regular_bytes(path, maximum=4096)
    if current != payload:
        raise PackProgramError(f"managed language-session lock changed externally: {path}")
    path.unlink()
    _fsync_directory(path.parent)


def _write_fresh_bytes(path: Path, payload: bytes, *, mode: int) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0),
        mode,
    )
    try:
        total = 0
        while total < len(payload):
            total += os.write(descriptor, payload[total:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _write_fresh_json(path: Path, value: Mapping[str, Any]) -> None:
    _write_fresh_bytes(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        mode=0o600,
    )


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EINVAL, errno.ENOTSUP}:
            return
        raise
    try:
        os.fsync(descriptor)
    except OSError as exc:
        if exc.errno not in {errno.EACCES, errno.EINVAL, errno.ENOTSUP}:
            raise
    finally:
        os.close(descriptor)


def _create_session_directory(storage: Path, session_id: str) -> Path:
    requested = storage.expanduser()
    if requested.exists() and (requested.is_symlink() or not requested.is_dir()):
        raise PackProgramError(f"managed session storage is unsafe: {requested}")
    requested.mkdir(parents=True, exist_ok=True)
    resolved = requested.resolve()
    if resolved.is_symlink() or not resolved.is_dir():
        raise PackProgramError(f"managed session storage is unsafe: {resolved}")
    leaf = session_id.rsplit(":", 1)[-1]
    destination = resolved / leaf
    destination.mkdir(mode=0o700)
    return destination


def _safe_projection_file(root: Path, relative: PurePosixPath, context: str) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PackProgramError(f"{context} traverses a symlink: {current}")
    resolved = current.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise PackProgramError(f"{context} escapes its projection") from exc
    if not resolved.is_file():
        raise PackProgramError(f"{context} is not a regular file: {resolved}")
    return resolved


def _reserve_port(requested: int | None) -> tuple[socket.socket, int, str]:
    if requested is not None and (
        isinstance(requested, bool) or not isinstance(requested, int) or not 1 <= requested <= 65535
    ):
        raise PackProgramError("managed language-session port must be between 1 and 65535")
    reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            reservation.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        reservation.bind(("127.0.0.1", 0 if requested is None else requested))
        port = int(reservation.getsockname()[1])
        return reservation, port, (
            "reserved-random-loopback" if requested is None else "explicit-free-loopback"
        )
    except OSError:
        reservation.close()
        raise


def _reserve_managed_endpoint(
    requested: int | None, *, bridge_required: bool
) -> tuple[socket.socket, int, int, str]:
    reservation, endpoint_port, allocation = _reserve_port(requested)
    if not bridge_required:
        return reservation, endpoint_port, endpoint_port, allocation
    try:
        for _attempt in range(32):
            upstream_port = _windows_ephemeral_port()
            if upstream_port == endpoint_port:
                continue
            if _windows_port_available(upstream_port):
                return reservation, endpoint_port, upstream_port, allocation
    except (OSError, PackProgramError, subprocess.SubprocessError):
        reservation.close()
        raise
    reservation.close()
    raise PackProgramError(
        "unable to select a distinct free Windows upstream language-server port"
    )


def _windows_port_available(port: int) -> bool:
    output = _powershell(
        _POWERSHELL_PORT_AVAILABLE,
        instance_id="check-managed-endpoint-port",
        port=port,
    )
    try:
        available = json.loads(output)
    except json.JSONDecodeError as exc:
        raise PackProgramError("Windows endpoint-port check returned malformed JSON") from exc
    if not isinstance(available, bool):
        raise PackProgramError("Windows endpoint-port check returned a non-boolean result")
    return available


def _windows_ephemeral_port() -> int:
    output = _powershell(
        _POWERSHELL_EPHEMERAL_PORT,
        instance_id="allocate-managed-upstream-port",
    )
    try:
        port = json.loads(output)
    except json.JSONDecodeError as exc:
        raise PackProgramError("Windows upstream-port allocation returned malformed JSON") from exc
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise PackProgramError("Windows upstream-port allocation returned an invalid port")
    return port


def _requires_windows_stdio_bridge(binding: PrismLaunchBinding) -> bool:
    return (
        binding.host_os == "windows"
        and os.name == "posix"
        and bool(os.environ.get("WSL_INTEROP"))
    )


def _server_workspace_uri(path: Path, *, bridge_required: bool) -> str:
    resolved = path.resolve()
    if not bridge_required:
        return resolved.as_uri()
    executable = shutil.which("wslpath")
    if executable is None:
        raise PackProgramError(
            "wslpath is required to bind the Windows GroovyScript workspace URI"
        )
    completed = subprocess.run(
        [executable, "-w", str(resolved)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=5,
    )
    if completed.returncode:
        message = _safe_text(completed.stderr.decode("utf-8", "replace"), 2048)
        raise PackProgramError(
            f"unable to translate managed workspace into Windows: {message or completed.returncode}"
        )
    if len(completed.stdout) > 16384:
        raise PackProgramError("translated Windows workspace path exceeded its bound")
    try:
        windows_path = completed.stdout.decode("utf-8", "strict").strip("\r\n")
    except UnicodeError as exc:
        raise PackProgramError("translated Windows workspace path is not UTF-8") from exc
    if not windows_path or any(ord(character) < 32 for character in windows_path):
        raise PackProgramError("translated Windows workspace path is malformed")
    return _windows_path_file_uri(windows_path)


def _windows_path_file_uri(windows_path: str) -> str:
    drive = re.fullmatch(r"(?P<drive>[A-Za-z]):\\(?P<tail>.*)", windows_path)
    if drive is not None:
        tail = drive.group("tail").replace("\\", "/")
        normalized = f"{drive.group('drive').upper()}:/{tail}"
        return "file:///" + quote(normalized, safe="/:")
    if windows_path.startswith("\\\\"):
        parts = windows_path[2:].split("\\")
        if len(parts) >= 2 and re.fullmatch(r"[A-Za-z0-9._-]+", parts[0]):
            suffix = quote("/".join(parts[1:]), safe="/:")
            return f"file://{parts[0]}/{suffix}"
    raise PackProgramError("translated Windows workspace path has an unsupported form")


def _file_uri_path(value: Any, context: str) -> Path:
    uri = _bounded_text(value, context, 16384)
    parsed = urlsplit(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"} or parsed.query or parsed.fragment:
        raise PackProgramError(f"{context} must be a local file URI")
    decoded = url2pathname(parsed.path)
    if not decoded or "\x00" in decoded:
        raise PackProgramError(f"{context} is malformed")
    return Path(decoded).resolve()


def _flag_value(command: Sequence[str], flag: str) -> str:
    positions = [index for index, item in enumerate(command) if item == flag]
    if len(positions) != 1 or positions[0] + 1 >= len(command):
        raise PackProgramError(f"Prism command must contain exactly one {flag}")
    return command[positions[0] + 1]


def _file_identity(path: Path, *, maximum: int) -> dict[str, Any]:
    raw = safe_regular_bytes(path, maximum=maximum)
    return {"sha256": _sha(raw), "size": len(raw)}


def _emitter(
    journal: EventJournal,
    callback: Callable[[Mapping[str, Any]], None] | None,
) -> Callable[..., Mapping[str, Any]]:
    def emit(state: str, **details: Any) -> Mapping[str, Any]:
        event = journal.emit(state, **details)
        if callback is not None:
            callback(event)
        return event

    return emit


def _timeout(value: Any, context: str, *, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PackProgramError(f"{context} must be numeric")
    parsed = float(value)
    if not 0.05 <= parsed <= maximum:
        raise PackProgramError(f"{context} must be between 0.05 and {maximum} seconds")
    return parsed


def _bounded_text(value: Any, context: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise PackProgramError(f"{context} must be bounded non-empty text")
    if any(ord(character) < 32 for character in value):
        raise PackProgramError(f"{context} contains control characters")
    return value


def _safe_text(value: str, maximum: int) -> str:
    return "".join(
        character if character in "\t\n" or 32 <= ord(character) != 127 else "?"
        for character in value
    )[:maximum]


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "PrismLaunchBinding",
    "load_prism_launch_binding",
    "run_managed_language_session",
]
