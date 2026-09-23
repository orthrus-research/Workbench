"""Material source intent compared with one frozen runtime classification."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from workbench_atlas.runtime_graph_material_classification import MaterialClassificationResult
from workbench_atlas.runtime_material_evidence_binding import (
    validate_runtime_material_evidence_binding,
)
from workbench_api.source_declarations import validate_source_declarations


MATERIAL_SOURCE_RUNTIME_COMPARISON_FORMAT = (
    "workbench-atlas-material-source-runtime-comparison-v1"
)
MATERIAL_SOURCE_RUNTIME_COMPARISON_SCHEMA_VERSION = 1
_COMPARISON_ID_PREFIX = "workbench-atlas-material-comparison:sha256:"


class MaterialSourceRuntimeComparisonError(ValueError):
    """Inputs cannot support an authority-preserving material comparison."""


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
        raise MaterialSourceRuntimeComparisonError(
            f"material comparison is not canonical JSON: {exc}"
        ) from exc


def _content_id(prefix: str, value: Any) -> str:
    return prefix + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise MaterialSourceRuntimeComparisonError(
            f"{label} must be a non-empty string"
        )
    return value


def _sha256(value: object, label: str) -> str:
    value = _required_text(value, label)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise MaterialSourceRuntimeComparisonError(
            f"{label} must be a lowercase SHA-256"
        )
    return value


def _sorted_text(values: Sequence[str], label: str) -> tuple[str, ...]:
    result = tuple(values)
    if any(not isinstance(value, str) or not value for value in result):
        raise MaterialSourceRuntimeComparisonError(f"{label} contains an invalid name")
    if result != tuple(sorted(set(result), key=lambda item: item.encode("utf-8"))):
        raise MaterialSourceRuntimeComparisonError(
            f"{label} must be unique and canonically sorted"
        )
    return result


@dataclass(frozen=True)
class MaterialSourceRuntimeComparisonPolicy:
    """Exact profile bindings and source-flag-to-runtime-form semantics."""

    policy_id: str
    pack_profile_id: str
    platform_profile_id: str
    physical_side: str
    runtime_policy_id: str
    runtime_policy_sha256: str
    admitted_source_kinds: tuple[str, ...]
    flag_form_prefixes: tuple[tuple[str, tuple[str, ...]], ...] = ()
    semantic_source_files: tuple[tuple[str, str], ...] = ()
    platform_profile_equivalences: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        for value, label in (
            (self.policy_id, "material comparison policy ID"),
            (self.pack_profile_id, "material comparison pack profile"),
            (self.platform_profile_id, "material comparison platform profile"),
            (self.physical_side, "material comparison physical side"),
            (self.runtime_policy_id, "material comparison runtime policy ID"),
        ):
            _required_text(value, label)
        _sha256(self.runtime_policy_sha256, "material comparison runtime policy SHA-256")
        kinds = _sorted_text(
            self.admitted_source_kinds,
            "material comparison admitted source kinds",
        )
        if not kinds:
            raise MaterialSourceRuntimeComparisonError(
                "material comparison must admit at least one source kind"
            )
        mappings: list[tuple[str, tuple[str, ...]]] = []
        for flag, prefixes in self.flag_form_prefixes:
            mappings.append(
                (
                    _required_text(flag, "material form flag"),
                    _sorted_text(prefixes, f"material form prefixes for {flag}"),
                )
            )
        if tuple(mappings) != tuple(sorted(mappings, key=lambda row: row[0].encode("utf-8"))):
            raise MaterialSourceRuntimeComparisonError(
                "material flag form mappings must have unique, sorted flags"
            )
        if len({flag for flag, _ in mappings}) != len(mappings):
            raise MaterialSourceRuntimeComparisonError(
                "material flag form mappings contain duplicate flags"
            )
        source_files: list[tuple[str, str]] = []
        for path, sha256 in self.semantic_source_files:
            source_files.append(
                (
                    _required_text(path, "material comparison semantic source path"),
                    _sha256(
                        sha256,
                        f"material comparison semantic source {path} SHA-256",
                    ),
                )
            )
        if tuple(source_files) != tuple(sorted(set(source_files))):
            raise MaterialSourceRuntimeComparisonError(
                "material comparison semantic source files must be unique and sorted"
            )
        equivalences = tuple(self.platform_profile_equivalences)
        if equivalences != tuple(sorted(set(equivalences))):
            raise MaterialSourceRuntimeComparisonError(
                "material profile equivalences must be unique and sorted"
            )
        object.__setattr__(self, "admitted_source_kinds", kinds)
        object.__setattr__(self, "flag_form_prefixes", tuple(mappings))
        object.__setattr__(self, "semantic_source_files", tuple(source_files))
        object.__setattr__(self, "platform_profile_equivalences", equivalences)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "pack_profile_id": self.pack_profile_id,
            "platform_profile_id": self.platform_profile_id,
            "physical_side": self.physical_side,
            "runtime_policy_id": self.runtime_policy_id,
            "runtime_policy_sha256": self.runtime_policy_sha256,
            "admitted_source_kinds": list(self.admitted_source_kinds),
            "flag_form_prefixes": {
                flag: list(prefixes) for flag, prefixes in self.flag_form_prefixes
            },
            "semantic_source_files": [
                {"path": path, "sha256": sha256}
                for path, sha256 in self.semantic_source_files
            ],
            "platform_profile_equivalences": [
                {"source": source, "runtime": runtime}
                for source, runtime in self.platform_profile_equivalences
            ],
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(_canonical_bytes(self.to_dict())).hexdigest()


def _comparison_identity(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("comparison_id", None)
    return _content_id(_COMPARISON_ID_PREFIX, payload)


def _profile_matches(
    source: str,
    runtime: str,
    policy: MaterialSourceRuntimeComparisonPolicy,
) -> bool:
    return source == runtime or (source, runtime) in set(
        policy.platform_profile_equivalences
    )


def _resource_from_registration(row: Mapping[str, Any]) -> str | None:
    attributes = row.get("attributes")
    if not isinstance(attributes, Mapping):
        return None
    identity = attributes.get("identity")
    resource = identity.get("resource_location") if isinstance(identity, Mapping) else None
    return resource if isinstance(resource, str) and resource else None


def _resource_from_mutation(row: Mapping[str, Any]) -> str | None:
    attributes = row.get("attributes")
    target = attributes.get("target") if isinstance(attributes, Mapping) else None
    resource = target.get("resource_location") if isinstance(target, Mapping) else None
    return resource if isinstance(resource, str) and resource else None


def _facet(
    state: str,
    source_value: Any,
    runtime_value: Any,
    source_ids: Sequence[str],
    runtime_ids: Sequence[str],
) -> dict[str, Any]:
    return {
        "state": state,
        "source_value": source_value,
        "runtime_value": runtime_value,
        "evidence": {
            "source_declaration_ids": list(source_ids),
            "runtime_material_ids": list(runtime_ids),
        },
    }


def _set_state(expected: Iterable[str], actual: Iterable[str]) -> tuple[str, dict[str, list[str]]]:
    expected_set = set(expected)
    actual_set = set(actual)
    missing = sorted(expected_set - actual_set)
    additional = sorted(actual_set - expected_set)
    if missing and additional:
        state = "different"
    elif missing:
        state = "missing-at-runtime"
    elif additional:
        state = "additional-at-runtime"
    else:
        state = "equal"
    return state, {
        "expected": sorted(expected_set),
        "actual": sorted(actual_set),
        "missing": missing,
        "additional": additional,
    }


def _inclusion_state(expected: Iterable[str], actual: Iterable[str]) -> tuple[str, dict[str, list[str]]]:
    expected_set = set(expected)
    actual_set = set(actual)
    missing = sorted(expected_set - actual_set)
    return (
        "missing-at-runtime" if missing else "equal",
        {
            "required": sorted(expected_set),
            "actual": sorted(actual_set),
            "missing": missing,
        },
    )


def _runtime_component_values(
    runtime: Mapping[str, Any],
    resources_by_material_id: Mapping[str, str],
) -> list[dict[str, Any]] | None:
    rows: list[dict[str, Any]] = []
    for component in runtime["core"]["composition"]["components"]:
        resource = resources_by_material_id.get(component["material_id"])
        if resource is None:
            return None
        rows.append(
            {
                "ordinal": component["ordinal"],
                "resource_location": resource,
                "amount": component["amount"],
            }
        )
    return rows


def _source_component_values(registration: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    composition = registration["attributes"]["declared_material_core"]["composition"]
    rows: list[dict[str, Any]] = []
    for component in composition["components"]:
        resource = component.get("material_resource_location")
        amount = component.get("amount")
        ordinal = component.get("ordinal")
        if not isinstance(resource, str) or not isinstance(amount, int) or not isinstance(ordinal, int):
            return None
        rows.append(
            {
                "ordinal": ordinal,
                "resource_location": resource,
                "amount": amount,
            }
        )
    return rows


def _registration_parity(
    registration: Mapping[str, Any] | None,
    runtime: Mapping[str, Any] | None,
    resources_by_material_id: Mapping[str, str],
) -> dict[str, Any]:
    source_ids = [] if registration is None else [registration["source_declaration_id"]]
    runtime_ids = [] if runtime is None else [runtime["material_id"]]
    if registration is None or runtime is None:
        return {"state": "not-comparable", "facets": {}, "evidence": {
            "source_declaration_ids": source_ids,
            "runtime_material_ids": runtime_ids,
        }}
    core = registration["attributes"]["declared_material_core"]
    runtime_core = runtime["core"]
    facets: dict[str, Any] = {}
    numeric = core["identity"].get("numeric_id")
    numeric_state = "not-comparable"
    if isinstance(numeric, int) and not isinstance(numeric, bool):
        numeric_state = "equal" if numeric == runtime_core["identity"]["numeric_id"] else "different"
    facets["numeric_id"] = _facet(
        numeric_state,
        numeric,
        runtime_core["identity"]["numeric_id"],
        source_ids,
        runtime_ids,
    )
    closure = core["expected_verified_closure"]
    closure_exact = closure.get("state") == "exact-static"
    for name, source_values, runtime_values in (
        ("properties", closure["properties"]["keys"], runtime_core["properties"]["keys"]),
        ("flags", closure["flags"]["names"], runtime_core["flags"]["names"]),
    ):
        if closure_exact:
            state, values = _set_state(source_values, runtime_values)
        else:
            state, values = "not-comparable", {
                "source_lower_bound": list(source_values),
                "actual": list(runtime_values),
            }
        facets[name] = _facet(state, values, list(runtime_values), source_ids, runtime_ids)
    source_composition = core["composition"]
    source_components = _source_component_values(registration)
    runtime_components = _runtime_component_values(runtime, resources_by_material_id)
    if (
        source_composition.get("elements")
        or source_components is None
        or runtime_components is None
    ):
        composition_state = "not-comparable"
    else:
        source_value = {
            "basis": source_composition["basis"],
            "components": source_components,
        }
        runtime_value = {
            "basis": runtime_core["composition"]["basis"],
            "components": runtime_components,
        }
        composition_state = "equal" if source_value == runtime_value else "different"
    facets["composition"] = _facet(
        composition_state,
        {"basis": source_composition["basis"], "components": source_components},
        {"basis": runtime_core["composition"]["basis"], "components": runtime_components},
        source_ids,
        runtime_ids,
    )
    facets["presentation"] = _facet(
        "not-comparable",
        core.get("presentation"),
        runtime_core.get("presentation"),
        source_ids,
        runtime_ids,
    )
    # Presentation is optional source intent.  An absent source presentation
    # claim must not make an otherwise exact core comparison a frontier.
    states = {
        facets[name]["state"]
        for name in ("numeric_id", "properties", "flags", "composition")
    }
    if states & {"different", "missing-at-runtime", "additional-at-runtime"}:
        state = "difference"
    elif "not-comparable" in states:
        state = "frontier"
    else:
        state = "parity"
    return {"state": state, "facets": facets}


def _runtime_form_sets(runtime: Mapping[str, Any] | None) -> tuple[set[str], set[str]]:
    fluid: set[str] = set()
    prefixes: set[str] = set()
    if runtime is None:
        return fluid, prefixes
    for row in runtime["form_lens"]["realized"]["forms"]:
        if row["form_kind"] == "fluid":
            attributes = row.get("attributes")
            storage = attributes.get("storage_key") if isinstance(attributes, Mapping) else None
            if isinstance(storage, str) and storage:
                fluid.add(storage.rpartition(":")[2])
        elif row["form_kind"] == "item_variant" and isinstance(row.get("prefix_name"), str):
            prefixes.add(row["prefix_name"])
    return fluid, prefixes


def _requested_forms(
    registration: Mapping[str, Any] | None,
    mutations: Sequence[Mapping[str, Any]],
    policy: MaterialSourceRuntimeComparisonPolicy,
) -> tuple[set[str], set[str]]:
    storage: set[str] = set()
    flags: set[str] = set()
    if registration is not None:
        attributes = registration["attributes"]
        for row in attributes.get("form_lens", {}).get("fluid_storage_relationships", []):
            key = row.get("storage_key")
            if isinstance(key, str) and key:
                storage.add(key.rpartition(":")[2])
        closure = attributes["declared_material_core"]["expected_verified_closure"]
        flags.update(closure["flags"]["names"])
    for mutation in mutations:
        if mutation["attributes"].get("resolution_state") != "exact-static":
            continue
        constraints = mutation["attributes"].get("constraints", {})
        storage.update(
            key.rpartition(":")[2]
            for key in constraints.get("requested_fluid_storage_keys", [])
            if isinstance(key, str) and key
        )
        flags.update(
            flag for flag in constraints.get("must_include_flags", [])
            if isinstance(flag, str)
        )
    mappings = dict(policy.flag_form_prefixes)
    prefixes = {prefix for flag in flags for prefix in mappings.get(flag, ())}
    return storage, prefixes


def _form_realization(
    registration: Mapping[str, Any] | None,
    mutations: Sequence[Mapping[str, Any]],
    runtime: Mapping[str, Any] | None,
    policy: MaterialSourceRuntimeComparisonPolicy,
) -> dict[str, Any]:
    requested_storage, requested_prefixes = _requested_forms(
        registration, mutations, policy
    )
    observed_storage, observed_prefixes = _runtime_form_sets(runtime)
    source_ids = [
        *([] if registration is None else [registration["source_declaration_id"]]),
        *(row["source_declaration_id"] for row in mutations),
    ]
    runtime_ids = [] if runtime is None else [runtime["material_id"]]
    facets: dict[str, Any] = {}
    for name, requested, observed in (
        ("fluid_storage_keys", requested_storage, observed_storage),
        ("item_prefixes", requested_prefixes, observed_prefixes),
    ):
        state, values = _set_state(requested, observed)
        facets[name] = _facet(state, values, sorted(observed), source_ids, runtime_ids)
    if not requested_storage and not requested_prefixes:
        state = (
            "additional-observed-forms"
            if observed_storage or observed_prefixes
            else "not-requested"
        )
    elif runtime is None:
        state = "not-observed"
    elif any(facet["state"] in {"missing-at-runtime", "different"} for facet in facets.values()):
        state = "not-observed"
    elif any(facet["state"] == "additional-at-runtime" for facet in facets.values()):
        state = "realized-with-additional-observed-forms"
    else:
        state = "realized"
    return {"state": state, "facets": facets}


def _mutation_check(
    mutation: Mapping[str, Any],
    runtime: Mapping[str, Any] | None,
) -> dict[str, Any]:
    source_id = mutation["source_declaration_id"]
    runtime_ids = [] if runtime is None else [runtime["material_id"]]
    operation = mutation["attributes"].get("operation")
    constraints = mutation["attributes"].get("constraints", {})
    facets: dict[str, Any] = {}
    if mutation["attributes"].get("resolution_state") != "exact-static":
        return {
            "source_declaration_id": source_id,
            "operation": operation,
            "declared_constraints": dict(constraints),
            "state": "frontier",
            "facets": {},
        }
    if runtime is None:
        return {
            "source_declaration_id": source_id,
            "operation": operation,
            "declared_constraints": dict(constraints),
            "state": "difference",
            "facets": {
                "target": _facet(
                    "missing-at-runtime", True, False, [source_id], runtime_ids
                )
            },
        }
    core = runtime["core"]
    for key, actual in (
        ("must_include_properties", core["properties"]["keys"]),
        ("must_include_flags", core["flags"]["names"]),
    ):
        if key not in constraints:
            continue
        state, values = _inclusion_state(constraints[key], actual)
        facets[key] = _facet(state, values, list(actual), [source_id], runtime_ids)
    if "chemical_formula_equals" in constraints:
        expected = constraints["chemical_formula_equals"]
        actual = core["composition"].get("chemical_formula")
        state = "equal" if expected == actual else "different"
        facets["chemical_formula"] = _facet(
            state, expected, actual, [source_id], runtime_ids
        )
    if "presentation_field" in constraints:
        field = constraints["presentation_field"]
        expected = constraints.get("equals")
        actual = core["presentation"].get(field)
        state = "equal" if expected == actual else "different"
        facets["presentation"] = _facet(
            state,
            {field: expected},
            {field: actual},
            [source_id],
            runtime_ids,
        )
    if not facets:
        state = "frontier"
    elif any(facet["state"] in {"different", "missing-at-runtime"} for facet in facets.values()):
        state = "difference"
    elif any(facet["state"] == "not-comparable" for facet in facets.values()):
        state = "frontier"
    else:
        state = "compliant"
    return {
        "source_declaration_id": source_id,
        "operation": operation,
        "declared_constraints": dict(constraints),
        "state": state,
        "facets": facets,
    }


def _material_row(
    resource: str,
    registrations: Sequence[Mapping[str, Any]],
    mutations: Sequence[Mapping[str, Any]],
    runtimes: Sequence[Mapping[str, Any]],
    resources_by_material_id: Mapping[str, str],
    policy: MaterialSourceRuntimeComparisonPolicy,
    capture_id: str,
) -> dict[str, Any]:
    source_ambiguous = len(registrations) > 1
    runtime_ambiguous = len(runtimes) > 1
    registration = registrations[0] if len(registrations) == 1 else None
    runtime = runtimes[0] if len(runtimes) == 1 else None
    parity = _registration_parity(registration, runtime, resources_by_material_id)
    checks = [_mutation_check(mutation, runtime) for mutation in mutations]
    if not checks:
        mutation_state = "not-declared"
    elif any(check["state"] == "difference" for check in checks):
        mutation_state = "difference"
    elif any(check["state"] == "frontier" for check in checks):
        mutation_state = "frontier"
    else:
        mutation_state = "compliant"
    forms = _form_realization(registration, mutations, runtime, policy)
    if source_ambiguous or runtime_ambiguous:
        aggregate = "ambiguous"
    elif runtime is None:
        aggregate = "source-only"
    elif registration is None and not mutations:
        aggregate = "runtime-only"
    elif (
        parity["state"] == "difference"
        or mutation_state == "difference"
        or forms["state"] == "not-observed"
    ):
        aggregate = "difference"
    elif (
        parity["state"] == "frontier"
        or mutation_state == "frontier"
        or runtime["classification_status"] == "frontier"
    ):
        aggregate = "frontier"
    else:
        aggregate = "parity"
    return {
        "resource_location": resource,
        "aggregate_state": aggregate,
        "attribution": (
            "unattributed-runtime"
            if runtime is not None and registration is None and not mutations
            else "source-associated"
        ),
        "source": {
            "registration_declaration_ids": [
                row["source_declaration_id"] for row in registrations
            ],
            "mutation_declaration_ids": [
                row["source_declaration_id"] for row in mutations
            ],
        },
        "runtime": {
            "material_ids": [row["material_id"] for row in runtimes],
            "classification_statuses": [
                row["classification_status"] for row in runtimes
            ],
        },
        "registration_parity": parity,
        "mutation_compliance": {
            "state": mutation_state,
            "checks": checks,
        },
        "execution_lineage": {
            "state": (
                "not-established"
                if registrations or mutations
                else "not-applicable"
            ),
            "capture_id": capture_id,
            "operation_ledger_id": None,
            "operation_ids": [],
            "source_declaration_ids_established": [],
            "source_declaration_ids_execution_only": [],
            "source_declaration_ids_unobserved": [
                *[row["source_declaration_id"] for row in registrations],
                *[row["source_declaration_id"] for row in mutations],
            ],
        },
        "form_realization": forms,
    }


def compare_material_source_to_runtime(
    source_feed: Mapping[str, Any],
    runtime_classification: MaterialClassificationResult,
    runtime_evidence_binding: Mapping[str, Any],
    policy: MaterialSourceRuntimeComparisonPolicy,
) -> dict[str, Any]:
    """Compare static material intent with one capture-bound frozen census."""

    source = validate_source_declarations(source_feed)
    if not isinstance(runtime_classification, MaterialClassificationResult):
        raise MaterialSourceRuntimeComparisonError(
            "material comparison requires a MaterialClassificationResult"
        )
    if not isinstance(policy, MaterialSourceRuntimeComparisonPolicy):
        raise MaterialSourceRuntimeComparisonError(
            "material comparison requires a comparison policy"
        )
    evidence = validate_runtime_material_evidence_binding(
        runtime_evidence_binding, runtime_classification
    )
    source_binding = source["binding"]
    if source_binding["source_kind"] not in policy.admitted_source_kinds:
        raise MaterialSourceRuntimeComparisonError(
            "material source kind is not admitted by the comparison policy"
        )
    if source_binding["pack_profile_id"] != policy.pack_profile_id or evidence["scope"]["pack_profile_id"] != policy.pack_profile_id:
        raise MaterialSourceRuntimeComparisonError(
            "material source/runtime pack profiles differ from policy"
        )
    if not _profile_matches(
        source_binding["platform_profile_id"],
        policy.platform_profile_id,
        policy,
    ) or runtime_classification.policy.platform_profile != policy.platform_profile_id:
        raise MaterialSourceRuntimeComparisonError(
            "material source/runtime platform profiles differ from policy"
        )
    if runtime_classification.scope.physical_side != policy.physical_side:
        raise MaterialSourceRuntimeComparisonError(
            "material runtime physical side differs from policy"
        )
    if (
        runtime_classification.policy.policy_id != policy.runtime_policy_id
        or runtime_classification.policy.sha256 != policy.runtime_policy_sha256
    ):
        raise MaterialSourceRuntimeComparisonError(
            "material runtime classification policy differs from comparison policy"
        )

    registrations: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    mutations: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    unresolved: list[dict[str, Any]] = []
    template_count = 0
    for row in source["declarations"]:
        descriptor = row["semantic_descriptor"]
        if descriptor["domain"] != "material":
            continue
        if descriptor["kind"] == "material-generator-template":
            template_count += 1
            continue
        if descriptor["kind"] == "material-registration":
            closure = row["attributes"]["declared_material_core"]["expected_verified_closure"]
            if (
                closure.get("closure_policy_id") != policy.runtime_policy_id
                or closure.get("closure_policy_sha256") != policy.runtime_policy_sha256
            ):
                raise MaterialSourceRuntimeComparisonError(
                    "material source expected-closure policy differs from runtime"
                )
            resource = _resource_from_registration(row)
            if resource is None:
                unresolved.append(
                    {
                        "source_declaration_id": row["source_declaration_id"],
                        "kind": "material-registration",
                        "state": "ambiguous",
                        "reason": "source-resource-location-unresolved",
                    }
                )
            else:
                registrations[resource].append(row)
        elif descriptor["kind"] == "material-mutation":
            resource = _resource_from_mutation(row)
            if resource is None:
                unresolved.append(
                    {
                        "source_declaration_id": row["source_declaration_id"],
                        "kind": "material-mutation",
                        "state": "ambiguous",
                        "reason": "mutation-target-unresolved",
                    }
                )
            else:
                mutations[resource].append(row)

    runtimes: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    resources_by_material_id: dict[str, str] = {}
    for row in runtime_classification.rows:
        resource = row["core"]["identity"]["registry_name"]
        runtimes[resource].append(row)
        resources_by_material_id[row["material_id"]] = resource
    resources = sorted(set(registrations) | set(mutations) | set(runtimes))
    capture_id = evidence["frozen_runtime"]["capture_id"]
    rows = [
        _material_row(
            resource,
            registrations[resource],
            mutations[resource],
            runtimes[resource],
            resources_by_material_id,
            policy,
            capture_id,
        )
        for resource in resources
    ]
    counts = Counter(row["aggregate_state"] for row in rows)
    lineage_counts = Counter(
        row["execution_lineage"]["state"] for row in rows
    )
    value: dict[str, Any] = {
        "format": MATERIAL_SOURCE_RUNTIME_COMPARISON_FORMAT,
        "schema_version": MATERIAL_SOURCE_RUNTIME_COMPARISON_SCHEMA_VERSION,
        "comparison_id": "",
        "authority": {
            "owner": "Atlas",
            "claim": "derived comparison of retained source intent and frozen runtime state",
            "source_owner": "Pack Program Studio",
            "runtime_owner": "Crucible",
        },
        "binding": {
            "pack_profile_id": policy.pack_profile_id,
            "platform_profile_id": policy.platform_profile_id,
            "physical_side": policy.physical_side,
            "source_declaration_set_id": source["declaration_set_id"],
            "source_program_id": source_binding["program_id"],
            "source_sha256": source_binding["source_sha256"],
            "runtime_classification_sha256": runtime_classification.sha256,
            "runtime_evidence_binding_id": evidence["evidence_binding_id"],
            "capture_id": capture_id,
            "crucible_snapshot_id": evidence["frozen_runtime"]["crucible_snapshot_id"],
            "frozen_stage": evidence["frozen_runtime"]["stage"],
            "manager_phase": evidence["frozen_runtime"]["manager_phase"],
            "operation_ledger_id": None,
        },
        "policy": {**policy.to_dict(), "sha256": policy.sha256},
        "materials": rows,
        "unresolved_source": sorted(
            unresolved, key=lambda row: row["source_declaration_id"]
        ),
        "summary": {
            "material_comparisons": len(rows),
            "state_counts": {
                state: counts[state] for state in sorted(counts)
            },
            "source_generator_templates_excluded": template_count,
            "unresolved_source_count": len(unresolved),
            "execution_lineage_established_count": 0,
            "execution_lineage_state_counts": dict(
                sorted(lineage_counts.items())
            ),
        },
        "limitations": [
            "State parity does not establish that a source operation executed or caused runtime state.",
            "Runtime materials without matching source registrations or mutations are unattributed runtime evidence, not automatic errors.",
            "Execution lineage remains not-established until a capture-bound Crucible operation ledger is reconciled.",
        ],
    }
    value["comparison_id"] = _comparison_identity(value)
    return validate_material_source_runtime_comparison(value)


def validate_material_source_runtime_comparison(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MaterialSourceRuntimeComparisonError(
            "material source/runtime comparison must be an object"
        )
    required = {
        "format",
        "schema_version",
        "comparison_id",
        "authority",
        "binding",
        "policy",
        "materials",
        "unresolved_source",
        "summary",
        "limitations",
    }
    if set(value) != required:
        raise MaterialSourceRuntimeComparisonError(
            "material source/runtime comparison has unexpected keys"
        )
    if (
        value["format"] != MATERIAL_SOURCE_RUNTIME_COMPARISON_FORMAT
        or value["schema_version"] != MATERIAL_SOURCE_RUNTIME_COMPARISON_SCHEMA_VERSION
    ):
        raise MaterialSourceRuntimeComparisonError(
            "unsupported material source/runtime comparison format"
        )
    if value["comparison_id"] != _comparison_identity(value):
        raise MaterialSourceRuntimeComparisonError(
            "material source/runtime comparison identity does not match content"
        )
    authority = value["authority"]
    if (
        not isinstance(authority, Mapping)
        or authority.get("owner") != "Atlas"
        or authority.get("source_owner") != "Pack Program Studio"
        or authority.get("runtime_owner") != "Crucible"
    ):
        raise MaterialSourceRuntimeComparisonError(
            "material comparison authority is malformed"
        )
    rows = value["materials"]
    if not isinstance(rows, list):
        raise MaterialSourceRuntimeComparisonError(
            "material comparison rows must be a list"
        )
    resources = [row.get("resource_location") for row in rows if isinstance(row, Mapping)]
    if len(resources) != len(rows) or resources != sorted(set(resources)):
        raise MaterialSourceRuntimeComparisonError(
            "material comparison resources must be unique and sorted"
        )
    summary = value["summary"]
    if not isinstance(summary, Mapping) or summary.get("material_comparisons") != len(rows):
        raise MaterialSourceRuntimeComparisonError(
            "material comparison summary is stale"
        )
    expected_counts = dict(sorted(Counter(row["aggregate_state"] for row in rows).items()))
    if summary.get("state_counts") != expected_counts:
        raise MaterialSourceRuntimeComparisonError(
            "material comparison state counts are stale"
        )
    expected_lineage_counts = dict(
        sorted(Counter(row["execution_lineage"]["state"] for row in rows).items())
    )
    if summary.get("execution_lineage_state_counts") != expected_lineage_counts:
        raise MaterialSourceRuntimeComparisonError(
            "material comparison execution-lineage state counts are stale"
        )
    established_count = sum(
        row["execution_lineage"]["state"] == "transition-established"
        for row in rows
    )
    if summary.get("execution_lineage_established_count") != established_count:
        raise MaterialSourceRuntimeComparisonError(
            "material comparison established-lineage count is stale"
        )
    unresolved = value["unresolved_source"]
    if not isinstance(unresolved, list) or summary.get("unresolved_source_count") != len(unresolved):
        raise MaterialSourceRuntimeComparisonError(
            "material comparison unresolved-source summary is stale"
        )
    return dict(value)


__all__ = [
    "MATERIAL_SOURCE_RUNTIME_COMPARISON_FORMAT",
    "MaterialSourceRuntimeComparisonError",
    "MaterialSourceRuntimeComparisonPolicy",
    "compare_material_source_to_runtime",
    "validate_material_source_runtime_comparison",
]
