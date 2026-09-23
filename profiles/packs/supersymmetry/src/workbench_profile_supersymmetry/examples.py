"""Profile-owned catalog and validation for packaged Supersymmetry examples."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
from typing import Any, Mapping


CATALOG_FORMAT = "workbench-developer-feature-example-catalog-v1"
PLAN_RESULT_FORMAT = "workbench-developer-feature-example-plan-result-v1"
PROFILE = "supersymmetry"
MAX_EXAMPLE_BYTES = 512 * 1024
EXAMPLE_DESCRIPTORS = (
    {
        "family": "material-fluid-recipe",
        "example_key": "supersymmetry-material-fluid-recipe-radon",
        "path": (
            "profiles/packs/supersymmetry/blueprints/examples/"
            "material-fluid-recipe-radon.json"
        ),
        "runtime_section": "observed_run",
    },
    {
        "family": "recipe-change",
        "example_key": "supersymmetry-recipe-change-copper-sulfate-solution",
        "path": (
            "profiles/packs/supersymmetry/blueprints/examples/"
            "recipe-change-copper-sulfate-solution.json"
        ),
        "runtime_section": "runtime_observation",
    },
    {
        "family": "quest-for-process",
        "example_key": "supersymmetry-quest-for-process-gas-atomizer",
        "path": (
            "profiles/packs/supersymmetry/blueprints/examples/"
            "quest-for-process-gas-atomizer.json"
        ),
        "runtime_section": "runtime_observation",
    },
)
EXAMPLE_SELECTORS = tuple(
    [descriptor["family"] for descriptor in EXAMPLE_DESCRIPTORS]
    + [descriptor["example_key"] for descriptor in EXAMPLE_DESCRIPTORS]
)
EXAMPLE_KEYS = tuple(
    descriptor["example_key"] for descriptor in EXAMPLE_DESCRIPTORS
)
EXAMPLE_REQUEST_FIELDS = {
    "material-fluid-recipe": {
        "color",
        "duration",
        "input_amount",
        "input_fluid",
        "material_id",
        "name",
        "output_amount",
        "recipe_map",
        "recipe_map_registry_name",
        "recipe_script",
        "registry_name",
        "symbol",
        "translation",
        "voltage_tier",
    },
    "recipe-change": {
        "duration",
        "fluid_inputs",
        "fluid_outputs",
        "item_inputs",
        "item_outputs",
        "mutation",
        "recipe_map",
        "script",
        "voltage_tier",
    },
    "quest-for-process": {
        "add_prerequisite_id",
        "description",
        "quest_id",
        "requirement_type",
        "title",
    },
}
AUTHORITY_BOUNDARY = {
    "source": "profile-owned-non-identity-bearing-examples",
    "read_only": True,
    "construction_authority": False,
    "profile_support_claimed": False,
    "profile_action_authorized": False,
    "release_qualified": False,
    "publication_authorized": False,
}


class DeveloperFeatureExampleError(ValueError):
    """One explicitly selected packaged example is absent or untrustworthy."""


def _regular_example_bytes(suite_root: Path, relative: str) -> bytes:
    candidate = Path(relative)
    if (
        candidate.is_absolute()
        or ".." in candidate.parts
        or candidate.as_posix() != relative
    ):
        raise DeveloperFeatureExampleError("example path is unsafe")
    target = suite_root / candidate
    try:
        before = target.lstat()
    except OSError as exc:
        raise DeveloperFeatureExampleError(
            f"packaged Blueprint example is unavailable: {relative}"
        ) from exc
    if target.is_symlink() or not stat.S_ISREG(before.st_mode):
        raise DeveloperFeatureExampleError(
            f"packaged Blueprint example is not a regular file: {relative}"
        )
    if before.st_size > MAX_EXAMPLE_BYTES:
        raise DeveloperFeatureExampleError(
            f"packaged Blueprint example exceeds the byte bound: {relative}"
        )
    try:
        raw = target.read_bytes()
        after = target.lstat()
    except OSError as exc:
        raise DeveloperFeatureExampleError(
            f"packaged Blueprint example cannot be read: {relative}"
        ) from exc
    if (
        len(raw) != before.st_size
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
        or after.st_ino != before.st_ino
        or target.is_symlink()
    ):
        raise DeveloperFeatureExampleError(
            f"packaged Blueprint example changed while read: {relative}"
        )
    return raw


def _object(value: Any, *, label: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise DeveloperFeatureExampleError(f"{label} must be one JSON object")
    return value


def _load_example(
    suite_root: Path,
    descriptor: Mapping[str, str],
) -> dict[str, Any]:
    relative = descriptor["path"]
    raw = _regular_example_bytes(suite_root, relative)
    try:
        record = json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeveloperFeatureExampleError(
            f"packaged Blueprint example is not valid UTF-8 JSON: {relative}"
        ) from exc
    record = _object(record, label="example")
    if (
        record.get("format") != "workbench-blueprints-current-example-v1"
        or type(record.get("schema_version")) is not int
        or record["schema_version"] != 1
        or record.get("record_class") != "mutable-current-example"
        or record.get("identity_bearing") is not False
        or record.get("example_key") != descriptor["example_key"]
    ):
        raise DeveloperFeatureExampleError(
            f"packaged Blueprint example identity is invalid: {relative}"
        )
    request = _object(record.get("request"), label="example request")
    if set(request) != EXAMPLE_REQUEST_FIELDS[descriptor["family"]]:
        raise DeveloperFeatureExampleError(
            f"packaged Blueprint example request fields are invalid: {relative}"
        )
    boundary = _object(
        record.get("authority_boundary"),
        label="example authority boundary",
    )
    for claim in (
        "profile_tested_support",
        "profile_action_authorized",
        "release_qualified",
        "publication_authorized",
    ):
        if boundary.get(claim) is not False:
            raise DeveloperFeatureExampleError(
                f"packaged Blueprint example overclaims {claim}: {relative}"
            )
    runtime = _object(
        record.get(descriptor["runtime_section"]),
        label="example runtime evidence",
    )
    runtime_state = runtime.get("state")
    if type(runtime_state) is not str or not runtime_state:
        raise DeveloperFeatureExampleError(
            f"packaged Blueprint example runtime state is invalid: {relative}"
        )
    return {
        "family": descriptor["family"],
        "example_key": descriptor["example_key"],
        "source_path": relative,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_size": len(raw),
        "runtime_evidence_state": runtime_state,
        "record": dict(record),
    }


def load_feature_examples(
    suite_root: Path | str,
    *,
    selector: str | None = None,
) -> dict[str, Any]:
    """Load only the three bounded profile records embedded in the product."""

    if selector is not None and selector not in EXAMPLE_SELECTORS:
        expected = ", ".join(EXAMPLE_SELECTORS)
        raise DeveloperFeatureExampleError(
            f"unknown Blueprint example selector {selector!r}; expected one of: {expected}"
        )
    root = Path(suite_root).expanduser().absolute()
    selected = [
        descriptor
        for descriptor in EXAMPLE_DESCRIPTORS
        if selector is None
        or selector == descriptor["family"]
        or selector == descriptor["example_key"]
    ]
    examples = [_load_example(root, descriptor) for descriptor in selected]
    return {
        "format": CATALOG_FORMAT,
        "schema_version": 1,
        "state": "available",
        "profile": PROFILE,
        "selection": {
            "kind": (
                "all"
                if selector is None
                else "family"
                if selector in {row["family"] for row in EXAMPLE_DESCRIPTORS}
                else "example-key"
            ),
            "value": selector,
        },
        "count": len(examples),
        "examples": examples,
        "authority_boundary": dict(AUTHORITY_BOUNDARY),
    }


__all__ = [
    "AUTHORITY_BOUNDARY",
    "CATALOG_FORMAT",
    "DeveloperFeatureExampleError",
    "EXAMPLE_DESCRIPTORS",
    "EXAMPLE_KEYS",
    "EXAMPLE_REQUEST_FIELDS",
    "EXAMPLE_SELECTORS",
    "PLAN_RESULT_FORMAT",
    "load_feature_examples",
]
