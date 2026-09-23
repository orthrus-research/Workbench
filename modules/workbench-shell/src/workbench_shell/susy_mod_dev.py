"""Build a Supersymmetry constituent mod and prepare an exact pack overlay.

This is deliberately a developer-loop product, not a support classifier.  A
source project is eligible when it can be matched unambiguously to the selected
Supersymmetry Packwiz manifest.  The project may use legacy ForgeGradle,
RetroFuturaGradle, or another Gradle layout; Cleanroom is the later execution
target, not a requirement imposed on the source build.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tomllib
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse
from urllib.request import url2pathname
from zipfile import BadZipFile, ZipFile

from .runtime_materialize import (
    packwiz_materialization_version,
    verify_packwiz_materialization_receipt_identity,
)
from workbench_api.state_paths import default_suite_state_root


PLAN_FORMAT = "workbench-susy-mod-dev-plan-v1"
RESULT_FORMAT = "workbench-susy-mod-dev-result-v1"
MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_SOURCE_FILES = 200_000
MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024
MAX_RUNTIME_FILES = 100_000
MAX_RUNTIME_BYTES = 4 * 1024 * 1024 * 1024
EXCLUDED_SOURCE_NAMES = frozenset(
    {".git", ".gradle", ".idea", ".workbench", "build", "out", "run", "runs"}
)
NON_DISTRIBUTABLE_TOKENS = (
    "-sources",
    "-source",
    "-javadoc",
    "-dev",
    "-deobf",
    "-api",
    "-slim",
    "-all-dev",
    "-downgraded",
    "-dev-undowngraded",
)
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RFG_PLUGIN_ID = "com.gtnewhorizons.retrofuturagradle"
RFG_RETIRED_VERSION = "1.4.0"
RFG_COMPATIBILITY_VERSION = "1.4.9"
LAUNCHWRAPPER_GROUP = "net.minecraft"
LAUNCHWRAPPER_MODULE = "launchwrapper"
LAUNCHWRAPPER_UNAVAILABLE_VERSION = "1.17.2"
LAUNCHWRAPPER_COMPATIBILITY_VERSION = "1.12"
PACKWIZ_MATERIALIZATION_RESULT_FORMAT = (
    "workbench-packwiz-materialization-result-v2"
)
PACKWIZ_MATERIALIZATION_RECEIPT_NAME = "packwiz-materialization-v2.json"


class SusyModDevError(RuntimeError):
    """The requested Supersymmetry mod-development operation is unsafe."""


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _digest(value: Mapping[str, Any]) -> str:
    return "sha256:" + sha256(_canonical_bytes(value)).hexdigest()


def _read_regular(path: Path, label: str, *, limit: int = MAX_TEXT_BYTES) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise SusyModDevError(f"{label} cannot be read: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SusyModDevError(f"{label} must be a regular file: {path}")
    if info.st_size > limit:
        raise SusyModDevError(f"{label} exceeds {limit} bytes: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise SusyModDevError(f"{label} cannot be read: {path}") from exc


def _load_toml(path: Path, label: str) -> tuple[bytes, dict[str, Any]]:
    raw = _read_regular(path, label)
    try:
        value = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise SusyModDevError(f"{label} is invalid TOML: {path}") from exc
    if not isinstance(value, dict):
        raise SusyModDevError(f"{label} must be an object: {path}")
    return raw, value


def _properties(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        return {}
    raw = _read_regular(path, path.name)
    result: dict[str, str] = {}
    for line in raw.decode("utf-8", "replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!")):
            continue
        match = re.match(r"^([^:=\s]+)\s*[:=]\s*(.*?)\s*$", stripped)
        if match:
            result[match.group(1)] = match.group(2)
    return result


def _normal(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _safe_relative(root: Path, raw: str, label: str) -> Path:
    value = Path(raw)
    if value.is_absolute() or ".." in value.parts:
        raise SusyModDevError(f"{label} must stay below {root}: {raw}")
    target = (root / value).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise SusyModDevError(f"{label} escapes {root}: {raw}") from exc
    return target


def _find_project_root(requested: Path | str) -> Path:
    path = Path(requested).expanduser().resolve()
    if path.is_file():
        path = path.parent
    if not path.is_dir():
        raise SusyModDevError(f"mod checkout does not exist: {path}")
    markers = ("settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts")
    for candidate in (path, *path.parents):
        if any((candidate / marker).is_file() for marker in markers) and any(
            (candidate / wrapper).is_file() for wrapper in ("gradlew", "gradlew.bat")
        ):
            return candidate
    raise SusyModDevError(
        f"no Gradle wrapper/build root was found at or above {path}"
    )


def _literal_mod_ids(root: Path) -> list[str]:
    result: set[str] = set()
    source = root / "src"
    if not source.is_dir() or source.is_symlink():
        return []
    paths = sorted(source.rglob("mcmod.info"))[:32]
    for path in paths:
        if path.is_symlink() or not path.is_file():
            continue
        try:
            value = json.loads(_read_regular(path, "mcmod.info").decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError, SusyModDevError):
            continue
        rows = value if isinstance(value, list) else [value]
        for row in rows:
            if not isinstance(row, dict):
                continue
            mod_id = row.get("modid")
            if (
                isinstance(mod_id, str)
                and re.fullmatch(r"[a-z0-9_.-]+", mod_id)
                and not any(token in mod_id for token in ("$", "{", "}", "@"))
            ):
                result.add(mod_id)
    return sorted(result)


def _wrapper_version(root: Path) -> tuple[str | None, str | None]:
    properties = _properties(root / "gradle/wrapper/gradle-wrapper.properties")
    url = properties.get("distributionUrl")
    if not url:
        return None, None
    match = re.search(r"gradle-([0-9]+(?:\.[0-9]+)*)-", url)
    return url, match.group(1) if match else None


def _project_identity(root: Path) -> dict[str, Any]:
    merged: dict[str, str] = {}
    property_files: list[str] = []
    version_property: dict[str, str] | None = None
    for name in ("build.properties", "gradle.properties", "buildscript.properties"):
        values = _properties(root / name)
        if values:
            property_files.append(name)
            merged.update(values)
            for key in ("modVersion", "mod_version"):
                if key in values:
                    version_property = {
                        "path": name,
                        "key": key,
                        "value": values[key],
                    }

    build_paths = [
        path
        for name in (
            "build.gradle",
            "build.gradle.kts",
            "settings.gradle",
            "settings.gradle.kts",
        )
        if (path := root / name).is_file() and not path.is_symlink()
    ]
    build_sources = [
        (path, _read_regular(path, path.name).decode("utf-8", "replace"))
        for path in build_paths
    ]
    build_text = "\n".join(text for _path, text in build_sources)
    curseforge = next(
        (
            value
            for key in ("curseForgeProjectId", "curseforgeProjectId", "curseforge_project_id")
            if (value := merged.get(key)) and value.isdigit()
        ),
        None,
    )
    if curseforge is None:
        match = re.search(
            r"(?i)curseforge(?:Project)?Id\s*[=:]\s*['\"]?([0-9]+)",
            build_text,
        )
        curseforge = match.group(1) if match else None

    def first(*names: str) -> str | None:
        for name in names:
            value = merged.get(name)
            if value:
                return value
        return None

    wrapper = root / ("gradlew.bat" if os.name == "nt" else "gradlew")
    wrapper_url, gradle_version = _wrapper_version(root)
    mod_ids = _literal_mod_ids(root)
    declared_id = first("modId", "mod_id", "modid")
    if declared_id and re.fullmatch(r"[a-z0-9_.-]+", declared_id):
        mod_ids = sorted(set((*mod_ids, declared_id)))
    lowered_build = build_text.lower()
    configuration_keys: set[str] = set()
    for property_match in re.finditer(
        r"(?:project\s*\.\s*)?getProperty\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
        build_text,
    ):
        key = property_match.group(1)
        if (
            not re.search(r"(?i)(?:password|username|token|secret|api[_-]?key)$", key)
            or key in merged
        ):
            continue
        nearby = build_text[max(0, property_match.start() - 500) : property_match.start()]
        last_guard = nearby.rfind("hasProperty")
        last_block_end = nearby.rfind("}")
        if last_guard > last_block_end:
            continue
        configuration_keys.add(key)
    configuration_defaults = [
        {
            "property": key,
            "value": "workbench-disabled",
            "reason": "unguarded credential-shaped Gradle property is disabled for local assembly",
        }
        for key in sorted(configuration_keys)
    ]
    if RFG_PLUGIN_ID in lowered_build:
        declarations: list[dict[str, str]] = []
        declaration_pattern = re.compile(
            rf"\bid\s*(['\"]){re.escape(RFG_PLUGIN_ID)}\1\s+version\s*(['\"])([^'\"]+)\2"
        )
        for path, text in build_sources:
            declarations.extend(
                {
                    "path": path.name,
                    "declared_version": match.group(3),
                }
                for match in declaration_pattern.finditer(text)
            )
        overrides = re.findall(
            rf"case\s+['\"]{re.escape(RFG_PLUGIN_ID)}['\"]\s*:\s*useVersion\s+['\"]([^'\"]+)['\"]",
            build_text,
        )
        plugin = deepcopy(declarations[0]) if len(declarations) == 1 else None
        if plugin is not None:
            plugin.update(
                {
                    "id": RFG_PLUGIN_ID,
                    "effective_version": (
                        overrides[0] if len(overrides) == 1 else plugin["declared_version"]
                    ),
                    "version_override_observed": len(overrides) == 1,
                }
            )
        dependency_repairs: list[dict[str, str]] = []
        unavailable_coordinate = (
            f"{LAUNCHWRAPPER_GROUP}:{LAUNCHWRAPPER_MODULE}:"
            f"{LAUNCHWRAPPER_UNAVAILABLE_VERSION}"
        )
        for path, text in build_sources:
            if text.count(unavailable_coordinate) == 1:
                dependency_repairs.append(
                    {
                        "path": path.name,
                        "group": LAUNCHWRAPPER_GROUP,
                        "module": LAUNCHWRAPPER_MODULE,
                        "from_version": LAUNCHWRAPPER_UNAVAILABLE_VERSION,
                        "value": LAUNCHWRAPPER_COMPATIBILITY_VERSION,
                    }
                )
        adapter = {
            "id": "retrofuturagradle-assemble-v1",
            "family": "retrofuturagradle",
            "candidate_task": "assemble",
            "artifact_semantics": "RFG assembly including configured downgrade, shading, and reobfuscation finalizers",
            "confidence": "observed-plugin",
            "configuration_defaults": configuration_defaults,
            "environment_inputs": (
                ["BUILD_NUMBER"] if "BUILD_NUMBER" in build_text else []
            ),
            "plugin": plugin,
            "dependency_compatibility": dependency_repairs,
        }
    elif "net.minecraftforge.gradle" in lowered_build or "forgegradle" in lowered_build:
        adapter = {
            "id": "forgegradle-assemble-v1",
            "family": "forgegradle",
            "candidate_task": "assemble",
            "artifact_semantics": "ForgeGradle assembly with project-configured reobfuscation finalizers",
            "confidence": "observed-plugin",
            "configuration_defaults": configuration_defaults,
            "environment_inputs": (
                ["BUILD_NUMBER"] if "BUILD_NUMBER" in build_text else []
            ),
        }
    else:
        adapter = {
            "id": "generic-gradle-assemble-v1",
            "family": "gradle",
            "candidate_task": "assemble",
            "artifact_semantics": "generic Gradle assembly followed by strict artifact inspection",
            "confidence": "bounded-fallback",
            "configuration_defaults": configuration_defaults,
            "environment_inputs": (
                ["BUILD_NUMBER"] if "BUILD_NUMBER" in build_text else []
            ),
        }
    return {
        "root": str(root),
        "name": first("modName", "mod_name") or root.name,
        "mod_ids": mod_ids,
        "archive_base": first("modArchivesBaseName", "archivesBaseName", "archive_base"),
        "minecraft_version": first("minecraftVersion", "minecraft_version"),
        "declared_version": first("modVersion", "mod_version", "version"),
        "version_property": version_property,
        "curseforge_project_id": int(curseforge) if curseforge else None,
        "property_files": property_files,
        "build_files": [path.name for path in build_paths],
        "wrapper": {
            "path": str(wrapper),
            "present": wrapper.is_file() and not wrapper.is_symlink(),
            "executable": wrapper.is_file()
            and not wrapper.is_symlink()
            and (os.name == "nt" or os.access(wrapper, os.X_OK)),
            "distribution_url": wrapper_url,
            "gradle_version": gradle_version,
        },
        "build_adapter": adapter,
        "build_features": {
            "modern_java_syntax": (first("enableModernJavaSyntax") or "").lower()
            == "true",
            "mixins": (first("usesMixins") or "").lower() == "true",
            "mixin_refmap": first("mixinConfigRefmap"),
            "access_transformer": first("accessTransformersFile"),
            "coremod_class": first("coreModClass"),
            "shadowed_dependencies": (
                first("usesShadowedDependencies") or ""
            ).lower()
            == "true",
        },
    }


def _pack_entry(path: Path, root: Path) -> dict[str, Any]:
    raw, value = _load_toml(path, "Packwiz mod metadata")
    name = value.get("name")
    filename = value.get("filename")
    side = value.get("side", "both")
    if not isinstance(name, str) or not name:
        raise SusyModDevError(f"Packwiz mod metadata lacks name: {path}")
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise SusyModDevError(f"Packwiz mod metadata has unsafe filename: {path}")
    if side not in {"both", "client", "server"}:
        raise SusyModDevError(f"Packwiz mod metadata has unsupported side: {path}")
    download = value.get("download")
    if not isinstance(download, dict):
        download = {}
    update = value.get("update")
    if not isinstance(update, dict):
        update = {}
    curseforge = update.get("curseforge")
    if not isinstance(curseforge, dict):
        curseforge = {}
    modrinth = update.get("modrinth")
    if not isinstance(modrinth, dict):
        modrinth = {}
    return {
        "metadata_path": path.relative_to(root).as_posix(),
        "metadata_sha256": sha256(raw).hexdigest(),
        "name": name,
        "filename": filename,
        "side": side,
        "baseline": {
            "hash_format": download.get("hash-format"),
            "hash": download.get("hash"),
        },
        "curseforge_project_id": curseforge.get("project-id"),
        "curseforge_file_id": curseforge.get("file-id"),
        "modrinth_project_id": modrinth.get("mod-id"),
        "modrinth_version_id": modrinth.get("version"),
    }


def _load_pack(root_value: Path | str) -> dict[str, Any]:
    root = Path(root_value).expanduser().resolve()
    if not root.is_dir():
        raise SusyModDevError(f"Supersymmetry checkout does not exist: {root}")
    manifest_raw, manifest = _load_toml(root / "pack.toml", "Packwiz manifest")
    if manifest.get("name") != "Supersymmetry":
        raise SusyModDevError("selected Packwiz checkout is not named Supersymmetry")
    pack_format = manifest.get("pack-format")
    if not isinstance(pack_format, str) or not pack_format.startswith("packwiz:"):
        raise SusyModDevError("Supersymmetry manifest has unsupported pack-format")
    versions = manifest.get("versions")
    if not isinstance(versions, dict) or versions.get("minecraft") != "1.12.2":
        raise SusyModDevError("Supersymmetry manifest must target Minecraft 1.12.2")
    index = manifest.get("index")
    if not isinstance(index, dict):
        raise SusyModDevError("Supersymmetry manifest lacks an index declaration")
    index_file = index.get("file")
    declared_hash = index.get("hash")
    hash_format = index.get("hash-format")
    if not isinstance(index_file, str) or hash_format != "sha256" or not isinstance(
        declared_hash, str
    ) or not SHA256_RE.fullmatch(declared_hash):
        raise SusyModDevError("Supersymmetry index declaration is incomplete")
    index_path = _safe_relative(root, index_file, "Packwiz index")
    index_raw = _read_regular(index_path, "Packwiz index", limit=64 * 1024 * 1024)
    actual_hash = sha256(index_raw).hexdigest()

    mods_root = root / "mods"
    if not mods_root.is_dir() or mods_root.is_symlink():
        raise SusyModDevError("Supersymmetry checkout lacks a regular mods directory")
    paths = sorted(mods_root.glob("*.pw.toml"))
    if not paths:
        raise SusyModDevError("Supersymmetry checkout has no Packwiz mod metadata")
    entries = [_pack_entry(path, root) for path in paths]
    return {
        "root": str(root),
        "name": "Supersymmetry",
        "version": manifest.get("version"),
        "author": manifest.get("author"),
        "pack_format": pack_format,
        "minecraft_version": versions.get("minecraft"),
        "source_loader": {
            key: versions[key]
            for key in sorted(versions)
            if key != "minecraft" and isinstance(versions[key], str)
        },
        "manifest_sha256": sha256(manifest_raw).hexdigest(),
        "index": {
            "path": index_file,
            "declared_sha256": declared_hash,
            "actual_sha256": actual_hash,
            "matches_declared_hash": actual_hash == declared_hash,
        },
        "entry_count": len(entries),
        "entries": entries,
    }


def _match_entry(
    project: Mapping[str, Any],
    pack: Mapping[str, Any],
    explicit: str | None,
) -> dict[str, Any]:
    entries = pack.get("entries")
    if not isinstance(entries, list):
        raise SusyModDevError("pack entry inventory is unavailable")
    if explicit:
        normalized = explicit.replace("\\", "/")
        candidates = [
            row
            for row in entries
            if isinstance(row, dict)
            and (
                row.get("metadata_path") == normalized
                or Path(str(row.get("metadata_path"))).name == normalized
            )
        ]
        if len(candidates) != 1:
            return {
                "state": "blocked",
                "reason": "explicit-pack-entry-not-unique",
                "selected": None,
                "candidates": [],
            }
        return {
            "state": "exact",
            "reason": "explicit-pack-entry",
            "selected": deepcopy(candidates[0]),
            "candidates": [],
        }

    project_cf = project.get("curseforge_project_id")
    archive = _normal(project.get("archive_base"))
    name = _normal(project.get("name"))
    mod_ids = {_normal(item) for item in project.get("mod_ids", []) if isinstance(item, str)}
    root_name = _normal(Path(str(project.get("root"))).name)
    ranked: list[tuple[int, list[str], dict[str, Any]]] = []
    for row in entries:
        if not isinstance(row, dict):
            continue
        score = 0
        reasons: list[str] = []
        if project_cf is not None and row.get("curseforge_project_id") == project_cf:
            score += 100
            reasons.append("curseforge-project-id")
        filename = _normal(str(row.get("filename", "")))
        entry_name = _normal(str(row.get("name", "")))
        metadata_stem = _normal(Path(str(row.get("metadata_path", ""))).name.removesuffix(".pw.toml"))
        if archive and filename.startswith(archive):
            score += 70
            reasons.append("archive-filename")
        if name and name == entry_name:
            score += 55
            reasons.append("project-name")
        if root_name and root_name in {entry_name, metadata_stem}:
            score += 35
            reasons.append("checkout-name")
        if mod_ids & {entry_name, metadata_stem}:
            score += 45
            reasons.append("mod-id")
        if score:
            ranked.append((score, reasons, deepcopy(row)))
    ranked.sort(
        key=lambda item: (-item[0], str(item[2].get("metadata_path", "")))
    )
    if not ranked:
        return {
            "state": "blocked",
            "reason": "no-pack-entry-match",
            "selected": None,
            "candidates": [],
        }
    top = ranked[0][0]
    top_rows = [item for item in ranked if item[0] == top]
    candidates = [
        {
            "score": score,
            "reasons": reasons,
            "entry": row,
        }
        for score, reasons, row in ranked[:8]
    ]
    if len(top_rows) != 1 or top < 45:
        return {
            "state": "ambiguous",
            "reason": "pack-entry-match-ambiguous",
            "selected": None,
            "candidates": candidates,
        }
    score, reasons, selected = top_rows[0]
    return {
        "state": "exact",
        "reason": "+".join(reasons),
        "score": score,
        "selected": selected,
        "candidates": candidates[1:],
    }


def _java_release(home: Path) -> tuple[int, str] | None:
    release = home / "release"
    if not release.is_file() or release.is_symlink():
        return None
    values = _properties(release)
    version = values.get("JAVA_VERSION", "").strip('"')
    match = re.match(r"(?:1\.)?([0-9]+)", version)
    if not match or not (home / "bin/java").is_file():
        return None
    return int(match.group(1)), version


def _java_homes(suite_root: Path, explicit: Path | str | None) -> list[dict[str, Any]]:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit).expanduser().resolve())
    env_home = os.environ.get("JAVA_HOME")
    if env_home:
        candidates.append(Path(env_home).expanduser().resolve())
    candidates.extend((Path.home() / ".gradle/jdks").glob("*"))
    candidates.extend((default_suite_state_root(suite_root) / "toolchains").glob("**/*"))
    seen: set[Path] = set()
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        release = _java_release(resolved)
        if release is None:
            continue
        major, version = release
        rows.append({"home": str(resolved), "major": major, "version": version})
    rows.sort(key=lambda row: (row["major"], row["home"]))
    return rows


def _required_build_java(project: Mapping[str, Any]) -> int | None:
    wrapper = project.get("wrapper")
    version = wrapper.get("gradle_version") if isinstance(wrapper, dict) else None
    if not isinstance(version, str):
        return None
    major = int(version.split(".", 1)[0])
    if major <= 4:
        return 8
    if major <= 6:
        return 8
    if major == 7:
        return 11
    return 17


def _problem(
    code: str,
    summary: str,
    detail: str,
    *,
    repair: list[str] | None = None,
    evidence_uri: str | None = None,
) -> dict[str, Any]:
    value = {
        "code": code,
        "summary": summary,
        "detail": detail,
        "next_safe_argv": repair,
    }
    if evidence_uri is not None:
        value["evidence_uri"] = evidence_uri
    return value


def _gradle_failure_detail(stderr_path: Path, exit_code: int | None) -> str:
    try:
        with stderr_path.open("rb") as stream:
            raw = stream.read(1024 * 1024)
    except OSError:
        return f"Gradle exit={exit_code}; stderr could not be reopened."
    text = raw.decode("utf-8", "replace")
    marker = "* What went wrong:"
    if marker in text:
        block = text.split(marker, 1)[1].split("* Try:", 1)[0]
        lines = [line.strip().lstrip("> ").strip() for line in block.splitlines()]
        useful = [line for line in lines if line]
    else:
        useful = [line.strip() for line in text.splitlines() if line.strip()]
    detail = " ".join(useful[:8])
    if not detail:
        detail = "No diagnostic text was written to stderr."
    if len(detail) > 1_200:
        detail = detail[:1_197].rstrip() + "..."
    return f"Gradle exit={exit_code}: {detail}"


def plan_susy_mod_dev(
    suite_root: Path | str,
    project_root: Path | str,
    pack_root: Path | str,
    *,
    pack_mod: str | None = None,
    java_home: Path | str | None = None,
    stage_client: bool = False,
) -> dict[str, Any]:
    suite = Path(suite_root).resolve()
    state = default_suite_state_root(suite)
    project_path = _find_project_root(project_root)
    project = _project_identity(project_path)
    source_manifest = _source_manifest(project_path, None)
    project["source_fingerprint"] = {
        "digest": source_manifest["source_digest"],
        "file_count": source_manifest["file_count"],
        "total_bytes": source_manifest["total_bytes"],
        "excluded_names": sorted(EXCLUDED_SOURCE_NAMES),
    }
    pack = _load_pack(pack_root)
    match = _match_entry(project, pack, pack_mod)
    java_inventory = _java_homes(suite, java_home)
    required_java = _required_build_java(project)
    selected_java = next(
        (row for row in java_inventory if row["major"] == required_java), None
    )
    problems: list[dict[str, Any]] = []
    wrapper = project["wrapper"]
    if not wrapper["present"]:
        problems.append(
            _problem(
                "GRADLE_WRAPPER_MISSING",
                "The mod checkout has no Gradle wrapper",
                "Workbench will not substitute a host Gradle executable for a project-owned wrapper.",
            )
        )
    elif not wrapper["executable"]:
        problems.append(
            _problem(
                "GRADLE_WRAPPER_NOT_EXECUTABLE",
                "The project Gradle wrapper is not executable",
                str(wrapper["path"]),
            )
        )
    if required_java is None:
        problems.append(
            _problem(
                "BUILD_JAVA_UNRESOLVED",
                "Workbench cannot derive the wrapper's build Java",
                "The wrapper distribution version is absent or unsupported; select --java-home explicitly.",
            )
        )
    elif selected_java is None:
        problems.append(
            _problem(
                "BUILD_JAVA_MISSING",
                f"Java {required_java} is required to run this Gradle wrapper",
                "No matching JDK was found in JAVA_HOME, ~/.gradle/jdks, or managed Workbench toolchains.",
            )
        )
    if match["state"] != "exact":
        problems.append(
            _problem(
                "SUSY_MOD_MATCH_" + ("AMBIGUOUS" if match["state"] == "ambiguous" else "MISSING"),
                "The source checkout does not map to exactly one Supersymmetry mod entry",
                "Pass --pack-mod with the exact mods/*.pw.toml basename when metadata cannot establish one identity.",
            )
        )
    if project.get("minecraft_version") not in {None, "1.12.2"}:
        problems.append(
            _problem(
                "MINECRAFT_VERSION_MISMATCH",
                "The source checkout does not target Minecraft 1.12.2",
                str(project.get("minecraft_version")),
            )
        )

    version_property = project.get("version_property")
    version_overlay: dict[str, str] | None = None
    if not project.get("declared_version"):
        if (
            isinstance(version_property, dict)
            and isinstance(version_property.get("path"), str)
            and isinstance(version_property.get("key"), str)
            and version_property.get("value") == ""
        ):
            version_overlay = {
                "path": version_property["path"],
                "key": version_property["key"],
            }
        else:
            problems.append(
                _problem(
                    "CANDIDATE_VERSION_UNRESOLVED",
                    "The staged build cannot bind a deterministic candidate version",
                    "No fixed version or empty modVersion/mod_version property can be overlaid in managed custody.",
                )
            )

    candidate_version = (
        str(project.get("declared_version"))
        if project.get("declared_version")
        else "workbench-dev-" + source_manifest["source_digest"].rsplit(":", 1)[-1][:12]
    )
    managed_overlays: list[dict[str, Any]] = []
    if version_overlay is not None:
        managed_overlays.append(
            {
                "kind": "empty-property",
                **version_overlay,
                "value": candidate_version,
                "purpose": "bind the candidate artifact to the exact source fingerprint inside managed build custody",
            }
        )

    warnings: list[dict[str, str]] = []
    if not pack["index"]["matches_declared_hash"]:
        warnings.append(
            {
                "code": "PACK_INDEX_STALE",
                "summary": "The source checkout's Packwiz index is stale or intentionally unmaterialized.",
                "detail": "Build matching uses the exact manifest and mod metadata; runtime staging must refresh the index only in a disposable copy.",
            }
        )
    selected = match.get("selected")
    sides: list[str] = []
    if isinstance(selected, dict):
        side = selected.get("side")
        sides = ["client", "server"] if side == "both" else [str(side)]
    plugin = project["build_adapter"].get("plugin")
    if (
        isinstance(plugin, dict)
        and plugin.get("declared_version") == RFG_RETIRED_VERSION
        and plugin.get("effective_version") == RFG_RETIRED_VERSION
        and plugin.get("version_override_observed") is False
    ):
        managed_overlays.append(
            {
                "kind": "gradle-plugin-version",
                "path": plugin["path"],
                "plugin_id": RFG_PLUGIN_ID,
                "from_version": RFG_RETIRED_VERSION,
                "value": RFG_COMPATIBILITY_VERSION,
                "purpose": (
                    "replace the unavailable RFG 1.4.0 plugin marker with the "
                    "1.4.9 compatibility version only inside managed build custody"
                ),
            }
        )
        warnings.append(
            {
                "code": "RFG_PLUGIN_COMPATIBILITY_OVERLAY",
                "summary": "The source requests an unavailable RFG plugin marker.",
                "detail": (
                    "Workbench will test RFG 1.4.9 in the managed source copy; "
                    "the original checkout remains unchanged."
                ),
            }
        )
    for repair in project["build_adapter"].get("dependency_compatibility", []):
        if not isinstance(repair, dict):
            continue
        managed_overlays.append(
            {
                "kind": "gradle-dependency-version",
                **repair,
                "purpose": (
                    "replace an unavailable LaunchWrapper coordinate with the "
                    "Minecraft 1.12 coordinate used by passing sibling RFG builds, "
                    "only inside managed build custody"
                ),
            }
        )
        warnings.append(
            {
                "code": "LAUNCHWRAPPER_COMPATIBILITY_OVERLAY",
                "summary": "The source requests an unavailable LaunchWrapper coordinate.",
                "detail": (
                    "Workbench will test net.minecraft:launchwrapper:1.12 in the "
                    "managed source copy; the original checkout remains unchanged."
                ),
            }
        )
    environment_overrides = []
    if "BUILD_NUMBER" in project["build_adapter"]["environment_inputs"]:
        environment_overrides.append(
            {
                "name": "BUILD_NUMBER",
                "value": "workbench-"
                + source_manifest["source_digest"].rsplit(":", 1)[-1][:12],
                "purpose": "replace host CI identity with a deterministic source-bound local build number",
            }
        )
    command = None
    if selected_java is not None and wrapper["present"]:
        command = [str(wrapper["path"]), "--no-daemon", "--console=plain"]
        command.extend(
            f"-P{row['property']}={row['value']}"
            for row in project["build_adapter"]["configuration_defaults"]
        )
        command.append(project["build_adapter"]["candidate_task"])
    identity = {
        "project": project,
        "pack": {key: value for key, value in pack.items() if key != "entries"},
        "match": match,
        "build_java": selected_java,
        "required_build_java": required_java,
        "command": command,
        "request": {
            "stage_client": stage_client,
            "pack_mod": pack_mod,
        },
    }
    plan_id = "workbench-susy-mod-dev-plan:" + _digest(identity)
    result = {
        "format": PLAN_FORMAT,
        "schema_version": 1,
        "plan_id": plan_id,
        "state": "ready" if not problems else "blocked",
        "operation_class": "read-only",
        "requested_operation": (
            "build-and-stage-client" if stage_client else "build"
        ),
        "project": project,
        "supersymmetry": {
            key: value for key, value in pack.items() if key != "entries"
        },
        "replacement": {
            "match": match,
            "applicable_sides": sides,
        },
        "build": {
            "required_java_major": required_java,
            "java": selected_java,
            "available_java": java_inventory,
            "argv": command,
            "task": project["build_adapter"]["candidate_task"],
            "adapter": deepcopy(project["build_adapter"]),
            "candidate_version": candidate_version,
            "managed_overlays": managed_overlays,
            "environment_overrides": environment_overrides,
            "output_policy": (
                "fresh ZIP-valid distributable artifact set whose mod IDs bind "
                "to the source identity; classifiers such as sources/dev/deobf/api are excluded"
            ),
        },
        "intended_writes": [
            {
                "root": str((state / "dev-runs").resolve()),
                "purpose": "managed source snapshot, build logs, artifact set, and SUSY replacement overlay",
            }
        ]
        + (
            [
                {
                    "root": str((state / "fixtures").resolve()),
                    "purpose": "reuse or materialize an exact disposable Supersymmetry client",
                }
            ]
            if stage_client
            else []
        ),
        "problems": problems,
        "warnings": warnings,
        "limitations": [
            "This slice builds and verifies the candidate artifact set and prepares an overlay; it does not launch Minecraft yet.",
            "The candidate task is Gradle assemble; project-specific check/test/lint lifecycles are not executed in this slice.",
            "A successful constituent build does not claim that every Supersymmetry mod is source-buildable or Cleanroom-compatible.",
            "The source pack's Forge declaration is observed input; the later disposable execution target is Cleanroom.",
        ],
    }
    validate_susy_mod_plan(result)
    return result


def validate_susy_mod_plan(value: Mapping[str, Any]) -> None:
    if value.get("format") != PLAN_FORMAT or value.get("schema_version") != 1:
        raise SusyModDevError("unsupported SUSY mod dev plan")
    if value.get("state") not in {"ready", "blocked"}:
        raise SusyModDevError("SUSY mod dev plan has invalid state")
    problems = value.get("problems")
    if not isinstance(problems, list):
        raise SusyModDevError("SUSY mod dev plan lacks problems")
    if (value.get("state") == "ready") != (not problems):
        raise SusyModDevError("SUSY mod dev plan state contradicts its problems")
    replacement = value.get("replacement")
    build = value.get("build")
    if not isinstance(replacement, dict) or not isinstance(build, dict):
        raise SusyModDevError("SUSY mod dev plan is incomplete")
    match = replacement.get("match")
    if value.get("state") == "ready":
        if not isinstance(match, dict) or match.get("state") != "exact":
            raise SusyModDevError("ready SUSY mod plan lacks an exact replacement")
        if not isinstance(build.get("argv"), list) or not build["argv"]:
            raise SusyModDevError("ready SUSY mod plan lacks build argv")
        if not isinstance(build.get("java"), dict):
            raise SusyModDevError("ready SUSY mod plan lacks build Java")


def _source_manifest(source: Path, destination: Path | None) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    count = 0
    total = 0
    for current, directories, names in os.walk(source, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(source)
        kept_directories: list[str] = []
        for name in sorted(directories):
            if name in EXCLUDED_SOURCE_NAMES:
                continue
            path = current_path / name
            if path.is_symlink():
                link = os.readlink(path)
                resolved = (path.parent / link).resolve()
                try:
                    resolved.relative_to(source)
                except ValueError as exc:
                    raise SusyModDevError(
                        f"source symlink escapes the checkout: "
                        f"{path.relative_to(source)} -> {link}"
                    ) from exc
                if destination is not None:
                    target = destination / path.relative_to(source)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.symlink_to(link, target_is_directory=True)
                files.append(
                    {
                        "path": path.relative_to(source).as_posix(),
                        "kind": "symlink",
                        "target": link,
                    }
                )
                count += 1
                continue
            kept_directories.append(name)
        directories[:] = kept_directories
        if destination is not None:
            target_dir = destination / relative_dir
            target_dir.mkdir(parents=True, exist_ok=True)
        for name in sorted(names):
            if name in EXCLUDED_SOURCE_NAMES:
                continue
            path = current_path / name
            relative = path.relative_to(source)
            target = destination / relative if destination is not None else None
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                link = os.readlink(path)
                resolved = (path.parent / link).resolve()
                try:
                    resolved.relative_to(source)
                except ValueError as exc:
                    raise SusyModDevError(
                        f"source symlink escapes the checkout: {relative} -> {link}"
                    ) from exc
                if target is not None:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.symlink_to(link)
                files.append({"path": relative.as_posix(), "kind": "symlink", "target": link})
                count += 1
                continue
            if not stat.S_ISREG(info.st_mode):
                raise SusyModDevError(f"unsupported source entry: {relative}")
            count += 1
            total += info.st_size
            if count > MAX_SOURCE_FILES or total > MAX_SOURCE_BYTES:
                raise SusyModDevError("source checkout exceeds the managed snapshot limit")
            raw = path.read_bytes()
            if target is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target, follow_symlinks=False)
            files.append(
                {
                    "path": relative.as_posix(),
                    "kind": "file",
                    "size": len(raw),
                    "sha256": sha256(raw).hexdigest(),
                    "executable": bool(info.st_mode & stat.S_IXUSR),
                }
            )
    projection = {"files": files, "file_count": count, "total_bytes": total}
    projection["source_digest"] = _digest(projection)
    return projection


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )
    os.replace(temporary, path)


def _file_uri_path(value: Any, label: str) -> Path:
    if not isinstance(value, str):
        raise SusyModDevError(f"{label} must be a local file URI")
    parsed = urlparse(value)
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise SusyModDevError(f"{label} must be a local file URI")
    text = url2pathname(parsed.path)
    if os.name == "nt" and len(text) >= 3 and text[0] == "/" and text[2] == ":":
        text = text[1:]
    return Path(text)


def _packwiz_materialization_version(
    materialization: Mapping[str, Any],
    receipt: Any,
) -> int | None:
    receipt_version = (
        packwiz_materialization_version(receipt)
        if isinstance(receipt, Mapping)
        else None
    )
    if receipt_version != 2:
        return None
    if not verify_packwiz_materialization_receipt_identity(receipt):
        return None
    if (
        materialization.get("format")
        != PACKWIZ_MATERIALIZATION_RESULT_FORMAT
        or materialization.get("schema_version") != 2
    ):
        return None
    return receipt_version


def _regular_seed_fixture_parent(
    state: Path,
    relative_parts: tuple[str, ...],
) -> Path | None:
    parent = state.joinpath(*relative_parts)
    if not parent.exists() and not parent.is_symlink():
        return None
    current = state
    for part in (None, *relative_parts):
        if part is not None:
            current /= part
        try:
            info = current.lstat()
        except OSError as exc:
            raise SusyModDevError(
                f"canonical Packwiz seed state cannot be read: {current}"
            ) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise SusyModDevError(
                "canonical Packwiz seed state must be a regular directory: "
                f"{current}"
            )
    return parent


def _canonical_packwiz_seed_roots(state_root: Path | str) -> list[Path]:
    """Find completed canonical client payloads eligible for exact-hash seeding.

    These roots are only an artifact cache. ``runtime_materialize`` still
    admits each file by the selected Packwiz metafile's content hash, so an
    older pack payload cannot silently define the new pack.
    """

    state = Path(state_root).expanduser()
    if not state.is_absolute():
        state = Path.cwd() / state
    fixture_parent = _regular_seed_fixture_parent(
        state,
        ("fixtures", "packwiz-v2"),
    )
    fixture_parents = [] if fixture_parent is None else [fixture_parent]
    if not fixture_parents:
        return []

    roots: list[Path] = []
    fixtures = sorted(
        (
            fixture
            for fixture_parent in fixture_parents
            for fixture in fixture_parent.iterdir()
        ),
        key=lambda item: item.as_posix(),
    )
    for fixture in fixtures:
        try:
            fixture_info = fixture.lstat()
        except OSError as exc:
            raise SusyModDevError(
                f"canonical Packwiz fixture cannot be inspected: {fixture}"
            ) from exc
        if stat.S_ISLNK(fixture_info.st_mode):
            raise SusyModDevError(
                f"canonical Packwiz fixture must not be a symlink: {fixture}"
            )
        if not stat.S_ISDIR(fixture_info.st_mode):
            continue

        receipt_parent = fixture / "receipts"
        receipt_path = receipt_parent / PACKWIZ_MATERIALIZATION_RECEIPT_NAME
        instance = fixture / "instance"
        payload = instance / ".minecraft"
        if not (receipt_path.exists() or receipt_path.is_symlink()) or not (
            payload.exists() or payload.is_symlink()
        ):
            continue
        for path, label in (
            (receipt_parent, "receipt directory"),
            (instance, "instance directory"),
            (payload, "payload directory"),
        ):
            try:
                path_info = path.lstat()
            except OSError as exc:
                raise SusyModDevError(
                    f"canonical Packwiz {label} cannot be inspected: {path}"
                ) from exc
            if stat.S_ISLNK(path_info.st_mode) or not stat.S_ISDIR(path_info.st_mode):
                raise SusyModDevError(
                    f"canonical Packwiz {label} must be a regular directory: {path}"
                )

        try:
            receipt = json.loads(
                _read_regular(
                    receipt_path,
                    "canonical Packwiz materialization receipt",
                ).decode("utf-8")
            )
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise SusyModDevError(
                f"canonical Packwiz materialization receipt is invalid JSON: {receipt_path}"
            ) from exc
        target = receipt.get("target") if isinstance(receipt, dict) else None
        recorded_payload = (
            receipt.get("payload") if isinstance(receipt, dict) else None
        )
        request = receipt.get("request") if isinstance(receipt, dict) else None
        receipt_version = (
            packwiz_materialization_version(receipt)
            if isinstance(receipt, Mapping)
            else None
        )
        v2_target_valid = (
            isinstance(target, dict)
            and target.get("variant") == "packwiz-v2"
            and _file_uri_path(
                target.get("variant_root_uri"),
                "canonical Packwiz V2 variant root URI",
            ).resolve()
            == fixture.resolve()
        )
        if (
            not isinstance(receipt, dict)
            or receipt_version != 2
            or not verify_packwiz_materialization_receipt_identity(receipt)
            or receipt.get("state") != "materialized"
            or receipt.get("readiness") != "pack-payload-installed"
            or not isinstance(request, dict)
            or request.get("side") != "client"
            or not isinstance(target, dict)
            or not isinstance(recorded_payload, dict)
            or _file_uri_path(
                target.get("instance_root_uri"),
                "canonical Packwiz instance URI",
            ).resolve()
            != instance.resolve()
            or _file_uri_path(
                target.get("receipt_uri"),
                "canonical Packwiz receipt URI",
            ).resolve()
            != receipt_path.resolve()
            or _file_uri_path(
                recorded_payload.get("root_uri"),
                "canonical Packwiz payload URI",
            ).resolve()
            != payload.resolve()
            or not v2_target_valid
        ):
            raise SusyModDevError(
                "canonical Packwiz materialization receipt does not bind its payload: "
                f"{receipt_path}"
            )
        roots.append(payload.resolve())
    return roots


def _runtime_tree(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if not root.is_dir() or root.is_symlink():
        raise SusyModDevError(f"runtime payload is not a regular directory: {root}")
    records: dict[str, dict[str, Any]] = {}
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise SusyModDevError(f"runtime payload contains a symlink: {relative}")
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise SusyModDevError(f"runtime payload contains a special file: {relative}")
        raw_digest = sha256()
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                raw_digest.update(chunk)
                size += len(chunk)
        total += size
        if len(records) + 1 > MAX_RUNTIME_FILES or total > MAX_RUNTIME_BYTES:
            raise SusyModDevError("runtime payload exceeds the composition limits")
        records[relative] = {
            "sha256": raw_digest.hexdigest(),
            "size": size,
            "mode": stat.S_IMODE(info.st_mode),
        }
    entries = [
        {"path": path, **record}
        for path, record in records.items()
    ]
    summary = {
        "tree_sha256": "sha256:"
        + sha256(
            json.dumps(
                entries,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
        "file_count": len(records),
        "total_bytes": total,
    }
    return summary, records


def _stage_client_runtime(
    run_root: Path,
    plan: Mapping[str, Any],
    artifact: Mapping[str, Any],
    materialization: Mapping[str, Any],
) -> dict[str, Any]:
    receipt = materialization.get("receipt")
    materialization_version = _packwiz_materialization_version(
        materialization,
        receipt,
    )
    if (
        materialization_version != 2
        or materialization.get("outcome") not in {"installed", "reused"}
        or not isinstance(receipt, dict)
        or receipt.get("state") != "materialized"
        or receipt.get("readiness") != "pack-payload-installed"
    ):
        raise SusyModDevError("client stage requires an exact Packwiz materialization receipt")
    request = receipt.get("request")
    workspace = receipt.get("workspace")
    target = receipt.get("target")
    payload = receipt.get("payload")
    launcher = receipt.get("launcher")
    if not all(isinstance(row, dict) for row in (request, workspace, target, payload, launcher)):
        raise SusyModDevError("client materialization receipt is incomplete")
    assert isinstance(request, dict)
    assert isinstance(workspace, dict)
    assert isinstance(target, dict)
    assert isinstance(payload, dict)
    assert isinstance(launcher, dict)
    if request.get("side") != "client" or request.get("launcher") not in {"prism", "multimc"}:
        raise SusyModDevError("client materialization has the wrong side or launcher")
    pack_root = Path(str(plan["supersymmetry"]["root"])).resolve()
    if _file_uri_path(workspace.get("root_uri"), "materialized pack root").resolve() != pack_root:
        raise SusyModDevError("client materialization belongs to a different pack checkout")
    source_instance = _file_uri_path(
        target.get("instance_root_uri"), "materialized client instance"
    ).resolve()
    payload_root = source_instance / ".minecraft"
    source_summary, source_records = _runtime_tree(payload_root)
    expected_summary = {
        key: payload.get(key)
        for key in ("tree_sha256", "file_count", "total_bytes")
    }
    if source_summary != expected_summary:
        raise SusyModDevError("materialized client payload has drifted")
    manifest = source_instance / "mmc-pack.json"
    manifest_raw = _read_regular(manifest, "materialized launcher manifest")
    if sha256(manifest_raw).hexdigest() != launcher.get("manifest_sha256_after"):
        raise SusyModDevError("materialized launcher manifest has drifted")

    selected = plan["replacement"]["match"]["selected"]
    baseline_relative = "mods/" + str(selected["filename"])
    baseline_record = source_records.get(baseline_relative)
    if baseline_record is None:
        raise SusyModDevError(
            f"materialized client lacks the pinned baseline artifact: {baseline_relative}"
        )
    baseline = selected.get("baseline")
    if not isinstance(baseline, dict):
        raise SusyModDevError("Packwiz replacement lacks a baseline digest")
    algorithm = baseline.get("hash_format")
    expected_hash = baseline.get("hash")
    if algorithm not in {"sha1", "sha256"} or not isinstance(expected_hash, str):
        raise SusyModDevError("Packwiz replacement uses an unsupported baseline digest")
    baseline_path = payload_root / baseline_relative
    observed_hash = hashlib.new(algorithm, baseline_path.read_bytes()).hexdigest()
    if observed_hash != expected_hash:
        raise SusyModDevError(
            f"materialized baseline digest drifted: expected {expected_hash}, observed {observed_hash}"
        )

    artifact_path = Path(str(artifact.get("path"))).resolve()
    artifact_raw = _read_regular(artifact_path, "candidate artifact", limit=MAX_SOURCE_BYTES)
    candidate_sha256 = sha256(artifact_raw).hexdigest()
    if candidate_sha256 != artifact.get("sha256"):
        raise SusyModDevError("candidate artifact changed after verification")
    destination = run_root / "runtime/client"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise SusyModDevError(f"client stage target already exists: {destination}")
    shutil.copytree(source_instance, destination, copy_function=shutil.copy2)
    installed = destination / ".minecraft" / baseline_relative
    temporary = installed.with_name(installed.name + ".workbench-candidate")
    with temporary.open("xb") as stream:
        stream.write(artifact_raw)
    os.replace(temporary, installed)
    target_summary, target_records = _runtime_tree(destination / ".minecraft")
    if set(target_records) != set(source_records):
        raise SusyModDevError("candidate client composition changed the payload path set")
    changed = [
        path
        for path in source_records
        if source_records[path] != target_records[path]
    ]
    if changed != [baseline_relative]:
        raise SusyModDevError(
            "candidate client composition changed unexpected payload files: "
            + ", ".join(changed[:10])
        )
    if target_records[baseline_relative]["sha256"] != candidate_sha256:
        raise SusyModDevError("candidate client composition installed the wrong bytes")
    if sha256(baseline_path.read_bytes()).hexdigest() != baseline_record["sha256"]:
        raise SusyModDevError("candidate client composition mutated the canonical materialization")
    stage_receipt = {
        "format": "workbench-susy-mod-client-stage-v1",
        "schema_version": 1,
        "state": "staged",
        "run_id": run_root.name,
        "plan_id": plan["plan_id"],
        "materialization_id": receipt.get("materialization_id"),
        "source": {
            "instance_uri": source_instance.as_uri(),
            "payload": source_summary,
            "receipt_uri": target.get("receipt_uri"),
        },
        "replacement": {
            "path": baseline_relative,
            "baseline": {
                "hash_format": algorithm,
                "hash": expected_hash,
                "sha256": baseline_record["sha256"],
                "size": baseline_record["size"],
            },
            "candidate": {
                "sha256": candidate_sha256,
                "size": len(artifact_raw),
                "mod_ids": list(artifact.get("mod_ids", [])),
            },
        },
        "target": {
            "instance_uri": destination.as_uri(),
            "payload": target_summary,
            "changed_paths": changed,
            "launch_ready": True,
            "launched": False,
        },
        "canonical_materialization_mutated": False,
    }
    stage_receipt["stage_id"] = "workbench-susy-mod-client-stage:" + _digest(stage_receipt)
    _write_json(run_root / "runtime/client-stage-v1.json", stage_receipt)
    return stage_receipt


def _apply_managed_overlays(
    source: Path,
    overlays: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    for overlay in overlays:
        kind = overlay.get("kind", "empty-property")
        relative = overlay.get("path")
        value = overlay.get("value")
        if not all(isinstance(item, str) and item for item in (relative, value)):
            raise SusyModDevError("managed build overlay is incomplete")
        assert isinstance(relative, str)
        assert isinstance(value, str)
        path = _safe_relative(source, relative, "managed build overlay")
        before = _read_regular(path, "managed build overlay")
        text = before.decode("utf-8")
        details: dict[str, Any]
        if kind == "empty-property":
            key = overlay.get("key")
            if not isinstance(key, str) or not key:
                raise SusyModDevError("managed property overlay lacks a key")
            pattern = re.compile(
                rf"(?m)^(?P<prefix>[ \t]*{re.escape(key)}[ \t]*[:=][ \t]*)(?P<value>[^\r\n]*)(?P<ending>\r?)$"
            )
            matches = list(pattern.finditer(text))
            if len(matches) != 1 or matches[0].group("value").strip():
                raise SusyModDevError(
                    f"managed version overlay expected one empty {key} property: {relative}"
                )
            after_text = pattern.sub(
                lambda match: match.group("prefix") + value + match.group("ending"),
                text,
                count=1,
            )
            details = {"key": key}
        elif kind == "gradle-plugin-version":
            plugin_id = overlay.get("plugin_id")
            from_version = overlay.get("from_version")
            if not all(
                isinstance(item, str) and item
                for item in (plugin_id, from_version)
            ):
                raise SusyModDevError("managed Gradle plugin overlay is incomplete")
            assert isinstance(plugin_id, str)
            assert isinstance(from_version, str)
            pattern = re.compile(
                rf"(?P<prefix>\bid\s*(['\"]){re.escape(plugin_id)}\2\s+version\s*(['\"]))"
                rf"(?P<version>[^'\"]+)(?P<suffix>\3)"
            )
            matches = list(pattern.finditer(text))
            if len(matches) != 1 or matches[0].group("version") != from_version:
                raise SusyModDevError(
                    "managed Gradle plugin overlay expected exactly one "
                    f"{plugin_id}:{from_version} declaration: {relative}"
                )
            after_text = pattern.sub(
                lambda match: match.group("prefix") + value + match.group("suffix"),
                text,
                count=1,
            )
            details = {
                "plugin_id": plugin_id,
                "from_version": from_version,
            }
        elif kind == "gradle-dependency-version":
            group = overlay.get("group")
            module = overlay.get("module")
            from_version = overlay.get("from_version")
            if not all(
                isinstance(item, str) and item
                for item in (group, module, from_version)
            ):
                raise SusyModDevError("managed Gradle dependency overlay is incomplete")
            assert isinstance(group, str)
            assert isinstance(module, str)
            assert isinstance(from_version, str)
            pattern = re.compile(
                rf"(?P<prefix>(['\"]){re.escape(group)}:{re.escape(module)}:)"
                rf"(?P<version>[^:'\"]+)(?P<suffix>\2)"
            )
            matches = list(pattern.finditer(text))
            if len(matches) != 1 or matches[0].group("version") != from_version:
                raise SusyModDevError(
                    "managed Gradle dependency overlay expected exactly one "
                    f"{group}:{module}:{from_version} declaration: {relative}"
                )
            after_text = pattern.sub(
                lambda match: match.group("prefix") + value + match.group("suffix"),
                text,
                count=1,
            )
            details = {
                "group": group,
                "module": module,
                "from_version": from_version,
            }
        else:
            raise SusyModDevError(f"unsupported managed overlay kind: {kind}")
        after = after_text.encode("utf-8")
        path.write_bytes(after)
        applied.append(
            {
                "kind": kind,
                "path": relative,
                **details,
                "value": value,
                "purpose": overlay.get("purpose"),
                "before_sha256": sha256(before).hexdigest(),
                "after_sha256": sha256(after).hexdigest(),
            }
        )
    return applied


def _jar_facts(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    mod_ids: set[str] = set()
    mod_metadata: list[dict[str, Any]] = []
    class_versions: set[int] = set()
    mixin_configs: list[str] = []
    refmaps: list[str] = []
    manifest: dict[str, str] = {}
    try:
        with ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise SusyModDevError(f"built artifact has corrupt ZIP members: {path}")
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise SusyModDevError(f"built artifact has duplicate ZIP members: {path}")
            for name in names:
                pure = Path(name)
                if pure.is_absolute() or ".." in pure.parts:
                    raise SusyModDevError(f"built artifact has unsafe ZIP member: {name}")
                if name.endswith(".class"):
                    header = archive.read(name)[:8]
                    if len(header) == 8 and header[:4] == b"\xca\xfe\xba\xbe":
                        class_versions.add(int.from_bytes(header[6:8], "big"))
                if re.fullmatch(r"(?:.*/)?mixins\..+\.json", name):
                    mixin_configs.append(name)
                if name.endswith("refmap.json"):
                    refmaps.append(name)
            for candidate in ("mcmod.info", "META-INF/mcmod.info"):
                if candidate not in names:
                    continue
                try:
                    value = json.loads(archive.read(candidate).decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise SusyModDevError(
                        f"built artifact has invalid {candidate}: {path}"
                    ) from exc
                rows = value if isinstance(value, list) else [value]
                for row in rows:
                    if isinstance(row, dict) and isinstance(row.get("modid"), str):
                        mod_ids.add(row["modid"])
                        mod_metadata.append(
                            {
                                key: row.get(key)
                                for key in ("modid", "name", "version", "mcversion")
                            }
                        )
            if "META-INF/MANIFEST.MF" in names:
                text = archive.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
                current: str | None = None
                for line in text.splitlines():
                    if line.startswith(" ") and current:
                        manifest[current] += line[1:]
                    elif ": " in line:
                        current, value = line.split(": ", 1)
                        manifest[current] = value
    except BadZipFile as exc:
        raise SusyModDevError(f"built artifact is not a ZIP/JAR: {path}") from exc
    return {
        "path": str(path),
        "size": len(raw),
        "sha256": sha256(raw).hexdigest(),
        "mod_ids": sorted(mod_ids),
        "mod_metadata": mod_metadata,
        "classfile_majors": sorted(class_versions),
        "mixin_configs": sorted(mixin_configs),
        "refmaps": sorted(refmaps),
        "manifest": manifest,
    }


def _select_artifacts(
    source: Path,
    project: Mapping[str, Any],
    *,
    expected_version: str | None,
) -> list[dict[str, Any]]:
    paths = sorted(source.glob("**/build/libs/*.jar"))
    eligible = [
        path
        for path in paths
        if not any(path.stem.lower().endswith(token) for token in NON_DISTRIBUTABLE_TOKENS)
    ]
    facts: list[dict[str, Any]] = []
    failures: list[str] = []
    expected_ids = set(project.get("mod_ids", []))
    archive = _normal(project.get("archive_base"))
    for path in eligible:
        try:
            row = _jar_facts(path)
        except SusyModDevError as exc:
            failures.append(str(exc))
            continue
        score = 0
        if expected_ids & set(row["mod_ids"]):
            score += 100
        if archive and _normal(path.name).startswith(archive):
            score += 25
        if row["mod_ids"]:
            score += 10
        row["selection_score"] = score
        facts.append(row)
    if not facts:
        detail = "; ".join(failures[:3]) or "no distributable JAR was produced"
        raise SusyModDevError(detail)
    best = max(row["selection_score"] for row in facts)
    selected = [row for row in facts if row["selection_score"] == best]
    if len(selected) != 1 or best < 10:
        names = ", ".join(Path(row["path"]).name for row in selected)
        raise SusyModDevError(f"built artifact set is ambiguous: {names}")
    artifact = selected[0]
    poison = re.search(
        r"(?i)(?:^|[-_.])(?:null|undefined|unknown|no[-_]?git[-_]?tag[-_]?set)(?:$|[-_.])",
        Path(artifact["path"]).stem,
    )
    if poison is not None:
        raise SusyModDevError(
            "built artifact filename contains an unresolved identity token: "
            + poison.group(0).strip("-_.")
        )
    if expected_ids and not expected_ids.issubset(set(artifact["mod_ids"])):
        raise SusyModDevError(
            "built artifact mod IDs do not contain the source project identity"
        )
    if expected_version is not None:
        versions = {
            row.get("version")
            for row in artifact["mod_metadata"]
            if row.get("modid") in expected_ids and isinstance(row.get("version"), str)
        }
        if expected_ids and versions != {expected_version}:
            raise SusyModDevError(
                "built artifact version does not match the source-bound candidate "
                f"version: expected {expected_version}, observed {sorted(versions)}"
            )
    if artifact["classfile_majors"] and max(artifact["classfile_majors"]) > 52:
        raise SusyModDevError(
            "built artifact contains bytecode newer than Java 8: "
            + str(max(artifact["classfile_majors"]))
        )
    return [artifact]


def execute_susy_mod_build(
    suite_root: Path | str,
    plan: Mapping[str, Any],
    *,
    timeout_seconds: int = 1_200,
    stage_client: bool = False,
    materialization: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    validate_susy_mod_plan(plan)
    if plan["state"] != "ready":
        raise SusyModDevError("blocked SUSY mod plan cannot execute")
    if not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 7_200:
        raise SusyModDevError("build timeout must be between 1 and 7200 seconds")
    suite = Path(suite_root).resolve()
    state = default_suite_state_root(suite)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    plan_suffix = str(plan["plan_id"]).rsplit(":", 1)[-1][:12]
    run_id = f"susy-mod-{timestamp}-{plan_suffix}"
    run_root = state / "dev-runs" / run_id
    if run_root.exists():
        raise SusyModDevError(f"managed run already exists: {run_root}")
    source = run_root / "source"
    source.mkdir(parents=True)
    started = datetime.now(timezone.utc)
    manifest = _source_manifest(Path(plan["project"]["root"]), source)
    expected_source = plan["project"].get("source_fingerprint")
    if not isinstance(expected_source, dict) or manifest["source_digest"] != expected_source.get(
        "digest"
    ):
        raise SusyModDevError(
            "source checkout changed after planning; review a fresh SUSY mod plan"
        )
    _write_json(run_root / "source-manifest.json", manifest)
    applied_overlays = _apply_managed_overlays(
        source,
        plan["build"].get("managed_overlays", []),
    )

    wrapper_name = "gradlew.bat" if os.name == "nt" else "gradlew"
    wrapper = source / wrapper_name
    planned_argv = plan["build"].get("argv")
    if not isinstance(planned_argv, list) or len(planned_argv) < 2:
        raise SusyModDevError("SUSY mod plan lacks executable build argv")
    argv = [str(wrapper), *[str(token) for token in planned_argv[1:]]]
    java = plan["build"]["java"]
    java_home = Path(java["home"])
    available_java = plan["build"].get("available_java", [])
    installation_paths = ",".join(
        row["home"]
        for row in available_java
        if isinstance(row, dict) and isinstance(row.get("home"), str)
    )
    if installation_paths:
        argv.insert(3, f"-Dorg.gradle.java.installations.paths={installation_paths}")
        argv.insert(4, "-Dorg.gradle.java.installations.auto-detect=false")
    environment = dict(os.environ)
    for key in ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "GRADLE_OPTS"):
        environment.pop(key, None)
    environment["JAVA_HOME"] = str(java_home)
    environment["PATH"] = str(java_home / "bin") + os.pathsep + environment.get("PATH", "")
    environment["GRADLE_USER_HOME"] = str(
        (state / "gradle-home/susy-mod-dev").resolve()
    )
    environment["CI"] = "true"
    for override in plan["build"].get("environment_overrides", []):
        if (
            not isinstance(override, dict)
            or not isinstance(override.get("name"), str)
            or not isinstance(override.get("value"), str)
        ):
            raise SusyModDevError("managed build environment override is invalid")
        environment[override["name"]] = override["value"]
    stdout_path = run_root / "build.stdout.log"
    stderr_path = run_root / "build.stderr.log"
    stage: dict[str, Any] = {
        "id": "project-build",
        "state": "running",
        "argv": argv,
        "cwd": str(source),
        "started_at": started.isoformat(),
        "stdout_uri": stdout_path.as_uri(),
        "stderr_uri": stderr_path.as_uri(),
        "environment_overrides": deepcopy(
            plan["build"].get("environment_overrides", [])
        ),
    }
    timed_out = False
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            completed = subprocess.run(
                argv,
                cwd=source,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                timeout=timeout_seconds,
                check=False,
            )
            exit_code: int | None = completed.returncode
        except subprocess.TimeoutExpired:
            exit_code = None
            timed_out = True
    ended = datetime.now(timezone.utc)
    stage.update(
        {
            "state": "failed" if timed_out or exit_code != 0 else "passed",
            "exit_code": exit_code,
            "timed_out": timed_out,
            "ended_at": ended.isoformat(),
            "duration_seconds": round((ended - started).total_seconds(), 3),
        }
    )
    problems: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    overlay: dict[str, Any] | None = None
    runtime_stage: dict[str, Any] | None = None
    stages: list[dict[str, Any]] = [stage]
    if stage["state"] != "passed":
        problems.append(
            _problem(
                "GRADLE_BUILD_TIMEOUT" if timed_out else "GRADLE_BUILD_FAILED",
                "The constituent mod build did not complete successfully",
                (
                    f"Gradle exceeded the {timeout_seconds}-second timeout."
                    if timed_out
                    else _gradle_failure_detail(stderr_path, exit_code)
                ),
                evidence_uri=stderr_path.as_uri(),
            )
        )
    else:
        try:
            artifacts = _select_artifacts(
                source,
                plan["project"],
                expected_version=(
                    plan["build"].get("candidate_version")
                    if not plan["project"].get("declared_version")
                    else None
                ),
            )
        except SusyModDevError as exc:
            problems.append(
                _problem(
                    "BUILD_ARTIFACT_INVALID",
                    "Gradle exited zero but no trustworthy distributable artifact set was produced",
                    str(exc),
                )
            )
        if artifacts:
            selected = plan["replacement"]["match"]["selected"]
            overlay_root = run_root / "supersymmetry-overlay"
            mods = overlay_root / "mods"
            mods.mkdir(parents=True)
            installed = mods / selected["filename"]
            shutil.copy2(Path(artifacts[0]["path"]), installed)
            installed_raw = installed.read_bytes()
            overlay = {
                "root_uri": overlay_root.as_uri(),
                "metadata_path": selected["metadata_path"],
                "baseline_filename": selected["filename"],
                "baseline_hash": deepcopy(selected["baseline"]),
                "candidate_path": installed.relative_to(overlay_root).as_posix(),
                "candidate_sha256": sha256(installed_raw).hexdigest(),
                "candidate_size": len(installed_raw),
                "applicable_sides": list(plan["replacement"]["applicable_sides"]),
                "runtime_installed_and_loaded": False,
            }
            _write_json(overlay_root / "replacement.json", overlay)

    if stage_client and artifacts:
        client_started = datetime.now(timezone.utc)
        client_stage_record: dict[str, Any] = {
            "id": "client-materialization-and-replacement",
            "state": "running",
            "started_at": client_started.isoformat(),
        }
        try:
            selected_materialization = materialization
            if selected_materialization is None:
                from .runtime_materialize import (
                    PackwizMaterializationError,
                    materialize_project_runtime,
                )

                try:
                    selected_materialization = materialize_project_runtime(
                        suite,
                        plan["supersymmetry"]["root"],
                        launcher="prism",
                        state_root=state,
                        packwiz_executable=(
                            Path(plan["supersymmetry"]["root"]) / "packwiz"
                        ),
                        seed_roots=_canonical_packwiz_seed_roots(
                            state
                        ),
                    )
                except PackwizMaterializationError as exc:
                    raise SusyModDevError(str(exc)) from exc
            runtime_stage = _stage_client_runtime(
                run_root,
                plan,
                artifacts[0],
                selected_materialization,
            )
            client_stage_record["state"] = "passed"
            client_stage_record["stage_id"] = runtime_stage["stage_id"]
        except (OSError, ValueError, SusyModDevError) as exc:
            client_stage_record["state"] = "failed"
            client_stage_record["detail"] = str(exc)
            problems.append(
                _problem(
                    "CLIENT_STAGE_FAILED",
                    "The candidate built, but the disposable Supersymmetry client could not be composed",
                    str(exc),
                )
            )
        client_ended = datetime.now(timezone.utc)
        client_stage_record["ended_at"] = client_ended.isoformat()
        client_stage_record["duration_seconds"] = round(
            (client_ended - client_started).total_seconds(), 3
        )
        stages.append(client_stage_record)

    outcome = "passed" if not problems else "failed"
    selector_argv: list[str] = []
    match = plan["replacement"]["match"]
    selected_entry = match.get("selected") if isinstance(match, dict) else None
    if (
        isinstance(match, dict)
        and match.get("reason") == "explicit-pack-entry"
        and isinstance(selected_entry, dict)
        and isinstance(selected_entry.get("metadata_path"), str)
    ):
        selector_argv = [
            "--pack-mod",
            Path(selected_entry["metadata_path"]).name,
        ]
    rerun_argv = [
        "workbench",
        "dev",
        "build",
        "--project",
        plan["project"]["root"],
        "--pack",
        plan["supersymmetry"]["root"],
        *selector_argv,
    ]
    stage_argv = [
        "workbench",
        "dev",
        "stage",
        "--project",
        plan["project"]["root"],
        "--pack",
        plan["supersymmetry"]["root"],
        *selector_argv,
    ]
    result: dict[str, Any] = {
        "format": RESULT_FORMAT,
        "schema_version": 1,
        "run_id": run_id,
        "outcome": outcome,
        "failed_stage": (
            None
            if outcome == "passed"
            else (
                "client-materialization-and-replacement"
                if stage_client and stage["state"] == "passed"
                else "project-build"
            )
        ),
        "plan_id": plan["plan_id"],
        "project": deepcopy(plan["project"]),
        "supersymmetry": deepcopy(plan["supersymmetry"]),
        "replacement": deepcopy(plan["replacement"]),
        "source_snapshot": {
            "root_uri": source.as_uri(),
            "manifest_uri": (run_root / "source-manifest.json").as_uri(),
            "source_digest": manifest["source_digest"],
            "file_count": manifest["file_count"],
            "total_bytes": manifest["total_bytes"],
            "managed_build_overlays": applied_overlays,
        },
        "stages": stages,
        "artifact_set": artifacts,
        "overlay": overlay,
        "runtime": {
            "client": runtime_stage,
            "server": None,
        },
        "problems": problems,
        "cleanup": {
            "owned_processes_running": False,
            "checkout_mutated_by_workbench": False,
            "managed_run_root": str(run_root),
        },
        "next_actions": [
            {
                "id": "rerun-build",
                "available": True,
                "argv": rerun_argv,
            },
            {
                "id": "stage-client",
                "available": bool(artifacts) and runtime_stage is None,
                "argv": (
                    stage_argv
                    if artifacts and runtime_stage is None
                    else None
                ),
                "reason": (
                    None
                    if artifacts and runtime_stage is None
                    else (
                        "client already staged"
                        if runtime_stage is not None
                        else "candidate build did not pass"
                    )
                ),
            },
            {
                "id": "launch-client",
                "available": outcome == "passed" and runtime_stage is not None,
                "argv": (
                    ["workbench", "dev", "launch", "--run", run_id]
                    if outcome == "passed" and runtime_stage is not None
                    else None
                ),
                "reason": (
                    None
                    if outcome == "passed" and runtime_stage is not None
                    else "candidate client was not staged"
                ),
            },
        ],
        "limitations": list(plan["limitations"]),
    }
    result["result_id"] = "workbench-susy-mod-dev-result:" + _digest(result)
    _write_json(run_root / "result.json", result)
    return result


def render_susy_mod_plan(plan: Mapping[str, Any]) -> str:
    validate_susy_mod_plan(plan)
    project = plan["project"]
    match = plan["replacement"]["match"]
    lines = [
        "Supersymmetry Mod Dev",
        f"Project: {project['name']} ({', '.join(project['mod_ids']) or 'mod ID unresolved'})",
        f"Checkout: {project['root']}",
        f"Target: Supersymmetry {plan['supersymmetry'].get('version')} / Cleanroom execution target",
        f"State: {plan['state']}",
    ]
    selected = match.get("selected") if isinstance(match, dict) else None
    if isinstance(selected, dict):
        lines.append(
            f"Replacement: {selected['metadata_path']} -> {selected['filename']} ({selected['side']})"
        )
    java = plan["build"].get("java")
    if isinstance(java, dict):
        lines.append(f"Build Java: {java['version']} at {java['home']}")
    adapter = plan["build"].get("adapter")
    if isinstance(adapter, dict):
        lines.append(
            f"Adapter: {adapter['id']} ({adapter['confidence']}; task={adapter['candidate_task']})"
        )
    argv = plan["build"].get("argv")
    if isinstance(argv, list):
        lines.append("Build: " + json.dumps(argv, ensure_ascii=False))
    for warning in plan["warnings"]:
        lines.append(f"Warning [{warning['code']}]: {warning['summary']}")
    for problem in plan["problems"]:
        lines.append(f"Blocked [{problem['code']}]: {problem['summary']}")
    lines.append(
        "Boundary: build and replacement overlay only; no Minecraft process is launched yet."
    )
    return "\n".join(lines) + "\n"


def render_susy_mod_result(result: Mapping[str, Any]) -> str:
    lines = [
        "Supersymmetry Mod Dev Build",
        f"Run: {result['run_id']}",
        f"Outcome: {result['outcome']}",
        f"Evidence: {result['cleanup']['managed_run_root']}/result.json",
    ]
    for artifact in result.get("artifact_set", []):
        lines.append(
            f"Artifact: {Path(artifact['path']).name} sha256:{artifact['sha256']} "
            f"mod IDs={','.join(artifact['mod_ids'])}"
        )
    overlay = result.get("overlay")
    if isinstance(overlay, dict):
        lines.append(
            f"Overlay: {overlay['candidate_path']} replaces {overlay['baseline_filename']}"
        )
    runtime = result.get("runtime")
    client = runtime.get("client") if isinstance(runtime, dict) else None
    if isinstance(client, dict):
        lines.append(
            "Client staged: "
            + client["target"]["instance_uri"]
            + f" (changed {','.join(client['target']['changed_paths'])})"
        )
    for problem in result.get("problems", []):
        lines.append(f"Failed [{problem['code']}]: {problem['summary']}")
    lines.append(
        "Runtime: client staged but not launched; loaded-byte proof is next."
        if isinstance(client, dict)
        else "Runtime: not launched; physical SUSY client/server is the next slice."
    )
    return "\n".join(lines) + "\n"
