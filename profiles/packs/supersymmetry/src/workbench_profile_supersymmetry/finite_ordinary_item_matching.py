"""Qualify the observed null-tag, zero-serializable-writer ordinary input slice.

Original internal targets and every raw candidate occurrence are required. This
does not infer matcher state from display representatives or compare arbitrary
capability serialization, tagged inputs, or runtime subclasses.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable, Iterator

from .finite_item_matching import ARTIFACT_SHA256, DOMAIN_SCOPE, ItemDomain


ORDINARY_ADAPTER = "gt-ordinary-item-matching"
ORDINARY_CATEGORY = "transformation-ordinary-item-matching"
ORDINARY_MODEL = "gt-2.8.10-forge-1.12.2-null-tag-zero-writer-item-matching-v2"
NATIVE_COPY_INITIALIZATION_POLICY = "initialize-source-potential-matches-otherwise-preserve-deferred-v1"
ORDINARY_ARTIFACT_SHA256 = {
    **ARTIFACT_SHA256,
    "loliasm_sha256": "110741f7ddbff454dea6d55ccefa56096f25973a2ef875e2e3e207566682e45a",
}
ORDINARY_CLASS_SHA256 = {
    "GTRecipeItemInput": "67f7d28d5fe9b0d8028482b0afb59c5d691da109bf9eceafb8af8c4c6b0e6c75",
    "GTRecipeInput": "3d51be9938860f213a240a58e9411eac6374d5c1b81c6a19120878d27ca7e155",
    "CapabilityDispatcher": "43b3b448d8325c87446e7408235757aa04a80bcb7d35eb35aed403fdd65174d7",
    "LoliItemStackMixin": "6084f9e59f4905324bb4a470632c8e9ac4e46d19a88f55ad789a163b3e4730fd",
    "IItemStackCapabilityDelayer": "b664f358750badcd595d8ce4f8dbfa368fd9b5fefad8358cd9c2a561ffe01275",
}
_HEADER = {"record_type", "model", "domain_scope", "domain_sha256", "domain_count",
           "occurrence_count", "occurrences_sha256", "artifacts", "class_sha256",
           "native_copy_initialization_policy"}
_CANDIDATE = {"record_type", "domain_ordinal", "occurrence_count", "occurrences_sha256", "occurrences"}
_OCCURRENCE = {"recipe_record_sha256", "pointer", "copy_stack",
               "original_initialized_stack", "copy_before_initialization_stack",
               "original_capability_initialization_state", "copy_capability_initialization_state",
               "original_capability_writer_count", "copy_capability_writer_count"}
_SELECTOR = {"record_type", "recipe_map", "semantic_sha256", "duplicate_ordinal",
             "recipe_record_sha256", "selector_ordinal", "targets",
             "accepted_domain_ordinals", "accepted_occurrence_ordinals"}
_TARGET = {"registry_name", "metadata", "tag", "capability_writer_count",
           "stack_before_initialization", "stack_after_initialization", "capability_initialization_state"}
_STACK = {"registry_name", "metadata", "item_damage", "tag", "count"}


class FiniteOrdinaryItemMatchingError(ValueError):
    """Ordinary matching evidence does not support this finite qualification."""


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _object(value: Any, fields: set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise FiniteOrdinaryItemMatchingError(f"invalid ordinary matching {name} fields")
    return value


def _int(value: Any, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise FiniteOrdinaryItemMatchingError(f"invalid ordinary matching {name}")
    return value


def _array(value: Any, name: str) -> list[Any]:
    if type(value) is not list:
        raise FiniteOrdinaryItemMatchingError(f"invalid ordinary matching {name}")
    return value


def _text(value: Any, name: str) -> str:
    if type(value) is not str or not value:
        raise FiniteOrdinaryItemMatchingError(f"invalid ordinary matching {name}")
    return value


def _identity(stack: dict[str, Any]) -> str:
    return _json({key: value for key, value in stack.items() if key != "count"})


def _initialization(value: Any) -> str:
    if type(value) is not str or value not in {"initialized", "unknown"}:
        raise FiniteOrdinaryItemMatchingError("invalid ordinary capability initialization state")
    return value


def _writers(value: Any, state: str, name: str) -> int | None:
    if state == "unknown":
        if value is not None:
            raise FiniteOrdinaryItemMatchingError("unknown capability initialization cannot claim a writer count")
        return None
    return _int(value, name)


def _stack(value: Any) -> dict[str, Any]:
    value = _object(value, _STACK, "copied stack")
    _text(value["registry_name"], "copied item name")
    if value["registry_name"] == "minecraft:air":
        raise FiniteOrdinaryItemMatchingError("empty copied ordinary matching stack")
    for key in ("metadata", "item_damage", "count"):
        _int(value[key], "copied " + key, 1 if key == "count" else 0)
    if value["tag"] is not None and type(value["tag"]) is not dict:
        raise FiniteOrdinaryItemMatchingError("invalid copied ordinary matching tag")
    return value


def _raw_occurrences(recipes: list[dict[str, Any]], hashes: tuple[str, ...],
                     cancel: Callable[[], None]) -> Iterator[tuple[dict[str, str], dict[str, Any]]]:
    if len(recipes) != len(hashes):
        raise FiniteOrdinaryItemMatchingError("ordinary recipe hashes differ from records")
    for index, row in enumerate(recipes):
        cancel()
        recipe = row["recipe"]
        prefix = f"/records/{index}/recipe/"
        for slot, selector in enumerate(recipe["item_inputs"]):
            for position, stack in enumerate(selector["item_stack_representatives"]):
                cancel()
                yield {"recipe_record_sha256": hashes[index], "pointer": prefix +
                       f"item_inputs/{slot}/item_stack_representatives/{position}"}, stack
        for family in ("item_outputs", "chanced_item_outputs"):
            entries = recipe[family]["entries"] if family.startswith("chanced") else recipe[family]
            for position, entry in enumerate(entries):
                cancel()
                yield {"recipe_record_sha256": hashes[index], "pointer": prefix + family +
                       ("/entries" if family.startswith("chanced") else "") + f"/{position}/value"}, entry["value"]


def _indices(value: Any, count: int, name: str) -> list[int]:
    value = _array(value, name)
    if (any(type(i) is not int or i < 0 or i >= count for i in value)
            or value != sorted(set(value))):
        raise FiniteOrdinaryItemMatchingError(f"invalid ordinary matching {name}")
    return value


@dataclass(frozen=True)
class OrdinarySelector:
    witness_ordinal: int
    accepted_domain_ordinals: tuple[int, ...]


@dataclass(frozen=True)
class FiniteOrdinaryItemMatching:
    domain: ItemDomain
    selectors: dict[tuple[int, int], OrdinarySelector]
    occurrence_count: int
    occurrences_sha256: str
    copy_identities_unchanged: bool
    copy_counts_unchanged: bool
    capability_initialization_complete: bool
    initialization_identities_unchanged: bool
    initialization_counts_unchanged: bool


def qualify_ordinary_item_matching(records: list[dict[str, Any]], recipes: list[dict[str, Any]],
                                   recipe_hashes: tuple[str, ...], *,
                                   check_cancelled: Callable[[], None]) -> FiniteOrdinaryItemMatching:
    """Recompute coverage and compare native outcomes with the original predicate."""
    cancel = check_cancelled
    occurrences = list(_raw_occurrences(recipes, recipe_hashes, cancel))
    locators = [locator for locator, _ in occurrences]
    domain_stacks, domain_locators, groups, domains_by_identity = [], [], [], {}
    occurrence_domains = []
    for ordinal, (locator, stack) in enumerate(occurrences):
        cancel()
        identity = _identity(stack)
        domain_ordinal = domains_by_identity.get(identity)
        if domain_ordinal is None:
            domain_ordinal = len(domain_stacks)
            domains_by_identity[identity] = domain_ordinal
            domain_stacks.append(stack)
            domain_locators.append(locator)
            groups.append([])
        groups[domain_ordinal].append(ordinal)
        occurrence_domains.append(domain_ordinal)
    domain = ItemDomain(tuple(domain_stacks), tuple(domain_locators), _digest(domain_locators))
    occurrences_digest = _digest(locators)
    headers = [row for row in records if type(row) is dict and row.get("record_type") == "gt-ordinary-item-matching-domain"]
    if len(headers) != 1:
        raise FiniteOrdinaryItemMatchingError("ordinary matching requires one domain header")
    header = _object(headers[0], _HEADER, "domain header")
    if (header["model"] != ORDINARY_MODEL or header["domain_scope"] != DOMAIN_SCOPE
            or header["native_copy_initialization_policy"] != NATIVE_COPY_INITIALIZATION_POLICY
            or header["domain_sha256"] != domain.sha256
            or _int(header["domain_count"], "domain count") != len(domain_stacks)
            or _int(header["occurrence_count"], "occurrence count") != len(occurrences)
            or header["occurrences_sha256"] != occurrences_digest
            or header["artifacts"] != ORDINARY_ARTIFACT_SHA256 or header["class_sha256"] != ORDINARY_CLASS_SHA256):
        raise FiniteOrdinaryItemMatchingError("ordinary matching domain or original byte binding differs")
    candidates, witnesses = {}, []
    for ordinal, row in enumerate(records):
        cancel()
        if row is header:
            continue
        if type(row) is not dict:
            raise FiniteOrdinaryItemMatchingError("invalid ordinary matching record")
        if row.get("record_type") == "gt-ordinary-item-matching-candidate":
            _object(row, _CANDIDATE, "candidate")
            index = _int(row["domain_ordinal"], "domain ordinal")
            if index >= len(groups) or index in candidates:
                raise FiniteOrdinaryItemMatchingError("ordinary candidate is absent or duplicated")
            candidates[index] = row
        elif row.get("record_type") == "gt-ordinary-item-matching-selector":
            witnesses.append((ordinal, _object(row, _SELECTOR, "selector")))
        else:
            raise FiniteOrdinaryItemMatchingError("unknown ordinary matching record type")
    if len(candidates) != len(groups):
        raise FiniteOrdinaryItemMatchingError("ordinary matching candidate coverage is incomplete")
    copy_states = [None] * len(occurrences)
    null_tag_candidates = {}
    unchanged = True
    counts_unchanged = True
    initialized = True
    initialization_identities_unchanged = True
    initialization_counts_unchanged = True
    for domain_ordinal, raw_indices in enumerate(groups):
        cancel()
        row = candidates[domain_ordinal]
        expected_locators = [locators[index] for index in raw_indices]
        observed = _array(row["occurrences"], "candidate occurrences")
        if (_int(row["occurrence_count"], "candidate occurrence count") != len(raw_indices)
                or row["occurrences_sha256"] != _digest(expected_locators)
                or len(observed) != len(raw_indices)):
            raise FiniteOrdinaryItemMatchingError("ordinary candidate occurrence coverage differs")
        for index, witness in zip(raw_indices, observed):
            cancel()
            _object(witness, _OCCURRENCE, "candidate occurrence")
            if {key: witness[key] for key in ("recipe_record_sha256", "pointer")} != locators[index]:
                raise FiniteOrdinaryItemMatchingError("ordinary candidate occurrence locator differs")
            copied = _stack(witness["copy_stack"])
            before_copy = _stack(witness["copy_before_initialization_stack"])
            after_original = _stack(witness["original_initialized_stack"])
            original_state = _initialization(witness["original_capability_initialization_state"])
            copy_state = _initialization(witness["copy_capability_initialization_state"])
            original_writers = _writers(witness["original_capability_writer_count"], original_state, "original writer count")
            copy_writers = _writers(witness["copy_capability_writer_count"], copy_state, "copy writer count")
            original = occurrences[index][1]
            original_identity = _identity(original)
            after_original_identity = _identity(after_original)
            before_copy_identity = _identity(before_copy)
            copied_identity = _identity(copied)
            initialized = initialized and original_state == copy_state == "initialized"
            initialization_identities_unchanged = (initialization_identities_unchanged
                and original_identity == after_original_identity
                and before_copy_identity == copied_identity)
            initialization_counts_unchanged = (initialization_counts_unchanged
                and original["count"] == after_original["count"]
                and before_copy["count"] == copied["count"])
            unchanged = unchanged and before_copy_identity == copied_identity == original_identity
            counts_unchanged = counts_unchanged and before_copy["count"] == copied["count"] == original["count"]
            copy_states[index] = (copied, original_writers, copy_writers)
            if copied["tag"] is None:
                null_tag_candidates.setdefault((copied["registry_name"], copied["metadata"]), []).append(index)
    by_hash = {value: index for index, value in enumerate(recipe_hashes)}
    if len(by_hash) != len(recipe_hashes):
        raise FiniteOrdinaryItemMatchingError("ordinary recipe record identity is duplicated")
    qualified = {}
    for ordinal, witness in witnesses:
        cancel()
        if not unchanged:
            raise FiniteOrdinaryItemMatchingError("ordinary witness cannot qualify changed candidate copy identities")
        if not counts_unchanged:
            raise FiniteOrdinaryItemMatchingError("ordinary witness cannot qualify changed candidate copy counts")
        if not initialized:
            raise FiniteOrdinaryItemMatchingError("ordinary witness requires initialized original and copied capabilities")
        if not initialization_identities_unchanged:
            raise FiniteOrdinaryItemMatchingError("ordinary witness cannot qualify changed initialization identities")
        if not initialization_counts_unchanged:
            raise FiniteOrdinaryItemMatchingError("ordinary witness cannot qualify changed initialization counts")
        record_hash = _text(witness["recipe_record_sha256"], "recipe hash")
        if record_hash not in by_hash:
            raise FiniteOrdinaryItemMatchingError("ordinary witness refers to an absent recipe")
        index = by_hash[record_hash]
        recipe = recipes[index]
        if any(witness[key] != recipe[key] for key in ("recipe_map", "semantic_sha256", "duplicate_ordinal")):
            raise FiniteOrdinaryItemMatchingError("ordinary selector recipe binding differs")
        _int(witness["duplicate_ordinal"], "duplicate ordinal")
        slot = _int(witness["selector_ordinal"], "selector ordinal")
        if slot >= len(recipe["recipe"]["item_inputs"]) or (index, slot) in qualified:
            raise FiniteOrdinaryItemMatchingError("ordinary selector is absent or duplicated")
        selector = recipe["recipe"]["item_inputs"][slot]
        if (selector["runtime_class"] != "gregtech.api.recipes.ingredients.GTRecipeItemInput"
                or selector["ore_dictionary"] or selector["has_nbt_matching_condition"]
                or selector["nbt_matcher"] is not None or selector["nbt_condition"] is not None):
            raise FiniteOrdinaryItemMatchingError("ordinary witness has an unqualified input class or custom NBT")
        targets = _array(witness["targets"], "ordered original targets")
        target_keys = set()
        for target in targets:
            cancel()
            _object(target, _TARGET, "original target")
            if _initialization(target["capability_initialization_state"]) != "initialized":
                raise FiniteOrdinaryItemMatchingError("ordinary target requires initialized capabilities")
            before = _stack(target["stack_before_initialization"])
            after = _stack(target["stack_after_initialization"])
            if _identity(before) != _identity(after) or before["count"] != after["count"]:
                raise FiniteOrdinaryItemMatchingError("ordinary target changed during capability initialization")
            key = (_text(target["registry_name"], "target item name"), _int(target["metadata"], "stored metadata"))
            if target["tag"] is not None or _int(target["capability_writer_count"], "target writer count") != 0:
                raise FiniteOrdinaryItemMatchingError("ordinary target is outside null-tag zero-writer qualification")
            target_keys.add(key)
        predicted_occurrences = []
        for key in target_keys:
            for occurrence_ordinal in null_tag_candidates.get(key, ()):
                cancel()
                _, original_writers, copy_writers = copy_states[occurrence_ordinal]
                if original_writers != 0 or copy_writers != 0:
                    raise FiniteOrdinaryItemMatchingError("ordinary candidate has unqualified serializable capabilities")
                predicted_occurrences.append(occurrence_ordinal)
        predicted_occurrences.sort()
        actual_occurrences = _indices(witness["accepted_occurrence_ordinals"], len(occurrences), "accepted occurrence ordinals")
        if actual_occurrences != predicted_occurrences:
            raise FiniteOrdinaryItemMatchingError("native ordinary occurrence acceptance differs from the source predicate")
        accepted_set = set(actual_occurrences)
        predicted_domain = sorted({occurrence_domains[index] for index in actual_occurrences})
        for domain_ordinal in predicted_domain:
            cancel()
            if any(index not in accepted_set for index in groups[domain_ordinal]):
                raise FiniteOrdinaryItemMatchingError("native ordinary outcomes differ within a collapsed candidate")
        actual_domain = _indices(witness["accepted_domain_ordinals"], len(groups), "accepted domain ordinals")
        if actual_domain != predicted_domain:
            raise FiniteOrdinaryItemMatchingError("native ordinary domain acceptance differs from occurrence outcomes")
        qualified[index, slot] = OrdinarySelector(ordinal, tuple(actual_domain))
    return FiniteOrdinaryItemMatching(domain, qualified, len(occurrences), occurrences_digest, unchanged, counts_unchanged,
                                      initialized, initialization_identities_unchanged, initialization_counts_unchanged)
