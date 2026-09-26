"""Core custody records for exact, retained IDE toolchain trees.

An admission binds a locked archive to the original extracted directory inode
and the member inventory observed under pinned handles. Every use must repeat
the archive-to-tree readback. Admission neither repairs a tree nor authorizes
its cleanup, and historical trees can be admitted after exact readback.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from workbench_api.host_filesystem import DurableRecordError

from . import check_storage
from .durable_records import (
    private_record_lock, publish_immutable_bytes, read_private_single_link_bytes,
)
from .ide_toolchain_reader import inspect_ide_toolchain_tree
from .host_filesystem import private_path
from .storage.record_stores import CoreRecordStores
from .temporary_leases import CoreTemporaryLeases, TemporaryLeaseError


_FORMAT = "workbench-ide-toolchain-admission-v1"
_LIMIT = 8192
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_STAGE = re.compile(r"workbench-temporary-lease-v1:[0-9a-f]{32}\Z")


class IdeToolchainAdmissionError(ValueError):
    pass


def _record_path(store: Path, destination: Path) -> Path:
    key = sha256(os.fsencode(destination)).hexdigest()
    return store / f"{key}.json"


def _read_record(path: Path) -> dict[str, Any]:
    try:
        raw = read_private_single_link_bytes(path, byte_limit=_LIMIT)
        record = json.loads(raw)
        if (
            not isinstance(record, dict)
            or record.get("format") != _FORMAT
            or raw != check_storage.canonical(record) + b"\n"
            or record != check_storage.seal(
                _FORMAT, {key: value for key, value in record.items() if key != "id"},
            )
        ):
            raise IdeToolchainAdmissionError("IDE toolchain admission changed")
        return record
    except (OSError, ValueError, TypeError) as exc:
        if isinstance(exc, IdeToolchainAdmissionError):
            raise
        raise IdeToolchainAdmissionError("IDE toolchain admission is unavailable or changed") from exc


class CoreIdeToolchainAdmissions:
    """Admit and reopen one historical target under a registered Core store."""

    def __init__(self, toolchain_root: Path):
        if (
            not isinstance(toolchain_root, Path)
            or not toolchain_root.is_absolute()
            or ".." in toolchain_root.parts
        ):
            raise IdeToolchainAdmissionError("IDE toolchain root must be an absolute selected path")
        self.toolchain_root = toolchain_root
        self.workspace = toolchain_root.parent
        self.configuration_home = self.workspace / ".ide-toolchain-core"

    def _source_stages(self, destination: Path, archive_sha256: str) -> list[dict]:
        """Read matching Core stages; a missing admission cannot adopt one."""

        try:
            rows = CoreTemporaryLeases.inventory_catalog(
                self.configuration_home, workspace=self.workspace,
            )
        except TemporaryLeaseError as exc:
            raise IdeToolchainAdmissionError("IDE toolchain source stages need review") from exc
        prefix = f"ide-{archive_sha256}-"
        return [row for row in rows if (
            row["owner_id"] == "validation"
            and row["role"] == "ide-toolchain"
            and Path(row["path"]).parent == destination.parent
            and Path(row["path"]).name.startswith(prefix)
        )]

    def inventory_catalog(self) -> list[dict[str, Any]]:
        """Read current admission rows and interrupted stages without opening trees."""

        directory = self.configuration_home / "admissions-v1"
        if not directory.exists() and not directory.is_symlink():
            return []
        try:
            if not private_path(directory, directory=True):
                raise IdeToolchainAdmissionError("IDE admission store is not owner-private")
            rows: list[dict[str, Any]] = []
            for path in sorted(directory.iterdir()):
                info = path.lstat()
                if (
                    not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or not private_path(path, directory=False)
                ):
                    raise IdeToolchainAdmissionError("IDE admission store has an unsafe entry")
                if re.fullmatch(r"[0-9a-f]{64}\.lock", path.name):
                    continue  # The lock can precede the first admission row.
                if re.fullmatch(r"\.[0-9a-f]{64}\.json\.[a-z0-9_]{8}", path.name):
                    rows.append({
                        "path": str(path), "status": "unbound-record-stage",
                        "retention": "protected-until-reviewed-policy",
                    })
                    continue
                if re.fullmatch(r"[0-9a-f]{64}\.json", path.name) is None:
                    raise IdeToolchainAdmissionError("IDE admission store has an unknown entry")
                record = _read_record(path)
                required = {
                    "id", "format", "store_id", "workspace", "target",
                    "target_device", "target_inode", "archive_sha256",
                    "archive_size", "expected_root", "archive_format",
                    "regular_files", "members_sha256", "stage_lease_id", "retention",
                }
                target = Path(record.get("target", ""))
                if (
                    set(record) != required
                    or record["workspace"] != str(self.workspace)
                    or target.parent != self.toolchain_root
                    or ".." in target.parts
                    or path != _record_path(directory, target)
                    or not isinstance(record["store_id"], str)
                    or not record["store_id"].startswith("workbench-record-store-v1:sha256:")
                    or any(type(record[field]) is not int or record[field] < 0 for field in (
                        "target_device", "target_inode", "archive_size", "regular_files",
                    ))
                    or record["archive_size"] == 0
                    or record["regular_files"] == 0
                    or not isinstance(record["archive_sha256"], str)
                    or _DIGEST.fullmatch(record["archive_sha256"]) is None
                    or not isinstance(record["members_sha256"], str)
                    or _DIGEST.fullmatch(record["members_sha256"]) is None
                    or record["archive_format"] not in {"zip", "tar"}
                    or not isinstance(record["expected_root"], str)
                    or record["expected_root"] in {"", ".", ".."}
                    or "/" in record["expected_root"]
                    or "\\" in record["expected_root"]
                    or (record["stage_lease_id"] is not None and (
                        not isinstance(record["stage_lease_id"], str)
                        or _STAGE.fullmatch(record["stage_lease_id"]) is None
                    ))
                    or record["retention"] != "protected-until-reviewed-policy"
                ):
                    raise IdeToolchainAdmissionError("IDE admission identity changed")
                rows.append({
                    "path": str(path), "target": str(target),
                    "store_id": record["store_id"],
                    "stage_lease_id": record["stage_lease_id"],
                    "archive_sha256": record["archive_sha256"],
                    "target_device": record["target_device"],
                    "target_inode": record["target_inode"],
                    "status": "catalog-only",
                    "retention": record["retention"],
                })
            return rows
        except (OSError, ValueError, TypeError) as exc:
            if isinstance(exc, IdeToolchainAdmissionError):
                raise
            raise IdeToolchainAdmissionError("IDE admission catalog is unavailable or changed") from exc

    def admit(
        self, archive: Path, destination: Path, *, archive_sha256: str,
        archive_size: int, expected_root: str, archive_format: str,
        stage_lease_id: str | None = None,
    ) -> dict[str, Any]:
        if (
            not isinstance(destination, Path)
            or not destination.is_absolute()
            or ".." in destination.parts
            or destination.parent != self.toolchain_root
            or not isinstance(archive, Path)
            or not archive.is_absolute()
            or ".." in archive.parts
            or type(archive_sha256) is not str
            or _DIGEST.fullmatch(archive_sha256) is None
            or (stage_lease_id is not None and (
                not isinstance(stage_lease_id, str)
                or _STAGE.fullmatch(stage_lease_id) is None
            ))
        ):
            raise IdeToolchainAdmissionError("IDE toolchain admission policy is invalid")
        try:
            store = CoreRecordStores(
                workspace=self.workspace, configuration_home=self.configuration_home,
                owner_id="validation",
            ).open("validation-ide-toolchain-admissions-v1", self.workspace)
            path = _record_path(store.root, destination)
            with private_record_lock(path.with_suffix(".lock"), wait=True):
                stages = self._source_stages(destination, archive_sha256)
                live = [row for row in stages if row["status"] != "disposed"]
                retained = _read_record(path) if path.exists() or path.is_symlink() else None
                if retained is None:
                    if stage_lease_id is None and live:
                        raise IdeToolchainAdmissionError(
                            "missing IDE admission has a retained Core source stage",
                        )
                    if stage_lease_id is not None and (
                        len(live) != 1
                        or live[0]["lease_id"] != stage_lease_id
                        or live[0]["status"] != "active-or-abandoned"
                    ):
                        raise IdeToolchainAdmissionError(
                            "fresh IDE admission differs from its active Core source stage",
                        )
                else:
                    original_stage = retained.get("stage_lease_id")
                    if original_stage is None and live:
                        raise IdeToolchainAdmissionError(
                            "historical IDE admission has an unclaimed Core source stage",
                        )
                    if original_stage is not None and (
                        len(live) != 1
                        or live[0]["lease_id"] != original_stage
                        or live[0]["status"] != "retained-unproven"
                    ):
                        raise IdeToolchainAdmissionError(
                            "IDE admission source stage is unavailable or changed",
                        )
                readback = inspect_ide_toolchain_tree(
                    archive, destination, archive_sha256=archive_sha256,
                    archive_size=archive_size, expected_root=expected_root,
                    archive_format=archive_format,
                )
                body = {
                    "format": _FORMAT,
                    "store_id": store.store_id,
                    "workspace": str(self.workspace),
                    "target": str(destination),
                    "target_device": readback.device,
                    "target_inode": readback.inode,
                    "archive_sha256": archive_sha256,
                    "archive_size": archive_size,
                    "expected_root": expected_root,
                    "archive_format": archive_format,
                    "regular_files": readback.files,
                    "members_sha256": readback.members_sha256,
                    "stage_lease_id": stage_lease_id,
                    "retention": "protected-until-reviewed-policy",
                }
                expected = check_storage.seal(_FORMAT, body)
                if retained is not None:
                    # The first successful admission retains its provenance.
                    # Later callers need only match its physical/lock binding.
                    if any(
                        retained.get(key) != value
                        for key, value in body.items() if key != "stage_lease_id"
                    ) or (stage_lease_id is not None and retained.get("stage_lease_id") != stage_lease_id):
                        raise IdeToolchainAdmissionError(
                            "retained IDE toolchain admission differs from the selected tree",
                        )
                    return retained
                publish_immutable_bytes(
                    path, check_storage.canonical(expected) + b"\n",
                    byte_limit=_LIMIT,
                )
                if _read_record(path) != expected:
                    raise IdeToolchainAdmissionError("IDE toolchain admission changed after publication")
                return expected
        except (DurableRecordError, OSError, ValueError) as exc:
            if isinstance(exc, IdeToolchainAdmissionError):
                raise
            raise IdeToolchainAdmissionError(f"Core IDE toolchain admission needs review: {exc}") from exc


__all__ = ["CoreIdeToolchainAdmissions", "IdeToolchainAdmissionError"]
