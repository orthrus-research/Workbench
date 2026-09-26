"""Candidate-scoped write-ahead evidence for resources born under a fresh root.

The two logs live in the configuration home and the selected workspace. Neither
log upgrades the V1 cleanup guard or claims that pre-epoch output is known.
"""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Iterator, Mapping

from workbench_api.durable_resources import DurableResourceError

from .. import check_storage
from ..durable_records import private_record_lock, publish_immutable_bytes, read_private_single_link_bytes
from ..host_filesystem import file_lease, private_path, secure_private_path
from ..output_routing import _private_directory


ISSUE_KIND = "workbench-resource-issuance-v2"
_FILE = re.compile(r"[0-9]{16}\.json\Z")
_RESOURCE = re.compile(r"workbench-resource-v1:[0-9a-f]{32}\Z")
_MAX_ROW_BYTES = 4096


def _paths(catalog, root_record: Mapping[str, object], workspace: Path) -> tuple[Path, Path, Path]:
    epoch = str(root_record["root_epoch"])
    workspace_id = sha256(os.fsencode(workspace)).hexdigest()
    config_rows = catalog.configuration_home / "resource-issuance-v2" / epoch / workspace_id
    workspace_root = workspace / ".workbench/resource-issuance-v2"
    return config_rows, workspace_root / epoch, workspace_root / (epoch + ".lock")


def _directory(path: Path, *, create: bool) -> None:
    if not path.exists() and not path.is_symlink():
        if not create:
            raise DurableResourceError("resource.unavailable", "resource issuance ledger is unavailable")
        _private_directory(path)
        secure_private_path(path, directory=True)
    if any(parent.is_symlink() or getattr(parent, "is_junction", lambda: False)()
           for parent in (path, *path.parents)):
        raise DurableResourceError("resource.changed", "resource issuance ledger crosses a redirected path")
    if not private_path(path, directory=True):
        raise DurableResourceError("resource.changed", "resource issuance ledger lost private custody")


def _workspace_parents(workspace: Path, directory: Path) -> None:
    """Refuse a writable parent that could replace the workspace-side witness."""
    cursor = directory
    while True:
        try:
            info = cursor.lstat()
        except OSError as exc:
            raise DurableResourceError("resource.unavailable", "resource issuance parent is unavailable") from exc
        if (not stat.S_ISDIR(info.st_mode) or cursor.is_symlink()
                or getattr(cursor, "is_junction", lambda: False)()):
            raise DurableResourceError("resource.changed", "resource issuance parent is redirected")
        if os.name == "nt":
            if not private_path(cursor, directory=True):
                raise DurableResourceError("resource.changed", "resource issuance parent is not owner-private")
        elif (info.st_mode & 0o022) or (hasattr(os, "geteuid") and info.st_uid != os.geteuid()):
            raise DurableResourceError("resource.changed", "resource issuance parent is writable by another user")
        if cursor == workspace:
            return
        if cursor == cursor.parent or not cursor.is_relative_to(workspace):
            raise DurableResourceError("resource.changed", "resource issuance escaped its workspace")
        cursor = cursor.parent


def preflight_workspace(workspace: Path) -> None:
    """Reject an unsafe existing parent before electing a V2 root epoch."""
    _workspace_parents(workspace, workspace)
    for path in (
        workspace / ".workbench",
        workspace / ".workbench/resource-issuance-v2",
    ):
        if path.exists() or path.is_symlink():
            _workspace_parents(workspace, path)


@contextmanager
def _read_lock(path: Path) -> Iterator[None]:
    try:
        _directory(path.parent, create=False)
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not private_path(path, directory=False):
            raise DurableResourceError("resource.changed", "resource issuance lease is unsafe")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise DurableResourceError("resource.changed", "resource issuance lease changed")
            with file_lease(descriptor, exclusive=False):
                yield
                after = path.lstat()
                if (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
                    raise DurableResourceError("resource.changed", "resource issuance lease was replaced")
        finally:
            os.close(descriptor)
    except BlockingIOError as exc:
        raise DurableResourceError("resource.busy", "resource issuance is active") from exc
    except FileNotFoundError as exc:
        raise DurableResourceError("resource.unavailable", "resource issuance lease is unavailable") from exc


def _rows(directory: Path, root_record: Mapping[str, object], workspace: Path) -> list[dict]:
    _directory(directory, create=False)
    try:
        entries = sorted(directory.iterdir())
    except OSError as exc:
        raise DurableResourceError("resource.unavailable", "resource issuance ledger cannot be listed") from exc
    rows = []
    previous = str(root_record["id"])
    for sequence, path in enumerate(entries, 1):
        if _FILE.fullmatch(path.name) is None or path.name != f"{sequence:016d}.json":
            raise DurableResourceError("resource.changed", "resource issuance sequence has a gap or unknown entry")
        try:
            raw = read_private_single_link_bytes(path, byte_limit=_MAX_ROW_BYTES)
            row = json.loads(raw)
        except (OSError, ValueError, TypeError) as exc:
            raise DurableResourceError("resource.changed", "resource issuance row is unavailable or changed") from exc
        required = {
            "id", "format", "schema_version", "root_id", "root_epoch", "configuration_home",
            "workspace", "sequence", "previous_id", "resource_id", "reservation_id",
            "owner_id", "target",
        }
        if (not isinstance(row, dict) or set(row) != required
                or row["format"] != ISSUE_KIND or type(row["schema_version"]) is not int
                or row["schema_version"] != 2 or type(row["sequence"]) is not int
                or row["sequence"] != sequence or row["previous_id"] != previous
                or row["root_id"] != root_record["id"]
                or row["root_epoch"] != root_record["root_epoch"]
                or row["configuration_home"] != root_record["configuration_home"]
                or row["workspace"] != str(workspace)
                or not isinstance(row["resource_id"], str)
                or _RESOURCE.fullmatch(row["resource_id"]) is None
                or not isinstance(row["reservation_id"], str)
                or not isinstance(row["owner_id"], str)
                or not isinstance(row["target"], str) or not Path(row["target"]).is_absolute()
                or row != check_storage.seal(ISSUE_KIND, {key: value for key, value in row.items() if key != "id"})
                or raw != check_storage.canonical(row) + b"\n"):
            raise DurableResourceError("resource.changed", "resource issuance row identity changed")
        rows.append(row)
        previous = row["id"]
    return rows


def _paired_rows(catalog, root_record: Mapping[str, object], workspace: Path) -> list[dict]:
    config_dir, workspace_dir, lock = _paths(catalog, root_record, workspace)
    config_exists = config_dir.exists() or config_dir.is_symlink()
    workspace_exists = workspace_dir.exists() or workspace_dir.is_symlink()
    if not config_exists and not workspace_exists:
        if lock.exists() or lock.is_symlink():
            raise DurableResourceError("resource.changed", "resource issuance rows were lost")
        return []
    if config_exists != workspace_exists:
        raise DurableResourceError("resource.changed", "resource issuance has only one surviving ledger")
    _workspace_parents(workspace, workspace_dir)
    config_rows = _rows(config_dir, root_record, workspace)
    workspace_rows = _rows(workspace_dir, root_record, workspace)
    if config_rows != workspace_rows:
        raise DurableResourceError("resource.changed", "resource issuance ledgers differ")
    return config_rows


def _match_reservations(catalog, rows: list[dict]) -> None:
    if len({row["resource_id"] for row in rows}) != len(rows):
        raise DurableResourceError("resource.changed", "resource issuance identity was reused")
    for row in rows:
        try:
            reservation = catalog._reservation(row["resource_id"])
        except DurableResourceError as exc:
            raise DurableResourceError("resource.incomplete", "resource issuance has no exact reservation") from exc
        if (reservation["id"] != row["reservation_id"]
                or reservation["workspace"] != row["workspace"]
                or reservation["owner_id"] != row["owner_id"]
                or str(catalog._target(reservation)) != row["target"]):
            raise DurableResourceError("resource.changed", "resource issuance differs from its reservation")


def issue(catalog, root_record: Mapping[str, object], reservation: Mapping[str, object]) -> dict:
    """Publish both ordered rows before writing the reservation or target."""
    workspace = Path(str(reservation["workspace"]))
    _workspace_parents(workspace, workspace)
    config_dir, workspace_dir, lock = _paths(catalog, root_record, workspace)
    prior = (config_dir.exists() or config_dir.is_symlink()
             or workspace_dir.exists() or workspace_dir.is_symlink())
    if prior and not lock.is_file():
        raise DurableResourceError("resource.changed", "resource issuance lease was lost")
    if lock.exists() and not prior:
        raise DurableResourceError("resource.changed", "resource issuance rows were lost")
    _directory(config_dir, create=True)
    _directory(workspace_dir, create=True)
    _workspace_parents(workspace, workspace_dir)
    with private_record_lock(lock, wait=True):
        rows = _paired_rows(catalog, root_record, workspace)
        _match_reservations(catalog, rows)
        sequence = len(rows) + 1
        row = check_storage.seal(ISSUE_KIND, {
            "format": ISSUE_KIND, "schema_version": 2,
            "root_id": root_record["id"], "root_epoch": root_record["root_epoch"],
            "configuration_home": str(catalog.configuration_home), "workspace": str(workspace),
            "sequence": sequence,
            "previous_id": rows[-1]["id"] if rows else root_record["id"],
            "resource_id": reservation["resource_id"], "reservation_id": reservation["id"],
            "owner_id": reservation["owner_id"], "target": str(catalog._target(reservation)),
        })
        raw = check_storage.canonical(row) + b"\n"
        name = f"{sequence:016d}.json"
        publish_immutable_bytes(workspace_dir / name, raw, byte_limit=_MAX_ROW_BYTES)
        publish_immutable_bytes(config_dir / name, raw, byte_limit=_MAX_ROW_BYTES)
        if _paired_rows(catalog, root_record, workspace)[-1] != row:
            raise DurableResourceError("resource.changed", "resource issuance did not reopen exactly")
        return row


@contextmanager
def verified_candidate(catalog, root_record: Mapping[str, object], *, workspace: Path,
                       resource_id: str, owner_id: str, target: Path) -> Iterator[dict | None]:
    """Hold the read-only witness lease through one candidate's Core proof."""
    config_dir, workspace_dir, lock = _paths(catalog, root_record, workspace)
    if not any(path.exists() or path.is_symlink() for path in (config_dir, workspace_dir, lock)):
        yield None
        return
    with _read_lock(lock):
        rows = _paired_rows(catalog, root_record, workspace)
        _match_reservations(catalog, rows)
        matched = [row for row in rows if row["resource_id"] == resource_id]
        if not matched:
            yield None
            return
        if len(matched) != 1:
            raise DurableResourceError("resource.changed", "resource issuance identity was reused")
        row = matched[0]
        if row["owner_id"] != owner_id or row["target"] != str(target):
            yield None
            return
        yield row
