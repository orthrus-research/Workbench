"""Adapter ports and explicitly synthetic fixtures for effect comparison.

Adapters translate admitted snapshot observations into comparison-only semantic
records.  They do not decide runtime truth, causality, construction validity,
support, or action authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Protocol, runtime_checkable


class EffectAdapterError(ValueError):
    """An adapter cannot truthfully project an accepted runtime observation."""


def _plain_json(value: Any, *, label: str) -> Any:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return json.loads(encoded)
    except (RecursionError, TypeError, ValueError) as exc:
        raise EffectAdapterError(f"{label} is not bounded ordinary JSON: {exc}") from exc


def _nonempty_string(value: Any, *, label: str) -> str:
    if type(value) is not str or not value:
        raise EffectAdapterError(f"{label} must be a non-empty string")
    return value


@dataclass(frozen=True)
class SemanticEffectRecord:
    """One adapter-owned comparison projection of one Crucible observation."""

    family: str
    semantic_owner_id: str
    adapter_id: str
    semantic_version: str
    correlation_policy_id: str
    fingerprint_policy_id: str
    runtime_record_id: str
    record_kind: str
    descriptor_kind: str
    operation: str | None
    correlation: dict[str, Any] | None
    semantic_fingerprint: str | None


@runtime_checkable
class EffectFamilyAdapter(Protocol):
    """Family-owned semantic projection and correspondence port.

    `correlation` must return an exact family identity or ``None``.  Returning
    ``None`` is an honest unresolved result; Process Studio never substitutes a
    label, fingerprint, list position, or runtime record ID.
    """

    family: str
    semantic_owner_id: str
    adapter_id: str
    semantic_version: str
    correlation_policy_id: str
    fingerprint_policy_id: str

    def accepts(self, observation: Mapping[str, Any]) -> bool:
        """Return whether this adapter owns the observation's descriptor."""

    def project(self, observation: Mapping[str, Any]) -> SemanticEffectRecord:
        """Project one already validated Crucible observation."""


class _RecipeAdapter:
    family: str
    semantic_owner_id: str
    adapter_id: str
    semantic_version: str
    correlation_policy_id: str
    fingerprint_policy_id: str
    descriptor_domain: str

    def accepts(self, observation: Mapping[str, Any]) -> bool:
        descriptor = observation.get("semantic_descriptor")
        return (
            isinstance(descriptor, Mapping)
            and descriptor.get("domain") == self.descriptor_domain
        )

    def project(self, observation: Mapping[str, Any]) -> SemanticEffectRecord:
        if not self.accepts(observation):
            raise EffectAdapterError(
                f"{self.adapter_id} does not own the supplied observation"
            )
        descriptor = observation["semantic_descriptor"]
        if not isinstance(descriptor, Mapping):
            raise EffectAdapterError("semantic descriptor is not an object")
        descriptor_kind = _nonempty_string(
            descriptor.get("kind"), label="semantic descriptor kind"
        )
        key = descriptor.get("key")
        if not isinstance(key, Mapping) or not key:
            raise EffectAdapterError("semantic descriptor key is missing")
        record_kind = _nonempty_string(
            observation.get("record_kind"), label="runtime record kind"
        )
        operation: str | None
        semantic_value: dict[str, Any] = {
            "record_kind": record_kind,
            "semantic_descriptor": _plain_json(
                descriptor, label="semantic descriptor"
            ),
        }
        if record_kind == "registry-observation":
            operation = None
            semantic_value["registry_state"] = _plain_json(
                observation.get("registry_state"), label="registry state"
            )
        elif record_kind == "effect-observation":
            operation = _nonempty_string(
                observation.get("operation"), label="effect operation"
            )
            semantic_value["operation"] = operation
            semantic_value["effect_state"] = _plain_json(
                observation.get("effect_state"), label="effect state"
            )
        else:
            raise EffectAdapterError(f"unsupported runtime record kind: {record_kind}")
        correlation = self._correlation(key)
        return SemanticEffectRecord(
            family=self.family,
            semantic_owner_id=self.semantic_owner_id,
            adapter_id=self.adapter_id,
            semantic_version=self.semantic_version,
            correlation_policy_id=self.correlation_policy_id,
            fingerprint_policy_id=self.fingerprint_policy_id,
            runtime_record_id=_nonempty_string(
                observation.get("runtime_record_id"), label="runtime record ID"
            ),
            record_kind=record_kind,
            descriptor_kind=descriptor_kind,
            operation=operation,
            correlation=correlation,
            semantic_fingerprint=hashlib.sha256(
                json.dumps(
                    semantic_value,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
        )

    def _correlation(self, key: Mapping[str, Any]) -> dict[str, Any] | None:
        raise NotImplementedError

    @staticmethod
    def _declared_correlation(key: Mapping[str, Any]) -> dict[str, Any] | None:
        declared = key.get("correlation")
        if declared is None:
            return None
        if not isinstance(declared, Mapping) or not declared:
            raise EffectAdapterError("declared correlation must be a non-empty object")
        projected = _plain_json(declared, label="declared correlation")
        if not isinstance(projected, dict) or not projected:
            raise EffectAdapterError("declared correlation must remain a non-empty object")
        return projected


class SyntheticGTRecipeFixtureAdapter(_RecipeAdapter):
    """Synthetic GT-shaped fixture; never a production GT semantic adapter."""

    family = "gt-recipe"
    semantic_owner_id = "workbench-process-studio-synthetic-fixture-owner:gt-recipe:v1"
    adapter_id = "workbench-process-studio-synthetic-fixture-adapter:gt-recipe:v1"
    semantic_version = "synthetic-gt-recipe-effect:v1"
    correlation_policy_id = "workbench-process-studio-synthetic-correlation:declared-only:v1"
    fingerprint_policy_id = "workbench-process-studio-synthetic-fingerprint:gt-recipe:v1"
    descriptor_domain = "gt-recipe"

    def _correlation(self, key: Mapping[str, Any]) -> dict[str, Any] | None:
        return self._declared_correlation(key)


class SyntheticForgeCraftingFixtureAdapter(_RecipeAdapter):
    """Synthetic Forge-shaped fixture; never a production Forge adapter."""

    family = "forge-crafting"
    semantic_owner_id = "workbench-process-studio-synthetic-fixture-owner:forge-crafting:v1"
    adapter_id = "workbench-process-studio-synthetic-fixture-adapter:forge-crafting:v1"
    semantic_version = "synthetic-forge-crafting-effect:v1"
    correlation_policy_id = "workbench-process-studio-synthetic-correlation:declared-only:v1"
    fingerprint_policy_id = "workbench-process-studio-synthetic-fingerprint:forge-crafting:v1"
    descriptor_domain = "forge-crafting"

    def _correlation(self, key: Mapping[str, Any]) -> dict[str, Any] | None:
        return self._declared_correlation(key)


SYNTHETIC_FIXTURE_ADAPTERS: tuple[EffectFamilyAdapter, ...] = (
    SyntheticForgeCraftingFixtureAdapter(),
    SyntheticGTRecipeFixtureAdapter(),
)


__all__ = [
    "EffectAdapterError",
    "EffectFamilyAdapter",
    "SYNTHETIC_FIXTURE_ADAPTERS",
    "SemanticEffectRecord",
    "SyntheticForgeCraftingFixtureAdapter",
    "SyntheticGTRecipeFixtureAdapter",
]
