"""Declared saved-source inputs for the qualified finite Forge recipe model.

The source manifest is a value contract: no Core or Project Intelligence
implementation is imported and no filesystem is read. Its custody ID remains
bound to the original observation, while its content and pack IDs exclude the
workstation root. Validation establishes consistent declarations, not proof that
the game loaded those files. Crucible separately binds these bytes to a capture.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any
import unicodedata
from urllib.parse import urlsplit

from .finite_item_matching import ARTIFACT_SHA256


INPUT_FORMAT = "workbench-supersymmetry-developer-observation-input-v1"
SOURCE_FORMAT = "workbench-supersymmetry-saved-recipe-source-v1"
QUALIFIED_PLATFORM = {"minecraft_version": "1.12.2", "loader": "forge",
                      "loader_version": "14.23.5.2860", "java_major": 8}
QUALIFIED_ARTIFACTS = {
    **ARTIFACT_SHA256,
    "groovyscript_sha256": "07617b7ce9170a857199bd61d730a0db3af2685cf83d31734a2d4b628fda7533",
    "susy_core_sha256": "ec9b56070f8788b63f5d381c765cd05ed66ba42755e1f87ad2eb6605285a5a46",
}
_PREPARATION = {"policy": "forge-loli-original-capability-materialization-v1",
                "phase": "before-two-effective-samples"}
_PACK_PREFIX = "supersymmetry-saved-recipe-source:sha256:"
_PLATFORM_PREFIX = "forge-developer-observation:sha256:"
_SOURCE_FIELDS = {"root_uri", "revision", "dirty", "file_count", "source_sha256"}
_INPUT_FIELDS = {"format", "capture_id", "launch_id", "physical_side", "pack_source",
                 "platform", "runtime_artifacts", "pack_binding_id", "platform_binding_id",
                 "candidate_lock_sha256", "adapter_profile_sha256"}
_RESERVED = re.compile(r"(?:con|prn|aux|nul|conin\$|conout\$|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?", re.IGNORECASE)


class RecipeCaptureInputError(ValueError):
    """Declared inputs do not satisfy the selected recipe capture model."""


def _json(value: Any, *, ascii_only: bool = False) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=ascii_only, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise RecipeCaptureInputError("capture input is not canonical JSON data") from exc


def _digest(value: Any, *, ascii_only: bool = False) -> str:
    return hashlib.sha256(_json(value, ascii_only=ascii_only)).hexdigest()


# Public dictionaries are convenient caller templates, never mutable policy.
_QUALIFIED_PLATFORM_BYTES = _json(QUALIFIED_PLATFORM)
_QUALIFIED_ARTIFACT_BYTES = _json(QUALIFIED_ARTIFACTS)


def _object(value: Any, fields: set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise RecipeCaptureInputError(f"invalid {name} fields")
    return value


def _text(value: Any, name: str) -> str:
    if type(value) is not str or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise RecipeCaptureInputError(f"invalid {name}")
    _json(value)
    return value


def _hash(value: Any, name: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise RecipeCaptureInputError(f"invalid {name} digest")
    return value


def _integer(value: Any, name: str, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise RecipeCaptureInputError(f"invalid {name}")
    return value


def _path(value: Any) -> str:
    name = _text(value, "source path")
    path = PurePosixPath(name)
    if (not path.parts or path.is_absolute() or path.as_posix() != name
            or any(c in name for c in '\\:<>"|?*')
            or any(part in {".", ".."} or part.casefold() == ".git"
                   or part.endswith((".", " ")) or _RESERVED.fullmatch(part)
                   for part in path.parts)):
        raise RecipeCaptureInputError("source path is not a portable relative file path")
    return name


def _candidate(candidate: Any) -> dict[str, Any]:
    _object(candidate, {"format", "source", "files", "id"}, "saved candidate")
    if candidate["format"] != "workbench-saved-candidate-v1":
        raise RecipeCaptureInputError("unsupported saved candidate format")
    source = candidate["source"]
    if (type(source) is not dict or set(source) not in
            (_SOURCE_FIELDS | {"index_sha256"}, _SOURCE_FIELDS | {"kind"})):
        raise RecipeCaptureInputError("invalid saved candidate source fields")
    root_uri = _text(source["root_uri"], "source root URI")
    try:
        parsed = urlsplit(root_uri)
    except ValueError as exc:
        raise RecipeCaptureInputError("invalid source root URI") from exc
    if parsed.scheme != "file" or not parsed.path.startswith("/") or parsed.query or parsed.fragment:
        raise RecipeCaptureInputError("source root must be an absolute file URI")
    if type(source["revision"]) is not str or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", source["revision"]) is None:
        raise RecipeCaptureInputError("source revision is not an exact Git object ID")
    if type(source["dirty"]) is not bool:
        raise RecipeCaptureInputError("source dirty state must be explicit")
    _integer(source["file_count"], "source file count", 100_000)
    _hash(source["source_sha256"], "source observation")
    if "index_sha256" in source:
        _hash(source["index_sha256"], "source index")
    elif source["kind"] != "local-git-commit" or source["dirty"]:
        raise RecipeCaptureInputError("unsupported saved baseline source kind")
    files = candidate["files"]
    if type(files) is not list or len(files) > 100_000:
        raise RecipeCaptureInputError("invalid saved candidate file table")
    paths, total = [], 0
    for row in files:
        _object(row, {"path", "mode", "size", "sha256"}, "saved file")
        paths.append(_path(row["path"]))
        if type(row["mode"]) is not int or row["mode"] not in (0o100644, 0o100755):
            raise RecipeCaptureInputError("saved file requires an ordinary Git file mode")
        total += _integer(row["size"], "saved file size", 64 * 1024**2)
        _hash(row["sha256"], "saved file")
    if total > 512 * 1024**2 or paths != sorted(paths):
        raise RecipeCaptureInputError("saved file table is oversized or not canonical")
    body = {key: value for key, value in candidate.items() if key != "id"}
    # Project Intelligence's existing saved-candidate V1 identity escapes Unicode.
    if candidate["id"] != "candidate:sha256:" + _digest(body, ascii_only=True):
        raise RecipeCaptureInputError("saved candidate identity differs from its contents")
    return candidate


def _portable_tree(paths: list[str]) -> None:
    """Admit each directory spelling as well as each file on every host."""
    spellings: dict[tuple[str, ...], str] = {}
    files: set[tuple[str, ...]] = set()
    directories: set[tuple[str, ...]] = set()
    for path in paths:
        parts = tuple(path.split("/"))
        keys = tuple(unicodedata.normalize("NFC", part.casefold()) for part in parts)
        for index, part in enumerate(parts):
            prefix = keys[:index + 1]
            leaf = index == len(parts) - 1
            if (leaf and prefix in directories) or (not leaf and prefix in files):
                raise RecipeCaptureInputError("source path collides as both file and directory")
            if prefix in spellings and spellings[prefix] != part:
                raise RecipeCaptureInputError("source path components collide by case or normalization")
            spellings[prefix] = part
        if keys in files:
            raise RecipeCaptureInputError("duplicate portable source path")
        files.add(keys)
        directories.update(keys[:length] for length in range(1, len(keys)))


def build_source_binding(candidate: dict[str, Any], *, deleted_paths: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    """Bind complete saved-file declarations without choosing paths or reading files.

    Saved-candidate V1 omits deleted filenames but counts them in source.file_count.
    The caller must provide those names explicitly. Their count must reconcile;
    this helper cannot independently establish their origin or completeness.
    """
    _candidate(candidate)
    if type(deleted_paths) not in (list, tuple):
        raise RecipeCaptureInputError("deleted source paths must be a canonical array")
    deleted = [_path(path) for path in deleted_paths]
    if deleted != sorted(deleted):
        raise RecipeCaptureInputError("deleted source paths are not canonical")
    paths = [row["path"] for row in candidate["files"]] + deleted
    if len(paths) != candidate["source"]["file_count"]:
        raise RecipeCaptureInputError("source paths do not reconcile with observation count")
    _portable_tree(paths)
    if deleted and (not candidate["source"]["dirty"] or "kind" in candidate["source"]):
        raise RecipeCaptureInputError("deleted source requires a dirty working-tree observation")
    content = _digest({"files": candidate["files"], "deleted_paths": deleted})
    identity = _PACK_PREFIX + _digest({"revision": candidate["source"]["revision"],
                                      "source_content_sha256": content})
    return {"format": SOURCE_FORMAT, "id": identity, "candidate": deepcopy(candidate),
            "deleted_paths": deleted, "source_content_sha256": content}


def validate_source_binding(value: dict[str, Any]) -> str:
    """Revalidate the complete declaration and return its portable pack identity."""
    _object(value, {"format", "id", "candidate", "deleted_paths", "source_content_sha256"}, "source binding")
    if type(value["deleted_paths"]) is not list:
        raise RecipeCaptureInputError("deleted source paths must be an array")
    expected = build_source_binding(value["candidate"], deleted_paths=value["deleted_paths"])
    if _json(value) != _json(expected):
        raise RecipeCaptureInputError("saved source binding differs from its contents")
    return expected["id"]


def _runtime(platform: Any, artifacts: Any) -> str:
    if (_json(platform) != _QUALIFIED_PLATFORM_BYTES
            or _json(artifacts) != _QUALIFIED_ARTIFACT_BYTES):
        raise RecipeCaptureInputError("unsupported developer recipe runtime; requires the qualified Forge/Java 8 artifact tuple")
    return _PLATFORM_PREFIX + _digest({"platform": platform, "runtime_artifacts": artifacts})


def build_capture_input(candidate: dict[str, Any], *, platform: dict[str, Any],
                        runtime_artifacts: dict[str, Any], capture_id: str, launch_id: str,
                        candidate_lock_sha256: str, adapter_profile_sha256: str,
                        deleted_paths: list[str] | tuple[str, ...] = (),
                        observation_preparation: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a declared input binding; caller owns capture, storage and execution."""
    source = build_source_binding(candidate, deleted_paths=deleted_paths)
    result = {"format": INPUT_FORMAT, "pack_source": source,
              "pack_binding_id": source["id"],
              "platform": deepcopy(platform), "runtime_artifacts": deepcopy(runtime_artifacts),
              "platform_binding_id": _runtime(platform, runtime_artifacts),
              "capture_id": capture_id, "launch_id": launch_id, "physical_side": "dedicated_server",
              "candidate_lock_sha256": candidate_lock_sha256,
              "adapter_profile_sha256": adapter_profile_sha256}
    if observation_preparation is not None:
        result["observation_preparation"] = deepcopy(observation_preparation)
    validate_capture_input(result)
    return result


def validate_capture_input(value: dict[str, Any]) -> tuple[str, str]:
    """Validate declarations only; sealed capture and preparation admission follow."""
    if type(value) is not dict or set(value) not in (_INPUT_FIELDS, _INPUT_FIELDS | {"observation_preparation"}):
        raise RecipeCaptureInputError("invalid developer capture input fields")
    if value["format"] != INPUT_FORMAT or value["physical_side"] != "dedicated_server":
        raise RecipeCaptureInputError("unsupported developer capture format or physical side")
    for field in ("capture_id", "launch_id"):
        _text(value[field], field)
    for field in ("candidate_lock_sha256", "adapter_profile_sha256"):
        _hash(value[field], field)
    pack_id = validate_source_binding(value["pack_source"])
    platform_id = _runtime(value["platform"], value["runtime_artifacts"])
    if value["pack_binding_id"] != pack_id or value["platform_binding_id"] != platform_id:
        raise RecipeCaptureInputError("developer capture binding identity differs")
    if "observation_preparation" in value and _json(value["observation_preparation"]) != _json(_PREPARATION):
        raise RecipeCaptureInputError("unsupported developer capture preparation policy")
    return pack_id, platform_id
