"""Admit exact installed wheel members after Core's retained offline pip run.

The result is a current-byte witness for one Core-owned isolated environment.
Historical install captures remain separate; reopening rechecks the live tree.
"""

from __future__ import annotations

import base64
import csv
from hashlib import sha256
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping
import zipfile

from workbench_api.managed_trees import ManagedTreeError

from .durable_files import _directory as pinned_directory
from .durable_records import private_record_lock
from .environment_package_import import reopen_package_import
from .environment_package_install import _commands, reopen_package_install
from .environment_package_install_plan import _wheel_targets, plan_package_install_preflight
from .environment_reconstruction import ReconstructionError, _canonical, _resource_host, _seal
from .environment_retained_wheel import opened_retained_wheel
from .environment_resolution import resolve_environment
from .storage.exact_tree_inventory import exact_content_sha256, inventory_exact_members
from .storage.registered import DurableResourceError


FORMAT = "workbench-environment-package-admission-v1"
_RESULT_NAME = "environment-package-admission.json"
_RECORD_LIMIT = 8 * 1024 * 1024
_HASH = re.compile(r"sha256=([A-Za-z0-9_-]{43})\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_SCOPE = (
    "Current installed distribution files and declared launchers match the "
    "retained wheelhouse and pip RECORDs; profile/fixture execution and other "
    "reconstruction inputs remain separate."
)


def _pinned_bytes(path: Path, limit: int) -> bytes:
    """Read one installed file through no-follow parent and leaf descriptors."""

    try:
        parent = pinned_directory(path.parent, create=False)
        try:
            visible = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (not stat.S_ISREG(visible.st_mode) or visible.st_nlink != 1
                    or visible.st_size > limit):
                raise ReconstructionError("installed package file is unsafe or exceeds its bound")
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                opened = os.fstat(descriptor)
                if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                        or (opened.st_dev, opened.st_ino, opened.st_size)
                        != (visible.st_dev, visible.st_ino, visible.st_size)):
                    raise ReconstructionError("installed package file changed before read")
                chunks: list[bytes] = []
                remaining = limit + 1
                while remaining:
                    block = os.read(descriptor, min(65536, remaining))
                    if not block:
                        break
                    chunks.append(block)
                    remaining -= len(block)
                after = os.fstat(descriptor)
                final = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
                if (sum(map(len, chunks)) != opened.st_size
                        or any(getattr(opened, field) != getattr(after, field)
                               or getattr(after, field) != getattr(final, field)
                               for field in fields)):
                    raise ReconstructionError("installed package file changed during read")
                return b"".join(chunks)
            finally:
                os.close(descriptor)
        finally:
            os.close(parent)
    except OSError as exc:
        raise ReconstructionError(f"installed package file cannot be pinned: {exc}") from exc


def _inventory(root: Path) -> tuple[dict[str, dict[str, Any]], set[str], dict[str, Any]]:
    try:
        rows, mode, files, directories = inventory_exact_members(root)
    except (ManagedTreeError, OSError, ValueError) as exc:
        raise ReconstructionError(f"installed package tree cannot be inventoried: {exc}") from exc
    return ({str(row["path"]): row for row in rows if row["kind"] == "file"},
            {str(row["path"]) for row in rows if row["kind"] == "directory"}, {
        "root_mode": mode, "files": files, "directories": directories,
        "content_sha256": "sha256:" + exact_content_sha256(rows),
    })


def _relative_to_destination(destination: Path, target: Path) -> str:
    if not target.is_relative_to(destination):
        raise ReconstructionError("installed package target escaped its Core allocation")
    return target.relative_to(destination).as_posix()


def _record_target(destination: Path, site: Path, raw: str) -> str:
    if (not raw or "\\" in raw or "\x00" in raw
            or PurePosixPath(raw).is_absolute()
            or PurePosixPath(raw).as_posix() != raw):
        raise ReconstructionError("installed package RECORD has an unsafe path")
    target = Path(os.path.normpath(str(site / raw)))
    return _relative_to_destination(destination, target)


def _source_digest(archive: zipfile.ZipFile, member: str, size: int) -> str:
    digest = sha256()
    total = 0
    with archive.open(member) as source:
        while block := source.read(1024 * 1024):
            total += len(block)
            if total > size:
                raise ReconstructionError("installed wheel member exceeds its target size")
            digest.update(block)
    if total != size:
        raise ReconstructionError("installed wheel member differs in size")
    return digest.hexdigest()


def _records(
    destination: Path, site: Path, bin_path: Path,
    files: Mapping[str, Mapping[str, Any]],
    site_rows: Mapping[str, Mapping[str, Any]],
    bin_rows: Mapping[str, Mapping[str, Any]],
    wheels: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    owners = {row["name"] for row in wheels}
    expected: dict[str, set[str]] = {name: set() for name in owners}
    metadata: dict[str, str] = {}
    site_prefix = site.relative_to(destination).as_posix() + "/"
    bin_prefix = bin_path.relative_to(destination).as_posix() + "/"
    for path, item in files.items():
        owner = item["owner"]
        if owner not in owners:
            raise ReconstructionError("installed target has another wheel owner")
        expected[owner].add(path)
        if path.startswith(site_prefix) and path.endswith(".dist-info/METADATA"):
            if owner in metadata:
                raise ReconstructionError("installed wheel has repeated metadata")
            metadata[owner] = path.rsplit("/", 1)[0]
        if not (path.startswith(site_prefix) or path.startswith(bin_prefix)):
            raise ReconstructionError("installed wheel has an unsupported target root")
    if set(metadata) != owners:
        raise ReconstructionError("installed wheel lacks singular metadata")

    summaries: list[dict[str, Any]] = []
    for row in sorted(wheels, key=lambda item: item["name"]):
        owner = row["name"]
        meta = metadata[owner]
        record_target = meta + "/RECORD"
        expected[owner].add(record_target)
        record_path = destination / record_target
        raw_record = _pinned_bytes(record_path, _RECORD_LIMIT)
        if site_rows.get(record_target.removeprefix(site_prefix), {}).get("sha256") != sha256(raw_record).hexdigest():
            raise ReconstructionError("installed package RECORD changed during inventory")
        for suffix, exact in (("INSTALLER", b"pip\n"), ("REQUESTED", b"")):
            target = meta + "/" + suffix
            if target.removeprefix(site_prefix) in site_rows:
                payload = _pinned_bytes(destination / target, 64)
                if payload != exact:
                    raise ReconstructionError("installed package has another installer marker")
                expected[owner].add(target)
        try:
            text = raw_record.decode("utf-8")
            entries = list(csv.reader(io.StringIO(text, newline=""), strict=True))
        except (UnicodeError, csv.Error) as exc:
            raise ReconstructionError(f"installed package RECORD is invalid: {exc}") from exc
        if not entries or len(entries) > 250_000:
            raise ReconstructionError("installed package RECORD has no bounded membership")
        seen: set[str] = set()
        for entry in entries:
            if len(entry) != 3:
                raise ReconstructionError("installed package RECORD has malformed fields")
            path = _record_target(destination, site, entry[0])
            if path in seen or path not in expected[owner]:
                raise ReconstructionError(
                    f"installed package RECORD has another member for {owner}: {path[:160]}"
                )
            seen.add(path)
            if path.startswith(site_prefix):
                observed = site_rows.get(path.removeprefix(site_prefix))
            elif path.startswith(bin_prefix):
                observed = bin_rows.get(path.removeprefix(bin_prefix))
            else:
                observed = None
            if observed is None:
                raise ReconstructionError("installed package RECORD names a missing file")
            if path.startswith(bin_prefix) and not int(observed["mode"]) & 0o111:
                raise ReconstructionError("installed package launcher is not executable")
            if path == record_target:
                if entry[1:] != ["", ""]:
                    raise ReconstructionError("installed package RECORD must leave its own hash empty")
            else:
                match = _HASH.fullmatch(entry[1])
                if match is None or _DECIMAL.fullmatch(entry[2]) is None:
                    raise ReconstructionError("installed package RECORD lacks an exact file hash")
                try:
                    digest = base64.urlsafe_b64decode(match.group(1) + "=")
                except (ValueError, base64.binascii.Error) as exc:
                    raise ReconstructionError("installed package RECORD has an invalid digest") from exc
                if (digest.hex() != observed["sha256"] or int(entry[2]) != observed["size"]):
                    raise ReconstructionError("installed package RECORD differs from its file")
        if seen != expected[owner]:
            raise ReconstructionError("installed package RECORD omits expected wheel files")
        summaries.append({
            "name": owner, "version": row["version"],
            "record_sha256": "sha256:" + sha256(raw_record).hexdigest(),
            "record_members": len(seen), "metadata_path": meta + "/METADATA",
        })
    installed_site = {site_prefix + path for path in site_rows}
    expected_site = {path for paths in expected.values() for path in paths if path.startswith(site_prefix)}
    if installed_site != expected_site:
        raise ReconstructionError("installed site-packages has missing or extra files")
    return summaries


def _snapshot(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any],
    closure_plan: Mapping[str, Any], *, workspace: Path | str,
    package_result_resource_id: str, install_result_resource_id: str,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    installed = reopen_package_install(
        suite_root, share, candidate, closure_plan, workspace=workspace,
        package_result_resource_id=package_result_resource_id,
        result_resource_id=install_result_resource_id, environment=environment,
    )
    current = plan_package_install_preflight(
        suite_root, share, candidate, closure_plan, workspace=workspace,
        package_result_resource_id=package_result_resource_id, environment=environment,
    )
    retained = reopen_package_import(
        suite_root, share, candidate, closure_plan, workspace=workspace,
        result_resource_id=package_result_resource_id, environment=environment,
    )
    if (installed["package_tree_id"] != retained["tree_id"]
            or installed["closure_plan_id"] != closure_plan["plan_id"]
            or installed["unresolved_inputs"] != closure_plan["unresolved_inputs"]
            or "optional-module-packages" not in installed["unresolved_inputs"]):
        raise ReconstructionError("installed packages have another retained closure")
    destination = Path(installed["destination"])
    site = Path(current["installation_paths"]["purelib"])
    bin_path = Path(current["installation_paths"]["scripts"])
    if site != Path(current["installation_paths"]["platlib"]):
        raise ReconstructionError("installed package admission needs one isolated site root")
    source = Path(retained["tree_path"])
    expected_commands = _commands(current, closure_plan, source)
    for stage in ("install", "check"):
        observed = installed["captures"].get(stage, {}).get("argv_sha256")
        expected = "sha256:" + sha256(_canonical(expected_commands[stage])).hexdigest()
        if observed != expected:
            raise ReconstructionError(
                "installed package execution has no exact retained command identity"
            )
    target_review = _wheel_targets(
        source, closure_plan["wheels"], destination,
        {key: Path(value) for key, value in current["installation_paths"].items()},
        include_file_map=True,
    )
    file_map = target_review.pop("file_map")
    directory_map = target_review.pop("directory_map")
    if target_review != current["targets"]:
        raise ReconstructionError("installed package targets changed after preflight")
    site_rows, site_directories, site_inventory = _inventory(site)
    bin_rows, bin_directories, bin_inventory = _inventory(bin_path)
    site_prefix = site.relative_to(destination).as_posix() + "/"
    bin_prefix = bin_path.relative_to(destination).as_posix() + "/"
    if (site_directories != {path.removeprefix(site_prefix) for path in directory_map
                             if path.startswith(site_prefix)}
            or bin_directories != {path.removeprefix(bin_prefix) for path in directory_map
                                   if path.startswith(bin_prefix)}):
        raise ReconstructionError("installed package tree has missing or extra directories")
    try:
        for row in closure_plan["wheels"]:
            with opened_retained_wheel(source / "wheels" / row["filename"], row) as archive:
                for path, item in file_map.items():
                    if item["owner"] != row["name"] or item["wheel_member"] is None:
                        continue
                    member = item["wheel_member"]
                    if path.endswith(".dist-info/RECORD"):
                        continue  # pip regenerates RECORD; _records checks every installed entry.
                    observed = site_rows.get(path.removeprefix(site_prefix)) if path.startswith(site_prefix) else None
                    if observed is None or _source_digest(archive, member, int(observed["size"])) != observed["sha256"]:
                        raise ReconstructionError("installed wheel member differs from retained source bytes")
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, KeyError) as exc:
        if isinstance(exc, ReconstructionError):
            raise
        raise ReconstructionError(f"retained wheel member cannot be compared to its installed file: {exc}") from exc
    distributions = _records(
        destination, site, bin_path, file_map, site_rows, bin_rows, closure_plan["wheels"],
    )
    again_site, again_site_directories, again_site_inventory = _inventory(site)
    again_bin, again_bin_directories, again_bin_inventory = _inventory(bin_path)
    if (again_site != site_rows or again_bin != bin_rows
            or again_site_directories != site_directories
            or again_bin_directories != bin_directories
            or again_site_inventory != site_inventory or again_bin_inventory != bin_inventory):
        raise ReconstructionError("installed package tree changed during admission")
    if reopen_package_import(
        suite_root, share, candidate, closure_plan, workspace=workspace,
        result_resource_id=package_result_resource_id, environment=environment,
    ) != retained:
        raise ReconstructionError("retained wheelhouse changed during installed admission")
    remaining = [value for value in installed["unresolved_inputs"]
                 if value != "optional-module-packages"]
    return _seal({
        "format": FORMAT, "schema_version": 1,
        "state": "installed-packages-admitted",
        "share_id": installed["share_id"], "candidate_id": installed["candidate_id"],
        "closure_plan_id": installed["closure_plan_id"],
        "package_result_resource_id": package_result_resource_id,
        "install_result_resource_id": install_result_resource_id,
        "install_finished_id": installed["finished_id"],
        "allocation_id": installed["allocation_id"],
        "destination": installed["destination"],
        "python": installed["python"],
        "package_tree_id": retained["tree_id"],
        "site_inventory": site_inventory, "bin_inventory": bin_inventory,
        "distributions": distributions,
        "resolved_inputs": ["optional-module-packages"],
        "remaining_unresolved_inputs": remaining,
        "scope": _SCOPE,
    }, "workbench-environment-package-admission", "admission_id")


def _resource(service: Any, resource_id: str, expected: Mapping[str, Any]) -> dict[str, Any]:
    try:
        ref = service.describe(resource_id)
        raw = service.read_bytes(resource_id)
        value = json.loads(raw.decode("utf-8"))
        rows = service.catalog.inventory(workspace=service.workspace)["resources"]
        cataloged = [row for row in rows if row["resource_id"] == resource_id]
    except (DurableResourceError, OSError, ValueError, UnicodeError, TypeError) as exc:
        raise ReconstructionError(f"installed package admission cannot be reopened: {exc}") from exc
    if (ref.owner_id != "workbench-core" or ref.role != "evidence"
            or ref.domain_id != expected["share_id"]
            or not ref.path.name.endswith("-" + _RESULT_NAME)
            or len(cataloged) != 1
            or len(cataloged[0]["references"]) != 2
            or set(cataloged[0]["references"]) != {
                expected["install_result_resource_id"], expected["package_result_resource_id"],
            }
            or type(value) is not dict or raw != _canonical(value) + b"\n"
            or value != expected):
        raise ReconstructionError("installed package admission differs from current exact bytes")
    return {**value, "resource": {
        "resource_id": ref.resource_id, "store_id": ref.store_id,
        "path": str(ref.path), "sha256": ref.sha256,
    }}


def _existing(service: Any, workspace: Path, install_result_resource_id: str) -> str | None:
    try:
        rows = service.catalog.inventory(workspace=workspace)["resources"]
    except (DurableResourceError, OSError, ValueError, KeyError, TypeError) as exc:
        raise ReconstructionError(f"installed package admissions cannot be inventoried: {exc}") from exc
    matches = [row for row in rows
               if row["owner_id"] == "workbench-core" and row["role"] == "evidence"
               and Path(row["path"]).name.endswith("-" + _RESULT_NAME)
               and install_result_resource_id in row["references"]]
    if len(matches) > 1:
        raise ReconstructionError("installed package admission has ambiguous results")
    if not matches:
        return None
    row = matches[0]
    if row["status"] in {"published-uncommitted", "committed-needs-reconcile"}:
        try:
            service.catalog.reconcile(row["resource_id"])
        except DurableResourceError as exc:
            raise ReconstructionError(f"installed package admission needs catalog recovery: {exc}") from exc
    elif row["status"] != "committed":
        raise ReconstructionError("installed package admission has an incomplete publication")
    return row["resource_id"]


def admit_package_install(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any],
    closure_plan: Mapping[str, Any], *, workspace: Path | str,
    package_result_resource_id: str, install_result_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Publish or reuse one exact installed-byte admission under Core custody."""

    values = dict(os.environ if environment is None else environment)
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    service = _resource_host(Path(suite_root), local.workspace, values)
    lock = local.locations["evidence"] / (
        ".environment-admission-" + sha256(install_result_resource_id.encode()).hexdigest() + ".lock"
    )
    with private_record_lock(lock, wait=True):
        expected = _snapshot(
            suite_root, share, candidate, closure_plan, workspace=local.workspace,
            package_result_resource_id=package_result_resource_id,
            install_result_resource_id=install_result_resource_id, environment=values,
        )
        prior = _existing(service, local.workspace, install_result_resource_id)
        if prior is not None:
            return _resource(service, prior, expected)
        payload = _canonical(expected) + b"\n"
        ref = service.publish_bytes(
            "evidence", _RESULT_NAME, payload, domain_id=expected["share_id"],
            references=(install_result_resource_id, package_result_resource_id),
        )
        return _resource(service, ref.resource_id, expected)


def reopen_package_admission(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any],
    closure_plan: Mapping[str, Any], *, workspace: Path | str,
    package_result_resource_id: str, install_result_resource_id: str,
    admission_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Recheck retained pip, wheel and installed bytes before trusting admission."""

    values = dict(os.environ if environment is None else environment)
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    service = _resource_host(Path(suite_root), local.workspace, values)
    expected = _snapshot(
        suite_root, share, candidate, closure_plan, workspace=local.workspace,
        package_result_resource_id=package_result_resource_id,
        install_result_resource_id=install_result_resource_id, environment=values,
    )
    return _resource(service, admission_resource_id, expected)


__all__ = ["admit_package_install", "reopen_package_admission"]
