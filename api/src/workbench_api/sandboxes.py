"""Host-owned isolation selection for source-evaluating modules."""

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Iterator, Protocol


@dataclass(frozen=True)
class WorkerSandboxSelection:
    backend: str
    policy: str
    jvm_arguments: tuple[str, ...]
    session_id: str | None = None


class SandboxHost(Protocol):
    def open_axiom(self, backend: str, *, state_root: Path, cancelled: Event) -> WorkerSandboxSelection: ...
    def close_axiom(self, selection: WorkerSandboxSelection) -> None: ...


_host: SandboxHost | None = None


def bind_sandbox_host(host: SandboxHost) -> None:
    global _host
    if not callable(getattr(host, "open_axiom", None)) or not callable(getattr(host, "close_axiom", None)):
        raise ValueError("sandbox host must implement launch selection and cleanup")
    if _host is not None and _host is not host:
        raise ValueError("a different sandbox host is already bound")
    _host = host


@contextmanager
def axiom_worker_sandbox(backend: str | None, *, state_root: Path,
                         cancelled: Event) -> Iterator[WorkerSandboxSelection]:
    selected = "bubblewrap" if backend is None else backend
    if selected == "bubblewrap":
        yield WorkerSandboxSelection("bubblewrap", "axiom.bubblewrap-worker.v1", ())
        return
    if selected not in {"docker", "gvisor"}:
        raise ValueError("unknown Axiom worker sandbox backend")
    if _host is None:
        raise ValueError("no Core sandbox host is bound")
    selection = _host.open_axiom(selected, state_root=state_root, cancelled=cancelled)
    try:
        yield selection
    finally:
        _host.close_axiom(selection)
