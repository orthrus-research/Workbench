"""V1 material-classification input contract shared by producers and Atlas.

Only immutable data, structural admission and the existing policy identity live
here. Producers supply domain rules; Atlas applies them to admitted graph data.
The Python API version is separate from the retained V1 policy JSON: adding the
version to those identity-bearing bytes would change historical policy digests.
"""

from __future__ import annotations
from dataclasses import dataclass, field, fields
import hashlib
import json
import re
from typing import Any, Protocol, Sequence

MATERIAL_CLASSIFICATION_POLICY_API_VERSION = 1


class MaterialPolicyValidationError(ValueError):
    """Raised when a material policy violates the public input contract."""


class LegacyMaterialPolicySnapshot(Protocol):
    """Existing V1 providers expose canonical policy JSON and its SHA-256.

    This is a serialization adapter, not admission based on a producer's class
    name. Both values are checked by :func:`material_classification_policy_from_v1`.
    """

    def to_dict(self) -> dict[str, Any]: ...

    @property
    def sha256(self) -> str: ...


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
        raise MaterialPolicyValidationError(
            f"material classification is not canonical JSON: {exc}"
        ) from exc


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise MaterialPolicyValidationError(f"{label} must be a non-empty string")
    return value


def _sorted_text_tuple(values: Sequence[str], label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise MaterialPolicyValidationError(f"{label} must be a sequence of names")
    result = tuple(values)
    if any(not isinstance(value, str) or not value for value in result):
        raise MaterialPolicyValidationError(f"{label} contains an invalid name")
    expected = tuple(sorted(set(result), key=lambda value: value.encode("utf-8")))
    if result != expected:
        raise MaterialPolicyValidationError(
            f"{label} must be unique and canonically sorted"
        )
    return result


def _mapping_rows(
    values: Sequence[tuple[str, Sequence[str]]],
    label: str,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    rows: list[tuple[str, tuple[str, ...]]] = []
    for key, children in values:
        key = _required_text(key, f"{label} key")
        rows.append((key, _sorted_text_tuple(children, f"{label} {key}")))
    expected = sorted(rows, key=lambda row: row[0].encode("utf-8"))
    if rows != expected or len({key for key, _ in rows}) != len(rows):
        raise MaterialPolicyValidationError(
            f"{label} must have unique, canonically sorted keys"
        )
    return tuple(rows)


@dataclass(frozen=True)
class MaterialClassificationPolicy:
    """Profile authority for interpreting one material graph shape.

    The policy deliberately uses immutable tuples.  Its canonical digest is
    included in every result so a future Groovy declaration checker can bind
    the exact closure rules used to interpret runtime state.
    """

    policy_id: str
    platform_profile: str
    admitted_material_adapters: tuple[str, ...]
    base_property_keys: tuple[str, ...]
    capability_property_keys: tuple[str, ...]
    property_dependencies: tuple[tuple[str, tuple[str, ...]], ...] = ()
    property_any_dependencies: tuple[tuple[str, tuple[str, ...]], ...] = ()
    property_fallback_dependencies: tuple[tuple[str, tuple[str, ...]], ...] = ()
    property_incompatibilities: tuple[tuple[str, str], ...] = ()
    flag_categories: tuple[tuple[str, tuple[str, ...]], ...] = ()
    flag_dependencies: tuple[tuple[str, tuple[str, ...]], ...] = ()
    flag_property_requirements: tuple[tuple[str, tuple[str, ...]], ...] = ()
    property_implied_flags: tuple[tuple[str, tuple[str, ...]], ...] = ()
    generation_constraint_kind: str | None = None
    api_version: int = field(
        default=MATERIAL_CLASSIFICATION_POLICY_API_VERSION,
        kw_only=True,
    )

    def __post_init__(self) -> None:
        if (
            type(self.api_version) is not int
            or self.api_version != MATERIAL_CLASSIFICATION_POLICY_API_VERSION
        ):
            raise MaterialPolicyValidationError(
                f"unsupported material classification policy API version: {self.api_version!r}"
            )
        _required_text(self.policy_id, "material classification policy ID")
        _required_text(self.platform_profile, "material platform profile")
        adapters = _sorted_text_tuple(
            self.admitted_material_adapters,
            "material policy adapters",
        )
        base = _sorted_text_tuple(
            self.base_property_keys,
            "material base property keys",
        )
        capability = _sorted_text_tuple(
            self.capability_property_keys,
            "material capability property keys",
        )
        if set(base) & set(capability):
            raise MaterialPolicyValidationError(
                "material base and capability property keys overlap"
            )
        dependencies = _mapping_rows(
            self.property_dependencies,
            "material property dependencies",
        )
        any_dependencies = _mapping_rows(
            self.property_any_dependencies,
            "material property alternative dependencies",
        )
        fallback_dependencies = _mapping_rows(
            self.property_fallback_dependencies,
            "material property fallback dependencies",
        )
        categories = _mapping_rows(
            self.flag_categories,
            "material flag categories",
        )
        flag_dependencies = _mapping_rows(
            self.flag_dependencies,
            "material flag dependencies",
        )
        flag_property_requirements = _mapping_rows(
            self.flag_property_requirements,
            "material flag property requirements",
        )
        property_implied_flags = _mapping_rows(
            self.property_implied_flags,
            "material property implied flags",
        )
        alternatives_by_key = dict(any_dependencies)
        for key, defaults in fallback_dependencies:
            if key not in alternatives_by_key or not set(defaults).issubset(
                alternatives_by_key[key]
            ):
                raise MaterialPolicyValidationError(
                    "material property fallback dependencies must select from "
                    f"the alternatives for {key}"
                )
        incompatibilities: list[tuple[str, str]] = []
        for left, right in self.property_incompatibilities:
            left = _required_text(left, "material property incompatibility")
            right = _required_text(right, "material property incompatibility")
            if left == right:
                raise MaterialPolicyValidationError(
                    "material property cannot be incompatible with itself"
                )
            incompatibilities.append(
                tuple(sorted((left, right), key=lambda value: value.encode("utf-8")))
            )
        expected_incompatibilities = sorted(
            set(incompatibilities),
            key=lambda row: (row[0].encode("utf-8"), row[1].encode("utf-8")),
        )
        if incompatibilities != expected_incompatibilities:
            raise MaterialPolicyValidationError(
                "material property incompatibilities must be unique and sorted"
            )
        if self.generation_constraint_kind is not None:
            _required_text(
                self.generation_constraint_kind,
                "material generation constraint kind",
            )
        # Reassign canonical tuple instances even when callers supplied tuple
        # subclasses.  The dataclass remains frozen to consumers.
        object.__setattr__(self, "admitted_material_adapters", adapters)
        object.__setattr__(self, "base_property_keys", base)
        object.__setattr__(self, "capability_property_keys", capability)
        object.__setattr__(self, "property_dependencies", dependencies)
        object.__setattr__(
            self,
            "property_any_dependencies",
            any_dependencies,
        )
        object.__setattr__(
            self,
            "property_fallback_dependencies",
            fallback_dependencies,
        )
        object.__setattr__(
            self,
            "property_incompatibilities",
            tuple(expected_incompatibilities),
        )
        object.__setattr__(self, "flag_categories", categories)
        object.__setattr__(self, "flag_dependencies", flag_dependencies)
        object.__setattr__(
            self,
            "flag_property_requirements",
            flag_property_requirements,
        )
        object.__setattr__(
            self,
            "property_implied_flags",
            property_implied_flags,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "platform_profile": self.platform_profile,
            "admitted_material_adapters": list(
                self.admitted_material_adapters
            ),
            "property_classes": {
                "base": list(self.base_property_keys),
                "capability": list(self.capability_property_keys),
            },
            "property_dependencies": {
                key: list(required)
                for key, required in self.property_dependencies
            },
            "property_any_dependencies": {
                key: list(required)
                for key, required in self.property_any_dependencies
            },
            "property_fallback_dependencies": {
                key: list(required)
                for key, required in self.property_fallback_dependencies
            },
            "property_incompatibilities": [
                list(pair) for pair in self.property_incompatibilities
            ],
            "flag_categories": {
                key: list(categories)
                for key, categories in self.flag_categories
            },
            "flag_dependencies": {
                key: list(required)
                for key, required in self.flag_dependencies
            },
            "flag_property_requirements": {
                key: list(required)
                for key, required in self.flag_property_requirements
            },
            "property_implied_flags": {
                key: list(flags)
                for key, flags in self.property_implied_flags
            },
            "generation_constraint_kind": self.generation_constraint_kind,
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(_canonical_bytes(self.to_dict())).hexdigest()


def material_classification_policy_from_v1(
    payload: dict[str, Any], *, expected_sha256: str
) -> MaterialClassificationPolicy:
    """Admit an exact legacy V1 policy snapshot through the current contract.

    The named adapter selects the retained V1 format, which predates an explicit
    API version field. No field or cached producer digest bypasses structural
    validation; the digest is checked against reconstructed canonical data.
    """

    mappings = (
        "property_dependencies",
        "property_any_dependencies",
        "property_fallback_dependencies",
        "flag_categories",
        "flag_dependencies",
        "flag_property_requirements",
        "property_implied_flags",
    )
    required = {
        "policy_id", "platform_profile", "admitted_material_adapters",
        "property_classes", "property_incompatibilities",
        "generation_constraint_kind", *mappings,
    }
    if type(payload) is not dict or set(payload) != required:
        raise MaterialPolicyValidationError("legacy V1 material policy has unexpected fields")
    classes = payload["property_classes"]
    if type(classes) is not dict or set(classes) != {"base", "capability"}:
        raise MaterialPolicyValidationError("legacy V1 material policy has invalid property classes")
    for names in (
        payload["admitted_material_adapters"], classes["base"], classes["capability"]
    ):
        if type(names) is not list:
            raise MaterialPolicyValidationError("legacy V1 material policy names must be arrays")
    converted: dict[str, Any] = {}
    for name in mappings:
        rows = payload[name]
        if type(rows) is not dict or any(type(values) is not list for values in rows.values()):
            raise MaterialPolicyValidationError(f"legacy V1 material policy {name} must map to arrays")
        converted[name] = tuple((key, tuple(values)) for key, values in rows.items())
    pairs = payload["property_incompatibilities"]
    if type(pairs) is not list or any(type(pair) is not list or len(pair) != 2 for pair in pairs):
        raise MaterialPolicyValidationError("legacy V1 material policy incompatibilities must be pairs")
    admitted = MaterialClassificationPolicy(
        policy_id=payload["policy_id"],
        platform_profile=payload["platform_profile"],
        admitted_material_adapters=tuple(payload["admitted_material_adapters"]),
        base_property_keys=tuple(classes["base"]),
        capability_property_keys=tuple(classes["capability"]),
        property_incompatibilities=tuple(tuple(pair) for pair in pairs),
        generation_constraint_kind=payload["generation_constraint_kind"],
        **converted,
    )
    if admitted.to_dict() != payload:
        raise MaterialPolicyValidationError("legacy V1 material policy data is not canonical")
    if (
        type(expected_sha256) is not str
        or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
        or admitted.sha256 != expected_sha256
    ):
        raise MaterialPolicyValidationError("legacy V1 material policy digest does not match content")
    return admitted


def admit_material_classification_policy(
    policy: MaterialClassificationPolicy | LegacyMaterialPolicySnapshot,
) -> MaterialClassificationPolicy:
    """Validate and snapshot a producer's policy at the consumer boundary.

    Reconstruct the public contract rather than relying on overridden producer
    methods, a cached digest, or a caller's assertion that it was validated.
    """

    if isinstance(policy, MaterialClassificationPolicy):
        return MaterialClassificationPolicy(
            **{
                field.name: getattr(policy, field.name)
                for field in fields(MaterialClassificationPolicy)
            }
        )
    try:
        version = getattr(policy, "api_version", 1)
        if type(version) is not int or version != MATERIAL_CLASSIFICATION_POLICY_API_VERSION:
            raise MaterialPolicyValidationError(
                f"unsupported material classification policy API version: {version!r}"
            )
        return material_classification_policy_from_v1(
            policy.to_dict(), expected_sha256=policy.sha256
        )
    except MaterialPolicyValidationError:
        raise
    except Exception as exc:
        raise MaterialPolicyValidationError(
            "material classification requires a MaterialClassificationPolicy "
            "or a valid legacy V1 policy snapshot"
        ) from exc


__all__ = [
    "MATERIAL_CLASSIFICATION_POLICY_API_VERSION",
    "LegacyMaterialPolicySnapshot",
    "MaterialClassificationPolicy",
    "MaterialPolicyValidationError",
    "admit_material_classification_policy",
    "material_classification_policy_from_v1",
]
