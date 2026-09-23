from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

import workbench_crucible_kernel.equivalence as equivalence_module  # noqa: E402
from workbench_crucible_kernel.equivalence import (  # noqa: E402
    PartitionAssemblyEquivalenceReceipt,
    verify_partition_assembly_equivalence,
)
from workbench_crucible_kernel import (  # noqa: E402
    AuthorityBinding,
    KernelValidationError,
    OwnerPolicyBinding,
    ShardArtifact,
    ShardCoordinate,
    ShardKernelConfig,
    assemble_graph_partitions,
)
from workbench_crucible import ValidatedRecord, seal_record  # noqa: E402
from workbench_crucible.synthetic import build_synthetic_publication  # noqa: E402


class _RaisingEquality:
    def __eq__(self, other):
        del other
        raise AssertionError("hostile wrapper equality was invoked")

    def __hash__(self):
        raise AssertionError("hostile wrapper hashing was invoked")


class _ExplosiveTuple(tuple):
    def __len__(self):
        raise AssertionError("hostile tuple length was invoked")

    def __iter__(self):
        raise AssertionError("hostile tuple iteration was invoked")


class CrucibleKernelEquivalenceV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        publication = build_synthetic_publication()
        graph = publication.record("graph-revision")
        owner = AuthorityBinding(**graph["recipe_owner"])
        cls.config = ShardKernelConfig(
            graph["record_schema_object_descriptor_ids"][0],
            OwnerPolicyBinding(graph["total_order_policy_id"], owner),
            OwnerPolicyBinding(graph["partition_policy_id"], owner),
            2,
        )
        by_key = {
            record.to_dict()["logical_key"]: record
            for record in publication.records
            if record.kind == "graph-record"
            and record.to_dict()["resolution_id"] == "synthetic.exact"
        }
        alpha = by_key["node:alpha"]
        beta = by_key["node:beta"]

        def derived(key: str, *, variant: bool = False):
            candidate = beta.to_dict()
            candidate.pop("id")
            candidate["logical_key"] = key
            if variant:
                candidate["body"]["node_kind"] = "synthetic.subject.variant"
            return seal_record(candidate)

        cls.prior_rows = (alpha, beta, derived("node:delta"), derived("node:gamma"))
        cls.target_rows = (
            alpha,
            derived("node:beta", variant=True),
            derived("node:delta"),
            derived("node:gamma"),
        )
        cls.prior = cls.assemble(cls.prior_rows)
        cls.clean = cls.assemble(cls.target_rows)
        cls.incremental = cls.assemble(
            reversed(cls.target_rows), prior=cls.prior.shards
        )

    @staticmethod
    def logical_key_port(record):
        return record.to_dict()["logical_key"]

    @staticmethod
    def partition_key_port(record, logical_key):
        del logical_key
        return f"synthetic.{record.to_dict()['body']['record_kind']}"

    @classmethod
    def assemble(cls, rows, *, prior=()):
        return assemble_graph_partitions(
            rows,
            config=cls.config,
            logical_key_port=cls.logical_key_port,
            partition_key_port=cls.partition_key_port,
            prior_shards=prior,
        )

    def assertCode(self, caught, code: str) -> None:
        self.assertEqual(code, caught.exception.code)

    def test_exact_equivalence_returns_stable_immutable_receipt(self) -> None:
        first = verify_partition_assembly_equivalence(self.clean, self.incremental)
        second = verify_partition_assembly_equivalence(self.clean, self.incremental)
        self.assertIsInstance(first, PartitionAssemblyEquivalenceReceipt)
        self.assertEqual(first, second)
        self.assertEqual(self.incremental.reused_coordinates, first.reused_coordinates)
        self.assertEqual(64, len(first.canonical_projection_sha256))
        self.assertEqual(64, len(first.receipt_sha256))
        self.assertIn(
            b'"format":"workbench-crucible-partition-assembly-identity-v1"',
            first.canonical_projection_bytes,
        )
        with self.assertRaises(FrozenInstanceError):
            first.receipt_sha256 = "0" * 64

    def test_identity_bytes_and_fields_must_match(self) -> None:
        with self.assertRaises(KernelValidationError) as caught:
            verify_partition_assembly_equivalence(self.clean, self.prior)
        self.assertCode(caught, "kernel.equivalence-mismatch")
        self.assertTrue(caught.exception.path.startswith("/identity/shards/0/"))

        changed_schema = replace(
            self.incremental,
            record_schema_object_descriptor_id="object-descriptor:sha256:" + "1" * 64,
        )
        with self.assertRaises(KernelValidationError) as caught:
            verify_partition_assembly_equivalence(self.clean, changed_schema)
        self.assertCode(caught, "kernel.equivalence-invalid-artifact")
        self.assertIn("object_descriptor", caught.exception.path)

    def test_malformed_artifact_and_inconsistent_derived_fields_fail_closed(self) -> None:
        first = self.incremental.shards[0]
        malformed = replace(first, ndjson_bytes=first.ndjson_bytes + b"\n")
        candidate = replace(
            self.incremental,
            shards=(malformed, *self.incremental.shards[1:]),
        )
        with self.assertRaises(KernelValidationError) as caught:
            verify_partition_assembly_equivalence(self.clean, candidate)
        self.assertCode(caught, "kernel.equivalence-invalid-artifact")

        counts = replace(
            self.incremental.record_counts,
            nodes=self.incremental.record_counts.nodes + 1,
        )
        candidate = replace(self.incremental, record_counts=counts)
        with self.assertRaises(KernelValidationError) as caught:
            verify_partition_assembly_equivalence(self.clean, candidate)
        self.assertCode(caught, "kernel.equivalence-invalid-assembly")
        self.assertEqual("/incremental/record_counts", caught.exception.path)

    def test_hostile_validated_record_wrapper_metadata_is_discarded(self) -> None:
        raising = _RaisingEquality()
        retained = self.incremental.shards[-1]
        forged_descriptor = ValidatedRecord(
            id=raising,
            kind=raising,
            format=raising,
            schema_id=raising,
            canonical_bytes=retained.object_descriptor.canonical_bytes,
            references=raising,
        )
        forged = ShardArtifact(
            retained.coordinate,
            retained.record_ids,
            retained.logical_keys,
            retained.ndjson_bytes,
            forged_descriptor,
            retained.partition,
        )
        candidate = replace(
            self.incremental,
            shards=(*self.incremental.shards[:-1], forged),
        )
        receipt = verify_partition_assembly_equivalence(self.clean, candidate)
        self.assertEqual(self.incremental.reused_coordinates, receipt.reused_coordinates)

    def test_forced_wrapper_mutation_rejects_without_dynamic_equality(self) -> None:
        candidate = replace(self.incremental)
        object.__setattr__(
            candidate,
            "record_schema_object_descriptor_id",
            _RaisingEquality(),
        )
        with self.assertRaises(KernelValidationError) as caught:
            verify_partition_assembly_equivalence(self.clean, candidate)
        self.assertCode(caught, "kernel.equivalence-invalid-assembly")
        self.assertEqual(
            "/incremental/record_schema_object_descriptor_id",
            caught.exception.path,
        )

    def test_reuse_metadata_is_validated_but_excluded_from_identity(self) -> None:
        no_reuse = replace(self.incremental, reused_coordinates=())
        first = verify_partition_assembly_equivalence(self.clean, self.incremental)
        second = verify_partition_assembly_equivalence(self.clean, no_reuse)
        self.assertEqual(
            first.canonical_projection_sha256,
            second.canonical_projection_sha256,
        )
        self.assertNotEqual(first.receipt_sha256, second.receipt_sha256)
        self.assertEqual((), second.reused_coordinates)

        missing = ShardCoordinate("synthetic.node", "node", 99)
        invalid = replace(self.incremental, reused_coordinates=(missing,))
        with self.assertRaises(KernelValidationError) as caught:
            verify_partition_assembly_equivalence(self.clean, invalid)
        self.assertCode(caught, "kernel.equivalence-invalid-reuse")

        claimed_clean_reuse = replace(
            self.clean,
            reused_coordinates=(self.clean.shards[0].coordinate,),
        )
        with self.assertRaises(KernelValidationError) as caught:
            verify_partition_assembly_equivalence(claimed_clean_reuse, self.incremental)
        self.assertCode(caught, "kernel.equivalence-clean-reuse")

    def test_shard_bound_fails_before_duplicate_or_artifact_walk(self) -> None:
        oversized = replace(
            self.incremental,
            shards=(self.incremental.shards[0],) * 4_097,
        )
        with self.assertRaises(KernelValidationError) as caught:
            verify_partition_assembly_equivalence(self.clean, oversized)
        self.assertCode(caught, "kernel.equivalence-bound")
        self.assertEqual("/incremental/shards", caught.exception.path)

    def test_artifact_byte_bounds_fail_before_validation_or_parsing(self) -> None:
        first = self.clean.shards[0]
        total_bytes = sum(
            len(shard.ndjson_bytes) + len(shard.object_descriptor.canonical_bytes)
            for shard in self.clean.shards
        )
        cases = (
            (
                "ndjson",
                self.clean,
                "_MAX_SHARD_BYTES",
                len(first.ndjson_bytes) - 1,
                "/clean/shards/0/ndjson_bytes",
            ),
            (
                "descriptor",
                self.clean,
                "_MAX_DESCRIPTOR_BYTES",
                len(first.object_descriptor.canonical_bytes) - 1,
                "/clean/shards/0/object_descriptor",
            ),
            (
                "aggregate",
                self.clean,
                "_MAX_CANONICAL_BYTES",
                total_bytes - 1,
                f"/clean/shards/{len(self.clean.shards) - 1}",
            ),
        )
        for label, candidate, bound_name, bound, expected_path in cases:
            with self.subTest(label=label):
                with patch.object(equivalence_module, bound_name, bound), patch.object(
                    equivalence_module,
                    "_validate_prior_artifact",
                    side_effect=AssertionError("artifact validator was reached"),
                ) as validator:
                    with self.assertRaises(KernelValidationError) as caught:
                        verify_partition_assembly_equivalence(
                            candidate, self.incremental
                        )
                validator.assert_not_called()
                self.assertCode(caught, "kernel.equivalence-bound")
                self.assertEqual(expected_path, caught.exception.path)

    def test_row_bounds_fail_during_offset_scan_before_validation(self) -> None:
        first = self.clean.shards[0]
        cases = (
            ("row-bytes", b"xxxx\n", "_MAX_ROW_BYTES", 3, "/clean/shards/0/rows/0"),
            (
                "row-count",
                b"{}\n{}\n{}\n",
                "_MAX_RECORDS",
                2,
                "/clean/shards/0/ndjson_bytes",
            ),
        )
        for label, raw, bound_name, bound, expected_path in cases:
            with self.subTest(label=label):
                forged = replace(first, ndjson_bytes=raw)
                candidate = replace(
                    self.clean,
                    shards=(forged, *self.clean.shards[1:]),
                )
                with patch.object(equivalence_module, bound_name, bound), patch.object(
                    equivalence_module,
                    "_validate_prior_artifact",
                    side_effect=AssertionError("artifact validator was reached"),
                ) as validator:
                    with self.assertRaises(KernelValidationError) as caught:
                        verify_partition_assembly_equivalence(
                            candidate, self.incremental
                        )
                validator.assert_not_called()
                self.assertCode(caught, "kernel.equivalence-bound")
                self.assertEqual(expected_path, caught.exception.path)

    def test_malformed_framing_and_type_fail_during_preflight(self) -> None:
        first = self.clean.shards[0]
        cases = (
            ("unterminated", replace(first, ndjson_bytes=b"{}"), "/clean/shards/0/ndjson_bytes"),
            ("empty-row", replace(first, ndjson_bytes=b"{}\n\n"), "/clean/shards/0/ndjson_bytes"),
            ("wrong-type", object(), "/clean/shards/0"),
        )
        for label, forged, expected_path in cases:
            with self.subTest(label=label):
                candidate = replace(
                    self.clean,
                    shards=(forged, *self.clean.shards[1:]),
                )
                with patch.object(
                    equivalence_module,
                    "_validate_prior_artifact",
                    side_effect=AssertionError("artifact validator was reached"),
                ) as validator:
                    with self.assertRaises(KernelValidationError) as caught:
                        verify_partition_assembly_equivalence(
                            candidate, self.incremental
                        )
                validator.assert_not_called()
                self.assertCode(caught, "kernel.equivalence-invalid-artifact")
                self.assertEqual(expected_path, caught.exception.path)

    def test_row_metadata_cardinality_fails_before_artifact_validation(self) -> None:
        first = self.clean.shards[0]
        raising = _RaisingEquality()
        cases = (
            (
                "record-ids",
                replace(
                    first,
                    record_ids=(raising,)
                    * (equivalence_module._MAX_RECORDS + 1),
                ),
                "/clean/shards/0/record_ids",
            ),
            (
                "logical-keys",
                replace(
                    first,
                    logical_keys=(raising,) * (len(first.logical_keys) + 1),
                ),
                "/clean/shards/0/logical_keys",
            ),
            (
                "tuple-subclass",
                replace(first, record_ids=_ExplosiveTuple(first.record_ids)),
                "/clean/shards/0/record_ids",
            ),
        )
        for label, forged, expected_path in cases:
            with self.subTest(label=label):
                candidate = replace(
                    self.clean,
                    shards=(forged, *self.clean.shards[1:]),
                )
                with patch.object(
                    equivalence_module,
                    "_validate_prior_artifact",
                    side_effect=AssertionError("artifact validator was reached"),
                ) as validator:
                    with self.assertRaises(KernelValidationError) as caught:
                        verify_partition_assembly_equivalence(
                            candidate, self.incremental
                        )
                validator.assert_not_called()
                self.assertCode(caught, "kernel.equivalence-invalid-artifact")
                self.assertEqual(expected_path, caught.exception.path)

    def test_reuse_metadata_cardinality_and_type_fail_before_artifact_walk(self) -> None:
        raising = _RaisingEquality()
        cases = (
            (
                "too-many",
                (raising,) * (equivalence_module._MAX_SHARDS + 1),
            ),
            ("tuple-subclass", _ExplosiveTuple()),
        )
        for label, reuse in cases:
            with self.subTest(label=label):
                candidate = replace(self.clean, reused_coordinates=reuse)
                with patch.object(
                    equivalence_module,
                    "_validate_prior_artifact",
                    side_effect=AssertionError("artifact validator was reached"),
                ) as validator:
                    with self.assertRaises(KernelValidationError) as caught:
                        verify_partition_assembly_equivalence(
                            candidate, self.incremental
                        )
                validator.assert_not_called()
                self.assertCode(caught, "kernel.equivalence-invalid-reuse")
                self.assertEqual(
                    "/clean/reused_coordinates", caught.exception.path
                )

        candidate = replace(
            self.clean,
            reused_coordinates=(ShardCoordinate("x" * 8193, "node", 0),),
        )
        with self.assertRaises(KernelValidationError) as caught:
            verify_partition_assembly_equivalence(candidate, self.incremental)
        self.assertCode(caught, "kernel.equivalence-invalid-reuse")
        self.assertEqual(
            "/clean/reused_coordinates/0/partition_key",
            caught.exception.path,
        )


if __name__ == "__main__":
    unittest.main()
