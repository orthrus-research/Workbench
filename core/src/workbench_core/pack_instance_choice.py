"""Core-owned, recoverable selection of a Supersymmetry install source.

The dotfile stores choices, never an imported archive or instance payload.
Callers must reopen the selected Core source before using its plan ID.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from workbench_api.host_filesystem import DurableRecordError

from .durable_records import read_private_bytes
from .preference_records import update_preference_bytes


FORMAT = "workbench-pack-instance-choice-v1"
_SOURCE_PLAN_ID = re.compile(r"workbench-pack-release-client-composition-plan:sha256:[0-9a-f]{64}\Z")
_RECORD_ID = re.compile(r"workbench-pack-instance-choice:sha256:[0-9a-f]{64}\Z")
_MAX_BYTES = 16 * 1024


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _record(source_kind: str | None, source_plan_id: str | None,
            launcher_root: str | None, workspace_name: str | None) -> dict[str, Any]:
    if source_kind not in {None, "user-prism-zip", "published-release"}:
        raise ValueError("unsupported pack instance source kind")
    if (source_kind is None) != (source_plan_id is None):
        raise ValueError("pack instance source kind and plan ID must be selected together")
    if source_plan_id is not None and _SOURCE_PLAN_ID.fullmatch(source_plan_id) is None:
        raise ValueError("invalid pack instance source plan ID")
    if launcher_root is not None and (not isinstance(launcher_root, str)
                                      or not Path(launcher_root).is_absolute()):
        raise ValueError("Prism launcher root must be an absolute path")
    if workspace_name is not None and (not isinstance(workspace_name, str)
                                       or not workspace_name or len(workspace_name) > 128
                                       or any(ord(char) < 32 for char in workspace_name)):
        raise ValueError("invalid selected workspace name")
    body = {
        "format": FORMAT, "schema_version": 1, "profile": "supersymmetry",
        "source_kind": source_kind, "source_plan_id": source_plan_id,
        "launcher_root": launcher_root, "workspace_name": workspace_name,
    }
    return {**body, "record_id": "workbench-pack-instance-choice:sha256:"
            + sha256(_canonical(body)).hexdigest()}


def _decode(raw: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate pack instance choice key")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("invalid pack instance choice JSON") from exc
    if type(value) is not dict or set(value) != {
        "format", "schema_version", "profile", "source_kind", "source_plan_id",
        "launcher_root", "workspace_name", "record_id",
    } or value.get("schema_version") != 1 or value.get("profile") != "supersymmetry":
        raise ValueError("unsupported pack instance choice")
    expected = _record(value["source_kind"], value["source_plan_id"],
                       value["launcher_root"], value["workspace_name"])
    if value != expected or _RECORD_ID.fullmatch(value["record_id"]) is None:
        raise ValueError("pack instance choice identity differs")
    return value


def choice_path(config_home: Path) -> Path:
    if not isinstance(config_home, Path) or not config_home.is_absolute():
        raise ValueError("pack instance config home must be absolute")
    return config_home / "pack-instance-supersymmetry.json"


def load_pack_instance_choice(config_home: Path) -> dict[str, Any]:
    path = choice_path(config_home)
    if not path.exists() and not path.is_symlink():
        if any(parent.is_symlink() for parent in (path.parent, *path.parent.parents)):
            raise ValueError("pack instance choice directory is a symbolic link")
        return _record(None, None, None, None)
    try:
        raw = read_private_bytes(path, byte_limit=_MAX_BYTES)
    except DurableRecordError as exc:
        if exc.code != "unavailable" or path.exists() or path.is_symlink():
            raise
        return _record(None, None, None, None)
    return _decode(raw)


def save_pack_instance_choice(
    config_home: Path, *, source_kind: str | None, source_plan_id: str | None,
    launcher_root: str | None, workspace_name: str | None,
    expected_record_id: str,
) -> dict[str, Any]:
    """Atomically save Textual's reviewed choices after a Core source reopen."""
    if not isinstance(expected_record_id, str) or _RECORD_ID.fullmatch(expected_record_id) is None:
        raise ValueError("select a reviewed pack instance choice revision")
    proposed = _record(source_kind, source_plan_id, launcher_root, workspace_name)

    def update(previous: bytes | None) -> bytes:
        current = _record(None, None, None, None) if previous is None else _decode(previous)
        if current["record_id"] != expected_record_id:
            raise ValueError("pack instance choices changed after review")
        return _canonical(proposed) + b"\n"

    update_preference_bytes(choice_path(config_home), update, byte_limit=_MAX_BYTES)
    return proposed


__all__ = ["choice_path", "load_pack_instance_choice", "save_pack_instance_choice"]
