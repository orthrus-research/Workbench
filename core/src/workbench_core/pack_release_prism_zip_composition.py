"""Retain a complete user Prism ZIP as an exact release-client composition.

Launcher metadata and the Minecraft payload are retained separately so Core
can project the original Prism platform into an installed instance.
"""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, Iterator, Mapping
import unicodedata
from zipfile import BadZipFile, ZipFile, ZipInfo

from workbench_api.managed_trees import ManagedTreeReference

from .durable_files import _directory as pinned_directory, _visible_parent
from .durable_records import private_record_lock, read_private_single_link_bytes
from .environment_package_install_plan import _SUPPORTED_FILESYSTEMS, _mount_type
from .host_filesystem import private_path
from .managed_trees import CoreManagedTrees
from .output_routing import _private_directory
from .pack_release import MAX_ARCHIVE_ENTRIES, MAX_ARCHIVE_UNCOMPRESSED_BYTES
from .pack_release_client_layout import _relative
from .pack_release_local import _canonical, _object
from .storage.exact_tree_inventory import (
    EXACT_INVENTORY_POLICY, MAX_FILE_BYTES, inventory_exact_members,
)


PLAN_FORMAT = "workbench-pack-release-client-composition-plan-v3"
LOCK_FORMAT = "workbench-pack-release-client-composition-source-lock-v3"
RESULT_FORMAT = "workbench-pack-release-client-composition-result-v3"
_PLAN_PREFIX = "workbench-pack-release-client-composition-plan:sha256:"
_PLAN_ID = re.compile(r"workbench-pack-release-client-composition-plan:sha256:[0-9a-f]{64}\Z")
_DISPLAY_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+/@ -]{0,127}\Z")
_POLICY_ID = "workbench-pack-release-user-prism-zip-source-policy-v1"
_SOURCE_KIND = "user-prism-zip"
_LOCK_LIMIT = 4 * 1024 * 1024
_ARCHIVE_LIMIT = 4 * 1024 * 1024 * 1024
_CHUNK = 1024 * 1024
_STAT_FIELDS = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
_BLOCKERS = ["external-file-origin-unverified", "runtime-compatibility-unqualified"]
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_COMPONENT_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+/-]{0,127}\Z")
_FORGE_112 = re.compile(r"14\.23\.5\.[0-9]+\Z")
_CLEANROOM = re.compile(r"0\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9._-]+)?\Z")


def _same(left: os.stat_result, right: os.stat_result) -> bool:
    return all(getattr(left, field) == getattr(right, field) for field in _STAT_FIELDS)


@contextmanager
def _source(archive: Path) -> Iterator[tuple[int, int]]:
    """Pin a regular ZIP, permitting a read-only WSL 9p observation source."""

    if (not isinstance(archive, Path) or not archive.is_absolute()
            or any(part in {".", ".."} for part in archive.parts)):
        raise ValueError("Prism ZIP needs an absolute ordinary Linux path")
    filesystem = _mount_type(archive.parent)
    if filesystem not in _SUPPORTED_FILESYSTEMS and not (
            filesystem == "9p" and os.statvfs(archive.parent).f_flag & os.ST_RDONLY):
        raise ValueError("Prism ZIP needs a Linux filesystem or read-only WSL 9p source")
    parent = pinned_directory(archive.parent, create=False)
    descriptor = -1
    try:
        parent_id = (os.fstat(parent).st_dev, os.fstat(parent).st_ino)
        before = os.stat(archive.name, dir_fd=parent, follow_symlinks=False)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 0 < before.st_size <= _ARCHIVE_LIMIT):
            raise ValueError("Prism ZIP must be one bounded ordinary file")
        descriptor = os.open(
            archive.name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0), dir_fd=parent,
        )
        opened = os.fstat(descriptor)
        if not _same(before, opened):
            raise ValueError("Prism ZIP changed before review")
        yield descriptor, opened.st_size
        after = os.fstat(descriptor)
        visible = os.stat(archive.name, dir_fd=parent, follow_symlinks=False)
        if not _same(opened, after) or not _same(after, visible):
            raise ValueError("Prism ZIP changed during review")
        _visible_parent(archive.parent, parent_id)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def _hash_source(descriptor: int, expected_size: int,
                 check_cancelled: Callable[[], None]) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest, observed = sha256(), 0
    while block := os.read(descriptor, _CHUNK):
        check_cancelled()
        observed += len(block)
        if observed > expected_size:
            raise ValueError("Prism ZIP grew while hashing")
        digest.update(block)
    if observed != expected_size:
        raise ValueError("Prism ZIP changed size while hashing")
    return "sha256:" + digest.hexdigest()


def _host(state_root: Path, config_home: Path, *,
          check_cancelled: Callable[[], None] = lambda: None) -> tuple[CoreManagedTrees, Path]:
    if (not isinstance(state_root, Path) or not state_root.is_absolute()
            or not isinstance(config_home, Path) or not config_home.is_absolute()
            or _mount_type(state_root) not in _SUPPORTED_FILESYSTEMS):
        raise ValueError("Prism composition needs a qualified Linux Core state root")
    root = state_root / "pack-release-client-compositions"
    return CoreManagedTrees(
        workspace=state_root, configuration_home=config_home,
        locations={"artifacts": root}, owner_id="supersymmetry", policy_id=_POLICY_ID,
        location_sources={"artifacts": "user-prism-zip-composition"},
        check_cancelled=check_cancelled,
    ), root


def _target(root: Path, plan_id: str) -> Path:
    return root / plan_id.rsplit(":", 1)[-1] / "snapshot"


def _lock(plan: Mapping[str, Any]) -> bytes:
    body = {key: value for key, value in plan.items() if key not in {"action", "tree_id"}}
    return _canonical({**body, "format": LOCK_FORMAT}) + b"\n"


def _archive_root(names: list[str]) -> str:
    found = set(names)
    if "instance.cfg" in found and "mmc-pack.json" in found:
        return ""
    first = {name.split("/", 1)[0] for name in names}
    if len(first) == 1:
        candidate = next(iter(first))
        if candidate + "/instance.cfg" in found and candidate + "/mmc-pack.json" in found:
            return candidate + "/"
    raise ValueError("Prism ZIP needs one instance root with instance.cfg and mmc-pack.json")


def _display_version(archive: ZipFile, info: ZipInfo) -> str | None:
    """Read Prism's optional, user-controlled pack label as display metadata."""

    if info.file_size > 256 * 1024:
        return None
    try:
        lines = archive.read(info).decode("utf-8-sig").splitlines()
    except UnicodeError:
        return None
    values = [line.partition("=")[2].strip() for line in lines
              if line.startswith("ManagedPackVersionName=")]
    if len(values) != 1 or _DISPLAY_VERSION.fullmatch(values[0]) is None:
        return None
    return values[0]


def source_platform_from_manifest(manifest: Mapping[str, Any]) -> dict[str, str]:
    """Describe declared Prism components without claiming runtime compatibility."""

    components = manifest.get("components")
    if type(components) is not list or not 2 <= len(components) <= 128:
        raise ValueError("Prism ZIP launcher manifest needs Minecraft and a platform")
    versions: dict[str, str] = {}
    for row in components:
        if (type(row) is not dict or type(row.get("uid")) is not str
                or _COMPONENT.fullmatch(row["uid"]) is None
                or type(row.get("version")) is not str
                or _COMPONENT_VERSION.fullmatch(row["version"]) is None
                or row["uid"] in versions):
            raise ValueError("Prism ZIP launcher manifest has invalid or repeated components")
        versions[row["uid"]] = row["version"]
    if versions.get("net.minecraft") != "1.12.2":
        raise ValueError("Prism ZIP is not a Minecraft 1.12.2 instance")
    platform = [(uid, version) for uid, version in versions.items()
                if uid != "net.minecraft"]
    explicit = [(uid, version) for uid, version in platform
                if "cleanroom" in uid.lower()]
    if explicit:
        uid, version = explicit[0]
        kind, classification = "cleanroom", "component-id"
    elif "net.minecraftforge" in versions:
        uid, version = "net.minecraftforge", versions["net.minecraftforge"]
        if _FORGE_112.fullmatch(version):
            kind, classification = "forge", "version-pattern"
        elif _CLEANROOM.fullmatch(version):
            kind, classification = "cleanroom", "version-pattern"
        else:
            kind, classification = "custom", "unclassified"
    else:
        uid, version = platform[0]
        kind, classification = "custom", "unclassified"
    return {
        "kind": kind, "component_uid": uid, "component_version": version,
        "minecraft_version": "1.12.2", "classification": classification,
    }


def _scan_zip(
    archive: ZipFile, *, check_cancelled: Callable[[], None],
) -> tuple[dict[str, Any], dict[str, ZipInfo]]:
    infos = archive.infolist()
    if not 1 <= len(infos) <= MAX_ARCHIVE_ENTRIES:
        raise ValueError("Prism ZIP exceeds its member bound")
    names: list[str] = []
    seen: set[str] = set()
    file_names: set[str] = set()
    total = 0
    for info in infos:
        check_cancelled()
        name = _relative(info.filename.rstrip("/"))
        kind = stat.S_IFMT(info.external_attr >> 16)
        if (info.orig_filename != info.filename or info.flag_bits & 1
                or info.external_attr & 0x400 or info.compress_type not in {0, 8}
                or kind not in ({0, stat.S_IFDIR} if info.is_dir()
                                else {0, stat.S_IFREG})):
            raise ValueError("Prism ZIP has an unsupported member")
        key = unicodedata.normalize("NFC", name).casefold()
        if key in seen:
            raise ValueError("Prism ZIP repeats or collides on a member path")
        seen.add(key)
        if info.is_dir():
            if info.file_size or info.compress_size or info.CRC:
                raise ValueError("Prism ZIP has a nonempty directory member")
        else:
            if info.file_size > MAX_FILE_BYTES:
                raise ValueError("Prism ZIP member exceeds Core's file bound")
            total += info.file_size
            if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise ValueError("Prism ZIP exceeds its expansion bound")
            file_names.add(name)
        names.append(name)
    for name in names:
        if any("/".join(name.split("/")[:index]) in file_names
               for index in range(1, len(name.split("/")))):
            raise ValueError("Prism ZIP uses a file as a directory")
    root = _archive_root(names)
    # The root itself may be present as an explicit directory entry.
    normalized: dict[str, ZipInfo] = {}
    entries: list[dict[str, Any]] = []
    payload: list[dict[str, Any]] = []
    metadata: list[dict[str, Any]] = []
    for info, name in zip(infos, names, strict=True):
        check_cancelled()
        if name == root.rstrip("/") and info.is_dir():
            entries.append({"archive_path": name + "/", "relative_path": name,
                            "kind": "directory", "size": 0,
                            "sha256": "sha256:" + sha256(b"").hexdigest()})
            continue
        if not name.startswith(root):
            raise ValueError("Prism ZIP has files outside its instance root")
        relative = name.removeprefix(root)
        _relative(relative)
        if info.is_dir():
            entries.append({"archive_path": info.filename, "relative_path": relative,
                            "kind": "directory", "size": 0,
                            "sha256": "sha256:" + sha256(b"").hexdigest()})
            continue
        digest, observed = sha256(), 0
        with archive.open(info) as source:
            while block := source.read(_CHUNK):
                check_cancelled()
                observed += len(block)
                if observed > info.file_size or observed > MAX_FILE_BYTES:
                    raise ValueError("Prism ZIP member expanded beyond its declaration")
                digest.update(block)
        if observed != info.file_size:
            raise ValueError("Prism ZIP member changed size during review")
        row = {"relative_path": relative, "size": observed,
               "sha256": "sha256:" + digest.hexdigest()}
        entries.append({**row, "archive_path": info.filename, "kind": "file"})
        normalized[relative] = info
    data_names = set(normalized)
    if not {"instance.cfg", "mmc-pack.json"}.issubset(data_names):
        raise ValueError("Prism ZIP lacks ordinary launcher metadata")
    if (not 0 < normalized["instance.cfg"].file_size <= 256 * 1024
            or any(name in data_names for name in (
                ".workbench-release-install.json",
                ".workbench-release-install-intent.json", ".minecraft"))):
        raise ValueError("Prism ZIP has an unsupported instance configuration")
    try:
        archive.read(normalized["instance.cfg"]).decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("Prism ZIP instance configuration is not UTF-8") from exc
    game_roots = [name for name in ("minecraft", ".minecraft")
                  if any(path.startswith(name + "/") for path in data_names)]
    if len(game_roots) != 1:
        raise ValueError("Prism ZIP needs exactly one Minecraft directory")
    game = game_roots[0]
    for row in entries:
        if row["kind"] != "file":
            continue
        relative = row["relative_path"]
        if relative.startswith(game + "/"):
            payload.append({"relative_path": relative.removeprefix(game + "/"),
                            "size": row["size"], "sha256": row["sha256"],
                            "source": "user-prism-zip"})
        else:
            metadata.append({"relative_path": relative, "size": row["size"],
                             "sha256": row["sha256"]})
    if not any(row["relative_path"].lower().startswith("mods/")
               and row["relative_path"].lower().endswith(".jar") and row["size"] > 0
               for row in payload):
        raise ValueError("Prism ZIP needs at least one nonempty mod JAR")
    manifest_info = normalized["mmc-pack.json"]
    if manifest_info.file_size > 1024 * 1024:
        raise ValueError("Prism ZIP launcher manifest exceeds its bound")
    manifest = _object(archive.read(manifest_info), "Prism ZIP launcher manifest")
    source_platform = source_platform_from_manifest(manifest)
    return {
        "archive_root": root.rstrip("/"), "minecraft_source_root": game,
        "source_version": _display_version(archive, normalized["instance.cfg"]),
        "source_platform": source_platform,
        "archive_entries": sorted(entries, key=lambda row: row["relative_path"]),
        "files": sorted(payload, key=lambda row: row["relative_path"]),
        "source_metadata_files": sorted(metadata, key=lambda row: row["relative_path"]),
    }, normalized


def _candidate(
    descriptor: int, size: int, *, check_cancelled: Callable[[], None],
) -> tuple[dict[str, Any], dict[str, ZipInfo]]:
    archive_digest = _hash_source(descriptor, size, check_cancelled)
    try:
        with os.fdopen(os.dup(descriptor), "rb") as stream, ZipFile(stream) as archive:
            selected, normalized = _scan_zip(archive, check_cancelled=check_cancelled)
    except BadZipFile as exc:
        raise ValueError("Prism source is not an intact ZIP") from exc
    if _hash_source(descriptor, size, check_cancelled) != archive_digest:
        raise ValueError("Prism ZIP changed during review")
    body = {
        "format": PLAN_FORMAT, "schema_version": 3, "profile": "supersymmetry",
        "source_kind": _SOURCE_KIND, "source_version": selected["source_version"],
        "source_platform": selected["source_platform"],
        "source_archive_sha256": archive_digest, "source_archive_size": size,
        "archive_root": selected["archive_root"],
        "minecraft_source_root": selected["minecraft_source_root"],
        "archive_entries": selected["archive_entries"],
        "source_metadata_files": selected["source_metadata_files"],
        "file_count": len(selected["files"]),
        "total_bytes": sum(row["size"] for row in selected["files"]),
        "files": selected["files"], "blockers": _BLOCKERS,
        "installation_state": "not-installed",
    }
    plan = {**body, "plan_id": _PLAN_PREFIX + sha256(_canonical(body)).hexdigest()}
    if len(_lock(plan)) > _LOCK_LIMIT:
        raise ValueError("Prism ZIP source lock exceeds its bound")
    return plan, normalized


def _validate_stage(stage: Path, plan: Mapping[str, Any], *,
                    check_cancelled: Callable[[], None] = lambda: None) -> None:
    def cancelled() -> bool:
        check_cancelled()
        return False

    members, root_mode, file_count, directory_count = inventory_exact_members(
        stage, cancelled=cancelled,
    )
    lock = _lock(plan)
    expected_files = {"source-lock.json": (len(lock), sha256(lock).hexdigest())}
    expected_dirs = {"minecraft-root", "source-metadata"}
    for root, rows in (("minecraft-root", plan["files"]),
                       ("source-metadata", plan["source_metadata_files"])):
        for row in rows:
            relative = root + "/" + row["relative_path"]
            expected_files[relative] = (row["size"], row["sha256"].removeprefix("sha256:"))
            parts = relative.split("/")
            expected_dirs.update("/".join(parts[:index]) for index in range(1, len(parts)))
    if (root_mode != 0o700 or file_count != len(expected_files)
            or directory_count != len(expected_dirs)
            or len(members) != file_count + directory_count):
        raise ValueError("retained Prism composition differs in member count or mode")
    observed_files, observed_dirs = set(), set()
    for member in members:
        path = str(member["path"])
        if member["kind"] == "directory":
            if path not in expected_dirs or member["mode"] != 0o700:
                raise ValueError("retained Prism composition has another directory")
            observed_dirs.add(path)
        elif member["kind"] == "file":
            if (path not in expected_files or member["mode"] != 0o600
                    or (member["size"], member["sha256"]) != expected_files[path]):
                raise ValueError("retained Prism composition has another file")
            observed_files.add(path)
        else:
            raise ValueError("retained Prism composition has an unsupported member")
    if observed_files != set(expected_files) or observed_dirs != expected_dirs:
        raise ValueError("retained Prism composition omits an expected member")
    if read_private_single_link_bytes(stage / "source-lock.json", byte_limit=_LOCK_LIMIT) != lock:
        raise ValueError("retained Prism composition source lock changed")


def _reopen_tree(host: CoreManagedTrees, reference: ManagedTreeReference,
                 target: Path, plan: Mapping[str, Any]) -> None:
    if (reference.path != target or reference.workspace != host.workspace
            or reference.owner_id != host.owner_id or reference.role != "artifacts"
            or reference.policy_id != _POLICY_ID or reference.domain_id != plan["plan_id"]
            or reference.references or reference.inventory_policy != EXACT_INVENTORY_POLICY
            or reference.derived_status != "current"):
        raise ValueError("Prism composition reopened with another Core identity")
    _validate_stage(reference.path, plan)


def _tree_state(host: CoreManagedTrees, target: Path,
                plan: Mapping[str, Any]) -> tuple[str, str | None]:
    rows = [row for row in host.catalog.trees.inventory() if row["path"] == str(target)]
    if not rows:
        if target.exists() or target.is_symlink():
            raise ValueError("Prism composition target exists outside Core custody")
        return "acquire", None
    if len(rows) != 1:
        raise ValueError("Prism composition has ambiguous Core reservations")
    row = rows[0]
    if (row["workspace"] != str(host.workspace) or row["owner_id"] != host.owner_id
            or row["role"] != "artifacts"):
        raise ValueError("Prism composition target belongs to another Core binding")
    if row["status"] != "committed":
        raise ValueError("Prism composition has an incomplete Core stage")
    reference = host.describe(str(row["tree_id"]))
    _reopen_tree(host, reference, target, plan)
    return "reuse", reference.tree_id


def _write_member(destination: Path, source: Any, expected: Mapping[str, Any], *,
                  check_cancelled: Callable[[], None]) -> None:
    parent = pinned_directory(destination.parent, create=True)
    try:
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                 | getattr(os, "O_CLOEXEC", 0))
        output = os.open(destination.name, flags, 0o600, dir_fd=parent)
        with os.fdopen(output, "wb") as sink:
            digest, observed = sha256(), 0
            while block := source.read(_CHUNK):
                check_cancelled()
                observed += len(block)
                if observed > expected["size"]:
                    raise ValueError("Prism ZIP member grew during custody")
                digest.update(block)
                sink.write(block)
            sink.flush()
            os.fsync(sink.fileno())
        if observed != expected["size"] or "sha256:" + digest.hexdigest() != expected["sha256"]:
            raise ValueError("Prism ZIP member changed after review")
    finally:
        os.close(parent)


def _copy_to_stage(stage: Path, descriptor: int, plan: Mapping[str, Any],
                   names: Mapping[str, ZipInfo], *,
                   check_cancelled: Callable[[], None]) -> None:
    stage.mkdir(mode=0o700)
    (stage / "minecraft-root").mkdir(mode=0o700)
    (stage / "source-metadata").mkdir(mode=0o700)
    with os.fdopen(os.dup(descriptor), "rb") as stream, ZipFile(stream) as archive:
        game = plan["minecraft_source_root"] + "/"
        for root, rows in (("minecraft-root", plan["files"]),
                           ("source-metadata", plan["source_metadata_files"])):
            for row in rows:
                check_cancelled()
                original = (game if root == "minecraft-root" else "") + row["relative_path"]
                with archive.open(names[original]) as member:
                    _write_member(stage / root / row["relative_path"], member, row,
                                  check_cancelled=check_cancelled)
    lock = _lock(plan)
    _write_member(stage / "source-lock.json", BytesIO(lock),
                  {"size": len(lock), "sha256": "sha256:" + sha256(lock).hexdigest()},
                  check_cancelled=check_cancelled)


def _result(plan: Mapping[str, Any], reference: ManagedTreeReference,
            outcome: str) -> dict[str, Any]:
    return {
        "format": RESULT_FORMAT, "schema_version": 3, "outcome": outcome,
        "plan_id": plan["plan_id"], "tree_id": reference.tree_id,
        "tree_content_sha256": reference.content_sha256,
        "source_kind": _SOURCE_KIND, "source_version": plan["source_version"],
        "source_platform": plan["source_platform"],
        "source_archive_sha256": plan["source_archive_sha256"],
        "file_count": plan["file_count"], "total_bytes": plan["total_bytes"],
        "files": plan["files"], "blockers": plan["blockers"],
        "installation_state": "not-installed",
    }


def plan_prism_zip_composition(
    archive: Path, *, state_root: Path, config_home: Path,
    check_cancelled: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Review one complete Prism ZIP without retaining or installing it."""

    host, root = _host(state_root, config_home, check_cancelled=check_cancelled)
    with _source(archive) as (descriptor, size):
        plan, _ = _candidate(descriptor, size, check_cancelled=check_cancelled)
    action, tree_id = _tree_state(host, _target(root, plan["plan_id"]), plan)
    return {**plan, "action": action, "tree_id": tree_id}


def apply_prism_zip_composition(
    archive: Path, *, state_root: Path, config_home: Path,
    expected_plan_id: str, check_cancelled: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Publish an immutable Minecraft payload and its exact ZIP source evidence."""

    if type(expected_plan_id) is not str or _PLAN_ID.fullmatch(expected_plan_id) is None:
        raise ValueError("select an exact reviewed Prism composition plan ID")
    reviewed = plan_prism_zip_composition(
        archive, state_root=state_root, config_home=config_home,
        check_cancelled=check_cancelled,
    )
    if reviewed["plan_id"] != expected_plan_id:
        raise ValueError("Prism ZIP changed after review")
    host, root = _host(state_root, config_home, check_cancelled=check_cancelled)
    target = _target(root, expected_plan_id)
    _private_directory(target.parent)
    if not private_path(target.parent, directory=True):
        raise ValueError("Prism composition target parent is not private")
    lock_path = root / (".prism-zip-composition-" + expected_plan_id.rsplit(":", 1)[-1] + ".lock")
    with private_record_lock(lock_path, wait=True):
        with _source(archive) as (descriptor, size):
            plan, names = _candidate(descriptor, size, check_cancelled=check_cancelled)
            action, tree_id = _tree_state(host, target, plan)
            if {**plan, "action": action, "tree_id": tree_id} != reviewed:
                raise ValueError("Prism ZIP inputs changed before staging")
            if action == "acquire":
                with host.stage("artifacts", target.name, requested_path=target) as stage:
                    _copy_to_stage(stage.path, descriptor, plan, names,
                                   check_cancelled=check_cancelled)
                    if _hash_source(descriptor, size, check_cancelled) != plan["source_archive_sha256"]:
                        raise ValueError("Prism ZIP changed during custody")
                    reference = stage.publish(
                        validate=lambda path: _validate_stage(
                            path, plan, check_cancelled=check_cancelled),
                        domain_id=expected_plan_id,
                        inventory_policy=EXACT_INVENTORY_POLICY,
                    )
                outcome = "retained"
            else:
                reference = host.describe(tree_id)
                outcome = "reused"
            _reopen_tree(host, reference, target, plan)
    return _result(plan, reference, outcome)


def reopen_prism_zip_composition(
    *, state_root: Path, config_home: Path, expected_plan_id: str,
) -> dict[str, Any]:
    """Reopen the exact Core tree without access to the original ZIP."""

    if type(expected_plan_id) is not str or _PLAN_ID.fullmatch(expected_plan_id) is None:
        raise ValueError("select an exact retained Prism composition plan ID")
    host, root = _host(state_root, config_home)
    target = _target(root, expected_plan_id)
    rows = [row for row in host.catalog.trees.inventory() if row["path"] == str(target)]
    if len(rows) != 1 or rows[0]["status"] != "committed":
        raise ValueError("Prism composition has no committed Core tree")
    reference = host.describe(str(rows[0]["tree_id"]))
    raw = read_private_single_link_bytes(target / "source-lock.json", byte_limit=_LOCK_LIMIT)
    lock = _object(raw, "Prism composition source lock")
    body = {**lock, "format": PLAN_FORMAT}
    body.pop("plan_id", None)
    if (lock.get("format") != LOCK_FORMAT or lock.get("schema_version") != 3
            or lock.get("source_kind") != _SOURCE_KIND
            or (lock.get("source_version") is not None and
                (type(lock["source_version"]) is not str
                 or _DISPLAY_VERSION.fullmatch(lock["source_version"]) is None))
            or lock.get("plan_id") != expected_plan_id
            or _PLAN_PREFIX + sha256(_canonical(body)).hexdigest() != expected_plan_id
            or raw != _canonical(lock) + b"\n"
            or type(lock.get("files")) is not list
            or type(lock.get("source_metadata_files")) is not list
            or type(lock.get("archive_entries")) is not list
            or lock.get("source_platform") != source_platform_from_manifest(_object(
                read_private_single_link_bytes(
                    target / "source-metadata/mmc-pack.json", byte_limit=1024 * 1024),
                "Prism ZIP retained launcher manifest"))
            or lock.get("file_count") != len(lock["files"])
            or lock.get("total_bytes") != sum(row["size"] for row in lock["files"])
            or lock.get("blockers") != _BLOCKERS
            or lock.get("installation_state") != "not-installed"):
        raise ValueError("Prism composition source lock changed")
    _reopen_tree(host, reference, target, lock)
    return _result(lock, reference, "reopened")
