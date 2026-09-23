"""Focused tests for Shell transport of Atlas worldgen fingerprints."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_shell.runtime_worldgen_fingerprint import (  # noqa: E402
    RuntimeWorldgenFingerprintError,
    attribute_runtime_worldgen_blocks,
    compare_runtime_worldgen,
    fingerprint_runtime_worldgen,
)


class _AuthorityError(ValueError):
    pass


class RuntimeWorldgenFingerprintTest(unittest.TestCase):
    def test_fingerprint_can_be_retained_without_interpretation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            world = root / "world"
            world.mkdir()
            output = root / "fingerprint.json"
            expected = {
                "format": "atlas-experimental-anvil-worldgen-fingerprint-v1",
                "fingerprint_id": "sha256:test",
            }

            with patch(
                "workbench_shell.runtime_worldgen_fingerprint._atlas_authority",
                return_value=(lambda _world: expected, lambda _a, _b: {}, _AuthorityError),
            ):
                result = fingerprint_runtime_worldgen(
                    root,
                    world,
                    output=output,
                )

            self.assertIs(result, expected)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")),
                expected,
            )

    def test_retention_refuses_to_overwrite_an_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            world = root / "world"
            world.mkdir()
            output = root / "fingerprint.json"
            output.write_text("existing", encoding="utf-8")

            with patch(
                "workbench_shell.runtime_worldgen_fingerprint._atlas_authority",
                return_value=(lambda _world: {}, lambda _a, _b: {}, _AuthorityError),
            ):
                with self.assertRaisesRegex(
                    RuntimeWorldgenFingerprintError,
                    "already exists",
                ):
                    fingerprint_runtime_worldgen(root, world, output=output)

            self.assertEqual(output.read_text(encoding="utf-8"), "existing")

    def test_compare_reads_inputs_and_retains_authority_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            left = root / "left.json"
            right = root / "right.json"
            output = root / "comparison.json"
            left.write_text(json.dumps({"side": "left"}), encoding="utf-8")
            right.write_text(json.dumps({"side": "right"}), encoding="utf-8")
            expected = {
                "format": "atlas-experimental-anvil-worldgen-comparison-v1",
                "comparison_id": "sha256:test",
            }

            def compare(first: dict[str, object], second: dict[str, object]) -> dict[str, object]:
                self.assertEqual(first, {"side": "left"})
                self.assertEqual(second, {"side": "right"})
                return expected

            with patch(
                "workbench_shell.runtime_worldgen_fingerprint._atlas_authority",
                return_value=(lambda _world: {}, compare, _AuthorityError),
            ):
                result = compare_runtime_worldgen(
                    root,
                    left,
                    right,
                    output=output,
                )

            self.assertIs(result, expected)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")),
                expected,
            )

    def test_block_delta_routes_worlds_and_retains_authority_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            left = root / "left"
            right = root / "right"
            output = root / "block-delta.json"
            left.mkdir()
            right.mkdir()
            expected = {
                "format": "atlas-experimental-anvil-block-delta-v2",
                "observation_id": "sha256:test",
            }

            def observe(
                first: Path,
                second: Path,
            ) -> dict[str, object]:
                self.assertEqual(first, left)
                self.assertEqual(second, right)
                return expected

            with patch(
                "workbench_shell.runtime_worldgen_fingerprint."
                "_atlas_block_delta_authority",
                return_value=(observe, _AuthorityError),
            ):
                result = attribute_runtime_worldgen_blocks(
                    root,
                    left,
                    right,
                    output=output,
                )

            self.assertIs(result, expected)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")),
                expected,
            )


if __name__ == "__main__":
    unittest.main()
