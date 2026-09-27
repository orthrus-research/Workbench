"""Read-only composition of one selected release's retained client payload.

This joins independently reopened Core trees and a verified client ZIP. It does
not extract the ZIP, publish a tree, install a client, or qualify a runtime.
"""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping
from zipfile import BadZipFile, ZipFile
import zlib

from .durable_records import read_bounded_bytes, read_private_single_link_bytes
from .environment_package_install_plan import _SUPPORTED_FILESYSTEMS, _mount_type
from .pack_release import (
    MAX_ARCHIVE_ENTRIES, MAX_ARCHIVE_UNCOMPRESSED_BYTES, MAX_ASSET_BYTES, ReleaseAuthority,
    _input_plan, _verify_client_archive, load_authority,
)
from .pack_release_local import _canonical, _held_file, _object
from .pack_release_mod_augmentation import (
    _LOCK_LIMIT as MOD_LOCK_LIMIT, LOCK_FORMAT_V2, PLAN_FORMAT_V2 as MOD_PLAN_FORMAT_V2,
    reopen_mod_augmentation_v2,
)
from .pack_release_prism_resourcepacks import (
    load_resourcepack_policy, reopen_prism_resourcepacks,
)
from .output_routing import _WINDOWS_RESERVED
from .storage.exact_tree_inventory import MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES


POLICY_FORMAT = "workbench-supersymmetry-release-client-layout-policy-v1"
PLAN_FORMAT = "workbench-pack-release-client-layout-plan-v1"
_PLAN_PREFIX = "workbench-pack-release-client-layout-plan:sha256:"
_INPUT_ID = re.compile(r"workbench-pack-release-input-plan:sha256:[0-9a-f]{64}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_POLICY_LIMIT = 16 * 1024
_CHUNK = 1024 * 1024


def load_client_layout_policy(path: Path) -> dict[str, Any]:
    """Load the pack-owned release layout declaration, not local source paths."""

    policy = _object(read_bounded_bytes(path, byte_limit=_POLICY_LIMIT), "client layout policy")
    keys = {"format", "schema_version", "profile", "input_plan_format", "input_plan_id",
            "release_id", "version", "asset_sha256", "manifest_sha256",
            "override_source_root", "destination_root", "override_file_count",
            "override_total_bytes", "optional_selected", "collision_policy",
            "installation_state", "runtime_qualification_state"}
    if (set(policy) != keys or policy["format"] != POLICY_FORMAT
            or policy["schema_version"] != 1 or policy["profile"] != "supersymmetry"
            or policy["input_plan_format"] != "workbench-pack-release-input-plan-v1"
            or type(policy["input_plan_id"]) is not str
            or _INPUT_ID.fullmatch(policy["input_plan_id"]) is None
            or type(policy["release_id"]) is not str
            or type(policy["version"]) is not str
            or re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", policy["version"]) is None
            or type(policy["asset_sha256"]) is not str
            or _SHA256.fullmatch(policy["asset_sha256"]) is None
            or type(policy["manifest_sha256"]) is not str
            or _SHA256.fullmatch(policy["manifest_sha256"]) is None
            or policy["override_source_root"] != "overrides/"
            or policy["destination_root"] != "minecraft-root"
            or type(policy["override_file_count"]) is not int
            or not 0 < policy["override_file_count"] <= MAX_ARCHIVE_ENTRIES
            or type(policy["override_total_bytes"]) is not int
            or not 0 < policy["override_total_bytes"] <= MAX_ARCHIVE_UNCOMPRESSED_BYTES
            or type(policy["optional_selected"]) is not list
            or policy["collision_policy"] != "reject"
            or policy["installation_state"] != "not-installed"
            or policy["runtime_qualification_state"] != "not-qualified"):
        raise ValueError("Supersymmetry client layout policy is incompatible")
    selected: set[tuple[int, int]] = set()
    for row in policy["optional_selected"]:
        if (type(row) is not dict or set(row) != {"project_id", "file_id"}
                or type(row["project_id"]) is not int or type(row["file_id"]) is not int
                or not 0 < row["project_id"] < 2**63
                or not 0 < row["file_id"] < 2**63):
            raise ValueError("client layout optional selection is invalid")
        key = row["project_id"], row["file_id"]
        if key in selected:
            raise ValueError("client layout repeats an optional selection")
        selected.add(key)
    return policy


def _selected_policy(input_plan: Mapping[str, Any], policy: Mapping[str, Any]) -> set[tuple[int, int]]:
    if any(input_plan.get(key) != policy[key] for key in (
            "release_id", "version", "asset_sha256", "manifest_sha256")):
        raise ValueError("client layout policy belongs to another release")
    if (input_plan.get("format") != policy["input_plan_format"]
            or input_plan.get("plan_id") != policy["input_plan_id"]):
        raise ValueError("client layout policy belongs to another input plan")
    declarations = input_plan.get("external_files")
    if type(declarations) is not list:
        raise ValueError("selected release has no external file declarations")
    optional = {(row["project_id"], row["file_id"]) for row in declarations
                if type(row) is dict and row.get("required") is False}
    selected = {(row["project_id"], row["file_id"]) for row in policy["optional_selected"]}
    if selected - optional:
        raise ValueError("client layout selects a file that is not optional")
    return selected


def _archive_cache_path(path: Path, state_root: Path, input_plan: Mapping[str, Any]) -> None:
    digest = input_plan.get("asset_sha256")
    if type(digest) is not str or _SHA256.fullmatch(digest) is None:
        raise ValueError("selected release has no exact client ZIP identity")
    expected = state_root / "artifacts" / "sha256" / digest.removeprefix("sha256:")
    if (not isinstance(path, Path) or not path.is_absolute()
            or not state_root.is_absolute() or path != expected
            or any(part in {".", ".."} for part in path.parts)
            or _mount_type(path.parent) not in _SUPPORTED_FILESYSTEMS):
        raise ValueError("client layout needs the verified Core artifact-cache ZIP")


def _relative(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or value.startswith("/") or "\\" in value or "\0" in value
            or path.as_posix() != value or any(part in {"", ".", ".."} for part in value.split("/"))
            or any(part.endswith((".", " "))
                   or part.split(".", 1)[0].rstrip(" ").upper() in _WINDOWS_RESERVED
                   or any(ord(character) < 32 or ord(character) == 127
                          or character in '<>:"|?*' for character in part)
                   for part in path.parts)):
        raise ValueError("client layout has an unsafe relative member path")
    return value


def _scan_overrides(archive: ZipFile, entries: list[Any]) -> tuple[list[dict[str, Any]], int, set[str]]:
    """Stream every override to EOF so ZIP CRC and bounded expansion are checked."""

    rows: list[dict[str, Any]] = []
    explicit_directories: set[str] = set()
    total = 0
    for entry in entries:
        name = entry.filename
        kind = stat.S_IFMT(entry.external_attr >> 16)
        if (entry.orig_filename != name or entry.flag_bits & 1
                or entry.external_attr & 0x400  # Windows reparse point.
                or entry.compress_type not in {0, 8}
                or kind not in ({0, stat.S_IFDIR} if entry.is_dir()
                                else {0, stat.S_IFREG})):
            raise ValueError("client ZIP has an unsupported override member")
        if name == "overrides/":
            if (not entry.is_dir() or entry.file_size or entry.compress_size or entry.CRC):
                raise ValueError("client ZIP override root is not an empty directory")
            continue
        if not name.startswith("overrides/"):
            continue
        relative = _relative(name.removeprefix("overrides/").rstrip("/"))
        if entry.is_dir():
            if entry.file_size or entry.compress_size or entry.CRC:
                raise ValueError("client ZIP has a nonempty override directory entry")
            explicit_directories.add(relative)
            continue
        if entry.file_size > MAX_FILE_BYTES:
            raise ValueError("client ZIP override file exceeds Core's exact-tree bound")
        total += entry.file_size
        if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError("client ZIP overrides exceed the expansion bound")
        digest, observed = sha256(), 0
        with archive.open(entry) as source:
            while block := source.read(_CHUNK):
                observed += len(block)
                if observed > entry.file_size or observed > MAX_FILE_BYTES:
                    raise ValueError("client ZIP override expanded beyond its declared size")
                digest.update(block)
        if observed != entry.file_size:
            raise ValueError("client ZIP override differs from its declared size")
        rows.append({"relative_path": relative, "size": observed,
                     "sha256": "sha256:" + digest.hexdigest(), "source": "release-overrides"})
        if len(rows) > MAX_FILES:
            raise ValueError("client ZIP overrides exceed Core's exact-tree file bound")
    return rows, total, explicit_directories


def _destinations(mod_files: list[dict[str, Any]], resourcepack_files: list[dict[str, Any]],
                  overrides: list[dict[str, Any]], explicit_directories: set[str],
                  declared: Mapping[tuple[int, int], bool],
                  optional_selected: set[tuple[int, int]]) -> list[dict[str, Any]]:
    rows = list(overrides)
    seen_ids: set[tuple[int, int]] = set()
    for source, files, prefix in (("mod-core-tree", mod_files, "mods/"),
                                  ("resourcepack-core-tree", resourcepack_files, "resourcepacks/")):
        if type(files) is not list:
            raise ValueError("client layout source tree has no file rows")
        for row in files:
            if type(row) is not dict:
                raise ValueError("client layout source tree has an invalid file row")
            key = row.get("project_id"), row.get("file_id")
            if (type(key[0]) is not int or type(key[1]) is not int
                    or key not in declared or key in seen_ids
                    or (not declared[key] and key not in optional_selected)):
                raise ValueError("client layout repeats or misclassifies an external file ID")
            seen_ids.add(key)
            relative = row.get("relative_path")
            digest, size = row.get("sha256"), row.get("size")
            if (type(relative) is not str or not relative.startswith(prefix)
                    or _relative(relative) != relative
                    or type(size) is not int or not 0 < size <= MAX_FILE_BYTES
                    or type(digest) is not str or _SHA256.fullmatch(digest) is None):
                raise ValueError("client layout source tree has an unsafe destination")
            rows.append({"relative_path": relative, "size": size, "sha256": digest,
                         "source": source, "project_id": key[0], "file_id": key[1],
                         "required": declared[key]})
    needed = {key for key, required in declared.items() if required} | optional_selected
    if seen_ids != needed:
        raise ValueError("client layout does not cover exactly the selected external file IDs")
    if len(rows) > MAX_FILES or sum(row["size"] for row in rows) > MAX_TOTAL_BYTES:
        raise ValueError("client layout exceeds Core's exact-tree bounds")

    # Reject conflicts before any future extraction, including aliases that
    # differ only in case and a file where another member needs a directory.
    files_by_fold: dict[str, str] = {}
    spelled: dict[str, str] = {}
    for row in rows:
        path = row["relative_path"]
        folded = path.casefold()
        if folded in files_by_fold:
            raise ValueError("client layout files collide at a destination")
        files_by_fold[folded] = path
        pieces = path.split("/")
        for index in range(1, len(pieces) + 1):
            part = "/".join(pieces[:index])
            prior = spelled.setdefault(part.casefold(), part)
            if prior != part:
                raise ValueError("client layout paths differ only by case")
    for directory in explicit_directories:
        _relative(directory)
        if directory.casefold() in files_by_fold:
            raise ValueError("client layout directory collides with a file")
        pieces = directory.split("/")
        for index in range(1, len(pieces) + 1):
            part = "/".join(pieces[:index])
            prior = spelled.setdefault(part.casefold(), part)
            if prior != part:
                raise ValueError("client layout directories differ only by case")
    for path in files_by_fold.values():
        pieces = path.split("/")
        if any("/".join(pieces[:index]).casefold() in files_by_fold
               for index in range(1, len(pieces))):
            raise ValueError("client layout file blocks another destination")
    return sorted(rows, key=lambda row: row["relative_path"])


def _scan_selected_archive(path: Path, input_plan: Mapping[str, Any],
                           authority: ReleaseAuthority) -> tuple[list[dict[str, Any]], int, set[str]]:
    size = input_plan.get("asset_size")
    if type(size) is not int or not 0 < size <= MAX_ASSET_BYTES:
        raise ValueError("selected client ZIP has an invalid byte size")
    with _held_file(path, expected_size=size) as (descriptor, _):
        with os.fdopen(os.dup(descriptor), "rb") as source:
            digest, observed = sha256(), 0
            while block := source.read(_CHUNK):
                observed += len(block)
                if observed > size:
                    raise ValueError("selected client ZIP grew during review")
                digest.update(block)
            if (observed != size or "sha256:" + digest.hexdigest() != input_plan["asset_sha256"]):
                raise ValueError("selected client ZIP differs from its exact release identity")
            source.seek(0)
            manifest, raw, entries = _verify_client_archive(source, authority, input_plan["version"])
            selected = {key: input_plan[key] for key in ("release_id", "version", "asset_sha256", "asset_size")}
            if _input_plan(selected, manifest, raw, entries) != input_plan:
                raise ValueError("client ZIP manifest differs from the selected input plan")
            source.seek(0)
            try:
                with ZipFile(source) as archive:
                    overrides, total, directories = _scan_overrides(archive, entries)
            except (BadZipFile, OSError, RuntimeError, EOFError, zlib.error) as exc:
                raise ValueError("client ZIP override stream failed CRC or decompression") from exc
            return overrides, total, directories


def plan_release_client_layout(
    input_plan: Mapping[str, Any], *, archive_path: Path, archive_state_root: Path,
    authority_path: Path, layout_policy_path: Path, resourcepack_policy_path: Path,
    local_input_policy_path: Path, prior_mod_policy_path: Path, mod_policy_path: Path,
    mod_plan_id: str, mod_state_root: Path, mod_config_home: Path,
    resourcepack_plan_id: str, resourcepack_state_root: Path,
    resourcepack_config_home: Path,
) -> dict[str, Any]:
    """Review exact release inputs and return a path-free, non-installing plan."""

    policy = load_client_layout_policy(layout_policy_path)
    selected_optional = _selected_policy(input_plan, policy)
    authority = load_authority(authority_path)
    _archive_cache_path(archive_path, archive_state_root, input_plan)

    mod = reopen_mod_augmentation_v2(
        input_plan, expected_plan_id=mod_plan_id,
        policy_path=local_input_policy_path,
        prior_augmentation_policy_path=prior_mod_policy_path,
        augmentation_policy_path=mod_policy_path,
        state_root=mod_state_root, config_home=mod_config_home,
    )
    resourcepacks = reopen_prism_resourcepacks(
        input_plan, expected_plan_id=resourcepack_plan_id,
        policy_path=resourcepack_policy_path,
        state_root=resourcepack_state_root, config_home=resourcepack_config_home,
    )
    if (mod.get("outcome") != "reopened" or mod.get("installation_state") != "not-installed"
            or resourcepacks.get("outcome") != "reopened"
            or resourcepacks.get("installation_state") != "not-installed"):
        raise ValueError("client layout needs independently reopened Core input trees")

    # The V2 reopener validates this exact sealed lock and every staged member.
    # Read it again to obtain the ID-to-destination mapping absent from its
    # compact result; execution must reopen all inputs again after review.
    mod_lock_path = (mod_state_root / "pack-release-mod-augmentations"
                     / mod_plan_id.rsplit(":", 1)[-1] / "snapshot" / "source-lock.json")
    mod_lock_raw = read_private_single_link_bytes(mod_lock_path, byte_limit=MOD_LOCK_LIMIT)
    mod_lock = _object(mod_lock_raw, "reopened mod source lock")
    mod_plan_body = {**mod_lock, "format": MOD_PLAN_FORMAT_V2}
    mod_plan_body.pop("plan_id", None)
    if (mod_lock.get("format") != LOCK_FORMAT_V2 or mod_lock.get("plan_id") != mod_plan_id
            or mod_lock.get("input_plan_id") != input_plan["plan_id"]
            or mod_lock.get("retained_file_count") != mod["retained_file_count"]
            or mod_lock.get("retained_total_bytes") != mod["retained_total_bytes"]
            or mod_lock.get("unresolved") != mod["unresolved"]
            or mod_lock_raw != _canonical(mod_lock) + b"\n"
            or "workbench-pack-release-mod-augmentation-plan:sha256:"
            + sha256(_canonical(mod_plan_body)).hexdigest() != mod_plan_id):
        raise ValueError("reopened mod mapping changed after Core validation")
    resourcepack_policy = load_resourcepack_policy(resourcepack_policy_path)
    resourcepack_policy_id = ("workbench-pack-release-resourcepack-policy:sha256:"
                              + sha256(_canonical(resourcepack_policy)).hexdigest())
    overrides, override_bytes, directories = _scan_selected_archive(
        archive_path, input_plan, authority,
    )
    if (len(overrides) != policy["override_file_count"]
            or override_bytes != policy["override_total_bytes"]):
        raise ValueError("client ZIP overrides differ from the pack-owned layout policy")
    declarations = input_plan["external_files"]
    declared = {(row["project_id"], row["file_id"]): row["required"] for row in declarations}
    if len(declared) != len(declarations):
        raise ValueError("client layout manifest repeats an external file ID")
    files = _destinations(mod_lock["files"], resourcepacks["files"], overrides,
                          directories, declared, selected_optional)
    source_context = ("single-core-catalog" if (
        mod_state_root == resourcepack_state_root
        and mod_config_home == resourcepack_config_home
    ) else "split-core-catalogs")
    blockers = []
    if source_context != "single-core-catalog":
        blockers.append("cross-catalog-publication-unavailable")
    if (mod.get("curseforge_file_identity_state") != "independently-verified"
            or resourcepacks.get("curseforge_file_identity_state") != "independently-verified"):
        blockers.append("external-file-origin-unverified")
    if policy["runtime_qualification_state"] != "qualified":
        blockers.append("runtime-compatibility-unqualified")
    body = {
        "format": PLAN_FORMAT, "schema_version": 1, "profile": "supersymmetry",
        "state": "blocked" if blockers else "reviewed",
        "input_plan_id": input_plan["plan_id"], "release_id": input_plan["release_id"],
        "version": input_plan["version"], "asset_sha256": input_plan["asset_sha256"],
        "asset_size": input_plan["asset_size"],
        "manifest_sha256": input_plan["manifest_sha256"],
        "archive_member_count": input_plan["archive_member_count"],
        "other_archive_file_count": input_plan["other_file_count"],
        "override_source_root": policy["override_source_root"],
        "destination_root": policy["destination_root"],
        "layout_policy_id": "workbench-pack-release-client-layout-policy:sha256:"
                            + sha256(_canonical(policy)).hexdigest(),
        "mod_policy_id": mod_lock["policy_id"],
        "resourcepack_policy_id": resourcepack_policy_id,
        "mod": {"plan_id": mod_plan_id, "tree_id": mod["tree_id"],
                "tree_content_sha256": mod["tree_content_sha256"],
                "file_count": mod["retained_file_count"]},
        "resourcepacks": {"plan_id": resourcepack_plan_id, "tree_id": resourcepacks["tree_id"],
                          "tree_content_sha256": resourcepacks["tree_content_sha256"],
                          "file_count": resourcepacks["retained_file_count"]},
        "source_catalog_state": source_context,
        "optional_selected": sorted(policy["optional_selected"],
                                    key=lambda row: (row["project_id"], row["file_id"])),
        "override_file_count": len(overrides), "override_total_bytes": override_bytes,
        "override_content_sha256": "sha256:" + sha256(_canonical(overrides)).hexdigest(),
        "file_count": len(files), "total_bytes": sum(row["size"] for row in files),
        "files": files, "blockers": blockers,
        "installation_state": "not-installed",
        "runtime_qualification_state": policy["runtime_qualification_state"],
    }
    return {**body, "plan_id": _PLAN_PREFIX + sha256(_canonical(body)).hexdigest()}


__all__ = ["POLICY_FORMAT", "PLAN_FORMAT", "load_client_layout_policy",
           "plan_release_client_layout"]
