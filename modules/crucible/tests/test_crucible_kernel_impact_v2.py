from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_kernel import KernelValidationError  # noqa: E402
from workbench_crucible_kernel.impact import (  # noqa: E402
    AdmissionPartitionChange,
    DependencyChangeSet,
    DependencyComponentChange,
    DependencyImpactBounds,
    DependencyImpactClosure,
    DependencyManifestSnapshot,
    EvidencePartitionChange,
    SourceGraphPartitionChange,
    SourceGraphRecordChange,
    plan_dependency_impact,
)
from workbench_api.canonical import canonical_json_bytes
from workbench_crucible import load_canonical_record, seal_record, semantic_root_v2
from workbench_crucible.synthetic import (  # noqa: E402
    build_synthetic_publication,
)


def _id(kind: str, label: str) -> str:
    return f"{kind}:sha256:{hashlib.sha256(label.encode('utf-8')).hexdigest()}"


class CrucibleKernelImpactV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.publication = build_synthetic_publication()
        cls.graph_set = next(
            record
            for record in cls.publication.records
            if record.kind == "graph-set-revision"
        )
        graph_set_value = cls.graph_set.to_dict()
        target_graph_ids = {
            item["graph_revision_id"]
            for collection in ("members", "join_graphs", "refinement_graphs")
            for item in graph_set_value[collection]
        }
        graph_records = tuple(
            record
            for record in cls.publication.records
            if record.kind == "graph-revision" and record.id in target_graph_ids
        )
        cls.snapshots = tuple(
            DependencyManifestSnapshot(
                graph_revision_bytes=graph.canonical_bytes,
                dependency_manifest_bytes=cls.publication.records_by_id[
                    graph.to_dict()["dependency_manifest_id"]
                ].canonical_bytes,
            )
            for graph in graph_records
        )
        cls.context_ref_id = graph_records[0].to_dict()["context_ref_id"]
        cls.closure = DependencyImpactClosure(
            cls.graph_set.canonical_bytes,
            cls.snapshots,
        )
        cls.main = next(
            graph
            for graph in graph_records
            if any(
                partition["partition_key"] == "synthetic.main"
                for partition in graph.to_dict()["partitions"]
            )
        )
        cls.refinement = next(
            graph
            for graph in graph_records
            if any(
                partition["partition_key"] == "synthetic.refinement"
                for partition in graph.to_dict()["partitions"]
            )
        )
        cls.main_manifest = cls.publication.records_by_id[
            cls.main.to_dict()["dependency_manifest_id"]
        ]
        cls.refinement_manifest = cls.publication.records_by_id[
            cls.refinement.to_dict()["dependency_manifest_id"]
        ]
        main_footprint = cls.main_manifest.to_dict()["footprints"][0]
        cls.evidence_record_id = main_footprint["evidence_record_ids"][0]
        cls.admission_record_id = main_footprint["admission_record_ids"][0]
        cls.evidence_partition = main_footprint["evidence_partitions"][0]
        cls.admission_partition = main_footprint["admission_partitions"][0]
        component_key = main_footprint["component_keys"][0]
        cls.component = next(
            item
            for item in cls.main_manifest.to_dict()["input_components"]
            if item["component_key"] == component_key
        )

    def assertCode(self, caught: unittest.case._AssertRaisesContext, code: str) -> None:
        self.assertEqual(caught.exception.code, code)

    def plan(self, changes, snapshots=None, *, bounds=DependencyImpactBounds()):
        return plan_dependency_impact(
            changes,
            self.closure
            if snapshots is None
            else DependencyImpactClosure(
                self.graph_set.canonical_bytes,
                snapshots,
            ),
            bounds=bounds,
        )

    def closure_for(self, snapshots):
        ordered = tuple(
            sorted(
                snapshots,
                key=lambda item: load_canonical_record(
                    item.graph_revision_bytes
                ).id,
            )
        )
        graphs = [
            load_canonical_record(item.graph_revision_bytes).to_dict()
            for item in ordered
        ]
        candidate = self.graph_set.to_dict()
        member_template = dict(candidate["members"][0])
        member_alignment = dict(member_template["alignment"])
        auxiliary_template = dict(candidate["refinement_graphs"][0])
        auxiliary_alignment = dict(auxiliary_template["alignment"])

        first = graphs[0]
        member_alignment.update(
            member_context_ref_id=first["context_ref_id"],
            member_evidence_set_revision_ids=first["evidence_set_revision_ids"],
        )
        candidate["members"] = [
            {
                **member_template,
                "alignment": member_alignment,
                "category_id": first["category_id"],
                "graph_revision_id": first["id"],
                "resolution_id": first["resolution_id"],
            }
        ]
        candidate["join_graphs"] = []
        auxiliaries = []
        for graph in graphs[1:]:
            alignment = dict(auxiliary_alignment)
            alignment.update(
                member_context_ref_id=graph["context_ref_id"],
                member_evidence_set_revision_ids=graph["evidence_set_revision_ids"],
            )
            auxiliaries.append(
                {
                    **auxiliary_template,
                    "alignment": alignment,
                    "graph_revision_id": graph["id"],
                }
            )
        candidate["refinement_graphs"] = auxiliaries
        candidate["conflict_record_ids"] = sorted(
            {
                conflict_id
                for graph in graphs
                for conflict_id in graph["conflict_record_ids"]
            }
        )
        candidate.pop("id", None)
        candidate["aggregate_semantic_root"] = ""
        candidate["aggregate_semantic_root"] = semantic_root_v2(
            "graph-set-revision/aggregate",
            {
                key: value
                for key, value in candidate.items()
                if key != "aggregate_semantic_root"
            },
        )
        graph_set = seal_record(candidate)
        return DependencyImpactClosure(graph_set.canonical_bytes, ordered)

    def test_exact_evidence_admission_partition_and_component_matches(self) -> None:
        cases = (
            DependencyChangeSet(
                self.context_ref_id,
                evidence_record_ids=(self.evidence_record_id,),
            ),
            DependencyChangeSet(
                self.context_ref_id,
                admission_record_ids=(self.admission_record_id,),
            ),
            DependencyChangeSet(
                self.context_ref_id,
                evidence_partitions=(
                    EvidencePartitionChange(
                        self.evidence_partition["evidence_set_revision_id"],
                        self.evidence_partition["partition_key"],
                        self.evidence_partition["shard_ordinal"],
                    ),
                ),
            ),
            DependencyChangeSet(
                self.context_ref_id,
                admission_partitions=(
                    AdmissionPartitionChange(
                        self.admission_partition["evidence_set_revision_id"],
                        self.admission_partition["partition_key"],
                        self.admission_partition["shard_ordinal"],
                    ),
                ),
            ),
            DependencyChangeSet(
                self.context_ref_id,
                components=(
                    DependencyComponentChange(
                        self.component["component_kind"],
                        self.component["component_id"],
                    ),
                ),
            ),
        )
        for changes in cases:
            with self.subTest(changes=changes):
                plan = self.plan(changes)
                self.assertEqual(plan.mode, "exact")
                self.assertFalse(plan.requires_full_rebuild)
                self.assertEqual(plan.reason_codes, ())
                self.assertEqual(len(plan.impacted_partitions), 8)
                self.assertRegex(plan.input_closure_sha256, r"^[0-9a-f]{64}$")
                self.assertRegex(plan.impact_root_sha256, r"^[0-9a-f]{64}$")

    def test_source_graph_partition_matches_exact_downstream_footprint(self) -> None:
        changes = DependencyChangeSet(
            self.context_ref_id,
            source_graph_partitions=(
                SourceGraphPartitionChange(
                    "synthetic.input-main",
                    self.main.id,
                    "synthetic.main",
                    0,
                    "node",
                ),
            ),
        )
        plan = self.plan(changes)
        self.assertEqual(plan.mode, "exact")
        self.assertEqual(len(plan.impacted_partitions), 1)
        self.assertEqual(plan.impacted_partitions[0].graph_revision_id, self.refinement.id)
        self.assertEqual(
            plan.impacted_partitions[0].partition_key, "synthetic.refinement"
        )

    def test_source_graph_record_matches_exact_contract_revision_and_record(self) -> None:
        footprint = self.refinement_manifest.to_dict()["footprints"][0]
        source = footprint["source_graph_records"][0]
        bindings = {
            item["graph_input_contract_key"]: item["graph_revision_id"]
            for item in self.refinement_manifest.to_dict()["input_graph_bindings"]
        }
        changes = DependencyChangeSet(
            self.context_ref_id,
            source_graph_records=(
                SourceGraphRecordChange(
                    source["graph_input_contract_key"],
                    bindings[source["graph_input_contract_key"]],
                    source["graph_record_id"],
                ),
            ),
        )
        plan = self.plan(changes)
        self.assertEqual(plan.mode, "exact")
        self.assertEqual(len(plan.impacted_partitions), 1)
        self.assertEqual(
            plan.impacted_partitions[0].partition_key, "synthetic.refinement"
        )

    def test_direct_impact_closes_transitively_across_manifest_graph_inputs(self) -> None:
        refinement = self.refinement_manifest.to_dict()
        footprint = dict(refinement["footprints"][0])
        footprint.update(
            evidence_record_ids=[],
            admission_record_ids=[],
            evidence_partitions=[],
            admission_partitions=[],
        )
        footprint["footprint_root"] = ""
        footprint["footprint_root"] = semantic_root_v2(
            "dependency-manifest/footprint",
            {key: value for key, value in footprint.items() if key != "footprint_root"},
        )
        refinement["footprints"] = [footprint]
        refinement.pop("id")
        refinement["semantic_root"] = ""
        refinement["semantic_root"] = semantic_root_v2(
            "dependency-manifest/aggregate",
            {key: value for key, value in refinement.items() if key != "semantic_root"},
        )
        downstream = seal_record(refinement)
        graph = self.refinement.to_dict()
        graph.pop("id")
        graph["dependency_manifest_id"] = downstream.id
        graph["aggregate_semantic_root"] = ""
        graph["aggregate_semantic_root"] = semantic_root_v2(
            "graph-revision/aggregate",
            {
                key: value
                for key, value in graph.items()
                if key != "aggregate_semantic_root"
            },
        )
        downstream_graph = seal_record(graph)
        snapshots = (
            next(
                item
                for item in self.snapshots
                if item.graph_revision_bytes == self.main.canonical_bytes
            ),
            DependencyManifestSnapshot(
                downstream_graph.canonical_bytes,
                downstream.canonical_bytes,
            ),
        )
        plan = plan_dependency_impact(
            DependencyChangeSet(
                self.context_ref_id,
                evidence_record_ids=(self.evidence_record_id,),
            ),
            self.closure_for(snapshots),
        )
        self.assertEqual(plan.mode, "exact")
        self.assertEqual(len(plan.impacted_partitions), 7)
        self.assertIn(
            (downstream_graph.id, "synthetic.refinement", "refinement-mapping"),
            {
                (item.graph_revision_id, item.partition_key, item.record_kind)
                for item in plan.impacted_partitions
            },
        )

    def test_transitive_closure_uses_one_indexed_pass_per_dependency(self) -> None:
        original = self.refinement_manifest.to_dict()
        graph_template = self.refinement.to_dict()
        upstream_graph = self.main.id
        snapshots = [
            next(
                item
                for item in self.snapshots
                if item.graph_revision_bytes == self.main.canonical_bytes
            )
        ]
        expected_graph_ids: list[str] = []
        for ordinal in range(24):
            manifest = dict(original)
            footprint = dict(manifest["footprints"][0])
            contract_key = f"chain.input.{ordinal:02d}"
            manifest["input_graph_bindings"] = [
                {
                    "graph_input_contract_key": contract_key,
                    "graph_revision_id": upstream_graph,
                }
            ]
            footprint.update(
                evidence_record_ids=[],
                admission_record_ids=[],
                evidence_partitions=[],
                admission_partitions=[],
                source_graph_records=[],
                source_graph_partitions=[
                    {
                        "graph_input_contract_key": contract_key,
                        "graph_revision_id": upstream_graph,
                        "partition_key": (
                            "synthetic.main"
                            if ordinal == 0
                            else f"synthetic.chain.{ordinal - 1:02d}"
                        ),
                        "record_kind": (
                            "node" if ordinal == 0 else "refinement-mapping"
                        ),
                        "shard_ordinal": 0,
                    }
                ],
                output_partitions=[
                    {
                        "partition_key": f"synthetic.chain.{ordinal:02d}",
                        "record_kind": "refinement-mapping",
                        "shard_ordinal": 0,
                    }
                ],
            )
            footprint["footprint_root"] = ""
            footprint["footprint_root"] = semantic_root_v2(
                "dependency-manifest/footprint",
                {
                    key: value
                    for key, value in footprint.items()
                    if key != "footprint_root"
                },
            )
            manifest["footprints"] = [footprint]
            manifest.pop("id", None)
            manifest["semantic_root"] = ""
            manifest["semantic_root"] = semantic_root_v2(
                "dependency-manifest/aggregate",
                {
                    key: value
                    for key, value in manifest.items()
                    if key != "semantic_root"
                },
            )
            sealed_manifest = seal_record(manifest)
            graph = dict(graph_template)
            graph.update(
                dependency_manifest_id=sealed_manifest.id,
                input_graph_bindings=manifest["input_graph_bindings"],
                partitions=[
                    {
                        **graph_template["partitions"][0],
                        "partition_key": f"synthetic.chain.{ordinal:02d}",
                    }
                ],
            )
            graph.pop("id", None)
            graph["aggregate_semantic_root"] = ""
            graph["aggregate_semantic_root"] = semantic_root_v2(
                "graph-revision/aggregate",
                {
                    key: value
                    for key, value in graph.items()
                    if key != "aggregate_semantic_root"
                },
            )
            sealed_graph = seal_record(graph)
            snapshots.append(
                DependencyManifestSnapshot(
                    sealed_graph.canonical_bytes,
                    sealed_manifest.canonical_bytes,
                )
            )
            expected_graph_ids.append(sealed_graph.id)
            upstream_graph = sealed_graph.id

        plan = plan_dependency_impact(
            DependencyChangeSet(
                self.context_ref_id,
                evidence_record_ids=(self.evidence_record_id,),
            ),
            self.closure_for(tuple(reversed(snapshots))),
        )
        self.assertEqual("exact", plan.mode)
        self.assertTrue(set(expected_graph_ids).issubset(
            {item.graph_revision_id for item in plan.impacted_partitions}
        ))

    def test_permutation_changes_neither_plan_order_nor_hashes(self) -> None:
        components = tuple(
            DependencyComponentChange(item["component_kind"], item["component_id"])
            for item in self.main_manifest.to_dict()["input_components"][:2]
        )
        forward = self.plan(
            DependencyChangeSet(self.context_ref_id, components=components)
        )
        reverse = self.plan(
            DependencyChangeSet(self.context_ref_id, components=components[::-1]),
            self.snapshots[::-1],
        )
        self.assertEqual(forward, reverse)
        self.assertEqual(forward.to_dict(), reverse.to_dict())

    def test_graph_set_identity_is_bound_into_plan_and_incomplete_closure_rejects(self) -> None:
        changes = DependencyChangeSet(self.context_ref_id)
        baseline = self.plan(changes)
        candidate = self.graph_set.to_dict()
        candidate["purpose_id"] = "synthetic.alternate-conformance"
        candidate.pop("id", None)
        candidate["aggregate_semantic_root"] = ""
        candidate["aggregate_semantic_root"] = semantic_root_v2(
            "graph-set-revision/aggregate",
            {
                key: value
                for key, value in candidate.items()
                if key != "aggregate_semantic_root"
            },
        )
        alternate_graph_set = seal_record(candidate)
        alternate = plan_dependency_impact(
            changes,
            DependencyImpactClosure(
                alternate_graph_set.canonical_bytes,
                self.snapshots,
            ),
        )
        self.assertNotEqual(
            baseline.graph_set_revision_id,
            alternate.graph_set_revision_id,
        )
        self.assertNotEqual(
            baseline.input_closure_sha256,
            alternate.input_closure_sha256,
        )
        self.assertNotEqual(baseline.impact_root_sha256, alternate.impact_root_sha256)

        with self.assertRaises(KernelValidationError) as caught:
            plan_dependency_impact(
                changes,
                DependencyImpactClosure(
                    self.graph_set.canonical_bytes,
                    self.snapshots[:-1],
                ),
            )
        self.assertCode(caught, "kernel.impact-incomplete-closure")

    def test_unknown_or_unmatched_change_falls_back_to_every_output(self) -> None:
        exact = self.plan(DependencyChangeSet(self.context_ref_id))
        self.assertEqual(exact.mode, "exact")
        self.assertEqual(exact.impacted_partitions, ())

        unknown = self.plan(DependencyChangeSet(self.context_ref_id, has_unknown_changes=True))
        unmatched = self.plan(
            DependencyChangeSet(
                self.context_ref_id,
                evidence_record_ids=(_id("evidence-record", "not-retained"),),
            )
        )
        self.assertEqual(unknown.mode, "full-rebuild")
        self.assertEqual(unknown.reason_codes, ("unknown-change",))
        self.assertEqual(unmatched.mode, "full-rebuild")
        self.assertEqual(unmatched.reason_codes, ("unmatched-change",))
        self.assertEqual(len(unknown.impacted_partitions), 8)
        self.assertEqual(unknown.impacted_partitions, unmatched.impacted_partitions)

    def test_wrong_types_duplicates_and_context_fail_with_stable_codes(self) -> None:
        with self.assertRaises(KernelValidationError) as caught:
            plan_dependency_impact(  # type: ignore[arg-type]
                replace(
                    DependencyChangeSet(self.context_ref_id),
                    evidence_record_ids=[self.evidence_record_id],
                ),
                self.closure,
            )
        self.assertCode(caught, "kernel.impact-invalid-change-set")

        duplicate = DependencyComponentChange(
            self.component["component_kind"], self.component["component_id"]
        )
        with self.assertRaises(KernelValidationError) as caught:
            plan_dependency_impact(
                DependencyChangeSet(
                    self.context_ref_id, components=(duplicate, duplicate)
                ),
                self.closure,
            )
        self.assertCode(caught, "kernel.impact-duplicate-change")

        with self.assertRaises(KernelValidationError) as caught:
            plan_dependency_impact(
                DependencyChangeSet(
                    _id("context-ref", "different-context")
                ),
                self.closure,
            )
        self.assertCode(caught, "kernel.impact-context-mismatch")

        with self.assertRaises(KernelValidationError) as caught:
            plan_dependency_impact(  # type: ignore[arg-type]
                replace(
                    DependencyChangeSet(self.context_ref_id),
                    has_unknown_changes=1,
                ),
                self.closure,
            )
        self.assertCode(caught, "kernel.impact-invalid-change-set")

    def test_manifest_types_canonicality_and_duplicates_fail_closed(self) -> None:
        with self.assertRaises(KernelValidationError) as caught:
            plan_dependency_impact(  # type: ignore[arg-type]
                DependencyChangeSet(self.context_ref_id),
                DependencyImpactClosure(
                    self.graph_set.canonical_bytes,
                    list(self.snapshots),  # type: ignore[arg-type]
                ),
            )
        self.assertCode(caught, "kernel.impact-invalid-manifests")

        first = self.snapshots[0]
        with self.assertRaises(KernelValidationError) as caught:
            plan_dependency_impact(
                DependencyChangeSet(self.context_ref_id),
                DependencyImpactClosure(
                    self.graph_set.canonical_bytes,
                    (
                    DependencyManifestSnapshot(
                        first.dependency_manifest_bytes,
                        first.dependency_manifest_bytes,
                    ),
                    ),
                ),
            )
        self.assertCode(caught, "kernel.impact-wrong-graph-kind")

        with self.assertRaises(KernelValidationError) as caught:
            plan_dependency_impact(
                DependencyChangeSet(self.context_ref_id),
                DependencyImpactClosure(
                    self.graph_set.canonical_bytes,
                    (
                    DependencyManifestSnapshot(
                        first.graph_revision_bytes,
                        self.snapshots[1].dependency_manifest_bytes,
                    ),
                    ),
                ),
            )
        self.assertCode(caught, "kernel.impact-graph-manifest-mismatch")

        with self.assertRaises(KernelValidationError) as caught:
            plan_dependency_impact(
                DependencyChangeSet(self.context_ref_id),
                DependencyImpactClosure(
                    self.graph_set.canonical_bytes,
                    (
                    DependencyManifestSnapshot(
                        first.graph_revision_bytes,
                        bytearray(first.dependency_manifest_bytes),  # type: ignore[arg-type]
                    ),
                    ),
                ),
            )
        self.assertCode(caught, "kernel.impact-invalid-manifest")

        with self.assertRaises(KernelValidationError) as caught:
            plan_dependency_impact(
                DependencyChangeSet(self.context_ref_id),
                DependencyImpactClosure(
                    self.graph_set.canonical_bytes,
                    (
                    DependencyManifestSnapshot(
                        first.graph_revision_bytes,
                        first.dependency_manifest_bytes + b"\n",
                    ),
                    ),
                ),
            )
        self.assertCode(caught, "kernel.impact-invalid-manifest")

        with self.assertRaises(KernelValidationError) as caught:
            plan_dependency_impact(
                DependencyChangeSet(self.context_ref_id),
                DependencyImpactClosure(
                    self.graph_set.canonical_bytes,
                    (first, first),
                ),
            )
        self.assertCode(caught, "kernel.impact-duplicate-graph")

    def test_all_resource_bounds_preflight_with_stable_diagnostics(self) -> None:
        cases = (
            (
                DependencyChangeSet(self.context_ref_id),
                self.closure,
                replace(
                    DependencyImpactBounds(),
                    maximum_manifests=len(self.snapshots) - 1,
                ),
                "kernel.impact-manifest-bound",
            ),
            (
                DependencyChangeSet(
                    self.context_ref_id,
                    evidence_record_ids=(self.evidence_record_id,),
                ),
                self.closure,
                replace(DependencyImpactBounds(), maximum_changes=0),
                "kernel.impact-change-bound",
            ),
            (
                DependencyChangeSet(self.context_ref_id),
                self.closure,
                replace(DependencyImpactBounds(), maximum_manifest_bytes=1),
                "kernel.impact-manifest-byte-bound",
            ),
            (
                DependencyChangeSet(self.context_ref_id),
                self.closure,
                replace(DependencyImpactBounds(), maximum_footprints=0),
                "kernel.impact-footprint-bound",
            ),
            (
                DependencyChangeSet(self.context_ref_id),
                self.closure,
                replace(DependencyImpactBounds(), maximum_output_partitions=0),
                "kernel.impact-output-bound",
            ),
            (
                DependencyChangeSet(self.context_ref_id),
                self.closure,
                replace(
                    DependencyImpactBounds(),
                    maximum_change_projection_bytes=1,
                ),
                "kernel.impact-change-projection-byte-bound",
            ),
        )
        for changes, manifests, bounds, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(KernelValidationError) as caught:
                    plan_dependency_impact(changes, manifests, bounds=bounds)
                self.assertCode(caught, code)

        with self.assertRaises(KernelValidationError) as caught:
            plan_dependency_impact(
                DependencyChangeSet(self.context_ref_id),
                self.closure,
                bounds=replace(DependencyImpactBounds(), maximum_manifests=True),
            )
        self.assertCode(caught, "kernel.impact-invalid-bounds")

        with self.assertRaises(KernelValidationError) as caught:
            plan_dependency_impact(
                DependencyChangeSet(self.context_ref_id),
                self.closure,
                bounds=replace(
                    DependencyImpactBounds(),
                    maximum_change_projection_bytes=True,
                ),
            )
        self.assertCode(caught, "kernel.impact-invalid-bounds")

    def test_aggregate_long_text_change_projection_is_byte_bounded(self) -> None:
        first = SourceGraphRecordChange(
            "a" + "\U0001f9f1" * 8191,
            self.main.id,
            _id("graph-record", "long-change-a"),
        )
        second = SourceGraphRecordChange(
            "b" + "\U0001f9f1" * 8191,
            self.main.id,
            _id("graph-record", "long-change-b"),
        )
        single_changes = DependencyChangeSet(
            self.context_ref_id,
            source_graph_records=(first,),
        )
        single_projection_bytes = len(canonical_json_bytes({
            "admission_partitions": [],
            "admission_record_ids": [],
            "components": [],
            "context_ref_id": self.context_ref_id,
            "evidence_partitions": [],
            "evidence_record_ids": [],
            "has_unknown_changes": False,
            "source_graph_partitions": [],
            "source_graph_records": [
                {
                    "graph_input_contract_key": first.graph_input_contract_key,
                    "graph_record_id": first.graph_record_id,
                    "graph_revision_id": first.graph_revision_id,
                }
            ],
        }))
        bounds = replace(
            DependencyImpactBounds(),
            maximum_change_projection_bytes=single_projection_bytes,
        )

        baseline = self.plan(single_changes)
        at_limit = self.plan(single_changes, bounds=bounds)
        self.assertEqual(at_limit, baseline)

        with self.assertRaises(KernelValidationError) as caught:
            self.plan(
                DependencyChangeSet(
                    self.context_ref_id,
                    source_graph_records=(first, second),
                ),
                bounds=bounds,
            )
        self.assertCode(caught, "kernel.impact-change-projection-byte-bound")
        self.assertEqual(caught.exception.path, "/changes")

        with self.assertRaises(KernelValidationError) as caught:
            self.plan(
                single_changes,
                bounds=replace(
                    bounds,
                    maximum_change_projection_bytes=single_projection_bytes - 1,
                ),
            )
        self.assertCode(caught, "kernel.impact-change-projection-byte-bound")

    def test_change_values_are_immutable_and_results_are_detached(self) -> None:
        change = EvidencePartitionChange(
            self.evidence_partition["evidence_set_revision_id"],
            self.evidence_partition["partition_key"],
            self.evidence_partition["shard_ordinal"],
        )
        with self.assertRaises(FrozenInstanceError):
            change.partition_key = "changed"  # type: ignore[misc]

        plan = self.plan(
            DependencyChangeSet(self.context_ref_id, evidence_partitions=(change,))
        )
        first = plan.to_dict()
        first["impacted_partitions"].clear()
        self.assertEqual(len(plan.impacted_partitions), 8)
        self.assertEqual(len(plan.to_dict()["impacted_partitions"]), 8)


if __name__ == "__main__":
    unittest.main()
