#!/usr/bin/env python3

"""Assemble one generic external diagnostic-health receipt."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_diagnostics import (  # noqa: E402
    DiagnosticHealthValidationError,
    build_external_diagnostic_health_receipt,
    write_external_diagnostic_health_receipt,
)
from workbench_crucible_diagnostics.diagnostic_health import (  # noqa: E402
    read_stable_regular_file,
)


MAX_SESSION_AUDIT_BYTES = 16 * 1024 * 1024
MAX_LAUNCH_LOG_BYTES = 32 * 1024 * 1024
MAX_POLICY_BYTES = 4 * 1024 * 1024


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate an exact completed session launch log using only a "
            "profile-owned literal diagnostic policy."
        )
    )
    parser.add_argument("--session-audit", required=True, type=Path)
    parser.add_argument("--launch-log", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    resolved_inputs = {
        args.session_audit.resolve(strict=True),
        args.launch_log.resolve(strict=True),
        args.policy.resolve(strict=True),
    }
    output = args.output.resolve(strict=False)
    if output in resolved_inputs:
        raise DiagnosticHealthValidationError(
            "receipt output cannot replace a diagnostic-health input"
        )

    receipt = build_external_diagnostic_health_receipt(
        session_audit_bytes=read_stable_regular_file(
            args.session_audit,
            context="session audit",
            maximum_size=MAX_SESSION_AUDIT_BYTES,
        ),
        launch_log_bytes=read_stable_regular_file(
            args.launch_log,
            context="launch log",
            maximum_size=MAX_LAUNCH_LOG_BYTES,
        ),
        policy_bytes=read_stable_regular_file(
            args.policy,
            context="literal diagnostic policy",
            maximum_size=MAX_POLICY_BYTES,
        ),
    )
    write_external_diagnostic_health_receipt(output, receipt)
    print(receipt["receipt_id"])
    print(receipt["summary"]["gate_state"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DiagnosticHealthValidationError, OSError) as exc:
        print(f"External diagnostic-health assembly failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
