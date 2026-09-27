"""Core custody for stable, mutable working directories and terminal evidence.

An allocation reservation names the final path before it is created. Native
tools work at that path, so later reports and handoffs never need relocation.
Only owner-selected evidence files are hashed at closure; the full allocation
remains protected from collection, including interrupted and failed runs.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import BinaryIO, Callable, Iterator, Mapping
from uuid import uuid4

from workbench_api.host_filesystem import DurableRecordError
from workbench_api.working_allocations import (
    WorkingAllocationDescription, WorkingAllocationError, WorkingAllocationReference,
)

from . import check_storage
from .durable_records import private_record_lock, publish_immutable_bytes, read_private_single_link_bytes
from .host_filesystem import fsync_directory, private_path, secure_private_path
from .output_routing import _WINDOWS_RESERVED, _private_directory
from .setup_cli import _workspace
from .storage.registered import ResourceCatalog


RESERVATION_KIND = "workbench-working-allocation-reservation-v1"
ACTIVATION_KIND = "workbench-working-allocation-activation-v1"
TERMINAL_KIND = "workbench-working-allocation-terminal-v1"
CANCELLATION_KIND = "workbench-working-allocation-cancellation-v1"
MARKER_KIND = "workbench-working-allocation-marker-v1"
RETENTION = "protected-until-reviewed-policy"
_ID = re.compile(r"workbench-working-allocation-v1:([0-9a-f]{32})\Z")
_OWNER = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RECORD_LIMIT = 4 * 1024 * 1024
_MAX_EVIDENCE = 256
_MAX_REFERENCES = 256
_MAX_EVIDENCE_BYTES = 2 * 1024**3
_CATALOG_CHILDREN = frozenset({
    "reservations", "activations", "terminals", "cancellations", "leases", "path-leases",
})
_RECORD_NAME = re.compile(r"([0-9a-f]{32})\.json\Z")
_LEASE_NAME = re.compile(r"([0-9a-f]{32})(?:\.transition)?\.lock\Z")
_PATH_LEASE_NAME = re.compile(r"[0-9a-f]{64}\.lock\Z")
_active_allocations: ContextVar[frozenset[str]] = ContextVar(
    "workbench_active_working_allocations", default=frozenset(),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _nonce(allocation_id: str) -> str:
    match = _ID.fullmatch(allocation_id) if isinstance(allocation_id, str) else None
    if match is None:
        raise WorkingAllocationError("working.id", "select an exact working-allocation ID")
    return match.group(1)


def _safe_name(value: str, *, label: str) -> None:
    if (type(value) is not str or _NAME.fullmatch(value) is None
            or value.split(".", 1)[0].upper() in _WINDOWS_RESERVED):
        raise WorkingAllocationError("working.path", f"working-allocation {label} is invalid")


def _sealed(kind: str, body: Mapping[str, object]) -> dict:
    return check_storage.seal(kind, dict(body))


class WorkingAllocationCatalog:
    """Append-only working records beneath the existing Core resource root.

    ``inventory_rows`` is the adapter for ResourceCatalog.inventory and cleanup
    integration. Until those shared consumers include it, domain use is gated.
    """

    def __init__(self, configuration_home: Path):
        self.resources = ResourceCatalog(configuration_home)
        self.root = self.resources.root / "working-allocations"

    def _directory(self, name: str) -> Path:
        return self.root / name

    def _path(self, name: str, nonce: str) -> Path:
        return self._directory(name) / f"{nonce}.json"

    def ensure(self) -> None:
        self.resources._ensure()
        for path in (self.root, *(self._directory(name) for name in (
            "reservations", "activations", "terminals", "cancellations", "leases", "path-leases",
        ))):
            _private_directory(path)
            secure_private_path(path, directory=True)

    def _write(self, name: str, nonce: str, kind: str, body: Mapping[str, object]) -> dict:
        record = _sealed(kind, body)
        publish_immutable_bytes(
            self._path(name, nonce), check_storage.canonical(record) + b"\n",
            byte_limit=_RECORD_LIMIT,
        )
        return record

    def _read(self, name: str, nonce: str, kind: str) -> dict:
        path = self._path(name, nonce)
        try:
            if not private_path(path, directory=False):
                raise WorkingAllocationError("working.changed", "working-allocation record lost private custody")
            row = check_storage.read_json(path, byte_limit=_RECORD_LIMIT)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            if isinstance(exc, WorkingAllocationError):
                raise
            raise WorkingAllocationError("working.unavailable", f"working-allocation {name} is unavailable") from exc
        if (not isinstance(row, dict) or row != _sealed(kind, {k: v for k, v in row.items() if k != "id"})):
            raise WorkingAllocationError("working.changed", f"working-allocation {name} changed")
        return row

    def reservation(self, allocation_id: str) -> dict:
        nonce = _nonce(allocation_id)
        row = self._read("reservations", nonce, RESERVATION_KIND)
        required = {
            "id", "format", "allocation_id", "family", "label", "owner_id", "workspace",
            "path", "store_root", "role", "role_source", "policy_id", "retention",
            "parent_device", "parent_inode", "allocated_at",
        }
        if (set(row) != required or row["format"] != RESERVATION_KIND
                or row["allocation_id"] != allocation_id or row["role"] != "evidence"
                or row["retention"] != RETENTION
                or not isinstance(row["family"], str) or _OWNER.fullmatch(row["family"]) is None
                or not isinstance(row["owner_id"], str) or _OWNER.fullmatch(row["owner_id"]) is None
                or not isinstance(row["label"], str) or _NAME.fullmatch(row["label"]) is None
                or not isinstance(row["workspace"], str) or not Path(row["workspace"]).is_absolute()
                or not isinstance(row["path"], str) or not Path(row["path"]).is_absolute()
                or ".." in Path(row["path"]).parts
                or Path(row["path"]).name != row["label"]
                or not isinstance(row["store_root"], str) or not Path(row["store_root"]).is_absolute()
                or ".." in Path(row["store_root"]).parts
                or not Path(row["path"]).is_relative_to(Path(row["store_root"]))
                or not isinstance(row["role_source"], str)
                or row["policy_id"] is not None and not isinstance(row["policy_id"], str)
                or type(row["parent_device"]) is not int or type(row["parent_inode"]) is not int):
            raise WorkingAllocationError("working.changed", "working-allocation reservation identity changed")
        return row

    def activation(self, allocation_id: str) -> dict:
        nonce = _nonce(allocation_id)
        row = self._read("activations", nonce, ACTIVATION_KIND)
        reservation = self.reservation(allocation_id)
        if (set(row) != {"id", "format", "allocation_id", "reservation_id", "device", "inode", "activated_at"}
                or row["format"] != ACTIVATION_KIND or row["allocation_id"] != allocation_id
                or row["reservation_id"] != reservation["id"]
                or type(row["device"]) is not int or type(row["inode"]) is not int):
            raise WorkingAllocationError("working.changed", "working-allocation activation changed")
        return row

    def terminal(self, allocation_id: str) -> dict | None:
        nonce = _nonce(allocation_id)
        path = self._path("terminals", nonce)
        if not path.exists() and not path.is_symlink():
            return None
        row = self._read("terminals", nonce, TERMINAL_KIND)
        activation = self.activation(allocation_id)
        root = Path(self.reservation(allocation_id)["path"])
        if (set(row) != {"id", "format", "allocation_id", "activation_id", "outcome", "evidence",
                         "absolute_references", "failure", "finished_at", "remaining_contents"}
                or row["format"] != TERMINAL_KIND or row["allocation_id"] != allocation_id
                or row["activation_id"] != activation["id"]
                or not isinstance(row["outcome"], str)
                or row["outcome"] not in {"complete", "failed"}
                or row["remaining_contents"] != "mutable-disposable-retained-until-review"
                or not isinstance(row["evidence"], list) or len(row["evidence"]) > _MAX_EVIDENCE
                or not isinstance(row["absolute_references"], list)
                or len(row["absolute_references"]) > _MAX_REFERENCES
                or row["failure"] is not None and (
                    not isinstance(row["failure"], str) or len(row["failure"]) > 4096
                )
                or row["outcome"] == "complete" and row["failure"] is not None
                or row["outcome"] == "failed" and not row["failure"]
                or not _valid_selected_rows(root, row["evidence"], row["absolute_references"])):
            raise WorkingAllocationError("working.changed", "working-allocation terminal record changed")
        return row

    def cancellation(self, allocation_id: str) -> dict | None:
        nonce = _nonce(allocation_id)
        path = self._path("cancellations", nonce)
        if not path.exists() and not path.is_symlink():
            return None
        row = self._read("cancellations", nonce, CANCELLATION_KIND)
        if (set(row) != {"id", "format", "allocation_id", "reservation_id", "binding"}
                or row["format"] != CANCELLATION_KIND
                or row["allocation_id"] != allocation_id
                or row["reservation_id"] != self.reservation(allocation_id)["id"]
                or not isinstance(row["binding"], str)
                or not row["binding"] or len(row["binding"]) > 256):
            raise WorkingAllocationError("working.changed", "working-allocation cancellation changed")
        return row

    def reference(self, allocation_id: str) -> WorkingAllocationReference:
        row = self.reservation(allocation_id)
        return WorkingAllocationReference(
            allocation_id, row["family"], row["label"], Path(row["path"]),
            Path(row["workspace"]), row["owner_id"],
        )

    def _read_marker(self, path: Path, allocation_id: str) -> dict:
        marker = path / ".workbench-allocation.json"
        if not private_path(marker, directory=False):
            raise WorkingAllocationError("working.changed", "working-allocation marker lost private custody")
        try:
            row = check_storage.read_json(marker, byte_limit=1024 * 1024)
        except (OSError, ValueError) as exc:
            raise WorkingAllocationError("working.changed", "working-allocation marker is unavailable") from exc
        if (not isinstance(row, dict)
                or set(row) != {"id", "format", "allocation_id", "reservation_id"}
                or row["format"] != MARKER_KIND or row["allocation_id"] != allocation_id
                or row != _sealed(MARKER_KIND, {key: value for key, value in row.items() if key != "id"})):
            raise WorkingAllocationError("working.changed", "working-allocation marker changed")
        return row

    def describe(self, allocation_id: str) -> WorkingAllocationDescription:
        reference = self.reference(allocation_id)
        nonce = _nonce(allocation_id)
        activation_path = self._path("activations", nonce)
        activated = activation_path.exists() or activation_path.is_symlink()
        terminal = self.terminal(allocation_id) if activated else None
        if not reference.path.exists() and not reference.path.is_symlink():
            status = "missing" if activated else "reserved"
        elif not activated:
            status = "incomplete"
        else:
            identity = self.activation(allocation_id)
            try:
                path = check_storage.ordinary(reference.path, directory=True)
                parent = check_storage.ordinary(path.parent, directory=True)
                reservation = self.reservation(allocation_id)
                info = path.stat()
                stable = (info.st_dev, info.st_ino) == (identity["device"], identity["inode"])
                parent_info = parent.stat()
                stable = stable and (parent_info.st_dev, parent_info.st_ino) == (
                    reservation["parent_device"], reservation["parent_inode"],
                )
                stable = stable and private_path(path, directory=True)
                stable = stable and self._read_marker(path, allocation_id)["reservation_id"] == reservation["id"]
            except (OSError, ValueError):
                stable = False
            status = (terminal["outcome"] if terminal is not None else "incomplete") if stable else "changed"
        return WorkingAllocationDescription(
            reference=reference, status=status, retention=RETENTION,
            evidence=tuple(terminal["evidence"]) if terminal is not None else (),
            absolute_references=tuple(terminal["absolute_references"]) if terminal is not None else (),
            failure=terminal["failure"] if terminal is not None else None,
        )

    @staticmethod
    def _inventory_directory(path: Path) -> Path:
        try:
            directory = check_storage.ordinary(path, directory=True)
        except (OSError, ValueError) as exc:
            raise WorkingAllocationError("working.changed", "working-allocation catalog directory is not ordinary") from exc
        if not private_path(directory, directory=True):
            raise WorkingAllocationError("working.changed", "working-allocation catalog directory lost private custody")
        return directory

    @staticmethod
    def _inventory_file(path: Path, *, label: str, empty: bool = False) -> None:
        try:
            if empty:
                read_private_single_link_bytes(path, byte_limit=0)
            else:
                check_storage.ordinary(path)
        except (OSError, ValueError) as exc:
            raise WorkingAllocationError("working.changed", f"working-allocation {label} lost private custody") from exc
        if not private_path(path, directory=False):
            raise WorkingAllocationError("working.changed", f"working-allocation {label} lost private custody")

    def _inventory_children(self) -> list[Path]:
        """Check all present-day catalog children before a workspace is selected."""
        root = self._inventory_directory(self.root)
        if {path.name for path in root.iterdir()} != _CATALOG_CHILDREN:
            raise WorkingAllocationError("working.changed", "working-allocation catalog has an unknown or missing child")
        children: dict[str, list[Path]] = {}
        for name in sorted(_CATALOG_CHILDREN):
            directory = self._inventory_directory(self._directory(name))
            children[name] = sorted(directory.iterdir())

        reservations: set[str] = set()
        for path in children["reservations"]:
            match = _RECORD_NAME.fullmatch(path.name)
            if match is None:
                raise WorkingAllocationError("working.changed", "working-allocation catalog has an invalid reservation")
            self._inventory_file(path, label="reservation")
            reservations.add(match.group(1))
        for name in ("activations", "terminals", "cancellations"):
            for path in children[name]:
                match = _RECORD_NAME.fullmatch(path.name)
                if match is None or match.group(1) not in reservations:
                    raise WorkingAllocationError("working.changed", "working-allocation catalog has an orphan record")
                self._inventory_file(path, label=name)
        for path in children["leases"]:
            match = _LEASE_NAME.fullmatch(path.name)
            if match is None or match.group(1) not in reservations:
                raise WorkingAllocationError("working.changed", "working-allocation catalog has an orphan lease")
            self._inventory_file(path, label="lease", empty=True)
        for path in children["path-leases"]:
            if _PATH_LEASE_NAME.fullmatch(path.name) is None:
                raise WorkingAllocationError("working.changed", "working-allocation catalog has an invalid path lease")
            # Allocation acquires this lease before publishing a reservation.
            self._inventory_file(path, label="path lease", empty=True)

        for path in children["activations"]:
            self.activation(f"workbench-working-allocation-v1:{path.stem}")
        for path in children["terminals"]:
            if self.terminal(f"workbench-working-allocation-v1:{path.stem}") is None:
                raise WorkingAllocationError("working.changed", "working-allocation terminal disappeared")
        for path in children["cancellations"]:
            if self.cancellation(f"workbench-working-allocation-v1:{path.stem}") is None:
                raise WorkingAllocationError("working.changed", "working-allocation cancellation disappeared")
        return children["reservations"]

    def inventory_rows(self, *, workspace: Path | None = None) -> list[dict[str, object]]:
        """Expose every registered root so Core cleanup can protect it wholesale."""
        if not self.root.exists() and not self.root.is_symlink():
            return []
        reservations = self._inventory_children()
        rows = []
        for path in reservations:
            description = self.describe(f"workbench-working-allocation-v1:{path.stem}")
            ref = description.reference
            if workspace is not None and ref.workspace != workspace:
                continue
            rows.append({
                "allocation_id": ref.allocation_id, "family": ref.family, "label": ref.label,
                "owner_id": ref.owner_id, "workspace": str(ref.workspace), "path": str(ref.path),
                "status": description.status, "retention": description.retention,
                "evidence": list(description.evidence),
                "absolute_references": list(description.absolute_references),
                "failure": description.failure,
            })
        return rows


class CoreWorkingAllocations:
    def __init__(
        self, *, workspace: Path, configuration_home: Path, locations: Mapping[str, Path],
        owner_id: str, policy_id: str | None = None,
        location_sources: Mapping[str, str] | None = None,
        check_cancelled: Callable[[], None] = lambda: None,
    ):
        if not isinstance(workspace, Path) or not workspace.is_absolute():
            raise WorkingAllocationError("working.policy", "working-allocation workspace must be absolute")
        if not isinstance(configuration_home, Path) or not configuration_home.is_absolute():
            raise WorkingAllocationError("working.policy", "working-allocation configuration home must be absolute")
        if not isinstance(owner_id, str) or _OWNER.fullmatch(owner_id) is None:
            raise WorkingAllocationError("working.policy", "working-allocation owner is invalid")
        try:
            self.workspace = _workspace(workspace)
        except ValueError as exc:
            raise WorkingAllocationError("working.policy", f"workspace is unavailable: {exc}") from exc
        self.locations = dict(locations)
        self.owner_id = owner_id
        self.policy_id = policy_id
        self.location_sources = dict(location_sources or {})
        self._check_dispatch_cancelled = check_cancelled
        self.catalog = WorkingAllocationCatalog(configuration_home)

    def _destination(self, family: str, label: str, requested_path: Path | None) -> tuple[Path, Path, str]:
        if not isinstance(family, str) or _OWNER.fullmatch(family) is None:
            raise WorkingAllocationError("working.path", "working-allocation family is invalid")
        _safe_name(label, label="label")
        evidence = self.locations.get("evidence")
        if (not isinstance(evidence, Path) or not evidence.is_absolute()
                or ".." in evidence.parts):
            raise WorkingAllocationError("working.policy", "Core did not select an evidence store")
        if requested_path is None:
            workspace_key = sha256(os.fsencode(os.path.normcase(str(self.workspace)))).hexdigest()
            target = evidence / "working-allocations" / f"workspace-{workspace_key}" / self.owner_id / family / label
            source = self.location_sources.get("evidence", "context")
            store_root = evidence
        else:
            if (not isinstance(requested_path, Path) or requested_path.name != label
                    or ".." in requested_path.parts):
                raise WorkingAllocationError("working.path", "working-allocation requested path is invalid")
            target = requested_path if requested_path.is_absolute() else self.workspace / requested_path
            if target.is_relative_to(evidence):
                store_root, source = evidence, self.location_sources.get("evidence", "context")
            elif target.is_relative_to(self.workspace / ".workbench"):
                store_root, source = self.workspace, "explicit-workspace"
            else:
                raise WorkingAllocationError(
                    "working.path", "working-allocation path is outside Core-selected stores and workspace",
                )
        if target.is_relative_to(self.catalog.resources.root):
            raise WorkingAllocationError("working.path", "working-allocation path overlaps Core catalog")
        for ancestor in (target, *target.parents):
            if ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)():
                raise WorkingAllocationError("working.path", "working-allocation path traverses a redirect")
        if target.exists() or target.is_symlink():
            raise WorkingAllocationError("working.exists", "working-allocation path already exists")
        return target, store_root, source

    def allocate(
        self, family: str, label: str, *, requested_path: Path | None = None,
    ) -> WorkingAllocationReference:
        target, store_root, source = self._destination(family, label, requested_path)
        self._check_dispatch_cancelled()
        self.catalog.ensure()
        path_key = sha256(os.fsencode(target)).hexdigest()
        with private_record_lock(self.catalog._directory("path-leases") / f"{path_key}.lock"):
            if target.exists() or target.is_symlink():
                raise WorkingAllocationError("working.exists", "working-allocation path already exists")
            _private_directory(target.parent)
            parent = check_storage.ordinary(target.parent, directory=True)
            if not private_path(parent, directory=True):
                raise WorkingAllocationError(
                    "working.unsafe", "working-allocation parent is not owner-private; on WSL use a Linux filesystem when the selected mount cannot enforce private ownership",
                )
            parent_info = parent.stat()
            nonce = uuid4().hex
            allocation_id = f"workbench-working-allocation-v1:{nonce}"
            reservation = self.catalog._write("reservations", nonce, RESERVATION_KIND, {
                "format": RESERVATION_KIND, "allocation_id": allocation_id,
                "family": family, "label": label, "owner_id": self.owner_id,
                "workspace": str(self.workspace), "path": str(target),
                "store_root": str(store_root), "role": "evidence", "role_source": source,
                "policy_id": self.policy_id, "retention": RETENTION,
                "parent_device": parent_info.st_dev, "parent_inode": parent_info.st_ino,
                "allocated_at": _now(),
            })
            self._check_dispatch_cancelled()
            try:
                target.mkdir(mode=0o700)
            except FileExistsError as exc:
                raise WorkingAllocationError("working.exists", "working-allocation path already exists") from exc
            secure_private_path(target, directory=True)
            fsync_directory(parent)
            marker = _sealed(MARKER_KIND, {
                "format": MARKER_KIND, "allocation_id": allocation_id,
                "reservation_id": reservation["id"],
            })
            publish_immutable_bytes(
                target / ".workbench-allocation.json", check_storage.canonical(marker) + b"\n",
                byte_limit=1024 * 1024,
            )
            info = check_storage.ordinary(target, directory=True).stat()
            self.catalog._write("activations", nonce, ACTIVATION_KIND, {
                "format": ACTIVATION_KIND, "allocation_id": allocation_id,
                "reservation_id": reservation["id"], "device": info.st_dev,
                "inode": info.st_ino, "activated_at": _now(),
            })
            return self.catalog.reference(allocation_id)

    def _checked(self, allocation: WorkingAllocationReference) -> WorkingAllocationReference:
        if not isinstance(allocation, WorkingAllocationReference):
            raise WorkingAllocationError("working.id", "working-allocation reference is invalid")
        expected = self.catalog.reference(allocation.allocation_id)
        if (allocation != expected or expected.owner_id != self.owner_id
                or expected.workspace != self.workspace):
            raise WorkingAllocationError("working.policy", "working allocation is outside this Core host")
        activation = self.catalog.activation(allocation.allocation_id)
        path = check_storage.ordinary(expected.path, directory=True)
        parent = check_storage.ordinary(path.parent, directory=True)
        reservation = self.catalog.reservation(allocation.allocation_id)
        info = path.stat()
        parent_info = parent.stat()
        if ((info.st_dev, info.st_ino) != (activation["device"], activation["inode"])
                or (parent_info.st_dev, parent_info.st_ino) != (
                    reservation["parent_device"], reservation["parent_inode"],
                )
                or not private_path(path, directory=True)):
            raise WorkingAllocationError("working.changed", "working-allocation path changed identity or custody")
        marker = self.catalog._read_marker(expected.path, allocation.allocation_id)
        if marker["reservation_id"] != reservation["id"]:
            raise WorkingAllocationError("working.changed", "working-allocation marker changed binding")
        return expected

    def open(self, allocation_id: str) -> WorkingAllocationReference:
        return self._checked(self.catalog.reference(allocation_id))

    @contextmanager
    def create_once_stream(
        self, allocation: WorkingAllocationReference, selected_path: Path, *,
        expected_family: str,
    ) -> Iterator[BinaryIO]:
        """Stream into a fresh private child file of an exact working allocation.

        The owner chooses an existing immediate child directory and final name.
        Core rechecks the allocation and opens the file exclusively, so a failed
        or interrupted writer leaves its partial bytes at the original path.
        """

        selected = self._checked(allocation)
        if selected.family != expected_family:
            raise WorkingAllocationError("working.policy", "working stream belongs to another allocation family")
        if self.catalog.terminal(selected.allocation_id) is not None:
            raise WorkingAllocationError("working.finished", "working allocation is already terminal")
        if not isinstance(selected_path, Path) or not selected_path.is_absolute():
            raise WorkingAllocationError("working.path", "working stream path must be absolute")
        try:
            parts = selected_path.relative_to(selected.path).parts
        except ValueError as exc:
            raise WorkingAllocationError("working.path", "working stream is outside its allocation") from exc
        if len(parts) != 2:
            raise WorkingAllocationError("working.path", "working stream needs an immediate child directory")
        for part in parts:
            _safe_name(part, label="stream path")
        parent = check_storage.ordinary(selected_path.parent, directory=True)
        if not private_path(parent, directory=True):
            raise WorkingAllocationError("working.unsafe", "working stream parent is not owner-private")
        parent_info = parent.stat()
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        if os.name == "posix" and os.open in os.supports_dir_fd:
            parent_fd = os.open(
                parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                opened_parent = os.fstat(parent_fd)
                if (opened_parent.st_dev, opened_parent.st_ino) != (parent_info.st_dev, parent_info.st_ino):
                    raise WorkingAllocationError("working.changed", "working stream parent changed before creation")
                descriptor = os.open(selected_path.name, flags, 0o600, dir_fd=parent_fd)
            finally:
                os.close(parent_fd)
        else:
            descriptor = os.open(selected_path, flags, 0o600)
        with os.fdopen(descriptor, "wb", buffering=0) as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise WorkingAllocationError("working.changed", "working stream is not an independent file")
            if os.name == "nt":
                secure_private_path(selected_path, directory=False)
            else:
                os.fchmod(stream.fileno(), 0o600)

            def check_visible() -> None:
                visible = selected_path.lstat()
                current_parent = check_storage.ordinary(parent, directory=True).stat()
                if (not stat.S_ISREG(visible.st_mode) or visible.st_nlink != 1
                        or (visible.st_dev, visible.st_ino) != (opened.st_dev, opened.st_ino)
                        or (current_parent.st_dev, current_parent.st_ino)
                        != (parent_info.st_dev, parent_info.st_ino)
                        or not private_path(selected_path, directory=False)
                        or not private_path(parent, directory=True)):
                    raise WorkingAllocationError("working.changed", "working stream path changed custody")

            check_visible()
            fsync_directory(parent)
            try:
                yield stream
            finally:
                stream.flush()
                os.fsync(stream.fileno())
                check_visible()
                self._checked(selected)

    def describe(self, allocation_id: str) -> WorkingAllocationDescription:
        result = self.catalog.describe(allocation_id)
        if result.reference.owner_id != self.owner_id or result.reference.workspace != self.workspace:
            raise WorkingAllocationError("working.policy", "working allocation is outside this Core host")
        return result

    def verify(self, allocation_id: str) -> WorkingAllocationDescription:
        result = self.describe(allocation_id)
        if result.status not in {"complete", "failed"}:
            raise WorkingAllocationError("working.incomplete", "working allocation has no available terminal evidence")
        observed = _selected_inventory(
            result.reference.path,
            tuple(Path(row["absolute_path"]) for row in result.evidence),
            tuple(Path(row["absolute_path"]) for row in result.absolute_references),
        )
        if (observed[0] != list(result.evidence)
                or observed[1] != list(result.absolute_references)):
            raise WorkingAllocationError("working.changed", "working-allocation terminal evidence changed")
        return result

    def inventory(self) -> tuple[WorkingAllocationDescription, ...]:
        rows = self.catalog.inventory_rows(workspace=self.workspace)
        return tuple(self.describe(row["allocation_id"]) for row in rows if row["owner_id"] == self.owner_id)

    @contextmanager
    def execution(self, allocation: WorkingAllocationReference) -> Iterator[None]:
        selected = self._checked(allocation)
        if self.catalog.terminal(selected.allocation_id) is not None:
            raise WorkingAllocationError("working.finished", "working allocation is already terminal")
        nonce = _nonce(selected.allocation_id)
        with ExitStack() as stack:
            try:
                stack.enter_context(private_record_lock(self.catalog._directory("leases") / f"{nonce}.lock"))
            except DurableRecordError as exc:
                if exc.code == "busy":
                    raise WorkingAllocationError("working.busy", "working allocation is already executing") from exc
                raise
            token = _active_allocations.set(_active_allocations.get() | {selected.allocation_id})
            try:
                yield
            finally:
                _active_allocations.reset(token)

    def active(self, allocation: WorkingAllocationReference) -> bool:
        selected = self._checked(allocation)
        if selected.allocation_id in _active_allocations.get():
            return True
        nonce = _nonce(selected.allocation_id)
        try:
            with private_record_lock(self.catalog._directory("leases") / f"{nonce}.lock"):
                return False
        except DurableRecordError as exc:
            if exc.code == "busy":
                return True
            raise

    def request_cancel(self, allocation: WorkingAllocationReference, binding: str) -> None:
        selected = self._checked(allocation)
        if not isinstance(binding, str) or not binding or len(binding) > 256:
            raise WorkingAllocationError("working.cancel", "cancellation requires a bounded owner binding")
        nonce = _nonce(selected.allocation_id)
        body = {
            "format": CANCELLATION_KIND, "allocation_id": selected.allocation_id,
            "reservation_id": self.catalog.reservation(selected.allocation_id)["id"],
            "binding": binding,
        }
        record = _sealed(CANCELLATION_KIND, body)
        with private_record_lock(self.catalog._directory("leases") / f"{nonce}.transition.lock"):
            if self.catalog.terminal(selected.allocation_id) is not None:
                raise WorkingAllocationError("working.finished", "working allocation is already terminal")
            try:
                publish_immutable_bytes(
                    self.catalog._path("cancellations", nonce),
                    check_storage.canonical(record) + b"\n", byte_limit=1024 * 1024,
                    idempotent=True,
                )
            except DurableRecordError as exc:
                if exc.code == "collision":
                    raise WorkingAllocationError(
                        "working.cancel", "working-allocation cancellation binding changed",
                    ) from exc
                raise

    def cancellation_requested(self, allocation: WorkingAllocationReference) -> bool:
        selected = self._checked(allocation)
        return self.catalog.cancellation(selected.allocation_id) is not None

    def check_cancelled(self, allocation: WorkingAllocationReference) -> None:
        selected = self._checked(allocation)
        self._check_dispatch_cancelled()
        if self.cancellation_requested(selected):
            raise WorkingAllocationError("working.cancelled", "working allocation has a cancellation request")

    def finish(
        self, allocation: WorkingAllocationReference, *, outcome: str,
        evidence: tuple[Path, ...] = (), absolute_references: tuple[Path, ...] = (),
        validate: Callable[[Path], object] | None = None, failure: str | None = None,
    ) -> WorkingAllocationDescription:
        selected = self._checked(allocation)
        if selected.allocation_id not in _active_allocations.get():
            raise WorkingAllocationError("working.lease", "finish requires the active Core execution lease")
        if self.catalog.terminal(selected.allocation_id) is not None:
            raise WorkingAllocationError("working.finished", "working allocation is already terminal")
        if outcome not in {"complete", "failed"}:
            raise WorkingAllocationError("working.outcome", "working-allocation outcome is invalid")
        if (outcome == "complete" and (not callable(validate) or failure is not None)):
            raise WorkingAllocationError("working.outcome", "completion requires owner validation without failure")
        if (outcome == "failed" and (not isinstance(failure, str) or not failure or len(failure) > 4096)):
            raise WorkingAllocationError("working.outcome", "failure requires a bounded explanation")
        if validate is not None and not callable(validate):
            raise WorkingAllocationError("working.outcome", "owner validator is invalid")
        before = _selected_inventory(selected.path, evidence, absolute_references)
        if validate is not None:
            validate(selected.path)
        after = _selected_inventory(selected.path, evidence, absolute_references)
        if before != after:
            raise WorkingAllocationError("working.changed", "selected evidence changed during owner validation")
        nonce = _nonce(selected.allocation_id)
        with private_record_lock(self.catalog._directory("leases") / f"{nonce}.transition.lock"):
            if self.catalog.terminal(selected.allocation_id) is not None:
                raise WorkingAllocationError("working.finished", "working allocation is already terminal")
            if outcome == "complete":
                self.check_cancelled(selected)
            self.catalog._write("terminals", nonce, TERMINAL_KIND, {
                "format": TERMINAL_KIND, "allocation_id": selected.allocation_id,
                "activation_id": self.catalog.activation(selected.allocation_id)["id"],
                "outcome": outcome, "evidence": before[0], "absolute_references": before[1],
                "failure": failure, "finished_at": _now(),
                "remaining_contents": "mutable-disposable-retained-until-review",
            })
        return self.describe(selected.allocation_id)


def _selected_path(root: Path, value: Path, *, absolute: bool) -> tuple[Path, str]:
    if not isinstance(value, Path) or ".." in value.parts or (absolute and not value.is_absolute()):
        raise WorkingAllocationError("working.evidence", "selected working path is invalid")
    candidate = value if value.is_absolute() else root / value
    if not candidate.is_relative_to(root) or candidate == root:
        raise WorkingAllocationError("working.evidence", "selected working path is outside its allocation")
    relative = candidate.relative_to(root).as_posix()
    try:
        check_storage.safe_path(relative)
    except ValueError as exc:
        raise WorkingAllocationError("working.evidence", "selected working path is not portable") from exc
    return candidate, relative


def _file_evidence(path: Path, relative: str) -> dict[str, object]:
    check_storage.ordinary(path)
    before = path.stat()
    if before.st_size > _MAX_EVIDENCE_BYTES:
        raise WorkingAllocationError("working.bounds", "selected evidence exceeds its byte bound")
    digest = sha256()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
                != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)):
            raise WorkingAllocationError("working.changed", "selected evidence changed before reading")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        os.fsync(descriptor)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    visible = path.stat()
    def identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
    if identity(opened) != identity(after) or identity(after) != identity(visible):
        raise WorkingAllocationError("working.changed", "selected evidence changed during reading")
    return {"relative_path": relative, "absolute_path": str(path),
            "bytes": before.st_size, "sha256": "sha256:" + digest.hexdigest()}


def _selected_inventory(
    root: Path, evidence: tuple[Path, ...], absolute_references: tuple[Path, ...],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if (not isinstance(evidence, tuple) or len(evidence) > _MAX_EVIDENCE
            or not isinstance(absolute_references, tuple)
            or len(absolute_references) > _MAX_REFERENCES):
        raise WorkingAllocationError("working.bounds", "too many selected working paths")
    retained = []
    seen = set()
    total = 0
    for value in evidence:
        path, relative = _selected_path(root, value, absolute=False)
        if relative.casefold() in seen:
            raise WorkingAllocationError("working.evidence", "selected evidence is duplicated")
        seen.add(relative.casefold())
        row = _file_evidence(path, relative)
        total += int(row["bytes"])
        if total > _MAX_EVIDENCE_BYTES:
            raise WorkingAllocationError("working.bounds", "selected evidence exceeds its total byte bound")
        retained.append(row)
    references = []
    seen.clear()
    for value in absolute_references:
        path, relative = _selected_path(root, value, absolute=True)
        if relative.casefold() in seen:
            raise WorkingAllocationError("working.evidence", "absolute working reference is duplicated")
        seen.add(relative.casefold())
        try:
            ordinary = check_storage.ordinary(path, directory=True)
            kind = "directory"
        except (OSError, ValueError):
            ordinary = check_storage.ordinary(path)
            kind = "file"
        info = ordinary.stat()
        references.append({"relative_path": relative, "absolute_path": str(path),
                           "kind": kind, "device": info.st_dev, "inode": info.st_ino})
    for parent in {Path(row["absolute_path"]).parent for row in retained} | {root}:
        fsync_directory(parent)
    return (sorted(retained, key=lambda row: str(row["relative_path"])),
            sorted(references, key=lambda row: str(row["relative_path"])))


def _valid_selected_rows(root: Path, evidence: list, references: list) -> bool:
    for rows, keys in (
        (evidence, {"relative_path", "absolute_path", "bytes", "sha256"}),
        (references, {"relative_path", "absolute_path", "kind", "device", "inode"}),
    ):
        names = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != keys:
                return False
            relative = row["relative_path"]
            absolute = row["absolute_path"]
            try:
                check_storage.safe_path(relative)
            except ValueError:
                return False
            if absolute != str(root / relative):
                return False
            names.append(relative)
            if rows is evidence:
                if (type(row["bytes"]) is not int or row["bytes"] < 0
                        or not isinstance(row["sha256"], str)
                        or _SHA.fullmatch(row["sha256"]) is None):
                    return False
            elif (not isinstance(row["kind"], str) or row["kind"] not in {"file", "directory"}
                  or type(row["device"]) is not int or type(row["inode"]) is not int):
                return False
        if names != sorted(names) or len({name.casefold() for name in names}) != len(names):
            return False
    return sum(int(row["bytes"]) for row in evidence) <= _MAX_EVIDENCE_BYTES


def resolve_direct_working_allocations(
    suite_root: Path, *, owner_id: str,
    environment: Mapping[str, str] | None = None,
) -> CoreWorkingAllocations:
    """Bind a supported direct module entry to Core's selected workspace/store."""

    from .environment_resolution import resolve_environment

    resolved = resolve_environment(suite_root, environment=environment)
    return CoreWorkingAllocations(
        workspace=resolved.workspace,
        configuration_home=resolved.configuration_home,
        locations=resolved.locations,
        owner_id=owner_id,
        policy_id=resolved.record["resolution_id"],
        location_sources={
            role: row["source"] for role, row in resolved.record["locations"].items()
        },
    )


__all__ = [
    "CoreWorkingAllocations", "WorkingAllocationCatalog",
    "resolve_direct_working_allocations",
]
