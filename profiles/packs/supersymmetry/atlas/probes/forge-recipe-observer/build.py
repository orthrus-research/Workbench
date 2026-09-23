#!/usr/bin/env python3
"""Repository wrapper for the installed profile's Forge observer builder.

Core, API and the Supersymmetry profile must be importable. This does not install
a mod, accept the Minecraft EULA, or launch a game/server.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from threading import Event

from workbench_core.host_services import install_local_host_services
from workbench_profile_supersymmetry.recipe_capture_runtime import build_observer


def build(java_home: Path, classpath: list[Path], output: Path) -> dict:
    install_local_host_services()
    return build_observer(java_home.absolute(), [path.absolute() for path in classpath],
                          output.absolute(), cancelled=Event())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--classpath", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.java_home, args.classpath, args.output_dir), indent=2))


if __name__ == "__main__":
    main()
