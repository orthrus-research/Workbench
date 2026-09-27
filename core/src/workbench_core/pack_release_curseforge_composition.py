"""Compose authorized release files and official overrides into one Core tree.

The published tree is an immutable installation source. Core's installer may
project it into a launcher instance; this module neither installs nor launches.
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
from .output_routing import _private_directory
from .pack_release_client_composition import _host, _target
from .pack_release_client_layout import (
    _destinations, _override_policy_id, _retained_override_plan,
    _relative, _selected_policy, _validated_input_plan, load_client_layout_policy,
    reopen_release_override_custody,
)
from .pack_release_curseforge import (
    plan_curseforge_acquisition, reopen_curseforge_acquisition,
)
from .pack_release_local import _canonical, _filename, _held_file, _object
from .pack_release_prism_resourcepacks import load_resourcepack_policy
from .storage.exact_tree_inventory import (
    EXACT_INVENTORY_POLICY, MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES,
    inventory_exact_members,
)
from .storage.tree_catalog import EXACT_INTENT_KIND


PLAN_FORMAT = "workbench-pack-release-client-composition-plan-v4"
LOCK_FORMAT = "workbench-pack-release-client-composition-source-lock-v4"
RESULT_FORMAT = "workbench-pack-release-client-composition-result-v4"
_PLAN_PREFIX = "workbench-pack-release-client-composition-plan:sha256:"
_PLAN_ID = re.compile(r"workbench-pack-release-client-composition-plan:sha256:[0-9a-f]{64}\Z")
_INPUT_ID = re.compile(r"workbench-pack-release-input-plan:sha256:[0-9a-f]{64}\Z")
_RELEASE_ID = re.compile(r"(?:profile-release|github-release):sha256:[0-9a-f]{64}\Z")
_ACQUISITION_ID = re.compile(r"workbench-pack-release-curseforge-acquisition-plan:sha256:[0-9a-f]{64}\Z")
_OVERRIDE_ID = re.compile(r"workbench-pack-release-override-custody-plan:sha256:[0-9a-f]{64}\Z")
_POLICY_ID = re.compile(r"workbench-pack-release-client-layout-policy:sha256:[0-9a-f]{64}\Z")
_RESOURCEPACK_ID = re.compile(r"workbench-pack-release-resourcepack-policy:sha256:[0-9a-f]{64}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_LOCK_LIMIT = 4 * 1024 * 1024
_CHUNK = 1024 * 1024


def _lock(plan: Mapping[str, Any]) -> bytes:
    body = {key: value for key, value in plan.items() if key not in {"action", "tree_id"}}
    return _canonical({**body, "format": LOCK_FORMAT}) + b"\n"


def _candidate(
    input_plan: Mapping[str, Any], *, layout_policy_path: Path,
    resourcepack_policy_path: Path, acquisition_plan_id: str, override_plan_id: str,
    optional_selected: tuple[tuple[int, int], ...] | None,
    state_root: Path, config_home: Path, check_cancelled: Callable[[], None],
) -> tuple[dict[str, Any], CoreManagedTrees, dict[str, ManagedTreeReference]]:
    _validated_input_plan(input_plan)
    policy = load_client_layout_policy(layout_policy_path)
    default_optional = _selected_policy(input_plan, policy)
    chosen = (tuple(sorted(default_optional)) if optional_selected is None
              else optional_selected)
    acquisition_plan = plan_curseforge_acquisition(
        input_plan, resourcepack_policy_path=resourcepack_policy_path,
        optional_selected=chosen,
    )
    if acquisition_plan["plan_id"] != acquisition_plan_id:
        raise ValueError("fresh client composition selects another external file plan")
    policy_id = _override_policy_id(policy)
    host, _ = _host(state_root, config_home, policy_id,
                    check_cancelled=check_cancelled)
    check_cancelled()
    acquired = reopen_curseforge_acquisition(
        acquisition_plan, state_root=state_root, config_home=config_home,
        check_cancelled=check_cancelled,
    )
    check_cancelled()
    overrides = reopen_release_override_custody(
        input_plan, expected_plan_id=override_plan_id,
        layout_policy_path=layout_policy_path,
        state_root=state_root, config_home=config_home,
    )
    results = {"acquisition": acquired, "overrides": overrides}
    ids = {"acquisition": acquisition_plan_id, "overrides": override_plan_id}
    references: dict[str, ManagedTreeReference] = {}
    for name, result in results.items():
        if (result.get("plan_id") != ids[name]
                or (name == "overrides" and result.get("outcome") != "reopened")):
            raise ValueError("fresh client composition needs reopened Core sources")
        reference = host.describe(result["tree_id"])
        if (reference.tree_id != result["tree_id"]
                or reference.content_sha256 != result["tree_content_sha256"]
                or reference.workspace != host.workspace
                or reference.owner_id != host.owner_id or reference.role != "artifacts"
                or reference.domain_id != ids[name]
                or reference.inventory_policy != EXACT_INVENTORY_POLICY
                or reference.derived_status != "current"):
            raise ValueError("fresh client source belongs to another Core catalog")
        references[name] = reference
    raw = read_private_single_link_bytes(
        references["overrides"].path / "source-lock.json", byte_limit=2 * 1024 * 1024,
    )
    override_lock = _retained_override_plan(input_plan, policy, raw, override_plan_id)
    if (override_lock["override_file_count"] != overrides["override_file_count"]
            or override_lock["override_total_bytes"] != overrides["override_total_bytes"]
            or override_lock["override_content_sha256"] != overrides["override_content_sha256"]):
        raise ValueError("official override mapping changed after Core review")
    declarations = input_plan["external_files"]
    declared = {(row["project_id"], row["file_id"]): row["required"]
                for row in declarations}
    if len(declared) != len(declarations):
        raise ValueError("fresh client manifest repeats an external file ID")
    mod_files = [row for row in acquired["files"]
                 if row["destination_root"] == "mods"]
    resourcepack_files = [row for row in acquired["files"]
                          if row["destination_root"] == "resourcepacks"]
    files = _destinations(
        mod_files, resourcepack_files, override_lock["files"], set(), declared,
        set(chosen),
    )
    files = [{**row, "source": ("curseforge-core-tree" if row["source"]
                                in {"mod-core-tree", "resourcepack-core-tree"}
                                else row["source"])} for row in files]
    resourcepack_policy = load_resourcepack_policy(resourcepack_policy_path)
    resourcepack_policy_id = ("workbench-pack-release-resourcepack-policy:sha256:"
                              + sha256(_canonical(resourcepack_policy)).hexdigest())
    if resourcepack_policy_id != acquisition_plan["policy_id"]:
        raise ValueError("fresh client resource-pack policy changed")
    blockers = ["runtime-compatibility-unqualified"]
    body = {
        "format": PLAN_FORMAT, "schema_version": 4, "profile": "supersymmetry",
        "source_kind": "official-release-curseforge",
        "state": "blocked", "custody_state": "reviewed",
        "input_plan_id": input_plan["plan_id"],
        "release_id": input_plan["release_id"], "version": input_plan["version"],
        "asset_sha256": input_plan["asset_sha256"],
        "asset_size": input_plan["asset_size"],
        "manifest_sha256": input_plan["manifest_sha256"],
        "layout_policy_id": policy_id,
        "resourcepack_policy_id": resourcepack_policy_id,
        "destination_root": policy["destination_root"],
        "acquisition": {
            "plan_id": acquisition_plan_id,
            "tree_id": references["acquisition"].tree_id,
            "tree_content_sha256": references["acquisition"].content_sha256,
            "file_count": acquired["file_count"],
        },
        "overrides": {
            "plan_id": override_plan_id,
            "tree_id": references["overrides"].tree_id,
            "tree_content_sha256": references["overrides"].content_sha256,
            "file_count": override_lock["override_file_count"],
            "content_sha256": override_lock["override_content_sha256"],
        },
        "optional_selected": [
            {"project_id": project_id, "file_id": file_id}
            for project_id, file_id in sorted(chosen)
        ],
        "file_count": len(files), "total_bytes": sum(row["size"] for row in files),
        "files": files, "blockers": blockers,
        "installation_state": "not-installed",
        "runtime_qualification_state": policy["runtime_qualification_state"],
    }
    plan = {**body, "plan_id": _PLAN_PREFIX + sha256(_canonical(body)).hexdigest()}
    if len(_lock(plan)) > _LOCK_LIMIT:
        raise ValueError("fresh client composition lock exceeds its bound")
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
        expected_directories.update("/".join(pieces[:index])
                                    for index in range(1, len(pieces)))
    if (root_mode != 0o700 or file_count != len(expected_files)
            or directory_count != len(expected_directories)
            or len(members) != file_count + directory_count):
        raise ValueError("fresh client composition differs in member count or mode")
    observed_files, observed_directories = set(), set()
    for member in members:
        path = str(member["path"])
        if member["kind"] == "directory":
            if path not in expected_directories or member["mode"] != 0o700:
                raise ValueError("fresh client composition has another directory")
            observed_directories.add(path)
        elif member["kind"] == "file":
            if (path not in expected_files or member["mode"] != 0o600
                    or (member["size"], member["sha256"]) != expected_files[path]):
                raise ValueError("fresh client composition has another file")
            observed_files.add(path)
        else:
            raise ValueError("fresh client composition has an unsupported member")
    if observed_files != set(expected_files) or observed_directories != expected_directories:
        raise ValueError("fresh client composition omits an expected member")
    raw = read_private_single_link_bytes(stage / "source-lock.json", byte_limit=_LOCK_LIMIT)
    if raw != lock:
        raise ValueError("fresh client composition source lock changed")


def _reopen_tree(
    host: CoreManagedTrees, reference: ManagedTreeReference, target: Path,
    plan: Mapping[str, Any], *, check_cancelled: Callable[[], None] = lambda: None,
) -> None:
    source_ids = tuple(plan[name]["tree_id"] for name in ("acquisition", "overrides"))
    if (reference.path != target or reference.workspace != host.workspace
            or reference.owner_id != host.owner_id or reference.role != "artifacts"
            or reference.policy_id != plan["layout_policy_id"]
            or reference.domain_id != plan["plan_id"] or reference.references != source_ids
            or reference.inventory_policy != EXACT_INVENTORY_POLICY
            or reference.derived_status != "current"):
        raise ValueError("fresh client composition reopened with another Core identity")
    _validate_stage(reference.path, plan, check_cancelled=check_cancelled)


def _tree_state(host: CoreManagedTrees, target: Path,
                plan: Mapping[str, Any], *,
                check_cancelled: Callable[[], None] = lambda: None) -> tuple[str, str | None]:
    rows = [row for row in host.catalog.trees.inventory() if row["path"] == str(target)]
    if not rows:
        if target.exists() or target.is_symlink():
            raise ValueError("fresh client composition target exists outside Core custody")
        return "acquire", None
    if len(rows) != 1:
        raise ValueError("fresh client composition has ambiguous Core reservations")
    row = rows[0]
    if (row["workspace"] != str(host.workspace) or row["owner_id"] != host.owner_id
            or row["role"] != "artifacts"):
        raise ValueError("fresh client composition belongs to another Core binding")
    if row["status"] != "committed":
        raise ValueError("fresh client composition has an incomplete Core stage")
    reference = host.describe(str(row["tree_id"]))
    _reopen_tree(host, reference, target, plan, check_cancelled=check_cancelled)
    return "reuse", reference.tree_id


def plan_curseforge_client_composition(
    input_plan: Mapping[str, Any], *, layout_policy_path: Path,
    resourcepack_policy_path: Path, acquisition_plan_id: str,
    override_plan_id: str, state_root: Path, config_home: Path,
    optional_selected: tuple[tuple[int, int], ...] | None = None,
    check_cancelled: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Review a complete authorized acquisition and official override tree."""
    plan, host, _ = _candidate(
        input_plan, layout_policy_path=layout_policy_path,
        resourcepack_policy_path=resourcepack_policy_path,
        acquisition_plan_id=acquisition_plan_id, override_plan_id=override_plan_id,
        optional_selected=optional_selected, state_root=state_root,
        config_home=config_home, check_cancelled=check_cancelled,
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
    for row in plan["files"]:
        check_cancelled()
        name = "overrides" if row["source"] == "release-overrides" else "acquisition"
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
                        raise ValueError("fresh client source grew during copy")
                    digest.update(block)
                    sink.write(block)
                sink.flush()
                os.fsync(sink.fileno())
                if observed != row["size"] or "sha256:" + digest.hexdigest() != row["sha256"]:
                    raise ValueError("fresh client source differs from reviewed bytes")
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
        "format": RESULT_FORMAT, "schema_version": 4, "outcome": outcome,
        "source_kind": "official-release-curseforge",
        "plan_id": plan["plan_id"], "tree_id": reference.tree_id,
        "tree_content_sha256": reference.content_sha256,
        "input_plan_id": plan["input_plan_id"],
        "layout_policy_id": plan["layout_policy_id"],
        "source_tree_ids": [plan[name]["tree_id"] for name in ("acquisition", "overrides")],
        "version": plan["version"], "asset_sha256": plan["asset_sha256"],
        "file_count": plan["file_count"], "total_bytes": plan["total_bytes"],
        "blockers": plan["blockers"], "installation_state": "not-installed",
        "runtime_qualification_state": plan["runtime_qualification_state"],
    }


def apply_curseforge_client_composition(
    input_plan: Mapping[str, Any], *, layout_policy_path: Path,
    resourcepack_policy_path: Path, acquisition_plan_id: str,
    override_plan_id: str, state_root: Path, config_home: Path,
    expected_plan_id: str, optional_selected: tuple[tuple[int, int], ...] | None = None,
    check_cancelled: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Publish an exact Core installation source, or reuse its verified tree."""
    if type(expected_plan_id) is not str or _PLAN_ID.fullmatch(expected_plan_id) is None:
        raise ValueError("select an exact fresh client composition plan ID")
    inputs = dict(
        layout_policy_path=layout_policy_path,
        resourcepack_policy_path=resourcepack_policy_path,
        acquisition_plan_id=acquisition_plan_id,
        override_plan_id=override_plan_id,
        state_root=state_root, config_home=config_home,
        optional_selected=optional_selected, check_cancelled=check_cancelled,
    )
    plan = plan_curseforge_client_composition(input_plan, **inputs)
    if plan["plan_id"] != expected_plan_id:
        raise ValueError("fresh client composition changed after review")
    host, root = _host(state_root, config_home, plan["layout_policy_id"],
                       check_cancelled=check_cancelled)
    target = _target(root, expected_plan_id)
    _private_directory(target.parent)
    if not private_path(target.parent, directory=True):
        raise ValueError("fresh client composition target parent is not private")
    lock_path = root / (".curseforge-composition-" + expected_plan_id.rsplit(":", 1)[-1] + ".lock")
    with private_record_lock(lock_path, wait=True):
        candidate, _, sources = _candidate(input_plan, **inputs)
        action, tree_id = _tree_state(host, target, candidate,
                                      check_cancelled=check_cancelled)
        current = {**candidate, "action": action, "tree_id": tree_id}
        if current != plan:
            raise ValueError("fresh client composition inputs changed before staging")
        if plan["action"] == "acquire":
            source_ids = tuple(plan[name]["tree_id"] for name in ("acquisition", "overrides"))
            with host.stage("artifacts", target.name, requested_path=target) as stage:
                _copy_to_stage(stage.path, plan, sources, check_cancelled=check_cancelled)
                reference = stage.publish(
                    validate=lambda path: _validate_stage(
                        path, plan, check_cancelled=check_cancelled),
                    domain_id=plan["plan_id"], references=source_ids,
                    inventory_policy=EXACT_INVENTORY_POLICY,
                )
            outcome = "retained"
        else:
            reference = host.describe(tree_id)
            _reopen_tree(host, reference, target, plan, check_cancelled=check_cancelled)
            outcome = "reused"
    return _result(plan, reference, outcome)


def reopen_curseforge_client_composition(
    input_plan: Mapping[str, Any], *, layout_policy_path: Path,
    resourcepack_policy_path: Path, acquisition_plan_id: str,
    override_plan_id: str, state_root: Path, config_home: Path,
    expected_plan_id: str, optional_selected: tuple[tuple[int, int], ...] | None = None,
) -> dict[str, Any]:
    """Reopen the complete official payload and both source references."""
    if type(expected_plan_id) is not str or _PLAN_ID.fullmatch(expected_plan_id) is None:
        raise ValueError("select an exact fresh client composition plan ID")
    plan, host, _ = _candidate(
        input_plan, layout_policy_path=layout_policy_path,
        resourcepack_policy_path=resourcepack_policy_path,
        acquisition_plan_id=acquisition_plan_id, override_plan_id=override_plan_id,
        optional_selected=optional_selected, state_root=state_root,
        config_home=config_home, check_cancelled=lambda: None,
    )
    if plan["plan_id"] != expected_plan_id:
        raise ValueError("fresh client composition belongs to another plan")
    _, root = _host(state_root, config_home, plan["layout_policy_id"])
    target = _target(root, expected_plan_id)
    selected = host.lookup_target("artifacts", target, domain_id=expected_plan_id)
    if selected.status != "committed":
        raise ValueError("fresh client composition lacks one completed Core tree")
    reference = host.describe(selected.tree_id)
    _reopen_tree(host, reference, target, plan)
    return _result(plan, reference, "reopened")


def reconcile_curseforge_client_composition(
    input_plan: Mapping[str, Any], *, layout_policy_path: Path,
    resourcepack_policy_path: Path, acquisition_plan_id: str,
    override_plan_id: str, state_root: Path, config_home: Path,
    expected_plan_id: str, optional_selected: tuple[tuple[int, int], ...] | None = None,
) -> dict[str, Any]:
    """Complete only a prepared exact Core publication after interruption."""
    if type(expected_plan_id) is not str or _PLAN_ID.fullmatch(expected_plan_id) is None:
        raise ValueError("select an exact fresh client composition plan ID")
    plan, host, _ = _candidate(
        input_plan, layout_policy_path=layout_policy_path,
        resourcepack_policy_path=resourcepack_policy_path,
        acquisition_plan_id=acquisition_plan_id, override_plan_id=override_plan_id,
        optional_selected=optional_selected, state_root=state_root,
        config_home=config_home, check_cancelled=lambda: None,
    )
    if plan["plan_id"] != expected_plan_id:
        raise ValueError("fresh client composition recovery belongs to another plan")
    _, root = _host(state_root, config_home, plan["layout_policy_id"])
    target = _target(root, expected_plan_id)
    lock_path = root / (".curseforge-composition-" + expected_plan_id.rsplit(":", 1)[-1] + ".lock")
    with private_record_lock(lock_path, wait=True):
        rows = [row for row in host.catalog.trees.inventory() if row["path"] == str(target)]
        if len(rows) != 1:
            raise ValueError("fresh client composition recovery needs one Core reservation")
        row = rows[0]
        if (row["workspace"] != str(host.workspace) or row["owner_id"] != host.owner_id
                or row["role"] != "artifacts"
                or row["status"] not in {"incomplete", "published-uncommitted"}):
            raise ValueError("fresh client composition reservation is not prepared for reconciliation")
        try:
            selected = host.lookup_target("artifacts", target, domain_id=expected_plan_id)
            intent = host.catalog.trees.intent(selected.tree_id)
        except ManagedTreeError as exc:
            raise ValueError("fresh client composition has no validated Core intent") from exc
        source_ids = [plan[name]["tree_id"] for name in ("acquisition", "overrides")]
        if (selected.tree_id != row["tree_id"] or selected.status != row["status"]
                or intent["format"] != EXACT_INTENT_KIND
                or intent["workspace"] != str(host.workspace)
                or intent["owner_id"] != host.owner_id or intent["role"] != "artifacts"
                or intent["policy_id"] != plan["layout_policy_id"]
                or intent["domain_id"] != expected_plan_id
                or intent["references"] != source_ids):
            raise ValueError("fresh client composition intent belongs to another Core binding")
        staged = (target if row["status"] == "published-uncommitted"
                  else target.parent / str(intent["staging"]) / "payload")
        _validate_stage(staged, plan)
        reference = host.reconcile(str(row["tree_id"]))
        _reopen_tree(host, reference, target, plan)
    return _result(plan, reference, "reconciled")


def _retained_plan(raw: bytes, expected_plan_id: str) -> dict[str, Any]:
    """Recover the exact reviewed V4 mapping from its retained source lock."""
    lock = _object(raw, "fresh client composition source lock")
    expected = {
        "format", "schema_version", "profile", "source_kind", "state",
        "custody_state", "input_plan_id", "release_id", "version",
        "asset_sha256", "asset_size", "manifest_sha256", "layout_policy_id",
        "resourcepack_policy_id", "destination_root", "acquisition", "overrides",
        "optional_selected", "file_count", "total_bytes", "files", "blockers",
        "installation_state", "runtime_qualification_state", "plan_id",
    }
    if (set(lock) != expected or lock["format"] != LOCK_FORMAT
            or lock["schema_version"] != 4 or lock["profile"] != "supersymmetry"
            or lock["source_kind"] != "official-release-curseforge"
            or lock["state"] != "blocked" or lock["custody_state"] != "reviewed"
            or lock["installation_state"] != "not-installed"
            or lock["runtime_qualification_state"] != "not-qualified"
            or lock["blockers"] != ["runtime-compatibility-unqualified"]
            or lock["destination_root"] != "minecraft-root"
            or lock["plan_id"] != expected_plan_id
            or type(lock["input_plan_id"]) is not str
            or _INPUT_ID.fullmatch(lock["input_plan_id"]) is None
            or type(lock["release_id"]) is not str
            or _RELEASE_ID.fullmatch(lock["release_id"]) is None
            or type(lock["version"]) is not str
            or re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", lock["version"]) is None
            or type(lock["asset_sha256"]) is not str
            or _SHA256.fullmatch(lock["asset_sha256"]) is None
            or type(lock["manifest_sha256"]) is not str
            or _SHA256.fullmatch(lock["manifest_sha256"]) is None
            or type(lock["asset_size"]) is not int
            or not 0 < lock["asset_size"] <= 2 * 1024**3
            or type(lock["layout_policy_id"]) is not str
            or _POLICY_ID.fullmatch(lock["layout_policy_id"]) is None
            or type(lock["resourcepack_policy_id"]) is not str
            or _RESOURCEPACK_ID.fullmatch(lock["resourcepack_policy_id"]) is None):
        raise ValueError("retained fresh client composition identity changed")
    plan = {**lock, "format": PLAN_FORMAT}
    body = {key: value for key, value in plan.items() if key != "plan_id"}
    if (_PLAN_PREFIX + sha256(_canonical(body)).hexdigest() != expected_plan_id
            or raw != _lock(plan)):
        raise ValueError("retained fresh client composition plan hash changed")
    acquisition, overrides = plan["acquisition"], plan["overrides"]
    for item, pattern in ((acquisition, _ACQUISITION_ID), (overrides, _OVERRIDE_ID)):
        if (type(item) is not dict or type(item.get("plan_id")) is not str
                or pattern.fullmatch(item["plan_id"]) is None
                or type(item.get("tree_id")) is not str
                or re.fullmatch(r"workbench-tree-v1:[0-9a-f]{32}", item["tree_id"]) is None
                or type(item.get("tree_content_sha256")) is not str
                or _SHA256.fullmatch(item["tree_content_sha256"]) is None
                or type(item.get("file_count")) is not int or item["file_count"] < 1):
            raise ValueError("retained fresh client source reference changed")
    if (set(acquisition) != {"plan_id", "tree_id", "tree_content_sha256", "file_count"}
            or set(overrides) != {"plan_id", "tree_id", "tree_content_sha256",
                                  "file_count", "content_sha256"}
            or type(overrides["content_sha256"]) is not str
            or _SHA256.fullmatch(overrides["content_sha256"]) is None):
        raise ValueError("retained fresh client source reference changed")
    optional = plan["optional_selected"]
    if type(optional) is not list:
        raise ValueError("retained fresh client optional selection changed")
    pairs = []
    for row in optional:
        if (type(row) is not dict or set(row) != {"project_id", "file_id"}
                or type(row["project_id"]) is not int
                or type(row["file_id"]) is not int
                or not 0 < row["project_id"] < 2**63
                or not 0 < row["file_id"] < 2**63):
            raise ValueError("retained fresh client optional selection changed")
        pairs.append((row["project_id"], row["file_id"]))
    if pairs != sorted(set(pairs)):
        raise ValueError("retained fresh client optional selection changed")
    files = plan["files"]
    if (type(files) is not list or not 0 < len(files) <= MAX_FILES
            or type(plan["file_count"]) is not int or plan["file_count"] != len(files)
            or type(plan["total_bytes"]) is not int
            or not 0 < plan["total_bytes"] <= MAX_TOTAL_BYTES):
        raise ValueError("retained fresh client file inventory changed")
    mods, resourcepacks, override_rows = [], [], []
    declared: dict[tuple[int, int], bool] = {}
    for row in files:
        if (type(row) is not dict or type(row.get("relative_path")) is not str
                or _relative(row["relative_path"]) != row["relative_path"]
                or type(row.get("size")) is not int
                or not 0 <= row["size"] <= MAX_FILE_BYTES
                or type(row.get("sha256")) is not str
                or _SHA256.fullmatch(row["sha256"]) is None):
            raise ValueError("retained fresh client has an invalid file row")
        if row.get("source") == "release-overrides":
            if set(row) != {"relative_path", "size", "sha256", "source"}:
                raise ValueError("retained fresh client override row changed")
            override_rows.append(row)
        elif row.get("source") == "curseforge-core-tree":
            if (set(row) != {"relative_path", "size", "sha256", "source",
                            "project_id", "file_id", "required"}
                    or type(row["project_id"]) is not int
                    or type(row["file_id"]) is not int
                    or not 0 < row["project_id"] < 2**63
                    or not 0 < row["file_id"] < 2**63
                    or type(row["required"]) is not bool
                    or row["size"] == 0):
                raise ValueError("retained fresh client external file row changed")
            key = row["project_id"], row["file_id"]
            if key in declared:
                raise ValueError("retained fresh client repeats an external file ID")
            declared[key] = row["required"]
            if row["relative_path"].startswith("mods/"):
                mods.append(row)
            elif row["relative_path"].startswith("resourcepacks/"):
                resourcepacks.append(row)
            else:
                raise ValueError("retained fresh client external placement changed")
        else:
            raise ValueError("retained fresh client file source changed")
    if (len(mods) + len(resourcepacks) != acquisition["file_count"]
            or len(override_rows) != overrides["file_count"]
            or sum(row["size"] for row in files) != plan["total_bytes"]
            or set(pairs) != {key for key, required in declared.items() if not required}):
        raise ValueError("retained fresh client source counts changed")
    expected_files = _destinations(mods, resourcepacks, override_rows, set(),
                                   declared, set(pairs))
    expected_files = [{**row, "source": ("curseforge-core-tree" if row["source"]
                                       in {"mod-core-tree", "resourcepack-core-tree"}
                                       else row["source"])} for row in expected_files]
    if expected_files != files:
        raise ValueError("retained fresh client destinations changed")
    return plan


def _retained_sources(plan: Mapping[str, Any], host: CoreManagedTrees) -> None:
    """Verify both referenced source trees and the maps carried by their locks."""
    expected = {
        "acquisition": ("pack-release-external-inputs", plan["resourcepack_policy_id"]),
        "overrides": ("pack-release-overrides", plan["layout_policy_id"]),
    }
    refs: dict[str, ManagedTreeReference] = {}
    for name, (location, policy_id) in expected.items():
        selected = plan[name]
        reference = host.describe(selected["tree_id"])
        target = (host.workspace / location
                  / selected["plan_id"].rsplit(":", 1)[-1] / "snapshot")
        if (reference.tree_id != selected["tree_id"]
                or reference.content_sha256 != selected["tree_content_sha256"]
                or reference.path != target or reference.workspace != host.workspace
                or reference.owner_id != host.owner_id or reference.role != "artifacts"
                or reference.policy_id != policy_id
                or reference.domain_id != selected["plan_id"]
                or reference.references or reference.inventory_policy != EXACT_INVENTORY_POLICY
                or reference.derived_status != "current"):
            raise ValueError("retained fresh client source reference changed")
        refs[name] = reference
    acquisition_raw = read_private_single_link_bytes(
        refs["acquisition"].path / "source-lock.json", byte_limit=2 * 1024 * 1024,
    )
    acquisition_lock = _object(acquisition_raw, "retained CurseForge acquisition lock")
    if (set(acquisition_lock) != {"format", "schema_version", "plan_id",
                                  "input_plan_id", "release_id", "policy_id",
                                  "source_kind", "files", "file_count", "total_bytes"}
            or acquisition_lock["format"] != "workbench-pack-release-curseforge-source-lock-v1"
            or acquisition_lock["schema_version"] != 1
            or acquisition_lock["plan_id"] != plan["acquisition"]["plan_id"]
            or acquisition_lock["input_plan_id"] != plan["input_plan_id"]
            or acquisition_lock["release_id"] != plan["release_id"]
            or acquisition_lock["policy_id"] != plan["resourcepack_policy_id"]
            or acquisition_lock["source_kind"] != "curseforge-authorized-api"
            or acquisition_raw != _canonical(acquisition_lock) + b"\n"
            or type(acquisition_lock["files"]) is not list
            or len(acquisition_lock["files"]) != plan["acquisition"]["file_count"]):
        raise ValueError("retained CurseForge acquisition lock changed")
    external = {((row["project_id"], row["file_id"])): row
                for row in plan["files"] if row["source"] == "curseforge-core-tree"}
    seen: set[tuple[int, int]] = set()
    for row in acquisition_lock["files"]:
        if (type(row) is not dict
                or set(row) != {"project_id", "file_id", "required", "destination_root",
                                "relative_path", "filename", "size", "sha1", "sha256"}):
            raise ValueError("retained CurseForge acquisition file mapping changed")
        key = row["project_id"], row["file_id"]
        selected = external.get(key)
        if selected is None or key in seen:
            raise ValueError("retained CurseForge acquisition file mapping changed")
        seen.add(key)
        if (row["destination_root"] not in {"mods", "resourcepacks"}
                or row["relative_path"] != selected["relative_path"]
                or row["required"] != selected["required"]
                or row["size"] != selected["size"]
                or row["sha256"] != selected["sha256"]
                or type(row["sha1"]) is not str
                or _SHA1.fullmatch(row["sha1"]) is None
                or _filename(row["filename"], [".zip"] if row["destination_root"]
                             == "resourcepacks" else [".jar", ".zip"])
                != selected["relative_path"].rsplit("/", 1)[-1]):
            raise ValueError("retained CurseForge acquisition file mapping changed")
    if (seen != set(external)
            or acquisition_lock["file_count"] != len(external)
            or acquisition_lock["total_bytes"] != sum(row["size"] for row in external.values())):
        raise ValueError("retained CurseForge acquisition total changed")
    override_raw = read_private_single_link_bytes(
        refs["overrides"].path / "source-lock.json", byte_limit=2 * 1024 * 1024,
    )
    override_lock = _object(override_raw, "retained official override lock")
    override_plan = {**override_lock, "format": "workbench-pack-release-override-custody-plan-v1"}
    override_body = {key: value for key, value in override_plan.items() if key != "plan_id"}
    override_files = [row for row in plan["files"] if row["source"] == "release-overrides"]
    if (override_lock.get("format") != "workbench-pack-release-override-custody-source-lock-v1"
            or override_lock.get("schema_version") != 1
            or override_lock.get("plan_id") != plan["overrides"]["plan_id"]
            or override_lock.get("input_plan_id") != plan["input_plan_id"]
            or override_lock.get("release_id") != plan["release_id"]
            or override_lock.get("version") != plan["version"]
            or override_lock.get("asset_sha256") != plan["asset_sha256"]
            or override_lock.get("asset_size") != plan["asset_size"]
            or override_lock.get("manifest_sha256") != plan["manifest_sha256"]
            or override_lock.get("layout_policy_id") != plan["layout_policy_id"]
            or override_lock.get("files") != override_files
            or override_lock.get("override_file_count") != len(override_files)
            or override_lock.get("override_total_bytes") != sum(row["size"] for row in override_files)
            or override_lock.get("override_content_sha256") != plan["overrides"]["content_sha256"]
            or override_lock.get("override_content_sha256") != "sha256:" + sha256(_canonical(override_files)).hexdigest()
            or override_lock.get("installation_state") != "not-installed"
            or override_lock.get("runtime_qualification_state") != "not-qualified"
            or override_raw != _canonical(override_lock) + b"\n"
            or ("workbench-pack-release-override-custody-plan:sha256:"
                + sha256(_canonical(override_body)).hexdigest()
                != override_lock["plan_id"])):
        raise ValueError("retained official override mapping changed")


def reopen_curseforge_client_composition_by_plan_id(
    *, expected_plan_id: str, state_root: Path, config_home: Path,
) -> dict[str, Any]:
    """Reopen a saved V4 source after the user's current release changes."""
    if type(expected_plan_id) is not str or _PLAN_ID.fullmatch(expected_plan_id) is None:
        raise ValueError("select an exact saved fresh composition plan ID")
    target = (state_root / "pack-release-client-compositions"
              / expected_plan_id.rsplit(":", 1)[-1] / "snapshot")
    raw = read_private_single_link_bytes(target / "source-lock.json",
                                         byte_limit=_LOCK_LIMIT)
    plan = _retained_plan(raw, expected_plan_id)
    host, root = _host(state_root, config_home, plan["layout_policy_id"])
    if target != _target(root, expected_plan_id):
        raise ValueError("saved fresh composition path changed")
    selected = host.lookup_target("artifacts", target, domain_id=expected_plan_id)
    if selected.status != "committed":
        raise ValueError("saved fresh composition is not committed")
    reference = host.describe(selected.tree_id)
    _retained_sources(plan, host)
    _reopen_tree(host, reference, target, plan)
    return _result(plan, reference, "reopened")


__all__ = [
    "PLAN_FORMAT", "LOCK_FORMAT", "RESULT_FORMAT",
    "plan_curseforge_client_composition", "apply_curseforge_client_composition",
    "reopen_curseforge_client_composition", "reconcile_curseforge_client_composition",
    "reopen_curseforge_client_composition_by_plan_id",
]
