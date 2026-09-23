from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


FIXTURE = Path(__file__).resolve().parents[1]
TOOL = FIXTURE / "tools" / "compare_world_studio_logs.py"
SPEC = importlib.util.spec_from_file_location("world_studio_log_comparison", TOOL)
assert SPEC is not None and SPEC.loader is not None
COMPARISON = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPARISON)


def write_log(path: Path, records: list[dict]) -> None:
    lines = [
        "[Server thread/INFO]: "
        + COMPARISON.PREFIX
        + json.dumps(record, separators=(",", ":"))
        for record in records
    ]
    lines.extend(
        (
            '[Server thread/INFO]: Done (1.000s)! For help, type "help"',
            "[Server thread/INFO]: Stopping the server",
        )
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def records(height_hash: str = "same", elapsed: int = 100) -> list[dict]:
    identity = {
        "profile": "test",
        "plan_version": 2,
        "plan_hash": "abc",
    }
    return [
        {"event": "plan.publish", **identity},
        {
            "event": "generator.construct",
            "seed": 41,
            "dimension": 0,
            "world_type": "wb_proto",
            "provider": "example.Provider",
            "generator": "example.Generator",
            **identity,
            "cave_generator": "example.Caves",
            "ravine_generator": "example.Ravines",
        },
        {
            "event": "chunk.generate",
            "dimension": 0,
            "chunk_x": 1,
            "chunk_z": -2,
            "height_hash": height_hash,
            "elapsed_us": elapsed,
            "chunk_sample_cache_hits": 1,
        },
        {
            "event": "chunk.populate",
            "dimension": 0,
            "chunk_x": 1,
            "chunk_z": -2,
            "feature_placed": True,
            "elapsed_us": elapsed,
        },
    ]


class WorldStudioLogComparisonTests(unittest.TestCase):
    def test_only_declared_telemetry_may_differ(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / "baseline.log"
            candidate = Path(directory) / "candidate.log"
            write_log(baseline, records(elapsed=100))
            candidate_records = records(elapsed=800)
            candidate_records[2]["point_biome_cache_hits"] = 70
            candidate_records[2]["watershed_tile_cache_hits"] = 90
            write_log(candidate, candidate_records)

            result = COMPARISON.compare_logs(baseline, candidate)

        self.assertTrue(result["equivalent"])
        self.assertTrue(result["runtime"]["usable"])
        self.assertTrue(result["records"]["chunk.generate"]["equivalent"])

    def test_generation_change_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / "baseline.log"
            candidate = Path(directory) / "candidate.log"
            write_log(baseline, records())
            write_log(candidate, records(height_hash="changed"))

            result = COMPARISON.compare_logs(baseline, candidate)

        self.assertFalse(result["equivalent"])
        changed = result["records"]["chunk.generate"]["changed_chunks"]
        self.assertEqual("height_hash", changed[0]["fields"][0]["field"])


if __name__ == "__main__":
    unittest.main()
