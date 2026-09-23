"""Project and launch one populated client through Prism or MultiMC."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import csv
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse
from urllib.request import url2pathname

from workbench_core.artifact_store import sha256_file
from workbench_core.configuration import (
    CONFIGURATION_PATH,
    WorkbenchConfiguration,
    WorkbenchConfigurationError,
    load_workbench_configuration,
)
from workbench_core.runtime_java import (
    JavaRuntimeError,
    ensure_java_runtime,
    host_platform,
    probe_java,
)
from .runtime_materialize import (
    PackwizMaterializationError,
    materialize_project_runtime,
    packwiz_materialization_version,
    verify_packwiz_materialization_receipt_identity,
)
from .runtime_compatibility import (
    RuntimeCompatibilityError,
    apply_compatibility_patches,
)
from workbench_api.state_paths import default_suite_state_root


MAX_CAPTURE_BYTES = 64 * 1024 * 1024
MAX_MONITOR_TAIL_BYTES = 8 * 1024 * 1024
OFFLINE_NAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")
SHA256_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
CLIENT_LOADED_RE = re.compile(
    r"Forge Mod Loader has successfully loaded \d+ mods"
)
LAUNCHER_FAMILIES = frozenset({"prism", "multimc"})


class RuntimeLaunchError(ValueError):
    """Raised when a populated client cannot be launched safely."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _local_uri_path(value: Any, label: str) -> Path:
    if not isinstance(value, str):
        raise RuntimeLaunchError(f"{label} must be a local file URI")
    parsed = urlparse(value)
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeLaunchError(f"{label} must be a local file URI")
    text = url2pathname(parsed.path)
    if (
        os.name == "nt"
        and len(text) >= 3
        and text[0] == "/"
        and text[2] == ":"
    ):
        text = text[1:]
    return Path(text)


def _safe_slug(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeLaunchError(f"{label} must be a non-empty string")
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if not slug:
        raise RuntimeLaunchError(f"{label} cannot form an instance ID")
    return slug[:32]


def _regular_tree_members(
    root: Path,
    *,
    excluded_top_level: frozenset[str] = frozenset(),
) -> list[tuple[str, Path, os.stat_result]]:
    """Return sorted tree members without descending into exclusions."""

    try:
        root_info = root.lstat()
    except OSError as exc:
        raise RuntimeLaunchError(
            "runtime tree is not a regular directory"
        ) from exc
    if not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(
        root_info.st_mode
    ):
        raise RuntimeLaunchError("runtime tree is not a regular directory")

    members: list[tuple[str, Path, os.stat_result]] = []

    def collect(directory: Path, prefix: Path) -> None:
        with os.scandir(directory) as stream:
            for member in stream:
                if not prefix.parts and member.name in excluded_top_level:
                    continue
                relative_path = prefix / member.name
                path = directory / member.name
                info = member.stat(follow_symlinks=False)
                members.append((relative_path.as_posix(), path, info))
                if stat.S_ISDIR(info.st_mode):
                    collect(path, relative_path)

    collect(root, Path())
    members.sort(key=lambda member: member[0])
    return members


def _regular_tree_identity_views(
    root: Path,
    *,
    include_directories: bool,
    mode_views: Sequence[bool],
    excluded_top_level: frozenset[str] = frozenset(),
) -> dict[bool, dict[str, Any]]:
    """Measure one tree once and project mode-free and mode-aware views."""

    selected_views = tuple(dict.fromkeys(mode_views))
    if not selected_views or any(
        type(view) is not bool for view in selected_views
    ):
        raise ValueError("tree identity mode views must be booleans")
    entries: dict[bool, list[dict[str, Any]]] = {
        view: [] for view in selected_views
    }
    file_count = 0
    total_bytes = 0
    for relative, path, info in _regular_tree_members(
        root,
        excluded_top_level=excluded_top_level,
    ):
        if stat.S_ISLNK(info.st_mode):
            raise RuntimeLaunchError(
                f"runtime tree contains a symbolic link: {relative}"
            )
        if stat.S_ISDIR(info.st_mode):
            if include_directories:
                for view in selected_views:
                    entries[view].append({
                        "kind": "directory",
                        "path": relative,
                    })
            continue
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeLaunchError(
                f"runtime tree contains a special file: {relative}"
            )
        digest, size = sha256_file(path)
        entry = {
            "path": relative,
            "sha256": digest,
            "size": size,
        }
        if include_directories:
            entry["kind"] = "file"
        for view in selected_views:
            projected_entry = dict(entry)
            if view:
                projected_entry["mode"] = stat.S_IMODE(info.st_mode)
            entries[view].append(projected_entry)
        file_count += 1
        total_bytes += size

    return {
        view: {
            "tree_sha256": "sha256:" + sha256(
                _canonical_bytes(entries[view])
            ).hexdigest(),
            "file_count": file_count,
            "total_bytes": total_bytes,
        }
        for view in selected_views
    }


def _regular_tree_identity(
    root: Path,
    *,
    include_directories: bool,
    include_modes: bool = False,
    excluded_top_level: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    return _regular_tree_identity_views(
        root,
        include_directories=include_directories,
        mode_views=(include_modes,),
        excluded_top_level=excluded_top_level,
    )[include_modes]


def _materialized_instance(
    materialization: Mapping[str, Any],
    launcher: str,
) -> tuple[dict[str, Any], Path]:
    receipt = materialization.get("receipt")
    receipt_version = (
        packwiz_materialization_version(receipt)
        if isinstance(receipt, dict)
        else None
    )
    if (
        receipt_version != 2
        or not isinstance(receipt, dict)
        or not verify_packwiz_materialization_receipt_identity(receipt)
        or materialization.get("format")
        != "workbench-packwiz-materialization-result-v2"
        or materialization.get("schema_version") != 2
        or materialization.get("outcome") not in {"installed", "reused"}
        or receipt.get("state") != "materialized"
        or receipt.get("readiness") != "pack-payload-installed"
        or receipt.get("request")
        != {"side": "client", "launcher": launcher}
        or SHA256_ID_RE.fullmatch(
            str(receipt.get("materialization_id"))
        )
        is None
        or SHA256_ID_RE.fullmatch(str(receipt.get("plan_id"))) is None
    ):
        raise RuntimeLaunchError(
            "launch input is not an exact populated client materialization"
        )
    target = receipt.get("target")
    payload = receipt.get("payload")
    launcher_record = receipt.get("launcher")
    if (
        not isinstance(target, dict)
        or not isinstance(payload, dict)
        or not isinstance(launcher_record, dict)
    ):
        raise RuntimeLaunchError(
            "materialization receipt lacks launcher target identity"
        )
    instance = _local_uri_path(
        target.get("instance_root_uri"),
        "materialized instance",
    ).resolve()
    if not instance.is_dir() or instance.is_symlink():
        raise RuntimeLaunchError(
            f"materialized instance is missing: {instance}"
        )
    manifest = instance / "mmc-pack.json"
    manifest_digest, _manifest_size = sha256_file(manifest)
    if manifest_digest != launcher_record.get("manifest_sha256_after"):
        raise RuntimeLaunchError(
            "materialized launcher manifest has drifted"
        )
    observed_payload = _regular_tree_identity(
        instance / ".minecraft",
        include_directories=False,
        include_modes=True,
    )
    expected_payload = {
        field: payload.get(field)
        for field in ("tree_sha256", "file_count", "total_bytes")
    }
    if observed_payload != expected_payload:
        raise RuntimeLaunchError(
            "materialized client payload has drifted"
        )
    return dict(receipt), instance


def _pe_architecture(executable: Path) -> str:
    try:
        with executable.open("rb") as stream:
            header = stream.read(64)
            if len(header) < 64 or header[:2] != b"MZ":
                raise RuntimeLaunchError(
                    "Windows launcher does not contain a PE header"
                )
            offset = int.from_bytes(header[60:64], "little")
            stream.seek(offset)
            pe_header = stream.read(6)
    except OSError as exc:
        raise RuntimeLaunchError(
            f"cannot inspect Windows launcher: {executable}"
        ) from exc
    if len(pe_header) != 6 or pe_header[:4] != b"PE\0\0":
        raise RuntimeLaunchError(
            "Windows launcher does not contain a valid PE header"
        )
    machine = int.from_bytes(pe_header[4:6], "little")
    architectures = {
        0x8664: "x64",
        0xAA64: "aarch64",
    }
    if machine not in architectures:
        raise RuntimeLaunchError(
            f"unsupported Windows launcher architecture: 0x{machine:04x}"
        )
    return architectures[machine]


def _launcher_host(executable: Path) -> dict[str, str]:
    native = host_platform()
    if executable.suffix.casefold() != ".exe":
        return native
    if native["os"] == "windows":
        return native
    if native["os"] != "linux" or "microsoft" not in (
        platform.release() + " " + platform.version()
    ).casefold():
        raise RuntimeLaunchError(
            "a Windows launcher can only be bridged from Windows or WSL"
        )
    architecture = _pe_architecture(executable)
    return {
        "os": "windows",
        "architecture": architecture,
        "system": "Windows",
        "machine": "AMD64" if architecture == "x64" else "ARM64",
    }


def _probe_launcher(
    executable: Path | str,
    launcher: str,
) -> tuple[Path, dict[str, Any], dict[str, str]]:
    if launcher not in LAUNCHER_FAMILIES:
        raise RuntimeLaunchError(
            f"unsupported client launcher: {launcher}"
        )
    path = Path(executable).expanduser().resolve()
    if not path.is_file():
        raise RuntimeLaunchError(
            f"launcher executable is missing: {path}"
        )
    try:
        completed = subprocess.run(
            [str(path), "--version"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeLaunchError(
            f"cannot execute {launcher} launcher: {path}"
        ) from exc
    output = completed.stdout.strip()
    if completed.returncode or not output or len(output) > 4096:
        raise RuntimeLaunchError(
            f"{launcher} launcher version probe failed"
        )
    normalized = re.sub(r"[^a-z0-9]", "", output.casefold())
    expected = "prismlauncher" if launcher == "prism" else "multimc"
    if expected not in normalized:
        raise RuntimeLaunchError(
            f"launcher reports {output!r}, not {launcher}"
        )
    digest, size = sha256_file(path)
    return path, {
        "family": launcher,
        "version_output": output,
        "executable_uri": path.as_uri(),
        "sha256": digest,
        "size": size,
    }, _launcher_host(path)


def _launcher_path(
    path: Path,
    host: Mapping[str, str],
    *,
    preserve_execution_alias: bool = False,
) -> str:
    resolved = (
        path.expanduser().absolute()
        if preserve_execution_alias
        else path.resolve()
    )
    if host["os"] != "windows":
        return str(resolved)
    if os.name == "nt":
        return str(resolved).replace("\\", "/")
    try:
        completed = subprocess.run(
            ["wslpath", "-w", str(resolved)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeLaunchError(
            "cannot map a Workbench path for the Windows launcher"
        ) from exc
    if completed.returncode or not completed.stdout.strip():
        raise RuntimeLaunchError(
            "cannot map a Workbench path for the Windows launcher"
        )
    return completed.stdout.strip().replace("\\", "/")


def _selected_java(
    result: Mapping[str, Any],
    host: Mapping[str, str],
) -> tuple[Path, dict[str, Any]]:
    if result.get("source") == "managed":
        receipt = result.get("receipt")
        if (
            result.get("format") != "workbench-java-runtime-result-v2"
            or result.get("schema_version") != 2
            or not isinstance(receipt, dict)
            or receipt.get("format") != "workbench-java-runtime-receipt-v2"
            or receipt.get("schema_version") != 2
            or receipt.get("host") != dict(host)
            or not isinstance(receipt.get("target"), dict)
            or not isinstance(receipt.get("probe"), dict)
            or not isinstance(receipt.get("runtime_id"), str)
        ):
            raise RuntimeLaunchError(
                "managed launcher Java result is incomplete"
            )
        executable = _local_uri_path(
            receipt["target"].get("java_uri"),
            "managed launcher Java",
        ).expanduser().absolute()
        identity = {
            "source": "managed",
            "runtime_id": receipt["runtime_id"],
            "policy": dict(receipt.get("policy", {})),
            "host": dict(host),
            "probe": dict(receipt["probe"]),
            "java_uri": executable.as_uri(),
        }
    elif result.get("source") == "external":
        runtime = result.get("runtime")
        if (
            not isinstance(runtime, dict)
            or not isinstance(runtime.get("probe"), dict)
        ):
            raise RuntimeLaunchError(
                "external launcher Java result is incomplete"
            )
        executable = _local_uri_path(
            runtime.get("java_uri"),
            "external launcher Java",
        ).expanduser().absolute()
        identity = {
            "source": "external",
            "runtime_id": "sha256:" + sha256(
                _canonical_bytes({
                    "host": dict(host),
                    "java_uri": executable.as_uri(),
                    "probe": runtime["probe"],
                    "policy": result.get("policy"),
                })
            ).hexdigest(),
            "policy": dict(result.get("policy", {})),
            "host": dict(host),
            "probe": dict(runtime["probe"]),
            "java_uri": executable.as_uri(),
        }
    else:
        raise RuntimeLaunchError(
            "launcher Java result has an unsupported source"
        )
    observed_probe = probe_java(executable)
    recorded_probe = dict(identity["probe"])
    if recorded_probe.get("java_home", "").startswith("@runtime/"):
        for field, value in observed_probe.items():
            if field != "java_home" and recorded_probe.get(field) != value:
                raise RuntimeLaunchError(
                    "launcher Java changed after provisioning"
                )
    elif observed_probe != recorded_probe:
        raise RuntimeLaunchError(
            "launcher Java changed after discovery"
        )
    launch_executable = executable
    if host["os"] == "windows":
        javaw = executable.with_name("javaw.exe")
        if javaw.is_file():
            launch_executable = javaw
    digest, size = sha256_file(launch_executable)
    identity["launch_executable_uri"] = launch_executable.as_uri()
    identity["launch_executable_sha256"] = digest
    identity["launch_executable_size"] = size
    return launch_executable, identity


def _update_instance_config(
    path: Path,
    updates: Mapping[str, str],
) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeLaunchError(
            "projected instance lacks a regular instance.cfg"
        )
    before, before_size = sha256_file(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise RuntimeLaunchError("instance.cfg is not UTF-8 text") from exc
    positions: dict[str, int] = {}
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";", "[")):
            continue
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key not in updates:
            continue
        if key in positions:
            raise RuntimeLaunchError(
                f"instance.cfg contains duplicate setting: {key}"
            )
        positions[key] = index
    for key, value in updates.items():
        line = f"{key}={value}"
        if key in positions:
            lines[positions[key]] = line
        else:
            lines.append(line)
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        raise RuntimeLaunchError(
            "cannot write projected instance.cfg"
        ) from exc
    after, after_size = sha256_file(path)
    return {
        "source_sha256": before,
        "source_size": before_size,
        "configured_sha256": after,
        "configured_size": after_size,
        "settings": dict(updates),
    }


def _probe_launcher_root(
    root: Path,
    launcher: str,
) -> dict[str, Any]:
    """Validate one initialized launcher root without mutating it."""

    if not root.is_dir() or root.is_symlink():
        raise RuntimeLaunchError(
            "launcher data root must already be initialized"
        )
    instances = root / "instances"
    if instances.exists() and (
        not instances.is_dir() or instances.is_symlink()
    ):
        raise RuntimeLaunchError(
            "launcher instances root is not a regular directory"
        )
    config_name = (
        "prismlauncher.cfg" if launcher == "prism" else "multimc.cfg"
    )
    config = root / config_name
    if not config.is_file() or config.is_symlink():
        raise RuntimeLaunchError(
            "launcher data root lacks its initialized configuration"
        )
    accounts = root / "accounts.json"
    if not accounts.is_file() or accounts.is_symlink():
        raise RuntimeLaunchError(
            "launcher data root has not completed account setup; "
            "Prism/MultiMC blocks even CLI offline launch behind its "
            "setup wizard. Open the launcher, complete Quick Setup with an "
            "account or offline profile, then retry runtime-launch"
        )
    digest, size = sha256_file(config)
    return {
        "root_uri": root.as_uri(),
        "instances_root_uri": instances.as_uri(),
        "configuration_uri": config.as_uri(),
        "configuration_sha256": digest,
        "configuration_size": size,
        "configuration_outcome": "existing",
        "account_store": "present-not-read",
    }


def _initialize_launcher_root(
    root: Path,
    launcher: str,
) -> dict[str, Any]:
    """Validate one launcher root, then create its managed instance lane."""

    record = _probe_launcher_root(root, launcher)
    try:
        (root / "instances").mkdir(exist_ok=True)
    except OSError as exc:
        raise RuntimeLaunchError(
            "launcher instances root cannot be initialized"
        ) from exc
    return record


def _project_instance(
    source: Path,
    destination: Path,
    *,
    expected_payload: Mapping[str, Any],
    java_path: str,
    java_probe: Mapping[str, str],
    display_name: str,
    memory_mib: int,
    additional_files: Mapping[str, Path] | None = None,
    additional_config: Mapping[str, str] | None = None,
    prepare_projection: Callable[[Path], None] | None = None,
) -> dict[str, Any]:
    if destination.exists() or destination.is_symlink():
        raise RuntimeLaunchError(
            f"launcher projection already exists: {destination}"
        )
    source_base = _regular_tree_identity(
        source,
        include_directories=True,
        excluded_top_level=frozenset({".minecraft"}),
    )
    source_payload_views = _regular_tree_identity_views(
        source / ".minecraft",
        include_directories=False,
        mode_views=(False, True),
    )
    source_payload = source_payload_views[False]
    source_payload_identity = source_payload_views[True]
    expected_source_payload = {
        field: expected_payload.get(field)
        for field in ("tree_sha256", "file_count", "total_bytes")
    }
    if source_payload_identity != expected_source_payload:
        raise RuntimeLaunchError(
            "launcher projection source payload has drifted"
        )
    staging_root = destination.parent.parent / ".workbench/projection-staging"
    if staging_root.is_symlink() or staging_root.parent.is_symlink():
        raise RuntimeLaunchError(
            "launcher projection staging root is a symbolic link"
        )
    try:
        staging_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeLaunchError(
            "launcher projection staging root cannot be initialized"
        ) from exc
    if not staging_root.is_dir():
        raise RuntimeLaunchError(
            "launcher projection staging root is unavailable"
        )
    staging_parent = Path(tempfile.mkdtemp(
        prefix=f".{destination.name}.",
        dir=staging_root,
    ))
    staged = staging_parent / "instance"
    try:
        shutil.copytree(source, staged, copy_function=shutil.copy2)
        staged_base = _regular_tree_identity(
            staged,
            include_directories=True,
            excluded_top_level=frozenset({".minecraft"}),
        )
        if staged_base != source_base:
            raise RuntimeLaunchError(
                "launcher projection differs from its source base"
            )
        staged_payload = _regular_tree_identity(
            staged / ".minecraft",
            include_directories=False,
        )
        if staged_payload != source_payload:
            raise RuntimeLaunchError(
                "launcher projection differs from its source payload"
            )
        added_files: list[dict[str, Any]] = []
        selected_additional_files = dict(additional_files or {})
        selected_additional_config = dict(additional_config or {})
        for relative, overlay_source in sorted(selected_additional_files.items()):
            relative_path = Path(relative)
            if (
                not relative
                or relative_path.is_absolute()
                or ".." in relative_path.parts
                or "\\" in relative
            ):
                raise RuntimeLaunchError(
                    f"launcher projection file path is unsafe: {relative!r}"
                )
            lexical_source = Path(overlay_source)
            if lexical_source.is_symlink():
                raise RuntimeLaunchError(
                    f"launcher projection file is unavailable: {lexical_source}"
                )
            selected_source = lexical_source.resolve()
            if not selected_source.is_file():
                raise RuntimeLaunchError(
                    f"launcher projection file is unavailable: {selected_source}"
                )
            target = staged / relative_path
            if target.exists() or target.is_symlink():
                raise RuntimeLaunchError(
                    f"launcher projection file already exists: {relative}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(selected_source, target)
            source_digest, source_size = sha256_file(selected_source)
            target_digest, target_size = sha256_file(target)
            if (source_digest, source_size) != (target_digest, target_size):
                raise RuntimeLaunchError(
                    f"launcher projection file copy drifted: {relative}"
                )
            added_files.append({
                "path": relative_path.as_posix(),
                "sha256": target_digest,
                "size": target_size,
            })
        if prepare_projection is not None:
            prepare_projection(staged)
        base_configuration = {
            "AutomaticJava": "false",
            "CloseAfterLaunch": "false",
            "IgnoreJavaCompatibility": "true",
            "JavaArchitecture": "64",
            "JavaPath": java_path,
            "JavaRealArchitecture": str(java_probe["os_arch"]),
            "JavaVendor": "Temurin",
            "JavaVersion": str(java_probe["java_version"]),
            "ManagedPack": "false",
            "MaxMemAlloc": str(memory_mib),
            "MinMemAlloc": "512",
            "OverrideJavaLocation": "true",
            "OverrideMemory": "true",
            "QuitAfterGameStop": "true",
            "ShowConsoleOnError": "true",
            "name": display_name,
        }
        if set(base_configuration) & set(selected_additional_config):
            raise RuntimeLaunchError(
                "additional launcher configuration overrides a core setting"
            )
        if any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or "\n" in key
            or "\n" in value
            or "\r" in key
            or "\r" in value
            for key, value in selected_additional_config.items()
        ):
            raise RuntimeLaunchError(
                "additional launcher configuration is invalid"
            )
        configuration = _update_instance_config(
            staged / "instance.cfg",
            {**base_configuration, **selected_additional_config},
        )
        prepared_base = _regular_tree_identity(
            staged,
            include_directories=True,
            excluded_top_level=frozenset({".minecraft"}),
        )
        prepared_payload = _regular_tree_identity(
            staged / ".minecraft",
            include_directories=False,
            include_modes=True,
        )
        try:
            staged.rename(destination)
        except OSError as exc:
            raise RuntimeLaunchError(
                "cannot publish launcher projection atomically"
            ) from exc
    finally:
        if staging_parent.exists():
            shutil.rmtree(staging_parent, ignore_errors=True)
    return {
        "source_instance_uri": source.as_uri(),
        "source_base": source_base,
        "payload": {
            "materialized": {
                field: expected_payload.get(field)
                for field in ("tree_sha256", "file_count", "total_bytes")
            },
            "portable_projection": source_payload,
            "prepared_projection": prepared_payload,
        },
        "prepared_base": prepared_base,
        "configuration": configuration,
        "added_files": added_files,
        "publication": {
            "staged_outside_launcher_instances": True,
            "atomic_rename": True,
        },
        "projection_uri": destination.as_uri(),
    }


def _capture_file(
    source: Path,
    destination: Path,
    label: str,
    *,
    redact_values: Sequence[str] = (),
) -> dict[str, Any]:
    if not source.is_file() or source.is_symlink():
        return {
            "label": label,
            "state": "absent",
            "source_uri": source.as_uri(),
        }
    try:
        size = source.stat().st_size
        if size > MAX_CAPTURE_BYTES:
            return {
                "label": label,
                "state": "too-large",
                "source_uri": source.as_uri(),
                "size": size,
                "limit": MAX_CAPTURE_BYTES,
            }
        with source.open("rb") as input_stream:
            payload = input_stream.read(MAX_CAPTURE_BYTES + 1)
        if len(payload) > MAX_CAPTURE_BYTES:
            return {
                "label": label,
                "state": "too-large",
                "source_uri": source.as_uri(),
                "size": len(payload),
                "limit": MAX_CAPTURE_BYTES,
            }
        redaction_matches = 0
        for value in redact_values:
            encoded = value.encode("utf-8")
            matches = payload.count(encoded)
            if matches:
                payload = payload.replace(
                    encoded,
                    b"<redacted-launch-identity>",
                )
                redaction_matches += matches
        with destination.open("xb") as output_stream:
            output_stream.write(payload)
    except OSError as exc:
        return {
            "label": label,
            "state": "capture-failed",
            "source_uri": source.as_uri(),
            "reason": str(exc),
        }
    record = {
        "label": label,
        "state": "captured",
        "source_uri": source.as_uri(),
        "capture_uri": destination.as_uri(),
        "sha256": sha256(payload).hexdigest(),
        "size": len(payload),
    }
    if redact_values:
        record["content_treatment"] = {
            "kind": "exact-launch-identity-redaction",
            "match_count": redaction_matches,
            "replacement": "<redacted-launch-identity>",
        }
    return record


def _latest_crash(root: Path) -> Path | None:
    if not root.is_dir() or root.is_symlink():
        return None
    candidates = [
        path
        for path in root.glob("crash-*.txt")
        if path.is_file() and not path.is_symlink()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def _windows_launcher_processes(
    image_name: str,
) -> dict[int, str] | None:
    try:
        completed = subprocess.run(
            [
                "tasklist.exe",
                "/FI",
                f"IMAGENAME eq {image_name}",
                "/V",
                "/FO",
                "CSV",
                "/NH",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode:
        return None
    processes: dict[int, str] = {}
    try:
        rows = csv.reader(completed.stdout.splitlines())
        for row in rows:
            if len(row) < 9 or row[0].casefold() != image_name.casefold():
                continue
            try:
                pid = int(row[1])
            except ValueError:
                continue
            processes[pid] = row[-1]
    except csv.Error:
        return None
    return processes


def _windows_blocker_kind(title: str) -> str | None:
    title = title.casefold()
    if "quick setup" in title:
        return "launcher-setup-required"
    if "account refresh failed" in title or "no accounts" in title:
        return "launcher-account-refresh-required"
    return None


def _windows_launcher_blocker(
    image_name: str,
    processes_before: Mapping[int, str] | None,
) -> dict[str, Any] | None:
    if processes_before is None:
        return None
    current = _windows_launcher_processes(image_name)
    if current is None:
        return None
    for pid, title in current.items():
        failure_kind = _windows_blocker_kind(title)
        if failure_kind is None:
            continue
        prior_title = processes_before.get(pid)
        if (
            prior_title is not None
            and _windows_blocker_kind(prior_title) == failure_kind
        ):
            continue
        return {
            "failure_kind": failure_kind,
            "windows_pid": pid,
        }
    return None


def _monitor_launch(
    process: subprocess.Popen[bytes],
    instance: Path,
    *,
    launcher_host: Mapping[str, str],
    launcher_image_name: str | None = None,
    launcher_processes_before: Mapping[int, str] | None = None,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> dict[str, Any]:
    if (
        not math.isfinite(timeout_seconds)
        or not math.isfinite(poll_interval_seconds)
        or timeout_seconds <= 0
        or poll_interval_seconds <= 0
    ):
        raise RuntimeLaunchError(
            "launch timeout and poll interval must be positive"
        )
    latest_log = instance / ".minecraft/logs/latest.log"
    crash_root = instance / ".minecraft/crash-reports"
    deadline = time.monotonic() + timeout_seconds
    offset = 0
    tail = ""
    dispatched = False
    next_window_check = 0.0
    while True:
        crash = _latest_crash(crash_root)
        if crash is not None:
            return {
                "outcome": "failed",
                "failure_kind": "minecraft-crash-report",
                "crash_report": crash,
                "launcher_returncode": process.poll(),
                "process_state": (
                    "running"
                    if process.poll() is None
                    else ("dispatched" if dispatched else "exited")
                ),
            }
        if latest_log.is_file() and not latest_log.is_symlink():
            try:
                size = latest_log.stat().st_size
                if size < offset:
                    offset = 0
                    tail = ""
                with latest_log.open(
                    "r",
                    encoding="utf-8",
                    errors="replace",
                ) as stream:
                    stream.seek(offset)
                    appended = stream.read()
                    offset = stream.tell()
            except OSError:
                appended = ""
            if appended:
                tail = (tail + appended)[-MAX_MONITOR_TAIL_BYTES:]
                match = CLIENT_LOADED_RE.search(tail)
                if match is not None:
                    return {
                        "outcome": "checkpoint-reached",
                        "checkpoint": {
                            "id": "fml-client-loaded",
                            "marker": match.group(0),
                            "source": "minecraft-latest-log",
                        },
                        "launcher_returncode": process.poll(),
                        "process_state": (
                            "running"
                            if process.poll() is None
                            else (
                                "dispatched" if dispatched else "exited"
                            )
                        ),
                    }
        returncode = process.poll()
        if returncode is not None:
            if returncode:
                return {
                    "outcome": "failed",
                    "failure_kind": "stopped-before-checkpoint",
                    "launcher_returncode": returncode,
                    "process_state": "exited",
                }
            dispatched = True
        now = time.monotonic()
        if (
            launcher_host["os"] == "windows"
            and launcher_image_name is not None
            and now >= next_window_check
        ):
            blocker = _windows_launcher_blocker(
                launcher_image_name,
                launcher_processes_before,
            )
            next_window_check = now + 2.0
            if blocker is not None:
                return {
                    "outcome": "failed",
                    **blocker,
                    "launcher_returncode": process.poll(),
                    "process_state": "blocked-window",
                }
        if now >= deadline:
            return {
                "outcome": "timed-out",
                "failure_kind": "checkpoint-timeout",
                "launcher_returncode": process.poll(),
                "process_state": (
                    "dispatched" if dispatched else "running"
                ),
            }
        time.sleep(poll_interval_seconds)


def _reap_detached_process(process: subprocess.Popen[bytes]) -> None:
    """Retain and reap a launcher that intentionally outlives the command."""

    if process.poll() is not None:
        process.wait()
        return
    thread = threading.Thread(
        target=process.wait,
        name=f"workbench-launcher-{process.pid}",
        daemon=True,
    )
    thread.start()


def _write_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(
                receipt,
                stream,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            stream.write("\n")
        temporary.rename(path)
    except OSError as exc:
        raise RuntimeLaunchError(
            "cannot retain runtime launch receipt"
        ) from exc
    finally:
        if temporary.exists():
            temporary.unlink()


def launch_materialized_client(
    materialization: Mapping[str, Any],
    *,
    suite_root: Path | str,
    state_root: Path | str | None = None,
    launcher: str,
    launcher_executable: Path | str,
    launcher_root: Path | str,
    java_result: Mapping[str, Any],
    launcher_profile: str | None = None,
    compatibility_patches: Sequence[Path | str] = (),
    memory_mib: int = 8192,
    offline_name: str = "Workbench",
    timeout_seconds: float = 600.0,
    poll_interval_seconds: float = 1.0,
) -> dict[str, Any]:
    """Project and launch one exact populated client fixture."""

    if memory_mib < 1024 or memory_mib > 131072:
        raise RuntimeLaunchError(
            "launcher memory must be between 1024 and 131072 MiB"
        )
    if (
        launcher_profile is None
        and OFFLINE_NAME_RE.fullmatch(offline_name) is None
    ):
        raise RuntimeLaunchError(
            "offline player name must be 3-16 letters, digits, or underscores"
        )
    if launcher_profile is not None and (
        not launcher_profile.strip()
        or len(launcher_profile) > 128
        or any(ord(character) < 32 for character in launcher_profile)
    ):
        raise RuntimeLaunchError(
            "launcher profile must be printable and at most 128 characters"
        )
    suite = Path(suite_root).resolve()
    state = (
        default_suite_state_root(suite)
        if state_root is None
        else Path(state_root).expanduser().resolve()
    )
    receipt, source_instance = _materialized_instance(
        materialization,
        launcher,
    )
    executable, launcher_identity, launcher_host = _probe_launcher(
        launcher_executable,
        launcher,
    )
    java_executable, java_identity = _selected_java(
        java_result,
        launcher_host,
    )
    root = Path(launcher_root).expanduser().resolve()
    started = _utc_now()
    materialization_id = str(receipt["materialization_id"])
    project_name = receipt.get("project", {}).get("name")
    project_slug = _safe_slug(project_name, "project name")
    stamp = started.strftime("%Y%m%dT%H%M%S%fZ")
    instance_id = (
        f"workbench-{project_slug}-"
        f"{materialization_id.removeprefix('sha256:')[:12]}-{stamp}"
    )
    launcher_root_record = _initialize_launcher_root(root, launcher)
    projection = root / "instances" / instance_id
    display_name = (
        f"Workbench {project_name} Cleanroom "
        f"{materialization_id.removeprefix('sha256:')[:8]}"
    )
    projection_record = _project_instance(
        source_instance,
        projection,
        expected_payload=receipt["payload"],
        java_path=_launcher_path(
            java_executable,
            launcher_host,
            preserve_execution_alias=True,
        ),
        java_probe=java_identity["probe"],
        display_name=display_name,
        memory_mib=memory_mib,
    )
    try:
        compatibility_records = apply_compatibility_patches(
            projection,
            compatibility_patches,
        )
    except RuntimeCompatibilityError as exc:
        raise RuntimeLaunchError(str(exc)) from exc
    if compatibility_records:
        projection_record["compatibility_patches"] = compatibility_records

    plan_id = str(receipt["plan_id"])
    run_root = (
        state
        / "evidence/runtime"
        / plan_id.removeprefix("sha256:")[:16]
        / "launches"
        / instance_id
    )
    if run_root.exists() or run_root.is_symlink():
        raise RuntimeLaunchError(
            f"runtime launch evidence already exists: {run_root}"
        )
    run_root.mkdir(parents=True)
    live_launcher_log = run_root / "launcher-command.live.log"
    command = [
        str(executable),
        "--dir",
        _launcher_path(root, launcher_host),
        "--launch",
        instance_id,
    ]
    redacted_command = list(command)
    if launcher_profile is not None:
        command.extend(["--profile", launcher_profile])
        redacted_command.extend(["--profile", "<redacted-profile>"])
    else:
        command.extend(["--offline", offline_name])
        redacted_command.extend(["--offline", offline_name])
    launcher_image_name: str | None = None
    launcher_processes_before: Mapping[int, str] | None = None
    if launcher_host["os"] == "windows":
        launcher_image_name = executable.name
        launcher_processes_before = _windows_launcher_processes(
            launcher_image_name
        )
    launch_error: str | None = None
    process: subprocess.Popen[bytes] | None = None
    with live_launcher_log.open("xb") as log_stream:
        log_stream.write(
            _canonical_bytes({
                "command": redacted_command,
                "cwd": str(root),
                "launcher_output": "discarded-account-boundary",
            })
            + b"\n"
        )
        log_stream.flush()
        try:
            process = subprocess.Popen(
                command,
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            launch_error = str(exc)

    if process is None:
        observation: dict[str, Any] = {
            "outcome": "failed",
            "failure_kind": "launcher-exec-failed",
            "launcher_returncode": None,
            "process_state": "not-started",
        }
    else:
        observation = _monitor_launch(
            process,
            projection,
            launcher_host=launcher_host,
            launcher_image_name=launcher_image_name,
            launcher_processes_before=launcher_processes_before,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        _reap_detached_process(process)
    observed = _utc_now()
    game_log_redactions = (
        () if launcher_profile is None else (launcher_profile,)
    )
    evidence = [
        _capture_file(
            projection / ".minecraft/logs/latest.log",
            run_root / "minecraft-latest.log",
            "minecraft-latest-log",
            redact_values=game_log_redactions,
        ),
        _capture_file(
            live_launcher_log,
            run_root / "launcher-command.log",
            "launcher-command-log",
        ),
    ]
    crash = observation.get("crash_report")
    if isinstance(crash, Path):
        evidence.append(_capture_file(
            crash,
            run_root / "minecraft-crash-report.txt",
            "minecraft-crash-report",
            redact_values=game_log_redactions,
        ))
        observation = {
            key: value
            for key, value in observation.items()
            if key != "crash_report"
        }

    identity = {
        "plan_id": plan_id,
        "materialization_id": materialization_id,
        "instance_id": instance_id,
        "started_at": _timestamp(started),
        "launcher": {
            "family": launcher,
            "sha256": launcher_identity["sha256"],
            "version_output": launcher_identity["version_output"],
        },
        "java_runtime_id": java_identity["runtime_id"],
        "projection_uri": projection.as_uri(),
    }
    if compatibility_records:
        identity["compatibility_patches"] = compatibility_records
    launch_id = "sha256:" + sha256(
        _canonical_bytes(identity)
    ).hexdigest()
    launch_receipt: dict[str, Any] = {
        "format": (
            "workbench-runtime-launch-receipt-v2"
            if compatibility_records
            else "workbench-runtime-launch-receipt-v1"
        ),
        "schema_version": 2 if compatibility_records else 1,
        "launch_id": launch_id,
        "operation_class": "local-mutation",
        "state": "observed",
        "outcome": observation["outcome"],
        "started_at": _timestamp(started),
        "observed_at": _timestamp(observed),
        "plan_id": plan_id,
        "materialization_id": materialization_id,
        "project": dict(receipt.get("project", {})),
        "launcher": {
            **launcher_identity,
            "host": launcher_host,
            "data_root": launcher_root_record,
            "instance_id": instance_id,
            "projection_uri": projection.as_uri(),
            "command": redacted_command,
            "pid": None if process is None else process.pid,
            "process_state": observation["process_state"],
            "returncode": observation["launcher_returncode"],
        },
        "java": java_identity,
        "launch_policy": {
            "account_mode": (
                "launcher-profile"
                if launcher_profile is not None
                else "offline"
            ),
            "launcher_profile": (
                "explicit-redacted"
                if launcher_profile is not None
                else "launcher-default"
            ),
            "offline_name": (
                None if launcher_profile is not None else offline_name
            ),
            "memory_mib": memory_mib,
            "timeout_seconds": timeout_seconds,
            "checkpoint": "fml-client-loaded",
            "launcher_output": "discarded-account-boundary",
        },
        "projection": projection_record,
        "observation": {
            key: value
            for key, value in observation.items()
            if key not in {
                "outcome",
                "process_state",
                "launcher_returncode",
            }
        },
        "evidence": evidence,
        "target": {
            "run_root_uri": run_root.as_uri(),
            "receipt_uri": (
                run_root / "runtime-launch-v1.json"
            ).as_uri(),
        },
        "limitations": [
            (
                "The FML client-loaded marker proves loader completion; "
                "it is not a visual assertion about a custom main menu."
            ),
            (
                "The launcher projection is mutable runtime state and is "
                "not the canonical materialization."
            ),
            (
                "Workbench does not read a Minecraft account into the "
                "receipt; a named profile remains launcher authority."
            ),
        ],
    }
    if compatibility_records:
        launch_receipt["compatibility_patches"] = compatibility_records
    if launch_error is not None:
        launch_receipt["observation"]["reason"] = launch_error
    receipt_path = run_root / "runtime-launch-v1.json"
    _write_receipt(receipt_path, launch_receipt)
    return {
        "format": (
            "workbench-runtime-launch-result-v2"
            if compatibility_records
            else "workbench-runtime-launch-result-v1"
        ),
        "schema_version": 2 if compatibility_records else 1,
        "outcome": observation["outcome"],
        "receipt": launch_receipt,
    }


def launch_project_runtime(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    launcher: str = "prism",
    state_root: Path | str | None = None,
    launcher_executable: Path | str,
    launcher_root: Path | str,
    launcher_profile: str | None = None,
    launcher_java: Path | str | None = None,
    launcher_java_state: Path | str | None = None,
    packwiz_executable: Path | str | None = None,
    seed_roots: Sequence[Path | str] = (),
    memory_mib: int = 8192,
    offline_name: str = "Workbench",
    compatibility_patches: Sequence[Path | str] = (),
    timeout_seconds: float = 600.0,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Materialize, project, and launch one Cleanroom client."""

    suite = Path(suite_root).resolve()
    if configuration is not None and config_path is not None:
        raise RuntimeLaunchError(
            "configuration and config_path are mutually exclusive"
        )
    try:
        active_configuration = configuration or load_workbench_configuration(
            suite,
            CONFIGURATION_PATH if config_path is None else config_path,
        )
        resolved_bindings = active_configuration.resolve_bindings(
            names=("java_candidate_home",),
        )
    except WorkbenchConfigurationError as exc:
        raise RuntimeLaunchError(
            f"Workbench configuration cannot be loaded: {exc}"
        ) from exc
    state = (
        default_suite_state_root(suite)
        if state_root is None
        else Path(state_root).expanduser().resolve()
    )
    explicit_java_state = (
        None
        if launcher_java_state is None
        else Path(launcher_java_state).expanduser().resolve()
    )
    try:
        materialization = materialize_project_runtime(
            suite,
            workspace_root,
            launcher=launcher,
            state_root=state,
            packwiz_executable=packwiz_executable,
            seed_roots=seed_roots,
            configuration=active_configuration,
            resolved_bindings=resolved_bindings,
        )
    except PackwizMaterializationError as exc:
        raise RuntimeLaunchError(str(exc)) from exc
    executable, _launcher_identity, selected_host = _probe_launcher(
        launcher_executable,
        launcher,
    )
    native_host = host_platform()
    cross_host = (
        selected_host["os"] != native_host["os"]
        or selected_host["architecture"] != native_host["architecture"]
    )
    if explicit_java_state is None:
        java_state = (
            Path(launcher_root).expanduser().resolve() / ".workbench"
            if cross_host
            else state
        )
    else:
        java_state = explicit_java_state
    candidates = (
        []
        if launcher_java is None
        else [("launcher-java", Path(launcher_java))]
    )
    try:
        java_result = ensure_java_runtime(
            suite,
            host=selected_host,
            state_root=java_state,
            candidates=candidates if cross_host or launcher_java else None,
            configuration=active_configuration,
            resolved_bindings=resolved_bindings,
        )
    except JavaRuntimeError as exc:
        raise RuntimeLaunchError(str(exc)) from exc
    result = launch_materialized_client(
        materialization,
        suite_root=suite,
        state_root=state,
        launcher=launcher,
        launcher_executable=executable,
        launcher_root=launcher_root,
        java_result=java_result,
        launcher_profile=launcher_profile,
        compatibility_patches=compatibility_patches,
        memory_mib=memory_mib,
        offline_name=offline_name,
        timeout_seconds=timeout_seconds,
    )
    result["materialization_outcome"] = materialization["outcome"]
    result["java_outcome"] = java_result["outcome"]
    return result
