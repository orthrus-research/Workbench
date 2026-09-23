#!/usr/bin/env python3
"""Run one bounded Atlas query over a sealed Worldgen Observatory bundle.

The command writes the ordinary V1 Atlas answer envelope.  It does not copy
records, mint a new evidence identity, or turn a derived answer into a
Crucible seal.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))
sys.path.insert(0, str(MODULE_ROOT.parent / "crucible" / "src"))

from workbench_atlas_worldgen import (  # noqa: E402
    load_admitted_worldgen_bundle,
    which_handler_changed_event,
    who_wrote_block,
)
from workbench_atlas_worldgen.exact_suite import (  # noqa: E402
    write_exact_query_report,
)
from workbench_crucible_observatory import CaptureValidationError  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--bundle", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    commands = result.add_subparsers(dest="query", required=True)

    writer = commands.add_parser(
        "who-wrote-block",
        help="attribute all observed logical writes at one exact position",
    )
    writer.add_argument("--dimension-id", type=int, required=True)
    writer.add_argument("--position", type=int, nargs=3, required=True)

    handler = commands.add_parser(
        "which-handler-changed-event",
        help="interpret one exact Forge event-post span",
    )
    handler.add_argument("--event-span-id", required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    bundle_path = arguments.bundle.resolve(strict=False)
    output_path = arguments.output.resolve(strict=False)
    if bundle_path == output_path:
        print("Atlas worldgen query: output aliases the evidence bundle", file=sys.stderr)
        return 2

    try:
        admitted = load_admitted_worldgen_bundle(bundle_path)
        if arguments.query == "who-wrote-block":
            answer = who_wrote_block(
                admitted,
                dimension_id=arguments.dimension_id,
                x=arguments.position[0],
                y=arguments.position[1],
                z=arguments.position[2],
            )
        else:
            answer = which_handler_changed_event(
                admitted,
                event_span_id=arguments.event_span_id,
            )
        write_exact_query_report(output_path, answer)
    except (CaptureValidationError, OSError, TypeError, ValueError) as exc:
        print(f"Atlas worldgen query: {exc}", file=sys.stderr)
        return 2
    return 0 if answer["status"] == "answered" else 1


if __name__ == "__main__":
    raise SystemExit(main())
