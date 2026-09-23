#!/usr/bin/env python3

"""Bind one material classification to the frozen runtime that produced it.

The material-classification V1 result intentionally describes graph content,
not the Crucible launch or graph database that supplied that content.  This
module adds that missing evidence envelope without changing the identity or
shape of the existing V1 classification format.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from workbench_atlas.runtime_graph_material_classification import (
    MATERIAL_CLASSIFICATION_FORMAT,
    MaterialClassificationResult,
)


RUNTIME_MATERIAL_EVIDENCE_BINDING_FORMAT = (
    "workbench-atlas-runtime-material-evidence-binding-v1"
)
RUNTIME_MATERIAL_EVIDENCE_BINDING_SCHEMA_VERSION = 1
_CLASSIFICATION_ID_PREFIX = (
    "workbench-atlas-runtime-material-classification:sha256:"
)
_BINDING_ID_PREFIX = "workbench-atlas-runtime-material-evidence:sha256:"


class RuntimeMaterialEvidenceBindingError(ValueError):
    """A value cannot truthfully bind a classification to frozen evidence."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuntimeMaterialEvidenceBindingError(
            f"runtime material evidence is not canonical JSON: {exc}"
        ) from exc


def _content_id(prefix: str, value: Any) -> str:
    return prefix + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeMaterialEvidenceBindingError(
            f"{label} must be a non-empty string"
        )
    return value


def _sha256(value: object, label: str) -> str:
    value = _required_text(value, label)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise RuntimeMaterialEvidenceBindingError(
            f"{label} must be a lowercase SHA-256"
        )
    return value


def runtime_material_evidence_identity(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("evidence_binding_id", None)
    return _content_id(_BINDING_ID_PREFIX, payload)


def build_runtime_material_evidence_binding(
    classification: MaterialClassificationResult,
    *,
    pack_profile_id: str,
    graph_database_sha256: str,
    graph_normalization_id: str,
    crucible_snapshot_id: str,
    capture_id: str,
    frozen_stage: str,
    manager_phase: str,
) -> dict[str, Any]:
    """Build an Atlas-owned binding to one Crucible-owned frozen capture."""

    if not isinstance(classification, MaterialClassificationResult):
        raise RuntimeMaterialEvidenceBindingError(
            "runtime material evidence requires a MaterialClassificationResult"
        )
    value: dict[str, Any] = {
        "format": RUNTIME_MATERIAL_EVIDENCE_BINDING_FORMAT,
        "schema_version": RUNTIME_MATERIAL_EVIDENCE_BINDING_SCHEMA_VERSION,
        "evidence_binding_id": "",
        "authority": {
            "owner": "Atlas",
            "claim": "binding of a derived material classification to one frozen runtime capture",
            "runtime_owner": "Crucible",
            "source_authority": "none",
        },
        "scope": {
            "pack_profile_id": pack_profile_id,
            "platform_profile_id": classification.policy.platform_profile,
            "runtime_graph_profile": classification.scope.profile,
            "physical_side": classification.scope.physical_side,
        },
        "classification": {
            "format": MATERIAL_CLASSIFICATION_FORMAT,
            "classification_id": _CLASSIFICATION_ID_PREFIX + classification.sha256,
            "sha256": classification.sha256,
            "policy_id": classification.policy.policy_id,
            "policy_sha256": classification.policy.sha256,
        },
        "runtime_graph": {
            "database_sha256": graph_database_sha256,
            "normalization_id": graph_normalization_id,
        },
        "frozen_runtime": {
            "freeze_state": "frozen",
            "stage": frozen_stage,
            "manager_phase": manager_phase,
            "crucible_snapshot_id": crucible_snapshot_id,
            "capture_id": capture_id,
        },
        "limitations": [
            "This envelope binds classification bytes to runtime evidence; it does not establish which source operation caused a classified material.",
            "The referenced Crucible capture retains runtime authority and must be validated independently when causal reconciliation is requested.",
        ],
    }
    value["evidence_binding_id"] = runtime_material_evidence_identity(value)
    return validate_runtime_material_evidence_binding(value, classification)


def validate_runtime_material_evidence_binding(
    value: Mapping[str, Any],
    classification: MaterialClassificationResult | None = None,
) -> dict[str, Any]:
    """Validate the closed envelope and, when supplied, its classification."""

    if not isinstance(value, Mapping):
        raise RuntimeMaterialEvidenceBindingError(
            "runtime material evidence binding must be an object"
        )
    required = {
        "format",
        "schema_version",
        "evidence_binding_id",
        "authority",
        "scope",
        "classification",
        "runtime_graph",
        "frozen_runtime",
        "limitations",
    }
    if set(value) != required:
        raise RuntimeMaterialEvidenceBindingError(
            "runtime material evidence binding has unexpected keys"
        )
    if (
        value["format"] != RUNTIME_MATERIAL_EVIDENCE_BINDING_FORMAT
        or value["schema_version"]
        != RUNTIME_MATERIAL_EVIDENCE_BINDING_SCHEMA_VERSION
    ):
        raise RuntimeMaterialEvidenceBindingError(
            "unsupported runtime material evidence binding format"
        )
    if value["evidence_binding_id"] != runtime_material_evidence_identity(value):
        raise RuntimeMaterialEvidenceBindingError(
            "runtime material evidence binding identity does not match content"
        )

    authority = value["authority"]
    if (
        not isinstance(authority, Mapping)
        or set(authority)
        != {"owner", "claim", "runtime_owner", "source_authority"}
        or authority.get("owner") != "Atlas"
        or authority.get("runtime_owner") != "Crucible"
        or authority.get("source_authority") != "none"
    ):
        raise RuntimeMaterialEvidenceBindingError(
            "runtime material evidence authority is malformed"
        )
    _required_text(authority.get("claim"), "runtime material evidence claim")

    scope = value["scope"]
    if not isinstance(scope, Mapping) or set(scope) != {
        "pack_profile_id",
        "platform_profile_id",
        "runtime_graph_profile",
        "physical_side",
    }:
        raise RuntimeMaterialEvidenceBindingError(
            "runtime material evidence scope is malformed"
        )
    for key in scope:
        _required_text(scope[key], f"runtime material evidence scope {key}")

    material = value["classification"]
    if not isinstance(material, Mapping) or set(material) != {
        "format",
        "classification_id",
        "sha256",
        "policy_id",
        "policy_sha256",
    }:
        raise RuntimeMaterialEvidenceBindingError(
            "runtime material classification binding is malformed"
        )
    if material["format"] != MATERIAL_CLASSIFICATION_FORMAT:
        raise RuntimeMaterialEvidenceBindingError(
            "runtime material classification format differs"
        )
    classification_sha256 = _sha256(
        material["sha256"], "runtime material classification SHA-256"
    )
    if material["classification_id"] != _CLASSIFICATION_ID_PREFIX + classification_sha256:
        raise RuntimeMaterialEvidenceBindingError(
            "runtime material classification identity differs"
        )
    _required_text(material["policy_id"], "runtime material policy ID")
    _sha256(material["policy_sha256"], "runtime material policy SHA-256")

    graph = value["runtime_graph"]
    if not isinstance(graph, Mapping) or set(graph) != {
        "database_sha256",
        "normalization_id",
    }:
        raise RuntimeMaterialEvidenceBindingError(
            "runtime material graph binding is malformed"
        )
    _sha256(graph["database_sha256"], "runtime graph database SHA-256")
    _required_text(graph["normalization_id"], "runtime graph normalization ID")

    frozen = value["frozen_runtime"]
    if not isinstance(frozen, Mapping) or set(frozen) != {
        "freeze_state",
        "stage",
        "manager_phase",
        "crucible_snapshot_id",
        "capture_id",
    }:
        raise RuntimeMaterialEvidenceBindingError(
            "frozen runtime binding is malformed"
        )
    if frozen.get("freeze_state") != "frozen":
        raise RuntimeMaterialEvidenceBindingError(
            "runtime material evidence must bind a frozen state"
        )
    for key in ("stage", "manager_phase", "crucible_snapshot_id", "capture_id"):
        _required_text(frozen[key], f"frozen runtime {key}")

    limitations = value["limitations"]
    if (
        not isinstance(limitations, list)
        or not limitations
        or any(not isinstance(item, str) or not item for item in limitations)
    ):
        raise RuntimeMaterialEvidenceBindingError(
            "runtime material evidence limitations are malformed"
        )

    if classification is not None:
        if not isinstance(classification, MaterialClassificationResult):
            raise RuntimeMaterialEvidenceBindingError(
                "classification validation input has the wrong type"
            )
        expected = {
            "format": MATERIAL_CLASSIFICATION_FORMAT,
            "classification_id": _CLASSIFICATION_ID_PREFIX + classification.sha256,
            "sha256": classification.sha256,
            "policy_id": classification.policy.policy_id,
            "policy_sha256": classification.policy.sha256,
        }
        if dict(material) != expected:
            raise RuntimeMaterialEvidenceBindingError(
                "runtime material evidence binds different classification bytes"
            )
        expected_scope = {
            "platform_profile_id": classification.policy.platform_profile,
            "runtime_graph_profile": classification.scope.profile,
            "physical_side": classification.scope.physical_side,
        }
        for key, expected_value in expected_scope.items():
            if scope[key] != expected_value:
                raise RuntimeMaterialEvidenceBindingError(
                    f"runtime material evidence {key} differs from classification"
                )
    return dict(value)


__all__ = [
    "RUNTIME_MATERIAL_EVIDENCE_BINDING_FORMAT",
    "RUNTIME_MATERIAL_EVIDENCE_BINDING_SCHEMA_VERSION",
    "RuntimeMaterialEvidenceBindingError",
    "build_runtime_material_evidence_binding",
    "runtime_material_evidence_identity",
    "validate_runtime_material_evidence_binding",
]
