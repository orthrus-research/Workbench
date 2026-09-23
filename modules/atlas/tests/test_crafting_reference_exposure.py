"""Forward-path toy oracles for the reverse stored-reference analysis."""

from copy import deepcopy
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from workbench_atlas_categorical_graph import CategoricalGraphBundleBuilder, edge_record, node_record
from workbench_atlas_observations import ObservationError, open_observations
from workbench_atlas_observations.exposure import derive_crafting_reference_exposure


def graph(path, catalogs, *, incomplete=False, extra_edges=(), unknown_value=None, legacy=False, mutate=None):
    nodes, edges, lookup = [], [], {}
    for name, (values, recipe_names, references) in catalogs.items():
        pointer = "/native/result/execution/" + name + "/nativeStoredCraftingRecipes"
        def make(kind, key, raw, member):
            source_pointer = pointer + member
            evidence = [{"snapshot_id": "snapshot:fixture", "side": "single", "section": "report",
                         "record_key": "value", "json_pointer": source_pointer, "observation_kind": "observed-storage"}]
            node = node_record("initialization-crafting-" + kind, name + ":" + key,
                {"label": key, "raw_value": raw, "json_pointer": source_pointer, "record_key": key}, evidence)
            nodes.append(node)
            lookup[(name, key)] = node
            return node
        owner = make("registry", "catalog", {"schema": "axiom.native-stored-crafting-recipes.v1",
            "status": "incomplete" if incomplete else "observed", "storedValuesComplete": not incomplete,
            "affectingGaps": ["unknown recipe fields"] if incomplete else []}, "")
        owner["properties"]["projected_fields"] = []
        owner["properties"]["projected_collections"] = {}
        for field, collection in (("nativeValues", values), ("entries", recipe_names)):
            if collection:
                owner["properties"]["projected_fields"].append(field)
                owner["properties"]["projected_collections"][field] = {"type": "mapping", "count": len(collection)}
            else:
                owner["properties"]["raw_value"][field] = {}
        for key in values:
            node = make("native-value", key, {"type": "list", "values": []}, "/nativeValues/" + key.replace("~", "~0").replace("/", "~1"))
            if unknown_value == key:
                node["properties"]["raw_value"] = {"observation": "incomplete", "type": "CustomValue", "reason": "unsupported"}
            edges.append(edge_record("has-native-value", owner["id"], node["id"], {}))
        for key in recipe_names:
            node = make("recipe", key, {"type": "CraftingRecipe", "storedFields": {},
                                        "storedValuesComplete": True, "affectingGaps": []}, "/entries/" + key.replace("~", "~0").replace("/", "~1"))
            edges.append(edge_record("has-stored-crafting-recipe", owner["id"], node["id"], {}))
        for ordinal, (source, target) in enumerate(references):
            a, b = lookup[(name, source)], lookup[(name, target)]
            a["properties"]["raw_value"].setdefault("references", []).append({"nativeValueRef": target})
            pointer = a["properties"]["json_pointer"] + "/references/" + str(len(a["properties"]["raw_value"]["references"]) - 1)
            evidence = [{**a["evidence"][0], "json_pointer": pointer}]
            edges.append(edge_record("references-native-value", a["id"], b["id"], {"ordinal": ordinal},
                                    evidence, semantic_key=pointer))
    for (a, source), (b, target) in extra_edges:
        edges.append(edge_record("references-native-value", lookup[(a, source)]["id"], lookup[(b, target)]["id"], {}))
    if mutate is not None:
        mutate(nodes, edges, lookup)
    builder = CategoricalGraphBundleBuilder(path,
        scope={"observation_contract": "workbench-atlas-initialization-projection-v1", "selected_side": "single"},
        evidence_binding={"snapshot_id": "snapshot:fixture", "native_outcome": "native-failed", "coverage": "complete"},
        **({} if legacy else {"evidence_authority": "retained-observations-v1"}))
    builder.add_partition("crafting", classification="fixture", dependencies=(), nodes=nodes, edges=edges,
                          evidence_categories=("report",), limitations=("Matching was not evaluated.",))
    builder.close()
    return lookup, edges


def forward_oracle(values, recipes, references, selected):
    """Enumerate simple forward paths: independent direction and termination."""
    paths = {}
    for source in [*values, *recipes]:
        shortest = None
        pending = [(source, (source,))]
        while pending:
            current, path = pending.pop()
            if current == selected:
                distance = len(path) - 1
                shortest = distance if shortest is None else min(shortest, distance)
            for origin, target in references:
                if origin == current and target not in path:
                    pending.append((target, (*path, target)))
        if shortest is not None:
            paths[source] = shortest
    return paths


class CraftingReferenceExposureTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def derive(self, definition, selected="v0", **kwargs):
        path = self.root / "graph"
        nodes, edges = graph(path, {"catalog": definition})
        with open_observations(path) as view:
            result = derive_crafting_reference_exposure(view, nodes[("catalog", selected)]["id"], **kwargs)
        return result, nodes, edges

    def test_cycles_aliases_and_shortest_paths_match_independent_forward_oracle(self):
        values = ["v0", "v1", "v2", "v3", "unused"]
        recipes = ["r1", "r2", "r3"]
        refs = [("v1", "v0"), ("v2", "v1"), ("v1", "v2"), ("v3", "v0"),
                ("r1", "v2"), ("r1", "v3"), ("r1", "v3"), ("r2", "v1"), ("r3", "unused")]
        expected = forward_oracle(values, recipes, refs, "v0")
        result, nodes, _ = self.derive((values, recipes, refs))
        actual = {row["recipe"]["properties"]["record_key"]: row["distance"] for row in result["results"]}
        self.assertEqual(actual, {key: expected[key] for key in recipes if key in expected})
        self.assertEqual(result["traversal"]["visited_nodes"], len(expected))
        self.assertEqual(result["traversal"]["examined_reference_edges"], 8)
        self.assertEqual(len(result["reference_edges"]), 8)  # Both aliases retained.
        self.assertEqual(result["evidence"]["native_outcome"], "native-failed")
        self.assertFalse(result["evidence"]["matching_evaluated"])
        by_id = {node["id"]: key for (_, key), node in nodes.items()}
        for row in result["results"]:
            witness = row["witness"]
            self.assertEqual(len(witness["edges"]), row["distance"])
            self.assertEqual(by_id[witness["node_ids"][-1]], "v0")
            self.assertEqual(len(witness["node_ids"]), len(set(witness["node_ids"])))
            self.assertEqual([(edge["source"], edge["target"]) for edge in witness["edges"]],
                             list(zip(witness["node_ids"], witness["node_ids"][1:])))

    def test_small_generated_graphs_use_forward_oracle_not_analysis_helpers(self):
        randomizer = random.Random(426)
        for case in range(8):
            values, recipes = [f"v{i}" for i in range(6)], [f"r{i}" for i in range(3)]
            refs = [(source, target) for source in values + recipes for target in values
                    if randomizer.randrange(5) == 0]
            nodes, _ = graph(self.root / str(case), {"c": (values, recipes, refs)})
            expected = forward_oracle(values, recipes, refs, "v0")
            with open_observations(self.root / str(case)) as view:
                result = derive_crafting_reference_exposure(view, nodes[("c", "v0")]["id"])
            actual = {row["recipe"]["properties"]["record_key"]: row["distance"] for row in result["results"]}
            self.assertEqual(actual, {key: expected[key] for key in recipes if key in expected})
            self.assertEqual(result["traversal"]["visited_nodes"], len(expected))

    def test_catalog_local_ids_are_not_shared_and_cross_catalog_edges_refuse(self):
        definition = (["v0", "v1"], ["r"], [("v1", "v0"), ("r", "v1")])
        nodes, _ = graph(self.root / "isolated", {"a": definition, "b": definition})
        with open_observations(self.root / "isolated") as view:
            result = derive_crafting_reference_exposure(view, nodes[("a", "v0")]["id"])
        self.assertEqual([row["recipe"]["id"] for row in result["results"]], [nodes[("a", "r")]["id"]])
        nodes, _ = graph(self.root / "crossed", {"a": definition, "b": definition},
                         extra_edges=[(("b", "v1"), ("a", "v0"))])
        with open_observations(self.root / "crossed") as view, self.assertRaisesRegex(ObservationError, "catalog"):
            derive_crafting_reference_exposure(view, nodes[("a", "v0")]["id"])

    def test_depth_and_node_bounds_preserve_frontier_and_block_absence(self):
        definition = (["v0", "v1", "v2"], ["r"], [("v1", "v0"), ("v2", "v1"), ("r", "v2")])
        nodes, _ = graph(self.root / "graph", {"c": definition})
        with open_observations(self.root / "graph") as view:
            for bound in ({"max_depth": 1}, {"max_nodes": 1}):
                result = derive_crafting_reference_exposure(view, nodes[("c", "v0")]["id"], **bound)
                self.assertEqual(result["summary"]["status"], "undetermined")
                self.assertEqual(result["traversal"]["state"], "truncated")
                self.assertEqual(len(result["frontier"]), 1)
                self.assertIn(result["frontier"][0]["reference_edge_id"], {edge["id"] for edge in result["reference_edges"]})
            full = derive_crafting_reference_exposure(view, nodes[("c", "v0")]["id"], max_depth=3, max_nodes=4)
            self.assertEqual(full["traversal"]["state"], "complete")
            self.assertEqual(full["results"][0]["distance"], 3)

    def test_self_reference_at_node_bound_is_complete_not_a_cut(self):
        result, _, _ = self.derive((["v0"], [], [("v0", "v0")]), max_depth=1, max_nodes=1)
        self.assertEqual(result["traversal"]["state"], "complete")
        self.assertEqual(result["summary"]["status"], "none-observed")
        self.assertEqual(result["traversal"]["examined_reference_edges"], 1)

    def test_incomplete_catalog_or_selected_unknown_never_claims_absence(self):
        for name, options in (("catalog-gap", {"incomplete": True}), ("value-gap", {"unknown_value": "v0"})):
            nodes, _ = graph(self.root / name, {"c": (["v0"], [], [])}, **options)
            with open_observations(self.root / name) as view:
                result = derive_crafting_reference_exposure(view, nodes[("c", "v0")]["id"])
            self.assertEqual(result["traversal"]["state"], "complete")
            self.assertEqual(result["evidence"]["crafting_reference_inventory"], "incomplete")
            self.assertEqual(result["summary"]["status"], "undetermined")
            self.assertTrue(result["evidence_gaps"])

    def test_more_than_one_generic_query_page_and_deep_chain_are_not_truncated(self):
        values = [f"v{i:04}" for i in range(80)]
        recipes = [f"r{i:04}" for i in range(1005)]
        references = list(zip(values[1:], values)) + [(recipe, values[-1]) for recipe in recipes]
        result, _, _ = self.derive((values, recipes, references), selected=values[0])
        self.assertEqual(result["traversal"]["state"], "complete")
        self.assertEqual(len(result["results"]), 1005)
        self.assertTrue(all(row["distance"] == 80 for row in result["results"]))

    def test_invalid_selection_limits_legacy_format_and_cancel_refuse(self):
        nodes, _ = graph(self.root / "graph", {"c": (["v0"], ["r"], [])})
        with open_observations(self.root / "graph") as view:
            for arguments in ({"max_nodes": True}, {"max_depth": 0}, {"max_nodes": -1}):
                with self.assertRaises(ObservationError):
                    derive_crafting_reference_exposure(view, nodes[("c", "v0")]["id"], **arguments)
            with self.assertRaises(ObservationError):
                derive_crafting_reference_exposure(view, nodes[("c", "r")]["id"])
            with patch.object(view, "_cancel", side_effect=RuntimeError("cancelled")), self.assertRaisesRegex(RuntimeError, "cancelled"):
                derive_crafting_reference_exposure(view, nodes[("c", "v0")]["id"])
        legacy, _ = graph(self.root / "legacy", {"c": (["v0"], [], [])}, legacy=True)
        with open_observations(self.root / "legacy") as view, self.assertRaises(ObservationError):
            derive_crafting_reference_exposure(view, legacy[("c", "v0")]["id"])

    def test_repeated_results_and_canonical_input_nodes_are_unchanged(self):
        nodes, _ = graph(self.root / "graph", {"c": (["v0", "v1"], ["r"], [("v1", "v0"), ("r", "v1")])})
        before = deepcopy(nodes)
        with open_observations(self.root / "graph") as view:
            a = derive_crafting_reference_exposure(view, nodes[("c", "v0")]["id"])
            b = derive_crafting_reference_exposure(view, nodes[("c", "v0")]["id"])
        self.assertEqual(a, b)
        self.assertEqual(nodes, before)

    def test_sealed_but_incorrect_reference_projection_refuses(self):
        definition = (["v0", "v1", "unused"], ["r"], [("v1", "v0"), ("r", "v1")])
        def corrupt(kind):
            def change(nodes, edges, lookup):
                first = next(i for i, edge in enumerate(edges) if edge["relation"] == "references-native-value")
                edge = edges[first]
                if kind == "missing-reference":
                    del edges[first]
                elif kind in {"wrong-target", "wrong-path", "wrong-evidence", "duplicate-reference"}:
                    evidence = deepcopy(edge["evidence"])
                    key, target = edge["semantic_key"], edge["target"]
                    if kind == "wrong-target":
                        # The raw reference still points to v0, while the sealed edge points to unused.
                        target = lookup[("c", "unused")]["id"]
                    if kind == "wrong-path":
                        key += "/nativeValueRef"
                        evidence[0]["json_pointer"] = key
                    if kind == "wrong-evidence":
                        evidence[0]["side"] = "candidate"
                    if kind == "duplicate-reference":
                        key += "/duplicate"
                    replacement = edge_record(edge["relation"], edge["source"], target,
                                              {"different-properties": True}, evidence, semantic_key=key)
                    if kind == "duplicate-reference":
                        edges.append(replacement)
                    else:
                        edges[first] = replacement
                elif kind in {"missing-ownership", "duplicate-ownership", "wrong-ownership"}:
                    member = lookup[("c", "unused")]["id"]
                    i = next(i for i, edge in enumerate(edges) if edge["relation"] == "has-native-value" and edge["target"] == member)
                    if kind == "missing-ownership":
                        del edges[i]
                    else:
                        e = edges[i]
                        edges.append(edge_record("has-stored-crafting-recipe" if kind == "wrong-ownership" else e["relation"],
                                                 e["source"], e["target"], {}, semantic_key="extra-owner"))
                elif kind == "wrong-count":
                    lookup[("c", "catalog")]["properties"]["projected_collections"]["nativeValues"]["count"] = 4
                elif kind == "wrong-collection-type":
                    lookup[("c", "catalog")]["properties"]["projected_collections"]["nativeValues"]["type"] = "sequence"
                elif kind == "malformed-reference":
                    lookup[("c", "v1")]["properties"]["raw_value"]["references"][0]["extra"] = True
                elif kind == "missing-target":
                    lookup[("c", "v1")]["properties"]["raw_value"]["references"][0]["nativeValueRef"] = "absent"
                elif kind == "record-key-mismatch":
                    lookup[("c", "v1")]["properties"]["record_key"] = "wrong"
                elif kind == "malformed-side":
                    lookup[("c", "v1")]["evidence"][0]["side"] = []
            return change
        for case in ("missing-reference", "wrong-target", "wrong-path", "wrong-evidence", "duplicate-reference",
                     "missing-ownership", "duplicate-ownership", "wrong-ownership", "wrong-count",
                     "wrong-collection-type", "malformed-reference", "missing-target", "record-key-mismatch", "malformed-side"):
            with self.subTest(case=case):
                nodes, _ = graph(self.root / case, {"c": definition}, mutate=corrupt(case))
                with open_observations(self.root / case) as view, self.assertRaises(ObservationError):
                    derive_crafting_reference_exposure(view, nodes[("c", "v0")]["id"])

    def test_escaped_keys_and_reference_path_are_exact(self):
        result, _, _ = self.derive((["v/0~", "v1"], ["mod:r/name~"], [("v1", "v/0~"), ("mod:r/name~", "v1")]), selected="v/0~")
        self.assertEqual(result["summary"]["recorded_recipe_count"], 1)
        self.assertIn("mod:r~1name~0", result["results"][0]["recipe"]["properties"]["json_pointer"])

    def test_unknown_elsewhere_in_catalog_prevents_absence_and_successful_verification_is_reused(self):
        nodes, _ = graph(self.root / "graph", {"c": (["v0", "unknown"], [], [])}, unknown_value="unknown")
        with open_observations(self.root / "graph") as view:
            first = derive_crafting_reference_exposure(view, nodes[("c", "v0")]["id"])
            self.assertEqual(first["summary"]["status"], "undetermined")
            first["evidence_gaps"].clear()  # A returned record cannot corrupt the verified view's cache.
            with patch("workbench_atlas_observations.exposure._raw_references", side_effect=AssertionError("rescanned catalog")):
                again = derive_crafting_reference_exposure(view, nodes[("c", "v0")]["id"])
            self.assertTrue(again["evidence_gaps"])

    def test_cancelled_catalog_verification_does_not_cache_success(self):
        nodes, _ = graph(self.root / "graph", {"c": (["v0", "v1"], [], [("v1", "v0")])})
        from workbench_atlas_observations import exposure
        original = exposure._raw_references
        with open_observations(self.root / "graph") as view:
            with patch.object(exposure, "_raw_references", side_effect=RuntimeError("cancelled inventory")), self.assertRaisesRegex(RuntimeError, "cancelled inventory"):
                derive_crafting_reference_exposure(view, nodes[("c", "v0")]["id"])
            with patch.object(exposure, "_raw_references", wraps=original) as checked:
                result = derive_crafting_reference_exposure(view, nodes[("c", "v0")]["id"])
                self.assertGreater(checked.call_count, 0)
                self.assertEqual(result["traversal"]["state"], "complete")


if __name__ == "__main__":
    unittest.main()
