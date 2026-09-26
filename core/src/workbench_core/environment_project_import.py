"""Reviewed project-byte import for portable exact source locks.

This binds one exact Git commit and tree to a Core-managed input. Earlier
environment import receipts remain local bindings with unresolved project bytes.
"""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping, Sequence

from .configuration import CONFIGURATION_PATH
from .durable_records import read_bounded_bytes, read_private_bytes
from .environment_resolution import resolve_environment
from .environment_reconstruction import (
    ReconstructionError, SHARE_FORMAT_V2, SHARE_FORMAT_V3, _canonical, _host_variant,
    _resource_host, _seal, plan_import, validate_share,
)
from .host_filesystem import private_path, secure_private_path
from .output_routing import _private_directory
from .runtime_java import host_platform
from .source_checkouts import CoreSourceCheckouts


PLAN_FORMAT = "workbench-environment-project-import-plan-v1"
PLAN_FORMAT_V2 = "workbench-environment-project-import-plan-v2"
RESULT_FORMAT = "workbench-environment-project-import-result-v1"
RESULT_FORMAT_V2 = "workbench-environment-project-import-result-v2"
ACQUISITION_FORMAT = "workbench-environment-project-acquisition-v1"
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
_MAX_GIT_BYTES = 64 * 1024 * 1024


def _required_paths(values: Sequence[str]) -> list[str]:
    if not isinstance(values, (tuple, list)) or not 1 <= len(values) <= 64:
        raise ReconstructionError("project import needs reviewed required paths")
    result: list[str] = []
    for value in values:
        if type(value) is not str or not value or "\\" in value or any(
            character in value for character in ":\0\r\n"
        ):
            raise ReconstructionError("project required path is not portable")
        path = PurePosixPath(value)
        if (path.is_absolute() or path.as_posix() != value or ".git" in path.parts
                or any(part in {"", ".", ".."} for part in path.parts)):
            raise ReconstructionError("project required path is not portable")
        result.append(value)
    if len(set(result)) != len(result):
        raise ReconstructionError("project required paths are repeated")
    return sorted(result, key=lambda value: value.encode("utf-8"))


def _git_binding(value: Path | str) -> dict[str, str]:
    selected = Path(value).expanduser()
    if not selected.is_absolute():
        raise ReconstructionError("selected Git executable must be absolute")
    for ancestor in (selected, *selected.parents):
        if ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)():
            raise ReconstructionError("selected Git executable traverses a redirect")
    try:
        info = selected.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_GIT_BYTES
                or not os.access(selected, os.X_OK)):
            raise ReconstructionError("selected Git executable is unavailable")
        payload = read_bounded_bytes(selected, byte_limit=_MAX_GIT_BYTES)
    except OSError as exc:
        raise ReconstructionError("selected Git executable is unavailable") from exc
    return {"path": str(selected), "sha256": "sha256:" + sha256(payload).hexdigest()}


def _transport_binding(value: str, repository: str) -> dict[str, Any]:
    if type(value) is not str or not value or value.startswith("-"):
        raise ReconstructionError("project acquisition transport is invalid")
    if value == repository:
        return {"kind": "locked-https", "uri": value, "device": None, "inode": None}
    selected = Path(value)
    if not selected.is_absolute():
        raise ReconstructionError("project mirror must be an explicit absolute local path")
    for ancestor in (selected, *selected.parents):
        if ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)():
            raise ReconstructionError("project mirror traverses a redirect")
    try:
        info = selected.lstat()
    except OSError as exc:
        raise ReconstructionError("project mirror is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise ReconstructionError("project mirror is not an ordinary directory")
    return {
        "kind": "local-mirror", "uri": str(selected),
        "device": info.st_dev, "inode": info.st_ino,
    }


def _managed_destination(state_root: Path, source_sha256: str) -> Path:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", source_sha256):
        raise ReconstructionError("project source-lock digest is invalid")
    return state_root / "project-inputs" / source_sha256[7:] / "checkout"


def _git_environment(values: Mapping[str, str]) -> dict[str, str]:
    """Keep caller Git overrides out of the reviewed acquisition."""

    return {
        **{key: value for key, value in values.items() if not key.startswith("GIT_")},
        "GIT_CONFIG_GLOBAL": os.devnull,
    }


def _acquisition_receipt(
    share: Mapping[str, Any], *, destination: Path, git: Mapping[str, str],
    transport: Mapping[str, Any], branch: str, required_paths: list[str],
    host: Mapping[str, str],
) -> dict[str, Any]:
    return _seal({
        "format": ACQUISITION_FORMAT, "schema_version": 1,
        "share_id": share["share_id"], "lock_id": share["lock"]["lock_id"],
        "project_source_lock": share["lock"]["project_source_lock"],
        "destination": str(destination), "git": dict(git),
        "transport": dict(transport), "branch": branch,
        "required_paths": required_paths, "host_variant": dict(host),
    }, "workbench-project-acquisition", "receipt_id")


def _receipt_path(state_root: Path, receipt_id: str) -> Path:
    return state_root / "evidence/project-acquisition" / f"{receipt_id.rsplit(':', 1)[-1]}.json"


def _verify_required(root: Path, required: Sequence[str]) -> None:
    for relative in required:
        cursor = root
        for part in PurePosixPath(relative).parts:
            cursor /= part
            try:
                info = cursor.lstat()
            except OSError as exc:
                raise ReconstructionError(f"required project path is unavailable: {relative}") from exc
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ReconstructionError(f"required project path is unsafe: {relative}")


def _verify_owned_checkout(
    destination: Path, receipt_path: Path, receipt_bytes: bytes,
    *, git: Mapping[str, str], lock: Mapping[str, str],
    environment: Mapping[str, str], required: Sequence[str],
) -> None:
    try:
        for directory in (
            destination, destination.parent, destination.parent.parent,
            destination.parent.parent.parent, receipt_path.parent,
            receipt_path.parent.parent,
        ):
            if not private_path(directory, directory=True):
                raise ReconstructionError("managed project input store is no longer private")
        if read_private_bytes(receipt_path, byte_limit=len(receipt_bytes)) != receipt_bytes:
            raise ReconstructionError("managed project acquisition receipt changed")
        CoreSourceCheckouts().verify_exact(
            destination, git_executable=git["path"],
            expected_commit=lock["revision"], expected_tree=lock["tree"],
            environment=environment,
        )
        _verify_required(destination, required)
    except (OSError, ValueError) as exc:
        if isinstance(exc, ReconstructionError):
            raise
        raise ReconstructionError(f"managed project input cannot be reopened exactly: {exc}") from exc


def plan_project_import(
    suite_root: Path, share: Mapping[str, Any], *, workspace_name: str,
    workspace: Path | str, git_executable: Path | str, transport: str,
    branch: str, required_paths: Sequence[str], timeout_seconds: float = 300,
    config_path: Path | str = CONFIGURATION_PATH,
    java_home: Path | str | None = None, acquire_managed_java: bool = False,
    environment: Mapping[str, str] | None = None,
    host: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Review one exact project acquisition without writing or contacting a remote."""

    portable = validate_share(dict(share))
    if portable["format"] not in {SHARE_FORMAT_V2, SHARE_FORMAT_V3}:
        raise ReconstructionError("project-byte import requires a share with an exact source lock")
    tool_bound = portable["format"] == SHARE_FORMAT_V3
    if (type(branch) is not str or _BRANCH.fullmatch(branch) is None
            or ".." in branch or branch.endswith("/")):
        raise ReconstructionError("project acquisition branch is invalid")
    if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 3600:
        raise ReconstructionError("project acquisition timeout is invalid")
    required = _required_paths(required_paths)
    values = dict(os.environ if environment is None else environment)
    git_environment = _git_environment(values)
    base = plan_import(
        suite_root, portable, workspace_name=workspace_name, workspace=workspace,
        config_path=config_path, java_home=java_home,
        acquire_managed_java=acquire_managed_java, environment=values, host=host,
    )
    selected_host = _host_variant(host_platform() if host is None else host)
    lock = portable["lock"]["project_source_lock"]
    git = _git_binding(git_executable)
    selected_transport = _transport_binding(transport, lock["repository"])
    resolution = resolve_environment(
        suite_root, workspace=Path(base["workspace"]), environment=values,
    )
    destination = _managed_destination(resolution.state_root, lock["sha256"])
    receipt = _acquisition_receipt(
        portable, destination=destination, git=git, transport=selected_transport,
        branch=branch, required_paths=required, host=selected_host,
    )
    receipt_path = _receipt_path(resolution.state_root, receipt["receipt_id"])
    blockers = list(base["blockers"])
    if selected_host != _host_variant(host_platform()):
        blockers.append("project acquisition host differs from the executing host")
    present = destination.exists() or destination.is_symlink()
    receipt_present = receipt_path.exists() or receipt_path.is_symlink()
    action = "acquire"
    if present != receipt_present:
        blockers.append("managed project input and acquisition receipt are incomplete; recovery is required")
    elif present:
        try:
            _verify_owned_checkout(
                destination, receipt_path, _canonical(receipt) + b"\n",
                git=git, lock=lock, environment=git_environment, required=required,
            )
            action = "reuse"
        except ReconstructionError as exc:
            blockers.append(str(exc))
    body = {
        "format": PLAN_FORMAT_V2 if tool_bound else PLAN_FORMAT,
        "schema_version": 2 if tool_bound else 1,
        "share_id": portable["share_id"], "lock_id": portable["lock"]["lock_id"],
        "base_plan_id": base["plan_id"], "workspace": base["workspace"],
        "workspace_name": workspace_name, "environment_resolution_id": base["environment_resolution_id"],
        "host_variant": selected_host, "project_source_lock": lock,
        "git": git, "transport": selected_transport, "branch": branch,
        "required_paths": required, "timeout_seconds": timeout_seconds,
        "state_root": str(resolution.state_root), "managed_destination": str(destination),
        "acquisition_receipt_id": receipt["receipt_id"],
        "action": action, "blockers": blockers,
        "state": "blocked" if blockers else "ready",
        "unresolved_inputs": list(base["unresolved_inputs"]),
    }
    if tool_bound:
        body["managed_tool_lock"] = portable["lock"]["managed_tool_lock"]
    return _seal(body, "workbench-environment-project-import-plan", "plan_id")


def apply_project_import(
    suite_root: Path, share: Mapping[str, Any], *, expected_plan_id: str,
    workspace_name: str, workspace: Path | str, git_executable: Path | str,
    transport: str, branch: str, required_paths: Sequence[str],
    timeout_seconds: float = 300,
    config_path: Path | str = CONFIGURATION_PATH,
    java_home: Path | str | None = None, acquire_managed_java: bool = False,
    environment: Mapping[str, str] | None = None,
    host: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Acquire or reuse the approved managed project bytes under Core custody."""

    values = dict(os.environ if environment is None else environment)
    git_environment = _git_environment(values)
    portable = validate_share(dict(share))
    plan = plan_project_import(
        suite_root, portable, workspace_name=workspace_name, workspace=workspace,
        git_executable=git_executable, transport=transport, branch=branch,
        required_paths=required_paths, timeout_seconds=timeout_seconds,
        config_path=config_path, java_home=java_home,
        acquire_managed_java=acquire_managed_java, environment=values, host=host,
    )
    if type(expected_plan_id) is not str or expected_plan_id != plan["plan_id"]:
        raise ReconstructionError("project import plan changed after review")
    if plan["state"] != "ready":
        raise ReconstructionError("project import is blocked: " + "; ".join(plan["blockers"]))
    service = _resource_host(Path(suite_root), Path(plan["workspace"]), values)
    if service.policy_id != plan["environment_resolution_id"]:
        raise ReconstructionError("project import resolution changed after review")
    prepared = {
        "format": ("workbench-environment-project-import-attempt-v2"
                   if portable["format"] == SHARE_FORMAT_V3
                   else "workbench-environment-project-import-attempt-v1"),
        "schema_version": 2 if portable["format"] == SHARE_FORMAT_V3 else 1,
        "state": "prepared", "plan_id": plan["plan_id"],
        "share_id": plan["share_id"], "project_source_lock": plan["project_source_lock"],
        "managed_destination": plan["managed_destination"],
    }
    if portable["format"] == SHARE_FORMAT_V3:
        prepared["managed_tool_lock"] = plan["managed_tool_lock"]
    prepared_ref = service.publish_bytes(
        "evidence", "environment-project-import-attempt.json",
        _canonical(prepared) + b"\n", domain_id=plan["share_id"],
    )
    # A concurrent change after the durable attempt must invalidate the plan.
    admitted = plan_project_import(
        suite_root, portable, workspace_name=workspace_name, workspace=workspace,
        git_executable=git_executable, transport=transport, branch=branch,
        required_paths=required_paths, timeout_seconds=timeout_seconds,
        config_path=config_path, java_home=java_home,
        acquire_managed_java=acquire_managed_java, environment=values, host=host,
    )
    if admitted["plan_id"] != plan["plan_id"] or admitted["state"] != "ready":
        raise ReconstructionError("project import inputs changed after prepared attempt")
    state_root = Path(plan["state_root"])
    destination = Path(plan["managed_destination"])
    receipt = _acquisition_receipt(
        portable, destination=destination, git=plan["git"], transport=plan["transport"],
        branch=branch, required_paths=plan["required_paths"], host=plan["host_variant"],
    )
    receipt_bytes = _canonical(receipt) + b"\n"
    receipt_path = _receipt_path(state_root, receipt["receipt_id"])
    if plan["action"] == "acquire":
        try:
            for directory in (state_root, destination.parent):
                _private_directory(directory)
                secure_private_path(directory, directory=True)
                if not private_path(directory, directory=True):
                    raise ReconstructionError("managed project input store is not private")
        except (OSError, ValueError) as exc:
            if isinstance(exc, ReconstructionError):
                raise
            raise ReconstructionError("managed project input store cannot enforce private custody") from exc
        checkout = CoreSourceCheckouts().open(
            destination, git_executable=plan["git"]["path"],
            remote_url=plan["transport"]["uri"], checkout_branch=branch,
            expected_commit=plan["project_source_lock"]["revision"],
            expected_tree=plan["project_source_lock"]["tree"],
            environment=git_environment, timeout_seconds=timeout_seconds,
        )
        try:
            CoreSourceCheckouts().verify_exact(
                checkout.staging_root, git_executable=plan["git"]["path"],
                expected_commit=plan["project_source_lock"]["revision"],
                expected_tree=plan["project_source_lock"]["tree"],
                environment=git_environment,
            )
            _verify_required(checkout.staging_root, plan["required_paths"])
            current = plan_project_import(
                suite_root, portable, workspace_name=workspace_name, workspace=workspace,
                git_executable=git_executable, transport=transport, branch=branch,
                required_paths=required_paths, timeout_seconds=timeout_seconds,
                config_path=config_path, java_home=java_home,
                acquire_managed_java=acquire_managed_java, environment=values, host=host,
            )
            if current["plan_id"] != plan["plan_id"] or current["state"] != "ready":
                raise ReconstructionError("project import inputs changed during acquisition")
            checkout.publish(
                state_root=state_root, receipt_id=receipt["receipt_id"],
                receipt_bytes=receipt_bytes, byte_limit=256 * 1024,
            )
        finally:
            checkout.close()
    _verify_owned_checkout(
        destination, receipt_path, receipt_bytes,
        git=plan["git"], lock=plan["project_source_lock"],
        environment=git_environment, required=plan["required_paths"],
    )
    result = {
        "format": RESULT_FORMAT_V2 if portable["format"] == SHARE_FORMAT_V3 else RESULT_FORMAT,
        "schema_version": 2 if portable["format"] == SHARE_FORMAT_V3 else 1,
        "outcome": "acquired" if plan["action"] == "acquire" else "reused",
        "plan_id": plan["plan_id"], "share_id": plan["share_id"],
        "attempt_resource_id": prepared_ref.resource_id,
        "project_source_lock": plan["project_source_lock"],
        "managed_destination": str(destination),
        "acquisition_receipt": {
            "receipt_id": receipt["receipt_id"], "path": str(receipt_path),
            "sha256": "sha256:" + sha256(receipt_bytes).hexdigest(),
        },
        "unresolved_inputs": [
            item for item in plan["unresolved_inputs"] if item != "workspace-project-bytes"
        ],
        "scope": "Exact managed pack project bytes only; packages, fixtures, tools and Java remain separate inputs.",
    }
    if portable["format"] == SHARE_FORMAT_V3:
        result["managed_tool_lock"] = plan["managed_tool_lock"]
    payload = _canonical(result) + b"\n"
    completed = _resource_host(Path(suite_root), Path(plan["workspace"]), values)
    reference = completed.publish_bytes(
        "evidence", "environment-project-import.json", payload,
        domain_id=plan["share_id"], references=(prepared_ref.resource_id,),
    )
    if completed.read_bytes(reference.resource_id) != payload:
        raise ReconstructionError("project import result did not reopen exactly")
    return {
        **result,
        "resource": {
            "resource_id": reference.resource_id, "store_id": reference.store_id,
            "path": str(reference.path), "sha256": reference.sha256,
        },
    }


__all__ = ["plan_project_import", "apply_project_import"]
