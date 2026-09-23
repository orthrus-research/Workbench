#!/usr/bin/env python3
"""Evaluate the exact-runtime-v2 Cleanroom worldgen projection matrix.

This entry point is deliberately bounded to the five execution identities used
by the exact runtime matrix.  It admits four small bundle projections and four
completed fixture-result artifacts, delegates every comparison to the generic
Crucible projection evaluator, and atomically publishes only the resulting
evaluation document.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_crucible_observatory import (  # noqa: E402
    CaptureValidationError,
    CleanroomProjectionExecution,
    canonical_json_bytes,
    evaluate_cleanroom_projection_matrix,
    load_cleanroom_bundle_projection,
)
from workbench_crucible_observatory.cleanroom_matrix import (  # noqa: E402
    load_fixture_result,
)


EXACT_RUNTIME_V2_ALIASES = {
    "aa-1": "exec-aa1",
    "aa-2": "exec-aa2",
    "observer-off": "exec-observer-off",
    "observer-on": "exec-aa1",
    "order-forward": "exec-aa1",
    "order-reverse": "exec-reverse",
    "restart-1": "exec-aa1",
    "restart-2": "exec-aa2",
    "crash-before-seal": "exec-crash",
}


def _require_absolute(path: Path, *, context: str) -> None:
    if not path.is_absolute():
        raise CaptureValidationError(f"{context} path must be absolute")


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(value))
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def evaluate_exact_runtime_v2_matrix(
    *,
    aa_1_projection: Path,
    aa_1_result: Path,
    aa_2_projection: Path,
    aa_2_result: Path,
    order_reverse_projection: Path,
    order_reverse_result: Path,
    observer_off_result: Path,
    crash_projection: Path,
    output: Path,
) -> dict[str, Any]:
    """Admit, evaluate, and atomically publish one exact-runtime-v2 matrix."""

    input_paths = {
        "aa-1 projection": aa_1_projection,
        "aa-1 fixture result": aa_1_result,
        "aa-2 projection": aa_2_projection,
        "aa-2 fixture result": aa_2_result,
        "order-reverse projection": order_reverse_projection,
        "order-reverse fixture result": order_reverse_result,
        "observer-off fixture result": observer_off_result,
        "crash-before-seal projection": crash_projection,
    }
    for context, path in input_paths.items():
        _require_absolute(path, context=context)
    _require_absolute(output, context="matrix evaluation output")
    if output in input_paths.values():
        raise CaptureValidationError(
            "matrix evaluation output must not replace an input artifact"
        )

    aa_1 = load_cleanroom_bundle_projection(aa_1_projection)
    aa_2 = load_cleanroom_bundle_projection(aa_2_projection)
    reverse = load_cleanroom_bundle_projection(order_reverse_projection)
    crash = load_cleanroom_bundle_projection(crash_projection)

    executions = {
        "exec-aa1": CleanroomProjectionExecution(
            execution_id="exec-aa1",
            observer_enabled=True,
            route_order="forward",
            fixture_result=load_fixture_result(aa_1_result),
            bundle_projection=aa_1,
        ),
        "exec-aa2": CleanroomProjectionExecution(
            execution_id="exec-aa2",
            observer_enabled=True,
            route_order="forward",
            fixture_result=load_fixture_result(aa_2_result),
            bundle_projection=aa_2,
        ),
        "exec-reverse": CleanroomProjectionExecution(
            execution_id="exec-reverse",
            observer_enabled=True,
            route_order="reverse",
            fixture_result=load_fixture_result(order_reverse_result),
            bundle_projection=reverse,
        ),
        "exec-observer-off": CleanroomProjectionExecution(
            execution_id="exec-observer-off",
            observer_enabled=False,
            route_order="forward",
            fixture_result=load_fixture_result(observer_off_result),
            bundle_projection=None,
        ),
        "exec-crash": CleanroomProjectionExecution(
            execution_id="exec-crash",
            observer_enabled=True,
            route_order="forward",
            fixture_result=None,
            bundle_projection=crash,
        ),
    }
    evaluation = evaluate_cleanroom_projection_matrix(
        executions,
        EXACT_RUNTIME_V2_ALIASES,
    )
    _atomic_write_json(output, evaluation)
    return evaluation


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate four admitted Cleanroom bundle projections and the "
            "observer-off fixture result as the exact-runtime-v2 matrix."
        )
    )
    parser.add_argument("--aa-1-projection", type=Path, required=True)
    parser.add_argument("--aa-1-result", type=Path, required=True)
    parser.add_argument("--aa-2-projection", type=Path, required=True)
    parser.add_argument("--aa-2-result", type=Path, required=True)
    parser.add_argument("--order-reverse-projection", type=Path, required=True)
    parser.add_argument("--order-reverse-result", type=Path, required=True)
    parser.add_argument("--observer-off-result", type=Path, required=True)
    parser.add_argument("--crash-projection", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="absolute caller-provided ignored matrix-evaluation JSON path",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    try:
        evaluation = evaluate_exact_runtime_v2_matrix(
            aa_1_projection=arguments.aa_1_projection,
            aa_1_result=arguments.aa_1_result,
            aa_2_projection=arguments.aa_2_projection,
            aa_2_result=arguments.aa_2_result,
            order_reverse_projection=arguments.order_reverse_projection,
            order_reverse_result=arguments.order_reverse_result,
            observer_off_result=arguments.observer_off_result,
            crash_projection=arguments.crash_projection,
            output=arguments.output,
        )
    except (CaptureValidationError, OSError) as exc:
        print(f"cleanroom projection matrix evaluation failed: {exc}", file=sys.stderr)
        return 2

    encoded = canonical_json_bytes(evaluation) + b"\n"
    print(
        json.dumps(
            {
                "evaluation_id": evaluation["evaluation_id"],
                "matrix_state": evaluation["matrix_state"],
                "output": str(arguments.output),
                "output_sha256": hashlib.sha256(encoded).hexdigest(),
            },
            sort_keys=True,
        )
    )
    return 0 if evaluation["matrix_state"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
