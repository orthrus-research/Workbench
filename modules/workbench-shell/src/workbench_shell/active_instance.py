"""Persist and revalidate the installed runtime selected for construction."""

from __future__ import annotations

from urllib.request import url2pathname

import configparser
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, NoReturn

from workbench_core.artifact_store import sha256_file
from .bootstrap import inspect_project
from workbench_core.configuration import (
    CONFIGURATION_PATH,
    WorkbenchConfiguration,
    WorkbenchConfigurationError,
    load_workbench_configuration,
)
from workbench_api.state_paths import default_suite_state_root


MAX_IDENTITY_BYTES = 1024 * 1024
SUPPORTED_PACK_PROFILE_ID = "workbench-pack:supersymmetry"


class ActiveInstanceError(ValueError):
    """Raised when an installed runtime cannot be selected or has drifted."""


def _fail(message: str) -> NoReturn:
    raise ActiveInstanceError(message)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _read_regular(path: Path, label: str, limit: int = MAX_IDENTITY_BYTES) -> bytes:
    if path.is_symlink() or not path.is_file():
        _fail(f"{label} must be a regular file")
    try:
        size = path.stat().st_size
        if size > limit:
            _fail(f"{label} exceeds {limit} bytes")
        return path.read_bytes()
    except OSError as exc:
        _fail(f"cannot read {label}: {exc}")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(_read_regular(path, label).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"{label} is not valid UTF-8 JSON: {exc}")
    if not isinstance(value, dict):
        _fail(f"{label} must be a JSON object")
    return value


def _profile_context(
    suite: Path,
    workspace: Path,
    configuration: WorkbenchConfiguration,
) -> dict[str, Any]:
    if configuration.pack_profile_id != SUPPORTED_PACK_PROFILE_ID:
        _fail(
            "active-instance recognition currently supports only the explicit "
            "Supersymmetry pack lane; another pack requires profile-owned "
            "runtime markers in a new profile schema"
        )
    inspection = inspect_project(
        suite,
        workspace,
        configuration=configuration,
    )
    context = inspection.get("workspace_context")
    pack = context.get("pack") if isinstance(context, dict) else None
    if (
        not isinstance(pack, dict)
        or pack.get("profile_family_id") != configuration.pack_profile_id
        or "construct" not in pack.get("permitted_operations", [])
    ):
        _fail("active-instance selection requires the selected constructible pack workspace")
    return context


def _platform_identity(
    configuration: WorkbenchConfiguration,
) -> dict[str, str]:
    value = configuration.platform_document.values
    profile_id = value.get("profile_id")
    minecraft_version = value.get("minecraft_version")
    cleanroom_version = value.get("cleanroom_version")
    if not all(isinstance(item, str) and item for item in (
        profile_id,
        minecraft_version,
        cleanroom_version,
    )):
        _fail("selected platform profile lacks exact runtime identity")
    return {
        "profile_id": profile_id,
        "minecraft_version": minecraft_version,
        "cleanroom_version": cleanroom_version,
    }


def _resolve_layout(selected: Path) -> tuple[Path, Path, str]:
    root = selected.expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        _fail("selected instance must be a regular directory")

    if (root / "mmc-pack.json").is_file():
        candidates = [
            (root / name, name)
            for name in (".minecraft", "minecraft")
            if (root / name).is_dir() and not (root / name).is_symlink()
        ]
        if len(candidates) != 1:
            _fail(
                "launcher instance must contain exactly one regular "
                ".minecraft or minecraft payload"
            )
        payload, name = candidates[0]
        return root, payload.resolve(), f"prism-{name}"

    if root.name in {".minecraft", "minecraft"} and (
        root.parent / "mmc-pack.json"
    ).is_file():
        return root.parent.resolve(), root, f"prism-{root.name}"

    _fail(
        "selected instance must be a Prism/MultiMC root, or its minecraft "
        "payload, with mmc-pack.json"
    )


def _component_identity(instance: Path, platform: dict[str, str]) -> list[dict[str, str]]:
    manifest = _load_json(instance / "mmc-pack.json", "launcher component manifest")
    components = manifest.get("components")
    if not isinstance(components, list):
        _fail("launcher component manifest lacks components")
    normalized: list[dict[str, str]] = []
    for index, value in enumerate(components):
        if not isinstance(value, dict):
            _fail(f"launcher component {index} must be an object")
        uid = value.get("uid")
        version = value.get("version")
        if not isinstance(uid, str) or not isinstance(version, str):
            _fail(f"launcher component {index} lacks uid/version")
        row = {"uid": uid, "version": version}
        cached_name = value.get("cachedName")
        if isinstance(cached_name, str) and cached_name:
            row["cached_name"] = cached_name
        normalized.append(row)
    normalized.sort(key=lambda row: (row["uid"], row["version"]))

    minecraft = [row for row in normalized if row["uid"] == "net.minecraft"]
    loader = [row for row in normalized if row["uid"] == "net.minecraftforge"]
    if len(minecraft) != 1 or minecraft[0]["version"] != platform["minecraft_version"]:
        _fail("selected instance does not use the profile Minecraft version")
    if (
        len(loader) != 1
        or loader[0]["version"] != platform["cleanroom_version"]
        or "cleanroom" not in loader[0].get("cached_name", "").casefold()
    ):
        _fail("selected instance is not the profile-selected Cleanroom runtime")
    return normalized


def _instance_cfg_identity(instance: Path) -> dict[str, str]:
    path = instance / "instance.cfg"
    if not path.exists():
        return {}
    try:
        text = _read_regular(path, "launcher instance configuration").decode("utf-8-sig")
    except UnicodeError as exc:
        _fail(f"launcher instance configuration is not UTF-8: {exc}")
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parse_text = text
    if re.search(r"^\s*\[[^\]\r\n]+\]\s*$", text, re.MULTILINE) is None:
        parse_text = "[General]\n" + text
    try:
        parser.read_string(parse_text)
    except configparser.Error as exc:
        _fail(f"launcher instance configuration is invalid: {exc}")
    if not parser.has_section("General"):
        _fail("launcher instance configuration lacks [General]")
    stable = {}
    for key in (
        "name",
        "ManagedPackID",
        "ManagedPackVersionID",
        "ManagedPackVersionName",
    ):
        value = parser.get("General", key, fallback="")
        if value:
            stable[key] = value
    return stable


def _runtime_markers(payload: Path) -> list[dict[str, Any]]:
    for required in ("config", "groovy", "mods"):
        path = payload / required
        if not path.is_dir() or path.is_symlink():
            _fail(f"selected Minecraft payload lacks regular {required}/")
    mods = payload / "mods"
    candidates = [
        path
        for path in mods.rglob("*.jar")
        if path.is_file() and not path.is_symlink()
    ]
    requirements = {
        "gregtech": re.compile(r"^gregtech(?:-|$)", re.IGNORECASE),
        "groovyscript": re.compile(r"^groovyscript(?:-|$)", re.IGNORECASE),
        "supersymmetry": re.compile(r"^(?:supersymmetry|susy-core)(?:-|$)", re.IGNORECASE),
    }
    markers: list[dict[str, Any]] = []
    for marker_id, pattern in requirements.items():
        matches = [path for path in candidates if pattern.search(path.name)]
        if len(matches) != 1:
            _fail(
                f"selected Minecraft payload must contain exactly one "
                f"{marker_id} runtime JAR"
            )
        path = matches[0]
        digest, size = sha256_file(path)
        markers.append({
            "id": marker_id,
            "path": path.relative_to(payload).as_posix(),
            "sha256": digest,
            "size": size,
        })
    return markers


def _observed_identity(
    instance: Path,
    payload: Path,
    layout: str,
    platform: dict[str, str],
) -> dict[str, Any]:
    return {
        "layout": layout,
        "payload_relative_path": payload.relative_to(instance).as_posix(),
        "components": _component_identity(instance, platform),
        "instance_configuration": _instance_cfg_identity(instance),
        "runtime_markers": _runtime_markers(payload),
    }


def _selection_id(
    workspace: Path,
    instance: Path,
    payload: Path,
    pack_profile_id: str,
    platform: dict[str, str],
    identity: dict[str, Any],
) -> str:
    return "sha256:" + sha256(_canonical_bytes({
        "workspace_uri": workspace.as_uri(),
        "instance_uri": instance.as_uri(),
        "payload_uri": payload.as_uri(),
        "profile_family_id": pack_profile_id,
        "platform": platform,
        "identity": identity,
    })).hexdigest()


def _selection_path(state: Path, workspace: Path) -> Path:
    key = sha256(workspace.as_uri().encode("utf-8")).hexdigest()
    return state / "active-instances" / f"{key}.json"


def _state_root(suite: Path, state_root: Path | str | None) -> Path:
    state = (
        default_suite_state_root(suite)
        if state_root is None
        else Path(state_root).expanduser().resolve()
    )
    state.mkdir(parents=True, exist_ok=True)
    if state.is_symlink() or not state.is_dir():
        _fail("Workbench state root must be a regular directory")
    return state


def _write_atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        _fail("active-instance state directory cannot be a symbolic link")
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as output:
            temporary_name = output.name
            json.dump(value, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    except OSError as exc:
        _fail(f"cannot persist active-instance selection: {exc}")
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def initialize_active_instance(
    suite_root: Path | str,
    workspace_root: Path | str,
    instance_root: Path | str,
    *,
    state_root: Path | str | None = None,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Select one installed profile-matched runtime for direct construction."""

    suite = Path(suite_root).resolve()
    workspace = Path(workspace_root).expanduser().resolve()
    if configuration is not None and config_path is not None:
        _fail("configuration and config_path are mutually exclusive")
    try:
        active_configuration = configuration or load_workbench_configuration(
            suite,
            CONFIGURATION_PATH if config_path is None else config_path,
        )
    except WorkbenchConfigurationError as exc:
        _fail(f"Workbench configuration cannot be loaded: {exc}")
    context = _profile_context(suite, workspace, active_configuration)
    platform = _platform_identity(active_configuration)
    instance, payload, layout = _resolve_layout(Path(instance_root))
    identity = _observed_identity(instance, payload, layout, platform)
    selection_id = _selection_id(
        workspace,
        instance,
        payload,
        active_configuration.pack_profile_id,
        platform,
        identity,
    )
    record = {
        "format": "workbench-active-instance-v1",
        "schema_version": 1,
        "selection_id": selection_id,
        "state": "active",
        "workspace": {
            "root_uri": workspace.as_uri(),
            "project_name": context["project"]["name"],
            "profile_family_id": active_configuration.pack_profile_id,
        },
        "platform": platform,
        "instance": {
            "root_uri": instance.as_uri(),
            "payload_root_uri": payload.as_uri(),
            "identity": identity,
        },
    }
    state = _state_root(suite, state_root)
    path = _selection_path(state, workspace)
    _write_atomic_json(path, record)
    return {
        "format": "workbench-active-instance-result-v1",
        "schema_version": 1,
        "outcome": "selected",
        "selection": record,
        "selection_uri": path.as_uri(),
    }


def load_active_instance(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    state_root: Path | str | None = None,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Load the active selection and reject instance identity drift."""

    suite = Path(suite_root).resolve()
    workspace = Path(workspace_root).expanduser().resolve()
    if configuration is not None and config_path is not None:
        _fail("configuration and config_path are mutually exclusive")
    try:
        active_configuration = configuration or load_workbench_configuration(
            suite,
            CONFIGURATION_PATH if config_path is None else config_path,
        )
    except WorkbenchConfigurationError as exc:
        _fail(f"Workbench configuration cannot be loaded: {exc}")
    _profile_context(suite, workspace, active_configuration)
    state = _state_root(suite, state_root)
    path = _selection_path(state, workspace)
    if not path.exists():
        _fail("no active instance is selected; run workbench initialize first")
    record = _load_json(path, "active-instance selection")
    if (
        record.get("format") != "workbench-active-instance-v1"
        or record.get("schema_version") != 1
        or record.get("state") != "active"
    ):
        _fail("active-instance selection has an unsupported format")
    selected_workspace = record.get("workspace")
    instance_value = record.get("instance")
    platform = record.get("platform")
    if (
        not isinstance(selected_workspace, dict)
        or selected_workspace.get("root_uri") != workspace.as_uri()
        or selected_workspace.get("profile_family_id")
        != active_configuration.pack_profile_id
        or not isinstance(instance_value, dict)
        or not isinstance(platform, dict)
    ):
        _fail("active-instance selection belongs to a different workspace")
    instance_uri = instance_value.get("root_uri")
    if not isinstance(instance_uri, str) or not instance_uri.startswith("file:"):
        _fail("active-instance selection lacks a local instance URI")
    from urllib.parse import urlparse

    parsed = urlparse(instance_uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        _fail("active-instance selection instance URI is not local")
    instance, payload, layout = _resolve_layout(Path(url2pathname(parsed.path)))
    if (
        instance_value.get("root_uri") != instance.as_uri()
        or instance_value.get("payload_root_uri") != payload.as_uri()
    ):
        _fail("active-instance selection paths are inconsistent")
    current_platform = _platform_identity(active_configuration)
    if platform != current_platform:
        _fail("active-instance selection platform profile has changed")
    observed = _observed_identity(instance, payload, layout, current_platform)
    if observed != instance_value.get("identity"):
        _fail("active instance identity has drifted; initialize it again")
    if record.get("selection_id") != _selection_id(
        workspace,
        instance,
        payload,
        active_configuration.pack_profile_id,
        current_platform,
        observed,
    ):
        _fail("active-instance selection identity is inconsistent")
    return {
        **record,
        "selection_uri": path.as_uri(),
        "instance_path": instance,
        "payload_path": payload,
    }


__all__ = [
    "ActiveInstanceError",
    "initialize_active_instance",
    "load_active_instance",
]
