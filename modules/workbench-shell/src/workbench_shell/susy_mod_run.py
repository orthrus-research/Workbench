"""Join the existing SUSY client and server owners into one developer run.

This module deliberately owns only orchestration.  The constituent build,
Packwiz materializations, exact loaded-source proof, process custody, and
cleanup decisions remain with their existing owners.  A dev-run receipt keeps
byte-bound references to those owner receipts and never relabels two
independent smokes as connected gameplay or client/server parity.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse
from urllib.request import url2pathname

from .susy_mod_dev import RESULT_FORMAT, _digest
from .susy_mod_launch import (
    COMPATIBILITY_EXPERIMENTS,
    LAUNCH_RECEIPT_FORMAT,
    LAUNCH_RESULT_FORMAT,
    SusyModLaunchError,
    _validate_retained_stage,
    launch_susy_mod_client,
)
from .susy_mod_server import (
    SERVER_EXPERIMENTS,
    SERVER_RECEIPT_FORMAT,
    SERVER_RESULT_FORMAT,
    SusyModServerError,
    _safe_run_root,
    _validate_retained_build,
    launch_susy_mod_server,
)


DEV_RUN_RECEIPT_FORMAT = "workbench-susy-mod-dev-run-receipt-v1"
DEV_RUN_RESULT_FORMAT = "workbench-susy-mod-dev-run-result-v1"
DEV_RUN_SIDES = ("auto", "client", "server", "both")
DEV_RUN_ID_PREFIX = "workbench-susy-mod-dev-run:"
DEV_RUN_REQUEST_ID_PREFIX = "workbench-susy-mod-dev-run-request:sha256:"
_PHYSICAL_SIDES = ("client", "server")
_DEV_RUN_ID_RE = re.compile(
    r"workbench-susy-dev-run-[0-9a-f]{12}-"
    r"[0-9]{8}T[0-9]{12}Z-[0-9a-f]{64}"
)


class SusyModRunError(RuntimeError):
    """An applicable-side constituent-mod run cannot proceed safely."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_digest(path: Path) -> tuple[str, int]:
    digest = sha256()
    size = 0
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise SusyModRunError(f"retained evidence must be a regular file: {path}")
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise SusyModRunError(f"cannot read retained evidence: {path}") from exc
    return digest.hexdigest(), size


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise SusyModRunError(f"{label} must be a regular file: {path}")
        if info.st_size > 16 * 1024 * 1024:
            raise SusyModRunError(f"{label} exceeds the retained-record limit")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SusyModRunError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise SusyModRunError(f"{label} must be a JSON object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    raw = json.dumps(
        value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    temporary = path.with_name(path.name + ".tmp-" + secrets.token_hex(6))
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise SusyModRunError(f"cannot retain unified dev run: {path}") from exc


def _file_uri(value: Any, label: str) -> Path:
    if not isinstance(value, str):
        raise SusyModRunError(f"{label} URI is missing")
    parsed = urlparse(value)
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise SusyModRunError(f"{label} must use a local file URI")
    return Path(url2pathname(parsed.path))


def _regular_directory(path: Path, label: str) -> Path:
    try:
        info = path.lstat()
    except OSError as exc:
        raise SusyModRunError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SusyModRunError(f"{label} must be a regular directory: {path}")
    return path.resolve()


def _ensure_child_directory(parent: Path, name: str) -> Path:
    target = parent / name
    if not target.exists() and not target.is_symlink():
        try:
            target.mkdir(mode=0o700)
        except OSError as exc:
            raise SusyModRunError(f"cannot create dev-run evidence directory: {target}") from exc
    resolved = _regular_directory(target, "dev-run evidence directory")
    if resolved.parent != parent.resolve():
        raise SusyModRunError("dev-run evidence directory escapes its retained run")
    return resolved


def _seal(receipt: Mapping[str, Any]) -> dict[str, Any]:
    sealed = {
        key: deepcopy(value)
        for key, value in receipt.items()
        if key != "receipt_id"
    }
    sealed["receipt_id"] = DEV_RUN_ID_PREFIX + sha256(_canonical(sealed)).hexdigest()
    return sealed


def _verify_receipt_identity(receipt: Mapping[str, Any]) -> bool:
    if (
        receipt.get("format") != DEV_RUN_RECEIPT_FORMAT
        or receipt.get("schema_version") != 1
    ):
        return False
    without_id = {
        key: deepcopy(value)
        for key, value in receipt.items()
        if key != "receipt_id"
    }
    return receipt.get("receipt_id") == DEV_RUN_ID_PREFIX + sha256(
        _canonical(without_id)
    ).hexdigest()


def _client_launch_id(receipt: Mapping[str, Any]) -> str:
    candidate = receipt.get("candidate")
    launcher = receipt.get("launcher")
    java = receipt.get("java")
    if not all(isinstance(value, Mapping) for value in (candidate, launcher, java)):
        raise SusyModRunError("client owner receipt identity fields are incomplete")
    assert isinstance(candidate, Mapping)
    assert isinstance(launcher, Mapping)
    assert isinstance(java, Mapping)
    identity = {
        "run_id": receipt.get("run_id"),
        "stage_id": receipt.get("stage_id"),
        "instance_id": launcher.get("instance_id"),
        "candidate_sha256": candidate.get("sha256"),
        "started_at": receipt.get("started_at"),
        "launcher_sha256": launcher.get("sha256"),
        "java_runtime_id": java.get("runtime_id"),
    }
    if not all(isinstance(value, str) and value for value in identity.values()):
        raise SusyModRunError("client owner receipt identity fields are invalid")
    return "workbench-susy-mod-launch:sha256:" + sha256(
        _canonical(identity)
    ).hexdigest()


def _owner_id(side: str, receipt: Mapping[str, Any]) -> str:
    if side == "client":
        expected = _client_launch_id(receipt)
        if receipt.get("launch_id") != expected:
            raise SusyModRunError("client owner receipt identity has drifted")
        return expected
    without_id = {
        key: value for key, value in receipt.items() if key != "receipt_id"
    }
    expected = "workbench-susy-mod-server-launch:" + sha256(
        _canonical(without_id)
    ).hexdigest()
    if receipt.get("receipt_id") != expected:
        raise SusyModRunError("server owner receipt identity has drifted")
    return expected


def _cleanup_safe(receipt: Mapping[str, Any]) -> bool:
    cleanup = receipt.get("cleanup")
    return bool(
        isinstance(cleanup, Mapping)
        and cleanup.get("owned_processes_running") is False
        and not cleanup.get("errors")
    )


def _source_result(suite: Path, run_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    try:
        run_root = _safe_run_root(suite, run_id)
    except SusyModServerError as exc:
        raise SusyModRunError(str(exc)) from exc
    result_path = run_root / "result.json"
    result = _read_json(result_path, "retained SUSY result")
    without_id = {key: value for key, value in result.items() if key != "result_id"}
    if (
        result.get("format") != RESULT_FORMAT
        or result.get("schema_version") != 1
        or result.get("run_id") != run_id
        or result.get("outcome") != "passed"
        or result.get("failed_stage") is not None
        or result.get("result_id")
        != "workbench-susy-mod-dev-result:" + _digest(without_id)
    ):
        raise SusyModRunError("unified run requires an exact passing retained build")
    replacement = result.get("replacement")
    match = replacement.get("match") if isinstance(replacement, Mapping) else None
    selected = match.get("selected") if isinstance(match, Mapping) else None
    artifacts = result.get("artifact_set")
    if (
        not isinstance(selected, Mapping)
        or match.get("state") != "exact"
        or selected.get("side") not in {"both", "client", "server"}
        or not isinstance(artifacts, list)
        or len(artifacts) != 1
        or not isinstance(artifacts[0], Mapping)
    ):
        raise SusyModRunError("retained build lacks one exact Packwiz replacement")
    applicable = replacement.get("applicable_sides")
    expected_applicable = {
        "both": ["client", "server"],
        "client": ["client"],
        "server": ["server"],
    }[str(selected["side"])]
    if applicable != expected_applicable:
        raise SusyModRunError("retained Packwiz side applicability has drifted")
    artifact = artifacts[0]
    artifact_path_value = artifact.get("path")
    if not isinstance(artifact_path_value, str):
        raise SusyModRunError("retained build artifact path is missing")
    artifact_path = Path(artifact_path_value)
    if artifact_path.is_symlink():
        raise SusyModRunError("retained build artifact must not be a symbolic link")
    artifact_path = artifact_path.resolve()
    if not artifact_path.is_relative_to(run_root) or not artifact_path.is_file():
        raise SusyModRunError("retained build artifact escapes its managed run")
    observed_sha, observed_size = _file_digest(artifact_path)
    if observed_sha != artifact.get("sha256") or observed_size != artifact.get("size"):
        raise SusyModRunError("retained build artifact identity has drifted")
    result_sha, result_size = _file_digest(result_path)
    context = {
        "result_id": result["result_id"],
        "result_uri": result_path.as_uri(),
        "result_sha256": result_sha,
        "result_size": result_size,
        "plan_id": result.get("plan_id"),
        "candidate": {
            "path": artifact_path.as_uri(),
            "sha256": artifact.get("sha256"),
            "size": artifact.get("size"),
            "mod_ids": deepcopy(artifact.get("mod_ids")),
        },
        "pack": {
            "root": result.get("supersymmetry", {}).get("root"),
            "version": result.get("supersymmetry", {}).get("version"),
            "manifest_sha256": result.get("supersymmetry", {}).get(
                "manifest_sha256"
            ),
            "metadata_path": selected.get("metadata_path"),
            "metadata_sha256": selected.get("metadata_sha256"),
            "side": selected.get("side"),
            "applicable_sides": expected_applicable,
        },
    }
    return run_root, result, context


def _selected_sides(requested: str, applicable: Sequence[str]) -> tuple[str, ...]:
    if requested not in DEV_RUN_SIDES:
        raise SusyModRunError(f"unsupported dev-run side: {requested}")
    applicable_set = set(applicable)
    desired = (
        applicable_set
        if requested == "auto"
        else ({"client", "server"} if requested == "both" else {requested})
    )
    unavailable = sorted(desired - applicable_set)
    if unavailable:
        raise SusyModRunError(
            "requested run side is not applicable to the exact Packwiz entry: "
            + ", ".join(unavailable)
        )
    return tuple(side for side in _PHYSICAL_SIDES if side in desired)


def _path_input(value: Path | str | None) -> str | None:
    return None if value is None else Path(value).expanduser().resolve().as_uri()


def _runner_identity() -> dict[str, Any]:
    path = Path(__file__).resolve()
    digest, size = _file_digest(path)
    return {"uri": path.as_uri(), "sha256": digest, "size": size}


def _validate_inputs(
    *,
    selected: Sequence[str],
    launcher: str,
    launcher_executable: Path | str | None,
    launcher_root: Path | str | None,
    launcher_profile: str | None,
    launcher_java: Path | str | None,
    launcher_java_state: Path | str | None,
    client_experiments: Sequence[str],
    server_template: Path | str | None,
    server_java: Path | str | None,
    accept_minecraft_eula: bool,
    server_experiments: Sequence[str],
    memory_mib: int,
    client_timeout_seconds: float,
    server_timeout_seconds: float,
    shutdown_timeout_seconds: float,
    client_poll_interval_seconds: float,
    server_poll_interval_seconds: float,
) -> None:
    if launcher not in {"prism", "multimc"}:
        raise SusyModRunError(f"unsupported client launcher: {launcher}")
    if (
        len(set(client_experiments)) != len(client_experiments)
        or any(item not in COMPATIBILITY_EXPERIMENTS for item in client_experiments)
        or len(set(server_experiments)) != len(server_experiments)
        or any(item not in SERVER_EXPERIMENTS for item in server_experiments)
    ):
        raise SusyModRunError("runtime compatibility experiment is unknown or repeated")
    if isinstance(memory_mib, bool) or not isinstance(memory_mib, int) or not (
        1024 <= memory_mib <= 131072
    ):
        raise SusyModRunError("dev-run memory must be between 1024 and 131072 MiB")
    if any(
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or value <= 0
        for value in (
            client_timeout_seconds,
            server_timeout_seconds,
            shutdown_timeout_seconds,
            client_poll_interval_seconds,
            server_poll_interval_seconds,
        )
    ):
        raise SusyModRunError("dev-run timeouts and poll intervals must be positive")
    if "server" in selected:
        if (server_template is None) == (not accept_minecraft_eula):
            raise SusyModRunError(
                "selected server run requires exactly one of an explicit "
                "server template or Minecraft EULA acceptance"
            )
    elif server_template is not None or server_java is not None or accept_minecraft_eula:
        raise SusyModRunError("server inputs were supplied for a client-only run")
    elif any(item not in client_experiments for item in server_experiments):
        raise SusyModRunError(
            "a server-only compatibility experiment was supplied for a client-only run"
        )
    if "client" not in selected:
        if any(
            value is not None
            for value in (
                launcher_executable,
                launcher_root,
                launcher_profile,
                launcher_java,
                launcher_java_state,
            )
        ):
            raise SusyModRunError("client inputs were supplied for a server-only run")
        if any(item not in server_experiments for item in client_experiments):
            raise SusyModRunError(
                "a client-only compatibility experiment was supplied for a server-only run"
            )


def _preflight_owners(
    suite: Path,
    run_id: str,
    selected: Sequence[str],
    *,
    managed_server: bool,
) -> None:
    if "client" in selected:
        try:
            _validate_retained_stage(suite, run_id)
        except SusyModLaunchError as exc:
            raise SusyModRunError(str(exc)) from exc
    if "server" in selected:
        try:
            _validate_retained_build(suite, run_id)
        except SusyModServerError as exc:
            raise SusyModRunError(str(exc)) from exc
    if "server" in selected and managed_server and "client" not in selected:
        try:
            _validate_retained_stage(suite, run_id)
        except SusyModLaunchError as exc:
            raise SusyModRunError(
                "managed server materialization requires a retained canonical "
                "client-stage seed; use an explicit measured --server-template "
                "for this server-only run"
            ) from exc


def _request_projection(
    *,
    source: Mapping[str, Any],
    requested_side: str,
    selected_sides: Sequence[str],
    launcher: str,
    launcher_executable: Path | str | None,
    launcher_root: Path | str | None,
    launcher_profile: str | None,
    launcher_java: Path | str | None,
    launcher_java_state: Path | str | None,
    client_experiments: Sequence[str],
    server_template: Path | str | None,
    server_java: Path | str | None,
    accept_minecraft_eula: bool,
    server_experiments: Sequence[str],
    memory_mib: int,
    offline_name: str,
    client_timeout_seconds: float,
    server_timeout_seconds: float,
    shutdown_timeout_seconds: float,
    client_poll_interval_seconds: float,
    server_poll_interval_seconds: float,
) -> dict[str, Any]:
    return {
        "source_result_id": source["result_id"],
        "candidate_sha256": source["candidate"]["sha256"],
        "requested_side": requested_side,
        "selected_sides": list(selected_sides),
        "client": {
            "launcher": launcher,
            "launcher_executable_uri": _path_input(launcher_executable),
            "launcher_root_uri": _path_input(launcher_root),
            "account_mode": "launcher-profile" if launcher_profile else "offline",
            "launcher_profile": "explicit-redacted" if launcher_profile else None,
            "offline_name": None if launcher_profile else offline_name,
            "java_uri": _path_input(launcher_java),
            "java_state_uri": _path_input(launcher_java_state),
            "compatibility_experiments": list(client_experiments),
            "timeout_seconds": float(client_timeout_seconds),
            "poll_interval_seconds": float(client_poll_interval_seconds),
        },
        "server": {
            "template_mode": "override" if server_template is not None else "managed",
            "template_uri": _path_input(server_template),
            "minecraft_eula_accepted": bool(accept_minecraft_eula),
            "java_uri": _path_input(server_java),
            "compatibility_experiments": list(server_experiments),
            "timeout_seconds": float(server_timeout_seconds),
            "shutdown_timeout_seconds": float(shutdown_timeout_seconds),
            "poll_interval_seconds": float(server_poll_interval_seconds),
        },
        "memory_mib": memory_mib,
    }


def _receipt_path_for_child(
    side: str, receipt: Mapping[str, Any], run_root: Path
) -> Path:
    if side == "client":
        target = receipt.get("target")
        path = _file_uri(
            target.get("receipt_uri") if isinstance(target, Mapping) else None,
            "client launch receipt",
        )
    else:
        attempt_id = receipt.get("attempt_id")
        if (
            not isinstance(attempt_id, str)
            or not attempt_id
            or Path(attempt_id).name != attempt_id
        ):
            raise SusyModRunError("server owner receipt lacks a safe attempt ID")
        path = (
            run_root
            / "runtime/server-launches"
            / attempt_id
            / "susy-mod-server-launch-v2.json"
        )
    if path.is_symlink():
        raise SusyModRunError(f"{side} owner receipt must not be a symbolic link")
    resolved = path.resolve()
    if not resolved.is_relative_to(run_root) or not resolved.is_file():
        raise SusyModRunError(f"{side} owner receipt escapes the retained run")
    return resolved


def _child_reference(
    side: str,
    result: Mapping[str, Any],
    *,
    run_id: str,
    run_root: Path,
) -> dict[str, Any]:
    expected_result = LAUNCH_RESULT_FORMAT if side == "client" else SERVER_RESULT_FORMAT
    expected_receipt = (
        LAUNCH_RECEIPT_FORMAT if side == "client" else SERVER_RECEIPT_FORMAT
    )
    expected_version = 1 if side == "client" else 2
    receipt_value = result.get("receipt") if isinstance(result, Mapping) else None
    if (
        result.get("format") != expected_result
        or result.get("schema_version") != expected_version
        or result.get("outcome") not in {"passed", "failed"}
        or not isinstance(receipt_value, Mapping)
        or receipt_value.get("format") != expected_receipt
        or receipt_value.get("schema_version") != expected_version
        or receipt_value.get("run_id") != run_id
        or receipt_value.get("outcome") != result.get("outcome")
    ):
        raise SusyModRunError(f"{side} owner returned an invalid result/receipt pair")
    receipt = dict(receipt_value)
    path = _receipt_path_for_child(side, receipt, run_root)
    on_disk = _read_json(path, f"{side} owner receipt")
    if on_disk != receipt:
        raise SusyModRunError(f"{side} owner receipt bytes differ from its result")
    owner_id = _owner_id(side, receipt)
    digest, size = _file_digest(path)
    cleanup_safe = _cleanup_safe(receipt)
    return {
        "result_format": result["format"],
        "receipt_format": receipt["format"],
        "owner_id": owner_id,
        "outcome": result["outcome"],
        "failure_kind": receipt.get("failure_kind"),
        "receipt_uri": path.as_uri(),
        "receipt_sha256": digest,
        "receipt_size": size,
        "cleanup_safe": cleanup_safe,
    }


def _verify_reference(
    reference: Mapping[str, Any], run_root: Path, *, side: str, run_id: str
) -> dict[str, Any]:
    path = _file_uri(reference.get("receipt_uri"), "owner receipt")
    if path.is_symlink():
        raise SusyModRunError("owner receipt reference became a symbolic link")
    path = path.resolve()
    if not path.is_relative_to(run_root) or not path.is_file():
        raise SusyModRunError("owner receipt reference escapes its retained run")
    digest, size = _file_digest(path)
    if digest != reference.get("receipt_sha256") or size != reference.get(
        "receipt_size"
    ):
        raise SusyModRunError("referenced owner receipt bytes have drifted")
    receipt = _read_json(path, f"{side} owner receipt")
    expected_receipt = (
        LAUNCH_RECEIPT_FORMAT if side == "client" else SERVER_RECEIPT_FORMAT
    )
    expected_result = (
        LAUNCH_RESULT_FORMAT if side == "client" else SERVER_RESULT_FORMAT
    )
    expected_version = 1 if side == "client" else 2
    if (
        receipt.get("format") != expected_receipt
        or receipt.get("schema_version") != expected_version
        or receipt.get("run_id") != run_id
        or receipt.get("outcome") not in {"passed", "failed"}
        or reference.get("receipt_format") != expected_receipt
        or reference.get("result_format") != expected_result
        or reference.get("outcome") != receipt.get("outcome")
        or reference.get("failure_kind") != receipt.get("failure_kind")
        or reference.get("cleanup_safe") is not _cleanup_safe(receipt)
        or reference.get("owner_id") != _owner_id(side, receipt)
    ):
        raise SusyModRunError(f"referenced {side} owner receipt semantics drifted")
    return receipt


def _validate_request_owner_binding(
    side: str,
    *,
    request: Mapping[str, Any],
    owner_receipt: Mapping[str, Any],
) -> None:
    memory = request.get("memory_mib")
    owner_request = request.get(side)
    candidate = owner_receipt.get("candidate")
    if not isinstance(owner_request, Mapping):
        raise SusyModRunError(f"retained {side} request is invalid")
    if (
        not isinstance(candidate, Mapping)
        or candidate.get("sha256") != request.get("candidate_sha256")
    ):
        raise SusyModRunError(f"retained {side} owner used another candidate")
    if side == "client":
        policy = owner_receipt.get("launch_policy")
        launcher = owner_receipt.get("launcher")
        if (
            not isinstance(policy, Mapping)
            or not isinstance(launcher, Mapping)
            or policy.get("memory_mib") != memory
            or policy.get("timeout_seconds") != owner_request.get("timeout_seconds")
            or policy.get("compatibility_experiments")
            != owner_request.get("compatibility_experiments")
            or policy.get("account_mode") != owner_request.get("account_mode")
            or policy.get("launcher_profile") != owner_request.get("launcher_profile")
            or policy.get("offline_name") != owner_request.get("offline_name")
            or launcher.get("family") != owner_request.get("launcher")
        ):
            raise SusyModRunError("retained client request does not bind its owner")
        return

    command = owner_receipt.get("command")
    projection = owner_receipt.get("projection")
    records = (
        projection.get("compatibility_experiments")
        if isinstance(projection, Mapping)
        else None
    )
    if not isinstance(command, list) or not all(
        isinstance(value, str) for value in command
    ):
        raise SusyModRunError("retained server owner command is invalid")
    experiment_ids: list[str] = []
    if not isinstance(records, list):
        raise SusyModRunError("retained server owner experiments are invalid")
    for record in records:
        if not isinstance(record, Mapping) or not isinstance(
            record.get("experiment_id"), str
        ):
            raise SusyModRunError("retained server owner experiment is invalid")
        experiment_ids.append(str(record["experiment_id"]))
    requested_experiments = owner_request.get("compatibility_experiments")
    expected_experiments = (
        [
            item
            for item in requested_experiments
            if item in COMPATIBILITY_EXPERIMENTS
        ]
        + [
            item
            for item in requested_experiments
            if item not in COMPATIBILITY_EXPERIMENTS
        ]
        if isinstance(requested_experiments, list)
        else None
    )
    if (
        owner_receipt.get("runtime_subject") is not None
        or f"-Xmx{memory}M" not in command
        or experiment_ids != expected_experiments
    ):
        raise SusyModRunError("retained server request does not bind its owner")


def _source_unchanged(source: Mapping[str, Any]) -> bool:
    try:
        path = _file_uri(source.get("result_uri"), "source result")
        digest, size = _file_digest(path)
    except SusyModRunError:
        return False
    return digest == source.get("result_sha256") and size == source.get("result_size")


def _side_rows(
    applicable: Sequence[str], selected: Sequence[str]
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for side in _PHYSICAL_SIDES:
        if side not in applicable:
            state = "not-applicable"
        elif side not in selected:
            state = "not-selected"
        else:
            state = "pending"
        rows[side] = {
            "applicable": side in applicable,
            "selected": side in selected,
            "state": state,
            "owner": None,
            "error": None,
        }
    return rows


def _validate_completed_receipt(
    receipt: Mapping[str, Any],
    *,
    source: Mapping[str, Any],
    run_id: str,
    dev_run_id: str,
) -> None:
    if (
        not _verify_receipt_identity(receipt)
        or receipt.get("dev_run_id") != dev_run_id
        or receipt.get("run_id") != run_id
        or receipt.get("source") != source
        or receipt.get("state") != "complete"
    ):
        raise SusyModRunError("retained unified dev-run identity has drifted")
    request = receipt.get("request")
    if (
        not isinstance(request, Mapping)
        or set(request)
        != {
            "source_result_id",
            "candidate_sha256",
            "requested_side",
            "selected_sides",
            "client",
            "server",
            "memory_mib",
        }
    ):
        raise SusyModRunError("retained unified dev-run request is invalid")
    selected_request = request.get("selected_sides")
    client_request = request.get("client")
    server_request = request.get("server")
    if (
        not isinstance(client_request, Mapping)
        or set(client_request)
        != {
            "launcher",
            "launcher_executable_uri",
            "launcher_root_uri",
            "account_mode",
            "launcher_profile",
            "offline_name",
            "java_uri",
            "java_state_uri",
            "compatibility_experiments",
            "timeout_seconds",
            "poll_interval_seconds",
        }
        or not isinstance(server_request, Mapping)
        or set(server_request)
        != {
            "template_mode",
            "template_uri",
            "minecraft_eula_accepted",
            "java_uri",
            "compatibility_experiments",
            "timeout_seconds",
            "shutdown_timeout_seconds",
            "poll_interval_seconds",
        }
    ):
        raise SusyModRunError("retained unified dev-run owner requests drifted")
    expected_request_id = DEV_RUN_REQUEST_ID_PREFIX + sha256(
        _canonical(request)
    ).hexdigest()
    expected_request_suffix = expected_request_id.removeprefix(
        DEV_RUN_REQUEST_ID_PREFIX
    )
    candidate = source.get("candidate")
    pack = source.get("pack")
    if (
        receipt.get("request_id") != expected_request_id
        or not dev_run_id.endswith("-" + expected_request_suffix)
        or not isinstance(candidate, Mapping)
        or not isinstance(pack, Mapping)
        or request.get("source_result_id") != source.get("result_id")
        or request.get("candidate_sha256") != candidate.get("sha256")
        or not isinstance(request.get("requested_side"), str)
        or not isinstance(pack.get("applicable_sides"), list)
        or isinstance(request.get("memory_mib"), bool)
        or not isinstance(request.get("memory_mib"), int)
        or not (1024 <= int(request["memory_mib"]) <= 131072)
        or client_request.get("launcher") not in {"prism", "multimc"}
        or client_request.get("account_mode") not in {"offline", "launcher-profile"}
        or server_request.get("template_mode") not in {"managed", "override"}
        or not isinstance(server_request.get("minecraft_eula_accepted"), bool)
    ):
        raise SusyModRunError("retained unified dev-run request identity drifted")
    for owner_request, timeout_fields in (
        (client_request, ("timeout_seconds", "poll_interval_seconds")),
        (
            server_request,
            (
                "timeout_seconds",
                "shutdown_timeout_seconds",
                "poll_interval_seconds",
            ),
        ),
    ):
        experiments = owner_request.get("compatibility_experiments")
        if (
            not isinstance(experiments, list)
            or any(not isinstance(item, str) or not item for item in experiments)
            or len(experiments) != len(set(experiments))
            or any(
                isinstance(owner_request.get(field), bool)
                or not isinstance(owner_request.get(field), (int, float))
                or not math.isfinite(float(owner_request[field]))
                or float(owner_request[field]) <= 0
                for field in timeout_fields
            )
        ):
            raise SusyModRunError("retained unified dev-run owner policy drifted")
    if (
        (
            client_request.get("account_mode") == "offline"
            and (
                client_request.get("launcher_profile") is not None
                or not isinstance(client_request.get("offline_name"), str)
                or not client_request.get("offline_name")
            )
        )
        or (
            client_request.get("account_mode") == "launcher-profile"
            and (
                client_request.get("launcher_profile") != "explicit-redacted"
                or client_request.get("offline_name") is not None
            )
        )
        or (
            isinstance(selected_request, list)
            and "server" in selected_request
            and server_request.get("template_mode") == "managed"
            and (
                server_request.get("template_uri") is not None
                or server_request.get("minecraft_eula_accepted") is not True
            )
        )
        or (
            isinstance(selected_request, list)
            and "server" in selected_request
            and server_request.get("template_mode") == "override"
            and (
                not isinstance(server_request.get("template_uri"), str)
                or server_request.get("minecraft_eula_accepted") is not False
            )
        )
        or (
            isinstance(selected_request, list)
            and "server" not in selected_request
            and (
                server_request.get("template_mode") != "managed"
                or server_request.get("template_uri") is not None
                or server_request.get("minecraft_eula_accepted") is not False
                or server_request.get("java_uri") is not None
            )
        )
    ):
        raise SusyModRunError("retained unified dev-run owner mode drifted")
    try:
        selected = _selected_sides(
            str(request["requested_side"]), pack["applicable_sides"]
        )
    except SusyModRunError as exc:
        raise SusyModRunError(
            "retained unified dev-run side applicability drifted"
        ) from exc
    if request.get("selected_sides") != list(selected):
        raise SusyModRunError("retained unified dev-run selected sides drifted")

    sides = receipt.get("sides")
    if not isinstance(sides, Mapping) or set(sides) != set(_PHYSICAL_SIDES):
        raise SusyModRunError("retained unified dev-run side records changed")
    applicable = set(pack["applicable_sides"])
    for side in _PHYSICAL_SIDES:
        row = sides.get(side)
        if (
            not isinstance(row, Mapping)
            or set(row) != {"applicable", "selected", "state", "owner", "error"}
            or row.get("applicable") is not (side in applicable)
            or row.get("selected") is not (side in selected)
        ):
            raise SusyModRunError("retained unified dev-run side row drifted")
        owner = row.get("owner")
        error = row.get("error")
        if side not in applicable:
            valid = row.get("state") == "not-applicable" and owner is None and error is None
        elif side not in selected:
            valid = row.get("state") == "not-selected" and owner is None and error is None
        elif isinstance(owner, Mapping):
            valid = (
                row.get("state") == owner.get("outcome")
                and owner.get("outcome") in {"passed", "failed"}
                and error is None
                and isinstance(owner.get("cleanup_safe"), bool)
            )
        else:
            valid = (
                owner is None
                and isinstance(error, Mapping)
                and row.get("state") in {"failed", "not-run"}
            )
        if not valid:
            raise SusyModRunError("retained unified dev-run side state drifted")

    selected_rows = [sides[side] for side in selected]
    passed = all(row.get("state") == "passed" for row in selected_rows)
    contained = all(
        isinstance(row.get("owner"), Mapping)
        and row["owner"].get("cleanup_safe") is True
        for row in selected_rows
    )
    expected_outcome = "passed" if passed and contained else "failed"
    unresolved_candidates: list[str] = []
    for side in selected:
        row = sides[side]
        owner = row.get("owner")
        error = row.get("error")
        if isinstance(owner, Mapping) and owner.get("cleanup_safe") is False:
            unresolved_candidates.append(side)
        elif (
            owner is None
            and isinstance(error, Mapping)
            and error.get("kind") in {"owner-error", "cancelled"}
        ):
            unresolved_candidates.append(side)
    expected_unresolved = (
        unresolved_candidates[0] if len(unresolved_candidates) == 1 else None
    )
    cleanup = receipt.get("cleanup")
    unresolved = cleanup.get("unresolved_side") if isinstance(cleanup, Mapping) else None
    if (
        receipt.get("outcome") != expected_outcome
        or not isinstance(cleanup, Mapping)
        or cleanup.get("all_selected_sides_contained") is not contained
        or len(unresolved_candidates) > 1
        or unresolved != expected_unresolved
        or not isinstance(receipt.get("ended_at"), str)
        or not isinstance(receipt.get("duration_seconds"), (int, float))
        or isinstance(receipt.get("duration_seconds"), bool)
        or float(receipt["duration_seconds"]) < 0
    ):
        raise SusyModRunError("retained unified dev-run outcome semantics drifted")


def _result(
    receipt: Mapping[str, Any], receipt_path: Path, *, reused: bool = False
) -> dict[str, Any]:
    request = receipt.get("request")
    retry = [
        "workbench",
        "dev",
        "run",
        "--run",
        str(receipt.get("run_id")),
        "--side",
        str(request.get("requested_side")) if isinstance(request, Mapping) else "auto",
    ]
    client = request.get("client") if isinstance(request, Mapping) else None
    server = request.get("server") if isinstance(request, Mapping) else None
    selected = request.get("selected_sides") if isinstance(request, Mapping) else None
    action: dict[str, Any] = {
        "id": "run-exact-candidate-again",
        "available": True,
        "argv": retry,
    }
    unavailable_reasons: list[str] = []
    if isinstance(selected, list) and "client" in selected and isinstance(client, Mapping):
        retry.extend(["--launcher", str(client.get("launcher", "prism"))])
        for field, option in (
            ("launcher_executable_uri", "--launcher-executable"),
            ("launcher_root_uri", "--launcher-root"),
            ("java_uri", "--launcher-java"),
            ("java_state_uri", "--launcher-java-state"),
        ):
            value = client.get(field)
            if isinstance(value, str):
                retry.extend([option, str(_file_uri(value, field))])
        if client.get("account_mode") == "launcher-profile":
            unavailable_reasons.append(
                "the launcher profile name is intentionally redacted; select it again"
            )
        elif isinstance(client.get("offline_name"), str):
            retry.extend(["--offline-name", str(client["offline_name"])])
    if isinstance(selected, list) and "server" in selected and isinstance(server, Mapping):
        template = server.get("template_uri")
        if isinstance(template, str):
            retry.extend(["--server-template", str(_file_uri(template, "server template"))])
        elif server.get("minecraft_eula_accepted") is True:
            unavailable_reasons.append(
                "Minecraft EULA acceptance is per invocation; explicitly accept it again"
            )
        java = server.get("java_uri")
        if isinstance(java, str):
            retry.extend(["--server-java", str(_file_uri(java, "server Java"))])
    experiments: list[str] = []
    for owner in (client, server):
        values = owner.get("compatibility_experiments") if isinstance(owner, Mapping) else None
        if isinstance(values, list):
            for value in values:
                if isinstance(value, str) and value not in experiments:
                    experiments.append(value)
    for experiment in experiments:
        retry.extend(["--runtime-experiment", experiment])
    memory = request.get("memory_mib") if isinstance(request, Mapping) else None
    if isinstance(memory, int):
        retry.extend(["--memory", str(memory)])
    client_timeout = client.get("timeout_seconds") if isinstance(client, Mapping) else None
    server_timeout = server.get("timeout_seconds") if isinstance(server, Mapping) else None
    selected_set = set(selected) if isinstance(selected, list) else set()
    active_timeouts = [
        value
        for side, value in (("client", client_timeout), ("server", server_timeout))
        if side in selected_set
    ]
    if active_timeouts and len(set(active_timeouts)) == 1:
        retry.extend(["--launch-timeout", str(active_timeouts[0])])
    elif active_timeouts:
        unavailable_reasons.append("the retained sides used different launch timeouts")
    if "server" in selected_set and isinstance(server, Mapping):
        retry.extend(
            ["--shutdown-timeout", str(server.get("shutdown_timeout_seconds"))]
        )
    expected_poll = {"client": 1.0, "server": 0.25}
    for side_name, owner in (("client", client), ("server", server)):
        if side_name not in selected_set or not isinstance(owner, Mapping):
            continue
        value = owner.get("poll_interval_seconds")
        if value != expected_poll[side_name]:
            unavailable_reasons.append(
                f"the retained {side_name} side used a non-CLI poll interval"
            )
    cleanup = receipt.get("cleanup")
    if not isinstance(cleanup, Mapping) or cleanup.get(
        "all_selected_sides_contained"
    ) is not True:
        unavailable_reasons.append(
            "the prior attempt did not complete and contain every selected side; "
            "review its custody and cleanup evidence before starting new work"
        )
    if unavailable_reasons:
        action["available"] = False
        action["argv"] = None
        action["reason"] = "; ".join(unavailable_reasons)
    return {
        "format": DEV_RUN_RESULT_FORMAT,
        "schema_version": 1,
        "outcome": receipt.get("outcome"),
        "dev_run_id": receipt.get("dev_run_id"),
        "receipt": deepcopy(dict(receipt)),
        "receipt_uri": receipt_path.as_uri(),
        "reused": reused,
        "next_actions": [action],
    }


def run_susy_mod(
    suite_root: Path | str,
    run_id: str,
    *,
    side: str = "auto",
    launcher: str = "prism",
    launcher_executable: Path | str | None = None,
    launcher_root: Path | str | None = None,
    launcher_profile: str | None = None,
    launcher_java: Path | str | None = None,
    launcher_java_state: Path | str | None = None,
    client_compatibility_experiments: Sequence[str] = (),
    server_template: Path | str | None = None,
    server_java: Path | str | None = None,
    accept_minecraft_eula: bool = False,
    server_compatibility_experiments: Sequence[str] = (),
    memory_mib: int = 8192,
    offline_name: str = "Workbench",
    client_timeout_seconds: float = 600.0,
    server_timeout_seconds: float = 600.0,
    shutdown_timeout_seconds: float = 180.0,
    client_poll_interval_seconds: float = 1.0,
    server_poll_interval_seconds: float = 0.25,
) -> dict[str, Any]:
    """Run one retained candidate on its selected applicable physical sides."""

    suite = Path(suite_root).resolve()
    run_root, _source_result_value, source = _source_result(suite, run_id)
    applicable = tuple(source["pack"]["applicable_sides"])
    selected = _selected_sides(side, applicable)
    client_experiments = tuple(client_compatibility_experiments)
    server_experiments = tuple(server_compatibility_experiments)
    _validate_inputs(
        selected=selected,
        launcher=launcher,
        launcher_executable=launcher_executable,
        launcher_root=launcher_root,
        launcher_profile=launcher_profile,
        launcher_java=launcher_java,
        launcher_java_state=launcher_java_state,
        client_experiments=client_experiments,
        server_template=server_template,
        server_java=server_java,
        accept_minecraft_eula=accept_minecraft_eula,
        server_experiments=server_experiments,
        memory_mib=memory_mib,
        client_timeout_seconds=client_timeout_seconds,
        server_timeout_seconds=server_timeout_seconds,
        shutdown_timeout_seconds=shutdown_timeout_seconds,
        client_poll_interval_seconds=client_poll_interval_seconds,
        server_poll_interval_seconds=server_poll_interval_seconds,
    )
    _preflight_owners(
        suite,
        run_id,
        selected,
        managed_server="server" in selected and server_template is None,
    )
    request = _request_projection(
        source=source,
        requested_side=side,
        selected_sides=selected,
        launcher=launcher,
        launcher_executable=launcher_executable,
        launcher_root=launcher_root,
        launcher_profile=launcher_profile,
        launcher_java=launcher_java,
        launcher_java_state=launcher_java_state,
        client_experiments=client_experiments,
        server_template=server_template,
        server_java=server_java,
        accept_minecraft_eula=accept_minecraft_eula,
        server_experiments=server_experiments,
        memory_mib=memory_mib,
        offline_name=offline_name,
        client_timeout_seconds=client_timeout_seconds,
        server_timeout_seconds=server_timeout_seconds,
        shutdown_timeout_seconds=shutdown_timeout_seconds,
        client_poll_interval_seconds=client_poll_interval_seconds,
        server_poll_interval_seconds=server_poll_interval_seconds,
    )
    request_id = DEV_RUN_REQUEST_ID_PREFIX + sha256(_canonical(request)).hexdigest()
    runner = _runner_identity()
    started = datetime.now(timezone.utc)
    stamp = started.strftime("%Y%m%dT%H%M%S%fZ")
    dev_run_id = (
        "workbench-susy-dev-run-"
        + str(source["candidate"]["sha256"])[:12]
        + "-"
        + stamp
        + "-"
        + request_id.removeprefix(DEV_RUN_REQUEST_ID_PREFIX)
    )
    runtime_root = run_root / "runtime"
    if not runtime_root.exists() and not runtime_root.is_symlink():
        try:
            runtime_root.mkdir(mode=0o700)
        except OSError as exc:
            raise SusyModRunError("cannot create retained runtime directory") from exc
    runtime_root = _regular_directory(runtime_root, "retained runtime directory")
    if runtime_root.parent != run_root:
        raise SusyModRunError("retained runtime directory escapes its run")
    attempts = _ensure_child_directory(runtime_root, "dev-runs")
    attempt_root = attempts / dev_run_id
    try:
        attempt_root.mkdir(mode=0o700)
    except OSError as exc:
        raise SusyModRunError("unified dev-run attempt already exists") from exc
    receipt_path = attempt_root / "susy-mod-dev-run-v1.json"
    lock_path = attempt_root / ".lock"
    try:
        with lock_path.open("x", encoding="ascii") as stream:
            stream.write(str(os.getpid()) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise SusyModRunError("unified dev-run attempt is already active") from exc

    receipt: dict[str, Any] = {
        "format": DEV_RUN_RECEIPT_FORMAT,
        "schema_version": 1,
        "dev_run_id": dev_run_id,
        "request_id": request_id,
        "run_id": run_id,
        "state": "running",
        "outcome": None,
        "started_at": started.isoformat(),
        "updated_at": started.isoformat(),
        "ended_at": None,
        "source": deepcopy(source),
        "runner": runner,
        "request": request,
        "sides": _side_rows(applicable, selected),
        "cleanup": {
            "all_selected_sides_contained": False,
            "unresolved_side": None,
        },
        "limitations": [
            "This joins independent applicable-side load/readiness/cleanup smokes; it is not a connected multiplayer session.",
            "Two passing sides do not establish behavioral parity, gameplay correctness, pack health, stable support, or release qualification.",
            "Build, Packwiz materialization, loaded-source proof, process custody, and cleanup remain owned by their referenced receipts.",
        ],
    }

    def persist() -> None:
        receipt["updated_at"] = _now()
        receipt.pop("receipt_id", None)
        receipt["receipt_id"] = DEV_RUN_ID_PREFIX + sha256(
            _canonical(receipt)
        ).hexdigest()
        _write_json(receipt_path, receipt)

    try:
        persist()
        stop_after: str | None = None
        unresolved_side: str | None = None
        for physical_side in _PHYSICAL_SIDES:
            row = receipt["sides"][physical_side]
            if not row["selected"]:
                continue
            if stop_after is not None:
                row["state"] = "not-run"
                row["error"] = {
                    "kind": (
                        "unresolved-custody"
                        if unresolved_side is not None
                        else "prior-side-cancelled"
                    ),
                    "detail": (
                        f"blocked by unresolved {stop_after} custody"
                        if unresolved_side is not None
                        else f"not started after {stop_after} cancellation"
                    ),
                }
                persist()
                continue
            if not _source_unchanged(source):
                row["state"] = "failed"
                row["error"] = {
                    "kind": "input-drift",
                    "detail": "retained source result changed between physical sides",
                }
                stop_after = physical_side
                persist()
                continue
            row["state"] = "running"
            persist()
            try:
                if physical_side == "client":
                    child = launch_susy_mod_client(
                        suite,
                        run_id,
                        launcher=launcher,
                        launcher_executable=launcher_executable,
                        launcher_root=launcher_root,
                        launcher_profile=launcher_profile,
                        launcher_java=launcher_java,
                        launcher_java_state=launcher_java_state,
                        compatibility_experiments=client_experiments,
                        memory_mib=memory_mib,
                        offline_name=offline_name,
                        timeout_seconds=client_timeout_seconds,
                        poll_interval_seconds=client_poll_interval_seconds,
                    )
                else:
                    child = launch_susy_mod_server(
                        suite,
                        run_id,
                        server_template=server_template,
                        server_java=server_java,
                        accept_minecraft_eula=accept_minecraft_eula,
                        compatibility_experiments=server_experiments,
                        memory_mib=memory_mib,
                        timeout_seconds=server_timeout_seconds,
                        shutdown_timeout_seconds=shutdown_timeout_seconds,
                        poll_interval_seconds=server_poll_interval_seconds,
                    )
                reference = _child_reference(
                    physical_side,
                    child,
                    run_id=run_id,
                    run_root=run_root,
                )
                row["owner"] = reference
                row["state"] = reference["outcome"]
                if not reference["cleanup_safe"]:
                    stop_after = physical_side
                    unresolved_side = physical_side
                elif reference.get("failure_kind") == "cancelled":
                    stop_after = physical_side
            except (OSError, ValueError, SusyModLaunchError, SusyModServerError, SusyModRunError) as exc:
                row["state"] = "failed"
                row["error"] = {
                    "kind": "owner-error",
                    "detail": str(exc),
                }
                stop_after = physical_side
                unresolved_side = physical_side
            except KeyboardInterrupt:
                row["state"] = "failed"
                row["error"] = {
                    "kind": "cancelled",
                    "detail": "developer interrupted the active physical side",
                }
                stop_after = physical_side
                unresolved_side = physical_side
            except Exception as exc:
                row["state"] = "failed"
                row["error"] = {
                    "kind": "owner-error",
                    "detail": f"unexpected owner failure: {exc}",
                }
                stop_after = physical_side
                unresolved_side = physical_side
            persist()

        selected_rows = [receipt["sides"][name] for name in selected]
        passed = all(row["state"] == "passed" for row in selected_rows)
        contained = all(
            isinstance(row.get("owner"), Mapping)
            and row["owner"].get("cleanup_safe") is True
            for row in selected_rows
        )
        receipt["state"] = "complete"
        receipt["outcome"] = "passed" if passed and contained else "failed"
        ended = datetime.now(timezone.utc)
        receipt["ended_at"] = ended.isoformat()
        receipt["duration_seconds"] = round((ended - started).total_seconds(), 3)
        receipt["cleanup"] = {
            "all_selected_sides_contained": contained,
            "unresolved_side": unresolved_side,
        }
        persist()
        return _result(receipt, receipt_path)
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass


def reopen_susy_mod_run(
    suite_root: Path | str, run_id: str, dev_run_id: str
) -> dict[str, Any]:
    """Reopen and revalidate one exact retained unified dev-run receipt."""

    if _DEV_RUN_ID_RE.fullmatch(dev_run_id) is None:
        raise SusyModRunError("reopen requires an exact retained dev-run ID")
    suite = Path(suite_root).resolve()
    run_root, _result_value, source = _source_result(suite, run_id)
    runtime_root = _regular_directory(
        run_root / "runtime", "retained runtime directory"
    )
    if runtime_root.parent != run_root:
        raise SusyModRunError("retained runtime directory escapes its source run")
    attempts = _regular_directory(
        runtime_root / "dev-runs", "retained dev-run evidence directory"
    )
    if attempts.parent != runtime_root:
        raise SusyModRunError("retained dev-run evidence escapes its source run")
    attempt = attempts / dev_run_id
    attempt = _regular_directory(attempt, "retained dev-run attempt")
    if attempt.parent != attempts:
        raise SusyModRunError("retained dev-run attempt escapes its source run")
    receipt_path = attempt / "susy-mod-dev-run-v1.json"
    receipt = _read_json(receipt_path, "retained unified dev-run receipt")
    _validate_completed_receipt(
        receipt,
        source=source,
        run_id=run_id,
        dev_run_id=dev_run_id,
    )
    sides = receipt.get("sides")
    assert isinstance(sides, Mapping)
    owner_receipts: dict[str, dict[str, Any]] = {}
    for side, row in sides.items():
        assert isinstance(row, Mapping)
        owner = row.get("owner")
        if isinstance(owner, Mapping):
            owner_receipts[side] = _verify_reference(
                owner, run_root, side=side, run_id=run_id
            )
    request = receipt.get("request")
    assert isinstance(request, Mapping)
    for side, owner_receipt in owner_receipts.items():
        _validate_request_owner_binding(
            side,
            request=request,
            owner_receipt=owner_receipt,
        )
    if not _source_unchanged(source):
        raise SusyModRunError("retained source result changed after the dev run")
    return _result(receipt, receipt_path, reused=True)


def render_susy_mod_run(
    result: Mapping[str, Any], *, json_output: bool = False
) -> str:
    if json_output:
        return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    receipt = result.get("receipt")
    if not isinstance(receipt, Mapping) or not _verify_receipt_identity(receipt):
        raise SusyModRunError("unified dev-run result lacks an exact receipt")
    source = receipt.get("source")
    request = receipt.get("request")
    sides = receipt.get("sides")
    if not all(isinstance(value, Mapping) for value in (source, request, sides)):
        raise SusyModRunError("unified dev-run receipt is incomplete")
    assert isinstance(source, Mapping)
    assert isinstance(request, Mapping)
    assert isinstance(sides, Mapping)
    _validate_completed_receipt(
        receipt,
        source=source,
        run_id=str(receipt.get("run_id")),
        dev_run_id=str(receipt.get("dev_run_id")),
    )
    if result.get("outcome") != receipt.get("outcome"):
        raise SusyModRunError("unified dev-run result outcome drifted")
    candidate = source.get("candidate")
    lines = [
        f"SUSY applicable-side dev run: {result.get('outcome')}",
        f"Source run: {receipt.get('run_id')}",
        f"Dev run: {receipt.get('dev_run_id')}",
        "Candidate: "
        + (
            f"{candidate.get('sha256')} · {', '.join(candidate.get('mod_ids', []))}"
            if isinstance(candidate, Mapping)
            else "unavailable"
        ),
        "Packwiz applicability: "
        + ", ".join(str(value) for value in source.get("pack", {}).get("applicable_sides", [])),
        "Selected sides: " + ", ".join(str(value) for value in request.get("selected_sides", [])),
    ]
    for physical_side, label in (("client", "Client"), ("server", "Server")):
        row = sides.get(physical_side)
        state = row.get("state") if isinstance(row, Mapping) else "invalid"
        lines.append(f"{label} independent candidate smoke: {state}")
        if isinstance(row, Mapping) and isinstance(row.get("owner"), Mapping):
            lines.append(f"  Owner receipt: {row['owner'].get('receipt_uri')}")
        if isinstance(row, Mapping) and isinstance(row.get("error"), Mapping):
            lines.append(f"  Error: {row['error'].get('detail')}")
    lines.extend(
        [
            "Client/server parity: not claimed",
            "Cleanup: "
            + (
                "complete"
                if receipt.get("cleanup", {}).get("all_selected_sides_contained")
                else "incomplete"
            ),
            f"Receipt: {result.get('receipt_uri')}",
        ]
    )
    return "\n".join(lines) + "\n"
