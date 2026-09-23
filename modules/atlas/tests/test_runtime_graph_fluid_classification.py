#!/usr/bin/env python3

from __future__ import annotations

from collections import Counter
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parent))

import workbench_atlas.runtime_graph as graph
import workbench_atlas.runtime_graph_domain_query as domain
import workbench_atlas.runtime_graph_fluid_classification as fluids
import workbench_atlas.runtime_graph_query as query


SCOPE = domain.ProfileScope("COMMON_FINAL_STATE", "DEDICATED_SERVER")


class FluidFixture:
    def __init__(self) -> None:
        self.nodes: list[tuple[object, ...]] = []
        self.keys: list[tuple[object, ...]] = []
        self.edges: list[tuple[object, ...]] = []
        self.edge_sequence = 0
        self.known_nodes: set[str] = set()

    @staticmethod
    def scope() -> dict[str, str]:
        return {
            "snapshot_id": graph.SNAPSHOT_ID,
            "profile": SCOPE.profile,
            "physical_side": SCOPE.physical_side,
            "adapter": "fixture",
        }

    def add_node(self, identifier: str, kind: str) -> str:
        if identifier in self.known_nodes:
            return identifier
        self.known_nodes.add(identifier)
        row = {
            "record_type": "node",
            "id": identifier,
            "kind": kind,
            "scope": self.scope(),
            "attributes": {},
        }
        self.nodes.append(
            (
                identifier,
                SCOPE.profile,
                SCOPE.physical_side,
                "fixture",
                kind,
                graph.canonical_json_payload(row).decode("utf-8"),
            )
        )
        return identifier

    def add_key(self, node_id: str, kind: str, value: str) -> None:
        self.keys.append(
            (node_id, kind, value, SCOPE.profile, SCOPE.physical_side)
        )

    def add_edge(
        self,
        predicate: str,
        subject: str,
        object_: str,
        attributes: dict[str, object] | None = None,
    ) -> str:
        self.edge_sequence += 1
        edge_attributes = {
            "fixture_sequence": self.edge_sequence,
            **({} if attributes is None else attributes),
        }
        row: dict[str, object] = {
            "record_type": "edge",
            "predicate": predicate,
            "subject": subject,
            "object": object_,
            "scope": self.scope(),
            "attributes": edge_attributes,
        }
        row["id"] = graph.edge_id(row)
        self.edges.append(
            (
                row["id"],
                SCOPE.profile,
                SCOPE.physical_side,
                "fixture",
                predicate,
                subject,
                object_,
                graph.canonical_json_payload(row).decode("utf-8"),
            )
        )
        return str(row["id"])

    def add_fluid(
        self,
        suffix: str,
        key_values: tuple[str, ...] = (),
        *,
        duplicate_variant_path: bool = False,
    ) -> tuple[str, str]:
        base = self.add_node(f"fluid:{suffix}", "fluid")
        variant = self.add_node(f"fluid_variant:{suffix}", "fluid_variant")
        for value in key_values:
            self.add_key(base, "fluid-resource-location", value)
        self.add_edge(
            "has_variant",
            base,
            variant,
            {"source": "forge_fluids"},
        )
        if duplicate_variant_path:
            self.add_edge(
                "has_variant",
                base,
                variant,
                {"source": "gt_materials"},
            )
        return base, variant

    def add_material_edge(
        self,
        variant: str,
        material: str,
        role: str,
        *,
        storage_key: str | None = None,
    ) -> str:
        self.add_node(material, "material")
        attributes: dict[str, object] = {"role": role}
        if storage_key is not None:
            attributes["storage_key"] = storage_key
        return self.add_edge("has_material", variant, material, attributes)

    def add_occurrence(
        self,
        suffix: str,
        alternative: str,
        predicate: str,
        *,
        owner_kind: str = "recipe",
        lookup_active: bool = True,
        second_alternative: str | None = None,
    ) -> str:
        owner = self.add_node(f"{owner_kind}:{suffix}", owner_kind)
        slot = self.add_node(f"ingredient_slot:{suffix}", "ingredient_slot")
        self.add_edge(predicate, owner, slot, {"ordinal": 0})
        self.add_edge(
            "accepts_alternative",
            slot,
            alternative,
            {"alternative_ordinal": 0},
        )
        if second_alternative is not None:
            self.add_edge(
                "accepts_alternative",
                slot,
                second_alternative,
                {"alternative_ordinal": 1},
            )
        if owner_kind == "recipe":
            recipe_map = self.add_node(f"recipe_map:{suffix}", "recipe_map")
            self.add_edge(
                "has_recipe",
                recipe_map,
                owner,
                {"lookup_active": lookup_active},
            )
        return owner

    def write(self, path: Path, *, reverse: bool = False) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                """
                PRAGMA journal_mode = OFF;
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
                CREATE INDEX nodes_scope_kind
                    ON nodes(profile, physical_side, kind);
                CREATE TABLE node_keys (
                    node_id TEXT NOT NULL,
                    key_kind TEXT NOT NULL,
                    key_value TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    physical_side TEXT NOT NULL,
                    PRIMARY KEY (
                        key_kind,
                        key_value,
                        profile,
                        physical_side,
                        node_id
                    )
                ) WITHOUT ROWID;
                CREATE INDEX node_keys_node
                    ON node_keys(node_id, key_kind, key_value);
                CREATE INDEX node_keys_lookup
                    ON node_keys(key_kind, key_value);
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
                CREATE INDEX edges_predicate_subject
                    ON edges(predicate, subject);
                CREATE INDEX edges_predicate_object
                    ON edges(predicate, object);
                CREATE INDEX edges_predicate_subject_object_id
                    ON edges(predicate, subject, object, id);
                CREATE INDEX edges_predicate_object_subject_id
                    ON edges(predicate, object, subject, id);
                """
            )
            order = (lambda values: list(reversed(values))) if reverse else list
            connection.executemany(
                "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?)",
                order(self.nodes),
            )
            connection.executemany(
                "INSERT INTO node_keys VALUES (?, ?, ?, ?, ?)",
                order(self.keys),
            )
            connection.executemany(
                "INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                order(self.edges),
            )
            connection.commit()
        finally:
            connection.close()


def populated_fixture() -> tuple[FluidFixture, dict[str, str]]:
    fixture = FluidFixture()
    ids: dict[str, str] = {}

    flow, flow_variant = fixture.add_fluid(
        "flow",
        ("example:flow",),
        duplicate_variant_path=True,
    )
    ids.update(flow=flow, flow_variant=flow_variant)
    fixture.add_material_edge(
        flow_variant,
        "material:a",
        "effective_fluid_unifier_lookup",
    )
    fixture.add_material_edge(
        flow_variant,
        "material:a",
        "material_fluid_storage_form",
        storage_key="liquid",
    )
    fixture.add_occurrence(
        "flow-produces",
        flow_variant,
        "produces",
        second_alternative=flow,
    )
    fixture.add_occurrence(
        "flow-may-produce",
        flow_variant,
        "may_produce",
        owner_kind="process_rule",
    )
    fixture.add_occurrence("flow-consumes", flow_variant, "consumes")
    fixture.add_occurrence(
        "flow-may-consume",
        flow_variant,
        "may_consume",
        owner_kind="worldgen_deposit",
    )
    fixture.add_occurrence("flow-requires", flow_variant, "requires")
    ids["inactive"] = fixture.add_occurrence(
        "flow-inactive",
        flow_variant,
        "produces",
        lookup_active=False,
    )

    reusable, reusable_variant = fixture.add_fluid(
        "reusable",
        ("example:reusable",),
    )
    fixture.add_occurrence(
        "reusable-only",
        reusable_variant,
        "requires",
        owner_kind="process_rule",
    )
    ids["reusable"] = reusable

    source, source_variant = fixture.add_fluid("source")
    fixture.add_material_edge(
        source_variant,
        "material:a",
        "material_fluid_storage_form",
        storage_key="source",
    )
    fixture.add_occurrence(
        "source-only",
        source_variant,
        "produces",
        owner_kind="recipe_rule",
    )
    ids["source"] = source

    sink, sink_variant = fixture.add_fluid(
        "sink",
        ("example:sink-a", "example:sink-b"),
    )
    fixture.add_material_edge(
        sink_variant,
        "material:a",
        "material_fluid_storage_form",
        storage_key="a",
    )
    fixture.add_material_edge(
        sink_variant,
        "material:b",
        "material_fluid_storage_form",
        storage_key="b",
    )
    fixture.add_occurrence(
        "sink-only",
        sink_variant,
        "consumes",
        owner_kind="worldgen_deposit",
    )
    ids["sink"] = sink

    reverse_only, reverse_only_variant = fixture.add_fluid(
        "reverse-only",
        ("example:reverse-only",),
    )
    fixture.add_material_edge(
        reverse_only_variant,
        "material:a",
        "effective_fluid_unifier_lookup",
    )
    ids["reverse_only"] = reverse_only

    reverse_conflict, reverse_conflict_variant = fixture.add_fluid(
        "reverse-conflict",
        ("example:reverse-conflict",),
    )
    fixture.add_material_edge(
        reverse_conflict_variant,
        "material:a",
        "effective_fluid_unifier_lookup",
    )
    fixture.add_material_edge(
        reverse_conflict_variant,
        "material:b",
        "material_fluid_storage_form",
        storage_key="conflict",
    )
    ids["reverse_conflict"] = reverse_conflict

    multi_conflict, multi_conflict_variant = fixture.add_fluid(
        "multi-conflict",
        ("example:multi-conflict",),
    )
    fixture.add_material_edge(
        multi_conflict_variant,
        "material:a",
        "effective_fluid_unifier_lookup",
    )
    fixture.add_material_edge(
        multi_conflict_variant,
        "material:a",
        "material_fluid_storage_form",
        storage_key="a",
    )
    fixture.add_material_edge(
        multi_conflict_variant,
        "material:b",
        "material_fluid_storage_form",
        storage_key="b",
    )
    ids["multi_conflict"] = multi_conflict
    return fixture, ids


class RuntimeGraphFluidClassificationTests(unittest.TestCase):
    def database(self, *, reverse: bool = False) -> tuple[Path, dict[str, str]]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "runtime-graph.sqlite"
        fixture, ids = populated_fixture()
        fixture.write(path, reverse=reverse)
        return path, ids

    @staticmethod
    def rows_by_id(
        result: fluids.FluidClassificationResult,
    ) -> dict[str, dict[str, object]]:
        return {str(row["fluid_id"]): row for row in result.rows}

    def test_counts_match_domain_queries_and_reusable_is_not_a_sink(self) -> None:
        database, ids = self.database()
        selector = query.NodeSelector.by_key(
            "fluid-resource-location",
            "example:flow",
            kind="fluid",
        )
        with query.RuntimeGraphReader(database) as reader:
            result = fluids.classify_fluids(reader, SCOPE)
            producers = domain.find_producers(
                reader,
                selector,
                page=query.PageRequest(limit=100),
                graph_scope=domain.GraphScope.of(SCOPE),
            )
            consumers = domain.find_consumers(
                reader,
                selector,
                page=query.PageRequest(limit=100),
                graph_scope=domain.GraphScope.of(SCOPE),
            )

        rows = self.rows_by_id(result)
        flow = rows[ids["flow"]]
        domain_counts = Counter(
            item["owner_relationship"]["predicate"]
            for item in (*producers.page.items, *consumers.page.items)
        )
        self.assertEqual(
            {predicate: domain_counts[predicate] for predicate in fluids.INCIDENCE_PREDICATES},
            flow["incidence"]["predicate_counts"],
        )
        self.assertEqual(2, producers.page.total)
        self.assertEqual(3, consumers.page.total)
        self.assertEqual(
            {
                "produces": 1,
                "may_produce": 1,
                "consumes": 1,
                "may_consume": 1,
                "requires": 1,
            },
            flow["incidence"]["predicate_counts"],
        )
        self.assertEqual("bidirectional", flow["incidence"]["classification"])
        self.assertEqual(
            [ids["flow_variant"]],
            flow["match_closure"]["variant_node_ids"],
        )
        self.assertEqual(
            2,
            len(flow["match_closure"]["has_variant_relationship_ids"]),
        )
        self.assertTrue(
            flow["incidence"]["flags"]["procedural_rule_producer"]
        )
        self.assertFalse(
            flow["incidence"]["flags"]["procedural_rule_consumer"]
        )
        self.assertTrue(
            flow["incidence"]["flags"]["worldgen_generative_consumer"]
        )

        reusable = rows[ids["reusable"]]
        self.assertEqual("isolated", reusable["incidence"]["classification"])
        self.assertEqual(0, reusable["incidence"]["consumer_count"])
        self.assertEqual(1, reusable["incidence"]["requirement_count"])
        self.assertTrue(reusable["incidence"]["flags"]["reusable"])
        self.assertTrue(
            reusable["incidence"]["flags"]["procedural_rule_requirement"]
        )
        self.assertEqual(
            "source-only",
            rows[ids["source"]]["incidence"]["classification"],
        )
        self.assertEqual(
            "sink-only",
            rows[ids["sink"]]["incidence"]["classification"],
        )
        self.assertEqual(
            {
                "bidirectional": 1,
                "source-only": 1,
                "sink-only": 1,
                "isolated": 4,
            },
            result.summary["incidence_classification_counts"],
        )
        self.assertEqual(
            {
                "produces": 2,
                "may_produce": 1,
                "consumes": 2,
                "may_consume": 1,
                "requires": 2,
            },
            result.summary["predicate_occurrence_counts"],
        )
        self.assertEqual(
            3,
            result.summary["procedural_rule_occurrence_count"],
        )
        self.assertEqual(3, result.summary["procedural_rule_fluid_count"])
        self.assertEqual(
            2,
            result.summary["worldgen_generative_occurrence_count"],
        )
        self.assertEqual(
            2,
            result.summary["worldgen_generative_fluid_count"],
        )

    def test_material_identity_and_missing_key_states_are_explicit(self) -> None:
        database, ids = self.database()
        with query.RuntimeGraphReader(database) as reader:
            result = fluids.classify_fluids(reader, SCOPE)

        rows = self.rows_by_id(result)
        expected = {
            ids["flow"]: "consistent",
            ids["reusable"]: "no_identity",
            ids["source"]: "structural_only",
            ids["sink"]: "ambiguous_structural_forms",
            ids["reverse_only"]: "reverse_only",
            ids["reverse_conflict"]: "reverse_structural_conflict",
            ids["multi_conflict"]: (
                "reverse_with_conflicting_structural_forms"
            ),
        }
        self.assertEqual(
            expected,
            {
                identifier: rows[identifier]["material_identity"]["status"]
                for identifier in expected
            },
        )
        self.assertEqual(
            {status: 1 for status in fluids.MATERIAL_IDENTITY_STATUSES},
            result.summary["material_identity_status_counts"],
        )
        flow_identity = rows[ids["flow"]]["material_identity"]
        self.assertEqual(1, flow_identity["form_count"])
        self.assertEqual(1, flow_identity["candidate_material_count"])
        self.assertEqual(
            "effective_fluid_unifier_lookup",
            flow_identity["reverse"]["role"],
        )
        self.assertEqual(1, flow_identity["reverse"]["relationship_count"])
        self.assertEqual(
            ["material:a"],
            flow_identity["structural_forms"]["material_ids"],
        )

        missing = rows[ids["source"]]
        self.assertEqual("missing", missing["fluid_resource_location_status"])
        self.assertIsNone(missing["fluid_resource_location"])
        self.assertEqual([], missing["fluid_resource_location_values"])
        ambiguous = rows[ids["sink"]]
        self.assertEqual(
            "ambiguous", ambiguous["fluid_resource_location_status"]
        )
        self.assertIsNone(ambiguous["fluid_resource_location"])
        self.assertEqual(
            ["example:sink-a", "example:sink-b"],
            ambiguous["fluid_resource_location_values"],
        )
        self.assertEqual(
            {"exact": 5, "missing": 1, "ambiguous": 1},
            result.summary["fluid_resource_location_status_counts"],
        )

    def test_canonical_output_is_insertion_and_sql_order_independent(self) -> None:
        first, _ = self.database()
        second, _ = self.database(reverse=True)
        traced: list[str] = []
        with query.RuntimeGraphReader(first) as reader:
            reader.connection.execute("PRAGMA reverse_unordered_selects = ON")
            reader.connection.set_trace_callback(traced.append)
            first_result = fluids.classify_fluids(reader, SCOPE)
        with query.RuntimeGraphReader(second) as reader:
            second_result = fluids.classify_fluids(reader, SCOPE)

        self.assertEqual(first_result.canonical_bytes(), second_result.canonical_bytes())
        self.assertEqual(first_result.sha256, second_result.sha256)
        self.assertEqual(64, len(first_result.sha256))

        with query.RuntimeGraphReader(first) as reader:
            one_at_a_time = fluids.classify_fluids(
                reader,
                SCOPE,
                fluids.FluidClassificationBounds(
                    metadata_chunk_size=1,
                    incidence_match_chunk_size=1,
                ),
            )
        self.assertEqual(first_result.canonical_bytes(), one_at_a_time.canonical_bytes())
        self.assertEqual(first_result.sha256, one_at_a_time.sha256)
        semantic = first_result.to_dict()

        def keys(value: object) -> set[str]:
            if isinstance(value, dict):
                return set(value) | set().union(*(keys(item) for item in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(item) for item in value), set())
            return set()

        semantic_keys = keys(semantic)
        self.assertFalse({"elapsed", "duration", "wall_clock"} & semantic_keys)
        incidence_statements = [
            statement
            for statement in traced
            if "FROM edges AS alternative_edge" in statement
        ]
        self.assertEqual(len(first_result.rows), len(incidence_statements))
        self.assertTrue(all("WITH " not in row.upper() for row in incidence_statements))

        # The synthetic schema exposes the same selective index as the retained
        # projection.  Confirm the hot query enters by predicate + exact target.
        connection = sqlite3.connect(first)
        try:
            plan = connection.execute(
                "EXPLAIN QUERY PLAN " + incidence_statements[0]
            ).fetchall()
        finally:
            connection.close()
        self.assertTrue(
            any(
                "SEARCH alternative_edge USING INDEX edges_predicate_" in str(row[3])
                and "object=?" in str(row[3])
                for row in plan
            ),
            plan,
        )

    def test_structural_bounds_fail_closed_above_the_real_dataset_scale(self) -> None:
        self.assertGreater(
            fluids.DEFAULT_FLUID_CLASSIFICATION_BOUNDS.max_fluids,
            2_448,
        )
        database, _ = self.database()
        with query.RuntimeGraphReader(database) as reader:
            with self.assertRaisesRegex(
                query.RuntimeGraphQueryError,
                "max_fluids",
            ):
                fluids.classify_fluids(
                    reader,
                    SCOPE,
                    fluids.FluidClassificationBounds(max_fluids=6),
                )
        with query.RuntimeGraphReader(database) as reader:
            with self.assertRaisesRegex(
                query.RuntimeGraphQueryError,
                "max_variants_per_fluid",
            ):
                fluids.classify_fluids(
                    reader,
                    SCOPE,
                    fluids.FluidClassificationBounds(
                        max_variants_per_fluid=0
                    ),
                )
        with self.assertRaisesRegex(
            query.RuntimeGraphQueryError,
            "metadata_chunk_size",
        ):
            fluids.FluidClassificationBounds(metadata_chunk_size=901)
        with self.assertRaisesRegex(
            query.RuntimeGraphQueryError,
            "SQLite integer bound",
        ):
            fluids.FluidClassificationBounds(max_fluids=10**100)
        with query.RuntimeGraphReader(database) as reader:
            with self.assertRaisesRegex(
                query.RuntimeGraphQueryError,
                "explicit ProfileScope",
            ):
                fluids.classify_fluids(reader, None)  # type: ignore[arg-type]

    def test_material_identity_never_synthesizes_across_fluid_variants(self) -> None:
        fixture = FluidFixture()
        fluid_id, first_variant = fixture.add_fluid(
            "split-identity",
            ("example:split-identity",),
        )
        second_variant = fixture.add_node(
            "fluid_variant:split-identity-second",
            "fluid_variant",
        )
        fixture.add_edge(
            "has_variant",
            fluid_id,
            second_variant,
            {"source": "second-observed-variant"},
        )
        fixture.add_material_edge(
            first_variant,
            "material:a",
            "effective_fluid_unifier_lookup",
        )
        fixture.add_material_edge(
            second_variant,
            "material:a",
            "material_fluid_storage_form",
            storage_key="liquid",
        )
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        database = Path(temporary.name) / "runtime-graph.sqlite"
        fixture.write(database)

        with query.RuntimeGraphReader(database) as reader:
            with self.assertRaisesRegex(
                query.RuntimeGraphQueryError,
                "exactly one distinct fluid_variant",
            ):
                fluids.classify_fluids(reader, SCOPE)

    def test_material_identity_uses_the_single_variant_not_the_base(self) -> None:
        fixture = FluidFixture()
        fluid_id, variant_id = fixture.add_fluid(
            "variant-authority",
            ("example:variant-authority",),
        )
        fixture.add_material_edge(
            variant_id,
            "material:a",
            "effective_fluid_unifier_lookup",
        )
        fixture.add_material_edge(
            variant_id,
            "material:a",
            "material_fluid_storage_form",
            storage_key="liquid",
        )
        fixture.add_material_edge(
            fluid_id,
            "material:b",
            "material_fluid_storage_form",
            storage_key="invalid-base-evidence",
        )
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        database = Path(temporary.name) / "runtime-graph.sqlite"
        fixture.write(database)

        with query.RuntimeGraphReader(database) as reader:
            result = fluids.classify_fluids(reader, SCOPE)

        identity = result.rows[0]["material_identity"]
        self.assertEqual("consistent", identity["status"])
        self.assertEqual(
            [variant_id],
            sorted({row["subject_id"] for row in identity["relationships"]}),
        )

    def test_fluid_variant_ownership_must_be_injective(self) -> None:
        fixture = FluidFixture()
        _, shared_variant = fixture.add_fluid(
            "first-owner",
            ("example:first-owner",),
        )
        second_fluid = fixture.add_node("fluid:second-owner", "fluid")
        fixture.add_key(
            second_fluid,
            "fluid-resource-location",
            "example:second-owner",
        )
        fixture.add_edge(
            "has_variant",
            second_fluid,
            shared_variant,
            {"source": "hostile-shared-variant"},
        )
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        database = Path(temporary.name) / "runtime-graph.sqlite"
        fixture.write(database)

        with query.RuntimeGraphReader(database) as reader:
            with self.assertRaisesRegex(
                query.RuntimeGraphQueryError,
                "injective fluid_variant ownership",
            ):
                fluids.classify_fluids(reader, SCOPE)

    def test_streamed_identity_bound_stops_before_exhausting_rows(self) -> None:
        database, ids = self.database()
        with query.RuntimeGraphReader(database) as reader:
            classifier = fluids.RuntimeGraphFluidClassifier(
                reader,
                fluids.FluidClassificationBounds(
                    max_identity_keys_per_fluid=2,
                ),
            )
            yielded = 0

            def hostile_rows(*_: object, **__: object):
                nonlocal yielded
                for index in range(1_000):
                    yielded += 1
                    yield (ids["flow"], f"example:key-{index}")

            classifier._rows = hostile_rows  # type: ignore[method-assign]
            with self.assertRaisesRegex(
                query.RuntimeGraphQueryError,
                "raw max_identity_keys_per_fluid",
            ):
                classifier._identity_keys((ids["flow"],))
            self.assertEqual(3, yielded)


if __name__ == "__main__":
    unittest.main()
