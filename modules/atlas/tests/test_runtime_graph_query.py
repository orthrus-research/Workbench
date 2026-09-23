#!/usr/bin/env python3

from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parent))

import workbench_atlas.runtime_graph as graph
import workbench_atlas.runtime_graph_query as query


class RuntimeGraphQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "runtime-graph.sqlite"
        self.connection = sqlite3.connect(self.database)
        self.addCleanup(self.connection.close)
        self.connection.executescript(
            """
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
            CREATE INDEX node_keys_node ON node_keys(node_id);
            CREATE INDEX node_keys_lookup ON node_keys(key_kind, key_value);
            """
        )

    @staticmethod
    def scope(adapter: str) -> dict[str, str]:
        return {
            "snapshot_id": graph.SNAPSHOT_ID,
            "profile": "COMMON_FINAL_STATE",
            "physical_side": "CLIENT",
            "adapter": adapter,
        }

    def add_node(
        self,
        identifier: str,
        kind: str,
        adapter: str,
        attributes: dict[str, object] | None = None,
    ) -> None:
        row = {
            "record_type": "node",
            "id": identifier,
            "kind": kind,
            "scope": self.scope(adapter),
            "attributes": {} if attributes is None else attributes,
        }
        self.connection.execute(
            "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?)",
            (
                identifier,
                "COMMON_FINAL_STATE",
                "CLIENT",
                adapter,
                kind,
                graph.canonical_json_payload(row).decode("utf-8"),
            ),
        )

    def add_edge(
        self,
        predicate: str,
        subject: str,
        object_: str,
        adapter: str,
        attributes: dict[str, object] | None = None,
    ) -> None:
        row: dict[str, object] = {
            "record_type": "edge",
            "predicate": predicate,
            "subject": subject,
            "object": object_,
            "scope": self.scope(adapter),
            "attributes": {} if attributes is None else attributes,
        }
        row["id"] = graph.edge_id(row)
        self.connection.execute(
            "INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"],
                "COMMON_FINAL_STATE",
                "CLIENT",
                adapter,
                predicate,
                subject,
                object_,
                graph.canonical_json_payload(row).decode("utf-8"),
            ),
        )

    def populate_machine_recipe(
        self,
        *,
        category_active: bool = False,
    ) -> dict[str, str]:
        identifiers = {
            "machine": "rg:common_final_state_client:machine:gregtech:macerator.lv",
            "map": "rg:common_final_state_client:recipe_map:macerator",
            "recipe": "rg:common_final_state_client:recipe:macerator:abc:0",
            "category_recipe": "rg:common_final_state_client:recipe:macerator:category-only:0",
            "ore_slot": "rg:common_final_state_client:ingredient_slot:macerator:abc:0:input:0",
            "water_slot": "rg:common_final_state_client:ingredient_slot:macerator:abc:0:input:1",
            "output_slot": "rg:common_final_state_client:ingredient_slot:macerator:abc:0:output:0",
            "ore_copper": "rg:common_final_state_client:ore_dictionary_key:oreCopper",
            "copper_ingot": "rg:common_final_state_client:ingredient:minecraft:copper_ingot",
            "water": "rg:common_final_state_client:fluid_variant:minecraft:water:none",
            "dust": "rg:common_final_state_client:ingredient:gregtech:copper_dust",
        }
        for key, kind, adapter in (
            ("machine", "machine", "gt_meta_tile_entities"),
            ("map", "recipe_map", "gt_recipe_maps"),
            ("recipe", "recipe", "gt_recipes"),
            ("category_recipe", "recipe", "gt_recipes"),
            ("ore_slot", "ingredient_slot", "gt_recipes"),
            ("water_slot", "ingredient_slot", "gt_recipes"),
            ("output_slot", "ingredient_slot", "gt_recipes"),
            ("ore_copper", "ore_dictionary_key", "forge_ore_dictionary"),
            ("copper_ingot", "ingredient", "gt_recipes"),
            ("water", "fluid_variant", "gt_recipes"),
            ("dust", "ingredient", "gt_recipes"),
        ):
            self.add_node(identifiers[key], kind, adapter)
        self.add_edge(
            "executes_recipe_map",
            identifiers["machine"],
            identifiers["map"],
            "gt_machine_recipe_maps",
        )
        self.add_edge(
            "has_recipe",
            identifiers["map"],
            identifiers["recipe"],
            "gt_recipes",
            {"category_present": True, "lookup_active": True},
        )
        self.add_edge(
            "has_recipe",
            identifiers["map"],
            identifiers["category_recipe"],
            "gt_recipes",
            {"category_present": True, "lookup_active": category_active},
        )
        self.add_edge(
            "consumes",
            identifiers["recipe"],
            identifiers["ore_slot"],
            "gt_recipes",
            {"ordinal": 0},
        )
        self.add_edge(
            "requires",
            identifiers["recipe"],
            identifiers["water_slot"],
            "gt_recipes",
            {"ordinal": 1},
        )
        self.add_edge(
            "produces",
            identifiers["recipe"],
            identifiers["output_slot"],
            "gt_recipes",
            {"ordinal": 0},
        )
        self.add_edge(
            "accepts_alternative",
            identifiers["ore_slot"],
            identifiers["ore_copper"],
            "gt_recipes",
        )
        self.add_edge(
            "accepts_alternative",
            identifiers["ore_slot"],
            identifiers["copper_ingot"],
            "gt_recipes",
        )
        self.add_edge(
            "accepts_alternative",
            identifiers["water_slot"],
            identifiers["water"],
            "gt_recipes",
        )
        self.add_edge(
            "accepts_alternative",
            identifiers["output_slot"],
            identifiers["dust"],
            "gt_recipes",
        )
        self.connection.commit()
        return identifiers

    def test_machine_recipe_query_groups_alternatives_by_slot(self) -> None:
        identifiers = self.populate_machine_recipe()

        result = graph.query_machine_recipes(
            self.database,
            identifiers["machine"],
            limit=10,
            offset=0,
        )

        self.assertEqual(identifiers["machine"], result["machine"]["id"])
        self.assertEqual(1, result["total_recipes"])
        self.assertEqual(1, result["returned_recipes"])
        self.assertFalse(result["truncated"])
        recipe_map = result["recipe_maps"][0]
        self.assertEqual("executes_recipe_map", recipe_map["relationship"]["predicate"])
        self.assertEqual(identifiers["map"], recipe_map["recipe_map"]["id"])
        recipe = recipe_map["recipes"][0]
        self.assertEqual(identifiers["recipe"], recipe["recipe"]["id"])
        self.assertEqual(
            [identifiers["ore_slot"], identifiers["water_slot"]],
            [row["slot"]["id"] for row in recipe["inputs"]],
        )
        self.assertEqual(
            [identifiers["copper_ingot"], identifiers["ore_copper"]],
            [row["node"]["id"] for row in recipe["inputs"][0]["alternatives"]],
        )
        self.assertEqual(
            [identifiers["water"]],
            [row["node"]["id"] for row in recipe["inputs"][1]["alternatives"]],
        )
        self.assertEqual(
            [identifiers["dust"]],
            [row["node"]["id"] for row in recipe["outputs"][0]["alternatives"]],
        )

    def test_reader_resolves_runtime_node_id_with_optional_narrowing(self) -> None:
        identifiers = self.populate_machine_recipe()

        with query.RuntimeGraphReader(self.database) as reader:
            selected = reader.resolve_exact(
                query.NodeSelector.by_id(
                    identifiers["machine"],
                    kind="machine",
                    profile="COMMON_FINAL_STATE",
                    physical_side="CLIENT",
                )
            )
            wrong_kind = reader.resolve_exact(
                query.NodeSelector.by_id(
                    identifiers["machine"],
                    kind="recipe",
                )
            )

        self.assertEqual((identifiers["machine"],), tuple(row["id"] for row in selected))
        self.assertEqual((), wrong_kind)

    def test_reader_searches_typed_identities_and_projects_relationships(self) -> None:
        identifiers = self.populate_machine_recipe()
        self.connection.executemany(
            "INSERT INTO node_keys VALUES (?, ?, ?, ?, ?)",
            [
                (
                    identifiers["machine"],
                    "registry-name",
                    "example:ore_washer",
                    "COMMON_FINAL_STATE",
                    "CLIENT",
                ),
                (
                    identifiers["water"],
                    "fluid-name",
                    "water",
                    "COMMON_FINAL_STATE",
                    "CLIENT",
                ),
            ],
        )
        self.connection.commit()

        with query.RuntimeGraphReader(self.database) as reader:
            exact = reader.search_nodes("example:ore_washer")
            partial = reader.search_nodes("ore_wash", kinds=("machine",))
            keys = reader.node_identity_keys(identifiers["machine"])
            related = reader.related_nodes(identifiers["machine"])
            recipe_maps = reader.related_nodes(
                identifiers["machine"], predicates=("executes_recipe_map",)
            )

        self.assertEqual(1, exact.total)
        self.assertEqual(1, exact.items[0]["rank"])
        self.assertEqual(
            [{"key_kind": "registry-name", "key_value": "example:ore_washer"}],
            exact.items[0]["matched_keys"],
        )
        self.assertEqual(1, partial.total)
        self.assertEqual("machine", partial.items[0]["node"]["kind"])
        self.assertEqual(1, keys.total)
        self.assertTrue(
            any(
                row["node"]["id"] == identifiers["map"]
                and row["relationship"]["predicate"] == "executes_recipe_map"
                and row["direction"] == "outgoing"
                for row in related.items
            )
        )
        self.assertEqual(1, recipe_maps.total)
        self.assertEqual(
            "executes_recipe_map",
            recipe_maps.items[0]["relationship"]["predicate"],
        )

    def test_reader_search_is_literal_bounded_and_filter_aware(self) -> None:
        identifiers = self.populate_machine_recipe()
        self.connection.execute(
            "INSERT INTO node_keys VALUES (?, ?, ?, ?, ?)",
            (
                identifiers["machine"],
                "display-name",
                "100%_washer",
                "COMMON_FINAL_STATE",
                "CLIENT",
            ),
        )
        self.connection.commit()

        with query.RuntimeGraphReader(self.database) as reader:
            literal = reader.search_nodes(
                "%_wash",
                profile="COMMON_FINAL_STATE",
                physical_side="CLIENT",
            )
            wrong_side = reader.search_nodes(
                "100%_washer", physical_side="DEDICATED_SERVER"
            )
            with self.assertRaisesRegex(
                query.RuntimeGraphQueryError, "single-line"
            ):
                reader.search_nodes("water\nnext")
            with self.assertRaisesRegex(
                query.RuntimeGraphQueryError, "contains duplicates"
            ):
                reader.search_nodes("water", kinds=("fluid", "fluid"))

        self.assertEqual(1, literal.total)
        self.assertEqual(0, wrong_side.total)

    def test_reader_resolves_typed_keys_in_deterministic_order(self) -> None:
        identifiers = self.populate_machine_recipe()
        for node_id in (identifiers["water"], identifiers["dust"]):
            self.connection.execute(
                "INSERT INTO node_keys VALUES (?, ?, ?, ?, ?)",
                (
                    node_id,
                    "test-shared-key",
                    "copper-chain",
                    "COMMON_FINAL_STATE",
                    "CLIENT",
                ),
            )
        self.connection.commit()

        with query.RuntimeGraphReader(self.database) as reader:
            selected = reader.resolve_exact(
                query.NodeSelector.by_key("test-shared-key", "copper-chain")
            )
            selected_ingredient = reader.resolve_exact(
                query.NodeSelector.by_key(
                    "test-shared-key",
                    "copper-chain",
                    kind="ingredient",
                )
            )
            with self.assertRaisesRegex(
                query.RuntimeGraphQueryError,
                "exact target selector is ambiguous.*matched 2",
            ):
                reader.resolve_one(
                    query.NodeSelector.by_key(
                        "test-shared-key",
                        "copper-chain",
                    ),
                    label="target",
                )

        self.assertEqual(
            sorted((identifiers["water"], identifiers["dust"])),
            [row["id"] for row in selected],
        )
        self.assertEqual(
            (identifiers["dust"],),
            tuple(row["id"] for row in selected_ingredient),
        )

    def test_reader_machine_recipe_query_returns_a_flat_page(self) -> None:
        identifiers = self.populate_machine_recipe()

        with query.RuntimeGraphReader(self.database) as reader:
            page = reader.machine_recipes(
                query.NodeSelector.by_id(identifiers["machine"]),
                query.PageRequest(limit=10, offset=0),
            )

        self.assertIsInstance(page, query.QueryPage)
        self.assertEqual(1, page.total)
        self.assertEqual(1, page.returned)
        self.assertEqual(10, page.limit)
        self.assertEqual(0, page.offset)
        self.assertFalse(page.truncated)
        self.assertEqual(1, len(page.items))
        item = page.items[0]
        self.assertEqual(
            "executes_recipe_map",
            item["recipe_map_relationship"]["predicate"],
        )
        self.assertEqual(identifiers["map"], item["recipe_map"]["id"])
        self.assertEqual(identifiers["recipe"], item["recipe"]["id"])
        self.assertEqual(
            (identifiers["ore_slot"], identifiers["water_slot"]),
            tuple(row["slot"]["id"] for row in item["inputs"]),
        )
        self.assertEqual(
            (identifiers["copper_ingot"], identifiers["ore_copper"]),
            tuple(row["node"]["id"] for row in item["inputs"][0]["alternatives"]),
        )

    def test_reader_machine_recipe_pages_are_stable_and_bounded(self) -> None:
        identifiers = self.populate_machine_recipe(category_active=True)

        with query.RuntimeGraphReader(self.database) as reader:
            first = reader.machine_recipes(
                query.NodeSelector.by_id(identifiers["machine"]),
                query.PageRequest(limit=1),
            )
            second = reader.machine_recipes(
                query.NodeSelector.by_id(identifiers["machine"]),
                query.PageRequest(limit=1, offset=1),
            )

        self.assertEqual(2, first.total)
        self.assertEqual(identifiers["recipe"], first.items[0]["recipe"]["id"])
        self.assertTrue(first.truncated)
        self.assertEqual(2, second.total)
        self.assertEqual(
            identifiers["category_recipe"],
            second.items[0]["recipe"]["id"],
        )
        self.assertFalse(second.truncated)

    def test_reader_is_query_only_and_closes_with_its_context(self) -> None:
        self.populate_machine_recipe()
        reader = query.RuntimeGraphReader(self.database)

        with reader:
            self.assertEqual(
                1,
                reader.connection.execute("PRAGMA query_only").fetchone()[0],
            )
            with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                reader.connection.execute(
                    "INSERT INTO nodes VALUES ('x', 'x', 'x', 'x', 'x', '{}')"
                )

        with self.assertRaisesRegex(
            query.RuntimeGraphQueryError,
            "reader is closed",
        ):
            _ = reader.connection
        reader.close()

    def test_projection_identity_rejects_path_rebinding(self) -> None:
        self.add_node(
            "rg:fixture:item_variant:projection-original",
            "item_variant",
            "fixture",
        )
        self.connection.commit()
        replacement_path = self.database.with_name("replacement.sqlite")
        replacement = sqlite3.connect(replacement_path)
        try:
            self.connection.backup(replacement)
            replacement.execute("DELETE FROM nodes")
            replacement.commit()
        finally:
            replacement.close()

        reader = query.RuntimeGraphReader(self.database)
        self.addCleanup(reader.close)
        self.assertEqual(
            1,
            reader.connection.execute(
                "SELECT COUNT(*) FROM nodes"
            ).fetchone()[0],
        )
        os.replace(replacement_path, self.database)
        visible = sqlite3.connect(self.database)
        try:
            self.assertEqual(
                0,
                visible.execute(
                    "SELECT COUNT(*) FROM nodes"
                ).fetchone()[0],
            )
        finally:
            visible.close()
        with self.assertRaisesRegex(
            query.RuntimeGraphQueryError,
            "no longer names the open SQLite file",
        ):
            reader.projection_identity()

    def test_reader_rejects_unsupported_database_identity(self) -> None:
        self.assertEqual(
            graph.QUERY_DATABASE_APPLICATION_ID,
            query.QUERY_DATABASE_APPLICATION_ID,
        )
        self.assertEqual(
            graph.QUERY_DATABASE_USER_VERSION,
            query.QUERY_DATABASE_USER_VERSION,
        )
        for pragma, invalid, valid in (
            (
                "application_id",
                query.QUERY_DATABASE_APPLICATION_ID + 1,
                query.QUERY_DATABASE_APPLICATION_ID,
            ),
            (
                "user_version",
                query.QUERY_DATABASE_USER_VERSION + 1,
                query.QUERY_DATABASE_USER_VERSION,
            ),
        ):
            with self.subTest(pragma=pragma):
                self.connection.execute(f"PRAGMA {pragma} = {invalid}")
                with self.assertRaisesRegex(
                    query.RuntimeGraphQueryError,
                    "not a supported normalized runtime graph query index",
                ):
                    query.RuntimeGraphReader(self.database)
                self.connection.execute(f"PRAGMA {pragma} = {valid}")

    def test_legacy_machine_recipe_adapter_preserves_validation_errors(self) -> None:
        identifiers = self.populate_machine_recipe()

        with self.assertRaisesRegex(
            graph.RuntimeGraphError,
            "recipe query limit must be an integer from 1 through 1000",
        ):
            graph.query_machine_recipes(
                self.database,
                identifiers["machine"],
                limit=True,
            )
        with self.assertRaisesRegex(
            graph.RuntimeGraphError,
            "recipe query offset must be a non-negative integer",
        ):
            graph.query_machine_recipes(
                self.database,
                identifiers["machine"],
                offset=-1,
            )
        with self.assertRaisesRegex(
            graph.RuntimeGraphError,
            "machine not found in runtime graph: missing",
        ):
            graph.query_machine_recipes(self.database, "missing")
        with self.assertRaisesRegex(
            graph.RuntimeGraphError,
            "runtime graph identifier is not a machine",
        ):
            graph.query_machine_recipes(self.database, identifiers["map"])

    def test_machine_recipe_cli_emits_exact_legacy_json(self) -> None:
        identifiers = self.populate_machine_recipe()
        expected = graph.query_machine_recipes(
            self.database,
            identifiers["machine"],
            limit=1,
            offset=0,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = graph.main(
                [
                    "machine-recipes",
                    str(self.database),
                    identifiers["machine"],
                    "--limit",
                    "1",
                ]
            )

        self.assertEqual(0, exit_code)
        self.assertEqual(
            graph.canonical_json_payload(expected).decode("utf-8") + "\n",
            output.getvalue(),
        )
        self.assertEqual(
            "89dab5468cd4e23246cefe7799d51da7ab5691ce392a2eb5e917b478fa92ef41",
            hashlib.sha256(output.getvalue().encode("utf-8")).hexdigest(),
        )
        payload = json.loads(output.getvalue())
        self.assertEqual(identifiers["machine"], payload["machine"]["id"])
        self.assertEqual(1, payload["returned_recipes"])

    def test_root_help_exposes_only_live_query_commands(self) -> None:
        help_text = " ".join(graph._parser().format_help().split())

        self.assertIn(
            "{machine-recipes,producers,consumers,process-chain}",
            help_text,
        )
        for retired in (
            "check-contract",
            "check-producer-binding",
            "check-raw",
            "capture-offline",
        ):
            self.assertNotIn(retired, help_text)
        self.assertNotIn(" check ", f" {help_text} ")
        self.assertNotIn(" normalize ", f" {help_text} ")

    def test_machine_recipe_help_contract_is_backward_compatible(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            graph._parser().parse_args(["machine-recipes", "--help"])

        self.assertEqual(0, raised.exception.code)
        help_text = " ".join(output.getvalue().split())
        self.assertIn(
            "machine-recipes [-h] [--limit LIMIT] [--offset OFFSET] database machine",
            help_text,
        )
        self.assertIn(
            "query a normalized database for a machine's recipes and slot alternatives",
            " ".join(graph._parser().format_help().split()),
        )


if __name__ == "__main__":
    unittest.main()
