"""Core port for a restartable, long-lived client process scope.

An exited launcher or process group cannot establish descendant absence. A
lease must bind containment before the owner changes durable client inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
from typing import Literal, Protocol, Sequence


class LongLivedProcessError(RuntimeError):
    """Core could not provide a restartable process scope."""


@dataclass(frozen=True, slots=True)
class ProcessAbsence:
    state: Literal["active", "absent", "unknown"]
    reason: str


@dataclass(frozen=True, slots=True)
class LongLivedProcessRequest:
    session_id: str
    host_os: Literal["linux", "windows"]
    instance_root: Path
    cwd: Path
    instance_device: int
    instance_inode: int
    launch_receipt_sha256: str
    command: tuple[str, ...]


class LongLivedProcessLease(Protocol):
    """A retained scope whose launch and absence observation stay in Core.

    Closing a local handle must not erase an active or unknown retained scope.
    """

    def launch(self) -> subprocess.Popen[bytes]: ...

    def observe_absence(self) -> ProcessAbsence: ...

    def close(self) -> None: ...


class LongLivedProcessHost(Protocol):
    def reserve(self, request: LongLivedProcessRequest) -> LongLivedProcessLease: ...


_host: LongLivedProcessHost | None = None


def bind_long_lived_process_host(host: LongLivedProcessHost) -> None:
    global _host
    if not callable(getattr(host, "reserve", None)):
        raise LongLivedProcessError("long-lived process host lacks reserve")
    if _host is not None and _host is not host:
        raise LongLivedProcessError("a different long-lived process host is already bound")
    _host = host


def reserve_long_lived_process(request: LongLivedProcessRequest) -> LongLivedProcessLease:
    """Require Core containment before an owner applies launch-time overlays."""

    if (not isinstance(request, LongLivedProcessRequest)
            or not request.session_id or len(request.session_id) > 512
            or request.host_os not in {"linux", "windows"}
            or not isinstance(request.instance_root, Path)
            or not request.instance_root.is_absolute()
            or not isinstance(request.cwd, Path) or not request.cwd.is_absolute()
            or type(request.instance_device) is not int or request.instance_device < 0
            or type(request.instance_inode) is not int or request.instance_inode <= 0
            or re.fullmatch(r"sha256:[0-9a-f]{64}", request.launch_receipt_sha256) is None
            or not request.command or len(request.command) > 256
            or any(type(part) is not str or not part or "\x00" in part
                   or len(part) > 16384 for part in request.command)):
        raise LongLivedProcessError("long-lived process request is invalid")
    if _host is None:
        raise LongLivedProcessError("no Core long-lived process host is bound")
    lease = _host.reserve(request)
    if (not callable(getattr(lease, "launch", None))
            or not callable(getattr(lease, "observe_absence", None))
            or not callable(getattr(lease, "close", None))):
        raise LongLivedProcessError("Core returned an incomplete process lease")
    return lease


def observe_process_absence(lease: LongLivedProcessLease) -> ProcessAbsence:
    """Only an exact Core scope may return ``absent``."""

    observed = lease.observe_absence()
    if (not isinstance(observed, ProcessAbsence)
            or observed.state not in {"active", "absent", "unknown"}
            or type(observed.reason) is not str or not observed.reason
            or len(observed.reason) > 1024
            or any(character in observed.reason for character in "\r\n\x00")):
        raise LongLivedProcessError("Core returned an invalid absence observation")
    return observed
