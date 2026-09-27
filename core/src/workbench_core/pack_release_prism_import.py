"""Core custody for reviewed local Prism/Packwiz mod bytes.

The selected release manifest supplies IDs. A local Packwiz sidecar supplies a
candidate ID-to-file assertion and SHA-1; neither establishes CurseForge
provenance. Core holds and rechecks source handles, copies into a private Linux
stage, and publishes an exact managed tree without installing the files.
"""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha1, sha256
import json
import os
from pathlib import Path
import re
import stat
import sys
import tomllib
from typing import Any, Iterator, Mapping

from workbench_api.managed_trees import ManagedTreeError, ManagedTreeReference

from .durable_files import _directory as pinned_directory, _visible_parent
from .durable_records import private_record_lock, read_private_single_link_bytes
from .environment_package_install_plan import _SUPPORTED_FILESYSTEMS, _mount_type
from .host_filesystem import private_path
from .managed_trees import CoreManagedTrees
from .output_routing import _private_directory
from .pack_release_local import _canonical, _filename, _object, _override_mod_names, load_local_input_policy
from .storage.exact_tree_inventory import EXACT_INVENTORY_POLICY


PLAN_FORMAT = "workbench-pack-release-prism-import-plan-v1"
LOCK_FORMAT = "workbench-pack-release-prism-source-lock-v1"
RESULT_FORMAT = "workbench-pack-release-prism-import-result-v1"
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_PLAN_ID = re.compile(r"workbench-pack-release-prism-import-plan:sha256:[0-9a-f]{64}\Z")
_MAX_SIDECARS = 4096
_MAX_SIDECAR_BYTES = 64 * 1024
_FIELDS = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")


def _same(left: os.stat_result, right: os.stat_result) -> bool:
    return all(getattr(left, field) == getattr(right, field) for field in _FIELDS)


def _source_filesystem(root: Path) -> str:
    if (not sys.platform.startswith("linux") or not root.is_absolute()
            or any(part in {".", ".."} for part in root.parts)):
        raise ValueError("Prism source needs an absolute Linux directory")
    kind = _mount_type(root)
    if kind in _SUPPORTED_FILESYSTEMS:
        return "qualified-linux-local"
    if kind == "9p" and os.statvfs(root).f_flag & os.ST_RDONLY:
        return "unqualified-readonly-wsl-9p"
    raise ValueError("Prism source needs a qualified Linux filesystem or read-only WSL 9p mount")


@contextmanager
def _source_directory(root: Path) -> Iterator[tuple[int, int]]:
    _source_filesystem(root)
    descriptor = pinned_directory(root, create=False)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("Prism source directory changed type")
        yield descriptor, info.st_dev
        _visible_parent(root, (info.st_dev, info.st_ino))
        if not _same(info, os.fstat(descriptor)):
            raise ValueError("Prism source directory changed during review")
    finally:
        os.close(descriptor)


def _read_sidecar(parent_fd: int, name: str) -> tuple[dict[str, Any], str]:
    visible = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if (not stat.S_ISREG(visible.st_mode) or visible.st_nlink != 1
            or not 0 < visible.st_size <= _MAX_SIDECAR_BYTES):
        raise ValueError("Packwiz sidecar is not one bounded ordinary file")
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=parent_fd)
    try:
        opened = os.fstat(descriptor)
        if not _same(visible, opened):
            raise ValueError("Packwiz sidecar changed before read")
        raw = bytearray()
        while block := os.read(descriptor, min(65536, _MAX_SIDECAR_BYTES + 1 - len(raw))):
            raw.extend(block)
            if len(raw) > _MAX_SIDECAR_BYTES:
                raise ValueError("Packwiz sidecar exceeds its bound")
        if (len(raw) != opened.st_size or not _same(opened, os.fstat(descriptor))
                or not _same(opened, os.stat(name, dir_fd=parent_fd, follow_symlinks=False))):
            raise ValueError("Packwiz sidecar changed during read")
    finally:
        os.close(descriptor)
    try:
        value = tomllib.loads(raw.decode("utf-8", "strict"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("Packwiz sidecar is not valid UTF-8 TOML") from exc
    if type(value) is not dict:
        raise ValueError("Packwiz sidecar is not a TOML table")
    return value, sha256(raw).hexdigest()


def _sidecar_row(value: dict[str, Any], sidecar_sha256: str,
                 policy: Mapping[str, Any]) -> dict[str, Any]:
    try:
        update = value["update"]["curseforge"]
        download = value["download"]
        project_id, file_id = update["project-id"], update["file-id"]
        filename, expected_sha1 = value["filename"], download["hash"]
    except (KeyError, TypeError) as exc:
        raise ValueError("Packwiz sidecar lacks an exact CurseForge file assertion") from exc
    if (type(update) is not dict or type(download) is not dict
            or type(project_id) is not int or not 0 < project_id < 2**63
            or type(file_id) is not int or not 0 < file_id < 2**63
            or download.get("mode") != "metadata:curseforge"
            or download.get("hash-format") != "sha1"
            or type(expected_sha1) is not str or _SHA1.fullmatch(expected_sha1) is None):
        raise ValueError("Packwiz sidecar has unsupported CurseForge identity or hash")
    return {"project_id": project_id, "file_id": file_id,
            "filename": _filename(filename, policy["allowed_extensions"]),
            "sha1": expected_sha1, "sidecar_sha256": sidecar_sha256}


def _measure_file(parent_fd: int, name: str, *, limit: int,
                  destination_fd: int | None = None) -> tuple[int, str, str, tuple[int, int]]:
    visible = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if (not stat.S_ISREG(visible.st_mode) or visible.st_nlink != 1
            or not 0 < visible.st_size <= limit):
        raise ValueError("Prism mod is not one bounded ordinary file")
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=parent_fd)
    try:
        opened = os.fstat(descriptor)
        if not _same(visible, opened):
            raise ValueError("Prism mod changed before read")
        one, two, size = sha1(), sha256(), 0
        while block := os.read(descriptor, 1024 * 1024):
            size += len(block)
            if size > opened.st_size or size > limit:
                raise ValueError("Prism mod grew during read")
            one.update(block)
            two.update(block)
            if destination_fd is not None:
                remaining = memoryview(block)
                while remaining:
                    written = os.write(destination_fd, remaining)
                    if written <= 0:
                        raise ValueError("Prism mod copy made no progress")
                    remaining = remaining[written:]
        if (size != opened.st_size or not _same(opened, os.fstat(descriptor))
                or not _same(opened, os.stat(name, dir_fd=parent_fd, follow_symlinks=False))):
            raise ValueError("Prism mod changed during read")
        return size, one.hexdigest(), two.hexdigest(), (opened.st_dev, opened.st_ino)
    finally:
        os.close(descriptor)


def _rows(input_plan: Mapping[str, Any], source_root: Path, policy: Mapping[str, Any],
          optional_selected: tuple[tuple[int, int], ...]) -> list[dict[str, Any]]:
    declared = {(row["project_id"], row["file_id"]): row["required"]
                for row in input_plan["external_files"]}
    if (len(declared) != len(input_plan["external_files"])
            or len(set(optional_selected)) != len(optional_selected)
            or any(key not in declared or declared[key] for key in optional_selected)):
        raise ValueError("Prism import has duplicate or invalid release IDs")
    selected = set(optional_selected)
    index = source_root / ".index" if (source_root / ".index").exists() else source_root
    if index == source_root and (source_root / ".index").is_symlink():
        raise ValueError("Prism sidecar index is redirected")
    rows: list[dict[str, Any]] = []
    seen_ids: set[tuple[int, int]] = set()
    seen_names: set[str] = set()
    seen_inodes: set[tuple[int, int]] = set()
    total = 0
    with _source_directory(source_root) as (mods_fd, mods_dev), _source_directory(index) as (index_fd, index_dev):
        if mods_dev != index_dev:
            raise ValueError("Prism mods and sidecars cross a source filesystem boundary")
        with os.scandir(index_fd) as scanned:
            names = sorted(entry.name for entry in scanned if entry.name.endswith(".pw.toml"))
        if len(names) > _MAX_SIDECARS:
            raise ValueError("Prism sidecar inventory exceeds its bound")
        for name in names:
            value, sidecar_digest = _read_sidecar(index_fd, name)
            row = _sidecar_row(value, sidecar_digest, policy)
            key = row["project_id"], row["file_id"]
            if key in seen_ids:
                raise ValueError("Prism sidecars repeat a project/file ID")
            seen_ids.add(key)
            if key not in declared or not (declared[key] or key in selected):
                continue
            folded = row["filename"].casefold()
            if folded in seen_names:
                raise ValueError("Prism mod filenames collide")
            seen_names.add(folded)
            try:
                size, observed_sha1, observed_sha256, identity = _measure_file(
                    mods_fd, row["filename"], limit=policy["max_file_bytes"],
                )
            except FileNotFoundError:
                continue  # A sidecar alone never counts as available bytes.
            if observed_sha1 != row["sha1"]:
                raise ValueError("Prism mod differs from its sidecar SHA-1")
            if identity in seen_inodes:
                raise ValueError("one Prism file is assigned to multiple release IDs")
            seen_inodes.add(identity)
            total += size
            if total > policy["max_total_bytes"]:
                raise ValueError("Prism mod bytes exceed the pack policy")
            rows.append({**row, "size": size, "sha256": "sha256:" + observed_sha256})
    return sorted(rows, key=lambda row: (row["project_id"], row["file_id"]))


def review_prism_import(input_plan: Mapping[str, Any], *, source_root: Path,
                        archive_path: Path, policy_path: Path,
                        optional_selected: tuple[tuple[int, int], ...] = ()) -> dict[str, Any]:
    """Return a path-free candidate tied to current published manifest IDs."""

    policy = load_local_input_policy(policy_path)
    if (input_plan.get("format") != policy["input_plan_format"]
            or type(input_plan.get("external_files")) is not list
            or not isinstance(source_root, Path)):
        raise ValueError("Prism import lacks the selected release input plan")
    source_filesystem_state = _source_filesystem(source_root)
    rows = _rows(input_plan, source_root, policy, optional_selected)
    override_names = _override_mod_names(
        archive_path, size=input_plan["asset_size"], digest=input_plan["asset_sha256"],
    )
    if any(row["filename"].casefold() in override_names for row in rows):
        raise ValueError("Prism mod collides with a client archive override")
    present = {(row["project_id"], row["file_id"]) for row in rows}
    selected = set(optional_selected)
    unresolved = []
    optional_unselected = []
    for declaration in input_plan["external_files"]:
        key = declaration["project_id"], declaration["file_id"]
        if key in present:
            continue
        if declaration["required"] or key in selected:
            unresolved.append({"project_id": key[0], "file_id": key[1],
                               "required": declaration["required"]})
        else:
            optional_unselected.append({"project_id": key[0], "file_id": key[1]})
    body = {"format": PLAN_FORMAT, "schema_version": 1,
            "profile": "supersymmetry", "input_plan_id": input_plan["plan_id"],
            "release_id": input_plan["release_id"],
            "asset_sha256": input_plan["asset_sha256"],
            "policy_id": "workbench-pack-release-local-input-policy:sha256:"
                         + sha256(_canonical(policy)).hexdigest(),
            "source_kind": "local-prism-packwiz-sidecars",
            "source_filesystem_state": source_filesystem_state,
            "files": rows, "unresolved": unresolved,
            "optional_unselected": optional_unselected,
            "retained_file_count": len(rows), "retained_total_bytes": sum(row["size"] for row in rows),
            "curseforge_file_identity_state": "unproven-by-local-sidecar",
            "installation_state": "not-installed"}
    return {**body, "plan_id": "workbench-pack-release-prism-import-plan:sha256:"
            + sha256(_canonical(body)).hexdigest()}


def _host(state_root: Path, config_home: Path, policy_id: str) -> tuple[CoreManagedTrees, Path]:
    if (_mount_type(state_root) not in _SUPPORTED_FILESYSTEMS
            or not state_root.is_absolute() or not config_home.is_absolute()):
        raise ValueError("Prism import destination needs a qualified Linux filesystem")
    root = state_root / "pack-release-mod-inputs"
    return CoreManagedTrees(
        workspace=state_root, configuration_home=config_home,
        locations={"artifacts": root}, owner_id="supersymmetry",
        policy_id=policy_id, location_sources={"artifacts": "pack-release-local-import"},
    ), root


def _target(root: Path, plan: Mapping[str, Any]) -> Path:
    return root / plan["plan_id"].rsplit(":", 1)[-1] / "snapshot"


def _tree_state(host: CoreManagedTrees, target: Path, plan: Mapping[str, Any]) -> tuple[str, str | None]:
    rows = [row for row in host.catalog.trees.inventory() if row["path"] == str(target)]
    if len(rows) > 1:
        raise ValueError("Prism import target has ambiguous Core reservations")
    if not rows:
        if target.exists() or target.is_symlink():
            raise ValueError("Prism import target exists outside Core custody")
        return "acquire", None
    row = rows[0]
    if row["owner_id"] != host.owner_id or row["workspace"] != str(host.workspace):
        raise ValueError("Prism import target belongs to another owner")
    if row["status"] != "committed":
        raise ValueError("Prism import has an incomplete stage requiring review")
    reference = host.describe(str(row["tree_id"]))
    if (reference.domain_id != plan["plan_id"] or reference.path != target
            or reference.policy_id != plan["policy_id"]):
        raise ValueError("Prism import retained another candidate")
    return "reuse", reference.tree_id


def plan_prism_import(input_plan: Mapping[str, Any], *, source_root: Path,
                      archive_path: Path, policy_path: Path, state_root: Path,
                      config_home: Path,
                      optional_selected: tuple[tuple[int, int], ...] = ()) -> dict[str, Any]:
    candidate = review_prism_import(
        input_plan, source_root=source_root, archive_path=archive_path,
        policy_path=policy_path, optional_selected=optional_selected,
    )
    host, root = _host(state_root, config_home, candidate["policy_id"])
    action, tree_id = _tree_state(host, _target(root, candidate), candidate)
    return {**candidate, "action": action, "tree_id": tree_id}


def _copy_to_stage(stage: Path, source_root: Path, plan: Mapping[str, Any]) -> None:
    stage.mkdir(mode=0o700)
    index = source_root / ".index" if (source_root / ".index").exists() else source_root
    with _source_directory(source_root) as (mods_fd, mods_dev), _source_directory(index) as (index_fd, index_dev):
        if mods_dev != index_dev:
            raise ValueError("Prism mods and sidecars cross a source filesystem boundary")
        sidecars: dict[tuple[int, int], tuple[str, dict[str, Any], str]] = {}
        with os.scandir(index_fd) as scanned:
            names = sorted(entry.name for entry in scanned if entry.name.endswith(".pw.toml"))
        if len(names) > _MAX_SIDECARS:
            raise ValueError("Prism sidecar inventory exceeds its bound")
        for name in names:
            value, digest = _read_sidecar(index_fd, name)
            update = value.get("update")
            ref = update.get("curseforge") if type(update) is dict else None
            if not isinstance(ref, dict):
                continue
            key = ref.get("project-id"), ref.get("file-id")
            if type(key[0]) is int and type(key[1]) is int:
                if key in sidecars:
                    raise ValueError("Prism sidecars repeat a project/file ID")
                sidecars[key] = name, value, digest
        for row in plan["files"]:
            key = row["project_id"], row["file_id"]
            if key not in sidecars:
                raise ValueError("Prism sidecar disappeared before import")
            sidecar_name, value, digest = sidecars[key]
            download = value.get("download")
            if (digest != row["sidecar_sha256"] or value.get("filename") != row["filename"]
                    or type(download) is not dict or download.get("hash") != row["sha1"]):
                raise ValueError("Prism sidecar changed before import")
            destination = stage / row["filename"]
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                size, one, two, _ = _measure_file(
                    mods_fd, row["filename"], limit=row["size"], destination_fd=descriptor,
                )
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            if (size != row["size"] or one != row["sha1"]
                    or "sha256:" + two != row["sha256"]):
                raise ValueError("Prism mod changed before retained import")
            _, after_digest = _read_sidecar(index_fd, sidecar_name)
            if after_digest != digest:
                raise ValueError("Prism sidecar changed during import")
    lock = {key: value for key, value in plan.items() if key not in {"action", "tree_id"}}
    lock["format"] = LOCK_FORMAT
    raw = _canonical(lock) + b"\n"
    descriptor = os.open(stage / "source-lock.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(descriptor)


def _validate_stage(stage: Path, plan: Mapping[str, Any]) -> None:
    names = {row["filename"] for row in plan["files"]}
    if {item.name for item in stage.iterdir()} != names | {"source-lock.json"}:
        raise ValueError("Prism import stage has unexpected members")
    lock = {key: value for key, value in plan.items() if key not in {"action", "tree_id"}}
    lock["format"] = LOCK_FORMAT
    if (stage / "source-lock.json").read_bytes() != _canonical(lock) + b"\n":
        raise ValueError("Prism import source lock changed")
    for row in plan["files"]:
        path = stage / row["filename"]
        visible = path.lstat()
        if not stat.S_ISREG(visible.st_mode) or visible.st_nlink != 1 or visible.st_size != row["size"]:
            raise ValueError("Prism import stage member changed type or size")
        digest = sha256()
        with path.open("rb") as source:
            while block := source.read(1024 * 1024):
                digest.update(block)
        if "sha256:" + digest.hexdigest() != row["sha256"]:
            raise ValueError("Prism import stage member changed bytes")


def _reopen(host: CoreManagedTrees, reference: ManagedTreeReference,
            target: Path, plan: Mapping[str, Any]) -> None:
    if (reference.path != target or reference.workspace != host.workspace
            or reference.owner_id != host.owner_id or reference.role != "artifacts"
            or reference.policy_id != plan["policy_id"]
            or reference.domain_id != plan["plan_id"]
            or reference.inventory_policy != EXACT_INVENTORY_POLICY
            or reference.derived_status != "current"):
        raise ValueError("Prism import tree reopened with another Core identity")
    _validate_stage(reference.path, plan)


def _retained_plan(input_plan: Mapping[str, Any], policy: Mapping[str, Any], raw: bytes,
                   expected_plan_id: str) -> dict[str, Any]:
    lock = _object(raw, "retained Prism source lock")
    if lock.get("format") != LOCK_FORMAT:
        raise ValueError("retained Prism source lock has another format")
    plan = {**lock, "format": PLAN_FORMAT}
    if (set(plan) != {"format", "schema_version", "profile", "input_plan_id", "release_id",
                      "asset_sha256", "policy_id", "source_kind", "source_filesystem_state", "files", "unresolved",
                      "optional_unselected", "retained_file_count", "retained_total_bytes",
                      "curseforge_file_identity_state", "installation_state", "plan_id"}
            or plan["schema_version"] != 1 or plan["profile"] != "supersymmetry"
            or plan["input_plan_id"] != input_plan.get("plan_id")
            or plan["release_id"] != input_plan.get("release_id")
            or plan["asset_sha256"] != input_plan.get("asset_sha256")
            or plan["source_kind"] != "local-prism-packwiz-sidecars"
            or plan["source_filesystem_state"] not in {"qualified-linux-local", "unqualified-readonly-wsl-9p"}
            or plan["curseforge_file_identity_state"] != "unproven-by-local-sidecar"
            or plan["installation_state"] != "not-installed"
            or plan["plan_id"] != expected_plan_id):
        raise ValueError("retained Prism source lock differs from selected release")
    policy_id = "workbench-pack-release-local-input-policy:sha256:" + sha256(_canonical(policy)).hexdigest()
    if plan["policy_id"] != policy_id:
        raise ValueError("retained Prism source lock has another pack policy")
    body = {key: value for key, value in plan.items() if key != "plan_id"}
    if "workbench-pack-release-prism-import-plan:sha256:" + sha256(_canonical(body)).hexdigest() != expected_plan_id:
        raise ValueError("retained Prism source lock identity changed")
    if (type(plan["files"]) is not list or type(plan["unresolved"]) is not list
            or type(plan["optional_unselected"]) is not list):
        raise ValueError("retained Prism source lock has invalid rows")
    declared = {(row["project_id"], row["file_id"]): row["required"]
                for row in input_plan["external_files"]}
    seen: set[tuple[int, int]] = set()
    names: set[str] = set()
    total = 0
    for row in plan["files"]:
        if (type(row) is not dict
                or set(row) != {"project_id", "file_id", "filename", "sha1",
                                "sidecar_sha256", "size", "sha256"}):
            raise ValueError("retained Prism source lock has an invalid file row")
        if type(row["project_id"]) is not int or type(row["file_id"]) is not int:
            raise ValueError("retained Prism source lock has an invalid file ID")
        key = row["project_id"], row["file_id"]
        if (key not in declared or key in seen
                or type(row["size"]) is not int
                or not 0 < row["size"] <= policy["max_file_bytes"]
                or type(row["sha1"]) is not str or _SHA1.fullmatch(row["sha1"]) is None
                or type(row["sha256"]) is not str or _SHA256.fullmatch(row["sha256"]) is None
                or type(row["sidecar_sha256"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", row["sidecar_sha256"]) is None):
            raise ValueError("retained Prism source lock has an invalid file identity")
        name = _filename(row["filename"], policy["allowed_extensions"])
        if name.casefold() in names:
            raise ValueError("retained Prism source lock has duplicate destinations")
        names.add(name.casefold())
        seen.add(key)
        total += row["size"]
    if (total > policy["max_total_bytes"] or plan["retained_file_count"] != len(seen)
            or plan["retained_total_bytes"] != total):
        raise ValueError("retained Prism source lock has invalid byte bounds")
    for optional in plan["optional_unselected"]:
        if type(optional) is not dict or set(optional) != {"project_id", "file_id"}:
            raise ValueError("retained Prism source lock has an invalid optional row")
        if type(optional["project_id"]) is not int or type(optional["file_id"]) is not int:
            raise ValueError("retained Prism source lock has an invalid optional ID")
        key = optional["project_id"], optional["file_id"]
        if key not in declared or declared[key] or key in seen:
            raise ValueError("retained Prism source lock has an invalid optional ID")
        seen.add(key)
    for unresolved in plan["unresolved"]:
        if type(unresolved) is not dict or set(unresolved) != {"project_id", "file_id", "required"}:
            raise ValueError("retained Prism source lock has an invalid unresolved row")
        if (type(unresolved["project_id"]) is not int or type(unresolved["file_id"]) is not int
                or type(unresolved["required"]) is not bool):
            raise ValueError("retained Prism source lock has an invalid unresolved ID")
        key = unresolved["project_id"], unresolved["file_id"]
        if (key not in declared or key in seen or unresolved["required"] != declared[key]):
            raise ValueError("retained Prism source lock has an invalid unresolved ID")
        seen.add(key)
    if seen != set(declared):
        raise ValueError("retained Prism source lock does not cover the selected manifest")
    return plan


def reopen_prism_import(input_plan: Mapping[str, Any], *, expected_plan_id: str,
                        policy_path: Path, state_root: Path, config_home: Path) -> dict[str, Any]:
    """Reopen exact ext4 bytes without the prior Prism source directory."""

    if type(expected_plan_id) is not str or _PLAN_ID.fullmatch(expected_plan_id) is None:
        raise ValueError("select an exact reviewed Prism import plan ID")
    policy = load_local_input_policy(policy_path)
    policy_id = "workbench-pack-release-local-input-policy:sha256:" + sha256(_canonical(policy)).hexdigest()
    host, root = _host(state_root, config_home, policy_id)
    target = root / expected_plan_id.rsplit(":", 1)[-1] / "snapshot"
    rows = [row for row in host.catalog.trees.inventory() if row["path"] == str(target)]
    if len(rows) != 1 or rows[0]["status"] != "committed":
        raise ValueError("Prism import lacks one completed Core tree")
    reference = host.describe(str(rows[0]["tree_id"]))
    if (reference.path != target or reference.owner_id != host.owner_id
            or reference.workspace != state_root or reference.policy_id != policy_id
            or reference.domain_id != expected_plan_id
            or reference.inventory_policy != EXACT_INVENTORY_POLICY
            or reference.derived_status != "current"):
        raise ValueError("Prism import tree changed before retained readback")
    raw = read_private_single_link_bytes(target / "source-lock.json", byte_limit=1024 * 1024)
    plan = _retained_plan(input_plan, policy, raw, expected_plan_id)
    _reopen(host, reference, target, plan)
    return {"format": RESULT_FORMAT, "schema_version": 1,
            "outcome": "reopened", "plan_id": plan["plan_id"],
            "tree_id": reference.tree_id, "tree_content_sha256": reference.content_sha256,
            "input_plan_id": plan["input_plan_id"], "retained_file_count": plan["retained_file_count"],
            "retained_total_bytes": plan["retained_total_bytes"],
            "unresolved": plan["unresolved"], "optional_unselected": plan["optional_unselected"],
            "source_filesystem_state": plan["source_filesystem_state"],
            "curseforge_file_identity_state": plan["curseforge_file_identity_state"],
            "installation_state": "not-installed"}


def apply_prism_import(input_plan: Mapping[str, Any], *, source_root: Path,
                       archive_path: Path, policy_path: Path, state_root: Path,
                       config_home: Path, expected_plan_id: str,
                       optional_selected: tuple[tuple[int, int], ...] = ()) -> dict[str, Any]:
    """Import a partial reviewed set into stable Core custody, never install."""

    plan = plan_prism_import(
        input_plan, source_root=source_root, archive_path=archive_path,
        policy_path=policy_path, state_root=state_root, config_home=config_home,
        optional_selected=optional_selected,
    )
    if plan["plan_id"] != expected_plan_id:
        raise ValueError("Prism import changed after review")
    host, root = _host(state_root, config_home, plan["policy_id"])
    target = _target(root, plan)
    _private_directory(target.parent)
    if not private_path(target.parent, directory=True):
        raise ValueError("Prism import target parent is not private")
    with private_record_lock(root / (".prism-import-" + expected_plan_id.rsplit(":", 1)[-1] + ".lock"), wait=True):
        current = plan_prism_import(
            input_plan, source_root=source_root, archive_path=archive_path,
            policy_path=policy_path, state_root=state_root, config_home=config_home,
            optional_selected=optional_selected,
        )
        if current != plan:
            raise ValueError("Prism import inputs changed before staging")
        if plan["action"] == "acquire":
            with host.stage("artifacts", target.name, requested_path=target) as stage:
                _copy_to_stage(stage.path, source_root, plan)
                reference = stage.publish(
                    validate=lambda path: _validate_stage(path, plan),
                    domain_id=plan["plan_id"], inventory_policy=EXACT_INVENTORY_POLICY,
                )
            outcome = "imported"
        else:
            reference = host.describe(plan["tree_id"])
            outcome = "reused"
        _reopen(host, reference, target, plan)
    return {"format": RESULT_FORMAT, "schema_version": 1,
            "outcome": outcome, "plan_id": plan["plan_id"],
            "tree_id": reference.tree_id, "tree_content_sha256": reference.content_sha256,
            "input_plan_id": plan["input_plan_id"], "retained_file_count": plan["retained_file_count"],
            "retained_total_bytes": plan["retained_total_bytes"],
            "unresolved": plan["unresolved"], "optional_unselected": plan["optional_unselected"],
            "source_filesystem_state": plan["source_filesystem_state"],
            "curseforge_file_identity_state": plan["curseforge_file_identity_state"],
            "installation_state": "not-installed"}


__all__ = ["PLAN_FORMAT", "LOCK_FORMAT", "RESULT_FORMAT", "review_prism_import",
           "plan_prism_import", "apply_prism_import", "reopen_prism_import"]
