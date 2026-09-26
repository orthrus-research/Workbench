"""Complete, chunked source inventory for a future GTCEu overlay transport.

This is a read-only V2 prerequisite. It does not publish an overlay or grant
custody of the source configuration to an inventory consumer.
"""

from __future__ import annotations

from contextlib import closing, contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Callable, Iterable, Iterator, Mapping
import unicodedata

from .inventory import (
    GtceuWorldgenValidationError,
    _config_inventory,
    canonical_json_bytes,
    parse_gtceu_worldgen_inventory,
)


FORMAT = "workbench-crucible-gtceu-overlay-copy-inventory-v2"
ID_PREFIX = "crucible-gtceu-overlay-copy:sha256:"
CHUNK_BYTES = 1024 * 1024
MANIFEST_BYTES = 16 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
    | {f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "¹²³"}
)
_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GtceuWorldgenValidationError(message)


def _portable_part(part: str) -> None:
    _require(
        part not in {"", ".", ".."}
        and not any(character in part for character in '\\/:\0<>"|?*')
        and not any(ord(character) < 32 or ord(character) == 127 for character in part)
        and not part.endswith((" ", "."))
        and part.split(".", 1)[0].upper() not in _WINDOWS_RESERVED
        and unicodedata.normalize("NFC", part) == part,
        f"GTCEu copied entry has a nonportable path component: {part!r}",
    )
    try:
        part.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise GtceuWorldgenValidationError("GTCEu copied entry path is not UTF-8") from exc


def _portable_path(value: str) -> None:
    _require(type(value) is str and value, "GTCEu copied entry path is missing")
    parts = value.split("/")
    for part in parts:
        _portable_part(part)
    _require(PurePosixPath(value).as_posix() == value, "GTCEu copied entry path is not canonical")


def _metadata(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
        info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _identity(info: os.stat_result) -> tuple[int, int, int]:
    return info.st_dev, info.st_ino, info.st_mode


def _mount_id(descriptor: int) -> int:
    try:
        with open(f"/proc/self/fdinfo/{descriptor}", "r", encoding="ascii") as stream:
            rows = stream.read().splitlines()
    except (OSError, UnicodeError) as exc:
        raise GtceuWorldgenValidationError("GTCEu V2 cannot inspect source mount identity") from exc
    values = [row.partition(":")[2].strip() for row in rows if row.startswith("mnt_id:")]
    _require(len(values) == 1 and values[0].isdigit(),
             "GTCEu V2 source mount identity is unavailable")
    return int(values[0])


@contextmanager
def _pinned_directory(path: Path) -> Iterator[int]:
    _require(
        sys.platform.startswith("linux") and hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW"),
        "GTCEu V2 copy inventory requires Linux no-follow handles and mount IDs",
    )
    selected = Path(os.path.abspath(os.fspath(path.expanduser())))
    descriptor = os.open(selected.anchor, _DIRECTORY_FLAGS)
    try:
        for part in selected.parts[1:]:
            next_descriptor = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        original = os.fstat(descriptor)
        original_mount_id = _mount_id(descriptor)
        yield descriptor
        with _pinned_again(selected) as observed:
            _require(
                _metadata(os.fstat(observed)) == _metadata(original)
                and _mount_id(observed) == original_mount_id,
                "GTCEu V2 copy inventory root changed during use",
            )
    except OSError as exc:
        raise GtceuWorldgenValidationError(f"cannot pin GTCEu directory {selected}: {exc}") from exc
    finally:
        os.close(descriptor)


@contextmanager
def _pinned_again(path: Path) -> Iterator[int]:
    descriptor = os.open(path.anchor, _DIRECTORY_FLAGS)
    try:
        for part in path.parts[1:]:
            next_descriptor = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _child_directory(parent: int, name: str, *, source_mount_id: int) -> Iterator[int]:
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    _require(stat.S_ISDIR(before.st_mode), f"GTCEu copied directory is redirected or unavailable: {name}")
    descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
    try:
        _require(_metadata(os.fstat(descriptor)) == _metadata(before), f"GTCEu copied directory changed: {name}")
        _require(_mount_id(descriptor) == source_mount_id,
                 f"GTCEu copied directory crosses a mount boundary: {name}")
        yield descriptor
        after = os.fstat(descriptor)
        visible = os.stat(name, dir_fd=parent, follow_symlinks=False)
        _require(
            _metadata(before) == _metadata(after) == _metadata(visible),
            f"GTCEu copied directory changed while inventoried: {name}",
        )
        reopened = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
        try:
            _require(_identity(os.fstat(reopened)) == _identity(before)
                     and _mount_id(reopened) == source_mount_id,
                     f"GTCEu copied directory changed mount identity: {name}")
        finally:
            os.close(reopened)
    finally:
        os.close(descriptor)


def _file_row(parent: int, name: str, relative: str, source_mount_id: int) -> dict[str, Any]:
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    _require(
        stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
        f"GTCEu copied file is linked or nonregular: {relative}",
    )
    descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
    try:
        opened = os.fstat(descriptor)
        _require(_metadata(opened) == _metadata(before), f"GTCEu copied file changed: {relative}")
        _require(_mount_id(descriptor) == source_mount_id,
                 f"GTCEu copied file crosses a mount boundary: {relative}")
        digest = sha256()
        size = 0
        while block := os.read(descriptor, 1024 * 1024):
            digest.update(block)
            size += len(block)
            _require(size <= before.st_size, f"GTCEu copied file grew during inventory: {relative}")
        after = os.fstat(descriptor)
        visible = os.stat(name, dir_fd=parent, follow_symlinks=False)
        _require(
            _metadata(before) == _metadata(after) == _metadata(visible) and size == before.st_size,
            f"GTCEu copied file changed while inventoried: {relative}",
        )
        reopened = os.open(name, _FILE_FLAGS, dir_fd=parent)
        try:
            _require(_identity(os.fstat(reopened)) == _identity(before)
                     and _mount_id(reopened) == source_mount_id,
                     f"GTCEu copied file changed mount identity: {relative}")
        finally:
            os.close(reopened)
        return {
            "path": relative, "kind": "file", "mode": stat.S_IMODE(before.st_mode),
            "size_bytes": size, "sha256": digest.hexdigest(),
        }
    finally:
        os.close(descriptor)


def _walk(parent: int, name: str, relative: str, source_mount_id: int) -> Iterator[dict[str, Any]]:
    _portable_path(relative)
    with _child_directory(parent, name, source_mount_id=source_mount_id) as directory:
        yield {
            "path": relative, "kind": "directory",
            "mode": stat.S_IMODE(os.fstat(directory).st_mode),
        }
        with os.scandir(directory) as entries:
            names = sorted(entry.name for entry in entries)
        folded: set[str] = set()
        for child in names:
            _portable_part(child)
            key = child.casefold()
            _require(key not in folded, f"GTCEu copied entries collide on portable hosts: {relative}/{child}")
            folded.add(key)
            child_relative = f"{relative}/{child}"
            info = os.stat(child, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                yield from _walk(directory, child, child_relative, source_mount_id)
            elif stat.S_ISREG(info.st_mode):
                yield _file_row(directory, child, child_relative, source_mount_id)
            else:
                raise GtceuWorldgenValidationError(
                    f"GTCEu copied entry is a symlink or special file: {child_relative}"
                )


def _source_rows(config_root: Path) -> Iterator[dict[str, Any]]:
    with _pinned_directory(config_root) as root:
        source_mount_id = _mount_id(root)
        for name in ("dimensions.json", "worldgen", "worldgen_extracted.json"):
            try:
                info = os.stat(name, dir_fd=root, follow_symlinks=False)
            except FileNotFoundError:
                _require(name != "worldgen", "GTCEu worldgen directory is missing")
                continue
            if name != "worldgen":
                _require(stat.S_ISREG(info.st_mode), f"GTCEu optional copied file is redirected: {name}")
                yield _file_row(root, name, name, source_mount_id)
                continue
            with _child_directory(root, "worldgen", source_mount_id=source_mount_id) as worldgen:
                # copytree creates this parent; it does not copy its source mode.
                yield {"path": "worldgen", "kind": "directory", "mode": None}
                for subtree in ("fluid", "vein"):
                    yield from _walk(worldgen, subtree, f"worldgen/{subtree}", source_mount_id)


def _summary(rows: Iterator[dict[str, Any]]) -> dict[str, Any]:
    digest = sha256()
    files = directories = total_bytes = 0
    for row in rows:
        digest.update(canonical_json_bytes(row) + b"\n")
        if row["kind"] == "file":
            files += 1
            total_bytes += row["size_bytes"]
        else:
            directories += 1
    return {
        "entry_count": files + directories,
        "file_count": files,
        "directory_count": directories,
        "total_bytes": total_bytes,
        "entries_sha256": digest.hexdigest(),
    }


def _manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    _require(type(value) is dict, "GTCEu V2 copy manifest must be an object")
    try:
        size = len(canonical_json_bytes(value) + b"\n")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise GtceuWorldgenValidationError("GTCEu V2 copy manifest is invalid") from exc
    _require(0 < size <= MANIFEST_BYTES, "GTCEu V2 copy manifest size drift")
    expected = {
        "format", "schema_version", "inventory_id", "source_inventory_id",
        "source_root_uri", "entry_count", "file_count", "directory_count",
        "total_bytes", "entries_sha256", "chunk_count", "chunks_sha256",
    }
    _require(set(value) == expected and value["format"] == FORMAT and value["schema_version"] == 2,
             "GTCEu V2 copy manifest fields drift")
    for name in ("entry_count", "file_count", "directory_count", "total_bytes", "chunk_count"):
        _require(type(value[name]) is int and value[name] >= 0, f"GTCEu V2 {name} is invalid")
    _require(value["entry_count"] == value["file_count"] + value["directory_count"]
             and value["entry_count"] >= 3 and value["chunk_count"] >= 1,
             "GTCEu V2 copy manifest counts drift")
    for name in ("entries_sha256", "chunks_sha256"):
        _require(type(value[name]) is str and _DIGEST.fullmatch(value[name]) is not None,
                 f"GTCEu V2 {name} is invalid")
    _require(type(value["source_root_uri"]) is str and value["source_root_uri"].startswith("file://"),
             "GTCEu V2 source root URI is invalid")
    _require(type(value["source_inventory_id"]) is str
             and re.fullmatch(r"crucible-gtceu-worldgen:sha256:[0-9a-f]{64}", value["source_inventory_id"]) is not None,
             "GTCEu V2 source inventory ID is invalid")
    identity = dict(value)
    identity["inventory_id"] = ""
    _require(value["inventory_id"] == ID_PREFIX + sha256(canonical_json_bytes(identity)).hexdigest(),
             "GTCEu V2 copy inventory ID drift")
    return dict(value)


def _checked_row(raw: bytes) -> dict[str, Any]:
    try:
        row = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GtceuWorldgenValidationError("GTCEu V2 copied entry is invalid JSON") from exc
    _require(type(row) is dict and canonical_json_bytes(row) + b"\n" == raw,
             "GTCEu V2 copied entry is not canonical")
    path = row.get("path")
    _portable_path(path)
    _require(path in {"dimensions.json", "worldgen", "worldgen_extracted.json"}
             or path.startswith("worldgen/fluid/") or path.startswith("worldgen/vein/")
             or path in {"worldgen/fluid", "worldgen/vein"},
             "GTCEu V2 copied entry lies outside the copied tree")
    _require(
        (row.get("kind") == "file" if path in {"dimensions.json", "worldgen_extracted.json"}
         else row.get("kind") == "directory" if path in {"worldgen", "worldgen/fluid", "worldgen/vein"}
         else row.get("kind") in {"file", "directory"}),
        "GTCEu V2 copied entry has an invalid kind for its path",
    )
    if row.get("kind") == "directory":
        _require(set(row) == {"path", "kind", "mode"}
                 and (row["mode"] is None if path == "worldgen" else
                      type(row["mode"]) is int and 0 <= row["mode"] <= 0o7777),
                 "GTCEu V2 copied directory fields drift")
    else:
        _require(row.get("kind") == "file"
                 and set(row) == {"path", "kind", "mode", "size_bytes", "sha256"}
                 and type(row["mode"]) is int and 0 <= row["mode"] <= 0o7777
                 and type(row["size_bytes"]) is int and row["size_bytes"] >= 0
                 and type(row["sha256"]) is str and _DIGEST.fullmatch(row["sha256"]) is not None,
                 "GTCEu V2 copied file fields drift")
    return row


def _verify_chunks(
    manifest: Mapping[str, Any], chunks: Iterable[bytes],
    source_rows: Iterator[dict[str, Any]] | None = None,
) -> None:
    chunks_digest = sha256()
    entries_digest = sha256()
    files = directories = total_bytes = 0
    previous = ""
    ancestors: list[tuple[str, set[str]]] = [("", set())]
    required: set[str] = set()
    records = iter(chunks)
    missing = object()
    for index in range(manifest["chunk_count"]):
        raw = next(records, missing)
        _require(raw is not missing, "GTCEu V2 inventory chunk set is incomplete")
        _require(type(raw) is bytes and 0 < len(raw) <= CHUNK_BYTES,
                 "GTCEu V2 inventory chunk exceeds its bound")
        chunks_digest.update(f"{index}\0{len(raw)}\0{sha256(raw).hexdigest()}\n".encode("ascii"))
        _require(raw.endswith(b"\n"), "GTCEu V2 inventory chunk has a partial line")
        for line in raw.splitlines(keepends=True):
            row = _checked_row(line)
            if source_rows is not None:
                _require(next(source_rows, None) == row,
                         f"GTCEu V2 copied source changed: {row['path']}")
            path = row["path"]
            _require(path > previous, "GTCEu V2 copied entries are unordered or duplicated")
            previous = path
            parent = path.rpartition("/")[0]
            while ancestors and ancestors[-1][0] != parent:
                ancestors.pop()
            _require(bool(ancestors),
                     "GTCEu V2 copied entry lacks its parent directory")
            folded = path.rpartition("/")[2].casefold()
            _require(folded not in ancestors[-1][1],
                     "GTCEu V2 copied entries collide on portable hosts")
            ancestors[-1][1].add(folded)
            if row["kind"] == "file":
                files += 1
                total_bytes += row["size_bytes"]
            else:
                directories += 1
                ancestors.append((path, set()))
                if path in {"worldgen", "worldgen/fluid", "worldgen/vein"}:
                    required.add(path)
            entries_digest.update(line)
    _require(next(records, missing) is missing, "GTCEu V2 inventory has an unexpected chunk")
    if source_rows is not None:
        _require(next(source_rows, None) is None, "GTCEu V2 copied source gained an entry")
    _require(required == {"worldgen", "worldgen/fluid", "worldgen/vein"},
             "GTCEu V2 inventory lacks required copied directories")
    _require((files, directories, total_bytes, files + directories, entries_digest.hexdigest(), chunks_digest.hexdigest())
             == (manifest["file_count"], manifest["directory_count"], manifest["total_bytes"],
                 manifest["entry_count"], manifest["entries_sha256"], manifest["chunks_sha256"]),
             "GTCEu V2 inventory aggregate drift")


def parse_gtceu_overlay_copy_inventory(
    manifest: Mapping[str, Any], chunks: Iterable[bytes],
) -> dict[str, Any]:
    """Validate an in-memory manifest and each ordered bounded chunk."""

    selected = _manifest(manifest)
    _verify_chunks(selected, chunks)
    return selected


def build_gtceu_overlay_copy_inventory(
    *, config_root: Path, source_inventory: Mapping[str, Any],
    emit_chunk: Callable[[int, bytes], None],
) -> dict[str, Any]:
    """Read every V1-copyable entry and stream bounded records to a caller.

    This function does no allocation or writing. The caller owns any physical
    custody and must discard emitted chunks if a final manifest is not returned.
    """

    selected = parse_gtceu_worldgen_inventory(source_inventory)
    source = Path(os.path.abspath(os.fspath(config_root.expanduser())))
    with closing(_source_rows(source)) as source_rows:
        first = _summary(source_rows)
    current_config, _definitions = _config_inventory(source)
    _require(current_config == selected["configuration"],
             "GTCEu V2 source configuration differs from its V1 inventory")
    chunk = bytearray()
    chunks_digest = sha256()
    entries_digest = sha256()
    files = directories = total_bytes = count = 0

    def flush() -> None:
        nonlocal count
        if not chunk:
            return
        raw = bytes(chunk)
        emit_chunk(count, raw)
        chunks_digest.update(f"{count}\0{len(raw)}\0{sha256(raw).hexdigest()}\n".encode("ascii"))
        count += 1
        chunk.clear()

    with closing(_source_rows(source)) as source_rows:
        for row in source_rows:
            line = canonical_json_bytes(row) + b"\n"
            _require(len(line) <= CHUNK_BYTES,
                     "GTCEu V2 copied entry exceeds one chunk")
            if len(chunk) + len(line) > CHUNK_BYTES:
                flush()
            chunk.extend(line)
            entries_digest.update(line)
            if row["kind"] == "file":
                files += 1
                total_bytes += row["size_bytes"]
            else:
                directories += 1
    flush()
    second = {
        "entry_count": files + directories, "file_count": files,
        "directory_count": directories, "total_bytes": total_bytes,
        "entries_sha256": entries_digest.hexdigest(),
    }
    _require(second == first, "GTCEu V2 source changed between inventory scans")
    body = {
        "format": FORMAT, "schema_version": 2,
        "inventory_id": "", "source_inventory_id": selected["inventory_id"],
        "source_root_uri": source.as_uri(), **second,
        "chunk_count": count, "chunks_sha256": chunks_digest.hexdigest(),
    }
    body["inventory_id"] = ID_PREFIX + sha256(canonical_json_bytes(body)).hexdigest()
    return _manifest(body)


def verify_gtceu_overlay_copy_source(
    *, config_root: Path, source_inventory: Mapping[str, Any],
    manifest: Mapping[str, Any], chunks: Iterable[bytes], expected_inventory_id: str,
) -> dict[str, Any]:
    """Recheck every selected input row before a later copy or publication."""

    selected = parse_gtceu_worldgen_inventory(source_inventory)
    selected_manifest = _manifest(manifest)
    source = Path(os.path.abspath(os.fspath(config_root.expanduser())))
    _require(selected_manifest["inventory_id"] == expected_inventory_id,
             "GTCEu V2 copy inventory changed after selection")
    _require(selected_manifest["source_inventory_id"] == selected["inventory_id"]
             and selected_manifest["source_root_uri"] == source.as_uri(),
             "GTCEu V2 inventory is bound to another source")
    with closing(_source_rows(source)) as actual:
        _verify_chunks(selected_manifest, chunks, actual)
    current_config, _definitions = _config_inventory(source)
    _require(current_config == selected["configuration"],
             "GTCEu V2 source definitions changed after capture")
    return selected_manifest


__all__ = [
    "FORMAT", "ID_PREFIX", "CHUNK_BYTES", "build_gtceu_overlay_copy_inventory",
    "parse_gtceu_overlay_copy_inventory", "verify_gtceu_overlay_copy_source",
]
