"""Core custody for disposable, process-scoped scratch directories.

These leases are separate from retained working allocations. A run may dispose
its exact scratch roots after its owner supplies a drain check. A restart never
infers that an unlocked directory is safe to delete.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
from typing import Callable, Iterator, Mapping
from uuid import uuid4

from . import check_storage
from .durable_records import (
    DurableRecordError, private_record_lock, publish_immutable_bytes,
    read_private_single_link_bytes,
)
from .host_filesystem import fsync_directory, private_path, secure_private_path
from .managed_trees import _rename_no_replace
from .output_routing import _private_directory
from .storage.registered import ResourceCatalog


_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_ID = re.compile(r"workbench-temporary-lease-v1:([0-9a-f]{32})\Z")
_RESERVATION = "workbench-temporary-reservation-v1"
_ACTIVATION = "workbench-temporary-activation-v1"
_MARKER = "workbench-temporary-marker-v1"
_DISPOSAL_INTENT = "workbench-temporary-disposal-intent-v1"
_DISPOSAL = "workbench-temporary-disposal-v1"
_FAILURE = "workbench-temporary-disposal-failure-v1"
_RETENTION = "workbench-temporary-retention-v1"
_MARKER_NAME = ".workbench-temporary-lease.json"
_RETENTION_NAME = ".workbench-temporary-retained.json"
_RECORD_LIMIT = 1024 * 1024
_CATALOG_CHILDREN = frozenset({
    "reservations", "activations", "disposal-intents", "disposals", "failures", "leases",
})
_RECORD_NAME = re.compile(r"([0-9a-f]{32})\.json\Z")
_LEASE_NAME = re.compile(r"([0-9a-f]{32})\.lock\Z")
_active: ContextVar[frozenset[str]] = ContextVar("workbench_active_temporary_leases", default=frozenset())


class TemporaryLeaseError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TemporaryLeaseReference:
    lease_id: str
    workspace: Path
    owner_id: str
    role: str
    path: Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _nonce(lease_id: str) -> str:
    match = _ID.fullmatch(lease_id) if isinstance(lease_id, str) else None
    if match is None:
        raise TemporaryLeaseError("temporary.id", "select an exact temporary lease ID")
    return match.group(1)


def _sealed(kind: str, body: Mapping[str, object]) -> dict:
    return check_storage.seal(kind, dict(body))


def _mount_id(directory_fd: int) -> int:
    """Read the Linux mount ID of an open directory, or refuse disposal."""

    if sys.platform != "linux":
        raise TemporaryLeaseError("temporary.unsafe", "temporary lease mount identity is unavailable")
    try:
        with open(f"/proc/self/fdinfo/{directory_fd}", encoding="ascii") as info:
            for line in info:
                key, separator, value = line.partition(":")
                if key == "mnt_id" and separator:
                    mount_id = int(value.strip())
                    if mount_id > 0:
                        return mount_id
    except (OSError, ValueError) as exc:
        raise TemporaryLeaseError("temporary.unsafe", "temporary lease mount identity is unavailable") from exc
    raise TemporaryLeaseError("temporary.unsafe", "temporary lease mount identity is unavailable")


class CoreTemporaryLeases:
    """Register, drain and dispose only exact Core-issued scratch roots."""

    def __init__(self, *, workspace: Path, configuration_home: Path,
                 locations: Mapping[str, Path], owner_id: str):
        if (not workspace.is_absolute() or not configuration_home.is_absolute()
                or not _TOKEN.fullmatch(owner_id)):
            raise TemporaryLeaseError("temporary.policy", "temporary lease host identity is invalid")
        if not locations or any(
            not _TOKEN.fullmatch(role) or not Path(root).is_absolute()
            for role, root in locations.items()
        ):
            raise TemporaryLeaseError("temporary.policy", "temporary lease roots are invalid")
        self.workspace = workspace
        self.configuration_home = configuration_home
        self.locations = {role: Path(root) for role, root in locations.items()}
        self.owner_id = owner_id
        self.root = ResourceCatalog(configuration_home).root / "temporary-leases"

    def _directory(self, name: str) -> Path:
        return self.root / name

    def _path(self, name: str, nonce: str) -> Path:
        return self._directory(name) / f"{nonce}.json"

    def _ensure_catalog(self) -> None:
        ResourceCatalog(self.configuration_home)._ensure()
        for path in (self.root, *(self._directory(name) for name in (
            "reservations", "activations", "disposal-intents", "disposals",
            "failures", "leases",
        ))):
            _private_directory(path)
            secure_private_path(path, directory=True)

    def _write(self, name: str, nonce: str, kind: str, body: Mapping[str, object],
               *, idempotent: bool = False) -> dict:
        record = _sealed(kind, body)
        publish_immutable_bytes(
            self._path(name, nonce), check_storage.canonical(record) + b"\n",
            byte_limit=_RECORD_LIMIT, idempotent=idempotent,
        )
        return record

    def _read(self, name: str, nonce: str, kind: str) -> dict:
        try:
            path = self._path(name, nonce)
            record = json.loads(read_private_single_link_bytes(path, byte_limit=_RECORD_LIMIT))
        except (OSError, ValueError) as exc:
            if isinstance(exc, TemporaryLeaseError):
                raise
            raise TemporaryLeaseError("temporary.unavailable", f"temporary lease {name} is unavailable") from exc
        if not isinstance(record, dict) or record != _sealed(
            kind, {key: value for key, value in record.items() if key != "id"}
        ):
            raise TemporaryLeaseError("temporary.changed", f"temporary lease {name} changed")
        return record

    def _reservation(self, lease_id: str) -> dict:
        nonce = _nonce(lease_id)
        row = self._read("reservations", nonce, _RESERVATION)
        selected_role = row.get("role")
        selected_path = row.get("path")
        selected_root = row.get("store_root")
        if (set(row) != {"id", "format", "lease_id", "workspace", "owner_id", "role",
                         "store_root", "path", "parent_device", "parent_inode", "reserved_at"}
                or row["format"] != _RESERVATION or row["lease_id"] != lease_id
                or row["workspace"] != str(self.workspace) or row["owner_id"] != self.owner_id
                or not isinstance(selected_role, str) or not _TOKEN.fullmatch(selected_role)
                or not isinstance(selected_root, str)
                or not Path(selected_root).is_absolute()
                or ".." in Path(selected_root).parts
                or not isinstance(selected_path, str)
                or not _TOKEN.fullmatch(Path(selected_path).name)
                or Path(selected_path).parent != Path(selected_root)
                or type(row["parent_device"]) is not int
                or type(row["parent_inode"]) is not int
                or not isinstance(row["reserved_at"], str) or not row["reserved_at"]):
            raise TemporaryLeaseError("temporary.changed", "temporary lease reservation changed")
        return row

    def _activation(self, lease_id: str) -> dict:
        nonce = _nonce(lease_id)
        row = self._read("activations", nonce, _ACTIVATION)
        reservation = self._reservation(lease_id)
        if (set(row) != {"id", "format", "lease_id", "reservation_id", "device", "inode", "activated_at"}
                or row["format"] != _ACTIVATION or row["lease_id"] != lease_id
                or row["reservation_id"] != reservation["id"]
                or type(row["device"]) is not int or type(row["inode"]) is not int
                or not isinstance(row["activated_at"], str) or not row["activated_at"]):
            raise TemporaryLeaseError("temporary.changed", "temporary lease activation changed")
        return row

    def _reference(self, reservation: Mapping[str, object]) -> TemporaryLeaseReference:
        return TemporaryLeaseReference(
            lease_id=str(reservation["lease_id"]), workspace=self.workspace,
            owner_id=self.owner_id, role=str(reservation["role"]),
            path=Path(reservation["path"]),
        )

    @classmethod
    @contextmanager
    def reference_lease(
        cls, configuration_home: Path, lease_id: str, *, workspace: Path,
        owner_id: str, role: str | None = None, path: Path | None = None,
    ) -> Iterator[TemporaryLeaseReference]:
        """Hold and reopen one typed dependency on a live scratch lease.

        Its active execution already holds the lock in this context. A later
        reader takes that same lock, excluding disposal while it checks the
        sealed reservation, activation and physical marker.
        """

        nonce = _nonce(lease_id)
        reservation_path = (
            ResourceCatalog(configuration_home).root / "temporary-leases"
            / "reservations" / f"{nonce}.json"
        )
        try:
            raw = json.loads(read_private_single_link_bytes(
                reservation_path, byte_limit=_RECORD_LIMIT,
            ))
            if (not isinstance(raw, dict) or not isinstance(raw.get("role"), str)
                    or not isinstance(raw.get("store_root"), str)):
                raise ValueError("temporary lease reservation is invalid")
            host = cls(
                workspace=workspace, configuration_home=configuration_home,
                locations={raw["role"]: Path(raw["store_root"])}, owner_id=owner_id,
            )
            def reopen() -> TemporaryLeaseReference:
                reservation = host._reservation(lease_id)
                if (role is not None and reservation["role"] != role
                        or path is not None and Path(reservation["path"]) != path
                        or host._path("disposal-intents", nonce).exists()
                        or host._disposed(lease_id)):
                    raise TemporaryLeaseError(
                        "temporary.reference", "referenced temporary lease is unavailable or changed",
                    )
                return host.open(lease_id)

            if lease_id in _active.get():
                yield reopen()
            else:
                with private_record_lock(host._directory("leases") / f"{nonce}.lock"):
                    yield reopen()
        except (OSError, ValueError, TypeError, KeyError, DurableRecordError) as exc:
            if isinstance(exc, TemporaryLeaseError):
                raise
            raise TemporaryLeaseError(
                "temporary.reference", "referenced temporary lease is unavailable or changed",
            ) from exc

    def _disposed(self, lease_id: str) -> bool:
        nonce = _nonce(lease_id)
        if not self._path("disposals", nonce).exists():
            return False
        row = self._read("disposals", nonce, _DISPOSAL)
        intent = self._read("disposal-intents", nonce, _DISPOSAL_INTENT)
        if (set(row) != {"id", "format", "lease_id", "intent_id", "disposed_at"}
                or row["format"] != _DISPOSAL or row["lease_id"] != lease_id
                or row["intent_id"] != intent["id"]
                or not isinstance(row["disposed_at"], str) or not row["disposed_at"]):
            raise TemporaryLeaseError("temporary.changed", "temporary lease disposal record changed")
        return True

    def _disposal_intent(self, lease_id: str) -> dict:
        nonce = _nonce(lease_id)
        row = self._read("disposal-intents", nonce, _DISPOSAL_INTENT)
        reservation = self._reservation(lease_id)
        activation = self._activation(lease_id)
        tombstone = Path(reservation["path"]).parent / f".workbench-temporary-{nonce}.disposing"
        if (set(row) != {"id", "format", "lease_id", "reservation_id", "activation_id",
                         "device", "inode", "tombstone", "processes_drained"}
                or row["format"] != _DISPOSAL_INTENT or row["lease_id"] != lease_id
                or row["reservation_id"] != reservation["id"]
                or row["activation_id"] != activation["id"]
                or type(row["device"]) is not int or type(row["inode"]) is not int
                or row["device"] != activation["device"] or row["inode"] != activation["inode"]
                or row["tombstone"] != str(tombstone)
                or row["processes_drained"] is not True):
            raise TemporaryLeaseError("temporary.changed", "temporary lease disposal intent changed")
        return row

    def _retention(self, lease_id: str) -> dict | None:
        reservation = self._reservation(lease_id)
        activation = self._activation(lease_id)
        marker = Path(reservation["path"]) / _RETENTION_NAME
        if not marker.exists() and not marker.is_symlink():
            return None
        try:
            parent = check_storage.ordinary(marker.parent.parent, directory=True)
            directory = check_storage.ordinary(marker.parent, directory=True)
            parent_info, info = parent.stat(), directory.stat()
            if ((parent_info.st_dev, parent_info.st_ino)
                    != (reservation["parent_device"], reservation["parent_inode"])
                    or (info.st_dev, info.st_ino)
                    != (activation["device"], activation["inode"])
                    or not private_path(directory, directory=True)):
                raise TemporaryLeaseError("temporary.changed", "temporary lease retention root changed")
        except (OSError, ValueError) as exc:
            if isinstance(exc, TemporaryLeaseError):
                raise
            raise TemporaryLeaseError("temporary.changed", "temporary lease retention root changed") from exc
        try:
            row = json.loads(read_private_single_link_bytes(marker, byte_limit=_RECORD_LIMIT))
        except (OSError, ValueError) as exc:
            raise TemporaryLeaseError("temporary.changed", "temporary lease retention changed") from exc
        if (not isinstance(row, dict)
                or row != _sealed(_RETENTION, {key: value for key, value in row.items() if key != "id"})
                or set(row) != {"id", "format", "lease_id", "activation_id", "outcome",
                                "reason", "retained_at"}
                or row["format"] != _RETENTION or row["lease_id"] != lease_id
                or row["activation_id"] != activation["id"]
                or type(row["outcome"]) is not str
                or row["outcome"] not in {"completed", "failed"}
                or row["reason"] != "process-absence-unproven"
                or not isinstance(row["retained_at"], str) or not row["retained_at"]):
            raise TemporaryLeaseError("temporary.changed", "temporary lease retention changed")
        return row

    @staticmethod
    def _inventory_directory(path: Path) -> Path:
        try:
            directory = check_storage.ordinary(path, directory=True)
        except (OSError, ValueError) as exc:
            raise TemporaryLeaseError("temporary.changed", "temporary lease catalog directory changed") from exc
        if not private_path(directory, directory=True):
            raise TemporaryLeaseError("temporary.changed", "temporary lease catalog directory lost private custody")
        return directory

    @classmethod
    def inventory_catalog(cls, configuration_home: Path, *, workspace: Path | None = None) -> list[dict[str, object]]:
        """Validate every current child before selecting one workspace's leases."""

        root = ResourceCatalog(configuration_home).root / "temporary-leases"
        if not root.exists() and not root.is_symlink():
            return []
        try:
            if {entry.name for entry in cls._inventory_directory(root).iterdir()} != _CATALOG_CHILDREN:
                raise TemporaryLeaseError("temporary.changed", "temporary lease catalog has an unknown or missing child")
            children = {
                name: sorted(cls._inventory_directory(root / name).iterdir())
                for name in sorted(_CATALOG_CHILDREN)
            }
            reservations = {}
            for path in children["reservations"]:
                match = _RECORD_NAME.fullmatch(path.name)
                if match is None:
                    raise TemporaryLeaseError("temporary.changed", "temporary lease reservation has an invalid name")
                raw_bytes = read_private_single_link_bytes(path, byte_limit=_RECORD_LIMIT)
                nonce = match.group(1)
                raw = json.loads(raw_bytes)
                if (not isinstance(raw, dict) or not isinstance(raw.get("workspace"), str)
                        or not isinstance(raw.get("owner_id"), str)
                        or not isinstance(raw.get("role"), str)
                        or not isinstance(raw.get("store_root"), str)):
                    raise TemporaryLeaseError("temporary.changed", "temporary lease reservation changed")
                host = cls(
                    workspace=Path(raw["workspace"]), configuration_home=configuration_home,
                    locations={raw["role"]: Path(raw["store_root"])}, owner_id=raw["owner_id"],
                )
                reservation = host._reservation(f"workbench-temporary-lease-v1:{nonce}")
                if raw != reservation:
                    raise TemporaryLeaseError("temporary.changed", "temporary lease reservation changed during inventory")
                reservations[nonce] = (host, reservation)
            for name in ("activations", "disposal-intents", "disposals"):
                for path in children[name]:
                    match = _RECORD_NAME.fullmatch(path.name)
                    if match is None or match.group(1) not in reservations:
                        raise TemporaryLeaseError("temporary.changed", "temporary lease catalog has an orphan record")
                    read_private_single_link_bytes(path, byte_limit=_RECORD_LIMIT)
            for path in children["leases"]:
                match = _LEASE_NAME.fullmatch(path.name)
                if match is None or match.group(1) not in reservations:
                    raise TemporaryLeaseError("temporary.changed", "temporary lease catalog has an orphan lock")
                read_private_single_link_bytes(path, byte_limit=0)
            for path in children["failures"]:
                if _RECORD_NAME.fullmatch(path.name) is None:
                    raise TemporaryLeaseError("temporary.changed", "temporary lease failure has an invalid name")
                raw = json.loads(read_private_single_link_bytes(path, byte_limit=_RECORD_LIMIT))
                nonce = _nonce(raw.get("lease_id") if isinstance(raw, dict) else None)
                if nonce not in reservations:
                    raise TemporaryLeaseError("temporary.changed", "temporary lease catalog has an orphan failure")
                host, _ = reservations[nonce]
                failure = host._read("failures", path.stem, _FAILURE)
                intent = host._disposal_intent(failure["lease_id"])
                if (set(failure) != {"id", "format", "lease_id", "intent_id", "error", "failed_at"}
                        or failure["format"] != _FAILURE
                        or failure["intent_id"] != intent["id"]
                        or not isinstance(failure["error"], str)
                        or not failure["error"] or len(failure["error"]) > 4096
                        or not isinstance(failure["failed_at"], str) or not failure["failed_at"]):
                    raise TemporaryLeaseError("temporary.changed", "temporary lease failure changed")
            rows = []
            for nonce, (host, reservation) in sorted(reservations.items()):
                lease_id = reservation["lease_id"]
                if (root / "activations" / f"{nonce}.json").exists():
                    host._activation(lease_id)
                if (root / "disposal-intents" / f"{nonce}.json").exists():
                    host._disposal_intent(lease_id)
                if (root / "disposals" / f"{nonce}.json").exists():
                    host._disposed(lease_id)
                    status = "disposed"
                elif (root / "disposal-intents" / f"{nonce}.json").exists():
                    target = Path(reservation["path"])
                    tombstone = target.parent / f".workbench-temporary-{nonce}.disposing"
                    status = ("disposal-incomplete" if any(
                        path.exists() or path.is_symlink() for path in (target, tombstone)
                    ) else "disposal-unknown")
                elif (root / "activations" / f"{nonce}.json").exists():
                    status = ("retained-unproven" if host._retention(lease_id) is not None
                              else "active-or-abandoned")
                else:
                    status = "reserved-incomplete"
                if workspace is None or reservation["workspace"] == str(workspace):
                    rows.append({
                        "lease_id": lease_id, "workspace": reservation["workspace"],
                        "owner_id": reservation["owner_id"], "role": reservation["role"],
                        "path": reservation["path"], "status": status,
                    })
            return rows
        except (OSError, ValueError, TypeError, KeyError) as exc:
            if isinstance(exc, TemporaryLeaseError):
                raise
            raise TemporaryLeaseError("temporary.changed", "temporary lease catalog changed") from exc

    def inventory(self) -> tuple[dict[str, object], ...]:
        """Expose exact local leases for explicit restart reconciliation."""

        if not self._directory("reservations").exists():
            return ()
        rows = []
        for path in sorted(self._directory("reservations").glob("*.json")):
            lease_id = f"workbench-temporary-lease-v1:{path.stem}"
            raw = self._read("reservations", path.stem, _RESERVATION)
            if raw.get("workspace") != str(self.workspace) or raw.get("owner_id") != self.owner_id:
                continue
            reservation = self._reservation(lease_id)
            nonce = _nonce(lease_id)
            if self._disposed(lease_id):
                state = "disposed"
            elif self._path("disposal-intents", nonce).exists():
                target = Path(reservation["path"])
                tombstone = target.parent / f".workbench-temporary-{nonce}.disposing"
                state = ("disposal-incomplete" if any(
                    candidate.exists() or candidate.is_symlink()
                    for candidate in (target, tombstone)
                ) else "disposal-unknown")
            elif self._path("activations", nonce).exists():
                state = ("retained-unproven" if self._retention(lease_id) is not None
                         else "active-or-abandoned")
            else:
                state = "reserved-incomplete"
            rows.append({"reference": self._reference(reservation), "state": state})
        return tuple(rows)

    def allocate(self, role: str, name: str) -> TemporaryLeaseReference:
        if role not in self.locations or not isinstance(name, str) or not _TOKEN.fullmatch(name):
            raise TemporaryLeaseError("temporary.path", "temporary lease role or name is invalid")
        parent = self.locations[role]
        target = parent / name
        if target == self.workspace or target == self.configuration_home or self.configuration_home in target.parents:
            raise TemporaryLeaseError("temporary.path", "temporary lease would cover protected storage")
        _private_directory(parent)
        try:
            secure_private_path(parent, directory=True)
        except OSError as exc:
            raise TemporaryLeaseError("temporary.unsafe", "temporary lease parent is not owner-private; on WSL use a Linux filesystem when the selected mount cannot enforce private ownership") from exc
        if not private_path(parent, directory=True):
            raise TemporaryLeaseError("temporary.unsafe", "temporary lease parent is not owner-private; on WSL use a Linux filesystem when the selected mount cannot enforce private ownership")
        parent_info = check_storage.ordinary(parent, directory=True).stat()
        if target.exists() or target.is_symlink():
            raise TemporaryLeaseError("temporary.exists", "temporary lease path already exists")
        self._ensure_catalog()
        nonce = uuid4().hex
        lease_id = f"workbench-temporary-lease-v1:{nonce}"
        reservation = self._write("reservations", nonce, _RESERVATION, {
            "format": _RESERVATION, "lease_id": lease_id,
            "workspace": str(self.workspace), "owner_id": self.owner_id,
            "role": role, "store_root": str(parent), "path": str(target),
            "parent_device": parent_info.st_dev, "parent_inode": parent_info.st_ino,
            "reserved_at": _now(),
        })
        try:
            target.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise TemporaryLeaseError("temporary.exists", "temporary lease path already exists") from exc
        try:
            secure_private_path(target, directory=True)
        except OSError as exc:
            raise TemporaryLeaseError("temporary.unsafe", "temporary lease root is not owner-private; on WSL use a Linux filesystem when the selected mount cannot enforce private ownership") from exc
        marker = _sealed(_MARKER, {
            "format": _MARKER, "lease_id": lease_id, "reservation_id": reservation["id"],
        })
        publish_immutable_bytes(
            target / _MARKER_NAME, check_storage.canonical(marker) + b"\n",
            byte_limit=_RECORD_LIMIT,
        )
        fsync_directory(target)
        fsync_directory(parent)
        info = check_storage.ordinary(target, directory=True).stat()
        self._write("activations", nonce, _ACTIVATION, {
            "format": _ACTIVATION, "lease_id": lease_id,
            "reservation_id": reservation["id"],
            "device": info.st_dev, "inode": info.st_ino, "activated_at": _now(),
        })
        return self._reference(reservation)

    def open(self, lease_id: str) -> TemporaryLeaseReference:
        reservation = self._reservation(lease_id)
        activation = self._activation(lease_id)
        path = Path(reservation["path"])
        try:
            parent = check_storage.ordinary(path.parent, directory=True)
            directory = check_storage.ordinary(path, directory=True)
            parent_info, info = parent.stat(), directory.stat()
            if ((parent_info.st_dev, parent_info.st_ino) != (reservation["parent_device"], reservation["parent_inode"])
                    or (info.st_dev, info.st_ino) != (activation["device"], activation["inode"])
                    or not private_path(directory, directory=True)):
                raise TemporaryLeaseError("temporary.changed", "temporary lease path changed identity or custody")
            marker_path = directory / _MARKER_NAME
            if not private_path(marker_path, directory=False):
                raise TemporaryLeaseError("temporary.changed", "temporary lease marker changed custody")
            marker = check_storage.read_json(marker_path, byte_limit=_RECORD_LIMIT)
            if (marker != _sealed(_MARKER, {
                "format": _MARKER, "lease_id": lease_id,
                "reservation_id": reservation["id"],
            })):
                raise TemporaryLeaseError("temporary.changed", "temporary lease marker changed")
            self._retention(lease_id)
        except (OSError, ValueError) as exc:
            if isinstance(exc, TemporaryLeaseError):
                raise
            raise TemporaryLeaseError("temporary.changed", "temporary lease path is unavailable or changed") from exc
        return self._reference(reservation)

    def prepared_stage_marker(
        self, reference: TemporaryLeaseReference,
    ) -> tuple[tuple[int, int], str, bytes]:
        """Describe the exact active lease marker for a pinned member move."""

        if reference.lease_id not in _active.get() or reference != self.open(reference.lease_id):
            raise TemporaryLeaseError("temporary.lease", "prepared stage requires its active Core lease")
        reservation = self._reservation(reference.lease_id)
        activation = self._activation(reference.lease_id)
        marker = _sealed(_MARKER, {
            "format": _MARKER, "lease_id": reference.lease_id,
            "reservation_id": reservation["id"],
        })
        return (
            (activation["device"], activation["inode"]),
            _MARKER_NAME,
            check_storage.canonical(marker) + b"\n",
        )

    def retain(self, reference: TemporaryLeaseReference, *, outcome: str) -> None:
        """Mark a completed call as protected until independent drain proof."""

        if reference.lease_id not in _active.get():
            raise TemporaryLeaseError("temporary.lease", "retention requires the active Core temporary lease")
        if outcome not in {"completed", "failed"}:
            raise TemporaryLeaseError("temporary.policy", "temporary lease outcome is invalid")
        if reference != self.open(reference.lease_id):
            raise TemporaryLeaseError("temporary.policy", "temporary lease reference changed")
        activation = self._activation(reference.lease_id)
        marker = reference.path / _RETENTION_NAME
        record = _sealed(_RETENTION, {
            "format": _RETENTION, "lease_id": reference.lease_id,
            "activation_id": activation["id"], "outcome": outcome,
            "reason": "process-absence-unproven", "retained_at": _now(),
        })
        publish_immutable_bytes(
            marker, check_storage.canonical(record) + b"\n", byte_limit=_RECORD_LIMIT,
        )
        fsync_directory(reference.path)
        self._retention(reference.lease_id)

    @contextmanager
    def execution(self, reference: TemporaryLeaseReference) -> Iterator[None]:
        if reference != self.open(reference.lease_id):
            raise TemporaryLeaseError("temporary.policy", "temporary lease reference changed")
        nonce = _nonce(reference.lease_id)
        try:
            with private_record_lock(self._directory("leases") / f"{nonce}.lock"):
                if self._disposed(reference.lease_id):
                    raise TemporaryLeaseError("temporary.disposed", "temporary lease is already disposed")
                token = _active.set(_active.get() | {reference.lease_id})
                try:
                    yield
                finally:
                    _active.reset(token)
        except DurableRecordError as exc:
            if exc.code == "busy":
                raise TemporaryLeaseError("temporary.busy", "temporary lease is active") from exc
            raise TemporaryLeaseError("temporary.unsafe", "temporary lease lock is unsafe") from exc

    @staticmethod
    def _remove_owned_tree(
        path: Path, *, device: int, inode: int,
        parent_device: int, parent_inode: int,
    ) -> None:
        """Delete through a pinned root FD on POSIX, then unlink that inode."""

        if os.name != "posix":  # pragma: no cover - Windows qualification
            info = path.lstat()
            if (info.st_dev, info.st_ino) != (device, inode) or not stat.S_ISDIR(info.st_mode):
                raise TemporaryLeaseError("temporary.changed", "temporary lease disposal root changed")
            shutil.rmtree(path)
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        parent_fd = os.open(path.parent, flags)
        try:
            parent_info = os.fstat(parent_fd)
            if (parent_info.st_dev, parent_info.st_ino) != (parent_device, parent_inode):
                raise TemporaryLeaseError("temporary.changed", "temporary lease disposal parent changed")
            root_fd = os.open(path.name, flags, dir_fd=parent_fd)
            try:
                info = os.fstat(root_fd)
                if (info.st_dev, info.st_ino) != (device, inode):
                    raise TemporaryLeaseError("temporary.changed", "temporary lease disposal root changed")
                root_mount_id = _mount_id(root_fd)
                for _, directories, filenames, directory_fd in os.fwalk(
                    ".", topdown=False, follow_symlinks=False, dir_fd=root_fd,
                ):
                    if (os.fstat(directory_fd).st_dev != device
                            or _mount_id(directory_fd) != root_mount_id):
                        raise TemporaryLeaseError("temporary.changed", "temporary lease contains another filesystem")
                    for name in filenames:
                        os.unlink(name, dir_fd=directory_fd)
                    for name in directories:
                        child = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        if stat.S_ISLNK(child.st_mode):
                            os.unlink(name, dir_fd=directory_fd)
                        else:
                            os.rmdir(name, dir_fd=directory_fd)
                visible = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                if (visible.st_dev, visible.st_ino) != (device, inode):
                    raise TemporaryLeaseError("temporary.changed", "temporary lease disposal root changed")
                os.rmdir(path.name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            finally:
                os.close(root_fd)
        finally:
            os.close(parent_fd)

    def _dispose_locked(self, reference: TemporaryLeaseReference,
                        drained: Callable[[], bool]) -> None:
        nonce = _nonce(reference.lease_id)
        if self._disposed(reference.lease_id):
            return
        reservation = self._reservation(reference.lease_id)
        if reservation["role"] == "packwiz-v2":
            raise TemporaryLeaseError(
                "temporary.policy",
                "Packwiz source scratch remains retained until its dependency and process absence policy is complete",
            )
        if not callable(drained) or drained() is not True:
            raise TemporaryLeaseError("temporary.active", "temporary lease process tree is not confirmed drained")
        try:
            for tree in ResourceCatalog(self.configuration_home).trees.inventory():
                if reference.lease_id in tree["references"]:
                    raise TemporaryLeaseError(
                        "temporary.referenced",
                        "temporary lease is retained by a managed tree",
                    )
        except (OSError, ValueError) as exc:
            if isinstance(exc, TemporaryLeaseError):
                raise
            raise TemporaryLeaseError(
                "temporary.unsafe", "managed tree dependencies cannot be inspected",
            ) from exc
        activation = self._activation(reference.lease_id)
        target = Path(reservation["path"])
        parent = check_storage.ordinary(target.parent, directory=True)
        parent_info = parent.stat()
        if (parent_info.st_dev, parent_info.st_ino) != (reservation["parent_device"], reservation["parent_inode"]):
            raise TemporaryLeaseError("temporary.changed", "temporary lease parent changed")
        tombstone = target.parent / f".workbench-temporary-{nonce}.disposing"
        intent_path = self._path("disposal-intents", nonce)
        if not intent_path.exists():
            self.open(reference.lease_id)
        intent = self._write("disposal-intents", nonce, _DISPOSAL_INTENT, {
            "format": _DISPOSAL_INTENT, "lease_id": reference.lease_id,
            "reservation_id": reservation["id"], "activation_id": activation["id"],
            "device": activation["device"], "inode": activation["inode"],
            "tombstone": str(tombstone),
            "processes_drained": True,
        }, idempotent=True)
        if tombstone.exists() or tombstone.is_symlink():
            selected = tombstone
        elif target.exists() or target.is_symlink():
            selected = target
        else:
            selected = None
        if selected is not None:
            observed = check_storage.ordinary(selected, directory=True).stat()
            if ((observed.st_dev, observed.st_ino) != (activation["device"], activation["inode"])
                    or not private_path(selected, directory=True)):
                raise TemporaryLeaseError("temporary.changed", "temporary lease disposal root changed")
        if selected == target:
            try:
                _rename_no_replace(
                    target, tombstone,
                    parent_identity=(reservation["parent_device"], reservation["parent_inode"]),
                    payload_identity=(activation["device"], activation["inode"]),
                )
            except ValueError as exc:
                raise TemporaryLeaseError("temporary.changed", f"temporary lease could not isolate its disposal root: {exc}") from exc
            selected = tombstone
        if selected is None:
            error = TemporaryLeaseError(
                "temporary.unknown",
                "temporary lease disposal root is missing; disposition cannot be verified",
            )
            self._write("failures", uuid4().hex, _FAILURE, {
                "format": _FAILURE, "lease_id": reference.lease_id,
                "intent_id": intent["id"], "error": f"{type(error).__name__}: {error}",
                "failed_at": _now(),
            })
            raise error
        if selected is not None:
            try:
                self._remove_owned_tree(
                    selected, device=activation["device"], inode=activation["inode"],
                    parent_device=reservation["parent_device"],
                    parent_inode=reservation["parent_inode"],
                )
            except (OSError, TemporaryLeaseError) as exc:
                self._write("failures", uuid4().hex, _FAILURE, {
                    "format": _FAILURE, "lease_id": reference.lease_id,
                    "intent_id": intent["id"], "error": f"{type(exc).__name__}: {exc}"[:4096],
                    "failed_at": _now(),
                })
                if isinstance(exc, TemporaryLeaseError):
                    raise
                raise TemporaryLeaseError("temporary.cleanup", f"temporary lease cleanup failed: {exc}") from exc
        fsync_directory(parent)
        self._write("disposals", nonce, _DISPOSAL, {
            "format": _DISPOSAL, "lease_id": reference.lease_id,
            "intent_id": intent["id"], "disposed_at": _now(),
        })

    def dispose(self, reference: TemporaryLeaseReference,
                *, drained: Callable[[], bool]) -> None:
        if reference.lease_id not in _active.get():
            raise TemporaryLeaseError("temporary.lease", "disposal requires the active Core temporary lease")
        if self._disposed(reference.lease_id):
            return
        if reference != self.open(reference.lease_id):
            raise TemporaryLeaseError("temporary.policy", "temporary lease reference changed")
        self._dispose_locked(reference, drained)

    def reconcile(self, lease_id: str, *, drained: Callable[[], bool]) -> None:
        """Resume disposal only after the owner newly proves processes drained."""
        reference = self._reference(self._reservation(lease_id))
        nonce = _nonce(lease_id)
        try:
            with private_record_lock(self._directory("leases") / f"{nonce}.lock"):
                if self._disposed(lease_id):
                    return
                if not self._path("disposal-intents", nonce).exists():
                    self.open(lease_id)
                self._dispose_locked(reference, drained)
        except DurableRecordError as exc:
            if exc.code == "busy":
                raise TemporaryLeaseError("temporary.busy", "temporary lease is active") from exc
            raise TemporaryLeaseError("temporary.unsafe", "temporary lease lock is unsafe") from exc


__all__ = ["CoreTemporaryLeases", "TemporaryLeaseError", "TemporaryLeaseReference"]
