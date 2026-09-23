"""Flat Host Adapter V3 inspection and executable identity custody.

V3 directly validates its complete capability vocabulary, adds descriptor-pinned
executable identity discovery, and derives operation-specific capability readiness.
It remains a disposable developer diagnostic and never qualifies a release.
"""

from __future__ import annotations

from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
import platform
import socket
import stat
import subprocess
import sys
import tempfile
import time
from typing import Callable, Mapping
import unicodedata
from urllib.parse import quote, unquote_to_bytes, urlsplit


FORMAT = "workbench-host-adapter-conformance-v3"
SCHEMA_VERSION = 3
ADAPTER_FORMAT = "workbench-host-adapter-v3"
ADAPTER_ID = "workbench.local-python-host-adapter:v3"
MEASUREMENT_FORMAT = "workbench-executable-measurement-v1"
MAX_TEXT_BYTES = 8192
MAX_PROBE_TEXT_BYTES = 4096
MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024
MAX_SEARCH_ROOTS = 32
MAX_DESCRIPTOR_IDENTITY = (1 << 128) - 1
WINDOWS_EXECUTABLE_SUFFIXES = frozenset({".exe", ".com"})

BASE_CAPABILITY_IDS = (
    "path-uri-round-trip",
    "unicode-path-round-trip",
    "filesystem-identity",
    "same-directory-atomic-replace",
    "file-durable-sync",
    "directory-durable-sync",
    "advisory-exclusive-lock",
    "exact-argv-spawn",
    "direct-process-termination",
    "process-tree-termination",
    "monotonic-clock",
    "local-ipc",
    "credential-storage",
    "desktop-notifications",
    "runtime-delegation",
)
CAPABILITY_STATES = frozenset({"available", "unavailable", "unverified"})

CAPABILITY_IDS = (
    *BASE_CAPABILITY_IDS[:7],
    "executable-identity-discovery",
    *BASE_CAPABILITY_IDS[7:],
)

CORE_HOST_CAPABILITY_IDS = frozenset(
    {
        "path-uri-round-trip",
        "unicode-path-round-trip",
        "filesystem-identity",
        "same-directory-atomic-replace",
        "file-durable-sync",
        "executable-identity-discovery",
        "exact-argv-spawn",
        "direct-process-termination",
        "monotonic-clock",
    }
)

OPERATION_REQUIREMENTS = (
    (
        "core-host",
        tuple(sorted(CORE_HOST_CAPABILITY_IDS)),
    ),
    (
        "durable-publication",
        tuple(
            sorted(
                CORE_HOST_CAPABILITY_IDS
                | {"advisory-exclusive-lock", "directory-durable-sync"}
            )
        ),
    ),
    (
        "hosted-local-service",
        tuple(
            sorted(
                CORE_HOST_CAPABILITY_IDS
                | {
                    "advisory-exclusive-lock",
                    "local-ipc",
                    "process-tree-termination",
                }
            )
        ),
    ),
    (
        "credential-backed-operation",
        tuple(sorted(CORE_HOST_CAPABILITY_IDS | {"credential-storage"})),
    ),
    (
        "desktop-notification",
        tuple(sorted(CORE_HOST_CAPABILITY_IDS | {"desktop-notifications"})),
    ),
    (
        "delegated-runtime",
        tuple(sorted(CORE_HOST_CAPABILITY_IDS | {"runtime-delegation"})),
    ),
)


_NEXT_ACTIONS = {
    "path-uri-round-trip": (
        "Provide an adapter-native canonical path/URI codec and rerun conformance."
    ),
    "unicode-path-round-trip": (
        "Move Workbench state to a filesystem with lossless Unicode names or supply "
        "an adapter-owned escaping scheme."
    ),
    "filesystem-identity": (
        "Provide stable adapter-owned file identity before enabling drift-sensitive "
        "or destructive operations."
    ),
    "same-directory-atomic-replace": (
        "Keep publication disabled until the adapter proves same-directory atomic "
        "replacement on the selected storage."
    ),
    "file-durable-sync": (
        "Keep durable commits disabled until file data and metadata can be flushed."
    ),
    "directory-durable-sync": (
        "Treat directory-entry crash durability as unverified and retain recovery "
        "markers until an adapter-specific primitive is qualified."
    ),
    "advisory-exclusive-lock": (
        "Use a qualified adapter lock provider before starting a shared writer."
    ),
    "exact-argv-spawn": (
        "Configure an adapter process launcher that preserves argument boundaries "
        "without a shell."
    ),
    "direct-process-termination": (
        "Disable cancellable jobs until the adapter can terminate and reap its direct "
        "child process."
    ),
    "process-tree-termination": (
        "Qualify an adapter-owned job/process-tree primitive before claiming bounded "
        "descendant cancellation."
    ),
    "monotonic-clock": (
        "Provide a monotonic adapter clock before enforcing deadlines or event order."
    ),
    "local-ipc": (
        "Select and qualify an authenticated adapter transport for hosted mode."
    ),
    "credential-storage": (
        "Configure and qualify an adapter credential provider; do not persist secrets "
        "in the workspace."
    ),
    "desktop-notifications": (
        "Keep notifications optional or configure a qualified adapter provider."
    ),
    "runtime-delegation": (
        "Configure an identity-bound remote execution adapter when local Minecraft "
        "execution is unavailable."
    ),
}


@dataclass(frozen=True)
class _ProbeResult:
    state: str
    evidence: tuple[str, ...]
    limitation: str | None = None


class HostAdapterV3Error(ValueError):
    """Raised when V3 discovery or receipt validation fails closed."""


def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _identity(prefix: str, value: Mapping[str, object], field: str) -> str:
    projection = dict(value)
    projection.pop(field, None)
    return prefix + hashlib.sha256(_canonical_bytes(projection)).hexdigest()


def compute_executable_measurement_id(value: Mapping[str, object]) -> str:
    return _identity(
        "workbench-executable-measurement:sha256:",
        value,
        "measurement_id",
    )


def compute_host_adapter_v3_receipt_id(value: Mapping[str, object]) -> str:
    return _identity(
        "workbench-host-adapter-conformance-v3:sha256:",
        value,
        "receipt_id",
    )


def _safe_text(value: object, *, allow_empty: bool = False) -> str:
    if type(value) is not str or (not value and not allow_empty):
        raise HostAdapterV3Error("value must be a bounded semantic string")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise HostAdapterV3Error("value must contain valid UTF-8 Unicode") from exc
    if (
        len(encoded) > MAX_TEXT_BYTES
        or value != unicodedata.normalize("NFC", value)
        or value != value.strip()
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise HostAdapterV3Error("value is not safe bounded semantic text")
    return value


def _safe_detail(exc: BaseException) -> str:
    """Return a bounded diagnostic that does not copy ambient paths or secrets."""

    return f"{type(exc).__module__}.{type(exc).__qualname__}"


def _is_safe_text(value: object, *, allow_empty: bool = False) -> bool:
    if type(value) is not str or (not allow_empty and not value):
        return False
    try:
        encoded = value.encode("utf-8", errors="strict")
        normalized = unicodedata.normalize("NFC", value)
    except UnicodeError:
        return False
    return (
        len(encoded) <= MAX_PROBE_TEXT_BYTES
        and value == normalized
        and value == value.strip()
        and not any(
            unicodedata.category(character).startswith("C")
            for character in value
        )
    )


def _safe_platform_label(provider: Callable[[], object]) -> str:
    """Read one diagnostic label without making it a probe-wide dependency."""

    try:
        value = provider()
    except Exception:
        return "unreported"
    return value if _is_safe_text(value) else "unreported"


def _available(*evidence: str) -> _ProbeResult:
    return _ProbeResult("available", tuple(evidence))


def _unavailable(exc: BaseException | str) -> _ProbeResult:
    detail = exc if isinstance(exc, str) else _safe_detail(exc)
    return _ProbeResult("unavailable", (), detail)


def _unverified(reason: str) -> _ProbeResult:
    return _ProbeResult("unverified", (), reason)


def _probe_exception_result(exc: Exception) -> _ProbeResult:
    if isinstance(exc, (AttributeError, ImportError, NotImplementedError)):
        return _unverified(_safe_detail(exc))
    return _unavailable(exc)


def _pure_path(path: os.PathLike[str] | str, *, flavour: str) -> PurePath:
    """Return one explicit-flavour path without consulting the running host."""

    if flavour == "posix":
        return PurePosixPath(path)
    if flavour == "windows":
        return PureWindowsPath(path)
    raise ValueError("path flavour must be 'posix' or 'windows'")


_WINDOWS_FORBIDDEN_COMPONENT_CHARACTERS = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED_COMPONENT_STEMS = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{suffix}" for suffix in "123456789¹²³"),
        *(f"LPT{suffix}" for suffix in "123456789¹²³"),
    }
)


def _validate_path_component(component: str, *, flavour: str) -> None:
    if component in {"", ".", ".."}:
        raise ValueError("file URI path contains an empty or traversal component")
    try:
        component.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError("file URI path contains invalid Unicode") from exc
    if any(
        character == "\x00"
        or unicodedata.category(character).startswith("C")
        for character in component
    ):
        raise ValueError("file URI path contains a NUL or control character")
    if flavour != "windows":
        return
    if any(
        character in _WINDOWS_FORBIDDEN_COMPONENT_CHARACTERS
        for character in component
    ):
        raise ValueError("file URI path contains an invalid Windows character")
    if component.endswith((" ", ".")):
        raise ValueError("file URI path contains a trailing Windows alias")
    stem = component.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED_COMPONENT_STEMS:
        raise ValueError("file URI path contains a reserved Windows component")


def _validate_pure_path(path: PurePath, *, flavour: str) -> None:
    if flavour == "posix":
        if path.anchor != "/":
            raise ValueError("POSIX file URI paths require one local root")
        components = path.parts[1:]
    else:
        windows = PureWindowsPath(path)
        components = windows.parts[1:]
        if windows.drive.startswith("\\\\"):
            drive_parts = windows.drive[2:].split("\\", 1)
            if len(drive_parts) != 2 or not all(drive_parts):
                raise ValueError("UNC paths require a server and share")
            authority, share = drive_parts
            _validate_path_component(authority, flavour="windows")
            _validate_path_component(share, flavour="windows")
        elif (
            len(windows.drive) != 2
            or not windows.drive[0].isalpha()
            or windows.drive[1] != ":"
        ):
            raise ValueError("Windows file URI paths require a drive or UNC share")
    for component in components:
        _validate_path_component(component, flavour=flavour)


def _path_to_file_uri(
    path: os.PathLike[str] | str, *, flavour: str
) -> str:
    """Encode an absolute POSIX or Windows path as one canonical file URI.

    ``flavour`` is explicit so Windows drive and UNC behavior can be exercised
    on every development host.  The local probe passes the running host's
    flavour and then reopens the decoded path; this helper alone is not
    capability evidence.
    """

    pure = _pure_path(path, flavour=flavour)
    if not pure.is_absolute():
        raise ValueError("file URI paths must be absolute")
    _validate_pure_path(pure, flavour=flavour)

    if flavour == "posix":
        encoded = quote(str(pure), safe="/")
        return "file://" + encoded

    windows = PureWindowsPath(pure)
    drive = windows.drive
    if drive.startswith("\\\\"):
        if drive.startswith(("\\\\?\\", "\\\\.\\")):
            raise ValueError("Windows device namespaces are not portable file URIs")
        authority_and_share = drive[2:].split("\\", 1)
        if len(authority_and_share) != 2 or not all(authority_and_share):
            raise ValueError("UNC paths require a server and share")
        authority, share = authority_and_share
        tail = "/".join(windows.parts[1:])
        encoded_path = "/" + quote(share, safe="")
        if tail:
            encoded_path += "/" + quote(tail, safe="/")
        return "file://" + quote(authority, safe="") + encoded_path

    if len(drive) != 2 or not drive[0].isalpha() or drive[1] != ":":
        raise ValueError("Windows file URI paths require a drive or UNC share")
    encoded_path = quote(windows.as_posix(), safe="/:")
    return "file:///" + encoded_path


def _decode_uri_text(value: str) -> str:
    try:
        return unquote_to_bytes(value).decode("utf-8", errors="strict")
    except (UnicodeDecodeError, UnicodeEncodeError) as exc:
        raise ValueError("file URI contains invalid UTF-8") from exc


def _file_uri_to_path(uri: str, *, flavour: str) -> PurePath:
    """Decode one URI emitted by :func:`_path_to_file_uri`."""

    if type(uri) is not str or not uri:
        raise ValueError("file URI must be a non-empty string")
    try:
        parsed = urlsplit(uri)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("file URI is malformed") from exc
    if parsed.scheme != "file" or parsed.query or parsed.fragment:
        raise ValueError("only canonical file URIs without query or fragment qualify")

    if flavour == "posix":
        if parsed.netloc:
            raise ValueError("the local POSIX adapter does not accept URI authorities")
        decoded = _decode_uri_text(parsed.path)
        if not decoded.startswith("/") or decoded.startswith("//"):
            raise ValueError("POSIX file URI requires one local root")
        for component in decoded.split("/")[1:]:
            if component:
                _validate_path_component(component, flavour="posix")
            elif decoded != "/":
                raise ValueError("file URI path contains an empty component")
        candidate: PurePath = PurePosixPath(decoded)
    elif flavour == "windows":
        decoded_path = _decode_uri_text(parsed.path)
        if "\\" in decoded_path:
            raise ValueError("Windows file URI path contains a separator alias")
        if parsed.netloc:
            authority = _decode_uri_text(parsed.netloc)
            _validate_path_component(authority, flavour="windows")
            if not decoded_path.startswith("/"):
                raise ValueError("UNC file URI requires an absolute share path")
            share_path = decoded_path[1:]
            if not share_path.strip("/"):
                raise ValueError("UNC file URI requires a share")
            components = share_path.split("/")
            for component in components:
                _validate_path_component(component, flavour="windows")
            candidate = PureWindowsPath(
                "\\\\" + authority + "\\" + share_path.replace("/", "\\")
            )
        else:
            if (
                len(decoded_path) < 4
                or decoded_path[0] != "/"
                or not decoded_path[1].isalpha()
                or decoded_path[2:4] != ":/"
            ):
                raise ValueError("Windows file URI requires /<drive>:/")
            tail = decoded_path[4:]
            if tail:
                for component in tail.split("/"):
                    _validate_path_component(component, flavour="windows")
            candidate = PureWindowsPath(decoded_path[1:].replace("/", "\\"))
    else:
        raise ValueError("path flavour must be 'posix' or 'windows'")

    if not candidate.is_absolute() or _path_to_file_uri(
        candidate, flavour=flavour
    ) != uri:
        raise ValueError("file URI is not a canonical lossless path encoding")
    return candidate


def _probe_path_uri(root: Path) -> _ProbeResult:
    target = root / "path target-資料.txt"
    payload = b"Workbench Host Adapter V3 path URI probe\n"
    try:
        target.write_bytes(payload)
        canonical = target.resolve(strict=True)
        if isinstance(canonical, PureWindowsPath):
            flavour = "windows"
        elif isinstance(canonical, PurePosixPath):
            flavour = "posix"
        else:
            return _unverified("running pathlib exposes an unknown local path flavour")
        uri = _path_to_file_uri(canonical, flavour=flavour)
        decoded = _file_uri_to_path(uri, flavour=flavour)
        reopened = Path(decoded)
        if reopened.read_bytes() != payload:
            return _unavailable("file URI reopened a different payload")
        if decoded != _pure_path(canonical, flavour=flavour):
            return _unavailable("file URI decoded to a different canonical path")
        return _available(
            "created file reopened through one canonical local file URI with exact bytes"
        )
    except (OSError, ValueError) as exc:
        return _unavailable(exc)


def _probe_unicode_path(root: Path) -> _ProbeResult:
    target = root / "workbench-Å-資料-𐐷.txt"
    payload = "Workbench host conformance\n".encode("utf-8")
    try:
        target.write_bytes(payload)
        entries = {entry.name for entry in root.iterdir()}
        if target.name not in entries or target.read_bytes() != payload:
            return _unavailable("Unicode filename or payload changed on round trip")
        return _available("non-ASCII BMP and supplementary-plane filename round-tripped")
    except OSError as exc:
        return _unavailable(exc)


def _probe_filesystem_identity(root: Path) -> _ProbeResult:
    target = root / "identity.bin"
    try:
        target.write_bytes(b"identity\n")
        with target.open("rb") as stream:
            by_descriptor = os.fstat(stream.fileno())
            by_path = target.stat()
        descriptor_identity = (
            by_descriptor.st_dev,
            by_descriptor.st_ino,
            stat.S_IFMT(by_descriptor.st_mode),
            by_descriptor.st_size,
        )
        path_identity = (
            by_path.st_dev,
            by_path.st_ino,
            stat.S_IFMT(by_path.st_mode),
            by_path.st_size,
        )
        if descriptor_identity != path_identity:
            return _unavailable("descriptor and pathname identities disagree")
        return _available("descriptor and pathname identity fields agreed")
    except OSError as exc:
        return _unavailable(exc)


def _probe_atomic_replace(root: Path) -> _ProbeResult:
    destination = root / "published.bin"
    staging = root / "staged.bin"
    try:
        destination.write_bytes(b"old\n")
        staging.write_bytes(b"new\n")
        os.replace(staging, destination)
        if destination.read_bytes() != b"new\n" or staging.exists():
            return _unavailable("same-directory replacement produced an unexpected view")
        return _available("same-directory replacement exposed the complete new payload")
    except OSError as exc:
        return _unavailable(exc)


def _probe_file_sync(root: Path) -> _ProbeResult:
    target = root / "sync.bin"
    try:
        with target.open("wb") as stream:
            stream.write(b"durable candidate\n")
            stream.flush()
            os.fsync(stream.fileno())
        return _available("regular-file flush and fsync completed")
    except OSError as exc:
        return _unavailable(exc)


def _probe_directory_sync(root: Path) -> _ProbeResult:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(root, flags)
        os.fsync(descriptor)
        return _available("directory descriptor fsync completed")
    except (OSError, TypeError) as exc:
        return _unverified(_safe_detail(exc))
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


def _probe_lock(root: Path) -> _ProbeResult:
    target = root / "lock.bin"
    try:
        import fcntl  # type: ignore[import-not-found]
    except ImportError:
        return _unverified("portable Python exposes no standard cross-process lock provider")
    first: int | None = None
    second: int | None = None
    try:
        target.write_bytes(b"0")
        first = os.open(target, os.O_RDWR)
        second = os.open(target, os.O_RDWR)
        fcntl.flock(first, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return _available("a competing nonblocking exclusive lock was rejected")
        return _unavailable("a competing descriptor acquired the exclusive lock")
    except OSError as exc:
        return _unavailable(exc)
    finally:
        if first is not None:
            with suppress(OSError):
                fcntl.flock(first, fcntl.LOCK_UN)
        for descriptor in (second, first):
            if descriptor is not None:
                with suppress(OSError):
                    os.close(descriptor)


def _probe_exact_argv(root: Path) -> _ProbeResult:
    token = "argument with spaces & metacharacters ; $()"
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; print(sys.argv[1], end='')",
                token,
            ],
            cwd=root,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _unavailable(exc)
    if completed.returncode != 0 or completed.stdout != token.encode("utf-8"):
        return _unavailable("child argument boundaries or output did not match")
    return _available("shell-free child preserved one exact metacharacter-bearing argument")


def _probe_direct_termination(root: Path) -> _ProbeResult:
    process: subprocess.Popen[bytes] | None = None
    result: _ProbeResult
    try:
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        process.terminate()
        process.wait(timeout=5)
        result = _available("direct child accepted termination and was reaped")
    except Exception as exc:
        result = _probe_exception_result(exc)
    finally:
        if process is not None:
            try:
                running = process.poll() is None
            except Exception:
                running = True
            if running:
                with suppress(Exception):
                    process.kill()
            with suppress(Exception):
                process.wait(timeout=5)
    return result


def _probe_monotonic_clock(_: Path) -> _ProbeResult:
    first = time.monotonic_ns()
    second = time.monotonic_ns()
    if second < first:
        return _unavailable("monotonic clock moved backwards")
    return _available("monotonic nanosecond clock was nondecreasing")


def _probe_local_ipc(_: Path) -> _ProbeResult:
    left: socket.socket | None = None
    right: socket.socket | None = None
    try:
        left, right = socket.socketpair()
        left.settimeout(2)
        right.settimeout(2)
        left.sendall(b"host-adapter-v3")
        if right.recv(64) != b"host-adapter-v3":
            return _unavailable("local IPC payload changed")
        return _available("bounded local socket pair exchanged exact bytes")
    except (OSError, TimeoutError) as exc:
        return _unavailable(exc)
    finally:
        for stream in (right, left):
            if stream is not None:
                with suppress(OSError):
                    stream.close()


_PROBES: dict[str, Callable[[Path], _ProbeResult]] = {
    "path-uri-round-trip": _probe_path_uri,
    "unicode-path-round-trip": _probe_unicode_path,
    "filesystem-identity": _probe_filesystem_identity,
    "same-directory-atomic-replace": _probe_atomic_replace,
    "file-durable-sync": _probe_file_sync,
    "directory-durable-sync": _probe_directory_sync,
    "advisory-exclusive-lock": _probe_lock,
    "exact-argv-spawn": _probe_exact_argv,
    "direct-process-termination": _probe_direct_termination,
    "monotonic-clock": _probe_monotonic_clock,
    "local-ipc": _probe_local_ipc,
}


def _run_probe(
    capability_id: str, probe: Callable[[Path], _ProbeResult], root: Path
) -> _ProbeResult:
    """Run one capability in an exception boundary independent of every peer."""

    try:
        result = probe(root)
    except Exception as exc:
        return _probe_exception_result(exc)
    coherent = type(result) is _ProbeResult
    if coherent:
        coherent = type(result.state) is str and result.state in CAPABILITY_STATES
    if coherent:
        coherent = (
            type(result.evidence) is tuple
            and len(result.evidence) <= 16
            and all(_is_safe_text(item) for item in result.evidence)
        )
    if coherent and result.state == "available":
        coherent = bool(result.evidence) and result.limitation is None
    elif coherent:
        coherent = not result.evidence and _is_safe_text(result.limitation)
    if not coherent:
        return _unavailable(
            f"probe {capability_id} returned a malformed internal result"
        )
    return result


def _static_unverified(capability_id: str) -> _ProbeResult:
    reasons = {
        "process-tree-termination": (
            "direct-child termination does not prove descendant containment"
        ),
        "credential-storage": "no credential provider was configured",
        "desktop-notifications": "no notification provider was configured",
        "runtime-delegation": "no remote execution provider was configured",
    }
    return _unverified(reasons[capability_id])


def _inspect_base_capabilities(
    *, scratch_parent: Path | str | None = None
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Exercise every base capability and return one unversioned local snapshot."""

    parent = None if scratch_parent is None else Path(scratch_parent).resolve()
    if parent is not None and (not parent.exists() or not parent.is_dir()):
        raise HostAdapterV3Error(
            "/scratch_parent: must be an existing directory controlled by the caller"
        )

    rows: list[dict[str, object]] = []
    try:
        with tempfile.TemporaryDirectory(
            prefix="workbench-host-adapter-v3-", dir=parent
        ) as temporary:
            root = Path(temporary)
            for capability_id in BASE_CAPABILITY_IDS:
                probe = _PROBES.get(capability_id)
                result = (
                    _run_probe(capability_id, probe, root)
                    if probe is not None
                    else _static_unverified(capability_id)
                )
                rows.append(
                    {
                        "id": capability_id,
                        "state": result.state,
                        "evidence": list(result.evidence),
                        "limitation": result.limitation,
                        "next_safe_action": _NEXT_ACTIONS[capability_id],
                    }
                )
    except OSError as exc:
        raise HostAdapterV3Error(
            "/scratch_parent: cannot create or clean disposable conformance storage"
        ) from exc

    adapter = {
        "format": ADAPTER_FORMAT,
        "adapter_id": ADAPTER_ID,
        "path_flavour": _path_flavour(Path.cwd()),
        "python_implementation": _safe_platform_label(platform.python_implementation),
        "python_version": _safe_platform_label(platform.python_version),
        "system_label": _safe_platform_label(platform.system),
        "system_release": _safe_platform_label(platform.release),
        "machine_label": _safe_platform_label(platform.machine),
    }
    return adapter, rows


def _path_flavour(path: object) -> str:
    if isinstance(path, PureWindowsPath):
        return "windows"
    if isinstance(path, PurePosixPath):
        return "posix"
    raise HostAdapterV3Error("running pathlib exposes an unknown local path flavour")


def _resolve_candidates(
    candidate: str | Path,
    search_roots: tuple[Path | str, ...],
) -> Path:
    if type(candidate) is not str and not isinstance(candidate, Path):
        raise HostAdapterV3Error("candidate must be an explicit string or Path")
    text = _safe_text(str(candidate))
    if "/" in text or "\\" in text:
        requested_path = Path(text)
        if not requested_path.is_absolute():
            raise HostAdapterV3Error(
                "relative executable discovery accepts one filename only"
            )
    requested = Path(text)
    if len(search_roots) > MAX_SEARCH_ROOTS:
        raise HostAdapterV3Error(
            f"search_roots exceeds the {MAX_SEARCH_ROOTS}-entry bound"
        )
    if requested.is_absolute():
        if search_roots:
            raise HostAdapterV3Error(
                "an absolute executable cannot be combined with search roots"
            )
        candidates = (requested,)
    else:
        if requested.name != text or text in {".", ".."}:
            raise HostAdapterV3Error(
                "relative executable discovery accepts one filename only"
            )
        if not search_roots:
            raise HostAdapterV3Error(
                "relative executable discovery requires explicit search roots"
            )
        normalized_roots: list[Path] = []
        for root in search_roots:
            if type(root) is not str and not isinstance(root, Path):
                raise HostAdapterV3Error(
                    "search roots must contain only explicit strings or Paths"
                )
            root_text = _safe_text(str(root))
            root_path = Path(root_text)
            if not root_path.is_absolute():
                raise HostAdapterV3Error(
                    "executable search roots must be explicit absolute paths"
                )
            normalized_roots.append(root_path)
        candidates = tuple(root / requested for root in normalized_roots)

    matches: list[Path] = []
    canonical_paths: set[str] = set()
    for raw in candidates:
        try:
            resolved = raw.resolve(strict=True)
            metadata = resolved.stat()
        except (OSError, RuntimeError):
            continue
        if isinstance(resolved, PureWindowsPath) and (
            resolved.suffix.lower() not in WINDOWS_EXECUTABLE_SUFFIXES
        ):
            continue
        if not stat.S_ISREG(metadata.st_mode) or not os.access(resolved, os.X_OK):
            continue
        canonical_path = str(resolved)
        if canonical_path not in canonical_paths:
            canonical_paths.add(canonical_path)
            matches.append(resolved)
    if not matches:
        raise HostAdapterV3Error(
            "no regular executable matched the explicit candidate boundary"
        )
    if len(matches) != 1:
        raise HostAdapterV3Error(
            "executable discovery is ambiguous across explicit search roots"
        )
    return matches[0]


def discover_executable(
    candidate: str | Path,
    *,
    search_roots: tuple[Path | str, ...] = (),
    maximum_bytes: int = MAX_EXECUTABLE_BYTES,
) -> dict[str, object]:
    """Measure one explicitly selected executable through a pinned descriptor."""

    if type(search_roots) is not tuple:
        raise HostAdapterV3Error("search_roots must be an exact bounded tuple")
    if (
        type(maximum_bytes) is not int
        or maximum_bytes < 1
        or maximum_bytes > MAX_EXECUTABLE_BYTES
    ):
        raise HostAdapterV3Error(
            f"maximum_bytes must be an integer from 1 through {MAX_EXECUTABLE_BYTES}"
        )
    resolved = _resolve_candidates(candidate, search_roots)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", getattr(os, "O_NDELAY", 0))
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(resolved, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise HostAdapterV3Error("resolved executable is not a regular file")
        permission_bits_required = not isinstance(resolved, PureWindowsPath)
        if permission_bits_required and not before.st_mode & (
            stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        ):
            raise HostAdapterV3Error(
                "resolved executable descriptor has no executable permission"
            )
        if not os.access(resolved, os.X_OK):
            raise HostAdapterV3Error(
                "resolved executable is not executable through the host API"
            )
        if before.st_size < 1 or before.st_size > maximum_bytes:
            raise HostAdapterV3Error(
                "resolved executable is outside the declared byte bound"
            )
        if isinstance(resolved, PureWindowsPath):
            header = os.read(descriptor, 2)
            if header != b"MZ":
                raise HostAdapterV3Error(
                    "resolved Windows executable lacks the required MZ header"
                )
            os.lseek(descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        consumed = 0
        while True:
            block = os.read(
                descriptor,
                min(1024 * 1024, maximum_bytes - consumed + 1),
            )
            if not block:
                break
            consumed += len(block)
            if consumed > maximum_bytes:
                raise HostAdapterV3Error(
                    "resolved executable grew beyond the declared byte bound"
                )
            digest.update(block)
        after = os.fstat(descriptor)
        descriptor_fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(
            getattr(before, field) != getattr(after, field)
            for field in descriptor_fields
        ):
            raise HostAdapterV3Error(
                "resolved executable changed while its bytes were measured"
            )
        current = resolved.lstat()
        path_identity_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        if not stat.S_ISREG(current.st_mode) or any(
            getattr(before, field) != getattr(current, field)
            for field in path_identity_fields
        ):
            raise HostAdapterV3Error(
                "resolved executable pathname no longer names the measured file"
            )
        if not os.access(resolved, os.X_OK):
            raise HostAdapterV3Error(
                "resolved executable is no longer executable after measurement"
            )
        final_descriptor = os.fstat(descriptor)
        if any(
            getattr(after, field) != getattr(final_descriptor, field)
            for field in descriptor_fields
        ):
            raise HostAdapterV3Error(
                "resolved executable changed during final pathname verification"
            )
        final_pathname = resolved.lstat()
        if not stat.S_ISREG(final_pathname.st_mode) or any(
            getattr(final_descriptor, field) != getattr(final_pathname, field)
            for field in path_identity_fields
        ):
            raise HostAdapterV3Error(
                "resolved executable namespace changed during final verification"
            )
        if consumed != before.st_size:
            raise HostAdapterV3Error(
                "resolved executable size changed while its bytes were measured"
            )
        record: dict[str, object] = {
            "format": MEASUREMENT_FORMAT,
            "measurement_id": "pending",
            "path_flavour": _path_flavour(resolved),
            "requested_name": Path(str(candidate)).name,
            "resolved_name": resolved.name,
            "resolved_uri": resolved.as_uri(),
            "size_bytes": consumed,
            "sha256": digest.hexdigest(),
            "executable_format": (
                "windows-pe-candidate"
                if isinstance(resolved, PureWindowsPath)
                else "posix-executable-file"
            ),
            "descriptor_identity": {
                "device": before.st_dev,
                "inode": before.st_ino,
                "mode": stat.S_IMODE(before.st_mode),
            },
        }
        record["measurement_id"] = compute_executable_measurement_id(record)
        return validate_executable_measurement(record)
    except OSError as exc:
        raise HostAdapterV3Error(
            f"executable measurement failed with {type(exc).__name__}"
        ) from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _discovery_row(
    measurement: dict[str, object] | None,
    limitation: str | None,
) -> dict[str, object]:
    if measurement is not None:
        return {
            "id": "executable-identity-discovery",
            "state": "available",
            "evidence": [measurement["measurement_id"]],
            "limitation": None,
            "next_safe_action": (
                "Bind this exact executable measurement to the selected package "
                "and remeasure immediately before process creation."
            ),
        }
    return {
        "id": "executable-identity-discovery",
        "state": "unavailable",
        "evidence": [],
        "limitation": limitation,
        "next_safe_action": (
            "Select one explicit regular executable or non-ambiguous explicit "
            "search-root tuple, then rerun Host Adapter V3 conformance."
        ),
    }


def _operation_rows(capabilities: list[dict[str, object]]) -> list[dict[str, object]]:
    states = {row["id"]: row["state"] for row in capabilities}
    rows: list[dict[str, object]] = []
    for operation_id, requirements in OPERATION_REQUIREMENTS:
        missing = [item for item in requirements if states[item] != "available"]
        rows.append(
            {
                "id": operation_id,
                "required_capability_ids": list(requirements),
                "state": "available" if not missing else "unavailable",
                "missing_capability_ids": missing,
            }
        )
    return rows


def inspect_local_host_adapter_v3(
    *,
    scratch_parent: Path | str | None = None,
) -> dict[str, object]:
    """Return one flat V3 diagnostic over the complete current vocabulary."""

    measurement: dict[str, object] | None
    limitation: str | None = None
    executable_locator = os.sys.executable
    try:
        if type(executable_locator) is not str or not executable_locator:
            raise HostAdapterV3Error(
                "running interpreter does not expose an explicit executable path"
            )
        before_measurement = discover_executable(Path(executable_locator))
    except HostAdapterV3Error as exc:
        before_measurement = None
        limitation = str(exc)
    adapter, base_capabilities = _inspect_base_capabilities(
        scratch_parent=scratch_parent
    )
    if before_measurement is None:
        measurement = None
    else:
        try:
            after_measurement = discover_executable(Path(executable_locator))
            if after_measurement != before_measurement:
                measurement = None
                limitation = (
                    "running executable identity changed across the V3 conformance probes"
                )
            else:
                measurement = after_measurement
        except HostAdapterV3Error as exc:
            measurement = None
            limitation = str(exc)

    discovery = _discovery_row(measurement, limitation)
    capabilities = [deepcopy(row) for row in base_capabilities]
    capabilities.insert(7, discovery)
    operation_states = _operation_rows(capabilities)
    core = next(row for row in operation_states if row["id"] == "core-host")
    receipt: dict[str, object] = {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "receipt_id": "pending",
        "adapter": adapter,
        "active_executable": measurement,
        "capabilities": capabilities,
        "operation_states": operation_states,
        "core_host_eligible": core["state"] == "available",
        "missing_core_capabilities": core["missing_capability_ids"],
        "release_qualified": False,
        "qualification_limitation": (
            "This flat disposable V3 receipt demonstrates local mechanics only; "
            "it is not packaged-artifact, profile, recovery, "
            "performance, cross-host, or release qualification evidence."
        ),
        "next_safe_action": (
            "Bind Host Adapter V3, its operation requirements, and the exact "
            "executable measurement into the independently installable core."
        ),
    }
    receipt["receipt_id"] = compute_host_adapter_v3_receipt_id(receipt)
    return validate_host_adapter_v3_receipt(receipt)


def _exact_keys(value: object, expected: set[str], path: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != expected:
        raise HostAdapterV3Error(f"{path}: must contain exact fields {sorted(expected)}")
    return value


def validate_executable_measurement(value: object) -> dict[str, object]:
    record = _exact_keys(
        value,
        {
            "format",
            "measurement_id",
            "path_flavour",
            "requested_name",
            "resolved_name",
            "resolved_uri",
            "size_bytes",
            "sha256",
            "executable_format",
            "descriptor_identity",
        },
        "/active_executable",
    )
    if record["format"] != MEASUREMENT_FORMAT:
        raise HostAdapterV3Error("/active_executable/format: unsupported format")
    for key in (
        "measurement_id",
        "resolved_name",
        "requested_name",
        "resolved_uri",
        "sha256",
        "executable_format",
    ):
        _safe_text(record[key])
    flavour = record["path_flavour"]
    if type(flavour) is not str or flavour not in {"posix", "windows"}:
        raise HostAdapterV3Error(
            "/active_executable/path_flavour: must be posix or windows"
        )
    for field in ("requested_name", "resolved_name"):
        filename = record[field]
        if filename in {".", ".."} or "/" in filename or "\\" in filename:
            raise HostAdapterV3Error(
                f"/active_executable/{field}: must be one filename"
            )
    resolved_uri = record["resolved_uri"]
    try:
        resolved_path = _file_uri_to_path(resolved_uri, flavour=flavour)
    except (TypeError, ValueError) as exc:
        raise HostAdapterV3Error(
            "/active_executable/resolved_uri: must be one canonical absolute file URI"
        ) from exc
    if resolved_path.name != record["resolved_name"]:
        raise HostAdapterV3Error(
            "/active_executable: resolved filename and URI differ"
        )
    if flavour == "windows":
        if (
            resolved_path.suffix.lower() not in WINDOWS_EXECUTABLE_SUFFIXES
            or record["executable_format"] != "windows-pe-candidate"
        ):
            raise HostAdapterV3Error(
                "/active_executable/executable_format: Windows measurement must retain the bounded PE-candidate observation"
            )
    elif record["executable_format"] != "posix-executable-file":
        raise HostAdapterV3Error(
            "/active_executable/executable_format: POSIX measurement format differs"
        )
    if (
        type(record["size_bytes"]) is not int
        or not 1 <= record["size_bytes"] <= MAX_EXECUTABLE_BYTES
    ):
        raise HostAdapterV3Error("/active_executable/size_bytes: outside V3 bound")
    if (
        type(record["sha256"]) is not str
        or len(record["sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in record["sha256"])
    ):
        raise HostAdapterV3Error("/active_executable/sha256: invalid digest")
    identity = _exact_keys(
        record["descriptor_identity"],
        {"device", "inode", "mode"},
        "/active_executable/descriptor_identity",
    )
    if any(
        type(identity[key]) is not int
        or identity[key] < 0
        or identity[key] > MAX_DESCRIPTOR_IDENTITY
        for key in ("device", "inode")
    ):
        raise HostAdapterV3Error(
            "/active_executable/descriptor_identity: device and inode must be bounded nonnegative integers"
        )
    if type(identity["mode"]) is not int or identity["mode"] < 0:
        raise HostAdapterV3Error(
            "/active_executable/descriptor_identity/mode: must be a nonnegative integer"
        )
    if identity["mode"] > 0o7777:
        raise HostAdapterV3Error(
            "/active_executable/descriptor_identity/mode: outside portable permission bounds"
        )
    if flavour == "posix" and not identity["mode"] & (
        stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    ):
        raise HostAdapterV3Error(
            "/active_executable/descriptor_identity/mode: POSIX measurement must retain executable permission"
        )
    if record["measurement_id"] != compute_executable_measurement_id(record):
        raise HostAdapterV3Error("/active_executable/measurement_id: identity mismatch")
    return deepcopy(record)


def validate_host_adapter_v3_receipt(value: object) -> dict[str, object]:
    receipt = _exact_keys(
        value,
        {
            "format",
            "schema_version",
            "receipt_id",
            "adapter",
            "active_executable",
            "capabilities",
            "operation_states",
            "core_host_eligible",
            "missing_core_capabilities",
            "release_qualified",
            "qualification_limitation",
            "next_safe_action",
        },
        "/",
    )
    if (
        receipt["format"] != FORMAT
        or type(receipt["schema_version"]) is not int
        or receipt["schema_version"] != SCHEMA_VERSION
    ):
        raise HostAdapterV3Error("/: unsupported Host Adapter V3 receipt")
    for key in ("receipt_id", "qualification_limitation", "next_safe_action"):
        _safe_text(receipt[key])
    adapter = _exact_keys(
        receipt["adapter"],
        {
            "format",
            "adapter_id",
            "path_flavour",
            "python_implementation",
            "python_version",
            "system_label",
            "system_release",
            "machine_label",
        },
        "/adapter",
    )
    if adapter["format"] != ADAPTER_FORMAT or adapter["adapter_id"] != ADAPTER_ID:
        raise HostAdapterV3Error("/adapter: unexpected V3 adapter identity")
    if (
        type(adapter["path_flavour"]) is not str
        or adapter["path_flavour"] not in {"posix", "windows"}
    ):
        raise HostAdapterV3Error("/adapter/path_flavour: unsupported local path semantics")
    for key in (
        "python_implementation",
        "python_version",
        "system_label",
        "system_release",
        "machine_label",
    ):
        _safe_text(adapter[key])

    measurement = (
        None
        if receipt["active_executable"] is None
        else validate_executable_measurement(receipt["active_executable"])
    )
    if (
        measurement is not None
        and measurement["path_flavour"] != adapter["path_flavour"]
    ):
        raise HostAdapterV3Error(
            "/active_executable/path_flavour: differs from the measured adapter boundary"
        )
    capabilities = receipt["capabilities"]
    if type(capabilities) is not list or len(capabilities) != len(CAPABILITY_IDS):
        raise HostAdapterV3Error("/capabilities: must contain the complete V3 vocabulary")
    ids = [row.get("id") if type(row) is dict else None for row in capabilities]
    if ids != list(CAPABILITY_IDS):
        raise HostAdapterV3Error("/capabilities: IDs are not complete and canonical")
    for index, candidate in enumerate(capabilities):
        path = f"/capabilities/{index}"
        row = _exact_keys(
            candidate,
            {"id", "state", "evidence", "limitation", "next_safe_action"},
            path,
        )
        _safe_text(row["id"])
        state = row["state"]
        if type(state) is not str or state not in CAPABILITY_STATES:
            raise HostAdapterV3Error(f"{path}/state: unknown capability state")
        evidence = row["evidence"]
        if type(evidence) is not list or len(evidence) > 16:
            raise HostAdapterV3Error(f"{path}/evidence: must be a bounded array")
        for item in evidence:
            _safe_text(item)
        limitation = row["limitation"]
        if limitation is not None:
            _safe_text(limitation)
        if state == "available" and (not evidence or limitation is not None):
            raise HostAdapterV3Error(
                f"{path}: available requires evidence and no limitation"
            )
        if state != "available" and (evidence or limitation is None):
            raise HostAdapterV3Error(
                f"{path}: unavailable or unverified requires one limitation and no evidence"
            )
        _safe_text(row["next_safe_action"])
    discovery = capabilities[7]
    if measurement is None:
        if (
            discovery["state"] != "unavailable"
            or discovery["evidence"] != []
            or type(discovery["limitation"]) is not str
        ):
            raise HostAdapterV3Error(
                "/capabilities/7: missing measurement must remain unavailable"
            )
        _safe_text(discovery["limitation"])
    elif (
        discovery["state"] != "available"
        or discovery["evidence"] != [measurement["measurement_id"]]
        or discovery["limitation"] is not None
    ):
        raise HostAdapterV3Error(
            "/capabilities/7: executable evidence does not bind the measurement"
        )
    _safe_text(discovery["next_safe_action"])

    expected_operations = _operation_rows(capabilities)
    if receipt["operation_states"] != expected_operations:
        raise HostAdapterV3Error("/operation_states: derived operation state mismatch")
    core = expected_operations[0]
    if (
        type(receipt["core_host_eligible"]) is not bool
        or receipt["core_host_eligible"] != (core["state"] == "available")
        or receipt["missing_core_capabilities"] != core["missing_capability_ids"]
    ):
        raise HostAdapterV3Error("/core_host_eligible: derived core state mismatch")
    if receipt["release_qualified"] is not False:
        raise HostAdapterV3Error("/release_qualified: V3 diagnostic cannot qualify release")
    if receipt["receipt_id"] != compute_host_adapter_v3_receipt_id(receipt):
        raise HostAdapterV3Error("/receipt_id: content identity mismatch")
    return deepcopy(receipt)


__all__ = [
    "ADAPTER_ID",
    "CAPABILITY_IDS",
    "CORE_HOST_CAPABILITY_IDS",
    "HostAdapterV3Error",
    "OPERATION_REQUIREMENTS",
    "compute_executable_measurement_id",
    "compute_host_adapter_v3_receipt_id",
    "discover_executable",
    "inspect_local_host_adapter_v3",
    "validate_executable_measurement",
    "validate_host_adapter_v3_receipt",
]
