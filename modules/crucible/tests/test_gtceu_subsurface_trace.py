from __future__ import annotations

from copy import deepcopy
import json
import jsonschema
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from workbench_crucible_gtceu_subsurface import (
    GtceuSubsurfaceTraceValidationError,
    build_gtceu_subsurface_trace,
    parse_gtceu_subsurface_trace,
    write_gtceu_subsurface_trace,
)


class GtceuSubsurfaceTraceTests(unittest.TestCase):
    @staticmethod
    def build(*, complete: bool = True) -> dict:
        return build_gtceu_subsurface_trace(
            adapter_profile={
                "id": "gtceu-1.12.2-2.8.10-subsurface-trace-v1",
                "inventory_id": "crucible-gtceu-worldgen:sha256:" + "a" * 64,
                "impact_inventory_id": "crucible-gtceu-worldgen-impact:sha256:"
                + "b" * 64,
            },
            capture={
                "run_id": "fixture-run",
                "state": "complete" if complete else "partial",
                "runtime_artifact_set_sha256": "c" * 64,
                "world_seed": 42,
                "dimension_id": 0,
                "chunk_window": {
                    "min_chunk_x": 0,
                    "min_chunk_z": 0,
                    "chunk_size_x": 1,
                    "chunk_size_z": 1,
                    "halo_chunks": 1,
                },
            },
            coverage={
                "selected_definitions_complete": complete,
                "position_decisions_complete": complete,
                "truncated": False,
                "selector": "fixture-all-deposits-and-position-decisions",
            },
            deposits=[
                {
                    "deposit_instance_id": "deposit-1",
                    "definition_path": "worldgen/vein/overworld/fluorite.json",
                    "grid_x": 0,
                    "grid_z": 0,
                    "selection_ordinal": 0,
                    "effective_weight": 80,
                    "priority": 0,
                    "count_as_vein": True,
                    "center": {"x": 1, "y": 4, "z": 1},
                    "bounds": {
                        "min_x": 1,
                        "min_y": 4,
                        "min_z": 1,
                        "max_x": 1,
                        "max_y": 4,
                        "max_z": 1,
                    },
                    "rng": {
                        "algorithm": "fixture-xoshiro",
                        "seed_material_sha256": "d" * 64,
                        "lane": "shape-and-filler",
                    },
                    "cache": {"hit": False, "epoch": "fixture-epoch"},
                    "placement": {
                        "candidate_count": 1,
                        "density_rejected_count": 0,
                        "host_rejected_count": 0,
                        "other_rejected_count": 0,
                        "successful_write_count": 1,
                    },
                }
            ],
            decisions=[
                {
                    "decision_id": "decision-1",
                    "deposit_instance_id": "deposit-1",
                    "position": {"x": 1, "y": 4, "z": 1},
                    "outcome": "written",
                    "reason": "host-and-density-accepted",
                    "before_state": "minecraft:stone[variant=stone]",
                    "after_state": "gregtech:ore_fluorite_0[stone_type=stone]",
                    "write_chain_id": "chain-1",
                }
            ],
        )

    def test_content_addressed_trace_closes_exact_position_coverage(self) -> None:
        trace = self.build()
        self.assertEqual(parse_gtceu_subsurface_trace(trace), trace)
        self.assertTrue(trace["coverage"]["position_decisions_complete"])
        self.assertTrue(trace["trace_id"].startswith("crucible-gtceu-subsurface:sha256:"))
        schema = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "schemas/gtceu-subsurface-trace-v1.schema.json"
            ).read_text()
        )
        jsonschema.Draft202012Validator(schema).validate(trace)

    def test_mutation_and_impossible_coverage_fail_closed(self) -> None:
        trace = self.build()
        changed = deepcopy(trace)
        changed["decisions"][0]["reason"] = "changed"
        with self.assertRaisesRegex(GtceuSubsurfaceTraceValidationError, "ID drift"):
            parse_gtceu_subsurface_trace(changed)

        partial = deepcopy(trace)
        partial["capture"]["state"] = "partial"
        with self.assertRaisesRegex(
            GtceuSubsurfaceTraceValidationError, "non-complete capture"
        ):
            parse_gtceu_subsurface_trace(partial)

    def test_position_and_placement_counts_are_exact(self) -> None:
        trace = self.build()
        changed = deepcopy(trace)
        changed["deposits"][0]["placement"]["candidate_count"] = 2
        with self.assertRaisesRegex(GtceuSubsurfaceTraceValidationError, "do not sum"):
            parse_gtceu_subsurface_trace(changed)

        duplicate = deepcopy(trace)
        duplicate["decisions"].append(deepcopy(duplicate["decisions"][0]))
        duplicate["decisions"][1]["decision_id"] = "decision-2"
        with self.assertRaisesRegex(
            GtceuSubsurfaceTraceValidationError, "duplicate deposit-position"
        ):
            parse_gtceu_subsurface_trace(duplicate)

    def test_writer_is_fresh_and_never_replaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "trace.json"
            trace = self.build()
            write_gtceu_subsurface_trace(output, trace)
            self.assertEqual(json.loads(output.read_text()), trace)
            with self.assertRaisesRegex(
                GtceuSubsurfaceTraceValidationError, "already exists"
            ):
                write_gtceu_subsurface_trace(output, trace)
            self.assertEqual(json.loads(output.read_text()), trace)

    def test_assembler_accepts_closed_input_and_refuses_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace = self.build()
            spec = {
                "format": "workbench-crucible-gtceu-subsurface-trace-input-v1",
                **{
                    key: trace[key]
                    for key in (
                        "adapter_profile",
                        "capture",
                        "coverage",
                        "deposits",
                        "decisions",
                    )
                },
            }
            source = root / "input.json"
            output = root / "trace.json"
            source.write_text(json.dumps(spec), encoding="utf-8")
            script = (
                Path(__file__).resolve().parents[1]
                / "tools/assemble_gtceu_subsurface_trace.py"
            )
            completed = subprocess.run(
                [sys.executable, str(script), "--input", str(source), "--output", str(output)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(parse_gtceu_subsurface_trace(json.loads(output.read_text())), trace)
            repeated = subprocess.run(
                [sys.executable, str(script), "--input", str(source), "--output", str(output)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(repeated.returncode, 2)
            self.assertIn("already exists", repeated.stderr)


if __name__ == "__main__":
    unittest.main()
