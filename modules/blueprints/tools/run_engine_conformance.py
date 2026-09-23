#!/usr/bin/env python3

"""Run the synthetic BLUEPRINTS-M1-V01 conformance proof."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


MODULE_ROOT = Path(__file__).resolve().parents[1]
for path in (MODULE_ROOT / "src", MODULE_ROOT / "tests"):
    sys.path.insert(0, str(path))

from engine_conformance_fixture import run_conformance  # noqa: E402
from workbench_blueprints.conformance import (  # noqa: E402
    write_engine_conformance_proof,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Execute and write the BLUEPRINTS-M1-V01 proof."
    )
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    proof = run_conformance()
    write_engine_conformance_proof(arguments.output, proof)
    print(proof["proof_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
