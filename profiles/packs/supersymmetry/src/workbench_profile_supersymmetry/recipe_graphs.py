"""Profile-owned finite GT capture projection for the Atlas recipe workflow."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Callable, Iterator

from workbench_atlas_categorical_graph import CategoricalGraphBundleBuilder, edge_record, node_record
from workbench_crucible_runtime_snapshot.capture import read_runtime_capture

from . import capability_preparation, finite_item_matching, finite_ordinary_item_matching, item_names, recipe_capture_inputs
from .capability_preparation import CapabilityPreparationError, admit_capability_preparation
from .finite_item_matching import (
    ARTIFACT_SHA256, DOMAIN_SCOPE, MATCHING_ADAPTER, MATCHING_CATEGORY, MATCHING_MODEL,
    FiniteItemMatchingError, qualify_item_matching,
)
from .item_names import NAMES_ADAPTER, NAMES_CATEGORY, ItemNameObservationError, validate_item_names
from .finite_ordinary_item_matching import (
    ORDINARY_ADAPTER, ORDINARY_CATEGORY, ORDINARY_MODEL,
    FiniteOrdinaryItemMatchingError, qualify_ordinary_item_matching,
)


PROFILE_API_VERSION = 1
RECIPE_GRAPH_API_VERSION = 1
PROJECTION_FORMAT = "workbench-supersymmetry-finite-recipe-projection-v1"
_CATEGORIES = {
    "gt-recipes": "transformation-recipe",
    "gt-recipe-maps": "transformation-recipe-map",
    "gt-meta-tile-entities": "machine-core",
    "gt-machine-recipe-maps": "machine-transformation-binding",
}
_LIMITATIONS = (
    "Only captured finite GT recipe occurrences and machine/map bindings are projected; dynamic rules, crafting, smelting, other mods, quests and external acquisition are outside this graph.",
    "Exact input edges retain qualified captured alternatives. Unqualified ordinary and ore/circuit inputs, unknown input classes, wildcard metadata, custom NBT predicates, floating-NBT fluids and empty selectors retain observations with acceptance_complete=false and no claimed exact acceptance edges.",
    "Ordinary GTRecipeItemInput matching uses metadata, full NBT equality and item capabilities; captured representative identities do not establish accepted alternatives or complete matching closure.",
    "Item/fluid identity excludes quantity and preserves captured metadata and NBT; quantities, reusable inputs and chance metadata remain observations, not a balance, guaranteed yield or independent-supply proof.",
    "Cycles and alternative producers do not establish bootstrap supply, machine formation, energy availability or player reachability. Original source causation is not projected.",
)
_RECIPE_FIELDS = frozenset({
    "category", "chanced_fluid_outputs", "chanced_item_outputs", "crafttweaker_recipe",
    "duration", "eut", "fluid_inputs", "fluid_outputs", "groovy_recipe", "hidden",
    "item_inputs", "item_outputs", "properties", "runtime_class",
})
_SELECTOR_FIELDS = frozenset({
    "amount", "fluid_stack", "has_nbt_matching_condition", "item_stack_representatives",
    "nbt_condition", "nbt_matcher", "non_consumable", "ordinal", "ore_dictionary",
    "ore_dictionary_id", "ore_dictionary_name", "runtime_class",
})
_INPUT_PACKAGE = "gregtech.api.recipes.ingredients."
_BRANCH_INPUT_FORMAT = "workbench-supersymmetry-branch-observation-input-v1"
_BRANCH_PACK = {"revision": "575b540c64f2d2e33222660da1a78694bcae3eb9",
                "tree": "7bda9fceffeee614f7c694c0d908ec76e72e34f8"}
_BRANCH_PLATFORM = {"minecraft_version": "1.12.2", "loader": "forge",
                    "loader_version": "14.23.5.2860", "java_major": 8}
_BRANCH_ARTIFACTS = {
    **ARTIFACT_SHA256,
    "groovyscript_sha256": "07617b7ce9170a857199bd61d730a0db3af2685cf83d31734a2d4b628fda7533",
    "susy_core_sha256": "ec9b56070f8788b63f5d381c765cd05ed66ba42755e1f87ad2eb6605285a5a46",
}


class RecipeGraphProjectionError(ValueError):
    """A capture cannot be projected within the declared finite recipe model."""


def _text(value: Any, name: str) -> str:
    if type(value) is not str or not value or any(ord(c) < 32 for c in value):
        raise RecipeGraphProjectionError(f"invalid {name}")
    return value


def _integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise RecipeGraphProjectionError(f"invalid {name}")
    return value


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _object(value: Any, name: str, fields: frozenset[str] | None = None) -> dict[str, Any]:
    if type(value) is not dict or (fields is not None and set(value) != fields):
        raise RecipeGraphProjectionError(f"invalid {name} object/fields")
    return value


def _array(value: Any, name: str) -> list[Any]:
    if type(value) is not list:
        raise RecipeGraphProjectionError(f"invalid {name} array")
    return value


def _boolean(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise RecipeGraphProjectionError(f"invalid {name} boolean")
    return value


def _stack(stack: Any, *, fluid: bool) -> dict[str, Any]:
    expected = frozenset({"amount", "fluid_name", "tag"} if fluid else
                         {"count", "item_damage", "metadata", "registry_name", "tag"})
    stack = _object(stack, "recipe stack", expected)
    _text(stack["fluid_name" if fluid else "registry_name"], "stack name")
    _integer(stack["amount" if fluid else "count"], "observed stack quantity", minimum=1)
    if not fluid:
        _integer(stack["metadata"], "item metadata")
        _integer(stack["item_damage"], "item damage")
    if stack["tag"] is not None:
        _object(stack["tag"], "stack NBT")
    return stack


def _has_floating_nbt(value: Any, check_cancelled: Callable[[], None]) -> bool:
    pending = [value]
    while pending:
        check_cancelled()
        current = pending.pop()
        if isinstance(current, dict):
            if current.get("tag_id") in (5, 6):
                return True
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return False


def _acceptance_gaps(selector: dict[str, Any], *, fluid: bool,
                     check_cancelled: Callable[[], None]) -> list[str]:
    gaps = []
    runtime_class = selector["runtime_class"]
    expected_class = _INPUT_PACKAGE + ("GTRecipeFluidInput" if fluid else "GTRecipeItemInput")
    if selector["ore_dictionary"] or runtime_class == _INPUT_PACKAGE + "GTRecipeOreInput":
        gaps.append("ore-dictionary-matching-closure-not-projected")
    elif runtime_class == _INPUT_PACKAGE + "IntCircuitIngredient":
        gaps.append("circuit-configuration-matching-closure-not-projected")
    elif runtime_class != expected_class:
        gaps.append("unqualified-input-runtime-class")
    elif not fluid:
        # getInputStacks() returns copies, while acceptsStack() additionally
        # checks the original item's metadata, NBT equality and capabilities.
        # Representatives therefore prove neither acceptance nor its closure.
        gaps.append("ordinary-item-matching-closure-not-qualified")
    if selector["has_nbt_matching_condition"] or selector["nbt_matcher"] is not None or selector["nbt_condition"] is not None:
        gaps.append("custom-nbt-matching-not-executed")
    if fluid and _has_floating_nbt(selector["fluid_stack"]["tag"], check_cancelled):
        # NBT float/double equality equates signed zero and can reject copied
        # NaNs. Retained exact resource identity is not that matching relation.
        gaps.append("floating-nbt-equality-not-qualified")
    if not fluid:
        if not selector["item_stack_representatives"]:
            gaps.append("no-captured-item-alternatives")
        if any(stack["metadata"] == 32767 or stack["item_damage"] == 32767
               for stack in selector["item_stack_representatives"]):
            gaps.append("wildcard-item-metadata-not-expanded")
    return gaps


def _validate_recipe(value: Any) -> dict[str, Any]:
    recipe = _object(value, "recipe", _RECIPE_FIELDS)
    _text(recipe["category"], "recipe category")
    _text(recipe["runtime_class"], "recipe runtime class")
    _integer(recipe["duration"], "recipe duration", minimum=1)
    if type(recipe["eut"]) is not int:
        raise RecipeGraphProjectionError("invalid recipe EUt")
    for field in ("hidden", "groovy_recipe", "crafttweaker_recipe"):
        _boolean(recipe[field], field)
    for prop in _array(recipe["properties"], "recipe properties"):
        _object(prop, "recipe property")
    for family, fluid in (("item_inputs", False), ("fluid_inputs", True)):
        for ordinal, selector in enumerate(_array(recipe[family], family)):
            _object(selector, "recipe selector", _SELECTOR_FIELDS)
            if _integer(selector["ordinal"], "selector ordinal") != ordinal:
                raise RecipeGraphProjectionError("recipe selector ordinal differs")
            # GT RecipeBuilder accepts zero input amounts. Retain the native
            # slot; quantity feasibility belongs to the route/scenario model.
            _integer(selector["amount"], "selector amount", minimum=0)
            _text(selector["runtime_class"], "selector runtime class")
            for key in ("non_consumable", "ore_dictionary", "has_nbt_matching_condition"):
                _boolean(selector[key], key)
            for stack in _array(selector["item_stack_representatives"], "item representatives"):
                _stack(stack, fluid=False)
            if fluid:
                _stack(selector["fluid_stack"], fluid=True)
                if selector["item_stack_representatives"] or selector["ore_dictionary"]:
                    raise RecipeGraphProjectionError("fluid selector has item alternatives")
            elif selector["fluid_stack"] is not None:
                raise RecipeGraphProjectionError("item selector has a fluid stack")
            if selector["ore_dictionary"]:
                _text(selector["ore_dictionary_name"], "ore dictionary name")
                _integer(selector["ore_dictionary_id"], "ore dictionary ID")
            elif selector["ore_dictionary_name"] is not None or selector["ore_dictionary_id"] is not None:
                raise RecipeGraphProjectionError("non-ore selector has an ore binding")
    for family, fluid in (("item_outputs", False), ("fluid_outputs", True),
                          ("chanced_item_outputs", False), ("chanced_fluid_outputs", True)):
        chance = family.startswith("chanced")
        if chance:
            container = _object(recipe[family], family, frozenset({"entries", "logic_class"}))
            _text(container["logic_class"], "chance logic class")
            entries = _array(container["entries"], "chance entries")
        else:
            entries = _array(recipe[family], family)
        for ordinal, entry in enumerate(entries):
            fields = {"ordinal", "value"}
            if chance:
                fields.update({"chance", "chance_boost", "runtime_class"})
            _object(entry, "output entry", frozenset(fields))
            if _integer(entry["ordinal"], "output ordinal") != ordinal:
                raise RecipeGraphProjectionError("recipe output ordinal differs")
            _stack(entry["value"], fluid=fluid)
            if chance:
                _integer(entry["chance"], "chance")
                if type(entry["chance_boost"]) is not int:
                    raise RecipeGraphProjectionError("invalid chance boost")
                _text(entry["runtime_class"], "chance entry class")
    return recipe


def _resource(stack: dict[str, Any], *, fluid: bool) -> dict[str, Any]:
    """Keep amounts on edges so ten units and one unit share a resource key."""
    _stack(stack, fluid=fluid)
    if fluid:
        name = _text(stack.get("fluid_name"), "fluid name")
        properties = {"name": name, "tag": stack["tag"],
                      "identity_model": "captured-fluid-variant-v1"}
        kind = "forge-fluid"
        key = name
    else:
        name = _text(stack.get("registry_name"), "item registry name")
        properties = {"registry_name": name,
                      "metadata": _integer(stack.get("metadata"), "item metadata"),
                      "item_damage": _integer(stack.get("item_damage"), "item damage"),
                      "tag": stack["tag"], "identity_model": "captured-item-variant-v1"}
        kind = "item-variant"
        key = f"{name}|{properties['metadata']}|{properties['item_damage']}"
    if properties["tag"] is not None:
        key += "|tag:sha256:" + hashlib.sha256(_json(properties["tag"]).encode()).hexdigest()
    return node_record(kind, key, properties)


def _admit_input(inputs: dict[str, Any], manifest: dict[str, Any]) -> tuple[str, str]:
    pack_binding = _text(inputs.get("pack_binding_id"), "pack binding")
    platform_binding = _text(inputs.get("platform_binding_id"), "platform binding")
    if inputs.get("format") == recipe_capture_inputs.INPUT_FORMAT:
        try:
            pack_binding, platform_binding = recipe_capture_inputs.validate_capture_input(inputs)
        except recipe_capture_inputs.RecipeCaptureInputError as exc:
            raise RecipeGraphProjectionError(str(exc)) from exc
        if any(inputs.get(key) != manifest[key] for key in ("candidate_lock_sha256", "adapter_profile_sha256")):
            raise RecipeGraphProjectionError("developer observation capture protocol binding differs")
    elif inputs.get("format") == _BRANCH_INPUT_FORMAT:
        if (inputs.get("pack_source") != _BRANCH_PACK or inputs.get("platform") != _BRANCH_PLATFORM
                or type(inputs["platform"].get("java_major")) is not int
                or inputs.get("runtime_artifacts") != _BRANCH_ARTIFACTS
                or inputs.get("physical_side") != "dedicated_server"):
            raise RecipeGraphProjectionError("unsupported Supersymmetry branch observation inputs")
        expected_pack = "supersymmetry-branch-observation:sha256:" + hashlib.sha256(_json(_BRANCH_PACK).encode()).hexdigest()
        expected_platform = "forge-branch-observation:sha256:" + hashlib.sha256(_json({
            "platform": _BRANCH_PLATFORM, "runtime_artifacts": _BRANCH_ARTIFACTS}).encode()).hexdigest()
        if pack_binding != expected_pack or platform_binding != expected_platform:
            raise RecipeGraphProjectionError("branch observation binding digest differs")
        if any(inputs.get(key) != manifest[key] for key in ("candidate_lock_sha256", "adapter_profile_sha256")):
            raise RecipeGraphProjectionError("branch observation capture protocol binding differs")
    else:
        if "observation_preparation" in inputs:
            raise RecipeGraphProjectionError("capability preparation requires the explicit Forge branch profile")
        if re.fullmatch(r"supersymmetry-foundation-target-binding:sha256:[0-9a-f]{64}", pack_binding) is None:
            raise RecipeGraphProjectionError("input manifest is not bound to the Supersymmetry capture profile")
        if re.fullmatch(r"cleanroom-foundation-target-binding:sha256:[0-9a-f]{64}", platform_binding) is None:
            raise RecipeGraphProjectionError("input manifest is not bound to a Cleanroom foundation target")
    return pack_binding, platform_binding


def project_capture(
    capture_root: Path, output: Path, *, input_manifest: Path,
    max_source_bytes: int = 1024 * 1024 * 1024,
    check_cancelled: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Project a sealed capture, publishing a new graph only after validation.

    The profile interprets GT records; Crucible verifies capture custody; Atlas
    owns graph construction. No game is launched and no input bytes are edited.
    """
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise RecipeGraphProjectionError("recipe graph output already exists")
    cancel = check_cancelled or (lambda: None)
    cancel()
    capture = read_runtime_capture(
        capture_root, categories=_CATEGORIES, input_manifest=input_manifest,
        max_source_bytes=max_source_bytes, record_digest_fields={"gt-recipes": ("recipe",)},
    )
    categories = dict(_CATEGORIES)
    present = {row["adapter_id"] for row in capture.manifest["categories"]}
    categories.update({key: category for key, category in (
        (MATCHING_ADAPTER, MATCHING_CATEGORY), (NAMES_ADAPTER, NAMES_CATEGORY),
        (ORDINARY_ADAPTER, ORDINARY_CATEGORY),
    ) if key in present})
    if categories != _CATEGORIES:
        original_manifest_sha256 = capture.manifest_file_sha256
        cancel()
        capture = read_runtime_capture(
            capture_root, categories=categories, input_manifest=input_manifest,
            max_source_bytes=max_source_bytes, record_digest_fields={"gt-recipes": ("recipe",)},
        )
        if capture.manifest_file_sha256 != original_manifest_sha256:
            raise RecipeGraphProjectionError("capture changed while admitting optional item observations")
    cancel()
    for key, category in capture.categories.items():
        if category["category_id"] != categories[key] or category["checkpoint_id"] != "post-start-end-tick":
            raise RecipeGraphProjectionError(f"unsupported recipe capture category/checkpoint: {key}")
    inputs = capture.input_manifest
    pack_binding, platform_binding = _admit_input(inputs, capture.manifest)
    records = {key: value["records"] for key, value in capture.categories.items()}

    def evidence(category: str, ordinal: int, pointer: str = "") -> list[dict[str, Any]]:
        return [{"capture_id": capture.manifest["capture_id"], "adapter_id": category,
                 "record_ordinal": ordinal, "record_sha256": capture.record_sha256[category][ordinal],
                 "pointer": f"/records/{ordinal}{pointer}"}]

    maps: dict[str, dict[str, Any]] = {}
    for ordinal, row in enumerate(records["gt-recipe-maps"]):
        _object(row, "recipe map")
        name = _text(row.get("name"), "recipe map name")
        if row.get("record_type") != "gt-recipe-map" or name in maps:
            raise RecipeGraphProjectionError("invalid or duplicate recipe map")
        _integer(row.get("union_identity_count"), "recipe map union count")
        maps[name] = node_record("gt-recipe-map", name, row, evidence("gt-recipe-maps", ordinal))
    machines: dict[str, dict[str, Any]] = {}
    for ordinal, row in enumerate(records["gt-meta-tile-entities"]):
        _object(row, "machine")
        name = _text(row.get("registry_name"), "machine registry name")
        if row.get("record_type") != "gt-meta-tile-entity" or name in machines:
            raise RecipeGraphProjectionError("invalid or duplicate machine")
        machines[name] = node_record("gt-machine", name, row, evidence("gt-meta-tile-entities", ordinal))
    recipes = records["gt-recipes"]
    signatures: dict[tuple[str, str], set[int]] = defaultdict(set)
    per_map: Counter[str] = Counter()
    resources: dict[str, dict[str, Any]] = {}

    def recipe_key(row: dict[str, Any]) -> str:
        return f"{row['recipe_map']}|{row['semantic_sha256']}|{row['duplicate_ordinal']}"

    def stacks(recipe: dict[str, Any]) -> Iterator[tuple[dict[str, Any], bool]]:
        for family, fluid in (("item_inputs", False), ("fluid_inputs", True)):
            for selector in recipe[family]:
                candidates = ([selector["fluid_stack"]] if fluid else selector["item_stack_representatives"])
                for stack in candidates:
                    yield stack, fluid
        for family, fluid in (("item_outputs", False), ("fluid_outputs", True),
                              ("chanced_item_outputs", False), ("chanced_fluid_outputs", True)):
            entries = recipe[family]["entries"] if family.startswith("chanced") else recipe[family]
            for entry in entries:
                yield entry["value"], fluid

    for ordinal, row in enumerate(recipes):
        if ordinal % 256 == 0:
            cancel()
        _object(row, "recipe occurrence", frozenset({"record_type", "recipe_map", "semantic_sha256",
                "duplicate_ordinal", "lookup_active", "category_present", "recipe"}))
        recipe_map = _text(row.get("recipe_map"), "recipe occurrence map")
        if row.get("record_type") != "gt-recipe" or recipe_map not in maps:
            raise RecipeGraphProjectionError("recipe has an unsupported type or missing map")
        if any(type(row.get(key)) is not bool for key in ("lookup_active", "category_present")):
            raise RecipeGraphProjectionError("recipe lookup/category membership is unknown")
        if not row["lookup_active"] and not row["category_present"]:
            raise RecipeGraphProjectionError("recipe has no captured membership")
        if row.get("semantic_sha256") != capture.record_field_sha256["gt-recipes"][ordinal]["recipe"]:
            raise RecipeGraphProjectionError("recipe semantic digest differs from the captured recipe")
        duplicate = _integer(row.get("duplicate_ordinal"), "duplicate ordinal")
        group = signatures[(row["recipe_map"], row["semantic_sha256"])]
        if duplicate in group:
            raise RecipeGraphProjectionError("recipe occurrence identity is duplicated")
        group.add(duplicate)
        per_map[row["recipe_map"]] += 1
        recipe = _validate_recipe(row["recipe"])
        for stack, fluid in stacks(recipe):
            resource = _resource(stack, fluid=fluid)
            _integer(stack.get("amount" if fluid else "count"), "observed stack quantity", minimum=1)
            resources.setdefault(resource["id"], resource)
    if any(group != set(range(len(group))) for group in signatures.values()):
        raise RecipeGraphProjectionError("recipe duplicate ordinals are not contiguous")
    for name, node in maps.items():
        if node["properties"].get("union_identity_count") != per_map[name]:
            raise RecipeGraphProjectionError("recipe map union count differs from captured occurrences")
    matching = None
    if MATCHING_ADAPTER in records:
        try:
            matching = qualify_item_matching(records[MATCHING_ADAPTER], recipes,
                                             capture.record_sha256["gt-recipes"], check_cancelled=cancel)
        except FiniteItemMatchingError as exc:
            raise RecipeGraphProjectionError(str(exc)) from exc
    ordinary_matching = None
    if ORDINARY_ADAPTER in records:
        try:
            ordinary_matching = qualify_ordinary_item_matching(records[ORDINARY_ADAPTER], recipes,
                capture.record_sha256["gt-recipes"], check_cancelled=cancel)
        except FiniteOrdinaryItemMatchingError as exc:
            raise RecipeGraphProjectionError(str(exc)) from exc
    try:
        preparation = admit_capability_preparation(capture, validate_recipe=_validate_recipe,
                                                   check_cancelled=cancel)
    except CapabilityPreparationError as exc:
        raise RecipeGraphProjectionError(str(exc)) from exc
    name_observations = None
    if NAMES_ADAPTER in records:
        if "item_name_queries" in inputs and type(inputs["item_name_queries"]) is not list:
            raise RecipeGraphProjectionError("invalid requested item name queries")
        try:
            names = validate_item_names(records[NAMES_ADAPTER],
                                        requested_queries=inputs.get("item_name_queries"),
                                        check_cancelled=cancel)
        except ItemNameObservationError as exc:
            raise RecipeGraphProjectionError(str(exc)) from exc
        name_observations = {
            "authority": item_names.RESOLVER_AUTHORITY,
            "scope": "observed resolver queries at the capture checkpoint; not registration causation or every possible query",
            "binding_count": len(names), "annotated_binding_count": 0,
            "unresolved_queries": [], "outside_recipe_resources": [],
        }
        for binding in names:
            cancel()
            row = binding.record
            refs = evidence(NAMES_ADAPTER, binding.ordinal)
            if row["resolution"] == "unresolved":
                name_observations["unresolved_queries"].append({"query": row["query"], "evidence": refs})
                continue
            resource = _resource(row["stack"], fluid=False)
            if resource["id"] not in resources:
                name_observations["outside_recipe_resources"].append({
                    "query": row["query"], "resource_id": resource["id"], "evidence": refs,
                })
                continue
            node = resources[resource["id"]]
            node["properties"].setdefault("observed_item_names", []).append({
                key: row[key] for key in ("query", "namespace", "name", "authority", "resolution")
            })
            node["evidence"].extend(refs)
            name_observations["annotated_binding_count"] += 1

    def machine_edges() -> Iterator[dict[str, Any]]:
        for index, row in enumerate(records["gt-machine-recipe-maps"]):
            _object(row, "machine-map binding")
            machine = _text(row.get("machine"), "machine-map machine")
            if machine not in machines:
                raise RecipeGraphProjectionError("machine-map binding refers to an absent machine")
            record_type = row.get("record_type")
            if record_type == "gt-machine-no-recipe-map-binding":
                continue
            if record_type == "gt-machine-recipe-map-selection":
                name = row.get("current_recipe_map")
                if name is None:
                    continue
                name = _text(name, "selected recipe map")
                relation = "currently-selects-recipe-map"
            elif record_type == "gt-machine-recipe-map-binding":
                name = _text(row.get("recipe_map"), "bound recipe map")
                relation = "uses-recipe-map"
            else:
                raise RecipeGraphProjectionError("unknown machine-map binding record")
            if name not in maps:
                raise RecipeGraphProjectionError("machine binding refers to an absent recipe map")
            yield edge_record(relation, machines[machine]["id"], maps[name]["id"], row,
                              evidence("gt-machine-recipe-maps", index), semantic_key=str(index))

    io_fields = {"item_inputs", "fluid_inputs", "item_outputs", "fluid_outputs",
                 "chanced_item_outputs", "chanced_fluid_outputs"}

    def recipe_node(row: dict[str, Any], index: int) -> dict[str, Any]:
        properties = {key: value for key, value in row["recipe"].items() if key not in io_fields}
        properties.update({key: value for key, value in row.items() if key != "recipe"})
        properties["captured_input_counts"] = {"item": len(row["recipe"]["item_inputs"]),
                                               "fluid": len(row["recipe"]["fluid_inputs"])}
        return node_record("gt-recipe", recipe_key(row), properties, evidence("gt-recipes", index))

    def qualified_item(index: int, slot: int) -> tuple[Any, Any, str, str] | None:
        for admission, adapter, model in ((matching, MATCHING_ADAPTER, MATCHING_MODEL),
                                           (ordinary_matching, ORDINARY_ADAPTER, ORDINARY_MODEL)):
            qualified = admission.selectors.get((index, slot)) if admission else None
            if qualified is not None:
                return admission, qualified, adapter, model
        return None

    def selector_node(row: dict[str, Any], index: int, family: str, selector: dict[str, Any]) -> dict[str, Any]:
        properties = {key: value for key, value in selector.items()
                      if key not in {"item_stack_representatives", "fluid_stack"}}
        properties["acceptance_model"] = "captured-stack-representatives"
        gaps = _acceptance_gaps(selector, fluid=family == "fluid_inputs", check_cancelled=cancel)
        properties["acceptance_complete"] = not gaps
        properties["acceptance_gaps"] = gaps
        properties["matching_limits"] = (
            ["item-capability-compatibility-not-captured"]
            if selector["runtime_class"] == _INPUT_PACKAGE + "GTRecipeItemInput" else []
        )
        qualification = qualified_item(index, selector["ordinal"]) if family == "item_inputs" else None
        refs = evidence("gt-recipes", index, f"/recipe/{family}/{selector['ordinal']}")
        if qualification is not None:
            admission, qualified, adapter, model = qualification
            witness = records[adapter][qualified.witness_ordinal]
            properties.update({
                "acceptance_model": model, "acceptance_complete": True, "acceptance_gaps": [],
                "acceptance_domain": {"scope": DOMAIN_SCOPE, "sha256": admission.domain.sha256,
                                      "count": len(admission.domain.stacks)},
                "matching_state": ({"matcher": "ordinary-item-null-tag-zero-writers", "targets": witness["targets"]}
                                   if adapter == ORDINARY_ADAPTER else
                                   {key: witness[key] for key in ("matcher", "matching_configurations", "integrated_circuit")}),
            })
            if adapter == ORDINARY_ADAPTER:
                properties["matching_limits"] = []
                properties["acceptance_domain"].update({"occurrence_count": admission.occurrence_count,
                                                       "occurrences_sha256": admission.occurrences_sha256})
            refs += evidence(adapter, qualified.witness_ordinal)
        if gaps or qualification is not None:
            properties["captured_item_stack_representatives"] = selector["item_stack_representatives"]
            properties["captured_fluid_stack"] = selector["fluid_stack"]
        return node_record("gt-recipe-input-selector", f"{recipe_key(row)}|{family}|{selector['ordinal']}",
                           properties, refs)

    def recipe_nodes() -> Iterator[dict[str, Any]]:
        for index, row in enumerate(recipes):
            if index % 256 == 0:
                cancel()
            yield recipe_node(row, index)
            for family in ("item_inputs", "fluid_inputs"):
                for selector in row["recipe"][family]:
                    yield selector_node(row, index, family, selector)

    def recipe_edges() -> Iterator[dict[str, Any]]:
        for index, row in enumerate(recipes):
            if index % 128 == 0:
                cancel()
            recipe = row["recipe"]
            source = recipe_node(row, index)["id"]
            refs = evidence("gt-recipes", index)
            yield edge_record("contained-in-recipe-map", source, maps[row["recipe_map"]]["id"], {}, refs)
            for family, fluid in (("item_inputs", False), ("fluid_inputs", True)):
                for selector in recipe[family]:
                    target = selector_node(row, index, family, selector)
                    yield edge_record("has-fluid-input-selector" if fluid else "has-item-input-selector",
                                      source, target["id"], {"ordinal": selector["ordinal"]}, refs)
                    qualification = qualified_item(index, selector["ordinal"]) if not fluid else None
                    if qualification is not None:
                        admission, qualified, adapter, _ = qualification
                        for domain_ordinal in qualified.accepted_domain_ordinals:
                            cancel()
                            stack = admission.domain.stacks[domain_ordinal]
                            locator = admission.domain.locators[domain_ordinal]
                            candidate_index = int(locator["pointer"].split("/")[2])
                            candidate_pointer = locator["pointer"].split(f"/records/{candidate_index}", 1)[1]
                            yield edge_record("accepts-gt-item-alternative", target["id"], _resource(stack, fluid=False)["id"],
                                              {"amount": selector["amount"], "non_consumable": selector["non_consumable"],
                                               "domain_ordinal": domain_ordinal, "observed_stack": stack},
                                              evidence(adapter, qualified.witness_ordinal) +
                                              evidence("gt-recipes", candidate_index, candidate_pointer),
                                              semantic_key=f"domain:{domain_ordinal}")
                        continue
                    candidates = [selector["fluid_stack"]] if fluid else selector["item_stack_representatives"]
                    for position, stack in enumerate(candidates):
                        resource = _resource(stack, fluid=fluid)
                        exact = target["properties"]["acceptance_complete"]
                        relation = ("accepts-gt-fluid-input" if fluid else "accepts-gt-item-alternative") if exact else (
                            "observes-gt-fluid-input-representative" if fluid else "observes-gt-item-input-representative")
                        yield edge_record(relation,
                                          target["id"], resource["id"],
                                          {"amount": selector["amount"], "non_consumable": selector["non_consumable"],
                                           "representative_ordinal": position, "observed_stack": stack},
                                          refs, semantic_key=str(position))
            for family, fluid in (("item_outputs", False), ("fluid_outputs", True),
                                  ("chanced_item_outputs", False), ("chanced_fluid_outputs", True)):
                chance = family.startswith("chanced")
                entries = recipe[family]["entries"] if chance else recipe[family]
                for position, entry in enumerate(entries):
                    if entry.get("ordinal") != position:
                        raise RecipeGraphProjectionError("recipe output ordinal differs")
                    stack = entry["value"]
                    resource = _resource(stack, fluid=fluid)
                    props = {key: value for key, value in entry.items() if key != "value"}
                    props.update({"amount": stack["amount" if fluid else "count"], "observed_stack": stack,
                                  "output_family": family, "chanced": chance})
                    if chance:
                        props["logic_class"] = recipe[family]["logic_class"]
                    yield edge_record("produces-gt-fluid" if fluid else "produces-gt-item", source,
                                      resource["id"], props, refs, semantic_key=f"{family}:{position}")

    category_bindings = {key: {k: v for k, v in value.items() if k != "records"}
                         for key, value in capture.categories.items()}
    projection_sources = {"recipe_graphs.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                          "finite_item_matching.py": hashlib.sha256(Path(finite_item_matching.__file__).read_bytes()).hexdigest(),
                          "finite_ordinary_item_matching.py": hashlib.sha256(Path(finite_ordinary_item_matching.__file__).read_bytes()).hexdigest(),
                          "item_names.py": hashlib.sha256(Path(item_names.__file__).read_bytes()).hexdigest()}
    if inputs.get("format") == recipe_capture_inputs.INPUT_FORMAT:
        projection_sources["recipe_capture_inputs.py"] = hashlib.sha256(
            Path(recipe_capture_inputs.__file__).read_bytes()).hexdigest()
    if preparation is not None:
        projection_sources["capability_preparation.py"] = hashlib.sha256(
            Path(capability_preparation.__file__).read_bytes()).hexdigest()
    projection_source_sha256 = hashlib.sha256(_json(projection_sources).encode()).hexdigest()
    limitations = list(_LIMITATIONS)
    if matching is not None:
        limitations[1] = ("Native ore/circuit acceptance observations are checked against pinned original predicates over the complete declared captured item domain. "
                          "Missing witnesses, custom NBT predicates and other unqualified selectors retain observations without exact acceptance claims. "
                          "Finite-domain completeness does not enumerate every possible game stack or prove recipe lookup, execution or supply.")
    if ordinary_matching is not None:
        limitations.append("Ordinary item matching is qualified only for observed null-tag internal targets and candidate copies with initialized capabilities and zero serializable writers. All raw occurrences must be covered before candidate collapse, with unchanged visible identity and count across initialization. This finite copied-domain result does not establish future inventory matching, item acquisition or machine execution.")
    if preparation is not None:
        limitations.append("These recipes were observed after an explicitly declared capability preparation phase that may change item identity or count. The sealed auxiliary payload retains original recipes and before/after object transitions; no equivalence with pre-preparation resources or passive-observation claim is made. Ordinary matching still requires unchanged initialization and copy identities within the later effective samples.")
    binding = {"projection_format": PROJECTION_FORMAT,
               "projection_source_sha256": projection_source_sha256,
               "projection_sources": projection_sources,
               "capture_manifest_sha256": capture.manifest_file_sha256,
               "capture_manifest": capture.manifest, "category_results": category_bindings,
               "input_manifest": inputs}
    if name_observations is not None:
        binding["item_name_observations"] = name_observations
    if preparation is not None:
        binding["capability_preparation"] = preparation
    if ordinary_matching is not None:
        binding["ordinary_item_matching"] = {
            "model": ORDINARY_MODEL, "domain_sha256": ordinary_matching.domain.sha256,
            "domain_count": len(ordinary_matching.domain.stacks),
            "occurrence_count": ordinary_matching.occurrence_count,
            "occurrences_sha256": ordinary_matching.occurrences_sha256,
            "copy_identities_unchanged": ordinary_matching.copy_identities_unchanged,
            "copy_counts_unchanged": ordinary_matching.copy_counts_unchanged,
            "capability_initialization_complete": ordinary_matching.capability_initialization_complete,
            "initialization_identities_unchanged": ordinary_matching.initialization_identities_unchanged,
            "initialization_counts_unchanged": ordinary_matching.initialization_counts_unchanged,
            "qualified_selector_count": len(ordinary_matching.selectors),
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    # Each projection has a single owned staging directory; failures cannot
    # replace either an existing graph or the retained capture.
    staging = Path(tempfile.mkdtemp(prefix=".atlas-recipe-projection-", dir=output.parent))
    try:
        scope = {"pack_profile_id": "workbench-pack:supersymmetry",
                                      "projection_format": PROJECTION_FORMAT,
                                      "projection_source_sha256": projection_source_sha256,
                                      "pack_binding_id": pack_binding, "platform_binding_id": platform_binding,
                                      "physical_side": capture.manifest["physical_side"],
                                      "lifecycle_checkpoint_id": "post-start-end-tick",
                                      "candidate_lock_sha256": capture.manifest["candidate_lock_sha256"]}
        if preparation is not None:
            scope["recipe_observation_state"] = "after-capability-preparation"
            scope["observation_preparation_policy"] = preparation["policy"]
        builder = CategoricalGraphBundleBuilder(
            staging / "graph", scope=scope,
            evidence_binding=binding,
        )
        builder.add_partition("machines", classification="captured machine and map definitions",
                              dependencies=(), nodes=iter([*maps.values(), *machines.values()]), edges=machine_edges(),
                              evidence_categories=tuple(sorted(set(_CATEGORIES) - {"gt-recipes"})))
        builder.add_partition("resources", classification="exact captured resource representatives",
                              dependencies=(), nodes=(resources[key] for key in sorted(resources)), edges=(),
                              evidence_categories=tuple(sorted(["gt-recipes"] + ([NAMES_ADAPTER] if name_observations is not None else []))))
        builder.add_partition("recipes", classification="finite captured GT recipe occurrences",
                              dependencies=("machines", "resources"), nodes=recipe_nodes(), edges=recipe_edges(),
                              evidence_categories=tuple(sorted(["gt-recipes"] + ([MATCHING_ADAPTER] if matching else []) +
                                                               ([ORDINARY_ADAPTER] if ordinary_matching else []))), limitations=limitations)
        cancel()
        manifest = builder.close()
        cancel()
        (staging / "graph").rename(output)
        return {"format": PROJECTION_FORMAT, "state": "complete", "root": str(output.resolve()),
                "graph_set_id": manifest["graph_set_id"], "summary": manifest["summary"],
                "recipe_count": len(recipes), "recipe_map_count": len(maps),
                "verified_capture_bytes": capture.verified_payload_bytes,
                "capture_manifest_sha256": capture.manifest_file_sha256, "limitations": limitations}
    except (KeyError, TypeError, IndexError) as exc:
        raise RecipeGraphProjectionError(f"unsupported finite recipe capture shape: {exc}") from exc
    finally:
        shutil.rmtree(staging)
