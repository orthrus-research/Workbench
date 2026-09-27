"""Profile-owned identity and content check for the remapped daily-loop JAR.

Core supplies exact retained source and artifact bytes. This module never
publishes, installs, or executes an artifact.
"""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
from pathlib import PurePosixPath
import re
import stat
from typing import Any, Mapping
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile


FORMAT = "workbench-cleanroom-fixture-artifact-spec-v1"
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
_MAX_MEMBERS = 4096
_MAX_EXPANDED_BYTES = 64 * 1024 * 1024
_MAX_MEMBER_BYTES = 8 * 1024 * 1024
_ARCHIVE = re.compile(r"[A-Za-z][A-Za-z0-9._-]*\Z")
_VERSION = re.compile(r"[0-9][A-Za-z0-9._-]*\Z")
_GENERATED_ROOT = ".workbench/build/cleanroom/0.6.8-alpha/generic-mod-daily-loop"
_MODID = "workbench_daily_loop"
_REQUIRED = frozenset({
    "mcmod.info", "pack.mcmeta", "mixins.workbench_daily_loop.json",
    "mixins.workbench_daily_loop.refmap.json",
    "assets/workbench_daily_loop/lang/en_us.lang",
    "assets/workbench_daily_loop/blockstates/probe_block.json",
    "assets/workbench_daily_loop/models/block/probe_block.json",
    "assets/workbench_daily_loop/models/item/probe_block.json",
    "dev/workbench/dailyloop/DailyLoopMod.class",
    "dev/workbench/dailyloop/DailyLoopContent.class",
    "dev/workbench/dailyloop/DailyLoopProbe.class",
    "dev/workbench/dailyloop/mixin/MixinBlock.class",
    "META-INF/MANIFEST.MF",
})


def _properties(raw: bytes) -> dict[str, str]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ValueError("fixture Gradle properties are not UTF-8") from exc
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!")):
            continue
        key, separator, value = stripped.partition("=")
        key = key.strip()
        if not separator or not key or key in values:
            raise ValueError("fixture Gradle properties are ambiguous")
        values[key] = value.strip()
    return values


def artifact_spec(
    *, fixture_lock: Mapping[str, Any], execution_policy: Mapping[str, Any],
    gradle_properties: bytes,
) -> dict[str, Any]:
    """Derive the sole remapped JAR path from retained locked owner inputs."""

    try:
        declared = fixture_lock["declared_values"]
        fixture = execution_policy["fixture"]
        generated = execution_policy["paths"]["generated_root_relative_to_projection_digest"]
        properties_row, = (
            row for row in declared["files"] if row["path"] == "gradle.properties"
        )
        if (fixture != {
                "declaration_id": fixture_lock["declaration_id"],
                "tree_digest": declared["tree_digest"],
            }
                or generated != _GENERATED_ROOT
                or properties_row["size"] != len(gradle_properties)
                or properties_row["sha256"] != "sha256:" + sha256(gradle_properties).hexdigest()):
            raise ValueError("fixture artifact inputs differ from their owner lock")
        values = _properties(gradle_properties)
        archive = values["fixture_archive"]
        version = values["fixture_version"]
        if (_ARCHIVE.fullmatch(archive) is None or _VERSION.fullmatch(version) is None):
            raise ValueError("fixture artifact name or version is unsafe")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"fixture artifact has no exact owner identity: {exc}") from exc
    filename = f"{archive}-{version}.jar"
    return {
        "format": FORMAT, "schema_version": 1,
        "relative_path": f"{generated}/libs/{filename}",
        "filename": filename, "modid": _MODID, "version": version,
        "maximum_bytes": MAX_ARTIFACT_BYTES,
    }


def _manifest_main(raw: bytes) -> dict[str, str]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ValueError("fixture JAR manifest is not UTF-8") from exc
    fields: dict[str, str] = {}
    key: str | None = None
    for line in lines:
        if not line:
            break
        if line.startswith(" "):
            if key is None:
                raise ValueError("fixture JAR manifest has an orphan continuation")
            fields[key] += line[1:]
            continue
        name, separator, value = line.partition(": ")
        if not separator or not name or name.lower() in fields:
            raise ValueError("fixture JAR manifest has an ambiguous main attribute")
        key = name.lower()
        fields[key] = value
    return fields


def inspect_artifact_bytes(raw: bytes, *, spec: Mapping[str, Any]) -> dict[str, Any]:
    """Validate every ZIP payload and the Cleanroom mod's exact identity."""

    if (type(raw) is not bytes or not 0 < len(raw) <= MAX_ARTIFACT_BYTES
            or type(spec) is not dict or spec.get("format") != FORMAT
            or spec.get("maximum_bytes") != MAX_ARTIFACT_BYTES
            or spec.get("modid") != _MODID):
        raise ValueError("fixture artifact bytes or owner spec are invalid")
    entries: dict[str, bytes] = {}
    names: set[str] = set()
    files: set[str] = set()
    expanded = 0
    try:
        with ZipFile(BytesIO(raw)) as jar:
            infos = jar.infolist()
            if not 0 < len(infos) <= _MAX_MEMBERS:
                raise ValueError("fixture JAR has an invalid member count")
            for info in infos:
                name = info.filename
                path = PurePosixPath(name)
                normalized = name.rstrip("/")
                if (not name or "\\" in name or "\x00" in name
                        or path.is_absolute() or name.rstrip("/") != path.as_posix()
                        or any(part in {"", ".", ".."} for part in normalized.split("/"))
                        or normalized.casefold() in names
                        or info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}):
                    raise ValueError("fixture JAR has an unsafe or duplicate member")
                names.add(normalized.casefold())
                mode = info.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if kind not in {0, stat.S_IFDIR if info.is_dir() else stat.S_IFREG}:
                    raise ValueError("fixture JAR contains a special or mismatched member")
                if info.is_dir():
                    if info.file_size != 0:
                        raise ValueError("fixture JAR directory has a payload")
                    continue
                files.add(normalized.casefold())
                if info.file_size > _MAX_MEMBER_BYTES:
                    raise ValueError("fixture JAR member exceeds its byte bound")
                expanded += info.file_size
                if expanded > _MAX_EXPANDED_BYTES:
                    raise ValueError("fixture JAR exceeds its expansion bound")
                with jar.open(info) as member:
                    data = member.read(_MAX_MEMBER_BYTES + 1)
                if len(data) != info.file_size:
                    raise ValueError("fixture JAR member has incomplete bytes")
                if name in _REQUIRED:
                    entries[name] = data
    except (BadZipFile, OSError, RuntimeError, NotImplementedError) as exc:
        raise ValueError(f"fixture JAR cannot be read exactly: {exc}") from exc
    if any(
        "/".join(name.split("/")[:index]) in files
        for name in files for index in range(1, len(name.split("/")))
    ):
        raise ValueError("fixture JAR has a file ancestor")
    if set(entries) != _REQUIRED:
        raise ValueError("fixture JAR lacks required owner entries")
    if any(name.startswith("org/spongepowered/asm/mixin/") for name in names):
        raise ValueError("fixture JAR embeds another Mixin implementation")
    if _manifest_main(entries["META-INF/MANIFEST.MF"]).get("mixinconfigs") != "mixins.workbench_daily_loop.json":
        raise ValueError("fixture JAR has the wrong MixinConfigs manifest")
    try:
        metadata = json.loads(entries["mcmod.info"].decode("utf-8"))
        refmap = json.loads(entries["mixins.workbench_daily_loop.refmap.json"].decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("fixture JAR metadata or refmap is invalid JSON") from exc
    if (type(metadata) is not list or len(metadata) != 1
            or type(metadata[0]) is not dict
            or metadata[0].get("modid") != _MODID
            or metadata[0].get("version") != spec.get("version")):
        raise ValueError("fixture JAR has another mod identity")
    if (type(refmap) is not dict or type(refmap.get("mappings")) is not dict
            or not refmap["mappings"]):
        raise ValueError("fixture JAR has no generated refmap mappings")
    if any(
        not raw_class.startswith(b"\xca\xfe\xba\xbe")
        for name, raw_class in entries.items() if name.endswith(".class")
    ):
        raise ValueError("fixture JAR has corrupt mod bytecode")
    return {
        "spec": dict(spec), "sha256": "sha256:" + sha256(raw).hexdigest(),
        "size": len(raw), "member_count": len(names),
        "state": "owner-content-validated",
    }


__all__ = ["artifact_spec", "inspect_artifact_bytes", "MAX_ARTIFACT_BYTES"]
