"""Plan one bounded edit to an existing Supersymmetry quest.

The pack profile owns BetterQuesting path, typed-key, localization, and
prerequisite conventions.  This module renders exact replacement bytes only;
Workbench Shell supplies review, consent, transaction, and runtime custody.
"""

from __future__ import annotations

PROFILE_API_VERSION = 1

from collections import deque
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping, NoReturn


QUEST_ROOT = PurePosixPath("config/betterquesting/DefaultQuests/Quests")
QUEST_LANGUAGE = PurePosixPath(
    "config/betterquesting/resources/supersymmetry/lang/en_us.lang"
)
MAX_QUEST_FILES = 4096
MAX_SOURCE_BYTES = 4 * 1024 * 1024
REQUIREMENT_TYPES = {"NORMAL": 0, "IMPLICIT": 1, "HIDDEN": 2}
REQUIREMENT_TYPE_NAMES = {
    value: name for name, value in REQUIREMENT_TYPES.items()
}
RENDER_FORMAT = "workbench-supersymmetry-quest-for-process-render-v1"
RENDER_STATE = "source-ready-runtime-unverified"
OPTIONS_FORMAT = "workbench-supersymmetry-quest-for-process-options-v1"
DEFAULT_OPTIONS_LIMIT = 50
MAX_OPTIONS_LIMIT = 200
RENDER_LIMITATIONS = (
    "Source structure does not prove BetterQuesting runtime load or player detection.",
    "Pack-default quest bytes do not rewrite quest state already retained in a world.",
    "New-edge checks do not certify pre-existing quest graph health; inspect it through Atlas.",
    "Objective reachability requires a compatible Atlas runtime graph.",
)
_TYPED_TAGS = {
    "betterquesting": 10,
    "desc": 8,
    "name": 8,
    "preRequisiteTypes": 7,
    "preRequisites": 11,
    "properties": 10,
    "questID": 3,
}
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_LANG_KEY = re.compile(r"[A-Za-z0-9_.:-]+")


class QuestForProcessError(ValueError):
    """The requested profile-owned quest edit is unsafe or unsupported."""


def _fail(message: str) -> NoReturn:
    raise QuestForProcessError(message)


def _regular_bytes(root: Path, relative: PurePosixPath, label: str) -> bytes:
    parent = root
    for part in relative.parts[:-1]:
        parent = parent / part
        try:
            parent_state = parent.lstat()
        except OSError as exc:
            raise QuestForProcessError(f"cannot inspect {label} parent") from exc
        if stat.S_ISLNK(parent_state.st_mode) or not stat.S_ISDIR(
            parent_state.st_mode
        ):
            _fail(f"{label} traverses a symbolic link or non-directory")
    path = root.joinpath(*relative.parts)
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
        )
    except OSError as exc:
        raise QuestForProcessError(f"cannot read {label}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or not 1 <= before.st_size <= MAX_SOURCE_BYTES
        ):
            _fail(f"{label} is outside the supported regular-file byte bound")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, MAX_SOURCE_BYTES + 1))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_SOURCE_BYTES:
                _fail(f"{label} exceeds its byte bound")
        value = b"".join(chunks)
        after = os.fstat(descriptor)
        identity = lambda row: (
            row.st_dev,
            row.st_ino,
            row.st_mode,
            row.st_size,
            row.st_mtime_ns,
        )
        if identity(before) != identity(after) or len(value) != before.st_size:
            _fail(f"{label} changed while being read")
        return value
    finally:
        os.close(descriptor)


def _ordinary_descendant_directory(
    root: Path, relative: PurePosixPath, label: str
) -> Path:
    path = root
    for part in relative.parts:
        path = path / part
        try:
            state = path.lstat()
        except OSError as exc:
            raise QuestForProcessError(f"cannot inspect {label}") from exc
        if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
            _fail(f"{label} traverses a symbolic link or non-directory")
    return path


def _line_shape(raw: bytes, label: str) -> tuple[str, bool]:
    if b"\r" in raw.replace(b"\r\n", b""):
        _fail(f"{label} contains a bare carriage return")
    has_crlf = b"\r\n" in raw
    without_crlf = raw.replace(b"\r\n", b"")
    if has_crlf and b"\n" in without_crlf:
        _fail(f"{label} mixes LF and CRLF line endings")
    return ("\r\n" if has_crlf else "\n", raw.endswith(b"\n"))


def _decode_json(
    raw: bytes, label: str
) -> tuple[dict[str, Any], str, bool, bool | None]:
    line_ending, trailing_newline = _line_shape(raw, label)
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise QuestForProcessError(f"{label} is not valid UTF-8 JSON: {exc}") from exc
    if type(value) is not dict:
        _fail(f"{label} must contain one JSON object")
    ascii_style: bool | None = None
    for ensure_ascii in (True, False):
        if _render_json(
            value,
            line_ending,
            trailing_newline,
            ensure_ascii=ensure_ascii,
        ) == raw:
            ascii_style = ensure_ascii
            break
    return value, line_ending, trailing_newline, ascii_style


def _render_json(
    value: Mapping[str, Any],
    line_ending: str,
    trailing_newline: bool,
    *,
    ensure_ascii: bool,
) -> bytes:
    text = json.dumps(value, ensure_ascii=ensure_ascii, indent=2)
    if trailing_newline:
        text += "\n"
    if line_ending == "\r\n":
        text = text.replace("\n", "\r\n")
    return text.encode("utf-8")


def _typed_key(value: Mapping[str, Any], semantic: str, label: str) -> str:
    expected = f"{semantic}:{_TYPED_TAGS[semantic]}"
    matches = [
        key
        for key in value
        if key == semantic
        or (key.startswith(semantic + ":") and key.rsplit(":", 1)[1].isdigit())
    ]
    if matches != [expected]:
        _fail(f"{label} must contain exactly one {expected} field")
    return expected


def _optional_typed_key(
    value: Mapping[str, Any], semantic: str, label: str
) -> str | None:
    expected = f"{semantic}:{_TYPED_TAGS[semantic]}"
    matches = [
        key
        for key in value
        if key == semantic
        or (key.startswith(semantic + ":") and key.rsplit(":", 1)[1].isdigit())
    ]
    if matches and matches != [expected]:
        _fail(f"{label} must use the exact optional field {expected}")
    return expected if matches else None


def _quest_identifier(value: object, label: str) -> int:
    if type(value) is not int or not 0 <= value <= 2_147_483_647:
        _fail(f"{label} must be a non-negative signed 32-bit integer")
    return value


def _prerequisite_fields(
    value: Mapping[str, Any], label: str
) -> tuple[str | None, str | None, list[int], list[int]]:
    prerequisites_key = _optional_typed_key(value, "preRequisites", label)
    prerequisite_types_key = _optional_typed_key(
        value, "preRequisiteTypes", label
    )
    prerequisites = [] if prerequisites_key is None else value[prerequisites_key]
    prerequisite_types = (
        [] if prerequisite_types_key is None else value[prerequisite_types_key]
    )
    if (
        type(prerequisites) is not list
        or type(prerequisite_types) is not list
        or len(prerequisite_types) > len(prerequisites)
        or any(type(item) is not int for item in prerequisites)
        or any(
            type(item) is not int or item not in REQUIREMENT_TYPES.values()
            for item in prerequisite_types
        )
        or len(set(prerequisites)) != len(prerequisites)
    ):
        _fail(f"quest prerequisites are malformed: {label}")
    return (
        prerequisites_key,
        prerequisite_types_key,
        list(prerequisites),
        list(prerequisite_types),
    )


def _validated_request(value: object) -> dict[str, Any]:
    fields = {
        "add_prerequisite_id",
        "description",
        "quest_id",
        "requirement_type",
        "title",
    }
    if type(value) is not dict or set(value) != fields:
        _fail("quest-for-process request fields changed")
    request = dict(value)
    request["quest_id"] = _quest_identifier(request["quest_id"], "quest ID")
    prerequisite = request["add_prerequisite_id"]
    if prerequisite is not None:
        prerequisite = _quest_identifier(prerequisite, "prerequisite quest ID")
    requirement = request["requirement_type"]
    if (prerequisite is None and requirement is not None) or (
        prerequisite is not None and requirement not in REQUIREMENT_TYPES
    ):
        _fail("quest prerequisite type must be NORMAL, IMPLICIT, or HIDDEN")
    for field in ("title", "description"):
        replacement = request[field]
        if replacement is not None and (
            type(replacement) is not str
            or not replacement
            or any(character in replacement for character in "\r\n")
        ):
            _fail(f"quest localization value for {field} is invalid")
    if prerequisite is None and request["title"] is None and request["description"] is None:
        _fail("quest update requires a prerequisite or localization change")
    return request


def _quest_path(value: object, quest_id: int) -> str:
    if type(value) is not str or "\\" in value:
        _fail("quest source path is invalid")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.parts[: len(QUEST_ROOT.parts)] != QUEST_ROOT.parts
        or relative.suffix != ".json"
        or relative.stem != str(quest_id)
    ):
        _fail("quest source path and identity differ")
    return value


def _updated_quest_value(
    source: Mapping[str, Any], request: Mapping[str, Any]
) -> dict[str, Any]:
    quest_value = json.loads(json.dumps(source, ensure_ascii=False))
    (
        prerequisites_key,
        prerequisite_types_key,
        prerequisites,
        prerequisite_types,
    ) = _prerequisite_fields(quest_value, "selected quest")
    prerequisite_id = request["add_prerequisite_id"]
    if prerequisite_id is None:
        return quest_value
    if prerequisite_id == request["quest_id"]:
        _fail("a quest cannot require itself")
    if prerequisite_id in prerequisites:
        _fail("selected prerequisite already exists on the quest")
    prerequisites.append(prerequisite_id)
    while len(prerequisite_types) < len(prerequisites) - 1:
        prerequisite_types.append(REQUIREMENT_TYPES["NORMAL"])
    prerequisite_types.append(REQUIREMENT_TYPES[request["requirement_type"]])
    prerequisites_key = prerequisites_key or "preRequisites:11"
    prerequisite_types_key = prerequisite_types_key or "preRequisiteTypes:7"
    return {
        prerequisite_types_key: prerequisite_types,
        prerequisites_key: prerequisites,
        **{
            key: item
            for key, item in quest_value.items()
            if key not in {prerequisites_key, prerequisite_types_key}
        },
    }


def _quest_sources(
    root: Path,
) -> tuple[dict[int, dict[str, Any]], dict[int, set[int]], dict[str, Any]]:
    quest_root = _ordinary_descendant_directory(
        root, QUEST_ROOT, "BetterQuesting quest root"
    )
    paths = sorted(path for path in quest_root.rglob("*.json"))
    if not paths or len(paths) > MAX_QUEST_FILES:
        _fail("BetterQuesting quest file universe is outside its bound")

    quests: dict[int, dict[str, Any]] = {}
    graph: dict[int, set[int]] = {}
    bindings: list[dict[str, Any]] = []
    for path in paths:
        relative = PurePosixPath(path.relative_to(root).as_posix())
        if path.is_symlink() or not path.is_file():
            _fail(f"quest source is not a regular file: {relative}")
        raw = _regular_bytes(root, relative, f"quest source {relative}")
        value, line_ending, trailing_newline, ensure_ascii = _decode_json(
            raw, f"quest source {relative}"
        )
        quest_key = _typed_key(value, "questID", f"quest source {relative}")
        quest_id = _quest_identifier(
            value[quest_key], f"quest identity in {relative}"
        )
        if path.stem != str(quest_id) or quest_id in quests:
            _fail(f"quest filename or identity is ambiguous: {relative}")
        (
            prerequisites_key,
            prerequisite_types_key,
            prerequisites,
            _prerequisite_types,
        ) = _prerequisite_fields(value, f"quest source {relative}")
        quests[quest_id] = {
            "id": quest_id,
            "path": relative.as_posix(),
            "raw": raw,
            "value": value,
            "line_ending": line_ending,
            "trailing_newline": trailing_newline,
            "ensure_ascii": ensure_ascii,
            "prerequisites_key": prerequisites_key,
            "prerequisite_types_key": prerequisite_types_key,
        }
        graph[quest_id] = set(prerequisites)
        bindings.append(
            {
                "path": relative.as_posix(),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            }
        )

    digest_source = json.dumps(
        bindings, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return quests, graph, {
        "file_count": len(bindings),
        "sha256": hashlib.sha256(digest_source).hexdigest(),
    }


def _reaches(graph: Mapping[int, set[int]], start: int, target: int) -> bool:
    queue = deque([start])
    visited: set[int] = set()
    while queue:
        current = queue.popleft()
        if current == target:
            return True
        if current in visited:
            continue
        visited.add(current)
        queue.extend(sorted(graph.get(current, set()) - visited))
    return False


def _quest_language_keys(quest: Mapping[str, Any]) -> tuple[str, str]:
    properties_key = _typed_key(quest, "properties", "selected quest")
    properties = quest[properties_key]
    if type(properties) is not dict:
        _fail("selected quest properties are malformed")
    better_key = _typed_key(properties, "betterquesting", "selected quest properties")
    better = properties[better_key]
    if type(better) is not dict:
        _fail("selected quest BetterQuesting properties are malformed")
    title_key = _typed_key(better, "name", "selected quest presentation")
    description_key = _typed_key(better, "desc", "selected quest presentation")
    title = better[title_key]
    description = better[description_key]
    if (
        type(title) is not str
        or type(description) is not str
        or _LANG_KEY.fullmatch(title) is None
        or _LANG_KEY.fullmatch(description) is None
    ):
        _fail("selected quest localization keys are malformed")
    return title, description


def _language_lines(raw: bytes) -> tuple[list[str], str, bool]:
    line_ending, trailing_newline = _line_shape(raw, "quest localization")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise QuestForProcessError("quest localization is not UTF-8") from exc
    return text.splitlines(), line_ending, trailing_newline


def _language_values(lines: list[str]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if _LANG_KEY.fullmatch(key) is None:
            continue
        values.setdefault(key, []).append(value)
    return values


def discover_quest_for_process_options(
    workspace_root: Path | str,
    *,
    query: str | None = None,
    limit: int = DEFAULT_OPTIONS_LIMIT,
) -> dict[str, Any]:
    """List bounded current quest owners without authorizing a source edit."""

    workspace = Path(
        os.path.abspath(os.fspath(Path(workspace_root).expanduser()))
    )
    try:
        workspace_state = workspace.lstat()
    except OSError as exc:
        raise QuestForProcessError("cannot inspect quest workspace") from exc
    if stat.S_ISLNK(workspace_state.st_mode) or not stat.S_ISDIR(
        workspace_state.st_mode
    ):
        _fail("quest workspace is not an ordinary directory")
    workspace = workspace.resolve()
    if query is not None and (
        type(query) is not str
        or not query.strip()
        or len(query) > 256
        or any(not character.isprintable() for character in query)
    ):
        _fail("quest option query must be 1 to 256 printable characters")
    if (
        type(limit) is not int
        or type(limit) is bool
        or not 1 <= limit <= MAX_OPTIONS_LIMIT
    ):
        _fail(
            f"quest option limit must be between 1 and {MAX_OPTIONS_LIMIT}"
        )

    quests, _graph, graph_binding = _quest_sources(workspace)
    language_raw = _regular_bytes(
        workspace, QUEST_LANGUAGE, "quest localization"
    )
    language_lines, _line_ending, _trailing_newline = _language_lines(
        language_raw
    )
    language_values = _language_values(language_lines)
    normalized_query = None if query is None else query.strip().casefold()
    matches: list[dict[str, Any]] = []
    for quest_id in sorted(quests):
        quest = quests[quest_id]
        title_key, _description_key = _quest_language_keys(quest["value"])
        title_values = language_values.get(title_key, [])
        localized_title = title_values[0] if len(title_values) == 1 else None
        localization_state = (
            "resolved"
            if len(title_values) == 1
            else "missing"
            if not title_values
            else "ambiguous"
        )
        (
            _prerequisites_key,
            _prerequisite_types_key,
            prerequisites,
            prerequisite_types,
        ) = _prerequisite_fields(
            quest["value"], f"quest source {quest['path']}"
        )
        padded_types = [
            *prerequisite_types,
            *(
                [REQUIREMENT_TYPES["NORMAL"]]
                * (len(prerequisites) - len(prerequisite_types))
            ),
        ]
        option = {
            "current_prerequisites": [
                {
                    "quest_id": prerequisite_id,
                    "requirement_type": REQUIREMENT_TYPE_NAMES[
                        padded_types[index]
                    ],
                }
                for index, prerequisite_id in enumerate(prerequisites)
            ],
            "localization_state": localization_state,
            "localized_title": localized_title,
            "path": quest["path"],
            "quest_id": quest_id,
            "title_key": title_key,
        }
        searchable = (
            str(quest_id),
            title_key,
            "" if localized_title is None else localized_title,
        )
        if normalized_query is None or any(
            normalized_query in value.casefold() for value in searchable
        ):
            matches.append(option)

    selected = matches[:limit]
    return {
        "authority_boundary": {
            "construction_authority": False,
            "mutation_authorized": False,
            "profile_owner": "supersymmetry-quest-for-process",
            "runtime_verified": False,
        },
        "format": OPTIONS_FORMAT,
        "limit": limit,
        "matched_count": len(matches),
        "options": selected,
        "query": query,
        "returned_count": len(selected),
        "schema_version": 1,
        "source_binding": graph_binding,
        "state": "available",
        "total_count": len(quests),
        "truncated": len(selected) < len(matches),
        "workspace_uri": workspace.as_uri(),
    }


def _replace_language(
    lines: list[str], key: str, replacement: str | None
) -> tuple[list[str], str]:
    matches = [index for index, line in enumerate(lines) if line.startswith(key + "=")]
    if len(matches) != 1:
        _fail(f"quest localization key is absent or duplicated: {key}")
    current = lines[matches[0]].split("=", 1)[1]
    if replacement is None:
        return lines, current
    if not replacement or any(character in replacement for character in "\r\n"):
        _fail(f"quest localization value for {key} is invalid")
    result = list(lines)
    result[matches[0]] = f"{key}={replacement}"
    return result, current


def _operation(path: str, before: bytes, after: bytes, role: str) -> dict[str, Any]:
    if before == after:
        _fail(f"quest update produced no change for {path}")
    return {
        "operation": "update",
        "path": path,
        "role": role,
        "before_sha256": hashlib.sha256(before).hexdigest(),
        "before_size": len(before),
        "content": after,
        "content_sha256": hashlib.sha256(after).hexdigest(),
        "content_size": len(after),
    }


def validate_quest_for_process_render(
    value: Mapping[str, Any],
    *,
    before_bytes: Mapping[str, bytes],
) -> dict[str, Any]:
    """Validate exact request-to-source semantics without consulting a checkout.

    The caller supplies the two source snapshots used by the renderer.  This
    keeps retained plans reviewable after a checkout moves and prevents a
    self-sealed receipt from authorizing bytes the profile would not render.
    """

    if type(value) is not dict or set(value) != {
        "evidence",
        "format",
        "limitations",
        "operations",
        "request",
        "schema_version",
        "state",
    }:
        _fail("quest-for-process render fields changed")
    rendered = dict(value)
    if (
        rendered.get("format") != RENDER_FORMAT
        or type(rendered.get("schema_version")) is not int
        or rendered.get("schema_version") != 1
        or rendered.get("state") != RENDER_STATE
        or rendered.get("limitations") != list(RENDER_LIMITATIONS)
    ):
        _fail("quest-for-process render identity changed")
    request = _validated_request(rendered.get("request"))
    evidence = rendered.get("evidence")
    if type(evidence) is not dict or set(evidence) != {
        "localization",
        "prerequisite_graph",
        "quest_path",
        "quest_source_sha256",
        "source_checks",
    }:
        _fail("quest-for-process source evidence changed")
    quest_path = _quest_path(evidence.get("quest_path"), request["quest_id"])
    graph = evidence.get("prerequisite_graph")
    localization = evidence.get("localization")
    source_checks = evidence.get("source_checks")
    if (
        type(graph) is not dict
        or set(graph) != {"file_count", "sha256"}
        or type(graph.get("file_count")) is not int
        or not 1 <= graph["file_count"] <= MAX_QUEST_FILES
        or type(graph.get("sha256")) is not str
        or _DIGEST.fullmatch(graph["sha256"]) is None
        or type(evidence.get("quest_source_sha256")) is not str
        or _DIGEST.fullmatch(evidence["quest_source_sha256"]) is None
        or type(localization) is not dict
        or set(localization)
        != {
            "description_before",
            "description_key",
            "title_before",
            "title_key",
        }
        or any(type(item) is not str for item in localization.values())
        or source_checks
        != {
            "localization_keys_unique": True,
            "new_edge_creates_cycle": False,
            "prerequisite_exists": True,
            "quest_exists": True,
        }
    ):
        _fail("quest-for-process source evidence identity changed")

    required_sources = {quest_path, QUEST_LANGUAGE.as_posix()}
    if type(before_bytes) is not dict or set(before_bytes) != required_sources:
        _fail("quest-for-process source snapshots changed")
    if any(
        type(raw) is not bytes or not 1 <= len(raw) <= MAX_SOURCE_BYTES
        for raw in before_bytes.values()
    ):
        _fail("quest-for-process source snapshot bytes changed")

    quest_raw = before_bytes[quest_path]
    quest_value, line_ending, trailing_newline, ensure_ascii = _decode_json(
        quest_raw, "selected quest snapshot"
    )
    if ensure_ascii is None:
        _fail("selected quest does not use the supported stable JSON layout")
    quest_key = _typed_key(quest_value, "questID", "selected quest snapshot")
    if _quest_identifier(quest_value[quest_key], "selected quest identity") != request[
        "quest_id"
    ]:
        _fail("quest source path and identity differ")
    _prerequisite_fields(quest_value, "selected quest snapshot")
    if hashlib.sha256(quest_raw).hexdigest() != evidence["quest_source_sha256"]:
        _fail("quest source evidence and snapshot differ")

    updated_quest = _updated_quest_value(quest_value, request)
    quest_after = _render_json(
        updated_quest,
        line_ending,
        trailing_newline,
        ensure_ascii=ensure_ascii,
    )
    title_key, description_key = _quest_language_keys(updated_quest)
    language_raw = before_bytes[QUEST_LANGUAGE.as_posix()]
    language_lines, language_ending, language_trailing = _language_lines(language_raw)
    language_lines, title_before = _replace_language(
        language_lines, title_key, request["title"]
    )
    language_lines, description_before = _replace_language(
        language_lines, description_key, request["description"]
    )
    language_text = language_ending.join(language_lines)
    if language_trailing:
        language_text += language_ending
    language_after = language_text.encode("utf-8")
    if localization != {
        "description_before": description_before,
        "description_key": description_key,
        "title_before": title_before,
        "title_key": title_key,
    }:
        _fail("quest localization evidence and snapshots differ")

    expected_operations: list[dict[str, Any]] = []
    if quest_after != quest_raw:
        expected_operations.append(
            _operation(quest_path, quest_raw, quest_after, "quest-definition")
        )
    if language_after != language_raw:
        expected_operations.append(
            _operation(
                QUEST_LANGUAGE.as_posix(),
                language_raw,
                language_after,
                "quest-localization",
            )
        )
    if not expected_operations:
        _fail("quest update is identical to the selected checkout")
    if rendered.get("operations") != expected_operations:
        _fail("quest-for-process operations do not implement the request")
    return rendered


def render_quest_for_process_update(
    workspace_root: Path | str,
    *,
    quest_id: int,
    add_prerequisite_id: int | None = None,
    requirement_type: str = "IMPLICIT",
    title: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Render exact bytes for one existing quest and its existing lang keys."""

    workspace = Path(
        os.path.abspath(os.fspath(Path(workspace_root).expanduser()))
    )
    try:
        workspace_state = workspace.lstat()
    except OSError as exc:
        raise QuestForProcessError("cannot inspect quest workspace") from exc
    if stat.S_ISLNK(workspace_state.st_mode) or not stat.S_ISDIR(
        workspace_state.st_mode
    ):
        _fail("quest workspace is not an ordinary directory")
    workspace = workspace.resolve()
    request = _validated_request(
        {
            "add_prerequisite_id": add_prerequisite_id,
            "description": description,
            "quest_id": quest_id,
            "requirement_type": (
                requirement_type if add_prerequisite_id is not None else None
            ),
            "title": title,
        }
    )
    selected_id = request["quest_id"]

    quests, graph, graph_binding = _quest_sources(workspace)
    selected = quests.get(selected_id)
    if selected is None:
        _fail(f"selected quest does not exist: {selected_id}")
    if selected["ensure_ascii"] is None:
        _fail("selected quest does not use the supported stable JSON layout")
    prior_prerequisites = list(graph[selected_id])
    if add_prerequisite_id is not None:
        prerequisite_id = request["add_prerequisite_id"]
        if prerequisite_id == selected_id:
            _fail("a quest cannot require itself")
        if prerequisite_id not in quests:
            _fail(f"prerequisite quest does not exist: {prerequisite_id}")
        if prerequisite_id in prior_prerequisites:
            _fail("selected prerequisite already exists on the quest")
        if _reaches(graph, prerequisite_id, selected_id):
            _fail("adding the prerequisite would create a quest cycle")
    quest_value = _updated_quest_value(selected["value"], request)

    quest_after = _render_json(
        quest_value,
        selected["line_ending"],
        selected["trailing_newline"],
        ensure_ascii=selected["ensure_ascii"],
    )
    operations: list[dict[str, Any]] = []
    if quest_after != selected["raw"]:
        operations.append(
            _operation(selected["path"], selected["raw"], quest_after, "quest-definition")
        )

    language_raw = _regular_bytes(workspace, QUEST_LANGUAGE, "quest localization")
    language_lines, language_ending, language_trailing = _language_lines(language_raw)
    title_key, description_key = _quest_language_keys(quest_value)
    language_lines, prior_title = _replace_language(language_lines, title_key, title)
    language_lines, prior_description = _replace_language(
        language_lines, description_key, description
    )
    language_text = language_ending.join(language_lines)
    if language_trailing:
        language_text += language_ending
    language_after = language_text.encode("utf-8")
    if language_after != language_raw:
        operations.append(
            _operation(
                QUEST_LANGUAGE.as_posix(),
                language_raw,
                language_after,
                "quest-localization",
            )
        )
    if not operations:
        _fail("quest update is identical to the selected checkout")

    result = {
        "format": RENDER_FORMAT,
        "schema_version": 1,
        "state": RENDER_STATE,
        "request": request,
        "operations": operations,
        "evidence": {
            "localization": {
                "description_key": description_key,
                "description_before": prior_description,
                "title_key": title_key,
                "title_before": prior_title,
            },
            "prerequisite_graph": graph_binding,
            "quest_path": selected["path"],
            "quest_source_sha256": hashlib.sha256(selected["raw"]).hexdigest(),
            "source_checks": {
                "localization_keys_unique": True,
                "new_edge_creates_cycle": False,
                "prerequisite_exists": add_prerequisite_id is None
                or add_prerequisite_id in quests,
                "quest_exists": True,
            },
        },
        "limitations": list(RENDER_LIMITATIONS),
    }
    return validate_quest_for_process_render(
        result,
        before_bytes={
            selected["path"]: selected["raw"],
            QUEST_LANGUAGE.as_posix(): language_raw,
        },
    )


__all__ = [
    "QUEST_LANGUAGE",
    "QUEST_ROOT",
    "RENDER_FORMAT",
    "RENDER_LIMITATIONS",
    "QuestForProcessError",
    "render_quest_for_process_update",
    "validate_quest_for_process_render",
]
