"""Versioned static declaration feed for Atlas semantic projection consumers."""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from workbench_api.source_declarations import (
    SourceDeclarationError,
    declaration_set_identity,
    validate_source_declarations as _validate_source_declarations,
)
from .model import PackProgramError, content_id


DECLARATION_FORMAT = "workbench-pack-source-declarations-v1"
DECLARATION_SCHEMA_VERSION = 1


def source_declaration(
    *,
    semantic_descriptor: Mapping[str, Any],
    attributes: Mapping[str, Any],
    lifecycle: Mapping[str, Any],
    provenance: Mapping[str, Any],
    source_effect_id: str | None = None,
) -> dict[str, Any]:
    """Build one content-addressed static declaration candidate."""

    descriptor = _semantic_descriptor(semantic_descriptor)
    record: dict[str, Any] = {
        "source_declaration_id": "",
        "semantic_descriptor": descriptor,
        "evidence_state": "static-candidate",
        "attributes": dict(attributes),
        "lifecycle": dict(lifecycle),
        "provenance": dict(provenance),
        "source_effect_id": source_effect_id,
        "limitations": [
            "This declaration is inferred from source and does not establish runtime registration or playable reachability."
        ],
    }
    record["source_declaration_id"] = content_id(
        "workbench-pack-source-declaration:sha256:",
        {key: value for key, value in record.items() if key != "source_declaration_id"},
    )
    return record


def build_source_declarations(
    *,
    program_id: str,
    pack_profile_id: str,
    platform_profile_id: str,
    source_sha256: str,
    declarations: Sequence[Mapping[str, Any]],
    source_kind: str = "pack-program-static-analysis",
) -> dict[str, Any]:
    """Build a declaration set without claiming that its effects executed."""

    value: dict[str, Any] = {
        "format": DECLARATION_FORMAT,
        "schema_version": DECLARATION_SCHEMA_VERSION,
        "declaration_set_id": "",
        "authority": {
            "owner": "Pack Program Studio",
            "claim": "source declarations only",
            "runtime_authority": "none",
            "playability_authority": "none",
        },
        "binding": {
            "program_id": program_id,
            "pack_profile_id": pack_profile_id,
            "platform_profile_id": platform_profile_id,
            "source_sha256": source_sha256,
            "source_kind": source_kind,
        },
        "declarations": [dict(row) for row in declarations],
        "summary": {
            "declarations": len(declarations),
            "by_kind": dict(
                sorted(
                    Counter(
                        row["semantic_descriptor"]["kind"] for row in declarations
                    ).items()
                )
            ),
        },
        "limitations": [
            "The feed preserves static candidates; Crucible remains the owner of observed runtime registry and effect state.",
            "Atlas may normalize these descriptors but must preserve this source provenance and evidence state.",
        ],
    }
    value["declaration_set_id"] = declaration_set_identity(value)
    return validate_source_declarations(value)


def declarations_from_program(program: Mapping[str, Any]) -> dict[str, Any]:
    """Project an analyzed Groovy program into the generic source feed."""

    binding = program.get("binding")
    effects = program.get("effects")
    if not isinstance(binding, Mapping) or not isinstance(effects, list):
        raise PackProgramError("Groovy program cannot feed source declarations")
    declarations = []
    for effect in effects:
        if not isinstance(effect, Mapping):
            raise PackProgramError("Groovy program effect is malformed")
        identity = effect.get("identity")
        if not isinstance(identity, Mapping) or not identity:
            identity = {"static_semantic_key": effect.get("semantic_key")}
        descriptor = {
            "domain": _domain_for_effect(effect),
            "kind": str(effect.get("kind")),
            "key": dict(identity),
        }
        declarations.append(
            source_declaration(
                semantic_descriptor=descriptor,
                attributes={
                    "operation": effect.get("operation"),
                    "fields": dict(effect.get("fields", {})),
                    "expression": effect.get("expression"),
                    "recipe": effect.get("recipe"),
                },
                lifecycle=dict(effect.get("lifecycle", {})),
                provenance={
                    "authority": "Pack Program Studio",
                    "program_id": program.get("program_id"),
                    "source": dict(effect.get("source", {})),
                    "rule_id": effect.get("rule_id"),
                },
                source_effect_id=effect.get("effect_id"),
            )
        )
    return build_source_declarations(
        program_id=str(program.get("program_id")),
        pack_profile_id=str(binding.get("pack_profile_id")),
        platform_profile_id=str(binding.get("platform_profile_id")),
        source_sha256=str(binding.get("source_sha256")),
        declarations=declarations,
    )


def validate_source_declarations(value: Mapping[str, Any]) -> dict[str, Any]:
    """Read the public V1 contract while preserving the PPS error boundary."""
    try:
        return _validate_source_declarations(value)
    except SourceDeclarationError as exc:
        raise PackProgramError(str(exc)) from exc


def _semantic_descriptor(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"domain", "kind", "key"}:
        raise PackProgramError("semantic descriptor must contain domain, kind, and key")
    if not isinstance(value["domain"], str) or not value["domain"]:
        raise PackProgramError("semantic descriptor domain is missing")
    if not isinstance(value["kind"], str) or not value["kind"]:
        raise PackProgramError("semantic descriptor kind is missing")
    if not isinstance(value["key"], Mapping) or not value["key"]:
        raise PackProgramError("semantic descriptor key is missing")
    return {"domain": value["domain"], "kind": value["kind"], "key": dict(value["key"])}


def _domain_for_effect(effect: Mapping[str, Any]) -> str:
    category = effect.get("category")
    if effect.get("kind") == "machine-recipe":
        return "recipe-process"
    if category == "reference":
        return "registry-identity"
    if category == "material":
        return "material-form"
    return str(category or "pack-program-effect")


__all__ = [
    "DECLARATION_FORMAT",
    "DECLARATION_SCHEMA_VERSION",
    "build_source_declarations",
    "declaration_set_identity",
    "declarations_from_program",
    "source_declaration",
    "validate_source_declarations",
]
