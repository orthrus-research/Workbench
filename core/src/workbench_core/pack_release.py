"""Supersymmetry release choice and verified client download custody.

The pack profile declares the release authority. Core owns the network boundary,
per-user choice, and content-addressed artifact; this does not install a pack.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, BinaryIO, Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile, ZipInfo

from packaging.version import Version
from workbench_api.durable_resources import DurableResourceError
from workbench_api.host_filesystem import DurableRecordError
from workbench_api.profiles import profile_resources
from workbench_api.state_paths import default_runtime_state_root

from .artifact_store import ArtifactStoreError, fetch_verified_artifact, sha256_file
from .durable_records import read_bounded_bytes, read_private_bytes
from .preference_records import update_preference_bytes
from .user_config_home import default_user_config_home


SCHEMA = "workbench.pack-release.v1"
INPUT_PLAN_FORMAT = "workbench-pack-release-input-plan-v1"
CHOICE_FORMAT = "workbench-pack-release-choice-v1"
AUTHORITY_FORMAT = "workbench-supersymmetry-release-authority-v1"
REPOSITORY = "SymmetricDevs/Supersymmetry"
PROFILE = "supersymmetry"
MAX_AUTHORITY_BYTES = 64 * 1024
MAX_API_BYTES = 1024 * 1024
MAX_CHOICE_BYTES = 16 * 1024
MAX_ASSET_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 512 * 1024
MAX_EXTERNAL_FILES = 10_000
_TAG = re.compile(r"[0-9]+(?:\.[0-9]+){3}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RELEASE_ID = re.compile(r"github-release:sha256:[0-9a-f]{64}\Z")
_SELECTION_ID = re.compile(r"(?:github-release|profile-release):sha256:[0-9a-f]{64}\Z")


class ReleaseUnavailable(ValueError):
    """Latest release metadata or the selected client asset is unavailable."""


class ReleaseStale(ValueError):
    """The reviewed release or saved choice changed before an action."""


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"{label} is not valid JSON: {exc}") from exc
    if type(value) is not dict:
        raise ValueError(f"{label} must be a JSON object")
    return value


def _positive_int(value: Any, label: str, *, maximum: int | None = None) -> int:
    if type(value) is not int or value <= 0 or (maximum is not None and value > maximum):
        raise ValueError(f"{label} must be a bounded positive integer")
    return value


def _digest(value: Any, label: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a SHA-256 digest")
    return value


def _tag(value: Any) -> str:
    if type(value) is not str or len(value) > 64 or _TAG.fullmatch(value) is None:
        raise ValueError("release tag must be a four-part numeric pack version")
    return value


def _published_at(value: Any) -> str:
    if type(value) is not str or not value.endswith("Z") or len(value) > 40:
        raise ValueError("release publication time is invalid")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("release publication time is invalid") from exc
    return value


@dataclass(frozen=True)
class ReleaseAuthority:
    api_url: str
    baseline_version: str
    baseline_sha256: str
    baseline_size: int
    baseline_asset_name: str
    baseline_asset_url: str
    baseline_release_url: str
    baseline_published_at: str
    asset_pattern: re.Pattern[str]
    minecraft_version: str
    mod_loader: str


def load_authority(path: Path) -> ReleaseAuthority:
    """Read the admitted profile's bounded release authority, never an arbitrary URL."""

    try:
        value = _json_object(read_bounded_bytes(path, byte_limit=MAX_AUTHORITY_BYTES),
                             "release authority")
    except DurableRecordError as exc:
        raise ValueError(f"release authority is not a bounded regular profile resource: {exc}") from exc
    repository = value.get("repository")
    baseline = value.get("baseline_release")
    policy = value.get("release_policy")
    if (value.get("format") != AUTHORITY_FORMAT or value.get("schema_version") != 1
            or value.get("profile_id") != "workbench-pack:supersymmetry"
            or type(repository) is not dict or type(baseline) is not dict
            or type(policy) is not dict):
        raise ValueError("Supersymmetry release authority is incompatible")
    api_url = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
    if repository != {"kind": "github", "name": REPOSITORY, "latest_release_api": api_url}:
        raise ValueError("Supersymmetry release repository is incompatible")
    baseline_tag = _tag(baseline.get("tag"))
    asset = baseline.get("client_asset")
    if type(asset) is not dict or asset.get("archive_format") != "curseforge-client-manifest-v1":
        raise ValueError("baseline client archive declaration is incompatible")
    baseline_sha256 = _digest(asset.get("sha256"), "baseline client asset")
    baseline_size = _positive_int(asset.get("size"), "baseline client asset size", maximum=MAX_ASSET_BYTES)
    if asset.get("name") != f"supersymmetry-{baseline_tag}.zip":
        raise ValueError("baseline client asset name is incompatible")
    baseline_release_url = f"https://github.com/{REPOSITORY}/releases/tag/{baseline_tag}"
    baseline_asset_url = f"https://github.com/{REPOSITORY}/releases/download/{baseline_tag}/{asset['name']}"
    if (baseline.get("release_url") != baseline_release_url
            or asset.get("url") != baseline_asset_url):
        raise ValueError("baseline GitHub release location is incompatible")
    baseline_published_at = _published_at(baseline.get("published_at"))
    pattern_text = policy.get("client_asset_name_pattern")
    if type(pattern_text) is not str or len(pattern_text) > 256:
        raise ValueError("client asset selection pattern is invalid")
    try:
        pattern = re.compile(pattern_text)
    except re.error as exc:
        raise ValueError("client asset selection pattern is invalid") from exc
    if "tag" not in pattern.groupindex or pattern.fullmatch(asset["name"]) is None:
        raise ValueError("client asset selection pattern does not admit the baseline")
    minecraft_version = asset.get("minecraft_version")
    mod_loader = asset.get("mod_loader")
    if minecraft_version != "1.12.2" or mod_loader != "forge-14.23.5.2860":
        raise ValueError("client archive game and loader declaration is incompatible")
    return ReleaseAuthority(
        api_url, baseline_tag, baseline_sha256, baseline_size, asset["name"],
        baseline_asset_url, baseline_release_url, baseline_published_at,
        pattern, minecraft_version, mod_loader,
    )


def fetch_latest_release(api_url: str) -> dict[str, Any]:
    """Read one bounded GitHub API response with an explicit socket timeout."""

    request = Request(api_url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Workbench-Pack-Release/0.1",
    })
    try:
        with urlopen(request, timeout=8.0) as response:
            if response.geturl() != api_url:
                raise ReleaseUnavailable("GitHub latest-release API redirected")
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > MAX_API_BYTES:
                raise ReleaseUnavailable("GitHub latest-release response exceeds its size limit")
            raw = response.read(MAX_API_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ReleaseUnavailable(f"GitHub latest release cannot be reached: {exc}") from exc
    except ReleaseUnavailable:
        raise
    except ValueError as exc:
        raise ReleaseUnavailable("GitHub latest-release response has an invalid size") from exc
    if len(raw) > MAX_API_BYTES:
        raise ReleaseUnavailable("GitHub latest-release response exceeds its size limit")
    try:
        return _json_object(raw, "GitHub latest-release response")
    except ValueError as exc:
        raise ReleaseUnavailable(str(exc)) from exc


def _candidate(authority: ReleaseAuthority, release: dict[str, Any]) -> dict[str, Any]:
    if type(release) is not dict:
        raise ReleaseUnavailable("GitHub latest release must be a JSON object")
    try:
        release_number = _positive_int(release.get("id"), "GitHub release ID")
        tag = _tag(release.get("tag_name"))
        published_at = _published_at(release.get("published_at"))
        if release.get("draft") is not False or release.get("prerelease") is not False:
            raise ValueError("GitHub latest release is draft or prerelease")
        release_url = f"https://github.com/{REPOSITORY}/releases/tag/{tag}"
        if release.get("html_url") != release_url:
            raise ValueError("GitHub release URL does not match the declared repository and tag")
        assets = release.get("assets")
        if type(assets) is not list or len(assets) > 128:
            raise ValueError("GitHub release asset list is invalid")
        matching = []
        for asset in assets:
            if type(asset) is not dict or type(asset.get("name")) is not str:
                raise ValueError("GitHub release asset metadata is invalid")
            name = asset["name"]
            match = authority.asset_pattern.fullmatch(name) if len(name) <= 256 else None
            if match is not None and match.group("tag") == tag:
                matching.append(asset)
        if len(matching) != 1:
            raise ValueError("GitHub release has no unique declared client ZIP asset")
        asset = matching[0]
        name = asset["name"]
        asset_number = _positive_int(asset.get("id"), "GitHub asset ID")
        size = _positive_int(asset.get("size"), "GitHub client asset size", maximum=MAX_ASSET_BYTES)
        digest = _digest(asset.get("digest"), "GitHub client asset digest")
        asset_url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"
        if asset.get("state") != "uploaded" or asset.get("browser_download_url") != asset_url:
            raise ValueError("GitHub client asset URL or upload state is invalid")
    except ValueError as exc:
        raise ReleaseUnavailable(str(exc)) from exc
    identity = {
        "repository": REPOSITORY, "release_number": release_number, "tag": tag,
        "published_at": published_at, "release_url": release_url,
        "asset_number": asset_number, "asset_name": name, "asset_size": size,
        "asset_sha256": digest, "asset_url": asset_url,
    }
    return {
        "release_id": "github-release:sha256:" + sha256(_json_bytes(identity)).hexdigest(),
        "version": tag, "tag": tag, "published_at": published_at,
        "release_url": release_url, "asset_name": name, "asset_size": size,
        "asset_sha256": digest, "asset_url": asset_url,
    }


def _verify_client_archive(
    source: Path | BinaryIO, authority: ReleaseAuthority, version: str,
) -> tuple[dict[str, Any], bytes, list[ZipInfo]]:
    """Inspect the manifest without extracting or trusting archive paths."""

    try:
        with ZipFile(source) as archive:
            entries = archive.infolist()
            if not 1 <= len(entries) <= MAX_ARCHIVE_ENTRIES:
                raise ValueError("client ZIP has an invalid entry count")
            seen: set[str] = set()
            total = 0
            for entry in entries:
                name = entry.filename
                if (not name or name in seen or name.startswith("/") or "\\" in name
                        or any(part in {"", ".", ".."} for part in name.rstrip("/").split("/"))
                        or entry.flag_bits & 1
                        or stat.S_IFMT(entry.external_attr >> 16) == stat.S_IFLNK):
                    raise ValueError("client ZIP has an unsafe or duplicate entry")
                seen.add(name)
                total += entry.file_size
                if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    raise ValueError("client ZIP expands beyond its size limit")
            if "manifest.json" not in seen or "overrides/" not in seen:
                raise ValueError("client ZIP lacks its root manifest or overrides")
            manifest_entry = archive.getinfo("manifest.json")
            if manifest_entry.file_size > MAX_MANIFEST_BYTES:
                raise ValueError("client ZIP manifest exceeds its size limit")
            with archive.open(manifest_entry) as stream:
                raw = stream.read(MAX_MANIFEST_BYTES + 1)
            if len(raw) > MAX_MANIFEST_BYTES:
                raise ValueError("client ZIP manifest exceeds its size limit")
            manifest = _json_object(raw, "client ZIP manifest")
    except (BadZipFile, OSError, RuntimeError) as exc:
        raise ValueError(f"client ZIP cannot be inspected: {exc}") from exc
    minecraft = manifest.get("minecraft")
    loaders = minecraft.get("modLoaders") if type(minecraft) is dict else None
    if (manifest.get("manifestType") != "minecraftModpack"
            or type(manifest.get("manifestVersion")) is not int
            or manifest["manifestVersion"] != 1
            or manifest.get("version") != version
            or type(minecraft) is not dict
            or minecraft.get("version") != authority.minecraft_version
            or type(loaders) is not list
            or not any(type(row) is dict and row.get("id") == authority.mod_loader
                       and row.get("primary") is True for row in loaders)
            or type(manifest.get("files")) is not list):
        raise ValueError("client ZIP manifest does not match the declared pack format")
    return manifest, raw, entries


def _input_plan(
    selected: dict[str, Any], manifest: dict[str, Any], manifest_raw: bytes,
    entries: list[ZipInfo],
) -> dict[str, Any]:
    """Describe exact external identifiers without treating them as acquired bytes."""

    files = manifest["files"]
    if manifest.get("overrides") != "overrides" or len(files) > MAX_EXTERNAL_FILES:
        raise ValueError("client ZIP manifest has unsupported external input declarations")
    external = []
    seen: set[tuple[int, int]] = set()
    for row in files:
        if (type(row) is not dict or set(row) != {"projectID", "fileID", "required"}
                or type(row["projectID"]) is not int
                or type(row["fileID"]) is not int
                or not 0 < row["projectID"] < 2**63
                or not 0 < row["fileID"] < 2**63
                or type(row["required"]) is not bool):
            raise ValueError("client ZIP manifest has an invalid external file declaration")
        key = (row["projectID"], row["fileID"])
        if key in seen:
            raise ValueError("client ZIP manifest repeats an external file declaration")
        seen.add(key)
        external.append({"project_id": key[0], "file_id": key[1],
                         "required": row["required"]})
    external.sort(key=lambda row: (row["project_id"], row["file_id"]))
    overrides = sum(entry.filename.startswith("overrides/") and not entry.is_dir()
                    for entry in entries)
    other_files = sum(not entry.is_dir()
                      and entry.filename != "manifest.json"
                      and not entry.filename.startswith("overrides/") for entry in entries)
    body = {
        "format": INPUT_PLAN_FORMAT, "schema_version": 1,
        "profile": PROFILE, "source_kind": "published-client-archive",
        "release_id": selected["release_id"], "version": selected["version"],
        "asset_sha256": selected["asset_sha256"], "asset_size": selected["asset_size"],
        "manifest_sha256": "sha256:" + sha256(manifest_raw).hexdigest(),
        "archive_member_count": len(entries),
        "override_file_count": overrides, "other_file_count": other_files,
        "external_files": external,
        "acquisition_state": (
            "external-file-bytes-unresolved" if external
            else "client-install-policy-unresolved"
        ),
    }
    return {**body, "plan_id": "workbench-pack-release-input-plan:sha256:"
            + sha256(_json_bytes(body)).hexdigest()}


class PackReleaseService:
    def __init__(
        self, authority: ReleaseAuthority, *, config_home: Path, state_root: Path,
        latest_fetcher: Callable[[str], dict[str, Any]] = fetch_latest_release,
        artifact_fetcher: Callable[..., tuple[Path, str]] = fetch_verified_artifact,
    ) -> None:
        self.authority = authority
        self.choice_path = config_home / "pack-release-supersymmetry.json"
        self.state_root = state_root
        self.latest_fetcher = latest_fetcher
        self.artifact_fetcher = artifact_fetcher

    def _read_choice(self, raw: bytes | None = None) -> dict[str, Any]:
        if raw is None:
            if not self.choice_path.exists() and not self.choice_path.is_symlink():
                if any(parent.is_symlink() for parent in
                       (self.choice_path.parent, *self.choice_path.parent.parents)):
                    raise ValueError("pack release choice directory is a symbolic link")
                return self._baseline_choice()
            try:
                raw = read_private_bytes(self.choice_path, byte_limit=MAX_CHOICE_BYTES)
            except DurableRecordError as exc:
                if exc.code != "unavailable" or self.choice_path.exists() or self.choice_path.is_symlink():
                    raise
                return self._baseline_choice()
        value = _json_object(raw, "pack release choice")
        selected = value.get("selected")
        ignored = value.get("ignored_release_id")
        if (value.get("format") != CHOICE_FORMAT or value.get("profile") != PROFILE
                or type(selected) is not dict
                or type(selected.get("version")) is not str
                or _TAG.fullmatch(selected["version"]) is None
                or not (type(selected.get("asset_sha256")) is str
                        and _SHA256.fullmatch(selected["asset_sha256"]))
                or type(selected.get("asset_size")) is not int
                or selected["asset_size"] <= 0
                or selected["asset_size"] > MAX_ASSET_BYTES
                or not (ignored is None or type(ignored) is str and _RELEASE_ID.fullmatch(ignored))):
            raise ValueError("saved pack release choice is invalid")
        release_id = selected.get("release_id")
        if type(release_id) is not str or _SELECTION_ID.fullmatch(release_id) is None:
            raise ValueError("saved pack release identity is invalid")
        version = selected["version"]
        name = selected.get("asset_name")
        match = self.authority.asset_pattern.fullmatch(name) if type(name) is str and len(name) <= 256 else None
        if (match is None or match.group("tag") != version
                or selected.get("asset_url") != f"https://github.com/{REPOSITORY}/releases/download/{version}/{name}"
                or selected.get("release_url") != f"https://github.com/{REPOSITORY}/releases/tag/{version}"):
            raise ValueError("saved client asset source is invalid")
        _published_at(selected.get("published_at"))
        artifact_path = selected.get("artifact_path")
        if artifact_path is not None and (type(artifact_path) is not str or not Path(artifact_path).is_absolute()):
            raise ValueError("saved client artifact path is invalid")
        return value

    def _baseline_choice(self) -> dict[str, Any]:
        baseline_identity = {
            "profile": PROFILE, "version": self.authority.baseline_version,
            "asset_sha256": self.authority.baseline_sha256,
            "asset_size": self.authority.baseline_size,
            "asset_url": self.authority.baseline_asset_url,
        }
        return {
            "format": CHOICE_FORMAT, "profile": PROFILE,
            "selected": {"version": self.authority.baseline_version,
                         "release_id": "profile-release:sha256:" + sha256(_json_bytes(baseline_identity)).hexdigest(),
                         "asset_sha256": self.authority.baseline_sha256,
                         "asset_size": self.authority.baseline_size,
                         "asset_name": self.authority.baseline_asset_name,
                         "asset_url": self.authority.baseline_asset_url,
                         "release_url": self.authority.baseline_release_url,
                         "published_at": self.authority.baseline_published_at,
                         "artifact_path": None},
            "ignored_release_id": None,
        }

    def _status(self, candidate: dict[str, Any], choice: dict[str, Any]) -> str:
        selected = choice["selected"]
        latest = Version(candidate["version"])
        saved = Version(selected["version"])
        if latest < saved or (latest == saved and candidate["asset_sha256"] == selected["asset_sha256"]):
            return "current"
        if choice["ignored_release_id"] == candidate["release_id"]:
            return "ignored"
        return "update_available"

    def _result(self, action: str, status: str, choice: dict[str, Any],
                candidate: dict[str, Any] | None, reason: str | None = None,
                artifact_path: Path | None = None) -> dict[str, Any]:
        result = {"schema": SCHEMA, "action": action, "status": status,
                  "selected_version": choice["selected"]["version"],
                  "selected": choice["selected"],
                  "ignored_release_id": choice["ignored_release_id"],
                  "candidate": candidate, "reason": reason}
        result["artifact_state"] = (
            "not_prepared" if choice["selected"].get("artifact_path") is None
            else "recorded"
        )
        if artifact_path is not None:
            result["artifact_path"] = str(artifact_path)
        return result

    def show(self) -> dict[str, Any]:
        """Read the durable choice offline and verify any prepared artifact anew."""

        choice = self._read_choice()
        selected = dict(choice["selected"])
        path_value = selected.get("artifact_path")
        state = "none"
        if path_value is not None:
            path = Path(path_value)
            expected = (self.state_root.expanduser().resolve() / "artifacts" / "sha256"
                        / selected["asset_sha256"].removeprefix("sha256:"))
            if path != expected:
                state = "other_root"
                selected["artifact_path"] = None
            elif path.is_symlink():
                state = "changed"
                selected["artifact_path"] = None
            elif not path.exists():
                state = "missing"
                selected["artifact_path"] = None
            elif not path.is_file():
                state = "changed"
                selected["artifact_path"] = None
            else:
                try:
                    observed_sha256, observed_size = sha256_file(path)
                    if (observed_sha256 == selected["asset_sha256"].removeprefix("sha256:")
                            and observed_size == selected["asset_size"]):
                        state = "verified"
                    else:
                        state = "changed"
                except OSError:
                    state = "changed"
                if state != "verified":
                    selected["artifact_path"] = None
        choice = {**choice, "selected": selected}
        result = self._result("show", "selected", choice, None)
        result["artifact_state"] = state
        return result

    def inputs(self) -> dict[str, Any]:
        """Review a verified selected archive's unresolved acquisition inputs."""

        shown = self.show()
        choice = self._read_choice()
        state = shown["artifact_state"]
        if state != "verified":
            result = self._result("inputs", "unavailable", choice, None,
                                  f"selected client archive is {state}")
            return {**result, "artifact_state": state, "input_plan": None}
        selected = choice["selected"]
        if selected != shown["selected"]:
            result = self._result("inputs", "stale", choice, None,
                                  "selected pack release changed during review")
            return {**result, "artifact_state": state, "input_plan": None}
        try:
            with Path(selected["artifact_path"]).open("rb") as source:
                before = os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError("selected client archive is not a regular file")
                manifest, raw, entries = _verify_client_archive(
                    source, self.authority, selected["version"],
                )
                source.seek(0)
                digest = sha256()
                size = 0
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                after = os.fstat(source.fileno())
                if (before.st_dev, before.st_ino, before.st_size,
                        before.st_mtime_ns, before.st_ctime_ns) != (
                        after.st_dev, after.st_ino, after.st_size,
                        after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError("selected client archive changed during review")
                if ("sha256:" + digest.hexdigest() != selected["asset_sha256"]
                        or size != selected["asset_size"]):
                    raise ValueError("selected client archive changed after preparation")
            plan = _input_plan(selected, manifest, raw, entries)
        except (OSError, ValueError) as exc:
            observed = self.show()
            result = self._result("inputs", "unavailable", self._read_choice(), None,
                                  str(exc))
            return {**result, "artifact_state": observed["artifact_state"],
                    "input_plan": None}
        current = self._read_choice()
        if current != choice:
            result = self._result("inputs", "stale", current, None,
                                  "selected pack release changed during review")
            return {**result, "artifact_state": "verified", "input_plan": None}
        result = self._result("inputs", "planned", choice, None)
        return {**result, "artifact_state": "verified", "input_plan": plan}

    def local_inputs(self, source_path: Path, policy_path: Path) -> dict[str, Any]:
        """Review local bytes against manifest IDs without asserting their origin."""

        from .pack_release_local import review_local_inputs

        first = self.inputs()
        if first["status"] != "planned":
            return {"schema": "workbench.pack-release.local-inputs.v1",
                    "action": "local-inputs", "status": first["status"],
                    "reason": first["reason"], "local_input_plan": None}
        input_plan = first["input_plan"]
        selected = first["selected"]
        try:
            plan = review_local_inputs(
                input_plan, source_path=source_path, policy_path=policy_path,
                archive_path=Path(selected["artifact_path"]),
            )
        except (DurableRecordError, DurableResourceError, OSError, ValueError) as exc:
            return {"schema": "workbench.pack-release.local-inputs.v1",
                    "action": "local-inputs", "status": "unavailable",
                    "reason": str(exc), "local_input_plan": None}
        current = self.inputs()
        if (current["status"] != "planned"
                or current["input_plan"]["plan_id"] != input_plan["plan_id"]
                or current["selected"] != selected):
            return {"schema": "workbench.pack-release.local-inputs.v1",
                    "action": "local-inputs", "status": "stale",
                    "reason": "selected client archive changed during local review",
                    "local_input_plan": None}
        return {"schema": "workbench.pack-release.local-inputs.v1",
                "action": "local-inputs", "status": "planned", "reason": None,
                "local_input_plan": plan}

    def prism_inputs(self, mods_root: Path, policy_path: Path, *,
                     optional_selected: tuple[tuple[int, int], ...] = ()) -> dict[str, Any]:
        """Review local Prism/Packwiz bytes without changing the saved choice."""

        from .pack_release_prism_import import plan_prism_import

        first = self.inputs()
        if first["status"] != "planned":
            return {"schema": "workbench.pack-release.prism-import.v1",
                    "action": "prism-inputs", "status": first["status"],
                    "reason": "selected pack release inputs are unavailable",
                    "prism_import_plan": None}
        try:
            plan = plan_prism_import(
                first["input_plan"], source_root=mods_root,
                archive_path=Path(first["selected"]["artifact_path"]),
                policy_path=policy_path, state_root=self.state_root,
                config_home=self.choice_path.parent,
                optional_selected=optional_selected,
            )
        except (DurableRecordError, DurableResourceError, OSError, ValueError):
            return {"schema": "workbench.pack-release.prism-import.v1",
                    "action": "prism-inputs", "status": "unavailable",
                    "reason": "Prism source or Core custody did not pass review",
                    "prism_import_plan": None}
        current = self.inputs()
        if (current["status"] != "planned"
                or current["input_plan"]["plan_id"] != first["input_plan"]["plan_id"]
                or current["selected"] != first["selected"]):
            return {"schema": "workbench.pack-release.prism-import.v1",
                    "action": "prism-inputs", "status": "stale",
                    "reason": "selected pack release changed during Prism review",
                    "prism_import_plan": None}
        return {"schema": "workbench.pack-release.prism-import.v1",
                "action": "prism-inputs", "status": "planned", "reason": None,
                "prism_import_plan": plan}

    def import_prism_inputs(self, mods_root: Path, policy_path: Path, *,
                            expected_plan_id: str,
                            optional_selected: tuple[tuple[int, int], ...] = ()) -> dict[str, Any]:
        """Retain reviewed local bytes through Core; leave installation unresolved."""

        from .pack_release_prism_import import apply_prism_import

        first = self.inputs()
        if first["status"] != "planned":
            return {"schema": "workbench.pack-release.prism-import.v1",
                    "action": "prism-import", "status": first["status"],
                    "reason": "selected pack release inputs are unavailable",
                    "prism_import_result": None}
        try:
            result = apply_prism_import(
                first["input_plan"], source_root=mods_root,
                archive_path=Path(first["selected"]["artifact_path"]),
                policy_path=policy_path, state_root=self.state_root,
                config_home=self.choice_path.parent,
                expected_plan_id=expected_plan_id,
                optional_selected=optional_selected,
            )
        except (DurableRecordError, DurableResourceError, OSError, ValueError):
            return {"schema": "workbench.pack-release.prism-import.v1",
                    "action": "prism-import", "status": "unavailable",
                    "reason": "Prism source or Core custody did not pass review",
                    "prism_import_result": None}
        current = self.inputs()
        if (current["status"] != "planned"
                or current["input_plan"]["plan_id"] != first["input_plan"]["plan_id"]
                or current["selected"] != first["selected"]):
            return {"schema": "workbench.pack-release.prism-import.v1",
                    "action": "prism-import", "status": "stale",
                    "reason": "selected pack release changed during Prism import",
                    "prism_import_result": result}
        return {"schema": "workbench.pack-release.prism-import.v1",
                "action": "prism-import", "status": "retained", "reason": None,
                "prism_import_result": result}

    def reopen_prism_inputs(self, policy_path: Path, *, expected_plan_id: str) -> dict[str, Any]:
        """Reopen retained Core bytes even when the external Prism source is gone."""

        from .pack_release_prism_import import reopen_prism_import

        first = self.inputs()
        if first["status"] != "planned":
            return {"schema": "workbench.pack-release.prism-import.v1",
                    "action": "prism-reopen", "status": first["status"],
                    "reason": "selected pack release inputs are unavailable",
                    "prism_import_result": None}
        try:
            result = reopen_prism_import(
                first["input_plan"], expected_plan_id=expected_plan_id,
                policy_path=policy_path, state_root=self.state_root,
                config_home=self.choice_path.parent,
            )
        except (DurableRecordError, DurableResourceError, OSError, ValueError):
            return {"schema": "workbench.pack-release.prism-import.v1",
                    "action": "prism-reopen", "status": "unavailable",
                    "reason": "retained Core tree did not pass readback",
                    "prism_import_result": None}
        current = self.inputs()
        if (current["status"] != "planned"
                or current["input_plan"]["plan_id"] != first["input_plan"]["plan_id"]
                or current["selected"] != first["selected"]):
            return {"schema": "workbench.pack-release.prism-import.v1",
                    "action": "prism-reopen", "status": "stale",
                    "reason": "selected pack release changed during retained readback",
                    "prism_import_result": None}
        return {"schema": "workbench.pack-release.prism-import.v1",
                "action": "prism-reopen", "status": "reopened", "reason": None,
                "prism_import_result": result}

    def check(self) -> dict[str, Any]:
        choice = self._read_choice()
        try:
            candidate = _candidate(self.authority, self.latest_fetcher(self.authority.api_url))
        except (ReleaseUnavailable, HTTPError, URLError, TimeoutError, OSError) as exc:
            return self._result("check", "unavailable", choice, None, str(exc))
        return self._result("check", self._status(candidate, choice), choice, candidate)

    def _reviewed(self, action: str, expected_release_id: str) -> dict[str, Any]:
        if type(expected_release_id) is not str or _RELEASE_ID.fullmatch(expected_release_id) is None:
            raise ValueError("expected release ID is invalid")
        observed = self.check()
        observed["action"] = action
        if observed["status"] == "unavailable":
            return observed
        candidate = observed["candidate"]
        if (observed["status"] != "update_available"
                or candidate["release_id"] != expected_release_id):
            observed["status"] = "stale"
            observed["reason"] = "latest release or saved pack choice changed after review"
        return observed

    def _save(self, action: str, candidate: dict[str, Any], artifact_path: Path | None,
              reviewed: dict[str, Any]) -> None:
        def transform(previous: bytes | None) -> bytes:
            choice = self._baseline_choice() if previous is None else self._read_choice(previous)
            if (choice["selected"] != reviewed["selected"]
                    or choice["ignored_release_id"] != reviewed["ignored_release_id"]):
                raise ReleaseStale("saved pack choice changed after review")
            if self._status(candidate, choice) != "update_available":
                raise ReleaseStale("saved pack choice changed after review")
            if action == "accept":
                choice["selected"] = {
                    "version": candidate["version"], "release_id": candidate["release_id"],
                    "asset_sha256": candidate["asset_sha256"],
                    "asset_size": candidate["asset_size"],
                    "asset_name": candidate["asset_name"],
                    "asset_url": candidate["asset_url"],
                    "release_url": candidate["release_url"],
                    "published_at": candidate["published_at"],
                    "artifact_path": str(artifact_path),
                }
            choice["ignored_release_id"] = candidate["release_id"] if action == "ignore" else None
            return _json_bytes(choice) + b"\n"

        update_preference_bytes(self.choice_path, transform, byte_limit=MAX_CHOICE_BYTES)

    def ignore(self, expected_release_id: str) -> dict[str, Any]:
        reviewed = self._reviewed("ignore", expected_release_id)
        if reviewed["status"] != "update_available":
            return reviewed
        candidate = reviewed["candidate"]
        try:
            self._save("ignore", candidate, None, reviewed)
        except ReleaseStale as exc:
            return self._result("ignore", "stale", self._read_choice(), candidate, str(exc))
        return self._result("ignore", "ignored", self._read_choice(), candidate)

    def _download(self, candidate: dict[str, Any]) -> Path:
        path, _ = self.artifact_fetcher(
            url=candidate["asset_url"],
            expected_sha256=candidate["asset_sha256"].removeprefix("sha256:"),
            expected_size=candidate["asset_size"], state_root=self.state_root,
            label="Supersymmetry client release", timeout_seconds=30.0,
        )
        _verify_client_archive(path, self.authority, candidate["version"])
        return path

    def accept(self, expected_release_id: str) -> dict[str, Any]:
        reviewed = self._reviewed("accept", expected_release_id)
        if reviewed["status"] != "update_available":
            return reviewed
        candidate = reviewed["candidate"]
        try:
            path = self._download(candidate)
        except (ArtifactStoreError, ValueError, OSError) as exc:
            return self._result("accept", "unavailable", self._read_choice(), candidate, str(exc))
        fresh = self._reviewed("accept", expected_release_id)
        if fresh["status"] != "update_available":
            return fresh
        if (fresh["selected"] != reviewed["selected"]
                or fresh["ignored_release_id"] != reviewed["ignored_release_id"]):
            return self._result("accept", "stale", self._read_choice(), candidate,
                                "saved pack choice changed during client preparation")
        try:
            self._save("accept", candidate, path, reviewed)
        except ReleaseStale as exc:
            return self._result("accept", "stale", self._read_choice(), candidate, str(exc))
        return self._result("accept", "accepted", self._read_choice(), candidate, artifact_path=path)

    @staticmethod
    def _selected_candidate(selected: dict[str, Any]) -> dict[str, Any]:
        return {key: selected[key] for key in (
            "release_id", "version", "asset_name", "asset_size", "asset_sha256",
            "asset_url", "release_url", "published_at",
        )} | {"tag": selected["version"]}

    def prepare(self, expected_release_id: str) -> dict[str, Any]:
        """Explicitly retain the saved selection, including an older ignored baseline."""

        if type(expected_release_id) is not str or _SELECTION_ID.fullmatch(expected_release_id) is None:
            raise ValueError("expected selected release ID is invalid")
        choice = self._read_choice()
        selected = choice["selected"]
        candidate = self._selected_candidate(selected)
        if selected["release_id"] != expected_release_id:
            return self._result("prepare", "stale", choice, candidate,
                                "selected pack release changed after review")
        try:
            path = self._download(candidate)
        except (ArtifactStoreError, ValueError, OSError) as exc:
            return self._result("prepare", "unavailable", self._read_choice(), candidate, str(exc))

        def transform(previous: bytes | None) -> bytes:
            current = self._baseline_choice() if previous is None else self._read_choice(previous)
            if current["selected"] != selected:
                raise ReleaseStale("selected pack release changed after review")
            current["selected"] = {**selected, "artifact_path": str(path)}
            return _json_bytes(current) + b"\n"

        try:
            update_preference_bytes(self.choice_path, transform, byte_limit=MAX_CHOICE_BYTES)
        except ReleaseStale as exc:
            return self._result("prepare", "stale", self._read_choice(), candidate, str(exc))
        return self._result("prepare", "prepared", self._read_choice(), candidate, artifact_path=path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="workbench pack release")
    parser.add_argument("action", choices=("show", "check", "accept", "ignore", "prepare", "inputs", "local-inputs", "prism-inputs", "prism-import", "prism-reopen"))
    parser.add_argument("--profile", required=True, choices=(PROFILE,))
    parser.add_argument("--expected-release-id")
    parser.add_argument("--sources", type=Path)
    parser.add_argument("--mods-root", type=Path)
    parser.add_argument("--expected-plan-id")
    parser.add_argument("--include-optional", action="append", default=[], metavar="PROJECT:FILE")
    parser.add_argument("--json", action="store_true")
    selected = parser.parse_args(argv)
    if selected.action in {"show", "check", "inputs", "local-inputs", "prism-inputs", "prism-import", "prism-reopen"} and selected.expected_release_id is not None:
        parser.error("this action does not accept an expected release ID")
    if selected.action in {"accept", "ignore", "prepare"} and selected.expected_release_id is None:
        parser.error("accept, ignore, and prepare require --expected-release-id")
    if selected.action == "local-inputs" and selected.sources is None:
        parser.error("local-inputs requires --sources")
    if selected.action != "local-inputs" and selected.sources is not None:
        parser.error("--sources is only accepted by local-inputs")
    if selected.action in {"prism-inputs", "prism-import"} and selected.mods_root is None:
        parser.error("Prism actions require --mods-root")
    if selected.action not in {"prism-inputs", "prism-import"} and selected.mods_root is not None:
        parser.error("--mods-root is only accepted by Prism actions")
    if selected.action in {"prism-import", "prism-reopen"} and selected.expected_plan_id is None:
        parser.error("prism-import and prism-reopen require --expected-plan-id")
    if selected.action not in {"prism-import", "prism-reopen"} and selected.expected_plan_id is not None:
        parser.error("--expected-plan-id is only accepted by prism-import and prism-reopen")
    if selected.action not in {"prism-inputs", "prism-import"} and selected.include_optional:
        parser.error("--include-optional is only accepted by Prism actions")
    optional_selected: tuple[tuple[int, int], ...] = ()
    if selected.include_optional:
        pairs = []
        for value in selected.include_optional:
            if not re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", value):
                parser.error("--include-optional requires PROJECT:FILE IDs")
            project, file = value.split(":", 1)
            pairs.append((int(project), int(file)))
        optional_selected = tuple(pairs)
    resources = profile_resources("release-authority")
    if PROFILE not in resources:
        raise ValueError("Supersymmetry release authority profile is unavailable")
    service = PackReleaseService(
        load_authority(resources[PROFILE]),
        config_home=default_user_config_home(),
        state_root=default_runtime_state_root(),
    )
    if selected.action in {"local-inputs", "prism-inputs", "prism-import", "prism-reopen"}:
        policies = profile_resources("release-local-input-policy")
        if PROFILE not in policies:
            raise ValueError("Supersymmetry local input policy profile is unavailable")
    result = (service.show() if selected.action == "show" else
              service.check() if selected.action == "check" else
              service.inputs() if selected.action == "inputs" else
              service.local_inputs(selected.sources, policies[PROFILE]) if selected.action == "local-inputs" else
              service.prism_inputs(selected.mods_root, policies[PROFILE], optional_selected=optional_selected) if selected.action == "prism-inputs" else
              service.import_prism_inputs(selected.mods_root, policies[PROFILE], expected_plan_id=selected.expected_plan_id,
                                          optional_selected=optional_selected) if selected.action == "prism-import" else
              service.reopen_prism_inputs(policies[PROFILE], expected_plan_id=selected.expected_plan_id) if selected.action == "prism-reopen" else
              service.accept(selected.expected_release_id) if selected.action == "accept" else
              service.ignore(selected.expected_release_id) if selected.action == "ignore" else
              service.prepare(selected.expected_release_id))
    if selected.json:
        print(json.dumps(result, sort_keys=True))
    elif selected.action == "local-inputs":
        print(f"Supersymmetry local inputs: {result['status'].replace('_', ' ')}")
        if result["reason"]:
            print(f"  Reason: {result['reason']}")
        if result["local_input_plan"] is not None:
            plan = result["local_input_plan"]
            print(f"  Local bytes verified: {plan['local_bytes_verified']}")
            print(f"  Required files unresolved: {plan['required_unresolved']}")
            print(f"  Optional files selected but unresolved: {plan['optional_selected_unresolved']}")
            print("  CurseForge file identity: unproven by local hash")
    elif selected.action in {"prism-inputs", "prism-import", "prism-reopen"}:
        print(f"Supersymmetry Prism input: {result['status'].replace('_', ' ')}")
        if result["reason"]:
            print(f"  Reason: {result['reason']}")
        detail = result.get("prism_import_plan") or result.get("prism_import_result")
        if detail is not None:
            print(f"  Local files: {detail['retained_file_count']}")
            print(f"  Required or selected files unresolved: {len(detail['unresolved'])}")
            print("  CurseForge file identity: unproven by local sidecar")
            print("  Installation: not installed")
    else:
        print(f"Supersymmetry release: {result['status'].replace('_', ' ')}")
        if result["candidate"]:
            print(f"  Latest: {result['candidate']['version']}")
        print(f"  Selected: {result['selected_version']}")
        if result["reason"]:
            print(f"  Reason: {result['reason']}")
        if result.get("artifact_path"):
            print(f"  Verified client ZIP: {result['artifact_path']}")
        if result.get("input_plan") is not None:
            plan = result["input_plan"]
            print(f"  External files awaiting acquisition: {len(plan['external_files'])}")
            print(f"  Override files in archive: {plan['override_file_count']}")
    return 0 if result["status"] not in {"unavailable", "stale"} or selected.action == "check" else 2


__all__ = ["PackReleaseService", "ReleaseAuthority", "load_authority", "fetch_latest_release", "main"]
