#!/usr/bin/env python3
"""Inspect source validation and native release build ownership."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from component_versions import load_authority, check_projections, ComponentVersionError

ROOT = Path(__file__).resolve().parents[1]


class BuildPathError(ValueError):
    pass


def load_build_paths(root: Path = ROOT):
    try:
        _, components = load_authority(root)
        failures = check_projections(components, root)
        if failures:
            raise BuildPathError("; ".join(failures))
        rows = [
            {"id": "source", "description": "Validate the frozen source tree", "argv": ["python", "validation/validate.py", "--full"]},
            {"id": "native-component", "description": "Build a selected Python component and its dependency closure", "argv": ["python", "tools/build_native_distribution.py", "--component", "{component}"]},
            {"id": "native-suite", "description": "Assemble current native modules and profiles without another distribution", "argv": ["python", "tools/build_native_distribution.py", "--suite"]},
            {"id": "client", "description": "Build one independently versioned IDE client", "argv": ["python", "tools/build_release_clients.py", "--component", "{component}"]},
            {"id": "axiom-engine", "description": "Build and test the standalone Java Axiom engine", "argv": ["python", "tools/build_axiom.py", "--provision"]},
        ]
        for row in rows:
            script = root / row["argv"][1]
            if script.is_symlink() or not script.is_file():
                raise BuildPathError(f"missing direct build entry point: {script}")
        return {"format": "workbench-native-build-paths-v1", "schema_version": 1, "build_paths": rows,
                "components": components, "qualified": False, "published": False}
    except (ComponentVersionError, OSError, ValueError) as exc:
        raise BuildPathError(str(exc)) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "list", "show"))
    parser.add_argument("build_path", nargs="?")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        value = load_build_paths()
        if args.command == "show":
            value = next(row for row in value["build_paths"] if row["id"] == args.build_path)
        elif args.command == "validate" and not args.json:
            print(f"Native build paths: PASS ({len(value['build_paths'])} routes, {len(value['components'])} independently versioned components)")
            return 0
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    except (BuildPathError, StopIteration) as exc:
        print(f"build path failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
