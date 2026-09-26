"""Read-only review of an exact offline package closure for a V3 share.

The native wheelhouse is an explicit local input, not an acquired Core tree.
This plan binds its bytes to retained optional wheels but never installs them.
"""

from __future__ import annotations

from email.parser import BytesParser
from hashlib import sha256
import configparser
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import sys
import zipfile
from typing import Any, Mapping

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from .durable_records import read_bounded_bytes
from .environment_input_candidates import validate_input_candidate
from .environment_reconstruction import (
    ReconstructionError, SHARE_FORMAT_V3, _host_variant, _seal,
    validate_share,
)
from .environment_wheel_import import reopen_wheel_import
from .runtime_java import JavaRuntimeError, host_platform


FORMAT = "workbench-environment-package-closure-plan-v1"
_MANIFEST_FORMAT = "workbench-native-wheelhouse-v1"
_WHEEL_LIMIT = 512 * 1024 * 1024
_MAX_WHEELS = 256
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_WHEEL_FILE = re.compile(r"[A-Za-z0-9_.+!-]+\.whl\Z")
_PIP_BOOTSTRAP = ("26.1.2", "pip-26.1.2-py3-none-any.whl")


def _ordinary(path: Path, *, directory: bool = False) -> None:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ReconstructionError("package wheelhouse requires absolute local paths")
    if any(part.is_symlink() or getattr(part, "is_junction", lambda: False)()
           for part in (path, *path.parents)):
        raise ReconstructionError("package wheelhouse traverses a redirect")
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReconstructionError(f"package wheelhouse source is unavailable: {exc}") from exc
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ReconstructionError("package wheelhouse contains a nonordinary source")


def _read(path: Path, limit: int) -> bytes:
    _ordinary(path)
    try:
        raw = read_bounded_bytes(path, byte_limit=limit)
    except OSError as exc:
        raise ReconstructionError(f"package wheelhouse source cannot be read: {exc}") from exc
    _ordinary(path)
    return raw


def _digest_file(path: Path, expected_size: int) -> str:
    _ordinary(path)
    before = path.stat()
    if before.st_size != expected_size or not 0 < expected_size <= _WHEEL_LIMIT:
        raise ReconstructionError("package wheel size differs from its manifest")
    digest = sha256()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReconstructionError(f"package wheel cannot be opened: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ReconstructionError("package wheel changed before inspection")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            size = 0
            while block := source.read(1024 * 1024):
                size += len(block)
                if size > expected_size:
                    raise ReconstructionError("package wheel grew during inspection")
                digest.update(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    _ordinary(path)
    visible = path.stat()
    if (size != expected_size or
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (visible.st_dev, visible.st_ino, visible.st_size, visible.st_mtime_ns)):
        raise ReconstructionError("package wheel changed during inspection")
    return digest.hexdigest()


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReconstructionError("duplicate package wheelhouse manifest key")
        result[key] = value
    return result


def _wheelhouse(root: Path) -> tuple[dict[str, Any], str, str]:
    """Mirror the standalone native manifest/lock admission at Core's boundary."""

    _ordinary(root, directory=True)
    raw = _read(root / "wheelhouse.json", 1024 * 1024)
    try:
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise ReconstructionError(f"invalid package wheelhouse manifest: {exc}") from exc
    if (type(manifest) is not dict or set(manifest) != {
            "format", "source_sha256", "selected_components", "native_versions",
            "target", "wheels", "qualified",
        } or manifest["format"] != _MANIFEST_FORMAT or manifest["qualified"] is not False
            or type(manifest["source_sha256"]) is not str
            or _DIGEST.fullmatch(manifest["source_sha256"]) is None):
        raise ReconstructionError("package wheelhouse has an unsupported native manifest")
    target = manifest["target"]
    if (type(target) is not dict or set(target) != {"python", "platform", "machine"}
            or any(type(value) is not str or not value for value in target.values())):
        raise ReconstructionError("package wheelhouse has an invalid target")
    expected = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "platform": sys.platform, "machine": platform.machine(),
    }
    if target != expected or not (3, 12) <= sys.version_info[:2] < (3, 15):
        raise ReconstructionError("package wheelhouse targets another Python/OS/architecture")
    rows = manifest["wheels"]
    if type(rows) is not list or not 0 < len(rows) <= _MAX_WHEELS:
        raise ReconstructionError("package wheelhouse has no bounded wheel set")
    wheels = root / "wheels"
    _ordinary(wheels, directory=True)
    names: set[str] = set()
    distributions: set[str] = set()
    for row in rows:
        if (type(row) is not dict or set(row) != {"filename", "name", "version", "size", "sha256"}
                or type(row["filename"]) is not str or _WHEEL_FILE.fullmatch(row["filename"]) is None
                or type(row["name"]) is not str
                or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", row["name"])
                or type(row["version"]) is not str
                or not re.fullmatch(r"[A-Za-z0-9_.+!]+", row["version"])
                or type(row["size"]) is not int or not 0 < row["size"] <= _WHEEL_LIMIT
                or type(row["sha256"]) is not str or _DIGEST.fullmatch(row["sha256"]) is None
                or row["filename"] in names or row["name"] in distributions):
            raise ReconstructionError("package wheelhouse has an invalid or duplicate wheel record")
        if _digest_file(wheels / row["filename"], row["size"]) != row["sha256"]:
            raise ReconstructionError("package wheel differs from its manifest")
        names.add(row["filename"])
        distributions.add(row["name"])
    if {path.name for path in wheels.iterdir()} != names:
        raise ReconstructionError("package wheelhouse contains missing or extra wheels")
    versions = {row["name"]: row["version"] for row in rows}
    native = manifest["native_versions"]
    if (type(native) is not dict or not native
            or any(type(name) is not str or not name.startswith("workbench-")
                   or type(version) is not str or versions.get(name) != version
                   for name, version in native.items())
            or {name for name in versions if name.startswith("workbench-")} != set(native)):
        raise ReconstructionError("package wheelhouse native component lock differs")
    pip = [row for row in rows if row["name"] == "pip"]
    if len(pip) != 1 or (pip[0]["version"], pip[0]["filename"]) != _PIP_BOOTSTRAP:
        raise ReconstructionError("package wheelhouse lacks the supported pip bootstrap")
    selected = manifest["selected_components"]
    if (type(selected) is not list or not selected
            or any(type(name) is not str for name in selected)
            or len(set(selected)) != len(selected) or not set(selected) <= set(native)):
        raise ReconstructionError("package wheelhouse selected components differ")
    lock = _read(root / "requirements.lock", 1024 * 1024)
    expected_lock = "".join(
        f"{row['name']}=={row['version']} --hash=sha256:{row['sha256']}\n"
        for row in rows
    ).encode("utf-8")
    if lock != expected_lock:
        raise ReconstructionError("package wheelhouse requirements lock differs")
    return manifest, sha256(raw).hexdigest(), sha256(lock).hexdigest()


def _metadata(path: Path, row: Mapping[str, Any]) -> dict[str, Any]:
    """Inspect exact wheel metadata and reject unsafe archive structure."""

    try:
        filename_name, filename_version, _, tags = parse_wheel_filename(row["filename"])
        if (filename_name != row["name"] or str(filename_version) != row["version"]
                or not tags.intersection(sys_tags())):
            raise ReconstructionError("package wheel filename or target tags differ")
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if (len(names) != len(set(names)) or len(names) > 100_000
                    or sum(entry.file_size for entry in entries) > 2 * 1024 * 1024 * 1024):
                raise ReconstructionError("package wheel has duplicate or excessive members")
            for entry in entries:
                member = PurePosixPath(entry.filename)
                if (not member.parts or member.is_absolute() or ".." in member.parts
                        or "\\" in entry.filename or ":" in entry.filename
                        or "\0" in entry.filename
                        or stat.S_ISLNK(entry.external_attr >> 16)):
                    raise ReconstructionError("package wheel has an unsafe member")
            if archive.testzip() is not None:
                raise ReconstructionError("package wheel has a corrupt ZIP member")
            matches = [name for name in names if name.endswith(".dist-info/METADATA")]
            if len(matches) != 1 or archive.getinfo(matches[0]).file_size > 1024 * 1024:
                raise ReconstructionError("package wheel needs one bounded metadata record")
            metadata_root = matches[0].split("/", 1)[0].removesuffix(".dist-info")
            if "-" not in metadata_root:
                raise ReconstructionError("package wheel has an invalid metadata owner")
            metadata_name, metadata_version = metadata_root.rsplit("-", 1)
            if (canonicalize_name(metadata_name) != row["name"]
                    or Version(metadata_version) != Version(row["version"])):
                raise ReconstructionError("package wheel metadata owner differs from its filename")
            scheme = matches[0].removesuffix("METADATA") + "WHEEL"
            if ([name for name in names if name.endswith(".dist-info/WHEEL")] != [scheme]
                    or archive.getinfo(scheme).file_size > 65536):
                raise ReconstructionError("package wheel lacks its bounded installation scheme")
            wheel_message = BytesParser().parsebytes(archive.read(scheme))
            purelib = wheel_message.get_all("Root-Is-Purelib", [])
            version = wheel_message.get_all("Wheel-Version", [])
            declared_tags = wheel_message.get_all("Tag", [])
            if (len(purelib) != 1 or purelib[0].lower() not in {"true", "false"}
                    or len(version) != 1 or version[0].split(".", 1)[0] != "1"
                    or not declared_tags or not set(declared_tags) & {str(tag) for tag in tags}):
                raise ReconstructionError("package wheel has an invalid installation scheme")
            message = BytesParser().parsebytes(archive.read(matches[0]))
            if (len(message.get_all("Name", [])) != 1
                    or len(message.get_all("Version", [])) != 1
                    or canonicalize_name(message["Name"]) != row["name"]
                    or Version(message["Version"]) != Version(row["version"])):
                raise ReconstructionError("package wheel metadata differs from its filename")
            python = message.get_all("Requires-Python", [])
            if len(python) > 1 or (python and not SpecifierSet(python[0]).contains(
                    ".".join(map(str, sys.version_info[:3])))):
                raise ReconstructionError("package wheel requires another Python version")
            requirements = []
            for text in message.get_all("Requires-Dist", []):
                requirement = Requirement(text)
                if requirement.url:
                    raise ReconstructionError("package wheel declares a direct URL dependency")
                requirements.append(requirement)
            extras = {canonicalize_name(value) for value in message.get_all("Provides-Extra", [])}
        if _digest_file(path, row["size"]) != row["sha256"]:
            raise ReconstructionError("package wheel changed during metadata inspection")
        return {"requirements": requirements, "extras": extras}
    except (OSError, ValueError, RuntimeError, NotImplementedError, zipfile.BadZipFile, KeyError) as exc:
        if isinstance(exc, ReconstructionError):
            raise
        raise ReconstructionError(f"package wheel metadata cannot be admitted: {exc}") from exc


def _fixture_owner_code(path: Path, owner: Mapping[str, Any]) -> None:
    """Compare the candidate's installed-source identity to exact wheel members."""

    module = owner["module"]
    if (not isinstance(module, str) or not all(
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part) for part in module.split("."))):
        raise ReconstructionError("fixture owner module has no package source path")
    module_path = module.replace(".", "/")
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            choices = (module_path + ".py", module_path + "/__init__.py")
            found = [name for name in choices if name in names]
            if len(found) != 1:
                raise ReconstructionError("fixture owner wheel has no unambiguous module source")
            source_name = found[0]
            if "/" not in source_name:
                raise ReconstructionError("fixture owner module has no private package subtree")
            prefix = source_name.rsplit("/", 1)[0] + "/"
            if source_name not in names or archive.getinfo(source_name).file_size != owner["size"]:
                raise ReconstructionError("fixture owner code size differs from its wheel")
            if sha256(archive.read(source_name)).hexdigest() != owner["sha256"]:
                raise ReconstructionError("fixture owner code bytes differ from its wheel")
            source_rows = []
            for name in sorted(names):
                if not name.startswith(prefix) or name.endswith("/"):
                    continue
                relative = name.removeprefix(prefix)
                if "__pycache__" in PurePosixPath(relative).parts or Path(relative).suffix in {".pyc", ".pyo"}:
                    continue
                if archive.getinfo(name).file_size > 2 * 1024 * 1024 or len(source_rows) >= 256:
                    raise ReconstructionError("fixture owner package source exceeds its identity bound")
                source_rows.append((relative, sha256(archive.read(name)).hexdigest()))
            observed = sha256(json.dumps(source_rows, separators=(",", ":")).encode()).hexdigest()
            if observed != owner["package_source_sha256"]:
                raise ReconstructionError("fixture owner package source differs from its wheel")
            entries = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
            if len(entries) != 1 or archive.getinfo(entries[0]).file_size > 65536:
                raise ReconstructionError("fixture owner wheel lacks bounded entry points")
            parser = configparser.ConfigParser(interpolation=None)
            parser.optionxform = str
            parser.read_string(archive.read(entries[0]).decode("utf-8"))
            group = owner["group"]
            profile = owner["profile_id"]
            if (not parser.has_section(group) or not parser.has_option(group, profile)
                    or parser.get(group, profile).partition(":")[0].strip() != module):
                raise ReconstructionError("fixture owner extension differs from its wheel")
    except (OSError, UnicodeError, configparser.Error, zipfile.BadZipFile) as exc:
        raise ReconstructionError(f"fixture owner wheel cannot be inspected: {exc}") from exc


def _closure(rows: list[dict[str, Any]], wheelhouse: Path, roots: set[str]) -> list[str]:
    records = {row["name"]: row for row in rows}
    parsed = {name: _metadata(wheelhouse / "wheels" / row["filename"], row)
              for name, row in records.items()}
    required: dict[str, set[str]] = {}
    pending = [Requirement(name) for name in sorted(roots)]
    marker_environment = default_environment()
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        row = records.get(name)
        if row is None or Version(row["version"]) not in requirement.specifier:
            raise ReconstructionError(f"package wheelhouse cannot satisfy {requirement}")
        extras = {canonicalize_name(extra) for extra in requirement.extras}
        if extras - parsed[name]["extras"]:
            raise ReconstructionError(f"package wheelhouse lacks an extra for {name}")
        requested = extras | {""}
        previous = required.get(name, set())
        if name in required and requested <= previous:
            continue
        active = previous | requested
        required[name] = active
        for dependency in parsed[name]["requirements"]:
            if dependency.marker is None or any(dependency.marker.evaluate(
                    environment={**marker_environment, "extra": extra}) for extra in active):
                pending.append(dependency)
    if set(required) != set(records):
        raise ReconstructionError("package wheelhouse contains wheels outside the exact closure")
    return sorted(required)


def plan_package_closure(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, wheel_resource_id: str, wheelhouse: Path,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Review a complete local wheel set without staging or installing it."""

    portable = validate_share(dict(share))
    if portable["format"] != SHARE_FORMAT_V3:
        raise ReconstructionError("package closure requires a V3 environment share")
    reviewed = validate_input_candidate(portable, dict(candidate))
    try:
        executing = _host_variant(host_platform())
    except (JavaRuntimeError, ReconstructionError) as exc:
        raise ReconstructionError(f"package closure has no supported host: {exc}") from exc
    if executing != reviewed["host_variant"] or executing["os"] != "linux":
        raise ReconstructionError("package closure requires the selected Linux/WSL host")
    if not isinstance(wheelhouse, Path) or not wheelhouse.is_absolute():
        raise ReconstructionError("select an absolute local native wheelhouse")
    retained = reopen_wheel_import(
        suite_root, portable, reviewed, workspace=workspace,
        result_resource_id=wheel_resource_id, environment=environment,
    )
    manifest, manifest_sha, lock_sha = _wheelhouse(wheelhouse)
    records = {row["name"]: row for row in manifest["wheels"]}
    for package in retained["packages"]:
        row = records.get(package["distribution"])
        if (row is None or row["version"] != package["version"]
                or row["sha256"] != package["sha256"].removeprefix("sha256:")
                or row["size"] != package["size"]):
            raise ReconstructionError("package wheelhouse differs from Core-retained optional wheels")
    owner = reviewed["profile_fixture"]["owner_code"]
    owner_distribution = canonicalize_name(owner["distribution"])
    owner_row = records.get(owner_distribution)
    if owner_row is None or owner_row["version"] != owner["version"]:
        raise ReconstructionError("package wheelhouse lacks the admitted fixture owner version")
    selected = {"workbench-core", owner_distribution}
    selected.update(row["distribution"] for row in reviewed["packages"]
                    if row["distribution"].startswith("workbench-"))
    if (not owner_distribution.startswith("workbench-")
            or set(manifest["selected_components"]) != selected):
        raise ReconstructionError("package wheelhouse component selection differs from the V3 candidate")
    _fixture_owner_code(wheelhouse / "wheels" / owner_row["filename"], owner)
    roots = {"workbench-core", "workbench-api", "pip", owner_distribution}
    roots.update(row["distribution"] for row in reviewed["packages"])
    closure = _closure(manifest["wheels"], wheelhouse, roots)
    # A plan is a review receipt only. Exact bytes and module/profile admission
    # still need a separate custody and isolated installation operation.
    return _seal({
        "format": FORMAT, "schema_version": 1,
        "share_id": portable["share_id"], "candidate_id": reviewed["candidate_id"],
        "wheel_resource_id": wheel_resource_id,
        "wheel_tree_id": retained["tree_id"],
        "wheelhouse": str(wheelhouse),
        "manifest_sha256": "sha256:" + manifest_sha,
        "requirements_lock_sha256": "sha256:" + lock_sha,
        "target": manifest["target"], "host_variant": executing,
        "fixture_owner": {
            "distribution": owner_distribution, "version": owner["version"],
            "wheel_sha256": "sha256:" + owner_row["sha256"],
            "wheel_size": owner_row["size"],
            "code_sha256": owner["sha256"], "code_size": owner["size"],
            "package_source_sha256": owner["package_source_sha256"],
        },
        "wheels": manifest["wheels"], "closure": closure,
        "unresolved_inputs": list(portable["lock"]["unresolved_inputs"]),
        "coverage": "reviewed-offline-dependency-closure-only", "state": "reviewed",
    }, "workbench-environment-package-closure-plan", "plan_id")


__all__ = ["plan_package_closure"]
