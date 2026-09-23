"""Public command tests for the Atlas recipe-health view."""

from __future__ import annotations

from io import StringIO
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
for source in sorted((ROOT / "modules").glob("*/src")):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_atlas_recipe_health import cli as atlas_recipe_cli  # noqa: E402
from workbench_api import ExecutionContext


class AtlasRecipeCliTests(unittest.TestCase):
    def _checkout(self, root: Path) -> Path:
        checkout = root / "supersymmetry"
        source = checkout / "groovy/postInit/chemistry/Probe.groovy"
        source.parent.mkdir(parents=True)
        source.write_text(
            "MIXER.recipeBuilder()\n"
            "    .fluidInputs(fluid('water') * 1000)\n"
            "    .buildAndRegister()\n",
            encoding="utf-8",
        )
        return checkout

    def _main(self, *arguments: str, **kwargs) -> tuple[int, str, str]:
        output = StringIO()
        error = StringIO()
        status = atlas_recipe_cli.main(arguments, output=output, error=error, **kwargs)
        return status, output.getvalue(), error.getvalue()

    def test_context_search_and_inspect_source_checkout_as_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = self._checkout(Path(temporary))

            status, output, error = self._main(
                "context", str(checkout), "--json"
            )
            self.assertEqual(status, 0, error)
            context = json.loads(output)
            self.assertEqual(context["context_type"], "source-only-checkout")
            self.assertEqual(context["source_file_count"], 1)
            self.assertFalse(context["capabilities"]["recipe_search"])

            status, output, error = self._main(
                "search", str(checkout), "water", "--json"
            )
            self.assertEqual(status, 0, error)
            search = json.loads(output)
            self.assertEqual(len(search["results"]), 1)
            self.assertEqual(
                search["evidence_gaps"][0]["code"],
                "runtime-recipes-unavailable",
            )

            selection_id = search["results"][0]["selection_id"]
            status, output, error = self._main(
                "inspect", str(checkout), selection_id, "--json"
            )
            self.assertEqual(status, 0, error)
            report = json.loads(output)
            self.assertEqual(report["role"], "source-occurrence")
            self.assertEqual(report["selection"]["selection_id"], selection_id)
            self.assertEqual(report["ownership"]["status"], "source-location-only")
            self.assertIn(
                "reachability-not-assessed",
                {gap["code"] for gap in report["evidence_gaps"]},
            )

    def test_human_output_keeps_source_only_evidence_gaps_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = self._checkout(Path(temporary))
            status, search_output, error = self._main(
                "search", str(checkout), "water"
            )
            self.assertEqual(status, 0, error)
            self.assertRegex(search_output, r"source-text:[0-9a-f]{64}:[^\s]+:[0-9]+:[0-9]+")
            self.assertNotIn("source-occurrence:", search_output)
            self.assertIn("Evidence gaps:", search_output)
            self.assertIn("runtime-recipes-unavailable", search_output)

            with atlas_recipe_cli.open_recipe_health(checkout) as view:
                selection_id = view.search("water")["results"][0]["selection_id"]
            status, inspect_output, error = self._main(
                "inspect", str(checkout), selection_id
            )
            self.assertEqual(status, 0, error)
            self.assertIn("Ownership: source-location-only", inspect_output)
            self.assertIn("reachability-not-assessed", inspect_output)

    def test_context_discovers_without_opening_the_full_graph(self) -> None:
        context = {"context_type": "categorical-graph-v2", "root": "/fixture"}
        with mock.patch.object(
            atlas_recipe_cli,
            "discover_recipe_health_operational_context",
            return_value=context,
        ) as discovered, mock.patch.object(
            atlas_recipe_cli,
            "open_recipe_health",
        ) as opened:
            status, output, error = self._main("context", "/fixture", "--json")
        self.assertEqual(status, 0, error)
        self.assertEqual(context, json.loads(output))
        discovered.assert_called_once_with(Path("/fixture"))
        opened.assert_not_called()

    def test_missing_index_context_human_output_denies_search_and_names_repair(self) -> None:
        rendered = atlas_recipe_cli._render_context(
            {
                "context_type": "categorical-graph-v2",
                "root": "/graph",
                "graph_set_id": "workbench-atlas-graph-set-v2:sha256:" + "1" * 64,
                "recipe_count": 12,
                "capabilities": {"recipe_search": False},
                "evidence_capabilities": {
                    "recipe_search": True,
                    "reachability": False,
                },
                "query_index": {
                    "state": "missing",
                    "reason_code": "query-index-descriptor-absent",
                    "reason": "The graph manifest has no derived query-index descriptor.",
                },
                "repair": {
                    "argv": ["workbench", "atlas", "recipes", "index", "/graph"]
                },
            }
        )

        self.assertIn("Search executable now: False", rendered)
        self.assertIn("query-index-descriptor-absent", rendered)
        self.assertIn("workbench atlas recipes index /graph", rendered)
        self.assertIn("graph evidence is unchanged", rendered)

    def test_index_delegates_bounds_and_reports_progress_to_stderr(self) -> None:
        record = {
            "format": "workbench-atlas-recipe-index-operation-v1",
            "state": "complete",
            "graph_set_id": "workbench-atlas-graph-set-v2:sha256:" + "1" * 64,
            "operation_id": "workbench-atlas-recipe-index-operation:sha256:" + "2" * 64,
            "bounds": {
                "source_record_count": 3,
                "source_stream_bytes": 100,
                "free_bytes_before": 1_000_000,
                "required_free_bytes": 8192,
            },
            "custody": {
                "published_query_index": {"size": 4096, "sha256": "3" * 64}
            },
        }

        def rebuild(path, *, max_source_bytes, max_index_bytes, progress):
            self.assertEqual(Path("/graph"), path)
            self.assertEqual(2000, max_source_bytes)
            self.assertEqual(4096, max_index_bytes)
            progress(
                {
                    "phase": "validated",
                    "records_completed": 0,
                    "records_total": 3,
                }
            )
            progress(
                {
                    "phase": "published",
                    "records_completed": 3,
                    "records_total": 3,
                }
            )
            return record

        with mock.patch.object(
            atlas_recipe_cli, "rebuild_recipe_health_index", side_effect=rebuild
        ) as rebuilt:
            status, output, error = self._main(
                "index",
                "/graph",
                "--max-source-bytes",
                "2000",
                "--max-index-bytes",
                "4096",
                "--json",
            )

        self.assertEqual(0, status, error)
        self.assertEqual(record, json.loads(output))
        self.assertIn("validated 0%", error)
        self.assertIn("published 100%", error)
        rebuilt.assert_called_once()

    def test_query_actions_use_one_opened_view(self) -> None:
        class FakeView:
            def __init__(self) -> None:
                self.entered = 0
                self.exited = 0

            def __enter__(self):
                self.entered += 1
                return self

            def __exit__(self, *_: object) -> None:
                self.exited += 1

            def describe(self):
                return {"context_type": "source-only-checkout", "root": "/fixture"}

            def search(self, query: str, *, limit: int):
                return {"query": query, "results": [], "truncated": False}

            def inspect(self, selection_id: str):
                return {
                    "role": "source-occurrence",
                    "selection": {"selection_id": selection_id},
                    "evidence_gaps": [],
                }

            def impact(self, selection_id: str, *, max_depth: int, max_nodes: int):
                return {
                    "format": "workbench-atlas-recipe-impact-report-v1",
                    "selection": {"selection_id": selection_id},
                    "summary": {
                        "at_risk_resource_candidate_count": 2,
                        "at_risk_recipe_candidate_count": 1,
                        "quest_requirement_exposure_count": 1,
                        "structural_quest_dependent_count": 0,
                        "truncated": False,
                    },
                    "bounds": {
                        "max_depth": max_depth,
                        "max_nodes": max_nodes,
                        "visited_node_count": 7,
                    },
                    "evidence_gaps": [
                        {
                            "code": "progression-reachability-not-proven",
                            "message": "candidate exposure is not proof",
                        }
                    ],
                }

        for arguments in (
            ("search", "/fixture", "water"),
            ("inspect", "/fixture", "source-occurrence:probe:1:1"),
            ("impact", "/fixture", "gt-recipe:probe", "--max-depth", "3", "--max-nodes", "100"),
        ):
            with self.subTest(arguments=arguments):
                view = FakeView()
                with mock.patch.object(
                    atlas_recipe_cli,
                    "open_recipe_health",
                    return_value=view,
                ) as opened:
                    status, _output, error = self._main(*arguments)
                self.assertEqual(status, 0, error)
                opened.assert_called_once_with(Path("/fixture"))
                self.assertEqual((view.entered, view.exited), (1, 1))

    def test_invalid_context_is_a_bounded_command_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            status, output, error = self._main("context", temporary, "--json")
        self.assertEqual(status, 2)
        self.assertEqual(output, "")
        self.assertIn("Atlas recipes failed:", error)
        self.assertIn("no Groovy or ZenScript", error)

    def test_assess_plan_without_adapter_is_bounded_and_actionable(self) -> None:
        status, output, error = self._main("assess-plan", "/graph", "plan", "--json")
        self.assertEqual(2, status)
        self.assertEqual("", output)
        self.assertIn("requires an enabled plan-assessment adapter", error)

    def test_assess_plan_only_invokes_explicit_compatible_adapter(self) -> None:
        from atlas_recipe_assessment_fixture import assessment_fixture
        report = assessment_fixture()
        assess = mock.Mock(return_value=report)
        adapter = atlas_recipe_cli.RecipePlanAssessmentAdapter(assess)
        status, output, error = self._main(
            "assess-plan", "/graph", "plan", "--state-root", "/state",
            "--max-depth", "3", "--max-nodes", "100", "--json",
            plan_adapter=adapter,
        )
        self.assertEqual(0, status, error)
        self.assertEqual(report, json.loads(output))
        assess.assert_called_once_with(
            Path("/graph"), "plan", state_root=Path("/state"), max_depth=3, max_nodes=100
        )

    def test_plan_adapter_rejects_missing_malformed_and_overclaiming_reports(self) -> None:
        from atlas_recipe_assessment_fixture import assessment_fixture
        report = assessment_fixture()
        malformed = [{"format": report["format"]}]
        for key in report:
            changed = deepcopy(report)
            del changed[key]
            malformed.append(changed)
        for key, replacement in (("schema_version", True), ("summary", []), ("frontiers", {}), ("proposal", {})):
            changed = deepcopy(report)
            changed[key] = replacement
            malformed.append(changed)
        for section, key, replacement in (
            ("summary", "exact_observed_resource_key_count", True),
            ("summary", "truncated", "false"),
            ("scenario", "proposed_source_bytes_observed_runtime", True),
            ("analysis_model", "claim_boundary", "player reachability proved"),
        ):
            changed = deepcopy(report)
            changed[section][key] = replacement
            malformed.append(changed)
        for index, record in enumerate(malformed):
            with self.subTest(case=index):
                adapter = atlas_recipe_cli.RecipePlanAssessmentAdapter(mock.Mock(return_value=record))
                status, output, error = self._main("assess-plan", "/graph", "plan", "--json", plan_adapter=adapter)
                self.assertEqual(2, status)
                self.assertEqual("", output)
                self.assertIn("invalid assessment", error)

    def test_incompatible_or_failing_plan_adapters_are_bounded(self) -> None:
        assess = mock.Mock()
        incompatible = atlas_recipe_cli.RecipePlanAssessmentAdapter(assess, api_version=2)
        failed = atlas_recipe_cli.RecipePlanAssessmentAdapter(
            mock.Mock(side_effect=RuntimeError("owner validation failed"))
        )
        invalid = atlas_recipe_cli.RecipePlanAssessmentAdapter(mock.Mock(return_value={}))
        for adapter, reason in (
            (incompatible, "incompatible API"),
            (failed, "owner validation failed"),
            (invalid, "invalid assessment"),
        ):
            with self.subTest(reason=reason):
                status, output, error = self._main(
                    "assess-plan", "/graph", "plan", "--json", plan_adapter=adapter
                )
                self.assertEqual(2, status)
                self.assertEqual("", output)
                self.assertIn(reason, error)
        assess.assert_not_called()

    def test_cancelled_command_does_not_open_evidence(self) -> None:
        context = ExecutionContext(ROOT, ROOT / ".workbench")
        context.cancelled.set()
        with mock.patch.object(atlas_recipe_cli, "open_recipe_health") as opened:
            status, output, error = self._main(
                "search", "/graph", "water", context=context
            )
        self.assertEqual(2, status)
        self.assertEqual("", output)
        self.assertIn("operation cancelled", error)
        opened.assert_not_called()

    def test_cancellation_before_output_does_not_emit_success(self) -> None:
        context = ExecutionContext(ROOT, ROOT / ".workbench")

        def discover(path):
            context.cancelled.set()
            return {"context_type": "categorical-graph-v2", "root": str(path)}

        with mock.patch.object(
            atlas_recipe_cli, "discover_recipe_health_operational_context", side_effect=discover
        ):
            status, output, error = self._main("context", "/graph", context=context)
        self.assertEqual(2, status)
        self.assertEqual("", output)
        self.assertIn("operation cancelled", error)

    def test_plan_assessment_human_output_keeps_uncertainty_prominent(self) -> None:
        rendered = atlas_recipe_cli._render_plan_assessment(
            {
                "proposal": {"proposal_id": "workbench-plan:sha256:" + "1" * 64},
                "summary": {
                    "exact_observed_resource_key_count": 3,
                    "unresolved_or_ambiguous_resource_count": 1,
                    "structural_collision_candidate_count": 2,
                    "output_input_cycle_candidate_count": 4,
                    "output_with_existing_producer_count": 1,
                    "output_with_existing_consumer_count": 1,
                    "quest_output_reference_count": 0,
                    "truncated": True,
                },
                "frontiers": [{"kind": "relation-bound"}],
                "unknowns": [{"code": "capture-freshness-unbound"}],
                "evidence_gaps": [],
            }
        )

        self.assertIn("Structural review candidates: 2 collision-shape", rendered)
        self.assertNotIn("2 collision,", rendered)
        self.assertIn("Overall closure: truncated (1 frontier(s), 1 unknown(s))", rendered)
        self.assertIn("- unspecified: truncated; 1 retained frontier(s)", rendered)
        self.assertNotIn("complete-within-model", rendered)
        self.assertIn("not runtime collisions", rendered)

    def test_impact_human_output_uses_overall_closure_and_caps_frontiers(self) -> None:
        frontiers = [
            {
                "kind": "depth-bound",
                "phase": "alternative-cycle-scan",
                "from_node_id": f"resource:{index}",
            }
            for index in range(20)
        ] + [
            {"kind": "node-bound", "phase": "machine-tier-signals"}
        ]
        rendered = atlas_recipe_cli._render_impact(
            {
                "selection": {"selection_id": "gt-recipe:fixture"},
                "summary": {
                    "at_risk_resource_candidate_count": 0,
                    "at_risk_recipe_candidate_count": 0,
                    "quest_requirement_exposure_count": 0,
                    "structural_quest_dependent_count": 0,
                    "alternative_dependency_cycle_signal_count": 3,
                    "truncated": True,
                },
                "bounds": {
                    "max_depth": 4,
                    "max_nodes": 500,
                    "visited_node_count": 500,
                },
                "propagation": {"status": "complete-within-model"},
                "frontiers": frontiers,
                "unknowns": [{"code": "cycle-scan-truncated"}],
                "evidence_gaps": [],
            }
        )

        self.assertIn("Overall closure: truncated (21 frontier(s), 1 unknown(s))", rendered)
        self.assertIn(
            "alternative-cycle-scan: truncated; 20 retained frontier(s)", rendered
        )
        self.assertIn("machine-tier-signals: truncated; 1 retained frontier(s)", rendered)
        self.assertIn("--json retains every exact frontier row", rendered)
        self.assertNotIn("complete-within-model", rendered)
        self.assertLess(len(rendered.splitlines()), 20)

    def test_unknown_without_frontier_is_not_presented_as_complete(self) -> None:
        closure = atlas_recipe_cli._closure_summary(
            {
                "summary": {"truncated": False},
                "frontiers": [],
                "unknowns": [{"code": "producer-lookup-state-unavailable"}],
            }
        )

        self.assertEqual("unresolved-within-bounds", closure["status"])

    def test_runtime_compare_opens_two_views_and_keeps_claim_boundary(self) -> None:
        class FakeView:
            def __init__(self, root: Path) -> None:
                self.root = root
                self.entered = 0
                self.exited = 0

            def __enter__(self):
                self.entered += 1
                return self

            def __exit__(self, *_: object) -> None:
                self.exited += 1

        opened: list[FakeView] = []

        def open_view(path: Path) -> FakeView:
            view = FakeView(path)
            opened.append(view)
            return view

        report = {
            "format": "workbench-atlas-runtime-recipe-comparison-v1",
            "compatibility": {"state": "compatible"},
            "bounds": {
                "max_depth": 3,
                "max_nodes": 100,
                "visited_node_count": 8,
            },
            "summary": {
                "removed_exact_signature_count": 1,
                "added_exact_signature_count": 2,
                "newly_exposed_resource_candidate_count": 3,
                "at_risk_recipe_candidate_count": 4,
                "quest_requirement_exposure_count": 1,
                "truncated": False,
            },
            "evidence_gaps": [],
        }
        with mock.patch.object(
            atlas_recipe_cli, "open_recipe_health", side_effect=open_view
        ), mock.patch.object(
            atlas_recipe_cli,
            "compare_runtime_recipe_graphs",
            return_value=report,
        ) as compared:
            status, output, error = self._main(
                "compare-runtime",
                "/baseline",
                "/candidate",
                "--max-recipes",
                "1000",
                "--max-recipe-deltas",
                "20",
                "--max-resources",
                "30",
                "--max-depth",
                "3",
                "--max-nodes",
                "100",
            )
        self.assertEqual(status, 0, error)
        self.assertEqual(
            [view.root for view in opened],
            [Path("/baseline"), Path("/candidate")],
        )
        self.assertTrue(
            all((view.entered, view.exited) == (1, 1) for view in opened)
        )
        compared.assert_called_once_with(
            opened[0],
            opened[1],
            max_recipes=1000,
            max_recipe_deltas=20,
            max_resources=30,
            max_depth=3,
            max_nodes=100,
        )
        self.assertIn("Comparability: compatible", output)
        self.assertIn("not proof of broken, dead, or unreachable", output)

    def test_runtime_compare_incompatible_json_emits_no_success_claim(self) -> None:
        report = {
            "format": "workbench-atlas-runtime-recipe-comparison-v1",
            "compatibility": {"state": "incomparable"},
            "summary": {},
            "bounds": {},
            "evidence_gaps": [],
        }
        view = mock.MagicMock()
        view.__enter__.return_value = view
        with mock.patch.object(
            atlas_recipe_cli, "open_recipe_health", return_value=view
        ), mock.patch.object(
            atlas_recipe_cli, "compare_runtime_recipe_graphs", return_value=report
        ):
            status, output, error = self._main(
                "compare-runtime", "/baseline", "/candidate", "--json"
            )
        self.assertEqual(status, 0, error)
        self.assertEqual(report, json.loads(output))
        self.assertEqual(
            "incomparable", json.loads(output)["compatibility"]["state"]
        )

    def test_registered_atlas_route_exposes_help_without_shell(self) -> None:
        from workbench_core.modules import InstalledModule, dispatch
        import workbench_registration_atlas

        with mock.patch.object(workbench_registration_atlas, "version", return_value="0.1.0"):
            module = workbench_registration_atlas.module()
        installed = InstalledModule("atlas", "workbench-atlas", "0.1.0", "available", module=module)
        context = ExecutionContext(ROOT, ROOT / ".workbench")
        output = StringIO()
        with redirect_stdout(output):
            status = dispatch(["atlas", "recipes", "--help"], context, [installed])
        self.assertEqual(0, status)
        help_text = output.getvalue()
        self.assertIn("usage: workbench atlas recipes", help_text)
        for action in ("context", "index", "search", "inspect", "impact", "assess-plan", "compare-runtime"):
            self.assertIn(action, help_text)

    def test_atlas_namespace_help_and_unknown_commands_do_not_enter_recipe_parser(self) -> None:
        from workbench_core.modules import InstalledModule, dispatch
        import workbench_registration_atlas

        with mock.patch.object(workbench_registration_atlas, "version", return_value="0.1.0"):
            module = workbench_registration_atlas.module()
        installed = InstalledModule("atlas", "workbench-atlas", "0.1.0", "available", module=module)
        context = ExecutionContext(ROOT, ROOT / ".workbench")
        with mock.patch.object(atlas_recipe_cli, "main") as recipe_main:
            for arguments in (["atlas"], ["atlas", "--help"]):
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(0, dispatch(arguments, context, [installed]))
                self.assertIn("usage: workbench atlas", output.getvalue())
                self.assertIn("recipes", output.getvalue())
            for command in ("search", "unknown"):
                error = StringIO()
                with redirect_stderr(error):
                    self.assertEqual(2, dispatch(["atlas", command], context, [installed]))
                self.assertIn("invalid choice", error.getvalue())
            recipe_main.assert_not_called()


if __name__ == "__main__":
    unittest.main()
