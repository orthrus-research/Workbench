#!/usr/bin/env python3

from __future__ import annotations

import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


TEST_ROOT = Path(__file__).resolve().parent
ATLAS_ROOT = TEST_ROOT.parent
TOOLS_ROOT = ATLAS_ROOT / "src/workbench_atlas"
REPO_ROOT = ATLAS_ROOT.parents[1]
DATA_ROOT = ATLAS_ROOT / "data"
CATALOG_ROOT = DATA_ROOT / "catalogs"
QUESTIONS_PATH = DATA_ROOT / "acceptance-questions-v1.json"
SNAPSHOT_ID = "SNAPSHOT-SUSY-0-1-16-11-9D3AA7AE0"
CANONICAL_ID = "MATERIAL-SUSY-DILUTED-OIL-LIGHT"

if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import workbench_atlas.corpus_bridge as bridge
import workbench_atlas.atlas_query as atlas_query
import workbench_atlas.knowledge_catalog as catalog
import workbench_atlas.runtime_graph as graph


class CorpusBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / "runtime-graph.sqlite"
        self.links = self.root / "corpus-links.jsonl"
        self.quest_nodes = self.root / "quest-nodes.jsonl"
        self.quest_edges = self.root / "quest-edges.jsonl"
        self.connection = sqlite3.connect(self.database)
        self.addCleanup(self.connection.close)
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
            COMMIT;
            """
        )
        self.material_id = (
            "rg:common_final_state_client:material:susy:diluted_oil_light"
        )
        self.fluid_id = (
            "rg:common_final_state_client:fluid:diluted_oil_light"
        )
        self.variant_id = (
            "rg:common_final_state_client:fluid_variant:"
            "diluted_oil_light:default"
        )
        self.add_node(
            self.material_id,
            "material",
            "gt_materials",
            {"registry_name": "susy:diluted_oil_light", "id": 20000},
        )
        self.add_node(
            self.fluid_id,
            "fluid",
            "forge_fluids",
            {
                "name": "diluted_oil_light",
                "default_registration_name": "susy:diluted_oil_light",
            },
        )
        self.add_node(
            self.variant_id,
            "fluid_variant",
            "gt_materials",
            {"fluid": "diluted_oil_light"},
        )
        self.add_key(
            self.material_id,
            "material-resource-location",
            "susy:diluted_oil_light",
        )
        self.add_key(
            self.fluid_id,
            "fluid-name",
            "diluted_oil_light",
        )
        self.variant_edge_id = self.add_edge(
            "has_variant",
            self.fluid_id,
            self.variant_id,
            "forge_fluids",
            {"role": "default-variant"},
        )
        self.material_edge_id = self.add_edge(
            "has_material",
            self.variant_id,
            self.material_id,
            "gt_materials",
            {"role": "material_fluid_storage_form"},
        )
        self.recipe_map_id = (
            "rg:common_final_state_client:recipe_map:test_atlas"
        )
        self.machine_id = (
            "rg:common_final_state_client:machine:test:atlas_machine"
        )
        self.consulting_machine_id = (
            "rg:common_final_state_client:machine:test:atlas_viewer"
        )
        self.recipe_wrapper_id = (
            "rg:client_jei_final_state_client:recipe_wrapper:"
            "test_atlas:producer"
        )
        self.producer_id = (
            "rg:common_final_state_client:recipe:test_atlas:producer"
        )
        self.producer_slot_id = (
            "rg:common_final_state_client:ingredient_slot:"
            "test_atlas:producer:output:0"
        )
        self.consumer_id = (
            "rg:common_final_state_client:recipe:test_atlas:consumer"
        )
        self.consumer_slot_id = (
            "rg:common_final_state_client:ingredient_slot:"
            "test_atlas:consumer:input:0"
        )
        self.input_a_id = (
            "rg:common_final_state_client:item_variant:test:input_a:0"
        )
        self.input_b_id = (
            "rg:common_final_state_client:item_variant:test:input_b:0"
        )
        self.catalyst_id = (
            "rg:common_final_state_client:item_variant:test:catalyst:0"
        )
        self.producer_input_slot_id = (
            "rg:common_final_state_client:ingredient_slot:"
            "test_atlas:producer:input:0"
        )
        self.producer_reusable_slot_id = (
            "rg:common_final_state_client:ingredient_slot:"
            "test_atlas:producer:requires:1"
        )
        self.add_node(
            self.recipe_map_id,
            "recipe_map",
            "gt_recipe_maps",
            {"name": "test_atlas"},
        )
        self.add_node(
            self.machine_id,
            "machine",
            "gt_meta_tile_entities",
            {"registry_key": "test:atlas_machine"},
        )
        self.add_node(
            self.consulting_machine_id,
            "machine",
            "gt_meta_tile_entities",
            {"registry_key": "test:atlas_viewer"},
        )
        self.add_node(
            self.recipe_wrapper_id,
            "recipe_wrapper",
            "hei_recipes",
            {"category": "test_atlas"},
            profile="CLIENT_JEI_FINAL_STATE",
        )
        self.add_node(
            self.producer_id,
            "recipe",
            "gt_recipes",
            {
                "native_recipe_map": "test_atlas",
                "duration": 20,
                "eut": 30,
            },
        )
        self.add_node(
            self.producer_slot_id,
            "ingredient_slot",
            "gt_recipes",
            {"amount": 1000, "channel": "fluid_output", "ordinal": 0},
        )
        self.add_node(
            self.consumer_id,
            "recipe",
            "gt_recipes",
            {
                "native_recipe_map": "test_atlas",
                "duration": 20,
                "eut": 30,
            },
        )
        self.add_node(
            self.consumer_slot_id,
            "ingredient_slot",
            "gt_recipes",
            {
                "amount": 1000,
                "channel": "fluid_input",
                "ordinal": 0,
            },
        )
        self.add_node(
            self.input_a_id,
            "item_variant",
            "gt_recipes",
            {
                "registry_name": "test:input_a",
                "metadata": 0,
                "nbt": {"grade": "a"},
                "capabilities": {"test:quality": {"level": 2}},
            },
        )
        self.add_node(
            self.input_b_id,
            "item_variant",
            "gt_recipes",
            {
                "registry_name": "test:input_b",
                "metadata": 0,
            },
        )
        self.add_node(
            self.catalyst_id,
            "item_variant",
            "gt_recipes",
            {
                "registry_name": "test:catalyst",
                "metadata": 0,
            },
        )
        self.add_node(
            self.producer_input_slot_id,
            "ingredient_slot",
            "gt_recipes",
            {
                "amount": 2,
                "channel": "item_input",
                "ordinal": 0,
            },
        )
        self.add_node(
            self.producer_reusable_slot_id,
            "ingredient_slot",
            "gt_recipes",
            {
                "amount": 1,
                "channel": "item_input",
                "ordinal": 1,
            },
        )
        self.add_edge(
            "executes_recipe_map",
            self.machine_id,
            self.recipe_map_id,
            "gt_machine_recipe_maps",
            {"selection": "direct"},
        )
        self.add_edge(
            "consults_recipe_map",
            self.consulting_machine_id,
            self.recipe_map_id,
            "gt_machine_recipe_maps",
            {"selection": "presentation-only"},
        )
        for recipe_id in (self.producer_id, self.consumer_id):
            self.add_edge(
                "has_recipe",
                self.recipe_map_id,
                recipe_id,
                "gt_recipes",
                {"lookup_active": True, "category_present": True},
            )
        self.add_edge(
            "reconciles_to",
            self.recipe_wrapper_id,
            self.producer_id,
            "hei_recipes",
            {"match": "exact"},
            profile="CLIENT_JEI_FINAL_STATE",
        )
        self.add_edge(
            "produces",
            self.producer_id,
            self.producer_slot_id,
            "gt_recipes",
            {},
        )
        self.add_edge(
            "accepts_alternative",
            self.producer_slot_id,
            self.variant_id,
            "gt_recipes",
            {"alternative_ordinal": 0},
        )
        self.add_edge(
            "consumes",
            self.consumer_id,
            self.consumer_slot_id,
            "gt_recipes",
            {},
        )
        self.add_edge(
            "accepts_alternative",
            self.consumer_slot_id,
            self.variant_id,
            "gt_recipes",
            {"alternative_ordinal": 0},
        )
        self.add_edge(
            "consumes",
            self.producer_id,
            self.producer_input_slot_id,
            "gt_recipes",
            {"ordinal": 0},
        )
        self.add_edge(
            "accepts_alternative",
            self.producer_input_slot_id,
            self.input_a_id,
            "gt_recipes",
            {"alternative_ordinal": 0},
        )
        self.add_edge(
            "accepts_alternative",
            self.producer_input_slot_id,
            self.input_b_id,
            "gt_recipes",
            {"alternative_ordinal": 1},
        )
        self.add_edge(
            "requires",
            self.producer_id,
            self.producer_reusable_slot_id,
            "gt_recipes",
            {"ordinal": 1},
        )
        self.add_edge(
            "accepts_alternative",
            self.producer_reusable_slot_id,
            self.catalyst_id,
            "gt_recipes",
            {"alternative_ordinal": 0},
        )
        self.write_corpus_files()
        self.connection.commit()

    @staticmethod
    def scope(
        adapter: str,
        profile: str = "COMMON_FINAL_STATE",
        physical_side: str = "CLIENT",
    ) -> dict[str, str]:
        return {
            "snapshot_id": SNAPSHOT_ID,
            "profile": profile,
            "physical_side": physical_side,
            "adapter": adapter,
        }

    def add_node(
        self,
        identifier: str,
        kind: str,
        adapter: str,
        attributes: dict[str, object],
        *,
        profile: str = "COMMON_FINAL_STATE",
        physical_side: str = "CLIENT",
    ) -> None:
        row = {
            "record_type": "node",
            "id": identifier,
            "kind": kind,
            "scope": self.scope(adapter, profile, physical_side),
            "attributes": attributes,
        }
        self.connection.execute(
            "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?)",
            (
                identifier,
                profile,
                physical_side,
                adapter,
                kind,
                graph.canonical_json_payload(row).decode("utf-8"),
            ),
        )

    def add_key(
        self,
        node_id: str,
        key_kind: str,
        key_value: str,
        *,
        profile: str = "COMMON_FINAL_STATE",
        physical_side: str = "CLIENT",
    ) -> None:
        self.connection.execute(
            "INSERT INTO node_keys VALUES (?, ?, ?, ?, ?)",
            (
                node_id,
                key_kind,
                key_value,
                profile,
                physical_side,
            ),
        )

    def add_edge(
        self,
        predicate: str,
        subject: str,
        object_: str,
        adapter: str,
        attributes: dict[str, object],
        *,
        profile: str = "COMMON_FINAL_STATE",
        physical_side: str = "CLIENT",
    ) -> str:
        row: dict[str, object] = {
            "record_type": "edge",
            "predicate": predicate,
            "subject": subject,
            "object": object_,
            "scope": self.scope(adapter, profile, physical_side),
            "attributes": attributes,
        }
        row["id"] = graph.edge_id(row)
        self.connection.execute(
            "INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"],
                profile,
                physical_side,
                adapter,
                predicate,
                subject,
                object_,
                graph.canonical_json_payload(row).decode("utf-8"),
            ),
        )
        return str(row["id"])

    @staticmethod
    def provenance(path: str, pointer: str) -> dict[str, object]:
        return {
            "source_path": path,
            "source_sha256": "0" * 64,
            "json_pointer": pointer,
            "rewrite_ids": [],
        }

    def quest_node(
        self,
        identifier: str,
        kind: str,
        attributes: dict[str, object],
        pointer: str,
    ) -> dict[str, object]:
        return {
            "record_type": "node",
            "id": identifier,
            "snapshot_id": SNAPSHOT_ID,
            "kind": kind,
            "subject": None,
            "object": None,
            "attributes": attributes,
            "provenance": self.provenance(
                "config/betterquesting/DefaultQuests/Quests/"
                "MultipleQuestLine/173.json",
                pointer,
            ),
        }

    def quest_edge(
        self,
        identifier: str,
        kind: str,
        subject: str,
        object_: str,
    ) -> dict[str, object]:
        return {
            "record_type": "edge",
            "id": identifier,
            "snapshot_id": SNAPSHOT_ID,
            "kind": kind,
            "subject": subject,
            "object": object_,
            "attributes": {},
            "provenance": self.provenance(
                "config/betterquesting/DefaultQuests/Quests/"
                "MultipleQuestLine/173.json",
                "",
            ),
        }

    def write_corpus_files(self) -> None:
        catalog.write_jsonl(
            self.links,
            [
                {
                    "id": "LINK-DILUTED-OIL-LIGHT-RUNTIME-CLIENT",
                    "snapshot_id": SNAPSHOT_ID,
                    "canonical_entity_id": CANONICAL_ID,
                    "observed_id": self.material_id,
                    "observed_kind": "runtime-node",
                    "basis": "exact-typed-key",
                    "key_kind": "material-resource-location",
                    "key_value": "susy:diluted_oil_light",
                    "profile_scope": ["COMMON_FINAL_STATE/CLIENT"],
                },
                {
                    "id": "LINK-DILUTED-OIL-LIGHT-QUEST-TASK-173-0",
                    "snapshot_id": SNAPSHOT_ID,
                    "canonical_entity_id": CANONICAL_ID,
                    "observed_id": "qg:task:173:0",
                    "observed_kind": "quest-task",
                    "basis": "exact-typed-key",
                    "key_kind": "fluid-name",
                    "key_value": "diluted_oil_light",
                    "profile_scope": ["COMMON_FINAL_STATE/CLIENT"],
                },
            ],
        )
        catalog.write_jsonl(
            self.quest_nodes,
            [
                self.quest_node(
                    "qg:quest:172",
                    "quest",
                    {"quest_id": 172, "name": "Prior separation"},
                    "",
                ),
                self.quest_node(
                    "qg:quest:173",
                    "quest",
                    {"quest_id": 173, "name": "Light diluted oil"},
                    "",
                ),
                self.quest_node(
                    "qg:quest:175",
                    "quest",
                    {"quest_id": 175, "name": "Downstream refining"},
                    "",
                ),
                self.quest_node(
                    "qg:quest_line:4",
                    "quest_line",
                    {"line_id": 4},
                    "",
                ),
                self.quest_node(
                    "qg:quest_line:16",
                    "quest_line",
                    {"line_id": 16},
                    "",
                ),
                self.quest_node(
                    "qg:task:173:0",
                    "task",
                    {
                        "amount": 1000,
                        "container_key": "0:10",
                        "key_kind": "fluid-name",
                        "key_value": "diluted_oil_light",
                        "ordinal": 0,
                    },
                    "/tasks/0:10",
                ),
            ],
        )
        catalog.write_jsonl(
            self.quest_edges,
            [
                self.quest_edge(
                    "qge:belongs_to_line:173:4",
                    "belongs_to_line",
                    "qg:quest:173",
                    "qg:quest_line:4",
                ),
                self.quest_edge(
                    "qge:belongs_to_line:173:16",
                    "belongs_to_line",
                    "qg:quest:173",
                    "qg:quest_line:16",
                ),
                self.quest_edge(
                    "qge:has_task:173:0",
                    "has_task",
                    "qg:quest:173",
                    "qg:task:173:0",
                ),
                self.quest_edge(
                    "qge:requires_quest:173:172",
                    "requires_quest",
                    "qg:quest:173",
                    "qg:quest:172",
                ),
                self.quest_edge(
                    "qge:requires_quest:175:173",
                    "requires_quest",
                    "qg:quest:175",
                    "qg:quest:173",
                ),
            ],
        )

    def build(
        self,
        question_id: str = "PLAYER-QUEST-CONTEXT-001",
        scopes: list[str] | None = None,
        question_path: Path = (
            QUESTIONS_PATH
        ),
        chain_options: bridge.ChainOptions = bridge.ChainOptions(),
        construction_chain_options: bridge.ChainOptions = (
            bridge.DEFAULT_CONSTRUCTION_CHAIN_OPTIONS
        ),
        max_construction_machine_roots: int = (
            bridge.DEFAULT_MAX_CONSTRUCTION_MACHINE_ROOTS
        ),
    ) -> dict[str, object]:
        return bridge.build_answer(
            runtime_database=self.database,
            catalog_root=CATALOG_ROOT,
            question_path=question_path,
            question_id=question_id,
            links_path=self.links,
            quest_nodes_path=self.quest_nodes,
            quest_edges_path=self.quest_edges,
            snapshot_id=SNAPSHOT_ID,
            scope_values=(
                ["COMMON_FINAL_STATE:CLIENT"]
                if scopes is None
                else scopes
            ),
            chain_options=chain_options,
            construction_chain_options=construction_chain_options,
            max_construction_machine_roots=max_construction_machine_roots,
        )

    def query_instance(
        self,
        question_id: str,
        *,
        kind: str = "material",
        key_kind: str = "material-resource-location",
        key: str = "susy:diluted_oil_light",
        scopes: list[str] | None = None,
    ) -> dict[str, object]:
        question = atlas_query.question_by_id(question_id)
        instance = atlas_query.build_v1_query_instance(
            question,
            snapshot_id=SNAPSHOT_ID,
            scope_values=(
                ["COMMON_FINAL_STATE:CLIENT"]
                if scopes is None
                else scopes
            ),
        )
        instance["selector"] = {
            "kind": kind,
            "key_kind": key_kind,
            "key": key,
        }
        instance["query_instance_id"] = atlas_query.query_instance_id(
            instance
        )
        return instance

    def byproduct_question_path(self) -> Path:
        path = self.root / "byproducts-question.json"
        question = {
            "id": "PLAYER-BYPRODUCTS-PROJECTION-001",
            "question": "Which exact mechanical co-outputs exist?",
            "audience": "player",
            "query": "chain",
            "selector": {
                "kind": "material",
                "key": "susy:diluted_oil_light",
            },
            "required_fields": [
                "target",
                "routes",
                "byproducts",
                "evidence",
                "snapshot_id",
                "profile_scope",
            ],
            "required_evidence_bases": ["runtime-mechanics"],
            "status": "active",
        }
        path.write_bytes(
            graph.canonical_json_payload([question]) + b"\n"
        )
        return path

    def add_byproduct_routes(self) -> dict[str, str]:
        identifiers = {
            "guaranteed_slot": (
                "rg:common_final_state_client:ingredient_slot:"
                "test_atlas:producer:byproduct:1"
            ),
            "conditional_slot": (
                "rg:common_final_state_client:ingredient_slot:"
                "test_atlas:producer:byproduct:2"
            ),
            "byproduct_a": (
                "rg:common_final_state_client:item_variant:"
                "test:byproduct_a:0"
            ),
            "byproduct_b": (
                "rg:common_final_state_client:item_variant:"
                "test:byproduct_b:0"
            ),
            "byproduct_c": (
                "rg:common_final_state_client:item_variant:"
                "test:byproduct_c:0"
            ),
            "empty_producer": (
                "rg:common_final_state_client:recipe:"
                "test_atlas:producer_without_byproducts"
            ),
            "empty_production_slot": (
                "rg:common_final_state_client:ingredient_slot:"
                "test_atlas:producer_without_byproducts:output:0"
            ),
        }
        for key in (
            "byproduct_a",
            "byproduct_b",
            "byproduct_c",
        ):
            self.add_node(
                identifiers[key],
                "item_variant",
                "gt_recipes",
                {"registry_name": f"test:{key}", "metadata": 0},
            )
        self.add_node(
            identifiers["guaranteed_slot"],
            "ingredient_slot",
            "gt_recipes",
            {"amount": 2, "channel": "item_output", "ordinal": 1},
        )
        self.add_node(
            identifiers["conditional_slot"],
            "ingredient_slot",
            "gt_recipes",
            {"amount": 1, "channel": "item_output", "ordinal": 2},
        )
        self.add_edge(
            "produces",
            self.producer_id,
            identifiers["guaranteed_slot"],
            "gt_recipes",
            {"ordinal": 1},
        )
        for ordinal, key in enumerate(("byproduct_a", "byproduct_b")):
            self.add_edge(
                "accepts_alternative",
                identifiers["guaranteed_slot"],
                identifiers[key],
                "gt_recipes",
                {"alternative_ordinal": ordinal},
            )
        self.add_edge(
            "may_produce",
            self.producer_id,
            identifiers["conditional_slot"],
            "gt_recipes",
            {
                "ordinal": 2,
                "chance": 2500,
                "chance_scale": 10000,
            },
        )
        self.add_edge(
            "accepts_alternative",
            identifiers["conditional_slot"],
            identifiers["byproduct_c"],
            "gt_recipes",
            {"alternative_ordinal": 0},
        )

        self.add_node(
            identifiers["empty_producer"],
            "recipe",
            "gt_recipes",
            {
                "native_recipe_map": "test_atlas",
                "duration": 40,
                "eut": 30,
            },
        )
        self.add_node(
            identifiers["empty_production_slot"],
            "ingredient_slot",
            "gt_recipes",
            {"amount": 1000, "channel": "fluid_output", "ordinal": 0},
        )
        self.add_edge(
            "has_recipe",
            self.recipe_map_id,
            identifiers["empty_producer"],
            "gt_recipes",
            {"lookup_active": True, "category_present": True},
        )
        self.add_edge(
            "produces",
            identifiers["empty_producer"],
            identifiers["empty_production_slot"],
            "gt_recipes",
            {"ordinal": 0},
        )
        self.add_edge(
            "accepts_alternative",
            identifiers["empty_production_slot"],
            self.variant_id,
            "gt_recipes",
            {"alternative_ordinal": 0},
        )
        self.connection.commit()
        return identifiers

    def add_recycling_routes(
        self,
        identifiers: dict[str, str],
    ) -> dict[str, str]:
        recycling = {
            "shared": (
                "rg:common_final_state_client:item_variant:"
                "test:recycling_shared:0"
            ),
            "unused": (
                "rg:common_final_state_client:item_variant:"
                "test:recycling_unused:0"
            ),
        }
        for key in ("shared", "unused"):
            self.add_node(
                recycling[key],
                "item_variant",
                "gt_recipes",
                {"registry_name": f"test:recycling_{key}", "metadata": 0},
            )

        def add_operation(
            name: str,
            input_predicate: str,
            input_target: str,
            outputs: list[str],
            *,
            input_attributes: dict[str, object] | None = None,
        ) -> str:
            owner = (
                "rg:common_final_state_client:recipe:"
                f"test_atlas:recycling:{name}"
            )
            input_slot = (
                "rg:common_final_state_client:ingredient_slot:"
                f"test_atlas:recycling:{name}:input:0"
            )
            self.add_node(
                owner,
                "recipe",
                "gt_recipes",
                {
                    "native_recipe_map": "test_atlas",
                    "duration": 20,
                    "eut": 30,
                },
            )
            self.add_node(
                input_slot,
                "ingredient_slot",
                "gt_recipes",
                {
                    "amount": 1,
                    "channel": "item_input",
                    "ordinal": 0,
                    **({} if input_attributes is None else input_attributes),
                },
            )
            self.add_edge(
                "has_recipe",
                self.recipe_map_id,
                owner,
                "gt_recipes",
                {"lookup_active": True, "category_present": True},
            )
            self.add_edge(
                input_predicate,
                owner,
                input_slot,
                "gt_recipes",
                {"ordinal": 0},
            )
            self.add_edge(
                "accepts_alternative",
                input_slot,
                input_target,
                "gt_recipes",
                {"alternative_ordinal": 0},
            )
            for ordinal, output in enumerate(outputs):
                output_slot = (
                    "rg:common_final_state_client:ingredient_slot:"
                    f"test_atlas:recycling:{name}:output:{ordinal}"
                )
                self.add_node(
                    output_slot,
                    "ingredient_slot",
                    "gt_recipes",
                    {
                        "amount": 1,
                        "channel": "item_output",
                        "ordinal": ordinal,
                    },
                )
                self.add_edge(
                    "produces",
                    owner,
                    output_slot,
                    "gt_recipes",
                    {"ordinal": ordinal},
                )
                self.add_edge(
                    "accepts_alternative",
                    output_slot,
                    output,
                    "gt_recipes",
                    {"alternative_ordinal": 0},
                )
            return owner

        recycling["consume_a"] = add_operation(
            "consume_a",
            "consumes",
            identifiers["byproduct_a"],
            [recycling["shared"]],
        )
        recycling["consume_b"] = add_operation(
            "consume_b",
            "may_consume",
            identifiers["byproduct_b"],
            [recycling["shared"]],
            input_attributes={"chance": 2500, "chance_scale": 10000},
        )
        recycling["requires_c"] = add_operation(
            "requires_c",
            "requires",
            identifiers["byproduct_c"],
            [recycling["unused"]],
        )
        recycling["consume_shared"] = add_operation(
            "consume_shared",
            "consumes",
            recycling["shared"],
            [
                self.variant_id,
                self.input_a_id,
                identifiers["byproduct_a"],
            ],
        )
        self.connection.commit()
        return recycling

    def add_classification_rows(self) -> tuple[str, str]:
        category_recipe = (
            "rg:common_final_state_client:recipe:test_atlas:category_only"
        )
        category_slot = (
            "rg:common_final_state_client:ingredient_slot:"
            "test_atlas:category_only:output:0"
        )
        procedural_rule = (
            "rg:common_final_state_client:process_rule:"
            "test_atlas:procedural"
        )
        procedural_slot = (
            "rg:common_final_state_client:ingredient_slot:"
            "test_atlas:procedural:output:0"
        )
        self.add_node(
            category_recipe,
            "recipe",
            "gt_recipes",
            {"native_recipe_map": "test_atlas"},
        )
        self.add_node(
            category_slot,
            "ingredient_slot",
            "gt_recipes",
            {"amount": 1000, "channel": "fluid_output", "ordinal": 0},
        )
        self.add_edge(
            "has_recipe",
            self.recipe_map_id,
            category_recipe,
            "gt_recipes",
            {"lookup_active": False, "category_present": True},
        )
        self.add_edge(
            "produces",
            category_recipe,
            category_slot,
            "gt_recipes",
            {},
        )
        self.add_edge(
            "accepts_alternative",
            category_slot,
            self.variant_id,
            "gt_recipes",
            {"alternative_ordinal": 0},
        )
        self.add_node(
            procedural_rule,
            "process_rule",
            "procedural_rules",
            {"rule": "fixture"},
        )
        self.add_node(
            procedural_slot,
            "ingredient_slot",
            "procedural_rules",
            {"amount": 1000, "channel": "fluid_output", "ordinal": 0},
        )
        self.add_edge(
            "governed_by_rule",
            self.machine_id,
            procedural_rule,
            "procedural_rules",
            {},
        )
        self.add_edge(
            "produces",
            procedural_rule,
            procedural_slot,
            "procedural_rules",
            {},
        )
        self.add_edge(
            "accepts_alternative",
            procedural_slot,
            self.variant_id,
            "procedural_rules",
            {"alternative_ordinal": 0},
        )
        self.connection.commit()
        return category_recipe, procedural_rule

    def add_dedicated_common_clone(self) -> dict[str, str]:
        source_prefix = "rg:common_final_state_client:"
        target_prefix = "rg:common_final_state_dedicated_server:"

        def rewrite(value: object) -> object:
            if isinstance(value, str):
                return value.replace(source_prefix, target_prefix)
            if isinstance(value, list):
                return [rewrite(child) for child in value]
            if isinstance(value, dict):
                return {
                    key: rewrite(child)
                    for key, child in value.items()
                }
            return value

        node_rows = self.connection.execute(
            "SELECT json FROM nodes "
            "WHERE profile = 'COMMON_FINAL_STATE' "
            "AND physical_side = 'CLIENT' "
            "ORDER BY id"
        ).fetchall()
        identifiers: dict[str, str] = {}
        for (encoded,) in node_rows:
            source = json.loads(encoded)
            clone = rewrite(source)
            clone["scope"]["physical_side"] = "DEDICATED_SERVER"
            source_id = str(source["id"])
            clone_id = str(clone["id"])
            identifiers[source_id] = clone_id
            self.connection.execute(
                "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?)",
                (
                    clone_id,
                    "COMMON_FINAL_STATE",
                    "DEDICATED_SERVER",
                    clone["scope"]["adapter"],
                    clone["kind"],
                    graph.canonical_json_payload(clone).decode("utf-8"),
                ),
            )

        key_rows = self.connection.execute(
            "SELECT node_id, key_kind, key_value FROM node_keys "
            "WHERE profile = 'COMMON_FINAL_STATE' "
            "AND physical_side = 'CLIENT' "
            "ORDER BY key_kind, key_value, node_id"
        ).fetchall()
        for node_id, key_kind, key_value in key_rows:
            self.add_key(
                identifiers[node_id],
                key_kind,
                key_value,
                profile="COMMON_FINAL_STATE",
                physical_side="DEDICATED_SERVER",
            )

        edge_rows = self.connection.execute(
            "SELECT json FROM edges "
            "WHERE profile = 'COMMON_FINAL_STATE' "
            "AND physical_side = 'CLIENT' "
            "ORDER BY id"
        ).fetchall()
        for (encoded,) in edge_rows:
            source = json.loads(encoded)
            clone = rewrite(source)
            clone["scope"]["physical_side"] = "DEDICATED_SERVER"
            clone["id"] = graph.edge_id(clone)
            self.connection.execute(
                "INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    clone["id"],
                    "COMMON_FINAL_STATE",
                    "DEDICATED_SERVER",
                    clone["scope"]["adapter"],
                    clone["predicate"],
                    clone["subject"],
                    clone["object"],
                    graph.canonical_json_payload(clone).decode("utf-8"),
                ),
            )

        links = catalog.read_jsonl(self.links)
        client_link = next(
            row
            for row in links
            if row["observed_kind"] == "runtime-node"
        )
        links.append(
            {
                **client_link,
                "id": "LINK-DILUTED-OIL-LIGHT-RUNTIME-DEDICATED",
                "observed_id": identifiers[self.material_id],
                "profile_scope": [
                    "COMMON_FINAL_STATE/DEDICATED_SERVER"
                ],
            }
        )
        for row in links:
            if row["observed_kind"] == "quest-task":
                row["profile_scope"] = [
                    "COMMON_FINAL_STATE/CLIENT",
                    "COMMON_FINAL_STATE/DEDICATED_SERVER",
                ]
        links.sort(key=lambda row: row["id"])
        catalog.write_jsonl(self.links, links)
        self.connection.commit()
        return identifiers

    def test_answer_preserves_independent_authorities_and_exact_path(self) -> None:
        answer = self.build()
        self.assertEqual("answered", answer["result_status"])
        self.assertEqual(
            [CANONICAL_ID],
            answer["target"]["canonical_entity_ids"],
        )

        quest = answer["quests"][0]
        self.assertEqual("qg:quest:173", quest["quest"]["id"])
        self.assertEqual(
            ["qg:quest_line:16", "qg:quest_line:4"],
            sorted(
                membership["quest_line"]["id"]
                for membership in quest["quest_lines"]
            ),
        )
        task = quest["matches"][0]["record"]
        self.assertEqual("0:10", task["attributes"]["container_key"])
        self.assertEqual(0, task["attributes"]["ordinal"])

        basis = quest["matches"][0]["runtime_basis"][0]
        self.assertEqual(
            {
                "key_kind": "fluid-name",
                "key_value": "diluted_oil_light",
            },
            basis["selector"],
        )
        self.assertEqual(self.fluid_id, basis["resolved_node_id"])
        self.assertEqual(
            [self.fluid_id, self.variant_id, self.material_id],
            basis["paths"][0]["node_ids"],
        )
        self.assertEqual(
            [self.variant_edge_id, self.material_edge_id],
            basis["paths"][0]["edge_ids"],
        )

        prerequisites = {
            row["relationship"]["id"]: row
            for row in answer["quest_prerequisites"]
        }
        self.assertEqual(
            {
                "qge:requires_quest:173:172",
                "qge:requires_quest:175:173",
            },
            set(prerequisites),
        )
        self.assertEqual(
            "qg:quest:172",
            prerequisites[
                "qge:requires_quest:173:172"
            ]["prerequisite"]["id"],
        )
        gating = prerequisites["qge:requires_quest:175:173"]
        self.assertEqual(
            "qg:quest:175",
            gating["dependent_quest"]["id"],
        )
        self.assertEqual(
            "qg:quest:173",
            gating["prerequisite"]["id"],
        )
        self.assertTrue(
            all(
                row["semantics"] == "quest-ordering-only"
                for row in prerequisites.values()
            )
        )

        evidence = {row["id"]: row for row in answer["evidence"]}
        self.assertEqual(
            ("normalized-runtime-graph", "runtime-mechanics"),
            (
                evidence[self.material_edge_id]["authority"],
                evidence[self.material_edge_id]["basis"],
            ),
        )
        self.assertEqual(
            ("quest-graph", "quest-data"),
            (
                evidence["qge:requires_quest:175:173"]["authority"],
                evidence["qge:requires_quest:175:173"]["basis"],
            ),
        )
        self.assertEqual(
            "curated-catalog",
            evidence[
                "CLAIM-MATERIAL-DILUTED-OIL-LIGHT-DECLARATION"
            ]["authority"],
        )

    def test_prerequisite_relationships_are_globally_unique(self) -> None:
        nodes = catalog.read_jsonl(self.quest_nodes)
        nodes.append(
            self.quest_node(
                "qg:task:175:0",
                "task",
                {
                    "amount": 1000,
                    "container_key": "0:10",
                    "key_kind": "fluid-name",
                    "key_value": "diluted_oil_light",
                    "ordinal": 0,
                },
                "/tasks/0:10",
            )
        )
        catalog.write_jsonl(self.quest_nodes, nodes)

        edges = catalog.read_jsonl(self.quest_edges)
        edges.append(
            self.quest_edge(
                "qge:has_task:175:0",
                "has_task",
                "qg:quest:175",
                "qg:task:175:0",
            )
        )
        catalog.write_jsonl(self.quest_edges, edges)

        links = catalog.read_jsonl(self.links)
        links.append(
            {
                "id": "LINK-DILUTED-OIL-LIGHT-QUEST-TASK-175-0",
                "snapshot_id": SNAPSHOT_ID,
                "canonical_entity_id": CANONICAL_ID,
                "observed_id": "qg:task:175:0",
                "observed_kind": "quest-task",
                "basis": "exact-typed-key",
                "key_kind": "fluid-name",
                "key_value": "diluted_oil_light",
                "profile_scope": ["COMMON_FINAL_STATE/CLIENT"],
            }
        )
        catalog.write_jsonl(self.links, links)

        answer = self.build()
        relationship_ids = [
            row["relationship"]["id"]
            for row in answer["quest_prerequisites"]
        ]
        self.assertEqual(len(relationship_ids), len(set(relationship_ids)))
        self.assertEqual(
            1,
            relationship_ids.count("qge:requires_quest:175:173"),
        )

    def test_declaration_projection_uses_pinned_catalog_source(self) -> None:
        answer = self.build("DEVELOPER-DECLARATION-001")
        declaration = answer["declaration"]
        self.assertEqual("answered", declaration["status"])
        self.assertEqual("SRC-PACK", declaration["items"][0]["source"]["id"])
        evidence = {row["id"]: row for row in answer["evidence"]}
        self.assertEqual(
            "pinned-source",
            evidence[
                "CLAIM-MATERIAL-DILUTED-OIL-LIGHT-DECLARATION"
            ]["basis"],
        )

    def test_process_usage_projects_exact_producers_and_consumers(self) -> None:
        answer = self.build("DEVELOPER-PROCESS-USAGE-001")
        self.assertEqual("answered", answer["result_status"])
        self.assertEqual(
            [self.producer_id],
            [row["owner"]["id"] for row in answer["producers"]["items"]],
        )
        self.assertEqual(
            [self.consumer_id],
            [row["owner"]["id"] for row in answer["consumers"]["items"]],
        )
        self.assertEqual(
            {
                "limit": 100,
                "offset": 0,
                "returned": 1,
                "total": 1,
                "truncated": False,
            },
            answer["producers"]["page"],
        )
        assertions = {row["path"]: row for row in answer["assertions"]}
        self.assertEqual("supported", assertions["/producers"]["status"])
        self.assertEqual("supported", assertions["/consumers"]["status"])
        evidence = {row["id"]: row for row in answer["evidence"]}
        for identifier in (
            answer["producers"]["evidence_ids"]
            + answer["consumers"]["evidence_ids"]
        ):
            self.assertEqual(
                "normalized-runtime-graph",
                evidence[identifier]["authority"],
            )

    def test_developer_execution_projects_only_execution_proven_machines(
        self,
    ) -> None:
        answer = self.build("DEVELOPER-EXECUTION-001")

        self.assertEqual("answered", answer["result_status"])
        recipes = {
            row["recipe"]["id"]: row
            for row in answer["recipes"]["items"]
        }
        self.assertEqual(
            {self.producer_id, self.consumer_id},
            set(recipes),
        )
        self.assertEqual(
            ["producer"],
            recipes[self.producer_id]["roles"],
        )
        self.assertEqual(
            [self.recipe_wrapper_id],
            [
                row["source"]["id"]
                for row in recipes[self.producer_id]["mechanics"][
                    "presentation"
                ]
            ],
        )
        self.assertEqual(
            ["consumer"],
            recipes[self.consumer_id]["roles"],
        )
        execution = {
            row["recipe"]["id"]: row
            for row in answer["executable_machines"]["items"]
        }
        self.assertEqual(
            [],
            answer["executable_machines"]["without_executable_machine"],
        )
        for recipe_id in (self.producer_id, self.consumer_id):
            self.assertEqual(
                [self.machine_id],
                [
                    row["machine"]["id"]
                    for row in execution[recipe_id]["machines"]
                ],
            )
            self.assertTrue(
                all(
                    row["machine_relationship"]["predicate"]
                    == "executes_recipe_map"
                    for row in execution[recipe_id]["machines"]
                )
            )
            self.assertNotIn(
                self.consulting_machine_id,
                {
                    row["machine"]["id"]
                    for row in execution[recipe_id]["machines"]
                },
            )
        evidence = {row["id"]: row for row in answer["evidence"]}
        self.assertEqual(
            "runtime-presentation",
            evidence[self.recipe_wrapper_id]["basis"],
        )
        self.assertNotIn(
            self.recipe_wrapper_id,
            answer["executable_machines"]["evidence_ids"],
        )
        self.assertNotIn("producers", answer)
        self.assertNotIn("consumers", answer)
        assertions = {row["path"]: row for row in answer["assertions"]}
        self.assertEqual("supported", assertions["/recipes"]["status"])
        self.assertEqual(
            "supported",
            assertions["/executable_machines"]["status"],
        )

    def test_developer_recipe_details_retain_all_exact_slot_semantics(
        self,
    ) -> None:
        answer = self.build("DEVELOPER-RECIPE-DETAILS-001")
        recipes = {
            row["recipe"]["id"]: row
            for row in answer["recipes"]["items"]
        }
        producer = recipes[self.producer_id]

        self.assertEqual(20, producer["recipe"]["attributes"]["duration"])
        self.assertEqual(30, producer["recipe"]["attributes"]["eut"])
        self.assertEqual(
            {"returned": 1, "total": 1, "truncated": False},
            {
                key: answer["recipes"]["source_pages"]["producers"][key]
                for key in ("returned", "total", "truncated")
            },
        )
        self.assertEqual(
            "AND",
            producer["ingredient_slots"]["mode"],
        )
        self.assertEqual(1, len(producer["ingredient_slots"]["slots"]))
        input_slot = producer["ingredient_slots"]["slots"][0]
        self.assertEqual(2, input_slot["slot"]["attributes"]["amount"])
        self.assertEqual("OR", input_slot["alternatives"]["mode"])
        self.assertEqual(
            [self.input_a_id, self.input_b_id],
            [
                row["node"]["id"]
                for row in input_slot["alternatives"]["items"]
            ],
        )
        input_a = input_slot["alternatives"]["items"][0]["node"]
        self.assertEqual({"grade": "a"}, input_a["attributes"]["nbt"])
        self.assertEqual(
            {"test:quality": {"level": 2}},
            input_a["attributes"]["capabilities"],
        )
        self.assertEqual(
            [self.producer_reusable_slot_id],
            [
                row["slot"]["id"]
                for row in producer["reusable_requirements"]["slots"]
            ],
        )
        self.assertEqual(
            [self.producer_slot_id],
            [row["slot"]["id"] for row in producer["output_slots"]],
        )
        self.assertNotIn("executable_machines", answer)

    def test_developer_row_classification_preserves_all_authority_states(
        self,
    ) -> None:
        category_recipe, procedural_rule = self.add_classification_rows()
        answer = self.build("DEVELOPER-ROW-CLASSIFICATION-001")

        self.assertEqual("answered", answer["result_status"])
        classified = {
            row["row"]["id"]: row
            for row in answer["classification"]["items"]
        }
        self.assertEqual(
            "runtime-executable",
            classified[self.producer_id]["classification"],
        )
        self.assertEqual(
            "runtime-executable",
            classified[self.consumer_id]["classification"],
        )
        self.assertEqual(
            "category-only",
            classified[category_recipe]["classification"],
        )
        self.assertFalse(classified[category_recipe]["eligible"])
        self.assertEqual(
            "procedural",
            classified[procedural_rule]["classification"],
        )
        self.assertEqual(
            "presentation-only",
            classified[self.recipe_wrapper_id]["classification"],
        )
        self.assertEqual(
            [
                {"classification": "category-only", "count": 1},
                {"classification": "presentation-only", "count": 1},
                {"classification": "procedural", "count": 1},
                {"classification": "runtime-executable", "count": 2},
            ],
            answer["classification"]["counts"],
        )
        self.assertEqual(
            {"runtime-mechanics", "runtime-presentation"},
            {
                row["basis"]
                for row in answer["evidence"]
                if row["id"]
                in answer["classification"]["evidence_ids"]
            },
        )
        assertions = {row["path"]: row for row in answer["assertions"]}
        self.assertEqual(
            "supported",
            assertions["/classification"]["status"],
        )

    def test_row_classification_requires_presentation_evidence(self) -> None:
        self.connection.execute(
            "DELETE FROM edges WHERE predicate = 'reconciles_to'"
        )
        self.connection.commit()

        answer = self.build("DEVELOPER-ROW-CLASSIFICATION-001")

        self.assertEqual("unresolved-evidence", answer["result_status"])
        self.assertEqual(
            ["required-evidence-missing"],
            [
                row["code"]
                for row in answer["known_gaps"]
                if row["code"] == "required-evidence-missing"
            ],
        )
        self.assertNotIn(
            "presentation-only",
            {
                row["classification"]
                for row in answer["classification"]["items"]
            },
        )

    def test_developer_profile_comparison_separates_four_scope_states(
        self,
    ) -> None:
        self.add_dedicated_common_clone()
        scopes = [
            "OFFLINE_ARTIFACT_STATE:OFFLINE",
            "COMMON_FINAL_STATE:DEDICATED_SERVER",
            "CLIENT_JEI_FINAL_STATE:CLIENT",
            "COMMON_FINAL_STATE:CLIENT",
        ]
        answer = self.build(
            "DEVELOPER-PROFILE-COMPARISON-001",
            scopes=scopes,
        )
        comparison = answer["comparison"]
        by_scope = {
            bridge._comparison_scope_key(row["scope"]): row
            for row in comparison["scopes"]
        }

        self.assertEqual(
            "answered-with-profile-gap",
            answer["result_status"],
        )
        self.assertEqual(
            "answered-with-missing-targets",
            comparison["status"],
        )
        self.assertEqual(
            {
                "mechanics": "scope-owned runtime-mechanics records only",
                "presentation": (
                    "CLIENT_JEI_FINAL_STATE records joined by exact "
                    "reconciles_to edges only"
                ),
                "missing_target": (
                    "a missing exact target is unavailable state, not an "
                    "empty observation set"
                ),
            },
            comparison["authority_policy"],
        )

        common_client = by_scope["COMMON_FINAL_STATE/CLIENT"]
        common_server = by_scope[
            "COMMON_FINAL_STATE/DEDICATED_SERVER"
        ]
        hei = by_scope["CLIENT_JEI_FINAL_STATE/CLIENT"]
        offline = by_scope["OFFLINE_ARTIFACT_STATE/OFFLINE"]

        self.assertEqual("observed", common_client["target"]["status"])
        self.assertEqual("observed", common_server["target"]["status"])
        self.assertEqual("observed", common_client["mechanics"]["status"])
        self.assertEqual("observed", common_server["mechanics"]["status"])
        self.assertEqual(
            "none-observed",
            common_client["presentation"]["status"],
        )
        self.assertEqual(
            "none-observed",
            common_server["presentation"]["status"],
        )
        self.assertEqual("missing", hei["target"]["status"])
        self.assertEqual("unavailable", hei["mechanics"]["status"])
        self.assertEqual(
            "observed-via-reconciliation",
            hei["presentation"]["status"],
        )
        self.assertEqual(
            [self.recipe_wrapper_id],
            [
                row["row"]["id"]
                for row in hei["presentation"]["items"]
            ],
        )
        self.assertEqual("missing", offline["target"]["status"])
        self.assertEqual("unavailable", offline["mechanics"]["status"])
        self.assertEqual(
            "unavailable",
            offline["presentation"]["status"],
        )

        pairs = {
            (row["left_scope"], row["right_scope"]): row
            for row in comparison["pairwise"]
        }
        common_pair = pairs[
            (
                "COMMON_FINAL_STATE/CLIENT",
                "COMMON_FINAL_STATE/DEDICATED_SERVER",
            )
        ]
        self.assertEqual("equivalent", common_pair["target"])
        self.assertEqual("equivalent", common_pair["mechanics"])
        self.assertEqual("equivalent", common_pair["presentation"])
        self.assertEqual(
            2,
            len(common_pair["mechanics_delta"]["equivalent_keys"]),
        )
        self.assertEqual(
            [],
            common_pair["mechanics_delta"]["changed"],
        )
        hei_client_pair = pairs[
            (
                "CLIENT_JEI_FINAL_STATE/CLIENT",
                "COMMON_FINAL_STATE/CLIENT",
            )
        ]
        self.assertEqual("not-comparable", hei_client_pair["target"])
        self.assertEqual(
            "not-comparable",
            hei_client_pair["mechanics"],
        )
        self.assertEqual("different", hei_client_pair["presentation"])
        self.assertEqual(
            ["presentation"],
            hei_client_pair["difference_codes"],
        )
        self.assertEqual(
            [self.recipe_wrapper_id],
            [
                row["row_id"]
                for row in hei_client_pair["presentation_delta"][
                    "left_only"
                ]
            ],
        )
        self.assertEqual(6, len(comparison["pairwise"]))
        self.assertEqual(
            [
                "COMMON_FINAL_STATE/CLIENT",
                "COMMON_FINAL_STATE/DEDICATED_SERVER",
            ],
            comparison["summary"]["observed_target_scopes"],
        )
        self.assertEqual(
            [
                "CLIENT_JEI_FINAL_STATE/CLIENT",
                "OFFLINE_ARTIFACT_STATE/OFFLINE",
            ],
            comparison["summary"]["missing_target_scopes"],
        )
        self.assertEqual(
            ["CLIENT_JEI_FINAL_STATE/CLIENT"],
            comparison["summary"]["presentation_observed_scopes"],
        )
        self.assertNotIn("classification", answer)
        assertions = {
            row["path"]: row
            for row in answer["assertions"]
        }
        self.assertEqual(
            "supported",
            assertions["/comparison"]["status"],
        )
        self.assertEqual(
            {"runtime-mechanics", "runtime-presentation"},
            {
                row["basis"]
                for row in answer["evidence"]
                if row["id"] in comparison["evidence_ids"]
            },
        )

    def test_profile_comparison_detects_common_mechanics_difference(
        self,
    ) -> None:
        identifiers = self.add_dedicated_common_clone()
        dedicated_producer = identifiers[self.producer_id]
        encoded = self.connection.execute(
            "SELECT json FROM nodes WHERE id = ?",
            (dedicated_producer,),
        ).fetchone()[0]
        record = json.loads(encoded)
        record["attributes"]["duration"] = 40
        self.connection.execute(
            "UPDATE nodes SET json = ? WHERE id = ?",
            (
                graph.canonical_json_payload(record).decode("utf-8"),
                dedicated_producer,
            ),
        )
        self.connection.commit()

        answer = self.build(
            "DEVELOPER-PROFILE-COMPARISON-001",
            scopes=[
                "COMMON_FINAL_STATE:CLIENT",
                "COMMON_FINAL_STATE:DEDICATED_SERVER",
                "CLIENT_JEI_FINAL_STATE:CLIENT",
                "OFFLINE_ARTIFACT_STATE:OFFLINE",
            ],
        )
        pair = next(
            row
            for row in answer["comparison"]["pairwise"]
            if {
                row["left_scope"],
                row["right_scope"],
            }
            == {
                "COMMON_FINAL_STATE/CLIENT",
                "COMMON_FINAL_STATE/DEDICATED_SERVER",
            }
        )
        self.assertEqual("different", pair["mechanics"])
        self.assertIn("mechanics", pair["difference_codes"])
        self.assertEqual(
            {
                "/occurrences/producers/0/owner/attributes/duration",
                "/row/attributes/duration",
            },
            {
                difference["path"]
                for changed in pair["mechanics_delta"]["changed"]
                for difference in changed["differences"]
            },
        )

    def test_profile_comparison_requires_all_four_exact_scopes(self) -> None:
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "requires exactly COMMON client",
        ):
            self.build(
                "DEVELOPER-PROFILE-COMPARISON-001",
                scopes=["COMMON_FINAL_STATE:CLIENT"],
            )

    def test_profile_comparison_requires_presentation_evidence(self) -> None:
        self.add_dedicated_common_clone()
        self.connection.execute(
            "DELETE FROM edges WHERE predicate = 'reconciles_to'"
        )
        self.connection.commit()
        answer = self.build(
            "DEVELOPER-PROFILE-COMPARISON-001",
            scopes=[
                "COMMON_FINAL_STATE:CLIENT",
                "COMMON_FINAL_STATE:DEDICATED_SERVER",
                "CLIENT_JEI_FINAL_STATE:CLIENT",
                "OFFLINE_ARTIFACT_STATE:OFFLINE",
            ],
        )
        by_scope = {
            bridge._comparison_scope_key(row["scope"]): row
            for row in answer["comparison"]["scopes"]
        }
        self.assertEqual("unresolved-evidence", answer["result_status"])
        self.assertEqual(
            "unavailable",
            by_scope["CLIENT_JEI_FINAL_STATE/CLIENT"][
                "presentation"
            ]["status"],
        )
        self.assertIn(
            "required-evidence-missing",
            {row["code"] for row in answer["known_gaps"]},
        )

    def test_player_verification_scope_is_explicitly_bounded(self) -> None:
        answer = self.build("PLAYER-VERIFICATION-SCOPE-001")
        confidence = answer["confidence"]

        self.assertEqual("answered", answer["result_status"])
        self.assertEqual("verified-with-known-gaps", confidence["status"])
        self.assertEqual(
            SNAPSHOT_ID,
            confidence["snapshot_id"],
        )
        self.assertEqual(answer["profile_scope"], confidence["profile_scope"])
        self.assertEqual(
            {
                "global_validity_claimed": False,
                "profile_comparison_evaluated": False,
                "mechanical_route_evaluated": False,
                "quest_prerequisites_are_mechanical": False,
            },
            confidence["boundaries"],
        )
        checks = {row["name"]: row for row in confidence["checks"]}
        self.assertEqual("passed", checks["exact-identity"]["status"])
        self.assertEqual("passed", checks["snapshot-bound"]["status"])
        self.assertEqual("passed", checks["profile-coverage"]["status"])
        self.assertEqual(
            "passed",
            checks["required-evidence-bases"]["status"],
        )
        self.assertEqual("passed", checks["quest-guidance-link"]["status"])
        self.assertEqual(
            ["ownership-unresolved"],
            confidence["known_gap_codes"],
        )
        self.assertEqual(
            [],
            confidence["evidence_bases"]["missing"],
        )

    def test_player_verification_scope_reports_partial_profile_coverage(
        self,
    ) -> None:
        answer = self.build(
            "PLAYER-VERIFICATION-SCOPE-001",
            scopes=[
                "COMMON_FINAL_STATE:CLIENT",
                "COMMON_FINAL_STATE:DEDICATED_SERVER",
            ],
        )
        checks = {
            row["name"]: row
            for row in answer["confidence"]["checks"]
        }

        self.assertEqual(
            "answered-with-profile-gap",
            answer["result_status"],
        )
        self.assertEqual(
            "verified-with-known-gaps",
            answer["confidence"]["status"],
        )
        self.assertEqual("partial", checks["profile-coverage"]["status"])
        self.assertIn(
            "COMMON_FINAL_STATE/DEDICATED_SERVER",
            checks["profile-coverage"]["detail"],
        )
        self.assertEqual(
            ["ownership-unresolved", "profile-gap"],
            answer["confidence"]["known_gap_codes"],
        )

    def test_player_produce_material_projects_bounded_route_dag(self) -> None:
        answer = self.build("PLAYER-PRODUCE-MATERIAL-001")
        self.assertEqual("answered", answer["result_status"])
        routes = answer["routes"]
        self.assertEqual("answered", routes["status"])
        self.assertEqual(1, len(routes["items"]))
        self.assertEqual(
            self.producer_id,
            routes["items"][0]["producer"]["id"],
        )
        self.assertEqual(
            {
                "truncated": False,
                "reasons": [],
                "limits": {
                    "max_depth": 8,
                    "max_routes": 50,
                    "max_alternatives_per_slot": 25,
                    "max_visited_nodes": 10000,
                },
            },
            routes["truncation"],
        )
        assertions = {row["path"]: row for row in answer["assertions"]}
        self.assertEqual("supported", assertions["/routes"]["status"])

    def test_player_ingredient_grouping_is_a_per_route_and_or_projection(
        self,
    ) -> None:
        answer = self.build("PLAYER-INGREDIENT-GROUPING-001")
        projection = answer["ingredient_slots"]

        self.assertEqual("answered", projection["status"])
        self.assertEqual(1, len(projection["items"]))
        route = projection["items"][0]
        self.assertEqual(self.producer_id, route["producer"]["id"])
        self.assertEqual("AND", route["mode"])
        self.assertEqual(1, len(route["slots"]))
        self.assertTrue(route["slots"][0]["semantics"]["jointly_required"])
        self.assertEqual(
            "OR",
            route["slots"][0]["alternatives"]["mode"],
        )
        self.assertEqual(
            [self.input_a_id, self.input_b_id],
            [
                row["target"]["id"]
                for row in route["slots"][0]["alternatives"]["items"]
            ],
        )
        assertions = {row["path"]: row for row in answer["assertions"]}
        self.assertEqual(
            "supported",
            assertions["/ingredient_slots"]["status"],
        )

    def test_player_reusable_requirements_stay_non_consumable(
        self,
    ) -> None:
        answer = self.build("PLAYER-REUSABLE-REQUIREMENTS-001")
        projection = answer["reusable_requirements"]

        self.assertEqual("answered", projection["status"])
        self.assertEqual(1, len(projection["items"]))
        route = projection["items"][0]
        self.assertEqual("AND", route["mode"])
        self.assertEqual(1, len(route["slots"]))
        requirement = route["slots"][0]
        self.assertTrue(requirement["semantics"]["reusable"])
        self.assertFalse(requirement["semantics"]["consumed"])
        self.assertEqual(
            [self.catalyst_id],
            [
                row["target"]["id"]
                for row in requirement["alternatives"]["items"]
            ],
        )
        assertions = {row["path"]: row for row in answer["assertions"]}
        self.assertEqual(
            "supported",
            assertions["/reusable_requirements"]["status"],
        )

    def test_byproducts_are_a_lossless_evidence_closed_route_projection(
        self,
    ) -> None:
        identifiers = self.add_byproduct_routes()
        question_path = self.byproduct_question_path()
        answer = self.build(
            "PLAYER-BYPRODUCTS-PROJECTION-001",
            question_path=question_path,
        )
        routes = answer["routes"]
        projection = answer["byproducts"]

        self.assertEqual("answered", answer["result_status"])
        self.assertEqual("answered", projection["status"])
        self.assertEqual(routes["target"], projection["target"])
        self.assertEqual(routes["truncation"], projection["truncation"])
        self.assertEqual(
            {
                "source": "retained-route-co-output-slots",
                "classification": "mechanical-co-output-not-economic-waste",
                "aggregate_yield": "not-derived",
            },
            projection["semantics"],
        )
        self.assertEqual(
            {
                "route_count": 2,
                "routes_with_byproducts": 1,
                "output_slot_count": 2,
                "alternative_count": 3,
                "guaranteed_slot_count": 1,
                "conditional_slot_count": 1,
            },
            projection["summary"],
        )
        self.assertEqual(
            [route["id"] for route in routes["items"]],
            [item["route_id"] for item in projection["items"]],
        )

        by_producer = {
            item["producer"]["id"]: item
            for item in projection["items"]
        }
        with_byproducts = by_producer[self.producer_id]
        without_byproducts = by_producer[identifiers["empty_producer"]]
        source_route = next(
            route
            for route in routes["items"]
            if route["producer"]["id"] == self.producer_id
        )
        self.assertEqual(
            source_route["production"],
            with_byproducts["selected_production"],
        )
        self.assertEqual(
            source_route["byproducts"],
            with_byproducts["slots"],
        )
        self.assertEqual([], without_byproducts["slots"])
        self.assertEqual(2, len(with_byproducts["slots"]))

        guaranteed, conditional = with_byproducts["slots"]
        self.assertTrue(guaranteed["semantics"]["guaranteed"])
        self.assertFalse(guaranteed["semantics"]["conditional"])
        self.assertEqual(
            [
                identifiers["byproduct_a"],
                identifiers["byproduct_b"],
            ],
            [
                alternative["node"]["id"]
                for alternative in guaranteed["alternatives"]["items"]
            ],
        )
        self.assertTrue(conditional["semantics"]["conditional"])
        self.assertFalse(conditional["semantics"]["guaranteed"])
        self.assertEqual(
            2500,
            conditional["chance"]["owner_relationship"]["chance"],
        )
        self.assertEqual(
            [identifiers["byproduct_c"]],
            [
                alternative["node"]["id"]
                for alternative in conditional["alternatives"]["items"]
            ],
        )

        expected_payload = bridge._byproduct_payload(routes)
        self.assertEqual(
            expected_payload,
            {
                key: value
                for key, value in projection.items()
                if key != "evidence_ids"
            },
        )
        projected_records = bridge._runtime_records_in(projection)
        self.assertEqual(
            set(projected_records),
            set(projection["evidence_ids"]),
        )
        evidence_by_id = {
            row["id"]: row for row in answer["evidence"]
        }
        self.assertTrue(
            all(
                evidence_by_id[identifier]["basis"]
                == "runtime-mechanics"
                for identifier in projection["evidence_ids"]
            )
        )
        assertions = {row["path"]: row for row in answer["assertions"]}
        self.assertEqual("supported", assertions["/byproducts"]["status"])
        self.assertEqual(
            projection["evidence_ids"],
            assertions["/byproducts"]["evidence_ids"],
        )
        self.assertNotIn("recycling_paths", answer)

    def test_byproduct_semantic_and_evidence_drift_fail_closed(self) -> None:
        identifiers = self.add_byproduct_routes()
        question_path = self.byproduct_question_path()
        question = bridge._load_question(
            question_path,
            "PLAYER-BYPRODUCTS-PROJECTION-001",
        )
        answer = self.build(
            "PLAYER-BYPRODUCTS-PROJECTION-001",
            question_path=question_path,
        )

        semantic_drift = copy.deepcopy(answer)
        semantic_drift["byproducts"]["summary"]["output_slot_count"] += 1
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "differs from its lossless route projection",
        ):
            bridge.validate_answer(semantic_drift, question)

        evidence_drift = copy.deepcopy(answer)
        evidence_drift["byproducts"]["evidence_ids"].remove(
            identifiers["byproduct_a"]
        )
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "byproducts runtime evidence closure differs",
        ):
            bridge.validate_answer(evidence_drift, question)

        malformed_routes = copy.deepcopy(answer["routes"])
        source_route = next(
            route
            for route in malformed_routes["items"]
            if route["producer"]["id"] == self.producer_id
        )
        source_route["byproducts"][0]["relationship"][
            "predicate"
        ] = "requires"
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "non-output predicate",
        ):
            bridge._byproduct_payload(malformed_routes)

    def test_final_byproducts_recycling_question_is_evidence_closed(
        self,
    ) -> None:
        identifiers = self.add_byproduct_routes()
        recycling_ids = self.add_recycling_routes(identifiers)
        answer = self.build("PLAYER-BYPRODUCTS-RECYCLING-001")
        projection = answer["recycling_paths"]

        self.assertEqual("answered", answer["result_status"])
        self.assertEqual("answered", projection["status"])
        self.assertEqual(
            {
                "root_definition": (
                    "exact-deduplicated-byproduct-alternatives"
                ),
                "consumer_policy": "consumes-and-may-consume-only",
                "requires_policy": "reusable-not-recycling",
                "closure_policy": (
                    "exact-final-target-route-reentry-or-forward-cycle"
                ),
                "chance_policy": "stage-local-not-multiplied",
                "yield_policy": "not-balanced-or-optimized",
            },
            projection["semantics"],
        )
        self.assertEqual(5, projection["summary"]["root_count"])
        self.assertEqual(
            5,
            projection["summary"]["visited_target_count"],
        )
        self.assertEqual(
            4,
            projection["summary"]["retained_operation_count"],
        )
        self.assertEqual(
            1,
            projection["summary"]["reusable_only_candidate_count"],
        )
        self.assertEqual(
            {
                self.consumer_id,
                recycling_ids["consume_a"],
                recycling_ids["consume_b"],
                self.producer_id,
            },
            {
                operation["owner"]["id"]
                for operation in projection["operations"]
            },
        )
        self.assertNotIn(
            recycling_ids["requires_c"],
            {
                operation["owner"]["id"]
                for operation in projection["operations"]
            },
        )
        self.assertEqual(
            {
                "returns-final-target",
                "rejoins-retained-route",
                "open-terminal",
            },
            {
                closure["classification"]
                for closure in projection["closures"]
            },
        )
        self.assertEqual([], projection["cycles"])
        self.assertFalse(projection["truncation"]["truncated"])

        records = bridge._runtime_records_in(projection)
        self.assertEqual(
            set(records),
            set(projection["evidence_ids"]),
        )
        assertions = {row["path"]: row for row in answer["assertions"]}
        self.assertEqual(
            "supported",
            assertions["/recycling_paths"]["status"],
        )
        self.assertEqual(
            projection["evidence_ids"],
            assertions["/recycling_paths"]["evidence_ids"],
        )

        question = bridge._load_question(
            QUESTIONS_PATH,
            "PLAYER-BYPRODUCTS-RECYCLING-001",
        )
        semantic_drift = copy.deepcopy(answer)
        semantic_drift["recycling_paths"]["summary"][
            "retained_operation_count"
        ] += 1
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "semantic recomputation failed",
        ):
            bridge.validate_answer(semantic_drift, question)

        evidence_drift = copy.deepcopy(answer)
        evidence_drift["recycling_paths"]["evidence_ids"].remove(
            identifiers["byproduct_a"]
        )
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "recycling_paths runtime evidence closure differs",
        ):
            bridge.validate_answer(evidence_drift, question)

    def test_recycling_reports_no_roots_without_global_absence_claim(
        self,
    ) -> None:
        answer = self.build("PLAYER-BYPRODUCTS-RECYCLING-001")
        projection = answer["recycling_paths"]

        self.assertEqual("answered", answer["result_status"])
        self.assertEqual("no-byproduct-roots", projection["status"])
        self.assertEqual([], projection["roots"])
        self.assertEqual([], projection["operations"])
        self.assertEqual(0, projection["summary"]["root_count"])
        self.assertFalse(projection["truncation"]["truncated"])
        self.assertNotIn(
            "not recyclable anywhere",
            graph.canonical_json_payload(answer).decode("utf-8"),
        )

    def test_recycling_command_line_limits_are_independent(self) -> None:
        args = bridge._parser().parse_args(
            [
                "answer",
                "--runtime-database",
                str(self.database),
                "--question-id",
                "PLAYER-BYPRODUCTS-RECYCLING-001",
                "--snapshot-id",
                SNAPSHOT_ID,
                "--scope",
                "COMMON_FINAL_STATE:CLIENT",
                "--recycling-max-depth",
                "2",
                "--recycling-max-operations",
                "17",
                "--recycling-max-consumers-per-target",
                "3",
                "--recycling-max-output-alternatives-per-slot",
                "5",
                "--recycling-max-visited-targets",
                "71",
            ]
        )

        self.assertEqual(2, args.recycling_max_depth)
        self.assertEqual(17, args.recycling_max_operations)
        self.assertEqual(3, args.recycling_max_consumers_per_target)
        self.assertEqual(
            5,
            args.recycling_max_output_alternatives_per_slot,
        )
        self.assertEqual(71, args.recycling_max_visited_targets)

    def test_player_build_prerequisites_projects_route_readiness(
        self,
    ) -> None:
        alternate_machine_id = (
            "rg:common_final_state_client:machine:test:alternate_atlas_machine"
        )
        self.add_node(
            alternate_machine_id,
            "machine",
            "gt_meta_tile_entities",
            {"registry_key": "test:alternate_atlas_machine"},
        )
        self.add_edge(
            "executes_recipe_map",
            alternate_machine_id,
            self.recipe_map_id,
            "gt_machine_recipe_maps",
            {"selection": "alternate-direct"},
        )
        self.connection.commit()

        answer = self.build("PLAYER-BUILD-PREREQUISITES-001")
        routes = answer["routes"]
        projection = answer["prerequisites"]

        self.assertEqual("answered", answer["result_status"])
        self.assertEqual("answered", projection["status"])
        self.assertEqual(routes["target"], projection["target"])
        self.assertEqual(routes["roots"], projection["roots"])
        self.assertEqual(routes["subproblems"], projection["subproblems"])
        self.assertEqual(routes["cycles"], projection["cycles"])
        self.assertEqual(
            routes["unresolved_leaves"],
            projection["unresolved"],
        )
        self.assertEqual(routes["truncation"], projection["truncation"])
        self.assertEqual(
            {
                "player_inventory": "unknown",
                "existing_infrastructure": "unknown",
                "quest_ordering": "guidance-only-not-mechanical",
                "route_preference": "none",
                "completeness": "bounded-observed-route-dag",
            },
            projection["assumptions"],
        )

        self.assertEqual(1, len(projection["operations"]))
        operation = projection["operations"][0]
        self.assertEqual(routes["items"][0]["id"], operation["route_id"])
        self.assertEqual(
            routes["items"][0]["subproblem_id"],
            operation["subproblem_id"],
        )
        self.assertEqual(
            routes["items"][0]["production"],
            operation["production"],
        )
        self.assertEqual("AND", operation["requirements"]["mode"])
        groups = operation["requirements"]["groups"]
        self.assertEqual(
            [
                "executable-machine",
                "consumed-input",
                "reusable-requirement",
            ],
            [group["kind"] for group in groups],
        )

        machine_group = groups[0]
        self.assertEqual("OR", machine_group["mode"])
        self.assertEqual("resolved", machine_group["status"])
        self.assertEqual(
            {self.machine_id, alternate_machine_id},
            {
                row["machine"]["id"]
                for row in machine_group["items"]
            },
        )
        self.assertNotIn(
            self.consulting_machine_id,
            {
                row["machine"]["id"]
                for row in machine_group["items"]
            },
        )
        self.assertEqual(
            {"executes_recipe_map"},
            {
                binding["machine_relationship"]["predicate"]
                for row in machine_group["items"]
                for binding in row["execution_bindings"]
            },
        )

        consumed_group = groups[1]
        reusable_group = groups[2]
        self.assertEqual("OR", consumed_group["mode"])
        self.assertEqual("OR", reusable_group["mode"])
        self.assertEqual(
            [self.input_a_id, self.input_b_id],
            [
                row["target"]["id"]
                for row in consumed_group["requirement"][
                    "alternatives"
                ]["items"]
            ],
        )
        self.assertTrue(
            reusable_group["requirement"]["semantics"]["reusable"]
        )
        self.assertFalse(
            reusable_group["requirement"]["semantics"]["consumed"]
        )

        self.assertEqual(3, len(projection["dependency_edges"]))
        self.assertEqual(
            {"consumed-input", "reusable-requirement"},
            {
                row["requirement_kind"]
                for row in projection["dependency_edges"]
            },
        )
        self.assertEqual(
            {
                alternative["subproblem_id"]
                for group in (consumed_group, reusable_group)
                for alternative in group["requirement"]["alternatives"]["items"]
            },
            {
                row["from_subproblem_id"]
                for row in projection["dependency_edges"]
            },
        )
        self.assertNotIn(
            self.consulting_machine_id,
            projection["evidence_ids"],
        )
        self.assertNotIn(
            self.recipe_wrapper_id,
            projection["evidence_ids"],
        )
        self.assertEqual(
            {"runtime-mechanics"},
            {
                row["basis"]
                for row in answer["evidence"]
                if row["id"] in projection["evidence_ids"]
            },
        )
        assertions = {row["path"]: row for row in answer["assertions"]}
        self.assertEqual(
            "supported",
            assertions["/prerequisites"]["status"],
        )

    def test_prerequisite_machine_status_requires_execution_or_boundary(
        self,
    ) -> None:
        routes = copy.deepcopy(
            self.build("PLAYER-PRODUCE-MATERIAL-001")["routes"]
        )
        route = routes["items"][0]
        route["mechanics"]["execution"] = []
        route["boundaries"] = [
            {
                "producer": route["producer"],
                "status": "worldgen-acquisition-no-machine-required",
            }
        ]

        payload = bridge._prerequisite_payload(routes)
        machine_group = payload["operations"][0]["requirements"]["groups"][0]
        self.assertEqual("not-required", machine_group["status"])
        self.assertEqual([], machine_group["items"])

        route["boundaries"][0]["status"] = "procedural-no-machine-binding"
        payload = bridge._prerequisite_payload(routes)
        machine_group = payload["operations"][0]["requirements"]["groups"][0]
        self.assertEqual("unresolved", machine_group["status"])
        self.assertEqual([], machine_group["items"])

    def test_machine_construction_seeds_aggregate_exact_route_provenance(
        self,
    ) -> None:
        alternate_machine_id = (
            "rg:common_final_state_client:machine:test:alternate_seed_machine"
        )
        self.add_node(
            alternate_machine_id,
            "machine",
            "gt_meta_tile_entities",
            {"registry_key": "test:alternate_seed_machine"},
        )
        self.add_edge(
            "executes_recipe_map",
            alternate_machine_id,
            self.recipe_map_id,
            "gt_machine_recipe_maps",
            {"selection": "alternate-direct"},
        )
        self.connection.commit()

        prerequisites = copy.deepcopy(
            self.build("PLAYER-BUILD-PREREQUISITES-001")[
                "prerequisites"
            ]
        )
        repeated = copy.deepcopy(prerequisites["operations"][0])
        repeated["route_id"] = "route:second-machine-use"
        repeated["subproblem_id"] = "subproblem:second-machine-use"
        prerequisites["operations"].append(repeated)

        seeds = bridge._machine_construction_seeds(prerequisites)
        by_id = {seed["machine"]["id"]: seed for seed in seeds}

        self.assertEqual(
            {self.machine_id, alternate_machine_id},
            set(by_id),
        )
        for machine_id in sorted(by_id):
            seed = by_id[machine_id]
            self.assertEqual(
                [
                    prerequisites["operations"][0]["route_id"],
                    "route:second-machine-use",
                ],
                [row["route_id"] for row in seed["required_by"]],
            )
            self.assertEqual(
                {machine_id},
                {
                    binding["machine"]["id"]
                    for row in seed["required_by"]
                    for binding in row["execution_bindings"]
                },
            )

    def test_infrastructure_handoff_retains_both_operation_domains(
        self,
    ) -> None:
        answer = self.build("PLAYER-BUILD-PREREQUISITES-001")
        material_operation = copy.deepcopy(answer["routes"]["items"][0])
        construction_operation = copy.deepcopy(material_operation)
        construction_operation["id"] = "route:construction-operation"
        construction_operation[
            "subproblem_id"
        ] = "subproblem:construction-operation"
        machine_construction = {
            "construction_forest": {
                "items": [construction_operation],
            },
        }

        seeds = bridge._infrastructure_operation_seeds(
            {"items": [material_operation]},
            machine_construction,
        )

        self.assertEqual(
            ["material-route", "construction-route"],
            [row["origin"] for row in seeds],
        )
        self.assertEqual(material_operation, seeds[0]["operation"])
        self.assertEqual(construction_operation, seeds[1]["operation"])
        for row in seeds:
            subjects = row["condition_subjects"]
            self.assertEqual(
                "operation-owner",
                subjects[0]["role"],
            )
            self.assertEqual(
                row["operation"]["producer"],
                subjects[0]["record"],
            )
            self.assertEqual(
                {self.machine_id},
                {
                    subject["record"]["id"]
                    for subject in subjects[1:]
                },
            )
            self.assertTrue(
                all(
                    subject["role"] == "execution-machine"
                    for subject in subjects[1:]
                )
            )

    def test_infrastructure_candidates_collect_without_admission(
        self,
    ) -> None:
        constraint_id = (
            "rg:common_final_state_client:constraint:test:cleanroom"
        )
        energy_id = "rg:common_final_state_client:energy_carrier:test:eu"
        effect_slot_id = (
            "rg:common_final_state_client:ingredient_slot:test:environment"
        )
        environment_id = (
            "rg:common_final_state_client:ingredient:test:environment"
        )
        self.add_node(
            constraint_id,
            "constraint",
            "gt_procedural_machine_rules",
            {"constraint_kind": "cleanroom_recipe_envelope"},
        )
        self.add_node(
            energy_id,
            "energy_carrier",
            "gt_procedural_machine_rules",
            {"carrier": "gregtech:eu"},
        )
        self.add_node(
            effect_slot_id,
            "ingredient_slot",
            "gt_procedural_machine_rules",
            {"ordinal": 0},
        )
        self.add_node(
            environment_id,
            "ingredient",
            "gt_procedural_machine_rules",
            {"domain": "cleanroom_state"},
        )
        self.add_edge(
            "has_constraint",
            self.producer_id,
            constraint_id,
            "gt_procedural_machine_rules",
            {},
        )
        self.add_edge(
            "uses_energy",
            self.machine_id,
            energy_id,
            "gt_procedural_machine_rules",
            {},
        )
        self.add_edge(
            "affects",
            self.producer_id,
            effect_slot_id,
            "gt_procedural_machine_rules",
            {"ordinal": 0},
        )
        self.add_edge(
            "accepts_alternative",
            effect_slot_id,
            environment_id,
            "gt_procedural_machine_rules",
            {"alternative_ordinal": 0},
        )
        self.connection.commit()

        answer = self.build("PLAYER-MACHINE-CONSTRUCTION-001")
        with bridge.RuntimeGraphReader(self.database) as reader:
            candidates = bridge._infrastructure_candidate_payload(
                reader,
                answer["routes"],
                answer["machine_construction"],
            )

        self.assertEqual("collected", candidates["status"])
        self.assertEqual(
            bridge.INFRASTRUCTURE_CANDIDATE_ASSUMPTIONS,
            candidates["assumptions"],
        )
        self.assertEqual(
            {
                "operation_count": 1,
                "unique_subject_count": 2,
                "candidate_path_count": 4,
                "unique_candidate_path_count": 4,
                "path_kind_counts": {"direct": 2, "slot": 2},
                "predicate_counts": {
                    "affects": 1,
                    "has_constraint": 1,
                    "requires": 1,
                    "uses_energy": 1,
                },
                "unique_predicate_counts": {
                    "affects": 1,
                    "has_constraint": 1,
                    "requires": 1,
                    "uses_energy": 1,
                },
            },
            candidates["summary"],
        )
        paths = [
            path
            for operation in candidates["operations"]
            for subject in operation["condition_subjects"]
            for path in subject["candidate_paths"]
        ]
        self.assertEqual(
            {
                constraint_id,
                energy_id,
                effect_slot_id,
                self.producer_reusable_slot_id,
            },
            {
                (
                    path["condition"]["id"]
                    if path["path_kind"] == "direct"
                    else path["slot"]["id"]
                )
                for path in paths
            },
        )
        self.assertTrue(
            all(
                "admission" not in path
                and "requirement_type" not in path
                for path in paths
            )
        )

    def test_infrastructure_answer_is_source_and_runtime_recomputable(
        self,
    ) -> None:
        energy_id = (
            "rg:common_final_state_client:energy_carrier:test:eu"
        )
        effective_methods_id = (
            "rg:common_final_state_client:constraint:"
            "test:effective-method-owners"
        )
        self.add_node(
            energy_id,
            "energy_carrier",
            "gt_procedural_machine_rules",
            {"carrier": "gregtech:eu"},
        )
        self.add_node(
            effective_methods_id,
            "constraint",
            "gt_procedural_machine_rules",
            {
                "constraint_kind": "effective_method_owners",
                "logic_methods": {
                    "calculateOverclock": (
                        "gregtech.api.capability.impl.AbstractRecipeLogic"
                    ),
                },
            },
        )
        energy_edge_id = self.add_edge(
            "uses_energy",
            self.machine_id,
            energy_id,
            "gt_procedural_machine_rules",
            {},
        )
        self.add_edge(
            "has_constraint",
            self.machine_id,
            effective_methods_id,
            "gt_procedural_machine_rules",
            {},
        )
        self.connection.commit()

        answer = self.build(
            "PLAYER-INFRASTRUCTURE-REQUIREMENTS-001"
        )
        projection = answer["infrastructure_requirements"]

        self.assertEqual(
            "susy-infrastructure-requirements-v1",
            projection["format"],
        )
        self.assertEqual("partial", projection["status"])
        self.assertEqual(
            "SUSY-INFRASTRUCTURE-SOURCE-AUTHORITIES-0001",
            projection["source_authority_registry_id"],
        )
        self.assertIn(energy_edge_id, projection["evidence_ids"])
        self.assertTrue(projection["used_citation_ids"])
        self.assertTrue(
            set(projection["used_citation_ids"]).issubset(
                projection["evidence_ids"]
            )
        )
        self.assertNotIn("machine_construction", answer)
        self.assertIn(
            "infrastructure_authorities",
            answer["pinned_source"],
        )
        self.assertEqual(
            {"normalized-runtime-graph", "source-authority-registry"},
            {
                row["authority"]
                for row in answer["evidence"]
                if row["id"] in projection["evidence_ids"]
            },
        )

        question = bridge._load_question(
            QUESTIONS_PATH,
            "PLAYER-INFRASTRUCTURE-REQUIREMENTS-001",
        )
        semantic_drift = copy.deepcopy(answer)
        energy = next(
            row
            for row in semantic_drift["infrastructure_requirements"][
                "definitions"
            ]
            if row["kind"] == "energy-and-voltage"
        )
        energy["facts"]["minimum_recipe_tier"]["short_name"] = "HV"
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "semantic recomputation failed",
        ):
            bridge.validate_answer(semantic_drift, question)

        evidence_drift = copy.deepcopy(answer)
        evidence_drift["infrastructure_requirements"][
            "evidence_ids"
        ].remove(energy_edge_id)
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "evidence closure differs",
        ):
            bridge.validate_answer(evidence_drift, question)

    def test_player_machine_construction_closes_exact_forms_and_cycles(
        self,
    ) -> None:
        form_id = (
            "rg:common_final_state_client:item_variant:"
            "test:atlas_machine:stack"
        )
        construction_recipe_id = (
            "rg:common_final_state_client:recipe:"
            "test_atlas:construct_machine"
        )
        construction_slot_id = (
            "rg:common_final_state_client:ingredient_slot:"
            "test_atlas:construct_machine:output:0"
        )
        self.add_node(
            form_id,
            "item_variant",
            "gt_meta_tile_entities",
            {"registry_name": "gregtech:machine", "metadata": 42},
        )
        form_edge_id = self.add_edge(
            "has_form",
            self.machine_id,
            form_id,
            "gt_meta_tile_entities",
            {"role": "registered_mte_stack_form"},
        )
        self.add_node(
            construction_recipe_id,
            "recipe",
            "gt_recipes",
            {"native_recipe_map": "test_atlas"},
        )
        self.add_node(
            construction_slot_id,
            "ingredient_slot",
            "gt_recipes",
            {"amount": 1, "channel": "item_output", "ordinal": 0},
        )
        self.add_edge(
            "has_recipe",
            self.recipe_map_id,
            construction_recipe_id,
            "gt_recipes",
            {"lookup_active": True, "category_present": True},
        )
        self.add_edge(
            "produces",
            construction_recipe_id,
            construction_slot_id,
            "gt_recipes",
            {},
        )
        self.add_edge(
            "accepts_alternative",
            construction_slot_id,
            form_id,
            "gt_recipes",
            {"alternative_ordinal": 0},
        )
        self.connection.commit()

        answer = self.build("PLAYER-MACHINE-CONSTRUCTION-001")
        projection = answer["machine_construction"]

        self.assertEqual("answered", answer["result_status"])
        self.assertEqual("answered", projection["status"])
        self.assertEqual(1, len(projection["machines"]))
        machine = projection["machines"][0]
        self.assertEqual(self.machine_id, machine["machine"]["id"])
        self.assertEqual(
            ["construction-route", "material-route"],
            machine["origins"],
        )
        resolution = machine["form_resolution"]
        self.assertEqual("resolved", resolution["status"])
        self.assertEqual(form_edge_id, resolution["selected_relationship_id"])
        self.assertIsNotNone(resolution["root_subproblem_id"])
        self.assertEqual(
            "admitted",
            resolution["paths"][0]["admission"],
        )

        forest = projection["construction_forest"]
        self.assertEqual(1, len(forest["roots"]))
        self.assertEqual(
            [construction_recipe_id],
            [row["producer"]["id"] for row in forest["items"]],
        )
        self.assertEqual(1, len(projection["machine_dependency_edges"]))
        dependency = projection["machine_dependency_edges"][0]
        self.assertEqual("resolved", dependency["status"])
        self.assertEqual(
            resolution["root_subproblem_id"],
            dependency["to_subproblem_id"],
        )
        self.assertEqual(1, len(projection["bootstrapping_cycles"]))
        self.assertEqual(
            {"execution-machine"},
            {
                row["kind"]
                for row in projection["bootstrapping_cycles"][0][
                    "dependencies"
                ]
            },
        )
        self.assertEqual([], projection["unresolved"])
        self.assertFalse(projection["truncation"]["truncated"])
        self.assertIn(form_edge_id, projection["evidence_ids"])
        self.assertEqual(
            {"runtime-mechanics"},
            {
                row["basis"]
                for row in answer["evidence"]
                if row["id"] in projection["evidence_ids"]
            },
        )
        question = bridge._load_question(
            QUESTIONS_PATH,
            "PLAYER-MACHINE-CONSTRUCTION-001",
        )
        dependency_drift = copy.deepcopy(answer)
        dependency_drift["machine_construction"][
            "machine_dependency_edges"
        ][0]["to_subproblem_id"] = None
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "dependency resolution differs",
        ):
            bridge.validate_answer(dependency_drift, question)

        cycle_drift = copy.deepcopy(answer)
        cycle_drift["machine_construction"]["bootstrapping_cycles"] = []
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "cycles differ",
        ):
            bridge.validate_answer(cycle_drift, question)

        evidence_drift = copy.deepcopy(answer)
        evidence_drift["machine_construction"]["evidence_ids"].remove(
            form_edge_id
        )
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "runtime evidence closure differs",
        ):
            bridge.validate_answer(evidence_drift, question)

    def test_player_machine_construction_preserves_missing_and_ambiguous_forms(
        self,
    ) -> None:
        answer = self.build("PLAYER-MACHINE-CONSTRUCTION-001")
        projection = answer["machine_construction"]
        self.assertEqual([], projection["construction_forest"]["roots"])
        self.assertEqual(
            ["unresolved-form"],
            [row["reason"] for row in projection["unresolved"]],
        )

        first_form = (
            "rg:common_final_state_client:item_variant:"
            "test:atlas_machine:ambiguous_a"
        )
        second_form = (
            "rg:common_final_state_client:item_variant:"
            "test:atlas_machine:ambiguous_b"
        )
        for form_id in (first_form, second_form):
            self.add_node(
                form_id,
                "item_variant",
                "gt_meta_tile_entities",
                {"registry_name": "gregtech:machine"},
            )
            self.add_edge(
                "has_form",
                self.machine_id,
                form_id,
                "gt_meta_tile_entities",
                {"role": "registered_mte_stack_form"},
            )
        self.connection.commit()

        answer = self.build("PLAYER-MACHINE-CONSTRUCTION-001")
        projection = answer["machine_construction"]
        resolution = projection["machines"][0]["form_resolution"]
        self.assertEqual("ambiguous", resolution["status"])
        self.assertIsNone(resolution["selected_relationship_id"])
        self.assertIsNone(resolution["root_subproblem_id"])
        self.assertEqual(
            ["ambiguous-form"],
            [row["reason"] for row in projection["unresolved"]],
        )

    def test_machine_construction_deduplicates_shared_exact_forms(
        self,
    ) -> None:
        alternate_machine_id = (
            "rg:common_final_state_client:machine:test:shared_form_machine"
        )
        form_id = (
            "rg:common_final_state_client:item_variant:"
            "test:shared_machine_form"
        )
        recipe_id = (
            "rg:common_final_state_client:recipe:"
            "test_atlas:shared_machine_form"
        )
        slot_id = (
            "rg:common_final_state_client:ingredient_slot:"
            "test_atlas:shared_machine_form:output:0"
        )
        self.add_node(
            alternate_machine_id,
            "machine",
            "gt_meta_tile_entities",
            {"registry_key": "test:shared_form_machine"},
        )
        self.add_node(
            form_id,
            "item_variant",
            "gt_meta_tile_entities",
            {"registry_name": "gregtech:machine"},
        )
        self.add_node(
            recipe_id,
            "recipe",
            "gt_recipes",
            {"native_recipe_map": "test_atlas"},
        )
        self.add_node(
            slot_id,
            "ingredient_slot",
            "gt_recipes",
            {"amount": 1, "channel": "item_output", "ordinal": 0},
        )
        self.add_edge(
            "executes_recipe_map",
            alternate_machine_id,
            self.recipe_map_id,
            "gt_machine_recipe_maps",
            {"selection": "direct"},
        )
        for machine_id in (self.machine_id, alternate_machine_id):
            self.add_edge(
                "has_form",
                machine_id,
                form_id,
                "gt_meta_tile_entities",
                {"role": "registered_mte_stack_form"},
            )
        self.add_edge(
            "has_recipe",
            self.recipe_map_id,
            recipe_id,
            "gt_recipes",
            {"lookup_active": True, "category_present": True},
        )
        self.add_edge(
            "produces",
            recipe_id,
            slot_id,
            "gt_recipes",
            {},
        )
        self.add_edge(
            "accepts_alternative",
            slot_id,
            form_id,
            "gt_recipes",
            {"alternative_ordinal": 0},
        )
        self.connection.commit()

        projection = self.build(
            "PLAYER-MACHINE-CONSTRUCTION-001"
        )["machine_construction"]

        self.assertEqual(2, len(projection["machines"]))
        self.assertEqual(1, len(projection["construction_forest"]["roots"]))
        self.assertEqual(
            1,
            len(
                {
                    row["form_resolution"]["root_subproblem_id"]
                    for row in projection["machines"]
                }
            ),
        )
        self.assertEqual(2, len(projection["machine_dependency_edges"]))

    def test_machine_construction_discovers_execution_machine_closure(
        self,
    ) -> None:
        secondary_machine_id = (
            "rg:common_final_state_client:machine:test:bootstrap_machine"
        )
        primary_form_id = (
            "rg:common_final_state_client:item_variant:"
            "test:atlas_machine:bootstrap"
        )
        secondary_form_id = (
            "rg:common_final_state_client:item_variant:"
            "test:bootstrap_machine:stack"
        )
        first_map_id = (
            "rg:common_final_state_client:recipe_map:"
            "test_construct_primary"
        )
        second_map_id = (
            "rg:common_final_state_client:recipe_map:"
            "test_construct_secondary"
        )
        self.add_node(
            secondary_machine_id,
            "machine",
            "gt_meta_tile_entities",
            {"registry_key": "test:bootstrap_machine"},
        )
        for form_id in (primary_form_id, secondary_form_id):
            self.add_node(
                form_id,
                "item_variant",
                "gt_meta_tile_entities",
                {"registry_name": "gregtech:machine"},
            )
        self.add_edge(
            "has_form",
            self.machine_id,
            primary_form_id,
            "gt_meta_tile_entities",
            {"role": "registered_mte_stack_form"},
        )
        self.add_edge(
            "has_form",
            secondary_machine_id,
            secondary_form_id,
            "gt_meta_tile_entities",
            {"role": "registered_mte_stack_form"},
        )
        for map_id in (first_map_id, second_map_id):
            self.add_node(
                map_id,
                "recipe_map",
                "gt_recipe_maps",
                {"name": map_id.rsplit(":", 1)[-1]},
            )
        self.add_edge(
            "executes_recipe_map",
            secondary_machine_id,
            first_map_id,
            "gt_machine_recipe_maps",
            {"selection": "direct"},
        )
        self.add_edge(
            "executes_recipe_map",
            self.machine_id,
            second_map_id,
            "gt_machine_recipe_maps",
            {"selection": "direct"},
        )
        for index, (map_id, form_id) in enumerate(
            (
                (first_map_id, primary_form_id),
                (second_map_id, secondary_form_id),
            )
        ):
            recipe_id = (
                "rg:common_final_state_client:recipe:"
                f"test_bootstrap:{index}"
            )
            slot_id = (
                "rg:common_final_state_client:ingredient_slot:"
                f"test_bootstrap:{index}:output:0"
            )
            self.add_node(
                recipe_id,
                "recipe",
                "gt_recipes",
                {"native_recipe_map": map_id.rsplit(":", 1)[-1]},
            )
            self.add_node(
                slot_id,
                "ingredient_slot",
                "gt_recipes",
                {"amount": 1, "channel": "item_output", "ordinal": 0},
            )
            self.add_edge(
                "has_recipe",
                map_id,
                recipe_id,
                "gt_recipes",
                {"lookup_active": True, "category_present": True},
            )
            self.add_edge(
                "produces",
                recipe_id,
                slot_id,
                "gt_recipes",
                {},
            )
            self.add_edge(
                "accepts_alternative",
                slot_id,
                form_id,
                "gt_recipes",
                {"alternative_ordinal": 0},
            )
        self.connection.commit()

        projection = self.build(
            "PLAYER-MACHINE-CONSTRUCTION-001"
        )["machine_construction"]
        machines = {
            row["machine"]["id"]: row for row in projection["machines"]
        }

        self.assertEqual(
            {self.machine_id, secondary_machine_id},
            set(machines),
        )
        self.assertEqual(
            ["construction-route"],
            machines[secondary_machine_id]["origins"],
        )
        self.assertEqual(2, len(projection["construction_forest"]["roots"]))
        self.assertEqual(2, len(projection["machine_dependency_edges"]))
        self.assertEqual(
            {"resolved"},
            {
                row["status"]
                for row in projection["machine_dependency_edges"]
            },
        )
        self.assertEqual(1, len(projection["bootstrapping_cycles"]))
        cycle = projection["bootstrapping_cycles"][0]
        self.assertEqual(2, len(cycle["subproblem_ids"]))
        self.assertEqual(
            {"execution-machine"},
            {row["kind"] for row in cycle["dependencies"]},
        )

        limited = self.build(
            "PLAYER-MACHINE-CONSTRUCTION-001",
            max_construction_machine_roots=1,
        )["machine_construction"]
        self.assertEqual(1, len(limited["construction_forest"]["roots"]))
        self.assertEqual(
            ["max-machine-roots"],
            limited["truncation"]["reasons"],
        )
        self.assertEqual(
            [secondary_machine_id],
            [
                row["machine"]["id"]
                for row in limited["unresolved"]
                if row["reason"] == "max-machine-roots"
            ],
        )
        self.assertEqual(
            {"max-machine-roots"},
            {
                row["status"]
                for row in limited["machine_dependency_edges"]
            },
        )

    def test_machine_construction_semantic_drift_is_rejected(self) -> None:
        form_id = (
            "rg:common_final_state_client:item_variant:"
            "test:atlas_machine:drift"
        )
        self.add_node(
            form_id,
            "item_variant",
            "gt_meta_tile_entities",
            {"registry_name": "gregtech:machine"},
        )
        self.add_edge(
            "has_form",
            self.machine_id,
            form_id,
            "gt_meta_tile_entities",
            {"role": "registered_mte_stack_form"},
        )
        self.connection.commit()
        answer = copy.deepcopy(
            self.build("PLAYER-MACHINE-CONSTRUCTION-001")
        )
        answer["machine_construction"]["machines"][0][
            "form_resolution"
        ]["paths"][0]["admission"] = "unsupported-role"
        question = bridge._load_question(
            QUESTIONS_PATH,
            "PLAYER-MACHINE-CONSTRUCTION-001",
        )
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "admission differs from policy",
        ):
            bridge.validate_answer(answer, question)

    def test_prerequisite_projection_drift_is_rejected(self) -> None:
        answer = copy.deepcopy(
            self.build("PLAYER-BUILD-PREREQUISITES-001")
        )
        edge = answer["prerequisites"]["dependency_edges"][0]
        edge["from_subproblem_id"] = edge["to_subproblem_id"]
        question = bridge._load_question(
            QUESTIONS_PATH,
            "PLAYER-BUILD-PREREQUISITES-001",
        )
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "differs from its route DAG projection",
        ):
            bridge.validate_answer(answer, question)

    def test_player_build_prerequisites_fails_closed_without_route(
        self,
    ) -> None:
        self.connection.execute(
            "DELETE FROM edges WHERE predicate = 'executes_recipe_map'"
        )
        self.connection.commit()

        answer = self.build("PLAYER-BUILD-PREREQUISITES-001")
        projection = answer["prerequisites"]

        self.assertEqual("no-mechanical-route", answer["result_status"])
        self.assertEqual("no-mechanical-route", projection["status"])
        self.assertEqual([], projection["operations"])
        self.assertEqual(
            ["missing-execution-evidence"],
            [row["reason"] for row in projection["unresolved"]],
        )

    def test_player_build_prerequisites_preserves_route_cycles(
        self,
    ) -> None:
        cycle_recipe_id = (
            "rg:common_final_state_client:recipe:test_atlas:cycle_input_a"
        )
        cycle_output_slot_id = (
            "rg:common_final_state_client:ingredient_slot:"
            "test_atlas:cycle_input_a:output:0"
        )
        cycle_input_slot_id = (
            "rg:common_final_state_client:ingredient_slot:"
            "test_atlas:cycle_input_a:input:0"
        )
        self.add_node(
            cycle_recipe_id,
            "recipe",
            "gt_recipes",
            {"native_recipe_map": "test_atlas", "duration": 5, "eut": 1},
        )
        self.add_node(
            cycle_output_slot_id,
            "ingredient_slot",
            "gt_recipes",
            {"amount": 1, "channel": "item_output", "ordinal": 0},
        )
        self.add_node(
            cycle_input_slot_id,
            "ingredient_slot",
            "gt_recipes",
            {"amount": 1, "channel": "item_input", "ordinal": 0},
        )
        self.add_edge(
            "has_recipe",
            self.recipe_map_id,
            cycle_recipe_id,
            "gt_recipes",
            {"lookup_active": True, "category_present": True},
        )
        self.add_edge(
            "produces",
            cycle_recipe_id,
            cycle_output_slot_id,
            "gt_recipes",
            {},
        )
        self.add_edge(
            "accepts_alternative",
            cycle_output_slot_id,
            self.input_a_id,
            "gt_recipes",
            {"alternative_ordinal": 0},
        )
        self.add_edge(
            "consumes",
            cycle_recipe_id,
            cycle_input_slot_id,
            "gt_recipes",
            {},
        )
        self.add_edge(
            "accepts_alternative",
            cycle_input_slot_id,
            self.material_id,
            "gt_recipes",
            {"alternative_ordinal": 0},
        )
        self.connection.commit()

        answer = self.build("PLAYER-BUILD-PREREQUISITES-001")
        routes = answer["routes"]
        projection = answer["prerequisites"]

        self.assertGreater(len(routes["cycles"]), 0)
        self.assertEqual(routes["cycles"], projection["cycles"])
        self.assertEqual(
            {route["id"] for route in routes["items"]},
            {operation["route_id"] for operation in projection["operations"]},
        )
        root_ids = set(routes["roots"])
        self.assertTrue(
            any(
                edge["from_subproblem_id"] in root_ids
                for edge in projection["dependency_edges"]
            )
        )

    def test_player_build_prerequisites_preserves_route_truncation(
        self,
    ) -> None:
        alternate_recipe_id = (
            "rg:common_final_state_client:recipe:test_atlas:"
            "alternate_producer"
        )
        alternate_output_slot_id = (
            "rg:common_final_state_client:ingredient_slot:"
            "test_atlas:alternate_producer:output:0"
        )
        self.add_node(
            alternate_recipe_id,
            "recipe",
            "gt_recipes",
            {"native_recipe_map": "test_atlas", "duration": 10, "eut": 8},
        )
        self.add_node(
            alternate_output_slot_id,
            "ingredient_slot",
            "gt_recipes",
            {"amount": 1000, "channel": "fluid_output", "ordinal": 0},
        )
        self.add_edge(
            "has_recipe",
            self.recipe_map_id,
            alternate_recipe_id,
            "gt_recipes",
            {"lookup_active": True, "category_present": True},
        )
        self.add_edge(
            "produces",
            alternate_recipe_id,
            alternate_output_slot_id,
            "gt_recipes",
            {},
        )
        self.add_edge(
            "accepts_alternative",
            alternate_output_slot_id,
            self.variant_id,
            "gt_recipes",
            {"alternative_ordinal": 0},
        )
        self.connection.commit()

        answer = self.build(
            "PLAYER-BUILD-PREREQUISITES-001",
            chain_options=bridge.ChainOptions(max_routes=1),
        )
        routes = answer["routes"]
        projection = answer["prerequisites"]

        self.assertTrue(routes["truncation"]["truncated"])
        self.assertEqual(["max-routes"], routes["truncation"]["reasons"])
        self.assertEqual(routes["truncation"], projection["truncation"])
        self.assertEqual(
            routes["unresolved_leaves"],
            projection["unresolved"],
        )
        self.assertIn(
            "max-routes",
            {row["reason"] for row in projection["unresolved"]},
        )

    def test_runtime_projection_evidence_padding_is_rejected(self) -> None:
        answer = copy.deepcopy(self.build("DEVELOPER-PROCESS-USAGE-001"))
        answer["producers"]["evidence_ids"].append(self.consumer_id)
        question = bridge._load_question(
            QUESTIONS_PATH,
            "DEVELOPER-PROCESS-USAGE-001",
        )
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "runtime evidence closure differs",
        ):
            bridge.validate_answer(answer, question)

    def test_recipe_projection_evidence_omission_is_rejected(self) -> None:
        answer = copy.deepcopy(
            self.build("DEVELOPER-RECIPE-DETAILS-001")
        )
        answer["recipes"]["evidence_ids"].remove(self.input_a_id)
        question = bridge._load_question(
            QUESTIONS_PATH,
            "DEVELOPER-RECIPE-DETAILS-001",
        )
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "runtime evidence closure differs",
        ):
            bridge.validate_answer(answer, question)

    def test_route_detail_projection_drift_is_rejected(self) -> None:
        answer = copy.deepcopy(
            self.build("PLAYER-INGREDIENT-GROUPING-001")
        )
        answer["ingredient_slots"]["items"][0]["mode"] = "OR"
        question = bridge._load_question(
            QUESTIONS_PATH,
            "PLAYER-INGREDIENT-GROUPING-001",
        )
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "differs from its route projection",
        ):
            bridge.validate_answer(answer, question)

    def test_classification_count_drift_is_rejected(self) -> None:
        answer = copy.deepcopy(
            self.build("DEVELOPER-ROW-CLASSIFICATION-001")
        )
        answer["classification"]["counts"][0]["count"] += 1
        question = bridge._load_question(
            QUESTIONS_PATH,
            "DEVELOPER-ROW-CLASSIFICATION-001",
        )
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "counts differ",
        ):
            bridge.validate_answer(answer, question)

    def test_confidence_gap_drift_is_rejected(self) -> None:
        answer = copy.deepcopy(
            self.build("PLAYER-VERIFICATION-SCOPE-001")
        )
        answer["confidence"]["known_gap_codes"] = []
        question = bridge._load_question(
            QUESTIONS_PATH,
            "PLAYER-VERIFICATION-SCOPE-001",
        )
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "gap codes differ",
        ):
            bridge.validate_answer(answer, question)

    def test_confidence_check_drift_is_rejected(self) -> None:
        answer = copy.deepcopy(
            self.build("PLAYER-VERIFICATION-SCOPE-001")
        )
        profile_check = next(
            row
            for row in answer["confidence"]["checks"]
            if row["name"] == "profile-coverage"
        )
        profile_check["status"] = "partial"
        question = bridge._load_question(
            QUESTIONS_PATH,
            "PLAYER-VERIFICATION-SCOPE-001",
        )
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "confidence check differs",
        ):
            bridge.validate_answer(answer, question)

    def test_profile_comparison_pairwise_drift_is_rejected(self) -> None:
        self.add_dedicated_common_clone()
        answer = copy.deepcopy(
            self.build(
                "DEVELOPER-PROFILE-COMPARISON-001",
                scopes=[
                    "COMMON_FINAL_STATE:CLIENT",
                    "COMMON_FINAL_STATE:DEDICATED_SERVER",
                    "CLIENT_JEI_FINAL_STATE:CLIENT",
                    "OFFLINE_ARTIFACT_STATE:OFFLINE",
                ],
            )
        )
        answer["comparison"]["pairwise"][0]["mechanics"] = "different"
        question = bridge._load_question(
            QUESTIONS_PATH,
            "DEVELOPER-PROFILE-COMPARISON-001",
        )
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "pairwise deltas differ",
        ):
            bridge.validate_answer(answer, question)

    def test_unreconciled_cross_profile_projection_is_rejected(self) -> None:
        answer = copy.deepcopy(self.build("DEVELOPER-EXECUTION-001"))
        producer = next(
            row
            for row in answer["recipes"]["items"]
            if row["recipe"]["id"] == self.producer_id
        )
        presentation = producer["mechanics"]["presentation"][0]
        presentation["relationship"]["predicate"] = "consults_recipe_map"
        evidence = {
            row["id"]: row
            for row in answer["evidence"]
        }
        evidence[presentation["relationship"]["id"]]["record"][
            "predicate"
        ] = "consults_recipe_map"
        question = bridge._load_question(
            QUESTIONS_PATH,
            "DEVELOPER-EXECUTION-001",
        )
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "escaped requested scopes",
        ):
            bridge.validate_answer(answer, question)

    def test_exact_runtime_link_disambiguates_shared_catalog_alias(self) -> None:
        catalogs = copy.deepcopy(catalog.load_catalogs(CATALOG_ROOT))
        canonical = next(
            row
            for row in catalogs["entities.jsonl"]
            if row["id"] == CANONICAL_ID
        )
        candidate = copy.deepcopy(canonical)
        candidate["id"] = "MATERIAL-SUSY-DILUTED-OIL-LIGHT-ALIAS"
        candidate["name"] = "Diluted Oil Light alias candidate"
        catalogs["entities.jsonl"].append(candidate)
        catalogs["entities.jsonl"].sort(key=lambda row: row["id"])

        with patch.object(bridge, "_load_catalogs", return_value=catalogs):
            answer = self.build()
        self.assertEqual("answered", answer["result_status"])
        self.assertEqual(
            [CANONICAL_ID],
            answer["target"]["canonical_entity_ids"],
        )

    def test_missing_requested_profile_is_explicit_not_global(self) -> None:
        answer = self.build(
            scopes=[
                "COMMON_FINAL_STATE:CLIENT",
                "COMMON_FINAL_STATE:DEDICATED_SERVER",
            ]
        )
        self.assertEqual(
            "answered-with-profile-gap",
            answer["result_status"],
        )
        self.assertEqual(
            [
                {
                    "profile": "COMMON_FINAL_STATE",
                    "physical_side": "CLIENT",
                    "availability": "consulted",
                },
                {
                    "profile": "COMMON_FINAL_STATE",
                    "physical_side": "DEDICATED_SERVER",
                    "availability": "missing",
                },
            ],
            answer["profile_scope"],
        )
        self.assertEqual(
            ["ownership-unresolved", "profile-gap"],
            [gap["code"] for gap in answer["known_gaps"]],
        )

    def test_quest_link_requires_explicit_material_identity_edge(self) -> None:
        self.connection.execute(
            "DELETE FROM edges WHERE id = ?",
            (self.material_edge_id,),
        )
        self.connection.commit()
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "no explicit role-qualified has_material path",
        ):
            self.build()

    def test_quest_key_is_not_fuzzy_namespaced(self) -> None:
        self.connection.execute(
            "DELETE FROM node_keys WHERE key_kind = 'fluid-name'"
        )
        self.add_key(
            self.fluid_id,
            "fluid-name",
            "susy:diluted_oil_light",
        )
        self.connection.commit()
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "exact runtime key is missing",
        ):
            self.build()

    def test_duplicate_quest_record_id_is_rejected(self) -> None:
        rows = catalog.read_jsonl(self.quest_edges)
        duplicate = copy.deepcopy(rows[-1])
        duplicate["subject"] = "qg:quest:173"
        duplicate["object"] = "qg:quest:172"
        rows.append(duplicate)
        catalog.write_jsonl(self.quest_edges, rows)
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "quest record identifiers are duplicated",
        ):
            self.build()

    def test_duplicate_corpus_link_id_is_rejected(self) -> None:
        rows = catalog.read_jsonl(self.links)
        duplicate = copy.deepcopy(rows[0])
        duplicate["observed_id"] = (
            "rg:common_final_state_dedicated_server:material:"
            "susy:diluted_oil_light"
        )
        duplicate["profile_scope"] = [
            "COMMON_FINAL_STATE/DEDICATED_SERVER"
        ]
        rows.append(duplicate)
        catalog.write_jsonl(self.links, rows)
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "corpus link identifiers are duplicated",
        ):
            self.build()

    def test_missing_runtime_corpus_link_is_an_explicit_gap(self) -> None:
        rows = [
            row
            for row in catalog.read_jsonl(self.links)
            if row["observed_kind"] != "runtime-node"
        ]
        catalog.write_jsonl(self.links, rows)
        answer = self.build()
        self.assertEqual("unresolved-identity", answer["result_status"])
        self.assertEqual("unresolved", answer["target"]["resolution_status"])
        self.assertEqual(
            [
                "corpus-link-missing",
                "ownership-unresolved",
                "quest-link-missing",
                "required-evidence-missing",
            ],
            [gap["code"] for gap in answer["known_gaps"]],
        )

    def test_missing_quest_link_is_a_fail_closed_answer(self) -> None:
        rows = [
            row
            for row in catalog.read_jsonl(self.links)
            if row["observed_kind"] != "quest-task"
        ]
        catalog.write_jsonl(self.links, rows)
        answer = self.build()
        self.assertEqual("unresolved-evidence", answer["result_status"])
        self.assertEqual("resolved", answer["target"]["resolution_status"])
        self.assertEqual(
            [
                "ownership-unresolved",
                "quest-link-missing",
                "required-evidence-missing",
            ],
            [gap["code"] for gap in answer["known_gaps"]],
        )

    def test_ambiguous_runtime_candidate_cannot_escape_snapshot(self) -> None:
        identifier = (
            "rg:common_final_state_client:material:"
            "susy:diluted_oil_light:wrong-snapshot"
        )
        record = {
            "record_type": "node",
            "id": identifier,
            "kind": "material",
            "scope": {
                **self.scope("gt_materials"),
                "snapshot_id": "SNAPSHOT-WRONG",
            },
            "attributes": {
                "registry_name": "susy:diluted_oil_light",
                "id": 20000,
            },
        }
        self.connection.execute(
            "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?)",
            (
                identifier,
                "COMMON_FINAL_STATE",
                "CLIENT",
                "gt_materials",
                "material",
                graph.canonical_json_payload(record).decode("utf-8"),
            ),
        )
        self.add_key(
            identifier,
            "material-resource-location",
            "susy:diluted_oil_light",
        )
        self.connection.commit()
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "different snapshot",
        ):
            self.build()

    def test_schema_rejects_untyped_core_projections(self) -> None:
        question = bridge._load_question(
            QUESTIONS_PATH,
            "PLAYER-QUEST-CONTEXT-001",
        )
        answer = copy.deepcopy(self.build())
        answer["runtime"] = {}
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "required fields missing",
        ):
            bridge.validate_answer(answer, question)

        answer = copy.deepcopy(self.build())
        answer["quests"] = [{"nonsense": True}]
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "required fields missing|additional fields",
        ):
            bridge.validate_answer(answer, question)

    def test_validator_rejects_projected_record_evidence_drift(self) -> None:
        answer = json.loads(json.dumps(self.build()))
        relationship = answer["quest_prerequisites"][0]["relationship"]
        relationship["subject"] = "qg:quest:999"
        relationship["object"] = "qg:quest:998"
        question = bridge._load_question(
            QUESTIONS_PATH,
            "PLAYER-QUEST-CONTEXT-001",
        )
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "differs from cited evidence",
        ):
            bridge.validate_answer(answer, question)

    def test_validator_rejects_authority_collapse(self) -> None:
        answer = copy.deepcopy(self.build())
        for row in answer["evidence"]:
            if row["id"] == self.material_edge_id:
                row["authority"] = "curated-catalog"
                break
        question = bridge._load_question(
            QUESTIONS_PATH,
            "PLAYER-QUEST-CONTEXT-001",
        )
        with self.assertRaisesRegex(
            bridge.CorpusBridgeError,
            "oneOf mismatch|evidence basis collapses authority",
        ):
            bridge.validate_answer(answer, question)

    def test_parameterized_runtime_only_item_variant_answer(self) -> None:
        self.add_key(
            self.input_a_id,
            "item-variant-resource-location",
            "test:input_a",
        )
        self.connection.commit()
        instance = self.query_instance(
            "DEVELOPER-PROCESS-USAGE-001",
            kind="item-variant",
            key_kind="item-variant-resource-location",
            key="test:input_a",
        )
        result = bridge.build_query_answer(
            runtime_database=self.database,
            query_instance=instance,
            catalog_root=None,
            links_path=None,
            quest_nodes_path=None,
            quest_edges_path=None,
        )
        self.assertEqual("answered", result["result_status"])
        self.assertEqual(
            ["supported", "supported", "supported"],
            [row["status"] for row in result["capabilities"]],
        )
        self.assertEqual(
            {
                "kind": "item-variant",
                "key_kind": "item-variant-resource-location",
                "key": "test:input_a",
            },
            result["answer"]["target"]["selector"],
        )
        authority = {
            row["basis"]: row
            for row in result["authorities"]
        }
        self.assertEqual(
            "available",
            authority["runtime-mechanics"]["status"],
        )
        self.assertEqual(
            "unavailable",
            authority["pinned-source"]["status"],
        )
        self.assertEqual([], result["answer"]["target"]["canonical_entity_ids"])
        atlas_query.validate_query_result(result)

    def test_parameterized_query_composes_exact_optional_authorities(self) -> None:
        instance = self.query_instance("PLAYER-QUEST-CONTEXT-001")
        result = bridge.build_query_answer(
            runtime_database=self.database,
            query_instance=instance,
            catalog_root=CATALOG_ROOT,
            links_path=self.links,
            quest_nodes_path=self.quest_nodes,
            quest_edges_path=self.quest_edges,
        )
        self.assertEqual("answered", result["result_status"])
        self.assertEqual(
            ["supported", "supported", "supported"],
            [row["status"] for row in result["capabilities"]],
        )
        authority = {
            row["basis"]: row
            for row in result["authorities"]
        }
        self.assertEqual("available", authority["quest-data"]["status"])
        self.assertEqual("available", authority["pinned-source"]["status"])
        self.assertIsInstance(result["answer"], dict)
        self.assertEqual(self.build(), result["answer"])
        atlas_query.validate_query_result(result)

    def test_opt_in_route_and_recycling_continuation_api(self) -> None:
        byproducts = self.add_byproduct_routes()
        self.add_recycling_routes(byproducts)
        instance = self.query_instance("PLAYER-PRODUCE-MATERIAL-001")
        evidence: list[dict[str, object]] = []

        packet = bridge.start_query_continuation(
            runtime_database=self.database,
            query_instance=instance,
            evidence=evidence,
            kind="route",
            max_work_items=1,
        )
        route = packet["manifest"]
        self.assertEqual("route", route["kind"])
        self.assertEqual(
            instance["query_instance_id"],
            route["binding"]["query_instance_id"],
        )
        while route["status"] == "active":
            packet = bridge.resume_query_continuation(
                runtime_database=self.database,
                manifest=route,
                max_work_items=1,
            )
            self.assertEqual(
                packet["manifest"],
                bridge.atlas_continuation.compose_traversal_continuation(
                    route,
                    packet["delta"],
                ),
            )
            route = packet["manifest"]
        self.assertTrue(route["result"]["routes"])

        recycling_packet = bridge.start_query_continuation(
            runtime_database=self.database,
            query_instance=instance,
            evidence=evidence,
            kind="recycling",
            max_work_items=1,
            route_manifest=route,
        )
        recycling = recycling_packet["manifest"]
        self.assertEqual("recycling", recycling["kind"])
        while recycling["status"] == "active":
            recycling = bridge.resume_query_continuation(
                runtime_database=self.database,
                manifest=recycling,
                max_work_items=1,
            )["manifest"]
        self.assertTrue(recycling["result"]["roots"])
        bridge.validate_recycling_result(
            recycling["result"],
            recycling["result"]["roots"],
        )

    def test_default_v1_answer_is_unchanged_by_continuation_surface(self) -> None:
        before = self.build("PLAYER-PRODUCE-MATERIAL-001")
        instance = self.query_instance("PLAYER-PRODUCE-MATERIAL-001")
        bridge.start_query_continuation(
            runtime_database=self.database,
            query_instance=instance,
            evidence=[],
            kind="route",
            max_work_items=1,
        )
        after = self.build("PLAYER-PRODUCE-MATERIAL-001")
        self.assertEqual(
            graph.canonical_json_payload(before),
            graph.canonical_json_payload(after),
        )

    def test_parameterized_query_never_falls_back_to_embedded_selector(self) -> None:
        instance = self.query_instance(
            "PLAYER-PRODUCE-MATERIAL-001",
            key="susy:not_present",
        )
        result = bridge.build_query_answer(
            runtime_database=self.database,
            query_instance=instance,
            catalog_root=CATALOG_ROOT,
            links_path=self.links,
            quest_nodes_path=self.quest_nodes,
            quest_edges_path=self.quest_edges,
        )
        self.assertEqual("unresolved-identity", result["result_status"])
        self.assertIsNone(result["answer"])
        self.assertEqual(
            "susy:not_present",
            result["query_instance"]["selector"]["key"],
        )
        self.assertEqual([], result["resolution"]["targets"])

    def test_parameterized_non_applicable_kind_stops_before_bridge(self) -> None:
        self.add_key(
            self.producer_id,
            "recipe-native-identity",
            "test_atlas:producer",
        )
        self.connection.commit()
        instance = self.query_instance(
            "PLAYER-PRODUCE-MATERIAL-001",
            kind="recipe",
            key_kind="recipe-native-identity",
            key="test_atlas:producer",
        )
        result = bridge.build_query_answer(
            runtime_database=self.database,
            query_instance=instance,
            catalog_root=None,
            links_path=None,
            quest_nodes_path=None,
            quest_edges_path=None,
        )
        self.assertEqual("not-applicable", result["result_status"])
        self.assertEqual(
            ["supported", "not-applicable"],
            [row["status"] for row in result["capabilities"]],
        )
        self.assertIsNone(result["answer"])

    def test_parameterized_cli_emits_one_canonical_result(self) -> None:
        instance = self.query_instance("PLAYER-QUEST-CONTEXT-001")
        instance_path = self.root / "query-instance.json"
        instance_path.write_bytes(
            graph.canonical_json_payload(instance) + b"\n"
        )
        command = [
            sys.executable,
            str(TOOLS_ROOT / "corpus_bridge.py"),
            "query",
            "--runtime-database",
            str(self.database),
            "--query-instance",
            str(instance_path),
            "--links",
            str(self.links),
            "--quest-nodes",
            str(self.quest_nodes),
            "--quest-edges",
            str(self.quest_edges),
        ]
        first = subprocess.run(
            command,
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
        second = subprocess.run(
            command,
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
        self.assertEqual(b"", first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        result = json.loads(first.stdout)
        self.assertEqual("answered", result["result_status"])
        self.assertEqual(
            "susy-atlas-query-result-v1",
            result["format"],
        )
        self.assertEqual(graph.canonical_json(result), first.stdout)

    def test_continuation_cli_start_resume_and_compose_are_canonical(
        self,
    ) -> None:
        instance = self.query_instance("PLAYER-PRODUCE-MATERIAL-001")
        instance_path = self.root / "continuation-query.json"
        evidence_path = self.root / "continuation-evidence.json"
        instance_path.write_bytes(
            graph.canonical_json_payload(instance) + b"\n"
        )
        evidence_path.write_bytes(b"[]\n")
        start_command = [
            sys.executable,
            str(TOOLS_ROOT / "corpus_bridge.py"),
            "continuation-start",
            "--runtime-database",
            str(self.database),
            "--query-instance",
            str(instance_path),
            "--evidence",
            str(evidence_path),
            "--kind",
            "route",
            "--work-items",
            "1",
        ]
        first = subprocess.run(
            start_command,
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
        second = subprocess.run(
            start_command,
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
        self.assertEqual(first.stdout, second.stdout)
        packet = json.loads(first.stdout)
        parent = packet["manifest"]
        parent_path = self.root / "continuation-parent.json"
        parent_path.write_bytes(
            graph.canonical_json_payload(parent) + b"\n"
        )
        resumed = subprocess.run(
            [
                sys.executable,
                str(TOOLS_ROOT / "corpus_bridge.py"),
                "continuation-resume",
                "--runtime-database",
                str(self.database),
                "--manifest",
                str(parent_path),
                "--work-items",
                "1",
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
        child_packet = json.loads(resumed.stdout)
        delta_path = self.root / "continuation-delta.json"
        delta_path.write_bytes(
            graph.canonical_json_payload(child_packet["delta"]) + b"\n"
        )
        composed = subprocess.run(
            [
                sys.executable,
                str(TOOLS_ROOT / "corpus_bridge.py"),
                "continuation-compose",
                "--parent",
                str(parent_path),
                "--delta",
                str(delta_path),
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
        self.assertEqual(
            child_packet["manifest"],
            json.loads(composed.stdout),
        )
        self.assertEqual(
            graph.canonical_json(child_packet),
            resumed.stdout,
        )
        invalidated = subprocess.run(
            [
                sys.executable,
                str(TOOLS_ROOT / "corpus_bridge.py"),
                "continuation-invalidate",
                "--parent",
                str(parent_path),
                "--reason",
                "snapshot-changed",
                "--reason",
                "evidence-changed",
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
        invalidation_packet = json.loads(invalidated.stdout)
        self.assertEqual(
            "invalidated",
            invalidation_packet["manifest"]["status"],
        )
        self.assertEqual(
            ["evidence-changed", "snapshot-changed"],
            invalidation_packet["manifest"]["invalidation"]["reasons"],
        )

    def test_cli_emits_one_canonical_answer(self) -> None:
        command = [
            sys.executable,
            str(TOOLS_ROOT / "corpus_bridge.py"),
            "answer",
            "--runtime-database",
            str(self.database),
            "--question-id",
            "PLAYER-QUEST-CONTEXT-001",
            "--links",
            str(self.links),
            "--quest-nodes",
            str(self.quest_nodes),
            "--quest-edges",
            str(self.quest_edges),
            "--snapshot-id",
            SNAPSHOT_ID,
            "--scope",
            "COMMON_FINAL_STATE:CLIENT",
        ]
        first = subprocess.run(
            command,
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
        second = subprocess.run(
            command,
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
        self.assertEqual(b"", first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        answer = json.loads(first.stdout)
        self.assertEqual("answered", answer["result_status"])
        self.assertEqual(graph.canonical_json(answer), first.stdout)


if __name__ == "__main__":
    unittest.main()
