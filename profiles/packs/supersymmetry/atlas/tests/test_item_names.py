"""Synthetic sealed native-name observations; no game or alias inference."""

from copy import deepcopy
import unittest

from workbench_atlas_recipe_health import open_recipe_health
from workbench_crucible_runtime_snapshot.capture import RuntimeCaptureError
from workbench_profile_supersymmetry.finite_item_matching import ARTIFACT_SHA256
from workbench_profile_supersymmetry.item_names import (
    RESOLVER_CLASS_SHA256, ItemNameObservationError, validate_item_names,
)
from workbench_profile_supersymmetry.recipe_graphs import RecipeGraphProjectionError
import test_recipe_graph_projection as fixtures
from test_recipe_graph_projection import item


def binding(query="circuit.microprocessor", *, stack=None, resolution="cache"):
    namespace, name = "gregtech", query
    i = query.find(":")
    if i >= 0:
        name = query[i + 1:]
        if i > 1:
            namespace = query[:i]
    return dict(record_type="gt-item-name-binding", query=query, namespace=namespace, name=name,
                authority="GroovyScriptModule.getMetaItem", resolution=resolution,
                stack=None if resolution == "unresolved" else item() if stack is None else stack,
                gregtech_sha256=ARTIFACT_SHA256["gregtech_sha256"],
                resolver_class_sha256=RESOLVER_CLASS_SHA256)


class ObservedItemNameTests(unittest.TestCase):
    setUp = fixtures.FiniteRecipeProjectionTests.setUp
    add_recipe = fixtures.FiniteRecipeProjectionTests.add_recipe
    publish = fixtures.FiniteRecipeProjectionTests.publish
    project = fixtures.FiniteRecipeProjectionTests.project
    rows = fixtures.FiniteRecipeProjectionTests.rows

    def names(self, rows):
        self.categories["gt-item-names"] = ("reference-item-names", rows)

    def test_search_literal_alias_preserves_exact_ids_and_recipe_edges(self):
        self.publish()
        self.project()
        _, before, before_edges = self.rows()
        before_ids = {node["id"] for node in before}
        self.output = self.home / "named-graph"
        self.names([binding(), binding("gregtech:circuit.microprocessor")])
        self.publish()
        self.project()
        manifest, nodes, edges = self.rows()
        self.assertEqual(before_ids, {node["id"] for node in nodes})
        self.assertEqual(before_edges, edges)
        with open_recipe_health(self.output) as view:
            found = view.search("circuit.microprocessor", kinds=["item-variant"])["results"]
        self.assertEqual(1, len(found))
        node = next(n for n in nodes if n["id"] == found[0]["selection_id"])
        self.assertEqual("fixture:tool|3|3", node["semantic_key"])
        self.assertEqual({"circuit.microprocessor", "gregtech:circuit.microprocessor"},
                         {row["query"] for row in node["properties"]["observed_item_names"]})
        self.assertEqual({"gt-item-names"}, {r["adapter_id"] for r in node["evidence"]})
        self.assertEqual(2, manifest["evidence_binding"]["item_name_observations"]["annotated_binding_count"])
        # The same registry/metadata with a different tag must not inherit names.
        tagged = next(n for n in nodes if n["kind"] == "item-variant" and n["properties"]["tag"])
        self.assertNotIn("observed_item_names", tagged["properties"])

    def test_names_are_optional_and_do_not_infer_from_display_or_metadata(self):
        self.publish()
        self.project()
        manifest, nodes, _ = self.rows()
        self.assertNotIn("item_name_observations", manifest["evidence_binding"])
        self.assertFalse(any("observed_item_names" in n["properties"] for n in nodes))
        with open_recipe_health(self.output) as view:
            self.assertEqual([], view.search("circuit.microprocessor")["results"])

    def test_typed_tag_binding_selects_only_that_exact_resource(self):
        tag = {"tag_id": 10, "value": {"Configuration": {"tag_id": 3, "value": 7}}}
        row = self.categories["gt-recipes"][1][0]
        row["recipe"]["chanced_item_outputs"]["entries"][0]["value"]["tag"] = tag
        row["semantic_sha256"] = fixtures.digest(row["recipe"])
        self.names([binding("configured.tool", stack=item(5, tag=tag))])
        self.publish()
        self.project()
        _, nodes, _ = self.rows()
        with open_recipe_health(self.output) as view:
            found = view.search("configured.tool", kinds=["item-variant"])["results"]
        self.assertEqual(1, len(found))
        self.assertEqual(tag, found[0]["properties"]["tag"])
        plain = next(n for n in nodes if n["semantic_key"] == "fixture:tool|3|3")
        self.assertNotIn("observed_item_names", plain["properties"])

    def test_null_and_outside_domain_are_retained_without_new_resources(self):
        self.names([binding("missing", resolution="unresolved"),
                    binding("other:machine", stack=item(metadata=99), resolution="meta-tile-entity")])
        self.input_value["item_name_queries"] = ["missing"]
        self.publish()
        self.project()
        manifest, nodes, _ = self.rows()
        self.assertEqual(5, sum(n["kind"] in {"item-variant", "forge-fluid"} for n in nodes))
        names = manifest["evidence_binding"]["item_name_observations"]
        self.assertEqual(0, names["annotated_binding_count"])
        self.assertEqual(["missing"], [r["query"] for r in names["unresolved_queries"]])
        self.assertEqual(["other:machine"], [r["query"] for r in names["outside_recipe_resources"]])
        self.assertTrue(names["unresolved_queries"][0]["evidence"][0]["record_sha256"])

    def test_requested_missing_row_and_ambiguous_equivalent_queries_refused(self):
        cases = [([binding()], ["missing"], "no observation"),
                 ([binding(), binding()], None, "duplicate"),
                 ([binding(), binding("gregtech:circuit.microprocessor", stack=item(metadata=4))], None, "different results")]
        for rows, requested, message in cases:
            with self.subTest(message=message):
                self.names(rows)
                if requested is None:
                    self.input_value.pop("item_name_queries", None)
                else:
                    self.input_value["item_name_queries"] = requested
                self.publish()
                with self.assertRaisesRegex((RecipeGraphProjectionError, RuntimeCaptureError), message):
                    self.project()
                self.assertFalse(self.output.exists())

    def test_malformed_pins_split_resolution_quantity_and_nbt_are_refused(self):
        mutations = [lambda r: r.update(resolver_class_sha256="0" * 64),
                     lambda r: r.update(gregtech_sha256="0" * 64),
                     lambda r: r.update(namespace="other"),
                     lambda r: r.update(authority="display-name"),
                     lambda r: r.update(resolution="unresolved"),
                     lambda r: r.update(stack=None),
                     lambda r: r["stack"].update(count=True),
                     lambda r: r["stack"].update(tag={"tag_id": 9, "value": []})]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                row = binding()
                mutate(row)
                self.names([row])
                self.publish()
                with self.assertRaises(RecipeGraphProjectionError):
                    self.project()
                self.assertFalse(self.output.exists())

    def test_requested_query_list_has_strict_shape(self):
        self.names([binding()])
        for value in (None, "circuit.microprocessor", ["circuit.microprocessor"] * 2, [True]):
            with self.subTest(value=value):
                self.input_value["item_name_queries"] = value
                self.publish()
                with self.assertRaises(RecipeGraphProjectionError):
                    self.project()
                self.assertFalse(self.output.exists())

    def test_qualified_aliases_and_original_single_character_namespace_rule(self):
        # Literal expected splits distinguish the original `i > 1` from `i > 0`.
        a, b = binding("a:tool"), binding("aa:tool", resolution="meta-tile-entity")
        self.assertEqual(("gregtech", "tool"), (a["namespace"], a["name"]))
        self.assertEqual(("aa", "tool"), (b["namespace"], b["name"]))
        self.assertEqual(2, len(validate_item_names([a, b])))
        a["namespace"] = "a"
        with self.assertRaisesRegex(ItemNameObservationError, "split"):
            validate_item_names([a])

    def test_category_checkpoint_and_tampering_use_original_capture_custody(self):
        self.names([binding()])
        self.publish(category_changes={"gt-item-names": {"checkpoint_id": "earlier"}})
        with self.assertRaises(RecipeGraphProjectionError):
            self.project()
        self.publish()
        payload = self.capture / "gt-item-names.json"
        payload.write_bytes(payload.read_bytes().replace(b"circuit.microprocessor", b"circuit.otherprocessor"))
        with self.assertRaises(RuntimeCaptureError):
            self.project()
        self.assertFalse(self.output.exists())

    def test_cancellation_during_nested_nbt_validation(self):
        calls = 0
        row = binding(stack=item(tag={"tag_id": 10, "value": {
            "numbers": {"tag_id": 11, "value": list(range(50))}}}))
        original = deepcopy(row)
        def cancel():
            nonlocal calls
            calls += 1
            if calls == 15:
                raise InterruptedError("during item name validation")
        with self.assertRaisesRegex(InterruptedError, "during"):
            validate_item_names([row], check_cancelled=cancel)
        self.assertEqual(original, row)


if __name__ == "__main__":
    unittest.main()
