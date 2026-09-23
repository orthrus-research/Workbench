"""Guarded materialization of the exact Cleanroom client bootstrap."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname
from zipfile import BadZipFile, ZipFile, ZipInfo

from workbench_core.artifact_store import (
    ArtifactStoreError,
    DOWNLOAD_CHUNK_BYTES,
    fetch_verified_artifact,
    sha256_file,
)
from workbench_core.configuration import (
    CONFIGURATION_PATH,
    WorkbenchConfiguration,
    WorkbenchConfigurationError,
    load_workbench_configuration,
)
from .runtime_plan import plan_project_runtime
from workbench_api.state_paths import default_suite_state_root


RECEIPT_PATH = Path("receipts/cleanroom-client-bootstrap-v1.json")
MAX_ARCHIVE_MEMBERS = 10_000
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
BOOTSTRAP_BLOCKERS = frozenset({
    "cleanroom-version-unresolved",
    "cleanroom-client-unresolved",
})


class RuntimeBootstrapError(ValueError):
    """Raised when a Cleanroom bootstrap cannot be materialized safely."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _local_path(uri: Any, label: str) -> Path:
    if not isinstance(uri, str):
        raise RuntimeBootstrapError(f"{label} must be a file URI")
    parsed = urlparse(uri)
    if (
        parsed.scheme != "file"
        or parsed.query
        or parsed.fragment
        or parsed.params
        or parsed.netloc not in {"", "localhost"}
    ):
        raise RuntimeBootstrapError(f"{label} must be a local file URI")
    path_text = url2pathname(parsed.path)
    if (
        os.name == "nt"
        and len(path_text) >= 3
        and path_text[0] == "/"
        and path_text[2] == ":"
    ):
        path_text = path_text[1:]
    return Path(path_text)


def _client_artifact(plan: dict[str, Any]) -> dict[str, str]:
    artifacts = plan.get("artifacts")
    if not isinstance(artifacts, list):
        raise RuntimeBootstrapError("runtime plan lacks artifacts")
    matches = [
        artifact
        for artifact in artifacts
        if isinstance(artifact, dict)
        and artifact.get("id") == "cleanroom_client"
    ]
    if len(matches) != 1:
        raise RuntimeBootstrapError(
            "runtime plan must contain one Cleanroom client artifact"
        )
    artifact = matches[0]
    for field in ("url", "sha256", "state"):
        if not isinstance(artifact.get(field), str):
            raise RuntimeBootstrapError(
                f"Cleanroom client artifact lacks {field}"
            )
    return {
        "id": "cleanroom_client",
        "url": artifact["url"],
        "sha256": artifact["sha256"],
        "state": artifact["state"],
    }


def _validate_plan(plan: dict[str, Any], state_root: Path) -> tuple[Path, dict[str, str]]:
    if plan.get("format") != "workbench-runtime-plan-v1":
        raise RuntimeBootstrapError("unsupported runtime plan format")
    request = plan.get("request")
    if not isinstance(request, dict) or request.get("side") != "client":
        raise RuntimeBootstrapError(
            "runtime bootstrap currently supports client plans only"
        )
    if request.get("launcher") not in {"prism", "multimc"}:
        raise RuntimeBootstrapError(
            "runtime bootstrap requires Prism or MultiMC"
        )

    blockers = plan.get("blockers")
    if not isinstance(blockers, list):
        raise RuntimeBootstrapError("runtime plan lacks blockers")
    bootstrap_blockers = [
        blocker.get("id")
        for blocker in blockers
        if isinstance(blocker, dict)
        and blocker.get("id") in BOOTSTRAP_BLOCKERS
    ]
    if bootstrap_blockers:
        raise RuntimeBootstrapError(
            "Cleanroom client bootstrap is blocked: "
            + ", ".join(sorted(bootstrap_blockers))
        )

    artifact = _client_artifact(plan)
    if artifact["state"] != "resolved":
        raise RuntimeBootstrapError(
            "Cleanroom client artifact is not hash-locked"
        )
    target = plan.get("target")
    if not isinstance(target, dict):
        raise RuntimeBootstrapError("runtime plan lacks a target")
    fixture_root = _local_path(
        target.get("fixture_root_uri"),
        "runtime fixture root",
    ).resolve()
    resolved_state_root = state_root.resolve()
    if not fixture_root.is_relative_to(resolved_state_root):
        raise RuntimeBootstrapError(
            "runtime fixture root escapes Workbench state"
        )
    return fixture_root, artifact


def _archive_member_path(info: ZipInfo) -> PurePosixPath:
    name = info.filename
    if not name or "\\" in name:
        raise RuntimeBootstrapError(
            f"unsafe archive member path: {name!r}"
        )
    member = PurePosixPath(name)
    if (
        member.is_absolute()
        or ".." in member.parts
        or not member.parts
        or ":" in member.parts[0]
    ):
        raise RuntimeBootstrapError(
            f"unsafe archive member path: {name!r}"
        )
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if stat.S_ISLNK(mode):
        raise RuntimeBootstrapError(
            f"archive member is a symbolic link: {name}"
        )
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise RuntimeBootstrapError(
            f"archive member has unsupported type: {name}"
        )
    if info.flag_bits & 0x1:
        raise RuntimeBootstrapError(
            f"archive member is encrypted: {name}"
        )
    return member


def _extract_client_archive(archive: Path, instance_root: Path) -> None:
    try:
        with ZipFile(archive) as source:
            infos = source.infolist()
            if not infos:
                raise RuntimeBootstrapError(
                    "Cleanroom client archive is empty"
                )
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                raise RuntimeBootstrapError(
                    "Cleanroom client archive has too many members"
                )
            expanded_bytes = sum(info.file_size for info in infos)
            if expanded_bytes > MAX_EXPANDED_BYTES:
                raise RuntimeBootstrapError(
                    "Cleanroom client archive expands beyond the safety limit"
                )

            normalized_paths: set[str] = set()
            validated: list[tuple[ZipInfo, PurePosixPath]] = []
            for info in infos:
                member = _archive_member_path(info)
                normalized = member.as_posix().rstrip("/")
                collision_key = normalized.casefold()
                if collision_key in normalized_paths:
                    raise RuntimeBootstrapError(
                        f"duplicate archive member path: {normalized}"
                    )
                normalized_paths.add(collision_key)
                validated.append((info, member))

            instance_root.mkdir(parents=True)
            for info, member in validated:
                normalized = member.as_posix().rstrip("/")
                destination = instance_root.joinpath(*member.parts)
                if info.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with source.open(info) as input_stream:
                    with destination.open("xb") as output_stream:
                        shutil.copyfileobj(
                            input_stream,
                            output_stream,
                            DOWNLOAD_CHUNK_BYTES,
                        )
                if destination.stat().st_size != info.file_size:
                    raise RuntimeBootstrapError(
                        f"archive member size mismatch: {normalized}"
                    )
    except BadZipFile as exc:
        raise RuntimeBootstrapError(
            "Cleanroom client artifact is not a valid ZIP archive"
        ) from exc


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeBootstrapError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeBootstrapError(f"{label} must be a JSON object")
    return value


def _validate_client_instance(
    instance_root: Path,
    *,
    minecraft_version: Any,
    cleanroom_version: Any,
) -> None:
    if not isinstance(minecraft_version, str) or not minecraft_version:
        raise RuntimeBootstrapError(
            "runtime plan lacks a Minecraft version"
        )
    if not isinstance(cleanroom_version, str) or not cleanroom_version:
        raise RuntimeBootstrapError(
            "runtime plan lacks a Cleanroom version"
        )
    required_paths = (
        "instance.cfg",
        "mmc-pack.json",
        "patches/net.minecraft.json",
        "patches/net.minecraftforge.json",
    )
    missing = [
        relative
        for relative in required_paths
        if not (instance_root / relative).is_file()
    ]
    if missing:
        raise RuntimeBootstrapError(
            "Cleanroom client archive lacks required instance files: "
            + ", ".join(missing)
        )

    manifest = _load_json(
        instance_root / "mmc-pack.json",
        "Cleanroom launcher manifest",
    )
    components = manifest.get("components")
    if not isinstance(components, list):
        raise RuntimeBootstrapError(
            "Cleanroom launcher manifest lacks components"
        )
    versions: dict[str, str] = {}
    for component in components:
        if not isinstance(component, dict):
            raise RuntimeBootstrapError(
                "Cleanroom launcher component must be an object"
            )
        uid = component.get("uid")
        version = component.get("version")
        if not isinstance(uid, str) or not isinstance(version, str):
            raise RuntimeBootstrapError(
                "Cleanroom launcher component lacks UID or version"
            )
        if uid in versions:
            raise RuntimeBootstrapError(
                f"duplicate Cleanroom launcher component: {uid}"
            )
        versions[uid] = version
    expected = {
        "net.minecraft": minecraft_version,
        "net.minecraftforge": cleanroom_version,
    }
    mismatches = [
        f"{uid} expected {version}, found {versions.get(uid)!r}"
        for uid, version in expected.items()
        if versions.get(uid) != version
    ]
    if mismatches:
        raise RuntimeBootstrapError(
            "Cleanroom launcher manifest identity mismatch: "
            + "; ".join(mismatches)
        )


def _tree_manifest(
    root: Path,
    *,
    excluded_top_level: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if not root.is_dir() or root.is_symlink():
        raise RuntimeBootstrapError(
            "materialized instance root is not a regular directory"
        )
    entries: list[dict[str, Any]] = []
    file_count = 0
    total_bytes = 0
    paths = sorted(
        root.rglob("*"),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if path.relative_to(root).parts[0] in excluded_top_level:
            continue
        if path.is_symlink():
            raise RuntimeBootstrapError(
                f"materialized instance contains a symbolic link: {relative}"
            )
        if path.is_dir():
            entries.append({"kind": "directory", "path": relative})
            continue
        if not path.is_file():
            raise RuntimeBootstrapError(
                f"materialized instance contains a special file: {relative}"
            )
        digest, size = sha256_file(path)
        entries.append({
            "kind": "file",
            "path": relative,
            "sha256": digest,
            "size": size,
        })
        file_count += 1
        total_bytes += size
    return {
        "tree_sha256": "sha256:" + sha256(
            _canonical_bytes(entries)
        ).hexdigest(),
        "file_count": file_count,
        "total_bytes": total_bytes,
        "entries": entries,
    }


def _remaining_blockers(plan: dict[str, Any]) -> list[dict[str, Any]]:
    blockers = plan.get("blockers")
    assert isinstance(blockers, list)
    return [
        dict(blocker)
        for blocker in blockers
        if isinstance(blocker, dict)
        and blocker.get("id") not in BOOTSTRAP_BLOCKERS
    ]


def _receipt(
    plan: dict[str, Any],
    *,
    artifact: dict[str, str],
    artifact_size: int,
    artifact_source_revision: str,
    cache_path: Path,
    fixture_root: Path,
    instance: dict[str, Any],
) -> dict[str, Any]:
    target = plan["target"]
    request = plan["request"]
    project = plan["project"]
    workspace = plan["workspace"]
    identity = {
        "plan_id": plan["plan_id"],
        "artifact": {
            "id": artifact["id"],
            "url": artifact["url"],
            "source_revision": artifact_source_revision,
            "sha256": artifact["sha256"],
            "size": artifact_size,
        },
        "instance_tree_sha256": instance["tree_sha256"],
    }
    return {
        "format": "workbench-runtime-bootstrap-receipt-v1",
        "schema_version": 1,
        "bootstrap_id": "sha256:" + sha256(
            _canonical_bytes(identity)
        ).hexdigest(),
        "operation_class": "local-mutation",
        "state": "materialized",
        "readiness": "launcher-base-instance",
        "plan_id": plan["plan_id"],
        "request": dict(request),
        "workspace": dict(workspace),
        "project": {
            "name": project.get("name"),
            "version": project.get("version"),
            "manifest_sha256": project.get("manifest_sha256"),
        },
        "platform": {
            "profile_id": target.get("platform_profile_id"),
            "cleanroom_version": target.get("cleanroom_version"),
            "minecraft_version": project.get("minecraft_version"),
        },
        "artifact": {
            "id": artifact["id"],
            "url": artifact["url"],
            "source_revision": artifact_source_revision,
            "sha256": artifact["sha256"],
            "size": artifact_size,
            "cache_uri": cache_path.as_uri(),
        },
        "target": {
            "fixture_root_uri": fixture_root.as_uri(),
            "instance_root_uri": (fixture_root / "instance").as_uri(),
            "receipt_uri": (fixture_root / RECEIPT_PATH).as_uri(),
        },
        "instance": instance,
        "remaining_plan_blockers": _remaining_blockers(plan),
        "warnings": list(plan.get("warnings", [])),
        "limitations": [
            "The Cleanroom launcher base is materialized; no Packwiz payload "
            "is installed by this operation.",
            "No Java runtime is installed or selected by this operation.",
            "No Prism or MultiMC installation is modified.",
        ],
    }


def _reuse_existing(
    plan: dict[str, Any],
    *,
    artifact: dict[str, str],
    artifact_size: int,
    artifact_source_revision: str,
    fixture_root: Path,
) -> dict[str, Any]:
    if not fixture_root.is_dir() or fixture_root.is_symlink():
        raise RuntimeBootstrapError(
            "runtime fixture target already exists and is not a directory"
        )
    receipt_path = fixture_root / RECEIPT_PATH
    if receipt_path.is_symlink():
        raise RuntimeBootstrapError(
            "runtime bootstrap receipt cannot be a symbolic link"
        )
    receipt = _load_json(receipt_path, "runtime bootstrap receipt")
    expected_artifact = {
        "id": artifact["id"],
        "url": artifact["url"],
        "source_revision": artifact_source_revision,
        "sha256": artifact["sha256"],
        "size": artifact_size,
    }
    recorded_artifact = receipt.get("artifact")
    if not isinstance(recorded_artifact, dict):
        raise RuntimeBootstrapError(
            "existing runtime bootstrap receipt lacks its artifact"
        )
    recorded_identity = {
        field: recorded_artifact.get(field)
        for field in expected_artifact
    }
    if (
        receipt.get("format")
        != "workbench-runtime-bootstrap-receipt-v1"
        or receipt.get("schema_version") != 1
        or receipt.get("operation_class") != "local-mutation"
        or receipt.get("state") != "materialized"
        or receipt.get("readiness") != "launcher-base-instance"
        or receipt.get("plan_id") != plan.get("plan_id")
        or recorded_identity != expected_artifact
    ):
        raise RuntimeBootstrapError(
            "runtime fixture target belongs to a different bootstrap"
        )
    actual_instance = _tree_manifest(fixture_root / "instance")
    recorded_instance = receipt.get("instance")
    if not isinstance(recorded_instance, dict) or any(
        recorded_instance.get(field) != actual_instance[field]
        for field in ("tree_sha256", "file_count", "total_bytes", "entries")
    ):
        raise RuntimeBootstrapError(
            "runtime fixture target has drifted from its bootstrap receipt"
        )
    expected_bootstrap_identity = {
        "plan_id": plan["plan_id"],
        "artifact": expected_artifact,
        "instance_tree_sha256": actual_instance["tree_sha256"],
    }
    expected_bootstrap_id = "sha256:" + sha256(
        _canonical_bytes(expected_bootstrap_identity)
    ).hexdigest()
    expected_target = {
        "fixture_root_uri": fixture_root.as_uri(),
        "instance_root_uri": (fixture_root / "instance").as_uri(),
        "receipt_uri": receipt_path.as_uri(),
    }
    if (
        receipt.get("bootstrap_id") != expected_bootstrap_id
        or receipt.get("request") != plan.get("request")
        or receipt.get("target") != expected_target
    ):
        raise RuntimeBootstrapError(
            "runtime fixture receipt identity has been modified"
        )
    fixture_entries = sorted(
        path.relative_to(fixture_root).as_posix()
        for path in fixture_root.iterdir()
    )
    receipt_entries = {
        path.relative_to(receipt_path.parent).as_posix(): path
        for path in receipt_path.parent.iterdir()
    }
    if fixture_entries != ["instance", "receipts"] or any(
        name != RECEIPT_PATH.name
        or not path.is_file()
        or path.is_symlink()
        for name, path in receipt_entries.items()
    ):
        raise RuntimeBootstrapError(
            "runtime fixture target contains unrecorded top-level state"
        )
    return {
        "format": "workbench-runtime-bootstrap-result-v1",
        "schema_version": 1,
        "outcome": "reused",
        "receipt": receipt,
    }


def materialize_client_bootstrap(
    plan: dict[str, Any],
    *,
    artifact_size: int,
    artifact_source_revision: str,
    state_root: Path | str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Download, verify, and extract one deterministic client bootstrap."""

    if type(artifact_size) is not int or artifact_size <= 0:
        raise RuntimeBootstrapError(
            "Cleanroom client artifact size must be a positive integer"
        )
    if re.fullmatch(r"[0-9a-f]{40}", artifact_source_revision) is None:
        raise RuntimeBootstrapError(
            "Cleanroom client source revision must be a Git commit"
        )
    if timeout_seconds <= 0:
        raise RuntimeBootstrapError(
            "download timeout must be positive"
        )
    state = Path(state_root).expanduser().resolve()
    fixture_root, artifact = _validate_plan(plan, state)
    if fixture_root.exists() or fixture_root.is_symlink():
        return _reuse_existing(
            plan,
            artifact=artifact,
            artifact_size=artifact_size,
            artifact_source_revision=artifact_source_revision,
            fixture_root=fixture_root,
        )

    try:
        cache_path, artifact_outcome = fetch_verified_artifact(
            url=artifact["url"],
            expected_sha256=artifact["sha256"],
            expected_size=artifact_size,
            state_root=state,
            label="Cleanroom client artifact",
            timeout_seconds=timeout_seconds,
            user_agent="Workbench-Cleanroom-Bootstrap/0.1",
        )
    except ArtifactStoreError as exc:
        raise RuntimeBootstrapError(str(exc)) from exc
    fixture_root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(
        prefix=f".{fixture_root.name}.bootstrap-",
        dir=fixture_root.parent,
    ))
    try:
        instance_root = staging / "instance"
        _extract_client_archive(cache_path, instance_root)
        _validate_client_instance(
            instance_root,
            minecraft_version=plan["project"].get("minecraft_version"),
            cleanroom_version=plan["target"].get("cleanroom_version"),
        )
        instance = _tree_manifest(instance_root)
        receipt = _receipt(
            plan,
            artifact=artifact,
            artifact_size=artifact_size,
            artifact_source_revision=artifact_source_revision,
            cache_path=cache_path,
            fixture_root=fixture_root,
            instance=instance,
        )
        receipt_path = staging / RECEIPT_PATH
        receipt_path.parent.mkdir(parents=True)
        receipt_path.write_text(
            json.dumps(
                receipt,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            staging.rename(fixture_root)
        except OSError as exc:
            raise RuntimeBootstrapError(
                "cannot publish the disposable runtime fixture atomically"
            ) from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    return {
        "format": "workbench-runtime-bootstrap-result-v1",
        "schema_version": 1,
        "outcome": "created",
        "artifact_outcome": artifact_outcome,
        "receipt": receipt,
    }


def _load_bootstrap_artifact(
    profile: Mapping[str, Any],
    plan: dict[str, Any],
) -> tuple[int, str]:
    artifacts = profile.get("runtime_artifacts")
    record = (
        artifacts.get("cleanroom_client")
        if isinstance(artifacts, Mapping)
        else None
    )
    if not isinstance(record, Mapping):
        raise RuntimeBootstrapError(
            "selected platform profile lacks cleanroom_client"
        )
    size = record.get("size")
    if type(size) is not int or size <= 0:
        raise RuntimeBootstrapError(
            "Cleanroom client profile lock lacks an exact size"
        )
    source_revision = record.get("source_revision")
    if (
        not isinstance(source_revision, str)
        or re.fullmatch(r"[0-9a-f]{40}", source_revision) is None
    ):
        raise RuntimeBootstrapError(
            "Cleanroom client profile lock lacks an exact source revision"
        )
    planned = _client_artifact(plan)
    if (
        record.get("url") != planned["url"]
        or record.get("sha256") != planned["sha256"]
    ):
        raise RuntimeBootstrapError(
            "Cleanroom platform profile changed during bootstrap planning"
        )
    return size, source_revision


def bootstrap_project_runtime(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    launcher: str = "prism",
    state_root: Path | str | None = None,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Materialize the exact profile-locked client bootstrap for a project."""

    suite = Path(suite_root).resolve()
    if configuration is not None and config_path is not None:
        raise RuntimeBootstrapError(
            "configuration and config_path are mutually exclusive"
        )
    try:
        active_configuration = configuration or load_workbench_configuration(
            suite,
            CONFIGURATION_PATH if config_path is None else config_path,
        )
    except WorkbenchConfigurationError as exc:
        raise RuntimeBootstrapError(
            f"Workbench configuration cannot be loaded: {exc}"
        ) from exc
    state = (
        default_suite_state_root(suite)
        if state_root is None
        else Path(state_root).expanduser().resolve()
    )
    plan = plan_project_runtime(
        suite,
        workspace_root,
        side="client",
        launcher=launcher,
        state_root=state,
        configuration=active_configuration,
    )
    artifact_size, artifact_source_revision = _load_bootstrap_artifact(
        active_configuration.platform_document.values,
        plan,
    )
    return materialize_client_bootstrap(
        plan,
        artifact_size=artifact_size,
        artifact_source_revision=artifact_source_revision,
        state_root=state,
    )
