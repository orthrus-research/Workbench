#!/usr/bin/env python3
"""Emit a strict streaming hook-health receipt for exact Cleanroom cases."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_crucible_observatory import (  # noqa: E402
    CaptureValidationError,
    CleanroomHookHealthCase,
    EXCLUDED_INCOMPLETE_CASE_ROLE,
    REQUIRED_CASE_ROLE,
    evaluate_cleanroom_hook_health,
    write_cleanroom_hook_health_evaluation,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--schema-sha256", required=True)
    parser.add_argument("--schema-id", required=True)
    parser.add_argument("--probe-plan", required=True)
    parser.add_argument("--probe-plan-sha256", required=True)
    parser.add_argument("--probe-plan-id", required=True)
    parser.add_argument("--candidate-lock", required=True)
    parser.add_argument("--candidate-lock-sha256", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument(
        "--case",
        action="append",
        nargs=6,
        metavar=(
            "CASE_ID",
            "ROLE",
            "RAW_NDJSON",
            "RAW_SHA256",
            "SESSION_AUDIT",
            "SESSION_AUDIT_SHA256",
        ),
        required=True,
        help=(
            "repeat for each case; ROLE is required-complete or "
            "excluded-incomplete"
        ),
    )
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    cases = [
        CleanroomHookHealthCase(
            case_id=case_id,
            case_role=role,
            raw_path=raw_path,
            expected_raw_sha256=raw_sha256,
            session_audit_path=session_audit_path,
            expected_session_audit_sha256=session_audit_sha256,
        )
        for (
            case_id,
            role,
            raw_path,
            raw_sha256,
            session_audit_path,
            session_audit_sha256,
        ) in arguments.case
    ]
    try:
        evaluation = evaluate_cleanroom_hook_health(
            schema_path=arguments.schema,
            expected_schema_sha256=arguments.schema_sha256,
            expected_schema_id=arguments.schema_id,
            probe_plan_path=arguments.probe_plan,
            expected_probe_plan_sha256=arguments.probe_plan_sha256,
            expected_probe_plan_id=arguments.probe_plan_id,
            candidate_lock_path=arguments.candidate_lock,
            expected_candidate_lock_sha256=arguments.candidate_lock_sha256,
            expected_candidate_id=arguments.candidate_id,
            cases=cases,
        )
        write_cleanroom_hook_health_evaluation(arguments.output, evaluation)
    except (CaptureValidationError, OSError) as exc:
        print(f"hook-health evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(evaluation["evaluation_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EXCLUDED_INCOMPLETE_CASE_ROLE",
    "REQUIRED_CASE_ROLE",
    "main",
]
