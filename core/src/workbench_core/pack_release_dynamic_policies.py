"""Retain release-specific pack policies from one verified official archive.

The archive determines override files and exact external IDs. The user reviews
which required IDs are resource packs, while the pack profile supplies safe
placement and size rules. Core publishes the resulting policy pair together.
"""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from .durable_records import private_record_lock, read_private_single_link_bytes
from .environment_package_install_plan import _SUPPORTED_FILESYSTEMS, _mount_type
from .host_filesystem import private_path
from .managed_trees import CoreManagedTrees
from .output_routing import _private_directory
from .pack_release import load_authority
from .pack_release_client_layout import (
    POLICY_FORMAT as LAYOUT_FORMAT, _archive_cache_path, _scan_selected_archive,
    _selected_policy, _validated_input_plan, load_client_layout_policy,
)
from .pack_release_local import _canonical, _object
from .pack_release_prism_resourcepacks import (
    POLICY_FORMAT as RESOURCEPACK_FORMAT, load_resourcepack_policy,
)
from .storage.exact_tree_inventory import EXACT_INVENTORY_POLICY, inventory_exact_members


PLAN_FORMAT = "workbench-pack-release-derived-policies-plan-v1"
LOCK_FORMAT = "workbench-pack-release-derived-policies-source-lock-v1"
RESULT_FORMAT = "workbench-pack-release-derived-policies-result-v1"
_PLAN_PREFIX = "workbench-pack-release-derived-policies-plan:sha256:"
_PLAN_ID = re.compile(r"workbench-pack-release-derived-policies-plan:sha256:[0-9a-f]{64}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_LOCK_LIMIT = 64 * 1024


def _pairs(value: tuple[tuple[int, int], ...], label: str,
           declared: Mapping[tuple[int, int], bool], *, required: bool) -> tuple[tuple[int, int], ...]:
    if (type(value) is not tuple or any(
            type(pair) is not tuple or len(pair) != 2
            or any(type(item) is not int or not 0 < item < 2**63 for item in pair)
            for pair in value)
            or len(set(value)) != len(value)
            or any(declared.get(pair) is not required for pair in value)):
        raise ValueError(f"{label} must name distinct selected release file IDs")
    return tuple(sorted(value))


def suggest_resourcepack_placements(
    input_plan: Mapping[str, Any], *, baseline_policy_path: Path,
) -> dict[str, Any]:
    """Suggest matching project IDs; Textual must still get user review."""
    _validated_input_plan(input_plan)
    baseline = load_resourcepack_policy(baseline_policy_path)
    declared = input_plan.get("external_files")
    if type(declared) is not list:
        raise ValueError("selected release lacks external file declarations")
    by_project: dict[int, list[tuple[int, int]]] = {}
    for row in declared:
        if (type(row) is not dict or set(row) != {"project_id", "file_id", "required"}
                or type(row["project_id"]) is not int
                or type(row["file_id"]) is not int
                or type(row["required"]) is not bool):
            raise ValueError("selected release has invalid external file declarations")
        if row["required"]:
            by_project.setdefault(row["project_id"], []).append(
                (row["project_id"], row["file_id"]))
    suggested, unresolved = [], []
    for row in baseline["placements"]:
        project_id = row["project_id"]
        matches = by_project.get(project_id, [])
        if len(matches) == 1:
            suggested.append({"project_id": project_id, "file_id": matches[0][1],
                              "destination_root": "resourcepacks"})
        else:
            unresolved.append(project_id)
    return {
        "format": "workbench-pack-release-resourcepack-suggestions-v1",
        "input_plan_id": input_plan["plan_id"],
        "review_state": "requires-user-review",
        "suggested": suggested, "unresolved_project_ids": unresolved,
    }


def _host(state_root: Path, config_home: Path, policy_id: str) -> tuple[CoreManagedTrees, Path]:
    if (not isinstance(state_root, Path) or not state_root.is_absolute()
            or not isinstance(config_home, Path) or not config_home.is_absolute()
            or _mount_type(state_root) not in _SUPPORTED_FILESYSTEMS):
        raise ValueError("release policy custody needs a qualified Linux Core state root")
    root = state_root / "pack-release-derived-policies"
    return CoreManagedTrees(
        workspace=state_root, configuration_home=config_home,
        locations={"artifacts": root}, owner_id="supersymmetry",
        policy_id=policy_id,
        location_sources={"artifacts": "pack-release-derived-policies"},
    ), root


def _target(root: Path, plan_id: str) -> Path:
    return root / plan_id.rsplit(":", 1)[-1] / "snapshot"


def _lock(plan: Mapping[str, Any]) -> bytes:
    body = {key: value for key, value in plan.items() if key not in {"action", "tree_id"}}
    return _canonical({**body, "format": LOCK_FORMAT}) + b"\n"


def _policies(input_plan: Mapping[str, Any], overrides: list[dict[str, Any]], total: int,
              resourcepack_pairs: tuple[tuple[int, int], ...],
              optional_selected: tuple[tuple[int, int], ...],
              baseline_resourcepack: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    declared_rows = input_plan["external_files"]
    declared = {(row["project_id"], row["file_id"]): row["required"]
                for row in declared_rows}
    if len(declared) != len(declared_rows):
        raise ValueError("selected release repeats an external file ID")
    resourcepacks = _pairs(resourcepack_pairs, "resource-pack mapping", declared,
                           required=True)
    optional = _pairs(optional_selected, "optional selection", declared,
                      required=False)
    if not resourcepacks:
        raise ValueError("resource-pack mapping needs at least one reviewed ID")
    if not overrides or len(overrides) != input_plan["override_file_count"] or total <= 0:
        raise ValueError("selected release has no compatible override inventory")
    layout = {
        "format": LAYOUT_FORMAT, "schema_version": 1, "profile": "supersymmetry",
        "input_plan_format": "workbench-pack-release-input-plan-v1",
        "input_plan_id": input_plan["plan_id"],
        "release_id": input_plan["release_id"],
        "version": input_plan["version"],
        "asset_sha256": input_plan["asset_sha256"],
        "manifest_sha256": input_plan["manifest_sha256"],
        "override_source_root": "overrides/", "destination_root": "minecraft-root",
        "override_file_count": len(overrides), "override_total_bytes": total,
        "optional_selected": [{"project_id": project, "file_id": file}
                              for project, file in optional],
        "collision_policy": "reject", "installation_state": "not-installed",
        "runtime_qualification_state": "not-qualified",
    }
    resourcepack = {
        "format": RESOURCEPACK_FORMAT, "schema_version": 1,
        "profile": "supersymmetry",
        "input_plan_format": "workbench-pack-release-input-plan-v1",
        "input_plan_id": input_plan["plan_id"],
        "version": input_plan["version"],
        "manifest_sha256": input_plan["manifest_sha256"],
        "external_file_count": len(declared_rows),
        "allowed_extensions": baseline_resourcepack["allowed_extensions"],
        "max_file_bytes": baseline_resourcepack["max_file_bytes"],
        "max_total_bytes": baseline_resourcepack["max_total_bytes"],
        "placements": [{"project_id": project, "file_id": file,
                        "required": True, "destination_root": "resourcepacks"}
                       for project, file in resourcepacks],
    }
    _selected_policy(input_plan, layout)
    return layout, resourcepack


def _validate_stage(stage: Path, plan: Mapping[str, Any]) -> None:
    expected = {
        "layout-policy.json": _canonical(plan["layout_policy"]) + b"\n",
        "resourcepack-policy.json": _canonical(plan["resourcepack_policy"]) + b"\n",
        "source-lock.json": _lock(plan),
    }
    members, root_mode, file_count, directory_count = inventory_exact_members(stage)
    if (root_mode != 0o700 or file_count != len(expected) or directory_count
            or len(members) != len(expected)):
        raise ValueError("retained release policies have another member inventory")
    for row in members:
        path = row["path"]
        raw = expected.get(path)
        if (row["kind"] != "file" or raw is None or row["mode"] != 0o600
                or row["size"] != len(raw)
                or row["sha256"] != sha256(raw).hexdigest()):
            raise ValueError("retained release policy member changed")
    if (read_private_single_link_bytes(stage / "source-lock.json",
                                       byte_limit=_LOCK_LIMIT) != expected["source-lock.json"]
            or read_private_single_link_bytes(stage / "layout-policy.json",
                                              byte_limit=16 * 1024) != expected["layout-policy.json"]
            or read_private_single_link_bytes(stage / "resourcepack-policy.json",
                                              byte_limit=16 * 1024) != expected["resourcepack-policy.json"]):
        raise ValueError("retained release policy bytes changed")
    if (load_client_layout_policy(stage / "layout-policy.json") != plan["layout_policy"]
            or load_resourcepack_policy(stage / "resourcepack-policy.json")
            != plan["resourcepack_policy"]):
        raise ValueError("retained release policies are incompatible")


def _tree_state(host: CoreManagedTrees, target: Path,
                plan: Mapping[str, Any]) -> tuple[str, str | None]:
    rows = [row for row in host.catalog.trees.inventory() if row["path"] == str(target)]
    if not rows:
        if target.exists() or target.is_symlink():
            raise ValueError("release policy target exists outside Core custody")
        return "acquire", None
    if len(rows) != 1:
        raise ValueError("release policy target has ambiguous Core reservations")
    row = rows[0]
    if (row["owner_id"] != host.owner_id or row["workspace"] != str(host.workspace)
            or row["role"] != "artifacts"):
        raise ValueError("release policy tree belongs to another Core binding")
    if row["status"] != "committed":
        raise ValueError("release policy tree has an incomplete Core stage")
    ref = host.describe(str(row["tree_id"]))
    if (ref.path != target or ref.domain_id != plan["plan_id"]
            or ref.policy_id != plan["policy_id"]
            or ref.owner_id != host.owner_id or ref.workspace != host.workspace
            or ref.references or ref.inventory_policy != EXACT_INVENTORY_POLICY
            or ref.derived_status != "current"):
        raise ValueError("release policy tree changed after Core publication")
    _validate_stage(ref.path, plan)
    return "reuse", ref.tree_id


def plan_release_dynamic_policies(
    input_plan: Mapping[str, Any], *, archive_path: Path, authority_path: Path,
    baseline_resourcepack_policy_path: Path,
    resourcepack_pairs: tuple[tuple[int, int], ...],
    optional_selected: tuple[tuple[int, int], ...] = (),
    state_root: Path, config_home: Path,
    check_cancelled: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Review official ZIP overrides and explicit resource-pack placement."""
    _validated_input_plan(input_plan)
    _archive_cache_path(archive_path, state_root, input_plan)
    authority = load_authority(authority_path)
    baseline_resourcepack = load_resourcepack_policy(baseline_resourcepack_policy_path)
    overrides, total, _ = _scan_selected_archive(
        archive_path, input_plan, authority, check_cancelled=check_cancelled,
    )
    layout, resourcepack = _policies(
        input_plan, overrides, total, resourcepack_pairs, optional_selected,
        baseline_resourcepack,
    )
    pair_digest = sha256(_canonical({"layout": layout, "resourcepack": resourcepack})).hexdigest()
    policy_id = "workbench-pack-release-derived-policies:sha256:" + pair_digest
    body = {
        "format": PLAN_FORMAT, "schema_version": 1, "profile": "supersymmetry",
        "source_kind": "verified-official-release",
        "input_plan_id": input_plan["plan_id"],
        "release_id": input_plan["release_id"],
        "version": input_plan["version"],
        "asset_sha256": input_plan["asset_sha256"],
        "asset_size": input_plan["asset_size"],
        "manifest_sha256": input_plan["manifest_sha256"],
        "override_content_sha256": "sha256:" + sha256(_canonical(overrides)).hexdigest(),
        "baseline_resourcepack_policy_id": "workbench-pack-release-resourcepack-policy:sha256:"
        + sha256(_canonical(baseline_resourcepack)).hexdigest(),
        "policy_id": policy_id,
        "layout_policy": layout, "resourcepack_policy": resourcepack,
    }
    plan = {**body, "plan_id": _PLAN_PREFIX + sha256(_canonical(body)).hexdigest()}
    if len(_lock(plan)) > _LOCK_LIMIT:
        raise ValueError("derived release policies exceed their lock bound")
    host, root = _host(state_root, config_home, policy_id)
    action, tree_id = _tree_state(host, _target(root, plan["plan_id"]), plan)
    return {**plan, "action": action, "tree_id": tree_id}


def apply_release_dynamic_policies(
    input_plan: Mapping[str, Any], *, archive_path: Path, authority_path: Path,
    baseline_resourcepack_policy_path: Path,
    resourcepack_pairs: tuple[tuple[int, int], ...],
    optional_selected: tuple[tuple[int, int], ...] = (),
    state_root: Path, config_home: Path, expected_plan_id: str,
    check_cancelled: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Publish the reviewed policy pair as one exact Core tree."""
    if type(expected_plan_id) is not str or _PLAN_ID.fullmatch(expected_plan_id) is None:
        raise ValueError("select an exact reviewed release policy plan ID")
    inputs = dict(
        archive_path=archive_path, authority_path=authority_path,
        baseline_resourcepack_policy_path=baseline_resourcepack_policy_path,
        resourcepack_pairs=resourcepack_pairs, optional_selected=optional_selected,
        state_root=state_root, config_home=config_home,
        check_cancelled=check_cancelled,
    )
    plan = plan_release_dynamic_policies(input_plan, **inputs)
    if plan["plan_id"] != expected_plan_id:
        raise ValueError("release policy selection changed after review")
    host, root = _host(state_root, config_home, plan["policy_id"])
    target = _target(root, plan["plan_id"])
    _private_directory(target.parent)
    if not private_path(target.parent, directory=True):
        raise ValueError("release policy target parent must be owner-private")
    lock_path = root / (".derived-policy-" + expected_plan_id.rsplit(":", 1)[-1] + ".lock")
    with private_record_lock(lock_path, wait=True):
        current = plan_release_dynamic_policies(input_plan, **inputs)
        if current != plan:
            raise ValueError("release policy inputs changed before Core publication")
        if plan["action"] == "acquire":
            with host.stage("artifacts", target.name, requested_path=target) as stage:
                stage.path.mkdir(mode=0o700)
                for name, raw in {
                    "layout-policy.json": _canonical(plan["layout_policy"]) + b"\n",
                    "resourcepack-policy.json": _canonical(plan["resourcepack_policy"]) + b"\n",
                    "source-lock.json": _lock(plan),
                }.items():
                    descriptor = os.open(
                        stage.path / name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                    )
                    try:
                        with os.fdopen(descriptor, "wb", closefd=False) as output:
                            output.write(raw)
                            output.flush()
                            os.fsync(output.fileno())
                    finally:
                        os.close(descriptor)
                reference = stage.publish(
                    validate=lambda path: _validate_stage(path, plan),
                    domain_id=plan["plan_id"], inventory_policy=EXACT_INVENTORY_POLICY,
                )
            outcome = "retained"
        else:
            reference = host.describe(plan["tree_id"])
            outcome = "reused"
    return {
        "format": RESULT_FORMAT, "schema_version": 1,
        "outcome": outcome, "plan_id": plan["plan_id"],
        "tree_id": reference.tree_id,
        "tree_content_sha256": reference.content_sha256,
        "input_plan_id": plan["input_plan_id"], "release_id": plan["release_id"],
        "version": plan["version"], "asset_sha256": plan["asset_sha256"],
        "layout_policy_path": str(reference.path / "layout-policy.json"),
        "resourcepack_policy_path": str(reference.path / "resourcepack-policy.json"),
        "resourcepack_file_count": len(plan["resourcepack_policy"]["placements"]),
        "override_file_count": plan["layout_policy"]["override_file_count"],
    }


def reopen_release_dynamic_policies(
    *, expected_plan_id: str, state_root: Path, config_home: Path,
) -> dict[str, Any]:
    """Reopen retained policies without the ZIP or current release choice."""
    if type(expected_plan_id) is not str or _PLAN_ID.fullmatch(expected_plan_id) is None:
        raise ValueError("select an exact retained release policy plan ID")
    target = (state_root / "pack-release-derived-policies"
              / expected_plan_id.rsplit(":", 1)[-1] / "snapshot")
    raw = read_private_single_link_bytes(target / "source-lock.json",
                                         byte_limit=_LOCK_LIMIT)
    lock = _object(raw, "retained release policy source lock")
    expected = {"format", "schema_version", "profile", "source_kind",
                "input_plan_id", "release_id", "version", "asset_sha256",
                "asset_size", "manifest_sha256", "override_content_sha256",
                "baseline_resourcepack_policy_id",
                "policy_id", "layout_policy", "resourcepack_policy", "plan_id"}
    if (set(lock) != expected or lock["format"] != LOCK_FORMAT
            or lock["schema_version"] != 1 or lock["profile"] != "supersymmetry"
            or lock["source_kind"] != "verified-official-release"
            or lock["plan_id"] != expected_plan_id
            or type(lock["asset_sha256"]) is not str
            or _SHA256.fullmatch(lock["asset_sha256"]) is None
            or type(lock["override_content_sha256"]) is not str
            or _SHA256.fullmatch(lock["override_content_sha256"]) is None
            or type(lock["baseline_resourcepack_policy_id"]) is not str
            or re.fullmatch(r"workbench-pack-release-resourcepack-policy:sha256:[0-9a-f]{64}",
                            lock["baseline_resourcepack_policy_id"]) is None
            or type(lock["layout_policy"]) is not dict
            or type(lock["resourcepack_policy"]) is not dict):
        raise ValueError("retained release policy identity changed")
    plan = {**lock, "format": PLAN_FORMAT}
    body = {key: value for key, value in plan.items() if key != "plan_id"}
    digest = sha256(_canonical({"layout": plan["layout_policy"],
                                "resourcepack": plan["resourcepack_policy"]})).hexdigest()
    if (raw != _lock(plan)
            or _PLAN_PREFIX + sha256(_canonical(body)).hexdigest() != expected_plan_id
            or plan["policy_id"] != "workbench-pack-release-derived-policies:sha256:" + digest):
        raise ValueError("retained release policy plan hash changed")
    host, root = _host(state_root, config_home, plan["policy_id"])
    if target != _target(root, expected_plan_id):
        raise ValueError("retained release policy path changed")
    action, tree_id = _tree_state(host, target, plan)
    if action != "reuse" or tree_id is None:
        raise ValueError("retained release policies lack a committed Core tree")
    reference = host.describe(tree_id)
    return {
        "format": RESULT_FORMAT, "schema_version": 1,
        "outcome": "reopened", "plan_id": expected_plan_id,
        "tree_id": reference.tree_id,
        "tree_content_sha256": reference.content_sha256,
        "input_plan_id": plan["input_plan_id"], "release_id": plan["release_id"],
        "version": plan["version"], "asset_sha256": plan["asset_sha256"],
        "layout_policy_path": str(reference.path / "layout-policy.json"),
        "resourcepack_policy_path": str(reference.path / "resourcepack-policy.json"),
        "resourcepack_file_count": len(plan["resourcepack_policy"]["placements"]),
        "override_file_count": plan["layout_policy"]["override_file_count"],
    }


__all__ = [
    "PLAN_FORMAT", "LOCK_FORMAT", "RESULT_FORMAT",
    "suggest_resourcepack_placements", "plan_release_dynamic_policies",
    "apply_release_dynamic_policies", "reopen_release_dynamic_policies",
]
