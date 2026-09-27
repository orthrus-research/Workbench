"""Extend exact retained Core mod trees with reviewed JAR bytes.

This is Core custody of bytes, not proof of CurseForge origin or installation.
Each original managed tree is read and referenced, never changed.
"""

from __future__ import annotations

from hashlib import sha1, sha256
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, Mapping

from workbench_api.managed_trees import ManagedTreeReference

from .durable_records import private_record_lock, read_bounded_bytes, read_private_single_link_bytes
from .environment_package_install_plan import _SUPPORTED_FILESYSTEMS, _mount_type
from .host_filesystem import private_path
from .managed_trees import CoreManagedTrees
from .output_routing import _private_directory
from .pack_release_local import _canonical, _filename, _held_file, _object, load_local_input_policy
from .pack_release_prism_import import (
    _host as prism_host, _retained_plan as retained_prism_plan, reopen_prism_import,
)
from .storage.exact_tree_inventory import EXACT_INVENTORY_POLICY


PLAN_FORMAT = "workbench-pack-release-mod-augmentation-plan-v1"
LOCK_FORMAT = "workbench-pack-release-mod-augmentation-source-lock-v1"
RESULT_FORMAT = "workbench-pack-release-mod-augmentation-result-v1"
PLAN_FORMAT_V2 = "workbench-pack-release-mod-augmentation-plan-v2"
LOCK_FORMAT_V2 = "workbench-pack-release-mod-augmentation-source-lock-v2"
RESULT_FORMAT_V2 = "workbench-pack-release-mod-augmentation-result-v2"
_PLAN_PREFIX = "workbench-pack-release-mod-augmentation-plan:sha256:"
_PLAN_ID = re.compile(r"workbench-pack-release-mod-augmentation-plan:sha256:[0-9a-f]{64}\Z")
_TREE_ID = re.compile(r"workbench-tree-v1:[0-9a-f]{32}\Z")
_PRISM_PLAN_ID = re.compile(r"workbench-pack-release-prism-import-plan:sha256:[0-9a-f]{64}\Z")
_INPUT_PLAN_ID = re.compile(r"workbench-pack-release-input-plan:sha256:[0-9a-f]{64}\Z")
_RELEASE_ID = re.compile(r"profile-release:sha256:[0-9a-f]{64}\Z")
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_LOCK_LIMIT = 1024 * 1024
_POLICY_LIMIT = 16 * 1024
POLICY_FORMAT = "workbench-supersymmetry-release-mod-augmentation-policy-v1"
POLICY_FORMAT_V2 = "workbench-supersymmetry-release-mod-augmentation-policy-v2"

def load_augmentation_policy(path: Path) -> dict[str, Any]:
    """Load the selected pack owner's two bounded local-byte assertions."""

    policy = _object(read_bounded_bytes(path, byte_limit=_POLICY_LIMIT), "mod augmentation policy")
    required = {"format", "schema_version", "profile", "input_plan_format", "input_plan_id",
                "release_id", "version", "manifest_sha256", "external_file_count",
                "destination_root", "sources"}
    if (set(policy) != required or policy["format"] != POLICY_FORMAT
            or policy["schema_version"] != 1 or policy["profile"] != "supersymmetry"
            or policy["input_plan_format"] != "workbench-pack-release-input-plan-v1"
            or type(policy["input_plan_id"]) is not str
            or _INPUT_PLAN_ID.fullmatch(policy["input_plan_id"]) is None
            or type(policy["release_id"]) is not str
            or _RELEASE_ID.fullmatch(policy["release_id"]) is None
            or type(policy["version"]) is not str
            or re.fullmatch(r"[0-9]+(\.[0-9]+){3}", policy["version"]) is None
            or type(policy["manifest_sha256"]) is not str
            or _SHA256.fullmatch(policy["manifest_sha256"]) is None
            or type(policy["external_file_count"]) is not int
            or not 1 <= policy["external_file_count"] <= 10000
            or policy["destination_root"] != "mods"
            or type(policy["sources"]) is not list or len(policy["sources"]) != 2):
        raise ValueError("Supersymmetry mod augmentation policy is incompatible")
    seen_ids: set[tuple[int, int]] = set()
    seen_names: set[str] = set()
    for row in policy["sources"]:
        if (type(row) is not dict
                or set(row) != {"project_id", "file_id", "filename", "size", "sha1", "sha256"}
                or type(row["project_id"]) is not int or not 0 < row["project_id"] < 2**63
                or type(row["file_id"]) is not int or not 0 < row["file_id"] < 2**63
                or _filename(row["filename"], [".jar"]) != row["filename"]
                or type(row["size"]) is not int or not 0 < row["size"] <= 536870912
                or type(row["sha1"]) is not str or _SHA1.fullmatch(row["sha1"]) is None
                or type(row["sha256"]) is not str or _SHA256.fullmatch(row["sha256"]) is None):
            raise ValueError("mod augmentation policy has an invalid source identity")
        key = row["project_id"], row["file_id"]
        folded = row["filename"].casefold()
        if key in seen_ids or folded in seen_names:
            raise ValueError("mod augmentation policy repeats a source or destination")
        seen_ids.add(key)
        seen_names.add(folded)
    return policy


def load_augmentation_policy_v2(path: Path) -> dict[str, Any]:
    """Load one release-owned identity for a verified Core artifact-cache entry."""

    policy = _object(read_bounded_bytes(path, byte_limit=_POLICY_LIMIT), "mod augmentation policy V2")
    required = {"format", "schema_version", "profile", "input_plan_format", "input_plan_id",
                "release_id", "version", "manifest_sha256", "external_file_count",
                "destination_root", "source_kind", "prior_retained_file_count", "sources"}
    if (set(policy) != required or policy["format"] != POLICY_FORMAT_V2
            or policy["schema_version"] != 2 or policy["profile"] != "supersymmetry"
            or policy["input_plan_format"] != "workbench-pack-release-input-plan-v1"
            or type(policy["input_plan_id"]) is not str
            or _INPUT_PLAN_ID.fullmatch(policy["input_plan_id"]) is None
            or type(policy["release_id"]) is not str
            or _RELEASE_ID.fullmatch(policy["release_id"]) is None
            or type(policy["version"]) is not str
            or re.fullmatch(r"[0-9]+(\.[0-9]+){3}", policy["version"]) is None
            or type(policy["manifest_sha256"]) is not str
            or _SHA256.fullmatch(policy["manifest_sha256"]) is None
            or type(policy["external_file_count"]) is not int
            or not 1 <= policy["external_file_count"] <= 10000
            or policy["destination_root"] != "mods"
            or policy["source_kind"] != "verified-core-artifact-cache"
            or type(policy["prior_retained_file_count"]) is not int
            or policy["prior_retained_file_count"] != 189
            or type(policy["sources"]) is not list or len(policy["sources"]) != 1):
        raise ValueError("Supersymmetry mod augmentation policy V2 is incompatible")
    row = policy["sources"][0]
    if (type(row) is not dict
            or set(row) != {"project_id", "file_id", "filename", "size", "sha1", "sha256"}
            or type(row["project_id"]) is not int or not 0 < row["project_id"] < 2**63
            or type(row["file_id"]) is not int or not 0 < row["file_id"] < 2**63
            or _filename(row["filename"], [".jar"]) != row["filename"]
            or type(row["size"]) is not int or not 0 < row["size"] <= 536870912
            or type(row["sha1"]) is not str or _SHA1.fullmatch(row["sha1"]) is None
            or type(row["sha256"]) is not str or _SHA256.fullmatch(row["sha256"]) is None):
        raise ValueError("mod augmentation policy V2 has an invalid source identity")
    return policy


def _selected_plan(input_plan: Mapping[str, Any], policy: Mapping[str, Any]) -> None:
    if (not isinstance(input_plan, Mapping)
            or input_plan.get("format") != "workbench-pack-release-input-plan-v1"
            or type(input_plan.get("plan_id")) is not str
            or type(input_plan.get("external_files")) is not list
            or input_plan.get("plan_id") != policy["input_plan_id"]
            or input_plan.get("release_id") != policy["release_id"]
            or input_plan.get("version") != policy["version"]
            or input_plan.get("manifest_sha256") != policy["manifest_sha256"]
            or len(input_plan["external_files"]) != policy["external_file_count"]):
        raise ValueError("mod augmentation needs the selected release input plan")
    body = {key: value for key, value in input_plan.items() if key != "plan_id"}
    if input_plan["plan_id"] != "workbench-pack-release-input-plan:sha256:" + sha256(_canonical(body)).hexdigest():
        raise ValueError("selected release input plan identity changed")
    declared = {(row["project_id"], row["file_id"]): row["required"]
                for row in input_plan["external_files"]}
    if (len(declared) != len(input_plan["external_files"])
            or any(declared.get((row["project_id"], row["file_id"])) is not True
                   for row in policy["sources"])):
        raise ValueError("older fixture IDs are not exact required release declarations")


def _source_id(row: Mapping[str, Any]) -> str:
    return f"reviewed-local-linux:{row['project_id']}:{row['file_id']}:{row['sha256']}"


def _local_rows(policy: Mapping[str, Any],
                filesystems: Mapping[tuple[int, int], str]) -> list[dict[str, Any]]:
    return [dict(row, source_id=_source_id(row), relative_path="mods/" + row["filename"],
                 source_filesystem=filesystems[row["project_id"], row["file_id"]])
            for row in policy["sources"]]


def _paths(source_paths: Mapping[tuple[int, int], Path],
           policy: Mapping[str, Any]) -> tuple[dict[tuple[int, int], Path],
                                              dict[tuple[int, int], str]]:
    required = {(row["project_id"], row["file_id"]) for row in policy["sources"]}
    if not isinstance(source_paths, Mapping) or set(source_paths) != required:
        raise ValueError("mod augmentation needs exactly two reviewed local sources")
    paths: dict[tuple[int, int], Path] = {}
    filesystems: dict[tuple[int, int], str] = {}
    for row in policy["sources"]:
        key = row["project_id"], row["file_id"]
        path = source_paths[key]
        if (not isinstance(path, Path) or not path.is_absolute()
                or any(part in {".", ".."} for part in path.parts)
                or path.name != row["filename"]):
            raise ValueError("reviewed older fixture source must have its declared filename")
        filesystem = _mount_type(path.parent)
        if filesystem not in _SUPPORTED_FILESYSTEMS:
            raise ValueError("reviewed older fixture source needs a qualified Linux filesystem")
        paths[key] = path
        filesystems[key] = filesystem
    return paths, filesystems


def _copy_verified(source: Path, destination: Path, row: Mapping[str, Any], *,
                   copy: bool) -> tuple[int, int]:
    """Read a held ordinary file, hashing every byte before accepting it."""

    descriptor_out = -1
    try:
        if copy:
            descriptor_out = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with _held_file(source, expected_size=row["size"]) as (descriptor, opened):
            one, two, size = sha1(), sha256(), 0
            while block := os.read(descriptor, 1024 * 1024):
                size += len(block)
                if size > row["size"]:
                    raise ValueError("mod augmentation source grew during read")
                one.update(block)
                two.update(block)
                if descriptor_out >= 0:
                    remaining = memoryview(block)
                    while remaining:
                        written = os.write(descriptor_out, remaining)
                        if written <= 0:
                            raise ValueError("mod augmentation copy made no progress")
                        remaining = remaining[written:]
            if (size != row["size"] or one.hexdigest() != row["sha1"]
                    or "sha256:" + two.hexdigest() != row["sha256"]):
                raise ValueError("mod augmentation source bytes differ from reviewed identity")
            if descriptor_out >= 0:
                os.fsync(descriptor_out)
            return opened.st_dev, opened.st_ino
    finally:
        if descriptor_out >= 0:
            os.close(descriptor_out)


def _prior(input_plan: Mapping[str, Any], *, prior_plan_id: str, prior_tree_id: str,
           policy_path: Path, state_root: Path, config_home: Path,
           local_policy: Mapping[str, Any],
           augmentation_policy: Mapping[str, Any]) -> tuple[ManagedTreeReference, dict[str, Any]]:
    if (type(prior_plan_id) is not str or _PRISM_PLAN_ID.fullmatch(prior_plan_id) is None
            or type(prior_tree_id) is not str or _TREE_ID.fullmatch(prior_tree_id) is None):
        raise ValueError("select exact prior Prism plan and Core tree IDs")
    previous = reopen_prism_import(
        input_plan, expected_plan_id=prior_plan_id, policy_path=policy_path,
        state_root=state_root, config_home=config_home,
    )
    if previous["tree_id"] != prior_tree_id:
        raise ValueError("prior Prism tree is not the selected Core tree")
    prior_host, _ = prism_host(state_root, config_home, previous_policy_id(local_policy))
    reference = prior_host.describe(prior_tree_id)
    raw = read_private_single_link_bytes(reference.path / "source-lock.json", byte_limit=_LOCK_LIMIT)
    plan = retained_prism_plan(input_plan, local_policy, raw, prior_plan_id)
    if (plan["retained_file_count"] != previous["retained_file_count"]
            or reference.content_sha256 != previous["tree_content_sha256"]):
        raise ValueError("prior Prism tree changed during augmentation review")
    missing = {(row["project_id"], row["file_id"]): row for row in plan["unresolved"]}
    if any(missing.get((row["project_id"], row["file_id"]), {}).get("required") is not True
           for row in augmentation_policy["sources"]):
        raise ValueError("older fixture IDs are not unresolved in the prior Core tree")
    return reference, plan


def previous_policy_id(policy: Mapping[str, Any]) -> str:
    return "workbench-pack-release-local-input-policy:sha256:" + sha256(_canonical(policy)).hexdigest()


def _policy_id(local_policy: Mapping[str, Any], augmentation_policy: Mapping[str, Any]) -> str:
    identity = {"format": PLAN_FORMAT, "prior_policy_id": previous_policy_id(local_policy),
                "augmentation_policy": augmentation_policy}
    return "workbench-pack-release-mod-augmentation-policy:sha256:" + sha256(_canonical(identity)).hexdigest()


def _candidate(input_plan: Mapping[str, Any], prior: ManagedTreeReference,
               prior_plan: Mapping[str, Any], local_policy: Mapping[str, Any],
               augmentation_policy: Mapping[str, Any],
               source_filesystems: Mapping[tuple[int, int], str]) -> dict[str, Any]:
    existing = [{"project_id": row["project_id"], "file_id": row["file_id"],
                 "filename": row["filename"], "relative_path": "mods/" + row["filename"],
                 "size": row["size"], "sha1": row["sha1"], "sha256": row["sha256"],
                 "source_id": prior.tree_id} for row in prior_plan["files"]]
    local = _local_rows(augmentation_policy, source_filesystems)
    names = [row["filename"].casefold() for row in existing + local]
    if len(set(names)) != len(names):
        raise ValueError("older fixture destination collides with prior Prism mods")
    if any(_filename(row["filename"], local_policy["allowed_extensions"]) != row["filename"]
           for row in existing + local):
        raise ValueError("mod augmentation has an unsafe destination filename")
    keys = {(row["project_id"], row["file_id"]) for row in local}
    unresolved = [row for row in prior_plan["unresolved"]
                  if (row["project_id"], row["file_id"]) not in keys]
    files = existing + local
    total = sum(row["size"] for row in files)
    if (any(row["size"] > local_policy["max_file_bytes"] for row in files)
            or total > local_policy["max_total_bytes"]):
        raise ValueError("mod augmentation exceeds the selected pack byte limits")
    body = {"format": PLAN_FORMAT, "schema_version": 1, "profile": "supersymmetry",
            "input_plan_id": input_plan["plan_id"], "release_id": input_plan["release_id"],
            "asset_sha256": input_plan["asset_sha256"],
            "policy_id": _policy_id(local_policy, augmentation_policy),
            "prior_plan_id": prior_plan["plan_id"], "prior_tree_id": prior.tree_id,
            "prior_tree_content_sha256": prior.content_sha256,
            "source_kind": "prior-core-prism-tree-plus-reviewed-local-linux-fixtures",
            "destination_root": "mods", "local_sources": local, "files": files,
            "unresolved": unresolved, "optional_unselected": prior_plan["optional_unselected"],
            "retained_file_count": len(files), "retained_total_bytes": total,
            "curseforge_file_identity_state": "unproven-by-local-assertions",
            "installation_state": "not-installed"}
    return {**body, "plan_id": _PLAN_PREFIX + sha256(_canonical(body)).hexdigest()}


def _policy_id_v2(local_policy: Mapping[str, Any],
                  prior_augmentation_policy: Mapping[str, Any],
                  augmentation_policy: Mapping[str, Any]) -> str:
    identity = {"format": PLAN_FORMAT_V2,
                "prior_policy_id": _policy_id(local_policy, prior_augmentation_policy),
                "augmentation_policy": augmentation_policy}
    return "workbench-pack-release-mod-augmentation-policy:sha256:" + sha256(_canonical(identity)).hexdigest()


def _prior_v2(input_plan: Mapping[str, Any], *, prior_plan_id: str, prior_tree_id: str,
              policy_path: Path, prior_augmentation_policy_path: Path,
              state_root: Path, config_home: Path, local_policy: Mapping[str, Any],
              augmentation_policy: Mapping[str, Any]) -> tuple[ManagedTreeReference, dict[str, Any]]:
    if (type(prior_plan_id) is not str or _PLAN_ID.fullmatch(prior_plan_id) is None
            or type(prior_tree_id) is not str or _TREE_ID.fullmatch(prior_tree_id) is None):
        raise ValueError("select exact prior augmentation plan and Core tree IDs")
    previous = reopen_mod_augmentation(
        input_plan, expected_plan_id=prior_plan_id, policy_path=policy_path,
        augmentation_policy_path=prior_augmentation_policy_path,
        state_root=state_root, config_home=config_home,
    )
    if (previous["tree_id"] != prior_tree_id
            or previous["retained_file_count"] != augmentation_policy["prior_retained_file_count"]):
        raise ValueError("prior augmentation is not the selected 189-file Core tree")
    prior_policy = load_augmentation_policy(prior_augmentation_policy_path)
    prior_host, _ = _host(state_root, config_home, _policy_id(local_policy, prior_policy))
    reference = prior_host.describe(prior_tree_id)
    raw = read_private_single_link_bytes(reference.path / "source-lock.json", byte_limit=_LOCK_LIMIT)
    lock = _object(raw, "prior mod augmentation source lock")
    prior_plan = {**lock, "format": PLAN_FORMAT}
    if (lock.get("format") != LOCK_FORMAT or raw != _lock(prior_plan)
            or prior_plan["plan_id"] != prior_plan_id
            or reference.content_sha256 != previous["tree_content_sha256"]):
        raise ValueError("prior augmentation changed during follow-on review")
    required = {(row["project_id"], row["file_id"]): row["required"]
                for row in prior_plan["unresolved"]}
    if required.get((augmentation_policy["sources"][0]["project_id"],
                     augmentation_policy["sources"][0]["file_id"])) is not True:
        raise ValueError("follow-on mod is not unresolved and required in the prior Core tree")
    return reference, prior_plan


def _artifact_cache_path(artifact_path: Path, state_root: Path,
                         row: Mapping[str, Any]) -> str:
    expected = state_root / "artifacts" / "sha256" / row["sha256"].removeprefix("sha256:")
    if (not isinstance(artifact_path, Path) or not artifact_path.is_absolute()
            or any(part in {".", ".."} for part in artifact_path.parts)
            or artifact_path != expected):
        raise ValueError("follow-on mod needs the exact verified Core artifact-cache path")
    filesystem = _mount_type(artifact_path.parent)
    if filesystem not in _SUPPORTED_FILESYSTEMS:
        raise ValueError("follow-on artifact cache needs a qualified Linux filesystem")
    return filesystem


def _candidate_v2(input_plan: Mapping[str, Any], prior: ManagedTreeReference,
                  prior_plan: Mapping[str, Any], local_policy: Mapping[str, Any],
                  prior_augmentation_policy: Mapping[str, Any],
                  augmentation_policy: Mapping[str, Any], source_filesystem: str) -> dict[str, Any]:
    source = augmentation_policy["sources"][0]
    existing = [{**row, "source_id": prior.tree_id} for row in prior_plan["files"]]
    local = [dict(source, source_id="verified-core-artifact-cache:" + source["sha256"],
                  relative_path="mods/" + source["filename"],
                  source_filesystem=source_filesystem)]
    files = existing + local
    if len({row["filename"].casefold() for row in files}) != len(files):
        raise ValueError("follow-on mod destination collides with prior Core mods")
    if any(_filename(row["filename"], local_policy["allowed_extensions"]) != row["filename"]
           for row in files):
        raise ValueError("follow-on mod has an unsafe destination filename")
    if len(existing) != augmentation_policy["prior_retained_file_count"]:
        raise ValueError("follow-on mod needs the selected 189-file Core tree")
    key = source["project_id"], source["file_id"]
    unresolved = [row for row in prior_plan["unresolved"]
                  if (row["project_id"], row["file_id"]) != key]
    total = sum(row["size"] for row in files)
    if (any(row["size"] > local_policy["max_file_bytes"] for row in files)
            or total > local_policy["max_total_bytes"]):
        raise ValueError("follow-on mod exceeds the selected pack byte limits")
    body = {"format": PLAN_FORMAT_V2, "schema_version": 2, "profile": "supersymmetry",
            "input_plan_id": input_plan["plan_id"], "release_id": input_plan["release_id"],
            "asset_sha256": input_plan["asset_sha256"],
            "policy_id": _policy_id_v2(local_policy, prior_augmentation_policy, augmentation_policy),
            "prior_plan_id": prior_plan["plan_id"], "prior_tree_id": prior.tree_id,
            "prior_tree_content_sha256": prior.content_sha256,
            "source_kind": "prior-core-mod-augmentation-tree-plus-verified-artifact-cache",
            "destination_root": "mods", "local_sources": local, "files": files,
            "unresolved": unresolved, "optional_unselected": prior_plan["optional_unselected"],
            "retained_file_count": len(files), "retained_total_bytes": total,
            "curseforge_file_identity_state": "unproven-by-local-assertions",
            "installation_state": "not-installed"}
    return {**body, "plan_id": _PLAN_PREFIX + sha256(_canonical(body)).hexdigest()}


def _host(state_root: Path, config_home: Path, policy_id: str) -> tuple[CoreManagedTrees, Path]:
    if (_mount_type(state_root) not in _SUPPORTED_FILESYSTEMS
            or not state_root.is_absolute() or not config_home.is_absolute()):
        raise ValueError("mod augmentation destination needs a qualified Linux filesystem")
    root = state_root / "pack-release-mod-augmentations"
    return CoreManagedTrees(
        workspace=state_root, configuration_home=config_home,
        locations={"artifacts": root}, owner_id="supersymmetry",
        policy_id=policy_id, location_sources={"artifacts": "pack-release-mod-augmentation"},
    ), root


def _target(root: Path, plan_id: str) -> Path:
    return root / plan_id.rsplit(":", 1)[-1] / "snapshot"


def _tree_state(host: CoreManagedTrees, target: Path, plan: Mapping[str, Any]) -> tuple[str, str | None]:
    rows = [row for row in host.catalog.trees.inventory() if row["path"] == str(target)]
    if len(rows) > 1:
        raise ValueError("mod augmentation has ambiguous Core reservations")
    if not rows:
        if target.exists() or target.is_symlink():
            raise ValueError("mod augmentation target exists outside Core custody")
        return "acquire", None
    if rows[0]["status"] != "committed":
        raise ValueError("mod augmentation has an incomplete stage requiring review")
    reference = host.describe(str(rows[0]["tree_id"]))
    if (reference.path != target or reference.owner_id != host.owner_id
            or reference.workspace != host.workspace or reference.role != "artifacts"
            or reference.policy_id != plan["policy_id"]
            or reference.domain_id != plan["plan_id"]
            or reference.references != (plan["prior_tree_id"],)):
        raise ValueError("mod augmentation retained another candidate")
    return "reuse", reference.tree_id


def _review(input_plan: Mapping[str, Any], *, prior_plan_id: str, prior_tree_id: str,
            source_paths: Mapping[tuple[int, int], Path], policy_path: Path,
            augmentation_policy_path: Path,
            state_root: Path, config_home: Path) -> tuple[dict[str, Any], ManagedTreeReference]:
    local_policy = load_local_input_policy(policy_path)
    augmentation_policy = load_augmentation_policy(augmentation_policy_path)
    _selected_plan(input_plan, augmentation_policy)
    prior, prior_plan = _prior(
        input_plan, prior_plan_id=prior_plan_id, prior_tree_id=prior_tree_id,
        policy_path=policy_path, state_root=state_root, config_home=config_home,
        local_policy=local_policy, augmentation_policy=augmentation_policy,
    )
    paths, source_filesystems = _paths(source_paths, augmentation_policy)
    identities = [_copy_verified(paths[(row["project_id"], row["file_id"])], Path(), row, copy=False)
                  for row in augmentation_policy["sources"]]
    if len(set(identities)) != len(identities):
        raise ValueError("one older fixture source is assigned to both release IDs")
    return _candidate(input_plan, prior, prior_plan, local_policy,
                      augmentation_policy, source_filesystems), prior


def plan_mod_augmentation(input_plan: Mapping[str, Any], *, prior_plan_id: str,
                          prior_tree_id: str, source_paths: Mapping[tuple[int, int], Path],
                          policy_path: Path, augmentation_policy_path: Path, state_root: Path,
                          config_home: Path) -> dict[str, Any]:
    """Review the old Core tree and two qualified Linux files; return a path-free plan."""

    candidate, _ = _review(
        input_plan, prior_plan_id=prior_plan_id, prior_tree_id=prior_tree_id,
        source_paths=source_paths, policy_path=policy_path,
        augmentation_policy_path=augmentation_policy_path,
        state_root=state_root, config_home=config_home,
    )
    host, root = _host(state_root, config_home, candidate["policy_id"])
    action, tree_id = _tree_state(host, _target(root, candidate["plan_id"]), candidate)
    return {**candidate, "action": action, "tree_id": tree_id}


def _lock(plan: Mapping[str, Any]) -> bytes:
    value = {key: item for key, item in plan.items() if key not in {"action", "tree_id"}}
    value["format"] = LOCK_FORMAT_V2 if plan["format"] == PLAN_FORMAT_V2 else LOCK_FORMAT
    return _canonical(value) + b"\n"


def _write_lock(stage: Path, plan: Mapping[str, Any]) -> None:
    descriptor = os.open(stage / "source-lock.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            output.write(_lock(plan))
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(descriptor)


def _copy_to_stage(stage: Path, prior: ManagedTreeReference,
                   source_paths: Mapping[tuple[int, int], Path],
                   augmentation_policy: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    paths, filesystems = _paths(source_paths, augmentation_policy)
    if any(filesystems[row["project_id"], row["file_id"]] != row["source_filesystem"]
           for row in plan["local_sources"]):
        raise ValueError("reviewed older fixture filesystem changed before copy")

    def source_for(row: Mapping[str, Any]) -> Path:
        key = row["project_id"], row["file_id"]
        return prior.path / row["filename"] if row["source_id"] == prior.tree_id else paths[key]

    _copy_rows_to_stage(stage, plan, source_for)


def _copy_rows_to_stage(stage: Path, plan: Mapping[str, Any],
                        source_for: Callable[[Mapping[str, Any]], Path]) -> None:
    stage.mkdir(mode=0o700)
    mods = stage / "mods"
    mods.mkdir(mode=0o700)
    for row in plan["files"]:
        _copy_verified(source_for(row), mods / row["filename"], row, copy=True)
    _write_lock(stage, plan)


def _validate_stage(stage: Path, plan: Mapping[str, Any]) -> None:
    if {entry.name for entry in stage.iterdir()} != {"mods", "source-lock.json"}:
        raise ValueError("mod augmentation stage has unexpected root members")
    mods = stage / "mods"
    if not stat.S_ISDIR(mods.lstat().st_mode):
        raise ValueError("mod augmentation destination changed type")
    if {entry.name for entry in mods.iterdir()} != {row["filename"] for row in plan["files"]}:
        raise ValueError("mod augmentation stage has unexpected mod members")
    if read_private_single_link_bytes(stage / "source-lock.json", byte_limit=_LOCK_LIMIT) != _lock(plan):
        raise ValueError("mod augmentation source lock changed")
    for row in plan["files"]:
        path = mods / row["filename"]
        visible = path.lstat()
        if (not stat.S_ISREG(visible.st_mode) or visible.st_nlink != 1
                or visible.st_size != row["size"]):
            raise ValueError("mod augmentation stage member changed type or size")
        _copy_verified(path, Path(), row, copy=False)


def _reopen(host: CoreManagedTrees, reference: ManagedTreeReference,
            target: Path, plan: Mapping[str, Any]) -> None:
    if (reference.path != target or reference.workspace != host.workspace
            or reference.owner_id != host.owner_id or reference.role != "artifacts"
            or reference.policy_id != plan["policy_id"]
            or reference.domain_id != plan["plan_id"]
            or reference.references != (plan["prior_tree_id"],)
            or reference.inventory_policy != EXACT_INVENTORY_POLICY
            or reference.derived_status != "current"):
        raise ValueError("mod augmentation tree reopened with another Core identity")
    _validate_stage(reference.path, plan)


def _result(plan: Mapping[str, Any], reference: ManagedTreeReference, outcome: str) -> dict[str, Any]:
    version = 2 if plan["format"] == PLAN_FORMAT_V2 else 1
    return {"format": RESULT_FORMAT_V2 if version == 2 else RESULT_FORMAT,
            "schema_version": version, "outcome": outcome,
            "plan_id": plan["plan_id"], "tree_id": reference.tree_id,
            "tree_content_sha256": reference.content_sha256,
            "input_plan_id": plan["input_plan_id"], "release_id": plan["release_id"],
            "prior_plan_id": plan["prior_plan_id"], "prior_tree_id": plan["prior_tree_id"],
            "prior_tree_content_sha256": plan["prior_tree_content_sha256"],
            "local_sources": plan["local_sources"],
            "retained_file_count": plan["retained_file_count"],
            "retained_total_bytes": plan["retained_total_bytes"],
            "unresolved": plan["unresolved"],
            "curseforge_file_identity_state": plan["curseforge_file_identity_state"],
            "installation_state": "not-installed"}


def reopen_mod_augmentation(input_plan: Mapping[str, Any], *, expected_plan_id: str,
                            policy_path: Path, augmentation_policy_path: Path, state_root: Path,
                            config_home: Path) -> dict[str, Any]:
    """Reopen the new exact tree without either local source directory."""

    if type(expected_plan_id) is not str or _PLAN_ID.fullmatch(expected_plan_id) is None:
        raise ValueError("select an exact reviewed mod augmentation plan ID")
    local_policy = load_local_input_policy(policy_path)
    augmentation_policy = load_augmentation_policy(augmentation_policy_path)
    _selected_plan(input_plan, augmentation_policy)
    host, root = _host(state_root, config_home, _policy_id(local_policy, augmentation_policy))
    target = _target(root, expected_plan_id)
    rows = [row for row in host.catalog.trees.inventory() if row["path"] == str(target)]
    if len(rows) != 1 or rows[0]["status"] != "committed":
        raise ValueError("mod augmentation lacks one completed Core tree")
    reference = host.describe(str(rows[0]["tree_id"]))
    raw = read_private_single_link_bytes(target / "source-lock.json", byte_limit=_LOCK_LIMIT)
    lock = _object(raw, "retained mod augmentation source lock")
    if lock.get("format") != LOCK_FORMAT:
        raise ValueError("retained mod augmentation source lock has another format")
    plan = {**lock, "format": PLAN_FORMAT}
    expected_keys = {"format", "schema_version", "profile", "input_plan_id", "release_id",
                     "asset_sha256", "policy_id", "prior_plan_id", "prior_tree_id",
                     "prior_tree_content_sha256", "source_kind", "destination_root",
                     "local_sources", "files", "unresolved", "optional_unselected",
                     "retained_file_count", "retained_total_bytes",
                     "curseforge_file_identity_state", "installation_state", "plan_id"}
    if (set(plan) != expected_keys or plan["plan_id"] != expected_plan_id
            or raw != _lock(plan) or plan["schema_version"] != 1
            or plan["profile"] != "supersymmetry"
            or plan["input_plan_id"] != input_plan["plan_id"]
            or plan["release_id"] != input_plan["release_id"]
            or plan["asset_sha256"] != input_plan["asset_sha256"]
            or plan["policy_id"] != _policy_id(local_policy, augmentation_policy)
            or plan["source_kind"] != "prior-core-prism-tree-plus-reviewed-local-linux-fixtures"
            or plan["destination_root"] != "mods"
            or plan["curseforge_file_identity_state"] != "unproven-by-local-assertions"
            or plan["installation_state"] != "not-installed"):
        raise ValueError("retained mod augmentation lock differs from selected release")
    body = {key: value for key, value in plan.items() if key != "plan_id"}
    if _PLAN_PREFIX + sha256(_canonical(body)).hexdigest() != expected_plan_id:
        raise ValueError("retained mod augmentation plan identity changed")
    prior, prior_plan = _prior(
        input_plan, prior_plan_id=plan["prior_plan_id"], prior_tree_id=plan["prior_tree_id"],
        policy_path=policy_path, state_root=state_root, config_home=config_home,
        local_policy=local_policy, augmentation_policy=augmentation_policy,
    )
    local_sources = plan["local_sources"]
    if (type(local_sources) is not list or len(local_sources) != 2
            or any(type(row) is not dict
                   or set(row) != {"project_id", "file_id", "filename", "size", "sha1",
                                   "sha256", "source_id", "relative_path", "source_filesystem"}
                   or type(row["source_filesystem"]) is not str
                   or row["source_filesystem"] not in _SUPPORTED_FILESYSTEMS
                   for row in local_sources)):
        raise ValueError("retained mod augmentation has invalid local source rows")
    filesystems = {(row["project_id"], row["file_id"]): row["source_filesystem"]
                   for row in local_sources}
    if plan != _candidate(input_plan, prior, prior_plan, local_policy,
                          augmentation_policy, filesystems):
        raise ValueError("retained mod augmentation rows differ from source identities")
    _reopen(host, reference, target, plan)
    return _result(plan, reference, "reopened")


def apply_mod_augmentation(input_plan: Mapping[str, Any], *, prior_plan_id: str,
                           prior_tree_id: str, source_paths: Mapping[tuple[int, int], Path],
                           policy_path: Path, augmentation_policy_path: Path,
                           state_root: Path, config_home: Path,
                           expected_plan_id: str) -> dict[str, Any]:
    """Publish a new exact Core mods tree, preserving and referencing the old tree."""

    plan = plan_mod_augmentation(
        input_plan, prior_plan_id=prior_plan_id, prior_tree_id=prior_tree_id,
        source_paths=source_paths, policy_path=policy_path,
        augmentation_policy_path=augmentation_policy_path,
        state_root=state_root, config_home=config_home,
    )
    if plan["plan_id"] != expected_plan_id:
        raise ValueError("mod augmentation changed after review")
    host, root = _host(state_root, config_home, plan["policy_id"])
    target = _target(root, expected_plan_id)
    _private_directory(target.parent)
    if not private_path(target.parent, directory=True):
        raise ValueError("mod augmentation target parent is not private")
    with private_record_lock(root / (".mod-augmentation-" + expected_plan_id.rsplit(":", 1)[-1] + ".lock"), wait=True):
        current, prior = _review(
            input_plan, prior_plan_id=prior_plan_id, prior_tree_id=prior_tree_id,
            source_paths=source_paths, policy_path=policy_path,
            augmentation_policy_path=augmentation_policy_path,
            state_root=state_root, config_home=config_home,
        )
        action, tree_id = _tree_state(host, target, current)
        if {**current, "action": action, "tree_id": tree_id} != plan:
            raise ValueError("mod augmentation inputs changed before staging")
        if action == "acquire":
            augmentation_policy = load_augmentation_policy(augmentation_policy_path)
            local_policy = load_local_input_policy(policy_path)
            if _policy_id(local_policy, augmentation_policy) != plan["policy_id"]:
                raise ValueError("mod augmentation policy changed before staging")
            with host.stage("artifacts", target.name, requested_path=target) as stage:
                _copy_to_stage(stage.path, prior, source_paths, augmentation_policy, plan)
                reference = stage.publish(
                    validate=lambda path: _validate_stage(path, plan),
                    domain_id=plan["plan_id"], references=(prior_tree_id,),
                    inventory_policy=EXACT_INVENTORY_POLICY,
                )
            outcome = "retained"
        else:
            reference = host.describe(tree_id)
            outcome = "reused"
        _reopen(host, reference, target, plan)
    return _result(plan, reference, outcome)


def _review_v2(input_plan: Mapping[str, Any], *, prior_plan_id: str, prior_tree_id: str,
               artifact_path: Path, policy_path: Path, prior_augmentation_policy_path: Path,
               augmentation_policy_path: Path, state_root: Path,
               config_home: Path) -> tuple[dict[str, Any], ManagedTreeReference]:
    local_policy = load_local_input_policy(policy_path)
    prior_policy = load_augmentation_policy(prior_augmentation_policy_path)
    augmentation_policy = load_augmentation_policy_v2(augmentation_policy_path)
    _selected_plan(input_plan, augmentation_policy)
    prior, prior_plan = _prior_v2(
        input_plan, prior_plan_id=prior_plan_id, prior_tree_id=prior_tree_id,
        policy_path=policy_path, prior_augmentation_policy_path=prior_augmentation_policy_path,
        state_root=state_root, config_home=config_home, local_policy=local_policy,
        augmentation_policy=augmentation_policy,
    )
    source = augmentation_policy["sources"][0]
    filesystem = _artifact_cache_path(artifact_path, state_root, source)
    _copy_verified(artifact_path, Path(), source, copy=False)
    return _candidate_v2(input_plan, prior, prior_plan, local_policy, prior_policy,
                         augmentation_policy, filesystem), prior


def plan_mod_augmentation_v2(input_plan: Mapping[str, Any], *, prior_plan_id: str,
                             prior_tree_id: str, artifact_path: Path, policy_path: Path,
                             prior_augmentation_policy_path: Path,
                             augmentation_policy_path: Path, state_root: Path,
                             config_home: Path) -> dict[str, Any]:
    """Review a held Core cache entry against the V1 tree; return a path-free plan."""

    candidate, _ = _review_v2(
        input_plan, prior_plan_id=prior_plan_id, prior_tree_id=prior_tree_id,
        artifact_path=artifact_path, policy_path=policy_path,
        prior_augmentation_policy_path=prior_augmentation_policy_path,
        augmentation_policy_path=augmentation_policy_path,
        state_root=state_root, config_home=config_home,
    )
    host, root = _host(state_root, config_home, candidate["policy_id"])
    action, tree_id = _tree_state(host, _target(root, candidate["plan_id"]), candidate)
    return {**candidate, "action": action, "tree_id": tree_id}


def _copy_v2_to_stage(stage: Path, prior: ManagedTreeReference, artifact_path: Path,
                      augmentation_policy: Mapping[str, Any], plan: Mapping[str, Any],
                      state_root: Path) -> None:
    source = augmentation_policy["sources"][0]
    if _artifact_cache_path(artifact_path, state_root, source) != plan["local_sources"][0]["source_filesystem"]:
        raise ValueError("follow-on artifact-cache filesystem changed before copy")

    def source_for(row: Mapping[str, Any]) -> Path:
        return (prior.path / "mods" / row["filename"]
                if row["source_id"] == prior.tree_id else artifact_path)

    _copy_rows_to_stage(stage, plan, source_for)


def reopen_mod_augmentation_v2(input_plan: Mapping[str, Any], *, expected_plan_id: str,
                               policy_path: Path, prior_augmentation_policy_path: Path,
                               augmentation_policy_path: Path, state_root: Path,
                               config_home: Path) -> dict[str, Any]:
    """Reopen the 190-JAR Core tree without the held artifact-cache entry."""

    if type(expected_plan_id) is not str or _PLAN_ID.fullmatch(expected_plan_id) is None:
        raise ValueError("select an exact reviewed mod augmentation plan ID")
    local_policy = load_local_input_policy(policy_path)
    prior_policy = load_augmentation_policy(prior_augmentation_policy_path)
    augmentation_policy = load_augmentation_policy_v2(augmentation_policy_path)
    _selected_plan(input_plan, augmentation_policy)
    policy_id = _policy_id_v2(local_policy, prior_policy, augmentation_policy)
    host, root = _host(state_root, config_home, policy_id)
    target = _target(root, expected_plan_id)
    rows = [row for row in host.catalog.trees.inventory() if row["path"] == str(target)]
    if len(rows) != 1 or rows[0]["status"] != "committed":
        raise ValueError("follow-on augmentation lacks one completed Core tree")
    reference = host.describe(str(rows[0]["tree_id"]))
    raw = read_private_single_link_bytes(target / "source-lock.json", byte_limit=_LOCK_LIMIT)
    lock = _object(raw, "retained follow-on mod augmentation source lock")
    if lock.get("format") != LOCK_FORMAT_V2:
        raise ValueError("retained follow-on mod augmentation source lock has another format")
    plan = {**lock, "format": PLAN_FORMAT_V2}
    expected_keys = {"format", "schema_version", "profile", "input_plan_id", "release_id",
                     "asset_sha256", "policy_id", "prior_plan_id", "prior_tree_id",
                     "prior_tree_content_sha256", "source_kind", "destination_root",
                     "local_sources", "files", "unresolved", "optional_unselected",
                     "retained_file_count", "retained_total_bytes",
                     "curseforge_file_identity_state", "installation_state", "plan_id"}
    if (set(plan) != expected_keys or plan["plan_id"] != expected_plan_id
            or raw != _lock(plan) or plan["schema_version"] != 2
            or plan["profile"] != "supersymmetry"
            or plan["input_plan_id"] != input_plan["plan_id"]
            or plan["release_id"] != input_plan["release_id"]
            or plan["asset_sha256"] != input_plan["asset_sha256"]
            or plan["policy_id"] != policy_id
            or plan["source_kind"] != "prior-core-mod-augmentation-tree-plus-verified-artifact-cache"
            or plan["destination_root"] != "mods"
            or plan["curseforge_file_identity_state"] != "unproven-by-local-assertions"
            or plan["installation_state"] != "not-installed"):
        raise ValueError("retained follow-on mod augmentation lock differs from selected release")
    body = {key: value for key, value in plan.items() if key != "plan_id"}
    if _PLAN_PREFIX + sha256(_canonical(body)).hexdigest() != expected_plan_id:
        raise ValueError("retained follow-on mod augmentation plan identity changed")
    prior, prior_plan = _prior_v2(
        input_plan, prior_plan_id=plan["prior_plan_id"], prior_tree_id=plan["prior_tree_id"],
        policy_path=policy_path, prior_augmentation_policy_path=prior_augmentation_policy_path,
        state_root=state_root, config_home=config_home, local_policy=local_policy,
        augmentation_policy=augmentation_policy,
    )
    local_sources = plan["local_sources"]
    if (type(local_sources) is not list or len(local_sources) != 1
            or type(local_sources[0]) is not dict
            or set(local_sources[0]) != {"project_id", "file_id", "filename", "size", "sha1",
                                          "sha256", "source_id", "relative_path", "source_filesystem"}
            or type(local_sources[0]["source_filesystem"]) is not str
            or local_sources[0]["source_filesystem"] not in _SUPPORTED_FILESYSTEMS):
        raise ValueError("retained follow-on mod augmentation has invalid source rows")
    candidate = _candidate_v2(input_plan, prior, prior_plan, local_policy, prior_policy,
                              augmentation_policy, local_sources[0]["source_filesystem"])
    if plan != candidate:
        raise ValueError("retained follow-on mod augmentation rows differ from source identity")
    _reopen(host, reference, target, plan)
    return _result(plan, reference, "reopened")


def apply_mod_augmentation_v2(input_plan: Mapping[str, Any], *, prior_plan_id: str,
                              prior_tree_id: str, artifact_path: Path, policy_path: Path,
                              prior_augmentation_policy_path: Path,
                              augmentation_policy_path: Path, state_root: Path,
                              config_home: Path, expected_plan_id: str) -> dict[str, Any]:
    """Retain 190 exact JARs, preserving and referencing the V1 Core tree."""

    plan = plan_mod_augmentation_v2(
        input_plan, prior_plan_id=prior_plan_id, prior_tree_id=prior_tree_id,
        artifact_path=artifact_path, policy_path=policy_path,
        prior_augmentation_policy_path=prior_augmentation_policy_path,
        augmentation_policy_path=augmentation_policy_path,
        state_root=state_root, config_home=config_home,
    )
    if plan["plan_id"] != expected_plan_id:
        raise ValueError("follow-on mod augmentation changed after review")
    host, root = _host(state_root, config_home, plan["policy_id"])
    target = _target(root, expected_plan_id)
    _private_directory(target.parent)
    if not private_path(target.parent, directory=True):
        raise ValueError("follow-on mod augmentation target parent is not private")
    with private_record_lock(root / (".mod-augmentation-" + expected_plan_id.rsplit(":", 1)[-1] + ".lock"), wait=True):
        current, prior = _review_v2(
            input_plan, prior_plan_id=prior_plan_id, prior_tree_id=prior_tree_id,
            artifact_path=artifact_path, policy_path=policy_path,
            prior_augmentation_policy_path=prior_augmentation_policy_path,
            augmentation_policy_path=augmentation_policy_path,
            state_root=state_root, config_home=config_home,
        )
        action, tree_id = _tree_state(host, target, current)
        if {**current, "action": action, "tree_id": tree_id} != plan:
            raise ValueError("follow-on mod augmentation inputs changed before staging")
        if action == "acquire":
            augmentation_policy = load_augmentation_policy_v2(augmentation_policy_path)
            prior_policy = load_augmentation_policy(prior_augmentation_policy_path)
            local_policy = load_local_input_policy(policy_path)
            if _policy_id_v2(local_policy, prior_policy, augmentation_policy) != plan["policy_id"]:
                raise ValueError("follow-on mod augmentation policy changed before staging")
            with host.stage("artifacts", target.name, requested_path=target) as stage:
                _copy_v2_to_stage(stage.path, prior, artifact_path, augmentation_policy,
                                  plan, state_root)
                reference = stage.publish(
                    validate=lambda path: _validate_stage(path, plan),
                    domain_id=plan["plan_id"], references=(prior_tree_id,),
                    inventory_policy=EXACT_INVENTORY_POLICY,
                )
            outcome = "retained"
        else:
            reference = host.describe(tree_id)
            outcome = "reused"
        _reopen(host, reference, target, plan)
    return _result(plan, reference, outcome)


__all__ = ["POLICY_FORMAT", "PLAN_FORMAT", "LOCK_FORMAT", "RESULT_FORMAT",
           "load_augmentation_policy", "plan_mod_augmentation",
           "apply_mod_augmentation", "reopen_mod_augmentation",
           "POLICY_FORMAT_V2", "PLAN_FORMAT_V2", "LOCK_FORMAT_V2", "RESULT_FORMAT_V2",
           "load_augmentation_policy_v2", "plan_mod_augmentation_v2",
           "apply_mod_augmentation_v2", "reopen_mod_augmentation_v2"]
