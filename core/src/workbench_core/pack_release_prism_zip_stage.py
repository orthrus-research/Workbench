"""Review and stage a writable WSL 9p Prism ZIP into private Linux Core state.

The existing ZIP importer remains strict about its source filesystem. This
bridge copies one exact, pinned source file so the importer can use the same
Linux path for its review and publication. Interrupted sibling stages remain
private and are never mistaken for the completed source-bound result.
"""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, Iterator, Mapping
from uuid import uuid4

from .durable_files import _directory as pinned_directory, _visible_parent
from .durable_records import private_record_lock, read_private_single_link_bytes
from .environment_package_install_plan import _SUPPORTED_FILESYSTEMS, _mount_type
from .host_filesystem import private_path, promote_prepared_directory
from .output_routing import _private_directory
from .pack_release_local import _canonical, _object


PLAN_FORMAT = "workbench-prism-zip-stage-plan-v1"
RECEIPT_FORMAT = "workbench-prism-zip-stage-receipt-v1"
RESULT_FORMAT = "workbench-prism-zip-stage-result-v1"
_PLAN_PREFIX = "workbench-prism-zip-stage-plan:sha256:"
_PLAN_ID = re.compile(r"workbench-prism-zip-stage-plan:sha256:[0-9a-f]{64}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MAX_ARCHIVE = 4 * 1024 * 1024 * 1024
_CHUNK = 1024 * 1024
_STAGE_INTENT = "stage-intent.json"
_STAT_FIELDS = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")


def _same(left: os.stat_result, right: os.stat_result) -> bool:
    return all(getattr(left, field) == getattr(right, field) for field in _STAT_FIELDS)


def _source_mode(archive: Path) -> tuple[str, str]:
    if (not isinstance(archive, Path) or not archive.is_absolute()
            or any(part in {".", ".."} for part in archive.parts)
            or len(str(archive).encode("utf-8")) > 2048):
        raise ValueError("Prism ZIP staging needs an absolute ordinary source path")
    filesystem = _mount_type(archive.parent)
    if filesystem in _SUPPORTED_FILESYSTEMS:
        return "direct", filesystem
    if filesystem == "9p":
        readonly = bool(os.statvfs(archive.parent).f_flag & os.ST_RDONLY)
        return ("direct" if readonly else "copy"), filesystem
    raise ValueError("Prism ZIP source needs Linux storage or a WSL 9p mount")


@contextmanager
def _held_source(archive: Path) -> Iterator[tuple[int, int]]:
    parent = pinned_directory(archive.parent, create=False)
    descriptor = -1
    try:
        parent_id = (os.fstat(parent).st_dev, os.fstat(parent).st_ino)
        before = os.stat(archive.name, dir_fd=parent, follow_symlinks=False)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 0 < before.st_size <= _MAX_ARCHIVE):
            raise ValueError("Prism ZIP staging needs one bounded ordinary file")
        descriptor = os.open(
            archive.name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0), dir_fd=parent,
        )
        opened = os.fstat(descriptor)
        if not _same(before, opened):
            raise ValueError("Prism ZIP changed before staging review")
        yield descriptor, opened.st_size
        if (not _same(opened, os.fstat(descriptor))
                or not _same(opened, os.stat(archive.name, dir_fd=parent,
                                             follow_symlinks=False))):
            raise ValueError("Prism ZIP changed during staging")
        _visible_parent(archive.parent, parent_id)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def _hash_descriptor(descriptor: int, size: int,
                     check_cancelled: Callable[[], None]) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest, observed = sha256(), 0
    while block := os.read(descriptor, _CHUNK):
        check_cancelled()
        observed += len(block)
        if observed > size:
            raise ValueError("Prism ZIP grew during staging review")
        digest.update(block)
    if observed != size:
        raise ValueError("Prism ZIP changed size during staging review")
    return "sha256:" + digest.hexdigest()


def _stage_root(state_root: Path) -> Path:
    if (not isinstance(state_root, Path) or not state_root.is_absolute()
            or _mount_type(state_root) not in _SUPPORTED_FILESYSTEMS):
        raise ValueError("Prism ZIP staging needs a qualified Linux Core state root")
    return state_root / "pack-release-zip-staging"


def _target(root: Path, plan_id: str) -> Path:
    return root / plan_id.rsplit(":", 1)[-1]


def _plan_body(archive: Path, mode: str, filesystem: str, size: int,
               digest: str) -> dict[str, Any]:
    return {
        "format": PLAN_FORMAT, "schema_version": 1,
        "action": mode, "source_path": str(archive),
        "source_filesystem": filesystem, "source_sha256": digest,
        "source_size": size,
    }


def _archive_path(root: Path, plan_id: str, mode: str, source: Path) -> Path:
    return source if mode == "direct" else _target(root, plan_id) / "archive.zip"


def plan_prism_zip_stage(
    archive: Path, *, state_root: Path,
    check_cancelled: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Return an exact read-only copy decision for one absolute source ZIP."""

    root = _stage_root(state_root)
    mode, filesystem = _source_mode(archive)
    with _held_source(archive) as (descriptor, size):
        digest = _hash_descriptor(descriptor, size, check_cancelled)
    body = _plan_body(archive, mode, filesystem, size, digest)
    plan_id = _PLAN_PREFIX + sha256(_canonical(body)).hexdigest()
    return {**body, "plan_id": plan_id,
            "archive_path": str(_archive_path(root, plan_id, mode, archive))}


def _write_once(path: Path, data: bytes) -> None:
    parent = pinned_directory(path.parent, create=False)
    try:
        descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(descriptor, "wb") as sink:
            sink.write(data)
            sink.flush()
            os.fsync(sink.fileno())
    finally:
        os.close(parent)


def _receipt(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {**{key: value for key, value in plan.items() if key != "archive_path"},
            "format": RECEIPT_FORMAT}


def _verified_copy(root: Path, plan_id: str) -> dict[str, Any]:
    if type(plan_id) is not str or _PLAN_ID.fullmatch(plan_id) is None:
        raise ValueError("select an exact reviewed Prism ZIP stage")
    target = _target(root, plan_id)
    if not private_path(target, directory=True):
        raise ValueError("retained Prism ZIP stage is unavailable or unsafe")
    try:
        raw = read_private_single_link_bytes(target / "receipt.json", byte_limit=4096)
        receipt = _object(raw, "Prism ZIP stage receipt")
    except (OSError, ValueError) as exc:
        raise ValueError("retained Prism ZIP stage receipt is unavailable") from exc
    body = {**receipt, "format": PLAN_FORMAT}
    body.pop("plan_id", None)
    if (set(receipt) != {"format", "schema_version", "action", "source_path",
                            "source_filesystem", "source_sha256", "source_size", "plan_id"}
            or receipt["format"] != RECEIPT_FORMAT
            or receipt["schema_version"] != 1 or receipt["action"] != "copy"
            or receipt["source_filesystem"] != "9p"
            or type(receipt["source_path"]) is not str
            or not Path(receipt["source_path"]).is_absolute()
            or type(receipt["source_size"]) is not int
            or not 0 < receipt["source_size"] <= _MAX_ARCHIVE
            or type(receipt["source_sha256"]) is not str
            or _SHA256.fullmatch(receipt["source_sha256"]) is None
            or receipt["plan_id"] != plan_id
            or _PLAN_PREFIX + sha256(_canonical(body)).hexdigest() != plan_id
            or raw != _canonical(receipt) + b"\n"
            or {entry.name for entry in target.iterdir()} != {"archive.zip", "receipt.json"}):
        raise ValueError("retained Prism ZIP stage identity changed")
    archive = target / "archive.zip"
    with _held_source(archive) as (descriptor, size):
        if size != receipt["source_size"] or _hash_descriptor(descriptor, size, lambda: None) != receipt["source_sha256"]:
            raise ValueError("retained Prism ZIP stage bytes changed")
    return {**receipt, "format": RESULT_FORMAT, "outcome": "reopened",
            "archive_path": str(archive)}


def apply_prism_zip_stage(
    archive: Path, *, state_root: Path, expected_plan_id: str,
    check_cancelled: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Retain a reviewed writable 9p ZIP on Linux; direct sources stay in place."""

    if type(expected_plan_id) is not str or _PLAN_ID.fullmatch(expected_plan_id) is None:
        raise ValueError("select an exact reviewed Prism ZIP stage")
    reviewed = plan_prism_zip_stage(
        archive, state_root=state_root, check_cancelled=check_cancelled,
    )
    if reviewed["plan_id"] != expected_plan_id:
        raise ValueError("Prism ZIP staging inputs changed after review")
    if reviewed["action"] == "direct":
        return {**reviewed, "format": RESULT_FORMAT, "outcome": "direct"}
    root = _stage_root(state_root)
    _private_directory(root)
    if not private_path(root, directory=True):
        raise ValueError("Prism ZIP staging root is not owner-private")
    target = _target(root, expected_plan_id)
    lock_path = root / ("." + expected_plan_id.rsplit(":", 1)[-1] + ".lock")
    with private_record_lock(lock_path, wait=True):
        if target.exists() or target.is_symlink():
            reopened = _verified_copy(root, expected_plan_id)
            if any(reopened[key] != reviewed[key] for key in (
                    "plan_id", "source_path", "source_sha256", "source_size", "archive_path")):
                raise ValueError("retained Prism ZIP belongs to another source")
            return {**reopened, "outcome": "reused"}
        with _held_source(archive) as (source, size):
            if size != reviewed["source_size"]:
                raise ValueError("Prism ZIP changed before staging")
            check_cancelled()
            stage = root / ("." + target.name + ".zip-stage-" + uuid4().hex)
            stage.mkdir(mode=0o700)
            intent = _canonical({"format": PLAN_FORMAT, "plan_id": expected_plan_id}) + b"\n"
            _write_once(stage / _STAGE_INTENT, intent)
            payload = stage / "payload"
            payload.mkdir(mode=0o700)
            destination = payload / "archive.zip"
            parent = pinned_directory(payload, create=False)
            try:
                output = os.open(destination.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                 | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                digest, observed = sha256(), 0
                os.lseek(source, 0, os.SEEK_SET)
                with os.fdopen(output, "wb") as sink:
                    while block := os.read(source, _CHUNK):
                        check_cancelled()
                        observed += len(block)
                        if observed > size:
                            raise ValueError("Prism ZIP grew during staging")
                        sink.write(block)
                        digest.update(block)
                    sink.flush()
                    os.fsync(sink.fileno())
            finally:
                os.close(parent)
            if (observed != size or "sha256:" + digest.hexdigest() != reviewed["source_sha256"]):
                raise ValueError("Prism ZIP changed while staging")
            check_cancelled()
            _write_once(payload / "receipt.json", _canonical(_receipt(reviewed)) + b"\n")
            stage_info = stage.stat()
            promote_prepared_directory(
                payload, target,
                stage_marker=((stage_info.st_dev, stage_info.st_ino),
                              _STAGE_INTENT, intent),
            )
        reopened = _verified_copy(root, expected_plan_id)
        return {**reopened, "outcome": "copied"}


def reopen_prism_zip_stage(*, state_root: Path, expected_plan_id: str) -> dict[str, Any]:
    """Reopen one exact completed Linux copy without the original 9p path."""

    return _verified_copy(_stage_root(state_root), expected_plan_id)


__all__ = ["plan_prism_zip_stage", "apply_prism_zip_stage", "reopen_prism_zip_stage"]
