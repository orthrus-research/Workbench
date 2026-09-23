"""Installed client and dedicated-server execution for profile runtime pairs."""

from __future__ import annotations

from workbench_crucible.runtime_pair import RuntimeExecutionServices
from workbench_blueprints.reviewed_plan import ReviewedPlanPorts
from .server_observation import (
    FML_LOADED_TEXT,
    GROOVY_SCRIPT_FAILURE_TEXT,
    SERVER_READY_RE,
    SUSY_PACK_READY_RE,
    TERMINAL_RE,
    _shutdown_acknowledgment,
)

PROFILE_API_VERSION = 1

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
import tomllib
from typing import Any, Mapping, Sequence

from .material_fluid_runtime_pair import (
    EXECUTION_ID_PREFIX,
    EXECUTION_RESULT_FORMAT,
    InstalledSupersymmetryRuntimeConfig,
    MARKER_PREFIX,
    MaterialFluidRuntimePairError,
    _canonical,
    _content_id,
    _local_uri,
    _probe_spec,
    _write_immutable,
    interpret_material_fluid_runtime_marker,
)


SERVER_MATERIALIZATION_FORMAT = (
    "workbench-supersymmetry-staged-pack-server-materialization-v1"
)
SERVER_LAUNCH_FORMAT = "workbench-supersymmetry-staged-pack-server-launch-v1"
CLIENT_COMPATIBILITY_FORMAT = (
    "workbench-supersymmetry-staged-client-compatibility-policy-v1"
)
SERVER_MATERIALIZATION_ID_PREFIX = (
    "workbench-supersymmetry-staged-pack-server-materialization:sha256:"
)
SERVER_LAUNCH_ID_PREFIX = "workbench-supersymmetry-staged-pack-server-launch:sha256:"
CLIENT_COMPATIBILITY_ID_PREFIX = (
    "workbench-supersymmetry-staged-client-compatibility-policy:sha256:"
)
_PROBE_TARGET = (
    ".minecraft/groovy/postInit/utils/ZzzzWorkbenchMaterialFluidRecipeAssertion.groovy"
)
_MAX_LOG_BYTES = 64 * 1024 * 1024

_NATIVE_CLIENT_COMPONENTS = {
    "recurrent-complex": {
        "path": "mods/recurrent-complex.pw.toml",
        "filename": "RecurrentComplex-1.4.8.7.jar",
        "download_hash_format": "sha1",
        "download_hash": "9b7eee3f3a71cb20a27490860812cd3ababc4258",
    },
    "susycore": {
        "path": "mods/susycore.pw.toml",
        "filename": "Susy-Core-0.1.116.jar",
        "download_hash_format": "sha1",
        "download_hash": "1b530a4b5a32fc4d4c47559cb558644a9796390e",
    },
}
_LEGACY_PATCHED_CLIENT_COMPONENTS = {
    "recurrent-complex": {
        "filename": "RecurrentComplex-1.4.8.6.jar",
        "download_hash_format": "sha1",
        "download_hash": "31059d070bee48368050cf774ea3e8f9caea0356",
    },
    "susycore": {
        "filename": "supersymmetry-v0.1.111.jar",
        "download_hash_format": "sha1",
        "download_hash": "7231c23bb8ce324d506f62e93d131696c41c6d81",
    },
}


def _fail(message: str) -> None:
    raise MaterialFluidRuntimePairError(message)


def _utcnow() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _regular_file(path: Path | str, label: str) -> Path:
    selected = Path(path).expanduser().resolve()
    try:
        state = selected.lstat()
    except OSError as exc:
        raise MaterialFluidRuntimePairError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISREG(state.st_mode):
        _fail(f"{label} must be one regular file")
    return selected


def _regular_directory(path: Path | str, label: str) -> Path:
    selected = Path(path).expanduser().resolve()
    try:
        state = selected.lstat()
    except OSError as exc:
        raise MaterialFluidRuntimePairError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
        _fail(f"{label} must be one regular directory")
    return selected


def _pack_component_binding(
    workspace: Path,
    *,
    component: str,
    relative: str,
) -> dict[str, Any]:
    path = workspace / relative
    try:
        state = path.lstat()
        raw = path.read_bytes()
        value = tomllib.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise MaterialFluidRuntimePairError(
            f"staged {component} Packwiz metadata is unavailable or invalid"
        ) from exc
    download = value.get("download") if isinstance(value, dict) else None
    if (
        stat.S_ISLNK(state.st_mode)
        or not stat.S_ISREG(state.st_mode)
        or len(raw) > 64 * 1024
        or not isinstance(download, dict)
        or type(value.get("filename")) is not str
        or type(download.get("hash-format")) is not str
        or type(download.get("hash")) is not str
    ):
        _fail(f"staged {component} Packwiz metadata is unsafe or malformed")
    return {
        "component": component,
        "metadata_path": relative,
        "metadata_sha256": sha256(raw).hexdigest(),
        "filename": value["filename"],
        "download_hash_format": download["hash-format"],
        "download_hash": download["hash"],
    }


def _staged_client_compatibility_policy(
    suite: Path,
    workspace: Path,
    *,
    plan_id: str,
    stage_id: str,
    revision: str,
    services: RuntimeExecutionServices,
) -> tuple[tuple[Path, ...], dict[str, Any]]:
    """Resolve only exact profile-reviewed compatibility for this staged pack."""

    bindings = [
        _pack_component_binding(
            workspace,
            component=component,
            relative=str(expected["path"]),
        )
        for component, expected in _NATIVE_CLIENT_COMPONENTS.items()
    ]
    by_component = {row["component"]: row for row in bindings}
    native = all(
        by_component[component][key] == value
        for component, expected in _NATIVE_CLIENT_COMPONENTS.items()
        for key, value in expected.items()
        if key != "path"
    )
    legacy = all(
        by_component[component][key] == value
        for component, expected in _LEGACY_PATCHED_CLIENT_COMPONENTS.items()
        for key, value in expected.items()
    )
    if native:
        authorized: tuple[Path, ...] = ()
        mode = "exact-native-compatible-pair"
        patches: list[dict[str, Any]] = []
        identity_patches: list[dict[str, Any]] = []
    elif legacy:
        authorized, retained = services.material_fluid_runtime_compatibility_policy(
            suite
        )
        mode = "required-exact-profile-patch-set"
        patches = deepcopy(retained["patches"])
        identity_patches = deepcopy(retained["identity_patches"])
    else:
        _fail(
            "staged Recurrent Complex and SusyCore pair lacks reviewed client "
            "compatibility authority"
        )
    body = {
        "format": CLIENT_COMPATIBILITY_FORMAT,
        "schema_version": 1,
        "plan_id": plan_id,
        "stage_id": stage_id,
        "staged_revision": revision,
        "mode": mode,
        "components": bindings,
        "patches": patches,
        "identity_patches": identity_patches,
        "limitations": [
            "This policy applies only to the exact staged Packwiz component pair.",
            "Native compatibility means no profile archive mutation is authorized.",
        ],
    }
    return authorized, {
        **body,
        "policy_id": _content_id(CLIENT_COMPATIBILITY_ID_PREFIX, body),
    }


def _owner_ref(
    path: Path,
    *,
    owner_id: str,
    record_id: str,
    record_kind: str,
    outcome: str,
    services: RuntimeExecutionServices,
) -> dict[str, Any]:
    path = _regular_file(path, "physical runtime owner record")
    digest, size = services.sha256_file(path)
    return {
        "owner_id": owner_id,
        "record_id": record_id,
        "record_kind": record_kind,
        "uri": path.as_uri(),
        "sha256": "sha256:" + digest,
        "size": size,
        "outcome": outcome,
    }


def _discover_packwiz(workspace: Path) -> Path:
    candidates = (
        (workspace / "packwiz", workspace / "packwiz.exe")
        if os.name == "posix"
        else (workspace / "packwiz.exe", workspace / "packwiz")
    )
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink():
            return candidate.resolve()
    discovered = shutil.which("packwiz")
    if discovered is None:
        _fail("staged Packwiz checkout lacks an executable Packwiz tool")
    return _regular_file(discovered, "Packwiz executable")


def _probe_material(
    suite: Path,
    plan: Mapping[str, Any],
    *,
    role: str,
    side: str,
    root: Path,
) -> tuple[Path, dict[str, Any]]:
    authority, spec = _probe_spec(suite, plan)
    raw = authority.build_material_fluid_recipe_probe(spec)
    if side == "server":
        human = json.dumps(spec.translation, ensure_ascii=False).encode("utf-8")
        key = json.dumps(
            "susy.material." + spec.registry_name,
            ensure_ascii=False,
        ).encode("utf-8")
        if raw.count(human) != 1:
            _fail("server probe localization expectation is not uniquely replaceable")
        raw = raw.replace(human, key)
    else:
        raw += b"""\n// Disposable client closure owned by the dynamic F01 probe.\nThread.startDaemon('Workbench-F01-Client-Close') {\n    Thread.sleep(90000L)\n    def client = net.minecraft.client.Minecraft.getMinecraft()\n    Runnable close = { client.shutdown() } as Runnable\n    client.addScheduledTask(close)\n}\n"""
    probe_root = root / "probe"
    probe_root.mkdir(mode=0o700)
    source_name = f"MaterialFluidRecipeAssertion-{role}-{side}.groovy"
    source = probe_root / source_name
    source.write_bytes(raw)
    material = {
        "plan_id": plan["id"],
        "probe_id": spec.probe_id,
        "role": role,
        "side": side,
        "source_sha256": sha256(raw).hexdigest(),
        "target": _PROBE_TARGET,
    }
    overlay = {
        "format": "workbench-runtime-compatibility-file-overlay-v1",
        "schema_version": 1,
        "patch_id": "workbench-supersymmetry-material-fluid-runtime-probe:sha256:"
        + sha256(_canonical(material)).hexdigest(),
        "description": (
            "Disposable dynamic Supersymmetry material-fluid role observation; "
            "it grants no construction or support authority."
        ),
        "target": {"path": _PROBE_TARGET, "must_be_absent": True},
        "source": {"path": source_name, "sha256": material["source_sha256"]},
    }
    overlay_path = probe_root / "overlay-v1.json"
    overlay_raw = json.dumps(overlay, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    overlay_path.write_bytes(overlay_raw)
    record = {
        "probe_id": spec.probe_id,
        "source_plan_id": plan["id"],
        "role": role,
        "side": side,
        "script_sha256": material["source_sha256"],
        "script_size": len(raw),
        "script_uri": source.as_uri(),
        "overlay_id": overlay["patch_id"],
        "overlay_spec_sha256": sha256(overlay_raw).hexdigest(),
        "overlay_spec_uri": overlay_path.as_uri(),
        "projection_target": _PROBE_TARGET,
        "operation": "disposable-observation-file-overlay",
    }
    return overlay_path, record


def _execution_result(
    request: Mapping[str, Any],
    plan: Mapping[str, Any],
    stage: Mapping[str, Any],
    *,
    observation: Mapping[str, Any],
    owner_refs: Sequence[Mapping[str, Any]],
    limitations: Sequence[str],
) -> dict[str, Any]:
    passed = observation.get("state") == "observed" and all(
        state == "observed" for state in observation.get("assertions", {}).values()
    )
    body = {
        "format": EXECUTION_RESULT_FORMAT,
        "schema_version": 1,
        "request_id": request["request_id"],
        "plan_id": plan["id"],
        "stage_id": stage["stage_id"],
        "role": request["role"],
        "side": request["side"],
        "state": "complete" if passed else "failed",
        "outcome": "passed" if passed else "failed",
        "observation": deepcopy(dict(observation)),
        "cleanup": {"contained": True, "owned_processes_running": False},
        "owner_refs": [deepcopy(dict(row)) for row in owner_refs],
        "limitations": list(limitations),
    }
    return {**body, "execution_id": _content_id(EXECUTION_ID_PREFIX, body)}


def _client_refs(
    evidence: Mapping[str, Any],
    *,
    services: RuntimeExecutionServices,
) -> list[dict[str, Any]]:
    pairs = (
        (
            "final_launch_receipt",
            "workbench-runtime-launch",
            "workbench-runtime-launch-receipt-v3",
        ),
        (
            "runtime_session_receipt",
            "workbench-runtime-observer",
            "workbench-runtime-observation-session-v1",
        ),
    )
    refs: list[dict[str, Any]] = []
    for key, owner_id, kind in pairs:
        value = evidence.get(key)
        if not isinstance(value, Mapping):
            _fail("client runtime evidence projection is incomplete")
        path = _local_uri(value.get("uri"), "client runtime receipt")
        refs.append(
            _owner_ref(
                path,
                owner_id=owner_id,
                record_id=str(value.get("id")),
                record_kind=kind,
                outcome="passed",
                services=services,
            )
        )
    return refs


def execute_installed_client(
    config: InstalledSupersymmetryRuntimeConfig,
    *,
    request: Mapping[str, Any],
    suite_root: Path,
    plan: Mapping[str, Any],
    stage: Mapping[str, Any],
    execution_root: Path,
    services: RuntimeExecutionServices,
    construction: ReviewedPlanPorts,
) -> Mapping[str, Any]:
    """Execute one staged role through the existing installed client owners."""

    suite = Path(suite_root).resolve()
    root = _regular_directory(execution_root, "client execution root")
    workspace = _local_uri(stage.get("workspace_uri"), "staged Packwiz workspace")
    launcher_root = _regular_directory(
        config.client_launcher_root, "installed launcher root"
    )
    launcher_executable = _regular_file(
        config.client_launcher_executable, "installed launcher executable"
    )
    state = root / "runtime-state"
    expected_plan = services.plan_project_runtime(
        suite,
        workspace,
        side="client",
        launcher=config.launcher,
        state_root=state,
    )
    if (
        expected_plan.get("state") != "ready"
        or expected_plan.get("blockers") != []
        or expected_plan.get("workspace", {}).get("revision") != stage["revision"]
    ):
        _fail("staged client runtime plan is not exactly ready")
    authorized_patches, compatibility_policy = _staged_client_compatibility_policy(
        suite,
        workspace,
        plan_id=plan["id"],
        stage_id=stage["stage_id"],
        revision=stage["revision"],
        services=services,
    )
    compatibility_path = root / "client-compatibility-policy-v1.json"
    _write_immutable(compatibility_path, compatibility_policy)
    compatibility_ref = _owner_ref(
        compatibility_path,
        owner_id="supersymmetry-staged-client-compatibility",
        record_id=compatibility_policy["policy_id"],
        record_kind=CLIENT_COMPATIBILITY_FORMAT,
        outcome="passed",
        services=services,
    )
    overlay, probe = _probe_material(
        suite,
        plan,
        role=request["role"],
        side="client",
        root=root,
    )
    runtime = services.observe_project_runtime(
        suite,
        workspace,
        launcher=config.launcher,
        state_root=state,
        launcher_executable=launcher_executable,
        launcher_root=launcher_root,
        launcher_profile=config.client_launcher_profile,
        launcher_java=config.client_launcher_java,
        launcher_java_state=config.client_launcher_java_state,
        packwiz_executable=_discover_packwiz(workspace),
        seed_roots=config.client_seed_roots,
        memory_mib=config.memory_mib,
        compatibility_patches=(*authorized_patches, overlay),
        timeout_seconds=config.client_timeout_seconds,
        attach_timeout=config.client_attach_timeout_seconds,
        session_timeout=config.client_session_timeout_seconds,
    )
    runtime_summary, base_assertions = services.summarize_disposable_runtime(
        runtime,
        expected_evidence_root=state / "evidence/runtime",
        launcher_root=launcher_root,
        source_workspace=_local_uri(
            stage["source_workspace_uri"], "feature source workspace"
        ),
        staged_workspace=workspace,
        expected_runtime_plan=expected_plan,
        compatibility_policy=compatibility_policy,
        probe=probe,
    )
    if base_assertions.get("fml_client_load", {}).get("state") != "observed":
        _fail("client did not reach the exact FML loaded checkpoint")
    services.require_runtime_processes_closed(runtime)
    log_bytes, _capture = services.captured_disposable_groovy_log(
        state / "evidence/runtime",
        runtime,
        probe,
        compatibility_policy,
        expected_plan,
        workspace,
    )
    refs = [
        compatibility_ref,
        *_client_refs(
            services.disposable_runtime_receipt_evidence(runtime), services=services
        ),
    ]
    observation = interpret_material_fluid_runtime_marker(
        plan,
        role=request["role"],
        side="client",
        groovy_log_bytes=log_bytes,
        source_refs=refs,
        suite_root=suite,
        construction=construction,
    )
    result = _execution_result(
        request,
        plan,
        stage,
        observation=observation,
        owner_refs=refs,
        limitations=(
            "The installed launcher remains account and authentication authority.",
            "The source checkout is unchanged; only a disposable projected client was observed.",
            "Compatibility authority is bound to the exact staged Recurrent Complex and SusyCore pair.",
            "Runtime summary state: " + str(runtime_summary.get("state")),
        ),
    )
    _write_immutable(root / "staged-pack-client-execution-v1.json", result)
    return result


def _seed_receipt(
    config: InstalledSupersymmetryRuntimeConfig,
    *,
    services: RuntimeExecutionServices,
) -> tuple[Path, bytes, dict[str, Any], Path]:
    path = _regular_file(config.server_seed_receipt, "server seed receipt")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterialFluidRuntimePairError("server seed receipt is invalid") from exc
    version = services.server_materialization_version(value)
    target = value.get("target") if isinstance(value, dict) else None
    if (
        version != 2
        or not services.verify_server_materialization_identity(value)
        or value.get("state") != "materialized"
        or not isinstance(target, Mapping)
        or target.get("receipt_uri") != path.as_uri()
    ):
        _fail("server seed receipt identity is invalid")
    template = _local_uri(target.get("template_uri"), "server seed template")
    _regular_directory(template, "server seed template")
    summary, _records = services.runtime_tree(template)
    if summary != target.get("payload"):
        _fail("server seed template payload changed after its receipt")
    return path, raw, value, template


def _copy_regular(
    source: Path,
    target: Path,
    label: str,
    *,
    services: RuntimeExecutionServices,
) -> dict[str, Any]:
    source = _regular_file(source, label)
    if target.exists() or target.is_symlink():
        _fail(f"{label} destination unexpectedly exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_stream, target.open("xb") as output_stream:
        shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
        output_stream.flush()
        os.fsync(output_stream.fileno())
    target.chmod(stat.S_IMODE(source.stat().st_mode))
    digest, size = services.sha256_file(target)
    return {"path": target.name, "sha256": digest, "size": size}


def _copy_infrastructure(
    template: Path,
    runtime: Path,
    *,
    services: RuntimeExecutionServices,
) -> dict[str, Any]:
    runtime.mkdir(mode=0o700)
    libraries = template / "libraries"
    if libraries.is_symlink() or not libraries.is_dir():
        _fail("server seed lacks regular Cleanroom libraries")
    shutil.copytree(libraries, runtime / "libraries", copy_function=shutil.copy2)
    launchers = sorted(template.glob("cleanroom-*.jar"))
    minecraft = sorted(template.glob("minecraft_server*.jar"))
    if len(launchers) != 1 or len(minecraft) != 1:
        _fail("server seed lacks one exact Cleanroom and Minecraft launcher")
    launcher = _copy_regular(
        launchers[0],
        runtime / launchers[0].name,
        "Cleanroom server launcher",
        services=services,
    )
    minecraft_record = _copy_regular(
        minecraft[0],
        runtime / minecraft[0].name,
        "Minecraft server launcher",
        services=services,
    )
    return {
        "cleanroom_launcher": launcher,
        "minecraft_server": minecraft_record,
        "launcher_name": launchers[0].name,
    }


def _server_properties(template: Path, runtime: Path) -> dict[str, Any]:
    source = _regular_file(template / "server.properties", "server seed properties")
    try:
        raw = source.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise MaterialFluidRuntimePairError(
            "server seed properties are not bounded ASCII"
        ) from exc
    values: dict[str, str] = {}
    for line in raw.splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key in values:
            _fail("server seed properties are malformed or repeated")
        values[key] = value
    world_port = values.get("defaultworldgenerator-port")
    level_type = values.get("level-type")
    if (
        not isinstance(world_port, str)
        or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            world_port,
        )
        is None
        or level_type != "RTG"
    ):
        _fail("server seed lacks exact Supersymmetry world-generator properties")
    selected = {
        "defaultworldgenerator-port": world_port,
        "enable-query": "false",
        "enable-rcon": "false",
        "level-name": "workbench-feature-world",
        "level-type": level_type,
        "online-mode": "false",
        "server-ip": "127.0.0.1",
        "server-port": "0",
    }
    payload = "".join(f"{key}={selected[key]}\n" for key in sorted(selected)).encode(
        "ascii"
    )
    target = runtime / "server.properties"
    target.write_bytes(payload)
    return {
        "source_uri": source.as_uri(),
        "defaultworldgenerator_port": world_port,
        "level_type": level_type,
        "projection_sha256": sha256(payload).hexdigest(),
        "projection_size": len(payload),
    }


def _validate_tool_against_seed(
    selected: Path,
    expected: Mapping[str, Any],
    label: str,
    *,
    services: RuntimeExecutionServices,
) -> dict[str, Any]:
    selected = _regular_file(selected, label)
    digest, size = services.sha256_file(selected)
    if digest != expected.get("sha256") or (
        expected.get("size") is not None and size != expected.get("size")
    ):
        _fail(f"{label} differs from the verified server seed tool identity")
    return {"uri": selected.as_uri(), "sha256": digest, "size": size}


def _prepare_server_materialization(
    config: InstalledSupersymmetryRuntimeConfig,
    *,
    request: Mapping[str, Any],
    suite: Path,
    plan: Mapping[str, Any],
    stage: Mapping[str, Any],
    root: Path,
    services: RuntimeExecutionServices,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    seed_path, seed_raw, seed, template = _seed_receipt(config, services=services)
    tools = seed.get("tools")
    artifacts = tools.get("artifacts") if isinstance(tools, Mapping) else None
    java_seed = tools.get("java") if isinstance(tools, Mapping) else None
    installer_seed = (
        artifacts.get("packwiz_installer") if isinstance(artifacts, Mapping) else None
    )
    if not isinstance(java_seed, Mapping) or not isinstance(installer_seed, Mapping):
        _fail("server seed receipt lacks exact Java and Packwiz Installer tools")
    java = _validate_tool_against_seed(
        config.server_java, java_seed, "server Java executable", services=services
    )
    installer = _validate_tool_against_seed(
        config.packwiz_installer_jar,
        installer_seed,
        "Packwiz Installer",
        services=services,
    )
    stage_workspace = _local_uri(stage["workspace_uri"], "staged Packwiz workspace")
    state = root / "runtime-state"
    runtime_plan = services.plan_project_runtime(
        suite, stage_workspace, side="server", state_root=state
    )
    if (
        runtime_plan.get("state") != "ready"
        or runtime_plan.get("blockers") != []
        or runtime_plan.get("workspace", {}).get("revision") != stage["revision"]
    ):
        _fail("staged dedicated-server runtime plan is not exactly ready")
    source = root / "materialization-source"
    source_tree, excluded = services.copy_tracked_workspace(stage_workspace, source)
    packwiz = _discover_packwiz(source)
    refresh_log = root / "packwiz-refresh.log"
    services.run_owned_logged(
        [
            str(packwiz),
            "--cache",
            str(root / "packwiz-cache/downloads"),
            "--config",
            str(root / "packwiz-cache/config.toml"),
            "--yes",
            "refresh",
        ],
        cwd=source,
        log_path=refresh_log,
        timeout_seconds=300.0,
        label="staged Packwiz refresh",
        custody_root=root,
    )
    refreshed_identity, refreshed_tree = services.validate_refreshed_pack(
        runtime_plan, source
    )
    refreshed = {**refreshed_identity, "staged_tree": refreshed_tree}
    pack = services.load_pack(source)
    decisions = services.packwiz_optional_decisions(source, side="server")
    disabled = frozenset(
        str(row["metadata_path"])
        for row in decisions
        if row["declared_default"] is False
    )
    runtime = root / "server-runtime"
    infrastructure = _copy_infrastructure(template, runtime, services=services)
    seed_paths = services.server_seed_paths(
        pack,
        template,
        include_packwiz_state=False,
        disabled_optional_metadata=disabled,
    )
    seed_identity, seeded = services.copy_seed(template, runtime, seed_paths)
    initial_state = services.write_packwiz_initial_state(
        runtime, decisions, side="server"
    )
    install_log = root / "packwiz-installer.log"
    services.run_owned_logged(
        [
            str(config.server_java),
            "-cp",
            str(config.packwiz_installer_jar),
            services.INSTALLER_MAIN_CLASS,
            "--no-gui",
            "--side",
            "server",
            "--pack-folder",
            str(runtime),
            (source / "pack.toml").as_uri(),
        ],
        cwd=runtime,
        log_path=install_log,
        timeout_seconds=1800.0,
        label="staged Packwiz server install",
        custody_root=root,
    )
    final_state, option_rows = services.verify_packwiz_final_state(
        runtime, decisions, refreshed, side="server"
    )
    mod_inventory = services.expected_server_mods(
        pack, runtime, disabled_optional_metadata=disabled
    )
    for operation in plan["operations"]:
        expected = next(
            row for row in stage["outputs"] if row["path"] == operation["path"]
        )
        target = runtime / operation["path"]
        digest, size = services.sha256_file(
            _regular_file(target, "materialized feature target")
        )
        if digest != expected["sha256"] or size != expected["size"]:
            _fail("materialized server differs from the exact staged feature role")
    eula_payload = b"eula=true\n"
    (runtime / "eula.txt").write_bytes(eula_payload)
    server_properties = _server_properties(template, runtime)
    for generated in ("world", "workbench-feature-world", "logs", "crash-reports"):
        path = runtime / generated
        if path.exists() or path.is_symlink():
            _fail("fresh materialized server contains generated runtime state")
    body = {
        "format": SERVER_MATERIALIZATION_FORMAT,
        "schema_version": 1,
        "plan_id": plan["id"],
        "stage_id": stage["stage_id"],
        "role": request["role"],
        "state": "materialized",
        "source": {
            "workspace_uri": stage_workspace.as_uri(),
            "revision": stage["revision"],
            "copy": source_tree,
            "untracked_excluded": excluded,
            "refreshed_pack": refreshed,
        },
        "seed": {
            "policy": "cleanroom-bootstrap-and-artifact-seed-only",
            "receipt_uri": seed_path.as_uri(),
            "receipt_sha256": sha256(seed_raw).hexdigest(),
            "receipt_size": len(seed_raw),
            "materialization_id": seed["materialization_id"],
            "template_uri": template.as_uri(),
            "template_payload": seed["target"]["payload"],
            "seed_tree": seed_identity,
            "seeded_files": seeded,
        },
        "tools": {
            "java": java,
            "packwiz": {
                "uri": packwiz.as_uri(),
                "sha256": services.sha256_file(packwiz)[0],
                "size": services.sha256_file(packwiz)[1],
            },
            "installer": installer,
        },
        "packwiz_options": {
            "side": "server",
            "decisions": decisions,
            "initial_state": initial_state,
            "final_state": final_state,
            "files": option_rows,
        },
        "infrastructure": infrastructure,
        "eula": {
            "operation": "fresh-target-only",
            "sha256": sha256(eula_payload).hexdigest(),
            "size": len(eula_payload),
            "value": True,
        },
        "server_properties": server_properties,
        "mod_inventory": mod_inventory,
        "target": {"runtime_uri": runtime.as_uri()},
        "limitations": [
            "The verified prior server is an artifact seed only; this receipt independently owns the staged Packwiz source and install.",
            "This local materialization grants no stable profile or release authority.",
        ],
    }
    receipt = {
        **body,
        "materialization_id": _content_id(SERVER_MATERIALIZATION_ID_PREFIX, body),
    }
    raw, identity = _write_immutable(
        root / "staged-pack-server-materialization-v1.json", receipt
    )
    del raw
    ref = {
        "owner_id": "supersymmetry-staged-pack-server-materialization",
        "record_id": receipt["materialization_id"],
        "record_kind": SERVER_MATERIALIZATION_FORMAT,
        **identity,
        "outcome": "passed",
    }
    return runtime, receipt, ref


def _bounded_text(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        return ""
    if path.stat().st_size > _MAX_LOG_BYTES:
        _fail("dedicated-server log exceeds its byte bound")
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise MaterialFluidRuntimePairError("cannot read dedicated-server log") from exc


def _launch_server(
    config: InstalledSupersymmetryRuntimeConfig,
    *,
    request: Mapping[str, Any],
    plan: Mapping[str, Any],
    stage: Mapping[str, Any],
    root: Path,
    runtime: Path,
    materialization: Mapping[str, Any],
    materialization_ref: Mapping[str, Any],
    probe: Mapping[str, Any],
    services: RuntimeExecutionServices,
) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    if (
        not math.isfinite(config.server_timeout_seconds)
        or not math.isfinite(config.server_shutdown_timeout_seconds)
        or config.server_timeout_seconds <= 0
        or config.server_shutdown_timeout_seconds <= 0
    ):
        _fail("dedicated-server timeouts must be positive")
    launcher = materialization["infrastructure"]["launcher_name"]
    home = root / "server-home"
    temporary = root / "server-tmp"
    home.mkdir()
    temporary.mkdir()
    command = [
        str(_regular_file(config.server_java, "server Java executable")),
        "-Xms1024M",
        f"-Xmx{config.memory_mib}M",
        f"-Duser.home={home}",
        f"-Djava.io.tmpdir={temporary}",
        "-jar",
        launcher,
        "nogui",
    ]
    environment = dict(os.environ)
    for key in ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS"):
        environment.pop(key, None)
    environment.update(
        {
            "HOME": str(home),
            "TMPDIR": str(temporary),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
        }
    )
    stdout_path = root / "server.stdout.log"
    process: subprocess.Popen[bytes] | None = None
    ready = False
    marker_seen = False
    stop_sent = False
    returncode: int | None = None
    failure: str | None = None
    cleanup = {"pgid": None, "forced": False, "running": False}
    started_at = _utcnow()
    try:
        with stdout_path.open("xb") as output:
            process = subprocess.Popen(
                command,
                cwd=runtime,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            deadline = time.monotonic() + config.server_timeout_seconds
            while time.monotonic() < deadline:
                latest = _bounded_text(runtime / "logs/latest.log")
                groovy = _bounded_text(runtime / "logs/groovy_server.log")
                if not groovy:
                    groovy = _bounded_text(runtime / "logs/groovy.log")
                terminal = TERMINAL_RE.search(latest)
                if terminal is not None:
                    failure = "fatal-startup-log: " + terminal.group(0)
                    break
                ready = bool(
                    SERVER_READY_RE.search(latest)
                    and FML_LOADED_TEXT in latest
                    and SUSY_PACK_READY_RE.search(latest)
                )
                marker_seen = MARKER_PREFIX in groovy
                if ready and marker_seen:
                    assert process.stdin is not None
                    process.stdin.write(b"stop\n")
                    process.stdin.flush()
                    process.stdin.close()
                    stop_sent = True
                    try:
                        returncode = process.wait(
                            timeout=config.server_shutdown_timeout_seconds
                        )
                    except subprocess.TimeoutExpired:
                        failure = "dedicated-server clean shutdown timed out"
                    break
                if process.poll() is not None:
                    returncode = process.returncode
                    failure = "dedicated-server exited before exact readiness"
                    break
                time.sleep(0.25)
            else:
                failure = "dedicated-server readiness or role marker timed out"
            output.flush()
            os.fsync(output.fileno())
    finally:
        if process is not None:
            if process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            cleanup = services.stop_process_group(process)
            returncode = process.returncode
    latest = _bounded_text(runtime / "logs/latest.log")
    groovy_path = runtime / "logs/groovy_server.log"
    if not groovy_path.is_file():
        groovy_path = runtime / "logs/groovy.log"
    groovy_bytes = _regular_file(
        groovy_path, "dedicated-server Groovy observation log"
    ).read_bytes()
    shutdown = _shutdown_acknowledgment(latest)
    terminal_after = TERMINAL_RE.search(latest)
    groovy_failed = GROOVY_SCRIPT_FAILURE_TEXT in groovy_bytes.decode(
        "utf-8", errors="replace"
    )
    passed = bool(
        failure is None
        and ready
        and marker_seen
        and stop_sent
        and returncode == 0
        and shutdown["complete"]
        and terminal_after is None
        and not groovy_failed
        and cleanup.get("running") is False
    )
    if not passed:
        failure = failure or "dedicated-server readiness or clean-stop contract failed"
    finished_at = _utcnow()
    body = {
        "format": SERVER_LAUNCH_FORMAT,
        "schema_version": 1,
        "plan_id": plan["id"],
        "stage_id": stage["stage_id"],
        "materialization_id": materialization["materialization_id"],
        "role": request["role"],
        "started_at": started_at,
        "finished_at": finished_at,
        "state": "complete" if passed else "failed",
        "outcome": "passed" if passed else "failed",
        "command": command,
        "readiness": {
            "fml_loaded": FML_LOADED_TEXT in latest,
            "cleanroom_ready": SERVER_READY_RE.search(latest) is not None,
            "pack_ready": SUSY_PACK_READY_RE.search(latest) is not None,
            "role_marker": marker_seen,
        },
        "shutdown": {
            "stop_sent": stop_sent,
            "returncode": returncode,
            "acknowledgment": shutdown,
        },
        "cleanup": cleanup,
        "failure": failure,
        "evidence": {
            "stdout_uri": stdout_path.as_uri(),
            "latest_log_uri": (runtime / "logs/latest.log").as_uri(),
            "groovy_log_uri": groovy_path.as_uri(),
            "groovy_log_sha256": sha256(groovy_bytes).hexdigest(),
            "groovy_log_size": len(groovy_bytes),
            "probe": deepcopy(dict(probe)),
        },
        "limitations": [
            "The launch owns only this fresh staged server runtime and sends stop only after all exact readiness markers.",
            "No SUSY mod candidate or frozen Stage-5 authority is reused.",
        ],
    }
    receipt = {
        **body,
        "launch_id": _content_id(SERVER_LAUNCH_ID_PREFIX, body),
    }
    raw, identity = _write_immutable(
        root / "staged-pack-server-launch-v1.json", receipt
    )
    del raw
    ref = {
        "owner_id": "supersymmetry-staged-pack-server-launch",
        "record_id": receipt["launch_id"],
        "record_kind": SERVER_LAUNCH_FORMAT,
        **identity,
        "outcome": receipt["outcome"],
    }
    if not passed:
        _fail("dedicated-server physical execution failed: " + str(failure))
    return groovy_bytes, receipt, ref


def execute_installed_server(
    config: InstalledSupersymmetryRuntimeConfig,
    *,
    request: Mapping[str, Any],
    suite_root: Path,
    plan: Mapping[str, Any],
    stage: Mapping[str, Any],
    execution_root: Path,
    services: RuntimeExecutionServices,
    construction: ReviewedPlanPorts,
) -> Mapping[str, Any]:
    """Materialize and launch one exact staged Packwiz dedicated-server role."""

    suite = Path(suite_root).resolve()
    root = _regular_directory(execution_root, "server execution root")
    runtime, materialization, materialization_ref = _prepare_server_materialization(
        config,
        request=request,
        suite=suite,
        plan=plan,
        stage=stage,
        root=root,
        services=services,
    )
    _overlay_path, probe = _probe_material(
        suite,
        plan,
        role=request["role"],
        side="server",
        root=root,
    )
    probe_source = _local_uri(probe["script_uri"], "server observation probe")
    target = runtime / _PROBE_TARGET.removeprefix(".minecraft/")
    if target.exists() or target.is_symlink():
        _fail("server observation target already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    _copy_regular(probe_source, target, "server observation probe", services=services)
    groovy, _launch, launch_ref = _launch_server(
        config,
        request=request,
        plan=plan,
        stage=stage,
        root=root,
        runtime=runtime,
        materialization=materialization,
        materialization_ref=materialization_ref,
        probe=probe,
        services=services,
    )
    refs = [materialization_ref, launch_ref]
    observation = interpret_material_fluid_runtime_marker(
        plan,
        role=request["role"],
        side="server",
        groovy_log_bytes=groovy,
        source_refs=refs,
        dedicated_server_ready=True,
        suite_root=suite,
        construction=construction,
    )
    result = _execution_result(
        request,
        plan,
        stage,
        observation=observation,
        owner_refs=refs,
        limitations=(
            "The verified retained server contributes only Cleanroom/bootstrap and hash-matched artifact seed bytes.",
            "The staged checkout independently owns Packwiz refresh, server install, launch, marker, and cleanup.",
        ),
    )
    _write_immutable(root / "staged-pack-server-execution-v1.json", result)
    return result


__all__ = [
    "CLIENT_COMPATIBILITY_FORMAT",
    "SERVER_LAUNCH_FORMAT",
    "SERVER_MATERIALIZATION_FORMAT",
    "execute_installed_client",
    "execute_installed_server",
]
