"""Consumer-owned native source interpreter and declaration navigation contract."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from workbench_api.profile_extensions import (
    require_profile_extension,
    profile_extension_identity,
)
from workbench_api.profiles import profiles
from workbench_project_intelligence.working_tree import SourceInputs
from workbench_api import source_declarations as declaration_contract
from workbench_api import source_locations as location_contract
from workbench_api.source_declarations import MAX_DECLARATIONS, MAX_RELATIONSHIPS
from .declarations import declaration_set_identity, validate_source_declarations
from .model import PackProgramError

SOURCE_INTERPRETER_GROUP = "workbench.source_interpreters"
_LOADED_NORMALIZER = None


class SourceIntelligenceError(ValueError):
    pass


def source_interpreter(profile_id: str):
    owner = require_profile_extension(SOURCE_INTERPRETER_GROUP, profile_id)
    if not callable(getattr(owner, "source_declarations", None)):
        raise SourceIntelligenceError("profile has no source declaration interpreter")
    return owner


def semantic_key(kind: str, key: Mapping[str, Any], *, domain: str) -> dict[str, Any]:
    try:
        return declaration_contract.semantic_key(kind, key, domain=domain)
    except declaration_contract.SourceNavigationContractError as exc:
        raise SourceIntelligenceError(str(exc)) from exc


def key_identity(key: Mapping[str, Any]) -> str:
    try:
        return declaration_contract.key_identity(key)
    except declaration_contract.SourceNavigationContractError as exc:
        raise SourceIntelligenceError(str(exc)) from exc


def normalizer_identity() -> str:
    global _LOADED_NORMALIZER
    root = Path(__file__).parent
    files = [(path.name, path) for path in sorted(root.glob("*.py"))]
    # Navigation IDs bind the validators and coordinates that left this package,
    # as well as the producer implementation. Never accept changed loaded code.
    files.extend(
        (module.__name__, Path(module.__file__))
        for module in (declaration_contract, location_contract)
    )
    if len(files) > 256 or any(
        path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024
        for _, path in files
    ):
        raise SourceIntelligenceError("normalizer source exceeds its package bound")
    digest = sha256(
        b"\0".join(
            name.encode() + b"\0" + sha256(path.read_bytes()).digest()
            for name, path in files
        )
    ).hexdigest()
    if _LOADED_NORMALIZER is None:
        _LOADED_NORMALIZER = digest
    if digest != _LOADED_NORMALIZER:
        raise SourceIntelligenceError(
            "loaded normalizer source changed; restart the host"
        )
    return digest


def source_profile_resources(pack_profile, platform_profile):
    admitted = {profile.id: profile for profile in profiles()}
    resources = []
    for profile_id in (pack_profile, platform_profile):
        if profile_id not in admitted:
            raise SourceIntelligenceError("selected source profile is unavailable")
        for role in sorted(admitted[profile_id].resources):
            path = admitted[profile_id].resource(role)
            if path.stat().st_size > 4 * 1024 * 1024:
                raise SourceIntelligenceError(
                    "source profile resource exceeds its bound"
                )
            raw = path.read_bytes()
            resources.append(
                {
                    "profile": profile_id,
                    "role": role,
                    "sha256": sha256(raw).hexdigest(),
                    "size": len(raw),
                }
            )
    return resources


def build_navigation_declarations(
    inputs: SourceInputs, *, pack_profile: str, platform_profile: str, variant: str
) -> dict[str, Any]:
    identity = profile_extension_identity(SOURCE_INTERPRETER_GROUP, pack_profile)
    normalizer = normalizer_identity()
    resources = source_profile_resources(pack_profile, platform_profile)
    feed = source_interpreter(pack_profile).source_declarations(
        inputs, platform_profile=platform_profile, variant=variant
    )
    feed = validate_source_declarations(feed)
    feed["binding"] = {
        **feed["binding"],
        "source_observation": inputs.observation,
        "source_interpreter": identity,
        "normalizer_sha256": normalizer,
        "profile_resources": resources,
        "selected_profile": {
            "pack": pack_profile,
            "platform": platform_profile,
            "variant": variant,
        },
        "navigation_contract": 1,
    }
    feed["declaration_set_id"] = declaration_set_identity(feed)
    validate_navigation_declarations(feed, inputs=inputs)
    if (
        profile_extension_identity(SOURCE_INTERPRETER_GROUP, pack_profile) != identity
        or normalizer_identity() != normalizer
        or source_profile_resources(pack_profile, platform_profile) != resources
    ):
        raise SourceIntelligenceError("source interpreter changed during normalization")
    return feed


def validate_navigation_declarations(feed, *, inputs: SourceInputs | None = None):
    """PPS facade over the independently readable navigation record contract."""
    try:
        return declaration_contract.validate_navigation_declarations(
            feed, sources=None if inputs is None else inputs.sources
        )
    except declaration_contract.SourceDeclarationError as exc:
        raise PackProgramError(str(exc)) from exc
    except declaration_contract.SourceNavigationContractError as exc:
        raise SourceIntelligenceError(str(exc)) from exc
