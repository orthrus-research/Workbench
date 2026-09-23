"""Focused tests for stopped-world binding to Atlas Anvil authority."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
PROJECT_INTELLIGENCE_ROOT = REPOSITORY_ROOT / "modules/project-intelligence/src"
sys.path.insert(0, str(PROJECT_INTELLIGENCE_ROOT))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_shell.runtime_anvil_observe import (  # noqa: E402
    RuntimeAnvilObserveError,
    observe_runtime_anvil_worlds,
)


def _observation(identifier: str) -> dict[str, object]:
    return {
        "format": "atlas-experimental-anvil-region-observation-v1",
        "schema_version": 1,
        "observation_id": identifier,
        "state": "no-findings-observed",
        "facts": {"summary": {
            "region_file_count": 1,
            "region_bytes": 8192,
            "allocated_chunk_count": 1,
        }},
    }


class RuntimeAnvilObserveTest(unittest.TestCase):
    def test_no_saves_directory_returns_no_observations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / ".minecraft"
            runtime.mkdir()
            authority = Mock()
            with patch(
                "workbench_shell.runtime_anvil_observe._atlas_authority",
                return_value=(authority, ValueError),
            ):
                result = observe_runtime_anvil_worlds(
                    REPOSITORY_ROOT,
                    runtime,
                )

            self.assertEqual(result, [])
            authority.assert_not_called()

    def test_worlds_are_sorted_and_results_remain_atlas_owned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / ".minecraft"
            saves = runtime / "saves"
            for name in ("Zulu", "Alpha"):
                (saves / name).mkdir(parents=True)
            authority = Mock(side_effect=(
                _observation("sha256:" + ("1" * 64)),
                _observation("sha256:" + ("2" * 64)),
            ))
            with patch(
                "workbench_shell.runtime_anvil_observe._atlas_authority",
                return_value=(authority, ValueError),
            ):
                result = observe_runtime_anvil_worlds(
                    REPOSITORY_ROOT,
                    runtime,
                )

            self.assertEqual(
                [item["world_name"] for item in result],
                ["Alpha", "Zulu"],
            )
            self.assertEqual(
                [item["observation"]["observation_id"] for item in result],
                ["sha256:" + ("1" * 64), "sha256:" + ("2" * 64)],
            )
            self.assertEqual(
                [call.args[0].name for call in authority.call_args_list],
                ["Alpha", "Zulu"],
            )

    def test_symbolic_link_world_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / ".minecraft"
            saves = runtime / "saves"
            outside = root / "outside"
            saves.mkdir(parents=True)
            outside.mkdir()
            (saves / "linked").symlink_to(outside, target_is_directory=True)
            with patch(
                "workbench_shell.runtime_anvil_observe._atlas_authority",
                return_value=(lambda _world: {}, ValueError),
            ):
                with self.assertRaisesRegex(
                    RuntimeAnvilObserveError,
                    "contains a symbolic link",
                ):
                    observe_runtime_anvil_worlds(
                        REPOSITORY_ROOT,
                        runtime,
                    )

    def test_one_world_failure_does_not_erase_later_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / ".minecraft"
            saves = runtime / "saves"
            for name in ("Alpha", "Zulu"):
                (saves / name).mkdir(parents=True)
            authority = Mock(side_effect=(
                ValueError("broken region"),
                _observation("sha256:" + ("2" * 64)),
            ))
            with patch(
                "workbench_shell.runtime_anvil_observe._atlas_authority",
                return_value=(authority, ValueError),
            ):
                result = observe_runtime_anvil_worlds(
                    REPOSITORY_ROOT,
                    runtime,
                )

            self.assertEqual(result[0]["world_name"], "Alpha")
            self.assertEqual(
                result[0]["error"]["kind"],
                "atlas-observation-failed",
            )
            self.assertEqual(
                result[1]["observation"]["observation_id"],
                "sha256:" + ("2" * 64),
            )

    def test_aggregate_chunk_limit_bounds_multi_world_collection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / ".minecraft"
            saves = runtime / "saves"
            for name in ("Alpha", "Zulu"):
                (saves / name).mkdir(parents=True)
            authority = Mock(side_effect=(
                _observation("sha256:" + ("1" * 64)),
                _observation("sha256:" + ("2" * 64)),
            ))
            with (
                patch(
                    "workbench_shell.runtime_anvil_observe._atlas_authority",
                    return_value=(authority, ValueError),
                ),
                patch(
                    "workbench_shell.runtime_anvil_observe.MAX_AGGREGATE_CHUNKS",
                    1,
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeAnvilObserveError,
                    "aggregate Anvil observation limit",
                ):
                    observe_runtime_anvil_worlds(
                        REPOSITORY_ROOT,
                        runtime,
                    )


if __name__ == "__main__":
    unittest.main()
