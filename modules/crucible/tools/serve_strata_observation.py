#!/usr/bin/env python3
"""Validate and serve one exact Strata viewer handoff for personal inspection."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_strata_micro_region import (  # noqa: E402
    StrataViewerHandoffValidationError,
    parse_strata_viewer_handoff,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "handoff",
        type=Path,
        help="viewer-handoff.json emitted by run_strata_observation.py",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate and print the launch details without starting the server",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    handoff = parse_strata_viewer_handoff(args.handoff, workbench_root=ROOT)
    npm = shutil.which("npm")
    if not npm:
        raise StrataViewerHandoffValidationError("npm is not available on PATH")
    command = list(handoff["command"])
    command[0] = npm
    print(f"manifest: {handoff['manifest']}")
    print(f"open: {handoff['url']}")
    print(f"cwd: {handoff['cwd']}")
    print("command: " + " ".join(command))
    if args.check:
        print("handoff check passed; no server was started")
        return 0
    environment = os.environ.copy()
    environment["STRATA_EXTERNAL_ARTIFACT_ROOT"] = handoff[
        "external_artifact_root"
    ]
    print("Press Ctrl-C after inspection to stop the local viewer.", flush=True)
    try:
        completed = subprocess.run(
            command,
            cwd=handoff["cwd"],
            env=environment,
            check=False,
        )
    except KeyboardInterrupt:
        return 130
    return completed.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, StrataViewerHandoffValidationError) as exc:
        print(f"Strata viewer handoff failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
