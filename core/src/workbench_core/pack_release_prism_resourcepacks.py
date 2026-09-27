"""Retain selected Prism resource-pack bytes in a distinct Core tree.

The pack owner binds the three resource-pack IDs to an exact released input
plan. Packwiz sidecars assert local file IDs and SHA-1, but do not prove origin.
"""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping

from workbench_api.managed_trees import ManagedTreeReference

from .durable_records import private_record_lock, read_bounded_bytes, read_private_single_link_bytes
from .environment_package_install_plan import _SUPPORTED_FILESYSTEMS, _mount_type
from .host_filesystem import private_path
from .managed_trees import CoreManagedTrees
from .output_routing import _private_directory
from .pack_release_local import _canonical, _filename, _object, _override_destination_names
from .pack_release_prism_import import (
    _MAX_SIDECARS, _measure_file, _read_sidecar, _rows, _source_directory,
    _source_filesystem, _target, _tree_state,
)
from .storage.exact_tree_inventory import EXACT_INVENTORY_POLICY


POLICY_FORMAT = "workbench-supersymmetry-release-resourcepack-input-policy-v1"
PLAN_FORMAT = "workbench-pack-release-prism-resourcepack-plan-v1"
LOCK_FORMAT = "workbench-pack-release-prism-resourcepack-source-lock-v1"
RESULT_FORMAT = "workbench-pack-release-prism-resourcepack-result-v1"
_PLAN_PREFIX = "workbench-pack-release-prism-resourcepack-plan:sha256:"
_PLAN_ID = re.compile(r"workbench-pack-release-prism-resourcepack-plan:sha256:[0-9a-f]{64}\Z")
_INPUT_ID = re.compile(r"workbench-pack-release-input-plan:sha256:[0-9a-f]{64}\Z")
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_HEX256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_POLICY_BYTES = 16 * 1024
_MAX_LOCK_BYTES = 1024 * 1024


def load_resourcepack_policy(path: Path) -> dict[str, Any]:
    policy = _object(read_bounded_bytes(path, byte_limit=_MAX_POLICY_BYTES), "resource-pack policy")
    if (set(policy) != {"format", "schema_version", "profile", "input_plan_format",
                        "input_plan_id", "version", "manifest_sha256", "external_file_count",
                        "allowed_extensions", "max_file_bytes", "max_total_bytes", "placements"}
            or policy["format"] != POLICY_FORMAT or policy["schema_version"] != 1
            or policy["profile"] != "supersymmetry"
            or policy["input_plan_format"] != "workbench-pack-release-input-plan-v1"
            or type(policy["input_plan_id"]) is not str
            or _INPUT_ID.fullmatch(policy["input_plan_id"]) is None
            or type(policy["version"]) is not str
            or re.fullmatch(r"[0-9]+(\.[0-9]+){3}", policy["version"]) is None
            or type(policy["manifest_sha256"]) is not str
            or _SHA256.fullmatch(policy["manifest_sha256"]) is None
            or type(policy["external_file_count"]) is not int
            or not 1 <= policy["external_file_count"] <= 10000
            or policy["allowed_extensions"] != [".zip"]
            or type(policy["max_file_bytes"]) is not int
            or not 0 < policy["max_file_bytes"] <= 536870912
            or type(policy["max_total_bytes"]) is not int
            or not policy["max_file_bytes"] <= policy["max_total_bytes"] <= 1610612736
            or type(policy["placements"]) is not list
            or not 1 <= len(policy["placements"]) <= policy["external_file_count"]):
        raise ValueError("Supersymmetry resource-pack policy is incompatible")
    pairs: set[tuple[int, int]] = set()
    for row in policy["placements"]:
        if (type(row) is not dict
                or set(row) != {"project_id", "file_id", "required", "destination_root"}
                or type(row["project_id"]) is not int or not 0 < row["project_id"] < 2**63
                or type(row["file_id"]) is not int or not 0 < row["file_id"] < 2**63
                or row["required"] is not True or row["destination_root"] != "resourcepacks"):
            raise ValueError("resource-pack placement is invalid")
        key = row["project_id"], row["file_id"]
        if key in pairs:
            raise ValueError("resource-pack placement repeats an ID")
        pairs.add(key)
    return policy


def _selected_declarations(input_plan: Mapping[str, Any], policy: Mapping[str, Any]) -> list[dict[str, Any]]:
    declarations = input_plan.get("external_files")
    if (input_plan.get("format") != policy["input_plan_format"]
            or input_plan.get("plan_id") != policy["input_plan_id"]
            or input_plan.get("version") != policy["version"]
            or input_plan.get("manifest_sha256") != policy["manifest_sha256"]
            or type(declarations) is not list
            or len(declarations) != policy["external_file_count"]):
        raise ValueError("resource-pack placement belongs to another selected release")
    body = {key: value for key, value in input_plan.items() if key != "plan_id"}
    if "workbench-pack-release-input-plan:sha256:" + sha256(_canonical(body)).hexdigest() != input_plan["plan_id"]:
        raise ValueError("selected release input plan identity changed")
    declared: dict[tuple[int, int], bool] = {}
    for row in declarations:
        if (type(row) is not dict or set(row) != {"project_id", "file_id", "required"}
                or type(row["project_id"]) is not int or type(row["file_id"]) is not int
                or not 0 < row["project_id"] < 2**63 or not 0 < row["file_id"] < 2**63
                or type(row["required"]) is not bool):
            raise ValueError("selected release external declaration is invalid")
        key = row["project_id"], row["file_id"]
        if key in declared:
            raise ValueError("selected release repeats an external ID")
        declared[key] = row["required"]
    selected = []
    for placement in policy["placements"]:
        key = placement["project_id"], placement["file_id"]
        if declared.get(key) is not True:
            raise ValueError("resource-pack placement is absent or not required")
        selected.append({"project_id": key[0], "file_id": key[1], "required": True})
    return selected


def review_prism_resourcepacks(input_plan: Mapping[str, Any], *, source_root: Path,
                               archive_path: Path, policy_path: Path) -> dict[str, Any]:
    """Return a path-free plan for the exact pack-owned resource-pack IDs."""

    policy = load_resourcepack_policy(policy_path)
    selected = _selected_declarations(input_plan, policy)
    if not isinstance(source_root, Path) or source_root.name != "resourcepacks":
        raise ValueError("Prism resource-pack source must be the instance resourcepacks directory")
    source_state = _source_filesystem(source_root)
    rows = _rows({"external_files": selected}, source_root, policy, ())
    override_names = _override_destination_names(
        archive_path, size=input_plan["asset_size"], digest=input_plan["asset_sha256"],
        destination_root="resourcepacks",
    )
    if any(row["filename"].casefold() in override_names for row in rows):
        raise ValueError("Prism resource pack collides with a client archive override")
    files = [{**row, "destination_root": "resourcepacks",
              "relative_path": "resourcepacks/" + row["filename"]} for row in rows]
    present = {(row["project_id"], row["file_id"]) for row in files}
    unresolved = [{"project_id": row["project_id"], "file_id": row["file_id"], "required": True}
                  for row in selected if (row["project_id"], row["file_id"]) not in present]
    body = {
        "format": PLAN_FORMAT, "schema_version": 1, "profile": "supersymmetry",
        "input_plan_id": input_plan["plan_id"], "release_id": input_plan["release_id"],
        "asset_sha256": input_plan["asset_sha256"],
        "policy_id": "workbench-pack-release-resourcepack-policy:sha256:"
                     + sha256(_canonical(policy)).hexdigest(),
        "source_kind": "local-prism-packwiz-resourcepacks",
        "source_filesystem_state": source_state, "destination_root": "resourcepacks",
        "files": files, "unresolved": unresolved,
        "retained_file_count": len(files),
        "retained_total_bytes": sum(row["size"] for row in files),
        "curseforge_file_identity_state": "unproven-by-local-sidecar",
        "installation_state": "not-installed",
    }
    return {**body, "plan_id": _PLAN_PREFIX + sha256(_canonical(body)).hexdigest()}


def _host(state_root: Path, config_home: Path, policy_id: str) -> tuple[CoreManagedTrees, Path]:
    if (_mount_type(state_root) not in _SUPPORTED_FILESYSTEMS
            or not state_root.is_absolute() or not config_home.is_absolute()):
        raise ValueError("resource-pack import destination needs a qualified Linux filesystem")
    root = state_root / "pack-release-resourcepack-inputs"
    return CoreManagedTrees(
        workspace=state_root, configuration_home=config_home,
        locations={"artifacts": root}, owner_id="supersymmetry",
        policy_id=policy_id, location_sources={"artifacts": "pack-release-resourcepack-import"},
    ), root


def plan_prism_resourcepacks(input_plan: Mapping[str, Any], *, source_root: Path,
                             archive_path: Path, policy_path: Path, state_root: Path,
                             config_home: Path) -> dict[str, Any]:
    plan = review_prism_resourcepacks(
        input_plan, source_root=source_root, archive_path=archive_path, policy_path=policy_path,
    )
    host, root = _host(state_root, config_home, plan["policy_id"])
    action, tree_id = _tree_state(host, _target(root, plan), plan)
    return {**plan, "action": action, "tree_id": tree_id}


def _copy_to_stage(stage: Path, source_root: Path, plan: Mapping[str, Any]) -> None:
    stage.mkdir(mode=0o700)
    destination_root = stage / "resourcepacks"
    destination_root.mkdir(mode=0o700)
    index = source_root / ".index" if (source_root / ".index").exists() else source_root
    with _source_directory(source_root) as (source_fd, source_dev), _source_directory(index) as (index_fd, index_dev):
        if source_dev != index_dev:
            raise ValueError("Prism resource packs and sidecars cross a source filesystem boundary")
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
                raise ValueError("Prism resource-pack sidecar disappeared before import")
            sidecar_name, value, digest = sidecars[key]
            download = value.get("download")
            if (digest != row["sidecar_sha256"] or value.get("filename") != row["filename"]
                    or type(download) is not dict or download.get("hash") != row["sha1"]):
                raise ValueError("Prism resource-pack sidecar changed before import")
            descriptor = os.open(
                destination_root / row["filename"],
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
            )
            try:
                size, one, two, _ = _measure_file(
                    source_fd, row["filename"], limit=row["size"], destination_fd=descriptor,
                )
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            if (size != row["size"] or one != row["sha1"]
                    or "sha256:" + two != row["sha256"]):
                raise ValueError("Prism resource pack changed before retained import")
            _, after_digest = _read_sidecar(index_fd, sidecar_name)
            if after_digest != digest:
                raise ValueError("Prism resource-pack sidecar changed during import")
    lock = {key: value for key, value in plan.items() if key not in {"action", "tree_id"}}
    lock["format"] = LOCK_FORMAT
    descriptor = os.open(stage / "source-lock.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            output.write(_canonical(lock) + b"\n")
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(descriptor)


def _validate_stage(stage: Path, plan: Mapping[str, Any]) -> None:
    names = {row["filename"] for row in plan["files"]}
    if {item.name for item in stage.iterdir()} != {"resourcepacks", "source-lock.json"}:
        raise ValueError("resource-pack import stage has unexpected members")
    destination = stage / "resourcepacks"
    if not stat.S_ISDIR(destination.lstat().st_mode):
        raise ValueError("resource-pack import destination changed type")
    if {item.name for item in destination.iterdir()} != names:
        raise ValueError("resource-pack import destination has unexpected members")
    lock = {key: value for key, value in plan.items() if key not in {"action", "tree_id"}}
    lock["format"] = LOCK_FORMAT
    if (stage / "source-lock.json").read_bytes() != _canonical(lock) + b"\n":
        raise ValueError("resource-pack source lock changed")
    for row in plan["files"]:
        path = destination / row["filename"]
        visible = path.lstat()
        if not stat.S_ISREG(visible.st_mode) or visible.st_nlink != 1 or visible.st_size != row["size"]:
            raise ValueError("resource-pack stage member changed type or size")
        digest = sha256()
        with path.open("rb") as source:
            while block := source.read(1024 * 1024):
                digest.update(block)
        if "sha256:" + digest.hexdigest() != row["sha256"]:
            raise ValueError("resource-pack stage member changed bytes")


def _reopen(host: CoreManagedTrees, reference: ManagedTreeReference,
            target: Path, plan: Mapping[str, Any]) -> None:
    if (reference.path != target or reference.workspace != host.workspace
            or reference.owner_id != host.owner_id or reference.role != "artifacts"
            or reference.policy_id != plan["policy_id"]
            or reference.domain_id != plan["plan_id"]
            or reference.inventory_policy != EXACT_INVENTORY_POLICY
            or reference.derived_status != "current"):
        raise ValueError("resource-pack tree reopened with another Core identity")
    _validate_stage(reference.path, plan)


def _retained_plan(input_plan: Mapping[str, Any], policy: Mapping[str, Any], raw: bytes,
                   expected_plan_id: str) -> dict[str, Any]:
    selected = _selected_declarations(input_plan, policy)
    lock = _object(raw, "retained resource-pack source lock")
    if lock.get("format") != LOCK_FORMAT:
        raise ValueError("retained resource-pack source lock has another format")
    plan = {**lock, "format": PLAN_FORMAT}
    if (set(plan) != {"format", "schema_version", "profile", "input_plan_id", "release_id",
                      "asset_sha256", "policy_id", "source_kind", "source_filesystem_state",
                      "destination_root", "files", "unresolved", "retained_file_count",
                      "retained_total_bytes", "curseforge_file_identity_state", "installation_state", "plan_id"}
            or plan["schema_version"] != 1 or plan["profile"] != "supersymmetry"
            or plan["input_plan_id"] != input_plan["plan_id"]
            or plan["release_id"] != input_plan["release_id"]
            or plan["asset_sha256"] != input_plan["asset_sha256"]
            or plan["source_kind"] != "local-prism-packwiz-resourcepacks"
            or plan["source_filesystem_state"] not in {"qualified-linux-local", "unqualified-readonly-wsl-9p"}
            or plan["destination_root"] != "resourcepacks"
            or plan["curseforge_file_identity_state"] != "unproven-by-local-sidecar"
            or plan["installation_state"] != "not-installed"
            or plan["plan_id"] != expected_plan_id):
        raise ValueError("retained resource-pack source lock differs from selected release")
    policy_id = "workbench-pack-release-resourcepack-policy:sha256:" + sha256(_canonical(policy)).hexdigest()
    if plan["policy_id"] != policy_id:
        raise ValueError("retained resource-pack source lock has another pack policy")
    body = {key: value for key, value in plan.items() if key != "plan_id"}
    if _PLAN_PREFIX + sha256(_canonical(body)).hexdigest() != expected_plan_id:
        raise ValueError("retained resource-pack source lock identity changed")
    if type(plan["files"]) is not list or type(plan["unresolved"]) is not list:
        raise ValueError("retained resource-pack source lock has invalid rows")
    declared = {(row["project_id"], row["file_id"]) for row in selected}
    seen: set[tuple[int, int]] = set()
    names: set[str] = set()
    total = 0
    for row in plan["files"]:
        if (type(row) is not dict
                or set(row) != {"project_id", "file_id", "filename", "sha1",
                                "sidecar_sha256", "size", "sha256", "destination_root",
                                "relative_path"}
                or type(row["project_id"]) is not int or type(row["file_id"]) is not int):
            raise ValueError("retained resource-pack source lock has an invalid file row")
        key = row["project_id"], row["file_id"]
        if (key not in declared or key in seen
                or row["destination_root"] != "resourcepacks"
                or type(row["size"]) is not int
                or not 0 < row["size"] <= policy["max_file_bytes"]
                or type(row["sha1"]) is not str or _SHA1.fullmatch(row["sha1"]) is None
                or type(row["sha256"]) is not str or _SHA256.fullmatch(row["sha256"]) is None
                or type(row["sidecar_sha256"]) is not str
                or _HEX256.fullmatch(row["sidecar_sha256"]) is None):
            raise ValueError("retained resource-pack source lock has an invalid file identity")
        name = _filename(row["filename"], policy["allowed_extensions"])
        if row["relative_path"] != "resourcepacks/" + name or name.casefold() in names:
            raise ValueError("retained resource-pack source lock has an invalid destination")
        names.add(name.casefold())
        seen.add(key)
        total += row["size"]
    if (total > policy["max_total_bytes"] or plan["retained_file_count"] != len(seen)
            or plan["retained_total_bytes"] != total):
        raise ValueError("retained resource-pack source lock has invalid byte bounds")
    for row in plan["unresolved"]:
        if (type(row) is not dict or set(row) != {"project_id", "file_id", "required"}
                or type(row["project_id"]) is not int or type(row["file_id"]) is not int
                or row["required"] is not True):
            raise ValueError("retained resource-pack source lock has an invalid unresolved row")
        key = row["project_id"], row["file_id"]
        if key not in declared or key in seen:
            raise ValueError("retained resource-pack source lock has an invalid unresolved ID")
        seen.add(key)
    if seen != declared:
        raise ValueError("retained resource-pack source lock does not cover selected placements")
    return plan


def _result(plan: Mapping[str, Any], reference: ManagedTreeReference, outcome: str) -> dict[str, Any]:
    return {"format": RESULT_FORMAT, "schema_version": 1, "outcome": outcome,
            "plan_id": plan["plan_id"], "tree_id": reference.tree_id,
            "tree_content_sha256": reference.content_sha256,
            "input_plan_id": plan["input_plan_id"],
            "destination_root": plan["destination_root"], "files": plan["files"],
            "retained_file_count": plan["retained_file_count"],
            "retained_total_bytes": plan["retained_total_bytes"],
            "unresolved": plan["unresolved"],
            "source_filesystem_state": plan["source_filesystem_state"],
            "curseforge_file_identity_state": plan["curseforge_file_identity_state"],
            "installation_state": "not-installed"}


def reopen_prism_resourcepacks(input_plan: Mapping[str, Any], *, expected_plan_id: str,
                               policy_path: Path, state_root: Path, config_home: Path) -> dict[str, Any]:
    """Verify exact ext4 custody without reading the former Prism directory."""

    if type(expected_plan_id) is not str or _PLAN_ID.fullmatch(expected_plan_id) is None:
        raise ValueError("select an exact reviewed resource-pack plan ID")
    policy = load_resourcepack_policy(policy_path)
    _selected_declarations(input_plan, policy)
    policy_id = "workbench-pack-release-resourcepack-policy:sha256:" + sha256(_canonical(policy)).hexdigest()
    host, root = _host(state_root, config_home, policy_id)
    target = root / expected_plan_id.rsplit(":", 1)[-1] / "snapshot"
    rows = [row for row in host.catalog.trees.inventory() if row["path"] == str(target)]
    if len(rows) != 1 or rows[0]["status"] != "committed":
        raise ValueError("resource-pack import lacks one completed Core tree")
    reference = host.describe(str(rows[0]["tree_id"]))
    if (reference.path != target or reference.owner_id != host.owner_id
            or reference.workspace != state_root or reference.policy_id != policy_id
            or reference.domain_id != expected_plan_id
            or reference.inventory_policy != EXACT_INVENTORY_POLICY
            or reference.derived_status != "current"):
        raise ValueError("resource-pack tree changed before retained readback")
    raw = read_private_single_link_bytes(target / "source-lock.json", byte_limit=_MAX_LOCK_BYTES)
    plan = _retained_plan(input_plan, policy, raw, expected_plan_id)
    _reopen(host, reference, target, plan)
    return _result(plan, reference, "reopened")


def apply_prism_resourcepacks(input_plan: Mapping[str, Any], *, source_root: Path,
                              archive_path: Path, policy_path: Path, state_root: Path,
                              config_home: Path, expected_plan_id: str) -> dict[str, Any]:
    """Copy reviewed bytes to resourcepacks/ in a private Core tree; do not install."""

    plan = plan_prism_resourcepacks(
        input_plan, source_root=source_root, archive_path=archive_path,
        policy_path=policy_path, state_root=state_root, config_home=config_home,
    )
    if plan["plan_id"] != expected_plan_id:
        raise ValueError("Prism resource-pack import changed after review")
    host, root = _host(state_root, config_home, plan["policy_id"])
    target = _target(root, plan)
    _private_directory(target.parent)
    if not private_path(target.parent, directory=True):
        raise ValueError("resource-pack target parent is not private")
    with private_record_lock(root / (".prism-resourcepack-" + expected_plan_id.rsplit(":", 1)[-1] + ".lock"), wait=True):
        current = plan_prism_resourcepacks(
            input_plan, source_root=source_root, archive_path=archive_path,
            policy_path=policy_path, state_root=state_root, config_home=config_home,
        )
        if current != plan:
            raise ValueError("Prism resource-pack inputs changed before staging")
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
    return _result(plan, reference, outcome)


__all__ = ["POLICY_FORMAT", "PLAN_FORMAT", "LOCK_FORMAT", "RESULT_FORMAT",
           "load_resourcepack_policy", "review_prism_resourcepacks",
           "plan_prism_resourcepacks", "apply_prism_resourcepacks", "reopen_prism_resourcepacks"]
