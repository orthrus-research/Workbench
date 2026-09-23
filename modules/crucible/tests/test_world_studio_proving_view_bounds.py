from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
CRUCIBLE_SOURCE = ROOT / "modules/crucible/src"
if str(CRUCIBLE_SOURCE) not in sys.path:
    sys.path.insert(0, str(CRUCIBLE_SOURCE))

from workbench_crucible_worldgen import (  # noqa: E402
    MAX_WORLD_STUDIO_QUERY_ROW_BYTES,
    MAX_WORLD_STUDIO_QUERY_ROWS,
    MAX_WORLD_STUDIO_RESULT_BYTES,
    MAX_WORLD_STUDIO_RESULT_DEPTH,
    MAX_WORLD_STUDIO_RESULT_NODES,
    WorldStudioProvingViewError,
)
from workbench_crucible_worldgen import view as proving_view  # noqa: E402


def _candidate(*, rows: list[object] | None = None, **extra: object) -> dict:
    return {
        "format": "workbench-worldgen-query-result-v1",
        "graph_set_revision_id": "graph-set-revision:sha256:" + "0" * 64,
        "id": "worldgen-query-result:sha256:" + "0" * 64,
        "kind": "worldgen-query-result",
        "query": {"query": "capture-health"},
        "raw_archive_open_count": 0,
        "results": [] if rows is None else rows,
        "schema_version": 1,
        "truncated": False,
        **extra,
    }


class WorldStudioProvingViewBoundsTests(unittest.TestCase):
    def _assert_preflight_rejects(
        self,
        candidate: dict,
        pattern: str,
    ) -> None:
        with patch.object(
            proving_view,
            "_detached",
            side_effect=AssertionError("owner canonicalization was reached"),
        ):
            with self.assertRaisesRegex(WorldStudioProvingViewError, pattern):
                proving_view._validate_query_result(
                    candidate,
                    "graph-set-revision:sha256:" + "0" * 64,
                    {"query": "capture-health"},
                )

    def test_declared_bounds_run_before_owner_canonicalization(self) -> None:
        deep: object = "leaf"
        for _ in range(MAX_WORLD_STUDIO_RESULT_DEPTH + 2):
            deep = [deep]

        cycle: dict[str, object] = {}
        cycle["self"] = cycle

        cases = (
            (
                "rows",
                _candidate(rows=[{} for _ in range(MAX_WORLD_STUDIO_QUERY_ROWS + 1)]),
                "row bound",
            ),
            (
                "row-bytes",
                _candidate(
                    rows=[{"value": "x" * (MAX_WORLD_STUDIO_QUERY_ROW_BYTES + 1)}]
                ),
                "byte bound",
            ),
            (
                "result-bytes",
                _candidate(padding="x" * (MAX_WORLD_STUDIO_RESULT_BYTES + 1)),
                "byte bound",
            ),
            ("depth", _candidate(rows=[{"value": deep}]), "depth bound"),
            (
                "nodes",
                _candidate(rows=[{"value": [0] * (MAX_WORLD_STUDIO_RESULT_NODES + 1)}]),
                "node bound",
            ),
            ("cycle", _candidate(rows=[cycle]), "cyclic object"),
            ("custom", _candidate(rows=[{"value": object()}]), "ordinary JSON"),
            ("key", _candidate(rows=[{1: "value"}]), "non-string object key"),
            ("surrogate", _candidate(rows=[{"value": "\ud800"}]), "surrogate"),
            (
                "integer",
                _candidate(rows=[{"value": 2**63}]),
                "signed 64-bit",
            ),
        )
        for label, candidate, pattern in cases:
            with self.subTest(boundary=label):
                self._assert_preflight_rejects(candidate, pattern)

    def test_ordinary_bounded_shape_reaches_owner_canonicalization(self) -> None:
        marker = AssertionError("bounded shape reached canonicalization")
        with patch.object(proving_view, "_detached", side_effect=marker):
            with self.assertRaisesRegex(AssertionError, str(marker)):
                proving_view._validate_query_result(
                    _candidate(rows=[{"category_id": "fixture.category"}]),
                    "graph-set-revision:sha256:" + "0" * 64,
                    {"query": "capture-health"},
                )


if __name__ == "__main__":
    unittest.main()
