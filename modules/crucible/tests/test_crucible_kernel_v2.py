from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_kernel import (  # noqa: E402
    AuthorityBinding,
    GraphShardAssembler,
    KernelValidationError,
    OwnerPolicyBinding,
    ShardArtifact,
    ShardKernelConfig,
    assemble_graph_partitions,
)
from workbench_crucible_kernel.kernel import ShardKernelBounds  # noqa: E402
from workbench_api.canonical import CANONICALIZER_ID, canonical_json_bytes, record_content_id
from workbench_crucible import ValidatedRecord, seal_record
from workbench_crucible.synthetic import (  # noqa: E402
    build_synthetic_publication,
)


class _RaisingEquality:
    def __eq__(self, other):
        del other
        raise AssertionError("untrusted convenience metadata equality was invoked")

    def __hash__(self):
        raise AssertionError("untrusted convenience metadata hashing was invoked")


class CrucibleKernelV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.publication = build_synthetic_publication()
        cls.records_by_key = {}
        cls.coarse_node = None
        for record in cls.publication.records:
            if record.kind != "graph-record":
                continue
            value = record.to_dict()
            if value["resolution_id"] == "synthetic.exact":
                cls.records_by_key[value["logical_key"]] = record
            elif (
                value["resolution_id"] == "synthetic.coarse"
                and value["logical_key"] == "node:alpha"
            ):
                cls.coarse_node = record
        graph = cls.publication.record("graph-revision")
        recipe_owner = AuthorityBinding(**graph["recipe_owner"])
        cls.config = ShardKernelConfig(
            record_schema_object_descriptor_id=graph[
                "record_schema_object_descriptor_ids"
            ][0],
            total_order_policy=OwnerPolicyBinding(
                policy_id=graph["total_order_policy_id"],
                authority=recipe_owner,
            ),
            partition_policy=OwnerPolicyBinding(
                policy_id=graph["partition_policy_id"],
                authority=recipe_owner,
            ),
            records_per_shard=2,
        )
        cls.base = tuple(
            cls.records_by_key[key]
            for key in (
                "node:alpha",
                "node:beta",
                "conflict:a-forward",
                "conflict:z-target",
            )
        )
        node_delta = cls.base[1].to_dict()
        node_delta.pop("id")
        node_delta["logical_key"] = "node:delta"
        cls.node_delta = seal_record(node_delta)
        node_gamma = cls.base[1].to_dict()
        node_gamma.pop("id")
        node_gamma["logical_key"] = "node:gamma"
        cls.node_gamma = seal_record(node_gamma)
        replacement = cls.base[1].to_dict()
        replacement.pop("id")
        replacement["body"]["node_kind"] = "synthetic.subject.variant"
        cls.replacement_node_beta = seal_record(replacement)
        cls.multi_shard_nodes = (
            cls.base[0],
            cls.base[1],
            cls.node_delta,
            cls.node_gamma,
        )

    @staticmethod
    def logical_key_port(record):
        return record.to_dict()["logical_key"]

    @staticmethod
    def partition_key_port(record, logical_key):
        del logical_key
        return f"synthetic.{record.to_dict()['body']['record_kind']}"

    @classmethod
    def assemble(cls, records, *, prior=(), bounds=None):
        return assemble_graph_partitions(
            records,
            config=cls.config,
            logical_key_port=cls.logical_key_port,
            partition_key_port=cls.partition_key_port,
            prior_shards=prior,
            bounds=bounds,
        )

    @staticmethod
    def identity_projection(assembly):
        return (
            assembly.scope,
            canonical_json_bytes(assembly.graph_revision_fields()),
            tuple(
                (
                    shard.coordinate,
                    shard.ndjson_bytes,
                    shard.object_descriptor.canonical_bytes,
                )
                for shard in assembly.shards
            ),
        )

    def assertKernelCode(self, caught, code: str) -> None:
        self.assertEqual(code, caught.exception.code)

    def test_input_permutation_does_not_change_any_identity_bytes(self) -> None:
        expected = self.identity_projection(self.assemble(self.base))
        variants = (
            tuple(reversed(self.base)),
            (self.base[2], self.base[0], self.base[3], self.base[1]),
            (self.base[1], self.base[3], self.base[0], self.base[2]),
        )
        for variant in variants:
            with self.subTest(order=[record.id for record in variant]):
                self.assertEqual(expected, self.identity_projection(self.assemble(variant)))

    def test_arbitrary_input_batches_equal_one_clean_batch(self) -> None:
        expected = self.identity_projection(self.assemble(self.base))
        assembler = GraphShardAssembler(
            config=self.config,
            logical_key_port=self.logical_key_port,
            partition_key_port=self.partition_key_port,
        )
        assembler.add_batch([self.base[3].canonical_bytes])
        assembler.add_batch(
            [bytearray(self.base[0].canonical_bytes), self.base[2]]
        )
        assembler.add_batch([memoryview(self.base[1].canonical_bytes)])
        self.assertEqual(expected, self.identity_projection(assembler.finish()))

    def test_failed_batch_rolls_back_without_partial_record_commit(self) -> None:
        def partition_port(record, logical_key):
            if logical_key == "node:beta":
                raise KernelValidationError(
                    "owner.forged-diagnostic", "/forged", "must be wrapped"
                )
            return self.partition_key_port(record, logical_key)

        assembler = GraphShardAssembler(
            config=self.config,
            logical_key_port=self.logical_key_port,
            partition_key_port=partition_port,
        )
        with self.assertRaises(KernelValidationError) as caught:
            assembler.add_batch((self.base[0], self.base[1]))
        self.assertKernelCode(caught, "kernel.partition-port-failed")
        self.assertEqual("/records/1/partition_key", caught.exception.path)

        assembler.add_batch((self.base[0],))
        actual = assembler.finish()
        expected = self.assemble((self.base[0],))
        self.assertEqual(
            self.identity_projection(expected), self.identity_projection(actual)
        )

    def test_duplicate_ids_and_distinct_records_with_one_logical_key_fail_closed(self) -> None:
        with self.assertRaises(KernelValidationError) as caught:
            self.assemble((self.base[0], self.base[0]))
        self.assertKernelCode(caught, "kernel.duplicate-record-id")

        collision_candidate = self.base[1].to_dict()
        collision_candidate.pop("id")
        collision_candidate["logical_key"] = self.base[0].to_dict()["logical_key"]
        collision = seal_record(collision_candidate)
        self.assertNotEqual(self.base[0].id, collision.id)
        with self.assertRaises(KernelValidationError) as caught:
            self.assemble((self.base[0], collision))
        self.assertKernelCode(caught, "kernel.logical-key-collision")

    def test_descriptor_bytes_blob_digest_and_roots_are_exact_c01_values(self) -> None:
        assembly = self.assemble(self.base)
        node_shard = next(
            shard for shard in assembly.shards if shard.coordinate.record_kind == "node"
        )
        ordered_nodes = sorted(
            self.base[:2],
            key=lambda record: record.to_dict()["logical_key"].encode("utf-8"),
        )
        expected_raw = b"".join(record.canonical_bytes + b"\n" for record in ordered_nodes)
        self.assertEqual(expected_raw, node_shard.ndjson_bytes)
        digest = hashlib.sha256(expected_raw).hexdigest()
        expected_descriptor = {
            "kind": "object-descriptor",
            "format": "workbench-crucible-object-descriptor-v2",
            "schema_version": 2,
            "schema_id": (
                "workbench://schemas/crucible/"
                "crucible-object-descriptor-v2.schema.json"
            ),
            "canonicalizer": CANONICALIZER_ID,
            "object_id": f"workbench-blob-v2:sha256:{digest}",
            "sha256": digest,
            "byte_length": len(expected_raw),
            "media_type": "application/x-ndjson",
            "representation": "canonical-ndjson-shard",
            "semantic_role": "graph-record-shard",
            "described_schema_id": (
                "workbench://schemas/crucible/"
                "crucible-graph-record-v2.schema.json"
            ),
            "described_schema_object_descriptor_id": (
                self.config.record_schema_object_descriptor_id
            ),
            "described_record_kind": "graph-record",
            "canonical_item_count": 2,
            "total_order_policy_id": self.config.total_order_policy.policy_id,
            "total_order_authority": self.config.total_order_policy.authority.to_dict(),
            "minimum_record_key": "node:alpha",
            "maximum_record_key": "node:beta",
        }
        expected_descriptor["id"] = record_content_id(expected_descriptor)
        self.assertEqual(
            canonical_json_bytes(expected_descriptor),
            node_shard.object_descriptor.canonical_bytes,
        )
        self.assertEqual(
            "d27ed977b5ac9e19be81e9be733a783a1db4314e7f60c8d01a7eca04d763bf00",
            digest,
        )
        self.assertEqual(
            "object-descriptor:sha256:"
            "1df30a6b07f33c0f2c3d462593311b789bf9bd8d8ddf9b7ca05a078e273de4f5",
            node_shard.object_descriptor.id,
        )
        self.assertEqual(
            "4ade77c1aa753e4419d28a11b5e7fadbb89567e6b58bebaf54bd5e341974dd04",
            hashlib.sha256(node_shard.object_descriptor.canonical_bytes).hexdigest(),
        )

        partition_value = {
            "partition_key": "synthetic.node",
            "record_ids": [record.id for record in ordered_nodes],
            "shard_ordinal": 0,
        }
        expected_partition_root = hashlib.sha256(
            b"workbench-semantic-root-v2\n"
            + b"graph-revision/partition/node\n"
            + canonical_json_bytes(partition_value)
        ).hexdigest()
        expected_record_root = hashlib.sha256(
            b"workbench-semantic-root-v2\n"
            + b"graph-revision/records/node\n"
            + canonical_json_bytes([record.id for record in ordered_nodes])
        ).hexdigest()
        self.assertEqual(
            "dedde2defd96dc133e64309694f0e639e3dff1f83172a24b94bc517aded31fcf",
            expected_partition_root,
        )
        self.assertEqual(expected_partition_root, node_shard.partition.semantic_root)
        self.assertEqual(
            "23062c0ed5c3d9e985e81ca971fd5b4573414c818afefed9345fca5f679a84dc",
            expected_record_root,
        )
        self.assertEqual(expected_record_root, assembly.record_roots.nodes)

    def test_unchanged_shards_are_reused_by_exact_coordinate_and_bytes(self) -> None:
        prior = self.assemble(self.multi_shard_nodes)
        target = (
            self.base[0],
            self.replacement_node_beta,
            self.node_delta,
            self.node_gamma,
        )
        incremental = self.assemble(target, prior=prior.shards)
        prior_by_ordinal = {
            shard.coordinate.shard_ordinal: shard for shard in prior.shards
        }
        current_by_ordinal = {
            shard.coordinate.shard_ordinal: shard for shard in incremental.shards
        }
        self.assertEqual(
            {prior_by_ordinal[1].coordinate},
            set(incremental.reused_coordinates),
        )
        self.assertNotEqual(
            prior_by_ordinal[0].object_descriptor.id,
            current_by_ordinal[0].object_descriptor.id,
        )
        self.assertIs(
            prior_by_ordinal[1].ndjson_bytes,
            current_by_ordinal[1].ndjson_bytes,
        )

    def test_clean_and_incremental_assembly_have_identical_bytes_and_ids(self) -> None:
        prior = self.assemble(self.multi_shard_nodes)
        target = (
            self.base[0],
            self.replacement_node_beta,
            self.node_delta,
            self.node_gamma,
        )
        clean = self.assemble(target)
        incremental = self.assemble(reversed(target), prior=prior.shards)
        self.assertEqual(
            self.identity_projection(clean),
            self.identity_projection(incremental),
        )
        self.assertTrue(incremental.reused_coordinates)
        self.assertFalse(clean.reused_coordinates)

    def test_empty_assembly_has_no_scope_shards_counts_or_roots(self) -> None:
        def unexpected_port(*args):
            del args
            self.fail("empty assembly must not invoke owner ports")

        assembly = assemble_graph_partitions(
            (),
            config=self.config,
            logical_key_port=unexpected_port,
            partition_key_port=unexpected_port,
        )
        self.assertIsNone(assembly.scope)
        self.assertEqual((), assembly.shards)
        self.assertEqual((), assembly.reused_coordinates)
        self.assertEqual({0}, set(assembly.record_counts.to_dict().values()))
        self.assertEqual({None}, set(assembly.record_roots.to_dict().values()))

    def test_malformed_prior_rejects_without_consuming_assembler(self) -> None:
        assembler = GraphShardAssembler(
            config=self.config,
            logical_key_port=self.logical_key_port,
            partition_key_port=self.partition_key_port,
        )
        assembler.add_batch(self.base)
        prior = self.assemble(self.base)
        malformed = replace(
            prior.shards[0], ndjson_bytes=prior.shards[0].ndjson_bytes + b"\n"
        )
        with self.assertRaises(KernelValidationError) as caught:
            assembler.finish(prior_shards=(malformed,))
        self.assertKernelCode(caught, "kernel.invalid-prior-shard")
        self.assertEqual(
            self.identity_projection(self.assemble(self.base)),
            self.identity_projection(assembler.finish()),
        )

    def test_config_and_policy_owner_mismatches_fail_closed(self) -> None:
        other_owner = AuthorityBinding(
            owner_authority_id="authority:sha256:" + "1" * 64,
            owner_revision_id="owner-revision:sha256:" + "2" * 64,
            authority_adapter_id="authority-adapter:sha256:" + "3" * 64,
        )
        cases = (
            (
                replace(
                    self.config,
                    partition_policy=OwnerPolicyBinding(
                        policy_id=self.config.partition_policy.policy_id,
                        authority=other_owner,
                    ),
                ),
                "kernel.policy-authority-mismatch",
            ),
            (replace(self.config, records_per_shard=True), "kernel.invalid-config"),
        )
        for config, expected_code in cases:
            with self.subTest(code=expected_code):
                with self.assertRaises(KernelValidationError) as caught:
                    GraphShardAssembler(
                        config=config,
                        logical_key_port=self.logical_key_port,
                        partition_key_port=self.partition_key_port,
                    )
                self.assertKernelCode(caught, expected_code)

    def test_shift_append_and_removal_reuse_only_exact_unchanged_shards(self) -> None:
        def node_with_key(logical_key):
            candidate = self.base[1].to_dict()
            candidate.pop("id")
            candidate["logical_key"] = logical_key
            return seal_record(candidate)

        prior = self.assemble(self.multi_shard_nodes)
        prior_coordinates = tuple(shard.coordinate for shard in prior.shards)
        cases = (
            ((node_with_key("node:aardvark"), *self.multi_shard_nodes), ()),
            ((*self.multi_shard_nodes, node_with_key("node:omega")), prior_coordinates),
            (self.multi_shard_nodes[:3], prior_coordinates[:1]),
        )
        for records, expected_reuse in cases:
            with self.subTest(keys=[record.to_dict()["logical_key"] for record in records]):
                incremental = self.assemble(records, prior=prior.shards)
                self.assertEqual(expected_reuse, incremental.reused_coordinates)
                self.assertEqual(
                    self.identity_projection(self.assemble(records)),
                    self.identity_projection(incremental),
                )

    def test_untrusted_wrapper_metadata_is_discarded_without_equality_dispatch(self) -> None:
        raising = _RaisingEquality()
        source = self.base[0]
        forged_record = ValidatedRecord(
            id=raising,
            kind=raising,
            format=raising,
            schema_id=raising,
            canonical_bytes=source.canonical_bytes,
            references=raising,
        )
        expected = self.assemble((source.canonical_bytes,))
        actual = self.assemble((forged_record,))
        self.assertEqual(
            self.identity_projection(expected), self.identity_projection(actual)
        )

        prior = self.assemble(self.multi_shard_nodes)
        retained = prior.shards[1]
        forged_descriptor = ValidatedRecord(
            id=raising,
            kind=raising,
            format=raising,
            schema_id=raising,
            canonical_bytes=retained.object_descriptor.canonical_bytes,
            references=raising,
        )
        forged_artifact = ShardArtifact(
            coordinate=retained.coordinate,
            record_ids=retained.record_ids,
            logical_keys=retained.logical_keys,
            ndjson_bytes=retained.ndjson_bytes,
            object_descriptor=forged_descriptor,
            partition=retained.partition,
        )
        incremental = self.assemble(
            self.multi_shard_nodes, prior=(forged_artifact,)
        )
        self.assertIn(retained.coordinate, incremental.reused_coordinates)

    def test_rows_from_different_graph_revision_scopes_cannot_be_combined(self) -> None:
        self.assertIsNotNone(self.coarse_node)
        with self.assertRaises(KernelValidationError) as caught:
            self.assemble((self.base[1], self.coarse_node))
        self.assertKernelCode(caught, "kernel.graph-scope-mismatch")

    def test_owner_port_surrogates_fail_with_a_stable_kernel_diagnostic(self) -> None:
        with self.assertRaises(KernelValidationError) as caught:
            assemble_graph_partitions(
                (self.base[0],),
                config=self.config,
                logical_key_port=self.logical_key_port,
                partition_key_port=lambda record, logical_key: "\ud800",
            )
        self.assertKernelCode(caught, "kernel.invalid-semantic-text")
        self.assertEqual("/records/0/partition_key", caught.exception.path)

    def test_owner_ports_cannot_mutate_their_isolated_record_requests(self) -> None:
        original_id = self.base[0].id

        def mutating_logical_port(record):
            object.__setattr__(record, "id", self.base[1].id)
            return record.to_dict()["logical_key"]

        def mutating_partition_port(record, logical_key):
            object.__setattr__(record, "kind", "object-descriptor")
            return self.partition_key_port(record, logical_key)

        cases = (
            (
                mutating_logical_port,
                self.partition_key_port,
                "kernel.key-port-mutation",
            ),
            (
                self.logical_key_port,
                mutating_partition_port,
                "kernel.partition-port-mutation",
            ),
        )
        for logical_port, partition_port, expected_code in cases:
            with self.subTest(code=expected_code):
                with self.assertRaises(KernelValidationError) as caught:
                    assemble_graph_partitions(
                        (self.base[0],),
                        config=self.config,
                        logical_key_port=logical_port,
                        partition_key_port=partition_port,
                    )
                self.assertKernelCode(caught, expected_code)

        # Both callbacks received reconstructed transports, so the original
        # canonical record remains reusable after either rejected call.
        self.assertEqual(original_id, self.base[0].id)
        self.assemble((self.base[0],))

    def test_operational_bounds_are_exact_and_excluded_from_identity(self) -> None:
        prior = self.assemble(self.base)
        bounded = ShardKernelBounds(
            maximum_records=len(self.base),
            maximum_record_bytes=max(
                len(record.canonical_bytes) for record in self.base
            ),
            maximum_input_bytes=sum(
                len(record.canonical_bytes) for record in self.base
            ),
            maximum_prior_shards=len(prior.shards),
            maximum_prior_bytes=sum(
                len(shard.ndjson_bytes)
                + len(shard.object_descriptor.canonical_bytes)
                for shard in prior.shards
            ),
            maximum_output_shards=len(prior.shards),
            maximum_shard_bytes=max(
                len(shard.ndjson_bytes) for shard in prior.shards
            ),
        )
        clean = self.assemble(reversed(self.base), bounds=bounded)
        incremental = self.assemble(
            self.base, prior=prior.shards, bounds=bounded
        )
        self.assertEqual(
            self.identity_projection(prior), self.identity_projection(clean)
        )
        self.assertEqual(
            self.identity_projection(clean),
            self.identity_projection(incremental),
        )
        self.assertEqual(
            tuple(shard.coordinate for shard in incremental.shards),
            incremental.reused_coordinates,
        )

        invalid = (
            replace(ShardKernelBounds(), maximum_records=True),
            replace(ShardKernelBounds(), maximum_input_bytes=0),
            replace(ShardKernelBounds(), maximum_output_shards=10**20),
        )
        for bounds in invalid:
            with self.subTest(bounds=bounds):
                with self.assertRaises(KernelValidationError) as caught:
                    GraphShardAssembler(
                        config=self.config,
                        logical_key_port=self.logical_key_port,
                        partition_key_port=self.partition_key_port,
                        bounds=bounds,
                    )
                self.assertKernelCode(caught, "kernel.invalid-bounds")

    def test_record_count_and_byte_bounds_fail_before_parse_or_callback(self) -> None:
        callback_calls = 0

        def logical_port(record):
            nonlocal callback_calls
            callback_calls += 1
            return self.logical_key_port(record)

        oversized = GraphShardAssembler(
            config=self.config,
            logical_key_port=logical_port,
            partition_key_port=self.partition_key_port,
            bounds=replace(ShardKernelBounds(), maximum_record_bytes=1),
        )
        with self.assertRaises(KernelValidationError) as caught:
            oversized.add_batch((b"not-canonical-json",))
        self.assertKernelCode(caught, "kernel.record-byte-bound")
        self.assertEqual(0, callback_calls)

        first_size = len(self.base[0].canonical_bytes)
        second_size = len(self.base[1].canonical_bytes)
        aggregate = GraphShardAssembler(
            config=self.config,
            logical_key_port=logical_port,
            partition_key_port=self.partition_key_port,
            bounds=replace(
                ShardKernelBounds(),
                maximum_input_bytes=first_size + second_size - 1,
            ),
        )
        with self.assertRaises(KernelValidationError) as caught:
            aggregate.add_batch(self.base[:2])
        self.assertKernelCode(caught, "kernel.input-byte-bound")
        self.assertEqual(1, callback_calls)
        aggregate.add_batch((self.base[1],))
        self.assertEqual(
            self.identity_projection(self.assemble((self.base[1],))),
            self.identity_projection(aggregate.finish()),
        )

        class InfiniteLike:
            def __init__(inner_self):
                inner_self.reads = 0

            def __iter__(inner_self):
                return inner_self

            def __next__(inner_self):
                inner_self.reads += 1
                return self.base[0]

        source = InfiniteLike()
        counted = GraphShardAssembler(
            config=self.config,
            logical_key_port=self.logical_key_port,
            partition_key_port=self.partition_key_port,
            bounds=replace(ShardKernelBounds(), maximum_records=1),
        )
        with self.assertRaises(KernelValidationError) as caught:
            counted.add_batch(source)
        self.assertKernelCode(caught, "kernel.record-bound")
        self.assertEqual(2, source.reads)
        counted.add_batch((self.base[0],))
        counted.finish()

        released = memoryview(self.base[0].canonical_bytes)
        released.release()
        rejected = GraphShardAssembler(
            config=self.config,
            logical_key_port=self.logical_key_port,
            partition_key_port=self.partition_key_port,
        )
        with self.assertRaises(KernelValidationError) as caught:
            rejected.add_batch((released,))
        self.assertKernelCode(caught, "kernel.invalid-record-input")

    def test_raising_input_iterator_rolls_back_bound_accounting(self) -> None:
        def raising_records():
            yield self.base[0]
            raise RuntimeError("iterator failed")

        assembler = GraphShardAssembler(
            config=self.config,
            logical_key_port=self.logical_key_port,
            partition_key_port=self.partition_key_port,
            bounds=replace(ShardKernelBounds(), maximum_records=1),
        )
        with self.assertRaises(KernelValidationError) as caught:
            assembler.add_batch(raising_records())
        self.assertKernelCode(caught, "kernel.batch-read-failed")
        assembler.add_batch((self.base[0],))
        self.assertEqual(
            self.identity_projection(self.assemble((self.base[0],))),
            self.identity_projection(assembler.finish()),
        )

    def test_prior_hint_count_and_bytes_are_bounded_before_row_parsing(self) -> None:
        prior = self.assemble(self.base)
        assembler = GraphShardAssembler(
            config=self.config,
            logical_key_port=self.logical_key_port,
            partition_key_port=self.partition_key_port,
            bounds=replace(ShardKernelBounds(), maximum_prior_shards=1),
        )
        assembler.add_batch(self.base)
        with self.assertRaises(KernelValidationError) as caught:
            assembler.finish(prior_shards=prior.shards)
        self.assertKernelCode(caught, "kernel.prior-shard-bound")
        self.assertEqual(
            self.identity_projection(prior),
            self.identity_projection(assembler.finish()),
        )

        first_size = len(prior.shards[0].ndjson_bytes) + len(
            prior.shards[0].object_descriptor.canonical_bytes
        )
        byte_bounded = GraphShardAssembler(
            config=self.config,
            logical_key_port=self.logical_key_port,
            partition_key_port=self.partition_key_port,
            bounds=replace(
                ShardKernelBounds(), maximum_prior_bytes=first_size
            ),
        )
        byte_bounded.add_batch(self.base)
        with self.assertRaises(KernelValidationError) as caught:
            byte_bounded.finish(prior_shards=prior.shards)
        self.assertKernelCode(caught, "kernel.prior-byte-bound")

        forged = replace(prior.shards[0], ndjson_bytes=b"not-json\n")
        preparse = GraphShardAssembler(
            config=self.config,
            logical_key_port=self.logical_key_port,
            partition_key_port=self.partition_key_port,
            bounds=replace(ShardKernelBounds(), maximum_shard_bytes=1),
        )
        preparse.add_batch((self.base[0],))
        with self.assertRaises(KernelValidationError) as caught:
            preparse.finish(prior_shards=(forged,))
        self.assertKernelCode(caught, "kernel.prior-shard-byte-bound")

        metadata_forged = replace(
            prior.shards[0],
            record_ids=(object(),) * 100_001,
            logical_keys=(object(),) * 100_001,
        )
        metadata_preparse = GraphShardAssembler(
            config=self.config,
            logical_key_port=self.logical_key_port,
            partition_key_port=self.partition_key_port,
        )
        metadata_preparse.add_batch((self.base[0],))
        with mock.patch(
            "workbench_crucible_kernel.kernel._validate_prior_artifact",
            side_effect=AssertionError("metadata bound must precede validation"),
        ) as validator:
            with self.assertRaises(KernelValidationError) as caught:
                metadata_preparse.finish(prior_shards=(metadata_forged,))
        self.assertKernelCode(caught, "kernel.invalid-prior-shard")
        validator.assert_not_called()

    def test_output_shard_count_and_bytes_fail_before_shard_construction(self) -> None:
        one_row_config = replace(self.config, records_per_shard=1)
        with self.assertRaises(KernelValidationError) as caught:
            assemble_graph_partitions(
                self.base,
                config=one_row_config,
                logical_key_port=self.logical_key_port,
                partition_key_port=self.partition_key_port,
                bounds=replace(
                    ShardKernelBounds(), maximum_output_shards=3
                ),
            )
        self.assertKernelCode(caught, "kernel.output-shard-bound")

        with self.assertRaises(KernelValidationError) as caught:
            self.assemble(
                (self.base[0],),
                bounds=replace(
                    ShardKernelBounds(),
                    maximum_shard_bytes=len(self.base[0].canonical_bytes),
                ),
            )
        self.assertKernelCode(caught, "kernel.output-shard-byte-bound")


if __name__ == "__main__":
    unittest.main()
