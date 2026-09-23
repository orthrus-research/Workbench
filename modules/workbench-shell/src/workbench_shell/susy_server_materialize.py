"""Materialize an exact Supersymmetry Packwiz payload as a Cleanroom server.

The upstream Supersymmetry builder produces a Forge server and relies on
out-of-band files for a few API-excluded CurseForge artifacts.  Workbench's
developer flow already retains those exact files in a validated canonical
client materialization.  This module uses that payload only as a content seed,
runs Packwiz again with ``side=server``, and combines the resulting pack payload
with the exact Cleanroom server installer selected by the platform profile.

The source checkout, canonical client, and retained developer run are never
modified.  A complete template and its receipt are published together under
ignored Workbench state.
"""

from __future__ import annotations

from urllib.request import url2pathname

from workbench_project_intelligence.working_tree import WorkingTreeError, copy_tracked_workspace

from copy import deepcopy
import ctypes
import errno
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from typing import Any, Mapping
from urllib.parse import urlparse

from workbench_core.artifact_store import ArtifactStoreError, fetch_verified_artifact, sha256_file
from workbench_core.configuration import (
    CONFIGURATION_PATH,
    WorkbenchConfiguration,
    WorkbenchConfigurationError,
    load_workbench_configuration,
)
from workbench_core.runtime_java import JavaRuntimeError, ensure_java_runtime, host_platform
from .runtime_launch import RuntimeLaunchError, _selected_java
from .runtime_materialize import (
    PACKWIZ_V2_POLICY,
    PACKWIZ_V2_POLICY_VERSION,
    PackwizMaterializationError,
    _tool_identity,
    _tree_identity,
    _validate_refreshed_pack,
    _packwiz_decisions_sha256,
    _packwiz_initial_state_bytes,
    _packwiz_optional_decisions,
    _verify_packwiz_final_state,
    _write_packwiz_initial_state,
    packwiz_materialization_version,
    verify_packwiz_materialization_receipt_identity,
)
from .runtime_plan import RuntimePlanError, plan_project_runtime
from workbench_api.state_paths import default_suite_state_root
from .susy_mod_dev import SusyModDevError, _load_pack
from .susy_mod_launch import (
    SusyModLaunchError,
    _file_digest,
    _read_json,
    _validate_retained_stage,
)


MATERIALIZATION_RESULT_FORMAT_V2 = "workbench-susy-server-materialization-result-v2"
MATERIALIZATION_RECEIPT_FORMAT_V2 = "workbench-susy-server-materialization-receipt-v2"
MATERIALIZATION_ID_PREFIX = "workbench-susy-server-materialization:"
RECEIPT_RELATIVE_V2 = Path("receipts/susy-server-materialization-v2.json")
SOURCE_VARIANT_FORMAT = "workbench-susy-server-source-variant-v1"
INSTALLER_MAIN_CLASS = "link.infra.packwiz.installer.Main"
MAX_RECORD_BYTES = 8 * 1024 * 1024
MAX_LOG_BYTES = 64 * 1024 * 1024
SERVER_PROPERTIES_TEXT = (
    "# Workbench disposable SUSY server template\n"
    "defaultworldgenerator-port=a55790f1-609f-11ee-b9f3-80e82ceaaf53\n"
    "level-type=RTG\n"
    "online-mode=false\n"
    "server-ip=127.0.0.1\n"
)
MATERIALIZATION_CLAIMS = {
    "source_checkout_mutated": False,
    "canonical_client_mutated": False,
    "retained_run_mutated": False,
    "server_side_packwiz_reconciled": True,
    "cleanroom_server_installed": True,
    "minecraft_launched": False,
}
MATERIALIZATION_LIMITATIONS = [
    "This is a disposable developer template, not a supported SUSY server distribution.",
    "The canonical client is a byte source for API-excluded pack files; Packwiz server-side reconciliation remains authoritative for inclusion.",
    "Materialization does not establish mod compatibility or client/server parity; launch-server and dev check exercise those claims.",
]


class SusyServerMaterializationError(RuntimeError):
    """An exact SUSY Cleanroom server template cannot be materialized safely."""


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _receipt_id(receipt: Mapping[str, Any]) -> str:
    payload = {key: deepcopy(value) for key, value in receipt.items() if key != "materialization_id"}
    return MATERIALIZATION_ID_PREFIX + sha256(_canonical(payload)).hexdigest()


def susy_server_materialization_version(
    receipt: Mapping[str, Any],
) -> int | None:
    pair = (receipt.get("format"), receipt.get("schema_version"))
    return 2 if pair == (MATERIALIZATION_RECEIPT_FORMAT_V2, 2) else None


def verify_susy_server_materialization_receipt_identity(
    receipt: Mapping[str, Any],
) -> bool:
    return (
        susy_server_materialization_version(receipt) is not None
        and receipt.get("materialization_id") == _receipt_id(receipt)
    )


def _canonical_client_provenance(
    receipt: Mapping[str, Any],
    receipt_path: Path,
) -> dict[str, Any]:
    version = packwiz_materialization_version(receipt)
    if version != 2:
        raise SusyServerMaterializationError(
            "canonical client materialization version is unsupported"
        )
    digest, size = sha256_file(receipt_path)
    provenance: dict[str, Any] = {
        "format": receipt["format"],
        "schema_version": version,
        "materialization_id": receipt.get("materialization_id"),
        "receipt_uri": receipt_path.as_uri(),
        "receipt_sha256": digest,
        "receipt_size": size,
    }
    target = receipt.get("target")
    variant_id = target.get("variant_id") if isinstance(target, dict) else None
    if not isinstance(variant_id, str) or not variant_id.startswith("sha256:"):
        raise SusyServerMaterializationError(
            "canonical Packwiz V2 materialization lacks its target variant"
        )
    provenance["variant_id"] = variant_id
    return provenance


def _server_source_variant(
    plan: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    projection = {
        "format": SOURCE_VARIANT_FORMAT,
        "server_plan_id": plan.get("plan_id"),
        "planned_fixture_root_uri": (
            plan.get("target", {}).get("fixture_root_uri")
            if isinstance(plan.get("target"), Mapping)
            else None
        ),
        "canonical_client": dict(provenance),
        "server_options": {
            "policy": PACKWIZ_V2_POLICY,
            "policy_version": PACKWIZ_V2_POLICY_VERSION,
            "side": "server",
        },
    }
    return {
        **projection,
        "variant_id": "sha256:" + sha256(_canonical(projection)).hexdigest(),
    }


def _server_packwiz_options(
    *,
    published_runtime: Path,
    decisions: list[dict[str, Any]],
    initial: Mapping[str, Any],
    final: Mapping[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "policy": PACKWIZ_V2_POLICY,
        "policy_version": PACKWIZ_V2_POLICY_VERSION,
        "side": "server",
        "decisions_sha256": _packwiz_decisions_sha256(decisions),
        "optional_count": len(decisions),
        "enabled_count": sum(
            int(row["declared_default"] is True) for row in decisions
        ),
        "disabled_count": sum(
            int(row["declared_default"] is False) for row in decisions
        ),
        "installer_state": {
            "relative_path": "packwiz.json",
            "uri": (published_runtime / "packwiz.json").as_uri(),
            "cached_side": "server",
            "initial": dict(initial),
            "final": dict(final),
        },
        "files": [dict(row) for row in rows],
    }


def _validate_server_packwiz_options(
    value: Any,
    *,
    runtime: Path,
    refreshed_pack: Mapping[str, Any],
) -> frozenset[str]:
    if not isinstance(value, dict) or not isinstance(value.get("files"), list):
        raise SusyServerMaterializationError(
            "server Packwiz option authority is missing"
        )
    rows = value["files"]
    if not all(isinstance(row, dict) for row in rows):
        raise SusyServerMaterializationError(
            "server Packwiz option rows are invalid"
        )
    recorded_decisions = [
        {
            field: row.get(field)
            for field in (
                "metadata_path",
                "metafile_sha256",
                "output_path",
                "name",
                "side",
                "declared_default",
                "applied",
            )
        }
        for row in rows
    ]
    if any(
        row.get("side") not in {"both", "server"}
        or type(row.get("declared_default")) is not bool
        or row.get("applied") is not row.get("declared_default")
        for row in rows
    ):
        raise SusyServerMaterializationError(
            "server Packwiz option rows do not preserve declared defaults"
        )
    source_root = runtime.parent / "source"
    try:
        observed_source_tree, _ = _tree_identity(source_root)
        authoritative_decisions = _packwiz_optional_decisions(
            source_root,
            side="server",
        )
    except (PackwizMaterializationError, OSError) as exc:
        raise SusyServerMaterializationError(
            "server Packwiz option source authority is unavailable"
        ) from exc
    recorded_source_tree = refreshed_pack.get("staged_tree")
    if (
        observed_source_tree != recorded_source_tree
        or recorded_decisions != authoritative_decisions
    ):
        raise SusyServerMaterializationError(
            "server Packwiz option decisions differ from refreshed source authority"
        )
    initial_bytes = _packwiz_initial_state_bytes(
        authoritative_decisions,
        side="server",
    )
    initial = {
        "sha256": sha256(initial_bytes).hexdigest(),
        "size": len(initial_bytes),
    }
    try:
        final, observed_rows = _verify_packwiz_final_state(
            runtime,
            authoritative_decisions,
            refreshed_pack,
            side="server",
        )
    except PackwizMaterializationError as exc:
        raise SusyServerMaterializationError(str(exc)) from exc
    expected = _server_packwiz_options(
        published_runtime=runtime,
        decisions=authoritative_decisions,
        initial=initial,
        final=final,
        rows=observed_rows,
    )
    if value != expected:
        raise SusyServerMaterializationError(
            "server Packwiz option authority has drifted"
        )
    return frozenset(
        str(row["metadata_path"])
        for row in rows
        if row.get("declared_default") is False
    )


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    raw = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise SusyServerMaterializationError(
            f"cannot retain SUSY server materialization record: {path}"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _local_uri(value: Any, label: str, *, directory: bool = False) -> Path:
    if not isinstance(value, str):
        raise SusyServerMaterializationError(f"{label} URI is missing")
    parsed = urlparse(value)
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or parsed.query
        or parsed.fragment
    ):
        raise SusyServerMaterializationError(f"{label} must use a local file URI")
    lexical = Path(url2pathname(parsed.path))
    try:
        info = lexical.lstat()
    except OSError as exc:
        raise SusyServerMaterializationError(f"{label} is missing: {lexical}") from exc
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if stat.S_ISLNK(info.st_mode) or not expected:
        kind = "directory" if directory else "regular file"
        raise SusyServerMaterializationError(f"{label} must be a {kind}: {lexical}")
    return lexical.resolve()


def _regular_json(path: Path, label: str) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise SusyServerMaterializationError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SusyServerMaterializationError(f"{label} must be a regular file: {path}")
    if info.st_size > MAX_RECORD_BYTES:
        raise SusyServerMaterializationError(f"{label} exceeds the record limit: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SusyServerMaterializationError(f"{label} is invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise SusyServerMaterializationError(f"{label} must be a JSON object: {path}")
    return value


def _ensure_state_directory(suite: Path, relative: Path) -> Path:
    """Create one Workbench state directory without following parent links."""

    current = suite
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            try:
                current.mkdir()
                info = current.lstat()
            except (FileExistsError, OSError) as exc:
                raise SusyServerMaterializationError(
                    f"cannot create managed server state directory: {current}"
                ) from exc
        except OSError as exc:
            raise SusyServerMaterializationError(
                f"cannot inspect managed server state directory: {current}"
            ) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise SusyServerMaterializationError(
                f"managed server state path must contain only directories: {current}"
            )
    if current.resolve() != current.absolute():
        raise SusyServerMaterializationError(
            "managed server state directory escapes through a filesystem link"
        )
    return current


def _checkout_identity(root: Path) -> dict[str, Any]:
    """Hash every checkout entry except Git's own mutable bookkeeping."""

    entries: list[dict[str, Any]] = []
    try:
        paths = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
    except OSError as exc:
        raise SusyServerMaterializationError(
            f"cannot inventory the Supersymmetry checkout: {root}"
        ) from exc
    for path in paths:
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] == ".git":
            continue
        try:
            info = path.lstat()
        except OSError as exc:
            raise SusyServerMaterializationError(
                f"cannot inventory Supersymmetry checkout entry: {relative}"
            ) from exc
        if stat.S_ISDIR(info.st_mode):
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise SusyServerMaterializationError(
                f"Supersymmetry checkout contains an unsafe entry: {relative}"
            )
        digest, size = sha256_file(path)
        entries.append({
            "mode": stat.S_IMODE(info.st_mode),
            "path": relative.as_posix(),
            "sha256": digest,
            "size": size,
        })
    return {
        "selection": "all-regular-files-excluding-dot-git",
        "tree_sha256": "sha256:" + sha256(_canonical(entries)).hexdigest(),
        "file_count": len(entries),
        "total_bytes": sum(int(entry["size"]) for entry in entries),
    }


def _process_group_alive(process: subprocess.Popen[bytes]) -> bool:
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_process_group(process: subprocess.Popen[bytes]) -> bool:
    """Stop the exact POSIX group and report whether forced cleanup was used."""

    forced = False
    if _process_group_alive(process):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 3.0
        while _process_group_alive(process) and time.monotonic() < deadline:
            time.sleep(0.02)
    if _process_group_alive(process):
        forced = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 3.0
        while _process_group_alive(process) and time.monotonic() < deadline:
            time.sleep(0.02)
    if process.poll() is None:
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
    if _process_group_alive(process):
        raise SusyServerMaterializationError(
            "materialization tool left an owned process group running"
        )
    return forced


def _linux_process_identity(pid: int) -> str | None:
    try:
        raw = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    closing = raw.rfind(")")
    if closing < 0:
        return None
    fields = raw[closing + 2 :].split()
    return fields[19] if len(fields) > 19 else None


def _scoped_linux_processes(root: Path) -> dict[int, str]:
    """Find live processes still bound to one private staging tree."""

    proc = Path("/proc")
    if not proc.is_dir():  # pragma: no cover - managed host boundary
        raise SusyServerMaterializationError(
            "automatic SUSY server materialization requires Linux process custody"
        )
    root_bytes = os.fsencode(str(root))
    rows: dict[int, str] = {}
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        pid = int(entry.name)
        if pid == os.getpid():
            continue
        scoped = False
        try:
            cwd = Path(os.readlink(entry / "cwd"))
            scoped = cwd == root or cwd.is_relative_to(root)
        except (OSError, ValueError):
            pass
        if not scoped:
            try:
                command = (entry / "cmdline").read_bytes()
                scoped = root_bytes in command
            except OSError:
                pass
        if scoped:
            identity = _linux_process_identity(pid)
            if identity is not None:
                rows[pid] = identity
    return rows


def _stop_scoped_linux_processes(rows: Mapping[int, str]) -> None:
    def alive(pid: int, identity: str) -> bool:
        return _linux_process_identity(pid) == identity

    for selected_signal in (signal.SIGTERM, signal.SIGKILL):
        for pid, identity in rows.items():
            if alive(pid, identity):
                try:
                    os.kill(pid, selected_signal)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and any(
            alive(pid, identity) for pid, identity in rows.items()
        ):
            time.sleep(0.02)
        if not any(alive(pid, identity) for pid, identity in rows.items()):
            return
    if any(alive(pid, identity) for pid, identity in rows.items()):
        raise SusyServerMaterializationError(
            "materialization tool left a detached staging process running"
        )


def _run_owned_logged(
    command: list[str],
    *,
    cwd: Path,
    log_path: Path,
    timeout_seconds: float,
    label: str,
    custody_root: Path,
) -> None:
    """Run one materialization tool and require its whole process group to exit."""

    if os.name != "posix" or not Path("/proc").is_dir():  # pragma: no cover
        raise SusyServerMaterializationError(
            "automatic SUSY server materialization currently requires Linux process custody"
        )
    existing_scoped = _scoped_linux_processes(custody_root)
    if existing_scoped:
        raise SusyServerMaterializationError(
            "materialization staging already has live process custody"
        )
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise SusyServerMaterializationError(f"{label} timeout must be positive")
    if log_path.exists() or log_path.is_symlink():
        raise SusyServerMaterializationError(f"{label} log target already exists")
    environment = dict(os.environ)
    for key in ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS"):
        environment.pop(key, None)
    process: subprocess.Popen[bytes] | None = None
    timed_out = False
    try:
        with log_path.open("xb") as log:
            log.write(
                json.dumps(
                    {"command": command, "cwd": str(cwd)},
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
                + b"\n"
            )
            log.flush()
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                returncode = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                returncode = None
            log.flush()
            os.fsync(log.fileno())
    except OSError as exc:
        if process is not None:
            _stop_process_group(process)
        raise SusyServerMaterializationError(
            f"cannot execute {label}; see {log_path.as_uri()}"
        ) from exc
    if process is None:
        raise SusyServerMaterializationError(f"cannot execute {label}")
    descendants_remained = _process_group_alive(process)
    forced = _stop_process_group(process) if descendants_remained or timed_out else False
    detached = _scoped_linux_processes(custody_root)
    if detached:
        _stop_scoped_linux_processes(detached)
    try:
        log_size = log_path.stat().st_size
    except OSError as exc:
        raise SusyServerMaterializationError(f"cannot inspect {label} log") from exc
    if log_size > MAX_LOG_BYTES:
        raise SusyServerMaterializationError(
            f"{label} log exceeds the retained size limit: {log_path.as_uri()}"
        )
    if timed_out:
        raise SusyServerMaterializationError(
            f"{label} timed out; see {log_path.as_uri()}"
        )
    if descendants_remained:
        qualifier = " and required forced cleanup" if forced else ""
        raise SusyServerMaterializationError(
            f"{label} left descendant processes running{qualifier}; see {log_path.as_uri()}"
        )
    if detached:
        raise SusyServerMaterializationError(
            f"{label} left detached staging processes running; see {log_path.as_uri()}"
        )
    if returncode:
        raise SusyServerMaterializationError(
            f"{label} exited with {returncode}; see {log_path.as_uri()}"
        )


def _rename_no_replace(source: Path, destination: Path) -> None:
    """Atomically publish one directory without replacing an existing target."""

    if os.name == "nt":  # pragma: no cover - host dependent
        try:
            source.rename(destination)
            return
        except FileExistsError as exc:
            raise SusyServerMaterializationError(
                "server materialization target appeared before atomic publish"
            ) from exc
        except OSError as exc:
            raise SusyServerMaterializationError(
                "cannot atomically publish the server materialization"
            ) from exc
    if os.name != "posix":  # pragma: no cover - host dependent
        raise SusyServerMaterializationError(
            "atomic no-replace server publication is unavailable on this host"
        )
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:  # pragma: no cover - platform dependent
        raise SusyServerMaterializationError(
            "atomic no-replace server publication requires renameat2"
        )
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100,
        os.fsencode(source),
        -100,
        os.fsencode(destination),
        1,
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise SusyServerMaterializationError(
            "server materialization target appeared before atomic publish"
        )
    if error in {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP}:
        mover = shutil.which("mv")
        if mover is None:  # pragma: no cover - minimal host image
            raise SusyServerMaterializationError(
                "atomic no-replace server publication is unavailable on this filesystem"
            )
        try:
            completed = subprocess.run(
                [mover, "-T", "--no-clobber", "--", str(source), str(destination)],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=30.0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SusyServerMaterializationError(
                "cannot execute no-replace server publication"
            ) from exc
        if source.exists() or source.is_symlink():
            raise SusyServerMaterializationError(
                "server materialization target appeared before atomic publish"
            )
        if completed.returncode or not destination.is_dir() or destination.is_symlink():
            raise SusyServerMaterializationError(
                "cannot atomically publish the server materialization"
            )
        return
    raise SusyServerMaterializationError(
        f"cannot atomically publish the server materialization: {os.strerror(error)}"
    )


def _profile_locks(
    profile_path: Path,
    profile_bytes: bytes,
    profile: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], str]:
    artifacts = profile.get("runtime_artifacts")
    if not isinstance(artifacts, Mapping):
        raise SusyServerMaterializationError(
            "selected platform profile lacks runtime artifacts"
        )
    locks: dict[str, dict[str, Any]] = {}
    for artifact_id in ("packwiz_installer", "cleanroom_server"):
        value = artifacts.get(artifact_id)
        if not isinstance(value, Mapping):
            raise SusyServerMaterializationError(
                f"selected platform profile lacks {artifact_id}"
            )
        lock = {
            "id": artifact_id,
            "url": value.get("url"),
            "source_revision": value.get("source_revision"),
            "sha256": value.get("sha256"),
            "size": value.get("size"),
        }
        if (
            not isinstance(lock["url"], str)
            or not lock["url"].startswith("https://")
            or not isinstance(lock["source_revision"], str)
            or len(lock["source_revision"]) < 40
            or not isinstance(lock["sha256"], str)
            or len(lock["sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in lock["sha256"])
            or isinstance(lock["size"], bool)
            or not isinstance(lock["size"], int)
            or lock["size"] <= 0
        ):
            raise SusyServerMaterializationError(
                f"Cleanroom profile has an incomplete {artifact_id} lock"
            )
        locks[artifact_id] = lock

    planned = {
        str(row.get("id")): row
        for row in plan.get("artifacts", [])
        if isinstance(row, dict)
    }
    for artifact_id, lock in locks.items():
        row = planned.get(artifact_id)
        if (
            not isinstance(row, dict)
            or row.get("state") != "resolved"
            or row.get("url") != lock["url"]
            or row.get("sha256") != lock["sha256"]
        ):
            raise SusyServerMaterializationError(
                f"runtime plan and Cleanroom profile disagree on {artifact_id}"
            )
    return locks, sha256(profile_bytes).hexdigest()


def _server_seed_paths(
    pack: Mapping[str, Any],
    source: Path,
    *,
    include_packwiz_state: bool = True,
    disabled_optional_metadata: frozenset[str] = frozenset(),
) -> list[Path]:
    entries = pack.get("entries")
    if not isinstance(entries, list):
        raise SusyServerMaterializationError("Supersymmetry server inventory is unavailable")
    paths = [Path("packwiz.json")] if include_packwiz_state else []
    seen = {"packwiz.json"} if include_packwiz_state else set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("side") not in {"both", "server"}:
            continue
        if entry.get("metadata_path") in disabled_optional_metadata:
            continue
        filename = entry.get("filename")
        baseline = entry.get("baseline")
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not isinstance(baseline, dict)
            or baseline.get("hash_format") not in {"sha1", "sha256"}
            or not isinstance(baseline.get("hash"), str)
        ):
            raise SusyServerMaterializationError("Supersymmetry server seed identity is invalid")
        relative = Path("mods") / filename
        key = relative.as_posix().casefold()
        if key in seen:
            raise SusyServerMaterializationError(
                f"Supersymmetry server seed paths collide: {relative.as_posix()}"
            )
        seen.add(key)
        path = source / relative
        if not path.exists() and not path.is_symlink():
            # Server-only files are legitimately absent from a canonical
            # client. Packwiz remains responsible for acquiring them.
            continue
        if path.is_symlink() or not path.is_file():
            raise SusyServerMaterializationError(
                f"canonical client server seed is unsafe: {relative.as_posix()}"
            )
        observed, _ = _file_digest(path, str(baseline["hash_format"]))
        if observed != baseline["hash"]:
            raise SusyServerMaterializationError(
                f"canonical client server seed differs from Packwiz: {filename}"
            )
        paths.append(relative)
    return paths


def _copy_seed(
    source: Path,
    destination: Path,
    relative_paths: list[Path],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    before, _records = _tree_identity(source)
    copied: list[dict[str, Any]] = []
    for relative in sorted(relative_paths, key=lambda item: item.as_posix()):
        path = source / relative
        try:
            info = path.lstat()
        except OSError as exc:
            raise SusyServerMaterializationError(
                f"canonical client seed file is missing: {relative.as_posix()}"
            ) from exc
        target = destination / relative
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise SusyServerMaterializationError(
                f"canonical client seed is not a regular file: {relative.as_posix()}"
            )
        if target.exists() or target.is_symlink():
            raise SusyServerMaterializationError(
                f"canonical client file collides with Cleanroom output: {relative.as_posix()}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("rb") as input_stream, target.open("xb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
            target.chmod(stat.S_IMODE(info.st_mode))
        except OSError as exc:
            raise SusyServerMaterializationError(
                f"cannot seed canonical client file: {relative.as_posix()}"
            ) from exc
        digest, size = sha256_file(target)
        copied.append(
            {
                "path": relative.as_posix(),
                "sha256": digest,
                "size": size,
            }
        )
    after, _ = _tree_identity(source)
    if after != before:
        raise SusyServerMaterializationError(
            "canonical client changed while seeding the server payload"
        )
    return before, copied


def _artifact_record(path: Path, lock: Mapping[str, Any], outcome: str) -> dict[str, Any]:
    digest, size = sha256_file(path)
    if digest != lock.get("sha256") or size != lock.get("size"):
        raise SusyServerMaterializationError(
            f"cached {lock.get('id')} differs from the profile lock"
        )
    return {
        **dict(lock),
        "cache_uri": path.as_uri(),
        "cache_outcome": outcome,
    }


def _runner_identity() -> dict[str, Any]:
    path = Path(__file__).resolve()
    digest, size = sha256_file(path)
    return {"sha256": digest, "size": size}


def _expected_server_mods(
    pack: Mapping[str, Any],
    runtime: Path,
    *,
    disabled_optional_metadata: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    entries = pack.get("entries")
    if not isinstance(entries, list):
        raise SusyServerMaterializationError("Supersymmetry server inventory is unavailable")
    expected: list[dict[str, Any]] = []
    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("side") not in {"both", "server"}:
            continue
        if entry.get("metadata_path") in disabled_optional_metadata:
            continue
        filename = entry.get("filename")
        baseline = entry.get("baseline")
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or filename in names
            or not isinstance(baseline, dict)
            or baseline.get("hash_format") not in {"sha1", "sha256"}
            or not isinstance(baseline.get("hash"), str)
        ):
            raise SusyServerMaterializationError("Supersymmetry server mod identity is invalid")
        names.add(filename)
        path = runtime / "mods" / filename
        if path.is_symlink() or not path.is_file():
            raise SusyServerMaterializationError(
                f"materialized server is missing {filename}"
            )
        observed, size = _file_digest(path, str(baseline["hash_format"]))
        if observed != baseline["hash"]:
            raise SusyServerMaterializationError(
                f"materialized server mod differs from Packwiz: {filename}"
            )
        digest, _ = _file_digest(path, "sha256")
        expected.append(
            {
                "filename": filename,
                "metadata_path": entry.get("metadata_path"),
                "side": entry.get("side"),
                "hash_format": baseline["hash_format"],
                "content_hash": baseline["hash"],
                "sha256": digest,
                "size": size,
            }
        )
    actual = {
        path.name
        for path in (runtime / "mods").iterdir()
        if path.is_file() and not path.is_symlink() and path.suffix.casefold() == ".jar"
    }
    if actual != names:
        raise SusyServerMaterializationError(
            "materialized server mod inventory differs from Packwiz; "
            f"missing={sorted(names - actual)!r}, extra={sorted(actual - names)!r}"
        )
    expected.sort(key=lambda row: str(row["filename"]))
    return {
        "entry_count": len(expected),
        "inventory_sha256": "sha256:" + sha256(_canonical(expected)).hexdigest(),
        "entries": expected,
    }


def _validate_reusable(
    fixture_root: Path,
    *,
    plan: Mapping[str, Any],
    canonical_payload: Mapping[str, Any],
    canonical_materialization_id: str,
    canonical_provenance: Mapping[str, Any],
    source_variant: Mapping[str, Any],
    profile_sha256: str,
    locks: Mapping[str, Mapping[str, Any]],
    java_tool: Mapping[str, Any],
    packwiz_tool: Mapping[str, Any],
    pack: Mapping[str, Any],
    canonical_receipt: Mapping[str, Any],
    checkout_identity: Mapping[str, Any],
) -> dict[str, Any]:
    receipts_root = fixture_root / "receipts"
    try:
        receipts_info = receipts_root.lstat()
    except OSError as exc:
        raise SusyServerMaterializationError(
            "existing SUSY server materialization receipts are missing"
        ) from exc
    if stat.S_ISLNK(receipts_info.st_mode) or not stat.S_ISDIR(receipts_info.st_mode):
        raise SusyServerMaterializationError(
            "existing SUSY server materialization receipts are unsafe"
        )
    receipt_path = fixture_root / RECEIPT_RELATIVE_V2
    receipt = _regular_json(receipt_path, "SUSY server materialization receipt")
    recorded_runner = receipt.get("runner")
    v2_runner_valid = (
        isinstance(recorded_runner, dict)
        and isinstance(recorded_runner.get("sha256"), str)
        and len(recorded_runner["sha256"]) == 64
        and all(character in "0123456789abcdef" for character in recorded_runner["sha256"])
        and isinstance(recorded_runner.get("size"), int)
        and not isinstance(recorded_runner.get("size"), bool)
        and recorded_runner["size"] > 0
    )
    if (
        receipt.get("format") != MATERIALIZATION_RECEIPT_FORMAT_V2
        or receipt.get("schema_version") != 2
        or receipt.get("state") != "materialized"
        or receipt.get("plan_id") != plan.get("plan_id")
        or not verify_susy_server_materialization_receipt_identity(receipt)
        or not v2_runner_valid
        or receipt.get("source_variant") != dict(source_variant)
    ):
        raise SusyServerMaterializationError(
            "existing SUSY server materialization receipt is invalid"
        )
    seed = receipt.get("canonical_client_seed")
    platform = receipt.get("platform")
    tools = receipt.get("tools")
    target = receipt.get("target")
    if (
        not isinstance(seed, dict)
        or seed.get("materialization_id") != canonical_materialization_id
        or seed.get("payload") != dict(canonical_payload)
        or seed.get("selection")
        != "packwiz-declared-server-defaults-and-applicable-mod-seeds"
        or seed.get("provenance") != dict(canonical_provenance)
        or not isinstance(platform, dict)
        or platform.get("profile_sha256") != profile_sha256
        or platform.get("artifacts") != {key: dict(value) for key, value in locks.items()}
        or not isinstance(tools, dict)
        or tools.get("packwiz") != dict(packwiz_tool)
        or not isinstance(target, dict)
    ):
        raise SusyServerMaterializationError(
            "existing SUSY server materialization belongs to different inputs"
        )
    recorded_java = tools.get("java")
    if not isinstance(recorded_java, dict) or any(
        recorded_java.get(field) != java_tool.get(field)
        for field in ("identity", "java_uri", "sha256")
    ):
        raise SusyServerMaterializationError(
            "existing SUSY server materialization used a different Java runtime"
        )
    source_snapshot = receipt.get("source_snapshot")
    canonical_source = canonical_receipt.get("source_snapshot")
    if (
        not isinstance(source_snapshot, dict)
        or not isinstance(canonical_source, dict)
        or source_snapshot.get("selection") != "git-tracked-regular-files"
        or any(
            source_snapshot.get(field) != canonical_source.get(field)
            for field in (
                "tree_sha256",
                "file_count",
                "total_bytes",
                "untracked_excluded",
            )
        )
        or receipt.get("refreshed_pack") != canonical_receipt.get("refreshed_pack")
        or receipt.get("checkout_snapshot") != dict(checkout_identity)
    ):
        raise SusyServerMaterializationError(
            "existing SUSY server materialization source provenance is invalid"
        )
    template = _local_uri(target.get("template_uri"), "materialized server template", directory=True)
    if template != (fixture_root / ".minecraft").resolve():
        raise SusyServerMaterializationError(
            "existing SUSY server materialization target has drifted"
        )
    expected_variant_id = source_variant.get("variant_id")
    if (
        target.get("variant") != "packwiz-source-v2"
        or target.get("variant_id") != expected_variant_id
        or target.get("variant_root_uri") != fixture_root.as_uri()
        or target.get("fixture_root_uri") != fixture_root.as_uri()
        or target.get("receipt_uri") != receipt_path.as_uri()
        or target.get("planned_fixture_root_uri")
        != source_variant.get("planned_fixture_root_uri")
        or set(target)
        != {
            "fixture_root_uri",
            "template_uri",
            "receipt_uri",
            "payload",
            "variant",
            "variant_id",
            "variant_root_uri",
            "planned_fixture_root_uri",
        }
    ):
        raise SusyServerMaterializationError(
            "existing SUSY server materialization variant has drifted"
        )
    observed, _ = _tree_identity(template)
    if observed != target.get("payload"):
        raise SusyServerMaterializationError(
            "existing SUSY server materialization payload has drifted"
        )
    for forbidden in ("world", "logs", "crash-reports"):
        path = template / forbidden
        if path.exists() or path.is_symlink():
            raise SusyServerMaterializationError(
                f"existing SUSY server materialization contains {forbidden}"
            )
    refreshed = receipt.get("refreshed_pack")
    if not isinstance(refreshed, dict):
        raise SusyServerMaterializationError(
            "existing server materialization lacks its refreshed Packwiz input"
        )
    disabled_optional_metadata = _validate_server_packwiz_options(
        receipt.get("server_packwiz_options"),
        runtime=template,
        refreshed_pack=refreshed,
    )
    canonical_payload_record = canonical_receipt.get("payload")
    canonical_payload_uri = (
        canonical_payload_record.get("root_uri")
        if isinstance(canonical_payload_record, Mapping)
        else None
    )
    canonical_root = _local_uri(
        canonical_payload_uri,
        "canonical client payload",
        directory=True,
    )
    expected_seeded_files: list[dict[str, Any]] = []
    expected_seed_paths = _server_seed_paths(
        pack,
        canonical_root,
        include_packwiz_state=False,
        disabled_optional_metadata=disabled_optional_metadata,
    )
    for relative in sorted(
        expected_seed_paths,
        key=lambda item: item.as_posix(),
    ):
        digest, size = sha256_file(canonical_root / relative)
        expected_seeded_files.append(
            {
                "path": relative.as_posix(),
                "sha256": digest,
                "size": size,
            }
        )
    if seed.get("seeded_files") != expected_seeded_files:
        raise SusyServerMaterializationError(
            "existing SUSY server materialization seed provenance is invalid"
        )
    eula_path = template / "eula.txt"
    properties_path = template / "server.properties"
    try:
        if (
            eula_path.is_symlink()
            or eula_path.read_text(encoding="utf-8") != "eula=true\n"
            or properties_path.is_symlink()
            or properties_path.read_text(encoding="utf-8") != SERVER_PROPERTIES_TEXT
        ):
            raise SusyServerMaterializationError(
                "existing SUSY server materialization configuration is invalid"
            )
    except (OSError, UnicodeError) as exc:
        raise SusyServerMaterializationError(
            "existing SUSY server materialization configuration is unreadable"
        ) from exc

    server_payload = receipt.get("server_payload")
    recorded_launcher = (
        server_payload.get("launcher")
        if isinstance(server_payload, dict)
        else None
    )
    launchers = [
        path
        for path in sorted(template.glob("cleanroom-*.jar"))
        if path.is_file() and not path.is_symlink()
    ]
    if len(launchers) != 1 or not isinstance(recorded_launcher, dict):
        raise SusyServerMaterializationError(
            "existing SUSY server materialization launcher is invalid"
        )
    launcher_digest, launcher_size = sha256_file(launchers[0])
    if recorded_launcher != {
        "path": launchers[0].name,
        "sha256": launcher_digest,
        "size": launcher_size,
    }:
        raise SusyServerMaterializationError(
            "existing SUSY server materialization launcher identity is invalid"
        )
    if (
        server_payload.get("mod_inventory")
        != _expected_server_mods(
            pack,
            template,
            disabled_optional_metadata=disabled_optional_metadata,
        )
        or server_payload.get("eula")
        != {
            "accepted": True,
            "basis": "explicit --accept-minecraft-eula request",
        }
        or receipt.get("claims") != MATERIALIZATION_CLAIMS
        or receipt.get("limitations") != MATERIALIZATION_LIMITATIONS
    ):
        raise SusyServerMaterializationError(
            "existing SUSY server materialization semantic claims are invalid"
        )

    evidence_root = fixture_root / "evidence"
    try:
        evidence_info = evidence_root.lstat()
    except OSError as exc:
        raise SusyServerMaterializationError(
            "existing SUSY server materialization evidence is missing"
        ) from exc
    if stat.S_ISLNK(evidence_info.st_mode) or not stat.S_ISDIR(evidence_info.st_mode):
        raise SusyServerMaterializationError(
            "existing SUSY server materialization evidence is unsafe"
        )
    recorded_evidence = receipt.get("evidence")
    if not isinstance(recorded_evidence, dict):
        raise SusyServerMaterializationError(
            "existing SUSY server materialization evidence is invalid"
        )
    actual_evidence: dict[str, dict[str, Any]] = {}
    for path in sorted(evidence_root.iterdir(), key=lambda item: item.name):
        try:
            info = path.lstat()
        except OSError as exc:
            raise SusyServerMaterializationError(
                "existing SUSY server materialization evidence changed"
            ) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise SusyServerMaterializationError(
                "existing SUSY server materialization evidence contains an unsafe entry"
            )
        digest, size = sha256_file(path)
        actual_evidence[path.name] = {
            "uri": path.as_uri(),
            "sha256": digest,
            "size": size,
        }
    if actual_evidence != recorded_evidence:
        raise SusyServerMaterializationError(
            "existing SUSY server materialization evidence identity is invalid"
        )
    return receipt


def materialize_susy_server(
    suite_root: Path | str,
    run_id: str,
    *,
    server_java: Path | str | None = None,
    accept_minecraft_eula: bool = False,
    refresh_timeout_seconds: float = 300.0,
    install_timeout_seconds: float = 1800.0,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Create or reopen the exact server template for one retained SUSY run."""

    if accept_minecraft_eula is not True:
        raise SusyServerMaterializationError(
            "automatic server materialization requires --accept-minecraft-eula"
        )
    if any(
        not math.isfinite(value) or value <= 0
        for value in (refresh_timeout_seconds, install_timeout_seconds)
    ):
        raise SusyServerMaterializationError("materialization timeouts must be positive")
    suite = Path(suite_root).resolve()
    if configuration is not None and config_path is not None:
        raise SusyServerMaterializationError(
            "configuration and config_path are mutually exclusive"
        )
    try:
        active_configuration = configuration or load_workbench_configuration(
            suite,
            CONFIGURATION_PATH if config_path is None else config_path,
        )
    except WorkbenchConfigurationError as exc:
        raise SusyServerMaterializationError(
            f"Workbench configuration cannot be loaded: {exc}"
        ) from exc
    state = default_suite_state_root(suite)
    try:
        run_root, result, stage, _staged_instance, _candidate = _validate_retained_stage(
            suite, run_id
        )
    except (SusyModLaunchError, SusyModDevError) as exc:
        raise SusyServerMaterializationError(str(exc)) from exc
    result_path = run_root / "result.json"
    result_digest_before, _ = _file_digest(result_path, "sha256")
    retained_pack = result.get("supersymmetry")
    pack_root_value = retained_pack.get("root") if isinstance(retained_pack, dict) else None
    if not isinstance(pack_root_value, str):
        raise SusyServerMaterializationError(
            "retained run lacks its exact Supersymmetry checkout"
        )
    pack_root = Path(pack_root_value).resolve()
    try:
        pack = _load_pack(pack_root)
    except SusyModDevError as exc:
        raise SusyServerMaterializationError(str(exc)) from exc
    public_pack = {key: value for key, value in pack.items() if key != "entries"}
    if public_pack != retained_pack:
        raise SusyServerMaterializationError(
            "Supersymmetry checkout identity drifted after the retained build"
        )

    source = stage.get("source")
    if not isinstance(source, dict):
        raise SusyServerMaterializationError("retained client stage lacks its canonical source")
    canonical_instance = _local_uri(
        source.get("instance_uri"),
        "canonical client materialization",
        directory=True,
    )
    receipt_path = _local_uri(source.get("receipt_uri"), "canonical materialization receipt")
    canonical_receipt = _read_json(receipt_path, "canonical materialization receipt")
    canonical_payload = {
        key: source.get("payload", {}).get(key)
        for key in ("tree_sha256", "file_count", "total_bytes")
    }
    canonical_target = canonical_receipt.get("target")
    canonical_receipt_version = packwiz_materialization_version(
        canonical_receipt
    )
    if (
        canonical_receipt_version != 2
        or not verify_packwiz_materialization_receipt_identity(canonical_receipt)
        or canonical_receipt.get("materialization_id") != stage.get("materialization_id")
        or not isinstance(canonical_target, dict)
        or canonical_target.get("receipt_uri") != receipt_path.as_uri()
        or canonical_target.get("instance_root_uri") != canonical_instance.as_uri()
        or canonical_receipt.get("payload", {}).get("root_uri")
        != (canonical_instance / ".minecraft").as_uri()
    ):
        raise SusyServerMaterializationError(
            "canonical client receipt does not bind the retained stage"
        )
    canonical_provenance = _canonical_client_provenance(
        canonical_receipt,
        receipt_path,
    )
    canonical_root = canonical_instance / ".minecraft"
    observed_canonical, _ = _tree_identity(canonical_root)
    if observed_canonical != canonical_payload:
        raise SusyServerMaterializationError("canonical client payload has drifted")

    canonical_request = canonical_receipt.get("request")
    canonical_launcher = (
        canonical_request.get("launcher")
        if isinstance(canonical_request, dict)
        else None
    )
    if (
        not isinstance(canonical_launcher, str)
        or canonical_request.get("side") != "client"
    ):
        raise SusyServerMaterializationError(
            "canonical client receipt lacks its runtime-plan request"
        )
    try:
        current_client_plan = plan_project_runtime(
            suite,
            pack_root,
            side="client",
            launcher=canonical_launcher,
            state_root=state,
            configuration=active_configuration,
        )
    except RuntimePlanError as exc:
        raise SusyServerMaterializationError(str(exc)) from exc
    if current_client_plan.get("plan_id") != canonical_receipt.get("plan_id"):
        raise SusyServerMaterializationError(
            "current pack or platform profile differs from the canonical client plan"
        )

    try:
        plan = plan_project_runtime(
            suite,
            pack_root,
            side="server",
            launcher="dedicated-server",
            state_root=state,
            configuration=active_configuration,
        )
    except RuntimePlanError as exc:
        raise SusyServerMaterializationError(str(exc)) from exc
    blockers = plan.get("blockers")
    if plan.get("state") != "ready" or not isinstance(blockers, list) or blockers:
        ids = [str(row.get("id")) for row in blockers or [] if isinstance(row, dict)]
        raise SusyServerMaterializationError(
            "SUSY server runtime plan is blocked" + (": " + ", ".join(ids) if ids else "")
        )
    canonical_workspace = canonical_receipt.get("workspace")
    if (
        not isinstance(canonical_workspace, dict)
        or plan.get("workspace", {}).get("revision")
        != canonical_workspace.get("revision")
        or canonical_workspace.get("root_uri") != pack_root.as_uri()
    ):
        raise SusyServerMaterializationError(
            "server runtime plan belongs to a different Supersymmetry revision"
        )
    profile_source = active_configuration.platform_document.source
    profile_path = profile_source.path
    profile_bytes = profile_source.source_bytes
    locks, profile_sha256 = _profile_locks(
        profile_path,
        profile_bytes,
        active_configuration.platform_document.values,
        plan,
    )

    target = plan.get("target")
    if not isinstance(target, dict):
        raise SusyServerMaterializationError("server runtime plan lacks a target")
    source_variant = _server_source_variant(plan, canonical_provenance)
    fixture_family_relative = Path("fixtures/supersymmetry/server-v2")
    fixture_parent = _ensure_state_directory(
        state,
        fixture_family_relative,
    )
    fixture_root = fixture_parent / str(
        source_variant["variant_id"]
    ).removeprefix("sha256:")[:16]

    native_host = host_platform()
    candidates = None if server_java is None else [
        ("server-java", Path(server_java).expanduser().resolve())
    ]
    try:
        java_result = ensure_java_runtime(
            suite,
            state_root=state,
            host=native_host,
            candidates=candidates,
            configuration=active_configuration,
        )
        java_path, java_identity = _selected_java(java_result, native_host)
    except (JavaRuntimeError, RuntimeLaunchError) as exc:
        raise SusyServerMaterializationError(str(exc)) from exc
    if not java_path.with_name("javac").is_file():
        raise SusyServerMaterializationError(
            "selected server Java is not a full JDK; javac is required"
        )
    java_digest, _java_size = sha256_file(java_path)
    java_tool = {
        "identity": java_identity,
        "java_uri": java_path.as_uri(),
        "sha256": java_digest,
    }

    canonical_tools = canonical_receipt.get("tools")
    canonical_packwiz = (
        canonical_tools.get("packwiz")
        if isinstance(canonical_tools, dict)
        else None
    )
    packwiz_path = pack_root / ("packwiz.exe" if os.name == "nt" else "packwiz")
    try:
        measured_packwiz, observed_packwiz = _tool_identity(
            packwiz_path,
            "Packwiz executable",
        )
    except PackwizMaterializationError as exc:
        raise SusyServerMaterializationError(str(exc)) from exc
    if not isinstance(canonical_packwiz, dict) or any(
        observed_packwiz.get(field) != canonical_packwiz.get(field)
        for field in ("sha256", "size", "source_uri")
    ):
        raise SusyServerMaterializationError(
            "Packwiz executable differs from the canonical client materialization"
        )
    checkout_before = _checkout_identity(pack_root)

    if fixture_root.exists() or fixture_root.is_symlink():
        if not fixture_root.is_dir() or fixture_root.is_symlink():
            raise SusyServerMaterializationError(
                "existing SUSY server materialization target is unsafe"
            )
        receipt = _validate_reusable(
            fixture_root,
            plan=plan,
            canonical_payload=canonical_payload,
            canonical_materialization_id=str(stage["materialization_id"]),
            canonical_provenance=canonical_provenance,
            source_variant=source_variant,
            profile_sha256=profile_sha256,
            locks=locks,
            java_tool=java_tool,
            packwiz_tool=observed_packwiz,
            pack=pack,
            canonical_receipt=canonical_receipt,
            checkout_identity=checkout_before,
        )
        return {
            "format": MATERIALIZATION_RESULT_FORMAT_V2,
            "schema_version": 2,
            "outcome": "reused",
            "receipt": receipt,
        }

    fetched: dict[str, tuple[Path, str]] = {}
    for artifact_id, lock in locks.items():
        try:
            fetched[artifact_id] = fetch_verified_artifact(
                url=str(lock["url"]),
                expected_sha256=str(lock["sha256"]),
                expected_size=int(lock["size"]),
                state_root=state,
                label=artifact_id.replace("_", " ").title(),
                timeout_seconds=90.0,
                user_agent="Workbench-SUSY-Server-Materializer/0.1",
            )
        except ArtifactStoreError as exc:
            raise SusyServerMaterializationError(str(exc)) from exc
    artifacts = {
        artifact_id: _artifact_record(path, locks[artifact_id], outcome)
        for artifact_id, (path, outcome) in fetched.items()
    }

    if _ensure_state_directory(
        state,
        fixture_family_relative,
    ) != fixture_parent:
        raise SusyServerMaterializationError(
            "managed server state directory changed before materialization"
        )
    staging = Path(tempfile.mkdtemp(prefix=f".{fixture_root.name}.", dir=fixture_parent))
    source_staging = staging / "source"
    runtime = staging / ".minecraft"
    evidence = staging / "evidence"
    runtime.mkdir()
    evidence.mkdir()
    try:
        try:
            source_snapshot, exclusions = copy_tracked_workspace(pack_root, source_staging)
            recorded_source = canonical_receipt.get("source_snapshot")
            if (
                not isinstance(recorded_source, dict)
                or any(
                    source_snapshot.get(field) != recorded_source.get(field)
                    for field in ("tree_sha256", "file_count", "total_bytes")
                )
            ):
                raise SusyServerMaterializationError(
                    "Supersymmetry tracked source differs from the canonical client materialization"
                )
            staged_packwiz = source_staging / measured_packwiz.name
            staged_packwiz_identity = _tool_identity(staged_packwiz, "staged Packwiz executable")[1]
            if any(
                staged_packwiz_identity.get(field) != observed_packwiz.get(field)
                for field in ("sha256", "size")
            ):
                raise SusyServerMaterializationError(
                    "staged Packwiz executable differs from its tracked source"
                )
            _run_owned_logged(
                [
                    str(staged_packwiz),
                    "--cache",
                    str(state / "cache/packwiz/downloads"),
                    "--config",
                    str(state / "cache/packwiz/config.toml"),
                    "--yes",
                    "refresh",
                ],
                cwd=source_staging,
                log_path=evidence / "packwiz-refresh.log",
                timeout_seconds=refresh_timeout_seconds,
                label="Packwiz refresh",
                custody_root=staging,
            )
            refreshed_pack, refreshed_tree = _validate_refreshed_pack(plan, source_staging)
            server_decisions = _packwiz_optional_decisions(
                source_staging,
                side="server",
            )
            disabled_server_optional = frozenset(
                str(row["metadata_path"])
                for row in server_decisions
                if row.get("declared_default") is False
            )
            _run_owned_logged(
                [
                    str(java_path),
                    "-jar",
                    str(fetched["cleanroom_server"][0]),
                    "--install-server",
                    str(runtime),
                ],
                cwd=evidence,
                log_path=evidence / "cleanroom-installer.log",
                timeout_seconds=install_timeout_seconds,
                label="Cleanroom server installer",
                custody_root=staging,
            )
            launchers = [
                path
                for path in sorted(runtime.glob("cleanroom-*.jar"))
                if path.is_file() and not path.is_symlink()
            ]
            if len(launchers) != 1:
                raise SusyServerMaterializationError(
                    "Cleanroom installer did not produce one server launcher"
                )
            seed_paths = _server_seed_paths(
                pack,
                canonical_root,
                include_packwiz_state=False,
                disabled_optional_metadata=disabled_server_optional,
            )
            canonical_seed, seeded_files = _copy_seed(
                canonical_root,
                runtime,
                seed_paths,
            )
            server_initial_state = _write_packwiz_initial_state(
                runtime,
                server_decisions,
                side="server",
            )
            _run_owned_logged(
                [
                    str(java_path),
                    "-cp",
                    str(fetched["packwiz_installer"][0]),
                    INSTALLER_MAIN_CLASS,
                    "--no-gui",
                    "--side",
                    "server",
                    "--pack-folder",
                    str(runtime),
                    (source_staging / "pack.toml").as_uri(),
                ],
                cwd=runtime,
                log_path=evidence / "packwiz-installer.log",
                timeout_seconds=install_timeout_seconds,
                label="Packwiz server installer",
                custody_root=staging,
            )
            server_final_state, server_option_rows = (
                _verify_packwiz_final_state(
                    runtime,
                    server_decisions,
                    refreshed_pack,
                    side="server",
                )
            )
        except (PackwizMaterializationError, WorkingTreeError, OSError) as exc:
            raise SusyServerMaterializationError(str(exc)) from exc

        if (runtime / "missing_mods.txt").exists():
            raise SusyServerMaterializationError(
                "Packwiz server materialization is incomplete: missing_mods.txt exists"
            )
        if (runtime / "world").exists() or (runtime / "world").is_symlink():
            raise SusyServerMaterializationError(
                "fresh server materialization unexpectedly contains a world"
            )
        try:
            (runtime / "eula.txt").write_text("eula=true\n", encoding="utf-8")
            (runtime / "server.properties").write_text(
                SERVER_PROPERTIES_TEXT,
                encoding="utf-8",
            )
        except OSError as exc:
            raise SusyServerMaterializationError(
                "cannot write disposable server configuration"
            ) from exc
        server_mods = _expected_server_mods(
            pack,
            runtime,
            disabled_optional_metadata=disabled_server_optional,
        )
        server_packwiz_options = _server_packwiz_options(
            published_runtime=fixture_root / ".minecraft",
            decisions=server_decisions,
            initial=server_initial_state,
            final=server_final_state,
            rows=server_option_rows,
        )
        payload, _payload_records = _tree_identity(runtime)
        canonical_after, _ = _tree_identity(canonical_root)
        if canonical_after != canonical_payload or canonical_seed != canonical_payload:
            raise SusyServerMaterializationError(
                "canonical client changed during server materialization"
            )
        try:
            _validate_retained_stage(suite, run_id)
        except (SusyModLaunchError, SusyModDevError) as exc:
            raise SusyServerMaterializationError(
                f"retained run changed during server materialization: {exc}"
            ) from exc
        result_digest_after, _ = _file_digest(result_path, "sha256")
        if result_digest_after != result_digest_before:
            raise SusyServerMaterializationError(
                "retained run changed during server materialization"
            )
        if _checkout_identity(pack_root) != checkout_before:
            raise SusyServerMaterializationError(
                "Supersymmetry checkout changed during server materialization"
            )
        try:
            plan_after = plan_project_runtime(
                suite,
                pack_root,
                side="server",
                launcher="dedicated-server",
                state_root=state,
                configuration=active_configuration,
            )
        except RuntimePlanError as exc:
            raise SusyServerMaterializationError(str(exc)) from exc
        if plan_after != plan or profile_path.read_bytes() != profile_bytes:
            raise SusyServerMaterializationError(
                "server plan or Cleanroom profile changed during materialization"
            )

        launcher_digest, launcher_size = sha256_file(launchers[0])
        evidence_records = {}
        for path in sorted(evidence.iterdir(), key=lambda item: item.name):
            if path.is_file() and not path.is_symlink():
                digest, size = sha256_file(path)
                evidence_records[path.name] = {
                    "uri": (fixture_root / "evidence" / path.name).as_uri(),
                    "sha256": digest,
                    "size": size,
                }
        canonical_seed_record: dict[str, Any] = {
            "materialization_id": stage["materialization_id"],
            "receipt_uri": receipt_path.as_uri(),
            "payload": canonical_payload,
            "selection": "packwiz-declared-server-defaults-and-applicable-mod-seeds",
            "seeded_files": seeded_files,
            "provenance": dict(canonical_provenance),
        }

        target_record: dict[str, Any] = {
            "fixture_root_uri": fixture_root.as_uri(),
            "template_uri": (fixture_root / ".minecraft").as_uri(),
            "receipt_uri": (fixture_root / RECEIPT_RELATIVE_V2).as_uri(),
            "payload": payload,
            "variant": "packwiz-source-v2",
            "variant_id": source_variant["variant_id"],
            "variant_root_uri": fixture_root.as_uri(),
            "planned_fixture_root_uri": source_variant[
                "planned_fixture_root_uri"
            ],
        }

        receipt: dict[str, Any] = {
            "format": MATERIALIZATION_RECEIPT_FORMAT_V2,
            "schema_version": 2,
            "state": "materialized",
            "operation_class": "local-mutation",
            "plan_id": plan["plan_id"],
            "requested_by_run_id": run_id,
            "runner": _runner_identity(),
            "workspace": deepcopy(plan["workspace"]),
            "project": deepcopy(plan["project"]),
            "source_snapshot": {
                **source_snapshot,
                "selection": "git-tracked-regular-files",
                "untracked_excluded": exclusions,
            },
            "checkout_snapshot": checkout_before,
            "refreshed_pack": {
                **refreshed_pack,
                "staged_tree": refreshed_tree,
            },
            "canonical_client_seed": canonical_seed_record,
            "platform": {
                "profile_id": target.get("platform_profile_id"),
                "profile_sha256": profile_sha256,
                "cleanroom_version": target.get("cleanroom_version"),
                "artifacts": {key: dict(value) for key, value in locks.items()},
            },
            "tools": {
                "java": {
                    "identity": java_identity,
                    "outcome": java_result.get("outcome"),
                    "java_uri": java_path.as_uri(),
                    "sha256": java_digest,
                },
                "packwiz": observed_packwiz,
                "artifacts": artifacts,
            },
            "server_payload": {
                "mod_inventory": server_mods,
                "launcher": {
                    "path": launchers[0].name,
                    "sha256": launcher_digest,
                    "size": launcher_size,
                },
                "eula": {
                    "accepted": True,
                    "basis": "explicit --accept-minecraft-eula request",
                },
            },
            "target": target_record,
            "evidence": evidence_records,
            "claims": dict(MATERIALIZATION_CLAIMS),
            "limitations": list(MATERIALIZATION_LIMITATIONS),
            "source_variant": dict(source_variant),
            "server_packwiz_options": server_packwiz_options,
        }
        receipt["materialization_id"] = _receipt_id(receipt)
        _write_json(staging / RECEIPT_RELATIVE_V2, receipt)
        if _ensure_state_directory(
            state,
            fixture_family_relative,
        ) != fixture_parent:
            raise SusyServerMaterializationError(
                "managed server state directory changed before atomic publish"
            )
        _rename_no_replace(staging, fixture_root)
        staging = Path()
        try:
            published = _validate_reusable(
                fixture_root,
                plan=plan,
                canonical_payload=canonical_payload,
                canonical_materialization_id=str(stage["materialization_id"]),
                canonical_provenance=canonical_provenance,
                source_variant=source_variant,
                profile_sha256=profile_sha256,
                locks=locks,
                java_tool=java_tool,
                packwiz_tool=observed_packwiz,
                pack=pack,
                canonical_receipt=canonical_receipt,
                checkout_identity=checkout_before,
            )
        except Exception as exc:
            rejected = fixture_parent / (
                f".rejected-{fixture_root.name}-{os.getpid()}-{time.time_ns()}"
            )
            try:
                _rename_no_replace(fixture_root, rejected)
            except Exception as quarantine_exc:
                raise SusyServerMaterializationError(
                    "published SUSY server materialization failed verification and "
                    "could not be moved out of the canonical target"
                ) from quarantine_exc
            raise SusyServerMaterializationError(
                "published SUSY server materialization failed verification; "
                f"rejected bytes retained at {rejected.as_uri()}"
            ) from exc
        return {
            "format": MATERIALIZATION_RESULT_FORMAT_V2,
            "schema_version": 2,
            "outcome": "installed",
            "receipt": published,
        }
    finally:
        if staging != Path() and staging.exists():
            shutil.rmtree(staging)


def render_susy_server_materialization(
    result: Mapping[str, Any], *, json_output: bool = False
) -> str:
    result_pair = (result.get("format"), result.get("schema_version"))
    if result_pair != (MATERIALIZATION_RESULT_FORMAT_V2, 2):
        raise SusyServerMaterializationError(
            "unsupported SUSY server materialization result"
        )
    if json_output:
        return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    receipt = result.get("receipt")
    if not isinstance(receipt, Mapping):
        raise SusyServerMaterializationError(
            "SUSY server materialization result lacks its receipt"
        )
    if (
        susy_server_materialization_version(receipt) != 2
        or not verify_susy_server_materialization_receipt_identity(receipt)
    ):
        raise SusyServerMaterializationError(
            "SUSY server materialization result has an invalid receipt"
        )
    target = receipt.get("target")
    server = receipt.get("server_payload")
    inventory = server.get("mod_inventory") if isinstance(server, Mapping) else None
    lines = [
        "Supersymmetry Server Materialization",
        f"Outcome: {result.get('outcome')}",
        f"Template: {target.get('template_uri') if isinstance(target, Mapping) else 'unavailable'}",
        f"Mods: {inventory.get('entry_count') if isinstance(inventory, Mapping) else 'unavailable'}",
        f"Cleanroom: {receipt.get('platform', {}).get('cleanroom_version')}",
        f"Java: {receipt.get('tools', {}).get('java', {}).get('identity', {}).get('runtime_version', 'managed profile runtime')}",
        "Launch: not attempted",
    ]
    return "\n".join(lines) + "\n"
