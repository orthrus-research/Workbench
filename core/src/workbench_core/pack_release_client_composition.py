"""Retain one selected release client payload from three exact Core trees.

This is private byte custody, not installation or runtime qualification. The
release ZIP and the former Prism directory are not needed after their owner
trees have been retained and independently reopened.
"""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from workbench_api.managed_trees import ManagedTreeError, ManagedTreeReference

from .durable_files import _directory as pinned_directory
from .durable_records import private_record_lock, read_private_single_link_bytes
from .host_filesystem import private_path
from .managed_trees import CoreManagedTrees
from .pack_release_client_layout import (
    _destinations, _override_policy_id, _retained_override_plan, _selected_policy,
    _validated_input_plan, load_client_layout_policy, reopen_release_override_custody,
)
from .pack_release_local import _canonical, _held_file, _object
from .pack_release_mod_augmentation import (
    _LOCK_LIMIT as MOD_LOCK_LIMIT, LOCK_FORMAT_V2, PLAN_FORMAT_V2 as MOD_PLAN_FORMAT_V2,
    reopen_mod_augmentation_v2,
)
from .pack_release_prism_resourcepacks import (
    load_resourcepack_policy, reopen_prism_resourcepacks,
)
from .environment_package_install_plan import _SUPPORTED_FILESYSTEMS, _mount_type
from .output_routing import _private_directory
from .storage.exact_tree_inventory import EXACT_INVENTORY_POLICY, inventory_exact_members
from .storage.tree_catalog import EXACT_INTENT_KIND


PLAN_FORMAT = "workbench-pack-release-client-composition-plan-v2"
LOCK_FORMAT = "workbench-pack-release-client-composition-source-lock-v2"
RESULT_FORMAT = "workbench-pack-release-client-composition-result-v2"
_PLAN_PREFIX = "workbench-pack-release-client-composition-plan:sha256:"
_PLAN_ID = re.compile(r"workbench-pack-release-client-composition-plan:sha256:[0-9a-f]{64}\Z")
_LOCK_LIMIT = 4 * 1024 * 1024
_CHUNK = 1024 * 1024


def _host(
    state_root: Path, config_home: Path, policy_id: str, *,
    check_cancelled: Callable[[], None] = lambda: None,
) -> tuple[CoreManagedTrees, Path]:
    if (not isinstance(state_root, Path) or not state_root.is_absolute()
            or not isinstance(config_home, Path) or not config_home.is_absolute()
            or _mount_type(state_root) not in _SUPPORTED_FILESYSTEMS):
        raise ValueError("client composition needs a qualified Linux Core state root")
    root = state_root / "pack-release-client-compositions"
    return CoreManagedTrees(
        workspace=state_root, configuration_home=config_home,
        locations={"artifacts": root}, owner_id="supersymmetry",
        policy_id=policy_id, location_sources={"artifacts": "pack-release-client-composition"},
        check_cancelled=check_cancelled,
    ), root


def _target(root: Path, plan_id: str) -> Path:
    return root / plan_id.rsplit(":", 1)[-1] / "snapshot"


def _lock(plan: Mapping[str, Any]) -> bytes:
    body = {key: value for key, value in plan.items() if key not in {"action", "tree_id"}}
    return _canonical({**body, "format": LOCK_FORMAT}) + b"\n"


def _mod_rows(
    path: Path, expected_plan_id: str, input_plan_id: str,
    reopened: Mapping[str, Any],
) -> list[dict[str, Any]]:
    raw = read_private_single_link_bytes(path, byte_limit=MOD_LOCK_LIMIT)
    lock = _object(raw, "reopened mod source lock")
    body = {**lock, "format": MOD_PLAN_FORMAT_V2}
    body.pop("plan_id", None)
    if (lock.get("format") != LOCK_FORMAT_V2 or lock.get("plan_id") != expected_plan_id
            or lock.get("input_plan_id") != input_plan_id
            or lock.get("retained_file_count") != reopened["retained_file_count"]
            or lock.get("retained_total_bytes") != reopened["retained_total_bytes"]
            or lock.get("unresolved") != reopened["unresolved"]
            or raw != _canonical(lock) + b"\n"
            or "workbench-pack-release-mod-augmentation-plan:sha256:"
            + sha256(_canonical(body)).hexdigest() != expected_plan_id
            or type(lock.get("files")) is not list):
        raise ValueError("reopened mod mapping changed after Core validation")
    return lock["files"]


def _candidate(
    input_plan: Mapping[str, Any], *, layout_policy_path: Path,
    resourcepack_policy_path: Path, local_input_policy_path: Path,
    prior_mod_policy_path: Path, mod_policy_path: Path, mod_plan_id: str,
    resourcepack_plan_id: str, override_plan_id: str, state_root: Path,
    config_home: Path, check_cancelled: Callable[[], None],
) -> tuple[dict[str, Any], CoreManagedTrees, dict[str, ManagedTreeReference]]:
    _validated_input_plan(input_plan)
    policy = load_client_layout_policy(layout_policy_path)
    selected_optional = _selected_policy(input_plan, policy)
    policy_id = _override_policy_id(policy)
    host, _ = _host(state_root, config_home, policy_id, check_cancelled=check_cancelled)

    check_cancelled()
    mod = reopen_mod_augmentation_v2(
        input_plan, expected_plan_id=mod_plan_id, policy_path=local_input_policy_path,
        prior_augmentation_policy_path=prior_mod_policy_path,
        augmentation_policy_path=mod_policy_path,
        state_root=state_root, config_home=config_home,
    )
    check_cancelled()
    resourcepacks = reopen_prism_resourcepacks(
        input_plan, expected_plan_id=resourcepack_plan_id,
        policy_path=resourcepack_policy_path, state_root=state_root,
        config_home=config_home,
    )
    check_cancelled()
    overrides = reopen_release_override_custody(
        input_plan, expected_plan_id=override_plan_id,
        layout_policy_path=layout_policy_path, state_root=state_root,
        config_home=config_home,
    )
    check_cancelled()
    results = {"mod": mod, "resourcepacks": resourcepacks, "overrides": overrides}
    selected_ids = {"mod": mod_plan_id, "resourcepacks": resourcepack_plan_id,
                    "overrides": override_plan_id}
    references: dict[str, ManagedTreeReference] = {}
    for name, result in results.items():
        if (result.get("outcome") != "reopened" or result.get("plan_id") != selected_ids[name]
                or result.get("installation_state") != "not-installed"):
            raise ValueError("client composition needs three independently reopened Core trees")
        reference = host.describe(result["tree_id"])
        if (reference.tree_id != result["tree_id"]
                or reference.content_sha256 != result["tree_content_sha256"]
                or reference.workspace != host.workspace
                or reference.owner_id != host.owner_id or reference.role != "artifacts"
                or reference.domain_id != selected_ids[name]
                or reference.inventory_policy != EXACT_INVENTORY_POLICY
                or reference.derived_status != "current"):
            raise ValueError("client composition source belongs to another Core catalog")
        references[name] = reference

    mod_files = _mod_rows(references["mod"].path / "source-lock.json", mod_plan_id,
                          input_plan["plan_id"], mod)
    override_raw = read_private_single_link_bytes(
        references["overrides"].path / "source-lock.json", byte_limit=2 * 1024 * 1024,
    )
    override_lock = _retained_override_plan(input_plan, policy, override_raw, override_plan_id)
    if (override_lock["override_file_count"] != overrides["override_file_count"]
            or override_lock["override_total_bytes"] != overrides["override_total_bytes"]
            or override_lock["override_content_sha256"] != overrides["override_content_sha256"]):
        raise ValueError("reopened override mapping changed after Core validation")
    declarations = input_plan["external_files"]
    declared = {(row["project_id"], row["file_id"]): row["required"] for row in declarations}
    if len(declared) != len(declarations):
        raise ValueError("client composition manifest repeats an external file ID")
    files = _destinations(mod_files, resourcepacks["files"], override_lock["files"],
                          set(), declared, selected_optional)
    if (len(mod_files) != mod["retained_file_count"]
            or len(resourcepacks["files"]) != resourcepacks["retained_file_count"]
            or len(override_lock["files"]) != overrides["override_file_count"]
            or sum(row["size"] for row in mod_files) != mod["retained_total_bytes"]
            or sum(row["size"] for row in resourcepacks["files"])
            != resourcepacks["retained_total_bytes"]):
        raise ValueError("client composition source counts changed after Core validation")
    resourcepack_policy = load_resourcepack_policy(resourcepack_policy_path)
    resourcepack_policy_id = ("workbench-pack-release-resourcepack-policy:sha256:"
                              + sha256(_canonical(resourcepack_policy)).hexdigest())
    blockers = []
    if (mod.get("curseforge_file_identity_state") != "independently-verified"
            or resourcepacks.get("curseforge_file_identity_state") != "independently-verified"):
        blockers.append("external-file-origin-unverified")
    if policy["runtime_qualification_state"] != "qualified":
        blockers.append("runtime-compatibility-unqualified")
    body = {
        "format": PLAN_FORMAT, "schema_version": 2, "profile": "supersymmetry",
        "state": "blocked" if blockers else "reviewed", "custody_state": "reviewed",
        "input_plan_id": input_plan["plan_id"], "release_id": input_plan["release_id"],
        "version": input_plan["version"], "asset_sha256": input_plan["asset_sha256"],
        "asset_size": input_plan["asset_size"],
        "manifest_sha256": input_plan["manifest_sha256"],
        "layout_policy_id": policy_id, "resourcepack_policy_id": resourcepack_policy_id,
        "destination_root": policy["destination_root"],
        "mod": {"plan_id": mod_plan_id, "tree_id": references["mod"].tree_id,
                "tree_content_sha256": references["mod"].content_sha256,
                "file_count": len(mod_files)},
        "resourcepacks": {"plan_id": resourcepack_plan_id,
                          "tree_id": references["resourcepacks"].tree_id,
                          "tree_content_sha256": references["resourcepacks"].content_sha256,
                          "file_count": len(resourcepacks["files"])},
        "overrides": {"plan_id": override_plan_id,
                      "tree_id": references["overrides"].tree_id,
                      "tree_content_sha256": references["overrides"].content_sha256,
                      "file_count": len(override_lock["files"]),
                      "content_sha256": override_lock["override_content_sha256"]},
        "optional_selected": sorted(policy["optional_selected"],
                                    key=lambda row: (row["project_id"], row["file_id"])),
        "file_count": len(files), "total_bytes": sum(row["size"] for row in files),
        "files": files, "blockers": blockers,
        "installation_state": "not-installed",
        "runtime_qualification_state": policy["runtime_qualification_state"],
    }
    plan = {**body, "plan_id": _PLAN_PREFIX + sha256(_canonical(body)).hexdigest()}
    if len(_lock(plan)) > _LOCK_LIMIT:
        raise ValueError("client composition source lock exceeds its bound")
    return plan, host, references


def _validate_stage(
    stage: Path, plan: Mapping[str, Any], *,
    check_cancelled: Callable[[], None] = lambda: None,
) -> None:
    def cancelled() -> bool:
        check_cancelled()
        return False

    members, root_mode, file_count, directory_count = inventory_exact_members(
        stage, cancelled=cancelled,
    )
    lock = _lock(plan)
    expected_files = {"source-lock.json": (len(lock), sha256(lock).hexdigest())}
    expected_directories = {"minecraft-root"}
    for row in plan["files"]:
        relative = "minecraft-root/" + row["relative_path"]
        expected_files[relative] = (row["size"], row["sha256"].removeprefix("sha256:"))
        pieces = relative.split("/")
        expected_directories.update("/".join(pieces[:index]) for index in range(1, len(pieces)))
    if (root_mode != 0o700 or file_count != len(expected_files)
            or directory_count != len(expected_directories)
            or len(members) != file_count + directory_count):
        raise ValueError("retained client composition differs in member count or mode")
    observed_files, observed_directories = set(), set()
    for member in members:
        path = str(member["path"])
        if member["kind"] == "directory":
            if path not in expected_directories or member["mode"] != 0o700:
                raise ValueError("retained client composition has another directory")
            observed_directories.add(path)
        elif member["kind"] == "file":
            if (path not in expected_files or member["mode"] != 0o600
                    or (member["size"], member["sha256"]) != expected_files[path]):
                raise ValueError("retained client composition has another file")
            observed_files.add(path)
        else:
            raise ValueError("retained client composition has an unsupported member")
    if observed_files != set(expected_files) or observed_directories != expected_directories:
        raise ValueError("retained client composition omits an expected member")
    raw = read_private_single_link_bytes(stage / "source-lock.json", byte_limit=_LOCK_LIMIT)
    if raw != lock:
        raise ValueError("retained client composition source lock changed")


def _reopen_tree(
    host: CoreManagedTrees, reference: ManagedTreeReference, target: Path,
    plan: Mapping[str, Any], *, check_cancelled: Callable[[], None] = lambda: None,
) -> None:
    source_ids = tuple(plan[name]["tree_id"] for name in ("mod", "resourcepacks", "overrides"))
    if (reference.path != target or reference.workspace != host.workspace
            or reference.owner_id != host.owner_id or reference.role != "artifacts"
            or reference.policy_id != plan["layout_policy_id"]
            or reference.domain_id != plan["plan_id"] or reference.references != source_ids
            or reference.inventory_policy != EXACT_INVENTORY_POLICY
            or reference.derived_status != "current"):
        raise ValueError("client composition reopened with another Core identity")
    _validate_stage(reference.path, plan, check_cancelled=check_cancelled)


def _tree_state(host: CoreManagedTrees, target: Path,
                plan: Mapping[str, Any], *,
                check_cancelled: Callable[[], None] = lambda: None) -> tuple[str, str | None]:
    rows = [row for row in host.catalog.trees.inventory() if row["path"] == str(target)]
    if not rows:
        if target.exists() or target.is_symlink():
            raise ValueError("client composition target exists outside Core custody")
        return "acquire", None
    if len(rows) != 1:
        raise ValueError("client composition has ambiguous Core reservations")
    row = rows[0]
    if (row["workspace"] != str(host.workspace) or row["owner_id"] != host.owner_id
            or row["role"] != "artifacts"):
        raise ValueError("client composition target belongs to another Core binding")
    if row["status"] != "committed":
        raise ValueError("client composition has an incomplete stage requiring Core review: "
                         + str(row["status"]))
    reference = host.describe(str(row["tree_id"]))
    _reopen_tree(host, reference, target, plan, check_cancelled=check_cancelled)
    return "reuse", reference.tree_id


def plan_release_client_composition(
    input_plan: Mapping[str, Any], *, layout_policy_path: Path,
    resourcepack_policy_path: Path, local_input_policy_path: Path,
    prior_mod_policy_path: Path, mod_policy_path: Path, mod_plan_id: str,
    resourcepack_plan_id: str, override_plan_id: str, state_root: Path,
    config_home: Path, check_cancelled: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Review three retained trees and all selected destinations without the ZIP."""

    plan, host, _ = _candidate(
        input_plan, layout_policy_path=layout_policy_path,
        resourcepack_policy_path=resourcepack_policy_path,
        local_input_policy_path=local_input_policy_path,
        prior_mod_policy_path=prior_mod_policy_path, mod_policy_path=mod_policy_path,
        mod_plan_id=mod_plan_id, resourcepack_plan_id=resourcepack_plan_id,
        override_plan_id=override_plan_id, state_root=state_root, config_home=config_home,
        check_cancelled=check_cancelled,
    )
    _, root = _host(state_root, config_home, plan["layout_policy_id"],
                    check_cancelled=check_cancelled)
    action, tree_id = _tree_state(host, _target(root, plan["plan_id"]), plan,
                                  check_cancelled=check_cancelled)
    return {**plan, "action": action, "tree_id": tree_id}


def _copy_to_stage(
    stage: Path, plan: Mapping[str, Any], references: Mapping[str, ManagedTreeReference], *,
    check_cancelled: Callable[[], None],
) -> None:
    stage.mkdir(mode=0o700)
    payload = stage / "minecraft-root"
    payload.mkdir(mode=0o700)
    source_names = {"mod-core-tree": "mod", "resourcepack-core-tree": "resourcepacks",
                    "release-overrides": "overrides"}
    for row in plan["files"]:
        check_cancelled()
        name = source_names[row["source"]]
        relative = (("overrides/" if name == "overrides" else "") + row["relative_path"])
        source = references[name].path / relative
        destination = payload / row["relative_path"]
        parent = pinned_directory(destination.parent, create=True)
        try:
            flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                     | getattr(os, "O_CLOEXEC", 0))
            output = os.open(destination.name, flags, 0o600, dir_fd=parent)
            with os.fdopen(output, "wb") as sink, _held_file(source, expected_size=row["size"]) as (held, _):
                digest, observed = sha256(), 0
                while block := os.read(held, _CHUNK):
                    check_cancelled()
                    observed += len(block)
                    if observed > row["size"]:
                        raise ValueError("client composition source grew during copy")
                    digest.update(block)
                    sink.write(block)
                sink.flush()
                os.fsync(sink.fileno())
                if observed != row["size"] or "sha256:" + digest.hexdigest() != row["sha256"]:
                    raise ValueError("client composition source differs from reviewed bytes")
        finally:
            os.close(parent)
    parent = pinned_directory(stage, create=False)
    try:
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                 | getattr(os, "O_CLOEXEC", 0))
        output = os.open("source-lock.json", flags, 0o600, dir_fd=parent)
        with os.fdopen(output, "wb") as sink:
            sink.write(_lock(plan))
            sink.flush()
            os.fsync(sink.fileno())
    finally:
        os.close(parent)


def _result(plan: Mapping[str, Any], reference: ManagedTreeReference,
            outcome: str) -> dict[str, Any]:
    return {
        "format": RESULT_FORMAT, "schema_version": 2, "outcome": outcome,
        "plan_id": plan["plan_id"], "tree_id": reference.tree_id,
        "tree_content_sha256": reference.content_sha256,
        "input_plan_id": plan["input_plan_id"], "layout_policy_id": plan["layout_policy_id"],
        "source_tree_ids": [plan[name]["tree_id"] for name in ("mod", "resourcepacks", "overrides")],
        "file_count": plan["file_count"], "total_bytes": plan["total_bytes"],
        "blockers": plan["blockers"], "installation_state": "not-installed",
        "runtime_qualification_state": plan["runtime_qualification_state"],
    }


def apply_release_client_composition(
    input_plan: Mapping[str, Any], *, layout_policy_path: Path,
    resourcepack_policy_path: Path, local_input_policy_path: Path,
    prior_mod_policy_path: Path, mod_policy_path: Path, mod_plan_id: str,
    resourcepack_plan_id: str, override_plan_id: str, state_root: Path,
    config_home: Path, expected_plan_id: str,
    check_cancelled: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Publish a private exact client payload, without installing it."""

    if type(expected_plan_id) is not str or _PLAN_ID.fullmatch(expected_plan_id) is None:
        raise ValueError("select an exact reviewed client composition plan ID")
    inputs = dict(
        layout_policy_path=layout_policy_path, resourcepack_policy_path=resourcepack_policy_path,
        local_input_policy_path=local_input_policy_path,
        prior_mod_policy_path=prior_mod_policy_path, mod_policy_path=mod_policy_path,
        mod_plan_id=mod_plan_id, resourcepack_plan_id=resourcepack_plan_id,
        override_plan_id=override_plan_id, state_root=state_root, config_home=config_home,
        check_cancelled=check_cancelled,
    )
    plan = plan_release_client_composition(input_plan, **inputs)
    if plan["plan_id"] != expected_plan_id:
        raise ValueError("client composition changed after review")
    host, root = _host(state_root, config_home, plan["layout_policy_id"],
                       check_cancelled=check_cancelled)
    target = _target(root, expected_plan_id)
    _private_directory(target.parent)
    if not private_path(target.parent, directory=True):
        raise ValueError("client composition target parent is not private")
    lock_path = root / (".client-composition-" + expected_plan_id.rsplit(":", 1)[-1] + ".lock")
    with private_record_lock(lock_path, wait=True):
        candidate, _, sources = _candidate(input_plan, **inputs)
        action, tree_id = _tree_state(host, target, candidate,
                                      check_cancelled=check_cancelled)
        current = {**candidate, "action": action, "tree_id": tree_id}
        if current != plan:
            raise ValueError("client composition inputs changed before staging")
        if plan["action"] == "acquire":
            source_ids = tuple(plan[name]["tree_id"] for name in ("mod", "resourcepacks", "overrides"))
            with host.stage("artifacts", target.name, requested_path=target) as stage:
                _copy_to_stage(stage.path, plan, sources, check_cancelled=check_cancelled)
                reference = stage.publish(
                    validate=lambda path: _validate_stage(
                        path, plan, check_cancelled=check_cancelled),
                    domain_id=expected_plan_id, references=source_ids,
                    inventory_policy=EXACT_INVENTORY_POLICY,
                )
            outcome = "retained"
        else:
            reference = host.describe(plan["tree_id"])
            outcome = "reused"
        _reopen_tree(host, reference, target, plan, check_cancelled=check_cancelled)
    return _result(plan, reference, outcome)


def reopen_release_client_composition(
    input_plan: Mapping[str, Any], *, layout_policy_path: Path,
    resourcepack_policy_path: Path, local_input_policy_path: Path,
    prior_mod_policy_path: Path, mod_policy_path: Path, mod_plan_id: str,
    resourcepack_plan_id: str, override_plan_id: str, state_root: Path,
    config_home: Path, expected_plan_id: str,
) -> dict[str, Any]:
    """Verify the retained payload and its three source references without the ZIP."""

    if type(expected_plan_id) is not str or _PLAN_ID.fullmatch(expected_plan_id) is None:
        raise ValueError("select an exact reviewed client composition plan ID")
    plan, host, _ = _candidate(
        input_plan, layout_policy_path=layout_policy_path,
        resourcepack_policy_path=resourcepack_policy_path,
        local_input_policy_path=local_input_policy_path,
        prior_mod_policy_path=prior_mod_policy_path, mod_policy_path=mod_policy_path,
        mod_plan_id=mod_plan_id, resourcepack_plan_id=resourcepack_plan_id,
        override_plan_id=override_plan_id, state_root=state_root, config_home=config_home,
        check_cancelled=lambda: None,
    )
    if plan["plan_id"] != expected_plan_id:
        raise ValueError("retained client composition belongs to another plan")
    _, root = _host(state_root, config_home, plan["layout_policy_id"])
    target = _target(root, expected_plan_id)
    selected = host.lookup_target("artifacts", target, domain_id=expected_plan_id)
    if selected.status != "committed":
        raise ValueError("client composition lacks one completed Core tree")
    reference = host.describe(selected.tree_id)
    _reopen_tree(host, reference, target, plan)
    return _result(plan, reference, "reopened")


def reconcile_release_client_composition(
    input_plan: Mapping[str, Any], *, layout_policy_path: Path,
    resourcepack_policy_path: Path, local_input_policy_path: Path,
    prior_mod_policy_path: Path, mod_policy_path: Path, mod_plan_id: str,
    resourcepack_plan_id: str, override_plan_id: str, state_root: Path,
    config_home: Path, expected_plan_id: str,
) -> dict[str, Any]:
    """Complete only an exact prepared Core publication after interruption."""

    if type(expected_plan_id) is not str or _PLAN_ID.fullmatch(expected_plan_id) is None:
        raise ValueError("select an exact reviewed client composition plan ID")
    plan, host, _ = _candidate(
        input_plan, layout_policy_path=layout_policy_path,
        resourcepack_policy_path=resourcepack_policy_path,
        local_input_policy_path=local_input_policy_path,
        prior_mod_policy_path=prior_mod_policy_path, mod_policy_path=mod_policy_path,
        mod_plan_id=mod_plan_id, resourcepack_plan_id=resourcepack_plan_id,
        override_plan_id=override_plan_id, state_root=state_root, config_home=config_home,
        check_cancelled=lambda: None,
    )
    if plan["plan_id"] != expected_plan_id:
        raise ValueError("client composition recovery belongs to another plan")
    _, root = _host(state_root, config_home, plan["layout_policy_id"])
    target = _target(root, expected_plan_id)
    lock_path = root / (".client-composition-" + expected_plan_id.rsplit(":", 1)[-1] + ".lock")
    with private_record_lock(lock_path, wait=True):
        rows = [row for row in host.catalog.trees.inventory() if row["path"] == str(target)]
        if len(rows) != 1:
            raise ValueError("client composition recovery needs one Core reservation")
        row = rows[0]
        if (row["workspace"] != str(host.workspace) or row["owner_id"] != host.owner_id
                or row["role"] != "artifacts"
                or row["status"] not in {"incomplete", "published-uncommitted"}):
            raise ValueError("client composition reservation is not prepared for reconciliation")
        try:
            selected = host.lookup_target("artifacts", target, domain_id=expected_plan_id)
            intent = host.catalog.trees.intent(selected.tree_id)
        except ManagedTreeError as exc:
            raise ValueError("client composition reservation has no validated Core intent") from exc
        source_ids = [plan[name]["tree_id"] for name in ("mod", "resourcepacks", "overrides")]
        if (selected.tree_id != row["tree_id"] or selected.status != row["status"]
                or intent["format"] != EXACT_INTENT_KIND
                or intent["workspace"] != str(host.workspace)
                or intent["owner_id"] != host.owner_id or intent["role"] != "artifacts"
                or intent["policy_id"] != plan["layout_policy_id"]
                or intent["domain_id"] != expected_plan_id
                or intent["references"] != source_ids):
            raise ValueError("client composition intent belongs to another Core binding")
        staged = (target if row["status"] == "published-uncommitted"
                  else target.parent / str(intent["staging"]) / "payload")
        _validate_stage(staged, plan)
        reference = host.reconcile(str(row["tree_id"]))
        _reopen_tree(host, reference, target, plan)
    return _result(plan, reference, "reconciled")


__all__ = [
    "PLAN_FORMAT", "LOCK_FORMAT", "RESULT_FORMAT", "plan_release_client_composition",
    "apply_release_client_composition", "reopen_release_client_composition",
    "reconcile_release_client_composition",
]
