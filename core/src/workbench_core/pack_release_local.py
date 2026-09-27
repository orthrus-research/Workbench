"""Read-only review of user-supplied files for a selected client archive.

CurseForge IDs come from the publisher's manifest. Matching a local file to an
ID is a user assertion; hashing that file does not establish its provenance.
No bytes are acquired, copied, installed, or fetched from CurseForge here.
"""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import unicodedata
from typing import Any, Iterator, Mapping
from zipfile import BadZipFile, ZipFile

from workbench_api.durable_resources import DurableResourceError
from workbench_api.host_filesystem import DurableRecordError

from .durable_files import _directory as pinned_directory, _visible_parent
from .durable_records import read_bounded_bytes, read_private_single_link_bytes
from .environment_package_install_plan import _SUPPORTED_FILESYSTEMS, _mount_type


POLICY_FORMAT = "workbench-supersymmetry-release-local-input-policy-v1"
SOURCES_FORMAT = "workbench-pack-release-local-sources-v1"
PLAN_FORMAT = "workbench-pack-release-local-input-plan-v1"
MAX_POLICY_BYTES = 16 * 1024
MAX_SOURCES_BYTES = 2 * 1024 * 1024
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RESERVED_STEMS = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)),
                   *(f"LPT{n}" for n in range(1, 10))}
_STAT_FIELDS = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"{label} is not strict UTF-8 JSON: {exc}") from exc
    if type(value) is not dict:
        raise ValueError(f"{label} must be an object")
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def load_local_input_policy(path: Path) -> dict[str, Any]:
    """Read the pack owner's bounded policy resource."""

    value = _object(read_bounded_bytes(path, byte_limit=MAX_POLICY_BYTES), "local input policy")
    expected = {"format", "schema_version", "profile", "input_plan_format", "source_kind",
                "destination_root", "allowed_extensions", "max_file_bytes",
                "max_total_bytes", "optional_selection"}
    if (set(value) != expected or value["format"] != POLICY_FORMAT
            or value["schema_version"] != 1 or value["profile"] != "supersymmetry"
            or value["input_plan_format"] != "workbench-pack-release-input-plan-v1"
            or value["source_kind"] != "user-supplied-local-files"
            or value["destination_root"] != "mods"
            or value["allowed_extensions"] != [".jar", ".zip"]
            or value["optional_selection"] != "explicit"
            or type(value["max_file_bytes"]) is not int
            or not 0 < value["max_file_bytes"] <= 536870912
            or type(value["max_total_bytes"]) is not int
            or not 0 < value["max_total_bytes"] <= 8589934592
            or value["max_total_bytes"] < value["max_file_bytes"]):
        raise ValueError("Supersymmetry local input policy is incompatible")
    return value


def _absolute_path(value: Any) -> Path:
    if (type(value) is not str or not value.startswith("/") or "\x00" in value
            or "\\" in value or any(part in {"", ".", ".."} for part in value[1:].split("/"))):
        raise ValueError("local input path must be an absolute ordinary Linux path")
    return Path(value)


def _filename(value: Any, extensions: list[str]) -> str:
    if type(value) is not str:
        raise ValueError("local input filename is unsafe for the profile's mods destination")
    try:
        length = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("local input filename is not UTF-8") from exc
    if (not value or length > 255
            or value in {".", ".."} or value != unicodedata.normalize("NFC", value)
            or any(character in value for character in ("/", "\\", ":"))
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
            or value.endswith((" ", ".")) or value.split(".")[0].upper() in _RESERVED_STEMS
            or not any(value.lower().endswith(extension) for extension in extensions)):
        raise ValueError("local input filename is unsafe for the profile's mods destination")
    return value


def _key(row: Any, label: str) -> tuple[int, int]:
    if type(row) is not dict or type(row.get("project_id")) is not int or type(row.get("file_id")) is not int:
        raise ValueError(f"{label} must name a project and file ID")
    project_id, file_id = row["project_id"], row["file_id"]
    if not 0 < project_id < 2**63 or not 0 < file_id < 2**63:
        raise ValueError(f"{label} has invalid project or file IDs")
    return project_id, file_id


def _same_stat(left: os.stat_result, right: os.stat_result) -> bool:
    return all(getattr(left, field) == getattr(right, field) for field in _STAT_FIELDS)


@contextmanager
def _held_file(path: Path, *, expected_size: int) -> Iterator[tuple[int, os.stat_result]]:
    """Hold an ordinary source and its parent; refuse path or byte identity drift."""

    if os.name != "posix" or _mount_type(path.parent) not in _SUPPORTED_FILESYSTEMS:
        raise ValueError("local input needs a qualified Linux/WSL filesystem")
    parent = pinned_directory(path.parent, create=False)
    descriptor = -1
    try:
        parent_identity = os.fstat(parent)
        visible = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(visible.st_mode) or visible.st_nlink != 1 or visible.st_size != expected_size:
            raise ValueError("local input is not a single-link ordinary file of the declared size")
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW
                             | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0),
                             dir_fd=parent)
        opened = os.fstat(descriptor)
        if not _same_stat(visible, opened):
            raise ValueError("local input changed before reading")
        yield descriptor, opened
        after = os.fstat(descriptor)
        final = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if not _same_stat(opened, after) or not _same_stat(after, final):
            raise ValueError("local input changed during reading")
        _visible_parent(path.parent, (parent_identity.st_dev, parent_identity.st_ino))
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def _hash_held(path: Path, *, size: int, digest: str) -> tuple[int, int]:
    with _held_file(path, expected_size=size) as (descriptor, opened):
        observed = sha256()
        count = 0
        while block := os.read(descriptor, 1024 * 1024):
            count += len(block)
            if count > size:
                raise ValueError("local input exceeds its declared size")
            observed.update(block)
        if count != size or "sha256:" + observed.hexdigest() != digest:
            raise ValueError("local input bytes do not match the declared SHA-256 and size")
        return opened.st_dev, opened.st_ino


def _override_destination_names(archive_path: Path, *, size: int, digest: str,
                                destination_root: str) -> set[str]:
    if destination_root not in {"mods", "resourcepacks"}:
        raise ValueError("unsupported release input destination")
    prefix = f"overrides/{destination_root}/"
    with _held_file(archive_path, expected_size=size) as (descriptor, _):
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            try:
                with ZipFile(stream) as archive:
                    names = {name.removeprefix(prefix).split("/", 1)[0].casefold()
                             for name in archive.namelist()
                             if name.startswith(prefix)
                             and name.removeprefix(prefix)}
            except BadZipFile as exc:
                raise ValueError("selected client archive changed during local review") from exc
        os.lseek(descriptor, 0, os.SEEK_SET)
        observed = sha256()
        count = 0
        while block := os.read(descriptor, 1024 * 1024):
            count += len(block)
            if count > size:
                raise ValueError("selected client archive grew during local review")
            observed.update(block)
        if count != size or "sha256:" + observed.hexdigest() != digest:
            raise ValueError("selected client archive changed during local review")
        return names


def _override_mod_names(archive_path: Path, *, size: int, digest: str) -> set[str]:
    return _override_destination_names(
        archive_path, size=size, digest=digest, destination_root="mods",
    )


def review_local_inputs(
    input_plan: Mapping[str, Any], *, source_path: Path, policy_path: Path,
    archive_path: Path,
) -> dict[str, Any]:
    """Seal path-free local-byte observations against the selected release plan."""

    policy = load_local_input_policy(policy_path)
    raw_sources = read_private_single_link_bytes(source_path, byte_limit=MAX_SOURCES_BYTES)
    supplied = _object(raw_sources, "local sources document")
    if (input_plan.get("format") != policy["input_plan_format"]
            or supplied.get("format") != SOURCES_FORMAT
            or supplied.get("schema_version") != 1
            or supplied.get("input_plan_id") != input_plan.get("plan_id")
            or set(supplied) != {"format", "schema_version", "input_plan_id",
                                 "optional_selected", "sources"}
            or type(supplied["sources"]) is not list
            or type(supplied["optional_selected"]) is not list):
        raise ValueError("local sources do not match the selected release input plan")
    declarations = input_plan.get("external_files")
    if type(declarations) is not list or len(supplied["sources"]) > len(declarations):
        raise ValueError("local sources exceed the published external declarations")
    declared: dict[tuple[int, int], bool] = {}
    for row in declarations:
        key = _key(row, "published external declaration")
        if key in declared or type(row.get("required")) is not bool:
            raise ValueError("published external declarations are invalid")
        declared[key] = row["required"]
    selected: set[tuple[int, int]] = set()
    for row in supplied["optional_selected"]:
        if type(row) is not dict or set(row) != {"project_id", "file_id"}:
            raise ValueError("optional selection row is invalid")
        key = _key(row, "optional selection")
        if key in selected or key not in declared or declared[key]:
            raise ValueError("optional selection is duplicated or not optional")
        selected.add(key)
    sources: dict[tuple[int, int], dict[str, Any]] = {}
    filenames: set[str] = set()
    total = 0
    for row in supplied["sources"]:
        if type(row) is not dict or set(row) != {"project_id", "file_id", "local_path",
                                                 "filename", "size", "sha256"}:
            raise ValueError("local source row is invalid")
        key = _key(row, "local source")
        if key not in declared or key in sources or (not declared[key] and key not in selected):
            raise ValueError("local source is undeclared, repeated or not selected")
        path = _absolute_path(row["local_path"])
        filename = _filename(row["filename"], policy["allowed_extensions"])
        if filename != path.name:
            raise ValueError("local source filename does not match its path")
        folded = filename.casefold()
        if folded in filenames:
            raise ValueError("local sources collide at the mods destination")
        filenames.add(folded)
        size, digest = row["size"], row["sha256"]
        if (type(size) is not int or not 0 < size <= policy["max_file_bytes"]
                or type(digest) is not str or _SHA256.fullmatch(digest) is None):
            raise ValueError("local source size or SHA-256 is invalid")
        total += size
        if total > policy["max_total_bytes"]:
            raise ValueError("local sources exceed the profile byte limit")
        sources[key] = row
    override_names = _override_mod_names(
        archive_path, size=input_plan["asset_size"], digest=input_plan["asset_sha256"],
    )
    if filenames & override_names:
        raise ValueError("local source collides with a client archive mods override")
    identities: set[tuple[int, int]] = set()
    rows: list[dict[str, Any]] = []
    required_unresolved = optional_selected_unresolved = optional_unselected = verified = 0
    for key, required in sorted(declared.items()):
        source = sources.get(key)
        if source is not None:
            identity = _hash_held(_absolute_path(source["local_path"]),
                                  size=source["size"], digest=source["sha256"])
            if identity in identities:
                raise ValueError("one local file is assigned to multiple external IDs")
            identities.add(identity)
            verified += 1
            state = "local-bytes-verified"
        elif required:
            required_unresolved += 1
            state = "required-unresolved"
        elif key in selected:
            optional_selected_unresolved += 1
            state = "optional-selected-unresolved"
        else:
            optional_unselected += 1
            state = "optional-unselected"
        rows.append({
            "project_id": key[0], "file_id": key[1], "required": required,
            "selection": "required" if required else "selected" if key in selected else "unselected",
            "local_byte_state": state,
            "filename": source["filename"] if source else None,
            "size": source["size"] if source else None,
            "sha256": source["sha256"] if source else None,
        })
    if read_private_single_link_bytes(source_path, byte_limit=MAX_SOURCES_BYTES) != raw_sources:
        raise ValueError("local sources document changed during review")
    body = {
        "format": PLAN_FORMAT, "schema_version": 1,
        "profile": "supersymmetry", "source_kind": policy["source_kind"],
        "input_plan_id": input_plan["plan_id"],
        "release_id": input_plan["release_id"],
        "asset_sha256": input_plan["asset_sha256"],
        "policy_id": "workbench-pack-release-local-input-policy:sha256:"
                     + sha256(_canonical(policy)).hexdigest(),
        "destination_root": policy["destination_root"],
        "files": rows, "local_bytes_verified": verified,
        "required_unresolved": required_unresolved,
        "optional_selected_unresolved": optional_selected_unresolved,
        "optional_unselected": optional_unselected,
        "completeness_state": ("local-byte-set-reviewed" if not required_unresolved
                               and not optional_selected_unresolved else "local-bytes-unresolved"),
        "curseforge_file_identity_state": "unproven-by-local-hash",
        "acquisition_state": "not-acquired",
        "installation_state": "not-installed",
    }
    return {**body, "plan_id": "workbench-pack-release-local-input-plan:sha256:"
            + sha256(_canonical(body)).hexdigest()}


__all__ = ["load_local_input_policy", "review_local_inputs"]
