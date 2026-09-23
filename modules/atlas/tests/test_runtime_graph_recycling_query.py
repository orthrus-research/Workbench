#!/usr/bin/env python3

from __future__ import annotations

import copy
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parent))

from workbench_atlas.atlas_continuation import (  # noqa: E402
    canonical_sha256,
    compose_traversal_continuation,
    resume_traversal_continuation,
    start_traversal_continuation,
)
from workbench_atlas.runtime_graph_domain_query import (  # noqa: E402
    PRODUCER_PREDICATES,
    ProfileScope,
    ScopedTarget,
)
from workbench_atlas.runtime_graph_query import (  # noqa: E402
    NodeSelector,
    RuntimeGraphQueryError,
    RuntimeGraphReader,
)
from workbench_atlas.runtime_graph_recycling_query import (  # noqa: E402
    RecyclingOptions,
    RecyclingRootSeed,
    RecyclingRouteTarget,
    RuntimeGraphRecyclingContinuation,
    build_recycling_paths,
    validate_recycling_continuation_projection,
    canonical_recycling_roots,
    validate_recycling_result,
)
from test_runtime_graph_chain_query import (  # noqa: E402
    COMMON,
    GraphFixture,
)


class RuntimeGraphRecyclingQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = GraphFixture(self.root)
        self.addCleanup(self.fixture.close)

    def _source_origin(
        self,
        target: str,
        ordinal: int,
    ) -> tuple[str, str]:
        producer = self.fixture.add_node(
            "recipe",
            f"co-output-source:{ordinal}",
        )
        slot = self.fixture.add_slot(
            producer,
            f"co-output-source:{ordinal}",
            "produces",
            [target],
            ordinal=ordinal,
            slot_attributes={"amount": ordinal + 1},
        )
        return producer, slot

    @staticmethod
    def _scoped_target(
        reader: RuntimeGraphReader,
        identifier: str,
    ) -> ScopedTarget:
        node = reader.resolve_one(NodeSelector.by_id(identifier))
        scope = node["scope"]
        return ScopedTarget(
            ProfileScope(scope["profile"], scope["physical_side"]),
            node,
        )

    def _seed(
        self,
        reader: RuntimeGraphReader,
        target: str,
        producer: str,
        slot_id: str,
        *,
        route_id: str,
        subproblem_id: str,
    ) -> RecyclingRootSeed:
        scoped_target = self._scoped_target(reader, target)
        producer_record = reader.resolve_one(NodeSelector.by_id(producer))
        slot_row = next(
            row
            for row in reader.recipe_slots(
                producer,
                PRODUCER_PREDICATES,
            )
            if row["slot"]["id"] == slot_id
        )
        alternative = next(
            row
            for row in slot_row["alternatives"]
            if row["node"]["id"] == target
        )
        return RecyclingRootSeed(
            scoped_target,
            {
                "route_id": route_id,
                "subproblem_id": subproblem_id,
                "producer": producer_record,
                "byproduct_relationship": slot_row["relationship"],
                "slot": slot_row["slot"],
                "alternative_relationship": alternative["relationship"],
                "local_semantics": {
                    "output": {
                        "conditional": False,
                        "guaranteed": True,
                    },
                    "amount_source": "exact-slot-and-relationship-records",
                    "chance": {},
                },
            },
        )

    def _query(
        self,
        root_specs: list[tuple[str, str, str, str, str]],
        *,
        final_targets: list[str] | None = None,
        route_targets: list[tuple[str, str]] | None = None,
        options: RecyclingOptions = RecyclingOptions(),
    ) -> dict[str, object]:
        self.fixture.commit()
        with RuntimeGraphReader(self.fixture.database) as reader:
            seeds = [
                self._seed(
                    reader,
                    target,
                    producer,
                    slot,
                    route_id=route_id,
                    subproblem_id=subproblem_id,
                )
                for (
                    target,
                    producer,
                    slot,
                    route_id,
                    subproblem_id,
                ) in root_specs
            ]
            final = [
                self._scoped_target(reader, identifier)
                for identifier in (
                    [] if final_targets is None else final_targets
                )
            ]
            retained = [
                RecyclingRouteTarget(
                    subproblem_id,
                    self._scoped_target(reader, identifier),
                )
                for subproblem_id, identifier in (
                    [] if route_targets is None else route_targets
                )
            ]
            return build_recycling_paths(
                reader,
                seeds,
                final_targets=final,
                route_targets=retained,
                options=options,
            )

    def test_roots_deduplicate_exact_identity_and_retain_every_origin(
        self,
    ) -> None:
        root_a = self.fixture.add_item("root-a")
        root_b = self.fixture.add_item("root-b")
        source_a0 = self._source_origin(root_a, 0)
        source_a1 = self._source_origin(root_a, 1)
        source_b = self._source_origin(root_b, 2)

        result = self._query(
            [
                (root_b, *source_b, "route-b", "upstream-b"),
                (root_a, *source_a1, "route-a1", "upstream-a1"),
                (root_a, *source_a0, "route-a0", "upstream-a0"),
                (root_a, *source_a0, "route-a0", "upstream-a0"),
            ]
        )

        self.assertEqual(2, len(result["roots"]))
        by_target = {
            row["target"]["id"]: row for row in result["roots"]
        }
        self.assertEqual(2, len(by_target[root_a]["origins"]))
        self.assertEqual(1, len(by_target[root_b]["origins"]))
        self.assertEqual(
            ["route-a0", "route-a1"],
            [
                origin["route_id"]
                for origin in by_target[root_a]["origins"]
            ],
        )
        self.assertEqual(2, result["summary"]["root_count"])
        self.assertEqual(2, result["summary"]["visited_target_count"])
        self.assertEqual(
            {"open-terminal"},
            {
                row["classification"]
                for row in result["closures"]
            },
        )

    def test_empty_root_set_is_a_complete_empty_traversal(self) -> None:
        self.fixture.commit()
        with RuntimeGraphReader(self.fixture.database) as reader:
            result = build_recycling_paths(reader, [])

        self.assertEqual([], result["roots"])
        self.assertEqual([], result["subproblems"])
        self.assertEqual([], result["operations"])
        self.assertEqual(
            {
                "root_count": 0,
                "consuming_candidate_count": 0,
                "reusable_only_candidate_count": 0,
                "retained_operation_count": 0,
                "visited_target_count": 0,
                "closure_count": 0,
                "terminal_count": 0,
            },
            result["summary"],
        )
        self.assertFalse(result["truncation"]["truncated"])

    def test_same_depth_admission_is_fair_before_global_operation_limit(
        self,
    ) -> None:
        root_a = self.fixture.add_item("fair-root-a")
        root_b = self.fixture.add_item("fair-root-b")
        source_a = self._source_origin(root_a, 0)
        source_b = self._source_origin(root_b, 1)
        retained_owners: dict[tuple[str, int], str] = {}
        for target, name in ((root_a, "a"), (root_b, "b")):
            for rank in range(2):
                retained_owners[(name, rank)] = self.fixture.add_recipe(
                    f"fair-{name}-{rank}",
                    inputs=[("consumes", [target], {"amount": 1})],
                    outputs=[],
                )

        result = self._query(
            [
                (root_b, *source_b, "route-b", "upstream-b"),
                (root_a, *source_a, "route-a", "upstream-a"),
            ],
            options=RecyclingOptions(max_operations=2),
        )

        self.assertEqual(
            [
                retained_owners[("a", 0)],
                retained_owners[("b", 0)],
            ],
            [row["owner"]["id"] for row in result["operations"]],
        )
        self.assertEqual(
            [0, 0],
            [row["candidate_rank"] for row in result["operations"]],
        )
        self.assertEqual(
            [0, 1],
            [row["admission_index"] for row in result["operations"]],
        )
        self.assertIn(
            "max-operations",
            result["truncation"]["reasons"],
        )
        fairness_drift = copy.deepcopy(result)
        fairness_drift["operations"][0]["admission_index"] = 1
        fairness_drift["operations"][1]["admission_index"] = 0
        fairness_drift["operations"].reverse()
        with self.assertRaisesRegex(
            RuntimeGraphQueryError,
            "admission is not fair",
        ):
            validate_recycling_result(
                fairness_drift,
                copy.deepcopy(result["roots"]),
            )

    def test_recycling_continuation_pauses_within_rank_and_matches_fresh(
        self,
    ) -> None:
        root_a = self.fixture.add_item("resume-fair-root-a")
        root_b = self.fixture.add_item("resume-fair-root-b")
        source_a = self._source_origin(root_a, 0)
        source_b = self._source_origin(root_b, 1)
        for target, name in ((root_a, "a"), (root_b, "b")):
            for rank in range(2):
                self.fixture.add_recipe(
                    f"resume-fair-{name}-{rank}",
                    inputs=[("consumes", [target], {"amount": 1})],
                    outputs=[],
                )
        self.fixture.commit()

        with RuntimeGraphReader(self.fixture.database) as reader:
            seeds = [
                self._seed(
                    reader,
                    root_b,
                    *source_b,
                    route_id="route-b",
                    subproblem_id="upstream-b",
                ),
                self._seed(
                    reader,
                    root_a,
                    *source_a,
                    route_id="route-a",
                    subproblem_id="upstream-a",
                ),
            ]
            fresh = build_recycling_paths(reader, seeds)
            session = RuntimeGraphRecyclingContinuation(reader, seeds)
            first = session.step(1)
            self.assertFalse(first["complete"])
            self.assertEqual(0, first["frontier"]["active"]["rank"])
            self.assertEqual(
                1,
                first["frontier"]["active"]["identifier_index"],
            )
            self.assertEqual(
                [0],
                [row["candidate_rank"] for row in first["result"]["operations"]],
            )

            total_consumed = first["consumed_work_items"]
            document = first["state"]
            segments = 1
            while not session.complete:
                session = RuntimeGraphRecyclingContinuation.from_state(
                    reader,
                    document,
                )
                segment = session.step(1)
                total_consumed += segment["consumed_work_items"]
                document = segment["state"]
                segments += 1
                self.assertLess(segments, 20)

            self.assertGreater(segments, 4)
            self.assertEqual(total_consumed, session.cumulative_work_items)
            self.assertEqual(fresh, session.result())
            one_shot = RuntimeGraphRecyclingContinuation(reader, seeds)
            one_shot.step(100)
            self.assertTrue(one_shot.complete)
            self.assertEqual(one_shot.result(), session.result())

    def test_recycling_continuation_rejects_fairness_cursor_tampering(
        self,
    ) -> None:
        root = self.fixture.add_item("resume-tamper-root")
        source = self._source_origin(root, 0)
        self.fixture.add_recipe(
            "resume-tamper-consumer",
            inputs=[("consumes", [root], {"amount": 1})],
            outputs=[],
        )
        self.fixture.commit()

        with RuntimeGraphReader(self.fixture.database) as reader:
            seed = self._seed(
                reader,
                root,
                *source,
                route_id="route",
                subproblem_id="upstream",
            )
            session = RuntimeGraphRecyclingContinuation(reader, [seed])
            document = session.step(1)["state"]
            document["active_depth"]["identifier_index"] = 99
            with self.assertRaisesRegex(
                RuntimeGraphQueryError, "fairness cursor is invalid"
            ):
                RuntimeGraphRecyclingContinuation.from_state(
                    reader,
                    document,
                )

    def test_recycling_continuation_preserves_cycle_ancestry(self) -> None:
        root = self.fixture.add_item("resume-cycle-root")
        child = self.fixture.add_item("resume-cycle-child")
        source = self._source_origin(root, 0)
        self.fixture.add_recipe(
            "resume-cycle-forward",
            inputs=[("consumes", [root], {"amount": 1})],
            outputs=[("produces", [child], {"amount": 1})],
        )
        self.fixture.add_recipe(
            "resume-cycle-return",
            inputs=[("consumes", [child], {"amount": 1})],
            outputs=[("produces", [root], {"amount": 1})],
        )
        self.fixture.commit()

        with RuntimeGraphReader(self.fixture.database) as reader:
            seed = self._seed(
                reader,
                root,
                *source,
                route_id="route",
                subproblem_id="upstream",
            )
            fresh = build_recycling_paths(reader, [seed])
            session = RuntimeGraphRecyclingContinuation(reader, [seed])
            while not session.complete:
                segment = session.step(1)
                session = RuntimeGraphRecyclingContinuation.from_state(
                    reader,
                    segment["state"],
                )

            self.assertEqual(fresh, session.result())
            self.assertEqual(1, len(session.result()["cycles"]))
            self.assertIn(
                "forward-cycle",
                {
                    row["classification"]
                    for row in session.result()["closures"]
                },
            )

    def test_recycling_manifest_lineage_composes_exact_result(self) -> None:
        root = self.fixture.add_item("manifest-recycling-root")
        child = self.fixture.add_item("manifest-recycling-child")
        source = self._source_origin(root, 0)
        self.fixture.add_recipe(
            "manifest-recycling-consumer",
            inputs=[("consumes", [root], {"amount": 1})],
            outputs=[("produces", [child], {"amount": 1})],
        )
        self.fixture.commit()
        semantic = {
            "snapshot_id": "SNAPSHOT-FIXTURE",
            "scopes": [
                {
                    "profile": "COMMON_FINAL_STATE",
                    "physical_side": "CLIENT",
                }
            ],
        }
        query_instance = {
            **semantic,
            "query_instance_id": (
                "atlas-query:sha256:" + canonical_sha256(semantic)
            ),
        }

        with RuntimeGraphReader(self.fixture.database) as reader:
            seed = self._seed(
                reader,
                root,
                *source,
                route_id="route",
                subproblem_id="upstream",
            )
            fresh = build_recycling_paths(reader, [seed])
            packet = start_traversal_continuation(
                RuntimeGraphRecyclingContinuation(reader, [seed]),
                query_instance,
                [{"id": "evidence:recycling"}],
                max_work_items=1,
            )
            parent = packet["manifest"]
            while parent["status"] == "active":
                packet = resume_traversal_continuation(
                    reader,
                    parent,
                    max_work_items=1,
                )
                self.assertEqual(
                    packet["manifest"],
                    compose_traversal_continuation(
                        parent,
                        packet["delta"],
                    ),
                )
                parent = packet["manifest"]

            self.assertEqual(fresh, parent["result"])
            forged_state = copy.deepcopy(parent["state"])
            forged_state["operation_admission_count"] += 1
            with self.assertRaisesRegex(
                RuntimeGraphQueryError,
                "frontier differs",
            ):
                validate_recycling_continuation_projection(
                    forged_state,
                    parent["result"],
                    parent["frontier"],
                    cumulative_work_items=parent[
                        "cumulative_budget"
                    ]["consumed_work_items"],
                    status=parent["status"],
                )

    def test_recycling_continuation_preserves_structural_boundaries(
        self,
    ) -> None:
        root = self.fixture.add_item("resume-bound-root")
        output_a = self.fixture.add_item("resume-bound-output-a")
        output_b = self.fixture.add_item("resume-bound-output-b")
        source = self._source_origin(root, 0)
        self.fixture.add_recipe(
            "resume-bound-consumer-a",
            inputs=[("consumes", [root], {"amount": 1})],
            outputs=[("produces", [output_a, output_b], {"amount": 1})],
        )
        self.fixture.add_recipe(
            "resume-bound-consumer-b",
            inputs=[("consumes", [root], {"amount": 1})],
            outputs=[],
        )
        self.fixture.commit()
        options_rows = (
            RecyclingOptions(max_consumers_per_target=1),
            RecyclingOptions(max_output_alternatives_per_slot=1),
            RecyclingOptions(max_visited_targets=1),
            RecyclingOptions(max_depth=0),
            RecyclingOptions(max_operations=1),
        )

        with RuntimeGraphReader(self.fixture.database) as reader:
            seed = self._seed(
                reader,
                root,
                *source,
                route_id="route",
                subproblem_id="upstream",
            )
            for options in options_rows:
                with self.subTest(options=options):
                    fresh = build_recycling_paths(
                        reader,
                        [seed],
                        options=options,
                    )
                    session = RuntimeGraphRecyclingContinuation(
                        reader,
                        [seed],
                        options=options,
                    )
                    while not session.complete:
                        segment = session.step(1)
                        session = (
                            RuntimeGraphRecyclingContinuation.from_state(
                                reader,
                                segment["state"],
                            )
                        )
                    self.assertEqual(fresh, session.result())

    def test_recycling_continuation_preserves_procedural_boundary(
        self,
    ) -> None:
        root = self.fixture.add_item("resume-procedural-root")
        output = self.fixture.add_item("resume-procedural-output")
        source = self._source_origin(root, 0)
        self.fixture.add_process_rule(
            "resume-procedural-consumer",
            inputs=[
                (
                    "consumes",
                    [root],
                    {"amount_formula": "context"},
                )
            ],
            outputs=[
                (
                    "produces",
                    [output],
                    {"amount_formula": "context"},
                )
            ],
        )
        self.fixture.commit()

        with RuntimeGraphReader(self.fixture.database) as reader:
            seed = self._seed(
                reader,
                root,
                *source,
                route_id="route",
                subproblem_id="upstream",
            )
            fresh = build_recycling_paths(reader, [seed])
            session = RuntimeGraphRecyclingContinuation(reader, [seed])
            while not session.complete:
                segment = session.step(1)
                session = RuntimeGraphRecyclingContinuation.from_state(
                    reader,
                    segment["state"],
                )
            self.assertEqual(fresh, session.result())
            self.assertIn(
                "open-procedural-boundary",
                {
                    row["classification"]
                    for row in session.result()["closures"]
                },
            )

    def test_recycling_continuation_preserves_converged_reachability(
        self,
    ) -> None:
        root_a = self.fixture.add_item("resume-converge-root-a")
        root_b = self.fixture.add_item("resume-converge-root-b")
        shared = self.fixture.add_item("resume-converge-shared")
        source_a = self._source_origin(root_a, 0)
        source_b = self._source_origin(root_b, 1)
        self.fixture.add_recipe(
            "resume-converge-a",
            inputs=[("consumes", [root_a], {"amount": 1})],
            outputs=[("produces", [shared], {"amount": 1})],
        )
        self.fixture.add_recipe(
            "resume-converge-b",
            inputs=[("consumes", [root_b], {"amount": 1})],
            outputs=[("produces", [shared], {"amount": 1})],
        )
        self.fixture.commit()

        with RuntimeGraphReader(self.fixture.database) as reader:
            seeds = [
                self._seed(
                    reader,
                    root_a,
                    *source_a,
                    route_id="route-a",
                    subproblem_id="upstream-a",
                ),
                self._seed(
                    reader,
                    root_b,
                    *source_b,
                    route_id="route-b",
                    subproblem_id="upstream-b",
                ),
            ]
            fresh = build_recycling_paths(reader, seeds)
            session = RuntimeGraphRecyclingContinuation(reader, seeds)
            while not session.complete:
                segment = session.step(1)
                session = RuntimeGraphRecyclingContinuation.from_state(
                    reader,
                    segment["state"],
                )
            result = session.result()
            self.assertEqual(fresh, result)
            shared_row = next(
                row
                for row in result["subproblems"]
                if row["target"]["id"] == shared
            )
            self.assertEqual(2, len(shared_row["root_ids"]))
            self.assertEqual(
                1,
                len(
                    {
                        edge["to_subproblem_id"]
                        for edge in result["continuation_edges"]
                    }
                ),
            )

    def test_convergence_closures_cycle_and_reusable_exclusion(
        self,
    ) -> None:
        root_a = self.fixture.add_item("dag-root-a")
        root_b = self.fixture.add_item("dag-root-b")
        root_terminal = self.fixture.add_item("dag-root-terminal")
        shared = self.fixture.add_item("dag-shared")
        final = self.fixture.add_item("dag-final")
        route_target = self.fixture.add_item("dag-route-target")
        source_a = self._source_origin(root_a, 0)
        source_b = self._source_origin(root_b, 1)
        source_terminal = self._source_origin(root_terminal, 2)
        self.fixture.add_recipe(
            "dag-consume-a",
            inputs=[("consumes", [root_a], {"amount": 1})],
            outputs=[("produces", [shared], {"amount": 1})],
        )
        self.fixture.add_recipe(
            "dag-consume-b",
            inputs=[("may_consume", [root_b], {"chance": 2500})],
            outputs=[("produces", [shared], {"amount": 1})],
        )
        self.fixture.add_recipe(
            "dag-reusable-a",
            inputs=[("requires", [root_a], {"amount": 1})],
            outputs=[("produces", [route_target], {"amount": 1})],
        )
        self.fixture.add_recipe(
            "dag-consume-shared",
            inputs=[("consumes", [shared], {"amount": 1})],
            outputs=[
                ("produces", [final], {"amount": 1}),
                ("produces", [route_target], {"amount": 1}),
                ("produces", [root_a], {"amount": 1}),
            ],
        )

        result = self._query(
            [
                (root_a, *source_a, "route-a", "upstream-a"),
                (root_b, *source_b, "route-b", "upstream-b"),
                (
                    root_terminal,
                    *source_terminal,
                    "route-terminal",
                    "upstream-terminal",
                ),
            ],
            final_targets=[final],
            route_targets=[("retained-route-target", route_target)],
        )

        shared_rows = [
            row
            for row in result["subproblems"]
            if row["target"]["id"] == shared
        ]
        self.assertEqual(1, len(shared_rows))
        shared_id = shared_rows[0]["id"]
        self.assertEqual(
            2,
            len(
                [
                    edge
                    for edge in result["continuation_edges"]
                    if edge["to_subproblem_id"] == shared_id
                ]
            ),
        )
        self.assertEqual(
            {
                "returns-final-target",
                "rejoins-retained-route",
                "forward-cycle",
                "open-terminal",
            },
            {
                row["classification"]
                for row in result["closures"]
            },
        )
        self.assertEqual(1, len(result["cycles"]))
        self.assertEqual(
            1,
            result["summary"]["reusable_only_candidate_count"],
        )
        reusable_owner_ids = {
            row["owner"]["id"]
            for row in result["operations"]
            if row["owner"]["id"].endswith("dag-reusable-a")
        }
        self.assertEqual(set(), reusable_owner_ids)
        may_consume = next(
            row
            for row in result["operations"]
            if row["consumption"]["relationship"]["predicate"]
            == "may_consume"
        )
        self.assertTrue(
            may_consume["consumption"]["semantics"]["conditional"]
        )
        self.assertEqual(
            2500,
            may_consume["consumption"]["chance"]["slot"]["chance"],
        )
        self.assertFalse(result["truncation"]["truncated"])
        reachability_drift = copy.deepcopy(result)
        drifted_shared = next(
            row
            for row in reachability_drift["subproblems"]
            if row["target"]["id"] == shared
        )
        drifted_shared["root_ids"] = drifted_shared["root_ids"][:1]
        with self.assertRaisesRegex(
            RuntimeGraphQueryError,
            "reachability differs",
        ):
            validate_recycling_result(
                reachability_drift,
                copy.deepcopy(result["roots"]),
            )
        match_drift = copy.deepcopy(result)
        closed = next(
            row
            for row in match_drift["closures"]
            if row["classification"] == "rejoins-retained-route"
        )
        closed["match"]["path"] = [
            copy.deepcopy(
                match_drift["operations"][0]["consumption"][
                    "relationship"
                ]
            )
        ]
        with self.assertRaisesRegex(
            RuntimeGraphQueryError,
            "match path is disconnected",
        ):
            validate_recycling_result(
                match_drift,
                copy.deepcopy(result["roots"]),
            )

    def test_procedural_outputs_stop_at_explicit_boundary(self) -> None:
        root = self.fixture.add_item("procedural-root")
        output = self.fixture.add_item("procedural-output")
        source = self._source_origin(root, 0)
        self.fixture.add_process_rule(
            "procedural-consumer",
            inputs=[("consumes", [root], {"amount_formula": "context"})],
            outputs=[("produces", [output], {"amount_formula": "context"})],
        )

        result = self._query(
            [(root, *source, "route", "upstream")]
        )

        self.assertEqual(1, len(result["operations"]))
        self.assertEqual([], result["continuation_edges"])
        self.assertEqual(
            ["open-procedural-boundary"],
            [
                row["classification"]
                for row in result["closures"]
            ],
        )

    def test_each_global_bound_is_explicit_and_fail_closed(self) -> None:
        root = self.fixture.add_item("bounded-root")
        output_a = self.fixture.add_item("bounded-output-a")
        output_b = self.fixture.add_item("bounded-output-b")
        source = self._source_origin(root, 0)
        self.fixture.add_recipe(
            "bounded-consumer-a",
            inputs=[("consumes", [root], {"amount": 1})],
            outputs=[
                (
                    "produces",
                    [output_a, output_b],
                    {"amount": 1},
                )
            ],
        )
        self.fixture.add_recipe(
            "bounded-consumer-b",
            inputs=[("consumes", [root], {"amount": 1})],
            outputs=[],
        )
        spec = [(root, *source, "route", "upstream")]

        consumer_bound = self._query(
            spec,
            options=RecyclingOptions(max_consumers_per_target=1),
        )
        self.assertIn(
            "max-consumers-per-target",
            consumer_bound["truncation"]["reasons"],
        )
        root_subproblem = next(
            row
            for row in consumer_bound["subproblems"]
            if row["target"]["id"] == root
        )
        self.assertEqual(2, root_subproblem["consumer_page"]["total"])
        self.assertEqual(1, root_subproblem["consumer_page"]["returned"])

        output_bound = self._query(
            spec,
            options=RecyclingOptions(
                max_output_alternatives_per_slot=1,
            ),
        )
        self.assertIn(
            "max-output-alternatives-per-slot",
            output_bound["truncation"]["reasons"],
        )
        truncation_drift = copy.deepcopy(output_bound)
        truncation_drift["truncation"]["reasons"] = []
        truncation_drift["truncation"]["truncated"] = False
        with self.assertRaisesRegex(
            RuntimeGraphQueryError,
            "truncation reasons differ",
        ):
            validate_recycling_result(
                truncation_drift,
                copy.deepcopy(output_bound["roots"]),
            )

        visited_bound = self._query(
            spec,
            options=RecyclingOptions(max_visited_targets=1),
        )
        self.assertIn(
            "max-visited-targets",
            visited_bound["truncation"]["reasons"],
        )
        self.assertEqual(1, visited_bound["summary"]["visited_target_count"])

        depth_bound = self._query(
            spec,
            options=RecyclingOptions(max_depth=0),
        )
        self.assertIn("max-depth", depth_bound["truncation"]["reasons"])
        self.assertEqual(0, depth_bound["summary"]["retained_operation_count"])

        operation_bound = self._query(
            spec,
            options=RecyclingOptions(max_operations=1),
        )
        self.assertIn(
            "max-operations",
            operation_bound["truncation"]["reasons"],
        )
        self.assertEqual(
            1,
            operation_bound["summary"]["retained_operation_count"],
        )

    def test_semantic_recomputation_rejects_drift(self) -> None:
        root = self.fixture.add_item("validation-root")
        source = self._source_origin(root, 0)
        self.fixture.add_recipe(
            "validation-consumer",
            inputs=[("consumes", [root], {"amount": 1})],
            outputs=[],
        )
        result = self._query(
            [(root, *source, "route", "upstream")]
        )
        roots = copy.deepcopy(result["roots"])

        summary_drift = copy.deepcopy(result)
        summary_drift["summary"]["retained_operation_count"] += 1
        with self.assertRaisesRegex(
            RuntimeGraphQueryError,
            "summary differs",
        ):
            validate_recycling_result(summary_drift, roots)

        predicate_drift = copy.deepcopy(result)
        predicate_drift["operations"][0]["consumption"]["relationship"][
            "predicate"
        ] = "requires"
        with self.assertRaisesRegex(
            RuntimeGraphQueryError,
            "not an exact consuming occurrence",
        ):
            validate_recycling_result(predicate_drift, roots)

        candidate_drift = copy.deepcopy(result)
        drifted_candidate_id = "recycling-candidate:drift"
        candidate_drift["operations"][0]["candidate_id"] = drifted_candidate_id
        candidate_drift["subproblems"][0]["consumer_page"][
            "candidate_ids"
        ][0] = drifted_candidate_id
        candidate_drift["subproblems"][0]["candidate_dispositions"][0][
            "candidate_id"
        ] = drifted_candidate_id
        with self.assertRaisesRegex(
            RuntimeGraphQueryError,
            "identity differs",
        ):
            validate_recycling_result(candidate_drift, roots)

        operation_reference_drift = copy.deepcopy(result)
        operation_reference_drift["subproblems"][0][
            "operation_ids"
        ] = []
        with self.assertRaisesRegex(
            RuntimeGraphQueryError,
            "has no operation",
        ):
            validate_recycling_result(operation_reference_drift, roots)

        roots_drift = copy.deepcopy(result)
        roots_drift["roots"][0]["origins"] = []
        with self.assertRaisesRegex(
            RuntimeGraphQueryError,
            "roots differ",
        ):
            validate_recycling_result(roots_drift, roots)

    def test_invalid_root_endpoint_and_option_fail_closed(self) -> None:
        root = self.fixture.add_item("invalid-root")
        source = self._source_origin(root, 0)
        self.fixture.commit()
        with RuntimeGraphReader(self.fixture.database) as reader:
            seed = self._seed(
                reader,
                root,
                *source,
                route_id="route",
                subproblem_id="upstream",
            )
            malformed = copy.deepcopy(seed.origin)
            malformed["alternative_relationship"]["object"] = "wrong"
            with self.assertRaisesRegex(
                RuntimeGraphQueryError,
                "endpoints differ",
            ):
                canonical_recycling_roots(
                    [RecyclingRootSeed(seed.target, malformed)]
                )

        with self.assertRaisesRegex(
            RuntimeGraphQueryError,
            "max_consumers_per_target",
        ):
            RecyclingOptions(
                max_consumers_per_target=1001,
            )


if __name__ == "__main__":
    unittest.main()
