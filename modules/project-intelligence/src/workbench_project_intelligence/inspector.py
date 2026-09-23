"""Resolve exact read-only context for the current Workbench bootstrap."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import subprocess
import tomllib
from typing import Any

import yaml

from .git_observation import GitObservationError, run_git_observation


class ProjectInspectionError(ValueError):
    """Raised when an exact workspace context cannot be established."""


def _read_yaml_object(
    path: Path,
    label: str,
    *,
    source_bytes: bytes | None = None,
) -> tuple[dict[str, Any], str]:
    if source_bytes is None:
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise ProjectInspectionError(f"{label} cannot be read: {path}") from exc
    elif type(source_bytes) is bytes:
        payload = source_bytes
    else:
        raise ProjectInspectionError(f"{label} source bytes must be bytes: {path}")
    try:
        parsed = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise ProjectInspectionError(f"{label} is malformed: {path}") from exc
    if not isinstance(parsed, dict):
        raise ProjectInspectionError(f"{label} must be a YAML object: {path}")
    return parsed, sha256(payload).hexdigest()


def _required_string(record: dict[str, Any], field: str, label: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise ProjectInspectionError(f"{label} lacks {field}")
    return value


def _workspace_path(workspace: Path, relative: str, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ProjectInspectionError(f"{label} must be a relative path")
    path = Path(relative)
    if path.is_absolute():
        raise ProjectInspectionError(f"{label} must be a relative path")
    resolved = (workspace / path).resolve()
    if not resolved.is_relative_to(workspace):
        raise ProjectInspectionError(f"{label} escapes the workspace")
    return resolved


def _read_packwiz_project(
    workspace: Path,
    pack_profile: dict[str, Any],
    platform_profile: dict[str, Any],
) -> dict[str, Any]:
    recognition = pack_profile.get("workspace")
    if not isinstance(recognition, dict):
        raise ProjectInspectionError(
            "pack profile lacks workspace recognition"
        )
    kind = _required_string(
        recognition,
        "kind",
        "pack workspace recognition",
    )
    if kind != "packwiz-modpack":
        raise ProjectInspectionError(
            f"unsupported project workspace kind: {kind}"
        )

    required_paths = recognition.get("required_paths")
    if not isinstance(required_paths, list) or not required_paths:
        raise ProjectInspectionError(
            "pack workspace recognition lacks required_paths"
        )
    matched_paths: list[str] = []
    seen_paths: set[str] = set()
    for index, marker in enumerate(required_paths):
        label = f"workspace required_paths[{index}]"
        if not isinstance(marker, dict):
            raise ProjectInspectionError(f"{label} must be an object")
        relative = _required_string(marker, "path", label)
        marker_kind = _required_string(marker, "kind", label)
        if relative in seen_paths:
            raise ProjectInspectionError(
                f"duplicate workspace recognition path: {relative}"
            )
        seen_paths.add(relative)
        candidate = _workspace_path(workspace, relative, f"{label}.path")
        if marker_kind == "file":
            matches = candidate.is_file()
        elif marker_kind == "directory":
            matches = candidate.is_dir()
        else:
            raise ProjectInspectionError(
                f"{label}.kind must be file or directory"
            )
        if not matches:
            raise ProjectInspectionError(
                f"workspace does not match the pack profile: "
                f"{relative} is not a {marker_kind}"
            )
        matched_paths.append(relative)

    manifest_path = _workspace_path(
        workspace,
        "pack.toml",
        "Packwiz manifest path",
    )
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise ProjectInspectionError(
            f"Packwiz manifest cannot be read: {manifest_path}"
        ) from exc
    try:
        manifest = tomllib.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ProjectInspectionError(
            f"Packwiz manifest is malformed: {manifest_path}"
        ) from exc
    if not isinstance(manifest, dict):
        raise ProjectInspectionError("Packwiz manifest must be an object")

    name = _required_string(manifest, "name", "Packwiz manifest")
    expected_name = _required_string(
        recognition,
        "expected_name",
        "pack workspace recognition",
    )
    if name != expected_name:
        raise ProjectInspectionError(
            "workspace Packwiz name does not match the selected pack profile: "
            f"{name} != {expected_name}"
        )
    version = _required_string(manifest, "version", "Packwiz manifest")
    author = _required_string(manifest, "author", "Packwiz manifest")
    pack_format = _required_string(
        manifest,
        "pack-format",
        "Packwiz manifest",
    )
    if not pack_format.startswith("packwiz:"):
        raise ProjectInspectionError(
            f"unsupported Packwiz pack format: {pack_format}"
        )
    expected_pack_format = recognition.get("expected_pack_format")
    if expected_pack_format is not None and pack_format != expected_pack_format:
        raise ProjectInspectionError(
            "workspace Packwiz format does not match the selected pack profile: "
            f"{pack_format} != {expected_pack_format}"
        )

    versions = manifest.get("versions")
    if not isinstance(versions, dict):
        raise ProjectInspectionError("Packwiz manifest lacks versions")
    minecraft_version = _required_string(
        versions,
        "minecraft",
        "Packwiz versions",
    )
    target_minecraft = _required_string(
        platform_profile,
        "minecraft_version",
        "platform profile",
    )
    if minecraft_version != target_minecraft:
        raise ProjectInspectionError(
            "workspace Minecraft version differs from the target platform: "
            f"{minecraft_version} != {target_minecraft}"
        )
    loaders = [
        {"id": loader_id, "version": loader_version}
        for loader_id, loader_version in sorted(versions.items())
        if loader_id != "minecraft"
        and isinstance(loader_id, str)
        and isinstance(loader_version, str)
        and loader_id
        and loader_version
    ]
    if not loaders:
        raise ProjectInspectionError(
            "Packwiz manifest has no declared loader"
        )
    expected_loader = recognition.get("expected_loader")
    if expected_loader is None:
        legacy_candidates: list[dict[str, str]] = []
        profiles = pack_profile.get("profiles")
        if isinstance(profiles, dict):
            for profile in profiles.values():
                if not isinstance(profile, dict):
                    continue
                legacy_platform = profile.get("platform")
                if (
                    isinstance(legacy_platform, dict)
                    and legacy_platform.get("kind") == "legacy-forge"
                    and legacy_platform.get("minecraft_version") == minecraft_version
                    and isinstance(legacy_platform.get("forge_version"), str)
                    and legacy_platform["forge_version"]
                ):
                    legacy_candidates.append(
                        {
                            "id": "forge",
                            "version": legacy_platform["forge_version"],
                        }
                    )
        distinct_candidates = {
            (row["id"], row["version"]) for row in legacy_candidates
        }
        if len(distinct_candidates) > 1:
            raise ProjectInspectionError(
                "pack profile has ambiguous legacy loader applicability"
            )
        if len(distinct_candidates) == 1:
            loader_id, loader_version = next(iter(distinct_candidates))
            expected_loader = {"id": loader_id, "version": loader_version}
    if expected_loader is not None:
        if not isinstance(expected_loader, dict):
            raise ProjectInspectionError(
                "pack workspace recognition expected_loader must be an object"
            )
        expected_loader_id = _required_string(
            expected_loader, "id", "pack workspace expected loader"
        )
        expected_loader_version = _required_string(
            expected_loader, "version", "pack workspace expected loader"
        )
        expected_loaders = [
            {"id": expected_loader_id, "version": expected_loader_version}
        ]
        if loaders != expected_loaders:
            raise ProjectInspectionError(
                "workspace Packwiz loader does not match the selected pack profile: "
                f"{loaders} != {expected_loaders}"
            )

    index = manifest.get("index")
    if not isinstance(index, dict):
        raise ProjectInspectionError("Packwiz manifest lacks index")
    index_file = _required_string(index, "file", "Packwiz index")
    hash_format = _required_string(
        index,
        "hash-format",
        "Packwiz index",
    )
    declared_hash = _required_string(index, "hash", "Packwiz index")
    if hash_format != "sha256":
        raise ProjectInspectionError(
            f"unsupported Packwiz index hash format: {hash_format}"
        )
    if (
        len(declared_hash) != 64
        or any(character not in "0123456789abcdef" for character in declared_hash)
    ):
        raise ProjectInspectionError(
            "Packwiz index SHA-256 is malformed"
        )
    index_path = _workspace_path(
        workspace,
        index_file,
        "Packwiz index path",
    )
    try:
        index_bytes = index_path.read_bytes()
    except OSError as exc:
        raise ProjectInspectionError(
            f"Packwiz index cannot be read: {index_path}"
        ) from exc
    actual_hash = sha256(index_bytes).hexdigest()

    return {
        "kind": kind,
        "name": name,
        "version": version,
        "author": author,
        "pack_format": pack_format,
        "manifest_sha256": sha256(manifest_bytes).hexdigest(),
        "minecraft_version": minecraft_version,
        "loaders": loaders,
        "index": {
            "file": index_file,
            "hash_format": hash_format,
            "declared_hash": declared_hash,
            "actual_sha256": actual_hash,
            "matches_declared_hash": declared_hash == actual_hash,
        },
        "matched_paths": matched_paths,
    }


def _git(workspace: Path, *arguments: str) -> str:
    try:
        completed = run_git_observation(workspace, arguments)
    except (GitObservationError, OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectInspectionError(
            f"Git {' '.join(arguments)} could not be observed safely: {exc}"
        ) from exc
    if completed.returncode:
        detail = completed.stderr.strip() or "unknown Git failure"
        raise ProjectInspectionError(
            f"Git {' '.join(arguments)} failed: {detail}"
        )
    return completed.stdout


def inspect_workspace(
    workspace_root: Path | str,
    *,
    platform_profile_path: Path | str,
    pack_profile_path: Path | str,
    pack_selection: str,
    platform_profile_bytes: bytes | None = None,
    pack_profile_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Return exact project identity bound to an explicit profile selection.

    Integrators that already own an immutable authority snapshot pass its
    exact bytes. Standalone callers may omit them and read the explicit paths
    once here.
    """

    workspace = Path(workspace_root).expanduser().resolve()
    if not workspace.is_dir():
        raise ProjectInspectionError(
            f"workspace root is not a directory: {workspace}"
        )
    if not isinstance(pack_selection, str) or not pack_selection:
        raise ProjectInspectionError(
            "pack selection must be a non-empty string"
        )

    revision = _git(workspace, "rev-parse", "HEAD").strip()
    if not revision:
        raise ProjectInspectionError("Git returned an empty workspace revision")
    dirty_entries = sorted(
        line
        for line in _git(
            workspace,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=all",
        ).splitlines()
        if line
    )

    platform_path = Path(platform_profile_path).expanduser().resolve()
    pack_path = Path(pack_profile_path).expanduser().resolve()
    platform, platform_digest = _read_yaml_object(
        platform_path,
        "platform profile",
        source_bytes=platform_profile_bytes,
    )
    pack, pack_digest = _read_yaml_object(
        pack_path,
        "pack profile",
        source_bytes=pack_profile_bytes,
    )

    platform_id = _required_string(platform, "profile_id", "platform profile")
    profiles = pack.get("profiles")
    if not isinstance(profiles, dict):
        raise ProjectInspectionError("pack profile lacks profiles")
    selected_name = pack_selection
    selected = profiles.get(selected_name)
    if not isinstance(selected, dict):
        raise ProjectInspectionError(
            f"pack profile selection does not exist: {selected_name}"
        )
    selected_platform = _required_string(
        selected,
        "platform_profile_id",
        f"pack profile {selected_name}",
    )
    if selected_platform != platform_id:
        raise ProjectInspectionError(
            "selected pack profile binds a different platform profile: "
            f"{selected_platform} != {platform_id}"
        )
    operations = selected.get("permitted_operations")
    if (
        not isinstance(operations, list)
        or not operations
        or any(not isinstance(item, str) or not item for item in operations)
    ):
        raise ProjectInspectionError(
            f"pack profile {selected_name} lacks permitted_operations"
        )
    project = _read_packwiz_project(workspace, pack, platform)

    return {
        "format": "workbench-workspace-context-v2",
        "schema_version": 2,
        "workspace": {
            "root_uri": workspace.as_uri(),
            "revision": revision,
            "dirty": bool(dirty_entries),
            "dirty_entries": dirty_entries,
        },
        "project": project,
        "platform": {
            "profile_id": platform_id,
            "status": _required_string(platform, "status", "platform profile"),
            "minecraft_version": _required_string(
                platform,
                "minecraft_version",
                "platform profile",
            ),
            "cleanroom_version": _required_string(
                platform,
                "cleanroom_version",
                "platform profile",
            ),
            "document_sha256": platform_digest,
        },
        "pack": {
            "profile_family_id": _required_string(
                pack,
                "profile_family_id",
                "pack profile",
            ),
            "display_name": _required_string(
                pack,
                "display_name",
                "pack profile",
            ),
            "status": _required_string(pack, "status", "pack profile"),
            "selected_profile": selected_name,
            "maturity": _required_string(
                selected,
                "maturity",
                f"pack profile {selected_name}",
            ),
            "platform_profile_id": selected_platform,
            "permitted_operations": list(operations),
            "document_sha256": pack_digest,
        },
    }
