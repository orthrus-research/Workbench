"""Domain records over owner-admitted Axiom observations, without native execution.

The retained report remains the authority. This module gives its collections and
references names; it does not execute matchers, infer registration provenance, or
reinterpret fingerprints as the states they summarize.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any, Callable, Iterator

from workbench_atlas_categorical_graph.bundle import edge_record, node_record


FAMILIES = (
    "report", "lifecycle", "source", "configuration", "mod", "artifact",
    "transformation", "diagnostic", "material-registry", "material-fingerprint",
    "fluid-fingerprint", "generated-form-fingerprint", "material-witness",
    "generated-form-witness", "custom-item", "gt-map", "gt-lookup-recipe",
    "gt-category", "gt-category-outside-lookup", "gt-ingredient", "gt-output",
    "crafting-recipe", "crafting-native-value", "crafting-order", "furnace",
    "ore-dictionary", "ore-reload-record", "callback", "biome", "worldgen-binding",
    "block-hardness", "retained-metadata",
)

LIMITATIONS = (
    "Stored definitions and object references do not establish executed matching, lookup winners, crafting, machine operation, or world behavior.",
    "Final registry state does not identify the operation or source statement that created, replaced, rejected, or removed an entry; generated recipe parentage is not inferred.",
    "Material, fluid and generated-form fingerprint inventories retain membership and selected-state fingerprints; full values exist only where separately witnessed.",
    "Recipe state digests group equal stored values with multiplicity; they are not stable cross-run native object identities.",
    "Native value references are local to their enclosing crafting catalog; graph reference cycles are stored object topology, not recipe dependency or bootstrap viability.",
    "Absent families mean not observed in this selected report, not empty registries or proof that the family is outside the selected context.",
    "Historical retained observations describe their saved source, configuration, side and lifecycle; they do not describe a currently running game.",
)

_SCHEMAS = {
    "registrationEffects": "axiom.native-registration-effects.v1",
    "nativeStoredRecipes": "axiom.native-stored-recipes.v1",
    "nativeStoredCraftingRecipes": "axiom.native-stored-crafting-recipes.v1",
    "nativeStoredFurnaceRecipes": "axiom.native-stored-furnace-recipes.v1",
    "nativeOreMutations": "axiom.native-ore-mutations.v1",
    "nativeConfiguration": "axiom.native-configuration-bindings.v1",
    "nativeBiomes": "axiom.native-biome-registrations.v1",
    "nativeWorldgenBiomeBindings": "axiom.native-worldgen-biome-bindings.v1",
    "nativeBlockHardness": "axiom.native-block-hardness.v1",
    "nativeStoredCraftingCallbacks": "axiom.native-stored-crafting-callbacks.v1",
    "snapshotEncoding": "axiom.native-stage-snapshots.v1",
}
_STAGE_SCHEMAS = frozenset("axiom.native-" + stage + "-stage.v1" for stage in
    ("root", "early", "selection", "construction", "groovy", "preinit", "recipe"))


class InitializationFamilyError(ValueError):
    """A retained relationship cannot be projected without ambiguity."""


def _pointer(base: str, key: Any) -> str:
    return base + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _items(value: Any):
    if type(value) is dict:
        return value.items()
    if type(value) is list:
        return enumerate(value)
    raise InitializationFamilyError("Expected a retained mapping or sequence")


def _label(value: Any, fallback: Any) -> str:
    if type(value) is dict:
        for key in ("name", "registryName", "material", "path", "id", "class", "type"):
            if type(value.get(key)) is str and value[key]:
                return value[key]
    return str(fallback)


def _references(value: Any, pointer: str):
    """Iterative, catalog-local reference walk; preserve each occurrence path."""
    pending = [(value, pointer)]
    while pending:
        current, path = pending.pop()
        if type(current) is dict:
            if "nativeValueRef" in current:
                if set(current) != {"nativeValueRef"} or type(current["nativeValueRef"]) is not str:
                    raise InitializationFamilyError(f"Malformed native value reference at {path}")
                yield current["nativeValueRef"], path
            else:
                pending.extend((v, _pointer(path, k)) for k, v in reversed(tuple(current.items())))
        elif type(current) is list:
            pending.extend((current[i], _pointer(path, i)) for i in range(len(current) - 1, -1, -1))


@dataclass(frozen=True)
class InitializationPlan:
    """Repeatable streams holding references to one immutable admitted report.

    The caller must not mutate the report for the lifetime of this plan. No Axiom,
    profile, game or Pack Program Studio implementation is imported here.
    """

    report: dict[str, Any]
    snapshot_id: str
    side: str
    check_cancelled: Callable[[], None] | None = None
    _node_counts: Counter | None = field(default=None, init=False, repr=False, compare=False)

    @property
    def limitations(self) -> tuple[str, ...]:
        return LIMITATIONS

    @property
    def coverage(self) -> list[dict[str, Any]]:
        counts = self._node_counts or Counter()
        return [{"family": family, "status": ("not-scanned" if self._node_counts is None else
                                               "projected" if counts[family] else "not-observed"),
                 "node_count": counts[family]} for family in sorted(set(FAMILIES) | counts.keys())]

    def iter_nodes(self) -> Iterator[dict[str, Any]]:
        counts = Counter()
        for record_type, row in _Projection(self).records():
            if self.check_cancelled is not None:
                self.check_cancelled()
            if record_type == "node":
                counts[row["properties"]["family"]] += 1
                yield row
        object.__setattr__(self, "_node_counts", counts)

    def iter_edges(self) -> Iterator[dict[str, Any]]:
        for record_type, row in _Projection(self).records():
            if self.check_cancelled is not None:
                self.check_cancelled()
            if record_type == "edge":
                yield row


def build_initialization_plan(report: dict[str, Any], *, snapshot_id: str,
                              side: str = "single",
                              check_cancelled: Callable[[], None] | None = None) -> InitializationPlan:
    """Project a full retained report already admitted by its owning reader.

    ``side`` selects the exact native branch, never a merge of candidate and
    baseline state. This structural adapter does not replace owner admission.
    """
    if type(report) is not dict or type(snapshot_id) is not str or not snapshot_id:
        raise InitializationFamilyError("An admitted report and snapshot identity are required")
    if side not in ("single", "baseline", "candidate"):
        raise InitializationFamilyError("Observation side must be single, baseline or candidate")
    native = report.get("native")
    if native is None and side == "single":
        return InitializationPlan(report, snapshot_id, side, check_cancelled)
    if type(native) is not dict:
        raise InitializationFamilyError("The selected report has no native observation")
    if side != "single":
        result = native.get("result")
        if type(result) is not dict or type(result.get(side)) is not dict:
            raise InitializationFamilyError(f"The selected report has no {side} native observation")
    return InitializationPlan(report, snapshot_id, side, check_cancelled)


class _Projection:
    def __init__(self, plan: InitializationPlan):
        self.plan = plan

    def identity(self, kind: str, pointer: str) -> str:
        key = json.dumps([self.plan.snapshot_id, self.plan.side, pointer, kind],
                         ensure_ascii=False, separators=(",", ":"))
        return sha256(key.encode("utf-8")).hexdigest()

    def node(self, kind: str, family: str, pointer: str, value: Any,
             label: Any = None, *, omit=(), extra=None):
        removed = ({k: {"type": "mapping" if type(v) is dict else "sequence", "count": len(v)}
                    for k, v in value.items() if k in omit and type(v) in (dict, list) and v}
                   if omit and type(value) is dict else {})
        raw = {k: v for k, v in value.items() if k not in removed} if removed else value
        properties = {"family": family, "label": str(label) if label is not None else _label(value, kind),
                      "json_pointer": pointer, "raw_value": raw}
        if removed:
            properties["projected_fields"] = list(removed)
            properties["projected_collections"] = removed
        if extra:
            properties.update(extra)
        return node_record("initialization-" + kind, self.identity(kind, pointer), properties, [self.evidence(pointer)])

    def evidence(self, pointer: str):
        return {"snapshot_id": self.plan.snapshot_id, "section": "report", "record_key": "value",
                "json_pointer": pointer, "observation_kind": "observed-storage", "side": self.plan.side}

    def edge(self, relation: str, source, target, pointer: str, **properties):
        return "edge", edge_record(relation, source["id"], target["id"], properties,
                                   [self.evidence(pointer)], semantic_key=pointer)

    def emit(self, node, parent=None, relation="observes-initialization-record"):
        yield "node", node
        if parent is not None:
            yield self.edge(relation, parent, node, node["properties"]["json_pointer"])

    def rows(self, value, pointer, parent, *, family, kind, relation="has-stored-member"):
        for key, raw in _items(value):
            path = _pointer(pointer, key)
            node = self.node(kind, family, path, raw, _label(raw, key), extra={"record_key": key})
            yield from self.emit(node, parent, relation)

    def collection(self, value, pointer, parent, *, family, kind, fields):
        """Named native collection, with metadata retained on its owner node."""
        if type(value) is not dict:
            yield from self.emit(self.node(kind, family, pointer, value), parent)
            return
        node = self.node(kind, family, pointer, value, omit=fields)
        yield from self.emit(node, parent)
        for field, (child_kind, child_family, relation) in fields.items():
            if field in value:
                yield from self.rows(value[field], _pointer(pointer, field), node,
                                     kind=child_kind, family=child_family, relation=relation)

    def records(self):
        report = self.plan.report
        root = self.node("report", "report", "", report, "Axiom retained report", omit=("native", "findings"))
        yield from self.emit(root)
        if "findings" in report:
            yield from self.rows(report["findings"], "/findings", root, kind="finding", family="diagnostic")
        if report.get("native") is None:
            return
        native, path = report["native"], "/native"
        if self.plan.side != "single":
            # Retain pairing metadata, while only projecting the selected branch.
            wrapper = self.node("paired-native-report", "report", path, native, omit=("result",))
            yield from self.emit(wrapper, root)
            pair = self.node("pairing-metadata", "report", path + "/result", native["result"],
                             omit=("baseline", "candidate"))
            yield from self.emit(pair, wrapper)
            native = native["result"][self.plan.side]
            path += "/result/" + self.plan.side
            parent = pair
        else:
            parent = root
        owner = self.node("native-report", "report", path, native, omit=("result",))
        yield from self.emit(owner, parent)
        if type(native.get("result")) is dict:
            yield from self.result(native["result"], path + "/result", owner)

    def result(self, value, path, parent):
        node = self.node("native-result", "report", path, value, omit=tuple(value))
        yield from self.emit(node, parent)
        families = {"context": "configuration", "runtime": "artifact", "sourceAdmission": "source",
                    "sourceProgram": "source", "sourceScope": "source", "workerStages": "lifecycle",
                    "initialization": "lifecycle", "bootstrap": "lifecycle"}
        for key, raw in value.items():
            pointer = _pointer(path, key)
            if key == "execution" and type(raw) is dict:
                yield from self.stage(raw, pointer, node, kind="execution")
            else:
                yield from self.emit(self.node("result-field", families.get(key, "retained-metadata"),
                                               pointer, raw, key), node)

    def stage(self, value, path, parent, *, kind="native-stage", inherited_fields=None):
        # Every field is retained at its actual pointer, including compact
        # snapshots. Inherited keys are declarations about shared stored values,
        # never a fabricated second callback invocation.
        if kind == "native-stage" and "schema" in value and value["schema"] not in _STAGE_SCHEMAS:
            raise InitializationFamilyError(f"Unsupported native stage schema at {path}")
        node = self.node(kind, "lifecycle", path, value, omit=tuple(value))
        yield from self.emit(node, parent)
        for key, raw in value.items():
            pointer = _pointer(path, key)
            if key in {"nativeInitialization", "startupStage", "constructionStage", "earlyStage", "selectionStage", "groovyBoundary"} and type(raw) is dict:
                if set(raw) == {"inheritedKeys", "values"}:
                    compact = self.node("compressed-stage", "lifecycle", pointer, raw, key, omit=("values",))
                    yield from self.emit(compact, node, "retains-earlier-stage")
                    keys = raw["inheritedKeys"]
                    if (type(keys) is not list or any(type(k) is not str for k in keys)
                            or len(set(keys)) != len(keys)
                            or type(raw["values"]) is not dict
                            or any(k in raw["values"] or (k not in value and k not in (inherited_fields or {})) for k in keys)):
                        raise InitializationFamilyError(f"Invalid inherited stage keys at {pointer}")
                    yield from self.stage(raw["values"], pointer + "/values", compact)
                    # Each target is the exact enclosing field observation.
                    for inherited in raw["inheritedKeys"]:
                        target = (self.field_node(value, path, inherited) if inherited in value
                                  else inherited_fields[inherited])
                        yield "edge", edge_record("inherits-stored-stage-field", compact["id"],
                            target["id"], {"field": inherited},
                            [self.evidence(pointer + "/inheritedKeys")], semantic_key=inherited)
                else:
                    moved = None
                    if key == "nativeInitialization" and type(raw.get("snapshotEncoding")) is dict:
                        fields = raw["snapshotEncoding"].get("executionFields", [])
                        if (type(fields) is not list or any(type(k) is not str or k not in value for k in fields)
                                or len(set(fields)) != len(fields)):
                            raise InitializationFamilyError(f"Invalid moved execution fields at {pointer}")
                        moved = {k: self.field_node(value, path, k) for k in fields}
                    yield from self.stage(raw, pointer, node, inherited_fields=moved)
            else:
                yield from self.field(raw, pointer, node, key)

    def field_node(self, value, path, key):
        """Identity of the top record emitted for a stage field."""
        kind, family = self.field_type(key, value[key])
        return self.node(kind, family, _pointer(path, key), None)

    @staticmethod
    def field_type(key, raw):
        specialized = {"registrationEffects": ("registration-effects", "material-registry"),
            "customMetaItems": ("custom-items", "custom-item"),
            "nativeStoredRecipes": ("gt-recipes", "gt-map"),
            "nativeStoredCraftingRecipes": ("crafting-registry", "crafting-recipe"),
            "nativeStoredFurnaceRecipes": ("furnace-storage", "furnace"),
            "nativeOreMutations": ("ore-observations", "ore-dictionary"),
            "nativeMaterialRegistries": ("material-registries", "material-registry"),
            "nativeConfiguration": ("configuration-bindings", "configuration"),
            "nativeBiomes": ("biome-registry", "biome"),
            "nativeWorldgenBiomeBindings": ("worldgen-bindings", "worldgen-binding"),
            "nativeBlockHardness": ("block-hardness-catalog", "block-hardness"),
            "nativeStoredCraftingCallbacks": ("crafting-callbacks", "callback")}
        if key in specialized:
            return specialized[key]
        if key in {"nativeInitialization", "startupStage", "constructionStage", "earlyStage", "selectionStage", "groovyBoundary"} and type(raw) is dict:
            return ("compressed-stage" if set(raw) == {"inheritedKeys", "values"} else "native-stage", "lifecycle")
        family = "retained-metadata"
        if key in {"nativeMaterialWitnesses", "materials", "lookups", "missingMaterials", "vocabulary"}:
            family = "material-witness"
        elif key in {"prefixItems", "materialBlocks", "materialOres", "oreBlocks", "selectedObservations"}:
            family = "generated-form-witness"
        elif "Mod" in key or key in {"nativeActiveOwner", "discoveredMods"}:
            family = "mod"
        elif "iagnostic" in key or key in {"findings", "nativeErrors", "nativeGroovyErrors", "groovyDiagnostics", "coverageGaps"}:
            family = "diagnostic"
        elif "cript" in key or key in {"sourceProgram", "candidateDispatchObservations"}:
            family = "source"
        elif "rtifact" in key or key in {"definitions", "selectionDefinitions"}:
            family = "artifact"
        elif any(word in key.lower() for word in ("mixin", "transform", "tweak")):
            family = "transformation"
        elif "allback" in key or key == "nativeRecipeFunctions":
            family = "callback"
        elif key in {"initialization", "snapshotEncoding", "remainingScope", "recipeScopeGaps"}:
            family = "lifecycle"
        return "stage-field", family

    def field(self, raw, path, parent, key):
        expected = _SCHEMAS.get(key)
        if expected is not None and (type(raw) is not dict or raw.get("schema") != expected):
            raise InitializationFamilyError(f"Unsupported {key} observation schema at {path}; expected {expected}")
        kind, family = self.field_type(key, raw)
        if key == "registrationEffects" and type(raw) is dict:
            yield from self.effects(raw, path, parent)
        elif key == "nativeStoredRecipes" and type(raw) is dict:
            yield from self.gt(raw, path, parent)
        elif key == "nativeStoredCraftingRecipes" and type(raw) is dict:
            yield from self.crafting(raw, path, parent)
        elif key == "nativeOreMutations" and type(raw) is dict:
            yield from self.ores(raw, path, parent)
        elif key == "customMetaItems" and type(raw) is dict:
            node = self.node(kind, family, path, raw, omit=("items",))
            yield from self.emit(node, parent)
            for i, item in enumerate(raw.get("items", [])):
                pointer = _pointer(path + "/items", i)
                owner = self.node("custom-item", family, pointer, item, omit=("variants",))
                yield from self.emit(owner, node)
                for ordinal, variant in enumerate(item.get("variants", [])):
                    vp = _pointer(pointer + "/variants", ordinal)
                    child = self.node("custom-item-variant", family, vp, variant)
                    yield from self.emit(child, owner, "has-declared-item-variant")
                    if type(variant.get("components")) is list:
                        yield from self.rows(variant["components"], vp + "/components", child,
                            family=family, kind="custom-item-component", relation="has-stored-item-component")
        elif key in {"nativeMaterialWitnesses", "materials"} and type(raw) is list:
            owner = self.node(kind, family, path, raw, key,
                              extra={"collection_type": "list", "record_count": len(raw)})
            yield from self.emit(owner, parent)
            for ordinal, material in enumerate(raw):
                yield from self.material(material, _pointer(path, ordinal), owner)
        elif key in {"prefixItems", "materialBlocks", "materialOres", "oreBlocks"} and type(raw) is dict:
            owner = self.node(kind, family, path, raw, key, omit=("entries", "witnesses"))
            yield from self.emit(owner, parent)
            for field in ("entries", "witnesses"):
                if field in raw:
                    for ordinal, witness in _items(raw[field]):
                        yield from self.form(witness, _pointer(path + "/" + field, ordinal), owner)
        elif key == "nativeStoredFurnaceRecipes":
            fields = {f: ("furnace-" + f, "furnace", "has-stored-furnace-" + f)
                      for f in ("smelting", "experience", "timeWildcard", "timeMetadata", "fuelConversions")}
            yield from self.collection(raw, path, parent, family=family, kind=kind, fields=fields)
        elif key == "nativeWorldgenBiomeBindings" and type(raw) is dict:
            owner = self.node(kind, family, path, raw, omit=("definitions",))
            yield from self.emit(owner, parent)
            for name, definition in raw.get("definitions", {}).items():
                dp = _pointer(path + "/definitions", name)
                child = self.node("worldgen-definition", family, dp, definition, name)
                yield from self.emit(child, owner, "has-native-worldgen-definition")
                if type(definition.get("biomeWeights")) is dict:
                    yield from self.rows(definition["biomeWeights"], dp + "/biomeWeights", child,
                        family=family, kind="worldgen-biome-weight", relation="has-observed-biome-weight")
        elif key in {"nativeMaterialRegistries", "nativeConfiguration", "nativeBiomes", "nativeBlockHardness", "nativeStoredCraftingCallbacks"}:
            fields = {"nativeMaterialRegistries": {"registries": ("material-registry", family, "has-native-registry")},
                "nativeConfiguration": {"classes": ("configuration-class", family, "has-native-config-binding"),
                                        "declaredClasses": ("configuration-declaration", family, "has-config-declaration")},
                "nativeBiomes": {"biomes": ("biome", family, "has-native-biome"),
                                 "disabledBiomes": ("disabled-biome", family, "has-disabled-biome-name")},
                "nativeWorldgenBiomeBindings": {"definitions": ("worldgen-definition", family, "has-native-worldgen-definition")},
                "nativeBlockHardness": {"entries": ("block-hardness", family, "has-stored-block-hardness")},
                "nativeStoredCraftingCallbacks": {"entries": ("crafting-callback", family, "has-stored-crafting-callback")}}[key]
            yield from self.collection(raw, path, parent, family=family, kind=kind, fields=fields)
        else:
            # Unmodeled fields remain accessible records. This is a deliberately
            # shallow split, not a made-up domain relationship for every scalar.
            if type(raw) in (dict, list) and raw:
                owner = self.node(kind, family, path, raw, key,
                                  extra={"collection_type": type(raw).__name__, "record_count": len(raw)})
                yield from self.emit(owner, parent)
                yield from self.rows(raw, path, owner, family=family,
                                     kind="material-witness" if key == "nativeMaterialWitnesses" else "observation-record")
            else:
                yield from self.emit(self.node(kind, family, path, raw, key), parent)

    def effects(self, value, path, parent):
        owner = self.node("registration-effects", "material-registry", path, value, omit=tuple(k for k, v in value.items() if type(v) is dict))
        yield from self.emit(owner, parent)
        for name, raw in value.items():
            if type(raw) is not dict:
                continue
            pointer = _pointer(path, name)
            family = {"materials": "material-fingerprint", "fluids": "fluid-fingerprint"}.get(name,
                "generated-form-fingerprint" if name in {"prefixItems", "materialBlocks", "oreBlocks"} else "material-registry")
            catalog = self.node("effect-catalog", family, pointer, raw, name, omit=("entries", "witnesses"))
            yield from self.emit(catalog, owner)
            if "entries" in raw:
                yield from self.rows(raw["entries"], pointer + "/entries", catalog,
                    kind="fingerprint-member", family=family, relation="has-fingerprinted-native-member")
            for ordinal, witness in enumerate(raw.get("witnesses", [])):
                yield from self.form(witness, _pointer(pointer + "/witnesses", ordinal), catalog)

    def material(self, value, path, parent):
        material = self.node("material-witness", "material-witness", path, value)
        yield from self.emit(material, parent, "has-selected-material-witness")
        if type(value) is not dict:
            return
        for field, kind, relation in (("components", "material-component", "has-declared-material-component"),
                                      ("flags", "material-flag", "has-observed-material-flag"),
                                      ("properties", "material-property-key", "has-observed-material-property-key")):
            if type(value.get(field)) in (list, dict):
                yield from self.rows(value[field], path + "/" + field, material,
                    family="material-witness", kind=kind, relation=relation)
        state = value.get("nativePropertyState")
        if state is not None and (type(state) is not dict or state.get("schema") != "axiom.native-material-property-state.v1"):
            raise InitializationFamilyError(f"Unsupported material property observation schema at {path}")
        if type(state) is dict and type(state.get("properties")) is dict:
            for key, raw in state["properties"].items():
                pp = _pointer(path + "/nativePropertyState/properties", key)
                prop = self.node("material-property-state", "material-witness", pp, raw, key)
                yield from self.emit(prop, material, "has-attached-material-property")
                values = raw.get("values") if type(raw) is dict else None
                if raw.get("class") == "gregtech.api.unification.material.properties.FluidProperty" and type(values) is dict:
                    for name in ("stored", "queued"):
                        if type(values.get(name)) is list:
                            yield from self.rows(values[name], pp + "/values/" + name, prop,
                                family="material-witness", kind="material-fluid-" + name,
                                relation="has-native-fluid-" + name + "-binding")

    def form(self, value, path, parent):
        witness = self.node("generated-form-witness", "generated-form-witness", path, value)
        yield from self.emit(witness, parent, "has-selected-form-witness")
        if type(value) is not dict:
            return
        if "selected" in value:
            yield from self.emit(self.node("unifier-selection", "generated-form-witness", path + "/selected", value["selected"]),
                                 witness, "has-observed-unifier-selection")
        if type(value.get("generated")) is list:
            yield from self.rows(value["generated"], path + "/generated", witness,
                family="generated-form-witness", kind="generated-form", relation="has-observed-generated-form")

    def gt(self, value, path, parent):
        owner = self.node("gt-recipes", "gt-map", path, value, omit=("maps",))
        yield from self.emit(owner, parent)
        for name, raw in value.get("maps", {}).items():
            pointer = _pointer(path + "/maps", name)
            recipe_map = self.node("gt-map", "gt-map", pointer, raw, name, omit=("lookup", "categories"))
            yield from self.emit(recipe_map, owner, "has-stored-gt-map")
            lookup = raw.get("lookup")
            if type(lookup) is dict:
                yield from self.recipe_catalog(lookup, pointer + "/lookup", recipe_map, active=True)
            for i, category in enumerate(raw.get("categories", [])):
                cp = _pointer(pointer + "/categories", i)
                cat = self.node("gt-category", "gt-category", cp, category, omit=("outsideLookup",))
                yield from self.emit(cat, recipe_map, "has-stored-gt-category")
                for digest, multiplicity in category.get("lookupMembers", {}).items():
                    if type(lookup) is not dict or digest not in lookup.get("entries", {}):
                        raise InitializationFamilyError(f"GT category lookup member is missing at {cp}")
                    target = self.node("gt-stored-recipe", "gt-lookup-recipe", _pointer(pointer + "/lookup/entries", digest), None)
                    yield self.edge("category-references-lookup-recipe", cat, target, _pointer(cp + "/lookupMembers", digest), multiplicity=multiplicity)
                if type(category.get("outsideLookup")) is dict:
                    yield from self.recipe_catalog(category["outsideLookup"], cp + "/outsideLookup", cat, active=False)

    def recipe_catalog(self, value, path, parent, *, active):
        family = "gt-lookup-recipe" if active else "gt-category-outside-lookup"
        catalog = self.node("gt-recipe-catalog", family, path, value, omit=("entries",), extra={"lookup_membership_observed": active})
        yield from self.emit(catalog, parent, "has-lookup-storage" if active else "has-category-storage-outside-lookup")
        for digest, entry in value.get("entries", {}).items():
            pointer = _pointer(path + "/entries", digest)
            node = self.node("gt-stored-recipe", family, pointer, entry, digest,
                             extra={"stored_value_digest": digest, "lookup_membership_observed": active})
            yield from self.emit(node, catalog, "has-stored-recipe-value")
            recipe = entry.get("value")
            if type(recipe) is not dict:
                continue
            for field in ("inputs", "fluidInputs"):
                if type(recipe.get(field)) is list:
                    yield from self.rows(recipe[field], pointer + "/value/" + field, node,
                        family="gt-ingredient", kind="gt-ingredient", relation="stores-" + field)
            for field in ("outputs", "fluidOutputs", "chancedOutputs", "chancedFluidOutputs"):
                raw = recipe.get(field)
                if type(raw) is dict:
                    member = "entries" if field.startswith("chanced") else "values"
                    if type(raw.get(member)) is list:
                        yield from self.rows(raw[member], pointer + "/value/" + field + "/" + member, node,
                            family="gt-output", kind="gt-output", relation="stores-" + field)

    def crafting(self, value, path, parent):
        owner = self.node("crafting-registry", "crafting-recipe", path, value,
                          omit=("entries", "nativeValues", "nativeNameIterationOrder", "nativeLookupOrder"))
        yield from self.emit(owner, parent)
        catalog = value.get("nativeValues", {})
        if type(catalog) is not dict:
            raise InitializationFamilyError(f"Crafting native values must be a mapping at {path}")
        entries = value.get("entries", {})
        for field, mapping, kind, family in (("entries", entries, "crafting-recipe", "crafting-recipe"),
                                             ("nativeValues", catalog, "crafting-native-value", "crafting-native-value")):
            for key, raw in mapping.items():
                pointer = _pointer(path + "/" + field, key)
                node = self.node(kind, family, pointer, raw, key, extra={"record_key": key})
                yield from self.emit(node, owner, "has-native-value" if field == "nativeValues" else "has-stored-crafting-recipe")
                for reference, rp in _references(raw, pointer):
                    if reference not in catalog:
                        raise InitializationFamilyError(f"Unresolved native value reference {reference!r} at {rp}")
                    target = self.node("crafting-native-value", "crafting-native-value", _pointer(path + "/nativeValues", reference), None)
                    yield self.edge("references-native-value", node, target, rp)
        for field in ("nativeNameIterationOrder", "nativeLookupOrder"):
            for ordinal, raw in enumerate(value.get(field, [])):
                pointer = _pointer(path + "/" + field, ordinal)
                order = self.node("crafting-order-entry", "crafting-order", pointer, raw,
                                  extra={"order_kind": field, "ordinal": ordinal})
                yield from self.emit(order, owner, "has-observed-crafting-order-entry")
                key = raw.get("key") if type(raw) is dict else raw
                if type(key) is not str or key not in entries:
                    raise InitializationFamilyError(f"Crafting order references missing recipe at {pointer}")
                target = self.node("crafting-recipe", "crafting-recipe", _pointer(path + "/entries", key), None)
                yield self.edge("order-entry-references-recipe", order, target, pointer)

    def ores(self, value, path, parent):
        owner = self.node("ore-observations", "ore-dictionary", path, value, omit=("reloadRecords", "currentMembership"))
        yield from self.emit(owner, parent)
        for operation, records in value.get("reloadRecords", {}).items():
            yield from self.rows(records, _pointer(path + "/reloadRecords", operation), owner,
                family="ore-reload-record", kind="ore-reload-record", relation="has-reload-" + operation + "-record")
        for name, raw in value.get("currentMembership", {}).items():
            pointer = _pointer(path + "/currentMembership", name)
            membership = self.node("ore-membership", "ore-dictionary", pointer, raw, name)
            yield from self.emit(membership, owner, "has-observed-ore-membership")
            members = raw.get("members")
            if type(members) is dict and type(members.get("values")) is list:
                yield from self.rows(members["values"], pointer + "/members/values", membership,
                    family="ore-dictionary", kind="ore-member", relation="has-observed-ore-member")
