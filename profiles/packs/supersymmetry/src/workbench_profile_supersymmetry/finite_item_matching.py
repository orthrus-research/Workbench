"""Admit executed finite item matching against pinned original matcher semantics.

This module does not execute the game. Accepted alternatives require both a
bound native observation for every candidate and agreement with the original
GT/Forge predicate. It does not turn representative stacks into a global domain.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
import struct
from typing import Any, Callable


MATCHING_ADAPTER = "gt-item-matching"
MATCHING_CATEGORY = "transformation-item-matching"
MATCHING_MODEL = "gt-2.8.10-forge-1.12.2-finite-item-matching-v1"
DOMAIN_SCOPE = "captured-gt-input-output-item-variants"
ARTIFACT_SHA256 = {
    "gregtech_sha256": "54744bb11ea4679df4b55846d8073e9bb2ef8c1c53aae8aee41cda3fc22d927e",
    "forge_sha256": "cd3fbf85d7ca744507fd6a37a41b90122d43e616a4f8962332b1b658655e8a64",
    "minecraft_sha256": "fe1f9274e6dad9191bf6e6e8e36ee6ebc737f373603df0946aafcded0d53167e",
}
CLASS_SHA256 = {
    "GTRecipeOreInput": "a951fcaa6d5acc2c92401df58ee6cc5f66ff4183d8ac90f6b04b7716d5c0cf17",
    "IntCircuitIngredient": "490eccd082dc61e5ccd5f0296701df6ddaa5a916d376172838930c065201bf18",
    "MetaItem$MetaValueItem": "3804fcee35351de0da57077d24fcb3e31485d9d26e9fc45a7aaf7c7875782965",
    "OreDictionary": "41e31fd802528cf90431e60ed18f0b5ba01d3c997f227110e95b2d82a87e735f",
}
_PACKAGE = "gregtech.api.recipes.ingredients."
_HEADER_FIELDS = {"record_type", "model", "domain_scope", "domain_sha256", "domain_count",
                  "artifacts", "class_sha256"}
_WITNESS_FIELDS = {"record_type", "recipe_map", "semantic_sha256", "duplicate_ordinal",
                   "recipe_record_sha256", "selector_ordinal", "matcher",
                   "matching_configurations", "integrated_circuit", "accepted_domain_ordinals"}


class FiniteItemMatchingError(ValueError):
    """Optional matcher observations cannot support the declared finite model."""


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _int(value: Any, name: str, minimum: int, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise FiniteItemMatchingError(f"invalid matching {name}")
    return value


def _object(value: Any, fields: set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise FiniteItemMatchingError(f"invalid matching {name} fields")
    return value


def _floating(value: Any, tag_id: int) -> float:
    _object(value, {"decimal", "raw_bits", "value_kind"}, "floating NBT")
    width, code = (32, "f") if tag_id == 5 else (64, "d")
    if (value["value_kind"] != f"float{width}" or type(value["raw_bits"]) is not str
            or re.fullmatch(r"0|[1-9][0-9]*", value["raw_bits"]) is None
            or len(value["raw_bits"]) > 20 or type(value["decimal"]) is not str):
        raise FiniteItemMatchingError("invalid matching floating NBT encoding")
    bits = int(value["raw_bits"])
    if bits >= 1 << width:
        raise FiniteItemMatchingError("matching floating NBT bits exceed their width")
    raw = bits.to_bytes(width // 8, "big")
    number = struct.unpack(">" + code, raw)[0]
    decimal = value["decimal"]
    if decimal not in {"NaN", "Infinity", "-Infinity"} and re.fullmatch(
            r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]+)?", decimal) is None:
        raise FiniteItemMatchingError("invalid matching floating NBT decimal")
    try:
        decoded = float(decimal)
        agrees = math.isnan(number) and decimal == "NaN" or struct.pack(">" + code, decoded) == raw
    except (ValueError, OverflowError):
        agrees = False
    if not agrees:
        raise FiniteItemMatchingError("matching floating NBT decimal and bits differ")
    return number


def _validate_nbt(root: Any, cancel: Callable[[], None]) -> None:
    if root is None:
        return
    pending = [root]
    while pending:
        cancel()
        tag = pending.pop()
        if type(tag) is not dict:
            raise FiniteItemMatchingError("invalid matching typed NBT object")
        kind = _int(tag.get("tag_id"), "NBT tag ID", 1, 12)
        _object(tag, {"tag_id", "value", "element_type"} if kind == 9 else {"tag_id", "value"}, "typed NBT")
        value = tag["value"]
        if kind in (1, 2, 3, 4):
            width = {1: 8, 2: 16, 3: 32, 4: 64}[kind]
            _int(value, "NBT integer", -(1 << (width - 1)), (1 << (width - 1)) - 1)
        elif kind in (5, 6):
            _floating(value, kind)
        elif kind == 8:
            if type(value) is not str:
                raise FiniteItemMatchingError("invalid matching string NBT")
        elif kind == 10:
            if type(value) is not dict or any(type(key) is not str for key in value):
                raise FiniteItemMatchingError("invalid matching compound NBT")
            pending.extend(value.values())
        else:
            if type(value) is not list:
                raise FiniteItemMatchingError("invalid matching array/list NBT")
            if kind == 9:
                element_type = _int(tag["element_type"], "NBT list element type", 0, 12)
                kinds = set()
                for child in value:
                    cancel()
                    if type(child) is not dict:
                        raise FiniteItemMatchingError("invalid matching NBT list child")
                    kinds.add(_int(child.get("tag_id"), "list NBT tag ID", 1, 12))
                if kinds and kinds != {element_type}:
                    raise FiniteItemMatchingError("matching NBT list element types differ")
                pending.extend(value)
            else:
                width = {7: 8, 11: 32, 12: 64}[kind]
                for number in value:
                    cancel()
                    _int(number, "NBT array element", -(1 << (width - 1)), (1 << (width - 1)) - 1)
    if root["tag_id"] != 10:
        raise FiniteItemMatchingError("item matching NBT root must be a compound")


def _signed32(number: int) -> int:
    return (number + (1 << 31)) % (1 << 32) - (1 << 31)


def circuit_configuration(tag: dict[str, Any] | None) -> int:
    """Minecraft 1.12.2 NBTTagCompound.getInteger, after typed-NBT admission.

The original circuit getter installs Configuration=0 on a tagless input.
Reading that result here is intentionally pure and leaves captured data intact.
"""
    if tag is None:
        return 0
    entry = tag["value"].get("Configuration")
    if entry is None or entry["tag_id"] not in (1, 2, 3, 4, 5, 6):
        return 0
    if entry["tag_id"] <= 4:
        return _signed32(entry["value"])
    value = _floating(entry["value"], entry["tag_id"])
    # Java (float|double)->int saturates and maps NaN to zero. MathHelper
    # then subtracts one when the original is less than that converted int;
    # the subtraction itself has signed 32-bit overflow semantics.
    if math.isnan(value):
        integer = 0
    elif value >= (1 << 31) - 1:
        integer = (1 << 31) - 1
    elif value <= -(1 << 31):
        integer = -(1 << 31)
    else:
        integer = int(value)
    compared = struct.unpack(">f", struct.pack(">f", integer))[0] if entry["tag_id"] == 5 else integer
    return _signed32(integer - 1) if value < compared else integer


@dataclass(frozen=True)
class ItemDomain:
    stacks: tuple[dict[str, Any], ...]
    locators: tuple[dict[str, str], ...]
    sha256: str


def build_item_domain(recipes: list[dict[str, Any]], recipe_hashes: tuple[str, ...], *,
                      check_cancelled: Callable[[], None]) -> ItemDomain:
    """Enumerate exact variants in first-occurrence order, excluding quantity."""
    if len(recipes) != len(recipe_hashes):
        raise FiniteItemMatchingError("matching recipe hashes differ from records")
    seen, stacks, locators = set(), [], []
    for index, row in enumerate(recipes):
        check_cancelled()
        recipe = row["recipe"]
        candidates = []
        for selector in recipe["item_inputs"]:
            for position, stack in enumerate(selector["item_stack_representatives"]):
                candidates.append((f"item_inputs/{selector['ordinal']}/item_stack_representatives/{position}", stack))
        for field in ("item_outputs", "chanced_item_outputs"):
            entries = recipe[field]["entries"] if field.startswith("chanced") else recipe[field]
            for position, entry in enumerate(entries):
                candidates.append((f"{field}/" + ("entries/" if field.startswith("chanced") else "") + f"{position}/value", entry["value"]))
        for pointer, stack in candidates:
            check_cancelled()
            if stack["registry_name"] == "minecraft:air":
                raise FiniteItemMatchingError("empty air stack cannot enter the finite matching domain")
            identity = _json({key: value for key, value in stack.items() if key != "count"})
            if identity in seen:
                continue
            seen.add(identity)
            _validate_nbt(stack["tag"], check_cancelled)
            stacks.append(stack)
            locators.append({"recipe_record_sha256": recipe_hashes[index], "pointer": f"/records/{index}/recipe/{pointer}"})
    return ItemDomain(tuple(stacks), tuple(locators), hashlib.sha256(_json(locators).encode()).hexdigest())


@dataclass(frozen=True)
class QualifiedSelector:
    witness_ordinal: int
    accepted_domain_ordinals: tuple[int, ...]
    matcher: str


@dataclass(frozen=True)
class FiniteItemMatching:
    domain: ItemDomain
    selectors: dict[tuple[int, int], QualifiedSelector]


def qualify_item_matching(records: list[dict[str, Any]], recipes: list[dict[str, Any]],
                          recipe_hashes: tuple[str, ...], *,
                          check_cancelled: Callable[[], None]) -> FiniteItemMatching:
    """Validate optional native witnesses and independently compute their closure."""
    domain = build_item_domain(recipes, recipe_hashes, check_cancelled=check_cancelled)
    headers = [row for row in records if type(row) is dict and row.get("record_type") == "gt-item-matching-domain"]
    if len(headers) != 1:
        raise FiniteItemMatchingError("matching requires exactly one finite domain header")
    header = _object(headers[0], _HEADER_FIELDS, "domain header")
    if (header["model"] != MATCHING_MODEL or header["domain_scope"] != DOMAIN_SCOPE
            or header["domain_sha256"] != domain.sha256
            or type(header["domain_count"]) is not int or header["domain_count"] != len(domain.stacks)
            or header["artifacts"] != ARTIFACT_SHA256 or header["class_sha256"] != CLASS_SHA256):
        raise FiniteItemMatchingError("matching domain, model or original byte binding differs")
    by_hash = {value: index for index, value in enumerate(recipe_hashes)}
    if len(by_hash) != len(recipe_hashes):
        raise FiniteItemMatchingError("matching recipe record identity is duplicated")
    # Index only the original predicate's necessary identity fields. Exact
    # resource variants and their first-occurrence ordinals remain unchanged.
    by_registry: dict[str, list[int]] = {}
    by_metadata: dict[tuple[str, int], list[int]] = {}
    by_damage: dict[tuple[str, int], list[int]] = {}
    for candidate_ordinal, stack in enumerate(domain.stacks):
        check_cancelled()
        registry = stack["registry_name"]
        by_registry.setdefault(registry, []).append(candidate_ordinal)
        by_metadata.setdefault((registry, stack["metadata"]), []).append(candidate_ordinal)
        by_damage.setdefault((registry, stack["item_damage"]), []).append(candidate_ordinal)
    qualified = {}
    integrated_circuit_identity = None
    for ordinal, witness in enumerate(records):
        check_cancelled()
        if witness is header:
            continue
        _object(witness, _WITNESS_FIELDS, "selector witness")
        if witness["record_type"] != "gt-item-matching-selector":
            raise FiniteItemMatchingError("unknown matching record type")
        if type(witness["recipe_record_sha256"]) is not str or witness["recipe_record_sha256"] not in by_hash:
            raise FiniteItemMatchingError("matching witness refers to an absent recipe")
        index = by_hash[witness["recipe_record_sha256"]]
        recipe = recipes[index]
        if any(witness[key] != recipe[key] for key in ("recipe_map", "semantic_sha256", "duplicate_ordinal")):
            raise FiniteItemMatchingError("matching selector recipe binding differs")
        _int(witness["duplicate_ordinal"], "duplicate ordinal", 0)
        slot = _int(witness["selector_ordinal"], "selector ordinal", 0)
        if slot >= len(recipe["recipe"]["item_inputs"]) or (index, slot) in qualified:
            raise FiniteItemMatchingError("matching selector is absent or duplicated")
        selector = recipe["recipe"]["item_inputs"][slot]
        if (selector["has_nbt_matching_condition"] or selector["nbt_matcher"] is not None
                or selector["nbt_condition"] is not None):
            raise FiniteItemMatchingError("custom NBT matching is not qualified")
        accepted = witness["accepted_domain_ordinals"]
        if (type(accepted) is not list or any(type(value) is not int or value < 0 or value >= len(domain.stacks) for value in accepted)
                or accepted != sorted(set(accepted))):
            raise FiniteItemMatchingError("matching accepted domain ordinals are invalid")
        observed = []
        if witness["matcher"] == "ore-dictionary":
            if (selector["runtime_class"] != _PACKAGE + "GTRecipeOreInput" or not selector["ore_dictionary"]
                    or witness["matching_configurations"] is not None or witness["integrated_circuit"] is not None):
                raise FiniteItemMatchingError("ore matching class or original state differs")
            targets = set()
            for row in selector["item_stack_representatives"]:
                check_cancelled()
                targets.add((row["registry_name"], row["metadata"]))
            matching_ordinals = set()
            for registry, metadata in targets:
                check_cancelled()
                candidates = by_registry.get(registry, ()) if metadata == 32767 else by_metadata.get((registry, metadata), ())
                for candidate_ordinal in candidates:
                    check_cancelled()
                    matching_ordinals.add(candidate_ordinal)
            observed = sorted(matching_ordinals)
            check_cancelled()
        elif witness["matcher"] == "integrated-circuit":
            if selector["runtime_class"] != _PACKAGE + "IntCircuitIngredient" or selector["ore_dictionary"]:
                raise FiniteItemMatchingError("circuit matching class or original state differs")
            config = _int(witness["matching_configurations"], "circuit configuration", -(1 << 31), (1 << 31) - 1)
            circuit = _object(witness["integrated_circuit"], {"registry_name", "item_damage"}, "integrated circuit identity")
            if type(circuit["registry_name"]) is not str or not circuit["registry_name"]:
                raise FiniteItemMatchingError("invalid integrated circuit item name")
            _int(circuit["item_damage"], "integrated circuit damage", 0)
            if integrated_circuit_identity is not None and circuit != integrated_circuit_identity:
                raise FiniteItemMatchingError("integrated circuit identity differs between native witnesses")
            integrated_circuit_identity = circuit
            for candidate_ordinal in by_damage.get((circuit["registry_name"], circuit["item_damage"]), ()):
                check_cancelled()
                if circuit_configuration(domain.stacks[candidate_ordinal]["tag"]) == config:
                    observed.append(candidate_ordinal)
        else:
            raise FiniteItemMatchingError("unqualified matching predicate")
        if observed != accepted:
            raise FiniteItemMatchingError("native matching observation differs from the source-backed finite predicate")
        qualified[index, slot] = QualifiedSelector(ordinal, tuple(accepted), witness["matcher"])
    return FiniteItemMatching(domain, qualified)
