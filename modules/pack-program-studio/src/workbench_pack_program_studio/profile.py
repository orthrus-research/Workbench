"""Versioned platform and pack-profile loading for Groovy program analysis."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
from typing import Any, Mapping
import unicodedata

from workbench_api.source_locations import (
    MAX_PORTABLE_PATH_BYTES,
    SourceLocationError,
    portable_path_component,
    portable_relative_path as _api_portable_relative_path,
)

from .model import PackProgramError, sha256_bytes


PACK_PROFILE_FORMAT = "workbench-groovy-pack-profile-v1"
PLATFORM_PROFILE_FORMAT = "workbench-groovyscript-platform-profile-v1"
MAX_PROFILE_BYTES = 2 * 1024 * 1024
MAX_RELATIVE_PARENT_SEGMENTS = 32


@dataclass(frozen=True, slots=True)
class LoadedProfile:
    path: Path
    sha256: str
    value: Mapping[str, Any]
    platform_path: Path
    platform_sha256: str
    platform: Mapping[str, Any]

    @property
    def profile_id(self) -> str:
        return str(self.value["profile_id"])

    @property
    def pack_profile_id(self) -> str:
        return str(self.value["pack_profile_id"])

    @property
    def platform_profile_id(self) -> str:
        return str(self.platform["profile_id"])


class _DuplicateKey(ValueError):
    pass


def _windows_extended_path_text(value: str) -> str:
    if value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def _windows_display_path_text(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[len("\\\\?\\UNC\\") :]
    if value.startswith("\\\\?\\"):
        return value[len("\\\\?\\") :]
    return value


def native_filesystem_path(path: Path) -> Path:
    """Return an absolute Windows path that is not subject to ``MAX_PATH``."""

    if os.name != "nt":
        return path
    absolute = os.path.abspath(os.fspath(path)).replace("/", "\\")
    return Path(_windows_extended_path_text(absolute))


def display_filesystem_path(path: Path) -> str:
    """Render a native path without an internal Windows extended prefix."""

    value = os.fspath(path)
    return _windows_display_path_text(value) if os.name == "nt" else value


def portable_relative_path(
    value: Any, context: str, *, allow_directory_marker: bool = False
) -> PurePosixPath:
    """Compatibility facade for the shared portable source-path contract."""
    try:
        return _api_portable_relative_path(
            value, context, allow_directory_marker=allow_directory_marker
        )
    except SourceLocationError as exc:
        raise PackProgramError(str(exc)) from exc


def portable_relative_reference(value: Any, context: str) -> PurePosixPath:
    """Admit a portable file reference with canonical leading parent steps."""

    if not isinstance(value, str) or not value:
        raise PackProgramError(f"{context} must be a canonical portable relative reference")
    try:
        raw = value.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise PackProgramError(
            f"{context} must be a canonical portable relative reference"
        ) from exc
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    parts = tuple(value.split("/"))
    parent_count = 0
    while parent_count < len(parts) and parts[parent_count] == "..":
        parent_count += 1
    if (
        len(raw) > MAX_PORTABLE_PATH_BYTES
        or unicodedata.normalize("NFC", value) != value
        or "\x00" in value
        or "\\" in value
        or posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or any(part in {"", "."} for part in parts)
        or parent_count > MAX_RELATIVE_PARENT_SEGMENTS
        or parent_count == len(parts)
        or any(part == ".." for part in parts[parent_count:])
        or value != "/".join(parts)
        or any(not _portable_component(part) for part in parts[parent_count:])
    ):
        raise PackProgramError(f"{context} must be a canonical portable relative reference")
    return posix


def _portable_reference_path(
    root: Path, reference: PurePosixPath, context: str
) -> Path:
    """Resolve a validated reference with host-independent source spelling."""

    cursor = root
    for part in reference.parts:
        if part == "..":
            cursor = cursor.parent
            continue
        parent = cursor
        cursor = parent / part
        try:
            inspected = cursor.lstat()
            directory_entries = os.listdir(parent)
        except OSError as exc:
            raise PackProgramError(f"cannot inspect {context} {cursor}: {exc}") from exc
        if part not in directory_entries:
            raise PackProgramError(
                f"{context} does not use the source tree's exact path spelling: "
                f"{cursor}"
            )
        junction = getattr(cursor, "is_junction", None)
        try:
            is_junction = bool(junction()) if callable(junction) else False
        except OSError as exc:
            raise PackProgramError(f"cannot inspect {context} {cursor}: {exc}") from exc
        if stat.S_ISLNK(inspected.st_mode) or is_junction:
            raise PackProgramError(f"{context} traverses a filesystem link: {cursor}")
    return cursor


def _portable_component(part: str) -> bool:
    return portable_path_component(part)


def _pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        if key in result:
            raise _DuplicateKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def safe_regular_bytes(path: Path, *, maximum: int = MAX_PROFILE_BYTES) -> bytes:
    requested = native_filesystem_path(path.expanduser())
    try:
        before = requested.lstat()
    except OSError as exc:
        raise PackProgramError(f"cannot inspect file {requested}: {exc}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise PackProgramError(f"path is not a regular non-symlink file: {requested}")
    if before.st_size > maximum:
        raise PackProgramError(f"file exceeds {maximum} bytes: {requested}")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(requested, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
        ):
            raise PackProgramError(f"file changed identity while opening: {requested}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise PackProgramError(f"file exceeds {maximum} bytes: {requested}")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
        ):
            raise PackProgramError(f"file changed while reading: {requested}")
        return b"".join(chunks)
    except OSError as exc:
        raise PackProgramError(f"cannot read file {requested}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def strict_json_file(path: Path, *, maximum: int = MAX_PROFILE_BYTES) -> tuple[Any, bytes]:
    raw = safe_regular_bytes(path, maximum=maximum)
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number {item}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError, _DuplicateKey, ValueError) as exc:
        raise PackProgramError(f"malformed JSON in {path}: {exc}") from exc
    return value, raw


def resolve_named_profile(root: Path, name: str) -> Path:
    if name != "supersymmetry":
        raise PackProgramError(
            f"unknown Groovy pack-program profile {name!r}; use --profile-file for an explicit adapter"
        )
    return root / "profiles/packs/supersymmetry/groovy/groovy-program-profile-v1.json"


def load_profile(path: Path) -> LoadedProfile:
    requested = native_filesystem_path(path.expanduser()).resolve()
    value, raw = strict_json_file(requested)
    if not isinstance(value, dict):
        raise PackProgramError("Groovy pack profile must be an object")
    _validate_pack_profile(value)
    platform_reference = portable_relative_reference(
        value["platform_profile"], "Groovy platform profile reference"
    )
    # Leading parent traversal is useful for checked-in profiles, but the
    # resolved target must remain within the shared `profiles` ancestor below.
    platform_path = _portable_reference_path(
        requested.parent,
        platform_reference,
        "Groovy platform profile reference",
    ).resolve()
    profile_tree = next(
        (parent for parent in requested.parents if parent.name == "profiles"),
        None,
    )
    if profile_tree is not None and platform_path != profile_tree and profile_tree not in platform_path.parents:
        raise PackProgramError("Groovy platform profile escapes the profiles tree")
    platform, platform_raw = strict_json_file(platform_path)
    if not isinstance(platform, dict):
        raise PackProgramError("GroovyScript platform profile must be an object")
    _validate_platform_profile(platform)
    return LoadedProfile(
        path=requested,
        sha256=sha256_bytes(raw),
        value=value,
        platform_path=platform_path,
        platform_sha256=sha256_bytes(platform_raw),
        platform=platform,
    )


def _validate_pack_profile(value: Mapping[str, Any]) -> None:
    required = {
        "format",
        "schema_version",
        "profile_id",
        "pack_profile_id",
        "platform_profile",
        "source_layout",
        "expected_run_config",
        "rules",
        "identity_policies",
        "limitations",
    }
    if set(value) != required:
        raise PackProgramError("Groovy pack profile has unexpected or missing keys")
    if value["format"] != PACK_PROFILE_FORMAT or value["schema_version"] != 1:
        raise PackProgramError("unsupported Groovy pack profile format")
    for key in ("profile_id", "pack_profile_id", "platform_profile"):
        if not isinstance(value[key], str) or not value[key]:
            raise PackProgramError(f"Groovy pack profile {key} must be text")
    portable_relative_reference(
        value["platform_profile"], "Groovy platform profile reference"
    )
    layout = value["source_layout"]
    if not isinstance(layout, dict) or set(layout) != {
        "groovy_root",
        "run_config",
        "max_files",
        "max_file_bytes",
        "max_total_bytes",
    }:
        raise PackProgramError("Groovy pack source layout is malformed")
    for key in ("groovy_root", "run_config"):
        portable_relative_path(layout[key], f"Groovy source layout {key}")
    for key in ("max_files", "max_file_bytes", "max_total_bytes"):
        if isinstance(layout[key], bool) or not isinstance(layout[key], int) or layout[key] <= 0:
            raise PackProgramError(f"Groovy source layout {key} must be positive")
    if layout["max_files"] > 100_000 or layout["max_total_bytes"] > 1024 * 1024 * 1024:
        raise PackProgramError("Groovy source layout bounds are excessive")
    expected = value["expected_run_config"]
    if not isinstance(expected, dict) or set(expected) != {"pack_name", "pack_id"}:
        raise PackProgramError("expected Groovy runConfig identity is malformed")
    rules = value["rules"]
    if not isinstance(rules, list) or not rules:
        raise PackProgramError("Groovy pack profile must declare analysis rules")
    rule_ids: set[str] = set()
    for rule in rules:
        _validate_rule(rule)
        if rule["rule_id"] in rule_ids:
            raise PackProgramError(f"duplicate Groovy rule ID {rule['rule_id']}")
        rule_ids.add(rule["rule_id"])
    policies = value["identity_policies"]
    if not isinstance(policies, list):
        raise PackProgramError("Groovy identity policies must be a list")
    policy_ids: set[str] = set()
    for policy in policies:
        if not isinstance(policy, dict) or set(policy) != {
            "policy_id",
            "rule_id",
            "field",
            "identity_kind",
            "disposition",
            "save_risk",
        }:
            raise PackProgramError("Groovy identity policy is malformed")
        if policy["rule_id"] not in rule_ids:
            raise PackProgramError("Groovy identity policy references an unknown rule")
        if policy["disposition"] not in {"review", "reject"}:
            raise PackProgramError("Groovy identity policy disposition is unsupported")
        if policy["policy_id"] in policy_ids:
            raise PackProgramError("duplicate Groovy identity policy ID")
        policy_ids.add(policy["policy_id"])
    if not isinstance(value["limitations"], list) or any(
        not isinstance(item, str) or not item for item in value["limitations"]
    ):
        raise PackProgramError("Groovy pack profile limitations are malformed")


def _validate_rule(rule: Any) -> None:
    if not isinstance(rule, dict):
        raise PackProgramError("Groovy analysis rule must be an object")
    required = {
        "rule_id",
        "category",
        "kind",
        "operation",
        "match",
        "captures",
        "identity_fields",
        "reload",
        "description",
    }
    if set(rule) != required:
        raise PackProgramError("Groovy analysis rule has unexpected or missing keys")
    for key in ("rule_id", "category", "kind", "operation", "description"):
        if not isinstance(rule[key], str) or not rule[key]:
            raise PackProgramError(f"Groovy analysis rule {key} must be text")
    if rule["reload"] not in {"stage", "unknown", "restart-required"}:
        raise PackProgramError(f"Groovy analysis rule {rule['rule_id']} has invalid reload policy")
    match = rule["match"]
    if not isinstance(match, dict) or not match:
        raise PackProgramError("Groovy analysis rule match must be an object")
    allowed_match = {
        "callee",
        "callee_prefix",
        "exclude_callee_prefix",
        "terminal",
        "terminal_regex",
        "constructor",
        "path_regex",
        "token",
    }
    if not set(match) <= allowed_match:
        raise PackProgramError("Groovy analysis rule match has unknown fields")
    if "token" in match and len(match) != 1 and set(match) != {"token", "path_regex"}:
        raise PackProgramError("token rules may only add a path constraint")
    for key in ("terminal_regex", "path_regex"):
        if key in match:
            try:
                re.compile(str(match[key]))
            except re.error as exc:
                raise PackProgramError(f"invalid {key} in Groovy analysis rule: {exc}") from exc
    captures = rule["captures"]
    if not isinstance(captures, list):
        raise PackProgramError("Groovy rule captures must be a list")
    fields: set[str] = set()
    for capture in captures:
        if not isinstance(capture, dict) or set(capture) != {"field", "argument", "mode"}:
            raise PackProgramError("Groovy rule capture is malformed")
        if capture["mode"] not in {"integer", "string", "first-string", "wrapped-string", "expression", "qualifier", "terminal"}:
            raise PackProgramError("Groovy rule capture mode is unsupported")
        if capture["mode"] in {"qualifier", "terminal"}:
            if capture["argument"] is not None:
                raise PackProgramError("qualifier/terminal captures do not take an argument")
        elif isinstance(capture["argument"], bool) or not isinstance(capture["argument"], int) or capture["argument"] < 0:
            raise PackProgramError("Groovy rule capture argument must be non-negative")
        if capture["field"] in fields:
            raise PackProgramError("duplicate Groovy rule capture field")
        fields.add(capture["field"])
    identities = rule["identity_fields"]
    if not isinstance(identities, list) or not set(identities) <= fields:
        raise PackProgramError("Groovy rule identity fields must name captures")


def _validate_platform_profile(value: Mapping[str, Any]) -> None:
    required = {
        "format",
        "schema_version",
        "profile_id",
        "platform_profile_id",
        "groovyscript",
        "load_stages",
        "preprocessors",
        "class_cache",
        "limitations",
    }
    if set(value) != required:
        raise PackProgramError("GroovyScript platform profile has unexpected or missing keys")
    if value["format"] != PLATFORM_PROFILE_FORMAT or value["schema_version"] != 1:
        raise PackProgramError("unsupported GroovyScript platform profile format")
    stages = value["load_stages"]
    if not isinstance(stages, dict) or not stages:
        raise PackProgramError("GroovyScript platform profile has no load stages")
    orders: set[int] = set()
    for stage, policy in stages.items():
        if not isinstance(stage, str) or not isinstance(policy, dict) or set(policy) != {
            "order",
            "reload",
            "roles",
        }:
            raise PackProgramError("GroovyScript load-stage policy is malformed")
        if policy["reload"] not in {"reload-candidate", "restart-required"}:
            raise PackProgramError("GroovyScript load-stage reload state is unsupported")
        if isinstance(policy["order"], bool) or not isinstance(policy["order"], int):
            raise PackProgramError("GroovyScript load-stage order must be an integer")
        if policy["order"] in orders:
            raise PackProgramError("GroovyScript load-stage order is duplicate")
        orders.add(policy["order"])
    preprocessors = value["preprocessors"]
    if not isinstance(preprocessors, list) or any(
        item not in {"no_run", "debug_only", "no_reload", "mods_loaded", "side", "packmode"}
        for item in preprocessors
    ):
        raise PackProgramError("GroovyScript preprocessor list is malformed")


__all__ = [
    "LoadedProfile",
    "PACK_PROFILE_FORMAT",
    "PLATFORM_PROFILE_FORMAT",
    "display_filesystem_path",
    "load_profile",
    "native_filesystem_path",
    "portable_relative_path",
    "portable_relative_reference",
    "resolve_named_profile",
    "safe_regular_bytes",
    "strict_json_file",
]
