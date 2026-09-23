"""Independent small retained shapes for the initialization observation graph."""

from copy import deepcopy
import unittest

from workbench_atlas_observations.families import (
    InitializationFamilyError, build_initialization_plan,
)


def retained_fixture():
    recipe = {"type": "gregtech.api.recipes.Recipe", "duration": {"type": "java.lang.Integer", "value": "40"},
        "inputs": [{"type": "gregtech.api.recipes.ingredients.GTRecipeOreInput", "oreName": "dustIron",
                    "amount": {"type": "java.lang.Integer", "value": "2"}, "isConsumable": True}],
        "fluidInputs": [], "outputs": {"type": "list", "values": [{"type": "net.minecraft.item.ItemStack", "count": 1, "tag": None}]},
        "chancedOutputs": {"logic": {"constant": "OR"}, "entries": [{"chance": 5000, "ingredient": {"count": 1}}]}}
    lookup = {"entries": {"same-stored-value": {"multiplicity": 2, "value": recipe}},
              "distinctNativeObjects": 2, "lookupReferences": 3}
    outside = {"entries": {"same-stored-value": {"multiplicity": 1, "value": deepcopy(recipe)}}}
    native = {
        "schema": "axiom.native-recipe-stage.v1", "loaderState": "AVAILABLE",
        "nativeMaterialRegistries": {"status": "observed", "registries": [{"modId": "gregtech", "registeredMaterials": 1}]},
        "nativeMaterialWitnesses": [{"name": "iron", "storageRegistry": "gregtech", "id": 26,
            "components": [{"material": "iron", "amount": 1}], "flags": ["GENERATE_PLATE"],
            "properties": ["fluid"], "nativePropertyState": {"schema": "axiom.native-material-property-state.v1", "properties": {"fluid": {
                "class": "gregtech.api.unification.material.properties.FluidProperty", "valuesObserved": True,
                "values": {"registrationCompleted": True, "primaryKey": "liquid", "stored": [{"key": "liquid", "fluid": "iron", "fluidRegistryIdentity": True}], "queued": []}}}}}],
        "nativeStoredRecipes": {"status": "observed", "maps": {"arc/furnace": {"name": "arc/furnace", "lookup": lookup,
            "categories": [{"category": {"name": "visible"}, "lookupMembers": {"same-stored-value": 2}, "outsideLookup": outside}]}}},
        "nativeStoredCraftingRecipes": {"registryFrozen": True,
            "entries": {"mod:second": {"storedFields": {"recipe#ingredient": {"nativeValueRef": "0"}, "recipe#alias": {"nativeValueRef": "0"}}},
                        "mod:first": {"storedFields": {}}},
            "nativeValues": {"0": {"type": "list", "values": [{"nativeValueRef": "1"}]},
                             "1": {"type": "custom", "owner": {"nativeValueRef": "0"}}},
            "nativeNameIterationOrder": ["mod:second", "mod:first"],
            "nativeLookupOrder": [{"id": 4, "key": "mod:first"}, {"id": 9, "key": "mod:second"}]},
        "nativeStoredFurnaceRecipes": {"smelting": [{"key": {"item": "minecraft:iron_ore", "damage": 32767}, "value": {"item": "minecraft:iron_ingot", "count": 1}}],
            "experience": [{"key": {"item": "minecraft:iron_ingot"}, "value": {"type": "java.lang.Float", "bits": "3f333333"}}],
            "timeWildcard": [{"item": "minecraft:iron_ore", "time": 80}], "timeMetadata": [{"key": {"damage": 0}, "value": 40}],
            "timeWildcardDefault": {"type": "java.lang.Integer", "value": "-1"}, "timeMetadataDefault": 0,
            "fuelConversions": [{"smelted": {"count": 1}, "fuel": {"count": 2}}], "callbackInvocationsByObserver": 0},
        "nativeOreMutations": {"scope": "groovy-recorded-ore-names-current-membership",
            "reloadRecords": {"backup": [{"name": "dustIron", "stack": {"count": 1}}], "scripted": [{"name": "dustIron", "stack": {"count": 2}}]},
            "currentMembership": {"dustIron": {"registered": True, "nativeOreId": 12, "members": {"type": "list", "values": [{"count": 2}]}}, "absent": {"registered": False}}},
        "nativeConfiguration": {"classes": [{"class": "mod.Config", "owner": "mod", "path": "config/mod.cfg", "nativeOwnerIdentity": True}],
                                "declaredClasses": {"mod.Config": "mod"}, "observationsComplete": True},
        "nativeBiomes": {"biomes": {"mod:forest": {"dictionaryTypes": ["FOREST"]}}, "disabledBiomes": ["mod:desert"]},
        "nativeWorldgenBiomeBindings": {"definitions": {"config/gregtech/worldgen/vein/example.json": {
            "nativeDefinitionMembership": True, "biomeWeights": {"mod:forest": {"configuredWeight": 5, "nativeWeight": 5}}}}},
        "nativeBlockHardness": {"entries": {"mod:block": {"type": "float32", "bits": "3f800000"}}},
        "nativeStoredCraftingCallbacks": {"entries": {"mod:recipe": {"recipeFunction": {"kind": "guarded-source-closure", "compilation": {"sourceFile": "recipe.groovy", "bodyLines": [12]}}}}, "callbackInvocationsByObserver": 0},
        "nativeScriptIndex": [{"path": "postInit/recipe.groovy", "classDefined": True, "preprocessorCheckFailed": False}],
        "initializedMods": [{"id": "mod", "state": "AVAILABLE"}],
        "candidateDispatchObservations": {"recipe:12": {"owner": "mod.Native", "method": "register"}},
        "nativeArtifacts": {"mod": {"sha256": "abc"}}, "mixinConfigurations": ["mixins.mod.json"],
        "newOwnerObservation": {"unknownField": {"retained": [1, 2, 3]}},
        "constructionStage": {"inheritedKeys": ["nativeConfiguration", "nativeArtifacts"],
                              "values": {"loaderState": "CONSTRUCTING"}},
        "snapshotEncoding": {"executionFields": ["registrationEffects", "customMetaItems"], "fingerprintEncoding": "base64url-sha256"},
    }
    effects = {"phase": "FROZEN", "materials": {"status": "observed", "inventoryComplete": True, "entries": {"iron": "retained-hash-not-values"}},
        "fluids": {"status": "unavailable", "inventoryComplete": False, "failure": {"message": "read failed"}},
        "prefixItems": {"entries": {"plate": "encoded-fingerprint"}, "witnesses": [{"material": "iron", "prefix": "plate", "selected": {"empty": False, "item": "mod:plate", "unifierIdentity": True}, "generated": [{"item": "mod:plate", "metadata": 26}]}]}}
    effects["schema"] = "axiom.native-registration-effects.v1"
    for key, schema in {
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
    }.items():
        native[key]["schema"] = schema
    execution = {"nativeInitialization": native, "registrationEffects": effects,
        "customMetaItems": {"items": [{"registryName": "mod:meta", "variants": [{"name": "circuit", "meta": 3, "components": [{"class": "ElectricStats", "tier": 2}]}]}]},
        "diagnostics": [{"message": "original error", "locations": [{"path": "recipe.groovy", "line": 12}]}]}
    return {"format": "workbench-material-check-report-v1", "findings": [{"severity": "error", "message": "original error"}],
            "native": {"status": "complete", "result": {"execution": execution, "sourceScope": {"sourceSha256": "abc"},
                "initialization": {"status": "returned", "nativeErrorObserved": True}}}}


class InitializationFamiliesTest(unittest.TestCase):
    def project(self, report=None, **kwargs):
        report = report or retained_fixture()
        plan = build_initialization_plan(report, snapshot_id="snapshot-a", **kwargs)
        nodes, edges = list(plan.iter_nodes()), list(plan.iter_edges())
        ids = {n["id"] for n in nodes}
        self.assertEqual(len(ids), len(nodes))
        self.assertEqual(len({e["id"] for e in edges}), len(edges))
        self.assertTrue(all(e["source"] in ids and e["target"] in ids for e in edges))
        return plan, nodes, edges

    def test_all_families_are_original_observations_and_input_is_unchanged(self):
        report = retained_fixture()
        before = deepcopy(report)
        plan, nodes, edges = self.project(report)
        self.assertEqual(report, before)
        self.assertTrue(all(n["kind"].startswith("initialization-") for n in nodes))
        self.assertFalse(any(n["kind"] == "gt-recipe" for n in nodes))
        self.assertFalse(any("accepts" in e["relation"] or "caused" in e["relation"] for e in edges))
        coverage = {row["family"]: row for row in plan.coverage}
        for family in ("gt-map", "gt-ingredient", "gt-output", "material-fingerprint", "material-witness",
                       "generated-form-witness", "custom-item", "ore-reload-record", "configuration",
                       "biome", "worldgen-binding", "block-hardness", "callback", "source", "mod", "artifact"):
            self.assertGreater(coverage[family]["node_count"], 0, family)
        unknown = [n for n in nodes if n["properties"]["json_pointer"].endswith("/newOwnerObservation/unknownField")]
        self.assertEqual(unknown[0]["properties"]["raw_value"], {"retained": [1, 2, 3]})

    def test_gt_lookup_and_category_only_values_remain_separate(self):
        _, nodes, edges = self.project()
        recipes = [n for n in nodes if n["kind"] == "initialization-gt-stored-recipe"]
        self.assertEqual(len(recipes), 2)
        self.assertEqual({n["properties"]["lookup_membership_observed"] for n in recipes}, {True, False})
        active = next(n for n in recipes if n["properties"]["lookup_membership_observed"])
        self.assertEqual(active["properties"]["raw_value"]["multiplicity"], 2)
        refs = [e for e in edges if e["relation"] == "category-references-lookup-recipe"]
        self.assertEqual([(e["target"], e["properties"]["multiplicity"]) for e in refs], [(active["id"], 2)])
        self.assertIn("arc~1furnace", active["properties"]["json_pointer"])

    def test_shared_and_cyclic_crafting_values_preserve_occurrence_references(self):
        _, nodes, edges = self.project()
        values = [n for n in nodes if n["kind"] == "initialization-crafting-native-value"]
        self.assertEqual(len(values), 2)
        refs = [e for e in edges if e["relation"] == "references-native-value"]
        self.assertEqual(len(refs), 4)
        zero = next(n for n in values if n["properties"]["record_key"] == "0")
        self.assertEqual(sum(e["target"] == zero["id"] for e in refs), 3)
        self.assertTrue(any("recipe~1" not in e["semantic_key"] and "recipe#" in e["semantic_key"] for e in refs))

    def test_unresolved_and_malformed_reference_refused_in_both_streams(self):
        for bad in ({"nativeValueRef": "missing"}, {"nativeValueRef": "0", "extra": True}):
            report = retained_fixture()
            crafting = report["native"]["result"]["execution"]["nativeInitialization"]["nativeStoredCraftingRecipes"]
            crafting["entries"]["mod:second"]["storedFields"]["recipe#alias"] = bad
            plan = build_initialization_plan(report, snapshot_id="a")
            for stream in (plan.iter_nodes, plan.iter_edges):
                with self.assertRaises(InitializationFamilyError):
                    list(stream())

    def test_both_crafting_orders_and_furnace_subfamilies_are_retained(self):
        _, nodes, _ = self.project()
        orders = [n["properties"] for n in nodes if n["kind"] == "initialization-crafting-order-entry"]
        lookup = [n["raw_value"] for n in orders if n["order_kind"] == "nativeLookupOrder"]
        self.assertEqual(lookup, [{"id": 4, "key": "mod:first"}, {"id": 9, "key": "mod:second"}])
        names = [n["raw_value"] for n in orders if n["order_kind"] == "nativeNameIterationOrder"]
        self.assertEqual(names, ["mod:second", "mod:first"])
        furnace = next(n for n in nodes if n["kind"] == "initialization-furnace-storage")["properties"]["raw_value"]
        self.assertEqual(furnace["timeWildcardDefault"], {"type": "java.lang.Integer", "value": "-1"})
        for name in ("smelting", "experience", "timeWildcard", "timeMetadata", "fuelConversions"):
            self.assertEqual(sum(n["kind"] == "initialization-furnace-" + name for n in nodes), 1)

    def test_fingerprints_gaps_and_reload_state_do_not_gain_meaning(self):
        _, nodes, edges = self.project()
        fingerprint = next(n for n in nodes if n["kind"] == "initialization-fingerprint-member"
                           and n["properties"]["family"] == "material-fingerprint")
        self.assertEqual(fingerprint["properties"]["raw_value"], "retained-hash-not-values")
        fluids = next(n for n in nodes if n["kind"] == "initialization-effect-catalog" and n["properties"]["family"] == "fluid-fingerprint")
        self.assertEqual(fluids["properties"]["raw_value"]["status"], "unavailable")
        self.assertEqual(sum(n["kind"] == "initialization-ore-reload-record" for n in nodes), 2)
        self.assertEqual(sum(n["kind"] == "initialization-ore-member" for n in nodes), 1)
        self.assertTrue(any(e["relation"] == "has-reload-backup-record" for e in edges))

    def test_compact_stages_link_existing_fields_without_copying_execution(self):
        _, nodes, edges = self.project()
        inherited = [e for e in edges if e["relation"] == "inherits-stored-stage-field"]
        self.assertEqual({e["properties"]["field"] for e in inherited}, {"nativeConfiguration", "nativeArtifacts"})
        targets = {n["id"]: n for n in nodes}
        self.assertTrue(all("/constructionStage/" not in targets[e["target"]]["properties"]["json_pointer"] for e in inherited))
        report = retained_fixture()
        report["native"]["result"]["execution"]["nativeInitialization"]["constructionStage"]["inheritedKeys"].append("missing")
        with self.assertRaises(InitializationFamilyError):
            self.project(report)

    def test_occurrence_ids_bound_to_snapshot_and_selected_side(self):
        single = retained_fixture()
        paired = {"findings": [], "native": {"result": {"baseline": single["native"], "candidate": deepcopy(single["native"])}}}
        _, baseline, _ = self.project(paired, side="baseline")
        _, candidate, _ = self.project(paired, side="candidate")
        self.assertFalse({n["id"] for n in baseline} & {n["id"] for n in candidate})
        self.assertTrue(all("/candidate/" not in n["properties"]["json_pointer"] for n in baseline))
        other = list(build_initialization_plan(single, snapshot_id="other").iter_nodes())
        _, original, _ = self.project(single)
        self.assertFalse({n["id"] for n in other} & {n["id"] for n in original})

    def test_compact_inheritance_can_target_fields_moved_to_execution(self):
        report = retained_fixture()
        native = report["native"]["result"]["execution"]["nativeInitialization"]
        native["constructionStage"]["inheritedKeys"].append("registrationEffects")
        _, nodes, edges = self.project(report)
        inheritance = next(e for e in edges if e["relation"] == "inherits-stored-stage-field"
                           and e["properties"]["field"] == "registrationEffects")
        target = next(n for n in nodes if n["id"] == inheritance["target"])
        self.assertEqual(target["properties"]["json_pointer"], "/native/result/execution/registrationEffects")
        native["constructionStage"]["values"]["registrationEffects"] = {}
        with self.assertRaises(InitializationFamilyError):
            self.project(report)

    def test_coverage_does_not_walk_and_cancellation_keeps_it_unfinished(self):
        calls = []
        def cancel():
            calls.append(None)
            raise InterruptedError("cancelled")
        plan = build_initialization_plan(retained_fixture(), snapshot_id="a", check_cancelled=cancel)
        self.assertTrue(all(row["status"] == "not-scanned" for row in plan.coverage))
        self.assertEqual(calls, [])
        with self.assertRaises(InterruptedError):
            list(plan.iter_nodes())
        self.assertTrue(all(row["status"] == "not-scanned" for row in plan.coverage))

    def test_nested_schema_is_a_boundary_independent_of_report_admission(self):
        for field in ("nativeStoredRecipes", "nativeStoredCraftingRecipes", "nativeStoredFurnaceRecipes",
                      "nativeOreMutations", "nativeConfiguration", "snapshotEncoding"):
            for schema in ("future-native-format.v9", None):
                report = retained_fixture()
                section = report["native"]["result"]["execution"]["nativeInitialization"][field]
                if schema is None:
                    section.pop("schema")
                else:
                    section["schema"] = schema
                with self.subTest(field=field, schema=schema), self.assertRaises(InitializationFamilyError):
                    self.project(report)

    def test_empty_containers_and_scalar_values_are_not_lost_when_splitting(self):
        report = retained_fixture()
        furnace = report["native"]["result"]["execution"]["nativeInitialization"]["nativeStoredFurnaceRecipes"]
        furnace["timeWildcard"] = []
        _, nodes, _ = self.project(report)
        owner = next(n for n in nodes if n["kind"] == "initialization-furnace-storage")["properties"]
        self.assertEqual(owner["raw_value"]["timeWildcard"], [])
        self.assertNotIn("timeWildcard", owner["projected_fields"])
        self.assertEqual(owner["projected_collections"]["smelting"], {"type": "sequence", "count": 1})
        self.assertEqual(owner["raw_value"]["callbackInvocationsByObserver"], 0)

    def test_failed_check_without_native_remains_report_and_findings(self):
        report = {"native": None, "findings": [{"message": "failed before native"}], "state": "failed"}
        plan, nodes, edges = self.project(report)
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0]["properties"]["raw_value"]["native"], None)
        self.assertTrue(all(row["status"] == "not-observed" for row in plan.coverage
                            if row["family"] not in {"report", "diagnostic"}))
        with self.assertRaises(InitializationFamilyError):
            build_initialization_plan(report, snapshot_id="a", side="candidate")

    def test_every_raw_cut_is_the_original_value_with_only_declared_children_removed(self):
        report = retained_fixture()
        _, nodes, _ = self.project(report)
        for node in nodes:
            properties = node["properties"]
            original = report
            for part in properties["json_pointer"].split("/")[1:]:
                key = part.replace("~1", "/").replace("~0", "~")
                original = original[int(key)] if isinstance(original, list) else original[key]
            removed = properties.get("projected_fields", [])
            expected = {k: v for k, v in original.items() if k not in removed} if removed else original
            self.assertEqual(properties["raw_value"], expected, properties["json_pointer"])
            for key, descriptor in properties.get("projected_collections", {}).items():
                self.assertEqual(descriptor, {"type": "mapping" if isinstance(original[key], dict) else "sequence",
                                              "count": len(original[key])})


if __name__ == "__main__":
    unittest.main()
