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
sys.path.insert(0, str(MODULE_ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from evaluate_cleanroom_worldgen_matrix import (  # noqa: E402
    EXACT_RUNTIME_V2_ALIASES,
    evaluate_exact_runtime_v2_matrix,
)
from test_cleanroom_matrix import fixture_document, matrix_inputs  # noqa: E402
from workbench_crucible_observatory import (  # noqa: E402
    CaptureValidationError,
    canonical_json_bytes,
    write_cleanroom_bundle_projection,
)
from workbench_crucible_observatory.cleanroom_matrix import (  # noqa: E402
    PROJECTION_MATRIX_EVALUATION_SCHEMA,
)


class ExactRuntimeV2ProjectionMatrixToolTests(unittest.TestCase):
    def _inputs(self, root: Path) -> dict[str, Path]:
        executions, _ = matrix_inputs()
        paths = {
            "aa_1_projection": root / "aa-1-projection.json",
            "aa_1_result": root / "aa-1-result.json",
            "aa_2_projection": root / "aa-2-projection.json",
            "aa_2_result": root / "aa-2-result.json",
            "order_reverse_projection": root / "order-reverse-projection.json",
            "order_reverse_result": root / "order-reverse-result.json",
            "observer_off_result": root / "observer-off-result.json",
            "crash_projection": root / "crash-projection.json",
        }
        write_cleanroom_bundle_projection(
            paths["aa_1_projection"],
            executions["exec-forward-a"].canonical_bundle,
            execution_id="exec-aa1",
        )
        write_cleanroom_bundle_projection(
            paths["aa_2_projection"],
            executions["exec-forward-b"].canonical_bundle,
            execution_id="exec-aa2",
        )
        write_cleanroom_bundle_projection(
            paths["order_reverse_projection"],
            executions["exec-reverse"].canonical_bundle,
            execution_id="exec-reverse",
        )
        write_cleanroom_bundle_projection(
            paths["crash_projection"],
            executions["exec-crash"].canonical_bundle,
            execution_id="exec-crash",
        )
        paths["aa_1_result"].write_bytes(
            canonical_json_bytes(fixture_document()) + b"\n"
        )
        paths["aa_2_result"].write_bytes(
            canonical_json_bytes(fixture_document()) + b"\n"
        )
        paths["order_reverse_result"].write_bytes(
            canonical_json_bytes(fixture_document(order="reverse")) + b"\n"
        )
        paths["observer_off_result"].write_bytes(
            canonical_json_bytes(fixture_document()) + b"\n"
        )
        return paths

    def test_loads_exact_inputs_and_atomically_publishes_canonical_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = self._inputs(root)
            output = root / "nested" / "matrix-evaluation.json"

            first = evaluate_exact_runtime_v2_matrix(**inputs, output=output)
            first_bytes = output.read_bytes()
            second = evaluate_exact_runtime_v2_matrix(**inputs, output=output)

            self.assertEqual(first, second)
            self.assertEqual(PROJECTION_MATRIX_EVALUATION_SCHEMA, first["schema"])
            self.assertEqual("passed", first["matrix_state"])
            self.assertEqual(
                [
                    {"case_id": case_id, "execution_id": execution_id}
                    for case_id, execution_id in EXACT_RUNTIME_V2_ALIASES.items()
                ],
                first["case_aliases"],
            )
            self.assertEqual(canonical_json_bytes(first) + b"\n", first_bytes)
            self.assertEqual(first_bytes, output.read_bytes())
            self.assertFalse(
                any(path.name.endswith(".tmp") for path in output.parent.iterdir())
            )

    def test_foreign_projection_is_rejected_before_replacing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = self._inputs(root)
            output = root / "matrix-evaluation.json"
            output.write_bytes(b"existing-output\n")
            inputs["aa_1_projection"] = inputs["aa_2_projection"]

            with self.assertRaisesRegex(
                CaptureValidationError,
                "execution identity mismatch",
            ):
                evaluate_exact_runtime_v2_matrix(**inputs, output=output)
            self.assertEqual(b"existing-output\n", output.read_bytes())

    def test_cli_reports_published_identity_and_gate_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = self._inputs(root)
            output = root / "matrix-evaluation.json"
            script = MODULE_ROOT / "tools" / "evaluate_cleanroom_worldgen_matrix.py"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--aa-1-projection",
                    str(inputs["aa_1_projection"]),
                    "--aa-1-result",
                    str(inputs["aa_1_result"]),
                    "--aa-2-projection",
                    str(inputs["aa_2_projection"]),
                    "--aa-2-result",
                    str(inputs["aa_2_result"]),
                    "--order-reverse-projection",
                    str(inputs["order_reverse_projection"]),
                    "--order-reverse-result",
                    str(inputs["order_reverse_result"]),
                    "--observer-off-result",
                    str(inputs["observer_off_result"]),
                    "--crash-projection",
                    str(inputs["crash_projection"]),
                    "--output",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            report = json.loads(completed.stdout)
            evaluation = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("passed", report["matrix_state"])
            self.assertEqual(evaluation["evaluation_id"], report["evaluation_id"])
            self.assertEqual(str(output), report["output"])


if __name__ == "__main__":
    unittest.main()
