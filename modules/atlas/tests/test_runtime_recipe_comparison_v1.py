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
    RUNTIME_COMPARISON_FORMAT,
    compare_runtime_recipe_graphs,
    open_recipe_health,
)


class RuntimeRecipeComparisonV1Tests(unittest.TestCase):
    scope = {
        "pack_profile_id": "workbench-pack:supersymmetry",
        "platform_profile_id": "workbench-platform:cleanroom:provisional",
        "platform_candidate": "fixture-cleanroom",
        "physical_side": "dedicated_server",
        "projection_profile": "fixture-runtime-comparison-v1",
    }

    @staticmethod
    def recipe(
        name: str,
        digit: str,
        *,
        duration: int = 20,
        lookup_active: bool = True,
    ) -> dict:
        digest = digit * 64
        return node_record(
            "gt-recipe",
            f"mixer|{digest}|0",
            {
                "recipe_map": "mixer",
                "semantic_sha256": digest,
                "duplicate_ordinal": 0,
                "duration": duration,
                "eut": 30,
                "lookup_active": lookup_active,
                "fixture_name": name,
            },
        )

    @staticmethod
    def selector(recipe: dict, ordinal: int = 0) -> dict:
        return node_record(
            "gt-recipe-input-selector",
            f"{recipe['semantic_key']}|fluid|{ordinal}",
            {"ordinal": ordinal},
        )

    def build_graph(
        self,
        root: Path,
        *,
        state: str,
        adapter_profile: str = "a" * 64,
        same_signature_duration: int = 20,
        same_signature_lookup_active: bool = True,
        same_signature_evidence: tuple[dict[str, object], ...] = (),
    ) -> dict[str, dict]:
        feed = node_record("forge-fluid", "feed", {"name": "feed"})
        exposed = node_record("forge-fluid", "exposed", {"name": "exposed"})
        replacement = node_record(
            "forge-fluid", "replacement", {"name": "replacement"}
        )
        downstream = node_record(
            "forge-fluid", "downstream", {"name": "downstream"}
        )

        old = self.recipe("old", "1")
        consumer = self.recipe(
            "consumer",
            "2",
            duration=same_signature_duration,
            lookup_active=same_signature_lookup_active,
        )
        consumer["evidence"] = [dict(row) for row in same_signature_evidence]
        new = self.recipe("new", "3")
        cycle = self.recipe("cycle", "4")
        old_selector = self.selector(old)
        consumer_selector = self.selector(consumer)
        new_selector = self.selector(new)
        cycle_selector = self.selector(cycle)

        quest = node_record("betterquesting-quest", "100", {"quest_id": 100})
        dependent = node_record(
            "betterquesting-quest", "101", {"quest_id": 101}
        )
        task = node_record(
            "betterquesting-task-occurrence", "100|0", {"task_id": 0}
        )
        requirement = node_record(
            "betterquesting-fluid-requirement-occurrence",
            "100|0|0",
            {"ordinal": 0},
        )
        prerequisite = node_record(
            "betterquesting-prerequisite-occurrence",
            "101|0",
            {"ordinal": 0},
        )

        nodes = [
            feed,
            exposed,
            replacement,
            downstream,
            consumer,
            consumer_selector,
            quest,
            dependent,
            task,
            requirement,
            prerequisite,
        ]
        edges = [
            edge_record(
                "has-fluid-input-selector",
                consumer["id"],
                consumer_selector["id"],
                {"ordinal": 0},
            ),
            edge_record(
                "accepts-gt-fluid-input",
                consumer_selector["id"],
                exposed["id"],
                {"amount": 1000},
            ),
            edge_record(
                "produces-gt-fluid",
                consumer["id"],
                downstream["id"],
                {"amount": 1000},
            ),
            edge_record("owns-progression-task", quest["id"], task["id"], {}),
            edge_record(
                "has-progression-fluid-requirement",
                task["id"],
                requirement["id"],
                {},
            ),
            edge_record(
                "requires-progression-fluid",
                requirement["id"],
                exposed["id"],
                {"amount": 1000},
            ),
            edge_record(
                "owns-progression-prerequisite",
                dependent["id"],
                prerequisite["id"],
                {},
            ),
            edge_record(
                "targets-progression-prerequisite",
                prerequisite["id"],
                quest["id"],
                {},
            ),
        ]
        if state == "before":
            nodes.extend((old, old_selector))
            edges.extend(
                (
                    edge_record(
                        "has-fluid-input-selector",
                        old["id"],
                        old_selector["id"],
                        {"ordinal": 0},
                    ),
                    edge_record(
                        "accepts-gt-fluid-input",
                        old_selector["id"],
                        feed["id"],
                        {"amount": 1000},
                    ),
                    edge_record(
                        "produces-gt-fluid",
                        old["id"],
                        exposed["id"],
                        {"amount": 1000},
                    ),
                )
            )
        elif state == "after":
            nodes.extend((new, new_selector, cycle, cycle_selector))
            edges.extend(
                (
                    edge_record(
                        "has-fluid-input-selector",
                        new["id"],
                        new_selector["id"],
                        {"ordinal": 0},
                    ),
                    edge_record(
                        "accepts-gt-fluid-input",
                        new_selector["id"],
                        feed["id"],
                        {"amount": 1000},
                    ),
                    edge_record(
                        "produces-gt-fluid",
                        new["id"],
                        replacement["id"],
                        {"amount": 1000},
                    ),
                    edge_record(
                        "has-fluid-input-selector",
                        cycle["id"],
                        cycle_selector["id"],
                        {"ordinal": 0},
                    ),
                    edge_record(
                        "accepts-gt-fluid-input",
                        cycle_selector["id"],
                        replacement["id"],
                        {"amount": 1000},
                    ),
                    edge_record(
                        "produces-gt-fluid",
                        cycle["id"],
                        replacement["id"],
                        {"amount": 1000},
                    ),
                )
            )
        else:
            raise AssertionError(state)

        builder = CategoricalGraphBundleBuilder(
            root,
            scope=self.scope,
            evidence_binding={
                "capture_id": f"fixture-{state}",
                "adapter_profile_sha256": adapter_profile,
                "input_manifest_sha256": ("b" if state == "before" else "c")
                * 64,
                "category_results": {
                    "gt-recipes": {
                        "category_id": "transformation-recipe",
                        "checkpoint_id": "post-start-end-tick",
                        "record_count": sum(
                            node["kind"] == "gt-recipe" for node in nodes
                        ),
                        "records_sha256": ("d" if state == "before" else "e")
                        * 64,
                        "result_sha256": ("e" if state == "before" else "f")
                        * 64,
                    }
                },
            },
        )
        builder.add_partition(
            "runtime-comparison-fixture",
            classification="runtime recipe comparison fixture",
            dependencies=(),
            nodes=nodes,
            edges=edges,
            evidence_categories=(
                "progression-definition",
                "transformation-recipe",
            ),
        )
        builder.close()
        return {
            "old": old,
            "new": new,
            "cycle": cycle,
            "consumer": consumer,
            "exposed": exposed,
            "downstream": downstream,
            "quest": quest,
            "dependent": dependent,
        }

    def test_observed_delta_remains_unpaired_and_exposes_bounded_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before_root = root / "before"
            after_root = root / "after"
            fixture = self.build_graph(before_root, state="before")
            self.build_graph(after_root, state="after")
            with open_recipe_health(before_root) as before, open_recipe_health(
                after_root
            ) as after:
                report = compare_runtime_recipe_graphs(
                    before, after, max_depth=4, max_nodes=200
                )

        self.assertEqual(RUNTIME_COMPARISON_FORMAT, report["format"])
        self.assertEqual("compatible", report["compatibility"]["state"])
        signatures = report["recipe_signatures"]
        self.assertEqual(1, signatures["removed_exact_signature_count"])
        self.assertEqual(2, signatures["added_exact_signature_count"])
        self.assertEqual(
            "unavailable-no-cross-capture-occurrence-identity",
            signatures["change_correspondence"]["status"],
        )
        self.assertEqual([], signatures["change_correspondence"]["changed_recipe_pairs"])
        self.assertEqual(0, report["summary"]["changed_recipe_pair_count"])

        exposed_ids = set(
            report["resource_flow_deltas"][
                "newly_without_observed_finite_producers"
            ]
        )
        self.assertIn(fixture["exposed"]["id"], exposed_ids)
        propagated = {
            row["resource"]["selection_id"]
            for row in report["propagation"]["at_risk_resources"]
        }
        self.assertIn(fixture["downstream"]["id"], propagated)
        at_risk_recipes = {
            row["recipe"]["selection_id"]
            for row in report["propagation"]["at_risk_recipes"]
        }
        self.assertIn(fixture["consumer"]["id"], at_risk_recipes)

        progression = report["progression_signals"]
        self.assertEqual(1, len(progression["exposed_requirements_after"]))
        self.assertEqual(
            fixture["quest"]["id"],
            progression["exposed_requirements_after"][0]["identity"]["quest_id"],
        )
        dependent_ids = {
            row["quest"]["selection_id"]
            for row in progression["structural_prerequisite_dependents_after"]
        }
        self.assertIn(fixture["dependent"]["id"], dependent_ids)
        introduced_cycles = report["cycle_signals"][
            "introduced_with_added_recipes"
        ]
        self.assertTrue(
            any(row["recipe_id"] == fixture["cycle"]["id"] for row in introduced_cycles)
        )
        self.assertIn(
            "player-reachability-not-assessed",
            {row["code"] for row in report["evidence_gaps"]},
        )

    def test_adapter_protocol_mismatch_is_incomparable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before_root = root / "before"
            after_root = root / "after"
            self.build_graph(before_root, state="before", adapter_profile="a" * 64)
            self.build_graph(after_root, state="after", adapter_profile="9" * 64)
            with open_recipe_health(before_root) as before, open_recipe_health(
                after_root
            ) as after:
                report = compare_runtime_recipe_graphs(before, after)

        self.assertEqual("incomparable", report["compatibility"]["state"])
        self.assertEqual("not-compared", report["recipe_signatures"]["status"])
        self.assertEqual([], report["resource_flow_deltas"]["deltas"])
        self.assertIn(
            "adapter-protocol-mismatch",
            {row["code"] for row in report["unknowns"]},
        )

    def test_recipe_scan_bound_prevents_partial_membership_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before_root = root / "before"
            after_root = root / "after"
            self.build_graph(before_root, state="before")
            self.build_graph(after_root, state="after")
            with open_recipe_health(before_root) as before, open_recipe_health(
                after_root
            ) as after:
                report = compare_runtime_recipe_graphs(
                    before, after, max_recipes=1
                )

        self.assertEqual("not-compared", report["recipe_signatures"]["status"])
        self.assertEqual("not-compared", report["summary"]["comparison_state"])
        self.assertIn(
            "recipe-scan-bound", {row["kind"] for row in report["frontiers"]}
        )

    def test_graph_traversal_obeys_the_global_node_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before_root = root / "before"
            after_root = root / "after"
            self.build_graph(before_root, state="before")
            self.build_graph(after_root, state="after")
            with open_recipe_health(before_root) as before, open_recipe_health(
                after_root
            ) as after:
                report = compare_runtime_recipe_graphs(
                    before, after, max_nodes=10
                )

        self.assertLessEqual(report["bounds"]["visited_node_count"], 10)
        self.assertTrue(report["summary"]["truncated"])
        self.assertIn("node-bound", {row["kind"] for row in report["frontiers"]})

    def test_cycle_status_includes_frontiers_reached_during_cycle_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before_root = root / "before"
            after_root = root / "after"
            self.build_graph(before_root, state="before")
            self.build_graph(after_root, state="after")
            with open_recipe_health(before_root) as before, open_recipe_health(
                after_root
            ) as after:
                report = compare_runtime_recipe_graphs(
                    before, after, max_nodes=18
                )

        self.assertIn(
            "introduced-cycle-scan",
            {row.get("phase") for row in report["frontiers"]},
        )
        self.assertEqual("truncated", report["cycle_signals"]["status"])

    def test_same_signature_activation_delta_changes_the_resource_portfolio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before_root = root / "before"
            after_root = root / "after"
            self.build_graph(
                before_root,
                state="before",
                same_signature_lookup_active=True,
            )
            fixture = self.build_graph(
                after_root,
                state="before",
                same_signature_lookup_active=False,
            )
            with open_recipe_health(before_root) as before, open_recipe_health(
                after_root
            ) as after:
                report = compare_runtime_recipe_graphs(before, after)

        signatures = report["recipe_signatures"]
        self.assertEqual(0, signatures["removed_exact_signature_count"])
        self.assertEqual(0, signatures["added_exact_signature_count"])
        self.assertEqual(1, signatures["same_signature_observation_delta_count"])
        self.assertEqual(
            "not-needed", signatures["change_correspondence"]["status"]
        )
        self.assertEqual(0, report["summary"]["changed_recipe_pair_count"])
        self.assertIn(
            fixture["downstream"]["id"],
            report["resource_flow_deltas"][
                "newly_without_observed_finite_producers"
            ],
        )

    def test_same_signature_evidence_owner_change_is_observed(self) -> None:
        before_evidence = ({"source_owner": "groovy/postInit/Before.groovy"},)
        after_evidence = ({"source_owner": "groovy/postInit/After.groovy"},)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before_root = root / "before"
            after_root = root / "after"
            self.build_graph(
                before_root,
                state="before",
                same_signature_evidence=before_evidence,
            )
            self.build_graph(
                after_root,
                state="before",
                same_signature_evidence=after_evidence,
            )
            with open_recipe_health(before_root) as before, open_recipe_health(
                after_root
            ) as after:
                report = compare_runtime_recipe_graphs(before, after)

        signatures = report["recipe_signatures"]
        self.assertEqual(0, signatures["removed_exact_signature_count"])
        self.assertEqual(0, signatures["added_exact_signature_count"])
        self.assertEqual(1, signatures["same_signature_observation_delta_count"])
        self.assertEqual(
            {
                "evidence": {
                    "before": list(before_evidence),
                    "after": list(after_evidence),
                }
            },
            signatures["same_signature_observation_deltas"][0]["changed_fields"],
        )


if __name__ == "__main__":
    unittest.main()
