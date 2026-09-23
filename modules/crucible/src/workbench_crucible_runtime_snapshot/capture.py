"""Read sealed V1 runtime capture bytes without interpreting domain records.

The producer's canonical JSON preserves numeric spelling and escapes every
control character as a Unicode escape. Hash the original number tokens rather
than round-tripping them through Python floats.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterable, Mapping


class RuntimeCaptureError(ValueError):
    """Retained capture custody or a requested category is invalid."""


class _Number(str):
    pass


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeCaptureError("capture JSON contains a duplicate object key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise RuntimeCaptureError(f"capture JSON contains a nonfinite number: {value}")


def _parse(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_object, parse_int=_Number,
                          parse_float=_Number, parse_constant=_constant)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise RuntimeCaptureError(f"invalid capture JSON: {exc}") from exc


def capture_canonical_bytes(value: Any) -> bytes:
    """Canonical bytes matching the retained Java capture producer."""
    def encode(row: Any) -> str:
        if isinstance(row, _Number):
            return str(row)
        if row is None:
            return "null"
        if type(row) is bool:
            return "true" if row else "false"
        if type(row) in (int, float):
            return json.dumps(row, allow_nan=False)
        if type(row) is str:
            return '"' + ''.join(
                '\\"' if char == '"' else '\\\\' if char == '\\' else
                f"\\u{ord(char):04x}" if ord(char) < 32 else char
                for char in row
            ) + '"'
        if type(row) is list:
            return "[" + ",".join(encode(item) for item in row) + "]"
        if type(row) is dict:
            if any(type(key) is not str for key in row):
                raise RuntimeCaptureError("capture JSON object keys must be strings")
            return "{" + ",".join(encode(key) + ":" + encode(row[key])
                                   for key in sorted(row)) + "}"
        raise RuntimeCaptureError("unsupported capture JSON value")
    try:
        return encode(value).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise RuntimeCaptureError(f"invalid canonical capture JSON: {exc}") from exc


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _seal(value: dict[str, Any], field: str) -> None:
    if not isinstance(value, dict) or value.get(field) != _sha(
        capture_canonical_bytes({key: row for key, row in value.items() if key != field})
    ):
        raise RuntimeCaptureError(f"capture {field} does not match its content")


def _plain(value: Any) -> Any:
    if isinstance(value, _Number):
        number = json.loads(value)
        if type(number) is float and (
            not math.isfinite(number)
            or number == 0.0 and any(c in "123456789" for c in re.split("[eE]", value)[0])
        ):
            raise RuntimeCaptureError("capture number exceeds the supported finite numeric range")
        return number
    if type(value) is list:
        return [_plain(row) for row in value]
    if type(value) is dict:
        return {key: _plain(row) for key, row in value.items()}
    return value


def _canonical_document(raw: bytes) -> dict[str, Any]:
    value = _parse(raw)
    if type(value) is not dict or capture_canonical_bytes(value) != raw:
        raise RuntimeCaptureError("capture document is not a canonical UTF-8 object")
    return value


def _text(value: Any) -> bool:
    return type(value) is str and bool(value) and not any(ord(c) < 32 for c in value)


def _count(value: Any, expected: int) -> bool:
    return type(value) is int and value == expected


@dataclass(frozen=True)
class RetainedRuntimeCapture:
    root: Path
    manifest: dict[str, Any]
    manifest_file_sha256: str
    categories: dict[str, dict[str, Any]]
    record_sha256: dict[str, tuple[str, ...]]
    record_field_sha256: dict[str, tuple[dict[str, str], ...]]
    input_manifest: dict[str, Any]
    verified_payload_bytes: int


@dataclass(frozen=True)
class RetainedCapturePayload:
    """Verified auxiliary values and hashes preserving original number tokens."""

    value: dict[str, Any]
    file_sha256: str
    record_sha256: dict[str, tuple[str, ...]]
    record_field_sha256: dict[str, tuple[dict[str, str], ...]]


def _safe_payload_name(value: Any) -> bool:
    return (type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) is not None
            and value not in {".", "..", "manifest.json"})


def _file_state(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def _read_payload_member(path: Path, *, limit: int, exact_size: int | None = None) -> tuple[bytes, tuple[int, ...]]:
    path_before = path.lstat()
    if not stat.S_ISREG(path_before.st_mode):
        raise RuntimeCaptureError(f"capture member is not a regular file: {path}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_BINARY", 0))
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeCaptureError("capture member is not a regular file")
        # Windows path and handle stat can expose different ctime clocks. Bind
        # their common identity, then compare each full state to its own prior
        # observation so neither replacement nor clock changes are ignored.
        if _file_state(path_before)[:-1] != _file_state(before)[:-1]:
            raise RuntimeCaptureError("capture member changed before read")
        if before.st_size > limit or (exact_size is not None and before.st_size != exact_size):
            raise RuntimeCaptureError("capture member exceeds or differs from its byte bound")
        raw = stream.read(before.st_size + 1)
        after = os.fstat(stream.fileno())
    retained = path.lstat()
    if (len(raw) != before.st_size or _file_state(before) != _file_state(after)
            or _file_state(path_before) != _file_state(retained)):
        raise RuntimeCaptureError("capture member changed while read")
    return raw, _file_state(retained)


def _pointer(value: Any, pointer: str) -> Any:
    if type(pointer) is not str or (pointer and not pointer.startswith("/")) or re.search(r"~(?![01])", pointer):
        raise RuntimeCaptureError("record array paths must be valid RFC 6901 JSON pointers")
    for encoded in pointer.split("/")[1:] if pointer else ():
        token = encoded.replace("~1", "/").replace("~0", "~")
        if type(value) is dict and token in value:
            value = value[token]
        elif type(value) is list and re.fullmatch(r"0|[1-9][0-9]*", token):
            try:
                value = value[int(token)]
            except (IndexError, ValueError) as exc:
                raise RuntimeCaptureError("record array pointer is absent") from exc
        else:
            raise RuntimeCaptureError("record array pointer is absent")
    return value


def read_capture_payload(
    capture: RetainedRuntimeCapture, filename: str, *,
    record_arrays: Mapping[str, str] | None = None,
    record_digest_fields: Mapping[str, tuple[str, ...]] | None = None,
) -> RetainedCapturePayload:
    """Reopen a declared canonical object payload without interpreting its domain.

    ``record_arrays`` maps caller labels to RFC 6901 pointers to object arrays.
    Optional field digests name top-level fields in each selected record. Hashes
    retain Java numeric spellings; returned values use ordinary Python numbers.
    The fresh sealed manifest, never the mutable ``capture.manifest`` dictionary,
    supplies the payload descriptor. This does not promote a category's status.
    """
    if not isinstance(capture, RetainedRuntimeCapture) or not _safe_payload_name(filename):
        raise RuntimeCaptureError("payload reader requires a retained capture and safe declared filename")
    arrays = {} if record_arrays is None else record_arrays
    fields = {} if record_digest_fields is None else record_digest_fields
    if (not isinstance(arrays, Mapping) or any(not _text(key) or type(pointer) is not str
                                               for key, pointer in arrays.items())
            or not isinstance(fields, Mapping) or any(
                key not in arrays or type(names) is not tuple
                or any(not _text(name) for name in names) or len(set(names)) != len(names)
                for key, names in fields.items())):
        raise RuntimeCaptureError("payload record requests must name object arrays and unique record fields")
    try:
        root = Path(capture.root)
        if root.is_symlink() or not root.is_dir() or root.resolve() != root:
            raise RuntimeCaptureError("capture root is not the retained regular directory")
        root_state = _file_state(root.lstat())
        marker_path, manifest_path = root / ".capture-complete", root / "manifest.json"
        _, marker_state = _read_payload_member(marker_path, limit=0, exact_size=0)
        manifest_raw, manifest_state = _read_payload_member(manifest_path, limit=16 * 1024 * 1024)
        if _sha(manifest_raw) != capture.manifest_file_sha256:
            raise RuntimeCaptureError("retained capture manifest file hash differs")
        manifest_tokens = _canonical_document(manifest_raw)
        _seal(manifest_tokens, "manifest_sha256")
        manifest = _plain(manifest_tokens)
        if (manifest.get("format") != "workbench-runtime-graph-raw-bundle-v1"
                or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1
                or type(manifest.get("payloads")) is not list):
            raise RuntimeCaptureError("unsupported runtime capture manifest")
        descriptor, previous = None, ""
        for row in manifest["payloads"]:
            if (type(row) is not dict or not _safe_payload_name(row.get("file"))
                    or row["file"] <= previous or type(row.get("size")) is not int or row["size"] < 0
                    or type(row.get("sha256")) is not str or re.fullmatch(r"[a-f0-9]{64}", row["sha256"]) is None):
                raise RuntimeCaptureError("capture payload descriptor is invalid or duplicated")
            previous = row["file"]
            if row["file"] == filename:
                descriptor = row
        if descriptor is None:
            raise RuntimeCaptureError("requested payload is not declared in the retained manifest")
        path = root / filename
        raw, payload_state = _read_payload_member(path, limit=descriptor["size"], exact_size=descriptor["size"])
        digest = _sha(raw)
        if digest != descriptor["sha256"]:
            raise RuntimeCaptureError("capture payload digest/size mismatch")
        tokens = _canonical_document(raw)
        record_hashes, field_hashes = {}, {}
        for label, pointer in arrays.items():
            records = _pointer(tokens, pointer)
            if type(records) is not list or any(type(row) is not dict for row in records):
                raise RuntimeCaptureError("record array pointer must resolve to an array of objects")
            names = fields.get(label, ())
            if any(name not in row for row in records for name in names):
                raise RuntimeCaptureError("requested payload record field is absent")
            record_hashes[label] = tuple(_sha(capture_canonical_bytes(row)) for row in records)
            field_hashes[label] = tuple({name: _sha(capture_canonical_bytes(row[name])) for name in names}
                                       for row in records)
        value = _plain(tokens)
        # Keep the marker and sealed authority current through parsing and hashing.
        if root.resolve() != root:
            raise RuntimeCaptureError("retained capture path changed during payload verification")
        for member, state in ((root, root_state), (marker_path, marker_state),
                              (manifest_path, manifest_state), (path, payload_state)):
            if _file_state(member.lstat()) != state:
                raise RuntimeCaptureError("retained capture changed during payload verification")
        return RetainedCapturePayload(value, digest, record_hashes, field_hashes)
    except RuntimeCaptureError:
        raise
    except (OSError, TypeError, ValueError, KeyError, AttributeError, RecursionError) as exc:
        raise RuntimeCaptureError(f"cannot read retained capture payload: {exc}") from exc


def read_runtime_capture(
    root: Path, *, categories: Iterable[str], input_manifest: Path,
    max_source_bytes: int = 1024 * 1024 * 1024,
    record_digest_fields: Mapping[str, tuple[str, ...]] | None = None,
) -> RetainedRuntimeCapture:
    """Verify all payload bytes and fully admit only the requested categories.

    A valid seal proves retained content integrity, not a trusted execution or
    profile-support decision. Unselected partial categories remain partial.
    """
    if type(max_source_bytes) is not int or max_source_bytes < 1:
        raise RuntimeCaptureError("capture source-byte bound must be positive")
    if isinstance(categories, (str, bytes)):
        raise RuntimeCaptureError("capture categories must be an iterable of adapter IDs")
    try:
        requested = set(categories)
    except TypeError as exc:
        raise RuntimeCaptureError("capture categories must contain adapter IDs") from exc
    if not requested or any(type(row) is not str or not row for row in requested):
        raise RuntimeCaptureError("capture requires explicit category adapter IDs")
    digest_fields = record_digest_fields if record_digest_fields is not None else {}
    if not isinstance(digest_fields, Mapping) or any(
        key not in requested or type(fields) is not tuple
        or any(not _text(field) for field in fields) or len(set(fields)) != len(fields)
        for key, fields in digest_fields.items()
    ):
        raise RuntimeCaptureError("record digest fields must name unique fields for requested adapters")
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise RuntimeCaptureError("capture root is not a regular directory")
    root = root.resolve()
    consumed = 0

    def read(path: Path, *, limit: int | None = None) -> bytes:
        nonlocal consumed
        if path.is_symlink() or not path.is_file():
            raise RuntimeCaptureError(f"capture member is not a regular file: {path}")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_BINARY", 0))
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise RuntimeCaptureError("capture member is not a regular file")
            size = before.st_size
            if size > (limit if limit is not None else max_source_bytes) or consumed + size > max_source_bytes:
                raise RuntimeCaptureError("capture exceeds the source-byte bound")
            data = stream.read(size + 1)
            after = os.fstat(stream.fileno())
        if len(data) != size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns
        ):
            raise RuntimeCaptureError("capture member changed while read")
        consumed += size
        return data

    try:
        marker = root / ".capture-complete"
        if read(marker, limit=0):
            raise RuntimeCaptureError("capture completion marker is absent or invalid")
        manifest_raw = read(root / "manifest.json", limit=16 * 1024 * 1024)
        manifest_tokens = _canonical_document(manifest_raw)
        _seal(manifest_tokens, "manifest_sha256")
        manifest = _plain(manifest_tokens)
        if (manifest.get("format") != "workbench-runtime-graph-raw-bundle-v1"
                or type(manifest.get("schema_version")) is not int
                or manifest["schema_version"] != 1):
            raise RuntimeCaptureError("unsupported runtime capture manifest")
        binding_keys = ("capture_id", "launch_id", "input_manifest_sha256",
                        "candidate_lock_sha256", "adapter_profile_sha256", "physical_side")
        for key in binding_keys:
            value = manifest.get(key)
            if not _text(value):
                raise RuntimeCaptureError(f"invalid capture binding: {key}")
            if key.endswith("sha256") and re.fullmatch(r"[a-f0-9]{64}", value) is None:
                raise RuntimeCaptureError(f"invalid capture digest: {key}")
        input_raw = read(Path(input_manifest), limit=32 * 1024 * 1024)
        if _sha(input_raw) != manifest["input_manifest_sha256"]:
            raise RuntimeCaptureError("capture input manifest does not match retained binding")
        inputs = _plain(_parse(input_raw))
        if type(inputs) is not dict or any(
            inputs.get(field) != manifest[field]
            for field in ("physical_side", "capture_id", "launch_id")
        ):
            raise RuntimeCaptureError("capture input manifest launch, capture or physical side differs")
        descriptors = manifest.get("categories")
        payloads = manifest.get("payloads")
        if type(descriptors) is not list or type(payloads) is not list:
            raise RuntimeCaptureError("capture category or payload table is absent")
        by_adapter: dict[str, dict[str, Any]] = {}
        category_files: set[str] = set()
        for row in descriptors:
            if (type(row) is not dict or type(row.get("adapter_id")) is not str
                    or not _text(row["adapter_id"])
                    or row["adapter_id"] in by_adapter or type(row.get("file")) is not str
                    or row["file"] != row["adapter_id"] + ".json"
                    or row.get("status") not in {"complete", "partial", "failed"}
                    or row["file"] in category_files):
                raise RuntimeCaptureError("capture category descriptor is invalid or duplicated")
            by_adapter[row["adapter_id"]] = row
            category_files.add(row["file"])
        if list(by_adapter) != sorted(by_adapter):
            raise RuntimeCaptureError("capture category descriptors are not ordered")
        if not requested.issubset(by_adapter):
            raise RuntimeCaptureError("capture is missing a requested category")
        selected_files = {by_adapter[key]["file"]: key for key in requested}
        admitted: dict[str, dict[str, Any]] = {}
        record_hashes: dict[str, tuple[str, ...]] = {}
        field_hashes: dict[str, tuple[dict[str, str], ...]] = {}
        seen: set[str] = set()
        previous_name = ""
        for payload in payloads:
            if type(payload) is not dict:
                raise RuntimeCaptureError("invalid capture payload descriptor")
            name = payload.get("file")
            if (type(name) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) is None
                    or name in {".", "..", "manifest.json"} or name in seen
                    or name < previous_name):
                raise RuntimeCaptureError("capture payload path is unsafe or duplicated")
            seen.add(name)
            previous_name = name
            raw = read(root / name)
            if type(payload.get("size")) is not int or len(raw) != payload["size"] or _sha(raw) != payload.get("sha256"):
                raise RuntimeCaptureError(f"capture payload digest/size mismatch: {name}")
            if name not in selected_files:
                continue
            key = selected_files[name]
            tokens = _canonical_document(raw)
            _seal(tokens, "result_sha256")
            records = tokens.get("records")
            if type(records) is not list or _sha(capture_canonical_bytes(records)) != tokens.get("records_sha256"):
                raise RuntimeCaptureError(f"capture records seal differs: {key}")
            if any(type(row) is not dict for row in records):
                raise RuntimeCaptureError(f"capture records must be objects: {key}")
            hashes: list[str] = []
            previous_record: bytes | None = None
            for row in records:
                raw_record = capture_canonical_bytes(row)
                if previous_record is not None and previous_record > raw_record:
                    raise RuntimeCaptureError(f"capture records are not canonically ordered: {key}")
                hashes.append(_sha(raw_record))
                previous_record = raw_record
            record_hashes[key] = tuple(hashes)
            fields = digest_fields.get(key, ())
            field_hashes[key] = tuple({field: _sha(capture_canonical_bytes(row[field]))
                                      for field in fields} for row in records)
            value = _plain(tokens)
            if (value.get("format") != "workbench-crucible-runtime-category-result-v1"
                    or type(value.get("schema_version")) is not int or value["schema_version"] != 1
                    or value.get("adapter_id") != key
                    or not _text(value.get("category_id"))
                    or not _text(value.get("checkpoint_id"))
                    or any(value.get(field) != manifest[field] for field in binding_keys)):
                raise RuntimeCaptureError(f"capture category binding differs: {key}")
            if (by_adapter[key].get("status") != "complete" or value.get("status") != "complete"
                    or value.get("stable") is not True or not _count(value.get("unsupported_value_count"), 0)
                    or value.get("diagnostics") != [] or type(value.get("record_count")) is not int
                    or value["record_count"] != len(records)):
                raise RuntimeCaptureError(f"requested capture category is not complete and stable: {key}")
            samples = value.get("samples")
            if type(samples) is not list or len(samples) != 2 or any(
                type(row) is not dict
                or not _count(row.get("ordinal"), ordinal)
                or not _count(row.get("record_count"), len(records))
                or not _count(row.get("unsupported_value_count"), 0)
                or row != {"ordinal": ordinal, "record_count": len(records),
                        "records_sha256": value["records_sha256"], "unsupported_value_count": 0,
                        "diagnostics": []} for ordinal, row in enumerate(samples, 1)
            ):
                raise RuntimeCaptureError(f"capture repeated sample evidence differs: {key}")
            admitted[key] = value
        if not category_files.issubset(seen) or set(admitted) != requested:
            raise RuntimeCaptureError("capture category payload is missing")
        return RetainedRuntimeCapture(root, manifest, _sha(manifest_raw), admitted,
                                      record_hashes, field_hashes, inputs, consumed)
    except RuntimeCaptureError:
        raise
    except (OSError, TypeError, ValueError, KeyError, AttributeError, RecursionError) as exc:
        raise RuntimeCaptureError(f"cannot read retained runtime capture: {exc}") from exc
