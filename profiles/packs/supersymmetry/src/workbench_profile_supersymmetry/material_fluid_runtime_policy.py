"""Supersymmetry construction policy for the material-fluid runtime trial.

This profile authority approves exactly one projection-only compatibility
bridge.  Atlas observes the resulting runtime state; it does not authorize
or apply this mutation.
"""

from __future__ import annotations

PROFILE_API_VERSION = 1

from hashlib import sha256
import json
from pathlib import Path
from typing import Any


_PATCH_PATH = Path(
    "profiles/packs/supersymmetry/compatibility/"
    "recurrent-complex-1.4.8.6-structure-context-arg3-v1.json"
)
_PATCH_ID = (
    "workbench-pack:supersymmetry:"
    "recurrent-complex-1.4.8.6-structure-context-arg3-v1"
)
_SPEC_SHA256 = "c9ca5da88b005f85ab443192d2d9188805f97ab1109e868da48d82e7759a8b70"
_TARGET_SHA256 = "253226e6c7efe61ae255df0cc2e19d1420945cb7e86f7cd79f51d5db10fd9de8"
_ENTRY_SHA256 = "3690b5852458c0dac37e5ef4fdb5ab38faee7bbea94351bae41211dea7902be1"
_TARGET_PATH = ".minecraft/mods/RecurrentComplex-1.4.8.6.jar"
_TARGET_ENTRY = (
    "ivorius/reccomplex/world/gen/feature/structure/context/"
    "StructureSpawnContext.class"
)


class MaterialFluidRuntimePolicyError(ValueError):
    """The fixed profile construction policy is unavailable or drifted."""


def _fail(message: str) -> None:
    raise MaterialFluidRuntimePolicyError(message)


def material_fluid_feature_projection() -> dict[str, Any]:
    """Return the pack-owned construction facts rendered by Feature Studio."""

    return {
        "format": "workbench-supersymmetry-material-fluid-feature-projection-v1",
        "schema_version": 1,
        "profile_family_id": "workbench-pack:supersymmetry",
        "feature_kind": "material-backed-fluid",
        "registry_namespace": "susy",
        "physical_side": "client",
        "source_owners": [
            {
                "relative_path": "groovy/material/PetrochemistryMaterials.groovy",
                "role": "material-registration",
            },
            {
                "relative_path": "groovy/material/SuSyMaterials.groovy",
                "role": "material-declaration",
            },
            {
                "relative_path": "resources/langfiles/lang/en_us.lang",
                "role": "client-localization",
            },
        ],
    }


def material_fluid_runtime_compatibility_policy(
    suite_root: Path | str,
) -> dict[str, Any]:
    """Return the one exact profile-authorized disposable patch set."""

    path = Path(suite_root).resolve() / _PATCH_PATH
    if path.is_symlink() or not path.is_file():
        _fail("Supersymmetry material-fluid compatibility policy is unavailable")
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaterialFluidRuntimePolicyError(
            "Supersymmetry material-fluid compatibility policy is invalid"
        ) from exc
    target = value.get("target") if isinstance(value, dict) else None
    spec_sha256 = sha256(raw).hexdigest()
    if (
        len(raw) > 64 * 1024
        or spec_sha256 != _SPEC_SHA256
        or not isinstance(value, dict)
        or set(value)
        != {"description", "format", "patch_id", "schema_version", "target"}
        or value.get("format") != "workbench-runtime-compatibility-patch-v1"
        or value.get("schema_version") != 1
        or value.get("patch_id") != _PATCH_ID
        or not isinstance(target, dict)
        or set(target)
        != {
            "entry",
            "entry_sha256",
            "expected_matches",
            "find_utf8",
            "path",
            "replace_utf8",
            "sha256",
        }
        or target.get("path") != _TARGET_PATH
        or target.get("sha256") != _TARGET_SHA256
        or target.get("entry") != _TARGET_ENTRY
        or target.get("entry_sha256") != _ENTRY_SHA256
        or target.get("find_utf8") != "flag"
        or target.get("replace_utf8") != "arg3"
        or target.get("expected_matches") != 1
    ):
        _fail("Supersymmetry material-fluid compatibility policy drifted")
    return {
        "format": "workbench-supersymmetry-material-fluid-compatibility-policy-v1",
        "schema_version": 1,
        "authority": {
            "owner": "Supersymmetry profile construction authority",
            "role": "projection-only-runtime-compatibility",
        },
        "mode": "required-exact-profile-patch-set",
        "patches": [
            {
                "expected_matches": 1,
                "find_utf8": "flag",
                "patch_id": _PATCH_ID,
                "replace_utf8": "arg3",
                "spec_sha256": spec_sha256,
                "spec_uri": path.as_uri(),
                "target_entry": _TARGET_ENTRY,
                "target_entry_sha256": _ENTRY_SHA256,
                "target_path": _TARGET_PATH,
                "target_sha256": _TARGET_SHA256,
            }
        ],
    }


__all__ = [
    "MaterialFluidRuntimePolicyError",
    "material_fluid_feature_projection",
    "material_fluid_runtime_compatibility_policy",
]
