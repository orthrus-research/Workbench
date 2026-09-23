"""Safe, inspectable primitives for disposable world-generation iterations."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time
from typing import Any, Iterator, Mapping, Sequence
import zipfile

from workbench_api.runtime import RuntimeProviderError


FORMAT = "workbench-worldgen-iteration-report-v1"
PROFILE_FORMAT = "workbench-worldgen-iteration-profile-v2"
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
PLAN_RE = re.compile(r"\bmods\s*\.\s*worldStudio\b")
SKIP_TOP_LEVEL_DIRECTORIES = frozenset(
    {
        "backups",
        "cache",
        "crash-reports",
        "journeymap",
        "logs",
        "saves",
        "screenshots",
        "strata-worldgen-observer",
    }
)
WORLD_TOP_LEVEL_DIRECTORIES = frozenset(
    {"world", "world_nether", "world_the_end"}
)
SKIP_TOP_LEVEL_FILE_SUFFIXES = frozenset({".jfr", ".log"})
REQUIRED_PROFILE_KEYS = frozenset(
    {
        "artifact",
        "cleanroom",
        "defaults",
        "fixture",
        "format",
        "minecraft_version",
        "plan",
        "platform_profile_id",
        "profile_id",
        "runtime",
        "schema_version",
        "toolchains",
        "world_type",
    }
)


class WorldgenIterationError(RuntimeProviderError):
    """An actionable iteration input, provisioning, or execution failure."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _require_regular(path: Path, context: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise WorldgenIterationError(f"{context} cannot be a symlink: {expanded}")
    resolved = expanded.resolve()
    if not resolved.is_file():
        raise WorldgenIterationError(f"{context} is not a regular file: {resolved}")
    return resolved


def _require_directory(path: Path, context: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise WorldgenIterationError(f"{context} cannot be a symlink: {expanded}")
    resolved = expanded.resolve()
    if not resolved.is_dir():
        raise WorldgenIterationError(
            f"{context} is not a regular directory: {resolved}"
        )
    return resolved


def resolve_profile_path(root: Path, profile: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", profile):
        raise WorldgenIterationError(f"invalid profile name: {profile!r}")
    directory = (
        root
        / "profiles/packs"
        / profile
        / "worldgen"
    )
    return directory / "worldgen-iteration-profile-v2.json"


def load_profile(path: Path, root: Path) -> dict[str, Any]:
    path = _require_regular(path, "worldgen iteration profile")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorldgenIterationError(f"cannot parse profile {path}: {exc}") from exc
    if not isinstance(value, dict) or set(value) != REQUIRED_PROFILE_KEYS:
        raise WorldgenIterationError(
            "worldgen iteration profile has an unsupported shape"
        )
    profile_format = value.get("format")
    schema_version = value.get("schema_version")
    if (profile_format, schema_version) != (PROFILE_FORMAT, 2):
        raise WorldgenIterationError("unsupported worldgen iteration profile version")
    for key in (
        "profile_id",
        "platform_profile_id",
        "minecraft_version",
        "cleanroom",
        "world_type",
        "fixture",
        "plan",
    ):
        if not isinstance(value.get(key), str) or not value[key]:
            raise WorldgenIterationError(f"profile {key} must be a non-empty string")
    for key in ("artifact", "runtime", "toolchains", "defaults"):
        if not isinstance(value.get(key), dict):
            raise WorldgenIterationError(f"profile {key} must be an object")

    artifact = value["artifact"]
    if set(artifact) != {"exclude_suffixes", "glob", "mod_id"}:
        raise WorldgenIterationError("profile artifact has an unsupported shape")
    artifact_glob = artifact.get("glob")
    exclude_suffixes = artifact.get("exclude_suffixes")
    mod_id = artifact.get("mod_id")
    if (
        not isinstance(artifact_glob, str)
        or not artifact_glob
        or Path(artifact_glob).is_absolute()
        or ".." in Path(artifact_glob).parts
        or not isinstance(exclude_suffixes, list)
        or not all(isinstance(item, str) and item for item in exclude_suffixes)
        or not isinstance(mod_id, str)
        or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", mod_id)
    ):
        raise WorldgenIterationError("profile artifact contains invalid values")

    defaults = value["defaults"]
    expected_defaults = {
        "debug_region",
        "diagnostic_sample_modulo",
        "fast_region",
        "halo_chunks",
        "heap",
        "seed",
    }
    if set(defaults) != expected_defaults:
        raise WorldgenIterationError("profile defaults have an unsupported shape")
    for key in ("fast_region", "debug_region"):
        region = defaults.get(key)
        if (
            not isinstance(region, list)
            or len(region) != 4
            or not all(isinstance(item, int) and not isinstance(item, bool) for item in region)
        ):
            raise WorldgenIterationError(f"profile defaults.{key} is invalid")
        _, _, width, height = region
        if width < 1 or height < 1 or width > 64 or height > 64 or width * height > 1024:
            raise WorldgenIterationError(f"profile defaults.{key} is out of bounds")
    if not isinstance(defaults.get("seed"), int) or isinstance(defaults["seed"], bool):
        raise WorldgenIterationError("profile defaults.seed must be an integer")
    if (
        not isinstance(defaults.get("heap"), str)
        or not re.fullmatch(r"[1-9][0-9]*[KMGkmg]", defaults["heap"])
    ):
        raise WorldgenIterationError("profile defaults.heap is invalid")
    if (
        not isinstance(defaults.get("diagnostic_sample_modulo"), int)
        or isinstance(defaults["diagnostic_sample_modulo"], bool)
        or defaults["diagnostic_sample_modulo"] < 1
    ):
        raise WorldgenIterationError(
            "profile defaults.diagnostic_sample_modulo must be positive"
        )
    if (
        not isinstance(defaults.get("halo_chunks"), int)
        or isinstance(defaults["halo_chunks"], bool)
        or not 0 <= defaults["halo_chunks"] <= 64
    ):
        raise WorldgenIterationError("profile defaults.halo_chunks is invalid")

    runtime = value["runtime"]
    expected_runtime = {
        "discovery_roots",
        "passthrough_integrations",
        "required_mod_ids",
        "required_mod_filename_tokens",
        "server_jar_glob",
    }
    if set(runtime) != expected_runtime:
        raise WorldgenIterationError("profile runtime has an unsupported shape")
    discovery_roots = runtime.get("discovery_roots")
    required_tokens = runtime.get("required_mod_filename_tokens")
    required_mod_ids = runtime.get("required_mod_ids")
    server_glob = runtime.get("server_jar_glob")
    if (
        not isinstance(discovery_roots, list)
        or not discovery_roots
        or not all(
            isinstance(item, str)
            and item
            and not Path(item).is_absolute()
            and ".." not in Path(item).parts
            for item in discovery_roots
        )
        or not isinstance(required_tokens, list)
        or not required_tokens
        or not all(isinstance(item, str) and item for item in required_tokens)
        or not isinstance(server_glob, str)
        or not server_glob
        or Path(server_glob).is_absolute()
        or ".." in Path(server_glob).parts
    ):
        raise WorldgenIterationError("profile runtime contains invalid values")
    if (
        not isinstance(required_mod_ids, list)
        or not required_mod_ids
        or len(required_mod_ids) != len(set(required_mod_ids))
        or not all(
            isinstance(mod_id, str)
            and re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", mod_id)
            for mod_id in required_mod_ids
        )
    ):
        raise WorldgenIterationError("profile runtime.required_mod_ids is invalid")
    integrations = runtime.get("passthrough_integrations")
    if not isinstance(integrations, list):
        raise WorldgenIterationError(
            "profile runtime.passthrough_integrations must be an array"
        )
    integration_ids: set[str] = set()
    expected_integration = {"filename_tokens", "integration_id", "mod_ids", "policy"}
    for declaration in integrations:
        if not isinstance(declaration, dict) or set(declaration) != expected_integration:
            raise WorldgenIterationError(
                "profile passthrough integration has an unsupported shape"
            )
        integration_id = declaration["integration_id"]
        filename_tokens = declaration["filename_tokens"]
        integration_mod_ids = declaration["mod_ids"]
        policy = declaration["policy"]
        if (
            not isinstance(integration_id, str)
            or not integration_id
            or integration_id in integration_ids
            or not isinstance(filename_tokens, list)
            or not all(isinstance(item, str) and item for item in filename_tokens)
            or not isinstance(integration_mod_ids, list)
            or not all(isinstance(item, str) and item for item in integration_mod_ids)
            or not isinstance(policy, str)
            or not policy
        ):
            raise WorldgenIterationError(
                "profile passthrough integration contains invalid values"
            )
        integration_ids.add(integration_id)

    toolchains = value["toolchains"]
    expected_toolchains = {
        "minimum_cleanroom_java_major",
        "proven_cleanroom_java",
        "proven_gradle",
    }
    if set(toolchains) != expected_toolchains or (
        not isinstance(toolchains.get("minimum_cleanroom_java_major"), int)
        or isinstance(toolchains["minimum_cleanroom_java_major"], bool)
        or toolchains["minimum_cleanroom_java_major"] < 1
        or not isinstance(toolchains.get("proven_cleanroom_java"), str)
        or not toolchains["proven_cleanroom_java"]
        or not isinstance(toolchains.get("proven_gradle"), str)
        or not toolchains["proven_gradle"]
    ):
        raise WorldgenIterationError("profile toolchains contain invalid values")
    if value["minecraft_version"] != "1.12.2":
        raise WorldgenIterationError("worldgen iteration profiles require Minecraft 1.12.2")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value["cleanroom"]):
        raise WorldgenIterationError("profile cleanroom version is not path-safe")
    if not re.fullmatch(
        r"workbench-pack:[a-z0-9][a-z0-9_-]*:worldgen-[a-z0-9_.-]+",
        value["profile_id"],
    ):
        raise WorldgenIterationError("profile identity is invalid")
    if value["platform_profile_id"] != (
        f"workbench-platform:cleanroom:{value['cleanroom']}"
    ):
        raise WorldgenIterationError(
            "profile platform identity does not match its Cleanroom version"
        )
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", value["world_type"]):
        raise WorldgenIterationError("profile world_type contains unsupported characters")
    for key in ("fixture", "plan"):
        raw_path = Path(value[key])
        if raw_path.is_absolute() or ".." in raw_path.parts:
            raise WorldgenIterationError(f"profile {key} must stay inside Workbench")

    resolved_paths: dict[str, Path] = {}
    for key, kind in (("fixture", "directory"), ("plan", "file")):
        declared = root / value[key]
        current = root
        for part in Path(value[key]).parts:
            current = current / part
            if current.is_symlink():
                raise WorldgenIterationError(
                    f"profile {key} cannot traverse a symlink: {current}"
                )
        resolved = (
            _require_directory(declared, "profile fixture")
            if kind == "directory"
            else _require_regular(declared, "profile plan")
        )
        try:
            resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise WorldgenIterationError(
                f"profile {key} must resolve inside Workbench"
            ) from exc
        resolved_paths[key] = resolved
    fixture = resolved_paths["fixture"]
    plan = resolved_paths["plan"]
    value["_path"] = str(path)
    value["_fixture"] = str(fixture)
    value["_plan"] = str(plan)
    return value


def parse_region(value: str) -> tuple[int, int, int, int]:
    fields = value.split(",")
    if len(fields) != 4:
        raise WorldgenIterationError(
            "region must be minChunkX,minChunkZ,widthChunks,heightChunks"
        )
    try:
        x, z, width, height = (int(field.strip()) for field in fields)
    except ValueError as exc:
        raise WorldgenIterationError("region fields must be integers") from exc
    if width < 1 or height < 1 or width > 64 or height > 64:
        raise WorldgenIterationError("region width and height must each be 1..64 chunks")
    if width * height > 1024:
        raise WorldgenIterationError("region may contain at most 1024 chunks")
    return x, z, width, height


def jar_mod_ids(path: Path) -> tuple[str, ...]:
    """Read Forge 1.12 mcmod.info IDs without loading the artifact."""

    try:
        with zipfile.ZipFile(path) as archive:
            try:
                raw = archive.read("mcmod.info")
            except KeyError:
                return ()
    except (OSError, zipfile.BadZipFile):
        return ()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return ()
    rows: list[Any]
    if isinstance(value, list):
        rows = value
    elif isinstance(value, dict):
        nested = value.get("modList")
        rows = nested if isinstance(nested, list) else [value]
    else:
        return ()
    return tuple(
        sorted(
            {
                mod_id
                for row in rows
                if isinstance(row, dict)
                and isinstance((mod_id := row.get("modid")), str)
                and mod_id
            }
        )
    )


def inventory_mods(runtime: Path) -> list[dict[str, Any]]:
    mods = runtime / "mods"
    if not mods.is_dir():
        return []
    return [
        {
            "file": str(path.relative_to(mods)),
            "mod_ids": list(jar_mod_ids(path)),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(mods.rglob("*.jar"))
        if path.is_file() and not path.is_symlink()
    ]


def _server_jar_glob(profile: Mapping[str, Any]) -> str:
    runtime = profile.get("runtime")
    if not isinstance(runtime, Mapping):
        raise WorldgenIterationError("profile runtime must be an object")
    server_glob = runtime.get("server_jar_glob")
    if not isinstance(server_glob, str) or not server_glob:
        raise WorldgenIterationError("profile runtime.server_jar_glob is invalid")
    return server_glob


def _runtime_matches(
    candidate: Path,
    profile: Mapping[str, Any],
    *,
    require_mod_ids: bool = True,
) -> bool:
    runtime = profile["runtime"]
    server_glob = _server_jar_glob(profile)
    tokens = runtime.get("required_mod_filename_tokens")
    if not isinstance(tokens, list) or not all(
        isinstance(token, str) and token for token in tokens
    ):
        raise WorldgenIterationError(
            "profile runtime.required_mod_filename_tokens is invalid"
        )
    if len(list(candidate.glob(server_glob))) != 1:
        return False
    mod_archives = list((candidate / "mods").rglob("*.jar"))
    names = [path.name.lower() for path in mod_archives]
    if not all(any(token.lower() in name for name in names) for token in tokens):
        return False
    required_mod_ids = runtime.get("required_mod_ids")
    if required_mod_ids is None or not require_mod_ids:
        return True
    if not isinstance(required_mod_ids, list) or not all(
        isinstance(mod_id, str) and mod_id for mod_id in required_mod_ids
    ):
        raise WorldgenIterationError("profile runtime.required_mod_ids is invalid")
    observed_mod_ids = {
        mod_id
        for archive in mod_archives
        if archive.is_file() and not archive.is_symlink()
        for mod_id in jar_mod_ids(archive)
    }
    return set(required_mod_ids).issubset(observed_mod_ids)


def discover_runtime_template(
    root: Path,
    profile: Mapping[str, Any],
    configured: Path | None = None,
) -> Path:
    if configured is not None:
        candidate = _require_directory(configured, "runtime template")
        if not _runtime_matches(candidate, profile, require_mod_ids=False):
            raise WorldgenIterationError(
                "configured runtime template does not match the profile's exact "
                "Cleanroom server and required mod filename constraints"
            )
        return candidate

    search_roots = profile["runtime"].get("discovery_roots")
    if not isinstance(search_roots, list) or not all(
        isinstance(item, str) and item for item in search_roots
    ):
        raise WorldgenIterationError("profile runtime.discovery_roots is invalid")
    candidates: list[Path] = []
    for raw_root in search_roots:
        search_root = (root / raw_root).resolve()
        if not search_root.is_dir():
            continue
        options = [search_root, *sorted(path for path in search_root.iterdir() if path.is_dir())]
        for option in options:
            if option.is_symlink():
                continue
            if _runtime_matches(option, profile):
                candidates.append(option.resolve())
    unique = sorted(set(candidates))
    if len(unique) != 1:
        detail = "none" if not unique else ", ".join(str(path) for path in unique)
        raise WorldgenIterationError(
            "runtime discovery requires exactly one matching template; found "
            f"{detail}. Pass --runtime-template or set "
            "WORKBENCH_WORLDGEN_RUNTIME_TEMPLATE."
        )
    return unique[0]


def _is_world_directory(path: Path) -> bool:
    return (path / "level.dat").is_file() or (path / "level.dat_old").is_file()


def _skip_top_level_reason(path: Path) -> str | None:
    if path.is_dir():
        if (
            path.name == "saves"
            or path.name in WORLD_TOP_LEVEL_DIRECTORIES
            or _is_world_directory(path)
        ):
            return "world_or_save_residue"
        if path.name in SKIP_TOP_LEVEL_DIRECTORIES or path.name.startswith(
            "retained-"
        ):
            return "stale_run_residue"
        return None
    lower_name = path.name.lower()
    if (
        path.suffix.lower() in SKIP_TOP_LEVEL_FILE_SUFFIXES
        or lower_name.endswith(".summary.json")
        or lower_name.endswith(".comparison.json")
        or lower_name.endswith(".console.txt")
    ):
        return "stale_run_residue"
    return None


def _skip_top_level(path: Path) -> bool:
    return _skip_top_level_reason(path) is not None


def _is_skippable_world_studio_plan_symlink(path: Path) -> bool:
    if not path.is_symlink() or path.suffix.lower() != ".groovy":
        return False
    try:
        target = path.resolve(strict=True)
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return target.is_file() and bool(PLAN_RE.search(text))


def _relative_template_path(path: Path, template: Path) -> str:
    return path.relative_to(template).as_posix()


def _audit_copyable_entry(
    path: Path,
    template: Path,
    *,
    files: list[Path],
    skipped_entries: list[dict[str, str]],
    unsafe_entries: list[dict[str, str]],
) -> None:
    relative = _relative_template_path(path, template)
    if path.is_symlink():
        if _is_skippable_world_studio_plan_symlink(path):
            skipped_entries.append(
                {"path": relative, "reason": "symlinked_world_studio_plan"}
            )
        else:
            unsafe_entries.append({"path": relative, "reason": "symlink"})
        return
    if path.is_dir():
        for child in sorted(path.iterdir()):
            _audit_copyable_entry(
                child,
                template,
                files=files,
                skipped_entries=skipped_entries,
                unsafe_entries=unsafe_entries,
            )
        return
    if path.is_file():
        files.append(path)
        return
    unsafe_entries.append({"path": relative, "reason": "unsupported_entry"})


def _file_binding(path: Path, template: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "relative_path": _relative_template_path(path, template),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _jar_archive_problem(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as archive:
            corrupt_member = archive.testzip()
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        return f"{type(exc).__name__}: {exc}"
    if corrupt_member is not None:
        return f"CRC check failed for {corrupt_member}"
    return None


def audit_runtime_template(
    template: Path,
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Inspect exactly what provisioning may copy without mutating the template."""

    template = _require_directory(template, "runtime template")
    files: list[Path] = []
    skipped_entries: list[dict[str, str]] = []
    unsafe_entries: list[dict[str, str]] = []
    for path in sorted(template.iterdir()):
        relative = _relative_template_path(path, template)
        if path.is_symlink():
            unsafe_entries.append(
                {"path": relative, "reason": "top_level_symlink"}
            )
            continue
        skip_reason = _skip_top_level_reason(path)
        if skip_reason is not None:
            skipped_entries.append({"path": relative, "reason": skip_reason})
            continue
        _audit_copyable_entry(
            path,
            template,
            files=files,
            skipped_entries=skipped_entries,
            unsafe_entries=unsafe_entries,
        )

    jar_inventory: list[dict[str, Any]] = []
    invalid_jars: list[dict[str, str]] = []
    for path in sorted(file for file in files if file.suffix.lower() == ".jar"):
        binding = _file_binding(path, template)
        binding["mod_ids"] = list(jar_mod_ids(path))
        if profile is None:
            binding["archive_state"] = "not-checked"
        else:
            archive_problem = _jar_archive_problem(path)
            binding["archive_state"] = (
                "valid" if archive_problem is None else "invalid"
            )
            if archive_problem is not None:
                invalid_jars.append(
                    {
                        "path": binding["relative_path"],
                        "detail": archive_problem,
                    }
                )
        jar_inventory.append(binding)

    mod_id_files: dict[str, list[str]] = {}
    for binding in jar_inventory:
        if not binding["relative_path"].startswith("mods/"):
            continue
        for mod_id in binding["mod_ids"]:
            mod_id_files.setdefault(mod_id, []).append(binding["relative_path"])
    duplicate_mod_ids = [
        {"mod_id": mod_id, "paths": sorted(paths)}
        for mod_id, paths in sorted(mod_id_files.items())
        if len(paths) > 1
    ]

    problems: list[dict[str, Any]] = []
    server_jar: dict[str, Any] | None = None
    server_jar_candidates: list[dict[str, Any]] = []
    server_glob: str | None = None
    if profile is not None:
        server_glob = _server_jar_glob(profile)
        copyable_files = set(files)
        candidates = sorted(
            path
            for path in template.glob(server_glob)
            if path in copyable_files and path.is_file() and not path.is_symlink()
        )
        server_jar_candidates = [
            _file_binding(path, template) for path in candidates
        ]
        if len(server_jar_candidates) == 1:
            server_jar = server_jar_candidates[0]
        else:
            problems.append(
                {
                    "kind": "server_jar_match_count",
                    "expected": 1,
                    "actual": len(server_jar_candidates),
                    "glob": server_glob,
                }
            )
    if duplicate_mod_ids:
        problems.append(
            {
                "kind": "duplicate_mod_ids",
                "mod_ids": [row["mod_id"] for row in duplicate_mod_ids],
            }
        )
    if invalid_jars:
        problems.append(
            {
                "kind": "invalid_jar_archives",
                "archives": invalid_jars,
            }
        )
    if profile is not None:
        runtime = profile.get("runtime")
        required_mod_ids = (
            runtime.get("required_mod_ids")
            if isinstance(runtime, Mapping)
            else None
        )
        if required_mod_ids is not None:
            observed_mod_ids = set(mod_id_files)
            missing_mod_ids = sorted(set(required_mod_ids) - observed_mod_ids)
            if missing_mod_ids:
                problems.append(
                    {
                        "kind": "missing_required_mod_ids",
                        "mod_ids": missing_mod_ids,
                    }
                )

    return {
        "template": str(template),
        "safe_to_provision": not unsafe_entries and not problems,
        "unsafe_entries": unsafe_entries,
        "skipped_entries": skipped_entries,
        "jar_inventory": jar_inventory,
        "duplicate_mod_ids": duplicate_mod_ids,
        "server_jar_glob": server_glob,
        "server_jar": server_jar,
        "server_jar_candidates": server_jar_candidates,
        "problems": problems,
    }


def _copy_entry(source: Path, destination: Path, counters: dict[str, int]) -> None:
    if source.is_symlink():
        if _is_skippable_world_studio_plan_symlink(source):
            counters["skipped_symlinked_world_studio_plans"] += 1
            return
        raise WorldgenIterationError(f"runtime template contains a symlink: {source}")
    if source.is_dir():
        destination.mkdir()
        for child in sorted(source.iterdir()):
            _copy_entry(child, destination / child.name, counters)
        shutil.copystat(source, destination, follow_symlinks=False)
        return
    if not source.is_file():
        raise WorldgenIterationError(f"unsupported runtime template entry: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    counters["copied_files"] += 1
    counters["copied_bytes"] += source.stat().st_size


def provision_runtime(
    template: Path,
    destination: Path,
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    template = _require_directory(template, "runtime template")
    audit = audit_runtime_template(template, profile)
    if not audit["safe_to_provision"]:
        raise WorldgenIterationError(
            "runtime template audit rejected provisioning: "
            + json.dumps(
                {
                    "unsafe_entries": audit["unsafe_entries"],
                    "problems": audit["problems"],
                },
                sort_keys=True,
            )
        )
    destination = destination.expanduser().resolve()
    try:
        destination.relative_to(template)
    except ValueError:
        pass
    else:
        raise WorldgenIterationError(
            f"disposable runtime cannot be created inside its template: {destination}"
        )
    if destination.exists():
        raise WorldgenIterationError(
            f"disposable runtime already exists; choose a fresh label: {destination}"
        )
    destination.mkdir(parents=True)
    counters = {
        "copied_files": 0,
        "copied_bytes": 0,
        "hardlinked_files": 0,
        "hardlinked_bytes": 0,
        "skipped_symlinked_world_studio_plans": 0,
    }
    skipped: list[str] = []
    try:
        for source in sorted(template.iterdir()):
            if source.is_symlink():
                raise WorldgenIterationError(
                    f"runtime template contains a top-level symlink: {source}"
                )
            if _skip_top_level(source):
                skipped.append(source.name)
                continue
            _copy_entry(source, destination / source.name, counters)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return {
        "template": str(template),
        "runtime": str(destination),
        "template_audit": audit,
        "skipped_top_level": skipped,
        **counters,
    }


def _read_properties(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!")):
            continue
        separator = "=" if "=" in stripped else ":" if ":" in stripped else None
        if separator is None:
            values[stripped] = ""
        else:
            key, value = stripped.split(separator, 1)
            values[key.strip()] = value.strip()
    return values


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def configure_runtime(
    runtime: Path,
    *,
    seed: int,
    world_type: str,
    server_port: int | None = None,
    level_name: str = "world",
    max_tick_time_ms: int | None = None,
) -> dict[str, Any]:
    runtime = _require_directory(runtime, "disposable runtime")
    if level_name in {".", ".."} or not re.fullmatch(
        r"[A-Za-z0-9_.-]{1,64}", level_name
    ):
        raise WorldgenIterationError("level name contains unsupported characters")
    world = runtime / level_name
    if world.exists():
        raise WorldgenIterationError(
            f"fresh-world invariant failed; level path already exists: {world}"
        )
    port = server_port or _free_local_port()
    if not 1 <= port <= 65535:
        raise WorldgenIterationError("server port must be in 1..65535")
    if max_tick_time_ms is not None and (
        not isinstance(max_tick_time_ms, int)
        or isinstance(max_tick_time_ms, bool)
        or (max_tick_time_ms != -1 and max_tick_time_ms < 1)
    ):
        raise WorldgenIterationError("max tick time must be -1 or a positive integer")
    properties_path = runtime / "server.properties"
    properties = _read_properties(properties_path)
    properties.update(
        {
            "enable-query": "false",
            "enable-rcon": "false",
            "generate-structures": "true",
            "generator-settings": "",
            "level-name": level_name,
            "level-seed": str(seed),
            "level-type": world_type,
            "online-mode": "false",
            "server-ip": "127.0.0.1",
            "server-port": str(port),
            "spawn-protection": "0",
        }
    )
    if max_tick_time_ms is not None:
        properties["max-tick-time"] = str(max_tick_time_ms)
    properties_path.write_text(
        "# Workbench disposable worldgen iteration\n"
        + "".join(f"{key}={properties[key]}\n" for key in sorted(properties)),
        encoding="utf-8",
    )
    (runtime / "eula.txt").write_text("eula=true\n", encoding="utf-8")
    result = {
        "level_name": level_name,
        "level_path": str(world),
        "seed": seed,
        "server_port": port,
        "world_type": world_type,
    }
    if max_tick_time_ms is not None:
        result["max_tick_time_ms"] = max_tick_time_ms
    return result


def _remove_replaceable_mods(runtime: Path, mod_id: str) -> list[str]:
    removed: list[str] = []
    mods = runtime / "mods"
    mods.mkdir(parents=True, exist_ok=True)
    for path in sorted(mods.rglob("*.jar")):
        if mod_id in jar_mod_ids(path) or path.name.startswith("strata-worldgen-observer-"):
            removed.append(path.name)
            path.unlink()
    return removed


def install_mod(runtime: Path, artifact: Path, mod_id: str) -> dict[str, Any]:
    artifact = _require_regular(artifact, "built worldgen artifact")
    ids = jar_mod_ids(artifact)
    if mod_id not in ids:
        raise WorldgenIterationError(
            f"built artifact does not declare expected mod ID {mod_id!r}: {artifact}"
        )
    removed = _remove_replaceable_mods(runtime, mod_id)
    destination = runtime / "mods" / artifact.name
    shutil.copy2(artifact, destination)
    return {
        "artifact": str(destination),
        "mod_id": mod_id,
        "removed": removed,
        "sha256": sha256_file(destination),
        "size_bytes": destination.stat().st_size,
    }


def freeze_world_studio_plan(runtime: Path, source: Path) -> dict[str, Any]:
    source = _require_regular(source, "World Studio Groovy plan")
    source_text = source.read_text(encoding="utf-8")
    if not PLAN_RE.search(source_text):
        raise WorldgenIterationError(
            f"selected Groovy plan does not configure mods.worldStudio: {source}"
        )
    groovy = runtime / "groovy"
    groovy.mkdir(parents=True, exist_ok=True)
    removed: list[str] = []
    for candidate in sorted(groovy.rglob("*.groovy")):
        if PLAN_RE.search(candidate.read_text(encoding="utf-8", errors="replace")):
            removed.append(str(candidate.relative_to(runtime)))
            candidate.unlink()
    destination = groovy / "postInit" / "workbench_world_studio_plan.groovy"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(source_text, encoding="utf-8")
    selected = [
        candidate
        for candidate in groovy.rglob("*.groovy")
        if PLAN_RE.search(candidate.read_text(encoding="utf-8", errors="replace"))
    ]
    if selected != [destination]:
        raise WorldgenIterationError(
            "single-plan invariant failed after freezing the selected Groovy plan"
        )
    return {
        "source": str(source),
        "installed": str(destination),
        "removed": removed,
        "sha256": sha256_file(destination),
    }


def find_built_artifact(root: Path, profile: Mapping[str, Any]) -> Path:
    artifact = profile["artifact"]
    pattern = artifact.get("glob")
    excludes = artifact.get("exclude_suffixes")
    if not isinstance(pattern, str) or not pattern:
        raise WorldgenIterationError("profile artifact.glob is invalid")
    if not isinstance(excludes, list) or not all(isinstance(item, str) for item in excludes):
        raise WorldgenIterationError("profile artifact.exclude_suffixes is invalid")
    matches = [
        path.resolve()
        for path in root.glob(pattern)
        if path.is_file() and not any(path.name.endswith(suffix) for suffix in excludes)
    ]
    if len(matches) != 1:
        raise WorldgenIterationError(
            "expected exactly one production-remapped worldgen artifact after build; "
            f"found {[str(path) for path in matches]}"
        )
    return matches[0]


def _executable(value: str, context: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.parent == Path(".") and len(candidate.parts) == 1:
        found = shutil.which(value)
        if not found:
            raise WorldgenIterationError(f"{context} executable is unavailable: {value}")
        candidate = Path(found)
    candidate = candidate.resolve()
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise WorldgenIterationError(f"{context} is not executable: {candidate}")
    return candidate


def discover_java(root: Path, configured: str | None = None) -> Path:
    if configured:
        return _executable(configured, "Java")
    environment = os.environ.get("WORKBENCH_CLEANROOM_JAVA")
    if environment:
        return _executable(environment, "Java")
    candidates = sorted((root / ".workbench/jdks").glob("**/bin/java"))
    executable = [path for path in candidates if path.is_file() and os.access(path, os.X_OK)]
    if len(executable) == 1:
        return executable[0].resolve()
    return _executable("java", "Java")


def _version_tuple(path: Path) -> tuple[int, ...]:
    match = re.search(r"gradle-([0-9]+(?:\.[0-9]+)*)", str(path))
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


def discover_gradle(
    fixture: Path,
    configured: str | None = None,
    preferred_version: str | None = None,
    *,
    allow_wrapper: bool = True,
) -> Path:
    if configured:
        return _executable(configured, "Gradle")
    environment = os.environ.get("WORKBENCH_GRADLE")
    if environment:
        return _executable(environment, "Gradle")
    wrapper = fixture / "gradlew"
    if allow_wrapper and wrapper.is_file() and os.access(wrapper, os.X_OK):
        return wrapper.resolve()
    on_path = shutil.which("gradle")
    if on_path:
        return Path(on_path).resolve()
    candidates = [
        path
        for path in Path.home().glob(
            ".gradle/wrapper/dists/gradle-*-bin/*/gradle-*/bin/gradle"
        )
        if path.is_file() and os.access(path, os.X_OK)
    ]
    if not candidates:
        raise WorldgenIterationError(
            "Gradle is unavailable. Install a version supported by the selected "
            "Cleanroom fixture or pass --gradle-cmd; the distribution belongs in "
            "ignored tool storage, not the repository."
        )
    preferred = (
        [
            path
            for path in candidates
            if _version_tuple(path)
            == _version_tuple(Path(f"gradle-{preferred_version}"))
        ]
        if preferred_version
        else []
    )
    return max(preferred or candidates, key=_version_tuple).resolve()


def executable_identity(
    executable: Path,
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [str(executable), *arguments],
            check=False,
            capture_output=True,
            text=True,
            env=dict(environment) if environment is not None else None,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorldgenIterationError(
            f"timed out identifying executable {executable} after {timeout_seconds:g}s"
        ) from exc
    output = (completed.stdout + completed.stderr).strip()
    if completed.returncode:
        raise WorldgenIterationError(
            f"cannot identify executable {executable}: {output or completed.returncode}"
        )
    return {
        "path": str(executable),
        "sha256": sha256_file(executable),
        "version_output": output,
    }


def java_version(identity: Mapping[str, Any]) -> str:
    output = str(identity.get("version_output", ""))
    match = re.search(
        r'(?:openjdk|java) version "([^"]+)"', output, re.IGNORECASE
    )
    if not match:
        raise WorldgenIterationError("could not parse the selected Java version")
    return match.group(1)


def java_major(identity: Mapping[str, Any]) -> int:
    version = java_version(identity)
    match = re.match(r"([0-9]+)", version)
    if not match:
        raise WorldgenIterationError("could not parse the selected Java major version")
    major = int(match.group(1))
    return 8 if major == 1 and version.startswith("1.8") else major


def gradle_version(identity: Mapping[str, Any]) -> str:
    output = str(identity.get("version_output", ""))
    match = re.search(r"(?m)^Gradle\s+([^\s]+)\s*$", output)
    if not match:
        raise WorldgenIterationError("could not parse the selected Gradle version")
    return match.group(1)


@dataclass
class IterationReport:
    """Incrementally persist stage state so a failed command remains diagnosable."""

    path: Path
    root: Path
    value: dict[str, Any]

    @classmethod
    def start(
        cls,
        path: Path,
        root: Path,
        *,
        label: str,
        profile: str,
        mode: str,
        argv: Sequence[str],
    ) -> "IterationReport":
        if not LABEL_RE.fullmatch(label):
            raise WorldgenIterationError(
                "label must match [A-Za-z0-9][A-Za-z0-9_.-]{0,95}"
            )
        report = cls(
            path=path,
            root=root,
            value={
                "format": FORMAT,
                "schema_version": 1,
                "label": label,
                "profile": profile,
                "mode": mode,
                "status": "running",
                "started_at": _utc_now(),
                "completed_at": None,
                "invocation": list(argv),
                "reproduction_command": None,
                "inputs": {},
                "outputs": {},
                "stages": [],
                "failure": None,
            },
        )
        report.write()
        return report

    def write(self) -> None:
        _write_json(self.path, self.value)

    def set_input(self, key: str, value: Any) -> None:
        self.value["inputs"][key] = value
        self.write()

    def set_output(self, key: str, value: Any) -> None:
        self.value["outputs"][key] = value
        self.write()

    @contextmanager
    def stage(self, stage_id: str, purpose: str) -> Iterator[dict[str, Any]]:
        stage: dict[str, Any] = {
            "id": stage_id,
            "purpose": purpose,
            "status": "running",
            "started_at": _utc_now(),
            "completed_at": None,
            "duration_seconds": None,
            "details": {},
            "error": None,
        }
        self.value["stages"].append(stage)
        self.write()
        start = time.monotonic()
        try:
            yield stage["details"]
        except Exception as exc:
            stage["status"] = "failed"
            stage["error"] = f"{type(exc).__name__}: {exc}"
            stage["completed_at"] = _utc_now()
            stage["duration_seconds"] = round(time.monotonic() - start, 6)
            self.value["status"] = "failed"
            self.value["failure"] = {
                "stage": stage_id,
                "message": str(exc),
                "type": type(exc).__name__,
            }
            self.value["completed_at"] = _utc_now()
            self.write()
            raise
        else:
            stage["status"] = "complete"
            stage["completed_at"] = _utc_now()
            stage["duration_seconds"] = round(time.monotonic() - start, 6)
            self.write()

    def complete(self) -> None:
        self.value["status"] = "complete"
        self.value["completed_at"] = _utc_now()
        self.write()


__all__ = [
    "FORMAT",
    "PROFILE_FORMAT",
    "IterationReport",
    "WorldgenIterationError",
    "audit_runtime_template",
    "configure_runtime",
    "discover_gradle",
    "discover_java",
    "discover_runtime_template",
    "executable_identity",
    "find_built_artifact",
    "freeze_world_studio_plan",
    "gradle_version",
    "install_mod",
    "inventory_mods",
    "jar_mod_ids",
    "java_major",
    "java_version",
    "load_profile",
    "parse_region",
    "provision_runtime",
    "resolve_profile_path",
    "sha256_file",
]
