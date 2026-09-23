from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


FIXTURE = Path(__file__).resolve().parents[1]
TOOL = FIXTURE / "tools" / "summarize_world_studio_log.py"
SPEC = importlib.util.spec_from_file_location("world_studio_log_summary", TOOL)
assert SPEC is not None and SPEC.loader is not None
SUMMARY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUMMARY)


class WorldStudioLogSummaryTests(unittest.TestCase):
    def test_summary_keeps_counts_and_runtime_identity(self) -> None:
        records = [
            {
                "event": "plan.publish",
                "plan_version": 2,
                "plan_hash": "abc123",
                "profile": "test",
            },
            {
                "event": "generator.construct",
                "seed": 41,
                "cave_generator": "example.Caves",
                "ravine_generator": "example.Ravines",
                "shared_sampling": True,
                "chunk_sample_cache_capacity": 256,
                "biome_point_cache_capacity": 8192,
                "watershed_tile_cache_capacity": 16,
                "watershed_algorithm": "priority-flood-d8-v1",
                "watershed_cell_size_blocks": 16,
                "watershed_tile_size_cells": 32,
                "watershed_halo_cells": 24,
            },
            {
                "event": "chunk.generate",
                "mega_region": "craton",
                "lithology": "granite",
                "biome": "example:forest",
                "height_hash": "one",
                "river_columns": 256,
                "stream_columns": 0,
                "water_columns": 200,
                "filled_depression_columns": 3,
                "watershed_max_discharge": 55.0,
                "watershed_max_fill_depth": 2.5,
                "watershed_fingerprint": "aaaa",
                "carved_blocks": 12,
                "chunk_sample_cache_hits": 1,
                "chunk_sample_cache_misses": 1,
                "cached_biome_lookups": 32,
                "point_biome_cache_hits": 80,
                "scalar_biome_samples": 20,
                "watershed_tile_cache_hits": 10,
                "watershed_tile_cache_misses": 2,
                "watershed_tile_cache_evictions": 0,
                "watershed_computed_grid_cells": 12800,
                "elapsed_us": 100,
            },
            {
                "event": "chunk.generate",
                "mega_region": "basin",
                "lithology": "stone",
                "biome": "example:marsh",
                "height_hash": "two",
                "river_columns": 0,
                "stream_columns": 7,
                "water_columns": 7,
                "filled_depression_columns": 0,
                "watershed_max_discharge": 12.0,
                "watershed_max_fill_depth": 0.0,
                "watershed_fingerprint": "bbbb",
                "carved_blocks": 0,
                "chunk_sample_cache_hits": 2,
                "chunk_sample_cache_misses": 2,
                "cached_biome_lookups": 64,
                "point_biome_cache_hits": 140,
                "scalar_biome_samples": 25,
                "watershed_tile_cache_hits": 20,
                "watershed_tile_cache_misses": 3,
                "watershed_tile_cache_evictions": 0,
                "watershed_computed_grid_cells": 19200,
                "elapsed_us": 300,
            },
            {
                "event": "chunk.populate",
                "biome_decoration_invoked": True,
                "feature_placed": False,
                "elapsed_us": 50,
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "latest.log"
            lines = [
                "[Server thread/INFO]: "
                + SUMMARY.PREFIX
                + json.dumps(record, separators=(",", ":"))
                for record in records
            ]
            lines.extend(
                [
                    "[Server thread/INFO]: Done (1.234s)! For help, type help",
                    "[Server thread/INFO]: Stopping the server",
                ]
            )
            log.write_text("\n".join(lines) + "\n", encoding="utf-8")
            result = SUMMARY.summarize_log(log)

        self.assertEqual("abc123", result["plan"]["plan_hash"])
        self.assertEqual(1, result["plan_publications"]["count"])
        self.assertEqual(1, len(result["plan_publications"]["identities"]))
        self.assertEqual("example.Caves", result["generator"]["cave_generator"])
        self.assertTrue(result["runtime"]["ready"])
        self.assertTrue(result["runtime"]["clean_stop_observed"])
        self.assertEqual(2, result["generation"]["chunks"])
        self.assertTrue(result["sampling"]["shared_provider_generator"])
        self.assertEqual(256, result["sampling"]["chunk_sample_cache_capacity"])
        self.assertEqual(
            140,
            result["sampling"]["maximum_observed_counters"][
                "point_biome_cache_hits"
            ],
        )
        self.assertEqual(
            25,
            result["sampling"]["maximum_observed_counters"][
                "scalar_biome_samples"
            ],
        )
        self.assertEqual({"basin": 1, "craton": 1}, result["generation"]["regions"])
        self.assertEqual(1, result["hydrology"]["river_chunks"])
        self.assertEqual(1, result["hydrology"]["river_full_chunks"])
        self.assertEqual(1, result["hydrology"]["stream_chunks"])
        self.assertEqual(207, result["hydrology"]["water_columns"])
        self.assertEqual(3, result["hydrology"]["filled_depression_columns"])
        self.assertEqual(55.0, result["hydrology"]["maximum_discharge"])
        self.assertEqual("priority-flood-d8-v1", result["hydrology"]["algorithm"])
        self.assertEqual(16, result["hydrology"]["grid"]["cell_size_blocks"])
        self.assertEqual(2, result["generation"]["watershed_fingerprints"])
        self.assertEqual(
            3,
            result["sampling"]["maximum_observed_counters"][
                "watershed_tile_cache_misses"
            ],
        )
        self.assertEqual(1, result["carvers"]["chunks_with_underground_air"])
        self.assertEqual(1, result["population"]["native_biome_decoration_invoked"])
        self.assertEqual([], result["prototype_failures"])

    def test_missing_records_is_not_an_empty_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "latest.log"
            log.write_text("ordinary log line\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no WORLDGEN_PROTOTYPE"):
                SUMMARY.summarize_log(log)


if __name__ == "__main__":
    unittest.main()
