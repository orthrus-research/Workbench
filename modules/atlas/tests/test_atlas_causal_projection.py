#!/usr/bin/env python3

"""Audience-fidelity and bridge tests for ATLAS-M4-B01."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import workbench_atlas.atlas_causal_projection as projection
import workbench_atlas.atlas_causal_provenance_contract as provenance
import workbench_atlas.atlas_causal_query as causal_query
import workbench_atlas.corpus_bridge as corpus_bridge
import test_atlas_causal_query as query_fixtures  # noqa: E402
from workbench_atlas.knowledge_catalog import read_json  # noqa: E402


class AtlasCausalProjectionTests(unittest.TestCase):
    def test_cli_path_arguments_are_resolvable(self) -> None:
        arguments = projection.parser().parse_args(["check", "projection.json"])
        self.assertEqual(arguments.projection, Path("projection.json"))

    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = provenance.load_policy()
        query_fixtures.AtlasCausalQueryTests.policy = cls.policy

    @classmethod
    def result(
        cls,
        status: str,
    ) -> dict:
        if status == "closed":
            document = query_fixtures.AtlasCausalQueryTests.normalize()
            request = query_fixtures.AtlasCausalQueryTests.request(document)
        elif status == "partial":
            document = query_fixtures.AtlasCausalQueryTests.normalize(
                runtime_shape="partial"
            )
            request = query_fixtures.AtlasCausalQueryTests.request(document)
        elif status == "bounded":
            document = query_fixtures.AtlasCausalQueryTests.normalize()
            request = query_fixtures.AtlasCausalQueryTests.request(
                document,
                bounds={
                    "max_paths": 8,
                    "max_nodes": 3,
                    "max_relations": 32,
                    "max_evidence_records": 16,
                },
            )
        elif status == "unresolved":
            document = query_fixtures.AtlasCausalQueryTests.normalize(
                runtime_shape="final-only"
            )
            request = query_fixtures.AtlasCausalQueryTests.request(document)
        elif status == "unavailable":
            document = query_fixtures.AtlasCausalQueryTests.normalize(
                runtime_shape="final-only",
                authority_unavailable=True,
            )
            request = query_fixtures.AtlasCausalQueryTests.request(document)
        else:
            raise AssertionError(f"unknown fixture status: {status}")
        result = causal_query.query(document, request, cls.policy)
        if result["status"] != status:
            raise AssertionError(
                f"fixture produced {result['status']} instead of {status}"
            )
        return result

    @staticmethod
    def reidentify(document: dict) -> None:
        for audience in ("developer", "player"):
            for path in document[audience]["paths"]:
                for step in path["steps"]:
                    step["step_id"] = provenance.content_id(
                        projection.STEP_PREFIX,
                        step,
                        "step_id",
                    )
                path["projection_path_id"] = provenance.content_id(
                    projection.PATH_PREFIX,
                    path,
                    "projection_path_id",
                )
        document["projection_id"] = provenance.content_id(
            projection.PROJECTION_PREFIX,
            document,
            "projection_id",
        )

    def test_all_five_states_project_and_validate(self) -> None:
        for status in (
            "closed",
            "partial",
            "bounded",
            "unresolved",
            "unavailable",
        ):
            with self.subTest(status=status):
                document = projection.project(
                    self.result(status),
                    self.policy,
                )
                self.assertIs(
                    document,
                    projection.validate_projection(document, self.policy),
                )
                self.assertEqual(status, document["developer"]["status"])
                self.assertEqual(status, document["player"]["status"])
                self.assertEqual(
                    document["developer"]["summary_code"],
                    document["player"]["summary_code"],
                )
                self.assertEqual(
                    document["developer"]["limitations"],
                    document["player"]["limitations"],
                )

    def test_closed_projection_is_lossless_across_audiences(self) -> None:
        source = self.result("closed")
        document = projection.project(source, self.policy)
        source_path = source["paths"][0]
        developer_path = document["developer"]["paths"][0]
        player_path = document["player"]["paths"][0]
        self.assertEqual(source_path["path_id"], developer_path["path_id"])
        self.assertEqual(source_path["path_id"], player_path["path_id"])
        self.assertEqual(
            source_path["node_ids"],
            [item["node_id"] for item in developer_path["nodes"]],
        )
        self.assertEqual(
            source_path["relation_ids"],
            [item["relation_id"] for item in developer_path["steps"]],
        )
        self.assertEqual(
            source_path["relation_ids"],
            [item["relation_id"] for item in player_path["steps"]],
        )
        self.assertEqual(
            [item["predicate"] for item in developer_path["steps"]],
            [item["action_code"] for item in player_path["steps"]],
        )
        evidence_ids = [item["evidence_id"] for item in source["evidence"]]
        self.assertEqual(
            evidence_ids,
            document["developer"]["evidence_ids"],
        )
        self.assertEqual(evidence_ids, document["player"]["evidence_ids"])

    def test_every_c01_predicate_has_stable_player_wording(self) -> None:
        self.assertEqual(
            provenance.PREDICATES,
            set(projection.PLAYER_ACTION_WORDING),
        )
        self.assertTrue(
            all(
                value and value[-1] == "."
                for value in projection.PLAYER_ACTION_WORDING.values()
            )
        )

    def test_omitted_or_reordered_transition_is_rejected(self) -> None:
        document = projection.project(self.result("closed"), self.policy)
        document["player"]["paths"][0]["steps"].pop(1)
        self.reidentify(document)
        with self.assertRaisesRegex(
            projection.AtlasCausalProjectionError,
            "differs from its exact C01 derivation",
        ):
            projection.validate_projection(document, self.policy)

        document = projection.project(self.result("closed"), self.policy)
        document["player"]["paths"][0]["steps"].reverse()
        for position, step in enumerate(
            document["player"]["paths"][0]["steps"]
        ):
            step["position"] = position
        self.reidentify(document)
        with self.assertRaisesRegex(
            projection.AtlasCausalProjectionError,
            "differs from its exact C01 derivation",
        ):
            projection.validate_projection(document, self.policy)

    def test_strength_wording_scope_and_bounds_cannot_drift(self) -> None:
        document = projection.project(self.result("closed"), self.policy)
        document["player"]["paths"][0]["steps"][0][
            "causal_strength"
        ] = "causation"
        document["player"]["paths"][0]["steps"][0][
            "wording"
        ] = "This wording makes a stronger claim."
        self.reidentify(document)
        with self.assertRaisesRegex(
            projection.AtlasCausalProjectionError,
            "differs from its exact C01 derivation",
        ):
            projection.validate_projection(document, self.policy)

        document = projection.project(self.result("closed"), self.policy)
        document["binding"]["scope"]["physical_side"] = "CLIENT"
        self.reidentify(document)
        with self.assertRaisesRegex(
            projection.AtlasCausalProjectionError,
            "differs from its exact C01 derivation",
        ):
            projection.validate_projection(document, self.policy)

        document = projection.project(self.result("bounded"), self.policy)
        document["player"]["limitations"]["truncation"]["truncated"] = False
        self.reidentify(document)
        with self.assertRaisesRegex(
            projection.AtlasCausalProjectionError,
            "differs from its exact C01 derivation",
        ):
            projection.validate_projection(document, self.policy)

    def test_evidence_padding_and_negative_wording_are_rejected(self) -> None:
        document = projection.project(self.result("unresolved"), self.policy)
        document["player"]["evidence_ids"].append(
            "atlas-provenance-evidence:sha256:" + "0" * 64
        )
        document["player"]["evidence_ids"].sort()
        self.reidentify(document)
        with self.assertRaisesRegex(
            projection.AtlasCausalProjectionError,
            "differs from its exact C01 derivation",
        ):
            projection.validate_projection(document, self.policy)

        document = projection.project(self.result("unresolved"), self.policy)
        document["player"]["statements"][0][
            "bounded_wording"
        ] = "Nothing caused this."
        self.reidentify(document)
        with self.assertRaisesRegex(
            projection.AtlasCausalProjectionError,
            "differs from its exact C01 derivation",
        ):
            projection.validate_projection(document, self.policy)

    def test_projection_is_byte_deterministic(self) -> None:
        source = self.result("closed")
        first = projection.project(source, self.policy)
        second = projection.project(
            copy.deepcopy(source),
            copy.deepcopy(self.policy),
        )
        self.assertEqual(
            provenance.canonical_json(first),
            provenance.canonical_json(second),
        )

    def test_corpus_bridge_admits_exact_snapshot_scope_and_target(self) -> None:
        source = self.result("closed")
        final = source["request"]["final_runtime_record"]
        scope = source["request"]["scope"]
        scopes = (
            corpus_bridge.Scope(
                scope["profile"],
                scope["physical_side"],
            ),
        )
        document = corpus_bridge.causal_provenance_projection(
            source,
            snapshot_id=source["request"]["snapshot_id"],
            scopes=scopes,
            runtime_node_ids=[final["runtime_node_id"]],
        )
        self.assertIs(document, projection.validate_projection(document))

        with self.assertRaisesRegex(
            corpus_bridge.CorpusBridgeError,
            "different snapshot",
        ):
            corpus_bridge.causal_provenance_projection(
                source,
                snapshot_id="SNAPSHOT-OTHER",
                scopes=scopes,
                runtime_node_ids=[final["runtime_node_id"]],
            )
        with self.assertRaisesRegex(
            corpus_bridge.CorpusBridgeError,
            "scope was not requested",
        ):
            corpus_bridge.causal_provenance_projection(
                source,
                snapshot_id=source["request"]["snapshot_id"],
                scopes=(
                    corpus_bridge.Scope(
                        "COMMON_FINAL_STATE",
                        "CLIENT",
                    ),
                ),
                runtime_node_ids=[final["runtime_node_id"]],
            )
        with self.assertRaisesRegex(
            corpus_bridge.CorpusBridgeError,
            "differs from the resolved Atlas target",
        ):
            corpus_bridge.causal_provenance_projection(
                source,
                snapshot_id=source["request"]["snapshot_id"],
                scopes=scopes,
                runtime_node_ids=[
                    "rg:common_final_state_dedicated_server:recipe:other"
                ],
            )

    def test_reviewed_example_is_valid_and_reproducible(self) -> None:
        if not projection.EXAMPLE_PATH.exists():
            self.skipTest("reviewed B01 example has not been published yet")
        example = read_json(projection.EXAMPLE_PATH)
        self.assertIs(
            example,
            projection.validate_projection(example, self.policy),
        )
        regenerated = projection.project(
            self.result("closed"),
            self.policy,
        )
        self.assertEqual(
            provenance.canonical_json(example),
            provenance.canonical_json(regenerated),
        )


if __name__ == "__main__":
    unittest.main()
