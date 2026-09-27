"""Acquire a selected release's external files through an authorized provider.

The pack owns the selected project/file IDs and resource-pack placements. Core
owns network admission, exact byte retention, private progress records, and
publication of the complete file tree. No CurseForge credential is stored here.
"""

from __future__ import annotations

from hashlib import sha1, sha256
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, BinaryIO, Callable, ContextManager, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from workbench_api.host_filesystem import DurableRecordError
from workbench_api.managed_trees import ManagedTreeReference

from .artifact_store import ArtifactStoreError, fetch_verified_artifact
from .durable_records import (
    private_record_lock, read_private_single_link_bytes, replace_private_bytes,
)
from .environment_package_install_plan import _SUPPORTED_FILESYSTEMS, _mount_type
from .host_filesystem import private_path
from .managed_trees import CoreManagedTrees
from .output_routing import _private_directory
from .pack_release_local import _canonical, _filename
from .pack_release_prism_resourcepacks import load_resourcepack_policy
from .storage.exact_tree_inventory import EXACT_INVENTORY_POLICY


PLAN_FORMAT = "workbench-pack-release-curseforge-acquisition-plan-v1"
FILE_FORMAT = "workbench-pack-release-curseforge-file-v1"
LOCK_FORMAT = "workbench-pack-release-curseforge-source-lock-v1"
RESULT_FORMAT = "workbench-pack-release-curseforge-result-v1"
_PLAN_PREFIX = "workbench-pack-release-curseforge-acquisition-plan:sha256:"
_PLAN_ID = re.compile(r"workbench-pack-release-curseforge-acquisition-plan:sha256:[0-9a-f]{64}\Z")
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_INPUT_ID = re.compile(r"workbench-pack-release-input-plan:sha256:[0-9a-f]{64}\Z")
_RELEASE_ID = re.compile(r"(?:profile-release|github-release):sha256:[0-9a-f]{64}\Z")
_POLICY_ID = re.compile(r"workbench-pack-release-resourcepack-policy:sha256:[0-9a-f]{64}\Z")
_API_ROOT = "https://api.curseforge.com/v1/mods"
_MAX_RESPONSE = 256 * 1024
_MAX_RECEIPT = 16 * 1024
_MAX_LOCK = 2 * 1024 * 1024
_CHUNK = 1024 * 1024
_GAME_ID = 432


class CurseForgeAccessUnavailable(ValueError):
    """Workbench has no authorized CurseForge API access for new downloads."""


class CurseForgeSourceUnavailable(ValueError):
    """An exact selected file has no downloadable authorized source."""


def _object(raw: bytes, label: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in pairs:
            if name in result:
                raise ValueError(f"{label} repeats a JSON key")
            result[name] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if type(value) is not dict:
        raise ValueError(f"{label} must be a JSON object")
    return value


def _selected_plan(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    if (type(plan) is not dict or set(plan) != {
            "format", "schema_version", "profile", "input_plan_id", "release_id",
            "version", "asset_sha256", "manifest_sha256", "policy_id", "source_kind",
            "resourcepack_max_file_bytes", "resourcepack_max_total_bytes",
            "files", "required_count", "optional_count", "plan_id"}
            or plan["format"] != PLAN_FORMAT or plan["schema_version"] != 1
            or plan["profile"] != "supersymmetry"
            or plan["source_kind"] != "curseforge-authorized-api"
            or type(plan["input_plan_id"]) is not str
            or _INPUT_ID.fullmatch(plan["input_plan_id"]) is None
            or type(plan["release_id"]) is not str
            or _RELEASE_ID.fullmatch(plan["release_id"]) is None
            or type(plan["asset_sha256"]) is not str
            or _SHA256.fullmatch(plan["asset_sha256"]) is None
            or type(plan["manifest_sha256"]) is not str
            or _SHA256.fullmatch(plan["manifest_sha256"]) is None
            or type(plan["policy_id"]) is not str
            or _POLICY_ID.fullmatch(plan["policy_id"]) is None
            or type(plan["resourcepack_max_file_bytes"]) is not int
            or not 0 < plan["resourcepack_max_file_bytes"] <= 536870912
            or type(plan["resourcepack_max_total_bytes"]) is not int
            or not plan["resourcepack_max_file_bytes"] <= plan["resourcepack_max_total_bytes"] <= 1610612736
            or type(plan["plan_id"]) is not str
            or _PLAN_ID.fullmatch(plan["plan_id"]) is None
            or plan["plan_id"] != _PLAN_PREFIX + sha256(_canonical({
                key: value for key, value in plan.items() if key != "plan_id"
            })).hexdigest()
            or type(plan["files"]) is not list
            or not 0 < len(plan["files"]) <= 10_000):
        raise ValueError("CurseForge acquisition plan is invalid or changed")
    keys: set[tuple[int, int]] = set()
    required = optional = 0
    for row in plan["files"]:
        if (type(row) is not dict
                or set(row) != {"project_id", "file_id", "required", "destination_root"}
                or type(row["project_id"]) is not int or not 0 < row["project_id"] < 2**63
                or type(row["file_id"]) is not int or not 0 < row["file_id"] < 2**63
                or type(row["required"]) is not bool
                or row["destination_root"] not in {"mods", "resourcepacks"}):
            raise ValueError("CurseForge acquisition plan has an invalid file row")
        key = row["project_id"], row["file_id"]
        if key in keys:
            raise ValueError("CurseForge acquisition plan repeats a file ID")
        keys.add(key)
        required += row["required"]
        optional += not row["required"]
    if (plan["required_count"] != required or plan["optional_count"] != optional
            or plan["files"] != sorted(plan["files"], key=lambda row: (row["project_id"], row["file_id"]))):
        raise ValueError("CurseForge acquisition plan inventory changed")
    return plan["files"]


def plan_curseforge_acquisition(
    input_plan: Mapping[str, Any], *, resourcepack_policy_path: Path,
    optional_selected: tuple[tuple[int, int], ...] = (),
) -> dict[str, Any]:
    """Select every required ID and explicit optional IDs, without a credential."""

    if type(input_plan) is not dict:
        raise ValueError("selected release input plan is invalid")
    policy = load_resourcepack_policy(resourcepack_policy_path)
    declarations = input_plan.get("external_files")
    if (input_plan.get("format") != "workbench-pack-release-input-plan-v1"
            or type(declarations) is not list
            or input_plan.get("plan_id") != policy["input_plan_id"]
            or input_plan.get("version") != policy["version"]
            or input_plan.get("manifest_sha256") != policy["manifest_sha256"]
            or len(declarations) != policy["external_file_count"]
            or input_plan["plan_id"] != "workbench-pack-release-input-plan:sha256:"
            + sha256(_canonical({key: value for key, value in input_plan.items()
                                 if key != "plan_id"})).hexdigest()):
        raise ValueError("resource-pack placement belongs to another selected release")
    declared: dict[tuple[int, int], bool] = {}
    for row in declarations:
        if (type(row) is not dict or set(row) != {"project_id", "file_id", "required"}
                or type(row["project_id"]) is not int or not 0 < row["project_id"] < 2**63
                or type(row["file_id"]) is not int or not 0 < row["file_id"] < 2**63
                or type(row["required"]) is not bool):
            raise ValueError("selected release has an invalid external file ID")
        key = row["project_id"], row["file_id"]
        if key in declared:
            raise ValueError("selected release repeats an external file ID")
        declared[key] = row["required"]
    if (type(optional_selected) is not tuple
            or any(type(pair) is not tuple or len(pair) != 2
                   or any(type(item) is not int for item in pair)
                   for pair in optional_selected)):
        raise ValueError("CurseForge acquisition has an invalid optional selection")
    chosen = set(optional_selected)
    if (len(chosen) != len(optional_selected)
            or any(key not in declared or declared[key] for key in chosen)):
        raise ValueError("CurseForge acquisition has an invalid optional selection")
    resourcepacks = {(row["project_id"], row["file_id"])
                     for row in policy["placements"]}
    if any(declared.get(key) is not True for key in resourcepacks):
        raise ValueError("resource-pack placement is absent from required release files")
    rows = [
        {"project_id": project_id, "file_id": file_id, "required": required,
         "destination_root": "resourcepacks" if (project_id, file_id) in resourcepacks else "mods"}
        for (project_id, file_id), required in sorted(declared.items())
        if required or (project_id, file_id) in chosen
    ]
    body = {
        "format": PLAN_FORMAT, "schema_version": 1, "profile": "supersymmetry",
        "input_plan_id": input_plan["plan_id"], "release_id": input_plan["release_id"],
        "version": input_plan["version"], "asset_sha256": input_plan["asset_sha256"],
        "manifest_sha256": input_plan["manifest_sha256"],
        "policy_id": "workbench-pack-release-resourcepack-policy:sha256:"
        + sha256(_canonical(policy)).hexdigest(),
        "resourcepack_max_file_bytes": policy["max_file_bytes"],
        "resourcepack_max_total_bytes": policy["max_total_bytes"],
        "source_kind": "curseforge-authorized-api", "files": rows,
        "required_count": sum(row["required"] for row in rows),
        "optional_count": sum(not row["required"] for row in rows),
    }
    plan = {**body, "plan_id": _PLAN_PREFIX + sha256(_canonical(body)).hexdigest()}
    _selected_plan(plan)
    return plan


def _provider_key(value: str | None) -> str:
    if value is None:
        raise CurseForgeAccessUnavailable("Workbench CurseForge access is not configured")
    if (type(value) is not str or not 1 <= len(value) <= 512
            or any(not 33 <= ord(character) <= 126 for character in value)):
        raise CurseForgeAccessUnavailable("Workbench CurseForge credential is unavailable")
    return value


class _NoApiRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise CurseForgeSourceUnavailable("CurseForge API redirected an exact file request")


def _api_response(url: str, key: str) -> dict[str, Any]:
    request = Request(url, headers={
        "Accept": "application/json", "x-api-key": key,
        "User-Agent": "Workbench-CurseForge-Release/0.1",
    })
    try:
        with build_opener(_NoApiRedirect()).open(request, timeout=15.0) as response:
            if response.geturl() != url:
                raise CurseForgeSourceUnavailable("CurseForge API changed its file URL")
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > _MAX_RESPONSE:
                raise CurseForgeSourceUnavailable("CurseForge file response is too large")
            raw = response.read(_MAX_RESPONSE + 1)
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise CurseForgeAccessUnavailable("Workbench CurseForge access was rejected") from exc
        raise CurseForgeSourceUnavailable("CurseForge file metadata is unavailable") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise CurseForgeSourceUnavailable("CurseForge file metadata is unavailable") from exc
    if len(raw) > _MAX_RESPONSE:
        raise CurseForgeSourceUnavailable("CurseForge file response is too large")
    try:
        return _object(raw, "CurseForge file response")
    except ValueError as exc:
        raise CurseForgeSourceUnavailable("CurseForge returned invalid file metadata") from exc


def fetch_curseforge_file_metadata(project_id: int, file_id: int, key: str) -> dict[str, Any]:
    """Use Workbench-authorized API access for one exact project/file pair."""
    return _api_response(f"{_API_ROOT}/{project_id}/files/{file_id}", key)


def fetch_curseforge_download_url(project_id: int, file_id: int, key: str) -> str | None:
    value = _api_response(f"{_API_ROOT}/{project_id}/files/{file_id}/download-url", key)
    result = value.get("data")
    if result is not None and type(result) is not str:
        raise CurseForgeSourceUnavailable("CurseForge returned an invalid download URL")
    return result


def _cdn_url(url: str) -> str:
    if type(url) is not str or len(url) > 4096:
        raise CurseForgeSourceUnavailable("CurseForge file has no admitted download URL")
    try:
        parsed = urlsplit(url)
        host, port = parsed.hostname, parsed.port
    except ValueError as exc:
        raise CurseForgeSourceUnavailable("CurseForge file has an invalid download URL") from exc
    if (parsed.scheme != "https" or host is None
            or not host.endswith(".forgecdn.net")
            or parsed.username is not None or parsed.password is not None
            or port not in {None, 443} or parsed.fragment
            or not parsed.path.startswith("/files/")):
        raise CurseForgeSourceUnavailable("CurseForge file download URL is outside its admitted CDN")
    return url


def _metadata(payload: dict[str, Any], project_id: int, file_id: int,
              destination_root: str) -> dict[str, Any]:
    data = payload.get("data") if type(payload) is dict else None
    if (type(data) is not dict or data.get("id") != file_id
            or data.get("modId") != project_id or data.get("gameId") != _GAME_ID
            or type(data.get("isAvailable")) is not bool
            or type(data.get("fileLength")) is not int
            or not 0 < data["fileLength"] <= 536870912):
        raise CurseForgeSourceUnavailable("CurseForge metadata differs from the selected file ID")
    try:
        filename = _filename(data.get("fileName"),
                             [".zip"] if destination_root == "resourcepacks" else [".jar", ".zip"])
    except ValueError as exc:
        raise CurseForgeSourceUnavailable("CurseForge file has an unsafe destination name") from exc
    hashes = data.get("hashes")
    if type(hashes) is not list or len(hashes) > 8:
        raise CurseForgeSourceUnavailable("CurseForge file has no bounded hash list")
    sha1_values = [row.get("value") for row in hashes
                   if type(row) is dict and row.get("algo") == 1]
    if (len(sha1_values) != 1 or type(sha1_values[0]) is not str
            or _SHA1.fullmatch(sha1_values[0]) is None):
        raise CurseForgeSourceUnavailable("CurseForge file has no unique SHA-1")
    url = data.get("downloadUrl")
    if url is not None:
        _cdn_url(url)
    return {"project_id": project_id, "file_id": file_id,
            "filename": filename, "size": data["fileLength"],
            "sha1": sha1_values[0], "download_url": url,
            "available": data["isAvailable"]}


class _CdnRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        _cdn_url(newurl)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def _open_download(url: str) -> ContextManager[BinaryIO]:
    request = Request(_cdn_url(url), headers={
        "Accept": "application/octet-stream",
        "User-Agent": "Workbench-CurseForge-Release/0.1",
    })
    return build_opener(_CdnRedirect()).open(request, timeout=30.0)


def _download_file(
    metadata: Mapping[str, Any], *, state_root: Path,
    opener: Callable[[str], ContextManager[BinaryIO]],
    check_cancelled: Callable[[], None],
) -> tuple[Path, str]:
    staging = state_root / "pack-release-curseforge" / "staging"
    _private_directory(staging)
    if not private_path(staging, directory=True):
        raise ValueError("CurseForge download staging must be owner-private")
    descriptor, name = tempfile.mkstemp(prefix=".file-", dir=staging)
    temporary = Path(name)
    one, two, count = sha1(), sha256(), 0
    try:
        with os.fdopen(descriptor, "wb") as destination:
            descriptor = -1
            with opener(metadata["download_url"]) as source:
                final_url = source.geturl() if hasattr(source, "geturl") else metadata["download_url"]
                if final_url is None:
                    final_url = metadata["download_url"]
                _cdn_url(final_url)
                while block := source.read(_CHUNK):
                    check_cancelled()
                    count += len(block)
                    if count > metadata["size"]:
                        raise CurseForgeSourceUnavailable("CurseForge download exceeds its declared size")
                    one.update(block)
                    two.update(block)
                    destination.write(block)
            destination.flush()
            os.fsync(destination.fileno())
        if count != metadata["size"] or one.hexdigest() != metadata["sha1"]:
            raise CurseForgeSourceUnavailable("CurseForge download differs from its API size or SHA-1")
        path, _ = fetch_verified_artifact(
            url=temporary.as_uri(), expected_sha256=two.hexdigest(),
            expected_size=count, state_root=state_root,
            label="selected CurseForge release file", timeout_seconds=30.0,
        )
        return path, "sha256:" + two.hexdigest()
    except (ArtifactStoreError, HTTPError, URLError, TimeoutError, OSError) as exc:
        raise CurseForgeSourceUnavailable("CurseForge file download failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _file_row(plan: Mapping[str, Any], project_id: int, file_id: int) -> dict[str, Any]:
    if type(project_id) is not int or type(file_id) is not int:
        raise ValueError("CurseForge file selection needs exact numeric IDs")
    for row in _selected_plan(plan):
        if (row["project_id"], row["file_id"]) == (project_id, file_id):
            return row
    raise ValueError("CurseForge file ID is not selected by this release plan")


def _state_root(value: Path) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise ValueError("CurseForge acquisition needs an absolute Core state root")
    if any(part in {".", ".."} for part in value.parts):
        raise ValueError("CurseForge acquisition state root is not canonical")
    if _mount_type(value) not in _SUPPORTED_FILESYSTEMS:
        raise ValueError("CurseForge acquisition needs a qualified Linux filesystem")
    return value


def _receipt_path(plan: Mapping[str, Any], state_root: Path,
                  project_id: int, file_id: int) -> Path:
    return (state_root / "pack-release-curseforge" / "receipts"
            / plan["plan_id"].rsplit(":", 1)[-1] / f"{project_id}-{file_id}.json")


def _measure(path: Path, *, size: int, sha1_hex: str, sha256_hex: str,
             check_cancelled: Callable[[], None]) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("retained CurseForge artifact is unavailable")
    visible = path.lstat()
    if not stat.S_ISREG(visible.st_mode) or visible.st_size != size:
        raise ValueError("retained CurseForge artifact changed size or type")
    one, two, count = sha1(), sha256(), 0
    with path.open("rb") as source:
        opened = os.fstat(source.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
                visible.st_dev, visible.st_ino, visible.st_size):
            raise ValueError("retained CurseForge artifact changed before review")
        while block := source.read(_CHUNK):
            check_cancelled()
            count += len(block)
            if count > size:
                raise ValueError("retained CurseForge artifact grew during review")
            one.update(block)
            two.update(block)
        after = os.fstat(source.fileno())
    final = path.lstat()
    if (count != size or one.hexdigest() != sha1_hex or two.hexdigest() != sha256_hex
            or any((item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns,
                    item.st_ctime_ns, item.st_nlink) !=
                   (visible.st_dev, visible.st_ino, visible.st_size, visible.st_mtime_ns,
                    visible.st_ctime_ns, visible.st_nlink)
                   for item in (opened, after, final))):
        raise ValueError("retained CurseForge artifact bytes or custody changed")


def _receipt_value(plan: Mapping[str, Any], row: Mapping[str, Any],
                   metadata: Mapping[str, Any], sha256_value: str) -> dict[str, Any]:
    return {
        "format": FILE_FORMAT, "schema_version": 1,
        "plan_id": plan["plan_id"], "input_plan_id": plan["input_plan_id"],
        "project_id": row["project_id"], "file_id": row["file_id"],
        "required": row["required"], "destination_root": row["destination_root"],
        "filename": metadata["filename"], "size": metadata["size"],
        "sha1": metadata["sha1"], "sha256": sha256_value,
        "source": "curseforge-authorized-api",
    }


def reopen_curseforge_file(
    plan: Mapping[str, Any], *, project_id: int, file_id: int,
    state_root: Path, check_cancelled: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Reverify retained bytes without a credential or original download URL."""
    row = _file_row(plan, project_id, file_id)
    root = _state_root(state_root)
    receipt = _receipt_path(plan, root, project_id, file_id)
    try:
        value = _object(read_private_single_link_bytes(receipt, byte_limit=_MAX_RECEIPT),
                        "CurseForge file receipt")
    except DurableRecordError as exc:
        raise ValueError("CurseForge file has no retained Core receipt") from exc
    if (set(value) != {"format", "schema_version", "plan_id", "input_plan_id",
                       "project_id", "file_id", "required", "destination_root",
                       "filename", "size", "sha1", "sha256", "source"}
            or value["format"] != FILE_FORMAT or value["schema_version"] != 1
            or value["plan_id"] != plan["plan_id"]
            or value["input_plan_id"] != plan["input_plan_id"]
            or any(value[key] != row[key] for key in (
                "project_id", "file_id", "required", "destination_root"))
            or value["source"] != "curseforge-authorized-api"
            or type(value["size"]) is not int or not 0 < value["size"] <= 536870912
            or type(value["sha1"]) is not str or _SHA1.fullmatch(value["sha1"]) is None
            or type(value["sha256"]) is not str or _SHA256.fullmatch(value["sha256"]) is None):
        raise ValueError("CurseForge file receipt changed or names another selection")
    _filename(value["filename"], [".zip"] if row["destination_root"] == "resourcepacks"
              else [".jar", ".zip"])
    path = root / "artifacts" / "sha256" / value["sha256"].removeprefix("sha256:")
    _measure(path, size=value["size"], sha1_hex=value["sha1"],
             sha256_hex=value["sha256"].removeprefix("sha256:"),
             check_cancelled=check_cancelled)
    return {**value, "status": "ready", "artifact_path": str(path)}


def acquire_curseforge_file(
    plan: Mapping[str, Any], *, project_id: int, file_id: int,
    state_root: Path, provider_key: str | None = None,
    metadata_fetcher: Callable[[int, int, str], dict[str, Any]] = fetch_curseforge_file_metadata,
    download_url_fetcher: Callable[[int, int, str], str | None] = fetch_curseforge_download_url,
    download_opener: Callable[[str], ContextManager[BinaryIO]] = _open_download,
    check_cancelled: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Retain one selected file; Textual can call this once per progress row."""
    row = _file_row(plan, project_id, file_id)
    root = _state_root(state_root)
    receipt = _receipt_path(plan, root, project_id, file_id)
    if receipt.exists() or receipt.is_symlink():
        return {**reopen_curseforge_file(
            plan, project_id=project_id, file_id=file_id, state_root=root,
            check_cancelled=check_cancelled,
        ), "outcome": "reused"}
    key = _provider_key(provider_key)
    check_cancelled()
    metadata = _metadata(metadata_fetcher(project_id, file_id, key), project_id,
                         file_id, row["destination_root"])
    if (row["destination_root"] == "resourcepacks"
            and metadata["size"] > plan["resourcepack_max_file_bytes"]):
        raise CurseForgeSourceUnavailable("selected resource pack exceeds profile policy")
    if not metadata["available"]:
        raise CurseForgeSourceUnavailable("selected CurseForge file is not available")
    url = metadata["download_url"]
    if url is None:
        url = download_url_fetcher(project_id, file_id, key)
    if url is None:
        raise CurseForgeSourceUnavailable("selected CurseForge file has no authorized download URL")
    metadata["download_url"] = _cdn_url(url)
    check_cancelled()
    path, digest = _download_file(
        metadata, state_root=root, opener=download_opener,
        check_cancelled=check_cancelled,
    )
    value = _receipt_value(plan, row, metadata, digest)
    _private_directory(receipt.parent)
    try:
        replace_private_bytes(receipt, _canonical(value) + b"\n",
                              byte_limit=_MAX_RECEIPT, require_absent=True)
    except DurableRecordError as exc:
        if exc.code != "stale":
            raise
        existing = reopen_curseforge_file(
            plan, project_id=project_id, file_id=file_id, state_root=root,
            check_cancelled=check_cancelled,
        )
        if any(existing[key] != value[key] for key in value):
            raise ValueError("another CurseForge download retained different bytes") from exc
        return {**existing, "outcome": "reused"}
    _measure(path, size=value["size"], sha1_hex=value["sha1"],
             sha256_hex=digest.removeprefix("sha256:"), check_cancelled=check_cancelled)
    return {**value, "status": "ready", "artifact_path": str(path),
            "outcome": "downloaded"}


def _host(plan: Mapping[str, Any], state_root: Path,
          config_home: Path) -> tuple[CoreManagedTrees, Path]:
    root = _state_root(state_root)
    if not isinstance(config_home, Path) or not config_home.is_absolute():
        raise ValueError("CurseForge publication needs an absolute Core config home")
    output = root / "pack-release-external-inputs"
    return CoreManagedTrees(
        workspace=root, configuration_home=config_home,
        locations={"artifacts": output}, owner_id="supersymmetry",
        policy_id=plan["policy_id"],
        location_sources={"artifacts": "pack-release-curseforge-acquisition"},
    ), output


def _target(root: Path, plan: Mapping[str, Any]) -> Path:
    return root / plan["plan_id"].rsplit(":", 1)[-1] / "snapshot"


def _tree_state(host: CoreManagedTrees, target: Path,
                plan: Mapping[str, Any]) -> tuple[str, str | None]:
    rows = [row for row in host.catalog.trees.inventory() if row["path"] == str(target)]
    if len(rows) > 1:
        raise ValueError("CurseForge tree has ambiguous Core reservations")
    if not rows:
        if target.exists() or target.is_symlink():
            raise ValueError("CurseForge tree target exists outside Core custody")
        return "acquire", None
    row = rows[0]
    if row["owner_id"] != host.owner_id or row["workspace"] != str(host.workspace):
        raise ValueError("CurseForge tree belongs to another Core owner")
    if row["status"] != "committed":
        raise ValueError("CurseForge tree has an incomplete stage requiring review")
    reference = host.describe(str(row["tree_id"]))
    if (reference.domain_id != plan["plan_id"] or reference.path != target
            or reference.policy_id != plan["policy_id"]):
        raise ValueError("CurseForge tree retained another release selection")
    return "reuse", reference.tree_id


def _lock_rows(plan: Mapping[str, Any], state_root: Path,
               check_cancelled: Callable[[], None]) -> list[dict[str, Any]]:
    rows = []
    names: set[str] = set()
    total = 0
    resourcepack_total = 0
    for selected in _selected_plan(plan):
        check_cancelled()
        value = reopen_curseforge_file(
            plan, project_id=selected["project_id"], file_id=selected["file_id"],
            state_root=state_root, check_cancelled=check_cancelled,
        )
        relative = selected["destination_root"] + "/" + value["filename"]
        if relative.casefold() in names:
            raise ValueError("CurseForge selected files collide at an install destination")
        names.add(relative.casefold())
        total += value["size"]
        if selected["destination_root"] == "resourcepacks":
            resourcepack_total += value["size"]
            if (value["size"] > plan["resourcepack_max_file_bytes"]
                    or resourcepack_total > plan["resourcepack_max_total_bytes"]):
                raise ValueError("selected resource packs exceed profile policy")
        if total > 8589934592:
            raise ValueError("CurseForge selected files exceed their retained byte bound")
        rows.append({
            "project_id": selected["project_id"], "file_id": selected["file_id"],
            "required": selected["required"],
            "destination_root": selected["destination_root"],
            "relative_path": relative, "filename": value["filename"],
            "size": value["size"], "sha1": value["sha1"],
            "sha256": value["sha256"],
        })
    return rows


def _source_lock(plan: Mapping[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "format": LOCK_FORMAT, "schema_version": 1,
        "plan_id": plan["plan_id"], "input_plan_id": plan["input_plan_id"],
        "release_id": plan["release_id"], "policy_id": plan["policy_id"],
        "source_kind": plan["source_kind"], "files": rows,
        "file_count": len(rows), "total_bytes": sum(row["size"] for row in rows),
    }


def _validate_lock(plan: Mapping[str, Any], lock: Mapping[str, Any]) -> list[dict[str, Any]]:
    if (type(lock) is not dict
            or set(lock) != {"format", "schema_version", "plan_id", "input_plan_id",
                             "release_id", "policy_id", "source_kind", "files",
                             "file_count", "total_bytes"}
            or lock["format"] != LOCK_FORMAT or lock["schema_version"] != 1
            or any(lock[key] != plan[key] for key in (
                "plan_id", "input_plan_id", "release_id", "policy_id", "source_kind"))
            or type(lock["files"]) is not list
            or len(lock["files"]) != len(_selected_plan(plan))
            or lock["file_count"] != len(lock["files"])):
        raise ValueError("CurseForge tree source lock changed")
    names: set[str] = set()
    total = 0
    resourcepack_total = 0
    for selected, row in zip(plan["files"], lock["files"]):
        if (type(row) is not dict
                or set(row) != {"project_id", "file_id", "required", "destination_root",
                                "relative_path", "filename", "size", "sha1", "sha256"}
                or any(row[key] != selected[key] for key in (
                    "project_id", "file_id", "required", "destination_root"))
                or type(row["size"]) is not int or not 0 < row["size"] <= 536870912
                or type(row["sha1"]) is not str or _SHA1.fullmatch(row["sha1"]) is None
                or type(row["sha256"]) is not str or _SHA256.fullmatch(row["sha256"]) is None):
            raise ValueError("CurseForge tree source lock has an invalid file")
        _filename(row["filename"], [".zip"] if row["destination_root"] == "resourcepacks"
                  else [".jar", ".zip"])
        relative = row["destination_root"] + "/" + row["filename"]
        if row["relative_path"] != relative or relative.casefold() in names:
            raise ValueError("CurseForge tree source lock has a destination collision")
        names.add(relative.casefold())
        total += row["size"]
        if row["destination_root"] == "resourcepacks":
            resourcepack_total += row["size"]
            if (row["size"] > plan["resourcepack_max_file_bytes"]
                    or resourcepack_total > plan["resourcepack_max_total_bytes"]):
                raise ValueError("CurseForge tree resource packs exceed profile policy")
    if total != lock["total_bytes"] or total > 8589934592:
        raise ValueError("CurseForge tree source lock byte total changed")
    return lock["files"]


def _validate_stage(stage: Path, plan: Mapping[str, Any],
                    check_cancelled: Callable[[], None]) -> dict[str, Any]:
    raw = read_private_single_link_bytes(stage / "source-lock.json", byte_limit=_MAX_LOCK)
    lock = _object(raw, "CurseForge tree source lock")
    if raw != _canonical(lock) + b"\n":
        raise ValueError("CurseForge tree source lock encoding changed")
    rows = _validate_lock(plan, lock)
    expected_files = {row["relative_path"] for row in rows} | {"source-lock.json"}
    expected_directories = {row["destination_root"] for row in rows}
    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    for path in stage.rglob("*"):
        check_cancelled()
        relative = path.relative_to(stage).as_posix()
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            observed_directories.add(relative)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            observed_files.add(relative)
        else:
            raise ValueError("CurseForge tree has a link or special member")
    if observed_files != expected_files or observed_directories != expected_directories:
        raise ValueError("CurseForge tree has missing or extra members")
    for row in rows:
        _measure(stage / row["relative_path"], size=row["size"],
                 sha1_hex=row["sha1"], sha256_hex=row["sha256"].removeprefix("sha256:"),
                 check_cancelled=check_cancelled)
    return lock


def _copy_stage(stage: Path, plan: Mapping[str, Any], rows: list[dict[str, Any]],
                state_root: Path, check_cancelled: Callable[[], None]) -> None:
    stage.mkdir(mode=0o700)
    for root in {row["destination_root"] for row in rows}:
        (stage / root).mkdir(mode=0o700)
    for row in rows:
        check_cancelled()
        source = state_root / "artifacts" / "sha256" / row["sha256"].removeprefix("sha256:")
        _measure(source, size=row["size"], sha1_hex=row["sha1"],
                 sha256_hex=row["sha256"].removeprefix("sha256:"),
                 check_cancelled=check_cancelled)
        target = stage / row["relative_path"]
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with source.open("rb") as input_file:
                one, two, count = sha1(), sha256(), 0
                while block := input_file.read(_CHUNK):
                    check_cancelled()
                    count += len(block)
                    if count > row["size"]:
                        raise ValueError("CurseForge cache source grew during publication")
                    one.update(block)
                    two.update(block)
                    remaining = memoryview(block)
                    while remaining:
                        written = os.write(descriptor, remaining)
                        if written <= 0:
                            raise OSError("CurseForge tree write did not advance")
                        remaining = remaining[written:]
                if (count != row["size"] or one.hexdigest() != row["sha1"]
                        or "sha256:" + two.hexdigest() != row["sha256"]):
                    raise ValueError("CurseForge cache source changed during publication")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    lock = _source_lock(plan, rows)
    raw = _canonical(lock) + b"\n"
    if len(raw) > _MAX_LOCK:
        raise ValueError("CurseForge source lock exceeds its bound")
    descriptor = os.open(stage / "source-lock.json",
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(descriptor)


def reopen_curseforge_acquisition(
    plan: Mapping[str, Any], *, state_root: Path, config_home: Path,
    check_cancelled: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Reopen a complete tree without provider access or per-file receipts."""
    _selected_plan(plan)
    host, root = _host(plan, state_root, config_home)
    target = _target(root, plan)
    action, tree_id = _tree_state(host, target, plan)
    if action != "reuse" or tree_id is None:
        raise ValueError("CurseForge acquisition has no complete Core tree")
    reference: ManagedTreeReference = host.describe(tree_id)
    if (reference.path != target or reference.workspace != host.workspace
            or reference.owner_id != host.owner_id or reference.role != "artifacts"
            or reference.policy_id != plan["policy_id"]
            or reference.domain_id != plan["plan_id"]
            or reference.inventory_policy != EXACT_INVENTORY_POLICY
            or reference.derived_status != "current"):
        raise ValueError("CurseForge acquisition reopened another Core tree")
    lock = _validate_stage(reference.path, plan, check_cancelled)
    return {
        "format": RESULT_FORMAT, "schema_version": 1, "status": "ready",
        "plan_id": plan["plan_id"], "input_plan_id": plan["input_plan_id"],
        "tree_id": reference.tree_id,
        "tree_content_sha256": reference.content_sha256,
        "tree_path": str(reference.path),
        "file_count": lock["file_count"], "total_bytes": lock["total_bytes"],
        "files": lock["files"],
    }


def publish_curseforge_acquisition(
    plan: Mapping[str, Any], *, state_root: Path, config_home: Path,
    expected_plan_id: str, check_cancelled: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Publish all selected external bytes together, or publish nothing."""
    _selected_plan(plan)
    if plan["plan_id"] != expected_plan_id:
        raise ValueError("CurseForge acquisition changed after Textual review")
    host, root = _host(plan, state_root, config_home)
    target = _target(root, plan)
    _private_directory(target.parent)
    if not private_path(target.parent, directory=True):
        raise ValueError("CurseForge tree destination must be owner-private")
    lock_path = root / (".curseforge-" + expected_plan_id.rsplit(":", 1)[-1] + ".lock")
    with private_record_lock(lock_path, wait=True):
        action, tree_id = _tree_state(host, target, plan)
        if action == "reuse":
            result = reopen_curseforge_acquisition(
                plan, state_root=state_root, config_home=config_home,
                check_cancelled=check_cancelled,
            )
            return {**result, "outcome": "reused"}
        rows = _lock_rows(plan, state_root, check_cancelled)
        with host.stage("artifacts", target.name, requested_path=target) as stage:
            _copy_stage(stage.path, plan, rows, state_root, check_cancelled)
            stage.publish(
                validate=lambda path: _validate_stage(path, plan, check_cancelled),
                domain_id=plan["plan_id"], inventory_policy=EXACT_INVENTORY_POLICY,
            )
        result = reopen_curseforge_acquisition(
            plan, state_root=state_root, config_home=config_home,
            check_cancelled=check_cancelled,
        )
        return {**result, "outcome": "published"}


__all__ = [
    "PLAN_FORMAT", "FILE_FORMAT", "LOCK_FORMAT", "RESULT_FORMAT",
    "CurseForgeAccessUnavailable", "CurseForgeSourceUnavailable",
    "plan_curseforge_acquisition", "acquire_curseforge_file",
    "reopen_curseforge_file", "publish_curseforge_acquisition",
    "reopen_curseforge_acquisition",
]
