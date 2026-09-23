"""Read-only Workspace Home contract for the owned Cleanroom build fixture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from . import profile
from . import fixture_build


PROFILE_API_VERSION = 1

_PROFILE_ROOT = profile().root
_SCHEMA = _PROFILE_ROOT / "schemas/workbench-cleanroom-generic-mod-fixture-lock-v1.schema.json"
_TOOL = _PROFILE_ROOT / "tools/run_generic_mod_fixture_build.py"


def fixture_root() -> Path:
    """The sole canonical fixture accepted by this profile."""

    return fixture_build.FIXTURE.resolve(strict=True)


def _json_object(path: Path, label: str) -> dict[str, Any]:
    raw = fixture_build._read_ordinary_bytes(
        path, label=label, limit=fixture_build.MAX_LOCK_BYTES
    )
    value = json.loads(raw.decode("utf-8"))
    if type(value) is not dict:
        raise ValueError(f"{label} must be an object")
    return value


def read_owner_lock() -> dict[str, Any]:
    return _json_object(fixture_build.LOCK, "Cleanroom fixture owner lock")


def validate_owner_lock(value: Mapping[str, Any]) -> dict[str, Any]:
    """Reopen current owner bytes, schema, and the complete fixture tree."""

    current = read_owner_lock()
    if value != current:
        raise ValueError("Cleanroom fixture owner lock changed during validation")
    schema = _json_object(_SCHEMA, "Cleanroom fixture owner schema")
    Draft202012Validator.check_schema(schema)
    if any(Draft202012Validator(schema).iter_errors(current)):
        raise ValueError("Cleanroom fixture owner lock differs from its dedicated schema")
    if current.get("declared_values", {}).get("tree_digest") != fixture_build._validate_fixture():
        raise ValueError("Cleanroom fixture tree differs from its owner lock")
    return current


def source_inputs() -> tuple[dict[str, Any], ...]:
    """Exact profile resources Home retains as preflight input witnesses."""

    return (
        {"kind": "fixture-owner-lock", "path": fixture_build.LOCK,
         "display_path": "profiles/platforms/cleanroom/fixtures/generic-mod-daily-loop/fixture-lock-v1.json"},
        {"kind": "fixture-owner-schema", "path": _SCHEMA,
         "display_path": "profiles/platforms/cleanroom/schemas/workbench-cleanroom-generic-mod-fixture-lock-v1.schema.json"},
        {"kind": "profile-preflight-tool", "path": _TOOL,
         "display_path": "profiles/platforms/cleanroom/tools/run_generic_mod_fixture_build.py"},
    )


def cleanup_path() -> Path:
    return fixture_build.CLEANUP_INIT


def inspect_build_inputs(*, gradle_cmd: Path, java_home: Path) -> dict[str, Any]:
    return fixture_build.inspect_build_inputs(gradle_cmd=gradle_cmd, java_home=java_home)


def build_input_digest(inputs: dict[str, Any]) -> str:
    return fixture_build.build_input_digest(inputs)
