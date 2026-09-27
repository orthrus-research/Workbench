"""Core-owned Linux projection of a retained release into a fresh Prism instance.

The launcher owns the instance after publication and may change it during play.
Core retains the exact composition, bootstrap bytes, and initial install receipt;
normal launcher writes never become drift in the immutable source catalog.
"""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, Mapping
from urllib.parse import urlparse, unquote
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from workbench_api.host_filesystem import DurableRecordError

from .durable_files import _directory as pinned_directory
from .durable_records import private_record_lock, read_bounded_bytes, read_private_single_link_bytes
from .environment_package_install_plan import _SUPPORTED_FILESYSTEMS, _mount_type
from .host_filesystem import (
    count_prepared_directory_stages, private_path, promote_prepared_directory,
    publish_create_once_bytes,
)
from .managed_trees import CoreManagedTrees, _rename_no_replace
from .output_routing import _private_directory
from .pack_release_client_composition import LOCK_FORMAT as COMPOSITION_LOCK_FORMAT
from .pack_release_client_composition import PLAN_FORMAT as COMPOSITION_PLAN_FORMAT
from .pack_release_client_layout import _relative
from .pack_release_local import _canonical, _held_file, _object
from .pack_release_prism_zip_composition import source_platform_from_manifest
from .storage.exact_tree_inventory import (
    EXACT_INVENTORY_POLICY, MAX_FILE_BYTES, exact_content_sha256,
    inventory_exact_members,
)
from .tooling_provision import ToolingProvisionError, inspect_tools


POLICY_FORMAT = "workbench-supersymmetry-release-client-install-policy-v1"
PLAN_FORMAT = "workbench-pack-release-client-install-plan-v1"
RECEIPT_FORMAT = "workbench-pack-release-client-install-receipt-v1"
_PLAN_PREFIX = "workbench-pack-release-client-install-plan:sha256:"
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CHUNK = 1024 * 1024
_MAX_BOOTSTRAP = 64 * 1024 * 1024
_MAX_BOOTSTRAP_FILES = 10000
_MARKER = ".workbench-release-install.json"
_STAGE_INTENT = ".workbench-release-install-intent.json"
_RECEIPT_LIMIT = 64 * 1024
_ROOT_PLAN_FORMAT = "workbench-prism-data-root-plan-v1"
_ROOT_PLAN_PREFIX = "workbench-prism-data-root-plan:sha256:"
_ROOT_MARKER = ".workbench-prism-data-root.json"
_ROOT_STAGE_INTENT = ".workbench-prism-root-intent.json"
_USER_COMPOSITION_RESULT_FORMAT = "workbench-pack-release-client-composition-result-v3"
_USER_COMPOSITION_LOCK_FORMAT = "workbench-pack-release-client-composition-source-lock-v3"
_USER_COMPOSITION_PLAN_FORMAT = "workbench-pack-release-client-composition-plan-v3"
_FRESH_COMPOSITION_RESULT_FORMAT = "workbench-pack-release-client-composition-result-v4"
_FRESH_COMPOSITION_LOCK_FORMAT = "workbench-pack-release-client-composition-source-lock-v4"
_FRESH_COMPOSITION_PLAN_FORMAT = "workbench-pack-release-client-composition-plan-v4"


def load_release_install_policy(path: Path) -> dict[str, Any]:
    policy = _object(read_bounded_bytes(path, byte_limit=16384), "release install policy")
    expected = {"format", "schema_version", "profile",
                "minecraft_version", "launcher", "platform_profile_id",
                "cleanroom_version", "cleanroom_client", "recommended_java_feature", "memory_mib",
                "accepted_source_limitations"}
    archive = policy.get("cleanroom_client")
    if (set(policy) != expected or policy["format"] != POLICY_FORMAT
            or policy["schema_version"] != 1 or policy["profile"] != "supersymmetry"
            or policy["launcher"] != "prism" or policy["minecraft_version"] != "1.12.2"
            or policy["platform_profile_id"] != "workbench-platform:cleanroom:provisional"
            or policy["cleanroom_version"] != "0.6.12-alpha"
            or type(archive) is not dict or set(archive) != {"size", "sha256"}
            or type(archive["size"]) is not int or not 0 < archive["size"] <= _MAX_BOOTSTRAP
            or type(archive["sha256"]) is not str or _SHA256.fullmatch(archive["sha256"]) is None
            or policy["recommended_java_feature"] != 25 or type(policy["memory_mib"]) is not int
            or not 1024 <= policy["memory_mib"] <= 131072
            or policy["accepted_source_limitations"] != [
                "external-file-origin-unverified", "runtime-compatibility-unqualified"]):
        raise ValueError("Supersymmetry release install policy is incompatible")
    return policy


def _host(state_root: Path, config_home: Path, policy_id: str) -> CoreManagedTrees:
    if (not isinstance(state_root, Path) or not state_root.is_absolute()
            or not isinstance(config_home, Path) or not config_home.is_absolute()
            or _mount_type(state_root) not in _SUPPORTED_FILESYSTEMS):
        raise ValueError("release installation needs a qualified Linux Core state root")
    return CoreManagedTrees(
        workspace=state_root, configuration_home=config_home,
        locations={"artifacts": state_root / "pack-release-client-compositions",
                   "evidence": state_root / "pack-release-client-installs"},
        owner_id="supersymmetry", policy_id=policy_id,
        location_sources={"artifacts": "release-client-composition",
                          "evidence": "release-client-install"},
    )


def _composition(
    result: Mapping[str, Any], host: CoreManagedTrees, policy: Mapping[str, Any],
) -> tuple[Any, dict[str, Any]]:
    user_source = (isinstance(result, Mapping)
                   and result.get("format") == _USER_COMPOSITION_RESULT_FORMAT)
    fresh_source = (isinstance(result, Mapping)
                    and result.get("format") == _FRESH_COMPOSITION_RESULT_FORMAT)
    if (not isinstance(result, Mapping)
            or result.get("format") not in {
                "workbench-pack-release-client-composition-result-v2",
                _USER_COMPOSITION_RESULT_FORMAT, _FRESH_COMPOSITION_RESULT_FORMAT}
            or (user_source and result.get("schema_version") != 3)
            or (fresh_source and result.get("schema_version") != 4)
            or result.get("outcome") not in {"retained", "reused", "reopened", "reconciled"}
            or result.get("installation_state") != "not-installed"
            or type(result.get("tree_id")) is not str):
        raise ValueError("release install needs an exact reopened Core composition")
    reference = host.describe(result["tree_id"])
    if (reference.workspace != host.workspace or reference.owner_id != "supersymmetry"
            or reference.role != "artifacts" or reference.inventory_policy != EXACT_INVENTORY_POLICY
            or reference.derived_status != "current" or reference.domain_id != result.get("plan_id")
            or reference.content_sha256 != result.get("tree_content_sha256")
            or reference.path != host.workspace / "pack-release-client-compositions" /
            str(result["plan_id"]).rsplit(":", 1)[-1] / "snapshot"):
        raise ValueError("release composition belongs to another Core source")
    lock = _object(read_private_single_link_bytes(reference.path / "source-lock.json",
                                                   byte_limit=4 * 1024 * 1024),
                   "release composition source lock")
    body = {**lock, "format": (_USER_COMPOSITION_PLAN_FORMAT if user_source
                                else _FRESH_COMPOSITION_PLAN_FORMAT if fresh_source
                                else COMPOSITION_PLAN_FORMAT)}
    body.pop("plan_id", None)
    if (lock.get("format") != (_USER_COMPOSITION_LOCK_FORMAT if user_source
                               else _FRESH_COMPOSITION_LOCK_FORMAT if fresh_source
                               else COMPOSITION_LOCK_FORMAT)
            or lock.get("plan_id") != result["plan_id"]
            or _PLAN_PREFIX.replace("client-install", "client-composition")
            + sha256(_canonical(body)).hexdigest() != result["plan_id"]
            or (not user_source and (
                type(lock.get("version")) is not str
                or re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", lock["version"]) is None))
            or (fresh_source and (
                lock.get("schema_version") != 4
                or lock.get("source_kind") != "official-release-curseforge"
                or result.get("source_kind") != lock["source_kind"]
                or result.get("version") != lock["version"]
                or result.get("asset_sha256") != lock.get("asset_sha256")
                or result.get("source_tree_ids") != [
                    lock["acquisition"]["tree_id"], lock["overrides"]["tree_id"]]
                or result.get("input_plan_id") != lock.get("input_plan_id")
                or result.get("layout_policy_id") != lock.get("layout_policy_id")))
            or (user_source and (
                lock.get("schema_version") != 3
                or lock.get("source_kind") != "user-prism-zip"
                or result.get("source_kind") != lock["source_kind"]
                or lock.get("source_version") != result.get("source_version")
                or lock.get("source_platform") != result.get("source_platform")
                or (lock.get("source_version") is not None
                    and (type(lock["source_version"]) is not str
                         or len(lock["source_version"]) > 128
                         or any(character in lock["source_version"]
                                for character in "\r\n\x00")))
                or not isinstance(lock.get("source_archive_sha256"), str)
                or _SHA256.fullmatch(lock["source_archive_sha256"]) is None
                or lock["source_archive_sha256"] != result.get("source_archive_sha256")
                or type(lock.get("source_archive_size")) is not int
                or lock["source_archive_size"] <= 0))
            or lock.get("file_count") != result.get("file_count")
            or lock.get("total_bytes") != result.get("total_bytes")
            or lock.get("files") is None or type(lock["files"]) is not list
            or len(lock["files"]) != lock["file_count"]
            or type(lock.get("blockers")) is not list
            or any(type(item) is not str for item in lock["blockers"])
            or len(set(lock["blockers"])) != len(lock["blockers"])
            or not set(lock["blockers"]).issubset(policy["accepted_source_limitations"])):
        raise ValueError("release composition lock differs from the selected installation")
    if user_source:
        metadata = lock.get("source_metadata_files")
        if (type(metadata) is not list or not metadata
                or {row.get("relative_path") for row in metadata if type(row) is dict}
                < {"instance.cfg", "mmc-pack.json"}):
            raise ValueError("Prism source lacks retained launcher metadata")
        paths: set[str] = set()
        for row in metadata:
            if (type(row) is not dict or set(row) != {"relative_path", "size", "sha256"}
                    or type(row["relative_path"]) is not str
                    or _relative(row["relative_path"]) != row["relative_path"]
                    or row["relative_path"] in {_MARKER, _STAGE_INTENT, ".minecraft"}
                    or row["relative_path"].casefold() in paths
                    or type(row["size"]) is not int or not 0 <= row["size"] <= MAX_FILE_BYTES
                    or type(row["sha256"]) is not str
                    or _SHA256.fullmatch(row["sha256"]) is None):
                raise ValueError("Prism source launcher metadata is incompatible")
            paths.add(row["relative_path"].casefold())
        manifest = _object(read_private_single_link_bytes(
            reference.path / "source-metadata/mmc-pack.json", byte_limit=1024 * 1024,
        ), "retained Prism launcher manifest")
        if source_platform_from_manifest(manifest) != lock["source_platform"]:
            raise ValueError("Prism source platform differs from retained launcher manifest")
    return reference, lock


def _bootstrap_members(raw: bytes, policy: Mapping[str, Any]) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    directories: set[str] = set()
    folded: set[str] = set()
    try:
        with ZipFile(BytesIO(raw)) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > _MAX_BOOTSTRAP_FILES or sum(
                    row.file_size for row in infos) > _MAX_BOOTSTRAP:
                raise ValueError("Cleanroom client archive exceeds its member bound")
            for info in infos:
                name = info.filename.rstrip("/")
                path = _relative(name)
                mode = info.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if (info.flag_bits & 1 or kind not in {0, stat.S_IFREG, stat.S_IFDIR}
                        or info.is_dir() != (kind == stat.S_IFDIR or info.filename.endswith("/"))):
                    raise ValueError("Cleanroom archive contains an unsupported member")
                key = path.casefold()
                if key in folded:
                    raise ValueError("Cleanroom archive repeats a member")
                folded.add(key)
                if info.is_dir():
                    directories.add(path)
                else:
                    if path == _MARKER or path.startswith(".minecraft/"):
                        raise ValueError("Cleanroom archive conflicts with the selected payload")
                    data = archive.read(info)
                    if len(data) != info.file_size:
                        raise ValueError("Cleanroom archive member changed size")
                    files[path] = data
    except BadZipFile as exc:
        raise ValueError("Cleanroom client archive is not a valid ZIP") from exc
    for path in files:
        pieces = path.split("/")
        if any("/".join(pieces[:index]).casefold() in {p.casefold() for p in files}
               for index in range(1, len(pieces))):
            raise ValueError("Cleanroom archive file is a directory prefix")
    if not {"instance.cfg", "mmc-pack.json",
            "patches/net.minecraftforge.json"}.issubset(files):
        raise ValueError("Cleanroom client archive lacks Prism launcher metadata")
    try:
        manifest = json.loads(files["mmc-pack.json"].decode("utf-8"))
        components = manifest["components"]
        versions = {row["uid"]: row["version"] for row in components}
    except (UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise ValueError("Cleanroom launcher manifest is invalid") from exc
    if (type(components) is not list or len(versions) != len(components)
            or versions.get("net.minecraft") != policy["minecraft_version"]
            or type(versions.get("net.minecraftforge")) is not str
            or not versions["net.minecraftforge"]):
        raise ValueError("Cleanroom launcher components differ from selected policy")
    try:
        files["instance.cfg"].decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("Cleanroom instance configuration is not UTF-8") from exc
    return files


def _archive_bytes(path: Path, policy: Mapping[str, Any]) -> bytes:
    expected = policy["cleanroom_client"]
    with _held_file(path, expected_size=expected["size"]) as (descriptor, _):
        raw = bytearray()
        while block := os.read(descriptor, _CHUNK):
            raw.extend(block)
            if len(raw) > expected["size"]:
                raise ValueError("Cleanroom archive grew during review")
    data = bytes(raw)
    if len(data) != expected["size"] or "sha256:" + sha256(data).hexdigest() != expected["sha256"]:
        raise ValueError("Cleanroom archive differs from the selected policy")
    _bootstrap_members(data, policy)
    return data


def _java(result: Mapping[str, Any] | None, policy: Mapping[str, Any]) -> dict[str, Any] | None:
    if result is None:
        return None
    if not isinstance(result, Mapping):
        raise ValueError("selected Core Java result is incomplete")
    if (result.get("format") == "workbench-java-runtime-result-v3"
            and result.get("schema_version") == 3
            and result.get("source") == "user-path"
            and result.get("outcome") == "selected"):
        runtime = result.get("runtime")
        if (not isinstance(runtime, Mapping)
                or runtime.get("state") != "unverified"
                or not isinstance(runtime.get("java_home_uri"), str)):
            raise ValueError("selected user Java path is incomplete")
        parsed = urlparse(runtime["java_home_uri"])
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            raise ValueError("selected user Java home must be a local file URI")
        home = Path(unquote(parsed.path))
        if (not home.is_absolute() or "\n" in str(home) or "\r" in str(home)
                or "\x00" in str(home)):
            raise ValueError("selected user Java home is unsafe for Prism configuration")
        path = home / "bin/java"
        return {"runtime_id": "user-path:sha256:" + sha256(str(home).encode()).hexdigest(),
                "java_path": str(path), "java_version": None,
                "selection_state": "user-path-unverified"}
    receipt = result.get("receipt") if isinstance(result, Mapping) else None
    if (result.get("format") != "workbench-java-runtime-result-v2"
            or result.get("source") != "managed" or not isinstance(receipt, Mapping)
            or receipt.get("format") != "workbench-java-runtime-receipt-v2"
            or receipt.get("state") != "ready" or not isinstance(receipt.get("policy"), Mapping)
            or type(receipt["policy"].get("feature_version")) is not int
            or receipt["policy"]["feature_version"] < 1
            or not isinstance(receipt.get("host"), Mapping)
            or receipt["host"].get("os") != "linux"
            or not isinstance(receipt.get("target"), Mapping)
            or not isinstance(receipt.get("probe"), Mapping)
            or not isinstance(receipt.get("runtime_id"), str)):
        raise ValueError("release install needs the selected Core Linux Java result")
    uri = receipt["target"].get("java_uri")
    parsed = urlparse(uri) if isinstance(uri, str) else None
    if parsed is None or parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise ValueError("managed Java executable has no local path")
    path = Path(unquote(parsed.path))
    if not path.is_absolute() or not path.is_file():
        raise ValueError("managed Java executable is unavailable")
    return {"runtime_id": receipt["runtime_id"], "java_path": str(path),
            "java_version": receipt["probe"]["java_version"],
            "selection_state": "managed-verified",
            "feature_version": receipt["policy"]["feature_version"]}


def _launcher(root: Path) -> bool:
    if not isinstance(root, Path) or not root.is_absolute():
        return False
    if (_mount_type(root) not in _SUPPORTED_FILESYSTEMS or root.is_symlink()
            or not root.is_dir() or not (root / "prismlauncher.cfg").is_file()
            or (root / "prismlauncher.cfg").is_symlink()
            or not (root / "instances").is_dir()
            or (root / "instances").is_symlink()):
        return False
    parent = (root / "instances").stat()
    return (parent.st_uid == os.geteuid() and parent.st_mode & 0o022 == 0)


def plan_prism_data_root(launcher_root: Path, *, state_root: Path) -> dict[str, Any]:
    """Review a stable private Prism data root before initializing it."""

    if (not isinstance(launcher_root, Path) or not launcher_root.is_absolute()
            or not isinstance(state_root, Path) or not state_root.is_absolute()
            or not launcher_root.name or len(launcher_root.name) > 80
            or any(char in launcher_root.name for char in "/\\:\x00")):
        raise ValueError("Prism data root must be an absolute ordinary path")
    body = {"format": _ROOT_PLAN_FORMAT, "schema_version": 1,
            "launcher_root": str(launcher_root), "state_root": str(state_root)}
    plan_id = _ROOT_PLAN_PREFIX + sha256(_canonical(body)).hexdigest()
    blockers: list[str] = []
    if (_mount_type(state_root) not in _SUPPORTED_FILESYSTEMS
            or _mount_type(launcher_root.parent) not in _SUPPORTED_FILESYSTEMS):
        blockers.append("unsupported-linux-filesystem")
    existing_parent = launcher_root.parent
    while not existing_parent.exists() and not existing_parent.is_symlink():
        existing_parent = existing_parent.parent
    try:
        parent_fd = pinned_directory(existing_parent, create=False)
        try:
            info = os.fstat(parent_fd)
            if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                blockers.append("unsafe-prism-root-parent")
        finally:
            os.close(parent_fd)
    except (OSError, ValueError):
        blockers.append("prism-root-parent-unavailable")
    if _launcher(launcher_root):
        action = "reuse"
    elif launcher_root.exists() or launcher_root.is_symlink():
        action = "blocked"
        blockers.append("existing-prism-root-needs-review")
    elif not blockers and launcher_root.parent.is_dir() and count_prepared_directory_stages(
            launcher_root, stage_prefix=f".{launcher_root.name}.root-init-"):
        action = "reconcile"
        blockers.append("interrupted-prism-root-initialization")
    else:
        action = "initialize"
    return {**body, "plan_id": plan_id, "action": action,
            "state": "ready" if not blockers else "blocked", "blockers": blockers}


def initialize_prism_data_root(
    launcher_root: Path, *, state_root: Path, expected_plan_id: str,
    check_cancelled: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Create the selected Prism root with a Core no-replace directory move."""

    plan = plan_prism_data_root(launcher_root, state_root=state_root)
    if plan["plan_id"] != expected_plan_id or plan["state"] != "ready":
        raise ValueError("Prism data root plan changed or needs review")
    if plan["action"] == "reuse":
        return {**plan, "outcome": "reused"}
    lock_path = state_root / "pack-release-client-installs" / (
        ".prism-root-" + expected_plan_id.rsplit(":", 1)[-1] + ".lock")
    _private_directory(lock_path.parent)
    with private_record_lock(lock_path, wait=True):
        parent_fd = pinned_directory(launcher_root.parent, create=True)
        os.close(parent_fd)
        if plan_prism_data_root(launcher_root, state_root=state_root) != plan:
            raise ValueError("Prism data root changed before initialization")
        check_cancelled()
        stage = launcher_root.parent / (
            "." + launcher_root.name + ".root-init-" + uuid4().hex)
        stage.mkdir(mode=0o700)
        stage_intent = _canonical({
            "format": _ROOT_PLAN_FORMAT, "plan_id": expected_plan_id,
            "launcher_root": str(launcher_root)}) + b"\n"
        _write_file(stage / _ROOT_STAGE_INTENT, stage_intent)
        payload = stage / "payload"
        payload.mkdir(mode=0o700)
        (payload / "instances").mkdir(mode=0o700)
        _write_file(payload / "prismlauncher.cfg",
                    b"[General]\nConfigVersion=1.3\n")
        _write_file(payload / _ROOT_MARKER, _canonical({
            "format": _ROOT_PLAN_FORMAT, "plan_id": expected_plan_id,
            "launcher_root": str(launcher_root)}) + b"\n")
        check_cancelled()
        if not _launcher(payload):
            raise ValueError("prepared Prism data root is incomplete")
        stage_info = stage.stat()
        promote_prepared_directory(
            payload, launcher_root,
            stage_marker=((stage_info.st_dev, stage_info.st_ino),
                          _ROOT_STAGE_INTENT, stage_intent),
        )
        if not _launcher(launcher_root):
            raise ValueError("published Prism data root changed")
        return {**plan, "outcome": "initialized"}


def reconcile_prism_data_root(
    launcher_root: Path, *, state_root: Path, expected_plan_id: str,
) -> dict[str, Any]:
    """Finish only one complete Core-created Prism root stage."""

    plan = plan_prism_data_root(launcher_root, state_root=state_root)
    if plan["plan_id"] != expected_plan_id or plan["action"] != "reconcile":
        raise ValueError("Prism data root has no interrupted initialization")
    lock_path = state_root / "pack-release-client-installs" / (
        ".prism-root-" + expected_plan_id.rsplit(":", 1)[-1] + ".lock")
    _private_directory(lock_path.parent)
    with private_record_lock(lock_path, wait=True):
        prefix = "." + launcher_root.name + ".root-init-"
        stages = [path for path in launcher_root.parent.iterdir() if path.name.startswith(prefix)]
        if len(stages) != 1 or not private_path(stages[0], directory=True):
            raise ValueError("Prism data root has ambiguous initialization stages")
        payload = stages[0] / "payload"
        try:
            marker = _object(read_private_single_link_bytes(payload / _ROOT_MARKER,
                                                            byte_limit=4096), "Prism root marker")
        except (OSError, ValueError, DurableRecordError) as exc:
            raise ValueError("interrupted Prism data root is incomplete") from exc
        if (marker.get("format") != _ROOT_PLAN_FORMAT
                or marker.get("plan_id") != expected_plan_id
                or marker.get("launcher_root") != str(launcher_root)
                or not _launcher(payload)):
            raise ValueError("interrupted Prism data root is incomplete")
        stage_intent = _canonical({
            "format": _ROOT_PLAN_FORMAT, "plan_id": expected_plan_id,
            "launcher_root": str(launcher_root)}) + b"\n"
        stage_info = stages[0].stat()
        promote_prepared_directory(
            payload, launcher_root,
            stage_marker=((stage_info.st_dev, stage_info.st_ino),
                          _ROOT_STAGE_INTENT, stage_intent),
        )
        if not _launcher(launcher_root):
            raise ValueError("reconciled Prism data root changed")
        return {**plan, "outcome": "reconciled"}


def abandon_interrupted_prism_data_root(
    launcher_root: Path, *, state_root: Path, expected_plan_id: str,
) -> dict[str, Any]:
    """Move an incomplete root stage aside without deleting its bytes."""

    plan = plan_prism_data_root(launcher_root, state_root=state_root)
    if plan["plan_id"] != expected_plan_id or plan["action"] != "reconcile":
        raise ValueError("Prism data root has no interrupted initialization")
    lock_path = state_root / "pack-release-client-installs" / (
        ".prism-root-" + expected_plan_id.rsplit(":", 1)[-1] + ".lock")
    _private_directory(lock_path.parent)
    with private_record_lock(lock_path, wait=True):
        prefix = "." + launcher_root.name + ".root-init-"
        stages = [path for path in launcher_root.parent.iterdir() if path.name.startswith(prefix)]
        if len(stages) != 1 or not private_path(stages[0], directory=True):
            raise ValueError("Prism data root has ambiguous initialization stages")
        intent = _object(read_private_single_link_bytes(
            stages[0] / _ROOT_STAGE_INTENT, byte_limit=4096), "Prism root stage intent")
        if (intent.get("format") != _ROOT_PLAN_FORMAT
                or intent.get("plan_id") != expected_plan_id
                or intent.get("launcher_root") != str(launcher_root)):
            raise ValueError("Prism root stage belongs to another plan")
        parent = launcher_root.parent.stat()
        staged = stages[0].stat()
        retired = launcher_root.parent / (
            ".workbench-abandoned-prism-root-"
            + expected_plan_id.rsplit(":", 1)[-1][:16] + "-" + uuid4().hex)
        _rename_no_replace(
            stages[0], retired,
            parent_identity=(parent.st_dev, parent.st_ino),
            payload_identity=(staged.st_dev, staged.st_ino),
        )
        return {"format": "workbench-prism-data-root-abandonment-v1",
                "plan_id": expected_plan_id, "outcome": "abandoned",
                "retained_stage_path": str(retired)}


def _receipt_path(state_root: Path, plan_id: str) -> Path:
    return (state_root / "pack-release-client-installs" /
            plan_id.rsplit(":", 1)[-1] / "receipt.json")


def plan_release_client_install(
    composition_result: Mapping[str, Any], *, policy_path: Path,
    state_root: Path, config_home: Path, launcher_root: Path,
    managed_java_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Review an exact, separate Prism instance without changing sources or launcher."""

    policy = load_release_install_policy(policy_path)
    policy_id = "workbench-pack-release-client-install-policy:sha256:" + sha256(_canonical(policy)).hexdigest()
    host = _host(state_root, config_home, policy_id)
    reference, composition = _composition(composition_result, host, policy)
    blockers: list[str] = []
    archive_path = state_root / "artifacts/sha256" / policy["cleanroom_client"]["sha256"].removeprefix("sha256:")
    java = _java(managed_java_result, policy)
    if java is None:
        blockers.append("selected-java-unavailable")
    if not _launcher(launcher_root):
        blockers.append("prism-root-uninitialized-or-unsupported-filesystem")
    try:
        prism_tool = inspect_tools(state_root)["tools"]["prism"]
    except (OSError, ValueError, ToolingProvisionError):
        prism_tool = {"state": "invalid", "executable": None}
    if prism_tool["state"] != "ready":
        blockers.append("prism-executable-unavailable")
    source_kind = ("user-prism-zip" if composition_result["format"] == _USER_COMPOSITION_RESULT_FORMAT
                   else "official-release-curseforge" if composition_result["format"] == _FRESH_COMPOSITION_RESULT_FORMAT
                   else "official-release")
    user_source = source_kind == "user-prism-zip"
    source_version = composition.get("source_version") if source_kind == "user-prism-zip" else composition["version"]
    source_archive_sha256 = (composition["source_archive_sha256"] if source_kind == "user-prism-zip"
                             else composition["asset_sha256"])
    source_platform = (composition["source_platform"] if user_source else {
        "kind": "cleanroom", "component_uid": "net.minecraftforge",
        "component_version": policy["cleanroom_version"],
        "minecraft_version": policy["minecraft_version"],
        "classification": "pinned-profile",
    })
    if (user_source and source_platform["kind"] == "forge" and java is not None
            and java["selection_state"] == "managed-verified"
            and java["feature_version"] != 8):
        blockers.append("imported-forge-needs-java-8-or-custom-path")
    name = ("workbench-supersymmetry-import-" if source_kind == "user-prism-zip"
            else "workbench-supersymmetry-" + composition["version"] + "-")
    # Different Core Java choices or install policies need separate launcher
    # instances. A source-only name would collide with an earlier install.
    installation_choice = sha256(_canonical({
        "policy_id": policy_id, "java": java,
    })).hexdigest()[:12]
    name += composition["plan_id"].rsplit(":", 1)[-1][:12] + "-" + installation_choice
    target = launcher_root / "instances" / name
    body = {
        "format": PLAN_FORMAT, "schema_version": 1, "profile": "supersymmetry",
        "release_version": (composition["version"] if source_kind != "user-prism-zip"
                            else None),
        "composition_plan_id": composition["plan_id"],
        "source_kind": source_kind, "source_version": source_version,
        "source_platform": source_platform,
        "source_archive_sha256": source_archive_sha256,
        "composition_tree_id": reference.tree_id,
        "composition_content_sha256": reference.content_sha256,
        "file_count": composition["file_count"], "total_bytes": composition["total_bytes"],
        "policy_id": policy_id,
        "platform_profile_id": None if user_source else policy["platform_profile_id"],
        "bootstrap_sha256": None if user_source else policy["cleanroom_client"]["sha256"],
        "bootstrap_size": None if user_source else policy["cleanroom_client"]["size"],
        "cleanroom_version": None if user_source else policy["cleanroom_version"],
        "minecraft_version": policy["minecraft_version"],
        "java_runtime_id": None if java is None else java["runtime_id"],
        "java_path": None if java is None else java["java_path"],
        "java_version": None if java is None else java["java_version"],
        "java_selection_state": None if java is None else java["selection_state"],
        "java_recommended_feature": policy["recommended_java_feature"],
        "java_selected_feature": None if java is None else java.get("feature_version"),
        "launcher_root": str(launcher_root), "instance_path": str(target),
        "memory_mib": policy["memory_mib"],
        "source_limitations": composition["blockers"],
        "runtime_qualification_state": "not-qualified",
    }
    plan_id = _PLAN_PREFIX + sha256(_canonical(body)).hexdigest()
    receipt_path = _receipt_path(state_root, plan_id)
    action = "acquire"
    if target.is_symlink():
        action = "reconcile"
    elif target.exists():
        action = "reopen" if receipt_path.is_file() else "reconcile"
    elif _launcher(launcher_root) and count_prepared_directory_stages(
            target, stage_prefix=f".{name}.release-install-"):
        action = "reconcile"
    if action == "reconcile":
        blockers.append("interrupted-install-needs-reconcile")
    if action == "acquire" and not user_source:
        if not archive_path.is_file():
            blockers.append("cleanroom-bootstrap-unavailable")
        else:
            _archive_bytes(archive_path, policy)
    return {**body, "plan_id": plan_id, "state": "ready" if not blockers else "blocked",
            "blockers": blockers, "action": action,
            "prism_tool_state": prism_tool["state"],
            "launcher_executable": prism_tool["executable"],
            "account_state": "present-not-read" if (launcher_root / "accounts.json").is_file()
            else "launcher-setup-needed"}


def _write_file(path: Path, data: bytes) -> None:
    parent = pinned_directory(path.parent, create=True)
    try:
        descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                             dir_fd=parent)
        with os.fdopen(descriptor, "wb") as sink:
            sink.write(data)
            sink.flush()
            os.fsync(sink.fileno())
    finally:
        os.close(parent)


def _copy_file(source: Path, target: Path, *, size: int, digest: str,
               check_cancelled: Callable[[], None]) -> None:
    parent = pinned_directory(target.parent, create=True)
    try:
        descriptor = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        with os.fdopen(descriptor, "wb") as sink, _held_file(source, expected_size=size) as (held, _):
            observed, measured = 0, sha256()
            while block := os.read(held, _CHUNK):
                check_cancelled()
                observed += len(block)
                if observed > size:
                    raise ValueError("release composition member grew during install")
                measured.update(block)
                sink.write(block)
            sink.flush()
            os.fsync(sink.fileno())
        if observed != size or "sha256:" + measured.hexdigest() != digest:
            raise ValueError("release composition member changed during install")
    finally:
        os.close(parent)


def _configure_instance(raw: bytes, plan: Mapping[str, Any]) -> bytes:
    lines = raw.decode("utf-8-sig").splitlines()
    if plan["java_version"] is None:
        # A user path carries no Java probe, so discard any bootstrap hint.
        lines = [line for line in lines if line.split("=", 1)[0].strip()
                 not in {"JavaVersion", "JavaArchitecture"}]
    display_name = ("Workbench Supersymmetry " + plan["release_version"]
                    if plan["source_kind"] != "user-prism-zip" else
                    "Workbench Supersymmetry " + (plan["source_version"] or "imported")
                    + " (imported)")
    if plan["java_selection_state"] == "user-path-unverified":
        display_name += " (custom Java)"
    elif plan["java_selected_feature"] != plan["java_recommended_feature"]:
        display_name += " (Java " + str(plan["java_selected_feature"]) + ")"
    updates = {
        "AutomaticJava": "false", "IgnoreJavaCompatibility": "true",
        "JavaPath": plan["java_path"],
        "ManagedPack": "false",
        "MaxMemAlloc": str(plan["memory_mib"]), "MinMemAlloc": "512",
        "OverrideJavaLocation": "true", "OverrideMemory": "true",
        "name": display_name,
    }
    if plan["java_version"] is not None:
        updates["JavaVersion"] = plan["java_version"]
        updates["JavaArchitecture"] = "64"
    positions: dict[str, int] = {}
    for index, line in enumerate(lines):
        if "=" in line and not line.lstrip().startswith(("#", ";")):
            key = line.split("=", 1)[0].strip()
            if key in updates:
                if key in positions:
                    raise ValueError("Prism instance has a duplicate managed setting")
                positions[key] = index
    for key, value in updates.items():
        if "\n" in value or "\r" in value:
            raise ValueError("selected Java path is unsafe for Prism configuration")
        line = f"{key}={value}"
        if key in positions:
            lines[positions[key]] = line
        else:
            lines.append(line)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _initial_digest(root: Path) -> tuple[str, int, int]:
    members, mode, files, _ = inventory_exact_members(root)
    if mode != 0o700:
        raise ValueError("prepared Prism instance has an unsafe root mode")
    rows = [row for row in members if row["path"] != _MARKER]
    return "sha256:" + exact_content_sha256(rows), files - int(any(row["path"] == _MARKER for row in members)), sum(
        int(row["size"]) for row in rows if row["kind"] == "file")


def _marker(plan: Mapping[str, Any], bootstrap_resource_id: str | None,
            initial_digest: str, file_count: int, total_bytes: int) -> dict[str, Any]:
    return {"format": RECEIPT_FORMAT, "schema_version": 1,
            "plan_id": plan["plan_id"], "composition_tree_id": plan["composition_tree_id"],
            "composition_content_sha256": plan["composition_content_sha256"],
            "source_kind": plan["source_kind"], "source_version": plan["source_version"],
            "source_platform": plan["source_platform"],
            "source_archive_sha256": plan["source_archive_sha256"],
            "bootstrap_resource_id": bootstrap_resource_id,
            "bootstrap_sha256": plan["bootstrap_sha256"],
            "java_runtime_id": plan["java_runtime_id"], "java_path": plan["java_path"],
            "java_selection_state": plan["java_selection_state"],
            "java_selected_feature": plan["java_selected_feature"],
            "instance_path": plan["instance_path"], "initial_tree_sha256": initial_digest,
            "initial_file_count": file_count, "initial_total_bytes": total_bytes,
            "installation_state": "installed", "runtime_qualification_state": "not-qualified",
            "source_limitations": plan["source_limitations"]}


def _publish_receipt(plan: Mapping[str, Any], marker: Mapping[str, Any],
                     state_root: Path) -> dict[str, Any]:
    path = _receipt_path(state_root, plan["plan_id"])
    _private_directory(path.parent)
    raw = _canonical(marker) + b"\n"
    if path.exists() or path.is_symlink():
        if read_private_single_link_bytes(path, byte_limit=_RECEIPT_LIMIT) != raw:
            raise ValueError("release installation receipt belongs to another result")
    else:
        publish_create_once_bytes(path, raw, byte_limit=_RECEIPT_LIMIT)
    return {**marker, "outcome": "installed", "receipt_path": str(path)}


def _read_marker(instance: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    try:
        marker = _object(read_private_single_link_bytes(instance / _MARKER,
                                                        byte_limit=_RECEIPT_LIMIT),
                         "release instance marker")
    except (OSError, ValueError, DurableRecordError) as exc:
        raise ValueError("release installation marker is unavailable or incomplete") from exc
    if (marker.get("format") != RECEIPT_FORMAT or marker.get("plan_id") != plan["plan_id"]
            or marker.get("instance_path") != plan["instance_path"]
            or marker.get("composition_tree_id") != plan["composition_tree_id"]
            or marker.get("composition_content_sha256") != plan["composition_content_sha256"]
            or marker.get("source_kind") != plan["source_kind"]
            or marker.get("source_version") != plan["source_version"]
            or marker.get("source_platform") != plan["source_platform"]
            or marker.get("source_archive_sha256") != plan["source_archive_sha256"]
            or marker.get("bootstrap_sha256") != plan["bootstrap_sha256"]
            or marker.get("java_runtime_id") != plan["java_runtime_id"]
            or marker.get("java_selection_state") != plan["java_selection_state"]
            or marker.get("source_limitations") != plan["source_limitations"]
            or marker.get("runtime_qualification_state") != "not-qualified"):
        raise ValueError("Prism instance belongs to another release installation")
    return marker


def _verify_retained_bootstrap(
    marker: Mapping[str, Any], plan: Mapping[str, Any],
    host: CoreManagedTrees, policy: Mapping[str, Any],
) -> None:
    resource_id = marker.get("bootstrap_resource_id")
    if plan["source_kind"] == "user-prism-zip":
        if (resource_id is not None or marker.get("bootstrap_sha256") is not None
                or plan["bootstrap_sha256"] is not None or plan["bootstrap_size"] is not None):
            raise ValueError("imported Prism instance has unexpected Cleanroom bootstrap evidence")
        return
    if type(resource_id) is not str:
        raise ValueError("release installation lacks retained bootstrap evidence")
    reference, raw = host.read_file_reference(resource_id)
    if (reference.owner_id != "supersymmetry" or reference.role != "evidence"
            or reference.policy_id != plan["policy_id"]
            or reference.domain_id != plan["plan_id"] + ":bootstrap"
            or reference.bytes != plan["bootstrap_size"]
            or reference.sha256 != plan["bootstrap_sha256"]
            or "sha256:" + sha256(raw).hexdigest() != plan["bootstrap_sha256"]):
        raise ValueError("retained Cleanroom bootstrap differs from installation")
    _bootstrap_members(raw, policy)


def apply_release_client_install(
    composition_result: Mapping[str, Any], *, policy_path: Path,
    state_root: Path, config_home: Path, launcher_root: Path,
    managed_java_result: Mapping[str, Any], expected_plan_id: str,
    check_cancelled: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Publish one stable Linux Prism instance; preserve uncertain stages."""

    plan = plan_release_client_install(
        composition_result, policy_path=policy_path, state_root=state_root,
        config_home=config_home, launcher_root=launcher_root,
        managed_java_result=managed_java_result,
    )
    if plan["plan_id"] != expected_plan_id or plan["state"] != "ready":
        raise ValueError("release install inputs are unavailable or changed after review")
    if plan["action"] == "reopen":
        return reopen_release_client_install(
            composition_result, policy_path=policy_path, state_root=state_root,
            config_home=config_home, launcher_root=launcher_root,
            managed_java_result=managed_java_result, expected_plan_id=expected_plan_id,
        )
    policy = load_release_install_policy(policy_path)
    host = _host(state_root, config_home, plan["policy_id"])
    reference, composition = _composition(composition_result, host, policy)
    target = Path(plan["instance_path"])
    lock_path = state_root / "pack-release-client-installs" / ("." + expected_plan_id.rsplit(":", 1)[-1] + ".lock")
    _private_directory(lock_path.parent)
    with private_record_lock(lock_path, wait=True):
        current = plan_release_client_install(
            composition_result, policy_path=policy_path, state_root=state_root,
            config_home=config_home, launcher_root=launcher_root,
            managed_java_result=managed_java_result,
        )
        if current != plan or target.exists() or target.is_symlink():
            raise ValueError("release install destination changed before staging")
        check_cancelled()
        bootstrap: dict[str, bytes] = {}
        bootstrap_resource_id = None
        if plan["source_kind"] != "user-prism-zip":
            archive_path = (state_root / "artifacts/sha256"
                            / plan["bootstrap_sha256"].removeprefix("sha256:"))
            raw = _archive_bytes(archive_path, policy)
            bootstrap = _bootstrap_members(raw, policy)
            source = host.retain_file_reference(
                "evidence", "cleanroom-client.zip", archive_path,
                sha256=plan["bootstrap_sha256"].removeprefix("sha256:"),
                size=plan["bootstrap_size"],
                domain_id=expected_plan_id + ":bootstrap",
            )
            bootstrap_resource_id = source.resource_id
        stage = target.parent / ("." + target.name + ".release-install-" + uuid4().hex)
        stage.mkdir(mode=0o700)
        stage_intent = _canonical({
            "format": PLAN_FORMAT, "plan_id": expected_plan_id,
            "instance_path": str(target)}) + b"\n"
        _write_file(stage / _STAGE_INTENT, stage_intent)
        payload = stage / "payload"
        payload.mkdir(mode=0o700)
        if plan["source_kind"] == "user-prism-zip":
            for row in composition["source_metadata_files"]:
                check_cancelled()
                name = row["relative_path"]
                original = reference.path / "source-metadata" / name
                if name == "instance.cfg":
                    data = read_private_single_link_bytes(original, byte_limit=256 * 1024)
                    if (len(data) != row["size"]
                            or "sha256:" + sha256(data).hexdigest() != row["sha256"]):
                        raise ValueError("retained Prism configuration changed before install")
                    _write_file(payload / name, _configure_instance(data, plan))
                else:
                    _copy_file(original, payload / name, size=row["size"],
                               digest=row["sha256"], check_cancelled=check_cancelled)
        else:
            for name, data in sorted(bootstrap.items()):
                check_cancelled()
                if name == "instance.cfg":
                    data = _configure_instance(data, plan)
                _write_file(payload / name, data)
        minecraft = payload / ".minecraft"
        minecraft.mkdir(mode=0o700)
        for row in composition["files"]:
            check_cancelled()
            source_path = reference.path / "minecraft-root" / row["relative_path"]
            _copy_file(source_path, minecraft / row["relative_path"],
                       size=row["size"], digest=row["sha256"],
                       check_cancelled=check_cancelled)
        digest, count, total = _initial_digest(payload)
        marker = _marker(plan, bootstrap_resource_id, digest, count, total)
        _write_file(payload / _MARKER, _canonical(marker) + b"\n")
        check_cancelled()
        observed = _initial_digest(payload)
        if observed != (digest, count, total):
            raise ValueError("prepared Prism instance changed after validation")
        stage_info = stage.stat()
        promote_prepared_directory(
            payload, target,
            stage_marker=((stage_info.st_dev, stage_info.st_ino),
                          _STAGE_INTENT, stage_intent),
        )
        check_cancelled()
        if _initial_digest(target) != (digest, count, total):
            raise ValueError("published Prism instance changed before receipt")
        return _publish_receipt(plan, marker, state_root)


def reopen_release_client_install(
    composition_result: Mapping[str, Any], *, policy_path: Path,
    state_root: Path, config_home: Path, launcher_root: Path,
    managed_java_result: Mapping[str, Any], expected_plan_id: str,
) -> dict[str, Any]:
    """Read the initial Core receipt; report later launcher mutation separately."""

    plan = plan_release_client_install(
        composition_result, policy_path=policy_path, state_root=state_root,
        config_home=config_home, launcher_root=launcher_root,
        managed_java_result=managed_java_result,
    )
    if plan["plan_id"] != expected_plan_id or plan["action"] != "reopen":
        raise ValueError("release installation has no completed receipt")
    target = Path(plan["instance_path"])
    marker = _read_marker(target, plan)
    policy = load_release_install_policy(policy_path)
    host = _host(state_root, config_home, plan["policy_id"])
    _verify_retained_bootstrap(marker, plan, host, policy)
    path = _receipt_path(state_root, expected_plan_id)
    if read_private_single_link_bytes(path, byte_limit=_RECEIPT_LIMIT) != _canonical(marker) + b"\n":
        raise ValueError("Core receipt differs from the installed instance marker")
    initial = (marker["initial_tree_sha256"], marker["initial_file_count"],
               marker["initial_total_bytes"])
    current = _initial_digest(target)
    return {**marker, "outcome": "reopened", "receipt_path": str(path),
            "instance_state": "initial" if current == initial else "launcher-modified"}


def reconcile_release_client_install(
    composition_result: Mapping[str, Any], *, policy_path: Path,
    state_root: Path, config_home: Path, launcher_root: Path,
    managed_java_result: Mapping[str, Any], expected_plan_id: str,
) -> dict[str, Any]:
    """Finish only a validated prepared stage or post-promotion receipt gap."""

    plan = plan_release_client_install(
        composition_result, policy_path=policy_path, state_root=state_root,
        config_home=config_home, launcher_root=launcher_root,
        managed_java_result=managed_java_result,
    )
    if (plan["plan_id"] != expected_plan_id or plan["action"] != "reconcile"
            or "imported-forge-needs-java-8-or-custom-path" in plan["blockers"]):
        raise ValueError("release installation has no interrupted operation")
    target = Path(plan["instance_path"])
    policy = load_release_install_policy(policy_path)
    host = _host(state_root, config_home, plan["policy_id"])
    lock_path = state_root / "pack-release-client-installs" / ("." + expected_plan_id.rsplit(":", 1)[-1] + ".lock")
    _private_directory(lock_path.parent)
    with private_record_lock(lock_path, wait=True):
        if target.is_symlink():
            raise ValueError("interrupted Prism destination is a symbolic link")
        if target.exists():
            marker = _read_marker(target, plan)
            _verify_retained_bootstrap(marker, plan, host, policy)
            expected = (marker["initial_tree_sha256"], marker["initial_file_count"],
                        marker["initial_total_bytes"])
            if _initial_digest(target) != expected:
                raise ValueError("interrupted Prism instance changed before Core receipt")
        else:
            prefix = "." + target.name + ".release-install-"
            stages = [path for path in target.parent.iterdir() if path.name.startswith(prefix)]
            if len(stages) != 1 or not private_path(stages[0], directory=True):
                raise ValueError("interrupted release install has an ambiguous stage")
            payload = stages[0] / "payload"
            marker = _read_marker(payload, plan)
            _verify_retained_bootstrap(marker, plan, host, policy)
            expected = (marker["initial_tree_sha256"], marker["initial_file_count"],
                        marker["initial_total_bytes"])
            if _initial_digest(payload) != expected:
                raise ValueError("interrupted release install stage is incomplete")
            stage_intent = _canonical({
                "format": PLAN_FORMAT, "plan_id": expected_plan_id,
                "instance_path": str(target)}) + b"\n"
            stage_info = stages[0].stat()
            promote_prepared_directory(
                payload, target,
                stage_marker=((stage_info.st_dev, stage_info.st_ino),
                              _STAGE_INTENT, stage_intent),
            )
            if _initial_digest(target) != expected:
                raise ValueError("reconciled Prism instance changed before receipt")
        return _publish_receipt(plan, marker, state_root)


def abandon_interrupted_release_client_install(
    composition_result: Mapping[str, Any], *, policy_path: Path,
    state_root: Path, config_home: Path, launcher_root: Path,
    managed_java_result: Mapping[str, Any], expected_plan_id: str,
) -> dict[str, Any]:
    """Move one incomplete Core stage aside without deleting its bytes."""

    plan = plan_release_client_install(
        composition_result, policy_path=policy_path, state_root=state_root,
        config_home=config_home, launcher_root=launcher_root,
        managed_java_result=managed_java_result,
    )
    if plan["plan_id"] != expected_plan_id or plan["action"] != "reconcile":
        raise ValueError("release install has no interrupted stage to abandon")
    target = Path(plan["instance_path"])
    if target.exists() or target.is_symlink():
        raise ValueError("published Prism instance cannot be abandoned")
    lock_path = state_root / "pack-release-client-installs" / (
        "." + expected_plan_id.rsplit(":", 1)[-1] + ".lock")
    _private_directory(lock_path.parent)
    with private_record_lock(lock_path, wait=True):
        prefix = "." + target.name + ".release-install-"
        stages = [path for path in target.parent.iterdir() if path.name.startswith(prefix)]
        if len(stages) != 1 or not private_path(stages[0], directory=True):
            raise ValueError("interrupted release install has an ambiguous stage")
        intent = _object(read_private_single_link_bytes(
            stages[0] / _STAGE_INTENT, byte_limit=4096), "release install stage intent")
        if (intent.get("format") != PLAN_FORMAT
                or intent.get("plan_id") != expected_plan_id
                or intent.get("instance_path") != str(target)):
            raise ValueError("interrupted release install stage belongs to another plan")
        parent = target.parent.stat()
        staged = stages[0].stat()
        retired = target.parent / (
            ".workbench-abandoned-release-install-"
            + expected_plan_id.rsplit(":", 1)[-1][:16] + "-" + uuid4().hex)
        _rename_no_replace(
            stages[0], retired,
            parent_identity=(parent.st_dev, parent.st_ino),
            payload_identity=(staged.st_dev, staged.st_ino),
        )
        return {"format": "workbench-pack-release-client-install-abandonment-v1",
                "plan_id": expected_plan_id, "outcome": "abandoned",
                "retained_stage_path": str(retired),
                "installation_state": "not-installed"}


__all__ = ["load_release_install_policy", "plan_prism_data_root",
           "initialize_prism_data_root", "reconcile_prism_data_root",
           "abandon_interrupted_prism_data_root",
           "plan_release_client_install",
           "apply_release_client_install", "reopen_release_client_install",
           "reconcile_release_client_install",
           "abandon_interrupted_release_client_install"]
