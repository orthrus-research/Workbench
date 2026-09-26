"""Candidate-scoped write-ahead evidence for resources born under a fresh root.

The two logs live in the configuration home and the selected workspace. Neither
log upgrades the V1 cleanup guard or claims that pre-epoch output is known.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
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
WORKSPACE_EPOCH_KIND = "workbench-resource-workspace-epoch-v2"
WORKSPACE_EPOCH_NAME = "workspace-epoch.json"
WORKSPACE_EPOCH_LOCK = "workspace-epoch.lock"
_FILE = re.compile(r"[0-9]{16}\.json\Z")
_EPOCH = re.compile(r"[0-9a-f]{32}\Z")
_RESOURCE = re.compile(r"workbench-resource-v1:[0-9a-f]{32}\Z")
_MAX_ROW_BYTES = 4096


def _workspace_root(workspace: Path) -> Path:
    return workspace / ".workbench/resource-issuance-v2"


def _paths(catalog, root_record: Mapping[str, object], workspace: Path) -> tuple[Path, Path, Path]:
    epoch = str(root_record["root_epoch"])
    workspace_id = sha256(os.fsencode(workspace)).hexdigest()
    config_rows = catalog.configuration_home / "resource-issuance-v2" / epoch / workspace_id
    workspace_root = _workspace_root(workspace)
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


def _workspace_epoch_record(catalog, root_record: Mapping[str, object], workspace: Path) -> dict:
    return check_storage.seal(WORKSPACE_EPOCH_KIND, {
        "format": WORKSPACE_EPOCH_KIND, "schema_version": 2,
        "root_id": root_record["id"], "root_epoch": root_record["root_epoch"],
        "configuration_home": str(catalog.configuration_home), "workspace": str(workspace),
    })


def _workspace_epoch_state(catalog, workspace: Path, root_record: Mapping[str, object] | None) -> dict[str, object]:
    """Inspect the independent epoch latch without creating or adopting rows."""
    root = _workspace_root(workspace)
    selected_epoch = root_record["root_epoch"] if root_record is not None else None
    if not root.exists() and not root.is_symlink():
        return {
            "status": "no-witness", "selected_epoch": selected_epoch,
            "witness_epoch": None, "witness_root_id": None,
            "historical_completeness": "unproven",
        }
    _workspace_parents(workspace, root)
    _directory(root, create=False)
    epochs: set[str] = set()
    epoch_directories: set[str] = set()
    epoch_leases: set[str] = set()
    anchor_path = root / WORKSPACE_EPOCH_NAME
    try:
        for path in root.iterdir():
            if path.name == WORKSPACE_EPOCH_NAME:
                continue
            if path.name == WORKSPACE_EPOCH_LOCK or (
                path.name.endswith(".lock") and _EPOCH.fullmatch(path.name[:-5]) is not None
            ):
                if read_private_single_link_bytes(path, byte_limit=0) != b"":
                    raise DurableResourceError("resource.changed", "resource issuance epoch lease is not empty")
                if path.name != WORKSPACE_EPOCH_LOCK:
                    epochs.add(path.name[:-5])
                    epoch_leases.add(path.name[:-5])
            elif _EPOCH.fullmatch(path.name) is not None:
                _directory(path, create=False)
                epochs.add(path.name)
                epoch_directories.add(path.name)
            else:
                raise DurableResourceError("resource.changed", "resource issuance has an unknown workspace entry")
        anchor = None
        if anchor_path.exists() or anchor_path.is_symlink():
            raw = read_private_single_link_bytes(anchor_path, byte_limit=_MAX_ROW_BYTES)
            anchor = json.loads(raw)
            expected_keys = {
                "id", "format", "schema_version", "root_id", "root_epoch",
                "configuration_home", "workspace",
            }
            if (not isinstance(anchor, dict) or set(anchor) != expected_keys
                    or anchor["format"] != WORKSPACE_EPOCH_KIND
                    or type(anchor["schema_version"]) is not int or anchor["schema_version"] != 2
                    or not isinstance(anchor["root_epoch"], str)
                    or _EPOCH.fullmatch(anchor["root_epoch"]) is None
                    or not isinstance(anchor["root_id"], str)
                    or not isinstance(anchor["configuration_home"], str)
                    or not Path(anchor["configuration_home"]).is_absolute()
                    or anchor["workspace"] != str(workspace)
                    or anchor != check_storage.seal(
                        WORKSPACE_EPOCH_KIND, {key: value for key, value in anchor.items() if key != "id"},
                    )
                    or raw != check_storage.canonical(anchor) + b"\n"):
                raise DurableResourceError("resource.changed", "resource issuance workspace epoch changed")
            epochs.add(anchor["root_epoch"])
    except DurableResourceError:
        raise
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise DurableResourceError("resource.changed", "resource issuance workspace epoch is unavailable") from exc
    if anchor is not None and (
        anchor["root_epoch"] not in epoch_directories
        or anchor["root_epoch"] not in epoch_leases
    ):
        status = "incomplete-epoch"
    elif not epochs:
        status = "no-witness"
    elif (root_record is not None and epochs == {selected_epoch}
          and (anchor is None or anchor == _workspace_epoch_record(catalog, root_record, workspace))):
        status = "selected-epoch" if anchor is not None else "unanchored-selected-epoch"
    else:
        status = "foreign-or-lost-epoch"
    return {
        "status": status, "selected_epoch": selected_epoch,
        "witness_epoch": anchor["root_epoch"] if anchor is not None else (next(iter(epochs)) if len(epochs) == 1 else None),
        "witness_root_id": anchor["root_id"] if anchor is not None else None,
        "historical_completeness": "unproven",
    }


def inspect_workspace_epoch(catalog, workspace: Path) -> dict[str, object]:
    """Report retained workspace V2 evidence even after the selected home is lost."""
    if not isinstance(workspace, Path) or not workspace.is_absolute():
        raise DurableResourceError("resource.policy", "select an absolute issuance workspace")
    _workspace_parents(workspace, workspace)
    root_record = catalog.fresh_root_epoch()
    lock = _workspace_root(workspace) / WORKSPACE_EPOCH_LOCK
    if lock.exists() or lock.is_symlink():
        with _read_lock(lock):
            return _workspace_epoch_state(catalog, workspace, root_record)
    return _workspace_epoch_state(catalog, workspace, root_record)


def preflight_workspace_epoch(catalog, workspace: Path) -> None:
    # An already selected V2 root is fenced by issue() under the common lease.
    # This early check matters before a new configuration home can mint a root.
    if catalog.fresh_root_epoch() is not None:
        return
    state = inspect_workspace_epoch(catalog, workspace)
    if state["status"] not in {"no-witness", "selected-epoch", "unanchored-selected-epoch"}:
        raise DurableResourceError(
            "resource.changed", "resource issuance workspace retains another root epoch",
        )


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


def _reservation_absent(catalog, row: Mapping[str, object]) -> bool:
    nonce = str(row["resource_id"]).split(":", 1)[1]
    try:
        catalog._path("reservations", nonce).lstat()
    except FileNotFoundError:
        return True
    except OSError as exc:
        raise DurableResourceError("resource.unavailable", "resource issuance reservation cannot be inspected") from exc
    return False


def inspect_gap(catalog, root_record: Mapping[str, object], workspace: Path) -> dict[str, object]:
    """Report visible V2 issue gaps; the lease fences issues, not reservations."""
    if _workspace_epoch_state(catalog, workspace, root_record)["status"] not in {
        "no-witness", "selected-epoch", "unanchored-selected-epoch",
    }:
        raise DurableResourceError("resource.changed", "resource issuance workspace retains another root epoch")
    config_dir, workspace_dir, lock = _paths(catalog, root_record, workspace)
    if not any(path.exists() or path.is_symlink() for path in (config_dir, workspace_dir, lock)):
        raise DurableResourceError("resource.unavailable", "no retained resource issuance witness exists")
    with _read_lock(lock):
        if not all(path.exists() or path.is_symlink() for path in (config_dir, workspace_dir)):
            raise DurableResourceError("resource.changed", "resource issuance lost a ledger directory")
        _workspace_parents(workspace, workspace_dir)
        config_rows = _rows(config_dir, root_record, workspace)
        workspace_rows = _rows(workspace_dir, root_record, workspace)
        if not config_rows and not workspace_rows:
            raise DurableResourceError("resource.unavailable", "empty resource issuance ledgers cannot prove history")
        if len({row["resource_id"] for row in workspace_rows}) != len(workspace_rows):
            raise DurableResourceError("resource.changed", "resource issuance identity was reused")
        if config_rows == workspace_rows:
            if workspace_rows and _reservation_absent(catalog, workspace_rows[-1]):
                _match_reservations(catalog, workspace_rows[:-1])
                trailing = workspace_rows[-1]
                status = "current-paired-unreserved-tail"
            else:
                _match_reservations(catalog, workspace_rows)
                trailing = None
                status = "current-paired-prefix"
        elif (len(workspace_rows) == len(config_rows) + 1
              and workspace_rows[:-1] == config_rows):
            _match_reservations(catalog, config_rows)
            trailing = workspace_rows[-1]
            if not _reservation_absent(catalog, trailing):
                raise DurableResourceError(
                    "resource.changed", "one-sided resource issuance has a reservation",
                )
            status = "current-workspace-only-unreserved-tail"
        else:
            raise DurableResourceError("resource.changed", "resource issuance ledgers differ")
        if catalog.fresh_root_epoch() != root_record:
            raise DurableResourceError("resource.changed", "resource issuance root epoch changed")
        if _workspace_epoch_state(catalog, workspace, root_record)["status"] not in {
            "selected-epoch", "unanchored-selected-epoch",
        }:
            raise DurableResourceError("resource.changed", "resource issuance workspace epoch changed")
        return {
            "status": status, "paired_rows": len(config_rows),
            "issue_id": trailing["id"] if trailing is not None else None,
            "resource_id": trailing["resource_id"] if trailing is not None else None,
            "historical_completeness": "unproven",
        }


def inspect_current_resource_join(
    catalog, root_record: Mapping[str, object], workspace: Path,
) -> dict[str, object]:
    """Join retained V2 issues to one current catalog view without certifying history.

    The leases fence opt-in issue appends. Ordinary file publication and other
    catalog families do not share them, so even an empty unmatched list is not
    a complete-history or cleanup claim.
    """
    _, _, issue_lock = _paths(catalog, root_record, workspace)
    epoch_lock = _workspace_root(workspace) / WORKSPACE_EPOCH_LOCK
    with ExitStack() as stack:
        if epoch_lock.exists() or epoch_lock.is_symlink():
            stack.enter_context(_read_lock(epoch_lock))
        before = _workspace_epoch_state(catalog, workspace, root_record)
        if before["status"] not in {"no-witness", "selected-epoch", "unanchored-selected-epoch"}:
            raise DurableResourceError(
                "resource.changed", "resource issuance workspace retains another root epoch",
            )
        if issue_lock.exists() or issue_lock.is_symlink():
            stack.enter_context(_read_lock(issue_lock))
        rows = _paired_rows(catalog, root_record, workspace)
        _match_reservations(catalog, rows)
        inventory = catalog.inventory(workspace=workspace)
        resources = inventory["resources"]
        issue_by_resource = {row["resource_id"]: row for row in rows}
        if len(issue_by_resource) != len(rows):
            raise DurableResourceError("resource.changed", "resource issuance identity was reused")
        resource_by_id = {row["resource_id"]: row for row in resources}
        if len(resource_by_id) != len(resources) or set(issue_by_resource) - set(resource_by_id):
            raise DurableResourceError("resource.changed", "resource issuance is missing from current catalog")
        if (_paired_rows(catalog, root_record, workspace) != rows
                or _workspace_epoch_state(catalog, workspace, root_record) != before
                or catalog.fresh_root_epoch() != root_record):
            raise DurableResourceError("resource.changed", "resource issuance changed during inspection")
    return {
        "format": "workbench-resource-issuance-current-join-v1",
        "workspace": str(workspace), "root_epoch": root_record["root_epoch"],
        "root_state": inventory["root_state"],
        "witness_state": before["status"],
        "historical_completeness": "unproven", "cleanup_authority": "none",
        "resource_inventory_fenced": False,
        "resources": [
            {
                "resource_id": resource_id,
                "catalog_status": resource_by_id[resource_id]["status"],
                "retained_issue_id": issue_by_resource[resource_id]["id"]
                if resource_id in issue_by_resource else None,
            }
            for resource_id in sorted(resource_by_id)
        ],
        "unmatched_resource_ids": sorted(set(resource_by_id) - set(issue_by_resource)),
    }


def issue(catalog, root_record: Mapping[str, object], reservation: Mapping[str, object]) -> dict:
    """Publish both ordered rows before writing the reservation or target."""
    workspace = Path(str(reservation["workspace"]))
    _workspace_parents(workspace, workspace)
    config_dir, workspace_dir, lock = _paths(catalog, root_record, workspace)
    workspace_root = _workspace_root(workspace)
    _directory(workspace_root, create=True)
    _workspace_parents(workspace, workspace_root)
    with private_record_lock(workspace_root / WORKSPACE_EPOCH_LOCK, wait=True):
        state = _workspace_epoch_state(catalog, workspace, root_record)
        if state["status"] not in {"no-witness", "selected-epoch", "unanchored-selected-epoch"}:
            raise DurableResourceError(
                "resource.changed", "resource issuance workspace retains another root epoch",
            )
        prior = (config_dir.exists() or config_dir.is_symlink()
                 or workspace_dir.exists() or workspace_dir.is_symlink())
        if state["status"] == "selected-epoch" and not (
            config_dir.is_dir() and workspace_dir.is_dir() and lock.is_file()
        ):
            raise DurableResourceError("resource.changed", "resource issuance epoch lost its paired ledger")
        if prior and not (
            config_dir.is_dir() and workspace_dir.is_dir() and lock.is_file()
        ):
            raise DurableResourceError("resource.changed", "resource issuance paired ledger is incomplete")
        if lock.exists() and not prior:
            raise DurableResourceError("resource.changed", "resource issuance rows were lost")
        _directory(config_dir, create=True)
        _directory(workspace_dir, create=True)
        _workspace_parents(workspace, workspace_dir)
        with private_record_lock(lock, wait=True):
            rows = _paired_rows(catalog, root_record, workspace)
            _match_reservations(catalog, rows)
            anchor = workspace_root / WORKSPACE_EPOCH_NAME
            if state["status"] == "no-witness" or state["status"] == "unanchored-selected-epoch":
                epoch_record = _workspace_epoch_record(catalog, root_record, workspace)
                publish_immutable_bytes(
                    anchor, check_storage.canonical(epoch_record) + b"\n",
                    byte_limit=_MAX_ROW_BYTES,
                )
            if _workspace_epoch_state(catalog, workspace, root_record)["status"] != "selected-epoch":
                raise DurableResourceError("resource.changed", "resource issuance epoch did not reopen exactly")
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
    if _workspace_epoch_state(catalog, workspace, root_record)["status"] not in {
        "no-witness", "selected-epoch", "unanchored-selected-epoch",
    }:
        raise DurableResourceError("resource.changed", "resource issuance workspace retains another root epoch")
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
        if _workspace_epoch_state(catalog, workspace, root_record)["status"] not in {
            "selected-epoch", "unanchored-selected-epoch",
        }:
            raise DurableResourceError("resource.changed", "resource issuance workspace epoch changed")
