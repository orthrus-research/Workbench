from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


FIXTURE = Path(__file__).resolve().parents[1]
TOOL = FIXTURE / "tools" / "summarize_world_studio_jfr.py"
SPEC = importlib.util.spec_from_file_location("world_studio_jfr_summary", TOOL)
assert SPEC is not None and SPEC.loader is not None
SUMMARY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUMMARY)


def event(event_type: str, duration: str, **values):
    return {
        "type": event_type,
        "values": {"duration": duration, "planVersion": 2, "planHash": "abc", **values},
    }


class WorldStudioJfrSummaryTests(unittest.TestCase):
    def test_custom_events_are_grouped_without_claiming_unrecorded_requests(self):
        document = {
            "recording": {
                "events": [
                    event(
                        SUMMARY.EVENT_NAMES[0],
                        "PT0.000010S",
                        cacheHit=False,
                        cacheSize=1,
                    ),
                    event(
                        SUMMARY.EVENT_NAMES[0],
                        "PT0.000002S",
                        cacheHit=True,
                        cacheSize=1,
                    ),
                    event(
                        SUMMARY.EVENT_NAMES[1],
                        "PT0.000100S",
                        stage="primer.base",
                    ),
                    event(
                        SUMMARY.EVENT_NAMES[1],
                        "PT0.000300S",
                        stage="primer.base",
                    ),
                    event(
                        SUMMARY.EVENT_NAMES[2],
                        "PT0.000004S",
                        width=16,
                        height=16,
                        coordinateScale=1,
                        chunkFastPath=True,
                    ),
                    event(
                        SUMMARY.EVENT_NAMES[2],
                        "PT0.000001S",
                        width=1,
                        height=1,
                        coordinateScale=1,
                        chunkFastPath=False,
                    ),
                    event(
                        SUMMARY.EVENT_NAMES[3],
                        "PT0.002000S",
                        algorithmVersion="priority-flood-d8-v1",
                        computedGridCells=6400,
                        filledCoreCells=9,
                        maximumFillDepth=3.5,
                        maximumDischarge=81.0,
                        cacheSize=2,
                    ),
                ]
            }
        }

        result = SUMMARY.summarize_document(document)

        self.assertEqual(2, result["chunk_sampling"]["requests"])
        self.assertEqual(1, result["chunk_sampling"]["cache_hits"])
        self.assertEqual(10, result["chunk_sampling"]["cache_miss_latency"]["p95_us"])
        self.assertEqual(2, result["chunk_stages"]["primer.base"]["count"])
        self.assertEqual(300, result["chunk_stages"]["primer.base"]["latency"]["p95_us"])
        self.assertEqual(1, result["biome_areas"]["chunk_fast_path_requests"])
        self.assertEqual(1, result["biome_areas"]["recorded_point_requests"])
        self.assertEqual(2, len(result["biome_areas"]["request_shapes"]))
        self.assertEqual(1, result["watershed_tiles"]["builds"])
        self.assertEqual(2000, result["watershed_tiles"]["latency"]["p95_us"])
        self.assertEqual(6400, result["watershed_tiles"]["computed_grid_cells"])
        self.assertEqual(3.5, result["watershed_tiles"]["maximum_fill_depth"])
        self.assertEqual([{"plan_version": 2, "plan_hash": "abc"}], result["plan_identities"])

    def test_missing_event_array_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "recording.events"):
            SUMMARY.summarize_document({"recording": {}})


if __name__ == "__main__":
    unittest.main()
