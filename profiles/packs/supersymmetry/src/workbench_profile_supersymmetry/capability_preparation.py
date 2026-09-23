"""Admit an explicit, retained state transition before effective recipe samples.

Preparation can change a stack. It never establishes equivalence between the
before and after identities, and does not relax ordinary matching qualification.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Callable

from workbench_crucible_runtime_snapshot.capture import (
    capture_canonical_bytes, read_capture_payload,
)

POLICY = "forge-loli-original-capability-materialization-v1"
DECLARATION = {"policy": POLICY, "phase": "before-two-effective-samples"}
PAYLOAD_FILE = "capability-preparation.json"
_BINDINGS = {"capture_id", "launch_id", "physical_side", "input_manifest_sha256",
             "candidate_lock_sha256", "adapter_profile_sha256"}
_COUNTS = {"recipe_count", "object_count", "reference_count", "occurrence_reference_count",
           "stored_target_reference_count", "initializer_invocation_count", "changed_object_count",
           "unknown_object_count"}
_OBJECT = {"object_ordinal", "stack_before", "stack_before_initializer", "stack_after_initializer",
           "stack_after", "initialization_state_before", "initialization_state_before_initializer",
           "initialization_state_after_initializer", "initialization_state_after", "initializer_invoked",
           "references"}
_TARGET = {"recipe_record_sha256", "selector_ordinal", "item_entry_ordinal", "metadata_entry_ordinal",
           "tag_entry_ordinal", "registry_name", "metadata", "tag"}
_ROW = {"record_type", "recipe_map", "semantic_sha256", "duplicate_ordinal", "lookup_active",
        "category_present", "recipe"}
_STACK = {"registry_name", "metadata", "item_damage", "count", "tag"}


class CapabilityPreparationError(ValueError):
    """Preparation evidence does not support the declared observation scope."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CapabilityPreparationError(message)


def _object(value: Any, fields: set[str], name: str) -> dict[str, Any]:
    _require(type(value) is dict and set(value) == fields, "invalid preparation " + name + " fields")
    return value


def _array(value: Any, name: str) -> list[Any]:
    _require(type(value) is list, "invalid preparation " + name)
    return value


def _integer(value: Any, name: str) -> int:
    _require(type(value) is int and value >= 0, "invalid preparation " + name)
    return value


def _same(left: Any, right: Any) -> bool:
    return capture_canonical_bytes(left) == capture_canonical_bytes(right)


def _stack(value: Any) -> dict[str, Any]:
    row = _object(value, _STACK, "stack")
    _require(type(row["registry_name"]) is str and bool(row["registry_name"]), "invalid preparation item name")
    for key in ("metadata", "item_damage", "count"):
        _integer(row[key], "stack " + key)
    _require(row["count"] > 0 and (row["tag"] is None or type(row["tag"]) is dict),
             "invalid preparation nonempty stack")
    return row


def _occurrences(recipes: list[dict[str, Any]], hashes: tuple[str, ...],
                 cancel: Callable[[], None]) -> dict[tuple[str, str], dict[str, Any]]:
    result = {}
    for index, row in enumerate(recipes):
        cancel()
        recipe = row["recipe"]
        for slot, selector in enumerate(recipe["item_inputs"]):
            for position, stack in enumerate(selector["item_stack_representatives"]):
                cancel()
                pointer = f"/records/{index}/recipe/item_inputs/{slot}/item_stack_representatives/{position}"
                result[(hashes[index], pointer)] = stack
        for family in ("item_outputs", "chanced_item_outputs"):
            entries = recipe[family]["entries"] if family.startswith("chanced") else recipe[family]
            for position, entry in enumerate(entries):
                cancel()
                suffix = "/entries" if family.startswith("chanced") else ""
                pointer = f"/records/{index}/recipe/{family}{suffix}/{position}/value"
                result[(hashes[index], pointer)] = entry["value"]
    return result


def validate_preparation(preparation: Any, *, before_hashes: tuple[str, ...],
                         before_field_hashes: tuple[dict[str, str], ...],
                         recipes: list[dict[str, Any]], recipe_hashes: tuple[str, ...],
                         ordinary_records: list[dict[str, Any]], validate_recipe: Callable[[Any], Any],
                         check_cancelled: Callable[[], None]) -> dict[str, Any]:
    """Verify complete visible occurrence coverage and retain distinct states."""
    cancel = check_cancelled
    value = _object(preparation, {"policy", "state", "recipes_before", "recipe_bindings", "objects", "counts"}, "body")
    _require(value["policy"] == POLICY and value["state"] == "complete", "unsupported preparation policy/state")
    counts = _object(value["counts"], _COUNTS, "counts")
    for key in counts:
        _integer(counts[key], key)
    before = _array(value["recipes_before"], "original recipes")
    _require(len(before) == len(before_hashes) == len(before_field_hashes) == len(recipes) == len(recipe_hashes),
             "preparation recipe inventories differ")
    before_by_hash = {}
    for index, row in enumerate(before):
        cancel()
        _object(row, _ROW, "original recipe")
        _require(row["record_type"] == "gt-recipe" and row["semantic_sha256"] == before_field_hashes[index]["recipe"],
                 "preparation original recipe semantic digest differs")
        _integer(row["duplicate_ordinal"], "original recipe duplicate ordinal")
        _require(type(row["lookup_active"]) is bool and type(row["category_present"]) is bool
                 and (row["lookup_active"] or row["category_present"]), "invalid original recipe membership")
        validate_recipe(row["recipe"])
        _require(before_hashes[index] not in before_by_hash, "duplicate preparation original recipe")
        before_by_hash[before_hashes[index]] = row
    after_by_hash = dict(zip(recipe_hashes, recipes))
    _require(len(after_by_hash) == len(recipes), "duplicate prepared recipe")
    bindings = _array(value["recipe_bindings"], "recipe bindings")
    pairs, destinations = {}, set()
    for binding in bindings:
        cancel()
        _object(binding, {"recipe_map", "before_record_sha256", "after_record_sha256"}, "recipe binding")
        old, new = binding["before_record_sha256"], binding["after_record_sha256"]
        _require(type(old) is str and type(new) is str and old in before_by_hash and new in after_by_hash
                 and old not in pairs and new not in destinations, "preparation recipe binding is not a bijection")
        _require(binding["recipe_map"] == before_by_hash[old]["recipe_map"] == after_by_hash[new]["recipe_map"],
                 "preparation recipe map differs")
        pairs[old] = new
        destinations.add(new)
    _require(len(pairs) == len(before), "preparation recipe binding omitted an occurrence")
    expected = {"before": _occurrences(before, before_hashes, cancel),
                "after": _occurrences(recipes, recipe_hashes, cancel)}
    seen = {"before": set(), "after": set()}
    target_seen = {"before": set(), "after": set()}
    after_targets: dict[tuple[str, int], list[tuple[tuple[int, ...], dict[str, Any], dict[str, Any]]]] = {}
    actual = Counter(recipe_count=len(before))
    objects = _array(value["objects"], "objects")
    for ordinal, obj in enumerate(objects):
        cancel()
        _object(obj, _OBJECT, "object")
        _require(_integer(obj["object_ordinal"], "object ordinal") == ordinal, "preparation object order differs")
        for field in ("stack_before", "stack_before_initializer", "stack_after_initializer", "stack_after"):
            _stack(obj[field])
        for field in ("initialization_state_before", "initialization_state_before_initializer",
                      "initialization_state_after_initializer", "initialization_state_after"):
            _require(type(obj[field]) is str and obj[field] in {"deferred", "initialized", "unknown"},
                     "invalid preparation initialization state")
        invoked = obj["initializer_invoked"]
        _require(type(invoked) is bool and invoked == (obj["initialization_state_before_initializer"] == "deferred"),
                 "preparation initializer invocation differs from local state")
        if invoked:
            _require(obj["initialization_state_after_initializer"] == "initialized", "preparation initializer did not finish initialized")
        else:
            _require(obj["initialization_state_before_initializer"] == obj["initialization_state_after_initializer"]
                     and _same(obj["stack_before_initializer"], obj["stack_after_initializer"]),
                     "preparation non-invoked local state changed")
        _require(obj["initialization_state_after"] != "deferred", "preparation left a deferred object")
        refs = _array(obj["references"], "object references")
        _require(bool(refs), "unreferenced preparation object")
        actual.update(object_count=1, initializer_invocation_count=int(invoked),
                      changed_object_count=int(not _same(obj["stack_before"], obj["stack_after"])),
                      unknown_object_count=int(obj["initialization_state_after"] == "unknown"))
        for ref in refs:
            cancel()
            _object(ref, {"role", "before", "after"}, "reference")
            role = ref["role"]
            _require(type(role) is str and role in {"recipe-item-occurrence", "ordinary-stored-target"}, "unknown preparation reference role")
            keys = {}
            for side in ("before", "after"):
                locator = _object(ref[side], {"recipe_record_sha256", "pointer"} if role == "recipe-item-occurrence" else _TARGET, "reference locator")
                recipe_hash = locator["recipe_record_sha256"]
                rows = before_by_hash if side == "before" else after_by_hash
                _require(type(recipe_hash) is str and recipe_hash in rows, "preparation reference recipe missing")
                if role == "recipe-item-occurrence":
                    _require(type(locator["pointer"]) is str, "invalid preparation occurrence pointer")
                    key = (recipe_hash, locator["pointer"])
                    _require(key in expected[side] and key not in seen[side], "preparation occurrence reference missing or duplicated")
                    _require(_same(expected[side][key], obj["stack_" + side]), "preparation object differs from raw occurrence")
                    seen[side].add(key)
                    # Canonical sorting can move recipes and representatives
                    # within one selector; it cannot move an original output or
                    # target into another slot. Both exact pointers are checked
                    # independently against the complete raw inventories above.
                    slot = locator["pointer"].split("/", 3)[3]
                    keys[side] = slot.rsplit("/", 1)[0] if "/item_stack_representatives/" in slot else slot
                else:
                    indices = tuple(_integer(locator[key], "target " + key) for key in (
                        "selector_ordinal", "item_entry_ordinal", "metadata_entry_ordinal", "tag_entry_ordinal"))
                    selectors = rows[recipe_hash]["recipe"]["item_inputs"]
                    _require(indices[0] < len(selectors) and selectors[indices[0]]["runtime_class"] ==
                             "gregtech.api.recipes.ingredients.GTRecipeItemInput", "preparation target selector is not ordinary")
                    _require(type(locator["registry_name"]) is str and bool(locator["registry_name"])
                             and (locator["tag"] is None or type(locator["tag"]) is dict), "invalid stored target identity")
                    _integer(locator["metadata"], "stored metadata")
                    key = (recipe_hash, *indices)
                    _require(key not in target_seen[side], "duplicate preparation stored target")
                    target_seen[side].add(key)
                    keys[side] = indices
                    if side == "after":
                        after_targets.setdefault((recipe_hash, indices[0]), []).append((indices[1:], locator, obj))
            _require(pairs[ref["before"]["recipe_record_sha256"]] == ref["after"]["recipe_record_sha256"]
                     and keys["before"] == keys["after"], "preparation reference changed its original slot")
            actual.update(reference_count=1)
            actual.update({"occurrence_reference_count" if role == "recipe-item-occurrence" else "stored_target_reference_count": 1})
    _require(all(seen[side] == set(expected[side]) for side in seen), "preparation omitted a raw item occurrence")
    _require(all(counts[key] == actual[key] for key in _COUNTS), "preparation counts differ from retained inventories")
    # Matching witnesses must use the materialized targets retained here, rather
    # than quietly substituting an unrelated target list in the later sample.
    for witness in ordinary_records:
        cancel()
        if witness.get("record_type") != "gt-ordinary-item-matching-selector":
            continue
        key = (witness["recipe_record_sha256"], witness["selector_ordinal"])
        targets = sorted(after_targets.get(key, []), key=lambda entry: entry[0])
        _require(len(targets) == len(witness["targets"]), "prepared target inventory differs from ordinary witness")
        for (_, locator, obj), target in zip(targets, witness["targets"]):
            cancel()
            _require(all(_same(locator[field], target[field]) for field in ("registry_name", "metadata", "tag"))
                     and _same(obj["stack_after"], target["stack_before_initialization"])
                     and obj["initialization_state_after"] == target["capability_initialization_state"],
                     "ordinary witness differs from prepared target")
    return {"policy": POLICY, "state": "complete", "counts": dict(counts),
            "recipe_observation_state": "after-capability-preparation",
            "before_recipe_record_hashes_retained": True,
            "identity_equivalence_claimed": False}


def admit_capability_preparation(capture: Any, *, validate_recipe: Callable[[Any], Any],
                                 check_cancelled: Callable[[], None]) -> dict[str, Any] | None:
    """Read only a declared, sealed auxiliary payload through Crucible's API."""
    declared = capture.input_manifest.get("observation_preparation")
    present = any(row["file"] == PAYLOAD_FILE for row in capture.manifest["payloads"])
    if "observation_preparation" not in capture.input_manifest and not present:
        if any(row["file"] == "checkpoint.json" for row in capture.manifest["payloads"]):
            checkpoint = read_capture_payload(capture, "checkpoint.json").value
            _require("observation_preparation_policy" not in checkpoint,
                     "checkpoint declares preparation without its input declaration and payload")
        return None
    _require(_same(declared, DECLARATION) and present, "preparation declaration and retained payload differ")
    check_cancelled()
    payload = read_capture_payload(capture, PAYLOAD_FILE,
        record_arrays={"recipes_before": "/preparation/recipes_before"},
        record_digest_fields={"recipes_before": ("recipe",)})
    value = _object(payload.value, {"format", "schema_version", "preparation"} | _BINDINGS, "envelope")
    _require(value["format"] == "workbench-forge-item-capability-preparation-v1"
             and type(value["schema_version"]) is int and value["schema_version"] == 1,
             "unsupported preparation envelope")
    _require(all(value[key] == capture.manifest[key] for key in _BINDINGS), "preparation capture binding differs")
    checkpoint = read_capture_payload(capture, "checkpoint.json").value
    _require(checkpoint.get("observation_preparation_policy") == POLICY
             and checkpoint.get("checkpoint_id") == "post-start-end-tick"
             and checkpoint.get("physical_side") == "dedicated_server"
             and checkpoint.get("server_started") is True
             and checkpoint.get("actual_event") == "ServerTickEvent.END", "prepared observation checkpoint differs")
    summary = validate_preparation(value["preparation"], before_hashes=payload.record_sha256["recipes_before"],
        before_field_hashes=payload.record_field_sha256["recipes_before"],
        recipes=capture.categories["gt-recipes"]["records"], recipe_hashes=capture.record_sha256["gt-recipes"],
        ordinary_records=capture.categories.get("gt-ordinary-item-matching", {}).get("records", []),
        validate_recipe=validate_recipe, check_cancelled=check_cancelled)
    summary.update(payload_file=PAYLOAD_FILE, payload_sha256=payload.file_sha256,
                   before_records_pointer="/preparation/recipes_before", transitions_pointer="/preparation/objects")
    check_cancelled()
    return summary
