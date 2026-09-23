#!/usr/bin/env python3

from __future__ import annotations

import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import workbench_atlas.runtime_graph as graph
from workbench_atlas.runtime_graph_chain_query import (  # noqa: E402
    ChainOptions,
    RuntimeGraphChainContinuation,
    build_process_chain,
    build_process_chain_from_targets,
    validate_process_chain_result,
    validate_route_continuation_projection,
)
from workbench_atlas.runtime_graph_domain_query import (  # noqa: E402
    ProfileScope,
    ScopedTarget,
)
from workbench_atlas.runtime_graph_query import (  # noqa: E402
    NodeSelector,
    RuntimeGraphQueryError,
    RuntimeGraphReader,
)

COMMON = ("COMMON_FINAL_STATE", "CLIENT")
HEI = ("CLIENT_JEI_FINAL_STATE", "CLIENT")


class GraphFixture:
    def __init__(self, root: Path):
        self.database = root / "runtime-graph.sqlite"
        self.connection = sqlite3.connect(self.database)
        self.connection.executescript(
            """
            BEGIN;
            PRAGMA application_id = 1398098247;
            PRAGMA user_version = 2;
            CREATE TABLE nodes (
                id TEXT PRIMARY KEY,
                profile TEXT NOT NULL,
                physical_side TEXT NOT NULL,
                adapter TEXT NOT NULL,
                kind TEXT NOT NULL,
                json TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE node_keys (
                node_id TEXT NOT NULL,
                key_kind TEXT NOT NULL,
                key_value TEXT NOT NULL,
                profile TEXT NOT NULL,
                physical_side TEXT NOT NULL,
                PRIMARY KEY (
                    key_kind, key_value, profile, physical_side, node_id
                )
            ) WITHOUT ROWID;
            CREATE TABLE edges (
                id TEXT PRIMARY KEY,
                profile TEXT NOT NULL,
                physical_side TEXT NOT NULL,
                adapter TEXT NOT NULL,
                predicate TEXT NOT NULL,
                subject TEXT NOT NULL,
                object TEXT NOT NULL,
                json TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE diagnostics (
                id TEXT PRIMARY KEY,
                profile TEXT NOT NULL,
                physical_side TEXT NOT NULL,
                adapter TEXT NOT NULL,
                severity TEXT NOT NULL,
                code TEXT NOT NULL,
                json TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE reconciliations (
                edge_id TEXT PRIMARY KEY,
                hei_id TEXT NOT NULL,
                common_id TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE INDEX node_keys_lookup
                ON node_keys(key_kind, key_value);
            CREATE INDEX edges_subject ON edges(subject);
            CREATE INDEX edges_object ON edges(object);
            CREATE INDEX edges_predicate ON edges(predicate);
            COMMIT;
            """
        )

    @staticmethod
    def scope(
        adapter: str = "fixture",
        scope: tuple[str, str] = COMMON,
    ) -> dict[str, str]:
        return {
            "snapshot_id": graph.SNAPSHOT_ID,
            "profile": scope[0],
            "physical_side": scope[1],
            "adapter": adapter,
        }

    @staticmethod
    def node_id(
        kind: str,
        name: str,
        scope: tuple[str, str] = COMMON,
    ) -> str:
        profile = scope[0].lower()
        side = scope[1].lower()
        return f"rg:{profile}_{side}:{kind}:fixture:{name}"

    def add_node(
        self,
        kind: str,
        name: str,
        *,
        attributes: dict[str, object] | None = None,
        scope: tuple[str, str] = COMMON,
        adapter: str = "fixture",
    ) -> str:
        identifier = self.node_id(kind, name, scope)
        row = {
            "record_type": "node",
            "id": identifier,
            "kind": kind,
            "scope": self.scope(adapter, scope),
            "attributes": {} if attributes is None else attributes,
        }
        self.connection.execute(
            "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?)",
            (
                identifier,
                scope[0],
                scope[1],
                adapter,
                kind,
                graph.canonical_json_payload(row).decode("utf-8"),
            ),
        )
        return identifier

    def add_item(
        self,
        name: str,
        *,
        scope: tuple[str, str] = COMMON,
    ) -> str:
        return self.add_node("item_variant", name, scope=scope)

    def add_edge(
        self,
        predicate: str,
        subject: str,
        object_: str,
        *,
        attributes: dict[str, object] | None = None,
        scope: tuple[str, str] = COMMON,
        adapter: str = "fixture",
    ) -> dict[str, object]:
        row: dict[str, object] = {
            "record_type": "edge",
            "predicate": predicate,
            "subject": subject,
            "object": object_,
            "scope": self.scope(adapter, scope),
            "attributes": {} if attributes is None else attributes,
        }
        row["id"] = graph.edge_id(row)
        self.connection.execute(
            "INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"],
                scope[0],
                scope[1],
                adapter,
                predicate,
                subject,
                object_,
                graph.canonical_json_payload(row).decode("utf-8"),
            ),
        )
        return row

    def add_slot(
        self,
        owner: str,
        name: str,
        predicate: str,
        alternatives: list[str],
        *,
        ordinal: int,
        slot_attributes: dict[str, object] | None = None,
        relationship_attributes: dict[str, object] | None = None,
        alternative_attributes: list[dict[str, object]] | None = None,
        scope: tuple[str, str] = COMMON,
    ) -> str:
        attributes = {"ordinal": ordinal}
        if slot_attributes:
            attributes.update(slot_attributes)
        slot = self.add_node(
            "ingredient_slot",
            f"{name}:{predicate}:{ordinal}",
            attributes=attributes,
            scope=scope,
        )
        self.add_edge(
            predicate,
            owner,
            slot,
            attributes=relationship_attributes,
            scope=scope,
        )
        edge_attributes = (
            alternative_attributes
            if alternative_attributes is not None
            else [
                {"alternative_ordinal": index}
                for index in range(len(alternatives))
            ]
        )
        for alternative, relation_attributes in zip(
            alternatives,
            edge_attributes,
        ):
            self.add_edge(
                "accepts_alternative",
                slot,
                alternative,
                attributes=relation_attributes,
                scope=scope,
            )
        return slot

    def add_recipe(
        self,
        name: str,
        *,
        inputs: list[tuple[str, list[str], dict[str, object] | None]] = [],
        outputs: list[tuple[str, list[str], dict[str, object] | None]],
        execution: str | None = "executes_recipe_map",
        lookup_active: bool = True,
        scope: tuple[str, str] = COMMON,
    ) -> str:
        recipe = self.add_node("recipe", name, scope=scope)
        recipe_map = self.add_node("recipe_map", f"{name}:map", scope=scope)
        machine = self.add_node("machine", f"{name}:machine", scope=scope)
        self.add_edge(
            "has_recipe",
            recipe_map,
            recipe,
            attributes={
                "category_present": True,
                "lookup_active": lookup_active,
            },
            scope=scope,
        )
        if execution is not None:
            self.add_edge(
                execution,
                machine,
                recipe_map,
                scope=scope,
            )
        for ordinal, (predicate, alternatives, slot_attributes) in enumerate(
            inputs
        ):
            self.add_slot(
                recipe,
                f"{name}:input",
                predicate,
                alternatives,
                ordinal=ordinal,
                slot_attributes=slot_attributes,
                scope=scope,
            )
        for ordinal, (predicate, alternatives, slot_attributes) in enumerate(
            outputs
        ):
            self.add_slot(
                recipe,
                f"{name}:output",
                predicate,
                alternatives,
                ordinal=ordinal,
                slot_attributes=slot_attributes,
                scope=scope,
            )
        return recipe

    def add_process_rule(
        self,
        name: str,
        *,
        inputs: list[tuple[str, list[str], dict[str, object] | None]],
        outputs: list[tuple[str, list[str], dict[str, object] | None]],
    ) -> str:
        rule = self.add_node(
            "process_rule",
            name,
            attributes={"procedural": True},
        )
        machine = self.add_node("machine", f"{name}:machine")
        self.add_edge("governed_by_rule", machine, rule)
        for ordinal, (predicate, alternatives, slot_attributes) in enumerate(
            inputs
        ):
            self.add_slot(
                rule,
                f"{name}:input",
                predicate,
                alternatives,
                ordinal=ordinal,
                slot_attributes=slot_attributes,
            )
        for ordinal, (predicate, alternatives, slot_attributes) in enumerate(
            outputs
        ):
            self.add_slot(
                rule,
                f"{name}:output",
                predicate,
                alternatives,
                ordinal=ordinal,
                slot_attributes=slot_attributes,
            )
        return rule

    def commit(self) -> None:
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


class RuntimeGraphChainQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = GraphFixture(self.root)
        self.addCleanup(self.fixture.close)

    def query(
        self,
        target: str,
        options: ChainOptions = ChainOptions(),
        *,
        scopes: object = None,
    ) -> dict[str, object]:
        self.fixture.commit()
        with RuntimeGraphReader(self.fixture.database) as reader:
            return build_process_chain(
                reader,
                NodeSelector.by_id(target),
                options=options,
                graph_scope=scopes,
            )

    def query_targets(
        self,
        targets: list[str],
        options: ChainOptions = ChainOptions(),
    ) -> dict[str, object]:
        self.fixture.commit()
        with RuntimeGraphReader(self.fixture.database) as reader:
            scoped_targets = []
            for target in targets:
                node = reader.resolve_one(NodeSelector.by_id(target))
                scope = node["scope"]
                scoped_targets.append(
                    ScopedTarget(
                        ProfileScope(
                            scope["profile"],
                            scope["physical_side"],
                        ),
                        node,
                    )
                )
            return build_process_chain_from_targets(
                reader,
                scoped_targets,
                options=options,
            )

    def test_route_continuation_resumes_decoded_state_and_matches_fresh(
        self,
    ) -> None:
        raw = self.fixture.add_item("continuation-raw")
        intermediate = self.fixture.add_item("continuation-intermediate")
        target = self.fixture.add_item("continuation-target")
        self.fixture.add_recipe(
            "continuation-intermediate-a",
            inputs=[("consumes", [raw], {"amount": 1})],
            outputs=[("produces", [intermediate], {"amount": 1})],
        )
        self.fixture.add_recipe(
            "continuation-intermediate-b",
            inputs=[],
            outputs=[("produces", [intermediate], {"amount": 1})],
        )
        self.fixture.add_recipe(
            "continuation-target",
            inputs=[("consumes", [intermediate], {"amount": 1})],
            outputs=[("produces", [target], {"amount": 1})],
        )
        self.fixture.commit()

        with RuntimeGraphReader(self.fixture.database) as reader:
            node = reader.resolve_one(NodeSelector.by_id(target))
            scoped = ScopedTarget(ProfileScope(*COMMON), node)
            fresh = build_process_chain_from_targets(reader, [scoped])

            session = RuntimeGraphChainContinuation(reader, [scoped])
            first = session.step(1)
            self.assertFalse(first["complete"])
            self.assertEqual(1, first["consumed_work_items"])
            self.assertEqual(
                1,
                first["frontier"]["active"]["next_occurrence"],
            )
            checkpoints = [first["state"]]
            segments = 1
            total_consumed = first["consumed_work_items"]
            while not session.complete:
                session = RuntimeGraphChainContinuation.from_state(
                    reader,
                    checkpoints[-1],
                )
                segment = session.step(1)
                checkpoints.append(segment["state"])
                segments += 1
                total_consumed += segment["consumed_work_items"]
                self.assertLess(segments, 20)

            self.assertGreater(segments, 2)
            self.assertEqual(fresh, session.result())
            self.assertEqual(total_consumed, session.cumulative_work_items)
            one_shot = RuntimeGraphChainContinuation(reader, [scoped])
            one_shot.step(100)
            self.assertTrue(one_shot.complete)
            self.assertEqual(
                graph.canonical_json_payload(one_shot.result()),
                graph.canonical_json_payload(session.result()),
            )

    def test_route_continuation_recomputes_cycles_from_state(self) -> None:
        a = self.fixture.add_item("continuation-cycle-a")
        b = self.fixture.add_item("continuation-cycle-b")
        self.fixture.add_recipe(
            "continuation-cycle-a-from-b",
            inputs=[("consumes", [b], None)],
            outputs=[("produces", [a], None)],
        )
        self.fixture.add_recipe(
            "continuation-cycle-b-from-a",
            inputs=[("consumes", [a], None)],
            outputs=[("produces", [b], None)],
        )
        self.fixture.add_recipe(
            "continuation-cycle-b-from-b",
            inputs=[("consumes", [b], None)],
            outputs=[("produces", [b], None)],
        )
        self.fixture.commit()

        with RuntimeGraphReader(self.fixture.database) as reader:
            node = reader.resolve_one(NodeSelector.by_id(a))
            scoped = ScopedTarget(ProfileScope(*COMMON), node)
            fresh = build_process_chain_from_targets(reader, [scoped])
            session = RuntimeGraphChainContinuation(reader, [scoped])
            while not session.complete:
                segment = session.step(1)
                session = RuntimeGraphChainContinuation.from_state(
                    reader,
                    segment["state"],
                )

            resumed = session.result()
            state = session.export_state()
            frontier = session.frontier()
            self.assertEqual(fresh, resumed)
            self.assertEqual(
                [2],
                [
                    len(cycle["subproblem_ids"])
                    for cycle in resumed["cycles"]
                ],
            )
            self.assertTrue(
                any(
                    edge["from"] == edge["to"]
                    for edge in resumed["cycles"][0]["dependencies"]
                )
            )
            validate_route_continuation_projection(
                state,
                resumed,
                frontier,
                cumulative_work_items=session.cumulative_work_items,
                status="complete",
            )

            mutated_state = copy.deepcopy(state)
            self_loop = next(
                edge
                for edge in mutated_state["dependencies"]
                if edge["from"] == edge["to"]
            )
            self_loop["alternative_relationship_id"] = "fabricated"
            with self.assertRaisesRegex(
                RuntimeGraphQueryError,
                "result differs from its decoded state",
            ):
                validate_route_continuation_projection(
                    mutated_state,
                    resumed,
                    frontier,
                    cumulative_work_items=session.cumulative_work_items,
                    status="complete",
                )

    def test_route_continuation_rejects_target_state_tampering(self) -> None:
        target = self.fixture.add_item("continuation-tamper-target")
        self.fixture.add_recipe(
            "continuation-tamper-route",
            outputs=[("produces", [target], None)],
        )
        self.fixture.commit()

        with RuntimeGraphReader(self.fixture.database) as reader:
            node = reader.resolve_one(NodeSelector.by_id(target))
            scoped = ScopedTarget(ProfileScope(*COMMON), node)
            session = RuntimeGraphChainContinuation(reader, [scoped])
            document = session.step(1)["state"]
            root_id = document["root_target_ids"][0]
            document["targets"][root_id]["node"]["id"] = "fabricated"
            with self.assertRaisesRegex(
                RuntimeGraphQueryError, "target identity differs"
            ):
                RuntimeGraphChainContinuation.from_state(reader, document)

    def test_route_continuation_preserves_every_structural_boundary(
        self,
    ) -> None:
        raw_a = self.fixture.add_item("continuation-bound-raw-a")
        raw_b = self.fixture.add_item("continuation-bound-raw-b")
        target = self.fixture.add_item("continuation-bound-target")
        self.fixture.add_recipe(
            "continuation-bound-route-a",
            inputs=[("consumes", [raw_a, raw_b], None)],
            outputs=[("produces", [target], None)],
        )
        self.fixture.add_recipe(
            "continuation-bound-route-b",
            inputs=[],
            outputs=[("produces", [target], None)],
        )
        self.fixture.commit()

        options_rows = (
            ChainOptions(max_depth=0),
            ChainOptions(max_routes=1),
            ChainOptions(max_alternatives_per_slot=1),
            ChainOptions(max_visited_nodes=1),
        )
        with RuntimeGraphReader(self.fixture.database) as reader:
            node = reader.resolve_one(NodeSelector.by_id(target))
            scoped = ScopedTarget(ProfileScope(*COMMON), node)
            for options in options_rows:
                with self.subTest(options=options):
                    fresh = build_process_chain_from_targets(
                        reader,
                        [scoped],
                        options=options,
                    )
                    session = RuntimeGraphChainContinuation(
                        reader,
                        [scoped],
                        options=options,
                    )
                    while not session.complete:
                        segment = session.step(1)
                        session = RuntimeGraphChainContinuation.from_state(
                            reader,
                            segment["state"],
                        )
                    self.assertEqual(fresh, session.result())

    def test_route_result_validator_rejects_plausible_mutations(self) -> None:
        target = self.fixture.add_item("validator-target")
        self.fixture.add_recipe(
            "validator-route",
            outputs=[("produces", [target], None)],
        )
        result = self.query(target)
        validate_process_chain_result(result)

        route_drift = copy.deepcopy(result)
        route_drift["routes"][0]["id"] = "route:" + "0" * 64
        route_drift["subproblems"][0]["route_ids"][0] = (
            route_drift["routes"][0]["id"]
        )
        with self.assertRaisesRegex(
            RuntimeGraphQueryError, "route identity differs"
        ):
            validate_process_chain_result(route_drift)

        status_drift = copy.deepcopy(result)
        status_drift["subproblems"][0]["status"] = "pending"
        with self.assertRaisesRegex(
            RuntimeGraphQueryError, "pending subproblem"
        ):
            validate_process_chain_result(status_drift)

        truncation_drift = copy.deepcopy(result)
        truncation_drift["truncation"]["truncated"] = True
        with self.assertRaisesRegex(
            RuntimeGraphQueryError, "truncation differs"
        ):
            validate_process_chain_result(truncation_drift)

    @staticmethod
    def target_ids(result: dict[str, object]) -> list[str]:
        return [
            row["target"]["id"]
            for row in result["subproblems"]
        ]

    def test_linear_chain_is_a_memoized_route_dag(self) -> None:
        raw = self.fixture.add_item("raw")
        intermediate = self.fixture.add_item("intermediate")
        target = self.fixture.add_item("target")
        self.fixture.add_recipe(
            "make-intermediate",
            inputs=[("consumes", [raw], {"amount": 2})],
            outputs=[("produces", [intermediate], {"amount": 1})],
        )
        self.fixture.add_recipe(
            "make-target",
            inputs=[("consumes", [intermediate], {"amount": 3})],
            outputs=[("produces", [target], {"amount": 1})],
        )

        result = self.query(target)

        self.assertEqual(2, len(result["routes"]))
        self.assertEqual(
            {raw, intermediate, target},
            set(self.target_ids(result)),
        )
        self.assertEqual([], result["cycles"])
        self.assertFalse(result["truncation"]["truncated"])
        self.assertIn(
            "no-mechanical-producer",
            {row["reason"] for row in result["unresolved_leaves"]},
        )

    def test_exact_registered_stack_form_can_seed_construction_chain(
        self,
    ) -> None:
        raw = self.fixture.add_item("machine-part")
        form = self.fixture.add_item("registered-machine-stack")
        machine = self.fixture.add_node("machine", "registered-machine")
        form_edge = self.fixture.add_edge(
            "has_form",
            machine,
            form,
            attributes={"role": "registered_mte_stack_form"},
        )
        recipe = self.fixture.add_recipe(
            "construct-registered-machine",
            inputs=[("consumes", [raw], {"amount": 1})],
            outputs=[("produces", [form], {"amount": 1})],
        )

        result = self.query(form)

        self.assertEqual(
            {
                "key_kind": "runtime-node-id",
                "key_value": form,
            },
            result["target"]["selector"],
        )
        self.assertEqual(1, len(result["roots"]))
        root = next(
            row
            for row in result["subproblems"]
            if row["id"] == result["roots"][0]
        )
        self.assertEqual(form, root["target"]["id"])
        self.assertEqual(
            [recipe],
            [row["producer"]["id"] for row in result["routes"]],
        )
        self.assertEqual(machine, form_edge["subject"])
        self.assertEqual(form, form_edge["object"])

    def test_resolved_targets_share_roots_descendants_and_determinism(
        self,
    ) -> None:
        raw = self.fixture.add_item("shared-raw")
        shared = self.fixture.add_item("shared-component")
        root_a = self.fixture.add_item("construction-root-a")
        root_b = self.fixture.add_item("construction-root-b")
        shared_recipe = self.fixture.add_recipe(
            "make-shared-component",
            inputs=[("consumes", [raw], {"amount": 1})],
            outputs=[("produces", [shared], {"amount": 1})],
        )
        root_a_recipe = self.fixture.add_recipe(
            "make-construction-root-a",
            inputs=[("consumes", [shared], {"amount": 1})],
            outputs=[("produces", [root_a], {"amount": 1})],
        )
        root_b_recipe = self.fixture.add_recipe(
            "make-construction-root-b",
            inputs=[("consumes", [shared], {"amount": 2})],
            outputs=[("produces", [root_b], {"amount": 1})],
        )

        result = self.query_targets([root_b, root_a, root_a])
        reordered = self.query_targets([root_a, root_b])

        self.assertEqual(reordered, result)
        self.assertEqual(
            sorted([root_a, root_b]),
            [
                selector["key_value"]
                for selector in result["target"]["selectors"]
            ],
        )
        self.assertEqual(2, len(result["roots"]))
        self.assertEqual(
            1,
            self.target_ids(result).count(shared),
        )
        self.assertEqual(
            {shared_recipe, root_a_recipe, root_b_recipe},
            {row["producer"]["id"] for row in result["routes"]},
        )
        linked_shared_subproblems = []
        for route in result["routes"]:
            if route["producer"]["id"] not in {
                root_a_recipe,
                root_b_recipe,
            }:
                continue
            linked_shared_subproblems.append(
                route["ingredient_slots"]["slots"][0][
                    "alternatives"
                ]["items"][0]["subproblem_id"]
            )
        self.assertEqual(2, len(linked_shared_subproblems))
        self.assertEqual(1, len(set(linked_shared_subproblems)))

    def test_resolved_targets_share_one_global_route_budget(
        self,
    ) -> None:
        root_a = self.fixture.add_item("budget-root-a")
        root_b = self.fixture.add_item("budget-root-b")
        self.fixture.add_recipe(
            "make-budget-root-a",
            outputs=[("produces", [root_a], {"amount": 1})],
        )
        self.fixture.add_recipe(
            "make-budget-root-b",
            outputs=[("produces", [root_b], {"amount": 1})],
        )

        result = self.query_targets(
            [root_b, root_a],
            ChainOptions(max_routes=1),
        )

        self.assertEqual(2, len(result["roots"]))
        self.assertEqual(1, len(result["routes"]))
        status_by_target = {
            row["target"]["id"]: row["status"]
            for row in result["subproblems"]
        }
        self.assertEqual("expanded", status_by_target[root_a])
        self.assertEqual("truncated", status_by_target[root_b])
        self.assertEqual(
            [root_b],
            [
                row["target"]["id"]
                for row in result["unresolved_leaves"]
                if row["reason"] == "max-routes"
            ],
        )
        self.assertEqual(
            ["max-routes"],
            result["truncation"]["reasons"],
        )
        with self.assertRaisesRegex(
            RuntimeGraphQueryError,
            "must cover every explicit root: 1 < 2",
        ):
            self.query_targets(
                [root_a, root_b],
                ChainOptions(max_visited_nodes=1),
            )

    def test_resolved_target_provider_adds_roots_to_the_same_state(
        self,
    ) -> None:
        initial = self.fixture.add_item("provider-initial")
        discovered = self.fixture.add_item("provider-discovered")
        initial_recipe = self.fixture.add_recipe(
            "make-provider-initial",
            outputs=[("produces", [initial], {"amount": 1})],
        )
        discovered_recipe = self.fixture.add_recipe(
            "make-provider-discovered",
            outputs=[("produces", [discovered], {"amount": 1})],
        )
        self.fixture.commit()

        with RuntimeGraphReader(self.fixture.database) as reader:
            def scoped(identifier: str) -> ScopedTarget:
                node = reader.resolve_one(NodeSelector.by_id(identifier))
                return ScopedTarget(ProfileScope(*COMMON), node)

            contexts = []

            def provider(context: dict[str, object]) -> list[ScopedTarget]:
                contexts.append(context)
                return (
                    [scoped(discovered)]
                    if context["producer"]["id"] == initial_recipe
                    else []
                )

            result = build_process_chain_from_targets(
                reader,
                [scoped(initial)],
                additional_root_provider=provider,
            )

        self.assertEqual(2, len(result["roots"]))
        self.assertEqual(
            {initial, discovered},
            {
                row["target"]["id"]
                for row in result["subproblems"]
                if row["id"] in result["roots"]
            },
        )
        self.assertEqual(
            {initial_recipe, discovered_recipe},
            {row["producer"]["id"] for row in result["routes"]},
        )
        self.assertEqual(
            {initial_recipe, discovered_recipe},
            {row["producer"]["id"] for row in contexts},
        )

    def test_resolved_targets_are_validated_against_the_open_graph(
        self,
    ) -> None:
        target_id = self.fixture.add_item("validated-root")
        self.fixture.commit()

        with RuntimeGraphReader(self.fixture.database) as reader:
            node = reader.resolve_one(NodeSelector.by_id(target_id))
            scope = ProfileScope(COMMON[0], COMMON[1])
            with self.assertRaisesRegex(
                RuntimeGraphQueryError,
                "requires at least one target",
            ):
                build_process_chain_from_targets(reader, [])
            with self.assertRaisesRegex(
                RuntimeGraphQueryError,
                "must be a ScopedTarget",
            ):
                build_process_chain_from_targets(reader, [node])
            with self.assertRaisesRegex(
                RuntimeGraphQueryError,
                "scope differs from its node record",
            ):
                build_process_chain_from_targets(
                    reader,
                    [ScopedTarget(ProfileScope(HEI[0], HEI[1]), node)],
                )
            forged = {
                **node,
                "id": node["id"] + ":forged",
            }
            with self.assertRaisesRegex(
                RuntimeGraphQueryError,
                "is not an exact node in the open graph",
            ):
                build_process_chain_from_targets(
                    reader,
                    [ScopedTarget(scope, forged)],
                )

    def test_distinct_slots_are_and_and_alternatives_stay_nested_or(self) -> None:
        a = self.fixture.add_item("a")
        b = self.fixture.add_item("b")
        c = self.fixture.add_item("c")
        target = self.fixture.add_item("target")
        recipe = self.fixture.add_recipe(
            "and-or",
            inputs=[
                ("consumes", [a, b], {"amount": 1}),
                ("consumes", [c], {"amount": 4}),
            ],
            outputs=[("produces", [target], None)],
        )

        result = self.query(target)
        route = next(
            row for row in result["routes"] if row["producer"]["id"] == recipe
        )

        self.assertEqual("AND", route["ingredient_slots"]["mode"])
        slots = route["ingredient_slots"]["slots"]
        self.assertEqual(2, len(slots))
        self.assertEqual(["OR", "OR"], [
            row["alternatives"]["mode"] for row in slots
        ])
        self.assertEqual([2, 1], [
            len(row["alternatives"]["items"]) for row in slots
        ])
        self.assertEqual(1, len(result["routes"]))

    def test_slot_and_alternative_order_use_their_declared_ordinals(self) -> None:
        first = self.fixture.add_item("first")
        second = self.fixture.add_item("second")
        target = self.fixture.add_item("target")
        recipe = self.fixture.add_recipe(
            "declared-order",
            outputs=[("produces", [target], None)],
        )
        self.fixture.add_slot(
            recipe,
            "late-slot",
            "consumes",
            [second],
            ordinal=1,
            relationship_attributes={"ordinal": 0},
        )
        self.fixture.add_slot(
            recipe,
            "early-slot",
            "consumes",
            [second, first],
            ordinal=0,
            relationship_attributes={"ordinal": 9},
            alternative_attributes=[
                {"alternative_ordinal": 1},
                {"alternative_ordinal": 0},
            ],
        )

        route = self.query(target)["routes"][0]
        slots = route["ingredient_slots"]["slots"]

        self.assertEqual([0, 1], [
            row["slot"]["attributes"]["ordinal"] for row in slots
        ])
        self.assertEqual(
            [first, second],
            [
                row["target"]["id"]
                for row in slots[0]["alternatives"]["items"]
            ],
        )

    def test_non_exhaustive_representatives_are_a_match_domain_not_or(self) -> None:
        predicate = self.fixture.add_node(
            "ingredient",
            "authoritative-predicate",
        )
        representative = self.fixture.add_item("representative")
        target = self.fixture.add_item("target")
        recipe = self.fixture.add_recipe(
            "match-domain",
            outputs=[("produces", [target], None)],
        )
        self.fixture.add_slot(
            recipe,
            "domain-input",
            "consumes",
            [predicate, representative],
            ordinal=0,
            alternative_attributes=[
                {
                    "alternative_kind": "authoritative_match_domain",
                    "alternative_ordinal": 0,
                    "match_domain": True,
                    "public_representatives_exhaustive": False,
                },
                {
                    "alternative_kind": "public_representative",
                    "alternative_ordinal": 1,
                    "representative_only": True,
                    "public_representatives_exhaustive": False,
                },
            ],
        )

        slot = self.query(target)["routes"][0]["ingredient_slots"]["slots"][0]

        self.assertEqual("MATCH_DOMAIN", slot["alternatives"]["mode"])
        self.assertFalse(slot["alternatives"]["exhaustive"])
        self.assertEqual(
            [
                "authoritative-match-domain",
                "non-exhaustive-representative",
            ],
            [
                row["alternative_semantics"]
                for row in slot["alternatives"]["items"]
            ],
        )
        self.assertTrue(all(
            row["expansion"]["status"] == "boundary"
            for row in slot["alternatives"]["items"]
        ))

    def test_diamond_dependencies_share_one_subproblem(self) -> None:
        raw = self.fixture.add_item("raw")
        left = self.fixture.add_item("left")
        right = self.fixture.add_item("right")
        target = self.fixture.add_item("target")
        self.fixture.add_recipe(
            "make-left",
            inputs=[("consumes", [raw], None)],
            outputs=[("produces", [left], None)],
        )
        self.fixture.add_recipe(
            "make-right",
            inputs=[("consumes", [raw], None)],
            outputs=[("produces", [right], None)],
        )
        self.fixture.add_recipe(
            "combine",
            inputs=[
                ("consumes", [left], None),
                ("consumes", [right], None),
            ],
            outputs=[("produces", [target], None)],
        )

        result = self.query(target)
        raw_subproblems = [
            row
            for row in result["subproblems"]
            if row["target"]["id"] == raw
        ]
        raw_id = raw_subproblems[0]["id"]
        references = [
            alternative["subproblem_id"]
            for route in result["routes"]
            for slot in route["ingredient_slots"]["slots"]
            for alternative in slot["alternatives"]["items"]
            if alternative["target"]["id"] == raw
        ]

        self.assertEqual(1, len(raw_subproblems))
        self.assertEqual([raw_id, raw_id], references)

    def test_requires_is_a_reusable_requirement_not_consumption(self) -> None:
        catalyst = self.fixture.add_item("catalyst")
        feed = self.fixture.add_item("feed")
        target = self.fixture.add_item("target")
        self.fixture.add_recipe(
            "catalyzed",
            inputs=[
                ("requires", [catalyst], {"amount": 1}),
                ("consumes", [feed], {"amount": 5}),
            ],
            outputs=[("produces", [target], None)],
        )

        route = self.query(target)["routes"][0]

        self.assertEqual(
            [feed],
            [
                item["target"]["id"]
                for item in route["ingredient_slots"]["slots"][0][
                    "alternatives"
                ]["items"]
            ],
        )
        requirement = route["reusable_requirements"]["slots"][0]
        self.assertTrue(requirement["semantics"]["reusable"])
        self.assertEqual(
            catalyst,
            requirement["alternatives"]["items"][0]["target"]["id"],
        )

    def test_byproduct_and_chance_are_retained_without_upgrading_yield(self) -> None:
        feed = self.fixture.add_item("feed")
        target = self.fixture.add_item("target")
        byproduct = self.fixture.add_item("byproduct")
        self.fixture.add_recipe(
            "with-byproduct",
            inputs=[("consumes", [feed], None)],
            outputs=[
                ("produces", [target], {"amount": 1}),
                (
                    "may_produce",
                    [byproduct],
                    {"amount": 1, "chance": 2500, "chance_scale": 10000},
                ),
            ],
        )

        route = self.query(target)["routes"][0]
        byproduct_row = route["byproducts"][0]
        without_chance = self.query(
            target,
            ChainOptions(include_chanced_outputs=False),
        )

        self.assertTrue(byproduct_row["semantics"]["conditional"])
        self.assertEqual(2500, byproduct_row["chance"]["slot"]["chance"])
        self.assertEqual([], without_chance["routes"][0]["byproducts"])

    def test_nonchance_may_produce_is_not_filtered_by_chance_policy(self) -> None:
        target = self.fixture.add_item("target")
        self.fixture.add_recipe(
            "conditional-without-probability",
            outputs=[("may_produce", [target], {"amount_formula": "context"})],
        )

        result = self.query(
            target,
            ChainOptions(include_chanced_outputs=False),
        )

        self.assertEqual(1, len(result["routes"]))

    def test_chanced_target_route_is_conditional_and_policy_filterable(
        self,
    ) -> None:
        target = self.fixture.add_item("chanced-target")
        self.fixture.add_recipe(
            "chanced-target",
            outputs=[
                (
                    "may_produce",
                    [target],
                    {"chance": 1250, "chance_scale": 10000},
                )
            ],
        )

        included = self.query(target)
        excluded = self.query(
            target,
            ChainOptions(include_chanced_outputs=False),
        )

        production = included["routes"][0]["production"]
        self.assertTrue(production["semantics"]["conditional"])
        self.assertFalse(production["semantics"]["guaranteed"])
        self.assertEqual(1250, production["chance"]["slot"]["chance"])
        self.assertEqual([], excluded["routes"])
        self.assertIn(
            "chanced-outputs-disabled",
            {row["reason"] for row in excluded["unresolved_leaves"]},
        )

    def test_tarjan_reports_indirect_and_self_cycles(self) -> None:
        a = self.fixture.add_item("a")
        b = self.fixture.add_item("b")
        self.fixture.add_recipe(
            "a-from-b",
            inputs=[("consumes", [b], None)],
            outputs=[("produces", [a], None)],
        )
        self.fixture.add_recipe(
            "b-from-a",
            inputs=[("consumes", [a], None)],
            outputs=[("produces", [b], None)],
        )
        self.fixture.add_recipe(
            "b-from-b",
            inputs=[("consumes", [b], None)],
            outputs=[("produces", [b], None)],
        )

        result = self.query(a)
        member_counts = sorted(
            len(row["subproblem_ids"]) for row in result["cycles"]
        )

        # Tarjan correctly emits one SCC containing a and b; its retained
        # dependency list also contains the b self-loop.
        self.assertEqual([2], member_counts)
        cycle = result["cycles"][0]
        self.assertTrue(
            any(
                edge["from"] == edge["to"]
                for edge in cycle["dependencies"]
            )
        )
        self.assertIn(
            "cycle",
            {row["reason"] for row in result["unresolved_leaves"]},
        )

    def test_procedural_rule_is_an_explicit_symbolic_boundary(self) -> None:
        symbolic = self.fixture.add_node(
            "ingredient",
            "runtime-selected-feed",
            attributes={"amount_formula": "runtime_selected"},
        )
        target = self.fixture.add_item("target")
        rule = self.fixture.add_process_rule(
            "procedural",
            inputs=[("consumes", [symbolic], {"amount_formula": "runtime"})],
            outputs=[("produces", [target], {"amount_formula": "runtime"})],
        )

        result = self.query(target)
        route = result["routes"][0]
        disabled = self.query(
            target,
            ChainOptions(include_procedural_rules=False),
        )

        self.assertEqual(rule, route["producer"]["id"])
        self.assertEqual(
            "procedural-or-symbolic-boundary",
            route["boundaries"][0]["status"],
        )
        alternative = route["ingredient_slots"]["slots"][0][
            "alternatives"
        ]["items"][0]
        self.assertEqual("boundary", alternative["expansion"]["status"])
        self.assertEqual([], disabled["routes"])
        self.assertIn(
            "procedural-rules-disabled",
            {row["reason"] for row in disabled["unresolved_leaves"]},
        )

    def test_unbound_procedural_rule_does_not_fabricate_machine_evidence(
        self,
    ) -> None:
        target = self.fixture.add_item("target")
        rule = self.fixture.add_node("process_rule", "unbound-rule")
        self.fixture.add_slot(
            rule,
            "unbound-output",
            "produces",
            [target],
            ordinal=0,
        )

        route = self.query(target)["routes"][0]

        self.assertEqual([], route["mechanics"]["execution"])
        self.assertIn(
            "procedural-no-machine-binding",
            {row["status"] for row in route["boundaries"]},
        )

    def test_each_structural_bound_has_its_exact_reason(self) -> None:
        a = self.fixture.add_item("a")
        b = self.fixture.add_item("b")
        target = self.fixture.add_item("target")
        self.fixture.add_recipe(
            "route-a",
            inputs=[("consumes", [a, b], None)],
            outputs=[("produces", [target], None)],
        )
        self.fixture.add_recipe(
            "route-b",
            inputs=[],
            outputs=[("produces", [target], None)],
        )

        depth = self.query(target, ChainOptions(max_depth=0))
        routes = self.query(target, ChainOptions(max_routes=1))
        alternatives = self.query(
            target,
            ChainOptions(max_alternatives_per_slot=1),
        )
        visited = self.query(
            target,
            ChainOptions(max_visited_nodes=1),
        )

        self.assertEqual(["max-depth"], depth["truncation"]["reasons"])
        self.assertEqual(["max-routes"], routes["truncation"]["reasons"])
        self.assertIn(
            "max-alternatives-per-slot",
            alternatives["truncation"]["reasons"],
        )
        self.assertEqual(
            ["max-visited-nodes"],
            visited["truncation"]["reasons"],
        )
        self.assertEqual(1, len(routes["routes"]))
        self.assertEqual(1, len(
            alternatives["routes"][0]["ingredient_slots"]["slots"][0][
                "alternatives"
            ]["items"]
        ))
        self.assertEqual([], visited["routes"])

    def test_depth_bound_is_not_reported_when_no_expansion_was_possible(
        self,
    ) -> None:
        raw = self.fixture.add_item("raw")

        result = self.query(raw, ChainOptions(max_depth=0))

        self.assertFalse(result["truncation"]["truncated"])
        self.assertEqual([], result["truncation"]["reasons"])
        self.assertIn(
            "no-mechanical-producer",
            {row["reason"] for row in result["unresolved_leaves"]},
        )

    def test_visited_bound_keeps_an_already_admitted_route_partial(self) -> None:
        feed = self.fixture.add_item("feed")
        target = self.fixture.add_item("target")
        self.fixture.add_recipe(
            "partial",
            inputs=[("consumes", [feed], None)],
            outputs=[("produces", [target], None)],
        )

        result = self.query(
            target,
            ChainOptions(max_visited_nodes=3),
        )

        self.assertEqual(1, len(result["routes"]))
        self.assertIn(
            "max-visited-nodes",
            result["truncation"]["reasons"],
        )
        self.assertEqual(
            target,
            result["routes"][0]["production"]["matched_alternatives"][0][
                "node"
            ]["id"],
        )

    def test_result_is_deterministic_and_canonical_json_compatible(self) -> None:
        a = self.fixture.add_item("a")
        b = self.fixture.add_item("b")
        target = self.fixture.add_item("target")
        self.fixture.add_recipe(
            "z-route",
            inputs=[("consumes", [b, a], None)],
            outputs=[("produces", [target], None)],
        )
        self.fixture.add_recipe(
            "a-route",
            inputs=[],
            outputs=[("produces", [target], None)],
        )

        first = self.query(target)
        self.fixture.connection.execute("PRAGMA reverse_unordered_selects = ON")
        second = self.query(target)

        first_bytes = graph.canonical_json_payload(first)
        second_bytes = graph.canonical_json_payload(second)
        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(
            first,
            json.loads(first_bytes),
        )

    def test_hei_only_and_consult_only_evidence_never_become_routes(self) -> None:
        hei_target = self.fixture.add_item("hei-target", scope=HEI)
        wrapper = self.fixture.add_node(
            "recipe_wrapper",
            "wrapper",
            scope=HEI,
        )
        self.fixture.add_slot(
            wrapper,
            "wrapper-output",
            "produces",
            [hei_target],
            ordinal=0,
            scope=HEI,
        )

        consult_target = self.fixture.add_item("consult-target")
        self.fixture.add_recipe(
            "consult-only",
            outputs=[("produces", [consult_target], None)],
            execution="consults_recipe_map",
        )

        hei_result = self.query(
            hei_target,
            scopes=ProfileScope(*HEI),
        )
        consult_result = self.query(consult_target)

        self.assertEqual([], hei_result["routes"])
        self.assertEqual([], consult_result["routes"])
        self.assertIn(
            "no-mechanical-producer",
            {row["reason"] for row in hei_result["unresolved_leaves"]},
        )
        self.assertTrue(
            {
                row["reason"]
                for row in consult_result["unresolved_leaves"]
            }
            & {"missing-execution-evidence", "no-mechanical-producer"}
        )

    def test_scope_iterables_are_normalized_and_options_fail_closed(self) -> None:
        target = self.fixture.add_item("target")
        self.fixture.commit()
        with RuntimeGraphReader(self.fixture.database) as reader:
            result = build_process_chain(
                reader,
                NodeSelector.by_id(target),
                graph_scope=[ProfileScope(*COMMON)],
            )

        self.assertEqual(
            [{"profile": COMMON[0], "physical_side": COMMON[1]}],
            result["profile_scope"],
        )
        for kwargs in (
            {"max_depth": -1},
            {"max_routes": 0},
            {"max_alternatives_per_slot": 0},
            {"max_visited_nodes": 0},
            {"include_chanced_outputs": 1},
            {"include_procedural_rules": "yes"},
        ):
            with self.assertRaises(RuntimeGraphQueryError):
                ChainOptions(**kwargs)


if __name__ == "__main__":
    unittest.main()
