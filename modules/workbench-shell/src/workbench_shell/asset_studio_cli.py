"""Read-only local Minecraft asset-closure checks.

Asset Studio owns only filesystem closure over an explicitly selected
workspace.  It does not infer runtime registration, rendering success, or
translation ownership from source files alone.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
from typing import Any, Iterable, Mapping, Sequence, TextIO


FORMAT = "workbench-asset-closure-report-v1"
SCHEMA_VERSION = 1
MAX_ASSET_ROOTS = 64
MAX_ASSET_FILES = 50_000
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_LANG_BYTES = 8 * 1024 * 1024
IGNORED_DIRECTORIES = frozenset(
    {".git", ".gradle", ".idea", ".workbench", "build", "out", "target"}
)


class AssetStudioError(ValueError):
    """The explicit workspace or one of its local asset documents is invalid."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _report_id(value: Mapping[str, Any]) -> str:
    material = deepcopy(dict(value))
    material.pop("report_id", None)
    return "workbench-asset-closure-report:sha256:" + sha256(
        _canonical_bytes(material)
    ).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate object key: {key}")
        value[key] = item
    return value


def _read_json(path: Path) -> Any:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise AssetStudioError(f"cannot inspect asset JSON {path}: {exc}") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise AssetStudioError(f"asset JSON is not a regular unlinked file: {path}")
    if metadata.st_size > MAX_JSON_BYTES:
        raise AssetStudioError(
            f"asset JSON exceeds {MAX_JSON_BYTES} bytes: {path}"
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AssetStudioError(f"cannot read asset JSON {path}: {exc}") from exc
    if len(raw) != metadata.st_size:
        raise AssetStudioError(f"asset JSON changed while it was read: {path}")
    try:
        return json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number: {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AssetStudioError(f"invalid asset JSON {path}: {exc}") from exc


def _workspace(path: Path) -> Path:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AssetStudioError(f"cannot inspect workspace {path}: {exc}") from exc
    if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode) or not resolved.is_dir():
        raise AssetStudioError(
            f"workspace must be a real directory, not a symlink: {path}"
        )
    return resolved


def _discover_asset_roots(workspace: Path) -> tuple[Path, ...]:
    roots: list[Path] = []
    for current, directory_names, _ in os.walk(workspace, followlinks=False):
        directory_names[:] = sorted(
            name
            for name in directory_names
            if name not in IGNORED_DIRECTORIES
            and not (Path(current) / name).is_symlink()
        )
        current_path = Path(current)
        if current_path.name != "assets":
            continue
        # An explicit assets directory is valid by itself.  Within a larger
        # workspace, require the ordinary resources/.../assets shape so an
        # unrelated directory named "assets" is not silently treated as game
        # authority.
        if workspace.name == "assets" or "resources" in current_path.parts:
            roots.append(current_path.resolve(strict=True))
            directory_names[:] = []
        if len(roots) > MAX_ASSET_ROOTS:
            raise AssetStudioError(
                f"workspace contains more than {MAX_ASSET_ROOTS} asset roots"
            )
    if not roots:
        raise AssetStudioError(
            "no Minecraft assets directory was found; select an assets directory "
            "or a workspace containing resources/.../assets"
        )
    return tuple(sorted(set(roots), key=lambda value: value.as_posix()))


def _logical_path(path: Path, root: Path, category: str, suffix: str) -> tuple[str, str]:
    relative = path.relative_to(root)
    if len(relative.parts) < 3 or relative.parts[1] != category:
        raise AssetStudioError(f"asset path is outside {category}: {path}")
    namespace = relative.parts[0]
    tail = PurePosixPath(*relative.parts[2:]).as_posix()
    if not tail.endswith(suffix):
        raise AssetStudioError(f"asset path has the wrong suffix: {path}")
    return namespace, tail[: -len(suffix)]


def _collect_files(
    workspace: Path,
    roots: Iterable[Path],
) -> tuple[
    dict[str, list[Path]],
    dict[str, list[Path]],
    dict[str, list[Path]],
    tuple[Path, ...],
]:
    blockstates: dict[str, list[Path]] = defaultdict(list)
    models: dict[str, list[Path]] = defaultdict(list)
    textures: dict[str, list[Path]] = defaultdict(list)
    lang_files: list[Path] = []
    file_count = 0
    for root in roots:
        for current, directory_names, file_names in os.walk(root, followlinks=False):
            directory_names[:] = sorted(
                name
                for name in directory_names
                if not (Path(current) / name).is_symlink()
            )
            for name in sorted(file_names):
                path = Path(current) / name
                if path.is_symlink():
                    continue
                file_count += 1
                if file_count > MAX_ASSET_FILES:
                    raise AssetStudioError(
                        f"asset inventory exceeds {MAX_ASSET_FILES} files"
                    )
                relative = path.relative_to(root)
                if len(relative.parts) < 3:
                    continue
                category = relative.parts[1]
                if category == "blockstates" and name.endswith(".json"):
                    namespace, tail = _logical_path(path, root, category, ".json")
                    blockstates[f"{namespace}:{tail}"].append(path)
                elif category == "models" and name.endswith(".json"):
                    namespace, tail = _logical_path(path, root, category, ".json")
                    models[f"{namespace}:{tail}"].append(path)
                elif category == "textures" and name.endswith(".png"):
                    namespace, tail = _logical_path(path, root, category, ".png")
                    textures[f"{namespace}:{tail}"].append(path)
                elif category == "lang" and name.endswith((".lang", ".json")):
                    lang_files.append(path)
    # Retain paths relative to the selected workspace in serialized output.
    for table in (blockstates, models, textures):
        for key in table:
            table[key] = sorted(table[key], key=lambda value: value.as_posix())
    return blockstates, models, textures, tuple(sorted(lang_files))


def _resource_ref(raw: str, *, kind: str) -> tuple[str, str]:
    value = raw.strip().replace("\\", "/")
    if not value or any(character in value for character in "\r\n\x00"):
        raise AssetStudioError(f"{kind} reference is empty or contains controls")
    if value.startswith("#"):
        return "variable", value[1:]
    if value.startswith("builtin/") or value.startswith("builtin:"):
        return "builtin", value
    if ":" in value:
        namespace, tail = value.split(":", 1)
    else:
        namespace, tail = "minecraft", value
    if kind == "texture":
        tail = tail.removeprefix("textures/").removesuffix(".png")
    elif kind == "model":
        tail = tail.removeprefix("models/").removesuffix(".json")
    logical = PurePosixPath(tail).as_posix()
    if (
        not namespace
        or not logical
        or logical.startswith("/")
        or ".." in PurePosixPath(logical).parts
    ):
        raise AssetStudioError(f"unsafe {kind} reference: {raw}")
    return namespace, logical


def _walk_model_references(value: object, pointer: str = "") -> list[tuple[str, str]]:
    references: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_pointer = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
            if key == "model" and isinstance(child, str):
                references.append((child_pointer, child))
            references.extend(_walk_model_references(child, child_pointer))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            references.extend(_walk_model_references(child, f"{pointer}/{index}"))
    return references


def _walk_texture_variables(value: object, pointer: str = "") -> list[tuple[str, str]]:
    references: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_pointer = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
            if key in {"texture", "particle"} and isinstance(child, str) and child.startswith("#"):
                references.append((child_pointer, child[1:]))
            references.extend(_walk_texture_variables(child, child_pointer))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            references.extend(_walk_texture_variables(child, f"{pointer}/{index}"))
    return references


def _relative(path: Path, workspace: Path) -> str:
    return path.relative_to(workspace).as_posix()


def _one_local(
    table: Mapping[str, list[Path]],
    logical_id: str,
    *,
    reference_kind: str,
    source: Path,
    pointer: str,
    workspace: Path,
    required: list[dict[str, Any]],
    external: list[dict[str, Any]],
    broken: list[dict[str, Any]],
) -> Path | None:
    paths = table.get(logical_id, [])
    row = {
        "kind": reference_kind,
        "identity": logical_id,
        "source": _relative(source, workspace),
        "pointer": pointer,
    }
    if len(paths) == 1:
        required.append({**row, "state": "resolved", "path": _relative(paths[0], workspace)})
        return paths[0]
    if len(paths) > 1:
        broken.append(
            {
                **row,
                "state": "ambiguous-local",
                "paths": [_relative(path, workspace) for path in paths],
            }
        )
        return None
    if logical_id.startswith("minecraft:"):
        external.append(
            {
                **row,
                "state": "external-not-supplied",
                "reason": "the selected workspace does not contain the Minecraft namespace dependency",
            }
        )
        return None
    broken.append({**row, "state": "missing-local"})
    return None


def _load_localizations(
    workspace: Path,
    paths: Iterable[Path],
) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    entries: dict[str, list[str]] = defaultdict(list)
    diagnostics: list[dict[str, Any]] = []
    for path in paths:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise AssetStudioError(f"cannot inspect localization {path}: {exc}") from exc
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise AssetStudioError(f"localization is not a regular unlinked file: {path}")
        if metadata.st_size > MAX_LANG_BYTES:
            raise AssetStudioError(
                f"localization exceeds {MAX_LANG_BYTES} bytes: {path}"
            )
        relative = _relative(path, workspace)
        if path.suffix == ".json":
            value = _read_json(path)
            if not isinstance(value, dict):
                diagnostics.append(
                    {"path": relative, "state": "invalid", "reason": "locale JSON is not an object"}
                )
                continue
            for key, translated in value.items():
                if isinstance(key, str) and isinstance(translated, str):
                    entries[key].append(relative)
                else:
                    diagnostics.append(
                        {"path": relative, "state": "invalid-entry", "key": str(key)}
                    )
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise AssetStudioError(f"cannot read localization {path}: {exc}") from exc
        for line_number, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                diagnostics.append(
                    {"path": relative, "line": line_number, "state": "invalid-entry"}
                )
                continue
            key, _ = line.split("=", 1)
            if not key.strip():
                diagnostics.append(
                    {"path": relative, "line": line_number, "state": "invalid-entry"}
                )
                continue
            entries[key.strip()].append(relative)
    return entries, diagnostics


def _identity(value: str | None) -> tuple[str, str] | None:
    if value is None:
        return None
    if value.count(":") != 1:
        raise AssetStudioError("--identity must use namespace:path")
    namespace, path = value.split(":", 1)
    if (
        not namespace
        or not path
        or any(character in value for character in "\r\n\x00")
        or path.startswith("/")
        or ".." in PurePosixPath(path).parts
    ):
        raise AssetStudioError("--identity must use a safe namespace:path")
    return namespace, PurePosixPath(path).as_posix()


def check_assets(
    workspace: Path,
    *,
    identity: str | None = None,
    translation_keys: Sequence[str] = (),
) -> dict[str, Any]:
    """Return one bounded closure report without modifying the workspace."""

    selected_workspace = _workspace(workspace)
    selected_identity = _identity(identity)
    roots = _discover_asset_roots(selected_workspace)
    blockstates, models, textures, lang_files = _collect_files(
        selected_workspace, roots
    )
    duplicate_resources = [
        {
            "kind": kind,
            "identity": logical_id,
            "paths": [_relative(path, selected_workspace) for path in paths],
        }
        for kind, table in (
            ("blockstate", blockstates),
            ("model", models),
            ("texture", textures),
        )
        for logical_id, paths in sorted(table.items())
        if len(paths) > 1
    ]

    documents: dict[str, Any] = {}
    required: list[dict[str, Any]] = []
    external: list[dict[str, Any]] = []
    broken: list[dict[str, Any]] = []
    cycles: list[list[str]] = []

    if selected_identity is None:
        blockstate_queue = list(sorted(blockstates))
        model_queue = list(sorted(models))
    else:
        namespace, path = selected_identity
        blockstate_queue = [f"{namespace}:{path}"] if f"{namespace}:{path}" in blockstates else []
        model_queue = [
            logical_id
            for logical_id in (f"{namespace}:block/{path}", f"{namespace}:item/{path}")
            if logical_id in models
        ]
        if not blockstate_queue and not model_queue:
            broken.append(
                {
                    "kind": "identity",
                    "identity": identity,
                    "state": "not-found",
                    "reason": "no matching blockstate, block model, or item model was found",
                }
            )

    seen_blockstates: set[str] = set()
    seen_models: set[str] = set()
    parent_by_model: dict[str, str] = {}
    texture_bindings: dict[str, dict[str, str]] = {}
    texture_uses: dict[str, list[tuple[str, str]]] = defaultdict(list)

    while blockstate_queue:
        logical_id = blockstate_queue.pop(0)
        if logical_id in seen_blockstates:
            continue
        seen_blockstates.add(logical_id)
        paths = blockstates.get(logical_id, [])
        if len(paths) != 1:
            continue
        path = paths[0]
        value = _read_json(path)
        documents[_relative(path, selected_workspace)] = value
        for pointer, raw_reference in _walk_model_references(value):
            namespace, tail = _resource_ref(raw_reference, kind="model")
            if namespace in {"builtin", "variable"}:
                continue
            referenced = f"{namespace}:{tail}"
            resolved = _one_local(
                models,
                referenced,
                reference_kind="model",
                source=path,
                pointer=pointer,
                workspace=selected_workspace,
                required=required,
                external=external,
                broken=broken,
            )
            if resolved is not None:
                model_queue.append(referenced)

    while model_queue:
        logical_id = model_queue.pop(0)
        if logical_id in seen_models:
            continue
        seen_models.add(logical_id)
        paths = models.get(logical_id, [])
        if len(paths) != 1:
            continue
        path = paths[0]
        value = _read_json(path)
        if not isinstance(value, dict):
            broken.append(
                {
                    "kind": "model",
                    "identity": logical_id,
                    "state": "invalid-shape",
                    "source": _relative(path, selected_workspace),
                }
            )
            continue
        documents[_relative(path, selected_workspace)] = value
        raw_textures = value.get("textures")
        texture_bindings[logical_id] = (
            {
                str(key): str(item)
                for key, item in raw_textures.items()
                if isinstance(key, str) and isinstance(item, str)
            }
            if isinstance(raw_textures, dict)
            else {}
        )
        texture_uses[logical_id].extend(_walk_texture_variables(value))
        parent = value.get("parent")
        if isinstance(parent, str):
            namespace, tail = _resource_ref(parent, kind="model")
            if namespace != "builtin":
                referenced = f"{namespace}:{tail}"
                parent_by_model[logical_id] = referenced
                resolved = _one_local(
                    models,
                    referenced,
                    reference_kind="model-parent",
                    source=path,
                    pointer="/parent",
                    workspace=selected_workspace,
                    required=required,
                    external=external,
                    broken=broken,
                )
                if resolved is not None:
                    model_queue.append(referenced)
        for key, raw_reference in texture_bindings[logical_id].items():
            namespace, tail = _resource_ref(raw_reference, kind="texture")
            if namespace == "variable":
                texture_uses[logical_id].append((f"/textures/{key}", tail))
                continue
            if namespace == "builtin":
                continue
            _one_local(
                textures,
                f"{namespace}:{tail}",
                reference_kind="texture",
                source=path,
                pointer=f"/textures/{key}",
                workspace=selected_workspace,
                required=required,
                external=external,
                broken=broken,
            )
        for pointer, raw_reference in _walk_model_references(value):
            namespace, tail = _resource_ref(raw_reference, kind="model")
            if namespace in {"builtin", "variable"}:
                continue
            referenced = f"{namespace}:{tail}"
            resolved = _one_local(
                models,
                referenced,
                reference_kind="model",
                source=path,
                pointer=pointer,
                workspace=selected_workspace,
                required=required,
                external=external,
                broken=broken,
            )
            if resolved is not None:
                model_queue.append(referenced)

    def resolve_variable(model_id: str, variable: str) -> tuple[str, str] | None:
        visited: list[str] = []
        current = model_id
        selected = variable
        while current in texture_bindings or current in parent_by_model:
            marker = f"{current}#{selected}"
            if marker in visited:
                cycles.append([*visited[visited.index(marker) :], marker])
                return None
            visited.append(marker)
            raw = texture_bindings.get(current, {}).get(selected)
            if raw is not None:
                namespace, tail = _resource_ref(raw, kind="texture")
                if namespace == "variable":
                    selected = tail
                else:
                    return namespace, tail
            current = parent_by_model.get(current, "")
            if not current:
                break
        return None

    for model_id, uses in sorted(texture_uses.items()):
        source_paths = models.get(model_id, [])
        if len(source_paths) != 1:
            continue
        for pointer, variable in uses:
            resolution = resolve_variable(model_id, variable)
            if resolution is None:
                broken.append(
                    {
                        "kind": "texture-variable",
                        "identity": f"{model_id}#{variable}",
                        "state": "unresolved-local",
                        "source": _relative(source_paths[0], selected_workspace),
                        "pointer": pointer,
                    }
                )

    # Model-parent cycles do not require recursive file reads, but they do make
    # inherited model and texture closure indeterminate.
    for model_id in sorted(seen_models):
        chain: list[str] = []
        current = model_id
        while current in parent_by_model:
            if current in chain:
                cycle = [*chain[chain.index(current) :], current]
                if cycle not in cycles:
                    cycles.append(cycle)
                break
            chain.append(current)
            current = parent_by_model[current]
    for cycle in cycles:
        broken.append(
            {"kind": "model-cycle", "state": "cyclic-local", "identities": cycle}
        )

    localizations, localization_diagnostics = _load_localizations(
        selected_workspace, lang_files
    )
    explicit_keys = tuple(dict.fromkeys(translation_keys))
    if any(
        not key or len(key) > 8192 or any(character in key for character in "\r\n\x00")
        for key in explicit_keys
    ):
        raise AssetStudioError("--translation-key values must be bounded single-line text")
    expected_keys: list[dict[str, Any]] = []
    for key in explicit_keys:
        paths = sorted(set(localizations.get(key, [])))
        expected_keys.append(
            {"key": key, "basis": "explicit-user-input", "state": "present" if paths else "missing", "paths": paths}
        )
        if not paths:
            broken.append(
                {"kind": "localization", "identity": key, "state": "missing-explicit"}
            )

    conventional: list[dict[str, Any]] = []
    if selected_identity is not None:
        namespace, path = selected_identity
        candidates = (
            f"tile.{namespace}.{path.replace('/', '.')}.name",
            f"item.{namespace}.{path.replace('/', '.')}.name",
            f"block.{namespace}.{path.replace('/', '.')}",
            f"item.{namespace}.{path.replace('/', '.')}",
        )
        conventional = [
            {
                "key": key,
                "basis": "conventional-candidate-only",
                "state": "present" if key in localizations else "not-found",
                "paths": sorted(set(localizations.get(key, []))),
            }
            for key in candidates
        ]

    if duplicate_resources:
        broken.extend(
            {**row, "state": "ambiguous-local"} for row in duplicate_resources
        )
    broken.sort(key=lambda row: _canonical_bytes(row))
    required.sort(key=lambda row: _canonical_bytes(row))
    external.sort(key=lambda row: _canonical_bytes(row))
    attention = bool(broken or localization_diagnostics)
    limited = bool(external) or not explicit_keys
    status = "attention" if attention else ("limited" if limited else "complete")
    uncertainty = [
        "Source closure does not prove that an asset is registered, selected, rendered, or visible in a running client.",
        "Localization ownership cannot be derived from a resource identity alone; conventional keys are candidates unless the user supplies --translation-key.",
    ]
    if external:
        uncertainty.append(
            "Minecraft-namespace references not supplied by the workspace remain external and were not classified as missing local files."
        )
    report: dict[str, Any] = {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "report_id": "",
        "authority": {
            "owner": "Asset Studio",
            "basis": "read-only closure over explicitly selected local asset roots",
        },
        "workspace": str(selected_workspace),
        "asset_roots": [_relative(root, selected_workspace) or "." for root in roots],
        "selection": {
            "identity": identity,
            "mode": "identity" if identity is not None else "workspace",
        },
        "inventory": {
            "blockstates": len(blockstates),
            "models": len(models),
            "textures": len(textures),
            "localization_files": len(lang_files),
            "localization_keys": len(localizations),
        },
        "closure": {
            "visited_blockstates": sorted(seen_blockstates),
            "visited_models": sorted(seen_models),
            "resolved_local_references": required,
            "external_references": external,
            "broken_local_references": broken,
        },
        "localization": {
            "explicit_keys": expected_keys,
            "conventional_candidates": conventional,
            "diagnostics": localization_diagnostics,
            "state": (
                "explicit-checked"
                if explicit_keys
                else "candidate-only" if identity is not None else "inventory-only"
            ),
        },
        "summary": {
            "status": status,
            "broken_local_references": len(broken),
            "resolved_local_references": len(required),
            "external_references": len(external),
            "localization_diagnostics": len(localization_diagnostics),
        },
        "claims": {
            "local_filesystem_closure_complete": not attention,
            "runtime_registration_observed": False,
            "render_success_observed": False,
            "localization_ownership_proven": False,
        },
        "uncertainty": uncertainty,
    }
    report["report_id"] = _report_id(report)
    return report


def _render(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    selection = report["selection"]
    inventory = report["inventory"]
    closure = report["closure"]
    localization = report["localization"]
    lines = [
        f"Asset check: {summary['status']}",
        f"Workspace: {report['workspace']}",
        "Selection: " + (selection["identity"] or "all local assets"),
        (
            "Inventory: "
            f"{inventory['blockstates']} blockstate(s), "
            f"{inventory['models']} model(s), "
            f"{inventory['textures']} texture(s), "
            f"{inventory['localization_keys']} localization key(s)"
        ),
        (
            "Closure: "
            f"{summary['resolved_local_references']} local reference(s) resolved, "
            f"{summary['broken_local_references']} broken, "
            f"{summary['external_references']} external/not supplied"
        ),
        f"Localization: {localization['state']}",
    ]
    if closure["broken_local_references"]:
        lines.append("Broken local references:")
        for row in closure["broken_local_references"][:20]:
            identity = row.get("identity") or " -> ".join(row.get("identities", ()))
            lines.append(
                f"- {row.get('kind', 'asset')}: {identity} ({row.get('state', 'attention')})"
            )
        if len(closure["broken_local_references"]) > 20:
            lines.append("- More broken references are available in --json output.")
    if closure["external_references"]:
        lines.append(
            "External references were left unresolved because their dependency assets were not supplied."
        )
    lines.extend(
        (
            "Claim boundary: local files do not prove runtime registration or successful rendering.",
            "Use --translation-key for a localization requirement you want checked exactly.",
        )
    )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench assets",
        description=(
            "Check local Minecraft blockstate, model, texture, and localization "
            "references without changing the selected workspace."
        ),
    )
    actions = parser.add_subparsers(dest="action", required=True)
    check = actions.add_parser(
        "check",
        help="check one workspace or one namespaced asset identity",
    )
    check.add_argument("workspace", type=Path)
    check.add_argument(
        "--identity",
        help="optional exact namespace:path block or item identity",
    )
    check.add_argument(
        "--translation-key",
        action="append",
        default=[],
        help="exact localization key required by the caller; repeatable",
    )
    check.add_argument(
        "--json", action="store_true", help="emit the complete V1 closure report"
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    output: TextIO | None = None,
    error: TextIO | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    output = sys.stdout if output is None else output
    error = sys.stderr if error is None else error
    try:
        report = check_assets(
            args.workspace,
            identity=args.identity,
            translation_keys=args.translation_key,
        )
        if args.json:
            json.dump(report, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
        else:
            output.write(_render(report))
        return 1 if report["summary"]["status"] == "attention" else 0
    except (AssetStudioError, OSError, UnicodeError, ValueError) as exc:
        print(f"Asset check failed: {exc}", file=error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
