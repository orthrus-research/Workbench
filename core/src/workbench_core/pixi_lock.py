"""Strict, target-scoped projections of a Pixi workspace lock.

The raw manifest and lock are source inputs.  A closure produced here is a
derived description of the bytes selected for one environment and one target;
it is not a second dependency authority and it never edits either input.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tomllib
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

import yaml


FORMAT = "workbench-pixi-environment-closure-v1"
SCHEMA_VERSION = 1
BINDING_FORMAT = "workbench-pixi-materialization-binding-v1"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
EXACT_VERSION = re.compile(r"^==([0-9]+(?:\.[0-9]+){1,3}(?:[-+][0-9A-Za-z.-]+)?)$")
MAX_PIXI_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_PIXI_LOCK_BYTES = 64 * 1024 * 1024
_CUSTODY_FIELDS = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
_CROSS_VIEW_CUSTODY_FIELDS = tuple(
    field for field in _CUSTODY_FIELDS if field != "st_ctime_ns"
)


class PixiLockError(ValueError):
    """The selected Pixi manifest/lock projection is incomplete or ambiguous."""


@dataclass(frozen=True, slots=True)
class BoundPixiInputs:
    """One descriptor-custodied read of the exact manifest and lock bytes."""

    manifest_path: Path
    manifest_bytes: bytes
    manifest_sha256: str
    lock_path: Path
    lock_bytes: bytes
    lock_sha256: str


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _content_id(prefix: str, value: Mapping[str, Any], field: str) -> str:
    body = dict(value)
    body.pop(field, None)
    return prefix + hashlib.sha256(_canonical_bytes(body)).hexdigest()


def _same_custody(
    left: os.stat_result,
    right: os.stat_result,
    fields: tuple[str, ...],
) -> bool:
    return all(getattr(left, field) == getattr(right, field) for field in fields)


def _stable_custody(
    before: os.stat_result,
    opened: os.stat_result,
    after: os.stat_result,
    current: os.stat_result,
) -> bool:
    # Windows can expose a different stable ctime through a path and an open
    # handle.  Preserve ctime within each view and compare every other identity
    # field across views.
    return (
        _same_custody(before, current, _CUSTODY_FIELDS)
        and _same_custody(opened, after, _CUSTODY_FIELDS)
        and _same_custody(before, opened, _CROSS_VIEW_CUSTODY_FIELDS)
        and _same_custody(current, after, _CROSS_VIEW_CUSTODY_FIELDS)
    )


def _bound_regular_bytes(path: Path, *, label: str, maximum: int) -> tuple[Path, bytes]:
    """Read one bounded regular file once while retaining descriptor custody."""

    absolute = Path(os.path.abspath(os.fspath(path)))
    descriptor = -1
    try:
        before = os.lstat(absolute)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or not 1 <= before.st_size <= maximum
        ):
            raise PixiLockError(f"{label} is not one bounded ordinary file: {absolute}")
        descriptor = os.open(
            absolute,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not 1 <= opened.st_size <= maximum
            or not _same_custody(
                before,
                opened,
                _CROSS_VIEW_CUSTODY_FIELDS,
            )
        ):
            raise PixiLockError(f"{label} changed while it was opened: {absolute}")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        current = os.lstat(absolute)
    except PixiLockError:
        raise
    except OSError as error:
        raise PixiLockError(f"{label} cannot be read safely: {absolute}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        not _stable_custody(before, opened, after, current)
        or len(raw) != opened.st_size
        or not 1 <= len(raw) <= maximum
    ):
        raise PixiLockError(f"{label} changed while it was read: {absolute}")
    return absolute, raw


class _UniqueSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects every duplicate mapping key."""


def _construct_unique_mapping(
    loader: _UniqueSafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    value: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in value
        except TypeError as error:
            raise PixiLockError("Pixi lock contains an unhashable mapping key") from error
        if duplicate:
            raise PixiLockError(f"Pixi lock contains a duplicate mapping key: {key!r}")
        value[key] = loader.construct_object(value_node, deep=deep)
    return value


_UniqueSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _parse_toml(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = tomllib.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, tomllib.TOMLDecodeError) as error:
        raise PixiLockError(f"{label} is not valid UTF-8 TOML") from error
    if type(value) is not dict:
        raise PixiLockError(f"{label} root must be a table")
    return value


def _parse_lock(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = yaml.load(
            raw.decode("utf-8", errors="strict"),
            Loader=_UniqueSafeLoader,
        )
    except (UnicodeError, yaml.YAMLError) as error:
        raise PixiLockError(f"{label} is not valid UTF-8 YAML") from error
    if type(value) is not dict:
        raise PixiLockError(f"{label} root must be an object")
    return value


def bind_pixi_inputs(manifest_path: Path, lock_path: Path) -> BoundPixiInputs:
    """Read and retain the exact manifest/lock bytes used by later derivation."""

    bound_manifest_path, manifest_raw = _bound_regular_bytes(
        manifest_path,
        label="Pixi manifest",
        maximum=MAX_PIXI_MANIFEST_BYTES,
    )
    bound_lock_path, lock_raw = _bound_regular_bytes(
        lock_path,
        label="Pixi lock",
        maximum=MAX_PIXI_LOCK_BYTES,
    )
    return BoundPixiInputs(
        manifest_path=bound_manifest_path,
        manifest_bytes=manifest_raw,
        manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
        lock_path=bound_lock_path,
        lock_bytes=lock_raw,
        lock_sha256=hashlib.sha256(lock_raw).hexdigest(),
    )


def parse_pixi_manifest_bytes(raw: bytes) -> dict[str, Any]:
    """Parse already-custodied Pixi manifest bytes."""

    return _parse_toml(raw, label="Pixi manifest")


def parse_pixi_lock_bytes(raw: bytes) -> dict[str, Any]:
    """Parse already-custodied Pixi lock bytes with duplicate-key rejection."""

    return _parse_lock(raw, label="Pixi lock")


def file_sha256(path: Path) -> str:
    """Return the exact byte identity of one ordinary input file."""

    _absolute, raw = _bound_regular_bytes(
        path,
        label="Pixi input",
        maximum=MAX_PIXI_LOCK_BYTES,
    )
    return hashlib.sha256(raw).hexdigest()


def _required_pixi_version(manifest: Mapping[str, Any]) -> str:
    workspace = manifest.get("workspace")
    requirement = workspace.get("requires-pixi") if type(workspace) is dict else None
    match = EXACT_VERSION.fullmatch(requirement) if type(requirement) is str else None
    if match is None:
        raise PixiLockError("Pixi manifest must pin workspace.requires-pixi exactly")
    return match.group(1)


def required_pixi_version(manifest_path: Path) -> str:
    """Return the manifest's exact ``requires-pixi`` version."""

    _absolute, raw = _bound_regular_bytes(
        manifest_path,
        label="Pixi manifest",
        maximum=MAX_PIXI_MANIFEST_BYTES,
    )
    return _required_pixi_version(parse_pixi_manifest_bytes(raw))


def feature_dependency_version(
    manifest_path: Path,
    *,
    feature: str,
    dependency: str,
) -> str:
    """Return one exact Conda dependency pin from a named Pixi feature."""

    _absolute, raw = _bound_regular_bytes(
        manifest_path,
        label="Pixi manifest",
        maximum=MAX_PIXI_MANIFEST_BYTES,
    )
    manifest = parse_pixi_manifest_bytes(raw)
    features = manifest.get("feature")
    selected = features.get(feature) if type(features) is dict else None
    dependencies = selected.get("dependencies") if type(selected) is dict else None
    requirement = dependencies.get(dependency) if type(dependencies) is dict else None
    match = EXACT_VERSION.fullmatch(requirement) if type(requirement) is str else None
    if match is None:
        raise PixiLockError(
            f"Pixi feature {feature!r} must pin dependency {dependency!r} exactly"
        )
    return match.group(1)


def _manifest_platform(manifest: Mapping[str, Any], selected: str) -> dict[str, Any]:
    workspace = manifest.get("workspace")
    platforms = workspace.get("platforms") if type(workspace) is dict else None
    if type(platforms) is not list:
        raise PixiLockError("Pixi manifest workspace.platforms must be an array")
    matches: list[dict[str, Any]] = []
    for row in platforms:
        if type(row) is not dict:
            raise PixiLockError("Pixi manifest contains a malformed platform row")
        name = row.get("name")
        subdir = row.get("platform")
        if not all(type(item) is str and item for item in (name, subdir)):
            raise PixiLockError("Pixi manifest platform name/subdir is incomplete")
        if selected in {name, subdir}:
            matches.append(row)
    if len(matches) != 1:
        raise PixiLockError(f"Pixi platform is absent or ambiguous: {selected}")
    return matches[0]


def _lock_platform(lock: Mapping[str, Any], subdir: str) -> dict[str, Any]:
    platforms = lock.get("platforms")
    if type(platforms) is not list:
        raise PixiLockError("Pixi lock platforms must be an array")
    matches = [
        row
        for row in platforms
        if type(row) is dict and row.get("subdir") == subdir
    ]
    if len(matches) != 1:
        raise PixiLockError(f"Pixi lock platform is absent or ambiguous: {subdir}")
    row = matches[0]
    key = row.get("name")
    virtual = row.get("virtual-packages")
    if (
        type(key) is not str
        or not key
        or type(virtual) is not list
        or any(type(item) is not str or not item for item in virtual)
    ):
        raise PixiLockError("Pixi lock platform identity is incomplete")
    return row


def _package_identity(row: Mapping[str, Any]) -> tuple[str, str]:
    kinds = [kind for kind in ("conda", "pypi") if kind in row]
    if len(kinds) != 1 or type(row.get(kinds[0])) is not str or not row[kinds[0]]:
        raise PixiLockError("Pixi lock package must have one source URL")
    return kinds[0], row[kinds[0]]


def _name_version(kind: str, location: str, row: Mapping[str, Any]) -> tuple[str, str]:
    if kind == "pypi":
        name = row.get("name")
        version = row.get("version")
    else:
        filename = unquote(PurePosixPath(urlparse(location).path).name)
        stem = filename.removesuffix(".conda").removesuffix(".tar.bz2")
        try:
            name, version, _build = stem.rsplit("-", 2)
        except ValueError as error:
            raise PixiLockError(
                f"cannot derive locked Conda package identity: {location}"
            ) from error
    if not all(type(item) is str and item for item in (name, version)):
        raise PixiLockError("Pixi lock package name/version is incomplete")
    return name, version


def _build_environment_closure_values(
    manifest: Mapping[str, Any],
    lock: Mapping[str, Any],
    *,
    environment: str,
    platform: str,
) -> dict[str, Any]:
    if type(environment) is not str or not environment:
        raise PixiLockError("Pixi environment must be a non-empty string")
    manifest_platform = _manifest_platform(manifest, platform)
    lock_platform = _lock_platform(lock, manifest_platform["platform"])
    environments = lock.get("environments")
    selected = environments.get(environment) if type(environments) is dict else None
    packages_by_platform = selected.get("packages") if type(selected) is dict else None
    references = (
        packages_by_platform.get(lock_platform["name"])
        if type(packages_by_platform) is dict
        else None
    )
    if type(references) is not list or not references:
        raise PixiLockError(
            f"Pixi environment {environment!r} has no closure for {platform!r}"
        )

    package_rows = lock.get("packages")
    if type(package_rows) is not list:
        raise PixiLockError("Pixi lock package records must be an array")
    by_identity: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in package_rows:
        if type(row) is not dict:
            raise PixiLockError("Pixi lock contains a malformed package record")
        identity = _package_identity(row)
        if identity in by_identity:
            raise PixiLockError(f"Pixi lock package identity is duplicated: {identity[1]}")
        by_identity[identity] = row

    reference_identities: list[tuple[str, str]] = []
    for reference in references:
        if type(reference) is not dict:
            raise PixiLockError("Pixi environment contains a malformed package reference")
        reference_identities.append(_package_identity(reference))
    occurrences = Counter(reference_identities)
    rows: list[dict[str, Any]] = []
    for identity, count in occurrences.items():
        source = by_identity.get(identity)
        if source is None:
            raise PixiLockError(f"Pixi environment references an unknown package: {identity[1]}")
        digest = source.get("sha256")
        size = source.get("size")
        if (
            type(digest) is not str
            or SHA256.fullmatch(digest) is None
            or (size is not None and (type(size) is not int or size < 1))
        ):
            raise PixiLockError(f"Pixi package byte identity is incomplete: {identity[1]}")
        name, version = _name_version(identity[0], identity[1], source)
        purls = source.get("purls", [])
        declared_license = source.get("license")
        if declared_license is not None and type(declared_license) is not str:
            raise PixiLockError(
                f"Pixi package license metadata is malformed: {identity[1]}"
            )
        if (
            type(purls) is not list
            or any(type(item) is not str for item in purls)
            or len(purls) != len(set(purls))
        ):
            raise PixiLockError(f"Pixi package purl metadata is malformed: {identity[1]}")
        rows.append(
            {
                "declared_license": declared_license,
                "ecosystem": identity[0],
                "location": identity[1],
                "name": name,
                "occurrences": count,
                "purls": sorted(purls, key=lambda item: item.encode("utf-8")),
                "sha256": digest,
                "size": size,
                "version": version,
            }
        )
    rows.sort(
        key=lambda row: (
            row["ecosystem"].encode("utf-8"),
            row["name"].encode("utf-8"),
            row["version"].encode("utf-8"),
            row["location"].encode("utf-8"),
        )
    )
    channels = selected.get("channels")
    indexes = selected.get("indexes")
    if (
        type(channels) is not list
        or any(
            type(item) is not dict
            or type(item.get("url")) is not str
            or not item["url"]
            for item in channels
        )
        or type(indexes) is not list
        or any(type(item) is not str or not item for item in indexes)
    ):
        raise PixiLockError("Pixi environment source configuration is malformed")
    lock_version = lock.get("version")
    if type(lock_version) is not int or lock_version < 1:
        raise PixiLockError("Pixi lock version is invalid")

    value: dict[str, Any] = {
        "channels": [item["url"] for item in channels],
        "closure_id": "",
        "environment": environment,
        "format": FORMAT,
        "indexes": list(indexes),
        "lock_version": lock_version,
        "packages": rows,
        "platform": {
            "lock_key": lock_platform["name"],
            "name": manifest_platform["name"],
            "subdir": manifest_platform["platform"],
            "virtual_packages": sorted(
                lock_platform["virtual-packages"], key=lambda item: item.encode("utf-8")
            ),
        },
        "schema_version": SCHEMA_VERSION,
    }
    value["closure_id"] = _content_id(
        "workbench-pixi-environment-closure-v1:sha256:", value, "closure_id"
    )
    return value


def build_environment_closure_from_bound_inputs(
    inputs: BoundPixiInputs,
    *,
    environment: str,
    platform: str,
) -> dict[str, Any]:
    """Derive a target closure only from already-custodied input bytes."""

    if not isinstance(inputs, BoundPixiInputs):
        raise PixiLockError("Pixi closure requires bound manifest and lock bytes")
    return _build_environment_closure_values(
        parse_pixi_manifest_bytes(inputs.manifest_bytes),
        parse_pixi_lock_bytes(inputs.lock_bytes),
        environment=environment,
        platform=platform,
    )


def build_environment_closure(
    manifest_path: Path,
    lock_path: Path,
    *,
    environment: str,
    platform: str,
) -> dict[str, Any]:
    """Project one exact environment/target closure from a Pixi lock.

    The identity intentionally excludes the raw whole-file hashes.  Unrelated
    feature or platform changes therefore do not alter a runtime closure.  Use
    :func:`build_materialization_binding` when exact source-file custody is also
    required.
    """

    return build_environment_closure_from_bound_inputs(
        bind_pixi_inputs(manifest_path, lock_path),
        environment=environment,
        platform=platform,
    )


def build_materialization_binding_from_bound_inputs(
    inputs: BoundPixiInputs,
    *,
    environment: str,
    platform: str,
) -> dict[str, Any]:
    """Bind a closure and hashes derived from the same retained bytes."""

    if not isinstance(inputs, BoundPixiInputs):
        raise PixiLockError("Pixi binding requires bound manifest and lock bytes")
    manifest = parse_pixi_manifest_bytes(inputs.manifest_bytes)
    lock = parse_pixi_lock_bytes(inputs.lock_bytes)
    closure = _build_environment_closure_values(
        manifest,
        lock,
        environment=environment,
        platform=platform,
    )
    value: dict[str, Any] = {
        "binding_id": "",
        "closure": closure,
        "format": BINDING_FORMAT,
        "lock": {"path": inputs.lock_path.name, "sha256": inputs.lock_sha256},
        "manifest": {
            "path": inputs.manifest_path.name,
            "requires_pixi": _required_pixi_version(manifest),
            "sha256": inputs.manifest_sha256,
        },
        "schema_version": SCHEMA_VERSION,
    }
    value["binding_id"] = _content_id(
        "workbench-pixi-materialization-binding-v1:sha256:", value, "binding_id"
    )
    return value


def build_materialization_binding(
    manifest_path: Path,
    lock_path: Path,
    *,
    environment: str,
    platform: str,
) -> dict[str, Any]:
    """Bind a scoped closure to the exact manifest and lock bytes used."""

    return build_materialization_binding_from_bound_inputs(
        bind_pixi_inputs(manifest_path, lock_path),
        environment=environment,
        platform=platform,
    )


__all__ = [
    "BINDING_FORMAT",
    "BoundPixiInputs",
    "FORMAT",
    "MAX_PIXI_LOCK_BYTES",
    "MAX_PIXI_MANIFEST_BYTES",
    "PixiLockError",
    "SCHEMA_VERSION",
    "bind_pixi_inputs",
    "build_environment_closure",
    "build_environment_closure_from_bound_inputs",
    "build_materialization_binding",
    "build_materialization_binding_from_bound_inputs",
    "feature_dependency_version",
    "file_sha256",
    "parse_pixi_lock_bytes",
    "parse_pixi_manifest_bytes",
    "required_pixi_version",
]
