#!/usr/bin/env python3
"""Build a hash-locked native wheelhouse for the current Python/OS target."""
import argparse
import json
from pathlib import Path
from native_distribution import ROOT, build, derive, selected_components
from validation_diagnostics import DiagnosticRun, default_directory


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--component", action="append", dest="components")
    choice.add_argument("--suite", action="store_true", help="assemble all native modules and profiles")
    parser.add_argument("--output", type=Path, default=Path(".workbench/build/native-wheelhouse"))
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--from-wheelhouse", type=Path, help="derive a verified offline closure without rebuilding wheels")
    parser.add_argument("--diagnostics", type=Path, help="new directory for bounded phase logs")
    args = parser.parse_args(argv)
    if args.plan:
        result = selected_components(args.components, suite=args.suite)
    else:
        with DiagnosticRun(args.diagnostics or default_directory(ROOT, "native-build"), "native-build", ("assembly",)) as diagnostics:
            with diagnostics.phase("assembly"):
                if args.from_wheelhouse:
                    result = derive(args.from_wheelhouse, args.output, args.components, suite=args.suite)
                else:
                    result = build(args.output, args.components, suite=args.suite,
                                   command_runner=lambda command: diagnostics.command(command, cwd=ROOT, timeout=1200))
            diagnostics.document["metadata"].update(source_sha256=result["source_sha256"], target=result["target"], wheels=result["wheels"])
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
