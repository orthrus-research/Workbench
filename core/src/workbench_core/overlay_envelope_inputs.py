"""Core custody for copied inputs and planned writes in a staged overlay envelope.

The owner supplies a validated, chunked inventory and a read-only source
validator. Core retains those inputs before copying and performs every write
to the managed-tree payload and applies the owner's sealed effects. The
resulting stage is deliberately not a publication: sibling receipts and
whole-envelope publication still need a separate Core transaction.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Callable, Iterable, Iterator, Mapping
from uuid import uuid4

from .durable_files import _directory as pinned_directory
from .durable_records import publish_immutable_bytes, read_private_bytes
from .host_filesystem import private_path, secure_private_path
from .managed_trees import _CoreTreeStage
from .output_routing import _private_directory
from .storage.exact_tree_inventory import (
    MAX_DIRECTORIES, MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES,
    inventory_exact_members,
)
from .storage.registered import ResourceCatalog
from .transport_trees import _mount_id


_CHUNK_BYTES = 1024 * 1024
_MANIFEST_BYTES = 16 * 1024
_RECORD_BYTES = 1024 * 1024
_PLAN_CHUNK_BYTES = 1024 * 1024
# The two sibling V1 JSON files have not been built at the pre-copy boundary.
# Reserve their full V3 per-file allowance, then enforce the actual bytes at
# publication. This is a conservative, opt-in Core profile, not V1 parity.
_SIBLING_COUNT = 2
_SIBLING_RESERVE_BYTES = _SIBLING_COUNT * MAX_FILE_BYTES
_NONCE = re.compile(r"[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_PART = re.compile(r"[^/\\\0\r\n:]+\Z")
_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
_EFFECT_FILE = re.compile(r"([0-9]{16})\.json\Z")
_OPERATION_FILE = re.compile(r"([0-9]{16})-(attempted|complete)\.json\Z")


class OverlayEnvelopeInputError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _parts(value: object) -> tuple[str, ...]:
    if type(value) is not str or not value or value.startswith("/"):
        raise OverlayEnvelopeInputError("overlay.path", "overlay member path is invalid")
    parts = tuple(value.split("/"))
    if any(not part or part in {".", "..", ".git"} or _PART.fullmatch(part) is None
           for part in parts):
        raise OverlayEnvelopeInputError("overlay.path", "overlay member path is unsafe")
    return parts


def _metadata(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _open_directory(parent_fd: int, name: str, *, mount_id: int) -> int:
    visible = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(visible.st_mode):
        raise OverlayEnvelopeInputError("overlay.source", "copied source directory is redirected")
    selected = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    try:
        if _metadata(os.fstat(selected)) != _metadata(visible) or _mount_id(selected) != mount_id:
            raise OverlayEnvelopeInputError("overlay.source", "copied source directory changed custody")
        return selected
    except BaseException:
        os.close(selected)
        raise


def _finish_directory(descriptor: int, mode: int) -> None:
    try:
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _source_parent(root_fd: int, parts: tuple[str, ...], mount_id: int) -> int:
    parent = os.dup(root_fd)
    try:
        for name in parts[:-1]:
            child = _open_directory(parent, name, mount_id=mount_id)
            os.close(parent)
            parent = child
        return parent
    except BaseException:
        os.close(parent)
        raise


def _checked_row(line: bytes) -> dict[str, object]:
    try:
        value = json.loads(line)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OverlayEnvelopeInputError("overlay.inventory", "copy inventory row is invalid") from exc
    if type(value) is not dict or _canonical(value) + b"\n" != line:
        raise OverlayEnvelopeInputError("overlay.inventory", "copy inventory row is not canonical")
    _parts(value.get("path"))
    if value.get("kind") == "directory":
        if set(value) != {"path", "kind", "mode"} or (value["mode"] is not None
                and (type(value["mode"]) is not int or not 0 <= value["mode"] <= 0o7777)):
            raise OverlayEnvelopeInputError("overlay.inventory", "copy directory row is invalid")
    elif value.get("kind") == "file":
        if (set(value) != {"path", "kind", "mode", "size_bytes", "sha256"}
                or type(value["mode"]) is not int or not 0 <= value["mode"] <= 0o7777
                or type(value["size_bytes"]) is not int or value["size_bytes"] < 0
                or type(value["sha256"]) is not str or _DIGEST.fullmatch(value["sha256"]) is None):
            raise OverlayEnvelopeInputError("overlay.inventory", "copy file row is invalid")
    else:
        raise OverlayEnvelopeInputError("overlay.inventory", "copy entry kind is invalid")
    return value


def _effect_metadata(index: int, effect: Mapping[str, object]) -> dict[str, object]:
    data = effect["data"]
    return {
        "index": index, "op": effect["op"],
        "relative_path": effect["relative_path"],
        "expected_sha256": effect["expected_sha256"],
        "data_sha256": sha256(data).hexdigest() if data is not None else None,
        "data_bytes": len(data) if data is not None else None,
    }


def _read_record(path: Path, *, byte_limit: int = _RECORD_BYTES) -> dict[str, object]:
    raw = read_private_bytes(path, byte_limit=byte_limit)
    try:
        record = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OverlayEnvelopeInputError("overlay.changed", "overlay record is invalid") from exc
    if type(record) is not dict or _canonical(record) + b"\n" != raw:
        raise OverlayEnvelopeInputError("overlay.changed", "overlay record is not canonical")
    return record


def _effect_records(root: Path, attempt_id: str) -> tuple[tuple[dict[str, object], ...], str]:
    directory = root / "effects"
    if not directory.exists() and not directory.is_symlink():
        return (), sha256().hexdigest()
    if not private_path(directory, directory=True):
        raise OverlayEnvelopeInputError("overlay.changed", "overlay effects directory changed custody")
    names = sorted(path.name for path in directory.iterdir())
    if names != [f"{index:016d}.json" for index in range(len(names))]:
        raise OverlayEnvelopeInputError("overlay.changed", "overlay effect sequence changed")
    digest = sha256()
    result = []
    for index, name in enumerate(names):
        record = _read_record(directory / name)
        if (set(record) != {"format", "attempt_id", "effect"}
                or record["format"] != "workbench-overlay-envelope-effect-v1"
                or record["attempt_id"] != attempt_id
                or type(record["effect"]) is not dict):
            raise OverlayEnvelopeInputError("overlay.changed", "overlay effect record changed")
        effect = record["effect"]
        if (set(effect) != {"index", "op", "relative_path", "expected_sha256",
                           "data_sha256", "data_bytes"}
                or type(effect["index"]) is not int or effect["index"] != index
                or type(effect["op"]) is not str
                or effect["op"] not in {"add", "replace", "remove"}):
            raise OverlayEnvelopeInputError("overlay.changed", "overlay effect metadata changed")
        _parts(effect["relative_path"])
        if effect["op"] == "remove":
            if effect["data_sha256"] is not None or effect["data_bytes"] is not None:
                raise OverlayEnvelopeInputError("overlay.changed", "overlay remove metadata changed")
        elif (type(effect["data_sha256"]) is not str
              or _DIGEST.fullmatch(effect["data_sha256"]) is None
              or type(effect["data_bytes"]) is not int
              or not 0 <= effect["data_bytes"] <= MAX_FILE_BYTES):
            raise OverlayEnvelopeInputError("overlay.changed", "overlay write metadata changed")
        if (effect["op"] == "add" and effect["expected_sha256"] is not None
                or effect["op"] != "add" and (
                    type(effect["expected_sha256"]) is not str
                    or _DIGEST.fullmatch(effect["expected_sha256"]) is None
                )):
            raise OverlayEnvelopeInputError("overlay.changed", "overlay precondition metadata changed")
        digest.update(_canonical(effect) + b"\n")
        result.append(effect)
    return tuple(result), digest.hexdigest()


def _target_parent(root_fd: int, parts: tuple[str, ...], mount_id: int, *, create: bool) -> int:
    parent = os.dup(root_fd)
    try:
        for name in parts[:-1]:
            try:
                child = _open_directory(parent, name, mount_id=mount_id)
            except FileNotFoundError:
                if not create:
                    raise OverlayEnvelopeInputError("overlay.changed", "operation parent is missing")
                os.mkdir(name, mode=0o700, dir_fd=parent)
                child = _open_directory(parent, name, mount_id=mount_id)
                os.fchmod(child, 0o755)
                os.fsync(child)
                os.fsync(parent)
            os.close(parent)
            parent = child
        return parent
    except BaseException:
        os.close(parent)
        raise


def _checked_target_file(parent_fd: int, name: str, expected_sha256: str,
                         mount_id: int) -> os.stat_result:
    visible = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISREG(visible.st_mode) or visible.st_nlink != 1:
        raise OverlayEnvelopeInputError("overlay.changed", "operation target is not an independent file")
    selected = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
    try:
        if _metadata(os.fstat(selected)) != _metadata(visible) or _mount_id(selected) != mount_id:
            raise OverlayEnvelopeInputError("overlay.changed", "operation target changed custody")
        digest = sha256()
        size = 0
        while block := os.read(selected, _CHUNK_BYTES):
            digest.update(block)
            size += len(block)
            if size > visible.st_size:
                raise OverlayEnvelopeInputError("overlay.changed", "operation target grew")
        if (size != visible.st_size or digest.hexdigest() != expected_sha256
                or _metadata(os.fstat(selected)) != _metadata(visible)
                or _metadata(os.stat(name, dir_fd=parent_fd, follow_symlinks=False)) != _metadata(visible)):
            raise OverlayEnvelopeInputError("overlay.changed", "operation target differs from sealed source")
        return visible
    finally:
        os.close(selected)


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OverlayEnvelopeInputError("overlay.write", "Core operation made no progress")
        view = view[written:]


def _stage_inventory(path: Path, *, cancelled: Callable[[], bool] = lambda: False) -> dict[str, object]:
    rows, root_mode, file_count, directory_count = inventory_exact_members(path, cancelled=cancelled)
    digest = sha256()
    digest.update(_canonical({"root_mode": root_mode}) + b"\n")
    for row in rows:
        digest.update(_canonical(row) + b"\n")
    return {
        "sha256": digest.hexdigest(), "root_mode": root_mode,
        "file_count": file_count, "directory_count": directory_count,
    }


class CoreOverlayEnvelopeInputs:
    """A registered Core attempt store, separate from the sealed root manifest."""

    def __init__(self, *, workspace: Path, configuration_home: Path, owner_id: str):
        if not workspace.is_absolute() or not configuration_home.is_absolute():
            raise OverlayEnvelopeInputError("overlay.policy", "overlay identity needs absolute roots")
        if re.fullmatch(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*", owner_id) is None:
            raise OverlayEnvelopeInputError("overlay.policy", "overlay owner is invalid")
        self.workspace = workspace
        self.configuration_home = configuration_home
        self.owner_id = owner_id
        identity = sha256(_canonical({"workspace": str(workspace), "owner_id": owner_id})).hexdigest()
        self.root = configuration_home / f"overlay-envelope-inputs-v1-{identity}"

    def _ensure(self) -> None:
        resources = ResourceCatalog(self.configuration_home)
        resources._ensure()
        _private_directory(self.root)
        secure_private_path(self.root, directory=True)
        resources.register_record_store(
            family="overlay-envelope-inputs", owner_id=self.owner_id,
            workspace=self.workspace, root=self.root,
        )

    def start(self, *, stage: _CoreTreeStage, source_root: Path,
              plan_chunks: Iterable[bytes], content_root: str = "gregtech") -> CoreOverlayEnvelopeInputAttempt:
        if (not isinstance(stage, _CoreTreeStage)
                or stage.host.workspace != self.workspace
                or stage.host.catalog.configuration_home != self.configuration_home
                or stage.host.owner_id != self.owner_id
                or not isinstance(source_root, Path) or not source_root.is_absolute()
                or source_root == stage.target or source_root in stage.target.parents
                or stage.target in source_root.parents
                or not isinstance(plan_chunks, Iterable)
                or isinstance(plan_chunks, (bytes, str))
                or len(_parts(content_root)) != 1):
            raise OverlayEnvelopeInputError("overlay.policy", "overlay attempt is not bound to this Core stage")
        self._ensure()
        nonce = uuid4().hex
        attempt_root = self.root / nonce
        _private_directory(attempt_root)
        secure_private_path(attempt_root, directory=True)
        for name in ("plan", "inventory"):
            _private_directory(attempt_root / name)
            secure_private_path(attempt_root / name, directory=True)
        plan_digest = sha256()
        plan_chunk_count = 0
        for raw in plan_chunks:
            if type(raw) is not bytes or not 0 < len(raw) <= _PLAN_CHUNK_BYTES:
                raise OverlayEnvelopeInputError("overlay.plan", "overlay plan chunk exceeds its bound")
            stage.host.check_cancelled()
            publish_immutable_bytes(
                attempt_root / "plan" / f"{plan_chunk_count:016d}.bin", raw,
                byte_limit=_PLAN_CHUNK_BYTES,
            )
            plan_digest.update(raw)
            plan_chunk_count += 1
        if plan_chunk_count == 0:
            raise OverlayEnvelopeInputError("overlay.plan", "overlay plan has no bytes")
        reservation = {
            "format": "workbench-overlay-envelope-input-reservation-v1",
            "attempt_id": f"workbench-overlay-envelope-input-v1:{nonce}",
            "workspace": str(self.workspace), "owner_id": self.owner_id,
            "tree_id": stage.tree_id, "stage_path": str(stage.path),
            "target": str(stage.target), "source_root": str(source_root),
            "content_root": content_root, "plan_sha256": plan_digest.hexdigest(),
            "plan_chunks": plan_chunk_count,
        }
        publish_immutable_bytes(attempt_root / "reservation.json", _canonical(reservation) + b"\n",
                                byte_limit=_RECORD_BYTES)
        return CoreOverlayEnvelopeInputAttempt(self, stage, attempt_root, reservation)

    def inventory(self) -> tuple[dict[str, object], ...]:
        if not self.root.exists() and not self.root.is_symlink():
            return ()
        if not private_path(self.root, directory=True):
            raise OverlayEnvelopeInputError("overlay.changed", "overlay attempt store changed custody")
        rows: list[dict[str, object]] = []
        for root in sorted(self.root.iterdir()):
            if _NONCE.fullmatch(root.name) is None or not private_path(root, directory=True):
                raise OverlayEnvelopeInputError("overlay.changed", "overlay attempt store has an unknown entry")
            expected_entries = {
                "plan", "inventory", "reservation.json", "manifest.json",
                "input.json", "copy-attempted.json", "copy-complete.json",
                "effects", "effect-seal.json", "operations", "operations-complete.json",
            }
            if any(path.name not in expected_entries for path in root.iterdir()):
                raise OverlayEnvelopeInputError("overlay.changed", "overlay attempt has an unknown entry")
            reservation_path = root / "reservation.json"
            for name in ("plan", "inventory"):
                directory = root / name
                if not directory.exists() and not reservation_path.exists():
                    continue
                if not private_path(directory, directory=True):
                    raise OverlayEnvelopeInputError("overlay.changed", "overlay chunk directory changed custody")
                names = []
                for path in directory.iterdir():
                    if (re.fullmatch(r"[0-9]{16}\.bin", path.name) is None
                            or not private_path(path, directory=False)):
                        raise OverlayEnvelopeInputError("overlay.changed", "overlay chunk set has an unsafe entry")
                    names.append(path.name)
                if sorted(names) != [f"{index:016d}.bin" for index in range(len(names))]:
                    raise OverlayEnvelopeInputError("overlay.changed", "overlay chunk sequence changed")
            for name in expected_entries - {"plan", "inventory", "effects", "operations"}:
                path = root / name
                if (path.exists() or path.is_symlink()) and not private_path(path, directory=False):
                    raise OverlayEnvelopeInputError("overlay.changed", "overlay record changed custody")
            if not reservation_path.exists():
                rows.append({"attempt_id": f"workbench-overlay-envelope-input-v1:{root.name}",
                             "status": "orphan-pre-reservation", "path": str(root)})
                continue
            raw_reservation = read_private_bytes(reservation_path, byte_limit=_RECORD_BYTES)
            reservation = json.loads(raw_reservation)
            if (reservation.get("attempt_id") != f"workbench-overlay-envelope-input-v1:{root.name}"
                    or reservation.get("workspace") != str(self.workspace)
                    or reservation.get("owner_id") != self.owner_id
                    or _canonical(reservation) + b"\n" != raw_reservation
                    or set(reservation) != {
                        "format", "attempt_id", "workspace", "owner_id", "tree_id",
                        "stage_path", "target", "source_root", "content_root",
                        "plan_sha256", "plan_chunks",
                    }
                    or reservation["format"] != "workbench-overlay-envelope-input-reservation-v1"
                    or type(reservation["plan_chunks"]) is not int or reservation["plan_chunks"] < 1
                    or type(reservation["plan_sha256"]) is not str
                    or _DIGEST.fullmatch(reservation["plan_sha256"]) is None):
                raise OverlayEnvelopeInputError("overlay.changed", "overlay reservation changed")
            tree_id = reservation["tree_id"]
            target_text = reservation["target"]
            source_text = reservation["source_root"]
            stage_text = reservation["stage_path"]
            if (type(tree_id) is not str
                    or re.fullmatch(r"workbench-tree-v1:[0-9a-f]{32}", tree_id) is None
                    or any(type(value) is not str or not Path(value).is_absolute()
                           or any(part in {".", ".."} for part in Path(value).parts)
                           for value in (target_text, source_text, stage_text))
                    or type(reservation["content_root"]) is not str
                    or len(_parts(reservation["content_root"])) != 1):
                raise OverlayEnvelopeInputError("overlay.changed", "overlay path or tree identity changed")
            target = Path(target_text)
            source = Path(source_text)
            tree_catalog = ResourceCatalog(self.configuration_home).trees
            tree_reservation = tree_catalog.reservation(tree_id)
            expected_target = tree_catalog._target(tree_reservation)
            expected_stage = expected_target.parent / tree_reservation["staging"] / "payload"
            if (tree_reservation["workspace"] != str(self.workspace)
                    or tree_reservation["owner_id"] != self.owner_id
                    or target != expected_target or stage_text != str(expected_stage)
                    or source == target or source in target.parents or target in source.parents):
                raise OverlayEnvelopeInputError("overlay.changed", "overlay managed-stage binding changed")
            if len(tuple((root / "plan").iterdir())) != reservation["plan_chunks"]:
                raise OverlayEnvelopeInputError("overlay.changed", "overlay plan chunk set changed")
            plan_digest = sha256()
            for index in range(reservation["plan_chunks"]):
                plan_digest.update(read_private_bytes(
                    root / "plan" / f"{index:016d}.bin", byte_limit=_PLAN_CHUNK_BYTES,
                ))
            if plan_digest.hexdigest() != reservation["plan_sha256"]:
                raise OverlayEnvelopeInputError("overlay.changed", "overlay plan bytes changed")
            if (root / "copy-complete.json").exists() and not (root / "copy-attempted.json").exists():
                raise OverlayEnvelopeInputError("overlay.changed", "overlay copy completion lacks an attempt")
            if (root / "copy-attempted.json").exists() and not (root / "input.json").exists():
                raise OverlayEnvelopeInputError("overlay.changed", "overlay copy attempt lacks sealed inputs")
            if ((root / "copy-attempted.json").exists()
                    and (root / "effects").exists()
                    and not (root / "effect-seal.json").exists()):
                raise OverlayEnvelopeInputError("overlay.changed", "overlay copy attempt lacks sealed effects")
            if (root / "input.json").exists() and not (root / "manifest.json").exists():
                raise OverlayEnvelopeInputError("overlay.changed", "overlay inputs lack their manifest")
            copy_path = root / "copy-complete.json"
            if (root / "input.json").exists():
                input_raw = read_private_bytes(root / "input.json", byte_limit=_MANIFEST_BYTES)
                input_record = json.loads(input_raw)
                manifest_raw = read_private_bytes(root / "manifest.json", byte_limit=_MANIFEST_BYTES)
                manifest = json.loads(manifest_raw)
                count = input_record.get("chunk_count")
                if (type(count) is not int or count < 1
                        or len(tuple((root / "inventory").iterdir())) != count
                        or input_record.get("reservation_sha256") != sha256(raw_reservation).hexdigest()
                        or input_record.get("manifest_sha256") != sha256(manifest_raw).hexdigest()
                        or input_record.get("manifest_id") != manifest.get("inventory_id")
                        or input_record.get("chunks_sha256") != manifest.get("chunks_sha256")
                        or manifest.get("chunk_count") != count
                        or _canonical(input_record) + b"\n" != input_raw
                        or _canonical(manifest) + b"\n" != manifest_raw
                        or manifest.get("source_root_uri") not in {None, source.as_uri()}):
                    raise OverlayEnvelopeInputError("overlay.changed", "overlay input binding changed")
                chunks_digest = sha256()
                for index in range(count):
                    chunk = read_private_bytes(
                        root / "inventory" / f"{index:016d}.bin", byte_limit=_CHUNK_BYTES,
                    )
                    chunks_digest.update(
                        f"{index}\0{len(chunk)}\0{sha256(chunk).hexdigest()}\n".encode("ascii")
                    )
                if chunks_digest.hexdigest() != input_record["chunks_sha256"]:
                    raise OverlayEnvelopeInputError("overlay.changed", "overlay copied-entry bytes changed")
                for record_name, expected_format in (
                    ("copy-attempted.json", "workbench-overlay-envelope-copy-attempt-v1"),
                ):
                    path = root / record_name
                    if not path.exists():
                        continue
                    raw = read_private_bytes(path, byte_limit=_MANIFEST_BYTES)
                    record = json.loads(raw)
                    if (record != {"format": expected_format, "attempt_id": reservation["attempt_id"],
                                   "manifest_id": manifest["inventory_id"]}
                            or _canonical(record) + b"\n" != raw):
                        raise OverlayEnvelopeInputError("overlay.changed", "overlay copy milestone changed")
                if copy_path.exists():
                    copy_record = _read_record(copy_path, byte_limit=_MANIFEST_BYTES)
                    basic = {"attempt_id": reservation["attempt_id"],
                             "manifest_id": manifest["inventory_id"]}
                    if copy_record["format"] == "workbench-overlay-envelope-copy-complete-v1":
                        if copy_record != {"format": copy_record["format"], **basic}:
                            raise OverlayEnvelopeInputError("overlay.changed", "overlay legacy copy milestone changed")
                    elif copy_record["format"] == "workbench-overlay-envelope-copy-complete-v2":
                        if (set(copy_record) != {"format", *basic, "payload_device", "payload_inode",
                                                 "content_device", "content_inode"}
                                or any(type(copy_record[key]) is not int or copy_record[key] < 0
                                       for key in ("payload_device", "payload_inode",
                                                   "content_device", "content_inode"))
                                or any(copy_record[key] != value for key, value in basic.items())):
                            raise OverlayEnvelopeInputError("overlay.changed", "overlay copy identity changed")
                        for path, device, inode in (
                            (Path(stage_text), copy_record["payload_device"], copy_record["payload_inode"]),
                            (Path(stage_text) / reservation["content_root"],
                             copy_record["content_device"], copy_record["content_inode"]),
                        ):
                            try:
                                visible = path.lstat()
                            except FileNotFoundError as exc:
                                raise OverlayEnvelopeInputError("overlay.changed", "copied stage is missing") from exc
                            if not stat.S_ISDIR(visible.st_mode) or (visible.st_dev, visible.st_ino) != (device, inode):
                                raise OverlayEnvelopeInputError("overlay.changed", "copied stage identity changed")
                    else:
                        raise OverlayEnvelopeInputError("overlay.changed", "overlay copy format changed")
            effect_rows, effect_digest = _effect_records(root, reservation["attempt_id"])
            effect_seal_path = root / "effect-seal.json"
            if effect_seal_path.exists():
                if not (root / "input.json").exists():
                    raise OverlayEnvelopeInputError("overlay.changed", "overlay effect seal lacks inputs")
                effect_seal = _read_record(effect_seal_path, byte_limit=_MANIFEST_BYTES)
                capacity = effect_seal.get("capacity")
                if (set(effect_seal) != {"format", "attempt_id", "manifest_id", "plan_sha256", "capacity"}
                        or effect_seal["format"] != "workbench-overlay-envelope-effect-seal-v1"
                        or effect_seal["attempt_id"] != reservation["attempt_id"]
                        or effect_seal["manifest_id"] != manifest["inventory_id"]
                        or effect_seal["plan_sha256"] != reservation["plan_sha256"]
                        or type(capacity) is not dict
                        or set(capacity) != {"inventory_id", "effect_count", "effects_sha256",
                                             "maximum_files", "maximum_directories", "reserved_bytes",
                                             "inventory_policy"}
                        or capacity["inventory_id"] != manifest["inventory_id"]
                        or capacity["effect_count"] != len(effect_rows)
                        or capacity["effects_sha256"] != effect_digest
                        or capacity["inventory_policy"] != "posix-exact-v1"
                        or any(type(capacity[key]) is not int or capacity[key] < 0
                               for key in ("maximum_files", "maximum_directories", "reserved_bytes"))
                        or capacity["maximum_files"] > MAX_FILES
                        or capacity["maximum_directories"] > MAX_DIRECTORIES
                        or capacity["reserved_bytes"] > MAX_TOTAL_BYTES):
                    raise OverlayEnvelopeInputError("overlay.changed", "overlay effect seal changed")
            elif (root / "effects").exists() and (root / "copy-attempted.json").exists():
                raise OverlayEnvelopeInputError("overlay.changed", "overlay unsealed effects preceded copy")
            if (root / "operations").exists() or (root / "operations-complete.json").exists():
                self._inventory_operations(root, reservation["attempt_id"], effect_rows,
                                           effect_seal_path.exists(), copy_path.exists(),
                                           Path(stage_text))
            status = ("copy-complete" if (root / "copy-complete.json").is_file()
                      else "copy-incomplete" if (root / "copy-attempted.json").is_file()
                      else "effects-incomplete" if (root / "effects").exists() and not effect_seal_path.exists()
                      else "effects-sealed" if effect_seal_path.exists()
                      else "input-sealed" if (root / "input.json").is_file()
                      else "reserved-incomplete")
            if (root / "operations-complete.json").exists():
                status = "operations-complete"
            elif (root / "operations").exists():
                status = "operations-incomplete"
            rows.append({"attempt_id": reservation["attempt_id"], "status": status,
                         "path": str(root), "stage_path": reservation["stage_path"],
                         "tree_id": reservation["tree_id"],
                         "target": reservation["target"]})
        return tuple(rows)

    @staticmethod
    def _inventory_operations(
        root: Path, attempt_id: str, effects: tuple[dict[str, object], ...],
        sealed: bool, copied: bool, stage_path: Path,
    ) -> None:
        directory = root / "operations"
        if not sealed or not copied or not private_path(directory, directory=True):
            raise OverlayEnvelopeInputError("overlay.changed", "overlay operations lack sealed copied inputs")
        attempted: set[int] = set()
        completed: set[int] = set()
        for path in directory.iterdir():
            match = _OPERATION_FILE.fullmatch(path.name)
            if match is None or not private_path(path, directory=False):
                raise OverlayEnvelopeInputError("overlay.changed", "overlay operation store has an unknown entry")
            index = int(match.group(1))
            kind = match.group(2)
            if index >= len(effects):
                raise OverlayEnvelopeInputError("overlay.changed", "overlay operation exceeds sealed effects")
            expected = {
                "format": f"workbench-overlay-envelope-operation-{kind}-v1",
                "attempt_id": attempt_id, "index": index,
                "effect_sha256": sha256(_canonical(effects[index]) + b"\n").hexdigest(),
            }
            if _read_record(path) != expected:
                raise OverlayEnvelopeInputError("overlay.changed", "overlay operation record changed")
            (attempted if kind == "attempted" else completed).add(index)
        if (attempted != set(range(len(attempted)))
                or completed != set(range(len(completed)))
                or not completed <= attempted
                or len(attempted) > len(completed) + 1):
            raise OverlayEnvelopeInputError("overlay.changed", "overlay operation sequence changed")
        completion = root / "operations-complete.json"
        if completion.exists():
            if attempted != set(range(len(effects))) or completed != attempted:
                raise OverlayEnvelopeInputError("overlay.changed", "overlay operation completion is premature")
            expected = {
                "format": "workbench-overlay-envelope-operations-complete-v1",
                "attempt_id": attempt_id, "effect_count": len(effects),
                "effects_sha256": sha256(b"".join(
                    _canonical(effect) + b"\n" for effect in effects
                )).hexdigest(),
                "stage_inventory": _stage_inventory(stage_path),
            }
            if _read_record(completion, byte_limit=_MANIFEST_BYTES) != expected:
                raise OverlayEnvelopeInputError("overlay.changed", "overlay operation completion changed")


class CoreOverlayEnvelopeInputAttempt:
    def __init__(self, host: CoreOverlayEnvelopeInputs, stage: _CoreTreeStage,
                 root: Path, reservation: Mapping[str, object]):
        self.host = host
        self.stage = stage
        self.root = root
        self.reservation = dict(reservation)
        self.attempt_id = str(reservation["attempt_id"])
        self._next_chunk = 0

    def emit_chunk(self, ordinal: int, raw: bytes) -> None:
        if (self._sealed() or type(ordinal) is not int or ordinal != self._next_chunk
                or type(raw) is not bytes or not 0 < len(raw) <= _CHUNK_BYTES):
            raise OverlayEnvelopeInputError("overlay.inventory", "inventory chunk is out of order or invalid")
        publish_immutable_bytes(
            self.root / "inventory" / f"{ordinal:016d}.bin", raw,
            byte_limit=_CHUNK_BYTES,
        )
        self._next_chunk += 1

    def _sealed(self) -> bool:
        return (self.root / "input.json").exists()

    def _chunks(self, count: int) -> Iterator[bytes]:
        directory = self.root / "inventory"
        names = sorted(path.name for path in directory.iterdir())
        if names != [f"{index:016d}.bin" for index in range(count)]:
            raise OverlayEnvelopeInputError("overlay.changed", "retained inventory chunk set changed")
        for index in range(count):
            yield read_private_bytes(directory / f"{index:016d}.bin", byte_limit=_CHUNK_BYTES)

    def seal_inputs(self, manifest: Mapping[str, object], *,
                    validate_inventory: Callable[[Mapping[str, object], Iterable[bytes]], object]) -> None:
        if self._sealed() or not callable(validate_inventory) or type(manifest) is not dict:
            raise OverlayEnvelopeInputError("overlay.state", "overlay inputs were already sealed or are invalid")
        raw = _canonical(manifest) + b"\n"
        count = manifest.get("chunk_count")
        digest = manifest.get("chunks_sha256")
        if (len(raw) > _MANIFEST_BYTES or type(count) is not int or count != self._next_chunk
                or type(digest) is not str or _DIGEST.fullmatch(digest) is None):
            raise OverlayEnvelopeInputError("overlay.inventory", "overlay copy manifest is invalid")
        observed = sha256()
        for index, chunk in enumerate(self._chunks(count)):
            observed.update(f"{index}\0{len(chunk)}\0{sha256(chunk).hexdigest()}\n".encode("ascii"))
        if observed.hexdigest() != digest or validate_inventory(manifest, self._chunks(count)) != manifest:
            raise OverlayEnvelopeInputError("overlay.inventory", "overlay copy inventory did not validate")
        publish_immutable_bytes(self.root / "manifest.json", raw, byte_limit=_MANIFEST_BYTES)
        record = {
            "format": "workbench-overlay-envelope-input-v1",
            "attempt_id": self.attempt_id,
            "reservation_sha256": sha256(_canonical(self.reservation) + b"\n").hexdigest(),
            "manifest_sha256": sha256(raw).hexdigest(),
            "manifest_id": manifest.get("inventory_id"),
            "chunk_count": count, "chunks_sha256": digest,
        }
        publish_immutable_bytes(self.root / "input.json", _canonical(record) + b"\n",
                                byte_limit=_MANIFEST_BYTES)

    def _verified_inputs(self) -> dict[str, object]:
        if (self.stage.tree_id != self.reservation["tree_id"]
                or str(self.stage.path) != self.reservation["stage_path"]
                or str(self.stage.target) != self.reservation["target"]):
            raise OverlayEnvelopeInputError("overlay.changed", "managed stage identity changed")
        raw_reservation = read_private_bytes(self.root / "reservation.json", byte_limit=_RECORD_BYTES)
        if raw_reservation != _canonical(self.reservation) + b"\n":
            raise OverlayEnvelopeInputError("overlay.changed", "overlay reservation changed")
        record = json.loads(read_private_bytes(self.root / "input.json", byte_limit=_MANIFEST_BYTES))
        manifest_raw = read_private_bytes(self.root / "manifest.json", byte_limit=_MANIFEST_BYTES)
        if (record.get("attempt_id") != self.attempt_id
                or record.get("reservation_sha256") != sha256(raw_reservation).hexdigest()
                or record.get("manifest_sha256") != sha256(manifest_raw).hexdigest()
                or _canonical(record) + b"\n" != read_private_bytes(self.root / "input.json", byte_limit=_MANIFEST_BYTES)):
            raise OverlayEnvelopeInputError("overlay.changed", "sealed overlay input changed")
        manifest = json.loads(manifest_raw)
        if (record.get("manifest_id") != manifest.get("inventory_id")
                or record.get("chunk_count") != manifest.get("chunk_count")
                or record.get("chunks_sha256") != manifest.get("chunks_sha256")):
            raise OverlayEnvelopeInputError("overlay.changed", "overlay manifest binding changed")
        plan_digest = sha256()
        plan_count = self.reservation["plan_chunks"]
        plan_directory = self.root / "plan"
        if sorted(path.name for path in plan_directory.iterdir()) != [
            f"{index:016d}.bin" for index in range(plan_count)
        ]:
            raise OverlayEnvelopeInputError("overlay.changed", "retained overlay plan chunk set changed")
        for index in range(plan_count):
            plan_digest.update(read_private_bytes(
                plan_directory / f"{index:016d}.bin", byte_limit=_PLAN_CHUNK_BYTES,
            ))
        if plan_digest.hexdigest() != self.reservation["plan_sha256"]:
            raise OverlayEnvelopeInputError("overlay.changed", "retained overlay plan changed")
        chunks_digest = sha256()
        for index, chunk in enumerate(self._chunks(record["chunk_count"])):
            chunks_digest.update(f"{index}\0{len(chunk)}\0{sha256(chunk).hexdigest()}\n".encode("ascii"))
        if chunks_digest.hexdigest() != record["chunks_sha256"]:
            raise OverlayEnvelopeInputError("overlay.changed", "retained overlay inventory changed")
        return manifest

    def _rows(self, manifest: Mapping[str, object]) -> Iterator[dict[str, object]]:
        for chunk in self._chunks(int(manifest["chunk_count"])):
            if not chunk.endswith(b"\n"):
                raise OverlayEnvelopeInputError("overlay.inventory", "copy inventory chunk has a partial row")
            for line in chunk.splitlines(keepends=True):
                yield _checked_row(line)

    def preflight_v3(self, effects: Iterable[Mapping[str, object]]) -> dict[str, object]:
        """Refuse known V3 publication overflow before the first Core copy.

        This checks the retained copied-tree rows and the domain's exact
        proposed output bytes. The caller still has to validate those effects
        against its retained plan and validate the final staged envelope.
        """
        metadata = []
        for index, effect in enumerate(effects):
            if (type(effect) is not dict
                    or set(effect) != {"op", "relative_path", "expected_sha256", "data"}
                    or type(effect["op"]) is not str
                    or effect["op"] not in {"add", "replace", "remove"}):
                raise OverlayEnvelopeInputError("overlay.plan", "overlay operation shape is invalid")
            data = effect["data"]
            if effect["op"] == "remove":
                if data is not None:
                    raise OverlayEnvelopeInputError("overlay.plan", "overlay remove has output bytes")
            elif type(data) is not bytes:
                raise OverlayEnvelopeInputError("overlay.plan", "overlay write bytes are invalid")
            metadata.append(_effect_metadata(index, effect))
        return self._capacity_from_effect_rows(metadata)

    def _capacity_from_effect_rows(
        self, effects: Iterable[Mapping[str, object]], *,
        manifest: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        if manifest is None:
            manifest = self._verified_inputs()
        files: dict[str, dict[str, object]] = {}
        directories = {str(self.reservation["content_root"])}
        directory_modes = {"": 0o755}
        source_bytes = 0
        for row in self._rows(manifest):
            path = str(row["path"])
            if row["kind"] == "directory":
                directories.add(f"{self.reservation['content_root']}/{path}")
                mode = 0o755 if row["mode"] is None else int(row["mode"])
                if mode & 0o500 != 0o500:
                    raise OverlayEnvelopeInputError(
                        "overlay.unsupported", "source directory cannot be inventoried under V3",
                    )
                directory_modes[path] = mode
            else:
                files[path] = row
                size = int(row["size_bytes"])
                if size > MAX_FILE_BYTES:
                    raise OverlayEnvelopeInputError("overlay.unsupported", "source file exceeds V3 bound")
                source_bytes += size
            if len(files) + _SIBLING_COUNT > MAX_FILES or len(directories) > MAX_DIRECTORIES:
                raise OverlayEnvelopeInputError("overlay.unsupported", "source exceeds V3 member bound")
        if source_bytes + _SIBLING_RESERVE_BYTES > MAX_TOTAL_BYTES:
            raise OverlayEnvelopeInputError("overlay.unsupported", "source exceeds V3 byte reserve")

        count = additions = planned_bytes = 0
        seen: set[str] = set()
        digest = sha256()
        for effect in effects:
            path = effect["relative_path"]
            try:
                parts = _parts(path)
            except OverlayEnvelopeInputError as exc:
                raise OverlayEnvelopeInputError("overlay.unsupported", "operation path is outside V3 profile") from exc
            if path in seen:
                raise OverlayEnvelopeInputError("overlay.plan", "overlay operation target repeats")
            seen.add(path)
            prior = files.get(path)
            expected = effect["expected_sha256"]
            action = effect["op"]
            data_bytes = effect["data_bytes"]
            for depth in range(len(parts)):
                parent = "/".join(parts[:depth])
                mode = directory_modes.get(parent, 0o755)
                if mode & 0o300 != 0o300:
                    raise OverlayEnvelopeInputError(
                        "overlay.unsupported", "operation parent is not writable under V3",
                    )
            if action == "add":
                full_path = f"{self.reservation['content_root']}/{path}"
                if (prior is not None or full_path in directories
                        or any("/".join(parts[:depth]) in files
                               for depth in range(1, len(parts)))
                        or expected is not None):
                    raise OverlayEnvelopeInputError("overlay.plan", "overlay add precondition is invalid")
                additions += 1
                for depth in range(1, len(parts)):
                    directories.add(f"{self.reservation['content_root']}/{'/'.join(parts[:depth])}")
            elif (prior is None or expected != prior["sha256"]
                  or type(expected) is not str or _DIGEST.fullmatch(expected) is None):
                raise OverlayEnvelopeInputError("overlay.plan", "overlay existing-file precondition is invalid")
            if action == "remove":
                if data_bytes is not None or effect["data_sha256"] is not None:
                    raise OverlayEnvelopeInputError("overlay.plan", "overlay remove has output bytes")
            else:
                if type(data_bytes) is not int or data_bytes < 0:
                    raise OverlayEnvelopeInputError("overlay.plan", "overlay write size is invalid")
                if data_bytes > MAX_FILE_BYTES:
                    raise OverlayEnvelopeInputError("overlay.unsupported", "operation file exceeds V3 bound")
                planned_bytes += data_bytes
            digest.update(_canonical(effect) + b"\n")
            count += 1
            if (len(files) + additions + _SIBLING_COUNT > MAX_FILES
                    or len(directories) > MAX_DIRECTORIES
                    or source_bytes + planned_bytes + _SIBLING_RESERVE_BYTES > MAX_TOTAL_BYTES):
                raise OverlayEnvelopeInputError("overlay.unsupported", "planned envelope exceeds V3 bound")
        if count == 0:
            raise OverlayEnvelopeInputError("overlay.plan", "overlay plan has no operations")
        return {
            "inventory_id": manifest["inventory_id"],
            "effect_count": count, "effects_sha256": digest.hexdigest(),
            "maximum_files": len(files) + additions + _SIBLING_COUNT,
            "maximum_directories": len(directories),
            "reserved_bytes": source_bytes + planned_bytes + _SIBLING_RESERVE_BYTES,
            "inventory_policy": "posix-exact-v1",
        }

    def _plan_chunks(self) -> Iterator[bytes]:
        for index in range(int(self.reservation["plan_chunks"])):
            yield read_private_bytes(
                self.root / "plan" / f"{index:016d}.bin", byte_limit=_PLAN_CHUNK_BYTES,
            )

    def seal_effects(
        self, effects: tuple[dict[str, object], ...], *,
        validate_plan: Callable[[Iterable[bytes]], object],
    ) -> dict[str, object]:
        """Bind ordered domain effects and V3 capacity before the first copy."""
        if (type(effects) is not tuple or not callable(validate_plan)
                or (self.root / "effects").exists()
                or (self.root / "effect-seal.json").exists()
                or (self.root / "copy-attempted.json").exists()):
            raise OverlayEnvelopeInputError("overlay.state", "overlay effects cannot be sealed now")
        capacity = self.preflight_v3(effects)
        if validate_plan(self._plan_chunks()) != effects:
            raise OverlayEnvelopeInputError("overlay.plan", "ordered effects differ from retained plan")
        _private_directory(self.root / "effects")
        secure_private_path(self.root / "effects", directory=True)
        for index, effect in enumerate(effects):
            self.stage.host.check_cancelled()
            record = {
                "format": "workbench-overlay-envelope-effect-v1",
                "attempt_id": self.attempt_id,
                "effect": _effect_metadata(index, effect),
            }
            publish_immutable_bytes(
                self.root / "effects" / f"{index:016d}.json",
                _canonical(record) + b"\n", byte_limit=_RECORD_BYTES,
            )
        record = {
            "format": "workbench-overlay-envelope-effect-seal-v1",
            "attempt_id": self.attempt_id,
            "manifest_id": capacity["inventory_id"],
            "plan_sha256": self.reservation["plan_sha256"],
            "capacity": capacity,
        }
        publish_immutable_bytes(
            self.root / "effect-seal.json", _canonical(record) + b"\n",
            byte_limit=_MANIFEST_BYTES,
        )
        return capacity

    def _read_effect_seal(self) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
        manifest = self._verified_inputs()
        seal = _read_record(self.root / "effect-seal.json", byte_limit=_MANIFEST_BYTES)
        retained, digest = _effect_records(self.root, self.attempt_id)
        if (set(seal) != {"format", "attempt_id", "manifest_id", "plan_sha256", "capacity"}
                or seal["format"] != "workbench-overlay-envelope-effect-seal-v1"
                or seal["attempt_id"] != self.attempt_id
                or seal["manifest_id"] != manifest["inventory_id"]
                or seal["plan_sha256"] != self.reservation["plan_sha256"]
                or type(seal["capacity"]) is not dict
                or seal["capacity"].get("effect_count") != len(retained)
                or seal["capacity"].get("effects_sha256") != digest
                or seal["capacity"].get("inventory_id") != manifest["inventory_id"]
                or seal["capacity"].get("inventory_policy") != "posix-exact-v1"):
            raise OverlayEnvelopeInputError("overlay.changed", "sealed overlay effects changed")
        try:
            expected_capacity = self._capacity_from_effect_rows(retained, manifest=manifest)
        except OverlayEnvelopeInputError as exc:
            raise OverlayEnvelopeInputError("overlay.changed", "sealed overlay capacity cannot be reproduced") from exc
        if seal["capacity"] != expected_capacity:
            raise OverlayEnvelopeInputError("overlay.changed", "sealed overlay capacity changed")
        return seal, retained

    def _verified_effects(self, effects: tuple[dict[str, object], ...]) -> dict[str, object]:
        if type(effects) is not tuple:
            raise OverlayEnvelopeInputError("overlay.plan", "ordered effects must be a tuple")
        capacity = self.preflight_v3(effects)
        seal, retained = self._read_effect_seal()
        if len(effects) != len(retained) or any(
            _effect_metadata(index, effect) != retained[index]
            for index, effect in enumerate(effects)
        ):
            raise OverlayEnvelopeInputError("overlay.plan", "effect bytes differ from sealed attempt")
        if capacity != seal["capacity"]:
            raise OverlayEnvelopeInputError("overlay.changed", "sealed overlay capacity changed")
        return seal

    def copy_source(self, *, verify_source: Callable[[Mapping[str, object], Iterable[bytes]], object]) -> Path:
        """Copy exactly the sealed rows; retain an incomplete stage after failure."""
        if sys.platform != "linux" or not callable(verify_source):
            raise OverlayEnvelopeInputError("overlay.filesystem", "overlay copy needs Linux no-follow handles")
        manifest = self._verified_inputs()
        self._read_effect_seal()
        attempted = self.root / "copy-attempted.json"
        if attempted.exists() or (self.root / "copy-complete.json").exists():
            raise OverlayEnvelopeInputError("overlay.state", "overlay copy has already been attempted")
        publish_immutable_bytes(attempted, _canonical({
            "format": "workbench-overlay-envelope-copy-attempt-v1",
            "attempt_id": self.attempt_id, "manifest_id": manifest["inventory_id"],
        }) + b"\n", byte_limit=_MANIFEST_BYTES)
        count = int(manifest["chunk_count"])
        if verify_source(manifest, self._chunks(count)) != manifest:
            raise OverlayEnvelopeInputError("overlay.source", "source changed before Core copy")
        source = Path(str(self.reservation["source_root"]))
        payload = self.stage.path
        content = payload / str(self.reservation["content_root"])
        source_fd = pinned_directory(source, create=False)
        try:
            source_mount = _mount_id(source_fd)
            staging_fd = pinned_directory(self.stage.staging_root, create=False)
            try:
                try:
                    os.mkdir(payload.name, mode=0o700, dir_fd=staging_fd)
                except FileExistsError as exc:
                    raise OverlayEnvelopeInputError("overlay.changed", "managed payload already exists") from exc
                payload_fd = os.open(payload.name, _DIRECTORY_FLAGS, dir_fd=staging_fd)
                try:
                    content_name = str(self.reservation["content_root"])
                    os.mkdir(content_name, mode=0o700, dir_fd=payload_fd)
                    root_fd = os.open(content_name, _DIRECTORY_FLAGS, dir_fd=payload_fd)
                    root_identity = (os.fstat(root_fd).st_dev, os.fstat(root_fd).st_ino)
                    stack: list[tuple[tuple[str, ...], int, int]] = [((), root_fd, 0o755)]
                    previous = ""
                    try:
                        for row in self._rows(manifest):
                            relative = str(row["path"])
                            if relative <= previous:
                                raise OverlayEnvelopeInputError("overlay.inventory", "copy rows are unordered")
                            previous = relative
                            parts = _parts(relative)
                            while stack and stack[-1][0] != parts[:-1]:
                                _prefix, descriptor, mode = stack.pop()
                                _finish_directory(descriptor, mode)
                            if not stack:
                                raise OverlayEnvelopeInputError("overlay.inventory", "copy row lacks its parent")
                            target_parent = stack[-1][1]
                            source_parent = _source_parent(source_fd, parts, source_mount)
                            try:
                                name = parts[-1]
                                if row["kind"] == "directory":
                                    copied = _open_directory(source_parent, name, mount_id=source_mount)
                                    try:
                                        selected_mode = stat.S_IMODE(os.fstat(copied).st_mode)
                                        if row["mode"] is not None and row["mode"] != selected_mode:
                                            raise OverlayEnvelopeInputError("overlay.source", "copied directory mode changed")
                                    finally:
                                        os.close(copied)
                                    os.mkdir(name, mode=0o700, dir_fd=target_parent)
                                    target_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=target_parent)
                                    stack.append((parts, target_fd, 0o755 if row["mode"] is None else int(row["mode"])))
                                else:
                                    self._copy_file(source_parent, target_parent, name, row, source_mount)
                            finally:
                                os.close(source_parent)
                        while stack:
                            _prefix, descriptor, mode = stack.pop()
                            _finish_directory(descriptor, mode)
                    except BaseException:
                        for _prefix, descriptor, _mode in stack:
                            try:
                                os.close(descriptor)
                            except OSError:
                                pass
                        raise
                    visible_content = os.stat(content_name, dir_fd=payload_fd, follow_symlinks=False)
                    if (not stat.S_ISDIR(visible_content.st_mode)
                            or (visible_content.st_dev, visible_content.st_ino) != root_identity):
                        raise OverlayEnvelopeInputError("overlay.changed", "copied content root changed identity")
                    os.fsync(payload_fd)
                    visible_payload = os.stat(payload.name, dir_fd=staging_fd, follow_symlinks=False)
                    opened_payload = os.fstat(payload_fd)
                    if (not stat.S_ISDIR(visible_payload.st_mode)
                            or (visible_payload.st_dev, visible_payload.st_ino)
                            != (opened_payload.st_dev, opened_payload.st_ino)):
                        raise OverlayEnvelopeInputError("overlay.changed", "copied payload changed identity")
                    os.fsync(staging_fd)
                finally:
                    os.close(payload_fd)
                reopened_stage = pinned_directory(self.stage.staging_root, create=False)
                try:
                    if (os.fstat(reopened_stage).st_dev, os.fstat(reopened_stage).st_ino) != (
                            os.fstat(staging_fd).st_dev, os.fstat(staging_fd).st_ino):
                        raise OverlayEnvelopeInputError("overlay.changed", "managed staging root changed identity")
                finally:
                    os.close(reopened_stage)
            finally:
                os.close(staging_fd)
        finally:
            os.close(source_fd)
        if verify_source(manifest, self._chunks(count)) != manifest:
            raise OverlayEnvelopeInputError("overlay.source", "source changed during Core copy")
        payload_fd = pinned_directory(self.stage.path, create=False)
        try:
            payload_info = os.fstat(payload_fd)
            if (payload_info.st_dev, payload_info.st_ino) != (
                    opened_payload.st_dev, opened_payload.st_ino):
                raise OverlayEnvelopeInputError("overlay.changed", "copied payload changed after source check")
            content_fd = _open_directory(
                payload_fd, str(self.reservation["content_root"]), mount_id=_mount_id(payload_fd),
            )
            try:
                content_info = os.fstat(content_fd)
                if (content_info.st_dev, content_info.st_ino) != root_identity:
                    raise OverlayEnvelopeInputError("overlay.changed", "copied content changed after source check")
            finally:
                os.close(content_fd)
        finally:
            os.close(payload_fd)
        publish_immutable_bytes(self.root / "copy-complete.json", _canonical({
            "format": "workbench-overlay-envelope-copy-complete-v2",
            "attempt_id": self.attempt_id, "manifest_id": manifest["inventory_id"],
            "payload_device": payload_info.st_dev, "payload_inode": payload_info.st_ino,
            "content_device": content_info.st_dev, "content_inode": content_info.st_ino,
        }) + b"\n", byte_limit=_MANIFEST_BYTES)
        return content

    def _copied_stage(self) -> int:
        record = _read_record(self.root / "copy-complete.json", byte_limit=_MANIFEST_BYTES)
        manifest = self._verified_inputs()
        if (set(record) != {"format", "attempt_id", "manifest_id", "payload_device",
                            "payload_inode", "content_device", "content_inode"}
                or record["format"] != "workbench-overlay-envelope-copy-complete-v2"
                or record["attempt_id"] != self.attempt_id
                or record["manifest_id"] != manifest["inventory_id"]):
            raise OverlayEnvelopeInputError("overlay.changed", "copied stage has no exact identity record")
        try:
            payload_fd = pinned_directory(self.stage.path, create=False)
            payload_info = os.fstat(payload_fd)
            if (payload_info.st_dev, payload_info.st_ino) != (
                    record["payload_device"], record["payload_inode"]):
                raise OverlayEnvelopeInputError("overlay.changed", "copied payload identity changed")
            content_fd = _open_directory(
                payload_fd, str(self.reservation["content_root"]), mount_id=_mount_id(payload_fd),
            )
            content_info = os.fstat(content_fd)
            if (content_info.st_dev, content_info.st_ino) != (
                    record["content_device"], record["content_inode"]):
                os.close(content_fd)
                raise OverlayEnvelopeInputError("overlay.changed", "copied content identity changed")
            return content_fd
        except (OSError, ValueError) as exc:
            if isinstance(exc, OverlayEnvelopeInputError):
                raise
            raise OverlayEnvelopeInputError("overlay.changed", "copied stage cannot be pinned") from exc
        finally:
            if "payload_fd" in locals():
                os.close(payload_fd)

    def _expected_copied_rows(self) -> dict[str, dict[str, object]]:
        manifest = self._verified_inputs()
        expected = [{"path": str(self.reservation["content_root"]),
                     "kind": "directory", "mode": 0o755,
                     "classification": "authoritative"}]
        for row in self._rows(manifest):
            path = f"{self.reservation['content_root']}/{row['path']}"
            if row["kind"] == "directory":
                expected.append({"path": path, "kind": "directory",
                                 "mode": 0o755 if row["mode"] is None else row["mode"],
                                 "classification": "authoritative"})
            else:
                expected.append({"path": path, "kind": "file",
                                 "mode": row["mode"], "size": row["size_bytes"],
                                 "sha256": row["sha256"],
                                 "classification": "authoritative"})
        return {str(row["path"]): row for row in expected}

    def _verify_stage_rows(self, expected: Mapping[str, dict[str, object]]) -> None:
        actual, root_mode, _files, _directories = inventory_exact_members(
            self.stage.path, cancelled=self.stage.host._cancelled,
        )
        if (root_mode != 0o700 or len(actual) != len(expected)
                or {row["path"]: row for row in actual}
                != expected):
            raise OverlayEnvelopeInputError("overlay.changed", "overlay stage differs from sealed effects")

    def _verify_unedited_stage(self) -> None:
        """Compare every copied member to sealed source rows before operations."""
        self._verify_stage_rows(self._expected_copied_rows())

    def _verify_effected_stage(self, effects: tuple[dict[str, object], ...]) -> None:
        expected = self._expected_copied_rows()
        content_root = str(self.reservation["content_root"])
        for effect in effects:
            relative = str(effect["relative_path"])
            path = f"{content_root}/{relative}"
            action = effect["op"]
            if action == "remove":
                expected.pop(path)
                continue
            if action == "add":
                parts = relative.split("/")
                for depth in range(1, len(parts)):
                    parent = f"{content_root}/{'/'.join(parts[:depth])}"
                    expected.setdefault(parent, {
                        "path": parent, "kind": "directory", "mode": 0o755,
                        "classification": "authoritative",
                    })
                mode = 0o644
            else:
                mode = int(expected[path]["mode"])
            data = effect["data"]
            expected[path] = {
                "path": path, "kind": "file", "mode": mode,
                "size": len(data), "sha256": sha256(data).hexdigest(),
                "classification": "authoritative",
            }
        self._verify_stage_rows(expected)

    def apply_effects(self, effects: tuple[dict[str, object], ...]) -> Path:
        """Apply sealed ordered effects once; retain any interrupted stage."""
        if sys.platform != "linux":
            raise OverlayEnvelopeInputError("overlay.filesystem", "overlay operations need Linux handles")
        self._verified_effects(effects)
        if (self.root / "operations").exists() or (self.root / "operations-complete.json").exists():
            raise OverlayEnvelopeInputError("overlay.state", "overlay operations were already attempted")
        if self.stage.renamed or self.stage.committed or self.stage.target.exists() or self.stage.target.is_symlink():
            raise OverlayEnvelopeInputError("overlay.changed", "overlay target is no longer reserved")
        content_fd = self._copied_stage()
        try:
            self._verify_unedited_stage()
            _private_directory(self.root / "operations")
            secure_private_path(self.root / "operations", directory=True)
            _seal, retained = self._read_effect_seal()
            mount_id = _mount_id(content_fd)
            for index, effect in enumerate(effects):
                self.stage.host.check_cancelled()
                if _effect_metadata(index, effect) != retained[index]:
                    raise OverlayEnvelopeInputError("overlay.plan", "operation bytes changed after sealing")
                effect_sha = sha256(_canonical(retained[index]) + b"\n").hexdigest()
                common = {"attempt_id": self.attempt_id, "index": index,
                          "effect_sha256": effect_sha}
                publish_immutable_bytes(
                    self.root / "operations" / f"{index:016d}-attempted.json",
                    _canonical({"format": "workbench-overlay-envelope-operation-attempted-v1",
                                **common}) + b"\n", byte_limit=_RECORD_BYTES,
                )
                self._apply_effect(content_fd, mount_id, effect)
                publish_immutable_bytes(
                    self.root / "operations" / f"{index:016d}-complete.json",
                    _canonical({"format": "workbench-overlay-envelope-operation-complete-v1",
                                **common}) + b"\n", byte_limit=_RECORD_BYTES,
                )
            self._verify_effected_stage(effects)
            stage_inventory = _stage_inventory(self.stage.path, cancelled=self.stage.host._cancelled)
            self.stage.host.check_cancelled()
            publish_immutable_bytes(self.root / "operations-complete.json", _canonical({
                "format": "workbench-overlay-envelope-operations-complete-v1",
                "attempt_id": self.attempt_id,
                "effect_count": len(retained),
                "effects_sha256": _seal["capacity"]["effects_sha256"],
                "stage_inventory": stage_inventory,
            }) + b"\n", byte_limit=_MANIFEST_BYTES)
        finally:
            os.close(content_fd)
        return self.stage.path / str(self.reservation["content_root"])

    @staticmethod
    def _apply_effect(root_fd: int, mount_id: int, effect: Mapping[str, object]) -> None:
        parts = _parts(effect["relative_path"])
        action = str(effect["op"])
        parent_fd = _target_parent(root_fd, parts, mount_id, create=action == "add")
        try:
            name = parts[-1]
            if action == "remove":
                _checked_target_file(parent_fd, name, str(effect["expected_sha256"]), mount_id)
                os.unlink(name, dir_fd=parent_fd)
                os.fsync(parent_fd)
                return
            data = effect["data"]
            if type(data) is not bytes:
                raise OverlayEnvelopeInputError("overlay.plan", "overlay operation bytes changed")
            mode = 0o644
            if action == "replace":
                prior = _checked_target_file(parent_fd, name, str(effect["expected_sha256"]), mount_id)
                mode = stat.S_IMODE(prior.st_mode)
                temporary = f".workbench-overlay-{uuid4().hex}.pending"
                target_name = temporary
            else:
                target_name = name
            target_fd = os.open(
                target_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600, dir_fd=parent_fd,
            )
            try:
                _write_all(target_fd, data)
                os.fchmod(target_fd, mode)
                os.fsync(target_fd)
            finally:
                os.close(target_fd)
            if action == "replace":
                _checked_target_file(parent_fd, name, str(effect["expected_sha256"]), mount_id)
                os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)

    @staticmethod
    def _copy_file(source_parent: int, target_parent: int, name: str,
                   row: Mapping[str, object], source_mount: int) -> None:
        visible = os.stat(name, dir_fd=source_parent, follow_symlinks=False)
        if (not stat.S_ISREG(visible.st_mode) or visible.st_nlink != 1
                or stat.S_IMODE(visible.st_mode) != row["mode"]
                or visible.st_size != row["size_bytes"]):
            raise OverlayEnvelopeInputError("overlay.source", "copied source file changed")
        source_fd = os.open(name, _FILE_FLAGS, dir_fd=source_parent)
        try:
            if _metadata(os.fstat(source_fd)) != _metadata(visible) or _mount_id(source_fd) != source_mount:
                raise OverlayEnvelopeInputError("overlay.source", "copied source file changed custody")
            target_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                                0o600, dir_fd=target_parent)
            try:
                digest = sha256()
                size = 0
                while block := os.read(source_fd, _CHUNK_BYTES):
                    digest.update(block)
                    size += len(block)
                    if size > row["size_bytes"]:
                        raise OverlayEnvelopeInputError("overlay.source", "copied source file grew")
                    view = memoryview(block)
                    while view:
                        written = os.write(target_fd, view)
                        if written <= 0:
                            raise OverlayEnvelopeInputError("overlay.write", "Core copy made no progress")
                        view = view[written:]
                if size != row["size_bytes"] or digest.hexdigest() != row["sha256"]:
                    raise OverlayEnvelopeInputError("overlay.source", "copied source bytes changed")
                if (_metadata(os.fstat(source_fd)) != _metadata(visible)
                        or _metadata(os.stat(name, dir_fd=source_parent, follow_symlinks=False)) != _metadata(visible)):
                    raise OverlayEnvelopeInputError("overlay.source", "copied source file changed during read")
                os.fchmod(target_fd, int(row["mode"]))
                os.fsync(target_fd)
            finally:
                os.close(target_fd)
        finally:
            os.close(source_fd)


__all__ = ["CoreOverlayEnvelopeInputs", "CoreOverlayEnvelopeInputAttempt", "OverlayEnvelopeInputError"]
