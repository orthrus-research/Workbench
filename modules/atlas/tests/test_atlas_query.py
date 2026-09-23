#!/usr/bin/env python3

from __future__ import annotations

import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


TOOLS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_ROOT.parents[1]
SNAPSHOT_ID = "SNAPSHOT-SUSY-0-1-16-11-9D3AA7AE0"

if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import workbench_atlas.atlas_query as atlas
import workbench_atlas.corpus_bridge as bridge
from workbench_atlas.runtime_graph_query import (  # noqa: E402
    QUERY_DATABASE_APPLICATION_ID,
    QUERY_DATABASE_USER_VERSION,
    RuntimeGraphReader,
)


def evidence_records(
    basis: str,
    identifiers: tuple[str, ...],
) -> tuple[dict[str, object], ...]:
    authority = {
        "pinned-source": "curated-catalog",
        "curated-interpretation": "curated-catalog",
        "quest-data": "quest-graph",
        "placed-world-observation": "placed-world-observation",
    }[basis]
    record_kind = {
        "pinned-source": "curated-entity",
        "curated-interpretation": "curated-claim",
        "quest-data": "quest-node",
        "placed-world-observation": "formed-world-observation",
    }[basis]
    return tuple(
        {
            "id": identifier,
            "basis": basis,
            "authority": authority,
            "record_kind": record_kind,
            "record": {
                "id": identifier,
                "record_type": record_kind,
            },
        }
        for identifier in identifiers
    )


class AtlasQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / "runtime.sqlite"
        self.connection = sqlite3.connect(self.database)
        self.addCleanup(self.connection.close)
        self.connection.executescript(
            f"""
            PRAGMA application_id = {QUERY_DATABASE_APPLICATION_ID};
            PRAGMA user_version = {QUERY_DATABASE_USER_VERSION};
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
            CREATE INDEX node_keys_lookup
                ON node_keys(key_kind, key_value);
            """
        )
        self.material_client = self.add_node(
            "rg:common_final_state_client:material:susy:test_material",
            "material",
        )
        self.add_key(
            self.material_client,
            "material-resource-location",
            "susy:test_material",
        )
        self.material_server = self.add_node(
            (
                "rg:common_final_state_dedicated_server:"
                "material:susy:test_material"
            ),
            "material",
            physical_side="DEDICATED_SERVER",
        )
        self.add_key(
            self.material_server,
            "material-resource-location",
            "susy:test_material",
            physical_side="DEDICATED_SERVER",
        )
        self.material_presentation = self.add_node(
            (
                "rg:client_jei_final_state_client:"
                "material:susy:test_material"
            ),
            "material",
            profile="CLIENT_JEI_FINAL_STATE",
        )
        self.add_key(
            self.material_presentation,
            "material-resource-location",
            "susy:test_material",
            profile="CLIENT_JEI_FINAL_STATE",
        )
        self.material_offline = self.add_node(
            (
                "rg:offline_artifact_state_offline:"
                "material:susy:test_material"
            ),
            "material",
            profile="OFFLINE_ARTIFACT_STATE",
            physical_side="OFFLINE",
        )
        self.add_key(
            self.material_offline,
            "material-resource-location",
            "susy:test_material",
            profile="OFFLINE_ARTIFACT_STATE",
            physical_side="OFFLINE",
        )
        self.client_only_material = self.add_node(
            "rg:common_final_state_client:material:susy:client_only",
            "material",
        )
        self.add_key(
            self.client_only_material,
            "material-resource-location",
            "susy:client_only",
        )
        self.mixed_ambiguity_common = self.add_node(
            "rg:common_final_state_client:material:susy:mixed_ambiguity",
            "material",
        )
        self.add_key(
            self.mixed_ambiguity_common,
            "material-resource-location",
            "susy:mixed_ambiguity",
        )
        self.mixed_ambiguity_presentation = [
            self.add_node(
                (
                    "rg:client_jei_final_state_client:"
                    f"material:susy:mixed_ambiguity:{suffix}"
                ),
                "material",
                profile="CLIENT_JEI_FINAL_STATE",
            )
            for suffix in ("a", "b")
        ]
        for node_id in self.mixed_ambiguity_presentation:
            self.add_key(
                node_id,
                "material-resource-location",
                "susy:mixed_ambiguity",
                profile="CLIENT_JEI_FINAL_STATE",
            )

        self.kind_fixtures = [
            (
                "item-variant",
                "item_variant",
                "item-variant-resource-location",
                "test:item_variant",
            ),
            (
                "fluid-variant",
                "fluid_variant",
                "fluid-variant-name",
                "test_fluid_variant",
            ),
            (
                "recipe",
                "recipe",
                "recipe-native-identity",
                "test_map:recipe:0",
            ),
            (
                "recipe-map",
                "recipe_map",
                "recipe-map-name",
                "test_map",
            ),
            (
                "recipe-rule",
                "recipe_rule",
                "dynamic-rule-identity",
                "test:recipe_rule",
            ),
            (
                "process-rule",
                "process_rule",
                "procedural-rule-identity",
                "test:process_rule",
            ),
        ]
        self.fixture_ids: dict[str, str] = {}
        for public_kind, runtime_kind, key_kind, key in self.kind_fixtures:
            node_id = (
                "rg:common_final_state_client:"
                f"{runtime_kind}:fixture:{public_kind}"
            )
            self.fixture_ids[public_kind] = self.add_node(
                node_id,
                runtime_kind,
            )
            self.add_key(node_id, key_kind, key)

        self.machine_ids = [
            self.add_node(
                "rg:common_final_state_client:machine:susy:ambiguous:a",
                "machine",
            ),
            self.add_node(
                "rg:common_final_state_client:machine:susy:ambiguous:b",
                "machine",
            ),
        ]
        for node_id in self.machine_ids:
            self.add_key(
                node_id,
                "machine-resource-location",
                "susy:ambiguous",
            )
        self.worldgen_id = self.add_node(
            "rg:common_final_state_client:worldgen_deposit:test:vein",
            "worldgen_deposit",
        )
        self.universal_kind_ids = {
            "item": self.add_node(
                "rg:common_final_state_client:item:test:item",
                "item",
            ),
            "fluid": self.add_node(
                "rg:common_final_state_client:fluid:test_fluid",
                "fluid",
            ),
            "ore-dictionary": self.add_node(
                "rg:common_final_state_client:ore_dictionary_key:dustTest",
                "ore_dictionary_key",
            ),
            "ore-prefix": self.add_node(
                "rg:common_final_state_client:ore_prefix:dust",
                "ore_prefix",
            ),
            "machine": self.machine_ids[0],
            "worldgen-deposit": self.worldgen_id,
        }
        self.connection.commit()
        self.connection.close()

    def add_node(
        self,
        node_id: str,
        kind: str,
        *,
        profile: str = "COMMON_FINAL_STATE",
        physical_side: str = "CLIENT",
        snapshot_id: str = SNAPSHOT_ID,
    ) -> str:
        record = {
            "record_type": "node",
            "id": node_id,
            "kind": kind,
            "scope": {
                "snapshot_id": snapshot_id,
                "profile": profile,
                "physical_side": physical_side,
            },
            "attributes": {},
        }
        self.connection.execute(
            "INSERT INTO nodes "
            "(id, profile, physical_side, adapter, kind, json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                node_id,
                profile,
                physical_side,
                "atlas-query-test",
                kind,
                json.dumps(
                    record,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )
        return node_id

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
            "INSERT INTO node_keys "
            "(node_id, key_kind, key_value, profile, physical_side) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                node_id,
                key_kind,
                key_value,
                profile,
                physical_side,
            ),
        )

    def query(
        self,
        *,
        kind: str = "material",
        key_kind: str = "material-resource-location",
        key: str = "susy:test_material",
        scopes: list[str] | None = None,
        snapshot_id: str = SNAPSHOT_ID,
        question_id: str = "PLAYER-PRODUCE-MATERIAL-001",
    ) -> dict[str, object]:
        question = atlas.question_by_id(question_id)
        instance = atlas.build_v1_query_instance(
            question,
            snapshot_id=snapshot_id,
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
        instance["query_instance_id"] = atlas.query_instance_id(instance)
        return instance

    def resolve(
        self,
        instance: dict[str, object],
    ) -> tuple[atlas.ValidatedQuery, atlas.QueryResolution]:
        with RuntimeGraphReader(self.database) as reader:
            return atlas.resolve_query_instance(reader, instance)

    def test_v1_adapter_materializes_stable_explicit_request(self) -> None:
        question = atlas.question_by_id("PLAYER-PRODUCE-MATERIAL-001")
        first = atlas.build_v1_query_instance(
            question,
            snapshot_id=SNAPSHOT_ID,
            scope_values=["COMMON_FINAL_STATE:CLIENT"],
        )
        second = atlas.build_v1_query_instance(
            copy.deepcopy(question),
            snapshot_id=SNAPSHOT_ID,
            scope_values=["COMMON_FINAL_STATE:CLIENT"],
        )
        self.assertEqual(first, second)
        self.assertEqual(
            atlas.query_instance_id(first),
            first["query_instance_id"],
        )
        self.assertEqual(
            atlas.question_definition_sha256(question),
            first["question_definition_sha256"],
        )
        self.assertEqual(
            atlas.capability_policy_sha256(
                atlas.load_capability_policy()
            ),
            first["capability_policy_sha256"],
        )
        validated = atlas.validate_query_instance(first)
        self.assertEqual(
            "admitted",
            atlas.selector_capability(validated)["status"],
        )
        self.assertEqual(
            ("target", "routes"),
            validated.substantive_sections,
        )

    def test_exact_missing_profile_gap_and_multi_scope_resolution(self) -> None:
        exact = self.query()
        _, resolution = self.resolve(exact)
        self.assertEqual("resolved", resolution.status)
        self.assertEqual([self.material_client], [
            row["node_id"] for row in resolution.to_dict()["targets"]
        ])

        missing = self.query(key="susy:not_present")
        _, resolution = self.resolve(missing)
        self.assertEqual("missing", resolution.status)
        self.assertEqual(0, resolution.candidate_count)
        self.assertEqual(1, len(resolution.profile_gaps))

        gap = self.query(
            key="susy:client_only",
            scopes=[
                "COMMON_FINAL_STATE:CLIENT",
                "OFFLINE_ARTIFACT_STATE:OFFLINE",
            ],
        )
        _, resolution = self.resolve(gap)
        self.assertEqual("resolved-with-profile-gaps", resolution.status)
        self.assertEqual(1, resolution.candidate_count)
        self.assertEqual(
            "OFFLINE_ARTIFACT_STATE",
            resolution.profile_gaps[0].profile,
        )

        both = self.query(
            key="susy:test_material",
            scopes=[
                "COMMON_FINAL_STATE:CLIENT",
                "COMMON_FINAL_STATE:DEDICATED_SERVER",
            ],
        )
        _, resolution = self.resolve(both)
        self.assertEqual("resolved", resolution.status)
        self.assertEqual(
            [self.material_client, self.material_server],
            [target.node["id"] for target in resolution.targets],
        )

    def test_ambiguity_retains_all_ordered_candidates(self) -> None:
        instance = self.query(
            kind="machine",
            key_kind="machine-resource-location",
            key="susy:ambiguous",
            question_id="DEVELOPER-DECLARATION-001",
        )
        _, resolution = self.resolve(instance)
        self.assertEqual("ambiguous", resolution.status)
        self.assertEqual(sorted(self.machine_ids), [
            target.node["id"] for target in resolution.targets
        ])
        self.assertEqual(2, resolution.candidate_count)

    def test_cross_kind_exact_key_families_and_worldgen_node_id(self) -> None:
        for public_kind, _, key_kind, key in self.kind_fixtures:
            with self.subTest(kind=public_kind):
                instance = self.query(
                    kind=public_kind,
                    key_kind=key_kind,
                    key=key,
                )
                validated, resolution = self.resolve(instance)
                self.assertEqual("resolved", resolution.status)
                self.assertEqual(
                    public_kind,
                    atlas.selector_capability(validated)["kind"],
                )
                self.assertEqual(
                    self.fixture_ids[public_kind],
                    resolution.targets[0].node["id"],
                )

        worldgen = self.query(
            kind="worldgen-deposit",
            key_kind="runtime-node-id",
            key=self.worldgen_id,
        )
        _, resolution = self.resolve(worldgen)
        self.assertEqual("resolved", resolution.status)
        self.assertEqual(
            self.worldgen_id,
            resolution.targets[0].node["id"],
        )

    def test_recognized_but_unadmitted_kind_is_fail_closed(self) -> None:
        instance = self.query(
            kind="quest",
            key_kind="quest-id",
            key="qg:quest:173",
        )
        validated, resolution = self.resolve(instance)
        self.assertEqual(
            "unsupported-kind",
            atlas.selector_capability(validated)["status"],
        )
        self.assertEqual("unsupported-kind", resolution.status)
        self.assertEqual(0, resolution.candidate_count)
        result = atlas.build_query_preflight(validated, resolution)
        self.assertEqual("unsupported-query", result["result_status"])

    def test_request_identity_question_and_selector_mutations_fail(self) -> None:
        mutations = []

        changed_id = self.query()
        changed_id["query_instance_id"] = (
            "atlas-query:sha256:" + "0" * 64
        )
        mutations.append(changed_id)

        changed_question = self.query()
        changed_question["question_definition_sha256"] = "0" * 64
        changed_question["query_instance_id"] = atlas.query_instance_id(
            changed_question
        )
        mutations.append(changed_question)

        display_key = self.query()
        display_key["selector"]["key_kind"] = "display-name"
        display_key["query_instance_id"] = atlas.query_instance_id(display_key)
        mutations.append(display_key)

        hidden_scope = self.query()
        hidden_scope["selector"]["profile"] = "COMMON_FINAL_STATE"
        hidden_scope["query_instance_id"] = atlas.query_instance_id(hidden_scope)
        mutations.append(hidden_scope)

        for index, mutation in enumerate(mutations):
            with self.subTest(mutation=index):
                with self.assertRaises(atlas.AtlasQueryError):
                    atlas.validate_query_instance(mutation)

    def test_query_identity_binds_complete_capability_policy(self) -> None:
        instance = self.query()
        policy = atlas.load_capability_policy()
        mutation = copy.deepcopy(policy)
        routes = next(
            row
            for row in mutation["section_policies"]
            if row["section"] == "routes"
        )
        routes["required_authorities"].append("pinned-source")
        routes["required_authorities"].sort()
        policy_path = self.root / "mutated-capability-policy.json"
        policy_path.write_text(
            json.dumps(mutation, indent=2) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            atlas.AtlasQueryError,
            "capability policy hash differs",
        ):
            atlas.validate_query_instance(
                instance,
                policy_path=policy_path,
            )

        rebound = copy.deepcopy(instance)
        rebound["capability_policy_sha256"] = (
            atlas.capability_policy_sha256(mutation)
        )
        rebound["query_instance_id"] = atlas.query_instance_id(rebound)
        validated = atlas.validate_query_instance(
            rebound,
            policy_path=policy_path,
        )
        self.assertEqual(mutation, validated.policy)

    def test_scope_and_authority_mutations_fail(self) -> None:
        duplicate_scope = self.query()
        duplicate_scope["scopes"].append(
            copy.deepcopy(duplicate_scope["scopes"][0])
        )
        duplicate_scope["query_instance_id"] = atlas.query_instance_id(
            duplicate_scope
        )

        invalid_side = self.query()
        invalid_side["scopes"] = [
            {
                "profile": "CLIENT_JEI_FINAL_STATE",
                "physical_side": "DEDICATED_SERVER",
            }
        ]
        invalid_side["query_instance_id"] = atlas.query_instance_id(
            invalid_side
        )

        unsorted_authorities = self.query()
        optional = unsorted_authorities["authority_requirements"]["optional"]
        unsorted_authorities["authority_requirements"]["optional"] = list(
            reversed(optional)
        )
        unsorted_authorities["query_instance_id"] = atlas.query_instance_id(
            unsorted_authorities
        )

        overlapping = self.query()
        overlapping["authority_requirements"]["optional"].append(
            "runtime-mechanics"
        )
        overlapping["authority_requirements"]["optional"].sort()
        overlapping["query_instance_id"] = atlas.query_instance_id(overlapping)

        for index, mutation in enumerate(
            (
                duplicate_scope,
                invalid_side,
                unsorted_authorities,
                overlapping,
            )
        ):
            with self.subTest(mutation=index):
                with self.assertRaises(atlas.AtlasQueryError):
                    atlas.validate_query_instance(mutation)

    def test_snapshot_mismatch_is_not_reported_as_absence(self) -> None:
        instance = self.query(snapshot_id="SNAPSHOT-DIFFERENT")
        with self.assertRaisesRegex(
            atlas.AtlasQueryError,
            "different snapshot",
        ):
            self.resolve(instance)

    def test_runtime_only_authority_and_capabilities_remain_supported(self) -> None:
        validated, resolution = self.resolve(self.query())
        result = atlas.build_query_preflight(validated, resolution)
        self.assertEqual("resolved", result["result_status"])
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
        self.assertEqual(
            ["supported", "supported"],
            [row["status"] for row in result["capabilities"]],
        )
        self.assertIsNone(result["answer"])

    def test_required_source_authority_controls_only_source_sections(self) -> None:
        instance = self.query(
            question_id="DEVELOPER-DECLARATION-001",
        )
        validated, resolution = self.resolve(instance)
        unavailable = atlas.build_query_preflight(validated, resolution)
        self.assertEqual(
            ["supported", "unavailable", "unavailable"],
            [row["status"] for row in unavailable["capabilities"]],
        )
        self.assertEqual(
            "unresolved-evidence",
            unavailable["result_status"],
        )

        source = atlas.AuthorityObservation(
            "pinned-source",
            "available",
            ("SOURCE-SUSY-TEST-MATERIAL",),
            "one exact pinned source declaration is available",
            evidence_records(
                "pinned-source",
                ("SOURCE-SUSY-TEST-MATERIAL",),
            ),
        )
        available = atlas.build_query_preflight(
            validated,
            resolution,
            {"pinned-source": source},
        )
        self.assertEqual(
            ["supported", "supported", "supported"],
            [row["status"] for row in available["capabilities"]],
        )
        self.assertEqual("resolved", available["result_status"])
        self.assertEqual(
            ["SOURCE-SUSY-TEST-MATERIAL"],
            available["capabilities"][1]["evidence_ids"],
        )

    def test_ambiguous_required_authority_is_unresolved_not_unavailable(self) -> None:
        instance = self.query(
            question_id="DEVELOPER-DECLARATION-001",
        )
        validated, resolution = self.resolve(instance)
        ambiguous = atlas.AuthorityObservation(
            "pinned-source",
            "ambiguous",
            ("SOURCE-CANDIDATE-A", "SOURCE-CANDIDATE-B"),
            "two source candidates remain after exact runtime resolution",
            evidence_records(
                "pinned-source",
                ("SOURCE-CANDIDATE-A", "SOURCE-CANDIDATE-B"),
            ),
        )
        result = atlas.build_query_preflight(
            validated,
            resolution,
            {"pinned-source": ambiguous},
        )
        self.assertEqual(
            ["supported", "unresolved", "unresolved"],
            [row["status"] for row in result["capabilities"]],
        )
        self.assertEqual(
            ["available", "policy-unresolved", "policy-unresolved"],
            [row["reason_code"] for row in result["capabilities"]],
        )
        self.assertEqual("unresolved-evidence", result["result_status"])

    def test_caller_added_required_authority_blocks_aggregate_resolution(
        self,
    ) -> None:
        instance = self.query()
        requirements = instance["authority_requirements"]
        requirements["optional"].remove("pinned-source")
        requirements["required"].append("pinned-source")
        requirements["required"].sort()
        instance["query_instance_id"] = atlas.query_instance_id(instance)
        validated, resolution = self.resolve(instance)

        unavailable = atlas.build_query_preflight(validated, resolution)
        self.assertEqual(
            ["supported", "supported"],
            [row["status"] for row in unavailable["capabilities"]],
        )
        self.assertEqual(
            "unresolved-evidence",
            unavailable["result_status"],
        )

        source = atlas.AuthorityObservation(
            "pinned-source",
            "available",
            ("SOURCE-SUSY-TEST-MATERIAL",),
            "one exact pinned source declaration is available",
            evidence_records(
                "pinned-source",
                ("SOURCE-SUSY-TEST-MATERIAL",),
            ),
        )
        available = atlas.build_query_preflight(
            validated,
            resolution,
            {"pinned-source": source},
        )
        self.assertEqual("resolved", available["result_status"])

    def test_runtime_ambiguity_is_scoped_to_its_authority_basis(self) -> None:
        instance = self.query(
            key="susy:mixed_ambiguity",
            question_id="PLAYER-VERIFICATION-SCOPE-001",
            scopes=[
                "COMMON_FINAL_STATE:CLIENT",
                "CLIENT_JEI_FINAL_STATE:CLIENT",
            ],
        )
        validated, resolution = self.resolve(instance)
        self.assertEqual("ambiguous", resolution.status)
        result = atlas.build_query_preflight(validated, resolution)
        authority = {
            row["basis"]: row
            for row in result["authorities"]
        }
        self.assertEqual(
            "available",
            authority["runtime-mechanics"]["status"],
        )
        self.assertEqual(
            "ambiguous",
            authority["runtime-presentation"]["status"],
        )
        self.assertEqual("ambiguous", result["result_status"])

    def test_mixed_mechanics_and_presentation_authorities(self) -> None:
        instance = self.query(
            question_id="DEVELOPER-PROFILE-COMPARISON-001",
            scopes=[
                "COMMON_FINAL_STATE:CLIENT",
                "CLIENT_JEI_FINAL_STATE:CLIENT",
                "OFFLINE_ARTIFACT_STATE:OFFLINE",
                "COMMON_FINAL_STATE:DEDICATED_SERVER",
            ],
        )
        validated, resolution = self.resolve(instance)
        self.assertEqual(
            "resolved",
            resolution.status,
        )
        result = atlas.build_query_preflight(validated, resolution)
        authority = {
            row["basis"]: row
            for row in result["authorities"]
        }
        self.assertEqual(
            "available",
            authority["runtime-mechanics"]["status"],
        )
        self.assertEqual(
            "available",
            authority["runtime-presentation"]["status"],
        )
        self.assertEqual(
            ["supported", "supported"],
            [row["status"] for row in result["capabilities"]],
        )
        self.assertEqual("resolved", result["result_status"])

    def test_not_applicable_is_policy_driven_not_absence_driven(self) -> None:
        public_kind, _, key_kind, key = next(
            row for row in self.kind_fixtures if row[0] == "recipe"
        )
        instance = self.query(
            kind=public_kind,
            key_kind=key_kind,
            key=key,
        )
        validated, resolution = self.resolve(instance)
        result = atlas.build_query_preflight(validated, resolution)
        self.assertEqual(
            ["supported", "not-applicable"],
            [row["status"] for row in result["capabilities"]],
        )
        self.assertEqual(
            "target-kind-not-applicable",
            result["capabilities"][1]["reason_code"],
        )
        self.assertEqual("not-applicable", result["result_status"])

        missing = self.query(key="susy:not_present")
        validated, resolution = self.resolve(missing)
        missing_result = atlas.build_query_preflight(
            validated,
            resolution,
        )
        self.assertEqual(
            ["unresolved", "unresolved"],
            [row["status"] for row in missing_result["capabilities"]],
        )
        self.assertEqual(
            "unresolved-identity",
            missing_result["result_status"],
        )

    def test_optional_observation_states_do_not_become_negative_proof(self) -> None:
        validated, resolution = self.resolve(self.query())
        quest = atlas.AuthorityObservation(
            "quest-data",
            "not-observed",
            (),
            "the bounded quest graph was inspected without an exact link",
        )
        result = atlas.build_query_preflight(
            validated,
            resolution,
            {"quest-data": quest},
        )
        authority = {
            row["basis"]: row
            for row in result["authorities"]
        }
        self.assertEqual("not-observed", authority["quest-data"]["status"])
        self.assertEqual("resolved", result["result_status"])
        self.assertNotEqual(
            "not-applicable",
            authority["quest-data"]["status"],
        )

    def test_explicit_nonruntime_authority_states_remain_distinct(self) -> None:
        validated, resolution = self.resolve(
            self.query(question_id="DEVELOPER-DECLARATION-001")
        )
        cases = (
            (
                atlas.AuthorityObservation(
                    "pinned-source",
                    "unresolved",
                    ("SOURCE-SEMANTICALLY-OPEN",),
                    "a source record exists but semantic closure failed",
                    evidence_records(
                        "pinned-source",
                        ("SOURCE-SEMANTICALLY-OPEN",),
                    ),
                ),
                "unresolved",
                "unresolved-evidence",
            ),
            (
                atlas.AuthorityObservation(
                    "pinned-source",
                    "not-applicable",
                    (),
                    "positive source policy excludes this declaration form",
                ),
                "not-applicable",
                "unresolved-evidence",
            ),
        )
        for observation, authority_status, result_status in cases:
            with self.subTest(status=authority_status):
                result = atlas.build_query_preflight(
                    validated,
                    resolution,
                    {"pinned-source": observation},
                )
                authority = {
                    row["basis"]: row
                    for row in result["authorities"]
                }
                self.assertEqual(
                    authority_status,
                    authority["pinned-source"]["status"],
                )
                self.assertEqual(result_status, result["result_status"])

    def test_cross_kind_and_capability_keyed_conformance_matrix(self) -> None:
        selectors: dict[str, tuple[str, str]] = {
            "material": (
                "material-resource-location",
                "susy:test_material",
            ),
        }
        selectors.update(
            {
                public_kind: (key_kind, key)
                for public_kind, _, key_kind, key in self.kind_fixtures
            }
        )
        selectors.update(
            {
                public_kind: ("runtime-node-id", node_id)
                for public_kind, node_id in self.universal_kind_ids.items()
            }
        )
        policy = atlas.load_capability_policy()
        self.assertEqual(
            set(policy["admitted_target_kinds"]),
            set(selectors),
        )

        for public_kind in policy["admitted_target_kinds"]:
            with self.subTest(target_kind=public_kind):
                key_kind, key = selectors[public_kind]
                instance = self.query(
                    question_id="PLAYER-VERIFICATION-SCOPE-001",
                    kind=public_kind,
                    key_kind=key_kind,
                    key=key,
                )
                validated, resolution = self.resolve(instance)
                result = atlas.build_query_preflight(
                    validated,
                    resolution,
                )
                self.assertEqual("resolved", result["result_status"])
                self.assertEqual(
                    ["supported", "supported"],
                    [row["status"] for row in result["capabilities"]],
                )
                first = atlas.canonical_json_payload(result)
                second = atlas.canonical_json_payload(
                    atlas.build_query_preflight(validated, resolution)
                )
                self.assertEqual(first, second)
                atlas.validate_query_result(result)
                composed = bridge.build_query_answer(
                    runtime_database=self.database,
                    query_instance=instance,
                    catalog_root=None,
                    links_path=None,
                    quest_nodes_path=None,
                    quest_edges_path=None,
                )
                self.assertEqual("answered", composed["result_status"])
                self.assertEqual(
                    public_kind,
                    composed["answer"]["target"]["selector"]["kind"],
                )
                atlas.validate_query_result(composed)

        observed_sections: set[str] = set()
        for question in atlas.load_questions().values():
            if question["status"] != "active":
                continue
            needs_presentation = (
                "runtime-presentation"
                in question["required_evidence_bases"]
            )
            instance = self.query(
                question_id=question["id"],
                scopes=(
                    [
                        "COMMON_FINAL_STATE:CLIENT",
                        "COMMON_FINAL_STATE:DEDICATED_SERVER",
                        "CLIENT_JEI_FINAL_STATE:CLIENT",
                        "OFFLINE_ARTIFACT_STATE:OFFLINE",
                    ]
                    if needs_presentation
                    else ["COMMON_FINAL_STATE:CLIENT"]
                ),
            )
            validated, resolution = self.resolve(instance)
            requested = (
                set(instance["authority_requirements"]["required"])
                | set(instance["authority_requirements"]["optional"])
            )
            observations = {
                basis: atlas.AuthorityObservation(
                    basis,
                    "available",
                    (f"EVIDENCE-{basis.upper()}",),
                    f"synthetic exact {basis} authority is available",
                    evidence_records(
                        basis,
                        (f"EVIDENCE-{basis.upper()}",),
                    ),
                )
                for basis in requested
                if basis
                not in {"runtime-mechanics", "runtime-presentation"}
            }
            result = atlas.build_query_preflight(
                validated,
                resolution,
                observations,
            )
            self.assertEqual(
                "resolved",
                result["result_status"],
                question["id"],
            )
            self.assertTrue(
                all(
                    row["status"] == "supported"
                    for row in result["capabilities"]
                ),
                question["id"],
            )
            observed_sections.update(
                row["section"] for row in result["capabilities"]
            )

        policy_sections = {
            row["section"]
            for row in policy["section_policies"]
        }
        self.assertEqual(
            policy_sections,
            observed_sections,
        )

    def test_query_result_mutations_are_rejected(self) -> None:
        instance = self.query(
            question_id="DEVELOPER-DECLARATION-001",
        )
        validated, resolution = self.resolve(instance)
        source = atlas.AuthorityObservation(
            "pinned-source",
            "available",
            ("SOURCE-SUSY-TEST-MATERIAL",),
            "one exact pinned source declaration is available",
            evidence_records(
                "pinned-source",
                ("SOURCE-SUSY-TEST-MATERIAL",),
            ),
        )
        original = atlas.build_query_preflight(
            validated,
            resolution,
            {"pinned-source": source},
        )

        duplicate_authority = copy.deepcopy(original)
        duplicate_authority["authorities"][1] = copy.deepcopy(
            duplicate_authority["authorities"][0]
        )

        missing_source = copy.deepcopy(original)
        pinned = next(
            row
            for row in missing_source["authorities"]
            if row["basis"] == "pinned-source"
        )
        pinned["status"] = "unavailable"
        pinned["evidence_ids"] = []

        padded_capability = copy.deepcopy(original)
        padded_capability["capabilities"][0]["evidence_ids"].append(
            "UNBOUND-EVIDENCE"
        )

        missing_evidence_binding = copy.deepcopy(original)
        missing_evidence_binding["evidence"].pop()

        corrupt_evidence_binding = copy.deepcopy(original)
        corrupt_evidence_binding["evidence"][0]["record_sha256"] = "0" * 64

        missing_capability_evidence = copy.deepcopy(original)
        missing_capability_evidence["capabilities"][1]["evidence_ids"] = []

        drifted_runtime_evidence = copy.deepcopy(original)
        drifted_runtime = next(
            row
            for row in drifted_runtime_evidence["authorities"]
            if row["basis"] == "runtime-mechanics"
        )
        drifted_runtime["evidence_ids"] = ["BOGUS-RUNTIME-NODE"]
        drifted_runtime_evidence["capabilities"][0]["evidence_ids"] = [
            "BOGUS-RUNTIME-NODE"
        ]

        requested_not_requested = copy.deepcopy(original)
        requested_source = next(
            row
            for row in requested_not_requested["authorities"]
            if row["basis"] == "pinned-source"
        )
        requested_source["status"] = "not-requested"
        requested_source["evidence_ids"] = []

        wrong_status = copy.deepcopy(original)
        wrong_status["result_status"] = "unresolved-evidence"

        wrong_request = copy.deepcopy(original)
        wrong_request["request_id"] = (
            "atlas-query:sha256:" + "0" * 64
        )

        for index, mutation in enumerate(
            (
                duplicate_authority,
                missing_source,
                padded_capability,
                missing_evidence_binding,
                corrupt_evidence_binding,
                missing_capability_evidence,
                drifted_runtime_evidence,
                requested_not_requested,
                wrong_status,
                wrong_request,
            )
        ):
            with self.subTest(mutation=index):
                with self.assertRaises(atlas.AtlasQueryError):
                    atlas.validate_query_result(mutation)

    def test_resolution_scope_partition_is_total_and_canonical(self) -> None:
        instance = self.query(
            key="susy:client_only",
            scopes=[
                "COMMON_FINAL_STATE:CLIENT",
                "COMMON_FINAL_STATE:DEDICATED_SERVER",
                "OFFLINE_ARTIFACT_STATE:OFFLINE",
            ],
        )
        validated, resolution = self.resolve(instance)
        original = atlas.build_query_preflight(validated, resolution)
        self.assertEqual("resolved-with-profile-gaps", resolution.status)
        self.assertEqual(2, len(original["resolution"]["profile_gaps"]))

        missing_gap = copy.deepcopy(original)
        missing_gap["resolution"]["profile_gaps"].pop()

        ambiguous_instance = self.query(
            kind="machine",
            key_kind="machine-resource-location",
            key="susy:ambiguous",
            question_id="PLAYER-VERIFICATION-SCOPE-001",
        )
        ambiguous_validated, ambiguous_resolution = self.resolve(
            ambiguous_instance
        )
        reversed_targets = atlas.build_query_preflight(
            ambiguous_validated,
            ambiguous_resolution,
        )
        reversed_targets["resolution"]["targets"].reverse()

        ambiguous_gap_instance = self.query(
            kind="machine",
            key_kind="machine-resource-location",
            key="susy:ambiguous",
            question_id="PLAYER-VERIFICATION-SCOPE-001",
            scopes=[
                "COMMON_FINAL_STATE:CLIENT",
                "OFFLINE_ARTIFACT_STATE:OFFLINE",
            ],
        )
        ambiguous_gap_validated, ambiguous_gap_resolution = self.resolve(
            ambiguous_gap_instance
        )
        missing_ambiguous_gap = atlas.build_query_preflight(
            ambiguous_gap_validated,
            ambiguous_gap_resolution,
        )
        missing_ambiguous_gap["resolution"]["profile_gaps"].clear()

        for mutation in (
            missing_gap,
            reversed_targets,
            missing_ambiguous_gap,
        ):
            with self.assertRaises(atlas.AtlasQueryError):
                atlas.validate_query_result(mutation)

    def test_nested_answer_requires_full_semantics_and_resolution_agreement(
        self,
    ) -> None:
        instance = self.query(
            question_id="PLAYER-VERIFICATION-SCOPE-001",
        )
        original = bridge.build_query_answer(
            runtime_database=self.database,
            query_instance=instance,
            catalog_root=None,
            links_path=None,
            quest_nodes_path=None,
            quest_edges_path=None,
        )
        self.assertEqual("answered", original["result_status"])

        missing_runtime = copy.deepcopy(original)
        del missing_runtime["answer"]["runtime"]

        drifted_outer = copy.deepcopy(original)
        drifted_outer["resolution"]["targets"][0]["node_id"] = (
            "rg:common_final_state_client:material:susy:other"
        )
        runtime_authority = next(
            row
            for row in drifted_outer["authorities"]
            if row["basis"] == "runtime-mechanics"
        )
        runtime_authority["evidence_ids"] = [
            "rg:common_final_state_client:material:susy:other"
        ]
        for capability in drifted_outer["capabilities"]:
            capability["evidence_ids"] = [
                "rg:common_final_state_client:material:susy:other"
            ]

        unresolved_assertion = copy.deepcopy(original)
        target_assertion = next(
            row
            for row in unresolved_assertion["answer"]["assertions"]
            if row["path"] == "/target"
        )
        target_assertion["status"] = "unresolved"

        for mutation in (
            missing_runtime,
            drifted_outer,
            unresolved_assertion,
        ):
            with self.assertRaises(atlas.AtlasQueryError):
                atlas.validate_query_result(mutation)

    def test_authority_observation_contract_is_fail_closed(self) -> None:
        invalid = (
            (
                "pinned-source",
                "available",
                (),
                "missing evidence",
            ),
            (
                "pinned-source",
                "available",
                ("SOURCE-WITHOUT-RECORD",),
                "unbound evidence",
            ),
            (
                "pinned-source",
                "unavailable",
                ("SOURCE-IMPOSSIBLE",),
                "unavailable cannot cite evidence",
            ),
            (
                "unknown",
                "available",
                ("SOURCE-UNKNOWN",),
                "unknown authority",
            ),
        )
        for index, arguments in enumerate(invalid):
            with self.subTest(observation=index):
                with self.assertRaises(atlas.AtlasQueryError):
                    atlas.AuthorityObservation(*arguments)


if __name__ == "__main__":
    unittest.main()
