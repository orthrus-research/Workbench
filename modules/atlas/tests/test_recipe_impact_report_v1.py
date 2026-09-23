from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/atlas/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_atlas_categorical_graph import (  # noqa: E402
    CategoricalGraphBundleBuilder,
    edge_record,
    node_record,
)
from workbench_atlas_recipe_health import (  # noqa: E402
    IMPACT_FORMAT,
    RecipeHealthError,
    open_recipe_health,
)


class RecipeImpactReportV1Tests(unittest.TestCase):
    def build_graph(self, root: Path) -> dict[str, dict]:
        recipe_map = node_record("gt-recipe-map", "mixer", {"name": "mixer"})
        machine = node_record(
            "gt-machine",
            "fixture:mixer.lv",
            {"registry_name": "fixture:mixer.lv", "tier": 2},
        )

        feed = node_record("forge-fluid", "feed", {"name": "feed"})
        cycle_output = node_record(
            "forge-fluid", "cycle_output", {"name": "cycle_output"}
        )
        cycle_feed = node_record(
            "forge-fluid", "cycle_feed", {"name": "cycle_feed"}
        )
        sole_output = node_record(
            "forge-fluid", "sole_output", {"name": "sole_output"}
        )
        downstream_output = node_record(
            "forge-fluid", "downstream_output", {"name": "downstream_output"}
        )
        optional_feed = node_record(
            "forge-fluid", "optional_feed", {"name": "optional_feed"}
        )

        selected = node_record(
            "gt-recipe",
            "mixer|selected|0",
            {
                "recipe_map": "mixer",
                "semantic_sha256": "1" * 64,
                "duplicate_ordinal": 0,
                "duration": 40,
                "eut": 30,
                "hidden": False,
                "lookup_active": True,
            },
        )
        alternative = node_record(
            "gt-recipe",
            "mixer|alternative|0",
            {
                "recipe_map": "mixer",
                "semantic_sha256": "2" * 64,
                "duplicate_ordinal": 0,
                "lookup_active": True,
                "category_present": True,
            },
        )
        cycle = node_record(
            "gt-recipe",
            "mixer|cycle|0",
            {
                "recipe_map": "mixer",
                "semantic_sha256": "3" * 64,
                "duplicate_ordinal": 0,
                "lookup_active": True,
                "category_present": True,
            },
        )
        downstream = node_record(
            "gt-recipe",
            "mixer|downstream|0",
            {
                "recipe_map": "mixer",
                "semantic_sha256": "4" * 64,
                "duplicate_ordinal": 0,
                "lookup_active": True,
                "category_present": True,
            },
        )
        optional = node_record(
            "gt-recipe",
            "mixer|optional|0",
            {
                "recipe_map": "mixer",
                "semantic_sha256": "5" * 64,
                "duplicate_ordinal": 0,
                "lookup_active": True,
                "category_present": True,
            },
        )

        selectors = {
            name: node_record(
                "gt-recipe-input-selector",
                f"fixture|{name}",
                {"ordinal": 0, "amount": 1000, "non_consumable": False},
            )
            for name in ("selected", "alternative", "cycle", "downstream", "optional")
        }

        quest = node_record(
            "betterquesting-quest",
            "100",
            {
                "quest_id": 100,
                "runtime_properties": {
                    "betterquesting": {"tasklogic": "AND", "questlogic": "AND"}
                },
            },
        )
        dependent_quest = node_record(
            "betterquesting-quest",
            "101",
            {
                "quest_id": 101,
                "runtime_properties": {
                    "betterquesting": {"tasklogic": "AND", "questlogic": "AND"}
                },
            },
        )
        task = node_record("betterquesting-task-occurrence", "100|0", {"task_id": 0})
        fluid_requirement = node_record(
            "betterquesting-fluid-requirement-occurrence",
            "100|0|0",
            {"ordinal": 0, "amount": 1000},
        )
        prerequisite = node_record(
            "betterquesting-prerequisite-occurrence",
            "101|0",
            {"ordinal": 0, "quest_id": 100},
        )

        nodes = [
            recipe_map,
            machine,
            feed,
            cycle_output,
            cycle_feed,
            sole_output,
            downstream_output,
            optional_feed,
            selected,
            alternative,
            cycle,
            downstream,
            optional,
            *selectors.values(),
            quest,
            dependent_quest,
            task,
            fluid_requirement,
            prerequisite,
        ]
        edges = [
            edge_record("contained-in-recipe-map", selected["id"], recipe_map["id"], {}),
            edge_record("uses-recipe-map", machine["id"], recipe_map["id"], {}),
            edge_record(
                "has-fluid-input-selector",
                selected["id"],
                selectors["selected"]["id"],
                {"ordinal": 0},
            ),
            edge_record(
                "accepts-gt-fluid-input",
                selectors["selected"]["id"],
                feed["id"],
                {"amount": 1000},
            ),
            edge_record(
                "produces-gt-fluid", selected["id"], cycle_output["id"], {"amount": 1000}
            ),
            edge_record(
                "produces-gt-fluid", selected["id"], sole_output["id"], {"amount": 1000}
            ),
            edge_record(
                "has-fluid-input-selector",
                alternative["id"],
                selectors["alternative"]["id"],
                {"ordinal": 0},
            ),
            edge_record(
                "accepts-gt-fluid-input",
                selectors["alternative"]["id"],
                cycle_feed["id"],
                {"amount": 1000},
            ),
            edge_record(
                "produces-gt-fluid",
                alternative["id"],
                cycle_output["id"],
                {"amount": 1000},
            ),
            edge_record(
                "has-fluid-input-selector",
                cycle["id"],
                selectors["cycle"]["id"],
                {"ordinal": 0},
            ),
            edge_record(
                "accepts-gt-fluid-input",
                selectors["cycle"]["id"],
                cycle_output["id"],
                {"amount": 1000},
            ),
            edge_record(
                "produces-gt-fluid", cycle["id"], cycle_feed["id"], {"amount": 1000}
            ),
            edge_record(
                "has-fluid-input-selector",
                downstream["id"],
                selectors["downstream"]["id"],
                {"ordinal": 0},
            ),
            edge_record(
                "accepts-gt-fluid-input",
                selectors["downstream"]["id"],
                sole_output["id"],
                {"amount": 1000},
            ),
            edge_record(
                "produces-gt-fluid",
                downstream["id"],
                downstream_output["id"],
                {"amount": 1000},
            ),
            edge_record(
                "has-fluid-input-selector",
                optional["id"],
                selectors["optional"]["id"],
                {"ordinal": 0},
            ),
            edge_record(
                "accepts-gt-fluid-input",
                selectors["optional"]["id"],
                downstream_output["id"],
                {"amount": 1000},
            ),
            edge_record(
                "accepts-gt-fluid-input",
                selectors["optional"]["id"],
                optional_feed["id"],
                {"amount": 1000},
                semantic_key="alternative",
            ),
            edge_record("owns-progression-task", quest["id"], task["id"], {}),
            edge_record(
                "has-progression-fluid-requirement",
                task["id"],
                fluid_requirement["id"],
                {"ordinal": 0},
            ),
            edge_record(
                "requires-progression-fluid",
                fluid_requirement["id"],
                downstream_output["id"],
                {},
            ),
            edge_record(
                "owns-progression-prerequisite",
                dependent_quest["id"],
                prerequisite["id"],
                {"ordinal": 0},
            ),
            edge_record(
                "targets-progression-prerequisite",
                prerequisite["id"],
                quest["id"],
                {},
            ),
        ]
        builder = CategoricalGraphBundleBuilder(
            root,
            scope={"profile": "recipe-impact-fixture"},
            evidence_binding={"capture_id": "recipe-impact-fixture"},
        )
        builder.add_partition(
            "recipe-impact",
            classification="bounded recipe impact fixture",
            dependencies=(),
            nodes=nodes,
            edges=edges,
            evidence_categories=("fixture",),
        )
        builder.close()
        return {
            "selected": selected,
            "cycle_output": cycle_output,
            "sole_output": sole_output,
            "downstream_output": downstream_output,
            "downstream": downstream,
            "optional": optional,
            "quest": quest,
            "dependent_quest": dependent_quest,
        }

    def test_report_exposes_cascade_cycle_machine_and_quest_signals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            fixture = self.build_graph(root)
            with open_recipe_health(root) as view:
                report = view.impact(
                    fixture["selected"]["id"], max_depth=4, max_nodes=100
                )
                repeated = view.impact(
                    fixture["selected"]["id"], max_depth=4, max_nodes=100
                )

            self.assertEqual(report, repeated)
            self.assertEqual(IMPACT_FORMAT, report["format"])
            self.assertEqual("remove-exact-observed-recipe", report["scenario"]["kind"])
            portfolios = {
                row["output"]["node"]["selection_id"]: row["producer_portfolio"]
                for row in report["direct"]["outputs"]
            }
            self.assertEqual(
                "observed-alternatives-present",
                portfolios[fixture["cycle_output"]["id"]]["status"],
            )
            self.assertEqual(
                "sole-observed-finite-producer",
                portfolios[fixture["sole_output"]["id"]]["status"],
            )

            risk_resources = {
                row["resource"]["selection_id"]
                for row in report["propagation"]["at_risk_resources"]
            }
            self.assertEqual(
                {fixture["sole_output"]["id"], fixture["downstream_output"]["id"]},
                risk_resources,
            )
            risk_recipes = {
                row["recipe"]["selection_id"]
                for row in report["propagation"]["at_risk_recipes"]
            }
            self.assertIn(fixture["downstream"]["id"], risk_recipes)
            self.assertNotIn(fixture["optional"]["id"], risk_recipes)
            self.assertGreaterEqual(
                len(report["propagation"]["alternative_dependency_cycle_signals"]),
                1,
            )

            energy = report["progression_signals"]["energy_and_machine_signals"]
            self.assertEqual(30, energy["observed_recipe_properties"]["eut"])
            self.assertEqual([2], energy["machine_numeric_tiers_observed"])
            quests = report["progression_signals"]["quest_signals"]
            self.assertEqual(1, len(quests["direct_resource_requirements"]))
            self.assertEqual(
                fixture["quest"]["id"],
                quests["direct_resource_requirements"][0]["quest"]["selection_id"],
            )
            self.assertEqual(1, len(quests["structural_prerequisite_dependents"]))
            self.assertEqual(
                fixture["dependent_quest"]["id"],
                quests["structural_prerequisite_dependents"][0]["quest"]["selection_id"],
            )
            self.assertIn(
                "progression-reachability-not-proven",
                {row["code"] for row in report["evidence_gaps"]},
            )

    def test_depth_frontier_and_invalid_selections_fail_honestly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            fixture = self.build_graph(root)
            with open_recipe_health(root) as view:
                report = view.impact(
                    fixture["selected"]["id"], max_depth=1, max_nodes=100
                )
                self.assertTrue(report["summary"]["truncated"])
                self.assertIn(
                    "depth-bound", {row["kind"] for row in report["frontiers"]}
                )
                with self.assertRaisesRegex(RecipeHealthError, "exact observed gt-recipe"):
                    view.impact(fixture["sole_output"]["id"])
                with self.assertRaisesRegex(RecipeHealthError, "outside 1..12"):
                    view.impact(fixture["selected"]["id"], max_depth=0)

    def test_source_checkout_cannot_make_runtime_impact_claims(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary) / "checkout"
            source = checkout / "groovy/Recipes.groovy"
            source.parent.mkdir(parents=True)
            source.write_text("recipeBuilder()\n", encoding="utf-8")
            with open_recipe_health(checkout) as view:
                with self.assertRaisesRegex(RecipeHealthError, "categorical graph"):
                    view.impact("source-occurrence:any:1:1")

    def test_category_only_recipe_is_not_a_viable_alternative_producer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            selected = node_record(
                "gt-recipe",
                "lookup-state|selected",
                {
                    "recipe_map": "mixer",
                    "semantic_sha256": "a" * 64,
                    "lookup_active": True,
                    "category_present": True,
                },
            )
            category_only = node_record(
                "gt-recipe",
                "lookup-state|category-only",
                {
                    "recipe_map": "mixer",
                    "semantic_sha256": "b" * 64,
                    "lookup_active": False,
                    "category_present": True,
                },
            )
            lookup_only = node_record(
                "gt-recipe",
                "lookup-state|lookup-only",
                {
                    "recipe_map": "mixer",
                    "semantic_sha256": "c" * 64,
                    "lookup_active": True,
                    "category_present": False,
                },
            )
            category_output = node_record("forge-fluid", "category_output", {})
            lookup_output = node_record("forge-fluid", "lookup_output", {})
            builder = CategoricalGraphBundleBuilder(
                root,
                scope={"profile": "producer-lookup-state"},
                evidence_binding={"capture_id": "producer-lookup-state"},
            )
            builder.add_partition(
                "producer-lookup-state",
                classification="producer lookup-state fixture",
                dependencies=(),
                nodes=(
                    selected,
                    category_only,
                    lookup_only,
                    category_output,
                    lookup_output,
                ),
                edges=(
                    edge_record(
                        "produces-gt-fluid",
                        selected["id"],
                        category_output["id"],
                        {},
                    ),
                    edge_record(
                        "produces-gt-fluid",
                        selected["id"],
                        lookup_output["id"],
                        {},
                    ),
                    edge_record(
                        "produces-gt-fluid",
                        category_only["id"],
                        category_output["id"],
                        {},
                    ),
                    edge_record(
                        "produces-gt-fluid",
                        lookup_only["id"],
                        lookup_output["id"],
                        {},
                    ),
                ),
                evidence_categories=("fixture",),
            )
            builder.close()

            with open_recipe_health(root) as view:
                report = view.impact(selected["id"], max_nodes=100)

        portfolios = {
            row["output"]["node"]["selection_id"]: row["producer_portfolio"]
            for row in report["direct"]["outputs"]
        }
        self.assertEqual(
            "sole-observed-finite-producer",
            portfolios[category_output["id"]]["status"],
        )
        self.assertEqual(
            [], portfolios[category_output["id"]]["alternative_producers"]
        )
        self.assertEqual(
            "observed-alternatives-present",
            portfolios[lookup_output["id"]]["status"],
        )
        self.assertEqual(
            [lookup_only["id"]],
            [
                row["recipe"]["selection_id"]
                for row in portfolios[lookup_output["id"]]["alternative_producers"]
            ],
        )
        risk_resources = {
            row["resource"]["selection_id"]
            for row in report["propagation"]["at_risk_resources"]
        }
        self.assertIn(category_output["id"], risk_resources)
        self.assertNotIn(lookup_output["id"], risk_resources)
        self.assertNotIn(
            category_only["id"],
            {
                producer_id
                for row in report["propagation"]["at_risk_resources"]
                for producer_id in row["all_observed_finite_producers"]
            },
        )
        self.assertNotIn(
            category_only["id"],
            {row.get("subject_id") for row in report["unknowns"]},
        )

    def test_missing_producer_lookup_state_prevents_risk_classification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            selected = node_record(
                "gt-recipe",
                "unknown-lookup-state|selected",
                {
                    "recipe_map": "mixer",
                    "semantic_sha256": "d" * 64,
                    "lookup_active": True,
                    "category_present": True,
                },
            )
            unresolved = node_record(
                "gt-recipe",
                "unknown-lookup-state|alternative",
                {
                    "recipe_map": "mixer",
                    "semantic_sha256": "e" * 64,
                    "category_present": True,
                },
            )
            output = node_record("forge-fluid", "unknown_lookup_output", {})
            builder = CategoricalGraphBundleBuilder(
                root,
                scope={"profile": "unknown-producer-lookup-state"},
                evidence_binding={"capture_id": "unknown-producer-lookup-state"},
            )
            builder.add_partition(
                "unknown-producer-lookup-state",
                classification="unknown producer lookup-state fixture",
                dependencies=(),
                nodes=(selected, unresolved, output),
                edges=(
                    edge_record(
                        "produces-gt-fluid", selected["id"], output["id"], {}
                    ),
                    edge_record(
                        "produces-gt-fluid", unresolved["id"], output["id"], {}
                    ),
                ),
                evidence_categories=("fixture",),
            )
            builder.close()

            with open_recipe_health(root) as view:
                report = view.impact(selected["id"], max_nodes=100)

        portfolio = report["direct"]["outputs"][0]["producer_portfolio"]
        self.assertEqual("producer-set-truncated", portfolio["status"])
        self.assertEqual([], portfolio["alternative_producers"])
        self.assertEqual([], report["propagation"]["at_risk_resources"])
        self.assertIn(
            (
                "producer-lookup-state-unavailable",
                unresolved["id"],
            ),
            {
                (row["code"], row.get("subject_id"))
                for row in report["unknowns"]
            },
        )

    def test_direct_io_obeys_the_declared_node_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            selected = node_record(
                "gt-recipe",
                "bounded-direct-io",
                {
                    "recipe_map": "mixer",
                    "semantic_sha256": "9" * 64,
                    "lookup_active": True,
                },
            )
            nodes = [selected]
            edges = []
            for ordinal in range(20):
                selector = node_record(
                    "gt-recipe-input-selector",
                    f"bounded-direct-io|{ordinal}",
                    {"ordinal": ordinal},
                )
                resource = node_record(
                    "forge-fluid", f"bounded_input_{ordinal}", {}
                )
                nodes.extend((selector, resource))
                edges.extend(
                    (
                        edge_record(
                            "has-fluid-input-selector",
                            selected["id"],
                            selector["id"],
                            {"ordinal": ordinal},
                        ),
                        edge_record(
                            "accepts-gt-fluid-input",
                            selector["id"],
                            resource["id"],
                            {"amount": 1},
                        ),
                    )
                )
            builder = CategoricalGraphBundleBuilder(
                root,
                scope={"profile": "bounded-direct-io"},
                evidence_binding={"capture_id": "bounded-direct-io"},
            )
            builder.add_partition(
                "bounded-direct-io",
                classification="direct I/O bound fixture",
                dependencies=(),
                nodes=nodes,
                edges=edges,
                evidence_categories=("fixture",),
            )
            builder.close()

            with open_recipe_health(root) as view:
                report = view.impact(selected["id"], max_nodes=10)

        self.assertEqual(10, report["bounds"]["visited_node_count"])
        self.assertLess(len(report["direct"]["inputs"]), 20)
        self.assertTrue(report["summary"]["truncated"])
        self.assertIn(
            "direct-io", {row.get("phase") for row in report["frontiers"]}
        )
        self.assertIn(
            "direct-io-truncated", {row["code"] for row in report["unknowns"]}
        )

    def test_cycle_scan_canonicalizes_convergent_paths(self) -> None:
        branch = 3
        depth = 7
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            selected = node_record(
                "gt-recipe",
                "cycle-root",
                {
                    "recipe_map": "mixer",
                    "semantic_sha256": "8" * 64,
                    "lookup_active": True,
                },
            )
            resources = [
                node_record("forge-fluid", f"cycle_resource_{index}", {})
                for index in range(depth)
            ]
            nodes = [selected, *resources]
            edges = [
                edge_record(
                    "produces-gt-fluid", selected["id"], resources[0]["id"], {}
                )
            ]
            for layer in range(depth):
                for ordinal in range(branch):
                    recipe = node_record(
                        "gt-recipe",
                        f"cycle-alternative-{layer}-{ordinal}",
                        {
                            "recipe_map": "mixer",
                            "semantic_sha256": f"{layer * branch + ordinal + 10:064x}",
                            "lookup_active": True,
                        },
                    )
                    selector = node_record(
                        "gt-recipe-input-selector",
                        f"cycle-selector-{layer}-{ordinal}",
                        {"ordinal": 0},
                    )
                    target = (
                        resources[0]
                        if layer == depth - 1
                        else resources[layer + 1]
                    )
                    nodes.extend((recipe, selector))
                    edges.extend(
                        (
                            edge_record(
                                "produces-gt-fluid",
                                recipe["id"],
                                resources[layer]["id"],
                                {},
                            ),
                            edge_record(
                                "has-fluid-input-selector",
                                recipe["id"],
                                selector["id"],
                                {"ordinal": 0},
                            ),
                            edge_record(
                                "accepts-gt-fluid-input",
                                selector["id"],
                                target["id"],
                                {},
                            ),
                        )
                    )
            builder = CategoricalGraphBundleBuilder(
                root,
                scope={"profile": "convergent-cycle"},
                evidence_binding={"capture_id": "convergent-cycle"},
            )
            builder.add_partition(
                "convergent-cycle",
                classification="convergent alternative cycle fixture",
                dependencies=(),
                nodes=nodes,
                edges=edges,
                evidence_categories=("fixture",),
            )
            builder.close()

            with open_recipe_health(root) as view:
                report = view.impact(
                    selected["id"], max_depth=depth, max_nodes=100
                )

        signals = report["propagation"]["alternative_dependency_cycle_signals"]
        self.assertEqual(branch, len(signals))
        self.assertIn(
            "canonical-cycle-state",
            {row.get("relation") for row in report["frontiers"]},
        )
        self.assertTrue(report["summary"]["truncated"])


if __name__ == "__main__":
    unittest.main()
