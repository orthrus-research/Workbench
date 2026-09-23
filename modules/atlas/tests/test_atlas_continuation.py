#!/usr/bin/env python3

from __future__ import annotations

import copy
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parent))

from workbench_atlas.atlas_continuation import (  # noqa: E402
    AtlasContinuationError,
    canonical_sha256,
    compose_continuation,
    compose_traversal_continuation,
    create_delta,
    create_manifest,
    delta_id,
    invalidate_traversal_continuation,
    manifest_sha256,
    resume_traversal_continuation,
    start_traversal_continuation,
    validate_delta,
    validate_manifest,
)
import workbench_atlas.runtime_graph as graph
from workbench_atlas.runtime_graph_chain_query import (  # noqa: E402
    RuntimeGraphChainContinuation,
    build_process_chain_from_targets,
)
from workbench_atlas.runtime_graph_domain_query import (  # noqa: E402
    ProfileScope,
    ScopedTarget,
)
from workbench_atlas.runtime_graph_query import NodeSelector, RuntimeGraphReader  # noqa: E402
from test_runtime_graph_chain_query import COMMON, GraphFixture  # noqa: E402

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "examples"


def binding(kind: str = "route") -> dict[str, object]:
    limits = (
        {
            "include_chanced_outputs": True,
            "max_depth": 8,
            "max_routes": 50,
        }
        if kind == "route"
        else {
            "max_consumers_per_target": 25,
            "max_depth": 4,
            "max_operations": 250,
        }
    )
    return {
        "query_instance_id": "atlas-query:sha256:" + "1" * 64,
        "query_instance_sha256": "2" * 64,
        "original_request_sha256": "3" * 64,
        "algorithm": {
            "id": f"ATLAS-{kind.upper()}-CONTINUATION",
            "version": 1,
        },
        "snapshot_id": "SNAPSHOT-FIXTURE",
        "scopes": [
            {
                "profile": "COMMON_FINAL_STATE",
                "physical_side": "CLIENT",
            }
        ],
        "roots": [
            {
                "profile": "COMMON_FINAL_STATE",
                "physical_side": "CLIENT",
                "node_kind": "item_variant",
                "node_id": "rg:fixture:item_variant:root",
            }
        ],
        "structural_limits": limits,
        "fairness_policy_id": (
            "ATLAS-ROUTE-BREADTH-FIRST-V1"
            if kind == "route"
            else "ATLAS-RECYCLING-DEPTH-RANK-V1"
        ),
        "evidence_sha256": "4" * 64,
        "runtime_projection": {
            "format": "susy-runtime-graph-query-projection-v1",
            "bytes": 1,
            "sha256": "5" * 64,
            "sqlite_application_id": 1398098247,
            "sqlite_user_version": 2,
        },
    }


class AtlasContinuationContractTests(unittest.TestCase):
    def _root(self, kind: str = "route") -> dict[str, object]:
        return create_manifest(
            kind=kind,
            binding=binding(kind),
            state={"queue": ["root"], "visited": []},
            result={"rows": [], "truncated": True},
            frontier={"phase": "queue", "pending": ["root"]},
            allocated_work_items=1,
            consumed_work_items=1,
            cumulative_work_items=1,
            page_ranges=[
                {
                    "stream": "producer-occurrences",
                    "subject_id": "root",
                    "start": 0,
                    "end": 1,
                }
            ],
        )

    def _child(
        self,
        parent: dict[str, object],
        *,
        complete: bool = False,
    ) -> dict[str, object]:
        return create_manifest(
            kind=str(parent["kind"]),
            binding=copy.deepcopy(parent["binding"]),
            state={
                "queue": [] if complete else ["child"],
                "visited": ["root"],
            },
            result={
                "rows": [{"id": "route:1"}],
                "truncated": not complete,
            },
            frontier={
                "phase": "complete" if complete else "queue",
                "pending": [] if complete else ["child"],
            },
            allocated_work_items=2,
            consumed_work_items=1,
            cumulative_work_items=2,
            page_ranges=[
                {
                    "stream": "producer-occurrences",
                    "subject_id": "root",
                    "start": 0,
                    "end": 1,
                },
                {
                    "stream": "producer-occurrences",
                    "subject_id": "root",
                    "start": 1,
                    "end": 2,
                }
            ],
            status="complete" if complete else "active",
            parent={
                "continuation_id": parent["continuation_id"],
                "manifest_sha256": manifest_sha256(parent),
            },
            segment_index=1,
            ancestry=[parent["continuation_id"]],
        )

    def test_manifest_and_delta_round_trip_canonically(self) -> None:
        parent = self._root()
        child = self._child(parent)
        delta = create_delta(parent, child)

        self.assertIs(validate_manifest(parent), parent)
        self.assertIs(validate_manifest(child), child)
        self.assertIs(validate_delta(delta), delta)
        self.assertEqual(child, compose_continuation(parent, delta))
        self.assertEqual(delta, create_delta(parent, child))
        self.assertIn(
            "/result/rows/0",
            [row["path"] for row in delta["operations"]],
        )
        self.assertNotIn(
            "/result/rows",
            [row["path"] for row in delta["operations"]],
        )

    def test_equivalent_but_noncanonical_array_patch_is_rejected(self) -> None:
        parent = self._root()
        child = self._child(parent)
        delta = create_delta(parent, child)
        candidate = copy.deepcopy(delta)
        candidate["operations"] = [
            row
            for row in candidate["operations"]
            if not row["path"].startswith("/result/rows")
        ]
        candidate["operations"].append(
            {
                "op": "replace",
                "path": "/result/rows",
                "value": copy.deepcopy(child["result"]["rows"]),
            }
        )
        candidate["delta_id"] = delta_id(candidate)
        with self.assertRaisesRegex(
            AtlasContinuationError, "not the canonical"
        ):
            compose_continuation(parent, candidate)

    def test_committed_canonical_example_validates(self) -> None:
        import json

        example = json.loads(
            (
                CORPUS_ROOT / "atlas-continuation-example-v1.json"
            ).read_text(encoding="utf-8")
        )
        invalidations = json.loads(
            (
                CORPUS_ROOT
                / "atlas-continuation-invalidation-examples-v1.json"
            ).read_text(encoding="utf-8")
        )
        self.assertIs(validate_manifest(example), example)
        self.assertEqual(
            sorted(row["expected_reason"] for row in invalidations),
            sorted(
                {
                    "snapshot-changed",
                    "scope-changed",
                    "algorithm-changed",
                    "structural-policy-changed",
                    "evidence-changed",
                    "wrong-parent",
                    "budget-drift",
                    "state-digest-mismatch",
                }
            ),
        )

    def test_recycling_uses_the_same_envelope(self) -> None:
        parent = self._root("recycling")
        child = self._child(parent, complete=True)

        self.assertEqual(
            child,
            compose_continuation(parent, create_delta(parent, child)),
        )

    def test_explicit_invalidation_closes_lineage_canonically(self) -> None:
        parent = self._root()
        packet = invalidate_traversal_continuation(
            parent,
            ["evidence-changed", "snapshot-changed"],
        )
        child = packet["manifest"]
        self.assertEqual("invalidated", child["status"])
        self.assertFalse(child["invalidation"]["valid"])
        self.assertEqual(
            ["evidence-changed", "snapshot-changed"],
            child["invalidation"]["reasons"],
        )
        self.assertEqual(
            child,
            compose_continuation(parent, packet["delta"]),
        )
        with self.assertRaisesRegex(
            AtlasContinuationError, "only an active"
        ):
            invalidate_traversal_continuation(
                child,
                ["snapshot-changed"],
            )

    def test_tampered_manifest_state_is_rejected(self) -> None:
        candidate = copy.deepcopy(self._root())
        candidate["state"]["visited"].append("fabricated")
        with self.assertRaisesRegex(
            AtlasContinuationError, "state digest differs"
        ):
            validate_manifest(candidate)

    def test_changed_scope_or_algorithm_is_rejected(self) -> None:
        parent = self._root()
        for mutation in ("scope", "algorithm"):
            candidate_binding = copy.deepcopy(parent["binding"])
            if mutation == "scope":
                candidate_binding["scopes"][0]["physical_side"] = (
                    "DEDICATED_SERVER"
                )
            else:
                candidate_binding["algorithm"]["version"] = 2
            child = create_manifest(
                kind="route",
                binding=candidate_binding,
                state={"queue": [], "visited": ["root"]},
                result={"rows": [], "truncated": False},
                frontier={"phase": "complete", "pending": []},
                allocated_work_items=1,
                consumed_work_items=1,
                cumulative_work_items=2,
                page_ranges=[
                    {
                        "stream": "producer-occurrences",
                        "subject_id": "root",
                        "start": 0,
                        "end": 1,
                    },
                    {
                        "stream": "producer-occurrences",
                        "subject_id": "root",
                        "start": 1,
                        "end": 2,
                    },
                ],
                status="complete",
                parent={
                    "continuation_id": parent["continuation_id"],
                    "manifest_sha256": manifest_sha256(parent),
                },
                segment_index=1,
                ancestry=[parent["continuation_id"]],
            )
            with self.assertRaisesRegex(
                AtlasContinuationError, "immutable binding"
            ):
                create_delta(parent, child)

    def test_wrong_parent_and_replayed_delta_are_rejected(self) -> None:
        parent = self._root()
        child = self._child(parent)
        delta = create_delta(parent, child)
        wrong_parent = self._root("recycling")

        with self.assertRaisesRegex(
            AtlasContinuationError, "parent binding differs"
        ):
            compose_continuation(wrong_parent, delta)
        with self.assertRaisesRegex(
            AtlasContinuationError, "replay"
        ):
            compose_continuation(
                parent,
                delta,
                seen_delta_ids=[delta["delta_id"]],
            )

    def test_budget_drift_is_rejected_before_delta_creation(self) -> None:
        parent = self._root()
        child = self._child(parent)
        child["cumulative_budget"]["consumed_work_items"] = 3
        child["continuation_id"] = (
            "atlas-continuation:sha256:" + "0" * 64
        )
        # Rebuild a self-consistent manifest whose cumulative amount still
        # disagrees with the parent plus this segment.
        child = create_manifest(
            kind="route",
            binding=copy.deepcopy(parent["binding"]),
            state=child["state"],
            result=child["result"],
            frontier=child["frontier"],
            allocated_work_items=2,
            consumed_work_items=1,
            cumulative_work_items=3,
            page_ranges=[
                {
                    "stream": "producer-occurrences",
                    "subject_id": "root",
                    "start": 0,
                    "end": 1,
                },
                {
                    "stream": "producer-occurrences",
                    "subject_id": "root",
                    "start": 1,
                    "end": 3,
                },
            ],
            parent={
                "continuation_id": parent["continuation_id"],
                "manifest_sha256": manifest_sha256(parent),
            },
            segment_index=1,
            ancestry=[parent["continuation_id"]],
        )
        with self.assertRaisesRegex(
            AtlasContinuationError, "budget drifted"
        ):
            create_delta(parent, child)

    def test_noncanonical_page_ranges_are_rejected(self) -> None:
        with self.assertRaisesRegex(
            AtlasContinuationError, "ordered"
        ):
            create_manifest(
                kind="route",
                binding=binding(),
                state={},
                result={},
                frontier={},
                allocated_work_items=1,
                consumed_work_items=0,
                cumulative_work_items=0,
                page_ranges=[
                    {
                        "stream": "z",
                        "subject_id": "root",
                        "start": 0,
                        "end": 1,
                    },
                    {
                        "stream": "a",
                        "subject_id": "root",
                        "start": 0,
                        "end": 1,
                    },
                ],
            )

        with self.assertRaisesRegex(
            AtlasContinuationError, "overlap"
        ):
            create_manifest(
                kind="route",
                binding=binding(),
                state={},
                result={},
                frontier={},
                allocated_work_items=3,
                consumed_work_items=3,
                cumulative_work_items=3,
                page_ranges=[
                    {
                        "stream": "producer-occurrences",
                        "subject_id": "root",
                        "start": 0,
                        "end": 2,
                    },
                    {
                        "stream": "producer-occurrences",
                        "subject_id": "root",
                        "start": 1,
                        "end": 2,
                    },
                ],
            )

    def test_forged_completeness_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            AtlasContinuationError, "complete frontier"
        ):
            create_manifest(
                kind="route",
                binding=binding(),
                state={"pending": ["root"]},
                result={"routes": []},
                frontier={
                    "phase": "producer-occurrences",
                    "pending": ["root"],
                },
                allocated_work_items=1,
                consumed_work_items=1,
                cumulative_work_items=1,
                page_ranges=[
                    {
                        "stream": "producer-occurrences",
                        "subject_id": "root",
                        "start": 0,
                        "end": 1,
                    }
                ],
                status="complete",
            )


class AtlasTraversalCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = GraphFixture(self.root)
        self.addCleanup(self.fixture.close)

    @staticmethod
    def query_instance() -> dict[str, object]:
        semantic = {
            "schema_version": 1,
            "format": "susy-atlas-query-instance-v1",
            "snapshot_id": "SNAPSHOT-FIXTURE",
            "scopes": [
                {
                    "profile": "COMMON_FINAL_STATE",
                    "physical_side": "CLIENT",
                }
            ],
            "selector": {
                "kind": "item",
                "key_kind": "item-resource-location",
                "key": "fixture:target",
            },
        }
        return {
            **semantic,
            "query_instance_id": (
                "atlas-query:sha256:" + canonical_sha256(semantic)
            ),
        }

    def test_route_lineage_composes_to_fresh_result(self) -> None:
        raw = self.fixture.add_item("manifest-raw")
        intermediate = self.fixture.add_item("manifest-intermediate")
        target = self.fixture.add_item("manifest-target")
        self.fixture.add_recipe(
            "manifest-intermediate",
            inputs=[("consumes", [raw], {"amount": 1})],
            outputs=[("produces", [intermediate], {"amount": 1})],
        )
        self.fixture.add_recipe(
            "manifest-target",
            inputs=[("consumes", [intermediate], {"amount": 1})],
            outputs=[("produces", [target], {"amount": 1})],
        )
        self.fixture.commit()

        with RuntimeGraphReader(self.fixture.database) as reader:
            node = reader.resolve_one(NodeSelector.by_id(target))
            scoped = ScopedTarget(ProfileScope(*COMMON), node)
            fresh = build_process_chain_from_targets(reader, [scoped])
            session = RuntimeGraphChainContinuation(reader, [scoped])
            packet = start_traversal_continuation(
                session,
                self.query_instance(),
                [{"id": "evidence:fixture"}],
                max_work_items=1,
            )
            parent = packet["manifest"]
            deltas = []
            while parent["status"] == "active":
                packet = resume_traversal_continuation(
                    reader,
                    parent,
                    max_work_items=1,
                )
                child = packet["manifest"]
                delta = packet["delta"]
                self.assertEqual(
                    child,
                    compose_traversal_continuation(parent, delta),
                )
                deltas.append(delta["delta_id"])
                parent = child

            self.assertEqual(fresh, parent["result"])
            self.assertEqual(
                parent["segment_index"],
                len(parent["ancestry"]),
            )
            self.assertEqual(len(deltas), len(set(deltas)))

    def test_resume_rejects_self_consistent_but_fabricated_result(self) -> None:
        target = self.fixture.add_item("manifest-tamper-target")
        self.fixture.add_recipe(
            "manifest-tamper-route",
            outputs=[("produces", [target], None)],
        )
        self.fixture.commit()

        with RuntimeGraphReader(self.fixture.database) as reader:
            node = reader.resolve_one(NodeSelector.by_id(target))
            scoped = ScopedTarget(ProfileScope(*COMMON), node)
            packet = start_traversal_continuation(
                RuntimeGraphChainContinuation(reader, [scoped]),
                self.query_instance(),
                [],
                max_work_items=1,
            )
            parent = packet["manifest"]
            fabricated = create_manifest(
                kind="route",
                binding=copy.deepcopy(parent["binding"]),
                state=copy.deepcopy(parent["state"]),
                result={"fabricated": True},
                frontier=copy.deepcopy(parent["frontier"]),
                allocated_work_items=1,
                consumed_work_items=1,
                cumulative_work_items=1,
                page_ranges=copy.deepcopy(parent["page_ranges"]),
            )
            with self.assertRaisesRegex(
                AtlasContinuationError, "state differs"
            ):
                resume_traversal_continuation(
                    reader,
                    fabricated,
                    max_work_items=1,
                )

    def test_resume_rejects_a_different_runtime_projection(self) -> None:
        alternate_root = self.root / "alternate"
        alternate_root.mkdir()
        alternate = GraphFixture(alternate_root)
        self.addCleanup(alternate.close)
        target = self.fixture.add_item("projection-bound-target")
        alternate_target = alternate.add_item("projection-bound-target")
        self.assertEqual(target, alternate_target)
        self.fixture.add_recipe(
            "projection-bound-route",
            outputs=[("produces", [target], None)],
        )
        self.fixture.commit()
        alternate.commit()

        with RuntimeGraphReader(self.fixture.database) as reader:
            node = reader.resolve_one(NodeSelector.by_id(target))
            packet = start_traversal_continuation(
                RuntimeGraphChainContinuation(
                    reader,
                    [ScopedTarget(ProfileScope(*COMMON), node)],
                ),
                self.query_instance(),
                [],
                max_work_items=1,
            )
        with RuntimeGraphReader(alternate.database) as reader:
            with self.assertRaisesRegex(
                AtlasContinuationError,
                "immutable binding",
            ):
                resume_traversal_continuation(
                    reader,
                    packet["manifest"],
                    max_work_items=1,
                )

    def test_composition_rejects_complete_result_with_stale_state(
        self,
    ) -> None:
        target = self.fixture.add_item("stale-complete-state-target")
        self.fixture.add_recipe(
            "stale-complete-state-route",
            outputs=[("produces", [target], None)],
        )
        self.fixture.commit()

        with RuntimeGraphReader(self.fixture.database) as reader:
            node = reader.resolve_one(NodeSelector.by_id(target))
            parent = start_traversal_continuation(
                RuntimeGraphChainContinuation(
                    reader,
                    [ScopedTarget(ProfileScope(*COMMON), node)],
                ),
                self.query_instance(),
                [],
                max_work_items=1,
            )["manifest"]
            legitimate = resume_traversal_continuation(
                reader,
                parent,
                max_work_items=1,
            )["manifest"]
        self.assertEqual("complete", legitimate["status"])
        forged = create_manifest(
            kind="route",
            binding=copy.deepcopy(parent["binding"]),
            state=copy.deepcopy(parent["state"]),
            result=copy.deepcopy(legitimate["result"]),
            frontier=copy.deepcopy(legitimate["frontier"]),
            allocated_work_items=1,
            consumed_work_items=0,
            cumulative_work_items=parent["cumulative_budget"][
                "consumed_work_items"
            ],
            page_ranges=copy.deepcopy(parent["page_ranges"]),
            status="complete",
            parent={
                "continuation_id": parent["continuation_id"],
                "manifest_sha256": manifest_sha256(parent),
            },
            segment_index=1,
            ancestry=[parent["continuation_id"]],
        )
        delta = create_delta(parent, forged)
        with self.assertRaisesRegex(
            AtlasContinuationError,
            "state projection",
        ):
            compose_traversal_continuation(parent, delta)

    def test_start_rejects_duplicate_or_reordered_evidence(self) -> None:
        target = self.fixture.add_item("manifest-evidence-target")
        self.fixture.add_recipe(
            "manifest-evidence-route",
            outputs=[("produces", [target], None)],
        )
        self.fixture.commit()

        with RuntimeGraphReader(self.fixture.database) as reader:
            node = reader.resolve_one(NodeSelector.by_id(target))
            scoped = ScopedTarget(ProfileScope(*COMMON), node)
            for evidence in (
                [{"id": "evidence:b"}, {"id": "evidence:a"}],
                [{"id": "evidence:a"}, {"id": "evidence:a"}],
            ):
                with self.subTest(evidence=evidence):
                    with self.assertRaisesRegex(
                        AtlasContinuationError,
                        "unique canonically ordered",
                    ):
                        start_traversal_continuation(
                            RuntimeGraphChainContinuation(
                                reader,
                                [scoped],
                            ),
                            self.query_instance(),
                            evidence,
                            max_work_items=1,
                        )

    def test_empty_finalization_continuation_is_explicit(self) -> None:
        target = self.fixture.add_item("manifest-empty-target")
        self.fixture.add_recipe(
            "manifest-empty-route",
            outputs=[("produces", [target], None)],
        )
        self.fixture.commit()

        with RuntimeGraphReader(self.fixture.database) as reader:
            node = reader.resolve_one(NodeSelector.by_id(target))
            scoped = ScopedTarget(ProfileScope(*COMMON), node)
            parent = start_traversal_continuation(
                RuntimeGraphChainContinuation(reader, [scoped]),
                self.query_instance(),
                [],
                max_work_items=1,
            )["manifest"]
            self.assertEqual("active", parent["status"])
            packet = resume_traversal_continuation(
                reader,
                parent,
                max_work_items=1,
            )
            child = packet["manifest"]
            self.assertEqual("complete", child["status"])
            self.assertEqual(
                0,
                child["segment_budget"]["consumed_work_items"],
            )
            self.assertEqual(0, packet["delta"]["budget_increment"])
            self.assertEqual(
                child,
                compose_traversal_continuation(
                    parent,
                    packet["delta"],
                ),
            )


if __name__ == "__main__":
    unittest.main()
