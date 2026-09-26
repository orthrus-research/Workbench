"""Core custody for the inputs and first copy of a staged overlay envelope.

The owner supplies a validated, chunked inventory and a read-only source
validator. Core retains those inputs before copying and performs every write
to the managed-tree payload. A copied stage is deliberately not a publication:
the owner must still apply its plan, validate the whole result, and publish the
envelope through the managed-tree transaction.
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
            for name in expected_entries - {"plan", "inventory"}:
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
            if (root / "input.json").exists() and not (root / "manifest.json").exists():
                raise OverlayEnvelopeInputError("overlay.changed", "overlay inputs lack their manifest")
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
                    ("copy-complete.json", "workbench-overlay-envelope-copy-complete-v1"),
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
            status = ("copy-complete" if (root / "copy-complete.json").is_file()
                      else "copy-incomplete" if (root / "copy-attempted.json").is_file()
                      else "input-sealed" if (root / "input.json").is_file()
                      else "reserved-incomplete")
            rows.append({"attempt_id": reservation["attempt_id"], "status": status,
                         "path": str(root), "stage_path": reservation["stage_path"],
                         "tree_id": reservation["tree_id"],
                         "target": reservation["target"]})
        return tuple(rows)


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
        if (self.root / "copy-attempted.json").exists():
            raise OverlayEnvelopeInputError("overlay.state", "overlay capacity check followed copy")
        manifest = self._verified_inputs()
        files: dict[str, dict[str, object]] = {}
        directories = {str(self.reservation["content_root"])}
        source_bytes = 0
        for row in self._rows(manifest):
            path = str(row["path"])
            if row["kind"] == "directory":
                directories.add(f"{self.reservation['content_root']}/{path}")
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
            if (type(effect) is not dict
                    or set(effect) != {"op", "relative_path", "expected_sha256", "data"}
                    or effect["op"] not in {"add", "replace", "remove"}):
                raise OverlayEnvelopeInputError("overlay.plan", "overlay operation shape is invalid")
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
            data = effect["data"]
            if action == "add":
                if prior is not None or expected is not None:
                    raise OverlayEnvelopeInputError("overlay.plan", "overlay add precondition is invalid")
                additions += 1
                for depth in range(1, len(parts)):
                    directories.add(f"{self.reservation['content_root']}/{'/'.join(parts[:depth])}")
            elif (prior is None or expected != prior["sha256"]
                  or type(expected) is not str or _DIGEST.fullmatch(expected) is None):
                raise OverlayEnvelopeInputError("overlay.plan", "overlay existing-file precondition is invalid")
            if action == "remove":
                if data is not None:
                    raise OverlayEnvelopeInputError("overlay.plan", "overlay remove has output bytes")
            elif type(data) is not bytes:
                raise OverlayEnvelopeInputError("overlay.plan", "overlay write bytes are invalid")
            else:
                if len(data) > MAX_FILE_BYTES:
                    raise OverlayEnvelopeInputError("overlay.unsupported", "operation file exceeds V3 bound")
                planned_bytes += len(data)
            digest.update(_canonical({
                "index": count, "op": action, "relative_path": path,
                "expected_sha256": expected,
                "data_sha256": sha256(data).hexdigest() if data is not None else None,
                "data_bytes": len(data) if data is not None else None,
            }) + b"\n")
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

    def copy_source(self, *, verify_source: Callable[[Mapping[str, object], Iterable[bytes]], object]) -> Path:
        """Copy exactly the sealed rows; retain an incomplete stage after failure."""
        if sys.platform != "linux" or not callable(verify_source):
            raise OverlayEnvelopeInputError("overlay.filesystem", "overlay copy needs Linux no-follow handles")
        manifest = self._verified_inputs()
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
        publish_immutable_bytes(self.root / "copy-complete.json", _canonical({
            "format": "workbench-overlay-envelope-copy-complete-v1",
            "attempt_id": self.attempt_id, "manifest_id": manifest["inventory_id"],
        }) + b"\n", byte_limit=_MANIFEST_BYTES)
        return content

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
