#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_crucible_observatory.bundle import load_bundle  # noqa: E402
from workbench_crucible_observatory.synthetic_matrix import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    EVIDENCE_CLASS,
    REGION,
    SYNTHETIC_DISCLAIMER,
    build_synthetic_matrix,
    publish_synthetic_matrix,
    published_summary,
)


class SyntheticMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.matrix = build_synthetic_matrix()

    def test_required_comparison_gates_pass(self) -> None:
        gates = self.matrix.summary["required_gates"]
        self.assertTrue(gates)
        self.assertTrue(all(gates.values()), gates)
        self.assertEqual(
            self.matrix.cases["aa-left"].fingerprint_ids,
            self.matrix.cases["aa-right"].fingerprint_ids,
        )
        self.assertEqual(
            self.matrix.cases["observer-off"].semantic_output_sha256,
            self.matrix.cases["observer-on"].semantic_output_sha256,
        )
        self.assertEqual(
            self.matrix.cases["order-forward"].semantic_output_sha256,
            self.matrix.cases["order-reverse"].semantic_output_sha256,
        )
        self.assertEqual(
            self.matrix.cases["restart-before"].fingerprint_ids,
            self.matrix.cases["restart-after"].fingerprint_ids,
        )
        self.assertNotEqual(
            self.matrix.cases["restart-before"].bundle["run"]["run_id"],
            self.matrix.cases["restart-after"].bundle["run"]["run_id"],
        )

    def test_complete_bundles_are_balanced_zero_drop_and_sealed(self) -> None:
        for case_id, case in self.matrix.cases.items():
            if case_id == "crash-before-seal":
                continue
            bundle = case.bundle
            load_state = bundle["publication"]
            summary = bundle["summary"]
            self.assertEqual("completed", load_state["state"], case_id)
            self.assertIsNotNone(load_state["completion_seal"], case_id)
            self.assertEqual(0, summary["dropped_record_count"], case_id)
            self.assertEqual([], summary["open_span_ids"], case_id)
            self.assertEqual(
                summary["span_enter_count"],
                summary["span_return_count"] + summary["span_throw_count"],
                case_id,
            )

    def test_rng_lanes_and_semantics_are_chunk_order_independent(self) -> None:
        def rng_by_stream(case_id: str) -> dict[str, tuple[str, str]]:
            return {
                record["payload"]["stream_id"]: (
                    record["payload"]["result_sha256"],
                    record["payload"]["rolling_digest"],
                )
                for record in self.matrix.cases[case_id].bundle["records"]
                if record["record_type"] == "rng_observation"
            }

        self.assertEqual(
            rng_by_stream("order-forward"),
            rng_by_stream("order-reverse"),
        )
        self.assertEqual(
            self.matrix.cases["order-forward"].semantic_state,
            self.matrix.cases["order-reverse"].semantic_state,
        )

    def test_primer_event_and_unrelated_decorator_records_are_explicit(self) -> None:
        records = self.matrix.cases["aa-left"].bundle["records"]
        primer = [
            record
            for record in records
            if record["record_type"] == "block_write"
            and record["payload"]["channel"] == "chunk_primer"
        ]
        self.assertEqual(len(REGION), len(primer))

        mutations = [
            record
            for record in records
            if record["record_type"] == "event_dispatch"
            and record["payload"]["boundary"] == "listener_return"
            and record["payload"]["state_before_sha256"]
            != record["payload"]["state_after_sha256"]
        ]
        self.assertEqual(len(REGION), len(mutations))
        self.assertTrue(
            all(
                record["actor"]["mod_id"] == "synthetic_standard_event_handler"
                for record in mutations
            )
        )

        external = [
            record
            for record in records
            if record["record_type"] == "block_write"
            and record["actor"]["mod_id"] == "unrelated_synthetic_decorator"
        ]
        self.assertEqual(2 * len(REGION), len(external))
        chains: dict[str, list[dict]] = {}
        for record in external:
            self.assertEqual("exact", record["actor"]["binding"])
            chains.setdefault(record["payload"]["write_chain_id"], []).append(record)
        self.assertEqual(len(REGION), len(chains))
        for chain in chains.values():
            self.assertEqual(
                ["world_api", "chunk_storage"],
                [record["payload"]["channel"] for record in chain],
            )
            self.assertEqual(
                [False, True],
                [record["payload"]["terminal"] for record in chain],
            )

    def test_crash_is_incomplete_residue_and_never_a_seal(self) -> None:
        bundle = self.matrix.cases["crash-before-seal"].bundle
        self.assertEqual("incomplete", bundle["publication"]["state"])
        self.assertIsNone(bundle["publication"]["completion_seal"])
        self.assertIsNotNone(bundle["publication"]["crash_residue"])
        self.assertTrue(bundle["summary"]["open_span_ids"])
        self.assertEqual(
            bundle["summary"]["open_span_ids"],
            bundle["publication"]["crash_residue"]["open_span_ids"],
        )

    def test_every_artifact_is_labeled_synthetic_contract_evidence(self) -> None:
        self.assertEqual(EVIDENCE_CLASS, self.matrix.summary["evidence_class"])
        self.assertEqual(SYNTHETIC_DISCLAIMER, self.matrix.summary["disclaimer"])
        for comparison in self.matrix.summary["comparisons"]:
            self.assertEqual(EVIDENCE_CLASS, comparison["evidence_class"])
            self.assertEqual(SYNTHETIC_DISCLAIMER, comparison["disclaimer"])
        for case_id, case in self.matrix.cases.items():
            case_summary = self.matrix.summary["cases"][case_id]
            self.assertEqual(EVIDENCE_CLASS, case_summary["evidence_class"])
            self.assertEqual(SYNTHETIC_DISCLAIMER, case_summary["disclaimer"])
            self.assertIn(
                SYNTHETIC_DISCLAIMER, case.bundle["summary"]["limitations"]
            )
            self.assertTrue(
                all(
                    SYNTHETIC_DISCLAIMER in record["coverage"]["limitations"]
                    for record in case.bundle["records"]
                )
            )

    def test_matrix_publication_is_atomic_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "evidence"
            first = publish_synthetic_matrix(self.matrix, root)
            second = publish_synthetic_matrix(self.matrix, root)
            self.assertEqual(first, second)
            summary = published_summary(first)
            self.assertEqual(self.matrix.summary, summary)
            self.assertFalse(any(path.name.endswith(".tmp") for path in root.rglob("*")))
            self.assertFalse(any(path.name.startswith(".matrix-") for path in root.iterdir()))
            for case_id, metadata in summary["cases"].items():
                bundle = load_bundle(first / metadata["bundle_path"])
                self.assertEqual(self.matrix.cases[case_id].bundle, bundle)

    def test_cli_defaults_to_ignored_evidence_and_supports_explicit_root(self) -> None:
        self.assertTrue(str(DEFAULT_OUTPUT_ROOT).startswith(".workbench/evidence/"))
        script = MODULE_ROOT / "tools" / "run_worldgen_observatory_contract_fixtures.py"
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [sys.executable, str(script), "--output-root", temporary],
                check=True,
                capture_output=True,
                text=True,
            )
            result = json.loads(completed.stdout)
            self.assertEqual(EVIDENCE_CLASS, result["evidence_class"])
            self.assertEqual("pass", result["overall_outcome"])
            output = Path(result["output_directory"])
            self.assertTrue(output.is_dir())
            self.assertEqual(self.matrix.summary, published_summary(output))


if __name__ == "__main__":
    unittest.main()
