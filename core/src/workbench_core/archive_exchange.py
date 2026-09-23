"""Streaming, verified data exchange without execution or domain interpretation.

The canonical manifest identifies original payload bytes. Import receipts describe
local custody separately, and are never included in a subsequent export.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import sys
import unicodedata
from uuid import uuid4
import zipfile
import zlib

from . import check_storage as storage
from .filesystem_paths import native_path
from .host_filesystem import file_lease, fsync_directory, secure_private_path


MANIFEST_NAME = "manifest.json"
RECEIPT_NAME = "import-receipt.json"
SCHEMA = "workbench-archive-v1"
RECEIPT_SCHEMA = "workbench-archive-import-v1"
CHUNK_BYTES = 1024 * 1024
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_RESERVED = {"con", "prn", "aux", "nul", "clock$", "conin$", "conout$"}
_RESERVED.update(f"{prefix}{suffix}" for prefix in ("com", "lpt")
                 for suffix in (*"123456789", "¹", "²", "³"))


class ArchiveError(ValueError):
    """An exchange archive or its selected storage is not admissible."""


@dataclass(frozen=True)
class ArchiveLimits:
    max_members: int = 100_000
    max_payload_bytes: int = 64 * 1024**3
    max_manifest_bytes: int = 16 * 1024**2

    def __post_init__(self):
        for value in (self.max_members, self.max_payload_bytes, self.max_manifest_bytes):
            if type(value) is not int or value <= 0:
                raise ArchiveError("archive limits must be positive integers")


def _check_cancelled(cancelled):
    if cancelled():
        raise ArchiveError("archive exchange cancelled")


def _portable_name(value):
    try:
        path = storage.safe_path(value)
    except storage.CheckStorageError as exc:
        raise ArchiveError("archive member must use a portable relative path") from exc
    for part in path.parts:
        if (part.endswith((".", " ")) or part.split(".", 1)[0].casefold() in _RESERVED
                or any(ord(c) < 32 or ord(c) == 127 or c in '<>"|?*' for c in part)):
            raise ArchiveError("archive member is not portable to Windows")
        try:
            if len(part.encode("utf-16-le")) > 510:
                raise ArchiveError("archive member component exceeds its portable length")
        except UnicodeError as exc:
            raise ArchiveError("archive member contains invalid Unicode") from exc
    return path


def _name_key(value):
    return unicodedata.normalize("NFC", value).casefold()


def _validate_names(names, *, payload=False):
    seen = set()
    directories = set()
    for name in names:
        path = _portable_name(name)
        key = _name_key(name)
        if key in seen or key in directories:
            raise ArchiveError("archive contains duplicate or colliding member paths")
        if payload and _name_key(path.parts[0]) in {MANIFEST_NAME, RECEIPT_NAME}:
            raise ArchiveError("archive payload uses a reserved exchange record path")
        for parent in path.parents:
            if str(parent) != ".":
                parent_key = _name_key(parent.as_posix())
                if parent_key in seen:
                    raise ArchiveError("archive contains a file/directory path collision")
                directories.add(parent_key)
        seen.add(key)


def _ordinary(path, *, directory=False):
    selected = storage.ordinary(Path(path), directory=directory)
    # is_junction alone does not cover every Windows reparse-point type.
    for parent in (*reversed(selected.parents), selected):
        if getattr(native_path(parent).lstat(), "st_file_attributes", 0) & 0x400:
            raise ArchiveError("archive storage traverses a reparse point")
    return selected


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _same_opened_file(path_info, opened):
    # Windows path stat and fstat may expose different ctime clocks. Compare each
    # clock to itself before/after, and bind their common file identity separately.
    return (_stamp(path_info)[:-1] == _stamp(opened)[:-1]
            and stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1)


@contextmanager
def _stable_reader(path):
    selected = _ordinary(path)
    path_before = native_path(selected).lstat()
    descriptor = os.open(native_path(selected), os.O_RDONLY | getattr(os, "O_BINARY", 0)
                         | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened_before = os.fstat(descriptor)
        if not _same_opened_file(path_before, opened_before):
            raise ArchiveError("archive input changed while opening")
        with file_lease(descriptor, exclusive=False):
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                yield stream
                if _stamp(os.fstat(descriptor)) != _stamp(opened_before):
                    raise ArchiveError("archive input changed while reading")
            _ordinary(selected)
            if _stamp(native_path(selected).lstat()) != _stamp(path_before):
                raise ArchiveError("archive input path changed while reading")
    finally:
        os.close(descriptor)


def _copy_hash(source, destination, *, limit, cancelled):
    digest, size = sha256(), 0
    while True:
        _check_cancelled(cancelled)
        chunk = source.read(min(CHUNK_BYTES, limit - size + 1))
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise ArchiveError("archive payload exceeds its declared size or configured bound")
        digest.update(chunk)
        if destination is not None:
            destination.write(chunk)
    return {"size": size, "sha256": digest.hexdigest()}


def _unique_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ArchiveError("archive manifest contains duplicate JSON keys")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs)
        if not isinstance(value, dict) or storage.canonical(value) + b"\n" != raw:
            raise ArchiveError("archive manifest is not canonical JSON")
        return value
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise ArchiveError("archive manifest is not canonical unique-key JSON") from exc


def _manifest(raw, limits):
    if len(raw) > limits.max_manifest_bytes:
        raise ArchiveError("archive manifest exceeds its configured bound")
    value = _unique_json(raw)
    if set(value) != {"schema", "id", "metadata", "members", "payload_bytes"}:
        raise ArchiveError("archive manifest has unsupported fields")
    if value["schema"] != SCHEMA or not isinstance(value["metadata"], dict):
        raise ArchiveError("archive manifest has an unsupported schema or metadata")
    rows = value["members"]
    if not isinstance(rows, list) or not 0 < len(rows) <= limits.max_members:
        raise ArchiveError("archive manifest has an invalid member count")
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"path", "size", "sha256"}
                or type(row["size"]) is not int or row["size"] < 0
                or not isinstance(row["sha256"], str) or not _HASH.fullmatch(row["sha256"])):
            raise ArchiveError("archive manifest contains an invalid inventory entry")
    _validate_names([row["path"] for row in rows], payload=True)
    if rows != sorted(rows, key=lambda row: row["path"]):
        raise ArchiveError("archive inventory is not in canonical path order")
    total = sum(row["size"] for row in rows)
    if (type(value["payload_bytes"]) is not int or value["payload_bytes"] != total
            or total > limits.max_payload_bytes):
        raise ArchiveError("archive payload size is invalid or exceeds its configured bound")
    body = {key: val for key, val in value.items() if key != "id"}
    if storage.seal(SCHEMA, body) != value:
        raise ArchiveError("archive manifest identity does not match its contents")
    return value


def build_manifest(members, *, metadata: dict, limits=ArchiveLimits()):
    """Seal a caller's already-observed descriptors without rereading payload files.

    Domain exporters use this to bind ``expected_manifest`` to the exact bytes
    their validation admitted. This helper makes no claim to have read files.
    """
    try:
        rows = list(members)
        value = storage.seal(SCHEMA, {"schema": SCHEMA, "metadata": metadata,
            "members": sorted(rows, key=lambda row: row["path"]),
            "payload_bytes": sum(row["size"] for row in rows)})
        return _manifest(storage.canonical(value) + b"\n", limits)
    except (ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, ArchiveError):
            raise
        raise ArchiveError("cannot build an archive manifest from invalid descriptors") from exc


def _zip_inventory(archive, limits):
    infos = archive.infolist()
    if not 1 < len(infos) <= limits.max_members + 1:
        raise ArchiveError("archive has an invalid member count")
    _validate_names([info.filename for info in infos])
    for info in infos:
        kind = stat.S_IFMT(info.external_attr >> 16)
        if (info.orig_filename != info.filename or info.is_dir()
                or kind not in {0, stat.S_IFREG} or info.flag_bits & 1
                or info.external_attr & 0x410  # Windows directory/reparse attributes.
                or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
            raise ArchiveError("archive contains an unsupported member type or encoding")
    by_name = {info.filename: info for info in infos}
    info = by_name.get(MANIFEST_NAME)
    if info is None or info.file_size > limits.max_manifest_bytes:
        raise ArchiveError("archive lacks a bounded manifest")
    with archive.open(info) as stream:
        raw = stream.read(limits.max_manifest_bytes + 1)
    manifest = _manifest(raw, limits)
    expected = {row["path"] for row in manifest["members"]} | {MANIFEST_NAME}
    if set(by_name) != expected:
        raise ArchiveError("archive members do not match its exact inventory")
    if any(by_name[row["path"]].file_size != row["size"] for row in manifest["members"]):
        raise ArchiveError("archive member size disagrees with its inventory")
    return manifest, raw, by_name


def _bounded_zip_index(stream, limits):
    """Bound the central directory before ZipFile allocates its entry objects.

    This exchange format has no executable prefix, comments or trailing data.
    ZIP64 is admitted because completed observations can exceed four GiB.
    """
    stream.seek(0, os.SEEK_END)
    length = stream.tell()
    if length < 22:
        raise ArchiveError("archive is truncated")
    stream.seek(0)
    if stream.read(4) != b"PK\x03\x04":
        raise ArchiveError("archive must not contain an executable or arbitrary prefix")
    stream.seek(-22, os.SEEK_END)
    end = struct.unpack("<4s4H2LH", stream.read(22))
    signature, disk, central_disk, disk_count, count, size, offset, comment = end
    if signature != b"PK\x05\x06" or disk or central_disk or comment:
        raise ArchiveError("archive is truncated, split, commented or has trailing data")
    central_end = length - 22
    stream.seek(max(0, length - 42))
    has_zip64 = stream.read(4) == b"PK\x06\x07"
    if has_zip64 or count == 0xFFFF or disk_count == 0xFFFF or size == 0xFFFFFFFF or offset == 0xFFFFFFFF:
        if length < 98:
            raise ArchiveError("ZIP64 archive is truncated")
        stream.seek(length - 42)
        marker, zip_disk, zip_offset, disks = struct.unpack("<4sLQL", stream.read(20))
        if marker != b"PK\x06\x07" or zip_disk or disks != 1 or zip_offset > length - 98:
            raise ArchiveError("ZIP64 archive has an invalid locator")
        stream.seek(zip_offset)
        record = stream.read(56)
        if len(record) != 56:
            raise ArchiveError("ZIP64 archive is truncated")
        marker, record_size, _, _, disk, central_disk, disk_count, count, size, offset = (
            struct.unpack("<4sQ2H2L4Q", record))
        if (marker != b"PK\x06\x06" or record_size != 44 or disk or central_disk
                or zip_offset + 56 != length - 42):
            raise ArchiveError("ZIP64 archive has unsupported end records")
        central_end = zip_offset
    if (disk_count != count or not 1 < count <= limits.max_members + 1
            or size > 2 * limits.max_manifest_bytes + (limits.max_members + 1) * 128
            or offset + size != central_end):
        raise ArchiveError("archive central directory exceeds its bound or is inconsistent")
    stream.seek(0)


def _target(path):
    selected = Path(os.path.abspath(Path(path).expanduser()))
    _ordinary(selected.parent, directory=True)
    if native_path(selected).exists() or native_path(selected).is_symlink():
        raise ArchiveError("archive destination already exists")
    return selected


def _new_file(path):
    stream = native_path(path).open("xb")
    try:
        secure_private_path(path, directory=False)
    except BaseException:
        stream.close()
        native_path(path).unlink()
        raise
    return stream


def _zip_info(name):
    info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    return info


def export_archive(destination, members: Mapping[str, Path], *, metadata: dict,
                   limits=ArchiveLimits(), cancelled=lambda: False, expected_manifest=None):
    """Publish one new ZIP, streaming original regular files without rewriting them.

    ``expected_manifest`` binds a re-export to a previously verified object and
    refuses source changes between domain verification and archive creation.
    """
    temporary = None
    try:
        target = _target(destination)
        names = sorted(members)
        if not 0 < len(names) <= limits.max_members or not isinstance(metadata, dict):
            raise ArchiveError("archive requires bounded members and object metadata")
        _validate_names(names, payload=True)
        # Freeze opaque metadata before I/O; reject non-JSON/non-finite data.
        metadata = json.loads(storage.canonical(metadata))
        temporary = target.parent / (".archive-" + uuid4().hex + ".partial")
        rows, total = [], 0
        with _new_file(temporary) as output:
            with zipfile.ZipFile(output, "w", allowZip64=True) as archive:
                for name in names:
                    _check_cancelled(cancelled)
                    with _stable_reader(members[name]) as source:
                        with archive.open(_zip_info(name), "w", force_zip64=True) as sink:
                            info = _copy_hash(source, sink, limit=limits.max_payload_bytes - total,
                                              cancelled=cancelled)
                    rows.append({"path": name, **info})
                    total += info["size"]
                manifest = build_manifest(rows, metadata=metadata, limits=limits)
                raw = storage.canonical(manifest) + b"\n"
                _manifest(raw, limits)
                if expected_manifest is not None and manifest != expected_manifest:
                    raise ArchiveError("archive originals differ from the expected manifest")
                archive.writestr(_zip_info(MANIFEST_NAME), raw)
            output.flush()
            os.fsync(output.fileno())
        with _stable_reader(temporary) as source:
            archive_identity = _copy_hash(source, None, limit=native_path(temporary).stat().st_size,
                                          cancelled=cancelled)
        _check_cancelled(cancelled)
        _ordinary(target.parent, directory=True)
        os.link(native_path(temporary), native_path(target))
        native_path(temporary).unlink()
        temporary = None
        fsync_directory(target.parent)
        return {"manifest": manifest, "archive": {"path": str(target), **archive_identity}}
    except (OSError, ValueError, TypeError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, ArchiveError):
            raise
        raise ArchiveError(f"cannot export archive: {exc}") from exc
    finally:
        if temporary is not None:
            native_path(temporary).unlink(missing_ok=True)


def inspect_archive(archive, *, limits=ArchiveLimits(), cancelled=lambda: False):
    """Inspect only ZIP structure and manifest; this does not verify payload bytes."""
    try:
        _check_cancelled(cancelled)
        with _stable_reader(archive) as stream:
            _bounded_zip_index(stream, limits)
            with zipfile.ZipFile(stream) as source:
                return _zip_inventory(source, limits)[0]
    except (OSError, ValueError, zipfile.BadZipFile, zlib.error, struct.error, RuntimeError) as exc:
        if isinstance(exc, ArchiveError):
            raise
        raise ArchiveError(f"cannot inspect archive: {exc}") from exc


def _publish_directory(staging, destination):
    """Atomic directory publication that also refuses an existing empty directory."""
    _ordinary(staging, directory=True)
    _ordinary(destination.parent, directory=True)
    if os.name == "nt":
        os.rename(native_path(staging), native_path(destination))
    elif sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise ArchiveError("this host lacks atomic no-replace directory publication")
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                           ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(staging), -100, os.fsencode(destination), 1):
            code = ctypes.get_errno()
            if code == errno.EEXIST:
                raise ArchiveError("archive destination already exists")
            raise OSError(code, os.strerror(code), str(destination))
    else:
        raise ArchiveError("atomic archive import currently supports Windows and Linux")
    fsync_directory(destination.parent)


def _make_parents(root, path):
    relative = path.relative_to(root)
    current = root
    for name in relative.parts:
        current = current / name
        if not native_path(current).exists():
            native_path(current).mkdir(mode=0o700)
            secure_private_path(current, directory=True)
        _ordinary(current, directory=True)


def import_archive(archive, destination, *, validate: Callable[[Path, dict], dict] | None = None,
                   limits=ArchiveLimits(), cancelled=lambda: False):
    """Verify, optionally domain-validate, and publish a new private imported object.

    The validator runs on the private staging tree, must not change its originals,
    and returns JSON data for the separate local receipt. Nothing is executed.
    """
    staging = None
    try:
        target, selected = _target(destination), _ordinary(archive)
        with _stable_reader(selected) as stream:
            _bounded_zip_index(stream, limits)
            archive_identity = _copy_hash(stream, None, limit=native_path(selected).stat().st_size,
                                          cancelled=cancelled)
            stream.seek(0)
            _bounded_zip_index(stream, limits)
            with zipfile.ZipFile(stream) as source:
                manifest, raw, infos = _zip_inventory(source, limits)
                staging = target.parent / (".import-" + uuid4().hex + ".partial")
                native_path(staging).mkdir(mode=0o700)
                secure_private_path(staging, directory=True)
                for row in manifest["members"]:
                    _check_cancelled(cancelled)
                    path = staging.joinpath(*_portable_name(row["path"]).parts)
                    _make_parents(staging, path.parent)
                    with source.open(infos[row["path"]]) as member, _new_file(path) as output:
                        observed = _copy_hash(member, output, limit=row["size"], cancelled=cancelled)
                        output.flush()
                        os.fsync(output.fileno())
                    if observed != {key: row[key] for key in ("size", "sha256")}:
                        raise ArchiveError("archive member bytes do not match their inventory")
                storage.write_bytes(staging / MANIFEST_NAME, raw,
                                    byte_limit=limits.max_manifest_bytes)
        validation = validate(staging, manifest) if validate is not None else {}
        if not isinstance(validation, dict):
            raise ArchiveError("archive validator must return object metadata")
        # A validator may query databases, but must not silently alter or add data.
        if verify_directory(staging, limits=limits, cancelled=cancelled) != manifest:
            raise ArchiveError("archive originals changed during domain validation")
        receipt = storage.seal(RECEIPT_SCHEMA, {"schema": RECEIPT_SCHEMA,
            "manifest_id": manifest["id"], "destination": str(target),
            "archive": {"path": str(selected), **archive_identity}, "validation": validation,
            "imported_at": datetime.now(timezone.utc).isoformat()})
        storage.write_json(staging / RECEIPT_NAME, receipt, byte_limit=limits.max_manifest_bytes)
        _check_cancelled(cancelled)
        _publish_directory(staging, target)
        staging = None
        return receipt
    except (OSError, ValueError, TypeError, zipfile.BadZipFile, zlib.error, struct.error, RuntimeError) as exc:
        if isinstance(exc, ArchiveError):
            raise
        raise ArchiveError(f"cannot import archive: {exc}") from exc
    finally:
        if staging is not None and native_path(staging).exists():
            # Only our freshly created private staging directory is eligible.
            _ordinary(staging, directory=True)
            shutil.rmtree(native_path(staging))


def verify_directory(directory, *, limits=ArchiveLimits(), cancelled=lambda: False):
    """Verify the exact original inventory, allowing only a separate local receipt."""
    try:
        root = _ordinary(directory, directory=True)
        raw = storage.read_bytes(root / MANIFEST_NAME, byte_limit=limits.max_manifest_bytes)
        manifest = _manifest(raw, limits)
        allowed = {row["path"] for row in manifest["members"]} | {MANIFEST_NAME}
        receipt_path = root / RECEIPT_NAME
        if native_path(receipt_path).exists():
            receipt = storage.read_json(receipt_path, byte_limit=limits.max_manifest_bytes)
            if not isinstance(receipt, dict):
                raise ArchiveError("local import receipt is not an object")
            body = {key: value for key, value in receipt.items() if key != "id"}
            if (receipt.get("schema") != RECEIPT_SCHEMA
                    or receipt.get("manifest_id") != manifest["id"]
                    or storage.seal(RECEIPT_SCHEMA, body) != receipt):
                raise ArchiveError("local import receipt does not bind this archive")
            allowed.add(RECEIPT_NAME)
        allowed_dirs = {parent.as_posix() for name in allowed
                        for parent in _portable_name(name).parents if str(parent) != "."}
        observed = set()
        pending = [root]
        while pending:
            current = pending.pop()
            with os.scandir(native_path(current)) as entries:
                for entry in entries:
                    _check_cancelled(cancelled)
                    path = current / entry.name
                    name = path.relative_to(root).as_posix()
                    directory = entry.is_dir(follow_symlinks=False)
                    if name not in (allowed_dirs if directory else allowed):
                        raise ArchiveError("imported object contains unexpected files or directories")
                    _ordinary(path, directory=directory)
                    if directory:
                        pending.append(path)
                    else:
                        observed.add(name)
        if observed != allowed:
            raise ArchiveError("imported object lacks required original files")
        for row in manifest["members"]:
            with _stable_reader(root / row["path"]) as source:
                actual = _copy_hash(source, None, limit=row["size"], cancelled=cancelled)
            if actual != {key: row[key] for key in ("size", "sha256")}:
                raise ArchiveError("imported original bytes do not match their inventory")
        return manifest
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        if isinstance(exc, ArchiveError):
            raise
        raise ArchiveError(f"cannot verify imported object: {exc}") from exc
