"""Core-owned authenticated host and transports for service protocol V3.

Core owns authentication, negotiation, framing, endpoint lifecycle and bounded
dispatch. Modules supply handlers and durable-store implementations through
explicit API contracts. Owner-supplied
binding ports provide semantic response state; this module never infers
profile support, evidence quality, completeness, or action authorization.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import subprocess
import sys
import threading
import time
from types import MappingProxyType
from typing import Any, BinaryIO, TextIO

from workbench_core.service.runtime import LocalServiceAuthenticator, ServiceRuntimeV3
from workbench_api.service import ServiceHandlerRegistration, ServicePhysicalLeasePorts, ServiceV3Error
from workbench_api.host_filesystem import (
    fsync_directory,
    private_path,
    secure_private_endpoint,
    secure_private_path,
)
from workbench_api.canonical import canonical_json_bytes, content_id, parse_canonical_json

from workbench_core.host_adapter import validate_host_adapter_v3_receipt
from workbench_core.service.framing import FramingError, JsonPayloadError, read_message, write_message


PROTOCOL_VERSION = MappingProxyType({"major": 3, "minor": 0})
MAXIMUM_FRAME_BYTES = 4 * 1024 * 1024
MAXIMUM_OBJECT_RANGE_BYTES = 1024 * 1024
MAXIMUM_SUBSCRIPTIONS = 8
SUBSCRIPTION_RETENTION_EVENTS = 4096
WINDOWS_ENDPOINT_FORMAT = "workbench-windows-loopback-endpoint-v1"
WINDOWS_ENDPOINT_MAXIMUM_BYTES = 4096

_CONTENT_ID = re.compile(r"[a-z][a-z0-9-]*:sha256:[0-9a-f]{64}\Z")
_REQUEST_ID = re.compile(r"request-v3:[0-9a-f]{32}\Z")
_CLIENT_SESSION_ID = re.compile(r"client-session-v3:[0-9a-f]{32}\Z")
_TRANSPORTS = {"embedded", "local-endpoint", "stdio"}
_STATE_KEYS = {
    "action_gate",
    "action_gate_decision_ids",
    "completeness",
    "consent",
    "context",
    "continuity",
    "evidence",
    "freshness",
    "implementation",
    "inputs",
    "profile_revision_refs",
    "profile_support",
    "support_decision_ids",
}


class ServiceHostV3Error(ValueError):
    """Stable host or transport rejection."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise ServiceHostV3Error(code, message)


def _windows_endpoint_value(path: Path) -> tuple[str, int]:
    _require(
        private_path(path, directory=False),
        "service.invalid-endpoint",
        "Windows endpoint record is absent or accessible outside its owner",
    )
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ServiceHostV3Error(
            "service.invalid-endpoint", "Windows endpoint record is invalid"
        ) from exc
    _require(
        1 <= len(raw) <= WINDOWS_ENDPOINT_MAXIMUM_BYTES
        and type(value) is dict
        and set(value)
        == {"endpoint_id", "format", "host", "port", "schema_version"}
        and value["format"] == WINDOWS_ENDPOINT_FORMAT
        and value["schema_version"] == 1
        and value["host"] == "127.0.0.1"
        and type(value["port"]) is int
        and 1 <= value["port"] <= 65535
        and raw == canonical_json_bytes(value) + b"\n",
        "service.invalid-endpoint",
        "Windows endpoint record fields are invalid",
    )
    body = dict(value)
    endpoint_id = body.pop("endpoint_id")
    _require(
        endpoint_id == content_id("windows-loopback-endpoint", body),
        "service.invalid-endpoint",
        "Windows endpoint record identity changed",
    )
    return value["host"], value["port"]


def _publish_windows_endpoint(path: Path, listener: socket.socket) -> None:
    host, port = listener.getsockname()
    body = {
        "format": WINDOWS_ENDPOINT_FORMAT,
        "host": host,
        "port": port,
        "schema_version": 1,
    }
    value = {
        "endpoint_id": content_id("windows-loopback-endpoint", body),
        **body,
    }
    raw = canonical_json_bytes(value) + b"\n"
    _require(
        len(raw) <= WINDOWS_ENDPOINT_MAXIMUM_BYTES,
        "service.invalid-endpoint",
        "Windows endpoint record exceeded its byte budget",
    )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        secure_private_path(path, directory=False)
        fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not private_path(path, directory=False):
            path.unlink(missing_ok=True)


def _snapshot(value: Any) -> Any:
    return parse_canonical_json(canonical_json_bytes(value))


def _operational(prefix: str) -> str:
    return f"{prefix}:{os.urandom(16).hex()}"


@dataclass(frozen=True, slots=True)
class OwnerBindingProjection:
    """Owner-supplied response facts that Shell is forbidden to infer."""

    input_revision_refs: tuple[Mapping[str, Any], ...]
    output_revision_refs: tuple[Mapping[str, Any], ...]
    states: Mapping[str, Any]
    diagnostics: tuple[Mapping[str, Any], ...] = ()
    limitations: tuple[Mapping[str, Any], ...] = ()
    object_references: tuple[Mapping[str, Any], ...] = ()
    continuation_id: str | None = None

    def __post_init__(self) -> None:
        _require(
            type(self.input_revision_refs) is tuple
            and type(self.output_revision_refs) is tuple
            and type(self.diagnostics) is tuple
            and type(self.limitations) is tuple
            and type(self.object_references) is tuple,
            "service.invalid-owner-projection",
            "owner projection collections must be exact tuples",
        )
        _require(
            type(self.states) is dict and set(self.states) == _STATE_KEYS,
            "service.invalid-owner-projection",
            "owner projection state fields are not closed",
        )
        _snapshot(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_revision_refs": [dict(row) for row in self.input_revision_refs],
            "output_revision_refs": [dict(row) for row in self.output_revision_refs],
            "states": dict(self.states),
            "diagnostics": [dict(row) for row in self.diagnostics],
            "limitations": [dict(row) for row in self.limitations],
            "object_references": [dict(row) for row in self.object_references],
            "continuation_id": self.continuation_id,
        }


OwnerProjectionPort = Callable[[Mapping[str, Any]], OwnerBindingProjection]


@dataclass(frozen=True, slots=True)
class HostedMethodBinding:
    registration: ServiceHandlerRegistration
    result_schema_id: str
    owner_projection_port: OwnerProjectionPort

    def __post_init__(self) -> None:
        _require(
            type(self.registration) is ServiceHandlerRegistration,
            "service.invalid-host-binding",
            "host binding registration is invalid",
        )
        _require(
            type(self.result_schema_id) is str
            and self.result_schema_id.startswith("workbench://schemas/"),
            "service.invalid-host-binding",
            "host binding result schema is invalid",
        )
        _require(
            callable(self.owner_projection_port),
            "service.invalid-host-binding",
            "owner projection port is required",
        )


@dataclass(frozen=True, slots=True)
class ServiceTransportSelection:
    mode: str
    host_adapter_receipt_id: str
    provider_receipt_id: str | None
    missing_capability_ids: tuple[str, ...]
    limitation: str | None
    next_safe_action: str


@dataclass(frozen=True, slots=True)
class PosixServiceProviderReceipt:
    receipt_id: str
    provider_id: str
    host_adapter_receipt_id: str
    transport_kind: str
    supplemented_capability_ids: tuple[str, ...]
    evidence: tuple[str, ...]
    release_qualified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence": list(self.evidence),
            "host_adapter_receipt_id": self.host_adapter_receipt_id,
            "provider_id": self.provider_id,
            "receipt_id": self.receipt_id,
            "release_qualified": self.release_qualified,
            "supplemented_capability_ids": list(
                self.supplemented_capability_ids
            ),
            "transport_kind": self.transport_kind,
        }


def _provider_receipt_id(value: Mapping[str, Any]) -> str:
    body = dict(value)
    body.pop("receipt_id", None)
    return content_id("host-service-provider-receipt", body)


def posix_service_physical_lease_ports() -> ServicePhysicalLeasePorts:
    """Provide explicitly selected POSIX leases to the host-neutral runtime."""

    try:
        import fcntl
    except ImportError as exc:  # pragma: no cover - exercised on non-POSIX hosts
        raise ServiceHostV3Error(
            "service.lock-provider-unavailable",
            "the selected POSIX physical lease provider is unavailable",
        ) from exc

    def acquire_instance(path: Path) -> Callable[[], None]:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(descriptor)
            raise ServiceV3Error(
                "writer-busy",
                "another service instance owns the exact durable store",
                retryable=True,
            ) from exc
        released = False
        guard = threading.Lock()

        def release() -> None:
            nonlocal released
            with guard:
                if released:
                    return
                released = True
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

        return release

    @contextmanager
    def exclusive(path: Path):
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    return ServicePhysicalLeasePorts(
        "workbench.posix-file-lease-provider:v1",
        acquire_instance,
        exclusive,
    )


def windows_service_physical_lease_ports() -> ServicePhysicalLeasePorts:
    """Provide Windows byte-range leases without changing runtime custody."""

    try:
        import msvcrt
    except ImportError as exc:  # pragma: no cover - exercised on Windows hosts
        raise ServiceHostV3Error(
            "service.lock-provider-unavailable",
            "the selected Windows physical lease provider is unavailable",
        ) from exc

    def open_lock(path: Path) -> int:
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0),
            0o600,
        )
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    def lock(descriptor: int, mode: int) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, mode, 1)

    def acquire_instance(path: Path) -> Callable[[], None]:
        descriptor = open_lock(path)
        try:
            lock(descriptor, msvcrt.LK_NBLCK)
        except OSError as exc:
            os.close(descriptor)
            raise ServiceV3Error(
                "writer-busy",
                "another service instance owns the exact durable store",
                retryable=True,
            ) from exc
        released = False
        guard = threading.Lock()

        def release() -> None:
            nonlocal released
            with guard:
                if released:
                    return
                released = True
                try:
                    lock(descriptor, msvcrt.LK_UNLCK)
                finally:
                    os.close(descriptor)

        return release

    @contextmanager
    def exclusive(path: Path):
        descriptor = open_lock(path)
        try:
            lock(descriptor, msvcrt.LK_LOCK)
            yield
        finally:
            try:
                lock(descriptor, msvcrt.LK_UNLCK)
            finally:
                os.close(descriptor)

    return ServicePhysicalLeasePorts(
        "workbench.windows-byte-range-lease-provider:v1",
        acquire_instance,
        exclusive,
    )


def local_service_physical_lease_ports() -> ServicePhysicalLeasePorts:
    """Select the implemented lease provider from measured host capability."""

    if os.name == "nt":
        return windows_service_physical_lease_ports()
    return posix_service_physical_lease_ports()


def qualify_posix_service_provider(
    host_adapter_receipt: Mapping[str, Any], *, scratch_root: Path
) -> PosixServiceProviderReceipt:
    """Exercise an AF_UNIX endpoint and descendant process-group termination."""

    adapter = validate_host_adapter_v3_receipt(host_adapter_receipt)
    _require(
        isinstance(scratch_root, Path)
        and scratch_root.is_absolute()
        and scratch_root.is_dir()
        and not scratch_root.is_symlink(),
        "service.invalid-transport-root",
        "service transport scratch root must be an existing absolute directory",
    )
    _require(
        hasattr(socket, "AF_UNIX")
        and hasattr(os, "killpg")
        and hasattr(os, "getpgid"),
        "service.transport-unavailable",
        "this provider cannot supply local endpoint and process-tree custody",
    )
    endpoint = scratch_root / f"probe-{os.urandom(8).hex()}.sock"
    listener: socket.socket | None = None
    client: socket.socket | None = None
    accepted: socket.socket | None = None
    parent: subprocess.Popen[bytes] | None = None
    child_pid: int | None = None
    lock_descriptors: tuple[int, int] | None = None
    lock_path = scratch_root / f"probe-{os.urandom(8).hex()}.lock"
    try:
        try:
            import fcntl
        except ImportError as exc:
            raise ServiceHostV3Error(
                "service.transport-unavailable",
                "POSIX physical locking is unavailable",
            ) from exc
        first_lock = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        second_lock = os.open(lock_path, os.O_RDWR)
        lock_descriptors = (first_lock, second_lock)
        fcntl.flock(first_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            fcntl.flock(second_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise ServiceHostV3Error(
                "service.transport-unavailable",
                "POSIX lease probe admitted two exclusive owners",
            )

        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(endpoint))
        os.chmod(endpoint, 0o600)
        listener.listen(1)
        listener.settimeout(2)
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(2)
        client.connect(str(endpoint))
        accepted, _ = listener.accept()
        accepted.settimeout(2)
        client.sendall(b"workbench-service-provider-v1")
        _require(
            accepted.recv(64) == b"workbench-service-provider-v1"
            and endpoint.stat().st_mode & 0o077 == 0,
            "service.transport-unavailable",
            "owner-private local endpoint probe failed",
        )

        script = (
            "import subprocess,sys,time;"
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
            "print(p.pid,flush=True);time.sleep(60)"
        )
        parent = subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _require(
            parent.stdout is not None,
            "service.transport-unavailable",
            "process-tree probe did not expose its bounded identity channel",
        )
        selector = selectors.DefaultSelector()
        try:
            selector.register(parent.stdout, selectors.EVENT_READ)
            _require(
                bool(selector.select(3)),
                "service.transport-unavailable",
                "process-tree probe did not report its descendant",
            )
            raw_pid = parent.stdout.readline(64)
        finally:
            selector.close()
        _require(
            raw_pid.strip().isdigit(),
            "service.transport-unavailable",
            "process-tree probe returned an invalid descendant identity",
        )
        child_pid = int(raw_pid)
        group_id = os.getpgid(parent.pid)
        os.killpg(group_id, signal.SIGTERM)
        parent.wait(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            raise ServiceHostV3Error(
                "service.transport-unavailable",
                "process-tree provider did not terminate its descendant",
            )

        body: dict[str, Any] = {
            "evidence": [
                "owner-private AF_UNIX stream exchanged exact bounded bytes",
                "one isolated process group terminated its reported descendant",
                "one POSIX file lease rejected a second exclusive owner",
            ],
            "host_adapter_receipt_id": adapter["receipt_id"],
            "provider_id": "workbench.posix-local-service-provider:v1",
            "receipt_id": "pending",
            "release_qualified": False,
            "supplemented_capability_ids": [
                "local-ipc",
                "physical-exclusive-lock",
                "process-tree-termination",
            ],
            "transport_kind": "unix-domain-socket",
        }
        body["receipt_id"] = _provider_receipt_id(body)
        return PosixServiceProviderReceipt(
            body["receipt_id"],
            body["provider_id"],
            body["host_adapter_receipt_id"],
            body["transport_kind"],
            tuple(body["supplemented_capability_ids"]),
            tuple(body["evidence"]),
            False,
        )
    except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
        raise ServiceHostV3Error(
            "service.transport-unavailable",
            f"local service provider qualification failed with {type(exc).__name__}",
        ) from exc
    finally:
        if parent is not None and parent.poll() is None:
            try:
                os.killpg(parent.pid, signal.SIGKILL)
            except OSError:
                parent.kill()
            try:
                parent.wait(timeout=5)
            except Exception:
                pass
        if parent is not None and parent.stdout is not None:
            parent.stdout.close()
        for stream in (accepted, client, listener):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        if lock_descriptors is not None:
            for descriptor in reversed(lock_descriptors):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        endpoint.unlink(missing_ok=True)
        lock_path.unlink(missing_ok=True)


def select_service_transport(
    host_adapter_receipt: Mapping[str, Any],
    *,
    provider_receipt: PosixServiceProviderReceipt | None = None,
) -> ServiceTransportSelection:
    """Choose hosted or compatibility transport from adapter evidence only."""

    adapter = validate_host_adapter_v3_receipt(host_adapter_receipt)
    operation = next(
        row
        for row in adapter["operation_states"]
        if row["id"] == "hosted-local-service"
    )
    missing = tuple(operation["missing_capability_ids"])
    provider_ok = False
    if provider_receipt is not None:
        provider_value = provider_receipt.to_dict()
        provider_ok = (
            provider_receipt.host_adapter_receipt_id == adapter["receipt_id"]
            and provider_receipt.release_qualified is False
            and provider_receipt.receipt_id == _provider_receipt_id(provider_value)
            and set(missing).issubset(provider_receipt.supplemented_capability_ids)
        )
    if operation["state"] == "available" or provider_ok:
        return ServiceTransportSelection(
            "local-endpoint",
            adapter["receipt_id"],
            None if provider_receipt is None else provider_receipt.receipt_id,
            (),
            None,
            "Start or attach to the one authenticated local service for this store.",
        )
    return ServiceTransportSelection(
        "stdio",
        adapter["receipt_id"],
        None,
        missing,
        "Hosted service prerequisites are unavailable on this adapter evidence.",
        "Use the bounded stdio compatibility transport or qualify a local service provider.",
    )


class ServiceHostV3:
    """Transport-independent authenticated projection over one Crucible runtime."""

    def __init__(
        self,
        runtime: ServiceRuntimeV3,
        *,
        authenticator: LocalServiceAuthenticator,
        registry: Mapping[str, Any],
        registry_validation_port: Callable[[Mapping[str, Any]], bool],
        method_bindings: tuple[HostedMethodBinding, ...],
    ) -> None:
        _require(
            type(runtime) is ServiceRuntimeV3
            and type(authenticator) is LocalServiceAuthenticator,
            "service.invalid-host-composition",
            "host requires exact runtime and authenticator instances",
        )
        registry_snapshot = _snapshot(registry)
        _require(
            type(registry_snapshot) is dict
            and registry_snapshot.get("format")
            == "workbench-component-capability-registry-v3"
            and type(registry_snapshot.get("registry_id")) is str
            and registry_snapshot["registry_id"].startswith(
                "component-capability-registry:sha256:"
            )
            and type(registry_snapshot.get("service_distribution_id")) is str,
            "service.invalid-registry",
            "host registry identity is invalid",
        )
        _require(
            callable(registry_validation_port)
            and registry_validation_port(_snapshot(registry_snapshot)) is True,
            "service.invalid-registry",
            "component capability registry was not accepted by its validation port",
        )
        index: dict[tuple[str, str], HostedMethodBinding] = {}
        operations: dict[tuple[str, str], str] = {}
        binding_modes: dict[tuple[str, str], tuple[str, str]] = {}
        budget_limits: dict[tuple[str, str], dict[tuple[str, str], int]] = {}
        for binding in method_bindings:
            _require(
                type(binding) is HostedMethodBinding,
                "service.invalid-host-binding",
                "host method bindings must be an exact tuple of bindings",
            )
            key = (binding.registration.capability_id, binding.registration.method)
            _require(
                key not in index and runtime.registrations.get(key) == binding.registration,
                "service.invalid-host-binding",
                "host binding differs from the Crucible runtime registration",
            )
            descriptor = self._validate_registry_binding(
                registry_snapshot, binding
            )
            index[key] = binding
            operations[key] = descriptor["operation"]["operation_class"]
            binding_modes[key] = (
                descriptor["context_applicability"]["context_binding"],
                descriptor["context_applicability"]["input_binding"],
            )
            budget_limits[key] = {
                (row["budget_key"], row["unit"]): row["limit"]
                for row in descriptor["resources"]["default_budgets"]
            }
        _require(
            set(index) == set(runtime.registrations),
            "service.invalid-host-binding",
            "every runtime registration must have exactly one hosted binding",
        )
        self.runtime = runtime
        self.authenticator = authenticator
        self.registry = MappingProxyType(registry_snapshot)
        self.method_bindings = MappingProxyType(index)
        self._operation_classes = MappingProxyType(operations)
        self._binding_modes = MappingProxyType(binding_modes)
        self._budget_limits = MappingProxyType(
            {key: MappingProxyType(value) for key, value in budget_limits.items()}
        )
        methods = {key[1] for key in index}
        available_features = {"contexts"}
        if any(binding.registration.asynchronous for binding in index.values()):
            available_features.add("durable-jobs")
        if "job/cancel" in methods and "durable-jobs" in available_features:
            available_features.add("progress-and-cancellation")
        if any(method.endswith("/subscribe") for method in methods):
            available_features.add("subscriptions")
        if "object/read" in methods:
            available_features.add("object-range-reads")
        if any(
            descriptor["async_behavior"]["continuation"] != "none"
            for descriptor in registry_snapshot["capability_descriptors"]
            if descriptor["capability_id"] in {key[0] for key in index}
        ):
            available_features.add("continuations")
        self._available_features = frozenset(available_features)

    @staticmethod
    def _validate_registry_binding(
        registry: Mapping[str, Any], binding: HostedMethodBinding
    ) -> Mapping[str, Any]:
        registration = binding.registration
        descriptors = [
            row
            for row in registry["capability_descriptors"]
            if row["capability_id"] == registration.capability_id
        ]
        registrations = [
            row
            for row in registry["handler_registrations"]
            if row["capability_id"] == registration.capability_id
        ]
        _require(
            len(descriptors) == 1 and len(registrations) == 1,
            "service.invalid-host-binding",
            "runtime capability does not resolve exactly once in the V3 registry",
        )
        descriptor, declared = descriptors[0], registrations[0]
        descriptor_method = next(
            (
                row
                for row in descriptor["method_bindings"]
                if row["protocol_method"] == registration.method
            ),
            None,
        )
        declared_method = next(
            (
                row
                for row in declared["method_bindings"]
                if row["protocol_method"] == registration.method
            ),
            None,
        )
        _require(
            descriptor_method is not None
            and declared_method == descriptor_method
            and (
                descriptor_method["request_value_scope"] == "arguments"
                or (
                    registration.method in {"job/cancel", "job/subscribe"}
                    and descriptor_method["request_value_scope"] == "params"
                )
            )
            and descriptor_method["result_schema_id"] == binding.result_schema_id
            and descriptor["semantic_version"] == registration.capability_version
            and descriptor["handler_id"] == registration.handler_id
            and descriptor["handler_implementation_id"]
            == registration.implementation_id
            and descriptor["context_applicability"]["context_binding"]
            == registration.context_binding
            and descriptor["context_applicability"]["input_binding"]
            == registration.input_binding
            and declared["handler_id"] == registration.handler_id
            and declared["handler_implementation_id"]
            == registration.implementation_id
            and declared["state"] == "available"
            and {"embedded", "local-endpoint", "stdio"}.issubset(
                declared["invocation_modes"]
            ),
            "service.invalid-host-binding",
            "runtime registration drifts from the validated V3 registry",
        )
        _require(
            registration.request_validator is not None
            and registration.result_validator is not None,
            "service.invalid-host-binding",
            "hosted registrations require executable request and result validators",
        )
        return descriptor

    def new_session(self, transport: str) -> "ServiceRpcSessionV3":
        _require(
            transport in _TRANSPORTS,
            "service.unsupported-transport",
            "service transport is unsupported",
        )
        return ServiceRpcSessionV3(self, transport)


class ServiceRpcSessionV3:
    """One authenticated and initialized V3 client session."""

    def __init__(self, host: ServiceHostV3, transport: str) -> None:
        self.host = host
        self.transport = transport
        self.initialized = False
        self.closed = False
        self.client_session_id: str | None = None
        self._request_ids: set[str] = set()

    @staticmethod
    def _error(identifier: Any, code: int, kind: str, message: str, retryable: bool) -> dict[str, Any]:
        return {
            "error": {
                "code": code,
                "data": {
                    "availability": "unavailable",
                    "kind": kind,
                    "retryable": retryable,
                },
                "message": message,
            },
            "id": identifier,
            "jsonrpc": "2.0",
        }

    def handle_message(self, message: Any, *, bearer_token: str) -> dict[str, Any] | None:
        identifier = message.get("id") if type(message) is dict else None
        try:
            self.host.authenticator.authenticate(bearer_token)
            _require(
                not self.closed,
                "service.session-closed",
                "service session is closed",
            )
            _require(
                type(message) is dict
                and set(message) == {"id", "jsonrpc", "method", "params"}
                and message["jsonrpc"] == "2.0"
                and type(message["method"]) is str
                and type(message["params"]) is dict,
                "invalid-request",
                "JSON-RPC request envelope is invalid or open",
            )
            if not self.initialized:
                return self._initialize(message)
            _require(
                message["method"] != "service/initialize",
                "invalid-request",
                "service session may initialize exactly once",
            )
            if message["method"] == "service/shutdown":
                self.closed = True
                return {
                    "id": identifier,
                    "jsonrpc": "2.0",
                    "result": None,
                }
            return self._dispatch(message)
        except ServiceV3Error as exc:
            return self._error(
                identifier,
                -32004,
                exc.code,
                str(exc),
                exc.retryable,
            )
        except ServiceHostV3Error as exc:
            return self._error(
                identifier,
                -32602 if exc.code == "invalid-request" else -32004,
                exc.code,
                str(exc),
                exc.retryable,
            )
        except Exception:
            return self._error(
                identifier,
                -32603,
                "internal-transport-failure",
                "Internal service transport failure.",
                False,
            )

    def _initialize(self, message: Mapping[str, Any]) -> dict[str, Any]:
        _require(
            message["method"] == "service/initialize",
            "invalid-request",
            "first V3 request must initialize the service session",
        )
        params = message["params"]
        _require(
            set(params)
            == {
                "client",
                "maximum_frame_bytes",
                "protocol_version",
                "request_id",
                "required_capability_ids",
                "required_features",
                "transport",
            }
            and params["protocol_version"] == dict(PROTOCOL_VERSION)
            and params["transport"] == self.transport
            and type(params["request_id"]) is str
            and _REQUEST_ID.fullmatch(params["request_id"]) is not None
            and type(params["required_capability_ids"]) is list
            and type(params["required_features"]) is list
            and type(params["maximum_frame_bytes"]) is int
            and 1 <= params["maximum_frame_bytes"] <= MAXIMUM_FRAME_BYTES,
            "invalid-request",
            "service initialization parameters are invalid or open",
        )
        available = {key[0] for key in self.host.method_bindings}
        _require(
            all(
                type(item) is str and item in available
                for item in params["required_capability_ids"]
            ),
            "unavailable-capability",
            "a required capability is not loaded in this service instance",
        )
        _require(
            set(params["required_features"]).issubset(
                self.host._available_features
            ),
            "unavailable-capability",
            "a required V3 transport feature is unavailable",
        )
        self.initialized = True
        self._request_ids.add(params["request_id"])
        self.client_session_id = _operational("client-session-v3")
        _require(
            _CLIENT_SESSION_ID.fullmatch(self.client_session_id) is not None,
            "service.internal-identity",
            "client session identity generation failed",
        )
        registry = self.host.registry
        return {
            "id": message["id"],
            "jsonrpc": "2.0",
            "result": {
                "client_session_id": self.client_session_id,
                "format": "workbench-service-initialize-result-v3",
                "legacy_v2_gateway": {
                    "available": registry["legacy_v2_gateway"]["enabled"],
                    "disposition": registry["legacy_v2_gateway"]["disposition"],
                    "protocol_major": registry["legacy_v2_gateway"]["protocol_major"],
                },
                "limits": {
                    "maximum_frame_bytes": min(
                        params["maximum_frame_bytes"], MAXIMUM_FRAME_BYTES
                    ),
                    "maximum_object_range_bytes": MAXIMUM_OBJECT_RANGE_BYTES,
                    "maximum_subscriptions": MAXIMUM_SUBSCRIPTIONS,
                    "subscription_retention_events": SUBSCRIPTION_RETENTION_EVENTS,
                },
                "negotiated_features": params["required_features"],
                "protocol_version": dict(PROTOCOL_VERSION),
                "registry_id": registry["registry_id"],
                "request_id": params["request_id"],
                "service_distribution_id": registry["service_distribution_id"],
                "service_instance_id": self.host.runtime.service_instance_id,
            },
        }

    def _owner_projection(
        self, binding: HostedMethodBinding, request: Mapping[str, Any]
    ) -> OwnerBindingProjection:
        isolated = _snapshot(request)
        before = canonical_json_bytes(isolated)
        projection = binding.owner_projection_port(isolated)
        _require(
            canonical_json_bytes(isolated) == before
            and type(projection) is OwnerBindingProjection,
            "service.owner-projection-rejected",
            "owner projection port failed or mutated its detached request",
        )
        return projection

    def _dispatch(self, message: Mapping[str, Any]) -> dict[str, Any]:
        params = message["params"]
        if message["method"] == "job/cancel":
            _require(
                set(params)
                == {
                    "expected_event_id",
                    "expected_event_ordinal",
                    "job_id",
                    "reason",
                    "request",
                    "requester_id",
                }
                and type(params["request"]) is dict
                and type(params["job_id"]) is str
                and re.fullmatch(r"job-v2:[0-9a-f]{32}", params["job_id"])
                is not None
                and type(params["expected_event_id"]) is str
                and params["expected_event_id"].startswith(
                    "job-event:sha256:"
                )
                and type(params["expected_event_ordinal"]) is int
                and params["expected_event_ordinal"] >= 0
                and params["requester_id"] == self.host.runtime.actor_id
                and type(params["reason"]) is str
                and 1 <= len(params["reason"].encode("utf-8")) <= 1024,
                "invalid-request",
                "job cancellation parameters are invalid, open, or spoofed",
            )
            request = params["request"]
            arguments = {
                "expected_event_id": params["expected_event_id"],
                "expected_event_ordinal": params["expected_event_ordinal"],
                "idempotency_key": request.get("idempotency_key"),
                "job_id": params["job_id"],
                "plan_id": (
                    request["commit"].get("plan_id")
                    if type(request.get("commit")) is dict
                    else None
                ),
                "reason": params["reason"],
            }
        elif message["method"] == "job/subscribe":
            _require(
                set(params)
                == {
                    "delivery",
                    "event_families",
                    "position",
                    "request",
                    "scope",
                }
                and type(params["request"]) is dict
                and type(params["scope"]) is dict
                and set(params["scope"])
                == {"job_id", "job_submission_id", "kind"}
                and params["scope"]["kind"] == "job"
                and type(params["scope"]["job_id"]) is str
                and re.fullmatch(
                    r"job-v2:[0-9a-f]{32}", params["scope"]["job_id"]
                )
                is not None
                and type(params["scope"]["job_submission_id"]) is str
                and params["scope"]["job_submission_id"].startswith(
                    "job-submission:sha256:"
                )
                and _CONTENT_ID.fullmatch(
                    params["scope"]["job_submission_id"]
                )
                is not None
                and params["event_families"] == ["job"]
                and params["delivery"] == "lossless"
                and type(params["position"]) is dict,
                "invalid-request",
                "job subscription parameters are invalid or open",
            )
            request = params["request"]
            arguments = {
                "delivery": params["delivery"],
                "event_families": list(params["event_families"]),
                "job_id": params["scope"]["job_id"],
                "job_submission_id": params["scope"][
                    "job_submission_id"
                ],
                "position": dict(params["position"]),
            }
        else:
            _require(
                set(params) == {"arguments", "request"}
                and type(params["arguments"]) is dict
                and type(params["request"]) is dict,
                "invalid-request",
                "this hosted method requires the generic closed V3 parameter shape",
            )
            request = params["request"]
            arguments = params["arguments"]
        _require(
            set(request)
            == {
                "capability_id",
                "capability_version",
                "commit",
                "context_ref_id",
                "deadline",
                "idempotency_key",
                "input_binding_id",
                "intent",
                "operation_class",
                "protocol_version",
                "request_id",
                "resource_budgets",
            }
            and request["protocol_version"] == dict(PROTOCOL_VERSION)
            and type(request["request_id"]) is str
            and _REQUEST_ID.fullmatch(request["request_id"]) is not None
            and request["request_id"] not in self._request_ids,
            "invalid-request",
            "service request binding is invalid, open, or duplicated",
        )
        key = (request["capability_id"], message["method"])
        binding = self.host.method_bindings.get(key)
        _require(
            binding is not None
            and request["capability_version"]
            == binding.registration.capability_version,
            "unavailable-capability",
            "no exact registered handler matches the requested capability",
        )
        context_mode, input_mode = self.host._binding_modes[key]
        context_ref_id = request["context_ref_id"]
        input_binding_id = request["input_binding_id"]
        valid_context = (
            context_mode == "none" and context_ref_id is None
        ) or (
            context_mode == "required"
            and type(context_ref_id) is str
            and context_ref_id.startswith("context-ref:sha256:")
            and _CONTENT_ID.fullmatch(context_ref_id) is not None
        )
        valid_input = (
            input_mode == "none" and input_binding_id is None
        ) or (
            input_mode == "required"
            and type(input_binding_id) is str
            and input_binding_id.startswith("input-binding:sha256:")
            and _CONTENT_ID.fullmatch(input_binding_id) is not None
        )
        _require(
            request["operation_class"] == self.host._operation_classes[key]
            and type(request["intent"]) is str
            and re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", request["intent"])
            is not None
            and valid_context
            and valid_input,
            "invalid-request",
            "request operation, intent, or exact context binding is invalid",
        )
        budgets = request["resource_budgets"]
        _require(
            type(budgets) is list and len(budgets) <= 32,
            "budget-exceeded",
            "request resource budget set is invalid or too large",
        )
        seen_budgets: set[tuple[str, str]] = set()
        for row in budgets:
            _require(
                type(row) is dict
                and set(row) == {"budget_key", "limit", "unit"}
                and type(row["budget_key"]) is str
                and type(row["unit"]) is str
                and type(row["limit"]) is int
                and row["limit"] > 0,
                "budget-exceeded",
                "request resource budget is invalid or open",
            )
            budget_key = (row["budget_key"], row["unit"])
            selected_limit = self.host._budget_limits[key].get(budget_key)
            _require(
                budget_key not in seen_budgets
                and selected_limit is not None
                and row["limit"] <= selected_limit,
                "budget-exceeded",
                "request budget is duplicated, undeclared, or above the capability limit",
            )
            seen_budgets.add(budget_key)
        if "maximum_events" in arguments:
            event_limit = next(
                (
                    row["limit"]
                    for row in budgets
                    if (row["budget_key"], row["unit"])
                    == ("events", "events")
                ),
                self.host._budget_limits[key].get(("events", "events")),
            )
            _require(
                type(arguments["maximum_events"]) is int
                and type(event_limit) is int
                and 1 <= arguments["maximum_events"] <= event_limit,
                "budget-exceeded",
                "requested event page exceeds its exact transport budget",
            )
        deadline = request["deadline"]
        if deadline is not None:
            _require(
                type(deadline) is str and deadline.endswith("Z"),
                "deadline-exceeded",
                "request deadline is invalid",
            )
            try:
                deadline_value = datetime.fromisoformat(
                    deadline.removesuffix("Z") + "+00:00"
                )
            except ValueError as exc:
                raise ServiceHostV3Error(
                    "deadline-exceeded", "request deadline is invalid"
                ) from exc
            _require(
                deadline_value > datetime.now(timezone.utc),
                "deadline-exceeded",
                "request deadline elapsed before dispatch",
            )
        idempotency = request["idempotency_key"]
        if binding.registration.asynchronous or message["method"] == "job/cancel":
            _require(
                type(idempotency) is str
                and re.fullmatch(r"idempotency-v3:[0-9a-f]{32}", idempotency)
                is not None,
                "invalid-request",
                "durable operation requires an exact idempotency key",
            )
        elif idempotency is not None:
            _require(
                type(idempotency) is str,
                "invalid-request",
                "idempotency key is invalid",
            )
        commit = request["commit"]
        _require(
            commit is None
            or (
                type(commit) is dict
                and commit.get("idempotency_key") == idempotency
            ),
            "invalid-request",
            "commit and request idempotency bindings differ",
        )
        declared_plan_id = arguments.get("plan_id")
        if declared_plan_id is not None:
            _require(
                type(declared_plan_id) is str
                and declared_plan_id.startswith("operation-plan:sha256:")
                and type(commit) is dict
                and set(commit)
                == {
                    "consent_record_id",
                    "expected_head_refs",
                    "idempotency_key",
                    "plan_id",
                }
                and commit["plan_id"] == declared_plan_id
                and type(commit["consent_record_id"]) is str
                and commit["consent_record_id"].startswith(
                    "consent-decision:sha256:"
                )
                and type(commit["expected_head_refs"]) is list
                and bool(commit["expected_head_refs"]),
                "invalid-request",
                "proof-bound materialization requires exact plan, consent, and expected heads",
            )
            prior_ref: tuple[bytes, bytes] | None = None
            seen_refs: set[tuple[str, str]] = set()
            for row in commit["expected_head_refs"]:
                _require(
                    type(row) is dict
                    and set(row) == {"record_id", "record_kind"}
                    and type(row["record_kind"]) is str
                    and type(row["record_id"]) is str
                    and _CONTENT_ID.fullmatch(row["record_id"]) is not None
                    and row["record_id"].split(":sha256:", 1)[0]
                    == row["record_kind"],
                    "invalid-request",
                    "commit expected-head reference is invalid or open",
                )
                logical = (row["record_kind"], row["record_id"])
                sort_key = (
                    logical[0].encode("utf-8"),
                    logical[1].encode("utf-8"),
                )
                _require(
                    logical not in seen_refs
                    and (prior_ref is None or prior_ref < sort_key),
                    "invalid-request",
                    "commit expected-head references are duplicated or unordered",
                )
                seen_refs.add(logical)
                prior_ref = sort_key
        self._request_ids.add(request["request_id"])
        projection = self._owner_projection(binding, message)
        idempotency_key = request["idempotency_key"]
        if idempotency_key is None:
            idempotency_key = request["request_id"]
        result = self.host.runtime.dispatch(
            {
                "arguments": arguments,
                "capability_id": request["capability_id"],
                "context_ref_id": request["context_ref_id"],
                "idempotency_key": idempotency_key,
                "input_binding_id": request["input_binding_id"],
                "method": message["method"],
                "request_id": request["request_id"],
            }
        )
        response_context_ref_id = request["context_ref_id"]
        if message["method"] == "context/register" and result["outcome"] == "succeeded":
            registered_context_ref_id = result["result"].get("context_ref_id")
            _require(
                type(registered_context_ref_id) is str
                and registered_context_ref_id.startswith("context-ref:sha256:")
                and _CONTENT_ID.fullmatch(registered_context_ref_id) is not None,
                "service.invalid-handler-result",
                "context registration did not return its exact context identity",
            )
            response_context_ref_id = registered_context_ref_id
        binding_value = {
            "capability_id": binding.registration.capability_id,
            "capability_version": binding.registration.capability_version,
            "context_ref_id": response_context_ref_id,
            "diagnostics": [dict(row) for row in projection.diagnostics],
            "handler_id": binding.registration.handler_id,
            "handler_implementation_id": binding.registration.implementation_id,
            "input_binding_id": request["input_binding_id"],
            "input_revision_refs": [
                dict(row) for row in projection.input_revision_refs
            ],
            "limitations": [dict(row) for row in projection.limitations],
            "output_revision_refs": [
                dict(row) for row in projection.output_revision_refs
            ],
            "protocol_version": dict(PROTOCOL_VERSION),
            "request_id": request["request_id"],
            "request_method": message["method"],
            "states": dict(projection.states),
        }
        if result["outcome"] == "accepted":
            job = dict(result["job"])
            job.pop("terminal_outcome", None)
            job_ref = {
                "record_id": job["job_submission_id"],
                "record_kind": "job-submission",
            }
            if job_ref not in binding_value["output_revision_refs"]:
                binding_value["output_revision_refs"].append(job_ref)
            outcome = {"job": job, "state": "accepted"}
        else:
            outcome = {
                "continuation_id": projection.continuation_id,
                "object_references": [
                    dict(row) for row in projection.object_references
                ],
                "result_schema_id": binding.result_schema_id,
                "state": "succeeded",
                "value": result["result"],
            }
        return {
            "id": message["id"],
            "jsonrpc": "2.0",
            "result": {
                "binding": binding_value,
                "format": "workbench-service-result-v3",
                "outcome": outcome,
            },
        }


class LocalServiceEndpointV3:
    """Owner-private local endpoint for one already-composed host."""

    def __init__(
        self,
        host: ServiceHostV3,
        *,
        endpoint_path: Path,
        maximum_connections: int = 16,
    ) -> None:
        _require(
            isinstance(endpoint_path, Path)
            and endpoint_path.is_absolute()
            and endpoint_path.parent.is_dir()
            and not endpoint_path.parent.is_symlink()
            and private_path(endpoint_path.parent, directory=True),
            "service.invalid-endpoint",
            "endpoint must be placed in an existing owner-private directory",
        )
        _require(
            type(maximum_connections) is int and 1 <= maximum_connections <= 64,
            "service.invalid-budget",
            "endpoint connection budget is invalid",
        )
        self.host = host
        self.endpoint_path = endpoint_path
        self.maximum_connections = maximum_connections
        self._listener: socket.socket | None = None
        self._endpoint_identity: tuple[int, int] | None = None
        self._accept_thread: threading.Thread | None = None
        self._workers: set[threading.Thread] = set()
        self._worker_lock = threading.Lock()
        self._stopping = threading.Event()
        self._slots = threading.BoundedSemaphore(maximum_connections)

    def start(self) -> None:
        _require(
            self._listener is None and not self.endpoint_path.exists() and not self.endpoint_path.is_symlink(),
            "service.endpoint-busy",
            "local service endpoint already exists or is active",
        )
        if os.name != "nt":
            _require(len(os.fsencode(self.endpoint_path)) <= 103, "service.invalid-endpoint", "local service endpoint exceeds the portable AF_UNIX path bound")
        listener = socket.socket(
            socket.AF_INET if os.name == "nt" else socket.AF_UNIX,
            socket.SOCK_STREAM,
        )
        try:
            if os.name == "nt":
                listener.setsockopt(
                    socket.SOL_SOCKET,
                    socket.SO_EXCLUSIVEADDRUSE,
                    1,
                )
                listener.bind(("127.0.0.1", 0))
            else:
                listener.bind(str(self.endpoint_path))
                observation = self.endpoint_path.lstat()
                self._endpoint_identity = (observation.st_dev, observation.st_ino)
                secure_private_endpoint(self.endpoint_path)
            listener.listen(self.maximum_connections)
            listener.settimeout(0.2)
            if os.name == "nt":
                _publish_windows_endpoint(self.endpoint_path, listener)
                observation = self.endpoint_path.lstat()
                self._endpoint_identity = (observation.st_dev, observation.st_ino)
        except Exception:
            listener.close()
            self._remove_owned_endpoint()
            raise
        self._listener = listener
        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name="workbench-service-v3-endpoint",
            daemon=True,
        )
        self._accept_thread.start()

    def _accept_loop(self) -> None:
        assert self._listener is not None
        while not self._stopping.is_set():
            try:
                connection, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not self._slots.acquire(blocking=False):
                connection.close()
                continue
            worker = threading.Thread(
                target=self._serve_connection,
                args=(connection,),
                name="workbench-service-v3-client",
                daemon=True,
            )
            with self._worker_lock:
                self._workers.add(worker)
            worker.start()

    def _serve_connection(self, connection: socket.socket) -> None:
        current = threading.current_thread()
        try:
            connection.settimeout(30)
            stream = connection.makefile("rwb", buffering=0)
            auth = read_message(stream)
            _require(
                type(auth) is dict
                and set(auth) == {"bearer_token", "format", "transport"}
                and auth["format"] == "workbench-local-service-auth-v1",
                "service.authentication-failed",
                "local service authentication preface is invalid",
            )
            self.host.authenticator.authenticate(auth["bearer_token"])
            write_message(
                stream,
                {"format": "workbench-local-service-auth-result-v1", "accepted": True},
            )
            session = self.host.new_session(auth["transport"])
            while not self._stopping.is_set():
                message = read_message(stream)
                if message is None:
                    break
                response = session.handle_message(
                    message, bearer_token=auth["bearer_token"]
                )
                if response is not None:
                    write_message(stream, response)
                if session.closed:
                    break
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except (OSError, UnboundLocalError):
                pass
            try:
                connection.close()
            except OSError:
                pass
            with self._worker_lock:
                self._workers.discard(current)
            self._slots.release()

    def _remove_owned_endpoint(self) -> None:
        """Never remove a path won or replaced by another endpoint owner."""
        if self._endpoint_identity is not None:
            try:
                observation = self.endpoint_path.lstat()
                if (observation.st_dev, observation.st_ino) == self._endpoint_identity:
                    self.endpoint_path.unlink()
            except FileNotFoundError:
                pass
            self._endpoint_identity = None

    def close(self) -> None:
        self._stopping.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            self._listener = None
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=5)
            self._accept_thread = None
        with self._worker_lock:
            workers = tuple(self._workers)
        for worker in workers:
            worker.join(timeout=5)
        self._remove_owned_endpoint()

    def __enter__(self) -> "LocalServiceEndpointV3":
        self.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


class LocalServiceClientV3:
    """Bounded client used by CLI and the stateless stdio proxy."""

    def __init__(self, endpoint_path: Path, bearer_token: str) -> None:
        self.endpoint_path = endpoint_path
        self.bearer_token = bearer_token

    def connect(
        self, *, transport: str = "local-endpoint"
    ) -> "LocalServiceConnectionV3":
        return LocalServiceConnectionV3(
            self.endpoint_path, self.bearer_token, transport=transport
        )

    def exchange(self, messages: tuple[Mapping[str, Any], ...]) -> tuple[dict[str, Any], ...]:
        _require(
            type(messages) is tuple and 1 <= len(messages) <= 1024,
            "service.invalid-request-batch",
            "local service exchange must be a bounded exact tuple",
        )
        with self.connect() as connection:
            return tuple(connection.call(message) for message in messages)


class LocalServiceConnectionV3:
    """One authenticated endpoint connection preserving one V3 session."""

    def __init__(
        self,
        endpoint_path: Path,
        bearer_token: str,
        *,
        transport: str,
    ) -> None:
        self.endpoint_path = endpoint_path
        self.bearer_token = bearer_token
        _require(
            transport in {"local-endpoint", "stdio"},
            "service.unsupported-transport",
            "local connection transport projection is unsupported",
        )
        self.transport = transport
        self._connection: socket.socket | None = None
        self._stream: BinaryIO | None = None

    def __enter__(self) -> "LocalServiceConnectionV3":
        connection = socket.socket(
            socket.AF_INET if os.name == "nt" else socket.AF_UNIX,
            socket.SOCK_STREAM,
        )
        try:
            connection.settimeout(30)
            if os.name == "nt":
                connection.connect(_windows_endpoint_value(self.endpoint_path))
            else:
                connection.connect(str(self.endpoint_path))
            stream = connection.makefile("rwb", buffering=0)
            write_message(
                stream,
                {
                    "bearer_token": self.bearer_token,
                    "format": "workbench-local-service-auth-v1",
                    "transport": self.transport,
                },
            )
            auth = read_message(stream)
            _require(
                auth
                == {
                    "accepted": True,
                    "format": "workbench-local-service-auth-result-v1",
                },
                "service.authentication-failed",
                "local service authentication was not accepted",
            )
        except Exception:
            connection.close()
            raise
        self._connection = connection
        self._stream = stream
        return self

    def call(self, message: Mapping[str, Any]) -> dict[str, Any]:
        _require(
            self._stream is not None,
            "service.transport-failure",
            "local service connection is not open",
        )
        try:
            write_message(self._stream, dict(message))
            response = read_message(self._stream)
        except OSError as exc:
            raise ServiceHostV3Error(
                "service.transport-failure",
                f"local endpoint exchange failed with {type(exc).__name__}",
                retryable=True,
            ) from exc
        _require(
            type(response) is dict,
            "service.transport-failure",
            "local service closed before returning a response",
        )
        return response

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.close()
            except OSError:
                pass
        if self._connection is not None:
            try:
                self._connection.close()
            except OSError:
                pass
        self._connection = None
        self._stream = None

    def __exit__(self, *_args: Any) -> None:
        self.close()


def serve_stdio_service_proxy_v3(
    input_stream: BinaryIO,
    output_stream: BinaryIO,
    error_stream: TextIO,
    *,
    client: LocalServiceClientV3,
) -> int:
    """Frame and forward V3 messages; the proxy owns no store or runtime."""

    try:
        connection = client.connect(transport="stdio")
        connection.__enter__()
    except (OSError, ServiceHostV3Error) as exc:
        print(f"workbench-shell: V3 proxy connection failure: {exc}", file=error_stream)
        return 3
    try:
        while True:
            try:
                message = read_message(input_stream)
            except JsonPayloadError:
                write_message(
                    output_stream,
                    {
                        "error": {
                            "code": -32700,
                            "data": {"kind": "invalid_json"},
                            "message": "Parse error",
                        },
                        "id": None,
                        "jsonrpc": "2.0",
                    },
                )
                continue
            except FramingError as exc:
                print(f"workbench-shell: fatal V3 framing error: {exc}", file=error_stream)
                return 2
            if message is None:
                return 0
            try:
                response = connection.call(message)
            except ServiceHostV3Error as exc:
                print(f"workbench-shell: V3 proxy failure: {exc}", file=error_stream)
                return 3
            write_message(output_stream, response)
            if message.get("method") == "service/shutdown":
                return 0
    finally:
        connection.close()


__all__ = [
    "HostedMethodBinding",
    "LocalServiceClientV3",
    "LocalServiceConnectionV3",
    "LocalServiceEndpointV3",
    "OwnerBindingProjection",
    "PosixServiceProviderReceipt",
    "ServiceHostV3",
    "ServiceHostV3Error",
    "ServiceRpcSessionV3",
    "ServiceTransportSelection",
    "local_service_physical_lease_ports",
    "posix_service_physical_lease_ports",
    "qualify_posix_service_provider",
    "select_service_transport",
    "serve_stdio_service_proxy_v3",
    "windows_service_physical_lease_ports",
]
