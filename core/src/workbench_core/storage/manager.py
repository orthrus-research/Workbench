"""Conservative local storage, disposable runtime, and world management.

The manager intentionally treats discovery and authority as different things.  An
entry being underneath ``.workbench`` makes it observable, not disposable.  Only
an exact inventory item with an eligible/reviewed policy can enter a mutation
plan, and every plan is rebound to a fresh observation before execution.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import shlex
import socket
import stat
from typing import Any, Mapping, Sequence
import uuid

try:
    import grp
    import pwd
except ImportError:  # Native Windows has numeric file identity, not POSIX names.
    grp = pwd = None

from workbench_api.runtime import RuntimeProviderError, runtime_provider
from ..host_filesystem import fsync_directory
from .. import check_lifecycle


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def discover_runtime_template(workspace, profile, configured):
    return runtime_provider(profile["_runtime_provider"]).discover_runtime_template(workspace, profile, configured)


def audit_runtime_template(template, profile):
    return runtime_provider(profile["_runtime_provider"]).audit_runtime_template(template, profile)


def provision_runtime(template, staging, profile):
    return runtime_provider(profile["_runtime_provider"]).provision_runtime(template, staging, profile)


def configure_runtime(staging, *, profile, **parameters):
    return runtime_provider(profile["_runtime_provider"]).configure_runtime(staging, **parameters)


CANONICALIZATION_ID = "workbench-canonical-json-v1"
INVENTORY_FORMAT = "workbench-storage-inventory-v1"
RUNTIME_FORMAT = "workbench-managed-runtime-v1"
SNAPSHOT_FORMAT = "workbench-world-snapshot-v1"
PLAN_FORMAT = "workbench-storage-operation-plan-v1"
RECEIPT_FORMAT = "workbench-storage-operation-receipt-v1"
PRODUCER_ID = "workbench-crucible-runtime-manager-v1"

INVENTORY_PREFIX = "workbench-storage-inventory:sha256:"
ITEM_PREFIX = "workbench-storage-item:sha256:"
COMPONENT_PREFIX = "workbench-storage-component:sha256:"
RUNTIME_PREFIX = "workbench-managed-runtime:sha256:"
SNAPSHOT_PREFIX = "workbench-world-snapshot:sha256:"
PLAN_PREFIX = "workbench-storage-operation-plan:sha256:"
RECEIPT_PREFIX = "workbench-storage-operation-receipt:sha256:"
TRASH_PREFIX = "workbench-storage-trash:sha256:"

RUNTIME_MANIFEST = ".workbench-managed-runtime-v1.json"
SNAPSHOT_MANIFEST = ".workbench-world-snapshot-v1.json"
MAX_METADATA_BYTES = 4 * 1024 * 1024
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
LEVEL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
ITEM_ID_RE = re.compile(r"^workbench-storage-item:sha256:[0-9a-f]{64}$")

DEEP_CATEGORIES = frozenset({"evidence", "iterations"})
CLEANUP_CATEGORIES = frozenset(
    {"build", "cache", "fixtures", "gradle-project-cache", "iterations", "tmp"}
)
KNOWN_CATEGORIES = frozenset(
    {
        "artifacts",
        "blueprints",
        "build",
        "cache",
        "check-attempts",
        "evidence",
        "experiments",
        "fixtures",
        "firecrawl",
        "gradle-project-cache",
        "iterations",
        "jdks",
        "research",
        "retained-external",
        "retained-generated",
        "runs",
        "runtime-manager",
        "staging",
        "targets",
        "templates",
        "tmp",
        "trash",
    }
)


class RuntimeManagerError(RuntimeError):
    """An unsafe, stale, incompatible, or otherwise invalid manager request."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _identity(prefix: str, value: Any) -> str:
    return prefix + _canonical_sha256(value)


def _utc(now: datetime | str | None = None) -> str:
    if now is None:
        value = datetime.now(timezone.utc)
    elif isinstance(now, str):
        raw = now[:-1] + "+00:00" if now.endswith("Z") else now
        try:
            value = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise RuntimeManagerError(f"invalid operation timestamp: {now!r}") from exc
    elif isinstance(now, datetime):
        value = now
    else:
        raise RuntimeManagerError("now must be a datetime, ISO timestamp, or None")
    if value.tzinfo is None:
        raise RuntimeManagerError("operation timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _compact_timestamp(timestamp: str) -> str:
    return re.sub(r"[^0-9]", "", timestamp)[:14]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeManagerError(f"JSON object repeats key {key!r}")
        result[key] = value
    return result


def _read_json(path: Path, context: str, *, bounded: bool = True) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RuntimeManagerError(f"cannot inspect {context} {path}: {exc}") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeManagerError(f"{context} must be a regular non-symlink file: {path}")
    if bounded and metadata.st_size > MAX_METADATA_BYTES:
        raise RuntimeManagerError(
            f"{context} exceeds the {MAX_METADATA_BYTES}-byte metadata limit: {path}"
        )
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except RuntimeManagerError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeManagerError(f"cannot parse {context} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeManagerError(f"{context} must contain a JSON object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _ensure_ledger_directory(storage: Path, name: str) -> Path:
    if name not in {"operations", "trash"}:
        raise RuntimeManagerError(f"unsupported manager ledger directory: {name}")
    _assert_no_symlink_ancestors(storage, storage)
    manager = storage / "runtime-manager"
    directory = manager / name
    _assert_no_symlink_ancestors(manager, storage)
    manager.mkdir(exist_ok=True)
    if not manager.is_dir() or manager.is_symlink():
        raise RuntimeManagerError(f"manager ledger root is unsafe: {manager}")
    _assert_no_symlink_ancestors(directory, storage)
    directory.mkdir(exist_ok=True)
    if not directory.is_dir() or directory.is_symlink():
        raise RuntimeManagerError(f"manager ledger directory is unsafe: {directory}")
    _assert_no_symlink_ancestors(directory, storage)
    return directory


def _workspace(root: Path) -> tuple[Path, Path]:
    expanded = Path(root).expanduser()
    try:
        workspace = expanded.resolve(strict=True)
    except OSError as exc:
        raise RuntimeManagerError(f"Workbench root is unavailable: {expanded}") from exc
    if not workspace.is_dir():
        raise RuntimeManagerError(f"Workbench root is not a directory: {workspace}")
    storage = workspace / ".workbench"
    if storage.is_symlink():
        raise RuntimeManagerError(f"Workbench storage root cannot be a symlink: {storage}")
    if storage.exists() and not storage.is_dir():
        raise RuntimeManagerError(f"Workbench storage root is not a directory: {storage}")
    return workspace, storage


def _relative_to_workspace(path: Path, workspace: Path) -> str:
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError as exc:
        raise RuntimeManagerError(f"path escapes the Workbench root: {path}") from exc


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _assert_no_symlink_ancestors(path: Path, floor: Path) -> None:
    try:
        relative = path.relative_to(floor)
    except ValueError as exc:
        raise RuntimeManagerError(f"path is outside the authorized root: {path}") from exc
    current = floor
    if current.is_symlink():
        raise RuntimeManagerError(f"authorized root cannot be a symlink: {current}")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeManagerError(f"path traverses a symlink: {current}")


def _schema_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "schemas" / f"{name}.schema.json"


def _validate_schema(value: Mapping[str, Any], name: str, context: str) -> None:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - required by repository tooling
        raise RuntimeManagerError("jsonschema is required for manager validation") from exc
    schema = _read_json(_schema_path(name), f"{context} schema", bounded=False)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise RuntimeManagerError(f"invalid {context} at {location}: {error.message}")


def validate_storage_inventory(value: Mapping[str, Any]) -> None:
    _validate_schema(value, "storage-inventory-v1", "storage inventory V1")
    material = dict(value)
    inventory_id = material.pop("inventory_id", None)
    if inventory_id != _identity(INVENTORY_PREFIX, material):
        raise RuntimeManagerError("storage inventory identity does not match its content")

    workspace = _canonical_absolute_path(
        value["workspace_root"], "storage inventory workspace root"
    )
    storage = _canonical_absolute_path(
        value["storage_root"], "storage inventory storage root"
    )
    if storage != workspace / ".workbench":
        raise RuntimeManagerError(
            "storage inventory storage root does not bind its workspace root"
        )

    items = value["items"]
    order = [(item["relative_path"], item["item_id"]) for item in items]
    if order != sorted(order):
        raise RuntimeManagerError(
            "storage inventory items must be ordered by relative path and item ID"
        )
    item_ids = [item["item_id"] for item in items]
    relative_paths = [item["relative_path"] for item in items]
    absolute_paths = [item["path"] for item in items]
    if len(item_ids) != len(set(item_ids)):
        raise RuntimeManagerError("storage inventory item IDs must be unique")
    if len(relative_paths) != len(set(relative_paths)):
        raise RuntimeManagerError("storage inventory item paths must be unique")
    if len(absolute_paths) != len(set(absolute_paths)):
        raise RuntimeManagerError("storage inventory absolute paths must be unique")

    by_id = {item["item_id"]: item for item in items}
    item_paths: dict[str, Path] = {}
    component_ids: set[str] = set()
    component_paths: set[str] = set()
    for item in items:
        item_path = _inventory_bound_path(
            item["relative_path"], workspace, storage, "storage inventory item"
        )
        if item["path"] != str(item_path):
            raise RuntimeManagerError(
                "storage inventory item absolute path does not match its relative path"
            )
        expected_item_id = _identity(
            ITEM_PREFIX,
            {
                "storage_root": str(storage),
                "relative_path": item["relative_path"],
            },
        )
        if item["item_id"] != expected_item_id:
            raise RuntimeManagerError(
                "storage inventory item identity does not bind its storage root and path"
            )
        item_paths[item["item_id"]] = item_path

        size = item["size"]
        if size["exclusive_allocated_bytes"] > size["unique_allocated_bytes"]:
            raise RuntimeManagerError(
                "storage inventory item exclusive size exceeds its unique allocated size"
            )

        components = item["components"]
        component_order = [
            (component["relative_path"], component["component_id"])
            for component in components
        ]
        if component_order != sorted(component_order):
            raise RuntimeManagerError(
                "storage inventory components must be ordered by path and component ID"
            )
        for component in components:
            component_path = _inventory_bound_path(
                component["relative_path"],
                workspace,
                storage,
                "storage inventory component",
            )
            if not _inside(component_path, item_path):
                raise RuntimeManagerError(
                    "storage inventory component escapes its parent item"
                )
            expected_component_id = _identity(
                COMPONENT_PREFIX,
                {
                    "storage_root": str(storage),
                    "relative_path": component["relative_path"],
                },
            )
            if component["component_id"] != expected_component_id:
                raise RuntimeManagerError(
                    "storage inventory component identity does not bind its path"
                )
            if component["component_id"] in component_ids:
                raise RuntimeManagerError(
                    "storage inventory component IDs must be unique"
                )
            if component["relative_path"] in component_paths:
                raise RuntimeManagerError(
                    "storage inventory component paths must be unique"
                )
            component_ids.add(component["component_id"])
            component_paths.add(component["relative_path"])
            if (
                component["logical_bytes"] > size["logical_bytes"]
                or component["unique_allocated_bytes"]
                > size["unique_allocated_bytes"]
                or component["exclusive_allocated_bytes"]
                > component["unique_allocated_bytes"]
                or component["exclusive_allocated_bytes"]
                > size["exclusive_allocated_bytes"]
            ):
                raise RuntimeManagerError(
                    "storage inventory component size exceeds its parent item"
                )

        for problem in item["problems"]:
            if problem["path"] is not None:
                _inventory_bound_path(
                    problem["path"],
                    workspace,
                    storage,
                    "storage inventory problem",
                )

    for item in items:
        parent_id = item["parent_item_id"]
        if parent_id is None:
            continue
        parent = by_id.get(parent_id)
        if parent is None:
            raise RuntimeManagerError(
                "storage inventory parent item does not resolve in the inventory"
            )
        if parent_id == item["item_id"] or not _inside(
            item_paths[item["item_id"]], item_paths[parent_id]
        ) or item_paths[item["item_id"]] == item_paths[parent_id]:
            raise RuntimeManagerError(
                "storage inventory parent item does not contain its child path"
            )

    inbound: dict[str, set[str]] = {item_id: set() for item_id in by_id}
    required_inbound: dict[str, set[str]] = {item_id: set() for item_id in by_id}
    for item in items:
        references = item["references"]
        reference_order = [
            (
                reference["target_path"] or "",
                reference["target_item_id"] or "",
                reference["relation"],
                reference["evidence_path"] or "",
                reference["required"],
            )
            for reference in references
        ]
        if reference_order != sorted(reference_order):
            raise RuntimeManagerError(
                "storage inventory references must have deterministic order"
            )
        reference_material = [_canonical_json(reference) for reference in references]
        if len(reference_material) != len(set(reference_material)):
            raise RuntimeManagerError(
                "storage inventory references must not contain semantic duplicates"
            )
        for reference in references:
            target_id = reference["target_item_id"]
            target_path_value = reference["target_path"]
            target_path = None
            if target_path_value is not None:
                target_path = _inventory_bound_path(
                    target_path_value,
                    workspace,
                    storage,
                    "storage inventory reference target",
                )
            if reference["evidence_path"] is not None:
                _inventory_bound_path(
                    reference["evidence_path"],
                    workspace,
                    storage,
                    "storage inventory reference evidence",
                )

            matching_targets = [
                target_item_id
                for target_item_id, candidate in item_paths.items()
                if target_path is not None and _inside(target_path, candidate)
            ]
            resolved_target_id = max(
                matching_targets,
                key=lambda target_item_id: len(item_paths[target_item_id].parts),
                default=None,
            )
            if target_id is None:
                if resolved_target_id is not None:
                    raise RuntimeManagerError(
                        "storage inventory path-bound reference omits an available target item ID"
                    )
                continue
            if target_id not in by_id:
                raise RuntimeManagerError(
                    "storage inventory reference target does not resolve in the inventory"
                )
            if target_id == item["item_id"]:
                raise RuntimeManagerError(
                    "storage inventory item cannot reference itself"
                )
            if target_path is not None and resolved_target_id != target_id:
                raise RuntimeManagerError(
                    "storage inventory reference target ID does not bind its target path"
                )
            inbound[target_id].add(item["item_id"])
            if reference["required"]:
                required_inbound[target_id].add(item["item_id"])

    for item in items:
        item_id = item["item_id"]
        expected_backlinks = sorted(inbound[item_id])
        if item["deletion"]["referenced_by"] != expected_backlinks:
            raise RuntimeManagerError(
                "storage inventory deletion backlinks do not match its references"
            )
        if required_inbound[item_id] and item["deletion"]["state"] in {"eligible", "review"}:
            raise RuntimeManagerError(
                "storage inventory item with a required inbound reference cannot be eligible"
            )

    totals = value["totals"]
    logical_bytes = sum(item["size"]["logical_bytes"] for item in items)
    exclusive_bytes = sum(
        item["size"]["exclusive_allocated_bytes"] for item in items
    )
    unique_upper_bound = sum(
        item["size"]["unique_allocated_bytes"] for item in items
    )
    unique_lower_bound = max(
        (item["size"]["unique_allocated_bytes"] for item in items), default=0
    )
    expected_problem_count = sum(len(item["problems"]) for item in items)
    expected_totals = {
        "item_count": len(items),
        "logical_bytes": logical_bytes,
        "exclusive_allocated_bytes": exclusive_bytes,
        "eligible_bytes": sum(
            item["size"]["exclusive_allocated_bytes"]
            for item in items
            if item["deletion"]["state"] == "eligible"
        ),
        "review_bytes": sum(
            item["size"]["exclusive_allocated_bytes"]
            for item in items
            if item["deletion"]["state"] == "review"
        ),
        "protected_bytes": sum(
            item["size"]["exclusive_allocated_bytes"]
            for item in items
            if item["deletion"]["state"] == "protected"
        ),
        "active_bytes": sum(
            item["size"]["exclusive_allocated_bytes"]
            for item in items
            if item["deletion"]["state"] == "active"
        ),
        "problem_count": expected_problem_count,
    }
    for key, expected in expected_totals.items():
        if totals[key] != expected:
            raise RuntimeManagerError(
                f"storage inventory totals.{key} does not match its items"
            )
    if not (
        unique_lower_bound
        <= totals["unique_allocated_bytes"]
        <= unique_upper_bound
    ) or totals["unique_allocated_bytes"] < exclusive_bytes:
        raise RuntimeManagerError(
            "storage inventory total unique allocated size is inconsistent with its items"
        )
    if value["scan"]["problem_count"] != expected_problem_count:
        raise RuntimeManagerError(
            "storage inventory scan problem count does not match its items"
        )


def validate_managed_runtime(value: Mapping[str, Any]) -> None:
    _validate_schema(value, "managed-runtime-v1", "managed runtime V1")
    material = dict(value)
    runtime_id = material.pop("runtime_id", None)
    if runtime_id != _identity(RUNTIME_PREFIX, material):
        raise RuntimeManagerError("managed runtime identity does not match its content")

    workspace = _canonical_absolute_path(
        value["workspace_root"], "managed runtime workspace root"
    )
    storage = _canonical_absolute_path(
        value["storage_root"], "managed runtime storage root"
    )
    if storage != workspace / ".workbench":
        raise RuntimeManagerError(
            "managed runtime storage root does not bind its workspace root"
        )
    runtime = _inventory_bound_path(
        value["relative_path"], workspace, storage, "managed runtime"
    )

    _canonical_absolute_path(
        value["producer"]["implementation_path"],
        "managed runtime producer implementation",
    )
    profile_path = _canonical_absolute_path(
        value["profile"]["profile_path"], "managed runtime profile"
    )
    if not _inside(profile_path, workspace):
        raise RuntimeManagerError(
            "managed runtime profile path must stay within its workspace"
        )

    template = _canonical_absolute_path(
        value["source_template"]["path"], "managed runtime source template"
    )
    if not _inside(template, storage) or template == storage:
        raise RuntimeManagerError(
            "managed runtime source template escapes its storage root"
        )
    if _inside(runtime, template) or _inside(template, runtime):
        raise RuntimeManagerError(
            "managed runtime and source template paths must be separate"
        )
    server_relative = value["source_template"]["server_jar"]["relative_path"]
    server_relative_path = _canonical_relative_path(
        server_relative, "managed runtime source server JAR"
    )
    server_jar = template / server_relative_path
    if server_jar == template or not _inside(server_jar, template):
        raise RuntimeManagerError(
            "managed runtime source server JAR escapes its template"
        )

    world = value["world"]
    world_relative = _canonical_relative_path(
        world["relative_path"], "managed runtime world"
    )
    world_path = runtime / world_relative
    if (
        world_path == runtime
        or not _inside(world_path, runtime)
        or world_relative.parts != (world["level_name"],)
    ):
        if world["state"] == "reserved-absent":
            raise RuntimeManagerError(
                "managed runtime reserved-absent world must be the bound direct level-name path"
            )
        raise RuntimeManagerError(
            "managed runtime world must be the bound direct level-name path"
        )

    references = value["references"]
    reference_material = [_canonical_json(reference) for reference in references]
    if len(reference_material) != len(set(reference_material)):
        raise RuntimeManagerError(
            "managed runtime references must not contain semantic duplicates"
        )
    for reference in references:
        if reference["path"] is not None:
            _canonical_absolute_path(
                reference["path"], "managed runtime reference"
            )


def _canonical_absolute_path(value: str, context: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or str(path) != value:
        raise RuntimeManagerError(f"{context} must be one canonical absolute path")
    return path


def _canonical_relative_path(value: str, context: str) -> Path:
    # Relative paths are POSIX wire values on every host. Validate that spelling
    # before converting its components to the host-native filesystem path.
    if type(value) is not str or not value or any(character in value for character in ("\\", ":", "\x00")):
        raise RuntimeManagerError(f"{context} must be one canonical relative path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or relative.as_posix() != value or value == "." or ".." in relative.parts:
        raise RuntimeManagerError(f"{context} must be one canonical relative path")
    return Path(*relative.parts)


def _inventory_bound_path(
    value: str,
    workspace: Path,
    storage: Path,
    context: str,
) -> Path:
    relative = _canonical_relative_path(value, context)
    path = workspace / relative
    if path == storage or not _inside(path, storage):
        raise RuntimeManagerError(f"{context} escapes the declared storage root")
    return path


def validate_world_snapshot(value: Mapping[str, Any]) -> None:
    _validate_schema(value, "world-snapshot-v1", "world snapshot V1")
    material = dict(value)
    snapshot_id = material.pop("snapshot_id", None)
    if snapshot_id != _identity(SNAPSHOT_PREFIX, material):
        raise RuntimeManagerError("world snapshot identity does not match its content")
    files = value["files"]
    paths = [row["relative_path"] for row in files]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise RuntimeManagerError("world snapshot file paths must be unique and sorted")
    if value["content"]["tree_sha256"] != _canonical_sha256(files):
        raise RuntimeManagerError("world snapshot tree identity does not match its files")
    if value["content"]["file_count"] != len(files):
        raise RuntimeManagerError("world snapshot file count does not match its files")
    if value["content"]["logical_bytes"] != sum(
        row["size_bytes"] for row in files
    ):
        raise RuntimeManagerError("world snapshot logical size does not match its files")
    if value["content"]["unique_allocated_bytes"] != sum(
        row["allocated_bytes"] for row in files
    ):
        raise RuntimeManagerError("world snapshot allocated size does not match its files")
    if not any(path in {"level.dat", "level.dat_old"} for path in paths):
        raise RuntimeManagerError("world snapshot lacks a root level.dat record")
    consistency = value["consistency"]
    source = value["source"]
    if not (
        consistency["source_observation_before_sha256"]
        == consistency["source_observation_after_sha256"]
        == source["world_observation_sha256"]
    ):
        raise RuntimeManagerError("world snapshot source observations are inconsistent")
    workspace = Path(value["workspace_root"])
    storage = Path(value["storage_root"])
    snapshot_root = workspace / value["relative_path"]
    payload = workspace / value["content"]["payload_relative_path"]
    if storage != workspace / ".workbench" or not _inside(payload, snapshot_root):
        raise RuntimeManagerError("world snapshot payload escapes its declared snapshot root")


def validate_operation_plan(value: Mapping[str, Any]) -> None:
    _validate_schema(value, "storage-operation-plan-v1", "storage operation plan V1")
    material = dict(value)
    plan_id = material.pop("plan_id", None)
    if plan_id != _identity(PLAN_PREFIX, material):
        raise RuntimeManagerError("storage operation plan identity does not match its content")
    input_ids: set[str] = set()
    for row in value["inputs"]:
        input_material = dict(row)
        input_id = input_material.pop("input_id")
        if input_id != _identity("workbench-storage-input:sha256:", input_material):
            raise RuntimeManagerError("storage operation input identity does not match its content")
        if input_id in input_ids:
            raise RuntimeManagerError("storage operation input IDs are not unique")
        input_ids.add(input_id)
    action_ids: set[str] = set()
    for row in value["actions"]:
        action_material = dict(row)
        action_id = action_material.pop("action_id")
        if action_id != _identity("workbench-storage-action:sha256:", action_material):
            raise RuntimeManagerError("storage operation action identity does not match its content")
        if action_id in action_ids:
            raise RuntimeManagerError("storage operation action IDs are not unique")
        action_ids.add(action_id)
    _validate_plan_semantics(value)


def _validate_plan_semantics(value: Mapping[str, Any]) -> None:
    operation = value["operation"]
    expected_roles = {
        "runtime-create": ["profile", "runtime-template"],
        "world-snapshot": ["profile", "managed-runtime", "world"],
        "world-restore": ["profile", "world-snapshot", "runtime-template"],
        "cleanup": ["storage-item"],
        "restore-trash": ["trash"],
        "purge-trash": ["trash"],
    }
    expected_actions = {
        "runtime-create": [
            "audit-source",
            "copy-independent",
            "reserve-fresh-world",
            "write-manifest",
        ],
        "world-snapshot": ["audit-source", "copy-independent", "write-manifest"],
        "world-restore": [
            "audit-source",
            "copy-independent",
            "reserve-fresh-world",
            "copy-independent",
            "write-manifest",
        ],
        "cleanup": ["move-to-trash"],
        "restore-trash": ["restore-from-trash"],
        "purge-trash": ["purge-trash"],
    }
    roles = [row["role"] for row in value["inputs"]]
    kinds = [row["kind"] for row in value["actions"]]
    if roles != expected_roles[operation]:
        raise RuntimeManagerError(
            f"{operation} plan inputs do not match the required ordered roles"
        )
    if kinds != expected_actions[operation]:
        raise RuntimeManagerError(
            f"{operation} plan actions do not match the required ordered lifecycle"
        )
    if (value["status"] == "ready") != (not value["blockers"]):
        raise RuntimeManagerError("operation plan readiness does not match its blockers")

    workspace = Path(value["workspace_root"])
    storage = Path(value["storage_root"])
    if storage != workspace / ".workbench":
        raise RuntimeManagerError("operation plan storage root does not bind its workspace")

    def check_endpoint(endpoint: Mapping[str, Any] | None, context: str) -> None:
        if endpoint is None:
            return
        path = endpoint.get("path")
        relative = endpoint.get("relative_path")
        if isinstance(relative, str):
            expected = workspace / relative
            if not isinstance(path, str) or Path(path) != expected:
                raise RuntimeManagerError(
                    f"{context} absolute path does not match its relative path"
                )
            if expected in {workspace, storage} or not _inside(expected, storage):
                raise RuntimeManagerError(f"{context} is not one bounded storage path")

    for index, row in enumerate(value["inputs"]):
        check_endpoint(row, f"input {index}")
    for index, row in enumerate(value["actions"]):
        check_endpoint(row["source"], f"action {index} source")
        check_endpoint(row["destination"], f"action {index} destination")

    effects = value["effects"]
    inputs_by_role = {row["role"]: row for row in value["inputs"]}
    endpoint_fields = (
        "item_id",
        "resource_id",
        "path",
        "relative_path",
        "observation_sha256",
    )

    def input_endpoint(role: str) -> dict[str, Any]:
        row = inputs_by_role[role]
        return {key: row[key] for key in endpoint_fields}

    def target_endpoint(
        relative: str,
        *,
        item_id: str | None = None,
        resource_id: str | None = None,
        observation_sha256: str | None = None,
    ) -> dict[str, Any]:
        return {
            "item_id": item_id,
            "resource_id": resource_id,
            "path": str(workspace / relative),
            "relative_path": relative,
            "observation_sha256": observation_sha256,
        }

    def require_endpoint(
        actual: Mapping[str, Any] | None,
        expected: Mapping[str, Any] | None,
        context: str,
    ) -> None:
        if actual != expected:
            raise RuntimeManagerError(
                f"{operation} {context} does not match its bound input or effect"
            )

    item_roles = {
        "runtime-template",
        "managed-runtime",
        "world-snapshot",
        "storage-item",
        "trash",
    }
    for row in value["inputs"]:
        if row["role"] not in item_roles:
            continue
        expected_item_id = _identity(
            ITEM_PREFIX,
            {
                "storage_root": str(storage),
                "relative_path": row["relative_path"],
            },
        )
        if row["item_id"] != expected_item_id:
            raise RuntimeManagerError(
                f"{operation} {row['role']} input item ID does not bind its path"
            )

    actions = value["actions"]
    expected_recovery = {
        "runtime-create": "new-destination-only",
        "world-snapshot": "new-destination-only",
        "world-restore": "new-destination-only",
        "cleanup": "restore-trash",
        "restore-trash": "not-needed",
        "purge-trash": "irreversible",
    }
    if value["recovery"]["state"] != expected_recovery[operation]:
        raise RuntimeManagerError(f"{operation} plan has an invalid recovery state")
    expected_recoverable = operation != "purge-trash"
    if any(action["recoverable"] != expected_recoverable for action in actions):
        raise RuntimeManagerError(
            f"{operation} action recoverability does not match its operation"
        )

    if operation == "runtime-create":
        if len(effects["creates"]) != 1:
            raise RuntimeManagerError("runtime-create must create one runtime root")
        source = input_endpoint("runtime-template")
        target = target_endpoint(effects["creates"][0])
        expected_endpoints = (
            (source, None),
            (source, target),
            (None, target),
            (None, target),
        )
        if effects["retains"] != [inputs_by_role["runtime-template"]["relative_path"]]:
            raise RuntimeManagerError("runtime-create retain effect does not bind its template")
    elif operation == "world-snapshot":
        if len(effects["creates"]) != 2 or effects["creates"][1] != (
            effects["creates"][0] + "/world"
        ):
            raise RuntimeManagerError("world-snapshot create effects do not bind its payload")
        source = input_endpoint("world")
        target = target_endpoint(effects["creates"][0])
        expected_endpoints = ((source, None), (source, target), (None, target))
        if effects["retains"] != [inputs_by_role["managed-runtime"]["relative_path"]]:
            raise RuntimeManagerError("world-snapshot retain effect does not bind its runtime")
    elif operation == "world-restore":
        level_name = value["parameters"]["level_name"]
        if len(effects["creates"]) != 2 or effects["creates"][1] != (
            effects["creates"][0] + "/" + level_name
        ):
            raise RuntimeManagerError("world-restore create effects do not bind its world")
        snapshot_source = input_endpoint("world-snapshot")
        template_source = input_endpoint("runtime-template")
        target = target_endpoint(effects["creates"][0])
        expected_endpoints = (
            (snapshot_source, None),
            (template_source, target),
            (None, target),
            (snapshot_source, target),
            (None, target),
        )
        if effects["retains"] != [
            inputs_by_role["world-snapshot"]["relative_path"],
            inputs_by_role["runtime-template"]["relative_path"],
        ]:
            raise RuntimeManagerError("world-restore retain effects do not bind its inputs")
    elif operation == "cleanup":
        destination = actions[0]["destination"]
        if not destination["resource_id"].startswith(TRASH_PREFIX):
            raise RuntimeManagerError("cleanup destination lacks a manager trash ID")
        expected_endpoints = (
            (
                input_endpoint("storage-item"),
                target_endpoint(
                    destination["relative_path"],
                    resource_id=destination["resource_id"],
                ),
            ),
        )
        if effects["creates"] or effects["retains"] != [
            destination["relative_path"]
        ]:
            raise RuntimeManagerError(
                "cleanup effects must retain only its exact quarantine destination"
            )
    elif operation == "restore-trash":
        destination = actions[0]["destination"]
        expected_endpoints = (
            (
                input_endpoint("trash"),
                target_endpoint(
                    destination["relative_path"],
                    item_id=destination["item_id"],
                    resource_id=destination["resource_id"],
                    observation_sha256=destination["observation_sha256"],
                ),
            ),
        )
        if effects["creates"] or effects["retains"] != [
            destination["relative_path"]
        ]:
            raise RuntimeManagerError(
                "restore-trash effects must retain only its exact original destination"
            )
    else:
        expected_endpoints = ((input_endpoint("trash"), None),)
        if effects["creates"] or effects["retains"]:
            raise RuntimeManagerError(
                "purge-trash cannot create or retain an output path"
            )

    for index, (expected_source, expected_destination) in enumerate(expected_endpoints):
        require_endpoint(actions[index]["source"], expected_source, f"action {index} source")
        require_endpoint(
            actions[index]["destination"],
            expected_destination,
            f"action {index} destination",
        )

    for group in ("creates", "removes", "retains"):
        if len(effects[group]) != len(set(effects[group])):
            raise RuntimeManagerError(f"operation plan effects.{group} contains duplicates")
    if operation in {"cleanup", "restore-trash"}:
        action = value["actions"][0]
        expected_move = {
            "source": action["source"]["relative_path"],
            "destination": action["destination"]["relative_path"],
        }
        if effects["moves"] != [expected_move]:
            raise RuntimeManagerError(f"{operation} effects do not match its exact move")
        if effects["expected_reclaimed_bytes"] != 0:
            raise RuntimeManagerError(f"{operation} cannot claim reclaimed bytes")
    elif effects["moves"]:
        raise RuntimeManagerError(f"{operation} cannot declare move effects")
    if operation == "purge-trash":
        source = value["actions"][0]["source"]["relative_path"]
        if effects["removes"] != [source]:
            raise RuntimeManagerError("purge effects do not match the exact trash source")
        matches = (
            value["parameters"]["confirmation"]
            == value["recovery"]["confirmation_token"]
        )
        if value["status"] == "ready" and not matches:
            raise RuntimeManagerError("ready purge plan lacks exact confirmation")
    elif effects["expected_reclaimed_bytes"] != 0:
        raise RuntimeManagerError("only purge may predict reclaimed bytes")
    if operation != "purge-trash" and effects["removes"]:
        raise RuntimeManagerError(f"{operation} cannot declare removal effects")


def validate_operation_receipt(
    value: Mapping[str, Any], *, plan: Mapping[str, Any] | None = None
) -> None:
    _validate_schema(
        value, "storage-operation-receipt-v1", "storage operation receipt V1"
    )
    material = dict(value)
    receipt_id = material.pop("receipt_id", None)
    if receipt_id != _identity(RECEIPT_PREFIX, material):
        raise RuntimeManagerError("storage operation receipt identity does not match its content")
    action_ids = [row["action_id"] for row in value["actions"]]
    if len(action_ids) != len(set(action_ids)):
        raise RuntimeManagerError("storage operation receipt action IDs are not unique")
    workspace = _canonical_absolute_path(
        value["workspace_root"], "storage operation receipt workspace root"
    )
    storage = _canonical_absolute_path(
        value["storage_root"], "storage operation receipt storage root"
    )
    if storage != workspace / ".workbench":
        raise RuntimeManagerError(
            "storage operation receipt storage root does not bind its workspace"
        )

    def parsed_timestamp(raw: str, context: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
        except (TypeError, ValueError) as exc:
            raise RuntimeManagerError(f"{context} is not a valid UTC timestamp") from exc
        return parsed

    started_at = parsed_timestamp(value["started_at"], "receipt start time")
    completed_at = (
        None
        if value["completed_at"] is None
        else parsed_timestamp(value["completed_at"], "receipt completion time")
    )
    if completed_at is not None and completed_at < started_at:
        raise RuntimeManagerError("storage operation receipt completes before it starts")

    statuses = [row["status"] for row in value["actions"]]
    for row in value["actions"]:
        action_started = (
            None
            if row["started_at"] is None
            else parsed_timestamp(row["started_at"], "receipt action start time")
        )
        action_completed = (
            None
            if row["completed_at"] is None
            else parsed_timestamp(row["completed_at"], "receipt action completion time")
        )
        if action_started is not None and action_started < started_at:
            raise RuntimeManagerError("receipt action starts before its operation")
        if (
            action_started is not None
            and action_completed is not None
            and action_completed < action_started
        ):
            raise RuntimeManagerError("receipt action completes before it starts")
        if completed_at is not None and action_completed is not None and action_completed > completed_at:
            raise RuntimeManagerError("receipt action completes after its operation")

    if value["status"] == "running":
        prefix = 0
        while prefix < len(statuses) and statuses[prefix] == "complete":
            prefix += 1
        remainder = statuses[prefix:]
        if remainder and remainder[0] == "running":
            remainder = remainder[1:]
        if any(status != "pending" for status in remainder):
            raise RuntimeManagerError(
                "running receipt actions do not form an ordered lifecycle"
            )
    elif value["status"] == "failed":
        if statuses.count("failed") != 1:
            raise RuntimeManagerError("failed receipt must have exactly one failed action")
        failed_index = statuses.index("failed")
        if statuses[:failed_index] != ["complete"] * failed_index or statuses[
            failed_index + 1 :
        ] != ["pending"] * (len(statuses) - failed_index - 1):
            raise RuntimeManagerError(
                "failed receipt actions do not form an ordered lifecycle"
            )
        if value["error"]["action_id"] != action_ids[failed_index]:
            raise RuntimeManagerError("failed receipt error does not name its failed action")
    elif any(status != "complete" for status in statuses):
        raise RuntimeManagerError("completed receipt contains an unfinished action")

    result = value["result"]
    if result["resource_ids"] != sorted(set(result["resource_ids"])):
        raise RuntimeManagerError("receipt resource IDs must be unique and sorted")
    if result["output_paths"] != sorted(set(result["output_paths"])):
        raise RuntimeManagerError("receipt output paths must be unique and sorted")
    for relative in result["output_paths"]:
        output = workspace / _canonical_relative_path(
            relative, "storage operation receipt output"
        )
        if output == storage or not _inside(output, storage):
            raise RuntimeManagerError("storage operation receipt output escapes storage")

    if plan is not None:
        validate_operation_plan(plan)
        if (
            plan["status"] != "ready"
            or
            value["plan_id"] != plan["plan_id"]
            or value["operation"] != plan["operation"]
            or value["workspace_root"] != plan["workspace_root"]
            or value["storage_root"] != plan["storage_root"]
            or action_ids != [row["action_id"] for row in plan["actions"]]
            or [row["kind"] for row in value["actions"]]
            != [row["kind"] for row in plan["actions"]]
            or [row["source_relative_path"] for row in value["actions"]]
            != [
                None if row["source"] is None else row["source"]["relative_path"]
                for row in plan["actions"]
            ]
            or [row["destination_relative_path"] for row in value["actions"]]
            != [
                None
                if row["destination"] is None
                else row["destination"]["relative_path"]
                for row in plan["actions"]
            ]
        ):
            raise RuntimeManagerError("storage operation receipt does not match its plan")
        if value["status"] == "complete":
            receipt_path = (
                ".workbench/runtime-manager/operations/"
                + value["plan_id"].split(":")[-1]
                + ".json"
            )
            required_outputs = set(plan["effects"]["creates"])
            if value["operation"] in {"cleanup", "restore-trash"}:
                required_outputs.add(plan["effects"]["moves"][0]["destination"])
            required_outputs.add(receipt_path)
            if not required_outputs.issubset(result["output_paths"]):
                raise RuntimeManagerError(
                    "completed receipt does not retain its planned outputs"
                )
    if value["status"] == "complete":
        operation = value["operation"]
        resource_ids = result["resource_ids"]
        if operation in {"runtime-create", "world-restore"} and (
            len(resource_ids) != 1
            or not resource_ids[0].startswith(RUNTIME_PREFIX)
        ):
            raise RuntimeManagerError("completed runtime operation lacks one runtime result")
        if operation == "world-snapshot" and (
            len(resource_ids) != 1
            or not resource_ids[0].startswith(SNAPSHOT_PREFIX)
        ):
            raise RuntimeManagerError("completed snapshot operation lacks one snapshot result")
        if operation == "cleanup" and (
            result["trash_id"] is None or result["purged_bytes"] != 0
        ):
            raise RuntimeManagerError("completed cleanup lacks recoverable trash custody")
        if operation == "restore-trash" and result["restored_item_id"] is None:
            raise RuntimeManagerError("completed trash restore lacks its restored item ID")
        if operation == "cleanup" and plan is not None and (
            result["trash_id"] != plan["actions"][0]["destination"]["resource_id"]
        ):
            raise RuntimeManagerError("completed cleanup result does not match its trash action")
        if operation == "restore-trash" and plan is not None and (
            result["restored_item_id"] != plan["actions"][0]["destination"]["item_id"]
        ):
            raise RuntimeManagerError("completed restore result does not match its action")
        if operation != "cleanup" and result["trash_id"] is not None:
            raise RuntimeManagerError("only cleanup may emit a new trash ID")
        if operation != "restore-trash" and result["restored_item_id"] is not None:
            raise RuntimeManagerError("only trash restore may emit a restored item ID")
        if operation != "purge-trash" and result["purged_bytes"] != 0:
            raise RuntimeManagerError("only purge may report permanently removed bytes")
        if operation not in {"runtime-create", "world-snapshot", "world-restore"} and (
            resource_ids
        ):
            raise RuntimeManagerError(
                "only creation operations may emit managed resource IDs"
            )


def _problem(code: str, detail: str, path: str | None) -> dict[str, Any]:
    return {"code": code, "detail": detail.replace("\n", " "), "path": path}


def _kind_for_path(relative: str) -> str:
    parts = Path(relative).parts
    category = parts[1] if len(parts) > 1 else ""
    name = parts[-1].lower() if parts else ""
    suffix = Path(name).suffix
    category_kinds = {
        "artifacts": "artifact",
        "build": "build-output",
        "cache": "cache",
        "evidence": "evidence",
        "experiments": "experiment",
        "fixtures": "fixture",
        "gradle-project-cache": "gradle-cache",
        "iterations": "iteration",
        "jdks": "jdk",
        "research": "research",
        "runs": "runtime-template",
        "staging": "staging",
        "targets": "target",
        "templates": "runtime-template",
        "tmp": "temporary",
        "trash": "trash",
    }
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return "screenshot"
    if suffix in {".jfr", ".strataview"}:
        return "capture"
    if suffix == ".log":
        return "log"
    if "overlay" in name:
        return "overlay"
    return category_kinds.get(category, "unknown")


def _candidate_paths(storage: Path) -> list[Path]:
    if not storage.exists():
        return []
    candidates: list[Path] = []
    try:
        top_entries = sorted(storage.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise RuntimeManagerError(f"cannot enumerate Workbench storage: {exc}") from exc
    for top in top_entries:
        try:
            top_stat = top.lstat()
        except OSError:
            candidates.append(top)
            continue
        if not stat.S_ISDIR(top_stat.st_mode) or top.is_symlink():
            candidates.append(top)
            continue
        try:
            children = sorted(top.iterdir(), key=lambda item: item.name)
        except OSError:
            candidates.append(top)
            continue
        if top.name not in DEEP_CATEGORIES:
            candidates.extend(children)
            continue
        for group in children:
            try:
                group_stat = group.lstat()
            except OSError:
                candidates.append(group)
                continue
            if not stat.S_ISDIR(group_stat.st_mode) or group.is_symlink():
                candidates.append(group)
                continue
            try:
                grandchildren = sorted(group.iterdir(), key=lambda item: item.name)
            except OSError:
                candidates.append(group)
                continue
            candidates.extend(grandchildren or [group])
    return candidates


def _allocated(metadata: os.stat_result) -> int:
    blocks = getattr(metadata, "st_blocks", None)
    return int(blocks * 512) if isinstance(blocks, int) else int(metadata.st_size)


def _entry_type(mode: int) -> str:
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "special"


def _is_world(path: Path) -> bool:
    for name in ("level.dat", "level.dat_old"):
        candidate = path / name
        try:
            metadata = candidate.lstat()
        except OSError:
            continue
        if stat.S_ISREG(metadata.st_mode) and not candidate.is_symlink():
            return True
    return False


def _scan_tree(path: Path, workspace: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    world_paths: list[Path] = []
    owners: set[tuple[int, int]] = set()
    inode_occurrences: dict[tuple[int, int], int] = {}
    inode_metadata: dict[tuple[int, int], tuple[int, int, int]] = {}
    root_device: int | None = None
    try:
        storage_device: int | None = int((workspace / ".workbench").lstat().st_dev)
    except OSError:
        storage_device = None
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            metadata = current.lstat()
        except OSError as exc:
            relative = (
                _relative_to_workspace(current, workspace)
                if _inside(current, workspace)
                else None
            )
            problems.append(_problem("unreadable-entry", str(exc), relative))
            continue
        relative_workspace = _relative_to_workspace(current, workspace)
        relative_item = "." if current == path else current.relative_to(path).as_posix()
        kind = _entry_type(metadata.st_mode)
        if current == path:
            root_device = int(metadata.st_dev)
        owners.add((metadata.st_uid, metadata.st_gid))
        record = {
            "path": relative_item,
            "workspace_path": relative_workspace,
            "kind": kind,
            "mode": stat.S_IMODE(metadata.st_mode),
            "size": int(metadata.st_size),
            "allocated": _allocated(metadata),
            "device": int(metadata.st_dev),
            "inode": int(metadata.st_ino),
            "links": int(metadata.st_nlink),
            "mtime_ns": int(metadata.st_mtime_ns),
            "uid": int(metadata.st_uid),
            "gid": int(metadata.st_gid),
        }
        records.append(record)
        crosses_filesystem = (
            current == path
            and storage_device is not None
            and int(metadata.st_dev) != storage_device
        ) or (
            current != path
            and root_device is not None
            and int(metadata.st_dev) != root_device
        )
        if crosses_filesystem:
            problems.append(
                _problem(
                    "filesystem-boundary",
                    "inventory did not cross this nested filesystem or mount point",
                    relative_workspace,
                )
            )
        if kind == "file":
            inode = (record["device"], record["inode"])
            inode_occurrences[inode] = inode_occurrences.get(inode, 0) + 1
            inode_metadata.setdefault(
                inode, (record["allocated"], record["size"], record["links"])
            )
        elif kind == "symlink":
            problems.append(
                _problem(
                    "symlink-entry",
                    "inventory did not follow this symlink",
                    relative_workspace,
                )
            )
        elif kind == "special":
            problems.append(
                _problem(
                    "unsupported-entry",
                    "entry is neither a regular file nor a directory",
                    relative_workspace,
                )
            )
        elif kind == "directory":
            if crosses_filesystem:
                continue
            if _is_world(current):
                world_paths.append(current)
            try:
                children = sorted(
                    (Path(entry.path) for entry in os.scandir(current)),
                    key=lambda item: item.name,
                    reverse=True,
                )
            except OSError as exc:
                problems.append(_problem("unreadable-directory", str(exc), relative_workspace))
            else:
                stack.extend(children)

    records.sort(key=lambda row: row["path"])
    regular_records = [row for row in records if row["kind"] == "file"]
    logical_bytes = sum(row["size"] for row in regular_records)
    unique_allocated = sum(value[0] for value in inode_metadata.values())
    exclusive_allocated = sum(
        allocated
        for inode, (allocated, _size, links) in inode_metadata.items()
        if inode_occurrences[inode] >= links
    )
    size = {
        "logical_bytes": logical_bytes,
        "unique_allocated_bytes": unique_allocated,
        "exclusive_allocated_bytes": exclusive_allocated,
        "file_count": len(regular_records),
        "directory_count": sum(row["kind"] == "directory" for row in records),
        "symlink_count": sum(row["kind"] == "symlink" for row in records),
    }

    components: list[dict[str, Any]] = []
    for world in sorted(set(world_paths)):
        prefix = "." if world == path else world.relative_to(path).as_posix()
        included = [
            row
            for row in records
            if prefix == "."
            or row["path"] == prefix
            or row["path"].startswith(prefix + "/")
        ]
        component_inodes: dict[tuple[int, int], tuple[int, int, int]] = {}
        component_occurrences: dict[tuple[int, int], int] = {}
        for row in included:
            if row["kind"] != "file":
                continue
            inode = (row["device"], row["inode"])
            component_occurrences[inode] = component_occurrences.get(inode, 0) + 1
            component_inodes.setdefault(
                inode, (row["allocated"], row["size"], row["links"])
            )
        world_relative = _relative_to_workspace(world, workspace)
        material = {
            "storage_root": str(workspace / ".workbench"),
            "relative_path": world_relative,
        }
        components.append(
            {
                "component_id": _identity(COMPONENT_PREFIX, material),
                "kind": "world",
                "relative_path": world_relative,
                "logical_bytes": sum(
                    row["size"] for row in included if row["kind"] == "file"
                ),
                "unique_allocated_bytes": sum(
                    value[0] for value in component_inodes.values()
                ),
                "exclusive_allocated_bytes": sum(
                    allocated
                    for inode, (allocated, _size, links) in component_inodes.items()
                    if component_occurrences[inode] >= links
                ),
            }
        )

    root_record = next((row for row in records if row["path"] == "."), None)
    root_identity = (
        None
        if root_record is None
        else {
            "device": root_record["device"],
            "inode": root_record["inode"],
            "mode": root_record["mode"],
            "mtime_ns": root_record["mtime_ns"],
            "kind": root_record["kind"],
        }
    )
    observation_rows = [
        {
            key: row[key]
            for key in (
                "path",
                "kind",
                "mode",
                "size",
                "allocated",
                "device",
                "inode",
                "links",
                "mtime_ns",
            )
        }
        for row in records
    ]
    uid = root_record["uid"] if root_record else None
    gid = root_record["gid"] if root_record else None
    try:
        user = pwd.getpwuid(uid).pw_name if uid is not None and pwd is not None else None
    except KeyError:
        user = None
    try:
        group = grp.getgrgid(gid).gr_name if gid is not None and grp is not None else None
    except KeyError:
        group = None
    return {
        "size": size,
        "components": components,
        "problems": problems,
        "observation_sha256": _canonical_sha256(observation_rows),
        "root_identity": root_identity,
        "filesystem_owner": {
            "uid": uid,
            "gid": gid,
            "user": user,
            "group": group,
            "mixed": len(owners) > 1,
        },
        "active_markers": sorted(
            row["workspace_path"]
            for row in records
            if Path(row["workspace_path"]).name
            in {
                ".workbench-active",
                ".workbench-lease",
                ".workbench-runtime-manager-lease-v1.json",
            }
        ),
        "_inodes": inode_metadata,
        "_inode_occurrences": inode_occurrences,
    }


def _live_process_state() -> tuple[list[bytes], list[Path]]:
    proc = Path("/proc")
    if not proc.is_dir():
        return [], []
    cmdlines: list[bytes] = []
    paths: set[Path] = set()
    try:
        candidates = sorted(
            (path for path in proc.iterdir() if path.name.isdigit()),
            key=lambda path: int(path.name),
        )
    except OSError:
        return cmdlines, []
    for candidate in candidates:
        try:
            payload = (candidate / "cmdline").read_bytes()
        except OSError:
            pass
        else:
            if payload:
                cmdlines.append(payload)
        for link in (candidate / "cwd",):
            try:
                target = Path(os.readlink(link))
            except OSError:
                continue
            if target.is_absolute():
                paths.add(Path(os.path.normpath(target)))
        file_descriptors = candidate / "fd"
        try:
            descriptor_links = list(file_descriptors.iterdir())
        except OSError:
            continue
        for link in descriptor_links:
            try:
                target_value = os.readlink(link)
            except OSError:
                continue
            if target_value.endswith(" (deleted)"):
                continue
            target = Path(target_value)
            if target.is_absolute():
                paths.add(Path(os.path.normpath(target)))
    return cmdlines, sorted(paths)


def _metadata_for_item(path: Path) -> tuple[dict[str, Any] | None, Path | None, list[dict[str, Any]]]:
    problems: list[dict[str, Any]] = []
    if not path.is_dir() or path.is_symlink():
        return None, None, problems
    candidates = (
        RUNTIME_MANIFEST,
        SNAPSHOT_MANIFEST,
        "iteration-report-v1.json",
    )
    for name in candidates:
        candidate = path / name
        if not candidate.exists() and not candidate.is_symlink():
            continue
        try:
            return _read_json(candidate, name), candidate, problems
        except RuntimeManagerError as exc:
            problems.append(_problem("invalid-metadata", str(exc), None))
            return None, candidate, problems
    return None, None, problems


def _admit_managed_metadata(
    metadata: Mapping[str, Any],
    path: Path,
    workspace: Path,
    storage: Path,
) -> None:
    metadata_format = metadata.get("format")
    relative = _relative_to_workspace(path, workspace)
    if metadata_format == RUNTIME_FORMAT:
        validate_managed_runtime(metadata)
        profile = metadata["profile"]
        expected_prefix = f"managed-runtime--{str(profile['pack']).lower()}--"
        if (
            metadata["workspace_root"] != str(workspace)
            or metadata["storage_root"] != str(storage)
            or metadata["relative_path"] != relative
            or path.parent != storage / "fixtures"
            or not path.name.startswith(expected_prefix)
            or metadata["producer"]["producer_id"] != PRODUCER_ID
        ):
            raise RuntimeManagerError(
                "managed runtime manifest does not bind its exact manager-owned path"
            )
    elif metadata_format == SNAPSHOT_FORMAT:
        validate_world_snapshot(metadata)
        if (
            metadata["workspace_root"] != str(workspace)
            or metadata["storage_root"] != str(storage)
            or metadata["relative_path"] != relative
            or path.parent != storage / "fixtures"
            or not path.name.startswith("managed-snapshot--")
        ):
            raise RuntimeManagerError(
                "world snapshot manifest does not bind its exact manager-owned path"
            )
    elif metadata_format == "workbench-worldgen-iteration-report-v1":
        required = {
            "format",
            "schema_version",
            "label",
            "profile",
            "mode",
            "status",
            "started_at",
            "completed_at",
            "invocation",
            "reproduction_command",
            "inputs",
            "outputs",
            "stages",
            "failure",
        }
        if set(metadata) != required or metadata.get("schema_version") != 1:
            raise RuntimeManagerError(
                "worldgen iteration report has an unsupported V1 shape"
            )
        if path.parent != storage / "iterations/worldgen":
            raise RuntimeManagerError(
                "worldgen iteration report is outside manager-recognized iteration storage"
            )
        label = metadata.get("label")
        if (
            not isinstance(label, str)
            or not LABEL_RE.fullmatch(label)
            or label != path.name
        ):
            raise RuntimeManagerError(
                "worldgen iteration report label does not bind its exact directory"
            )
        if any(
            not isinstance(metadata.get(key), str) or not metadata[key]
            for key in ("profile", "mode", "started_at")
        ):
            raise RuntimeManagerError(
                "worldgen iteration report identity and start time are invalid"
            )
        _utc(metadata["started_at"])
        status = metadata.get("status")
        completed_at = metadata.get("completed_at")
        failure = metadata.get("failure")
        if status not in {"running", "failed", "complete"}:
            raise RuntimeManagerError("worldgen iteration report status is invalid")
        if status == "running":
            if completed_at is not None or failure is not None:
                raise RuntimeManagerError(
                    "running worldgen iteration report has terminal state"
                )
        else:
            if not isinstance(completed_at, str):
                raise RuntimeManagerError(
                    "terminal worldgen iteration report lacks a completion time"
                )
            _utc(completed_at)
            if status == "failed" and not isinstance(failure, Mapping):
                raise RuntimeManagerError(
                    "failed worldgen iteration report lacks failure detail"
                )
            if status == "complete" and failure is not None:
                raise RuntimeManagerError(
                    "complete worldgen iteration report retains a failure"
                )
        invocation = metadata.get("invocation")
        if not isinstance(invocation, list) or any(
            not isinstance(argument, str) for argument in invocation
        ):
            raise RuntimeManagerError("worldgen iteration invocation is invalid")
        if not all(
            isinstance(metadata.get(key), expected)
            for key, expected in (
                ("inputs", Mapping),
                ("outputs", Mapping),
                ("stages", list),
            )
        ):
            raise RuntimeManagerError("worldgen iteration report payload is invalid")
        reproduction = metadata.get("reproduction_command")
        if reproduction is not None and not isinstance(reproduction, str):
            raise RuntimeManagerError(
                "worldgen iteration reproduction command is invalid"
            )
    else:
        raise RuntimeManagerError("metadata format is not recognized by storage policy")


def _safe_identifier(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 512:
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/+@=-]*", value):
        return None
    return value


def _extract_storage_paths(value: Any, storage: Path) -> list[Path]:
    found: set[Path] = set()
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, Mapping):
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, str):
            candidate = Path(current)
            if candidate.is_absolute():
                normalized = Path(os.path.normpath(current))
                if normalized != storage and _inside(normalized, storage):
                    found.add(normalized)
            elif current.startswith(".workbench/") and ".." not in candidate.parts:
                found.add(storage.parent / candidate)
    return sorted(found)


def _project_storage_references(
    metadata: Mapping[str, Any], storage: Path
) -> list[tuple[Path, str, bool]]:
    """Project metadata paths without inventing dependency semantics.

    Generic legacy metadata remains conservative.  Manager formats override that
    default only where their contracts state whether a retained resource is an
    actual dependency or independent provenance.
    """

    paths = _extract_storage_paths(metadata, storage)
    overrides: dict[Path, tuple[str, bool]] = {}
    metadata_format = metadata.get("format")
    if metadata_format == SNAPSHOT_FORMAT:
        source = metadata.get("source")
        if isinstance(source, Mapping):
            for path in _extract_storage_paths(source, storage):
                overrides[path] = ("snapshot-of", False)
    elif metadata_format == RUNTIME_FORMAT:
        references = metadata.get("references")
        if isinstance(references, list):
            for reference in references:
                if not isinstance(reference, Mapping):
                    continue
                required = reference.get("required") is True
                relation = "depends-on" if required else "references"
                for path in _extract_storage_paths(reference, storage):
                    previous = overrides.get(path)
                    if previous is not None and previous[1]:
                        continue
                    overrides[path] = (relation, required)
    elif metadata_format == "workbench-storage-trash-transaction-v1":
        original = metadata.get("original_relative_path")
        if isinstance(original, str):
            for path in _extract_storage_paths(original, storage):
                overrides[path] = ("trash-of", False)
    return [
        (path, *overrides.get(path, ("references", True))) for path in paths
    ]


def _trash_records(storage: Path) -> dict[str, dict[str, Any]]:
    directory = storage / "runtime-manager/trash"
    try:
        _assert_no_symlink_ancestors(directory, storage)
    except RuntimeManagerError:
        return {}
    if not directory.is_dir() or directory.is_symlink():
        return {}
    records: dict[str, dict[str, Any]] = {}
    ambiguous: set[str] = set()
    try:
        candidates = sorted(directory.glob("*.json"))
    except OSError:
        return records
    for candidate in candidates:
        try:
            value = _read_json(candidate, "runtime-manager trash record")
        except RuntimeManagerError:
            continue
        relative = value.get("quarantine_relative_path")
        if isinstance(relative, str):
            if relative in records:
                ambiguous.add(relative)
                records.pop(relative, None)
                continue
            if relative in ambiguous:
                continue
            value = dict(value)
            value["_record_path"] = str(candidate)
            try:
                _validate_trash_record(value, storage=storage)
            except RuntimeManagerError:
                continue
            cleanup_status = _cleanup_receipt_status(storage, value)
            if cleanup_status is None:
                continue
            value["_cleanup_receipt_status"] = cleanup_status
            records[relative] = value
    return records


def _cleanup_receipt_status(
    storage: Path, record: Mapping[str, Any]
) -> str | None:
    directory = storage / "runtime-manager/operations"
    try:
        _assert_no_symlink_ancestors(directory, storage)
    except RuntimeManagerError:
        return None
    if not directory.is_dir() or directory.is_symlink():
        return None
    cleanup_plan_id = record.get("_cleanup_plan_id")
    if not isinstance(cleanup_plan_id, str) or not re.fullmatch(
        re.escape(PLAN_PREFIX) + r"[0-9a-f]{64}", cleanup_plan_id
    ):
        return None
    matches: list[str] = []
    for candidate in sorted(directory.glob("*.json")):
        try:
            receipt = _read_json(candidate, "storage operation receipt")
            validate_operation_receipt(receipt)
        except RuntimeManagerError:
            continue
        if receipt.get("operation") != "cleanup":
            continue
        if receipt.get("plan_id") != cleanup_plan_id:
            continue
        actions = receipt.get("actions")
        if not isinstance(actions, list) or len(actions) != 1:
            continue
        action = actions[0]
        if (
            action.get("kind") != "move-to-trash"
            or action.get("source_relative_path")
            != record.get("original_relative_path")
            or action.get("destination_relative_path")
            != record.get("quarantine_relative_path")
        ):
            continue
        expected_name = cleanup_plan_id.split(":")[-1] + ".json"
        if candidate.name != expected_name:
            continue
        receipt_status = receipt.get("status")
        action_status = action.get("status")
        if (
            receipt_status == "complete"
            and action_status == "complete"
            and receipt.get("result", {}).get("trash_id")
            == record.get("trash_id")
            and record.get("quarantine_relative_path")
            in receipt.get("result", {}).get("output_paths", [])
        ):
            matches.append("complete")
        elif receipt_status in {"running", "failed"} and action_status in {
            "running",
            "complete",
            "failed",
        }:
            matches.append("interrupted")
    if len(matches) != 1:
        return None
    return matches[0]


def _declared_quarantine_paths(storage: Path) -> set[str]:
    directory = storage / "runtime-manager/trash"
    try:
        _assert_no_symlink_ancestors(directory, storage)
    except RuntimeManagerError:
        return set()
    if not directory.is_dir() or directory.is_symlink():
        return set()
    paths: set[str] = set()
    for candidate in sorted(directory.glob("*.json")):
        try:
            value = _read_json(candidate, "runtime-manager trash record")
        except RuntimeManagerError:
            continue
        relative = value.get("quarantine_relative_path")
        if isinstance(relative, str):
            paths.add(relative)
    return paths


def _initial_policy(
    relative: str,
    kind: str,
    metadata: Mapping[str, Any] | None,
    metadata_path: Path | None,
    problems: Sequence[Mapping[str, Any]],
    trash_record: Mapping[str, Any] | None,
    active_observed: bool,
    contains_world: bool,
) -> dict[str, Any]:
    parts = Path(relative).parts
    category = parts[1] if len(parts) > 1 else ""
    evidence_paths = [relative] if metadata_path is not None else []
    custody_state = "unknown"
    custodian = None
    producer = None
    profile_id = None
    platform_profile_id = None
    resource_id = None
    last_use = {
        "state": "unknown",
        "at": None,
        "basis": "unknown",
        "evidence_paths": [],
    }
    reproducibility = {
        "state": "unknown",
        "reason": "No complete reproduction authority was observed.",
        "evidence_paths": [],
    }
    deletion_state = "protected"
    recoverability = "none"
    reasons = ["unrecognized-custody"]

    metadata_format = metadata.get("format") if isinstance(metadata, Mapping) else None
    if trash_record is not None:
        custody_state = "managed"
        custodian = "crucible"
        producer = PRODUCER_ID
        resource_id = _safe_identifier(trash_record.get("trash_id"))
        kind = "trash"
        reproducibility = {
            "state": "retained-only",
            "reason": "The original resource is retained in manager-owned recoverable trash.",
            "evidence_paths": [],
        }
        cleanup_status = trash_record.get("_cleanup_receipt_status")
        deletion_state, recoverability, reasons = "review", "none", [
            "interrupted-cleanup"
            if cleanup_status == "interrupted"
            else "managed-trash"
        ]
    elif metadata_format == RUNTIME_FORMAT:
        custody_state = "managed"
        custodian = "crucible"
        producer = PRODUCER_ID
        profile = metadata.get("profile")
        if isinstance(profile, Mapping):
            profile_id = _safe_identifier(profile.get("profile_id"))
            platform_profile_id = _safe_identifier(profile.get("platform_profile_id"))
        resource_id = _safe_identifier(metadata.get("runtime_id"))
        kind = "runtime"
        last_used = metadata.get("last_used_at")
        if isinstance(last_used, str):
            last_use = {
                "state": "observed",
                "at": last_used,
                "basis": "manager-ledger",
                "evidence_paths": evidence_paths,
            }
        reproducibility = {
            "state": "recipe-available",
            "reason": "A manager manifest binds a profile and source recipe, not a proven byte-identical recreation.",
            "evidence_paths": evidence_paths,
        }
        if metadata.get("state") == "active":
            deletion_state, recoverability, reasons = "active", "none", ["managed-active"]
        else:
            deletion_state, recoverability, reasons = "eligible", "trash", ["managed-runtime"]
    elif metadata_format == SNAPSHOT_FORMAT:
        custody_state = "managed"
        custodian = "crucible"
        producer = PRODUCER_ID
        profile = metadata.get("profile")
        if isinstance(profile, Mapping):
            profile_id = _safe_identifier(profile.get("profile_id"))
            platform_profile_id = _safe_identifier(profile.get("platform_profile_id"))
        resource_id = _safe_identifier(metadata.get("snapshot_id"))
        kind = "snapshot"
        reproducibility = {
            "state": "retained-only",
            "reason": "The manifest verifies retained snapshot integrity; it does not prove regeneration.",
            "evidence_paths": evidence_paths,
        }
        deletion_state, recoverability, reasons = "review", "snapshot-first", ["managed-snapshot"]
    elif metadata_format == "workbench-worldgen-iteration-report-v1":
        custody_state = "recognized-legacy"
        custodian = "crucible"
        producer = "workbench-crucible-worldgen-iteration-v1"
        profile_id = _safe_identifier(metadata.get("profile"))
        resource_id = _safe_identifier(metadata.get("label"))
        status = metadata.get("status")
        timestamp = metadata.get("completed_at") or metadata.get("started_at")
        if isinstance(timestamp, str):
            last_use = {
                "state": "observed",
                "at": timestamp,
                "basis": "receipt",
                "evidence_paths": evidence_paths,
            }
        if isinstance(metadata.get("reproduction_command"), str):
            reproducibility = {
                "state": "recipe-available",
                "reason": "The iteration report retains a command, but exact input availability is not proven.",
                "evidence_paths": evidence_paths,
            }
        if status in {"running", "incomplete"}:
            deletion_state, recoverability, reasons = "active", "none", ["iteration-active"]
        else:
            deletion_state, recoverability, reasons = "review", "snapshot-first", ["legacy-iteration"]
    elif category == "evidence":
        custody_state = "recognized-legacy"
        custodian = "atlas"
        producer = "workbench-observed-evidence"
        reproducibility = {
            "state": "retained-only",
            "reason": "Observed evidence is authoritative only while retained.",
            "evidence_paths": [],
        }
        deletion_state, recoverability, reasons = "protected", "none", ["authoritative-evidence"]
    elif category in {"cache", "build", "tmp", "gradle-project-cache"}:
        custody_state = "recognized-legacy"
        custodian = "workbench"
        producer = "workbench-local-tooling"
        reproducibility = {
            "state": "regenerable",
            "reason": "This category is derived local state and can be regenerated.",
            "evidence_paths": [],
        }
        deletion_state, recoverability, reasons = "eligible", "trash", ["regenerable-local-state"]
    elif category in KNOWN_CATEGORIES:
        custody_state = "recognized-legacy"
        custodian = "workbench"
        producer = "workbench-local-tooling"
        reproducibility = {
            "state": "unknown",
            "reason": "The category is recognized, but this resource lacks a manager identity.",
            "evidence_paths": [],
        }
        deletion_state, recoverability, reasons = "protected", "none", ["legacy-unmanaged"]

    if category == "trash" and trash_record is None:
        deletion_state, recoverability, reasons = "protected", "none", ["legacy-trash-no-receipt"]
    if category == "runtime-manager":
        deletion_state, recoverability, reasons = "protected", "none", ["manager-ledger"]
    if category in CLEANUP_CATEGORIES and len(parts) < 3:
        deletion_state, recoverability = "protected", "none"
        reasons = sorted(set([*reasons, "broad-category-root"]))
    if (
        contains_world
        and trash_record is None
        and metadata_format
        not in {
            RUNTIME_FORMAT,
            SNAPSHOT_FORMAT,
            "workbench-worldgen-iteration-report-v1",
        }
    ):
        reproducibility = {
            "state": "retained-only",
            "reason": "A world-shaped tree is personal or unmanaged until explicit custody proves otherwise.",
            "evidence_paths": [],
        }
        deletion_state, recoverability = "protected", "none"
        reasons = sorted(set([*reasons, "unmanaged-world"]))
    if problems:
        deletion_state, recoverability = "protected", "none"
        reasons = sorted(set([*reasons, "unsafe-filesystem-entry"]))
    if active_observed:
        deletion_state, recoverability = "active", "none"
        reasons = sorted(set([*reasons, "observed-active-use"]))
    return {
        "kind": kind,
        "resource_id": resource_id,
        "custody": {
            "state": custody_state,
            "custodian": custodian,
            "producer": producer,
            "profile_id": profile_id,
            "platform_profile_id": platform_profile_id,
            "evidence_paths": evidence_paths,
        },
        "last_use": last_use,
        "reproducibility": reproducibility,
        "deletion": {
            "state": deletion_state,
            "recoverability": recoverability,
            "reason_codes": reasons,
            "referenced_by": [],
        },
    }


def inventory_storage(root: Path, *, now: datetime | str | None = None) -> dict[str, Any]:
    """Return a read-only inventory; never create or change storage while scanning."""

    workspace, storage = _workspace(root)
    timestamp = _utc(now)
    trash_records = _trash_records(storage) if storage.exists() else {}
    process_cmdlines, process_paths = _live_process_state()
    check_records, check_errors = check_lifecycle.registrations(workspace)
    check_active = check_lifecycle.busy(workspace)
    items: list[dict[str, Any]] = []
    internals: dict[str, dict[str, Any]] = {}
    metadata_values: dict[str, tuple[dict[str, Any], Path]] = {}
    for path in _candidate_paths(storage):
        relative = _relative_to_workspace(path, workspace)
        scan = _scan_tree(path, workspace)
        metadata, metadata_path, metadata_problems = _metadata_for_item(path)
        trash_record = trash_records.get(relative)
        scan["problems"].extend(metadata_problems)
        if (
            trash_record is not None
            and trash_record.get("original_observation_sha256")
            != scan["observation_sha256"]
        ):
            scan["problems"].append(
                _problem(
                    "trash-payload-changed",
                    "The quarantined payload no longer matches its cleanup observation.",
                    relative,
                )
            )
            trash_record = None
        if trash_record is None and metadata is not None and metadata.get("format") in {
            RUNTIME_FORMAT,
            SNAPSHOT_FORMAT,
            "workbench-worldgen-iteration-report-v1",
        }:
            try:
                _admit_managed_metadata(metadata, path, workspace, storage)
            except RuntimeManagerError as exc:
                scan["problems"].append(
                    _problem("invalid-managed-metadata", str(exc), relative)
                )
                metadata = None
        policy = _initial_policy(
            relative,
            _kind_for_path(relative),
            metadata,
            metadata_path,
            scan["problems"],
            trash_record,
            bool(scan["active_markers"])
            or any(str(path).encode("utf-8") in cmdline for cmdline in process_cmdlines)
            or any(
                observed == path or _inside(observed, path)
                for observed in process_paths
            ),
            bool(scan["components"]),
        )
        check_record = check_lifecycle.inventory_policy(workspace, path, policy, check_records, check_errors,
            active=check_active or policy["deletion"]["state"] == "active", trash=trash_record, unsafe=bool(scan["problems"]) or bool(scan["components"]))
        if check_record is not None:
            metadata = {"format": "workbench-check-storage-references-v1", "references": check_record["references"]}
            metadata_path = path / check_lifecycle.MANIFEST
        item_id = _identity(
            ITEM_PREFIX,
            {"storage_root": str(storage), "relative_path": relative},
        )
        item = {
            "item_id": item_id,
            "parent_item_id": None,
            "resource_id": policy["resource_id"],
            "kind": policy["kind"],
            "path": str(path.absolute()),
            "relative_path": relative,
            "custody": policy["custody"],
            "filesystem_owner": scan["filesystem_owner"],
            "size": scan["size"],
            "last_use": policy["last_use"],
            "reproducibility": policy["reproducibility"],
            "deletion": policy["deletion"],
            "components": scan["components"],
            "references": [],
            "observation_sha256": scan["observation_sha256"],
            "problems": scan["problems"],
            "limitations": [
                "Filesystem modification times are not represented as actual last use.",
                "Inventory observation hashes bind metadata, not large file contents.",
            ],
        }
        items.append(item)
        internals[item_id] = {
            "root_identity": scan["root_identity"],
            "inodes": scan["_inodes"],
        }
        if check_record is not None:
            metadata_values[item_id] = (metadata, metadata_path)
        elif trash_record is not None and isinstance(trash_record.get("_record_path"), str):
            reference_record = {
                key: value
                for key, value in trash_record.items()
                if not key.startswith("_")
            }
            metadata_values[item_id] = (
                reference_record,
                Path(trash_record["_record_path"]),
            )
        elif metadata is not None and metadata_path is not None:
            metadata_values[item_id] = (metadata, metadata_path)

    items.sort(key=lambda item: item["relative_path"])

    def target_for(path: Path) -> dict[str, Any] | None:
        matches = [
            item
            for item in items
            if path == Path(item["path"]) or _inside(path, Path(item["path"]))
        ]
        return max(matches, key=lambda item: len(Path(item["path"]).parts), default=None)

    inbound: dict[str, set[str]] = {item["item_id"]: set() for item in items}
    required_inbound: dict[str, set[str]] = {
        item["item_id"]: set() for item in items
    }
    for source_id, (metadata, metadata_path) in metadata_values.items():
        source = next(item for item in items if item["item_id"] == source_id)
        for referenced_path, relation, required in _project_storage_references(
            metadata, storage
        ):
            for old_record in check_records.values():
                original = workspace / ".workbench/check-attempts" / old_record["attempt_id"]
                if referenced_path == original or _inside(referenced_path, original):
                    relocated, _ = check_lifecycle.locate(workspace, old_record, trash_records)
                    if relocated is not None:
                        referenced_path = relocated / referenced_path.relative_to(original)
                    break
            target = target_for(referenced_path)
            target_id = None if target is None else target["item_id"]
            if target_id == source_id:
                continue
            target_relative = _relative_to_workspace(referenced_path, workspace)
            reference = {
                "relation": relation,
                "target_item_id": target_id,
                "target_path": target_relative,
                "required": required,
                "evidence_path": _relative_to_workspace(metadata_path, workspace),
            }
            if reference not in source["references"]:
                source["references"].append(reference)
            if target_id is not None:
                inbound[target_id].add(source_id)
                if required:
                    required_inbound[target_id].add(source_id)

    for item in items:
        item["references"].sort(
            key=lambda row: (row["target_path"] or "", row["target_item_id"] or "")
        )
        referenced_by = sorted(inbound[item["item_id"]])
        item["deletion"]["referenced_by"] = referenced_by
        if required_inbound[item["item_id"]] and item["deletion"]["state"] in {"eligible", "review"}:
            item["deletion"] = {
                "state": "protected",
                "recoverability": "none",
                "reason_codes": sorted(
                    set([*item["deletion"]["reason_codes"], "referenced-resource"])
                ),
                "referenced_by": referenced_by,
            }

    global_inodes: dict[tuple[int, int], tuple[int, int, int]] = {}
    for internal in internals.values():
        for inode, values in internal["inodes"].items():
            global_inodes.setdefault(inode, values)
    totals = {
        "item_count": len(items),
        "logical_bytes": sum(item["size"]["logical_bytes"] for item in items),
        "unique_allocated_bytes": sum(value[0] for value in global_inodes.values()),
        "exclusive_allocated_bytes": sum(
            item["size"]["exclusive_allocated_bytes"] for item in items
        ),
        "eligible_bytes": sum(
            item["size"]["exclusive_allocated_bytes"]
            for item in items
            if item["deletion"]["state"] == "eligible"
        ),
        "review_bytes": sum(
            item["size"]["exclusive_allocated_bytes"]
            for item in items
            if item["deletion"]["state"] == "review"
        ),
        "protected_bytes": sum(
            item["size"]["exclusive_allocated_bytes"]
            for item in items
            if item["deletion"]["state"] == "protected"
        ),
        "active_bytes": sum(
            item["size"]["exclusive_allocated_bytes"]
            for item in items
            if item["deletion"]["state"] == "active"
        ),
        "problem_count": sum(len(item["problems"]) for item in items),
    }
    try:
        root_device = storage.lstat().st_dev if storage.exists() else None
    except OSError:
        root_device = None
    report: dict[str, Any] = {
        "format": INVENTORY_FORMAT,
        "schema_version": 1,
        "canonicalization_id": CANONICALIZATION_ID,
        "read_only": True,
        "generated_at": timestamp,
        "workspace_root": str(workspace),
        "storage_root": str(storage),
        "scan": {
            "started_at": timestamp,
            "completed_at": timestamp,
            "followed_symlinks": False,
            "content_hashes_computed": False,
            "root_device": root_device,
            "problem_count": totals["problem_count"],
        },
        "totals": totals,
        "items": items,
        "limitations": [
            "The inventory does not hash large payload contents.",
            "Unknown custody, active resources, evidence, unsafe entries, and legacy trash are protected.",
            "Last use is reported only when a receipt or manager ledger observes it.",
        ],
    }
    report["inventory_id"] = _identity(INVENTORY_PREFIX, report)
    validate_storage_inventory(report)
    return report


def resolve_inventory_item(
    report: Mapping[str, Any], selector: str
) -> dict[str, Any]:
    if not isinstance(selector, str) or not ITEM_ID_RE.fullmatch(selector):
        raise RuntimeManagerError(
            "inventory selectors must be one exact workbench-storage-item ID"
        )
    items = report.get("items")
    if not isinstance(items, list):
        raise RuntimeManagerError("storage inventory does not contain an item list")
    matches = [item for item in items if item.get("item_id") == selector]
    if len(matches) != 1:
        raise RuntimeManagerError(f"inventory item is unavailable or ambiguous: {selector}")
    return dict(matches[0])


def _label(value: str, context: str = "label") -> str:
    if not isinstance(value, str) or not LABEL_RE.fullmatch(value):
        raise RuntimeManagerError(
            f"{context} must start with an alphanumeric character and contain only "
            "letters, digits, dots, underscores, or hyphens"
        )
    return value


def _level_name(value: str) -> str:
    if (
        not isinstance(value, str)
        or value in {".", ".."}
        or not LEVEL_RE.fullmatch(value)
    ):
        raise RuntimeManagerError("level name contains unsupported characters")
    return value


def _port(value: int | None) -> int:
    if value is not None:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
            raise RuntimeManagerError("server port must be in 1..65535")
        return value
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _slug(value: str) -> str:
    return value.lower()


def _runtime_destination(storage: Path, profile_name: str, label: str) -> Path:
    return storage / "fixtures" / (
        f"managed-runtime--{_slug(profile_name)}--{_slug(label)}"
    )


def _snapshot_destination(storage: Path, label: str) -> Path:
    return storage / "fixtures" / f"managed-snapshot--{_slug(label)}"


def _profile_context(
    authority_root: Path, profile_name: str
) -> tuple[dict[str, Any], Path, dict[str, Any], dict[str, Any]]:
    try:
        provider = runtime_provider(profile_name)
        profile_path = provider.resolve_profile_path(authority_root, profile_name)
        profile = provider.load_profile(profile_path, authority_root)
        profile["_runtime_provider"] = profile_name
    except RuntimeProviderError as exc:
        raise RuntimeManagerError(str(exc)) from exc
    resolved = Path(profile["_path"])
    visible = {key: value for key, value in profile.items() if not key.startswith("_")}
    file_sha = sha256_file(resolved)
    canonical_sha = _canonical_sha256(visible)
    plan_binding = {
        "pack": profile_name,
        "profile_id": profile["profile_id"],
        "profile_path": str(resolved),
        "profile_file_sha256": file_sha,
        "profile_canonical_sha256": canonical_sha,
        "platform_profile_id": profile["platform_profile_id"],
        "side": "dedicated-server",
    }
    full_binding = {
        **plan_binding,
        "minecraft_version": profile["minecraft_version"],
        "cleanroom_version": profile["cleanroom"],
    }
    return profile, resolved, plan_binding, full_binding


def _template_files(
    template: Path, audit: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], str]:
    skipped = {
        row["path"]
        for row in audit.get("skipped_entries", [])
        if isinstance(row, Mapping) and isinstance(row.get("path"), str)
    }
    files: list[dict[str, Any]] = []
    template_device = template.lstat().st_dev
    stack = [template]
    while stack:
        current = stack.pop()
        relative = "." if current == template else current.relative_to(template).as_posix()
        if relative != "." and (
            relative in skipped
            or any(relative.startswith(prefix + "/") for prefix in skipped)
        ):
            continue
        try:
            before = current.lstat()
        except OSError as exc:
            raise RuntimeManagerError(f"cannot inspect runtime template entry {current}: {exc}") from exc
        if before.st_dev != template_device:
            raise RuntimeManagerError(
                f"runtime template copy set crosses a filesystem boundary: {current}"
            )
        if stat.S_ISLNK(before.st_mode):
            raise RuntimeManagerError(f"runtime template copy set contains a symlink: {current}")
        if stat.S_ISDIR(before.st_mode):
            try:
                children = sorted(current.iterdir(), key=lambda item: item.name, reverse=True)
            except OSError as exc:
                raise RuntimeManagerError(f"cannot enumerate runtime template {current}: {exc}") from exc
            stack.extend(children)
            continue
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeManagerError(f"runtime template contains an unsupported entry: {current}")
        digest = sha256_file(current)
        after = current.lstat()
        before_tuple = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        after_tuple = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if before_tuple != after_tuple:
            raise RuntimeManagerError(f"runtime template changed while it was inventoried: {current}")
        files.append(
            {
                "relative_path": relative,
                "size_bytes": int(after.st_size),
                "sha256": digest,
            }
        )
    files.sort(key=lambda row: row["relative_path"])
    return files, _canonical_sha256(files)


def _template_context(
    workspace: Path,
    storage: Path,
    profile: Mapping[str, Any],
    configured: Path | None,
    *,
    inventory: Mapping[str, Any] | None = None,
) -> tuple[Path, dict[str, Any], list[dict[str, Any]], str, dict[str, Any]]:
    try:
        template = discover_runtime_template(workspace, profile, configured)
        audit = audit_runtime_template(template, profile)
    except RuntimeProviderError as exc:
        raise RuntimeManagerError(str(exc)) from exc
    if not _inside(template, storage):
        raise RuntimeManagerError(
            f"runtime templates used by the manager must stay under {storage}: {template}"
        )
    _assert_no_symlink_ancestors(template, storage)
    report = inventory_storage(workspace) if inventory is None else inventory
    matches = [item for item in report["items"] if Path(item["path"]) == template]
    if len(matches) != 1:
        raise RuntimeManagerError(
            "runtime template must resolve to one exact Workbench inventory item"
        )
    item = dict(matches[0])
    files: list[dict[str, Any]] = []
    binding = _canonical_sha256(audit)
    if audit.get("safe_to_provision") is True:
        files, binding = _template_files(template, audit)
    return template, audit, files, binding, item


def _input(
    *,
    role: str,
    item_id: str | None,
    resource_id: str | None,
    path: str | None,
    relative_path: str | None,
    observation_sha256: str | None,
    binding_sha256: str | None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "role": role,
        "item_id": item_id,
        "resource_id": resource_id,
        "path": path,
        "relative_path": relative_path,
        "observation_sha256": observation_sha256,
        "binding_sha256": binding_sha256,
    }
    value["input_id"] = _identity("workbench-storage-input:sha256:", value)
    return value


def _endpoint(
    *,
    item_id: str | None = None,
    resource_id: str | None = None,
    path: str | None = None,
    relative_path: str | None = None,
    observation_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "resource_id": resource_id,
        "path": path,
        "relative_path": relative_path,
        "observation_sha256": observation_sha256,
    }


def _action(
    kind: str,
    purpose: str,
    *,
    source: Mapping[str, Any] | None = None,
    destination: Mapping[str, Any] | None = None,
    expected_logical_bytes: int | None = None,
    recoverable: bool,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "kind": kind,
        "purpose": purpose,
        "source": None if source is None else dict(source),
        "destination": None if destination is None else dict(destination),
        "expected_logical_bytes": expected_logical_bytes,
        "recoverable": recoverable,
    }
    value["action_id"] = _identity("workbench-storage-action:sha256:", value)
    return value


def _blocker(code: str, detail: str, *item_ids: str) -> dict[str, Any]:
    return {"code": code, "detail": detail, "item_ids": sorted(set(item_ids))}


def _parameters(
    *,
    label: str | None = None,
    seed: int | None = None,
    level_name: str | None = None,
    server_port: int | None = None,
    allow_review: bool = False,
    confirmation: str | None = None,
) -> dict[str, Any]:
    return {
        "label": label,
        "seed": seed,
        "level_name": level_name,
        "server_port": server_port,
        "allow_review": allow_review,
        "confirmation": confirmation,
    }


def _plan(
    *,
    operation: str,
    timestamp: str,
    workspace: Path,
    storage: Path,
    command: str,
    parameters: Mapping[str, Any],
    profile: Mapping[str, Any] | None,
    inputs: Sequence[Mapping[str, Any]],
    actions: Sequence[Mapping[str, Any]],
    creates: Sequence[str] = (),
    moves: Sequence[Mapping[str, str]] = (),
    removes: Sequence[str] = (),
    retains: Sequence[str] = (),
    expected_reclaimed_bytes: int = 0,
    recovery_state: str,
    recovery_instruction: str,
    confirmation_token: str | None = None,
    blockers: Sequence[Mapping[str, Any]] = (),
    limitations: Sequence[str] = (),
) -> dict[str, Any]:
    if operation in {'cleanup', 'restore-trash', 'purge-trash'} and (storage / 'runtime-manager/checks').exists():
        arguments = shlex.split(command)
        command = shlex.join([*arguments[:2], '--checks-root', str(workspace), *arguments[2:]])
    value: dict[str, Any] = {
        "format": PLAN_FORMAT,
        "schema_version": 1,
        "canonicalization_id": CANONICALIZATION_ID,
        "read_only_preview": True,
        "operation": operation,
        "status": "blocked" if blockers else "ready",
        "created_at": timestamp,
        "workspace_root": str(workspace),
        "storage_root": str(storage),
        "command": command,
        "parameters": dict(parameters),
        "profile": None if profile is None else dict(profile),
        "inputs": [dict(item) for item in inputs],
        "actions": [dict(item) for item in actions],
        "effects": {
            "creates": list(creates),
            "moves": [dict(item) for item in moves],
            "removes": list(removes),
            "retains": list(retains),
            "expected_reclaimed_bytes": expected_reclaimed_bytes,
        },
        "recovery": {
            "state": recovery_state,
            "instruction": recovery_instruction,
            "confirmation_token": confirmation_token,
        },
        "blockers": [dict(item) for item in blockers],
        "limitations": list(limitations),
    }
    value["plan_id"] = _identity(PLAN_PREFIX, value)
    validate_operation_plan(value)
    return value


def _plan_input(plan: Mapping[str, Any], role: str) -> dict[str, Any]:
    matches = [row for row in plan["inputs"] if row["role"] == role]
    if len(matches) != 1:
        raise RuntimeManagerError(f"operation plan requires exactly one {role} input")
    return dict(matches[0])


def _validate_executable_plan(
    root: Path, plan: Mapping[str, Any], operation: str
) -> tuple[Path, Path]:
    validate_operation_plan(plan)
    if plan.get("operation") != operation:
        raise RuntimeManagerError(
            f"expected a {operation} plan, found {plan.get('operation')!r}"
        )
    if plan.get("status") != "ready" or plan.get("blockers"):
        raise RuntimeManagerError("blocked operation plans cannot be executed")
    workspace, storage = _workspace(root)
    if plan.get("workspace_root") != str(workspace) or plan.get("storage_root") != str(storage):
        raise RuntimeManagerError("operation plan belongs to a different Workbench root")
    return workspace, storage


def _current_inventory_item(
    workspace: Path, planned_input: Mapping[str, Any]
) -> dict[str, Any]:
    item_id = planned_input.get("item_id")
    if not isinstance(item_id, str):
        raise RuntimeManagerError("operation input does not bind an inventory item")
    current = resolve_inventory_item(inventory_storage(workspace), item_id)
    if current.get("observation_sha256") != planned_input.get("observation_sha256"):
        raise RuntimeManagerError(f"inventory item changed after preview: {item_id}")
    if current.get("relative_path") != planned_input.get("relative_path"):
        raise RuntimeManagerError(f"inventory item path changed after preview: {item_id}")
    return current


def _profile_fresh(
    authority_root: Path, plan: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    profile_binding = plan.get("profile")
    if not isinstance(profile_binding, Mapping):
        raise RuntimeManagerError("operation plan does not bind a pack profile")
    pack = profile_binding.get("pack")
    if not isinstance(pack, str):
        raise RuntimeManagerError("operation plan pack profile is invalid")
    profile, _path, current_plan, current_full = _profile_context(
        authority_root, pack
    )
    if current_plan != dict(profile_binding):
        raise RuntimeManagerError("pack profile changed after preview")
    profile_input = _plan_input(plan, "profile")
    if profile_input.get("binding_sha256") != current_plan["profile_canonical_sha256"]:
        raise RuntimeManagerError("profile input binding does not match the planned profile")
    return profile, current_full


def _template_fresh(
    workspace: Path,
    storage: Path,
    profile: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> tuple[Path, dict[str, Any], list[dict[str, Any]], str]:
    planned = _plan_input(plan, "runtime-template")
    configured = Path(planned["path"]) if isinstance(planned.get("path"), str) else None
    current_inventory = inventory_storage(workspace)
    template, audit, files, binding, item = _template_context(
        workspace,
        storage,
        profile,
        configured,
        inventory=current_inventory,
    )
    if audit.get("safe_to_provision") is not True:
        raise RuntimeManagerError("runtime template audit is no longer safe")
    if (
        item["item_id"] != planned.get("item_id")
        or item["observation_sha256"] != planned.get("observation_sha256")
        or binding != planned.get("binding_sha256")
    ):
        raise RuntimeManagerError("runtime template changed after preview")
    return template, audit, files, binding


def _verify_provisioned_template(
    template: Path,
    runtime: Path,
    audit: Mapping[str, Any],
    expected_files: Sequence[Mapping[str, Any]],
    expected_binding: str,
) -> int:
    current_files, current_binding = _template_files(template, audit)
    if current_binding != expected_binding or current_files != list(expected_files):
        raise RuntimeManagerError("runtime template changed while it was copied")
    copied_files, copied_binding = _template_files(runtime, {"skipped_entries": []})
    if copied_binding != expected_binding or copied_files != list(expected_files):
        raise RuntimeManagerError(
            "provisioned runtime bytes do not match the audited template binding"
        )
    for row in expected_files:
        source = template / row["relative_path"]
        destination = runtime / row["relative_path"]
        source_stat = source.lstat()
        destination_stat = destination.lstat()
        if (source_stat.st_dev, source_stat.st_ino) == (
            destination_stat.st_dev,
            destination_stat.st_ino,
        ):
            raise RuntimeManagerError(
                f"provisioned runtime unexpectedly shares an inode: {row['relative_path']}"
            )
    return sum(row["size_bytes"] for row in copied_files)


def plan_runtime_create(
    root: Path,
    *,
    authority_root: Path | None = None,
    profile_name: str,
    label: str,
    runtime_template: Path | None = None,
    seed: int | None = None,
    level_name: str = "world",
    server_port: int | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    workspace, storage = _workspace(root)
    timestamp = _utc(now)
    label = _label(label)
    level_name = _level_name(level_name)
    profile, profile_path, profile_binding, _full_profile = _profile_context(
        workspace if authority_root is None else authority_root.expanduser().resolve(),
        profile_name,
    )
    seed_value = profile["defaults"]["seed"] if seed is None else seed
    if isinstance(seed_value, bool) or not isinstance(seed_value, int):
        raise RuntimeManagerError("world seed must be an integer")
    if not -(2**63) <= seed_value < 2**63:
        raise RuntimeManagerError("world seed must fit a signed 64-bit integer")
    port = _port(server_port)
    inventory = inventory_storage(workspace, now=now)
    template, audit, _files, template_binding, template_item = _template_context(
        workspace,
        storage,
        profile,
        runtime_template,
        inventory=inventory,
    )
    destination = _runtime_destination(storage, profile_name, label)
    destination_relative = _relative_to_workspace(destination, workspace)
    blockers: list[dict[str, Any]] = []
    if audit.get("safe_to_provision") is not True:
        blockers.append(
            _blocker(
                "unsafe-runtime-template",
                "The exact runtime template audit found unsafe or incompatible content.",
                template_item["item_id"],
            )
        )
    if destination.exists() or destination.is_symlink():
        blockers.append(
            _blocker("destination-exists", "The managed runtime destination already exists.")
        )
    profile_input = _input(
        role="profile",
        item_id=None,
        resource_id=profile["profile_id"],
        path=str(profile_path),
        relative_path=None,
        observation_sha256=profile_binding["profile_file_sha256"],
        binding_sha256=profile_binding["profile_canonical_sha256"],
    )
    template_input = _input(
        role="runtime-template",
        item_id=template_item["item_id"],
        resource_id=template_item["resource_id"],
        path=str(template),
        relative_path=template_item["relative_path"],
        observation_sha256=template_item["observation_sha256"],
        binding_sha256=template_binding,
    )
    source = _endpoint(
        item_id=template_item["item_id"],
        resource_id=template_item["resource_id"],
        path=str(template),
        relative_path=template_item["relative_path"],
        observation_sha256=template_item["observation_sha256"],
    )
    target = _endpoint(path=str(destination), relative_path=destination_relative)
    actions = [
        _action(
            "audit-source",
            "Revalidate the complete copied template inventory before mutation.",
            source=source,
            recoverable=True,
        ),
        _action(
            "copy-independent",
            "Copy the audited runtime template without hard links or symlinks.",
            source=source,
            destination=target,
            expected_logical_bytes=template_item["size"]["logical_bytes"],
            recoverable=True,
        ),
        _action(
            "reserve-fresh-world",
            "Write loopback-only server settings while leaving the world path absent.",
            destination=target,
            recoverable=True,
        ),
        _action(
            "write-manifest",
            "Bind the managed runtime to its pack profile and source template.",
            destination=target,
            recoverable=True,
        ),
    ]
    command = shlex.join(
        [
            "workbench",
            "runtime",
            "create",
            "--profile",
            profile_name,
            "--runtime-template",
            str(template),
            "--label",
            label,
            "--seed",
            str(seed_value),
            "--level-name",
            level_name,
            "--server-port",
            str(port),
        ]
    )
    return _plan(
        operation="runtime-create",
        timestamp=timestamp,
        workspace=workspace,
        storage=storage,
        command=command,
        parameters=_parameters(
            label=label, seed=seed_value, level_name=level_name, server_port=port
        ),
        profile=profile_binding,
        inputs=[profile_input, template_input],
        actions=actions,
        creates=[destination_relative],
        retains=[template_item["relative_path"]],
        recovery_state="new-destination-only",
        recovery_instruction="A failed create removes only its fresh staging destination.",
        blockers=blockers,
        limitations=[
            "The configured world path is reserved but remains absent until Minecraft generates it.",
            "The runtime is dedicated-server, loopback-bound, offline-mode disposable state.",
        ],
    )


def _runtime_manifest(
    *,
    workspace: Path,
    storage: Path,
    runtime: Path,
    canonical_runtime: Path,
    timestamp: str,
    full_profile: Mapping[str, Any],
    template: Path,
    audit: Mapping[str, Any],
    template_binding: str,
    world: Mapping[str, Any],
    state: str = "ready",
    references: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    scan = _scan_tree(runtime, workspace)
    if scan["problems"]:
        raise RuntimeManagerError("new managed runtime contains an unsafe filesystem entry")
    server = audit.get("server_jar")
    if not isinstance(server, Mapping):
        raise RuntimeManagerError("runtime template audit does not bind one server JAR")
    implementation = Path(__file__).resolve()
    value: dict[str, Any] = {
        "format": RUNTIME_FORMAT,
        "schema_version": 1,
        "canonicalization_id": CANONICALIZATION_ID,
        "state": state,
        "created_at": timestamp,
        "last_used_at": None,
        "workspace_root": str(workspace),
        "storage_root": str(storage),
        "relative_path": _relative_to_workspace(canonical_runtime, workspace),
        "producer": {
            "producer_id": PRODUCER_ID,
            "implementation_path": str(implementation),
            "implementation_sha256": sha256_file(implementation),
        },
        "profile": dict(full_profile),
        "source_template": {
            "path": str(template),
            "inventory_sha256": template_binding,
            "server_jar": {
                "relative_path": server["relative_path"],
                "size_bytes": server["size_bytes"],
                "sha256": server["sha256"],
            },
            "audit_safe": True,
        },
        "world": dict(world),
        "content": {
            "tree_observation_sha256": scan["observation_sha256"],
            "logical_bytes": scan["size"]["logical_bytes"],
            "unique_allocated_bytes": scan["size"]["unique_allocated_bytes"],
            "file_count": scan["size"]["file_count"],
            "directory_count": scan["size"]["directory_count"],
            "symlink_count": 0,
            "independent_copy": True,
        },
        "references": [dict(row) for row in references],
        "limitations": [
            "The content observation excludes this manifest to avoid self-reference.",
            "A reserved-absent world is not evidence that Minecraft generated a world.",
        ],
    }
    value["runtime_id"] = _identity(RUNTIME_PREFIX, value)
    validate_managed_runtime(value)
    return value


class _OperationJournal:
    def __init__(
        self,
        *,
        workspace: Path,
        storage: Path,
        plan: Mapping[str, Any],
        timestamp: str,
    ) -> None:
        self.workspace = workspace
        self.storage = storage
        self.plan = plan
        self.timestamp = timestamp
        self.directory = _ensure_ledger_directory(storage, "operations")
        self.path = self.directory / (plan["plan_id"].split(":")[-1] + ".json")
        if self.path.exists() or self.path.is_symlink():
            raise RuntimeManagerError(
                "this exact operation plan already has a receipt; create a fresh preview"
            )
        self.actions = [
            {
                "action_id": action["action_id"],
                "kind": action["kind"],
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "source_relative_path": (
                    None
                    if action["source"] is None
                    else action["source"]["relative_path"]
                ),
                "destination_relative_path": (
                    None
                    if action["destination"] is None
                    else action["destination"]["relative_path"]
                ),
                "bytes_processed": 0,
                "error": None,
            }
            for action in plan["actions"]
        ]
        self.current: int | None = None
        self.result = {
            "resource_ids": [],
            "trash_id": None,
            "restored_item_id": None,
            "purged_bytes": 0,
            "output_paths": [],
        }
        self._write("running")
        self.begin(0)

    def _value(
        self,
        status: str,
        *,
        completed_at: str | None,
        error: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "format": RECEIPT_FORMAT,
            "schema_version": 1,
            "canonicalization_id": CANONICALIZATION_ID,
            "plan_id": self.plan["plan_id"],
            "operation": self.plan["operation"],
            "status": status,
            "started_at": self.timestamp,
            "completed_at": completed_at,
            "workspace_root": str(self.workspace),
            "storage_root": str(self.storage),
            "actions": [dict(action) for action in self.actions],
            "result": dict(self.result),
            "error": None if error is None else dict(error),
            "limitations": [
                "This receipt records the local filesystem operation; it is not gameplay evidence."
            ],
        }
        value["receipt_id"] = _identity(RECEIPT_PREFIX, value)
        validate_operation_receipt(value, plan=self.plan)
        return value

    def _write(
        self,
        status: str,
        *,
        completed_at: str | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        value = self._value(status, completed_at=completed_at, error=error)
        _assert_no_symlink_ancestors(self.path.parent, self.storage)
        _write_json(self.path, value)
        return value

    def begin(self, index: int) -> None:
        if not 0 <= index < len(self.actions):
            raise RuntimeManagerError("receipt action index is out of bounds")
        if self.current is not None:
            raise RuntimeManagerError("finish the current receipt action before advancing")
        if any(action["status"] != "complete" for action in self.actions[:index]):
            raise RuntimeManagerError("receipt actions must advance in plan order")
        action = self.actions[index]
        if action["status"] != "pending":
            raise RuntimeManagerError("receipt action has already begun")
        action["status"] = "running"
        action["started_at"] = self.timestamp
        self.current = index
        self._write("running")

    def finish(self, *, bytes_processed: int = 0) -> None:
        if self.current is None:
            raise RuntimeManagerError("no receipt action is running")
        if bytes_processed < 0:
            raise RuntimeManagerError("receipt bytes processed cannot be negative")
        action = self.actions[self.current]
        action["status"] = "complete"
        action["completed_at"] = self.timestamp
        action["bytes_processed"] = bytes_processed
        self.current = None
        self._write("running")

    def advance(self, index: int) -> None:
        self.begin(index)

    def fail(self, exc: BaseException, *, bytes_processed: int = 0) -> None:
        if bytes_processed < 0:
            raise RuntimeManagerError("receipt bytes processed cannot be negative")
        if self.current is None:
            pending = next(
                (
                    index
                    for index, action in enumerate(self.actions)
                    if action["status"] == "pending"
                ),
                None,
            )
            if pending is not None:
                self.begin(pending)
            else:
                self.current = len(self.actions) - 1
                action = self.actions[self.current]
                action["status"] = "running"
                action["completed_at"] = None
                action["error"] = None
        assert self.current is not None
        action = self.actions[self.current]
        message = str(exc).replace("\r", " ").replace("\n", " ") or type(exc).__name__
        action["status"] = "failed"
        action["completed_at"] = self.timestamp
        action["bytes_processed"] = bytes_processed
        action["error"] = message
        error = {
            "type": type(exc).__name__,
            "message": message,
            "action_id": action["action_id"],
        }
        self.current = None
        self._write("failed", completed_at=self.timestamp, error=error)

    def complete(
        self,
        *,
        resource_ids: Sequence[str] = (),
        trash_id: str | None = None,
        restored_item_id: str | None = None,
        purged_bytes: int = 0,
        output_paths: Sequence[str] = (),
    ) -> dict[str, Any]:
        if self.current is not None or any(
            action["status"] != "complete" for action in self.actions
        ):
            raise RuntimeManagerError("cannot complete a receipt with unfinished actions")
        receipt_relative = _relative_to_workspace(self.path, self.workspace)
        self.result = {
            "resource_ids": sorted(set(resource_ids)),
            "trash_id": trash_id,
            "restored_item_id": restored_item_id,
            "purged_bytes": purged_bytes,
            "output_paths": sorted(set([*output_paths, receipt_relative])),
        }
        return self._write("complete", completed_at=self.timestamp)


def _complete_receipt(
    *,
    workspace: Path,
    storage: Path,
    plan: Mapping[str, Any],
    timestamp: str,
    bytes_processed: int = 0,
    resource_ids: Sequence[str] = (),
    trash_id: str | None = None,
    restored_item_id: str | None = None,
    purged_bytes: int = 0,
    output_paths: Sequence[str] = (),
    journal: _OperationJournal | None = None,
) -> dict[str, Any]:
    active = journal or _OperationJournal(
        workspace=workspace, storage=storage, plan=plan, timestamp=timestamp
    )
    if journal is None:
        for index in range(len(active.actions)):
            if index:
                active.advance(index)
            active.finish(bytes_processed=bytes_processed)
    return active.complete(
        resource_ids=resource_ids,
        trash_id=trash_id,
        restored_item_id=restored_item_id,
        purged_bytes=purged_bytes,
        output_paths=output_paths,
    )


def _record_operation_failure(
    journal: _OperationJournal,
    exc: BaseException,
    *,
    bytes_processed: int = 0,
) -> None:
    try:
        journal.fail(exc, bytes_processed=bytes_processed)
    except Exception as receipt_exc:
        raise RuntimeManagerError(
            f"operation failed ({exc}); failed receipt could not be persisted ({receipt_exc})"
        ) from exc


def execute_runtime_create(
    root: Path,
    plan: Mapping[str, Any],
    *,
    authority_root: Path | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    workspace, storage = _validate_executable_plan(root, plan, "runtime-create")
    timestamp = _utc(now)
    journal = _OperationJournal(
        workspace=workspace, storage=storage, plan=plan, timestamp=timestamp
    )
    staging: Path | None = None
    parent: Path | None = None
    try:
        profile, full_profile = _profile_fresh(
            workspace if authority_root is None else authority_root.expanduser().resolve(),
            plan,
        )
        template, audit, template_files, template_binding = _template_fresh(
            workspace, storage, profile, plan
        )
        parameters = plan["parameters"]
        label = parameters["label"]
        destination = _runtime_destination(storage, plan["profile"]["pack"], label)
        expected_relative = plan["effects"]["creates"]
        if expected_relative != [_relative_to_workspace(destination, workspace)]:
            raise RuntimeManagerError("runtime destination does not match the plan parameters")
        if destination.exists() or destination.is_symlink():
            raise RuntimeManagerError(
                f"managed runtime destination already exists: {destination}"
            )
        journal.finish()

        journal.advance(1)
        parent = destination.parent
        _assert_no_symlink_ancestors(parent, storage)
        parent.mkdir(parents=True, exist_ok=True)
        _assert_no_symlink_ancestors(parent, storage)
        staging = parent / f".{destination.name}.{uuid.uuid4().hex}.partial"
        if staging.exists() or staging.is_symlink():
            raise RuntimeManagerError(f"fresh runtime staging path already exists: {staging}")
        provision_runtime(template, staging, profile)
        copied_template_bytes = _verify_provisioned_template(
            template,
            staging,
            audit,
            template_files,
            template_binding,
        )
        journal.finish(bytes_processed=copied_template_bytes)

        journal.advance(2)
        configuration = configure_runtime(
            staging,
            profile=profile,
            seed=parameters["seed"],
            world_type=profile["world_type"],
            server_port=parameters["server_port"],
            level_name=parameters["level_name"],
        )
        level_path = Path(configuration["level_path"])
        if level_path.exists() or level_path.is_symlink():
            raise RuntimeManagerError("fresh-world reservation unexpectedly created the world")
        journal.finish()

        journal.advance(3)
        source_reference = {
            "relation": "copied-from",
            "resource_id": None,
            "path": str(template),
            "sha256": template_binding,
            "required": True,
        }
        manifest = _runtime_manifest(
            workspace=workspace,
            storage=storage,
            runtime=staging,
            canonical_runtime=destination,
            timestamp=timestamp,
            full_profile=full_profile,
            template=template,
            audit=audit,
            template_binding=template_binding,
            world={
                "level_name": parameters["level_name"],
                "relative_path": parameters["level_name"],
                "state": "reserved-absent",
                "seed": parameters["seed"],
                "world_type": profile["world_type"],
            },
            references=[source_reference],
        )
        _write_json(staging / RUNTIME_MANIFEST, manifest)
        if destination.exists() or destination.is_symlink():
            raise RuntimeManagerError(f"managed runtime destination appeared during create: {destination}")
        os.replace(staging, destination)
        journal.finish()
    except Exception as exc:
        if (
            staging is not None
            and parent is not None
            and staging.exists()
            and _inside(staging, parent)
            and staging.name.startswith(".")
        ):
            shutil.rmtree(staging, ignore_errors=True)
        if isinstance(exc, RuntimeProviderError):
            failure = RuntimeManagerError(str(exc))
            _record_operation_failure(journal, failure)
            raise failure from exc
        _record_operation_failure(journal, exc)
        raise
    return _complete_receipt(
        workspace=workspace,
        storage=storage,
        plan=plan,
        timestamp=timestamp,
        resource_ids=[manifest["runtime_id"]],
        output_paths=[_relative_to_workspace(destination, workspace)],
        journal=journal,
    )


def _load_runtime_manifest(item: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(item["path"]) / RUNTIME_MANIFEST
    value = _read_json(path, "managed runtime manifest")
    validate_managed_runtime(value)
    if value["relative_path"] != item["relative_path"]:
        raise RuntimeManagerError("managed runtime manifest path does not match inventory")
    return value


def _load_snapshot_manifest(item: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(item["path"]) / SNAPSHOT_MANIFEST
    value = _read_json(path, "world snapshot manifest")
    validate_world_snapshot(value)
    if value["relative_path"] != item["relative_path"]:
        raise RuntimeManagerError("world snapshot manifest path does not match inventory")
    return value


def _plan_profile_from_full(profile: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: profile[key]
        for key in (
            "pack",
            "profile_id",
            "profile_path",
            "profile_file_sha256",
            "profile_canonical_sha256",
            "platform_profile_id",
            "side",
        )
    }


def _select_world(
    item: Mapping[str, Any], world: str | None
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    components = [
        dict(component)
        for component in item.get("components", [])
        if component.get("kind") == "world"
    ]
    blockers: list[dict[str, Any]] = []
    if world is not None:
        _level_name(world)
        components = [
            component
            for component in components
            if Path(component["relative_path"]).name == world
        ]
    if not components:
        blockers.append(
            _blocker(
                "world-not-found",
                "The selected managed runtime does not contain a generated world.",
                item["item_id"],
            )
        )
        return None, blockers
    if len(components) != 1:
        blockers.append(
            _blocker(
                "world-selection-ambiguous",
                "Select one named world because the managed runtime contains multiple worlds.",
                item["item_id"],
            )
        )
        return None, blockers
    return components[0], blockers


def plan_world_snapshot(
    root: Path,
    *,
    selector: str,
    label: str,
    world: str | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    workspace, storage = _workspace(root)
    timestamp = _utc(now)
    label = _label(label)
    inventory = inventory_storage(workspace, now=now)
    item = resolve_inventory_item(inventory, selector)
    if item["kind"] != "runtime" or item["custody"]["state"] != "managed":
        raise RuntimeManagerError("world snapshots require one exact managed runtime item")
    manifest = _load_runtime_manifest(item)
    component, blockers = _select_world(item, world)
    if item["deletion"]["state"] == "active":
        blockers.append(
            _blocker(
                "runtime-active",
                "Stop the managed runtime before taking a filesystem snapshot.",
                item["item_id"],
            )
        )
    if item["problems"]:
        blockers.append(
            _blocker(
                "unsafe-filesystem-entry",
                "The runtime contains a symlink, special entry, or unreadable path.",
                item["item_id"],
            )
        )
    if component is None:
        source_path = Path(item["path"])
        source_relative = item["relative_path"]
        world_observation = item["observation_sha256"]
        expected_bytes = 0
    else:
        source_path = workspace / component["relative_path"]
        source_relative = component["relative_path"]
        world_scan = _scan_tree(source_path, workspace)
        world_observation = world_scan["observation_sha256"]
        expected_bytes = component["logical_bytes"]
        if world_scan["problems"]:
            blockers.append(
                _blocker(
                    "unsafe-world-entry",
                    "The selected world contains a symlink, special entry, or unreadable path.",
                    item["item_id"],
                )
            )
    destination = _snapshot_destination(storage, label)
    destination_relative = _relative_to_workspace(destination, workspace)
    payload_relative = _relative_to_workspace(destination / "world", workspace)
    if destination.exists() or destination.is_symlink():
        blockers.append(
            _blocker("destination-exists", "The managed snapshot destination already exists.")
        )
    full_profile = manifest["profile"]
    profile_binding = _plan_profile_from_full(full_profile)
    inputs = [
        _input(
            role="profile",
            item_id=None,
            resource_id=profile_binding["profile_id"],
            path=profile_binding["profile_path"],
            relative_path=None,
            observation_sha256=profile_binding["profile_file_sha256"],
            binding_sha256=profile_binding["profile_canonical_sha256"],
        ),
        _input(
            role="managed-runtime",
            item_id=item["item_id"],
            resource_id=manifest["runtime_id"],
            path=item["path"],
            relative_path=item["relative_path"],
            observation_sha256=item["observation_sha256"],
            binding_sha256=_canonical_sha256(manifest),
        ),
        _input(
            role="world",
            item_id=item["item_id"],
            resource_id=manifest["runtime_id"],
            path=str(source_path),
            relative_path=source_relative,
            observation_sha256=world_observation,
            binding_sha256=world_observation,
        ),
    ]
    source = _endpoint(
        item_id=item["item_id"],
        resource_id=manifest["runtime_id"],
        path=str(source_path),
        relative_path=source_relative,
        observation_sha256=world_observation,
    )
    target = _endpoint(path=str(destination), relative_path=destination_relative)
    actions = [
        _action(
            "audit-source",
            "Revalidate that the managed runtime is quiesced and unchanged.",
            source=source,
            recoverable=True,
        ),
        _action(
            "copy-independent",
            "Copy every regular world file into a fresh snapshot payload.",
            source=source,
            destination=target,
            expected_logical_bytes=expected_bytes,
            recoverable=True,
        ),
        _action(
            "write-manifest",
            "Bind per-file hashes and the source/profile context.",
            destination=target,
            recoverable=True,
        ),
    ]
    command_parts = [
        "workbench",
        "world",
        "snapshot",
        item["item_id"],
        "--label",
        label,
    ]
    if world is not None:
        command_parts.extend(["--world", world])
    return _plan(
        operation="world-snapshot",
        timestamp=timestamp,
        workspace=workspace,
        storage=storage,
        command=shlex.join(command_parts),
        parameters=_parameters(label=label),
        profile=profile_binding,
        inputs=inputs,
        actions=actions,
        creates=[destination_relative, payload_relative],
        retains=[item["relative_path"]],
        recovery_state="new-destination-only",
        recovery_instruction="A failed snapshot removes only its fresh staging destination.",
        blockers=blockers,
        limitations=[
            "Filesystem stability is checked before and after copying; this is not an in-game transactional snapshot.",
            "The source must be manager-quiesced and contain only regular files and directories.",
        ],
    )


def _file_manifest(root: Path) -> list[dict[str, Any]]:
    if root.is_symlink() or not root.is_dir():
        raise RuntimeManagerError(f"world payload must be a regular directory: {root}")
    files: list[dict[str, Any]] = []
    root_device = root.lstat().st_dev
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise RuntimeManagerError(f"cannot inspect world entry {current}: {exc}") from exc
        if metadata.st_dev != root_device:
            raise RuntimeManagerError(f"world payload crosses a filesystem boundary: {current}")
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeManagerError(f"world snapshots refuse symlinks: {current}")
        if stat.S_ISDIR(metadata.st_mode):
            try:
                children = sorted(current.iterdir(), key=lambda item: item.name, reverse=True)
            except OSError as exc:
                raise RuntimeManagerError(f"cannot enumerate world directory {current}: {exc}") from exc
            stack.extend(children)
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeManagerError(f"world snapshots refuse special files: {current}")
        before = (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
        digest = sha256_file(current)
        after_metadata = current.lstat()
        after = (
            after_metadata.st_dev,
            after_metadata.st_ino,
            after_metadata.st_size,
            after_metadata.st_mtime_ns,
        )
        if before != after:
            raise RuntimeManagerError(f"world file changed while hashing: {current}")
        files.append(
            {
                "relative_path": current.relative_to(root).as_posix(),
                "size_bytes": int(after_metadata.st_size),
                "allocated_bytes": _allocated(after_metadata),
                "sha256": digest,
            }
        )
    files.sort(key=lambda row: row["relative_path"])
    if not files:
        raise RuntimeManagerError("world snapshot payload cannot be empty")
    return files


def _copy_tree_independent(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise RuntimeManagerError(f"copy destination already exists: {destination}")

    source_device = source.lstat().st_dev

    def copy_entry(source_entry: Path, destination_entry: Path) -> None:
        metadata = source_entry.lstat()
        if metadata.st_dev != source_device:
            raise RuntimeManagerError(
                f"independent copy refuses a nested filesystem boundary: {source_entry}"
            )
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeManagerError(f"independent copies refuse symlinks: {source_entry}")
        if stat.S_ISDIR(metadata.st_mode):
            destination_entry.mkdir()
            for child in sorted(source_entry.iterdir(), key=lambda item: item.name):
                copy_entry(child, destination_entry / child.name)
            shutil.copystat(source_entry, destination_entry, follow_symlinks=False)
            return
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeManagerError(f"independent copies refuse special files: {source_entry}")
        destination_entry.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_entry, destination_entry)
        if source_entry.stat().st_ino == destination_entry.stat().st_ino:
            raise RuntimeManagerError(f"independent copy unexpectedly shares an inode: {source_entry}")

    copy_entry(source, destination)


def execute_world_snapshot(
    root: Path,
    plan: Mapping[str, Any],
    *,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    workspace, storage = _validate_executable_plan(root, plan, "world-snapshot")
    timestamp = _utc(now)
    journal = _OperationJournal(
        workspace=workspace, storage=storage, plan=plan, timestamp=timestamp
    )
    staging: Path | None = None
    parent: Path | None = None
    try:
        _profile, current_full_profile = _profile_fresh(workspace, plan)
        runtime_input = _plan_input(plan, "managed-runtime")
        runtime_item = _current_inventory_item(workspace, runtime_input)
        if runtime_item["deletion"]["state"] == "active":
            raise RuntimeManagerError("managed runtime became active after preview")
        if runtime_item["problems"]:
            raise RuntimeManagerError("managed runtime became unsafe after preview")
        runtime_manifest = _load_runtime_manifest(runtime_item)
        if runtime_manifest["runtime_id"] != runtime_input["resource_id"]:
            raise RuntimeManagerError("managed runtime identity changed after preview")
        if runtime_manifest["profile"] != current_full_profile:
            raise RuntimeManagerError("managed runtime profile context no longer matches")
        world_input = _plan_input(plan, "world")
        source_world = Path(world_input["path"])
        if not _inside(source_world, Path(runtime_item["path"])):
            raise RuntimeManagerError("planned world path escapes its managed runtime")
        _assert_no_symlink_ancestors(source_world, Path(runtime_item["path"]))
        before_observation = _scan_tree(source_world, workspace)
        if before_observation["problems"]:
            raise RuntimeManagerError("selected world contains an unsafe filesystem entry")
        if before_observation["observation_sha256"] != world_input["observation_sha256"]:
            raise RuntimeManagerError("selected world changed after preview")
        before_files = _file_manifest(source_world)
        destination = _snapshot_destination(storage, plan["parameters"]["label"])
        if plan["effects"]["creates"][0] != _relative_to_workspace(destination, workspace):
            raise RuntimeManagerError("snapshot destination does not match plan parameters")
        if destination.exists() or destination.is_symlink():
            raise RuntimeManagerError(
                f"managed snapshot destination already exists: {destination}"
            )
        journal.finish()

        journal.advance(1)
        parent = destination.parent
        _assert_no_symlink_ancestors(parent, storage)
        parent.mkdir(parents=True, exist_ok=True)
        _assert_no_symlink_ancestors(parent, storage)
        staging = parent / f".{destination.name}.{uuid.uuid4().hex}.partial"
        staging.mkdir()
        staging_payload = staging / "world"
        _copy_tree_independent(source_world, staging_payload)
        after_files = _file_manifest(source_world)
        after_observation = _scan_tree(source_world, workspace)
        if (
            before_files != after_files
            or before_observation["observation_sha256"]
            != after_observation["observation_sha256"]
        ):
            raise RuntimeManagerError("world changed while the snapshot was copied")
        copied_files = _file_manifest(staging_payload)
        if [
            {key: row[key] for key in ("relative_path", "size_bytes", "sha256")}
            for row in before_files
        ] != [
            {key: row[key] for key in ("relative_path", "size_bytes", "sha256")}
            for row in copied_files
        ]:
            raise RuntimeManagerError("snapshot payload does not match the source world")
        copied_bytes = sum(row["size_bytes"] for row in copied_files)
        journal.finish(bytes_processed=copied_bytes)

        journal.advance(2)
        final_payload = destination / "world"
        value: dict[str, Any] = {
            "format": SNAPSHOT_FORMAT,
            "schema_version": 1,
            "canonicalization_id": CANONICALIZATION_ID,
            "state": "complete",
            "created_at": timestamp,
            "workspace_root": str(workspace),
            "storage_root": str(storage),
            "relative_path": _relative_to_workspace(destination, workspace),
            "source": {
                "world_item_id": runtime_item["item_id"],
                "runtime_id": runtime_manifest["runtime_id"],
                "world_path": str(source_world),
                "world_relative_path": _relative_to_workspace(source_world, workspace),
                "world_observation_sha256": before_observation["observation_sha256"],
                "level_name": source_world.name,
                "seed": runtime_manifest["world"]["seed"],
                "world_type": runtime_manifest["world"]["world_type"],
            },
            "profile": dict(runtime_manifest["profile"]),
            "consistency": {
                "state": "manager-quiesced",
                "manager_lease_active": False,
                "source_observation_before_sha256": before_observation["observation_sha256"],
                "source_observation_after_sha256": after_observation["observation_sha256"],
                "source_stable": True,
                "statement": "Workbench observed the stopped filesystem tree as stable before and after an independent copy.",
            },
            "content": {
                "payload_relative_path": _relative_to_workspace(final_payload, workspace),
                "tree_sha256": _canonical_sha256(copied_files),
                "file_count": len(copied_files),
                "logical_bytes": sum(row["size_bytes"] for row in copied_files),
                "unique_allocated_bytes": sum(row["allocated_bytes"] for row in copied_files),
                "symlink_count": 0,
                "independent_copy": True,
            },
            "files": copied_files,
            "limitations": [
                "Manager-quiesced filesystem stability is not an in-game transactional guarantee.",
                "Snapshot integrity depends on retaining the copied payload bytes.",
            ],
        }
        value["snapshot_id"] = _identity(SNAPSHOT_PREFIX, value)
        validate_world_snapshot(value)
        _write_json(staging / SNAPSHOT_MANIFEST, value)
        if destination.exists() or destination.is_symlink():
            raise RuntimeManagerError(f"snapshot destination appeared during create: {destination}")
        os.replace(staging, destination)
        journal.finish()
    except Exception as exc:
        if (
            staging is not None
            and parent is not None
            and staging.exists()
            and _inside(staging, parent)
        ):
            shutil.rmtree(staging, ignore_errors=True)
        _record_operation_failure(journal, exc)
        raise
    return _complete_receipt(
        workspace=workspace,
        storage=storage,
        plan=plan,
        timestamp=timestamp,
        resource_ids=[value["snapshot_id"]],
        output_paths=[value["relative_path"], value["content"]["payload_relative_path"]],
        journal=journal,
    )


def _profiles_compatible(
    requested: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> bool:
    return dict(requested) == dict(snapshot)


def plan_world_restore(
    root: Path,
    *,
    authority_root: Path | None = None,
    snapshot_selector: str,
    profile_name: str,
    label: str,
    runtime_template: Path | None = None,
    level_name: str | None = None,
    server_port: int | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    workspace, storage = _workspace(root)
    timestamp = _utc(now)
    label = _label(label)
    inventory = inventory_storage(workspace, now=now)
    snapshot_item = resolve_inventory_item(inventory, snapshot_selector)
    if snapshot_item["kind"] != "snapshot" or snapshot_item["custody"]["state"] != "managed":
        raise RuntimeManagerError("world restore requires one exact managed snapshot item")
    snapshot = _load_snapshot_manifest(snapshot_item)
    profile, profile_path, profile_binding, full_profile = _profile_context(
        workspace if authority_root is None else authority_root.expanduser().resolve(),
        profile_name,
    )
    selected_level = _level_name(level_name or snapshot["source"]["level_name"])
    port = _port(server_port)
    template, audit, _files, template_binding, template_item = _template_context(
        workspace,
        storage,
        profile,
        runtime_template,
        inventory=inventory,
    )
    destination = _runtime_destination(storage, profile_name, label)
    destination_relative = _relative_to_workspace(destination, workspace)
    blockers: list[dict[str, Any]] = []
    if not _profiles_compatible(full_profile, snapshot["profile"]):
        blockers.append(
            _blocker(
                "profile-incompatible",
                "The requested pack/platform profile does not exactly match the snapshot context.",
                snapshot_item["item_id"],
            )
        )
    payload = workspace / snapshot["content"]["payload_relative_path"]
    try:
        if not _inside(payload, Path(snapshot_item["path"])):
            raise RuntimeManagerError("snapshot payload escapes its managed snapshot")
        _assert_no_symlink_ancestors(payload, Path(snapshot_item["path"]))
        payload_files = _file_manifest(payload)
        if (
            payload_files != snapshot["files"]
            or _canonical_sha256(payload_files)
            != snapshot["content"]["tree_sha256"]
        ):
            raise RuntimeManagerError(
                "snapshot payload no longer matches its retained manifest"
            )
    except RuntimeManagerError as exc:
        blockers.append(
            _blocker(
                "snapshot-payload-changed",
                str(exc),
                snapshot_item["item_id"],
            )
        )
    if snapshot_item["problems"]:
        blockers.append(
            _blocker(
                "unsafe-snapshot-entry",
                "The snapshot contains a symlink, special entry, or unreadable path.",
                snapshot_item["item_id"],
            )
        )
    if audit.get("safe_to_provision") is not True:
        blockers.append(
            _blocker(
                "unsafe-runtime-template",
                "The exact runtime template audit found unsafe or incompatible content.",
                template_item["item_id"],
            )
        )
    if destination.exists() or destination.is_symlink():
        blockers.append(_blocker("destination-exists", "The restore destination already exists."))
    inputs = [
        _input(
            role="profile",
            item_id=None,
            resource_id=profile["profile_id"],
            path=str(profile_path),
            relative_path=None,
            observation_sha256=profile_binding["profile_file_sha256"],
            binding_sha256=profile_binding["profile_canonical_sha256"],
        ),
        _input(
            role="world-snapshot",
            item_id=snapshot_item["item_id"],
            resource_id=snapshot["snapshot_id"],
            path=snapshot_item["path"],
            relative_path=snapshot_item["relative_path"],
            observation_sha256=snapshot_item["observation_sha256"],
            binding_sha256=_canonical_sha256(snapshot),
        ),
        _input(
            role="runtime-template",
            item_id=template_item["item_id"],
            resource_id=template_item["resource_id"],
            path=str(template),
            relative_path=template_item["relative_path"],
            observation_sha256=template_item["observation_sha256"],
            binding_sha256=template_binding,
        ),
    ]
    snapshot_endpoint = _endpoint(
        item_id=snapshot_item["item_id"],
        resource_id=snapshot["snapshot_id"],
        path=snapshot_item["path"],
        relative_path=snapshot_item["relative_path"],
        observation_sha256=snapshot_item["observation_sha256"],
    )
    template_endpoint = _endpoint(
        item_id=template_item["item_id"],
        resource_id=template_item["resource_id"],
        path=str(template),
        relative_path=template_item["relative_path"],
        observation_sha256=template_item["observation_sha256"],
    )
    target = _endpoint(path=str(destination), relative_path=destination_relative)
    actions = [
        _action(
            "audit-source",
            "Revalidate the snapshot, profile, and template bindings.",
            source=snapshot_endpoint,
            recoverable=True,
        ),
        _action(
            "copy-independent",
            "Provision a fresh independent runtime from the audited template.",
            source=template_endpoint,
            destination=target,
            expected_logical_bytes=template_item["size"]["logical_bytes"],
            recoverable=True,
        ),
        _action(
            "reserve-fresh-world",
            "Reserve an absent world path inside the new runtime.",
            destination=target,
            recoverable=True,
        ),
        _action(
            "copy-independent",
            "Copy and verify the snapshot payload into only the new world path.",
            source=snapshot_endpoint,
            destination=target,
            expected_logical_bytes=snapshot["content"]["logical_bytes"],
            recoverable=True,
        ),
        _action(
            "write-manifest",
            "Bind the restored world and snapshot identity in the new runtime manifest.",
            destination=target,
            recoverable=True,
        ),
    ]
    command = shlex.join(
        [
            "workbench",
            "world",
            "restore",
            snapshot_item["item_id"],
            "--profile",
            profile_name,
            "--label",
            label,
            "--runtime-template",
            str(template),
            "--level-name",
            selected_level,
            "--server-port",
            str(port),
        ]
    )
    return _plan(
        operation="world-restore",
        timestamp=timestamp,
        workspace=workspace,
        storage=storage,
        command=command,
        parameters=_parameters(
            label=label, level_name=selected_level, server_port=port
        ),
        profile=profile_binding,
        inputs=inputs,
        actions=actions,
        creates=[destination_relative, _relative_to_workspace(destination / selected_level, workspace)],
        retains=[snapshot_item["relative_path"], template_item["relative_path"]],
        recovery_state="new-destination-only",
        recovery_instruction="A failed restore removes only its fresh staging runtime.",
        blockers=blockers,
        limitations=[
            "Restore always creates a new managed runtime and never overwrites a world.",
            "Snapshot compatibility requires an exact pack and Cleanroom profile context match.",
        ],
    )


def execute_world_restore(
    root: Path,
    plan: Mapping[str, Any],
    *,
    authority_root: Path | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    workspace, storage = _validate_executable_plan(root, plan, "world-restore")
    timestamp = _utc(now)
    journal = _OperationJournal(
        workspace=workspace, storage=storage, plan=plan, timestamp=timestamp
    )
    staging: Path | None = None
    parent: Path | None = None
    try:
        profile, full_profile = _profile_fresh(
            workspace if authority_root is None else authority_root.expanduser().resolve(),
            plan,
        )
        template, audit, template_files, template_binding = _template_fresh(
            workspace, storage, profile, plan
        )
        snapshot_input = _plan_input(plan, "world-snapshot")
        snapshot_item = _current_inventory_item(workspace, snapshot_input)
        if snapshot_item["kind"] != "snapshot" or snapshot_item["problems"]:
            raise RuntimeManagerError("managed snapshot became unsafe after preview")
        snapshot = _load_snapshot_manifest(snapshot_item)
        if (
            snapshot["snapshot_id"] != snapshot_input["resource_id"]
            or _canonical_sha256(snapshot) != snapshot_input["binding_sha256"]
        ):
            raise RuntimeManagerError("snapshot identity changed after preview")
        if not _profiles_compatible(full_profile, snapshot["profile"]):
            raise RuntimeManagerError("snapshot is incompatible with the requested profile")
        payload = workspace / snapshot["content"]["payload_relative_path"]
        if not _inside(payload, Path(snapshot_item["path"])):
            raise RuntimeManagerError("snapshot payload escapes its managed snapshot")
        _assert_no_symlink_ancestors(payload, Path(snapshot_item["path"]))
        payload_files = _file_manifest(payload)
        comparable = [
            {key: row[key] for key in ("relative_path", "size_bytes", "sha256")}
            for row in payload_files
        ]
        expected = [
            {key: row[key] for key in ("relative_path", "size_bytes", "sha256")}
            for row in snapshot["files"]
        ]
        if (
            comparable != expected
            or _canonical_sha256(payload_files) != snapshot["content"]["tree_sha256"]
        ):
            raise RuntimeManagerError("snapshot payload no longer matches its manifest")
        parameters = plan["parameters"]
        destination = _runtime_destination(
            storage, plan["profile"]["pack"], parameters["label"]
        )
        if plan["effects"]["creates"][0] != _relative_to_workspace(
            destination, workspace
        ):
            raise RuntimeManagerError("restore destination does not match plan parameters")
        if destination.exists() or destination.is_symlink():
            raise RuntimeManagerError(f"restore destination already exists: {destination}")
        journal.finish()

        journal.advance(1)
        parent = destination.parent
        _assert_no_symlink_ancestors(parent, storage)
        parent.mkdir(parents=True, exist_ok=True)
        _assert_no_symlink_ancestors(parent, storage)
        staging = parent / f".{destination.name}.{uuid.uuid4().hex}.partial"
        provision_runtime(template, staging, profile)
        copied_template_bytes = _verify_provisioned_template(
            template,
            staging,
            audit,
            template_files,
            template_binding,
        )
        journal.finish(bytes_processed=copied_template_bytes)

        journal.advance(2)
        configuration = configure_runtime(
            staging,
            profile=profile,
            seed=snapshot["source"]["seed"],
            world_type=snapshot["source"]["world_type"],
            server_port=parameters["server_port"],
            level_name=parameters["level_name"],
        )
        journal.finish()

        journal.advance(3)
        restored_world = Path(configuration["level_path"])
        _copy_tree_independent(payload, restored_world)
        restored_files = _file_manifest(restored_world)
        restored_comparable = [
            {key: row[key] for key in ("relative_path", "size_bytes", "sha256")}
            for row in restored_files
        ]
        if restored_comparable != expected:
            raise RuntimeManagerError("restored world does not match the snapshot manifest")
        journal.finish(bytes_processed=snapshot["content"]["logical_bytes"])

        journal.advance(4)
        reference = {
            "relation": "uses",
            "resource_id": snapshot["snapshot_id"],
            "path": str(Path(snapshot_item["path"])),
            "sha256": snapshot["content"]["tree_sha256"],
            "required": True,
        }
        runtime_manifest = _runtime_manifest(
            workspace=workspace,
            storage=storage,
            runtime=staging,
            canonical_runtime=destination,
            timestamp=timestamp,
            full_profile=full_profile,
            template=template,
            audit=audit,
            template_binding=template_binding,
            world={
                "level_name": parameters["level_name"],
                "relative_path": parameters["level_name"],
                "state": "present",
                "seed": snapshot["source"]["seed"],
                "world_type": snapshot["source"]["world_type"],
            },
            references=[reference],
        )
        _write_json(staging / RUNTIME_MANIFEST, runtime_manifest)
        if destination.exists() or destination.is_symlink():
            raise RuntimeManagerError(f"restore destination appeared during copy: {destination}")
        os.replace(staging, destination)
        journal.finish()
    except Exception as exc:
        if (
            staging is not None
            and parent is not None
            and staging.exists()
            and _inside(staging, parent)
        ):
            shutil.rmtree(staging, ignore_errors=True)
        if isinstance(exc, RuntimeProviderError):
            failure = RuntimeManagerError(str(exc))
            _record_operation_failure(journal, failure)
            raise failure from exc
        _record_operation_failure(journal, exc)
        raise
    return _complete_receipt(
        workspace=workspace,
        storage=storage,
        plan=plan,
        timestamp=timestamp,
        resource_ids=[runtime_manifest["runtime_id"]],
        output_paths=[
            _relative_to_workspace(destination, workspace),
            _relative_to_workspace(destination / parameters["level_name"], workspace),
        ],
        journal=journal,
    )


def _next_trash_destination(
    storage: Path, item: Mapping[str, Any], timestamp: str
) -> Path:
    trash_root = storage / "trash"
    stem = (
        f"runtime-manager--{_compact_timestamp(timestamp)}--"
        f"{item['item_id'].split(':')[-1][:12]}--{Path(item['path']).name}"
    )
    known_paths = _declared_quarantine_paths(storage)
    counter = 1
    while True:
        suffix = "" if counter == 1 else f"--{counter}"
        destination = trash_root / f"{stem}{suffix}"
        relative = _relative_to_workspace(destination, storage.parent)
        if (
            not destination.exists()
            and not destination.is_symlink()
            and relative not in known_paths
        ):
            return destination
        counter += 1


def _trash_record(
    *,
    timestamp: str,
    item: Mapping[str, Any],
    destination_relative: str,
) -> dict[str, Any]:
    material = {
        "created_at": timestamp,
        "original_item_id": item["item_id"],
        "original_resource_id": item["resource_id"],
        "original_relative_path": item["relative_path"],
        "quarantine_relative_path": destination_relative,
        "original_observation_sha256": item["observation_sha256"],
    }
    return {
        "format": "workbench-storage-trash-transaction-v1",
        "schema_version": 1,
        "canonicalization_id": CANONICALIZATION_ID,
        "trash_id": _identity(TRASH_PREFIX, material),
        **material,
    }


def _validate_trash_record(
    value: Mapping[str, Any],
    *,
    storage: Path,
    item: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    keys = {
        "format",
        "schema_version",
        "canonicalization_id",
        "trash_id",
        "created_at",
        "original_item_id",
        "original_resource_id",
        "original_relative_path",
        "quarantine_relative_path",
        "original_observation_sha256",
    }
    visible = {key: entry for key, entry in value.items() if not key.startswith("_")}
    if set(visible) != keys:
        raise RuntimeManagerError("runtime-manager trash record has an unsupported V1 shape")
    if (
        visible["format"] != "workbench-storage-trash-transaction-v1"
        or visible["schema_version"] != 1
        or visible["canonicalization_id"] != CANONICALIZATION_ID
    ):
        raise RuntimeManagerError("unsupported runtime-manager trash record version")
    cleanup_plan_id = value.get("_cleanup_plan_id")
    if cleanup_plan_id is not None and (
        not isinstance(cleanup_plan_id, str)
        or not re.fullmatch(re.escape(PLAN_PREFIX) + r"[0-9a-f]{64}", cleanup_plan_id)
    ):
        raise RuntimeManagerError("runtime-manager trash cleanup plan ID is invalid")
    material = {
        key: visible[key]
        for key in (
            "created_at",
            "original_item_id",
            "original_resource_id",
            "original_relative_path",
            "quarantine_relative_path",
            "original_observation_sha256",
        )
    }
    if visible["trash_id"] != _identity(TRASH_PREFIX, material):
        raise RuntimeManagerError("runtime-manager trash identity does not match its record")
    if not isinstance(visible["created_at"], str) or _utc(visible["created_at"]) != visible["created_at"]:
        raise RuntimeManagerError("runtime-manager trash timestamp is invalid")
    if not isinstance(visible["original_item_id"], str) or not ITEM_ID_RE.fullmatch(
        visible["original_item_id"]
    ):
        raise RuntimeManagerError("runtime-manager trash original item ID is invalid")
    if not isinstance(visible["original_observation_sha256"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", visible["original_observation_sha256"]
    ):
        raise RuntimeManagerError("runtime-manager trash observation digest is invalid")
    if visible["original_resource_id"] is not None and _safe_identifier(
        visible["original_resource_id"]
    ) != visible["original_resource_id"]:
        raise RuntimeManagerError("runtime-manager trash original resource ID is invalid")
    for key in ("original_relative_path", "quarantine_relative_path"):
        path = visible[key]
        if (
            not isinstance(path, str)
            or not path.startswith(".workbench/")
            or ".." in Path(path).parts
        ):
            raise RuntimeManagerError(f"trash record {key} is unsafe")
    original_parts = Path(visible["original_relative_path"]).parts
    quarantine_parts = Path(visible["quarantine_relative_path"]).parts
    if (
        len(original_parts) < 3
        or original_parts[0] != ".workbench"
        or (original_parts[1] not in CLEANUP_CATEGORIES and not (
            len(original_parts) == 3 and original_parts[1] == "check-attempts"
            and re.fullmatch(r"[a-z][a-z0-9-]*-[0-9a-f]{32}", original_parts[2])))
    ):
        raise RuntimeManagerError(
            "trash record original path is not a cleanup-addressable Workbench resource"
        )
    expected_original_id = _identity(
        ITEM_PREFIX,
        {
            "storage_root": str(storage),
            "relative_path": visible["original_relative_path"],
        },
    )
    if visible["original_item_id"] != expected_original_id:
        raise RuntimeManagerError("trash record original item ID does not bind its path")
    if len(quarantine_parts) != 3 or quarantine_parts[:2] != (
        ".workbench",
        "trash",
    ):
        raise RuntimeManagerError(
            "trash record quarantine must be one direct child of .workbench/trash"
        )
    quarantine_name = quarantine_parts[2]
    match = re.fullmatch(
        r"runtime-manager--[0-9]{14}--([0-9a-f]{12})--[^/\r\n\x00]+",
        quarantine_name,
    )
    if match is None or match.group(1) != visible["original_item_id"].split(":")[-1][:12]:
        raise RuntimeManagerError("trash record quarantine name does not bind its original item")
    record_path_value = value.get("_record_path")
    if record_path_value is not None:
        record_path = Path(record_path_value)
        expected_ledger = storage / "runtime-manager/trash" / (
            visible["trash_id"].split(":")[-1] + ".json"
        )
        if record_path != expected_ledger:
            raise RuntimeManagerError("trash record filename does not bind its trash ID")
    if item is not None and (
        visible["quarantine_relative_path"] != item["relative_path"]
        or visible["trash_id"] != item["resource_id"]
        or visible["original_observation_sha256"] != item["observation_sha256"]
        or Path(item["path"]) != storage.parent / visible["quarantine_relative_path"]
    ):
        raise RuntimeManagerError("trash record does not match the selected inventory item")
    return visible


def _trash_record_context(
    storage: Path, item: Mapping[str, Any]
) -> tuple[dict[str, Any], str]:
    value = _trash_records(storage).get(item["relative_path"])
    if value is None:
        raise RuntimeManagerError("selected trash item lacks a manager transaction record")
    status = value.get("_cleanup_receipt_status")
    if status not in {"complete", "interrupted"}:
        raise RuntimeManagerError("selected trash item has no valid cleanup receipt state")
    return _validate_trash_record(value, storage=storage, item=item), status


def plan_cleanup(
    root: Path,
    *,
    selector: str,
    allow_review: bool = False,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    workspace, storage = _workspace(root)
    timestamp = _utc(now)
    inventory = inventory_storage(workspace, now=now)
    item = resolve_inventory_item(inventory, selector)
    destination = _next_trash_destination(storage, item, timestamp)
    destination_relative = _relative_to_workspace(destination, workspace)
    record = _trash_record(
        timestamp=timestamp,
        item=item,
        destination_relative=destination_relative,
    )
    blockers: list[dict[str, Any]] = []
    deletion_state = item["deletion"]["state"]
    if item["kind"] == "trash":
        blockers.append(
            _blocker(
                "already-trash",
                "Use restore or purge for a manager-owned trash item.",
                item["item_id"],
            )
        )
    elif deletion_state == "review" and not allow_review:
        blockers.append(
            _blocker(
                "review-required",
                "This item requires an explicit allow-review override before cleanup.",
                item["item_id"],
            )
        )
    elif deletion_state not in {"eligible", "review"}:
        blockers.append(
            _blocker(
                "deletion-protected",
                f"The inventory classifies this item as {deletion_state}.",
                item["item_id"],
            )
        )
    if item["problems"]:
        blockers.append(
            _blocker(
                "unsafe-filesystem-entry",
                "Cleanup refuses items with symlinks, special entries, or unreadable paths.",
                item["item_id"],
            )
        )
    source = _endpoint(
        item_id=item["item_id"],
        resource_id=item["resource_id"],
        path=item["path"],
        relative_path=item["relative_path"],
        observation_sha256=item["observation_sha256"],
    )
    target = _endpoint(
        resource_id=record["trash_id"],
        path=str(destination),
        relative_path=destination_relative,
    )
    action = _action(
        "move-to-trash",
        "Atomically move one exact inventory item into manager-owned recoverable trash.",
        source=source,
        destination=target,
        expected_logical_bytes=item["size"]["logical_bytes"],
        recoverable=True,
    )
    command = shlex.join(
        [
            "workbench",
            "storage",
            "cleanup",
            item["item_id"],
            *( ["--allow-review"] if allow_review else [] ),
            "--apply",
        ]
    )
    return _plan(
        operation="cleanup",
        timestamp=timestamp,
        workspace=workspace,
        storage=storage,
        command=command,
        parameters=_parameters(allow_review=allow_review),
        profile=None,
        inputs=[
            _input(
                role="storage-item",
                item_id=item["item_id"],
                resource_id=item["resource_id"],
                path=item["path"],
                relative_path=item["relative_path"],
                observation_sha256=item["observation_sha256"],
                binding_sha256=item["observation_sha256"],
            )
        ],
        actions=[action],
        moves=[{"source": item["relative_path"], "destination": destination_relative}],
        retains=[destination_relative],
        expected_reclaimed_bytes=0,
        recovery_state="restore-trash",
        recovery_instruction="Restore the exact manager trash ID while the original path remains absent.",
        blockers=blockers,
        limitations=[
            "Moving to recoverable trash does not reclaim disk space; a separately confirmed purge does.",
            "Only the exact selected inventory item is moved; paths and globs are not accepted.",
        ],
    )


@check_lifecycle.collection
def execute_cleanup(
    root: Path,
    plan: Mapping[str, Any],
    *,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    workspace, storage = _validate_executable_plan(root, plan, "cleanup")
    timestamp = _utc(now)
    journal = _OperationJournal(
        workspace=workspace, storage=storage, plan=plan, timestamp=timestamp
    )
    moved = False
    record_written = False
    processed_bytes = 0
    source: Path | None = None
    destination: Path | None = None
    ledger: Path | None = None
    try:
        trash_ledger = _ensure_ledger_directory(storage, "trash")
        planned_input = _plan_input(plan, "storage-item")
        item = _current_inventory_item(workspace, planned_input)
        allow_review = plan["parameters"]["allow_review"]
        state = item["deletion"]["state"]
        if state != "eligible" and not (state == "review" and allow_review):
            raise RuntimeManagerError("item is no longer eligible for the planned cleanup")
        if item["kind"] == "trash" or item["problems"]:
            raise RuntimeManagerError("cleanup refuses trash or unsafe filesystem entries")
        source = Path(item["path"])
        action = plan["actions"][0]
        destination = Path(action["destination"]["path"])
        destination_relative = action["destination"]["relative_path"]
        if (
            action["source"]["item_id"] != item["item_id"]
            or action["expected_logical_bytes"] != item["size"]["logical_bytes"]
        ):
            raise RuntimeManagerError(
                "cleanup action source or byte expectation does not match its input"
            )
        if not _inside(source, storage) or source in {workspace, storage}:
            raise RuntimeManagerError("cleanup source is outside exact Workbench storage")
        if not _inside(destination, storage / "trash"):
            raise RuntimeManagerError("cleanup destination is outside manager trash")
        _assert_no_symlink_ancestors(source, storage)
        if destination.exists() or destination.is_symlink():
            raise RuntimeManagerError(f"cleanup trash destination already exists: {destination}")
        _assert_no_symlink_ancestors(destination.parent, storage)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _assert_no_symlink_ancestors(destination.parent, storage)
        if source.lstat().st_dev != destination.parent.lstat().st_dev:
            raise RuntimeManagerError(
                "recoverable cleanup requires an atomic same-filesystem move"
            )
        record = _validate_trash_record(
            _trash_record(
                timestamp=plan["created_at"],
                item=item,
                destination_relative=destination_relative,
            ),
            storage=storage,
        )
        if record["trash_id"] != action["destination"]["resource_id"]:
            raise RuntimeManagerError("cleanup trash identity does not match its plan")
        ledger = trash_ledger / (record["trash_id"].split(":")[-1] + ".json")
        if ledger.exists() or ledger.is_symlink():
            raise RuntimeManagerError("cleanup transaction record already exists")
        processed_bytes = item["size"]["logical_bytes"]
        durable_record = {**record, "_cleanup_plan_id": plan["plan_id"]}
        _validate_trash_record(durable_record, storage=storage)
        _write_json(ledger, durable_record)
        record_written = True
        os.replace(source, destination)
        moved = True
        journal.finish(bytes_processed=processed_bytes)
        return _complete_receipt(
            workspace=workspace,
            storage=storage,
            plan=plan,
            timestamp=timestamp,
            trash_id=record["trash_id"],
            output_paths=[destination_relative],
            journal=journal,
        )
    except Exception as exc:
        failure: BaseException = exc
        record_can_be_removed = record_written and not moved
        if (
            moved
            and source is not None
            and destination is not None
            and destination.exists()
            and not source.exists()
        ):
            try:
                os.replace(destination, source)
            except OSError as rollback_exc:
                failure = RuntimeManagerError(
                    f"cleanup record failed and rollback also failed: {rollback_exc}"
                )
            else:
                record_can_be_removed = record_written
        if record_can_be_removed and ledger is not None:
            try:
                ledger.unlink()
            except OSError as ledger_exc:
                failure = RuntimeManagerError(
                    "cleanup did not retain its moved payload, but its transaction "
                    f"record could not be removed: {ledger_exc}"
                )
        _record_operation_failure(
            journal,
            failure,
            bytes_processed=processed_bytes if moved else 0,
        )
        if failure is not exc:
            raise failure from exc
        raise


def plan_restore_trash(
    root: Path,
    *,
    selector: str,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    workspace, storage = _workspace(root)
    timestamp = _utc(now)
    inventory = inventory_storage(workspace, now=now)
    item = resolve_inventory_item(inventory, selector)
    if item["kind"] != "trash" or item["custody"]["state"] != "managed":
        raise RuntimeManagerError("restore requires one exact manager-owned trash item")
    record, _cleanup_status = _trash_record_context(storage, item)
    destination = workspace / record["original_relative_path"]
    blockers: list[dict[str, Any]] = []
    if not check_lifecycle.restorable(item):
        blockers.append(
            _blocker(
                "trash-not-quiesced",
                "Trash restore requires an inactive, unchanged manager payload.",
                item["item_id"],
            )
        )
    if item["problems"]:
        blockers.append(
            _blocker(
                "unsafe-trash-entry",
                "Trash restore refuses symlinks, special entries, or unreadable paths.",
                item["item_id"],
            )
        )
    if destination.exists() or destination.is_symlink():
        blockers.append(
            _blocker(
                "restore-collision",
                "The original path is occupied; restore never overwrites it.",
                item["item_id"],
            )
        )
    source = _endpoint(
        item_id=item["item_id"],
        resource_id=record["trash_id"],
        path=item["path"],
        relative_path=item["relative_path"],
        observation_sha256=item["observation_sha256"],
    )
    target = _endpoint(
        item_id=record["original_item_id"],
        resource_id=record["original_resource_id"],
        path=str(destination),
        relative_path=record["original_relative_path"],
        observation_sha256=record["original_observation_sha256"],
    )
    action = _action(
        "restore-from-trash",
        "Atomically restore the quarantined resource only into its absent original path.",
        source=source,
        destination=target,
        expected_logical_bytes=item["size"]["logical_bytes"],
        recoverable=True,
    )
    return _plan(
        operation="restore-trash",
        timestamp=timestamp,
        workspace=workspace,
        storage=storage,
        command=shlex.join(
            ["workbench", "storage", "restore", item["item_id"], "--apply"]
        ),
        parameters=_parameters(),
        profile=None,
        inputs=[
            _input(
                role="trash",
                item_id=item["item_id"],
                resource_id=record["trash_id"],
                path=item["path"],
                relative_path=item["relative_path"],
                observation_sha256=item["observation_sha256"],
                binding_sha256=_canonical_sha256(record),
            )
        ],
        actions=[action],
        moves=[
            {
                "source": item["relative_path"],
                "destination": record["original_relative_path"],
            }
        ],
        retains=[record["original_relative_path"]],
        recovery_state="not-needed",
        recovery_instruction="The restored item can be sent to recoverable trash again through a new preview.",
        blockers=blockers,
        limitations=["Restore never merges with or overwrites an occupied original path."],
    )


@check_lifecycle.collection
def execute_restore_trash(
    root: Path,
    plan: Mapping[str, Any],
    *,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    workspace, storage = _validate_executable_plan(root, plan, "restore-trash")
    timestamp = _utc(now)
    journal = _OperationJournal(
        workspace=workspace, storage=storage, plan=plan, timestamp=timestamp
    )
    moved = False
    processed_bytes = 0
    source: Path | None = None
    destination: Path | None = None
    try:
        _ensure_ledger_directory(storage, "trash")
        planned_input = _plan_input(plan, "trash")
        item = _current_inventory_item(workspace, planned_input)
        if (
            item["kind"] != "trash"
            or not check_lifecycle.restorable(item)
            or item["problems"]
        ):
            raise RuntimeManagerError("selected trash item is no longer safe to restore")
        record, _cleanup_status = _trash_record_context(storage, item)
        if _canonical_sha256(record) != planned_input["binding_sha256"]:
            raise RuntimeManagerError("trash transaction record changed after preview")
        expected_source = _endpoint(
            item_id=item["item_id"],
            resource_id=record["trash_id"],
            path=item["path"],
            relative_path=item["relative_path"],
            observation_sha256=item["observation_sha256"],
        )
        source = Path(item["path"])
        destination = workspace / record["original_relative_path"]
        expected_destination = _endpoint(
            item_id=record["original_item_id"],
            resource_id=record["original_resource_id"],
            path=str(destination),
            relative_path=record["original_relative_path"],
            observation_sha256=record["original_observation_sha256"],
        )
        action = plan["actions"][0]
        if (
            action["source"] != expected_source
            or action["destination"] != expected_destination
            or action["expected_logical_bytes"] != item["size"]["logical_bytes"]
            or plan["effects"]["retains"] != [record["original_relative_path"]]
        ):
            raise RuntimeManagerError(
                "trash restore action does not match its transaction record"
            )
        if destination.exists() or destination.is_symlink():
            raise RuntimeManagerError(f"restore destination already exists: {destination}")
        if not _inside(destination, storage) or destination in {workspace, storage}:
            raise RuntimeManagerError(
                "trash record original path is outside exact Workbench storage"
            )
        _assert_no_symlink_ancestors(source, storage)
        _assert_no_symlink_ancestors(destination.parent, storage)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _assert_no_symlink_ancestors(destination.parent, storage)
        if source.lstat().st_dev != destination.parent.lstat().st_dev:
            raise RuntimeManagerError(
                "trash restore requires an atomic same-filesystem move"
            )
        processed_bytes = item["size"]["logical_bytes"]
        os.replace(source, destination)
        moved = True
        journal.finish(bytes_processed=processed_bytes)
        return _complete_receipt(
            workspace=workspace,
            storage=storage,
            plan=plan,
            timestamp=timestamp,
            restored_item_id=record["original_item_id"],
            output_paths=[record["original_relative_path"]],
            journal=journal,
        )
    except Exception as exc:
        failure: BaseException = exc
        if (
            moved
            and source is not None
            and destination is not None
            and destination.exists()
            and not source.exists()
        ):
            try:
                os.replace(destination, source)
            except OSError as rollback_exc:
                failure = RuntimeManagerError(
                    f"trash restore failed and rollback also failed: {rollback_exc}"
                )
        _record_operation_failure(
            journal,
            failure,
            bytes_processed=processed_bytes if moved else 0,
        )
        if failure is not exc:
            raise failure from exc
        raise


def plan_purge_trash(
    root: Path,
    *,
    selector: str,
    confirmation: str | None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    workspace, storage = _workspace(root)
    timestamp = _utc(now)
    inventory = inventory_storage(workspace, now=now)
    item = resolve_inventory_item(inventory, selector)
    if item["kind"] != "trash" or item["custody"]["state"] != "managed":
        raise RuntimeManagerError("purge requires one exact manager-owned trash item")
    record, cleanup_status = _trash_record_context(storage, item)
    supplied_confirmation = confirmation or "confirmation-required"
    blockers: list[dict[str, Any]] = []
    try:
        check_lifecycle.before_purge(workspace, item)
    except (ValueError, OSError) as exc:
        blockers.append(_blocker("check-evidence-export-required", str(exc), item["item_id"]))
    if cleanup_status != "complete":
        blockers.append(
            _blocker(
                "cleanup-interrupted",
                "Interrupted cleanup trash is restore-only and cannot be purged.",
                item["item_id"],
            )
        )
    if item["deletion"]["state"] != "review":
        blockers.append(
            _blocker(
                "trash-not-quiesced",
                "Permanent purge requires an inactive, unchanged manager payload.",
                item["item_id"],
            )
        )
    if confirmation != record["trash_id"]:
        blockers.append(
            _blocker(
                "confirmation-mismatch",
                "Permanent purge requires the exact manager trash resource ID.",
                item["item_id"],
            )
        )
    if item["problems"]:
        blockers.append(
            _blocker(
                "unsafe-trash-entry",
                "Purge refuses a trash item that changed to contain unsafe entries.",
                item["item_id"],
            )
        )
    source = _endpoint(
        item_id=item["item_id"],
        resource_id=record["trash_id"],
        path=item["path"],
        relative_path=item["relative_path"],
        observation_sha256=item["observation_sha256"],
    )
    action = _action(
        "purge-trash",
        "Permanently remove only the exact manager-owned quarantine item.",
        source=source,
        expected_logical_bytes=item["size"]["logical_bytes"],
        recoverable=False,
    )
    return _plan(
        operation="purge-trash",
        timestamp=timestamp,
        workspace=workspace,
        storage=storage,
        command=shlex.join(
            [
                "workbench",
                "storage",
                "purge",
                item["item_id"],
                "--confirm",
                supplied_confirmation,
            ]
        ),
        parameters=_parameters(confirmation=supplied_confirmation),
        profile=None,
        inputs=[
            _input(
                role="trash",
                item_id=item["item_id"],
                resource_id=record["trash_id"],
                path=item["path"],
                relative_path=item["relative_path"],
                observation_sha256=item["observation_sha256"],
                binding_sha256=_canonical_sha256(record),
            )
        ],
        actions=[action],
        removes=[item["relative_path"]],
        expected_reclaimed_bytes=item["size"]["exclusive_allocated_bytes"],
        recovery_state="irreversible",
        recovery_instruction="Permanent purge cannot be undone by Workbench.",
        confirmation_token=record["trash_id"],
        blockers=blockers,
        limitations=[
            "Purge is irreversible and is limited to a valid manager-owned trash transaction."
        ],
    )


def _purge_exact(
    path: Path,
    trash_root: Path,
    *,
    progress: list[int] | None = None,
) -> int:
    if path.parent != trash_root:
        raise RuntimeManagerError("purge target is not one exact child of manager trash")
    metadata = path.lstat()
    if path.is_symlink() or not (
        stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
    ):
        raise RuntimeManagerError(
            "purge target must be one regular non-symlink file or directory"
        )
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow
    processed = progress if progress is not None else [0]
    try:
        trash_fd = os.open(trash_root, directory_flags)
    except OSError as exc:
        raise RuntimeManagerError(
            f"cannot safely open manager trash root: {trash_root}: {exc}"
        ) from exc
    try:
        current = os.stat(path.name, dir_fd=trash_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino, current.st_mode) != (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
        ):
            raise RuntimeManagerError("purge target changed before it could be opened")
        if stat.S_ISREG(current.st_mode):
            allocated = _allocated(current) if current.st_nlink == 1 else 0
            try:
                os.unlink(path.name, dir_fd=trash_fd)
            except OSError as exc:
                raise RuntimeManagerError(
                    f"cannot purge file {path.name!r}: {exc}"
                ) from exc
            processed[0] += allocated
            return processed[0]
        try:
            root_fd = os.open(path.name, directory_flags, dir_fd=trash_fd)
        except OSError as exc:
            raise RuntimeManagerError(
                f"cannot safely open purge target: {path}: {exc}"
            ) from exc
        opened_root = os.fstat(root_fd)
        if (opened_root.st_dev, opened_root.st_ino) != (
            metadata.st_dev,
            metadata.st_ino,
        ):
            os.close(root_fd)
            raise RuntimeManagerError("purge target changed while it was opened")

        def purge_directory(directory_fd: int) -> None:
            directory_device = os.fstat(directory_fd).st_dev
            for name in sorted(os.listdir(directory_fd)):
                try:
                    entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                except OSError as exc:
                    raise RuntimeManagerError(
                        f"cannot inspect purge entry {name!r}: {exc}"
                    ) from exc
                if entry.st_dev != directory_device:
                    raise RuntimeManagerError(
                        f"purge refuses nested filesystem boundary: {name}"
                    )
                if stat.S_ISLNK(entry.st_mode):
                    raise RuntimeManagerError(f"purge refuses symlink entry: {name}")
                if stat.S_ISREG(entry.st_mode):
                    allocated = _allocated(entry) if entry.st_nlink == 1 else 0
                    try:
                        os.unlink(name, dir_fd=directory_fd)
                    except OSError as exc:
                        raise RuntimeManagerError(
                            f"cannot purge file {name!r}: {exc}"
                        ) from exc
                    processed[0] += allocated
                    continue
                if stat.S_ISDIR(entry.st_mode):
                    try:
                        child_fd = os.open(
                            name, directory_flags, dir_fd=directory_fd
                        )
                    except OSError as exc:
                        raise RuntimeManagerError(
                            f"purge directory changed or became unsafe {name!r}: {exc}"
                        ) from exc
                    opened_child = os.fstat(child_fd)
                    if (opened_child.st_dev, opened_child.st_ino) != (
                        entry.st_dev,
                        entry.st_ino,
                    ):
                        os.close(child_fd)
                        raise RuntimeManagerError(
                            f"purge directory changed while it was opened: {name}"
                        )
                    try:
                        purge_directory(child_fd)
                    finally:
                        os.close(child_fd)
                    try:
                        os.rmdir(name, dir_fd=directory_fd)
                    except OSError as exc:
                        raise RuntimeManagerError(
                            f"cannot purge directory {name!r}: {exc}"
                        ) from exc
                    continue
                raise RuntimeManagerError(f"purge refuses special entry: {name}")

        try:
            purge_directory(root_fd)
        finally:
            os.close(root_fd)
        try:
            os.rmdir(path.name, dir_fd=trash_fd)
        except OSError as exc:
            raise RuntimeManagerError(
                f"cannot remove emptied purge root {path}: {exc}"
            ) from exc
        return processed[0]
    finally:
        os.close(trash_fd)


@check_lifecycle.collection
def execute_purge_trash(
    root: Path,
    plan: Mapping[str, Any],
    *,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    workspace, storage = _validate_executable_plan(root, plan, "purge-trash")
    timestamp = _utc(now)
    journal = _OperationJournal(
        workspace=workspace, storage=storage, plan=plan, timestamp=timestamp
    )
    progress = [0]
    try:
        _ensure_ledger_directory(storage, "trash")
        planned_input = _plan_input(plan, "trash")
        item = _current_inventory_item(workspace, planned_input)
        if (
            item["kind"] != "trash"
            or item["deletion"]["state"] != "review"
            or item["problems"]
        ):
            raise RuntimeManagerError("selected trash item is no longer safe to purge")
        record, cleanup_status = _trash_record_context(storage, item)
        if cleanup_status != "complete":
            raise RuntimeManagerError(
                "interrupted cleanup trash is restore-only and cannot be purged"
            )
        if _canonical_sha256(record) != planned_input["binding_sha256"]:
            raise RuntimeManagerError("trash transaction record changed after preview")
        if (
            plan["parameters"]["confirmation"] != record["trash_id"]
            or plan["recovery"]["confirmation_token"] != record["trash_id"]
        ):
            raise RuntimeManagerError("purge confirmation does not match the exact trash ID")
        if (
            plan["actions"][0]["expected_logical_bytes"]
            != item["size"]["logical_bytes"]
            or plan["effects"]["expected_reclaimed_bytes"]
            != item["size"]["exclusive_allocated_bytes"]
        ):
            raise RuntimeManagerError(
                "purge byte expectations do not match the current trash item"
            )
        check_lifecycle.before_purge(workspace, item)
        target = Path(item["path"])
        _assert_no_symlink_ancestors(target, storage)
        purged_bytes = _purge_exact(
            target, storage / "trash", progress=progress
        )
        journal.finish(bytes_processed=progress[0])
        return _complete_receipt(
            workspace=workspace,
            storage=storage,
            plan=plan,
            timestamp=timestamp,
            purged_bytes=purged_bytes,
            journal=journal,
        )
    except Exception as exc:
        _record_operation_failure(
            journal, exc, bytes_processed=progress[0]
        )
        raise


def _human_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


def render_inventory(report: Mapping[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        f"Workbench storage: {report['storage_root']}",
        (
            f"{totals['item_count']} items; { _human_bytes(totals['unique_allocated_bytes']) } "
            f"unique allocated; { _human_bytes(totals['eligible_bytes']) } eligible"
        ),
    ]
    for item in report["items"]:
        lines.append(
            f"{item['item_id']}  {item['kind']:<16} "
            f"{item['deletion']['state']:<9} "
            f"{_human_bytes(item['size']['exclusive_allocated_bytes']):>10}  "
            f"{item['relative_path']}"
        )
    if not report["items"]:
        lines.append("No .workbench storage items were observed.")
    return "\n".join(lines) + "\n"


def render_operation(value: Mapping[str, Any]) -> str:
    operation = value.get("operation", "unknown-operation")
    status = value.get("status", "unknown")
    identifier = value.get("plan_id", value.get("receipt_id", "unknown"))
    lines = [f"{operation}: {status}", f"ID: {identifier}"]
    if value.get("format") == PLAN_FORMAT:
        lines.append("Preview only: no local state has changed.")
        for blocker in value.get("blockers", []):
            lines.append(f"BLOCKED {blocker['code']}: {blocker['detail']}")
        for action in value.get("actions", []):
            lines.append(f"- {action['kind']}: {action['purpose']}")
        lines.append(f"Recovery: {value['recovery']['instruction']}")
    elif value.get("format") == RECEIPT_FORMAT:
        if value.get('operation') == 'purge-trash':
            lines.append('File allocation unlinked: ' + _human_bytes(value['result']['purged_bytes']))
            lines.append('Filesystem free-space movement is recorded separately in runtime-manager/reclaim-observations.')
        for path in value.get("result", {}).get("output_paths", []):
            lines.append(f"Output: {path}")
    return "\n".join(lines) + "\n"
