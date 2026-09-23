"""Bounded static inventory for Runtime Explorer source-side navigation.

This scanner records declarations and literal identities available from exact
workspace files.  It deliberately describes them as declared or statically
possible; it never claims that Minecraft loaded, registered, transformed, or
executed them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

from .workspace_doctor import new_report


FORMAT_VERSION = "workbench-project-intelligence-runtime-surface-v1"
SCHEMA_VERSION = 1
MAX_FILES = 25_000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_DECLARATIONS = 150_000
MAX_JSON_DEPTH = 64
MAX_JSON_VALUES = 100_000
MAX_DISCOVERY_DIRECTORIES = 100_000

_TEXT_SUFFIXES = frozenset(
    {
        ".cfg",
        ".conf",
        ".gradle",
        ".groovy",
        ".java",
        ".json",
        ".json5",
        ".kt",
        ".kts",
        ".lang",
        ".mcmeta",
        ".properties",
        ".toml",
        ".xml",
        ".yaml",
        ".yml",
        ".zs",
    }
)
_SOURCE_SUFFIXES = frozenset({".java", ".groovy", ".kt", ".kts", ".zs"})
_CONFIG_SUFFIXES = frozenset(
    {".cfg", ".conf", ".json", ".json5", ".properties", ".toml", ".yaml", ".yml"}
)
_IDENTIFIER_RE = re.compile(r"^[^\r\n\x00]{1,8192}$")
_MOD_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_MINECRAFT_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])([a-z0-9_.-]+:[a-z0-9_./-]+)(?![A-Za-z0-9_./-])"
)
_PACKAGE_RE = re.compile(
    r"^\s*package\s+([A-Za-z_$][A-Za-z0-9_$.]*)\s*;?\s*$"
)
_TYPE_RE = re.compile(
    r"^\s*(?:public\s+|protected\s+|private\s+|abstract\s+|final\s+|"
    r"static\s+|strictfp\s+|sealed\s+|non-sealed\s+)*"
    r"(@interface|class|interface|enum|record)\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)
_METHOD_RE = re.compile(
    r"^\s*(?:(?:public|protected|private|static|final|abstract|synchronized|"
    r"native|strictfp|default)\s+)*(?:<[^>{};]+>\s*)?"
    r"((?:[A-Za-z_$][A-Za-z0-9_$.]*|void)(?:\s*<[^;{}()]+>)?(?:\[\])*)\s+"
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*\(([^;{}]*)\)"
)
_CONSTRUCTOR_RE = re.compile(
    r"^\s*(?:(?:public|protected|private)\s+)?"
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*\(([^;{}]*)\)"
)
_GROOVY_METHOD_RE = re.compile(
    r"^\s*(?:(?:public|protected|private|static|final|synchronized)\s+)*"
    r"def\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(([^)]*)\)"
)
_FIELD_RE = re.compile(
    r"^\s*(?:(?:public|protected|private|static|final|volatile|transient)\s+)+"
    r"([A-Za-z_$][A-Za-z0-9_$<>,.?\[\] ]*)\s+"
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*(?:=|;)"
)
_MOD_RE = re.compile(
    r"@Mod\s*\([^)]*?(?:modid\s*=\s*)?[\"']([a-z0-9_.-]+)[\"']",
    re.DOTALL,
)
_MIXIN_RE = re.compile(r"@Mixin\s*\((.*?)\)", re.DOTALL)
_CLASS_LITERAL_RE = re.compile(
    r"([A-Za-z_$][A-Za-z0-9_$.]*)\s*\.\s*class"
)
_QUOTED_CLASS_RE = re.compile(
    r"[\"']([A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+)[\"']"
)
_ASSIGNMENT_RE = re.compile(
    r"^\s*(?:[BIDS]\s*:)?([A-Za-z0-9_.-]{1,256})\s*[=:]"
)
_SECTION_RE = re.compile(r"^\s*\[([^\]\r\n]{1,256})\]\s*$")
_CONTROL_NAMES = frozenset(
    {"if", "for", "while", "switch", "catch", "return", "throw", "new"}
)
_KNOWN_IDENTITY_FIELDS: dict[str, str] = {
    "modid": "mod-id",
    "mod_id": "mod-id",
    "registryname": "registry-name",
    "registry_name": "registry-name",
    "resourcelocation": "resource-location",
    "resource_location": "resource-location",
    "class": "class-name",
    "class_name": "class-name",
    "targetclass": "class-name",
    "target_class": "class-name",
    "method": "method-name",
    "method_name": "method-name",
    "config": "config-key",
    "config_key": "config-key",
    "recipe": "recipe-id",
    "recipe_id": "recipe-id",
    "block": "block-id",
    "block_id": "block-id",
    "block_state": "blockstate-id",
    "blockstate": "blockstate-id",
    "blockstate_id": "blockstate-id",
    "item": "item-id",
    "item_id": "item-id",
    "item_stack": "item-stack",
    "stack": "item-stack",
    "metadata": "metadata-value",
    "metadata_value": "metadata-value",
    "loot_table": "loot-table-id",
    "loot_table_id": "loot-table-id",
    "biome": "biome-id",
    "biome_id": "biome-id",
    "dimension": "dimension-id",
    "dimension_id": "dimension-id",
    "structure": "structure-id",
    "structure_id": "structure-id",
    "generator": "generator-id",
    "generator_id": "generator-id",
    "machine": "machine-id",
    "machine_id": "machine-id",
    "fluid": "fluid-id",
    "fluid_id": "fluid-id",
    "material": "material-id",
    "material_id": "material-id",
    "oreprefix": "ore-prefix",
    "ore_prefix": "ore-prefix",
    "world_type": "world-type-id",
    "world_type_id": "world-type-id",
    "event": "event-name",
    "event_name": "event-name",
    "event_id": "event-id",
    "capability": "capability-name",
    "capability_name": "capability-name",
    "groovy_key": "groovy-key",
    "profiler": "profiler-event",
    "profiler_event": "profiler-event",
}
_DISCOVERY_SKIP_DIRECTORIES = frozenset(
    {
        ".deconstruction",
        ".git",
        ".gradle",
        ".idea",
        ".venv",
        ".workbench",
        "__pycache__",
        "build",
        "node_modules",
        "out",
        "target",
    }
)


class RuntimeSurfaceError(RuntimeError):
    """The static runtime surface cannot be inventoried safely."""


class _DuplicateJsonKey(ValueError):
    pass


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _single_line(value: object, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise RuntimeSurfaceError(f"{label} must be bounded single-line text")
    return value


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value}")


def _strict_json(text: str) -> Any:
    return json.loads(
        text,
        object_pairs_hook=_json_object,
        parse_constant=_reject_constant,
    )


def _identity(kind: str, value: str, basis: str) -> dict[str, str]:
    return {"kind": kind, "value": value, "basis": basis}


def _dedupe_dicts(values: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {_canonical_bytes(value): value for value in values}
    return [unique[key] for key in sorted(unique)]


def _entry(
    *,
    kind: str,
    name: str,
    path: str,
    line: int,
    file_sha256: str,
    language: str,
    identities: Iterable[dict[str, str]],
    owner_candidates: Iterable[dict[str, Any]] = (),
    relationships: Iterable[dict[str, Any]] = (),
    attributes: Mapping[str, Any] | None = None,
    state: str = "declared",
) -> dict[str, Any]:
    normalized_identities = _dedupe_dicts(identities)
    seed = {
        "file_sha256": file_sha256,
        "kind": kind,
        "line": line,
        "name": name,
        "path": path,
    }
    return {
        "declaration_id": "workbench-runtime-declaration:sha256:" + _digest(seed),
        "kind": kind,
        "name": name,
        "state": state,
        "identities": normalized_identities,
        "owner_candidates": _dedupe_dicts(owner_candidates),
        "declaration": {
            "path": path,
            "line": line,
            "file_sha256": file_sha256,
            "language": language,
        },
        "relationships": _dedupe_dicts(relationships),
        "attributes": dict(attributes or {}),
    }


def _owner_rows(mods: Iterable[Mapping[str, Any]], *, basis: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for mod in mods:
        mod_id = mod.get("mod_id")
        if not isinstance(mod_id, str) or not mod_id:
            continue
        result.append(
            {
                "mod_id": mod_id,
                "version": mod.get("version") if isinstance(mod.get("version"), str) else None,
                "state": "declared",
                "basis": basis,
            }
        )
    return _dedupe_dicts(result)


def _read_mcmod_bytes(raw: bytes, descriptor: str) -> list[dict[str, Any]]:
    try:
        value = _strict_json(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError):
        return []
    if isinstance(value, dict) and isinstance(value.get("modList"), list):
        rows = value["modList"]
    elif isinstance(value, list):
        rows = value
    elif isinstance(value, dict):
        rows = [value]
    else:
        rows = []
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        mod_id = row.get("modid")
        if not isinstance(mod_id, str) or not _MOD_ID_RE.fullmatch(mod_id):
            continue
        result.append(
            {
                "mod_id": mod_id,
                "name": row.get("name") if isinstance(row.get("name"), str) else None,
                "version": row.get("version") if isinstance(row.get("version"), str) else None,
                "descriptor": descriptor,
            }
        )
    return result


def _module_prefix(relative: str) -> tuple[str, ...]:
    parts = PurePosixPath(relative).parts
    try:
        index = parts.index("src")
    except ValueError:
        return ()
    return tuple(parts[:index])


def _owners_for_relative(
    relative: str,
    module_mods: Mapping[tuple[str, ...], list[dict[str, Any]]],
    default_owners: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    parts = PurePosixPath(relative).parts
    candidates = [
        (prefix, mods)
        for prefix, mods in module_mods.items()
        if len(prefix) <= len(parts) and tuple(parts[: len(prefix)]) == prefix
    ]
    if not candidates:
        return default_owners
    prefix, mods = max(candidates, key=lambda row: len(row[0]))
    rendered_prefix = PurePosixPath(*prefix).as_posix() if prefix else "."
    return _owner_rows(
        mods, basis=f"module mod descriptor under {rendered_prefix}"
    )


def _code_only_lines(text: str) -> list[str]:
    """Mask comments and literals while preserving declaration line numbers."""

    output: list[str] = []
    state = "code"
    escaped = False
    index = 0
    while index < len(text):
        character = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if state == "code":
            if character == "/" and following == "/":
                output.extend((" ", " "))
                index += 2
                state = "line-comment"
                continue
            if character == "/" and following == "*":
                output.extend((" ", " "))
                index += 2
                state = "block-comment"
                continue
            if character in {"\"", "'"}:
                output.append(" ")
                state = "string" if character == "\"" else "character"
                escaped = False
                index += 1
                continue
            output.append(character)
            index += 1
            continue
        if state == "line-comment":
            if character in "\r\n":
                output.append(character)
                state = "code"
            else:
                output.append(" ")
            index += 1
            continue
        if state == "block-comment":
            if character == "*" and following == "/":
                output.extend((" ", " "))
                index += 2
                state = "code"
            else:
                output.append(character if character in "\r\n" else " ")
                index += 1
            continue
        if character in "\r\n":
            # Preserve malformed or Groovy multiline strings conservatively.
            output.append(character)
            escaped = False
            index += 1
            continue
        quote = "\"" if state == "string" else "'"
        if escaped:
            output.append(" ")
            escaped = False
        elif character == "\\":
            output.append(" ")
            escaped = True
        elif character == quote:
            output.append(" ")
            state = "code"
        else:
            output.append(" ")
        index += 1
    masked = "".join(output).splitlines()
    lines = text.splitlines()
    if len(masked) < len(lines):
        masked.extend("" for _ in range(len(lines) - len(masked)))
    return masked


def _source_entries(
    text: str,
    *,
    path: str,
    file_sha256: str,
    language: str,
    default_owners: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    lines = text.splitlines()
    code_lines = _code_only_lines(text)
    package = ""
    for line in lines[:200]:
        match = _PACKAGE_RE.match(line)
        if match:
            package = match.group(1)
            break
    explicit_mod_ids = sorted(set(_MOD_RE.findall(text)))
    explicit_owners = [
        {"mod_id": mod_id, "version": None, "state": "declared", "basis": "@Mod"}
        for mod_id in explicit_mod_ids
    ]
    owners = explicit_owners or default_owners
    entries: list[dict[str, Any]] = []
    current_type: str | None = None
    pending_mixin_targets: list[str] = []
    for annotation in _MIXIN_RE.findall(text):
        pending_mixin_targets.extend(_CLASS_LITERAL_RE.findall(annotation))
        pending_mixin_targets.extend(_QUOTED_CLASS_RE.findall(annotation))
    mixin_emitted = False
    for line_number, line in enumerate(lines, 1):
        code_line = code_lines[line_number - 1]
        type_match = _TYPE_RE.search(code_line)
        if type_match:
            simple_name = type_match.group(2)
            qualified = f"{package}.{simple_name}" if package else simple_name
            current_type = qualified
            entries.append(
                _entry(
                    kind="class",
                    name=qualified,
                    path=path,
                    line=line_number,
                    file_sha256=file_sha256,
                    language=language,
                    identities=(
                        _identity("class-name", qualified, "source-declaration"),
                        _identity("class-simple-name", simple_name, "source-declaration"),
                    ),
                    owner_candidates=owners,
                    attributes={"declaration_kind": type_match.group(1)},
                )
            )
            if pending_mixin_targets and not mixin_emitted:
                identities = [
                    _identity("mixin-class", qualified, "@Mixin declaration")
                ]
                identities.extend(
                    _identity("target-class", target, "@Mixin target")
                    for target in pending_mixin_targets
                )
                entries.append(
                    _entry(
                        kind="mixin",
                        name=qualified,
                        path=path,
                        line=line_number,
                        file_sha256=file_sha256,
                        language=language,
                        identities=identities,
                        owner_candidates=owners,
                        relationships=(
                            {
                                "predicate": "targets-static",
                                "target": {"kind": "class-name", "value": target},
                                "basis": "@Mixin declaration",
                            }
                            for target in pending_mixin_targets
                        ),
                        attributes={"targets": sorted(set(pending_mixin_targets))},
                        state="static-possible",
                    )
                )
                mixin_emitted = True

        method_match = _GROOVY_METHOD_RE.match(code_line) if language == "groovy" else None
        return_type: str | None = None
        if method_match:
            method_name = method_match.group(1)
            parameters = method_match.group(2).strip()
        else:
            java_method = _METHOD_RE.match(code_line)
            if java_method and java_method.group(2) not in _CONTROL_NAMES:
                return_type = java_method.group(1).strip()
                method_name = java_method.group(2)
                parameters = java_method.group(3).strip()
            else:
                constructor = _CONSTRUCTOR_RE.match(code_line)
                current_simple = (
                    current_type.rsplit(".", 1)[-1] if current_type else None
                )
                if constructor and constructor.group(1) == current_simple:
                    method_name = "<init>"
                    parameters = constructor.group(2).strip()
                    return_type = None
                else:
                    method_name = ""
                    parameters = ""
        if method_name:
            qualified = (
                f"{current_type}#{method_name}" if current_type else method_name
            )
            entries.append(
                _entry(
                    kind="method",
                    name=qualified,
                    path=path,
                    line=line_number,
                    file_sha256=file_sha256,
                    language=language,
                    identities=(
                        _identity("method-name", method_name, "source-declaration"),
                        _identity("source-member", qualified, "source-declaration"),
                    ),
                    owner_candidates=owners,
                    relationships=(
                        ({
                            "predicate": "member-of",
                            "target": {"kind": "class-name", "value": current_type},
                            "basis": "lexical source declaration",
                        },)
                        if current_type
                        else ()
                    ),
                    attributes={
                        "parameters": parameters,
                        "return_type": return_type,
                    },
                )
            )
        elif (field_match := _FIELD_RE.match(code_line)) is not None:
            field_name = field_match.group(2)
            qualified = f"{current_type}#{field_name}" if current_type else field_name
            entries.append(
                _entry(
                    kind="field",
                    name=qualified,
                    path=path,
                    line=line_number,
                    file_sha256=file_sha256,
                    language=language,
                    identities=(
                        _identity("field-name", field_name, "source-declaration"),
                        _identity("source-member", qualified, "source-declaration"),
                    ),
                    owner_candidates=owners,
                    attributes={"declared_type": field_match.group(1).strip()},
                )
            )

        seen_ids: set[str] = set()
        for minecraft_id in _MINECRAFT_ID_RE.findall(line):
            if minecraft_id.split(":", 1)[0] in {"http", "https", "file"}:
                continue
            if minecraft_id in seen_ids:
                continue
            seen_ids.add(minecraft_id)
            registry_context = any(
                token in line.lower()
                for token in (
                    "registry",
                    "resourcelocation",
                    "recipe",
                    "biome",
                    "structure",
                    "fluid",
                    "material",
                    "generator",
                )
            )
            entries.append(
                _entry(
                    kind="registry-name" if registry_context else "resource-reference",
                    name=minecraft_id,
                    path=path,
                    line=line_number,
                    file_sha256=file_sha256,
                    language=language,
                    identities=(
                        _identity(
                            "registry-name" if registry_context else "resource-location",
                            minecraft_id,
                            "source-literal",
                        ),
                    ),
                    owner_candidates=owners,
                    relationships=(
                        ({
                            "predicate": "referenced-by",
                            "target": {"kind": "class-name", "value": current_type},
                            "basis": "source literal",
                        },)
                        if current_type
                        else ()
                    ),
                    state="static-possible",
                )
            )
    return entries


def _resource_identity(path: str) -> tuple[str, str] | None:
    parts = PurePosixPath(path).parts
    for marker in ("assets", "data"):
        try:
            index = parts.index(marker)
        except ValueError:
            continue
        if len(parts) <= index + 2:
            continue
        namespace = parts[index + 1]
        remainder = PurePosixPath(*parts[index + 2 :]).as_posix()
        return namespace, f"{namespace}:{remainder}"
    return None


def _resource_file_entry(
    *,
    path: str,
    file_sha256: str,
    language: str,
    surface: str,
    default_owners: list[dict[str, Any]],
) -> dict[str, Any]:
    resource = _resource_identity(path)
    identities = [_identity("file-path", path, "workspace inventory")]
    owners = default_owners
    kind = (
        "source-file"
        if Path(path).suffix.lower() in _SOURCE_SUFFIXES
        else "config-file"
        if surface == "configuration"
        else "script"
        if surface == "groovy"
        else "resource"
    )
    name = path
    if resource:
        namespace, resource_location = resource
        identities.append(
            _identity("resource-location", resource_location, "resource path")
        )
        owners = [
            {
                "mod_id": namespace,
                "version": None,
                "state": "declared",
                "basis": "resource namespace",
            }
        ]
        parts = PurePosixPath(path).parts
        marker_index = next(
            (
                parts.index(marker)
                for marker in ("assets", "data")
                if marker in parts
            ),
            None,
        )
        category = (
            parts[marker_index + 2]
            if marker_index is not None and len(parts) > marker_index + 2
            else ""
        )
        suffixless = str(PurePosixPath(resource_location.split(":", 1)[1]).with_suffix(""))
        if category in {"recipes", "loot_tables", "advancements", "blockstates", "models"}:
            singular = {
                "recipes": "recipe",
                "loot_tables": "loot-table",
                "advancements": "advancement",
                "blockstates": "blockstate",
                "models": "model",
            }[category]
            logical = suffixless.split("/", 1)[1] if "/" in suffixless else suffixless
            identities.append(
                _identity(f"{singular}-id", f"{namespace}:{logical}", "resource path")
            )
            kind = singular
            name = f"{namespace}:{logical}"
    return _entry(
        kind=kind,
        name=name,
        path=path,
        line=1,
        file_sha256=file_sha256,
        language=language,
        identities=identities,
        owner_candidates=owners,
        attributes={"surface": surface},
    )


def _json_entries(
    text: str,
    *,
    path: str,
    file_sha256: str,
    language: str,
    owners: list[dict[str, Any]],
    config_surface: bool,
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        value = _strict_json(text)
    except (json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
        return [], str(exc)
    entries: list[dict[str, Any]] = []
    visited = 0

    def walk(item: Any, pointer: str, depth: int) -> None:
        nonlocal visited
        if depth > MAX_JSON_DEPTH or visited >= MAX_JSON_VALUES:
            return
        visited += 1
        if isinstance(item, dict):
            for key, child in item.items():
                encoded_key = key.replace("~", "~0").replace("/", "~1")
                child_pointer = pointer + "/" + encoded_key
                if config_surface:
                    entries.append(
                        _entry(
                            kind="config-key",
                            name=f"{path}#{child_pointer}",
                            path=path,
                            line=1,
                            file_sha256=file_sha256,
                            language=language,
                            identities=(
                                _identity("config-key", key, "JSON object key"),
                                _identity("config-pointer", f"{path}#{child_pointer}", "JSON pointer"),
                            ),
                            owner_candidates=owners,
                            attributes={"json_pointer": child_pointer},
                        )
                    )
                normalized = key.lower().replace("-", "_")
                identity_kind = _KNOWN_IDENTITY_FIELDS.get(normalized)
                if identity_kind and isinstance(child, (str, int)) and not isinstance(child, bool):
                    rendered = str(child)
                    if rendered and len(rendered) <= 8192 and "\x00" not in rendered:
                        entries.append(
                            _entry(
                                kind=identity_kind.removesuffix("-id").removesuffix("-name") or "identity",
                                name=rendered,
                                path=path,
                                line=1,
                                file_sha256=file_sha256,
                                language=language,
                                identities=(
                                    _identity(identity_kind, rendered, f"JSON field {key}"),
                                ),
                                owner_candidates=owners,
                                attributes={"json_pointer": child_pointer},
                            )
                        )
                walk(child, child_pointer, depth + 1)
        elif isinstance(item, list):
            for index, child in enumerate(item):
                walk(child, pointer + f"/{index}", depth + 1)
        elif isinstance(item, str) and len(item) <= 8192:
            for minecraft_id in _MINECRAFT_ID_RE.findall(item):
                if minecraft_id.split(":", 1)[0] in {"http", "https", "file"}:
                    continue
                entries.append(
                    _entry(
                        kind="resource-reference",
                        name=minecraft_id,
                        path=path,
                        line=1,
                        file_sha256=file_sha256,
                        language=language,
                        identities=(
                            _identity("resource-location", minecraft_id, "JSON string value"),
                        ),
                        owner_candidates=owners,
                        attributes={"json_pointer": pointer},
                        state="static-possible",
                    )
                )

    walk(value, "", 0)
    if isinstance(value, dict) and any(
        isinstance(value.get(key), list) for key in ("mixins", "client", "server")
    ):
        package = value.get("package") if isinstance(value.get("package"), str) else ""
        for side in ("mixins", "client", "server"):
            rows = value.get(side)
            if not isinstance(rows, list):
                continue
            for raw_name in rows:
                if not isinstance(raw_name, str) or not raw_name:
                    continue
                qualified = f"{package}.{raw_name}" if package and "." not in raw_name else raw_name
                entries.append(
                    _entry(
                        kind="mixin",
                        name=qualified,
                        path=path,
                        line=1,
                        file_sha256=file_sha256,
                        language=language,
                        identities=(
                            _identity("mixin-class", qualified, f"Mixin config {side}"),
                        ),
                        owner_candidates=owners,
                        attributes={"configuration_side": side, "package": package or None},
                        state="static-possible",
                    )
                )
        plugin = value.get("plugin")
        if isinstance(plugin, str) and plugin:
            entries.append(
                _entry(
                    kind="mixin-plugin",
                    name=plugin,
                    path=path,
                    line=1,
                    file_sha256=file_sha256,
                    language=language,
                    identities=(_identity("class-name", plugin, "Mixin config plugin"),),
                    owner_candidates=owners,
                    state="static-possible",
                )
            )
    return entries, None


def _configuration_entries(
    text: str,
    *,
    path: str,
    file_sha256: str,
    language: str,
    owners: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    section = ""
    entries: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if section_match := _SECTION_RE.match(line):
            section = section_match.group(1).strip()
            continue
        match = _ASSIGNMENT_RE.match(line)
        if not match:
            continue
        key = match.group(1)
        qualified = f"{section}.{key}" if section else key
        identity_rows = [
            _identity("config-key", key, "configuration assignment"),
            _identity(
                "config-path-key",
                f"{path}#{qualified}",
                "configuration assignment",
            ),
        ]
        script_key = language in {"groovy", "zenscript"}
        if script_key:
            identity_rows.append(
                _identity("groovy-key", qualified, "script assignment")
            )
        entries.append(
            _entry(
                kind="groovy-key" if script_key else "config-key",
                name=f"{path}#{qualified}",
                path=path,
                line=line_number,
                file_sha256=file_sha256,
                language=language,
                identities=identity_rows,
                owner_candidates=owners,
                attributes={"section": section or None, "key": key},
            )
        )
    return entries


def _language(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".java": "java",
        ".groovy": "groovy",
        ".kt": "kotlin",
        ".kts": "kotlin",
        ".zs": "zenscript",
        ".json": "json",
        ".json5": "json5",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".properties": "properties",
        ".cfg": "forge-config",
        ".lang": "localization",
    }.get(suffix, suffix.removeprefix(".") or "text")


def _walk_regular_files(
    root: Path,
    *,
    maximum: int,
) -> tuple[list[Path], bool]:
    result: list[Path] = []
    visited_directories = 0
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        visited_directories += 1
        if visited_directories > MAX_DISCOVERY_DIRECTORIES:
            return result, True
        base = Path(directory)
        kept: list[str] = []
        for name in sorted(names):
            candidate = base / name
            try:
                metadata = candidate.lstat()
            except OSError:
                continue
            if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                kept.append(name)
        names[:] = kept
        for name in sorted(files):
            candidate = base / name
            try:
                metadata = candidate.lstat()
            except OSError:
                continue
            if stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                if len(result) >= maximum:
                    return result, True
                result.append(candidate)
    return result, False


def _discover_nested_surface_roots(
    root: Path,
) -> tuple[list[tuple[str, Path]], bool]:
    """Find conventional nested Gradle source/config roots without links."""

    result: dict[tuple[str, str], tuple[str, Path]] = {}
    visited = 0
    truncated = False
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        visited += 1
        if visited > MAX_DISCOVERY_DIRECTORIES:
            truncated = True
            break
        base = Path(directory)
        kept: list[str] = []
        for name in sorted(names):
            if name in _DISCOVERY_SKIP_DIRECTORIES:
                continue
            candidate = base / name
            try:
                metadata = candidate.lstat()
            except OSError:
                continue
            if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                kept.append(name)
        names[:] = kept
        relative = base.relative_to(root)
        parts = relative.parts
        if len(parts) >= 3 and parts[-3] == "src":
            language = parts[-1].casefold()
            if language in {"java", "kotlin", "resources", "groovy"}:
                surface = (
                    "resources"
                    if language == "resources"
                    else "groovy"
                    if language == "groovy"
                    else "generated"
                    if "generated" in (part.casefold() for part in parts)
                    else "source"
                )
                result[(surface, str(base))] = (surface, base)
        if any(name in files for name in ("build.gradle", "build.gradle.kts")):
            for child_name, surface in (
                ("config", "configuration"),
                ("configs", "configuration"),
                ("defaultconfigs", "configuration"),
                ("scripts", "groovy"),
                ("groovy", "groovy"),
            ):
                child = base / child_name
                try:
                    metadata = child.lstat()
                except OSError:
                    continue
                if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                    result[(surface, str(child))] = (surface, child)
    return [result[key] for key in sorted(result)], truncated


def _read_exact_file(path: Path, expected: os.stat_result) -> bytes:
    # Windows text-mode descriptors translate CRLF while ``st_size`` remains
    # the byte length on disk.  This reader binds exact source bytes, so always
    # request binary mode where the host exposes it.
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != expected.st_dev
            or opened.st_ino != expected.st_ino
            or opened.st_size != expected.st_size
        ):
            raise RuntimeSurfaceError(f"workspace file changed while opening: {path}")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise RuntimeSurfaceError(f"workspace file ended while reading: {path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        visible = path.lstat()
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or visible.st_dev != opened.st_dev
            or visible.st_ino != opened.st_ino
            or visible.st_size != opened.st_size
        ):
            raise RuntimeSurfaceError(f"workspace file changed while reading: {path}")
        return b"".join(chunks)
    except OSError as exc:
        raise RuntimeSurfaceError(f"cannot read workspace file {path}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def scan_runtime_surface(
    workspace: Path | str,
    *,
    max_files: int = MAX_FILES,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    max_declarations: int = MAX_DECLARATIONS,
) -> dict[str, Any]:
    """Return a deterministic, read-only declaration inventory."""

    for label, value, upper in (
        ("max_files", max_files, MAX_FILES),
        ("max_file_bytes", max_file_bytes, MAX_FILE_BYTES),
        ("max_total_bytes", max_total_bytes, MAX_TOTAL_BYTES),
        ("max_declarations", max_declarations, MAX_DECLARATIONS),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
            raise RuntimeSurfaceError(f"{label} must be an integer from 1 through {upper}")
    report = new_report(Path(workspace), requested_path=Path(workspace))
    root = Path(report["target"]["workspace"]["root"])
    try:
        root_before = root.lstat()
    except OSError as exc:
        raise RuntimeSurfaceError(f"cannot inspect workspace root {root}: {exc}") from exc
    if not stat.S_ISDIR(root_before.st_mode) or stat.S_ISLNK(root_before.st_mode):
        raise RuntimeSurfaceError("workspace root must be a real directory")
    surfaces = report["target"]["surfaces"]
    mods = surfaces.get("mods", []) if isinstance(surfaces, dict) else []
    default_owners = _owner_rows(mods, basis="workspace mod descriptor")
    roots: list[tuple[str, Path]] = []
    for surface in ("source", "resources", "generated", "groovy", "configuration"):
        values = surfaces.get(surface, []) if isinstance(surfaces, dict) else []
        if not isinstance(values, list):
            continue
        for relative in values:
            if not isinstance(relative, str):
                continue
            candidate = root / relative
            if candidate.is_dir() and not candidate.is_symlink():
                roots.append((surface, candidate))
    nested_roots, discovery_truncated = _discover_nested_surface_roots(root)
    known_roots = {(surface, str(path)) for surface, path in roots}
    roots.extend(
        row
        for row in nested_roots
        if (row[0], str(row[1])) not in known_roots
    )
    explicit_files = [
        candidate
        for candidate in (
            root / "gradle.properties",
            root / "build.gradle",
            root / "build.gradle.kts",
            root / "settings.gradle",
            root / "settings.gradle.kts",
        )
        if candidate.is_file() and not candidate.is_symlink()
    ]
    candidates: dict[str, tuple[str, Path]] = {}
    for surface, source_root in sorted(roots, key=lambda row: (str(row[1]), row[0])):
        remaining_discovery = max_files + 1 - len(candidates)
        if remaining_discovery <= 0:
            discovery_truncated = True
            break
        paths, limited = _walk_regular_files(
            source_root,
            maximum=remaining_discovery,
        )
        for path in paths:
            relative = path.relative_to(root).as_posix()
            candidates.setdefault(relative, (surface, path))
        if limited:
            discovery_truncated = True
            break
    for path in explicit_files:
        candidates.setdefault(path.relative_to(root).as_posix(), ("build", path))

    preloaded: dict[str, tuple[bytes, os.stat_result]] = {}
    module_mods: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    nested_mods: list[dict[str, Any]] = []
    for relative, (_, path) in sorted(candidates.items()):
        if PurePosixPath(relative).name.casefold() != "mcmod.info":
            continue
        try:
            metadata = path.lstat()
        except OSError:
            continue
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_size > max_file_bytes
        ):
            continue
        raw = _read_exact_file(path, metadata)
        preloaded[relative] = (raw, metadata)
        rows = _read_mcmod_bytes(raw, relative)
        if rows:
            prefix = _module_prefix(relative)
            module_mods.setdefault(prefix, []).extend(rows)
            nested_mods.extend(rows)
    if nested_mods:
        mods = sorted(
            {
                (row["mod_id"], row.get("descriptor")): row
                for row in [*mods, *nested_mods]
            }.values(),
            key=lambda row: (row["mod_id"], str(row.get("descriptor", ""))),
        )
        default_owners = _owner_rows(mods, basis="workspace mod descriptor")

    files: list[dict[str, Any]] = []
    declarations: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    total_bytes = 0
    truncated = discovery_truncated
    if discovery_truncated:
        skipped.append({"path": "*", "reason": "candidate-discovery-limit"})
    ordered = sorted(candidates.items())
    if len(ordered) > max_files:
        ordered = ordered[:max_files]
        truncated = True
        skipped.append({"path": "*", "reason": "file-count-limit"})
    for relative, (surface, path) in ordered:
        try:
            metadata = path.lstat()
        except OSError as exc:
            skipped.append({"path": relative, "reason": f"unreadable-metadata:{exc}"})
            continue
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            skipped.append({"path": relative, "reason": "not-regular-unlinked-file"})
            continue
        if metadata.st_size > max_file_bytes:
            skipped.append({"path": relative, "reason": "per-file-byte-limit"})
            truncated = True
            continue
        if total_bytes + metadata.st_size > max_total_bytes:
            skipped.append({"path": relative, "reason": "total-byte-limit"})
            truncated = True
            continue
        if relative in preloaded:
            raw, opened = preloaded[relative]
            visible = path.lstat()
            if (
                visible.st_dev != opened.st_dev
                or visible.st_ino != opened.st_ino
                or visible.st_size != opened.st_size
                or visible.st_mtime_ns != opened.st_mtime_ns
            ):
                raise RuntimeSurfaceError(
                    f"workspace file changed after descriptor discovery: {path}"
                )
        else:
            raw = _read_exact_file(path, metadata)
        total_bytes += len(raw)
        digest = hashlib.sha256(raw).hexdigest()
        language = _language(path)
        file_owners = _owners_for_relative(
            relative, module_mods, default_owners
        )
        file_row = {
            "path": relative,
            "surface": surface,
            "language": language,
            "bytes": len(raw),
            "sha256": digest,
            "text": path.suffix.lower() in _TEXT_SUFFIXES,
        }
        files.append(file_row)
        declarations.append(
            _resource_file_entry(
                path=relative,
                file_sha256=digest,
                language=language,
                surface=surface,
                default_owners=file_owners,
            )
        )
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            file_row["text"] = False
            skipped.append({"path": relative, "reason": "invalid-utf8-for-text-scan"})
            continue
        if path.suffix.lower() in _SOURCE_SUFFIXES:
            declarations.extend(
                _source_entries(
                    text,
                    path=relative,
                    file_sha256=digest,
                    language=language,
                    default_owners=file_owners,
                )
            )
        if path.suffix.lower() == ".json":
            json_rows, problem = _json_entries(
                text,
                path=relative,
                file_sha256=digest,
                language=language,
                owners=file_owners,
                config_surface=surface in {"configuration", "groovy"},
            )
            declarations.extend(json_rows)
            if problem:
                skipped.append({"path": relative, "reason": "malformed-json:" + problem[:512]})
        if surface in {"configuration", "groovy", "build"} or path.suffix.lower() in {
            ".cfg", ".conf", ".properties", ".toml"
        }:
            declarations.extend(
                _configuration_entries(
                    text,
                    path=relative,
                    file_sha256=digest,
                    language=language,
                    owners=file_owners,
                )
            )
        if len(declarations) >= max_declarations:
            declarations = declarations[:max_declarations]
            skipped.append({"path": "*", "reason": "declaration-count-limit"})
            truncated = True
            break

    try:
        root_after = root.lstat()
    except OSError as exc:
        raise RuntimeSurfaceError(f"cannot revalidate workspace root {root}: {exc}") from exc
    if (
        root_after.st_dev != root_before.st_dev
        or root_after.st_ino != root_before.st_ino
        or stat.S_ISLNK(root_after.st_mode)
        or not stat.S_ISDIR(root_after.st_mode)
    ):
        raise RuntimeSurfaceError("workspace root identity changed during scan")
    declarations = sorted(
        {row["declaration_id"]: row for row in declarations}.values(),
        key=lambda row: (
            row["name"].casefold(),
            row["kind"],
            row["declaration"]["path"],
            row["declaration"]["line"],
            row["declaration_id"],
        ),
    )
    inventory_identity = {
        "files": [
            {key: row[key] for key in ("path", "bytes", "sha256", "surface")}
            for row in files
        ],
        "declaration_ids": [row["declaration_id"] for row in declarations],
    }
    return {
        "format": FORMAT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "read_only": True,
        "surface_id": "workbench-runtime-surface:sha256:" + _digest(inventory_identity),
        "workspace": {
            "root": str(root),
            "doctor_format": report["format"],
            "repository": report["target"]["repository"],
            "platform": report["target"]["platform"],
            "mods": mods,
        },
        "limits": {
            "max_files": max_files,
            "max_file_bytes": max_file_bytes,
            "max_total_bytes": max_total_bytes,
            "max_declarations": max_declarations,
        },
        "coverage": {
            "complete": not truncated,
            "candidate_files": len(candidates),
            "scanned_files": len(files),
            "scanned_bytes": total_bytes,
            "declarations": len(declarations),
            "skipped": sorted(skipped, key=lambda row: (row["path"], row["reason"])),
        },
        "files": sorted(files, key=lambda row: row["path"]),
        "declarations": declarations,
        "limitations": [
            "This surface records source, resource, script, and configuration declarations; it does not prove runtime loading, registration, transformation, or execution.",
            "Source members are located with a bounded lexical parser, not a compiler or mapping resolver.",
            "Literal registry and resource references are static possibilities until Atlas or Crucible binds observed runtime evidence.",
        ],
    }


def validate_runtime_surface(value: Mapping[str, Any]) -> None:
    """Fail closed on the stable envelope and identity-bearing inventory."""

    if not isinstance(value, Mapping):
        raise RuntimeSurfaceError("runtime surface must be an object")
    if value.get("format") != FORMAT_VERSION or value.get("schema_version") != 1:
        raise RuntimeSurfaceError("runtime surface format is unsupported")
    if value.get("read_only") is not True:
        raise RuntimeSurfaceError("runtime surface must be read-only")
    files = value.get("files")
    declarations = value.get("declarations")
    if not isinstance(files, list) or not isinstance(declarations, list):
        raise RuntimeSurfaceError("runtime surface inventory is malformed")
    paths: set[str] = set()
    for row in files:
        if not isinstance(row, dict):
            raise RuntimeSurfaceError("runtime surface file row is malformed")
        path = _single_line(row.get("path"), "runtime surface file path")
        if path in paths or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
            raise RuntimeSurfaceError("runtime surface file paths are unsafe or duplicated")
        paths.add(path)
        if not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", ""))):
            raise RuntimeSurfaceError("runtime surface file hash is malformed")
    declaration_ids: set[str] = set()
    for row in declarations:
        if not isinstance(row, dict):
            raise RuntimeSurfaceError("runtime surface declaration is malformed")
        identifier = _single_line(row.get("declaration_id"), "declaration ID")
        if identifier in declaration_ids:
            raise RuntimeSurfaceError("runtime surface repeats a declaration ID")
        declaration_ids.add(identifier)
        declaration = row.get("declaration")
        if not isinstance(declaration, dict) or declaration.get("path") not in paths:
            raise RuntimeSurfaceError("runtime declaration references an unknown file")
    inventory_identity = {
        "files": [
            {key: row[key] for key in ("path", "bytes", "sha256", "surface")}
            for row in files
        ],
        "declaration_ids": [row["declaration_id"] for row in declarations],
    }
    expected = "workbench-runtime-surface:sha256:" + _digest(inventory_identity)
    if value.get("surface_id") != expected:
        raise RuntimeSurfaceError("runtime surface identity is stale or invalid")
