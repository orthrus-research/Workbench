from __future__ import annotations

from dataclasses import replace
import base64
import hashlib
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_materializer import (  # noqa: E402
    BUILTIN_GRAPH_HANDLER_ID,
    BUILTIN_SYNTHETIC_BEFORE_CAPABILITY_ID,
    BUILTIN_SYNTHETIC_BEFORE_HANDLER_ID,
    BUILTIN_WORLDGEN_CAPTURE_HEALTH_CAPABILITY_ID,
    BUILTIN_WORLDGEN_GENERATIVE_CAPABILITY_ID,
    BUILTIN_WORLDGEN_OCCURRENCE_CAPABILITY_ID,
    BUILTIN_WORLDGEN_REALIZED_CAPABILITY_ID,
    BUILTIN_WORLDGEN_STABILITY_CAPABILITY_ID,
    MaterializationError,
    MaterializationInput,
    RecipeExecutionBounds,
    RecipeExecutionInput,
    RecipeImplementationRegistration,
    RecipeSemanticOutput,
    WORLDGEN_GRAPH_BUNDLE_VALUE_KIND,
    execute_materialization,
    qualify_recipe_execution,
    seal_recipe_execution_binding,
    snapshot_recipe_implementation_registry,
)
from workbench_crucible_kernel import (  # noqa: E402
    DependencyChangeSet,
    DependencyImpactClosure,
    DependencyManifestSnapshot,
    plan_dependency_impact,
    verify_partition_assembly_equivalence,
)
from workbench_api.canonical import canonical_json_bytes, content_id, parse_canonical_json
from workbench_crucible import seal_record, semantic_root_v2
from workbench_crucible.synthetic import (  # noqa: E402
    build_synthetic_publication,
)


SUBJECT_SCHEMA_ID = (
    "workbench://schemas/crucible-fixtures/"
    "synthetic-subject-identity-v1.schema.json"
)


def _reseed(value: dict, **changes):
    candidate = dict(value)
    candidate.pop("id", None)
    candidate.update(changes)
    return seal_record(candidate)


def _manifest(candidate: dict, field: str, domain: str):
    candidate = dict(candidate)
    candidate.pop("id", None)
    candidate[field] = ""
    projection = dict(candidate)
    projection.pop(field)
    candidate[field] = semantic_root_v2(domain, projection)
    return seal_record(candidate)


class CrucibleMaterializerV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.publication = build_synthetic_publication()
        recipe = cls.publication.records_by_id[
            cls.publication.aliases["materialization-recipe"]
        ]
        recipe_value = recipe.to_dict()
        direct_step = next(
            item
            for item in recipe_value["derivation_steps"]
            if item["step_key"] == "synthetic.direct"
        )
        graph = cls.publication.records_by_id[
            cls.publication.aliases["graph-revision"]
        ].to_dict()
        binding = seal_recipe_execution_binding(
            recipe,
            derivation_step_id=direct_step["derivation_step_id"],
            output_contract_key="synthetic.main",
            handler_id=BUILTIN_SYNTHETIC_BEFORE_HANDLER_ID,
            capability_id=BUILTIN_SYNTHETIC_BEFORE_CAPABILITY_ID,
            subject_identity_schema_id=SUBJECT_SCHEMA_ID,
            materializer_component_id=graph["materializer"]["component_id"],
            materializer_implementation_id=graph["materializer"][
                "implementation_id"
            ],
            custodian_component_id=graph["custodian"]["component_id"],
            custodian_implementation_id=graph["custodian"]["implementation_id"],
            bounds=RecipeExecutionBounds(1024 * 1024, 4096, 64 * 1024, 64, 30),
        )
        cls.registration = RecipeImplementationRegistration(binding)

    def setUp(self) -> None:
        p = self.publication
        self.source = MaterializationInput(
            recipe=p.records_by_id[p.aliases["materialization-recipe"]],
            evidence_set_revision=p.records_by_id[
                p.aliases["evidence-set-revision"]
            ],
            evidence_partition_descriptor=p.records_by_id[
                p.aliases["evidence-shard"]
            ],
            evidence_partition_bytes=p.blobs_by_id[
                p.record("evidence-shard")["object_id"]
            ],
            admission_partition_descriptor=p.records_by_id[
                p.aliases["admission-shard"]
            ],
            admission_partition_bytes=p.blobs_by_id[
                p.record("admission-shard")["object_id"]
            ],
            evidence=p.records_by_id[p.aliases["evidence-record"]],
            admission=p.records_by_id[p.aliases["admission-record"]],
            payload_descriptor=p.records_by_id[p.aliases["evidence-payload"]],
            payload_bytes=p.blobs_by_id[
                p.record("evidence-payload")["object_id"]
            ],
        )

    def registration_with_binding(self, **changes):
        return RecipeImplementationRegistration(
            replace(self.registration.execution_binding, **changes)
        )

    def execute(
        self,
        source=None,
        registration=None,
        *,
        registry=None,
        prior_shards=(),
    ):
        source = self.source if source is None else source
        registration = self.registration if registration is None else registration
        if registry is None:
            registry = {registration.implementation_id: registration}
        return execute_materialization(
            source,
            registrations=registry,
            prior_shards=prior_shards,
        )

    def assertCode(self, caught, code: str) -> None:
        self.assertEqual(code, caught.exception.code)

    @staticmethod
    def identity(result):
        return (
            tuple(record.canonical_bytes for record in result.graph_records),
            tuple(record.canonical_bytes for record in result.object_descriptors),
            tuple((blob.object_id, blob.data) for blob in result.blobs),
            canonical_json_bytes(result.assembly.graph_revision_fields()),
            tuple(
                (shard.object_descriptor.canonical_bytes, shard.ndjson_bytes)
                for shard in result.assembly.shards
            ),
            canonical_json_bytes(result.publication_fields()),
        )

    def _descriptor_for_row(self, template, row, *, admission: bool):
        raw = row.canonical_bytes + b"\n"
        digest = hashlib.sha256(raw).hexdigest()
        key = row.to_dict()["candidate_record_id"] if admission else row.id
        descriptor = _reseed(
            template.to_dict(),
            object_id=f"workbench-blob-v2:sha256:{digest}",
            sha256=digest,
            byte_length=len(raw),
            canonical_item_count=1,
            minimum_record_key=key,
            maximum_record_key=key,
        )
        return descriptor, raw

    def _with_effective_pair(self, evidence, admission):
        evidence_descriptor, evidence_raw = self._descriptor_for_row(
            self.source.evidence_partition_descriptor, evidence, admission=False
        )
        admission_descriptor, admission_raw = self._descriptor_for_row(
            self.source.admission_partition_descriptor, admission, admission=True
        )
        revision = self.source.evidence_set_revision.to_dict()
        evidence_partition = dict(revision["evidence_partitions"][0])
        evidence_partition.update(
            {
                "object_descriptor_id": evidence_descriptor.id,
                "minimum_record_key": evidence.id,
                "maximum_record_key": evidence.id,
                "semantic_root": semantic_root_v2(
                    "evidence-set-revision/effective-evidence/partition",
                    {
                        "partition_key": evidence_partition["partition_key"],
                        "record_ids": [evidence.id],
                        "shard_ordinal": evidence_partition["shard_ordinal"],
                    },
                ),
            }
        )
        admission_partition = dict(revision["admission_partitions"][0])
        candidate_id = admission.to_dict()["candidate_record_id"]
        admission_partition.update(
            {
                "object_descriptor_id": admission_descriptor.id,
                "minimum_record_key": candidate_id,
                "maximum_record_key": candidate_id,
                "semantic_root": semantic_root_v2(
                    "evidence-set-revision/effective-admissions/partition",
                    {
                        "partition_key": admission_partition["partition_key"],
                        "record_ids": [admission.id],
                        "shard_ordinal": admission_partition["shard_ordinal"],
                    },
                ),
            }
        )

        def summary(item):
            return {
                key: item[key]
                for key in (
                    "partition_key",
                    "record_count",
                    "semantic_root",
                    "shard_ordinal",
                )
            }

        revision.update(
            {
                "evidence_partitions": [evidence_partition],
                "admission_partitions": [admission_partition],
                "effective_evidence_root": semantic_root_v2(
                    "evidence-set-revision/effective-evidence",
                    [summary(evidence_partition)],
                ),
                "effective_admission_root": semantic_root_v2(
                    "evidence-set-revision/effective-admissions",
                    [summary(admission_partition)],
                ),
            }
        )
        revision = _manifest(
            revision, "semantic_root", "evidence-set-revision/aggregate"
        )
        return replace(
            self.source,
            evidence_set_revision=revision,
            evidence_partition_descriptor=evidence_descriptor,
            evidence_partition_bytes=evidence_raw,
            admission_partition_descriptor=admission_descriptor,
            admission_partition_bytes=admission_raw,
            evidence=evidence,
            admission=admission,
        )

    @staticmethod
    def _source_artifact(rows, *, name="graph-source.ndjson"):
        raw = b"".join(canonical_json_bytes(row) + b"\n" for row in rows)
        return {
            "bytes_base64": base64.b64encode(raw).decode("ascii"),
            "name": name,
            "sha256": hashlib.sha256(raw).hexdigest(),
        }

    @staticmethod
    def _branch(*, properties=(), emit_evidence_links=True, prefix="worldgen"):
        return {
            "emit_evidence_links": emit_evidence_links,
            "partition_key": f"{prefix}.main",
            "properties": list(properties),
            "relations": [
                {
                    "direction": "directed",
                    "logical_key": f"edge:{prefix}-alpha-before-beta",
                    "predicate_id": f"{prefix}.before",
                    "source_row_ids": ["row.alpha", "row.beta"],
                    "source_subject_key": "subject.alpha",
                    "target_subject_key": "subject.beta",
                }
            ],
            "subjects": [
                {
                    "identity": {
                        "coordinates": [0, 64, 0],
                        "name": "alpha",
                        "profile": prefix,
                    },
                    "logical_key": f"node:{prefix}-alpha",
                    "source_row_ids": ["row.alpha"],
                    "subject_key": "subject.alpha",
                },
                {
                    "identity": [prefix, "beta", {"ordinal": 1}],
                    "logical_key": f"node:{prefix}-beta",
                    "source_row_ids": ["row.beta"],
                    "subject_key": "subject.beta",
                },
            ],
        }

    def _worldgen_payload(self):
        branches = (
            "capture_health",
            "generative",
            "occurrence",
            "realized",
            "stability",
        )
        return {
            "branches": {
                branch: self._branch(prefix=f"worldgen-{branch.replace('_', '-')}")
                for branch in branches
            },
            "source_artifacts": [
                self._source_artifact(
                    [
                        {"row_id": "row.alpha", "value": "alpha"},
                        {"row_id": "row.beta", "value": "beta"},
                    ]
                )
            ],
            "value_kind": WORLDGEN_GRAPH_BUNDLE_VALUE_KIND,
        }

    def _graph_recipe(self):
        value = self.source.recipe.to_dict()
        value.pop("id")
        for step in value["derivation_steps"]:
            if step["step_key"] != "synthetic.direct":
                continue
            for item in step["input_contracts"]:
                for contract in item["source_contracts"]:
                    if (
                        contract["source_kind"] == "local-output"
                        and contract["contract_key"] == "synthetic.main"
                    ):
                        contract["record_kinds"] = sorted(
                            {*contract["record_kinds"], "edge"},
                            key=lambda item: item.encode("utf-8"),
                        )
            body = dict(step)
            body.pop("derivation_step_id")
            step["derivation_step_id"] = content_id("derivation-step", body)
        return seal_record(value)

    def _source_with_payload(self, payload):
        raw = canonical_json_bytes(payload)
        digest = hashlib.sha256(raw).hexdigest()
        descriptor = _reseed(
            self.source.payload_descriptor.to_dict(),
            object_id=f"workbench-blob-v2:sha256:{digest}",
            sha256=digest,
            byte_length=len(raw),
        )
        evidence = _reseed(
            self.source.evidence.to_dict(),
            payload_object_descriptor_id=descriptor.id,
        )
        admission = _reseed(
            self.source.admission.to_dict(), candidate_record_id=evidence.id
        )
        return replace(
            self._with_effective_pair(evidence, admission),
            recipe=self._graph_recipe(),
            payload_descriptor=descriptor,
            payload_bytes=raw,
        )

    def _graph_registration(self, recipe, capability_id):
        recipe_value = recipe.to_dict()
        direct_step = next(
            item
            for item in recipe_value["derivation_steps"]
            if item["step_key"] == "synthetic.direct"
        )
        graph = self.publication.records_by_id[
            self.publication.aliases["graph-revision"]
        ].to_dict()
        return RecipeImplementationRegistration(
            seal_recipe_execution_binding(
                recipe,
                derivation_step_id=direct_step["derivation_step_id"],
                output_contract_key="synthetic.main",
                handler_id=BUILTIN_GRAPH_HANDLER_ID,
                capability_id=capability_id,
                subject_identity_schema_id=SUBJECT_SCHEMA_ID,
                materializer_component_id=graph["materializer"]["component_id"],
                materializer_implementation_id=graph["materializer"][
                    "implementation_id"
                ],
                custodian_component_id=graph["custodian"]["component_id"],
                custodian_implementation_id=graph["custodian"][
                    "implementation_id"
                ],
                bounds=RecipeExecutionBounds(
                    1024 * 1024, 64 * 1024, 64 * 1024, 64, 30
                ),
            )
        )

    def _execute_graph(self, payload, capability_id):
        source = self._source_with_payload(payload)
        registration = self._graph_registration(
            source.recipe, capability_id
        )
        return self.execute(source, registration), source, registration

    def test_graph_handler_alias_preserves_installed_identity(self) -> None:
        self.assertEqual(
            BUILTIN_SYNTHETIC_BEFORE_HANDLER_ID,
            BUILTIN_GRAPH_HANDLER_ID,
        )
        self.assertEqual(
            "handler:sha256:"
            "fd775d1648cd37901564e2af20f1c590e82b4a79e4b90cca0c278011a5b3a6ae",
            BUILTIN_GRAPH_HANDLER_ID,
        )

    def test_computes_exact_two_nodes_one_edge_and_k01_shards(self) -> None:
        result = self.execute()
        expected_ids = [
            self.publication.aliases["graph-record:node"],
            self.publication.aliases["graph-record:node-beta"],
            self.publication.aliases["graph-record:edge"],
        ]
        self.assertEqual(expected_ids, [record.id for record in result.graph_records])
        self.assertEqual(
            [
                "graph-record:sha256:cc229907b1ce215d4c8b8bd4a47948b714d15cfe627ddcaccb721d89666e4a7c",
                "graph-record:sha256:863f2b30d5135dd12dcf06d29e2f8e233a0b16fd4d1a555e2e901b00a51d76de",
                "graph-record:sha256:ca3cdc2d764c5e4d6a0aece6ea7c7bc1303374ebc1f66db654cb17a8b77558f6",
            ],
            [record.id for record in result.graph_records],
        )
        self.assertEqual(
            [
                self.publication.aliases["subject-identity:alpha"],
                self.publication.aliases["subject-identity:beta"],
            ],
            [record.id for record in result.object_descriptors],
        )
        self.assertEqual(
            [("synthetic.main", "node", 2), ("synthetic.main", "edge", 1)],
            [
                (
                    shard.coordinate.partition_key,
                    shard.coordinate.record_kind,
                    len(shard.record_ids),
                )
                for shard in result.assembly.shards
            ],
        )
        self.assertEqual(
            [
                self.publication.aliases["graph-shard:node"],
                self.publication.aliases["graph-shard:edge"],
            ],
            [shard.object_descriptor.id for shard in result.assembly.shards],
        )
        self.assertEqual(
            [
                "object-descriptor:sha256:1df30a6b07f33c0f2c3d462593311b789bf9bd8d8ddf9b7ca05a078e273de4f5",
                "object-descriptor:sha256:a5ebe2d7318460421c3d65c9f8dba28513a560f552931e8412886eb8cfb91468",
            ],
            [shard.object_descriptor.id for shard in result.assembly.shards],
        )
        edge = result.graph_records[2].to_dict()
        self.assertEqual("synthetic.before", edge["body"]["predicate_id"])
        self.assertEqual(
            {result.graph_records[0].id, result.graph_records[1].id},
            {
                item["graph_record_id"]
                for item in edge["source_graph_inputs"]
            },
        )

    def test_prior_shards_are_only_exact_cache_hints_after_fresh_execution(self) -> None:
        clean = self.execute()
        retained_graph = self.publication.records_by_id[
            self.publication.aliases["graph-revision"]
        ]
        retained_graph_set = next(
            record
            for record in self.publication.records
            if record.kind == "graph-set-revision"
        )
        graph_set_value = retained_graph_set.to_dict()
        retained_graph_ids = {
            item["graph_revision_id"]
            for collection in ("members", "join_graphs", "refinement_graphs")
            for item in graph_set_value[collection]
        }
        retained_snapshots = tuple(
            DependencyManifestSnapshot(
                graph.canonical_bytes,
                self.publication.records_by_id[
                    graph.to_dict()["dependency_manifest_id"]
                ].canonical_bytes,
            )
            for graph in self.publication.records
            if graph.kind == "graph-revision" and graph.id in retained_graph_ids
        )
        impact = plan_dependency_impact(
            DependencyChangeSet(
                clean.context_ref_id,
                evidence_record_ids=(clean.evidence_record_id,),
            ),
            DependencyImpactClosure(
                retained_graph_set.canonical_bytes,
                retained_snapshots,
            ),
        )
        self.assertEqual("exact", impact.mode)
        self.assertTrue(
            {
                (
                    shard.coordinate.partition_key,
                    shard.coordinate.shard_ordinal,
                    shard.coordinate.record_kind,
                )
                for shard in clean.assembly.shards
            }.issubset(
                {
                    (
                        item.partition_key,
                        item.shard_ordinal,
                        item.record_kind,
                    )
                    for item in impact.impacted_partitions
                }
            )
        )
        replay = self.execute(prior_shards=clean.assembly.shards)
        self.assertEqual(self.identity(clean), self.identity(replay))
        replay_receipt = verify_partition_assembly_equivalence(
            clean.assembly, replay.assembly
        )
        self.assertEqual(
            tuple(shard.coordinate for shard in clean.assembly.shards),
            replay_receipt.reused_coordinates,
        )

        changed_payload = canonical_json_bytes(
            {
                "observation": "alpha-before-gamma",
                "ordinal": 0,
                "value_kind": "evidence-payload",
            }
        )
        digest = hashlib.sha256(changed_payload).hexdigest()
        descriptor = _reseed(
            self.source.payload_descriptor.to_dict(),
            object_id=f"workbench-blob-v2:sha256:{digest}",
            sha256=digest,
            byte_length=len(changed_payload),
        )
        evidence = _reseed(
            self.source.evidence.to_dict(),
            payload_object_descriptor_id=descriptor.id,
        )
        admission = _reseed(
            self.source.admission.to_dict(), candidate_record_id=evidence.id
        )
        changed_source = replace(
            self._with_effective_pair(evidence, admission),
            payload_descriptor=descriptor,
            payload_bytes=changed_payload,
        )
        changed_clean = self.execute(changed_source)
        changed_incremental = self.execute(
            changed_source, prior_shards=clean.assembly.shards
        )
        self.assertEqual(
            self.identity(changed_clean), self.identity(changed_incremental)
        )
        changed_receipt = verify_partition_assembly_equivalence(
            changed_clean.assembly, changed_incremental.assembly
        )
        self.assertEqual((), changed_incremental.assembly.reused_coordinates)
        self.assertEqual((), changed_receipt.reused_coordinates)
        self.assertNotEqual(self.identity(clean), self.identity(changed_clean))

    def test_worldgen_bundle_selects_five_closed_owner_categories(self) -> None:
        capabilities = {
            BUILTIN_WORLDGEN_CAPTURE_HEALTH_CAPABILITY_ID: "capture_health",
            BUILTIN_WORLDGEN_GENERATIVE_CAPABILITY_ID: "generative",
            BUILTIN_WORLDGEN_OCCURRENCE_CAPABILITY_ID: "occurrence",
            BUILTIN_WORLDGEN_REALIZED_CAPABILITY_ID: "realized",
            BUILTIN_WORLDGEN_STABILITY_CAPABILITY_ID: "stability",
        }
        payload = self._worldgen_payload()
        identities = {}
        for capability_id, branch in capabilities.items():
            with self.subTest(branch=branch):
                result, source, _ = self._execute_graph(payload, capability_id)
                self.assertEqual(6, len(result.graph_records))
                self.assertEqual(
                    {f"worldgen-{branch.replace('_', '-')}.main"},
                    {shard.coordinate.partition_key for shard in result.assembly.shards},
                )
                self.assertEqual(source.evidence.id, result.evidence_record_id)
                identities[branch] = self.identity(result)
        self.assertEqual(5, len(set(identities.values())))

        malformed = parse_canonical_json(canonical_json_bytes(payload))
        malformed["branches"].pop("stability")
        with self.assertRaises(MaterializationError) as caught:
            self._execute_graph(
                malformed, BUILTIN_WORLDGEN_OCCURRENCE_CAPABILITY_ID
            )
        self.assertCode(caught, "materializer.implementation-failed")

    def test_worldgen_artifact_citations_order_and_bounds_fail_closed(self) -> None:
        valid = self._worldgen_payload()
        cases = []

        wrong_hash = parse_canonical_json(canonical_json_bytes(valid))
        wrong_hash["source_artifacts"][0]["sha256"] = "0" * 64
        cases.append(("artifact-hash", wrong_hash))

        missing_row = parse_canonical_json(canonical_json_bytes(valid))
        missing_row["branches"]["occurrence"]["subjects"][0]["source_row_ids"] = [
            "row.missing"
        ]
        cases.append(("missing-row", missing_row))

        unsorted_rows = parse_canonical_json(canonical_json_bytes(valid))
        unsorted_rows["branches"]["occurrence"]["relations"][0][
            "source_row_ids"
        ] = [
            "row.beta",
            "row.alpha",
        ]
        cases.append(("row-order", unsorted_rows))

        over_record_bound = parse_canonical_json(canonical_json_bytes(valid))
        occurrence = over_record_bound["branches"]["occurrence"]
        occurrence["relations"] = []
        occurrence["subjects"] = [
            {
                "identity": {"ordinal": index},
                "logical_key": f"node:bounded-{index:02d}",
                "source_row_ids": ["row.alpha"],
                "subject_key": f"subject.{index:02d}",
            }
            for index in range(17)
        ]
        cases.append(("record-bound", over_record_bound))

        duplicate_identity_rows = parse_canonical_json(canonical_json_bytes(valid))
        duplicate_identity_rows["source_artifacts"] = [
            self._source_artifact(
                [
                    {"row_id": "row.alpha", "value": 1},
                    {"row_id": "row.alpha", "value": 2},
                ]
            )
        ]
        cases.append(("duplicate-row", duplicate_identity_rows))

        both_identity_fields = parse_canonical_json(canonical_json_bytes(valid))
        both_identity_fields["source_artifacts"] = [
            self._source_artifact(
                [{"id": "row.alpha", "row_id": "row.alpha", "value": 1}]
            )
        ]
        cases.append(("ambiguous-row-id", both_identity_fields))

        for label, payload in cases:
            with self.subTest(label=label), self.assertRaises(
                MaterializationError
            ) as caught:
                self._execute_graph(
                    payload, BUILTIN_WORLDGEN_OCCURRENCE_CAPABILITY_ID
                )
            self.assertCode(caught, "materializer.implementation-failed")

        oversized = parse_canonical_json(canonical_json_bytes(valid))
        oversized["branches"]["occurrence"]["subjects"][0]["identity"] = (
            "x" * (64 * 1024)
        )
        with self.assertRaises(MaterializationError) as caught:
            self._execute_graph(
                oversized, BUILTIN_WORLDGEN_OCCURRENCE_CAPABILITY_ID
            )
        self.assertCode(caught, "materializer.payload-bound")

    def test_repeat_and_registry_snapshot_preserve_every_identity(self) -> None:
        expected = self.identity(self.execute())
        binding = self.registration.execution_binding
        dummy = replace(
            binding,
            execution_binding_id="",
            recipe_id="materialization-recipe:sha256:" + "1" * 64,
            implementation_id="implementation:sha256:" + "2" * 64,
            canonical_bytes=b"",
        )
        dummy_value = dummy.to_dict()
        dummy_value.pop("execution_binding_id")
        dummy = replace(
            dummy,
            execution_binding_id=content_id(
                "recipe-execution-binding", dummy_value
            ),
        )
        dummy = replace(dummy, canonical_bytes=canonical_json_bytes(dummy.to_dict()))
        dummy_registration = RecipeImplementationRegistration(dummy)
        variants = (
            {
                dummy.implementation_id: dummy_registration,
                binding.implementation_id: self.registration,
            },
            {
                binding.implementation_id: self.registration,
                dummy.implementation_id: dummy_registration,
            },
        )
        first_root = snapshot_recipe_implementation_registry(variants[0]).registry_root
        for registry in variants:
            snapshot = snapshot_recipe_implementation_registry(registry)
            with self.subTest(order=tuple(registry)):
                self.assertEqual(first_root, snapshot.registry_root)
                self.assertEqual(
                    expected,
                    self.identity(self.execute(registry=snapshot)),
                )

    def test_binding_is_content_addressed_complete_and_registry_is_immutable(self) -> None:
        binding = self.registration.execution_binding
        value = binding.to_dict()
        self.assertEqual(
            {
                "bounds",
                "capability_id",
                "custodian",
                "dependency_lock_object_descriptor_id",
                "derivation_step_id",
                "executable_object_descriptor_id",
                "execution_artifact_object_descriptor_id",
                "execution_binding_id",
                "format",
                "graph_record_schema_object_descriptor_id",
                "handler_id",
                "implementation_id",
                "materializer",
                "output_contract_key",
                "recipe_id",
                "recipe_owner",
                "resource_limits_object_descriptor_id",
                "runtime_environment_object_descriptor_id",
                "schema_version",
                "source_tree_object_descriptor_id",
                "subject_identity_schema_id",
                "subject_identity_schema_object_descriptor_id",
                "worker_protocol",
            },
            set(value),
        )
        self.assertEqual(binding.canonical_bytes, canonical_json_bytes(value))
        self.assertRegex(
            binding.execution_binding_id,
            r"^recipe-execution-binding:sha256:[0-9a-f]{64}$",
        )
        snapshot = snapshot_recipe_implementation_registry(
            {binding.implementation_id: self.registration}
        )
        self.assertIsNot(snapshot.registrations[0], self.registration)
        self.assertEqual(binding.execution_binding_id, snapshot.registrations[0].execution_binding.execution_binding_id)
        self.assertEqual(
            snapshot.registry_root,
            self.execute(registry=snapshot).implementation_registry_root,
        )

    def test_every_tampered_binding_group_rejects_before_worker_invocation(self) -> None:
        binding = self.registration.execution_binding
        changes = (
            {"recipe_id": "materialization-recipe:sha256:" + "1" * 64},
            {"implementation_id": "implementation:sha256:" + "2" * 64},
            {"source_tree_object_descriptor_id": "object-descriptor:sha256:" + "3" * 64},
            {"executable_object_descriptor_id": "object-descriptor:sha256:" + "d" * 64},
            {"execution_artifact_object_descriptor_id": "object-descriptor:sha256:" + "4" * 64},
            {"dependency_lock_object_descriptor_id": "object-descriptor:sha256:" + "5" * 64},
            {"runtime_environment_object_descriptor_id": "object-descriptor:sha256:" + "6" * 64},
            {"resource_limits_object_descriptor_id": "object-descriptor:sha256:" + "e" * 64},
            {"handler_id": "handler:sha256:" + "7" * 64},
            {"capability_id": "capability:sha256:" + "8" * 64},
            {"owner_authority_id": "authority:sha256:" + "f" * 64},
            {"owner_revision_id": "owner-revision:sha256:" + "9" * 64},
            {"authority_adapter_id": "authority-adapter:sha256:" + "0" * 64},
            {"subject_identity_schema_id": "workbench://schemas/other.json"},
            {"subject_identity_schema_object_descriptor_id": "object-descriptor:sha256:" + "1" * 64},
            {"graph_record_schema_object_descriptor_id": "object-descriptor:sha256:" + "2" * 64},
            {"derivation_step_id": "derivation-step:sha256:" + "a" * 64},
            {"output_contract_key": "synthetic.other"},
            {"materializer_component_id": "component:sha256:" + "b" * 64},
            {"materializer_implementation_id": "implementation:sha256:" + "3" * 64},
            {"custodian_component_id": "component:sha256:" + "4" * 64},
            {"custodian_implementation_id": "implementation:sha256:" + "c" * 64},
            {"worker_protocol": "workbench-crucible-recipe-worker-v2"},
            {"bounds": replace(binding.bounds, max_output_bytes=1)},
        )
        for change in changes:
            with self.subTest(change=next(iter(change))):
                registration = self.registration_with_binding(**change)
                with mock.patch(
                    "workbench_crucible_materializer.materializer.subprocess.run"
                ) as run:
                    with self.assertRaises(MaterializationError):
                        self.execute(registration=registration)
                run.assert_not_called()

        with mock.patch(
            "workbench_crucible_materializer.materializer._installed_worker_handler_id",
            return_value="handler:sha256:" + "e" * 64,
        ), mock.patch(
            "workbench_crucible_materializer.materializer.subprocess.run"
        ) as run, self.assertRaises(MaterializationError) as caught:
            self.execute()
        self.assertCode(caught, "materializer.worker-unavailable")
        run.assert_not_called()

    def test_fresh_worker_has_sanitized_environment_and_value_only_request(self) -> None:
        calls = []
        actual_run = subprocess.run

        def observed(*args, **kwargs):
            calls.append((args, kwargs))
            return actual_run(*args, **kwargs)

        with mock.patch(
            "workbench_crucible_materializer.materializer.subprocess.run",
            side_effect=observed,
        ):
            self.execute()
        self.assertEqual(3, len(calls))
        for args, kwargs in calls:
            self.assertEqual([sys.executable, "-I", "-S"], args[0][:3])
            self.assertEqual(
                {"LANG", "LC_ALL", "TZ", "WORKBENCH_RECIPE_WORKER"},
                set(kwargs["env"]),
            )
            self.assertIs(kwargs["close_fds"], True)
            self.assertIs(kwargs["start_new_session"], True)
            request = kwargs["input"]
            for forbidden in (b"store", b"reference", b"lease", b"resolver", b"authority"):
                self.assertNotIn(forbidden, request)
            wire = parse_canonical_json(request)
            self.assertEqual(
                {
                    "capability_id",
                    "derivation_step_id",
                    "evidence_kind",
                    "execution_binding_id",
                    "handler_id",
                    "implementation_id",
                    "output_contract_key",
                    "payload_base64",
                    "payload_sha256",
                    "protocol",
                    "recipe_id",
                    "request_nonce",
                },
                set(wire),
            )
            self.assertEqual(
                BUILTIN_SYNTHETIC_BEFORE_CAPABILITY_ID,
                wire["capability_id"],
            )
            self.assertEqual(
                self.registration.derivation_step_id,
                wire["derivation_step_id"],
            )
            self.assertEqual(
                self.registration.output_contract_key,
                wire["output_contract_key"],
            )
            self.assertEqual(
                self.source.evidence.to_dict()["evidence_kind"],
                wire["evidence_kind"],
            )

    def test_fresh_worker_timeout_has_a_stable_fail_closed_diagnostic(self) -> None:
        with mock.patch(
            "workbench_crucible_materializer.materializer.subprocess.run",
            side_effect=subprocess.TimeoutExpired("worker", 30),
        ), self.assertRaises(MaterializationError) as caught:
            self.execute()
        self.assertCode(caught, "materializer.worker-timeout")

    def test_fresh_worker_qualification_detects_cross_process_drift(self) -> None:
        binding = self.registration.execution_binding
        request = RecipeExecutionInput(
            binding.execution_binding_id,
            binding.recipe_id,
            binding.implementation_id,
            binding.derivation_step_id,
            self.source.evidence.id,
            self.source.evidence.to_dict()["evidence_kind"],
            self.source.evidence.to_dict()["context_ref_id"],
            self.source.payload_bytes,
        )
        alpha = RecipeSemanticOutput(
            "workbench.synthetic", "alpha", "beta", "synthetic.before", "directed", "synthetic.main"
        )
        gamma = replace(alpha, target_subject_name="gamma")
        with mock.patch(
            "workbench_crucible_materializer.materializer._fresh_worker_call",
            side_effect=(alpha, gamma),
        ):
            with self.assertRaises(MaterializationError) as caught:
                qualify_recipe_execution(self.registration, request)
        self.assertCode(caught, "materializer.nondeterministic-output")

        mismatched = replace(
            request,
            derivation_step_id="derivation-step:sha256:" + "f" * 64,
        )
        with mock.patch(
            "workbench_crucible_materializer.materializer.subprocess.run"
        ) as run, self.assertRaises(MaterializationError) as caught:
            qualify_recipe_execution(self.registration, mismatched)
        self.assertCode(caught, "materializer.worker-request-mismatch")
        run.assert_not_called()

    def test_unadmitted_effective_pair_is_rejected_before_worker(self) -> None:
        admission = _reseed(
            self.source.admission.to_dict(),
            outcome="rejected",
            eligible_for_materialization=False,
        )
        source = self._with_effective_pair(self.source.evidence, admission)
        with mock.patch(
            "workbench_crucible_materializer.materializer.subprocess.run"
        ) as run, self.assertRaises(MaterializationError) as caught:
            self.execute(source)
        self.assertCode(caught, "materializer.not-admitted")
        run.assert_not_called()

    def test_mismatched_effective_membership_is_rejected(self) -> None:
        evidence = _reseed(
            self.source.evidence.to_dict(), evidence_kind="synthetic.observation.other"
        )
        with self.assertRaises(MaterializationError) as caught:
            self.execute(replace(self.source, evidence=evidence))
        self.assertCode(caught, "materializer.partition-membership")

    def test_context_mismatch_is_rejected_after_exact_membership(self) -> None:
        other_context = "context-ref:sha256:" + "f" * 64
        evidence = _reseed(
            self.source.evidence.to_dict(), context_ref_id=other_context
        )
        admission = _reseed(
            self.source.admission.to_dict(),
            candidate_record_id=evidence.id,
            context_ref_id=other_context,
        )
        source = self._with_effective_pair(evidence, admission)
        with self.assertRaises(MaterializationError) as caught:
            self.execute(source)
        self.assertCode(caught, "materializer.context-mismatch")

    def test_payload_binding_canonicality_and_grammar_fail_closed(self) -> None:
        with self.assertRaises(MaterializationError) as caught:
            self.execute(replace(self.source, payload_bytes=b'{"wrong":true}'))
        self.assertCode(caught, "materializer.object-mismatch")

        payload_raw = canonical_json_bytes(
            {
                "value_kind": "evidence-payload",
                "observation": "not-a-relation",
                "ordinal": 0,
            }
        )
        digest = hashlib.sha256(payload_raw).hexdigest()
        payload_descriptor = _reseed(
            self.source.payload_descriptor.to_dict(),
            object_id=f"workbench-blob-v2:sha256:{digest}",
            sha256=digest,
            byte_length=len(payload_raw),
        )
        evidence = _reseed(
            self.source.evidence.to_dict(),
            payload_object_descriptor_id=payload_descriptor.id,
        )
        admission = _reseed(
            self.source.admission.to_dict(), candidate_record_id=evidence.id
        )
        source = replace(
            self._with_effective_pair(evidence, admission),
            payload_descriptor=payload_descriptor,
            payload_bytes=payload_raw,
        )
        with self.assertRaises(MaterializationError) as caught:
            self.execute(source)
        self.assertCode(caught, "materializer.implementation-failed")

    def test_payload_and_aggregate_byte_budgets_fail_before_execution(self) -> None:
        with self.assertRaises(MaterializationError) as caught:
            self.execute(replace(self.source, payload_bytes=b"x" * 4097))
        self.assertCode(caught, "materializer.payload-bound")

        with self.assertRaises(MaterializationError) as caught:
            self.execute(
                replace(self.source, evidence_partition_bytes=b"x" * (1024 * 1024))
            )
        self.assertCode(caught, "materializer.input-bound")

    def test_registration_and_shard_bounds_fail_closed(self) -> None:
        with self.assertRaises(MaterializationError) as caught:
            execute_materialization(self.source, registrations={})
        self.assertCode(caught, "materializer.implementation-unavailable")

        registration = self.registration_with_binding(
            bounds=replace(
                self.registration.execution_binding.bounds,
                records_per_shard=65,
            )
        )
        with self.assertRaises(MaterializationError) as caught:
            self.execute(registration=registration)
        self.assertCode(caught, "materializer.shard-bound")

    def test_subject_identity_schema_must_differ_from_graph_schema(self) -> None:
        recipe_value = self.source.recipe.to_dict()
        recipe_value.pop("id")
        for output in recipe_value["output_contracts"]:
            if output["output_contract_key"] == "synthetic.main":
                output["subject_contracts"][0][
                    "subject_identity_schema_object_descriptor_id"
                ] = output["graph_record_schema_object_descriptor_id"]
        recipe_value["output_contracts"] = sorted(
            recipe_value["output_contracts"], key=canonical_json_bytes
        )
        recipe = seal_record(recipe_value)
        source = replace(self.source, recipe=recipe)
        binding = self.registration.execution_binding
        registration = RecipeImplementationRegistration(
            seal_recipe_execution_binding(
                recipe,
                derivation_step_id=binding.derivation_step_id,
                output_contract_key=binding.output_contract_key,
                handler_id=binding.handler_id,
                capability_id=binding.capability_id,
                subject_identity_schema_id=binding.subject_identity_schema_id,
                materializer_component_id=binding.materializer_component_id,
                materializer_implementation_id=binding.materializer_implementation_id,
                custodian_component_id=binding.custodian_component_id,
                custodian_implementation_id=binding.custodian_implementation_id,
                bounds=binding.bounds,
            )
        )

        with self.assertRaises(MaterializationError) as caught:
            self.execute(source, registration)
        self.assertCode(caught, "materializer.subject-schema")


if __name__ == "__main__":
    unittest.main()
