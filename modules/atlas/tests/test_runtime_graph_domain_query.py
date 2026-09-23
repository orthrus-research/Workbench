#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parent))

import workbench_atlas.runtime_graph as graph
import workbench_atlas.runtime_graph_domain_query as domain
import workbench_atlas.runtime_graph_query as query


COMMON_CLIENT = domain.ProfileScope("COMMON_FINAL_STATE", "CLIENT")
COMMON_SERVER = domain.ProfileScope("COMMON_FINAL_STATE", "DEDICATED_SERVER")
JEI_CLIENT = domain.ProfileScope("CLIENT_JEI_FINAL_STATE", "CLIENT")
OFFLINE_SERVER = domain.ProfileScope("OFFLINE_RESOURCE_STATE", "DEDICATED_SERVER")


class RuntimeGraphDomainQueryTests(unittest.TestCase):
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
            """
        )

    @staticmethod
    def _scope(scope: domain.ProfileScope, adapter: str) -> dict[str, str]:
        return {
            "snapshot_id": graph.SNAPSHOT_ID,
            "profile": scope.profile,
            "physical_side": scope.physical_side,
            "adapter": adapter,
        }

    def add_node(
        self,
        identifier: str,
        kind: str,
        *,
        scope: domain.ProfileScope = COMMON_CLIENT,
        adapter: str = "fixture",
        attributes: dict[str, object] | None = None,
        key: tuple[str, str] | None = None,
    ) -> str:
        row = {
            "record_type": "node",
            "id": identifier,
            "kind": kind,
            "scope": self._scope(scope, adapter),
            "attributes": {} if attributes is None else attributes,
        }
        self.connection.execute(
            "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?)",
            (
                identifier,
                scope.profile,
                scope.physical_side,
                adapter,
                kind,
                graph.canonical_json_payload(row).decode("utf-8"),
            ),
        )
        if key is not None:
            self.connection.execute(
                "INSERT INTO node_keys VALUES (?, ?, ?, ?, ?)",
                (
                    identifier,
                    key[0],
                    key[1],
                    scope.profile,
                    scope.physical_side,
                ),
            )
        return identifier

    def add_edge(
        self,
        predicate: str,
        subject: str,
        object_: str,
        *,
        scope: domain.ProfileScope = COMMON_CLIENT,
        adapter: str = "fixture",
        attributes: dict[str, object] | None = None,
    ) -> str:
        row: dict[str, object] = {
            "record_type": "edge",
            "predicate": predicate,
            "subject": subject,
            "object": object_,
            "scope": self._scope(scope, adapter),
            "attributes": {} if attributes is None else attributes,
        }
        row["id"] = graph.edge_id(row)
        self.connection.execute(
            "INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"],
                scope.profile,
                scope.physical_side,
                adapter,
                predicate,
                subject,
                object_,
                graph.canonical_json_payload(row).decode("utf-8"),
            ),
        )
        return str(row["id"])

    def add_recipe_occurrence(
        self,
        suffix: str,
        alternative: str,
        *,
        predicate: str,
        ordinal: int,
        lookup_active: bool,
        execute: bool,
        consult: bool = False,
        scope: domain.ProfileScope = COMMON_CLIENT,
        owner_attributes: dict[str, object] | None = None,
        slot_attributes: dict[str, object] | None = None,
        alternative_attributes: dict[str, object] | None = None,
    ) -> dict[str, str]:
        ids = {
            "recipe": f"{scope.profile}:{scope.physical_side}:recipe:{suffix}",
            "slot": f"{scope.profile}:{scope.physical_side}:slot:{suffix}",
            "map": f"{scope.profile}:{scope.physical_side}:map:{suffix}",
            "machine": f"{scope.profile}:{scope.physical_side}:machine:{suffix}",
            "consultant": f"{scope.profile}:{scope.physical_side}:rule:consult:{suffix}",
        }
        self.add_node(
            ids["recipe"],
            "recipe",
            scope=scope,
            attributes={"duration": 80, "eut": 32},
        )
        self.add_node(
            ids["slot"],
            "ingredient_slot",
            scope=scope,
            attributes=slot_attributes,
        )
        self.add_node(ids["map"], "recipe_map", scope=scope)
        self.add_node(ids["machine"], "machine", scope=scope)
        self.add_edge(
            predicate,
            ids["recipe"],
            ids["slot"],
            scope=scope,
            attributes={
                "ordinal": ordinal,
                **({} if owner_attributes is None else owner_attributes),
            },
        )
        self.add_edge(
            "accepts_alternative",
            ids["slot"],
            alternative,
            scope=scope,
            attributes={
                "alternative_ordinal": 0,
                "amount": 4,
                **(
                    {}
                    if alternative_attributes is None
                    else alternative_attributes
                ),
            },
        )
        self.add_edge(
            "has_recipe",
            ids["map"],
            ids["recipe"],
            scope=scope,
            attributes={
                "lookup_active": lookup_active,
                "category_present": True,
            },
        )
        if execute:
            self.add_edge(
                "executes_recipe_map",
                ids["machine"],
                ids["map"],
                scope=scope,
            )
        if consult:
            self.add_node(ids["consultant"], "process_rule", scope=scope)
            self.add_edge(
                "consults_recipe_map",
                ids["consultant"],
                ids["map"],
                scope=scope,
            )
        return ids

    def populate_material_producers(self) -> dict[str, str]:
        ids = {
            "material": "common:material:copper",
            "item": "common:item:copper_dust",
            "variant_a": "common:item_variant:copper_dust:a",
            "variant_b": "common:item_variant:copper_dust:b",
        }
        self.add_node(
            ids["material"],
            "material",
            key=("material-resource-location", "susy:copper"),
        )
        self.add_node(ids["item"], "item")
        self.add_node(
            ids["variant_a"],
            "item_variant",
            attributes={
                "metadata": 0,
                "nbt": {"grade": "pure"},
                "capability_payload": {"heat": 0},
            },
        )
        self.add_node(
            ids["variant_b"],
            "item_variant",
            attributes={"metadata": 1},
        )
        self.add_edge("has_form", ids["material"], ids["item"])
        self.add_edge("has_variant", ids["item"], ids["variant_a"])
        self.add_edge("has_variant", ids["item"], ids["variant_b"])

        active = self.add_recipe_occurrence(
            "active",
            ids["variant_a"],
            predicate="may_produce",
            ordinal=2,
            lookup_active=True,
            execute=True,
            consult=True,
            owner_attributes={"chance": 2500, "boost_per_tier": 500},
            slot_attributes={"ordinal": 2, "quantity": 3},
            alternative_attributes={
                "alternative_kind": "authoritative_match_domain",
                "match_domain": True,
            },
        )
        # A second matched alternative belongs to the same slot. The query must
        # keep one occurrence and preserve the explicit alternative order.
        self.add_edge(
            "accepts_alternative",
            active["slot"],
            ids["variant_b"],
            attributes={
                "alternative_ordinal": 1,
                "amount": 7,
                "alternative_kind": "public_representative",
                "representative_only": True,
            },
        )
        self.add_recipe_occurrence(
            "category-only",
            ids["variant_a"],
            predicate="produces",
            ordinal=0,
            lookup_active=False,
            execute=True,
        )

        process_rule = self.add_node(
            "common:process_rule:copper_precipitation",
            "process_rule",
        )
        process_slot = self.add_node(
            "common:slot:process:copper",
            "ingredient_slot",
            attributes={"ordinal": 1, "quantity": 1},
        )
        self.add_edge(
            "produces",
            process_rule,
            process_slot,
            attributes={"ordinal": 1},
        )
        self.add_edge(
            "accepts_alternative",
            process_slot,
            ids["variant_a"],
        )

        wrapper = self.add_node(
            "jei:wrapper:copper",
            "recipe_wrapper",
            scope=JEI_CLIENT,
        )
        wrapper_slot = self.add_node(
            "jei:slot:copper",
            "ingredient_slot",
            scope=JEI_CLIENT,
        )
        wrapper_variant = self.add_node(
            "jei:item_variant:copper",
            "item_variant",
            scope=JEI_CLIENT,
        )
        self.add_edge(
            "produces",
            wrapper,
            wrapper_slot,
            scope=JEI_CLIENT,
        )
        self.add_edge(
            "accepts_alternative",
            wrapper_slot,
            wrapper_variant,
            scope=JEI_CLIENT,
        )
        self.add_edge(
            "reconciles_to",
            wrapper,
            active["recipe"],
            scope=JEI_CLIENT,
        )

        self.connection.commit()
        return {**ids, **{f"active_{key}": value for key, value in active.items()}}

    def test_producers_preserve_occurrences_semantics_and_authority(self) -> None:
        ids = self.populate_material_producers()

        with query.RuntimeGraphReader(self.database) as reader:
            result = domain.find_producers(
                reader,
                query.NodeSelector.by_key(
                    "material-resource-location",
                    "susy:copper",
                    kind="material",
                ),
            )

        self.assertEqual(2, result.page.total)
        by_owner = {item["owner"]["id"]: item for item in result.page.items}
        active = by_owner[ids["active_recipe"]]
        self.assertEqual("may_produce", active["owner_relationship"]["predicate"])
        self.assertEqual(2500, active["owner_relationship"]["attributes"]["chance"])
        self.assertEqual(3, active["slot"]["attributes"]["quantity"])
        self.assertEqual(
            [ids["variant_a"], ids["variant_b"]],
            [row["node"]["id"] for row in active["alternatives"]],
        )
        self.assertEqual(
            [ids["variant_a"], ids["variant_b"]],
            [row["node"]["id"] for row in active["matched_alternatives"]],
        )
        self.assertEqual(
            [
                "authoritative-match-domain",
                "non-exhaustive-representative",
            ],
            [
                row["alternative_semantics"]
                for row in active["alternatives"]
            ],
        )
        self.assertEqual(
            {"grade": "pure"},
            active["matched_alternatives"][0]["node"]["attributes"]["nbt"],
        )
        self.assertTrue(active["semantics"]["conditional"])
        self.assertFalse(active["semantics"]["guaranteed"])
        self.assertTrue(active["mechanics"]["eligible"])
        self.assertEqual("runtime-executable", active["mechanics"]["classification"])
        self.assertEqual(
            [ids["active_machine"]],
            [row["machine"]["id"] for row in active["mechanics"]["execution"]],
        )
        self.assertEqual(
            [ids["active_consultant"]],
            [row["actor"]["id"] for row in active["mechanics"]["consultation"]],
        )
        self.assertEqual(
            ["jei:wrapper:copper"],
            [row["source"]["id"] for row in active["mechanics"]["presentation"]],
        )
        self.assertTrue(
            {
                ids["material"],
                ids["active_recipe"],
                ids["active_slot"],
                ids["variant_a"],
            }.issubset(set(active["evidence_ids"]))
        )

        # Category provenance plus an executes edge is not executable recipe
        # membership and is absent from the mechanical producer result.
        self.assertNotIn(
            "COMMON_FINAL_STATE:CLIENT:recipe:category-only",
            by_owner,
        )
        self.assertIn(
            "common:process_rule:copper_precipitation",
            by_owner,
        )
        self.assertNotIn("jei:wrapper:copper", by_owner)
        # Public projection uses JSON arrays even though the reusable Python
        # API uses immutable tuples internally.
        graph.canonical_json_payload(result.to_dict())
        self.assertIsInstance(
            result.to_dict()["page"]["items"][0]["alternatives"],
            list,
        )

    def test_owner_mechanics_exposes_the_same_exact_classification(self) -> None:
        ids = self.populate_material_producers()

        with query.RuntimeGraphReader(self.database) as reader:
            owner = reader.resolve_one(
                query.NodeSelector.by_id(ids["active_recipe"]),
            )
            mechanics = domain.RuntimeGraphDomainQuery(
                reader
            ).owner_mechanics(owner, COMMON_CLIENT)

        self.assertEqual("runtime-executable", mechanics["classification"])
        self.assertTrue(mechanics["eligible"])
        self.assertEqual(
            [ids["active_machine"]],
            [row["machine"]["id"] for row in mechanics["execution"]],
        )
        self.assertEqual(
            ["jei:wrapper:copper"],
            [row["source"]["id"] for row in mechanics["presentation"]],
        )

    def test_machine_form_paths_preserve_every_exact_same_scope_path(
        self,
    ) -> None:
        machine_id = self.add_node(
            "common:machine:atlas",
            "machine",
        )
        admitted_form = self.add_node(
            "common:item_variant:atlas",
            "item_variant",
        )
        other_form = self.add_node(
            "common:item:atlas",
            "item",
        )
        admitted_edge = self.add_edge(
            "has_form",
            machine_id,
            admitted_form,
            attributes={"role": "registered_mte_stack_form"},
        )
        other_edge = self.add_edge(
            "has_form",
            machine_id,
            other_form,
            attributes={"role": "display_form"},
        )
        self.connection.commit()

        with query.RuntimeGraphReader(self.database) as reader:
            machine = reader.resolve_one(
                query.NodeSelector.by_id(machine_id),
            )
            paths = domain.RuntimeGraphDomainQuery(
                reader
            ).machine_form_paths(
                domain.ScopedTarget(COMMON_CLIENT, machine)
            )

        self.assertEqual(
            {admitted_edge, other_edge},
            {row["relationship"]["id"] for row in paths},
        )
        self.assertEqual(
            {admitted_form, other_form},
            {row["form"]["id"] for row in paths},
        )
        self.assertEqual(
            {machine_id},
            {row["machine"]["id"] for row in paths},
        )

    def test_condition_candidates_preserve_direct_and_grouped_paths(
        self,
    ) -> None:
        owner_id = self.add_node(
            "common:recipe:condition-owner",
            "recipe",
            attributes={"eut": 120, "duration": 40},
        )
        constraint_id = self.add_node(
            "common:constraint:cleanroom",
            "constraint",
            attributes={"constraint_kind": "cleanroom_recipe_envelope"},
        )
        energy_id = self.add_node(
            "common:energy:eu",
            "energy_carrier",
            attributes={"carrier": "gregtech:eu"},
        )
        config_id = self.add_node(
            "common:config:machines",
            "config",
            attributes={"path": "config/gregtech.cfg"},
        )
        reusable_slot_id = self.add_node(
            "common:slot:reusable",
            "ingredient_slot",
            attributes={"ordinal": 0},
        )
        effect_slot_id = self.add_node(
            "common:slot:effect",
            "ingredient_slot",
            attributes={"ordinal": 1},
        )
        reusable_id = self.add_node(
            "common:item_variant:catalyst",
            "item_variant",
        )
        environment_id = self.add_node(
            "common:ingredient:environment",
            "ingredient",
            attributes={"domain": "cleanroom"},
        )
        direct_edges = {
            self.add_edge("has_constraint", owner_id, constraint_id),
            self.add_edge("uses_energy", owner_id, energy_id),
            self.add_edge("configured_by", owner_id, config_id),
        }
        requires_edge = self.add_edge(
            "requires",
            owner_id,
            reusable_slot_id,
            attributes={"ordinal": 0},
        )
        reusable_edge = self.add_edge(
            "accepts_alternative",
            reusable_slot_id,
            reusable_id,
            attributes={"alternative_ordinal": 0},
        )
        affects_edge = self.add_edge(
            "affects",
            owner_id,
            effect_slot_id,
            attributes={"ordinal": 1},
        )
        environment_edge = self.add_edge(
            "accepts_alternative",
            effect_slot_id,
            environment_id,
            attributes={"alternative_ordinal": 0},
        )
        self.connection.commit()

        with query.RuntimeGraphReader(self.database) as reader:
            owner = reader.resolve_one(query.NodeSelector.by_id(owner_id))
            paths = domain.RuntimeGraphDomainQuery(
                reader
            ).condition_candidate_paths(
                domain.ScopedTarget(COMMON_CLIENT, owner)
            )

        direct = [row for row in paths if row["path_kind"] == "direct"]
        slots = [row for row in paths if row["path_kind"] == "slot"]
        self.assertEqual(
            direct_edges,
            {row["relationship"]["id"] for row in direct},
        )
        self.assertEqual(
            {constraint_id, energy_id, config_id},
            {row["condition"]["id"] for row in direct},
        )
        self.assertEqual(
            {requires_edge, affects_edge},
            {row["relationship"]["id"] for row in slots},
        )
        self.assertEqual(
            {reusable_edge, environment_edge},
            {
                alternative["relationship"]["id"]
                for row in slots
                for alternative in row["alternatives"]
            },
        )
        self.assertEqual(
            {owner_id},
            {row["subject"]["id"] for row in paths},
        )
        self.assertTrue(
            all(
                "admission" not in row and "requirement_type" not in row
                for row in paths
            )
        )

    def test_consumer_closure_keeps_ore_membership_and_requires_reusable(self) -> None:
        variant = self.add_node(
            "common:item_variant:copper",
            "item_variant",
            attributes={"nbt": {"purity": 100}, "capabilities": {"heat": 20}},
            key=("item-variant-identity", "fixture-copper"),
        )
        ore_key = self.add_node(
            "common:ore_key:ingotCopper",
            "ore_dictionary_key",
        )
        self.add_edge("member_of_ore_dictionary", variant, ore_key)
        consumes = self.add_recipe_occurrence(
            "consume-ore",
            ore_key,
            predicate="consumes",
            ordinal=1,
            lookup_active=True,
            execute=True,
        )
        requires = self.add_recipe_occurrence(
            "requires-tool",
            variant,
            predicate="requires",
            ordinal=0,
            lookup_active=True,
            execute=True,
            owner_attributes={"amount": 1},
        )
        self.connection.commit()

        with query.RuntimeGraphReader(self.database) as reader:
            result = domain.find_consumers(
                reader,
                query.NodeSelector.by_key(
                    "item-variant-identity",
                    "fixture-copper",
                    kind="item_variant",
                ),
            )

        self.assertEqual(2, result.page.total)
        by_owner = {item["owner"]["id"]: item for item in result.page.items}
        ore_consumer = by_owner[consumes["recipe"]]
        self.assertEqual(
            "ore-dictionary-membership",
            ore_consumer["matched_alternatives"][0]["match_role"],
        )
        self.assertEqual(
            ["member_of_ore_dictionary"],
            [
                edge["predicate"]
                for edge in ore_consumer["matched_alternatives"][0]["match_path"]
            ],
        )
        reusable = by_owner[requires["recipe"]]
        self.assertEqual("requires", reusable["owner_relationship"]["predicate"])
        self.assertTrue(reusable["semantics"]["reusable"])
        self.assertFalse(reusable["semantics"]["consumed"])
        self.assertFalse(reusable["semantics"]["conditional"])
        self.assertEqual(
            {"purity": 100},
            reusable["matched_alternatives"][0]["node"]["attributes"]["nbt"],
        )

    def test_consumer_predicate_subsets_have_independent_stable_pages(self) -> None:
        variant = self.add_node(
            "common:item_variant:filtered-consumer",
            "item_variant",
            key=("item-variant-identity", "filtered-consumer"),
        )
        expected_by_predicate: dict[str, list[str]] = {
            "consumes": [],
            "may_consume": [],
            "requires": [],
        }
        for suffix, predicate, ordinal in (
            ("alpha-consume", "consumes", 4),
            ("bravo-require", "requires", 0),
            ("charlie-may-consume", "may_consume", 3),
            ("delta-consume", "consumes", 2),
            ("echo-require", "requires", 1),
            ("foxtrot-may-consume", "may_consume", 5),
        ):
            occurrence = self.add_recipe_occurrence(
                suffix,
                variant,
                predicate=predicate,
                ordinal=ordinal,
                lookup_active=True,
                execute=True,
            )
            expected_by_predicate[predicate].append(occurrence["recipe"])
        self.connection.commit()

        selector = query.NodeSelector.by_key(
            "item-variant-identity",
            "filtered-consumer",
            kind="item_variant",
        )
        with query.RuntimeGraphReader(self.database) as reader:
            api = domain.RuntimeGraphDomainQuery(reader)
            default = api.consumers(
                selector,
                page=query.PageRequest(limit=20),
            )
            explicit_all = api.consumers(
                selector,
                page=query.PageRequest(limit=20),
                predicates=("requires", "may_consume", "consumes"),
            )
            consuming_first = api.consumers(
                selector,
                page=query.PageRequest(limit=2),
                predicates=("may_consume", "consumes"),
            )
            consuming_second = api.consumers(
                selector,
                page=query.PageRequest(limit=2, offset=2),
                predicates=("consumes", "may_consume"),
            )
            reusable = domain.find_consumers(
                reader,
                selector,
                page=query.PageRequest(limit=20),
                predicates=("requires",),
            )
            target = api.resolve_targets(selector).targets[0]
            reusable_occurrences = api.consumer_occurrences(
                target,
                predicates=("requires",),
            )
            reader.connection.execute("PRAGMA reverse_unordered_selects = ON")
            repeated_consuming = api.consumers(
                selector,
                page=query.PageRequest(limit=20),
                predicates=("consumes", "may_consume"),
            )

        self.assertEqual(
            graph.canonical_json_payload(default.to_dict()),
            graph.canonical_json_payload(explicit_all.to_dict()),
        )
        self.assertEqual(6, default.page.total)
        self.assertEqual(4, consuming_first.page.total)
        self.assertEqual(2, consuming_first.page.returned)
        self.assertTrue(consuming_first.page.truncated)
        self.assertEqual(4, consuming_second.page.total)
        self.assertEqual(2, consuming_second.page.returned)
        self.assertFalse(consuming_second.page.truncated)
        consuming_items = (
            consuming_first.page.items + consuming_second.page.items
        )
        self.assertEqual(
            sorted(
                expected_by_predicate["consumes"]
                + expected_by_predicate["may_consume"]
            ),
            [row["owner"]["id"] for row in consuming_items],
        )
        self.assertEqual(
            {"consumes", "may_consume"},
            {
                row["owner_relationship"]["predicate"]
                for row in consuming_items
            },
        )
        self.assertEqual(2, reusable.page.total)
        self.assertEqual(
            sorted(expected_by_predicate["requires"]),
            [row["owner"]["id"] for row in reusable.page.items],
        )
        self.assertEqual(
            [row["owner"]["id"] for row in reusable.page.items],
            [row["owner"]["id"] for row in reusable_occurrences],
        )
        self.assertEqual(
            [
                row["owner_relationship"]["id"]
                for row in consuming_items
            ],
            [
                row["owner_relationship"]["id"]
                for row in repeated_consuming.page.items
            ],
        )
        self.assertEqual(
            {
                row["owner_relationship"]["id"]
                for row in default.page.items
            },
            {
                row["owner_relationship"]["id"]
                for row in consuming_items + reusable.page.items
            },
        )

    def test_consumer_predicate_filter_rejects_invalid_sets(self) -> None:
        variant = self.add_node(
            "common:item_variant:invalid-filter",
            "item_variant",
            key=("item-variant-identity", "invalid-filter"),
        )
        self.connection.commit()
        selector = query.NodeSelector.by_key(
            "item-variant-identity",
            "invalid-filter",
        )

        with query.RuntimeGraphReader(self.database) as reader:
            api = domain.RuntimeGraphDomainQuery(reader)
            for predicates, expected in (
                ((), "at least one"),
                ("consumes", "iterable of predicate names"),
                (("consumes", "consumes"), "duplicate"),
                (("produces",), "unsupported predicates: produces"),
                ((1,), "non-empty strings"),
            ):
                with self.subTest(predicates=predicates):
                    with self.assertRaisesRegex(
                        query.RuntimeGraphQueryError,
                        expected,
                    ):
                        api.consumers(
                            selector,
                            predicates=predicates,  # type: ignore[arg-type]
                        )

    def test_resolution_accepts_cross_scope_identity_and_reports_explicit_gap(
        self,
    ) -> None:
        for scope, suffix in (
            (COMMON_CLIENT, "client"),
            (COMMON_SERVER, "server"),
        ):
            self.add_node(
                f"{suffix}:material:copper",
                "material",
                scope=scope,
                key=("material-resource-location", "susy:copper"),
            )
        self.connection.commit()
        requested = domain.GraphScope.of(
            COMMON_CLIENT,
            COMMON_SERVER,
            OFFLINE_SERVER,
        )

        with query.RuntimeGraphReader(self.database) as reader:
            result = domain.RuntimeGraphDomainQuery(reader).producers(
                query.NodeSelector.by_key(
                    "material-resource-location",
                    "susy:copper",
                    kind="material",
                ),
                graph_scope=requested,
            )

        self.assertEqual(
            (COMMON_CLIENT, COMMON_SERVER),
            tuple(target.scope for target in result.resolution.targets),
        )
        self.assertEqual((OFFLINE_SERVER,), result.resolution.gaps)
        payload = result.to_dict()
        self.assertEqual(
            [{"profile": OFFLINE_SERVER.profile, "physical_side": OFFLINE_SERVER.physical_side}],
            payload["target"]["profile_gaps"],
        )

    def test_resolution_fails_on_ambiguity_within_one_scope(self) -> None:
        for suffix in ("a", "b"):
            self.add_node(
                f"common:material:copper:{suffix}",
                "material",
                key=("material-resource-location", "susy:copper"),
            )
        self.connection.commit()

        with query.RuntimeGraphReader(self.database) as reader:
            with self.assertRaisesRegex(
                query.RuntimeGraphQueryError,
                "ambiguous within profile scope COMMON_FINAL_STATE/CLIENT",
            ):
                domain.RuntimeGraphDomainQuery(reader).producers(
                    query.NodeSelector.by_key(
                        "material-resource-location",
                        "susy:copper",
                        kind="material",
                    )
                )

    def test_pagination_follows_stable_owner_and_realistic_slot_order(self) -> None:
        variant = self.add_node(
            "common:item_variant:ordered",
            "item_variant",
            key=("item-variant-identity", "ordered"),
        )
        # Insert deliberately opposite to semantic owner/slot order.
        for suffix, ordinal in (("zeta", 2), ("alpha", 9), ("alpha", 1)):
            self.add_recipe_occurrence(
                f"{suffix}-{ordinal}",
                variant,
                predicate="produces",
                ordinal=ordinal,
                lookup_active=True,
                execute=True,
            )
        self.connection.commit()

        selector = query.NodeSelector.by_key(
            "item-variant-identity",
            "ordered",
        )
        with query.RuntimeGraphReader(self.database) as reader:
            api = domain.RuntimeGraphDomainQuery(reader)
            whole = api.producers(selector, page=query.PageRequest(limit=10))
            mechanics_calls: list[str] = []
            original_mechanics = api._mechanics

            def counted_mechanics(
                owner: dict[str, object],
                scope: domain.ProfileScope,
            ) -> dict[str, object]:
                mechanics_calls.append(str(owner["id"]))
                return original_mechanics(owner, scope)

            api._mechanics = counted_mechanics  # type: ignore[method-assign]
            first = api.producers(selector, page=query.PageRequest(limit=2))
            api._mechanics = original_mechanics  # type: ignore[method-assign]
            second = api.producers(
                selector,
                page=query.PageRequest(limit=2, offset=2),
            )
            repeated = api.producers(selector, page=query.PageRequest(limit=10))
            reader.connection.execute("PRAGMA reverse_unordered_selects = ON")
            reversed_selection = api.producers(
                selector,
                page=query.PageRequest(limit=10),
            )

        whole_ids = [item["owner"]["id"] for item in whole.page.items]
        self.assertEqual(
            [
                "COMMON_FINAL_STATE:CLIENT:recipe:alpha-1",
                "COMMON_FINAL_STATE:CLIENT:recipe:alpha-9",
                "COMMON_FINAL_STATE:CLIENT:recipe:zeta-2",
            ],
            whole_ids,
        )
        self.assertEqual(3, first.page.total)
        self.assertEqual(2, len(mechanics_calls))
        self.assertTrue(first.page.truncated)
        self.assertEqual(2, first.page.returned)
        self.assertEqual(1, second.page.returned)
        self.assertFalse(second.page.truncated)
        self.assertEqual(
            whole_ids,
            [item["owner"]["id"] for item in first.page.items + second.page.items],
        )
        self.assertEqual(
            graph.canonical_json_payload(
                whole.to_dict()["page"]["items"][:2]
            ),
            graph.canonical_json_payload(first.to_dict()["page"]["items"]),
        )
        self.assertEqual(
            json.dumps(whole.to_dict(), sort_keys=True),
            json.dumps(repeated.to_dict(), sort_keys=True),
        )
        self.assertEqual(
            graph.canonical_json_payload(whole.to_dict()),
            graph.canonical_json_payload(reversed_selection.to_dict()),
        )

    def test_slot_ordinal_precedes_owner_edge_ordinal_with_fallback(self) -> None:
        variant = self.add_node(
            "common:item_variant:slot-order",
            "item_variant",
            key=("item-variant-identity", "slot-order"),
        )
        recipe = self.add_node("common:recipe:slot-order", "recipe")
        recipe_map = self.add_node("common:map:slot-order", "recipe_map")
        self.add_edge(
            "has_recipe",
            recipe_map,
            recipe,
            attributes={"lookup_active": True, "category_present": True},
        )
        slots = (
            self.add_node(
                "common:slot:declared-nine",
                "ingredient_slot",
                attributes={"ordinal": 9},
            ),
            self.add_node(
                "common:slot:fallback-one",
                "ingredient_slot",
            ),
        )
        self.add_edge(
            "produces",
            recipe,
            slots[0],
            attributes={"ordinal": 0},
        )
        self.add_edge(
            "produces",
            recipe,
            slots[1],
            attributes={"ordinal": 1},
        )
        for slot in slots:
            self.add_edge("accepts_alternative", slot, variant)
        self.connection.commit()

        with query.RuntimeGraphReader(self.database) as reader:
            result = domain.find_producers(
                reader,
                query.NodeSelector.by_key(
                    "item-variant-identity",
                    "slot-order",
                ),
            )

        self.assertEqual(
            ["common:slot:fallback-one", "common:slot:declared-nine"],
            [item["slot"]["id"] for item in result.page.items],
        )

    def test_material_has_material_closure_accepts_only_identity_roles(self) -> None:
        material = self.add_node(
            "common:material:tin",
            "material",
            key=("material-resource-location", "susy:tin"),
        )
        identity_variant = self.add_node(
            "common:item_variant:tin",
            "item_variant",
        )
        component_variant = self.add_node(
            "common:item_variant:bronze",
            "item_variant",
        )
        self.add_edge(
            "has_material",
            identity_variant,
            material,
            attributes={"role": "effective_material_lookup"},
        )
        self.add_edge(
            "has_material",
            component_variant,
            material,
            attributes={"role": "item_material_info_component"},
        )
        identity_recipe = self.add_recipe_occurrence(
            "tin-identity",
            identity_variant,
            predicate="produces",
            ordinal=0,
            lookup_active=True,
            execute=True,
        )
        self.add_recipe_occurrence(
            "bronze-component",
            component_variant,
            predicate="produces",
            ordinal=0,
            lookup_active=True,
            execute=True,
        )
        self.connection.commit()

        with query.RuntimeGraphReader(self.database) as reader:
            result = domain.find_producers(
                reader,
                query.NodeSelector.by_key(
                    "material-resource-location",
                    "susy:tin",
                ),
            )

        self.assertEqual(1, result.page.total)
        self.assertEqual(identity_recipe["recipe"], result.page.items[0]["owner"]["id"])

    def test_alternative_semantics_do_not_promote_representatives(self) -> None:
        def relationship(**attributes: object) -> dict[str, object]:
            return {"attributes": attributes}

        self.assertEqual(
            "authoritative-match-domain",
            domain.alternative_semantics(
                relationship(match_domain=True)
            ),
        )
        self.assertEqual(
            "non-exhaustive-representative",
            domain.alternative_semantics(
                relationship(
                    alternative_kind="public_representative",
                    public_representatives_exhaustive=False,
                )
            ),
        )
        self.assertEqual(
            "symbolic",
            domain.alternative_semantics(relationship(symbolic=True)),
        )
        self.assertEqual(
            "exact",
            domain.alternative_semantics(relationship(amount=1)),
        )


if __name__ == "__main__":
    unittest.main()
